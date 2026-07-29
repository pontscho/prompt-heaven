#!/usr/bin/env python3
"""What the MCP fleet costs in tokens (A-G).  A MEASURING TAPE, NOT A GATE.

WHY THIS EXISTS
---------------
Three taxes, all paid out of the context window, all invisible until measured:

  1. DESCRIPTION TAX -- a connected server's `tools/list` reply is resent to the
     model on EVERY request for the whole session.  It is a fixed subtraction
     from the window, paid whether or not a single tool is ever called.  The
     single-dispatcher design exists to keep it small; this suite says whether
     it actually is.
  2. RESULT CEILING -- what ONE call may dump into the transcript.  A server
     with no output cap can spend the entire remaining window in one reply.
  3. BOILERPLATE -- what a handler emits regardless of content: headings,
     fences, command echoes, filter statistics, empty-result notes.

EVERY FINDING ABOUT A SERVER IS `INFO` IN THIS ROUND -- ON PURPOSE
-----------------------------------------------------------------
A missing cap, a non-conforming cap, a fat description: all INFO, never FAIL.
Turning them into a gate is a LATER work item, and doing it now would paint
`test all` red until seven separate server fixes land -- i.e. it would report a
decision that has not been made yet as a regression.

What IS gated here is the suite's own integrity, because a measuring tape that
silently reads zero is worse than no tape at all:

  * group D  -- the cap detector must discriminate.  Planted defects it MUST
                flag and bait it must NOT, same contract as
                `tests/test_spawn_stdin.py` group C.
  * group A  -- a probe floor: if fewer than MIN_ANSWERING servers answered,
                the prober is broken, not the fleet.
  * group G  -- hygiene: every write under `.claude/tmp`, no `.pyc` anywhere,
                no child handed a project root outside the sandbox.
  * group H  -- purity's truncation behaviour at RUNTIME.  Not a conformance
                verdict pending a decision: a regression guard on a defect that
                shipped and has been fixed (a reply of one header and zero rows).

THE REGISTERED-vs-EXISTING DISTINCTION IS LOAD-BEARING
------------------------------------------------------
`Scripts/_mcp_smoke_test.py`'s `SERVERS` table lists every server FILE.  That is
NOT the same set as the servers Claude Code actually launches: registration
lives in `~/.claude.json`.  An UNREGISTERED server's footprint is ZERO -- it is
never started, so its descriptions are never sent.  Summing the fleet over the
file set therefore optimises for the wrong number, and that exact conflation has
already produced one wrong conclusion in this repo's history.

So each `SERVERS` entry now carries an explicit `registered` flag, this suite
sums the footprint over `registered: True` only, reports the rest separately
under the label INERT, and group F cross-checks the flags against the live
`~/.claude.json`.  Unreadable file or unexpected structure -> SKIP WITH A
REASON.  Never invent registration data.

THE v1 CAP CONVENTION THIS SUITE RECOGNISES
-------------------------------------------
Decided by the caller; this suite MEASURES conformance and enforces nothing:

  * param `max_answer_chars` (int, per-call overridable), default 24000 chars
  * on truncation, EXACTLY ONE closing line:
      [truncated: kept <n> of <total> chars from the <head|tail>; raise
       max_answer_chars or narrow the query]
  * row-shaped payloads truncate by ROW:
      [showing rows <a>-<b> of <total>; offset=<b> for more]
  * head-biased by default, tail-biased where the summary is at the end (forge
    test aggregate), and the line SAYS which end it kept
  * truncation happens on a LINE BOUNDARY -- a `file:line` anchor is never cut

Three of those are checkable from source text (param name, default value, the
marker sentence including its head/tail phrase).  The row form is reported as an
extra.  Line-boundary behaviour is NOT statically checkable, so groups A-G do not
claim it; group H drives ONE server (purity) in-process and asserts it there,
including the degenerate case where no whole line fits and the cut must go inside
a row -- keeping its `file:line` anchor -- rather than return a bare header.

STRUCTURAL, NOT TEXTUAL
-----------------------
The ceiling detector is AST-based, like `test_spawn_stdin.py`, and for the same
reason: a text search gets this wrong in both directions, and both mistakes are
live in this repo right now.

  * `Scripts/mcp-git.py:697` and `Scripts/mcp-wiki.py:1140` -- the string
    "max_answer_chars (default 100000)" inside the TOOL DESCRIPTION.  A grep for
    the param name counts those as caps.  They are advertising, not code.
  * `Scripts/mcp-wiki.py:577` -- a DOCSTRING saying "truncating at
    max_answer_chars (default 100k)".  Same problem.
  * `Scripts/mcp-wiki.py:578` -- the real cap is `params.get("max_answer_chars")`
    with NO default argument; 100000 lives in the `int(raw) if raw is not None
    else 100000` fallback one line down.  A pattern keyed on `get(x, N)` misses
    it entirely, so the value is INFERRED from the enclosing function and
    LABELLED as inferred.

The description tax is measured over a real JSON-RPC handshake, porting the
robustness contract of `.claude/tmp/desc-audit/measure.py`: every server is
probed in full isolation, and one that is missing, dies on startup, closes its
stdin, or never answers is recorded WITH A REASON while the run continues.  No
single server can abort or stall the round.  (`measure.py`'s `inspect_delta()`
is deliberately NOT ported: a HEAD-vs-working-tree git comparison is an
on-demand audit, not a measurement.)

TOKEN NUMBERS ARE ESTIMATES
---------------------------
`~tokens` is `chars / 4`.  No tokenizer was run.  Every rendered table says so.

Groups:
  A  description tax, REGISTERED fleet -- one case per server, plus the total
     and the probe floor
  B  description tax, INERT files -- the same measurement, summed separately,
     because their live footprint is zero
  C  result ceiling, structural -- one case per server SOURCE file: cap param,
     default, v1 conformance, aliases, pagination knobs, hardcoded constants
  D  negative control -- planted caps the detector MUST classify correctly, and
     the description/docstring bait it must NOT count as a cap
  E  boilerplate -- the fixed per-call payload: pattern census with file:line
     exemplars, plus the one live number that is actually comparable
  F  registry drift -- SERVERS.registered vs ~/.claude.json (INFO this round)
  G  hygiene -- sandbox discipline and a bytecode-free tree
  H  cap runtime, purity white-box -- what the cut LANDS on: whole rows while any
     fit, an anchored in-line cut when none does, one count per reply

Offline apart from spawning the servers themselves; ~5-10 s.

Usage:
  python3 tests/test_mcp_footprint.py
  python3 tests/test_mcp_footprint.py --brief
  python3 tests/test_mcp_footprint.py --keep
Exit code 0 iff every non-informational case passes.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_footprint"
SKIP = "SKIP"

SMOKE_PATH = H.repo_path("Scripts", "_mcp_smoke_test.py")
SCRIPTS_DIR = H.repo_path("Scripts")
CLAUDE_JSON = os.path.expanduser("~/.claude.json")

GA = "A. description tax: REGISTERED fleet"
GB = "B. description tax: INERT files (footprint zero)"
GC = "C. result ceiling: structural, per server source"
GD = "D. negative control: the ceiling detector discriminates"
GE = "E. boilerplate: the fixed per-call payload"
GF = "F. registry drift: SERVERS.registered vs ~/.claude.json"
GG = "G. hygiene"
GH = "H. cap runtime: purity's line-boundary contract (white-box, GATED)"

# All scratch lives here, one mkdtemp subdir per run: a standalone run and a
# tests/run.py run may overlap, and a fixed path means one instance's teardown
# deletes the other's fixtures mid-scan.
FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_mcp_footprint")

# Every path written and every child command line launched is recorded, so
# group G can ASSERT the sandbox rules instead of stating them in a comment.
WRITES = []
CHILD_ARGV = []

# Ported from .claude/tmp/desc-audit/measure.py -- see the module docstring.
READ_TIMEOUT = 10.0        # budget for ONE response
SERVER_DEADLINE = 20.0     # hard cap for initialize + tools/list + probe
STDERR_TAIL = 240

# A blindness FLOOR, not a count.  Deliberately far below the 11 registered
# servers: it can only trip if the prober itself stopped working.  A fleet that
# legitimately shrinks moves the real number down, and the number is printed.
MIN_ANSWERING = 4

PROBE_FUNCTION = "__ph_footprint_probe__"


# ---------------------------------------------------------------------------
# the v1 cap convention (decided by the caller; measured, not enforced)
# ---------------------------------------------------------------------------

V1_PARAM = "max_answer_chars"
V1_DEFAULT = 24000
V1_TRUNC_OPEN = "[truncated: kept "
V1_TRUNC_CLOSE = "raise max_answer_chars or narrow the query]"
V1_TRUNC_END_WORDS = ("from the head", "from the tail")
V1_ROWS_OPEN = "[showing rows "
V1_ROWS_MID = "; offset="
V1_ROWS_CLOSE = " for more]"


def v1_truncation_marker(text):
    """True iff `text` is the v1 char-truncation line, head/tail phrase included."""
    return (V1_TRUNC_OPEN in text and V1_TRUNC_CLOSE in text
            and any(w in text for w in V1_TRUNC_END_WORDS))


def v1_row_marker(text):
    """True iff `text` is the v1 row-truncation line."""
    return (V1_ROWS_OPEN in text and V1_ROWS_MID in text
            and V1_ROWS_CLOSE in text)


# ---------------------------------------------------------------------------
# sandbox discipline
# ---------------------------------------------------------------------------

def write_text(path, body):
    """The ONLY write path in this module.  Creates parents; records the path."""
    WRITES.append(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def inside(path, root):
    """True iff `path` is `root` or lives under it (symlinks resolved)."""
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


# ---------------------------------------------------------------------------
# subgroup 1: the description tax, over a real handshake
# ---------------------------------------------------------------------------

class Probe(H.JsonRpcClient):
    """`H.JsonRpcClient` with the handshake lifted OUT of the constructor.

    The base class is the JSON-RPC layer this suite wants -- one request in
    flight, `select()` on the child's stdout so a wedged server becomes a
    timeout instead of a deadlock, a stderr tail on death.  All of that is
    reused verbatim.

    Only ONE thing is relocated: the base class sends `initialize` from inside
    `__init__`, so a server that never answers it raises BEFORE the instance
    exists -- and the caller is then holding no object to `.close()`, leaving a
    live child behind.  The ported robustness contract requires every probe to
    be reaped, so spawn and handshake are separated here and every failure path
    still ends in `close()`.
    """

    def __init__(self, argv, tool=None, cwd=None, timeout=READ_TIMEOUT):
        # Deliberately NOT super().__init__(): that would hand-shake.
        self.argv = list(argv)
        self.tool = tool
        self.timeout = float(timeout)
        self._id = 0
        self.proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd,
            env=H.child_env(),
        )

    def handshake(self):
        return self.rpc("initialize",
                        {"protocolVersion": self.PROTOCOL_VERSION,
                         "capabilities": {},
                         "clientInfo": {"name": "ph-footprint",
                                        "version": "1"}})

    def death_reason(self):
        """Why the child is unusable, with a stderr tail when there is one."""
        rc = self.proc.poll()
        tail = ""
        try:
            self.proc.kill()
            tail = (self.proc.stderr.read() or "").strip()
        except Exception:                                     # noqa: BLE001
            pass
        tail = tail.replace("\n", " | ")[-STDERR_TAIL:]
        head = ("stdin closed by the server (still running)" if rc is None
                else "exited immediately (rc=%s)" % rc)
        return head + (" [stderr: %s]" % tail if tail else "")


class Footprint:
    """One server's measured `tools/list` cost, or the reason it stayed unknown."""

    def __init__(self, file, tool, registered):
        self.file = file
        self.tool = tool
        self.registered = bool(registered)
        self.n_tools = 0
        self.desc_chars = 0
        self.wire_chars = 0
        self.descriptions = []
        self.probe_chars = None      # unknown-function reply length
        self.error = ""

    @property
    def ok(self):
        return not self.error

    @property
    def desc_blob(self):
        return "\n".join(self.descriptions)

    def row(self, width=18):
        if self.error:
            return "%-*s %5s %11s %8s %11s %8s  %s" % (
                width, self.file, "-", "-", "-", "-", "-", self.error)
        return "%-*s %5d %11d %8d %11d %8d  %s" % (
            width, self.file, self.n_tools, self.desc_chars,
            tok(self.desc_chars), self.wire_chars, tok(self.wire_chars),
            "probe reply %s chars" % (self.probe_chars
                                      if self.probe_chars is not None
                                      else "n/a"))


