#!/usr/bin/env python3
"""A tool-handler catch-all must leave a traceback at a level that is on by
default (A-E).

THE DEFECT
----------
`Scripts/_mcp_logging.py:102` is one line and it decides everything here:

    level = logging.DEBUG if (debug or log_file) else logging.WARNING

With no flags -- which is how every one of these servers is launched by Claude
Code -- the logger sits at WARNING.  A catch-all that reports a crashed handler
with `log.debug` therefore reports it to nobody.  The exception is swallowed,
the caller gets a one-line "Error: ..." with no type and no frame, and the
stack that would say WHERE is gone before anything could have written it down.

MEASURED at the commit this suite was written against: six of the fifteen
servers do exactly that -- `mcp-clangd.py`, `mcp-context7.py`, `mcp-cuda.py`,
`mcp-gdc.py`, `mcp-lldb.py` and `mcp-lua-lsp.py`, which is precisely the
`_dispatch_tool` family plus nothing else.  The other nine already use
`log.exception`, which is ERROR (40) > WARNING (30) and carries `exc_info`
implicitly.  The split is not a style disagreement; it is the difference
between a diagnosable failure and an invisible one.

THE INVARIANT, AS ONE SENTENCE
------------------------------
Every declared catch-all site must contain a logging call that BOTH preserves
the traceback AND sits at a level the default configuration emits.

  * `log.exception(...)` qualifies on its own -- ERROR, `exc_info` implicit.
  * `log.error/critical/warning(..., exc_info=True)` qualifies too.  Accepting
    these is deliberate: the rule is the PROPERTY, and gating the spelling
    `exception` would fail a correct refactor for no reason.
  * `log.debug` and `log.info` never qualify, `exc_info=True` or not, because
    the level gate drops the record before any handler sees it.
  * `exc_info=False` never qualifies.  It is the same as not passing it, and a
    reviewer skimming for the keyword would be reassured by it.

TWO SITE LAYERS, AND THE FLEET HAS BOTH
---------------------------------------
There is no single catch-all.  A server has one or two, and which it has is a
structural fact about how it dispatches:

  * layer **W** -- the broad `except Exception` inside `McpServer`'s
    `_handle_tool_call` (nine servers) or `_dispatch_tool` (six).  ALL FIFTEEN
    have this one.  It is the last guard before the JSON-RPC transport.
  * layer **D** -- the broad `except Exception` inside the module-level
    `handle_<x>_call` dispatcher.  NINE servers have it.

The six without a layer D are absent for TWO different reasons, and the table's
note column keeps them apart because merging them would record a coincidence as
a rule.  Five are the `_dispatch_tool` family: their module-level dispatcher
ends in `return await handler(...)` and deliberately owns no broad guard, so
layer W is the only one.  `mcp-git.py` is the sixth and is NOT that shape -- it
has `_handle_tool_call` like the nine, and its `handle_git_call` simply raises
nothing broad of its own.  Absent-because-the-family and
absent-because-this-one-does-not are not the same fact.

DECLARED DATA, NOT INFERRED
---------------------------
`FLEET` below is hand-written, one row per server, each with a prose reason.
The suite MEASURES each file and compares.  Inferring the layers from the tree
would produce a suite that agrees with whatever the fleet currently does -- it
would have passed, green, on the six silent servers, since "every catch-all the
analyser can find" is trivially satisfied by finding them and asking nothing.
This is `tests/test_read_loop.py`'s and `tests/test_wire_log.py`'s pattern and
it is here for their reason.

THE ONE SECURITY CLAUSE, AND WHY IT IS ONLY ONE LINE
----------------------------------------------------
The logging call's format string -- its first positional argument -- must be a
literal `str`.  All nine correct servers already satisfy it; every one passes a
constant and lets `%`-args carry the function name.

That clause exists to catch the f-string accident, `log.exception(f"crashed on
{params}")`, which would interpolate a caller-supplied payload into a log file
at a level that IS emitted -- CWE-532, the concern `docs/adr/0011` was written
about.

What this suite deliberately does NOT do is analyse the remaining arguments.
`tests/test_wire_log.py:93-102` refuses to grow a handler survey for a stated
reason -- "folding it in here would turn one crisp invariant into a survey" --
and the same refusal applies in the other direction.  ADR 0011 bounds two named
wire functions; this suite bounds the catch-all's LEVEL and the literalness of
its format string.  Anything more would be re-implementing that suite's
payload-root machinery at a site it declared out of scope.

DECLARED BLIND SPOTS -- on the page, because an unstated scope is the same
defect as a false invariant:

  1. **This gate reads structure and can never prove a line reached stderr.**
     It proves the call is written, at a qualifying level, in the right block.
     A logger reconfigured at runtime, a handler removed, a `logging.disable`
     anywhere -- all invisible here.  The destination is bounded by
     `Scripts/_mcp_logging.py`, which is a generated region gated elsewhere.
  2. **Traceback CONTENT is invisible to any AST analyser, by construction.**
     `log.exception(msg, *args)` pulls `exc_info` from `sys.exc_info()` inside
     the stdlib's logging module.  It is never an argument node, so no
     source-level analysis can see what a frame will render.  Only the LEVEL
     and the DESTINATION bound it, and both are outside this file.
  3. **Narrow handlers are not read.**  Only `except Exception` and bare
     `except:` are judged.  A server that moved its last-resort guard to
     `except BaseException` would not be seen -- deliberately, since catching
     BaseException is a different defect with a different argument.

NEGATIVE CONTROL (group D) -- mandatory
---------------------------------------
A checker that matches nothing is indistinguishable from a clean fleet.  Group
D points the same analyser at synthetic servers carrying one defect each --
`log.debug` in the catch-all, `log.error` with no `exc_info`, an explicit
`exc_info=False`, an f-string format, a bare `except Exception: pass`, a
catch-all that returns a result and logs nothing, and a `log.exception` sitting
in a DIFFERENT function so the scoping is proven rather than assumed -- plus
three CORRECT forms it must stay silent on.

SANDBOX DISCIPLINE -- all fixtures under
`.claude/tmp/test_handler_crash/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  The
fixture root is outside the scan root, so a control server can never leak into
the live gate.  Removed in a `finally` unless --keep.

The case count IS typed in run.py's SUITES table: a server appearing without a
declared row IS the defect, so a count that moves when the roster moves is the
alarm working, not noise.

AST only.  Starts nothing, imports no server, ~1s.

Usage:
  python3 tests/test_handler_crash.py
  python3 tests/test_handler_crash.py --brief
  python3 tests/test_handler_crash.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- one case per declared catch-all site (15 W + 9 D)
  B  DECLARED -- measured layers vs the FLEET table, one case per server
  C  ROSTER   -- the table covers the tree, totals hold, no row may declare a
                 site away
  D  control  -- planted defects the analyser MUST flag, and correct forms it
                 must not
  E  hygiene  -- every write under .claude/tmp, no bytecode, no new repo paths
"""

