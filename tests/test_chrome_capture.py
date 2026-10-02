#!/usr/bin/env python3
"""The Chrome capture harness -- `Scripts/chrome_capture.py`.

`Scripts/chrome_capture.py` is the INDEPENDENT oracle the stdlib Chrome client
(`Scripts/_mcp_chrome.py`) is measured with: it records what a real browser
puts on the wire on loopback, parses the ClientHello into JA3/JA4, decodes
HPACK, diffs two capture sets and exports reduced fixtures. An oracle that is
wrong makes every later comparison agree with the wrong thing, so this suite
checks the oracle itself.

THE ORACLE HERE IS WRITTEN FROM THE SPECS, NOT FROM THE MODULE. The ClientHello
walker, the JA3 string and the JA4 fingerprint below are this file's own,
written from the TLS 1.3 wire format (RFC 8446 4.1.2) and the FoxIO JA4
definition; the pinned JA4 is the value R-0017 measured on Chrome 153 and
recorded in `docs/concepts/spec-ddg.md` section 2.9. The HPACK vectors are
RFC 7541 Appendix C, typed in. The HelloRetryRequest marker is RFC 8446's own
definition, SHA-256("HelloRetryRequest"). The HTTP/2 frames and HPACK blocks
the loopback client sends are built by this file's encoder, never by the
module's `frame`/`hpack_literal`.

ONE FIXED FIXTURE. Group A reads exactly one file,
`tests/files/chrome_capture/navigate-r0017.json` (exported by this tool from
R-0017's first Chrome capture). It never iterates a directory, so the case
count does not move when more captures are committed.

LOOPBACK ONLY. Group C runs `serve` in a thread (and once as a child process)
on 127.0.0.1 with port 0 and drives it with a stdlib client. No name is
resolved, nothing leaves the host. Every file the suite makes lives in a
mkdtemp() workspace.

Groups:
  A. FIXTURE:  the one committed ClientHello fixture -- its shape, JA3/JA4 by
               the oracle, by the module and as stored, and the ja4 CLI
  B. HPACK:    the decoder against RFC 7541 C.1-C.6, and its refusals
  C. SERVE:    h2 multi-stream, h1 over TLS, plaintext h1, the scripted HRR
               (raw ClientHello and a real stdlib client), the CLI entry,
               owner-only records (0700 dir, 0600 file), a path --label
               refused, control bytes escaped in the summary line
  D. DIFF/EXPORT: diff of the fixture against itself and against a copy with
               one signature algorithm removed; a resumed (PSK) group on one
               side only reported unmatched, not fixed; export's refusals
               (foreign cookie name or value, non-loopback Referer, credential
               headers, unscripted or truncated bodies), its all-or-nothing
               write, a planted .tmp symlink never followed (0600 fixture),
               and every committed fixture under tests/files/chrome/
               re-exported unchanged -- ONE case, however many it reads
  E. BIND:     every non-loopback bind host refused, nothing created
  F. HYGIENE:  no bytecode, no new repo paths, writes confined, the module
               imports nothing of the client it measures and is import-safe

Usage:
  python3 tests/test_chrome_capture.py            # standalone
  python3 tests/run.py chrome_capture             # through the fleet runner
  python3 tests/test_chrome_capture.py --brief    # one line per case

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every case passes.
"""

import ast
import contextlib
import copy
import hashlib
import http.client
import io
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "chrome_capture"

SOURCE = H.repo_path("Scripts", "chrome_capture.py")
FIXTURE = H.repo_path("tests", "files", "chrome_capture", "navigate-r0017.json")
FIXTURE_DIR = os.path.dirname(FIXTURE)
SCRATCH = H.repo_path(".claude", "tmp")

GA = "A. FIXTURE: one committed ClientHello, JA3/JA4 by oracle, module, store, CLI"
GB = "B. HPACK: RFC 7541 C.1-C.6 and the decoder's refusals"
GC = "C. SERVE: h2 streams, h1 TLS, plain h1, scripted HRR, CLI -- loopback"
GD = "D. DIFF/EXPORT: self-diff, one sigalg removed, export refusals"
GE = "E. BIND: non-loopback bind hosts refused"
GF = "F. HYGIENE: bytecode, repo paths, confined writes, independence"

# docs/concepts/spec-ddg.md section 2.9: the JA4 R-0017 measured for a fresh
# Chrome 153 connection on loopback. Typed from the page, not computed here.
PINNED_JA4 = "t13d1517h2_8daaf6152771_cb7bf5808d99"

# RFC 8446 4.1.3: the ServerHello.random of a HelloRetryRequest, typed, and
# checked below against its definition SHA-256("HelloRetryRequest").
HRR_RANDOM = bytes.fromhex(
    "cf21ad74e59a6111be1d8c021e65b891c2a211167abb8c5e079e09e2c8a8339c")

H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
TEST_UA = "chrome-capture-test/1"
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

# Every directory handed to the tool as --out / --to / diff --out, so group F
# can prove they all sit inside the workspace.
WRITES = []


def problem_if(condition, message):
    return [message] if condition else []


def skip(suite, group, cid, reason):
    """Record a SKIP as an INFO case: informative, never a failure."""
    return suite.record(group, cid, (), status=H.INFO,
                        detail=["SKIPPED: %s" % reason],
                        brief="INFO | %s (skipped: %s)" % (cid, reason))


# --- the ClientHello oracle ---------------------------------------------------

def u16(data, pos):
    return (data[pos] << 8) | data[pos + 1]


def u16s(data):
    return [u16(data, i) for i in range(0, len(data) - 1, 2)]


def is_grease(value):
    """RFC 8701: 0x0A0A, 0x1A1A, ... 0xFAFA."""
    return (value & 0x0F0F) == 0x0A0A and (value >> 8) == (value & 0xFF)


def handshake_of_records(raw):
    """The first handshake message carried by a run of TLS records."""
    hs, pos = b"", 0
    while pos + 5 <= len(raw):
        if raw[pos] != 0x16:
            raise ValueError("record at %d is not a handshake record" % pos)
        size = u16(raw, pos + 3)
        hs += raw[pos + 5:pos + 5 + size]
        pos += 5 + size
    length = int.from_bytes(hs[1:4], "big")
    return hs[:4 + length]


def oracle_hello(hs):
    """Walk a ClientHello handshake message (RFC 8446 4.1.2)."""
    if hs[0] != 1:
        raise ValueError("not a ClientHello: type %d" % hs[0])
    length = int.from_bytes(hs[1:4], "big")
    body = hs[4:4 + length]
    if len(body) != length:
        raise ValueError("truncated ClientHello")
    pos = 2 + 32
    sid = body[pos + 1:pos + 1 + body[pos]]
    pos += 1 + body[pos]
    cipher_pos = pos
    size = u16(body, pos)
    ciphers = u16s(body[pos + 2:pos + 2 + size])
    pos += 2 + size
    pos += 1 + body[pos]
    ext_block = pos
    end = pos + 2 + u16(body, pos)
    pos += 2
    exts = []
    while pos < end:
        etype, size = u16(body, pos), u16(body, pos + 2)
        exts.append((etype, body[pos + 4:pos + 4 + size]))
        pos += 4 + size
    return {"version": u16(body, 0), "session_id": sid, "ciphers": ciphers,
            "extensions": exts, "body": body, "cipher_pos": cipher_pos,
            "ext_block": ext_block}


def ext_data(hello, etype):
    for t, data in hello["extensions"]:
        if t == etype:
            return data
    return None


def oracle_fields(hello):
    groups = u16s((ext_data(hello, 10) or b"\x00\x00")[2:])
    points = ext_data(hello, 11)
    points = list(points[1:1 + points[0]]) if points else []
    sigalgs = u16s((ext_data(hello, 13) or b"\x00\x00")[2:])
    versions = ext_data(hello, 43)
    versions = u16s(versions[1:1 + versions[0]]) if versions else []
    alpn, raw = [], (ext_data(hello, 16) or b"\x00\x00")[2:]
    while raw:
        alpn.append(raw[1:1 + raw[0]].decode("latin-1"))
        raw = raw[1 + raw[0]:]
    shares, raw = [], (ext_data(hello, 51) or b"\x00\x00")[2:]
    while raw:
        shares.append((u16(raw, 0), u16(raw, 2)))
        raw = raw[4 + u16(raw, 2):]
    return {"groups": groups, "points": points, "sigalgs": sigalgs,
            "versions": versions, "alpn": alpn, "shares": shares}


def oracle_ja3(hello):
    """JA3: SSLVersion,Ciphers,Extensions,EllipticCurves,EllipticCurvePointFormats."""
    f = oracle_fields(hello)
    return ",".join([
        str(hello["version"]),
        "-".join(str(c) for c in hello["ciphers"] if not is_grease(c)),
        "-".join(str(t) for t, _d in hello["extensions"] if not is_grease(t)),
        "-".join(str(g) for g in f["groups"] if not is_grease(g)),
        "-".join(str(p) for p in f["points"])])


