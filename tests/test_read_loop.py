#!/usr/bin/env python3
"""Every MCP server's read loop carries ADR 0008's shape (A-F).

WHY THIS IS A SUITE AND NOT AN AUDIT
------------------------------------
`docs/adr/0008` decided TWO things, and said in as many words that either alone
is not the fix:

  * each message becomes its own task, and
  * `sys.stdin.readline` runs on a **separate single-thread executor** --
    "With one shared pool, saturating it starves the readline and reintroduces
    the same deafness with more machinery.  The reader must own a thread no
    handler can take."

`f1d117b` converted fourteen servers in one pass.  `Scripts/mcp-webfetch.py` was
not one of them -- it already dispatched per message, so a sweep looking for the
awaits-the-handler-inline shape did not see it, and it kept `run_in_executor(None,
sys.stdin.readline)` with its handlers parked on that SAME default pool.  That is
not half a defect, it is a higher threshold for the whole one: the stdlib default
pool holds `min(32, cpu_count + 4)` threads, so the server went deaf at 16
concurrent fetches on a 12-core box instead of at 8.  MEASURED, against a
blackhole listener: 20 fetches + a trailing ping, and the pre-fix shape read 17
of 21 messages and then stopped consuming stdin entirely.

Nothing in `tests/` asserted any of this.  A fleet-wide invariant established by
a fourteen-server rollout was guarded only by a documentation page's freshness
heuristic, which is how webfetch stayed divergent through four later commits that
touched it.  Hence a gate.

WHY A NEW SUITE AND NOT AN EXISTING ONE
---------------------------------------
Every suite here is named by ONE subject line, and this assertion does not fit
any of the four candidates:

  * `spawn_stdin` -- "explicit stdin= at every subprocess spawn site".  Same
    technique (AST over `Scripts/**`), different subject: it is about what a
    CHILD process inherits, not about the shape of the parent's own read loop.
  * `generated_region` -- "generated regions match their canonical source".  The
    read loop is not a generated region and deliberately is not one: the ADR's
    whole point is that the concurrency decision DIVERGES per server while the
    transport does not.
  * `mcp_footprint` -- token footprint.  Unrelated.
  * `smoke` -- "MCP JSON-RPC plumbing invariants across the fleet" is the
    closest subject, and it is still the wrong home for two concrete reasons.
    It is BEHAVIOURAL: it starts all 15 servers, so a static source read would
    pay a process launch it does not need.  And it reports in `unit="servers"`
    with a derived count (`run.py:200`), so case-shaped assertions cannot be
    added to it without changing what it reports.

THE SPLIT IS DECLARED DATA, NOT INFERRED
----------------------------------------
The shape is NOT uniform, and a gate demanding one shape would fail servers that
are correct.  ADR 0008's own table records the divergence: eight servers run
handlers in a worker `ThreadPoolExecutor` (`_serve(loop, workers, msg)`), seven
keep them as coroutines on the single event-loop thread (`_serve(msg)`) because
their per-client `_next_id += 1` / `_pending[id] = fut` pair is safe ONLY there
-- "Moving them into a worker pool, faithfully copying the reference, would have
broken the very invariant that made them safe."

So `FLEET` below is a hand-written table, one row per server, that a reader can
check line-by-line against the ADR.  The suite MEASURES each server and compares
it to its declared row.  Inferring the split at runtime instead would make the
suite agree with whatever the fleet currently does -- a test that proves the
fleet agrees with itself, and that would have passed on the broken webfetch.

What is UNIFORM, and therefore gated identically for all fifteen (group A):
  1. `run()` creates a `ThreadPoolExecutor(max_workers=1)` for the reader;
  2. `sys.stdin.readline` is dispatched on THAT executor -- not `None`, not a
     pool a handler can enter;
  3. no handler can reach the reader's executor (it appears in `run()` only at
     its assignment, at the readline, and at its `shutdown`);
  4. each message is handed to its own task (`loop.create_task` or
     `asyncio.ensure_future` -- both create a task, and gating the SPELLING
     would fail a correct refactor);
  5. no handler is awaited inline on the read loop;
  6. every executor `run()` creates is shut down in `run()`.

Note what is deliberately NOT asserted: that handlers avoid the DEFAULT
executor.  `mcp-purity.py:5810` parks its sync file handlers on it on purpose,
which is fine precisely because the reader is no longer there.  The rule is
about the reader's thread, not about which pool handlers use.

AST, NOT REGEX
--------------
The executor a readline runs on is an argument three lines into a call; the
reader-isolation check needs node IDENTITY (the same `Name` used twice means
two different things depending on where).  Neither survives a line-oriented
regex.

NEGATIVE CONTROL (group D) -- mandatory
---------------------------------------
A checker that silently matches nothing is indistinguishable from a clean fleet,
and this suite was written AFTER the defect it describes was already fixed, so
"it passes" proves nothing on its own.  Group D points the same analyser at
synthetic servers carrying each defect one at a time -- the reader on the
default pool (webfetch's actual bug), the reader on the handler pool (the shape
the ADR names and rejects), a two-thread reader, an inline handler await, a
reader leaked into `_serve`, an executor nobody shuts down -- plus a correct
server that must stay silent.

SURVEY (group E, INFO only) -- `Scripts/MCP_SKELETON.md`
--------------------------------------------------------
Reported, never failed.  The canonical reference's section 5 still shows the
PRE-0008 loop.  Gating a Markdown page is a scope decision for a human; printing
the measurement so the gap is visible rather than invisible is this suite's job,
and follows `spawn_stdin`'s GATED-vs-SURVEYED precedent.

SANDBOX DISCIPLINE -- all fixtures under
`.claude/tmp/test_read_loop/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  The fixture
root is outside the scan root, so a control server can never leak into the live
gate.  Removed in a `finally` unless --keep.

The case count IS typed in run.py's SUITES table, unlike `mcp_footprint`'s
per-server count.  That is deliberate: here, a server appearing without a
declared row IS the defect, so a count that moves when the roster moves is the
alarm working, not noise.

Offline, read-only apart from the sandbox, ~1s.

Usage:
  python3 tests/test_read_loop.py
  python3 tests/test_read_loop.py --brief
  python3 tests/test_read_loop.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- the six uniform invariants, one case per server
  B  DECLARED -- measured shape vs the FLEET table, one case per server
  C  ROSTER   -- the table covers the tree exactly, prefixes unique, split totals
  D  control  -- planted defects the analyser MUST flag, and a correct one it
                 must not
  E  SURVEY   -- MCP_SKELETON.md section 5 (INFO only)
  F  hygiene  -- every write under .claude/tmp, no bytecode, no new repo paths
"""