def tok(chars):
    """The ESTIMATE.  chars / 4.  No tokenizer was run -- said everywhere."""
    return round(chars / 4)


def probe_argv(path, args, sandbox):
    """Child command line for one server: recorded, `-B`, sandbox-rooted.

    Two invariants group G later asserts:
      * `-B` on top of `H.child_env()`'s PYTHONDONTWRITEBYTECODE=1, so a probed
        server cannot drop a `.pyc` into the repo tree;
      * every `--project-root` the launch table supplies (it uses `/tmp`) is
        REWRITTEN to the fixture sandbox, so no probed child is pointed at the
        shared system temp dir.  The sandbox holds a minimal
        `project-forge.yaml`, which is also what makes mcp-forge answer.
    """
    argv = [sys.executable, "-B", path] + list(args)
    for idx, token in enumerate(argv[:-1]):
        if token == "--project-root":
            argv[idx + 1] = sandbox
    CHILD_ARGV.append(list(argv))
    return argv


def measure_server(cfg, sandbox):
    """Spawn one server, hand-shake, measure `tools/list`, then probe once.

    Never raises.  A missing file, an immediate death, a closed stdin or a
    server that simply never answers is recorded as a REASON on the returned
    Footprint, and the caller carries on to the next server.
    """
    foot = Footprint(cfg["file"], cfg.get("tool"), cfg.get("registered"))
    path = os.path.join(SCRIPTS_DIR, cfg["file"])
    if not os.path.isfile(path):
        foot.error = "file missing: Scripts/%s" % cfg["file"]
        return foot

    os.makedirs(sandbox, exist_ok=True)
    argv = probe_argv(path, cfg["args"], sandbox)
    client = None
    try:
        try:
            client = Probe(argv, tool=cfg.get("tool"), cwd=sandbox)
        except OSError as exc:
            foot.error = "spawn failed: %s" % exc
            return foot

        try:
            client.handshake()
        except (BrokenPipeError, ValueError) as exc:
            foot.error = "%s writing initialize: %s" % (type(exc).__name__,
                                                        client.death_reason())
            return foot
        except H.JsonRpcError as exc:
            foot.error = "no initialize answer: %s" % str(exc)[:STDERR_TAIL]
            return foot

        try:
            response = client.rpc("tools/list", {})
        except (BrokenPipeError, ValueError):
            foot.error = "stdin gone before tools/list: %s" % client.death_reason()
            return foot
        except H.JsonRpcError as exc:
            foot.error = "no tools/list answer: %s" % str(exc)[:STDERR_TAIL]
            return foot

        if "result" not in response:
            reason = (response.get("error") or {}).get("message",
                                                       "no result field")
            foot.error = "tools/list error: %s" % str(reason)[:160]
            return foot

        tools = response["result"].get("tools") or []
        foot.n_tools = len(tools)
        foot.descriptions = [t.get("description") or "" for t in tools]
        foot.desc_chars = sum(len(d) for d in foot.descriptions)
        # The whole wire payload, minified: this is what actually travels.
        foot.wire_chars = len(json.dumps({"tools": tools},
                                         separators=(",", ":")))

        # One extra call, on the same child: the unknown-function reply.  See
        # group E for exactly what this number is and is not.
        try:
            _iserr, text = client.call_tool(PROBE_FUNCTION)
            foot.probe_chars = len(text)
        except Exception:                                     # noqa: BLE001
            foot.probe_chars = None
        return foot
    except Exception as exc:                                  # noqa: BLE001
        # Last-resort net: one server must never kill the round.
        foot.error = "probe error: %s: %s" % (type(exc).__name__, exc)
        return foot
    finally:
        if client is not None:
            client.close()


# ---------------------------------------------------------------------------
# subgroup 2: the result ceiling, structurally
# ---------------------------------------------------------------------------

# A per-call output ceiling: the names that bound how many CHARS/BYTES/LINES a
# reply may carry.  These drive the verdict.
CAP_PARAM_RX = re.compile(r"^max_[a-z_]*(chars|bytes|lines|len|length)$")

# Pagination knobs.  Related, reported, but NOT a ceiling: they bound one page,
# not the reply, and a caller who does not pass them gets everything.
PAGINATION_PARAMS = {"head_limit", "offset", "max_rows", "start_line"}

# Hardcoded ceilings: module- or function-level constants.  The tail must name
# an OUTPUT unit, so input guards (`_MAX_REGEX_LEN`, `_HASH_MAX_MB`,
# `_VALIDATE_MAX_MB`) and transport limits (`_LSP_MAX_MESSAGE`) are not
# mislabelled as output caps.
CAP_CONST_RX = re.compile(
    r"^_?(DEFAULT_)?_?MAX_[A-Z0-9_]*(CHARS|BYTES|LINES|ROWS|HTML|LOG|OUTPUT)$")

# A cap default below this is a sentinel or a tiny page size, not a plausible
# character ceiling; used only when inferring a default from the enclosing
# function (the mcp-wiki shape).
INFER_FLOOR = 1000

# Verdicts
CAP_PARAM = "PARAM"        # per-call overridable, with a default
CAP_CONST = "CONST-ONLY"   # hardcoded; a caller cannot raise or lower it
CAP_NONE = "NONE"          # no output ceiling of any kind


def _render(node, limit=40):
    """Source text of an AST node, for the report only."""
    try:
        text = ast.unparse(node)
    except Exception:                                         # noqa: BLE001
        return "<unrenderable>"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _int_constants(tree):
    """name -> int, for every assignment in the file that resolves to an int.

    Two passes, so `A = 1024` / `B = 50 * A` both resolve.  Deliberately not
    scope-aware: a cap constant is either module-level or function-local and
    unique either way in this fleet, and a wrong-scope collision would only
    change a REPORTED number, never a gate.
    """
    consts = {}
    for _ in range(2):
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            value = _int_value(node.value, consts)
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = value
    return consts


def _int_value(node, consts):
    """Best-effort int value of an expression, or None.  No eval, no exec."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, int) \
            and not isinstance(node.value, bool) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _int_value(node.operand, consts)
        return None if inner is None else -inner
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp):
        left = _int_value(node.left, consts)
        right = _int_value(node.right, consts)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
    return None


def _docstring_ids(tree):
    """id() of every docstring Constant -- prose ABOUT a cap is not a cap."""
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def harvest_strings(tree):
    """[(lineno, text)] for every EMITTED string literal in a parsed file.

    Plain constants plus f-string TEMPLATES (`{}` where a value is
    interpolated), because a truncation marker is almost always an f-string and
    its static skeleton is exactly what has to be matched.  Docstrings are
    excluded, and an f-string's internal constants are not double-counted.
    """
    docs = _docstring_ids(tree)
    consumed = set()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                consumed.add(id(piece))
                parts.append(piece.value)
                continue
            parts.append("{}")
            for sub in ast.walk(piece):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    consumed.add(id(sub))
        out.append((node.lineno, "".join(parts)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs and id(node) not in consumed):
            out.append((node.lineno, node.value))
    return sorted(out, key=lambda row: (row[0], row[1][:40]))


def _functions(tree):
    """[(start, end, node)] for every function, innermost resolvable by start."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.lineno, getattr(node, "end_lineno", node.lineno),
                        node))
    return out


def _enclosing(functions, lineno):
    """The innermost function containing `lineno`, or None."""
    best = None
    for start, end, node in functions:
        if start <= lineno <= end and (best is None or start > best[0]):
            best = (start, node)
    return best[1] if best else None


class CapSite:
    """One `<obj>.get("<cap param>", <default>)` call site."""

    def __init__(self, path, lineno, name, receiver, expr, value, how):
        self.path = path
        self.lineno = lineno
        self.name = name
        self.receiver = receiver
        self.expr = expr          # rendered default expression
        self.value = value        # resolved int, or None
        self.how = how            # "explicit" | "inferred" | "unknown"

    @property
    def where(self):
        return "%s:%d" % (self.path, self.lineno)

    def label(self):
        if self.value is None:
            return "%s (unresolved: %s)" % (self.expr or "no default arg",
                                            self.how)
        suffix = "" if self.how == "explicit" else " (%s)" % self.how
        return "%d%s" % (self.value, suffix)