import ast
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "handler_crash"

SCAN_ROOT = "Scripts"
SERVER_GLOB_PREFIX = "mcp-"

GA = "A. GATE: every catch-all leaves a traceback"
GB = "B. DECLARED: measured layers vs the table"
GC = "C. ROSTER: the table covers the tree"
GD = "D. negative control"
GE = "E. hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_handler_crash")
WRITES = []

# ---------------------------------------------------------------------------
# THE DECLARED TABLE
# ---------------------------------------------------------------------------
# server file -> (w_method, d_shape, note)
#
#   w_method   which McpServer method carries layer W.  Every server has one.
#              W_HANDLE   `_handle_tool_call`  (sync or async)
#              W_DISPATCH `_dispatch_tool`
#
#   d_shape    whether the module-level `handle_<x>_call` owns a broad
#              `except Exception`, and when it does not, WHY -- the two reasons
#              are different facts and the table refuses to merge them:
#              D_PRESENT     it has one; both layers are gated
#              D_NONE_FAMILY the `_dispatch_tool` family: the dispatcher ends in
#                            `return await handler(...)` and owns no broad guard
#              D_NONE_OWN    it is NOT that family -- it has `_handle_tool_call`
#                            like the nine -- and simply raises nothing broad of
#                            its own
W_HANDLE = "_handle_tool_call"
W_DISPATCH = "_dispatch_tool"