import ast
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "read_loop"

SCAN_ROOT = "Scripts"
SERVER_GLOB_PREFIX = "mcp-"

SKELETON = "Scripts/MCP_SKELETON.md"

GA = "A. GATE: ADR 0008's shape, every server"
GB = "B. DECLARED: measured shape vs the table"
GC = "C. ROSTER: the table covers the tree"
GD = "D. negative control"
GE = "E. SURVEY (INFO): the canonical skeleton"
GF = "F. hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_read_loop")
WRITES = []

# ---------------------------------------------------------------------------
# THE DECLARED TABLE -- check this against docs/adr/0008's per-server table.
# ---------------------------------------------------------------------------
# server file -> (reader_prefix, dispatch, worker_prefix, worker_reach, note)
#
#   dispatch      "pool"      handlers run in a worker ThreadPoolExecutor;
#                             the loop hands it to `_serve`/`_dispatch`.
#                 "coroutine" handlers are coroutines on the event-loop thread.
#                             The ADR: their `_next_id += 1` and the
#                             `_pending[id] = fut` that follows sit between the
#                             same two awaits, so they are safe ONLY on one
#                             thread.  A worker pool here would be a REGRESSION.
#
#   worker_reach  how the worker pool reaches the handler, or None if there is
#                 no server-owned pool:
#                 "arg"     passed into the dispatch target as a parameter
#                 "global"  a module-level executor the handler closes over
#
# The one row that is neither of the two clean groups is context7: its handlers
# are coroutines (`_serve(msg)`) AND it owns a pool, because the only blocking
# thing it does is one urlopen, which it parks on a module-level `_HTTP_EXECUTOR`
# itself.  A table with two columns could not say that, which is why there are
# four.
FLEET = {
    "mcp-clangd.py":   ("clangd-stdin",   "coroutine", None,             None,
                        "single LSP backend, one lock; unregistered (purity_call)"),
    "mcp-context7.py": ("context7-stdin", "coroutine", "context7-http",  "global",
                        "coroutine handlers, but urlopen is parked on its own pool"),
    "mcp-cuda.py":     ("cuda-stdin",     "coroutine", None,             None,
                        "single LSP backend, one lock; unregistered (purity_call)"),
    "mcp-forge.py":    ("forge-stdin",    "pool",      "forge-call",     "arg",
                        "no serialization needed"),
    "mcp-gdc.py":      ("gdc-stdin",      "coroutine", None,             None,
                        "per-browser-target lane lock; CdpSession id counter"),
    "mcp-git.py":      ("git-stdin",      "pool",      "git-call",       "arg",
                        "one lock over the mutating stash door"),
    "mcp-inspect.py":  ("inspect-stdin",  "pool",      "inspect-call",   "arg",
                        "one lock over warnings.catch_warnings"),
    "mcp-jenkins.py":  ("jenkins-stdin",  "pool",      "jenkins-call",   "arg",
                        "the reference implementation; no mutable module state"),
    "mcp-lldb.py":     ("lldb-stdin",     "coroutine", None,             None,
                        "whole-handler lock; composite handlers span commands"),
    "mcp-lua-lsp.py":  ("luals-stdin",    "coroutine", None,             None,
                        "one lock; unregistered (purity_call)"),
    "mcp-postgres.py": ("pg-stdin",       "pool",      "pg-call",        "arg",
                        "per-connection lock across the whole exchange"),
    "mcp-purity.py":   ("purity-stdin",   "coroutine", None,             None,
                        "coroutine handlers; sync file ops park on the DEFAULT "
                        "pool on purpose (:5810) -- safe now the reader is not there"),
    "mcp-tshark.py":   ("tshark-stdin",   "pool",      "tshark-call",    "arg",
                        "registry + config locks"),
    "mcp-webfetch.py": ("webfetch-stdin", "pool",      "webfetch-call",  "arg",
                        "converted here; blocking curl_cffi/primp fetch needs a "
                        "thread, and its timeout is caller-supplied and unclamped"),
    "mcp-wiki.py":     ("wiki-stdin",     "pool",      "wiki-call",      "arg",
                        "none -- its index is build-fresh-and-swap"),
}

