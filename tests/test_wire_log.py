#!/usr/bin/env python3
"""Every MCP server's wire logging is STRUCTURE ONLY, never a payload (A-F).

WHY THIS IS A SUITE AND NOT AN AUDIT
------------------------------------
Audit finding F12 / CWE-532 (insertion of sensitive information into a log
file).  Both ends of an MCP server's JSON-RPC pipe pass through two functions --
`McpServer.run` reads a line and `McpServer._write` emits one -- and both used
to log the MESSAGE.  What travels on that wire is not metadata:
`purity_call(replace_content)` carries the new file body in
`params.arguments`, `read_file` carries the old one back in `result`,
`mcp-git`'s reply carries a diff, `mcp-postgres`' carries rows,
`mcp-jenkins`' request carries a token, `mcp-webfetch`'s reply carries whatever
the page served.  With `--debug` on, all of that lands in a log file with
whatever mode and lifetime the operator's logging config happens to give it.

`mcp-purity.py` is the only server where both ends were fixed, and its two
sites are the canonical form this suite gates against:

  OUTBOUND -- `McpServer._write`, `Scripts/mcp-purity.py:6059-6063`
      # F12/CWE-532: structure only (id + outcome), no body.
      log.debug(
          "→ id=%s %s", response.get("id"),
          "error" if "error" in response else "ok",
      )

  INBOUND -- `McpServer.run`, `Scripts/mcp-purity.py:6009-6021`
      log.debug(
          "← method=%s id=%s fn=%s keys=%s",
          msg.get("method"), msg.get("id"), _p.get("name"),
          list(_args.keys()),
      )

Note the inbound form logs argument KEYS and never argument values.  That is
the whole distinction this suite exists to hold: `keys=['path','content']` is a
debugging aid, `content='<the file>'` is the finding.

TRUNCATION IS NOT A MITIGATION
------------------------------
Nine servers log `out[:200]` / `json.dumps(msg)[:200]`.  A 200-character prefix
of a JSON-RPC frame is not a redaction -- it is the FRONT of the message, which
is exactly where the headers, the auth material and the first fields of the
payload are.  `{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":
"git_call","arguments":{"token":"...` fits well inside 200 characters.  So this
gate refuses any payload-derived expression, sliced or not, and a fix that
lowers the slice bound does not pass it.

THE RULE, PRECISELY
-------------------
An expression in a wire log is PAYLOAD-DERIVED, and therefore refused, when it
reaches a VALUE that came off the wire: the readline result and every name
assigned from it inside `run` (`line`, then `msg`), `_write`'s response
parameter and every name assigned from it inside `_write` (`out`), any
subscript or slice of one of those, any f-string or call that interpolates or
consumes one (`f"← RAW: {line}"`, `json.dumps(msg)`, `out[:200]`,
`str(response)`), and any `.get(k)` / `[k]` whose key is not one of the four
STRUCTURAL keys; allowed, and ONLY these, are the four structural keys
(`jsonrpc`, `method`, `id`, `name`), `.keys()` taken over a payload container
(`params`, `arguments`), a predicate or literal-branch conditional over the
payload (`"error" if "error" in response else "ok"`), `len()`, and whatever
`type()` / `isinstance()` returns.

Two consequences of that wording are deliberate and worth stating:

  * `msg.get("params")` yields a CONTAINER: drilling it to `.get("name")` or to
    `.keys()` is allowed, logging it whole is not.  A dict of values is a body.
  * the rule is on the VALUE REACHED, not on the spelling.  `msg["method"]` and
    `msg.get("method")` are the same fact and both pass (group D proves it),
    because gating a spelling fails a correct refactor -- the sibling gate in
    `test_read_loop.py` learned the same lesson over `create_task` vs
    `ensure_future`.

One DECLARED limitation.  An `except ... as exc` name is not a payload root,
so `log.warning("Invalid JSON: %s", exc)` passes.  That is a judgement, not an
oversight: `json.JSONDecodeError.__str__` renders its message plus
line/column/pos and never its `.doc`, so the offending line does not reach the
log through it -- and the canonical server itself logs exactly that.  If a
server ever logs `exc.doc`, this analyser will not see it.

EVERY LEVEL, AND EVERY SPELLING
-------------------------------
The analyser reads `debug`, `info`, `warning`, `error`, `exception` and
`critical` alike, on ANY receiver rather than only on the module-level `log`.
Both widenings are about evasions rather than about suspicion: CWE-532 is about
what lands in the file, so a payload in a warning lands just as hard, and a
gate that recognised only `log.debug` could be walked around with
`logging.getLogger(__name__).debug(out)` or a rename.  Measured: on today's
fleet both widenings cost nothing -- all thirty wire sites spell it `log.`, and
every non-`debug` call in them takes a literal, an exception name, or
`type(msg).__name__`.  An unfamiliar receiver is named in the case detail so a
reader can see what was judged.

SCOPE: THE TWO TRANSPORT FUNCTIONS, AND NOTHING ELSE
----------------------------------------------------
Only `McpServer.run` and `McpServer._write` are read.  That is the WIRE, which
is this suite's subject and the one place every message of every server passes
through.  A handler that logs its own arguments is a different question with a
different answer per handler, and folding it in here would turn one crisp
invariant into a survey.  Declared so the boundary is visible: six servers log
`method` + `id` from inside `_handle_message`, which this gate does not read
and does not need to -- those two fields are structure by the rule above
anyway.

AST, NOT REGEX
--------------
A slice is a `Subscript` node, an f-string hides its names inside
`FormattedValue`, and `json.dumps(msg)[:200]` is a subscript of a call whose
argument is three tokens away from the logger.  None of the three survives a
line-oriented regex, and two of the four defect shapes in the live fleet are
exactly those.  The analyser instead seeds a taint environment from the payload
ROOT of each site, propagates it across assignments to a fixpoint, and then
classifies every argument of every logging call in the site as PURE, FIELD (a
named structural projection), CONTAINER, or LEAK.

DECLARED DATA, NOT INFERRED
---------------------------
`WIRE` below is a hand-written table, one row per server, declaring the shape
each site is expected to log -- as the tuple of structural FIELDS, in order, or
`SILENT` for a site that is declared to log nothing at all.  The suite measures
each server and compares.  Inferring the expected shape from the fleet would
produce a suite that only proves the fleet agrees with itself, and it would
pass, green, on the thirteen servers that are broken today.

Three declared inbound forms, and the divergence is real rather than tidy:

  FULL     `("method", "id", "name", "keys")` -- the canonical form.  Every
           server here is a single-dispatcher server (`*_call`), so `params`
           always carries `name` + `arguments`, and the fn/keys pair is the
           half of the log that makes a debug session useful.
  MINIMAL  `("method", "id")` -- `mcp-lua-lsp.py`, which is already structure
           only.  Declared at the shape it has: this gate's subject is the
           payload, and the table's second job is to stop a shape moving
           unannounced, not to homogenise two safe shapes.
  SILENT   `mcp-gdc.py` and `mcp-lldb.py` log NOTHING on the read loop -- they
           go straight from the isinstance guard to `create_task`, and log
           method+id later, inside `_handle_message`, which is not a wire site.
           Silence satisfies the invariant, but it has to be declared: a server
           that quietly grows a wire log must be classified, not accepted.

Outbound is uniform: all fifteen rows declare `("id", "outcome")`.  There is no
second defensible outbound shape -- the reply's id and whether it is an error
is the entire structure of a JSON-RPC response, and everything else in it is
body.

NEGATIVE CONTROL (group D) -- mandatory
---------------------------------------
A checker that silently matches nothing is indistinguishable from a clean
fleet.  Group D points the SAME analyser at synthetic servers carrying one
planted defect each -- a bare `out`, a sliced `out[:200]`, the bare `response`
parameter, a non-structural key on it (`response.get("result")`), an f-string
interpolating `line`, `json.dumps(msg)[:200]`, a value reached inside
`arguments`, and the `arguments` container logged whole -- plus three that must
stay silent: the canonical form, the same fields spelled as subscripts, and a
server with no wire log at all.  Each fixture asserts its problem codes AND its
measured shapes by name, so the shape extractor is under control too, and a
plant that failed to apply (a `.replace()` that matched nothing) is itself a
recorded failure rather than a silent pass.

GATE (group E) -- `Scripts/MCP_SKELETON.md`
-------------------------------------------
Gated, not surveyed, and that is a departure from `test_read_loop.py`'s
INFO-only treatment of the same file.  The justification is specific: this
template is the PROVEN propagation vector for THIS defect.  Its §5a sample
teaches `log.debug("← %s", json.dumps(msg)[:200])` and
`log.debug("→ %s", out[:200])` -- both defect shapes, in the one file every new
server in this fleet is written from.  Fixing fifteen servers while the
template still teaches the defect fixes nothing for server sixteen.

The check is mechanical rather than by inspection, following `a7b165e`: the
python sample is lifted out of the markdown BY SCRIPT, spliced into a minimal
host, and handed to the SAME analyser the live fleet gets.  Hand-checking prose
is how the template stayed wrong through two edits after the read-loop rollout.

SANDBOX DISCIPLINE -- all fixtures under
`.claude/tmp/test_wire_log/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  The
fixture root is outside the scan root, so a control server can never leak into
the live gate.  Removed in a `finally` unless --keep.

The case count IS typed in run.py's SUITES table, for `read_loop`'s reason
rather than `mcp_footprint`'s: a server that appears without a declared row is
the defect this suite exists to catch, so a count that moves when the roster
moves is the alarm working, not noise.

Offline, read-only apart from the sandbox, ~1s.

Usage:
  python3 tests/test_wire_log.py
  python3 tests/test_wire_log.py --brief
  python3 tests/test_wire_log.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  GATE     -- outbound `McpServer._write`, one case per server
  B  GATE     -- inbound `McpServer.run`, one case per server
  C  ROSTER   -- the table covers the tree exactly, its totals hold, and it
                 cannot declare a payload
  D  control  -- planted defects the analyser MUST flag, and correct forms it
                 must not
  E  GATE     -- MCP_SKELETON.md's §5 sample, lifted and run through the same
                 analyser
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

NAME = "wire_log"

SCAN_ROOT = "Scripts"
SERVER_GLOB_PREFIX = "mcp-"

SKELETON = "Scripts/MCP_SKELETON.md"
SKELETON_SECTION = "## 5. "
SKELETON_SECTION_END = "## 6. "

GA = "A. GATE: outbound _write is structure only"
GB = "B. GATE: inbound run is structure only"
GC = "C. ROSTER: the table covers the tree"
GD = "D. negative control"
GE = "E. GATE: the canonical skeleton"
GF = "F. hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_wire_log")
WRITES = []

# ---------------------------------------------------------------------------
# THE DECLARED TABLE
# ---------------------------------------------------------------------------
# server file -> (outbound shape, inbound shape, why this row reads as it does)
#
# A shape is the tuple of structural FIELDS the site is expected to log, in
# order, or SILENT for a site declared to log nothing.  See the module
# docstring for why there are three inbound forms and only one outbound one.

SILENT = None
OUT = ("id", "outcome")
FULL = ("method", "id", "name", "keys")
MINIMAL = ("method", "id")

WIRE = {
    "mcp-clangd.py":   (OUT, FULL,
                        "unregistered (purity_call); clangd_call dispatcher"),
    "mcp-context7.py": (OUT, FULL,
                        "context7_call dispatcher; replies carry fetched docs"),
    "mcp-cuda.py":     (OUT, FULL,
                        "unregistered (purity_call); cuda_call dispatcher"),
    "mcp-forge.py":    (OUT, FULL,
                        "forge_call dispatcher; replies carry build output"),
    "mcp-gdc.py":      (OUT, SILENT,
                        "the read loop logs nothing: isinstance guard straight "
                        "to create_task.  method+id is logged later, in "
                        "_handle_message, which is not a wire site"),
    "mcp-git.py":      (OUT, FULL,
                        "git_call dispatcher; replies carry diffs and blame"),
    "mcp-inspect.py":  (OUT, FULL,
                        "inspect_call dispatcher; replies carry process and "
                        "environment state"),
    "mcp-jenkins.py":  (OUT, FULL,
                        "jenkins_call dispatcher; requests can carry API "
                        "tokens in arguments"),
    "mcp-lldb.py":     (OUT, SILENT,
                        "as gdc: nothing logged on the read loop, method+id "
                        "logged in _handle_message instead"),
    "mcp-lua-lsp.py":  (OUT, MINIMAL,
                        "already structure only at both ends; declared at the "
                        "shape it has -- two safe shapes need not be merged, "
                        "they need to be declared"),
    "mcp-postgres.py": (OUT, FULL,
                        "pg_call dispatcher; replies carry query rows"),
    "mcp-purity.py":   (OUT, FULL,
                        "the canonical form both sites are gated against "
                        "(:6009-6021 inbound, :6059-6063 outbound)"),
    "mcp-tshark.py":   (OUT, FULL,
                        "tshark_call dispatcher; replies carry packet bytes"),
    "mcp-webfetch.py": (OUT, FULL,
                        "webfetch_call dispatcher; replies carry whatever the "
                        "fetched page served"),
    "mcp-wiki.py":     (OUT, FULL,
                        "wiki_call dispatcher; replies carry page bodies"),
}

# Declared totals, so a silent re-classification of one row trips a case rather
# than sliding through as "the table matches the table".
DECLARED_FULL = 12
DECLARED_MINIMAL = 1
DECLARED_SILENT = 2

# ---------------------------------------------------------------------------
# the rule, as data
# ---------------------------------------------------------------------------

# Keys whose VALUE is protocol structure rather than payload.  "name" is the
# dispatcher function name inside params -- purity logs it as fn=.
STRUCTURE_KEYS = {"jsonrpc", "method", "id", "name"}

# Keys whose value is a CONTAINER of payload: drillable, never loggable whole.
CONTAINER_KEYS = {"params", "arguments"}

# Calls whose result cannot carry payload no matter what goes in.
TAINT_CUTTING = {"type", "isinstance", "callable"}

LOG_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}

# NOT a filter -- every `<anything>.<level>(...)` in a wire site is judged.
# This set only decides whether the receiver is worth NAMING in the case
# detail, so an unusual one is visible rather than silently trusted.
LOGGER_BASES = {"log", "logger", "LOG", "_log", "self.log", "self._log"}

PAYLOAD_TOKEN = "PAYLOAD"

# Every token the analyser can emit for a SAFE projection.  A declaration may
# use any of these; group C refuses a declaration containing PAYLOAD_TOKEN.
SAFE_TOKENS = {"jsonrpc", "method", "id", "name", "keys", "outcome", "len",
               "pred", "expr"}

PURE, FIELD, CONTAINER, LEAK = "pure", "field", "container", "leak"
RANK = {PURE: 0, FIELD: 1, CONTAINER: 2, LEAK: 3}

# Problem codes.  The control group asserts these by name.
NO_SERVER_CLASS = "NO-MCPSERVER-CLASS"
NO_RUN = "NO-RUN-METHOD"
NO_WRITE = "NO-WRITE-METHOD"
NO_ROOT = "NO-PAYLOAD-ROOT"
PAYLOAD = "PAYLOAD-IN-WIRE-LOG"
SHAPE = "WIRE-SHAPE-NOT-DECLARED"
UNDECLARED_LOG = "UNDECLARED-WIRE-LOG"
MISSING_LOG = "NO-WIRE-LOG"
NOT_DECLARED = "SERVER-NOT-IN-TABLE"


# ---------------------------------------------------------------------------
# the analyser
# ---------------------------------------------------------------------------

def _src(node):
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"


class Site:
    """One wire site of one server: its payload root, its logs, its verdict."""

    def __init__(self, label):
        self.label = label
        self.root = None            # the payload-root name, once resolved
        self.problems = []          # problem codes
        self.detail = []            # human lines
        self.shape = None           # tuple of tokens, or None for "silent"
        self.logs = []              # (lineno, level, src, tokens)

    def fail(self, code, line):
        if code not in self.problems:
            self.problems.append(code)
        self.detail.append("%s: %s" % (code, line))

    @property
    def ok(self):
        return not self.problems


class Wire:
    """What one server's two wire sites actually log."""

    def __init__(self, path):
        self.path = path
        self.out = Site("outbound _write")
        self.inn = Site("inbound run")

    def fail_both(self, code, line):
        self.out.fail(code, line)
        self.inn.fail(code, line)