D_PRESENT = "present"
D_NONE_FAMILY = "absent (_dispatch_tool family)"
D_NONE_OWN = "absent (own dispatcher raises nothing broad)"

FLEET = {
    "mcp-clangd.py":   (W_DISPATCH, D_NONE_FAMILY,
                        "handle_clangd_call is try/finally with no except; "
                        "every handler exception lands at layer W"),
    "mcp-context7.py": (W_DISPATCH, D_NONE_FAMILY,
                        "handle_context7_call ends in a handler call; the "
                        "per-handler guards are narrow and return dicts"),
    "mcp-cuda.py":     (W_DISPATCH, D_NONE_FAMILY,
                        "same shape as clangd, from which it was copied"),
    "mcp-forge.py":    (W_HANDLE, D_PRESENT,
                        "tabs; layer D sits after narrow (ValueError, OSError)"),
    "mcp-gdc.py":      (W_DISPATCH, D_NONE_FAMILY,
                        "handle_gdc_call ends in `return await handler(...)`"),
    "mcp-git.py":      (W_HANDLE, D_NONE_OWN,
                        "NOT the dispatch family -- it has _handle_tool_call \
 like the nine; handle_git_call owns no broad guard, so the file holds \
exactly two `except Exception` in total"),
    "mcp-inspect.py":  (W_HANDLE, D_PRESENT,
                        "layer D is commented `a handler bug must not hang the \
client`; it also logs a per-validator crash separately"),
    "mcp-jenkins.py":  (W_HANDLE, D_PRESENT,
                        "both layers route through _err so _is_error reads one \
shape; the only rows whose log message omits an `Internal error` prefix"),
    "mcp-lldb.py":     (W_DISPATCH, D_NONE_FAMILY,
                        "handle_lldb_call ends in a handler call; its ~25 \
per-command guards are narrow and return dicts"),
    "mcp-lua-lsp.py":  (W_DISPATCH, D_PRESENT,
                        "the only _dispatch_tool server that ALSO owns a layer \
D, and the only server SILENT at both; layer D sits after narrow ValueError and \
RuntimeError clauses"),
    "mcp-postgres.py": (W_HANDLE, D_PRESENT,
                        "layer D sits after a narrow PgError clause that keeps \
server-reported errors on their own channel"),
    "mcp-purity.py":   (W_HANDLE, D_PRESENT,
                        "the canonical form both layers are gated against"),
    "mcp-tshark.py":   (W_HANDLE, D_PRESENT,
                        "layer D after a narrow clause; canonical wording"),
    "mcp-webfetch.py": (W_HANDLE, D_PRESENT,
                        "tabs; the fetch path has its own narrow guard that \
returns a dict before layer D can see it"),
    "mcp-wiki.py":     (W_HANDLE, D_PRESENT,
                        "layer D is the one using %-formatting for the returned \
message rather than an f-string"),
}

# Declared totals, so a silent re-classification of one server trips a case
# rather than sliding through as "the table matches the table".
DECLARED_W = 15
DECLARED_D = 9

# ---------------------------------------------------------------------------
# the rule, as data
# ---------------------------------------------------------------------------

LOG_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}

# Levels the default WARNING configuration actually emits.
EMITTED_LEVELS = {"warning", "error", "exception", "critical"}

# `exception` carries exc_info implicitly; the rest must ask for it.
IMPLICIT_TRACEBACK = {"exception"}

W_METHODS = {W_HANDLE, W_DISPATCH}

# Problem codes.  The control group asserts these by name.
NO_SERVER_CLASS = "NO-MCPSERVER-CLASS"
NO_SITE = "CATCH-ALL-NOT-FOUND"
NO_LOG = "CATCH-ALL-LOGS-NOTHING"
BELOW_DEFAULT = "LOGGED-BELOW-DEFAULT-LEVEL"
NO_TRACEBACK = "TRACEBACK-DISCARDED"
DYNAMIC_FORMAT = "NON-LITERAL-FORMAT-STRING"
NOT_DECLARED = "SERVER-NOT-IN-TABLE"


