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
  kill        the request's CHILD PROCESS is killed (forge, tshark): every
              request-scoped Popen is adopted by a per-request reclaim record,
              and the dispatch target's CancelledError arm signals the child's
              process group from the loop thread while the worker thread is
              still blocked in communicate(); the thread then returns and its
              result is discarded.
  lsp-cancel  a semantic call is reclaimed IN the language server: the
              generated `_request` answers a CancelledError by dropping its
              `_pending` waiter and sending `$/cancelRequest` for the LSP id
              (clangd, cuda, lua-lsp, purity).  File operations and anything
              else parked in a worker thread remain reply-only.
  reply-only  the reply is suppressed but the work runs on: a worker thread,
              a subprocess, or a remote peer keeps going until it finishes.

Each class other than reply-only is MEASURED here (group B) before a row may
claim it:
  * a `task` row must be a `coroutine` server in `test_read_loop`'s own
    analysis, because a handler parked in a worker thread is out of reach of
    any CancelledError;
  * a `kill` row must adopt every Popen outside a declared exemption (KILL_
    EXEMPT, with its reason), start each adopted child in its own session so a
    process-group signal reaches its descendants, spawn nothing through a
    blocking subprocess.run/call/check_* whose handle cannot be recorded, and
    carry a re-raising CancelledError arm in the dispatch target that reaches
    os.killpg;
  * an `lsp-cancel` row must carry a `_request` whose CancelledError arm pops
    the `_pending` entry, names `$/cancelRequest`, and re-raises.
The behaviour behind the last two is gated elsewhere: forge's kill live in the
smoke harness (`inflight_kill_probe`), `_request`'s cancel arm in
`test_generated_region` group E against the canonical source.

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
  D  control  -- planted defects the analysers MUST flag, and correct ones
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
KILL = "kill"
LSP_CANCEL = "lsp-cancel"
REPLY_ONLY = "reply-only"
CLASSES = {
    TASK: "the asyncio work stops: handlers are coroutines on the loop",
    KILL: "the request's child process group is signalled: every "
          "request-scoped Popen is adopted, and the dispatch target's "
          "CancelledError arm kills it",
    LSP_CANCEL: "a semantic call is reclaimed in the language server via "
                "$/cancelRequest and its pending waiter dropped; file "
                "operations stay reply-only",
    REPLY_ONLY: "the reply is suppressed; a thread, subprocess or remote peer "
                "keeps working until it finishes",
}

FLEET = {
    "mcp-clangd.py":   (LSP_CANCEL, "the generated _request sends "
                                    "$/cancelRequest to clangd and drops its "
                                    "waiter"),
    "mcp-context7.py": (REPLY_ONLY, "the urlopen parked on _HTTP_EXECUTOR runs "
                                    "to its timeout"),
    "mcp-cuda.py":     (LSP_CANCEL, "the generated _request sends "
                                    "$/cancelRequest to clangd and drops its "
                                    "waiter"),
    "mcp-forge.py":    (KILL,       "run_command's build/test child is adopted "
                                    "and its process group killed on cancel"),
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
    "mcp-lua-lsp.py":  (LSP_CANCEL, "the generated _request sends "
                                    "$/cancelRequest to lua-language-server "
                                    "and drops its waiter"),
    "mcp-postgres.py": (REPLY_ONLY, "the query runs to completion in its worker "
                                    "thread"),
    "mcp-purity.py":   (LSP_CANCEL, "semantic calls: $/cancelRequest to "
                                    "clangd/luals; file ops on the default "
                                    "pool stay reply-only"),
    "mcp-tshark.py":   (KILL,       "each request's tshark run is adopted and "
                                    "its process group killed on cancel; the "
                                    "capture child is a session (KILL_EXEMPT)"),
    "mcp-webfetch.py": (REPLY_ONLY, "the fetch runs to completion in its worker "
                                    "thread (the drain-on-shutdown reason)"),
    "mcp-wiki.py":     (REPLY_ONLY, "the search runs to completion in its worker "
                                    "thread"),
}

