#!/usr/bin/env python3
"""Smoke-test harness for the converged MCP servers.

Launches each Scripts/mcp-*.py server as a subprocess, drives a canonical
JSON-RPC 2.0 sequence over stdin/stdout, and asserts the plumbing invariants
that the convergence patch guarantees:

  * initialize          -> result.protocolVersion == "2024-11-05"
                           result.serverInfo.version == "1.0.0"
  * notifications/...    -> NO response (must not error, must not reply)
  * ping                 -> result == {}
  * tools/list           -> exactly one tool, named <tool>
  * unknown method       -> error.code == -32601
  * forced handler exc   -> error.code == -32603  AND a response actually arrives
                           (this is the FIX-1 regression gate: bare loops crash,
                            silent-swallow loops hang -- both fail this check)
  * unknown FUNCTION     -> result.isError is TRUE
                           (a caller asking the dispatcher for a name that does
                            not exist has failed, and the MCP flag is the only
                            channel that says so; see error_envelope_checks)
  * omitted function     -> result.isError is FALSY
                           (the discriminating control: several servers answer
                            an empty function with a status/catalogue reply ON
                            PURPOSE, which is a success -- without this half the
                            gate would pass a server that flags everything)
  * aliased params       -> a canonical parameter name and one of its own
                           aliases in the SAME call is refused, never silently
                           decided by wire position (see alias_collision_checks)
  * tshark ceiling       -> max_answer_chars is canonical, max_output_chars a
                           kept alias of it (see tshark_ceiling_param_checks)
  * notifications/       -> an unknown, finished, bool, float or malformed
    cancelled               requestId is ignored and NEVER answered, and the
                           next ping still is (see cancel_notification_checks)
  * in-flight cancel     -> mcp-jenkins only: a cancelled call held open by a
                           loopback peer is never answered, a sibling targeted
                           only by ill-typed requestIds still is (R-0006; see
                           inflight_cancel_probe)
  * in-flight kill       -> mcp-forge and mcp-wiki: a cancelled test / measure
                           call's child process is killed, not left to
                           finish, and the cancelled id is never answered
                           (R-0006 phase 2, R-0043; see inflight_kill_probe)

Usage:
  python3 _mcp_smoke_test.py                 # all servers
  python3 _mcp_smoke_test.py mcp-clangd.py   # one or more specific servers

Exit code 0 iff every non-skipped server passes every check.
"""

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Per-server launch config: minimal args so argparse succeeds and the
# initialize/ping/unknown path runs WITHOUT spawning heavy subprocess work.
#
# `registered` says whether Claude Code actually LAUNCHES this server (i.e.
# whether ~/.claude.json has an `mcpServers` entry for it).  It is NOT the same
# question as "is this file in the table": this table enumerates server FILES,
# and an unregistered file is inert -- never started, so its tools/list
# descriptions are never sent to a model and its token footprint is exactly
# zero.  Conflating the two sets has already produced one wrong fleet-wide
# conclusion in this repo, so the distinction is now written down per entry
# instead of being re-derived (badly) each time.
#
# tests/test_mcp_footprint.py sums the fleet footprint over `registered: True`
# only, reports the rest separately, and cross-checks these flags against the
# live ~/.claude.json.  This smoke harness itself ignores the flag on purpose:
# its job is that every server file still speaks the protocol, registered or not.
#
# `launcher` is the OPTIONAL argv prefix a server must be started with, and it
# exists because `[sys.executable, <path>]` is not universal: mcp-webfetch.py
# declares its third-party deps in a PEP-723 `# /// script` block and imports
# bs4 at module scope, so a bare python3 kills it at import -- which is why
# every harness used to report it SKIP.  ~/.claude.json launches it as
# `uv run --script <path>`, and so does the table, so what is tested is what
# runs.  Absent -> this interpreter, as before.  See launch_prefix().
SERVERS = [
    {"file": "mcp-forge.py",    "tool": "forge_call",    "args": ["--project-root", "/tmp"], "registered": True},
    {"file": "mcp-git.py",      "tool": "git_call",      "args": ["--project-root", "/tmp"], "registered": True},
    {"file": "mcp-purity.py",   "tool": "purity_call",   "args": ["--project-root", "/tmp"], "registered": True},
    {"file": "mcp-jenkins.py",  "tool": "jenkins_call",  "args": ["--endpoint", "http://127.0.0.1:1", "--username", "x", "--token", "y"], "registered": True},
    {"file": "mcp-tshark.py",   "tool": "tshark_call",   "args": ["--project-root", "/tmp"], "registered": True},
    {"file": "mcp-webfetch.py", "tool": "webfetch_call", "args": [], "registered": True,
     "launcher": ["uv", "run", "--script"]},
    {"file": "mcp-context7.py", "tool": "context7_call", "args": [], "registered": True},
    {"file": "mcp-lldb.py",     "tool": "lldb_call",     "args": [], "registered": True},
    {"file": "mcp-gdc.py",      "tool": "gdc_call",      "args": [], "registered": True},
    {"file": "mcp-lua-lsp.py",  "tool": "luals_call",    "args": [], "registered": False},
    {"file": "mcp-clangd.py",   "tool": "clangd_call",   "args": [], "registered": False},
    {"file": "mcp-cuda.py",     "tool": "cuda_call",     "args": [], "registered": False},
    {"file": "mcp-postgres.py", "tool": "postgres_call", "args": ["--host", "127.0.0.1:1", "--dbname", "x"], "registered": True},
    {"file": "mcp-wiki.py",     "tool": "wiki_call",     "args": ["--project-root", "/tmp"], "registered": True},
    {"file": "mcp-inspect.py",  "tool": "inspect_call",  "args": [], "registered": True},
]

READ_TIMEOUT = 8.0  # seconds to wait for a single response line