# ---------------------------------------------------------------------------
# the analyser
# ---------------------------------------------------------------------------

def _src(node):
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"


def _own_nodes(fn):
    """Every node inside `fn` that is not inside a NESTED callable.

    Scoping matters and is asserted by ctl_log_elsewhere: a `log.exception` in
    a neighbouring function must not certify a catch-all that logs nothing.
    """
    out = []
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            stack.append(child)
    return out


def _is_broad(handler):
    """`except Exception` or a bare `except:` -- never a narrow clause."""
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _broad_handlers(fn):
    return [n for n in _own_nodes(fn)
            if isinstance(n, ast.ExceptHandler) and _is_broad(n)]


def _log_calls(handler):
    """(level, Call) for every `<anything>.<level>(...)` in this except block.

    Not restricted to a set of receiver names: a logger reached by any spelling
    is still a logger, and filtering by name would let `self._log.debug` pass.
    """
    out = []
    stack = list(handler.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in LOG_LEVELS:
            out.append((node.func.attr, node))
        stack.extend(ast.iter_child_nodes(node))
    return out


def _keeps_traceback(level, call):
    if level in IMPLICIT_TRACEBACK:
        return True
    for kw in call.keywords:
        if kw.arg == "exc_info":
            return not (isinstance(kw.value, ast.Constant)
                        and kw.value.value is False)
    return False


def _literal_format(call):
    return bool(call.args) and isinstance(call.args[0], ast.Constant) \
        and isinstance(call.args[0].value, str)


class Site:
    """One catch-all: where it is, and whether it reports."""

    def __init__(self, layer, owner):
        self.layer = layer          # "W" | "D"
        self.owner = owner          # function name, or None when absent
        self.present = False
        self.lineno = None
        self.problems = []
        self.detail = []

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))

    @property
    def ok(self):
        return not self.problems


def _judge(site, handlers):
    """Apply the invariant to every broad handler of one site.

    ALL of them must report: a function with two last-resort guards has two
    ways to go quiet, and gating only the first would be a hole shaped exactly
    like the defect this suite exists for.
    """
    for handler in handlers:
        calls = _log_calls(handler)
        if not calls:
            site.fail(NO_LOG,
                      "line %d: the catch-all writes nothing; with the default "
                      "WARNING level this crash reaches nobody at all"
                      % handler.lineno)
            continue
        good = [(lv, c) for lv, c in calls
                if lv in EMITTED_LEVELS and _keeps_traceback(lv, c)]
        if not good:
            if all(lv not in EMITTED_LEVELS for lv, _c in calls):
                site.fail(BELOW_DEFAULT,
                          "line %d: logged at %s, and _mcp_logging.py sets "
                          "WARNING with no flags -- the record is dropped "
                          "before any handler sees it"
                          % (handler.lineno,
                             "/".join(sorted({lv for lv, _c in calls}))))
            else:
                site.fail(NO_TRACEBACK,
                          "line %d: %s is emitted but carries no exc_info, so "
                          "the frames are gone and only the message survives"
                          % (handler.lineno,
                             "/".join(sorted({lv for lv, _c in calls}))))
            continue
        if not any(_literal_format(c) for _lv, c in good):
            site.fail(DYNAMIC_FORMAT,
                      "line %d: the format string is not a literal (%s) -- an "
                      "interpolated payload would land in the log at an "
                      "emitted level"
                      % (handler.lineno, _src(good[0][1].args[0])
                         if good[0][1].args else "no positional args"))