class CapReport:
    """Everything structurally known about one server's output ceiling."""

    def __init__(self, file, registered):
        self.file = file
        self.registered = bool(registered)
        self.parse_error = ""
        self.cap_sites = []       # [CapSite]  ceiling params
        self.page_sites = []      # [CapSite]  pagination knobs
        self.constants = []       # [(lineno, name, value)]
        self.aliases = []         # [(lineno, alias, canonical)]
        self.markers = []         # [(lineno, text)]  truncation notices emitted
        self.v1_trunc = []        # [(lineno, text)]  v1 char marker
        self.v1_rows = []         # [(lineno, text)]  v1 row marker

    # -- derived ----------------------------------------------------------

    @property
    def verdict(self):
        if self.cap_sites:
            return CAP_PARAM
        if self.constants:
            return CAP_CONST
        return CAP_NONE

    @property
    def cap_names(self):
        names = []
        for site in self.cap_sites:
            if site.name not in names:
                names.append(site.name)
        return names

    def defaults_of(self, name):
        """Ordered distinct default labels recorded for one cap param."""
        out = []
        for site in self.cap_sites:
            if site.name == name and site.label() not in out:
                out.append(site.label())
        return out

    @property
    def primary(self):
        """The cap param name to judge against v1: the canonical one if it is
        present at all, else the most frequent, else '-'."""
        if V1_PARAM in self.cap_names:
            return V1_PARAM
        if not self.cap_names:
            return "-"
        counts = {n: sum(1 for s in self.cap_sites if s.name == n)
                  for n in self.cap_names}
        return max(self.cap_names, key=lambda n: (counts[n], -len(n)))

    @property
    def v1_name_ok(self):
        return V1_PARAM in self.cap_names

    @property
    def v1_default_ok(self):
        return any(site.name == V1_PARAM and site.value == V1_DEFAULT
                   for site in self.cap_sites)

    @property
    def v1_marker_ok(self):
        return bool(self.v1_trunc)

    @property
    def v1_gaps(self):
        """Which of the three REQUIRED v1 criteria are unmet."""
        if self.verdict == CAP_NONE:
            return ["no output ceiling at all"]
        gaps = []
        if not self.v1_name_ok:
            gaps.append("param name (has %s)"
                        % (", ".join(self.cap_names) or "no per-call param"))
        if not self.v1_default_ok:
            gaps.append("default 24000 (has %s)"
                        % (", ".join(self.defaults_of(V1_PARAM))
                           if self.v1_name_ok else "n/a"))
        if not self.v1_marker_ok:
            gaps.append("truncation line")
        return gaps

    @property
    def v1_ok(self):
        return not self.v1_gaps

    def row(self, width=18):
        return "%-*s %-6s %-10s %-20s %-22s %-4s %s" % (
            width, self.file, "reg" if self.registered else "INERT",
            self.verdict, self.primary,
            ", ".join(self.defaults_of(self.primary))[:22] or "-",
            "YES" if self.v1_ok else "no",
            "rows:%s consts:%d aliases:%d markers:%d"
            % ("yes" if self.v1_rows else "no", len(self.constants),
               len(self.aliases), len(self.markers)))


