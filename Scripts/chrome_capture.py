#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Chrome capture harness: record what a real browser puts on the wire, on loopback only.

The domain is ONE question: what does Chrome actually send -- the TLS ClientHello
(and, after a scripted HelloRetryRequest, the second one), the HTTP/2 connection
preface and every HEADERS/DATA frame of every stream, or the HTTP/1.1 request
head over TLS or plaintext. The stdlib Chrome client (`Scripts/_mcp_chrome.py`)
is measured against these captures; this file is the INDEPENDENT oracle it is
measured with, so it shares no code with that client and must never import it.

**Two roles.**
- A library: `parse_client_hello`, `fingerprints` (JA3, JA4 and its raw/original
  variants, a peetprint-like string), `peek_client_hello`, `HpackDecoder` and
  `huffman_decode` are importable. The module is import-safe -- no socket, file,
  subprocess or print happens at import time; `main()` runs only under
  `__main__` -- so a test suite can load it by path.
- A tool: `chrome_capture.py serve ...` listens on 127.0.0.1 and writes one JSON
  record per accepted connection; `diff`, `ja4` and `export` read those records
  (and `diff`/`ja4` also read exported fixtures) and never touch the network.

**diff / ja4 / export.**
- `diff --reference DIR --candidate DIR [--out FILE]` compares GREASE-normalised,
  randomness-stripped features (TLS, the post-HRR ClientHello as `ch2.*`, the h2
  preface and first HEADERS block, the first HTTP/1.1 request). Exit 0 = no
  fixed difference, 1 = at least one, each named in the report. The report's
  "varying" section lists what is normalised away (declared) and what took more
  than one value on one side (measured). A decoded `client_hello` in a record
  wins over its bytes, so a JSON-level edit is compared as edited. Each side is
  split into a `fresh` and a `resumed` group (pre_shared_key, ext 41, present);
  only groups present on both sides are compared. A group on one side only is
  reported UNMATCHED and never counts as a fixed difference; two sides with no
  group in common are refused (exit 2) rather than reported as "no difference".
- `ja4 FILE` prints the JA4 `fingerprints` computes from the file's ClientHello
  bytes (plus `<ja4> client_hello_2` after an HRR).
- `export --from DIR --to DIR --label L` writes one reduced fixture per connection
  from an allowlist of fields. It REFUSES (exit 1, naming the field, writing
  nothing) a capture whose :authority/Host/Origin/Referer/request-target/SNI
  names anything but localhost, *.localhost or 127.0.0.1; whose cookie header
  carries a name the capture server did not set (`server_set_cookie_names`) or,
  when the record says what the server set (`server_set_cookies`), another value;
  that carries a credential header (authorization, proxy-authorization,
  x-client-data, x-api-key, any `*-token` or `*auth*` name); or whose request
  body (DATA payloads, h1 body_hex) is truncated, or non-empty and neither one of
  EXPORT_SCRIPTED_BODIES nor an `--allow-body` the operator named. A fixture that
  would contain this machine's home or repository path or its hostname is
  refused too. Anything else on the allowlist -- other header values, unknown
  frame payloads -- is copied as sent: review the diff before committing.
- `serve` writes each record owner-only (0600) into an --out directory it
  creates 0700, and refuses a --label carrying a path separator or "..".
  The summary line escapes control bytes in peer-chosen text.

**serve modes.**
- TLS (default): peek the ClientHello, complete a stdlib `ssl` handshake with a
  localhost certificate, then speak h2 or http/1.1 by the negotiated ALPN
  (`--alpn` picks what the server offers). The h2 side keeps the connection open
  for many streams, records every HEADERS block per stream (one HPACK decoder
  shared across streams, as the protocol requires), DATA frames (length, flags,
  payload hex up to 4 KiB), RST_STREAM, PRIORITY and SETTINGS ACKs, answers each
  stream with 200 and a body, and sends GOAWAY only when it stops on the idle
  window or the overall deadline.
- `--plain`: plaintext HTTP/1.1 (the `http://` navigation shape).
- `--hrr-group G`: a scripted HelloRetryRequest responder. It peeks and consumes
  ClientHello 1, answers with a plaintext HRR selecting group G plus a cookie
  extension, records any ChangeCipherSpec and ClientHello 2, and closes. No TLS
  library is involved.
- `--page PATH` is served as text/html for a GET navigation (`/`, or any GET that
  carries `sec-fetch-mode: navigate`), so a page script can `fetch()` on the same
  origin and connection. `--set-cookie NAME=VALUE` makes a GET of `/` set that
  cookie; every record lists the names the server sets, so an exporter can tell
  a server-set cookie from a foreign one.

**What it refuses.** Any bind host but 127.0.0.1; a ClientHello record, a
handshake message, an h2 frame, a header block, an HTTP/1.1 head or body above
the ceilings named below; an HPACK integer above 2**32 or a table-size update
above what the server advertised.

**One subprocess.** When no certificate is given and the committed test leaf
(`tests/files/tls/localhost-cert.pem` + `localhost-key.pem`) is absent, one is
generated into `.claude/tmp/chrome_capture/` with `openssl req`, with an explicit
`stdin=subprocess.DEVNULL`. Nothing else is spawned.

