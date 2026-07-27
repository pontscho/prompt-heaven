#!/usr/bin/env python3
"""PreToolUse(Bash) guard — enforce MCP-first routing.

Denies file-search / listing / file-read / stream-edit / dir-creation /
system-inspection binaries (grep, rg, find, fd, locate, ls, cat, head, tail,
awk, sed, mkdir, ps, lsof, netstat, ss, df, du, free) when used as the PRIMARY
command of a statement, steering the model to the purity_call / inspect_call
MCP equivalents. Downstream pipe stages (e.g. `journalctl | grep`,
`git log | awk '{print $1}'`) are allowed — there the binary filters a stream,
which has no MCP equivalent. Individual MODES with no MCP equivalent stay
allowed even as the primary command (see ALLOW_FLAGS: `tail -f`).

Decision is emitted purely via stdout JSON; the script ALWAYS exits 0 and
fails OPEN on any error, so it can never brick the Bash tool.
"""
import json
import os
import re
import sys

# basename -> suggested MCP replacement
BLOCKED = {
    "grep": "purity_call(search_for_pattern)",
    "egrep": "purity_call(search_for_pattern)",
    "fgrep": "purity_call(search_for_pattern)",
    "rg": "purity_call(search_for_pattern)",
    "ripgrep": "purity_call(search_for_pattern)",
    "find": "purity_call(find_file)",
    "fd": "purity_call(find_file)",
    "fdfind": "purity_call(find_file)",
    "locate": "purity_call(find_file)",
    "mlocate": "purity_call(find_file)",
    "plocate": "purity_call(find_file)",
    "ls": "purity_call(list_dir)",
    # file viewing -> built-in Read (it takes offset/limit, so slicing a big
    # file needs no head/tail)
    "cat": "Read (built-in); to feed a program stdin use `prog < file`, not `cat file | prog`",
    "head": "Read (built-in) with limit",
    "tail": "Read (built-in) with offset/limit (`tail -f` follow-mode stays allowed)",
    "awk": "purity_call(search_for_pattern) to search, or Read to view — never awk to read or rewrite a file",
    "sed": "purity_call(replace_content/replace_lines/insert_at_line) to edit, or Read to view",
    # scratch dirs -> purity. Usually you need NOTHING: create_text_file/Write
    # already create missing parent dirs, so a temp file needs no mkdir at all.
    "mkdir": (
        "nothing at all if you are about to write a file — purity_call(create_text_file) "
        "and Write create missing parent dirs; if a directory must exist up front, "
        "purity_call(create_temp_dir) with {subpath, unique} under .claude/tmp"
    ),
    # live system state -> mcp-inspect (read-only, pre-approved, no prompt)
    "ps": "inspect_call(function=processes)",
    "lsof": "inspect_call(function=open_files)",
    "netstat": "inspect_call(function=ports or connections)",
    "ss": "inspect_call(function=ports)",
    "df": "inspect_call(function=disk)",
    "du": "inspect_call(function=disk_usage)",
    "free": "inspect_call(function=memory)",
}

# leading tokens that wrap the real command — skip them to find the true cmd
SKIP_WRAPPERS = {"sudo", "command", "env", "nice", "nohup", "time", "builtin", "exec", "xargs"}

# basename -> (short-option LETTERS, long options) selecting a mode that has NO
# MCP equivalent at all; such a stage stays allowed even as the primary command.
# `Read` cannot follow a growing file, so denying `tail -f` would be a dead end.
# Letters are matched INSIDE short clusters: options bundle, so `tail -fn 100`
# carries `-f` and an exact-token check would miss it.
ALLOW_FLAGS = {
    "tail": ({"f", "F"}, {"--follow", "--retry"}),
}


def mode_exempt(basename, stage):
    """True if this stage selects a mode of `basename` with no MCP equivalent."""
    spec = ALLOW_FLAGS.get(basename)
    if not spec:
        return False
    letters, longs = spec
    for tok in stage.split():
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            # getopt_long accepts unambiguous abbreviations (`--fol` == `--follow`)
            if len(name) > 2 and any(lo.startswith(name) for lo in longs):
                return True
        elif tok.startswith("-") and len(tok) > 1:
            if letters & set(tok[1:]):
                return True
    return False