def launch_prefix(cfg, python_flags=()):
    """Argv prefix for one SERVERS entry: its `launcher`, else this interpreter.

    A `launcher` whose binary is NOT on PATH falls back to the interpreter, so a
    host without `uv` degrades to exactly the old behaviour -- the server dies at
    import and the harness records SKIP with the stderr tail -- instead of the
    run dying on FileNotFoundError from Popen.

    `python_flags` (e.g. `-B`) only apply to the interpreter form; the uv form
    relies on PYTHONDONTWRITEBYTECODE, which the test harnesses set for every
    child anyway.
    """
    launcher = cfg.get("launcher")
    if launcher and shutil.which(launcher[0]):
        return list(launcher)
    return [sys.executable] + list(python_flags)


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None

    def start(self):
        path = os.path.join(SCRIPT_DIR, self.cfg["file"])
        # No `env=` and no `-B` here, and that is not an oversight waiting to be
        # patched.  A server runs as __main__, which CPython never caches, and
        # the generated-region architecture means it imports no sibling from
        # this repo -- so there is nothing here for CPython to write a .pyc for.
        # MEASURED, not assumed: a standalone run of this file with
        # PYTHONDONTWRITEBYTECODE unset starts all fifteen servers and leaves
        # zero .pyc in the tree.  If a server ever grows a repo-local import,
        # this stops being true and the suites asserting zero absolutely are
        # what will say so.
        # `env` is absent from every SERVERS row, so None -- inherit -- is what
        # the fleet runs with; only the in-flight cancel probe passes one.
        self.proc = subprocess.Popen(
            launch_prefix(self.cfg) + [path] + self.cfg["args"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=self.cfg.get("env"),
        )

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout=READ_TIMEOUT):
        """Read one response line; return parsed dict or None on timeout/EOF."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                # process exited; try a final non-blocking drain
                rest = self.proc.stdout.readline()
                return json.loads(rest) if rest.strip() else None
            r, _, _ = select.select([self.proc.stdout], [], [], 0.25)
            if r:
                line = self.proc.stdout.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                return json.loads(line)
        return None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stderr_tail(self, n=400):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            _, err = self.proc.communicate(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
            err = ""
        return (err or "")[-n:]

    def stop(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def check(name, cond, detail=""):
    return (name, bool(cond), detail)


class _Absent:
    """The isError key was missing entirely.

    FALSY on purpose, and that is the whole point of the class: an MCP client
    reads a missing isError as success, so for verdict arithmetic `absent` and
    `False` must behave identically or the gate would measure a spelling rather
    than the contract.  A plain string sentinel got this backwards -- a
    non-empty string is truthy, which failed the negative control on every
    server whose status reply simply never sets the flag.

    It is still a DISTINCT value from False so the detail line can say which of
    the two it saw: "decided this is not an error" and "never had an opinion"
    are the same behaviour to a client and different defects to a fixer.
    """

    def __bool__(self):
        return False

    def __repr__(self):
        return "<absent>"


ABSENT = _Absent()


def _dispatch_call(srv, call_id, tool, arguments, timeout=READ_TIMEOUT):
    """tools/call `tool` with raw `arguments`; return (is_error, text, resp).

    `is_error` is the isError value AS SENT, with the falsy ABSENT sentinel
    standing in for a missing key (see _Absent).

    A missing/timed-out reply and a JSON-RPC-level error both come back as
    is_error=None, which fails the positive case AND the negative one: neither
    is a tool-result envelope, so neither can carry the flag.
    """
    srv.send({"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments}})
    resp = srv.read(timeout=timeout)
    if not isinstance(resp, dict) or not isinstance(resp.get("result"), dict):
        return None, "", resp
    result = resp["result"]
    content = result.get("content") or []
    text = content[0].get("text", "") if content else ""
    return result.get("isError", ABSENT), text, resp


def error_envelope_checks(srv, cfg, checks):
    """The tool-level error-envelope contract, per server, over live JSON-RPC.

    Two halves, and both are load-bearing:

      POSITIVE -- function="__no_such_function__".  A caller asking for a
        function that does not exist has failed, so the reply MUST carry
        isError: True.  Some servers answer by listing their whole function
        catalogue; that is still an error answer, so the assertion reads the
        FLAG and never the text (a length or substring heuristic here would
        pin the prose of 15 different servers instead of the contract).

      NEGATIVE -- function omitted entirely.  Most servers answer that with a
        status or catalogue reply BY DESIGN (mcp-gdc.py's handle_gdc_call:
        `if not function: return await handle_gdc_status(...)`), which is a
        success and MUST NOT be flagged.  Without this half a server that
        flagged every reply -- the same defect wearing the opposite sign --
        would pass the gate.

    Both probes need NO environment: no browser, no database, no debugger, no
    network.  Every server answers an unknown function name from its own
    handler table, and every `if not function` status handler reports what it
    knows locally.  The one that reaches out at all, gdc_status, wraps its
    Chrome fetch in a bounded urlopen(timeout=5) inside its own try/except and
    returns a running-server string either way, so its VERDICT does not depend
    on whether Chrome is up -- only its text does.

    Severity: this compares runtime behaviour of a locally-started process with
    no network dependency and no ordering dependency, so it cannot flap and is
    a hard failure -- the same bar every other check in this harness is held
    to.  No server measured here is an exception.

    The `registered` flag is deliberately ignored, exactly as the rest of this
    harness ignores it: mcp-clangd.py, mcp-cuda.py and mcp-lua-lsp.py are never
    launched by Claude Code, so their footprint is zero -- but all three start
    offline and answer both probes in milliseconds, so the gate DRIVES them
    rather than trusting a static reading of their source.  A measurement is
    strictly better evidence than a reading, and an unmeasured server must
    never be silently scored as a pass.
    """
    tool = cfg["tool"]

    # `is True`, not truthiness: MCP declares isError a boolean, and a server
    # stuffing a truthy string in there is a defect the strict form catches.
    is_error, _text, resp = _dispatch_call(srv, 6, tool,
                                           {"function": "__no_such_function__",
                                            "params": {}})
    checks.append(check(
        "unknown-function -> isError True",
        is_error is True,
        "isError=%r; envelope=%s" % (is_error, json.dumps(resp)[:220])))

    is_error, _text, resp = _dispatch_call(srv, 7, tool, {"params": {}})
    checks.append(check(
        "omitted-function -> isError falsy (control)",
        is_error is not None and not is_error,
        "isError=%r; envelope=%s" % (is_error, json.dumps(resp)[:220])))


# The one token every host's collision error carries.  Asserting a shared
# substring is the opposite of the prose-pinning error_envelope_checks warns
# against: that gate reads only the FLAG because fifteen servers word their
# failures differently and always did.  Here the wording is the contract being
# established -- without a discriminator the positive probe would pass on any
# error at all, including the "unknown function" one it is meant to look past.
COLLISION_TOKEN = "Ambiguous parameters"

# Per host: a function name, then TWO parameter spellings that canonicalize to
# the same name on that server.
#
# Probe DATA, hand-written like "__no_such_function__" above, NOT a derived
# copy of anything.  Deriving the pair would mean re-implementing three
# different alias-table shapes -- flat, per-function-over-global, and forge's
# injected table -- inside a harness whose subject is live JSON-RPC, and the
# per-function tables do not merely extend the global one: mcp-postgres.py
# INVERTS it for call_function (globally parameters->params, per-function
# parameters->args), so a reverse map built from either table alone would be
# wrong.  A row that stops colliding fails LOUDLY -- the positive half simply
# gets no collision error -- rather than silently testing nothing.
#
# Every one of these reaches its server's resolver with NO environment: no
# database, no browser, no language server, no network.  That is a property of
# the resolver's POSITION, and it now holds on all ten: parameter normalization
# happens before the handler lookup on every host.
ALIAS_COLLISION = {
    "mcp-clangd.py":   ("clangd_find_definition", "file",     "path"),
    "mcp-cuda.py":     ("cuda_find_definition",  "file",      "path"),
    "mcp-lua-lsp.py":  ("luals_find_definition", "file",      "path"),
    "mcp-tshark.py":   ("analyze",               "pcap",      "file"),
    "mcp-jenkins.py":  ("get_build_log",         "job",       "job_path"),
    "mcp-postgres.py": ("query",                 "statement", "sql"),
    "mcp-wiki.py":     ("search",                "q",         "query"),
    "mcp-purity.py":   ("read_file",             "path",      "relative_path"),
    "mcp-webfetch.py": ("fetch",                 "max_chars", "max_answer_chars"),
    "mcp-forge.py":    ("build",                 "t",         "targets"),
}


def alias_collision_checks(srv, cfg, checks):
    """Two spellings of one parameter must be an ERROR, per server, over live
    JSON-RPC.

    A caller that sends both a canonical name and one of its aliases has said
    the same thing twice and meant two different things.  Every host used to
    answer that by silently keeping one of the values, decided by WIRE POSITION
    -- six kept the last, four kept the first, and neither rule is
    order-independent or prefers the canonical spelling.  Picking one of the two
    would have picked a coin; the ambiguity is the defect, so the resolver
    reports it.

    Three halves, and each catches a different failure:

      POSITIVE -- both spellings.  The reply MUST carry isError: True AND name
        the ambiguity.  The flag alone is not enough here, unlike in
        error_envelope_checks: nearly any malformed call to these servers is
        already flagged, so a probe reading only the flag would pass a server
        that never learned the rule.

      CONTROL -- one spelling.  MUST NOT carry the collision token.  This is the
        opposite-sign defect: a resolver that flagged every call would satisfy
        the positive half.  It deliberately does NOT assert isError is falsy --
        a single bogus value is often a legitimate failure ('no such file'), and
        asserting on that would pin fifteen servers' validation prose.

      COVERAGE -- runs on ALL fifteen, including the five with no resolver at
        all.  It asserts the probe table has a row for exactly those files whose
        source defines `_resolve_aliases`, so an eleventh host cannot join the
        fleet and quietly skip the gate.  The set is DERIVED from the source,
        never typed: a hand-maintained copy of a fleet count was wrong within a
        day of being written in this repo once already.

    Scope, stated because an unstated one is the same defect as a false
    invariant: this gate proves the resolver REFUSES, never that the ten alias
    TABLES are free of collisions a caller could not have caused, and never the
    envelope-level `function`/`f` and `params`/`p` or-chains, which are a
    different mechanism at a different layer and remain first-wins.
    """
    row = ALIAS_COLLISION.get(cfg["file"])

    with open(os.path.join(SCRIPT_DIR, cfg["file"]), encoding="utf-8") as fh:
        has_resolver = "def _resolve_aliases(" in fh.read()
    checks.append(check(
        "alias probe row iff _resolve_aliases exists",
        has_resolver == (row is not None),
        "resolver=%r row=%r" % (has_resolver, row is not None)))
    if row is None or not has_resolver:
        return

    function, spelling_a, spelling_b = row
    tool = cfg["tool"]

    is_error, text, resp = _dispatch_call(srv, 8, tool, {
        "function": function,
        "params": {spelling_a: "A", spelling_b: "B"}})
    checks.append(check(
        "alias collision -> isError True + named",
        is_error is True and COLLISION_TOKEN in text,
        "isError=%r; text=%r" % (is_error, text[:180])))

    is_error, text, _resp = _dispatch_call(srv, 9, tool, {
        "function": function,
        "params": {spelling_a: "A"}})
    checks.append(check(
        "one spelling -> no collision error (control)",
        COLLISION_TOKEN not in text,
        "isError=%r; text=%r" % (is_error, text[:180])))


def _purity_call(srv, call_id, function, params=None):
    """Send a purity_call tools/call and return (text, raw_response)."""
    srv.send({"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
              "params": {"name": "purity_call",
                         "arguments": {"function": function, "params": params or {}}}})
    resp = srv.read() or {}
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return text, resp


# Canonical semantic functions the Phase-0 fold registers in purity_call.
PURITY_SEMANTIC = [
    "find_definition", "find_type_definition", "find_references",
    "find_implementations", "type_at",
    "diagnostics", "outline", "symbol", "symbol_context", "inlay_hints",
    "symbol_change_impact",
]


def purity_semantic_checks(srv, checks):
    """Phase-0 smoke checks for mcp-purity's folded-in semantic layer (Decision
    D2: all coverage driven over JSON-RPC; no in-process unit file). Every
    semantic call here is made with NO params, so each handler returns a fast
    validation error BEFORE any clangd subprocess is spawned -- the assertion is
    that the function DISPATCHES (registered + routed), not that an LSP runs.
    """
    cid = 100

    # (a) all 11 canonical semantic names dispatch (no "Unknown function")
    undispatched = []
    for fn in PURITY_SEMANTIC:
        text, _ = _purity_call(srv, cid, fn)
        cid += 1
        if "Unknown function" in text:
            undispatched.append(fn)
    checks.append(check("purity: 11 semantic names dispatch",
                        not undispatched, "undispatched=%r" % undispatched))

    # (b) legacy clangd_*/cuda_* names dispatch (direct HANDLERS keys [C1])
    legacy = ["clangd_find_definition", "cuda_find_definition",
              "clangd_workspace_symbols", "cuda_document_outline", "clangd_init"]
    undispatched_legacy = []
    for fn in legacy:
        text, _ = _purity_call(srv, cid, fn)
        cid += 1
        if "Unknown function" in text:
            undispatched_legacy.append(fn)
    checks.append(check("purity: legacy clangd_/cuda_ names dispatch",
                        not undispatched_legacy, "undispatched=%r" % undispatched_legacy))

    # (c) negative control: a bogus name IS reported unknown
    text, _ = _purity_call(srv, cid, "totally_bogus_fn")
    cid += 1
    checks.append(check("purity: bogus name -> Unknown function",
                        "Unknown function" in text, text[:80]))

    # (d) find_definition with neither 'symbol' nor 'at' -> validation error
    text, _ = _purity_call(srv, cid, "find_definition")
    cid += 1
    checks.append(check("purity: find_definition no-args -> validation error",
                        ("requires either" in text) and ("Unknown function" not in text),
                        text[:100]))

    # (e) the alias-collision refusal is ORDER-INDEPENDENT, and that is the
    #     whole reason it replaced last-wins here rather than first-wins: both
    #     of those read the WIRE ORDER, so the very same two keys sent the other
    #     way round quietly made a different call. This check used to pin the
    #     last-wins OUTCOME -- it asserted that 'relative_path' beat 'path'
    #     because it came second. Same pair now, sent both ways, and the two
    #     replies must be the SAME message.
    #
    #     It is purity-specific on purpose even though alias_collision_checks
    #     already drives all ten: that gate proves the refusal HAPPENS, this one
    #     proves the reply is ACTIONABLE -- it names both spellings the caller
    #     wrote and the canonical name they collide on, which is the difference
    #     between an error a model can fix and one it can only retry.
    ordered = []
    for pair in ({"path": "A.txt", "relative_path": "B.txt"},
                 {"relative_path": "B.txt", "path": "A.txt"}):
        text, resp = _purity_call(srv, cid, "read_file", pair)
        cid += 1
        ordered.append((text, resp.get("result", {}).get("isError")))
    (text_a, err_a), (text_b, err_b) = ordered
    checks.append(check(
        "purity: alias collision refused, order-independent, flagged",
        (err_a is True and err_b is True and text_a == text_b
         and "'path'" in text_a and "'relative_path'" in text_a),
        "a=%r b=%r isError=(%r, %r)" % (text_a[:110], text_b[:110], err_a, err_b)))

    # (f) legacy luals_* names dispatch (direct HANDLERS keys [C1, Phase 1])
    luals = ["luals_find_definition", "luals_find_references",
             "luals_workspace_symbols", "luals_document_outline",
             "luals_symbol_change_impact", "luals_init"]
    undispatched_luals = []
    for fn in luals:
        text, _ = _purity_call(srv, cid, fn)
        cid += 1
        if "Unknown function" in text:
            undispatched_luals.append(fn)
    checks.append(check("purity: legacy luals_* names dispatch",
                        not undispatched_luals, "undispatched=%r" % undispatched_luals))

    # (g) negative control: a luals bogus name IS reported unknown
    text, _ = _purity_call(srv, cid, "luals_bogus")
    cid += 1
    checks.append(check("purity: luals_bogus -> Unknown function",
                        "Unknown function" in text, text[:80]))

    # (h) search_for_pattern tolerates Grep-style output_mode="context" and
    #     context_lines=2 -- must NOT error, must return content-style matches.
    #     We write a tiny fixture under /tmp (the server's project-root) so the
    #     relative_path lookup works without touching the live repo.
    import tempfile, os as _os
    _fixture_dir = tempfile.mkdtemp(prefix="purity_smoke_", dir="/tmp")
    _fixture_rel = _os.path.relpath(
        _os.path.join(_fixture_dir, "fixture.txt"), "/tmp"
    )
    with open(_os.path.join(_fixture_dir, "fixture.txt"), "w") as _fh:
        _fh.write("line_before_1\nline_before_2\nTOKEN_CANARY\nline_after_1\nline_after_2\n")

    text_ctx2, _ = _purity_call(srv, cid, "search_for_pattern", {
        "substring_pattern": "TOKEN_CANARY",
        "relative_path": _fixture_rel,
        "output_mode": "context",
        "context_lines": 2,
    })
    cid += 1
    checks.append(check(
        "purity: search_for_pattern output_mode=context tolerated",
        "Unknown params" not in text_ctx2
        and "must be 'files_with_matches'" not in text_ctx2
        and "TOKEN_CANARY" in text_ctx2,
        text_ctx2[:120],
    ))

    # (i) context_lines=2 yields more lines than context_lines omitted (context actually works).
    text_ctx0, _ = _purity_call(srv, cid, "search_for_pattern", {
        "substring_pattern": "TOKEN_CANARY",
        "relative_path": _fixture_rel,
        "output_mode": "content",
    })
    cid += 1
    checks.append(check(
        "purity: search_for_pattern context_lines=2 expands output",
        len(text_ctx2) > len(text_ctx0),
        "ctx2_len=%d ctx0_len=%d" % (len(text_ctx2), len(text_ctx0)),
    ))

    # (j) prefixed-name unknown-param guard: luals_find_definition with bogus key
    #     -> guard now fires for prefixed names (previously INERT)
    text, _ = _purity_call(srv, cid, "luals_find_definition", {"bogus_key": 1})
    cid += 1
    checks.append(check(
        "purity: luals_find_definition bogus_key -> Unknown params",
        "Unknown params" in text,
        text[:120],
    ))

    # (k) valid call to luals_find_definition -> injected _backend not flagged up-front
    text, _ = _purity_call(srv, cid, "luals_find_definition", {"symbol_name": "x"})
    cid += 1
    checks.append(check(
        "purity: luals_find_definition valid param -> no Unknown params",
        "Unknown params" not in text,
        text[:120],
    ))

    # (l) clangd_find_definition_at resolves to find_definition's accepted-set
    text, _ = _purity_call(srv, cid, "clangd_find_definition_at", {"line": 1, "character": 1})
    cid += 1
    checks.append(check(
        "purity: clangd_find_definition_at valid params -> no Unknown params",
        "Unknown params" not in text,
        text[:120],
    ))

    # (m) clangd_init (no-op) stays lenient regardless of params
    text, _ = _purity_call(srv, cid, "clangd_init", {"anything": 1})
    cid += 1
    checks.append(check(
        "purity: clangd_init bogus param -> no Unknown params (no-op lenient)",
        "Unknown params" not in text,
        text[:120],
    ))

    # cleanup fixture
    import shutil as _shutil
    _shutil.rmtree(_fixture_dir, ignore_errors=True)


def _tiny_pcap_bytes(packets=5):
    """A classic little-endian pcap of `packets` Ethernet frames, stdlib only.

    The ethertype is 0x88B5 (IEEE local experimental), so tshark dissects each
    frame as plain Ethernet II with a data payload -- no checksum, no protocol
    state, nothing that could make the capture itself the reason a probe fails.
    """
    import struct
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for i in range(packets):
        frame = (b"\x02\x00\x00\x00\x00\x01" + b"\x02\x00\x00\x00\x00\x02"
                 + b"\x88\xb5" + (b"smoke-%02d" % i) * 4)
        out.append(struct.pack("<IIII", 1700000000 + i, 0, len(frame), len(frame)))
        out.append(frame)
    return b"".join(out)


def _fleet_trunc_words():
    """The fleet's closing-line spelling, READ from where it is written down once.

    No canonical generation source holds the closing line -- `_mcp_paging.py`
    says each host's `_cap_text` is a merge, not a lift -- so its one written
    spelling is the footprint suite's `V1_TRUNC_*` constants. They are read with
    `ast` rather than imported: importing a test module from here would run its
    harness setup for three string literals.
    """
    import ast
    path = os.path.join(os.path.dirname(SCRIPT_DIR), "tests", "test_mcp_footprint.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    found = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("V1_TRUNC_OPEN", "V1_TRUNC_CLOSE",
                                           "V1_TRUNC_END_WORDS")):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found["V1_TRUNC_OPEN"], found["V1_TRUNC_CLOSE"], found["V1_TRUNC_END_WORDS"]


def tshark_ceiling_param_checks(srv, cfg, checks):
    """mcp-tshark's reply ceiling answers to the fleet spelling (roadmap R-0008).

    `max_answer_chars` is the canonical name, as on every other server that
    takes a per-call ceiling; `max_output_chars` -- this server's own spelling
    until R-0008 -- stays accepted as a declared ALIAS so a caller who learned
    it keeps working. ADR 0015 makes two spellings of one parameter an error,
    so sending both is refused rather than decided by wire position.

    Two layers, and the second is optional on purpose:

      RESOLVER -- offline, always run. A collision reply names the canonical
        key it collided on, so `both set 'max_answer_chars'` proves which name
        is canonical from the wire alone, with no tshark binary and no capture.
        The tools/list description must advertise the fleet spelling.

      EFFECT -- needs the tshark binary. A five-frame pcap is written under the
        repo's .claude/tmp and analyzed three ways: with the new name, with the
        old alias, and with neither (the control: the default ceiling must NOT
        cut a reply this small, or the two positive halves would pass on any
        ceiling at all). When the server reports tshark absent the half is not
        run and adds no check: an unmeasured effect is not scored as a pass.
    """
    tool = cfg["tool"]
    cid = 200

    for pair in (("max_answer_chars", "max_output_chars"),
                 ("max_chars", "max_answer_chars")):
        is_error, text, _resp = _dispatch_call(srv, cid, tool, {
            "function": "analyze",
            "params": {pair[0]: 64, pair[1]: 64}})
        cid += 1
        checks.append(check(
            "tshark: %s + %s -> refused on 'max_answer_chars'" % pair,
            is_error is True and COLLISION_TOKEN in text
            and "both set 'max_answer_chars'" in text,
            "isError=%r; text=%r" % (is_error, text[:180])))

    srv.send({"jsonrpc": "2.0", "id": cid, "method": "tools/list", "params": {}})
    cid += 1
    tl = srv.read() or {}
    tools = tl.get("result", {}).get("tools", [])
    desc = tools[0].get("description", "") if tools else ""
    checks.append(check(
        "tshark: description advertises max_answer_chars + the kept alias",
        "max_answer_chars" in desc and "max_output_chars" in desc,
        "description tail=%r" % desc[-240:]))

    import tempfile
    tmp_root = os.path.join(os.path.dirname(SCRIPT_DIR), ".claude", "tmp")
    os.makedirs(tmp_root, exist_ok=True)
    fixture_dir = tempfile.mkdtemp(prefix="tshark_smoke_", dir=tmp_root)
    try:
        pcap = os.path.join(fixture_dir, "tiny.pcap")
        with open(pcap, "wb") as fh:
            fh.write(_tiny_pcap_bytes())

        replies = {}
        for label, extra in (("control", {}),
                             ("max_answer_chars", {"max_answer_chars": 64}),
                             ("max_output_chars", {"max_output_chars": 64})):
            params = {"file": pcap}
            params.update(extra)
            is_error, text, _resp = _dispatch_call(
                srv, cid, tool, {"function": "analyze", "params": params},
                timeout=30.0)
            cid += 1
            replies[label] = (is_error, text)

        ctl_err, ctl_text = replies["control"]
        if "tshark not found" in ctl_text:
            return
        # ADR 0013: a class licenses a deviation on the DEFAULT alone, so the
        # cut must close with the fleet's line, naming the uncut total.
        t_open, t_close, t_ends = _fleet_trunc_words()
        checks.append(check(
            "tshark: analyze with no ceiling param is not cut (control)",
            not ctl_err and "## Packet Analysis" in ctl_text
            and t_open not in ctl_text and "truncated" not in ctl_text
            and len(ctl_text) > 64,
            "isError=%r len=%d text=%r" % (ctl_err, len(ctl_text), ctl_text[:120])))
        for label in ("max_answer_chars", "max_output_chars"):
            err, text = replies[label]
            last = text.rstrip("\n").rsplit("\n", 1)[-1]
            checks.append(check(
                "tshark: %s=64 cuts the reply with the fleet closing line" % label,
                not err and last.startswith(t_open) and last.endswith(t_close)
                and "from the head" in last and "from the head" in t_ends
                and (" of %d chars " % len(ctl_text)) in last
                and text.count(t_open) == 1,
                "isError=%r len=%d last=%r" % (err, len(text), last[-200:])))
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)


# Every one of these names NO live request, so a server honouring R-0006 must
# ignore it silently -- and so must one that predates it, which is why these
# rows guard a regression rather than prove the feature: the proof is the
# in-flight probe below.  `1` is the initialize id, already answered: a finished
# id is unknown by definition.  `true` and `1.0` are the two spellings Python
# hashes equal to 1, which a registry keyed on a naive parse would match.
CANCEL_NOISE = [
    ("unknown id",       {"requestId": 987654, "reason": "smoke"}),
    ("finished id",      {"requestId": 1}),
    ("requestId true",   {"requestId": True}),
    ("requestId float",  {"requestId": 1.0}),
    ("requestId null",   {"requestId": None}),
    ("requestId list",   {"requestId": [1]}),
    ("params missing",   None),
    ("params not a dict", "x"),
]


def cancel_notification_checks(srv, checks):
    """notifications/cancelled that names no live request: never answered.

    One check per row, each followed by its own ping, so a failure names the
    spelling that drew a reply (or killed the process) instead of reporting
    "something in the batch".
    """
    cid = 300
    for label, params in CANCEL_NOISE:
        note = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
        if params is not None:
            note["params"] = params
        srv.send(note)
        srv.send({"jsonrpc": "2.0", "id": cid, "method": "ping", "params": {}})
        reply = srv.read() or {}
        checks.append(check(
            "cancel %s -> no reply, ping next" % label,
            reply.get("id") == cid and reply.get("result") == {},
            "first reply=%s alive=%r" % (json.dumps(reply)[:160], srv.alive())))
        cid += 1


class _HeldHttpPeer:
    """A loopback HTTP peer that accepts each request and HOLDS it until told.

    What makes the in-flight probe deterministic: the probe knows a call is in
    flight because the peer has its request bytes, not because a sleep elapsed,
    and nothing can finish until `release()` -- so a reply for a cancelled id
    cannot slip out before the cancel is sent, and cannot be missed after.
    """

    BODY = b'{"jobs": []}'

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.25)
        self.port = self.sock.getsockname()[1]
        self._release = threading.Event()
        self._closed = threading.Event()
        self._cond = threading.Condition()
        self.arrived = 0
        self.answered = 0
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._closed.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._hold, args=(conn,), daemon=True).start()

    def _hold(self, conn):
        try:
            conn.settimeout(10.0)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            with self._cond:
                self.arrived += 1
                self._cond.notify_all()
            self._release.wait(timeout=30.0)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: %d\r\nConnection: close\r\n\r\n"
                         % len(self.BODY) + self.BODY)
            with self._cond:
                self.answered += 1
                self._cond.notify_all()
        except OSError:
            pass
        finally:
            conn.close()

    def wait_for(self, attr, n, timeout):
        deadline = time.time() + timeout
        with self._cond:
            while getattr(self, attr) < n:
                left = deadline - time.time()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def release(self):
        self._release.set()

    def close(self):
        self._release.set()
        self._closed.set()
        try:
            self.sock.close()
        except OSError:
            pass


def _read_ids(srv, until_ids, timeout):
    """Read replies until every id in `until_ids` has been seen; return all ids."""
    seen = []
    deadline = time.time() + timeout
    while not set(until_ids) <= set(seen):
        left = deadline - time.time()
        if left <= 0:
            break
        reply = srv.read(timeout=left)
        if reply is None:
            break
        seen.append(reply.get("id"))
    return seen


def inflight_cancel_probe(checks):
    """R-0006 end to end, on a request that is really in flight.

    mcp-jenkins, because it is the one server whose slow path needs nothing
    but a URL: its endpoint is a launch argument, so a loopback peer that
    holds the HTTP request open makes `list_jobs` exactly as slow as the probe
    wants and no slower.  gdc and lldb -- the two `task`-class servers, where
    the cancel reclaims the work itself -- would need Chrome or an inferior
    process, which the fleet smoke cannot assume.  Jenkins is `reply-only`: its
    worker thread finishes the HTTP call regardless, so what this proves is the
    UNIFORM half of phase 1 -- the cancelled id is never answered -- which is
    the half every server shares.

    Two calls are held open.  Call 1 is targeted only by requestIds that must
    NOT match it -- `true` and `1.0` hash equal to 1, `"1"` is a different id
    -- and must still be answered once released.  Call 2 is cancelled for real
    and must never be answered, not even after its HTTP response arrives.
    """
    peer = _HeldHttpPeer()
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    cfg = {"file": "mcp-jenkins.py", "tool": "jenkins_call", "env": env,
           "args": ["--endpoint", "http://127.0.0.1:%d" % peer.port,
                    "--username", "x", "--token", "y"]}
    srv = Server(cfg)
    srv.start()
    try:
        srv.send({"jsonrpc": "2.0", "id": "init", "method": "initialize",
                  "params": {}})
        if (srv.read() or {}).get("id") != "init":
            checks.append(check("cancel probe: second instance initializes",
                                False, "alive=%r" % srv.alive()))
            return
        for call_id in (1, 2):
            srv.send({"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                      "params": {"name": "jenkins_call",
                                 "arguments": {"function": "list_jobs",
                                               "params": {}}}})
        both = peer.wait_for("arrived", 2, 10.0)
        checks.append(check("cancel probe: both calls held in flight by the peer",
                            both, "arrived=%d" % peer.arrived))
        if not both:
            return

        for bogus in (True, 1.0, "1", [1]):
            srv.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": bogus}})
        srv.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                  "params": {"requestId": 2, "reason": "smoke probe"}})
        srv.send({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        first = srv.read() or {}
        checks.append(check("cancel probe: no reply to a cancel, ping answered",
                            first.get("id") == 3,
                            "first reply=%s" % json.dumps(first)[:160]))

        peer.release()
        peer.wait_for("answered", 2, 10.0)
        srv.send({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}})
        seen = _read_ids(srv, (1, 4), 10.0)
        # A reply for 2 would be queued behind the same release as 1's; one more
        # ping and a short linger give a straggler every chance to show up.
        srv.send({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}})
        seen += _read_ids(srv, (5,), 10.0)
        seen += _read_ids(srv, ("never",), 1.0)
        checks.append(check(
            "cancel probe: sibling not cancelled by true/1.0/\"1\"/[1]",
            1 in seen, "ids seen=%r" % seen))
        checks.append(check(
            "cancel probe: the cancelled call is never answered",
            2 not in seen and 5 in seen, "ids seen=%r" % seen))
    finally:
        srv.stop()
        peer.close()


_KILL_PROBE_YAML = """version: 1
test:
  hold:
    description: "R-0006 smoke: record the shell pid, then become a 30s sleep"
    commands:
      - echo $$ > child.pid; exec sleep 30