# Declared totals, so a silent re-classification of one server trips a case
# rather than sliding through as "the table matches the table".
DECLARED_POOL = 8
DECLARED_COROUTINE = 7

# Both spellings create a task.  Gating one would fail a correct refactor.
TASK_FACTORIES = {"create_task", "ensure_future"}

# Method names that MUST NOT be awaited on the read loop itself.
HANDLER_NAMES = {"_handle_message", "handle_message"}

DISPATCH_TARGETS = {"_serve", "_dispatch"}

# Problem codes.  The control group asserts these by name.
NO_SERVER_CLASS = "NO-MCPSERVER-CLASS"
NO_RUN = "NO-RUN-METHOD"
NO_READER = "NO-READER-EXECUTOR"
NO_READLINE = "READLINE-NOT-FOUND"
READLINE_DEFAULT = "READLINE-ON-DEFAULT-POOL"
READER_NOT_SOLO = "READER-NOT-SINGLE-THREAD"
READER_SHARED = "READER-REACHABLE-BY-HANDLERS"
NO_DISPATCH = "NO-PER-MESSAGE-DISPATCH"
INLINE_HANDLER = "INLINE-HANDLER-ON-READ-LOOP"
NO_SHUTDOWN = "EXECUTOR-NEVER-SHUTDOWN"