def analyse(path, source=None):
    """Measure one server file's two catch-all layers."""
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    w = Site("W", None)
    d = Site("D", None)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        w.fail(NO_SERVER_CLASS, "unparseable: %s" % exc)
        return w, d

    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "McpServer":
            cls = node
            break
    if cls is None:
        w.fail(NO_SERVER_CLASS, "no class McpServer to inspect")
    else:
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and item.name in W_METHODS and w.owner is None:
                w.owner = item.name
                handlers = _broad_handlers(item)
                if handlers:
                    w.present = True
                    w.lineno = handlers[0].lineno
                    _judge(w, handlers)
                else:
                    w.fail(NO_SITE,
                           "%s holds no broad `except Exception`, so a handler "
                           "crash escapes to the transport catch-all unnamed"
                           % item.name)
        if w.owner is None:
            w.fail(NO_SITE, "McpServer has neither %s nor %s"
                   % (W_HANDLE, W_DISPATCH))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("handle_") \
                and node.name.endswith("_call") and d.owner is None:
            d.owner = node.name
            handlers = _broad_handlers(node)
            if handlers:
                d.present = True
                d.lineno = handlers[0].lineno
                _judge(d, handlers)
    return w, d


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _server_files():
    root = H.repo_path(SCAN_ROOT)
    return sorted(n for n in os.listdir(root)
                  if n.startswith(SERVER_GLOB_PREFIX) and n.endswith(".py"))


def group_gate(suite, sites):
    """A. one case per DECLARED site: does it leave a traceback?"""
    for name in sorted(sites):
        w, d = sites[name]
        row = FLEET.get(name)
        layers = [("W", w)]
        # A declared layer D is gated whether or not the analyser found it --
        # losing one silently is the defect, not an excuse to skip the case.
        # An UNDECLARED server contributes a D case only if it actually has
        # one; its missing row is group B's complaint, not this group's.
        wants_d = (row[1] == D_PRESENT) if row else d.present
        if wants_d:
            layers.append(("D", d))
        for layer, site in layers:
            detail = ["file        : %s/%s" % (SCAN_ROOT, name),
                      "layer       : %s (%s)" % (layer, site.owner or "none"),
                      "line        : %s" % (site.lineno or "n/a")]
            detail += ["  " + line for line in site.detail]
            if site.ok:
                detail.append("note        : traceback preserved at a level the "
                              "default WARNING configuration emits")
            suite.record(GA, "%s [%s]" % (name, layer), site.problems,
                         detail=detail,
                         brief="%s | %s [%s] | %s"
                               % (H.FAIL if site.problems else H.PASS, name,
                                  layer, site.problems or "reports"))


def group_declared(suite, sites):
    """B. measured layers vs the table, one case per server."""
    for name in sorted(sites):
        w, d = sites[name]
        if name not in FLEET:
            suite.record(GB, name, [NOT_DECLARED],
                         detail=["a new server must be declared, with which "
                                 "method carries layer W and whether it owns a "
                                 "layer D, before it can pass"])
            continue
        d_method, d_shape, note = FLEET[name]
        measured = (w.owner, D_PRESENT if d.present else "absent")
        declared = (d_method, D_PRESENT if d_shape == D_PRESENT else "absent")
        problems = []
        if measured != declared:
            problems.append("measured %r != declared %r" % (measured, declared))
        suite.record(GB, name, problems,
                     detail=["declared    : W=%s, D=%s" % (d_method, d_shape),
                             "measured    : W=%s, D=%s"
                             % (w.owner, "present" if d.present
                                else "absent (%s)" % (d.owner or "no dispatcher")),
                             "why         : %s" % note],
                     brief="%s | %s | %s"
                           % (H.FAIL if problems else H.PASS, name, d_shape))


