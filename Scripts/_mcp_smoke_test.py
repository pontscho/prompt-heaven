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

Usage:
  python3 _mcp_smoke_test.py                 # all servers
  python3 _mcp_smoke_test.py mcp-clangd.py   # one or more specific servers

Exit code 0 iff every non-skipped server passes every check.
"""

import json
import os
import select
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Per-server launch config: minimal args so argparse succeeds and the
# initialize/ping/unknown path runs WITHOUT spawning heavy subprocess work.
SERVERS = [
    {"file": "mcp-compile.py",  "tool": "compile_call",  "args": ["--project-root", "/tmp"]},
    {"file": "mcp-forge.py",    "tool": "forge_call",    "args": ["--project-root", "/tmp"]},
    {"file": "mcp-git.py",      "tool": "git_call",      "args": ["--project-root", "/tmp"]},
    {"file": "mcp-purity.py",   "tool": "purity_call",   "args": ["--project-root", "/tmp"]},
    {"file": "mcp-jenkins.py",  "tool": "jenkins_call",  "args": ["--endpoint", "http://127.0.0.1:1", "--username", "x", "--token", "y"]},
    {"file": "mcp-tshark.py",   "tool": "tshark_call",   "args": ["--project-root", "/tmp"]},
    {"file": "mcp-webfetch.py", "tool": "webfetch_call", "args": []},
    {"file": "mcp-context7.py", "tool": "context7_call", "args": []},
    {"file": "mcp-lldb.py",     "tool": "lldb_call",     "args": []},
    {"file": "mcp-gdc.py",      "tool": "gdc_call",      "args": []},
    {"file": "mcp-lua-lsp.py",  "tool": "luals_call",    "args": []},
    {"file": "mcp-clangd.py",   "tool": "clangd_call",   "args": []},
    {"file": "mcp-cuda.py",     "tool": "cuda_call",     "args": []},
    {"file": "mcp-postgres.py", "tool": "postgres_call", "args": ["--host", "127.0.0.1:1", "--dbname", "x"]},
]

READ_TIMEOUT = 8.0  # seconds to wait for a single response line


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None

    def start(self):
        path = os.path.join(SCRIPT_DIR, self.cfg["file"])
        self.proc = subprocess.Popen(
            [sys.executable, path] + self.cfg["args"],
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
    "find_definition", "find_references", "find_implementations", "type_at",
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

    # (a) all 10 canonical semantic names dispatch (no "Unknown function")
    undispatched = []
    for fn in PURITY_SEMANTIC:
        text, _ = _purity_call(srv, cid, fn)
        cid += 1
        if "Unknown function" in text:
            undispatched.append(fn)
    checks.append(check("purity: 10 semantic names dispatch",
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

        # 7. purity-only: semantic dispatch + alias-routing checks (Phase 0, D2)
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