# ---------------------------------------------------------------------------
# the analyser
# ---------------------------------------------------------------------------

class Shape:
    """What one server's `McpServer.run()` actually does."""

    def __init__(self, path):
        self.path = path
        self.problems = []          # problem codes
        self.detail = []            # human lines, one per finding
        self.executors = {}         # var -> (max_workers_src, prefix)
        self.reader_var = None
        self.reader_prefix = None
        self.dispatch = None        # "pool" | "coroutine" | None
        self.worker_var = None
        self.worker_prefix = None
        self.worker_reach = None
        self.task_factory = None

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))

    @property
    def ok(self):
        return not self.problems


def _src(node):
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"


def _find_run(tree):
    """The `async def run` inside `class McpServer`, or (None, why)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "McpServer":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "run":
                    return item, None
            return None, NO_RUN
    return None, NO_SERVER_CLASS


def _executor_assignments(run_node):
    """var -> (max_workers source, thread_name_prefix) for pools built in run()."""
    found = {}
    for node in ast.walk(run_node):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "ThreadPoolExecutor"):
            continue
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        workers = _src(kw["max_workers"]) if "max_workers" in kw else "?"
        prefix = None
        node_prefix = kw.get("thread_name_prefix")
        if isinstance(node_prefix, ast.Constant) and isinstance(node_prefix.value, str):
            prefix = node_prefix.value
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = (workers, prefix)
    return found


def _readline_call(run_node):
    """The `run_in_executor(X, sys.stdin.readline)` Call node, or None."""
    for node in ast.walk(run_node):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_in_executor"):
            continue
        if len(node.args) >= 2 and _src(node.args[1]) == "sys.stdin.readline":
            return node
    return None


def _dispatch_call(run_node):
    """(factory_name, inner Call) for the per-message task, or (None, None)."""
    for node in ast.walk(run_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in TASK_FACTORIES):
            continue
        if not node.args:
            continue
        inner = node.args[0]
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                and inner.func.attr in DISPATCH_TARGETS:
            return func.attr, inner
    return None, None


def analyse(path, source=None):
    """Measure one server file against ADR 0008's uniform half."""
    shape = Shape(path)
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        shape.fail(NO_SERVER_CLASS, "unparseable: %s" % exc)
        return shape

    run_node, why = _find_run(tree)
    if run_node is None:
        shape.fail(why, "no McpServer.run to inspect")
        return shape

    shape.executors = _executor_assignments(run_node)

    # -- 2. the readline, and which executor carries it ---------------------
    call = _readline_call(run_node)
    if call is None:
        shape.fail(NO_READLINE, "no run_in_executor(..., sys.stdin.readline)")
        return shape
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and arg.value is None:
        shape.fail(READLINE_DEFAULT,
                   "line %d: readline runs on the DEFAULT executor, which is "
                   "shared with every handler that parks there" % call.lineno)
        return shape
    if not isinstance(arg, ast.Name):
        shape.fail(NO_READER, "line %d: readline executor is %s, not a named "
                              "executor" % (call.lineno, _src(arg)))
        return shape

    shape.reader_var = arg.id
    if shape.reader_var not in shape.executors:
        shape.fail(NO_READER, "line %d: readline runs on %r, which run() never "
                              "builds as a ThreadPoolExecutor"
                   % (call.lineno, shape.reader_var))
        return shape
    workers_src, shape.reader_prefix = shape.executors[shape.reader_var]
    if workers_src != "1":
        shape.fail(READER_NOT_SOLO,
                   "reader %r has max_workers=%s; the reader must own a thread "
                   "no handler can take" % (shape.reader_var, workers_src))

    # -- 3. nothing but the readline may touch the reader's executor --------
    allowed = {id(arg)}
    for node in ast.walk(run_node):
        if isinstance(node, ast.Attribute) and node.attr == "shutdown" \
                and isinstance(node.value, ast.Name) \
                and node.value.id == shape.reader_var:
            allowed.add(id(node.value))
    for node in ast.walk(run_node):
        if isinstance(node, ast.Name) and node.id == shape.reader_var \
                and isinstance(node.ctx, ast.Load) and id(node) not in allowed:
            shape.fail(READER_SHARED,
                       "line %d: the reader's executor is used as %s -- a handler "
                       "that can enter it can starve the readline"
                       % (node.lineno, _src(node)))

    # -- 4/5. one task per message, no inline handler -----------------------
    factory, inner = _dispatch_call(run_node)
    if factory is None:
        shape.fail(NO_DISPATCH,
                   "no loop.create_task/asyncio.ensure_future around a "
                   "_serve/_dispatch call in run()")
    else:
        shape.task_factory = factory
        pool_args = [a for a in inner.args
                     if isinstance(a, ast.Name) and a.id in shape.executors
                     and a.id != shape.reader_var]
        if pool_args:
            shape.dispatch = "pool"
            shape.worker_var = pool_args[0].id
            _w, shape.worker_prefix = shape.executors[shape.worker_var]
            shape.worker_reach = "arg"
        else:
            shape.dispatch = "coroutine"
            extra = [v for v in shape.executors if v != shape.reader_var]
            if extra:
                shape.worker_var = extra[0]
                _w, shape.worker_prefix = shape.executors[extra[0]]
                shape.worker_reach = "global"

    for node in ast.walk(run_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in HANDLER_NAMES:
            shape.fail(INLINE_HANDLER,
                       "line %d: %s is called on the read loop; while it runs "
                       "the server is not reading" % (node.lineno, _src(node.func)))

    # -- 6. everything run() builds, run() shuts down -----------------------
    closed = set()
    for node in ast.walk(run_node):
        if isinstance(node, ast.Attribute) and node.attr == "shutdown" \
                and isinstance(node.value, ast.Name):
            closed.add(node.value.id)
    for var in sorted(shape.executors):
        if var not in closed:
            shape.fail(NO_SHUTDOWN,
                       "executor %r is never shut down; concurrent.futures' "
                       "atexit hook then joins its threads and the process "
                       "hangs at exit" % var)
    return shape


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _server_files():
    root = H.repo_path(SCAN_ROOT)
    return sorted(n for n in os.listdir(root)
                  if n.startswith(SERVER_GLOB_PREFIX) and n.endswith(".py"))


def group_gate(suite, shapes):
    """A. the six uniform invariants, one case per server."""
    for name in sorted(shapes):
        shape = shapes[name]
        detail = ["file        : %s/%s" % (SCAN_ROOT, name),
                  "reader      : %s (max_workers=1, prefix=%r)"
                  % (shape.reader_var, shape.reader_prefix),
                  "dispatch    : %s via %s"
                  % (shape.dispatch, shape.task_factory),
                  "worker pool : %s"
                  % (shape.worker_prefix or "none (coroutine handlers)")]
        detail += ["  " + d for d in shape.detail]
        suite.record(GA, name, shape.problems, detail=detail,
                     brief="%s | %s | %s" % (H.FAIL if shape.problems else H.PASS,
                                             name, shape.problems or "0008 shape"))


def group_declared(suite, shapes):
    """B. what was measured vs what the table declares."""
    for name in sorted(shapes):
        shape = shapes[name]
        problems = []
        if name not in FLEET:
            suite.record(GB, name, ["not declared in FLEET"],
                         detail=["a new server must be declared, with its "
                                 "concurrency decision, before it can pass"])
            continue
        d_reader, d_dispatch, d_worker, d_reach, note = FLEET[name]
        measured = (shape.reader_prefix, shape.dispatch, shape.worker_prefix,
                    shape.worker_reach)
        declared = (d_reader, d_dispatch, d_worker, d_reach)
        if measured != declared:
            problems.append("measured %r != declared %r" % (measured, declared))
        suite.record(GB, name, problems,
                     detail=["declared    : %r" % (declared,),
                             "measured    : %r" % (measured,),
                             "why         : %s" % note],
                     brief="%s | %s | %s" % (H.FAIL if problems else H.PASS,
                                             name, declared[1]))


def group_roster(suite, shapes, files):
    """C. the table covers the tree exactly, and the split totals hold."""
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
                         "note        : a server added without a row fails here "
                         "AND trips the typed case count in run.py"])

    prefixes = [row[0] for row in FLEET.values()]
    dupes = sorted({p for p in prefixes if prefixes.count(p) > 1})
    suite.record(GC, "reader thread prefixes are unique",
                 [] if not dupes else ["duplicate prefix(es): %s" % dupes],
                 detail=["prefixes    : %d across %d server(s)"
                         % (len(set(prefixes)), len(prefixes)),
                         "note        : the prefix is how a thread dump tells "
                         "the reader from a handler"])

    pool = sorted(n for n, r in FLEET.items() if r[1] == "pool")
    coro = sorted(n for n, r in FLEET.items() if r[1] == "coroutine")
    problems = []
    if len(pool) != DECLARED_POOL:
        problems.append("pool group is %d, declared %d" % (len(pool), DECLARED_POOL))
    if len(coro) != DECLARED_COROUTINE:
        problems.append("coroutine group is %d, declared %d"
                        % (len(coro), DECLARED_COROUTINE))
    suite.record(GC, "the pool/coroutine split matches its declared totals",
                 problems,
                 detail=["pool (%d)    : %s" % (len(pool), ", ".join(pool)),
                         "coroutine(%d): %s" % (len(coro), ", ".join(coro)),
                         "note        : moving a coroutine server into a worker "
                         "pool would break the id-counter invariant ADR 0008 "
                         "says keeps it safe -- so a silent re-classification "
                         "must trip something"])

    parsed = [n for n, s in shapes.items() if s.reader_var]
    suite.record(GC, "the analyser resolved a reader in every server",
                 [] if len(parsed) == len(files)
                 else ["resolved %d of %d" % (len(parsed), len(files))],
                 detail=["resolved    : %d/%d" % (len(parsed), len(files)),
                         "note        : a blindness floor -- an analyser that "
                         "stops resolving anything would otherwise report a "
                         "clean fleet"])


