#!/usr/bin/env python3
"""Shared plumbing for the prompt-heaven test suites under tests/.

Two deliberately separated layers, because the suites do NOT speak the same
protocol and must not be forced through one abstraction:

  Layer 1 -- protocol agnostic, used by EVERY suite
    Options / parse_options   common CLI surface: positional suite names plus
                              --keep, --show <substr...>, --whitebox, --brief
    Result / Suite            case model, PASS/FAIL/INFO tallies per group,
                              streamed or grouped rendering, failure dump,
                              exit-code contract (0 iff zero failures)
    SuiteReport               one-line-per-suite aggregate record for run.py
    TempWorkspace             tempfile.mkdtemp() sandbox for every fixture a
                              suite needs; removed on exit unless --keep
    run_process               one-shot "run this argv with this stdin" helper
    load_module_from_path     import a loose .py WITHOUT writing __pycache__
    pycache_snapshot          repo-pollution detector
    file_digests / sha256_file    fixture-tamper detector

  Layer 2 -- ONLY for suites that drive an MCP server
    JsonRpcClient             long-lived `python3 Scripts/mcp-*.py` child,
                              initialize + tools/call over line-delimited
                              JSON-RPC 2.0, with a real read timeout

Invariants this module enforces for its callers:
  * repo root is derived from __file__, never from os.getcwd()
  * every child process runs with PYTHONDONTWRITEBYTECODE=1, so no test run
    can drop a .pyc anywhere inside the repo tree
  * fixtures live in a mkdtemp() directory, never in the repo tree

Stdlib only. No third-party dependencies.
"""

import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time

# Never let importing this module (or anything a suite imports) leave a .pyc
# behind inside the repo tree.
sys.dont_write_bytecode = True

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)

PASS = "PASS"
FAIL = "FAIL"
INFO = "INFO"

BANNER = "=" * 72


def repo_path(*parts):
    """Absolute path inside the repo, resolved from __file__ (never getcwd)."""
    return os.path.join(REPO_ROOT, *parts)


