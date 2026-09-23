#!/usr/bin/env python3
"""forge_call's function dispatch: the `status` spelling and what it advertises.

`handle_forge_call` in Scripts/mcp-forge.py answers an EMPTY `function` with the
server status, and it does so at four separate points -- a missing config, a
YAML parse error, a config carrying validation errors, and the ordinary status
reply at the end.  `status` is the word a caller reaches for when that is the
answer it wants, and it is folded to "" before any of those points reads the
name.  The risk this suite exists for is a fold placed LATER than that: it would
still pass the ordinary case and quietly fail the other three, where `status`
would be answered as an unknown function or a config error instead.

So group A drives the dispatcher through all four paths, and each path carries
its own control row: the SAME fixture called with a real function (`list` or
`build`) must come back as the error the path exists to produce.  Without that
control a fixture that failed to trigger its path -- a "broken" YAML the parser
happens to accept -- would make the status row pass for the wrong reason.

Group B is the advertising half.  An alias nobody is told about is a feature
nobody calls, so the unknown-function error, the tool description's Functions
block and its closing `Call without 'function'` line, and the inputSchema's
`function` description must each name it.  Each is PARSED -- a list item, a
name-column entry, an exact `function="status"` spelling -- because every one of
those texts already contained the word "status" before the alias existed, and a
substring test would have passed against the old wording.

Imported in-process (`handle_forge_call` is a pure function of its arguments and
the config file), so no server child is started.  Fixtures are written into a
`tempfile.mkdtemp()` workspace; group C asserts the repo tree and its bytecode
are untouched.

Groups:
  A  `status` == the empty call, on every one of the empty call's four paths
  B  `status` is advertised wherever the function list is
  C  hygiene
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "forge_dispatch"
SERVER = H.repo_path("Scripts", "mcp-forge.py")
CFG = "project-forge.yaml"

# One fixture per path through handle_forge_call's empty-function branches.
# `None` means no config file at all.  Each carries the real function whose
# answer proves the fixture reached the intended path, and a token that answer
# must contain.
YAML_OK = (
    "version: 1\n"
    "test:\n"
    "  unit:\n"
    "    description: \"a target\"\n"
    "    commands:\n"
    "      - \"true\"\n"
)
YAML_BROKEN = "version: \"unterminated\n"
YAML_INVALID = (
    "version: 1\n"
    "configuration:\n"
    "  settings:\n"
    "    timeout: soon\n"
)
PATHS = (
    # (label,       yaml,         control function, control must say)
    ("ok",          YAML_OK,      None,    None),
    ("missing",     None,         "list",  "config file not found"),
    ("parse-error", YAML_BROKEN,  "list",  "YAML parse error"),
    ("invalid",     YAML_INVALID, "build", "validation error"),
)


def reply_text(reply):
    """The body of a handler reply, whichever key the handler used."""
    if not isinstance(reply, dict):
        return repr(reply)
    return reply.get("__raw_text__") or reply.get("error") or repr(reply)


# ---------------------------------------------------------------------------
# Group A -- `status` answers exactly what the empty call answers
# ---------------------------------------------------------------------------

def group_a(suite, mod, ws):
    for label, yaml_text, ctl_fn, ctl_token in PATHS:
        root = ws.subdir(label)
        if yaml_text is not None:
            ws.write_text(os.path.join(label, CFG), yaml_text)

        empty = mod.handle_forge_call({}, root, CFG)
        by_name = mod.handle_forge_call({"function": "status"}, root, CFG)
        by_short = mod.handle_forge_call({"f": " status "}, root, CFG)
        problems = []
        if "error" in empty or "## mcp-forge status" not in reply_text(empty):
            problems.append("the EMPTY call is not a status reply here: %s"
                            % reply_text(empty)[:160])
        if by_name != empty:
            problems.append("function=status differs from the empty call: %s"
                            % reply_text(by_name)[:160])
        if by_short != empty:
            problems.append("f=' status ' differs from the empty call: %s"
                            % reply_text(by_short)[:160])
        suite.record(
            "A", "status-is-empty-call-%s" % label, problems,
            detail=["path: %s" % label,
                    "the full reply dicts are compared, not a substring, and",
                    "the `f` spelling with padding rides along because the",
                    "fold happens after the strip"],
            text=reply_text(by_name), showable=True)

        if ctl_fn is None:
            continue
        ctl = mod.handle_forge_call({"function": ctl_fn}, root, CFG)
        text = reply_text(ctl)
        problems = []
        if "error" not in ctl:
            problems.append("function=%s was accepted, so the fixture never "
                            "reached the %s path" % (ctl_fn, label))
        elif ctl_token.lower() not in text.lower():
            problems.append("function=%s error does not mention %r"
                            % (ctl_fn, ctl_token))
        suite.record(
            "A", "control-%s-path-reached" % label, problems,
            detail=["CONTROL: the same fixture with a REAL function must be",
                    "refused with the error this path exists for, or the",
                    "status row above passed on a fixture that never took it",
                    "reply: %s" % text[:200]],
            text=text, showable=True)


# ---------------------------------------------------------------------------
# Group B -- the alias is advertised wherever the function list is
# ---------------------------------------------------------------------------

def name_list(text, lead, stop):
    """The comma-separated names between `lead` and `stop`, as a list.

    Parsed to ELEMENTS on purpose.  Every one of these texts already said
    "status" before the alias existed -- as the phrase "or empty for status" or
    "server status" -- so a substring test passes against the old wording and
    gates nothing.  Only `status` standing as its own list item is new.
    """
    body = text.split(lead, 1)[-1] if lead in text else ""
    body = body.split(stop, 1)[0]
    return [item.strip() for item in body.split(",") if item.strip()]


def group_b(suite, mod, ws):
    root = ws.subdir("ok")
    reply = mod.handle_forge_call({"function": "definitely_not_a_function"},
                                  root, CFG)
    text = reply_text(reply)
    names = name_list(text, "Available: ", " (")
    problems = []
    if "error" not in reply:
        problems.append("an unknown function was accepted")
    if "status" not in names:
        problems.append("`status` is not an item of the Available list: %s"
                        % names)
    suite.record("B", "unknown-function-lists-status", problems,
                 detail=["Available items: %s" % names,
                         "the old list ended at `clean` and said `status` only",
                         "inside `(or empty for status)`, which is not an item",
                         "reply: %s" % text[:200]], text=text, showable=True)

    desc = mod.FORGE_CALL_TOOL["description"]
    functions_block = desc.split("Functions:\n", 1)[-1].split("\n\n", 1)[0]
    # The NAME column only: the left side of each `->`, split on `/`.  The old
    # `(empty)` row said "server status" on its RIGHT side.
    listed = [name.strip()
              for row in functions_block.splitlines() if "->" in row
              for name in row.split("->", 1)[0].split("/")]
    suite.record("B", "description-lists-status",
                 [] if "status" in listed else
                 ["`status` is not in the Functions block's name column: %s"
                  % listed],
                 detail=["the block the model reads to pick a function name",
                         "name column: %s" % listed],
                 text=functions_block, showable=True)

    schema = (mod.FORGE_CALL_TOOL["inputSchema"]["properties"]["function"]
              ["description"])
    items = name_list(schema, "Function name: ", ". ")
    suite.record("B", "schema-function-lists-status",
                 [] if "status" in items else
                 ["`status` is not an item of inputSchema.function's list: %s"
                  % items],
                 detail=["items: %s" % items,
                         "the old list's last item was `or empty for status`",
                         "schema: %s" % schema], text=schema)

    # The closing one-liner is the other place the empty call is described.
    call_line = next((ln for ln in desc.splitlines()
                      if ln.startswith("Call without 'function'")), "")
    suite.record("B", "call-line-names-function-status",
                 [] if 'function="status"' in call_line else
                 ["the `Call without 'function'` line does not name "
                  "function=\"status\": %r" % (call_line or "<line missing>")],
                 detail=["the old line was `Call without 'function' for "
                         "status.` -- no spelling a caller could pass",
                         "line: %s" % call_line], text=call_line)


# ---------------------------------------------------------------------------
# Group C -- hygiene
# ---------------------------------------------------------------------------

def group_c(suite, before, pyc_before):
    after = H.repo_tree()
    new = sorted(after - before)
    suite.record("C", "no-new-repo-paths",
                 [] if not new else
                 ["this suite wrote into the repo tree: %s" % new[:12]],
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    # A delta, not the absolute form: `tests/test_purity_lsp.py:_pyc_problems`
    # is where zero is asserted outright.
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after
                     if pyc_before.get(p) != pyc_after[p])
    suite.record("C", "no-new-bytecode",
                 [] if not changed else
                 ["this run wrote or touched bytecode: %s" % changed[:6]],
                 detail=["%d .pyc before, %d after" % (len(pyc_before),
                                                        len(pyc_after))])


# ---------------------------------------------------------------------------

def run(opts=None):
    """Import the server module, drive the dispatcher, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="forge_call dispatch: `status` is the empty call on "
                          "all four of its paths, and is advertised wherever "
                          "the function list is",
                    opts=opts, mode="stream", group_width=3, cid_width=36)

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    mod = H.load_module_from_path("mcp_forge_dispatch", SERVER)

    with H.TempWorkspace("ph-forge-dispatch-", keep=opts.keep) as ws:
        suite.note("      server  : %s" % SERVER)
        suite.note("      fixture : %s" % ws.path)
        group_a(suite, mod, ws)
        group_b(suite, mod, ws)

    group_c(suite, before, pyc_before)
    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