Stdlib only; runs on Python 3.9.
"""
import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
import traceback
import urllib.parse
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_CERT = os.path.join(REPO_ROOT, "tests", "files", "tls", "localhost-cert.pem")
FIXTURE_KEY = os.path.join(REPO_ROOT, "tests", "files", "tls", "localhost-key.pem")
SCRATCH_DIR = os.path.join(REPO_ROOT, ".claude", "tmp", "chrome_capture")
SCRATCH_CERT = os.path.join(SCRATCH_DIR, "cert.pem")
SCRATCH_KEY = os.path.join(SCRATCH_DIR, "key.pem")

LOOPBACK_HOST = "127.0.0.1"

# --------------------------------------------------------------------------
# Ceilings. Every peer-driven length or count below is bounded by one of these.
# --------------------------------------------------------------------------
# TLSCiphertext may carry 2**14 + 256 bytes (RFC 8446 5.2); a plaintext record
# carrying a ClientHello is smaller, so this is the generous end.
MAX_RECORD_BYTES = 16384 + 256
# A ClientHello with a post-quantum key share is ~1.8 KiB; 256 KiB is two orders
# of magnitude of headroom and still a bounded allocation.
MAX_HANDSHAKE_BYTES = 256 * 1024
# The server sends an empty SETTINGS, so SETTINGS_MAX_FRAME_SIZE stays at its
# RFC 9113 default, which is also its floor.
MAX_FRAME_BYTES = 16384
# One header block across HEADERS + CONTINUATION frames.
MAX_HEADER_BLOCK_BYTES = 256 * 1024
# Decoded header list (RFC 7541 size accounting, 32 bytes overhead per field).
MAX_HEADER_LIST_BYTES = 262144
# The HPACK dynamic table the server advertises (RFC 9113 default; never changed).
HPACK_DEFAULT_TABLE_SIZE = 4096
HPACK_MAX_INT = 2 ** 32
# Frames recorded per connection; a browser capture is tens of frames.
MAX_H2_FRAMES = 10000
# DATA / unknown-frame payload bytes kept as hex per frame.
PAYLOAD_HEX_LIMIT = 4096
MAX_H1_HEAD_BYTES = 64 * 1024
MAX_H1_HEADERS = 256
MAX_H1_BODY_BYTES = 1024 * 1024
MAX_H1_CHUNK_LINE_BYTES = 4096
MAX_H1_REQUESTS = 1000
# The page is sent without honouring flow control, so it must fit the default
# 65535-byte initial window.
MAX_PAGE_BYTES = 65535
DEFAULT_IDLE = 2.0
HANDSHAKE_TIMEOUT = 5.0

# --------------------------------------------------------------------------
# TLS name tables
# --------------------------------------------------------------------------
EXT_NAMES = {
    0: "server_name", 1: "max_fragment_length", 5: "status_request",
    10: "supported_groups", 11: "ec_point_formats", 13: "signature_algorithms",
    14: "use_srtp", 15: "heartbeat", 16: "application_layer_protocol_negotiation",
    17: "status_request_v2", 18: "signed_certificate_timestamp",
    21: "padding", 22: "encrypt_then_mac", 23: "extended_master_secret",
    27: "compress_certificate", 28: "record_size_limit",
    34: "delegated_credentials", 35: "session_ticket", 41: "pre_shared_key",
    42: "early_data", 43: "supported_versions", 44: "cookie",
    45: "psk_key_exchange_modes", 47: "certificate_authorities",
    49: "post_handshake_auth", 50: "signature_algorithms_cert", 51: "key_share",
    57: "quic_transport_parameters", 17513: "application_settings_old",
    17613: "application_settings", 65037: "encrypted_client_hello",
    65281: "renegotiation_info",
}
GROUP_NAMES = {
    0x11EC: "X25519MLKEM768", 0x11EB: "SecP256r1MLKEM768", 0x11ED: "SecP384r1MLKEM1024",
    0x6399: "X25519Kyber768Draft00", 0x001D: "x25519", 0x001E: "x448",
    0x0017: "secp256r1", 0x0018: "secp384r1", 0x0019: "secp521r1",
    0x0100: "ffdhe2048", 0x0101: "ffdhe3072", 0x0102: "ffdhe4096",
    0x0103: "ffdhe6144", 0x0104: "ffdhe8192",
}
SIGALG_NAMES = {
    0x0401: "rsa_pkcs1_sha256", 0x0501: "rsa_pkcs1_sha384", 0x0601: "rsa_pkcs1_sha512",
    0x0403: "ecdsa_secp256r1_sha256", 0x0503: "ecdsa_secp384r1_sha384",
    0x0603: "ecdsa_secp521r1_sha512", 0x0804: "rsa_pss_rsae_sha256",
    0x0805: "rsa_pss_rsae_sha384", 0x0806: "rsa_pss_rsae_sha512",
    0x0807: "ed25519", 0x0808: "ed448", 0x0809: "rsa_pss_pss_sha256",
    0x080A: "rsa_pss_pss_sha384", 0x080B: "rsa_pss_pss_sha512",
    0x0201: "rsa_pkcs1_sha1", 0x0203: "ecdsa_sha1",
}
CERT_COMP_NAMES = {1: "zlib", 2: "brotli", 3: "zstd"}
VERSION_NAMES = {0x0300: "SSL3.0", 0x0301: "TLS1.0", 0x0302: "TLS1.1",
                 0x0303: "TLS1.2", 0x0304: "TLS1.3"}
TLS13_CIPHERS = (0x1301, 0x1302, 0x1303, 0x1304, 0x1305)


def is_grease(v):
    return (v & 0x0F0F) == 0x0A0A and (v >> 8) == (v & 0xFF)


def h4(v):
    return "%04x" % v


def gname(v):
    if is_grease(v):
        return "GREASE"
    return GROUP_NAMES.get(v, "unknown")


# --------------------------------------------------------------------------
# Byte reader
# --------------------------------------------------------------------------
class Reader(object):
    def __init__(self, data):
        self.d = data
        self.p = 0

    def left(self):
        return len(self.d) - self.p

    def take(self, n):
        if n > self.left():
            raise ValueError("truncated: need %d have %d" % (n, self.left()))
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("!H", self.take(2))[0]

    def u24(self):
        b = self.take(3)
        return (b[0] << 16) | (b[1] << 8) | b[2]

    def u32(self):
        return struct.unpack("!I", self.take(4))[0]

    def vec8(self):
        return self.take(self.u8())

    def vec16(self):
        return self.take(self.u16())


def u16list(b):
    return [struct.unpack("!H", b[i:i + 2])[0] for i in range(0, len(b) - 1, 2)]


def proto_list(b):
    r = Reader(b)
    out = []
    while r.left():
        out.append(r.vec8().decode("latin-1"))
    return out


# --------------------------------------------------------------------------
# Extension decoders
# --------------------------------------------------------------------------
def decode_ext(etype, data):
    r = Reader(data)
    if is_grease(etype):
        return {"grease": True, "payload_len": len(data)}
    if etype == 0:
        names = []
        lst = Reader(r.vec16())
        while lst.left():
            t = lst.u8()
            names.append({"type": t, "name": lst.vec16().decode("latin-1")})
        return {"names": names}
    if etype == 10:
        gs = u16list(r.vec16())
        return {"groups": [{"id": h4(g), "name": gname(g)} for g in gs]}
    if etype == 11:
        return {"formats": list(r.vec8())}
    if etype in (13, 50):
        algs = u16list(r.vec16())
        return {"algorithms": [{"id": h4(a), "name": "GREASE" if is_grease(a) else SIGALG_NAMES.get(a, "unknown")} for a in algs]}
    if etype == 16:
        return {"protocols": proto_list(r.vec16())}
    if etype == 5:
        if not data:
            return {"empty": True}
        st = r.u8()
        rid = r.vec16()
        rext = r.vec16()
        return {"status_type": st, "responder_id_list_len": len(rid), "request_ext_len": len(rext)}
    if etype == 18:
        return {"empty": len(data) == 0}
    if etype == 21:
        return {"padding_len": len(data), "all_zero": data.count(0) == len(data)}
    if etype == 23:
        return {"empty": len(data) == 0}
    if etype == 27:
        algs = u16list(r.vec8())
        return {"algorithms": [{"id": a, "name": CERT_COMP_NAMES.get(a, "unknown")} for a in algs]}
    if etype == 28:
        return {"limit": r.u16()}
    if etype == 34:
        algs = u16list(r.vec16())
        return {"algorithms": [{"id": h4(a), "name": SIGALG_NAMES.get(a, "unknown")} for a in algs]}
    if etype == 35:
        return {"ticket_len": len(data)}
    if etype == 41:
        ids = Reader(r.vec16())
        idents = []
        while ids.left():
            ident = ids.vec16()
            age = ids.u32()
            idents.append({"identity_len": len(ident), "obfuscated_age": age})
        bnd = Reader(r.vec16())
        binders = []
        while bnd.left():
            binders.append(len(bnd.vec8()))
        return {"identities": idents, "binder_lens": binders}
    if etype == 43:
        vs = u16list(r.vec8())
        return {"versions": [{"id": h4(v), "name": "GREASE" if is_grease(v) else VERSION_NAMES.get(v, "unknown")} for v in vs]}
    if etype == 44:
        return {"cookie_len": len(r.vec16())}
    if etype == 45:
        return {"modes": list(r.vec8())}
    if etype == 51:
        ks = Reader(r.vec16())
        shares = []
        while ks.left():
            g = ks.u16()
            k = ks.vec16()
            shares.append({"group": h4(g), "name": gname(g), "key_len": len(k)})
        return {"shares": shares}
    if etype == 65281:
        return {"renegotiated_connection_len": len(r.vec8())}
    if etype in (17513, 17613):
        return {"protocols": proto_list(r.vec16())}
    if etype == 65037:
        t = r.u8()
        out = {"type": t, "type_name": "outer" if t == 0 else ("inner" if t == 1 else "unknown")}
        if t == 0:
            out["kdf"] = h4(r.u16())
            out["aead"] = h4(r.u16())
            out["config_id"] = r.u8()
            out["enc_len"] = len(r.vec16())
            out["payload_len"] = len(r.vec16())
        return out
    return None


# --------------------------------------------------------------------------
# ClientHello parsing
# --------------------------------------------------------------------------
def parse_client_hello(body):
    """Parse a ClientHello handshake body (after the 4-byte handshake header).

    Returns a dict; the `_ciphers_raw` and `_legacy_version_raw` keys feed
    `fingerprints` and are popped before a record is written."""
    r = Reader(body)
    ch = {}
    ch["legacy_version"] = h4(r.u16())
    ch["random_len"] = len(r.take(32))
    ch["session_id_len"] = len(r.vec8())
    cs = u16list(r.vec16())
    ch["cipher_suites"] = [{"id": h4(c), "grease": is_grease(c)} for c in cs]
    ch["compression_methods"] = list(r.vec8())
    exts = []
    if r.left():
        er = Reader(r.vec16())
        while er.left():
            et = er.u16()
            ed = er.vec16()
            e = {"type": et, "hex_type": h4(et),
                 "name": "GREASE" if is_grease(et) else EXT_NAMES.get(et, "unknown"),
                 "length": len(ed), "payload_hex": ed.hex()}
            try:
                dec = decode_ext(et, ed)
                if dec is not None:
                    e["decoded"] = dec
            except Exception as ex:  # noqa -- one malformed extension must not hide the rest
                e["decode_error"] = repr(ex)
            exts.append(e)
    ch["extensions"] = exts
    ch["_ciphers_raw"] = cs
    ch["_legacy_version_raw"] = struct.unpack("!H", body[0:2])[0]
    return ch


def client_hello_session_id(body):
    """The legacy_session_id of a ClientHello body (echoed by a ServerHello/HRR)."""
    r = Reader(body)
    r.take(2 + 32)
    return r.vec8()


def fingerprints(ch):
    cs = ch["_ciphers_raw"]
    exts = ch["extensions"]
    ext_ids = [e["type"] for e in exts]
    by_type = {}
    for e in exts:
        by_type.setdefault(e["type"], e)

    def dec(t, key, default):
        e = by_type.get(t)
        if e and "decoded" in e:
            return e["decoded"].get(key, default)
        return default

    groups = [int(g["id"], 16) for g in dec(10, "groups", [])]
    ecpf = dec(11, "formats", [])
    sigalgs = [int(a["id"], 16) for a in dec(13, "algorithms", [])]
    alpn = dec(16, "protocols", [])
    versions = [int(v["id"], 16) for v in dec(43, "versions", [])]

    # JA3
    ja3 = "%d,%s,%s,%s,%s" % (
        ch["_legacy_version_raw"],
        "-".join(str(c) for c in cs if not is_grease(c)),
        "-".join(str(e) for e in ext_ids if not is_grease(e)),
        "-".join(str(g) for g in groups if not is_grease(g)),
        "-".join(str(f) for f in ecpf),
    )
    ja3_md5 = hashlib.md5(ja3.encode()).hexdigest()

    # JA4
    nv = [v for v in versions if not is_grease(v)]
    ver = max(nv) if nv else ch["_legacy_version_raw"]
    vmap = {0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10", 0x0300: "s3"}
    vstr = vmap.get(ver, "00")
    sni = "d" if 0 in ext_ids else "i"
    c_ng = [c for c in cs if not is_grease(c)]
    e_ng = [e for e in ext_ids if not is_grease(e)]
    if alpn:
        a0 = alpn[0]
        alpn_s = (a0[0] + a0[-1]) if a0 else "00"
        if not (alpn_s[0].isalnum() and alpn_s[1].isalnum()):
            alpn_s = a0.encode("latin-1").hex()[0] + a0.encode("latin-1").hex()[-1]
    else:
        alpn_s = "00"
    ja4_a = "t%s%s%02d%02d%s" % (vstr, sni, min(len(c_ng), 99), min(len(e_ng), 99), alpn_s)
    b_raw = ",".join(sorted(h4(c) for c in c_ng))
    ja4_b = hashlib.sha256(b_raw.encode()).hexdigest()[:12] if c_ng else "000000000000"
    c_exts = sorted(h4(e) for e in e_ng if e not in (0, 16))
    c_raw = ",".join(c_exts)
    sig_ng = [s for s in sigalgs if not is_grease(s)]
    if sig_ng:
        c_raw = c_raw + "_" + ",".join(h4(s) for s in sig_ng)
    ja4_c = hashlib.sha256(c_raw.encode()).hexdigest()[:12] if c_exts else "000000000000"
    ja4 = "%s_%s_%s" % (ja4_a, ja4_b, ja4_c)
    ja4_r = "%s_%s_%s" % (ja4_a, b_raw, c_raw)
    # JA4_o / JA4_ro: original order, SNI/ALPN kept
    b_o = ",".join(h4(c) for c in c_ng)
    c_o = ",".join(h4(e) for e in e_ng)
    if sig_ng:
        c_o = c_o + "_" + ",".join(h4(s) for s in sig_ng)
    ja4_o = "%s_%s_%s" % (ja4_a, hashlib.sha256(b_o.encode()).hexdigest()[:12],
                          hashlib.sha256(c_o.encode()).hexdigest()[:12])
    ja4_ro = "%s_%s_%s" % (ja4_a, b_o, c_o)

    # peetprint-like
    def g(x):
        return "GREASE" if is_grease(x) else str(x)
    ks = [int(s["group"], 16) for s in dec(51, "shares", [])]
    comp = [a["id"] for a in dec(27, "algorithms", [])]
    peet = "|".join([
        "-".join(g(v) for v in versions),
        "-".join(alpn),
        "-".join(g(x) for x in groups),
        "-".join(str(s) for s in sigalgs),
        "-".join(str(m) for m in dec(45, "modes", [])),
        "-".join(str(a) for a in comp),
        "-".join(g(c) for c in cs),
        "-".join(sorted(g(e) for e in ext_ids if not is_grease(e))),
    ])
    return {
        "ja3": ja3, "ja3_md5": ja3_md5,
        "ja4": ja4, "ja4_a": ja4_a, "ja4_b": ja4_b, "ja4_c": ja4_c, "ja4_r": ja4_r,
        "ja4_o": ja4_o, "ja4_ro": ja4_ro,
        "peetprint_like": peet,
        "peetprint_like_md5": hashlib.md5(peet.encode()).hexdigest(),
        "summary": {
            "alpn": alpn,
            "extension_order": [("GREASE" if is_grease(e) else e) for e in ext_ids],
            "groups": ["GREASE" if is_grease(x) else "%s(%s)" % (h4(x), GROUP_NAMES.get(x, "?")) for x in groups],
            "key_shares": [("GREASE" if is_grease(k) else "%s(%s)" % (h4(k), GROUP_NAMES.get(k, "?"))) + ":" +
                           str(s["key_len"]) for k, s in zip(ks, dec(51, "shares", []))],
            "sigalgs": [h4(s) for s in sigalgs],
            "versions": ["GREASE" if is_grease(v) else h4(v) for v in versions],
            "cipher_count_incl_grease": len(cs),
        },
    }


def peek_client_hello(sock, deadline):
    """Peek until the full ClientHello handshake message is available.

    Nothing is consumed from the socket. Returns
    (raw_records_bytes, handshake_message_bytes, record_count)."""
    want = 5
    buf = b""
    while True:
        if time.time() > deadline:
            raise TimeoutError("timed out peeking ClientHello (have %d bytes)" % len(buf))
        if want > MAX_HANDSHAKE_BYTES + 64 * 5:
            raise ValueError("tls: ClientHello needs %d bytes, above %d" % (want, MAX_HANDSHAKE_BYTES))
        buf = sock.recv(max(want, 16384) + 64, socket.MSG_PEEK)
        if not buf:
            raise ConnectionError("client closed before ClientHello")
        # walk records
        pos = 0
        hs = b""
        nrec = 0
        mlen = 0
        complete = False
        need_more = False
        while True:
            if len(buf) < pos + 5:
                want = pos + 5
                need_more = True
                break
            ctype = buf[pos]
            if ctype != 0x16:
                raise ValueError("first record not handshake (content type %d)" % ctype)
            rlen = struct.unpack("!H", buf[pos + 3:pos + 5])[0]
            if rlen > MAX_RECORD_BYTES:
                raise ValueError("tls: record of %d bytes exceeds %d" % (rlen, MAX_RECORD_BYTES))
            if len(buf) < pos + 5 + rlen:
                want = pos + 5 + rlen
                need_more = True
                break
            hs += buf[pos + 5:pos + 5 + rlen]
            pos += 5 + rlen
            nrec += 1
            if len(hs) >= 4:
                if hs[0] != 1:
                    raise ValueError("first handshake msg is not ClientHello (type %d)" % hs[0])
                mlen = (hs[1] << 16) | (hs[2] << 8) | hs[3]
                if mlen > MAX_HANDSHAKE_BYTES:
                    raise ValueError("tls: ClientHello of %d bytes exceeds %d" % (mlen, MAX_HANDSHAKE_BYTES))
                if len(hs) >= 4 + mlen:
                    complete = True
                    break
        if complete:
            return buf[:pos], hs[:4 + mlen], nrec
        if need_more:
            time.sleep(0.01)


def _client_hello_record(hs):
    """(parsed ClientHello without the private keys, fingerprints) of a handshake message."""
    ch = parse_client_hello(hs[4:])
    fp = fingerprints(ch)
    ch.pop("_ciphers_raw", None)
    ch.pop("_legacy_version_raw", None)
    return ch, fp


# --------------------------------------------------------------------------
# HPACK (RFC 7541)
# --------------------------------------------------------------------------
STATIC_TABLE = [
    (":authority", ""), (":method", "GET"), (":method", "POST"), (":path", "/"),
    (":path", "/index.html"), (":scheme", "http"), (":scheme", "https"),
    (":status", "200"), (":status", "204"), (":status", "206"), (":status", "304"),
    (":status", "400"), (":status", "404"), (":status", "500"),
    ("accept-charset", ""), ("accept-encoding", "gzip, deflate"),
    ("accept-language", ""), ("accept-ranges", ""), ("accept", ""),
    ("access-control-allow-origin", ""), ("age", ""), ("allow", ""),
    ("authorization", ""), ("cache-control", ""), ("content-disposition", ""),
    ("content-encoding", ""), ("content-language", ""), ("content-length", ""),
    ("content-location", ""), ("content-range", ""), ("content-type", ""),
    ("cookie", ""), ("date", ""), ("etag", ""), ("expect", ""), ("expires", ""),
    ("from", ""), ("host", ""), ("if-match", ""), ("if-modified-since", ""),
    ("if-none-match", ""), ("if-range", ""), ("if-unmodified-since", ""),
    ("last-modified", ""), ("link", ""), ("location", ""), ("max-forwards", ""),
    ("proxy-authenticate", ""), ("proxy-authorization", ""), ("range", ""),
    ("referer", ""), ("refresh", ""), ("retry-after", ""), ("server", ""),
    ("set-cookie", ""), ("strict-transport-security", ""),
    ("transfer-encoding", ""), ("user-agent", ""), ("vary", ""), ("via", ""),
    ("www-authenticate", ""),
]

# RFC 7541 Appendix B: (code, bit length) for symbols 0..256 (256 = EOS)
HUFFMAN = [
    (0x1ff8, 13), (0x7fffd8, 23), (0xfffffe2, 28), (0xfffffe3, 28),
    (0xfffffe4, 28), (0xfffffe5, 28), (0xfffffe6, 28), (0xfffffe7, 28),
    (0xfffffe8, 28), (0xffffea, 24), (0x3ffffffc, 30), (0xfffffe9, 28),
    (0xfffffea, 28), (0x3ffffffd, 30), (0xfffffeb, 28), (0xfffffec, 28),
    (0xfffffed, 28), (0xfffffee, 28), (0xfffffef, 28), (0xffffff0, 28),
    (0xffffff1, 28), (0xffffff2, 28), (0x3ffffffe, 30), (0xffffff3, 28),
    (0xffffff4, 28), (0xffffff5, 28), (0xffffff6, 28), (0xffffff7, 28),
    (0xffffff8, 28), (0xffffff9, 28), (0xffffffa, 28), (0xffffffb, 28),
    (0x14, 6), (0x3f8, 10), (0x3f9, 10), (0xffa, 12),
    (0x1ff9, 13), (0x15, 6), (0xf8, 8), (0x7fa, 11),
    (0x3fa, 10), (0x3fb, 10), (0xf9, 8), (0x7fb, 11),
    (0xfa, 8), (0x16, 6), (0x17, 6), (0x18, 6),
    (0x0, 5), (0x1, 5), (0x2, 5), (0x19, 6),
    (0x1a, 6), (0x1b, 6), (0x1c, 6), (0x1d, 6),
    (0x1e, 6), (0x1f, 6), (0x5c, 7), (0xfb, 8),
    (0x7ffc, 15), (0x20, 6), (0xffb, 12), (0x3fc, 10),
    (0x1ffa, 13), (0x21, 6), (0x5d, 7), (0x5e, 7),
    (0x5f, 7), (0x60, 7), (0x61, 7), (0x62, 7),
    (0x63, 7), (0x64, 7), (0x65, 7), (0x66, 7),
    (0x67, 7), (0x68, 7), (0x69, 7), (0x6a, 7),
    (0x6b, 7), (0x6c, 7), (0x6d, 7), (0x6e, 7),
    (0x6f, 7), (0x70, 7), (0x71, 7), (0x72, 7),
    (0xfc, 8), (0x73, 7), (0xfd, 8), (0x1ffb, 13),
    (0x7fff0, 19), (0x1ffc, 13), (0x3ffc, 14), (0x22, 6),
    (0x7ffd, 15), (0x3, 5), (0x23, 6), (0x4, 5),
    (0x24, 6), (0x5, 5), (0x25, 6), (0x26, 6),
    (0x27, 6), (0x6, 5), (0x74, 7), (0x75, 7),
    (0x28, 6), (0x29, 6), (0x2a, 6), (0x7, 5),
    (0x2b, 6), (0x76, 7), (0x2c, 6), (0x8, 5),
    (0x9, 5), (0x2d, 6), (0x77, 7), (0x78, 7),
    (0x79, 7), (0x7a, 7), (0x7b, 7), (0x7ffe, 15),
    (0x7fc, 11), (0x3ffd, 14), (0x1ffd, 13), (0xffffffc, 28),
    (0xfffe6, 20), (0x3fffd2, 22), (0xfffe7, 20), (0xfffe8, 20),
    (0x3fffd3, 22), (0x3fffd4, 22), (0x3fffd5, 22), (0x7fffd9, 23),
    (0x3fffd6, 22), (0x7fffda, 23), (0x7fffdb, 23), (0x7fffdc, 23),
    (0x7fffdd, 23), (0x7fffde, 23), (0xffffeb, 24), (0x7fffdf, 23),
    (0xffffec, 24), (0xffffed, 24), (0x3fffd7, 22), (0x7fffe0, 23),
    (0xffffee, 24), (0x7fffe1, 23), (0x7fffe2, 23), (0x7fffe3, 23),
    (0x7fffe4, 23), (0x1fffdc, 21), (0x3fffd8, 22), (0x7fffe5, 23),
    (0x3fffd9, 22), (0x7fffe6, 23), (0x7fffe7, 23), (0xffffef, 24),
    (0x3fffda, 22), (0x1fffdd, 21), (0xfffe9, 20), (0x3fffdb, 22),
    (0x3fffdc, 22), (0x7fffe8, 23), (0x7fffe9, 23), (0x1fffde, 21),
    (0x7fffea, 23), (0x3fffdd, 22), (0x3fffde, 22), (0xfffff0, 24),
    (0x1fffdf, 21), (0x3fffdf, 22), (0x7fffeb, 23), (0x7fffec, 23),
    (0x1fffe0, 21), (0x1fffe1, 21), (0x3fffe0, 22), (0x1fffe2, 21),
    (0x7fffed, 23), (0x3fffe1, 22), (0x7fffee, 23), (0x7fffef, 23),
    (0xfffea, 20), (0x3fffe2, 22), (0x3fffe3, 22), (0x3fffe4, 22),
    (0x7ffff0, 23), (0x3fffe5, 22), (0x3fffe6, 22), (0x7ffff1, 23),
    (0x3ffffe0, 26), (0x3ffffe1, 26), (0xfffeb, 20), (0x7fff1, 19),
    (0x3fffe7, 22), (0x7ffff2, 23), (0x3fffe8, 22), (0x1ffffec, 25),
    (0x3ffffe2, 26), (0x3ffffe3, 26), (0x3ffffe4, 26), (0x7ffffde, 27),
    (0x7ffffdf, 27), (0x3ffffe5, 26), (0xfffff1, 24), (0x1ffffed, 25),
    (0x7fff2, 19), (0x1fffe3, 21), (0x3ffffe6, 26), (0x7ffffe0, 27),
    (0x7ffffe1, 27), (0x3ffffe7, 26), (0x7ffffe2, 27), (0xfffff2, 24),
    (0x1fffe4, 21), (0x1fffe5, 21), (0x3ffffe8, 26), (0x3ffffe9, 26),
    (0xffffffd, 28), (0x7ffffe3, 27), (0x7ffffe4, 27), (0x7ffffe5, 27),
    (0xfffec, 20), (0xfffff3, 24), (0xfffed, 20), (0x1fffe6, 21),
    (0x3fffe9, 22), (0x1fffe7, 21), (0x1fffe8, 21), (0x7ffff3, 23),
    (0x3fffea, 22), (0x3fffeb, 22), (0x1ffffee, 25), (0x1ffffef, 25),
    (0xfffff4, 24), (0xfffff5, 24), (0x3ffffea, 26), (0x7ffff4, 23),
    (0x3ffffeb, 26), (0x7ffffe6, 27), (0x3ffffec, 26), (0x3ffffed, 26),
    (0x7ffffe7, 27), (0x7ffffe8, 27), (0x7ffffe9, 27), (0x7ffffea, 27),
    (0x7ffffeb, 27), (0xffffffe, 28), (0x7ffffec, 27), (0x7ffffed, 27),
    (0x7ffffee, 27), (0x7ffffef, 27), (0x7fffff0, 27), (0x3ffffee, 26),
    (0x3fffffff, 30),
]
_HUFF_DECODE = dict(((ln, code), sym) for sym, (code, ln) in enumerate(HUFFMAN))


def huffman_selfcheck():
    """Canonical-code + Kraft check of the embedded table. Returns error or None."""
    if len(HUFFMAN) != 257:
        return "table has %d entries" % len(HUFFMAN)
    order = sorted(range(257), key=lambda s: (HUFFMAN[s][1], s))
    code = 0
    prev_len = HUFFMAN[order[0]][1]
    for i, s in enumerate(order):
        c, ln = HUFFMAN[s]
        if i > 0:
            code = (code + 1) << (ln - prev_len)
        prev_len = ln
        if c != code:
            return "symbol %d: code %x expected canonical %x" % (s, c, code)
    kraft = sum(2 ** (30 - ln) for _, ln in HUFFMAN)
    if kraft != 2 ** 30:
        return "kraft sum mismatch"
    return None


def huffman_decode(data):
    """Decode an RFC 7541 Huffman string; refuses EOS, codes over 30 bits and bad padding."""
    out = bytearray()
    code = 0
    ln = 0
    for byte in data:
        for i in range(7, -1, -1):
            code = (code << 1) | ((byte >> i) & 1)
            ln += 1
            sym = _HUFF_DECODE.get((ln, code))
            if sym is not None:
                if sym == 256:
                    raise ValueError("EOS in huffman string")
                out.append(sym)
                code = 0
                ln = 0
            elif ln > 30:
                raise ValueError("invalid huffman code")
    if ln > 7 or code != (1 << ln) - 1:
        raise ValueError("invalid huffman padding")
    return bytes(out)


class HpackDecoder(object):
    """Stateful RFC 7541 decoder; one instance per HTTP/2 connection.

    `max_table_size` is the SETTINGS_HEADER_TABLE_SIZE the decoding side
    advertised: a dynamic-table-size update above it is refused, as is one that
    is not at the start of a header block (RFC 7541 4.2)."""

    def __init__(self, max_table_size=HPACK_DEFAULT_TABLE_SIZE, max_header_list=MAX_HEADER_LIST_BYTES):
        self.dyn = []  # newest first
        self.size = 0
        self.limit = max_table_size
        self.max_size = max_table_size
        self.max_header_list = max_header_list
        self.size_updates = []

    def _evict(self):
        while self.dyn and self.size > self.max_size:
            n, v = self.dyn.pop()
            self.size -= 32 + len(n) + len(v)

    def _add(self, n, v):
        self.dyn.insert(0, (n, v))
        self.size += 32 + len(n) + len(v)
        self._evict()

    def _get(self, idx):
        if idx <= 0:
            raise ValueError("hpack: index 0")
        if idx <= len(STATIC_TABLE):
            return STATIC_TABLE[idx - 1]
        d = idx - len(STATIC_TABLE) - 1
        if d >= len(self.dyn):
            raise ValueError("hpack: dynamic index %d out of range" % idx)
        return self.dyn[d]

    @staticmethod
    def _int(data, pos, prefix):
        if pos >= len(data):
            raise ValueError("hpack: integer truncated")
        mask = (1 << prefix) - 1
        v = data[pos] & mask
        pos += 1
        if v < mask:
            return v, pos
        m = 0
        while True:
            if pos >= len(data):
                raise ValueError("hpack: integer truncated")
            b = data[pos]
            pos += 1
            v += (b & 0x7F) << m
            m += 7
            if v > HPACK_MAX_INT:
                raise ValueError("hpack: integer exceeds 2**32")
            if not b & 0x80:
                return v, pos
            if m >= 42:
                raise ValueError("hpack: integer uses more than 6 continuation bytes")

    def _str(self, data, pos):
        if pos >= len(data):
            raise ValueError("hpack: string truncated")
        huff = bool(data[pos] & 0x80)
        ln, pos = self._int(data, pos, 7)
        raw = data[pos:pos + ln]
        if len(raw) != ln:
            raise ValueError("hpack: string truncated")
        pos += ln
        s = huffman_decode(raw) if huff else raw
        return s.decode("latin-1"), huff, pos

    def decode(self, data):
        pos = 0
        out = []
        list_size = 0
        seen_field = False
        while pos < len(data):
            b = data[pos]
            if b & 0x80:
                idx, pos = self._int(data, pos, 7)
                n, v = self._get(idx)
                out.append({"name": n, "value": v, "rep": "indexed", "index": idx})
            elif b & 0x40 or (b & 0xF0) in (0x00, 0x10):
                if b & 0x40:
                    prefix, rep = 6, "literal_incremental"
                elif b & 0x10:
                    prefix, rep = 4, "literal_never_indexed"
                else:
                    prefix, rep = 4, "literal_without_indexing"
                idx, pos = self._int(data, pos, prefix)
                if idx:
                    n = self._get(idx)[0]
                    nh = None
                else:
                    n, nh, pos = self._str(data, pos)
                v, vh, pos = self._str(data, pos)
                e = {"name": n, "value": v, "rep": rep, "name_index": idx, "value_huffman": vh}
                if nh is not None:
                    e["name_huffman"] = nh
                out.append(e)
                if rep == "literal_incremental":
                    self._add(n, v)
            elif (b & 0xE0) == 0x20:
                if seen_field:
                    raise ValueError("hpack: table size update after a header field")
                sz, pos = self._int(data, pos, 5)
                if sz > self.limit:
                    raise ValueError("hpack: table size update %d exceeds %d" % (sz, self.limit))
                self.max_size = sz
                self.size_updates.append(sz)
                self._evict()
                out.append({"rep": "dynamic_table_size_update", "size": sz})
                continue
            else:
                raise ValueError("hpack: bad byte 0x%02x" % b)
            seen_field = True
            last = out[-1]
            list_size += 32 + len(last["name"]) + len(last["value"])
            if list_size > self.max_header_list:
                raise ValueError("hpack: header list exceeds %d bytes" % self.max_header_list)
        return out


def hpack_encode_int(value, prefix, flags):
    """RFC 7541 5.1 integer with an N-bit prefix, OR-ed into `flags`."""
    mask = (1 << prefix) - 1
    if value < mask:
        return bytes([flags | value])
    out = bytearray([flags | mask])
    value -= mask
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def hpack_literal(name_index, value):
    """Literal header field without indexing, indexed name, raw (non-Huffman) value."""
    v = value.encode("latin-1")
    return hpack_encode_int(name_index, 4, 0x00) + hpack_encode_int(len(v), 7, 0x00) + v


# --------------------------------------------------------------------------
# Socket helpers
# --------------------------------------------------------------------------
def _arm(sock, deadline, idle):
    """Set the socket timeout to the smaller of the idle window and the deadline."""
    left = idle
    if deadline is not None:
        left = min(left, deadline - time.time())
    if left <= 0:
        raise socket.timeout("deadline reached")
    sock.settimeout(left)


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("EOF after %d/%d bytes" % (len(buf), n))
        buf += chunk
    return buf


def _recv_exact_or_eof(s, n):
    """recv_exact, except that EOF before the first byte returns None."""
    first = s.recv(n)
    if not first:
        return None
    if len(first) < n:
        first += recv_exact(s, n - len(first))
    return first


def drain(s):
    """Read (and discard) until EOF or a short timeout so the client sees the reply."""
    try:
        s.settimeout(1.0)
        end = time.time() + 1.5
        while time.time() < end:
            d = s.recv(65536)
            if not d:
                break
    except Exception:
        pass


# --------------------------------------------------------------------------
# Responses (shared by h2 and h1)
# --------------------------------------------------------------------------
def default_config():
    """The serve configuration a handler falls back to when called bare."""
    return {"page": None, "set_cookies": [], "idle": DEFAULT_IDLE}


def _set_cookie_names(cfg):
    return [n for n, _v in cfg.get("set_cookies", [])]


def pick_response(method, path, fetch_mode, cfg):
    """(status, content_type, body_to_send, content_length, set_cookie_pairs)."""
    bare_path = path.split("?", 1)[0]
    nav = method == "GET" and (bare_path == "/" or fetch_mode == "navigate")
    if nav and cfg.get("page") is not None:
        ctype, body = "text/html; charset=utf-8", cfg["page"]
    else:
        ctype, body = "text/plain", b"ok"
    cookies = list(cfg.get("set_cookies", [])) if (method == "GET" and bare_path == "/") else []
    length = len(body)
    if method == "HEAD":
        body = b""
    return 200, ctype, body, length, cookies


# --------------------------------------------------------------------------
# HTTP/2
# --------------------------------------------------------------------------
FRAME_TYPES = {0: "DATA", 1: "HEADERS", 2: "PRIORITY", 3: "RST_STREAM", 4: "SETTINGS",
               5: "PUSH_PROMISE", 6: "PING", 7: "GOAWAY", 8: "WINDOW_UPDATE",
               9: "CONTINUATION", 0x10: "PRIORITY_UPDATE"}
SETTINGS_NAMES = {1: "HEADER_TABLE_SIZE", 2: "ENABLE_PUSH", 3: "MAX_CONCURRENT_STREAMS",
                  4: "INITIAL_WINDOW_SIZE", 5: "MAX_FRAME_SIZE", 6: "MAX_HEADER_LIST_SIZE",
                  8: "ENABLE_CONNECT_PROTOCOL", 9: "NO_RFC7540_PRIORITIES"}
PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


def frame(ftype, flags, sid, payload):
    return struct.pack("!I", len(payload))[1:] + bytes([ftype, flags]) + struct.pack("!I", sid) + payload


def _h2_new_stream(sid):
    return {"stream_id": sid, "headers_flags": None, "headers_priority": None, "pad_len": None,
            "header_block": b"", "headers_done": False, "end_stream": False,
            "data_frames": [], "body_len": 0, "rst_stream": None, "answered": False,
            "trailers": None}


def _h2_finish_headers(dec, st):
    """Decode a completed header block for a stream (headers, or trailers when present)."""
    block = st.pop("header_block")
    st["header_block"] = b""
    hdrs = dec.decode(block)
    if st["headers_done"]:
        st["trailers"] = {"header_block_hex": block.hex(), "headers": hdrs}
        return
    st["headers_done"] = True
    st["header_block_hex"] = block.hex()
    st["headers"] = hdrs
    names = [h["name"] for h in hdrs if "name" in h]
    st["header_order"] = names
    ua = [h["value"] for h in hdrs if h.get("name") == "user-agent"]
    st["user_agent"] = ua[0] if ua else None


def _h2_answer(s, st, cfg):
    """Answer a finished stream with 200 and a body."""
    hdrs = st.get("headers") or []
    fields = {}
    for h in hdrs:
        if "name" in h:
            fields.setdefault(h["name"], h["value"])
    method = fields.get(":method", "GET")
    path = fields.get(":path", "/")
    status, ctype, body, length, cookies = pick_response(method, path, fields.get("sec-fetch-mode"), cfg)
    block = b"\x88" + hpack_literal(31, ctype) + hpack_literal(28, str(length))
    for n, v in cookies:
        block += hpack_literal(55, "%s=%s; Path=/" % (n, v))
    sid = st["stream_id"]
    if not body:
        out = frame(1, 0x5, sid, block)
    else:
        out = frame(1, 0x4, sid, block)
        for off in range(0, len(body), MAX_FRAME_BYTES):
            chunk = body[off:off + MAX_FRAME_BYTES]
            last = off + MAX_FRAME_BYTES >= len(body)
            out += frame(0, 0x1 if last else 0x0, sid, chunk)
    s.sendall(out)
    st["answered"] = True
    st["response"] = {"status": status, "content_type": ctype, "content_length": length,
                      "body_sent": len(body), "set_cookie_names": [n for n, _v in cookies]}


def _h2_public_stream(st):
    out = dict(st)
    out.pop("header_block", None)
    return out


def handle_h2(s, rec, cfg=None, deadline=None, idle=None):
    """Record an HTTP/2 connection: every frame, every stream, until EOF, GOAWAY or timeout.

    `rec["h2"]` is filled incrementally, so a protocol error still leaves the
    frames seen so far. The first stream's header fields are also copied to the
    top of `rec["h2"]` (the single-stream record shape older captures used)."""
    cfg = cfg or default_config()
    if idle is None:
        idle = cfg.get("idle", DEFAULT_IDLE)
    h2 = {"frames": [], "streams": [], "settings": [], "settings_acks": 0,
          "rst_streams": [], "goaway_received": None, "goaway_sent": None, "end_reason": None}
    rec["h2"] = h2
    _arm(s, deadline, idle)
    pre = recv_exact(s, len(PREFACE))
    h2["preface_ok"] = pre == PREFACE
    if pre != PREFACE:
        h2["preface_hex"] = pre.hex()
        h2["end_reason"] = "bad_preface"
        return
    s.sendall(frame(4, 0, 0, b""))
    dec = HpackDecoder()
    wu = []
    prios = []
    streams = {}
    order = []
    cont_sid = None
    max_sid = 0
    try:
        while True:
            try:
                _arm(s, deadline, idle)
                hdr = _recv_exact_or_eof(s, 9)
            except socket.timeout:
                h2["end_reason"] = "timeout"
                break
            except (ConnectionResetError, BrokenPipeError):
                h2["end_reason"] = "reset"
                break
            if hdr is None:
                h2["end_reason"] = "eof"
                break
            ln = (hdr[0] << 16) | (hdr[1] << 8) | hdr[2]
            ft, fl = hdr[3], hdr[4]
            sid = struct.unpack("!I", hdr[5:9])[0] & 0x7FFFFFFF
            if ln > MAX_FRAME_BYTES:
                raise ValueError("h2: frame of %d bytes exceeds %d" % (ln, MAX_FRAME_BYTES))
            if len(h2["frames"]) >= MAX_H2_FRAMES:
                raise ValueError("h2: more than %d frames on one connection" % MAX_H2_FRAMES)
            pl = recv_exact(s, ln)
            if cont_sid is not None and (ft != 9 or sid != cont_sid):
                raise ValueError("h2: expected CONTINUATION on stream %d" % cont_sid)
            f = {"type": ft, "name": FRAME_TYPES.get(ft, "UNKNOWN"), "flags": fl,
                 "stream_id": sid, "length": ln}
            h2["frames"].append(f)
            if ft == 4:
                if fl & 1:
                    f["ack"] = True
                    h2["settings_acks"] += 1
                else:
                    lst = []
                    for i in range(0, ln - ln % 6, 6):
                        k, v = struct.unpack("!HI", pl[i:i + 6])
                        lst.append({"id": k, "name": SETTINGS_NAMES.get(k, "UNKNOWN"), "value": v})
                    f["settings"] = lst
                    h2["settings"].append(lst)
                    s.sendall(frame(4, 1, 0, b""))
            elif ft == 8:
                if ln != 4:
                    raise ValueError("h2: WINDOW_UPDATE of %d bytes" % ln)
                inc = struct.unpack("!I", pl)[0] & 0x7FFFFFFF
                f["increment"] = inc
                wu.append((sid, inc))
            elif ft == 2:
                if ln != 5:
                    raise ValueError("h2: PRIORITY of %d bytes" % ln)
                d = struct.unpack("!I", pl[:4])[0]
                p = {"exclusive": bool(d >> 31), "dep": d & 0x7FFFFFFF, "weight": pl[4] + 1}
                f["priority"] = p
                prios.append((sid, p))
            elif ft in (1, 9):
                if sid == 0:
                    raise ValueError("h2: header frame on stream 0")
                if ft == 1:
                    off = 0
                    pad = 0
                    if fl & 0x8:
                        if ln < 1:
                            raise ValueError("h2: padded HEADERS without a pad length")
                        pad = pl[0]
                        off = 1
                        f["pad_len"] = pad
                    prio = None
                    if fl & 0x20:
                        if ln < off + 5:
                            raise ValueError("h2: HEADERS priority truncated")
                        d = struct.unpack("!I", pl[off:off + 4])[0]
                        prio = {"exclusive": bool(d >> 31), "dep": d & 0x7FFFFFFF,
                                "weight": pl[off + 4] + 1}
                        f["priority"] = prio
                        off += 5
                    if off + pad > ln:
                        raise ValueError("h2: HEADERS padding exceeds the frame")
                    frag = pl[off:ln - pad]
                    st = streams.get(sid)
                    if st is None:
                        st = _h2_new_stream(sid)
                        streams[sid] = st
                        order.append(sid)
                        max_sid = max(max_sid, sid)
                        st["headers_flags"] = fl
                        st["headers_priority"] = prio
                        st["pad_len"] = f.get("pad_len")
                    if fl & 0x1:
                        st["end_stream"] = True
                else:
                    st = streams.get(sid)
                    if st is None:
                        raise ValueError("h2: CONTINUATION on unknown stream %d" % sid)
                    frag = pl
                st["header_block"] += frag
                if len(st["header_block"]) > MAX_HEADER_BLOCK_BYTES:
                    raise ValueError("h2: header block exceeds %d bytes" % MAX_HEADER_BLOCK_BYTES)
                if fl & 0x4:
                    cont_sid = None
                    _h2_finish_headers(dec, st)
                else:
                    cont_sid = sid
            elif ft == 0:
                st = streams.get(sid)
                if st is None:
                    raise ValueError("h2: DATA on unknown stream %d" % sid)
                off = 0
                pad = 0
                if fl & 0x8:
                    if ln < 1:
                        raise ValueError("h2: padded DATA without a pad length")
                    pad = pl[0]
                    off = 1
                if off + pad > ln:
                    raise ValueError("h2: DATA padding exceeds the frame")
                data = pl[off:ln - pad]
                df = {"length": ln, "flags": fl, "payload_len": len(data),
                      "payload_hex": data[:PAYLOAD_HEX_LIMIT].hex(),
                      "payload_truncated": len(data) > PAYLOAD_HEX_LIMIT}
                if fl & 0x8:
                    df["pad_len"] = pad
                st["data_frames"].append(df)
                st["body_len"] += len(data)
                if fl & 0x1:
                    st["end_stream"] = True
                if ln:
                    s.sendall(frame(8, 0, 0, struct.pack("!I", ln)))
            elif ft == 3:
                if ln != 4:
                    raise ValueError("h2: RST_STREAM of %d bytes" % ln)
                code = struct.unpack("!I", pl)[0]
                f["error_code"] = code
                h2["rst_streams"].append({"stream_id": sid, "error_code": code})
                st = streams.get(sid)
                if st is not None:
                    st["rst_stream"] = code
            elif ft == 6:
                f["opaque_hex"] = pl.hex()
                if not fl & 1:
                    s.sendall(frame(6, 1, 0, pl))
            elif ft == 7:
                if ln < 8:
                    raise ValueError("h2: GOAWAY of %d bytes" % ln)
                last, code = struct.unpack("!II", pl[:8])
                h2["goaway_received"] = {"last_stream_id": last & 0x7FFFFFFF, "error_code": code,
                                         "debug_hex": pl[8:8 + PAYLOAD_HEX_LIMIT].hex()}
                f["error_code"] = code
                h2["end_reason"] = "client_goaway"
                break
            else:
                f["payload_hex"] = pl[:PAYLOAD_HEX_LIMIT].hex()
            # answer every stream that is complete and not reset
            for osid in order:
                st = streams[osid]
                if st["headers_done"] and st["end_stream"] and not st["answered"] and st["rst_stream"] is None:
                    _h2_answer(s, st, cfg)
        if h2["end_reason"] == "timeout":
            try:
                s.settimeout(1.0)
                s.sendall(frame(7, 0, 0, struct.pack("!II", max_sid, 0)))
                h2["goaway_sent"] = {"last_stream_id": max_sid, "error_code": 0}
            except OSError:
                pass
    finally:
        h2["streams"] = [_h2_public_stream(streams[x]) for x in order]
        first = streams[order[0]] if order else None
        names = (first or {}).get("header_order") or []
        if first is not None:
            h2["header_block_hex"] = first.get("header_block_hex")
            h2["headers"] = first.get("headers")
            h2["headers_priority"] = first.get("headers_priority")
            h2["stream_id"] = first["stream_id"]
            h2["header_order"] = names
            h2["user_agent"] = first.get("user_agent")
        # Akamai fingerprint
        settings = h2["settings"]
        s_part = ";".join("%d:%d" % (x["id"], x["value"]) for x in (settings[0] if settings else []))
        wu_conn = [inc for sid_, inc in wu if sid_ == 0]
        w_part = str(wu_conn[0]) if wu_conn else "00"
        if prios:
            p_part = ",".join("%d:%d:%d:%d" % (sid_, 1 if p["exclusive"] else 0, p["dep"], p["weight"])
                              for sid_, p in prios)
        else:
            p_part = "0"
        ph = {":method": "m", ":authority": "a", ":scheme": "s", ":path": "p", ":protocol": "pr"}
        o_part = ",".join(ph.get(n, n) for n in names if n.startswith(":"))
        h2["akamai"] = "%s|%s|%s|%s" % (s_part, w_part, p_part, o_part)


# --------------------------------------------------------------------------
# HTTP/1.1
# --------------------------------------------------------------------------
class _SockBuf(object):
    """A read buffer over a socket with bounded line/head/exact reads."""

    def __init__(self, sock, deadline, idle):
        self.sock = sock
        self.deadline = deadline
        self.idle = idle
        self.buf = b""

    def fill(self):
        _arm(self.sock, self.deadline, self.idle)
        d = self.sock.recv(65536)
        if not d:
            return False
        self.buf += d
        return True

    def read_until(self, sep, limit, what):
        while sep not in self.buf:
            if len(self.buf) > limit:
                raise ValueError("h1: %s exceeds %d bytes" % (what, limit))
            if not self.fill():
                return None
        idx = self.buf.index(sep)
        if idx > limit:
            raise ValueError("h1: %s exceeds %d bytes" % (what, limit))
        out = self.buf[:idx]
        self.buf = self.buf[idx + len(sep):]
        return out

    def read_exact(self, n):
        while len(self.buf) < n:
            if not self.fill():
                raise ConnectionError("h1: EOF inside a body (%d/%d bytes)" % (len(self.buf), n))
        out = self.buf[:n]
        self.buf = self.buf[n:]
        return out


def _h1_read_body(rb, fields, req):
    """Read a request body by Content-Length or chunked coding, bounded."""
    te = fields.get("transfer-encoding", "").lower()
    if "chunked" in te:
        req["chunked"] = True
        sizes = []
        body = b""
        while True:
            line = rb.read_until(b"\r\n", MAX_H1_CHUNK_LINE_BYTES, "chunk-size line")
            if line is None:
                raise ConnectionError("h1: EOF inside a chunked body")
            size_s = line.split(b";", 1)[0].strip()
            try:
                size = int(size_s, 16)
            except ValueError:
                raise ValueError("h1: bad chunk size %r" % size_s[:16])
            if size < 0 or len(body) + size > MAX_H1_BODY_BYTES:
                raise ValueError("h1: body exceeds %d bytes" % MAX_H1_BODY_BYTES)
            sizes.append(size)
            if size == 0:
                while True:
                    trailer = rb.read_until(b"\r\n", MAX_H1_CHUNK_LINE_BYTES, "trailer line")
                    if trailer is None or trailer == b"":
                        break
                break
            body += rb.read_exact(size)
            rb.read_exact(2)
        req["chunk_sizes"] = sizes
        return body
    cl = fields.get("content-length")
    if cl is None:
        return b""
    try:
        n = int(cl.strip())
    except ValueError:
        raise ValueError("h1: bad content-length")
    if n < 0 or n > MAX_H1_BODY_BYTES:
        raise ValueError("h1: body of %d bytes exceeds %d" % (n, MAX_H1_BODY_BYTES))
    return rb.read_exact(n)


def handle_h1(s, rec, cfg=None, deadline=None, idle=None):
    """Record HTTP/1.1 requests on one connection (keep-alive) until EOF, close or timeout.

    `rec["http1"]["requests"]` lists every request; the first one's fields are
    also copied to the top of `rec["http1"]` (the older single-request shape)."""
    cfg = cfg or default_config()
    if idle is None:
        idle = cfg.get("idle", DEFAULT_IDLE)
    h1 = {"requests": [], "end_reason": None}
    rec["http1"] = h1
    rb = _SockBuf(s, deadline, idle)
    try:
        while True:
            if len(h1["requests"]) >= MAX_H1_REQUESTS:
                h1["end_reason"] = "request_limit"
                break
            try:
                head_b = rb.read_until(b"\r\n\r\n", MAX_H1_HEAD_BYTES, "request head")
            except socket.timeout:
                h1["end_reason"] = "timeout"
                if rb.buf:
                    h1["partial_bytes"] = len(rb.buf)
                break
            except (ConnectionResetError, BrokenPipeError):
                h1["end_reason"] = "reset"
                break
            if head_b is None:
                h1["end_reason"] = "eof"
                if rb.buf:
                    h1["partial_bytes"] = len(rb.buf)
                break
            head = head_b.decode("latin-1")
            lines = head.split("\r\n")
            if len(lines) - 1 > MAX_H1_HEADERS:
                raise ValueError("h1: more than %d header lines" % MAX_H1_HEADERS)
            req = {"request_head": head, "request_line": lines[0] if lines else ""}
            parts = req["request_line"].split(" ")
            method = parts[0] if parts else ""
            target = parts[1] if len(parts) > 1 else ""
            version = parts[2] if len(parts) > 2 else ""
            req["method"] = method
            req["target"] = target
            req["version"] = version
            hdrs = []
            fields = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    hdrs.append({"name": k, "value": v.strip()})
                    fields.setdefault(k.lower(), v.strip())
            req["headers"] = hdrs
            req["header_order"] = [h["name"] for h in hdrs]
            ua = [h["value"] for h in hdrs if h["name"].lower() == "user-agent"]
            req["user_agent"] = ua[0] if ua else None
            h1["requests"].append(req)
            body = _h1_read_body(rb, fields, req)
            req["body_len"] = len(body)
            req["body_hex"] = body[:PAYLOAD_HEX_LIMIT].hex()
            req["body_truncated"] = len(body) > PAYLOAD_HEX_LIMIT
            status, ctype, rbody, length, cookies = pick_response(method, target, fields.get("sec-fetch-mode"), cfg)
            conn_tok = fields.get("connection", "").lower()
            close = "close" in conn_tok or version == "HTTP/1.0"
            resp = "HTTP/1.1 %d OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n" % (status, ctype, length)
            for n, v in cookies:
                resp += "Set-Cookie: %s=%s; Path=/\r\n" % (n, v)
            resp += "Connection: %s\r\n\r\n" % ("close" if close else "keep-alive")
            s.sendall(resp.encode("latin-1") + rbody)
            req["response"] = {"status": status, "content_type": ctype, "content_length": length,
                               "body_sent": len(rbody), "set_cookie_names": [n for n, _v in cookies]}
            if close:
                h1["end_reason"] = "connection_close"
                break
    finally:
        if h1["requests"]:
            first = h1["requests"][0]
            for key in ("request_head", "request_line", "headers", "header_order", "user_agent"):
                h1[key] = first.get(key)


# --------------------------------------------------------------------------
# Scripted HelloRetryRequest responder (no TLS library)
# --------------------------------------------------------------------------
# RFC 8446 4.1.3: the ServerHello.random that marks a HelloRetryRequest.
HRR_RANDOM = bytes.fromhex("CF21AD74E59A6111BE1D8C021E65B891C2A211167ABB8C5E079E09E2C8A8339C")
HRR_COOKIE = b"chrome_capture/hrr-cookie/v1"


def build_hello_retry_request(session_id, cipher, group, cookie=HRR_COOKIE):
    """A plaintext TLS record carrying a HelloRetryRequest (RFC 8446 4.1.4)."""
    exts = struct.pack("!HHH", 43, 2, 0x0304)
    exts += struct.pack("!HHH", 51, 2, group)
    exts += struct.pack("!HHH", 44, len(cookie) + 2, len(cookie)) + cookie
    body = struct.pack("!H", 0x0303) + HRR_RANDOM + bytes([len(session_id)]) + session_id
    body += struct.pack("!HB", cipher, 0) + struct.pack("!H", len(exts)) + exts
    hs = bytes([2]) + struct.pack("!I", len(body))[1:] + body
    return bytes([0x16, 0x03, 0x03]) + struct.pack("!H", len(hs)) + hs


def _pick_tls13_cipher(ciphers):
    for c in ciphers:
        if c in TLS13_CIPHERS:
            return c
    return 0x1301


def handle_hrr(conn, rec, group, deadline=None, idle=HANDSHAKE_TIMEOUT):
    """Consume ClientHello 1, answer with an HRR for `group`, record CCS + ClientHello 2."""
    hrr = {"group": h4(group), "group_name": gname(group), "records": [],
           "ccs_before_client_hello_2": False, "end_reason": None}
    rec["hrr"] = hrr
    raw, hs, nrec = peek_client_hello(conn, time.time() + HANDSHAKE_TIMEOUT)
    recv_exact(conn, len(raw))
    rec["client_hello_records"] = nrec
    rec["client_hello_raw_hex"] = raw.hex()
    rec["client_hello_handshake_len"] = len(hs)
    rec["client_hello"], rec["fingerprints"] = _client_hello_record(hs)
    body = hs[4:]
    ch_full = parse_client_hello(body)
    offered = [int(g["id"], 16) for e in ch_full["extensions"] if e["type"] == 10 and "decoded" in e
               for g in e["decoded"].get("groups", [])]
    shared = [int(sh["group"], 16) for e in ch_full["extensions"] if e["type"] == 51 and "decoded" in e
              for sh in e["decoded"].get("shares", [])]
    cipher = _pick_tls13_cipher(ch_full["_ciphers_raw"])
    hrr["group_offered"] = group in offered
    hrr["group_already_shared"] = group in shared
    hrr["cipher"] = h4(cipher)
    hrr["cookie_hex"] = HRR_COOKIE.hex()
    msg = build_hello_retry_request(client_hello_session_id(body), cipher, group)
    hrr["hello_retry_request_hex"] = msg.hex()
    conn.sendall(msg)
    hs2 = b""
    raw2 = b""
    while True:
        try:
            _arm(conn, deadline, idle)
            head = _recv_exact_or_eof(conn, 5)
        except socket.timeout:
            hrr["end_reason"] = "timeout"
            break
        except (ConnectionResetError, BrokenPipeError):
            hrr["end_reason"] = "reset"
            break
        if head is None:
            hrr["end_reason"] = "eof"
            break
        ct = head[0]
        rlen = struct.unpack("!H", head[3:5])[0]
        if rlen > MAX_RECORD_BYTES:
            raise ValueError("tls: record of %d bytes exceeds %d" % (rlen, MAX_RECORD_BYTES))
        payload = recv_exact(conn, rlen)
        r = {"type": ct, "version": h4(struct.unpack("!H", head[1:3])[0]), "length": rlen}
        hrr["records"].append(r)
        if len(hrr["records"]) > 64:
            raise ValueError("tls: more than 64 records after the HRR")
        if ct == 0x14:
            r["payload_hex"] = payload.hex()
            if not hs2:
                hrr["ccs_before_client_hello_2"] = True
            continue
        if ct == 0x15:
            if rlen >= 2:
                r["alert"] = {"level": payload[0], "description": payload[1]}
            hrr["end_reason"] = "alert"
            break
        if ct != 0x16:
            r["payload_hex"] = payload[:PAYLOAD_HEX_LIMIT].hex()
            hrr["end_reason"] = "unexpected_record"
            break
        raw2 += head + payload
        hs2 += payload
        if len(hs2) > MAX_HANDSHAKE_BYTES + 4:
            raise ValueError("tls: ClientHello 2 exceeds %d bytes" % MAX_HANDSHAKE_BYTES)
        if len(hs2) >= 4:
            if hs2[0] != 1:
                raise ValueError("tls: second handshake message is not ClientHello (type %d)" % hs2[0])
            mlen = (hs2[1] << 16) | (hs2[2] << 8) | hs2[3]
            if mlen > MAX_HANDSHAKE_BYTES:
                raise ValueError("tls: ClientHello 2 of %d bytes exceeds %d" % (mlen, MAX_HANDSHAKE_BYTES))
            if len(hs2) >= 4 + mlen:
                msg2 = hs2[:4 + mlen]
                hrr["client_hello_2_raw_hex"] = raw2.hex()
                hrr["client_hello_2_handshake_len"] = len(msg2)
                ch2, fp2 = _client_hello_record(msg2)
                hrr["client_hello_2"] = ch2
                hrr["fingerprints_2"] = fp2
                cookie_ext = [e for e in ch2["extensions"] if e["type"] == 44]
                want = (struct.pack("!H", len(HRR_COOKIE)) + HRR_COOKIE).hex()
                hrr["cookie_echoed"] = bool(cookie_ext) and cookie_ext[0]["payload_hex"] == want
                hrr["key_share_groups_2"] = [sh["group"] for e in ch2["extensions"] if e["type"] == 51 and "decoded" in e
                                             for sh in e["decoded"].get("shares", [])]
                hrr["end_reason"] = "client_hello_2"
                break


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------
class CaptureConfigError(ValueError):
    """A serve option or environment problem, reported as one line and exit 2."""


def check_bind_host(host):
    """Loopback only: any host but 127.0.0.1 is refused."""
    if host != LOOPBACK_HOST:
        raise CaptureConfigError("bind: refusing host %r; chrome_capture binds %s only" % (host, LOOPBACK_HOST))
    return host


def ensure_cert(cert=None, key=None):
    """Resolve the server certificate: explicit paths, the committed test leaf, or a scratch one.

    The scratch certificate is generated with `openssl req` -- the only
    subprocess this tool spawns -- with an explicit stdin=DEVNULL."""
    if cert or key:
        if not (cert and key):
            raise CaptureConfigError("cert: --cert and --key must be given together")
        for p in (cert, key):
            if not os.path.isfile(p):
                raise CaptureConfigError("cert: no such file: %s" % p)
        return cert, key
    if os.path.isfile(FIXTURE_CERT) and os.path.isfile(FIXTURE_KEY):
        return FIXTURE_CERT, FIXTURE_KEY
    if os.path.isfile(SCRATCH_CERT) and os.path.isfile(SCRATCH_KEY):
        return SCRATCH_CERT, SCRATCH_KEY
    make_private_dir(SCRATCH_DIR)
    argv = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", SCRATCH_KEY, "-out", SCRATCH_CERT, "-days", "60", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1",
            # end-entity cert: some TLS stacks reject a CA:TRUE leaf even with verification off
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=serverAuth"]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=60, check=False)
    except FileNotFoundError:
        raise CaptureConfigError("cert: openssl not found; pass --cert and --key")
    except subprocess.TimeoutExpired:
        raise CaptureConfigError("cert: openssl req timed out")
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CaptureConfigError("cert: openssl req failed: %s" % (lines[0] if lines else "exit %d" % proc.returncode))
    try:
        os.chmod(SCRATCH_KEY, 0o600)
    except OSError as ex:
        # the key still works; say so rather than leave it readable unnoticed
        print("chrome_capture: cert: cannot make %s owner-only: %s" % (SCRATCH_KEY, ex.strerror or ex),
              file=sys.stderr)
    return SCRATCH_CERT, SCRATCH_KEY


def parse_alpn(value):
    protos = [p.strip() for p in value.split(",") if p.strip()]
    if not protos:
        raise CaptureConfigError("alpn: empty protocol list")
    for p in protos:
        if p not in ("h2", "http/1.1"):
            raise CaptureConfigError("alpn: unsupported protocol %r (h2, http/1.1)" % p)
    if len(set(protos)) != len(protos):
        raise CaptureConfigError("alpn: duplicate protocol in %r" % value)
    return protos


def parse_group(value):
    try:
        g = int(value, 0)
    except ValueError:
        raise CaptureConfigError("hrr-group: not an integer: %r" % value)
    if not 0 <= g <= 0xFFFF:
        raise CaptureConfigError("hrr-group: 0x%x is not a 16-bit group id" % g)
    return g


_COOKIE_NAME_RX = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_COOKIE_VALUE_RX = re.compile(r"^[!#-+\--:<-\[\]-~]*$")


def parse_set_cookie(value):
    if "=" not in value:
        raise CaptureConfigError("set-cookie: expected NAME=VALUE, got %r" % value)
    name, val = value.split("=", 1)
    if not _COOKIE_NAME_RX.match(name):
        raise CaptureConfigError("set-cookie: bad cookie name %r" % name)
    if not _COOKIE_VALUE_RX.match(val):
        raise CaptureConfigError("set-cookie: bad cookie value for %r" % name)
    return name, val


def _build_config(opts):
    page = None
    if opts.page:
        try:
            with open(opts.page, "rb") as fh:
                page = fh.read(MAX_PAGE_BYTES + 1)
        except OSError as ex:
            raise CaptureConfigError("page: cannot read %s: %s" % (opts.page, ex.strerror or ex))
        if len(page) > MAX_PAGE_BYTES:
            raise CaptureConfigError("page: %s exceeds %d bytes" % (opts.page, MAX_PAGE_BYTES))
    cookies = [parse_set_cookie(c) for c in (opts.set_cookie or [])]
    if opts.idle <= 0:
        raise CaptureConfigError("idle: must be positive")
    return {"page": page, "set_cookies": cookies, "idle": opts.idle}


def handle_conn(conn, addr, ctx, idx, label, cfg=None, mode="tls", hrr_group=None, deadline=None):
    """Handle one accepted connection in `mode` (tls, plain or hrr) and return its record."""
    cfg = cfg or default_config()
    rec = {"label": label, "index": idx, "peer": "%s:%d" % (addr[0], addr[1]), "time": time.time(),
           "mode": mode, "server_set_cookie_names": _set_cookie_names(cfg),
           "server_set_cookies": dict(cfg.get("set_cookies", []))}
    conn.settimeout(HANDSHAKE_TIMEOUT)
    idle = cfg.get("idle", DEFAULT_IDLE)
    if mode == "plain":
        try:
            handle_h1(conn, rec, cfg, deadline, idle)
        except Exception as ex:
            rec["http_error"] = "%s\n%s" % (repr(ex), traceback.format_exc())
        finally:
            _close(conn)
        return rec
    if mode == "hrr":
        try:
            handle_hrr(conn, rec, hrr_group, deadline)
        except Exception as ex:
            rec["hrr_error"] = "%s\n%s" % (repr(ex), traceback.format_exc())
        finally:
            _close(conn)
        return rec
    try:
        raw, hs, nrec = peek_client_hello(conn, time.time() + HANDSHAKE_TIMEOUT)
        rec["client_hello_records"] = nrec
        rec["client_hello_raw_hex"] = raw.hex()
        rec["client_hello_handshake_len"] = len(hs)
        ch, fp = _client_hello_record(hs)
        rec["client_hello"] = ch
        rec["fingerprints"] = fp
    except Exception as ex:
        rec["client_hello_error"] = "%s\n%s" % (repr(ex), traceback.format_exc())
    tls = None
    try:
        tls = ctx.wrap_socket(conn, server_side=True)
        rec["tls"] = {"version": tls.version(), "cipher": tls.cipher(),
                      "alpn": tls.selected_alpn_protocol()}
    except Exception as ex:
        rec["handshake_error"] = repr(ex)
    if tls is not None:
        try:
            if tls.selected_alpn_protocol() == "h2":
                handle_h2(tls, rec, cfg, deadline, idle)
            else:
                handle_h1(tls, rec, cfg, deadline, idle)
        except Exception as ex:
            rec["http_error"] = "%s\n%s" % (repr(ex), traceback.format_exc())
        drain(tls)
        _close(tls)
    else:
        _close(conn)
    return rec


def _close(sock):
    try:
        sock.close()
    except Exception:
        pass


_CONTROL_RX = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _terminal_safe(value):
    """`value` as text with every C0/C1 control and DEL written as \\xNN.

    Peer-chosen bytes (HPACK header names in the akamai string, an error line)
    reach the operator's terminal through the summary line; an escaped ESC
    cannot drive it."""
    return _CONTROL_RX.sub(lambda m: "\\x%02x" % ord(m.group(0)), str(value))


def summary_line(rec):
    fp = rec.get("fingerprints", {})
    alpn = (rec.get("tls") or {}).get("alpn")
    h2 = rec.get("h2") or {}
    h1 = rec.get("http1") or {}
    ua = (h2 or h1).get("user_agent")
    ak = h2.get("akamai")
    err = (rec.get("client_hello_error") or rec.get("handshake_error") or rec.get("http_error")
           or rec.get("hrr_error") or rec.get("fatal_error"))
    parts = ["#%03d" % rec["index"], rec["peer"], "mode=%s" % rec.get("mode")]
    if rec.get("mode") == "hrr":
        hrr = rec.get("hrr") or {}
        parts.append("group=%s" % hrr.get("group"))
        parts.append("ccs=%s" % hrr.get("ccs_before_client_hello_2"))
        parts.append("ch2=%s" % ("yes" if hrr.get("client_hello_2") else "no"))
        parts.append("end=%s" % hrr.get("end_reason"))
        if fp:
            parts.append("ja4=%s" % fp.get("ja4"))
    elif rec.get("mode") == "plain":
        parts.append("requests=%d" % len(h1.get("requests") or []))
    else:
        parts += ["ja4=%s" % fp.get("ja4"), "ja3=%s" % fp.get("ja3_md5"), "alpn=%s" % alpn]
        if h2:
            parts.append("streams=%d" % len(h2.get("streams") or []))
        elif h1:
            parts.append("requests=%d" % len(h1.get("requests") or []))
    if ak:
        parts.append("akamai=%s" % _terminal_safe(ak))
    if ua:
        parts.append("ua=%s" % _terminal_safe(repr(ua)))
    if err:
        parts.append("ERROR=%s" % _terminal_safe(str(err).splitlines()[0]))
    return " ".join(parts)


def check_label(label):
    """Refuse a record-file prefix that could leave --out: a path separator or '..'."""
    seps = [s for s in (os.sep, os.altsep, "/") if s]
    if not label or any(s in label for s in seps) or ".." in label:
        raise CaptureConfigError("label: %r must be a plain file-name prefix (no %s, no '..')"
                                 % (label, " or ".join(sorted(set(seps)))))
    return label


def make_private_dir(path):
    """Create `path` owner-only (0700); a directory that already exists keeps its mode.

    os.makedirs' mode is masked by the umask, so a directory this call created
    is chmod-ed to 0700 explicitly. Records carry localhost cookies and bodies."""
    existed = os.path.isdir(path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not existed:
        os.chmod(path, 0o700)


def write_private_json(path, obj):
    """Write `obj` as JSON to `path`, owner read/write only (0600), never through a symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fd, 0o600)
        json.dump(obj, fh, indent=2, default=str)


