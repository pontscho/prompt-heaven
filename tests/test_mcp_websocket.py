#!/usr/bin/env python3
"""The stdlib WebSocket client -- `Scripts/_mcp_websocket.py` and its two hosts.

`Scripts/_mcp_websocket.py` is the canonical source for the RFC 6455 client that
`Scripts/amalgamate.py` generates into `Scripts/mcp-gdc.py` (asyncio) and
`Scripts/search_duckduckgo.py` (blocking socket, the `cdp` backend). The drift
gate in `tests/test_generated_region.py` proves the copies MATCH the source;
this suite proves the source is RIGHT, and then drives both hosts' generated
copies end to end, because a correct core wired into a host the wrong way is
still a broken host.

THE ORACLE IS WRITTEN FROM THE RFC, NOT FROM THE MODULE. The server-side frame
encoder, the client-frame decoder, the naive per-byte mask and the accept-key
computation below are this file's own, and the handshake case pins RFC 6455's
published example key and accept value. A suite that built its expected frames
with the module's `_ws_encode_frame` would only prove the module agrees with
itself -- the failure mode the fleet's drift gate already warns about.

RED FIRST, WHERE THE BEHAVIOUR CHANGED. Group E was written before the hosts
were switched to the generated client, and run against the hand-written one it
replaced: the fragmented CDP reply, the ping that must be answered and the
masked server frame that must be refused were each measured failing there
(ADR 0023 records the run). The core groups have no "before" to be red
against -- the functions they call did not exist.

LOOPBACK ONLY. Groups D and E open sockets, but only to a peer this file starts
on 127.0.0.1 in a thread, or to a `socket.socketpair()`. No name is resolved,
nothing leaves the host, and nothing is written anywhere.

Groups:
  A. HANDSHAKE: the upgrade request, the exact-match response check, the URL
                parser, the header-block cap
  B. FRAMES:    encode and parse at every length form, the mask, and every
                refusal a frame header can earn
  C. MESSAGES:  fragments, control frames between them, ping/pong, close, the
                message cap and strict UTF-8
  D. WRAPPERS:  the asyncio and socket wrappers over in-memory and loopback
                transports, handshake failure and timeout included
  E. HOSTS:     gdc's CdpSession and the search script's CDPSearcher, driven
                against a loopback CDP peer
  F. HYGIENE:   no bytecode written

Usage:
  python3 tests/test_mcp_websocket.py            # standalone
  python3 tests/run.py mcp_websocket             # through the fleet runner
  python3 tests/test_mcp_websocket.py --brief    # one line per case

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every case passes.
"""

import asyncio
import base64
import hashlib
import json
import os
import random
import socket
import struct
import sys
import threading

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_websocket"

SOURCE = H.repo_path("Scripts", "_mcp_websocket.py")
GDC = H.repo_path("Scripts", "mcp-gdc.py")
DDG = H.repo_path("Scripts", "search_duckduckgo.py")

GA = "A. HANDSHAKE: request, exact-match response check, URL, header cap"
GB = "B. FRAMES: every length form, the mask, header-level refusals"
GC = "C. MESSAGES: fragments, ping/pong, close, message cap, strict UTF-8"
GD = "D. WRAPPERS: asyncio and socket wrappers, in memory and on loopback"
GE = "E. HOSTS: gdc CdpSession and search CDPSearcher against a loopback peer"
GF = "F. HYGIENE: no bytecode"

# RFC 6455 section 1.3 -- the GUID and the worked example, typed from the RFC.
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
RFC_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
RFC_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

LENGTHS = (0, 125, 126, 65535, 65536)


# --- the oracle ---------------------------------------------------------------