DECLARED_TASK = 2
DECLARED_KILL = 2
DECLARED_LSP_CANCEL = 4
DECLARED_REPLY_ONLY = 7

# A `kill` server's Popen sites that are NOT request-scoped, by (file, enclosing
# function), each with the reason it may outlive the request that spawned it.
KILL_EXEMPT = {
    ("mcp-tshark.py", "handle_start_capture"):
        "the capture child IS the session: it outlives start_capture by design "
        "and is owned through SESSIONS by stop_capture and shutdown",
}

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

# -- the `kill` class --
RECORD_CALLS = {"adopt"}
BLOCKING_SPAWNS = {"run", "call", "check_call", "check_output"}
SIGNAL_CALLS = {"killpg"}
KILL_NO_SPAWN = "KILL-NO-ADOPTED-POPEN"
KILL_UNADOPTED = "KILL-POPEN-NOT-ADOPTED"
KILL_NO_SESSION = "KILL-CHILD-NOT-OWN-GROUP"
KILL_BLOCKING = "KILL-BLOCKING-SPAWN"
KILL_NO_ARM = "KILL-NO-CANCEL-ARM"
KILL_NO_SIGNAL = "KILL-ARM-NEVER-SIGNALS"

# -- the `lsp-cancel` class --
LSP_CANCEL_METHOD = "$/cancelRequest"
LSP_NO_REQUEST = "LSP-NO-REQUEST-HOP"
LSP_NO_ARM = "LSP-NO-CANCEL-ARM"
LSP_NO_NOTICE = "LSP-ARM-SENDS-NO-CANCELREQUEST"
LSP_LEAKS = "LSP-ARM-LEAVES-PENDING"
LSP_ARM_SWALLOWS = "LSP-ARM-SWALLOWS-CANCEL"


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
# the reclaim-class analysers (group B measures a row's class with these)
# ---------------------------------------------------------------------------

class Reclaim:
    """What one reclaim-class analyser found in one file."""

    def __init__(self, path):
        self.path = path
        self.problems = []
        self.detail = []

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))


def _parse(path, source, result):
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        result.fail(NO_SERVER_CLASS, "unparseable: %s" % exc)
        return None


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _func_index(tree):
    """Every def in the module by name, methods included."""
    index = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index.setdefault(node.name, []).append(node)
    return index


def _reach(nodes, index):
    """`nodes` plus every def they call BY NAME -- a bare name or the last
    attribute of a dotted call, so `reclaim.cancel(loop)` follows every def
    named `cancel` -- transitively.  Deliberately loose: this is a presence
    gate, and over-following can only find a signal that is there."""
    seen = set()
    out = list(nodes)
    queue = list(nodes)
    while queue:
        node = queue.pop()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = _call_name(sub)
            if name in index and name not in seen:
                seen.add(name)
                for d in index[name]:
                    out.append(d)
                    queue.append(d)
    return out


def _own_nodes(func):
    """Every node inside `func` that no nested def owns."""
    stack = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue
            stack.append(child)


def _is_subprocess(call, names):
    func = call.func
    return (isinstance(func, ast.Attribute) and func.attr in names
            and isinstance(func.value, ast.Name) and func.value.id == "subprocess")


def _own_session(call):
    for kw in call.keywords:
        if kw.arg == "start_new_session" and isinstance(kw.value, ast.Constant) \
                and kw.value.value is True:
            return True
        if kw.arg == "preexec_fn" and isinstance(kw.value, ast.Attribute) \
                and kw.value.attr == "setsid":
            return True
    return False


def _reraises(handler):
    return any(isinstance(n, ast.Raise) and n.exc is None
               for n in ast.walk(handler))


def _names_cancelled(handler):
    """True if the except clause NAMES CancelledError (bare/BaseException do
    not count as a deliberate cancel arm)."""
    if handler.type is None:
        return False
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        name = t.id if isinstance(t, ast.Name) else (
            t.attr if isinstance(t, ast.Attribute) else None)
        if name == "CancelledError":
            return True
    return False


