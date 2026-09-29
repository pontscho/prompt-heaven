#!/usr/bin/env python3
"""Every MCP server honours `notifications/cancelled` (roadmap R-0006, phase 1).

WHAT IS GATED
-------------
An MCP client that gives up on a request sends

    {"jsonrpc": "2.0", "method": "notifications/cancelled",
     "params": {"requestId": <id>, "reason": "..."}}

and the spec says the receiver SHOULD stop the work and MUST NOT answer the
notification.  Before R-0006 every server in this fleet dropped it through the
generic `if msg_id is None: return None` notification branch: the work ran to
completion and its reply was written against an id the client had abandoned.

Phase 1 is fleet-wide reply suppression plus task cancellation.  Each server's
`McpServer.run()` now keeps a registry mapping a request id to its in-flight
task, and handles the notification ON THE READ LOOP, before anything is
dispatched: a well-formed requestId that names a registered task gets
`task.cancel()`, and a CancelledError at the task's await means `_serve` writes
nothing.  The notification itself is never answered and never dispatched.

The requestId parse is HAND-WRITTEN in every server -- ADR 0012 decided the
transport tier is not shared, so there is no generated block for it -- and this
suite is the gate that keeps fifteen hand copies saying the same thing (the ADR
0015 precedent: a hand-written rule with a behavioural gate beside it).  The
live half is in `Scripts/_mcp_smoke_test.py` (`cancel_notification_checks` on
every server, plus one in-flight probe); this half is the AST.

Per server (group A), one case, the problems named by code:

  * a dict registry built in run(), referenced by the hook AND filled after
    dispatch, keyed by the message's `id`, forgotten by a done-callback;
  * the hook is an `if` on "notifications/cancelled" in run() itself -- the
    loop thread -- placed BEFORE the task factory, ending in `continue`;
  * the hook reads `requestId`, guards `params` with isinstance(..., dict),
    refuses a bool (True == 1 and hash(True) == hash(1), so accepting it lets
    `requestId: true` cancel request 1), calls `.cancel()`, and writes nothing;
  * `initialize` is excluded from the registry -- the handshake is never
    cancellable;
  * the dispatch target (`_serve` / `_dispatch`) does not swallow the
    CancelledError: no bare except, no BaseException, no CancelledError
    handler that fails to re-raise.

The helpers the hook and the tracking statement call are followed through
`self.<method>` calls, so a server may spell the parse in a helper or inline.

THE RECLAIM CLASS IS DECLARED DATA
----------------------------------
What a cancel actually RECLAIMS is not uniform, and the table below says which
it is per server, one column, so phase 2 changes a word and not a design:

  task        the asyncio work itself stops -- the handlers are coroutines on
              the loop and the CancelledError lands inside them (gdc, lldb).
  reply-only  the reply is suppressed but the work runs on: a worker thread,
              a subprocess, or a language server's own request keeps going
              until it finishes (the other thirteen).

Phase 2 moves forge and tshark to a `kill` class (the subprocess is killed) and
the LSP hosts to `$/cancelRequest`; each new value needs its own measurement
here before a row may claim it.  What IS measured today (group B): a `task` row
must be a `coroutine` server in `test_read_loop`'s own analysis, because a
handler parked in a worker thread is out of reach of any CancelledError.

NEGATIVE CONTROL (group D) -- mandatory, and this suite was written RED: the
fleet had no hook when it was first run, so the controls are what prove each
problem code can fire on its own.

GATE (group E) -- `Scripts/MCP_SKELETON.md` section 5's sample is lifted by
script and analysed like a server, as `test_wire_log` does: the template is
what a sixteenth server is copied from (ADR 0008: "Dead code that gets copied is
not dead").

SANDBOX DISCIPLINE -- fixtures under `.claude/tmp/test_cancel/run-<unique>/`,
outside the scan root, removed in a `finally` unless --keep.

The case count IS typed in run.py's SUITES table, for read_loop's reason: a
server appearing without a declared row is the defect.

Offline, AST only, starts nothing, ~1s.

Usage:
  python3 tests/test_cancel.py
  python3 tests/test_cancel.py --brief
  python3 tests/test_cancel.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- the hook, the registry and the cancel-safe dispatch target
  B  DECLARED -- the reclaim class vs what can be measured, one per server
  C  ROSTER   -- the table covers the tree, class totals, a blindness floor
  D  control  -- planted defects the analyser MUST flag, and a correct one
  E  GATE     -- MCP_SKELETON.md section 5's sample, lifted by script
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
import test_read_loop as RL  # noqa: E402  -- the dispatch split, measured there
from test_wire_log import lift_python_blocks  # noqa: E402

NAME = "cancel"

SCAN_ROOT = "Scripts"
SERVER_GLOB_PREFIX = "mcp-"

SKELETON = "Scripts/MCP_SKELETON.md"
SKELETON_SECTION = "## 5. "
SKELETON_SECTION_END = "## 6. "

GA = "A. GATE: notifications/cancelled, every server"
GB = "B. DECLARED: the reclaim class"
GC = "C. ROSTER: the table covers the tree"
GD = "D. negative control"
GE = "E. GATE: the canonical skeleton"
GF = "F. hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_cancel")
WRITES = []

CANCEL_METHOD = "notifications/cancelled"

# ---------------------------------------------------------------------------
# THE DECLARED TABLE -- one reclaim class per server, with the reason.
# ---------------------------------------------------------------------------
TASK = "task"
REPLY_ONLY = "reply-only"
CLASSES = {
    TASK: "the asyncio work stops: handlers are coroutines on the loop",
    REPLY_ONLY: "the reply is suppressed; a thread, subprocess or language "
                "server keeps working until it finishes",
}

FLEET = {
    "mcp-clangd.py":   (REPLY_ONLY, "the clangd request keeps running in clangd "
                                    "(phase 2: $/cancelRequest)"),
    "mcp-context7.py": (REPLY_ONLY, "the urlopen parked on _HTTP_EXECUTOR runs "
                                    "to its timeout"),
    "mcp-cuda.py":     (REPLY_ONLY, "the clangd request keeps running in clangd "
                                    "(phase 2: $/cancelRequest)"),
    "mcp-forge.py":    (REPLY_ONLY, "the build/test subprocess keeps running "
                                    "(phase 2: kill)"),
    "mcp-gdc.py":      (TASK,       "coroutine handlers: the CDP await is "
                                    "cancelled and the lane lock released"),
    "mcp-git.py":      (REPLY_ONLY, "the git subprocess runs to completion in "
                                    "its worker thread"),
    "mcp-inspect.py":  (REPLY_ONLY, "the probe runs to completion in its worker "
                                    "thread"),
    "mcp-jenkins.py":  (REPLY_ONLY, "the HTTP call runs to completion in its "
                                    "worker thread"),
    "mcp-lldb.py":     (TASK,       "coroutine handlers: the whole-handler lock "
                                    "is released on cancel"),
    "mcp-lua-lsp.py":  (REPLY_ONLY, "the request keeps running in "
                                    "lua-language-server (phase 2: "
                                    "$/cancelRequest)"),
    "mcp-postgres.py": (REPLY_ONLY, "the query runs to completion in its worker "
                                    "thread"),
    "mcp-purity.py":   (REPLY_ONLY, "LSP requests keep running in the language "
                                    "server and file ops on the default pool"),
    "mcp-tshark.py":   (REPLY_ONLY, "the tshark subprocess keeps running "
                                    "(phase 2: kill)"),
    "mcp-webfetch.py": (REPLY_ONLY, "the fetch runs to completion in its worker "
                                    "thread (the drain-on-shutdown reason)"),
    "mcp-wiki.py":     (REPLY_ONLY, "the search runs to completion in its worker "
                                    "thread"),
}

DECLARED_TASK = 2
DECLARED_REPLY_ONLY = 13

TASK_FACTORIES = {"create_task", "ensure_future"}
DISPATCH_TARGETS = {"_serve", "_dispatch"}
REPLY_CALLS = {"_write", "_result", "_error", "_tool_error", "write"}

# Problem codes.  The control group asserts these by name.
NO_SERVER_CLASS = "NO-MCPSERVER-CLASS"
NO_RUN = "NO-RUN-METHOD"
NO_DISPATCH = "NO-PER-MESSAGE-DISPATCH"
NO_HOOK = "NO-CANCEL-HOOK-ON-READ-LOOP"
HOOK_AFTER = "HOOK-AFTER-DISPATCH"
HOOK_FALLS = "HOOK-FALLS-THROUGH-TO-DISPATCH"
HOOK_REPLIES = "HOOK-REPLIES"
HOOK_NO_CANCEL = "HOOK-NEVER-CANCELS"
NO_REQUEST_ID = "HOOK-NEVER-READS-REQUESTID"
NO_DICT_GUARD = "PARAMS-NOT-DICT-GUARDED"
ACCEPTS_BOOL = "REQUESTID-ACCEPTS-BOOL"
NO_REGISTRY = "NO-ID-REGISTRY"
NOT_KEYED = "REGISTRY-NOT-KEYED-BY-ID"
NO_FORGET = "FINISHED-ID-NEVER-FORGOTTEN"
INIT_CANCELLABLE = "INITIALIZE-CANCELLABLE"
SWALLOWS = "DISPATCH-SWALLOWS-CANCEL"


# ---------------------------------------------------------------------------
# the analyser
# ---------------------------------------------------------------------------

class Shape:
    def __init__(self, path):
        self.path = path
        self.problems = []
        self.detail = []
        self.hook_line = None
        self.dispatch_line = None
        self.registry = None
        self.helpers = []

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))


def _src(node):
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"


def _find_class(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "McpServer":
            return node
    return None


def _methods(cls):
    return {n.name: n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _expand(nodes, methods, seen=None):
    """`nodes` plus every McpServer method they reach through self.<name>(...),
    transitively.  Returns (all nodes to walk, helper names followed)."""
    seen = set() if seen is None else seen
    out = list(nodes)
    queue = list(nodes)
    while queue:
        node = queue.pop()
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id in ("self", "McpServer", "cls")):
                continue
            name = sub.func.attr
            if name in methods and name not in seen:
                seen.add(name)
                out.append(methods[name])
                queue.append(methods[name])
    return out, sorted(seen)


def _walk_all(nodes):
    for node in nodes:
        for sub in ast.walk(node):
            yield sub


def _has_const(nodes, value):
    return any(isinstance(n, ast.Constant) and n.value == value
               for n in _walk_all(nodes))


def _has_call_attr(nodes, attrs):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in attrs for n in _walk_all(nodes))


def _isinstance_guards(nodes, type_name):
    """True if some isinstance(..., <type_name>) / isinstance(..., (.., T, ..))."""
    for n in _walk_all(nodes):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "isinstance" and len(n.args) == 2):
            continue
        spec = n.args[1]
        names = spec.elts if isinstance(spec, ast.Tuple) else [spec]
        if any(isinstance(x, ast.Name) and x.id == type_name for x in names):
            return True
    return False


def _dispatch_call(run_node):
    for node in ast.walk(run_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in TASK_FACTORIES):
            continue
        if node.args and isinstance(node.args[0], ast.Call) \
                and isinstance(node.args[0].func, ast.Attribute) \
                and node.args[0].func.attr in DISPATCH_TARGETS:
            return node, node.args[0].func.attr
    return None, None


def _dict_vars(run_node):
    """Names run() binds to a fresh dict: `x = {}`, `x: dict = {}`, `x = dict()`."""
    found = []
    for node in ast.walk(run_node):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        is_dict = isinstance(value, ast.Dict) or (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "dict")
        if not is_dict:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                found.append(t.id)
    return found


def _names_loaded(nodes):
    return {n.id for n in _walk_all(nodes)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _statements(body):
    """Every statement in a block, depth-first, keeping nested blocks."""
    for stmt in body:
        yield stmt
        for field in ("body", "orelse", "finalbody"):
            yield from _statements(getattr(stmt, field, []) or [])
        for handler in getattr(stmt, "handlers", []) or []:
            yield from _statements(handler.body)


def _catches_cancel(handler):
    """True if an except clause can catch CancelledError."""
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        name = t.id if isinstance(t, ast.Name) else (
            t.attr if isinstance(t, ast.Attribute) else None)
        if name in ("BaseException", "CancelledError"):
            return True
    return False


def analyse(path, source=None, line_offset=0):
    shape = Shape(path)
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        shape.fail(NO_SERVER_CLASS, "unparseable: %s" % exc)
        return shape
    cls = _find_class(tree)
    if cls is None:
        shape.fail(NO_SERVER_CLASS, "no class McpServer")
        return shape
    methods = _methods(cls)
    run_node = methods.get("run")
    if not isinstance(run_node, ast.AsyncFunctionDef):
        shape.fail(NO_RUN, "no async McpServer.run")
        return shape

    def ln(node):
        return node.lineno + line_offset

    factory, target = _dispatch_call(run_node)
    if factory is None:
        shape.fail(NO_DISPATCH, "no create_task/ensure_future around _serve/_dispatch")
        return shape
    shape.dispatch_line = ln(factory)

    # -- the hook ------------------------------------------------------------
    hook = None
    for node in ast.walk(run_node):
        if isinstance(node, ast.If) and _has_const([node.test], CANCEL_METHOD):
            hook = node
            break
    hook_nodes = []
    if hook is None:
        shape.fail(NO_HOOK, "run() has no `if` on %r -- the notification falls "
                            "through to the id-less branch and is dropped, so "
                            "the work runs on and its reply is written"
                   % CANCEL_METHOD)
    else:
        shape.hook_line = ln(hook)
        if hook.lineno > factory.lineno:
            shape.fail(HOOK_AFTER, "line %d: the hook sits after the dispatch at "
                                   "line %d" % (ln(hook), ln(factory)))
        if not (hook.body and isinstance(hook.body[-1], ast.Continue)):
            shape.fail(HOOK_FALLS, "line %d: the hook does not end in `continue`, "
                                   "so the notification is ALSO dispatched"
                       % ln(hook))
        hook_nodes, followed = _expand(hook.body, methods)
        shape.helpers.extend(followed)
        if not _has_call_attr(hook_nodes, {"cancel"}):
            shape.fail(HOOK_NO_CANCEL, "line %d: nothing reached from the hook "
                                       "calls .cancel()" % ln(hook))
        if _has_call_attr(hook_nodes, REPLY_CALLS):
            shape.fail(HOOK_REPLIES, "line %d: the hook reaches a reply call -- "
                                     "a notification is never answered" % ln(hook))
        if not _has_const(hook_nodes, "requestId"):
            shape.fail(NO_REQUEST_ID, "line %d: the hook never reads "
                                      "params.requestId" % ln(hook))
        if not _isinstance_guards(hook_nodes, "dict"):
            shape.fail(NO_DICT_GUARD, "line %d: params is not guarded with "
                                      "isinstance(..., dict) -- a missing or "
                                      "non-object params reaches .get()"
                       % ln(hook))
        if not _isinstance_guards(hook_nodes, "bool"):
            shape.fail(ACCEPTS_BOOL, "line %d: no isinstance(..., bool) refusal "
                                     "-- True == 1, so requestId true would "
                                     "cancel request 1" % ln(hook))

    # -- the registry ----------------------------------------------------------
    dicts = _dict_vars(run_node)
    hook_names = _names_loaded(hook.body) if hook is not None else set()
    hook_ids = {id(n) for n in _walk_all([hook])} if hook is not None else set()
    after = [s for s in _statements(run_node.body)
             if isinstance(s, (ast.Expr, ast.Assign)) and s.lineno > factory.lineno
             and id(s) not in hook_ids]
    registry = None
    track = []
    for var in dicts:
        if hook is not None and var not in hook_names:
            continue
        uses = [s for s in after if var in _names_loaded([s])
                or any(isinstance(n, ast.Name) and n.id == var
                       for n in _walk_all([s]))]
        if uses:
            registry, track = var, uses
            break
    if registry is None:
        shape.fail(NO_REGISTRY, "run() builds no dict that the hook reads AND "
                                "the dispatch fills -- an id-less set cannot "
                                "find a request by its id")
    else:
        shape.registry = registry
        track_nodes, followed = _expand(track, methods)
        shape.helpers.extend(f for f in followed if f not in shape.helpers)
        keyed = any(isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Subscript) for t in n.targets)
                    for n in _walk_all(track_nodes)) and _has_const(track_nodes, "id")
        if not keyed:
            shape.fail(NOT_KEYED, "%r is never stored into under the message's "
                                  "id" % registry)
        if not _has_call_attr(track_nodes, {"add_done_callback"}):
            shape.fail(NO_FORGET, "no done-callback removes a finished request "
                                  "from %r -- a finished id stays cancellable "
                                  "and the task is never released" % registry)
        if not _has_const(track_nodes, "initialize"):
            shape.fail(INIT_CANCELLABLE, "the registry does not exclude "
                                         "`initialize` -- the handshake must "
                                         "never be cancellable")

    # -- the dispatch target must let the CancelledError through -------------
    serve = methods.get(target)
    if serve is not None:
        for node in ast.walk(serve):
            if isinstance(node, ast.ExceptHandler) and _catches_cancel(node):
                reraises = any(isinstance(n, ast.Raise) and n.exc is None
                               for n in ast.walk(node))
                if not reraises:
                    shape.fail(SWALLOWS, "line %d: %s catches %s without "
                                         "re-raising -- a cancelled request "
                                         "would still be answered"
                               % (ln(node), target,
                                  _src(node.type) if node.type else "everything"))
    return shape


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _server_files():
    root = H.repo_path(SCAN_ROOT)
    return sorted(n for n in os.listdir(root)
                  if n.startswith(SERVER_GLOB_PREFIX) and n.endswith(".py"))


def _shape_detail(label, shape):
    return (["file        : %s" % label,
             "hook        : %s" % ("line %d" % shape.hook_line
                                   if shape.hook_line else "none"),
             "dispatch    : %s" % ("line %d" % shape.dispatch_line
                                   if shape.dispatch_line else "none"),
             "registry    : %s" % (shape.registry or "none"),
             "helpers     : %s" % (", ".join(shape.helpers) or "none")]
            + ["  " + d for d in shape.detail])


def group_gate(suite, shapes):
    """A. the hook, the registry and the cancel-safe dispatch target."""
    for name in sorted(shapes):
        shape = shapes[name]
        suite.record(GA, name, shape.problems,
                     detail=_shape_detail("%s/%s" % (SCAN_ROOT, name), shape),
                     brief="%s | %s | %s" % (H.FAIL if shape.problems else H.PASS,
                                             name, shape.problems or "cancel hook"))


def group_declared(suite, files):
    """B. the declared reclaim class vs what the dispatch split allows."""
    for name in sorted(files):
        if name not in FLEET:
            suite.record(GB, name, ["not declared in FLEET"],
                         detail=["a new server must declare what a cancel "
                                 "reclaims before it can pass"])
            continue
        klass, note = FLEET[name]
        problems = []
        if klass not in CLASSES:
            problems.append("reclaim class %r is not one of %s"
                            % (klass, sorted(CLASSES)))
        dispatch = RL.analyse(H.repo_path(SCAN_ROOT, name)).dispatch
        if klass == TASK and dispatch != "coroutine":
            problems.append("declared %r but test_read_loop measures dispatch "
                            "%r -- a handler in a worker thread is out of reach "
                            "of any CancelledError" % (klass, dispatch))
        suite.record(GB, name, problems,
                     detail=["declared    : %s -- %s"
                             % (klass, CLASSES.get(klass, "?")),
                             "dispatch    : %s (measured by test_read_loop)"
                             % dispatch,
                             "why         : %s" % note],
                     brief="%s | %s | %s" % (H.FAIL if problems else H.PASS,
                                             name, klass))


def group_roster(suite, shapes, files):
    """C. the table covers the tree, class totals, blindness floor."""
    declared, present = set(FLEET), set(files)
    problems = []
    if present - declared:
        problems.append("undeclared server(s): %s"
                        % ", ".join(sorted(present - declared)))
    if declared - present:
        problems.append("declared but absent: %s"
                        % ", ".join(sorted(declared - present)))
    suite.record(GC, "every server file has a declared row", problems,
                 detail=["in tree     : %d" % len(present),
                         "declared    : %d" % len(declared)])

    task = sorted(n for n, r in FLEET.items() if r[0] == TASK)
    reply = sorted(n for n, r in FLEET.items() if r[0] == REPLY_ONLY)
    problems = []
    if len(task) != DECLARED_TASK:
        problems.append("task group is %d, declared %d" % (len(task), DECLARED_TASK))
    if len(reply) != DECLARED_REPLY_ONLY:
        problems.append("reply-only group is %d, declared %d"
                        % (len(reply), DECLARED_REPLY_ONLY))
    suite.record(GC, "the reclaim classes match their declared totals", problems,
                 detail=["task (%d)       : %s" % (len(task), ", ".join(task)),
                         "reply-only (%d): %s" % (len(reply), ", ".join(reply)),
                         "note        : phase 2 moves rows to new classes; a "
                         "silent re-classification must trip something"])

    hooked = [n for n, s in shapes.items() if s.hook_line]
    suite.record(GC, "the analyser resolved a hook in every server",
                 [] if len(hooked) == len(files)
                 else ["resolved %d of %d" % (len(hooked), len(files))],
                 detail=["resolved    : %d/%d" % (len(hooked), len(files)),
                         "note        : a blindness floor"])

    registries = [n for n, s in shapes.items() if s.registry]
    suite.record(GC, "the analyser resolved a registry in every server",
                 [] if len(registries) == len(files)
                 else ["resolved %d of %d" % (len(registries), len(files))],
                 detail=["resolved    : %d/%d" % (len(registries), len(files))])


# -- group D fixtures --------------------------------------------------------

GOOD = '''
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor

log = None


class McpServer:
    async def run(self):
        loop = asyncio.get_running_loop()
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ctl-stdin")
        workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ctl-call")
        inflight = set()
        by_id: dict = {}
        try:
            while True:
                line = await loop.run_in_executor(reader, sys.stdin.readline)
                if not line:
                    break
                msg = json.loads(line)
                if msg.get("method") == "notifications/cancelled" and msg.get("id") is None:
                    self._cancel_request(msg.get("params"), by_id)
                    continue
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                self._track_request(msg, task, by_id)
        finally:
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)

    @staticmethod
    def _request_key(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, (str, int)):
            return value
        return None

    def _track_request(self, msg, task, by_id):
        if msg.get("method") == "initialize":
            return
        key = self._request_key(msg.get("id"))
        if key is None:
            return
        by_id[key] = task

        def _forget(done):
            if by_id.get(key) is done:
                del by_id[key]

        task.add_done_callback(_forget)

    def _cancel_request(self, params, by_id):
        if not isinstance(params, dict):
            return
        key = self._request_key(params.get("requestId"))
        if key is None:
            return
        task = by_id.get(key)
        if task is not None:
            task.cancel()

    async def _serve(self, loop, workers, msg):
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception:
            response = None
        if response is not None:
            self._write(response)

    def _handle_message(self, msg):
        return None

    def _write(self, response):
        sys.stdout.write(json.dumps(response) + "\\n")
'''

_HOOK = ('                if msg.get("method") == "notifications/cancelled" and msg.get("id") is None:\n'
         '                    self._cancel_request(msg.get("params"), by_id)\n'
         '                    continue\n')
_TRACK = "                self._track_request(msg, task, by_id)\n"

FIXTURES = {
    # name -> (source, expected problem codes)
    "ctl_good.py": (GOOD, []),
    "ctl_no_hook.py": (GOOD.replace(_HOOK, ""), [NO_HOOK]),
    "ctl_hook_in_handler.py": (
        GOOD.replace(_HOOK, "").replace(
            "    def _handle_message(self, msg):\n        return None\n",
            "    def _handle_message(self, msg):\n"
            "        if msg.get(\"method\") == \"notifications/cancelled\":\n"
            "            return None\n"
            "        return None\n"),
        [NO_HOOK]),
    "ctl_hook_after_dispatch.py": (
        GOOD.replace(_HOOK, "").replace(_TRACK, _TRACK + _HOOK),
        [HOOK_AFTER]),
    "ctl_hook_falls_through.py": (
        GOOD.replace(_HOOK, _HOOK.replace("                    continue\n", "")),
        [HOOK_FALLS]),
    "ctl_hook_replies.py": (
        GOOD.replace("            task.cancel()\n",
                     "            task.cancel()\n"
                     "        self._write({\"jsonrpc\": \"2.0\", \"result\": {}})\n"),
        [HOOK_REPLIES]),
    "ctl_hook_never_cancels.py": (
        GOOD.replace("            task.cancel()\n", "            by_id.pop(key)\n"),
        [HOOK_NO_CANCEL]),
    "ctl_accepts_bool.py": (
        GOOD.replace("        if isinstance(value, bool):\n            return None\n", ""),
        [ACCEPTS_BOOL]),
    "ctl_no_dict_guard.py": (
        GOOD.replace("        if not isinstance(params, dict):\n            return\n", ""),
        [NO_DICT_GUARD]),
    "ctl_no_registry.py": (
        GOOD.replace("        by_id: dict = {}\n", "        by_id: set = set()\n"),
        [NO_REGISTRY]),
    "ctl_no_forget.py": (
        GOOD.replace("        task.add_done_callback(_forget)\n", ""),
        [NO_FORGET]),
    "ctl_initialize_cancellable.py": (
        GOOD.replace('        if msg.get("method") == "initialize":\n            return\n', ""),
        [INIT_CANCELLABLE]),
    "ctl_serve_swallows.py": (
        GOOD.replace("        except Exception:\n            response = None\n",
                     "        except BaseException:\n            response = None\n"),
        [SWALLOWS]),
    "ctl_serve_reraises.py": (
        GOOD.replace("        except Exception:\n            response = None\n",
                     "        except asyncio.CancelledError:\n            raise\n"
                     "        except Exception:\n            response = None\n"),
        []),
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
    for name, (source, expected) in sorted(FIXTURES.items()):
        problems = []
        if expected and source == GOOD:
            problems.append("the mutation did not apply -- the fixture is GOOD "
                            "under another name and proves nothing")
        shape = analyse(os.path.join(fixture_root, name))
        got = sorted(shape.problems)
        if got != sorted(expected):
            problems.append("codes %r != expected %r" % (got, sorted(expected)))
        elif expected:
            flagged += 1
        suite.record(GD, "control-" + name, problems,
                     detail=["expected    : %r" % sorted(expected),
                             "got         : %r" % got]
                            + ["  " + d for d in shape.detail],
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))
    must_flag = sum(1 for _n, (_s, e) in FIXTURES.items() if e)
    suite.record(GD, "control fires at all",
                 [] if flagged == must_flag
                 else ["only %d of %d defective fixtures flagged exactly"
                       % (flagged, must_flag)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(FIXTURES), must_flag, len(FIXTURES) - must_flag)])


# -- group E: the skeleton ---------------------------------------------------

SKELETON_HOST = (
    "import asyncio\n"
    "import json\n"
    "import sys\n"
    "from concurrent.futures import ThreadPoolExecutor\n"
    "\n"
    "MAX_INFLIGHT_REQUESTS = 8\n"
    "log = None\n"
    "\n"
    "\n"
    "class McpServer:\n"
)


def group_skeleton(suite):
    """E. the template's own sample, through the same analyser as the fleet."""
    path = H.repo_path(SKELETON)
    blocks, why = ([], "%s is absent" % SKELETON)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            blocks, why = lift_python_blocks(fh.read(), SKELETON_SECTION,
                                             SKELETON_SECTION_END)
    problems = []
    shape = None
    if why:
        problems.append(why)
    elif len(blocks) != 1:
        problems.append("expected exactly 1 python sample in %r, found %d"
                        % (SKELETON_SECTION, len(blocks)))
    else:
        first, body = blocks[0]
        offset = first - SKELETON_HOST.count("\n") - 1
        shape = analyse(SKELETON, source=SKELETON_HOST + body + "\n",
                        line_offset=offset)
        if NO_SERVER_CLASS in shape.problems:
            problems.append("the lifted sample does not parse in a minimal host")
    suite.record(GE, "the section 5 sample lifts, splices and parses", problems,
                 detail=["file        : %s" % SKELETON,
                         "blocks      : %d" % len(blocks)])
    if shape is None:
        suite.record(GE, "skeleton carries the cancel hook",
                     ["no sample to measure (see the lift case above)"])
        return
    suite.record(GE, "skeleton carries the cancel hook", shape.problems,
                 detail=_shape_detail(SKELETON, shape)
                        + ["why         : every new server is written from "
                           "this file; fixing fifteen while the template "
                           "teaches the old loop fixes nothing for sixteen"])