def _combine(*pairs):
    """Worst-of over (kind, label) classifications; label follows the worst."""
    worst = (PURE, "const")
    for kind, label in pairs:
        if RANK[kind] > RANK[worst[0]]:
            worst = (kind, label)
    return worst


def _key_rule(key):
    """What reaching `key` on a payload-derived object yields."""
    if key in STRUCTURE_KEYS:
        return (FIELD, key)
    if key in CONTAINER_KEYS:
        return (CONTAINER, key)
    return (LEAK, key)


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def classify(node, env):
    """Classify an expression as (kind, label) under taint environment `env`.

    kind is PURE (no wire value reachable), FIELD (a named structural
    projection of one), CONTAINER (a dict of wire values: drillable, not
    loggable), or LEAK (a wire value, whole or in part).
    """
    if node is None:
        return (PURE, "const")

    if isinstance(node, ast.Constant):
        return (PURE, "const")

    if isinstance(node, ast.Name):
        return (env.get(node.id, PURE), node.id)

    if isinstance(node, ast.Attribute):
        return classify(node.value, env)

    if isinstance(node, ast.Subscript):
        base = classify(node.value, env)
        if base[0] == PURE:
            return (PURE, "const")
        key = _const_str(node.slice)
        if key is not None:
            # `msg["method"]` is the same fact as `msg.get("method")`.
            return _key_rule(key)
        # A slice, an index, a computed key: all of them reach into the body.
        return (LEAK, base[1])

    if isinstance(node, ast.Call):
        return _classify_call(node, env)

    if isinstance(node, ast.JoinedStr):
        return _combine(*[classify(v.value, env) for v in node.values
                          if isinstance(v, ast.FormattedValue)])

    if isinstance(node, ast.FormattedValue):
        return classify(node.value, env)

    if isinstance(node, ast.IfExp):
        body = classify(node.body, env)
        orelse = classify(node.orelse, env)
        if body[0] == PURE and orelse[0] == PURE:
            # `"error" if "error" in response else "ok"` -- both branches are
            # literals, so the value logged is a literal.  It is still a FIELD
            # rather than PURE when the test touches the payload, because the
            # ok/error outcome IS a structural fact worth declaring.
            test = classify(node.test, env)
            return (FIELD, "outcome") if test[0] != PURE else (PURE, "const")
        return _combine(body, orelse)

    if isinstance(node, ast.Compare):
        parts = [classify(node.left, env)]
        parts += [classify(c, env) for c in node.comparators]
        # A comparison yields a bool: one bit about the payload, not the body.
        return (FIELD, "pred") if _combine(*parts)[0] != PURE else (PURE, "const")

    if isinstance(node, (ast.BoolOp,)):
        return _combine(*[classify(v, env) for v in node.values])

    if isinstance(node, ast.BinOp):
        return _combine(classify(node.left, env), classify(node.right, env))

    if isinstance(node, ast.UnaryOp):
        return classify(node.operand, env)

    if isinstance(node, ast.Await):
        return classify(node.value, env)

    if isinstance(node, ast.Starred):
        return classify(node.value, env)

    # Anything not spelled out above (dicts, comprehensions, lambdas, ...) is
    # LEAK on contact.  Conservative on purpose: this is a gate, and the cost
    # of being wrong in this direction is a case a human reads, while the cost
    # of the other direction is a body in a log file nobody reads.
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and env.get(sub.id, PURE) != PURE:
            return (LEAK, sub.id)
    return (PURE, "const")