"""


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_KILL_PROBE_PAGE = """---
name: hold
title: Hold
type: concept
status: active
description: R-0043 smoke kill probe page
---

# Hold

<!-- BEGIN MEASURED: hold -->
<!-- END MEASURED: 000000000000 -->
"""


def _kill_probe_project(which, project, pid_file):
    """Write the throwaway project for one `kill`-class server.

    Returns (server cfg, tools/call arguments) for a call whose child writes
    its shell pid to `pid_file` and then `exec`s a 30s sleep -- so the pid on
    disk IS the child the cancel must kill.
    """
    if which == "forge":
        with open(os.path.join(project, "project-forge.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write(_KILL_PROBE_YAML)
        return ({"file": "mcp-forge.py", "tool": "forge_call",
                 "args": ["--project-root", project]},
                {"function": "test", "params": {"targets": ["hold"]}})
    # wiki: `measure` runs the command docs/measurements.json names for the
    # page's one measured region -- the request-scoped child _measure_run
    # adopts.  It runs in the repo root, hence the absolute pid path.
    import shlex
    docs = os.path.join(project, "docs")
    os.makedirs(docs, exist_ok=True)
    registry = {"version": 1, "measurements": {"hold": {
        "description": "R-0043 smoke: record the shell pid, then a 30s sleep",
        "command": ["sh", "-c",
                    "echo $$ > %s; exec sleep 30" % shlex.quote(pid_file)]}}}
    with open(os.path.join(docs, "measurements.json"), "w",
              encoding="utf-8") as fh:
        json.dump(registry, fh)
    with open(os.path.join(docs, "hold.md"), "w", encoding="utf-8") as fh:
        fh.write(_KILL_PROBE_PAGE)
    return ({"file": "mcp-wiki.py", "tool": "wiki_call",
             "args": ["--project-root", project]},
            {"function": "measure", "params": {"name": "hold"}})


def inflight_kill_probe(checks, which="forge"):
    """R-0006 phase 2 / R-0043, end to end: a cancelled call KILLS its child.

    mcp-forge and mcp-wiki are `kill`-class servers (tests/test_cancel.py):
    the child a request spawns -- forge's build/test command, wiki's
    measurement command -- is adopted by that request, and the cancel signals
    its process group while the worker thread is still blocked in
    communicate().  A second instance is pointed at a throwaway project under
    .claude/tmp (see _kill_probe_project).  After the cancel the child must be
    gone well inside its 30s, the ping behind the cancel must be answered
    first, and the cancelled id must never be answered.
    """
    import tempfile
    label = "kill probe" if which == "forge" else "%s kill probe" % which
    tmp_root = os.path.join(os.path.dirname(SCRIPT_DIR), ".claude", "tmp")
    os.makedirs(tmp_root, exist_ok=True)
    project = tempfile.mkdtemp(prefix="%s_kill_smoke_" % which, dir=tmp_root)
    pid_file = os.path.join(project, "child.pid")
    cfg, arguments = _kill_probe_project(which, project, pid_file)
    srv = Server(cfg)
    srv.start()
    pid = None
    try:
        srv.send({"jsonrpc": "2.0", "id": "init", "method": "initialize",
                  "params": {}})
        if (srv.read() or {}).get("id") != "init":
            checks.append(check("%s: second instance initializes" % label,
                                False, "alive=%r" % srv.alive()))
            return
        srv.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": cfg["tool"], "arguments": arguments}})
        deadline = time.time() + 10.0
        while pid is None and time.time() < deadline:
            try:
                with open(pid_file, encoding="utf-8") as fh:
                    pid = int(fh.read().strip() or "0") or None
            except (OSError, ValueError):
                pid = None
            if pid is None:
                time.sleep(0.05)
        running = pid is not None and _pid_alive(pid)
        checks.append(check("%s: the child is running in flight" % label,
                            running, "pid=%r" % pid))
        if not running:
            return

        srv.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                  "params": {"requestId": 1, "reason": "smoke kill probe"}})
        srv.send({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        first = srv.read() or {}
        checks.append(check("%s: no reply to the cancel, ping answered" % label,
                            first.get("id") == 2,
                            "first reply=%s" % json.dumps(first)[:160]))

        deadline = time.time() + 8.0
        while _pid_alive(pid) and time.time() < deadline:
            time.sleep(0.05)
        checks.append(check("%s: the cancelled call's child is killed" % label,
                            not _pid_alive(pid),
                            "pid %d still alive %.0fs after the cancel "
                            "(its sleep is 30s)" % (pid, 8.0)))

        srv.send({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        seen = _read_ids(srv, (3,), 10.0)
        seen += _read_ids(srv, ("never",), 1.0)
        checks.append(check("%s: the cancelled call is never answered" % label,
                            1 not in seen and 3 in seen, "ids seen=%r" % seen))
    finally:
        srv.stop()
        if pid is not None and _pid_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        shutil.rmtree(project, ignore_errors=True)


def run_server(cfg):
    """Return (status, checks) where status in {PASS, FAIL, SKIP, ERROR}."""
    srv = Server(cfg)
    srv.start()
    checks = []
    try:
        # 1. initialize
        srv.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init = srv.read()
        if init is None:
            if not srv.alive():
                tail = srv.stderr_tail()
                return ("SKIP", [check("startup", False,
                        "process exited before initialize; stderr tail:\n" + tail)])
            return ("FAIL", [check("initialize", False, "no response (timeout)")])
        res = (init or {}).get("result", {})
        checks.append(check("initialize.protocolVersion",
                            res.get("protocolVersion") == "2024-11-05",
                            repr(res.get("protocolVersion"))))
        checks.append(check("initialize.serverInfo.version",
                            res.get("serverInfo", {}).get("version") == "1.0.0",
                            repr(res.get("serverInfo", {}).get("version"))))

        # 2. notification (no id) -> no reply ; 3. ping right after
        srv.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        srv.send({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        ping = srv.read()
        ping = ping or {}
        checks.append(check("notification->no-extra-reply + ping.id",
                            ping.get("id") == 2,
                            "first reply id=%r (expected 2; a notification reply would shift this)"
                            % ping.get("id")))
        checks.append(check("ping.result=={}", ping.get("result") == {}, repr(ping.get("result"))))

        # 4. tools/list
        srv.send({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        tl = srv.read() or {}
        tools = tl.get("result", {}).get("tools", [])
        checks.append(check("tools/list count==1", len(tools) == 1, "count=%d" % len(tools)))
        checks.append(check("tools/list name",
                            len(tools) == 1 and tools[0].get("name") == cfg["tool"],
                            (tools[0].get("name") if tools else None)))

        # 5. unknown method -> -32601
        srv.send({"jsonrpc": "2.0", "id": 4, "method": "foo/bar", "params": {}})
        unk = srv.read() or {}
        checks.append(check("unknown-method -32601",
                            unk.get("error", {}).get("code") == -32601,
                            repr(unk.get("error", {}).get("code"))))

        # 6. forced handler exception -> -32603 AND a response arrives (FIX-1 gate)
        #    non-dict params makes the dispatcher's params.get(...) raise before
        #    any tool-internal try/except, so it bubbles to the run() catch-all.
        srv.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": "force-error"})
        exc = srv.read()
        if exc is None:
            if srv.alive():
                checks.append(check("forced-exc -32603", False,
                                    "NO RESPONSE (hang) -- silent-swallow bug"))
            else:
                checks.append(check("forced-exc -32603", False,
                                    "process CRASHED -- bare-loop bug"))
        else:
            checks.append(check("forced-exc -32603",
                                exc.get("error", {}).get("code") == -32603,
                                repr(exc.get("error", {}).get("code"))))

        # 7. tool-level error envelope: the isError flag on a FAILING call, and
        #    the control proving a deliberate status reply is not flagged.
        error_envelope_checks(srv, cfg, checks)

        # 8. two spellings of one parameter must be refused, not silently
        #    decided by wire position -- plus the coverage half, which runs on
        #    every server including the five with no resolver.
        alias_collision_checks(srv, cfg, checks)

        # 9. purity-only: semantic dispatch + alias-routing checks (Phase 0, D2)
        if cfg["tool"] == "purity_call":
            purity_semantic_checks(srv, checks)

        # 10. tshark-only: the reply ceiling answers to the fleet spelling,
        #     with the old one kept as an alias (roadmap R-0008).
        if cfg["tool"] == "tshark_call":
            tshark_ceiling_param_checks(srv, cfg, checks)

        # 11. notifications/cancelled naming no live request is never
        #     answered (R-0006), every server.
        cancel_notification_checks(srv, checks)

        # 12. jenkins-only: a cancel that lands on a call really in flight, in
        #     a second instance pointed at a loopback peer.
        if cfg["tool"] == "jenkins_call":
            inflight_cancel_probe(checks)

        # 13. forge-only: a cancel that lands on a build/test call really in
        #     flight kills the child it spawned (R-0006 phase 2, `kill` class).
        if cfg["tool"] == "forge_call":
            inflight_kill_probe(checks)

        # 14. wiki-only: the same for a `measure` call's measurement child
        #     (R-0043, `kill` class).
        if cfg["tool"] == "wiki_call":
            inflight_kill_probe(checks, which="wiki")

        status = "PASS" if all(ok for _, ok, _ in checks) else "FAIL"
        return (status, checks)
    finally:
        srv.stop()


def main():
    selected = sys.argv[1:]
    servers = [s for s in SERVERS if not selected or s["file"] in selected
               or os.path.basename(s["file"]) in selected]
    if not servers:
        print("No matching servers for:", selected)
        return 2

    overall_ok = True
    for cfg in servers:
        try:
            status, checks = run_server(cfg)
        except Exception as exc:
            status, checks = "ERROR", [check("harness", False, "%s: %s" % (type(exc).__name__, exc))]
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "—", "ERROR": "!"}[status]
        print("%s  %-18s %s" % (mark, cfg["file"], status))
        for cname, ok, detail in checks:
            if not ok or status in ("FAIL", "ERROR", "SKIP"):
                flag = "  ok " if ok else "  XX "
                print("%s%-42s %s" % (flag, cname, detail if not ok else ""))
        if status in ("FAIL", "ERROR"):
            overall_ok = False

    print()
    print("RESULT:", "ALL PASS" if overall_ok else "FAILURES PRESENT")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
