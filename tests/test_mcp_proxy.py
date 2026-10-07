#!/usr/bin/env python3
"""`Scripts/mcp-proxy.py` relays, it never composes (A-L).

WHAT IS GATED
-------------
`Scripts/mcp-proxy.py` is the one stdio MCP endpoint that aggregates several
fleet servers (its "children") under their own tool names.  Everything the
proxy promises is a property of a RELAY: a child's listing and results reach
the caller byte-for-byte, a caller's id, cancel and progress token are mapped
to the child's own and back, a dead child becomes an `isError` result naming
it, and no child or grandchild outlives the proxy.  This suite drives the real
proxy as a subprocess in front of the adversarial stub
`tests/files/mcp_proxy/tf_stub_child.py` (flags and `tf_*` functions in its
docstring), plus an in-process half for the config parser, the stop ladder and
a static AST pass over the proxy source.

WHY THE SUITE HAS ITS OWN CLIENT
--------------------------------
`_harness.JsonRpcClient` allows exactly ONE request in flight (P14).  Routing,
cancel and progress are only observable with several calls outstanding at
once, so `ProxyProc` below is a multi-in-flight client: a daemon reader thread
parses every stdout line into a queue, and `wait_for` keeps the messages a
caller did not ask for, so arrival ORDER is the observable, never elapsed time.
The proxy's stderr goes to a file in the sandbox, never an undrained PIPE: the
children inherit it, and a full pipe would block them.

Every live case has a hard deadline and kills the proxy and the stubs in a
`finally`, using the stub's `.pids` file to verify.  Timing is FAIL only behind
generous ceilings (`tests/_harness.py:54-56`).

SANDBOX DISCIPLINE -- all fixtures (config files written mode 0600, stub state
directories, the proxy's stderr) live under
`.claude/tmp/test_mcp_proxy/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  Removed in
a `finally` unless --keep.

The case count is TYPED in run.py's SUITES table: this is a fixed case table,
so a count that moves is the alarm.  The total below is the expected value; the
run is authoritative.

Total: 106 cases (A 13, B 7, C 6, D 4, E 4, F 5, G 7, H 1, I 6, J 45, K 4,
L 4; the round-1/2/3 reviews added A13, G6, G7, J34-J42 and L4; R-0069 added
J43, R-0076 J44, R-0075 J45).

Usage:
  python3 tests/test_mcp_proxy.py
  python3 tests/test_mcp_proxy.py --brief
  python3 tests/test_mcp_proxy.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  config    -- load_config / build_child_argv / build_child_env, in-process
  B  startup   -- listing, duplicate tools, crash on init, pagination, token
  C  routing   -- concurrent calls, id types, unknown tool / method, relay
  D  cancel    -- forwarded with the child's own id, late cancel, noise
  E  progress  -- token mapping both ways, after-result drop, strip
  F  restart   -- death mid-call, backoff, new pid, toolset, restart budget
  G  framing   -- 5 MiB result, frame limit, pollution, child requests,
                  a deeply nested upstream line, an id of the wrong type
  H  timeout   -- call_timeout cancels the child call
  I  shutdown  -- stdin EOF, grandchild sweep, SIGTERM, the stop ladder
  J  http      -- refuse-to-start rules, auth, Origin/Host, framing refusals,
                  sessions, SSE vs JSON, disconnect vs cancel, eviction, caps;
                  constant-time bearer and structure-only handler logs (AST),
                  no token / session id in the logs, Origin allowlist,
                  pre-auth timeout, bounded sink, SSE keepalive, the 202
                  loop half, SIGTERM with live HTTP traffic; the total
                  header deadline, deep nesting, repeated and near-miss
                  headers, no session left by a refused initialize,
                  malformed header lines, an id-less initialize, an id that
                  is not a string or integer (initialize and a deep list id)
  K  static    -- AST over the proxy source: spawn shape, logging, API floor
  L  hygiene   -- every write under .claude/tmp, no bytecode, no new repo paths,
                  _log_value marks every cut
"""

import ast
import asyncio
import hashlib
import http.client
import io
import json
import logging
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402
import test_wire_log as WL  # noqa: E402  -- K3 reuses its taint machinery

NAME = "mcp_proxy"

SERVER = H.repo_path("Scripts", "mcp-proxy.py")
STUB = H.repo_path("tests", "files", "mcp_proxy", "tf_stub_child.py")
STUB_DEFAULT_TOOL = "tf_stub_call"

GA = "A. config (in-process)"
GB = "B. startup"
GC = "C. routing"
GD = "D. cancel"
GE = "E. progress"
GF = "F. death and restart"
GG = "G. framing"
GH = "H. timeout"
GI = "I. shutdown"
GJ = "J. Streamable HTTP"
GK = "K. static AST over the proxy"
GL = "L. hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_mcp_proxy")
WRITES = []

PROTOCOL_VERSION = "2024-11-05"

# Generous ceilings: a timing assertion is a FAIL only past these.
START_TIMEOUT_S = 20.0       # proxy up + every child initialized and listed
REPLY_TIMEOUT_S = 15.0       # one ordinary request/response round trip
CLOSE_TIMEOUT_S = 15.0       # stdin closed -> proxy exited
KILL_TIMEOUT_S = 5.0         # after SIGKILL
PID_DEAD_TIMEOUT_S = 10.0    # polling os.kill(pid, 0) until ProcessLookupError

_EOF = object()              # the reader thread's end-of-stream sentinel


# ---------------------------------------------------------------------------
# Sandbox and config helpers
# ---------------------------------------------------------------------------

def new_sandbox(fixture_root, label):
    """A fresh per-case directory under the run's fixture root."""
    path = tempfile.mkdtemp(prefix=label + "-", dir=fixture_root)
    WRITES.append(path)
    return path


def write_file(path, text, mode=0o600):
    """Write *text* to *path* with *mode* (also on an existing file); recorded in WRITES."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    WRITES.append(path)
    return path


def write_config(sandbox, children, filename="proxy.json", **top):
    """Write a proxy config (mode 0600) into *sandbox* and return its path.

    *children* is a list of child dicts (see `stub_child`); every other
    top-level key (`frame_limit`, `root`, ...) comes from **top.
    """
    data = dict(top)
    data["children"] = list(children)
    return write_file(os.path.join(sandbox, filename),
                      json.dumps(data, indent=2, sort_keys=True) + "\n")


def stub_child(name, *flags, **keys):
    """A config child entry that runs the stub: `python -B STUB <flags...>`.

    Extra child keys (`call_timeout`, `env`, ...) come from **keys.
    """
    entry = {
        "name": name,
        "command": sys.executable,
        "args": ["-B", STUB] + [str(f) for f in flags],
    }
    entry.update(keys)
    return entry


def read_pids(state_dir, tool0=STUB_DEFAULT_TOOL):
    """Every pid the stub recorded in `<state_dir>/<tool0>.pids`, in spawn order."""
    path = os.path.join(state_dir, tool0 + ".pids")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [int(ln) for ln in (s.strip() for s in fh) if ln.isdigit()]
    except FileNotFoundError:
        return []


def read_events(state_dir, tool0=STUB_DEFAULT_TOOL):
    """Every JSON event the stub recorded in `<state_dir>/<tool0>.events.jsonl`."""
    path = os.path.join(state_dir, tool0 + ".events.jsonl")
    events = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return events


def pid_alive(pid):
    """True while *pid* exists (a zombie awaiting its reaper counts as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_dead(pid, timeout=PID_DEAD_TIMEOUT_S):
    """Poll `os.kill(pid, 0)` until ProcessLookupError; True iff dead within *timeout*."""
    deadline = time.monotonic() + timeout
    while True:
        if not pid_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def kill_pids(pids):
    """Best-effort SIGKILL of every pid still alive (a case's `finally`)."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def tool_text(msg):
    """The concatenated text content of a tools/call result, or "" if none."""
    result = msg.get("result") if isinstance(msg, dict) else None
    if not isinstance(result, dict):
        return ""
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def is_error_result(msg):
    """True iff *msg* is a tools/call result with isError true."""
    result = msg.get("result") if isinstance(msg, dict) else None
    return isinstance(result, dict) and result.get("isError") is True


# ---------------------------------------------------------------------------
# ProxyProc -- a multi-in-flight client for one live proxy
# ---------------------------------------------------------------------------

class ProxyProc:
    """One live `mcp-proxy.py` over stdio, with any number of requests in flight.

    A daemon reader thread parses each stdout line into a queue (an
    unparseable line is queued as `{"_unparseable": text}`).  `wait_for`
    returns the first message matching a predicate and KEEPS the others, so a
    later wait still sees them, in arrival order.
    """

    def __init__(self, sandbox, cfgpath, extra_argv=(), extra_env=None,
                 label="proxy"):
        self.sandbox = sandbox
        self.stderr_path = os.path.join(sandbox, label + ".stderr")
        WRITES.append(self.stderr_path)
        self._err = open(self.stderr_path, "wb")
        self.argv = [sys.executable, "-B", SERVER, "--project-root", sandbox,
                     "--config", cfgpath] + list(extra_argv)
        self._queue = queue.Queue()
        self._kept = []
        self.eof = False
        self.arrivals = []             # every parsed message, in arrival order
        self._closed = False
        try:
            self.proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._err, cwd=sandbox, env=H.child_env(extra_env),
            )
        except Exception:
            self._err.close()
            raise
        self._reader = threading.Thread(target=self._read_loop,
                                        name="tf-proxy-reader", daemon=True)
        self._reader.start()

    # -- wire ----------------------------------------------------------------

    def _read_loop(self):
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    msg = {"_unparseable": text}
                self._queue.put(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._queue.put(_EOF)

    def send(self, obj):
        """Write one JSON-RPC line; False if the proxy's stdin is gone."""
        line = obj if isinstance(obj, (bytes, bytearray)) else \
            (json.dumps(obj) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def request(self, method, params=None, rid=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if rid is not None:
            msg["id"] = rid
        return self.send(msg)

    def notify(self, method, params=None):
        return self.request(method, params)

    def call(self, rid, tool, function=None, params=None, meta=None):
        """Send a tools/call for *tool* with `{"function", "params"}` arguments."""
        arguments = {}
        if function is not None:
            arguments["function"] = function
        if params is not None:
            arguments["params"] = params
        p = {"name": tool, "arguments": arguments}
        if meta is not None:
            p["_meta"] = meta
        return self.request("tools/call", p, rid)

    def cancel(self, rid, reason="tf cancel"):
        return self.notify("notifications/cancelled",
                           {"requestId": rid, "reason": reason})

    # -- receive -------------------------------------------------------------

    def _take(self, timeout):
        """Next queued message, or None on timeout / EOF."""
        if self.eof:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                return None
        else:
            try:
                msg = self._queue.get(timeout=max(0.0, timeout))
            except queue.Empty:
                return None
        if msg is _EOF:
            self.eof = True
            return None
        self.arrivals.append(msg)
        return msg

    def wait_for(self, pred, timeout=REPLY_TIMEOUT_S):
        """First message matching *pred* within *timeout*, else None.

        Already-kept messages are searched first; every non-matching message
        received meanwhile is kept for a later wait.
        """
        for i, msg in enumerate(self._kept):
            if pred(msg):
                return self._kept.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not self.eof:
                return None
            msg = self._take(remaining)
            if msg is None:
                if self.eof or time.monotonic() >= deadline:
                    return None
                continue
            if pred(msg):
                return msg
            self._kept.append(msg)

    def wait_id(self, rid, timeout=REPLY_TIMEOUT_S):
        """The response (not a request) whose id equals *rid* in type and value."""
        def match(msg):
            return ("method" not in msg and "id" in msg
                    and type(msg["id"]) is type(rid) and msg["id"] == rid)
        return self.wait_for(match, timeout)

    def drain_all(self, timeout=0.0):
        """Every kept and queued message (waiting up to *timeout* for more)."""
        out = list(self._kept)
        del self._kept[:]
        deadline = time.monotonic() + timeout
        while True:
            msg = self._take(max(0.0, deadline - time.monotonic()))
            if msg is None:
                if self.eof or time.monotonic() >= deadline:
                    return out
                continue
            out.append(msg)

    def stderr_text(self):
        try:
            self._err.flush()
        except (OSError, ValueError):
            pass
        try:
            with open(self.stderr_path, "r", encoding="utf-8",
                      errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    # -- session -------------------------------------------------------------

    def initialize(self, rid="tf-init", timeout=START_TIMEOUT_S):
        """initialize + notifications/initialized; returns the reply (or None)."""
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ph-tests-mcp-proxy", "version": "1"},
        }, rid)
        reply = self.wait_id(rid, timeout)
        if reply is not None:
            self.notify("notifications/initialized")
        return reply

    def list_tools(self, rid="tf-list", timeout=REPLY_TIMEOUT_S):
        """tools/list reply's tool list, or None."""
        self.request("tools/list", {}, rid)
        reply = self.wait_id(rid, timeout)
        result = reply.get("result") if isinstance(reply, dict) else None
        if not isinstance(result, dict):
            return None
        return result.get("tools")

    # -- lifecycle -----------------------------------------------------------

    @property
    def pid(self):
        return self.proc.pid

    def poll(self):
        return self.proc.poll()

    def close_stdin(self):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def wait(self, timeout):
        """The exit code within *timeout*, else None."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate(self):
        try:
            self.proc.send_signal(signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    def close(self, timeout=CLOSE_TIMEOUT_S):
        """Close stdin, wait up to *timeout*, then SIGTERM, then SIGKILL.

        Idempotent; returns the exit code (None if even SIGKILL did not reap).
        """
        if self._closed:
            return self.proc.returncode
        self._closed = True
        rc = None
        try:
            self.close_stdin()
            rc = self.wait(timeout)
            if rc is None:
                self.terminate()
                rc = self.wait(KILL_TIMEOUT_S)
            if rc is None:
                try:
                    self.proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                rc = self.wait(KILL_TIMEOUT_S)
        finally:
            self._reader.join(KILL_TIMEOUT_S)
            try:
                self.proc.stdout.close()
            except (OSError, ValueError):
                pass
            try:
                self._err.close()
            except OSError:
                pass
        return rc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# A. config, in-process  (13 cases)
# ---------------------------------------------------------------------------

GA_CASES = (
    "valid config loads",                 # A1
    "unknown top-level key refused",      # A2
    "unknown child key refused",          # A3
    "duplicate child name refused",       # A4
    "bad command refused",                # A5
    "non-string args refused",            # A6
    "argv is wrapper+command+args",       # A7
    "a;b stays one argv element",         # A8
    "child env drops the token",          # A9
    "group-writable config refused",      # A10
    "bad or conflicting root refused",    # A11
    "call_timeout out of range refused",  # A12
    "deep nesting refused, not echoed",   # A13
)


def refused(mod, fn, needles, label):
    """Problems unless fn() raises mod.ConfigError whose message holds every needle."""
    try:
        fn()
    except mod.ConfigError as exc:
        msg = str(exc)
        missing = [n for n in needles if n not in msg]
        if missing:
            return ["%s: ConfigError %r does not name %r" % (label, msg, missing)]
        return []
    except Exception as exc:  # noqa: BLE001 -- any other type is the finding
        return ["%s: raised %s instead of ConfigError: %s"
                % (label, type(exc).__name__, exc)]
    return ["%s: accepted, expected a ConfigError" % label]


def inline_config(children, **top):
    """A config as a JSON string, for load_config's *inline* source."""
    data = dict(top)
    data["children"] = list(children)
    return json.dumps(data)


def group_a(suite, fixture_root):
    """A. load_config / build_child_argv / build_child_env, imported in-process.

    Every refusal must be a ConfigError whose message names the key or the
    child; every limit is read from the module, never typed here.
    """
    try:
        mod = H.load_module_from_path("ph_proxy", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GA_CASES:
            suite.record(GA, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT),
                                      type(exc).__name__, exc)])
        return

    sandbox = new_sandbox(fixture_root, "config")
    root = os.path.realpath(sandbox)

    def load_inline(children, **top):
        return lambda: mod.load_config(None, inline_config(children, **top), sandbox)

    # A1 -- a valid file config: fields, argv with {root} substituted, root.
    problems = []
    cfg_path = write_config(sandbox, [
        stub_child("alpha", "{root}"),
        stub_child("beta", call_timeout=5, startup_timeout=7),
    ], root=sandbox)
    try:
        cfg = mod.load_config(cfg_path, None, None)
    except Exception as exc:  # noqa: BLE001
        problems.append("refused: %s: %s" % (type(exc).__name__, exc))
    else:
        if cfg.root != root:
            problems.append("root %r != %r" % (cfg.root, root))
        names = [c.name for c in cfg.children]
        if names != ["alpha", "beta"]:
            problems.append("children %r != ['alpha', 'beta']" % names)
        else:
            alpha, beta = cfg.children
            want = (sys.executable, "-B", STUB, root)
            if alpha.argv != want:
                problems.append("alpha argv %r != %r" % (alpha.argv, want))
            if alpha.call_timeout != mod._DEFAULT_CALL_TIMEOUT_S:
                problems.append("alpha call_timeout %r != the module default"
                                % alpha.call_timeout)
            if alpha.startup_timeout != mod._DEFAULT_STARTUP_TIMEOUT_S:
                problems.append("alpha startup_timeout %r != the module default"
                                % alpha.startup_timeout)
            if (beta.call_timeout, beta.startup_timeout) != (5.0, 7.0):
                problems.append("beta timeouts %r != (5.0, 7.0)"
                                % ((beta.call_timeout, beta.startup_timeout),))
            if alpha.env != {}:
                problems.append("alpha env %r != {}" % alpha.env)
        if cfg.max_frame != mod._MAX_FRAME:
            problems.append("max_frame %r != _MAX_FRAME" % cfg.max_frame)
    suite.record(GA, GA_CASES[0], problems,
                 detail=["config      : file, mode 0600, root in the config",
                         "argv        : {root} -> realpath of the sandbox"])

    # A2 -- an unknown top-level key.
    suite.record(GA, GA_CASES[1], refused(
        mod, load_inline([stub_child("alpha")], tf_bogus_top=1),
        ["config", "tf_bogus_top"], "unknown top key"))

    # A3 -- an unknown child key.
    suite.record(GA, GA_CASES[2], refused(
        mod, load_inline([stub_child("alpha", tf_bogus_child=1)]),
        ["children[0]", "tf_bogus_child"], "unknown child key"))

    # A4 -- two children with the same name.
    suite.record(GA, GA_CASES[3], refused(
        mod, load_inline([stub_child("alpha"), stub_child("alpha")]),
        ["children[1]", "duplicate", "'alpha'"], "duplicate name"))

    # A5 -- a relative command containing "/" and an unresolvable bare name.
    bare = "tf-no-such-command-for-mcp-proxy"
    problems = []
    problems += refused(
        mod, load_inline([{"name": "alpha", "command": "bin/tf-tool"}]),
        ["child 'alpha'", "command", "relative"], "relative command")
    if shutil.which(bare) is None:
        problems += refused(
            mod, load_inline([{"name": "alpha", "command": bare}]),
            ["child 'alpha'", "command", "PATH"], "unresolvable bare name")
    else:
        problems.append("control: %r unexpectedly resolves on PATH" % bare)
    suite.record(GA, GA_CASES[4], problems,
                 detail=["relative    : bin/tf-tool (contains '/')",
                         "bare        : %s (not on PATH)" % bare])

    # A6 -- args holding a non-string.
    suite.record(GA, GA_CASES[5], refused(
        mod, load_inline([dict(stub_child("alpha"), args=["-B", 1])]),
        ["child 'alpha'", "args"], "non-str args"))

    # A7 -- argv composition, directly and through a configured wrapper.
    problems = []
    direct = mod.build_child_argv(["/usr/bin/env", "-i"], "/bin/echo", ["x", "y"])
    if direct != ("/usr/bin/env", "-i", "/bin/echo", "x", "y"):
        problems.append("build_child_argv returned %r" % (direct,))
    try:
        cfg = mod.load_config(None, inline_config(
            [stub_child("alpha", "--tf", wrapper=["/usr/bin/env", "-i"])]), sandbox)
        got = cfg.children[0].argv
        want = ("/usr/bin/env", "-i", sys.executable, "-B", STUB, "--tf")
        if got != want:
            problems.append("configured argv %r != %r" % (got, want))
    except Exception as exc:  # noqa: BLE001
        problems.append("wrapper config refused: %s: %s" % (type(exc).__name__, exc))
    suite.record(GA, GA_CASES[6], problems,
                 detail=["wrapper     : /usr/bin/env -i (absolute, kept as written)"])

    # A8 -- a shell metacharacter is data, never a split point.
    problems = []
    argv = mod.build_child_argv([], "/bin/echo", ["a;b"])
    if argv != ("/bin/echo", "a;b"):
        problems.append("build_child_argv split or quoted 'a;b': %r" % (argv,))
    try:
        cfg = mod.load_config(None, inline_config([stub_child("alpha", "a;b")]), sandbox)
        tail = cfg.children[0].argv[-1:]
        if tail != ("a;b",) or "a" in cfg.children[0].argv:
            problems.append("configured argv %r" % (cfg.children[0].argv,))
    except Exception as exc:  # noqa: BLE001
        problems.append("config refused: %s: %s" % (type(exc).__name__, exc))
    suite.record(GA, GA_CASES[7], problems)

    # A9 -- build_child_env drops the token and applies overrides; a config
    # env naming the token is refused.
    problems = []
    token_env = mod._TOKEN_ENV
    saved = {k: os.environ.get(k) for k in (token_env, "TF_PROXY_OVERRIDE")}
    try:
        os.environ[token_env] = "tf-secret-token"
        os.environ["TF_PROXY_OVERRIDE"] = "old"
        env = mod.build_child_env({"TF_PROXY_OVERRIDE": "new", "TF_PROXY_ADDED": "1"})
        if token_env in env:
            problems.append("%s survived into the child env" % token_env)
        if env.get("TF_PROXY_OVERRIDE") != "new":
            problems.append("override not applied: %r" % env.get("TF_PROXY_OVERRIDE"))
        if env.get("TF_PROXY_ADDED") != "1":
            problems.append("added var missing: %r" % env.get("TF_PROXY_ADDED"))
        if env.get("PATH") != os.environ.get("PATH"):
            problems.append("the proxy's own PATH was not inherited")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    problems += refused(
        mod, load_inline([stub_child("alpha", env={token_env: "x"})]),
        ["child 'alpha'", "env", token_env], "config env naming the token")
    suite.record(GA, GA_CASES[8], problems,
                 detail=["token var   : %s (read from the module)" % token_env])

    # A10 -- a group-writable (and a world-writable) config file.
    problems = []
    for mode in (0o620, 0o602):
        path = write_file(os.path.join(sandbox, "proxy-%o.json" % mode),
                          inline_config([stub_child("alpha")], root=sandbox),
                          mode=mode)
        problems += refused(mod, lambda p=path: mod.load_config(p, None, None),
                            ["--config", "writable"], "mode %o" % mode)
    suite.record(GA, GA_CASES[9], problems,
                 detail=["modes       : 0620, 0602 (set with fchmod, umask-proof)"])

    # A11 -- a root that is not a directory; a --project-root that disagrees
    # with the config's root.
    problems = []
    not_dir = write_file(os.path.join(sandbox, "tf-not-a-dir"), "x\n")
    problems += refused(
        mod, lambda: mod.load_config(None, inline_config(
            [stub_child("alpha")], root=not_dir), None),
        ["project root", "directory"], "root is a file")
    other = new_sandbox(fixture_root, "other-root")
    problems += refused(
        mod, lambda: mod.load_config(None, inline_config(
            [stub_child("alpha")], root=other), sandbox),
        ["--project-root", "root"], "differing roots")
    suite.record(GA, GA_CASES[10], problems)

    # A12 -- call_timeout 0, True, and one past the module's upper bound; the
    # bound itself is the accepted control.
    problems = []
    limit = mod._MAX_CALL_TIMEOUT_S
    for value in (0, True, limit + 1):
        problems += refused(
            mod, load_inline([stub_child("alpha", call_timeout=value)]),
            ["child 'alpha'", "call_timeout"], "call_timeout=%r" % (value,))
    try:
        cfg = mod.load_config(None, inline_config(
            [stub_child("alpha", call_timeout=limit)]), sandbox)
        if cfg.children[0].call_timeout != float(limit):
            problems.append("control: call_timeout=%r loaded as %r"
                            % (limit, cfg.children[0].call_timeout))
    except Exception as exc:  # noqa: BLE001
        problems.append("control: call_timeout=%r refused: %s" % (limit, exc))
    suite.record(GA, GA_CASES[11], problems,
                 detail=["bound       : _MAX_CALL_TIMEOUT_S = %g (from the module)"
                         % limit])

    # A13 -- a config nested past the decoder's recursion limit is a ConfigError
    # (exit 2), never a traceback, and the refusal does not echo the config.
    marker = "tf-config-marker-" + secrets.token_hex(4)
    deep = '{"%s": %s1%s}' % (marker, "[" * DEEP_NESTING, "]" * DEEP_NESTING)
    problems = refused(mod, lambda: mod.load_config(None, deep, sandbox),
                       ["config is not valid JSON"], "deep nesting")
    try:
        mod.load_config(None, deep, sandbox)
    except Exception as exc:  # noqa: BLE001 -- the type is judged by refused() above
        if marker in str(exc):
            problems.append("the refusal echoes the config text: %r" % str(exc)[:120])
    suite.record(GA, GA_CASES[12], problems,
                 detail=["config      : one key holding %d nested arrays" % DEEP_NESTING])