def analyse_caps(source, path, registered=True):
    """Structural ceiling report for one server's source text.

    An unparseable file is REPORTED, never skipped: a detector that shrugs at
    files it cannot read is how one goes blind while staying green.
    """
    report = CapReport(path, registered)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        report.parse_error = "SyntaxError line %s: %s" % (exc.lineno, exc.msg)
        return report

    consts = _int_constants(tree)
    functions = _functions(tree)

    for node in ast.walk(tree):
        # -- constants -----------------------------------------------------
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and CAP_CONST_RX.match(target.id):
                report.constants.append(
                    (node.lineno, target.id,
                     _int_value(node.value, consts)))

        # -- alias tables --------------------------------------------------
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant)
                        and isinstance(key.value, str)):
                    continue
                if not (isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    continue
                if (CAP_PARAM_RX.match(value.value)
                        and key.value != value.value):
                    report.aliases.append((getattr(key, "lineno", node.lineno),
                                           key.value, value.value))

        # -- `<obj>.get("<param>", <default>)` -----------------------------
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if not node.args:
            continue
        key = node.args[0]
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        name = key.value
        is_cap = bool(CAP_PARAM_RX.match(name))
        if not is_cap and name not in PAGINATION_PARAMS:
            continue

        receiver = _render(func.value, 18)
        if len(node.args) >= 2:
            site = CapSite(path, node.lineno, name, receiver,
                           _render(node.args[1]),
                           _int_value(node.args[1], consts), "explicit")
        else:
            # The mcp-wiki shape: no default argument, the fallback literal
            # lives in the enclosing function.  INFERRED, and labelled so.
            owner = _enclosing(functions, node.lineno)
            candidates = sorted({
                sub.value for sub in ast.walk(owner)
                if isinstance(sub, ast.Constant)
                and isinstance(sub.value, int)
                and not isinstance(sub.value, bool)
                and sub.value >= INFER_FLOOR
            }) if owner is not None else []
            site = CapSite(
                path, node.lineno, name, receiver,
                "no default arg; enclosing %s()" % (owner.name if owner
                                                    else "<module>"),
                candidates[0] if len(candidates) == 1 else None,
                "inferred" if len(candidates) == 1 else "unknown")
        (report.cap_sites if is_cap else report.page_sites).append(site)

    # -- truncation markers ------------------------------------------------
    for lineno, text in harvest_strings(tree):
        if v1_truncation_marker(text):
            report.v1_trunc.append((lineno, text))
        if v1_row_marker(text):
            report.v1_rows.append((lineno, text))
        # Heuristic, and labelled as one wherever it is printed: a SHORT
        # non-docstring literal that says "truncat" AND contains whitespace is
        # a notice a handler emits.  The length bound keeps long advertising
        # prose out (a tool description mentioning truncation); the whitespace
        # requirement keeps IDENTIFIERS out -- without it
        # `Scripts/mcp-jenkins.py:1312`'s `casesTruncated`, a field name in
        # Jenkins' own JSON, was counted as a truncation notice.
        if ("truncat" in text.lower() and len(text) <= 200
                and any(ch.isspace() for ch in text)):
            report.markers.append((lineno, text))
    return report


# ---------------------------------------------------------------------------
# subgroup 3: boilerplate -- what ships regardless of content
# ---------------------------------------------------------------------------

# (kind, regex, what it costs).  Applied to non-docstring string literals of at
# most BOILERPLATE_MAX chars, with the server's own tool-description text
# subtracted first (see boilerplate_census).
BOILERPLATE_KINDS = [
    ("heading", re.compile(r"(?:^|\\n|\n)#{1,6} "),
     "a markdown heading plus its blank line, on every reply"),
    ("fence", re.compile(r"```"),
     "a fence PAIR: 6 chars plus two newlines minimum"),
    ("cmd-echo", re.compile(r"\(exit |exit code|\*\*Cmd\*\*"),
     "the command line and/or its exit status, echoed back"),
    ("filter-stat", re.compile(r"\*\*Filter|no lines matched|-> \{\} matches"),
     "filter bookkeeping the caller already knows"),
    ("empty-note", re.compile(r"_\(no|_\(none\)_|\(no output\)|\(no result\)"
                              r"|\(none\)|^No [a-z]|is empty\."),
     "prose for the empty case where zero bytes would do"),
    ("pagination", re.compile(r"showing (?:lines|rows|first)|for more"
                              r"|of \{\} "),
     "a page recap line"),
    # A rule line is nothing BUT rule characters.  Anchoring both ends (plus
    # `-+-`, which is a real table rule in mcp-postgres) is what keeps
    # `Scripts/mcp-jenkins.py:103`'s secret-redaction marker `*** (len={})` from
    # being counted as a horizontal rule.
    ("separator", re.compile(r"(?m)^[-=*]{3,}[ \t]*$|-\+-"),
     "a rule line"),
    ("bold-label", re.compile(r"\*\*[A-Za-z][A-Za-z ]{0,20}\*\*\s*:"),
     "**Label**: -- four asterisks per field"),
]

BOILERPLATE_MAX = 200

# Markup a server BUILDS by repetition rather than writing out, so a literal
# census cannot see it.  `mcp-git.py` and `mcp-inspect.py` both fence every
# reply, yet neither file contains the string ``` -- their `_md_fence` helpers
# compute the fence width and emit "`" * width.  Counting those as fence=0 would
# have understated the two servers the inventory calls the heaviest fencers, so
# the repetition itself is detected and reported under its own kind.
BUILT_MARKUP = {"`": "fence", "-": "rule", "=": "rule", "*": "rule",
                "#": "heading"}

# A literal is only subtracted as tool-description prose when it is at least
# this long.  Without the floor, every short word that happens to occur inside
# a long description ("path", "error") is dropped too: mcp-purity lost 700
# literals that way, most of which were never description prose at all.
DESC_MIN_LEN = 12


class Boilerplate:
    """The fixed per-call payload census for one server source file."""

    def __init__(self, file, registered):
        self.file = file
        self.registered = bool(registered)
        self.parse_error = ""
        self.hits = {}            # kind -> [(lineno, text)]
        self.literals = 0         # literals actually classified
        self.desc_excluded = 0    # literals recognised as description prose
        self.desc_known = False   # was a live description blob available?

    def count(self, kind):
        return len(self.hits.get(kind, []))

    @property
    def total(self):
        return sum(len(v) for v in self.hits.values())

    def row(self, width=18):
        cells = " ".join("%s=%d" % (kind, self.count(kind))
                         for kind, _rx, _why in BOILERPLATE_KINDS)
        return "%-*s %-6s %4d  %s built=%d" % (
            width, self.file, "reg" if self.registered else "INERT",
            self.total, cells, self.count("built"))


def boilerplate_census(source, path, desc_blob, registered=True):
    """Classify the short literals one server emits into boilerplate kinds.

    Description prose is subtracted EXACTLY rather than guessed at: any literal
    that is a substring of the server's own live `tools/list` description text
    is advertising, not output, and is dropped.  That is the same measurement
    group A already made, reused as a filter -- when the probe failed there is
    no blob and the census says so instead of pretending.
    """
    census = Boilerplate(path, registered)
    census.desc_known = bool(desc_blob)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        census.parse_error = "SyntaxError line %s: %s" % (exc.lineno, exc.msg)
        return census

    for lineno, text in harvest_strings(tree):
        if not text.strip() or len(text) > BOILERPLATE_MAX:
            continue
        stripped = text.strip()
        if (desc_blob and len(stripped) >= DESC_MIN_LEN
                and stripped in desc_blob):
            census.desc_excluded += 1
            continue
        census.literals += 1
        for kind, regex, _why in BOILERPLATE_KINDS:
            if regex.search(text):
                census.hits.setdefault(kind, []).append((lineno, text))

    # Markup built by repetition: `"`" * width`, `"-" * (w + 2)`.  Invisible to
    # the literal census above, and it is exactly how the two heaviest fencers
    # in the fleet emit their fences.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
            continue
        for side in (node.left, node.right):
            if not (isinstance(side, ast.Constant)
                    and isinstance(side.value, str)):
                continue
            unit = side.value
            if len(unit) > 3 or not unit or unit[0] not in BUILT_MARKUP:
                continue
            if any(ch not in BUILT_MARKUP for ch in unit):
                continue
            census.hits.setdefault("built", []).append(
                (node.lineno, "%s built by repetition: %s"
                 % (BUILT_MARKUP[unit[0]], _render(node))))
    return census


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def load_registration():
    """(name -> [launch script basenames], "") or (None, reason).

    Only the `mcpServers` names and their argv are read -- the file is ~100 KB
    of unrelated session state.  Anything unexpected returns a REASON, and the
    caller degrades to SKIP.  This function never guesses.
    """
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:                                  # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)
    if not isinstance(data, dict):
        return None, "top level of %s is %s, not an object" % (
            CLAUDE_JSON, type(data).__name__)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None, "no top-level 'mcpServers' mapping in %s" % CLAUDE_JSON
    out = {}
    for name, cfg in servers.items():
        argv = []
        if isinstance(cfg, dict):
            if isinstance(cfg.get("command"), str):
                argv.append(cfg["command"])
            argv += [a for a in (cfg.get("args") or []) if isinstance(a, str)]
        out[name] = [os.path.basename(a) for a in argv if a.endswith(".py")]
    return out, ""


# ---------------------------------------------------------------------------
# negative control fixtures for the ceiling detector
# ---------------------------------------------------------------------------

_V1_LINE = ('f"\\n[truncated: kept {n} of {total} chars from the head; '
            'raise max_answer_chars or narrow the query]"')
_V1_ROWS = ('f"\\n[showing rows {a}-{b} of {total}; offset={b} for more]"')

# name -> (source, expectation dict).  The expectation names only what this
# suite claims to detect, and is compared EXACTLY.
FIXTURES = {
    # -- must be recognised as v1-conformant ------------------------------
    "conforming.py": (
        "def handle(params, body):\n"
        "    limit = params.get('max_answer_chars', 24000)\n"
        "    n, total = limit, len(body)\n"
        "    if total > limit:\n"
        "        body = body[:limit] + " + _V1_LINE + "\n"
        "    return body\n",
        {"verdict": CAP_PARAM, "primary": "max_answer_chars",
         "defaults": ["24000"], "v1": True, "rows": False},
    ),
    "conforming_rows.py": (
        "def handle(params, rows):\n"
        "    limit = params.get('max_answer_chars', 24000)\n"
        "    a, b, total, n = 0, 50, len(rows), limit\n"
        "    head = " + _V1_ROWS + "\n"
        "    body = ''.join(rows) + " + _V1_LINE + "\n"
        "    return head + body\n",
        {"verdict": CAP_PARAM, "primary": "max_answer_chars",
         "defaults": ["24000"], "v1": True, "rows": True},
    ),
    # -- capped, but not to the convention --------------------------------
    "wrong_name.py": (
        "def handle(params, body):\n"
        "    limit = params.get('max_output_chars', 24000)\n"
        "    n, total = limit, len(body)\n"
        "    return body[:limit] + " + _V1_LINE + "\n",
        {"verdict": CAP_PARAM, "primary": "max_output_chars",
         "defaults": ["24000"], "v1": False, "rows": False},
    ),
    "wrong_default.py": (
        "def handle(params, body):\n"
        "    limit = params.get('max_answer_chars', 500000)\n"
        "    n, total = limit, len(body)\n"
        "    return body[:limit] + " + _V1_LINE + "\n",
        {"verdict": CAP_PARAM, "primary": "max_answer_chars",
         "defaults": ["500000"], "v1": False, "rows": False},
    ),
    "no_marker.py": (
        "def handle(params, body):\n"
        "    limit = params.get('max_answer_chars', 24000)\n"
        "    return body[:limit] + '\\n... (truncated at %d chars)' % limit\n",
        {"verdict": CAP_PARAM, "primary": "max_answer_chars",
         "defaults": ["24000"], "v1": False, "rows": False},
    ),
    # -- a constant is not a per-call ceiling -----------------------------
    "const_only.py": (
        "MAX_OUTPUT_BYTES = 50 * 1024 * 1024\n"
        "def handle(params, body):\n"
        "    if len(body) > MAX_OUTPUT_BYTES:\n"
        "        body = body[:MAX_OUTPUT_BYTES] + '... (output truncated) ...'\n"
        "    return body\n",
        {"verdict": CAP_CONST, "primary": "-", "defaults": [],
         "v1": False, "rows": False},
    ),
    "uncapped.py": (
        "def handle(params, body):\n"
        "    return '## result\\n\\n' + body\n",
        {"verdict": CAP_NONE, "primary": "-", "defaults": [],
         "v1": False, "rows": False},
    ),
    # -- the mcp-wiki shape: default one line below the .get() ------------
    "inferred_default.py": (
        "def handle(params, body):\n"
        "    raw = params.get('max_answer_chars')\n"
        "    limit = int(raw) if raw is not None else 100000\n"
        "    return body[:limit]\n",
        {"verdict": CAP_PARAM, "primary": "max_answer_chars",
         "defaults": ["100000 (inferred)"], "v1": False, "rows": False},
    ),
    # -- bait: must NOT be counted as a ceiling ---------------------------
    "bait_description.py": (
        # The two REAL false positives in this repo, side by side: a tool
        # description advertising the param, and a docstring describing the
        # behaviour.  Neither is code, and there is no ceiling in this file.
        "def tool_schema():\n"
        "    return {'description': (\n"
        "        'query(sql, max_answer_chars) -- max_answer_chars '\n"
        "        '(default 100000), timeout (default 60s). Markdown output.'\n"
        "    )}\n"
        "\n"
        "def handle(params, body):\n"
        "    '''Wrap markdown for return, truncating at max_answer_chars\n"
        "    (default 100k). Emits [truncated: kept n of total chars from the\n"
        "    head; raise max_answer_chars or narrow the query].'''\n"
        "    return body\n",
        {"verdict": CAP_NONE, "primary": "-", "defaults": [],
         "v1": False, "rows": False},
    ),
    "bait_not_a_get.py": (
        # A dict literal and a subscript mention the param without any
        # `.get()`, so neither may register as a call site.  The dict is also
        # NOT an alias table (its value is an int, not a canonical name).
        "CONFIG = {'max_answer_chars': 24000}\n"
        "def handle(params, body):\n"
        "    limit = params['max_answer_chars']\n"
        "    return body[:limit]\n",
        {"verdict": CAP_NONE, "primary": "-", "defaults": [],
         "v1": False, "rows": False},
    ),
    # -- an alias table IS recognised, and is not itself a ceiling --------
    "alias_table.py": (
        "ALIASES = {'max_chars': 'max_answer_chars',\n"
        "           'max_output_chars': 'max_answer_chars'}\n"
        "def handle(params, body):\n"
        "    return body\n",
        {"verdict": CAP_NONE, "primary": "-", "defaults": [],
         "v1": False, "rows": False, "aliases": 2},
    ),
}

# Deliberately invalid Python: an unparseable file must be REPORTED, not
# skipped.  Kept out of FIXTURES because it produces an error, not a verdict.
BROKEN_FIXTURE = ("broken_syntax.py",
                  "def handle(params):\n"
                  "    return params.get('max_answer_chars', 24000\n")


def write_fixtures(root):
    for name, (source, _expect) in sorted(FIXTURES.items()):
        write_text(os.path.join(root, name), source)
    write_text(os.path.join(root, BROKEN_FIXTURE[0]), BROKEN_FIXTURE[1])
    return root


def observed_expectation(report):
    """The same dict shape FIXTURES declares, read back off a CapReport."""
    return {"verdict": report.verdict, "primary": report.primary,
            "defaults": report.defaults_of(report.primary)
            if report.primary != "-" else [],
            "v1": report.v1_ok, "rows": bool(report.v1_rows)}


# ---------------------------------------------------------------------------
# the v1 rule only RUNTIME can answer: truncation on a line boundary
# ---------------------------------------------------------------------------
#
# Groups C/D read source text, so the one v1 rule they cannot judge is what the
# cut actually LANDS on.  This subgroup drives purity's own `_cap_text` and its
# search handler in-process, on synthetic payloads, and asserts the rule from the
# outside: whole rows in the ordinary case, and -- the case that produced a real
# zero-payload reply in this repo -- an in-line cut that still yields an ANCHOR
# when not one row fits.
#
# Why GATED while every other server finding here is INFO: this is not fleet
# conformance pending a decision, it is a regression guard on a defect that has
# been fixed.  Same footing as group D (the detector must discriminate) and
# group G (hygiene) -- suite/server integrity, not a verdict on the fleet.

PURITY_FILE = "mcp-purity.py"

# Small enough to keep fixtures readable, large enough that a `path:line:` anchor
# and some payload fit under it.
CAP_CEILING = 900

# The phrase the closing line MUST carry when the cut went inside a row, and must
# NOT carry otherwise.  Written as a literal here for the same reason purity
# writes it as a literal there: an interpolated phrase is invisible to
# harvest_strings, and this suite's marker detector would then read zero.
V1_INLINE_WORDS = "cut INSIDE a line"

# `14+ match(es)` / `[... of 14+ (scan stopped ...)]` -- the count and its
# lower-bound marker, at the two places one reply states it.
HEADER_COUNT_RX = re.compile(r"(\d+)(\+?)")
NOTE_TOTAL_RX = re.compile(r" of (\d+)(\+?)")

# 13 short hits then two rows no ceiling can hold: the exact shape of the live
# `search_for_pattern("_wikilib", offset=13)` call that answered with a header and
# nothing else.  `offset=13` is the resume hint the previous reply itself handed
# back, which is what made the empty answer a dead end rather than an
# inconvenience.
CAP_NEEDLE = "_ph_capfix_needle"
CAP_LONG_ROW = 30_000
CAP_SHORT_HITS = 13


def cap_fixture_body():
    rows = ["%s short hit %d" % (CAP_NEEDLE, i) for i in range(CAP_SHORT_HITS)]
    rows.append("%s %s" % (CAP_NEEDLE, "Z" * CAP_LONG_ROW))
    rows.append("%s %s" % (CAP_NEEDLE, "Q" * CAP_LONG_ROW))
    return "\n".join(rows) + "\n"


def cap_probe(mod, text, ceiling=CAP_CEILING):
    """(reply, payload lines, closing line) for one _cap_text call."""
    reply = mod._cap_text(text, ceiling)
    lines = reply.split("\n")
    return reply, lines[:-1], lines[-1]


def count_claims(reply):
    """([(count, '+')] from the header, (total, '+') from the closing line).

    A header may state two counts (`N match(es) in M file(s)`), so the caller
    says which one the closing line's total is about.
    """
    lines = reply.split("\n")
    note = NOTE_TOTAL_RX.search(lines[-1])
    return HEADER_COUNT_RX.findall(lines[0]), (note.groups() if note else None)


# ---------------------------------------------------------------------------
# rendering helpers
# ---------------------------------------------------------------------------

EST_NOTE = ("~tokens is an ESTIMATE: chars / 4, no tokenizer was run")


def footprint_table(feet):
    width = max([len(f.file) for f in feet] + [len("server")])
    head = "%-*s %5s %11s %8s %11s %8s  %s" % (
        width, "server", "tools", "desc chars", "~tokens", "wire chars",
        "~tokens", "note")
    rows = [head, "-" * len(head)]
    rows += [f.row(width) for f in feet]
    good = [f for f in feet if f.ok]
    desc = sum(f.desc_chars for f in good)
    wire = sum(f.wire_chars for f in good)
    rows.append("-" * len(head))
    rows.append("%-*s %5d %11d %8d %11d %8d  %d of %d answered" % (
        width, "TOTAL", sum(f.n_tools for f in good), desc, tok(desc),
        wire, tok(wire), len(good), len(feet)))
    rows.append(EST_NOTE)
    return rows


def cap_table(reports):
    width = max([len(r.file) for r in reports] + [len("server")])
    head = "%-*s %-6s %-10s %-20s %-22s %-4s %s" % (
        width, "server", "scope", "ceiling", "primary param", "default(s)",
        "v1", "extras")
    return [head, "-" * len(head)] + [r.row(width) for r in reports]


def boilerplate_table(census_rows):
    width = max([len(c.file) for c in census_rows] + [len("server")])
    head = "%-*s %-6s %4s  %s" % (width, "server", "scope", "hits",
                                  "per-kind counts")
    return [head, "-" * len(head)] + [c.row(width) for c in census_rows]


def exemplars(pairs, path, limit=3):
    """Up to `limit` `file:line -- text` evidence lines."""
    out = []
    for lineno, text in pairs[:limit]:
        flat = " ".join(text.split())
        out.append("  %s:%d  %s" % (path, lineno,
                                    flat if len(flat) <= 78
                                    else flat[:75] + "..."))
    if len(pairs) > limit:
        out.append("  ... and %d more" % (len(pairs) - limit))
    return out


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def group_description_tax(suite, feet, group, scope):
    """A/B: one INFO case per server, then the summed total for that scope."""
    for foot in feet:
        detail = ["server      : Scripts/%s" % foot.file,
                  "dispatcher  : %s" % (foot.tool or "?"),
                  "scope       : %s" % scope]
        if foot.error:
            detail.append("result      : NO MEASUREMENT -- %s" % foot.error)
            detail.append("note        : recorded with a reason; the round "
                          "continues (one server may not stall the fleet)")
        else:
            detail += [
                "tools       : %d" % foot.n_tools,
                "desc chars  : %d  (~%d tokens, estimated)"
                % (foot.desc_chars, tok(foot.desc_chars)),
                "wire chars  : %d  (~%d tokens, estimated) -- the whole "
                "minified tools/list payload"
                % (foot.wire_chars, tok(foot.wire_chars)),
                "per request : this is resent on EVERY request while the "
                "server is connected",
            ]
        suite.record(group, "desc-tax %s" % foot.file, [], status=H.INFO,
                     detail=detail,
                     brief="INFO | %s | %s" % (
                         foot.file,
                         foot.error or "desc=%d (~%dtok) wire=%d (~%dtok)"
                         % (foot.desc_chars, tok(foot.desc_chars),
                            foot.wire_chars, tok(foot.wire_chars))))

    good = [f for f in feet if f.ok]
    failed = [f for f in feet if not f.ok]
    desc = sum(f.desc_chars for f in good)
    wire = sum(f.wire_chars for f in good)
    suite.record(group, "TOTAL (%s)" % scope, [], status=H.INFO,
                 detail=footprint_table(feet)
                 + ["", "servers     : %d measured, %d without a measurement"
                    % (len(good), len(failed))]
                 + (["unmeasured  : %s" % "; ".join(
                     "%s (%s)" % (f.file, f.error) for f in failed)]
                    if failed else [])
                 + (["footprint   : ZERO while unregistered -- these servers "
                     "are never started, so nothing above is currently paid"]
                    if scope == "INERT" else
                    ["footprint   : ~%d tokens of description and ~%d tokens "
                     "of wire payload, on EVERY request" % (tok(desc),
                                                            tok(wire))]),
                 brief="INFO | TOTAL %s | desc=%d (~%dtok) wire=%d (~%dtok)"
                       % (scope, desc, tok(desc), wire, tok(wire)))
    return good


def group_probe_floor(suite, answering, total):
    """A: the prober is not blind.  A FLOOR, not a count -- suite integrity."""
    problems = []
    if answering < MIN_ANSWERING:
        problems.append(
            "only %d of %d registered servers answered tools/list (floor %d): "
            "the PROBER is probably broken, not the fleet -- a real fleet "
            "outage would not take the handshake with it"
            % (answering, total, MIN_ANSWERING))
    suite.record(GA, "prober is not blind (floor, not a count)", problems,
                 detail=["answered    : %d of %d registered" % (answering,
                                                                total),
                         "floor       : %d" % MIN_ANSWERING,
                         "note        : deliberately far below the live "
                         "number. Retiring a server moves the real count "
                         "down and is printed; a broken prober trips this",
                         "gated       : YES -- this is suite integrity, not a "
                         "finding about a server. Findings are INFO"])


def group_ceilings(suite, reports):
    """C: one INFO case per server source, then the fleet table."""
    for report in reports:
        detail = ["server      : Scripts/%s" % report.file,
                  "scope       : %s" % ("registered" if report.registered
                                        else "INERT (unregistered)")]
        if report.parse_error:
            detail.append("result      : NOT ANALYSED -- %s"
                          % report.parse_error)
            suite.record(GC, "ceiling %s" % report.file, [], status=H.INFO,
                         detail=detail,
                         brief="INFO | %s | unparseable" % report.file)
            continue

        detail.append("ceiling     : %s" % report.verdict)
        if report.cap_sites:
            for name in report.cap_names:
                sites = [s for s in report.cap_sites if s.name == name]
                detail.append("  param     : %s -- default(s) %s  [%d site(s), "
                              "e.g. %s]"
                              % (name, ", ".join(report.defaults_of(name)),
                                 len(sites), sites[0].where))
        if report.constants:
            detail.append("  constants : %s"
                          % ", ".join("%s=%s @%s:%d" % (n, v, report.file, ln)
                                      for ln, n, v in report.constants))
        if report.aliases:
            detail.append("  aliases   : %s"
                          % ", ".join("%s->%s" % (a, c)
                                      for _ln, a, c in report.aliases))
        if report.page_sites:
            names = []
            for site in report.page_sites:
                if site.name not in names:
                    names.append(site.name)
            detail.append("  pagination: %s -- bounds ONE PAGE, not the reply"
                          % ", ".join(names))
        detail.append("v1 verdict  : %s" % ("CONFORMS" if report.v1_ok
                                            else "does not conform"))
        for gap in report.v1_gaps:
            detail.append("  gap       : %s" % gap)
        detail.append("v1 row form : %s" % ("present" if report.v1_rows
                                            else "absent"))
        if report.markers:
            detail.append("markers now : %d truncation notice(s) emitted "
                          "(heuristic: short non-docstring literal saying "
                          "'truncat')" % len(report.markers))
            detail += exemplars(report.markers, report.file)
        else:
            detail.append("markers now : none -- nothing tells the caller the "
                          "reply was cut")
        suite.record(GC, "ceiling %s" % report.file, [], status=H.INFO,
                     detail=detail,
                     brief="INFO | %s | %s | %s | v1=%s"
                           % (report.file, report.verdict, report.primary,
                              "YES" if report.v1_ok else "no"))

    live = [r for r in reports if r.registered]
    conforming = [r for r in live if r.v1_ok]
    uncapped = [r for r in live if r.verdict == CAP_NONE]
    const_only = [r for r in live if r.verdict == CAP_CONST]
    suite.record(GC, "fleet ceiling table", [], status=H.INFO,
                 detail=cap_table(reports)
                 + ["",
                    "v1 convention : param %r, default %d, one closing line "
                    "'%s...%s'" % (V1_PARAM, V1_DEFAULT, V1_TRUNC_OPEN,
                                   V1_TRUNC_CLOSE),
                    "registered    : %d" % len(live),
                    "conforming    : %d -- %s"
                    % (len(conforming),
                       ", ".join(r.file for r in conforming) or "none"),
                    "no ceiling    : %d -- %s"
                    % (len(uncapped),
                       ", ".join(r.file for r in uncapped) or "none"),
                    "const only    : %d -- %s (a caller cannot raise or lower "
                    "these per call)"
                    % (len(const_only),
                       ", ".join(r.file for r in const_only) or "none"),
                    "gated         : NO. This round MEASURES conformance. "
                    "Turning it into a gate is a separate work item and would "
                    "paint the whole run red until the server fixes land",
                    "not claimed   : line-boundary truncation, in THIS group. "
                    "The v1 rule that a `file:line` anchor is never cut in half "
                    "is a RUNTIME property; no static reading of the source can "
                    "assert it, so nothing above pretends to. Group H asserts it "
                    "in-process for %s -- one server, gated -- and the other %d "
                    "stay unmeasured on that rule"
                    % (PURITY_FILE, max(0, len(live) - 1))])

    unparsed = [r for r in reports if r.parse_error]
    suite.record(GC, "every server source parsed",
                 [] if not unparsed
                 else ["%d file(s) could not be analysed: %s"
                       % (len(unparsed), "; ".join("%s (%s)" % (r.file,
                                                                r.parse_error)
                                                   for r in unparsed))],
                 detail=["files       : %d" % len(reports),
                         "note        : an unparseable server is a FAILURE, "
                         "not a skip -- shrugging at files it cannot read is "
                         "how a detector goes blind while staying green",
                         "gated       : YES -- suite integrity"])


def group_control(suite, fixture_root):
    """D: planted ceilings the detector MUST classify, and bait it must not."""
    write_fixtures(fixture_root)
    observed = {}
    errors = {}
    for name in sorted(list(FIXTURES) + [BROKEN_FIXTURE[0]]):
        path = os.path.join(fixture_root, name)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        report = analyse_caps(source, name)
        if report.parse_error:
            errors[name] = report.parse_error
        observed[name] = report

    for name, (_source, expected) in sorted(FIXTURES.items()):
        report = observed[name]
        got = observed_expectation(report)
        want = {k: v for k, v in expected.items() if k != "aliases"}
        problems = []
        if report.parse_error:
            problems.append("fixture did not parse: %s" % report.parse_error)
        elif got != want:
            problems.append("detector said %r, expected %r" % (got, want))
        if "aliases" in expected and len(report.aliases) != expected["aliases"]:
            problems.append("aliases %d != expected %d"
                            % (len(report.aliases), expected["aliases"]))
        suite.record(GD, "control-" + name, problems,
                     detail=["fixture     : %s" % name,
                             "expected    : %r" % want,
                             "got         : %r" % got,
                             "aliases     : %d" % len(report.aliases),
                             "markers     : %d" % len(report.markers)],
                     brief="%s | control-%s | %s"
                           % (H.FAIL if problems else H.PASS, name,
                              got["verdict"]))

    # The unparseable fixture must surface as an ERROR and contribute no
    # verdict -- silently skipping it is the blind failure mode.
    problems = []
    if BROKEN_FIXTURE[0] not in errors:
        problems.append("an unparseable file was NOT reported as a parse error")
    broken = observed[BROKEN_FIXTURE[0]]
    if broken.cap_sites or broken.constants:
        problems.append("cap sites were reported from an unparseable file")
    suite.record(GD, "control-unparseable-file-is-reported", problems,
                 detail=["fixture     : %s" % BROKEN_FIXTURE[0],
                         "error       : %s" % errors.get(BROKEN_FIXTURE[0],
                                                         "(none reported)"),
                         "cap sites   : %d" % len(broken.cap_sites)])

    # The control must actually FIRE in both directions: a run in which every
    # fixture came back NONE, or every fixture came back conforming, proves
    # nothing about the live fleet.
    verdicts = {observed_expectation(observed[n])["verdict"]
                for n in FIXTURES}
    conforming = [n for n in FIXTURES if observed[n].v1_ok]
    non_conforming = [n for n in FIXTURES
                      if observed[n].verdict == CAP_PARAM
                      and not observed[n].v1_ok]
    problems = []
    if len(verdicts) < 3:
        problems.append("the detector produced only %r across %d fixtures: it "
                        "is not discriminating between ceiling kinds"
                        % (sorted(verdicts), len(FIXTURES)))
    if not conforming:
        problems.append("no fixture was judged v1-conformant, so a green live "
                        "table would prove nothing")
    if not non_conforming:
        problems.append("no capped fixture was judged non-conformant, so the "
                        "v1 check may be accepting everything")
    suite.record(GD, "control discriminates in both directions", problems,
                 detail=["fixtures    : %d + 1 unparseable" % len(FIXTURES),
                         "verdicts    : %s" % ", ".join(sorted(verdicts)),
                         "v1 pass     : %s" % ", ".join(sorted(conforming)),
                         "v1 fail     : %s" % ", ".join(sorted(non_conforming)),
                         "note        : a detector that always says the same "
                         "thing is indistinguishable from a clean fleet"])

    bait = [n for n in FIXTURES if n.startswith("bait_")]
    stray = ["%s -> %s" % (n, observed[n].verdict) for n in bait
             if observed[n].verdict != CAP_NONE or observed[n].cap_sites]
    suite.record(GD, "bait stays silent (description, docstring, subscript)",
                 [] if not stray else ["bait registered a ceiling: %r" % stray],
                 detail=["bait files  : %s" % ", ".join(sorted(bait)),
                         "note        : these are the REAL text-search false "
                         "positives in this repo -- mcp-git.py:697 and "
                         "mcp-wiki.py:1140 advertise `max_answer_chars "
                         "(default 100000)` in the TOOL DESCRIPTION, and "
                         "mcp-wiki.py:577 describes truncation in a "
                         "DOCSTRING. Neither is a ceiling",
                         "v1 markers  : %d (a docstring quoting the v1 line "
                         "must not count either)"
                         % len(observed["bait_description.py"].v1_trunc)])


def group_boilerplate(suite, census_rows, feet_by_file):
    """E: the fixed per-call payload, plus the one comparable live number."""
    for census in census_rows:
        detail = ["server      : Scripts/%s" % census.file,
                  "scope       : %s" % ("registered" if census.registered
                                        else "INERT (unregistered)")]
        if census.parse_error:
            detail.append("result      : NOT ANALYSED -- %s"
                          % census.parse_error)
        else:
            detail.append("literals    : %d classified, %d dropped as tool-"
                          "description prose%s"
                          % (census.literals, census.desc_excluded,
                             "" if census.desc_known
                             else " (NO live description available, so the "
                                  "subtraction could not be applied)"))
            kinds = list(BOILERPLATE_KINDS) + [
                ("built", None, "markup BUILT by repetition, which a literal "
                                "census cannot see (e.g. _md_fence's "
                                "\"`\" * width)")]
            for kind, _rx, why in kinds:
                hits = census.hits.get(kind)
                if not hits:
                    continue
                detail.append("  %-11s: %d -- %s" % (kind, len(hits), why))
                detail += exemplars(hits, census.file, 2)
        foot = feet_by_file.get(census.file)
        if foot is not None and foot.probe_chars is not None:
            detail.append("min reply   : %d chars for an unknown-function "
                          "call" % foot.probe_chars)
        detail.append("NOT claimed : that any of the above is emitted "
                      "UNCONDITIONALLY. Proving a literal ships on every "
                      "reply needs control-flow analysis this suite does not "
                      "do, so these are counts and file:line evidence, not a "
                      "per-call byte total")
        suite.record(GE, "boilerplate %s" % census.file, [], status=H.INFO,
                     detail=detail,
                     brief="INFO | %s | %d hit(s)" % (census.file,
                                                      census.total))

    live = [c for c in census_rows if c.registered]
    suite.record(GE, "fleet boilerplate census", [], status=H.INFO,
                 detail=boilerplate_table(census_rows)
                 + ["",
                    "method      : AST string-literal census. Docstrings "
                    "excluded, literals over %d chars excluded, and any "
                    "literal of at least %d chars that is a substring of the "
                    "server's own live tools/list description is dropped as "
                    "advertising -- the group A measurement reused as a filter"
                    % (BOILERPLATE_MAX, DESC_MIN_LEN),
                    "registered  : %d of %d files" % (len(live),
                                                      len(census_rows)),
                    "built=N     : markup a server assembles by REPETITION "
                    "instead of writing out. mcp-git and mcp-inspect fence "
                    "every reply and yet contain no ``` literal at all -- "
                    "their _md_fence helpers emit \"`\" * width. A pure "
                    "literal census scores those two fence=0, which is the "
                    "opposite of the truth, so the repetition is counted "
                    "separately rather than quietly missed",
                    "heuristics  : (1) the truncation-marker census in group C "
                    "keys on 'truncat' in a short literal CONTAINING "
                    "WHITESPACE -- the whitespace test is what stops "
                    "identifiers like mcp-jenkins.py:1312's `casesTruncated`, "
                    "a field name in Jenkins' own JSON, from counting as a "
                    "notice; (2) both bounds above are cut-offs, not proofs"])

    measured = [(f.file, f.probe_chars) for f in feet_by_file.values()
                if f.probe_chars is not None]
    measured.sort(key=lambda row: -row[1])
    total = sum(n for _f, n in measured)
    suite.record(GE, "live minimal-reply floor (what it is, exactly)", [],
                 status=H.INFO,
                 detail=["measured    : one `tools/call` per server naming a "
                         "function that does not exist (%r), over the same "
                         "child that answered tools/list" % PROBE_FUNCTION,
                         "servers     : %d answered" % len(measured)]
                 + ["  %-18s %6d chars (~%d tokens)" % (f, n, tok(n))
                    for f, n in measured]
                 + ["  %-18s %6d chars (~%d tokens)" % ("TOTAL", total,
                                                        tok(total)),
                    "",
                    "IS          : a real, uniform, comparable number -- the "
                    "same request shape to every server",
                    "IS NOT      : the boilerplate of a SUCCESSFUL reply. "
                    "Several servers answer an unknown function by listing "
                    "their whole function catalogue, so this number tracks "
                    "catalogue size as much as envelope cost",
                    "why no more : a real-reply floor would need a different "
                    "function per server (forge needs a project, git a repo, "
                    "psql and jenkins a live endpoint, gdc a browser, lldb a "
                    "process, tshark a capture). Numbers from different "
                    "functions are not comparable, and presenting them in one "
                    "column would fabricate comparability",
                    EST_NOTE])


def group_drift(suite, servers, table_flagged):
    """F: the table's `registered` flags vs the live ~/.claude.json."""
    missing = [cfg["file"] for cfg in servers if "registered" not in cfg]
    suite.record(GF, "every SERVERS entry declares `registered`",
                 [] if not missing
                 else ["%d entry(ies) have no `registered` flag: %s -- the "
                       "footprint arithmetic in groups A and B silently "
                       "mis-sums without it"
                       % (len(missing), ", ".join(missing))],
                 detail=["entries     : %d" % len(servers),
                         "registered  : %d" % sum(1 for c in servers
                                                  if c.get("registered")),
                         "inert       : %d" % sum(1 for c in servers
                                                  if not c.get("registered")),
                         "source      : Scripts/_mcp_smoke_test.py SERVERS",
                         "gated       : YES -- suite integrity. The flag "
                         "VALUES are checked below, and a mismatch there is "
                         "INFO this round"])

    registration, reason = load_registration()
    if registration is None:
        suite.record(GF, "~/.claude.json cross-check", [], status=SKIP,
                     detail=["file        : %s" % CLAUDE_JSON,
                             "skipped     : %s" % reason,
                             "note        : SKIP WITH A REASON, never an "
                             "invented answer. The `registered` flags in the "
                             "launch table stand unverified for this run, and "
                             "the group A/B totals are only as right as they "
                             "are"],
                     brief="%s | claude.json cross-check | %s" % (SKIP,
                                                                  reason))
        return

    table_files = {cfg["file"] for cfg in servers}
    live_scripts = set()
    for scripts in registration.values():
        live_scripts.update(scripts)
    live_fleet = {s for s in live_scripts if s in table_files}

    claims_yes = {cfg["file"] for cfg in servers if cfg.get("registered")}
    claims_no = table_files - claims_yes

    false_yes = sorted(claims_yes - live_fleet)
    false_no = sorted(claims_no & live_fleet)
    unknown = sorted(s for s in live_scripts
                     if s not in table_files and s.startswith("mcp-"))

    drift = []
    if false_yes:
        drift.append("flagged registered but ~/.claude.json launches no such "
                     "script: %s" % ", ".join(false_yes))
    if false_no:
        drift.append("flagged INERT but ~/.claude.json does launch it: %s"
                     % ", ".join(false_no))
    if unknown:
        drift.append("~/.claude.json launches mcp-* script(s) absent from the "
                     "launch table: %s" % ", ".join(unknown))

    by_script = {}
    for name, scripts in sorted(registration.items()):
        for script in scripts:
            by_script.setdefault(script, []).append(name)

    detail = ["file        : %s" % CLAUDE_JSON,
              "entries     : %d under `mcpServers`" % len(registration),
              "launching a .py: %d" % sum(1 for v in registration.values()
                                          if v),
              "live fleet  : %d script(s) also present in the launch table"
              % len(live_fleet),
              "",
              "registration name -> launch script (only mcp-* rows matter "
              "here):"]
    for name, scripts in sorted(registration.items()):
        detail.append("  %-16s %s" % (name, ", ".join(scripts) or "(no .py)"))
    detail += ["",
               "table flag vs live registration:"]
    for cfg in servers:
        flag = "registered" if cfg.get("registered") else "INERT"
        live = cfg["file"] in live_fleet
        names = ", ".join(by_script.get(cfg["file"], [])) or "-"
        detail.append("  %-18s table=%-10s live=%-5s as %s"
                      % (cfg["file"], flag, "yes" if live else "no", names))
    detail += ["",
               "verdict     : %s" % ("MATCH -- every flag agrees with the "
                                     "live registration" if not drift
                                     else "DRIFT")]
    detail += ["  drift     : %s" % line for line in drift]
    detail.append("gated       : NO this round. A mismatch is reported with "
                  "exact names so it can be fixed; gating it is the later "
                  "work item. This case is the hole that let the file set be "
                  "mistaken for the registered set")
    suite.record(GF, "~/.claude.json cross-check", [], status=H.INFO,
                 detail=detail,
                 brief="INFO | claude.json cross-check | %s"
                       % ("MATCH" if not drift else "DRIFT: %d" % len(drift)))

    suite.record(GF, "inert files carry zero footprint", [], status=H.INFO,
                 detail=["inert       : %s"
                         % (", ".join(sorted(claims_no)) or "none"),
                         "why it matters: an unregistered server is never "
                         "started, so its descriptions are never sent. "
                         "Summing the fleet over the FILE set optimises for a "
                         "number nobody pays",
                         "flagged rows : %d of %d entries carry an explicit "
                         "flag" % (table_flagged, len(servers))])


def group_cap_runtime(suite, fixture_root, purity_source):
    """H: purity's line-boundary contract, driven in-process.  GATED.

    Every case here is a regression guard on a defect this repo actually shipped,
    so unlike the fleet measurements above these FAIL rather than inform.
    """
    try:
        mod = H.load_module_from_path("ph_cap_runtime",
                                      os.path.join(SCRIPTS_DIR, PURITY_FILE))
    except Exception as exc:                                      # noqa: BLE001
        suite.record(GH, "purity loads for white-box probing",
                     ["%s: %s" % (type(exc).__name__, exc)],
                     detail=["file        : Scripts/%s" % PURITY_FILE,
                             "note        : the whole group needs the module in "
                             "process; a load failure is a FAILURE, not a skip"])
        return

    # -- H1: the degenerate case. One row, wider than the ceiling, under a count
    # header. Before the fix this answered with the header and nothing else: the
    # last newline inside the budget was the one ENDING the header, so the
    # boundary-only cut dropped the single row whole.
    anchor = "src/artefact.txt:14:"
    text = "15+ match(es)\n%s %s" % (anchor, "Z" * (CAP_CEILING * 4))
    reply, payload, note = cap_probe(mod, text)
    rows = [ln for ln in payload[1:] if ln.strip()]
    problems = []
    if not rows:
        problems.append("no payload under the header: %d char(s) kept, which is "
                        "the header alone -- zero anchors, nothing to resume "
                        "from" % len(payload[0]))
    elif not rows[0].startswith(anchor):
        problems.append("the row's %r anchor did not survive: row starts %r"
                        % (anchor, rows[0][:len(anchor) + 8]))
    if not v1_truncation_marker(note):
        problems.append("closing line is not the v1 marker: %r" % note)
    if V1_INLINE_WORDS not in note:
        problems.append("the closing line does not say the cut went inside a "
                        "line: %r" % note)
    if len(reply) > CAP_CEILING:
        problems.append("reply is %d chars, over the %d ceiling"
                        % (len(reply), CAP_CEILING))
    suite.record(GH, "cap-degenerate-row-keeps-its-head-and-anchor", problems,
                 detail=["input       : header + one row of %d chars, ceiling %d"
                         % (len(text) - len(payload[0]) - 1, CAP_CEILING),
                         "kept        : %d chars, %d payload row(s)"
                         % (len(reply), len(rows)),
                         "row head    : %r" % (rows[0][:48] if rows else ""),
                         "closing line: %r" % note,
                         "why gated   : a reply with zero rows carries zero "
                         "`file:line` anchors, so the caller has nothing to "
                         "pass to read_file or find_definition. Measured live: "
                         "12 chars of payload out of 84257"])

    # -- H2: the anchor prefix is longer than the whole ceiling. Defined,
    # non-empty behaviour: the prefix is emitted intact and the reply overshoots,
    # which is a deliberate, documented exception -- an unusable reply is worse
    # than an oversized one, the same trade the notice-wider-than-the-ceiling
    # branch already makes.
    long_anchor = "src/" + "deeply/" * 40 + "buried.txt:1201:"
    reply = mod._cap_text(long_anchor + " payload " + "Q" * 2000, 200)
    note = reply.split("\n")[-1]
    problems = []
    if not reply.strip():
        problems.append("empty reply")
    if not reply.startswith(long_anchor):
        problems.append("the anchor was cut: reply starts %r"
                        % reply[:len(long_anchor)])
    if not v1_truncation_marker(note):
        problems.append("closing line is not the v1 marker: %r" % note)
    suite.record(GH, "cap-anchor-wider-than-the-ceiling-is-still-whole",
                 problems,
                 detail=["input       : one row whose %d-char anchor alone "
                         "exceeds the 200-char ceiling" % len(long_anchor),
                         "kept        : %d chars" % len(reply),
                         "closing line: %r" % note,
                         "overshoot   : %d chars over the ceiling -- DELIBERATE "
                         "and documented at _cap_text; half an anchor is not an "
                         "address" % max(0, len(reply) - 200)])

    # -- H3: the ordinary case is UNCHANGED -- the v1 rule itself. Every kept row
    # must be a whole row, so the in-line fallback must not leak out of the
    # degenerate case it exists for.
    fixture_rows = ["src/f%02d.py:%d: match number %d" % (i, i * 7, i)
                    for i in range(1, 60)]
    text = "59 match(es)\n" + "\n".join(fixture_rows)
    reply, payload, note = cap_probe(mod, text)
    kept = payload[1:]
    partial = [ln for ln in kept if ln not in fixture_rows]
    problems = []
    if partial:
        problems.append("%d kept row(s) are not whole rows, e.g. %r"
                        % (len(partial), partial[0][-40:]))
    if len(kept) >= len(fixture_rows):
        problems.append("the fixture did not truncate at all (%d of %d rows "
                        "kept), so this case proves nothing"
                        % (len(kept), len(fixture_rows)))
    if len(kept) < 2:
        problems.append("only %d row(s) survived a ceiling that fits many"
                        % len(kept))
    if V1_INLINE_WORDS in note:
        problems.append("the in-line notice appeared while whole rows still "
                        "fit: %r" % note)
    if not v1_truncation_marker(note):
        problems.append("closing line is not the v1 marker: %r" % note)
    if len(reply) > CAP_CEILING:
        problems.append("reply is %d chars, over the %d ceiling"
                        % (len(reply), CAP_CEILING))
    suite.record(GH, "cap-normal-case-still-cuts-on-a-line-boundary", problems,
                 detail=["input       : header + %d rows of ~%d chars, ceiling "
                         "%d" % (len(fixture_rows), len(fixture_rows[0]),
                                 CAP_CEILING),
                         "kept        : %d of %d rows, %d chars"
                         % (len(kept), len(fixture_rows), len(reply)),
                         "closing line: %r" % note,
                         "pins        : the v1 rule that a `file:line` anchor is "
                         "never halved. The in-line cut is a FALLBACK for the "
                         "degenerate case only"])

    # -- H4: the same defect end to end, through the real search handler and the
    # dispatcher's cap, on a real file -- not just the helper in isolation.
    cap_root = os.path.join(fixture_root, "cap-root")
    write_text(os.path.join(cap_root, "artefact.txt"), cap_fixture_body())
    base = {"substring_pattern": CAP_NEEDLE,
            "restrict_search_to_code_files": False}
    resumed = dict(base, offset=CAP_SHORT_HITS)
    reply = mod._cap_result(
        mod.handle_search_for_pattern(dict(resumed), cap_root),
        resumed)["__raw_text__"]
    lines = reply.split("\n")
    row_anchor = "artefact.txt:%d:" % (CAP_SHORT_HITS + 1)
    problems = []
    if len(lines) < 2 or not lines[1].strip():
        problems.append("the reply is a header plus a notice, no payload")
    elif row_anchor not in lines[1]:
        problems.append("the surviving row carries no %r anchor: %r"
                        % (row_anchor, lines[1][:60]))
    if not v1_truncation_marker(lines[-1]):
        problems.append("no v1 closing line: %r" % lines[-1])
    if len(reply) > mod.DEFAULT_MAX_ANSWER_CHARS:
        problems.append("reply is %d chars, over the %d default ceiling"
                        % (len(reply), mod.DEFAULT_MAX_ANSWER_CHARS))
    suite.record(GH, "cap-search-reply-at-a-resume-offset-is-never-empty",
                 problems,
                 detail=["call        : search_for_pattern(%r, offset=%d) over "
                         "one file: %d short hits then two %d-char rows"
                         % (CAP_NEEDLE, CAP_SHORT_HITS, CAP_SHORT_HITS,
                            CAP_LONG_ROW),
                         "ceiling     : %d (the default, not overridden)"
                         % mod.DEFAULT_MAX_ANSWER_CHARS,
                         "kept        : %d chars, first payload line %r"
                         % (len(reply),
                            (lines[1][:48] if len(lines) > 1 else "")),
                         "closing line: %r" % lines[-1],
                         "history     : the offset came from the PREVIOUS "
                         "reply's own `offset=%d for more` hint, and the answer "
                         "was 12 chars of header" % CAP_SHORT_HITS])

    # -- H5: one reply, one count. A curtailed scan makes every count a lower
    # bound; the header used to state a bare number while the closing line said
    # `N+ (scan stopped at the ceiling; true total unknown)`, leaving the caller
    # to pick. (mode, params, which header number the closing total is about.)
    multi_root = os.path.join(fixture_root, "cap-multi")
    for idx in range(3):
        write_text(os.path.join(multi_root, "hit%d.txt" % idx),
                   "%s in file %d\n" % (CAP_NEEDLE, idx))
    probes = [
        ("content", dict(base), cap_root, 0),
        ("files_with_matches",
         dict(base, output_mode="files_with_matches", head_limit=1),
         multi_root, 0),
        ("count", dict(base, output_mode="count", head_limit=1),
         multi_root, 1),
    ]
    problems, detail = [], []
    for mode, params, root, which in probes:
        reply = mod.handle_search_for_pattern(dict(params),
                                              root)["__raw_text__"]
        heads, total = count_claims(reply)
        first, last = reply.split("\n")[0], reply.split("\n")[-1]
        detail.append("%-18s: header %r" % (mode, first))
        detail.append("%-18s  note   %r" % ("", last))
        if not v1_row_marker(last):
            problems.append("%s: closing line is not the v1 row marker: %r"
                            % (mode, last))
            continue
        if total is None:
            problems.append("%s: no total in the closing line: %r"
                            % (mode, last))
            continue
        if which >= len(heads):
            problems.append("%s: header states %d count(s), needed #%d: %r"
                            % (mode, len(heads), which + 1, first))
            continue
        if heads[which] != total:
            problems.append("%s: header says %s%s but the closing line says %s%s "
                            "-- one reply, two totals for the same set"
                            % (mode, heads[which][0], heads[which][1] or "",
                               total[0], total[1] or ""))
    suite.record(GH, "cap-curtailed-header-and-closing-line-agree", problems,
                 detail=detail
                 + ["pins        : the `+` lower-bound marking appears in BOTH "
                    "places or NEITHER. The meaning is unchanged -- the count is "
                    "what the scan reached -- and _rows_note still spells out "
                    "WHY once, at the bottom"])

    # -- H6: both markers must be visible to THIS suite's detector. The in-line
    # variant is written out in full in the source on purpose: build it by
    # interpolating a bias word and harvest_strings rewrites the slot to `{}`,
    # the marker check reads zero, and the v1 column goes green on a server that
    # emits no recognisable notice at all.
    problems, found = [], []
    try:
        tree = ast.parse(purity_source, filename=PURITY_FILE)
    except SyntaxError as exc:
        problems.append("purity source did not parse: %s" % exc)
    else:
        found = [(ln, text) for ln, text in harvest_strings(tree)
                 if v1_truncation_marker(text)]
        if not found:
            problems.append("harvest_strings sees NO v1 truncation marker in "
                            "Scripts/%s" % PURITY_FILE)
        if not any(V1_INLINE_WORDS in text for _ln, text in found):
            problems.append("the in-line variant is not harvestable: no "
                            "template carries %r together with the v1 phrases "
                            "-- it was probably assembled by interpolation"
                            % V1_INLINE_WORDS)
    suite.record(GH, "cap-both-marker-templates-are-harvestable", problems,
                 detail=["file        : Scripts/%s" % PURITY_FILE,
                         "markers     : %d" % len(found)]
                 + exemplars(found, PURITY_FILE, 4)
                 + ["trap        : an f-string slot is harvested as `{}`, so an "
                    "interpolated head/tail or in-line phrase erases the exact "
                    "words v1_truncation_marker() looks for"])


def group_hygiene(suite, fixture_root, pyc_before, sandbox):
    """G: sandbox discipline and a bytecode-free tree."""
    stray = [p for p in WRITES if not inside(p, FIXTURE_BASE)]
    suite.record(GG, "every write lands under .claude/tmp",
                 [] if not stray
                 else ["%d write(s) outside the sandbox: %s"
                       % (len(stray), ", ".join(stray))],
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d, all inside: %s"
                         % (len(WRITES), not stray),
                         "files       : %s"
                         % ", ".join(sorted(
                             os.path.relpath(p, fixture_root)
                             for p in WRITES))])

    rooted = []
    for argv in CHILD_ARGV:
        for idx, token in enumerate(argv[:-1]):
            if token == "--project-root" and not inside(argv[idx + 1],
                                                        FIXTURE_BASE):
                rooted.append(" ".join(argv))
    suite.record(GG, "no probed child is given a root outside the sandbox",
                 [] if not rooted
                 else ["%d child(ren) rooted outside %s: %s"
                       % (len(rooted), FIXTURE_BASE, "; ".join(rooted))],
                 detail=["children    : %d spawned" % len(CHILD_ARGV),
                         "sandbox root: %s" % os.path.relpath(sandbox,
                                                              H.REPO_ROOT),
                         "note        : the launch table says `--project-root "
                         "/tmp`; probe_argv() rewrites it, so no probed server "
                         "is pointed at the shared system temp dir"])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GG, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d" % (len(pyc_before),
                                                     len(pyc_after)),
                         "note        : ZERO, not 'unchanged' -- a "
                         "pre-existing file reads as 1 before / 1 after and "
                         "sails through a delta check"])

    outside = inside(FIXTURE_BASE, H.repo_path("Scripts"))
    suite.record(GG, "fixture root is outside the scanned server tree",
                 ["the sandbox lives under Scripts/, so a control fixture "
                  "could leak into the live ceiling scan"] if outside else [],
                 detail=["sandbox     : %s"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "scan root   : Scripts/"])