def oracle_ja4(hello):
    """(ja4, ja4_r, ja4_o, ja4_ro) by the FoxIO JA4 definition."""
    f = oracle_fields(hello)
    versions = [v for v in f["versions"] if not is_grease(v)]
    top = max(versions) if versions else hello["version"]
    ver = {0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10"}.get(top, "00")
    exts = [t for t, _d in hello["extensions"] if not is_grease(t)]
    ciphers = [c for c in hello["ciphers"] if not is_grease(c)]
    sni = "d" if 0 in exts else "i"
    first = f["alpn"][0] if f["alpn"] else ""
    alpn = (first[0] + first[-1]) if first else "00"
    part_a = "t%s%s%02d%02d%s" % (ver, sni, min(len(ciphers), 99), min(len(exts), 99), alpn)
    sigs = ",".join("%04x" % s for s in f["sigalgs"] if not is_grease(s))
    b_raw = ",".join(sorted("%04x" % c for c in ciphers))
    c_raw = ",".join(sorted("%04x" % t for t in exts if t not in (0x0000, 0x0010)))
    if sigs:
        c_raw += "_" + sigs
    b_ord = ",".join("%04x" % c for c in ciphers)
    c_ord = ",".join("%04x" % t for t in exts)
    if sigs:
        c_ord += "_" + sigs

    def h12(text):
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    return ("%s_%s_%s" % (part_a, h12(b_raw), h12(c_raw)),
            "%s_%s_%s" % (part_a, b_raw, c_raw),
            "%s_%s_%s" % (part_a, h12(b_ord), h12(c_ord)),
            "%s_%s_%s" % (part_a, b_ord, c_ord))


def rebuild_hello(hello, exts=None, body_patch=None):
    """A ClientHello message with its extension list replaced, lengths recomputed."""
    body = hello["body"]
    if body_patch is not None:
        body = body_patch
    if exts is not None:
        block = b"".join(struct.pack("!HH", t, len(d)) + d for t, d in exts)
        body = body[:hello["ext_block"]] + struct.pack("!H", len(block)) + block
    return b"\x01" + len(body).to_bytes(3, "big") + body


# --- the HPACK / HTTP/2 encoder the loopback client uses ----------------------

def hp_int(value, prefix, flags):
    """RFC 7541 5.1."""
    limit = (1 << prefix) - 1
    if value < limit:
        return bytes([flags | value])
    out = bytearray([flags | limit])
    value -= limit
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def hp_str(text):
    raw = text.encode("latin-1")
    return hp_int(len(raw), 7, 0x00) + raw


def hp_indexed(index):
    return hp_int(index, 7, 0x80)


def hp_literal(name, value, incremental=False):
    """Literal with a NEW name: 0x40 = incremental indexing, 0x00 = without."""
    return (b"\x40" if incremental else b"\x00") + hp_str(name) + hp_str(value)


def h2_frame(ftype, flags, sid, payload):
    return struct.pack("!I", len(payload))[1:] + bytes([ftype, flags]) + struct.pack("!I", sid) + payload


def request_block(pairs):
    return b"".join(hp_literal(n, v) for n, v in pairs)


# --- running the tool ---------------------------------------------------------

def run_main(cc, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cc.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def start_serve(cc, argv):
    """`serve` in a thread with --port 0; returns (box, thread) once it listens."""
    opts = cc.build_parser().parse_args(argv)
    WRITES.append(opts.out)
    box = {"out": io.StringIO()}
    ready = threading.Event()

    def on_ready(port):
        box["port"] = port
        ready.set()

    def body():
        try:
            box["rc"] = cc.serve(opts, ready=on_ready, out=box["out"])
        except Exception as exc:        # the case reports it
            box["err"] = repr(exc)
        finally:
            ready.set()

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    ready.wait(15)
    return box, thread


def load_record(out_dir, label):
    path = os.path.join(out_dir, "%s-001.json" % label)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def client_ctx(alpn):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if alpn:
        ctx.set_alpn_protocols(list(alpn))
    return ctx


def memory_client_hello():
    """The ClientHello a stdlib client sends, captured without a socket."""
    ctx = client_ctx(("h2", "http/1.1"))
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = ctx.wrap_bio(incoming, outgoing, server_hostname="localhost")
    try:
        obj.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def read_frames(sock, want_ended, timeout=5.0):
    """Client-side h2 frames until every stream in *want_ended* has ended."""
    sock.settimeout(timeout)
    buf, frames, ended = b"", [], set()
    deadline = time.time() + timeout
    while not want_ended <= ended and time.time() < deadline:
        while len(buf) >= 9:
            size = int.from_bytes(buf[:3], "big")
            if len(buf) < 9 + size:
                break
            ftype, flags = buf[3], buf[4]
            sid = struct.unpack("!I", buf[5:9])[0] & 0x7FFFFFFF
            frames.append((ftype, flags, sid, buf[9:9 + size]))
            if ftype in (0, 1) and flags & 0x1:
                ended.add(sid)
            buf = buf[9 + size:]
        if want_ended <= ended:
            break
        try:
            chunk = sock.recv(65536)
        except (socket.timeout, OSError):
            break
        if not chunk:
            break
        buf += chunk
    return frames, ended


def pairs_of(headers):
    return [(h["name"], h["value"]) for h in headers or [] if "name" in h]


# --- A. the one fixture -------------------------------------------------------

def group_fixture(suite, cc):
    try:
        with open(FIXTURE, encoding="utf-8") as fh:
            fx = json.load(fh)
        hs = bytes.fromhex(fx["client_hello_hex"])
        hello = oracle_hello(hs)
    except Exception as exc:
        for cid in ("fixture-shape", "oracle-ja4-equals-pinned", "module-parse-matches-oracle",
                    "module-fingerprints-match-oracle", "stored-fingerprints-match",
                    "ja4-cli-prints-pinned"):
            suite.record(GA, cid, ["the fixture did not load: %r" % exc])
        return

    problems = problem_if(fx.get("format") != "chrome_capture/fixture/v1",
                          "format is %r" % fx.get("format"))
    problems += problem_if(fx.get("label") != "navigate", "label is %r" % fx.get("label"))
    problems += problem_if(fx.get("sni") != "localhost", "sni is %r" % fx.get("sni"))
    problems += problem_if(fx.get("client_hello_record_lengths") != [len(hs)],
                           "one record of %d bytes expected, have %r"
                           % (len(hs), fx.get("client_hello_record_lengths")))
    problems += problem_if(fx.get("server_set_cookie_names") != [],
                           "server_set_cookie_names is %r" % fx.get("server_set_cookie_names"))
    leaked = [k for k in ("peer", "time", "client_hello_raw_hex", "client_hello_error",
                          "handshake_error") if k in fx]
    problems += problem_if(leaked, "unreduced capture fields in the fixture: %s" % leaked)
    names = []
    data = ext_data(hello, 0) or b""
    raw = data[2:]
    while len(raw) >= 3:
        names.append(raw[3:3 + u16(raw, 1)].decode("latin-1"))
        raw = raw[3 + u16(raw, 1):]
    problems += problem_if(names != ["localhost"], "the SNI in the bytes is %r" % names)
    suite.record(GA, "fixture-shape", problems,
                 detail=["%d-byte ClientHello, %d extensions, SNI %s"
                         % (len(hs), len(hello["extensions"]), names)])

    ja4, ja4_r, ja4_o, ja4_ro = oracle_ja4(hello)
    suite.record(GA, "oracle-ja4-equals-pinned", problem_if(
        ja4 != PINNED_JA4, "this file's JA4 of the fixture is %s, spec-ddg records %s"
        % (ja4, PINNED_JA4)), detail=["JA4 %s (docs/concepts/spec-ddg.md 2.9)" % ja4])

    parsed = cc.parse_client_hello(hs[4:])
    fields = oracle_fields(hello)
    problems = []
    got_ciphers = [int(c["id"], 16) for c in parsed["cipher_suites"]]
    problems += problem_if(got_ciphers != hello["ciphers"], "cipher suites %r" % got_ciphers)
    got_exts = [e["type"] for e in parsed["extensions"]]
    problems += problem_if(got_exts != [t for t, _d in hello["extensions"]],
                           "extension order %r" % got_exts)
    by_type = dict((e["type"], e.get("decoded") or {}) for e in parsed["extensions"])
    problems += problem_if([int(a["id"], 16) for a in by_type.get(13, {}).get("algorithms", [])]
                           != fields["sigalgs"], "signature_algorithms differ")
    problems += problem_if(by_type.get(16, {}).get("protocols") != fields["alpn"],
                           "ALPN %r" % by_type.get(16, {}).get("protocols"))
    problems += problem_if([int(g["id"], 16) for g in by_type.get(10, {}).get("groups", [])]
                           != fields["groups"], "supported_groups differ")
    problems += problem_if([(int(s["group"], 16), s["key_len"])
                            for s in by_type.get(51, {}).get("shares", [])] != fields["shares"],
                           "key_share differs")
    problems += problem_if(parsed["session_id_len"] != len(hello["session_id"]),
                           "session_id_len %r" % parsed["session_id_len"])
    suite.record(GA, "module-parse-matches-oracle", problems,
                 detail=["ALPN %s, key shares %s" % (fields["alpn"], ", ".join(
                     "%04x:%d" % s for s in fields["shares"]))])

    fp = cc.fingerprints(parsed)
    ja3 = oracle_ja3(hello)
    want = {"ja4": ja4, "ja4_r": ja4_r, "ja4_o": ja4_o, "ja4_ro": ja4_ro, "ja3": ja3,
            "ja3_md5": hashlib.md5(ja3.encode()).hexdigest()}
    problems = ["%s: module %s, oracle %s" % (k, fp.get(k), v)
                for k, v in sorted(want.items()) if fp.get(k) != v]
    suite.record(GA, "module-fingerprints-match-oracle", problems,
                 detail=["ja3_md5 %s" % want["ja3_md5"], "ja4_o %s" % ja4_o])

    stored = fx.get("fingerprints") or {}
    problems = ["%s: stored %s, recomputed %s" % (k, stored.get(k), fp.get(k))
                for k in ("ja3", "ja3_md5", "ja4", "ja4_r", "ja4_o", "ja4_ro")
                if stored.get(k) != fp.get(k)]
    decoded = dict(parsed)
    decoded.pop("_ciphers_raw", None)
    decoded.pop("_legacy_version_raw", None)
    via_decoded = cc.fingerprints_of_decoded(decoded)
    problems += problem_if(via_decoded["ja4"] != PINNED_JA4,
                           "fingerprints_of_decoded gives %s" % via_decoded["ja4"])
    suite.record(GA, "stored-fingerprints-match", problems,
                 detail=["what export stored is what the parser computes; the decoded-dict "
                         "path diff uses agrees"])

    rc, out, err = run_main(cc, ["ja4", FIXTURE])
    suite.record(GA, "ja4-cli-prints-pinned", problem_if(
        rc != 0 or out.strip() != PINNED_JA4, "rc=%r out=%r err=%r" % (rc, out.strip(), err.strip())))


# --- B. HPACK -----------------------------------------------------------------

# RFC 7541 Appendix C, typed in. Each entry: (hex, expected header list,
# expected dynamic table newest first, expected table size).
DATE1 = "Mon, 21 Oct 2013 20:13:21 GMT"
DATE2 = "Mon, 21 Oct 2013 20:13:22 GMT"
EXAMPLE = "https://www.example.com"
COOKIE = "foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1"

REQ1 = [(":method", "GET"), (":scheme", "http"), (":path", "/"), (":authority", "www.example.com")]
REQ2 = REQ1 + [("cache-control", "no-cache")]
REQ3 = [(":method", "GET"), (":scheme", "https"), (":path", "/index.html"),
        (":authority", "www.example.com"), ("custom-key", "custom-value")]
REQ_TABLES = ([(":authority", "www.example.com")],
              [("cache-control", "no-cache"), (":authority", "www.example.com")],
              [("custom-key", "custom-value"), ("cache-control", "no-cache"),
               (":authority", "www.example.com")])
REQ_SIZES = (57, 110, 164)

RESP1 = [(":status", "302"), ("cache-control", "private"), ("date", DATE1), ("location", EXAMPLE)]
RESP2 = [(":status", "307"), ("cache-control", "private"), ("date", DATE1), ("location", EXAMPLE)]
RESP3 = [(":status", "200"), ("cache-control", "private"), ("date", DATE2), ("location", EXAMPLE),
         ("content-encoding", "gzip"), ("set-cookie", COOKIE)]
RESP_TABLES = ([("location", EXAMPLE), ("date", DATE1), ("cache-control", "private"), (":status", "302")],
               [(":status", "307"), ("location", EXAMPLE), ("date", DATE1), ("cache-control", "private")],
               [("set-cookie", COOKIE), ("content-encoding", "gzip"), ("date", DATE2)])
RESP_SIZES = (222, 222, 215)

VECTORS = {
    "c3-requests-plain": (4096, [
        "828684410f7777772e6578616d706c652e636f6d",
        "828684be58086e6f2d6361636865",
        "828785bf400a637573746f6d2d6b65790c637573746f6d2d76616c7565",
    ], (REQ1, REQ2, REQ3), REQ_TABLES, REQ_SIZES),
    "c4-requests-huffman": (4096, [
        "828684418cf1e3c2e5f23a6ba0ab90f4ff",
        "828684be5886a8eb10649cbf",
        "828785bf408825a849e95ba97d7f8925a849e95bb8e8b4bf",
    ], (REQ1, REQ2, REQ3), REQ_TABLES, REQ_SIZES),
    "c5-responses-plain-eviction": (256, [
        "4803333032580770726976617465611d4d6f6e2c203231204f637420323031332032303a31333a323120474d54"
        "6e1768747470733a2f2f7777772e6578616d706c652e636f6d",
        "4803333037c1c0bf",
        "88c1611d4d6f6e2c203231204f637420323031332032303a31333a323220474d54c05a04677a69707738666f6f"
        "3d4153444a4b48514b425a584f5157454f50495541585157454f49553b206d61782d6167653d333630303b2076"
        "657273696f6e3d31",
    ], (RESP1, RESP2, RESP3), RESP_TABLES, RESP_SIZES),
    "c6-responses-huffman-eviction": (256, [
        "488264025885aec3771a4b6196d07abe941054d444a8200595040b8166e082a62d1bff6e919d29ad171863c78f"
        "0b97c8e9ae82ae43d3",
        "4883640effc1c0bf",
        "88c16196d07abe941054d444a8200595040b8166e084a62d1bffc05a839bd9ab77ad94e7821dd7f2e6c7b335df"
        "dfcd5b3960d5af27087f3672c1ab270fb5291f9587316065c003ed4ee5b1063d5007",
    ], (RESP1, RESP2, RESP3), RESP_TABLES, RESP_SIZES),
}


def refused(fn):
    """The ValueError *fn* raised, or None. Any other exception propagates."""
    try:
        fn()
    except ValueError as exc:
        return exc
    return None


def group_hpack(suite, cc):
    problems = []
    for value, prefix, want in ((10, 5, "0a"), (1337, 5, "1f9a0a"), (42, 8, "2a")):
        got = cc.hpack_encode_int(value, prefix, 0).hex()
        if got != want:
            problems.append("encode %d/%d-bit prefix -> %s, RFC says %s" % (value, prefix, got, want))
        decoded, used = cc.HpackDecoder._int(bytes.fromhex(want), 0, prefix)
        if (decoded, used) != (value, len(want) // 2):
            problems.append("decode %s/%d-bit prefix -> %r" % (want, prefix, (decoded, used)))
    suite.record(GB, "c1-integer-representation", problems,
                 detail=["C.1.1 10 in 5 bits, C.1.2 1337 in 5 bits, C.1.3 42 in 8 bits"])

    problems = []
    dec = cc.HpackDecoder()
    got = pairs_of(dec.decode(bytes.fromhex("400a637573746f6d2d6b65790d637573746f6d2d686561646572")))
    problems += problem_if(got != [("custom-key", "custom-header")] or dec.size != 55,
                           "C.2.1: %r, table size %d" % (got, dec.size))
    for hexstr, want, where in (("040c2f73616d706c652f70617468", (":path", "/sample/path"), "C.2.2"),
                                ("100870617373776f726406736563726574", ("password", "secret"), "C.2.3"),
                                ("82", (":method", "GET"), "C.2.4")):
        dec = cc.HpackDecoder()
        out = dec.decode(bytes.fromhex(hexstr))
        if pairs_of(out) != [want] or dec.dyn:
            problems.append("%s: %r, table %r" % (where, pairs_of(out), dec.dyn))
    reps = [cc.HpackDecoder().decode(bytes.fromhex(h))[0].get("rep")
            for h in ("040c2f73616d706c652f70617468", "100870617373776f726406736563726574")]
    problems += problem_if(reps != ["literal_without_indexing", "literal_never_indexed"],
                           "representations recorded as %r" % reps)
    suite.record(GB, "c2-header-field-representations", problems)

    for cid in sorted(VECTORS):
        table_size, blocks, lists, tables, sizes = VECTORS[cid]
        dec = cc.HpackDecoder(max_table_size=table_size)
        problems = []
        for i, hexstr in enumerate(blocks):
            try:
                got = pairs_of(dec.decode(bytes.fromhex(hexstr)))
            except ValueError as exc:
                problems.append("block %d refused: %s" % (i + 1, exc))
                break
            if got != lists[i]:
                problems.append("block %d decoded to %r" % (i + 1, got))
            if [tuple(e) for e in dec.dyn] != tables[i] or dec.size != sizes[i]:
                problems.append("block %d left table %r (size %d, RFC %d)"
                                % (i + 1, dec.dyn, dec.size, sizes[i]))
        suite.record(GB, cid, problems,
                     detail=["three blocks on one decoder, table limit %d, sizes %s"
                             % (table_size, "/".join(map(str, sizes)))])

    problems = []
    exc = refused(lambda: cc.HpackDecoder().decode(bytes.fromhex("3fe21f")))
    problems += problem_if(exc is None, "a size update to 4097 above the advertised 4096 was taken")
    exc = refused(lambda: cc.HpackDecoder().decode(bytes.fromhex("823f00")))
    problems += problem_if(exc is None, "a size update after a header field was taken")
    dec = cc.HpackDecoder()
    dec.decode(bytes.fromhex("400a637573746f6d2d6b65790d637573746f6d2d686561646572"))
    dec.decode(bytes.fromhex("20"))
    problems += problem_if(dec.dyn or dec.size, "a size update to 0 did not empty the table")
    suite.record(GB, "table-size-update-rules", problems,
                 detail=["above the limit, after a field: refused; to 0: evicts everything"])

    problems = []
    for hexstr, what in (("80", "index 0"), ("be", "dynamic index 62 on an empty table"),
                         ("ffffffffffffff7f", "an index above 2**32"),
                         ("8286", "")):
        exc = refused(lambda h=hexstr: cc.HpackDecoder().decode(bytes.fromhex(h)))
        if what and exc is None:
            problems.append("%s was taken" % what)
        if not what and exc is not None:
            problems.append("the control block 8286 was refused: %s" % exc)
    exc = refused(lambda: cc.HpackDecoder().decode(bytes.fromhex("400a637573746f6d2d6b6579")))
    problems += problem_if(exc is None, "a literal cut before its value was taken")
    suite.record(GB, "malformed-blocks-refused", problems,
                 detail=["index 0, out-of-range dynamic index, integer overflow, truncation; "
                         "8286 is the accepted control"])

    problems = problem_if(cc.huffman_decode(b"\x1f") != b"a",
                          "'a' padded with ones did not decode")
    problems += problem_if(refused(lambda: cc.huffman_decode(b"\x18")) is None,
                           "'a' padded with zeros was taken")
    problems += problem_if(refused(lambda: cc.huffman_decode(b"\xff\xff\xff\xff")) is None,
                           "a string carrying EOS was taken")
    problems += problem_if(refused(lambda: cc.huffman_decode(b"\x1f\xff")) is None,
                           "padding longer than 7 bits was taken")
    problems += problem_if(cc.huffman_selfcheck() is not None,
                           "the embedded table fails its canonical/Kraft check: %s"
                           % cc.huffman_selfcheck())
    suite.record(GB, "huffman-padding-and-eos", problems,
                 detail=["RFC 7541 5.2: EOS refused, padding must be < 8 one-bits"])


# --- C. serve over loopback ---------------------------------------------------

def group_serve(suite, cc, ws):
    out = ws.subdir("serve")
    page = ws.write_bytes("page.html", b"<html><body>capture page</body></html>")

    # h2: two streams on one connection, the second one POST with a body and
    # a header indexed from the dynamic table the first stream filled.
    box, thread = start_serve(cc, ["serve", "--port", "0", "--out", out, "--label", "h2",
                                   "--count", "1", "--timeout", "30", "--idle", "2",
                                   "--page", page, "--set-cookie", "sid=abc"])
    frames, ended, error = [], set(), None
    if "port" in box:
        try:
            sock = client_ctx(["h2"]).wrap_socket(
                socket.create_connection(("127.0.0.1", box["port"]), timeout=5),
                server_hostname="localhost")
            try:
                block1 = (hp_indexed(2) + hp_indexed(7) + hp_indexed(4)
                          + hp_literal(":authority", "localhost")
                          + hp_literal("x-shared", "one", incremental=True)
                          + hp_literal("user-agent", TEST_UA))
                block3 = (hp_indexed(3) + hp_indexed(7) + hp_literal(":path", "/lite/")
                          + hp_literal(":authority", "localhost") + hp_indexed(62)
                          + hp_literal("content-type", "application/x-www-form-urlencoded"))
                sock.sendall(H2_PREFACE
                             + h2_frame(4, 0, 0, struct.pack("!HI", 1, 65536))
                             + h2_frame(8, 0, 0, struct.pack("!I", 15663105))
                             + h2_frame(1, 0x25, 1, struct.pack("!IB", 0x80000000, 255) + block1)
                             + h2_frame(1, 0x4, 3, block3)
                             + h2_frame(0, 0x1, 3, b"q=test&kl="))
                frames, ended = read_frames(sock, {1, 3})
            finally:
                sock.close()
        except (OSError, ssl.SSLError) as exc:
            error = repr(exc)
    thread.join(20)
    rec = load_record(out, "h2") or {}
    h2 = rec.get("h2") or {}
    streams = h2.get("streams") or []
    s1 = streams[0] if streams else {}
    s3 = streams[1] if len(streams) > 1 else {}

    problems = problem_if("port" not in box, "serve did not listen: %s" % box.get("err"))
    problems += problem_if(error is not None, "client error: %s" % error)
    problems += problem_if(thread.is_alive() or box.get("rc") != 0,
                           "serve did not return 0 after one connection: %r" % box.get("rc"))
    problems += problem_if((rec.get("tls") or {}).get("alpn") != "h2",
                           "negotiated ALPN %r" % (rec.get("tls") or {}).get("alpn"))
    problems += problem_if([s.get("stream_id") for s in streams] != [1, 3],
                           "streams recorded: %r" % [s.get("stream_id") for s in streams])
    problems += problem_if(pairs_of(s1.get("headers")) != [
        (":method", "GET"), (":scheme", "https"), (":path", "/"), (":authority", "localhost"),
        ("x-shared", "one"), ("user-agent", TEST_UA)], "stream 1 headers %r" % pairs_of(s1.get("headers")))
    problems += problem_if(s1.get("headers_priority") != {"exclusive": True, "dep": 0, "weight": 256},
                           "stream 1 priority %r" % s1.get("headers_priority"))
    problems += problem_if(ended != {1, 3}, "the client saw streams %r end" % sorted(ended))
    suite.record(GC, "h2-two-streams-one-connection", problems,
                 detail=["end_reason %s, %d frames recorded" % (h2.get("end_reason"),
                                                                 len(h2.get("frames") or []))])

    shared = [h for h in s3.get("headers") or [] if h.get("name") == "x-shared"]
    problems = problem_if(not shared or shared[0].get("value") != "one"
                          or shared[0].get("rep") != "indexed" or shared[0].get("index") != 62,
                          "stream 3's dynamic-table reference decoded to %r" % shared)
    suite.record(GC, "h2-hpack-state-shared-across-streams", problems,
                 detail=["stream 1 inserts x-shared; stream 3 sends only index 62"])

    data = s3.get("data_frames") or []
    problems = problem_if(len(data) != 1 or data[0].get("payload_hex") != b"q=test&kl=".hex(),
                          "stream 3 DATA %r" % data)
    problems += problem_if(s3.get("body_len") != 10 or not s3.get("end_stream"),
                           "stream 3 body_len %r end_stream %r" % (s3.get("body_len"), s3.get("end_stream")))
    problems += problem_if(pairs_of(s3.get("headers"))[:1] != [(":method", "POST")],
                           "stream 3 method %r" % pairs_of(s3.get("headers"))[:1])
    suite.record(GC, "h2-post-data-recorded", problems)

    r1, r3 = s1.get("response") or {}, s3.get("response") or {}
    body1 = b"".join(p for t, _f, sid, p in frames if t == 0 and sid == 1)
    heads = dict((sid, p) for t, _f, sid, p in frames if t == 1)
    problems = problem_if(not str(r1.get("content_type", "")).startswith("text/html")
                          or r1.get("set_cookie_names") != ["sid"],
                          "stream 1 answer %r" % r1)
    problems += problem_if(r3.get("content_type") != "text/plain" or r3.get("set_cookie_names") != [],
                           "stream 3 answer %r" % r3)
    problems += problem_if(body1 != b"<html><body>capture page</body></html>",
                           "the page came back as %r" % body1[:60])
    problems += problem_if(heads.get(1, b"")[:1] != b"\x88" or heads.get(3, b"")[:1] != b"\x88",
                           "a response HEADERS block does not open with :status 200 (0x88)")
    problems += problem_if(rec.get("server_set_cookie_names") != ["sid"],
                           "server_set_cookie_names %r" % rec.get("server_set_cookie_names"))
    problems += problem_if(h2.get("akamai") != "1:65536|15663105|0|m,s,p,a",
                           "akamai %r, from the frames this client sent" % h2.get("akamai"))
    suite.record(GC, "h2-answers-page-cookie-akamai", problems,
                 detail=["akamai %s" % h2.get("akamai")])

    raw = rec.get("client_hello_raw_hex")
    try:
        want = oracle_ja4(oracle_hello(handshake_of_records(bytes.fromhex(raw))))[0] if raw else None
    except Exception as exc:
        want = "oracle failed: %r" % exc
    got = (rec.get("fingerprints") or {}).get("ja4")
    suite.record(GC, "h2-client-hello-fingerprinted", problem_if(
        not raw or got != want, "record JA4 %r, this file's JA4 of the recorded bytes %r" % (got, want)),
        detail=["stdlib client JA4 %s" % got])

    # h1 over TLS: three keep-alive requests on one connection.
    box, thread = start_serve(cc, ["serve", "--port", "0", "--out", out, "--label", "h1",
                                   "--count", "1", "--timeout", "30", "--idle", "2",
                                   "--alpn", "http/1.1"])
    error, bodies = None, []
    if "port" in box:
        conn = http.client.HTTPSConnection("127.0.0.1", box["port"], timeout=5,
                                           context=client_ctx(["http/1.1"]))
        try:
            for method, path, body in (("GET", "/", None), ("POST", "/x", b"a=1"), ("HEAD", "/", None)):
                conn.request(method, path, body=body, headers={"User-Agent": TEST_UA})
                bodies.append(conn.getresponse().read())
        except (OSError, http.client.HTTPException) as exc:
            error = repr(exc)
        finally:
            conn.close()
    thread.join(20)
    rec = load_record(out, "h1") or {}
    reqs = (rec.get("http1") or {}).get("requests") or []
    problems = problem_if("port" not in box or error, "serve %s, client %s" % (box.get("err"), error))
    problems += problem_if([q.get("method") for q in reqs] != ["GET", "POST", "HEAD"],
                           "requests %r" % [q.get("method") for q in reqs])
    problems += problem_if(len(reqs) > 1 and reqs[1].get("body_hex") != b"a=1".hex(),
                           "POST body %r" % (reqs[1].get("body_hex") if len(reqs) > 1 else None))
    problems += problem_if(len(reqs) > 2 and (reqs[2].get("response") or {}).get("body_sent") != 0,
                           "HEAD was answered with a body")
    problems += problem_if(bodies[:2] != [b"ok", b"ok"], "bodies %r" % bodies)
    problems += problem_if((rec.get("tls") or {}).get("alpn") != "http/1.1",
                           "ALPN %r" % (rec.get("tls") or {}).get("alpn"))
    problems += problem_if(box.get("rc") != 0, "serve returned %r" % box.get("rc"))
    suite.record(GC, "h1-tls-keepalive-three-requests", problems)

    # plaintext h1
    box, thread = start_serve(cc, ["serve", "--port", "0", "--out", out, "--label", "plain",
                                   "--count", "1", "--timeout", "30", "--idle", "2", "--plain"])
    error = None
    if "port" in box:
        conn = http.client.HTTPConnection("127.0.0.1", box["port"], timeout=5)
        try:
            conn.request("GET", "/", headers={"Connection": "close", "User-Agent": TEST_UA})
            conn.getresponse().read()
        except (OSError, http.client.HTTPException) as exc:
            error = repr(exc)
        finally:
            conn.close()
    thread.join(20)
    rec = load_record(out, "plain") or {}
    h1 = rec.get("http1") or {}
    problems = problem_if("port" not in box or error, "serve %s, client %s" % (box.get("err"), error))
    problems += problem_if(rec.get("mode") != "plain" or "tls" in rec or "client_hello" in rec,
                           "mode %r, tls/client_hello present: %s" % (rec.get("mode"),
                                                                     "tls" in rec or "client_hello" in rec))
    problems += problem_if(h1.get("request_line") != "GET / HTTP/1.1", "request line %r" % h1.get("request_line"))
    problems += problem_if(h1.get("end_reason") != "connection_close", "end_reason %r" % h1.get("end_reason"))
    problems += problem_if(h1.get("user_agent") != TEST_UA, "user_agent %r" % h1.get("user_agent"))
    suite.record(GC, "plain-h1-request", problems)

    # scripted HRR, fed a raw ClientHello 1
    ch1 = memory_client_hello()
    ch1_hello = oracle_hello(handshake_of_records(ch1))
    box, thread = start_serve(cc, ["serve", "--port", "0", "--out", out, "--label", "hrr",
                                   "--count", "1", "--timeout", "30", "--hrr-group", "0x0017"])
    resp, error = b"", None
    if "port" in box:
        sock = socket.create_connection(("127.0.0.1", box["port"]), timeout=5)
        try:
            sock.sendall(ch1)
            while len(resp) < 5 or len(resp) < 5 + u16(resp, 3):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
        except OSError as exc:
            error = repr(exc)
        finally:
            sock.close()
    thread.join(20)
    rec = load_record(out, "hrr") or {}
    problems = problem_if("port" not in box or error, "serve %s, client %s" % (box.get("err"), error))
    problems += problem_if(HRR_RANDOM != hashlib.sha256(b"HelloRetryRequest").digest(),
                           "the typed HRR marker disagrees with its RFC 8446 definition")
    try:
        if resp[0] != 0x16 or resp[5] != 2:
            raise ValueError("record type %d, handshake type %d" % (resp[0], resp[5]))
        body = resp[9:5 + u16(resp, 3)]
        sid = body[35:35 + body[34]]
        pos = 35 + body[34]
        cipher = u16(body, pos)
        pos += 3
        end = pos + 2 + u16(body, pos)
        pos += 2
        exts = {}
        while pos < end:
            exts[u16(body, pos)] = body[pos + 4:pos + 4 + u16(body, pos + 2)]
            pos += 4 + u16(body, pos + 2)
        problems += problem_if(body[2:34] != HRR_RANDOM, "ServerHello.random is not the HRR marker")
        problems += problem_if(sid != ch1_hello["session_id"], "legacy_session_id not echoed")
        problems += problem_if(cipher not in ch1_hello["ciphers"] or cipher >> 8 != 0x13,
                               "cipher 0x%04x is not an offered TLS 1.3 suite" % cipher)
        problems += problem_if(exts.get(51) != b"\x00\x17", "key_share selects %r" % exts.get(51))
        problems += problem_if(exts.get(43) != b"\x03\x04", "supported_versions %r" % exts.get(43))
        problems += problem_if(not exts.get(44), "no cookie extension")
    except (IndexError, ValueError) as exc:
        problems.append("the HRR does not parse: %r (%d bytes)" % (exc, len(resp)))
    hrr = rec.get("hrr") or {}
    problems += problem_if(hrr.get("group") != "0017" or hrr.get("group_offered") is not True,
                           "record hrr %r" % dict((k, hrr.get(k)) for k in ("group", "group_offered")))
    problems += problem_if((rec.get("fingerprints") or {}).get("ja4") != oracle_ja4(ch1_hello)[0],
                           "record JA4 %r" % (rec.get("fingerprints") or {}).get("ja4"))
    suite.record(GC, "hrr-raw-client-hello-answered", problems,
                 detail=["HRR selects secp256r1 (0x0017), cookie and supported_versions present"])

    # scripted HRR against a real stdlib client, which must send ClientHello 2
    offered = oracle_fields(ch1_hello)
    if 0x0017 not in offered["groups"] or 0x0017 in [g for g, _l in offered["shares"]]:
        skip(suite, GC, "hrr-stdlib-client-sends-ch2",
             "this OpenSSL (%s) does not offer secp256r1 without a key share" % ssl.OPENSSL_VERSION)
    else:
        box, thread = start_serve(cc, ["serve", "--port", "0", "--out", out, "--label", "hrr2",
                                       "--count", "1", "--timeout", "30", "--hrr-group", "0x0017"])
        if "port" in box:
            try:
                sock = client_ctx(None).wrap_socket(
                    socket.create_connection(("127.0.0.1", box["port"]), timeout=5),
                    server_hostname="localhost")
                sock.close()
            except (OSError, ssl.SSLError):
                pass                    # the responder closes after ClientHello 2
        thread.join(20)
        rec = load_record(out, "hrr2") or {}
        hrr = rec.get("hrr") or {}
        problems = problem_if("port" not in box, "serve did not listen: %s" % box.get("err"))
        problems += problem_if(hrr.get("end_reason") != "client_hello_2",
                               "end_reason %r" % hrr.get("end_reason"))
        try:
            ch2 = oracle_hello(handshake_of_records(bytes.fromhex(hrr.get("client_hello_2_raw_hex") or "")))
            shares = [g for g, _l in oracle_fields(ch2)["shares"]]
            cookie = ext_data(ch2, 44)
            want = (struct.pack("!H", len(cc.HRR_COOKIE)) + cc.HRR_COOKIE)
            problems += problem_if(shares != [0x0017], "ClientHello 2 key shares %r" % shares)
            problems += problem_if(cookie != want or hrr.get("cookie_echoed") is not True,
                                   "cookie %r, cookie_echoed %r" % (cookie, hrr.get("cookie_echoed")))
        except (IndexError, ValueError) as exc:
            problems.append("ClientHello 2 does not parse: %r" % exc)
        suite.record(GC, "hrr-stdlib-client-sends-ch2", problems,
                     detail=["CCS before CH2: %s" % hrr.get("ccs_before_client_hello_2")])

    # the CLI entry point, as a child: serve --port 0 --count 1 exits 0
    cli_out = ws.join("cli")
    WRITES.append(cli_out)
    proc = subprocess.Popen([sys.executable, "-B", SOURCE, "serve", "--port", "0", "--out", cli_out,
                             "--label", "smoke", "--count", "1", "--timeout", "30"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=H.child_env())
    rc, error = None, None
    try:
        line = proc.stdout.readline().decode("utf-8", "replace")
        port = int(line.split()[2].rsplit(":", 1)[1])
        conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=client_ctx(["http/1.1"]))
        try:
            conn.request("GET", "/")
            conn.getresponse().read()
        finally:
            conn.close()
        rc = proc.wait(30)
    except (OSError, ValueError, IndexError, http.client.HTTPException, subprocess.TimeoutExpired) as exc:
        error = repr(exc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        proc.stdout.close()
        proc.stderr.close()
    problems = problem_if(error is not None, "driving the child failed: %s" % error)
    problems += problem_if(rc != 0, "the child exited %r" % rc)
    problems += problem_if(not os.path.isfile(os.path.join(cli_out, "smoke-001.json")),
                           "no smoke-001.json written")
    suite.record(GC, "cli-serve-count-1-exits-0", problems,
                 detail=["python3 -B Scripts/chrome_capture.py serve --port 0 --count 1"])

    # F44: a record carries localhost cookies and request bodies, so serve makes
    # the --out directory it creates 0700 and writes every record 0600.
    smoke = os.path.join(cli_out, "smoke-001.json")
    dir_mode = os.stat(cli_out).st_mode & 0o777 if os.path.isdir(cli_out) else None
    rec_mode = os.stat(smoke).st_mode & 0o777 if os.path.isfile(smoke) else None
    h2_rec = os.path.join(out, "h2-001.json")
    h2_mode = os.stat(h2_rec).st_mode & 0o777 if os.path.isfile(h2_rec) else None
    problems = problem_if(dir_mode != 0o700, "the --out directory serve created is %s, not 0700"
                          % (oct(dir_mode) if dir_mode is not None else "missing"))
    problems += problem_if(rec_mode != 0o600, "smoke-001.json is %s, not 0600"
                           % (oct(rec_mode) if rec_mode is not None else "missing"))
    problems += problem_if(h2_mode != 0o600, "h2-001.json (pre-existing --out) is %s, not 0600"
                           % (oct(h2_mode) if h2_mode is not None else "missing"))
    suite.record(GC, "serve-record-owner-only", problems,
                 detail=["created --out 0700; each record opened O_CREAT 0600"])

    problems = []
    for i, label in enumerate(("../escape", "a%sb" % os.sep, "..", "x..y")):
        bad_out = ws.join("label", "%d" % i)
        WRITES.append(bad_out)
        opts = cc.build_parser().parse_args(["serve", "--port", "0", "--out", bad_out, "--label", label,
                                             "--count", "1", "--timeout", "5"])
        try:
            cc.serve(opts, out=io.StringIO())
            problems.append("%r: serve returned instead of refusing" % label)
        except cc.CaptureConfigError as exc:
            if "label" not in str(exc):
                problems.append("%r: refused with %r" % (label, str(exc)))
        if os.path.exists(bad_out):
            problems.append("%r: the output directory was created" % label)
    suite.record(GC, "serve-refuses-path-label", problems,
                 detail=["a --label carrying %r or '..' is refused before anything is created" % os.sep])

    # F34: the akamai string is built from raw HPACK pseudo-header names, which a
    # local peer chooses; the summary line must not carry its control bytes.
    rec = {"index": 1, "peer": "127.0.0.1:1", "mode": "tls",
           "h2": {"streams": [], "akamai": "1:65536|0|0|m,:\x1b]0;pwn\x07\x1b[2J,\x7fp",
                  "user_agent": "ua\x1b[31m"},
           "http_error": "boom\x1b[1m\nsecond"}
    line = cc.summary_line(rec)
    raw = sorted(set("%02x" % ord(c) for c in line if ord(c) < 0x20 or ord(c) == 0x7f))
    problems = problem_if(raw, "the summary line carries raw control bytes %s: %r" % (raw, line))
    problems += problem_if("akamai=1:65536|0|0|m," not in line, "the akamai prefix is gone: %r" % line)
    suite.record(GC, "summary-line-escapes-control-bytes", problems,
                 detail=["ESC, BEL and DEL in a pseudo-header name reach the terminal escaped"])


# --- D. diff and export -------------------------------------------------------

def fixed_rows(stdout):
    return [line.split(" ", 2)[2] for line in stdout.splitlines() if line.startswith("FIXED ")]


def planted(fx, headers=None, head=None, cookies=("a",)):
    """A capture record built from the fixture: TLS ClientHello plus one request."""
    rec = {"client_hello_hex": fx["client_hello_hex"], "server_set_cookie_names": list(cookies),
           "mode": "tls", "index": 1}
    if headers is not None:
        rec["tls"] = {"version": "TLSv1.3", "alpn": "h2"}
        rec["h2"] = {"frames": [], "streams": [
            {"stream_id": 1, "header_block_hex": request_block(headers).hex(), "end_stream": True}]}
    if head is not None:
        rec["tls"] = {"version": "TLSv1.3", "alpn": "http/1.1"}
        rec["http1"] = {"requests": [{"request_head": head}]}
    return rec


def nav_headers(extra):
    return [(":method", "GET"), (":authority", "localhost:8443"), (":scheme", "https"),
            (":path", "/"), ("user-agent", CHROME_UA)] + list(extra)


def nav_head(extra):
    return "\r\n".join(["GET / HTTP/1.1", "Host: localhost:8443", "User-Agent: %s" % CHROME_UA]
                       + ["%s: %s" % kv for kv in extra])


def write_json(ws, rel, obj):
    return ws.write_text(rel, json.dumps(obj, indent=1))


def group_diff_export(suite, cc, ws):
    with open(FIXTURE, encoding="utf-8") as fh:
        fx = json.load(fh)
    hs = bytes.fromhex(fx["client_hello_hex"])
    hello = oracle_hello(hs)

    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", FIXTURE])
    suite.record(GD, "diff-self-exits-0", problem_if(
        rc != 0 or "no fixed difference" not in out, "rc=%r %r" % (rc, (out + err)[:200])))

    victim = 0x0804
    parsed = cc.parse_client_hello(hs[4:])
    parsed.pop("_ciphers_raw", None)
    parsed.pop("_legacy_version_raw", None)
    mutated = copy.deepcopy(fx)
    for ext in parsed["extensions"]:
        if ext["type"] == 13:
            ext["decoded"]["algorithms"] = [a for a in ext["decoded"]["algorithms"]
                                            if int(a["id"], 16) != victim]
    mutated["client_hello"] = parsed
    cand = write_json(ws, "diff/decoded.json", mutated)
    report = ws.join("diff", "decoded.md")
    WRITES.append(report)
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", cand, "--out", report])
    rows = fixed_rows(out)
    problems = problem_if(rc != 1, "rc=%r, wanted 1: %r" % (rc, (out + err)[:200]))
    problems += problem_if("ext.13 (signature_algorithms)" not in rows,
                           "the removed sigalg is not named: %r" % rows)
    problems += problem_if(set(rows) - {"ext.13 (signature_algorithms)", "tls.ja4"},
                           "rows beyond the sigalg and the JA4 it feeds: %r" % rows)
    text = open(report, encoding="utf-8").read() if os.path.isfile(report) else ""
    problems += problem_if("## varying" not in text, "the report has no varying section")
    suite.record(GD, "diff-one-sigalg-removed-decoded", problems,
                 detail=["removed 0x%04x from the decoded client_hello; FIXED: %s" % (victim, rows)])

    exts = []
    for etype, data in hello["extensions"]:
        if etype == 13:
            algs = [a for a in u16s(data[2:]) if a != victim]
            data = struct.pack("!H", 2 * len(algs)) + b"".join(struct.pack("!H", a) for a in algs)
        exts.append((etype, data))
    cut = rebuild_hello(hello, exts=exts)
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = cut.hex()
    cand = write_json(ws, "diff/bytes.json", mutated)
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", cand,
                                 "--out", ws.join("diff", "bytes.md")])
    WRITES.append(ws.join("diff", "bytes.md"))
    rows = fixed_rows(out)
    problems = problem_if(len(cut) != len(hs) - 2, "the surgery removed %d bytes" % (len(hs) - len(cut)))
    problems += problem_if(victim in oracle_fields(oracle_hello(cut))["sigalgs"], "the sigalg survived")
    problems += problem_if(rc != 1 or "ext.13 (signature_algorithms)" not in rows,
                           "rc=%r rows %r" % (rc, rows))
    suite.record(GD, "diff-one-sigalg-removed-bytes", problems,
                 detail=["the ClientHello bytes cut by this file, every length recomputed"])

    host = b"capture.localhost"
    exts = []
    for etype, data in hello["extensions"]:
        if etype == 0:
            entry = b"\x00" + struct.pack("!H", len(host)) + host
            data = struct.pack("!H", len(entry)) + entry
        exts.append((etype, data))
    renamed = rebuild_hello(hello, exts=exts)
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = renamed.hex()
    cand = write_json(ws, "diff/sni.json", mutated)
    report = ws.join("diff", "sni.md")
    WRITES.append(report)
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", cand, "--out", report])
    text = open(report, encoding="utf-8").read() if os.path.isfile(report) else ""
    grown = len(renamed) - len(hs)
    no_sni = cc.parse_client_hello(hs[4:])
    no_sni["extensions"] = [e for e in no_sni["extensions"] if e["type"] != 0]
    problems = problem_if(grown != len(host) - len(b"localhost"), "the SNI surgery grew %d bytes" % grown)
    problems += problem_if(rc != 0, "an SNI-length-only change was reported as fixed: rc=%r %r"
                           % (rc, fixed_rows(out) or (out + err)[:200]))
    problems += problem_if("| tls.clienthello_len" in text or "## varying" not in text,
                           "a ClientHello length row still differs, or no report was written")
    problems += problem_if(cc._diff_sni_bytes(no_sni) != 0, "no SNI (IP literal) does not subtract 0")
    suite.record(GD, "diff-sni-length-is-not-fixed", problems,
                 detail=["SNI localhost -> capture.localhost (+%d bytes), every length recomputed" % grown])

    exts = [(etype, data + b"\x00" if etype == 23 else data) for etype, data in hello["extensions"]]
    grown_hello = rebuild_hello(hello, exts=exts)
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = grown_hello.hex()
    cand = write_json(ws, "diff/extbyte.json", mutated)
    report = ws.join("diff", "extbyte.md")
    WRITES.append(report)
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", cand, "--out", report])
    text = open(report, encoding="utf-8").read() if os.path.isfile(report) else ""
    rows = fixed_rows(out)
    problems = problem_if(len(grown_hello) != len(hs) + 1, "the surgery grew %d bytes" % (len(grown_hello) - len(hs)))
    problems += problem_if(rc != 1 or not any(r.startswith("ext.23") for r in rows), "rc=%r rows %r" % (rc, rows))
    problems += problem_if("| tls.clienthello_len_minus_sni |" not in text,
                           "a non-SNI length change is not reported")
    suite.record(GD, "diff-extension-byte-still-reported", problems,
                 detail=["control: one byte appended to extended_master_secret (23); FIXED: %s" % rows])

    # A resumed connection: the fixture's hello plus a pre_shared_key (41) as the
    # last extension (RFC 8446 4.2.11): one 32-byte identity, one 32-byte binder.
    identity = struct.pack("!H", 32) + b"\x11" * 32 + struct.pack("!I", 0)
    psk = (struct.pack("!H", len(identity)) + identity
           + struct.pack("!H", 33) + b"\x20" + b"\x22" * 32)
    fresh_exts = list(hello["extensions"])
    resumed = rebuild_hello(hello, exts=fresh_exts + [(41, psk)])
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = resumed.hex()
    write_json(ws, "diff/mixed/a-fresh.json", fx)
    write_json(ws, "diff/mixed/b-resumed.json", mutated)
    mixed = ws.join("diff", "mixed")
    only_resumed = write_json(ws, "diff/resumed-only.json", mutated)
    fwd = ws.join("diff", "unmatched-fwd.md")
    rev = ws.join("diff", "unmatched-rev.md")
    WRITES.extend([fwd, rev])
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", mixed, "--out", fwd])
    rc_rev, out_rev, err_rev = run_main(cc, ["diff", "--reference", mixed, "--candidate", FIXTURE, "--out", rev])
    rc_none, out_none, err_none = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", only_resumed])
    want_fwd = "UNMATCHED resumed: present only in candidate (1 connection); not compared"
    want_rev = "UNMATCHED resumed: present only in reference (1 connection); not compared"
    problems = problem_if(any(t == 41 for t, _d in fresh_exts), "the fixture already carries a pre_shared_key")
    problems += problem_if(rc != 0 or fixed_rows(out), "a one-sided resumed group was reported as fixed: rc=%r %r"
                           % (rc, fixed_rows(out) or (out + err)[:200]))
    problems += problem_if(want_fwd not in out.splitlines(), "no UNMATCHED line for the candidate: %r" % out[:300])
    problems += problem_if(rc_rev != 0 or fixed_rows(out_rev), "reverse: a one-sided resumed group was fixed: rc=%r %r"
                           % (rc_rev, fixed_rows(out_rev) or (out_rev + err_rev)[:200]))
    problems += problem_if(want_rev not in out_rev.splitlines(), "no UNMATCHED line for the reference: %r"
                           % out_rev[:300])
    problems += problem_if(rc_none != 2 or "no connection group in common" not in err_none,
                           "no group in common was not refused: rc=%r %r" % (rc_none, (out_none + err_none)[:200]))
    suite.record(GD, "diff-unmatched-resumed-is-not-fixed", problems,
                 detail=["candidate fresh+resumed vs reference fresh, both directions; resumed-only vs fresh "
                         "refused (exit 2)"])

    cut_exts = []
    for etype, data in fresh_exts:
        if etype == 13:
            algs = [a for a in u16s(data[2:]) if a != victim]
            data = struct.pack("!H", 2 * len(algs)) + b"".join(struct.pack("!H", a) for a in algs)
        cut_exts.append((etype, data))
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = rebuild_hello(hello, exts=cut_exts + [(41, psk)]).hex()
    write_json(ws, "diff/mixed-cut/a-fresh.json", fx)
    write_json(ws, "diff/mixed-cut/b-resumed.json", mutated)
    report = ws.join("diff", "resumed-cut.md")
    WRITES.append(report)
    rc, out, err = run_main(cc, ["diff", "--reference", mixed, "--candidate", ws.join("diff", "mixed-cut"),
                                 "--out", report])
    lines = [line for line in out.splitlines() if line.startswith("FIXED ")]
    problems = problem_if(rc != 1, "rc=%r, wanted 1: %r" % (rc, (out + err)[:200]))
    problems += problem_if("FIXED resumed ext.13 (signature_algorithms)" not in lines,
                           "the resumed group's removed sigalg is not FIXED: %r" % lines)
    problems += problem_if(any(not line.startswith("FIXED resumed ") for line in lines),
                           "a FIXED row outside the resumed group: %r" % lines)
    problems += problem_if("UNMATCHED" in out, "a group present on both sides was reported unmatched")
    suite.record(GD, "diff-matched-resumed-still-fixed", problems,
                 detail=["control: both sides fresh+resumed, 0x%04x removed from the candidate's resumed "
                         "hello; FIXED: %s" % (victim, lines)])

    body = hello["body"]
    pos = hello["cipher_pos"] + 2
    first = u16(body, pos)
    swapped = 0x2A2A if first != 0x2A2A else 0x3A3A
    patched = rebuild_hello(hello, body_patch=body[:pos] + struct.pack("!H", swapped) + body[pos + 2:])
    mutated = copy.deepcopy(fx)
    mutated["client_hello_hex"] = patched.hex()
    cand = write_json(ws, "diff/grease.json", mutated)
    rc, out, err = run_main(cc, ["diff", "--reference", FIXTURE, "--candidate", cand])
    problems = problem_if(not is_grease(first), "the first cipher suite 0x%04x is not GREASE" % first)
    problems += problem_if(rc != 0, "a GREASE-only change was reported as fixed: rc=%r %r"
                           % (rc, fixed_rows(out) or out[:200]))
    suite.record(GD, "diff-grease-change-is-not-fixed", problems,
                 detail=["control: GREASE cipher 0x%04x -> 0x%04x" % (first, swapped)])

    good = planted(fx, headers=nav_headers([("referer", "https://localhost:8443/"), ("cookie", "a=1")]))
    src = write_json(ws, "export/good/capture.json", good)
    dest = ws.join("export", "good-out")
    WRITES.append(dest)
    rc, out, err = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "cookie"])
    written = os.path.join(dest, "cookie-001.json")
    problems = problem_if(rc != 0 or not os.path.isfile(written), "rc=%r %r" % (rc, err.strip()))
    if os.path.isfile(written):
        with open(written, encoding="utf-8") as fh:
            got = json.load(fh)
        stream = ((got.get("h2") or {}).get("streams") or [{}])[0]
        problems += problem_if((got.get("fingerprints") or {}).get("ja4") != PINNED_JA4,
                               "exported JA4 %r" % (got.get("fingerprints") or {}).get("ja4"))
        problems += problem_if((got.get("meta") or {}).get("chrome_version") != "153.0.0.0",
                               "meta.chrome_version %r" % (got.get("meta") or {}).get("chrome_version"))
        problems += problem_if(("cookie", "a=1") not in pairs_of(stream.get("headers")),
                               "the server-set cookie did not survive: %r" % pairs_of(stream.get("headers")))
    suite.record(GD, "export-accepts-loopback-capture", problems,
                 detail=["control: loopback referer and a cookie the server set"])

    shapes = (("h2 cookie", planted(fx, headers=nav_headers([("cookie", "a=1; SID=x")]))),
              ("h1 cookie", planted(fx, head=nav_head([("Cookie", "SID=x")]))))
    problems = []
    for what, rec in shapes:
        tag = what.replace(" ", "-")
        src = write_json(ws, "export/%s/capture.json" % tag, rec)
        dest = ws.join("export", tag + "-out")
        WRITES.append(dest)
        rc, out, err = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "cookie"])
        if rc != 1 or "cookie" not in err or "'SID'" not in err:
            problems.append("%s: rc=%r %r" % (what, rc, err.strip()))
        if os.path.exists(dest):
            problems.append("%s: something was written" % what)
    suite.record(GD, "export-refuses-foreign-cookie", problems,
                 detail=["cookie SID was not set by the capture server (it set: a)"])

    shapes = (("h2 referer", planted(fx, headers=nav_headers([("referer", "https://www.example.com/")]))),
              ("h1 referer", planted(fx, head=nav_head([("Referer", "https://www.example.com/")]))))
    problems = []
    for what, rec in shapes:
        tag = what.replace(" ", "-")
        src = write_json(ws, "export/%s/capture.json" % tag, rec)
        dest = ws.join("export", tag + "-out")
        WRITES.append(dest)
        rc, out, err = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "navigate"])
        if rc != 1 or "referer" not in err or "www.example.com" not in err:
            problems.append("%s: rc=%r %r" % (what, rc, err.strip()))
        if os.path.exists(dest):
            problems.append("%s: something was written" % what)
    suite.record(GD, "export-refuses-non-loopback-referer", problems)

    write_json(ws, "export/mixed/1-good.json", good)
    write_json(ws, "export/mixed/2-bad.json",
               planted(fx, headers=nav_headers([("referer", "https://www.example.com/")])))
    dest = ws.join("export", "mixed-out")
    WRITES.append(dest)
    rc, out, err = run_main(cc, ["export", "--from", ws.join("export", "mixed"), "--to", dest,
                                 "--label", "navigate"])
    suite.record(GD, "export-all-or-nothing", problem_if(
        rc != 1 or os.path.exists(dest), "rc=%r, destination exists: %s" % (rc, os.path.exists(dest))),
        detail=["one good and one refused capture: nothing is written"])

    dest = ws.join("export", "good-out")
    rc, out, err = run_main(cc, ["export", "--from", ws.join("export", "good", "capture.json"),
                                 "--to", dest, "--label", "cookie"])
    rc_force, _o, err_force = run_main(cc, ["export", "--from", ws.join("export", "good", "capture.json"),
                                            "--to", dest, "--label", "cookie", "--force"])
    problems = problem_if(rc != 2 or "exists" not in err, "overwrite without --force: rc=%r %r" % (rc, err.strip()))
    problems += problem_if(rc_force != 0, "--force: rc=%r %r" % (rc_force, err_force.strip()))
    suite.record(GD, "export-overwrite-needs-force", problems)

    # A planted <fixture>.tmp symlink must not redirect the export write: the stale
    # .tmp is unlinked and the new one is created O_EXCL|O_NOFOLLOW, mode 0600.
    victim = ws.join("export", "victim.txt")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("untouched\n")
    tmp = os.path.join(dest, "cookie-001.json.tmp")
    os.symlink(victim, tmp)
    rc, out, err = run_main(cc, ["export", "--from", ws.join("export", "good", "capture.json"),
                                 "--to", dest, "--label", "cookie", "--force"])
    with open(victim, encoding="utf-8") as fh:
        victim_text = fh.read()
    written = os.path.join(dest, "cookie-001.json")
    problems = problem_if(rc != 0, "rc=%r %r" % (rc, err.strip()))
    problems += problem_if(victim_text != "untouched\n", "the write followed the .tmp symlink")
    problems += problem_if(os.path.lexists(tmp), "the .tmp was left behind")
    problems += problem_if(os.path.islink(written) or not os.path.isfile(written), "no regular fixture written")
    if os.path.isfile(written):
        mode = os.stat(written).st_mode & 0o777
        problems += problem_if(mode != 0o600, "fixture mode %o, want 600" % mode)
    suite.record(GD, "export-tmp-never-follows-a-symlink", problems,
                 detail=["planted cookie-001.json.tmp -> victim.txt; victim unchanged, fixture 0600"])

    # F45: credential-bearing request headers are refused, whatever the host.
    problems = []
    for name in ("authorization", "Proxy-Authorization", "x-client-data", "x-api-key", "x-csrf-token",
                 "x-goog-authuser"):
        for what, rec in (("h2", planted(fx, headers=nav_headers([(name.lower(), "secret")]))),
                          ("h1", planted(fx, head=nav_head([(name, "secret")])))):
            try:
                cc.build_fixture(rec, "navigate", "capture.json")
                problems.append("%s %s: exported" % (what, name))
            except cc.ExportRefused as exc:
                if name.lower() not in str(exc).lower() or "secret" in str(exc):
                    problems.append("%s %s: refused with %r" % (what, name, str(exc)))
    src = write_json(ws, "export/auth/capture.json",
                     planted(fx, headers=nav_headers([("authorization", "Bearer secret")])))
    dest = ws.join("export", "auth-out")
    WRITES.append(dest)
    rc, out, err = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "navigate"])
    problems += problem_if(rc != 1 or "authorization" not in err or os.path.exists(dest),
                           "CLI: rc=%r %r, destination exists: %s" % (rc, err.strip(), os.path.exists(dest)))
    suite.record(GD, "export-refuses-credential-headers", problems,
                 detail=["authorization, proxy-authorization, x-client-data, x-api-key, *-token, *auth*; "
                         "the value is never echoed"])

    # F45: a cookie the server set must carry the value the server set, when the
    # record says what that was.
    problems = []
    for value, want_ok in (("1", True), ("2", False)):
        rec = planted(fx, headers=nav_headers([("cookie", "a=%s" % value)]))
        rec["server_set_cookies"] = {"a": "1"}
        try:
            cc.build_fixture(rec, "cookie", "capture.json")
            problems += problem_if(not want_ok, "cookie a=%s exported though the server set a=1" % value)
        except cc.ExportRefused as exc:
            problems += problem_if(want_ok or "'a'" not in str(exc),
                                   "cookie a=%s refused with %r" % (value, str(exc)))
    suite.record(GD, "export-refuses-foreign-cookie-value", problems,
                 detail=["server set a=1: a=1 exported, a=2 refused"])

    # F45: a request body is exported only when it is one the scripted sets send
    # (EXPORT_SCRIPTED_BODIES) or one the operator allowed with --allow-body.
    def with_body(body, truncated=False):
        rec = planted(fx, headers=nav_headers([]))
        rec["h2"]["streams"][0]["data_frames"] = [
            {"length": len(body), "flags": 1, "payload_len": len(body), "payload_hex": body.hex(),
             "payload_truncated": truncated}]
        return rec

    problems = []
    try:
        got = cc.build_fixture(with_body(b"q=test&kl="), "cors-post", "capture.json")
        frames = got["h2"]["streams"][0]["data_frames"]
        problems += problem_if(frames[0].get("payload_hex") != b"q=test&kl=".hex(),
                               "the scripted body did not survive: %r" % frames)
    except cc.ExportRefused as exc:
        problems.append("the scripted body was refused: %s" % exc)
    shapes = [("h2 secret body", with_body(b"password=hunter2"), None),
              ("h2 truncated scripted prefix", with_body(b"q=test&kl=", truncated=True), None)]
    h1rec = planted(fx, head="POST / HTTP/1.1\r\nHost: localhost:8443")
    h1rec["http1"]["requests"][0].update({"body_len": 16, "body_hex": b"password=hunter2".hex()})
    shapes.append(("h1 secret body", h1rec, None))
    smuggled = planted(fx, headers=nav_headers([]))
    smuggled["h2"]["frames"] = [{"type": 0, "name": "DATA", "flags": 1, "stream_id": 1, "length": 16,
                                 "payload_hex": b"password=hunter2".hex()}]
    shapes.append(("h2 DATA payload in the frame list", smuggled, None))
    for what, rec, allow in shapes:
        try:
            cc.build_fixture(rec, "cors-post", "capture.json", allowed_bodies=allow)
            problems.append("%s: exported" % what)
        except cc.ExportRefused as exc:
            problems += problem_if("body" not in str(exc) and "payload" not in str(exc),
                                   "%s: refused with %r" % (what, str(exc)))
    try:
        cc.build_fixture(with_body(b"password=hunter2"), "cors-post", "capture.json",
                         allowed_bodies=[b"password=hunter2"])
    except cc.ExportRefused as exc:
        problems.append("an operator-allowed body was refused: %s" % exc)
    src = write_json(ws, "export/body/capture.json", with_body(b"a=1"))
    dest = ws.join("export", "body-out")
    WRITES.append(dest)
    rc, _o, err = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "cors-post"])
    rc_ok, _o, err_ok = run_main(cc, ["export", "--from", src, "--to", dest, "--label", "cors-post",
                                      "--allow-body", "a=1"])
    problems += problem_if(rc != 1 or "body" not in err, "CLI without --allow-body: rc=%r %r" % (rc, err.strip()))
    problems += problem_if(rc_ok != 0, "CLI with --allow-body a=1: rc=%r %r" % (rc_ok, err_ok.strip()))
    suite.record(GD, "export-refuses-unscripted-body", problems,
                 detail=["scripted q=test&kl= exported; other, truncated or frame-list bodies refused; "
                         "--allow-body admits one"])

    # The committed Chrome fixture sets re-export to themselves: the refusals
    # above must not reject, nor alter, what was measured and committed. Two
    # things cannot round-trip and are left out of the comparison: meta (the
    # capture time and source record are not in a fixture) and the TLS record
    # lengths (a fixture stores the handshake without its record framing).
    unframed = ("meta", "client_hello_record_lengths")
    chrome_root = H.repo_path("tests", "files", "chrome")
    checked, problems = 0, []
    for dirpath, dirnames, filenames in os.walk(chrome_root):
        dirnames.sort()
        for fname in sorted(f for f in filenames if f.endswith(".json")):
            path = os.path.join(dirpath, fname)
            with open(path, encoding="utf-8") as fh:
                stored = json.load(fh)
            meta = stored.get("meta") or {}
            try:
                again = cc.build_fixture(stored, stored.get("label"), meta.get("source"),
                                         meta.get("chrome_version"))
            except cc.ExportRefused as exc:
                problems.append("%s: refused: %s" % (os.path.relpath(path, chrome_root), exc))
                continue
            checked += 1
            stored_cmp = dict((k, v) for k, v in stored.items() if k not in unframed)
            again_cmp = dict((k, v) for k, v in (again or {}).items() if k not in unframed)
            for side in (stored_cmp, again_cmp):
                if isinstance(side.get("hrr"), dict):
                    side["hrr"] = dict((k, v) for k, v in side["hrr"].items()
                                       if k != "client_hello_2_record_lengths")
            if stored_cmp != again_cmp:
                keys = sorted(k for k in set(stored_cmp) | set(again_cmp) if stored_cmp.get(k) != again_cmp.get(k))
                problems.append("%s: re-export differs in %s" % (os.path.relpath(path, chrome_root), keys))
    if not checked and not problems:
        skip(suite, GD, "export-committed-fixtures-unchanged", "no fixture under tests/files/chrome")
    else:
        suite.record(GD, "export-committed-fixtures-unchanged", problems[:10] + (
            ["... %d more" % (len(problems) - 10)] if len(problems) > 10 else []),
            detail=["%d committed fixture(s) re-exported identically" % checked])