# ---------------------------------------------------------------------------
# B. startup  (7 cases)
# ---------------------------------------------------------------------------

START_FAIL_TIMEOUT_S = 10.0  # a refused start must exit within this (B2, B3, B5)


def stub_tool0(entry):
    """The stub's state-file name for a config child: its first --tool, else the default."""
    args = entry.get("args") or []
    for i, arg in enumerate(args[:-1]):
        if arg == "--tool":
            return args[i + 1]
    return STUB_DEFAULT_TOOL


def reap_stubs(state_dir, children):
    """Problems for every stub pid that outlived the case (each is then SIGKILLed).

    A child that recorded no pid at all is a problem too: the check must never
    pass vacuously because the stub was not spawned.
    """
    problems = []
    seen = set()
    for entry in children:
        tool0 = stub_tool0(entry)
        if tool0 in seen:
            continue
        seen.add(tool0)
        pids = read_pids(state_dir, tool0)
        if not pids:
            problems.append("child %r: no pid recorded in %s.pids"
                            % (entry.get("name"), tool0))
            continue
        alive = [pid for pid in pids if not pid_dead(pid)]
        if alive:
            kill_pids(alive)
            problems.append("child %r: pid(s) %r outlived the case"
                            % (entry.get("name"), alive))
    return problems


def stderr_tail(proxy, limit=400):
    """The last *limit* characters of the proxy's stderr, for a problem line."""
    text = proxy.stderr_text().strip()
    return text[-limit:] if text else "<empty>"


def started(proxy):
    """Problems unless the proxy answers initialize (a failed start names stderr)."""
    if proxy.initialize() is None:
        return ["no initialize reply (rc=%r); stderr: %s"
                % (proxy.poll(), stderr_tail(proxy))]
    return []


def live_case(suite, group, cid, fixture_root, label, make_children, body,
              extra_env=None, detail=(), top=None):
    """Run one live case: a fresh sandbox, a config, one proxy, then the reap.

    *make_children(sandbox)* returns the config's child entries; *body(proxy)*
    returns (problems, detail lines); *top* holds extra top-level config keys
    (`frame_limit`, ...).  The proxy is always closed and every stub pid is
    checked dead afterwards, pass or fail.
    """
    sandbox = new_sandbox(fixture_root, label)
    children = make_children(sandbox)
    cfgpath = write_config(sandbox, children, **(top or {}))
    problems = []
    lines = list(detail)
    proxy = None
    try:
        proxy = ProxyProc(sandbox, cfgpath, extra_env=extra_env, label=label)
        got_problems, got_detail = body(proxy)
        problems += got_problems
        lines += got_detail
    except Exception as exc:  # noqa: BLE001 -- any raise is the case's finding
        problems.append("case raised %s: %s" % (type(exc).__name__, exc))
    finally:
        if proxy is not None:
            proxy.close()
        problems += reap_stubs(sandbox, children)
    suite.record(group, cid, problems, detail=lines)