def child_env(extra=None):
    """Environment for every child process spawned by a test."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Layer 1: CLI options
# ---------------------------------------------------------------------------

class Options:
    """Common CLI options, shared by run.py and by every standalone suite."""

    def __init__(self, names=(), show=(), keep=False, whitebox=False,
                 brief=False, help=False):
        self.names = list(names)
        self.show = list(show)
        self.keep = bool(keep)
        self.whitebox = bool(whitebox)
        self.brief = bool(brief)
        self.help = bool(help)

    def flag_argv(self):
        """Re-serialise the flags (not the suite names) for a child process."""
        argv = []
        if self.keep:
            argv.append("--keep")
        if self.brief:
            argv.append("--brief")
        if self.whitebox:
            argv.append("--whitebox")
        if self.show:
            argv.append("--show")
            argv.extend(self.show)
        return argv


def parse_options(argv=None):
    """Parse the shared flag set. `--show` greedily eats following non-flags."""
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = Options()
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--keep":
            opts.keep = True
        elif tok == "--brief":
            opts.brief = True
        elif tok == "--whitebox":
            opts.whitebox = True
        elif tok in ("-h", "--help"):
            opts.help = True
        elif tok == "--show":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                opts.show.append(argv[i])
                i += 1
            continue
        elif tok.startswith("-"):
            raise SystemExit("unknown flag: %s" % tok)
        else:
            opts.names.append(tok)
        i += 1
    return opts


# ---------------------------------------------------------------------------
# Layer 1: case / result model
# ---------------------------------------------------------------------------

class Result:
    """One recorded case.

    `status` is the DISPLAY verdict (PASS / FAIL / INFO).  Pass/fail accounting
    is driven by `problems` alone, so an INFO case that trips a hard invariant
    (non-zero exit, dirty stderr) is still counted as a failure while keeping
    its informational label.
    """

    def __init__(self, group, cid, status, problems=(), detail=(), brief="",
                 text=""):
        self.group = group
        self.cid = cid
        self.status = status
        self.problems = list(problems)
        self.detail = list(detail)
        self.brief = brief
        self.text = text

    @property
    def ok(self):
        return not self.problems

    @property
    def informational(self):
        return self.status == INFO


class SuiteReport:
    """What run.py aggregates: one record per suite."""

    def __init__(self, name, ok, count, unit="cases", passed=0, failed=0,
                 info=0, output="", note=""):
        self.name = name
        self.ok = bool(ok)
        self.count = count
        self.unit = unit
        self.passed = passed
        self.failed = failed
        self.info = info
        self.output = output
        self.note = note

    def line(self):
        bits = ["%d %s" % (self.count, self.unit)]
        if self.unit == "cases":
            bits.append("%d pass" % self.passed)
            bits.append("%d fail" % self.failed)
            if self.info:
                bits.append("%d info" % self.info)
        if self.note:
            bits.append(self.note)
        return "%-20s %-10s %s" % (self.name,
                                   "ALL PASS" if self.ok else "FAILURES",
                                   ", ".join(bits))


class Suite:
    """Records cases, tallies them per group, renders them, sets the exit code.

    Two rendering modes, one per ported driver, so neither loses its output
    shape:
      "stream"  -- print a one-line verdict as each case is recorded
      "grouped" -- buffer everything, then emit `=== group (pass/fail/info) ===`
                   blocks with the per-case detail lines underneath
    """

    def __init__(self, name, title="", opts=None, mode="stream",
                 group_width=3, cid_width=28, unit="cases"):
        self.name = name
        self.title = title
        self.opts = opts or Options()
        self.mode = mode
        self.group_width = group_width
        self.cid_width = cid_width
        self.unit = unit
        self.results = []
        self._rendered = False

    # -- recording ---------------------------------------------------------

    def record(self, group, cid, problems=(), status=None, detail=(), brief="",
               text="", showable=False):
        problems = list(problems)
        if status is None:
            status = FAIL if problems else PASS
        res = Result(group, cid, status, problems, detail, brief, text)
        self.results.append(res)
        if self.mode == "stream":
            self._print_stream(res)
            if showable:
                self.maybe_show(cid, text)
        return res

    def note(self, line):
        """Print an informational line that is NOT a case."""
        print(line)

    def maybe_show(self, cid, text):
        """`--show <substr>`: dump the full server reply for matching cases."""
        if text and any(s in cid for s in self.opts.show):
            print("---- %s ----\n%s\n----" % (cid, text))

    # -- rendering ---------------------------------------------------------

    def _print_stream(self, res):
        print("[%s] %-*s %-*s %s" % (
            res.status, self.group_width, res.group, self.cid_width, res.cid,
            "" if res.ok else "; ".join(res.problems)))

    def render(self):
        """Emit the buffered per-case output (grouped mode). Idempotent."""
        if self._rendered:
            return
        self._rendered = True
        if self.mode == "grouped":
            self._render_grouped()

    def _render_grouped(self):
        for group in self.group_order():
            rows = [r for r in self.results if r.group == group]
            npass = sum(1 for r in rows if r.ok and not r.informational)
            nfail = sum(1 for r in rows if not r.ok)
            ninfo = sum(1 for r in rows if r.informational)
            print("\n=== %s  (pass %d, fail %d, info %d) ===" % (
                group, npass, nfail, ninfo))
            for res in rows:
                if self.opts.brief:
                    print(res.brief or "%s | %s" % (res.status, res.cid))
                    continue
                print("[%s] %s" % (res.status, res.cid))
                for line in res.detail:
                    print("        %s" % line)
                for problem in res.problems:
                    print("        PROBLEM     : %s" % problem)

    # -- tallies -----------------------------------------------------------

    def group_order(self):
        return sorted({r.group for r in self.results})

    def group_tally(self, group):
        rows = [r for r in self.results if r.group == group]
        return (len(rows),
                sum(1 for r in rows if r.ok and not r.informational),
                sum(1 for r in rows if not r.ok),
                sum(1 for r in rows if r.informational))

    def totals(self):
        return (len(self.results),
                sum(1 for r in self.results if r.ok and not r.informational),
                sum(1 for r in self.results if not r.ok),
                sum(1 for r in self.results if r.informational))

    @property
    def failures(self):
        return [r for r in self.results if not r.ok]

    @property
    def exit_code(self):
        return 1 if self.failures else 0

    def print_summary(self, failure_text_limit=1400):
        self.render()
        groups = self.group_order()
        width = max([len(g) for g in groups] + [5])
        print("\n" + BANNER)
        if self.title:
            print("  %s" % self.title)
        for group in groups:
            n, npass, nfail, ninfo = self.group_tally(group)
            print("  %-*s %3d cases  %3d pass  %3d fail  %3d info" % (
                width, group, n, npass, nfail, ninfo))
        n, npass, nfail, ninfo = self.totals()
        print("  %-*s %3d cases  %3d pass  %3d fail  %3d info" % (
            width, "TOTAL", n, npass, nfail, ninfo))
        print("VERDICT: %s" % ("ALL GREEN" if not nfail else "FAILURES"))
        if nfail:
            print("\nFAILURES:")
            for res in self.failures:
                print("  [%s] %s: %s" % (res.group, res.cid,
                                         "; ".join(res.problems)))
                if res.text:
                    body = res.text.replace("\n", "\n      ")
                    print("      " + body[:failure_text_limit])

    def report(self):
        n, npass, nfail, ninfo = self.totals()
        return SuiteReport(self.name, not nfail, n, self.unit, npass, nfail,
                           ninfo)


# ---------------------------------------------------------------------------
# Layer 1: temp workspace -- no test may write inside the repo tree
# ---------------------------------------------------------------------------

class TempWorkspace:
    """A mkdtemp() sandbox for a suite's fixtures.

    Removed on exit unless --keep, in which case the path is printed so a
    failing run can be inspected by hand.
    """

    def __init__(self, prefix, keep=False):
        self.keep = bool(keep)
        self.path = tempfile.mkdtemp(prefix=prefix)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    def join(self, *parts):
        return os.path.join(self.path, *parts)

    def subdir(self, *parts):
        target = self.join(*parts)
        os.makedirs(target, exist_ok=True)
        return target

    def write_text(self, name, body, encoding="utf-8"):
        target = self.join(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding=encoding) as fh:
            fh.write(body)
        return target

    def write_bytes(self, name, blob):
        target = self.join(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(blob)
        return target

    def cleanup(self):
        if self.keep:
            print("\n[--keep] fixtures retained at: %s" % self.path)
            return
        shutil.rmtree(self.path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Layer 1: one-shot process helper + repo-hygiene probes
# ---------------------------------------------------------------------------

def run_process(argv, stdin_text="", timeout=30, cwd=None):
    """Run argv once with `stdin_text` on stdin; return (rc, stdout, stderr).

    stdin is ALWAYS supplied (default: empty), never inherited.
    """
    proc = subprocess.run(list(argv), input=stdin_text, capture_output=True,
                          text=True, timeout=timeout, cwd=cwd, env=child_env())
    return proc.returncode, proc.stdout, proc.stderr


def load_module_from_path(name, path):
    """Import a loose .py file WITHOUT letting it write a __pycache__ entry."""
    import importlib.util
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


def pycache_snapshot(root=REPO_ROOT):
    """path -> mtime for every file under a __pycache__ dir in the repo tree."""
    snap = {}
    for dirpath, _dirs, files in os.walk(root):
        if ".git" in dirpath.split(os.sep):
            continue
        if os.path.basename(dirpath) == "__pycache__":
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    snap[path] = round(os.path.getmtime(path), 3)
                except OSError:
                    pass
    return snap


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_digests(dirpath):
    """name -> sha256 for every regular file directly inside `dirpath`."""
    out = {}
    if not os.path.isdir(dirpath):
        return out
    for name in sorted(os.listdir(dirpath)):
        path = os.path.join(dirpath, name)
        if os.path.isfile(path):
            out[name] = sha256_file(path)
    return out


# ---------------------------------------------------------------------------
# Layer 2: JSON-RPC over a long-lived MCP server child (MCP suites only)
# ---------------------------------------------------------------------------

class JsonRpcError(RuntimeError):
    pass


class JsonRpcClient:
    """Line-delimited JSON-RPC 2.0 client for one long-lived MCP server child.

    Exactly one request is in flight at a time, so `read` never has to cope
    with leftover buffered lines: select() on the child's stdout is enough to
    turn a hung server into a timeout instead of a deadlock.
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, argv, tool=None, cwd=None, timeout=30.0,
                 client_name="ph-tests"):
        self.argv = list(argv)
        self.tool = tool
        self.timeout = float(timeout)
        self.proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd,
            env=child_env(),
        )
        self._id = 0
        self.rpc("initialize", {"protocolVersion": self.PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": {"name": client_name,
                                               "version": "1"}})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def rpc(self, method, params=None):
        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method,
                   "params": params if params is not None else {}}
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        return self._read_response(method)

    def call_tool(self, function, params=None, tool=None):
        """`tools/call` a *_call dispatcher; return (is_error, markdown_text)."""
        response = self.rpc("tools/call", {
            "name": tool or self.tool,
            "arguments": {"function": function, "params": params or {}},
        })
        result = response.get("result") or {}
        content = result.get("content") or [{}]
        text = content[0].get("text", "") if content else ""
        return bool(result.get("isError")), text

    def _read_response(self, method):
        deadline = time.time() + self.timeout
        while True:
            if self.proc.poll() is not None:
                rest = self.proc.stdout.readline()
                if rest.strip():
                    return json.loads(rest)
                raise JsonRpcError("server exited during %r; stderr=%r"
                                   % (method, self._drain_stderr()))
            remaining = deadline - time.time()
            if remaining <= 0:
                raise JsonRpcError("timeout after %.1fs waiting for %r"
                                   % (self.timeout, method))
            ready, _, _ = select.select([self.proc.stdout], [], [],
                                        min(0.25, remaining))
            if not ready:
                continue
            line = self.proc.stdout.readline()
            if not line:
                raise JsonRpcError("server closed stdout during %r; stderr=%r"
                                   % (method, self._drain_stderr()))
            if not line.strip():
                continue
            return json.loads(line)

    def _drain_stderr(self, limit=2000):
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            return (self.proc.stderr.read() or "")[-limit:]
        except Exception:
            return ""

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