def analyse_kill(path, source=None, exempt=()):
    """The `kill` class: adopted Popen handles and a cancel arm that signals."""
    result = Reclaim(path)
    tree = _parse(path, source, result)
    if tree is None:
        return result
    index = _func_index(tree)

    adopted = 0
    for funcs in index.values():
        for func in funcs:
            own = list(_own_nodes(func))
            for node in own:
                if isinstance(node, ast.Call) and _is_subprocess(node, BLOCKING_SPAWNS):
                    result.fail(KILL_BLOCKING,
                                "line %d: %s() calls subprocess.%s -- a blocking "
                                "spawn hands back no handle a cancel could "
                                "signal" % (node.lineno, func.name, node.func.attr))
            popens = []
            for node in own:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                        and _is_subprocess(node.value, {"Popen"}):
                    target = node.targets[0]
                    popens.append((node.value, target.id
                                   if isinstance(target, ast.Name) else None))
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        expr = item.context_expr
                        if isinstance(expr, ast.Call) and _is_subprocess(expr, {"Popen"}):
                            var = item.optional_vars
                            popens.append((expr, var.id
                                           if isinstance(var, ast.Name) else None))
            if not popens:
                continue
            if func.name in exempt:
                result.detail.append("exempt: %s() -- %s"
                                     % (func.name, exempt[func.name]))
                continue
            for call, var in popens:
                recorded = var is not None and any(
                    isinstance(n, ast.Call) and _call_name(n) in RECORD_CALLS
                    and any(isinstance(a, ast.Name) and a.id == var for a in n.args)
                    for n in own)
                if not recorded:
                    result.fail(KILL_UNADOPTED,
                                "line %d: %s() spawns a Popen it never passes "
                                "to %s -- a cancel cannot find it"
                                % (call.lineno, func.name, sorted(RECORD_CALLS)))
                    continue
                adopted += 1
                if not _own_session(call):
                    result.fail(KILL_NO_SESSION,
                                "line %d: the adopted child is not started with "
                                "start_new_session=True / preexec_fn=os.setsid, "
                                "so a process-group signal cannot reach its "
                                "descendants" % call.lineno)
    if adopted == 0 and KILL_UNADOPTED not in result.problems:
        result.fail(KILL_NO_SPAWN, "no adopted subprocess.Popen anywhere -- "
                                   "nothing for a cancel to kill")
    result.detail.insert(0, "adopted     : %d Popen site(s)" % adopted)

    cls = _find_class(tree)
    serve = None
    if cls is not None:
        methods = _methods(cls)
        serve = next((methods[t] for t in sorted(DISPATCH_TARGETS)
                      if t in methods), None)
    arms = [] if serve is None else [
        n for n in ast.walk(serve)
        if isinstance(n, ast.ExceptHandler) and _names_cancelled(n) and _reraises(n)]
    if not arms:
        result.fail(KILL_NO_ARM, "the dispatch target has no re-raising "
                                 "`except asyncio.CancelledError` arm to kill "
                                 "from while the worker thread is blocked")
    else:
        reached = _reach([a for arm in arms for a in arm.body], index)
        if not any(isinstance(n, ast.Call) and _call_name(n) in SIGNAL_CALLS
                   for n in _walk_all(reached)):
            result.fail(KILL_NO_SIGNAL, "line %d: the cancel arm reaches no "
                                        "os.killpg" % arms[0].lineno)
    return result