# -- group D fixtures --------------------------------------------------------

GOOD = '''
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor


class McpServer:
    async def run(self):
        loop = asyncio.get_running_loop()
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ctl-stdin")
        workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ctl-call")
        inflight = set()
        try:
            while True:
                line = await loop.run_in_executor(reader, sys.stdin.readline)
                if not line:
                    break
                msg = json.loads(line)
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
        finally:
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)

    async def _serve(self, loop, workers, msg):
        return await loop.run_in_executor(workers, self._handle_message, msg)

    def _handle_message(self, msg):
        return None
'''

FIXTURES = {
    # name -> (source, expected problem codes)
    "ctl_default_pool.py": (
        GOOD.replace("run_in_executor(reader, sys.stdin.readline)",
                     "run_in_executor(None, sys.stdin.readline)"),
        [READLINE_DEFAULT]),
    "ctl_shared_pool.py": (
        GOOD.replace("run_in_executor(reader, sys.stdin.readline)",
                     "run_in_executor(workers, sys.stdin.readline)"),
        [READER_NOT_SOLO, READER_SHARED]),
    "ctl_two_thread_reader.py": (
        GOOD.replace('ThreadPoolExecutor(max_workers=1, thread_name_prefix="ctl-stdin")',
                     'ThreadPoolExecutor(max_workers=2, thread_name_prefix="ctl-stdin")'),
        [READER_NOT_SOLO]),
    "ctl_inline_handler.py": (
        GOOD.replace("                task = loop.create_task(self._serve(loop, workers, msg))\n"
                     "                inflight.add(task)\n",
                     "                response = self._handle_message(msg)\n"),
        [NO_DISPATCH, INLINE_HANDLER]),
    "ctl_reader_leaks.py": (
        GOOD.replace("self._serve(loop, workers, msg)",
                     "self._serve(loop, reader, msg)"),
        [READER_SHARED]),
    "ctl_no_shutdown.py": (
        GOOD.replace("            reader.shutdown(wait=False)\n"
                     "            workers.shutdown(wait=False)\n",
                     "            pass\n"),
        [NO_SHUTDOWN]),
    "ctl_good.py": (GOOD, []),
}


