#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the stdlib HTTP/1.1 server front.

The domain is ONE question: how a stdlib `http.server` listener takes a
connection and gets it through the header phase safely -- the server side of
HTTP/1.1 on the stdlib, up to the end of the header phase, plus the two
process-level pieces every such listener carries beside it: the ready file a
supervisor waits on, and the bearer token value the listener compares against.
It is a source of its own and not a corner of an existing one because none of
them can hold it without becoming a shelf: `_mcp_chrome.py` is the CLIENT side
of the wire, `_mcp_json.py` answers what a JSON-RPC frame is, and neither says
when a server stops reading headers (ADR 0014: the protocol is the domain).

**Two hosts.** `Scripts/mcp-proxy.py` (its `--http` Streamable HTTP front) and
`Scripts/llm-router.py` (its Anthropic Messages front) take every block here.
The blocks were byte-identical twins in both hosts before they were lifted
(R-0072), so the lift moved no behaviour.

**What stays host-side, and why.** `_refuse` and `send_error`'s table (each
host answers a refusal in its own envelope), `_precheck`, the request framing
and the relay (each host's own protocol), `log_message` (each masks a
different set of client text), `setup` (it picks the reader's cap),
`handle_one_request` (the router has a pre-auth header bound the proxy does
not), and `process_request_thread`. That last one MUST release `conn_sem` in a
`finally`: the generated `process_request` acquires the slot, and only the
host's thread knows when the connection is over (the router counts its drain
around it). tests/test_generated_region.py GI1 pins the finally.

**Generation copies code, not policy.** Every value a host decides reaches a
block through a seam, never through a host global: `_HeaderDeadlineReader`'s
`cap`, `_http_token_value`'s `min_len` and `error`, the ready-file pair's
`error` and `logger`, `parse_request`'s `self.timeout` (the stdlib's own
attribute, which the router's test rig patches in step with the header bound),
and the server members' `self.front_log`, a class attribute bound to the
host's logger object. A future member that needs a constant the rig patches
gets a hand-written `_front_*` hook method in the host that reads the constant
at call time -- never a class attribute, which would snapshot it at class
definition (PD-7).

**The `super()` note.** `server_bind`, `process_request`, `handle_error`,
`parse_request`, `handle_expect_100` and `_single_header` are top-level defs
here and METHODS in the hosts: the generator re-indents each to its marker's
column inside the host's server or handler class. Their zero-argument
`super()` works only once rendered inside a class, so this file is a TEXT
source for them; group E of tests/test_generated_region.py drives them
rendered inside a synthetic class.

**The block contract.** A block references only builtins, the stdlib names
imported below and its own arguments; no block names another block, so each
region refreshes on its own.

No server imports this module.
"""

import io
import json
import os
import socket
import socketserver
import sys
import time
from typing import Optional


# --- refusal headers --------------------------------------------------------

_HTTP_STDLIB_REFUSAL_HEADERS = (("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"))


# --- token -------------------------------------------------------------------

def _http_token_value(value: str, where: str, min_len: int, error) -> bytes:
    """Validate the bearer token and return it as ASCII bytes; refusals never include it.

    *min_len* and the *error* class raised are the host's policy, passed in."""
    if len(value) < min_len:
        raise error(f"{where}: the bearer token must be at least {min_len} characters")
    if not all(0x21 <= ord(ch) <= 0x7E for ch in value):
        raise error(f"{where}: the bearer token must be printable ASCII with no whitespace")
    return value.encode("ascii")


# --- ready file --------------------------------------------------------------

def _http_write_ready_file(path: str, port: int, error) -> None:
    """{"port", "pid"} written 0600 and atomically (.tmp + os.replace); a failure
    raises the host's *error* class naming the strerror or the type only."""
    tmp = path + ".tmp"
    try:
        if os.path.lexists(tmp):
            os.remove(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "pid": os.getpid()}, fh)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.remove(tmp)
    except OSError as exc:
        raise error(f"cannot write --ready-file: {exc.strerror or type(exc).__name__}") from None


