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

Specific INVOCATIONS are denied too, even when the binary itself is innocent:
`python3 -m py_compile` writes __pycache__/*.pyc into the tree as a side effect
of a mere syntax check, `python3 -m compileall` does exactly that across a
whole directory tree, and `node --check` / `node -c` is a syntax check with an
exact MCP equivalent (see BLOCKED_FORMS).  Deliberately NOT generalised to
`python3 -c`, `python3 -m json.tool` or a plain `node file.js`: the first two are
read-only and have far too much legitimate use to pay the false-positive price,
and the third RUNS the file, which no MCP tool here does.  The node rule also
fires ONLY when `node` is installed — with no node there is nothing to redirect
to either, since that validator FAILs without it.

WRAPPERS are peeled before the check, because the primary command can hide one
layer down:

    (cat f)          subshell             -> the opener is peeled off the stage
    { cat f; }       brace group          -> ditto
    echo $(cat f)    substitution         -> the $( ) / ` ` / <( ) / >( ) body
                                             is scanned as a command of its own
    bash -c 'cat f'  shell payload        -> the `-c STRING` is scanned likewise
    CAT f            ALL-CAPS spelling    -> folded to `cat`: on a
                                             case-insensitive filesystem
                                             (macOS default) /bin/CAT resolves,
                                             so `CAT f` really does run cat.
                                             The fold covers EVERY name this
                                             guard has an opinion about, so
                                             `PYTHON3 -m compileall` is seen and
                                             `BASH -c 'cat f'` is unwrapped too

Unwrapping recurses at most MAX_DEPTH layers (`bash -c 'bash -c "cat f"'`).
Heredoc bodies are NOT unwrapped: they are DATA (a commit message may mention
anything), and keeping `git commit -F - <<EOF` working outranks catching a
substitution nobody writes in a commit message.

Decision is emitted purely via stdout JSON; the script ALWAYS exits 0 and
fails OPEN on any error, so it can never brick the Bash tool.
"""
import json
import os
import re
import shutil
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

# Whole INVOCATIONS with an MCP equivalent, where the binary is fine and only
# this mode of it is not.  label -> suggested MCP replacement.  The label is
# what the deny reason names, so it must read like the command it forbids.
BLOCKED_FORMS = {
    "python3 -m py_compile": (
        "inspect_call(function=python), which compiles in memory; outside a "
        "session `python3 -c \"import sys; "
        "compile(open(sys.argv[1],'rb').read(), sys.argv[1], 'exec')\" <file>`. "
        "py_compile WRITES __pycache__/*.pyc into the tree as a side effect of "
        "a mere syntax check"
    ),
    "python3 -m compileall": (
        "an in-memory compile() on the raw bytes: inspect_call(function=python) "
        "for one file, or forge_call(function=build, targets=[\"syntax\"]), which "
        "IS this check for the whole repo; outside a session `python3 -B -c "
        "\"import pathlib, sys; [compile(p.read_bytes(), str(p), 'exec') for p in "
        "map(pathlib.Path, sys.argv[1:])]\" <files>`. compileall WRITES "
        "__pycache__/*.pyc across an ENTIRE directory tree — the py_compile side "
        "effect, only broader"
    ),
    "node --check": (
        "inspect_call(function=javascript, params={path: <file>}), which runs the "
        "very same `node --check` and reports status + line:col (a LIST of files "
        "in one call via params.paths). Only the SYNTAX-CHECK mode is redirected: "
        "`node file.js`, `node -e`, `npm`/`npx` and `node --version` are untouched"
    ),
}

# `python -m MODULE` values that map onto a BLOCKED_FORMS label. Every entry
# gets the short-option-cluster handling for free, because python_module() is
# the ONE place that reads python's argv: no module can be denied in its `-m
# mod` spelling but slip through as `-Bm mod`.
BLOCKED_MODULES = {
    "py_compile": "python3 -m py_compile",
    "compileall": "python3 -m compileall",
}

# every label the deny reason can name -> its steer
STEERS = dict(BLOCKED, **BLOCKED_FORMS)

# leading tokens that wrap the real command — skip them to find the true cmd
SKIP_WRAPPERS = {"sudo", "command", "env", "nice", "nohup", "time", "builtin", "exec", "xargs"}

# shells whose `-c STRING` argument is a whole command in its own right
SHELL_C = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash"}

# python, python3, python3.12, python2.7 — the interpreters that take `-m`
PY_INTERP_RE = re.compile(r"^python[0-9.]*$")

# python short options that take an ARGUMENT — glued to the letter (`-mmod`) or
# as the next word (`-m mod`). Inside a cluster such a letter ends the cluster,
# so `-Ximporttime` carries no `-m` however many m's its argument contains.
PY_ARG_LETTERS = "cmWXQ"

# python long options that consume the NEXT word
PY_ARG_OPTS = {"--check-hash-based-pycs"}

# node short options that take an ARGUMENT (`-e CODE`, `-p EXPR`, `-r MODULE`).
# Read letterwise like python's, so `-pc` is `-p c` and carries no `-c`.
NODE_ARG_LETTERS = "epr"

# The `node --check` rule fires ONLY when node is installed. Without it there is
# no working inspect_call(function=javascript) to steer to either — that
# validator FAILs with no node — so a deny would trade one dead end for another.
# Resolved once per process: this hook is spawned per Bash call and exits.
NODE_PRESENT = shutil.which("node") is not None

# longest-first, so `&&` is consumed before the bare `&` (and `||` before `|`)
STMT_SEPS = ["&&", "||", ";", "\n", "&"]

# How many wrapper layers to peel. Each layer costs one more level of quoting,
# which no real invocation survives past two (`bash -c 'bash -c "cat f"'` is
# already absurd), so a small cap buys the realistic cases and bounds the work
# on adversarial input. Text AT the cap is still analysed, just not unwrapped
# further — the miss is in the ALLOW direction, like every other limit here.
MAX_DEPTH = 3

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


def _tokens(stage):
    """Quote-aware word split of one pipe stage, with the QUOTES REMOVED.

    Same quote bookkeeping as split_top (only the matching quote closes, so a
    `'` inside "..." is an ordinary character). Dropping the quotes is what
    makes a wrapper payload usable downstream: `bash -c 'cat f'` arrives as the
    three tokens `bash`, `-c`, `cat f`, the last one ready to be re-scanned.
    """
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
    return toks


def has_opinion(name):
    """True if this guard treats the lower-case `name` specially AT ALL.

    The one place the guard's whole vocabulary is stated, and the sole gate on
    the ALL-CAPS fold in primary(). Four things the guard does with a command
    name:

        BLOCKED       the name is denied outright as a primary command
        SHELL_C       the name's `-c STRING` payload is unwrapped and re-scanned
        PY_INTERP_RE  the name's `-m MODULE` is checked against BLOCKED_MODULES
        node          its `--check`/`-c` MODE is denied (node_check_mode)

    Written as a predicate over all four rather than as a chain of special
    cases, because that is what makes the fold's safety argument a single
    sentence: folding can only ever reach a name the guard was already going to
    act on, so it cannot change the meaning of anything else. Measured on this
    host (macOS, 15 PATH dirs, 3 of them non-existent): every PATH dir was
    enumerated and NOT ONE ships a file whose name is the ALL-CAPS spelling of
    any of these names, so nothing real is shadowed. Of the shells,
    BASH/SH/ZSH/DASH/KSH each resolve to the SAME inode as their lower-case
    spelling (`which("BASH") -> /usr/local/bin/BASH`), while `mksh` and `ash`
    are not installed at all -- folding those two is inert today and correct the
    day someone installs them. `NODE` resolves to that same inode too, so
    `NODE --check f.js` really is a syntax check and folds like the rest.
    """
    return (name in BLOCKED
            or name in SHELL_C
            or bool(PY_INTERP_RE.match(name))
            or name == "node")


def primary(stage):
    """(name, argv) of the primary command of a pipe stage; (None, []) if none.

    Peels, in any order and any number of times: subshell / brace-group
    openers, leading VAR=val assignments, and wrapper commands. `name` is the
    basename, folded to lower case when an ALL-CAPS spelling of a blocked name
    is used. `argv` is every token after it.
    """
    toks = _tokens(stage)
    idx = 0
    while idx < len(toks):
        tok = toks[idx]
        if tok in ("(", "{"):
            idx += 1  # `( cat f )` / `{ cat f; }` group opener, spaced
            continue
        if tok.startswith("("):
            # glued opener: `(cat f)`. A `{` is NOT peeled here — bash requires
            # whitespace after it, so `{cat` is a command name, not a group.
            tok = tok.lstrip("(")
            if not tok:
                idx += 1
                continue
            toks[idx] = tok
        head = tok.split("=", 1)[0]
        if "=" in tok and head and head.replace("_", "").isalnum() and not tok.startswith("="):
            idx += 1  # VAR=val assignment
            continue
        if os.path.basename(tok) in SKIP_WRAPPERS:
            idx += 1  # wrapper (sudo/env/xargs/...)
            continue
        break
    if idx >= len(toks):
        return None, []
    name = os.path.basename(toks[idx])
    # An ALL-CAPS spelling reaches the real binary on a case-insensitive
    # filesystem — /bin/CAT resolves on macOS, so `CAT f` IS `cat f`. Folding is
    # restricted to names this guard already has an opinion about, which is
    # exactly has_opinion(): a blocked binary, a shell whose `-c STRING` is
    # unwrapped, or a python interpreter whose `-m MODULE` is inspected. It can
    # therefore never change the meaning of anything else — and no PATH dir here
    # ships an ALL-CAPS binary, so nothing real is shadowed. Mixed case (`Cat`)
    # is left alone: it would resolve too, but a mixed-case name is likelier to
    # be a DIFFERENT program, and that broadening was never asked for.
    if name.isupper() and has_opinion(name.lower()):
        name = name.lower()
    return name, toks[idx + 1:]


def first_cmd_token(stage):
    """Basename of the primary command in a pipe stage. None if empty."""
    return primary(stage)[0]


def dash_c_payload(argv):
    """The STRING of a shell's `-c STRING`, or None.

    Letter-wise inside short clusters, exactly like mode_exempt: options
    bundle, so an exact `-c` token test would miss `bash -lc 'cat f'`.
    """
    for i, tok in enumerate(argv):
        if tok == "--":
            break
        if tok.startswith("--"):
            continue
        if not tok.startswith("-"):
            break  # first operand — the option region is over
        if "c" in tok[1:]:
            return argv[i + 1] if i + 1 < len(argv) else None
    return None


def python_module(argv):
    """The MODULE of `python -m MODULE`, or None.

    Accepts `-m mod`, the cluster `-Bm mod` and the glued `-mmod` (and both at
    once, `-Bmmod`). Reading the LETTERS of a single-dash token instead of
    matching the whole token is this repo's standing rule for every flag
    decision on shell argv, in either direction — it is what `git hash-object
    -wt blob` and `tail -fn 100` each cost us once. CPython bundles too:
    `python3 -Bm py_compile` IS `python3 -B -m py_compile`.

    Clusters are read letterwise LEFT TO RIGHT, because the first
    argument-taking letter ends the cluster: a bare `in tok` test would read
    the `m` of `-Ximporttime` as an option, and `-Bcm py_compile` is really
    `-B -c m`, which names no module. Scanning stops at the first operand, because
    past the script name a `-m` belongs to the SCRIPT, not to python
    (`python3 tool.py -m py_compile` is not a py_compile run).
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            break  # operand: the script name ends python's own option region
        if tok.startswith("--"):
            i += 2 if tok in PY_ARG_OPTS else 1
            continue
        step = 1
        for at, letter in enumerate(tok[1:], start=1):
            if letter not in PY_ARG_LETTERS:
                continue  # a plain flag (the `B` of `-Bm`)
            glued = tok[at + 1:]
            if letter == "m":
                return glued or (argv[i + 1] if i + 1 < len(argv) else None)
            if not glued:
                step = 2  # this option's argument is the NEXT word
            break  # an argument-taking letter ends the cluster either way
        i += step
    return None


def node_check_mode(argv):
    """True if this node argv selects `--check` / `-c`, the syntax-check mode.

    The MODE is what has an MCP equivalent, not the binary: `node file.js` RUNS
    the file and must stay allowed. Options are read letterwise inside short
    clusters, the same rule python_module() follows and for the same reason —
    `-c` bundles (`node -ce 'x'`), while an argument-taking letter ends the
    cluster, so the `c` of `-pc` is `-p`'s ARGUMENT and names no check.
    Scanning stops at the first operand, because past the script name a `--check`
    belongs to the SCRIPT (`node tool.js --check` is not a node syntax check).
    """
    for tok in argv:
        if tok == "--" or not tok.startswith("-") or tok == "-":
            break  # operand (or the end-of-options marker): node's region is over
        if tok.startswith("--"):
            if tok.split("=", 1)[0] == "--check":
                return True
            continue
        for letter in tok[1:]:
            if letter == "c":
                return True
            if letter in NODE_ARG_LETTERS:
                break  # this letter's argument follows; the cluster ends here
    return False


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


def _balanced(cmd, i):
    """Body of an already-opened `(`, from index i to its matching `)`.

    Returns (body, index_after_the_paren). Quote- and nesting-aware, and the
    quotes are KEPT so the body can be re-tokenized. An unterminated opener
    yields the rest of the string: still the safe direction, since the text is
    then scanned instead of silently dropped.
    """
    depth, buf, q, n = 1, [], None, len(cmd)
    while i < n:
        c = cmd[i]
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
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return "".join(buf), i + 1
        buf.append(c)
        i += 1
    return "".join(buf), n


def substitutions(cmd):
    """Command strings nested in `cmd` via $( ), ` `, <( ) and >( ).

    Quote rules follow the shell, not split_top's simplification, because they
    decide whether the inner text RUNS: a single-quoted region expands nothing
    and is skipped whole, while a double-quoted one still executes `"$(cat f)"`.
    Process substitution is only recognised unquoted — `"<(cat f)"` is literal
    text to bash, so treating it as a command would be a false positive.
    `$((` is arithmetic and holds no command.
    """
    out = []
    i, n, q = 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if q == "'":  # nothing expands inside '...'
            if c == "'":
                q = None
            i += 1
            continue
        if c == "\\":
            i += 2  # escaped character can never open a substitution
            continue
        if q == '"':
            if c == '"':
                q = None
                i += 1
                continue
            # fall through: $( ) and ` ` DO expand inside "..."
        elif c in ("'", '"'):
            q = c
            i += 1
            continue
        if cmd.startswith("$((", i):
            i += 3
            continue
        if cmd.startswith("$(", i) or (q is None and cmd[i:i + 2] in ("<(", ">(")):
            body, i = _balanced(cmd, i + 2)
            out.append(body)
            continue
        if c == "`":
            end = cmd.find("`", i + 1)
            out.append(cmd[i + 1:] if end < 0 else cmd[i + 1:end])
            i = n if end < 0 else end + 1
            continue
        i += 1
    return out


def scan(cmd, depth=0):
    """Every blocked label in `cmd`, descending into wrappers up to MAX_DEPTH."""
    cmd = _strip_heredocs(cmd)  # heredoc bodies are data, not commands
    hits, payloads = [], []
    for stmt in split_top(cmd, STMT_SEPS):
        stage = split_top(stmt, ["|"])[0]  # '||' already consumed above
        name, argv = primary(stage)
        if name is None:
            continue
        if name in BLOCKED and not mode_exempt(name, stage):
            hits.append(name)
        if name in SHELL_C:
            payloads.append(dash_c_payload(argv))
        if PY_INTERP_RE.match(name):
            label = BLOCKED_MODULES.get(python_module(argv))
            if label:
                hits.append(label)
        if name == "node" and NODE_PRESENT and node_check_mode(argv):
            hits.append("node --check")
    if depth < MAX_DEPTH:
        for text in substitutions(cmd) + [p for p in payloads if p]:
            hits += scan(text, depth + 1)
    return hits


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return

    hits = scan(cmd)
    if not hits:
        return  # no opinion -> normal permission flow continues

    uniq = sorted(set(hits))
    mapping = "; ".join(f"`{h}` -> {STEERS[h]}" for h in uniq)
    reason = (
        "MCP-first routing violation: "
        + ", ".join(f"`{h}`" for h in uniq)
        + " is forbidden as a primary file-search / listing / read / dir-creation /"
        " inspection / syntax-check / tree-mutating command via Bash. Use instead: "
        + mapping
        + ". (Piping INTO these to filter another command's stdout is allowed; this "
        "blocks them only as the primary file operation. Hiding one in a subshell, "
        "a brace group, a command substitution or `bash -c` does not help — the "
        "guard looks inside those.)"
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