def serve(opts, ready=None, out=None):
    """Run the capture server described by an argparse namespace; returns the exit code.

    `ready(port)` is called once the socket listens (the bound port, useful with
    --port 0). Raises CaptureConfigError for a bad option; `main` maps it to 2."""
    out = out or sys.stdout
    host = check_bind_host(opts.host)
    if opts.count < 1:
        raise CaptureConfigError("count: must be at least 1")
    if opts.timeout <= 0:
        raise CaptureConfigError("timeout: must be positive")
    if opts.plain and opts.hrr_group is not None:
        raise CaptureConfigError("mode: --plain and --hrr-group are exclusive")
    check_label(opts.label)
    cfg = _build_config(opts)
    err = huffman_selfcheck()
    if err:
        raise CaptureConfigError("huffman: table self-check failed: %s" % err)
    mode = "plain" if opts.plain else ("hrr" if opts.hrr_group is not None else "tls")
    group = parse_group(opts.hrr_group) if opts.hrr_group is not None else None
    ctx = None
    if mode == "tls":
        alpn = parse_alpn(opts.alpn)
        cert, key = ensure_cert(opts.cert, opts.key)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        ctx.set_alpn_protocols(alpn)
    make_private_dir(opts.out)

    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((host, opts.port))
        ls.listen(16)
        port = ls.getsockname()[1]
        print("listening on %s:%d mode=%s label=%s out=%s count=%d timeout=%.0fs" %
              (host, port, mode, opts.label, opts.out, opts.count, opts.timeout), file=out, flush=True)
        if ready is not None:
            ready(port)
        deadline = time.time() + opts.timeout
        n = 0
        while n < opts.count:
            remaining = deadline - time.time()
            if remaining <= 0:
                print("overall timeout reached", file=out, flush=True)
                break
            ls.settimeout(remaining)
            try:
                conn, addr = ls.accept()
            except socket.timeout:
                print("overall timeout reached", file=out, flush=True)
                break
            n += 1
            try:
                rec = handle_conn(conn, addr, ctx, n, opts.label, cfg, mode, group, deadline)
            except Exception as ex:
                rec = {"label": opts.label, "index": n, "peer": "%s:%d" % (addr[0], addr[1]), "mode": mode,
                       "fatal_error": "%s\n%s" % (repr(ex), traceback.format_exc())}
            path = os.path.join(opts.out, "%s-%03d.json" % (opts.label, n))
            try:
                write_private_json(path, rec)
            except Exception as ex:
                print("write failed %s: %r" % (path, ex), file=out, flush=True)
            try:
                print(summary_line(rec), file=out, flush=True)
            except Exception as ex:
                print("#%03d summary failed: %r" % (n, ex), file=out, flush=True)
    finally:
        ls.close()
    return 0


