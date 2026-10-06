#!/usr/bin/env python3
"""`Scripts/llm-router.py` routes by model and translates at the edge (A-J).

WHAT IS GATED
-------------
`Scripts/llm-router.py` is a loopback HTTP front that speaks the Anthropic
Messages API to Claude Code and forwards each request, chosen by its `model`,
to one configured backend: an Anthropic-compatible passthrough, a llama.cpp
server, or Mistral's chat-completions API.  Everything the router promises is
observable at its two edges: what the caller receives (an Anthropic-shaped
JSON answer or event stream, or an Anthropic-shaped refusal), and what the
backend receives (one request, recorded by a scripted peer).  This suite
drives the real router as a subprocess against scripted loopback peers, plus
an in-process half for the config loader, the pure translators and a static
pass over the router source.

SECRETS ARE SENTINELS
---------------------
The router token and every backend key are `TF_*` sentinels minted per run.
Group I sweeps the router's stderr, its --log-file, every response body and
every request a peer recorded for the wrong backend; a sentinel found there
is a leak.

SANDBOX DISCIPLINE -- all fixtures (config files written mode 0600, the CA
copy, the router's stderr and log file) live under
`.claude/tmp/test_llm_router/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  Removed in
a `finally` unless --keep.  Every live router is closed SIGTERM -> SIGKILL in a
`finally`.

The case count is TYPED in run.py's SUITES table: this is a fixed case table,
so a count that moves is the alarm.  The run is authoritative.

Usage:
  python3 tests/test_llm_router.py
  python3 tests/test_llm_router.py --brief
  python3 tests/test_llm_router.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  A  config               -- load_config refusals and defaults, in-process and CLI
  B  front                -- bind, auth, Host/Origin, framing refusals, caps, GET /v1/models
  C  outbound             -- address policy, TLS verification, upstream refusals
  D  passthrough          -- an Anthropic-compatible backend, relayed
  E  llamacpp             -- request and event quirks against a llama.cpp peer
  F  mistral request      -- Anthropic -> chat-completions translation
  G  mistral stream       -- chat-completions SSE -> Anthropic SSE translation
  H  timeouts/disconnect  -- idle and total deadlines, a client that hangs up
  I  secret leak          -- no sentinel in stderr, the log, a body or a wrong peer
  J  static and hygiene   -- AST over the router source, writes, bytecode, tree
"""

import ast
import base64
import calendar
import hashlib
import http.client
import ipaddress
import json
import os
import pathlib
import platform
import queue
import re
import secrets
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.parse

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "llm_router"

SERVER = H.repo_path("Scripts", "llm-router.py")
FIXTURES = H.repo_path("tests", "files", "llm_router")
TLS = H.repo_path("tests", "files", "tls")

GA = "A. config"
GB = "B. front"
GC = "C. outbound"
GD = "D. passthrough"
GE = "E. llamacpp"
GF = "F. mistral request"
GG = "G. mistral stream"
GH = "H. timeouts and disconnect"
GI = "I. secret leak"
GJ = "J. static and hygiene"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_llm_router")
WRITES = []
LOG_PATHS = []               # every router stderr and --log-file path, swept by I1
RESPONSE_BODIES = []         # (label, bytes) of every response body a client received, swept by I2

MESSAGES_PATH = "/v1/messages"

# Generous ceilings: a timing assertion is a FAIL only past these.
READY_TIMEOUT_S = 10.0       # --ready-file must appear within this
HTTP_TIMEOUT_S = 15.0        # one HTTP request/response round trip
RAW_TIMEOUT_S = 5.0          # the raw-socket read waits this long for the server's close
CLOSE_TIMEOUT_S = 15.0       # SIGTERM -> router exited
KILL_TIMEOUT_S = 5.0         # after SIGKILL
REFUSAL_TIMEOUT_S = 10.0     # a refused start (exit 2) must have exited within this

# The scripted upstream peer.
PEER_SESSION_TIMEOUT_S = 15.0   # one blocking socket op on a peer connection
PEER_UNWRAP_TIMEOUT_S = 1.0     # waiting for the client's side of a close_notify
PEER_JOIN_TIMEOUT_S = 5.0       # close(): accept and connection threads
PEER_POLL_S = 0.05              # stall/gap granularity for noticing a client close
PEER_HEAD_LIMIT = 1 << 20       # one request head
PEER_BODY_LIMIT = 80 << 20      # one request body (above the router's 32 MiB default)

# Loopback-peer backend defaults (KD-18); a case overrides any of them.
LOOPBACK_DEFAULTS = (("allow_private", True), ("allow_loopback", True), ("idle_timeout", 3))

# Sentinels: minted once per run; none may ever leave its intended peer.
# TF_KEY_MISTRAL carries one reserved character ("+", printable ASCII, so the
# api_key rule admits it) so that its URL-encoded form differs from the value
# and I7's URL-encoded echo is a form of its own, not a second copy of the value.
TF_ROUTER_TOKEN = "tf-router-token-" + secrets.token_hex(16)
TF_KEY_MISTRAL = "tfkey-mistral-SENTINEL+" + secrets.token_hex(16)
TF_KEY_PASS = "tfkey-pass-SENTINEL-" + secrets.token_hex(16)
TF_CLIENT_XKEY = "tfkey-client-SENTINEL-" + secrets.token_hex(16)
SENTINELS = (TF_ROUTER_TOKEN, TF_KEY_MISTRAL, TF_KEY_PASS, TF_CLIENT_XKEY)


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


def is_loopback_url(url):
    """True iff *url*'s host is localhost or a loopback IP literal."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def write_config(sandbox, cfg, mode=0o600, filename="config.json"):
    """Write *cfg* as JSON to sandbox/config.json; return its path.

    Every backend whose `base_url` points at a loopback peer gets
    `allow_private: true`, `allow_loopback: true` and `idle_timeout: 3` unless
    the case set the key itself.  *cfg* is not mutated.
    """
    out = dict(cfg)
    backends = cfg.get("backends")
    if isinstance(backends, dict):
        filled = {}
        for name, entry in backends.items():
            if isinstance(entry, dict) and isinstance(entry.get("base_url"), str) \
                    and is_loopback_url(entry["base_url"]):
                entry = dict(entry)
                for key, value in LOOPBACK_DEFAULTS:
                    entry.setdefault(key, value)
            filled[name] = entry
        out["backends"] = filled
    return write_file(os.path.join(sandbox, filename), json.dumps(out, indent=2), mode)


def sandbox_ca_file(sandbox):
    """Copy tests/files/tls/test-ca.pem into *sandbox* at 0600; its absolute path."""
    with open(os.path.join(TLS, "test-ca.pem"), "r", encoding="utf-8") as fh:
        pem = fh.read()
    return os.path.abspath(write_file(os.path.join(sandbox, "test-ca.pem"), pem))


# ---------------------------------------------------------------------------
# The live router and its clients
# ---------------------------------------------------------------------------

def router_argv(cfgpath, ready_path, *extra):
    return [sys.executable, "-B", SERVER, "--config", cfgpath, "--port", "0",
            "--ready-file", ready_path] + [str(a) for a in extra]


class RouterProc:
    """One live `llm-router.py` with a ready file and its stderr in a file."""

    def __init__(self, sandbox, cfgpath, extra_argv=(), label="router"):
        self.sandbox = sandbox
        self.token = TF_ROUTER_TOKEN
        self.ready_path = os.path.join(sandbox, label + ".ready.json")
        WRITES.append(self.ready_path)
        self.stderr_path = os.path.join(sandbox, label + ".stderr")
        WRITES.append(self.stderr_path)
        LOG_PATHS.append(self.stderr_path)
        extra = [str(a) for a in extra_argv]
        for i, arg in enumerate(extra[:-1]):
            if arg == "--log-file":
                LOG_PATHS.append(extra[i + 1])
                WRITES.append(extra[i + 1])
        self._err = open(self.stderr_path, "wb")
        self.port = None
        self._closed = False
        argv = router_argv(cfgpath, self.ready_path, *extra)
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=self._err,
                                         cwd=sandbox, env=H.child_env())
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

    def client(self, token=None):
        return RouterClient(self.port, self.token if token is None else token)

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


class RouterClient:
    """Claude Code's request shape on http.client: bearer token, JSON body."""

    def __init__(self, port, token, path=MESSAGES_PATH):
        self.host = "127.0.0.1"
        self.port = port
        self.token = token
        self.path = path

    def connect(self, timeout=HTTP_TIMEOUT_S):
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def headers(self, extra=None):
        """Host, Authorization and Content-Type; *extra* adds, replaces or (None) drops."""
        hdrs = {"Host": "%s:%d" % (self.host, self.port),
                "Content-Type": "application/json"}
        if self.token:
            hdrs["Authorization"] = "Bearer " + self.token
        for key, value in (extra or {}).items():
            match = [k for k in hdrs if k.lower() == key.lower()]
            for k in match:
                del hdrs[k]
            if value is not None:
                hdrs[key] = value
        return hdrs

    def post(self, path=None, obj=None, headers=None, raw_body=None, stream=False,
             timeout=HTTP_TIMEOUT_S):
        """One POST.  (status, headers, body) with the connection closed, or with
        *stream* the open (connection, response) for `read_sse`; the caller
        closes the connection."""
        body = raw_body if raw_body is not None else \
            (json.dumps(obj).encode("utf-8") if obj is not None else b"")
        conn = self.connect(timeout)
        try:
            conn.putrequest("POST", path or self.path, skip_host=True,
                            skip_accept_encoding=True)
            hdrs = self.headers(headers)
            for key, value in hdrs.items():
                conn.putheader(key, value)
            if not any(k.lower() == "content-length" for k in hdrs):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            if stream:
                return conn, resp
            data = resp.read()
            RESPONSE_BODIES.append(("POST %s" % (path or self.path), data))
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        except BaseException:
            conn.close()
            raise
        finally:
            if not stream:
                conn.close()


def raw_exchange(client, pairs, body, path=None, timeout=RAW_TIMEOUT_S):
    """(status, headers, closed, raw bytes) of one POST on a raw socket whose
    header lines are exactly *pairs* (a list, so a name may repeat), plus Host
    and Content-Length."""
    lines = ["POST %s HTTP/1.1" % (path or client.path),
             "Host: %s:%d" % (client.host, client.port)]
    lines += ["%s: %s" % kv for kv in pairs]
    lines.append("Content-Length: %d" % len(body))
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
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
    RESPONSE_BODIES.append(("raw POST %s" % (path or client.path), data))
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
    RESPONSE_BODIES.append(("event stream", "\n".join(out["lines"]).encode("utf-8")))
    return out


# ---------------------------------------------------------------------------
# The scripted upstream peer
# ---------------------------------------------------------------------------

class _PeerClientGone(Exception):
    """The client closed its side while the script was still running."""


class _PeerDrop(Exception):
    """close_after's byte budget is spent: drop the connection, no close_notify."""


class _PeerStop(Exception):
    """ScriptedPeer.close() was called while a script was still running."""


def _peer_line_bytes(line):
    """One SSE line as bytes, newline-terminated (str is UTF-8 encoded)."""
    data = line if isinstance(line, bytes) else str(line).encode("utf-8")
    return data + b"\n"


class ScriptedPeer:
    """A loopback upstream that records each request and runs a script (adapted
    from RawPeer, tests/test_mcp_chrome.py).

    Each accepted connection gets its own daemon thread, which (with *tls*,
    after a server-side handshake presenting the committed localhost leaf from
    tests/files/tls -- verify it with test-ca.pem and the server name
    `localhost` or `127.0.0.1`) reads ONE request: the head up to the blank line,
    then exactly Content-Length body bytes when there is exactly one
    Content-Length header (otherwise whatever arrived with the head).  It
    appends {"line", "headers", "body"} to `self.requests` -- headers as a list
    of (name, value) pairs in arrival order, case preserved, so a repeated name
    stays visible -- and then runs the script.

    *script* is a sequence of steps, each a tuple `(verb, *args)`, run for every
    connection; or a callable `script(index, request) -> steps` for a peer whose
    connections differ (index counts from 0).  None (or no steps) answers
    nothing: the request is recorded and the connection closed.  Verbs:

      ("respond_json", status, obj[, headers])   obj: JSON-encoded; bytes/str sent raw.
                                                 headers: (name, value) pairs; a name
                                                 that matches a default replaces it
      ("respond_sse", lines[, gaps[, headers]])  text/event-stream, Connection: close,
                                                 close-delimited; gaps[i] seconds are
                                                 slept before lines[i]; headers as for
                                                 respond_json
      ("respond_chunked_sse", lines[, gaps[, headers]])
                                                 the same, Transfer-Encoding: chunked,
                                                 one chunk per line, then the last chunk
                                                 (C8 replaces Connection: close with
                                                 keep-alive, so http.client keeps
                                                 conn.sock and only a close() clears it)
      ("stall", seconds)                         send nothing for *seconds*
      ("close_after", n_bytes)                   arm a budget: the verbs that follow send
                                                 only their first n_bytes (head included),
                                                 then TCP is dropped -- over TLS without a
                                                 close_notify, the D12 ragged EOF
      ("redirect", location[, status])           a 307 (or *status*) with Location, no body

    A normal end of the script closes the connection; over TLS it first sends a
    close_notify, so a close-delimited stream ends cleanly.  `closed_event` is
    set when the peer sees the CLIENT close its side (an EOF while reading the
    request, while stalled or between SSE lines, or a failed write) -- H3, H7
    and H9 wait on it.  Every exception is recorded in `self.errors`, never
    printed.
    """

    # The default head pairs of respond_sse (sse_wire_len sizes D12's close_after budget from them).
    SSE_HEAD = (("Content-Type", "text/event-stream"), ("Cache-Control", "no-cache"),
                ("Connection", "close"))

    def __init__(self, script=None, tls=False):
        self.script = script
        self.tls = bool(tls)
        self._ctx = None
        if self.tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(os.path.join(TLS, "localhost-cert.pem"),
                                os.path.join(TLS, "localhost-key.pem"))
            self._ctx = ctx
        self.requests = []
        self.errors = []
        self.conns = 0
        self.closed_event = threading.Event()
        self._lock = threading.Lock()
        self._stopping = False
        self._threads = []
        self.lsock = socket.socket()
        try:
            self.lsock.bind(("127.0.0.1", 0))
            self.lsock.listen(16)
            self.lsock.settimeout(0.25)
            self.port = self.lsock.getsockname()[1]
        except BaseException:
            self.lsock.close()
            raise
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def url(self, host="127.0.0.1"):
        """The peer's base URL (http or https) for a backend's `base_url`."""
        return "%s://%s:%d" % ("https" if self.tls else "http", host, self.port)

    def close(self):
        """Stop accepting, abort running scripts, join the threads; idempotent."""
        self._stopping = True
        self.thread.join(PEER_JOIN_TIMEOUT_S)
        try:
            self.lsock.close()
        except OSError:
            pass
        deadline = time.monotonic() + PEER_JOIN_TIMEOUT_S
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _error(self, exc):
        with self._lock:
            self.errors.append("%s(%r)" % (type(exc).__name__, str(exc)[:120]))

    def _accept(self):
        while not self._stopping:
            try:
                sock, _addr = self.lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                index = self.conns
                self.conns += 1
            thread = threading.Thread(target=self._serve, args=(sock, index), daemon=True)
            with self._lock:
                self._threads.append(thread)
            thread.start()

    def _serve(self, sock, index):
        conn = sock
        clean = False
        try:
            sock.settimeout(PEER_SESSION_TIMEOUT_S)
            if self.tls:
                conn = self._ctx.wrap_socket(sock, server_side=True)
            request = self._read_request(conn)
            if request is None:
                return
            with self._lock:
                self.requests.append(request)
            steps = self.script(index, request) if callable(self.script) else self.script
            state = {"budget": None}
            for step in steps or ():
                self._run_step(conn, state, step)
            clean = True
        except _PeerClientGone:
            self.closed_event.set()
        except (_PeerDrop, _PeerStop):
            pass
        except Exception as exc:  # a handshake refusal or a hang-up lands here; recorded, never printed
            self._error(exc)
        finally:
            if clean and conn is not sock:
                try:
                    conn.settimeout(PEER_UNWRAP_TIMEOUT_S)
                    conn.unwrap()
                except (OSError, ValueError):
                    pass
            for s in (conn, sock):
                try:
                    s.close()
                except OSError:
                    pass

    def _read_request(self, conn):
        """{"line", "headers", "body"} of one request; None (closed_event set) on an early EOF."""
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > PEER_HEAD_LIMIT:
                raise ValueError("request head over %d bytes" % PEER_HEAD_LIMIT)
            chunk = conn.recv(65536)
            if not chunk:
                self.closed_event.set()
                return None
            buf += chunk
        head, _sep, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = []
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers.append((name, value.strip()))
        lengths = [v for k, v in headers if k.lower() == "content-length"]
        if len(lengths) == 1 and lengths[0].isdigit():
            want = int(lengths[0])
            if want > PEER_BODY_LIMIT:
                raise ValueError("request body over %d bytes" % PEER_BODY_LIMIT)
            while len(body) < want:
                chunk = conn.recv(min(65536, want - len(body)))
                if not chunk:
                    self.closed_event.set()
                    raise ValueError("EOF after %d of %d body bytes" % (len(body), want))
                body += chunk
            body = body[:want]
        return {"line": lines[0], "headers": headers, "body": body}

    # -- the verbs ----------------------------------------------------------

    def _run_step(self, conn, state, step):
        verb, args = step[0], tuple(step[1:])
        if verb == "respond_json":
            self._respond_json(conn, state, *args)
        elif verb == "respond_sse":
            self._respond_sse(conn, state, *args)
        elif verb == "respond_chunked_sse":
            self._respond_sse(conn, state, *args, chunked=True)
        elif verb == "stall":
            self._wait(conn, args[0])
        elif verb == "close_after":
            state["budget"] = int(args[0])
        elif verb == "redirect":
            self._redirect(conn, state, *args)
        else:
            raise ValueError("unknown ScriptedPeer verb %r" % (verb,))

    def _respond_json(self, conn, state, status, obj, headers=()):
        if isinstance(obj, bytes):
            body = obj
        elif isinstance(obj, str):
            body = obj.encode("utf-8")
        else:
            body = json.dumps(obj).encode("utf-8")
        pairs = [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                 ("Connection", "close")]
        self._send(conn, state, self._head(status, pairs, headers) + body)

    def _respond_sse(self, conn, state, lines, gaps=(), headers=(), chunked=False):
        pairs = list(self.SSE_HEAD)
        if chunked:
            pairs.append(("Transfer-Encoding", "chunked"))
        self._send(conn, state, self._head(200, pairs, headers))
        for i, line in enumerate(lines):
            if i < len(gaps) and gaps[i]:
                self._wait(conn, gaps[i])
            else:
                self._check_gone(conn)
            data = _peer_line_bytes(line)
            if chunked:
                data = b"%x\r\n" % len(data) + data + b"\r\n"
            self._send(conn, state, data)
        if chunked:
            self._send(conn, state, b"0\r\n\r\n")

    def _redirect(self, conn, state, location, status=307):
        pairs = [("Location", location), ("Content-Length", "0"), ("Connection", "close")]
        self._send(conn, state, self._head(status, pairs, ()))

    # -- plumbing -----------------------------------------------------------

    @staticmethod
    def _head(status, defaults, extra):
        """A response head; an *extra* pair whose name matches a default replaces it."""
        names = {k.lower() for k, _v in extra}
        pairs = [(k, v) for k, v in defaults if k.lower() not in names] + list(extra)
        lines = ["HTTP/1.1 %d %s" % (status, http.client.responses.get(status, "Status"))]
        lines += ["%s: %s" % kv for kv in pairs]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    def _send(self, conn, state, data):
        """sendall under close_after's budget; a failed write means the client is gone."""
        drop = False
        budget = state["budget"]
        if budget is not None:
            if len(data) >= budget:
                data, drop = data[:budget], True
            else:
                state["budget"] = budget - len(data)
        try:
            if data:
                conn.sendall(data)
        except socket.timeout:
            raise
        except OSError:
            raise _PeerClientGone()
        if drop:
            raise _PeerDrop()

    def _check_gone(self, conn):
        """Raise _PeerClientGone iff the client has closed its side (non-blocking probe)."""
        if self._stopping:
            raise _PeerStop()
        try:
            readable, _w, _x = select.select([conn], [], [], 0)
        except (OSError, ValueError):
            raise _PeerClientGone()
        if not readable:
            return
        old = conn.gettimeout()
        try:
            conn.settimeout(PEER_POLL_S)
            data = conn.recv(4096)
        except socket.timeout:
            return              # a partial TLS record: not an EOF
        except OSError:
            data = b""
        finally:
            try:
                conn.settimeout(old)
            except OSError:
                pass
        if not data:
            raise _PeerClientGone()

    def _wait(self, conn, seconds):
        """Sleep *seconds* in PEER_POLL_S slices, noticing a client close or close()."""
        deadline = time.monotonic() + float(seconds)
        while True:
            self._check_gone(conn)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                select.select([conn], [], [], min(PEER_POLL_S, remaining))
            except (OSError, ValueError):
                raise _PeerClientGone()


# ---------------------------------------------------------------------------
# Group A: config
# ---------------------------------------------------------------------------

GA_CASES = (
    "mode 0640 config refused",          # A1
    "mode 0604 config refused",          # A2
    "mode 0620 config refused",          # A3
    "symlinked config refused",          # A4
    "directory config refused",          # A5
    "duplicate top key refused",         # A6
    "duplicate backend key refused",     # A7
    "casefold route clash refused",      # A8
    "unknown key refused by name",       # A9
    "plain http needs allow_private",    # A10
    "base_url extras refused",           # A11
    "no sentinel in any refusal",        # A12
    "short auth_token refused",          # A13
    "reserved kind anthropic",           # A14
    "api_key == auth_token refused",     # A15
    "unknown route option refused",      # A16
    "documented example accepted",       # A17
    "unforwardable header refused",      # A18
    "unknown quirks_off refused",        # A19
    "bad ca_file refused",               # A20
    "CLI refusals exit 2, one line",     # A21
    "config of another uid refused",     # A22
    "route named default refused",       # A23
    "loose/symlinked ca_file refused",   # A24
    "short api_key refused",             # A25
    "cleartext api_key refused",         # A26
    "route display_name validated",      # A27
)

# (label, text, is_config_error) of every exception group A provoked and every
# refused-start stderr line; A12 sweeps it for every sentinel.
GA_MESSAGES = []

GA_DUP_MARKER = "TF_GA_DUP_MARKER"
GA_TOKEN_HINT = "secrets.token_urlsafe(32)"     # A13: every auth_token refusal names the generator
GA_ALPHABET = "abcdefghijklmnopqrstuvwxyz"      # A13: a token of exactly n distinct characters
# A26: http:// backends whose api_key needs no opt-in (a loopback host).
GA_LOOPBACK_HTTP = ("http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080")


def ga_redact(text):
    """*text* with every sentinel replaced, so a FAIL line never prints a secret."""
    for value in SENTINELS:
        text = text.replace(value, "<sentinel>")
    return text


def ga_base_config():
    """A valid config (a fresh copy per call); each A case breaks one field of it.

    No base_url is loopback, so write_config adds none of its loopback defaults.
    infernox sends its api_key over a non-loopback http:// URL, so it carries the
    explicit opt-in (A26 removes it).
    """
    return {
        "auth_token": TF_ROUTER_TOKEN,
        "backends": {
            "infernox": {"kind": "passthrough", "base_url": "http://192.168.1.20:8080",
                         "allow_private": True, "api_key": TF_KEY_PASS,
                         "auth_header": "x-api-key", "allow_cleartext_api_key": True},
            "llama": {"kind": "llamacpp", "base_url": "https://llama.lan:8443",
                      "allow_private": True},
            "mistral": {"kind": "mistral", "base_url": "https://api.mistral.ai",
                        "api_key": TF_KEY_MISTRAL},
        },
        "routes": {
            "claude-sonnet-4-5": {"backend": "mistral", "model": "mistral-large-latest"},
            "claude-haiku-4-5": {"backend": "llama", "model": "qwen3-coder"},
        },
        "default": {"backend": "infernox", "model": "local-model"},
    }


def ga_refused(mod, path, needles, label, absent=()):
    """Problems unless load_config(*path*) raises mod.ConfigError whose message
    holds every needle and none of *absent* (nor any sentinel)."""
    try:
        mod.load_config(path)
    except Exception as exc:  # noqa: BLE001 -- any other type is the finding
        msg = str(exc)
        is_cfg = isinstance(exc, getattr(mod, "ConfigError", ()))
        GA_MESSAGES.append((label, msg, is_cfg))
        if not is_cfg:
            return ["%s: raised %s instead of ConfigError: %s"
                    % (label, type(exc).__name__, ga_redact(msg)[:200])]
        problems = []
        missing = [n for n in needles if n not in msg]
        if missing:
            problems.append("%s: ConfigError %r does not name %r"
                            % (label, ga_redact(msg), missing))
        if any(value and value in msg for value in tuple(absent) + SENTINELS):
            problems.append("%s: the message echoes a value it must never show" % label)
        return problems
    return ["%s: accepted, expected a ConfigError" % label]


def ga_accepted(mod, path, label):
    """(config, problems): load_config(*path*) must succeed."""
    try:
        return mod.load_config(path), []
    except Exception as exc:  # noqa: BLE001
        return None, ["%s: refused: %s: %s" % (label, type(exc).__name__, ga_redact(str(exc))[:200])]


def ga_module_value(mod, name, problems):
    """A limit read from the module (never typed here); None plus a problem if absent."""
    if not hasattr(mod, name):
        problems.append("the module defines no %s" % name)
        return None
    return getattr(mod, name)


def ga_dup_text(cfg, raw):
    """*cfg* as JSON with the GA_DUP_MARKER string replaced by the raw JSON text *raw*."""
    return json.dumps(cfg, indent=2).replace(json.dumps(GA_DUP_MARKER), raw)


def ga_cli_refused(sandbox, cfgpath, stem, extra, needles):
    """(problems, stderr): a router start with *extra* must exit 2 before binding,
    with exactly one stderr line, `llm-router: <msg>`, naming every needle."""
    ready = os.path.join(sandbox, stem + ".ready.json")
    WRITES.append(ready)
    errpath = os.path.join(sandbox, stem + ".stderr")
    WRITES.append(errpath)
    LOG_PATHS.append(errpath)
    rc = None
    with open(errpath, "wb") as err:
        proc = subprocess.Popen(router_argv(cfgpath, ready, *extra),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err, cwd=sandbox, env=H.child_env())
        try:
            rc = proc.wait(timeout=REFUSAL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=KILL_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                pass
    with open(errpath, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    problems = []
    if rc is None:
        problems.append("%s: still running after %.0f s (killed); expected exit 2"
                        % (stem, REFUSAL_TIMEOUT_S))
    elif rc != 2:
        problems.append("%s: exit code %r, expected 2" % (stem, rc))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for line in lines:
        GA_MESSAGES.append((stem, line, False))
    if len(lines) != 1:
        problems.append("%s: %d stderr line(s), expected exactly 1: %r"
                        % (stem, len(lines), ga_redact(text.strip())[-300:]))
    else:
        if not lines[0].startswith("llm-router: "):
            problems.append("%s: stderr line %r does not start with 'llm-router: '"
                            % (stem, ga_redact(lines[0])))
        missing = [n for n in needles if n not in lines[0]]
        if missing:
            problems.append("%s: stderr line %r does not name %r"
                            % (stem, ga_redact(lines[0]), missing))
    if any(value in text for value in SENTINELS):
        problems.append("%s: a sentinel occurs on stderr" % stem)
    if os.path.exists(ready):
        problems.append("%s: the ready file was written -- the refusal came after bind" % stem)
    return problems


def ga_example_text(ca_file):
    """The plan's section-3 authoritative example config, byte for byte, with A17's three
    substitutions: ca_file, auth_token and the two api_key placeholders -- plus
    infernox's `"allow_cleartext_api_key": true`, the opt-in its http:// api_key
    has needed since validation round 1 (V14)."""
    subs = {
        "token": json.dumps(TF_ROUTER_TOKEN),
        "key_pass": json.dumps(TF_KEY_PASS),
        "ca_file": json.dumps(ca_file),
        "key_mistral": json.dumps(TF_KEY_MISTRAL),
    }
    return (
        '{\n'
        '  "auth_token": %(token)s,\n'
        '  "backends": {\n'
        '    "infernox": {"kind": "passthrough", "base_url": "http://192.168.1.20:8080", "allow_private": true,\n'
        '                 "api_key": %(key_pass)s, "auth_header": "x-api-key", "allow_cleartext_api_key": true,\n'
        '                 "forward_headers": ["anthropic-version", "anthropic-beta"]},\n'
        '    "llama":    {"kind": "llamacpp", "base_url": "https://llama.lan:8443", "allow_private": true,\n'
        '                 "ca_file": %(ca_file)s},\n'
        '    "mistral":  {"kind": "mistral", "base_url": "https://api.mistral.ai", "api_key": %(key_mistral)s,\n'
        '                 "connect_timeout": 10, "idle_timeout": 300}\n'
        '  },\n'
        '  "routes": {\n'
        '    "claude-sonnet-4-5": {"backend": "mistral", "model": "mistral-large-latest",\n'
        '                          "options": {"count_divisor": 4, "temperature": 0.3, "max_tokens_cap": 32768}},\n'
        '    "claude-haiku-4-5":  {"backend": "llama", "model": "qwen3-coder",\n'
        '                          "options": {"thinking_budget": 4096, "quirks_off": ["hoist-system"]}}\n'
        '  },\n'
        '  "default": {"backend": "infernox", "model": "local-model"}\n'
        '}\n'
    ) % subs


def ga_check_example(mod, cfg, ca_file):
    """Problems in the RouterConfig loaded from the section-3 example."""
    problems = []
    want_backends = {"infernox", "llama", "mistral"}
    if getattr(cfg, "token", None) != TF_ROUTER_TOKEN.encode("ascii"):
        problems.append("token is not the configured auth_token as ASCII bytes")
    backends = getattr(cfg, "backends", None) or {}
    if set(backends) != want_backends:
        problems.append("backends %r != %r" % (sorted(backends), sorted(want_backends)))
        return problems
    connect = ga_module_value(mod, "_CONNECT_TIMEOUT_S", problems)
    idle = ga_module_value(mod, "_IDLE_TIMEOUT_S", problems)
    want = {
        "infernox": {"kind": "passthrough", "scheme": "http", "host": "192.168.1.20",
                     "port": 8080, "base_path": "", "allow_private": True,
                     "allow_loopback": False, "ca_file": None, "api_key": TF_KEY_PASS,
                     "auth_header": "x-api-key",
                     "forward_headers": frozenset({"anthropic-version", "anthropic-beta"}),
                     "connect_timeout": connect, "idle_timeout": idle},
        "llama": {"kind": "llamacpp", "scheme": "https", "host": "llama.lan", "port": 8443,
                  "base_path": "", "allow_private": True, "allow_loopback": False,
                  "ca_file": ca_file, "api_key": None, "auth_header": "none",
                  "connect_timeout": connect, "idle_timeout": idle},
        "mistral": {"kind": "mistral", "scheme": "https", "host": "api.mistral.ai", "port": 443,
                    "base_path": "", "allow_private": False, "allow_loopback": False,
                    "ca_file": None, "api_key": TF_KEY_MISTRAL, "auth_header": "authorization",
                    "connect_timeout": 10.0, "idle_timeout": 300.0},
    }
    for name, fields in want.items():
        spec = backends[name]
        for field, value in fields.items():
            if not hasattr(spec, field):
                problems.append("backends.%s has no field %s" % (name, field))
                continue
            got = getattr(spec, field)
            if got != value:
                shown = "<redacted>" if field == "api_key" else repr(got)
                problems.append("backends.%s.%s = %s, expected %s"
                                % (name, field, shown,
                                   "<the configured key>" if field == "api_key" else repr(value)))
    ca_pem = getattr(backends["llama"], "ca_pem", None)
    if not isinstance(ca_pem, str) or "-----BEGIN CERTIFICATE-----" not in ca_pem:
        problems.append("backends.llama.ca_pem does not hold the PEM read from ca_file")
    for name in ("infernox", "mistral"):
        if getattr(backends[name], "ca_pem", "missing") is not None:
            problems.append("backends.%s.ca_pem is set without a ca_file" % name)
    routes = getattr(cfg, "routes", None) or {}
    want_routes = {
        "claude-sonnet-4-5": ("mistral", "mistral-large-latest",
                              {"count_divisor": 4, "temperature": 0.3, "max_tokens_cap": 32768}),
        "claude-haiku-4-5": ("llama", "qwen3-coder",
                             {"thinking_budget": 4096, "quirks_off": ["hoist-system"]}),
    }
    if set(routes) != set(want_routes):
        problems.append("routes %r != %r" % (sorted(routes), sorted(want_routes)))
    else:
        for name, (backend, model, options) in want_routes.items():
            route = routes[name]
            got = (getattr(route, "name", None), getattr(route, "backend", None),
                   getattr(route, "model", None))
            if got != (name, backend, model):
                problems.append("routes[%r] (name, backend, model) = %r" % (name, got))
            got_opts = getattr(route, "options", None)
            if not isinstance(got_opts, dict) or set(got_opts) != set(options) or any(
                    (list(got_opts[k]) if isinstance(v, list) else got_opts[k]) != v
                    for k, v in options.items()):
                problems.append("routes[%r].options = %r, expected %r" % (name, got_opts, options))
    default = getattr(cfg, "default", None)
    got = (getattr(default, "name", None), getattr(default, "backend", None),
           getattr(default, "model", None))
    if got != ("(default)", "infernox", "local-model"):
        problems.append("default (name, backend, model) = %r" % (got,))
    secrets_ = getattr(cfg, "secrets", None)
    if not isinstance(secrets_, tuple) or not {
            TF_ROUTER_TOKEN.encode("ascii"), TF_KEY_PASS.encode("ascii"),
            TF_KEY_MISTRAL.encode("ascii")} <= set(secrets_):
        problems.append("secrets does not hold the token and both api_keys as bytes")
    if not callable(getattr(cfg, "scrub", None)):
        problems.append("scrub is not callable")
    return problems


def group_a(suite, fixture_root):
    """A. load_config refusals, imported in-process; the CLI refusals as a subprocess.

    Every refusal must be a ConfigError whose message names the field and the
    rule and never a value; every limit is read from the module, never typed
    here.  An import failure or a missing load_config fails every case (red),
    never skips it.
    """
    del GA_MESSAGES[:]
    try:
        mod = H.load_module_from_path("ph_llm_router", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GA_CASES:
            suite.record(GA, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT),
                                      type(exc).__name__, exc)])
        return

    sandbox = new_sandbox(fixture_root, "config")
    with open(os.path.join(TLS, "test-ca.pem"), "r", encoding="utf-8") as fh:
        pem = fh.read()

    def cfg_with(name, mutate=None, mode=0o600):
        cfg = ga_base_config()
        if mutate is not None:
            mutate(cfg)
        return write_config(sandbox, cfg, mode=mode, filename=name)

    def mode_case(mode):
        def case():
            problems = []
            path = cfg_with("mode-%o.json" % mode, mode=mode)
            problems += ga_refused(mod, path, ["--config", "group/others"], "mode %04o" % mode)
            _cfg, ctl = ga_accepted(mod, cfg_with("mode-600.json"), "control: mode 0600")
            return problems + ctl, ["modes       : %04o refused, 0600 control loads (fchmod, umask-proof)"
                                    % mode]
        return case

    def a4():
        target = cfg_with("symlink-target.json")
        link = os.path.join(sandbox, "symlink-config.json")
        os.symlink(target, link)
        WRITES.append(link)
        problems = ga_refused(mod, link, ["--config"], "symlink to a valid 0600 config")
        _cfg, ctl = ga_accepted(mod, target, "control: the symlink target itself")
        return problems + ctl, ["link        : -> a valid 0600 config (O_NOFOLLOW refuses it)"]

    def a5():
        path = os.path.join(sandbox, "config-dir.json")
        os.mkdir(path, 0o700)
        WRITES.append(path)
        return ga_refused(mod, path, ["--config", "regular file"], "a directory"), []

    def a6():
        cfg = ga_base_config()
        text = json.dumps(cfg, indent=2)
        text = '{\n  "auth_token": %s,%s' % (json.dumps(TF_ROUTER_TOKEN), text[1:])
        path = write_file(os.path.join(sandbox, "dup-top.json"), text)
        return ga_refused(mod, path, ["duplicate key", "'auth_token'"],
                          "duplicate auth_token"), ["config      : auth_token twice (ADR 0015)"]

    def a7():
        cfg = ga_base_config()
        cfg["backends"]["mistral"] = GA_DUP_MARKER
        second = TF_KEY_MISTRAL + "-second"
        raw = ('{"kind": "mistral", "base_url": "https://api.mistral.ai", '
               '"api_key": %s, "api_key": %s}' % (json.dumps(TF_KEY_MISTRAL), json.dumps(second)))
        path = write_file(os.path.join(sandbox, "dup-backend.json"), ga_dup_text(cfg, raw))
        return ga_refused(mod, path, ["duplicate key", "'api_key'"], "duplicate api_key",
                          absent=(second,)), ["config      : backends.mistral.api_key twice"]

    def a8():
        def mutate(cfg):
            cfg["routes"]["Claude-Sonnet-4-5"] = {"backend": "llama", "model": "qwen3-coder"}
        path = cfg_with("casefold.json", mutate)
        return ga_refused(mod, path, ["routes", "differ only by case"],
                          "claude-sonnet-4-5 vs Claude-Sonnet-4-5"), []

    def a9():
        problems = []
        for key, value in (("tf_bogus_top", 1), ("allowed_origins", ["http://localhost"]),
                           ("body_limit", 1 << 20)):
            path = cfg_with("unknown-top-%s.json" % key,
                            lambda cfg, k=key, v=value: cfg.__setitem__(k, v))
            problems += ga_refused(mod, path, [key], "top key %s" % key)
        path = cfg_with("unknown-backend-key.json",
                        lambda cfg: cfg["backends"]["mistral"].__setitem__("tf_bogus_backend", 1))
        problems += ga_refused(mod, path, ["backends.mistral", "tf_bogus_backend"],
                               "backend key tf_bogus_backend")
        path = cfg_with("unknown-route-key.json",
                        lambda cfg: cfg["routes"]["claude-haiku-4-5"].__setitem__("tf_bogus_route", 1))
        problems += ga_refused(mod, path, ["tf_bogus_route"], "route key tf_bogus_route")
        return problems, ["top keys    : tf_bogus_top, allowed_origins, body_limit (CLI settings)",
                          "nested      : a backend key and a route key"]

    def a10():
        problems = []
        path = cfg_with("http-no-private.json",
                        lambda cfg: cfg["backends"]["infernox"].pop("allow_private"))
        problems += ga_refused(mod, path, ["backends.infernox", "allow_private"],
                               "http:// without allow_private")

        def loopback(cfg):
            cfg["backends"]["llama"] = {"kind": "llamacpp", "base_url": "https://127.0.0.1:8443",
                                        "allow_private": False, "allow_loopback": True}
        path = cfg_with("loopback-no-private.json", loopback)
        problems += ga_refused(mod, path, ["backends.llama.allow_loopback", "requires allow_private"],
                               "allow_loopback without allow_private")
        return problems, ["http        : http://192.168.1.20:8080, allow_private absent",
                          "loopback    : allow_loopback true, allow_private false"]

    def a11():
        problems = []
        for stem, url, needle, secret in (
                ("userinfo", "https://tf-user:tf-pass-ga11@api.mistral.ai", "userinfo", "tf-pass-ga11"),
                ("query", "https://api.mistral.ai/?tf-query-ga11=1", "query", "tf-query-ga11"),
                ("fragment", "https://api.mistral.ai/#tf-frag-ga11", "fragment", "tf-frag-ga11")):
            path = cfg_with("url-%s.json" % stem,
                            lambda cfg, u=url: cfg["backends"]["mistral"].__setitem__("base_url", u))
            problems += ga_refused(mod, path, ["backends.mistral.base_url", needle],
                                   "base_url with a %s" % stem, absent=(secret,))
        return problems, ["parts       : userinfo, query, fragment -- each named, never echoed"]

    def a13():
        problems = []
        need = ga_module_value(mod, "_TOKEN_MIN_LEN", problems)
        if not isinstance(need, int):
            return problems, []
        short = ("tf-short-" + "x" * need)[:need - 1]
        path = cfg_with("token-short.json", lambda cfg: cfg.__setitem__("auth_token", short))
        problems += ga_refused(mod, path, ["auth_token", GA_TOKEN_HINT],
                               "token of %d chars" % (need - 1), absent=(short,))
        # V1: a long token of too few distinct characters is refused the same way.
        distinct = ga_module_value(mod, "_TOKEN_MIN_DISTINCT", problems)
        if isinstance(distinct, int):
            for stem, weak in (("one", "a" * need),
                               ("few", (GA_ALPHABET[:distinct - 1] * need)[:need])):
                path = cfg_with("token-%s.json" % stem,
                                lambda cfg, w=weak: cfg.__setitem__("auth_token", w))
                problems += ga_refused(mod, path, ["auth_token", "distinct", GA_TOKEN_HINT],
                                       "token of %d chars, %d distinct" % (need, len(set(weak))),
                                       absent=(weak,))
        exact = ("tf-exact-" + "y" * need)[:need]
        if isinstance(distinct, int) and len(set(exact)) < distinct:
            problems.append("control token has %d distinct chars, under _TOKEN_MIN_DISTINCT = %d"
                            % (len(set(exact)), distinct))
        path = cfg_with("token-exact.json", lambda cfg: cfg.__setitem__("auth_token", exact))
        _cfg, ctl = ga_accepted(mod, path, "control: token of exactly %d chars" % need)
        return problems + ctl, ["bound       : _TOKEN_MIN_LEN = %d (read from the module)" % need,
                                "distinct    : _TOKEN_MIN_DISTINCT = %r; %d-char tokens of 1 and "
                                "%r distinct refused" % (distinct, need,
                                                         distinct - 1 if isinstance(distinct, int)
                                                         else None),
                                "hint        : every refusal names %s" % GA_TOKEN_HINT]

    def a14():
        def mutate(cfg):
            cfg["backends"]["tf-reserved"] = {"kind": "anthropic",
                                              "base_url": "https://api.anthropic.com"}
        path = cfg_with("kind-anthropic.json", mutate)
        return ga_refused(mod, path,
                          ['backends.tf-reserved.kind: "anthropic" is reserved and not implemented'],
                          "kind anthropic"), ["pinned      : the reserved-kind message, verbatim"]

    def a15():
        path = cfg_with("key-is-token.json",
                        lambda cfg: cfg["backends"]["infernox"].__setitem__("api_key", TF_ROUTER_TOKEN))
        return ga_refused(mod, path, ["backends.infernox.api_key", "auth_token"],
                          "api_key == auth_token"), []

    def a16():
        problems = []
        path = cfg_with("route-option.json",
                        lambda cfg: cfg["routes"]["claude-sonnet-4-5"].__setitem__(
                            "options", {"tf_bogus_option": 1}))
        problems += ga_refused(mod, path, ["claude-sonnet-4-5", "tf_bogus_option"],
                               "mistral route option tf_bogus_option")
        path = cfg_with("default-option.json",
                        lambda cfg: cfg["default"].__setitem__("options", {"temperature": 0.3}))
        problems += ga_refused(mod, path, ["temperature"],
                               "passthrough default route option temperature")
        return problems, ["options     : unknown on mistral; any on passthrough (it has none)"]

    def a17():
        ca_file = sandbox_ca_file(sandbox)
        path = write_file(os.path.join(sandbox, "example.json"), ga_example_text(ca_file))
        cfg, problems = ga_accepted(mod, path, "the section-3 example")
        if cfg is not None:
            problems += ga_check_example(mod, cfg, ca_file)
        return problems, ["config      : plan section-3 example, byte for byte, three substitutions:",
                          "              ca_file -> sandbox_ca_file(sandbox),",
                          "              auth_token -> TF_ROUTER_TOKEN,",
                          "              api_key placeholders -> TF_KEY_PASS / TF_KEY_MISTRAL"]

    def a18():
        problems = []
        path = cfg_with("fwd-passthrough.json",
                        lambda cfg: cfg["backends"]["infernox"].__setitem__(
                            "forward_headers", ["authorization"]))
        problems += ga_refused(mod, path, ["backends.infernox.forward_headers", "'authorization'"],
                               "authorization on passthrough")
        path = cfg_with("fwd-mistral.json",
                        lambda cfg: cfg["backends"]["mistral"].__setitem__(
                            "forward_headers", ["anthropic-version"]))
        problems += ga_refused(mod, path, ["backends.mistral.forward_headers", "'anthropic-version'"],
                               "anthropic-version on mistral")
        return problems, ["passthrough : authorization (never FORWARDABLE)",
                          "mistral     : anything (FORWARDABLE is empty)"]

    def a19():
        problems = []
        path = cfg_with("quirk-unknown.json",
                        lambda cfg: cfg["routes"]["claude-haiku-4-5"].__setitem__(
                            "options", {"quirks_off": ["tf-no-such-quirk"]}))
        problems += ga_refused(mod, path, ["quirks_off", "tf-no-such-quirk"], "quirks_off name")
        path = cfg_with("quirk-known.json",
                        lambda cfg: cfg["routes"]["claude-haiku-4-5"].__setitem__(
                            "options", {"quirks_off": ["hoist-system"]}))
        _cfg, ctl = ga_accepted(mod, path, "control: quirks_off ['hoist-system']")
        return problems + ctl, []

    def a20():
        problems = []
        missing = os.path.join(sandbox, "tf-missing-ca.pem")
        path = cfg_with("ca-missing.json",
                        lambda cfg: cfg["backends"]["llama"].__setitem__("ca_file", missing))
        problems += ga_refused(mod, path, ["backends.llama.ca_file"], "ca_file missing")
        ca_dir = os.path.join(sandbox, "ca-dir.pem")
        os.mkdir(ca_dir, 0o700)
        WRITES.append(ca_dir)
        path = cfg_with("ca-dir.json",
                        lambda cfg: cfg["backends"]["llama"].__setitem__("ca_file", ca_dir))
        problems += ga_refused(mod, path, ["backends.llama.ca_file", "regular file"],
                               "ca_file a directory")
        good = os.path.abspath(write_file(os.path.join(sandbox, "ca-good.pem"), pem))
        path = cfg_with("ca-on-http.json",
                        lambda cfg: cfg["backends"]["infernox"].__setitem__("ca_file", good))
        problems += ga_refused(mod, path, ["backends.infernox.ca_file", "http"],
                               "ca_file on an http:// URL")
        path = cfg_with("ca-good.json",
                        lambda cfg: cfg["backends"]["llama"].__setitem__("ca_file", good))
        _cfg, ctl = ga_accepted(mod, path, "control: a valid 0600 ca_file on https")
        return problems + ctl, []

    def a21():
        problems = []
        limits = ga_module_value(mod, "_BODY_LIMIT_RANGE", problems)
        cfgpath = cfg_with("cli.json")
        if isinstance(limits, tuple) and len(limits) == 2:
            low, high = limits
            for stem, value in (("body-low", low - 1), ("body-high", high + 1)):
                problems += ga_cli_refused(sandbox, cfgpath, stem, ["--body-limit", value],
                                           ["--body-limit"])
        problems += ga_cli_refused(sandbox, cfgpath, "bind-remote", ["--bind", "0.0.0.0"],
                                   ["--bind", "--allow-remote"])
        # V2: --allow-remote admits a VPN interface, never every interface or a
        # public address. The settings rule is checked in-process (no listener
        # is ever bound on 0.0.0.0 by a red run); the CLI line only proves the
        # refusal comes before bind, and runs only once the settings rule did.
        any_refused = False
        for bind, refused in (("0.0.0.0", True), ("8.8.8.8", True), ("10.0.0.1", False),
                              ("100.64.0.1", False), ("127.0.0.1", False)):
            label = "--bind %s --allow-remote" % bind
            try:
                mod._rt_settings(mod._rt_parser().parse_args(
                    ["--config", cfgpath, "--bind", bind, "--allow-remote"]))
            except mod.ConfigError as exc:
                any_refused = any_refused or bind == "0.0.0.0"
                if not refused:
                    problems.append("%s: refused: %s" % (label, exc))
                elif not all(n in str(exc) for n in ("--bind", "--allow-remote")):
                    problems.append("%s: ConfigError %r does not name --bind and --allow-remote"
                                    % (label, str(exc)))
            else:
                if refused:
                    problems.append("%s: accepted by _rt_settings, expected a ConfigError" % label)
        if any_refused:
            problems += ga_cli_refused(sandbox, cfgpath, "bind-any-remote",
                                       ["--bind", "0.0.0.0", "--allow-remote"],
                                       ["--bind", "--allow-remote"])
        return problems, ["argv        : --body-limit min-1 and max+1 (_BODY_LIMIT_RANGE);",
                          "              --bind 0.0.0.0 without --allow-remote",
                          "              --bind 0.0.0.0 --allow-remote (V2)",
                          "settings    : --allow-remote refuses 0.0.0.0, 8.8.8.8; admits 10.0.0.1, "
                          "100.64.0.1, 127.0.0.1",
                          "expect      : exit 2, one `llm-router: ` stderr line, no ready file"]

    def a22():
        if os.geteuid() != 0:
            return None, ["not root: the suite cannot chown the config to another uid",
                          "(INFO; the mcp-proxy suite records the same limit)"]
        path = cfg_with("other-uid.json")
        os.chown(path, 1, -1)
        return ga_refused(mod, path, ["--config", "not owned"], "config owned by uid 1"), []

    def a23():
        problems = []
        for name in ("default", "Default"):
            path = cfg_with("route-%s.json" % name,
                            lambda cfg, n=name: cfg["routes"].__setitem__(
                                n, {"backend": "llama", "model": "qwen3-coder"}))
            problems += ga_refused(mod, path, ["routes", "reserved"], "routes key %r" % name)
        return problems, ["names       : 'default' and 'Default' (casefold)"]

    def a24():
        problems = []
        for mode in (0o664, 0o646):
            ca = os.path.abspath(write_file(os.path.join(sandbox, "ca-%o.pem" % mode), pem, mode))
            path = cfg_with("ca-%o.json" % mode,
                            lambda cfg, c=ca: cfg["backends"]["llama"].__setitem__("ca_file", c))
            problems += ga_refused(mod, path, ["backends.llama.ca_file", "group/others"],
                                   "ca_file mode %04o" % mode,
                                   absent=("-----BEGIN CERTIFICATE-----",))
        good = os.path.abspath(write_file(os.path.join(sandbox, "ca-link-target.pem"), pem))
        link = os.path.join(sandbox, "ca-link.pem")
        os.symlink(good, link)
        WRITES.append(link)
        path = cfg_with("ca-link.json",
                        lambda cfg: cfg["backends"]["llama"].__setitem__("ca_file", link))
        problems += ga_refused(mod, path, ["backends.llama.ca_file"], "ca_file a symlink",
                               absent=("-----BEGIN CERTIFICATE-----",))
        ca = os.path.abspath(write_file(os.path.join(sandbox, "ca-644.pem"), pem, 0o644))
        path = cfg_with("ca-644.json",
                        lambda cfg: cfg["backends"]["llama"].__setitem__("ca_file", ca))
        _cfg, ctl = ga_accepted(mod, path, "control: ca_file mode 0644 (no write bit)")
        return problems + ctl, ["refused     : 0664, 0646, a symlink to a valid PEM",
                                "control     : 0644 loads (the rule is on write bits)"]

    def a25():
        problems = []
        need = ga_module_value(mod, "_API_KEY_MIN_LEN", problems)
        if not isinstance(need, int):
            return problems, []
        short = ("tf-shortkey-" + "k" * need)[:need - 1]
        path = cfg_with("key-short.json",
                        lambda cfg: cfg["backends"]["mistral"].__setitem__("api_key", short))
        problems += ga_refused(mod, path, ["backends.mistral.api_key", str(need)],
                               "api_key of %d chars" % (need - 1), absent=(short,))
        exact = ("tf-exactkey-" + "q" * need)[:need]
        path = cfg_with("key-exact.json",
                        lambda cfg: cfg["backends"]["mistral"].__setitem__("api_key", exact))
        _cfg, ctl = ga_accepted(mod, path, "control: api_key of exactly %d chars" % need)
        return problems + ctl, ["bound       : _API_KEY_MIN_LEN = %d (read from the module)" % need]

    def a26():
        problems = []
        path = cfg_with("key-cleartext.json",
                        lambda cfg: cfg["backends"]["infernox"].pop("allow_cleartext_api_key"))
        problems += ga_refused(mod, path, ["backends.infernox.api_key", "http://",
                                           "allow_cleartext_api_key"],
                               "api_key on http://192.168.1.20 without the opt-in")
        path = cfg_with("key-cleartext-str.json",
                        lambda cfg: cfg["backends"]["infernox"].__setitem__(
                            "allow_cleartext_api_key", "yes"))
        problems += ga_refused(mod, path, ["backends.infernox.allow_cleartext_api_key",
                                           "true or false"], "allow_cleartext_api_key a string")
        _cfg, ctl = ga_accepted(mod, cfg_with("key-cleartext-optin.json"),
                                "control: the opt-in set (the base config)")
        problems += ctl
        for url in GA_LOOPBACK_HTTP:
            def loop(cfg, u=url):
                cfg["backends"]["infernox"] = {"kind": "passthrough", "base_url": u,
                                               "allow_private": True, "allow_loopback": True,
                                               "api_key": TF_KEY_PASS, "auth_header": "x-api-key"}
            _cfg, ctl = ga_accepted(mod, cfg_with("key-loopback-%d.json" % GA_LOOPBACK_HTTP.index(url),
                                                  loop),
                                    "control: api_key on %s, no opt-in" % url)
            problems += ctl
        return problems, ["refused     : api_key on a non-loopback http:// URL, no opt-in; "
                          "the opt-in as a string",
                          "accepted    : the opt-in true; %s with no opt-in" % ", ".join(GA_LOOPBACK_HTTP)]

    def a27():
        problems = []
        limit = ga_module_value(mod, "_RT_DISPLAY_NAME_LIMIT", problems)
        if not isinstance(limit, int):
            return problems, []
        long_value = ("tf-dn-long-" + "d" * limit)[:limit + 1]
        bad = (("a number", 123), ("null", None), ("empty", ""),
               ("a control character", "tf-dn-bell\x07"), ("%d characters" % (limit + 1), long_value))
        places = (("routes.claude-sonnet-4-5", lambda cfg: cfg["routes"]["claude-sonnet-4-5"]),
                  ("default", lambda cfg: cfg["default"]))
        n = 0
        for where, place in places:
            for label, value in bad:
                n += 1
                path = cfg_with("display-name-%d.json" % n,
                                lambda cfg, p=place, v=value: p(cfg).__setitem__("display_name", v))
                absent = (value,) if isinstance(value, str) and value else ()
                problems += ga_refused(mod, path, [where + ".display_name"],
                                       "%s.display_name %s" % (where, label), absent=absent)
        good_route = "TF Sonnet ✓"
        good_default = ("tf-dn-max-" + "e" * limit)[:limit]

        def good(cfg):
            cfg["routes"]["claude-sonnet-4-5"]["display_name"] = good_route
            cfg["default"]["display_name"] = good_default
        cfg, ctl = ga_accepted(mod, cfg_with("display-name-good.json", good),
                               "control: display_name on a route and (%d chars) on the default" % limit)
        problems += ctl
        if cfg is not None:
            for label, spec, want in (("routes.claude-sonnet-4-5", cfg.routes.get("claude-sonnet-4-5"),
                                       good_route),
                                      ("default", cfg.default, good_default),
                                      ("routes.claude-haiku-4-5 (unset)", cfg.routes.get("claude-haiku-4-5"),
                                       None)):
                got = getattr(spec, "display_name", "<no display_name field>")
                if got != want:
                    problems.append("%s: RouteSpec.display_name %r, expected %r"
                                    % (label, ga_redact(str(got))[:80], want))
        return problems, ["refused     : a number, null, empty, a control character, %d characters "
                          "-- on a route and on the default, by key name" % (limit + 1),
                          "accepted    : a non-ASCII name; exactly %d characters "
                          "(_RT_DISPLAY_NAME_LIMIT); unset -> None" % limit]

    def a12():
        problems = []

        def bad_key(cfg):
            cfg["backends"]["mistral"]["api_key"] = TF_KEY_MISTRAL + " tail"
        problems += ga_refused(mod, cfg_with("key-space.json", bad_key),
                               ["backends.mistral.api_key"], "api_key with whitespace")
        problems += ga_refused(mod, cfg_with("token-space.json",
                                             lambda cfg: cfg.__setitem__(
                                                 "auth_token", TF_ROUTER_TOKEN + " tail")),
                               ["auth_token"], "auth_token with whitespace")
        refusals = [m for m in GA_MESSAGES if m[2]]
        if not refusals:
            problems.append("no ConfigError was raised anywhere in group A: nothing to sweep "
                            "(a vacuous pass is refused)")
        for label, text, _is_cfg in GA_MESSAGES:
            if any(value in text for value in SENTINELS):
                problems.append("%s: a sentinel occurs in the refusal" % label)
        return problems, ["swept       : %d message(s), %d of them ConfigError(s)"
                          % (len(GA_MESSAGES), len(refusals)),
                          "sentinels   : router token, both backend keys, the client key"]

    cases = {
        GA_CASES[0]: mode_case(0o640), GA_CASES[1]: mode_case(0o604),
        GA_CASES[2]: mode_case(0o620), GA_CASES[3]: a4, GA_CASES[4]: a5,
        GA_CASES[5]: a6, GA_CASES[6]: a7, GA_CASES[7]: a8, GA_CASES[8]: a9,
        GA_CASES[9]: a10, GA_CASES[10]: a11, GA_CASES[12]: a13, GA_CASES[13]: a14,
        GA_CASES[14]: a15, GA_CASES[15]: a16, GA_CASES[16]: a17, GA_CASES[17]: a18,
        GA_CASES[18]: a19, GA_CASES[19]: a20, GA_CASES[20]: a21, GA_CASES[21]: a22,
        GA_CASES[22]: a23, GA_CASES[23]: a24, GA_CASES[24]: a25, GA_CASES[25]: a26,
        GA_CASES[26]: a27,
        GA_CASES[11]: a12,      # last: it sweeps every message the others provoked
    }
    results = {}
    for cid, fn in cases.items():
        try:
            results[cid] = fn()
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            results[cid] = (["case raised %s: %s" % (type(exc).__name__, ga_redact(str(exc))[:200])], [])
    for cid in GA_CASES:
        problems, detail = results[cid]
        if problems is None:
            suite.record(GA, cid, [], status=H.INFO, detail=detail)
        else:
            suite.record(GA, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group B: front
# ---------------------------------------------------------------------------

GB_CASES = (
    "no Authorization -> 401",           # B1
    "wrong token -> 401",                # B2
    "x-api-key alone -> 401",            # B3
    "foreign Host -> 403",               # B4
    "M1 query ok, other query 404",      # B5
    "Origin foreign 403, else ok",       # B6
    "token in the query -> 400",         # B7
    "token in x-api-key -> 400",         # B8
    "chunked body -> 411",               # B9
    "over-limit body -> 413",            # B10
    "OPTIONS 405, no CORS header",       # B11
    "duplicate Content-Type -> 415",     # B12
    "malformed header line -> 400",      # B13
    "header trickle cut at deadline",    # B14
    "ready file 0600 with the port",     # B15
    "busy port -> exit 2, one line",     # B16
    "every error body an envelope",      # B17
    "deep JSON nesting -> 400",          # B18
    "two Authorization -> 401",          # B19
    "connection cap -> raw 503",         # B20
    "stdlib refusals are envelopes",     # B21
    "body read only after auth",         # B22
    "body trickle cut at deadline",      # B23
    "NaN/Infinity, max_tokens -> 400",   # B24
    "GET /v1/models list shape",         # B25
    "GET /v1/models/{id} hit, 404",      # B26
    "models: auth, Host, Origin",        # B27
    "models: query keys, methods",       # B28
    "models: no routes -> data []",      # B29
)

# "Accepted" in a B case means "not a front refusal" (plan section 8): the status
# is not one of these and is not the misplaced-token 400 (told apart by its
# message).  The status behind the front moves across steps (501 from the Step 5
# stub, 400/501 in Steps 7-8, 502/200 later), so no B case pins it.
GB_FRONT_REFUSALS = frozenset({401, 403, 404, 405, 411, 413, 415})
GB_MISPLACED = "the router token must appear only in the Authorization header"

# KD-14 _STATUS_TO_TYPE, for the statuses the front answers (the spec, typed).
GB_TYPES = {400: "invalid_request_error", 401: "authentication_error",
            403: "permission_error", 404: "not_found_error",
            405: "invalid_request_error", 411: "invalid_request_error",
            413: "request_too_large", 414: "invalid_request_error",
            415: "invalid_request_error", 501: "api_error"}

GB_M1_QUERY = "beta=true"        # the query M1 records; this default stands until G-M1 is measured
GB_CHUNKED_DECODED = False       # G-M1: True iff Step 5 implemented the chunked decoder instead of 411
GB_OK_ORIGIN = "http://localhost:5173"
GB_EVIL_ORIGIN = "http://tf-evil.example"
GB_BODY_LIMIT = 16 << 20         # --body-limit of the shared router: above B22's 10 MiB
GB_UNREAD_BODY_LEN = 10485760    # B22: the declared Content-Length whose body is withheld
GB_UNREAD_ANSWER_S = 1.0         # B22: the 401 must arrive within this, body never sent
GB_BODY_WAIT_S = 1.0             # B22: with a valid bearer, no answer while the body is withheld
GB_TRICKLE_STEP_S = 1.0          # B14: one header byte per this, far under the per-recv timeout
GB_TRICKLE_SLACK_S = 3.0         # B14: the cut must land within _HTTP_HEADER_TIMEOUT_S + this
GB_PARTIAL_LINE = b"POST /v1/messages HTTP/1.1\r\n"   # B14/B20: a request line, then nothing
GB_DEEP_DEPTH = 100000           # B18: array nesting far past the interpreter's recursion limit
GB_LONG_LINE = 65536 + 64        # B21: the stdlib reads at most 65537 bytes of a request line
GB_CAP_RETRY_S = 5.0             # B20: the slot must come back within this after the release
GB_LINE_SENTINEL = "tf-line-SENTINEL-" + secrets.token_hex(8)   # B21: never echoed
GB_TRICKLE_BODY_LEN = 1 << 20    # B23: the declared body, sent one byte per GB_TRICKLE_STEP_S
# B24: (label, raw JSON text of one extra top-level field) -- each a non-finite number.
GB_NONFINITE = (("NaN", '"temperature": NaN'), ("Infinity", '"top_p": Infinity'),
                ("-Infinity", '"temperature": -Infinity'), ("1e999", '"temperature": 1e999'))
# B24: a 2xx upstream answer carrying NaN; it must not reach the client.
GB_NAN_ANSWER = (b'{"id": "msg_tf_nan", "type": "message", "role": "assistant", "model": "tf-model", '
                 b'"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", '
                 b'"stop_sequence": null, "usage": {"input_tokens": NaN, "output_tokens": 1}}')

# (label, status, body) of every front refusal group B provoked; B17 sweeps it.
GB_ERROR_BODIES = []

GB_PEER_ANSWER = {"id": "msg_tf_front", "type": "message", "role": "assistant",
                  "model": "tf-model", "content": [{"type": "text", "text": "ok"}],
                  "stop_reason": "end_turn", "stop_sequence": None,
                  "usage": {"input_tokens": 1, "output_tokens": 1}}


def gb_body():
    """A minimal valid Messages request (a fresh copy per call)."""
    return {"model": "claude-sonnet-4-5", "max_tokens": 16,
            "messages": [{"role": "user", "content": "tf ping"}]}


def gb_config(peer_url):
    """One passthrough backend at the loopback peer, one route plus the default."""
    return {
        "auth_token": TF_ROUTER_TOKEN,
        "backends": {
            "tfpeer": {"kind": "passthrough", "base_url": peer_url,
                       "api_key": TF_KEY_PASS, "auth_header": "x-api-key"},
        },
        "routes": {"claude-sonnet-4-5": {"backend": "tfpeer", "model": "tf-model"}},
        "default": {"backend": "tfpeer", "model": "tf-model"},
    }


# B25-B29: GET /v1/models and /v1/models/{id}, answered from the config, never upstream.
GB_MODELS_PATH = "/v1/models"
# (route key, display_name or None) in config order; B25 expects exactly these ids, in this order.
GB_MODEL_ROUTES = (("claude-sonnet-4-5", None), ("tf-b-second", "TF Second Model"), ("tf b/third", None))
GB_DEFAULT_DISPLAY = "TF Default Display Name"     # set on the default route; never listed
GB_MODELS_QUERY = "limit=20&after_id=tf-a&before_id=tf-b&beta=true"   # every allowed key, values ignored
GB_LIST_KEYS = frozenset({"data", "has_more", "first_id", "last_id"})
GB_ENTRY_KEYS = frozenset({"type", "id", "display_name", "created_at"})
GB_CREATED_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
GB_CREATED_SLACK_S = 2.0       # created_at (second precision) must lie in [launch - this, now + this]


def gb_models_config(peer_url, routes=GB_MODEL_ROUTES):
    """gb_config with *routes* (each display_name set when given) and a named default."""
    cfg = gb_config(peer_url)
    cfg["routes"] = {}
    for key, display in routes:
        entry = {"backend": "tfpeer", "model": "tf-model"}
        if display is not None:
            entry["display_name"] = display
        cfg["routes"][key] = entry
    cfg["default"] = {"backend": "tfpeer", "model": "tf-model", "display_name": GB_DEFAULT_DISPLAY}
    return cfg


def gb_get(client, path, pairs=None, **kw):
    """(status, headers, body) of one GET on a raw socket: by default the valid
    bearer and Connection: close, no Content-Length (*kw* goes to gb_request)."""
    if pairs is None:
        pairs = [("Authorization", "Bearer " + client.token), ("Connection", "close")]
    kw.setdefault("length", None)
    status, hdrs, body, _first, _closed = gb_raw(client, gb_request(client, method="GET", path=path,
                                                                    pairs=pairs, **kw))
    return status, hdrs, body


def gb_json_200(label, status, hdrs, body):
    """(object, problems): a 200 application/json answer, parsed."""
    if status != 200:
        _t, msg, _why = gb_envelope(body) if status is not None else (None, None, None)
        return None, ["%s: status %r%s, expected 200"
                      % (label, status, (" (%s)" % ga_redact(msg)[:120]) if msg else "")]
    problems = []
    if (hdrs.get("content-type") or "").split(";")[0].strip() != "application/json":
        problems.append("%s: Content-Type %r, expected application/json" % (label, hdrs.get("content-type")))
    try:
        return json.loads(body.decode("utf-8")), problems
    except (UnicodeDecodeError, ValueError):
        return None, problems + ["%s: the body is not JSON: %r" % (label, ga_redact(gb_text(body))[:80])]


def gb_check_entry(label, entry, want_id, want_display, launched):
    """Problems unless *entry* is exactly {type: model, id, display_name, created_at}
    with a second-precision RFC 3339 UTC created_at between the router's launch and now."""
    if not isinstance(entry, dict) or set(entry) != GB_ENTRY_KEYS:
        return ["%s: entry %r, expected exactly the keys %s"
                % (label, ga_redact(str(entry))[:160], sorted(GB_ENTRY_KEYS))]
    problems = []
    for key, want in (("type", "model"), ("id", want_id), ("display_name", want_display)):
        if entry[key] != want:
            problems.append("%s: %s %r, expected %r" % (label, key, entry[key], want))
    created = entry["created_at"]
    if not isinstance(created, str) or not GB_CREATED_RE.match(created):
        problems.append("%s: created_at %r is not YYYY-MM-DDTHH:MM:SSZ" % (label, created))
    else:
        stamp = calendar.timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))
        if not launched - GB_CREATED_SLACK_S <= stamp <= time.time() + GB_CREATED_SLACK_S:
            problems.append("%s: created_at %s is not the router's start time (launched %.0f)"
                            % (label, created, launched))
    return problems


def gb_check_list(label, obj, routes, launched):
    """Problems unless *obj* is the Anthropic model list of *routes*: one entry per
    route key in config order (display_name falling back to the key), has_more
    false, first_id / last_id the ends (null when empty), one shared created_at."""
    if not isinstance(obj, dict) or set(obj) != GB_LIST_KEYS:
        return ["%s: answer %r, expected exactly the keys %s"
                % (label, ga_redact(str(obj))[:160], sorted(GB_LIST_KEYS))]
    data = obj["data"]
    if not isinstance(data, list):
        return ["%s: data is %s, expected a list" % (label, type(data).__name__)]
    problems = []
    want_ids = [key for key, _d in routes]
    ids = [entry.get("id") if isinstance(entry, dict) else None for entry in data]
    if ids != want_ids:
        problems.append("%s: ids %r, expected %r (config order, the default never listed)"
                        % (label, ids, want_ids))
    if obj["has_more"] is not False:
        problems.append("%s: has_more %r, expected false" % (label, obj["has_more"]))
    for key, want in (("first_id", want_ids[0] if want_ids else None),
                      ("last_id", want_ids[-1] if want_ids else None)):
        if obj[key] != want:
            problems.append("%s: %s %r, expected %r" % (label, key, obj[key], want))
    for entry, (key, display) in zip(data, routes):
        problems += gb_check_entry("%s [%s]" % (label, key), entry, key, display or key, launched)
    stamps = {entry.get("created_at") for entry in data if isinstance(entry, dict)}
    if len(stamps) > 1:
        problems.append("%s: %d different created_at values, expected one" % (label, len(stamps)))
    return problems


def gb_not_found(label, status, body, absent):
    """Problems unless the answer is a 404 not_found_error envelope that does not echo *absent*."""
    if status != 404:
        return ["%s: status %r, expected 404" % (label, status)]
    GB_ERROR_BODIES.append((label, status, body))
    etype, _msg, why = gb_envelope(body)
    if why:
        return ["%s: %s" % (label, why)]
    problems = []
    if etype != "not_found_error":
        problems.append("%s: error.type %r, expected 'not_found_error'" % (label, etype))
    if absent and absent in gb_text(body):
        problems.append("%s: the answer echoes the requested id" % label)
    return problems


def gb_not_ready(proc):
    """One problem line for a router that never wrote its ready file."""
    lines = [ln for ln in proc.stderr_text().splitlines() if ln.strip()]
    last = ga_redact(lines[-1])[:200] if lines else "(no stderr)"
    return "the router never became ready (exit %r): %s" % (proc.proc.poll(), last)


def socket_closed_by_peer(sock, deadline):
    """(closed, data): read *sock* until the server ends it (EOF or reset) or
    the monotonic *deadline* passes; *closed* is False on the deadline
    (copied from tests/test_mcp_proxy.py)."""
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


def gb_request(client, method="POST", path=None, pairs=(), body=b"", length=True,
               version="HTTP/1.1", host=True):
    """Raw request bytes: the request line, Host (unless *host* is False), *pairs*
    as-is (a name may repeat), Content-Length (len(body) when *length* is True,
    the given value otherwise, none when None), then *body*."""
    lines = ["%s %s %s" % (method, path or client.path, version)]
    if host:
        lines.append("Host: %s:%d" % (client.host, client.port))
    lines += ["%s: %s" % kv for kv in pairs]
    if length is True:
        lines.append("Content-Length: %d" % len(body))
    elif length is not None:
        lines.append("Content-Length: %s" % length)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def gb_parse_raw(data):
    """(status, headers, body, status line) of a raw HTTP response."""
    head, _sep, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1", "replace").split("\r\n")
    first = lines[0] if lines else ""
    parts = first.split(" ", 2)
    status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return status, headers, rest, first


def gb_raw(client, request, timeout=RAW_TIMEOUT_S):
    """(status, headers, body, status line, closed) of *request* sent on a raw
    socket, read until the server closes or *timeout* passes."""
    with socket.create_connection((client.host, client.port), timeout=timeout) as sock:
        try:
            sock.sendall(request)
        except OSError:
            pass                # the server may refuse and close mid-send; read what it said
        closed, data = socket_closed_by_peer(sock, time.monotonic() + timeout)
    status, headers, body, first = gb_parse_raw(data)
    RESPONSE_BODIES.append(("raw %s" % first[:40], body))
    return status, headers, body, first, closed


def gb_auth_pairs(client, content_type="application/json"):
    """Valid bearer, Content-Type and Connection: close as raw header pairs."""
    return [("Authorization", "Bearer " + client.token), ("Content-Type", content_type),
            ("Connection", "close")]


def gb_text(body):
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")


def gb_envelope(body):
    """(error type, message, problem): *body* must be {"type":"error","error":
    {"type": str, "message": str}}; problem is None when it is."""
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, None, "body is not JSON: %r" % ga_redact(gb_text(body))[:80]
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(obj, dict) or obj.get("type") != "error" or not isinstance(err, dict):
        return None, None, "body is not an Anthropic error envelope: %r" \
            % ga_redact(gb_text(body))[:120]
    etype, msg = err.get("type"), err.get("message")
    if not isinstance(etype, str) or not isinstance(msg, str):
        return None, None, "error.type / error.message are not both strings: %r" \
            % ga_redact(gb_text(body))[:120]
    return etype, msg, None


def gb_refused(label, status, headers, body, want, needle=None, extra_headers=()):
    """Problems unless the answer is a *want* front refusal: an Anthropic envelope
    of KD-14's type (holding *needle*), Connection: close, every *extra_headers*
    pair, and no sentinel.  An error body is recorded for B17's sweep."""
    if isinstance(status, int) and status >= 400:
        GB_ERROR_BODIES.append((label, status, body))
    if status != want:
        shown = ""
        if status is not None:
            _t, msg, _why = gb_envelope(body)
            shown = " (%s)" % ga_redact(msg)[:120] if msg else ""
        return ["%s: status %r%s, expected %d" % (label, status, shown, want)]
    problems = []
    etype, msg, why = gb_envelope(body)
    if why:
        problems.append("%s: %s" % (label, why))
    elif etype != GB_TYPES[want]:
        problems.append("%s: error.type %r, expected %r" % (label, etype, GB_TYPES[want]))
    if needle and (msg is None or needle not in msg):
        problems.append("%s: error.message %r does not hold %r"
                        % (label, ga_redact(msg or "")[:120], needle))
    if (headers.get("connection") or "").lower() != "close":
        problems.append("%s: no Connection: close (got %r)" % (label, headers.get("connection")))
    for name, value in extra_headers:
        if headers.get(name.lower()) != value:
            problems.append("%s: header %s %r, expected %r"
                            % (label, name, headers.get(name.lower()), value))
    if any(value in gb_text(body) for value in SENTINELS):
        problems.append("%s: a sentinel occurs in the answer body" % label)
    return problems


def gb_accepted(label, status, body):
    """Problems unless the answer is not a front refusal (the rule at the head of group B)."""
    if status is None:
        return ["%s: no answer" % label]
    if status in GB_FRONT_REFUSALS:
        _t, msg, _why = gb_envelope(body)
        return ["%s: status %d, a front refusal%s" % (
            label, status, (" (%s)" % ga_redact(msg)[:120]) if msg else "")]
    if status == 400 and GB_MISPLACED in gb_text(body):
        return ["%s: the misplaced-token 400" % label]
    return []


def gb_control(client, label="control: a valid request"):
    """A plain valid POST must not be a front refusal."""
    status, _hdrs, body = client.post(obj=gb_body())
    return gb_accepted(label, status, body)


def gb_body_trickle(client, bound, out):
    """B23, on its own thread: a valid bearer and Content-Length GB_TRICKLE_BODY_LEN,
    then one body byte per GB_TRICKLE_STEP_S (far under the per-recv timeout)
    until the router closes or *bound* + GB_TRICKLE_SLACK_S passes.  *out* gets
    {"closed", "took", "sent", "data"} (and "error" on a connect failure)."""
    sent, closed, data = 0, False, b""
    t0 = time.monotonic()
    try:
        with socket.create_connection((client.host, client.port), timeout=RAW_TIMEOUT_S) as sock:
            sock.sendall(gb_request(client, pairs=gb_auth_pairs(client), length=GB_TRICKLE_BODY_LEN))
            while time.monotonic() - t0 < bound + GB_TRICKLE_SLACK_S:
                try:
                    sock.sendall(b" ")
                    sent += 1
                except OSError:
                    closed = True
                    break
                closed, data = socket_closed_by_peer(sock, time.monotonic() + GB_TRICKLE_STEP_S)
                if closed:
                    break
    except OSError as exc:
        out["error"] = type(exc).__name__
    out.update(closed=closed, took=time.monotonic() - t0, sent=sent, data=data)


def group_b(suite, fixture_root):
    """B. the HTTP front: bearer auth, Host/Origin, the misplaced token, framing
    refusals, the stdlib's own refusals, the ready file, bind-first and the cap.

    One live router (--allowed-origin, --body-limit 16 MiB) in front of one
    loopback passthrough peer serves every case but B16 (a busy port), B20
    (its own router with --max-connections 1), B25-B28 (a second router on the
    same peer whose routes carry display_name) and B29 (a router with no
    routes).  The models cases also assert that the peer is never contacted.
    Every front refusal must be an
    Anthropic envelope of KD-14's type with Connection: close; "accepted" means
    "not a front refusal" and never pins the status behind the front.  A router
    that never becomes ready fails every case (red), never skips it.
    """
    del GB_ERROR_BODIES[:]
    try:
        mod = H.load_module_from_path("ph_llm_router_b", SERVER)
        mod_why = None
    except Exception as exc:  # noqa: BLE001 -- only the module-bound case (B14) needs it
        mod = None
        mod_why = "cannot import %s: %s" % (os.path.relpath(SERVER, H.REPO_ROOT),
                                            type(exc).__name__)
    sandbox = new_sandbox(fixture_root, "front")
    peer = ScriptedPeer(script=[("respond_json", 200, GB_PEER_ANSWER)])
    proc = None
    models_proc = None
    results = {}
    try:
        cfgpath = write_config(sandbox, gb_config(peer.url()))
        proc = RouterProc(sandbox, cfgpath, label="front",
                          extra_argv=("--allowed-origin", GB_OK_ORIGIN,
                                      "--body-limit", GB_BODY_LIMIT))
        port = proc.wait_ready()
        client = proc.client() if port is not None else None
        why = gb_not_ready(proc) if port is None else None
        # B25-B28: a second router on the same peer whose routes carry display_name.
        models_box = new_sandbox(fixture_root, "models")
        launched = time.time()
        models_proc = RouterProc(models_box, write_config(models_box, gb_models_config(peer.url())),
                                 label="models", extra_argv=("--allowed-origin", GB_OK_ORIGIN))
        mclient = models_proc.client() if models_proc.wait_ready() is not None else None
        mwhy = gb_not_ready(models_proc) if mclient is None else None
        # B23 runs on its own thread from here, so its deadline overlaps the other cases.
        body_bound = getattr(mod, "_HTTP_BODY_TIMEOUT_S", None) if mod is not None else None
        trickle, trickle_thread = {}, None
        if client is not None and isinstance(body_bound, (int, float)):
            trickle_thread = threading.Thread(target=gb_body_trickle,
                                              args=(client, body_bound, trickle), daemon=True)
            trickle_thread.start()

        def live(fn):
            def case():
                if client is None:
                    return [why], []
                return fn()
            return case

        def live_models(fn):
            def case():
                if mclient is None:
                    return [mwhy], []
                return fn()
            return case

        def peer_untouched(before, label):
            time.sleep(0.2)
            if len(peer.requests) != before:
                return ["%s: the peer was contacted (%d request(s))"
                        % (label, len(peer.requests) - before)]
            return []

        def b23():
            if mod is None:
                return [mod_why], []
            if not isinstance(body_bound, (int, float)):
                return ["the module defines no _HTTP_BODY_TIMEOUT_S"], []
            trickle_thread.join(body_bound + GB_TRICKLE_SLACK_S + RAW_TIMEOUT_S + GB_TRICKLE_STEP_S)
            problems = []
            if trickle_thread.is_alive() or "took" not in trickle:
                return ["the body trickle did not finish"], []
            if "error" in trickle:
                problems.append("the body trickle could not connect: %s" % trickle["error"])
            elif not trickle["closed"]:
                problems.append("a body trickle (1 byte / %gs) still open after %.1fs (total "
                                "body deadline %gs)" % (GB_TRICKLE_STEP_S, trickle["took"], body_bound))
            if trickle.get("data"):
                problems.append("the cut sent %d byte(s); expected a close without an answer"
                                % len(trickle["data"]))
            return problems, ["trickle     : %d body byte(s) of %d declared, closed %s after %.1fs "
                              "(deadline %gs from the module, slack %gs)"
                              % (trickle["sent"], GB_TRICKLE_BODY_LEN, trickle["closed"],
                                 trickle["took"], body_bound, GB_TRICKLE_SLACK_S)]

        def b24():
            if mod is None:
                return [mod_why], []
            problems = []
            base = json.dumps(gb_body())
            for label, field in GB_NONFINITE:
                before = len(peer.requests)
                status, hdrs, body = client.post(raw_body=(base[:-1] + ", " + field + "}").encode("ascii"))
                problems += gb_refused("body with %s" % label, status, hdrs, body, 400)
                if len(peer.requests) != before:
                    problems.append("body with %s: the peer was contacted" % label)
            limit = ga_module_value(mod, "_RT_MAX_TOKENS_LIMIT", problems)
            if isinstance(limit, int):
                for value in (limit, limit + 1):
                    obj = gb_body()
                    obj["max_tokens"] = value
                    status, _h, body = client.post(obj=obj)
                    if value == limit:
                        problems += gb_accepted("control: max_tokens %d" % value, status, body)
                        continue
                    etype, msg, bad = gb_envelope(body)
                    if status != 400 or bad or etype != GB_TYPES[400] or "max_tokens" not in msg:
                        problems.append("max_tokens %d: status %r, %r; expected a 400 naming "
                                        "max_tokens" % (value, status, bad or msg))
            saved = peer.script
            peer.script = [("respond_json", 200, GB_NAN_ANSWER)]
            try:
                status, _h, body = client.post(obj=gb_body())
            finally:
                peer.script = saved
            if status != 502:
                problems.append("a 2xx upstream body with NaN: status %r, expected 502" % status)
            if b"NaN" in body:
                problems.append("a 2xx upstream body with NaN: NaN reached the client")
            problems += gb_control(client)
            return problems, ["refused 400 : a body field %s (no peer contact)"
                              % ", ".join(lb for lb, _f in GB_NONFINITE),
                              "max_tokens  : _RT_MAX_TOKENS_LIMIT = %r accepted, +1 refused" % limit,
                              "upstream    : a 2xx answer with NaN -> 502"]

        def b1():
            before = len(peer.requests)
            status, hdrs, body = client.post(obj=gb_body(), headers={"Authorization": None})
            problems = gb_refused("no Authorization", status, hdrs, body, 401,
                                  extra_headers=(("WWW-Authenticate", "Bearer"),))
            time.sleep(0.2)
            if len(peer.requests) != before:
                problems.append("the peer was contacted for an unauthenticated request")
            problems += gb_control(client)
            return problems, ["refused     : no Authorization -> 401 + WWW-Authenticate: Bearer",
                              "peer        : %d request(s) before and after" % before]

        def b2():
            problems = []
            for label, value in (
                    ("wrong token", "Bearer tf-wrong-token-" + secrets.token_hex(16)),
                    ("token + 1 char", "Bearer " + TF_ROUTER_TOKEN + "x"),
                    ("Basic scheme", "Basic " + TF_ROUTER_TOKEN)):
                status, hdrs, body = client.post(obj=gb_body(), headers={"Authorization": value})
                problems += gb_refused(label, status, hdrs, body, 401,
                                       extra_headers=(("WWW-Authenticate", "Bearer"),))
            problems += gb_control(client)
            return problems, ["refused     : a wrong token, the token plus one char, "
                              "the token under Basic"]

        def b3():
            status, hdrs, body = client.post(obj=gb_body(), headers={
                "Authorization": None, "x-api-key": TF_ROUTER_TOKEN})
            problems = gb_refused("x-api-key = the token, no Authorization", status, hdrs,
                                  body, 401, extra_headers=(("WWW-Authenticate", "Bearer"),))
            problems += gb_control(client)
            return problems, ["refused     : x-api-key carries the router token; it is "
                              "never an auth source"]

        def b4():
            problems = []
            status, hdrs, body = client.post(obj=gb_body(),
                                             headers={"Host": "evil:%d" % client.port})
            problems += gb_refused("Host evil:port", status, hdrs, body, 403)
            status, _h, body = client.post(obj=gb_body(),
                                           headers={"Host": "localhost:%d" % client.port})
            problems += gb_accepted("control: Host localhost:port", status, body)
            problems += gb_control(client, "control: Host 127.0.0.1:port")
            return problems, ["refused     : Host evil:%d" % client.port,
                              "accepted    : localhost:%d, 127.0.0.1:%d"
                              % (client.port, client.port)]

        def b5():
            problems = []
            for path in (MESSAGES_PATH, "%s?%s" % (MESSAGES_PATH, GB_M1_QUERY),
                         MESSAGES_PATH + "/count_tokens"):
                status, _h, body = client.post(path=path, obj=gb_body())
                problems += gb_accepted("path %s" % path, status, body)
            for path in (MESSAGES_PATH + "?tf_other=1", "/v1/tf-nope"):
                status, hdrs, body = client.post(path=path, obj=gb_body())
                problems += gb_refused("path %s" % path, status, hdrs, body, 404)
            return problems, ["accepted    : /v1/messages, ?%s (default until G-M1), count_tokens"
                              % GB_M1_QUERY,
                              "refused 404 : ?tf_other=1, /v1/tf-nope"]

        def b6():
            problems = []
            for label, origin in (("Origin absent", None), ("Origin " + GB_OK_ORIGIN, GB_OK_ORIGIN)):
                status, _h, body = client.post(obj=gb_body(), headers={"Origin": origin})
                problems += gb_accepted(label, status, body)
            for origin in (GB_EVIL_ORIGIN, GB_OK_ORIGIN + "/"):
                status, hdrs, body = client.post(obj=gb_body(), headers={"Origin": origin})
                problems += gb_refused("Origin " + origin, status, hdrs, body, 403)
            return problems, ["accepted    : absent, %s (--allowed-origin)" % GB_OK_ORIGIN,
                              "refused 403 : %s, %s/ (exact match)" % (GB_EVIL_ORIGIN, GB_OK_ORIGIN)]

        def b7():
            problems = []
            encoded = "".join("%%%02X" % b for b in TF_ROUTER_TOKEN.encode("ascii"))
            for label, path in (
                    ("token as the query", "%s?tf_key=%s" % (MESSAGES_PATH, TF_ROUTER_TOKEN)),
                    ("token after the M1 query", "%s?%s&tf_key=%s"
                     % (MESSAGES_PATH, GB_M1_QUERY, TF_ROUTER_TOKEN)),
                    ("token percent-encoded", "%s?tf_key=%s" % (MESSAGES_PATH, encoded))):
                status, hdrs, body = client.post(path=path, obj=gb_body())
                problems += gb_refused(label, status, hdrs, body, 400, needle=GB_MISPLACED)
            problems += gb_control(client)
            return problems, ["refused 400 : the token in the query, after ?%s, percent-encoded"
                              % GB_M1_QUERY,
                              "message     : %r" % GB_MISPLACED]

        def b8():
            problems = []
            for label, extra in (("x-api-key = the token", {"x-api-key": TF_ROUTER_TOKEN}),
                                 ("X-Tf-Echo holds the token",
                                  {"X-Tf-Echo": "tf " + TF_ROUTER_TOKEN})):
                status, hdrs, body = client.post(obj=gb_body(), headers=extra)
                problems += gb_refused(label, status, hdrs, body, 400, needle=GB_MISPLACED)
            status, _h, body = client.post(obj=gb_body(), headers={"x-api-key": TF_CLIENT_XKEY})
            problems += gb_accepted("control: x-api-key = a client key", status, body)
            return problems, ["refused 400 : the token in x-api-key / another header, "
                              "beside a valid bearer",
                              "accepted    : x-api-key with a client key (ignored)"]

        def b9():
            problems = []
            payload = json.dumps(gb_body()).encode("utf-8")
            chunked = b"%x\r\n" % len(payload) + payload + b"\r\n0\r\n\r\n"
            te = [("Transfer-Encoding", "chunked")]
            status, hdrs, body, _first, _closed = gb_raw(client, gb_request(
                client, pairs=gb_auth_pairs(client) + te, body=chunked, length=None))
            if GB_CHUNKED_DECODED:
                problems += gb_accepted("chunked body (G-M1 decoder)", status, body)
            else:
                problems += gb_refused("chunked body", status, hdrs, body, 411)
                status, hdrs, body, _first, _closed = gb_raw(client, gb_request(
                    client, pairs=gb_auth_pairs(client) + te, body=chunked, length=len(chunked)))
                problems += gb_refused("chunked + Content-Length", status, hdrs, body, 411)
            return problems, ["G-M1        : GB_CHUNKED_DECODED = %s (M1 not measured: 411 kept)"
                              % GB_CHUNKED_DECODED]

        def b10():
            status, hdrs, body, _first, _closed = gb_raw(client, gb_request(
                client, pairs=gb_auth_pairs(client), length=GB_BODY_LIMIT + 1))
            problems = gb_refused("Content-Length limit+1, body withheld", status, hdrs, body, 413)
            return problems, ["framing     : Content-Length %d over --body-limit %d, no body sent"
                              % (GB_BODY_LIMIT + 1, GB_BODY_LIMIT)]

        def b11():
            problems = []
            preflight = [("Origin", GB_OK_ORIGIN), ("Access-Control-Request-Method", "POST"),
                         ("Connection", "close")]
            got = []
            for label, pairs, want in (
                    ("OPTIONS, valid bearer", [("Authorization", "Bearer " + client.token)]
                     + preflight, 405),
                    ("OPTIONS, browser preflight", preflight, None)):
                status, hdrs, body, _first, _closed = gb_raw(client, gb_request(
                    client, method="OPTIONS", pairs=pairs, length=None))
                got.append(status)
                if want is not None:
                    problems += gb_refused(label, status, hdrs, body, want,
                                           extra_headers=(("Allow", "POST"),))
                elif status not in GB_FRONT_REFUSALS:
                    problems.append("%s: status %r, expected a front refusal" % (label, status))
                cors = sorted(k for k in hdrs if k.startswith("access-control-"))
                if cors:
                    problems.append("%s: CORS header(s) in the answer: %s" % (label, cors))
            return problems, ["answers     : bearer %r, preflight %r; no Access-Control-* in either"
                              % tuple(got)]

        def b12():
            problems = []
            auth = [("Authorization", "Bearer " + client.token), ("Connection", "close")]
            body = json.dumps(gb_body()).encode("utf-8")
            for label, ctypes, want in (
                    ("two Content-Type", ["application/json", "application/json"], 415),
                    ("Content-Type text/plain", ["text/plain"], 415),
                    ("control: one Content-Type", ["application/json"], None)):
                pairs = auth + [("Content-Type", c) for c in ctypes]
                status, hdrs, rbody, _first, _closed = gb_raw(
                    client, gb_request(client, pairs=pairs, body=body))
                if want is None:
                    problems += gb_accepted(label, status, rbody)
                else:
                    problems += gb_refused(label, status, hdrs, rbody, want)
            return problems, ["refused 415 : application/json twice, text/plain",
                              "accepted    : one application/json"]

        def b13():
            problems = []
            body = json.dumps(gb_body()).encode("utf-8")
            for label, extra, want in (
                    ("'X y: z'", [("X y", "z")], 400),
                    ("'Transfer-Encoding : chunked'", [("Transfer-Encoding ", "chunked")], 400),
                    ("control: well-formed", [], None)):
                status, hdrs, rbody, _first, _closed = gb_raw(client, gb_request(
                    client, pairs=gb_auth_pairs(client) + extra, body=body))
                if want is None:
                    problems += gb_accepted(label, status, rbody)
                else:
                    problems += gb_refused(label, status, hdrs, rbody, want)
            # V29: a forwardable header whose value carries CR or LF (an obs-fold
            # continuation, or a bare CR) is refused before any upstream request.
            for label, value in (("obs-fold anthropic-version", "2023-06-01\r\n X-Tf-Fold: v"),
                                 ("bare CR in anthropic-version", "2023-06-01\r X-Tf-Fold: v")):
                before = len(peer.requests)
                status, hdrs, rbody, _first, _closed = gb_raw(client, gb_request(
                    client, pairs=gb_auth_pairs(client) + [("anthropic-version", value)], body=body))
                # An inbound-validation 400 (after the body), so no Connection: close is pinned.
                etype, msg, why = gb_envelope(rbody) if status == 400 else (None, None, None)
                if status != 400:
                    problems.append("%s: status %r, expected 400" % (label, status))
                elif why:
                    problems.append("%s: %s" % (label, why))
                elif etype != GB_TYPES[400] or "anthropic-version" not in msg:
                    problems.append("%s: envelope %r / %r does not name the header"
                                    % (label, etype, ga_redact(msg)[:120]))
                time.sleep(0.2)
                if len(peer.requests) != before:
                    problems.append("%s: the peer was contacted" % label)
            return problems, ["refused 400 : a name with a space, a name with a trailing space "
                              "(headers.defects)",
                              "refused 400 : anthropic-version with an obs-fold, with a bare CR "
                              "(V29), the peer never contacted"]

        def b14():
            if mod is None:
                return [mod_why], []
            problems = []
            # V3: a connection that has not authenticated yet gets the shorter
            # pre-auth deadline; an authenticated keep-alive one keeps the header one.
            bound = ga_module_value(mod, "_HTTP_PREAUTH_TIMEOUT_S", problems)
            header_bound = ga_module_value(mod, "_HTTP_HEADER_TIMEOUT_S", problems)
            if not isinstance(bound, (int, float)) or not isinstance(header_bound, (int, float)):
                return problems, []
            if bound >= header_bound:
                problems.append("_HTTP_PREAUTH_TIMEOUT_S %g is not under _HTTP_HEADER_TIMEOUT_S %g"
                                % (bound, header_bound))
            sent, closed = 0, False
            sock = socket.create_connection((client.host, client.port), timeout=RAW_TIMEOUT_S)
            t0 = time.monotonic()
            try:
                sock.sendall(GB_PARTIAL_LINE)
                while time.monotonic() - t0 < bound + GB_TRICKLE_SLACK_S:
                    try:
                        sock.sendall(b"X")
                        sent += 1
                    except OSError:
                        closed = True
                        break
                    closed, _data = socket_closed_by_peer(sock, time.monotonic() + GB_TRICKLE_STEP_S)
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
                                "(total deadline %gs)" % (GB_TRICKLE_STEP_S, took, bound))
            problems += gb_control(client, "after the cut: a valid request")
            # The authenticated keep-alive connection idles past the pre-auth bound
            # and is still served: it is under the header deadline, not the pre-auth one.
            idle = min(bound + 1.0, header_bound - 1.0)
            conn = client.connect()
            try:
                answers = []
                for n in range(2):
                    if n:
                        time.sleep(idle)
                    conn.request("POST", client.path, body=json.dumps(gb_body()).encode("utf-8"),
                                 headers=client.headers())
                    resp = conn.getresponse()
                    data = resp.read()
                    RESPONSE_BODIES.append(("keep-alive POST %d" % n, data))
                    answers.append(resp.status)
                    problems += gb_accepted("keep-alive request %d" % n, resp.status, data)
            except (OSError, http.client.HTTPException) as exc:
                problems.append("an authenticated keep-alive connection idle %.1fs was cut (%s): "
                                "it got the pre-auth deadline" % (idle, type(exc).__name__))
            finally:
                conn.close()
            return problems, ["trickle     : %d byte(s), closed %s after %.1fs (pre-auth deadline "
                              "%gs from the module, slack %gs)"
                              % (sent, closed, took, bound, GB_TRICKLE_SLACK_S),
                              "keep-alive  : authenticated, idle %.1fs, then served (header "
                              "deadline %gs)" % (idle, header_bound)]

        def b15():
            problems = []
            mode = os.stat(proc.ready_path).st_mode & 0o777
            if mode != 0o600:
                problems.append("ready file mode %04o, expected 0600" % mode)
            with open(proc.ready_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or data.get("port") != client.port \
                    or isinstance(data.get("port"), bool):
                problems.append("ready file %r does not carry the listening port %d"
                                % (data, client.port))
            problems += gb_control(client, "control: a request to the ready file's port")
            # V24: a clean shutdown removes the ready file while it holds the router's
            # pid, and leaves one that another writer has since replaced.
            shown = []
            for label, foreign in (("b15-own", False), ("b15-foreign", True)):
                other = RouterProc(sandbox, cfgpath, label=label)
                try:
                    port = other.wait_ready()
                    if port is None:
                        problems.append("%s: the router never wrote its ready file" % label)
                        continue
                    if foreign:
                        with open(other.ready_path, "w", encoding="utf-8") as fh:
                            json.dump({"port": port, "pid": 1}, fh)
                finally:
                    code = other.close()
                left = os.path.lexists(other.ready_path)
                shown.append("%s exit %r, file %s" % (label, code, "kept" if left else "removed"))
                if code != 0:
                    problems.append("%s: the router exited %r after SIGTERM, expected 0" % (label, code))
                if foreign and not left:
                    problems.append("%s: shutdown removed a ready file holding another pid" % label)
                if not foreign and left:
                    problems.append("%s: the ready file survived a clean shutdown (V24)" % label)
            return problems, ["ready file  : mode %04o, port %d" % (mode, client.port),
                              "shutdown    : %s" % "; ".join(shown)]

        def b16():
            holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                holder.bind(("127.0.0.1", 0))
                holder.listen(1)
                busy = holder.getsockname()[1]
                problems = ga_cli_refused(sandbox, cfgpath, "busy-port", ["--port", busy],
                                          ["cannot listen on", "127.0.0.1:%d" % busy])
            finally:
                holder.close()
            return problems, ["busy port   : a held listener on 127.0.0.1:%d, the group's "
                              "valid config" % busy,
                              "expect      : exit 2, one `llm-router: cannot listen on` line, "
                              "no ready file"]

        def b17():
            problems = []
            status, hdrs, body = client.post(path="/v1/tf-sweep", obj=gb_body())
            problems += gb_refused("probe: unknown path", status, hdrs, body, 404)
            status, hdrs, body = client.post(obj=gb_body(), headers={"Content-Type": "text/plain"})
            problems += gb_refused("probe: text/plain", status, hdrs, body, 415)
            seen = set()
            for label, got, rbody in GB_ERROR_BODIES:
                etype, _msg, why = gb_envelope(rbody)
                if why:
                    problems.append("%s (%d): %s" % (label, got, why))
                    continue
                seen.add(got)
                if got in GB_TYPES and etype != GB_TYPES[got]:
                    problems.append("%s (%d): error.type %r, expected %r"
                                    % (label, got, etype, GB_TYPES[got]))
            need = {400, 401, 403, 404, 405, 413, 415} | (set() if GB_CHUNKED_DECODED else {411})
            missing = sorted(need - seen)
            if not GB_ERROR_BODIES:
                problems.append("no front refusal was swept (a vacuous pass is refused)")
            elif missing:
                problems.append("no envelope was swept for status(es) %s" % missing)
            return problems, ["swept       : %d error body(ies), statuses %s"
                              % (len(GB_ERROR_BODIES), sorted(seen))]

        def b18():
            depth = GB_DEEP_DEPTH
            raw = ('{"model": "claude-sonnet-4-5", "max_tokens": 16, "messages": '
                   + "[" * depth + "]" * depth + "}").encode("ascii")
            status, hdrs, body = client.post(raw_body=raw)
            problems = gb_refused("nesting %d deep" % depth, status, hdrs, body, 400)
            problems += gb_control(client, "after: a valid request")
            return problems, ["body        : %d nested arrays (%d bytes)" % (depth, len(raw))]

        def b19():
            problems = []
            body = json.dumps(gb_body()).encode("utf-8")
            bearer = ("Authorization", "Bearer " + client.token)
            rest = [("Content-Type", "application/json"), ("Connection", "close")]
            status, hdrs, rbody, _first, _closed = gb_raw(client, gb_request(
                client, pairs=[bearer, bearer] + rest, body=body))
            problems += gb_refused("two valid Authorization", status, hdrs, rbody, 401,
                                   extra_headers=(("WWW-Authenticate", "Bearer"),))
            status, _h, rbody, _first, _closed = gb_raw(client, gb_request(
                client, pairs=[bearer] + rest, body=body))
            problems += gb_accepted("control: one Authorization", status, rbody)
            return problems, ["refused 401 : the valid bearer twice (P11 single header)"]

        def b20():
            cap_box = new_sandbox(fixture_root, "cap")
            cap = RouterProc(cap_box, cfgpath, extra_argv=("--max-connections", 1), label="cap")
            try:
                if cap.wait_ready() is None:
                    return [gb_not_ready(cap)], []
                cc = cap.client()
                problems = []
                hold = socket.create_connection((cc.host, cc.port), timeout=RAW_TIMEOUT_S)
                try:
                    hold.sendall(GB_PARTIAL_LINE)
                    with socket.create_connection((cc.host, cc.port),
                                                  timeout=RAW_TIMEOUT_S) as second:
                        closed, data = socket_closed_by_peer(second,
                                                             time.monotonic() + RAW_TIMEOUT_S)
                    if not data.startswith(b"HTTP/1.1 503"):
                        problems.append("a second connection while the slot is held got %r, "
                                        "expected a raw 503" % data[:40])
                    if not closed:
                        problems.append("the 503 connection was not closed by the router")
                finally:
                    hold.close()
                deadline = time.monotonic() + GB_CAP_RETRY_S
                attempts, last = 0, []
                while True:
                    attempts += 1
                    try:
                        last = gb_control(cc, "after the release")
                    except (OSError, http.client.HTTPException) as exc:
                        last = ["after the release: %s" % type(exc).__name__]
                    if not last or time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                problems += last
                return problems, ["router      : --max-connections 1",
                                  "held        : one connection sent a request line only",
                                  "after       : %d attempt(s) once it was closed" % attempts]
            finally:
                cap.close()

        def b21():
            problems = []
            line_q = "%s?%s" % (MESSAGES_PATH, GB_LINE_SENTINEL)
            close = [("Connection", "close")]
            for label, request, want, absent in (
                    ("BREW (unknown method)",
                     gb_request(client, method="BREW", path=line_q, pairs=close, length=None),
                     501, "BREW"),
                    ("request line over 64 KiB",
                     gb_request(client, path=line_q + "x" * GB_LONG_LINE, pairs=close, length=None),
                     414, None),
                    ("version FOO/1.1",
                     gb_request(client, path=line_q, pairs=close, length=None, version="FOO/1.1"),
                     400, "FOO")):
                status, hdrs, body, first, _closed = gb_raw(client, request)
                if not first.startswith("HTTP/1.1 %d" % want):
                    problems.append("%s: status line %r, expected 'HTTP/1.1 %d ...'"
                                    % (label, first[:60], want))
                problems += gb_refused(label, status, hdrs, body, want)
                if status == want and \
                        (hdrs.get("content-type") or "").split(";")[0].strip() != "application/json":
                    problems.append("%s: Content-Type %r, expected application/json"
                                    % (label, hdrs.get("content-type")))
                text = gb_text(body)
                if GB_LINE_SENTINEL in text or any(GB_LINE_SENTINEL in v for v in hdrs.values()):
                    problems.append("%s: the request line is echoed (sentinel found)" % label)
                if absent and (absent in text or absent in first):
                    # the stock 501 puts "Unsupported method ('BREW')" in the reason phrase
                    problems.append("%s: the answer quotes %r from the request line" % (label, absent))
            head_req = gb_request(client, method="HEAD",
                                  pairs=[("Authorization", "Bearer " + client.token)] + close,
                                  length=None)
            status, hdrs, body, _first, _closed = gb_raw(client, head_req)
            if status != 405:
                problems.append("HEAD, valid bearer: status %r, expected 405" % status)
            if hdrs.get("allow") != "POST":
                problems.append("HEAD: Allow %r, expected 'POST'" % hdrs.get("allow"))
            if body:
                problems.append("HEAD: %d body byte(s) after the head, expected 0" % len(body))
            return problems, ["refused     : BREW 501, a %d-byte request line 414, FOO/1.1 400 "
                              "(the HTTP/0.9 guard)" % (len(line_q) + GB_LONG_LINE),
                              "each        : HTTP/1.1 status line, envelope, Connection: close, "
                              "request line not echoed",
                              "HEAD        : 405, Allow: POST, zero body bytes"]

        def b22():
            problems = []
            n = GB_UNREAD_BODY_LEN
            head = gb_request(client, pairs=[("Content-Type", "application/json"),
                                             ("Connection", "close")], length=n)
            t0 = time.monotonic()
            with socket.create_connection((client.host, client.port),
                                          timeout=RAW_TIMEOUT_S) as sock:
                sock.sendall(head)
                closed, data = socket_closed_by_peer(sock, t0 + GB_UNREAD_ANSWER_S)
            took = time.monotonic() - t0
            status, hdrs, body, _first = gb_parse_raw(data)
            if status is None:
                problems.append("no Authorization, body withheld: no answer within %gs "
                                "(the router waited for the body)" % GB_UNREAD_ANSWER_S)
            else:
                problems += gb_refused("no Authorization, body withheld", status, hdrs, body,
                                       401, extra_headers=(("WWW-Authenticate", "Bearer"),))
                if not closed:
                    problems.append("no Authorization: the connection stayed open")
            payload = json.dumps(gb_body()).encode("utf-8")
            payload += b" " * (n - len(payload))
            early_status = None
            with socket.create_connection((client.host, client.port),
                                          timeout=HTTP_TIMEOUT_S) as sock:
                sock.sendall(gb_request(client, pairs=gb_auth_pairs(client), length=n))
                early_closed, early = socket_closed_by_peer(sock, time.monotonic() + GB_BODY_WAIT_S)
                if early or early_closed:
                    early_status = gb_parse_raw(early)[0]
                    problems.append("valid bearer: answered (%r) or closed before the body "
                                    "arrived" % early_status)
                else:
                    sock.settimeout(HTTP_TIMEOUT_S)
                    sock.sendall(payload)
                    _closed, data = socket_closed_by_peer(sock, time.monotonic() + HTTP_TIMEOUT_S)
                    status2, _h, body2, _first = gb_parse_raw(data)
                    problems += gb_accepted("valid bearer, body sent after %gs" % GB_BODY_WAIT_S,
                                            status2, body2)
            problems += b22_expect(n, payload)
            return problems, ["unauth      : Content-Length %d, no body: answer %r after %.2fs "
                              "(bound %gs)" % (n, status, took, GB_UNREAD_ANSWER_S),
                              "bearer      : no answer for %gs, then the body, then an answer"
                              % GB_BODY_WAIT_S,
                              "expect      : `Expect: 100-continue` gets no 100 before auth or on "
                              "a 413; with a valid bearer the 100 comes, then the body (V37)"]

        def b22_expect(n, payload):
            """V37: the interim 100 Continue only after auth and the framing checks."""
            problems = []
            expect = [("Expect", "100-continue")]
            for label, pairs, length, want in (
                    ("Expect, no Authorization", [("Content-Type", "application/json"),
                                                  ("Connection", "close")], n, 401),
                    ("Expect, valid bearer, over --body-limit", gb_auth_pairs(client),
                     GB_BODY_LIMIT + 1, 413)):
                with socket.create_connection((client.host, client.port),
                                              timeout=RAW_TIMEOUT_S) as sock:
                    sock.sendall(gb_request(client, pairs=pairs + expect, length=length))
                    _closed, data = socket_closed_by_peer(sock, time.monotonic() + GB_UNREAD_ANSWER_S)
                if data.startswith(b"HTTP/1.1 100"):
                    problems.append("%s: answered 100 Continue first" % label)
                    data = data.partition(b"\r\n\r\n")[2]
                status, hdrs, body, _first = gb_parse_raw(data)
                problems += gb_refused(label, status, hdrs, body, want)
            with socket.create_connection((client.host, client.port),
                                          timeout=HTTP_TIMEOUT_S) as sock:
                sock.sendall(gb_request(client, pairs=gb_auth_pairs(client) + expect, length=n))
                deadline = time.monotonic() + GB_BODY_WAIT_S
                data = b""
                while b"\r\n\r\n" not in data and time.monotonic() < deadline:
                    sock.settimeout(max(0.01, deadline - time.monotonic()))
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk
                interim, _sep, rest = data.partition(b"\r\n\r\n")
                if not interim.startswith(b"HTTP/1.1 100"):
                    problems.append("Expect, valid bearer: %r before the body, expected "
                                    "HTTP/1.1 100 Continue" % interim[:40])
                else:
                    sock.settimeout(HTTP_TIMEOUT_S)
                    sock.sendall(payload)
                    _closed, more = socket_closed_by_peer(sock, time.monotonic() + HTTP_TIMEOUT_S)
                    status, _h, body, _first = gb_parse_raw(rest + more)
                    problems += gb_accepted("Expect, valid bearer, body after the 100", status, body)
            return problems

        def b25():
            problems = []
            before = len(peer.requests)
            for path in (GB_MODELS_PATH, "%s?%s" % (GB_MODELS_PATH, GB_MODELS_QUERY), GB_MODELS_PATH + "?"):
                status, hdrs, body = gb_get(mclient, path)
                obj, bad = gb_json_200("GET " + path, status, hdrs, body)
                problems += bad
                if obj is not None:
                    problems += gb_check_list("GET " + path, obj, GB_MODEL_ROUTES, launched)
                if GB_DEFAULT_DISPLAY in gb_text(body):
                    problems.append("GET %s: the default route's display_name is listed" % path)
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH, length=0)
            problems += gb_json_200("GET with Content-Length: 0", status, hdrs, body)[1]
            problems += peer_untouched(before, "GET /v1/models")
            return problems, ["list        : %s in config order; display_name or the key; has_more "
                              "false; first_id / last_id; one created_at"
                              % ", ".join(repr(k) for k, _d in GB_MODEL_ROUTES),
                              "default     : never listed (its display_name %r absent)" % GB_DEFAULT_DISPLAY,
                              "query       : %s accepted and ignored" % GB_MODELS_QUERY,
                              "peer        : never contacted"]

        def b26():
            problems = []
            before = len(peer.requests)
            for label, path, want_id, want_display in (
                    ("plain id", "/v1/models/claude-sonnet-4-5", "claude-sonnet-4-5", "claude-sonnet-4-5"),
                    ("display_name", "/v1/models/tf-b-second", "tf-b-second", "TF Second Model"),
                    ("percent-encoded space and slash", "/v1/models/tf%20b%2Fthird", "tf b/third",
                     "tf b/third"),
                    ("percent-encoded plain chars", "/v1/models/claude%2Dsonnet%2D4%2D5",
                     "claude-sonnet-4-5", "claude-sonnet-4-5"),
                    ("an allowed query", "/v1/models/tf-b-second?beta=true", "tf-b-second",
                     "TF Second Model")):
                status, hdrs, body = gb_get(mclient, path)
                obj, bad = gb_json_200(label, status, hdrs, body)
                problems += bad
                if obj is not None:
                    problems += gb_check_entry(label, obj, want_id, want_display, launched)
            unknown = "tf-unknown-model-" + secrets.token_hex(6)
            for label, path, absent in (
                    ("unknown id", "/v1/models/" + unknown, unknown),
                    ("the default's name", "/v1/models/%28default%29", "(default)"),
                    ("a key in another case", "/v1/models/CLAUDE-SONNET-4-5", "CLAUDE-SONNET-4-5"),
                    ("an invalid UTF-8 escape", "/v1/models/tf%FFx", "%FF"),
                    ("an empty id", "/v1/models/", None),
                    ("two segments", "/v1/models/claude-sonnet-4-5/x", "claude-sonnet-4-5")):
                status, _h, body = gb_get(mclient, path)
                problems += gb_not_found(label, status, body, absent)
            problems += peer_untouched(before, "GET /v1/models/{id}")
            return problems, ["hit         : a plain id, display_name, %20 and %2F decoded, %2D decoded, "
                              "?beta=true",
                              "404         : an unknown id, (default), another case, %FF, an empty id, "
                              "two segments -- not_found_error, the id never echoed",
                              "peer        : never contacted"]

        def b27():
            problems = []
            before = len(peer.requests)
            close = ("Connection", "close")
            bearer = ("Authorization", "Bearer " + mclient.token)
            for path in (GB_MODELS_PATH, "/v1/models/claude-sonnet-4-5",
                         "/v1/models/tf-unknown-" + secrets.token_hex(4)):
                for label, pairs in (("no Authorization", [close]),
                                     ("wrong token", [("Authorization", "Bearer tf-wrong-token-"
                                                       + secrets.token_hex(16)), close]),
                                     ("x-api-key = the token", [("x-api-key", TF_ROUTER_TOKEN), close])):
                    status, hdrs, body = gb_get(mclient, path, pairs=pairs)
                    problems += gb_refused("GET %s, %s" % (path, label), status, hdrs, body, 401,
                                           extra_headers=(("WWW-Authenticate", "Bearer"),))
                    if b'"data"' in body or b"claude-sonnet-4-5" in body:
                        problems.append("GET %s, %s: the 401 carries model data" % (path, label))
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH, host=False,
                                        pairs=[("Host", "evil:%d" % mclient.port), bearer, close])
            problems += gb_refused("GET /v1/models, Host evil", status, hdrs, body, 403)
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH,
                                        pairs=[bearer, ("Origin", GB_EVIL_ORIGIN), close])
            problems += gb_refused("GET /v1/models, Origin %s" % GB_EVIL_ORIGIN, status, hdrs, body, 403)
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH,
                                        pairs=[bearer, ("Origin", GB_OK_ORIGIN), close])
            problems += gb_json_200("control: Origin %s" % GB_OK_ORIGIN, status, hdrs, body)[1]
            status, hdrs, body = gb_get(mclient, "%s?after_id=%s" % (GB_MODELS_PATH, TF_ROUTER_TOKEN))
            problems += gb_refused("GET /v1/models, the token in the query", status, hdrs, body, 400,
                                   needle=GB_MISPLACED)
            problems += peer_untouched(before, "models auth")
            return problems, ["refused 401 : no Authorization, a wrong token, x-api-key -- on the list, "
                              "a known id and an unknown id (no model data)",
                              "refused 403 : Host evil:port, Origin %s" % GB_EVIL_ORIGIN,
                              "refused 400 : the token as after_id (misplaced token)"]

        def b28():
            problems = []
            before = len(peer.requests)
            for path in ("%s?tf_other=1" % GB_MODELS_PATH, "%s?limit=1&tf_other=1" % GB_MODELS_PATH,
                         "/v1/models/tf-b-second?tf_other=1"):
                status, hdrs, body = gb_get(mclient, path)
                problems += gb_refused("GET %s" % path, status, hdrs, body, 400)
            for path in (GB_MODELS_PATH, "/v1/models/claude-sonnet-4-5"):
                status, hdrs, body = mclient.post(path=path, obj=gb_body())
                problems += gb_refused("POST %s" % path, status, hdrs, body, 405,
                                       extra_headers=(("Allow", "GET"),))
            for path in (MESSAGES_PATH, MESSAGES_PATH + "/count_tokens"):
                status, hdrs, body = gb_get(mclient, path)
                problems += gb_refused("GET %s" % path, status, hdrs, body, 405,
                                       extra_headers=(("Allow", "POST"),))
            bearer = [("Authorization", "Bearer " + mclient.token), ("Connection", "close")]
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH, body=b'{"tf": 1}', length=True)
            problems += gb_refused("GET /v1/models with a body", status, hdrs, body, 400)
            status, hdrs, body = gb_get(mclient, GB_MODELS_PATH, length=None,
                                        pairs=bearer + [("Transfer-Encoding", "chunked")], body=b"0\r\n\r\n")
            problems += gb_refused("GET /v1/models, Transfer-Encoding", status, hdrs, body, 411)
            status, hdrs, body, _first, _closed = gb_raw(mclient, gb_request(
                mclient, method="HEAD", path=GB_MODELS_PATH, pairs=bearer, length=None))
            if status != 405 or hdrs.get("allow") != "GET" or body:
                problems.append("HEAD /v1/models: status %r, Allow %r, %d body byte(s); expected 405, "
                                "Allow: GET, none" % (status, hdrs.get("allow"), len(body)))
            # A two-word (HTTP/0.9) GET reaches do_GET; it must get an HTTP/1.1 head, never a bare body.
            head09 = ("GET %s\r\nHost: %s:%d\r\nAuthorization: Bearer %s\r\n\r\n"
                      % (GB_MODELS_PATH, mclient.host, mclient.port, mclient.token)).encode("latin-1")
            status, hdrs, body, first, _closed = gb_raw(mclient, head09)
            if not first.startswith("HTTP/1.1 400"):
                problems.append("HTTP/0.9 GET /v1/models: status line %r, expected 'HTTP/1.1 400 ...'"
                                % first[:60])
            problems += peer_untouched(before, "models methods")
            return problems, ["refused 400 : ?tf_other=1, alone and beside limit, on the list and an id",
                              "refused 405 : POST on both models paths (Allow: GET); GET on both "
                              "messages paths (Allow: POST); HEAD /v1/models (Allow: GET)",
                              "framing     : a GET body 400, Transfer-Encoding 411, HTTP/0.9 400"]

        def b29():
            box = new_sandbox(fixture_root, "models-empty")
            empty = RouterProc(box, write_config(box, gb_models_config(peer.url(), routes=())),
                               label="models-empty")
            try:
                if empty.wait_ready() is None:
                    return [gb_not_ready(empty)], []
                ec = empty.client()
                before = len(peer.requests)
                status, hdrs, body = gb_get(ec, GB_MODELS_PATH)
                obj, problems = gb_json_200("GET /v1/models, no routes", status, hdrs, body)
                want = {"data": [], "has_more": False, "first_id": None, "last_id": None}
                if obj is not None and obj != want:
                    problems.append("GET /v1/models, no routes: %r, expected %r"
                                    % (ga_redact(str(obj))[:160], want))
                status, _h, body = gb_get(ec, "/v1/models/claude-sonnet-4-5")
                problems += gb_not_found("GET /v1/models/claude-sonnet-4-5, no routes", status, body,
                                         "claude-sonnet-4-5")
                problems += peer_untouched(before, "no routes")
                problems += gb_control(ec, "control: a POST still takes the default route")
                return problems, ["config      : routes {}, a default with display_name",
                                  "list        : data [], has_more false, first_id / last_id null"]
            finally:
                empty.close()

        cases = [(GB_CASES[0], live(b1)), (GB_CASES[1], live(b2)), (GB_CASES[2], live(b3)),
                 (GB_CASES[3], live(b4)), (GB_CASES[4], live(b5)), (GB_CASES[5], live(b6)),
                 (GB_CASES[6], live(b7)), (GB_CASES[7], live(b8)), (GB_CASES[8], live(b9)),
                 (GB_CASES[9], live(b10)), (GB_CASES[10], live(b11)), (GB_CASES[11], live(b12)),
                 (GB_CASES[12], live(b13)), (GB_CASES[13], live(b14)), (GB_CASES[14], live(b15)),
                 (GB_CASES[15], b16), (GB_CASES[17], live(b18)), (GB_CASES[18], live(b19)),
                 (GB_CASES[19], b20), (GB_CASES[20], live(b21)), (GB_CASES[21], live(b22)),
                 (GB_CASES[23], live(b24)), (GB_CASES[22], live(b23)),
                 (GB_CASES[24], live_models(b25)), (GB_CASES[25], live_models(b26)),
                 (GB_CASES[26], live_models(b27)), (GB_CASES[27], live_models(b28)),
                 (GB_CASES[28], b29),
                 (GB_CASES[16], live(b17))]   # last: it sweeps every error body the others provoked
        for cid, fn in cases:
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s"
                                 % (type(exc).__name__, ga_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001 -- a setup failure fails every case not yet run
        setup = "the group setup raised %s: %s" % (type(exc).__name__, ga_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    finally:
        if proc is not None:
            proc.close()
        if models_proc is not None:
            models_proc.close()
        peer.close()
    for cid in GB_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GB, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group C: outbound
# ---------------------------------------------------------------------------

GC_CASES = (
    "public-only refuses 127.0.0.1",     # C1
    "public-only refuses localhost",     # C2
    "loopback allowed, one C-L",         # C3
    "a 302 is refused, not followed",    # C4
    "proxy env vars are ignored",        # C5
    "TLS without ca_file refused",       # C6
    "chunked SSE read line by line",     # C7
    "over-long line: pump closes all",   # C8
    "connect timeout, full backlog",     # C9
    "TLS with ca_file verified",         # C10
    "_ch_address_refused table",         # C11
    "private policy: v4 and mapped",     # C12
    "private policy: v6 link/multi",     # C13
    "loopback needs allow_loopback",     # C14
)

GC_BACKEND = "tfc"
GC_PATH = "/v1/chat/completions"
GC_BODY = json.dumps({"model": "tf-model", "messages": [{"role": "user", "content": "tf c"}]},
                     separators=(",", ":")).encode("utf-8")
GC_PEER_ANSWER = {"id": "tf-cmpl-c", "object": "chat.completion", "created": 1,
                  "model": "tf-model", "choices": []}
GC_OK_SCRIPT = [("respond_json", 200, GC_PEER_ANSWER)]

# The transport (Step 6, task-031) and the address policy (Step 6, task-030):
# a case whose symbols are absent FAILS with their names -- the red state.
GC_SEND = ("_rt_send", "_rt_upstream_headers")
GC_POLICY = ("_rt_private_policy_refused", "_rt_resolve")
# C8: the upstream pump (Step 7, task-037) and the module-level values it reads.
GC_PUMP = ("_rt_pump", "_RtByteQueue")
GC_PUMP_VALUES = ("_LINE_TOO_LONG", "_EOF", "_SSE_LINE_LIMIT", "_RT_QUEUE_BYTES")
GC_PUMP_ITEMS_S = 10.0           # C8: the over-long line must reach the queue within this
GC_PUMP_JOIN_S = 10.0            # C8: the pump must have exited within this after its last item
GC_PUMP_ITEM_CAP = 8             # C8: more queue items than this means the pump did not stop
GC_PUMP_HEAD = "event: ping"     # C8: one ordinary line first, so a line-agnostic pump is visible

GC_CONNECT_TIMEOUT_S = 1         # C9: the backend's connect_timeout
GC_CONNECT_SLACK_S = 2.0         # C9: the failure must land within connect_timeout + this
GC_FILL_LIMIT = 256              # C9: at most this many filler connects into a listen(0) backlog
GC_FILL_PROBE_S = 0.2            # C9: a filler connect still pending after this means the backlog is full
GC_RESOLVE_HOLD_S = 10.0         # C9 (V9): the stalled resolver answers after this, unless released

GC_PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
GC_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")

GC_SSE_LINES = ("event: message_start", 'data: {"type":"message_start"}', "",
                ": keep-alive", 'data: {"type":"ping"}', "", "data: [DONE]", "")

# C11: (address, needle in the verdict, or None for accepted) -- the public-only
# classifier, hand-copied verbatim from Scripts/_mcp_chrome.py.
GC_PUBLIC_TABLE = (("127.0.0.1", "loopback"), ("10.0.0.1", "private"), ("100.64.0.1", ""),
                   ("::ffff:10.0.0.1", "IPv4-mapped private"),
                   ("64:ff9b::a00:1", "translation prefix"), ("8.8.8.8", None))
# C12/C13 under allow_private + allow_loopback: (address, reason class, or None for accepted).
GC_PRIVATE_V4 = (("169.254.169.254", "link-local"), ("::ffff:169.254.169.254", "link-local"),
                 ("224.0.0.1", "multicast"), ("0.0.0.0", "unspecified"),
                 ("240.0.0.1", "reserved"), ("10.0.0.1", None), ("fd00::1", None),
                 # V10: Alibaba's metadata address sits in CGNAT, which otherwise passes.
                 ("100.100.100.200", "metadata"), ("::ffff:100.100.100.200", "metadata"),
                 ("100.64.0.1", None))
# V10: AWS's IPv6 metadata address is a ULA, and Teredo tunnels to an embedded IPv4.
GC_PRIVATE_V6 = (("fe80::1", "link-local"), ("ff02::1", "multicast"),
                 ("fd00:ec2::254", "metadata"), ("2001::1", "Teredo"), ("fd00:ec2::253", None))


def gc_missing(mod, names):
    """One problem naming every symbol of *names* the module does not define (red, never a crash)."""
    absent = [n for n in names if not callable(getattr(mod, n, None))]
    if not absent:
        return []
    return ["the module defines no %s (Step 6 not landed)" % ", ".join(absent)]


def gc_entry(url, **extra):
    """A mistral backend entry at *url*; *extra* sets (and so overrides the loopback defaults of) keys."""
    entry = {"kind": "mistral", "base_url": url, "api_key": TF_KEY_MISTRAL}
    entry.update(extra)
    return entry


def gc_backend(mod, sandbox, stem, entry):
    """The BackendSpec of *entry*, built through load_config on a sandbox config."""
    cfg = {"auth_token": TF_ROUTER_TOKEN,
           "backends": {GC_BACKEND: entry},
           "routes": {"claude-tf-c": {"backend": GC_BACKEND, "model": "tf-model"}},
           "default": {"backend": GC_BACKEND, "model": "tf-model"}}
    path = write_config(sandbox, cfg, filename="c-%s.json" % stem)
    return mod.load_config(path).backends[GC_BACKEND]


def gc_open(mod, backend, stream=False):
    """(conn, resp) of one _rt_send POST of GC_BODY with the headers _rt_upstream_headers builds."""
    inbound = types.SimpleNamespace(backend=backend, client_headers={}, stream=stream)
    headers = mod._rt_upstream_headers(inbound)
    return mod._rt_send(backend, GC_PATH, headers, GC_BODY)


def gc_send(mod, backend):
    """{"exc"} if _rt_send raised, else {"status", "body", "tls"} with the connection closed."""
    try:
        conn, resp = gc_open(mod, backend)
    except Exception as exc:  # noqa: BLE001 -- the type is the finding
        return {"exc": exc}
    try:
        sock = getattr(conn, "raw_sock", None) or getattr(conn, "sock", None)
        tls = sock.version() if isinstance(sock, ssl.SSLSocket) else None
        data = resp.read()
        RESPONSE_BODIES.append(("C upstream body", data))
        return {"status": resp.status, "body": data, "tls": tls}
    finally:
        conn.close()


def gc_refused(mod, label, res, needles=()):
    """Problems unless *res* is an UpstreamError 502 whose message holds every needle and no sentinel."""
    exc = res.get("exc")
    if exc is None:
        return ["%s: succeeded with status %r, expected UpstreamError 502" % (label, res.get("status"))]
    msg = str(exc)
    if not isinstance(exc, getattr(mod, "UpstreamError", ())):
        return ["%s: raised %s instead of UpstreamError: %s"
                % (label, type(exc).__name__, ga_redact(msg)[:200])]
    problems = []
    if getattr(exc, "status", None) != 502:
        problems.append("%s: UpstreamError status %r, expected 502" % (label, getattr(exc, "status", None)))
    missing = [n for n in needles if n not in msg]
    if missing:
        problems.append("%s: message %r does not name %r" % (label, ga_redact(msg)[:200], missing))
    if any(value in msg for value in SENTINELS):
        problems.append("%s: the message carries a sentinel" % label)
    return problems


def gc_ok(label, res):
    """Problems unless *res* is a 200 whose body is GC_PEER_ANSWER."""
    exc = res.get("exc")
    if exc is not None:
        return ["%s: raised %s: %s" % (label, type(exc).__name__, ga_redact(str(exc))[:200])]
    problems = []
    if res.get("status") != 200:
        problems.append("%s: status %r, expected 200" % (label, res.get("status")))
    try:
        if json.loads(res.get("body") or b"null") != GC_PEER_ANSWER:
            problems.append("%s: the body is not the peer's answer" % label)
    except ValueError:
        problems.append("%s: the body is not JSON" % label)
    return problems


def gc_untouched(label, peer):
    """Problems unless the (closed) *peer* accepted no connection and recorded no request."""
    if peer.conns or peer.requests:
        return ["%s: accepted %d connection(s), recorded %d request(s); expected none"
                % (label, peer.conns, len(peer.requests))]
    return []


def gc_one_request(label, peer):
    """Problems unless *peer* recorded exactly one request."""
    if len(peer.requests) != 1:
        return ["%s: recorded %d request(s), expected 1" % (label, len(peer.requests))]
    return []


def gc_outcome(res):
    """One short detail word for a gc_send result."""
    exc = res.get("exc")
    if exc is None:
        return "status %r" % res.get("status")
    return "%s %s: %s" % (type(exc).__name__, getattr(exc, "status", ""), ga_redact(str(exc))[:120])


def gc_full_backlog():
    """(listener, fillers, full): a listen(0) socket that never accepts, its backlog
    filled by non-blocking connects; *full* is True once a filler stays pending."""
    lsock = socket.socket()
    fillers = []
    full = False
    try:
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(0)
        addr = lsock.getsockname()
        for _ in range(GC_FILL_LIMIT):
            sock = socket.socket()
            fillers.append(sock)
            sock.setblocking(False)
            sock.connect_ex(addr)
            _r, writable, _x = select.select([], [sock], [], GC_FILL_PROBE_S)
            if not writable:
                full = True
                break
    except BaseException:
        for sock in fillers + [lsock]:
            sock.close()
        raise
    return lsock, fillers, full


def gc_vet(mod, base, addr, needle):
    """Problems: *addr* through _rt_private_policy_refused and through _rt_resolve
    (on *base* with host *addr*); refused with *needle* in the reason, or (None)
    accepted and returned."""
    problems = []
    ip = ipaddress.ip_address(addr)
    try:
        verdict = mod._rt_private_policy_refused(ip, True)
    except Exception as exc:  # noqa: BLE001
        verdict = None
        problems.append("%s: _rt_private_policy_refused raised %s" % (addr, type(exc).__name__))
    else:
        if needle is None and verdict is not None:
            problems.append("%s: _rt_private_policy_refused refused it (%r), expected accepted"
                            % (addr, verdict))
        elif needle is not None and not (isinstance(verdict, str) and needle in verdict):
            problems.append("%s: _rt_private_policy_refused -> %r, expected a refusal as %s"
                            % (addr, verdict, needle))
    try:
        got = mod._rt_resolve(base._replace(host=addr))
    except Exception as exc:  # noqa: BLE001
        if needle is None:
            problems.append("%s: _rt_resolve raised %s: %s, expected the address"
                            % (addr, type(exc).__name__, ga_redact(str(exc))[:160]))
        else:
            problems += gc_refused(mod, "%s: _rt_resolve" % addr, {"exc": exc}, (needle,))
    else:
        if needle is not None:
            problems.append("%s: _rt_resolve returned %r, expected a refusal as %s" % (addr, got, needle))
        else:
            found = False
            for item in got or ():
                try:
                    found = found or ipaddress.ip_address(str(item).split("%")[0]) == ip
                except ValueError:
                    pass
            if not found:
                problems.append("%s: _rt_resolve returned %r, which does not hold it" % (addr, got))
    return problems


def group_c(suite, fixture_root):
    """C. outbound: the address policy, TLS verification and upstream refusals,
    in-process against loopback ScriptedPeers (plain and verified TLS).

    The module is imported once; each case calls _rt_resolve / _rt_send /
    _rt_upstream_headers / the classifiers directly.  A missing symbol FAILS the
    case with its name (the red state before Step 6), never crashes the group.
    """
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_c", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GC_CASES:
            suite.record(GC, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT),
                                      type(exc).__name__, exc)])
        return

    sandbox = new_sandbox(fixture_root, "outbound")
    ca_file = sandbox_ca_file(sandbox)
    private = dict(allow_private=True, allow_loopback=True)
    public = dict(allow_private=False, allow_loopback=False)

    def c1():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT, tls=True) as peer:
            backend = gc_backend(mod, sandbox, "c1", gc_entry(peer.url(), ca_file=ca_file, **public))
            res = gc_send(mod, backend)
        problems += gc_refused(mod, "https://127.0.0.1 public-only", res, ("loopback",))
        problems += gc_untouched("the peer", peer)
        return problems, ["backend     : mistral %s, allow_private false, ca_file set" % peer.url(),
                          "outcome     : %s" % gc_outcome(res)]

    def c2():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT, tls=True) as peer:
            backend = gc_backend(mod, sandbox, "c2",
                                 gc_entry(peer.url("localhost"), ca_file=ca_file, **public))
            res = gc_send(mod, backend)
        problems += gc_refused(mod, "https://localhost public-only", res, ("loopback",))
        problems += gc_untouched("the peer", peer)
        return problems, ["backend     : mistral %s, allow_private false" % peer.url("localhost"),
                          "outcome     : %s" % gc_outcome(res)]

    def c3():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT) as peer:
            backend = gc_backend(mod, sandbox, "c3", gc_entry(peer.url(), **private))
            res = gc_send(mod, backend)
        problems += gc_ok("loopback with allow_private + allow_loopback", res)
        problems += gc_one_request("the peer", peer)
        lengths = []
        if peer.requests:
            req = peer.requests[0]
            lengths = [v for k, v in req["headers"] if k.lower() == "content-length"]
            if len(lengths) != 1:
                problems.append("the request carried %d Content-Length header(s), expected exactly 1 "
                                "(M1: _rt_send frames the body itself)" % len(lengths))
            elif not lengths[0].isdigit() or int(lengths[0]) != len(req["body"]):
                problems.append("Content-Length %r != the %d body bytes the peer read"
                                % (lengths[0], len(req["body"])))
            if req["body"] != GC_BODY:
                problems.append("the peer read a body that differs from the one sent")
            if any(k.lower() == "transfer-encoding" for k, _v in req["headers"]):
                problems.append("the request carried Transfer-Encoding")
        return problems, ["framing     : Content-Length %s for %d body bytes"
                          % ("/".join(lengths) or "absent", len(GC_BODY)),
                          "outcome     : %s" % gc_outcome(res)]

    def c4():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT) as target:
            location = target.url() + GC_PATH
            with ScriptedPeer(script=[("redirect", location, 302)]) as src:
                backend = gc_backend(mod, sandbox, "c4", gc_entry(src.url(), **private))
                res = gc_send(mod, backend)
        problems += gc_refused(mod, "a 302 from upstream", res, ("redirect",))
        problems += gc_one_request("the redirecting peer", src)
        problems += gc_untouched("the redirect target", target)
        return problems, ["redirect    : 302 -> another loopback peer",
                          "outcome     : %s" % gc_outcome(res)]

    def c5():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        saved = {k: os.environ.get(k) for k in GC_PROXY_VARS + GC_NO_PROXY_VARS}
        with ScriptedPeer(script=GC_OK_SCRIPT) as proxy, ScriptedPeer(script=GC_OK_SCRIPT) as target:
            backend = gc_backend(mod, sandbox, "c5", gc_entry(target.url(), **private))
            try:
                for key in GC_PROXY_VARS:
                    os.environ[key] = proxy.url()
                for key in GC_NO_PROXY_VARS:
                    os.environ.pop(key, None)
                res = gc_send(mod, backend)
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        problems += gc_ok("the direct request", res)
        problems += gc_one_request("the target", target)
        problems += gc_untouched("the proxy peer", proxy)
        return problems, ["environment : %s -> a recording peer, NO_PROXY unset"
                          % ", ".join(GC_PROXY_VARS),
                          "outcome     : %s" % gc_outcome(res)]

    def c6():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT, tls=True) as peer:
            backend = gc_backend(mod, sandbox, "c6", gc_entry(peer.url(), **private))
            res = gc_send(mod, backend)
        problems += gc_refused(mod, "TLS to the test leaf without ca_file", res,
                               ("certificate verify failed",))
        if peer.requests:
            problems.append("the peer recorded %d request(s) over an unverified handshake"
                            % len(peer.requests))
        return problems, ["backend     : %s, no ca_file (system trust store)" % peer.url(),
                          "outcome     : %s" % gc_outcome(res)]

    def c7():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        want = [_peer_line_bytes(line) for line in GC_SSE_LINES]
        got = []
        chunked = None
        with ScriptedPeer(script=[("respond_chunked_sse", list(GC_SSE_LINES))]) as peer:
            backend = gc_backend(mod, sandbox, "c7", gc_entry(peer.url(), **private))
            try:
                conn, resp = gc_open(mod, backend, stream=True)
            except Exception as exc:  # noqa: BLE001
                return ["_rt_send raised %s: %s" % (type(exc).__name__, ga_redact(str(exc))[:200])], []
            try:
                chunked = getattr(resp, "chunked", None)
                for _ in range(len(want) + 8):
                    line = resp.readline()
                    if not line:
                        break
                    got.append(line)
            finally:
                conn.close()
        if chunked is not True:
            problems.append("the response was not read as chunked (resp.chunked = %r)" % (chunked,))
        if got != want:
            problems.append("lines read %r != the %d lines sent" % (got[:12], len(want)))
        return problems, ["stream      : %d SSE lines, one chunk each, read with readline()"
                          % len(want), "python      : %s" % platform.python_version()]

    def c8():
        problems = gc_missing(mod, GC_SEND)
        absent = [n for n in GC_PUMP + GC_PUMP_VALUES if not hasattr(mod, n)]
        if absent:
            problems.append("the module defines no %s (Step 7 not landed)" % ", ".join(absent))
        if problems:
            return problems, []
        limit = mod._SSE_LINE_LIMIT
        long_line = b"data: " + b"x" * (limit + 1 - len(b"data: "))   # limit + 1 bytes before "\n"
        # Chunked + keep-alive: http.client keeps conn.sock after getresponse(), so
        # "conn.sock is None" afterwards can only mean that someone called conn.close().
        script = [("respond_chunked_sse", [GC_PUMP_HEAD, long_line], (), (("Connection", "keep-alive"),))]

        def shown(item):
            if isinstance(item, bytes):
                return "line(%d bytes)" % len(item)
            if item is mod._LINE_TOO_LONG:
                return "_LINE_TOO_LONG"
            if item is mod._EOF:
                return "_EOF"
            return repr(item)[:60]

        items = []
        with ScriptedPeer(script=script) as peer:
            backend = gc_backend(mod, sandbox, "c8", gc_entry(peer.url(), **private))
            conn, resp = gc_open(mod, backend, stream=True)
            kept = conn.sock is not None
            raw = getattr(conn, "raw_sock", None)
            stop = threading.Event()
            pump = None
            try:
                q = mod._RtByteQueue(mod._RT_QUEUE_BYTES)
                pump = threading.Thread(target=mod._rt_pump, args=(conn, resp, q, stop),
                                        name="rt-pump", daemon=True)
                pump.start()
                deadline = time.monotonic() + GC_PUMP_ITEMS_S
                while len(items) <= GC_PUMP_ITEM_CAP and time.monotonic() < deadline:
                    try:
                        item = q.get(0.25)
                    except queue.Empty:
                        if not pump.is_alive():
                            break
                        continue
                    items.append(item)
                    if not isinstance(item, bytes):
                        break           # a sentinel or an exception item ends the stream
                pump.join(GC_PUMP_JOIN_S)
                alive = pump.is_alive()
                resp_closed = resp.isclosed()
                sock_gone = conn.sock is None
                raw_closed = raw is not None and raw.fileno() == -1
            finally:
                if pump is not None and pump.is_alive():
                    stop.set()
                    try:
                        socket.socket.shutdown(raw, socket.SHUT_RDWR)
                    except (OSError, TypeError):
                        pass
                    pump.join(GC_PUMP_JOIN_S)
                resp.close()
                conn.close()
        RESPONSE_BODIES.append(("C8 pump lines", b"".join(i for i in items if isinstance(i, bytes))))
        if not kept:
            problems.append("precondition: conn.sock was already None after _rt_send (http.client "
                            "closed it), so the conn half of the case proves nothing")
        want_head = _peer_line_bytes(GC_PUMP_HEAD)
        lines = [i for i in items if isinstance(i, bytes)]
        if not items or items[-1] is not mod._LINE_TOO_LONG:
            problems.append("the queue yielded %s, expected the head line then _LINE_TOO_LONG"
                            % ([shown(i) for i in items] or "nothing"))
        if lines != [want_head]:
            problems.append("line items %s, expected exactly the %d-byte head line (the over-long "
                            "line must never reach the queue as bytes)"
                            % ([shown(i) for i in lines], len(want_head)))
        if alive:
            problems.append("the pump thread was still alive %.0f s after its last item" % GC_PUMP_JOIN_S)
        if not resp_closed:
            problems.append("resp is not closed: the pump did not call resp.close()")
        if not sock_gone:
            problems.append("conn.sock is still set: the pump did not call conn.close()")
        if not raw_closed:
            problems.append("the raw upstream socket is still open after the pump exited")
        return problems, ["upstream    : chunked, keep-alive; %r then one line of %d + 1 bytes"
                          % (GC_PUMP_HEAD, limit),
                          "queue       : %s" % ", ".join(shown(i) for i in items),
                          "after join  : alive=%s resp.isclosed()=%s conn.sock is None=%s raw fd closed=%s"
                          % (alive, resp_closed, sock_gone, raw_closed)]

    def c9_resolve():
        """V9: a resolver that stalls is bounded by connect_timeout (504), the peer untouched."""
        problems = []
        real = getattr(mod, "_rt_getaddrinfo", None)
        if not callable(real):
            return ["the module defines no _rt_getaddrinfo seam, so the resolver runs outside "
                    "the connect deadline (V9)"], "no seam"
        release = threading.Event()
        calls = []

        def stalled(host, port):
            calls.append(host)
            release.wait(GC_RESOLVE_HOLD_S)
            return real(host, port)

        with ScriptedPeer(script=GC_OK_SCRIPT) as peer:
            backend = gc_backend(mod, sandbox, "c9-resolve",
                                 gc_entry(peer.url(), connect_timeout=GC_CONNECT_TIMEOUT_S, **private))
            mod._rt_getaddrinfo = stalled
            try:
                started = time.monotonic()
                res = gc_send(mod, backend)
                elapsed = time.monotonic() - started
            finally:
                mod._rt_getaddrinfo = real
                release.set()
        if not calls:
            problems.append("the stalled resolver was never called: _rt_resolve does not go "
                            "through _rt_getaddrinfo")
        exc = res.get("exc")
        if exc is None:
            problems.append("a stalled resolver: succeeded with status %r, expected a 504"
                            % res.get("status"))
        elif not isinstance(exc, getattr(mod, "UpstreamError", ())):
            problems.append("a stalled resolver: raised %s instead of UpstreamError" % type(exc).__name__)
        elif getattr(exc, "status", None) != 504:
            problems.append("a stalled resolver: UpstreamError status %r, expected 504"
                            % getattr(exc, "status", None))
        bound = GC_CONNECT_TIMEOUT_S + GC_CONNECT_SLACK_S
        if elapsed > bound:
            problems.append("a stalled resolver: the failure took %.2f s, over connect_timeout + "
                            "%.0f s = %.1f s" % (elapsed, GC_CONNECT_SLACK_S, bound))
        problems += gc_untouched("the peer behind the stalled resolver", peer)
        return problems, "%s after %.2f s" % (gc_outcome(res), elapsed)

    def c9():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        resolve_problems, resolve_shown = c9_resolve()
        problems += resolve_problems
        where = "%s %s, Python %s" % (platform.system(), platform.release(),
                                      platform.python_version())
        lsock, fillers, full = gc_full_backlog()
        try:
            backend = gc_backend(mod, sandbox, "c9",
                                 gc_entry("http://127.0.0.1:%d" % lsock.getsockname()[1],
                                          connect_timeout=GC_CONNECT_TIMEOUT_S, **private))
            started = time.monotonic()
            res = gc_send(mod, backend)
            elapsed = time.monotonic() - started
        finally:
            for sock in fillers + [lsock]:
                sock.close()
        detail = ["backlog     : listen(0), %d filler connect(s), %s"
                  % (len(fillers), "full" if full else "every connect completed"),
                  "outcome     : %s after %.2f s" % (gc_outcome(res), elapsed),
                  "resolver    : stalled %g s -> %s" % (GC_RESOLVE_HOLD_S, resolve_shown),
                  "platform    : %s" % where]
        if not full:
            if problems:
                return problems, detail
            return [], detail + ["note        : the kernel completes connects into a full "
                                 "backlog here; the timeout is unprovable (INFO)"], H.INFO
        exc = res.get("exc")
        if exc is None:
            problems.append("succeeded with status %r against a listener that never accepts"
                            % res.get("status"))
        elif not isinstance(exc, getattr(mod, "UpstreamError", ())):
            problems.append("raised %s instead of UpstreamError" % type(exc).__name__)
        elif getattr(exc, "status", None) not in (502, 504):
            problems.append("UpstreamError status %r, expected 502 or 504" % getattr(exc, "status", None))
        bound = GC_CONNECT_TIMEOUT_S + GC_CONNECT_SLACK_S
        if elapsed > bound:
            problems.append("the failure took %.2f s, over connect_timeout + %.0f s = %.1f s"
                            % (elapsed, GC_CONNECT_SLACK_S, bound))
        return problems, detail

    def c10():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT, tls=True) as peer:
            backend = gc_backend(mod, sandbox, "c10", gc_entry(peer.url(), ca_file=ca_file, **private))
            res = gc_send(mod, backend)
        problems += gc_ok("TLS with ca_file", res)
        problems += gc_one_request("the peer", peer)
        if res.get("exc") is None and res.get("tls") not in ("TLSv1.2", "TLSv1.3"):
            problems.append("the connection's socket is not a TLS 1.2+ SSLSocket (version %r)"
                            % (res.get("tls"),))
        return problems, ["backend     : %s, ca_file = the sandbox copy of test-ca.pem" % peer.url(),
                          "handshake   : %s" % (res.get("tls") or "none"),
                          "outcome     : %s" % gc_outcome(res)]

    def c11():
        problems = gc_missing(mod, ("_ch_address_refused",))
        if problems:
            return problems, []
        shown = []
        for addr, needle in GC_PUBLIC_TABLE:
            verdict = mod._ch_address_refused(ipaddress.ip_address(addr))
            shown.append("%s=%s" % (addr, verdict or "accepted"))
            if needle is None:
                if verdict is not None:
                    problems.append("%s refused (%r), expected accepted" % (addr, verdict))
            elif not (isinstance(verdict, str) and verdict and needle in verdict):
                problems.append("%s -> %r, expected a refusal%s"
                                % (addr, verdict, " naming %r" % needle if needle else ""))
        return problems, ["verdicts    : %s" % "; ".join(shown)]

    def policy_case(stem, table):
        def case():
            problems = gc_missing(mod, GC_POLICY)
            if problems:
                return problems, []
            base = gc_backend(mod, sandbox, stem, gc_entry("https://10.0.0.1:8443", **private))
            for addr, needle in table:
                problems += gc_vet(mod, base, addr, needle)
            return problems, ["flags       : allow_private true, allow_loopback true",
                              "table       : %s" % ", ".join("%s %s" % (a, n or "accepted")
                                                         for a, n in table)]
        return case

    def c14():
        problems = gc_missing(mod, GC_SEND)
        if problems:
            return problems, []
        with ScriptedPeer(script=GC_OK_SCRIPT) as peer:
            refused = gc_backend(mod, sandbox, "c14-off",
                                 gc_entry(peer.url(), allow_private=True, allow_loopback=False))
            res_off = gc_send(mod, refused)
            before = len(peer.requests)
            allowed = gc_backend(mod, sandbox, "c14-on", gc_entry(peer.url(), **private))
            res_on = gc_send(mod, allowed)
        problems += gc_refused(mod, "127.0.0.1 without allow_loopback", res_off, ("loopback",))
        if before:
            problems.append("the peer recorded %d request(s) without allow_loopback" % before)
        problems += gc_ok("127.0.0.1 with allow_loopback", res_on)
        if peer.conns != 1 or len(peer.requests) != 1:
            problems.append("the peer accepted %d connection(s) and recorded %d request(s) "
                            "over both calls, expected 1 and 1" % (peer.conns, len(peer.requests)))
        return problems, ["off         : %s" % gc_outcome(res_off),
                          "on          : %s" % gc_outcome(res_on)]

    try:
        cases = [(GC_CASES[0], c1), (GC_CASES[1], c2), (GC_CASES[2], c3), (GC_CASES[3], c4),
                 (GC_CASES[4], c5), (GC_CASES[5], c6), (GC_CASES[6], c7), (GC_CASES[7], c8),
                 (GC_CASES[8], c9), (GC_CASES[9], c10), (GC_CASES[10], c11),
                 (GC_CASES[11], policy_case("c12", GC_PRIVATE_V4)),
                 (GC_CASES[12], policy_case("c13", GC_PRIVATE_V6)), (GC_CASES[13], c14)]
        for cid, fn in cases:
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s"
                                 % (type(exc).__name__, ga_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001 -- a setup failure fails every case not yet run
        setup = "the group setup raised %s: %s" % (type(exc).__name__, ga_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    for cid in GC_CASES:
        got = results.get(cid, ([setup], []))
        status = got[2] if len(got) > 2 else None
        suite.record(GC, cid, got[0], status=status, detail=got[1])


# ---------------------------------------------------------------------------
# Streams: Anthropic event lines, the timed client reader (groups D and H)
# ---------------------------------------------------------------------------

STREAM_TIMEOUT_S = 40.0          # one client recv on a stream: above the 16 s silences of D4 and H1


def an_message_start(model):
    return {"type": "message_start",
            "message": {"id": "msg_tf_stream_0123456789abcdef", "type": "message",
                        "role": "assistant", "model": model, "content": [],
                        "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 12, "output_tokens": 1}}}


def an_text_delta(text):
    return {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}}


AN_BLOCK_START = {"type": "content_block_start", "index": 0,
                  "content_block": {"type": "text", "text": ""}}
AN_BLOCK_STOP = {"type": "content_block_stop", "index": 0}
AN_MESSAGE_DELTA = {"type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 5}}
AN_MESSAGE_STOP = {"type": "message_stop"}
AN_PING = {"type": "ping"}
AN_UPSTREAM_MODEL = "tf-upstream-echo-model"     # the model an Anthropic-speaking peer answers with


def an_lines(*payloads):
    """SSE lines (no newlines) of one event per payload, named by its "type".

    The JSON keeps json.dumps' default ", " / ": " separators and raw UTF-8, so
    a relay that re-serialises an event instead of relaying its bytes is
    visible line for line."""
    lines = []
    for payload in payloads:
        lines += ["event: %s" % payload["type"],
                  "data: %s" % json.dumps(payload, ensure_ascii=False), ""]
    return lines


# An Anthropic stream in three parts: the first event, the first delta, the rest.
AN_HEAD = an_lines(an_message_start(AN_UPSTREAM_MODEL))
AN_DELTA = an_lines(AN_BLOCK_START, an_text_delta("Hello"))
AN_FINISH = an_lines(an_text_delta(" wörld ✓"), AN_BLOCK_STOP, AN_MESSAGE_DELTA,
                     AN_MESSAGE_STOP)


def an_events(lines):
    """[(event, data)] of SSE *lines*, parsed as read_sse parses a stream."""
    events, event, data = [], None, []
    for line in lines:
        if line == "":
            if event is not None or data:
                events.append((event or "message", "\n".join(data)))
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].lstrip(" "))
    return events


def sse_wire_len(lines):
    """The bytes respond_sse sends for *lines* with its default head (a close_after budget)."""
    head = ScriptedPeer._head(200, ScriptedPeer.SSE_HEAD, ())
    return len(head) + sum(len(_peer_line_bytes(line)) for line in lines)


def open_stream(client, body, headers=None, path=None, timeout=STREAM_TIMEOUT_S):
    """(conn, resp, problems): an open 200 text/event-stream answer, or (None,
    None, [why]) with any other answer read, recorded and closed."""
    conn, resp = client.post(path=path, obj=body, headers=headers, stream=True, timeout=timeout)
    ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
    if resp.status == 200 and ctype == "text/event-stream":
        return conn, resp, []
    try:
        data = resp.read()
    except (OSError, http.client.HTTPException):
        data = b""
    finally:
        conn.close()
    RESPONSE_BODIES.append(("stream POST", data))
    _t, msg, _why = gb_envelope(data)
    return None, None, ["status %r %s%s, expected 200 text/event-stream"
                        % (resp.status, ctype or "(no Content-Type)",
                           (" (%s)" % gi_redact(msg)[:120]) if msg else "")]


def close_stream(conn, resp):
    """Hang up: the response's makefile first (it keeps the fd alive, P24), then the socket."""
    for closer in (resp.close, conn.close):
        try:
            closer()
        except OSError:
            pass


def read_stream(resp, on_event=None):
    """read_sse with arrival times: {"lines", "comments", "events", "eof", "exc"}.

    events are (event, data, monotonic time of its blank line).  *on_event(events)*
    runs after each complete event; True stops reading early (the caller hangs
    up).  An unterminated tail is kept as a last event, as read_sse keeps it, so
    a partial event the router let through fails the JSON check."""
    out = {"lines": [], "comments": [], "events": [], "eof": False, "exc": None}
    event, data = None, []
    try:
        while True:
            raw = resp.readline()
            if not raw:
                out["eof"] = True
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            out["lines"].append(line)
            if line == "":
                if event is not None or data:
                    out["events"].append((event or "message", "\n".join(data), time.monotonic()))
                    event, data = None, []
                    if on_event is not None and on_event(out["events"]):
                        break
                event, data = None, []
            elif line.startswith(":"):
                out["comments"].append(line)
            elif line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].lstrip(" "))
    except (OSError, http.client.HTTPException) as exc:
        out["exc"] = type(exc).__name__
    if event is not None or data:
        out["events"].append((event or "message", "\n".join(data), time.monotonic()))
    RESPONSE_BODIES.append(("event stream", "\n".join(out["lines"]).encode("utf-8")))
    return out


def ev_names(events):
    return [e[0] for e in events]


def ev_unparsed(events):
    """Problems: one per client event whose data is not a JSON object."""
    problems = []
    for i, ev in enumerate(events):
        try:
            ok = isinstance(json.loads(ev[1]), dict)
        except ValueError:
            ok = False
        if not ok:
            problems.append("client event %d (%s) does not parse as a JSON object: %r"
                            % (i, ev[0], gi_redact(ev[1])[:80]))
    return problems


def ev_errors(events):
    """[(error.type, error.message)] of every `event: error` (None, None when malformed)."""
    found = []
    for ev in events:
        if ev[0] != "error":
            continue
        try:
            err = json.loads(ev[1]).get("error") or {}
            found.append((err.get("type"), err.get("message")))
        except (ValueError, AttributeError):
            found.append((None, None))
    return found


def ev_shape(events):
    """One detail word list: the client's event names, a long run of one name collapsed."""
    names = ev_names(events)
    return ", ".join(names[:12]) + (" ... (%d events)" % len(names) if len(names) > 12 else "") \
        if names else "(none)"


def ev_ends_in_error(label, out, needle=None, err_type=None):
    """Problems unless the stream's last event is one `event: error` (holding *needle*,
    of *err_type*), no message_stop was relayed, and every event parses."""
    events = out["events"]
    problems = []
    errors = ev_errors(events)
    if not errors:
        problems.append("%s: no event: error in the client stream (%s)" % (label, ev_shape(events)))
    elif events[-1][0] != "error":
        problems.append("%s: the stream does not end with its event: error (%s)" % (label, ev_shape(events)))
    if len(errors) > 1:
        problems.append("%s: %d event: error, expected one" % (label, len(errors)))
    if errors:
        etype, msg = errors[-1]
        if needle and not (isinstance(msg, str) and needle in msg):
            problems.append("%s: the error message %r does not hold %r"
                            % (label, gi_redact(str(msg))[:160], needle))
        if err_type and etype != err_type:
            problems.append("%s: error.type %r, expected %r" % (label, etype, err_type))
    if "message_stop" in ev_names(events):
        problems.append("%s: a message_stop reached the client (KD-7)" % label)
    problems += ev_unparsed(events)
    return problems


def answer_status(label, status, body, want):
    """Problems unless *status* is *want* (the envelope message shown when it is not)."""
    if status == want:
        return []
    _t, msg, _why = gb_envelope(body) if body else (None, None, None)
    return ["%s: status %r%s, expected %d" % (label, status,
                                             (" (%s)" % gi_redact(msg)[:120]) if msg else "", want)]


def missing_names(mod, names):
    """One problem naming every symbol of *names* the module does not define (red, never a crash)."""
    absent = [n for n in names if not callable(getattr(mod, n, None))]
    return ["the module defines no %s" % ", ".join(absent)] if absent else []


# ---------------------------------------------------------------------------
# Group D: passthrough
# ---------------------------------------------------------------------------

GD_CASES = (
    "body relayed, model rewritten",     # D1
    "allow-listed headers + key",        # D2
    "SSE event bytes unchanged",         # D3
    "comment -> ping, ping rule",        # D4
    "EOF before stop -> error",          # D5
    "count_tokens, model rewritten",     # D6
    "error envelope kept, scrubbed",     # D7
    "429 -> 429 + retry-after",          # D8
    "upstream 401/403 -> 502",           # D9
    "unknown model: default or 404",     # D10
    "upstream model echoed as is",       # D11
    "TLS ragged EOF after stop ok",      # D12
    "oversized event -> error",          # D13
    "bare CR / BOM parsed as a client",  # D14
)

GD_BACKEND = "tfd"
GD_TLS_BACKEND = "tfdtls"
GD_DEFAULT_MODEL = "tf-d-default"
GD_UNROUTED = "claude-unrouted-x"
# route key claude-tf-<n> -> upstream model tf-<n>; d12/d12b on the verified-TLS backend.
GD_ROUTES = ("d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d8b", "d9", "d9b", "d11", "d13")
GD_TLS_ROUTES = ("d12", "d12b")
GD_COUNT = 4242                       # D6: the peer's input_tokens
GD_EVENT_LIMIT_FALLBACK = 2 * 1024 * 1024   # D13: _SSE_EVENT_LIMIT per the plan, when the module lacks it
GD_D13_LINE = 65536                   # D13: one data: line's bytes, far under _SSE_LINE_LIMIT
GD_D13_MARKER = "tf-d13-oversized-"
GD_D4_GAPS = (8.0, 8.5)               # D4: silences before ": tf-c2" and ": tf-c3" -- 16.5 s > _PING_INTERVAL_S
GD_D4_PINGS = 1                       # D4: pings in that silence under the ping rule (one per 15 s since a write)
GD_IDLE_S = 30                        # both D backends: idle_timeout above D4's 8.5 s gaps between comment lines

GD_ANSWER = {"id": "msg_tf_pass", "type": "message", "role": "assistant",
             "model": AN_UPSTREAM_MODEL, "content": [{"type": "text", "text": "ok ✓"}],
             "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 3, "output_tokens": 2}}

# D2: what the client sends, and what the upstream may see besides the two forwarded names.
GD_CLIENT_HEADERS = {"anthropic-version": "2023-06-01", "anthropic-beta": "tf-beta-2026-01-01",
                     "x-api-key": TF_CLIENT_XKEY, "x-tf-extra": "tf-extra-value",
                     "Cookie": "tf=cookie", "User-Agent": "tf-client/1.0"}
GD_FORWARDED = ("anthropic-version", "anthropic-beta")
GD_DUP_BETA = ("tf-beta-dup-one", "tf-beta-dup-two")   # D2: a forwardable header sent twice -> 400
GD_ROUTER_OWNED = frozenset({"host", "content-type", "accept", "user-agent", "accept-encoding",
                             "content-length"})

GD_STREAM_LINES = AN_HEAD + an_lines(AN_PING) + AN_DELTA + AN_FINISH     # D3, D12


def gd_error(etype, message):
    return {"type": "error", "error": {"type": etype, "message": message}}


GD_BOM = b"\xef\xbb\xbf"


def gd_d14(mod):
    """D14 (V30), in-process: _rt_sse_events splits lines on a bare CR as a client's
    SSE parser does, and both event relays also strip a stream-leading BOM -- so an
    `event: error` or `message_stop` a client would see is one the router sees too."""
    if mod is None:
        return ["the router module cannot be imported"], []
    parse = getattr(mod, "_rt_sse_events", None)
    relays = [(name, getattr(mod, name, None)) for name in ("EventRelay", "LlamacppRelay")]
    missing = [name for name, fn in [("_rt_sse_events", parse)] + relays if not callable(fn)]
    if missing:
        return ["the module defines no %s" % ", ".join(missing)], []
    problems, shown = [], []
    table = (("bare CR fields, CRLF blank", [b'event: ping\revent: x\rdata: {"a":1}\r\r\n'], [("x", '{"a":1}')]),
             ("two events on one LF line", [b"data: a\r\rdata: b\r\r\n"], [("message", "a"), ("message", "b")]),
             ("control: LF and CRLF lines", [b"event: x\n", b"data: 1\r\n", b"\n"], [("x", "1")]))
    for label, lines, want in table:
        events = list(parse(lines))
        got = [(n, d) for n, d, _r in events]
        if got != want:
            problems.append("parser, %s: %r, expected %r" % (label, got, want))
        if b"".join(r for _n, _d, r in events) != b"".join(lines):
            problems.append("parser, %s: the raw bytes are not the input bytes" % label)
        shown.append("parser      : %s -> %s" % (label, [n for n, _d in got]))
    err = json.dumps(gd_error("api_error", "tf d14 " + TF_KEY_PASS)).encode("ascii")
    smuggled = (("error behind bare CRs", [b"event: ping\revent: error\rdata: " + err + b"\r\r\n", b"\n"]),
                ("error behind a leading BOM", [GD_BOM + b"event: error\n", b"data: " + err + b"\n", b"\n"]))
    stop = [b'data: {"type":"ping"}\r\revent: message_stop\rdata: {"type":"message_stop"}\r\r\n', b"\n"]
    control = [b"event: ping\r\n", b'data: {"type":"ping"}\r\n', b"\r\n"]
    for cls_name, cls in relays:
        for label, lines in smuggled:
            relay = cls(gg_inbound(mod))
            text = b"".join(relay.feed(line) for line in lines).decode("utf-8", "replace")
            names = [n for n, _d in an_events(text.split("\n"))]
            if names != ["error"] or gi_redact(text) != text or not relay.closed:
                problems.append("%s, %s: events %s, closed %r, sentinel %s -- expected one scrubbed "
                                "event: error that closes the relay"
                                % (cls_name, label, names, relay.closed,
                                   "relayed" if gi_redact(text) != text else "absent"))
        relay = cls(gg_inbound(mod))
        for line in stop:
            relay.feed(line)
        after = relay.feed(b"event: ping\n") + relay.feed(b"\n")
        if not relay.closed or after:
            problems.append("%s, message_stop behind bare CRs: closed %r, %d byte(s) relayed after it"
                            % (cls_name, relay.closed, len(after)))
        relay = cls(gg_inbound(mod))
        out = b"".join(relay.feed(line) for line in control)
        if out != b"".join(control):
            problems.append("%s, control CRLF event: %r, expected the bytes unchanged" % (cls_name, out[:80]))
        shown.append("relay       : %s -- CR- and BOM-smuggled error scrubbed, message_stop closes" % cls_name)
    return problems, shown


GD_D7_VISIBLE = "tf-d7-visible"
GD_D7_ERROR = gd_error("billing_error", "tf upstream: credit low for key %s (%s)"
                       % (TF_KEY_PASS, GD_D7_VISIBLE))


def gd_body(route, stream=False, text="tf d"):
    return {"model": route, "max_tokens": 64, "stream": stream,
            "messages": [{"role": "user", "content": text}]}


def gd_d1_body():
    """D1: a request with every kind of field a relay could drop, reorder or normalise."""
    return {"model": "claude-tf-d1", "max_tokens": 64, "stream": False,
            "system": [{"type": "text", "text": "tf sys", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "tf d1 é✓"}]},
                         {"role": "assistant", "content": "ok"},
                         {"role": "user", "content": "again"}],
            "tools": [{"name": "Read", "description": "r",
                       "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}}}],
            "temperature": 0.5, "metadata": {"user_id": "tf-user"},
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "tf_future_field": {"nested": [1, 2.5, None, True, "✓"]}}


def gd_oversized_lines(limit):
    """D13: one event whose lines before its blank line total limit + 1 bytes
    (newlines included), spread over data: lines of at most GD_D13_LINE bytes."""
    head = "event: content_block_delta"
    lines = [head]
    need = limit + 1 - (len(head) + 1)
    while need > 0:
        if need <= GD_D13_LINE:
            size = need
        elif need - GD_D13_LINE >= 64:
            size = GD_D13_LINE
        else:
            size = need - 64
        lines.append("data: " + GD_D13_MARKER + "x" * (size - 7 - len(GD_D13_MARKER)))
        need -= size
    assert sum(len(line) + 1 for line in lines) == limit + 1
    return lines + [""]


def gd_scripts(limit):
    """upstream model -> the plain peer's steps."""
    answer = [("respond_json", 200, GD_ANSWER)]
    d4_lines = [": tf-pre-start"] + AN_HEAD + [": tf-c1", ": tf-c2", ": tf-c3"] + AN_DELTA + AN_FINISH
    d4_gaps = [0.0] * len(d4_lines)
    d4_gaps[d4_lines.index(": tf-c2")] = GD_D4_GAPS[0]
    d4_gaps[d4_lines.index(": tf-c3")] = GD_D4_GAPS[1]
    rate = gd_error("rate_limit_error", "tf slow down")
    return {
        "tf-d1": answer, "tf-d2": answer, "tf-d11": answer, GD_DEFAULT_MODEL: answer,
        "tf-d3": [("respond_sse", GD_STREAM_LINES)],
        "tf-d4": [("respond_sse", d4_lines, d4_gaps)],
        "tf-d5": [("respond_sse", AN_HEAD + AN_DELTA)],
        "tf-d6": [("respond_json", 200, {"input_tokens": GD_COUNT})],
        "tf-d7": [("respond_json", 400, GD_D7_ERROR)],
        "tf-d8": [("respond_json", 429, rate, (("Retry-After", "7"),))],
        "tf-d8b": [("respond_json", 429, rate, (("Retry-After", "7; tf-bad"),))],
        "tf-d9": [("respond_json", 401, gd_error("authentication_error", "invalid x-api-key"))],
        "tf-d9b": [("respond_json", 403, gd_error("permission_error", "tf key lacks access"))],
        "tf-d13": [("respond_sse", AN_HEAD + gd_oversized_lines(limit) + AN_FINISH)],
    }


def gd_tls_scripts():
    """upstream model -> the TLS peer's steps: the stream, then TCP dropped with no close_notify."""
    cut = GD_STREAM_LINES[:-3]           # without the message_stop event
    return {
        "tf-d12": [("close_after", sse_wire_len(GD_STREAM_LINES)), ("respond_sse", GD_STREAM_LINES)],
        "tf-d12b": [("close_after", sse_wire_len(cut)), ("respond_sse", cut)],
    }


def gd_dispatch(scripts):
    """A ScriptedPeer script choosing its steps by the upstream body's model."""
    def script(_index, request):
        try:
            model = json.loads((request.get("body") or b"").decode("utf-8")).get("model")
        except (UnicodeDecodeError, ValueError, AttributeError):
            model = None
        return scripts.get(model, [("respond_json", 404, gd_error("not_found_error", "tf no script"))])
    return script


def gd_config(peer_url, tls_url=None, ca_file=None, default=True):
    """The plain backend with every GD_ROUTES route; the TLS backend and its routes
    only with *tls_url*; the default route on the plain backend unless *default* is False."""
    cfg = {
        "auth_token": TF_ROUTER_TOKEN,
        "backends": {
            GD_BACKEND: {"kind": "passthrough", "base_url": peer_url, "idle_timeout": GD_IDLE_S,
                         "api_key": TF_KEY_PASS, "auth_header": "x-api-key"},
        },
        "routes": {},
    }
    for name in GD_ROUTES:
        cfg["routes"]["claude-tf-" + name] = {"backend": GD_BACKEND, "model": "tf-" + name}
    if tls_url is not None:
        cfg["backends"][GD_TLS_BACKEND] = {"kind": "passthrough", "base_url": tls_url,
                                           "idle_timeout": GD_IDLE_S, "ca_file": ca_file,
                                           "api_key": TF_KEY_PASS, "auth_header": "x-api-key"}
        for name in GD_TLS_ROUTES:
            cfg["routes"]["claude-tf-" + name] = {"backend": GD_TLS_BACKEND, "model": "tf-" + name}
    if default:
        cfg["default"] = {"backend": GD_BACKEND, "model": GD_DEFAULT_MODEL}
    return cfg


def gd_requests(peer, model):
    """The requests *peer* recorded whose JSON body names *model*."""
    found = []
    for request in list(peer.requests):
        try:
            body = json.loads((request.get("body") or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("model") == model:
            found.append((request, body))
    return found


def gd_one_request(peer, model):
    """((request, body) or None, problems): exactly one request for *model*."""
    found = gd_requests(peer, model)
    if len(found) != 1:
        return None, ["the peer recorded %d request(s) for upstream model %s, expected 1"
                      % (len(found), model)]
    return found[0], []


def gd_json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, AttributeError):
        return None


def gd_envelope_checks(label, status, hdrs, body, want_status, want_type):
    """Problems unless the answer is a *want_status* JSON envelope of *want_type* with no sentinel."""
    problems = answer_status(label, status, body, want_status)
    if problems:
        return problems
    etype, msg, why = gb_envelope(body)
    if why:
        return ["%s: %s" % (label, why)]
    if etype != want_type:
        problems.append("%s: error.type %r, expected %r" % (label, etype, want_type))
    ctype = (hdrs.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        problems.append("%s: Content-Type %r, expected application/json" % (label, ctype))
    if any(value in gb_text(body) for value in SENTINELS):
        problems.append("%s: a sentinel occurs in the answer body" % label)
    return problems


def group_d(suite, fixture_root):
    """D. passthrough: an Anthropic-compatible backend, relayed (Step 8).

    One live router (--debug) in front of two passthrough peers: a plain one
    whose script is chosen by the upstream model (route claude-tf-<n> -> model
    tf-<n>), and a verified-TLS one for D12.  D10's second half runs its own
    router without a default route.  Before the passthrough adapter is wired
    every routed request is the 501 "backend kind passthrough is not wired yet":
    each case FAILS naming that status, never crashes the group.
    """
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_d", SERVER)
    except Exception:  # noqa: BLE001 -- only D13's limit needs it; the fallback is the plan's value
        mod = None
    limit = getattr(mod, "_SSE_EVENT_LIMIT", None)
    limit_note = "_SSE_EVENT_LIMIT"
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1024:
        limit, limit_note = GD_EVENT_LIMIT_FALLBACK, "the plan's 2 MiB (the module has no _SSE_EVENT_LIMIT)"
    sandbox = new_sandbox(fixture_root, "pass")
    peer = ScriptedPeer(script=gd_dispatch(gd_scripts(limit)))
    tpeer = ScriptedPeer(script=gd_dispatch(gd_tls_scripts()), tls=True)
    proc = None
    try:
        ca_file = sandbox_ca_file(sandbox)
        cfgpath = write_config(sandbox, gd_config(peer.url(), tpeer.url(), ca_file))
        proc = RouterProc(sandbox, cfgpath, label="pass", extra_argv=("--debug",))
        port = proc.wait_ready()
        client = proc.client() if port is not None else None
        why = gb_not_ready(proc) if port is None else None

        def live(fn):
            def case():
                if client is None:
                    return [why], []
                return fn()
            return case

        def d1():
            sent = gd_d1_body()
            status, _h, body = client.post(obj=sent)
            problems = answer_status("the client", status, body, 200)
            got, more = gd_one_request(peer, "tf-d1")
            problems += more
            if got is not None:
                request, up = got
                want = dict(sent)
                want["model"] = "tf-d1"
                if up != want:
                    keys = sorted(k for k in set(up) | set(want) if up.get(k) != want.get(k))
                    problems.append("the upstream body differs from the client's beyond the model: "
                                    "key(s) %s" % ", ".join(keys))
                if request["line"] != "POST /v1/messages HTTP/1.1":
                    problems.append("upstream request line %r, expected POST /v1/messages HTTP/1.1"
                                    % request["line"][:80])
            if status == 200 and gd_json(body) != GD_ANSWER:
                problems.append("the client's 200 body is not the peer's answer")
            return problems, ["client      : %d top-level key(s) incl. system list, tools, thinking, "
                              "metadata, tf_future_field" % len(sent),
                              "answer      : status %r" % status]

        def d2():
            status, _h, body = client.post(obj=gd_body("claude-tf-d2"), headers=GD_CLIENT_HEADERS)
            problems = answer_status("the client", status, body, 200)
            got, more = gd_one_request(peer, "tf-d2")
            problems += more
            names = []
            if got is not None:
                request = got[0]
                pairs = [(k.lower(), v) for k, v in request["headers"]]
                names = [k for k, _v in pairs]
                for name in GD_FORWARDED:
                    values = [v for k, v in pairs if k == name]
                    if values != [GD_CLIENT_HEADERS[name]]:
                        problems.append("%s reached the upstream %d time(s)%s, expected once with "
                                        "the client's value" % (name, len(values),
                                                                "" if len(values) != 1 else " (value differs)"))
                keys = [v for k, v in pairs if k == "x-api-key"]
                if keys != [TF_KEY_PASS]:
                    problems.append("x-api-key: %d value(s), expected exactly the backend's TF_KEY_PASS"
                                    % len(keys))
                extra = sorted(set(names) - GD_ROUTER_OWNED - set(GD_FORWARDED) - {"x-api-key"})
                if extra:
                    problems.append("header(s) outside the allow-list reached the upstream: %s"
                                    % ", ".join(extra))
                agents = [v for k, v in pairs if k == "user-agent"]
                if GD_CLIENT_HEADERS["User-Agent"] in agents:
                    problems.append("the client's User-Agent reached the upstream")
                for label, value in GI_SENTINEL_LABELS:
                    if label != "TF_KEY_PASS" and any(value in v for _k, v in pairs):
                        problems.append("a header carries %s" % label)
            # A forwardable header sent twice is ambiguous (ADR 0015): a 400 naming
            # the header, never a value, and nothing reaches the upstream.
            dup_pairs = gb_auth_pairs(client) + [("anthropic-beta", GD_DUP_BETA[0]),
                                                 ("anthropic-beta", GD_DUP_BETA[1])]
            dup_status, _dh, _closed, raw = raw_exchange(
                client, dup_pairs, json.dumps(gd_body("claude-tf-d2")).encode("utf-8"))
            dup_body = raw.partition(b"\r\n\r\n")[2]
            problems += answer_status("duplicate anthropic-beta", dup_status, dup_body, 400)
            if dup_status == 400:
                etype, msg, why = gb_envelope(dup_body)
                if why:
                    problems.append("duplicate anthropic-beta: %s" % why)
                else:
                    if etype != "invalid_request_error":
                        problems.append("duplicate anthropic-beta: error.type %r, expected "
                                        "'invalid_request_error'" % etype)
                    if "anthropic-beta" not in msg:
                        problems.append("duplicate anthropic-beta: the message does not name the header")
                    if any(v in msg for v in GD_DUP_BETA):
                        problems.append("duplicate anthropic-beta: the message carries a header value")
            if len(gd_requests(peer, "tf-d2")) > 1:
                problems.append("a request with a duplicate anthropic-beta reached the upstream")
            return problems, ["client sent : %s, plus Authorization" % ", ".join(sorted(
                                  k.lower() for k in GD_CLIENT_HEADERS)),
                              "upstream got: %s" % (", ".join(names) or "(no request)"),
                              "anthropic-beta twice: status %r" % dup_status]

        def d3():
            conn, resp, problems = open_stream(client, gd_body("claude-tf-d3", stream=True))
            if problems:
                return problems, ["peer        : %d SSE lines (with an upstream ping)" % len(GD_STREAM_LINES)]
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            got = out["lines"]
            if got != GD_STREAM_LINES:
                first = next((i for i, (a, b) in enumerate(zip(got, GD_STREAM_LINES)) if a != b),
                             min(len(got), len(GD_STREAM_LINES)))
                problems.append("the client's lines differ from the peer's: %d vs %d line(s), first "
                                "difference at line %d (%r vs %r)"
                                % (len(got), len(GD_STREAM_LINES), first,
                                   gi_redact(got[first] if first < len(got) else "<end>")[:60],
                                   (GD_STREAM_LINES[first] if first < len(GD_STREAM_LINES) else "<end>")[:60]))
            return problems, ["peer        : %d SSE lines, spaced JSON, raw UTF-8" % len(GD_STREAM_LINES),
                              "client      : %d line(s): %s" % (len(got), ev_shape(out["events"]))]

        def d4():
            conn, resp, problems = open_stream(client, gd_body("claude-tf-d4", stream=True))
            if problems:
                return problems, []
            t0 = time.monotonic()
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            elapsed = time.monotonic() - t0
            events = out["events"]
            names = ev_names(events)
            if out["comments"]:
                problems.append("%d comment line(s) reached the client raw: %r"
                                % (len(out["comments"]), out["comments"][:3]))
            if not names or names[0] != "message_start":
                problems.append("the first client event is %r, expected message_start (no ping "
                                "before the first event, L13)" % (names[0] if names else None))
            pings = names.count("ping")
            if pings != GD_D4_PINGS:
                problems.append("%d ping(s) over a %.1f s upstream silence of comments only, expected %d "
                                "(one per _PING_INTERVAL_S since the last write)"
                                % (pings, sum(GD_D4_GAPS), GD_D4_PINGS))
            want = [n for n, _d in an_events(AN_HEAD + AN_DELTA + AN_FINISH)]
            if [n for n in names if n != "ping"] != want:
                problems.append("the relayed events are %s, expected %s plus the ping(s)"
                                % (ev_shape(events), ", ".join(want)))
            problems += ev_unparsed(events)
            return problems, ["peer        : ': tf-pre-start', message_start, 3 comments over %.1f s, "
                              "then the rest" % sum(GD_D4_GAPS),
                              "client      : %s in %.1f s" % (ev_shape(events), elapsed)]

        def d5():
            conn, resp, problems = open_stream(client, gd_body("claude-tf-d5", stream=True))
            if problems:
                return problems, []
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            events = out["events"]
            problems += ev_ends_in_error("EOF before message_stop", out)
            want = an_events(AN_HEAD + AN_DELTA)
            if [(n, d) for n, d, _t in events[:-1]] != want:
                problems.append("the events before the error are not the peer's %d events unchanged"
                                % len(want))
            return problems, ["peer        : message_start, content_block_start, one delta, then EOF",
                              "client      : %s" % ev_shape(events)]

        def d6():
            sent = {"model": "claude-tf-d6", "system": "tf sys",
                    "messages": [{"role": "user", "content": "tf count ✓"}]}
            status, _h, body = client.post(path="/v1/messages/count_tokens", obj=sent)
            problems = answer_status("the client", status, body, 200)
            if status == 200 and gd_json(body) != {"input_tokens": GD_COUNT}:
                problems.append("the client's body is not the peer's {\"input_tokens\": %d}" % GD_COUNT)
            got, more = gd_one_request(peer, "tf-d6")
            problems += more
            if got is not None:
                request, up = got
                want = dict(sent)
                want["model"] = "tf-d6"
                if up != want:
                    problems.append("the upstream body differs from the client's beyond the model")
                if request["line"] != "POST /v1/messages/count_tokens HTTP/1.1":
                    problems.append("upstream request line %r, expected POST /v1/messages/count_tokens"
                                    % request["line"][:80])
            return problems, ["answer      : status %r" % status]

        def d7():
            status, hdrs, body = client.post(obj=gd_body("claude-tf-d7"))
            problems = gd_envelope_checks("upstream 400 billing_error", status, hdrs, body, 400,
                                          "billing_error")
            _t, msg, _why = gb_envelope(body)
            if not problems and msg is not None:
                if GI_REDACTED not in msg:
                    problems.append("the message %r holds no %s" % (gi_redact(msg)[:160], GI_REDACTED))
                if GD_D7_VISIBLE not in msg:
                    problems.append("the message %r lost the upstream's own text %r"
                                    % (gi_redact(msg)[:160], GD_D7_VISIBLE))
            return problems, ["upstream    : 400 billing_error, message carrying TF_KEY_PASS",
                              "answer      : status %r, %s" % (status, gi_redact(msg or "")[:100])]

        def d8():
            problems = []
            shown = []
            for model, value, want in (("claude-tf-d8", "7", "7"), ("claude-tf-d8b", "7; tf-bad", None)):
                status, hdrs, body = client.post(obj=gd_body(model))
                label = "Retry-After %r" % value
                problems += gd_envelope_checks(label, status, hdrs, body, 429, "rate_limit_error")
                got = hdrs.get("retry-after")
                if status == 429 and got != want:
                    problems.append("%s: the answer's retry-after is %r, expected %r" % (label, got, want))
                shown.append("%s -> %r retry-after=%r" % (value, status, got))
            return problems, ["answers     : %s" % "; ".join(shown)]

        def d9():
            problems = []
            shown = []
            for model, up in (("claude-tf-d9", 401), ("claude-tf-d9b", 403)):
                status, hdrs, body = client.post(obj=gd_body(model))
                label = "upstream %d" % up
                problems += gd_envelope_checks(label, status, hdrs, body, 502, "api_error")
                if "www-authenticate" in hdrs:
                    problems.append("%s: the answer carries WWW-Authenticate" % label)
                shown.append("%d -> %r" % (up, status))
            return problems, ["answers     : %s (KD-8: never the router's own 401)" % ", ".join(shown)]

        def d10():
            status, _h, body = client.post(obj=gd_body(GD_UNROUTED))
            problems = answer_status("unknown model with a default", status, body, 200)
            _got, more = gd_one_request(peer, GD_DEFAULT_MODEL)
            problems += ["with a default: " + p for p in more]
            nsandbox = new_sandbox(fixture_root, "pass-nodefault")
            with ScriptedPeer(script=[("respond_json", 200, GD_ANSWER)]) as npeer:
                cfg = gd_config(npeer.url(), default=False)
                nproc = RouterProc(nsandbox, write_config(nsandbox, cfg), label="pass-nodefault")
                try:
                    if nproc.wait_ready() is None:
                        problems.append("without a default: " + gb_not_ready(nproc))
                        nstatus = None
                    else:
                        nstatus, nhdrs, nbody = nproc.client().post(obj=gd_body(GD_UNROUTED))
                        problems += gd_envelope_checks("without a default", nstatus, nhdrs, nbody,
                                                       404, "not_found_error")
                finally:
                    nproc.close()
            if npeer.requests:
                problems.append("without a default: the peer recorded %d request(s), expected none"
                                % len(npeer.requests))
            return problems, ["with default: %s -> status %r, upstream model %s"
                              % (GD_UNROUTED, status, GD_DEFAULT_MODEL),
                              "no default  : status %r" % nstatus]

        def d11():
            status, _h, body = client.post(obj=gd_body("claude-tf-d11"))
            problems = answer_status("the client", status, body, 200)
            got = gd_json(body) if status == 200 else None
            if status == 200:
                if got != GD_ANSWER:
                    problems.append("the client's body is not the peer's answer unchanged")
                if isinstance(got, dict) and got.get("model") != AN_UPSTREAM_MODEL:
                    problems.append("model %r, expected the upstream's %r unchanged (KD-16)"
                                    % (got.get("model"), AN_UPSTREAM_MODEL))
            return problems, ["model       : requested claude-tf-d11, routed tf-d11, upstream answered %s"
                              % AN_UPSTREAM_MODEL,
                              "answer      : status %r model %r"
                              % (status, got.get("model") if isinstance(got, dict) else None)]

        def d12():
            problems = []
            shown = []
            conn, resp, more = open_stream(client, gd_body("claude-tf-d12", stream=True))
            if more:
                problems += ["complete + ragged EOF: " + p for p in more]
            else:
                try:
                    out = read_stream(resp)
                finally:
                    close_stream(conn, resp)
                names = ev_names(out["events"])
                if not names or names[-1] != "message_stop":
                    problems.append("complete + ragged EOF: the stream ends with %r, expected "
                                    "message_stop (%s)" % (names[-1] if names else None,
                                                           ev_shape(out["events"])))
                if "error" in names:
                    problems.append("complete + ragged EOF: an event: error followed a complete "
                                    "stream (M10: the relay closes on message_stop)")
                if out["lines"] != GD_STREAM_LINES:
                    problems.append("complete + ragged EOF: the client's lines are not the peer's")
                shown.append("complete: %s" % ev_shape(out["events"]))
            # Control: the same ragged EOF before message_stop must be a truncation, so the
            # pass above is not a relay that hides every TLS EOF error.
            conn, resp, more = open_stream(client, gd_body("claude-tf-d12b", stream=True))
            if more:
                problems += ["control (ragged EOF before message_stop): " + p for p in more]
            else:
                try:
                    out = read_stream(resp)
                finally:
                    close_stream(conn, resp)
                problems += ev_ends_in_error("control (ragged EOF before message_stop)", out,
                                             needle="cut")
                shown.append("control: %s" % ev_shape(out["events"]))
            if tpeer.errors:
                problems.append("the TLS peer recorded error(s): %s" % "; ".join(tpeer.errors[:2]))
            return problems, ["peer        : verified TLS, the stream then TCP dropped without "
                              "close_notify"] + ["client      : " + s for s in shown]

        def d13():
            conn, resp, problems = open_stream(client, gd_body("claude-tf-d13", stream=True))
            if problems:
                return problems, ["event       : %d bytes (%s + 1)" % (limit + 1, limit_note)]
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            problems += ev_ends_in_error("oversized event", out, needle="oversized event")
            leaked = sum(1 for line in out["lines"] if GD_D13_MARKER in line)
            if leaked:
                problems.append("%d line(s) of the oversized event reached the client" % leaked)
            names = ev_names(out["events"])
            if names[:1] != ["message_start"] or len(names) != 2:
                problems.append("client events %s, expected message_start then the one error"
                                % ev_shape(out["events"]))
            total = sum(len(line) + 1 for line in out["lines"])
            if total >= limit:
                problems.append("the client received %d bytes, not less than the event limit" % total)
            return problems, ["event       : %d bytes over data: lines of <= %d (%s + 1)"
                              % (limit + 1, GD_D13_LINE, limit_note),
                              "client      : %s, %d bytes" % (ev_shape(out["events"]), total)]

        for cid, fn in zip(GD_CASES, (d1, d2, d3, d4, d5, d6, d7, d8, d9, d10, d11, d12, d13)):
            try:
                results[cid] = live(fn)()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s"
                                 % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
        try:
            results[GD_CASES[13]] = gd_d14(mod)      # in-process: needs no live router
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            results[GD_CASES[13]] = (["case raised %s: %s"
                                      % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001 -- a setup failure fails every case not yet run
        setup = "the group setup raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    finally:
        if proc is not None:
            proc.close()
        peer.close()
        tpeer.close()
    for cid in GD_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GD, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group E: llamacpp
# ---------------------------------------------------------------------------

GE_CASES = (
    "tool-results-first reorders",       # E1
    "tool-results-first: no-op",         # E2
    "hoist-system joins, no cache",      # E3
    "adaptive-thinking -> enabled",      # E4
    "sampling-override",                 # E5
    "every row idempotent, pure",        # E6
    "tool-use-input adds input {}",      # E7
    "error shapes rewritten",            # E8
    "count_tokens with quirks",          # E9
    "stream replay, model echoed",       # E10
    "quirks_off honoured",               # E11
    "registry rows = six names",         # E12
    "default route echoes model",        # E13
)

# E12: the six names Step 4 wrote provisionally, as a LITERAL -- never the module's
# _RT_LL_QUIRK_NAMES, which Step 9 derives from the registries (a tautology).
GE_REQUEST_ROWS = ("tool-results-first", "hoist-system", "adaptive-thinking", "sampling-override")
GE_EVENT_ROWS = ("tool-use-input", "error-shape")
GE_QUIRK_NAMES = frozenset({"tool-results-first", "hoist-system", "adaptive-thinking",
                            "sampling-override", "tool-use-input", "error-shape"})
GE_REGISTRIES = (("LLAMACPP_REQUEST_QUIRKS", GE_REQUEST_ROWS), ("LLAMACPP_EVENT_QUIRKS", GE_EVENT_ROWS))

GE_BACKEND = "tfe"
GE_DEFAULT_MODEL = "tf-e-default"
GE_IDLE_S = 30
GE_COUNT = 777                               # E9: the peer's input_tokens
GE_LLAMA_MODEL = "tf-llamacpp-model.gguf"    # the model the llama.cpp fixtures' message_start carries
GE_THINKING_DEFAULT = 8192                   # adaptive-thinking's budget without a route thinking_budget
# Until M4 runs, every fixture is synthetic (tests/files/llm_router/README.md).
GE_DEFENSIVE = "provenance  : defensive, not observed (synthetic fixture, pending M4)"
GE_FIXTURES = {"stream_tool": "tf_llamacpp_stream_tool.sse",
               "stream_error": "tf_llamacpp_stream_error.sse",
               "error_json": "tf_llamacpp_error.json"}
GE_E8_NEEDLE = "exceeds the available context size"     # tf_llamacpp_error.json's message
GE_E8S_NEEDLE = "server is shutting down"               # tf_llamacpp_stream_error.sse's message

GE_ANSWER = {"id": "msg_tfll_json_0123456789abcdef", "type": "message", "role": "assistant",
             "model": GE_LLAMA_MODEL, "content": [{"type": "text", "text": "ok ✓"}],
             "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 3, "output_tokens": 2}}


def ge_tool_result(tid, text):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


def ge_tool_use(tid, path):
    return {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}


GE_TR_A = ge_tool_result("toolu_tfe_a", "tf result a")
GE_TR_B = ge_tool_result("toolu_tfe_b", "tf result b")
GE_NOTE_1 = {"type": "text", "text": "tf note 1"}
GE_NOTE_2 = {"type": "text", "text": "tf note 2 ✓"}
GE_SYSTEM_LIST = [{"type": "text", "text": "tf sys one", "cache_control": {"type": "ephemeral"}},
                  {"type": "text", "text": "tf sys two"}]
GE_SYSTEM_JOINED = "tf sys one\n\ntf sys two"


def ge_history(last_user):
    """A three-message history whose last user message has *last_user* as its content."""
    return [{"role": "user", "content": "tf start"},
            {"role": "assistant", "content": [{"type": "text", "text": "calling"},
                                              ge_tool_use("toolu_tfe_a", "/tf/a"),
                                              ge_tool_use("toolu_tfe_b", "/tf/b")]},
            {"role": "user", "content": last_user}]


GE_MIXED = [GE_NOTE_1, GE_TR_A, GE_NOTE_2, GE_TR_B]      # tool_result blocks interleaved with text
GE_ORDERED = [GE_TR_A, GE_TR_B, GE_NOTE_1, GE_NOTE_2]    # the same blocks, tool results first


def ge_body(route, stream=False, messages=None, **extra):
    body = {"model": route, "max_tokens": 64, "stream": stream,
            "messages": messages if messages is not None else [{"role": "user", "content": "tf e"}]}
    body.update(extra)
    return body


def ge_clone(obj):
    """A deep copy of a JSON value (every quirk input is JSON)."""
    return json.loads(json.dumps(obj))


def ge_types(content):
    return ", ".join(b.get("type", "?") if isinstance(b, dict) else type(b).__name__
                     for b in content) if isinstance(content, list) else repr(content)[:40]


def ge_row(mod, name):
    """(fn, None) of the registry row *name* in either llama.cpp registry, or (None, why)."""
    for reg, _names in GE_REGISTRIES:
        rows = getattr(mod, reg, None)
        if not isinstance(rows, (tuple, list)):
            continue
        for row in rows:
            if isinstance(row, tuple) and len(row) == 3 and row[0] == name:
                return (row[2], None) if callable(row[2]) else (None, "registry row %r has no callable" % name)
    return None, ("the module has no quirk row %r (LLAMACPP_REQUEST_QUIRKS / LLAMACPP_EVENT_QUIRKS)"
                  % name)


def ge_route(mod, **options):
    """A RouteSpec on the llama.cpp backend with validated-shape *options*."""
    return mod.RouteSpec(name="claude-tf-e", backend=GE_BACKEND, model="tf-e", options=dict(options))


def ge_scrubber(mod):
    """(scrub, None): the router's own KD-9 scrubber over the suite's sentinels, or (None, why)."""
    make = getattr(mod, "_rt_make_scrubber", None)
    if not callable(make):
        return None, "the module defines no _rt_make_scrubber"
    return make(tuple(s.encode("ascii") for s in SENTINELS)), None


def ge_call(fn, args, label):
    """(out, problems): fn(*args) with args[0] (a body, or an event's data at args[1]) checked unmutated."""
    pos = 0 if len(args) == 2 else 1
    before = ge_clone(args[pos])
    out = fn(*args)
    problems = []
    if args[pos] != before:
        problems.append("%s: the quirk mutated its input" % label)
    if not isinstance(out, dict):
        problems.append("%s: returned %s, expected a dict" % (label, type(out).__name__))
    return out, problems


def ge_diff_keys(got, want):
    if not isinstance(got, dict):
        return "(not a dict)"
    return ", ".join(sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))) or "(none)"


def ge_fixtures():
    """{"stream_tool": [lines], "stream_error": [lines], "error_json": bytes} from tests/files/llm_router."""
    out = {}
    for key, name in GE_FIXTURES.items():
        with open(os.path.join(FIXTURES, name), "rb") as fh:
            data = fh.read()
        if name.endswith(".json"):
            json.loads(data.decode("utf-8"))           # a broken fixture fails here, not in the peer
            out[key] = data.rstrip(b"\n")
        else:
            lines = data.decode("utf-8").split("\n")
            if lines and lines[-1] == "":
                lines.pop()                             # the file's final newline
            out[key] = lines
    return out


def ge_scripts(fx):
    """(upstream model, stream) -> the llama.cpp peer's steps."""
    answer = [("respond_json", 200, GE_ANSWER)]
    scripts = {("tf-e9", False): [("respond_json", 200, {"input_tokens": GE_COUNT})],
               ("tf-e11", False): answer, ("tf-e11b", False): answer,
               (GE_DEFAULT_MODEL, False): answer}
    if fx:
        scripts.update({("tf-e8", False): [("respond_json", 400, fx["error_json"])],
                        ("tf-e8s", True): [("respond_sse", fx["stream_error"])],
                        ("tf-e10", True): [("respond_sse", fx["stream_tool"])],
                        (GE_DEFAULT_MODEL, True): [("respond_sse", fx["stream_tool"])]})
    return scripts


def ge_dispatch(scripts):
    """A ScriptedPeer script choosing its steps by the upstream body's (model, stream)."""
    def script(_index, request):
        try:
            body = json.loads((request.get("body") or b"").decode("utf-8"))
            key = (body.get("model"), bool(body.get("stream")))
        except (UnicodeDecodeError, ValueError, AttributeError):
            key = None
        return scripts.get(key, [("respond_json", 404, {"error": {"code": 404, "message": "tf no script",
                                                                   "type": "not_found_error"}})])
    return script


def ge_config(peer_url):
    """One llama.cpp backend (no key: auth_header none), the E routes, a default route."""
    routes = {"claude-tf-" + n: {"backend": GE_BACKEND, "model": "tf-" + n}
              for n in ("e8", "e8s", "e9", "e10", "e11b")}
    routes["claude-tf-e11"] = {"backend": GE_BACKEND, "model": "tf-e11",
                               "options": {"quirks_off": ["tool-results-first"]}}
    return {"auth_token": TF_ROUTER_TOKEN,
            "backends": {GE_BACKEND: {"kind": "llamacpp", "base_url": peer_url,
                                      "idle_timeout": GE_IDLE_S}},
            "routes": routes,
            "default": {"backend": GE_BACKEND, "model": GE_DEFAULT_MODEL}}


def ge_expected_events(lines, requested):
    """[(event, parsed data)] the client must see for the llama.cpp stream *lines*:
    message_start's model echoed as *requested* (KD-16), a tool_use block start
    given "input": {} (tool-use-input), every other event unchanged."""
    want = []
    for name, data in an_events(lines):
        obj = json.loads(data)
        if name == "message_start":
            obj["message"]["model"] = requested
        block = obj.get("content_block") if name == "content_block_start" else None
        if isinstance(block, dict) and block.get("type") == "tool_use" and "input" not in block:
            block["input"] = {}
        want.append((name, obj))
    return want


def ge_check_replay(label, out, lines, requested):
    """Problems unless the client stream is the fixture *lines* relayed whole, as
    ge_expected_events says, all block stops at the end included."""
    events = out["events"]
    problems = ev_unparsed(events)
    want = ge_expected_events(lines, requested)
    names = ev_names(events)
    if names != [n for n, _o in want]:
        problems.append("%s: client events %s, expected the fixture's %d events %s"
                        % (label, ev_shape(events), len(want), ", ".join(n for n, _o in want)))
    bad = []
    for i, ((name, data, _t), (_wn, wobj)) in enumerate(zip(events, want)):
        try:
            got = json.loads(data)
        except ValueError:
            continue
        if got != wobj:
            bad.append("%d %s" % (i, name))
    if bad:
        problems.append("%s: event(s) differ from the fixture's (model echo and input {} applied): %s"
                        % (label, "; ".join(bad[:4])))
    model = None
    if names[:1] == ["message_start"]:
        try:
            model = (json.loads(events[0][1]).get("message") or {}).get("model")
        except (ValueError, AttributeError):
            model = None
    if model != requested:
        problems.append("%s: message_start.message.model %r, expected the requested %r"
                        % (label, model, requested))
    return problems


def group_e(suite, fixture_root):
    """E. llamacpp: the quirk registries in-process, a llama.cpp peer live (Step 9).

    E1-E7 and E12 call the registry rows (LLAMACPP_REQUEST_QUIRKS /
    LLAMACPP_EVENT_QUIRKS) in-process; E6 runs every row twice on every sample,
    event rows with the router's own scrubber over the suite's sentinels.  E8
    has an in-process half (both error shapes, the conforming envelope) and a
    live half (the non-stream and the mid-stream fixture).  E9-E11 and E13 run
    one live --debug router in front of a llama.cpp peer chosen by the
    upstream (model, stream).  The fixtures are synthetic until M4 runs: every
    case that replays one says so.  Before Step 9 lands, every routed request
    is the 501 "backend kind llamacpp is not wired yet" and the registries do
    not exist: each case FAILS naming that, never crashes the group.
    """
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_e", SERVER)
        mod_why = None
    except Exception as exc:  # noqa: BLE001 -- the in-process cases fail naming it
        mod = None
        mod_why = "cannot import %s: %s" % (os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__)
    try:
        fx = ge_fixtures()
        fx_why = None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fx = {}
        fx_why = "the llama.cpp fixtures under tests/files/llm_router cannot be read: %s" % type(exc).__name__
    sandbox = new_sandbox(fixture_root, "llamacpp")
    peer = ScriptedPeer(script=ge_dispatch(ge_scripts(fx)))
    proc = None
    try:
        cfgpath = write_config(sandbox, ge_config(peer.url()))
        proc = RouterProc(sandbox, cfgpath, label="llamacpp", extra_argv=("--debug",))
        port = proc.wait_ready()
        client = proc.client() if port is not None else None
        why = gb_not_ready(proc) if port is None else None

        def inproc(fn):
            def case():
                if mod is None:
                    return [mod_why], []
                return fn()
            return case

        def live(fn):
            def case():
                if client is None:
                    return [why], []
                return fn()
            return case

        def replayed(fn):
            def case():
                if fx_why:
                    return [fx_why], [GE_DEFENSIVE]
                problems, detail = live(fn)()
                return problems, detail + [GE_DEFENSIVE]
            return case

        def e1():
            fn, no = ge_row(mod, "tool-results-first")
            if fn is None:
                return [no], []
            sent = ge_body("claude-tf-e1", messages=ge_history(GE_MIXED))
            out, problems = ge_call(fn, (ge_clone(sent), ge_route(mod)), "interleaved tool results")
            want = ge_clone(sent)
            want["messages"][2]["content"] = ge_clone(GE_ORDERED)
            got = out.get("messages") if isinstance(out, dict) else None
            last = got[2].get("content") if isinstance(got, list) and len(got) == 3 \
                and isinstance(got[2], dict) else None
            if out != want:
                problems.append("the result is not the body with the last user message's tool_result "
                                "blocks first, each group in its order (differing key(s): %s; last user "
                                "content: %s)" % (ge_diff_keys(out, want), ge_types(last)))
            return problems, ["user content: %s -> %s" % (ge_types(GE_MIXED), ge_types(last))]

        def e2():
            fn, no = ge_row(mod, "tool-results-first")
            if fn is None:
                return [no], []
            problems = []
            samples = (("tool results already first", ge_body("claude-tf-e2", messages=ge_history(GE_ORDERED))),
                       ("string and text-only content", ge_body("claude-tf-e2", messages=[
                           {"role": "user", "content": "tf plain"},
                           {"role": "assistant", "content": "ok"},
                           {"role": "user", "content": [GE_NOTE_1, GE_NOTE_2]}])))
            for label, sent in samples:
                out, more = ge_call(fn, (ge_clone(sent), ge_route(mod)), label)
                problems += more
                if out != sent:
                    problems.append("%s: conforming input changed (key(s) %s)" % (label, ge_diff_keys(out, sent)))
            return problems, ["inputs      : %s" % "; ".join(label for label, _s in samples)]

        def e3():
            fn, no = ge_row(mod, "hoist-system")
            if fn is None:
                return [no], []
            problems = []
            sent = ge_body("claude-tf-e3", system=ge_clone(GE_SYSTEM_LIST))
            out, problems = ge_call(fn, (ge_clone(sent), ge_route(mod)), "list system")
            want = ge_clone(sent)
            want["system"] = GE_SYSTEM_JOINED
            got = out.get("system") if isinstance(out, dict) else None
            if out != want:
                problems.append("list system: system became %r, expected %r with nothing else changed "
                                "(key(s) %s)" % (got if isinstance(got, str) else ge_types(got),
                                                 GE_SYSTEM_JOINED, ge_diff_keys(out, want)))
            if "cache_control" in json.dumps(got):
                problems.append("list system: cache_control survived in system")
            for label, sent in (("string system", ge_body("claude-tf-e3", system="tf sys")),
                                ("no system", ge_body("claude-tf-e3"))):
                same, more = ge_call(fn, (ge_clone(sent), ge_route(mod)), label)
                problems += more
                if same != sent:
                    problems.append("%s: conforming input changed" % label)
            return problems, ["system      : %d text blocks with cache_control -> %r"
                              % (len(GE_SYSTEM_LIST), got if isinstance(got, str) else ge_types(got))]

        def e4():
            fn, no = ge_row(mod, "adaptive-thinking")
            if fn is None:
                return [no], []
            adaptive = {"type": "adaptive"}
            absent = object()
            table = (
                ("budget 1024, max_tokens 4096", adaptive, 4096, {"thinking_budget": 1024},
                 {"type": "enabled", "budget_tokens": 1024}),
                ("budget 1024 clamped by max_tokens 512", adaptive, 512, {"thinking_budget": 1024},
                 {"type": "enabled", "budget_tokens": 511}),
                ("no budget, max_tokens 16000", adaptive, 16000, {},
                 {"type": "enabled", "budget_tokens": GE_THINKING_DEFAULT}),
                ("no budget clamped by max_tokens 100", adaptive, 100, {},
                 {"type": "enabled", "budget_tokens": 99}),
                ("max_tokens 1 drops thinking", adaptive, 1, {"thinking_budget": 1024}, absent),
                ("enabled kept", {"type": "enabled", "budget_tokens": 2048}, 4096, {"thinking_budget": 1024},
                 None),
                ("disabled kept", {"type": "disabled"}, 4096, {}, None),
            )
            problems, shown = [], []
            for label, thinking, max_tokens, options, want_thinking in table:
                sent = ge_body("claude-tf-e4", thinking=ge_clone(thinking))
                sent["max_tokens"] = max_tokens
                out, more = ge_call(fn, (ge_clone(sent), ge_route(mod, **options)), label)
                problems += more
                want = ge_clone(sent)
                if want_thinking is absent:
                    del want["thinking"]
                elif want_thinking is not None:
                    want["thinking"] = want_thinking
                got = out.get("thinking", "(absent)") if isinstance(out, dict) else None
                if out != want:
                    problems.append("%s: thinking %r, expected %r (differing key(s): %s)"
                                    % (label, got, want.get("thinking", "(absent)"), ge_diff_keys(out, want)))
                shown.append("%s -> %r" % (label, got))
            return problems, ["case        : " + s for s in shown[:5]]

        def e5():
            fn, no = ge_row(mod, "sampling-override")
            if fn is None:
                return [no], []
            table = (
                ("all three options", {"temperature": 0.2, "top_p": 0.9, "top_k": 40},
                 {"temperature": 1}, {"temperature": 0.2, "top_p": 0.9, "top_k": 40}),
                ("temperature only", {"temperature": 0.2},
                 {"temperature": 1, "top_p": 0.5}, {"temperature": 0.2, "top_p": 0.5}),
                ("no option", {}, {"temperature": 1}, {"temperature": 1}),
            )
            problems = []
            for label, options, client_sampling, want_sampling in table:
                sent = ge_body("claude-tf-e5", **client_sampling)
                out, more = ge_call(fn, (ge_clone(sent), ge_route(mod, **options)), label)
                problems += more
                want = ge_clone(sent)
                want.update(want_sampling)
                if out != want:
                    got = {k: out.get(k) for k in ("temperature", "top_p", "top_k") if k in out} \
                        if isinstance(out, dict) else out
                    problems.append("%s: sampling %r, expected %r (differing key(s): %s)"
                                    % (label, got, want_sampling, ge_diff_keys(out, want)))
            return problems, ["route       : temperature/top_p/top_k override Claude Code's temperature 1"]

        def e6():
            scrub, no = ge_scrubber(mod)
            if scrub is None:
                return [no], []
            problems, shown = [], []
            route = ge_route(mod, thinking_budget=1024, temperature=0.2, top_p=0.9, top_k=40)
            adaptive = ge_body("claude-tf-e6", thinking={"type": "adaptive"})
            adaptive["max_tokens"] = 4096
            tiny = ge_clone(adaptive)
            tiny["max_tokens"] = 1
            bodies = (ge_body("claude-tf-e6", messages=ge_history(GE_MIXED)),
                      ge_body("claude-tf-e6", messages=ge_history(GE_ORDERED), system=ge_clone(GE_SYSTEM_LIST)),
                      ge_body("claude-tf-e6", system="tf sys", temperature=1, top_p=0.5),
                      adaptive, tiny)
            events = (
                ("message_start", an_message_start(GE_LLAMA_MODEL)),
                ("content_block_start", {"type": "content_block_start", "index": 1,
                                         "content_block": {"type": "tool_use", "id": "toolu_tfe6", "name": "Read"}}),
                ("content_block_start", {"type": "content_block_start", "index": 1,
                                         "content_block": ge_tool_use("toolu_tfe6", "/tf/e6")}),
                ("content_block_start", AN_BLOCK_START),
                ("content_block_delta", an_text_delta("tf e6 ✓")),
                ("error", {"code": 400, "message": "tf ctx " + TF_KEY_PASS, "type": "exceed_context_size_error"}),
                ("error", {"error": {"code": 503, "message": "tf loading " + TF_KEY_MISTRAL,
                                     "type": "unavailable_error"}}),
                ("error", gd_error("overloaded_error", "tf busy " + TF_ROUTER_TOKEN)),
            )
            for reg, names in GE_REGISTRIES:
                rows = getattr(mod, reg, None)
                if not isinstance(rows, (tuple, list)) or not rows:
                    problems.append("the module has no %s registry" % reg)
                    continue
                for row in rows:
                    if not (isinstance(row, tuple) and len(row) == 3 and callable(row[2])):
                        problems.append("%s: a row is not (name, upstream_ref, fn)" % reg)
                        continue
                    name, _ref, fn = row
                    calls = 0
                    if reg == "LLAMACPP_REQUEST_QUIRKS":
                        samples = [(lambda x, f=fn: f(x, route), b) for b in bodies]
                    else:
                        samples = [(lambda x, f=fn, e=ev: f(e, x, scrub), d) for ev, d in events]
                    for i, (call, sample) in enumerate(samples):
                        label = "%s sample %d" % (name, i)
                        x0 = ge_clone(sample)
                        try:
                            once = call(x0)
                            x1 = ge_clone(once)
                            twice = call(x1)
                        except Exception as exc:  # noqa: BLE001 -- one row's crash fails, the rest still run
                            problems.append("%s: raised %s" % (label, type(exc).__name__))
                            continue
                        if x0 != sample:
                            problems.append("%s: the input was mutated" % label)
                        if x1 != once:
                            problems.append("%s: the second call mutated its input" % label)
                        if twice != once:
                            problems.append("%s: fn(fn(x)) != fn(x)" % label)
                        calls += 2
                    shown.append("%s x%d" % (name, calls))
            return problems, ["rows        : %s" % (", ".join(shown) or "(none)"),
                              "scrub       : the router's _rt_make_scrubber over the %d sentinels" % len(SENTINELS)]

        def e7():
            fn, no = ge_row(mod, "tool-use-input")
            if fn is None:
                return [no], []
            scrub, _no = ge_scrubber(mod)
            scrub = scrub or (lambda text: text)
            bare = {"type": "content_block_start", "index": 1,
                    "content_block": {"type": "tool_use", "id": "toolu_tfe7", "name": "Read"}}
            out, problems = ge_call(fn, ("content_block_start", ge_clone(bare), scrub), "tool_use without input")
            want = ge_clone(bare)
            want["content_block"]["input"] = {}
            if out != want:
                problems.append("tool_use without input: %r, expected input {} added and nothing else changed"
                                % (out.get("content_block") if isinstance(out, dict) else out,))
            for label, event, data in (
                    ("tool_use with input", "content_block_start",
                     {"type": "content_block_start", "index": 1,
                      "content_block": ge_tool_use("toolu_tfe7", "/tf/e7")}),
                    ("text block start", "content_block_start", AN_BLOCK_START),
                    ("text delta", "content_block_delta", an_text_delta("tf e7"))):
                same, more = ge_call(fn, (event, ge_clone(data), scrub), label)
                problems += more
                if same != data:
                    problems.append("%s: conforming input changed" % label)
            return problems, ["start       : tool_use without input -> %r"
                              % ((out.get("content_block") or {}).get("input", "(absent)")
                                 if isinstance(out, dict) else out,)]

        def e8():
            problems, detail = [], []
            # In-process half: the error-shape row on both llama.cpp shapes and the conforming envelope.
            if mod is None:
                problems.append(mod_why)
            else:
                fn, no = ge_row(mod, "error-shape")
                scrub, no2 = ge_scrubber(mod)
                if fn is None or scrub is None:
                    problems += [w for w in (no, no2) if w]
                else:
                    table = (
                        ("flat {code 400}", {"code": 400, "message": "tf ctx " + TF_KEY_PASS,
                                              "type": "exceed_context_size_error"},
                         "invalid_request_error", "tf ctx " + TF_KEY_PASS),
                        ("nested {error: code 503}", {"error": {"code": 503, "message": "tf loading model",
                                                                "type": "unavailable_error"}},
                         "overloaded_error", "tf loading model"),
                        ("nested {error: code 500}", {"error": {"code": 500, "message": "tf internal",
                                                                "type": "server_error"}},
                         "api_error", "tf internal"),
                        ("flat {code 418}", {"code": 418, "message": "tf teapot", "type": "tf_type"},
                         "api_error", "tf teapot"),
                        ("conforming envelope", gd_error("rate_limit_error", "tf slow " + TF_KEY_MISTRAL),
                         "rate_limit_error", "tf slow " + TF_KEY_MISTRAL),
                    )
                    for label, data, want_type, message in table:
                        out, more = ge_call(fn, ("error", ge_clone(data), scrub), label)
                        problems += more
                        want = {"type": "error", "error": {"type": want_type, "message": scrub(message)}}
                        if out != want:
                            problems.append("in-process %s: %r, expected %r"
                                            % (label, gi_redact(json.dumps(out))[:160],
                                               gi_redact(json.dumps(want))[:160]))
                        if any(s in json.dumps(out) for s in SENTINELS):
                            problems.append("in-process %s: a sentinel survived the rewrite" % label)
                    detail.append("in-process  : %d error data shapes through error-shape" % len(table))
                    # V16: an event whose data is {"error": ...} is an error whatever its name.
                    relay_cls = getattr(mod, "LlamacppRelay", None)
                    if not callable(relay_cls) or not callable(getattr(mod, "InboundRequest", None)):
                        problems.append("the module defines no LlamacppRelay / InboundRequest")
                    else:
                        err = json.dumps({"error": {"code": 503, "message": "tf ll data-only " + TF_KEY_PASS}})
                        for label, head in (("data-only {error: code 503}", []),
                                            ("event: message_delta {error: code 503}", [b"event: message_delta\n"])):
                            relay = relay_cls(gg_inbound(mod))
                            out = b"".join(relay.feed(line)
                                           for line in head + [b"data: " + err.encode("ascii") + b"\n", b"\n"])
                            text = out.decode("utf-8", "replace")
                            events = an_events(text.split("\n"))
                            etype = None
                            if [n for n, _d in events] == ["error"]:
                                try:
                                    etype = (json.loads(events[0][1]).get("error") or {}).get("type")
                                except (ValueError, AttributeError):
                                    etype = None
                            if etype != "overloaded_error":
                                problems.append("relay %s: %s, expected one event: error of overloaded_error"
                                                % (label, gi_redact(text)[:160]))
                            if gi_redact(text) != text:
                                problems.append("relay %s: a sentinel reached the client unscrubbed" % label)
                            if not relay.closed:
                                problems.append("relay %s: the relay did not close" % label)
                        detail.append("relay       : data-only and named {error} events -> event: error")
            # Live half: the fixtures, non-stream and mid-stream.
            if fx_why:
                problems.append(fx_why)
            elif client is None:
                problems.append(why)
            else:
                status, hdrs, body = client.post(obj=ge_body("claude-tf-e8"))
                more = gd_envelope_checks("non-stream 400", status, hdrs, body, 400, "invalid_request_error")
                _t, msg, _w = gb_envelope(body)
                if not more and not (isinstance(msg, str) and GE_E8_NEEDLE in msg):
                    more.append("non-stream 400: the message %r lost the llama.cpp text %r"
                                % (gi_redact(str(msg))[:120], GE_E8_NEEDLE))
                problems += more
                conn, resp, more = open_stream(client, ge_body("claude-tf-e8s", stream=True))
                if more:
                    problems += ["mid-stream: " + p for p in more]
                    shape = "-"
                else:
                    try:
                        out = read_stream(resp)
                    finally:
                        close_stream(conn, resp)
                    problems += ev_ends_in_error("mid-stream", out, needle=GE_E8S_NEEDLE,
                                                 err_type="overloaded_error")
                    want = [n for n, _d in an_events(fx["stream_error"])][:-1]
                    if ev_names(out["events"])[:-1] != want:
                        problems.append("mid-stream: the events before the error are %s, expected %s"
                                        % (ev_shape(out["events"][:-1]), ", ".join(want)))
                    shape = ev_shape(out["events"])
                detail += ["non-stream  : %s -> status %r" % (GE_FIXTURES["error_json"], status),
                           "mid-stream  : %s -> %s" % (GE_FIXTURES["stream_error"], shape),
                           GE_DEFENSIVE]
            return problems, detail

        def e9():
            sent = {"model": "claude-tf-e9", "system": ge_clone(GE_SYSTEM_LIST),
                    "messages": ge_history(GE_MIXED)}
            status, _h, body = client.post(path="/v1/messages/count_tokens", obj=sent)
            problems = answer_status("the client", status, body, 200)
            if status == 200 and gd_json(body) != {"input_tokens": GE_COUNT}:
                problems.append("the client's body is not the peer's {\"input_tokens\": %d}" % GE_COUNT)
            got, more = gd_one_request(peer, "tf-e9")
            problems += more
            if got is not None:
                request, up = got
                want = ge_clone(sent)
                want["model"] = "tf-e9"
                want["system"] = GE_SYSTEM_JOINED
                want["messages"][2]["content"] = ge_clone(GE_ORDERED)
                if up != want:
                    problems.append("the upstream body is not the client's with the request quirks applied "
                                    "(differing key(s): %s)" % ge_diff_keys(up, want))
                if request["line"] != "POST /v1/messages/count_tokens HTTP/1.1":
                    problems.append("upstream request line %r, expected POST /v1/messages/count_tokens"
                                    % request["line"][:80])
            return problems, ["client      : list system + interleaved tool results",
                              "answer      : status %r" % status]

        def e10():
            conn, resp, problems = open_stream(client, ge_body("claude-tf-e10", stream=True))
            if problems:
                return problems, []
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            problems += ge_check_replay("exact route", out, fx["stream_tool"], "claude-tf-e10")
            _got, more = gd_one_request(peer, "tf-e10")
            problems += more
            return problems, ["peer        : %s (%s, both block stops at the end)"
                              % (GE_FIXTURES["stream_tool"], GE_LLAMA_MODEL),
                              "client      : %s" % ev_shape(out["events"])]

        def e11():
            problems, shown = [], []
            for route, upstream, reorder in (("claude-tf-e11", "tf-e11", False), ("claude-tf-e11b", "tf-e11b", True)):
                label = "quirks_off [tool-results-first]" if not reorder else "control (quirks on)"
                sent = ge_body(route, messages=ge_history(GE_MIXED))
                status, _h, body = client.post(obj=sent)
                problems += answer_status(label, status, body, 200)
                got, more = gd_one_request(peer, upstream)
                problems += ["%s: %s" % (label, p) for p in more]
                last = None
                if got is not None:
                    msgs = got[1].get("messages")
                    last = msgs[2].get("content") if isinstance(msgs, list) and len(msgs) == 3 \
                        and isinstance(msgs[2], dict) else None
                    want = GE_ORDERED if reorder else GE_MIXED
                    if last != want:
                        problems.append("%s: the peer's last user content is %s, expected %s"
                                        % (label, ge_types(last), ge_types(want)))
                shown.append("%s: %s" % (label, ge_types(last)))
            return problems, ["client      : %s" % ge_types(GE_MIXED)] + ["peer        : " + s for s in shown]

        def e13():
            problems = []
            conn, resp, more = open_stream(client, ge_body(GD_UNROUTED, stream=True))
            if more:
                problems += ["stream: " + p for p in more]
                shape = "-"
            else:
                try:
                    out = read_stream(resp)
                finally:
                    close_stream(conn, resp)
                problems += ge_check_replay("stream via the default route", out, fx["stream_tool"], GD_UNROUTED)
                shape = ev_shape(out["events"])
            status, _h, body = client.post(obj=ge_body(GD_UNROUTED))
            problems += answer_status("non-stream via the default route", status, body, 200)
            got = gd_json(body) if status == 200 else None
            model = got.get("model") if isinstance(got, dict) else None
            if status == 200 and model != GD_UNROUTED:
                problems.append("non-stream via the default route: model %r, expected the requested %r "
                                "(never default / (default) / the upstream's)" % (model, GD_UNROUTED))
            found = len(gd_requests(peer, GE_DEFAULT_MODEL))
            if found != 2:
                problems.append("the peer recorded %d request(s) for the default route's model %s, expected 2"
                                % (found, GE_DEFAULT_MODEL))
            return problems, ["requested   : %s -> default route, upstream model %s" % (GD_UNROUTED, GE_DEFAULT_MODEL),
                              "stream      : %s" % shape,
                              "non-stream  : status %r model %r" % (status, model)]

        def e12():
            problems, names, shown = [], [], []
            for reg, expected in GE_REGISTRIES:
                rows = getattr(mod, reg, None)
                if not isinstance(rows, (tuple, list)):
                    problems.append("the module defines no %s tuple" % reg)
                    continue
                here = []
                for i, row in enumerate(rows):
                    if not (isinstance(row, tuple) and len(row) == 3):
                        problems.append("%s[%d] is not a (name, upstream_ref, fn) tuple" % (reg, i))
                        continue
                    name, ref, fn = row
                    if not isinstance(name, str) or not name:
                        problems.append("%s[%d]: the name is not a non-empty str" % (reg, i))
                        continue
                    if not isinstance(ref, str) or not ref.strip():
                        problems.append("%s row %r: empty upstream_ref" % (reg, name))
                    if not callable(fn):
                        problems.append("%s row %r: fn is not callable" % (reg, name))
                    here.append(name)
                if set(here) != set(expected):
                    problems.append("%s holds %s, expected %s" % (reg, sorted(here), sorted(expected)))
                names += here
                shown.append("%s %d" % (reg, len(here)))
            dups = sorted({n for n in names if names.count(n) > 1})
            if dups:
                problems.append("duplicate row name(s): %s" % ", ".join(dups))
            if set(names) != GE_QUIRK_NAMES:
                problems.append("registry names %s != the literal six %s"
                                % (sorted(set(names)), sorted(GE_QUIRK_NAMES)))
            return problems, ["rows        : %s" % (", ".join(shown) or "(no registry)")]

        fns = (inproc(e1), inproc(e2), inproc(e3), inproc(e4), inproc(e5), inproc(e6), inproc(e7),
               e8, live(e9), replayed(e10), live(e11), inproc(e12), replayed(e13))
        for cid, fn in zip(GE_CASES, fns):
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s"
                                 % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001 -- a setup failure fails every case not yet run
        setup = "the group setup raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    finally:
        if proc is not None:
            proc.close()
        peer.close()
    for cid in GE_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GE, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group F: mistral request
# ---------------------------------------------------------------------------

GF_CASES = (
    "system -> system message",          # F1
    "tool_use -> tool_calls",            # F2
    "tool_result -> tool message",       # F3
    "bridge between tool and user",      # F4
    "hashed id is 9 alnum chars",        # F5
    "ids stable across resends",         # F6
    "forced collision walks",            # F7
    "foreign Mistral id -> toolu_",      # F8
    "tools, tool_choice, parallel",      # F9
    "stop, max_tokens_cap, sampling",    # F10
    "only the explicit keys sent",       # F11
    "count_tokens = bytes // div",       # F12
    "orphan tool_result -> text",        # F13
    "image -> image_url data URL",       # F14
    "minted id round trip",              # F15
    "minted keeps id, hashed walks",     # F16
)

GF_BACKEND = "tff"
GF_ROUTE = "claude-tf-f"
GF_MODEL = "tf-mistral-model"
GF_MINTED = "toolu_lr"                      # KD-6's _MINTED_PREFIX, as a literal: the plan fixes it
GF_ID_RE = re.compile(r"^[A-Za-z0-9]{9}$")  # the Mistral tool-id shape (A-7, M5 (f))
GF_FOREIGN_RE = re.compile(r"^toolu_[A-Za-z0-9]{24}$")
GF_PATH = "/v1/chat/completions"
GF_ORPHAN = "[tool result for an earlier call]\n"
GF_BRIDGE = {"role": "assistant", "content": "Done."}
GF_FIXTURE = "tf_mistral_error_extra_inputs.json"
# F3/F4/F5 rest on the A-7 rules M5 (e)-(g) is to confirm; F11 on the synthetic 422 fixture.
GF_DEFENSIVE_RULE = "provenance  : defensive, not observed (A-7 Mistral rule, pending M5)"
GF_DEFENSIVE_FIXTURE = "provenance  : defensive, not observed (synthetic fixture, pending M5)"
# F11: the only keys a translated body (and each of its parts) may carry (plan Step 10).
GF_TOP_KEYS = frozenset({"model", "messages", "max_tokens", "temperature", "top_p", "stop", "stream",
                         "tools", "tool_choice", "parallel_tool_calls"})
GF_MESSAGE_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
GF_PART_KEYS = frozenset({"type", "text", "image_url"})
# F11: what the stuffed request carries that no Mistral body may.
GF_STUFFED_TOP = {"metadata": {"user_id": "tf-user-0"}, "thinking": {"type": "enabled", "budget_tokens": 1024},
                  "top_k": 5, "service_tier": "auto", "context_management": {"edits": []},
                  "future_field": {"tf": 1}}
GF_CACHE = {"type": "ephemeral"}
GF_FORCED_DIGEST = hashlib.sha256(b"tf-forced-collision").digest()   # F7: what both forced ids hash to
GF_COLLIDE = ("toolu_tff_col_a", "toolu_tff_col_b")                   # F7: sorted, a first
GF_PNG = "iVBORw0KGgoAAAANSUhEUg=="                                     # F14: a base64 payload (no image)


def gf_need(mod, names):
    """One problem naming every callable of *names* the module does not define (red, never a crash)."""
    absent = [n for n in names if not callable(getattr(mod, n, None))]
    if not absent:
        return []
    return ["the module defines no %s (Step 10 not landed)" % ", ".join(absent)]


def gf_route(mod, **options):
    return mod.RouteSpec(name=GF_ROUTE, backend=GF_BACKEND, model=GF_MODEL, options=dict(options))


def gf_backend(mod):
    """A mistral BackendSpec for an in-process InboundRequest (no key: count_tokens never sends)."""
    return mod.BackendSpec(name=GF_BACKEND, kind="mistral", scheme="https", host="tf-mistral.invalid",
                           port=443, base_path="", allow_private=False, allow_loopback=False,
                           ca_file=None, ca_pem=None, api_key=None, auth_header="authorization",
                           forward_headers=frozenset(), connect_timeout=5.0, idle_timeout=30.0)


def gf_inbound(mod, endpoint, body, route):
    return mod.InboundRequest(endpoint=endpoint, requested_model=body.get("model"), body=body,
                              stream=bool(body.get("stream")), route=route, backend=gf_backend(mod),
                              client_headers={}, scrub=lambda text: text)


def gf_body(messages, **extra):
    body = {"model": GF_ROUTE, "max_tokens": 256, "stream": False, "messages": messages}
    body.update(extra)
    return body


def gf_tool_use(tid, name="Read", tool_input=None):
    return {"type": "tool_use", "id": tid, "name": name,
            "input": tool_input if tool_input is not None else {"file_path": "/tf/" + tid}}


def gf_tool_result(tid, content, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def gf_pair_history(pairs, lead="tf start"):
    """[user *lead*, assistant (text + one tool_use per (id, name)), user (one tool_result each)]."""
    return [{"role": "user", "content": lead},
            {"role": "assistant", "content": [{"type": "text", "text": "calling"}]
             + [gf_tool_use(tid, name) for tid, name in pairs]},
            {"role": "user", "content": [gf_tool_result(tid, "tf result %s" % tid) for tid, _n in pairs]}]


def gf_translate(mod, body, route, label):
    """(out, problems): _rt_ms_translate(body, route), the input checked unmutated and out a new
    dict with a messages list; (None, problems) when the function is absent or the shape is wrong."""
    problems = gf_need(mod, ("_rt_ms_translate",))
    if problems:
        return None, problems
    sent = ge_clone(body)
    out = mod._rt_ms_translate(sent, route)
    if sent != body:
        problems.append("%s: _rt_ms_translate mutated its input" % label)
    if out is sent:
        problems.append("%s: _rt_ms_translate returned its input, not a new dict" % label)
    if not isinstance(out, dict) or not isinstance(out.get("messages"), list):
        problems.append("%s: returned %s, expected a dict with a messages list"
                        % (label, type(out).__name__ if not isinstance(out, dict) else "a dict without one"))
        return None, problems
    return out, problems


def gf_id_map(mod, ids, label):
    """(mapping, problems): _rt_ms_tool_id_map(ids) checked to map exactly *ids* to 9-alnum values."""
    problems = gf_need(mod, ("_rt_ms_tool_id_map",))
    if problems:
        return None, problems
    got = mod._rt_ms_tool_id_map(list(ids))
    if not isinstance(got, dict) or set(got) != set(ids):
        return None, ["%s: _rt_ms_tool_id_map returned %s, expected a dict keyed by exactly the %d id(s)"
                      % (label, sorted(got) if isinstance(got, dict) else type(got).__name__, len(set(ids)))]
    bad = sorted(k for k, v in got.items() if not (isinstance(v, str) and GF_ID_RE.match(v)))
    if bad:
        problems.append("%s: id(s) %s map to %s, not 9 characters of [A-Za-z0-9]"
                        % (label, bad, [got[k] for k in bad]))
    if len(set(got.values())) != len(got):
        problems.append("%s: two ids share one Mistral id: %r" % (label, got))
    return got, problems


def gf_roles(msgs):
    return [m.get("role") if isinstance(m, dict) else type(m).__name__ for m in msgs]


def gf_call_ids(msgs):
    """[(id, function name)] of every assistant tool_call, in order."""
    out = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for call in m.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                out.append((call.get("id") if isinstance(call, dict) else None,
                            fn.get("name") if isinstance(fn, dict) else None))
    return out


def gf_tool_ids(msgs):
    return [m.get("tool_call_id") for m in msgs if isinstance(m, dict) and m.get("role") == "tool"]


def gf_paired(msgs, label, want=None):
    """(call ids, problems): every tool_call id is 9 alnum, the tool messages answer them in the
    same order with the same ids (one request is self-consistent), and equal *want* if given."""
    calls = [cid for cid, _n in gf_call_ids(msgs)]
    tools = gf_tool_ids(msgs)
    problems = []
    if calls != tools:
        problems.append("%s: tool_calls ids %s but tool_call_ids %s (a pair disagrees)" % (label, calls, tools))
    bad = [c for c in calls if not (isinstance(c, str) and GF_ID_RE.match(c))]
    if bad:
        problems.append("%s: tool_calls id(s) %r are not 9 characters of [A-Za-z0-9]" % (label, bad))
    if len(set(calls)) != len(calls):
        problems.append("%s: tool_calls ids %s are not unique" % (label, calls))
    if want is not None and calls != want:
        problems.append("%s: tool_calls ids %s, expected %s" % (label, calls, want))
    return calls, problems


def gf_keys(obj, path="$"):
    """Yield (path, key) for every dict key anywhere in *obj*."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield path, key
            for item in gf_keys(value, "%s.%s" % (path, key)):
                yield item
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            for item in gf_keys(value, "%s[%d]" % (path, i)):
                yield item


def gf_adapter(mod, method):
    """(MistralAdapter(), None) when the class overrides *method*, else (None, why)."""
    cls = getattr(mod, "MistralAdapter", None)
    base = getattr(mod, "Adapter", None)
    if cls is None:
        return None, "the module defines no MistralAdapter"
    if base is not None and getattr(cls, method, None) is getattr(base, method, None):
        return None, ("MistralAdapter.%s is still the Adapter base's NotImplementedError (Step 10 not landed)"
                      % method)
    return cls(), None


def gf_fixture_keys():
    """(the key names tf_mistral_error_extra_inputs.json rejects, None) or (None, why)."""
    try:
        with open(os.path.join(FIXTURES, GF_FIXTURE), "rb") as fh:
            obj = json.loads(fh.read().decode("utf-8"))
        detail = obj["message"]["detail"]
        names = set()
        for entry in detail:
            if entry.get("msg") != "Extra inputs are not permitted" or not isinstance(entry.get("loc"), list):
                return None, "%s: an entry is not an extra-inputs refusal with a loc list" % GF_FIXTURE
            names.add(entry["loc"][-1])
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return None, "%s cannot be read as a 422 detail list: %s" % (GF_FIXTURE, type(exc).__name__)
    if not names:
        return None, "%s names no rejected key" % GF_FIXTURE
    return names, None


def gf_forced_sha(targets, hits):
    """A sha256 stand-in: the digest of any input ENDING in one of *targets* (a first hash, never a
    walk, which ends in "\\0<n>") is GF_FORCED_DIGEST and the target is added to *hits*; every
    other input gets its real sha256."""
    class ForcedSha:
        digest_size = 32
        block_size = 64
        name = "sha256"

        def __init__(self, data=b"", **_kw):
            self._buf = bytearray(data)

        def update(self, data):
            self._buf += data

        def copy(self):
            return ForcedSha(bytes(self._buf))

        def digest(self):
            data = bytes(self._buf)
            for target in targets:
                if data.endswith(target.encode("utf-8")):
                    hits.add(target)
                    return GF_FORCED_DIGEST
            return hashlib.sha256(data).digest()

        def hexdigest(self):
            return self.digest().hex()

    return ForcedSha


def group_f(suite, fixture_root):
    """F. mistral request: the Anthropic -> chat-completions translation and the
    KD-6 tool-id mapping, in-process (Step 10).

    Every case calls the plan's named functions -- `_rt_ms_translate(body,
    route)`, `_rt_ms_tool_id_map(ids)`, `_rt_ms_anthropic_id(mistral_id)` and
    `MistralAdapter.count_tokens / upstream_request` -- and asserts behaviour,
    never a private helper or an exact hash value, so Step 10 may name its
    helpers freely.  F7 forces a collision by replacing sha256 in the module
    (as `hashlib.sha256` or a module-level `sha256`) for the duration of the
    case.  F3-F5 rest on the A-7 rules and F11 on a synthetic fixture: they say
    "defensive, not observed" until M5 runs.  Before Step 10 lands every case
    FAILS naming the missing function, never crashes the group.
    """
    del fixture_root            # in-process only: nothing is written
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_f", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GF_CASES:
            suite.record(GF, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__, exc)])
        return

    def f1():
        problems, shown = [], []
        msgs = [{"role": "user", "content": "tf hi"}]
        system_list = [{"type": "text", "text": "tf sys one", "cache_control": GF_CACHE},
                       {"type": "text", "text": "tf sys two"}]
        out, problems = gf_translate(mod, gf_body(msgs, system=system_list), gf_route(mod), "list system")
        if out is not None:
            first = out["messages"][0] if out["messages"] else None
            text = first.get("content") if isinstance(first, dict) else None
            if not isinstance(first, dict) or set(first) != {"role", "content"} or first.get("role") != "system":
                problems.append("list system: messages[0] is %r, expected {role: system, content: <text>}"
                                % (first,))
            elif not (isinstance(text, str) and "tf sys one" in text and "tf sys two" in text
                      and text.index("tf sys one") < text.index("tf sys two")):
                problems.append("list system: system content %r does not join both texts in order" % (text,))
            if "cache_control" in json.dumps(out):
                problems.append("list system: cache_control survived the translation")
            if out["messages"][1:] != [{"role": "user", "content": "tf hi"}]:
                problems.append("list system: the messages after the system one are %r, expected the user turn"
                                % (out["messages"][1:],))
            shown.append("list system -> %r" % (text,))
        out, more = gf_translate(mod, gf_body(msgs, system="tf sys"), gf_route(mod), "string system")
        problems += more
        if out is not None and out["messages"] != [{"role": "system", "content": "tf sys"},
                                                   {"role": "user", "content": "tf hi"}]:
            problems.append("string system: messages %r, expected the system message then the user turn"
                            % (out["messages"],))
        out, more = gf_translate(mod, gf_body(msgs), gf_route(mod), "no system")
        problems += more
        if out is not None and out["messages"] != [{"role": "user", "content": "tf hi"}]:
            problems.append("no system: messages %r, expected only the user turn" % (out["messages"],))
        return problems, ["system      : " + s for s in shown]

    def f2():
        tool_input = {"file_path": "/tf/ä ✓", "n": 1, "flags": [True, None]}
        arguments = json.dumps(tool_input, separators=(",", ":"), ensure_ascii=False)
        history = [{"role": "user", "content": "tf start"},
                   {"role": "assistant", "content": [
                       {"type": "thinking", "thinking": "tf think", "signature": "tf-sig"},
                       {"type": "redacted_thinking", "data": "tf-redacted"},
                       {"type": "text", "text": "calling"},
                       gf_tool_use("toolu_tff_a", "Read", tool_input)]},
                   {"role": "user", "content": [gf_tool_result("toolu_tff_a", "tf result")]},
                   {"role": "assistant", "content": [gf_tool_use("toolu_tff_b", "Bash", {"command": "tf"})]},
                   {"role": "user", "content": [gf_tool_result("toolu_tff_b", "tf b")]}]
        out, problems = gf_translate(mod, gf_body(history), gf_route(mod), "tool_use history")
        if out is None:
            return problems, []
        msgs = out["messages"]
        calls, more = gf_paired(msgs, "tool_use history")
        problems += more
        assistants = [m for m in msgs if isinstance(m, dict) and m.get("role") == "assistant"
                      and m.get("content") != "Done."]
        want = [{"role": "assistant", "content": "calling",
                 "tool_calls": [{"id": calls[0] if calls else None, "type": "function",
                                 "function": {"name": "Read", "arguments": arguments}}]},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"id": calls[1] if len(calls) > 1 else None, "type": "function",
                                 "function": {"name": "Bash", "arguments": '{"command":"tf"}'}}]}]
        if assistants != want:
            problems.append("the assistant messages are %r, expected %r" % (assistants, want))
        dumped = json.dumps(out, ensure_ascii=False)
        for needle in ("tf think", "tf-sig", "tf-redacted"):
            if needle in dumped:
                problems.append("a thinking block survived (%r in the body)" % needle)
        return problems, ["arguments   : %r" % (arguments,),
                          "call ids    : %s" % calls]

    def f3():
        pairs = (("toolu_tff_a", "Read"), ("toolu_tff_b", "Bash"))
        history = gf_pair_history(pairs)
        history[2]["content"] = [gf_tool_result("toolu_tff_a", "tf result a"),
                                 gf_tool_result("toolu_tff_b", [{"type": "text", "text": "tf b1"},
                                                                {"type": "text", "text": "tf b2"}],
                                                is_error=True)]
        out, problems = gf_translate(mod, gf_body(history), gf_route(mod), "two tool results")
        if out is None:
            return problems, [GF_DEFENSIVE_RULE]
        msgs = out["messages"]
        calls, more = gf_paired(msgs, "two tool results")
        problems += more
        tools = [m for m in msgs if isinstance(m, dict) and m.get("role") == "tool"]
        if gf_roles(msgs) != ["user", "assistant", "tool", "tool"]:
            problems.append("roles %s, expected user, assistant, tool, tool" % gf_roles(msgs))
        if len(tools) == 2 and len(calls) == 2:
            want_a = {"role": "tool", "tool_call_id": calls[0], "name": "Read", "content": "tf result a"}
            if tools[0] != want_a:
                problems.append("tool message a is %r, expected %r" % (tools[0], want_a))
            b = tools[1]
            text = b.get("content")
            if set(b) != {"role", "tool_call_id", "name", "content"} or b.get("name") != "Bash" \
                    or b.get("tool_call_id") != calls[1]:
                problems.append("tool message b is %r, expected role/tool_call_id %r/name Bash/content"
                                % (b, calls[1]))
            if not (isinstance(text, str) and text.startswith("Error: ") and "tf b1" in text
                    and "tf b2" in text and text.index("tf b1") < text.index("tf b2")):
                problems.append("tool message b content %r, expected \"Error: \" then both texts in order"
                                % (text,))
        return problems, ["tools       : %s" % [(t.get("tool_call_id"), t.get("name")) for t in tools],
                          GF_DEFENSIVE_RULE]

    def f4():
        problems, shown = [], []
        pairs = (("toolu_tff_a", "Read"), ("toolu_tff_b", "Read"))
        bridged = gf_pair_history(pairs)
        bridged[2]["content"] = bridged[2]["content"] + [{"type": "text", "text": "tf next"}]
        unbridged = gf_pair_history(pairs) + [{"role": "assistant", "content": "tf ok"},
                                              {"role": "user", "content": "tf more"}]
        trailing = gf_pair_history(pairs)
        table = (("tool, tool, then user text", bridged,
                  ["user", "assistant", "tool", "tool", "assistant", "user"], 4),
                 ("tool then an assistant turn", unbridged,
                  ["user", "assistant", "tool", "tool", "assistant", "user"], None),
                 ("history ends on a tool message", trailing, ["user", "assistant", "tool", "tool"], None))
        for label, history, roles, bridge_at in table:
            out, more = gf_translate(mod, gf_body(history), gf_route(mod), label)
            problems += more
            if out is None:
                continue
            msgs = out["messages"]
            shown.append("%s -> %s" % (label, ", ".join(str(r) for r in gf_roles(msgs))))
            if gf_roles(msgs) != roles:
                problems.append("%s: roles %s, expected %s" % (label, gf_roles(msgs), roles))
                continue
            bridges = [i for i, m in enumerate(msgs) if m == GF_BRIDGE]
            if bridges != ([bridge_at] if bridge_at is not None else []):
                problems.append("%s: the bridge {assistant: \"Done.\"} sits at %s, expected %s"
                                % (label, bridges, [bridge_at] if bridge_at is not None else "nowhere"))
            if bridge_at is not None and msgs[-1] != {"role": "user", "content": "tf next"}:
                problems.append("%s: the last message is %r, expected the user text" % (label, msgs[-1]))
        return problems, ["case        : " + s for s in shown] + [GF_DEFENSIVE_RULE]

    def f5():
        ids = ("toolu_01A09q90qw90lq917835lq9", "call_tf-0.1", "toolu_" + "Z" * 64, "abcdefghi")
        mapping, problems = gf_id_map(mod, ids, "_rt_ms_tool_id_map")
        out, more = gf_translate(mod, gf_body(gf_pair_history([(i, "Read") for i in ids])), gf_route(mod),
                                 "four native ids")
        problems += more
        if out is not None:
            calls, more = gf_paired(out["messages"], "four native ids",
                                    want=[mapping[i] for i in ids] if mapping else None)
            problems += more
        return problems, ["ids         : %s" % (sorted(mapping.values()) if mapping else "(none)"),
                          GF_DEFENSIVE_RULE]

    def f6():
        pairs = [("toolu_tff_a", "Read"), ("toolu_tff_b", "Bash")]
        history = gf_pair_history(pairs)
        first, problems = gf_translate(mod, gf_body(history), gf_route(mod), "first send")
        again, more = gf_translate(mod, gf_body(history), gf_route(mod), "resend")
        problems += more
        longer = history + [{"role": "assistant", "content": [gf_tool_use("toolu_tff_c", "Bash")]},
                            {"role": "user", "content": [gf_tool_result("toolu_tff_c", "tf c")]}]
        grown, more = gf_translate(mod, gf_body(longer), gf_route(mod), "appended turn")
        problems += more
        if first is None or again is None or grown is None:
            return problems, []
        ids1, more = gf_paired(first["messages"], "first send")
        problems += more
        ids2, more = gf_paired(again["messages"], "resend")
        problems += more
        ids3, more = gf_paired(grown["messages"], "appended turn")
        problems += more
        if ids2 != ids1:
            problems.append("the same history twice gave %s then %s" % (ids1, ids2))
        if ids3[:len(ids1)] != ids1 or len(ids3) != len(ids1) + 1:
            problems.append("with a turn appended the ids are %s, expected the shared prefix %s and one more"
                            % (ids3, ids1))
        ids = ["toolu_tff_a", "toolu_tff_b", "toolu_tff_c", GF_MINTED + "AbC123xyz"]
        fwd, more = gf_id_map(mod, ids, "map in order")
        problems += more
        rev, more = gf_id_map(mod, list(reversed(ids)), "map reversed")
        problems += more
        if fwd is not None and rev is not None and fwd != rev:
            problems.append("_rt_ms_tool_id_map depends on the input order: %r vs %r" % (fwd, rev))
        return problems, ["ids         : %s / %s / %s" % (ids1, ids2, ids3)]

    def f7():
        problems = gf_need(mod, ("_rt_ms_tool_id_map", "_rt_ms_translate"))
        if problems:
            return problems, []
        hits = set()
        shim = gf_forced_sha(GF_COLLIDE, hits)
        saved = []
        if getattr(mod, "hashlib", None) is hashlib:
            ns = types.SimpleNamespace(**{n: getattr(hashlib, n) for n in dir(hashlib) if not n.startswith("__")})
            ns.sha256 = shim
            saved.append(("hashlib", mod.hashlib))
            mod.hashlib = ns
        if getattr(mod, "sha256", None) is hashlib.sha256:
            saved.append(("sha256", mod.sha256))
            mod.sha256 = shim
        if not saved:
            return ["the module reaches sha256 neither as hashlib.sha256 nor as a module-level sha256: "
                    "the collision cannot be forced"], []
        a, b = GF_COLLIDE
        try:
            both, problems = gf_id_map(mod, [a, b], "forced pair")
            missed = [t for t in GF_COLLIDE if t not in hits]
            if missed:
                problems.append("the stand-in sha256 never hashed an input ending in %s: the collision was "
                                "not forced (the hash must end with the id, KD-6)" % missed)
            lone, more = gf_id_map(mod, [a], "a alone")
            problems += more
            swapped, more = gf_id_map(mod, [b, a], "forced pair, reversed")
            problems += more
            if both is not None and lone is not None and both[a] != lone[a]:
                problems.append("%s (sorted first) became %r, expected its own first hash %r; %s must walk"
                                % (a, both[a], lone[a], b))
            if both is not None and swapped is not None and swapped != both:
                problems.append("the walk depends on the input order: %r vs %r" % (both, swapped))
            out, more = gf_translate(mod, gf_body(gf_pair_history([(a, "Read"), (b, "Bash")])),
                                     gf_route(mod), "forced pair translated")
            problems += more
            if out is not None:
                _calls, more = gf_paired(out["messages"], "forced pair translated",
                                         want=[both[a], both[b]] if both else None)
                problems += more
        finally:
            for attr, value in saved:
                setattr(mod, attr, value)
        return problems, ["forced      : %s -> %s" % (", ".join(GF_COLLIDE), both if both else "(none)"),
                          "patched     : %s" % ", ".join(attr for attr, _v in saved)]

    def f8():
        problems = gf_need(mod, ("_rt_ms_anthropic_id",))
        if problems:
            return problems, []
        shown = []
        minted = mod._rt_ms_anthropic_id("AbC123xyz")
        if minted != GF_MINTED + "AbC123xyz":
            problems.append("a 9-alnum Mistral id AbC123xyz became %r, expected %r"
                            % (minted, GF_MINTED + "AbC123xyz"))
        foreign = {}
        for mid in ("call_tf-01.x", "AbC123xyzQ", "AbC12-xyz"):
            got = mod._rt_ms_anthropic_id(mid)
            again = mod._rt_ms_anthropic_id(mid)
            foreign[mid] = got
            shown.append("%r -> %r" % (mid, got))
            if not (isinstance(got, str) and GF_FOREIGN_RE.match(got)):
                problems.append("Mistral id %r became %r, expected toolu_ + 24 characters of [A-Za-z0-9]"
                                % (mid, got))
            if again != got:
                problems.append("Mistral id %r became %r then %r (not one consistent id)" % (mid, got, again))
        empty = mod._rt_ms_anthropic_id("")
        if not (isinstance(empty, str) and GF_FOREIGN_RE.match(empty)):
            problems.append("an empty Mistral id became %r, expected toolu_ + 24 characters" % (empty,))
        anth = foreign.get("call_tf-01.x")
        if isinstance(anth, str):
            history = gf_pair_history([(anth, "Read")])
            out1, more = gf_translate(mod, gf_body(history), gf_route(mod), "resend 1")
            problems += more
            out2, more = gf_translate(mod, gf_body(history), gf_route(mod), "resend 2")
            problems += more
            if out1 is not None and out2 is not None:
                ids1, more = gf_paired(out1["messages"], "resend 1")
                problems += more
                ids2, more = gf_paired(out2["messages"], "resend 2")
                problems += more
                if ids1 != ids2 or len(ids1) != 1:
                    problems.append("the resent foreign id became %s then %s, expected one consistent id"
                                    % (ids1, ids2))
                shown.append("resent %s -> %s" % (anth, ids1))
        return problems, ["id          : " + s for s in shown]

    def f9():
        schema = {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}
        tools = [{"name": "Read", "description": "tf read", "input_schema": schema, "cache_control": GF_CACHE},
                 {"name": "Bash", "description": "tf bash", "input_schema": {"type": "object", "properties": {}}}]
        want_tools = [{"type": "function", "function": {"name": "Read", "description": "tf read",
                                                        "parameters": schema}},
                      {"type": "function", "function": {"name": "Bash", "description": "tf bash",
                                                        "parameters": {"type": "object", "properties": {}}}}]
        msgs = [{"role": "user", "content": "tf hi"}]
        table = (("auto", {"type": "auto"}, "auto", None),
                 ("any", {"type": "any"}, "any", None),
                 ("none", {"type": "none"}, "none", None),
                 ("tool Read", {"type": "tool", "name": "Read"}, {"type": "function", "function": {"name": "Read"}},
                  None),
                 ("auto + disable_parallel_tool_use", {"type": "auto", "disable_parallel_tool_use": True}, "auto",
                  False),
                 ("tool Read + disable_parallel_tool_use",
                  {"type": "tool", "name": "Read", "disable_parallel_tool_use": True},
                  {"type": "function", "function": {"name": "Read"}}, False))
        problems, shown = [], []
        for label, choice, want_choice, want_parallel in table:
            out, more = gf_translate(mod, gf_body(msgs, tools=ge_clone(tools), tool_choice=choice),
                                     gf_route(mod), label)
            problems += more
            if out is None:
                continue
            if out.get("tools") != want_tools:
                problems.append("%s: tools %r, expected %r" % (label, out.get("tools"), want_tools))
            if out.get("tool_choice", "(absent)") != want_choice:
                problems.append("%s: tool_choice %r, expected %r" % (label, out.get("tool_choice", "(absent)"),
                                                                    want_choice))
            if want_parallel is not None and out.get("parallel_tool_calls", "(absent)") is not want_parallel:
                problems.append("%s: parallel_tool_calls %r, expected False"
                                % (label, out.get("parallel_tool_calls", "(absent)")))
            shown.append("%s -> %r" % (label, out.get("tool_choice", "(absent)")))
        out, more = gf_translate(mod, gf_body(msgs, tools=[]), gf_route(mod), "empty tools")
        problems += more
        if out is not None and "tools" in out:
            problems.append("empty tools: tools %r is sent, expected it omitted" % (out["tools"],))
        return problems, ["tool_choice : " + s for s in shown[:6]]

    def f10():
        msgs = [{"role": "user", "content": "tf hi"}]
        sampled = gf_body(msgs, max_tokens=4096, stop_sequences=["tf-stop-1", "tf-stop-2"], temperature=0.7,
                          top_p=0.8, stream=True)
        base = {"model": GF_MODEL, "messages": msgs, "stream": True, "stop": ["tf-stop-1", "tf-stop-2"]}
        table = (("no options", sampled, {}, dict(base, max_tokens=4096, temperature=0.7, top_p=0.8)),
                 ("cap 1000 + route sampling", sampled, {"max_tokens_cap": 1000, "temperature": 0.2, "top_p": 0.5},
                  dict(base, max_tokens=1000, temperature=0.2, top_p=0.5)),
                 ("cap 8192 above max_tokens", sampled, {"max_tokens_cap": 8192},
                  dict(base, max_tokens=4096, temperature=0.7, top_p=0.8)),
                 ("no sampling anywhere", gf_body(msgs, max_tokens=300), {},
                  {"model": GF_MODEL, "messages": msgs, "stream": False, "max_tokens": 300}),
                 ("route sampling only", gf_body(msgs, max_tokens=300), {"temperature": 0.0, "top_p": 1.0},
                  {"model": GF_MODEL, "messages": msgs, "stream": False, "max_tokens": 300,
                   "temperature": 0.0, "top_p": 1.0}))
        problems, shown = [], []
        for label, body, options, want in table:
            out, more = gf_translate(mod, body, gf_route(mod, **options), label)
            problems += more
            if out is None:
                continue
            if out != want:
                problems.append("%s: body %r, expected %r (differing key(s): %s)"
                                % (label, {k: v for k, v in out.items() if k != "messages"},
                                   {k: v for k, v in want.items() if k != "messages"}, ge_diff_keys(out, want)))
            shown.append("%s -> max_tokens %s, temperature %s, top_p %s"
                         % (label, out.get("max_tokens"), out.get("temperature", "-"), out.get("top_p", "-")))
        return problems, ["case        : " + s for s in shown]

    def f11():
        names, why = gf_fixture_keys()
        if why:
            return [why], [GF_DEFENSIVE_FIXTURE]
        stuffed = set(GF_STUFFED_TOP) | {"cache_control"}
        problems = []
        if not names <= stuffed:
            problems.append("%s rejects %s, which F11 does not stuff" % (GF_FIXTURE, sorted(names - stuffed)))
        history = [{"role": "user", "content": [{"type": "text", "text": "tf start", "cache_control": GF_CACHE}]},
                   {"role": "assistant", "content": [{"type": "text", "text": "calling", "cache_control": GF_CACHE},
                                                     dict(gf_tool_use("toolu_tff_a"), cache_control=GF_CACHE)]},
                   {"role": "user", "content": [dict(gf_tool_result("toolu_tff_a", "tf a"), cache_control=GF_CACHE),
                                                {"type": "text", "text": "tf next", "cache_control": GF_CACHE},
                                                {"type": "image", "cache_control": GF_CACHE,
                                                 "source": {"type": "base64", "media_type": "image/png",
                                                            "data": GF_PNG}}]}]
        body = gf_body(history, system=[{"type": "text", "text": "tf sys", "cache_control": GF_CACHE}],
                       tools=[{"name": "Read", "description": "tf read", "cache_control": GF_CACHE,
                               "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}],
                       tool_choice={"type": "auto"}, stop_sequences=["tf-stop"], temperature=0.5, top_p=0.9,
                       **ge_clone(GF_STUFFED_TOP))
        out, more = gf_translate(mod, body, gf_route(mod), "stuffed request")
        problems += more
        if out is None:
            return problems, [GF_DEFENSIVE_FIXTURE]
        extra = sorted(set(out) - GF_TOP_KEYS)
        if extra:
            problems.append("top-level key(s) outside the explicit list: %s" % extra)
        leaked = sorted({"%s.%s" % (path, key) for path, key in gf_keys(out) if key in stuffed})
        if leaked:
            problems.append("stuffed key(s) reached the Mistral body: %s" % leaked[:6])
        for i, m in enumerate(out["messages"]):
            if not isinstance(m, dict) or not set(m) <= GF_MESSAGE_KEYS:
                problems.append("messages[%d] carries key(s) %s outside %s"
                                % (i, sorted(set(m) - GF_MESSAGE_KEYS) if isinstance(m, dict) else m,
                                   sorted(GF_MESSAGE_KEYS)))
                continue
            for part in m.get("content") if isinstance(m.get("content"), list) else []:
                if not isinstance(part, dict) or not set(part) <= GF_PART_KEYS:
                    problems.append("messages[%d] has a content part %r outside %s" % (i, part, sorted(GF_PART_KEYS)))
            for call in m.get("tool_calls") or []:
                if set(call) != {"id", "type", "function"} or set(call.get("function") or {}) != {"name",
                                                                                                    "arguments"}:
                    problems.append("messages[%d] has a tool_call %r outside {id, type, function{name, arguments}}"
                                    % (i, call))
        for tool in out.get("tools") or []:
            if set(tool) != {"type", "function"} or not set(tool.get("function") or {}) <= {"name", "description",
                                                                                            "parameters"}:
                problems.append("a tool %r is outside {type, function{name, description, parameters}}" % (tool,))
        sent = None
        adapter, why = gf_adapter(mod, "upstream_request")
        if adapter is None:
            problems.append(why)
        else:
            got = adapter.upstream_request(gf_inbound(mod, "messages", ge_clone(body), gf_route(mod)))
            if not (isinstance(got, tuple) and len(got) == 2 and isinstance(got[1], bytes)):
                problems.append("MistralAdapter.upstream_request returned %s, expected (path, bytes)"
                                % type(got).__name__)
            else:
                path, sent = got
                if path != GF_PATH:
                    problems.append("MistralAdapter.upstream_request path %r, expected %r" % (path, GF_PATH))
                try:
                    if json.loads(sent.decode("utf-8")) != out:
                        problems.append("the bytes upstream_request sends are not _rt_ms_translate's body")
                except (UnicodeDecodeError, ValueError):
                    problems.append("upstream_request sent bytes that are not UTF-8 JSON")
        return problems, ["fixture     : %s rejects %s" % (GF_FIXTURE, sorted(names)),
                          "sent keys   : %s" % sorted(out),
                          "upstream    : %s" % ("%d bytes" % len(sent) if sent is not None else "(not built)"),
                          GF_DEFENSIVE_FIXTURE]

    def f12():
        problems = gf_need(mod, ("_rt_ms_translate",))
        adapter, why = gf_adapter(mod, "count_tokens")
        if why:
            problems.append(why)
        if problems:
            return problems, []
        history = gf_pair_history([("toolu_tff_a", "Read")], lead="tf ä ✓ " * 40)
        body = gf_body(history, system="tf sys ✓", metadata={"user_id": "tf-user-0"}, top_k=5,
                       tools=[{"name": "Read", "description": "tf read ✓",
                               "input_schema": {"type": "object", "properties": {}}}])
        shown = []
        for divisor in (1, 16):
            route = gf_route(mod, count_divisor=divisor)
            sent = ge_clone(body)
            got = adapter.count_tokens(gf_inbound(mod, "count_tokens", sent, route))
            if sent != body:
                problems.append("divisor %d: count_tokens mutated the body" % divisor)
            translated = mod._rt_ms_translate(ge_clone(body), route)
            size = len(json.dumps(translated, separators=(",", ":"), ensure_ascii=False)
                       .encode("utf-8", "surrogatepass"))
            want = {"input_tokens": max(1, size // divisor)}
            if got != want:
                problems.append("divisor %d: count_tokens answered %r, expected %r (%d bytes of the translated "
                                "body // %d)" % (divisor, got, want, size, divisor))
            shown.append("divisor %2d -> %r (%d bytes)" % (divisor, got, size))
        return problems, ["count       : " + s for s in shown]

    def f13():
        problems, shown = [], []
        alone = [{"role": "user", "content": "tf start"}, {"role": "assistant", "content": "tf plain answer"},
                 {"role": "user", "content": [gf_tool_result("toolu_tff_orphan", "tf orphan text")]}]
        want_text = GF_ORPHAN + "tf orphan text"
        out, more = gf_translate(mod, gf_body(alone), gf_route(mod), "orphan alone")
        problems += more
        if out is not None:
            msgs = out["messages"]
            last = msgs[-1] if msgs else None
            if gf_roles(msgs) != ["user", "assistant", "user"]:
                problems.append("orphan alone: roles %s, expected user, assistant, user" % gf_roles(msgs))
            if last not in ({"role": "user", "content": want_text},
                            {"role": "user", "content": [{"type": "text", "text": want_text}]}):
                problems.append("orphan alone: the last message is %r, expected a user text %r" % (last, want_text))
            if "tool_call_id" in json.dumps(out):
                problems.append("orphan alone: a tool_call_id is sent for an id with no tool_use")
            shown.append("alone -> %s" % ", ".join(str(r) for r in gf_roles(msgs)))
        mixed = gf_pair_history([("toolu_tff_a", "Read")])
        mixed[2]["content"] = [gf_tool_result("toolu_tff_a", "tf a"),
                               gf_tool_result("toolu_tff_orphan", "tf orphan text")]
        out, more = gf_translate(mod, gf_body(mixed), gf_route(mod), "orphan after a paired result")
        problems += more
        if out is not None:
            msgs = out["messages"]
            _calls, more = gf_paired(msgs, "orphan after a paired result")
            problems += more
            roles = ["user", "assistant", "tool", "assistant", "user"]
            if gf_roles(msgs) != roles:
                problems.append("orphan after a paired result: roles %s, expected %s (tool, bridge, user text)"
                                % (gf_roles(msgs), roles))
            elif msgs[-1] not in ({"role": "user", "content": want_text},
                                  {"role": "user", "content": [{"type": "text", "text": want_text}]}):
                problems.append("orphan after a paired result: the last message is %r, expected %r"
                                % (msgs[-1], want_text))
            shown.append("mixed -> %s" % ", ".join(str(r) for r in gf_roles(msgs)))
        return problems, ["case        : " + s for s in shown]

    def f14():
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "tf look"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": GF_PNG}},
            {"type": "image", "source": {"type": "url", "url": "https://tf.invalid/tf.png"}}]}]
        want = {"role": "user", "content": [{"type": "text", "text": "tf look"},
                                            {"type": "image_url", "image_url": "data:image/png;base64," + GF_PNG},
                                            {"type": "image_url", "image_url": "https://tf.invalid/tf.png"}]}
        out, problems = gf_translate(mod, gf_body(msgs), gf_route(mod), "user images")
        if out is not None and out["messages"] != [want]:
            problems.append("user images: messages %r, expected %r" % (out["messages"], [want]))
        history = gf_pair_history([("toolu_tff_a", "Read")])
        history[2]["content"] = [gf_tool_result("toolu_tff_a", [
            {"type": "text", "text": "tf shot"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": GF_PNG}}])]
        out2, more = gf_translate(mod, gf_body(history), gf_route(mod), "image in a tool result")
        problems += more
        text = None
        if out2 is not None:
            tools = [m for m in out2["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
            text = tools[0].get("content") if len(tools) == 1 else None
            if not (isinstance(text, str) and "tf shot" in text and "[image omitted]" in text
                    and GF_PNG not in text):
                problems.append("image in a tool result: the tool message content is %r, expected the text "
                                "and \"[image omitted]\" (no image data)" % (text,))
        return problems, ["user        : %s" % (ge_types(out["messages"][0].get("content"))
                                                 if out is not None and out["messages"] else "(none)"),
                          "tool result : %r" % (text,)]

    def f15():
        minted = GF_MINTED + "AbC123xyz"
        mapping, problems = gf_id_map(mod, [minted, "toolu_tff_other"], "minted + other")
        if mapping is not None and mapping.get(minted) != "AbC123xyz":
            problems.append("%s maps to %r, expected exactly AbC123xyz" % (minted, mapping.get(minted)))
        for near in (GF_MINTED + "AbC123xy", GF_MINTED + "AbC123xyz0", GF_MINTED + "AbC-23xyz"):
            got, more = gf_id_map(mod, [near], "near miss %s" % near)
            problems += more
            tail = near[len(GF_MINTED):]
            if got is not None and got[near] in (tail, tail[:9]):
                problems.append("the near miss %s was treated as minted: it maps to %r (the minted form is "
                                "exactly ^toolu_lr[A-Za-z0-9]{9}$)" % (near, got[near]))
        out, more = gf_translate(mod, gf_body(gf_pair_history([(minted, "Read"), ("toolu_tff_other", "Bash")])),
                                 gf_route(mod), "minted resend")
        problems += more
        calls = []
        if out is not None:
            calls, more = gf_paired(out["messages"], "minted resend")
            problems += more
            if calls[:1] != ["AbC123xyz"] or gf_tool_ids(out["messages"])[:1] != ["AbC123xyz"]:
                problems.append("minted resend: tool_calls[0].id / tool_call_id are %s / %s, expected AbC123xyz"
                                % (calls[:1], gf_tool_ids(out["messages"])[:1]))
        return problems, ["ids         : %s" % calls]

    def f16():
        hashed = "toolu_01tff_hashed"     # sorts BEFORE toolu_lr...: a sorted walk without reservation fails
        lone, problems = gf_id_map(mod, [hashed], "hashed alone")
        if lone is None:
            return problems, []
        h9 = lone[hashed]
        minted = GF_MINTED + h9
        both, more = gf_id_map(mod, [hashed, minted], "hashed + minted")
        problems += more
        swapped, more = gf_id_map(mod, [minted, hashed], "minted + hashed")
        problems += more
        if both is not None:
            if both[minted] != h9:
                problems.append("%s maps to %r, expected exactly its own %s" % (minted, both[minted], h9))
            if both[hashed] == h9:
                problems.append("%s kept %s although the minted id reserves it" % (hashed, h9))
            if swapped is not None and swapped != both:
                problems.append("the result depends on the input order: %r vs %r" % (both, swapped))
        out, more = gf_translate(mod, gf_body(gf_pair_history([(hashed, "Read"), (minted, "Bash")])),
                                 gf_route(mod), "both translated")
        problems += more
        calls = []
        if out is not None:
            calls, more = gf_paired(out["messages"], "both translated",
                                    want=[both[hashed], both[minted]] if both else None)
            problems += more
        return problems, ["walk        : %s alone -> %s; with %s -> %s" % (hashed, h9, minted, both),
                          "ids         : %s" % calls]

    def needs(fn, *names):
        """The case, failing once with the missing names before it runs (one red line, not one per call)."""
        def case():
            missing = gf_need(mod, names)
            return (missing, []) if missing else fn()
        return case

    tr, idmap, anth = "_rt_ms_translate", "_rt_ms_tool_id_map", "_rt_ms_anthropic_id"
    fns = (needs(f1, tr), needs(f2, tr), needs(f3, tr), needs(f4, tr), needs(f5, idmap, tr),
           needs(f6, idmap, tr), needs(f7, idmap, tr), needs(f8, anth, tr), needs(f9, tr), needs(f10, tr),
           needs(f11, tr), f12, needs(f13, tr), needs(f14, tr), needs(f15, idmap, tr), needs(f16, idmap, tr))
    try:
        for cid, fn in zip(GF_CASES, fns):
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001
        setup = "the group setup raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    for cid in GF_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GF, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group G: mistral stream
# ---------------------------------------------------------------------------

GG_CASES = (
    "text fixture -> exact events",      # G1
    "one tool -> one json delta",        # G2
    "parallel tools in index order",     # G3
    "text closed before tools",          # G4
    "delta.content as a list",           # G5
    "unknown p key ignored",             # G6
    "usage from the last chunk",         # G7
    "length -> max_tokens",              # G8
    "stop with tools -> tool_use",       # G9
    "error chunk -> event: error",       # G10
    "EOF, no finish -> error",           # G11
    "bad or over-cap args -> error",     # G12
    "conflicting tool -> error",         # G13
    "non-stream mapping",                # G14
    "live TLS: minted ids round trip",   # G15
    "model echo, exact route",           # G16
    "model echo, default route",         # G17
)

GG_BACKEND = "tfg"
GG_ROUTE = "claude-tf-g"                    # the in-process requested model (an exact route)
GG_G15_ROUTE = "claude-tf-g15"
GG_G16_ROUTE = "claude-tf-g16"
GG_LIVE_ROUTES = {GG_G15_ROUTE: "tf-g15", GG_G16_ROUTE: "tf-g16"}
GG_DEFAULT_MODEL = "tf-g-default"
GG_UNROUTED = "claude-unrouted-x"
GG_UPSTREAM_MODEL = "tf-mistral-upstream"   # every Mistral chunk and answer names it; never echoed (KD-16)
GG_INPUT_TOKENS = 321                       # the local estimate the translator is built with
GG_MSG_ID_RE = re.compile(r"^msg_[A-Za-z0-9]{8,}$")
GG_TOOL_LIMIT_FALLBACK = 8 * 1024 * 1024    # _RT_BUFFERED_TOOL_LIMIT per the plan, when the module lacks it
GG_TOOL_COUNT_FALLBACK = 128                # G12 (V33): _RT_BUFFERED_TOOL_COUNT, when the module lacks it
GG_FRAGMENT = 256 * 1024                    # G12: one argument fragment of the over-cap streams
GG_LEAK_BOUND = 65536                       # G12: client bytes of a refused over-cap stream stay under this
GG_PAD = "tfpadpadpad"                      # G6: the value of every unknown "p" key
# G1-G3 and G15 replay synthetic fixtures (tests/files/llm_router/README.md) until M5 runs.
GG_DEFENSIVE = "provenance  : defensive, not observed (synthetic fixture, pending M5)"
GG_FIXTURES = {"text": "tf_mistral_stream_text.sse", "tool": "tf_mistral_stream_tool.sse",
               "parallel": "tf_mistral_stream_parallel.sse"}
GG_ANTHROPIC_TYPES = frozenset({"invalid_request_error", "authentication_error", "billing_error",
                                "permission_error", "not_found_error", "request_too_large",
                                "rate_limit_error", "api_error", "timeout_error", "overloaded_error"})
GG_TERMINALS = ("message_stop", "error")


def ms_chunk(delta=None, finish=None, usage=None, **top):
    """One Mistral `chat.completion.chunk` (the OpenAI chunk shape) for choice 0."""
    obj = {"id": "tfcmpl-g-inline", "object": "chat.completion.chunk", "created": 1759622400,
           "model": GG_UPSTREAM_MODEL,
           "choices": [{"index": 0, "delta": delta if delta is not None else {}, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = usage
    obj.update(top)
    return obj


def ms_tool(index, args="", tid=None, name=None):
    """One delta.tool_calls item: *index* (None leaves it out), an arguments fragment,
    and (on a first fragment) the id and the function name."""
    item = {"function": {"arguments": args}}
    if index is not None:
        item["index"] = index
    if name is not None:
        item["function"]["name"] = name
    if tid is not None:
        item["id"] = tid
        item["type"] = "function"
    return item


def ms_tools(*items):
    return ms_chunk({"tool_calls": list(items)})


def ms_text(text):
    return ms_chunk({"content": text})


def ms_usage(prompt, completion):
    return {"prompt_tokens": prompt, "total_tokens": prompt + completion, "completion_tokens": completion}


def ms_stop(reason, usage=None):
    """The closing chunk: empty content, *reason*, *usage* when given."""
    return ms_chunk({"content": ""}, finish=reason, usage=usage)


MS_ROLE = ms_chunk({"role": "assistant", "content": ""})


def ms_lines(*chunks, done=True):
    """SSE lines (no newlines): `data: <compact JSON>` and a blank line per chunk (a str
    chunk is sent as is), then `data: [DONE]` unless *done* is False."""
    lines = []
    for chunk in chunks:
        payload = chunk if isinstance(chunk, str) else json.dumps(chunk, separators=(",", ":"),
                                                                   ensure_ascii=False)
        lines += ["data: " + payload, ""]
    if done:
        lines += ["data: [DONE]", ""]
    return lines


def ms_answer(message, finish, usage=None):
    """A non-stream Mistral `chat.completion` answer for choice 0."""
    return {"id": "tfcmpl-g-json", "object": "chat.completion", "created": 1759622400,
            "model": GG_UPSTREAM_MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage if usage is not None else ms_usage(25, 15)}


GG_JSON_ANSWER = ms_answer({"role": "assistant", "content": "tf g json"}, "stop")


def gg_chunks(lines):
    """[parsed chunk] of every `data:` line but [DONE] (a broken fixture raises here)."""
    out = []
    for line in lines:
        if line.startswith("data:") and line[len("data:"):].strip() != "[DONE]":
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def gg_fixtures():
    """{"text"|"tool"|"parallel": [lines]} from tests/files/llm_router, each parsed once."""
    out = {}
    for key, name in GG_FIXTURES.items():
        with open(os.path.join(FIXTURES, name), "rb") as fh:
            lines = fh.read().decode("utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()                                 # the file's final newline
        gg_chunks(lines)
        out[key] = lines
    return out


def gg_delta(chunk):
    choices = chunk.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("delta") or {}
    return {}


def gg_fx_texts(chunks):
    """The non-empty string contents of *chunks*, in order (one text_delta each)."""
    return [gg_delta(c)["content"] for c in chunks
            if isinstance(gg_delta(c).get("content"), str) and gg_delta(c)["content"]]


def gg_fx_tools(chunks):
    """{index: {"id", "name", "args"}} of *chunks*' tool calls, fragments joined in arrival order."""
    tools = {}
    for chunk in chunks:
        for item in gg_delta(chunk).get("tool_calls") or []:
            tool = tools.setdefault(item["index"], {"id": "", "name": "", "args": ""})
            tool["id"] = tool["id"] or item.get("id") or ""
            tool["name"] = tool["name"] or (item.get("function") or {}).get("name") or ""
            tool["args"] += (item.get("function") or {}).get("arguments") or ""
    return tools


def gg_fx_usage(chunks):
    """The last `usage` of *chunks* (None when there is none)."""
    usage = None
    for chunk in chunks:
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
    return usage


def gg_inbound(mod, stream=True, model=GG_ROUTE):
    """A mistral InboundRequest built in-process, scrubbing with the router's own KD-9
    scrubber over the suite's sentinels (G10 asserts the error text is scrubbed)."""
    body = {"model": model, "max_tokens": 256, "stream": stream,
            "messages": [{"role": "user", "content": "tf g"}]}
    scrub, _why = ge_scrubber(mod)
    return mod.InboundRequest(endpoint="messages", requested_model=model, body=body, stream=stream,
                              route=mod.RouteSpec(name=model, backend=GF_BACKEND, model=GF_MODEL, options={}),
                              backend=gf_backend(mod), client_headers={},
                              scrub=scrub if scrub is not None else (lambda text: text))


def gg_events(data):
    """[(event name, parsed data object or None)] of encoder bytes."""
    out = []
    for name, payload in an_events(data.decode("utf-8", "replace").split("\n")):
        try:
            obj = json.loads(payload)
        except ValueError:
            obj = None
        out.append((name, obj if isinstance(obj, dict) else None))
    return out


def gg_names(events):
    return [e[0] for e in events]


def gg_drive(tr, lines, eof=True):
    """Drive a translator as the relay loop does: begin(), feed(line + b"\\n") per upstream
    line, and on *eof* finish() (the pump's _EOF: called even when already closed).

    {"begin", "feeds" (bytes per line), "end", "out", "events", "tr"}."""
    begin = tr.begin()
    feeds = []
    for line in lines:
        data = line if isinstance(line, bytes) else line.encode("utf-8")
        feeds.append(tr.feed(data + b"\n"))
    end = tr.finish() if eof else b""
    out = begin + b"".join(feeds) + end
    return {"begin": begin, "feeds": feeds, "end": end, "out": out, "events": gg_events(out), "tr": tr}


def gg_wellformed(label, run, requested=GG_ROUTE):
    """Problems unless the translator's bytes are a well-formed Anthropic stream:
    begin() is exactly message_start (requested model, a msg_ id, the estimate as
    input_tokens), every event parses with type == its name, blocks open at
    consecutive indices one at a time with their deltas inside, and nothing
    follows the one terminal event (message_stop or error)."""
    problems = []
    events = run["events"]
    if run["out"] and not run["out"].endswith(b"\n\n"):
        problems.append("%s: the output does not end at an event boundary" % label)
    first = gg_events(run["begin"])
    if gg_names(first) != ["message_start"]:
        problems.append("%s: begin() returned %s, expected exactly one message_start"
                        % (label, gg_names(first) or "nothing"))
    else:
        msg = (first[0][1] or {}).get("message") or {}
        if msg.get("model") != requested:
            problems.append("%s: message_start.message.model %r, expected the requested %r (KD-16)"
                            % (label, msg.get("model"), requested))
        if not (isinstance(msg.get("id"), str) and GG_MSG_ID_RE.match(msg["id"])):
            problems.append("%s: message_start.message.id %r is not a msg_ id" % (label, msg.get("id")))
        if (msg.get("usage") or {}).get("input_tokens") != GG_INPUT_TOKENS:
            problems.append("%s: message_start usage %r, expected input_tokens %d (the estimate)"
                            % (label, msg.get("usage"), GG_INPUT_TOKENS))
        if msg.get("role") != "assistant" or msg.get("content") != []:
            problems.append("%s: message_start.message role/content %r/%r"
                            % (label, msg.get("role"), msg.get("content")))
    if gg_names(events).count("message_start") != 1:
        problems.append("%s: %d message_start events, expected 1"
                        % (label, gg_names(events).count("message_start")))
    open_index, next_index, terminal = None, 0, None
    for i, (name, obj) in enumerate(events):
        if terminal is not None:
            problems.append("%s: event %d (%s) follows the %s" % (label, i, name, terminal))
            break
        if obj is None:
            problems.append("%s: event %d (%s) does not parse as a JSON object" % (label, i, name))
            continue
        if obj.get("type") != name:
            problems.append("%s: event %d is named %s but typed %r" % (label, i, name, obj.get("type")))
        if name == "content_block_start":
            if open_index is not None:
                problems.append("%s: block %r started while block %r is open" % (label, obj.get("index"), open_index))
            if obj.get("index") != next_index:
                problems.append("%s: block index %r, expected %d" % (label, obj.get("index"), next_index))
            open_index, next_index = obj.get("index"), next_index + 1
        elif name == "content_block_delta":
            if open_index is None or obj.get("index") != open_index:
                problems.append("%s: a delta for block %r, which is not open" % (label, obj.get("index")))
        elif name == "content_block_stop":
            if open_index is None or obj.get("index") != open_index:
                problems.append("%s: a stop for block %r, which is not open" % (label, obj.get("index")))
            open_index = None
        elif name == "message_delta" and open_index is not None:
            problems.append("%s: message_delta while block %r is open" % (label, open_index))
        elif name in GG_TERMINALS:
            terminal = name
    if terminal is None:
        problems.append("%s: no message_stop and no event: error (%s)" % (label, ev_shape(events)))
    return problems


def gg_blocks(events):
    """[{index, type, id, name, input, texts, json, deltas}] of the content blocks in order."""
    blocks = []
    for name, obj in events:
        if obj is None:
            continue
        if name == "content_block_start":
            cb = obj.get("content_block") or {}
            blocks.append({"index": obj.get("index"), "type": cb.get("type"), "id": cb.get("id"),
                           "name": cb.get("name"), "input": cb.get("input"), "texts": [], "json": "",
                           "deltas": 0})
        elif name == "content_block_delta" and blocks:
            delta = obj.get("delta") or {}
            blocks[-1]["deltas"] += 1
            if delta.get("type") == "text_delta":
                blocks[-1]["texts"].append(delta.get("text"))
            elif delta.get("type") == "input_json_delta":
                blocks[-1]["json"] += delta.get("partial_json") or ""
    return blocks


def gg_message_delta(events):
    for name, obj in events:
        if name == "message_delta" and obj is not None:
            return obj
    return None


def gg_complete(label, run, stop_reason, usage=None):
    """Problems unless the stream completed: no error, message_stop last, and its
    message_delta carries *stop_reason* and (when *usage* is given) its tokens."""
    events = run["events"]
    problems = []
    names = gg_names(events)
    if "error" in names or names[-1:] != ["message_stop"]:
        errors = [o.get("error") for n, o in events if n == "error" and o]
        problems.append("%s: the stream did not complete with message_stop (%s)%s"
                        % (label, ev_shape(events),
                           (" -- error %r" % gi_redact(json.dumps(errors[0]))[:160]) if errors else ""))
        return problems
    md = gg_message_delta(events) or {}
    want = {"stop_reason": stop_reason, "stop_sequence": None}
    if md.get("delta") != want:
        problems.append("%s: message_delta.delta %r, expected %r" % (label, md.get("delta"), want))
    if usage is not None:
        got = md.get("usage") if isinstance(md.get("usage"), dict) else {}
        if got.get("output_tokens") != usage.get("completion_tokens"):
            problems.append("%s: message_delta usage %r, expected output_tokens %r"
                            % (label, got, usage.get("completion_tokens")))
        if "input_tokens" in got and got["input_tokens"] != usage.get("prompt_tokens"):
            problems.append("%s: message_delta usage %r, input_tokens is not the chunk's prompt_tokens %r"
                            % (label, got, usage.get("prompt_tokens")))
        if set(got) - {"output_tokens", "input_tokens"}:
            problems.append("%s: message_delta usage carries %s" % (label, sorted(set(got) - {"output_tokens",
                                                                                                "input_tokens"})))
    return problems


def gg_failed(label, run):
    """Problems unless the stream ended in ONE event: error (an Anthropic type) and no message_stop."""
    events = run["events"]
    problems = []
    names = gg_names(events)
    if names.count("error") != 1 or names[-1:] != ["error"]:
        problems.append("%s: expected exactly one event: error, last (%s)" % (label, ev_shape(events)))
    if "message_stop" in names:
        problems.append("%s: a message_stop was emitted after a failure (KD-7)" % label)
    for name, obj in events:
        if name == "error" and obj is not None:
            etype = (obj.get("error") or {}).get("type")
            if etype not in GG_ANTHROPIC_TYPES:
                problems.append("%s: error.type %r is not an Anthropic error type" % (label, etype))
    return problems


def gg_error_message(events):
    for name, obj in events:
        if name == "error" and obj is not None:
            return (obj.get("error") or {}).get("message")
    return None


def gg_anthropic_id(tid):
    return GF_MINTED + tid


def gg_tool_check(label, block, tool):
    """Problems unless *block* is the whole tool call *tool* ({"id", "name", "args"}): a
    tool_use start with the minted id toolu_lr<9>, the name, input {}, ONE
    input_json_delta whose JSON equals the joined arguments."""
    problems = []
    if not GF_ID_RE.match(tool["id"]):
        problems.append("%s: precondition: the upstream id %r is not 9 alnum" % (label, tool["id"]))
    if block.get("type") != "tool_use":
        return problems + ["%s: block %r is %r, expected tool_use" % (label, block.get("index"), block.get("type"))]
    if block.get("id") != gg_anthropic_id(tool["id"]):
        problems.append("%s: tool id %r, expected %r (KD-6 minted form)"
                        % (label, block.get("id"), gg_anthropic_id(tool["id"])))
    if block.get("name") != tool["name"]:
        problems.append("%s: tool name %r, expected %r" % (label, block.get("name"), tool["name"]))
    if block.get("input") != {}:
        problems.append("%s: content_block_start input %r, expected {}" % (label, block.get("input")))
    if block.get("deltas") != 1:
        problems.append("%s: %d delta(s) in the tool block, expected ONE complete input_json_delta (KD-5)"
                        % (label, block.get("deltas")))
    try:
        if json.loads(block.get("json") or "") != json.loads(tool["args"]):
            problems.append("%s: input_json %r, expected %r" % (label, block.get("json")[:120], tool["args"]))
    except ValueError:
        problems.append("%s: input_json %r does not parse" % (label, (block.get("json") or "")[:120]))
    return problems


def gg_fragments(total, valid):
    """Argument fragments totalling exactly *total* bytes: '{"d":"' + x... + '"}' when
    *valid*, else the string is never closed."""
    head, tail = '{"d":"', ('"}' if valid else "")
    body = total - len(head) - len(tail)
    frags = [head]
    while body > 0:
        n = min(GG_FRAGMENT, body)
        frags.append("x" * n)
        body -= n
    if tail:
        frags.append(tail)
    return frags


def gg_cap_lines(tools, finish=True):
    """(lines, marks, total): stream lines carrying each (index, tid, name, frags) of
    *tools* in turn; *marks* is [(index in lines of a data line, running argument bytes
    after it)] for gg_crossing; *total* the argument bytes of the whole stream."""
    lines = ms_lines(MS_ROLE, done=False)
    marks = []                      # (index in lines of a data line, running argument bytes after it)
    total = 0
    for index, tid, name, frags in tools:
        for i, frag in enumerate(frags):
            item = ms_tool(index, frag, tid, name) if i == 0 else ms_tool(index, frag)
            marks.append((len(lines), total + len(frag.encode("utf-8"))))
            total += len(frag.encode("utf-8"))
            lines += ms_lines(ms_tools(item), done=False)
    if finish:
        lines += ms_lines(ms_stop("tool_calls", ms_usage(9, 9)))
    return lines, marks, total


def gg_crossing(marks, bound):
    """The index in lines of the first data line whose running total exceeds *bound*."""
    for at, running in marks:
        if running > bound:
            return at
    return None


def gg_first_error_feed(run):
    for i, chunk in enumerate(run["feeds"]):
        if b"event: error" in chunk:
            return i
    return None


def gg_strip_id(events):
    """*events* with message_start's id blanked (two runs mint two ids)."""
    out = []
    for name, obj in events:
        if name == "message_start" and obj is not None:
            obj = json.loads(json.dumps(obj))
            obj.get("message", {})["id"] = "msg_"
        out.append((name, obj))
    return out


def gg_live_events(out):
    """[(name, parsed object or None)] of a live read_stream result."""
    events = []
    for name, data, _t in out["events"]:
        try:
            obj = json.loads(data)
        except ValueError:
            obj = None
        events.append((name, obj if isinstance(obj, dict) else None))
    return events


class GgLive:
    """G15-G17's fixture: a VERIFIED-TLS Mistral-kind peer (the sandbox copy of
    test-ca.pem as ca_file), routes claude-tf-g15 / claude-tf-g16 and a default
    route, one --debug router.  The peer answers by (upstream model, stream, a
    tool message in the history): G15's first turn the tool fixture, every other
    stream the text fixture, every non-stream request GG_JSON_ANSWER."""

    def __init__(self, fixture_root, fx):
        self.fx = fx
        self.sandbox = new_sandbox(fixture_root, "g-live")
        self.peer = ScriptedPeer(script=self.script, tls=True)
        self.proc = None
        self.client = None
        self.why = None
        try:
            entry = {"kind": "mistral", "base_url": self.peer.url(), "api_key": TF_KEY_MISTRAL,
                     "ca_file": sandbox_ca_file(self.sandbox)}
            cfg = {"auth_token": TF_ROUTER_TOKEN, "backends": {GG_BACKEND: entry},
                   "routes": {route: {"backend": GG_BACKEND, "model": model}
                              for route, model in GG_LIVE_ROUTES.items()},
                   "default": {"backend": GG_BACKEND, "model": GG_DEFAULT_MODEL}}
            self.proc = RouterProc(self.sandbox, write_config(self.sandbox, cfg), label="g-live",
                                   extra_argv=("--debug",))
            if self.proc.wait_ready() is None:
                self.why = gb_not_ready(self.proc)
            else:
                self.client = self.proc.client()
        except BaseException:
            self.close()
            raise

    def script(self, _index, request):
        try:
            body = json.loads((request.get("body") or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = None
        if not isinstance(body, dict):
            return [("respond_json", 400, {"object": "error", "message": "tf no script", "type": "tf"})]
        has_tool = any(isinstance(m, dict) and m.get("role") == "tool" for m in body.get("messages") or [])
        if body.get("stream"):
            first_g15 = body.get("model") == GG_LIVE_ROUTES[GG_G15_ROUTE] and not has_tool
            return [("respond_sse", self.fx["tool"] if first_g15 else self.fx["text"])]
        return [("respond_json", 200, GG_JSON_ANSWER)]

    def bodies(self, model):
        """The parsed upstream bodies the peer recorded for upstream *model*."""
        out = []
        for request in list(self.peer.requests):
            try:
                body = json.loads((request.get("body") or b"").decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(body, dict) and body.get("model") == model:
                out.append((request.get("line") or "", body))
        return out

    def close(self):
        if self.proc is not None:
            self.proc.close()
        self.peer.close()


def gg_body(model, stream, messages=None, **extra):
    body = {"model": model, "max_tokens": 64, "stream": stream,
            "messages": messages if messages is not None else [{"role": "user", "content": "tf g live"}]}
    body.update(extra)
    return body


def gg_echo(live, requested, upstream):
    """(problems, detail): G16/G17 -- a stream and a non-stream request for *requested*:
    message_start.message.model and the JSON answer's model are *requested* (KD-16), and
    the peer was asked for *upstream*."""
    if live is None or live.client is None:
        return [live.why if live is not None else "the live rig was not built"], []
    problems, detail = [], []
    conn, resp, problems = open_stream(live.client, gg_body(requested, True))
    if not problems:
        try:
            out = read_stream(resp)
        finally:
            close_stream(conn, resp)
        events = gg_live_events(out)
        model = None
        if gg_names(events)[:1] == ["message_start"] and events[0][1] is not None:
            model = (events[0][1].get("message") or {}).get("model")
        if model != requested:
            problems.append("stream: message_start.message.model %r, expected the requested %r" % (model, requested))
        if gg_names(events)[-1:] != ["message_stop"] or "error" in gg_names(events):
            problems.append("stream: did not complete with message_stop (%s)" % ev_shape(events))
        problems += ev_unparsed(out["events"])
        detail.append("stream      : model %r, %s" % (model, ev_shape(events)))
    else:
        problems = ["stream: " + p for p in problems]
    status, _hdrs, body = live.client.post(obj=gg_body(requested, False))
    if status != 200:
        problems += answer_status("non-stream", status, body, 200)
    else:
        try:
            model = json.loads(body.decode("utf-8")).get("model")
        except (UnicodeDecodeError, ValueError, AttributeError):
            model = "(unparsable)"
        if model != requested:
            problems.append("non-stream: model %r, expected the requested %r" % (model, requested))
        detail.append("non-stream  : model %r" % (model,))
    if not live.bodies(upstream):
        problems.append("the peer recorded no request for upstream model %s" % upstream)
    return problems, detail


def group_g(suite, fixture_root):
    """G. mistral stream: the chat-completions SSE -> Anthropic SSE state machine
    (Step 11), in-process through the plan's sans-IO `MistralStreamTranslator(inbound,
    input_tokens)` -- begin() / feed(line) / finish() driven as the relay loop drives
    it -- plus `MistralAdapter.json_response` (G14), and live over verified TLS (G15-G17).

    Every in-process case first asserts the bytes form a well-formed Anthropic stream
    (gg_wellformed), then its own rule.  G1-G3 replay synthetic fixtures and say
    "defensive, not observed" until M5 runs.  Before Step 11 lands every in-process
    case FAILS naming MistralStreamTranslator (G14: the Adapter base's json_response),
    and every live case FAILS on the 501 "backend kind mistral is not wired yet"."""
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_g", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GG_CASES:
            suite.record(GG, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__, exc)])
        return
    try:
        fx, fx_why = gg_fixtures(), None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fx, fx_why = None, "the G fixtures cannot be read: %s" % type(exc).__name__
    limit = getattr(mod, "_RT_BUFFERED_TOOL_LIMIT", GG_TOOL_LIMIT_FALLBACK)

    def drive(lines, eof=True):
        """(run, problems): a fresh translator driven over *lines*; (None, [why]) before Step 11."""
        cls = getattr(mod, "MistralStreamTranslator", None)
        if not callable(cls):
            return None, ["the module defines no MistralStreamTranslator (Step 11 not landed)"]
        return gg_drive(cls(gg_inbound(mod), GG_INPUT_TOKENS), lines, eof), []

    def g1():
        if fx is None:
            return [fx_why], [GG_DEFENSIVE]
        chunks = gg_chunks(fx["text"])
        run, problems = drive(fx["text"])
        if run is None:
            return problems, [GG_DEFENSIVE]
        problems += gg_wellformed("text fixture", run)
        texts = gg_fx_texts(chunks)
        want = [("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}})]
        want += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": t}}) for t in texts]
        want += [("content_block_stop", {"type": "content_block_stop", "index": 0})]
        events = run["events"]
        want_names = ["message_start"] + gg_names(want) + ["message_delta", "message_stop"]
        if gg_names(events) != want_names:
            problems.append("events %s, expected %s" % (ev_shape(events), ", ".join(want_names)))
        else:
            for i, ((_wn, wobj), (_gn, gobj)) in enumerate(zip(want, events[1:])):
                if gobj != wobj:
                    problems.append("event %d is %r, expected %r" % (i + 1, gobj, wobj))
            problems += gg_complete("text fixture", run, "end_turn", gg_fx_usage(chunks))
            if events[-1][1] != {"type": "message_stop"}:
                problems.append("message_stop is %r" % (events[-1][1],))
        return problems, ["fixture     : %s, %d text chunk(s)" % (GG_FIXTURES["text"], len(texts)),
                          "client      : %s" % ev_shape(events), GG_DEFENSIVE]

    def g2():
        if fx is None:
            return [fx_why], [GG_DEFENSIVE]
        chunks = gg_chunks(fx["tool"])
        tool = gg_fx_tools(chunks)[0]
        run, problems = drive(fx["tool"])
        if run is None:
            return problems, [GG_DEFENSIVE]
        problems += gg_wellformed("tool fixture", run)
        want = ["message_start", "content_block_start", "content_block_delta", "content_block_stop",
                "message_delta", "message_stop"]
        if gg_names(run["events"]) != want:
            problems.append("events %s, expected %s" % (ev_shape(run["events"]), ", ".join(want)))
        blocks = gg_blocks(run["events"])
        if blocks:
            problems += gg_tool_check("tool fixture", blocks[0], tool)
        problems += gg_complete("tool fixture", run, "tool_use", gg_fx_usage(chunks))
        return problems, ["tool        : %s %s, %d argument fragment(s) upstream"
                          % (tool["id"], tool["name"],
                             sum(1 for c in chunks if gg_delta(c).get("tool_calls"))),
                          "client      : %s" % ev_shape(run["events"]), GG_DEFENSIVE]

    def g3():
        if fx is None:
            return [fx_why], [GG_DEFENSIVE]
        problems, shown = [], []
        reversed_first = ms_lines(MS_ROLE,
                                  ms_tools(ms_tool(1, '{"command": ', "tfToolB03", "Bash")),
                                  ms_tools(ms_tool(0, '{"file_path": ', "tfToolA03", "Read")),
                                  ms_tools(ms_tool(1, '"ls /tf/g3"}')),
                                  ms_tools(ms_tool(0, '"/tf/g3.txt"}')),
                                  ms_stop("tool_calls", ms_usage(30, 20)))
        for label, lines in (("parallel fixture (0, 1, 0, 1)", fx["parallel"]),
                             ("index 1 arrives first (1, 0, 1, 0)", reversed_first)):
            chunks = gg_chunks(lines)
            tools = gg_fx_tools(chunks)
            run, more = drive(lines)
            if run is None:
                return more, [GG_DEFENSIVE]
            problems += gg_wellformed(label, run)
            blocks = gg_blocks(run["events"])
            if len(blocks) != 2:
                problems.append("%s: %d block(s) (%s), expected two tool blocks"
                                % (label, len(blocks), ev_shape(run["events"])))
            else:
                for key, block in zip(sorted(tools), blocks):
                    problems += gg_tool_check("%s, index %d" % (label, key), block, tools[key])
            problems += gg_complete(label, run, "tool_use", gg_fx_usage(chunks))
            shown.append("%s -> %s" % (label, [b.get("name") for b in blocks]))
        return problems, ["order       : " + s for s in shown] + [GG_DEFENSIVE]

    def g4():
        lines = ms_lines(MS_ROLE, ms_text("tf g4 before"),
                         ms_tools(ms_tool(0, '{"file_path": "/tf/g4.txt"}', "tfToolG04", "Read")),
                         ms_text(" tf g4 after"), ms_stop("tool_calls", ms_usage(10, 12)))
        run, problems = drive(lines)
        if run is None:
            return problems, []
        problems += gg_wellformed("text, tool, text", run)
        want = ["message_start", "content_block_start", "content_block_delta", "content_block_delta",
                "content_block_stop", "content_block_start", "content_block_delta", "content_block_stop",
                "message_delta", "message_stop"]
        if gg_names(run["events"]) != want:
            problems.append("events %s, expected %s" % (ev_shape(run["events"]), ", ".join(want)))
        blocks = gg_blocks(run["events"])
        if len(blocks) == 2:
            if blocks[0]["type"] != "text" or blocks[0]["texts"] != ["tf g4 before", " tf g4 after"]:
                problems.append("block 0 is %r %r, expected the text block with both deltas"
                                % (blocks[0]["type"], blocks[0]["texts"]))
            problems += gg_tool_check("block 1", blocks[1], {"id": "tfToolG04", "name": "Read",
                                                             "args": '{"file_path": "/tf/g4.txt"}'})
        problems += gg_complete("text, tool, text", run, "tool_use")
        return problems, ["client      : %s" % ev_shape(run["events"])]

    def g5():
        lines = ms_lines(MS_ROLE,
                         ms_chunk({"content": [{"type": "text", "text": "tf g5 a"},
                                               {"type": "thinking", "thinking": [{"type": "text",
                                                                                  "text": "tf g5 hidden"}]},
                                               {"type": "text", "text": "tf g5 b"}]}),
                         ms_chunk({"content": [{"type": "thinking", "thinking": [{"type": "text",
                                                                                   "text": "tf g5 hidden 2"}]}]}),
                         ms_chunk({"content": [{"type": "text", "text": " tf g5 c"}]}),
                         ms_stop("stop", ms_usage(5, 6)))
        run, problems = drive(lines)
        if run is None:
            return problems, []
        problems += gg_wellformed("content lists", run)
        blocks = gg_blocks(run["events"])
        text = "".join(t or "" for b in blocks for t in b["texts"])
        if [b["type"] for b in blocks] != ["text"]:
            problems.append("blocks %s, expected one text block" % [b["type"] for b in blocks])
        if not ("tf g5 a" in text and "tf g5 b" in text and " tf g5 c" in text
                and text.index("tf g5 a") < text.index("tf g5 b") < text.index(" tf g5 c")):
            problems.append("text %r does not hold the three text parts in order" % text)
        if "hidden" in run["out"].decode("utf-8", "replace"):
            problems.append("a thinking part reached the client")
        if any(t == "" for b in blocks for t in b["texts"]):
            problems.append("an empty text_delta was emitted for the thinking-only chunk")
        # The plan's DEBUG counter (§9 dropped_thinking=<n>): two thinking parts were dropped.
        dropped = getattr(run["tr"], "dropped_thinking", None)
        if dropped != 2:
            problems.append("dropped_thinking is %r, expected 2 (one per dropped thinking part)" % (dropped,))
        return problems, ["text        : %r" % text, "dropped     : dropped_thinking %r" % (dropped,)]

    def g6():
        def padded(chunk):
            chunk = json.loads(json.dumps(chunk))
            chunk["p"] = GG_PAD
            for choice in chunk.get("choices") or []:
                choice["p"] = GG_PAD
                choice["delta"]["p"] = GG_PAD
            return chunk
        chunks = [MS_ROLE, ms_text("tf g6 a"),
                  ms_tools(ms_tool(0, '{"file_path": "/tf/g6"}', "tfToolG06", "Read")),
                  ms_text(" tf g6 b"), ms_stop("tool_calls", ms_usage(7, 8))]
        plain, problems = drive(ms_lines(*chunks))
        if plain is None:
            return problems, []
        with_p, _ = drive(ms_lines(*[padded(c) for c in chunks]))
        problems += gg_wellformed("without p", plain) + gg_wellformed("with p", with_p)
        if gg_strip_id(with_p["events"]) != gg_strip_id(plain["events"]):
            problems.append("the stream with \"p\" keys gives %s, without them %s"
                            % (ev_shape(with_p["events"]), ev_shape(plain["events"])))
        if GG_PAD.encode("ascii") in with_p["out"]:
            problems.append("a \"p\" value reached the client")
        problems += gg_complete("with p", with_p, "tool_use")
        return problems, ["client      : %s" % ev_shape(with_p["events"])]

    def g7():
        problems, shown = [], []
        final = ms_usage(11, 37)
        table = (("usage on the finish chunk only",
                  ms_lines(MS_ROLE, ms_text("tf g7"), ms_stop("stop", final))),
                 ("an earlier usage, then the last",
                  ms_lines(MS_ROLE, ms_chunk({"content": "tf g7"}, usage=ms_usage(11, 1)), ms_stop("stop", final))),
                 ("usage on a trailing chunk with empty choices",
                  ms_lines(MS_ROLE, ms_text("tf g7"), ms_stop("stop"),
                           dict(ms_chunk(usage=final), choices=[]))))
        for label, lines in table:
            run, more = drive(lines)
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            problems += gg_complete(label, run, "end_turn", final)
            md = gg_message_delta(run["events"]) or {}
            shown.append("%s -> %r" % (label, md.get("usage")))
        return problems, ["usage       : " + s for s in shown]

    def g8():
        problems, shown = [], []
        for reason in ("length", "model_length"):
            run, more = drive(ms_lines(MS_ROLE, ms_text("tf g8"), ms_stop(reason, ms_usage(3, 64))))
            if run is None:
                return more, []
            problems += gg_wellformed(reason, run)
            problems += gg_complete("finish_reason %s" % reason, run, "max_tokens", ms_usage(3, 64))
            shown.append("%s -> %r" % (reason, ((gg_message_delta(run["events"]) or {}).get("delta") or {})
                                       .get("stop_reason")))
        return problems, ["stop_reason : " + s for s in shown]

    def g9():
        lines = ms_lines(MS_ROLE, ms_tools(ms_tool(0, '{"command": "ls"}', "tfToolG09", "Bash")),
                         ms_stop("stop", ms_usage(4, 9)))
        run, problems = drive(lines)
        if run is None:
            return problems, []
        problems += gg_wellformed("tool + stop", run)
        problems += gg_complete("finish_reason stop with a tool", run, "tool_use", ms_usage(4, 9))
        return problems, ["client      : %s" % ev_shape(run["events"])]

    def g10():
        problems, shown = [], []
        table = (("object: error", {"object": "error", "message": "tf g10 overloaded " + TF_KEY_MISTRAL,
                                    "type": "service_tier_capacity_exceeded", "param": None, "code": "3505"}),
                 ("top-level error", {"error": {"message": "tf g10 upstream " + TF_KEY_MISTRAL,
                                                "type": "internal_error", "code": 500}}),
                 ("message, no choices", {"message": "tf g10 bare " + TF_KEY_MISTRAL, "code": 1000}))
        for label, chunk in table:
            lines = ms_lines(MS_ROLE, ms_text("tf g10 before"), chunk, ms_text("tf g10 after"),
                             ms_stop("stop", ms_usage(1, 2)))
            run, more = drive(lines)
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            problems += gg_failed(label, run)
            text = run["out"].decode("utf-8", "replace")
            if "tf g10 before" not in text:
                problems.append("%s: the text before the error chunk never reached the client" % label)
            if "tf g10 after" in text:
                problems.append("%s: text after the error chunk was emitted" % label)
            msg = gg_error_message(run["events"])
            if not isinstance(msg, str) or gi_redact(msg) != msg or "[redacted]" not in msg:
                problems.append("%s: the error message %r is not the upstream text scrubbed (KD-9)"
                                % (label, gi_redact(str(msg))[:160]))
            after = run["feeds"][lines.index(ms_lines(ms_text("tf g10 after"), done=False)[0]):] + [run["end"]]
            if any(after):
                problems.append("%s: feed()/finish() returned bytes after the error" % label)
            shown.append("%s -> %r" % (label, gi_redact(str(msg))[:80]))
        return problems, ["error       : " + s for s in shown]

    def g11():
        problems, shown = [], []
        table = (("text, then EOF (no finish_reason, no [DONE])",
                  ms_lines(MS_ROLE, ms_text("tf g11"), ms_text(" more"), done=False), False),
                 ("only a comment, then EOF", [": tf keep-alive", ""], False),
                 ("control: finish_reason, then EOF (no [DONE])",
                  ms_lines(MS_ROLE, ms_text("tf g11"), ms_stop("stop", ms_usage(2, 3)), done=False), True))
        for label, lines, completes in table:
            run, more = drive(lines)
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            if completes:
                problems += gg_complete(label, run, "end_turn", ms_usage(2, 3))
            else:
                problems += gg_failed(label, run)
            shown.append("%s -> %s" % (label, ev_shape(run["events"])))
        return problems, ["eof         : " + s for s in shown]

    def g12():
        problems, shown = [], []
        bad = ms_lines(MS_ROLE, ms_tools(ms_tool(0, '{"file_path": "/tf/g12', "tfToolG12", "Read")),
                       ms_stop("tool_calls", ms_usage(2, 2)))
        run, more = drive(bad)
        if run is None:
            return more, []
        problems += gg_wellformed("malformed arguments", run) + gg_failed("malformed arguments", run)
        if any(b["type"] == "tool_use" for b in gg_blocks(run["events"])):
            problems.append("malformed arguments: a tool_use block was emitted for them")
        shown.append("malformed arguments -> %s" % ev_shape(run["events"]))
        half = limit // 2
        table = (("one tool of limit + 1 bytes", [(0, "tfToolC01", "Write", gg_fragments(limit + 1, False))],
                  False),
                 ("two tools of limit + 1 bytes together",
                  [(0, "tfToolC02", "Write", gg_fragments(half, False)),
                   (1, "tfToolC03", "Write", gg_fragments(limit - half + 1, False))], False),
                 # V33: the stored id and name count against the same cap as the arguments.
                 ("control: one tool of exactly limit bytes (id + name + arguments)",
                  [(0, "tfToolC04", "Write", gg_fragments(limit - len("tfToolC04Write"), True))], True))
        for label, tools, fits in table:
            lines, marks, total = gg_cap_lines(tools)
            run, more = drive(lines)
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            if fits:
                problems += gg_complete(label, run, "tool_use")
                blocks = gg_blocks(run["events"])
                if len(blocks) != 1 or len(blocks[0]["json"].encode("utf-8")) != total:
                    problems.append("%s: tool block(s) %s, expected one carrying all %d bytes"
                                    % (label, [len(b["json"]) for b in blocks], total))
            else:
                problems += gg_failed(label, run)
                crossing, first = gg_crossing(marks, limit), gg_first_error_feed(run)
                if first is None or crossing is None or first > crossing:
                    problems.append("%s: the error came at line %s, expected by line %s (the chunk that "
                                    "crossed the cap, NFR-2b)" % (label, first, crossing))
                if len(run["out"]) > GG_LEAK_BOUND:
                    problems.append("%s: %d bytes reached the client" % (label, len(run["out"])))
            shown.append("%s (%d bytes) -> %s" % (label, total, ev_shape(run["events"])))
        # F1 (V31): the joined arguments must parse strictly to a JSON object, as on the
        # non-stream path (_rt_ms_tool_use); an `arguments` value that is a list is refused.
        for label, args in (("an array", "[1,2]"), ("a string", '"tf g12"'), ("a number", "3"),
                            ("null", "null"), ("NaN", "NaN"), ("an object holding NaN", '{"d":NaN}'),
                            ("an object holding 1e999", '{"d":1e999}'), ("arguments as a list", [1, 2])):
            run, more = drive(ms_lines(MS_ROLE, ms_tools(ms_tool(0, args, "tfToolG12", "Read")),
                                       ms_stop("tool_calls", ms_usage(2, 2))))
            if run is None:
                return more, []
            label = "arguments %s" % label
            problems += gg_wellformed(label, run) + gg_failed(label, run)
            if any(b["type"] == "tool_use" for b in gg_blocks(run["events"])):
                problems.append("%s: a tool_use block was emitted for them" % label)
            shown.append("%s -> %s" % (label, ev_shape(run["events"])))
        for label, args in (("control: spaced object arguments are re-dumped", '{"file_path": "/tf/g12",  "n": 1}'),
                            ("control: object arguments", {"file_path": "/tf/g12", "n": 1})):
            run, more = drive(ms_lines(MS_ROLE, ms_tools(ms_tool(0, args, "tfToolG12", "Read")),
                                       ms_stop("tool_calls", ms_usage(2, 2))))
            if run is None:
                return more, []
            problems += gg_wellformed(label, run) + gg_complete(label, run, "tool_use")
            blocks = gg_blocks(run["events"])
            if [b["json"] for b in blocks] != ['{"file_path":"/tf/g12","n":1}']:
                problems.append("%s: input_json %r, expected the compact re-dump" % (label, [b["json"] for b in blocks]))
            shown.append("%s -> %s" % (label, ev_shape(run["events"])))
        # V33: at most _RT_BUFFERED_TOOL_COUNT tools per stream, and a name alone over the cap.
        count = getattr(mod, "_RT_BUFFERED_TOOL_COUNT", GG_TOOL_COUNT_FALLBACK)
        for label, n, fits in (("count + 1 tools", count + 1, False), ("control: count tools", count, True)):
            run, more = drive(ms_lines(MS_ROLE, *[ms_tools(ms_tool(i, "{}", "tfTo%05d" % i, "Read"))
                                                  for i in range(n)], ms_stop("tool_calls", ms_usage(2, 2))))
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            if fits:
                problems += gg_complete(label, run, "tool_use")
                if len(gg_blocks(run["events"])) != n:
                    problems.append("%s: %d block(s), expected %d" % (label, len(gg_blocks(run["events"])), n))
            else:
                problems += gg_failed(label, run)
            shown.append("%s (%d) -> %s" % (label, n, ev_shape(run["events"])[:80]))
        for label, tid, name, args in (("a name of limit + 1 bytes", "tfToolC05", "N" * (limit + 1), "{}"),
                                       ("an id of limit bytes, then arguments", "i" * limit, "Read", '{"d":1}')):
            run, more = drive(ms_lines(MS_ROLE, ms_tools(ms_tool(0, args, tid, name)),
                                       ms_stop("tool_calls", ms_usage(2, 2))))
            if run is None:
                return more, []
            problems += gg_wellformed(label, run) + gg_failed(label, run)
            if len(run["out"]) > GG_LEAK_BOUND:
                problems.append("%s: %d bytes reached the client" % (label, len(run["out"])))
            shown.append("%s -> %s" % (label, ev_shape(run["events"])))
        return problems, (["limit       : _RT_BUFFERED_TOOL_LIMIT = %d, _RT_BUFFERED_TOOL_COUNT = %d" % (limit, count)]
                          + ["args        : " + s for s in shown])

    def g13():
        problems, shown = [], []
        first = ms_tools(ms_tool(0, '{"file_path": ', "tfToolG13", "Read"))
        table = (("a later delta renames the tool", ms_tools(ms_tool(0, '"/tf/g13"}', name="Bash")), False),
                 ("a later delta changes the id", ms_tools(ms_tool(0, '"/tf/g13"}', tid="tfToolX13")), False),
                 ("control: a later delta repeats id and name",
                  ms_tools(ms_tool(0, '"/tf/g13"}', "tfToolG13", "Read")), True))
        for label, later, completes in table:
            run, more = drive(ms_lines(MS_ROLE, first, later, ms_stop("tool_calls", ms_usage(3, 3))))
            if run is None:
                return more, []
            problems += gg_wellformed(label, run)
            if completes:
                problems += gg_complete(label, run, "tool_use")
                blocks = gg_blocks(run["events"])
                if len(blocks) == 1:
                    problems += gg_tool_check(label, blocks[0], {"id": "tfToolG13", "name": "Read",
                                                                 "args": '{"file_path": "/tf/g13"}'})
                else:
                    problems.append("%s: %d blocks, expected one tool block" % (label, len(blocks)))
            else:
                problems += gg_failed(label, run)
            shown.append("%s -> %s" % (label, ev_shape(run["events"])))
        return problems, ["conflict    : " + s for s in shown]

    def g14():
        # The instance _post dispatches on when it is registered (Step 11), else the class.
        adapter, why = (getattr(mod, "ADAPTERS", None) or {}).get("mistral"), None
        if adapter is None:
            adapter, why = gf_adapter(mod, "json_response")
        if adapter is None:
            return [why.replace("Step 10", "Step 11")], []
        inbound = gg_inbound(mod, stream=False)
        problems, shown = [], []

        def answer(label, status, obj):
            body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
            got = adapter.json_response(inbound, status, body)
            if not (isinstance(got, tuple) and len(got) == 2 and isinstance(got[1], dict)):
                problems.append("%s: json_response returned %r, expected (status, dict)" % (label, type(got).__name__))
                return None, {}
            return got

        def message_checks(label, obj, content, stop_reason, usage):
            keys = {"id", "type", "role", "model", "content", "stop_reason", "stop_sequence", "usage"}
            if set(obj) != keys:
                problems.append("%s: keys %s, expected %s" % (label, sorted(obj), sorted(keys)))
            if obj.get("type") != "message" or obj.get("role") != "assistant":
                problems.append("%s: type/role %r/%r" % (label, obj.get("type"), obj.get("role")))
            if not (isinstance(obj.get("id"), str) and GG_MSG_ID_RE.match(obj["id"])):
                problems.append("%s: id %r is not a msg_ id" % (label, obj.get("id")))
            if obj.get("model") != GG_ROUTE:
                problems.append("%s: model %r, expected the requested %r (KD-16)" % (label, obj.get("model"), GG_ROUTE))
            if obj.get("stop_reason") != stop_reason or obj.get("stop_sequence", "absent") is not None:
                problems.append("%s: stop_reason/stop_sequence %r/%r, expected %r/None"
                                % (label, obj.get("stop_reason"), obj.get("stop_sequence", "absent"), stop_reason))
            if obj.get("usage") != {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}:
                problems.append("%s: usage %r, expected input_tokens %d / output_tokens %d"
                                % (label, obj.get("usage"), usage["prompt_tokens"], usage["completion_tokens"]))
            got = obj.get("content")
            if not isinstance(got, list) or len(got) != len(content):
                problems.append("%s: content %r, expected %d block(s)" % (label, got, len(content)))
                return
            for i, (block, want) in enumerate(zip(got, content)):
                if callable(want):
                    why_not = want(block)
                    if why_not:
                        problems.append("%s: content[%d] %r %s" % (label, i, block, why_not))
                elif block != want:
                    problems.append("%s: content[%d] is %r, expected %r" % (label, i, block, want))

        def foreign_bash(block):
            if not (isinstance(block, dict) and block.get("type") == "tool_use"
                    and isinstance(block.get("id"), str) and GF_FOREIGN_RE.match(block["id"])
                    and block.get("name") == "Bash" and block.get("input") == {"command": "ls"}):
                return "is not the Bash tool_use with a toolu_ + 24 id and input {command: ls}"
            return None

        usage = ms_usage(25, 15)
        full = ms_answer({"role": "assistant", "content": "tf g14 text",
                          "tool_calls": [{"id": "tfToolG14", "type": "function",
                                          "function": {"name": "Read", "arguments": '{"file_path":"/tf/g14 ✓"}'}},
                                         {"id": "call_tf-14.x", "type": "function",
                                          "function": {"name": "Bash", "arguments": '{"command":"ls"}'}}]},
                         "tool_calls", usage)
        status, obj = answer("text + two tool_calls", 200, full)
        if status is not None:
            if status != 200:
                problems.append("text + two tool_calls: status %r, expected 200" % status)
            message_checks("text + two tool_calls", obj,
                           [{"type": "text", "text": "tf g14 text"},
                            {"type": "tool_use", "id": gg_anthropic_id("tfToolG14"), "name": "Read",
                             "input": {"file_path": "/tf/g14 ✓"}},
                            foreign_bash], "tool_use", usage)
            shown.append("text + two tool_calls -> %s" % [b.get("type") for b in obj.get("content") or []])
        status, obj = answer("text, length", 200, ms_answer({"role": "assistant", "content": "tf g14 cut"},
                                                            "length", usage))
        if status is not None:
            message_checks("text, length", obj, [{"type": "text", "text": "tf g14 cut"}], "max_tokens", usage)
        status, obj = answer("tool only, stop", 200,
                             ms_answer({"role": "assistant", "content": None,
                                        "tool_calls": [{"id": "tfToolG15", "type": "function",
                                                        "function": {"name": "Read", "arguments": ""}}]},
                                       "stop", usage))
        if status is not None:
            message_checks("tool only (no content, empty arguments), stop", obj,
                           [{"type": "tool_use", "id": gg_anthropic_id("tfToolG15"), "name": "Read", "input": {}}],
                           "tool_use", usage)
        status, obj = answer("malformed arguments", 200,
                             ms_answer({"role": "assistant", "content": "",
                                        "tool_calls": [{"id": "tfToolG16", "type": "function",
                                                        "function": {"name": "Read", "arguments": '{"file_path": '}}]},
                                       "tool_calls", usage))
        if status is not None:
            etype = (obj.get("error") or {}).get("type") if isinstance(obj.get("error"), dict) else None
            if status != 502 or obj.get("type") != "error" or etype != "api_error":
                problems.append("malformed arguments: %r %r, expected 502 api_error" % (status, obj))
        # F1 (V31) parity with G12: a non-object or non-finite input is refused here too.
        for label, args in (("array arguments", "[1,2]"), ("NaN in arguments", '{"d":NaN}'),
                            ("arguments as a list", [1, 2])):
            body = json.dumps(ms_answer({"role": "assistant", "content": "",
                                         "tool_calls": [{"id": "tfToolG16", "type": "function",
                                                         "function": {"name": "Read", "arguments": args}}]},
                                        "tool_calls", usage)).encode("utf-8")
            status, obj = answer(label, 200, body)
            if status is not None:
                etype = (obj.get("error") or {}).get("type") if isinstance(obj.get("error"), dict) else None
                if status != 502 or etype != "api_error":
                    problems.append("%s: %r %r, expected 502 api_error" % (label, status, obj))
                shown.append("%s -> %r" % (label, status))
        try:
            with open(os.path.join(FIXTURES, GF_FIXTURE), "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            problems.append("%s cannot be read: %s" % (GF_FIXTURE, type(exc).__name__))
        else:
            status, obj = answer("422 extra inputs", 422, raw)
            if status is not None:
                err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
                msg = err.get("message") if isinstance(err.get("message"), str) else ""
                if status != 400 or obj.get("type") != "error" or err.get("type") != "invalid_request_error":
                    problems.append("422 extra inputs: %r %s, expected 400 invalid_request_error (KD-14)"
                                    % (status, json.dumps(obj)[:160]))
                missing = [k for k in ("metadata", "thinking", "top_k", "cache_control") if k not in msg]
                if missing:
                    problems.append("422 extra inputs: the message %r does not name %s" % (msg[:200], missing))
                leaked = [v for v in ("tf-user-0", "budget_tokens", "ephemeral", "Extra inputs") if v in msg]
                if leaked:
                    problems.append("422 extra inputs: the message carries values or raw detail text %s" % leaked)
                shown.append("422 -> %r %r" % (status, msg[:100]))
        return problems, ["mapping     : " + s for s in shown]

    live = {}

    def g15():
        rig = live.get("rig")
        if rig is None or rig.client is None:
            return [live.get("why") or (rig.why if rig is not None else "the live rig was not built")], [GG_DEFENSIVE]
        tool = gg_fx_tools(gg_chunks(fx["tool"]))[0]
        minted = gg_anthropic_id(tool["id"])
        tools = [{"name": "Read", "description": "tf read",
                  "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]
        first = [{"role": "user", "content": "tf g15 read"}]
        conn, resp, problems = open_stream(rig.client, gg_body(GG_G15_ROUTE, True, first, tools=tools))
        if problems:
            return ["turn 1: " + p for p in problems], [GG_DEFENSIVE]
        try:
            out = read_stream(resp)
        finally:
            close_stream(conn, resp)
        events = gg_live_events(out)
        problems += ev_unparsed(out["events"])
        blocks = [b for b in gg_blocks(events) if b["type"] == "tool_use"]
        if gg_names(events)[-1:] != ["message_stop"] or len(blocks) != 1:
            return problems + ["turn 1: %s, expected one tool_use block and message_stop" % ev_shape(events)], \
                [GG_DEFENSIVE]
        block = blocks[0]
        problems += gg_tool_check("turn 1", block, tool)
        try:
            tool_input = json.loads(block["json"])
        except ValueError:
            tool_input = {}
        history = first + [{"role": "assistant", "content": [{"type": "tool_use", "id": block["id"],
                                                               "name": block["name"], "input": tool_input}]},
                           {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block["id"],
                                                         "content": "tf g15 result"}]}]
        conn, resp, more = open_stream(rig.client, gg_body(GG_G15_ROUTE, True, history, tools=tools))
        if more:
            return problems + ["turn 2: " + p for p in more], [GG_DEFENSIVE]
        try:
            out2 = read_stream(resp)
        finally:
            close_stream(conn, resp)
        events2 = gg_live_events(out2)
        problems += ev_unparsed(out2["events"])
        if gg_names(events2)[-1:] != ["message_stop"] or "error" in gg_names(events2):
            problems.append("turn 2: %s, expected a completed stream" % ev_shape(events2))
        seen = [(line, body) for line, body in rig.bodies(GG_LIVE_ROUTES[GG_G15_ROUTE])
                if any(isinstance(m, dict) and m.get("role") == "tool" for m in body.get("messages") or [])]
        calls = tool_ids = None
        if len(seen) != 1:
            problems.append("the peer recorded %d resend(s) with a tool message, expected 1" % len(seen))
        else:
            line, body = seen[0]
            if not line.startswith("POST /v1/chat/completions "):
                problems.append("the resend went to %r, expected POST /v1/chat/completions" % line)
            msgs = body.get("messages") or []
            calls, tool_ids = gf_call_ids(msgs), gf_tool_ids(msgs)
            if calls != [(tool["id"], tool["name"])] or tool_ids != [tool["id"]]:
                problems.append("the peer minted %r but saw tool_calls %r and tool_call_ids %r in the resent "
                                "history (KD-6 exact round trip)" % (tool["id"], calls, tool_ids))
        return problems, ["turn 1      : %s, tool id %r" % (ev_shape(events), block.get("id")),
                          "turn 2      : %s" % ev_shape(events2),
                          "peer        : minted %r, saw tool_calls %r, tool_call_ids %r" % (minted[len(GF_MINTED):],
                                                                                           calls, tool_ids),
                          "tls         : verified (ca_file = sandbox copy of test-ca.pem)", GG_DEFENSIVE]

    def g16():
        return gg_echo(live.get("rig"), GG_G16_ROUTE, GG_LIVE_ROUTES[GG_G16_ROUTE])

    def g17():
        return gg_echo(live.get("rig"), GG_UNROUTED, GG_DEFAULT_MODEL)

    fns = (g1, g2, g3, g4, g5, g6, g7, g8, g9, g10, g11, g12, g13, g14, g15, g16, g17)
    try:
        for cid, fn in zip(GG_CASES[:14], fns[:14]):
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
        if fx is None:
            live["why"] = fx_why
        else:
            live["rig"] = GgLive(fixture_root, fx)
        for cid, fn in zip(GG_CASES[14:], fns[14:]):
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001
                results[cid] = (["case raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001
        setup = "the group setup raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    finally:
        if live.get("rig") is not None:
            live["rig"].close()
    for cid in GG_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GG, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group H: timeouts and disconnect
# ---------------------------------------------------------------------------

# The H table is kind-parametrised: h_cases(kind, ...) builds H1-H6 for any kind
# in H_PROFILES.  A profile says only what is kind-specific -- the backend entry
# and the UPSTREAM lines that make the client see its first event (message_start),
# its first content_block_delta and the rest of a complete answer, a cut tail, and
# an upstream 500 body.  Every client-side expectation is Anthropic, so the same
# assertions serve all three kinds.  Step 9 (task-041) added "llamacpp";
# Step 11 (task-047) added "mistral" (OpenAI-style `data:` chunks and
# `data: [DONE]`; the client's message_start comes from the translator's
# begin(), the head chunk only opens the upstream) plus the mistral-only H8/H9.

H_TITLES = (
    "ping in 16 s silence",              # H1
    "silent -> stalled error",           # H2
    "client gone -> closed",             # H3
    "SIGTERM -> overloaded",             # H4
    "500 -> JSON, no SSE",               # H5
    "cut mid-event: error",              # H6
)
GH_H7 = "pumps and threads released"   # H7, once (passthrough over verified TLS)
GH_H8 = "mist: ping while tools buffer"  # H8, mistral only (M2)
GH_H9 = "mist: EOF seen while buffering"  # H9, mistral only (M2)

H_ROUTE = "claude-tf-h"
H_UPSTREAM_MODEL = "tf-h-model"
H_SILENCE_S = 16.0               # H1: the upstream's silence after its first event
H_LONG_IDLE_S = 30               # H1, H3, H4, H7: idle_timeout above every silence (M11)
H_HOLD_S = 30.0                  # H3, H4, H7: the upstream holds the stream open and silent this long
H_STALL_S = 12.0                 # H2: the upstream's silence, far past idle_timeout 3
H_IDLE_S = 3                     # H2: the suite's default idle_timeout (LOOPBACK_DEFAULTS)
H_ERROR_SLACK_S = 2.0            # H2: the error must arrive within idle_timeout + this
H_CLOSE_S = 2.0                  # H3, H7: the peer must see its socket closed within this
H_EXIT_S = 5.0                   # H4: SIGTERM -> event: error and exit 0 within this
H_529_S = 1.0                    # H4: the in-process 529 must be answered within this
H_529_DECLARED = 4096            # H4: the Content-Length of the 529 request, body never sent
H_SETTLE_S = 3.0                 # H7: rt-pump count / active_count must settle within this
H7_ROUNDS = 5
H7_MAX_CONNECTIONS = 2
H_INPROC_NAMES = ("_RouterHttpServer", "RouterSettings", "load_config")
H_AN_PARTIAL = ["event: content_block_delta",
                'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", '
                '"text": "tf-h6-partial']
H_PARTIAL_MARKER = "tf-h6-partial"
# H6 x mistral: a chunk cut mid-JSON (the line ends, the object never does), then EOF.
H_MS_PARTIAL = ('data: {"id":"tfcmpl-g-inline","object":"chat.completion.chunk","choices":[{"index":0,'
                '"delta":{"content":"tf-h6-partial')
H_FRAGMENT_GAP_S = 1.0           # H8, H9: one tool-argument fragment per this
H8_FRAGMENTS = 20                # H8: 20 fragments ~ 20 s, past the 15 s ping interval with nothing written
H9_FRAGMENTS = 90                # H9: "indefinitely" -- the peer outlasts any sane hang-up detection
H9_CLOSE_S = 3.0                 # H9: the peer must see its socket closed within this


def h_entry_passthrough(url, **extra):
    entry = {"kind": "passthrough", "base_url": url, "api_key": TF_KEY_PASS, "auth_header": "x-api-key"}
    entry.update(extra)
    return entry


def h_entry_llamacpp(url, **extra):
    entry = {"kind": "llamacpp", "base_url": url}       # no key: auth_header defaults to none
    entry.update(extra)
    return entry


def h_entry_mistral(url, **extra):
    entry = {"kind": "mistral", "base_url": url, "api_key": TF_KEY_MISTRAL}   # auth_header: authorization
    entry.update(extra)
    return entry


H_PROFILES = {
    "passthrough": {
        "tag": "pass",
        "entry": h_entry_passthrough,
        "head": AN_HEAD,                 # upstream lines -> the client's message_start
        "delta": AN_DELTA,               # -> the client's first content_block_delta
        "finish": AN_FINISH,             # -> the rest, through message_stop
        "partial": H_AN_PARTIAL,         # an event cut mid-line, never completed
        "error_500": gd_error("api_error", "tf upstream internal error"),
        "inproc_529": True,              # H4's in-process 529 half runs on this kind only
    },
    "llamacpp": {
        "tag": "llama",
        "entry": h_entry_llamacpp,
        # llama-server speaks Anthropic SSE on /v1/messages: the same event lines, its own
        # model name (echoed back as the requested model by LlamacppRelay, KD-16).
        "head": an_lines(an_message_start(GE_LLAMA_MODEL)),
        "delta": AN_DELTA,
        "finish": AN_FINISH,
        "partial": H_AN_PARTIAL,
        "error_500": {"error": {"code": 500, "message": "tf llama.cpp internal error",
                                "type": "server_error"}},           # llama.cpp's error shape
        "inproc_529": False,
    },
    "mistral": {
        "tag": "mist",
        "entry": h_entry_mistral,
        # chat-completions chunks: the role chunk (message_start is begin()'s), one text
        # chunk (-> content_block_start + the first delta), the rest through [DONE].
        "head": ms_lines(MS_ROLE, done=False),
        "delta": ms_lines(ms_text("Hello"), done=False),
        "finish": ms_lines(ms_text(" wörld ✓"), ms_stop("stop", ms_usage(12, 5))),
        "partial": [H_MS_PARTIAL],
        "error_500": {"object": "error", "message": "tf mistral internal error",
                      "type": "internal_server_error", "param": None, "code": "1000"},   # Mistral's shape
        "inproc_529": False,
    },
}
H_KINDS = ("passthrough", "llamacpp", "mistral")


def h_case_ids(kind):
    tag = H_PROFILES[kind]["tag"]
    return tuple("%s: %s" % (tag, title) for title in H_TITLES)


def h_body(stream=True):
    return {"model": H_ROUTE, "max_tokens": 64, "stream": stream,
            "messages": [{"role": "user", "content": "tf h"}]}


def h_config(entry):
    return {"auth_token": TF_ROUTER_TOKEN, "backends": {"tfh": entry},
            "routes": {H_ROUTE: {"backend": "tfh", "model": H_UPSTREAM_MODEL}}}


class HRig:
    """One H case's fixture: a peer running *script*, one backend of *kind* at it
    (with *backend* keys over the loopback defaults), one route H_ROUTE, and a
    live --debug router.  `client` is None (and `why` says so) if it never got ready."""

    def __init__(self, fixture_root, kind, label, script, tls=False, **backend):
        profile = H_PROFILES[kind]
        self.sandbox = new_sandbox(fixture_root, "h-%s-%s" % (profile["tag"], label))
        self.peer = ScriptedPeer(script=script, tls=tls)
        self.proc = None
        self.client = None
        self.why = None
        try:
            if tls:
                backend.setdefault("ca_file", sandbox_ca_file(self.sandbox))
            self.cfgpath = write_config(self.sandbox, h_config(profile["entry"](self.peer.url(), **backend)))
            self.proc = RouterProc(self.sandbox, self.cfgpath, label=label, extra_argv=("--debug",))
            if self.proc.wait_ready() is None:
                self.why = gb_not_ready(self.proc)
            else:
                self.client = self.proc.client()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.proc is not None:
            self.proc.close()
        self.peer.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def h_settings(mod, max_connections):
    """RouterSettings for an in-process router (there is no command line in-process)."""
    return mod.RouterSettings(bind="127.0.0.1", port=0, allow_remote=False, origins=frozenset(),
                              body_limit=getattr(mod, "_ROUTER_BODY_LIMIT", 32 << 20),
                              max_connections=max_connections, ready_file=None, debug=False,
                              log_file=None)


def h_serve(mod, cfg, max_connections):
    """(srv, thread): _RouterHttpServer on 127.0.0.1:0 serving on a daemon thread."""
    srv = mod._RouterHttpServer(("127.0.0.1", 0), h_settings(mod, max_connections), cfg)
    thread = threading.Thread(target=srv.serve_forever, name="tf-h-serve", daemon=True)
    thread.start()
    return srv, thread


def h_unserve(srv, thread):
    try:
        srv.shutdown()
    finally:
        srv.server_close()
        thread.join(PEER_JOIN_TIMEOUT_S)


def h_pumps():
    return sum(1 for t in threading.enumerate() if t.name == "rt-pump" and t.is_alive())


def h_wait(predicate, seconds):
    """True once *predicate()* holds, polled until *seconds* pass."""
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def h_first(name):
    """An on_event predicate: stop once an event named *name* arrived."""
    return lambda events: bool(events) and events[-1][0] == name


def h_closing_529(mod, mod_why, cfgpath):
    """(problems, detail): H4's in-process half -- srv.closing set, one authenticated
    POST with a Content-Length and no body -> 529 overloaded_error + Connection: close
    within H_529_S (the body was never waited for)."""
    if mod is None:
        return [mod_why], []
    problems = missing_names(mod, H_INPROC_NAMES)
    if problems:
        return problems, []
    srv, thread = h_serve(mod, mod.load_config(cfgpath), 4)
    try:
        srv.closing.set()
        client = RouterClient(srv.server_port, TF_ROUTER_TOKEN)
        request = gb_request(client, pairs=gb_auth_pairs(client), body=b"", length=H_529_DECLARED)
        t0 = time.monotonic()
        status, headers, body, _first, closed = gb_raw(client, request, timeout=H_529_S + 2.0)
        took = time.monotonic() - t0
    finally:
        h_unserve(srv, thread)
    label = "closing router"
    if status != 529:
        problems += answer_status(label, status, body, 529)
    else:
        etype, _msg, why = gb_envelope(body)
        if why:
            problems.append("%s: %s" % (label, why))
        elif etype != "overloaded_error":
            problems.append("%s: error.type %r, expected overloaded_error" % (label, etype))
        if (headers.get("connection") or "").lower() != "close":
            problems.append("%s: no Connection: close" % label)
    if not closed or took > H_529_S:
        problems.append("%s: answered and closed after %.2f s (closed=%s), expected within %.1f s"
                        % (label, took, closed, H_529_S))
    return problems, ["529 half    : in-process, status %r in %.2f s" % (status, took)]


def h_cases(kind, fixture_root, mod=None, mod_why=None):
    """[(cid, fn)] of H1-H6 for *kind*; each fn() -> (problems, detail) and owns its rig."""
    profile = H_PROFILES[kind]
    head, delta, finish = profile["head"], profile["delta"], profile["finish"]
    full = head + delta + finish

    def gaps_after(lines, seconds):
        """gaps for respond_sse: *seconds* of silence right after *lines*."""
        return [0.0] * len(lines) + [seconds]

    def h1():
        script = [("respond_sse", full, gaps_after(head, H_SILENCE_S))]
        with HRig(fixture_root, kind, "h1", script, idle_timeout=H_LONG_IDLE_S) as rig:
            if rig.client is None:
                return [rig.why], []
            conn, resp, problems = open_stream(rig.client, h_body())
            if problems:
                return problems, []
            t0 = time.monotonic()
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            took = time.monotonic() - t0
        events = out["events"]
        names = ev_names(events)
        if not names or names[0] != "message_start":
            problems.append("the first client event is %r, expected message_start (L13)"
                            % (names[0] if names else None))
        if "ping" not in names:
            problems.append("no event: ping during a %.0f s upstream silence" % H_SILENCE_S)
        if not names or names[-1] != "message_stop" or "error" in names:
            problems.append("the stream did not complete with message_stop (%s)" % ev_shape(events))
        problems += ev_unparsed(events)
        return problems, ["upstream    : first event, %.0f s silence, the rest; idle_timeout %d"
                          % (H_SILENCE_S, H_LONG_IDLE_S),
                          "client      : %s in %.1f s" % (ev_shape(events), took)]

    def h2():
        script = [("respond_sse", full, gaps_after(head, H_STALL_S))]
        with HRig(fixture_root, kind, "h2", script) as rig:
            if rig.client is None:
                return [rig.why], []
            conn, resp, problems = open_stream(rig.client, h_body())
            if problems:
                return problems, []
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
        events = out["events"]
        problems += ev_ends_in_error("silent upstream", out, needle="stalled")
        late = None
        if events and events[-1][0] == "error":
            late = events[-1][2] - events[0][2]
            if late > H_IDLE_S + H_ERROR_SLACK_S:
                problems.append("the error came %.1f s after the first event, expected within %d + %.0f s"
                                % (late, H_IDLE_S, H_ERROR_SLACK_S))
        if not out["eof"]:
            problems.append("the stream was not closed after the error (%s)" % (out["exc"] or "no EOF"))
        return problems, ["upstream    : first event, then %.0f s silence; idle_timeout %d"
                          % (H_STALL_S, H_IDLE_S),
                          "client      : %s; error after %s" % (
                              ev_shape(events), "%.1f s" % late if late is not None else "-")]

    def h3():
        script = [("respond_sse", full, gaps_after(head + delta, H_HOLD_S))]
        with HRig(fixture_root, kind, "h3", script, idle_timeout=H_LONG_IDLE_S) as rig:
            if rig.client is None:
                return [rig.why], []
            conn, resp, problems = open_stream(rig.client, h_body())
            if problems:
                return problems, []
            try:
                out = read_stream(resp, on_event=h_first("content_block_delta"))
            finally:
                early = rig.peer.closed_event.is_set()
                close_stream(conn, resp)
            t0 = time.monotonic()
            seen = rig.peer.closed_event.wait(H_CLOSE_S + 3.0)
            took = time.monotonic() - t0
        names = ev_names(out["events"])
        if "content_block_delta" not in names:
            problems.append("no content_block_delta arrived before the hang-up (%s)" % ev_shape(out["events"]))
        if early:
            problems.append("the upstream was closed before the client hung up")
        if not seen:
            problems.append("the peer never saw its socket closed (waited %.1f s)" % took)
        elif took > H_CLOSE_S:
            problems.append("the peer saw its socket closed %.2f s after the hang-up, expected within %.1f s"
                            % (took, H_CLOSE_S))
        return problems, ["client      : %s, then hung up" % ev_shape(out["events"]),
                          "peer        : %s" % ("closed after %.2f s" % took if seen else "never closed")]

    def h4():
        script = [("respond_sse", full, gaps_after(head + delta, H_HOLD_S))]
        detail = []
        with HRig(fixture_root, kind, "h4", script, idle_timeout=H_LONG_IDLE_S) as rig:
            if rig.client is None:
                problems = [rig.why]
            else:
                problems = []
                sig = {}

                def on_event(events):
                    if events[-1][0] == "content_block_delta" and "t" not in sig:
                        sig["t"] = time.monotonic()
                        rig.proc.proc.send_signal(signal.SIGTERM)
                    return False

                conn, resp, problems = open_stream(rig.client, h_body())
                if not problems:
                    try:
                        out = read_stream(resp, on_event=on_event)
                    finally:
                        close_stream(conn, resp)
                    if "t" not in sig:
                        problems.append("no content_block_delta arrived, so no SIGTERM was sent (%s)"
                                        % ev_shape(out["events"]))
                    else:
                        try:
                            code = rig.proc.proc.wait(max(0.0, sig["t"] + H_EXIT_S - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            code = None
                        took = time.monotonic() - sig["t"]
                        problems += ev_ends_in_error("SIGTERM mid-stream", out, err_type="overloaded_error")
                        if code is None:
                            problems.append("the router had not exited %.0f s after SIGTERM" % H_EXIT_S)
                        elif code != 0:
                            problems.append("the router exited %r after SIGTERM, expected 0" % code)
                        detail += ["client      : %s" % ev_shape(out["events"]),
                                   "router      : exit %r, %.2f s after SIGTERM" % (code, took)]
            if profile.get("inproc_529"):
                more, extra = h_closing_529(mod, mod_why, rig.cfgpath)
                problems += more
                detail += extra
        return problems, detail

    def h5():
        script = [("respond_json", 500, profile["error_500"])]
        with HRig(fixture_root, kind, "h5", script) as rig:
            if rig.client is None:
                return [rig.why], []
            conn, resp = rig.client.post(obj=h_body(), stream=True)
            try:
                status = resp.status
                ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
                body = resp.read()
            finally:
                close_stream(conn, resp)
        RESPONSE_BODIES.append(("H5 answer", body))
        problems = []
        if ctype == "text/event-stream" or status == 200:
            problems.append("an upstream 500 became a %r %s answer, never a 200 SSE (KD-15)" % (status, ctype))
        problems += gd_envelope_checks("upstream 500 on stream: true", status,
                                       {"content-type": ctype}, body, 502, "api_error")
        return problems, ["answer      : status %r, %s" % (status, ctype or "(no Content-Type)")]

    def h6():
        script = [("respond_sse", head + delta + profile["partial"])]
        with HRig(fixture_root, kind, "h6", script) as rig:
            if rig.client is None:
                return [rig.why], []
            conn, resp, problems = open_stream(rig.client, h_body())
            if problems:
                return problems, []
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
        problems += ev_ends_in_error("cut mid-event", out)
        if any(H_PARTIAL_MARKER in line for line in out["lines"]):
            problems.append("bytes of the cut event reached the client")
        names = ev_names(out["events"])
        if names[:1] != ["message_start"]:
            problems.append("the first client event is %r, expected message_start" % (names[:1] or [None])[0])
        return problems, ["upstream    : first event, first delta, then half an event and EOF",
                          "client      : %s" % ev_shape(out["events"])]

    return list(zip(h_case_ids(kind), (h1, h2, h3, h4, h5, h6)))


def h7(fixture_root, mod, mod_why):
    """H7: the pump and thread baseline over verified TLS (S1, KD-4's SSLSocket shutdown).

    In-process _RouterHttpServer (max_connections 2) in front of a TLS passthrough
    peer that sends its first event and then holds the stream open and silent.
    Five sequential streams hang up after the first event: within H_CLOSE_S of each
    the peer sees its socket closed and no rt-pump thread is alive; after the fifth
    threading.active_count() is back to its baseline; a sixth stream still opens
    (no slot leaked to a pump)."""
    if mod is None:
        return [mod_why], []
    problems = missing_names(mod, H_INPROC_NAMES)
    if problems:
        return problems, []
    profile = H_PROFILES["passthrough"]
    lines = profile["head"] + profile["delta"] + profile["finish"]
    peer = ScriptedPeer(script=[("respond_sse", lines, [0.0] * len(profile["head"]) + [H_HOLD_S])],
                        tls=True)
    srv = thread = None
    closes, rounds = [], 0
    baseline = after = None
    sixth = "not run"
    try:
        sandbox = new_sandbox(fixture_root, "h7")
        entry = profile["entry"](peer.url(), ca_file=sandbox_ca_file(sandbox), idle_timeout=H_LONG_IDLE_S)
        cfg = mod.load_config(write_config(sandbox, h_config(entry)))
        srv, thread = h_serve(mod, cfg, H7_MAX_CONNECTIONS)
        client = RouterClient(srv.server_port, TF_ROUTER_TOKEN)
        baseline = threading.active_count()

        def one(n):
            """Problems of one stream: open, first event, hang up, peer closed, no pump."""
            peer.closed_event.clear()
            conn, resp, why = open_stream(client, h_body())
            if why:
                return ["stream %d: %s" % (n, why[0])]
            try:
                out = read_stream(resp, on_event=lambda events: True)
            finally:
                close_stream(conn, resp)
            t0 = time.monotonic()
            seen = peer.closed_event.wait(H_CLOSE_S + 3.0)
            took = time.monotonic() - t0
            closes.append(took if seen else None)
            found = []
            if ev_names(out["events"])[:1] != ["message_start"]:
                found.append("stream %d: the first event is not message_start (%s)"
                             % (n, ev_shape(out["events"])))
            if not seen:
                found.append("stream %d: the peer never saw its socket closed" % n)
            elif took > H_CLOSE_S:
                found.append("stream %d: the peer saw its socket closed after %.2f s, expected within %.1f s"
                             % (n, took, H_CLOSE_S))
            if not h_wait(lambda: h_pumps() == 0, H_SETTLE_S):
                found.append("stream %d: %d rt-pump thread(s) still alive %.0f s after the hang-up"
                             % (n, h_pumps(), H_SETTLE_S))
            return found

        for n in range(1, H7_ROUNDS + 1):
            found = one(n)
            rounds = n
            problems += found
            if found and found[0].startswith("stream %d: status" % n):
                break
        if not problems:
            if not h_wait(lambda: threading.active_count() <= baseline, H_SETTLE_S):
                problems.append("threading.active_count() is %d after %d streams, baseline %d"
                                % (threading.active_count(), H7_ROUNDS, baseline))
            after = threading.active_count()
            found = one(H7_ROUNDS + 1)
            sixth = "ok" if not found else found[0]
            problems += found
    finally:
        if srv is not None:
            h_unserve(srv, thread)
        peer.close()
    shown = ", ".join("%.2f" % c if c is not None else "never" for c in closes) or "-"
    return problems, ["router      : in-process, max_connections %d, verified TLS peer" % H7_MAX_CONNECTIONS,
                      "streams     : %d run; peer closed after %s s" % (rounds, shown),
                      "threads     : baseline %s, after the fifth %s; sixth stream: %s"
                      % (baseline, after, sixth)]


def h_ms_fragments(tid, count, finish):
    """(lines, gaps, args): a mistral stream -- the role chunk, a tool call's first
    fragment (id, name), then *count* argument fragments H_FRAGMENT_GAP_S apart, and
    with *finish* the closing fragment, finish_reason tool_calls and [DONE].  *args* is
    the whole argument string the client must receive (with *finish*)."""
    frags = ['{"file_path": "/tf/h']
    lines = ms_lines(MS_ROLE, ms_tools(ms_tool(0, frags[0], tid, "Read")), done=False)
    gaps = [0.0] * len(lines)
    for i in range(count):
        frags.append("/p%02d" % i)
        more = ms_lines(ms_tools(ms_tool(0, frags[-1])), done=False)
        lines += more
        gaps += [H_FRAGMENT_GAP_S] + [0.0] * (len(more) - 1)
    if finish:
        frags.append('"}')
        more = ms_lines(ms_tools(ms_tool(0, frags[-1])), ms_stop("tool_calls", ms_usage(20, 30)))
        lines += more
        gaps += [0.0] * len(more)
    return lines, gaps, "".join(frags)


def h8(fixture_root):
    """H8 (mistral, M2): tool-argument fragments every 1 s for ~20 s, then
    finish_reason tool_calls.  The tool call is buffered (KD-5), so nothing is
    written after message_start: the relay's last-WRITE ping timer must still fire
    (A-6), then the whole tool block and message_stop arrive."""
    lines, gaps, args = h_ms_fragments("tfToolH08", H8_FRAGMENTS, True)
    with HRig(fixture_root, "mistral", "h8", [("respond_sse", lines, gaps)], idle_timeout=H_LONG_IDLE_S) as rig:
        if rig.client is None:
            return [rig.why], []
        conn, resp, problems = open_stream(rig.client, h_body())
        if problems:
            return problems, []
        t0 = time.monotonic()
        try:
            out = read_stream(resp)
        finally:
            close_stream(conn, resp)
        took = time.monotonic() - t0
    events = out["events"]
    names = ev_names(events)
    problems += ev_unparsed(events)
    if names[:1] != ["message_start"]:
        problems.append("the first client event is %r, expected message_start" % ((names[:1] or [None])[0],))
    at = names.index("content_block_start") if "content_block_start" in names else len(names)
    pings = [i for i in range(1, at) if names[i] == "ping"]
    others = sorted({names[i] for i in range(1, at)} - {"ping"})
    if not pings:
        problems.append("no event: ping while the tool call was buffered (%d fragments, %.0f s apart, "
                        "nothing else written)" % (H8_FRAGMENTS, H_FRAGMENT_GAP_S))
    if others:
        problems.append("%s was written while the tool call was still streaming in (KD-5 buffers it)" % others)
    tail = names[at:]
    want = ["content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
    if tail != want:
        problems.append("after the pings: %s, expected %s" % (", ".join(tail) or "(nothing)", ", ".join(want)))
    else:
        try:
            start = json.loads(events[at][1]).get("content_block") or {}
            delta = (json.loads(events[at + 1][1]).get("delta") or {}).get("partial_json")
            if start.get("type") != "tool_use" or start.get("id") != GF_MINTED + "tfToolH08" \
                    or start.get("name") != "Read":
                problems.append("the tool block start is %r, expected tool_use %s Read"
                                % (start, GF_MINTED + "tfToolH08"))
            if json.loads(delta or "") != json.loads(args):
                problems.append("the tool input %r, expected %r" % ((delta or "")[:120], args[:120]))
        except (ValueError, AttributeError) as exc:
            problems.append("the tool block does not parse: %s" % type(exc).__name__)
    ping_at = ["%.1f" % (events[i][2] - events[0][2]) for i in pings]
    return problems, ["upstream    : %d tool-argument fragments %.0f s apart, then tool_calls"
                      % (H8_FRAGMENTS, H_FRAGMENT_GAP_S),
                      "client      : %s in %.1f s; ping(s) at %s s after message_start"
                      % (ev_shape(events), took, ", ".join(ping_at) or "-")]


def h9(fixture_root):
    """H9 (mistral, M2): tool-argument fragments every 1 s indefinitely; the client
    hangs up right after message_start.  Nothing is ever written to the client while
    the call is buffered, so only the relay's client-EOF poll (KD-4) can notice: the
    peer must see its upstream socket closed within H9_CLOSE_S."""
    lines, gaps, _args = h_ms_fragments("tfToolH09", H9_FRAGMENTS, False)
    with HRig(fixture_root, "mistral", "h9", [("respond_sse", lines, gaps)], idle_timeout=H_LONG_IDLE_S) as rig:
        if rig.client is None:
            return [rig.why], []
        conn, resp, problems = open_stream(rig.client, h_body())
        if problems:
            return problems, []
        try:
            out = read_stream(resp, on_event=h_first("message_start"))
        finally:
            early = rig.peer.closed_event.is_set()
            close_stream(conn, resp)
        t0 = time.monotonic()
        seen = rig.peer.closed_event.wait(H9_CLOSE_S + 3.0)
        took = time.monotonic() - t0
    names = ev_names(out["events"])
    if names[:1] != ["message_start"]:
        problems.append("the first client event is %r, expected message_start" % ((names[:1] or [None])[0],))
    if early:
        problems.append("the upstream was closed before the client hung up")
    if not seen:
        problems.append("the peer never saw its socket closed (waited %.1f s): the client EOF went unnoticed "
                        "while the tool call was buffered" % took)
    elif took > H9_CLOSE_S:
        problems.append("the peer saw its socket closed %.2f s after the hang-up, expected within %.1f s"
                        % (took, H9_CLOSE_S))
    return problems, ["upstream    : tool-argument fragments %.0f s apart, never finished" % H_FRAGMENT_GAP_S,
                      "client      : %s, then hung up" % ev_shape(out["events"]),
                      "peer        : %s" % ("closed after %.2f s" % took if seen else "never closed")]


def group_h(suite, fixture_root):
    """H. timeouts and disconnect: H1-H6 per kind in H_KINDS (each its own peer and
    router), H8 and H9 on mistral (tool deltas buffered, nothing written), then H7
    once on passthrough over verified TLS, in-process.

    Before a kind's adapter is wired every stream request on it is the 501
    "backend kind <kind> is not wired yet": each case FAILS naming it."""
    try:
        mod = H.load_module_from_path("ph_llm_router_h", SERVER)
        mod_why = None
    except Exception as exc:  # noqa: BLE001 -- only the in-process halves need it
        mod = None
        mod_why = "cannot import %s: %s" % (os.path.relpath(SERVER, H.REPO_ROOT), type(exc).__name__)
    cases = []
    for kind in H_KINDS:
        cases += h_cases(kind, fixture_root, mod, mod_why)
    if "mistral" in H_KINDS:
        cases += [(GH_H8, lambda: h8(fixture_root)), (GH_H9, lambda: h9(fixture_root))]
    cases.append((GH_H7, lambda: h7(fixture_root, mod, mod_why)))
    for cid, fn in cases:
        try:
            problems, detail = fn()
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])], []
        suite.record(GH, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group I: secret leak
# ---------------------------------------------------------------------------

GI_CASES = (
    "no secret in stderr or the log",    # I1
    "no sentinel in a response body",    # I2
    "mistral peer: its key only",        # I3
    "passthrough peer: no foreign key",  # I4
    "client x-api-key reached no peer",  # I5
    "repr(cfg) shows no secret",         # I6
    "upstream echo -> [redacted]",       # I7
)

GI_ROUNDS = 3                    # I4: alternating mistral/passthrough request pairs
# I1 (V21): unrouted paths carrying the router token; each is a 404 whose access line must not log it.
GI_TOKEN_PATHS = (MESSAGES_PATH + "/" + TF_ROUTER_TOKEN, "/" + TF_ROUTER_TOKEN)
GI_REDACTED = "[redacted]"
GI_MISTRAL_ROUTE = "claude-sonnet-4-5"
GI_PASS_ROUTE = "claude-haiku-4-5"
GI_LLAMA_ROUTE = "claude-opus-4-5"

# I7 (KD-9): the four forms of TF_KEY_MISTRAL an upstream error message echoes, each
# behind its own marker in the request so the mistral peer knows which to echo.
# The same case drives the other two scrub routes once each (M2): a llamacpp
# peer's mid-stream event: error carrying TF_KEY_PASS (_rt_ll_error_shape, then
# EventRelay's error path) and a passthrough peer's Anthropic error envelope
# carrying TF_ROUTER_TOKEN (PassthroughAdapter.json_response).  Any secret of
# the config is scrubbed on any route, so the echo need not be the routed
# backend's own key.
GI_I7_PASS_MARKER = "tf-i7-pass-envelope"
GI_I7_PASS_ECHO = "tf upstream: rejected token %s" % TF_ROUTER_TOKEN
GI_I7_LLAMA_ECHO = "tf llama: bad key %s" % TF_KEY_PASS
GI_I7_LLAMA_LINES = (
    "event: message_start",
    "data: " + json.dumps({"type": "message_start", "message": {
        "id": "msg_tfi7", "type": "message", "role": "assistant", "content": [],
        "model": "tf-llama-model", "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0}}}),
    "",
    "event: content_block_start",
    "data: " + json.dumps({"type": "content_block_start", "index": 0,
                           "content_block": {"type": "text", "text": ""}}),
    "",
    "event: content_block_delta",
    "data: " + json.dumps({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "text_delta", "text": "tf partial"}}),
    "",
    "event: error",
    "data: " + json.dumps({"code": 400, "message": GI_I7_LLAMA_ECHO,
                           "type": "invalid_request_error"}),
    "",
)
GI_I7_FORMS = (
    ("tf-i7-value", "the value", TF_KEY_MISTRAL),
    ("tf-i7-url", "URL-encoded", urllib.parse.quote(TF_KEY_MISTRAL, safe="")),
    ("tf-i7-b64", "base64", base64.b64encode(TF_KEY_MISTRAL.encode("ascii")).decode("ascii")),
    ("tf-i7-first8", "first 8 chars", TF_KEY_MISTRAL[:8]),
)

GI_MISTRAL_ANSWER = {"id": "tf-cmpl-i", "object": "chat.completion", "created": 1,
                     "model": "tf-mistral-model",
                     "choices": [{"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": "ok"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def gi_secret_forms():
    """Every KD-9 form of every sentinel, longest first (for redacting FAIL lines)."""
    forms = set()
    for value in SENTINELS:
        raw = value.encode("ascii")
        forms.add(value)
        forms.add(urllib.parse.quote(value, safe=""))
        for enc in (base64.b64encode(raw), base64.urlsafe_b64encode(raw)):
            text = enc.decode("ascii")
            forms.add(text)
            forms.add(text.rstrip("="))
        if len(value) >= 16:
            forms.add(value[:8])
            forms.add(value[-8:])
    return sorted(forms, key=len, reverse=True)


GI_SECRET_FORMS = gi_secret_forms()


def gi_redact(text):
    """*text* with every KD-9 form of every sentinel replaced, so a FAIL line never prints one."""
    for form in GI_SECRET_FORMS:
        text = text.replace(form, "<sentinel>")
    return text


def gi_config(mistral_url, pass_url, llama_url):
    """Three backends (mistral, passthrough, keyless llamacpp), three routes, the default on passthrough."""
    return {
        "auth_token": TF_ROUTER_TOKEN,
        "backends": {
            "tfmistral": {"kind": "mistral", "base_url": mistral_url, "api_key": TF_KEY_MISTRAL},
            "tfpass": {"kind": "passthrough", "base_url": pass_url,
                       "api_key": TF_KEY_PASS, "auth_header": "x-api-key"},
            "tfllama": {"kind": "llamacpp", "base_url": llama_url},
        },
        "routes": {
            GI_MISTRAL_ROUTE: {"backend": "tfmistral", "model": "tf-mistral-model"},
            GI_PASS_ROUTE: {"backend": "tfpass", "model": "tf-pass-model"},
            GI_LLAMA_ROUTE: {"backend": "tfllama", "model": "tf-llama-model"},
        },
        "default": {"backend": "tfpass", "model": "tf-pass-model"},
    }


def gi_body(route, text):
    """A minimal non-stream Messages request for *route* whose user text is *text*."""
    return {"model": route, "max_tokens": 16,
            "messages": [{"role": "user", "content": text}]}


def gi_mistral_script(_index, request):
    """An I7 marker in the request body -> a Mistral 400 echoing that form; else a 200."""
    body = request.get("body") or b""
    for marker, label, form in GI_I7_FORMS:
        if marker.encode("ascii") in body:
            return [("respond_json", 400, {
                "object": "error", "type": "invalid_request_error", "param": None,
                "code": None,
                "message": "tf upstream: invalid api key %s (%s)" % (form, label)})]
    return [("respond_json", 200, GI_MISTRAL_ANSWER)]


def gi_pass_script(_index, request):
    """The I7 passthrough marker -> a 400 Anthropic error envelope echoing TF_ROUTER_TOKEN; else a 200."""
    if GI_I7_PASS_MARKER.encode("ascii") in (request.get("body") or b""):
        return [("respond_json", 400, {"type": "error", "error": {
            "type": "invalid_request_error", "message": GI_I7_PASS_ECHO}})]
    return [("respond_json", 200, GB_PEER_ANSWER)]


def gi_request_parts(request):
    """[(where, text)] of one recorded peer request: the line, each header, the body."""
    parts = [("request line", request.get("line") or "")]
    for name, value in request.get("headers") or ():
        parts.append(("header %s" % name, "%s: %s" % (name, value)))
    parts.append(("body", (request.get("body") or b"").decode("latin-1")))
    return parts


def gi_peer_leaks(peer_name, requests, forbidden):
    """Problems: one per (request, place) carrying a *forbidden* (label, value) pair."""
    problems = []
    for i, request in enumerate(requests):
        for where, text in gi_request_parts(request):
            for label, value in forbidden:
                if value in text:
                    problems.append("%s request %d: %s carries %s" % (peer_name, i, where, label))
    return problems


GI_SENTINEL_LABELS = (("TF_ROUTER_TOKEN", TF_ROUTER_TOKEN), ("TF_KEY_MISTRAL", TF_KEY_MISTRAL),
                      ("TF_KEY_PASS", TF_KEY_PASS), ("TF_CLIENT_XKEY", TF_CLIENT_XKEY))


def group_i(suite, fixture_root):
    """I. secret leak: no sentinel in stderr, the --log-file, a response body, a
    request recorded by the wrong peer, or the repr of the loaded config; an
    upstream error echoing a key reaches the client as [redacted] (KD-9).

    One live router (--debug, --log-file) in front of a mistral peer and a
    passthrough peer, two routes, alternating requests that each carry the
    client's x-api-key TF_CLIENT_XKEY; a keyless llamacpp peer on a third
    route serves only I7's mid-stream error.  Every sweep refuses a vacuous pass:
    nothing to sweep is a FAIL (as A12, B17).  I1 runs last, after the router
    has exited, and sweeps every stderr and log file the whole run collected.
    """
    sandbox = new_sandbox(fixture_root, "secret")
    mpeer = ScriptedPeer(script=gi_mistral_script)
    ppeer = ScriptedPeer(script=gi_pass_script)
    lpeer = ScriptedPeer(script=[("respond_sse", list(GI_I7_LLAMA_LINES))])
    log_path = os.path.abspath(os.path.join(sandbox, "secret.log"))
    proc = None
    results = {}
    traffic = []                 # (route, status) of the I3-I5 rounds
    routed_ok = []               # (label, body) of every 2xx answer to a routed request
    path_probes = []             # I1: the status of each GI_TOKEN_PATHS probe
    try:
        cfgpath = write_config(sandbox, gi_config(mpeer.url(), ppeer.url(), lpeer.url()))
        proc = RouterProc(sandbox, cfgpath, label="secret",
                          extra_argv=("--debug", "--log-file", log_path))
        port = proc.wait_ready()
        client = proc.client() if port is not None else None
        why = gb_not_ready(proc) if port is None else None

        if client is not None:
            for n in range(GI_ROUNDS):
                for route in (GI_MISTRAL_ROUTE, GI_PASS_ROUTE):
                    try:
                        status, _h, body = client.post(
                            obj=gi_body(route, "tf round %d" % n),
                            headers={"x-api-key": TF_CLIENT_XKEY})
                    except (OSError, http.client.HTTPException) as exc:
                        traffic.append((route, type(exc).__name__))
                        continue
                    traffic.append((route, status))
                    if 200 <= status < 300:
                        routed_ok.append(("%s round %d" % (route, n), body))
            # V21: the token in an unrouted path is a 404 before auth; I1 then
            # proves the --debug access line did not write that path to the log.
            for path in GI_TOKEN_PATHS:
                try:
                    status, _h, _body = client.post(path=path, obj=gi_body(GI_PASS_ROUTE, "tf i1"))
                except (OSError, http.client.HTTPException) as exc:
                    status = type(exc).__name__
                path_probes.append(status)
            # ... and the token as the method: the stdlib's 501, same access line.
            try:
                status = gb_raw(client, gb_request(client, method=TF_ROUTER_TOKEN,
                                                   pairs=[("Connection", "close")], length=None))[0]
            except OSError as exc:
                status = type(exc).__name__
            path_probes.append(status)

        def live(fn):
            def case():
                if client is None:
                    return [why], []
                return fn()
            return case

        def traffic_detail():
            return ["traffic     : %s" % ", ".join("%s=%s" % (r.split("-")[1], s)
                                                    for r, s in traffic)]

        def i3():
            problems = []
            reqs = list(mpeer.requests)
            if not reqs:
                return ["the mistral peer recorded no request: nothing to check "
                        "(a vacuous pass is refused)"], traffic_detail()
            want = "Bearer " + TF_KEY_MISTRAL
            for i, request in enumerate(reqs):
                auth = [v for k, v in request["headers"] if k.lower() == "authorization"]
                if auth != [want]:
                    problems.append("mistral request %d: %d Authorization header(s), "
                                    "expected exactly `Bearer TF_KEY_MISTRAL`%s"
                                    % (i, len(auth), "" if len(auth) != 1 else " (value differs)"))
                for where, text in gi_request_parts(request):
                    if where.startswith("header ") and where[7:].lower() == "authorization":
                        text = text.replace(want, "", 1)
                    for label, value in GI_SENTINEL_LABELS:
                        if value in text:
                            problems.append("mistral request %d: %s carries %s" % (i, where, label))
            return problems, traffic_detail() + ["mistral     : %d request(s) checked" % len(reqs)]

        def i4():
            reqs = list(ppeer.requests)
            if not reqs:
                return ["the passthrough peer recorded no request: nothing to check "
                        "(a vacuous pass is refused)"], traffic_detail()
            forbidden = [kv for kv in GI_SENTINEL_LABELS if kv[0] != "TF_KEY_PASS"]
            problems = gi_peer_leaks("passthrough", reqs, forbidden)
            return problems, traffic_detail() + [
                "passthrough : %d request(s), swept for TF_KEY_MISTRAL, TF_ROUTER_TOKEN, "
                "TF_CLIENT_XKEY" % len(reqs)]

        def i5():
            sent = sum(1 for _r, _s in traffic)
            got = [("mistral", r) for r in mpeer.requests] + [("passthrough", r) for r in ppeer.requests]
            problems = []
            if not got:
                problems.append("no peer recorded a request: nothing to sweep "
                                "(a vacuous pass is refused)")
            for name, request in got:
                problems += gi_peer_leaks(name, [request], [("TF_CLIENT_XKEY", TF_CLIENT_XKEY)])
            return problems, ["client      : %d request(s) sent with x-api-key TF_CLIENT_XKEY" % sent,
                              "peers       : %d request(s) swept (both peers)" % len(got)]

        def i6():
            try:
                mod = H.load_module_from_path("ph_llm_router_i", SERVER)
            except Exception as exc:  # noqa: BLE001
                return ["cannot import %s: %s" % (os.path.relpath(SERVER, H.REPO_ROOT),
                                                  type(exc).__name__)], []
            try:
                cfg = mod.load_config(cfgpath)
            except Exception as exc:  # noqa: BLE001
                return ["load_config refused the group's config: %s: %s"
                        % (type(exc).__name__, gi_redact(str(exc))[:200])], []
            problems = []
            backends = getattr(cfg, "backends", None) or {}
            for name, key in (("tfmistral", TF_KEY_MISTRAL), ("tfpass", TF_KEY_PASS)):
                if getattr(backends.get(name), "api_key", None) != key:
                    problems.append("backends.%s does not hold its api_key: its repr proves "
                                    "nothing (a vacuous pass is refused)" % name)
            if getattr(cfg, "token", None) != TF_ROUTER_TOKEN.encode("ascii"):
                problems.append("cfg.token is not the configured auth_token: its repr proves "
                                "nothing (a vacuous pass is refused)")
            shown = [("repr(cfg)", repr(cfg)), ("str(cfg)", str(cfg))]
            for name in sorted(backends):
                shown.append(("repr(cfg.backends[%r])" % name, repr(backends[name])))
            for name, route in sorted((getattr(cfg, "routes", None) or {}).items()):
                shown.append(("repr(cfg.routes[%r])" % name, repr(route)))
            shown.append(("repr(cfg.default)", repr(getattr(cfg, "default", None))))
            for where, text in shown:
                for label, value in GI_SENTINEL_LABELS:
                    if value in text:
                        problems.append("%s shows %s" % (where, label))
            return problems, ["swept       : %d repr/str text(s): cfg, %d backend(s), routes, default"
                              % (len(shown), len(backends))]

        def i7():
            problems = []
            seen = []
            for marker, label, form in GI_I7_FORMS:
                before = len(mpeer.requests)
                status, _h, body = client.post(
                    obj=gi_body(GI_MISTRAL_ROUTE, "%s please" % marker),
                    headers={"x-api-key": TF_CLIENT_XKEY})
                reached = any(marker.encode("ascii") in (r.get("body") or b"")
                              for r in mpeer.requests[before:])
                if not reached:
                    problems.append("%s: the mistral peer never received the request, so no "
                                    "echo was sent (a vacuous pass is refused); router answered %r"
                                    % (label, status))
                    continue
                etype, msg, bad = gb_envelope(body)
                seen.append("%s=%s" % (marker[6:], status))
                if bad:
                    problems.append("%s: %s" % (label, gi_redact(bad)))
                    continue
                if not isinstance(status, int) or status < 400:
                    problems.append("%s: status %r for an upstream 400" % (label, status))
                if GI_REDACTED not in msg:
                    problems.append("%s: error.message %r holds no %s"
                                    % (label, gi_redact(msg)[:160], GI_REDACTED))
                text = gb_text(body)
                if form in text:
                    problems.append("%s: the echoed form reaches the client unscrubbed" % label)
                for slabel, value in GI_SENTINEL_LABELS:
                    if value in text:
                        problems.append("%s: the answer body carries %s" % (label, slabel))
            if GI_I7_FORMS[1][2] == TF_KEY_MISTRAL:
                problems.append("URL-encoded: quote(safe='') of TF_KEY_MISTRAL equals the value, "
                                "so that form adds no coverage (the sentinel lost its reserved character)")
            llama_problems, llama_seen = i7_llama()
            pass_problems, pass_seen = i7_pass()
            problems += llama_problems + pass_problems
            return problems, ["mistral     : %s, upstream 400 echoing TF_KEY_MISTRAL; answers %s"
                              % (GI_MISTRAL_ROUTE, ", ".join(seen) or "none"),
                              "forms       : %s (the URL-encoded form differs from the value)"
                              % ", ".join(lb for _m, lb, _f in GI_I7_FORMS),
                              "llamacpp    : %s, mid-stream event: error echoing TF_KEY_PASS -> %s"
                              % (GI_LLAMA_ROUTE, llama_seen),
                              "passthrough : %s, 400 Anthropic envelope echoing TF_ROUTER_TOKEN -> %s"
                              % (GI_PASS_ROUTE, pass_seen)]

        def i7_scrubbed(label, msg, text, sentinel, slabel):
            """Problems unless *msg* holds [redacted] and *text* holds no form of any sentinel."""
            problems = []
            if GI_REDACTED not in msg:
                problems.append("%s: error.message %r holds no %s"
                                % (label, gi_redact(msg)[:160], GI_REDACTED))
            if sentinel in text:
                problems.append("%s: the echoed %s reaches the client unscrubbed" % (label, slabel))
            for form in GI_SECRET_FORMS:
                if form in text:
                    problems.append("%s: the answer carries a KD-9 form of a sentinel" % label)
                    break
            return problems

        def i7_llama():
            """The llamacpp mid-stream route: (problems, shape of the client stream)."""
            before = len(lpeer.requests)
            conn, resp, more = open_stream(client, dict(gi_body(GI_LLAMA_ROUTE, "tf i7 llama"),
                                                        stream=True),
                                           headers={"x-api-key": TF_CLIENT_XKEY})
            if more:
                return ["llamacpp: " + p for p in more], "-"
            try:
                out = read_stream(resp)
            finally:
                close_stream(conn, resp)
            if len(lpeer.requests) == before:
                return ["llamacpp: the llamacpp peer never received the request, so no echo "
                        "was sent (a vacuous pass is refused)"], ev_shape(out["events"])
            problems = ev_ends_in_error("llamacpp mid-stream", out)
            names = ev_names(out["events"])
            if not names or names[0] != "message_start":
                problems.append("llamacpp: the stream did not start before the error (%s), so "
                                "this is not the mid-stream route" % ev_shape(out["events"]))
            errors = ev_errors(out["events"])
            msg = errors[-1][1] if errors else None
            text = "\n".join(out["lines"])
            if isinstance(msg, str):
                problems += i7_scrubbed("llamacpp", msg, text, TF_KEY_PASS, "TF_KEY_PASS")
            elif TF_KEY_PASS in text:
                problems.append("llamacpp: the echoed TF_KEY_PASS reaches the client unscrubbed")
            return problems, ev_shape(out["events"])

        def i7_pass():
            """The passthrough envelope route: (problems, the router's status)."""
            before = len(ppeer.requests)
            status, _h, body = client.post(
                obj=gi_body(GI_PASS_ROUTE, "%s please" % GI_I7_PASS_MARKER),
                headers={"x-api-key": TF_CLIENT_XKEY})
            if not any(GI_I7_PASS_MARKER.encode("ascii") in (r.get("body") or b"")
                       for r in ppeer.requests[before:]):
                return ["passthrough: the passthrough peer never received the request, so no "
                        "echo was sent (a vacuous pass is refused); router answered %r" % status], status
            etype, msg, bad = gb_envelope(body)
            if bad:
                return ["passthrough: %s" % gi_redact(bad)], status
            problems = []
            if status != 400:
                problems.append("passthrough: status %r for an upstream 400" % status)
            if etype != "invalid_request_error":
                problems.append("passthrough: error.type %r, expected the envelope's "
                                "invalid_request_error" % etype)
            problems += i7_scrubbed("passthrough", msg, gb_text(body), TF_ROUTER_TOKEN, "TF_ROUTER_TOKEN")
            return problems, status

        def i2():
            problems = []
            if not RESPONSE_BODIES:
                problems.append("no response body was recorded: nothing to sweep "
                                "(a vacuous pass is refused)")
            if not routed_ok:
                problems.append("no routed request got a 2xx answer: no backend body to sweep "
                                "(a vacuous pass is refused)")
            for label, body in RESPONSE_BODIES:
                text = gb_text(body)
                for slabel, value in GI_SENTINEL_LABELS:
                    if value in text:
                        problems.append("%s: the response body carries %s" % (gi_redact(label), slabel))
            return problems, ["swept       : %d response body(ies) from the whole run, %d routed 2xx"
                              % (len(RESPONSE_BODIES), len(routed_ok))]

        cases = [(GI_CASES[2], live(i3)), (GI_CASES[3], live(i4)), (GI_CASES[4], live(i5)),
                 (GI_CASES[5], i6), (GI_CASES[6], live(i7)),
                 (GI_CASES[1], i2)]     # after I7: it sweeps I7's error bodies too
        for cid, fn in cases:
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s"
                                 % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    except Exception as exc:  # noqa: BLE001 -- a setup failure fails every case not yet run
        setup = "the group setup raised %s: %s" % (type(exc).__name__, gi_redact(str(exc))[:200])
    else:
        setup = "case did not run"
    finally:
        if proc is not None:
            proc.close()
        mpeer.close()
        ppeer.close()
        lpeer.close()

    def i1():
        problems = []
        swept, nonempty = 0, 0
        for path in LOG_PATHS:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            swept += 1
            nonempty += 1 if text.strip() else 0
            for label, value in GI_SENTINEL_LABELS:
                if value in text:
                    problems.append("%s: carries %s" % (os.path.basename(path), label))
        if not swept:
            problems.append("no stderr or log file was found: nothing to sweep "
                            "(a vacuous pass is refused)")
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                own = fh.read()
        except OSError:
            own = None
        if own is None:
            problems.append("the --debug --log-file was never written: nothing to sweep "
                            "(a vacuous pass is refused)")
        elif not own.strip():
            problems.append("the --debug --log-file is empty: nothing to sweep "
                            "(a vacuous pass is refused)")
        if path_probes != [404] * len(GI_TOKEN_PATHS) + [501]:
            problems.append("the token probes answered %r, expected a 404 per path and a 501 for "
                            "the method: the log proves nothing about them (a vacuous pass is "
                            "refused)" % (path_probes,))
        return problems, ["swept       : %d of %d stderr/log path(s), %d non-empty"
                          % (swept, len(LOG_PATHS), nonempty),
                          "probes      : the router token in %d unrouted path(s) and as the "
                          "method -> %r (V21)" % (len(GI_TOKEN_PATHS), path_probes),
                          "log file    : %s" % ("missing" if own is None else
                                                "%d line(s)" % len(own.splitlines()))]

    try:
        results[GI_CASES[0]] = i1()
    except Exception as exc:  # noqa: BLE001
        results[GI_CASES[0]] = (["case raised %s: %s"
                                 % (type(exc).__name__, gi_redact(str(exc))[:200])], [])
    for cid in GI_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GI, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group J: static and hygiene
# ---------------------------------------------------------------------------

# Step 7's in-process half of J: the encoder invariants (J6-J8, J21) and the
# forward-table check (J12).  Step 12 adds J1-J4, J13 and J15-J19 (GJ_STATIC_CASES,
# below) and, in its second part, J5, J9-J11, J14, J20 and J22 (GJ_HYGIENE_CASES).
GJ_CASES = (
    "encoder closed after error()",      # J6
    "finish after tool forces tool_use", # J7
    "text then tool: indices 0 and 1",   # J8
    "forward tables vs _NEVER_FORWARD",  # J12
    "lone surrogates escaped, ASCII",    # J21
)

GJ_KINDS = ("llamacpp", "mistral", "passthrough")   # J12: KIND_CLASSES must hold all three
GJ_MESSAGE_ID = "msg_tfj0123456789abcdef01234"
GJ_MODEL = "claude-tf-j"
GJ_USAGE = {"output_tokens": 7}
GJ_TOOL = ("toolu_tfj0123456789abcdef0123", "Read", '{"file_path":"/tf/j"}')


def gj_missing(mod, names):
    """One problem naming every symbol of *names* the module does not define (red, never a crash)."""
    absent = [n for n in names if not hasattr(mod, n)]
    if not absent:
        return []
    return ["the module defines no %s (Step 7 not landed)" % ", ".join(absent)]


def gj_events(data):
    """[(event, payload)] of encoder output: ASCII bytes made only of
    `event: <name>\\ndata: <json>\\n\\n` frames whose payload type is <name>.
    Raises ValueError (with the reason) on anything else."""
    if not isinstance(data, bytes):
        raise ValueError("returned %s, not bytes" % type(data).__name__)
    data.decode("ascii")        # UnicodeDecodeError is a ValueError
    if not data:
        return []
    if not data.endswith(b"\n\n"):
        raise ValueError("output does not end with a blank line: %r" % data[-40:])
    out = []
    for frame in data[:-2].split(b"\n\n"):
        lines = frame.split(b"\n")
        if (len(lines) != 2 or not lines[0].startswith(b"event: ")
                or not lines[1].startswith(b"data: ")):
            raise ValueError("frame %r is not one event line and one data line" % frame[:80])
        name = lines[0][len(b"event: "):].decode("ascii")
        payload = json.loads(lines[1][len(b"data: "):].decode("ascii"))
        if not isinstance(payload, dict) or payload.get("type") != name:
            raise ValueError("event %r carries a payload of type %r"
                             % (name, payload.get("type") if isinstance(payload, dict) else None))
        out.append((name, payload))
    return out


def gj_shape(events):
    """A compact (name, index) list of *events* for detail and FAIL lines."""
    return ["%s%s" % (name, "" if "index" not in payload else "[%s]" % payload["index"])
            for name, payload in events]


# Step 12 (part 1): the static AST rules J1-J4 and the census rule J13.  Each
# rule is a function of the source TEXT (J13: of the census ROWS), never of
# SERVER itself, so its planted negative control (J15-J19) runs the SAME
# function, unchanged, over a doctored input.  A rule returns [finding]; an
# empty list is a pass.
GJ_STATIC_CASES = (
    "front hardening markers kept",      # J1
    "no env, proxy, urllib, asyncio",    # J2
    "no Access-Control- literal",        # J3
    "upstream headers, pure units",      # J4
    "hand copies all declared",          # J13
    "control: 2nd compare_digest",       # J15 (J1)
    "control: HTTPS_PROXY read",         # J16 (J2)
    "control: CORS literal",             # J17 (J3)
    "control: three J4 plants",          # J18 (J4)
    "control: undeclared hand copy",     # J19 (J13)
)

GJ_ENV_ATTRS = ("environ", "environb", "getenv", "getenvb")
GJ_PURE_FORBIDDEN = ("socket", "ssl", "http", "select", "time", "threading")   # NFR-8
GJ_PURE_UNITS = (2, 3, 4, 5, 6)          # J4c: the banners that must exist, once each, in order
GJ_BANNER_RE = re.compile(r"^# Unit (\d+):", re.M)


def gj_parse(source):
    """(tree, []) or (None, [finding]): a source that does not parse is itself a finding."""
    try:
        return ast.parse(source), []
    except (SyntaxError, ValueError) as exc:
        return None, ["the source does not parse: %s: %s" % (type(exc).__name__, exc)]


def gj_scopes(tree):
    """[(node, name of the innermost enclosing function or None)] for every node of *tree*."""
    out = []
    stack = [(tree, None)]
    while stack:
        node, scope = stack.pop()
        out.append((node, scope))
        inner = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
        stack.extend((child, inner) for child in ast.iter_child_nodes(node))
    return out


def gj_callee(call):
    """The bare name a Call invokes (`x.f(...)` and `f(...)` both give "f"), else None."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def gj_is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def gj_is_environ(node):
    return isinstance(node, ast.Attribute) and node.attr in ("environ", "environb") \
        and gj_is_name(node.value, "os")


def gj_scope_text(scope):
    return "%s()" % scope if scope else "module level"


def rule_j1(source):
    """J1: the copied front still holds its hardening markers -- _HeaderDeadlineReader
    defined and used, `headers.defects` read, and hmac.compare_digest called exactly
    once, inside _precheck (the bearer check is the file's one constant-time compare)."""
    tree, found = gj_parse(source)
    if tree is None:
        return found
    defined = used = defects = False
    digests = []
    for node, scope in gj_scopes(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_HeaderDeadlineReader":
            defined = True
        elif (isinstance(node, ast.Name) and node.id == "_HeaderDeadlineReader"
              and isinstance(node.ctx, ast.Load)):
            used = True
        elif isinstance(node, ast.Attribute) and node.attr == "defects" and (
                gj_is_name(node.value, "headers")
                or (isinstance(node.value, ast.Attribute) and node.value.attr == "headers")):
            defects = True
        elif isinstance(node, ast.Call) and gj_callee(node) == "compare_digest":
            digests.append((node.lineno, scope))
    if not defined:
        found.append("no class _HeaderDeadlineReader: the header deadline is gone")
    elif not used:
        found.append("_HeaderDeadlineReader is defined but never used")
    if not defects:
        found.append("headers.defects is never read: a malformed header block is no longer refused")
    digests.sort()
    if len(digests) != 1:
        found.append("%d compare_digest call(s) (lines %s), expected exactly one, in _precheck"
                     % (len(digests), [ln for ln, _s in digests]))
    for lineno, scope in digests:
        if scope != "_precheck":
            found.append("%d: compare_digest called in %s, not _precheck"
                         % (lineno, gj_scope_text(scope)))
    return found


def gj_env_key(node):
    """The key node an environment read *node* names (os.getenv(K), os.environ.get(K),
    os.environ[K], K in os.environ), else None."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("getenv", "getenvb") \
                and gj_is_name(func.value, "os"):
            return node.args[0]
        if isinstance(func, ast.Attribute) and func.attr in ("get", "pop", "setdefault") \
                and gj_is_environ(func.value):
            return node.args[0]
    if isinstance(node, ast.Subscript) and gj_is_environ(node.value):
        key = node.slice
        return getattr(key, "value", key) if type(key).__name__ == "Index" else key
    if isinstance(node, ast.Compare) and len(node.ops) == 1 \
            and isinstance(node.ops[0], (ast.In, ast.NotIn)) and gj_is_environ(node.comparators[0]):
        return node.left
    return None


def rule_j2(source):
    """J2: no os.environ / os.getenv outside main(), none whose key ends in _PROXY
    (anywhere, main() included); no urllib.request; no asyncio; no
    socket.create_connection."""
    tree, found = gj_parse(source)
    if tree is None:
        return found
    for node, scope in gj_scopes(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Attribute) and node.attr in GJ_ENV_ATTRS \
                and gj_is_name(node.value, "os") and scope != "main":
            found.append("%d: os.%s read in %s, outside main()" % (line, node.attr, gj_scope_text(scope)))
        key = gj_env_key(node)
        if isinstance(key, ast.Constant) and isinstance(key.value, (str, bytes)):
            text = key.value.decode("ascii", "replace") if isinstance(key.value, bytes) else key.value
            if text.upper().endswith("_PROXY"):
                found.append("%d: an environment read of %s (a *_PROXY variable) in %s"
                             % (line, text, gj_scope_text(scope)))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "urllib.request" or alias.name.startswith("urllib.request."):
                    found.append("%d: import %s" % (line, alias.name))
                if alias.name.split(".")[0] == "asyncio":
                    found.append("%d: import %s" % (line, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            if module == "urllib.request" or module.startswith("urllib.request.") \
                    or (module == "urllib" and "request" in names):
                found.append("%d: from %s import %s" % (line, module, ", ".join(names)))
            if module.split(".")[0] == "asyncio":
                found.append("%d: from %s import ..." % (line, module))
            if module == "os" and set(names) & set(GJ_ENV_ATTRS):
                found.append("%d: from os import %s (an environment read main() cannot scope)"
                             % (line, ", ".join(sorted(set(names) & set(GJ_ENV_ATTRS)))))
            if module == "socket" and "create_connection" in names:
                found.append("%d: from socket import create_connection" % line)
        elif isinstance(node, ast.Attribute):
            if node.attr == "request" and gj_is_name(node.value, "urllib"):
                found.append("%d: urllib.request referenced" % line)
            if node.attr == "create_connection" and gj_is_name(node.value, "socket"):
                found.append("%d: socket.create_connection referenced" % line)
        elif isinstance(node, ast.Name) and node.id == "asyncio":
            found.append("%d: asyncio referenced" % line)
    return sorted(set(found), key=lambda f: (int(f.split(":", 1)[0]), f))


def rule_j3(source):
    """J3: no string literal starting with Access-Control- (no CORS header, ever)."""
    tree, found = gj_parse(source)
    if tree is None:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            text = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
            if text.lower().startswith("access-control-"):
                found.append("%d: a literal %r" % (node.lineno, text[:60]))
    return sorted(found, key=lambda f: (int(f.split(":", 1)[0]), f))


def gj_banners(source):
    """({unit: [line, ...]}) of every `# Unit N:` banner line in *source*."""
    lines = {}
    for match in GJ_BANNER_RE.finditer(source):
        lines.setdefault(int(match.group(1)), []).append(source.count("\n", 0, match.start()) + 1)
    return lines


def rule_j4(source):
    """J4: (a) every putheader call is inside _rt_send, and _rt_send is called at
    exactly one site, inside _post, with _rt_upstream_headers(...) as its headers
    argument; (b) the attribute client_headers is read only inside
    _rt_upstream_headers; (c) nothing between the `# Unit 2:` and `# Unit 6:`
    banners references socket, ssl, http, select, time or threading -- and a
    missing, repeated or out-of-order banner is itself a finding."""
    tree, found = gj_parse(source)
    if tree is None:
        return found
    scoped = gj_scopes(tree)

    # (a)
    putheaders, sends = [], []
    for node, scope in scoped:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "putheader":
            putheaders.append((node.lineno, scope))
        elif isinstance(node, ast.Call) and gj_callee(node) == "_rt_send":
            sends.append((node, scope))
    if not putheaders:
        found.append("(a) no putheader call at all: the rule would pass vacuously")
    for lineno, scope in sorted(putheaders, key=lambda p: p[0]):
        if scope != "_rt_send":
            found.append("%d: putheader called in %s, not _rt_send" % (lineno, gj_scope_text(scope)))
    if len(sends) != 1:
        found.append("(a) _rt_send is called at %d site(s) (lines %s), expected exactly one, in _post"
                     % (len(sends), sorted(n.lineno for n, _s in sends)))
    for node, scope in sends:
        if scope != "_post":
            found.append("%d: _rt_send called in %s, not _post" % (node.lineno, gj_scope_text(scope)))
        headers = node.args[2] if len(node.args) > 2 else next(
            (kw.value for kw in node.keywords if kw.arg == "headers"), None)
        if not (isinstance(headers, ast.Call) and gj_callee(headers) == "_rt_upstream_headers"):
            found.append("%d: _rt_send's headers argument is not _rt_upstream_headers(...)" % node.lineno)

    # (b)
    reads_inside = 0
    for node, scope in scoped:
        read = None
        if isinstance(node, ast.Attribute) and node.attr == "client_headers" \
                and isinstance(node.ctx, ast.Load):
            read = "client_headers read"
        elif isinstance(node, ast.Call) and gj_is_name(node.func, "getattr") and len(node.args) > 1 \
                and isinstance(node.args[1], ast.Constant) and node.args[1].value == "client_headers":
            read = "client_headers read (getattr)"
        if read is None:
            continue
        if scope == "_rt_upstream_headers":
            reads_inside += 1
        else:
            found.append("%d: %s in %s, not _rt_upstream_headers"
                         % (node.lineno, read, gj_scope_text(scope)))
    if not reads_inside:
        found.append("(b) _rt_upstream_headers reads no client_headers: the rule would pass vacuously")

    # (c)
    banners = gj_banners(source)
    order = []
    for unit in GJ_PURE_UNITS:
        at = banners.get(unit, [])
        if not at:
            found.append("(c) the `# Unit %d:` banner is missing" % unit)
        elif len(at) > 1:
            found.append("(c) the `# Unit %d:` banner appears %d times (lines %s)" % (unit, len(at), at))
        else:
            order.append(at[0])
    if len(order) == len(GJ_PURE_UNITS) and order != sorted(order):
        found.append("(c) the unit 2-6 banners are out of order (lines %s)" % order)
    elif len(order) == len(GJ_PURE_UNITS):
        first, last = order[0], order[-1]

        def unit_of(lineno):
            return max(u for u, ln in zip(GJ_PURE_UNITS, order) if ln <= lineno)

        hits = set()
        for node in ast.walk(tree):
            lineno = getattr(node, "lineno", None)
            if lineno is None or not first < lineno < last:
                continue
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Import):
                names += [alias.name.split(".")[0] for alias in node.names]
                names += [alias.asname for alias in node.names if alias.asname]
            elif isinstance(node, ast.ImportFrom):
                names.append((node.module or "").split(".")[0])
                names += [alias.asname or alias.name for alias in node.names]
            for name in names:
                if name in GJ_PURE_FORBIDDEN:
                    hits.add((lineno, "%d: unit %d references %r (units 2-5 take no socket and "
                                      "no clock)" % (lineno, unit_of(lineno), name)))
        found += [text for _ln, text in sorted(hits)]
    return found


def rule_j13(rows, reasons):
    """J13: every (host, name) census row for llm-router.py has a HAND_COPY_REASONS entry."""
    found = []
    for host, name in rows:
        base = os.path.basename(host)
        if base == "llm-router.py" and (base, name) not in reasons:
            found.append("%s: %s is a hand copy with no HAND_COPY_REASONS entry" % (host, name))
    return found


# The planted negative controls (docs/subsystems/tests.md: "Every scanner suite
# must carry a negative control").  Each is (checker, clean, planted, needles):
# the checker over *clean* must report nothing (the plant, not noise, is what
# turns it), over *planted* it must report every needle.
GJ_J1_CLEAN = '''import hmac


class _HeaderDeadlineReader:
    pass


class Handler:
    def setup(self):
        self.reader = _HeaderDeadlineReader()

    def _precheck(self, presented, token):
        if self.headers.defects:
            return None
        return hmac.compare_digest(presented, token)
'''
GJ_J1_PLANTED = GJ_J1_CLEAN + '''

def _check_key(api_key, token):
    return hmac.compare_digest(api_key, token)
'''

GJ_J2_CLEAN = '''import os


def _rt_proxy_hint():
    return None


def main():
    return os.environ.get("TF_ROUTER_HOME")
'''
GJ_J2_PLANTED = GJ_J2_CLEAN.replace(
    "def _rt_proxy_hint():\n    return None\n",
    "def _rt_proxy_hint():\n    return os.environ.get(\"HTTPS_PROXY\")\n")

GJ_J3_CLEAN = '''HEADERS = (("Content-Type", "application/json"),)
'''
GJ_J3_PLANTED = '''HEADERS = (("Content-Type", "application/json"),
           ("Access-Control-Allow-Origin", "*"))
'''

GJ_J4_CLEAN = '''# ---------------------------------------------------------------------------
# Unit 1: errors
# ---------------------------------------------------------------------------
import time

# ---------------------------------------------------------------------------
# Unit 2: config
# ---------------------------------------------------------------------------
def load(raw):
    return dict(raw)

# ---------------------------------------------------------------------------
# Unit 3: inbound
# ---------------------------------------------------------------------------
class InboundRequest:
    client_headers = None

# ---------------------------------------------------------------------------
# Unit 4: SSE toolkit
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Unit 5: adapters
# ---------------------------------------------------------------------------
def translate(inbound):
    return inbound

# ---------------------------------------------------------------------------
# Unit 6: outbound transport
# ---------------------------------------------------------------------------
def _rt_send(backend, path, headers, body):
    conn = backend.connect(time.monotonic())
    for name, value in headers:
        conn.putheader(name, value)
    return conn


def _rt_upstream_headers(inbound):
    return list((inbound.client_headers or {}).items())

# ---------------------------------------------------------------------------
# Unit 7: HTTP front
# ---------------------------------------------------------------------------
class Handler:
    def _post(self, inbound):
        return _rt_send(inbound.backend, "/v1/messages", _rt_upstream_headers(inbound), b"")
'''
GJ_J4_PLANTED = GJ_J4_CLEAN.replace(
    "def translate(inbound):\n    return inbound\n",
    "def translate(inbound):\n    started = time.monotonic()\n    return inbound, started\n",
).replace(
    "class Handler:\n",
    "def _log_inbound(inbound):\n    return len(inbound.client_headers)\n\n\n"
    "class Handler:\n    def _probe(self, conn):\n        conn.putheader(\"X-Tf-Probe\", \"1\")\n\n",
)

GJ_J19_REASONS = {("llm-router.py", "_tf_declared"): "tf: a declared hand copy"}
GJ_J19_CLEAN = [("Scripts/llm-router.py", "_tf_declared"),
                ("Scripts/mcp-tf-other.py", "_tf_other")]     # another host is not J13's to judge
GJ_J19_PLANTED = GJ_J19_CLEAN + [("Scripts/llm-router.py", "_tf_undeclared")]


def gj_j19_checker(rows):
    return rule_j13(rows, GJ_J19_REASONS)


GJ_CONTROLS = {
    "control: 2nd compare_digest": (rule_j1, GJ_J1_CLEAN, GJ_J1_PLANTED,
                                    ("2 compare_digest call(s)",
                                     "compare_digest called in _check_key()")),
    "control: HTTPS_PROXY read": (rule_j2, GJ_J2_CLEAN, GJ_J2_PLANTED,
                                  ("environment read of HTTPS_PROXY",
                                   "os.environ read in _rt_proxy_hint()")),
    "control: CORS literal": (rule_j3, GJ_J3_CLEAN, GJ_J3_PLANTED,
                              ("'Access-Control-Allow-Origin'",)),
    "control: three J4 plants": (rule_j4, GJ_J4_CLEAN, GJ_J4_PLANTED,
                                 ("putheader called in _probe()",
                                  "client_headers read in _log_inbound()",
                                  "unit 5 references 'time'")),
    "control: undeclared hand copy": (gj_j19_checker, GJ_J19_CLEAN, GJ_J19_PLANTED,
                                      ("_tf_undeclared is a hand copy",)),
}


def gj_control(checker, clean, planted, needles):
    """(problems, detail) of one planted control: *checker* over *clean* must report
    nothing; over *planted* it must report every needle.  The checker is a parameter
    so the SC-3 red run can hand it a stub that returns no finding."""
    problems = []
    if planted == clean:
        problems.append("the plant did not change the input (an anchor drifted)")
    base = checker(clean)
    if base:
        problems.append("the clean baseline already has %d finding(s): %s" % (len(base), base[:3]))
    got = checker(planted)
    for needle in needles:
        if not any(needle in finding for finding in got):
            problems.append("the plant %r was not reported (findings: %s)" % (needle, got[:4] or "none"))
    return problems, ["clean       : %d finding(s)" % len(base)] + \
        ["planted     : %s" % finding for finding in (got or ["no finding"])]


def group_j_static(suite):
    """J1-J4 and J13 over the live router, then their planted controls J15-J19."""
    rel = os.path.relpath(SERVER, H.REPO_ROOT)
    results = {}
    try:
        with open(SERVER, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        source = None
        why = "cannot read %s: %s: %s" % (rel, type(exc).__name__, exc)
        for cid in GJ_STATIC_CASES[:4]:
            results[cid] = ([why], [])

    if source is not None:
        tree, _bad = gj_parse(source)
        digests = sorted(node.lineno for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and gj_callee(node) == "compare_digest") \
            if tree is not None else []
        sends = sorted(node.lineno for node in ast.walk(tree)
                       if isinstance(node, ast.Call) and gj_callee(node) == "_rt_send") \
            if tree is not None else []
        banners = gj_banners(source)
        live = (
            (rule_j1, ["scope       : every node of %s" % rel,
                       "digests     : compare_digest at line(s) %s" % digests]),
            (rule_j2, ["scope       : every node of %s; os.environ/os.getenv allowed in main() "
                       "only, never a *_PROXY key" % rel]),
            (rule_j3, ["scope       : every str/bytes constant of %s" % rel]),
            (rule_j4, ["_rt_send    : called at line(s) %s" % sends,
                       "banners     : %s" % ", ".join("unit %d @ %s" % (u, banners.get(u))
                                                     for u in GJ_PURE_UNITS),
                       "forbidden   : %s between unit 2 and unit 6" % ", ".join(GJ_PURE_FORBIDDEN)]),
        )
        for cid, (rule, detail) in zip(GJ_STATIC_CASES, live):
            try:
                results[cid] = (rule(source), detail)
            except Exception as exc:  # noqa: BLE001 -- a broken rule fails, never aborts the group
                results[cid] = (["rule raised %s: %s" % (type(exc).__name__, str(exc)[:200])], detail)

    # J13 -- the generator's own census, judged FAIL-grade for this host.
    cid = GJ_STATIC_CASES[4]
    try:
        amg = H.load_module_from_path("ph_amalgamate_j", H.repo_path("Scripts", "amalgamate.py"))
        rows = amg.hand_copies(amg.load_all_blocks(), [pathlib.Path(SERVER)])
        results[cid] = (rule_j13(rows, amg.HAND_COPY_REASONS),
                        ["census      : %s" % ("%s: %s" % row) for row in rows]
                        or ["census      : no hand copy in %s" % rel])
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a broken census fails the case
        results[cid] = (["the census raised %s: %s" % (type(exc).__name__, str(exc)[:200])], [])

    for cid, (checker, clean, planted, needles) in GJ_CONTROLS.items():
        try:
            results[cid] = gj_control(checker, clean, planted, needles)
        except Exception as exc:  # noqa: BLE001
            results[cid] = (["control raised %s: %s" % (type(exc).__name__, str(exc)[:200])], [])

    for cid in GJ_STATIC_CASES:
        problems, detail = results.get(cid, (["case did not run"], []))
        suite.record(GJ, cid, problems, detail=detail)


def group_j(suite, fixture_root, pyc_before, tree_before):
    """J. static and hygiene -- Step 7's in-process cases: the sans-IO
    AnthropicSseEncoder's invariants and _rt_check_forward_tables.

    A missing symbol FAILS the case with its name (the red state before Step 7),
    never crashes the group.  *fixture_root*, *pyc_before* and *tree_before*
    are for Step 12's J14, J22 and J9/J10 (group_j_hygiene, run last).
    """
    group_j_static(suite)
    results = {}
    try:
        mod = H.load_module_from_path("ph_llm_router_j", SERVER)
    except Exception as exc:  # noqa: BLE001 -- an import failure fails the group
        for cid in GJ_CASES:
            suite.record(GJ, cid, ["cannot import %s: %s: %s"
                                   % (os.path.relpath(SERVER, H.REPO_ROOT),
                                      type(exc).__name__, exc)])
        group_j_hygiene(suite, fixture_root, pyc_before, tree_before)
        return

    def encoder():
        return mod.AnthropicSseEncoder(GJ_MESSAGE_ID, GJ_MODEL)

    def j6():
        problems = gj_missing(mod, ("AnthropicSseEncoder",))
        if problems:
            return problems, []
        enc = encoder()
        enc.start(3)
        enc.text("tf j6")
        err = enc.error("api_error", "tf j6 failure")
        try:
            events = gj_events(err)
        except ValueError as exc:
            events = []
            problems.append("error() output is malformed: %s" % exc)
        want = [("error", {"type": "error", "error": {"type": "api_error", "message": "tf j6 failure"}})]
        if events != want:
            problems.append("error() emitted %s, expected exactly one event: error envelope"
                            % gj_shape(events))
        if getattr(enc, "closed", None) is not True:
            problems.append("closed is %r after error(), expected True" % (getattr(enc, "closed", None),))
        after = (("start", lambda: enc.start(1)), ("text", lambda: enc.text("tf after")),
                 ("tool_block", lambda: enc.tool_block(*GJ_TOOL)), ("ping", enc.ping),
                 ("finish", lambda: enc.finish("end_turn", dict(GJ_USAGE))),
                 ("error", lambda: enc.error("api_error", "tf again")))
        shown = []
        for name, call in after:
            got = call()
            shown.append("%s=%r" % (name, got[:24] if isinstance(got, bytes) else got))
            if got != b"" or not isinstance(got, bytes):
                problems.append("%s() after error() returned %r, expected b\"\""
                                % (name, got[:60] if isinstance(got, bytes) else got))
        if getattr(enc, "closed", None) is not True:
            problems.append("closed flipped back to %r" % (getattr(enc, "closed", None),))
        return problems, ["error()     : %s" % gj_shape(events),
                          "after       : %s" % ", ".join(shown)]

    def j7():
        problems = gj_missing(mod, ("AnthropicSseEncoder",))
        if problems:
            return problems, []
        enc = encoder()
        enc.start(3)
        tool = enc.tool_block(*GJ_TOOL)
        fin = enc.finish("end_turn", dict(GJ_USAGE))
        try:
            tool_events, fin_events = gj_events(tool), gj_events(fin)
        except ValueError as exc:
            return ["malformed encoder output: %s" % exc], []
        deltas = [p for n, p in fin_events if n == "message_delta"]
        if len(deltas) != 1:
            problems.append("finish() emitted %d message_delta event(s), expected 1" % len(deltas))
        else:
            reason = (deltas[0].get("delta") or {}).get("stop_reason")
            if reason != "tool_use":
                problems.append("stop_reason %r after a tool_block with finish(\"end_turn\"), "
                                "expected \"tool_use\"" % (reason,))
            if deltas[0].get("usage") != GJ_USAGE:
                problems.append("message_delta usage %r, expected %r" % (deltas[0].get("usage"), GJ_USAGE))
        if not fin_events or fin_events[-1][0] != "message_stop":
            problems.append("finish() does not end with message_stop: %s" % gj_shape(fin_events))
        starts = [p for n, p in tool_events if n == "content_block_start"]
        block = starts[0].get("content_block") if starts else None
        if block != {"type": "tool_use", "id": GJ_TOOL[0], "name": GJ_TOOL[1], "input": {}}:
            problems.append("tool_block's content_block_start carries %r" % (block,))
        partial = [((p.get("delta") or {}).get("type"), (p.get("delta") or {}).get("partial_json"))
                   for n, p in tool_events if n == "content_block_delta"]
        if partial != [("input_json_delta", GJ_TOOL[2])]:
            problems.append("tool_block's deltas %r, expected one input_json_delta with the arguments"
                            % (partial,))
        return problems, ["tool_block  : %s" % gj_shape(tool_events),
                          "finish      : %s, stop_reason %r"
                          % (gj_shape(fin_events),
                             (deltas[0].get("delta") or {}).get("stop_reason") if deltas else None)]

    def j8():
        problems = gj_missing(mod, ("AnthropicSseEncoder",))
        if problems:
            return problems, []
        enc = encoder()
        try:
            start = gj_events(enc.start(3))
            text = gj_events(enc.text("tf j8"))
            tool = gj_events(enc.tool_block(*GJ_TOOL))
            fin = gj_events(enc.finish("tool_use", dict(GJ_USAGE)))
        except ValueError as exc:
            return ["malformed encoder output: %s" % exc], []
        if [n for n, _p in start] != ["message_start"]:
            problems.append("start() emitted %s, expected one message_start" % gj_shape(start))
        got = gj_shape(text + tool + fin)
        want = ["content_block_start[0]", "content_block_delta[0]", "content_block_stop[0]",
                "content_block_start[1]", "content_block_delta[1]", "content_block_stop[1]",
                "message_delta", "message_stop"]
        if got != want:
            problems.append("events after message_start %s, expected %s" % (got, want))
        if "content_block_stop[0]" not in gj_shape(tool):
            problems.append("tool_block() did not close the open text block itself (stop[0] not in "
                            "its own output: %s)" % gj_shape(tool))
        for name, payload in text:
            if name == "content_block_start" and payload.get("content_block") != {"type": "text", "text": ""}:
                problems.append("the text block starts as %r" % (payload.get("content_block"),))
            if name == "content_block_delta" and payload.get("delta") != {"type": "text_delta",
                                                                           "text": "tf j8"}:
                problems.append("the text delta is %r" % (payload.get("delta"),))
        for name, payload in tool:
            if name == "content_block_start" and (payload.get("content_block") or {}).get("type") != "tool_use":
                problems.append("block 1 starts as %r, expected tool_use" % (payload.get("content_block"),))
        return problems, ["text        : %s" % gj_shape(text),
                          "tool_block  : %s" % gj_shape(tool),
                          "finish      : %s" % gj_shape(fin)]

    def j12():
        problems = gj_missing(mod, ("_rt_check_forward_tables", "KIND_CLASSES", "_NEVER_FORWARD"))
        if problems:
            return problems, []
        table = mod.KIND_CLASSES
        kinds = tuple(sorted(table))
        if kinds != GJ_KINDS:
            problems.append("KIND_CLASSES holds %s, expected all of %s (a partial table makes the "
                            "check vacuous)" % (list(kinds), list(GJ_KINDS)))
        if "authorization" not in mod._NEVER_FORWARD:
            problems.append("_NEVER_FORWARD does not hold \"authorization\"")
        try:
            mod._rt_check_forward_tables(table)
        except Exception as exc:  # noqa: BLE001 -- a refusal of the real table is the finding
            problems.append("the real KIND_CLASSES was refused: %s: %s" % (type(exc).__name__, exc))
        shown = []
        for kind in kinds:
            cls = table[kind]
            before = frozenset(cls.FORWARDABLE)
            doctored = dict(table)
            doctored[kind] = type("TfDoctored" + cls.__name__, (cls,),
                                  {"FORWARDABLE": before | {"authorization"}})
            try:
                mod._rt_check_forward_tables(doctored)
            except Exception as exc:  # noqa: BLE001 -- any refusal is the expected answer
                shown.append("%s=%s" % (kind, type(exc).__name__))
            else:
                shown.append("%s=accepted" % kind)
                problems.append("%s: a FORWARDABLE with \"authorization\" was accepted" % kind)
            if frozenset(table[kind].FORWARDABLE) != before:
                problems.append("%s: the real class's FORWARDABLE changed under the doctored copy" % kind)
        return problems, ["real table  : %s accepted" % ", ".join(kinds),
                          "doctored    : + authorization -> %s" % ", ".join(shown)]

    def j21():
        problems = []
        dumped = None
        if hasattr(mod, "_rt_dumps"):
            try:
                dumped = mod._rt_dumps({"x": "\udfff"})
            except Exception as exc:  # noqa: BLE001 -- raising is the defect
                problems.append("_rt_dumps raised %s on a lone surrogate" % type(exc).__name__)
            else:
                if dumped != b'{"x":"\\udfff"}':
                    problems.append("_rt_dumps({\"x\": \"\\udfff\"}) -> %r, expected b'{\"x\":\"\\\\udfff\"}'"
                                    % (dumped,))
        else:
            problems += gj_missing(mod, ("_rt_dumps",))
        text = None
        missing = gj_missing(mod, ("AnthropicSseEncoder",))
        if missing:
            problems += missing
        else:
            enc = encoder()
            enc.start(1)
            try:
                text = enc.text("a\ud800b")
            except Exception as exc:  # noqa: BLE001 -- raising is the defect
                problems.append("text(\"a\\ud800b\") raised %s" % type(exc).__name__)
            else:
                try:
                    events = gj_events(text)
                except ValueError as exc:
                    events = []
                    problems.append("text() output is not ASCII SSE: %s" % exc)
                if b"\\ud800" not in (text if isinstance(text, bytes) else b""):
                    problems.append("text() output carries no \\ud800 escape: %r" % (text,))
                deltas = [p.get("delta") for n, p in events if n == "content_block_delta"]
                if deltas != [{"type": "text_delta", "text": "a\ud800b"}]:
                    problems.append("the delta round-trips as %r" % (deltas,))
        return problems, ["_rt_dumps   : %r" % (dumped,),
                          "text()      : %r" % ((text or b"")[-80:],)]

    try:
        for cid, fn in zip(GJ_CASES, (j6, j7, j8, j12, j21)):
            try:
                results[cid] = fn()
            except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
                results[cid] = (["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], [])
    except Exception as exc:  # noqa: BLE001
        setup = "the group setup raised %s: %s" % (type(exc).__name__, str(exc)[:200])
    else:
        setup = "case did not run"
    for cid in GJ_CASES:
        problems, detail = results.get(cid, ([setup], []))
        suite.record(GJ, cid, problems, detail=detail)

    group_j_hygiene(suite, fixture_root, pyc_before, tree_before)


# Step 12 (part 2): the fixture sweep J5 and its planted control J22, the
# hand-copy parity J20, the live summary-line case J14, the _log_value cut
# marker J11, and the hygiene cases J9/J10 -- which run LAST, after every live
# case of the suite has written what it writes.  Like J1-J4, the sweep is a
# function of its INPUT (the directory), never of FIXTURES itself, so J22 runs
# the SAME function, unchanged, over a planted sandbox directory.
GJ_HYGIENE_CASES = (
    "fixture sweep: no key, no TF_*",    # J5
    "control: planted key, sentinel",    # J22 (J5)
    "hand copies match _mcp_chrome",     # J20
    "one req line per request",          # J14
    "_log_value marks every cut",        # J11
    "no .pyc written in the repo",       # J9
    "no new repo path; tmp writes",      # J10
)

CHROME = H.repo_path("Scripts", "_mcp_chrome.py")
GJ_HAND_COPIES = ("_CH_TRANSLATION_PREFIXES", "_ch_embedded_ipv4", "_ch_address_refused")

# J5: the key-prefix patterns.  The suite cannot know the user's real key (plan
# "Out of Scope"), so it sweeps by SHAPE: the two prefixed key families a
# backend of this router can hold (Anthropic sk-ant-, OpenAI-style sk-) and the
# prefix-less Mistral key (32 alphanumerics with a letter and a digit, standing
# alone).  A finding names the file, the pattern and the byte offset -- never
# the matched text, which would copy a key into the report.
GJ_SWEEP_PATTERNS = (
    ("an Anthropic key prefix (sk-ant-)", re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("an sk- key prefix", re.compile(rb"(?<![A-Za-z0-9_-])sk-(?!ant-)[A-Za-z0-9_-]{16,}")),
    ("a Mistral-shaped key (32 alphanumerics)",
     re.compile(rb"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{0,31}[0-9])(?=[A-Za-z0-9]{0,31}[A-Za-z])"
                rb"[A-Za-z0-9]{32}(?![A-Za-z0-9])")),
)
GJ_SENTINEL_HEX_RE = re.compile(r"[0-9a-f]{32}$")     # the per-run tail every TF_* sentinel ends in

# J22: the sandbox the planted control sweeps -- one clean file (must NOT be
# reported) and the two plants of the plan (both MUST be reported).
GJ_J22_CLEAN = ("tf_clean.sse",
                'data: {"id":"msg_tfll0123456789abcdef0123","type":"message_start"}\n\n')
GJ_J22_SENTINEL = "tf_planted_sentinel.json"
GJ_J22_PREFIX = "tf_planted_prefix.json"

# J14: one live router (--debug --log-file) in front of one passthrough peer.
GJ_J14_BACKEND = "tfj"
GJ_J14_ROUTE = "claude-tf-j14"
GJ_J14_MODEL = "tf-j14"
GJ_J14_TEXT = "tf j14 body " + TF_CLIENT_XKEY          # a body byte, and a sentinel, the line must never carry
GJ_J14_KEYS = ("endpoint", "route", "kind", "stream", "status", "up_status", "in", "out", "ms", "up_ms", "end")
GJ_J14_OPTIONAL = ("dropped_thinking",)                # mistral only (§9)
GJ_J14_ENDS = ("message_stop", "error", "disconnected", "shutdown", "json")
GJ_J14_LINE_S = 5.0                                    # a request's req line must be in the log within this
GJ_J14_FIELD_RE = re.compile(r"([a-z_]+)=(\S+)")
GJ_J14_COUNT = 5


def gj_sweep_needles():
    """[(label, text)]: every TF_* sentinel's value, and its stem (the value
    without its per-run hex tail), so a sentinel of an EARLIER run committed into
    a fixture is found too."""
    out = []
    for label, value in GI_SENTINEL_LABELS:
        out.append((label, value))
        stem = GJ_SENTINEL_HEX_RE.sub("", value)
        if stem and stem != value:
            out.append(("%s stem" % label, stem))
    return out


def gj_sweep(dirpath, needles=None):
    """J5: [finding] of every file under *dirpath*: a TF_* sentinel (value or
    stem) or a key-prefix pattern, by file, label and byte offset only.  A file
    that cannot be read, or is not a regular file, is a finding (the sweep
    cannot vouch for it); a directory with no file at all is a finding (a sweep
    of nothing must not pass, ADR 0017)."""
    needles = gj_sweep_needles() if needles is None else needles
    if not os.path.isdir(dirpath):
        return ["%s is not a directory" % dirpath]
    found, files = [], 0
    for root, dirs, names in os.walk(dirpath):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, dirpath)
            if os.path.islink(path) or not os.path.isfile(path):
                found.append("%s: not a regular file, not swept" % rel)
                continue
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                found.append("%s: unreadable (%s), not swept" % (rel, type(exc).__name__))
                continue
            files += 1
            for label, value in needles:
                at = data.find(value.encode("utf-8"))
                if at >= 0:
                    found.append("%s: %s at byte %d" % (rel, label, at))
            for label, rx in GJ_SWEEP_PATTERNS:
                match = rx.search(data)
                if match:
                    found.append("%s: %s at byte %d" % (rel, label, match.start()))
    if not files:
        found.append("no file swept under %s: a sweep of nothing would pass vacuously" % dirpath)
    return found


def gj_sweep_files(dirpath):
    """The *dirpath*-relative names of every file *gj_sweep* visits under *dirpath*."""
    out = []
    for root, dirs, names in os.walk(dirpath):
        dirs.sort()
        out += [os.path.relpath(os.path.join(root, n), dirpath) for n in sorted(names)]
    return out


def gj_sweep_control(sweep, sandbox):
    """(problems, detail) of J22: *sweep* over *sandbox* holding one clean file,
    one file planted with TF_KEY_MISTRAL and one planted with a key-prefix
    string must report both plants, and nothing for the clean file.  The sweep
    is a parameter so the SC-3 red run can hand it a stub that returns no finding."""
    prefix_plant = "sk-ant-api03-tfj22" + secrets.token_hex(12)
    write_file(os.path.join(sandbox, GJ_J22_CLEAN[0]), GJ_J22_CLEAN[1])
    write_file(os.path.join(sandbox, GJ_J22_SENTINEL),
               json.dumps({"tf": "planted", "api_key": TF_KEY_MISTRAL}) + "\n")
    write_file(os.path.join(sandbox, GJ_J22_PREFIX),
               json.dumps({"tf": "planted", "note": "pasted " + prefix_plant}) + "\n")
    got = sweep(sandbox)
    problems = []
    wants = ((GJ_J22_SENTINEL, "TF_KEY_MISTRAL"), (GJ_J22_PREFIX, "Anthropic key prefix"))
    for name, needle in wants:
        if not any(f.startswith(name + ":") and needle in f for f in got):
            problems.append("the plant %s (%s) was not reported (findings: %s)"
                            % (name, needle, got[:4] or "none"))
    noise = [f for f in got if not f.startswith((GJ_J22_SENTINEL + ":", GJ_J22_PREFIX + ":"))]
    if noise:
        problems.append("the sweep reported %d finding(s) outside the two plants: %s" % (len(noise), noise[:3]))
    return problems, ["sandbox     : %s (3 files: 1 clean, 2 planted)"
                      % os.path.relpath(sandbox, H.REPO_ROOT)] + \
        ["planted     : %s" % f for f in (got or ["no finding"])]


def gj_top_level(tree, name):
    """The module-level nodes of *tree* that define *name* (def, class, or assignment)."""
    hits = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            hits.append(node)
        elif isinstance(node, ast.Assign) and any(gj_is_name(t, name) for t in node.targets):
            hits.append(node)
        elif isinstance(node, ast.AnnAssign) and gj_is_name(node.target, name):
            hits.append(node)
    return hits


def rule_j20(copy_source, origin_source, names=GJ_HAND_COPIES):
    """J20: for each of *names*, the one module-level node in *copy_source* has the
    same ast.dump as the one in *origin_source* (docstrings included, comments and
    line numbers not).  A finding names the drifted copy."""
    copy_tree, found = gj_parse(copy_source)
    origin_tree, more = gj_parse(origin_source)
    found += ["origin: %s" % f for f in more]
    if copy_tree is None or origin_tree is None:
        return found
    for name in names:
        copies, origins = gj_top_level(copy_tree, name), gj_top_level(origin_tree, name)
        if len(copies) != 1 or len(origins) != 1:
            found.append("%s: defined %d time(s) in the copy and %d time(s) in the source, expected once each"
                         % (name, len(copies), len(origins)))
            continue
        got, want = ast.dump(copies[0]), ast.dump(origins[0])
        if got != want:
            at = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
            found.append("%s: the copy (line %d) has drifted from its source (line %d): ast.dump differs "
                         "at offset %d (copy %r, source %r)"
                         % (name, copies[0].lineno, origins[0].lineno, at,
                            got[max(0, at - 20):at + 40], want[max(0, at - 20):at + 40]))
    return found


def gj_req_lines(path):
    """The messages of every `req ` line of the router log at *path*."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        _head, sep, msg = line.partition(" llm-router DEBUG ")
        if sep and msg.startswith("req "):
            out.append(msg)
    return out


def gj_j14_script(_index, request):
    """The J14 peer: count_tokens answers a count, a streamed request a stream, else JSON."""
    if "count_tokens" in (request.get("line") or ""):
        return [("respond_json", 200, {"input_tokens": 5})]
    try:
        stream = json.loads((request.get("body") or b"").decode("utf-8")).get("stream")
    except (UnicodeDecodeError, ValueError, AttributeError):
        stream = None
    if stream:
        return [("respond_sse", GD_STREAM_LINES)]
    return [("respond_json", 200, GD_ANSWER)]


def gj_j14(fixture_root):
    """(problems, detail) of J14: five POSTs, one at a time, each waited for in the
    log before the next: exactly one `req ` line each, the §9 key set, the
    client's status, and no sentinel and no body byte on any line."""
    sandbox = new_sandbox(fixture_root, "j14")
    logpath = os.path.join(sandbox, "j14.log")
    peer = ScriptedPeer(script=gj_j14_script)
    proc = None
    problems, detail = [], []
    try:
        cfg = {"auth_token": TF_ROUTER_TOKEN,
               "backends": {GJ_J14_BACKEND: {"kind": "passthrough", "base_url": peer.url(),
                                             "api_key": TF_KEY_PASS, "auth_header": "x-api-key"}},
               "routes": {GJ_J14_ROUTE: {"backend": GJ_J14_BACKEND, "model": GJ_J14_MODEL}}}
        cfgpath = write_config(sandbox, cfg)
        proc = RouterProc(sandbox, cfgpath, label="j14", extra_argv=("--debug", "--log-file", logpath))
        if proc.wait_ready() is None:
            return [gb_not_ready(proc)], []
        client = proc.client()
        body = {"model": GJ_J14_ROUTE, "max_tokens": 16, "stream": False,
                "messages": [{"role": "user", "content": GJ_J14_TEXT}]}
        sbody = dict(body, stream=True)
        xkey = {"x-api-key": TF_CLIENT_XKEY}
        size = len(json.dumps(body).encode("utf-8"))
        ssize = len(json.dumps(sbody).encode("utf-8"))
        # (label, send, want): send() -> the status the client saw; want: the fields the line must hold.
        wrong = RouterClient(proc.port, "tf-wrong-token-" + secrets.token_hex(16))

        def post_json(c, path, obj):
            return lambda: c.post(path=path, obj=obj, headers=xkey)[0]

        def post_stream():
            conn, resp, why = open_stream(client, sbody, headers=xkey)
            if conn is None:
                problems.extend("stream: %s" % w for w in why)
                return None
            try:
                read_stream(resp)
            finally:
                close_stream(conn, resp)
            return 200

        plan = (
            ("json", post_json(client, None, body),
             {"endpoint": "'messages'", "route": repr(GJ_J14_ROUTE), "kind": "'passthrough'",
              "stream": "0", "up_status": "200", "in": str(size), "end": "'json'"}),
            ("stream", post_stream,
             {"endpoint": "'messages'", "route": repr(GJ_J14_ROUTE), "stream": "1",
              "up_status": "200", "in": str(ssize), "end": "'message_stop'"}),
            ("count_tokens", post_json(client, "/v1/messages/count_tokens", body),
             {"endpoint": "'count_tokens'", "route": repr(GJ_J14_ROUTE), "in": str(size), "end": "'json'"}),
            ("bad bearer", post_json(wrong, None, body),
             {"endpoint": "'-'", "up_status": "0", "in": "0", "up_ms": "0", "end": "'json'"}),
            ("unknown path", post_json(client, "/v1/tf-j14-nope", body),
             {"endpoint": "'-'", "up_status": "0", "in": "0", "up_ms": "0", "end": "'json'"}),
        )
        assert len(plan) == GJ_J14_COUNT
        statuses = []
        for i, (label, send, want) in enumerate(plan):
            status = send()
            statuses.append(status)
            deadline = time.monotonic() + GJ_J14_LINE_S
            while len(gj_req_lines(logpath)) <= i and time.monotonic() < deadline:
                time.sleep(0.05)
            lines = gj_req_lines(logpath)
            if len(lines) != i + 1:
                problems.append("%s: %d req line(s) after request %d, expected %d"
                                % (label, len(lines), i + 1, i + 1))
                continue
            fields = GJ_J14_FIELD_RE.findall(lines[i][len("req "):])
            keys = [k for k, _v in fields]
            values = dict(fields)
            if [k for k in keys if k not in GJ_J14_OPTIONAL] != list(GJ_J14_KEYS) \
                    or len(keys) != len(set(keys)):
                problems.append("%s: key set %s, expected %s (+ optional %s)"
                                % (label, keys, list(GJ_J14_KEYS), list(GJ_J14_OPTIONAL)))
            if values.get("end", "").strip("'") not in GJ_J14_ENDS:
                problems.append("%s: end=%s is not one of %s" % (label, values.get("end"), GJ_J14_ENDS))
            if status is not None and values.get("status") != str(status):
                problems.append("%s: status=%s, the client saw %s" % (label, values.get("status"), status))
            for key, value in want.items():
                if values.get(key) != value:
                    problems.append("%s: %s=%s, expected %s" % (label, key, values.get(key), value))
            for key in ("ms", "up_ms", "out"):
                if not (values.get(key) or "").isdigit():
                    problems.append("%s: %s=%s is not a non-negative integer" % (label, key, values.get(key)))
        code = proc.close()
        lines = gj_req_lines(logpath)
        if len(lines) != GJ_J14_COUNT:
            problems.append("%d req line(s) for %d requests after shutdown (exit %r)"
                            % (len(lines), GJ_J14_COUNT, code))
        for label, needle in gj_sweep_needles() + [("a body byte", "tf j14 body")]:
            hit = [i for i, line in enumerate(lines) if needle in line]
            if hit:
                problems.append("req line(s) %s carry %s" % (hit, label))
        detail = ["requests    : %s -> status %s" % (", ".join(p[0] for p in plan), statuses),
                  "log         : %d req line(s); keys of the first: %s"
                  % (len(lines), " ".join(k for k, _v in GJ_J14_FIELD_RE.findall(lines[0][4:]))
                     if lines else "-")]
        detail += ["line        : %s" % line[:160] for line in lines]
    finally:
        if proc is not None:
            proc.close()
        peer.close()
    return problems, detail


def group_j_hygiene(suite, fixture_root, pyc_before, tree_before):
    """J5, J22, J20, J14, J11, then J9/J10 (last: every live case has written by now)."""
    results = {}

    def guarded(cid, fn):
        try:
            results[cid] = fn()
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            results[cid] = (["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], [])

    j5, j22, j20, j14, j11, j9, j10 = GJ_HYGIENE_CASES

    def case_j5():
        files = gj_sweep_files(FIXTURES)
        return gj_sweep(FIXTURES), ["swept       : %d file(s) under %s"
                                    % (len(files), os.path.relpath(FIXTURES, H.REPO_ROOT)),
                                    "needles     : %d TF_* value/stem form(s), %d key pattern(s)"
                                    % (len(gj_sweep_needles()), len(GJ_SWEEP_PATTERNS))]

    def case_j20():
        with open(SERVER, "r", encoding="utf-8") as fh:
            copy_source = fh.read()
        with open(CHROME, "r", encoding="utf-8") as fh:
            origin_source = fh.read()
        return rule_j20(copy_source, origin_source), \
            ["copies      : %s" % ", ".join(GJ_HAND_COPIES),
             "compared    : ast.dump of %s vs %s (docstrings in, comments and line numbers out)"
             % (os.path.relpath(SERVER, H.REPO_ROOT), os.path.relpath(CHROME, H.REPO_ROOT))]

    def case_j11():
        # Copied from tests/test_mcp_proxy.py L4 with SERVER = the router: an
        # uncut value of non-printables whose repr() overruns the 4 x width cap
        # ends in "...", and a short printable str is its repr() exactly.
        problems = []
        mod = H.load_module_from_path("ph_llm_router_j11", SERVER)
        width = mod._LOG_VALUE_WIDTH
        wide = mod._log_value("\U0010ffff" * width)
        if not wide.endswith("..."):
            problems.append("%d x U+10FFFF: no '...' marker on a capped repr (%d chars)" % (width, len(wide)))
        if len(wide) > 4 * width + 3:
            problems.append("%d x U+10FFFF: %d chars, cap %d" % (width, len(wide), 4 * width + 3))
        short = mod._log_value("messages")
        if short != repr("messages"):
            problems.append("short str logged as %r, expected %r" % (short, repr("messages")))
        return problems, ["capped      : %d x U+10FFFF -> %d chars, ends %r" % (width, len(wide), wide[-3:]),
                          "short       : %s" % short]

    guarded(j5, case_j5)
    guarded(j22, lambda: gj_sweep_control(gj_sweep, new_sandbox(fixture_root, "j22")))
    guarded(j20, case_j20)
    guarded(j14, lambda: gj_j14(fixture_root))
    guarded(j11, case_j11)

    # J9 / J10 -- copied from tests/test_mcp_proxy.py group_l (L1-L3).
    def case_j9():
        pyc_after = H.pycache_snapshot()
        new = sorted(set(pyc_after) - set(pyc_before))
        touched = sorted(k for k in set(pyc_after) & set(pyc_before) if pyc_after[k] != pyc_before[k])
        return ([] if not (new or touched) else ["new=%r touched=%r" % (new, touched)],
                ["pyc before=%d after=%d" % (len(pyc_before), len(pyc_after)),
                 "note        : the router runs with -B and the suite sets dont_write_bytecode"])

    def case_j10():
        problems = []
        stray = [p for p in WRITES
                 if not os.path.abspath(p).startswith(os.path.abspath(FIXTURE_BASE) + os.sep)]
        if stray:
            problems.append("%d write(s) outside the sandbox: %s" % (len(stray), ", ".join(stray)))
        added = sorted(H.repo_tree() - tree_before)
        if added:
            problems.append("%d new path(s): %s" % (len(added), added[:5]))
        return problems, ["sandbox     : %s/run-<unique>" % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                          "writes      : %d" % len(WRITES),
                          "note        : the scratch area is excluded by _harness.repo_tree, so the "
                          "fixtures are invisible here by construction, not by luck"]

    guarded(j9, case_j9)
    guarded(j10, case_j10)
    for cid in GJ_HYGIENE_CASES:
        problems, detail = results.get(cid, (["case did not run"], []))
        suite.record(GJ, cid, problems, detail=detail)


# ---------------------------------------------------------------------------

# The groups in run order.  A group whose step has not landed yet is absent
# from this module and is simply not called.
GROUP_ORDER = ("group_a", "group_b", "group_c", "group_d", "group_e",
               "group_f", "group_g", "group_h", "group_i", "group_j")


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="llm-router routes by model and translates at the edge: "
                          "config, front, outbound, three backend kinds, "
                          "timeouts and secret hygiene against scripted peers",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]
    del LOG_PATHS[:]
    del RESPONSE_BODIES[:]

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    WRITES.append(fixture_root)
    try:
        for name in GROUP_ORDER:
            group = globals().get(name)
            if group is None:
                continue
            if name == "group_j":
                group(suite, fixture_root, pyc_before, tree_before)
            else:
                group(suite, fixture_root)
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