def stub_listing(*flags):
    """The stub's OWN tools/list result, asked directly (no proxy in between)."""
    lines = [
        {"jsonrpc": "2.0", "id": "tf-own-init", "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "ph-tests-mcp-proxy", "version": "1"}}},
        {"jsonrpc": "2.0", "id": "tf-own-list", "method": "tools/list", "params": {}},
    ]
    stdin_text = "".join(json.dumps(m) + "\n" for m in lines)
    _rc, out, _err = H.run_process([sys.executable, "-B", STUB] + list(flags),
                                   stdin_text=stdin_text, timeout=REPLY_TIMEOUT_S)
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == "tf-own-list":
            return (msg.get("result") or {}).get("tools")
    return None


def refused_start(proxy, needles):
    """Problems unless the proxy exits 1 within the bound with every needle on stderr."""
    rc = proxy.wait(START_FAIL_TIMEOUT_S)
    problems = []
    if rc is None:
        problems.append("still running after %gs, expected exit 1"
                        % START_FAIL_TIMEOUT_S)
        return problems, []
    if rc != 1:
        problems.append("exit code %r, expected 1" % rc)
    err = proxy.stderr_text()
    missing = [n for n in needles if n not in err]
    if missing:
        problems.append("stderr does not name %r: %s" % (missing, stderr_tail(proxy)))
    line = [ln for ln in err.splitlines() if ln.startswith("mcp-proxy:")]
    return problems, ["exit        : %r" % rc,
                      "stderr      : %s" % (line[0] if line else "<no mcp-proxy: line>")]


def group_b(suite, fixture_root):
    """B. listing, duplicate tool, crash on init, pagination, token.

    Every case runs the real proxy in front of one or two stubs; every refusal
    must end the proxy with exit 1 before it reads upstream and leave no stub.
    """
    import_problem = ""
    try:
        mod = H.load_module_from_path("ph_proxy_b", SERVER)
        declared_version = mod.McpServer.PROTOCOL_VERSION
    except Exception as exc:  # noqa: BLE001 -- an import failure only fails B6
        mod = None
        declared_version = None
        import_problem = "cannot import %s: %s: %s" % (
            os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__, exc)

    # B1 -- two stubs, two tools: both listed, each byte-equal to its own listing.
    own = {}
    for tool in ("tf_alpha_call", "tf_beta_call"):
        listing = stub_listing("--tool", tool)
        own[tool] = listing[0] if listing else None

    def b1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        tools = proxy.list_tools()
        if tools is None:
            return ["no tools/list result"], []
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        if names != ["tf_alpha_call", "tf_beta_call"]:
            problems.append("listed %r, expected ['tf_alpha_call', 'tf_beta_call']"
                            % names)
        for tool in tools:
            want = own.get(tool.get("name")) if isinstance(tool, dict) else None
            if want is None:
                problems.append("no own listing to compare %r against" % (tool,))
            elif json.dumps(tool, sort_keys=True) != json.dumps(want, sort_keys=True):
                problems.append("%s differs from the stub's own listing"
                                % tool.get("name"))
        return problems, ["listed      : %s" % ", ".join(map(str, names))]

    live_case(suite, GB, "two children, both listed verbatim", fixture_root, "b1",
              lambda sb: [
                  stub_child("alpha", "--tool", "tf_alpha_call", "--state-dir", sb),
                  stub_child("beta", "--tool", "tf_beta_call", "--state-dir", sb),
              ], b1,
              detail=["compare     : json.dumps(sort_keys=True) vs the stub asked directly"])

    # B2 -- one tool name exposed by two children refuses the start.
    live_case(suite, GB, "duplicate tool refuses start", fixture_root, "b2",
              lambda sb: [
                  stub_child("alpha", "--tool", "tf_alpha_call", "--tool", "tf_dup_call",
                             "--state-dir", sb),
                  stub_child("beta", "--tool", "tf_beta_call", "--tool", "tf_dup_call",
                             "--state-dir", sb),
              ],
              lambda proxy: refused_start(proxy, ["tf_dup_call", "'alpha'", "'beta'"]),
              detail=["duplicate   : tf_dup_call in alpha and beta"])

    # B3 -- a child that dies on initialize refuses the start, naming it.
    live_case(suite, GB, "crash on init refuses start", fixture_root, "b3",
              lambda sb: [
                  stub_child("tf-crasher", "--crash-on-init", "--state-dir", sb),
              ],
              lambda proxy: refused_start(proxy, ["'tf-crasher'"]),
              detail=["stub        : --crash-on-init (exit 3, no answer)"])

    # B4 -- a paginated listing is followed to the end.
    def b4(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        tools = proxy.list_tools()
        names = [t.get("name") for t in tools or [] if isinstance(t, dict)]
        want = ["tf_p1_call", "tf_p2_call", "tf_p3_call"]
        if names != want:
            problems.append("listed %r, expected %r" % (names, want))
        return problems, ["listed      : %s" % ", ".join(map(str, names))]

    live_case(suite, GB, "paginated listing followed", fixture_root, "b4",
              lambda sb: [
                  stub_child("alpha", "--tool", "tf_p1_call", "--tool", "tf_p2_call",
                             "--tool", "tf_p3_call", "--page-size", "1",
                             "--state-dir", sb),
              ], b4,
              detail=["stub        : --page-size 1, 3 tools"])

    # B5 -- a cursor that never ends refuses the start.
    live_case(suite, GB, "cursor loop refuses start", fixture_root, "b5",
              lambda sb: [
                  stub_child("alpha", "--cursor-loop", "--state-dir", sb),
              ],
              lambda proxy: refused_start(proxy, ["cursor", "'alpha'"]),
              detail=["stub        : --cursor-loop (same nextCursor every page)"])

    # B6 -- the declared protocol version is answered; one page, no cursor.
    def b6(proxy):
        if declared_version is None:
            return [import_problem], []
        problems = []
        proxy.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "ph-tests-mcp-proxy", "version": "1"},
        }, "tf-init")
        reply = proxy.wait_id("tf-init", START_TIMEOUT_S)
        if reply is None:
            return ["no initialize reply; stderr: %s" % stderr_tail(proxy)], []
        proxy.notify("notifications/initialized")
        got = (reply.get("result") or {}).get("protocolVersion")
        if got != declared_version:
            problems.append("protocolVersion %r != McpServer.PROTOCOL_VERSION %r"
                            % (got, declared_version))
        proxy.request("tools/list", {}, "tf-list")
        listed = proxy.wait_id("tf-list")
        result = listed.get("result") if isinstance(listed, dict) else None
        if not isinstance(result, dict):
            problems.append("no tools/list result: %r" % (listed,))
        elif "nextCursor" in result:
            problems.append("tools/list carries nextCursor %r" % result["nextCursor"])
        return problems, ["version     : %r (read from the module)" % declared_version]

    live_case(suite, GB, "protocol version, no nextCursor", fixture_root, "b6",
              lambda sb: [stub_child("alpha", "--state-dir", sb)], b6)

    # B7 -- the bearer token never reaches a child (FR-11); PATH is the control.
    def b7(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        token_env = mod._TOKEN_ENV if mod is not None else "MCP_PROXY_TOKEN"
        seen = {}
        for rid, name in ((71, token_env), (72, "PATH")):
            proxy.call(rid, STUB_DEFAULT_TOOL, "tf_env", {"name": name})
            reply = proxy.wait_id(rid)
            try:
                seen[name] = json.loads(tool_text(reply)).get("present")
            except (ValueError, AttributeError):
                problems.append("tf_env %s: unreadable reply %r" % (name, reply))
        if token_env in seen and seen[token_env] is not False:
            problems.append("%s present in the child env: %r"
                            % (token_env, seen[token_env]))
        if "PATH" in seen and seen["PATH"] is not True:
            problems.append("control: PATH not present in the child env")
        return problems, ["seen        : %s" % json.dumps(seen, sort_keys=True)]

    live_case(suite, GB, "token never reaches a child", fixture_root, "b7",
              lambda sb: [stub_child("alpha", "--state-dir", sb)], b7,
              extra_env={"MCP_PROXY_TOKEN": "tf-secret"},
              detail=["proxy env   : MCP_PROXY_TOKEN=tf-secret"])


# ---------------------------------------------------------------------------
# C. routing  (6 cases)
# ---------------------------------------------------------------------------

def one_stub(sandbox):
    """The config of a routing case: one stub, the default tool."""
    return [stub_child("alpha", "--state-dir", sandbox)]


def expect_error(reply, code, label):
    """Problems unless *reply* is a JSON-RPC error with *code*."""
    if reply is None:
        return ["%s: no reply" % label]
    err = reply.get("error")
    if not isinstance(err, dict):
        return ["%s: not a JSON-RPC error: %r" % (label, reply)]
    if err.get("code") != code:
        return ["%s: error code %r, expected %d" % (label, err.get("code"), code)]
    return []


def group_c(suite, fixture_root):
    """C. concurrent calls, id types, unknown tool / method, relay.

    Arrival ORDER is the observable, never elapsed time: each case reads the
    replies the proxy wrote, in the order it wrote them.
    """
    tool = STUB_DEFAULT_TOOL

    # C1 -- three concurrent sleeps answer shortest first, each under its own id.
    def c1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        plan = ((11, 0.6), (12, 0.3), (13, 0.0))
        for rid, seconds in plan:
            proxy.call(rid, tool, "tf_sleep", {"seconds": seconds})
        replies = {rid: proxy.wait_id(rid) for rid, _s in plan}
        for rid, seconds in plan:
            reply = replies[rid]
            if reply is None:
                problems.append("id %d: no reply" % rid)
            elif tool_text(reply) != "tf slept %s" % float(seconds):
                problems.append("id %d: text %r, expected the %gs sleep"
                                % (rid, tool_text(reply), seconds))
        order = [m["id"] for m in proxy.arrivals
                 if "method" not in m and type(m.get("id")) is int
                 and m.get("id") in (11, 12, 13)]
        if order != [13, 12, 11]:
            problems.append("arrival order %r, expected [13, 12, 11]" % order)
        return problems, ["order       : %r" % order]

    live_case(suite, GC, "concurrent calls, own ids", fixture_root, "c1",
              one_stub, c1,
              detail=["sleeps      : id 11=0.6s, 12=0.3s, 13=0.0s, sent back to back"])

    # C2 -- string, zero and 2**53 ids come back with type and value.
    def c2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        ids = ("abc", 0, 2 ** 53)
        for rid in ids:
            proxy.call(rid, tool, "tf_echo", {"rid": repr(rid)})
        for rid in ids:
            reply = proxy.wait_id(rid)
            if reply is None:
                problems.append("id %r (%s): no reply with that type and value"
                                % (rid, type(rid).__name__))
            elif is_error_result(reply) or "error" in reply:
                problems.append("id %r: error reply %r" % (rid, reply))
        return problems, ["ids         : %s" % ", ".join(repr(i) for i in ids)]

    live_case(suite, GC, "id types echoed", fixture_root, "c2", one_stub, c2)

    # C3 -- an unknown tool is a protocol error, -32602.
    def c3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(31, "tf_no_such_tool", "tf_echo", {})
        reply = proxy.wait_id(31)
        problems += expect_error(reply, -32602, "unknown tool")
        if not problems and "tf_no_such_tool" not in reply["error"].get("message", ""):
            problems.append("message %r does not name the tool"
                            % reply["error"].get("message"))
        return problems, []

    live_case(suite, GC, "unknown tool is -32602", fixture_root, "c3", one_stub, c3)

    # C4 -- resources/list and prompts/list are not served, -32601.
    def c4(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        for rid, method in ((41, "resources/list"), (42, "prompts/list")):
            proxy.request(method, {}, rid)
            problems += expect_error(proxy.wait_id(rid), -32601, method)
        return problems, []

    live_case(suite, GC, "resources, prompts are -32601", fixture_root, "c4",
              one_stub, c4)

    # C5 -- a child's own JSON-RPC error is relayed verbatim.
    def c5(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(51, tool, "tf_error", {})
        reply = proxy.wait_id(51)
        want = {"code": -32000, "message": "tf boom"}
        if reply is None:
            problems.append("no reply")
        elif reply.get("error") != want:
            problems.append("relayed %r, expected error %r" % (reply, want))
        return problems, []

    live_case(suite, GC, "child error relayed verbatim", fixture_root, "c5",
              one_stub, c5)

    # C6 -- the arguments reach the child unchanged.
    def c6(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        params = {"a": [1, "x", None, 2.5], "b": {"c": True, "d": {}},
                  "u": "é✓", "e": ""}
        proxy.call(61, tool, "tf_echo", params)
        reply = proxy.wait_id(61)
        sent = {"function": "tf_echo", "params": params}
        try:
            got = json.loads(tool_text(reply))
        except ValueError:
            got = None
        if got != sent:
            problems.append("echoed %r, sent %r" % (got, sent))
        return problems, []

    live_case(suite, GC, "arguments relayed unchanged", fixture_root, "c6",
              one_stub, c6)


# ---------------------------------------------------------------------------
# D. cancel  (4 cases)
# ---------------------------------------------------------------------------

CANCEL_SILENCE_S = 6.0       # D1: a cancelled 5 s sleep must stay silent this long
QUIET_S = 1.0                # D2, D3, E2, E3: no stray line within this window
EVENT_TIMEOUT_S = 10.0       # polling the stub's events.jsonl for one record


def wait_event(state_dir, pred, timeout=EVENT_TIMEOUT_S):
    """The first event in the stub's events.jsonl matching *pred*, polled; else None."""
    deadline = time.monotonic() + timeout
    while True:
        for event in read_events(state_dir):
            if pred(event):
                return event
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def stub_events(state_dir, method):
    """Every event the stub recorded for *method*, in arrival order."""
    return [e for e in read_events(state_dir) if e.get("method") == method]


def is_progress(msg):
    return isinstance(msg, dict) and msg.get("method") == "notifications/progress"


def progress_token(msg):
    """The progressToken of a notifications/progress message (None if absent)."""
    params = msg.get("params") if isinstance(msg, dict) else None
    return params.get("progressToken") if isinstance(params, dict) else None


def response_index(arrivals, rid):
    """Index of the response with *rid* (type and value) in *arrivals*, else None."""
    for i, msg in enumerate(arrivals):
        if ("method" not in msg and "id" in msg
                and type(msg["id"]) is type(rid) and msg["id"] == rid):
            return i
    return None


def group_d(suite, fixture_root):
    """D. cancel forwarding with the child's own id.

    The stub records every notifications/cancelled it receives, with the
    requestId it named, so the forwarded id is observed on the child's side of
    the wire, not inferred from the proxy's silence.
    """
    tool = STUB_DEFAULT_TOOL

    # D1 -- the forwarding proof: an in-flight tf_sleep 5 cancelled upstream
    # stays silent, and the child gets a cancel naming ITS id, not ours.
    def d1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        state = proxy.sandbox
        rid = 901
        proxy.call(rid, tool, "tf_sleep", {"seconds": 5})
        call_ev = wait_event(state, lambda e: e.get("method") == "tools/call")
        if call_ev is None:
            return ["the stub never recorded the tools/call"], []
        child_id = call_ev.get("id")
        proxy.cancel(rid, "tf d1 cancel")
        late = proxy.wait_id(rid, CANCEL_SILENCE_S)
        if late is not None:
            problems.append("a line with id %r arrived after the cancel: %r" % (rid, late))
        proxy.request("ping", {}, "tf-d1-ping")
        if proxy.wait_id("tf-d1-ping") is None:
            problems.append("the ping after the cancel was not answered")
        cancels = stub_events(state, "notifications/cancelled")
        if len(cancels) != 1:
            problems.append("the stub saw %d cancel(s), expected 1: %r"
                            % (len(cancels), cancels))
        else:
            got = cancels[0].get("cancel_id")
            if type(got) is not type(child_id) or got != child_id:
                problems.append("cancel_id %r != the stub's tools/call id %r"
                                % (got, child_id))
            if got == rid:
                problems.append("cancel_id %r is the upstream id" % got)
            if "upstream cancelled" not in str(cancels[0].get("reason")):
                problems.append("cancel reason %r lacks 'upstream cancelled'"
                                % cancels[0].get("reason"))
        done = [e for e in stub_events(state, "tf_sleep/done")
                if e.get("id") == child_id]
        if done:
            problems.append("the cancelled sleep still finished in the stub")
        return problems, ["ids         : upstream %r -> child %r" % (rid, child_id)]

    live_case(suite, GD, "in-flight cancel forwarded", fixture_root, "d1",
              one_stub, d1,
              detail=["call        : tf_sleep 5, cancelled once the stub has it",
                      "silence     : %gs, then ping" % CANCEL_SILENCE_S])

    # D2 -- a cancel after the response: nothing upstream, nothing downstream.
    def d2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 21
        proxy.call(rid, tool, "tf_echo", {"x": 1})
        if proxy.wait_id(rid) is None:
            return ["no reply to the tf_echo call"], []
        proxy.cancel(rid, "tf d2 late cancel")
        extra = proxy.drain_all(QUIET_S)
        if extra:
            problems.append("%d extra line(s) after the late cancel: %r"
                            % (len(extra), extra[:3]))
        cancels = stub_events(proxy.sandbox, "notifications/cancelled")
        if cancels:
            problems.append("the stub saw a cancel: %r" % cancels)
        return problems, []

    live_case(suite, GD, "late cancel is a no-op", fixture_root, "d2",
              one_stub, d2,
              detail=["quiet       : %gs after the cancel" % QUIET_S])

    # D3 -- smoke-style noise: a bool, a float, an unknown id and non-object
    # params name nothing; the in-flight call 1 (True == 1 == 1.0) survives.
    def d3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 1
        proxy.call(rid, tool, "tf_sleep", {"seconds": 1.0})
        if wait_event(proxy.sandbox, lambda e: e.get("method") == "tools/call") is None:
            return ["the stub never recorded the tools/call"], []
        noise = ({"requestId": True}, {"requestId": 1.0},
                 {"requestId": "tf-no-such-id"}, "tf-not-an-object", [1])
        for params in noise:
            proxy.notify("notifications/cancelled", params)
        reply = proxy.wait_id(rid)
        if reply is None:
            problems.append("call %r got no reply: the noise cancelled it" % rid)
        elif tool_text(reply) != "tf slept 1.0":
            problems.append("call %r answered %r" % (rid, reply))
        extra = proxy.drain_all(QUIET_S)
        if extra:
            problems.append("%d line(s) answered the noise: %r" % (len(extra), extra[:3]))
        cancels = stub_events(proxy.sandbox, "notifications/cancelled")
        if cancels:
            problems.append("the stub saw a cancel: %r" % cancels)
        return problems, ["noise       : requestId true, 1.0, unknown string; "
                          "params string, list"]

    live_case(suite, GD, "cancel noise ignored", fixture_root, "d3",
              one_stub, d3,
              detail=["in flight   : id 1, tf_sleep 1.0"])

    # D4 -- a cancel naming the initialize id cannot cancel the handshake.
    def d4(proxy):
        problems = []
        proxy.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "ph-tests-mcp-proxy", "version": "1"},
        }, "tf-init")
        proxy.cancel("tf-init", "tf d4 cancel the handshake")
        reply = proxy.wait_id("tf-init", START_TIMEOUT_S)
        if reply is None or not isinstance(reply.get("result"), dict):
            return ["no initialize result after the cancel: %r; stderr: %s"
                    % (reply, stderr_tail(proxy))], []
        proxy.notify("notifications/initialized")
        proxy.request("ping", {}, "tf-d4-ping")
        if proxy.wait_id("tf-d4-ping") is None:
            problems.append("the ping after the handshake was not answered")
        return problems, []

    live_case(suite, GD, "initialize is not cancellable", fixture_root, "d4",
              one_stub, d4,
              detail=["order       : initialize, cancel(its id), then read"])


def group_e(suite, fixture_root):
    """E. progressToken mapping both ways.

    The child only ever sees the proxy's own "px:" token; the caller only ever
    sees its own token back, with its JSON type, and only while the call runs.
    """
    tool = STUB_DEFAULT_TOOL

    # E1 -- a string token: 3 progress lines carry it, all before the response,
    # and the child saw a "px:" token instead.
    def e1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 501
        proxy.call(rid, tool, "tf_progress", {"n": 3},
                   meta={"progressToken": "tok-A"})
        if proxy.wait_id(rid) is None:
            return ["no reply to the tf_progress call"], []
        arrivals = list(proxy.arrivals)
        resp_at = response_index(arrivals, rid)
        progress = [(i, m) for i, m in enumerate(arrivals) if is_progress(m)]
        tokens = [progress_token(m) for _i, m in progress]
        if tokens != ["tok-A"] * 3:
            problems.append("progress tokens %r, expected 3 x 'tok-A'" % tokens)
        late = [i for i, _m in progress if resp_at is None or i > resp_at]
        if late:
            problems.append("%d progress line(s) after the response" % len(late))
        calls = stub_events(proxy.sandbox, "tools/call")
        seen = [e.get("progress_token") for e in calls]
        if len(seen) != 1 or not (isinstance(seen[0], str) and seen[0].startswith("px:")):
            problems.append("the stub saw token(s) %r, expected one 'px:...'" % seen)
        return problems, ["child token : %r" % (seen[0] if seen else None)]

    live_case(suite, GE, "string token mapped both ways", fixture_root, "e1",
              one_stub, e1,
              detail=["call        : tf_progress n=3, progressToken 'tok-A'"])

    # E2 -- a progress the child sends after its result is dropped.
    def e2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 502
        proxy.call(rid, tool, "tf_progress", {"n": 2, "after_result": True},
                   meta={"progressToken": "tok-B"})
        if proxy.wait_id(rid) is None:
            return ["no reply to the tf_progress call"], []
        proxy.drain_all(QUIET_S)
        arrivals = list(proxy.arrivals)
        resp_at = response_index(arrivals, rid)
        before = [m for i, m in enumerate(arrivals) if is_progress(m) and i < resp_at]
        after = [m for i, m in enumerate(arrivals) if is_progress(m) and i > resp_at]
        if len(before) != 2:
            problems.append("control: %d progress line(s) before the response, "
                            "expected 2" % len(before))
        if after:
            problems.append("%d progress line(s) after the response: %r"
                            % (len(after), after))
        return problems, ["quiet       : %gs after the response" % QUIET_S]

    live_case(suite, GE, "after-result progress dropped", fixture_root, "e2",
              one_stub, e2,
              detail=["call        : tf_progress n=2 after_result, token 'tok-B'"])

    # E3 -- no token, and an unroutable one (1.5, true): the child sees none
    # (M3 strip) and no progress reaches upstream.
    def e3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        plan = ((511, None), (512, 1.5), (513, True))
        for rid, token in plan:
            meta = None if token is None else {"progressToken": token}
            proxy.call(rid, tool, "tf_progress", {"n": 2}, meta=meta)
            if proxy.wait_id(rid) is None:
                problems.append("id %d: no reply" % rid)
        proxy.drain_all(QUIET_S)
        seen = [e.get("progress_token") for e in stub_events(proxy.sandbox, "tools/call")]
        if seen != [None, None, None]:
            problems.append("the stub saw tokens %r, expected [None, None, None]" % seen)
        progress = [m for m in proxy.arrivals if is_progress(m)]
        if progress:
            problems.append("%d progress line(s) reached upstream: %r"
                            % (len(progress), progress[:3]))
        return problems, ["tokens      : none, 1.5, true (sent one call each)"]

    live_case(suite, GE, "absent or unroutable token", fixture_root, "e3",
              one_stub, e3,
              detail=["call        : tf_progress n=2, three times"])

    # E4 -- an int token comes back as an int.
    def e4(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 504
        proxy.call(rid, tool, "tf_progress", {"n": 2}, meta={"progressToken": 7})
        if proxy.wait_id(rid) is None:
            return ["no reply to the tf_progress call"], []
        tokens = [progress_token(m) for m in proxy.arrivals if is_progress(m)]
        if len(tokens) != 2 or any(type(t) is not int or t != 7 for t in tokens):
            problems.append("progress tokens %r, expected 2 x int 7" % tokens)
        return problems, ["tokens      : %r" % tokens]

    live_case(suite, GE, "int token stays an int", fixture_root, "e4",
              one_stub, e4,
              detail=["call        : tf_progress n=2, progressToken 7"])


# ---------------------------------------------------------------------------
# F. death and restart  (5 cases)
# ---------------------------------------------------------------------------

BACKOFF_REPLY_S = 1.0        # F2: a call during the backoff is refused within this
RESTART_POLL_S = 20.0        # F3, F4: one restart (backoff 1 s x1.2 + a start) lands within this
BUDGET_POLL_S = 50.0         # F5: backoffs 1+2+4+8+16 s x1.2 jitter + six interpreter starts
POLL_STEP_S = 0.25           # the pause between two polling calls
NO_SPAWN_S = 3.0             # F5: no further spawn within this after "disabled"


class _Rids:
    """Fresh int request ids for polling loops, one counter per case."""

    def __init__(self, start):
        self.next = start

    def __call__(self):
        rid = self.next
        self.next += 1
        return rid


def poll_call(proxy, rids, tool, function, params, pred, bound):
    """Call *tool* until a reply satisfies *pred* or *bound* seconds pass.

    Returns (matching reply or None, last reply seen, calls made).
    """
    deadline = time.monotonic() + bound
    last = None
    calls = 0
    while time.monotonic() < deadline:
        rid = rids()
        proxy.call(rid, tool, function, params)
        calls += 1
        reply = proxy.wait_id(rid, REPLY_TIMEOUT_S)
        if reply is not None:
            last = reply
            if pred(reply):
                return reply, last, calls
        if proxy.eof:
            break
        time.sleep(POLL_STEP_S)
    return None, last, calls


def crash_mid_call(proxy, rid, tool=STUB_DEFAULT_TOOL):
    """tf_crash in flight; problems unless the reply is isError naming child 'alpha'."""
    proxy.call(rid, tool, "tf_crash", {"code": 7})
    reply = proxy.wait_id(rid)
    if reply is None:
        return ["the crashed call got no reply; stderr: %s" % stderr_tail(proxy)], None
    if not is_error_result(reply):
        return ["the crashed call is not isError: %r" % reply], reply
    if "'alpha'" not in tool_text(reply):
        return ["isError text %r does not name child 'alpha'" % tool_text(reply)], reply
    return [], reply


def group_f(suite, fixture_root):
    """F. death mid-call, backoff, new pid, toolset, restart budget.

    The real backoff is used throughout: no live case patches the proxy's
    constants, and every count the cases assert is read from the module.
    """
    import_problem = ""
    try:
        mod = H.load_module_from_path("ph_proxy_f", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure only fails F5
        mod = None
        import_problem = "cannot import %s: %s: %s" % (
            os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__, exc)
    tool = STUB_DEFAULT_TOOL

    # F1 -- tf_crash mid-call: isError naming the child; a second child's tool
    # still answers.
    def f1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        got, reply = crash_mid_call(proxy, 601, "tf_alpha_call")
        problems += got
        proxy.call(602, "tf_beta_call", "tf_echo", {"x": 1})
        other = proxy.wait_id(602)
        if other is None:
            problems.append("child 'beta' did not answer after alpha died")
        elif is_error_result(other) or "error" in other:
            problems.append("child 'beta' answered an error: %r" % other)
        return problems, ["isError     : %s" % (tool_text(reply) if reply else None)]

    live_case(suite, GF, "death mid-call is isError", fixture_root, "f1",
              lambda sb: [
                  stub_child("alpha", "--tool", "tf_alpha_call", "--state-dir", sb),
                  stub_child("beta", "--tool", "tf_beta_call", "--state-dir", sb),
              ], f1,
              detail=["crash       : alpha tf_crash code 7; then beta tf_echo"])

    # F2 -- a call during the backoff is refused at once, saying "restarting".
    def f2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        got, _reply = crash_mid_call(proxy, 611)
        if got:
            return got, []
        t0 = time.monotonic()
        proxy.call(612, tool, "tf_echo", {"x": 1})
        reply = proxy.wait_id(612)
        elapsed = time.monotonic() - t0
        if reply is None:
            return ["the call during the backoff got no reply"], []
        if not is_error_result(reply) or "restarting" not in tool_text(reply):
            problems.append("expected isError with 'restarting', got %r" % reply)
        if elapsed > BACKOFF_REPLY_S:
            problems.append("answered in %.2fs, bound %gs" % (elapsed, BACKOFF_REPLY_S))
        return problems, ["isError     : %s" % tool_text(reply),
                          "elapsed     : %.3fs (bound %gs)" % (elapsed, BACKOFF_REPLY_S)]

    live_case(suite, GF, "call during backoff refused", fixture_root, "f2",
              one_stub, f2)

    # F3 -- after retry_at the tool works again under a new pid.
    def f3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(621, tool, "tf_pid", {})
        first = proxy.wait_id(621)
        old_pid = tool_text(first) if first is not None else ""
        if not old_pid.isdigit():
            return ["control: tf_pid before the crash answered %r" % (first,)], []
        got, _reply = crash_mid_call(proxy, 622)
        if got:
            return got, []
        rids = _Rids(623)
        ok, last, calls = poll_call(
            proxy, rids, tool, "tf_pid", {},
            lambda r: not is_error_result(r) and tool_text(r).isdigit(),
            RESTART_POLL_S)
        if ok is None:
            return ["no successful tf_pid within %gs (%d calls); last %r"
                    % (RESTART_POLL_S, calls, last)], []
        new_pid = tool_text(ok)
        if new_pid == old_pid:
            problems.append("tf_pid answered the old pid %s after the restart" % old_pid)
        pids = [str(p) for p in read_pids(proxy.sandbox)]
        if pids[-1:] != [new_pid]:
            problems.append("new pid %s is not the last spawn in .pids %r" % (new_pid, pids))
        return problems, ["pids        : %s -> %s (%d polling call(s))"
                          % (old_pid, new_pid, calls)]

    live_case(suite, GF, "restarted under a new pid", fixture_root, "f3",
              one_stub, f3,
              detail=["poll        : tf_pid every %gs, bound %gs"
                      % (POLL_STEP_S, RESTART_POLL_S)])

    # F4 -- the tool set changes across the restart: the child is disabled.
    def f4_children(sandbox):
        return [stub_child("alpha", "--state-dir", sandbox, "--toolset-file",
                           os.path.join(sandbox, "tf-toolset.txt"))]

    def f4(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        write_file(os.path.join(proxy.sandbox, "tf-toolset.txt"),
                   "%s\ntf_extra_call\n" % tool)
        got, _reply = crash_mid_call(proxy, 631)
        if got:
            return got, []
        rids = _Rids(632)
        ok, last, calls = poll_call(
            proxy, rids, tool, "tf_echo", {"x": 1},
            lambda r: "restarting" not in tool_text(r), RESTART_POLL_S)
        if ok is None:
            return ["still restarting after %gs (%d calls); last %r"
                    % (RESTART_POLL_S, calls, last)], []
        text = tool_text(ok)
        if not is_error_result(ok) or "disabled" not in text:
            problems.append("expected isError with 'disabled', got %r" % ok)
        if len(read_pids(proxy.sandbox)) != 2:
            problems.append("control: .pids holds %r, expected 2 spawns"
                            % read_pids(proxy.sandbox))
        return problems, ["isError     : %s" % text]

    live_case(suite, GF, "changed tool set disables", fixture_root, "f4",
              f4_children, f4,
              detail=["toolset     : rewritten to add tf_extra_call before tf_crash"])

    # F5 -- the restart budget (H1 proof): every restart attempt crashes at
    # initialize, so each failed attempt is one more charged death until the
    # budget is spent; then the child is disabled and nothing respawns.
    def f5(proxy):
        if mod is None:
            return [import_problem], []
        problems = started(proxy)
        if problems:
            return problems, []
        spawns = mod._RESTART_BUDGET + 1
        want = "disabled (%d crashes in %.0fs)" % (spawns, mod._RESTART_WINDOW_S)
        got, _reply = crash_mid_call(proxy, 641)
        if got:
            return got, []
        t0 = time.monotonic()
        rids = _Rids(642)
        ok, last, calls = poll_call(
            proxy, rids, tool, "tf_echo", {"x": 1},
            lambda r: "disabled" in tool_text(r), BUDGET_POLL_S)
        elapsed = time.monotonic() - t0
        if ok is None:
            return ["not disabled within %gs (%d calls); last %r"
                    % (BUDGET_POLL_S, calls, last)], []
        text = tool_text(ok)
        if not is_error_result(ok) or want not in text:
            problems.append("expected isError with %r, got %r" % (want, ok))
        pids = read_pids(proxy.sandbox)
        if len(pids) != spawns:
            problems.append(".pids holds %d line(s), expected _RESTART_BUDGET + 1 = %d"
                            % (len(pids), spawns))
        time.sleep(NO_SPAWN_S)
        later = read_pids(proxy.sandbox)
        if len(later) != len(pids):
            problems.append("%d further spawn(s) within %gs of 'disabled'"
                            % (len(later) - len(pids), NO_SPAWN_S))
        proxy.request("ping", {}, "tf-f5-ping")
        if proxy.wait_id("tf-f5-ping") is None:
            problems.append("the ping after 'disabled' was not answered")
        if "never retrieved" in proxy.stderr_text():
            problems.append("stderr carries 'never retrieved': %s" % stderr_tail(proxy))
        return problems, ["isError     : %s" % text,
                          "spawns      : %d (_RESTART_BUDGET %d, from the module)"
                          % (len(later), mod._RESTART_BUDGET),
                          "disabled in : %.1fs (bound %gs, %d polling call(s))"
                          % (elapsed, BUDGET_POLL_S, calls)]

    live_case(suite, GF, "restart budget disables", fixture_root, "f5",
              lambda sb: [stub_child("alpha", "--state-dir", sb,
                                     "--crash-after-init-count", "1")], f5,
              detail=["stub        : --crash-after-init-count 1, then tf_crash once",
                      "backoff     : the real one, no constant patched"])


# ---------------------------------------------------------------------------
# G. framing  (7 cases)
# ---------------------------------------------------------------------------

BIG_BYTES = 5 * 1024 * 1024  # G1: above asyncio's 64 KiB default StreamReader limit (SC-3)
FRAME_LIMIT = 1024 * 1024    # G2: the config's frame_limit
OVERSIZE_BYTES = 2000000     # G2: one non-JSON line over FRAME_LIMIT
DEEP_NESTING = 100000        # A13, G6, J35: '[' count past the JSON decoder's recursion limit


def child_reply(reply):
    """The JSON-RPC message a tf_sample / tf_ping_proxy call reports it got, else None."""
    try:
        got = json.loads(tool_text(reply))
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def stderr_lacks(proxy, needles):
    """Problems for every needle found on the proxy's stderr."""
    err = proxy.stderr_text()
    return ["stderr carries %r: %s" % (n, stderr_tail(proxy))
            for n in needles if n in err]


def group_g(suite, fixture_root):
    """G. 5 MiB result, frame limit, pollution, child requests, deep nesting,
    an id of the wrong type.

    G3 and G4 close the proxy inside the case before reading its stderr, so a
    "never retrieved" or "reader failed" written at shutdown is seen too.
    """
    tool = STUB_DEFAULT_TOOL

    # G1 -- a 5 MiB result is relayed byte-identical (SC-3).
    def g1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(901, tool, "tf_big", {"bytes": BIG_BYTES})
        reply = proxy.wait_id(901)
        if reply is None:
            return ["no reply to tf_big; stderr: %s" % stderr_tail(proxy)], []
        text = tool_text(reply)
        want = hashlib.sha256(b"x" * BIG_BYTES).hexdigest()
        got = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if is_error_result(reply) or "error" in reply:
            problems.append("tf_big answered an error: %r" % (reply.get("error")
                                                             or text[:200],))
        if len(text) != BIG_BYTES:
            problems.append("length %d, expected %d" % (len(text), BIG_BYTES))
        if got != want:
            problems.append("sha256 %s, expected %s" % (got, want))
        return problems, ["length      : %d (expected %d)" % (len(text), BIG_BYTES),
                          "sha256      : %s" % got[:16]]

    live_case(suite, GG, "5 MiB result relayed exactly", fixture_root, "g1",
              one_stub, g1,
              detail=["call        : tf_big %d bytes (default frame limit)" % BIG_BYTES])

    # G2 -- a frame over frame_limit is a desync: isError, a new pid, the proxy lives.
    def g2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(911, tool, "tf_pid", {})
        first = proxy.wait_id(911)
        old_pid = tool_text(first) if first is not None else ""
        if not old_pid.isdigit():
            return ["control: tf_pid before the oversize answered %r" % (first,)], []
        proxy.call(912, tool, "tf_oversize", {"bytes": OVERSIZE_BYTES})
        reply = proxy.wait_id(912)
        if reply is None:
            return ["the oversize call got no reply; stderr: %s"
                    % stderr_tail(proxy)], []
        if not is_error_result(reply):
            problems.append("the oversize call is not isError: %r" % reply)
        ok, last, calls = poll_call(
            proxy, _Rids(913), tool, "tf_pid", {},
            lambda r: not is_error_result(r) and tool_text(r).isdigit(),
            RESTART_POLL_S)
        if ok is None:
            problems.append("no successful tf_pid within %gs (%d calls); last %r"
                            % (RESTART_POLL_S, calls, last))
            return problems, ["isError     : %s" % tool_text(reply)]
        new_pid = tool_text(ok)
        if new_pid == old_pid:
            problems.append("tf_pid answered the old pid %s after the oversize" % old_pid)
        if proxy.poll() is not None:
            problems.append("the proxy exited (rc=%r)" % proxy.poll())
        return problems, ["isError     : %s" % tool_text(reply),
                          "pids        : %s -> %s (%d polling call(s))"
                          % (old_pid, new_pid, calls)]

    live_case(suite, GG, "frame over limit restarts", fixture_root, "g2",
              one_stub, g2, top={"frame_limit": FRAME_LIMIT},
              detail=["config      : frame_limit %d" % FRAME_LIMIT,
                      "call        : tf_oversize %d bytes, no newline before it"
                      % OVERSIZE_BYTES])

    # G3 -- pollution is dropped: the result arrives, the reader and the child survive.
    def g3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(921, tool, "tf_pid", {})
        first = proxy.wait_id(921)
        old_pid = tool_text(first) if first is not None else ""
        if not old_pid.isdigit():
            return ["control: tf_pid before the pollution answered %r" % (first,)], []
        proxy.call(922, tool, "tf_pollute", {})
        reply = proxy.wait_id(922)
        if reply is None:
            problems.append("tf_pollute got no reply; stderr: %s" % stderr_tail(proxy))
        elif is_error_result(reply) or tool_text(reply) != "tf polluted":
            problems.append("tf_pollute answered %r, expected 'tf polluted'" % reply)
        proxy.call(923, tool, "tf_pid", {})
        after = proxy.wait_id(923)
        new_pid = tool_text(after) if after is not None else ""
        if new_pid != old_pid:
            problems.append("the next call answered pid %r, expected the same pid %s"
                            % (new_pid or after, old_pid))
        if len(read_pids(proxy.sandbox)) != 1:
            problems.append(".pids holds %r, expected one spawn"
                            % read_pids(proxy.sandbox))
        proxy.close()
        problems += stderr_lacks(proxy, ["reader failed"])
        return problems, ["pid         : %s before, %s after" % (old_pid, new_pid)]

    live_case(suite, GG, "pollution dropped, same pid", fixture_root, "g3",
              one_stub, g3,
              detail=["pollution   : print line, non-UTF-8 line, progress with "
                      "string params, progress with list params, method 5"])

    # G4 -- a child->client request other than ping is refused -32601.
    def g4(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(931, tool, "tf_sample", {})
        reply = proxy.wait_id(931)
        got = child_reply(reply) if reply is not None else None
        if reply is None:
            problems.append("tf_sample got no reply")
        elif got is None:
            problems.append("tf_sample did not report a reply: %r" % reply)
        else:
            problems += expect_error(got, -32601, "sampling/createMessage")
            if got.get("id") != "s1":
                problems.append("the refusal carries id %r, expected 's1'" % got.get("id"))
        proxy.close()
        problems += stderr_lacks(proxy, ["never retrieved"])
        return problems, ["child got   : %s" % json.dumps(got, sort_keys=True)]

    live_case(suite, GG, "child request refused -32601", fixture_root, "g4",
              one_stub, g4,
              detail=["stub        : sends {id: s1, method: sampling/createMessage}"])

    # G5 -- a child's ping is answered {}.
    def g5(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(941, tool, "tf_ping_proxy", {})
        reply = proxy.wait_id(941)
        got = child_reply(reply) if reply is not None else None
        if reply is None:
            problems.append("tf_ping_proxy got no reply")
        elif got is None:
            problems.append("tf_ping_proxy did not report a reply: %r" % reply)
        elif got.get("id") != "p1" or got.get("result") != {} or "error" in got:
            problems.append("the child got %r, expected id 'p1' with result {}" % got)
        return problems, ["child got   : %s" % json.dumps(got, sort_keys=True)]

    live_case(suite, GG, "child ping answered {}", fixture_root, "g5",
              one_stub, g5,
              detail=["stub        : sends {id: p1, method: ping}"])

    # G6 -- a deeply nested upstream line is a -32700, never a dead proxy (F17).
    def g6(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.send(b"[" * DEEP_NESTING + b"\n")
        err = proxy.wait_for(lambda m: m.get("id") is None and "error" in m)
        code = ((err or {}).get("error") or {}).get("code")
        if code != -32700:
            problems.append("deep line answered %r, expected a -32700 with id null" % (err,))
        proxy.request("ping", {}, 961)
        pong = proxy.wait_id(961)
        if pong is None or pong.get("result") != {}:
            problems.append("ping after the deep line answered %r (rc=%r); stderr: %s"
                            % (pong, proxy.poll(), stderr_tail(proxy)))
        proxy.close()
        problems += stderr_lacks(proxy, ["Traceback"])
        return problems, ["answers     : deep line %r, ping %s"
                          % (code, "ok" if pong is not None else None)]

    live_case(suite, GG, "deep nesting -> -32700, alive", fixture_root, "g6",
              one_stub, g6,
              detail=["upstream    : one line of %d '[' (past the JSON decoder's "
                      "recursion limit)" % DEEP_NESTING])

    # G7 -- a request id that is neither a string nor an integer is refused at
    # intake with -32600 and id null, never dispatched; the proxy keeps serving.
    def g7(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        bad_ids = (True, 1.5)
        for rid in bad_ids:
            proxy.request("ping", {}, rid)
        proxy.request("ping", {}, 971)
        pong = proxy.wait_id(971)
        if pong is None or pong.get("result") != {}:
            problems.append("ping after the bad ids answered %r (rc=%r); stderr: %s"
                            % (pong, proxy.poll(), stderr_tail(proxy)))
        rest = proxy.drain_all(0.2)
        refusals = [m for m in rest if m.get("id") is None and "error" in m]
        echoed = [m for m in rest if "id" in m and m.get("id") is not None]
        codes = [(m.get("error") or {}).get("code") for m in refusals]
        if codes != [-32600] * len(bad_ids):
            problems.append("bad ids answered %r with id null, expected -32600 x%d"
                            % (codes, len(bad_ids)))
        if echoed:
            problems.append("a reply echoed a bad id: %r" % echoed[:2])
        proxy.close()
        problems += stderr_lacks(proxy, ["Traceback"])
        return problems, ["answers     : refusals %r, echoed %d, ping %s"
                          % (codes, len(echoed), "ok" if pong is not None else None)]

    live_case(suite, GG, "bad id type -> -32600, alive", fixture_root, "g7",
              one_stub, g7,
              detail=["upstream    : ping with id true, ping with id 1.5, then ping 971"])


# ---------------------------------------------------------------------------
# H. timeout  (1 case)
# ---------------------------------------------------------------------------

def group_h(suite, fixture_root):
    """H. call_timeout cancels the child call."""
    tool = STUB_DEFAULT_TOOL

    # H1 -- call_timeout 1 against a 5 s sleep: isError "within 1s", and the
    # child received a cancel whose reason says the call timed out.
    def h1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        rid = 801
        proxy.call(rid, tool, "tf_sleep", {"seconds": 5})
        reply = proxy.wait_id(rid)
        if reply is None:
            return ["the timed-out call got no reply"], []
        text = tool_text(reply)
        if not is_error_result(reply) or "within 1s" not in text:
            problems.append("expected isError with 'within 1s', got %r" % reply)
        cancel = wait_event(proxy.sandbox,
                            lambda e: e.get("method") == "notifications/cancelled")
        if cancel is None:
            problems.append("the stub never recorded a cancel")
        elif "timed out" not in str(cancel.get("reason")):
            problems.append("cancel reason %r lacks 'timed out'" % cancel.get("reason"))
        return problems, ["isError     : %s" % text,
                          "cancel      : %r" % ((cancel or {}).get("reason"),)]

    live_case(suite, GH, "call_timeout cancels the call", fixture_root, "h1",
              lambda sb: [stub_child("alpha", "--state-dir", sb, call_timeout=1)], h1,
              detail=["call        : tf_sleep 5 under call_timeout 1"])


# ---------------------------------------------------------------------------
# I. shutdown  (6 cases)
# ---------------------------------------------------------------------------

FAKE_PGID = 424242           # I4, I5: the fake incarnation's pid == pgid; never really signalled
I_GRACE_S = 0.5              # I4, I5: _SHUTDOWN_GRACE_S while the case runs (restored after)
IN_PROCESS_TIMEOUT_S = 10.0  # I4, I5: hard deadline on one in-process coroutine
PROBE_WAIT_S = 2.0           # I5: the first signal-0 probe must be seen within this
PIDS_POLL_S = 10.0           # I6: the second incarnation's .pids line lands within this


class _KillpgRecorder:
    """Stands in for `os.killpg`: records (pgid, sig), answers from a script, signals nothing.

    `script` maps a signal number to an exception class to raise; a signal
    absent from it (or mapped to None) returns normally.
    """

    def __init__(self):
        self.calls = []
        self.script = {}

    def __call__(self, pgid, sig):
        self.calls.append((pgid, int(sig)))
        exc = self.script.get(int(sig))
        if exc is not None:
            raise exc("tf scripted killpg answer")


class _FakeStdin:
    def close(self):
        pass

    def is_closing(self):
        return True


class _FakeProcess:
    """An already-reaped leader: the ladder's steps 3-4 are no-ops, only the sweep signals."""

    pid = FAKE_PGID
    returncode = 0

    def __init__(self):
        self.stdin = _FakeStdin()

    async def wait(self):
        return 0


class _Records(logging.Handler):
    """Collects every WARNING-or-above record emitted on the proxy's logger."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


async def _fixture(mod):
    """A ChildClient with a fake, reaped leader and pgid FAKE_PGID.

    Awaited INSIDE the coroutine asyncio.run drives: on 3.9 the Locks
    ChildClient.__init__ creates bind to the loop current at construction.
    """
    spec = mod.ChildSpec(name="tf-i", argv=(sys.executable,), env={},
                         call_timeout=1.0, startup_timeout=1.0)
    slot = mod.ChildSlot(spec, H.REPO_ROOT, mod._MAX_FRAME)
    client = mod.ChildClient(slot)
    client.process = _FakeProcess()
    client.pgid = FAKE_PGID
    client._reader_task = None
    return client


def in_process_stop(mod, body):
    """Run `body(rec, handler)` under asyncio.run with os.killpg recorded.

    os.killpg is the recorder and mod._SHUTDOWN_GRACE_S is I_GRACE_S for the
    duration; both, and the logger's level and handlers, are restored in a
    `finally`.  Returns the body's problems (a raise is a problem).
    """
    rec = _KillpgRecorder()
    handler = _Records()
    real_killpg = os.killpg
    saved_grace = mod._SHUTDOWN_GRACE_S
    saved_level = mod.log.level
    mod.log.addHandler(handler)
    mod.log.setLevel(logging.WARNING)
    setattr(os, "killpg", rec)
    mod._SHUTDOWN_GRACE_S = I_GRACE_S

    async def main():
        return await asyncio.wait_for(body(rec, handler), IN_PROCESS_TIMEOUT_S)

    try:
        return asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 -- any raise is the case's finding
        return ["case raised %s: %s" % (type(exc).__name__, exc)]
    finally:
        setattr(os, "killpg", real_killpg)
        mod._SHUTDOWN_GRACE_S = saved_grace
        mod.log.setLevel(saved_level)
        mod.log.removeHandler(handler)


def group_i(suite, fixture_root):
    """I. stdin EOF, grandchild sweep, SIGTERM, the stop ladder.

    I1-I3 and I6 run the real proxy; I4-I5 drive ChildClient.stop() in-process
    against a recorded os.killpg, so the sweep's signal sequence is the
    observable.  Every bound is derived from the loaded module, never typed.
    """
    cids = ("stdin EOF exits, child dead", "group sweep kills grandchild",
            "SIGTERM exits 0, all dead", "swept group never signalled",
            "interrupted sweep resumes", "SIGTERM during restart exits")
    try:
        mod = H.load_module_from_path("ph_proxy_i", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in cids:
            suite.record(GI, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT),
                                      type(exc).__name__, exc)])
        return
    tool = STUB_DEFAULT_TOOL
    bound = 2 * mod._SHUTDOWN_GRACE_S + 2
    term = int(signal.SIGTERM)

    # I1 -- closing stdin ends the proxy within the bound; the child is dead.
    def i1(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        pids = read_pids(proxy.sandbox)
        if not pids:
            return ["control: the stub recorded no pid"], []
        t0 = time.monotonic()
        proxy.close_stdin()
        rc = proxy.wait(bound)
        elapsed = time.monotonic() - t0
        if rc is None:
            problems.append("still running %gs after stdin closed" % bound)
        alive = [pid for pid in pids if not pid_dead(pid)]
        if alive:
            problems.append("child pid(s) %r alive after the proxy exited" % alive)
        return problems, ["exit        : rc %r in %.2fs (bound %gs)" % (rc, elapsed, bound)]

    live_case(suite, GI, cids[0], fixture_root, "i1", one_stub, i1,
              detail=["bound       : 2 * _SHUTDOWN_GRACE_S + 2 (from the module)"])

    # I2 -- the H2 proof: the stub exits by itself on EOF, so only the
    # always-on group sweep can kill the grandchild it left in its group.
    def i2(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        proxy.call(1001, tool, "tf_spawn_grandchild", {})
        reply = proxy.wait_id(1001)
        text = tool_text(reply) if reply is not None else ""
        if not text.isdigit():
            return ["control: tf_spawn_grandchild answered %r" % (reply,)], []
        gpid = int(text)
        lines = ["grandchild  : pid %d (sleep 600, the stub's group)" % gpid]
        try:
            stubs = read_pids(proxy.sandbox)
            if not pid_alive(gpid):
                return ["control: the grandchild is dead before stdin closed"], lines
            t0 = time.monotonic()
            proxy.close_stdin()
            leader_gone = all(pid_dead(pid, mod._SHUTDOWN_GRACE_S) for pid in stubs)
            leader_s = time.monotonic() - t0
            if not leader_gone:
                problems.append("control: the stub outlived _SHUTDOWN_GRACE_S after "
                                "EOF, so the leader ladder (not the sweep) may have "
                                "signalled the group")
            g_dead = pid_dead(gpid, max(0.0, bound - (time.monotonic() - t0)))
            g_s = time.monotonic() - t0
            if not g_dead:
                problems.append("grandchild %d alive %gs after stdin closed" % (gpid, bound))
            rc = proxy.wait(max(0.0, bound - (time.monotonic() - t0)))
            if rc is None:
                problems.append("proxy still running %gs after stdin closed" % bound)
            lines += ["stub exit   : within %.2fs (grace %gs)" % (leader_s, mod._SHUTDOWN_GRACE_S),
                      "grandchild  : dead after %.2fs (bound %gs)" % (g_s, bound),
                      "proxy       : rc %r" % rc]
        finally:
            if pid_alive(gpid):
                kill_pids([gpid])
        return problems, lines

    live_case(suite, GI, cids[1], fixture_root, "i2", one_stub, i2,
              detail=["proof       : the leader exits on EOF; only the group "
                      "sweep reaches the grandchild"])

    # I3 -- SIGTERM: rc 0 within the bound, every child dead.
    def i3_children(sandbox):
        return [stub_child("alpha", "--tool", "tf_alpha_call", "--state-dir", sandbox),
                stub_child("beta", "--tool", "tf_beta_call", "--state-dir", sandbox)]

    def i3(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        pids = (read_pids(proxy.sandbox, "tf_alpha_call")
                + read_pids(proxy.sandbox, "tf_beta_call"))
        if len(pids) != 2:
            return ["control: .pids hold %r, expected 2 spawns" % pids], []
        t0 = time.monotonic()
        proxy.terminate()
        rc = proxy.wait(bound)
        elapsed = time.monotonic() - t0
        if rc is None:
            problems.append("still running %gs after SIGTERM" % bound)
        elif rc != 0:
            problems.append("exit code %r after SIGTERM, expected 0" % rc)
        alive = [pid for pid in pids if not pid_dead(pid)]
        if alive:
            problems.append("child pid(s) %r alive after the proxy exited" % alive)
        return problems, ["exit        : rc %r in %.2fs (bound %gs)" % (rc, elapsed, bound)]

    live_case(suite, GI, cids[2], fixture_root, "i3", i3_children, i3,
              detail=["children    : alpha, beta"])

    # I4 -- R-30: a fully swept incarnation is never signalled again (ESRCH
    # and EPERM both end the sweep).
    async def i4(rec, handler):
        problems = []
        client = await _fixture(mod)
        rec.script = {term: None, 0: ProcessLookupError}
        await client.stop()
        want = [(FAKE_PGID, term), (FAKE_PGID, 0)]
        if rec.calls != want:
            problems.append("ESRCH: killpg calls %r, expected %r" % (rec.calls, want))
        if client.pgid is not None:
            problems.append("ESRCH: pgid %r after the sweep, expected None" % client.pgid)
        del rec.calls[:]
        await client.stop()
        if rec.calls:
            problems.append("ESRCH: a second stop() signalled %r" % rec.calls)

        client = await _fixture(mod)
        del rec.calls[:]
        del handler.records[:]
        rec.script = {term: PermissionError}
        await client.stop()
        if rec.calls != [(FAKE_PGID, term)]:
            problems.append("EPERM: killpg calls %r, expected [(%d, %d)]"
                            % (rec.calls, FAKE_PGID, term))
        if client.pgid is not None:
            problems.append("EPERM: pgid %r after the sweep, expected None" % client.pgid)
        warnings = [r.getMessage() for r in handler.records
                    if r.levelno == logging.WARNING]
        if not any("PermissionError" in m for m in warnings):
            problems.append("EPERM: no WARNING naming PermissionError: %r" % warnings)
        leaked = [m for m in warnings if str(FAKE_PGID) in m]
        if leaked:
            problems.append("EPERM: a WARNING carries the pgid: %r" % leaked)
        del rec.calls[:]
        await client.stop()
        if rec.calls:
            problems.append("EPERM: a second stop() signalled %r" % rec.calls)
        return problems

    suite.record(GI, cids[3], in_process_stop(mod, i4),
                 detail=["fake leader : pid = pgid = %d, already reaped" % FAKE_PGID,
                         "killpg      : recorded, scripted; the real one never called",
                         "grace       : _SHUTDOWN_GRACE_S patched to %g" % I_GRACE_S])

    # I5 -- R-30: a stop cancelled inside the sweep keeps pgid, and the next
    # stop resumes the sweep from SIGTERM.
    async def i5(rec, _handler):
        problems = []
        client = await _fixture(mod)
        rec.script = {}                     # every signal returns: a member lingers
        task = asyncio.ensure_future(client.stop())
        deadline = asyncio.get_running_loop().time() + PROBE_WAIT_S
        while not any(sig == 0 for _pgid, sig in rec.calls):
            if task.done() or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)
        if not any(sig == 0 for _pgid, sig in rec.calls):
            problems.append("control: no signal-0 probe within %gs: %r"
                            % (PROBE_WAIT_S, rec.calls))
        if task.done():
            problems.append("control: stop() finished before the cancel")
        task.cancel()
        await asyncio.wait({task})
        if client.pgid != FAKE_PGID:
            problems.append("pgid %r after the cancelled sweep, expected %d"
                            % (client.pgid, FAKE_PGID))
        first = list(rec.calls)
        del rec.calls[:]
        rec.script = {0: ProcessLookupError}
        await client.stop()
        calls = list(rec.calls)
        if calls[:1] != [(FAKE_PGID, term)]:
            problems.append("the resumed sweep began %r, expected (%d, %d)"
                            % (calls[:1], FAKE_PGID, term))
        if calls[-1:] != [(FAKE_PGID, 0)]:
            problems.append("the resumed sweep ended %r, expected a 0 probe" % calls[-1:])
        if client.pgid is not None:
            problems.append("pgid %r after the resumed sweep, expected None" % client.pgid)
        del rec.calls[:]
        await client.stop()
        if rec.calls:
            problems.append("a third stop() signalled %r" % rec.calls)
        if problems:
            problems.append("cancelled sweep's calls: %r; resumed: %r" % (first, calls))
        return problems

    suite.record(GI, cids[4], in_process_stop(mod, i5),
                 detail=["cancel      : once the recorder saw a signal-0 probe",
                         "resume      : SIGTERM first, then a 0 probe -> ESRCH"])

    # I6 -- R-31: SIGTERM while a restart is inside _bring_up (the second
    # incarnation sleeps before answering initialize).
    stop_bound = mod._STOP_WAIT_S + 2

    def i6(proxy):
        problems = started(proxy)
        if problems:
            return problems, []
        got, _reply = crash_mid_call(proxy, 1061)
        if got:
            return got, []
        deadline = time.monotonic() + PIDS_POLL_S
        pids = read_pids(proxy.sandbox)
        while len(pids) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
            pids = read_pids(proxy.sandbox)
        if len(pids) < 2:
            return ["no second spawn within %gs: .pids %r" % (PIDS_POLL_S, pids)], []
        t0 = time.monotonic()
        proxy.terminate()
        rc = proxy.wait(stop_bound)
        elapsed = time.monotonic() - t0
        if rc is None:
            problems.append("still running %gs after SIGTERM" % stop_bound)
        elif rc != 0:
            problems.append("exit code %r after SIGTERM, expected 0" % rc)
        alive = [pid for pid in pids if not pid_dead(pid, stop_bound)]
        if alive:
            problems.append("pid(s) %r alive after the proxy exited" % alive)
        return problems, ["pids        : %r" % pids,
                          "exit        : rc %r in %.2fs (bound _STOP_WAIT_S + 2 = %gs)"
                          % (rc, elapsed, stop_bound)]

    live_case(suite, GI, cids[5], fixture_root, "i6",
              lambda sb: [stub_child("alpha", "--state-dir", sb, "--init-delay", "30")],
              i6,
              detail=["stub        : --init-delay 30 (every incarnation after the first)"])


# ---------------------------------------------------------------------------
# J. Streamable HTTP  (J1-J45)
# ---------------------------------------------------------------------------

READY_TIMEOUT_S = 10.0       # --ready-file must appear within this
HTTP_TIMEOUT_S = 15.0        # one HTTP request/response round trip
TOKEN_ENV = "MCP_PROXY_TOKEN"
SDK_ACCEPT = "application/json, text/event-stream"
J_MAX_BODY = 1024            # J10: the --max-body-bytes of the refusal proxy
J_SESSION_IDLE_S = 2         # J23: --session-idle of the eviction proxy
J_IDLE_WAIT_S = 3.0          # J23: a session untouched this long is past the idle bound
J_DISCONNECT_AFTER_S = 0.3   # J20: the client hangs up this long after the SSE headers
J_DONE_BOUND_S = 6.0         # J24: the abandoned tf_sleep 3 must finish within this
J_RETRY_BOUND_S = 2.0        # J24: done recorded -> the slot frees within this
J_RAW_TIMEOUT_S = 5.0        # J24: the raw-socket read waits this long for the server's close
SID_RX = re.compile(r"^[\x21-\x7e]{32,}$")


def new_token():
    """48 random URL-safe characters (36 bytes of entropy)."""
    return secrets.token_urlsafe(36)


def http_env(extra=None):
    """The proxy's environment: the test's own, never its MCP_PROXY_TOKEN, plus *extra*."""
    env = H.child_env()
    env.pop(TOKEN_ENV, None)
    if extra:
        env.update(extra)
    return env


def http_argv(sandbox, cfgpath, *extra):
    return [sys.executable, "-B", SERVER, "--project-root", sandbox,
            "--config", cfgpath, "--http"] + [str(a) for a in extra]


def init_body(rid=1):
    """An SDK-shaped initialize request."""
    return {"jsonrpc": "2.0", "id": rid, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "ph-tests-mcp-proxy", "version": "1"}}}


class HttpReply:
    """One HTTP answer: status, headers (case-insensitive lookup), body bytes."""

    def __init__(self, resp, body):
        self.status = resp.status
        self.headers = resp.headers
        self.body = body

    def header(self, name):
        return self.headers.get(name)

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None


def read_sse(resp):
    """Parse a close-delimited text/event-stream line by line until EOF.

    Returns {"lines", "comments", "events", "ids"}: every raw line, the comment
    lines (starting ":"), the (event, data) pairs in order, and any "id:" line.
    """
    out = {"lines": [], "comments": [], "events": [], "ids": []}
    event, data = None, []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        out["lines"].append(line)
        if line == "":
            if event is not None or data:
                out["events"].append((event or "message", "\n".join(data)))
            event, data = None, []
        elif line.startswith(":"):
            out["comments"].append(line)
        elif line.startswith("id:"):
            out["ids"].append(line)
        elif line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].lstrip(" "))
    if event is not None or data:
        out["events"].append((event or "message", "\n".join(data)))
    return out


def sse_messages(sse):
    """(problems, [(event, msg)]): every SSE event's data parsed as JSON, in order."""
    problems, out = [], []
    for event, data in sse["events"]:
        try:
            out.append((event, json.loads(data)))
        except ValueError:
            problems.append("unparseable SSE data %r" % data[:80])
    return problems, out


def is_response(msg):
    return isinstance(msg, dict) and "method" not in msg and "id" in msg


def sse_call(resp, rid):
    """(problems, response, messages): a tools/call answered as SSE with
    exactly one `event: message` carrying the response for *rid*, and no
    `id:` line; *messages* is every parsed event message, in order."""
    problems = []
    ctype = resp.headers.get("Content-Type") or ""
    if resp.status != 200:
        problems.append("status %d, expected 200" % resp.status)
    if not ctype.startswith("text/event-stream"):
        problems.append("Content-Type %r, expected text/event-stream" % ctype)
    sse = read_sse(resp)
    if sse["ids"]:
        problems.append("SSE carries id: line(s) %r" % sse["ids"])
    bad, pairs = sse_messages(sse)
    problems += bad
    responses = []
    for event, msg in pairs:
        if is_response(msg):
            if event != "message":
                problems.append("response under event %r" % event)
            responses.append(msg)
    messages = [msg for _event, msg in pairs]
    if len(responses) != 1:
        problems.append("%d response event(s), expected 1" % len(responses))
        return problems, None, messages
    got = responses[0]
    if type(got.get("id")) is not type(rid) or got.get("id") != rid:
        problems.append("response id %r, expected %r" % (got.get("id"), rid))
    return problems, got, messages


def sse_result(resp, rid):
    """(problems, response): `sse_call` without the event list."""
    problems, got, _messages = sse_call(resp, rid)
    return problems, got


def sse_unanswered(resp, label):
    """Problems unless the SSE stream of a cancelled tools/call ends (EOF)
    with no response event and no `id:` line (Assumption 9)."""
    problems = []
    try:
        sse = read_sse(resp)
    except OSError as exc:
        return ["%s: the stream did not end: %s" % (label, type(exc).__name__)]
    if sse["ids"]:
        problems.append("%s: SSE carries id: line(s) %r" % (label, sse["ids"]))
    bad, pairs = sse_messages(sse)
    problems += ["%s: %s" % (label, b) for b in bad]
    answered = [msg for _event, msg in pairs if is_response(msg)]
    if answered:
        problems.append("%s: the stream carried a response %r" % (label, answered[0]))
    return problems


class HttpClient:
    """The ai-soul SDK's request shape on http.client (client/streamableHttp.js:67-70).

    Headers, in the SDK's order: content-type, accept, authorization,
    mcp-session-id, mcp-protocol-version.  No Origin unless a case sets one.
    """

    def __init__(self, port, token, path="/mcp"):
        self.host = "127.0.0.1"
        self.port = port
        self.token = token
        self.path = path

    def connect(self, timeout=HTTP_TIMEOUT_S):
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def headers(self, sid=None, version=None, token=True, extra=None):
        hdrs = {"content-type": "application/json", "accept": SDK_ACCEPT}
        tok = self.token if token is True else token
        if tok:
            hdrs["authorization"] = "Bearer " + tok
        if sid is not None:
            hdrs["mcp-session-id"] = sid
        if version is not None:
            hdrs["mcp-protocol-version"] = version
        for key, value in (extra or {}).items():
            if value is None:
                hdrs.pop(key, None)
            else:
                hdrs[key] = value
        return hdrs

    def open(self, obj=None, raw=None, method="POST", sid=None, version=None,
             token=True, extra=None, timeout=HTTP_TIMEOUT_S):
        """Send one request; return (connection, response) with the body unread."""
        body = raw if raw is not None else \
            (json.dumps(obj).encode("utf-8") if obj is not None else None)
        conn = self.connect(timeout)
        conn.request(method, self.path, body=body,
                     headers=self.headers(sid, version, token, extra))
        return conn, conn.getresponse()

    def send(self, obj=None, **kw):
        """One complete request/response; the connection is closed afterwards."""
        conn, resp = self.open(obj, **kw)
        try:
            return HttpReply(resp, resp.read())
        finally:
            conn.close()


class HttpProxy:
    """One live `mcp-proxy.py --http` with a 0600 token file and a ready file."""

    def __init__(self, sandbox, cfgpath, extra_argv=(), label="http"):
        self.sandbox = sandbox
        self.token = new_token()
        self.token_path = write_file(os.path.join(sandbox, "tok"), self.token + "\n")
        self.ready_path = os.path.join(sandbox, "ready.json")
        WRITES.append(self.ready_path)
        self.stderr_path = os.path.join(sandbox, label + ".stderr")
        WRITES.append(self.stderr_path)
        self._err = open(self.stderr_path, "wb")
        self.port = None
        self._closed = False
        argv = http_argv(sandbox, cfgpath, "--port", 0, "--ready-file", self.ready_path,
                         "--token-file", self.token_path, *extra_argv)
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=self._err,
                                         cwd=sandbox, env=http_env())
        except Exception:
            self._err.close()
            raise

    def wait_ready(self, timeout=READY_TIMEOUT_S):
        """The listening port from the ready file, polled; None if it never appeared."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open(self.ready_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data.get("port"), int) and data["port"] > 0:
                    self.port = data["port"]
                    return self.port
            except (OSError, ValueError, AttributeError):
                pass
            if self.proc.poll() is not None:
                return None
            time.sleep(0.05)
        return None

    def client(self):
        return HttpClient(self.port, self.token)

    def stderr_text(self):
        try:
            self._err.flush()
        except (OSError, ValueError):
            pass
        try:
            with open(self.stderr_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def close(self, timeout=CLOSE_TIMEOUT_S):
        """SIGTERM, wait, then SIGKILL; idempotent; the exit code (None if unreaped)."""
        if self._closed:
            return self.proc.returncode
        self._closed = True
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.send_signal(signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self.proc.wait(timeout=KILL_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    pass
                return None
        finally:
            try:
                self._err.close()
            except OSError:
                pass


def http_session(suite, fixture_root, label, cases, extra_argv=(), detail=()):
    """Run several J cases against ONE live HTTP proxy, then reap.

    *cases* is a list of (cid, fn) with fn(proxy, client) -> (problems, detail).
    *extra_argv* may be a callable(sandbox) -> argv tuple, for a flag whose
    value is a path inside the batch's own sandbox.  A failed start fails every case; a SIGTERM exit other than 0 and a stub
    that outlived the proxy are findings recorded on every case of the batch.
    """
    sandbox = new_sandbox(fixture_root, label)
    children = one_stub(sandbox)
    cfgpath = write_config(sandbox, children)
    if callable(extra_argv):          # J27: an argv that names a path inside the sandbox
        extra_argv = tuple(extra_argv(sandbox))
    results = []
    tail = []
    proxy = None
    try:
        proxy = HttpProxy(sandbox, cfgpath, extra_argv, label=label)
        if proxy.wait_ready() is None:
            why = ["no ready file within %gs (rc=%r); stderr: %s"
                   % (READY_TIMEOUT_S, proxy.proc.poll(), stderr_tail(proxy))]
            results = [(cid, list(why), []) for cid, _fn in cases]
        else:
            client = proxy.client()
            for cid, fn in cases:
                try:
                    got_problems, got_detail = fn(proxy, client)
                except Exception as exc:  # noqa: BLE001 -- any raise is the finding
                    got_problems, got_detail = (["case raised %s: %s"
                                                 % (type(exc).__name__, exc)], [])
                results.append((cid, list(got_problems), list(got_detail)))
    except Exception as exc:  # noqa: BLE001
        tail.append("harness raised %s: %s" % (type(exc).__name__, exc))
    finally:
        if proxy is not None:
            rc = proxy.close()
            if rc != 0:
                tail.append("proxy exit code %r after SIGTERM, expected 0" % rc)
            err = proxy.stderr_text()
            if proxy.token in err:
                tail.append("the bearer token occurs on the proxy's stderr")
        tail += reap_stubs(sandbox, children)
    if not results:
        results = [(cid, [], []) for cid, _fn in cases]
    for cid, problems, lines in results:
        suite.record(GJ, cid, problems + tail, detail=list(detail) + lines)


def refused_http(sandbox, argv, env_extra=None):
    """(rc, stderr) of a proxy start that must be refused; a hang is killed (rc None)."""
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, cwd=sandbox, env=http_env(env_extra),
                              timeout=START_FAIL_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or b""
        return None, err.decode("utf-8", "replace")
    return proc.returncode, proc.stderr.decode("utf-8", "replace")


def check_refused(sandbox, label, rc, err, needles=(), secret=None):
    """Problems unless rc is 2, stderr has one `mcp-proxy:` line naming every
    needle, the secret never appears, and no stub was spawned."""
    problems = []
    if rc != 2:
        problems.append("%s: exit code %r, expected 2" % (label, rc))
    lines = [ln for ln in err.splitlines() if ln.startswith("mcp-proxy:")]
    if len(lines) != 1:
        problems.append("%s: %d 'mcp-proxy:' stderr line(s), expected 1: %r"
                        % (label, len(lines), err.strip()[-300:]))
    else:
        missing = [n for n in needles if n not in lines[0]]
        if missing:
            problems.append("%s: stderr line %r does not name %r" % (label, lines[0], missing))
    if secret and secret in err:
        problems.append("%s: the token occurs on stderr" % label)
    pids = read_pids(sandbox)
    if pids:
        kill_pids(pids)
        problems.append("%s: a stub was spawned (pids %r)" % (label, pids))
    return problems


def rpc_body(method, params=None, rid=None):
    """One JSON-RPC message; a notification when *rid* is None."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if rid is not None:
        msg["id"] = rid
    return msg


def call_body(rid, function, params=None, token=None):
    """A tools/call of the stub's default tool; `_meta.progressToken` if *token*."""
    p = {"name": STUB_DEFAULT_TOOL,
         "arguments": {"function": function, "params": params or {}}}
    if token is not None:
        p["_meta"] = {"progressToken": token}
    return rpc_body("tools/call", p, rid)


def http_init(client):
    """(problems, sid): initialize, then notifications/initialized on the new session."""
    reply = client.send(init_body())
    sid = reply.header("Mcp-Session-Id")
    if reply.status != 200 or not sid:
        return ["initialize: status %d, session %r" % (reply.status, sid)], None
    note = client.send(rpc_body("notifications/initialized"), sid=sid,
                       version=PROTOCOL_VERSION)
    if note.status != 202:
        return ["notifications/initialized: status %d, expected 202" % note.status], sid
    return [], sid


def open_call(client, sid, body):
    """(connection, response) of a tools/call whose SSE headers have arrived."""
    return client.open(body, sid=sid, version=PROTOCOL_VERSION)


def hang_up(conn, resp):
    """Close a client connection without reading the rest of its stream."""
    for obj in (resp, conn):
        try:
            obj.close()
        except OSError:
            pass


def event_mark(state_dir):
    """How many events the stub has recorded so far (a later wait starts after it)."""
    return len(read_events(state_dir))


def wait_new_event(state_dir, mark, pred, timeout=EVENT_TIMEOUT_S):
    """The first event after index *mark* matching *pred*, polled; else None."""
    deadline = time.monotonic() + timeout
    while True:
        for event in read_events(state_dir)[mark:]:
            if pred(event):
                return event
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def events_since(state_dir, mark, method):
    return [e for e in read_events(state_dir)[mark:] if e.get("method") == method]


def raw_post(client, obj, sid=None, version=PROTOCOL_VERSION, timeout=J_RAW_TIMEOUT_S):
    """(status, headers, closed) of one POST on a raw socket.

    *closed* is True iff the server ended the connection after its answer
    (EOF or reset) within *timeout*; http.client would close it itself on a
    `Connection: close`, so the server's half is only observable here.
    """
    body = json.dumps(obj).encode("utf-8")
    pairs = list(client.headers(sid, version).items())
    status, headers, closed, _data = raw_exchange(client, pairs, body, timeout)
    return status, headers, closed


def raw_exchange(client, pairs, body, timeout=J_RAW_TIMEOUT_S):
    """(status, headers, closed, raw bytes) of one POST on a raw socket whose
    header lines are exactly *pairs* (a list, so a name may repeat), plus Host
    and Content-Length."""
    lines = ["POST %s HTTP/1.1" % client.path,
             "Host: %s:%d" % (client.host, client.port)]
    lines += ["%s: %s" % kv for kv in pairs]
    lines.append("Content-Length: %d" % len(body))
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    return raw_request(client, request, timeout)


def raw_request(client, request, timeout=J_RAW_TIMEOUT_S):
    """(status, headers, closed, raw bytes) of *request* sent verbatim on a raw
    socket, read until the server closes or *timeout* passes; status None when
    the answer has no parseable status line."""
    data, closed = b"", False
    with socket.create_connection((client.host, client.port), timeout=timeout) as sock:
        sock.sendall(request)
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except ConnectionResetError:
                closed = True
                break
            if not chunk:
                closed = True
                break
            data += chunk
    head = data.partition(b"\r\n\r\n")[0].decode("latin-1", "replace").split("\r\n")
    status = None
    parts = head[0].split(" ", 2) if head and head[0] else []
    if len(parts) >= 2 and parts[1].isdigit():
        status = int(parts[1])
    headers = {}
    for line in head[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return status, headers, closed, data


def group_j(suite, fixture_root):
    """J. Streamable HTTP: refuse-to-start rules, the pre-body refusals, then
    sessions, the two answer modes, cancel, eviction and the caps.

    The client replays the ai-soul SDK's exact request headers; a refusal case
    always carries a valid bearer unless the bearer is what it tests, so the
    status observed is the one rule under test.  Cancel and disconnect are
    observed on the stub's side of the wire (its events.jsonl), never inferred
    from the proxy's silence.
    """
    # -- J1-J3: refused starts (rc 2, one stderr line, no child spawned) -----
    def start_sandbox(label):
        sandbox = new_sandbox(fixture_root, label)
        cfgpath = write_config(sandbox, one_stub(sandbox))
        token = new_token()
        tok = write_file(os.path.join(sandbox, "tok"), token + "\n")
        return sandbox, cfgpath, token, tok

    # J1 -- no token source; both sources at once.
    sandbox, cfgpath, token, tok = start_sandbox("j1")
    problems = []
    rc, err = refused_http(sandbox, http_argv(sandbox, cfgpath))
    problems += check_refused(sandbox, "no token", rc, err, ["token"])
    rc, err = refused_http(sandbox, http_argv(sandbox, cfgpath, "--token-file", tok),
                           {TOKEN_ENV: token})
    problems += check_refused(sandbox, "both sources", rc, err, ["token"], secret=token)
    suite.record(GJ, "no token / two tokens -> rc 2", problems,
                 detail=["sources     : none; --token-file + %s" % TOKEN_ENV])

    # J2 -- a 0644 token file.
    sandbox, cfgpath, token, tok = start_sandbox("j2")
    os.chmod(tok, 0o644)
    rc, err = refused_http(sandbox, http_argv(sandbox, cfgpath, "--token-file", tok))
    suite.record(GJ, "0644 token file -> rc 2",
                 check_refused(sandbox, "0644", rc, err, ["--token-file"], secret=token),
                 detail=["token file  : mode 0644 (chmod, umask-proof)"])

    # J3 -- a non-loopback bind without --allow-remote; IPv6 and a host name;
    # a busy port (bound before any spawn: one line, no .pids).
    sandbox, cfgpath, token, tok = start_sandbox("j3")
    problems = []
    for bind, needles in (("0.0.0.0", ["--allow-remote"]), ("::1", ["IPv4"]),
                          ("localhost", ["IPv4"])):
        rc, err = refused_http(sandbox, http_argv(sandbox, cfgpath, "--bind", bind,
                                                  "--token-file", tok))
        problems += check_refused(sandbox, "--bind %s" % bind, rc, err, needles,
                                  secret=token)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        rc, err = refused_http(sandbox, http_argv(sandbox, cfgpath, "--port", busy,
                                                  "--token-file", tok))
    finally:
        holder.close()
    problems += check_refused(sandbox, "busy port", rc, err, secret=token)
    listen_lines = [ln for ln in err.splitlines()
                    if ln.startswith("mcp-proxy: cannot listen on")]
    if len(listen_lines) != 1:
        problems.append("busy port: %d 'mcp-proxy: cannot listen on' line(s), expected 1"
                        % len(listen_lines))
    if os.path.exists(os.path.join(sandbox, STUB_DEFAULT_TOOL + ".pids")):
        problems.append("busy port: the stub's .pids file exists")
    suite.record(GJ, "bad bind / busy port -> rc 2", problems,
                 detail=["binds       : 0.0.0.0 (no --allow-remote), ::1, localhost",
                         "busy port   : a held listener on 127.0.0.1:%d" % busy])

    # -- J4-J12: one live proxy, every refusal before the body is used --------
    def j4(proxy, client):
        reply = client.send(init_body(), token=None)
        problems = []
        if reply.status != 401:
            problems.append("status %d, expected 401" % reply.status)
        if not reply.header("WWW-Authenticate"):
            problems.append("no WWW-Authenticate header")
        return problems, ["answer      : %d, WWW-Authenticate %r"
                          % (reply.status, reply.header("WWW-Authenticate"))]

    def j5(proxy, client):
        problems = []
        wrong = new_token()
        reply = client.send(init_body(), token=wrong)
        if reply.status != 401:
            problems.append("wrong token: status %d, expected 401" % reply.status)
        control = client.send(init_body(), version=PROTOCOL_VERSION)
        if control.status != 200 or not control.header("Mcp-Session-Id"):
            problems.append("control: the right token got %d (session %r)"
                            % (control.status, bool(control.header("Mcp-Session-Id"))))
        return problems, ["answers     : wrong %d, right %d" % (reply.status, control.status)]

    def j6(proxy, client):
        reply = client.send(init_body(), extra={"Origin": "http://evil.example"})
        return ([] if reply.status == 403 else ["status %d, expected 403" % reply.status],
                ["answer      : %d" % reply.status])

    def j7(proxy, client):
        reply = client.send(init_body(), extra={"Host": "evil.example"})
        return ([] if reply.status == 403 else ["status %d, expected 403" % reply.status],
                ["answer      : %d" % reply.status])

    def j8(proxy, client):
        reply = client.send(None, method="GET")
        problems = []
        if reply.status != 405:
            problems.append("status %d, expected 405" % reply.status)
        allow = reply.header("Allow") or ""
        if "POST" not in allow:
            problems.append("Allow %r does not name POST" % allow)
        return problems, ["answer      : %d, Allow %r" % (reply.status, allow)]

    def j9(proxy, client):
        reply = client.send([init_body()])
        problems = []
        if reply.status != 400:
            problems.append("status %d, expected 400" % reply.status)
        problems += expect_error(reply.json(), -32600, "batch")
        return problems, ["answer      : %d %s" % (reply.status, reply.body[:120])]

    def j10(proxy, client):
        body = init_body()
        body["params"]["tf_pad"] = ""
        base = len(json.dumps(body).encode("utf-8"))
        body["params"]["tf_pad"] = "x" * (J_MAX_BODY + 1 - base)
        raw = json.dumps(body).encode("utf-8")
        reply = client.send(raw=raw)
        problems = []
        if len(raw) != J_MAX_BODY + 1:
            problems.append("control: body is %d bytes, expected %d"
                            % (len(raw), J_MAX_BODY + 1))
        if reply.status != 413:
            problems.append("status %d, expected 413" % reply.status)
        return problems, ["answer      : %d for %d bytes (cap %d)"
                          % (reply.status, len(raw), J_MAX_BODY)]

    def j11(proxy, client):
        payload = json.dumps(init_body()).encode("utf-8")
        chunked = b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload)
        reply = client.send(raw=chunked, extra={"Transfer-Encoding": "chunked"})
        return ([] if reply.status == 411 else ["status %d, expected 411" % reply.status],
                ["answer      : %d" % reply.status])

    def j12(proxy, client):
        reply = client.send(init_body(), extra={"content-type": "text/plain"})
        return ([] if reply.status == 415 else ["status %d, expected 415" % reply.status],
                ["answer      : %d" % reply.status])

    http_session(suite, fixture_root, "j-refuse", [
        ("no Authorization -> 401", j4),
        ("wrong token -> 401", j5),
        ("foreign Origin -> 403", j6),
        ("foreign Host -> 403", j7),
        ("GET -> 405 with Allow", j8),
        ("batch -> 400 -32600", j9),
        ("body over the cap -> 413", j10),
        ("chunked -> 411", j11),
        ("text/plain -> 415", j12),
    ], extra_argv=("--max-body-bytes", J_MAX_BODY),
        detail=["proxy       : --http --port 0 --max-body-bytes %d, token file 0600"
                % J_MAX_BODY])

    # -- J13-J22: one live proxy, sessions and the two answer modes ----------
    try:
        declared_version = H.load_module_from_path(
            "ph_proxy_j", SERVER).McpServer.PROTOCOL_VERSION
    except Exception as exc:  # noqa: BLE001 -- an import failure only fails J13
        declared_version = "cannot import the proxy: %s: %s" % (type(exc).__name__, exc)

    def j13(proxy, client):
        reply = client.send(init_body())
        problems = []
        sid = reply.header("Mcp-Session-Id") or ""
        if reply.status != 200:
            problems.append("status %d, expected 200" % reply.status)
        if not (reply.header("Content-Type") or "").startswith("application/json"):
            problems.append("Content-Type %r, expected application/json"
                            % reply.header("Content-Type"))
        if not SID_RX.match(sid):
            problems.append("Mcp-Session-Id %r does not match %s" % (sid, SID_RX.pattern))
        result = (reply.json() or {}).get("result")
        got = result.get("protocolVersion") if isinstance(result, dict) else None
        if got != declared_version:
            problems.append("protocolVersion %r, expected %r" % (got, declared_version))
        return problems, ["session id  : %d chars; protocolVersion %r" % (len(sid), got)]

    def j14(proxy, client):
        reply = client.send(rpc_body("ping", {}, 14), version=PROTOCOL_VERSION)
        problems = [] if reply.status == 400 else ["status %d, expected 400" % reply.status]
        problems += expect_error(reply.json(), -32600, "no session")
        return problems, ["answer      : %d %s" % (reply.status, reply.body[:120])]

    def j15(proxy, client):
        sid = "tf-no-such-session-" + secrets.token_urlsafe(24)
        reply = client.send(rpc_body("ping", {}, 15), sid=sid, version=PROTOCOL_VERSION)
        problems = [] if reply.status == 404 else ["status %d, expected 404" % reply.status]
        problems += expect_error(reply.json(), -32001, "unknown session")
        msg = ((reply.json() or {}).get("error") or {}).get("message")
        if not problems and msg != "Session not found":
            problems.append("message %r, expected 'Session not found'" % msg)
        return problems, ["answer      : %d %s" % (reply.status, reply.body[:120])]

    def j16(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        bad = client.send(rpc_body("ping", {}, 161), sid=sid, version="1999-01-01")
        if bad.status != 400:
            problems.append("1999-01-01: status %d, expected 400" % bad.status)
        bare = client.send(rpc_body("ping", {}, 162), sid=sid)
        if bare.status != 200:
            problems.append("no header: status %d, expected 200" % bare.status)
        elif (bare.json() or {}).get("id") != 162 or "result" not in (bare.json() or {}):
            problems.append("no header: answer %r, expected the ping result" % bare.body[:120])
        return problems, ["answers     : 1999-01-01 %d, header absent %d"
                          % (bad.status, bare.status)]

    def j17(proxy, client):
        reply = client.send(init_body())
        sid = reply.header("Mcp-Session-Id")
        if reply.status != 200 or not sid:
            return ["initialize: status %d, session %r" % (reply.status, sid)], []
        note = client.send(rpc_body("notifications/initialized"), sid=sid,
                           version=PROTOCOL_VERSION)
        problems = []
        if note.status != 202:
            problems.append("status %d, expected 202" % note.status)
        if note.body:
            problems.append("body %r, expected empty" % note.body[:120])
        return problems, ["answer      : %d, %d body byte(s)" % (note.status, len(note.body))]

    def j18(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        params = {"x": 1, "u": "é"}
        conn, resp = open_call(client, sid, call_body(18, "tf_echo", params))
        try:
            got_problems, got, messages = sse_call(resp, 18)
        finally:
            hang_up(conn, resp)
        problems += got_problems
        if len(messages) != 1:
            problems.append("%d SSE event(s), expected exactly 1" % len(messages))
        want = json.dumps({"function": "tf_echo", "params": params}, sort_keys=True)
        if got is not None and tool_text(got) != want:
            problems.append("text %r, expected %r" % (tool_text(got), want))
        listing = client.send(rpc_body("tools/list", {}, 181), sid=sid,
                              version=PROTOCOL_VERSION)
        ctype = listing.header("Content-Type") or ""
        if listing.status != 200 or not ctype.startswith("application/json"):
            problems.append("tools/list: %d %r, expected 200 application/json"
                            % (listing.status, ctype))
        tools = ((listing.json() or {}).get("result") or {}).get("tools") or []
        if STUB_DEFAULT_TOOL not in [t.get("name") for t in tools if isinstance(t, dict)]:
            problems.append("tools/list does not name %s" % STUB_DEFAULT_TOOL)
        return problems, ["modes       : tools/call %s, tools/list %s"
                          % (resp.headers.get("Content-Type"), ctype)]

    def j19(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        token = "tf-j19"
        conn, resp = open_call(client, sid, call_body(19, "tf_progress", {"n": 3}, token))
        try:
            got_problems, got, messages = sse_call(resp, 19)
        finally:
            hang_up(conn, resp)
        problems += got_problems
        kinds = ["progress" if is_progress(m) else "response" if is_response(m) else "other"
                 for m in messages]
        if kinds != ["progress"] * 3 + ["response"]:
            problems.append("event order %r, expected 3 progress then the response" % kinds)
        tokens = [progress_token(m) for m in messages if is_progress(m)]
        if tokens != [token] * 3:
            problems.append("progress tokens %r, expected %r x3" % (tokens, token))
        if got is not None and tool_text(got) != "tf progress 3":
            problems.append("text %r, expected 'tf progress 3'" % tool_text(got))
        return problems, ["events      : %s" % ", ".join(kinds)]

    def j20(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        state = proxy.sandbox
        mark = event_mark(state)
        conn, resp = open_call(client, sid, call_body(20, "tf_sleep", {"seconds": 2}))
        call_ev = wait_new_event(state, mark, lambda e: e.get("method") == "tools/call")
        time.sleep(J_DISCONNECT_AFTER_S)
        hang_up(conn, resp)
        if call_ev is None:
            return ["the stub never recorded the tools/call"], []
        child_id = call_ev.get("id")
        conn2, resp2 = open_call(client, sid, call_body(201, "tf_echo", {"after": "j20"}))
        try:
            got_problems, _got = sse_result(resp2, 201)
        finally:
            hang_up(conn2, resp2)
        problems += ["follow-up: %s" % p for p in got_problems]
        done = wait_new_event(state, mark, lambda e: e.get("method") == "tf_sleep/done"
                              and e.get("id") == child_id)
        if done is None:
            problems.append("the abandoned tf_sleep never completed in the stub")
        cancels = events_since(state, mark, "notifications/cancelled")
        if cancels:
            problems.append("the disconnect reached the stub as a cancel: %r" % cancels)
        return problems, ["hang-up     : %gs after the SSE headers; child id %r"
                          % (J_DISCONNECT_AFTER_S, child_id)]

    def j21(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        state = proxy.sandbox
        mark = event_mark(state)
        conn, resp = open_call(client, sid, call_body(21, "tf_sleep", {"seconds": 5}))
        try:
            call_ev = wait_new_event(state, mark, lambda e: e.get("method") == "tools/call")
            if call_ev is None:
                return ["the stub never recorded the tools/call"], []
            child_id = call_ev.get("id")
            note = client.send(rpc_body("notifications/cancelled",
                                        {"requestId": 21, "reason": "tf j21 cancel"}),
                               sid=sid, version=PROTOCOL_VERSION)
            if note.status != 202:
                problems.append("cancel: status %d, expected 202" % note.status)
            problems += sse_unanswered(resp, "cancelled stream")
        finally:
            hang_up(conn, resp)
        cancel = wait_new_event(state, mark,
                                lambda e: e.get("method") == "notifications/cancelled")
        if cancel is None:
            problems.append("the stub never recorded the cancel")
        elif type(cancel.get("cancel_id")) is not type(child_id) \
                or cancel.get("cancel_id") != child_id:
            problems.append("cancel_id %r != the stub's tools/call id %r"
                            % (cancel.get("cancel_id"), child_id))
        return problems, ["ids         : upstream 21 -> child %r" % child_id]

    def j22(proxy, client):
        problems = []
        sids = []
        for _i in range(2):
            got_problems, sid = http_init(client)
            if got_problems:
                return got_problems, []
            sids.append(sid)
        counts = (3, 4)          # each session's own progress total tells them apart
        out = [None, None]
        gate = threading.Barrier(2)

        def one(i):
            try:
                gate.wait(HTTP_TIMEOUT_S)
                conn, resp = open_call(client, sids[i],
                                       call_body(1, "tf_progress", {"n": counts[i]}, "t1"))
                try:
                    out[i] = sse_call(resp, 1)
                finally:
                    hang_up(conn, resp)
            except Exception as exc:  # noqa: BLE001 -- recorded as the session's finding
                out[i] = (["raised %s: %s" % (type(exc).__name__, exc)], None, [])

        threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(HTTP_TIMEOUT_S + 5)
        for i, n in enumerate(counts):
            label = "session %d" % (i + 1)
            if out[i] is None:
                problems.append("%s: no answer" % label)
                continue
            got_problems, got, messages = out[i]
            problems += ["%s: %s" % (label, p) for p in got_problems]
            if got is not None and tool_text(got) != "tf progress %d" % n:
                problems.append("%s: text %r, expected 'tf progress %d'"
                                % (label, tool_text(got), n))
            progress = [m for m in messages if is_progress(m)]
            totals = [(m.get("params") or {}).get("total") for m in progress]
            tokens = [progress_token(m) for m in progress]
            if totals != [n] * n or tokens != ["t1"] * n:
                problems.append("%s: progress totals %r tokens %r, expected only its own %d"
                                % (label, totals, tokens, n))
        return problems, ["sessions    : id 1, token 't1' in both; tf_progress n=3 and n=4"]

    http_session(suite, fixture_root, "j-session", [
        ("initialize -> session id", j13),
        ("no session -> 400", j14),
        ("unknown session -> 404", j15),
        ("protocol version header", j16),
        ("notification -> 202", j17),
        ("tools/call is SSE, list JSON", j18),
        ("SSE progress then response", j19),
        ("disconnect is not cancel", j20),
        ("cancelled -> stream ends", j21),
        ("two sessions, same id/token", j22),
    ], detail=["proxy       : --http --port 0, default caps, token file 0600"])

    # -- J23: DELETE and idle eviction both cancel the session's calls -------
    def j23(proxy, client):
        state = proxy.sandbox
        problems, sid = http_init(client)
        if problems:
            return problems, []

        def cancelled_at_stub(mark, child_id, label):
            ev = wait_new_event(state, mark, lambda e: e.get("method") == "notifications/cancelled"
                                and e.get("cancel_id") == child_id)
            return [] if ev is not None else ["%s: the stub never saw a cancel for %r"
                                              % (label, child_id)]

        # DELETE: 200, reuse 404, the in-flight call cancelled, its stream unanswered.
        mark = event_mark(state)
        conn, resp = open_call(client, sid, call_body(231, "tf_sleep", {"seconds": 5}))
        try:
            call_ev = wait_new_event(state, mark, lambda e: e.get("method") == "tools/call")
            if call_ev is None:
                return ["DELETE: the stub never recorded the tools/call"], []
            gone = client.send(None, method="DELETE", sid=sid)
            if gone.status != 200:
                problems.append("DELETE: status %d, expected 200" % gone.status)
            problems += sse_unanswered(resp, "DELETE")
        finally:
            hang_up(conn, resp)
        problems += cancelled_at_stub(mark, call_ev.get("id"), "DELETE")
        reuse = client.send(rpc_body("ping", {}, 232), sid=sid, version=PROTOCOL_VERSION)
        if reuse.status != 404:
            problems.append("DELETE: reuse status %d, expected 404" % reuse.status)

        # Eviction: C idles past --session-idle with a call in flight; D's
        # initialize runs the eviction.
        got_problems, sid_c = http_init(client)
        if got_problems:
            return problems + ["eviction: C %s" % p for p in got_problems], []
        mark = event_mark(state)
        conn, resp = open_call(client, sid_c, call_body(233, "tf_sleep", {"seconds": 10}))
        try:
            call_ev = wait_new_event(state, mark, lambda e: e.get("method") == "tools/call")
            if call_ev is None:
                return problems + ["eviction: the stub never recorded the tools/call"], []
            time.sleep(J_IDLE_WAIT_S)
            got_problems, _sid_d = http_init(client)
            problems += ["eviction: D %s" % p for p in got_problems]
            problems += sse_unanswered(resp, "eviction")
        finally:
            hang_up(conn, resp)
        problems += cancelled_at_stub(mark, call_ev.get("id"), "eviction")
        ping = client.send(rpc_body("ping", {}, 234), sid=sid_c, version=PROTOCOL_VERSION)
        if ping.status != 404:
            problems.append("eviction: ping on C status %d, expected 404" % ping.status)
        return problems, ["DELETE      : %d, reuse %d" % (gone.status, reuse.status),
                          "eviction    : idle %gs, D after %gs, ping on C %d"
                          % (J_SESSION_IDLE_S, J_IDLE_WAIT_S, ping.status)]

    http_session(suite, fixture_root, "j-evict", [
        ("DELETE and eviction cancel", j23),
    ], extra_argv=("--session-idle", J_SESSION_IDLE_S),
        detail=["proxy       : --http --port 0 --session-idle %d" % J_SESSION_IDLE_S])

    # -- J24: the session cap and the in-flight cap --------------------------
    def j24(proxy, client):
        state = proxy.sandbox
        problems = []
        sids = []
        for _i in range(2):
            got_problems, sid = http_init(client)
            if got_problems:
                return got_problems, []
            sids.append(sid)
        third = client.send(init_body())
        if third.status != 503:
            problems.append("third initialize: status %d, expected 503" % third.status)
        mark = event_mark(state)
        conn, resp = open_call(client, sids[0], call_body(241, "tf_sleep", {"seconds": 3}))
        hang_up(conn, resp)
        if resp.status != 200:
            return problems + ["tf_sleep: status %d, expected 200" % resp.status], []
        echo = call_body(242, "tf_echo", {"j24": True})
        status, headers, closed = raw_post(client, echo, sid=sids[1])
        if status != 503:
            problems.append("busy: status %r, expected 503" % status)
        if (headers.get("connection") or "").lower() != "close":
            problems.append("busy: Connection %r, expected close" % headers.get("connection"))
        if not closed:
            problems.append("busy: the server kept the connection open past %gs"
                            % J_RAW_TIMEOUT_S)
        done = wait_new_event(state, mark, lambda e: e.get("method") == "tf_sleep/done",
                              J_DONE_BOUND_S)
        if done is None:
            return problems + ["the abandoned tf_sleep 3 never completed within %gs"
                               % J_DONE_BOUND_S], []
        attempts, last = 0, None
        deadline = time.monotonic() + J_RETRY_BOUND_S
        while True:
            attempts += 1
            conn, resp = open_call(client, sids[1], echo)
            try:
                if resp.status == 200:
                    got_problems, _got = sse_result(resp, 242)
                    problems += ["freed: %s" % p for p in got_problems]
                    last = 200
                    break
                last = resp.status
            finally:
                hang_up(conn, resp)
            if time.monotonic() >= deadline:
                problems.append("freed: status %r after %d attempt(s), expected 200"
                                % (last, attempts))
                break
            time.sleep(0.1)
        return problems, ["sessions    : third initialize %d" % third.status,
                          "in flight   : busy %r (Connection %r, closed %s); freed %r "
                          "after %d attempt(s)" % (status, headers.get("connection"),
                                                   closed, last, attempts)]

    http_session(suite, fixture_root, "j-caps", [
        ("session and in-flight caps", j24),
    ], extra_argv=("--max-sessions", 2, "--max-inflight", 1),
        detail=["proxy       : --http --port 0 --max-sessions 2 --max-inflight 1"])

    group_j_hardening(suite, fixture_root)


# -- J25-J33: the security and liveness rows of group J ----------------------

J_HANDLER_CLASS = "_ProxyHttpHandler"
J26_SEED = ("body", "msg", "raw")        # names seeded as payload (WL.LEAK)
J_OK_ORIGIN = "http://ok.example"        # J28: the --allowed-origin of the allowlist proxy
J_EVIL_ORIGIN = "http://evil.example"    # J28: the control Origin, still refused
J_MAX_CONNECTIONS = 2                    # J29: --max-connections of the pre-auth proxy
J_PARTIAL_LINE = b"POST /mcp HTTP/1.1\r\n"   # J29: a request line, then nothing
J_HEADERS_BOUND_S = 1.0                  # J31: SSE headers must arrive within this
J_SHUTDOWN_SLEEP_S = 30                  # J33: the tf_sleep in flight at SIGTERM
J_SINK_EXTRA = 10                        # J30: progress items fed past the cap

# J25/J26 plant-and-detect controls: each must be FLAGGED by its own check.
J25_PLANT = '''\
class _ProxyHttpHandler:
    def _precheck(self):
        if presented == self.server.settings.token:
            return True
'''
J26_PLANT = '''\
class _ProxyHttpHandler:
    def _post(self):
        raw = self.rfile.read(1)
        log.debug("got %s", raw)
    def _precheck(self):
        log.debug("auth %s", self.headers.get("Authorization"))
'''


def _handler_class(tree):
    """The ClassDef of the HTTP handler, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == J_HANDLER_CLASS:
            return node
    return None


def j25_token_compare(cls):
    """(compare_digest calls, [(lineno, what)]): every `==`-style Compare whose
    operand reaches a name or attribute `token` is a finding."""
    calls, found = 0, []
    for node in ast.walk(cls):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "compare_digest" and _is_name(node.func.value, "hmac")):
            calls += 1
        elif isinstance(node, ast.Compare):
            for operand in [node.left] + list(node.comparators):
                for sub in ast.walk(operand):
                    if _is_name(sub, "token") or (isinstance(sub, ast.Attribute)
                                                  and sub.attr == "token"):
                        found.append((node.lineno, "Compare on token: %s" % WL._src(node)))
                        break
    return calls, sorted(set(found))


def j26_handler_logs(cls):
    """(log calls, problems, per-method lines): every log call in every handler
    method judged by test_wire_log's taint rule, plus `self.headers` = LEAK."""
    total, problems, lines = 0, [], []
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env = {seed: WL.LEAK for seed in J26_SEED}
        site = WL.Site("%s.%s" % (cls.name, fn.name))
        WL._propagate(env, fn)
        WL._collect(site, fn, env)
        if WL.PAYLOAD in site.problems:
            problems += ["%s: %s" % (fn.name, d) for d in site.detail
                         if d.startswith(WL.PAYLOAD)]
        calls = [n for n in ast.walk(fn) if WL._log_call_base(n) is not None]
        for call in calls:
            for arg in list(call.args) + [kw.value for kw in call.keywords]:
                if any(isinstance(sub, ast.Attribute) and sub.attr == "headers"
                       and _is_name(sub.value, "self") for sub in ast.walk(arg)):
                    problems.append("%s: line %d: self.headers inside a log call: %s"
                                    % (fn.name, call.lineno, WL._src(call)))
                    break
        total += len(calls)
        if calls:
            lines.append("%-18s: %d log call(s), shape %s"
                         % (fn.name, len(calls),
                            "silent" if site.shape is None else "/".join(site.shape)))
    return total, problems, lines


def socket_closed_by_peer(sock, deadline):
    """(closed, data): read *sock* until the server ends it (EOF or reset) or
    the monotonic *deadline* passes; *closed* is False on the deadline."""
    data = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return False, data
        sock.settimeout(left)
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            return False, data
        except ConnectionResetError:
            return True, data
        if not chunk:
            return True, data
        data += chunk


def group_j_hardening(suite, fixture_root):
    """J25-J33: constant-time auth and structure-only logs (static), no secret
    in the logs, the Origin allowlist, the pre-auth timeout, the bounded sink,
    immediate SSE headers with keepalive, the 202 loop half, and SIGTERM with
    live HTTP traffic.  Every bound is read from the loaded module, never typed."""
    rel = os.path.relpath(SERVER, H.REPO_ROOT)
    try:
        mod = H.load_module_from_path("ph_proxy_jh", SERVER)
        mod_why = None
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the module-bound rows
        mod, mod_why = None, "cannot import the proxy: %s: %s" % (type(exc).__name__, exc)
    try:
        with open(SERVER, "r", encoding="utf-8") as fh:
            cls = _handler_class(ast.parse(fh.read(), filename=SERVER))
        cls_why = None if cls is not None else "no class %s in %s" % (J_HANDLER_CLASS, rel)
    except (OSError, SyntaxError) as exc:
        cls, cls_why = None, "cannot parse %s: %s: %s" % (rel, type(exc).__name__, exc)

    # J25 -- the bearer is compared with hmac.compare_digest, never with ==.
    problems, lines = [], []
    if cls is None:
        problems.append(cls_why)
    else:
        calls, found = j25_token_compare(cls)
        problems += _findings(found)
        if calls == 0:
            problems.append("no hmac.compare_digest call in %s" % J_HANDLER_CLASS)
        lines.append("live        : %d hmac.compare_digest call(s), %d token Compare(s)"
                     % (calls, len(found)))
    plant_calls, plant_found = j25_token_compare(_handler_class(ast.parse(J25_PLANT)))
    if plant_calls != 0 or not plant_found:
        problems.append("control: the planted `== settings.token` was not flagged "
                        "(%d compare_digest, %d finding(s))" % (plant_calls, len(plant_found)))
    lines.append("control     : planted `presented == self.server.settings.token` -> "
                 "%d finding(s), 0 compare_digest" % len(plant_found))
    suite.record(GJ, "bearer: compare_digest, no ==", problems, detail=lines)

    # J26 -- every handler log call is structure only.
    problems, lines = [], ["seed        : %s = LEAK; self.headers in a log call = LEAK"
                           % ", ".join(J26_SEED)]
    if cls is None:
        problems.append(cls_why)
    else:
        total, got, method_lines = j26_handler_logs(cls)
        problems += got
        lines += method_lines
        if total == 0:
            problems.append("no log call found in %s: the check would pass vacuously"
                            % J_HANDLER_CLASS)
    _total, plant_problems, _lines = j26_handler_logs(_handler_class(ast.parse(J26_PLANT)))
    plant_hit = (any(p.startswith("_post:") for p in plant_problems),
                 any(p.startswith("_precheck:") for p in plant_problems))
    if plant_hit != (True, True):
        problems.append("control: planted leaks not both flagged (raw %s, headers %s)"
                        % plant_hit)
    lines.append("control     : planted log of `raw` and of self.headers -> both flagged")
    suite.record(GJ, "handler logs structure only", problems, detail=lines)

    # J27 -- with --debug --log-file, neither the token nor a full session id is logged.
    def j27(proxy, client):
        log_path = os.path.join(proxy.sandbox, "proxy.log")
        problems, sids = [], []
        reply = client.send(init_body())                                       # J13
        sid = reply.header("Mcp-Session-Id")
        if reply.status != 200 or not sid:
            return ["initialize: status %d, session %r" % (reply.status, sid)], []
        sids.append(sid)
        note = client.send(rpc_body("notifications/initialized"), sid=sid,
                           version=PROTOCOL_VERSION)
        if note.status != 202:
            problems.append("notifications/initialized: status %d" % note.status)
        conn, resp = open_call(client, sid, call_body(271, "tf_echo", {"j27": 1}))   # J18
        try:
            problems += ["echo: %s" % p for p in sse_result(resp, 271)[0]]
        finally:
            hang_up(conn, resp)
        listing = client.send(rpc_body("tools/list", {}, 272), sid=sid,
                              version=PROTOCOL_VERSION)
        if listing.status != 200:
            problems.append("tools/list: status %d" % listing.status)
        conn, resp = open_call(client, sid, call_body(273, "tf_progress", {"n": 3},   # J19
                                                      "tf-j27"))
        try:
            problems += ["progress: %s" % p for p in sse_result(resp, 273)[0]]
        finally:
            hang_up(conn, resp)
        state = proxy.sandbox                                                  # J23
        mark = event_mark(state)
        conn, resp = open_call(client, sid, call_body(274, "tf_sleep", {"seconds": 5}))
        try:
            if wait_new_event(state, mark, lambda e: e.get("method") == "tools/call") is None:
                problems.append("DELETE: the stub never recorded the tools/call")
            gone = client.send(None, method="DELETE", sid=sid)
            if gone.status != 200:
                problems.append("DELETE: status %d, expected 200" % gone.status)
            problems += sse_unanswered(resp, "DELETE")
        finally:
            hang_up(conn, resp)
        rc = proxy.close()            # every log line, shutdown included, is in the file now
        if rc != 0:
            problems.append("proxy exit code %r after SIGTERM, expected 0" % rc)
        try:
            mode = os.stat(log_path).st_mode & 0o777
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            return problems + ["log file unreadable: %s" % type(exc).__name__], []
        err = proxy.stderr_text()
        if mode != 0o600:
            problems.append("log file mode %o, expected 600" % mode)
        for where, body in (("log file", text), ("stderr", err)):
            if proxy.token in body:
                problems.append("the bearer token occurs in the %s" % where)
            for s in sids:
                if s in body:
                    problems.append("a full session id occurs in the %s" % where)
        # Controls: the file really carries the debug wire log and the 6-char prefixes.
        if "<- http method=" not in text:
            problems.append("control: no '<- http method=' line in the log file "
                            "(was DEBUG enabled?)")
        if not all(s[:6] in text for s in sids):
            problems.append("control: a session's 6-char prefix is missing from the log file")
        return problems, ["log file    : mode %o, %d line(s), %d stderr byte(s)"
                          % (mode, text.count("\n"), len(err)),
                          "driven      : initialize, tools/call echo + progress, "
                          "tools/list, DELETE with a call in flight"]

    http_session(suite, fixture_root, "j-log", [
        ("no token or sid in the logs", j27),
    ], extra_argv=lambda sandbox: ("--debug", "--log-file",
                                   os.path.join(sandbox, "proxy.log")),
        detail=["proxy       : --http --port 0 --debug --log-file <sandbox>/proxy.log"])

    # J28 -- an absent Origin is accepted; an allowlisted one is too.
    def j28(proxy, client):
        problems = []
        bare = client.send(init_body())
        if bare.status != 200 or not bare.header("Mcp-Session-Id"):
            problems.append("no Origin: status %d, expected 200 with a session" % bare.status)
        ok = client.send(init_body(), extra={"Origin": J_OK_ORIGIN})
        if ok.status != 200 or not ok.header("Mcp-Session-Id"):
            problems.append("Origin %s: status %d, expected 200 with a session"
                            % (J_OK_ORIGIN, ok.status))
        evil = client.send(init_body(), extra={"Origin": J_EVIL_ORIGIN})
        if evil.status != 403:
            problems.append("control: Origin %s got %d, expected 403"
                            % (J_EVIL_ORIGIN, evil.status))
        return problems, ["answers     : none %d, allowed %d, foreign %d (control)"
                          % (bare.status, ok.status, evil.status)]

    http_session(suite, fixture_root, "j-origin", [
        ("Origin absent / allowlisted", j28),
    ], extra_argv=("--allowed-origin", J_OK_ORIGIN),
        detail=["proxy       : --http --port 0 --allowed-origin %s" % J_OK_ORIGIN])

    # J29 -- a silent or stalled connection is closed before auth; its slot comes back.
    def j29(proxy, client):
        if mod is None:
            return [mod_why], []
        bound = mod._HTTP_HEADER_TIMEOUT_S + 3
        problems = []
        silent = socket.create_connection((client.host, client.port), timeout=bound)
        stalled = socket.create_connection((client.host, client.port), timeout=bound)
        t0 = time.monotonic()
        try:
            stalled.sendall(J_PARTIAL_LINE)
            # Control: both slots are held, so a third connection is refused 503.
            with socket.create_connection((client.host, client.port),
                                          timeout=J_RAW_TIMEOUT_S) as third:
                _closed, head = socket_closed_by_peer(third,
                                                      time.monotonic() + J_RAW_TIMEOUT_S)
            if not head.startswith(b"HTTP/1.1 503"):
                problems.append("control: a third connection got %r, expected 503 "
                                "(the slots were not held)" % head[:40])
            elapsed = {}
            for label, sock in (("silent", silent), ("stalled", stalled)):
                closed, _data = socket_closed_by_peer(sock, t0 + bound)
                elapsed[label] = time.monotonic() - t0
                if not closed:
                    problems.append("%s: still open %.1fs after connect (bound %gs)"
                                    % (label, elapsed[label], bound))
        finally:
            for sock in (silent, stalled):
                try:
                    sock.close()
                except OSError:
                    pass
        deadline = t0 + bound
        attempts, status = 0, None
        while True:
            attempts += 1
            try:
                reply = client.send(init_body())
                status = reply.status
                if status == 200 and reply.header("Mcp-Session-Id"):
                    break
            except (OSError, http.client.HTTPException) as exc:
                status = type(exc).__name__
            if time.monotonic() >= deadline:
                problems.append("initialize after the idle sockets closed: %r after "
                                "%d attempt(s), expected 200" % (status, attempts))
                break
            time.sleep(0.1)
        return problems, ["closed      : silent %.1fs, stalled %.1fs (bound %gs = "
                          "_HTTP_HEADER_TIMEOUT_S + 3)"
                          % (elapsed.get("silent", -1), elapsed.get("stalled", -1), bound),
                          "slots       : third connection 503 while held; initialize "
                          "%r after %d attempt(s)" % (status, attempts)]

    http_session(suite, fixture_root, "j-preauth", [
        ("pre-auth timeout frees the slot", j29),
    ], extra_argv=("--max-connections", J_MAX_CONNECTIONS),
        detail=["proxy       : --http --port 0 --max-connections %d" % J_MAX_CONNECTIONS])

    # J30 -- the sink drops progress past its cap, never the response or the sentinel.
    problems, lines = [], []
    if mod is None:
        problems.append(mod_why)
    else:
        cap = mod._HTTP_SINK_PROGRESS_CAP
        q = queue.Queue()
        sink = mod._bounded_sink(q)
        for i in range(cap + J_SINK_EXTRA):
            sink({"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {"progressToken": "tf-j30", "progress": i}})
        response = {"jsonrpc": "2.0", "id": 30, "result": {}}
        sink(response)
        sink(None)
        size = q.qsize()
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        if size != cap + 2:
            problems.append("qsize %d, expected %d (cap + response + sentinel)"
                            % (size, cap + 2))
        if len(items) < 2 or items[-2] is not response or items[-1] is not None:
            problems.append("the last two items are not the response and None")
        kept = [it["params"]["progress"] for it in items[:-2] if isinstance(it, dict)]
        if kept != list(range(cap)):
            problems.append("kept progress is not the first %d in order" % cap)
        lines.append("fed         : %d progress (cap %d + %d), the response, None"
                     % (cap + J_SINK_EXTRA, cap, J_SINK_EXTRA))
        lines.append("queued      : %d" % size)
    suite.record(GJ, "bounded sink keeps the response", problems, detail=lines)

    # J31 -- a tools/call gets its SSE headers at once and a keepalive while it waits.
    def j31(proxy, client):
        if mod is None:
            return [mod_why], []
        problems, sid = http_init(client)
        if problems:
            return problems, []
        seconds = mod._HTTP_KEEPALIVE_S + 2
        t0 = time.monotonic()
        conn, resp = client.open(call_body(31, "tf_sleep", {"seconds": seconds}), sid=sid,
                                 version=PROTOCOL_VERSION, timeout=seconds + HTTP_TIMEOUT_S)
        head_s = time.monotonic() - t0
        try:
            ctype = resp.headers.get("Content-Type") or ""
            if resp.status != 200 or not ctype.startswith("text/event-stream"):
                problems.append("headers: %d %r, expected 200 text/event-stream"
                                % (resp.status, ctype))
            if head_s > J_HEADERS_BOUND_S:
                problems.append("headers after %.2fs, expected within %gs"
                                % (head_s, J_HEADERS_BOUND_S))
            sse = read_sse(resp)
        finally:
            hang_up(conn, resp)
        total_s = time.monotonic() - t0
        body = [ln for ln in sse["lines"] if ln]
        first = body[0] if body else None
        if first != ": keepalive":
            problems.append("first non-empty line %r, expected ': keepalive'" % first)
        try:
            k_at = body.index(": keepalive")
            e_at = body.index("event: message")
            if k_at > e_at:
                problems.append("the keepalive came after the event: message")
        except ValueError:
            problems.append("missing ': keepalive' or 'event: message' in %r" % body[:6])
        if sse["ids"]:
            problems.append("SSE carries id: line(s) %r" % sse["ids"])
        bad, pairs = sse_messages(sse)
        problems += bad
        answers = [msg for _event, msg in pairs if is_response(msg)]
        if len(answers) != 1 or answers[0].get("id") != 31:
            problems.append("%d response(s) %r, expected one for id 31"
                            % (len(answers), [a.get("id") for a in answers]))
        elif tool_text(answers[0]) != "tf slept %s" % float(seconds):
            problems.append("text %r, expected %r"
                            % (tool_text(answers[0]), "tf slept %s" % float(seconds)))
        return problems, ["timing      : headers %.2fs, stream %.1fs (tf_sleep %gs, "
                          "keepalive %gs)" % (head_s, total_s, seconds, mod._HTTP_KEEPALIVE_S),
                          "lines       : %s" % " | ".join(body[:3])]

    http_session(suite, fixture_root, "j-keepalive", [
        ("SSE headers now, then keepalive", j31),
    ], detail=["proxy       : --http --port 0, default caps"])

    # J32 -- a request cancelled before its first step posts started + sentinel only.
    problems, lines = [], []
    if mod is None:
        problems.append(mod_why)
    else:
        q = queue.Queue()
        state = {}

        async def j32_body():
            server = mod.McpServer(mod.ProxyConfig(fixture_root, (), mod._MAX_FRAME))
            server.http = types.SimpleNamespace(max_inflight=mod._HTTP_INFLIGHT_CAP)
            session = mod.HttpSession("tf-sid")
            server._http_start(session, {"jsonrpc": "2.0", "id": 7, "method": "ping"},
                               mod._bounded_sink(q))
            state["registered"] = 7 in session.by_id
            server._cancel_request({"requestId": 7}, session.by_id)
            for _i in range(5):
                await asyncio.sleep(0)
            state["tasks_left"] = len(server._http_tasks)
            state["by_id_left"] = len(session.by_id)

        try:
            asyncio.run(asyncio.wait_for(j32_body(), IN_PROCESS_TIMEOUT_S))
        except Exception as exc:  # noqa: BLE001 -- any raise is the case's finding
            problems.append("case raised %s: %s" % (type(exc).__name__, exc))
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        names = ["_REQUEST_STARTED" if it is mod._REQUEST_STARTED else
                 "None" if it is None else type(it).__name__ for it in items]
        if names != ["_REQUEST_STARTED", "None"]:
            problems.append("queue %r, expected [_REQUEST_STARTED, None]" % names)
        if not state.get("registered"):
            problems.append("id 7 was not registered in session.by_id")
        if state.get("tasks_left") or state.get("by_id_left"):
            problems.append("left behind: %r task(s), %r by_id entr(y/ies)"
                            % (state.get("tasks_left"), state.get("by_id_left")))
        lines.append("queue       : %s" % ", ".join(names))
        lines.append("note        : the handler's started + sentinel -> 202 mapping is "
                     "not driven over the wire (cross-thread race; Assumption 9)")
    suite.record(GJ, "cancel before step: started+None", problems, detail=lines)

    # J33 -- SIGTERM with a call streaming and an idle keep-alive connection.
    j33_shutdown(suite, fixture_root, mod, mod_why)

    group_j_strict(suite, fixture_root, mod, mod_why)

    # J45 -- an ordered shutdown removes the ready file only while it is ours.
    j45_ready_file(suite, fixture_root, mod, mod_why)


# -- J34-J42: the round-1/2/3 security-review rows of group J -----------------

J_TRICKLE_STEP_S = 1.0       # J34: one header byte per this, far under the per-recv timeout
J_TRICKLE_SLACK_S = 3.0      # J34: the cut must land within _HTTP_HEADER_TIMEOUT_S + this
J_ORPHAN_TRIES = 3           # J38: initializes refused at the in-flight cap
J_DEEP_ID_BAND = 16          # J42: depths swept below the deepest id that still parses
J43_MARK = b"<script>TFMARK"  # J43: client text the stdlib would quote into its error page
J43_MARK_TOKEN = b"TFMARK"    # J43: must not occur in the answer, raw or HTML-escaped
J43_LINE_LIMIT = 65537       # J43: the stdlib's readline bound; one byte past it is 414/431
J43_MAX_HEADERS = 100        # J43: http.client's _MAXHEADERS; one more is 431


def j43_rows(host):
    """(label, request bytes, expected status, stdlib refusal?) of J43. Each
    request ends exactly where the stdlib stops reading, so no unread byte turns
    the server's close into a reset that could discard the answer."""
    pad_line = b"GET /" + J43_MARK
    pad_line += b"A" * (J43_LINE_LIMIT - len(pad_line))
    head = b"POST /mcp HTTP/1.1\r\nHost: " + host + b"\r\n"
    big_header = b"X-Tf: " + J43_MARK
    big_header += b"A" * (J43_LINE_LIMIT - len(big_header))
    many = b"".join(b"X-Tf-%d: %s\r\n" % (i, J43_MARK) for i in range(J43_MAX_HEADERS))
    return (
        ("bad version word", b"GET /mcp " + J43_MARK + b"\r\n", 400, True),
        ("four-word line", b"GET /mcp " + J43_MARK + b" HTTP/1.1\r\n", 400, True),
        ("HTTP/0.9 non-GET", J43_MARK + b" /mcp\r\n", 400, True),
        ("HTTP/2.0", b"GET /mcp HTTP/2.0\r\n", 505, True),
        ("request line too long", pad_line, 414, True),
        ("unknown method", b"TF" + J43_MARK + b" /mcp HTTP/1.1\r\nHost: " + host
         + b"\r\nConnection: close\r\n\r\n", 501, True),
        ("header line too long", head + big_header, 431, True),
        ("too many headers", head + many, 431, True),
        # The proxy's own refusal of a bare HTTP/0.9 GET: the stdlib writes no
        # head for HTTP/0.9, so the answer must still be sent with one.
        ("HTTP/0.9 GET, no Host", b"GET /mcp\r\n\r\n", 403, False),
    )


J44_INTERIM_WAIT_S = 2.0     # J44: how long the client waits for a 100 before it gives up
J44_HEAD_RX = re.compile(rb"(?:^|\r\n\r\n)HTTP/1\.[01] ([0-9]{3}) ")


def expect_exchange(client, pairs, length, body=None):
    """(statuses, raw bytes, closed, body sent?) of one POST that carries its
    head first and its body only after a 100 Continue (when *body* is given).
    A refusal row passes body None: the body is never sent, so a refusal must
    come on the head alone. *statuses* is every response head in order."""
    lines = ["POST %s HTTP/1.1" % client.path, "Host: %s:%d" % (client.host, client.port)]
    lines += ["%s: %s" % kv for kv in pairs]
    lines.append("Content-Length: %d" % length)
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    data, closed, sent = b"", False, False
    with socket.create_connection((client.host, client.port), timeout=J_RAW_TIMEOUT_S) as sock:
        sock.sendall(head)
        deadline = time.monotonic() + J44_INTERIM_WAIT_S
        while b"\r\n\r\n" not in data and time.monotonic() < deadline:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except ConnectionResetError:
                closed = True
                break
            if not chunk:
                closed = True
                break
            data += chunk
        if body is not None and not closed and data.startswith(b"HTTP/1.1 100 "):
            sock.sendall(body)
            sent = True
        sock.settimeout(J_RAW_TIMEOUT_S)
        while not closed:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            except ConnectionResetError:
                closed = True
                break
            if not chunk:
                closed = True
                break
            data += chunk
    return [int(m) for m in J44_HEAD_RX.findall(data)], data, closed, sent


def strict_pairs(client, sid=None, version=PROTOCOL_VERSION, extra=()):
    """The SDK's header pairs, plus Connection: close (the raw read then ends at
    the answer) and *extra* pairs appended as-is, so a name may repeat."""
    return (list(client.headers(sid, version).items()) + [("Connection", "close")]
            + list(extra))


def group_j_strict(suite, fixture_root, mod, mod_why):
    """J34-J42: the total header deadline (F8), deep nesting over HTTP (F16),
    duplicate and near-miss headers (F28), a malformed header line (F1), the
    session an initialize refused at the in-flight cap must not leave behind
    (G1), an id-less initialize that must open none (F7), and an id that is
    not a string or integer: an initialize that must open none, and a deep
    list id that must still be answered (R3-F1/F2)."""

    # J34 -- a header trickle is cut at the TOTAL deadline, not per recv.
    def j34(proxy, client):
        if mod is None:
            return [mod_why], []
        bound = mod._HTTP_HEADER_TIMEOUT_S
        problems, sent, closed = [], 0, False
        sock = socket.create_connection((client.host, client.port), timeout=J_RAW_TIMEOUT_S)
        t0 = time.monotonic()
        try:
            sock.sendall(J_PARTIAL_LINE)
            while time.monotonic() - t0 < bound + J_TRICKLE_SLACK_S:
                try:
                    sock.sendall(b"X")
                    sent += 1
                except OSError:
                    closed = True
                    break
                closed, _data = socket_closed_by_peer(sock, time.monotonic() + J_TRICKLE_STEP_S)
                if closed:
                    break
        finally:
            try:
                sock.close()
            except OSError:
                pass
        took = time.monotonic() - t0
        if not closed:
            problems.append("a header trickle (1 byte / %gs) still open after %.1fs "
                            "(total deadline %gs)" % (J_TRICKLE_STEP_S, took, bound))
        after = client.send(init_body())
        if after.status != 200 or not after.header("Mcp-Session-Id"):
            problems.append("initialize after the cut: status %d, expected 200" % after.status)
        return problems, ["trickle     : %d byte(s), closed %s after %.1fs (deadline %gs, "
                          "slack %gs)" % (sent, closed, took, bound, J_TRICKLE_SLACK_S),
                          "after       : initialize %d" % after.status]

    # J35 -- a deeply nested body is a 400 -32700, and the proxy keeps serving.
    def j35(proxy, client):
        reply = client.send(raw=b"[" * DEEP_NESTING)
        problems = [] if reply.status == 400 else ["status %d, expected 400" % reply.status]
        problems += expect_error(reply.json(), -32700, "deep nesting")
        after = client.send(init_body())
        if after.status != 200:
            problems.append("initialize after the deep body: status %d" % after.status)
        return problems, ["answers     : deep body %d, then initialize %d"
                          % (reply.status, after.status)]

    # J36 -- a repeated Mcp-Session-Id, MCP-Protocol-Version or Content-Type is refused.
    def j36(proxy, client):
        problems, sid = http_init(client)
        if problems:
            return problems, []
        body = json.dumps(rpc_body("ping", {}, 36)).encode("utf-8")
        other = "tf-other-" + secrets.token_urlsafe(24)
        rows = (
            ("control: one of each", [], 200),
            ("Mcp-Session-Id twice", [("mcp-session-id", other)], 400),
            ("MCP-Protocol-Version twice", [("mcp-protocol-version", PROTOCOL_VERSION)], 400),
            ("Content-Type twice", [("content-type", "application/json")], 415),
        )
        got = []
        for label, extra, want in rows:
            status, _headers, _closed, _data = raw_exchange(
                client, strict_pairs(client, sid, extra=extra), body)
            got.append("%s %r" % (label, status))
            if status != want:
                problems.append("%s: status %r, expected %d" % (label, status, want))
        return problems, ["answers     : %s" % "; ".join(got)]

    # J37 -- the media type is matched exactly: a prefix is not application/json.
    def j37(proxy, client):
        problems, got = [], []
        for ctype, want in (("application/jsonp", 415), ("application/json-seq", 415),
                            ("application/json; charset=utf-8", 200)):
            reply = client.send(init_body(), extra={"content-type": ctype})
            got.append("%s %d" % (ctype, reply.status))
            if reply.status != want:
                problems.append("%s: status %d, expected %d" % (ctype, reply.status, want))
        return problems, ["answers     : %s" % "; ".join(got)]

    # J40 -- a malformed header line is refused: http.client.parse_headers stops
    # at it and records a defect, so every later header (here Content-Length too)
    # would escape the duplicate and Transfer-Encoding checks.
    def j40(proxy, client):
        body = json.dumps(init_body()).encode("utf-8")
        rows = (
            ("control: well-formed", [], 200),
            ("'Transfer-Encoding : chunked'", [("Transfer-Encoding ", "chunked")], 400),
            ("'X y: z' then a second Content-Type",
             [("X y", "z"), ("content-type", "text/plain")], 400),
        )
        problems, got, measured = [], [], []
        for label, extra, want in rows:
            if extra:
                # The stdlib's own verdict on this shape, measured, not assumed.
                block = "".join("%s: %s\r\n" % kv for kv in extra) + "Content-Length: 0\r\n\r\n"
                defects = http.client.parse_headers(io.BytesIO(block.encode("latin-1"))).defects
                measured.append("%s %s" % (label, [type(d).__name__ for d in defects]))
                if not defects:
                    problems.append("%s: the stdlib records no header defect" % label)
            status, _headers, _closed, _data = raw_exchange(
                client, strict_pairs(client, extra=extra), body)
            got.append("%s %r" % (label, status))
            if status != want:
                problems.append("%s: status %r, expected %d" % (label, status, want))
        return problems, ["answers     : %s" % "; ".join(got),
                          "defects     : %s" % "; ".join(measured)]

    # J42 -- a deeply nested LIST id that json.loads still accepts is answered,
    # never a dropped connection: echoed into the reply it made json.dumps raise
    # RecursionError, and the fallback re-embedded it (R3-F1).  Where the
    # decoder's and the encoder's limits fall is interpreter-dependent, so the
    # deepest body that still parses is FOUND (bisection), then the band just
    # below it is swept; every answer must be a 400 -32600 with id null or a
    # 400 -32700.
    def j42(proxy, client):
        probes = {}

        def probe(depth):
            if depth not in probes:
                raw = ('{"jsonrpc": "2.0", "method": "ping", "id": %s%s}'
                       % ("[" * depth, "]" * depth)).encode("utf-8")
                try:
                    reply = client.send(raw=raw)
                    body = reply.json() or {}
                    probes[depth] = (reply.status, (body.get("error") or {}).get("code"),
                                     body.get("id"))
                except (OSError, http.client.HTTPException) as exc:
                    probes[depth] = (None, type(exc).__name__, None)
            return probes[depth]

        def parsed(depth):
            return probe(depth)[1] != -32700

        lo, hi = 1, DEEP_NESTING
        problems = []
        if not parsed(lo):
            return ["control: a depth-1 list id was a parse error %r" % (probes[lo],)], []
        if parsed(hi):
            problems.append("control: a depth-%d id parsed %r" % (hi, probes[hi]))
        else:
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if parsed(mid):
                    lo = mid
                else:
                    hi = mid
            for depth in range(max(1, lo - J_DEEP_ID_BAND), hi + 1):
                probe(depth)
        for depth in sorted(probes):
            status, code, rid = probes[depth]
            if status is None:
                problems.append("depth %d: no answer, the connection dropped (%s)" % (depth, code))
            elif status != 400 or code not in (-32600, -32700):
                problems.append("depth %d: status %r code %r, expected 400 -32600/-32700"
                                % (depth, status, code))
            elif rid is not None:
                problems.append("depth %d: the answer echoed the id (type %s)"
                                % (depth, type(rid).__name__))
        after = client.send(init_body())
        if after.status != 200:
            problems.append("initialize after the deep ids: status %d" % after.status)
        return problems[:6], ["deepest     : %d parses, %d does not (%d probe(s), band %d)"
                              % (lo, hi, len(probes), J_DEEP_ID_BAND),
                              "after       : initialize %d" % after.status]

    # J43 -- every refusal the stdlib answers before any proxy check (send_error)
    # is fixed and body-less (R-0069): the status line carries the fixed reason
    # phrase, Content-Length 0, nosniff, no-store, Connection: close, and no byte
    # of the request line, method or header reaches the answer.
    def j43(proxy, client):
        host = ("%s:%d" % (client.host, client.port)).encode("ascii")
        problems, got = [], []
        for label, request, want, stdlib in j43_rows(host):
            status, headers, closed, data = raw_request(client, request)
            got.append("%s %r" % (label, status))
            if status != want:
                problems.append("%s: status %r, expected %d" % (label, status, want))
                continue
            head, _sep, body = data.partition(b"\r\n\r\n")
            if body:
                problems.append("%s: a %d-byte body" % (label, len(body)))
            if headers.get("content-length") != "0":
                problems.append("%s: Content-Length %r, expected '0'"
                                % (label, headers.get("content-length")))
            if headers.get("connection", "").lower() != "close" or not closed:
                problems.append("%s: Connection %r, closed by the server %s"
                                % (label, headers.get("connection"), closed))
            if stdlib:
                if headers.get("x-content-type-options") != "nosniff":
                    problems.append("%s: X-Content-Type-Options %r, expected nosniff"
                                    % (label, headers.get("x-content-type-options")))
                if headers.get("cache-control") != "no-store":
                    problems.append("%s: Cache-Control %r, expected no-store"
                                    % (label, headers.get("cache-control")))
                reason = head.split(b"\r\n", 1)[0].split(b" ", 2)[2:]
                fixed = http.HTTPStatus(want).phrase.encode("ascii")
                if reason != [fixed]:
                    problems.append("%s: reason phrase %r, expected %r" % (label, reason, fixed))
            if J43_MARK_TOKEN in data:
                problems.append("%s: the request's marker is reflected in the answer" % label)
        after = client.send(init_body())
        if after.status != 200:
            problems.append("initialize after the refusals: status %d" % after.status)
        return problems, ["answers     : %s" % "; ".join(got),
                          "after       : initialize %d" % after.status]

    # J44 -- the 100 Continue is sent only after the bearer, Origin/Host, media
    # type and framing pass, just before the body is read (R-0076): a refused
    # request gets its final status with no interim 100; an accepted one gets
    # exactly one, and its body is sent only after it. An Expect value other
    # than 100-continue is ignored, as the router does: no 100, no 417.
    def j44(proxy, client):
        if mod is None:
            return [mod_why], []
        body = json.dumps(init_body()).encode("utf-8")

        def pairs(expect, token=True, extra=None):
            return (list(client.headers(None, PROTOCOL_VERSION, token=token, extra=extra).items())
                    + [("Connection", "close"), ("Expect", expect)])

        refusals = (
            ("wrong bearer", pairs("100-continue", token="tf-wrong-" + secrets.token_urlsafe(24)),
             len(body), 401),
            ("foreign Origin", pairs("100-continue", extra={"origin": J_EVIL_ORIGIN}), len(body), 403),
            ("wrong media type", pairs("100-continue", extra={"content-type": "text/plain"}),
             len(body), 415),
            ("Content-Length over the cap", pairs("100-continue"), mod._HTTP_BODY_LIMIT + 1, 413),
        )
        problems, got = [], []
        for label, hdrs, length, want in refusals:
            statuses, data, closed, _sent = expect_exchange(client, hdrs, length)
            got.append("%s %r" % (label, statuses))
            if statuses != [want]:
                problems.append("%s: response heads %r, expected [%d] (no interim 100)"
                                % (label, statuses, want))
            if not closed:
                problems.append("%s: the server did not close the connection" % label)
        for label, expect in (("100-continue", "100-continue"), ("100-Continue", "100-Continue")):
            statuses, data, closed, sent = expect_exchange(client, pairs(expect), len(body), body)
            got.append("accepted, Expect %s %r" % (label, statuses))
            if statuses != [100, 200] or not sent:
                problems.append("accepted, Expect %s: response heads %r (body sent after a 100: %s), "
                                "expected [100, 200]" % (label, statuses, sent))
        status, _headers, _closed, data = raw_exchange(client, pairs("tf-other"), body)
        others = [int(m) for m in J44_HEAD_RX.findall(data)]
        got.append("Expect tf-other %r" % others)
        if others != [200]:
            problems.append("Expect tf-other: response heads %r, expected [200]" % others)
        return problems, ["answers     : %s" % "; ".join(got)]

    http_session(suite, fixture_root, "j-strict", [
        ("header trickle cut at deadline", j34),
        ("deep nesting -> 400 -32700", j35),
        ("repeated headers refused", j36),
        ("media type matched exactly", j37),
        ("malformed header line refused", j40),
        ("deep list id answered", j42),
        ("stdlib refusals fixed, body-less", j43),
        ("100 Continue only after auth", j44),
    ], detail=["proxy       : --http --port 0, default caps"])

    # J38 -- an initialize refused at the in-flight cap leaves no session behind.
    def j38(proxy, client):
        state = proxy.sandbox
        problems, sid = http_init(client)                       # session 1 of 2
        if problems:
            return problems, []
        mark = event_mark(state)
        conn, resp = open_call(client, sid, call_body(381, "tf_sleep", {"seconds": 3}))
        hang_up(conn, resp)                                     # the call holds the one slot
        if resp.status != 200:
            return ["tf_sleep: status %d, expected 200" % resp.status], []
        busy = [client.send(init_body()).status for _i in range(J_ORPHAN_TRIES)]
        if busy != [503] * J_ORPHAN_TRIES:
            problems.append("initialize at the in-flight cap: %r, expected 503 x%d"
                            % (busy, J_ORPHAN_TRIES))
        if wait_new_event(state, mark, lambda e: e.get("method") == "tf_sleep/done",
                          J_DONE_BOUND_S) is None:
            return problems + ["the abandoned tf_sleep 3 never completed within %gs"
                               % J_DONE_BOUND_S], []
        # Session 2 of 2 is free only if no refused initialize kept a session.
        attempts, last = 0, None
        deadline = time.monotonic() + J_RETRY_BOUND_S
        while True:
            attempts += 1
            reply = client.send(init_body())
            last = reply.status
            if last == 200 and reply.header("Mcp-Session-Id"):
                break
            if time.monotonic() >= deadline:
                problems.append("initialize after the call ended: %r after %d attempt(s), "
                                "expected 200 (a refused initialize left a session)"
                                % (last, attempts))
                break
            time.sleep(0.1)
        control = client.send(init_body())
        if control.status != 503:
            problems.append("control: a third session got %d, expected 503 "
                            "(--max-sessions 2 not reached)" % control.status)
        return problems, ["refused     : %r" % busy,
                          "afterwards  : initialize %r after %d attempt(s); control %d"
                          % (last, attempts, control.status)]

    http_session(suite, fixture_root, "j-orphan", [
        ("refused initialize keeps no sid", j38),
    ], extra_argv=("--max-sessions", 2, "--max-inflight", 1),
        detail=["proxy       : --http --port 0 --max-sessions 2 --max-inflight 1"])

    # J39 -- an initialize must be a request: one whose id is absent or null is a
    # 400 -32600 and opens no session, so the one --max-sessions slot stays free.
    def j39(proxy, client):
        absent = init_body()
        del absent["id"]
        rows = (("id absent", absent), ("id null", dict(init_body(), id=None)))
        problems, got = [], []
        for label, body in rows:
            reply = client.send(body)
            got.append("%s %d" % (label, reply.status))
            if reply.status != 400:
                problems.append("%s: status %d, expected 400" % (label, reply.status))
            problems += expect_error(reply.json(), -32600, label)
            if reply.header("Mcp-Session-Id"):
                problems.append("%s: answered with an Mcp-Session-Id" % label)
        first = client.send(init_body())
        if first.status != 200 or not first.header("Mcp-Session-Id"):
            problems.append("initialize after the refusals: status %d, expected 200 "
                            "(an id-less initialize kept the one session slot)" % first.status)
        control = client.send(init_body())
        if control.status != 503:
            problems.append("control: a second session got %d, expected 503 "
                            "(--max-sessions 1 not reached)" % control.status)
        return problems, ["refused     : %s" % "; ".join(got),
                          "afterwards  : initialize %d; control %d"
                          % (first.status, control.status)]

    http_session(suite, fixture_root, "j-noid", [
        ("id-less initialize refused", j39),
    ], extra_argv=("--max-sessions", 1),
        detail=["proxy       : --http --port 0 --max-sessions 1"])

    # J41 -- an initialize whose id is neither a string nor an integer is a 400
    # -32600 with id null and opens no session (R3-F2): the one --max-sessions
    # slot stays free.
    def j41(proxy, client):
        rows = (("id {}", dict(init_body(), id={})), ("id [[1]]", dict(init_body(), id=[[1]])))
        problems, got = [], []
        for label, body in rows:
            reply = client.send(body)
            got.append("%s %d" % (label, reply.status))
            if reply.status != 400:
                problems.append("%s: status %d, expected 400" % (label, reply.status))
            problems += expect_error(reply.json(), -32600, label)
            if (reply.json() or {}).get("id", "absent") is not None:
                problems.append("%s: the answer's id is %r, expected null"
                                % (label, (reply.json() or {}).get("id", "absent")))
            if reply.header("Mcp-Session-Id"):
                problems.append("%s: answered with an Mcp-Session-Id" % label)
        first = client.send(init_body())
        if first.status != 200 or not first.header("Mcp-Session-Id"):
            problems.append("initialize after the refusals: status %d, expected 200 "
                            "(a bad-id initialize kept the one session slot)" % first.status)
        control = client.send(init_body())
        if control.status != 503:
            problems.append("control: a second session got %d, expected 503 "
                            "(--max-sessions 1 not reached)" % control.status)
        return problems, ["refused     : %s" % "; ".join(got),
                          "afterwards  : initialize %d; control %d"
                          % (first.status, control.status)]

    http_session(suite, fixture_root, "j-badid", [
        ("bad-id initialize refused", j41),
    ], extra_argv=("--max-sessions", 1),
        detail=["proxy       : --http --port 0 --max-sessions 1"])


def j33_shutdown(suite, fixture_root, mod, mod_why):
    """J33: SIGTERM ends the proxy within the module's own ladder bound while a
    tools/call streams and a keep-alive connection idles in its header read."""
    cid = "SIGTERM with live HTTP traffic"
    if mod is None:
        suite.record(GJ, cid, [mod_why])
        return
    bound = (mod._SHUTDOWN_GRACE_S + mod._HTTP_BRIDGE_TIMEOUT_S
             + 2 * mod._SHUTDOWN_GRACE_S + 2)
    sandbox = new_sandbox(fixture_root, "j-shutdown")
    children = one_stub(sandbox)
    cfgpath = write_config(sandbox, children)
    problems, lines = [], ["bound       : %gs = grace + bridge + 2 x grace + 2" % bound]
    proxy = None
    conns = []
    try:
        proxy = HttpProxy(sandbox, cfgpath, label="j-shutdown")
        if proxy.wait_ready() is None:
            problems.append("no ready file within %gs (rc=%r); stderr: %s"
                            % (READY_TIMEOUT_S, proxy.proc.poll(), stderr_tail(proxy)))
        else:
            client = proxy.client()
            got, sid = http_init(client)
            problems += got
            if not got:
                mark = event_mark(sandbox)
                conn, resp = open_call(client, sid, call_body(
                    331, "tf_sleep", {"seconds": J_SHUTDOWN_SLEEP_S}))
                conns.append((conn, resp))
                call_ev = wait_new_event(sandbox, mark,
                                         lambda e: e.get("method") == "tools/call")
                if resp.status != 200 or call_ev is None:
                    problems.append("tf_sleep: status %d, stub saw the call %s"
                                    % (resp.status, call_ev is not None))
                idle = client.connect()
                idle.request("POST", client.path,
                             body=json.dumps(rpc_body("ping", {}, 332)).encode("utf-8"),
                             headers=client.headers(sid, PROTOCOL_VERSION))
                ping = idle.getresponse()
                ping_body = ping.read()
                conns.append((idle, ping))
                if ping.status != 200 or b'"result"' not in ping_body:
                    problems.append("idle ping: status %d %r" % (ping.status, ping_body[:80]))
                if ping.will_close:
                    problems.append("idle ping: the server closed the keep-alive "
                                    "connection (the case needs it idle)")
                t0 = time.monotonic()
                proxy.proc.send_signal(signal.SIGTERM)
                try:
                    rc = proxy.proc.wait(timeout=bound)
                except subprocess.TimeoutExpired:
                    rc = None
                took = time.monotonic() - t0
                if rc != 0:
                    problems.append("exit code %r within %gs, expected 0" % (rc, bound))
                problems += sse_unanswered(resp, "in-flight stream")
                if call_ev is not None:
                    child_id = call_ev.get("id")
                    if wait_new_event(sandbox, mark, lambda e: e.get("method")
                                      == "notifications/cancelled"
                                      and e.get("cancel_id") == child_id) is None:
                        problems.append("the stub never saw a cancel for tf_sleep id %r"
                                        % child_id)
                pids = read_pids(sandbox)
                alive = [p for p in pids
                         if not pid_dead(p, max(0.0, t0 + bound - time.monotonic()))]
                if not pids:
                    problems.append("no stub pid recorded")
                if alive:
                    problems.append("pid(s) %r alive past the bound" % alive)
                lines.append("exit        : rc %r after %.1fs; stub pids %d, alive %d"
                             % (rc, took, len(pids), len(alive)))
    except Exception as exc:  # noqa: BLE001 -- any raise is the case's finding
        problems.append("case raised %s: %s" % (type(exc).__name__, exc))
    finally:
        for conn, resp in conns:
            hang_up(conn, resp)
        if proxy is not None:
            proxy.close()
            if proxy.token in proxy.stderr_text():
                problems.append("the bearer token occurs on the proxy's stderr")
        problems += reap_stubs(sandbox, children)
    suite.record(GJ, cid, problems, detail=lines)


J45_FOREIGN_PID = 1          # J45: a pid that is never the proxy's own


def j45_ready_file(suite, fixture_root, mod, mod_why):
    """J45: SIGTERM removes the ready file while it holds the proxy's own pid
    (R-0075), leaves one another writer rewrote with a foreign pid, and never
    follows a symlink: the ready path replaced by a link to a sentinel that
    holds the proxy's OWN pid must leave both the link and the sentinel, so
    only O_NOFOLLOW (not the pid check) can be what protects them."""
    cid = "ready file removed, own pid only"
    if mod is None:
        suite.record(GJ, cid, [mod_why])
        return
    problems, lines = [], []
    for mode in ("own", "foreign pid", "symlink"):
        sandbox = new_sandbox(fixture_root, "j-ready")
        children = one_stub(sandbox)
        cfgpath = write_config(sandbox, children)
        proxy = None
        try:
            proxy = HttpProxy(sandbox, cfgpath, label="j-ready")
            if proxy.wait_ready() is None:
                problems.append("%s: no ready file within %gs (rc=%r); stderr: %s"
                                % (mode, READY_TIMEOUT_S, proxy.proc.poll(), stderr_tail(proxy)))
                continue
            path = proxy.ready_path
            planted = None
            sentinel = os.path.join(sandbox, "sentinel.json")
            if mode == "foreign pid":
                planted = json.dumps({"port": proxy.port, "pid": J45_FOREIGN_PID}) + "\n"
                write_file(path, planted)
            elif mode == "symlink":
                planted = json.dumps({"port": proxy.port, "pid": proxy.proc.pid}) + "\n"
                write_file(sentinel, planted)
                os.remove(path)
                os.symlink(sentinel, path)
            rc = proxy.close()
            if rc != 0:
                problems.append("%s: exit code %r after SIGTERM, expected 0" % (mode, rc))
            if mode == "own":
                left = os.path.lexists(path)
                if left:
                    problems.append("own: the ready file survived an ordered shutdown")
                lines.append("own         : rc %r, ready file left %s" % (rc, left))
            elif mode == "foreign pid":
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        now = fh.read()
                except OSError as exc:
                    now = None
                    problems.append("foreign pid: the rewritten ready file is gone (%s)"
                                    % type(exc).__name__)
                if now is not None and now != planted:
                    problems.append("foreign pid: the rewritten ready file was changed")
                lines.append("foreign pid : rc %r, file kept %s" % (rc, now == planted))
            else:
                link_kept = os.path.islink(path) and os.readlink(path) == sentinel
                try:
                    with open(sentinel, "r", encoding="utf-8") as fh:
                        target_kept = fh.read() == planted
                except OSError:
                    target_kept = False
                if not link_kept:
                    problems.append("symlink: the symlink at the ready path was removed or changed")
                if not target_kept:
                    problems.append("symlink: the symlink's target was removed or changed")
                lines.append("symlink     : rc %r, link kept %s, target kept %s"
                             % (rc, link_kept, target_kept))
        except Exception as exc:  # noqa: BLE001 -- any raise is the case's finding
            problems.append("%s: case raised %s: %s" % (mode, type(exc).__name__, exc))
        finally:
            if proxy is not None:
                proxy.close()
                if proxy.token in proxy.stderr_text():
                    problems.append("%s: the bearer token occurs on the proxy's stderr" % mode)
            problems += reap_stubs(sandbox, children)
    lines.append("declared    : a replace between the read and the unlink is not seen; "
                 "SIGKILL leaves the file")
    suite.record(GJ, cid, problems, detail=lines)


# ---------------------------------------------------------------------------
# K. static AST over Scripts/mcp-proxy.py  (4 cases)
# ---------------------------------------------------------------------------

# Every ChildClient method that handles child-side wire data, read or written.
K3_SITES = ("_reader_loop", "_on_response", "_reply_to_child", "_die",
            "_send_line", "notify_child", "rpc")
K3_SEED = ("line", "msg", "obj", "data", "payload")   # names seeded as payload (WL.LEAK)
K4_ASYNCIO = ("timeout", "TaskGroup", "Runner")
K4_NAMES = ("ExceptionGroup", "BaseExceptionGroup")


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _kw(call, name):
    """The keyword node *name* of *call*, else None."""
    for kw in call.keywords:
        if kw.arg == name:
            return kw
    return None


def k1_shell(tree):
    """[(lineno, what)]: a shell= keyword, os.system / os.popen."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _kw(node, "shell") is not None:
            found.append((node.lineno, "shell= keyword on %s" % WL._src(node.func)))
        elif (isinstance(node, ast.Attribute) and node.attr in ("system", "popen")
              and _is_name(node.value, "os")):
            found.append((node.lineno, "os.%s" % node.attr))
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in ("system", "popen"):
                    found.append((node.lineno, "from os import %s" % alias.name))
    return sorted(found)


def k2_spawns(tree):
    """(calls, [(lineno, what)]): every create_subprocess_exec and what it lacks."""
    calls, found = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.attr if isinstance(func, ast.Attribute) else \
            func.id if isinstance(func, ast.Name) else None
        if fname != "create_subprocess_exec":
            continue
        calls += 1
        for key in ("stdin", "limit"):
            if _kw(node, key) is None:
                found.append((node.lineno, "create_subprocess_exec without %s=" % key))
        sns = _kw(node, "start_new_session")
        if sns is None or not (isinstance(sns.value, ast.Constant)
                               and sns.value.value is True):
            found.append((node.lineno,
                          "create_subprocess_exec without start_new_session=True"))
    return calls, sorted(found)


def k4_api_311(tree):
    """[(lineno, what)]: 3.11+ asyncio / exception-group APIs api_310 does not list."""
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in K4_ASYNCIO
                and _is_name(node.value, "asyncio")):
            found.append((node.lineno, "asyncio.%s (3.11)" % node.attr))
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for alias in node.names:
                if alias.name in K4_ASYNCIO:
                    found.append((node.lineno,
                                  "from asyncio import %s (3.11)" % alias.name))
        elif isinstance(node, ast.Name) and node.id in K4_NAMES:
            found.append((node.lineno, "%s (3.11)" % node.id))
        elif hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):
            found.append((node.lineno, "except* (3.11)"))
    return sorted(found)


def _child_client_methods(tree):
    """{name: FunctionDef} of class ChildClient's methods, or None if no such class."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChildClient":
            return {fn.name: fn for fn in node.body
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return None


def _findings(found):
    return ["%d: %s" % (lineno, what) for lineno, what in found]


def group_k(suite):
    """K. spawn shape, structure-only logging, 3.9 API floor.

    Static, against the live file.  K3 runs `test_wire_log`'s own taint
    machinery (imported, not copied), so the child-side wire is judged by the
    same rule as `McpServer.run` / `_write`.  None of the four cases has a
    plant-and-detect control (declared): K3's rule is controlled in
    `test_wire_log` group D, K1/K2/K4 are fixed refusal lists.
    """
    rel = os.path.relpath(SERVER, H.REPO_ROOT)
    try:
        with open(SERVER, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=SERVER)
    except (OSError, SyntaxError) as exc:
        why = ["cannot parse %s: %s: %s" % (rel, type(exc).__name__, exc)]
        for cid in ("no shell, os.system, os.popen", "spawn: stdin, session, limit",
                    "child-side logs structure only", "no 3.11+ asyncio API"):
            suite.record(GK, cid, why)
        return

    # K1 -- no shell anywhere.
    found = k1_shell(tree)
    suite.record(GK, "no shell, os.system, os.popen", _findings(found),
                 detail=["scope       : every Call / Attribute / ImportFrom in %s" % rel])

    # K2 -- every child spawn carries stdin=, start_new_session=True and limit=.
    calls, found = k2_spawns(tree)
    problems = _findings(found)
    if calls == 0:
        problems.append("no create_subprocess_exec call found: the check would "
                        "pass vacuously")
    suite.record(GK, "spawn: stdin, session, limit", problems,
                 detail=["spawns      : %d create_subprocess_exec call(s)" % calls])

    # K3 -- every wire-handling ChildClient method (K3_SITES) logs structure only.
    methods = _child_client_methods(tree)
    problems, lines = [], ["seed        : %s = LEAK" % ", ".join(K3_SEED)]
    if methods is None:
        problems.append("no class ChildClient in %s" % rel)
    else:
        for name in K3_SITES:
            fn = methods.get(name)
            if fn is None:
                problems.append("ChildClient.%s not found" % name)
                continue
            env = {seed: WL.LEAK for seed in K3_SEED}
            site = WL.Site("ChildClient.%s" % name)
            WL._propagate(env, fn)
            WL._collect(site, fn, env)
            if WL.PAYLOAD in site.problems:
                problems += [d for d in site.detail if d.startswith(WL.PAYLOAD)]
            lines.append("%-14s: %d wire log(s), shape %s"
                         % (name, len(site.logs),
                            "silent" if site.shape is None else "/".join(site.shape)))
    suite.record(GK, "child-side logs structure only", problems, detail=lines)

    # K4 -- no 3.11+ asyncio / exception-group API the fixed api_310 list misses.
    found = k4_api_311(tree)
    suite.record(GK, "no 3.11+ asyncio API", _findings(found),
                 detail=["refused     : asyncio.%s, %s, except*%s"
                         % ("/".join(K4_ASYNCIO), "/".join(K4_NAMES),
                            "" if hasattr(ast, "TryStar")
                            else " (not checkable on this interpreter)"),
                         "note        : a fixed refusal list, no plant-and-detect "
                         "control (declared)"])


# ---------------------------------------------------------------------------
# L. hygiene  (4 cases)
# ---------------------------------------------------------------------------

def group_l(suite, fixture_root, pyc_before, tree_before):
    """L. sandbox discipline, no bytecode, no new repo paths, log-line hygiene."""
    stray = [p for p in WRITES
             if not os.path.abspath(p).startswith(
                 os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GL, "every write lands under .claude/tmp",
                 [] if not stray
                 else ["%d write(s) outside the sandbox: %s"
                       % (len(stray), ", ".join(stray))],
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d" % len(WRITES)])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GL, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after)),
                         "note        : the proxy and every stub run with -B "
                         "and PYTHONDONTWRITEBYTECODE=1"])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GL, "no new repo paths",
                 [] if not added else ["%d new path(s): %s" % (len(added), added[:5])],
                 detail=["note        : the scratch area is excluded by "
                         "_harness.repo_tree, so the fixtures are invisible here "
                         "by construction, not by luck"])

    # L4 -- _log_value marks EVERY cut (R3-F5, CWE-117): an uncut value of
    # non-printables whose repr() overruns the 4 x width cap ends in "...",
    # never sliced mid-escape without a marker; a short printable str is its
    # repr() exactly.
    problems, lines = [], []
    try:
        mod = H.load_module_from_path("ph_proxy_l4", SERVER)
        width = mod._LOG_VALUE_WIDTH
        wide = mod._log_value("\U0010ffff" * width)
        if not wide.endswith("..."):
            problems.append("%d x U+10FFFF: no '...' marker on a capped repr (%d chars)"
                            % (width, len(wide)))
        if len(wide) > 4 * width + 3:
            problems.append("%d x U+10FFFF: %d chars, cap %d" % (width, len(wide), 4 * width + 3))
        short = mod._log_value("tools/call")
        if short != repr("tools/call"):
            problems.append("short str logged as %r, expected %r" % (short, repr("tools/call")))
        lines = ["capped      : %d x U+10FFFF -> %d chars, ends %r" % (width, len(wide), wide[-3:]),
                 "short       : %s" % short]
    except Exception as exc:  # noqa: BLE001 -- an import or call failure is the finding
        problems.append("raised %s: %s" % (type(exc).__name__, exc))
    suite.record(GL, "_log_value marks every cut", problems, detail=lines)


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="mcp-proxy relays, it never composes: listing, "
                          "routing, cancel, progress, restart, framing, "
                          "timeout and shutdown against an adversarial stub",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    WRITES.append(fixture_root)
    try:
        group_a(suite, fixture_root)
        group_b(suite, fixture_root)
        group_c(suite, fixture_root)
        group_d(suite, fixture_root)
        group_e(suite, fixture_root)
        group_f(suite, fixture_root)
        group_g(suite, fixture_root)
        group_h(suite, fixture_root)
        group_i(suite, fixture_root)
        group_j(suite, fixture_root)
        group_k(suite)
        group_l(suite, fixture_root, pyc_before, tree_before)
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