def group_roster(suite, sites, files):
    """C. the table covers the tree, its totals hold, it cannot declare a site away."""
    declared = set(FLEET)
    present = set(files)
    problems = []
    if declared != present:
        missing = sorted(present - declared)
        stale = sorted(declared - present)
        if missing:
            problems.append("undeclared server(s): %s" % ", ".join(missing))
        if stale:
            problems.append("declared but absent: %s" % ", ".join(stale))
    suite.record(GC, "every server file has a declared row", problems,
                 detail=["in tree     : %d" % len(present),
                         "declared    : %d" % len(declared),
                         "note        : a server added without a row fails "
                         "group B by name AND trips the typed case count in "
                         "run.py"])

    w_rows = [n for n, r in FLEET.items() if r[0] in W_METHODS]
    d_rows = sorted(n for n, r in FLEET.items() if r[1] == D_PRESENT)
    problems = []
    if len(w_rows) != DECLARED_W:
        problems.append("layer-W rows are %d, declared %d"
                        % (len(w_rows), DECLARED_W))
    if len(d_rows) != DECLARED_D:
        problems.append("layer-D rows are %d, declared %d"
                        % (len(d_rows), DECLARED_D))
    suite.record(GC, "the declared layer split matches its totals", problems,
                 detail=["layer W (%d) : every server, %d via %s and %d via %s"
                         % (len(w_rows),
                            sum(1 for r in FLEET.values() if r[0] == W_HANDLE),
                            W_HANDLE,
                            sum(1 for r in FLEET.values() if r[0] == W_DISPATCH),
                            W_DISPATCH),
                         "layer D (%d) : %s" % (len(d_rows), ", ".join(d_rows)),
                         "note        : gaining or losing a catch-all is a "
                         "decision about where a crash is caught, so it must "
                         "trip something rather than slide through"])

    bad = []
    for name in sorted(FLEET):
        _w_method, d_shape, _note = FLEET[name]
        if d_shape == D_PRESENT or name not in sites:
            continue
        _w, d = sites[name]
        if d.present:
            bad.append("%s declares layer D %r but %s holds a broad "
                       "`except Exception` at line %d"
                       % (name, d_shape, d.owner, d.lineno))
    suite.record(GC, "no row declares a site the analyser can find", bad,
                 detail=["absent rows : %d"
                         % sum(1 for r in FLEET.values() if r[1] != D_PRESENT),
                         "note        : without this case the table is a hole "
                         "in the gate -- a failing server could be 'fixed' by "
                         "declaring its catch-all absent"])

    resolved = [n for n, (w, _d) in sites.items() if w.owner]
    suite.record(GC, "the analyser resolved a layer W in every server",
                 [] if len(resolved) == len(files)
                 else ["resolved %d of %d: missing %s"
                       % (len(resolved), len(files),
                          ", ".join(sorted(set(files) - set(resolved))))],
                 detail=["resolved    : %d/%d" % (len(resolved), len(files)),
                         "note        : a blindness floor -- an analyser that "
                         "stopped finding the method would report a clean "
                         "fleet while measuring nothing"])


# -- group D fixtures --------------------------------------------------------

GOOD = '''
import logging

log = logging.getLogger("ctl")


def handle_ctl_call(arguments):
    function = (arguments.get("function") or "").strip()
    try:
        return {"ok": function}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("Unhandled exception in handler '%s'", function)
        return {"error": f"Internal error: {type(exc).__name__}: {exc}"}


class McpServer:
    def _handle_tool_call(self, msg_id, params):
        try:
            result = handle_ctl_call(params.get("arguments") or {})
        except Exception as exc:
            log.exception("Unhandled exception in handle_ctl_call")
            result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}
        return self._result(msg_id, result)
'''

# The layer-W logging line, which every defective fixture rewrites.
W_LOG = '            log.exception("Unhandled exception in handle_ctl_call")\n'
W_BLOCK = (
    '        except Exception as exc:\n'
    + W_LOG
    + '            result = {"error": f"Internal server error: '
      '{type(exc).__name__}: {exc}"}\n'
)

FIXTURES = {
    # name -> (source, expected problem codes at (W, D))
    "ctl_debug.py": (
        GOOD.replace(W_LOG,
                     '            log.debug("handler error: %s", exc)\n'),
        ([BELOW_DEFAULT], [])),
    "ctl_error_no_excinfo.py": (
        GOOD.replace(W_LOG,
                     '            log.error("handler failed: %s", exc)\n'),
        ([NO_TRACEBACK], [])),
    "ctl_excinfo_false.py": (
        GOOD.replace(W_LOG,
                     '            log.error("handler failed", exc_info=False)\n'),
        ([NO_TRACEBACK], [])),
    "ctl_fstring.py": (
        GOOD.replace(W_LOG,
                     '            log.exception(f"crashed on {msg_id}")\n'),
        ([DYNAMIC_FORMAT], [])),
    "ctl_bare_pass.py": (
        GOOD.replace(W_BLOCK, '        except Exception:\n            pass\n'),
        ([NO_LOG], [])),
    "ctl_no_log.py": (
        GOOD.replace(W_LOG, ''),
        ([NO_LOG], [])),
    "ctl_log_elsewhere.py": (
        GOOD.replace(W_LOG, '')
        + '\n\ndef _unrelated(exc):\n'
          '    log.exception("this one is not the catch-all")\n',
        ([NO_LOG], [])),
    "ctl_good.py": (GOOD, ([], [])),
    "ctl_good_extra_args.py": (
        GOOD.replace(W_LOG,
                     '            log.exception("Unhandled exception in %s",'
                     ' "handle_ctl_call")\n'),
        ([], [])),
    "ctl_good_error_excinfo.py": (
        GOOD.replace(W_LOG,
                     '            log.error("Unhandled exception in '
                     'handle_ctl_call", exc_info=True)\n'),
        ([], [])),
}