# --------------------------------------------------------------------------
# Reading capture records and exported fixtures
# --------------------------------------------------------------------------
# A capture record is 20-30 KiB. The worst case this tool itself writes is
# MAX_H2_FRAMES frames each keeping PAYLOAD_HEX_LIMIT bytes as hex (~80 MiB),
# so 128 MiB admits every record serve can produce and still bounds json.load.
MAX_CAPTURE_FILE_BYTES = 128 * 1024 * 1024
# A capture directory holds one file per connection; a session is tens of them.
MAX_CAPTURE_FILES = 10000
# Hex digits accepted for one ClientHello: the handshake ceiling plus room for
# the 5-byte headers of the records that carry it.
MAX_CLIENT_HELLO_HEX = 2 * (MAX_HANDSHAKE_BYTES + 64 * 1024)


def load_capture(path):
    """One capture record or exported fixture as a dict; CaptureConfigError when unreadable."""
    try:
        size = os.path.getsize(path)
    except OSError as ex:
        raise CaptureConfigError("read: cannot stat %s: %s" % (path, ex.strerror or ex))
    if size > MAX_CAPTURE_FILE_BYTES:
        raise CaptureConfigError("read: %s is %d bytes, above %d" % (path, size, MAX_CAPTURE_FILE_BYTES))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except OSError as ex:
        raise CaptureConfigError("read: cannot read %s: %s" % (path, ex.strerror or ex))
    except ValueError as ex:
        raise CaptureConfigError("read: %s is not JSON: %s" % (path, ex))
    if not isinstance(rec, dict):
        raise CaptureConfigError("read: %s is not a JSON object" % path)
    return rec