def analyse_lsp(path, source=None):
    """The `lsp-cancel` class: `_request`'s CancelledError arm."""
    result = Reclaim(path)
    tree = _parse(path, source, result)
    if tree is None:
        return result
    hops = _func_index(tree).get("_request", [])
    if not hops:
        result.fail(LSP_NO_REQUEST, "no `_request` -- nothing speaks to a "
                                    "language server here")
        return result
    for hop in hops:
        arms = [n for n in ast.walk(hop)
                if isinstance(n, ast.ExceptHandler) and _names_cancelled(n)]
        if not arms:
            result.fail(LSP_NO_ARM, "line %d: _request has no `except "
                                    "asyncio.CancelledError` arm -- the "
                                    "pending waiter leaks and the server keeps "
                                    "computing" % hop.lineno)
            continue
        for arm in arms:
            if not _has_const([arm], LSP_CANCEL_METHOD):
                result.fail(LSP_NO_NOTICE, "line %d: the cancel arm never "
                                           "names %s" % (arm.lineno,
                                                         LSP_CANCEL_METHOD))
            pops = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "pop"
                       and isinstance(n.func.value, ast.Attribute)
                       and n.func.value.attr == "_pending"
                       for n in ast.walk(arm))
            if not pops:
                result.fail(LSP_LEAKS, "line %d: the cancel arm leaves the "
                                       "waiter in _pending" % arm.lineno)
            if not _reraises(arm):
                result.fail(LSP_ARM_SWALLOWS, "line %d: the cancel arm does not "
                                              "re-raise -- the cancelled call "
                                              "would carry on" % arm.lineno)
    result.detail.insert(0, "_request    : %d hop(s)" % len(hops))
    return result


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
        path = H.repo_path(SCAN_ROOT, name)
        dispatch = RL.analyse(path).dispatch
        measured = []
        if klass == TASK and dispatch != "coroutine":
            problems.append("declared %r but test_read_loop measures dispatch "
                            "%r -- a handler in a worker thread is out of reach "
                            "of any CancelledError" % (klass, dispatch))
        if klass == KILL:
            exempt = {fn: why for (f, fn), why in KILL_EXEMPT.items() if f == name}
            got = analyse_kill(path, exempt=exempt)
            problems.extend("declared %r: %s" % (klass, p) for p in got.problems)
            measured = ["  " + d for d in got.detail]
        if klass == LSP_CANCEL:
            got = analyse_lsp(path)
            problems.extend("declared %r: %s" % (klass, p) for p in got.problems)
            measured = ["  " + d for d in got.detail]
        suite.record(GB, name, problems,
                     detail=["declared    : %s -- %s"
                             % (klass, CLASSES.get(klass, "?")),
                             "dispatch    : %s (measured by test_read_loop)"
                             % dispatch,
                             "why         : %s" % note] + measured,
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

    declared_totals = ((TASK, DECLARED_TASK), (KILL, DECLARED_KILL),
                       (LSP_CANCEL, DECLARED_LSP_CANCEL),
                       (REPLY_ONLY, DECLARED_REPLY_ONLY))
    problems = []
    detail = []
    for klass, want in declared_totals:
        rows = sorted(n for n, r in FLEET.items() if r[0] == klass)
        if len(rows) != want:
            problems.append("%s group is %d, declared %d" % (klass, len(rows), want))
        detail.append("%-11s (%d): %s" % (klass, len(rows), ", ".join(rows)))
    if set(CLASSES) != {k for k, _w in declared_totals}:
        problems.append("CLASSES and the declared totals name different classes")
    suite.record(GC, "the reclaim classes match their declared totals", problems,
                 detail=detail + ["note        : a silent re-classification "
                                  "must trip something"])

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


GOOD_KILL = '''
import asyncio
import os
import signal
import subprocess


class _Reclaim:
    def __init__(self):
        self.procs = []

    def adopt(self, proc):
        self.procs.append(proc)

    def cancel(self):
        for proc in self.procs:
            os.killpg(proc.pid, signal.SIGTERM)


def run_command(reclaim, argv):
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, start_new_session=True)
    reclaim.adopt(proc)
    return proc.communicate()


class McpServer:
    async def _serve(self, loop, workers, msg):
        reclaim = _Reclaim()
        try:
            response = await loop.run_in_executor(workers, run_command, reclaim, msg)
        except asyncio.CancelledError:
            reclaim.cancel()
            raise
        except Exception:
            response = None
        return response
'''

_KILL_ARM = ("        except asyncio.CancelledError:\n"
             "            reclaim.cancel()\n"
             "            raise\n")

GOOD_LSP = '''
import asyncio


class Client:
    async def _request(self, method, params, timeout=10.0):
        req_id = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                              "params": params})
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"error": {"message": "timeout"}}
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            try:
                await self._send({"jsonrpc": "2.0", "method": "$/cancelRequest",
                                  "params": {"id": req_id}})
            except Exception:
                pass
            raise
'''

_LSP_ARM_HEAD = ("        except asyncio.CancelledError:\n"
                 "            self._pending.pop(req_id, None)\n")

RECLAIM_FIXTURES = {
    # name -> (class, source, expected problem codes)
    "rcl_kill_good.py": (KILL, GOOD_KILL, []),
    "rcl_kill_not_adopted.py": (
        KILL, GOOD_KILL.replace("    reclaim.adopt(proc)\n", ""), [KILL_UNADOPTED]),
    "rcl_kill_no_session.py": (
        KILL, GOOD_KILL.replace(", start_new_session=True)", ")"), [KILL_NO_SESSION]),
    "rcl_kill_blocking_spawn.py": (
        KILL, GOOD_KILL.replace("    return proc.communicate()\n",
                                "    subprocess.run(argv, stdin=subprocess.DEVNULL)\n"
                                "    return proc.communicate()\n"),
        [KILL_BLOCKING]),
    "rcl_kill_no_arm.py": (KILL, GOOD_KILL.replace(_KILL_ARM, ""), [KILL_NO_ARM]),
    "rcl_kill_arm_never_signals.py": (
        KILL, GOOD_KILL.replace("            reclaim.cancel()\n",
                                "            reclaim.procs.clear()\n"),
        [KILL_NO_SIGNAL]),
    "rcl_lsp_good.py": (LSP_CANCEL, GOOD_LSP, []),
    "rcl_lsp_no_arm.py": (
        LSP_CANCEL, GOOD_LSP[:GOOD_LSP.index(_LSP_ARM_HEAD)], [LSP_NO_ARM]),
    "rcl_lsp_no_notice.py": (
        LSP_CANCEL, GOOD_LSP.replace("$/cancelRequest", "$/progress"), [LSP_NO_NOTICE]),
    "rcl_lsp_leaks.py": (
        LSP_CANCEL, GOOD_LSP.replace(_LSP_ARM_HEAD,
                                     "        except asyncio.CancelledError:\n"),
        [LSP_LEAKS]),
    "rcl_lsp_swallows.py": (
        LSP_CANCEL, GOOD_LSP.replace("            raise\n", "            return None\n"),
        [LSP_ARM_SWALLOWS]),
}


def write_fixtures(root):
    sources = [(n, s) for n, (s, _e) in FIXTURES.items()]
    sources += [(n, s) for n, (_k, s, _e) in RECLAIM_FIXTURES.items()]
    for name, source in sorted(sources):
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

    # The reclaim-class analysers, planted the same way: group B's `kill` and
    # `lsp-cancel` verdicts are only worth what these prove each code can do.
    good = {KILL: GOOD_KILL, LSP_CANCEL: GOOD_LSP}
    flagged = 0
    for name, (klass, source, expected) in sorted(RECLAIM_FIXTURES.items()):
        problems = []
        if expected and source == good[klass]:
            problems.append("the mutation did not apply -- the fixture is GOOD "
                            "under another name and proves nothing")
        path = os.path.join(fixture_root, name)
        got_shape = analyse_kill(path) if klass == KILL else analyse_lsp(path)
        got = sorted(got_shape.problems)
        if got != sorted(expected):
            problems.append("codes %r != expected %r" % (got, sorted(expected)))
        elif expected:
            flagged += 1
        suite.record(GD, "control-" + name, problems,
                     detail=["class       : %s" % klass,
                             "expected    : %r" % sorted(expected),
                             "got         : %r" % got]
                            + ["  " + d for d in got_shape.detail],
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))
    must_flag = sum(1 for _n, (_k, _s, e) in RECLAIM_FIXTURES.items() if e)
    suite.record(GD, "reclaim control fires at all",
                 [] if flagged == must_flag
                 else ["only %d of %d defective fixtures flagged exactly"
                       % (flagged, must_flag)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(RECLAIM_FIXTURES), must_flag,
                            len(RECLAIM_FIXTURES) - must_flag)])


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