def _classify_call(node, env):
    func = node.func
    args = list(node.args) + [kw.value for kw in node.keywords]

    if isinstance(func, ast.Attribute):
        base = classify(func.value, env)
        attr = func.attr
        if base[0] != PURE:
            if attr == "get" and node.args:
                key = _const_str(node.args[0])
                if key is not None:
                    return _key_rule(key)
                return (LEAK, base[1])
            if attr == "keys":
                return (FIELD, "keys")
            if attr in ("values", "items"):
                return (LEAK, base[1])
        return _combine(base, *[classify(a, env) for a in args])

    if isinstance(func, ast.Name):
        if func.id in TAINT_CUTTING:
            return (PURE, "const")
        inner = _combine(*[classify(a, env) for a in args])
        if func.id == "len":
            return (FIELD, "len") if inner[0] != PURE else (PURE, "const")
        if inner[0] in (LEAK, CONTAINER):
            return (LEAK, inner[1])
        if inner[0] == FIELD:
            # list(args.keys()), sorted(...), tuple(...): a wrapper around an
            # already-safe projection stays safe and keeps its name.
            return inner
        return (PURE, "const")

    return _combine(*[classify(a, env) for a in args])


def _assign_targets(node):
    out = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                out.append(target.id)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        if isinstance(node.target, ast.Name):
            out.append(node.target.id)
    return out