def capture_paths(path):
    """The capture files named by `path`: the file itself, or the *.json of a directory, sorted."""
    if os.path.isfile(path):
        return [path]
    if not os.path.isdir(path):
        raise CaptureConfigError("read: no such file or directory: %s" % path)
    try:
        names = sorted(n for n in os.listdir(path) if n.endswith(".json"))
    except OSError as ex:
        raise CaptureConfigError("read: cannot list %s: %s" % (path, ex.strerror or ex))
    if len(names) > MAX_CAPTURE_FILES:
        raise CaptureConfigError("read: %s holds %d captures, above %d" % (path, len(names), MAX_CAPTURE_FILES))
    return [os.path.join(path, n) for n in names if os.path.isfile(os.path.join(path, n))]


def handshake_from_hex(hexstr):
    """(ClientHello handshake message, record payload lengths or None) from hex.

    Accepts the raw TLS records a capture stores (`client_hello_raw_hex`,
    `client_hello_2_raw_hex`) or the bare handshake message an exported fixture
    stores (`client_hello_hex`, `client_hello_2_hex`). Raises ValueError."""
    if not isinstance(hexstr, str):
        raise ValueError("client hello: not a hex string")
    if len(hexstr) > MAX_CLIENT_HELLO_HEX:
        raise ValueError("client hello: %d hex digits exceed %d" % (len(hexstr), MAX_CLIENT_HELLO_HEX))
    data = bytes.fromhex(hexstr)
    if not data:
        raise ValueError("client hello: empty")
    lengths = None
    if data[0] == 0x16:
        lengths = []
        hs = b""
        pos = 0
        while pos < len(data):
            if len(data) < pos + 5:
                raise ValueError("client hello: truncated record header")
            if data[pos] != 0x16:
                raise ValueError("client hello: record %d is not a handshake record" % len(lengths))
            rlen = struct.unpack("!H", data[pos + 3:pos + 5])[0]
            if rlen > MAX_RECORD_BYTES:
                raise ValueError("tls: record of %d bytes exceeds %d" % (rlen, MAX_RECORD_BYTES))
            if len(data) < pos + 5 + rlen:
                raise ValueError("client hello: truncated record")
            hs += data[pos + 5:pos + 5 + rlen]
            lengths.append(rlen)
            pos += 5 + rlen
    else:
        hs = data
    if len(hs) < 4 or hs[0] != 1:
        raise ValueError("client hello: not a ClientHello handshake message")
    mlen = (hs[1] << 16) | (hs[2] << 8) | hs[3]
    if mlen > MAX_HANDSHAKE_BYTES:
        raise ValueError("tls: ClientHello of %d bytes exceeds %d" % (mlen, MAX_HANDSHAKE_BYTES))
    if len(hs) < 4 + mlen:
        raise ValueError("client hello: truncated handshake message")
    return hs[:4 + mlen], lengths


def fingerprints_of_decoded(ch):
    """`fingerprints` of a ClientHello dict as a capture record stores it (private keys popped)."""
    full = dict(ch)
    full["_ciphers_raw"] = [int(c["id"], 16) for c in ch["cipher_suites"]]
    full["_legacy_version_raw"] = int(ch["legacy_version"], 16)
    return fingerprints(full)