def group_hygiene(suite, pyc_before, tree_before):
    """F. sandbox discipline, no bytecode, no new repo paths."""
    stray = [p for p in WRITES
             if not os.path.abspath(p).startswith(
                 os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GF, "every write lands under .claude/tmp",
                 [] if not stray else ["%d write(s) outside the sandbox: %s"
                                       % (len(stray), ", ".join(stray))],
                 detail=["writes      : %d" % len(WRITES)])
    inside = os.path.abspath(FIXTURE_BASE).startswith(
        os.path.abspath(H.repo_path(SCAN_ROOT)) + os.sep)
    suite.record(GF, "fixture root is outside the scan root",
                 [] if not inside else ["the sandbox lives inside %s" % SCAN_ROOT])
    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GF, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)])
    added = sorted(H.repo_tree() - tree_before)
    suite.record(GF, "no new repo paths",
                 [] if not added else ["%d new path(s): %s" % (len(added), added[:5])])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="every MCP server honours notifications/cancelled on "
                          "its read loop: an id registry, a hook before "
                          "dispatch, initialize never cancellable, and the "
                          "reclaim class declared per server",
                    opts=opts, mode="grouped", cid_width=32)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    files = _server_files()
    shapes = {n: analyse(H.repo_path(SCAN_ROOT, n)) for n in files}

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_gate(suite, shapes)
        group_declared(suite, files)
        group_roster(suite, shapes, files)
        group_control(suite, fixture_root)
        group_skeleton(suite)
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