def _propagate(env, scope):
    """Spread taint across assignments in `scope` to a fixpoint (worst-of).

    Worst-of matters: `_write` assigns `out` twice, and only the first one --
    `json.dumps(response)` -- is a leak.  A last-writer-wins environment would
    untaint `out` on the exception path and then wave `log.debug("→ %s", out)`
    straight through.
    """
    assigns = [n for n in ast.walk(scope)
               if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))]
    for _ in range(8):
        changed = False
        for node in assigns:
            value = getattr(node, "value", None)
            if value is None:
                continue
            kind, _label = classify(value, env)
            for name in _assign_targets(node):
                if RANK[kind] > RANK[env.get(name, PURE)]:
                    env[name] = kind
                    changed = True
        if not changed:
            break
    return env


def _log_call_base(node):
    """The logger expression of a `<x>.debug(...)`-shaped call, else None.

    Deliberately NOT restricted to `LOGGER_BASES`: a gate that only recognises
    the fleet's current spelling hands a fixer a one-line evasion
    (`logging.getLogger(__name__).debug(out)` would be invisible).  Every
    `<anything>.<level>(...)` inside a wire site is judged by the same rule,
    and an unfamiliar base is named in the case detail so a reader can see
    what was judged.  Measured: the live fleet spells all thirty of them
    `log.`, so the widening costs nothing today and closes the hole tomorrow.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in LOG_LEVELS:
        return None
    return _src(node.func.value)


def _collect(site, scope, env, line_offset=0):
    """Record every wire log in `scope` and the shape they add up to.

    `line_offset` exists for group E: the skeleton's sample is analysed inside
    a synthetic host, and a finding that cites the host's line numbers sends
    the reader to the wrong file.  Every number this records is the number in
    the file a human would open.
    """
    calls = sorted((n for n in ast.walk(scope) if _log_call_base(n) is not None),
                   key=lambda n: (n.lineno, n.col_offset))
    tokens = []
    wire_logs = 0
    for call in calls:
        base = _log_call_base(call)
        spelling = "%s.%s" % (base, call.func.attr)
        args = list(call.args) + [kw.value for kw in call.keywords]
        local, leaks = [], []
        for arg in args:
            kind, label = classify(arg, env)
            if kind == PURE:
                continue
            if kind in (LEAK, CONTAINER):
                local.append(PAYLOAD_TOKEN)
                leaks.append((kind, label, _src(arg)))
            else:
                local.append(label)
        if not local:
            continue                       # a literal-only log is not a wire log
        wire_logs += 1
        tokens.extend(local)
        lineno = call.lineno + line_offset
        site.logs.append((lineno, spelling, _src(call), tuple(local)))
        if base not in LOGGER_BASES:
            site.detail.append("unfamiliar logger base %r at line %d -- judged "
                               "by the same rule as `log.`" % (base, lineno))
        for kind, label, src in leaks:
            site.fail(PAYLOAD,
                      "line %d: %s argument `%s` is payload-derived (%s "
                      "%r) -- a %s of the wire message, not its structure"
                      % (lineno, spelling, src, kind, label,
                         "container" if kind == CONTAINER else "value"))
    site.shape = tuple(tokens) if wire_logs else None


def _readline_target(run_node):
    """The name the readline result is bound to in `run()`, or (None, why)."""
    call = None
    for node in ast.walk(run_node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_in_executor" and len(node.args) >= 2
                and _src(node.args[1]) == "sys.stdin.readline"):
            call = node
            break
    if call is None:
        for node in ast.walk(run_node):
            if isinstance(node, ast.Call) and _src(node.func) == "sys.stdin.readline":
                call = node
                break
    if call is None:
        return None, "run() contains no sys.stdin.readline to taint from"
    for node in ast.walk(run_node):
        if isinstance(node, ast.Assign):
            for sub in ast.walk(node.value):
                if sub is call:
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    if names:
                        return names[0], None
    return None, ("line %d: the readline result is not bound to a plain name"
                  % call.lineno)


def analyse(path, source=None, line_offset=0):
    """Measure one server file's two wire sites."""
    wire = Wire(path)
    if source is None:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        wire.fail_both(NO_SERVER_CLASS, "unparseable: %s" % exc)
        return wire

    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "McpServer":
            cls = node
            break
    if cls is None:
        wire.fail_both(NO_SERVER_CLASS, "no class McpServer to inspect")
        return wire

    run_node = write_node = None
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name == "run" and run_node is None:
                run_node = item
            elif item.name == "_write" and write_node is None:
                write_node = item

    if run_node is None:
        wire.inn.fail(NO_RUN, "no McpServer.run to inspect")
    else:
        root, why = _readline_target(run_node)
        if root is None:
            wire.inn.fail(NO_ROOT, why)
        else:
            wire.inn.root = root
            env = _propagate({root: LEAK}, run_node)
            wire.inn.detail.append("tainted     : %s"
                                   % ", ".join("%s=%s" % (k, v) for k, v
                                               in sorted(env.items())))
            _collect(wire.inn, run_node, env, line_offset)

    if write_node is None:
        wire.out.fail(NO_WRITE, "no McpServer._write to inspect")
    else:
        params = [a.arg for a in write_node.args.args]
        if len(params) < 2:
            wire.out.fail(NO_ROOT, "line %d: _write takes no response parameter"
                          % write_node.lineno)
        else:
            root = params[1]
            wire.out.root = root
            env = _propagate({root: LEAK}, write_node)
            wire.out.detail.append("tainted     : %s"
                                   % ", ".join("%s=%s" % (k, v) for k, v
                                               in sorted(env.items())))
            _collect(wire.out, write_node, env, line_offset)

    return wire