def client_hello_sni(ch):
    """Every host_name of the server_name extension; ValueError when it does not decode."""
    names = []
    for e in ch["extensions"]:
        if e["type"] != 0:
            continue
        if "decoded" not in e:
            raise ValueError("server_name: extension does not decode")
        for n in e["decoded"].get("names", []):
            names.append(n["name"])
    return names


def client_hello_ech_payload_len(ch):
    for e in ch["extensions"]:
        if e["type"] == 65037:
            return (e.get("decoded") or {}).get("payload_len")
    return None


# --------------------------------------------------------------------------
# diff: fixed differences between a reference and a candidate capture set
# --------------------------------------------------------------------------
DIFF_HEADERS = ("accept", "accept-encoding", "accept-language", "cache-control", "content-type",
                "priority", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest",
                "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user", "upgrade-insecure-requests",
                "user-agent")
# Chrome permutes the extension order per connection, so these differ between
# any two connections; they are judged across connections, never one-to-one.
DIFF_ORDER_DEPENDENT = ("tls.ext_order", "tls.ja3", "tls.ja4_o")
# Values Chrome draws per connection (ECH GREASE payload length and what it
# drags along; padding; PSK sizes). One value per side cannot decide them.
DIFF_RANDOM_VALUED = ("tls.ech_payload_len", "tls.ech_ext_len", "tls.clienthello_len_minus_sni", "ext.65037",
                      "ext.21", "ext.41")
DIFF_DERIVED = {
    "tls.clienthello_len_minus_sni": "derived: sum of extension sizes, SNI hostname bytes subtracted",
    "tls.ja4": "derived: cipher set + extension set + sigalgs",
    "tls.ext_count": "derived: extension set",
    "tls.ech_ext_len": "derived: ECH payload_len + 42",
    "h2.akamai": "derived: settings / window / priorities / pseudo-header order",
    "h2.hpack_reps": "per-header HPACK representation; /H = Huffman-coded value",
    "hdr.cache-control": "a reload sends max-age=0; a typed navigation omits it",
}
# What diff never compares, or compares only modulo randomness: the declared
# half of the "varying" set. The measured half is added per run.
DIFF_VARYING_DECLARED = (
    ("tls.random", "32 random bytes; never compared"),
    ("tls.session_id", "random legacy_session_id bytes; only the length is compared"),
    ("GREASE", "GREASE code points in cipher_suites, extension types, supported_groups, key_share, "
               "supported_versions and signature_algorithms are normalised to G"),
    ("ext.51.key_exchange", "key share bytes; only group:length is compared"),
    ("ext.65037", "ECH GREASE config_id, enc and payload bytes are random; type/kdf/aead/enc_len/payload_len "
                  "are compared"),
    ("ext.0.host_name", "the SNI hostname; only the name count and name types are compared, and "
                        "tls.clienthello_len_minus_sni is the handshake length minus the hostname bytes"),
    ("ext.41", "pre_shared_key identities and binders; only their lengths are compared"),
    ("ext.order", "Chrome permutes the extension order per connection; tls.ext_order, tls.ja3 and "
                  "tls.ja4_o are judged across connections, never on one connection per side"),
    ("peetprint", "hashes the raw GREASE values, so it is not compared"),
    ("http.origin_values", ":authority, host, origin, referer, cookie and the request target name the "
                           "capture origin; they are not compared"),
)
DIFF_KINDS = ("FIXED", "UNDECIDED", "FROZEN", "PARTIAL", "CLIENTVARIES", "random", "not_compared")
_DIFF_EXTRA_EXT_NAMES = {51764: "trust_anchors (draft codepoint 0xca34)"}


def _diff_g(h):
    try:
        v = int(h, 16)
    except (TypeError, ValueError):
        return h
    return "G" if is_grease(v) else h


def _diff_ext_key(t):
    return "G" if is_grease(t) else str(t)


def _diff_hex(ph):
    if len(ph) <= 120:
        return ph
    return "%s...sha256=%s" % (ph[:64], hashlib.sha256(ph.encode()).hexdigest()[:16])


def _diff_ext_payload(e):
    """GREASE-normalised, randomness-stripped descriptor of one extension."""
    t = e["type"]
    d = e.get("decoded") or {}
    ph = e.get("payload_hex", "")
    if is_grease(t):
        return "len=%d hex=%s" % (e["length"], _diff_hex(ph))
    if t == 0:
        return "names=%d types=%s" % (len(d.get("names", [])), ",".join(str(n.get("type")) for n in d.get("names", [])))
    if t == 10:
        return "groups=" + ",".join(_diff_g(x["id"]) for x in d.get("groups", []))
    if t in (13, 50):
        return "algs=" + ",".join(_diff_g(x["id"]) for x in d.get("algorithms", []))
    if t == 43:
        return "versions=" + ",".join(_diff_g(x["id"]) for x in d.get("versions", []))
    if t == 51:
        return "shares=" + ",".join("%s:%d" % (_diff_g(x["group"]), x["key_len"]) for x in d.get("shares", []))
    if t == 65037:
        return "type=%s kdf=%s aead=%s enc_len=%s payload_len=%s" % (
            d.get("type"), d.get("kdf"), d.get("aead"), d.get("enc_len"), d.get("payload_len"))
    if t == 21:
        return "padding_len=%d" % e["length"]
    if t == 41:
        return "ident_lens=%s binders=%s" % ([i["identity_len"] for i in d.get("identities", [])], d.get("binder_lens"))
    if t == 35:
        return "ticket_len=%d" % e["length"]
    return "len=%d hex=%s" % (e["length"], _diff_hex(ph))


def _diff_ch_from(decoded, hexstr, hs_len, nrec, lengths_field):
    """(ClientHello dict, handshake length, record count) of one ClientHello, or (None, None, None).

    A decoded dict wins over the bytes when both exist, so a record edited at
    the JSON level is compared as edited; the lengths always come from the bytes."""
    ch = decoded if isinstance(decoded, dict) and isinstance(decoded.get("extensions"), list) else None
    if hexstr:
        hs, lengths = handshake_from_hex(hexstr)
        hs_len = len(hs)
        if lengths is not None:
            nrec = len(lengths)
        elif isinstance(lengths_field, list):
            nrec = len(lengths_field)
        if ch is None:
            ch = parse_client_hello(hs[4:])
    if ch is None:
        return None, None, None
    return ch, hs_len, nrec


def _diff_view(rec):
    """Normalise a capture record or an exported fixture into what diff compares."""
    v = {}
    v["ch"], v["hs_len"], v["nrec"] = _diff_ch_from(
        rec.get("client_hello"), rec.get("client_hello_hex") or rec.get("client_hello_raw_hex"),
        rec.get("client_hello_handshake_len"), rec.get("client_hello_records"),
        rec.get("client_hello_record_lengths"))
    hrr = rec.get("hrr") if isinstance(rec.get("hrr"), dict) else {}
    v["ch2"], v["ch2_len"], v["ch2_nrec"] = _diff_ch_from(
        hrr.get("client_hello_2"), hrr.get("client_hello_2_hex") or hrr.get("client_hello_2_raw_hex"),
        hrr.get("client_hello_2_handshake_len"), None, hrr.get("client_hello_2_record_lengths"))
    v["h2"] = None
    h2 = rec.get("h2")
    if isinstance(h2, dict):
        streams = h2.get("streams") or []
        first = streams[0] if streams else h2
        headers = first.get("headers")
        if headers:
            v["h2"] = {"frames": h2.get("frames") or [], "headers": headers,
                       "headers_priority": first.get("headers_priority"), "akamai": h2.get("akamai")}
    v["h1"] = None
    h1 = rec.get("h1") if isinstance(rec.get("h1"), dict) else rec.get("http1")
    if isinstance(h1, dict):
        reqs = h1.get("requests") or []
        first = reqs[0] if reqs else h1
        if first.get("headers"):
            v["h1"] = {"method": first.get("method"), "version": first.get("version"), "headers": first["headers"]}
    v["alpn"] = (rec.get("tls") or {}).get("alpn")
    return v


def _diff_sni_bytes(ch):
    """Total host_name bytes of the server_name extension; 0 with no SNI (IP literal) or an undecodable one.

    The hostname is not compared, so the length it adds to the ClientHello is
    taken back out: two captures against different origins stay comparable."""
    try:
        names = client_hello_sni(ch)
    except ValueError:
        return 0
    return sum(len(n) for n in names)


def _diff_tls_features(f, p, ch, hs_len, nrec):
    exts = ch["extensions"]
    keys = [_diff_ext_key(e["type"]) for e in exts]
    fp = fingerprints_of_decoded(ch)
    f[p + "tls.legacy_version"] = ch["legacy_version"]
    f[p + "tls.session_id_len"] = ch["session_id_len"]
    f[p + "tls.compression"] = str(ch["compression_methods"])
    f[p + "tls.ciphers"] = ",".join(_diff_g(c["id"]) for c in ch["cipher_suites"])
    f[p + "tls.cipher_count"] = len(ch["cipher_suites"])
    f[p + "tls.ext_count"] = len(exts)
    f[p + "tls.ext_set"] = ",".join(sorted(keys, key=lambda k: (k == "G", int(k) if k != "G" else 0)))
    f[p + "tls.ext_order"] = ",".join(keys)
    f[p + "tls.ext_first"] = keys[0] if keys else None
    f[p + "tls.ext_last"] = keys[-1] if keys else None
    gi = 0
    for e in exts:
        k = _diff_ext_key(e["type"])
        if k == "G":
            gi += 1
            f[p + "ext.GREASE#%d" % gi] = _diff_ext_payload(e)
        else:
            f[p + "ext.%s" % k] = _diff_ext_payload(e)
    ech = [e for e in exts if e["type"] == 65037]
    if ech:
        f[p + "tls.ech_payload_len"] = (ech[0].get("decoded") or {}).get("payload_len")
        f[p + "tls.ech_ext_len"] = ech[0]["length"]
    else:
        f[p + "tls.ech_payload_len"] = "ABSENT"
        f[p + "tls.ech_ext_len"] = "ABSENT"
    f[p + "tls.clienthello_len_minus_sni"] = (hs_len - _diff_sni_bytes(ch)) if hs_len is not None else None
    f[p + "tls.records"] = nrec
    f[p + "tls.ja4"] = fp["ja4"]
    f[p + "tls.ja4_o"] = fp["ja4_o"]
    f[p + "tls.ja3"] = fp["ja3"]


def _diff_features(v):
    """feature -> value (None = not observed) for one normalised record; None when nothing to compare."""
    if v["ch"] is None and v["h2"] is None and v["h1"] is None:
        return None
    f = {}
    if v["ch"] is not None:
        _diff_tls_features(f, "", v["ch"], v["hs_len"], v["nrec"])
    if v["ch2"] is not None:
        _diff_tls_features(f, "ch2.", v["ch2"], v["ch2_len"], v["ch2_nrec"])
    h2 = v["h2"]
    if h2 is not None:
        preface = []
        for x in h2["frames"]:
            if x.get("type") == 1:
                break
            preface.append(x)
        seq = ["%s/f%d/s%d" % (x.get("name"), x.get("flags", 0), x.get("stream_id", 0)) for x in preface]
        f["h2.preface_seq"] = " ".join(seq) or "none"
        sett = [x for x in preface if x.get("type") == 4 and not x.get("ack") and x.get("settings") is not None]
        f["h2.settings"] = (";".join("%s=%d" % (s["name"], s["value"]) for s in sett[0]["settings"])
                            if sett else "none")
        f["h2.window_update"] = ";".join("s%d+%d" % (x.get("stream_id", 0), x.get("increment", 0))
                                         for x in preface if x.get("type") == 8) or "none"
        hf = [x for x in h2["frames"] if x.get("type") == 1]
        f["h2.headers_flags"] = hf[0].get("flags") if hf else None
        f["h2.headers_priority"] = json.dumps(h2["headers_priority"], sort_keys=True)
        f["h2.akamai"] = h2["akamai"]
        named = [h for h in h2["headers"] if "name" in h]
        f["h2.header_order_raw"] = ",".join(h["name"] for h in named)
        # cache-control: max-age=0 is a reload artifact; order / HPACK features skip it.
        hl = [h for h in named if h["name"] != "cache-control"]
        f["h2.header_order"] = ",".join(h["name"] for h in hl)
        f["h2.hpack_reps"] = ",".join("%s=%s%s" % (h["name"], h.get("rep", "?")[:9], "/H" if h.get("value_huffman") else "")
                                      for h in hl)
        vals = {}
        for h in named:
            vals.setdefault(h["name"], h["value"])
        for n in DIFF_HEADERS:
            f["hdr.%s" % n] = vals.get(n, "ABSENT")
    h1 = v["h1"]
    if h1 is not None:
        f["h1.method"] = h1["method"]
        f["h1.version"] = h1["version"]
        f["h1.header_order"] = ",".join(h["name"] for h in h1["headers"] if "name" in h)
        vals = {}
        for h in h1["headers"]:
            if "name" in h:
                vals.setdefault(h["name"].lower(), h["value"])
        for n in DIFF_HEADERS:
            f["h1.hdr.%s" % n] = vals.get(n, "ABSENT")
    f["tls.alpn_negotiated"] = v["alpn"]
    return f


def _diff_load(path):
    """{"fresh": [features...], "resumed": [...]} of every record under `path` (ext 41 = resumed)."""
    groups = {}
    for p in capture_paths(path):
        rec = load_capture(p)
        try:
            ft = _diff_features(_diff_view(rec))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, struct.error) as ex:
            raise CaptureConfigError("diff: %s: cannot read the record: %s: %s" % (p, type(ex).__name__, ex))
        if ft is None:
            continue
        kind = "resumed" if "41" in (ft.get("tls.ext_set") or "").split(",") else "fresh"
        groups.setdefault(kind, []).append(ft)
    if not groups:
        raise CaptureConfigError("diff: %s holds no record with a ClientHello or a request" % path)
    return groups


def _diff_base(feat):
    return feat[4:] if feat.startswith("ch2.") else feat


def _diff_summarize(fts):
    """feature -> Counter of observed values (None dropped)."""
    allk = []
    for f in fts:
        for k in f:
            if k not in allk:
                allk.append(k)
    out = {}
    for k in allk:
        c = Counter()
        prefix = "ch2." if k.startswith("ch2.") else ""
        for f in fts:
            dflt = "ABSENT" if (_diff_base(k).startswith("ext.") and (prefix + "tls.legacy_version") in f) else None
            val = f.get(k, dflt)
            if val is not None:
                c[val] += 1
        if c:
            out[k] = c
    return out


def _diff_classify(feat, cc, rc):
    """cc = candidate Counter, rc = reference Counter. Returns (kind, note)."""
    base = _diff_base(feat)
    cs, rs = set(cc), set(rc)
    if cs == rs and len(cs) == 1:
        return "same", ""
    if len(cs) == 1 and len(rs) == 1:
        if base in DIFF_ORDER_DEPENDENT or base in DIFF_RANDOM_VALUED:
            return "UNDECIDED", "varies by design; one value per side cannot tell randomisation from a fixed difference"
        return "FIXED", ""
    if len(rs) > 1 and len(cs) == 1:
        if base in DIFF_ORDER_DEPENDENT:
            return "FROZEN", "reference varies per connection (%d distinct / %d), candidate constant" % (
                len(rs), sum(rc.values()))
        if list(cs)[0] in rs:
            return "FROZEN", "candidate value is one of the reference's %d values but never varies" % len(rs)
        return "FIXED", "candidate constant, reference varies but never takes the candidate value"
    if len(cs) > 1 and len(rs) == 1:
        if list(rs)[0] in cs:
            return "CLIENTVARIES", "candidate varies (%d distinct), reference constant; its value sometimes matched" % len(cs)
        return "FIXED", "candidate varies (%d distinct) but never takes the reference's constant value" % len(cs)
    if base in DIFF_ORDER_DEPENDENT:
        return "random", "both vary per connection"
    if cs <= rs:
        return "random", "both vary, candidate values are a subset of the reference's"
    if cs & rs:
        outside = sum(n for k, n in cc.items() if k not in rs)
        return "PARTIAL", "both vary; %d/%d candidate connections take a value the reference never produced" % (
            outside, sum(cc.values()))
    return "FIXED", "both vary, value sets disjoint"


def _diff_is_http(feat):
    return _diff_base(feat).startswith(("h2.", "hdr.", "h1."))


