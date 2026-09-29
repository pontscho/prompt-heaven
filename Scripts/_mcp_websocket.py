#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the stdlib WebSocket client (RFC 6455, client side, ws:// only).

The domain is ONE question: how a client speaks the WebSocket wire -- the HTTP
upgrade that opens it, the frames that carry it, the fragments a message may
arrive in, the control frames it must answer, and the ceilings that stop a peer
from making it allocate without bound. It is a sixth source and not a corner of
an existing one because none of the five can hold it without becoming a shelf:
`_mcp_lsp.py` speaks `Content-Length` framing over stdio, `_mcp_json.py` speaks
JSON-RPC envelopes, and a WebSocket frame is neither. The rule that decided it
is ADR 0014's, and the decision itself is ADR 0023.

**Two hosts, and one of them is not an MCP server.** `Scripts/mcp-gdc.py` drives
Chrome's DevTools Protocol from an asyncio loop; `Scripts/search_duckduckgo.py`
drives the same protocol from a synchronous script for its opt-in `cdp` backend,
where it used to need the third-party `websocket-client` package. The second host
is named in `Scripts/amalgamate.py:DECLARED_HOSTS`, by hand, for the reason the
source registry is written by hand: a file must not become a generation target
by merely existing.

**Sans-IO core, two thin wrappers.** Everything that decides anything is a pure
function over bytes: `_ws_handshake_request` / `_ws_handshake_split` /
`_ws_handshake_verify` for the upgrade, `_ws_encode_frame` / `_ws_parse_frame` /
`_ws_mask` for the frames, `_ws_assemble` / `_ws_control_reply` / `_ws_step` for
messages. The two wrappers only move bytes between that core and a transport:
`_ws_connect` / `_ws_recv` / `_ws_send` over asyncio streams (gdc's names, kept),
and `_ws_sync_connect` / `_ws_sync_recv` / `_ws_sync_send` / `_ws_sync_close`
over a blocking socket. A host takes the core plus the wrapper it uses, all on
ONE marker, because `host_provides` offers a region only the host's imports and
never a name another region defines -- so every block that calls another block
must be co-listed with it, dependency first.

**What the core refuses**, each as `WebSocketError` -- a `ConnectionError`, so a
host that already treats a dead link as an `OSError` or catches `Exception`
keeps doing so without a new clause:

* a handshake whose status line is not exactly `HTTP/1.1 101`, whose `Upgrade`
  is not `websocket`, whose `Connection` carries no `upgrade` token, whose
  `Sec-WebSocket-Accept` is not the EXACT expected value (the client this
  replaced accepted the key as a substring of the whole response), or which
  negotiates an extension or subprotocol nobody offered;
* a header block larger than `WS_MAX_HANDSHAKE_BYTES`;
* a frame with a reserved bit set, an unknown opcode, or a MASK bit -- a server
  must never mask;
* a control frame over 125 bytes or without FIN;
* a continuation with no message open, or a new data frame inside one;
* a frame over `WS_MAX_FRAME_BYTES` (refused from its HEADER, before a payload
  byte is read) or a message over `WS_MAX_MESSAGE_BYTES`;
* a text message that is not valid UTF-8;
* a close frame with a one-byte payload, a reserved code, or a reason that is
  not UTF-8.

What it does instead of refusing: a ping is answered with a pong carrying the
same payload, a pong is dropped, a close is answered with a close echoing the
status code and the reader returns None, and a fragmented message is assembled
-- all iteratively, so a peer streaming control frames cannot grow the stack.

**Out of scope, deliberately:** `wss://` (TLS -- refused by name, never silently
downgraded), extensions including permessage-deflate (never offered, so a
server that negotiates one is refused), subprotocols, a client-initiated ping,
and the minimal-length-encoding rule for the 16- and 64-bit length forms (a
peer that uses a longer form than it needs is accepted).

**Tab safety is a constraint on how this file is WRITTEN.** One host indents
with tabs, and `Scripts/amalgamate.py:block_is_tab_safe` refuses a block that
joins a line inside an open bracket or indents by anything but whole 4-space
levels. So every call and literal here fits one physical line, and the request
is built by appending rather than as one bracketed list.

**Block contract.** A block reads only builtins, the stdlib names this module
imports (`asyncio`, `base64`, `hashlib`, `os`, `socket`), its own arguments and
the blocks co-listed on the same marker. No annotation names a `typing` symbol,
so no host has to import one for a block's sake.

**No server imports this module**, for the reasons `_mcp_json.py` gives. The test
fleet does: `tests/test_mcp_websocket.py` loads it and exercises the core, and
drives both hosts' generated copies against a loopback peer.
"""

import asyncio
import base64
import hashlib
import os
import socket


class WebSocketError(ConnectionError):
    """A WebSocket protocol violation or a failed handshake.

    A `ConnectionError` on purpose: to every caller that already treats a dead
    link as an `OSError`, a peer that broke the protocol is the same event.
    """


# The upgrade response is a status line and a handful of headers -- Chrome's is
# under 200 bytes. 64 KiB is three orders of magnitude of headroom and still a
# ceiling: a peer that streams header bytes and never ends the block is refused
# here instead of being buffered for as long as it cares to keep sending.
WS_MAX_HANDSHAKE_BYTES = 64 * 1024


# The largest single frame accepted, refused from the frame HEADER, before any
# of its payload is read -- a 64-bit length field can announce 2**63 bytes, and
# the client this replaced would have tried to read them.
#
# The number is argued from the one peer both hosts talk to. CDP answers are
# single unfragmented text frames, and the largest ones are base64 screenshots
# and captured response bodies: a full-page capture of a long page at a high
# device-pixel ratio is tens of megabytes of PNG, a third more once base64'd.
# Chromium's DevTools HTTP handler sizes its own send buffer at 256 MiB (recalled
# from `devtools_http_handler.cc`, not re-measured for this change), so no
# message Chrome can send is larger -- and a cap at the sender's own ceiling
# refuses nothing a real browser produces while still bounding a hostile or
# broken peer. The frame cap equals the message cap because Chrome does not
# fragment: a smaller frame cap would refuse a message the message cap admits.
WS_MAX_FRAME_BYTES = 256 * 1024 * 1024


# The largest assembled message, counted across all of its fragments. Same
# number and same argument as the frame cap above; it is a separate constant
# because it bounds a different thing -- a peer can stay under the frame cap
# with every fragment and still grow one message without end.
WS_MAX_MESSAGE_BYTES = 256 * 1024 * 1024


def _ws_parse_url(url: str) -> tuple:
    """Split a ``ws://host[:port]/path`` URL into ``(host, port, path)``.

    ``wss://`` is refused by name rather than dialled in clear text on port 80,
    which is what the hand parser this replaced did with it. A bracketed IPv6
    literal loses its brackets here and regains them in the ``Host`` header. The
    query string stays on the path, where the request line needs it; a fragment
    is dropped, since it never goes on the wire.
    """
    if not url.startswith("ws://"):
        scheme = url.split("://", 1)[0] if "://" in url else url[:16]
        raise WebSocketError("only ws:// URLs are supported, not %r" % scheme)
    rest = url[5:].split("#", 1)[0]
    cut = len(rest)
    for mark in "/?":
        at = rest.find(mark)
        if 0 <= at < cut:
            cut = at
    authority, path = rest[:cut], rest[cut:]
    if not path.startswith("/"):
        path = "/" + path
    if "@" in authority:
        raise WebSocketError("a ws:// URL with user information is refused")
    port = ""
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0:
            raise WebSocketError("unterminated IPv6 literal in %r" % authority)
        host, tail = authority[1:close], authority[close + 1:]
        if tail:
            if not tail.startswith(":"):
                raise WebSocketError("junk after the IPv6 literal in %r" % authority)
            port = tail[1:]
    elif ":" in authority:
        host, port = authority.rsplit(":", 1)
    else:
        host = authority
    if not host:
        raise WebSocketError("a ws:// URL needs a host")
    if not port:
        return host, 80, path
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise WebSocketError("invalid port %r" % port)
    return host, int(port), path


def _ws_handshake_request(host: str, port: int, path: str) -> tuple:
    """The HTTP upgrade request and the random key it carries: ``(bytes, str)``.

    No ``Origin`` header is sent. Chrome refuses a DevTools WebSocket whose
    Origin is not on its ``--remote-allow-origins`` list, and a client that
    sends none is not a browser page -- which is what ``websocket-client``'s
    ``suppress_origin=True`` bought the search script, and why this never
    grew an option to send one.

    A host, or a path, carrying whitespace or a control character is refused:
    either would let a URL write its own header lines into the request.
    """
    for part in (host, path):
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in part):
            raise WebSocketError("refusing a host or path with whitespace or control characters")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    authority = "[%s]:%d" % (host, port) if ":" in host else "%s:%d" % (host, port)
    lines = ["GET %s HTTP/1.1" % path]
    lines.append("Host: %s" % authority)
    lines.append("Upgrade: websocket")
    lines.append("Connection: Upgrade")
    lines.append("Sec-WebSocket-Key: %s" % key)
    lines.append("Sec-WebSocket-Version: 13")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), key


def _ws_handshake_split(buf) -> int:
    """Where the upgrade response's header block ends in *buf*, or -1 for "read more".

    The returned offset is one past the blank line, so ``buf[:end]`` is the
    header block and ``buf[end:]`` is the first frame bytes if the server sent
    any in the same segment -- which the client this replaced read into its
    header buffer and threw away.
    """
    end = bytes(buf[:WS_MAX_HANDSHAKE_BYTES + 4]).find(b"\r\n\r\n")
    if 0 <= end and end + 4 <= WS_MAX_HANDSHAKE_BYTES:
        return end + 4
    if end < 0 and len(buf) <= WS_MAX_HANDSHAKE_BYTES:
        return -1
    raise WebSocketError("WebSocket handshake failed: the header block exceeds %d bytes" % WS_MAX_HANDSHAKE_BYTES)


def _ws_handshake_verify(head: bytes, key: str) -> dict:
    """Check the upgrade response against RFC 6455 4.1; return its headers.

    Header names are folded to lower case and a repeated header is joined with
    ``", "``, so every check below reads ONE value and compares it EXACTLY --
    the client this replaced looked for ``101`` anywhere in the status line and
    for the accept key anywhere in the response.
    """
    lines = head.decode("latin-1").split("\r\n")
    status = lines[0].split(" ", 2)
    if len(status) < 2 or status[0] != "HTTP/1.1" or status[1] != "101":
        raise WebSocketError("WebSocket handshake rejected: %r" % lines[0][:200])
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip() or line[0] in " \t":
            raise WebSocketError("WebSocket handshake failed: malformed header line %r" % line[:200])
        name = name.lower()
        value = value.strip(" \t")
        headers[name] = headers[name] + ", " + value if name in headers else value
    if headers.get("upgrade", "").lower() != "websocket":
        raise WebSocketError("WebSocket handshake failed: Upgrade is not websocket")
    if "upgrade" not in [token.strip().lower() for token in headers.get("connection", "").split(",")]:
        raise WebSocketError("WebSocket handshake failed: Connection carries no upgrade token")
    digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    if headers.get("sec-websocket-accept") != base64.b64encode(digest).decode("ascii"):
        raise WebSocketError("WebSocket handshake failed: invalid accept key")
    if "sec-websocket-extensions" in headers or "sec-websocket-protocol" in headers:
        raise WebSocketError("WebSocket handshake failed: the server negotiated an extension or subprotocol nobody offered")
    return headers


def _ws_mask(key: bytes, data: bytes) -> bytes:
    """XOR *data* with the 4-byte *key* repeated -- masking and unmasking alike.

    One big-integer XOR over the whole payload instead of a Python-level loop
    over its bytes: the loop this replaced cost a generator step per byte, which
    on a multi-megabyte frame is the whole of the send time.
    """
    size = len(data)
    if not size:
        return b""
    pad = (bytes(key) * (size // 4 + 1))[:size]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(size, "big")


def _ws_encode_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """One client frame: FIN/opcode, the shortest length form, a fresh mask key.

    Always masked -- RFC 6455 5.1 requires it of every client frame, and a
    server is obliged to drop the connection on an unmasked one.
    """
    if opcode not in (0x0, 0x1, 0x2, 0x8, 0x9, 0xA):
        raise WebSocketError("unknown opcode 0x%X" % opcode)
    size = len(payload)
    if opcode >= 0x8 and (size > 125 or not fin):
        raise WebSocketError("a control frame must be FIN and at most 125 bytes")
    head = bytearray([(0x80 if fin else 0x00) | opcode])
    if size < 126:
        head.append(0x80 | size)
    elif size < 65536:
        head.append(0x80 | 126)
        head += size.to_bytes(2, "big")
    else:
        head.append(0x80 | 127)
        head += size.to_bytes(8, "big")
    key = os.urandom(4)
    return bytes(head) + key + _ws_mask(key, payload)


def _ws_parse_frame(buf):
    """The first complete server frame in *buf*: ``(fin, opcode, payload, used)``.

    Returns None while *buf* does not yet hold the whole frame; *used* is how
    many bytes of *buf* the frame took. Every refusal that can be made from
    the header is made from the header, before the payload arrives, so an
    announced 2**63-byte frame costs ten bytes of reading and not an attempt.
    """
    if len(buf) < 2:
        return None
    first, second = buf[0], buf[1]
    if first & 0x70:
        raise WebSocketError("reserved bits set on a frame, and no extension was negotiated")
    opcode = first & 0x0F
    if opcode not in (0x0, 0x1, 0x2, 0x8, 0x9, 0xA):
        raise WebSocketError("unknown opcode 0x%X" % opcode)
    fin = bool(first & 0x80)
    if second & 0x80:
        raise WebSocketError("the server sent a masked frame")
    size = second & 0x7F
    offset = 2
    if size == 126:
        if len(buf) < 4:
            return None
        size, offset = int.from_bytes(bytes(buf[2:4]), "big"), 4
    elif size == 127:
        if len(buf) < 10:
            return None
        size, offset = int.from_bytes(bytes(buf[2:10]), "big"), 10
        if size >> 63:
            raise WebSocketError("the most significant bit of a 64-bit frame length is set")
    if opcode >= 0x8 and (size > 125 or not fin):
        raise WebSocketError("a control frame must be FIN and at most 125 bytes")
    if size > WS_MAX_FRAME_BYTES:
        raise WebSocketError("a %d-byte frame exceeds the %d-byte cap" % (size, WS_MAX_FRAME_BYTES))
    end = offset + size
    if len(buf) < end:
        return None
    return fin, opcode, bytes(buf[offset:end]), end


def _ws_assemble(pending: list, fin: bool, opcode: int, payload: bytes):
    """Fold one frame into *pending*; return ``(opcode, value)`` or None.

    *pending* is the caller's per-connection list: empty between messages, and
    ``[opcode, bytearray]`` while a fragmented one is open. A control frame is
    returned at once as ``(opcode, payload)`` -- RFC 6455 lets it arrive
    between two fragments, and it does not disturb the message. A data message
    is returned once its FIN fragment lands: text as ``str`` (strict UTF-8),
    binary as ``bytes``. None means "a fragment was absorbed, keep reading" --
    the client this replaced returned None for a continuation frame, which its
    caller read as the connection closing.
    """
    if opcode >= 0x8:
        return opcode, payload
    if opcode == 0x0:
        if not pending:
            raise WebSocketError("a continuation frame arrived with no message open")
    elif pending:
        raise WebSocketError("a new data frame arrived inside a fragmented message")
    else:
        pending[:] = [opcode, bytearray()]
    if len(pending[1]) + len(payload) > WS_MAX_MESSAGE_BYTES:
        raise WebSocketError("a message exceeds the %d-byte cap" % WS_MAX_MESSAGE_BYTES)
    pending[1] += payload
    if not fin:
        return None
    kind, data = pending[0], bytes(pending[1])
    del pending[:]
    if kind == 0x2:
        return kind, data
    try:
        return kind, data.decode("utf-8")
    except UnicodeDecodeError:
        raise WebSocketError("a text message is not valid UTF-8") from None


def _ws_control_reply(opcode: int, payload: bytes):
    """The frame a control frame obliges the client to send back, or None.

    A ping is answered with a pong carrying the SAME payload (RFC 6455 5.5.2);
    the client this replaced read the ping and answered nothing. A close is
    answered with a close echoing its status code, after the payload is
    checked: one byte is not a status code, the codes an endpoint must never
    send are refused, and so is a reason that is not UTF-8. A pong needs no
    answer.
    """
    if opcode == 0x9:
        return _ws_encode_frame(0xA, payload)
    if opcode != 0x8:
        return None
    if len(payload) == 1:
        raise WebSocketError("a close frame carried a one-byte payload")
    if not payload:
        return _ws_encode_frame(0x8, b"")
    code = int.from_bytes(payload[:2], "big")
    if code < 1000 or code in (1004, 1005, 1006, 1015):
        raise WebSocketError("a close frame carried the reserved status code %d" % code)
    try:
        payload[2:].decode("utf-8")
    except UnicodeDecodeError:
        raise WebSocketError("a close frame's reason is not valid UTF-8") from None
    return _ws_encode_frame(0x8, payload[:2])


class _WsConnection:
    """One open client connection: its transport and the parser state between reads.

    The asyncio wrapper sets *reader* and *writer*, the socket wrapper sets
    *sock*; the core never touches any of the three. *buf* holds bytes read
    but not yet parsed -- including any the server sent in the same segment as
    its handshake -- and *pending* is `_ws_assemble`'s open message.
    """

    def __init__(self, reader, writer, sock, timeout: float):
        self.reader = reader
        self.writer = writer
        self.sock = sock
        self.timeout = timeout
        self.buf = bytearray()
        self.pending = []


def _ws_step(conn):
    """Advance *conn* over what is buffered: ``(reply, message, closed)`` or None.

    None means the buffer holds no complete frame and the wrapper must read.
    Otherwise *reply* is a frame the wrapper must send first (or None),
    *message* is the next data message as ``str`` (or None), and *closed*
    says the peer sent a close. A binary message is decoded with replacement
    rather than refused, which is what the client this replaced returned for
    one -- CDP never sends binary, so this is compatibility, not a feature.

    A loop, not a recursion: the client this replaced called itself once per
    ping or pong, so a peer streaming control frames grew its stack.
    """
    while True:
        frame = _ws_parse_frame(conn.buf)
        if frame is None:
            return None
        fin, opcode, payload, used = frame
        del conn.buf[:used]
        event = _ws_assemble(conn.pending, fin, opcode, payload)
        if event is None:
            continue
        kind, value = event
        if kind == 0x1:
            return None, value, False
        if kind == 0x2:
            return None, value.decode("utf-8", "replace"), False
        reply = _ws_control_reply(kind, value)
        if reply is not None or kind == 0x8:
            return reply, None, kind == 0x8


async def _ws_connect(host: str, port: int, path: str, timeout: float = 10.0):
    """Open a WebSocket over asyncio streams; return the `_WsConnection`.

    The TCP connect is bounded by *timeout*, and so is the upgrade -- the
    request's drain and the response's header block share one deadline -- so
    a stalled or half-open link to Chrome fails fast instead of hanging the
    handler until the MCP client gives up with a bare "Connection closed".
    The same *timeout* later bounds the drain of every reply `_ws_recv`
    sends. On any failure the stream is closed before the error propagates.
    """
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    try:
        request, key = _ws_handshake_request(host, port, path)
        conn = _WsConnection(reader, writer, None, timeout)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        end = -1
        while end < 0:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=max(0.0, deadline - loop.time()))
            if not chunk:
                raise WebSocketError("WebSocket handshake failed: connection closed")
            conn.buf += chunk
            end = _ws_handshake_split(conn.buf)
        _ws_handshake_verify(bytes(conn.buf[:end]), key)
        del conn.buf[:end]
    except BaseException:
        writer.close()
        raise
    return conn


async def _ws_recv(conn):
    """The next data message as text; None once the peer has closed.

    Pings are answered and pongs dropped on the way, and a fragmented message
    is returned whole. A link that ends without a close frame raises
    `WebSocketError` rather than returning None: that is an abnormal closure,
    and it is reported as one.
    """
    while True:
        step = _ws_step(conn)
        if step is None:
            chunk = await conn.reader.read(65536)
            if not chunk:
                raise WebSocketError("the connection ended without a close frame")
            conn.buf += chunk
            continue
        reply, message, closed = step
        if reply is not None:
            conn.writer.write(reply)
            await asyncio.wait_for(conn.writer.drain(), timeout=conn.timeout)
        if closed:
            return None
        if message is not None:
            return message


async def _ws_send(conn, text: str) -> None:
    """Send *text* as one masked text frame.

    Unbounded on purpose, as before: the caller wraps it in its own timeout.
    One `write` per frame, so a pong `_ws_recv` sends between two calls can
    never land inside this frame's bytes.
    """
    conn.writer.write(_ws_encode_frame(0x1, text.encode("utf-8")))
    await conn.writer.drain()


def _ws_sync_connect(url: str, timeout: float = 30.0):
    """Open a WebSocket to a ``ws://`` URL over a blocking socket.

    *timeout* is the socket's own, so it bounds the connect and every later
    read and write separately -- the semantics of ``websocket-client``'s
    ``create_connection(timeout=...)``, which this replaced.
    """
    host, port, path = _ws_parse_url(url)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        request, key = _ws_handshake_request(host, port, path)
        conn = _WsConnection(None, None, sock, timeout)
        sock.sendall(request)
        end = -1
        while end < 0:
            chunk = sock.recv(65536)
            if not chunk:
                raise WebSocketError("WebSocket handshake failed: connection closed")
            conn.buf += chunk
            end = _ws_handshake_split(conn.buf)
        _ws_handshake_verify(bytes(conn.buf[:end]), key)
        del conn.buf[:end]
    except BaseException:
        sock.close()
        raise
    return conn


def _ws_sync_recv(conn):
    """The next data message as text; None once the peer has closed."""
    while True:
        step = _ws_step(conn)
        if step is None:
            chunk = conn.sock.recv(65536)
            if not chunk:
                raise WebSocketError("the connection ended without a close frame")
            conn.buf += chunk
            continue
        reply, message, closed = step
        if reply is not None:
            conn.sock.sendall(reply)
        if closed:
            return None
        if message is not None:
            return message


def _ws_sync_send(conn, text: str) -> None:
    """Send *text* as one masked text frame."""
    conn.sock.sendall(_ws_encode_frame(0x1, text.encode("utf-8")))


def _ws_sync_close(conn) -> None:
    """Send a normal-closure frame, best effort, and close the socket.

    The peer's answering close is not waited for: the caller is done with the
    connection, and a peer that never answers must not keep it open.
    """
    try:
        conn.sock.sendall(_ws_encode_frame(0x8, (1000).to_bytes(2, "big")))
    except OSError:
        pass
    conn.sock.close()