def compare(site, declared):
    """Problems from comparing a measured site against its declared shape."""
    problems = list(site.problems)
    if declared is SILENT and site.shape is not None:
        if UNDECLARED_LOG not in problems:
            problems.append(UNDECLARED_LOG)
    elif declared is not SILENT and site.shape is None:
        if MISSING_LOG not in problems:
            problems.append(MISSING_LOG)
    elif declared is not SILENT and site.shape != tuple(declared):
        if SHAPE not in problems:
            problems.append(SHAPE)
    return problems


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _server_files():
    root = H.repo_path(SCAN_ROOT)
    return sorted(n for n in os.listdir(root)
                  if n.startswith(SERVER_GLOB_PREFIX) and n.endswith(".py"))


def _shape_text(shape):
    return "SILENT (no wire log)" if shape is None else repr(tuple(shape))


def _site_detail(name, site, declared):
    detail = ["file        : %s/%s" % (SCAN_ROOT, name),
              "site        : McpServer.%s" % ("_write" if "out" in site.label
                                              else "run"),
              "payload root: %s" % (site.root or "UNRESOLVED"),
              "declared    : %s" % _shape_text(declared),
              "measured    : %s" % _shape_text(site.shape)]
    for lineno, spelling, src, tokens in site.logs:
        detail.append("wire log    : line %d  %s -> %r" % (lineno, spelling, tokens))
        detail.append("              %s" % src[:160])
    detail += ["  " + d for d in site.detail if not d.startswith("tainted")]
    return detail