def diff_captures(reference, candidate):
    """Compare two capture sets. Returns a result dict; its FIXED rows decide the exit code.

    Only a group (fresh / resumed) present on both sides is compared: a resumed
    ClientHello differs from a fresh one by construction (ext 41, the extension
    set, JA4), so a group on one side only is listed as unmatched, never FIXED."""
    ref = _diff_load(reference)
    cand = _diff_load(candidate)
    rsum = dict((g, _diff_summarize(f)) for g, f in ref.items())
    csum = dict((g, _diff_summarize(f)) for g, f in cand.items())
    matched = sorted(g for g in csum if g in rsum)
    if not matched:
        raise CaptureConfigError("diff: no connection group in common (reference: %s; candidate: %s); "
                                 "a fresh ClientHello is never compared with a resumed one"
                                 % (", ".join(sorted(rsum)), ", ".join(sorted(csum))))
    unmatched = ([(g, "candidate", len(cand[g])) for g in sorted(csum) if g not in rsum]
                 + [(g, "reference", len(ref[g])) for g in sorted(rsum) if g not in csum])
    comparisons = []
    for g in matched:
        cs = csum[g]
        rg = g
        rs = rsum[rg]
        rows = dict((k, []) for k in DIFF_KINDS)
        feats = list(cs)
        for k in rs:
            if k not in cs:
                feats.append(k)
        for feat in feats:
            cc, rc = cs.get(feat), rs.get(feat)
            if not cc and not rc:
                continue
            if not cc or not rc:
                side = "candidate" if cc else "reference"
                if _diff_is_http(feat):
                    rows["not_compared"].append((feat, "HTTP layer observed on the %s only" % side, cc, rc))
                else:
                    rows["FIXED"].append((feat, "observed on the %s only" % side, cc, rc))
                continue
            kind, note = _diff_classify(feat, cc, rc)
            if kind == "same":
                continue
            base = _diff_base(feat)
            if base in DIFF_DERIVED:
                note = (note + "; " if note else "") + DIFF_DERIVED[base]
            rows[kind].append((feat, note, cc, rc))
        comparisons.append({"group": g, "reference_group": rg, "rows": rows})
    measured = []
    for side, summ in (("reference", rsum), ("candidate", csum)):
        for g, s in summ.items():
            for feat, c in s.items():
                if len(c) > 1:
                    measured.append((feat, "%s/%s" % (side, g), len(c), sum(c.values())))
    return {"reference": reference, "candidate": candidate,
            "reference_counts": dict((g, len(f)) for g, f in ref.items()),
            "candidate_counts": dict((g, len(f)) for g, f in cand.items()),
            "comparisons": comparisons, "unmatched": unmatched,
            "varying_declared": list(DIFF_VARYING_DECLARED),
            "varying_measured": measured,
            "fixed": [(c["group"], r[0]) for c in comparisons for r in c["rows"]["FIXED"]]}


def _diff_esc(s, lim=160):
    s = str(s)
    if len(s) > lim:
        s = s[:lim] + "..."
    return s.replace("|", "\\|").replace("\n", " ")


