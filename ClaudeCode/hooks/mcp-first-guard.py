#!/usr/bin/env python3
"""PreToolUse(Bash) guard — enforce MCP-first routing.

Denies file-search / listing / stream-edit / system-inspection binaries
(grep, rg, find, fd, locate, ls, sed, ps, lsof, netstat, ss, df, du, free)
when used as the PRIMARY command of a statement, steering the model to the
purity_call / inspect_call MCP equivalents. Downstream pipe stages (e.g.
`journalctl | grep`, `cat f | sed`) are allowed — there the binary filters a
stream, which has no MCP equivalent.

Decision is emitted purely via stdout JSON; the script ALWAYS exits 0 and
fails OPEN on any error, so it can never brick the Bash tool.
"""
import json
import os
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
    "sed": "purity_call(replace_content/replace_lines/insert_at_line) to edit, or Read to view",
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


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return

    hits = []
    for stmt in split_top(cmd, ["&&", "||", ";", "\n"]):
        stages = split_top(stmt, ["|"])  # '||' already consumed above
        if stages:
            tok = first_cmd_token(stages[0])
            if tok in BLOCKED:
                hits.append(tok)

    if not hits:
        return  # no opinion -> normal permission flow continues

    uniq = sorted(set(hits))
    mapping = "; ".join(f"`{h}` -> {BLOCKED[h]}" for h in uniq)
    reason = (
        "MCP-first routing violation: "
        + ", ".join(f"`{h}`" for h in uniq)
        + " is forbidden as a primary file-search / listing / inspection command via Bash. Use instead: "
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