def _group_site(suite, group, shapes, which):
    for name in sorted(shapes):
        wire = shapes[name]
        site = wire.out if which == "out" else wire.inn
        if name not in WIRE:
            suite.record(group, name, [NOT_DECLARED],
                         detail=["a new server must be declared in WIRE, with "
                                 "the shape each of its two wire sites logs, "
                                 "before it can pass",
                                 "measured    : %s" % _shape_text(site.shape)],
                         brief="%s | %s | %s" % (H.FAIL, name, NOT_DECLARED))
            continue
        declared = WIRE[name][0 if which == "out" else 1]
        problems = compare(site, declared)
        suite.record(group, name, problems,
                     detail=_site_detail(name, site, declared)
                            + ["why         : %s" % WIRE[name][2]],
                     brief="%s | %s | %s" % (H.FAIL if problems else H.PASS,
                                             name,
                                             problems or _shape_text(site.shape)))


def group_outbound(suite, shapes):
    """A. every server's _write logs id + outcome and nothing else."""
    _group_site(suite, GA, shapes, "out")


def group_inbound(suite, shapes):
    """B. every server's run() logs protocol structure and nothing else."""
    _group_site(suite, GB, shapes, "inn")


def group_roster(suite, shapes, files):
    """C. the table covers the tree, its totals hold, it cannot declare a leak."""
    declared = set(WIRE)
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
                         "groups A and B by name AND trips the typed case "
                         "count in run.py"])

    full = sorted(n for n, r in WIRE.items() if r[1] == FULL)
    minimal = sorted(n for n, r in WIRE.items() if r[1] == MINIMAL)
    silent = sorted(n for n, r in WIRE.items() if r[1] is SILENT)
    odd_out = sorted(n for n, r in WIRE.items() if r[0] != OUT)
    problems = []
    if len(full) != DECLARED_FULL:
        problems.append("FULL inbound group is %d, declared %d"
                        % (len(full), DECLARED_FULL))
    if len(minimal) != DECLARED_MINIMAL:
        problems.append("MINIMAL inbound group is %d, declared %d"
                        % (len(minimal), DECLARED_MINIMAL))
    if len(silent) != DECLARED_SILENT:
        problems.append("SILENT inbound group is %d, declared %d"
                        % (len(silent), DECLARED_SILENT))
    if odd_out:
        problems.append("non-canonical outbound declaration(s): %s"
                        % ", ".join(odd_out))
    suite.record(GC, "the declared inbound split matches its totals", problems,
                 detail=["FULL   (%d) : %s" % (len(full), ", ".join(full)),
                         "MINIMAL(%d) : %s" % (len(minimal), ", ".join(minimal)),
                         "SILENT (%d) : %s" % (len(silent), ", ".join(silent)),
                         "outbound    : %d row(s) declare %r"
                         % (len(WIRE) - len(odd_out), OUT),
                         "note        : moving a server between these groups "
                         "is a decision about what a debug log is worth, so it "
                         "must trip something rather than slide through"])

    bad = []
    for name, (out_shape, in_shape, _why) in sorted(WIRE.items()):
        for label, shape in (("outbound", out_shape), ("inbound", in_shape)):
            if shape is SILENT:
                continue
            for token in shape:
                if token == PAYLOAD_TOKEN or token not in SAFE_TOKENS:
                    bad.append("%s %s declares %r" % (name, label, token))
    suite.record(GC, "no declared row declares a payload", bad,
                 detail=["vocabulary  : %s" % ", ".join(sorted(SAFE_TOKENS)),
                         "note        : without this case the table is a hole "
                         "in the gate -- a failing server could be 'fixed' by "
                         "declaring %r as its expected shape" % PAYLOAD_TOKEN])

    resolved = [n for n, w in shapes.items() if w.out.root and w.inn.root]
    suite.record(GC, "the analyser resolved a payload root at both sites",
                 [] if len(resolved) == len(files)
                 else ["resolved %d of %d: missing %s"
                       % (len(resolved), len(files),
                          ", ".join(sorted(set(files) - set(resolved))))],
                 detail=["resolved    : %d/%d" % (len(resolved), len(files)),
                         "note        : a blindness floor.  With no root there "
                         "is no taint, every argument classifies PURE, and the "
                         "gate reports a clean fleet while measuring nothing"])


# -- group D fixtures --------------------------------------------------------

INBOUND_GOOD = '''\
                log.debug(
                    "← method=%s id=%s fn=%s keys=%s",
                    msg.get("method"), msg.get("id"), _p.get("name"),
                    list(_args.keys()),
                )'''