def write_fixtures(root):
    for name, (source, _expected) in sorted(FIXTURES.items()):
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        WRITES.append(path)


def group_control(suite, fixture_root):
    """D. planted defects the analyser MUST flag, and correct forms it must not."""
    write_fixtures(fixture_root)
    flagged = 0
    for name, (_source, expected) in sorted(FIXTURES.items()):
        w, d = analyse(os.path.join(fixture_root, name))
        exp_w, exp_d = expected
        got = (sorted(w.problems), sorted(d.problems))
        want = (sorted(exp_w), sorted(exp_d))
        problems = []
        if got != want:
            problems.append("codes %r != expected %r" % (got, want))
        if exp_w or exp_d:
            flagged += 1
        suite.record(GD, "control-" + name, problems,
                     detail=["expected    : W=%r D=%r" % want,
                             "got         : W=%r D=%r" % got]
                            + ["  " + line for line in w.detail + d.detail],
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got[0]))

    must_flag = sum(1 for _n, (_s, e) in FIXTURES.items() if e[0] or e[1])
    suite.record(GD, "control fires at all",
                 [] if flagged == must_flag
                 else ["only %d of %d defective fixtures flagged"
                       % (flagged, must_flag)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(FIXTURES), must_flag,
                            len(FIXTURES) - must_flag),
                         "note        : without this group, an analyser that "
                         "silently matched nothing would be indistinguishable "
                         "from a fleet that already reports"])


def group_hygiene(suite, pyc_before, tree_before):
    """E. sandbox discipline, no bytecode, no new repo paths."""
    stray = [p for p in WRITES
             if not os.path.abspath(p).startswith(
                 os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GE, "every write lands under .claude/tmp",
                 [] if not stray
                 else ["%d write(s) outside the sandbox: %s"
                       % (len(stray), ", ".join(stray))],
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d" % len(WRITES)])

    inside = os.path.abspath(FIXTURE_BASE).startswith(
        os.path.abspath(H.repo_path(SCAN_ROOT)) + os.sep)
    suite.record(GE, "fixture root is outside the scan root",
                 [] if not inside
                 else ["the sandbox lives inside %s, so a control server could "
                       "leak into the live gate" % SCAN_ROOT],
                 detail=["sandbox     : %s"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "scan root   : %s" % SCAN_ROOT])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GE, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after))])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GE, "no new repo paths",
                 [] if not added
                 else ["%d new path(s): %s" % (len(added), added[:5])],
                 detail=["note        : the scratch area is excluded by "
                         "_harness.repo_tree, so the fixtures are invisible "
                         "here by construction, not by luck"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="every MCP server's tool-handler catch-all leaves a "
                          "traceback at a level the default configuration "
                          "emits, at both declared site layers",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    files = _server_files()
    sites = {n: analyse(H.repo_path(SCAN_ROOT, n)) for n in files}

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_gate(suite, sites)
        group_declared(suite, sites)
        group_roster(suite, sites, files)
        group_control(suite, fixture_root)
        group_hygiene(suite, pyc_before, tree_before)
    finally:
        if opts.keep:
            print("\n[--keep] fixtures retained at: %s" % fixture_root)
        else:
            shutil.rmtree(fixture_root, ignore_errors=True)

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