# ---------------------------------------------------------------------------

def read_source(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(), ""
    except OSError as exc:
        return "", "unreadable: %s" % exc


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="MCP fleet token footprint: description tax, result "
                          "ceilings, boilerplate (measuring tape, not a gate)",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    del WRITES[:]
    del CHILD_ARGV[:]

    smoke = H.load_module_from_path("ph_footprint_table", SMOKE_PATH)
    servers = list(smoke.SERVERS)
    table_flagged = sum(1 for cfg in servers if "registered" in cfg)

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        sandbox = os.path.join(fixture_root, "server-root")
        write_text(os.path.join(sandbox, "project-forge.yaml"), "version: 1\n")

        feet = [measure_server(cfg, sandbox) for cfg in servers]
        feet_by_file = {f.file: f for f in feet}
        live = [f for f in feet if f.registered]
        inert = [f for f in feet if not f.registered]

        answering = group_description_tax(suite, live, GA, "REGISTERED")
        group_probe_floor(suite, len(answering), len(live))
        group_description_tax(suite, inert, GB, "INERT")

        reports, census_rows, sources = [], [], {}
        for cfg in servers:
            source, err = read_source(os.path.join(SCRIPTS_DIR, cfg["file"]))
            sources[cfg["file"]] = source
            registered = bool(cfg.get("registered"))
            if err:
                report = CapReport(cfg["file"], registered)
                report.parse_error = err
                census = Boilerplate(cfg["file"], registered)
                census.parse_error = err
            else:
                report = analyse_caps(source, cfg["file"], registered)
                foot = feet_by_file.get(cfg["file"])
                census = boilerplate_census(
                    source, cfg["file"],
                    foot.desc_blob if foot is not None else "", registered)
            reports.append(report)
            census_rows.append(census)

        group_ceilings(suite, reports)
        group_control(suite, fixture_root)
        group_boilerplate(suite, census_rows, feet_by_file)
        group_drift(suite, servers, table_flagged)
        # Before hygiene: group G asserts over every write this run made, and
        # group H writes search fixtures of its own.
        group_cap_runtime(suite, fixture_root, sources.get(PURITY_FILE, ""))
        group_hygiene(suite, fixture_root, pyc_before, sandbox)
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