def _http_remove_ready_file(path: str, logger) -> None:
    """On an ordered shutdown, unlink the ready file only while it still holds
    this process's pid (mcp-proxy's R-0075, the router's V24).

    Read without following a symlink (O_NOFOLLOW: a link at the path is
    refused, so neither it nor its target is touched); a file another writer
    has replaced (another pid, not JSON, unreadable) is left alone. Best
    effort: never raises; a refusal is logged on the host's *logger* at DEBUG
    by type only.

    Declared limits: a replace between the read and the unlink is not seen
    (the new file is removed); SIGKILL, or any exit that skips the ordered
    shutdown, leaves the file behind with a dead pid.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read(4096))
        pid = data.get("pid") if isinstance(data, dict) else None
        if type(pid) is int and pid == os.getpid():
            os.remove(path)
        else:
            logger.debug("ready file left: not this pid")
    except (OSError, ValueError) as exc:
        logger.debug("ready file left: %s", type(exc).__name__)


# --- header deadline ---------------------------------------------------------

class _HeaderDeadlineReader(io.RawIOBase):
    """The raw reader under a handler's rfile: enforces a TOTAL header deadline.

    While `deadline` (a time.monotonic() value) is set, every recv gets at most
    the time left until it, never more than *cap* seconds, and none is
    attempted once it has passed: a client that trickles one header byte per
    recv can no longer hold its --max-connections slot past the host's header
    bound in total (F8). The socket.timeout raised is the one the stdlib's
    handle_one_request already answers by closing the connection. A host may
    set `deadline` again around its body read (the router's _post does, V4).
    With `deadline` None it is a plain recv under the socket's own timeout.
    """

    def __init__(self, sock: socket.socket, cap: float) -> None:
        super().__init__()
        self._sock = sock
        self._cap = cap
        self.deadline: Optional[float] = None

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("header deadline passed")
            self._sock.settimeout(min(left, self._cap))
        return self._sock.recv_into(buf)


# --- server members (rendered INSIDE the host's ThreadingHTTPServer subclass) --

def server_bind(self) -> None:
    # The stdlib HTTPServer.server_bind does a reverse-DNS getfqdn() the
    # host never uses and that can stall startup (I1).
    socketserver.TCPServer.server_bind(self)
    self.server_name, self.server_port = self.server_address[:2]


def process_request(self, request, client_address) -> None:
    # The slot taken here is given back by the host's hand-written
    # process_request_thread, in a finally (tests/test_generated_region.py GI1).
    if not self.conn_sem.acquire(blocking=False):
        self.front_log.warning("http connection refused: %d connections open", self.settings.max_connections)
        try:
            request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
        self.shutdown_request(request)
        return
    try:
        super().process_request(request, client_address)
    except BaseException:
        # The handler thread did not start: give its slot back. The stdlib
        # caller then runs handle_error and shutdown_request.
        self.conn_sem.release()
        raise


def handle_error(self, request, client_address) -> None:
    # Type only: the stdlib traceback's message text can quote request data (ADR 0011).
    self.front_log.warning("http handler failed: %s", type(sys.exc_info()[1]).__name__)


# --- handler members (rendered INSIDE the host's BaseHTTPRequestHandler subclass)

def parse_request(self) -> bool:
    # The header phase ends here, before any do_* runs: the body read and
    # the SSE stream are never under the header deadline. The per-recv
    # timeout becomes the handler's class-level `timeout` (the header
    # bound) until the host's precheck passes -- longer than the router's
    # pre-auth header bound. Harmless: no socket read happens between
    # here and the precheck verdict (the precheck reads only the parsed
    # request line and headers), so the only socket operation under it
    # before authentication is a refusal's own write, the first on the
    # connection (measured, R-0091).
    try:
        return super().parse_request()
    finally:
        self._header_reader.deadline = None
        self.connection.settimeout(self.timeout)


def handle_expect_100(self) -> bool:
    # Called from parse_request, before any auth: send nothing (mcp-proxy's
    # R-0076, the router's V37). The host's POST handler sends the 100
    # Continue itself once its precheck and framing checks pass.
    return True


def _single_header(self, name: str):
    """(ok, value) of a header that must appear at most once (ADR 0015:
    ambiguity is the defect): ok False when it is repeated, value None when absent."""
    values = self.headers.get_all(name) or []
    if len(values) > 1:
        return False, None
    return True, (values[0] if values else None)