def write_fixtures(root):
    for name, (source, _expected) in sorted(FIXTURES.items()):
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        WRITES.append(path)


def group_control(suite, fixture_root):
    """D. planted defects the analyser MUST flag, and a correct one it must not."""
    write_fixtures(fixture_root)
    flagged_total = 0
    for name, (_source, expected) in sorted(FIXTURES.items()):
        shape = analyse(os.path.join(fixture_root, name))
        got = sorted(shape.problems)
        problems = []
        if got != sorted(expected):
            problems.append("codes %r != expected %r" % (got, sorted(expected)))
        if expected:
            flagged_total += 1
        suite.record(GD, "control-" + name, problems,
                     detail=["expected    : %r" % sorted(expected),
                             "got         : %r" % got]
                            + ["  " + d for d in shape.detail],
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))

    must_flag = sum(1 for _n, (_s, e) in FIXTURES.items() if e)
    suite.record(GD, "control fires at all",
                 [] if flagged_total == must_flag
                 else ["only %d of %d defective fixtures flagged"
                       % (flagged_total, must_flag)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(FIXTURES), must_flag, len(FIXTURES) - must_flag),
                         "note        : this suite was written AFTER the defect "
                         "it describes was fixed, so a green live gate proves "
                         "nothing without this group"])


def group_survey(suite):
    """E. the canonical skeleton, measured and reported -- never failed."""
    path = H.repo_path(SKELETON)
    detail = []
    if not os.path.isfile(path):
        detail = ["%s is absent" % SKELETON]
    else:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        hits = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
                if "run_in_executor(None, sys.stdin.readline)" in ln]
        tasks = [i + 1 for i, ln in enumerate(lines)
                 if any(f in ln for f in TASK_FACTORIES)]
        detail = ["file        : %s" % SKELETON,
                  "readline on the DEFAULT executor at line(s): %s"
                  % (", ".join(str(n) for n, _ in hits) or "none"),
                  "per-message task factory at line(s)        : %s"
                  % (", ".join(str(n) for n in tasks) or "NONE"),
                  "note        : section 5 is the shape a new server is copied "
                  "from.  ADR 0008 converted three servers that never launch "
                  "for exactly this reason -- \"Dead code that gets copied is "
                  "not dead.\"",
                  "note        : INFO, not FAIL.  Gating a Markdown reference "
                  "is a scope call for a human; this suite's job is to stop the "
                  "gap being invisible."]
    suite.record(GE, "MCP_SKELETON.md section 5 vs ADR 0008", [], status=H.INFO,
                 detail=detail)