# --- E. bind ------------------------------------------------------------------

def group_bind(suite, cc, ws):
    problems = []
    for host in ("0.0.0.0", "192.0.2.1", "::", "::1", "localhost", "127.0.0.2"):
        out = ws.join("bind", host.replace(":", "_") or "empty")
        WRITES.append(out)
        opts = cc.build_parser().parse_args(["serve", "--port", "0", "--out", out, "--label", "x",
                                             "--host", host, "--count", "1", "--timeout", "5"])
        try:
            cc.serve(opts, out=io.StringIO())
            problems.append("%s: serve returned instead of refusing" % host)
        except cc.CaptureConfigError as exc:
            if "bind" not in str(exc):
                problems.append("%s: refused with %r" % (host, str(exc)))
        if os.path.exists(out):
            problems.append("%s: the output directory was created" % host)
    suite.record(GE, "serve-refuses-non-loopback-host", problems,
                 detail=["0.0.0.0, 192.0.2.1, ::, ::1, localhost, 127.0.0.2"])

    out = ws.join("bind", "main")
    WRITES.append(out)
    rc, _out, err = run_main(cc, ["serve", "--port", "0", "--out", out, "--label", "x",
                                  "--host", "0.0.0.0", "--count", "1", "--timeout", "5"])
    suite.record(GE, "main-exits-2-on-non-loopback-host", problem_if(
        rc != 2 or "bind" not in err or os.path.exists(out), "rc=%r %r" % (rc, err.strip())))