def split_top(s, seps):
    """Split s at top-level occurrences of any sep in seps (longest-first),
    respecting single/double quotes."""
    seps = sorted(seps, key=len, reverse=True)
    out, buf = [], []
    i, n, q = 0, len(s), None
    while i < n:
        c = s[i]
        if q:
            buf.append(c)
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            buf.append(c)
            i += 1
            continue
        hit = next((sep for sep in seps if s.startswith(sep, i)), None)
        if hit:
            out.append("".join(buf))
            buf = []
            i += len(hit)
        else:
            buf.append(c)
            i += 1
    out.append("".join(buf))
    return out


def first_cmd_token(stage):
    """Return the basename of the primary command in a pipe stage, skipping
    leading VAR=val assignments and wrapper commands. None if empty."""
    toks, buf, q = [], [], None
    for c in stage.strip():
        if q:
            if c == q:
                q = None
            else:
                buf.append(c)
        elif c in ("'", '"'):
            q = c
        elif c.isspace():
            if buf:
                toks.append("".join(buf))
                buf = []
        else:
            buf.append(c)
    if buf:
        toks.append("".join(buf))

    idx = 0
    while idx < len(toks):
        t = toks[idx]
        head = t.split("=", 1)[0]
        if "=" in t and head and head.replace("_", "").isalnum() and not t.startswith("="):
            idx += 1  # VAR=val assignment
            continue
        if os.path.basename(t) in SKIP_WRAPPERS:
            idx += 1  # wrapper (sudo/env/xargs/...)
            continue
        break
    return os.path.basename(toks[idx]) if idx < len(toks) else None


# The lookarounds keep a HERESTRING (`cmd <<<WORD`) from being read as a
# heredoc: without them the regex matches at the SECOND `<`, takes WORD for a
# delimiter and swallows every following line — hiding real commands from the
# scan.
HEREDOC_RE = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredocs(cmd):
    """Drop heredoc BODIES so their data lines are not mis-read as commands.

    The heredoc-introducing line is kept (it holds the real command, e.g.
    `git commit -F - <<'EOF'`); everything from the next line through the
    closing delimiter is removed. Handles <<, <<-, and quoted/unquoted
    delimiters. A real command AFTER the terminator is still analyzed.
    """
    out, active = [], None
    for line in cmd.split("\n"):
        if active is not None:
            delim, strip_tabs = active
            probe = line.lstrip("\t") if strip_tabs else line
            if probe.strip() == delim:
                active = None
            continue  # drop body line (and the terminator line)
        out.append(line)
        m = HEREDOC_RE.search(line)
        if m:
            active = (m.group(2), m.group(0).startswith("<<-"))
    return "\n".join(out)


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return
    cmd = _strip_heredocs(cmd)  # heredoc bodies are data, not commands

    hits = []
    # longest-first splitting means `&&` is consumed before the bare `&`
    for stmt in split_top(cmd, ["&&", "||", ";", "\n", "&"]):
        stages = split_top(stmt, ["|"])  # '||' already consumed above
        if stages:
            tok = first_cmd_token(stages[0])
            if tok in BLOCKED and not mode_exempt(tok, stages[0]):
                hits.append(tok)

    if not hits:
        return  # no opinion -> normal permission flow continues

    uniq = sorted(set(hits))
    mapping = "; ".join(f"`{h}` -> {BLOCKED[h]}" for h in uniq)
    reason = (
        "MCP-first routing violation: "
        + ", ".join(f"`{h}`" for h in uniq)
        + " is forbidden as a primary file-search / listing / read / dir-creation /"
        " inspection command via Bash. Use instead: "
        + mapping
        + ". (Piping INTO these to filter another command's stdout is allowed; this "
        "blocks them only as the primary file operation.)"
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open — never brick Bash
    sys.exit(0)