def _diff_fmt(c, lim=160):
    if not c:
        return "absent"
    if len(c) == 1:
        return "`%s`" % _diff_esc(list(c)[0], lim)
    if all(isinstance(k, int) for k in c):
        ks = sorted(c)
        return "%d distinct, range %d..%d: %s" % (len(c), ks[0], ks[-1], _diff_esc(ks, lim))
    items = sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if len(c) > 4:
        return "%d distinct (e.g. `%s`)" % (len(c), _diff_esc(items[0][0], 80))
    return "; ".join("`%s` x%d" % (_diff_esc(k, lim // 2), n) for k, n in items)


def diff_feature_label(feat):
    """`ext.13` -> `ext.13 (signature_algorithms)`; any other feature unchanged."""
    base = _diff_base(feat)
    if base.startswith("ext.") and base[4:].isdigit():
        t = int(base[4:])
        return "%s (%s)" % (feat, EXT_NAMES.get(t) or _DIFF_EXTRA_EXT_NAMES.get(t, "unknown"))
    return feat


def diff_unmatched_lines(result):
    """One `UNMATCHED <group>: ...` line per group present on one side only."""
    return ["UNMATCHED %s: present only in %s (%d connection%s); not compared"
            % (g, side, n, "" if n == 1 else "s") for g, side, n in result["unmatched"]]


def render_diff(result):
    """The diff result as a Markdown report."""
    nfixed = len(result["fixed"])
    L = ["# chrome_capture diff", ""]
    L.append("- reference: `%s` (%s)" % (result["reference"], ", ".join(
        "%s=%d" % kv for kv in sorted(result["reference_counts"].items()))))
    L.append("- candidate: `%s` (%s)" % (result["candidate"], ", ".join(
        "%s=%d" % kv for kv in sorted(result["candidate_counts"].items()))))
    L.append("")
    L.append("## RESULT: %s" % ("no fixed difference" if not nfixed else "%d fixed difference(s)" % nfixed))
    L.append("")
    for g, feat in result["fixed"]:
        L.append("- FIXED %s %s" % (g, diff_feature_label(feat)))
    if nfixed:
        L.append("")
    for line in diff_unmatched_lines(result):
        L.append("- " + line)
    if result["unmatched"]:
        L.append("")
    titles = (("FIXED", "Fixed differences (decide the exit code)"),
              ("UNDECIDED", "Undecided: varies by design, one value per side"),
              ("FROZEN", "Reference randomises, candidate frozen"),
              ("PARTIAL", "Both randomise, candidate leaves the reference's value set"),
              ("CLIENTVARIES", "Candidate varies, reference constant"),
              ("random", "Randomised on both sides, not a difference"),
              ("not_compared", "Not compared"))
    for comp in result["comparisons"]:
        L.append("## candidate/%s vs reference/%s" % (comp["group"], comp["reference_group"]))
        L.append("")
        for kind, title in titles:
            rows = comp["rows"][kind]
            if not rows:
                continue
            L.append("### %s" % title)
            L.append("")
            L.append("| feature | candidate | reference | note |")
            L.append("|---|---|---|---|")
            for feat, note, cc, rc in rows:
                L.append("| %s | %s | %s | %s |" % (_diff_esc(diff_feature_label(feat)), _diff_fmt(cc, 400),
                                                    _diff_fmt(rc, 400), _diff_esc(note, 400)))
            L.append("")
    L.append("## varying")
    L.append("")
    L.append("Declared (normalised away or compared modulo randomness):")
    L.append("")
    for field, why in result["varying_declared"]:
        L.append("- `%s`: %s" % (field, why))
    L.append("")
    L.append("Measured (takes more than one value across the connections of one side):")
    L.append("")
    if not result["varying_measured"]:
        L.append("- none")
    for feat, where, distinct, n in result["varying_measured"]:
        L.append("- `%s` in %s: %d distinct / %d" % (diff_feature_label(feat), where, distinct, n))
    L.append("")
    return "\n".join(L)


def cmd_diff(opts, out=None):
    """`diff`: exit 0 when no matched group has a fixed difference, 1 when at least one; the report
    names each field. An unmatched group (one side only) is listed and never moves the exit code."""
    out = out or sys.stdout
    result = diff_captures(opts.reference, opts.candidate)
    text = render_diff(result)
    nfixed = len(result["fixed"])
    if opts.out:
        try:
            with open(opts.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as ex:
            raise CaptureConfigError("diff: cannot write %s: %s" % (opts.out, ex.strerror or ex))
        print("diff: %s" % ("no fixed difference" if not nfixed else "%d fixed difference(s)" % nfixed), file=out)
        for g, feat in result["fixed"]:
            print("FIXED %s %s" % (g, diff_feature_label(feat)), file=out)
        for line in diff_unmatched_lines(result):
            print(line, file=out)
        print("wrote %s" % opts.out, file=out)
    else:
        out.write(text)
    return 1 if nfixed else 0


# --------------------------------------------------------------------------
# ja4: the JA4 of a capture record or an exported fixture
# --------------------------------------------------------------------------
def cmd_ja4(opts, out=None):
    """Print the JA4 of the file's ClientHello (and ` client_hello_2` for a post-HRR one)."""
    out = out or sys.stdout
    rec = load_capture(opts.file)
    hrr = rec.get("hrr") if isinstance(rec.get("hrr"), dict) else {}
    found = 0
    for suffix, hexstr, decoded in (
            ("", rec.get("client_hello_hex") or rec.get("client_hello_raw_hex"), rec.get("client_hello")),
            (" client_hello_2", hrr.get("client_hello_2_hex") or hrr.get("client_hello_2_raw_hex"),
             hrr.get("client_hello_2"))):
        try:
            if hexstr:
                hs, _lengths = handshake_from_hex(hexstr)
                fp = fingerprints(parse_client_hello(hs[4:]))
            elif isinstance(decoded, dict):
                fp = fingerprints_of_decoded(decoded)
            else:
                continue
        except (ValueError, KeyError, TypeError, IndexError, struct.error) as ex:
            print("chrome_capture: ja4: %s: %s" % (opts.file, ex), file=sys.stderr)
            return 1
        print(fp["ja4"] + suffix, file=out)
        found += 1
    if not found:
        print("chrome_capture: ja4: %s carries no ClientHello" % opts.file, file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------
# export: a reduced, machine-independent fixture per captured connection
# --------------------------------------------------------------------------
EXPORT_LABELS = ("navigate", "cors-get", "cors-post", "cors-head", "nav-cors-pair", "h1-tls", "h1-plain",
                 "ip-literal", "cookie", "hrr")
FIXTURE_FORMAT = "chrome_capture/fixture/v1"
_EXPORT_FP_KEYS = ("ja3", "ja3_md5", "ja4", "ja4_r", "ja4_o", "ja4_ro")
_EXPORT_H2_FRAME_KEYS = ("type", "name", "flags", "stream_id", "length", "ack", "settings", "increment",
                         "priority", "error_code", "pad_len", "payload_hex")
_EXPORT_H2_STREAM_KEYS = ("stream_id", "headers_flags", "headers_priority", "pad_len", "end_stream",
                          "rst_stream", "body_len")
_EXPORT_DATA_KEYS = ("length", "flags", "payload_len", "payload_hex", "payload_truncated", "pad_len")
_EXPORT_H1_KEYS = ("body_len", "body_hex", "body_truncated", "chunked", "chunk_sizes")
_EXPORT_HRR_KEYS = ("group", "group_name", "group_offered", "group_already_shared", "cipher", "cookie_hex",
                    "hello_retry_request_hex", "ccs_before_client_hello_2", "cookie_echoed",
                    "key_share_groups_2", "end_reason")
_EXPORT_HRR_RECORD_KEYS = ("type", "version", "length", "alert")
_EXPORT_ERROR_KEYS = ("client_hello_error", "handshake_error", "http_error", "hrr_error", "fatal_error")
_EXPORT_URL_FIELDS = ("origin", "referer")
_EXPORT_AUTHORITY_FIELDS = (":authority", "host")
# Request headers that carry a credential by name. A header ending in -token or
# containing "auth" is refused too (is_credential_header); nothing Chrome sends to
# a loopback capture server matches, so a match is always someone's secret.
_EXPORT_CREDENTIAL_HEADERS = ("authorization", "proxy-authorization", "x-client-data", "x-api-key")
# The request bodies the scripted capture sets send -- the README's "Exact
# JavaScript evaluated" section: cors-post POSTs this form. Any other non-empty
# body is refused unless the operator names it with `export --allow-body`.
EXPORT_SCRIPTED_BODIES = (b"q=test&kl=",)
_EXPORT_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_CHROME_VERSION_RX = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,6}){0,3}$")
_UA_CHROME_RX = re.compile(r"Chrome/([0-9]{1,4}(?:\.[0-9]{1,6}){0,3})")
_LOOPBACK_LABEL_RX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ExportRefused(ValueError):
    """A capture export refuses: a non-loopback identifier, a foreign cookie, or a malformed record."""


def is_loopback_name(host):
    """True for localhost, *.localhost (well-formed labels) and 127.0.0.1 -- nothing else."""
    if host in ("localhost", LOOPBACK_HOST):
        return True
    if host.endswith(".localhost"):
        labels = host[:-len(".localhost")].split(".")
        return all(_LOOPBACK_LABEL_RX.match(label) for label in labels)
    return False


def _shown(value):
    value = str(value)
    return repr(value[:80] + ("..." if len(value) > 80 else ""))


def _authority_host(value):
    """The host of `host[:port]`, or None when it carries userinfo, a bad port or no host."""
    v = value.strip()
    if not v or "@" in v:
        return None
    if ":" in v:
        host, _sep, port = v.rpartition(":")
        if not port.isdigit():
            return None
    else:
        host = v
    return host.lower() or None


def _url_host(value):
    """The host of an http(s) URL, or None when it carries userinfo, a bad port or no host."""
    try:
        parts = urllib.parse.urlsplit(value.strip())
        _port = parts.port  # raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return None
    return parts.hostname


def is_credential_header(name):
    """True for a request header that carries a credential: the named ones, *-token, *auth*.

    The authority fields (:authority, host) are host-bearing, not credentials, and
    are checked as hosts before this is asked."""
    n = name.lower()
    return n in _EXPORT_CREDENTIAL_HEADERS or n.endswith("-token") or "auth" in n


def _export_check_headers(where, pairs, allowed_cookies):
    """Refuse a host-bearing field naming a non-loopback host, a credential header, or a cookie
    the server did not set (by name, and by value when the record says what the value was).

    `allowed_cookies` maps each server-set cookie name to its value, or to None when
    the record predates `server_set_cookies` and only the name is known. A refusal
    names the header or cookie, never the value it carried."""
    for name, value in pairs:
        n = name.lower()
        if not isinstance(value, str):
            raise ExportRefused("%s: %s value is not a string" % (where, n))
        if n in _EXPORT_AUTHORITY_FIELDS or n in _EXPORT_URL_FIELDS:
            if n == "origin" and value.strip() == "null":
                # an opaque origin names no host at all
                continue
            host = _url_host(value) if n in _EXPORT_URL_FIELDS else _authority_host(value)
            if host is None or not is_loopback_name(host):
                raise ExportRefused("%s: %s names %s, not localhost, *.localhost or %s" % (
                    where, n, _shown(value), LOOPBACK_HOST))
        elif n == "cookie":
            for part in value.split(";"):
                part = part.strip()
                if not part:
                    continue
                cname, _eq, cvalue = part.partition("=")
                cname = cname.strip()
                if cname not in allowed_cookies:
                    raise ExportRefused("%s: cookie %s was not set by the capture server (it set: %s)" % (
                        where, _shown(cname), ", ".join(sorted(allowed_cookies)) or "none"))
                want = allowed_cookies[cname]
                if want is not None and cvalue.strip() != want:
                    raise ExportRefused("%s: cookie %s carries a value other than the one the capture "
                                        "server set" % (where, _shown(cname)))
        elif is_credential_header(n):
            raise ExportRefused("%s: %s is a credential header; a fixture never carries one" % (where, n))


def _export_check_target(where, target):
    if target.startswith("/") or target == "*":
        return
    host = _url_host(target) if "://" in target else _authority_host(target)
    if host is None or not is_loopback_name(host):
        raise ExportRefused("%s: request-target names %s, not localhost, *.localhost or %s" % (
            where, _shown(target), LOOPBACK_HOST))


def _export_client_hello(hexstr, where):
    """(fixture fields, parsed ClientHello) of one ClientHello; refuses a non-loopback SNI."""
    try:
        hs, lengths = handshake_from_hex(hexstr)
        ch = parse_client_hello(hs[4:])
        fp = fingerprints(ch)
        names = client_hello_sni(ch)
    except (ValueError, IndexError, KeyError, struct.error) as ex:
        raise ExportRefused("%s: %s" % (where, ex))
    for name in names:
        if not is_loopback_name(name.lower()):
            raise ExportRefused("%s: SNI names %s, not localhost, *.localhost or %s" % (
                where, _shown(name), LOOPBACK_HOST))
    return {"hex": hs.hex(), "record_lengths": lengths or [], "sni": names[0] if names else None,
            "ech_payload_len": client_hello_ech_payload_len(ch),
            "fingerprints": dict((k, fp[k]) for k in _EXPORT_FP_KEYS)}, ch


def _pick(d, keys):
    return dict((k, d[k]) for k in keys if k in d)


def _named_pairs(headers):
    return [(h["name"], h["value"]) for h in headers if isinstance(h, dict) and "name" in h]


def _export_decode(dec, block_hex, recorded, where, allowed):
    """Re-decode a header block; the recorded headers are checked first, then the decoded ones."""
    if isinstance(recorded, list):
        _export_check_headers(where, _named_pairs(recorded), allowed)
    try:
        block = bytes.fromhex(block_hex)
        if len(block) > MAX_HEADER_BLOCK_BYTES:
            raise ValueError("header block exceeds %d bytes" % MAX_HEADER_BLOCK_BYTES)
        hdrs = dec.decode(block)
    except (ValueError, TypeError) as ex:
        raise ExportRefused("%s: header_block_hex does not decode: %s" % (where, ex))
    _export_check_headers(where, _named_pairs(hdrs), allowed)
    if recorded is not None and recorded != hdrs:
        raise ExportRefused("%s: headers differ from what header_block_hex decodes to" % where)
    return hdrs


def _hex_bytes(value, where):
    if value is None or value == "":
        return b""
    try:
        return bytes.fromhex(value)
    except (ValueError, TypeError):
        raise ExportRefused("%s: body payload is not hex" % where)


def _export_check_body(where, body, truncated, bodies):
    """Refuse a non-empty request body that is not one of `bodies` (the scripted ones plus
    any the operator allowed). A truncated body is refused: its recorded prefix proves
    nothing about the rest. The body itself is never echoed, only its length."""
    if not body and not truncated:
        return
    if truncated:
        raise ExportRefused("%s: request body was truncated in the capture; it cannot be reviewed" % where)
    if body not in bodies:
        raise ExportRefused("%s: request body of %d bytes is not one the scripted sets send; "
                            "review it and pass --allow-body to export it" % (where, len(body)))


def _export_h2(h2, allowed, bodies):
    streams = h2.get("streams")
    if streams is None:
        # the older single-stream record shape
        streams = [] if h2.get("header_block_hex") is None else [{
            "stream_id": h2.get("stream_id", 1), "header_block_hex": h2.get("header_block_hex"),
            "headers": h2.get("headers"), "headers_priority": h2.get("headers_priority")}]
    frames = h2.get("frames") or []
    if not isinstance(streams, list) or not isinstance(frames, list) or len(frames) > MAX_H2_FRAMES:
        raise ExportRefused("h2: streams/frames are not lists of at most %d entries" % MAX_H2_FRAMES)
    dec = HpackDecoder()
    out_streams = []
    for st in streams:
        where = "h2 stream %s" % st.get("stream_id")
        o = _pick(st, _EXPORT_H2_STREAM_KEYS)
        if st.get("header_block_hex") is not None:
            hdrs = _export_decode(dec, st["header_block_hex"], st.get("headers"), where, allowed)
            o["header_block_hex"] = st["header_block_hex"]
            o["headers"] = hdrs
            o["header_order"] = [h["name"] for h in hdrs if "name" in h]
        tr = st.get("trailers")
        if isinstance(tr, dict) and tr.get("header_block_hex") is not None:
            thdrs = _export_decode(dec, tr["header_block_hex"], tr.get("headers"), where + " trailers", allowed)
            o["trailers"] = {"header_block_hex": tr["header_block_hex"], "headers": thdrs}
        data = [_pick(d, _EXPORT_DATA_KEYS) for d in (st.get("data_frames") or [])]
        _export_check_body(where, b"".join(_hex_bytes(d.get("payload_hex"), where) for d in data),
                           any(d.get("payload_truncated") for d in data), bodies)
        o["data_frames"] = data
        out_streams.append(o)
    out_frames = [_pick(f, _EXPORT_H2_FRAME_KEYS) for f in frames]
    for f in out_frames:
        # serve keeps a DATA payload in its stream's data_frames only; one in the
        # frame list bypasses the body check above, so it is refused outright
        if f.get("type") == 0 and f.get("payload_hex"):
            raise ExportRefused("h2 frame list: a DATA frame on stream %s carries a payload "
                                "outside data_frames" % f.get("stream_id"))
    preface = []
    for f in out_frames:
        if f.get("type") == 1:
            break
        preface.append(f)
    goaway = h2.get("goaway_received")
    return {"preface_ok": h2.get("preface_ok"), "preface_frames": preface, "frames": out_frames,
            "settings_acks": h2.get("settings_acks"), "akamai": h2.get("akamai"),
            "goaway_received": _pick(goaway, ("last_stream_id", "error_code")) if isinstance(goaway, dict) else None,
            "end_reason": h2.get("end_reason"), "streams": out_streams}


def _export_h1(h1, allowed, bodies):
    reqs = h1.get("requests")
    if reqs is None:
        reqs = [h1] if h1.get("request_head") is not None else []
    if not isinstance(reqs, list) or len(reqs) > MAX_H1_REQUESTS:
        raise ExportRefused("h1: requests is not a list of at most %d entries" % MAX_H1_REQUESTS)
    out = []
    for i, req in enumerate(reqs):
        where = "h1 request %d" % (i + 1)
        head = req.get("request_head")
        if not isinstance(head, str) or len(head) > MAX_H1_HEAD_BYTES:
            raise ExportRefused("%s: no request_head of at most %d bytes" % (where, MAX_H1_HEAD_BYTES))
        if isinstance(req.get("headers"), list):
            _export_check_headers(where, _named_pairs(req["headers"]), allowed)
        lines = head.split("\r\n")
        rl = lines[0].split(" ")
        if len(rl) != 3:
            raise ExportRefused("%s: request line is not METHOD TARGET VERSION" % where)
        _export_check_target(where, rl[1])
        hdrs = []
        for line in lines[1:]:
            if ":" not in line or line[:1] in (" ", "\t"):
                raise ExportRefused("%s: request_head carries a line that is not a header field" % where)
            k, val = line.split(":", 1)
            hdrs.append({"name": k, "value": val.strip()})
        _export_check_headers(where, _named_pairs(hdrs), allowed)
        o = {"request_head": head, "method": rl[0], "target": rl[1], "version": rl[2], "headers": hdrs,
             "header_order": [h["name"] for h in hdrs]}
        o.update(_pick(req, _EXPORT_H1_KEYS))
        _export_check_body(where, _hex_bytes(o.get("body_hex"), where), bool(o.get("body_truncated")), bodies)
        out.append(o)
    return {"request_head": out[0]["request_head"] if out else None, "requests": out,
            "end_reason": h1.get("end_reason")}


def _export_hrr(hrr):
    o = _pick(hrr, _EXPORT_HRR_KEYS)
    o["records"] = [_pick(r, _EXPORT_HRR_RECORD_KEYS) for r in (hrr.get("records") or []) if isinstance(r, dict)]
    hexstr = hrr.get("client_hello_2_raw_hex") or hrr.get("client_hello_2_hex")
    if hexstr:
        fields, _ch = _export_client_hello(hexstr, "client_hello_2")
        o["client_hello_2_hex"] = fields["hex"]
        o["client_hello_2_record_lengths"] = fields["record_lengths"]
        o["sni_2"] = fields["sni"]
        o["ech_payload_len_2"] = fields["ech_payload_len"]
        o["fingerprints_2"] = fields["fingerprints"]
    return o


def _local_identifiers():
    """(kind, text) pairs a fixture must never carry: this machine's paths and hostname."""
    out = []
    home = os.path.expanduser("~")
    if home and home not in ("/", "~"):
        out.append(("home directory", home))
    out.append(("repository path", REPO_ROOT))
    try:
        host = socket.gethostname().strip().lower()
    except OSError:
        host = ""
    for h in (host, host.split(".", 1)[0]):
        # a short or hex-only name would match inside hex payloads by chance
        if len(h) >= 4 and not is_loopback_name(h) and re.search(r"[^0-9a-f]", h):
            out.append(("machine hostname", h))
    return out


def _chrome_version_of(fx):
    uas = []
    for st in ((fx.get("h2") or {}).get("streams") or []):
        uas += [h["value"] for h in st.get("headers") or [] if h.get("name") == "user-agent"]
    for req in ((fx.get("h1") or {}).get("requests") or []):
        uas += [h["value"] for h in req["headers"] if h["name"].lower() == "user-agent"]
    for ua in uas:
        m = _UA_CHROME_RX.search(ua)
        if m:
            return m.group(1)
    return None


def build_fixture(rec, label, source, chrome_version=None, allowed_bodies=None):
    """The reduced fixture of one capture record; None when it holds no ClientHello and no request.

    Only allowlisted fields are copied, so the capture's peer address, timings,
    error tracebacks and local paths never reach the fixture. A request body must
    be empty, one of EXPORT_SCRIPTED_BODIES or one of `allowed_bodies` (bytes).
    Raises ExportRefused."""
    names = rec.get("server_set_cookie_names") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ExportRefused("server_set_cookie_names: not a list of cookie names")
    values = rec.get("server_set_cookies") or {}
    if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in values.items()):
        raise ExportRefused("server_set_cookies: not a map of cookie names to values")
    allowed = dict((n, values.get(n)) for n in names)
    bodies = set(EXPORT_SCRIPTED_BODIES) | set(allowed_bodies or ())
    fx = {"format": FIXTURE_FORMAT, "label": label, "meta": None}
    hexstr = rec.get("client_hello_raw_hex") or rec.get("client_hello_hex")
    have = False
    if hexstr:
        fields, _ch = _export_client_hello(hexstr, "client_hello")
        fx["client_hello_hex"] = fields["hex"]
        fx["client_hello_record_lengths"] = fields["record_lengths"]
        fx["sni"] = fields["sni"]
        fx["ech_payload_len"] = fields["ech_payload_len"]
        fx["fingerprints"] = fields["fingerprints"]
        have = True
    tls = rec.get("tls")
    fx["tls"] = _pick(tls, ("version", "alpn")) if isinstance(tls, dict) else None
    fx["h2"] = _export_h2(rec["h2"], allowed, bodies) if isinstance(rec.get("h2"), dict) else None
    h1 = rec.get("http1") if isinstance(rec.get("http1"), dict) else rec.get("h1")
    fx["h1"] = _export_h1(h1, allowed, bodies) if isinstance(h1, dict) else None
    fx["hrr"] = _export_hrr(rec["hrr"]) if isinstance(rec.get("hrr"), dict) else None
    have = have or bool(fx["h2"] and fx["h2"]["streams"]) or bool(fx["h1"] and fx["h1"]["requests"])
    if not have:
        return None
    fx["server_set_cookie_names"] = sorted(allowed)
    fx["capture_errors"] = [k for k in _EXPORT_ERROR_KEYS if rec.get(k)]
    when = rec.get("time")
    fx["meta"] = {
        "chrome_version": chrome_version or _chrome_version_of(fx),
        "captured_on": time.strftime("%Y-%m-%d", time.gmtime(when)) if isinstance(when, (int, float)) else None,
        "mode": rec.get("mode") if isinstance(rec.get("mode"), str) else None,
        "source": source,
        "index": rec.get("index") if isinstance(rec.get("index"), int) else None,
        "exported_by": "chrome_capture.py export",
    }
    text = json.dumps(fx).lower()
    for kind, ident in _local_identifiers():
        if ident.lower() in text:
            raise ExportRefused("fixture would carry the %s" % kind)
    return fx


def cmd_export(opts, out=None):
    """`export`: validate every capture first, write nothing when one is refused (exit 1)."""
    out = out or sys.stdout
    if opts.label not in EXPORT_LABELS:
        raise CaptureConfigError("export: label %r is not one of %s" % (opts.label, ", ".join(EXPORT_LABELS)))
    if opts.chrome_version is not None and not _CHROME_VERSION_RX.match(opts.chrome_version):
        raise CaptureConfigError("export: --chrome-version %r is not a dotted version" % opts.chrome_version)
    paths = capture_paths(opts.source)
    stem = None
    if opts.name is not None:
        stem = opts.name[:-5] if opts.name.endswith(".json") else opts.name
        if not _EXPORT_NAME_RX.match(stem):
            raise CaptureConfigError("export: --name %r is not a plain file name" % opts.name)
        if len(paths) != 1:
            raise CaptureConfigError("export: --name needs exactly one capture; %s holds %d" % (opts.source, len(paths)))
    bodies = [b.encode("utf-8") for b in (getattr(opts, "allow_body", None) or [])]
    fixtures = []
    for p in paths:
        rec = load_capture(p)
        base = os.path.basename(p)
        try:
            fx = build_fixture(rec, opts.label, base, opts.chrome_version, bodies)
        except ExportRefused as ex:
            print("chrome_capture: export: refusing %s: %s" % (base, ex), file=sys.stderr)
            return 1
        except (KeyError, TypeError, AttributeError, IndexError, ValueError, struct.error) as ex:
            print("chrome_capture: export: refusing %s: malformed record: %s: %s" % (base, type(ex).__name__, ex),
                  file=sys.stderr)
            return 1
        if fx is None:
            print("export: skipping %s: no ClientHello and no request" % base, file=out)
            continue
        fixtures.append(fx)
    if not fixtures:
        print("chrome_capture: export: nothing to export in %s" % opts.source, file=sys.stderr)
        return 1
    targets = []
    for i, fx in enumerate(fixtures, 1):
        fname = (stem if stem is not None else "%s-%03d" % (opts.label, i)) + ".json"
        path = os.path.join(opts.to, fname)
        if os.path.exists(path) and not opts.force:
            raise CaptureConfigError("export: %s exists; pass --force to overwrite" % path)
        targets.append((path, fx))
    os.makedirs(opts.to, exist_ok=True)
    for path, fx in targets:
        tmp = path + ".tmp"
        # A stale .tmp (a killed run, or a planted symlink) is unlinked first --
        # unlink removes a symlink itself, never its target -- and the new one is
        # created O_EXCL|O_NOFOLLOW, so the write never lands through a link.
        if os.path.lexists(tmp):
            os.remove(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(fx, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.remove(tmp)
        print("wrote %s ja4=%s" % (path, (fx.get("fingerprints") or {}).get("ja4")), file=out)
    print("export: %d fixture(s) in %s" % (len(targets), opts.to), file=out)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="chrome_capture.py",
                                 description="Record Chrome's TLS ClientHello and HTTP requests on loopback.")
    sub = ap.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("serve", help="listen on 127.0.0.1 and write one JSON record per connection")
    sp.add_argument("--port", type=int, required=True, help="port to bind (0 = any free port)")
    sp.add_argument("--out", required=True, help="directory for the JSON records")
    sp.add_argument("--label", required=True, help="record file prefix")
    sp.add_argument("--count", type=int, default=20, help="connections to record (default 20)")
    sp.add_argument("--timeout", type=float, default=300.0, help="overall seconds to keep listening")
    sp.add_argument("--idle", type=float, default=DEFAULT_IDLE,
                    help="seconds a connection may stay silent before it is closed (default %.1f)" % DEFAULT_IDLE)
    sp.add_argument("--host", default=LOOPBACK_HOST, help="bind host; only 127.0.0.1 is accepted")
    sp.add_argument("--alpn", default="h2,http/1.1", help="ALPN offered by the server: h2,http/1.1 or http/1.1")
    sp.add_argument("--plain", action="store_true", help="plaintext HTTP/1.1, no TLS")
    sp.add_argument("--page", help="HTML file served for a GET navigation")
    sp.add_argument("--set-cookie", action="append", metavar="NAME=VALUE",
                    help="cookie a GET of / sets (repeatable)")
    sp.add_argument("--hrr-group", help="answer ClientHello 1 with a HelloRetryRequest for this group (e.g. 0x0017)")
    sp.add_argument("--cert", help="server certificate (PEM)")
    sp.add_argument("--key", help="server private key (PEM)")
    dp = sub.add_parser("diff", help="compare a candidate capture set with a reference set; exit 1 on a fixed difference")
    dp.add_argument("--reference", required=True, help="reference capture directory (or one file)")
    dp.add_argument("--candidate", required=True, help="candidate capture directory (or one file)")
    dp.add_argument("--out", help="write the Markdown report here instead of stdout")
    jp = sub.add_parser("ja4", help="print the JA4 of a capture record or an exported fixture")
    jp.add_argument("file", help="capture record or fixture JSON")
    ep = sub.add_parser("export", help="write a reduced, machine-independent fixture per captured connection")
    ep.add_argument("--from", dest="source", required=True, help="capture directory (or one capture file)")
    ep.add_argument("--to", required=True, help="fixture directory, e.g. tests/files/chrome/<major>/")
    ep.add_argument("--label", required=True, choices=EXPORT_LABELS, help="fixture label")
    ep.add_argument("--name", help="output file stem instead of <label>-NNN; only with exactly one capture")
    ep.add_argument("--chrome-version", help="Chrome version to record (default: from the captured user-agent)")
    ep.add_argument("--force", action="store_true", help="overwrite existing fixture files")
    ep.add_argument("--allow-body", action="append", metavar="TEXT",
                    help="a request body (UTF-8 text) to export besides the scripted sets' own; "
                         "repeatable; review it first")
    return ap


def main(argv=None):
    opts = build_parser().parse_args(argv)
    try:
        if opts.command == "serve":
            return serve(opts)
        if opts.command == "diff":
            return cmd_diff(opts)
        if opts.command == "ja4":
            return cmd_ja4(opts)
        if opts.command == "export":
            return cmd_export(opts)
    except CaptureConfigError as ex:
        print("chrome_capture: %s" % ex, file=sys.stderr)
        return 2
    except OSError as ex:
        print("chrome_capture: %s: %s" % (type(ex).__name__, ex), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