def oracle_accept(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def naive_mask(key, data):
    return bytes(b ^ key[i % 4] for i, b in enumerate(data))


def server_frame(opcode, payload=b"", fin=True, rsv=0, mask_key=None, size=None):
    """A frame as a SERVER would send it; *size* forges the announced length."""
    size = len(payload) if size is None else size
    first = (0x80 if fin else 0) | rsv | opcode
    mbit = 0x80 if mask_key else 0
    if size < 126:
        head = bytes([first, mbit | size])
    elif size < 65536:
        head = bytes([first, mbit | 126]) + struct.pack(">H", size)
    else:
        head = bytes([first, mbit | 127]) + struct.pack(">Q", size)
    if mask_key:
        return head + mask_key + naive_mask(mask_key, payload)
    return head + payload


def decode_client_frame(data):
    """(fin, opcode, masked, payload, used, header_len) or None if incomplete."""
    if len(data) < 2:
        return None
    fin, opcode = bool(data[0] & 0x80), data[0] & 0x0F
    masked, size, pos = bool(data[1] & 0x80), data[1] & 0x7F, 2
    if size == 126:
        if len(data) < 4:
            return None
        size, pos = struct.unpack(">H", data[2:4])[0], 4
    elif size == 127:
        if len(data) < 10:
            return None
        size, pos = struct.unpack(">Q", data[2:10])[0], 10
    key = b""
    if masked:
        if len(data) < pos + 4:
            return None
        key, pos = data[pos:pos + 4], pos + 4
    if len(data) < pos + size:
        return None
    payload = data[pos:pos + size]
    if masked:
        payload = naive_mask(key, payload)
    return fin, opcode, masked, payload, pos + size, pos


def decode_all(data):
    frames, data = [], bytes(data)
    while data:
        got = decode_client_frame(data)
        if got is None:
            break
        frames.append(got[:4])
        data = data[got[4]:]
    return frames


def upgrade_response(key, status="HTTP/1.1 101 Switching Protocols",
                     accept=None, extra=(), upgrade="websocket",
                     connection="Upgrade"):
    lines = [status]
    if upgrade is not None:
        lines.append("Upgrade: %s" % upgrade)
    if connection is not None:
        lines.append("Connection: %s" % connection)
    lines.append("Sec-WebSocket-Accept: %s"
                 % (oracle_accept(key) if accept is None else accept))
    lines.extend(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def raises(mod, fn):
    """The WebSocketError *fn* raised, or None. Any OTHER exception propagates."""
    try:
        fn()
    except mod.WebSocketError as exc:
        return exc
    return None


def problem_if(condition, message):
    return [message] if condition else []


class FakeWriter:
    """An asyncio StreamWriter stand-in that keeps what it was given."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, blob):
        self.data += blob

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


async def feed_and_recv(mod, blob, eof=True):
    reader = asyncio.StreamReader()
    reader.feed_data(blob)
    if eof:
        reader.feed_eof()
    writer = FakeWriter()
    conn = mod._WsConnection(reader, writer, None, 5.0)
    try:
        result = await mod._ws_recv(conn)
    except Exception as exc:        # the case inspects which one
        return exc, writer
    return result, writer


# --- the loopback peer --------------------------------------------------------

class Peer:
    """A one-connection WebSocket SERVER on 127.0.0.1, in a thread.

    Written against the RFC with this file's own oracle; *scenario* is called
    with the peer and the accepted socket once the request head is read, and
    decides everything after that -- including whether the upgrade is sent.
    """

    def __init__(self, scenario, timeout=5.0):
        self.scenario = scenario
        self.timeout = timeout
        self.request = b""
        self.buf = b""
        self.frames = []
        self.pongs = []
        self.error = None
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.srv.settimeout(timeout)
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def url(self, path="/devtools/page/T1"):
        return "ws://127.0.0.1:%d%s" % (self.port, path)

    def _serve(self):
        try:
            sock, _addr = self.srv.accept()
        except OSError as exc:
            self.error = "accept: %r" % exc
            self.srv.close()
            return
        try:
            sock.settimeout(self.timeout)
            while b"\r\n\r\n" not in self.buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise EOFError("client closed during the handshake")
                self.buf += chunk
            self.request, _sep, self.buf = self.buf.partition(b"\r\n\r\n")
            self.scenario(self, sock)
        except Exception as exc:
            self.error = repr(exc)
        finally:
            sock.close()
            self.srv.close()

    def header(self, name):
        for line in self.request.decode("latin-1").split("\r\n")[1:]:
            key, _sep, value = line.partition(":")
            if key.strip().lower() == name:
                return value.strip()
        return None

    def upgrade(self):
        return upgrade_response(self.header("sec-websocket-key") or "")

    def read_frame(self, sock):
        """The next client frame, or None on EOF or timeout."""
        while True:
            got = decode_client_frame(self.buf)
            if got is not None:
                self.buf = self.buf[got[4]:]
                self.frames.append(got[:4])
                if got[1] == 0xA:
                    self.pongs.append(got[3])
                return got[:4]
            try:
                chunk = sock.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self.buf += chunk

    def join(self):
        self.thread.join(self.timeout + 5)


def cdp_reply(style, msg):
    body = json.dumps({"id": msg["id"],
                       "result": {"echo": msg.get("method")}}).encode()
    if style == "fragmented":
        return (server_frame(0x1, body[:5], fin=False)
                + server_frame(0x9, b"mid")
                + server_frame(0x0, body[5:12], fin=False)
                + server_frame(0x0, body[12:]))
    if style == "masked":
        return server_frame(0x1, body, mask_key=b"\x01\x02\x03\x04")
    return server_frame(0x1, body)


def cdp_scenario(style, ping=None):
    """Answer every CDP command; with *ping*, hold every answer until it is ponged.

    The ping travels in the SAME segment as the 101, so a client that throws
    away the bytes after its header block never sees it.
    """
    def scenario(peer, sock):
        out = peer.upgrade()
        if ping is not None:
            out += server_frame(0x9, ping)
        sock.sendall(out)
        queued = []
        while True:
            frame = peer.read_frame(sock)
            if frame is None:
                return
            _fin, opcode, _masked, payload = frame
            if opcode == 0x8:
                sock.sendall(server_frame(0x8, payload[:2]))
                return
            if opcode == 0x1:
                queued.append(json.loads(payload.decode("utf-8")))
            if ping is not None and ping not in peer.pongs:
                continue
            for msg in queued:
                sock.sendall(cdp_reply(style, msg))
            queued = []
    return scenario


# --- A. handshake -------------------------------------------------------------

def group_handshake(suite, mod):
    request, key = mod._ws_handshake_request("127.0.0.1", 9222, "/devtools/page/X?a=1")
    _again, key2 = mod._ws_handshake_request("127.0.0.1", 9222, "/")
    text = request.decode("ascii")
    head, _sep, rest = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _s, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    problems = problem_if(lines[0] != "GET /devtools/page/X?a=1 HTTP/1.1",
                          "request line is %r" % lines[0])
    problems += problem_if(rest != "", "bytes after the header block: %r" % rest)
    wanted = {"host": "127.0.0.1:9222", "upgrade": "websocket",
              "connection": "Upgrade", "sec-websocket-version": "13",
              "sec-websocket-key": key}
    problems += ["header %s is %r, wanted %r" % (k, headers.get(k), v)
                 for k, v in sorted(wanted.items()) if headers.get(k) != v]
    problems += problem_if("origin" in headers,
                           "an Origin header was sent; Chrome refuses a "
                           "DevTools socket whose Origin is not allow-listed")
    try:
        raw = base64.b64decode(key, validate=True)
    except ValueError:
        raw = b""
    problems += problem_if(len(raw) != 16, "the key is not 16 base64'd bytes")
    problems += problem_if(key == key2, "two requests carried the same key")
    suite.record(GA, "request-shape", problems,
                 detail=["headers: %s" % ", ".join(sorted(headers))])

    request, _k = mod._ws_handshake_request("::1", 9222, "/")
    suite.record(GA, "request-ipv6-host-bracketed", problem_if(
        b"\r\nHost: [::1]:9222\r\n" not in request,
        "an IPv6 host was not bracketed in Host: %r" % request[:80]))

    bad = [("127.0.0.1", "/a\r\nX-Injected: 1"), ("127.0.0.1", "/a b"),
           ("evil\nhost", "/"), ("127.0.0.1", "/a\x00")]
    problems = ["%r %r was not refused" % pair for pair in bad
                if raises(mod, lambda p=pair: mod._ws_handshake_request(p[0], 80, p[1])) is None]
    suite.record(GA, "request-refuses-injection", problems)

    headers = mod._ws_handshake_verify(
        upgrade_response(RFC_KEY, upgrade="WebSocket",
                         connection="keep-alive, Upgrade")[:-4], RFC_KEY)
    problems = problem_if(oracle_accept(RFC_KEY) != RFC_ACCEPT,
                          "the test's own oracle disagrees with the RFC")
    problems += problem_if(headers.get("sec-websocket-accept") != RFC_ACCEPT,
                           "headers returned: %r" % headers)
    bare = b"HTTP/1.1 101\r\nUpgrade: websocket\r\nConnection: upgrade\r\n" \
           b"Sec-WebSocket-Accept: " + RFC_ACCEPT.encode() + b"\r\n"
    problems += problem_if(raises(mod, lambda: mod._ws_handshake_verify(bare, RFC_KEY)) is not None,
                           "a 101 with no reason phrase was refused")
    suite.record(GA, "verify-accepts-rfc-example", problems,
                 detail=["key %s -> accept %s" % (RFC_KEY, RFC_ACCEPT),
                         "mixed-case Upgrade value and a two-token Connection accepted"])

    other = oracle_accept("AAAAAAAAAAAAAAAAAAAAAA==")
    exc = raises(mod, lambda: mod._ws_handshake_verify(
        upgrade_response(RFC_KEY, accept=other), RFC_KEY))
    suite.record(GA, "verify-wrong-accept", problem_if(
        exc is None, "an accept value for a different key was taken"),
        detail=[str(exc)])

    # The client this replaced searched the WHOLE response for the expected
    # value, so both of these passed it.
    shapes = {
        "accept wrapped in junk": upgrade_response(RFC_KEY, accept="x%sx" % RFC_ACCEPT),
        "right value in another header": upgrade_response(
            RFC_KEY, accept=other, extra=("X-Echo: %s" % RFC_ACCEPT,)),
    }
    problems = ["%s: accepted" % what for what, blob in sorted(shapes.items())
                if raises(mod, lambda b=blob: mod._ws_handshake_verify(b, RFC_KEY)) is None]
    suite.record(GA, "verify-accept-is-exact-not-substring", problems)

    statuses = ["HTTP/1.1 200 OK", "HTTP/1.1 1010 Weird", "HTTP/1.0 101 Switching",
                "HTTP/1.1 301 Moved to /101", "garbage"]
    problems = ["%r: accepted" % status for status in statuses
                if raises(mod, lambda s=status: mod._ws_handshake_verify(
                    upgrade_response(RFC_KEY, status=s), RFC_KEY)) is None]
    suite.record(GA, "verify-non-101-refused", problems,
                 detail=["refused: %s" % ", ".join(statuses)])

    variants = {
        "Upgrade: websocketx": dict(upgrade="websocketx"),
        "no Upgrade": dict(upgrade=None),
        "Connection: Upgraded": dict(connection="Upgraded"),
        "no Connection": dict(connection=None),
    }
    problems = ["%s: accepted" % what for what, kw in sorted(variants.items())
                if raises(mod, lambda k=kw: mod._ws_handshake_verify(
                    upgrade_response(RFC_KEY, **k), RFC_KEY)) is None]
    suite.record(GA, "verify-upgrade-connection-exact", problems)

    extras = ["Sec-WebSocket-Extensions: permessage-deflate",
              "Sec-WebSocket-Protocol: chat"]
    problems = ["%s: accepted" % extra for extra in extras
                if raises(mod, lambda e=extra: mod._ws_handshake_verify(
                    upgrade_response(RFC_KEY, extra=(e,)), RFC_KEY)) is None]
    suite.record(GA, "verify-unoffered-negotiation-refused", problems)

    malformed = ["No colon on this line", " folded: continuation", ": empty-name"]
    problems = ["%r: accepted" % line for line in malformed
                if raises(mod, lambda x=line: mod._ws_handshake_verify(
                    upgrade_response(RFC_KEY, extra=(x,)), RFC_KEY)) is None]
    suite.record(GA, "verify-malformed-header-refused", problems)

    cap = mod.WS_MAX_HANDSHAKE_BYTES
    problems = []
    if mod._ws_handshake_split(bytearray(b"x" * cap)) != -1:
        problems.append("a header block AT the cap with no terminator did not ask for more")
    if raises(mod, lambda: mod._ws_handshake_split(bytearray(b"x" * (cap + 1)))) is None:
        problems.append("a header block one byte OVER the cap was not refused")
    exact = bytearray(b"x" * (cap - 4) + b"\r\n\r\n")
    if mod._ws_handshake_split(exact) != cap:
        problems.append("a block ending exactly at the cap was not taken")
    over = bytearray(b"x" * (cap - 3) + b"\r\n\r\n")
    if raises(mod, lambda: mod._ws_handshake_split(over)) is None:
        problems.append("a block ending one byte past the cap was taken")
    blob = bytearray(upgrade_response(RFC_KEY) + b"\x81\x02hi")
    end = mod._ws_handshake_split(blob)
    if end < 0 or bytes(blob[end:]) != b"\x81\x02hi":
        problems.append("the bytes after the header block were not left in place")
    suite.record(GA, "split-and-header-cap", problems,
                 detail=["cap: %d bytes" % cap])

    good = {
        "ws://h:1/p?q=1#frag": ("h", 1, "/p?q=1"),
        "ws://h": ("h", 80, "/"),
        "ws://h?x=1": ("h", 80, "/?x=1"),
        "ws://127.0.0.1:9222/devtools/page/A": ("127.0.0.1", 9222, "/devtools/page/A"),
        "ws://[::1]:9222/x": ("::1", 9222, "/x"),
        "ws://[::1]": ("::1", 80, "/"),
    }
    problems = ["%s -> %r, wanted %r" % (url, mod._ws_parse_url(url), want)
                for url, want in sorted(good.items()) if mod._ws_parse_url(url) != want]
    refused = ["wss://h/p", "http://h/", "h:1/p", "ws://u:p@h/", "ws://h:0/",
               "ws://h:65536/", "ws://h:x/", "ws://:80/", "ws://[::1/", "ws://[::1]x/"]
    problems += ["%s: not refused" % url for url in refused
                 if raises(mod, lambda u=url: mod._ws_parse_url(u)) is None]
    suite.record(GA, "parse-url", problems,
                 detail=["%d parsed, %d refused" % (len(good), len(refused))])


# --- B. frames ----------------------------------------------------------------

def group_frames(suite, mod):
    rng = random.Random(6455)
    problems = []
    for size in (0, 1, 3, 4, 5, 1000, 65537):
        key = bytes(rng.randrange(256) for _ in range(4))
        data = bytes(rng.randrange(256) for _ in range(size))
        got = mod._ws_mask(key, data)
        if got != naive_mask(key, data):
            problems.append("size %d: differs from the per-byte oracle" % size)
        if mod._ws_mask(key, got) != data:
            problems.append("size %d: masking twice is not the identity" % size)
    lead = mod._ws_mask(b"\x00\x00\x00\x00", b"\x00\x00\x01")
    problems += problem_if(lead != b"\x00\x00\x01",
                           "leading zero bytes were lost: %r" % lead)
    suite.record(GB, "mask-matches-oracle", problems)

    problems, detail = [], []
    for size in LENGTHS:
        payload = bytes(rng.randrange(256) for _ in range(size))
        frame = mod._ws_encode_frame(0x1, payload)
        got = decode_client_frame(frame)
        if got is None:
            problems.append("%d: the oracle could not decode it" % size)
            continue
        fin, opcode, masked, body, used, header = got
        want_header = 6 if size < 126 else (8 if size < 65536 else 14)
        if not (fin and opcode == 0x1 and masked and body == payload
                and used == len(frame) and header == want_header):
            problems.append("%d: fin=%s op=%d masked=%s header=%d (wanted %d)"
                            % (size, fin, opcode, masked, header, want_header))
        detail.append("%d bytes -> %d-byte header" % (size, header))
    keys = {mod._ws_encode_frame(0x1, b"same")[2:6] for _ in range(8)}
    problems += problem_if(len(keys) < 2, "eight frames reused one mask key")
    nonfin = decode_client_frame(mod._ws_encode_frame(0x0, b"x", fin=False))
    problems += problem_if(nonfin[0] or nonfin[1] != 0x0,
                           "a non-FIN continuation was encoded as %r" % (nonfin[:2],))
    suite.record(GB, "encode-every-length-form", problems, detail=detail)

    shapes = {"ping of 126": (0x9, b"x" * 126, True),
              "close without FIN": (0x8, b"", False),
              "opcode 0x3": (0x3, b"", True)}
    problems = ["%s: encoded" % what for what, (op, pl, fin) in sorted(shapes.items())
                if raises(mod, lambda o=op, p=pl, f=fin: mod._ws_encode_frame(o, p, f)) is None]
    suite.record(GB, "encode-refuses-bad-frames", problems)

    problems = []
    for size in LENGTHS:
        payload = bytes(rng.randrange(256) for _ in range(size))
        frame = server_frame(0x2, payload)
        got = mod._ws_parse_frame(bytearray(frame + b"\x89\x00"))
        if got != (True, 0x2, payload, len(frame)):
            problems.append("%d: parsed %r" % (size, None if got is None else got[:2] + (len(got[2]), got[3])))
    suite.record(GB, "parse-every-length-form", problems,
                 detail=["lengths: %s, trailing bytes left unconsumed" % ", ".join(map(str, LENGTHS))])

    problems = []
    for size in (5, 126, 65536):
        frame = server_frame(0x1, b"a" * size)
        cuts = sorted(set([0, 1, 2, 3, 9, len(frame) - 1] + [len(frame) // 2]))
        for cut in cuts:
            if cut < len(frame) and mod._ws_parse_frame(bytearray(frame[:cut])) is not None:
                problems.append("size %d: a %d-byte prefix parsed as a frame" % (size, cut))
    suite.record(GB, "parse-incomplete-asks-for-more", problems)

    problems = ["RSV 0x%02X: accepted" % rsv for rsv in (0x40, 0x20, 0x10)
                if raises(mod, lambda r=rsv: mod._ws_parse_frame(
                    bytearray(server_frame(0x1, b"x", rsv=r)))) is None]
    suite.record(GB, "parse-rsv-refused", problems)

    exc = raises(mod, lambda: mod._ws_parse_frame(
        bytearray(server_frame(0x1, b"hello", mask_key=b"abcd"))))
    suite.record(GB, "parse-masked-server-frame-refused", problem_if(
        exc is None, "a masked server frame was accepted"), detail=[str(exc)])

    unknown = list(range(0x3, 0x8)) + list(range(0xB, 0x10))
    problems = ["opcode 0x%X: accepted" % op for op in unknown
                if raises(mod, lambda o=op: mod._ws_parse_frame(
                    bytearray(server_frame(o, b"")))) is None]
    suite.record(GB, "parse-unknown-opcode-refused", problems,
                 detail=["%d reserved opcodes" % len(unknown)])

    problems = problem_if(
        raises(mod, lambda: mod._ws_parse_frame(bytearray(server_frame(0x9, b"x" * 126)))) is None,
        "a 126-byte ping was accepted")
    problems += problem_if(
        raises(mod, lambda: mod._ws_parse_frame(bytearray(server_frame(0x8, b"", fin=False)))) is None,
        "a close without FIN was accepted")
    problems += problem_if(
        mod._ws_parse_frame(bytearray(server_frame(0x9, b"x" * 125))) is None,
        "a 125-byte ping -- the largest legal one -- was not parsed")
    suite.record(GB, "parse-control-frame-rules", problems)

    cap = mod.WS_MAX_FRAME_BYTES
    head_over = server_frame(0x2, size=cap + 1)
    head_at = server_frame(0x2, size=cap)
    msb = bytes([0x82, 127]) + struct.pack(">Q", 1 << 63)
    problems = problem_if(len(head_over) != 10, "the forged header is %d bytes" % len(head_over))
    problems += problem_if(
        raises(mod, lambda: mod._ws_parse_frame(bytearray(head_over))) is None,
        "a header announcing cap+1 was not refused before its payload")
    problems += problem_if(mod._ws_parse_frame(bytearray(head_at)) is not None,
                           "a header announcing exactly the cap did not wait for its payload")
    problems += problem_if(raises(mod, lambda: mod._ws_parse_frame(bytearray(msb))) is None,
                           "a 64-bit length with its top bit set was accepted")
    suite.record(GB, "parse-frame-cap-from-the-header", problems,
                 detail=["cap: %d bytes, refused from a 10-byte header" % cap])

    # The one relation the two caps are argued to hold: Chrome does not
    # fragment, so a frame cap BELOW the message cap would refuse a message
    # the message cap admits. The values themselves are argued in the source
    # and deliberately not restated here.
    suite.record(GB, "frame-cap-admits-every-message", problem_if(
        mod.WS_MAX_FRAME_BYTES < mod.WS_MAX_MESSAGE_BYTES,
        "the frame cap is below the message cap"))


# --- C. messages --------------------------------------------------------------

def group_messages(suite, mod):
    text = "héllo wörld ☃".encode("utf-8")
    cut = text.index(b"\xc3") + 1           # inside the first two-byte character
    pending, events = [], []
    frames = [(False, 0x1, text[:cut]), (True, 0x9, b"ping"),
              (False, 0x0, text[cut:cut + 4]), (True, 0x0, text[cut + 4:])]
    for fin, opcode, payload in frames:
        events.append(mod._ws_assemble(pending, fin, opcode, payload))
    problems = problem_if(events != [None, (0x9, b"ping"), None, (0x1, text.decode("utf-8"))],
                          "events: %r" % events)
    problems += problem_if(pending != [], "the message stayed open: %r" % pending)
    binary = [mod._ws_assemble(pending, False, 0x2, b"\x00\xff"),
              mod._ws_assemble(pending, True, 0x0, b"\xfe")]
    problems += problem_if(binary != [None, (0x2, b"\x00\xff\xfe")], "binary: %r" % binary)
    suite.record(GC, "assemble-fragments-around-a-ping", problems,
                 detail=["a UTF-8 character split across two fragments reassembles"])

    problems = problem_if(raises(mod, lambda: mod._ws_assemble([], True, 0x0, b"x")) is None,
                          "a continuation with no message open was taken")
    open_msg = [0x1, bytearray(b"a")]
    problems += problem_if(raises(mod, lambda: mod._ws_assemble(open_msg, True, 0x1, b"b")) is None,
                           "a text frame inside an open message was taken")
    suite.record(GC, "assemble-sequence-errors", problems)

    saved = mod.WS_MAX_MESSAGE_BYTES
    mod.WS_MAX_MESSAGE_BYTES = 8
    try:
        pending = []
        mod._ws_assemble(pending, False, 0x1, b"abcd")
        at_cap = mod._ws_assemble(pending, True, 0x0, b"efgh")
        pending = []
        mod._ws_assemble(pending, False, 0x1, b"abcde")
        over = raises(mod, lambda: mod._ws_assemble(pending, True, 0x0, b"fghi"))
    finally:
        mod.WS_MAX_MESSAGE_BYTES = saved
    problems = problem_if(at_cap != (0x1, "abcdefgh"), "a message AT the cap: %r" % (at_cap,))
    problems += problem_if(over is None, "a message one byte over the cap was assembled")
    suite.record(GC, "assemble-message-cap-across-fragments", problems,
                 detail=["cap lowered to 8 for the case, then restored"])

    problems = problem_if(raises(mod, lambda: mod._ws_assemble([], True, 0x1, b"ok \xff")) is None,
                          "invalid UTF-8 was accepted as text")
    problems += problem_if(raises(mod, lambda: mod._ws_assemble([], True, 0x1, b"\xc3")) is None,
                           "a truncated UTF-8 sequence was accepted as text")
    suite.record(GC, "assemble-strict-utf8", problems)

    reply = mod._ws_control_reply(0x9, b"abc")
    got = decode_client_frame(reply)
    problems = problem_if(got is None or got[:4] != (True, 0xA, True, b"abc"),
                          "a ping was answered with %r" % (got,))
    big = decode_client_frame(mod._ws_control_reply(0x9, b"z" * 125))
    problems += problem_if(big is None or big[3] != b"z" * 125, "a 125-byte ping was not echoed")
    problems += problem_if(mod._ws_control_reply(0xA, b"abc") is not None,
                           "a pong was answered")
    suite.record(GC, "ping-answered-with-same-payload", problems)

    problems = []
    got = decode_client_frame(mod._ws_control_reply(0x8, b"\x03\xe8bye"))
    if got is None or got[:4] != (True, 0x8, True, b"\x03\xe8"):
        problems.append("close 1000 was answered with %r" % (got,))
    got = decode_client_frame(mod._ws_control_reply(0x8, b""))
    if got is None or got[:4] != (True, 0x8, True, b""):
        problems.append("an empty close was answered with %r" % (got,))
    bad = {"one byte": b"\x03", "code 999": b"\x03\xe7", "code 1005": b"\x03\xed",
           "code 1006": b"\x03\xee", "reason not UTF-8": b"\x03\xe8\xff"}
    problems += ["%s: accepted" % what for what, pl in sorted(bad.items())
                 if raises(mod, lambda p=pl: mod._ws_control_reply(0x8, p)) is None]
    suite.record(GC, "close-echoes-code-and-checks-payload", problems)

    conn = mod._WsConnection(None, None, None, 1.0)
    conn.buf += server_frame(0x9, b"p") * 3000 + server_frame(0xA, b"q") + server_frame(0x1, b"done")
    replies, message, steps = 0, None, 0
    while message is None and steps < 5000:
        steps += 1
        step = mod._ws_step(conn)
        if step is None:
            break
        reply, message, _closed = step
        replies += reply is not None
    problems = problem_if(message != "done", "the text after 3000 pings was %r" % message)
    problems += problem_if(replies != 3000, "%d pongs for 3000 pings" % replies)
    conn.buf += server_frame(0x8, b"\x03\xe8")
    closing = mod._ws_step(conn)
    problems += problem_if(closing is None or not closing[2], "a close did not report closed")
    suite.record(GC, "step-is-iterative", problems,
                 detail=["3000 pings and a pong ahead of one text frame, no recursion",
                         "recursion limit here: %d" % sys.getrecursionlimit()])


# --- D. wrappers --------------------------------------------------------------

def group_wrappers(suite, mod):
    blob = (server_frame(0x1, b"par", fin=False) + server_frame(0x9, b"hb")
            + server_frame(0x0, b"tial"))
    result, writer = asyncio.run(feed_and_recv(mod, blob))
    frames = decode_all(writer.data)
    problems = problem_if(result != "partial", "_ws_recv returned %r" % (result,))
    problems += problem_if(frames != [(True, 0xA, True, b"hb")], "wrote %r" % frames)
    suite.record(GD, "async-recv-fragmented-with-ping", problems)

    blob = server_frame(0x9, b"x") * 3000 + server_frame(0x1, b"after")
    result, writer = asyncio.run(feed_and_recv(mod, blob))
    problems = problem_if(result != "after", "_ws_recv returned %r" % (result,))
    problems += problem_if(len(decode_all(writer.data)) != 3000,
                           "%d pongs written" % len(decode_all(writer.data)))
    suite.record(GD, "async-recv-ping-stream-no-recursion", problems)

    result, writer = asyncio.run(feed_and_recv(mod, server_frame(0x8, b"\x03\xe9gone"), eof=False))
    frames = decode_all(writer.data)
    problems = problem_if(result is not None, "a close returned %r, not None" % (result,))
    problems += problem_if(frames != [(True, 0x8, True, b"\x03\xe9")], "wrote %r" % frames)
    suite.record(GD, "async-recv-close-returns-none", problems)

    result, _w = asyncio.run(feed_and_recv(mod, server_frame(0x1, b"cut short")[:5]))
    problems = problem_if(not isinstance(result, mod.WebSocketError),
                          "EOF mid-frame gave %r" % (result,))
    problems += problem_if(not isinstance(result, ConnectionError),
                           "WebSocketError is not a ConnectionError")
    suite.record(GD, "async-recv-eof-is-an-error", problems, detail=[repr(result)])

    left, right = socket.socketpair()
    try:
        left.settimeout(5)
        right.settimeout(5)
        conn = mod._WsConnection(None, None, left, 5.0)
        right.sendall(server_frame(0x9, b"sync") + server_frame(0x1, b"hel", fin=False)
                      + server_frame(0x0, b"lo"))
        got = mod._ws_sync_recv(conn)
        mod._ws_sync_send(conn, "reply")
        mod._ws_sync_close(conn)
        written = b""
        while True:
            chunk = right.recv(65536)
            if not chunk:
                break
            written += chunk
    finally:
        right.close()
        left.close()
    frames = decode_all(written)
    wanted = [(True, 0xA, True, b"sync"), (True, 0x1, True, b"reply"),
              (True, 0x8, True, b"\x03\xe8")]
    problems = problem_if(got != "hello", "_ws_sync_recv returned %r" % (got,))
    problems += problem_if(frames != wanted, "wrote %r" % frames)
    suite.record(GD, "sync-recv-send-close-over-socketpair", problems)

    def leftover(peer, sock):
        sock.sendall(peer.upgrade() + server_frame(0x1, b"early"))
        peer.read_frame(sock)

    async def async_connect(peer):
        conn = await mod._ws_connect("127.0.0.1", peer.port, "/x", timeout=5.0)
        try:
            return await mod._ws_recv(conn)
        finally:
            conn.writer.close()

    problems = []
    peer = Peer(leftover)
    got = asyncio.run(async_connect(peer))
    peer.join()
    problems += problem_if(got != "early", "async: the frame sent with the 101 read as %r" % (got,))
    peer = Peer(leftover)
    conn = mod._ws_sync_connect(peer.url("/x"), timeout=5.0)
    try:
        got = mod._ws_sync_recv(conn)
    finally:
        conn.sock.close()
    peer.join()
    problems += problem_if(got != "early", "sync: the frame sent with the 101 read as %r" % (got,))
    problems += problem_if(peer.header("origin") is not None, "sync: an Origin header was sent")
    suite.record(GD, "connect-keeps-bytes-after-the-101", problems,
                 detail=["both wrappers, one loopback peer each"])

    def wrong_accept(peer, sock):
        sock.sendall(upgrade_response("AAAAAAAAAAAAAAAAAAAAAA=="))
        peer.read_frame(sock)

    def silent(peer, sock):
        peer.read_frame(sock)

    async def async_expect(peer, timeout):
        try:
            await mod._ws_connect("127.0.0.1", peer.port, "/", timeout=timeout)
        except Exception as exc:
            return exc
        return None

    problems = []
    peer = Peer(wrong_accept)
    exc = asyncio.run(async_expect(peer, 5.0))
    peer.join()
    problems += problem_if(not isinstance(exc, mod.WebSocketError), "async wrong accept: %r" % (exc,))
    problems += problem_if(peer.error is not None, "async: the peer saw %s" % peer.error)
    peer = Peer(wrong_accept)
    try:
        mod._ws_sync_connect(peer.url(), timeout=5.0)
        exc = None
    except Exception as err:
        exc = err
    peer.join()
    problems += problem_if(not isinstance(exc, mod.WebSocketError), "sync wrong accept: %r" % (exc,))
    peer = Peer(silent, timeout=3.0)
    exc = asyncio.run(async_expect(peer, 0.5))
    peer.join()
    problems += problem_if(not isinstance(exc, asyncio.TimeoutError),
                           "a peer that never answers gave %r, not a timeout" % (exc,))
    suite.record(GD, "connect-failures-close-the-transport", problems,
                 detail=["wrong accept refused by both wrappers; the peer then saw EOF, "
                         "not a hang", "silent peer: asyncio.TimeoutError after 0.5 s"])


# --- E. hosts -----------------------------------------------------------------

async def drive_gdc(gdc, peer):
    session = gdc.CdpSession("T1", peer.url())
    try:
        await asyncio.wait_for(session.connect(), 20)
        result = await session.send("Runtime.evaluate", {"expression": "1"}, timeout=10)
        return None, result
    except Exception as exc:
        return exc, None
    finally:
        await session.close()


def group_hosts(suite):
    try:
        gdc = H.load_module_from_path("mcp_gdc_under_ws_test", GDC)
    except Exception as exc:
        gdc = None
        load_error = repr(exc)

    def gdc_case(cid, scenario, check, detail):
        if gdc is None:
            suite.record(GE, cid, ["mcp-gdc.py did not load: %s" % load_error])
            return
        peer = Peer(scenario)
        exc, result = asyncio.run(drive_gdc(gdc, peer))
        peer.join()
        suite.record(GE, cid, check(peer, exc, result), detail=detail + [
            "session error: %r" % (exc,), "peer error: %s" % peer.error])

    def fragmented_ok(peer, exc, result):
        problems = problem_if(exc is not None, "the session failed: %r" % (exc,))
        problems += problem_if(result != {"echo": "Runtime.evaluate"},
                               "Runtime.evaluate answered %r" % (result,))
        problems += problem_if(b"mid" not in peer.pongs,
                               "the ping between two fragments was not answered")
        problems += problem_if(any(not f[2] for f in peer.frames), "an unmasked client frame")
        return problems

    gdc_case("gdc-fragmented-reply", cdp_scenario("fragmented"), fragmented_ok,
             ["every CDP answer in three fragments with a ping between them"])

    def ping_ok(peer, exc, result):
        problems = problem_if(exc is not None, "the session failed: %r" % (exc,))
        problems += problem_if(b"cdp-ping" not in peer.pongs,
                               "the ping was not answered with its own payload: %r" % peer.pongs)
        return problems

    gdc_case("gdc-ping-answered", cdp_scenario("plain", ping=b"cdp-ping"), ping_ok,
             ["every answer withheld until the ping is ponged; the ping rides "
              "in the 101's own segment"])

    def masked_refused(peer, exc, result):
        return problem_if(exc is None, "a session whose every answer was a MASKED "
                                       "server frame connected and answered %r" % (result,))

    gdc_case("gdc-masked-server-frame-refused", cdp_scenario("masked"), masked_refused,
             ["the peer masks every answer, which RFC 6455 forbids a server"])

    try:
        ddg = H.load_module_from_path("search_ddg_under_ws_test", DDG)
    except Exception as exc:
        suite.record(GE, "search-cdp-backend", ["search_duckduckgo.py did not load: %r" % exc])
        return
    peer = Peer(cdp_scenario("fragmented", ping=b"hello"))
    try:
        searcher = ddg.CDPSearcher(peer.url())
        try:
            result = searcher._send("Runtime.evaluate", {"expression": "1"})
        finally:
            searcher.close()
        exc = None
    except Exception as err:
        exc, result = err, None
    peer.join()
    problems = problem_if(exc is not None, "the CDP backend failed: %r" % (exc,))
    problems += problem_if((result or {}).get("result") != {"echo": "Runtime.evaluate"},
                           "Runtime.evaluate answered %r" % (result,))
    problems += problem_if(peer.header("origin") is not None,
                           "an Origin header was sent: %r" % peer.header("origin"))
    problems += problem_if(b"hello" not in peer.pongs, "the ping was not answered")
    problems += problem_if(not any(f[1] == 0x8 for f in peer.frames),
                           "close() sent no close frame")
    suite.record(GE, "search-cdp-backend", problems,
                 detail=["CDPSearcher over loopback: no Origin, a ping in the 101's "
                         "segment, a fragmented answer, a close on the way out",
                         "error: %r" % (exc,), "peer error: %s" % peer.error])


def group_hygiene(suite, pyc_before):
    pyc_after = H.pycache_snapshot()
    suite.record(GF, "pycache-zero", problem_if(
        pyc_before or pyc_after,
        "expected zero .pyc in the tree, saw %d before and %d after"
        % (len(pyc_before), len(pyc_after))))


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="the stdlib WebSocket client and its two hosts",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    mod = H.load_module_from_path("mcp_websocket_under_test", SOURCE)
    group_handshake(suite, mod)
    group_frames(suite, mod)
    group_messages(suite, mod)
    group_wrappers(suite, mod)
    group_hosts(suite)
    group_hygiene(suite, pyc_before)
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
