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

Usage:
  python3 _mcp_smoke_test.py                 # all servers
  python3 _mcp_smoke_test.py mcp-clangd.py   # one or more specific servers

Exit code 0 iff every non-skipped server passes every check.
"""

import json
import os
import select
import shutil
import subprocess
import sys
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
        self.proc = subprocess.Popen(
            launch_prefix(self.cfg) + [path] + self.cfg["args"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
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

    # (e) _resolve_aliases last-wins (bug #3) observable: 'path' and
    #     'relative_path' both canonicalize to relative_path; the LAST key wins,
    #     so the not-found error must name the canonical (last) value.
    text, _ = _purity_call(srv, cid, "read_file",
                           {"path": "alias_first.txt", "relative_path": "canonical_last.txt"})
    cid += 1
    checks.append(check("purity: _resolve_aliases last-wins",
                        ("canonical_last.txt" in text) and ("alias_first.txt" not in text),
                        text[:120]))

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

        # 8. purity-only: semantic dispatch + alias-routing checks (Phase 0, D2)
        if cfg["tool"] == "purity_call":
            purity_semantic_checks(srv, checks)

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