def group_hygiene(suite, fixture_root, pyc_before, tree_before):
    """F. sandbox discipline, no bytecode, no new repo paths."""
    stray = [p for p in WRITES
             if not os.path.abspath(p).startswith(
                 os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GF, "every write lands under .claude/tmp",
                 [] if not stray
                 else ["%d write(s) outside the sandbox: %s"
                       % (len(stray), ", ".join(stray))],
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d" % len(WRITES)])

    inside = os.path.abspath(FIXTURE_BASE).startswith(
        os.path.abspath(H.repo_path(SCAN_ROOT)) + os.sep)
    suite.record(GF, "fixture root is outside the scan root",
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
    suite.record(GF, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after))])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GF, "no new repo paths",
                 [] if not added else ["%d new path(s): %s" % (len(added), added[:5])],
                 detail=["note        : the scratch area is excluded by "
                         "_harness.repo_tree, so the fixtures are invisible here "
                         "by construction, not by luck"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="every MCP server's read loop carries ADR 0008's "
                          "shape: a single-thread reader executor no handler "
                          "can take, and one task per message",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    files = _server_files()
    shapes = {n: analyse(H.repo_path(SCAN_ROOT, n)) for n in files}

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_gate(suite, shapes)
        group_declared(suite, shapes)
        group_roster(suite, shapes, files)
        group_control(suite, fixture_root)
        group_survey(suite)
        group_hygiene(suite, fixture_root, pyc_before, tree_before)
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