# --- F. hygiene ---------------------------------------------------------------

def group_hygiene(suite, ws, pyc_before, tree_before, fixture_before):
    pyc_after = H.pycache_snapshot()
    suite.record(GF, "pycache-zero", problem_if(
        pyc_before or pyc_after, "expected zero .pyc in the tree, saw %d before and %d after"
        % (len(pyc_before), len(pyc_after))))

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GF, "no-new-repo-paths", problem_if(
        added, "%d new path(s): %s" % (len(added), added[:5])),
        detail=["the scratch area is excluded by _harness.repo_tree"])

    root = os.path.realpath(ws.path) + os.sep
    repo = os.path.realpath(H.REPO_ROOT) + os.sep
    stray = [p for p in WRITES if not os.path.realpath(p).startswith(root)]
    problems = problem_if(stray, "outputs outside the workspace: %s" % stray[:5])
    problems += problem_if(root.startswith(repo) and not root.startswith(os.path.realpath(SCRATCH) + os.sep),
                           "the workspace %s is inside the repo but not under .claude/tmp" % ws.path)
    problems += problem_if(H.file_digests(FIXTURE_DIR) != fixture_before,
                           "the committed fixture directory changed")
    suite.record(GF, "writes-confined-to-workspace", problems,
                 detail=["%d output path(s), all under the mkdtemp workspace" % len(WRITES)])

    tree = ast.parse(open(SOURCE, encoding="utf-8").read(), SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    ours = sorted(n for n in imported if n.startswith("_mcp") or n.startswith("mcp")
                  or os.path.isfile(H.repo_path("Scripts", n + ".py")))
    problems = problem_if(ours, "the oracle imports repo modules: %s" % ours)
    loose = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign)):
            continue
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
            continue
        loose.append("%s at line %d" % (type(node).__name__, node.lineno))
    problems += problem_if(loose, "top-level statements that run at import: %s" % loose)
    suite.record(GF, "oracle-independent-and-import-safe", problems,
                 detail=["imports: %s" % ", ".join(sorted(imported))])


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="the Chrome capture harness: fixture, HPACK, serve, diff/export",
                    opts=opts, mode="grouped")
    del WRITES[:]
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    fixture_before = H.file_digests(FIXTURE_DIR)
    cc = H.load_module_from_path("chrome_capture_under_test", SOURCE)
    ws = H.TempWorkspace("ph-chrome-capture-", keep=opts.keep)
    try:
        group_fixture(suite, cc)
        group_hpack(suite, cc)
        group_serve(suite, cc, ws)
        group_diff_export(suite, cc, ws)
        group_bind(suite, cc, ws)
        group_hygiene(suite, ws, pyc_before, tree_before, fixture_before)
    finally:
        ws.cleanup()
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