OUTBOUND_GOOD = '''\
        log.debug(
            "→ id=%s %s", response.get("id"),
            "error" if "error" in response else "ok",
        )'''

# Spliced with str.replace, never with `%`: the sample is full of `%s` format
# strings of its own, and `%`-formatting a template that contains the thing it
# is teaching is how the first draft of this file failed to import.
GOOD_TEMPLATE = '''\
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor

log = None


class McpServer:
    async def run(self):
        loop = asyncio.get_running_loop()
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ctl-stdin")
        inflight = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    continue
                _p = msg.get("params")
                _p = _p if isinstance(_p, dict) else {}
                _args = _p.get("arguments")
                _args = _args if isinstance(_args, dict) else {}
@@INBOUND@@
                task = loop.create_task(self._serve(msg))
                inflight.add(task)
        finally:
            reader.shutdown(wait=False)
            log.info("MCP server shutting down")

    def _write(self, response):
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
@@OUTBOUND@@
        try:
            sys.stdout.write(out + "\\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            log.warning("stdout write failed: %s", exc)
'''

INBOUND_SLOT = "@@INBOUND@@"
OUTBOUND_SLOT = "@@OUTBOUND@@"


def _splice(inbound, outbound):
    return (GOOD_TEMPLATE.replace(INBOUND_SLOT, inbound)
                         .replace(OUTBOUND_SLOT, outbound))


GOOD = _splice(INBOUND_GOOD, OUTBOUND_GOOD)


def _plant(inbound=None, outbound=None):
    """GOOD with one wire log replaced.  Returns (source, applied).

    `applied` is False when a plant produced a file byte-identical to GOOD --
    a defect fixture that failed to apply would otherwise pass, green, and
    prove nothing.  Group D records that as a failure in its own right.
    """
    source = _splice(INBOUND_GOOD if inbound is None else inbound,
                     OUTBOUND_GOOD if outbound is None else outbound)
    return source, source != GOOD


# name -> (source, applied, expected codes, expected out shape, expected in shape)
def _fixtures():
    out = {}

    def add(name, expect, out_shape, in_shape, inbound=None, outbound=None):
        source, applied = _plant(inbound=inbound, outbound=outbound)
        if inbound is None and outbound is None:
            applied = True                      # ctl_good is GOOD on purpose
        out[name] = (source, applied, expect, out_shape, in_shape)

    add("ctl_good.py", [], OUT, FULL)

    add("ctl_silent.py", [], None, None,
        inbound="                pass",
        outbound="        pass")

    add("ctl_subscript_key.py", [], OUT, MINIMAL,
        inbound='                log.debug("← method=%s id=%s",\n'
                '                          msg["method"], msg["id"])')

    add("ctl_bare_out.py", [PAYLOAD], (PAYLOAD_TOKEN,), FULL,
        outbound='        log.debug("→ %s", out)')

    add("ctl_sliced_out.py", [PAYLOAD], (PAYLOAD_TOKEN,), FULL,
        outbound='        log.debug("→ %s", out[:200])')

    add("ctl_bare_response.py", [PAYLOAD], (PAYLOAD_TOKEN,), FULL,
        outbound='        log.debug("→ %s", response)')

    add("ctl_result_value.py", [PAYLOAD], ("id", PAYLOAD_TOKEN), FULL,
        outbound='        log.debug("→ id=%s %s", response.get("id"),\n'
                 '                  response.get("result"))')

    add("ctl_fstring_line.py", [PAYLOAD], OUT, (PAYLOAD_TOKEN,),
        inbound='                log.debug(f"← RAW: {line}")')

    add("ctl_json_dumps_msg.py", [PAYLOAD], OUT, (PAYLOAD_TOKEN,),
        inbound='                log.debug("← %s", json.dumps(msg)[:200])')

    add("ctl_arg_value.py", [PAYLOAD], OUT, ("method", PAYLOAD_TOKEN),
        inbound='                log.debug("← %s %s", msg.get("method"),\n'
                '                          _args.get("content"))')

    add("ctl_container.py", [PAYLOAD], OUT, ("method", PAYLOAD_TOKEN),
        inbound='                log.debug("← %s %s", msg.get("method"),\n'
                '                          _p.get("arguments"))')

    return out


FIXTURES = _fixtures()


def write_fixtures(root):
    for name, row in sorted(FIXTURES.items()):
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(row[0])
        WRITES.append(path)


def group_control(suite, fixture_root):
    """D. planted defects the analyser MUST flag, and correct ones it must not."""
    write_fixtures(fixture_root)
    flagged = 0
    for name, (_source, applied, expect, out_shape, in_shape) in sorted(
            FIXTURES.items()):
        wire = analyse(os.path.join(fixture_root, name))
        got = sorted(set(wire.out.problems) | set(wire.inn.problems))
        problems = []
        if not applied:
            problems.append("the plant did not apply: the fixture is identical "
                            "to GOOD, so a green case here proves nothing")
        if got != sorted(expect):
            problems.append("codes %r != expected %r" % (got, sorted(expect)))
        if wire.out.shape != (None if out_shape is None else tuple(out_shape)):
            problems.append("outbound shape %s != expected %s"
                            % (_shape_text(wire.out.shape), _shape_text(out_shape)))
        if wire.inn.shape != (None if in_shape is None else tuple(in_shape)):
            problems.append("inbound shape %s != expected %s"
                            % (_shape_text(wire.inn.shape), _shape_text(in_shape)))
        if expect:
            flagged += 1
        detail = ["expected    : codes=%r out=%s in=%s"
                  % (sorted(expect), _shape_text(out_shape), _shape_text(in_shape)),
                  "got         : codes=%r out=%s in=%s"
                  % (got, _shape_text(wire.out.shape), _shape_text(wire.inn.shape))]
        detail += ["  " + d for d in wire.out.detail + wire.inn.detail
                   if not d.startswith("tainted")]
        suite.record(GD, "control-" + name, problems, detail=detail,
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))

    must = sum(1 for _n, row in FIXTURES.items() if row[2])
    suite.record(GD, "control fires at all",
                 [] if flagged == must
                 else ["only %d of %d defective fixtures flagged"
                       % (flagged, must)],
                 detail=["fixtures    : %d (%d defective, %d correct)"
                         % (len(FIXTURES), must, len(FIXTURES) - must),
                         "note        : the three correct fixtures matter as "
                         "much as the eight defective ones -- an analyser that "
                         "refuses everything is as useless as one that refuses "
                         "nothing, and ctl_subscript_key proves the rule is on "
                         "the value reached rather than on the spelling"])


# -- group E: the skeleton ---------------------------------------------------

# Concatenated, never `%`-formatted: the lifted sample is full of `%s` format
# strings, which is precisely what makes `%` the wrong splice here.
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


def lift_python_blocks(text, start_prefix, end_prefix):
    """Every ```python block between two headings: [(first code line, text)]."""
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None:
            if line.startswith(start_prefix):
                start = i
        elif line.startswith(end_prefix):
            end = i
            break
    if start is None:
        return [], "section %r not found in %s" % (start_prefix, SKELETON)
    if end is None:
        end = len(lines)
    blocks, current, first = [], None, 0
    for offset, line in enumerate(lines[start:end]):
        lineno = start + offset + 1                     # 1-based, file-relative
        if current is None:
            if line.strip() == "```python":
                current, first = [], lineno + 1
        elif line.strip() == "```":
            blocks.append((first, "\n".join(current)))
            current = None
        else:
            current.append(line)
    if current is not None:
        return blocks, "unterminated ```python fence in %s" % start_prefix
    return blocks, None


def group_skeleton(suite):
    """E. the template's own sample, through the same analyser as the fleet."""
    path = H.repo_path(SKELETON)
    blocks, why = ([], "%s is absent" % SKELETON)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            blocks, why = lift_python_blocks(fh.read(), SKELETON_SECTION,
                                             SKELETON_SECTION_END)

    problems = []
    wire = None
    if why:
        problems.append(why)
    elif len(blocks) != 1:
        problems.append("expected exactly 1 python sample in %r, found %d -- "
                        "the lift is ambiguous and the gate would be measuring "
                        "a guess" % (SKELETON_SECTION, len(blocks)))
    else:
        first, body = blocks[0]
        host = SKELETON_HOST + body + "\n"
        # The sample's first line becomes host line len(prefix)+1, so this
        # offset turns every host line number back into a MCP_SKELETON.md one.
        offset = first - SKELETON_HOST.count("\n") - 1
        try:
            wire = analyse(SKELETON, source=host, line_offset=offset)
        except SyntaxError as exc:
            problems.append("the lifted sample does not parse in a minimal "
                            "host: %s" % exc)
    suite.record(GE, "the section 5 sample lifts, splices and parses", problems,
                 detail=["file        : %s" % SKELETON,
                         "section     : %r .. %r"
                         % (SKELETON_SECTION, SKELETON_SECTION_END),
                         "blocks      : %d" % len(blocks),
                         "sample at   : %s"
                         % (("%s:%d" % (SKELETON, blocks[0][0]))
                            if len(blocks) == 1 else "n/a"),
                         "note        : lifted BY SCRIPT and run through the "
                         "SAME analyser the fleet gets (a7b165e's technique). "
                         "Hand-checking prose is how this template stayed "
                         "wrong through two edits after the read-loop rollout"])

    for label, which, declared in (("outbound _write", "out", OUT),
                                   ("inbound run", "inn", FULL)):
        if wire is None:
            suite.record(GE, "skeleton %s" % label,
                         ["no sample to measure (see the lift case above)"],
                         detail=["note        : this suite deliberately GATES "
                                 "the template rather than surveying it -- it "
                                 "is the proven propagation vector for exactly "
                                 "this defect"])
            continue
        site = wire.out if which == "out" else wire.inn
        suite.record(GE, "skeleton %s" % label, compare(site, declared),
                     detail=_site_detail("MCP_SKELETON.md", site, declared)
                            + ["note        : the line numbers above are "
                               "%s's own, not the synthetic host's" % SKELETON,
                               "why         : every new server in this fleet "
                               "is written from this file.  Fixing fifteen "
                               "servers while the template still teaches the "
                               "defect fixes nothing for server sixteen"],
                     brief="%s | skeleton %s | %s"
                           % (H.FAIL if compare(site, declared) else H.PASS,
                              label, _shape_text(site.shape)))


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
                         "_harness.repo_tree, so the fixtures are invisible "
                         "here by construction, not by luck"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="every MCP server's wire logging is structure only: "
                          "protocol metadata and argument KEYS, never a "
                          "payload body or value (F12 / CWE-532)",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    files = _server_files()
    shapes = {n: analyse(H.repo_path(SCAN_ROOT, n)) for n in files}

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_outbound(suite, shapes)
        group_inbound(suite, shapes)
        group_roster(suite, shapes, files)
        group_control(suite, fixture_root)
        group_skeleton(suite)
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
