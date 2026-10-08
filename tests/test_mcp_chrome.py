#!/usr/bin/env python3
"""The stdlib Chrome client's crypto and TLS 1.3 layers -- `Scripts/_mcp_chrome.py` (Gates G3, G4, G5).

`Scripts/_mcp_chrome.py` is a canonical generation source (roadmap R-0044),
registered in `Scripts/amalgamate.py` CANONICAL_NAMES and WHOLE_SOURCES and
carried whole by four hosts (mcp-webfetch.py, mcp-search.py,
search_duckduckgo.py, search_github.py). The generated_region drift gate proves
each copy equals the source; this suite proves the source right. It loads the
file by path with `H.load_module_from_path` and drives it; groups M and O load
three hosts' copies (mcp-search.py's is driven by tests/test_mcp_search.py).

THE ORACLE IS NOT THE MODULE. Every expected value in group A is a published
vector pasted here as hex, each citing where it was copied from: RFC 7748
(X25519, sections 5.2 and 6.1), RFC 5903 section 8.1 (P-256 ECDH), the C2SP
Wycheproof `aes_gcm_test.json` (AES-GCM, by tcId), RFC 8439 (ChaCha20 2.4.2,
Poly1305 key generation 2.6.2, AEAD 2.8.2 and A.5), RFC 5869 appendix A.1-A.3
(HKDF-SHA256) and RFC 8448 section 3 (the TLS 1.3 key schedule of the simple
1-RTT trace). Nothing here is computed by the module first and then pinned.
ML-KEM-768 has no offline published vector in this tree: it is proven by an
OpenSSL >= 3.5 cross-check in BOTH directions (OpenSSL encapsulates to our key
and we decapsulate; we encapsulate to an OpenSSL key and it decapsulates), plus
FIPS 203's implicit-rejection rule written here as `SHAKE256(z || c, 32)`.

The TLS groups keep the same rule. Group B judges the ClientHello with the
INDEPENDENT parser of `Scripts/chrome_capture.py` (loaded by path) against the
committed Chrome 154 captures and the numbers their README states; the module
never grades its own builder. Group C replays the RFC 8448 section 3 records
pasted below, runs real TLS stacks (Python ssl over MemoryBIO and a loopback
socket, `openssl s_server`), and a scripted TLS 1.3 peer written here: its key
schedule, transcript, Finished values and record layer are hmac/hashlib code in
this file; its key agreement and AEAD are the module's primitives, used only
because group A proves each one by published vectors first -- a declared,
partial self-agreement, cross-checked by the independent stacks. Group H drives
the HelloRetryRequest path through the same peer and the set 9 captures. Group
D holds RFC 7541 Appendix B and the Appendix C.2-C.6 vectors as copied out of
the RFC text by script, and replays every HEADERS block committed under
tests/files/chrome/154/ (decoded and cross-checked against chrome_capture's
HpackDecoder, re-encoded byte for byte). Group E drives _ChH2Connection
against H2Responder, an h2 server side written here from RFC 9113: its frames
are h2f(), its response heads plain RFC 7541 literals, and the client's own
blocks are decoded by chrome_capture; the first flight is compared byte for
byte with all h2 capture connections and the Akamai string of
docs/concepts/spec-ddg.md:448, and every ceiling (CH_MAX_H2_*,
CH_MAX_HEADER_*, CH_MAX_FRAME_BYTES) is read off the module and probed at N
and N+1. Group
F compares the HTTP/1.1 request head byte for byte with the committed set 5
(h1-tls/) and set 6 (h1-plain/) captures, and drives _ChH1Connection against a
scripted HTTP/1.1 peer written here, once over a TLS 1.3 Python ssl server
with ALPN http/1.1 and once over a cleartext socket; every ceiling is read off
the module (CH_MAX_H1_*, CH_MAX_BODY_BYTES), never typed. Group G drives the
whole session (_ch_session_new) against threaded loopback peers written here:
their request heads are decoded by chrome_capture's HpackDecoder, a connect
policy maps every test host to 127.0.0.1, the cookie slot is judged against
the committed cookie/ capture, and every cap is read off the module (the jar
total lowered through the module attribute and restored in finally). Group I
keeps that shape for the limits and the connect policy: the budgets are read
off the module and lowered only through its attributes (restored in finally),
gzip and raw-deflate bodies are built by the stdlib gzip/zlib modules, every
SSRF form is its own row with the exact reason (or, where an ipaddress table
chooses the word, the stdlib property that must still read as it did), and
socket.getaddrinfo is replaced by recorders. Group J runs the TLS 1.2 fallback
against a Python ssl server capped at TLS 1.2 on the tests/files/tls leaf,
verified with the committed test CA as the ONLY anchor, and reads the module's
AST for trust material, the environment and the late binding of
_ch_open_socket -- each checker with planted defects it must catch. Groups G,
I and J mean the Chrome path and say so (transport="chrome", through the
with_session / with_policy setdefault or at the call site). Group P drives the
session's DEFAULT, the verified stdlib transport, against a Python ssl h1
server on the same leaf with the test CA as the only anchor, and judges the
chain label a redirect across transports earns.

WHERE A HOST CAN SAY NO. The OpenSSL rows need an `openssl` binary; the ML-KEM
ones and the `s_server` group/suite matrix need OpenSSL 3.5 or later. When it is absent each such row is recorded as
INFO ("SKIPPED: ...") rather than omitted, so the case count is the same on every
host. The optional `tests/files/mlkem/` vector row is INFO while that directory
does not exist. Throughput is measured, not asserted -- except the ONE floor the
plan sets (AES-256-GCM decrypt >= 0.5 MiB/s, G3), the only group-L row that can
FAIL, and only below a tenth of that floor: a rate in [floor/10, floor) is INFO
carrying the measured value and a load caveat, because a wall-clock rate flaps
under concurrent load and FAIL is reserved for rules that cannot flap
(docs/subsystems/tests.md); a tenfold shortfall is an algorithmic regression no
load explains. The profile-age row is INFO forever (ADR 0019). The Python ssl server
restricted with set_ecdh_curve("prime256v1") is INFO when that call does not
restrict the TLS 1.3 groups on the local OpenSSL (no HRR happens); the
`s_server -groups P-256` row proves the HRR path then.

Groups:
  A. CRYPTO KATs:  X25519, P-256, AES-GCM (Wycheproof), ChaCha20-Poly1305, HKDF,
                   the RFC 8448 key schedule, ML-KEM-768 round trip + OpenSSL
  B. CLIENTHELLO:  20 deterministic seeds: 17 non-GREASE extensions, GREASE
                   positions, a varying permutation, ECH payload buckets, the
                   length rule, the README's JA4, no SNI for an IP literal,
                   `chrome_capture diff` against eight capture sets, and a
                   profile missing one sigalg that the diff must catch
  C. TLS 1.3:      MemoryBIO and loopback ssl servers, the RFC 8448 trace
                   (flight decrypt, Finished, replay, tampering), the scripted
                   peer (X25519 / X25519MLKEM768 x three suites, client ALPS
                   EE, TLS 1.2 answer, record and handshake ceilings, bad
                   Finished and tag, alerts, KeyUpdate and its bound, tickets,
                   unknown post-handshake types, short key shares, sequence
                   exhaustion) and the openssl s_server matrix
  H. HRR:          P-256 completion per suite over the message_hash
                   transcript, CH2 on the wire and against set 9, every
                   refused HelloRetryRequest, a P-256-only ssl server
  D. HPACK:        the derived Huffman code vs RFC 7541 Appendix B; C.2-C.6
                   decode (with and without Huffman), C.4 encode; every
                   committed h2 HEADERS block decoded (vs chrome_capture) and
                   re-encoded byte-identical per set, with the derived total;
                   _ch_profile_headers vs each set's lists; index 0, out of
                   range, integer and continuation caps, bad padding, EOS,
                   table size update at and over CH_HPACK_TABLE_BYTES; every
                   caller-header rule with its exact one-line message;
                   x-custom lowercase on h2; a swapped navigate_order that
                   must FAIL
  E. HTTP/2:       first flight vs every h2 capture connection and the Akamai
                   string; frame size from the header; CONTINUATION by bytes,
                   frames and empty run; SETTINGS / PING / RST_STREAM /
                   unknown / mixed control floods and the unsolicited second
                   ACK at N and N+1; empty DATA flood; a header list of
                   exactly CH_MAX_HEADER_LIST_BYTES and one over;
                   WINDOW_UPDATE 0 and to 2^31, credit past both windows;
                   GOAWAY retry-safe; RST_STREAM; padding; max_bytes ->
                   RST_STREAM(CANCEL); 1xx; trailers; protocol refusals; the
                   structure-only log; one socketpair exchange
  F. HTTP/1.1:     _ch_split_url (defaults 80/443, userinfo, IDNA, trailing
                   dot, bracketed IPv6, the percent-encoded target, every
                   refusal); request heads vs sets 5/6; per transport (TLS with
                   ALPN http/1.1, cleartext): Content-Length, chunked,
                   EOF-delimited, 1xx, keep-alive vs close, every ceiling at
                   and over its bound, obs-fold, trailers; the h1 guard
                   driven white-box with unchecked CR/LF/NUL and framing
                   (zero bytes written); caller injection refused before the
                   socket opens (the peer accepts no connection)
  G. SESSION:      _ch_session_new against threaded loopback peers written
                   here (h2 over TLS, HTTP/1.1 over TLS, cleartext): stream 1
                   then 3 on a same-origin redirect, a new connection
                   cross-origin, POST->303->GET per transport, exactly
                   CH_MAX_REDIRECTS followed and one more refused, ftp and
                   the https->http downgrade refused before the policy,
                   allow_downgrade still policy-checked, gzip plus br/zstd
                   loaded BY PATH and injected (INFO when the library is
                   absent), decode_error, FR-16 caller headers and Referer
                   per hop, 307/308 cross-origin re-send, cookies (the
                   captured slot, IP host-only, Secure, every cap at N and
                   N+1 read off the module), the caller Cookie merge, and no
                   os.environ read
  I. LIMITS:       max_bytes on the wire and decoded (.limit), CH_MAX_BODY_BYTES
                   and CH_DEFAULT_DECODE_CAP lowered and probed at N and N+1,
                   three codings, the cumulative budget across layers, gzip
                   members and a raw-deflate fallback; on_headers (final hop
                   only, its exception unchanged, RST_STREAM(CANCEL), beating
                   the size limit on h2 and h1); _ch_address_refused one row
                   per SSRF form plus the patched-property row;
                   _ch_public_only_policy and _ch_open_socket against
                   recorders; the policy on hop 2, ordered addresses, the
                   connect override, redirect schemes, userinfo, IDNA, the
                   trailing dot and SNI, every path through the policy, a
                   slow-loris peer, and the re-vetted reconnect (GOAWAY /
                   closed keep-alive, no silent POST retry)
  J. FALLBACK:     TLS 1.2 server: flag off, verified by the test CA
                   (impersonated=False, cert_verified=True, one re-issue over
                   the policy's list), default context / host mismatch /
                   non-verifying context refused, limits, on_headers, a
                   redirect with a cookie, HEAD, POST, a silent peer; CR/LF
                   refused before the first hop and white-box; *_PROXY at a
                   closed port ignored; AST rows (default context, no test CA
                   or unverified context, no os.environ / urllib.request,
                   _ch_open_socket only ever called by its global name) with
                   planted controls, and the late-binding behavioural twin
  P. TRANSPORT:    the session default is transport="verified" (and its AST
                   default); a verified GET (not impersonated, cert verified,
                   HTTP/1.1, no ALPN at the peer); the default context and a
                   stalled peer refused with verified: / timeout: verified;
                   chrome, tls12-fallback and cleartext labels; an unknown
                   transport refused; verified-path parity (redirects,
                   Authorization dropped cross-origin, on_headers, max_bytes,
                   gzip, the downgrade, a POST against the cors-post
                   capture, late binding); the weakest-hop chain label; the
                   decoded cap; and the AST rule that every _ch_session_new
                   call in this file says transport= or is declared, with a
                   planted control
  M. HOSTS:        Scripts/search_github.py's GENERATED copy, loaded by path,
                   reaching loopback peers through a port-443 wrapper of the
                   host's _ch_open_socket (restored, `is` identity): the
                   Chrome-path cors GET vs the cors-get capture with grep.app's
                   Accept, the referer from the warm-up's final URL, the canned
                   JSON parsed; create_session()'s policy identity (one
                   session, Chrome only); a redirect into metadata space
                   refused by the policy; the block handling over stub
                   sessions (403, html 200 and html 429 are blocks, final with
                   no second attempt; JSON 429, 5xx, a decode error, another
                   host's 403 and every transport failure are not), no label
                   on "No results found.", rotation every ROTATE_EVERY queries
                   and a failed session dropped; the F32 cap; AST rows (R14,
                   no rand=) with planted controls; the NFR-5 startup delta
                   (INFO). Then Scripts/search_duckduckgo.py's copy the same
                   way: the warm-up navigation on stream 1 and the cors POST on
                   stream 3 of one connection vs the cors-post capture
                   (headers, body, referer), the Bing GET vs the navigate
                   capture and the tf_bing_serp fixture parsed; the policy rows
                   (a 302 GET and a 307 POST into metadata space); a DDG block
                   switching this and every later query to Bing on a fresh
                   session, a Bing 403 blocking the query (429/5xx, transport
                   failures, another host's answer are not blocks), the
                   structural DDG predicate with reflected query / snippet rows
                   whose control is today's substring test (the positive row is
                   the plan's FALLBACK predicate: no live challenge page was
                   ever observed), rotation counting queries not endpoints; the
                   F32 cap and the single parse; AST rows (R14, no rand=). For
                   both hosts, main() exits 1 on a block with one blocked line
                   (another query's results still printed), and under a guard
                   on every network entry point: -h / --help print usage and
                   exit 0, an unknown option (alone or after a query) exits 2,
                   none reaching the network -- with a control that the guard
                   stops a real query
  O. WEBFETCH:     Scripts/mcp-webfetch.py loaded by path with stub bs4 /
                   markdownify, handle_fetch driven over loopback: rows not
                   about the Chrome path run on the DEFAULT verified transport
                   against an ssl h1 peer on the test leaf (the module's
                   _ch_session_new wrapped to inject the test-CA factory);
                   profile="chrome" rows reach an h2 peer and the TLS 1.2-only
                   peer. 200 HTML, 403 x3 (connections, policy per attempt,
                   attempts label, the hint), profile=firefox refused,
                   max_bytes N / N+1 / negative, headers-before-size per
                   transport (closed connection, RST_STREAM(CANCEL)), HEAD,
                   the caller Cookie across hops, redirect re-vetting, the
                   link-local redirect, _check_host_allowed's forms, the
                   allow_private split (offline cache hit, scheme, IDNA host
                   vs SNI), header refusals before the cache and any
                   connection, every transport label (status line and title
                   line apart), cache admission and the S3 chain honesty rows,
                   the opt-in (impersonate alias), the default verifying
                   ('verified: certificate verify failed'), the status reply
                   (no decoder load), the downgrade, and one uv stdio row
  N. NEGATIVE:    modified tags (Wycheproof "invalid") and flipped bytes
                   refused, all-zero X25519, off-curve P-256, wrong lengths,
                   ML-KEM key/ciphertext checks, HKDF bounds, and an AST gate
                   that every AEAD tag goes through hmac.compare_digest -- with a
                   synthetic `!=` the gate must catch
  K. STRUCTURE:    generator block shapes (no module-level loop), no duplicate
                   names, the _ch_/_Ch/CH_/_CH_ prefixes plus five declared
                   exceptions, tab safety, free names, no `assert` -- with
                   planted controls the checker must refuse. Written so Steps
                   6-9 re-run it unchanged as the TLS/h2/h1 layers land
  L. THROUGHPUT:   MiB/s decrypting 1 MiB (64 x 16 KiB records) per AEAD, the
                   ML-KEM and P-256 timings, the CHROME_PROFILE age
  Z. HYGIENE:      no bytecode, no new repo paths, scratch only under mkdtemp

Usage:
  python3 tests/test_mcp_chrome.py            # standalone
  python3 tests/run.py mcp_chrome             # through the fleet runner
  python3 tests/test_mcp_chrome.py --brief    # one line per case

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every case passes. Writes only into a mkdtemp workspace.
"""

import ast
import codecs
import ctypes
import ctypes.util
import datetime
import gzip
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import os
import re
import select
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import types
import urllib.parse
import zlib

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_chrome"

SOURCE = H.repo_path("Scripts", "_mcp_chrome.py")
GENERATOR = H.repo_path("Scripts", "amalgamate.py")
MLKEM_VECTORS = H.repo_path("tests", "files", "mlkem")
CAPTURE_TOOL = H.repo_path("Scripts", "chrome_capture.py")
FIXTURES = H.repo_path("tests", "files", "chrome", "154")
FIXTURE_README = os.path.join(FIXTURES, "README.md")
TLS_CERT = H.repo_path("tests", "files", "tls", "localhost-cert.pem")
TLS_KEY = H.repo_path("tests", "files", "tls", "localhost-key.pem")
TLS_CA = H.repo_path("tests", "files", "tls", "test-ca.pem")

GA = "A. CRYPTO KATs: published vectors, each cited"
GB = "B. CLIENTHELLO: 20 seeds vs the Chrome 154 captures, judged by chrome_capture's parser"
GC = "C. TLS 1.3: MemoryBIO + loopback ssl servers, the RFC 8448 trace, a scripted peer, openssl s_server"
GH = "H. HELLORETRYREQUEST: P-256 completion, CH2 vs set 9, every refusal"
GD = "D. HPACK + HEADER PROFILES: RFC 7541 vectors, every committed HEADERS block, caller-header rules"
GE = "E. HTTP/2: a test-side RFC 9113 responder; first flight vs the captures, every ceiling at N and N+1"
GF = "F. HTTP/1.1: over TLS (ALPN http/1.1) and cleartext, every ceiling, heads vs sets 5/6, injection refused"
GG = "G. SESSION: loopback h2 / h1-over-TLS / cleartext h1 peers; redirects, downgrade, decoders, cookies, cross-origin rules"
GI = "I. LIMITS + CONNECT POLICY: body and decode budgets, on_headers, one row per SSRF form, resolver, opener, per-hop vetting"
GJ = "J. TLS 1.2 FALLBACK + ENVIRONMENT: the test-CA-verified stdlib path, *_PROXY ignored, AST rows, late binding"
GP = "P. TRANSPORT: the verified default vs transport=\"chrome\", the chain label, verified: refusals, parity, the call-site rule"
GM = "M. HOSTS: search_github's and search_duckduckgo's generated copies -- the captured cors GET / POST, their policy, the Chrome-only block handling and exit codes, the AST rules"
GO = "O. WEBFETCH HOST: handle_fetch over loopback -- the verified default, the profile=chrome opt-in, labels, cache admission, the policy split"
GN = "N. NEGATIVE CONTROL: refusals and the tag-compare gate"
GK = "K. STRUCTURE: block shapes, prefixes, tab safety, no assert"
GL = "L. THROUGHPUT + PROFILE AGE: INFO, FAIL only below a tenth of the AES-256-GCM floor"
GZ = "Z. HYGIENE: no bytecode, no new repo paths"

PREFIXES = ("_ch_", "_Ch", "CH_", "_CH_")
EXCEPTIONS = ("CHROME_PROFILE", "_chrome_profile", "ChromeClientError", "ChromeTls12Error", "ChromeBodyTooLarge")

# G3 (feature plan Step 5 exit criterion): AES-256-GCM decrypt >= 0.5 MiB/s,
# i.e. 5 MiB in <= 10 s, a third of the 30 s default timeout. A wall-clock rate
# flaps with host load (0.27-0.48 MiB/s measured under ordinary concurrent load,
# 0.53-0.66 alone), and FAIL is reserved for rules that cannot flap
# (docs/subsystems/tests.md). So the row is three-banded: >= the floor PASS;
# floor/CATASTROPHE_FACTOR <= rate < floor INFO with the measured value and the
# load caveat; below floor/CATASTROPHE_FACTOR FAIL -- a tenfold shortfall no
# load explains, i.e. an algorithmic regression (a lost table, a per-byte
# allocation).
AES256_DECRYPT_FLOOR_MIB_S = 0.5
CATASTROPHE_FACTOR = 10.0

# The throughput rows decrypt this many bytes as records of the TLS maximum
# plaintext size (RFC 8446 section 5.1: 2^14).
RECORD_BYTES = 16384
THROUGHPUT_BYTES = 1024 * 1024

# Where an OpenSSL new enough for ML-KEM usually lives (macOS ships LibreSSL
# as /usr/bin/openssl, which has none); PATH is tried last.
OPENSSL_CANDIDATES = ("/usr/local/bin/openssl", "/opt/homebrew/bin/openssl", "/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/bin/openssl")


def h(text):
    """Hex as the documents print it: spaces, newlines and colons are ignored."""
    return bytes.fromhex(re.sub(r"[\s:]", "", text))


# --- published vectors ----------------------------------------------------------

# RFC 7748 section 5.2, "X25519:", the two scalar/u-coordinate pairs.
X25519_52 = (
    ("rfc7748-5.2-vector-1",
     "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4",
     "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
     "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"),
    ("rfc7748-5.2-vector-2",
     "4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d",
     "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493",
     "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"),
)

# RFC 7748 section 5.2, the iterated test: k = u = 9 initially.
X25519_ITER_START = "0900000000000000000000000000000000000000000000000000000000000000"
X25519_ITER = (
    (1, "422c8e7a6227d7bca1350b3e2bb7279f7897b87bb6854b783c60e80311ae3079"),
    (1000, "684cf59ba83309552800ef566f2f4d3c1c3887c49360e3875f2eb94d99532c51"),
)

# RFC 7748 section 6.1, Curve25519 Diffie-Hellman test vector.
X25519_61_A = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
X25519_61_A_PUB = "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
X25519_61_B = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
X25519_61_B_PUB = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
X25519_61_K = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"

# RFC 5903 section 8.1, 256-Bit Random ECP Group (P-256).
P256_I = "C88F01F5 10D9AC3F 70A292DA A2316DE5 44E9AAB8 AFE84049 C62A9C57 862D1433"
P256_GIX = "DAD0B653 94221CF9 B051E1FE CA5787D0 98DFE637 FC90B9EF 945D0C37 72581180"
P256_GIY = "5271A046 1CDB8252 D61F1C45 6FA3E59A B1F45B33 ACCF5F58 389E0577 B8990BB3"
P256_R = "C6EF9C5D 78AE012A 011164AC B397CE20 88685D8F 06BF9BE0 B283AB46 476BEE53"
P256_GRX = "D12DFB52 89C8D4F8 1208B702 70398C34 2296970A 0BCCB74C 736FC755 4494BF63"
P256_GRY = "56FBF3CA 366CC23E 8157854C 13C58D6A AC23F046 ADA30F83 53E74F33 039872AB"
P256_GIRX = "D6840F6B 42F6EDAF D13116E0 E1256520 2FEF8E9E CE7DCE03 812464D0 4B9442DE"

# C2SP Wycheproof testvectors_v1/aes_gcm_test.json (source google-wycheproof
# 0.9rc5), result "valid", ivSize 96, tagSize 128:
# (tcId, key, iv, aad, msg, ct, tag). keySize 128 then keySize 256.
GCM_VALID = (
    (1, "5b9604fe14eadba931b0ccf34843dab9", "028318abc1824029138141a2", "", "001d0c231287c1182784554ca3a21908", "26073cc1d851beff176384dc9896d5ff", "0a3ea7a5487cb5f7d70fb6c58d038554"),
    (2, "5b9604fe14eadba931b0ccf34843dab9", "921d2507fa8007b7bd067d34", "00112233445566778899aabbccddeeff", "001d0c231287c1182784554ca3a21908", "49d8b9783e911913d87094d1f63cc765", "1e348ba07cca2cf04c618cb4d43a5b92"),
    (3, "aa023d0478dcb2b2312498293d9a9129", "0432bc49ac34412081288127", "aac39231129872a2", "2035af313d1346ab00154fea78322105", "eea945f3d0f98cc0fbab472a0cf24e87", "4bb9b4812519dadf9e1232016d068133"),
    (4, "bedcfb5a011ebc84600fcb296c15af0d", "438a547a94ea88dce46c6c85", "", "", "", "960247ba5cde02e41a313c4c0136edc3"),
    (5, "384ea416ac3c2f51a76e7d8226346d4e", "b30c084727ad1c592ac21d12", "", "35", "54", "7c1e4ae88bb27e5638343cb9fd3f6337"),
    (9, "c15ed227dd2e237ecd087eaaaad19ea4", "965ff6643116ac1443a2dec7", "", "fee62fde973fe025ad6b322dcdf3c63fc7", "0de51fe4f7f2d1f0f917569f5c6d1b009c", "6cd8521422c0177e83ef1b7a845d97db"),
    (11, "28ff3def08179311e2734c6d1c4e2871", "32bcb9b569e3b852d37c766a", "c3", "dfc61a20df8505b53e3cd59f25770d5018add3d6", "f58d453212c2c8a436e9283672f579f119122978", "5901131d0760c8715901d881fdfd3bc0"),
    (13, "38449890234eb8afab0bbf82e2385454", "33e90658416e7c1a7c005f11", "4020855c66ac4595058395f367201c4c", "f762776bf83163b323ca63a6b3adeac1e1357262", "a6f2ef3c7ef74a126dd2d5f6673964e27d5b34b6", "b8bbdc4f5014bc752c8b4e9b87f650a3"),
    (15, "bb571c160132b0c8d5d190d0bc356ddc", "2596c440cf0232950ec66bc4", "", "053be1b6190a717fc74c879e6fd62dc44628495507e50d662271dee795a4ad26e0c4f86cb6b20ac6bd9d682d2d8a05c9dad875a6911b49ea0af4f17c97a5f2", "b1cfad142a462f3656e0921627fd41d4f1fa8e2f8bd94bb51fdcf06f606296f7d2885337bed7a4ca6ddb4a9fc7fdb2476b5f7fa5220e1d6752a5e7c31c916c", "a231b617352ffdb63d32d69d99e7d629"),
    (27, "5a475f9976ed117ab37a4fffab0592eb", "6bce45bea6ad59bd2a08f7b3", "e8bb51b694b6b0763e097bad1152f5c762a878a3e7f7a9d78e809838de78567900281b7e4f0f185493fd85e28db79b595541aba7e158b3936490b632355d74", "dc6ab0e261412cc709422289ea202021d9298060", "35d3ab0534102884ed0db4694a221df1bf94dcdb", "d78d2c197deb70ed52933f4fa0b09856"),
    (39, "00112233445566778899aabbccddeeff", "000000000000000000000000", "", "ebd4a3e10cf6d41c50aeae007563b072", "f62d84d649e56bc8cfedc5d74a51e2f7", "ffffffffffffffffffffffffffffffff"),
    (40, "00112233445566778899aabbccddeeff", "ffffffffffffffffffffffff", "", "d593c4d8224f1b100c35e4f6c4006543", "431f31e6840931fd95f94bf88296ff69", "00000000000000000000000000000000"),
    (91, "92ace3e348cd821092cd921aa3546374299ab46209691bc28b8752d17f123c20", "00112233445566778899aabb", "00000000ffffffff", "00010203040506070809", "e27abdd2d2a53d2f136b", "9a4a2579529301bcfb71c78d4060f52c"),
    (92, "29d3a44f8723dc640239100c365423a312934ac80239212ac3df3421a2098123", "00112233445566778899aabb", "aabbccddeeff", "", "", "2a7d77fa526b8250cb296078926b5020"),
    (94, "cc56b680552eb75008f5484b4cb803fa5063ebd6eab91f6ab6aef4916a766273", "99e23ec48985bccdeeab60f1", "", "2a", "06", "633c1e9703ef744ffffb40edf9d14355"),
    (97, "59d4eafb4de0cfc7d3db99a8f54b15d7b39f0acc8da69763b019c1699f87674a", "2fcb1b38a99e71b84740ad9b", "", "549b365af913f3b081131ccb6b825588", "f58c16690122d75356907fd96b570fca", "28752c20153092818faba2a334640d6e"),
    (98, "3b2458d8176e1621c0cc24c0c0e24c1e80d72f7ee9149a4b166176629616d011", "45aaa3e5d16d2d42dc03445d", "", "3ff1514b1c503915918f0c0c31094a6e1f", "73a6b6f45f6ccc5131e07f2caa1f2e2f56", "2d7379ec1db5952d4e95d30c340b1b1d"),
    (100, "b279f57e19c8f53f2f963f5f2519fdb7c1779be2ca2b3ae8e1128b7d6c627fc4", "98bc2c7438d5cd7665d76f6e", "c0", "fcc515b294408c8645c9183e3f4ecee5127846d1", "eb5500e3825952866d911253f8de860c00831c81", "ecb660e1fb0541ec41e8d68a64141b3a"),
    (104, "6efca98126918ab564d88c6bec02e8998b2be50e3f906ff9adfdd185f373e756", "4abd6cfc83bd06b11efaa2a7", "", "bbec79c086d41e602d090f7e40494d6bf3faa1dc6df0ab8a88ea5d35d426b248c2ad880351e223f6170d37cc9655e10459e59cbd6d1c092ed31d72ccc7af20", "97b4c73a4d8b5b21bc4b50dbb70dfa77b1a7bf0bbe7cf16ecf5bb60ba8070acc5740780435ed145a62a613dd9881b721168fbb3f5af385ee5d4f856cf93cba", "27ac8c4010d8e81b7051ceb06b30fe2d"),
    (128, "00112233445566778899aabbccddeeff102132435465768798a9bacbdcedfe0f", "000000000000000000000000", "", "561008fa07a68f5c61285cd013464eaf", "23293e9b07ca7d1b0cae7cc489a973b3", "ffffffffffffffffffffffffffffffff"),
    (129, "00112233445566778899aabbccddeeff102132435465768798a9bacbdcedfe0f", "ffffffffffffffffffffffff", "", "c6152244cea1978d3e0bc274cf8c0b3b", "7cb6fc7c6abc009efe9551a99f36a421", "00000000000000000000000000000000"),
)

# The same file, result "invalid", flag ModifiedTag: (tcId, key, iv, aad, ct, tag).
GCM_INVALID = (
    (41, "000102030405060708090a0b0c0d0e0f", "505152535455565758595a5b", "", "eb156d081ed6b6b55f4612f021d87b39", "d9847dbc326a06e988c77ad3863e6083"),
    (42, "000102030405060708090a0b0c0d0e0f", "505152535455565758595a5b", "", "eb156d081ed6b6b55f4612f021d87b39", "da847dbc326a06e988c77ad3863e6083"),
    (43, "000102030405060708090a0b0c0d0e0f", "505152535455565758595a5b", "", "eb156d081ed6b6b55f4612f021d87b39", "58847dbc326a06e988c77ad3863e6083"),
    (45, "000102030405060708090a0b0c0d0e0f", "505152535455565758595a5b", "", "eb156d081ed6b6b55f4612f021d87b39", "d8847d3c326a06e988c77ad3863e6083"),
    (130, "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "505152535455565758595a5b", "", "b2061457c0759fc1749f174ee1ccadfa", "9de8fef6d8ab1bf1bf887232eab590dd"),
    (131, "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "505152535455565758595a5b", "", "b2061457c0759fc1749f174ee1ccadfa", "9ee8fef6d8ab1bf1bf887232eab590dd"),
    (132, "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "505152535455565758595a5b", "", "b2061457c0759fc1749f174ee1ccadfa", "1ce8fef6d8ab1bf1bf887232eab590dd"),
)

# RFC 8439 sections 2.4.2 and 2.8.2 print the same "sunscreen" plaintext.
SUNSCREEN = """
4c 61 64 69 65 73 20 61 6e 64 20 47 65 6e 74 6c
65 6d 65 6e 20 6f 66 20 74 68 65 20 63 6c 61 73
73 20 6f 66 20 27 39 39 3a 20 49 66 20 49 20 63
6f 75 6c 64 20 6f 66 66 65 72 20 79 6f 75 20 6f
6e 6c 79 20 6f 6e 65 20 74 69 70 20 66 6f 72 20
74 68 65 20 66 75 74 75 72 65 2c 20 73 75 6e 73
63 72 65 65 6e 20 77 6f 75 6c 64 20 62 65 20 69
74 2e
"""

# RFC 8439 section 2.4.2: key, nonce, initial counter 1, "Ciphertext Sunscreen".
CHACHA_242_KEY = "00:01:02:03:04:05:06:07:08:09:0a:0b:0c:0d:0e:0f:10:11:12:13:14:15:16:17:18:19:1a:1b:1c:1d:1e:1f"
CHACHA_242_NONCE = "00:00:00:00:00:00:00:4a:00:00:00:00"
CHACHA_242_CT = """
6e 2e 35 9a 25 68 f9 80 41 ba 07 28 dd 0d 69 81
e9 7e 7a ec 1d 43 60 c2 0a 27 af cc fd 9f ae 0b
f9 1b 65 c5 52 47 33 ab 8f 59 3d ab cd 62 b3 57
16 39 d6 24 e6 51 52 ab 8f 53 0c 35 9f 08 61 d8
07 ca 0d bf 50 0d 6a 61 56 a3 8e 08 8a 22 b6 5e
52 bc 51 4d 16 cc f8 06 81 8c e9 1a b7 79 37 36
5a f9 0b bf 74 a3 5b e6 b4 0b 8e ed f2 78 5e 42
87 4d
"""

# RFC 8439 section 2.6.2, Poly1305 key generation: key, nonce, output bytes.
POLYKEY_262_KEY = """
80 81 82 83 84 85 86 87 88 89 8a 8b 8c 8d 8e 8f
90 91 92 93 94 95 96 97 98 99 9a 9b 9c 9d 9e 9f
"""
POLYKEY_262_NONCE = "00 00 00 00 00 01 02 03 04 05 06 07"
POLYKEY_262_OUT = """
8a d5 a0 8b 90 5f 81 cc 81 50 40 27 4a b2 94 71
a8 33 b6 37 e3 fd 0d a5 08 db b8 e2 fd d1 a6 46
"""

# RFC 8439 section 2.8.2: AAD, key, IV, 32-bit fixed-common part (the nonce is
# fixed-common || IV), Poly1305 key, ciphertext, tag.
AEAD_282_AAD = "50 51 52 53 c0 c1 c2 c3 c4 c5 c6 c7"
AEAD_282_KEY = """
80 81 82 83 84 85 86 87 88 89 8a 8b 8c 8d 8e 8f
90 91 92 93 94 95 96 97 98 99 9a 9b 9c 9d 9e 9f
"""
AEAD_282_IV = "40 41 42 43 44 45 46 47"
AEAD_282_FIXED = "07 00 00 00"
AEAD_282_POLYKEY = """
7b ac 2b 25 2d b4 47 af 09 b6 7a 55 a4 e9 55 84
0a e1 d6 73 10 75 d9 eb 2a 93 75 78 3e d5 53 ff
"""
AEAD_282_CT = """
d3 1a 8d 34 64 8e 60 db 7b 86 af bc 53 ef 7e c2
a4 ad ed 51 29 6e 08 fe a9 e2 b5 a7 36 ee 62 d6
3d be a4 5e 8c a9 67 12 82 fa fb 69 da 92 72 8b
1a 71 de 0a 9e 06 0b 29 05 d6 a5 b6 7e cd 3b 36
92 dd bd 7f 2d 77 8b 8c 98 03 ae e3 28 09 1b 58
fa b3 24 e4 fa d6 75 94 55 85 80 8b 48 31 d7 bc
3f f4 de f0 8e 4b 7a 9d e5 76 d2 65 86 ce c6 4b
61 16
"""
AEAD_282_TAG = "1a:e1:0b:59:4f:09:e2:6a:7e:90:2e:cb:d0:60:06:91"

# RFC 8439 appendix A.5, ChaCha20-Poly1305 AEAD decryption.
AEAD_A5_KEY = """
1c 92 40 a5 eb 55 d3 8a f3 33 88 86 04 f6 b5 f0
47 39 17 c1 40 2b 80 09 9d ca 5c bc 20 70 75 c0
"""
AEAD_A5_CT = """
64 a0 86 15 75 86 1a f4 60 f0 62 c7 9b e6 43 bd
5e 80 5c fd 34 5c f3 89 f1 08 67 0a c7 6c 8c b2
4c 6c fc 18 75 5d 43 ee a0 9e e9 4e 38 2d 26 b0
bd b7 b7 3c 32 1b 01 00 d4 f0 3b 7f 35 58 94 cf
33 2f 83 0e 71 0b 97 ce 98 c8 a8 4a bd 0b 94 81
14 ad 17 6e 00 8d 33 bd 60 f9 82 b1 ff 37 c8 55
97 97 a0 6e f4 f0 ef 61 c1 86 32 4e 2b 35 06 38
36 06 90 7b 6a 7c 02 b0 f9 f6 15 7b 53 c8 67 e4
b9 16 6c 76 7b 80 4d 46 a5 9b 52 16 cd e7 a4 e9
90 40 c5 a4 04 33 22 5e e2 82 a1 b0 a0 6c 52 3e
af 45 34 d7 f8 3f a1 15 5b 00 47 71 8c bc 54 6a
0d 07 2b 04 b3 56 4e ea 1b 42 22 73 f5 48 27 1a
0b b2 31 60 53 fa 76 99 19 55 eb d6 31 59 43 4e
ce bb 4e 46 6d ae 5a 10 73 a6 72 76 27 09 7a 10
49 e6 17 d9 1d 36 10 94 fa 68 f0 ff 77 98 71 30
30 5b ea ba 2e da 04 df 99 7b 71 4d 6c 6f 2c 29
a6 ad 5c b4 02 2b 02 70 9b
"""
AEAD_A5_NONCE = "00 00 00 00 01 02 03 04 05 06 07 08"
AEAD_A5_AAD = "f3 33 88 86 00 00 00 00 00 00 4e 91"
AEAD_A5_TAG = "ee ad 9d 67 89 0c bb 22 39 23 36 fe a1 85 1f 38"
AEAD_A5_PT = """
49 6e 74 65 72 6e 65 74 2d 44 72 61 66 74 73 20
61 72 65 20 64 72 61 66 74 20 64 6f 63 75 6d 65
6e 74 73 20 76 61 6c 69 64 20 66 6f 72 20 61 20
6d 61 78 69 6d 75 6d 20 6f 66 20 73 69 78 20 6d
6f 6e 74 68 73 20 61 6e 64 20 6d 61 79 20 62 65
20 75 70 64 61 74 65 64 2c 20 72 65 70 6c 61 63
65 64 2c 20 6f 72 20 6f 62 73 6f 6c 65 74 65 64
20 62 79 20 6f 74 68 65 72 20 64 6f 63 75 6d 65
6e 74 73 20 61 74 20 61 6e 79 20 74 69 6d 65 2e
20 49 74 20 69 73 20 69 6e 61 70 70 72 6f 70 72
69 61 74 65 20 74 6f 20 75 73 65 20 49 6e 74 65
72 6e 65 74 2d 44 72 61 66 74 73 20 61 73 20 72
65 66 65 72 65 6e 63 65 20 6d 61 74 65 72 69 61
6c 20 6f 72 20 74 6f 20 63 69 74 65 20 74 68 65
6d 20 6f 74 68 65 72 20 74 68 61 6e 20 61 73 20
2f e2 80 9c 77 6f 72 6b 20 69 6e 20 70 72 6f 67
72 65 73 73 2e 2f e2 80 9d
"""

# RFC 5869 appendix A.1-A.3 (Hash = SHA-256): (cid, IKM, salt, info, L, PRK, OKM).
HKDF_CASES = (
    ("rfc5869-A.1", "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b", "000102030405060708090a0b0c", "f0f1f2f3f4f5f6f7f8f9", 42,
     "077709362c2e32df0ddc3f0dc47bba63 90b6c73bb50f9c3122ec844ad7c2b3e5",
     "3cb25f25faacd57a90434f64d0362f2a 2d2d0a90cf1a5a4c5db02d56ecc4c5bf 34007208d5b887185865"),
    ("rfc5869-A.2",
     "000102030405060708090a0b0c0d0e0f 101112131415161718191a1b1c1d1e1f 202122232425262728292a2b2c2d2e2f 303132333435363738393a3b3c3d3e3f 404142434445464748494a4b4c4d4e4f",
     "606162636465666768696a6b6c6d6e6f 707172737475767778797a7b7c7d7e7f 808182838485868788898a8b8c8d8e8f 909192939495969798999a9b9c9d9e9f a0a1a2a3a4a5a6a7a8a9aaabacadaeaf",
     "b0b1b2b3b4b5b6b7b8b9babbbcbdbebf c0c1c2c3c4c5c6c7c8c9cacbcccdcecf d0d1d2d3d4d5d6d7d8d9dadbdcdddedf e0e1e2e3e4e5e6e7e8e9eaebecedeeef f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff",
     82,
     "06a6b88c5853361a06104c9ceb35b45c ef760014904671014a193f40c15fc244",
     "b11e398dc80327a1c8e7f78c596a4934 4f012eda2d4efad8a050cc4c19afa97c 59045a99cac7827271cb41c65e590e09 da3275600c2f09b8367793a9aca3db71 cc30c58179ec3e87c14c01d5c1f3434f 1d87"),
    ("rfc5869-A.3", "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b", "", "", 42,
     "19ef24a32c717b167f33a91d6f648bdf 96596776afdb6377ac434c1c293ccb04",
     "8da4e775a563c18f715f802a063c5a31 b8a11f5c5ee1879ec3454e5f3c738d2d 9d201395faa4b61a96c8"),
)

# RFC 8448 section 3, "Simple 1-RTT Handshake" (TLS_AES_128_GCM_SHA256, x25519).
T_CLIENT_PRIV = "49 af 42 ba 7f 79 94 85 2d 71 3e f2 78 4b cb ca a7 91 1d e2 6a dc 56 42 cb 63 45 40 e7 ea 50 05"
T_CLIENT_PUB = "99 38 1d e5 60 e4 bd 43 d2 3d 8e 43 5a 7d ba fe b3 c0 6e 51 c1 3c ae 4d 54 13 69 1e 52 9a af 2c"
T_SERVER_PRIV = "b1 58 0e ea df 6d d5 89 b8 ef 4f 2d 56 52 57 8c c8 10 e9 98 01 91 ec 8d 05 83 08 ce a2 16 a2 1e"
T_SERVER_PUB = "c9 82 88 76 11 20 95 fe 66 76 2b db f7 c6 72 e1 56 d6 cc 25 3b 83 3d f1 dd 69 b1 b0 4e 75 1f 0f"
T_ZERO32 = "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
T_EARLY = "33 ad 0a 1c 60 7e c0 3b 09 e6 cd 98 93 68 0c e2 10 ad f3 00 aa 1f 26 60 e1 b2 2e 10 f1 70 f9 2a"
T_EMPTY_HASH = "e3 b0 c4 42 98 fc 1c 14 9a fb f4 c8 99 6f b9 24 27 ae 41 e4 64 9b 93 4c a4 95 99 1b 78 52 b8 55"
T_DERIVED_EARLY = "6f 26 15 a1 08 c7 02 c5 67 8f 54 fc 9d ba b6 97 16 c0 76 18 9c 48 25 0c eb ea c3 57 6c 36 11 ba"
T_ECDHE = "8b d4 05 4f b5 5b 9d 63 fd fb ac f9 f0 4b 9f 0d 35 e6 d6 3f 53 75 63 ef d4 62 72 90 0f 89 49 2d"
T_HS = "1d c8 26 e9 36 06 aa 6f dc 0a ad c1 2f 74 1b 01 04 6a a6 b9 9f 69 1e d2 21 a9 f0 ca 04 3f be ac"
T_HS_HASH = "86 0c 06 ed c0 78 58 ee 8e 78 f0 e7 42 8c 58 ed d6 b4 3f 2c a3 e6 e9 5f 02 ed 06 3c f0 e1 ca d8"
T_C_HS = "b3 ed db 12 6e 06 7f 35 a7 80 b3 ab f4 5e 2d 8f 3b 1a 95 07 38 f5 2e 96 00 74 6a 0e 27 a5 5a 21"
T_S_HS = "b6 7b 7d 69 0c c1 6c 4e 75 e5 42 13 cb 2d 37 b4 e9 c9 12 bc de d9 10 5d 42 be fd 59 d3 91 ad 38"
T_DERIVED_HS = "43 de 77 e0 c7 77 13 85 9a 94 4d b9 db 25 90 b5 31 90 a6 5b 3e e2 e4 f1 2d d7 a0 bb 7c e2 54 b4"
T_MASTER = "18 df 06 84 3d 13 a0 8b f2 a4 49 84 4c 5f 8a 47 80 01 bc 4d 4c 62 79 84 d5 a4 1d a8 d0 40 29 19"
T_AP_HASH = "96 08 10 2a 0f 1c cc 6d b6 25 0b 7b 7e 41 7b 1a 00 0e aa da 3d aa e4 77 7a 76 86 c9 ff 83 df 13"
T_C_AP = "9e 40 64 6c e7 9a 7f 9d c0 5a f8 88 9b ce 65 52 87 5a fa 0b 06 df 00 87 f7 92 eb b7 c1 75 04 a5"
T_S_AP = "a1 1a f9 f0 55 31 f8 56 ad 47 11 6b 45 a9 50 32 82 04 b4 f4 4b fb 6b 3a 4b 4f 1f 3f cb 63 16 43"
T_EXP = "fe 22 f8 81 17 6e da 18 eb 8f 44 52 9e 67 92 c5 0c 9a 3f 89 45 2f 68 d8 ae 31 1b 43 09 d3 cf 50"
T_S_HS_KEY = "3f ce 51 60 09 c2 17 27 d0 f2 e4 e8 6e e4 03 bc"
T_S_HS_IV = "5d 31 3e b2 67 12 76 ee 13 00 0b 30"
T_S_AP_KEY = "9f 02 28 3b 6c 9c 07 ef c2 6b b9 f2 ac 92 e3 56"
T_S_AP_IV = "cf 78 2b 88 dd 83 54 9a ad f1 e9 84"
T_C_HS_KEY = "db fa a6 93 d1 76 2c 5b 66 6a f5 d9 50 25 8d 01"
T_C_HS_IV = "5b d3 c7 1b 83 6e 0b 76 bb 73 26 5f"
T_S_FINISHED_KEY = "00 8d 3b 66 f8 16 ea 55 9f 96 b5 37 e8 85 c3 1f c0 68 bf 49 2c 65 2f 01 f2 88 a1 d8 cd c1 9f c8"

# RFC 8448 section 3, the wire bytes of the simple 1-RTT trace, copied block by
# block (the octet counts are the RFC's own headings).
# {client} construct a ClientHello handshake message: ClientHello (196 octets).
R8448_CH = (
    "01 00 00 c0 03 03 cb 34 ec b1 e7 81 63 ba 1c 38 c6 da cb 19 6a 6d ff a2"
    "1a 8d 99 12 ec 18 a2 ef 62 83 02 4d ec e7 00 00 06 13 01 13 03 13 02 01"
    "00 00 91 00 00 00 0b 00 09 00 00 06 73 65 72 76 65 72 ff 01 00 01 00 00"
    "0a 00 14 00 12 00 1d 00 17 00 18 00 19 01 00 01 01 01 02 01 03 01 04 00"
    "23 00 00 00 33 00 26 00 24 00 1d 00 20 99 38 1d e5 60 e4 bd 43 d2 3d 8e"
    "43 5a 7d ba fe b3 c0 6e 51 c1 3c ae 4d 54 13 69 1e 52 9a af 2c 00 2b 00"
    "03 02 03 04 00 0d 00 20 00 1e 04 03 05 03 06 03 02 03 08 04 08 05 08 06"
    "04 01 05 01 06 01 02 01 04 02 05 02 06 02 02 02 00 2d 00 02 01 01 00 1c"
    "00 02 40 01"
)
# {server} send handshake record: complete record (95 octets) -- the ServerHello.
R8448_SH_REC = (
    "16 03 03 00 5a 02 00 00 56 03 03 a6 af 06 a4 12 18 60 dc 5e 6e 60 24 9c"
    "d3 4c 95 93 0c 8a c5 cb 14 34 da c1 55 77 2e d3 e2 69 28 00 13 01 00 00"
    "2e 00 33 00 24 00 1d 00 20 c9 82 88 76 11 20 95 fe 66 76 2b db f7 c6 72"
    "e1 56 d6 cc 25 3b 83 3d f1 dd 69 b1 b0 4e 75 1f 0f 00 2b 00 02 03 04"
)
# {server} send handshake record: complete record (679 octets) -- EE, Certificate, CertificateVerify, Finished.
R8448_FLIGHT_REC = (
    "17 03 03 02 a2 d1 ff 33 4a 56 f5 bf f6 59 4a 07 cc 87 b5 80 23 3f 50 0f"
    "45 e4 89 e7 f3 3a f3 5e df 78 69 fc f4 0a a4 0a a2 b8 ea 73 f8 48 a7 ca"
    "07 61 2e f9 f9 45 cb 96 0b 40 68 90 51 23 ea 78 b1 11 b4 29 ba 91 91 cd"
    "05 d2 a3 89 28 0f 52 61 34 aa dc 7f c7 8c 4b 72 9d f8 28 b5 ec f7 b1 3b"
    "d9 ae fb 0e 57 f2 71 58 5b 8e a9 bb 35 5c 7c 79 02 07 16 cf b9 b1 18 3e"
    "f3 ab 20 e3 7d 57 a6 b9 d7 47 76 09 ae e6 e1 22 a4 cf 51 42 73 25 25 0c"
    "7d 0e 50 92 89 44 4c 9b 3a 64 8f 1d 71 03 5d 2e d6 5b 0e 3c dd 0c ba e8"
    "bf 2d 0b 22 78 12 cb b3 60 98 72 55 cc 74 41 10 c4 53 ba a4 fc d6 10 92"
    "8d 80 98 10 e4 b7 ed 1a 8f d9 91 f0 6a a6 24 82 04 79 7e 36 a6 a7 3b 70"
    "a2 55 9c 09 ea d6 86 94 5b a2 46 ab 66 e5 ed d8 04 4b 4c 6d e3 fc f2 a8"
    "94 41 ac 66 27 2f d8 fb 33 0e f8 19 05 79 b3 68 45 96 c9 60 bd 59 6e ea"
    "52 0a 56 a8 d6 50 f5 63 aa d2 74 09 96 0d ca 63 d3 e6 88 61 1e a5 e2 2f"
    "44 15 cf 95 38 d5 1a 20 0c 27 03 42 72 96 8a 26 4e d6 54 0c 84 83 8d 89"
    "f7 2c 24 46 1a ad 6d 26 f5 9e ca ba 9a cb bb 31 7b 66 d9 02 f4 f2 92 a3"
    "6a c1 b6 39 c6 37 ce 34 31 17 b6 59 62 22 45 31 7b 49 ee da 0c 62 58 f1"
    "00 d7 d9 61 ff b1 38 64 7e 92 ea 33 0f ae ea 6d fa 31 c7 a8 4d c3 bd 7e"
    "1b 7a 6c 71 78 af 36 87 90 18 e3 f2 52 10 7f 24 3d 24 3d c7 33 9d 56 84"
    "c8 b0 37 8b f3 02 44 da 8c 87 c8 43 f5 e5 6e b4 c5 e8 28 0a 2b 48 05 2c"
    "f9 3b 16 49 9a 66 db 7c ca 71 e4 59 94 26 f7 d4 61 e6 6f 99 88 2b d8 9f"
    "c5 08 00 be cc a6 2d 6c 74 11 6d bd 29 72 fd a1 fa 80 f8 5d f8 81 ed be"
    "5a 37 66 89 36 b3 35 58 3b 59 91 86 dc 5c 69 18 a3 96 fa 48 a1 81 d6 b6"
    "fa 4f 9d 62 d5 13 af bb 99 2f 2b 99 2f 67 f8 af e6 7f 76 91 3f a3 88 cb"
    "56 30 c8 ca 01 e0 c6 5d 11 c6 6a 1e 2a c4 c8 59 77 b7 c7 a6 99 9b bf 10"
    "dc 35 ae 69 f5 51 56 14 63 6c 0b 9b 68 c1 9e d2 e3 1c 0b 3b 66 76 30 38"
    "eb ba 42 f3 b3 8e dc 03 99 f3 a9 f2 3f aa 63 97 8c 31 7f c9 fa 66 a7 3f"
    "60 f0 50 4d e9 3b 5b 84 5e 27 55 92 c1 23 35 ee 34 0b bc 4f dd d5 02 78"
    "40 16 e4 b3 be 7e f0 4d da 49 f4 b4 40 a3 0c b5 d2 af 93 98 28 fd 4a e3"
    "79 4e 44 f9 4d f5 a6 31 ed e4 2c 17 19 bf da bf 02 53 fe 51 75 be 89 8e"
    "75 0e dc 53 37 0d 2b"
)
# {client} send handshake record: complete record (58 octets) -- the client Finished.
R8448_CFIN_REC = (
    "17 03 03 00 35 75 ec 4d c2 38 cc e6 0b 29 80 44 a7 1e 21 9c 56 cc 77 b0"
    "51 7f e9 b9 3c 7a 4b fc 44 d8 7f 38 f8 03 38 ac 98 fc 46 de b3 84 bd 1c"
    "ae ac ab 68 67 d7 26 c4 05 46"
)
# {server} send handshake record: complete record (227 octets) -- the NewSessionTicket.
R8448_NST_REC = (
    "17 03 03 00 de 3a 6b 8f 90 41 4a 97 d6 95 9c 34 87 68 0d e5 13 4a 2b 24"
    "0e 6c ff ac 11 6e 95 d4 1d 6a f8 f6 b5 80 dc f3 d1 1d 63 c7 58 db 28 9a"
    "01 59 40 25 2f 55 71 3e 06 1d c1 3e 07 88 91 a3 8e fb cf 57 53 ad 8e f1"
    "70 ad 3c 73 53 d1 6d 9d a7 73 b9 ca 7f 2b 9f a1 b6 c0 d4 a3 d0 3f 75 e0"
    "9c 30 ba 1e 62 97 2a c4 6f 75 f7 b9 81 be 63 43 9b 29 99 ce 13 06 46 15"
    "13 98 91 d5 e4 c5 b4 06 f1 6e 3f c1 81 a7 7c a4 75 84 00 25 db 2f 0a 77"
    "f8 1b 5a b0 5b 94 c0 13 46 75 5f 69 23 2c 86 51 9d 86 cb ee ac 87 aa c3"
    "47 d1 43 f9 60 5d 64 f6 50 db 4d 02 3e 70 e9 52 ca 49 fe 51 37 12 1c 74"
    "bc 26 97 68 7e 24 87 46 d6 df 35 30 05 f3 bc e1 86 96 12 9c 81 53 55 6b"
    "3b 6c 67 79 b3 7b f1 59 85 68 4f"
)
# {client} send application_data record: complete record (72 octets); its payload
# (50 octets) is 00 01 02 ... 31, and the server's record carries the same 50.
R8448_C_APP_REC = (
    "17 03 03 00 43 a2 3f 70 54 b6 2c 94 d0 af fa fe 82 28 ba 55 cb ef ac ea"
    "42 f9 14 aa 66 bc ab 3f 2b 98 19 a8 a5 b4 6b 39 5b d5 4a 9a 20 44 1e 2b"
    "62 97 4e 1f 5a 62 92 a2 97 70 14 bd 1e 3d ea e6 3a ee bb 21 69 49 15 e4"
)
# {server} send application_data record: complete record (72 octets).
R8448_S_APP_REC = (
    "17 03 03 00 43 2e 93 7e 11 ef 4a c7 40 e5 38 ad 36 00 5f c4 a4 69 32 fc"
    "32 25 d0 5f 82 aa 1b 36 e3 0e fa f9 7d 90 e6 df fc 60 2d cb 50 1a 59 a8"
    "fc c4 9c 4b f2 e5 f0 a2 1c 00 47 c2 ab f3 32 54 0d d0 32 e1 67 c2 95 5d"
)
# {client} send alert record: complete record (24 octets) -- close_notify.
R8448_C_ALERT_REC = "17 03 03 00 13 c9 87 27 60 65 56 66 b7 4d 7f f1 15 3e fd 6d b6 d0 b0 e3"
# {server} send alert record: complete record (24 octets) -- close_notify.
R8448_S_ALERT_REC = "17 03 03 00 13 b5 8f d6 71 66 eb f5 99 d2 47 20 cf be 7e fa 7a 88 64 a9"
R8448_APP_PAYLOAD = bytes(range(50))

# RFC 8446 section 4.1.3, the HelloRetryRequest Random: SHA-256("HelloRetryRequest").
HRR_RANDOM = "CF 21 AD 74 E5 9A 61 11 BE 1D 8C 02 1E 65 B8 91 C2 A2 11 16 7A BB 8C 5E 07 9E 09 E2 C8 A8 33 9C"

# RFC 8446 appendix B.4: the TLS 1.3 suites -> (hash, AEAD key length, the
# module's AEAD class, the OpenSSL / IANA name).
TLS13_SUITES = {
    0x1301: (hashlib.sha256, 16, "_ChAesGcm", "TLS_AES_128_GCM_SHA256"),
    0x1302: (hashlib.sha384, 32, "_ChAesGcm", "TLS_AES_256_GCM_SHA384"),
    0x1303: (hashlib.sha256, 32, "_ChChaCha20Poly1305", "TLS_CHACHA20_POLY1305_SHA256"),
}

# The client EncryptedExtensions Chrome sends when the server negotiated ALPS
# for h2: one extension 17613 with an EMPTY value. Chromium main 43502f9b,
# net/http/http_network_session.cc ("Enable ALPS for HTTP/2 with empty data");
# BoringSSL main 98df6817, ssl/tls13_client.cc copies the value verbatim.
CLIENT_ALPS_EE = "08 000006 0004 44cd 0000"

# What the scripted peer puts in its own EE ALPS: an h2 SETTINGS frame (RFC
# 9113 section 6.5, HEADER_TABLE_SIZE = 4096), as a Google front end sends one.
SERVER_ALPS = "000006 04 00 00000000 0001 00001000"

# The chrome_capture origin of sets 1-5, 8 and 9 (tests/files/chrome/154/README.md).
CAPTURE_HOST = "capture.localhost"

# Group B: the h2/h1 TLS sets and the ALPN each one's server negotiated
# (README "Layout"; set 6 is cleartext and set 9 is group H's).
CH_SETS = (("navigate", CAPTURE_HOST, "h2"), ("navigate-reload", CAPTURE_HOST, "h2"), ("cors-post", CAPTURE_HOST, "h2"),
           ("cors-get", CAPTURE_HOST, "h2"), ("cors-head", CAPTURE_HOST, "h2"), ("h1-tls", CAPTURE_HOST, "http/1.1"),
           ("ip-literal", "127.0.0.1", "h2"), ("cookie", CAPTURE_HOST, "h2"))
CH_SEEDS = 20


# --- helpers ------------------------------------------------------------------

def problem_if(condition, message):
    return [message] if condition else []


def skip(suite, group, cid, reason):
    """Record a SKIP as an INFO case: informative, never a failure."""
    return suite.record(group, cid, (), status=H.INFO,
                        detail=["SKIPPED: %s" % reason],
                        brief="INFO | %s (skipped: %s)" % (cid, reason))


def outcome(fn):
    """(kind, value): ("value", v) or ("exc", exception). Never raises."""
    try:
        return "value", fn()
    except Exception as exc:  # the rows below judge the type
        return "exc", exc


def expect_value(result, want):
    kind, value = result
    if kind == "exc":
        return ["raised %s(%r)" % (type(value).__name__, str(value)[:160])]
    if value != want:
        return ["got %s, wanted %s" % (short(value), short(want))]
    return []


def expect_refusal(result, exc_type, message):
    """Problems unless *result* is exactly *exc_type* (not a subclass) with *message*."""
    kind, value = result
    if kind == "value":
        return ["returned %s instead of raising %s" % (short(value), exc_type.__name__)]
    if type(value) is not exc_type:
        return ["raised %s(%r), wanted %s" % (type(value).__name__, str(value)[:160], exc_type.__name__)]
    if str(value) != message:
        return ["message %r, wanted %r" % (str(value), message)]
    return []


def short(value):
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).hex()
        return text if len(text) <= 72 else "%s...(%d bytes)" % (text[:64], len(value))
    return repr(value)[:120]


def kat(suite, cid, fn, want, source):
    result = outcome(fn)
    suite.record(GA, cid, expect_value(result, want), detail=[source])


def find_openssl():
    """(path, (major, minor)) of the newest OpenSSL (not LibreSSL) found, or (None, reason)."""
    tried = []
    candidates = list(OPENSSL_CANDIDATES)
    on_path = shutil.which("openssl")
    if on_path and on_path not in candidates:
        candidates.append(on_path)
    best = None
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            proc = subprocess.run([path, "version"], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            tried.append("%s (%s)" % (path, type(exc).__name__))
            continue
        text = proc.stdout.decode("ascii", "replace").strip()
        tried.append("%s (%s)" % (path, text[:40]))
        match = re.match(r"OpenSSL (\d+)\.(\d+)", text)
        if match:
            version = (int(match.group(1)), int(match.group(2)))
            if best is None or version > best[1]:
                best = (path, version)
    if best is None:
        return None, "no OpenSSL binary found; tried %s" % (", ".join(tried) or ", ".join(candidates))
    return best, "; ".join(tried)


def openssl_run(path, args, data=None, timeout=60):
    """Run openssl once; stdin is always explicit. Returns (rc, stdout bytes, stderr text)."""
    if data is None:
        proc = subprocess.run([path] + list(args), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    else:
        proc = subprocess.run([path] + list(args), input=data, capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace").strip()[:200]


def mlkem_spki(ek):
    """SubjectPublicKeyInfo DER for an ML-KEM-768 key (RFC 5280 4.1, OID 2.16.840.1.101.3.4.4.2)."""
    oid = bytes([0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x04, 0x02])
    algid = bytes([0x30, len(oid)]) + oid
    bits = b"\x00" + ek
    bitstr = b"\x03\x82" + len(bits).to_bytes(2, "big") + bits
    body = algid + bitstr
    return b"\x30\x82" + len(body).to_bytes(2, "big") + body


# --- A. KATs -----------------------------------------------------------------------

def group_kats(suite, mod, openssl, ws):
    x = mod._ch_x25519
    for cid, k, u, out in X25519_52:
        kat(suite, cid, lambda: x(h(k), h(u)), h(out), "RFC 7748 section 5.2, X25519 test vector")
    k = u = h(X25519_ITER_START)
    done = 0
    for count, want in X25519_ITER:
        while done < count:
            k, u = x(k, u), k
            done += 1
        suite.record(GA, "rfc7748-5.2-iterated-%d" % count, expect_value(("value", k), h(want)),
                     detail=["RFC 7748 section 5.2, X25519 after %d iteration(s); the 1,000,000 row is not run (minutes in pure Python)" % count])

    a, b = h(X25519_61_A), h(X25519_61_B)
    kat(suite, "rfc7748-6.1-alice-public", lambda: mod._ch_x25519_keypair(lambda n: a[:n]), (a, h(X25519_61_A_PUB)), "RFC 7748 section 6.1, Alice's public key X25519(a, 9) via _ch_x25519_keypair(rand)")
    kat(suite, "rfc7748-6.1-bob-public", lambda: mod._ch_x25519_keypair(lambda n: b[:n]), (b, h(X25519_61_B_PUB)), "RFC 7748 section 6.1, Bob's public key X25519(b, 9) via _ch_x25519_keypair(rand)")
    kat(suite, "rfc7748-6.1-shared-both-ways", lambda: (x(a, h(X25519_61_B_PUB)), x(b, h(X25519_61_A_PUB))), (h(X25519_61_K), h(X25519_61_K)), "RFC 7748 section 6.1, their shared secret K from both sides")

    i_bytes, r_bytes = h(P256_I), h(P256_R)
    gi = b"\x04" + h(P256_GIX) + h(P256_GIY)
    gr = b"\x04" + h(P256_GRX) + h(P256_GRY)
    kat(suite, "rfc5903-8.1-initiator-public", lambda: mod._ch_p256_keypair(lambda n: i_bytes[:n]), (int.from_bytes(i_bytes, "big"), gi), "RFC 5903 section 8.1, g^i = (gix, giy) via _ch_p256_keypair(rand)")
    kat(suite, "rfc5903-8.1-responder-public", lambda: mod._ch_p256_keypair(lambda n: r_bytes[:n]), (int.from_bytes(r_bytes, "big"), gr), "RFC 5903 section 8.1, g^r = (grx, gry) via _ch_p256_keypair(rand)")
    kat(suite, "rfc5903-8.1-shared-both-ways", lambda: (mod._ch_p256_shared(int.from_bytes(i_bytes, "big"), gr), mod._ch_p256_shared(int.from_bytes(r_bytes, "big"), gi)), (h(P256_GIRX), h(P256_GIRX)), "RFC 5903 section 8.1, the shared secret girx from both sides")

    for tc, key, iv, aad, msg, ct, tag in GCM_VALID:
        aead = outcome(lambda: mod._ChAesGcm(h(key)))
        if aead[0] == "exc":
            suite.record(GA, "wycheproof-aes-gcm-tc%d" % tc, expect_value(aead, None))
            continue
        g = aead[1]
        problems = expect_value(outcome(lambda: g.seal(h(iv), h(aad), h(msg))), h(ct) + h(tag))
        problems += expect_value(outcome(lambda: g.open(h(iv), h(aad), h(ct) + h(tag))), h(msg))
        suite.record(GA, "wycheproof-aes-gcm-tc%d" % tc, problems,
                     detail=["Wycheproof aes_gcm_test.json tcId %d (AES-%d, %d-byte msg, %d-byte aad): seal and open" % (tc, len(h(key)) * 8, len(h(msg)), len(h(aad)))])

    ecb_row(suite, mod, openssl)

    cc = mod._ChChaCha20Poly1305
    key242 = h(CHACHA_242_KEY)
    words242 = outcome(lambda: cc(key242)._nonce_words(h(CHACHA_242_NONCE)))
    kat(suite, "rfc8439-2.4.2-chacha20-cipher", lambda: cc(key242)._xor(*(words242[1] + (h(SUNSCREEN),))), h(CHACHA_242_CT), "RFC 8439 section 2.4.2, ChaCha20 from counter 1: Ciphertext Sunscreen")
    key262 = h(POLYKEY_262_KEY)
    kat(suite, "rfc8439-2.6.2-poly1305-key", lambda: cc(key262)._block(0, *cc(key262)._nonce_words(h(POLYKEY_262_NONCE)))[:32], h(POLYKEY_262_OUT), "RFC 8439 section 2.6.2, Poly1305 key generation (block counter 0)")
    nonce282 = h(AEAD_282_FIXED) + h(AEAD_282_IV)
    key282 = h(AEAD_282_KEY)
    kat(suite, "rfc8439-2.8.2-poly1305-key", lambda: cc(key282)._block(0, *cc(key282)._nonce_words(nonce282))[:32], h(AEAD_282_POLYKEY), "RFC 8439 section 2.8.2, the Poly1305 key (sender id 7)")
    kat(suite, "rfc8439-2.8.2-aead-seal", lambda: cc(key282).seal(nonce282, h(AEAD_282_AAD), h(SUNSCREEN)), h(AEAD_282_CT) + h(AEAD_282_TAG), "RFC 8439 section 2.8.2, ciphertext || tag")
    kat(suite, "rfc8439-2.8.2-aead-open", lambda: cc(key282).open(nonce282, h(AEAD_282_AAD), h(AEAD_282_CT) + h(AEAD_282_TAG)), h(SUNSCREEN), "RFC 8439 section 2.8.2, decrypted back to the sunscreen plaintext")
    kat(suite, "rfc8439-A.5-aead-open", lambda: cc(h(AEAD_A5_KEY)).open(h(AEAD_A5_NONCE), h(AEAD_A5_AAD), h(AEAD_A5_CT) + h(AEAD_A5_TAG)), h(AEAD_A5_PT), "RFC 8439 appendix A.5, ChaCha20-Poly1305 AEAD decryption")

    sha = hashlib.sha256
    for cid, ikm, salt, info, length, prk, okm in HKDF_CASES:
        got_prk = outcome(lambda: mod._ch_hkdf_extract(sha, h(salt), h(ikm)))
        problems = expect_value(got_prk, h(prk))
        problems += expect_value(outcome(lambda: mod._ch_hkdf_expand(sha, h(prk), h(info), length)), h(okm))
        suite.record(GA, cid, problems, detail=["RFC 5869 appendix %s, HKDF-SHA256: PRK and OKM (L=%d)" % (cid.split("-")[1], length)])
    kat(suite, "rfc5869-A.3-salt-none-is-hashlen-zeros", lambda: mod._ch_hkdf_extract(sha, None, h(HKDF_CASES[2][1])), h(HKDF_CASES[2][5]), "RFC 5869 section 2.2 (salt not provided = HashLen zeros) against the A.3 PRK")

    group_rfc8448(suite, mod)
    group_mlkem(suite, mod, openssl, ws)


def ecb_row(suite, mod, openssl):
    """AES block cipher vs `openssl enc -aes-*-ecb -nopad` (any OpenSSL); INFO when none."""
    cid = "openssl-aes-ecb-cross-check"
    found, why = openssl
    if found is None:
        skip(suite, GA, cid, why)
        return
    path = found[0]
    problems = []
    for bits in (128, 192, 256):
        key = bytes(range(bits // 8))
        data = bytes((7 * i + 3) & 0xFF for i in range(16 * 8))
        rc, out, err = openssl_run(path, ["enc", "-aes-%d-ecb" % bits, "-nopad", "-K", key.hex()], data)
        if rc != 0:
            problems.append("openssl enc -aes-%d-ecb rc=%d: %s" % (bits, rc, err))
            continue
        g = mod._ChAesGcm(key)
        mine = b"".join(g.encrypt_block(data[i:i + 16]) for i in range(0, len(data), 16))
        problems += problem_if(mine != out, "AES-%d: encrypt_block differs from openssl on 8 blocks" % bits)
    suite.record(GA, cid, problems, detail=["%s %d.%d: 8 blocks each at AES-128/192/256" % (path, found[1][0], found[1][1])])


def group_rfc8448(suite, mod):
    sha = hashlib.sha256
    src = "RFC 8448 section 3"
    kat(suite, "rfc8448-3-client-x25519-public", lambda: mod._ch_x25519_keypair(lambda n: h(T_CLIENT_PRIV)[:n])[1], h(T_CLIENT_PUB), src + ", {client} ephemeral x25519 public key")
    kat(suite, "rfc8448-3-server-x25519-public", lambda: mod._ch_x25519_keypair(lambda n: h(T_SERVER_PRIV)[:n])[1], h(T_SERVER_PUB), src + ", {server} ephemeral x25519 public key")
    kat(suite, "rfc8448-3-ecdhe-both-ways", lambda: (mod._ch_x25519(h(T_CLIENT_PRIV), h(T_SERVER_PUB)), mod._ch_x25519(h(T_SERVER_PRIV), h(T_CLIENT_PUB))), (h(T_ECDHE), h(T_ECDHE)), src + ", the IKM of extract secret \"handshake\" from both key pairs")
    kat(suite, "rfc8448-3-early-secret", lambda: mod._ch_hkdf_extract(sha, None, h(T_ZERO32)), h(T_EARLY), src + ", extract secret \"early\" (salt 0, IKM 32 zero octets)")
    kat(suite, "rfc8448-3-derived-for-handshake", lambda: mod._ch_derive_secret(sha, h(T_EARLY), b"derived", h(T_EMPTY_HASH)), h(T_DERIVED_EARLY), src + ", derive secret for handshake \"tls13 derived\"")
    kat(suite, "rfc8448-3-handshake-secret", lambda: mod._ch_hkdf_extract(sha, h(T_DERIVED_EARLY), h(T_ECDHE)), h(T_HS), src + ", extract secret \"handshake\"")
    kat(suite, "rfc8448-3-c-hs-traffic", lambda: mod._ch_derive_secret(sha, h(T_HS), b"c hs traffic", h(T_HS_HASH)), h(T_C_HS), src + ", derive secret \"tls13 c hs traffic\"")
    kat(suite, "rfc8448-3-s-hs-traffic", lambda: mod._ch_derive_secret(sha, h(T_HS), b"s hs traffic", h(T_HS_HASH)), h(T_S_HS), src + ", derive secret \"tls13 s hs traffic\"")
    kat(suite, "rfc8448-3-derived-for-master", lambda: mod._ch_derive_secret(sha, h(T_HS), b"derived", h(T_EMPTY_HASH)), h(T_DERIVED_HS), src + ", derive secret for master \"tls13 derived\"")
    kat(suite, "rfc8448-3-master-secret", lambda: mod._ch_hkdf_extract(sha, h(T_DERIVED_HS), h(T_ZERO32)), h(T_MASTER), src + ", extract secret \"master\"")
    kat(suite, "rfc8448-3-c-ap-traffic", lambda: mod._ch_derive_secret(sha, h(T_MASTER), b"c ap traffic", h(T_AP_HASH)), h(T_C_AP), src + ", derive secret \"tls13 c ap traffic\"")
    kat(suite, "rfc8448-3-s-ap-traffic", lambda: mod._ch_derive_secret(sha, h(T_MASTER), b"s ap traffic", h(T_AP_HASH)), h(T_S_AP), src + ", derive secret \"tls13 s ap traffic\"")
    kat(suite, "rfc8448-3-exp-master", lambda: mod._ch_derive_secret(sha, h(T_MASTER), b"exp master", h(T_AP_HASH)), h(T_EXP), src + ", derive secret \"tls13 exp master\"")
    kat(suite, "rfc8448-3-server-hs-key-iv", lambda: (mod._ch_hkdf_expand_label(sha, h(T_S_HS), b"key", b"", 16), mod._ch_hkdf_expand_label(sha, h(T_S_HS), b"iv", b"", 12)), (h(T_S_HS_KEY), h(T_S_HS_IV)), src + ", {server} derive write traffic keys for handshake data")
    kat(suite, "rfc8448-3-client-hs-key-iv", lambda: (mod._ch_hkdf_expand_label(sha, h(T_C_HS), b"key", b"", 16), mod._ch_hkdf_expand_label(sha, h(T_C_HS), b"iv", b"", 12)), (h(T_C_HS_KEY), h(T_C_HS_IV)), src + ", {server} derive read traffic keys for handshake data")
    kat(suite, "rfc8448-3-server-ap-key-iv", lambda: (mod._ch_hkdf_expand_label(sha, h(T_S_AP), b"key", b"", 16), mod._ch_hkdf_expand_label(sha, h(T_S_AP), b"iv", b"", 12)), (h(T_S_AP_KEY), h(T_S_AP_IV)), src + ", {server} derive write traffic keys for application data")
    kat(suite, "rfc8448-3-server-finished-key", lambda: mod._ch_hkdf_expand_label(sha, h(T_S_HS), b"finished", b"", 32), h(T_S_FINISHED_KEY), src + ", {server} calculate finished \"tls13 finished\" (expanded)")


def group_mlkem(suite, mod, openssl, ws):
    kem = mod._ChMlKem768()
    d, z, m = bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96))

    def round_trip():
        ek, dk = kem.keygen_internal(d, z)
        ct, ss = kem.encaps_internal(ek, m)
        return (len(ek), len(dk), len(ct), len(ss), kem.decaps(dk, ct) == ss)

    kat(suite, "mlkem768-round-trip-sizes", round_trip, (1184, 2400, 1088, 32, True), "FIPS 203 ML-KEM-768 sizes (ek 1184, dk 2400, ct 1088, K 32); decaps(encaps) agrees")

    def deterministic():
        first = kem.keygen_internal(d, z) + kem.encaps_internal(kem.keygen_internal(d, z)[0], m)
        again = mod._ChMlKem768().keygen_internal(d, z)
        again = again + mod._ChMlKem768().encaps_internal(again[0], m)
        drawn = iter((d, z))
        via_rand = kem.keygen(lambda n: next(drawn)[:n])
        return (first == again, via_rand == first[:2], first[1][-32:] == z)

    kat(suite, "mlkem768-deterministic-and-rand-order", deterministic, (True, True, True), "FIPS 203 Algorithms 16/19: KeyGen_internal is a function of (d, z); keygen(rand) draws d then z; z closes dk")

    def implicit_rejection():
        ek, dk = kem.keygen_internal(d, z)
        ct, ss = kem.encaps_internal(ek, m)
        bad = bytes([ct[0] ^ 1]) + ct[1:]
        got = kem.decaps(dk, bad)
        # FIPS 203 Algorithm 18: K_bar = J(z || c), J(s) = SHAKE256(s, 8 * 32).
        return (got == hashlib.shake_256(z + bad).digest(32), got != ss)

    kat(suite, "mlkem768-implicit-rejection", implicit_rejection, (True, True), "FIPS 203 Algorithm 18: a tampered ciphertext yields J(z || c), never an error")

    found, why = openssl
    ok = found is not None and found[1] >= (3, 5)
    reason = why if found is None else "%s is OpenSSL %d.%d; ML-KEM needs >= 3.5" % (found[0], found[1][0], found[1][1])
    if ok:
        rc, out, err = openssl_run(found[0], ["list", "-kem-algorithms"])
        if rc != 0 or b"ML-KEM-768" not in out:
            ok = False
            reason = "%s lists no ML-KEM-768 KEM (rc=%d)" % (found[0], rc)
    for cid, fn in (("openssl-encaps-our-decaps", mlkem_openssl_to_us), ("our-encaps-openssl-decaps", mlkem_us_to_openssl)):
        if not ok:
            skip(suite, GA, "mlkem768-%s" % cid, reason)
            continue
        problems, detail = fn(kem, found[0], ws)
        suite.record(GA, "mlkem768-%s" % cid, problems, detail=["%s OpenSSL %d.%d; %s" % (found[0], found[1][0], found[1][1], detail)])

    if not os.path.isdir(MLKEM_VECTORS):
        skip(suite, GA, "mlkem768-nist-vectors", "tests/files/mlkem/ does not exist (optional NIST ACVP cases, plan Step 5)")
    else:
        names = sorted(n for n in os.listdir(MLKEM_VECTORS) if n != "README.md")
        suite.record(GA, "mlkem768-nist-vectors", (), status=H.INFO,
                     detail=["tests/files/mlkem/ holds %d file(s) but this suite has no reader for them yet: %r" % (len(names), names[:5])])


def mlkem_openssl_to_us(kem, openssl, ws):
    """OpenSSL encapsulates to OUR encapsulation key; we decapsulate. Three rounds."""
    problems = []
    for n in range(3):
        ek, dk = kem.keygen()
        pub = ws.write_bytes("mlkem/o2u%d_pub.der" % n, mlkem_spki(ek))
        ct_path = ws.join("mlkem", "o2u%d_ct.bin" % n)
        sec_path = ws.join("mlkem", "o2u%d_sec.bin" % n)
        rc, _out, err = openssl_run(openssl, ["pkeyutl", "-encap", "-pubin", "-inkey", pub, "-keyform", "DER", "-secret", sec_path, "-out", ct_path])
        if rc != 0:
            problems.append("round %d: openssl pkeyutl -encap rc=%d: %s" % (n, rc, err))
            continue
        with open(ct_path, "rb") as fh:
            ct = fh.read()
        with open(sec_path, "rb") as fh:
            secret = fh.read()
        problems += problem_if(len(ct) != 1088 or len(secret) != 32, "round %d: openssl produced ct %d / secret %d bytes" % (n, len(ct), len(secret)))
        got = outcome(lambda: kem.decaps(dk, ct))
        problems += ["round %d: %s" % (n, p) for p in expect_value(got, secret)]
    return problems, "3 rounds: openssl pkeyutl -encap to our SPKI, our decaps equals its secret"


def mlkem_us_to_openssl(kem, openssl, ws):
    """We encapsulate to an OpenSSL-generated key; OpenSSL decapsulates. Three rounds."""
    problems = []
    for n in range(3):
        priv = ws.join("mlkem", "u2o%d_priv.pem" % n)
        os.makedirs(os.path.dirname(priv), exist_ok=True)
        rc, _out, err = openssl_run(openssl, ["genpkey", "-algorithm", "ML-KEM-768", "-out", priv])
        if rc != 0:
            problems.append("round %d: openssl genpkey rc=%d: %s" % (n, rc, err))
            continue
        rc, der, err = openssl_run(openssl, ["pkey", "-in", priv, "-pubout", "-outform", "DER"])
        if rc != 0:
            problems.append("round %d: openssl pkey -pubout rc=%d: %s" % (n, rc, err))
            continue
        ek = der[-1184:]
        problems += problem_if(der != mlkem_spki(ek), "round %d: OpenSSL's SPKI is not the layout this suite writes" % n)
        got = outcome(lambda: kem.encaps(ek))
        if got[0] == "exc":
            problems.append("round %d: encaps raised %s: %s" % (n, type(got[1]).__name__, got[1]))
            continue
        ct, ss = got[1]
        ct_path = ws.write_bytes("mlkem/u2o%d_ct.bin" % n, ct)
        sec_path = ws.join("mlkem", "u2o%d_sec.bin" % n)
        rc, _out, err = openssl_run(openssl, ["pkeyutl", "-decap", "-inkey", priv, "-in", ct_path, "-secret", sec_path])
        if rc != 0:
            problems.append("round %d: openssl pkeyutl -decap rc=%d: %s" % (n, rc, err))
            continue
        with open(sec_path, "rb") as fh:
            secret = fh.read()
        problems += problem_if(secret != ss, "round %d: openssl's secret %s differs from ours %s" % (n, short(secret), short(ss)))
    return problems, "3 rounds: our encaps to an openssl genpkey ML-KEM-768 key, openssl pkeyutl -decap equals our secret"


# --- the test's own TLS 1.3 arithmetic: RFC 5869 and RFC 8446 section 7.1 over hmac ---

def vec(width, data):
    """An RFC 8446 section 3.4 vector: `data` behind a `width`-byte big-endian length."""
    data = bytes(data)
    return len(data).to_bytes(width, "big") + data


def hkdf_extract(hm, salt, ikm):
    return hmac.new(salt, ikm, hm).digest()


def hkdf_expand(hm, prk, info, length):
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hm).digest()
        out += block
        counter += 1
    return out[:length]


def expand_label(hm, secret, label, context, length):
    """HKDF-Expand-Label, written here from RFC 8446 section 7.1 -- never the module's."""
    return hkdf_expand(hm, secret, length.to_bytes(2, "big") + vec(1, b"tls13 " + label) + vec(1, context), length)


def derive_secret(hm, secret, label, transcript):
    return expand_label(hm, secret, label, hm(transcript).digest(), hm().digest_size)


def hs_msg(msg_type, body):
    return bytes([msg_type]) + vec(3, body)


def hs_messages(data):
    """[(type, whole message)] of concatenated handshake messages."""
    out = []
    while len(data) >= 4:
        end = 4 + int.from_bytes(data[1:4], "big")
        out.append((data[0], data[:end]))
        data = data[end:]
    return out


def split_records(data):
    """[(content_type, body)] of a byte string made of whole TLS records."""
    out = []
    data = bytes(data)
    while len(data) >= 5:
        n = int.from_bytes(data[3:5], "big")
        out.append((data[0], data[5:5 + n]))
        data = data[5 + n:]
    return out


def records_handshake(data):
    """The handshake bytes the plaintext handshake records of `data` carry (a CCS is skipped)."""
    return b"".join(body for ctype, body in split_records(data) if ctype == 22)


def hello_record(session_id, cipher, exts, random, legacy=0x0303):
    """A plaintext ServerHello / HelloRetryRequest record (RFC 8446 section 4.1.3); `exts` is [(type, body)] or None."""
    body = legacy.to_bytes(2, "big") + random + vec(1, session_id) + cipher.to_bytes(2, "big") + b"\x00"
    if exts is not None:
        block = b""
        for etype, ebody in exts:
            block += etype.to_bytes(2, "big") + vec(2, ebody)
        body += vec(2, block)
    return b"\x16\x03\x03" + vec(2, hs_msg(2, body))


def hello_prefix_len(ch):
    """Bytes of a ClientHello message before its extension block (header, version, random, session id, suites, compression)."""
    p = 4 + 2 + 32
    p += 1 + ch[p]
    p += 2 + int.from_bytes(ch[p:p + 2], "big")
    p += 1 + ch[p]
    return p


def peer_parse_ch(ch):
    """(session id, {group: key_exchange}, [(extension type, body)]) of a ClientHello message, parsed here."""
    sid = ch[39:39 + ch[38]]
    p = hello_prefix_len(ch)
    end = p + 2 + int.from_bytes(ch[p:p + 2], "big")
    p += 2
    exts = []
    while p < end:
        n = int.from_bytes(ch[p + 2:p + 4], "big")
        exts.append((int.from_bytes(ch[p:p + 2], "big"), ch[p + 4:p + 4 + n]))
        p += 4 + n
    shares = {}
    body = dict(exts).get(51, b"")
    q = 2
    while q < len(body):
        n = int.from_bytes(body[q + 2:q + 4], "big")
        shares[int.from_bytes(body[q:q + 2], "big")] = body[q + 4:q + 4 + n]
        q += 4 + n
    return sid, shares, exts


class PeerCipher:
    """One direction's record protection, written here (RFC 8446 sections 5.2-5.4).

    Only the AEAD primitive is the module's, used after group A proved it by
    published vectors (declared partial self-agreement). seal() polices no
    plaintext limit, so a peer can build the over-limit records the client
    must refuse.
    """

    def __init__(self, aead, iv, seq=0):
        self.aead = aead
        self.iv = int.from_bytes(iv, "big")
        self.seq = seq

    def nonce(self):
        value = (self.iv ^ self.seq).to_bytes(12, "big")
        self.seq += 1
        return value

    def seal(self, ctype, content, pad=0):
        inner = bytes(content) + bytes([ctype]) + bytes(pad)
        header = b"\x17\x03\x03" + (len(inner) + 16).to_bytes(2, "big")
        return header + self.aead.seal(self.nonce(), header, inner)

    def open(self, body):
        header = b"\x17\x03\x03" + len(body).to_bytes(2, "big")
        inner = self.aead.open(self.nonce(), header, bytes(body)).rstrip(b"\x00")
        return inner[-1], inner[:-1]


def peer_cipher(mod, suite, secret):
    """A PeerCipher for one traffic secret: key and iv by the test's own HKDF-Expand-Label (RFC 8446 section 7.3)."""
    hm, key_len, cls, _name = TLS13_SUITES[suite]
    return PeerCipher(getattr(mod, cls)(expand_label(hm, secret, b"key", b"", key_len)), expand_label(hm, secret, b"iv", b"", 12))


# A DER SEQUENCE { INTEGER 0 }: the client parses the chain with bounds but does not verify it.
FAKE_CERT = b"\x30\x03\x02\x01\x00"


class ScriptedPeer:
    """A scripted TLS 1.3 server written in this test, driving one _ChTls.

    The transcript, the key schedule, the Finished values and the record layer
    are computed HERE with hashlib/hmac; the key agreement (X25519, P-256,
    ML-KEM-768 per draft-ietf-tls-ecdhe-mlkem: ciphertext || X25519, secret =
    ML-KEM || X25519) and the AEAD use the module's primitives, each proven by
    a published vector in group A first (declared partial self-agreement).
    Every method returns what the client wrote back or lets its refusal
    propagate.
    """

    def __init__(self, mod, suite=0x1301, group=0x001D, alps=False):
        self.mod = mod
        self.suite = suite
        self.group = group
        self.alps = alps
        self.hm = TLS13_SUITES[suite][0]
        self.tls = mod._ChTls("example.com")
        self.ch1 = records_handshake(self.tls.start())
        self.transcript = self.ch1
        self.sid, self.shares, self.exts = peer_parse_ch(self.ch1)
        self.ccs_sent = False
        self.ch2 = None
        self.hrr_out = b""

    def hrr_record(self, group, cipher=None, cookie=None):
        cipher = self.suite if cipher is None else cipher
        exts = [(43, b"\x03\x04"), (51, group.to_bytes(2, "big"))]
        if cookie is not None:
            exts.append((44, vec(2, cookie)))
        return hello_record(self.sid, cipher, exts, h(HRR_RANDOM))

    def send_hrr(self, group=0x0017, cookie=b"mcp_chrome/scripted-hrr-cookie"):
        """Send an HRR; adopt CH2 and the message_hash transcript (RFC 8446 section 4.4.1)."""
        rec = self.hrr_record(group, None, cookie)
        out = self.tls.feed(rec)
        self.transcript = hs_msg(254, self.hm(self.ch1).digest()) + rec[5:]
        recs = split_records(out)
        self.ccs_sent = bool(recs) and recs[0] == (20, b"\x01")
        self.ch2 = records_handshake(out)
        self.transcript += self.ch2
        self.sid, self.shares, self.exts = peer_parse_ch(self.ch2)
        self.group = group
        self.hrr_out = out
        return out

    def server_share(self):
        """(server key_exchange, shared secret) for self.group against the client's share."""
        mod = self.mod
        client = self.shares[self.group]
        if self.group == 0x11EC:
            ct, secret = mod._ChMlKem768().encaps(client[:1184])
            priv, pub = mod._ch_x25519_keypair()
            return ct + pub, secret + mod._ch_x25519(priv, client[1184:])
        if self.group == 0x0017:
            d, pub = mod._ch_p256_keypair()
            return pub, mod._ch_p256_shared(d, client)
        priv, pub = mod._ch_x25519_keypair()
        return pub, mod._ch_x25519(priv, client)

    def server_hello(self, cipher=None, share=None):
        """The ServerHello record; derives the handshake traffic secrets. `share(pub)` may tamper the key."""
        cipher = self.suite if cipher is None else cipher
        pub, shared = self.server_share()
        if share is not None:
            pub = share(pub)
        rec = hello_record(self.sid, cipher, [(43, b"\x03\x04"), (51, self.group.to_bytes(2, "big") + vec(2, pub))], os.urandom(32))
        hm = self.hm
        hl = hm().digest_size
        self.transcript += rec[5:]
        early = hkdf_extract(hm, bytes(hl), bytes(hl))
        self.hs_secret = hkdf_extract(hm, derive_secret(hm, early, b"derived", b""), shared)
        self.c_hs = derive_secret(hm, self.hs_secret, b"c hs traffic", self.transcript)
        self.s_hs = derive_secret(hm, self.hs_secret, b"s hs traffic", self.transcript)
        self.s_hs_w = peer_cipher(self.mod, self.suite, self.s_hs)
        self.c_hs_r = peer_cipher(self.mod, self.suite, self.c_hs)
        return rec

    def flight(self, bad_finished=False):
        """EE, Certificate, CertificateVerify and Finished; derives the application traffic secrets."""
        hm = self.hm
        hl = hm().digest_size
        exts = (16).to_bytes(2, "big") + vec(2, vec(2, vec(1, b"h2")))
        if self.alps:
            exts += (17613).to_bytes(2, "big") + vec(2, h(SERVER_ALPS))
        ee = hs_msg(8, vec(2, exts))
        cert = hs_msg(11, b"\x00" + vec(3, vec(3, FAKE_CERT) + vec(2, b"")))
        cv = hs_msg(15, (0x0403).to_bytes(2, "big") + vec(2, b"\x01\x02"))
        key = expand_label(hm, self.s_hs, b"finished", b"", hl)
        verify = hmac.new(key, hm(self.transcript + ee + cert + cv).digest(), hm).digest()
        if bad_finished:
            verify = bytes([verify[0] ^ 1]) + verify[1:]
        fin = hs_msg(20, verify)
        self.transcript += ee + cert + cv + fin
        master = hkdf_extract(hm, derive_secret(hm, self.hs_secret, b"derived", b""), bytes(hl))
        self.c_ap = derive_secret(hm, master, b"c ap traffic", self.transcript)
        self.s_ap = derive_secret(hm, master, b"s ap traffic", self.transcript)
        self.s_ap_w = peer_cipher(self.mod, self.suite, self.s_ap)
        self.c_ap_r = peer_cipher(self.mod, self.suite, self.c_ap)
        return ee + cert + cv + fin

    def handshake(self):
        """ServerHello + the server flight through the client; problems with the client's answer."""
        out = self.tls.feed(self.server_hello())
        out += self.tls.feed(self.s_hs_w.seal(22, self.flight()))
        return self.check_client_flight(out)

    def check_client_flight(self, out):
        """The client's second flight: [CCS], [client EE with ALPS], Finished over the peer's transcript."""
        recs = split_records(out)
        if not self.ccs_sent:
            if not recs or recs[0] != (20, b"\x01"):
                return ["no compat ChangeCipherSpec before the client Finished: records %r" % [(t, len(b)) for t, b in recs]]
            recs = recs[1:]
        want = 2 if self.alps else 1
        if len(recs) != want or any(t != 23 for t, _b in recs):
            return ["client flight %r, wanted %d protected record(s)" % ([(t, len(b)) for t, b in recs], want)]
        problems = []
        client_ee = b""
        if self.alps:
            ctype, client_ee = self.c_hs_r.open(recs[0][1])
            problems += problem_if((ctype, client_ee) != (22, h(CLIENT_ALPS_EE)), "client EncryptedExtensions %s (type %d), wanted %s" % (short(client_ee), ctype, CLIENT_ALPS_EE))
        ctype, fin = self.c_hs_r.open(recs[-1][1])
        hm = self.hm
        key = expand_label(hm, self.c_hs, b"finished", b"", hm().digest_size)
        want_fin = hs_msg(20, hmac.new(key, hm(self.transcript + client_ee).digest(), hm).digest())
        problems += problem_if((ctype, fin) != (22, want_fin), "the client Finished does not verify over the peer's transcript%s" % (" incl. the client EE" if self.alps else ""))
        self.transcript += client_ee + fin
        return problems

    def exchange(self, data=b"server to client", reply=b"client to server"):
        """Application data both ways under the application traffic keys; problems."""
        self.tls.feed(self.s_ap_w.seal(23, data))
        got = self.tls.read_app()
        recs = split_records(self.tls.send_app(reply))
        problems = problem_if(got != data, "the client read %s, wanted %s" % (short(got), short(data)))
        if len(recs) != 1:
            return problems + ["send_app wrote %d records for %d bytes" % (len(recs), len(reply))]
        return problems + problem_if(self.c_ap_r.open(recs[0][1]) != (23, reply), "the peer cannot read the client's application data")

    def rekey_server(self):
        self.s_ap = expand_label(self.hm, self.s_ap, b"traffic upd", b"", self.hm().digest_size)
        self.s_ap_w = peer_cipher(self.mod, self.suite, self.s_ap)

    def rekey_client(self):
        self.c_ap = expand_label(self.hm, self.c_ap, b"traffic upd", b"", self.hm().digest_size)
        self.c_ap_r = peer_cipher(self.mod, self.suite, self.c_ap)


def connected(mod, suite=0x1301, group=0x001D, alps=False):
    """A ScriptedPeer whose handshake completed; raises when the client's flight is wrong."""
    peer = ScriptedPeer(mod, suite, group, alps)
    problems = peer.handshake()
    if problems:
        raise RuntimeError("scripted handshake: " + "; ".join(problems))
    return peer


def run_row(suite, group, cid, fn, source=""):
    """Record fn() -> (problems, detail); an exception is the row's problem, never a crash."""
    got = outcome(fn)
    if got[0] == "exc":
        suite.record(group, cid, ["raised %s(%r)" % (type(got[1]).__name__, str(got[1])[:200])], detail=[source] if source else [])
        return
    problems, detail = got[1]
    if source:
        detail = [source] + list(detail)
    suite.record(group, cid, problems, detail=detail)


def refusal(fn, exc_type, message, tls=None):
    """expect_refusal plus: the engine must stay failed afterwards."""
    problems = expect_refusal(outcome(fn), exc_type, message)
    if tls is not None and not problems:
        problems += problem_if(not tls.failed, "the engine is not marked failed after the refusal")
    return problems


def suite_tag(s):
    return TLS13_SUITES[s][3]


# --- B. ClientHello vs the Chrome 154 captures -------------------------------------

def readme_facts():
    """What tests/files/chrome/154/README.md states: JA4 values, extension list, ECH buckets, length formulas."""
    with open(FIXTURE_README, encoding="utf-8") as fh:
        text = fh.read()
    facts = {}
    for label, key in (("every fresh, successful ClientHello with an SNI", "ja4_sni"), ("IP-literal origin, no SNI", "ja4_ip"), ("second ClientHello after a HelloRetryRequest", "ja4_ch2")):
        m = re.search(r"^\| %s[^|]*\| `(t13[0-9a-z_]+)` \|$" % re.escape(label), text, re.M)
        facts[key] = m.group(1) if m else None
    m = re.search(r"17 non-GREASE extensions \(([0-9,\s]+)\)", text)
    facts["ext_types"] = tuple(int(x) for x in m.group(1).split(",")) if m else None
    m = re.search(r"ECH GREASE\s+payload length drawn from \{([0-9, ]+)\}", text)
    facts["ech_buckets"] = tuple(int(x) for x in m.group(1).split(",")) if m else None
    m = re.search(r"handshake length =\s+(\d+) \+ len\(SNI\) \+ ECH payload length", text)
    facts["len_base_sni"] = int(m.group(1)) if m else None
    m = re.search(r"length = (\d+) \+ ECH payload length", text)
    facts["len_base_ip"] = int(m.group(1)) if m else None
    m = re.search(r"handshake length = CH1 length - (\d+) \+ (\d+) \+ cookie length", text)
    facts["ch2_delta"] = (int(m.group(1)), int(m.group(2))) if m else None
    return facts


def seeded_rand(seed):
    """A deterministic rand(n) per seed: SHAKE-256 over (seed, draw number) -- the test's own source [G-b]."""
    state = [0]

    def rand(n):
        state[0] += 1
        return hashlib.shake_256(b"mcp_chrome seed %d draw %d" % (seed, state[0])).digest(n)
    return rand


def build_hello(mod, host, seed, profile=None):
    """(records, handshake message, meta) of a first ClientHello; the key shares are drawn bytes of the draft's lengths."""
    rand = seeded_rand(seed)
    shares = {0x11EC: rand(1216), 0x001D: rand(32)}
    return mod._ch_client_hello(host, shares, profile, rand)


def build_many(mod, cc, host, profile=None, first_seed=0):
    """[(records, hs, meta, parsed)] for CH_SEEDS seeds and the problems met building or parsing them."""
    built, problems = [], []
    for seed in range(first_seed, first_seed + CH_SEEDS):
        got = outcome(lambda: build_hello(mod, host, seed, profile))
        if got[0] == "exc":
            problems.append("seed %d: raised %s(%r)" % (seed, type(got[1]).__name__, str(got[1])[:120]))
            continue
        records, hs, meta = got[1]
        parsed = outcome(lambda: cc.parse_client_hello(hs[4:]))
        if parsed[0] == "exc":
            problems.append("seed %d: chrome_capture cannot parse it: %s" % (seed, parsed[1]))
            continue
        built.append((records, hs, meta, parsed[1]))
    return built, problems


def write_candidates(ws, name, records):
    """One JSON per candidate connection, in the shape chrome_capture's diff reads."""
    path = ws.subdir("candidates", name)
    for i, rec in enumerate(records):
        with open(os.path.join(path, "candidate-%03d.json" % (i + 1)), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
    return path


def diff_problems(cc, ref_dir, cand_dir):
    """(problems, detail) of chrome_capture diff: every FIXED row and every unmatched group is a problem."""
    got = outcome(lambda: cc.diff_captures(ref_dir, cand_dir))
    if got[0] == "exc":
        return ["diff raised %s: %s" % (type(got[1]).__name__, got[1])], []
    result = got[1]
    problems = ["FIXED %s %s" % (g, cc.diff_feature_label(f)) for g, f in result["fixed"]]
    problems += cc.diff_unmatched_lines(result)
    kinds = {}
    for comp in result["comparisons"]:
        for kind, rows in comp["rows"].items():
            kinds[kind] = kinds.get(kind, 0) + len(rows)
    detail = ["reference %r, candidate %r; rows by kind %s" % (result["reference_counts"], result["candidate_counts"], ", ".join("%s=%d" % kv for kv in sorted(kinds.items()) if kv[1]) or "none")]
    return problems, detail


def ext_by_type(ch):
    return dict((e["type"], e) for e in ch["extensions"])


def grease_problems(cc, ch):
    """RFC 8701 GREASE where BoringSSL puts it, and nowhere else (read by chrome_capture's parser)."""
    problems = []
    exts = ch["extensions"]
    types = [e["type"] for e in exts]
    problems += problem_if(not exts or not cc.is_grease(types[0]) or exts[0]["length"] != 0, "the first extension is not an empty GREASE")
    problems += problem_if(not exts or not cc.is_grease(types[-1]) or exts[-1]["payload_hex"] != "00", "the last extension is not a GREASE carrying one zero byte")
    problems += problem_if(sum(1 for t in types if cc.is_grease(t)) != 2 or types[0] == types[-1], "GREASE extension types %r" % [t for t in types if cc.is_grease(t)])
    ciphers = ch["cipher_suites"]
    problems += problem_if(not ciphers[0]["grease"] or any(c["grease"] for c in ciphers[1:]), "GREASE is not the first cipher suite only")
    ext = ext_by_type(ch)
    for etype, key, field in ((10, "groups", "id"), (13, "algorithms", "id"), (43, "versions", "id")):
        ids = [int(x[field], 16) for x in ext[etype]["decoded"][key]]
        problems += problem_if(not ids or not cc.is_grease(ids[0]) or any(cc.is_grease(x) for x in ids[1:]), "extension %d: GREASE is not the first entry only: %r" % (etype, ids[:3]))
    shares = ext[51]["decoded"]["shares"]
    first = int(shares[0]["group"], 16) if shares else 0
    problems += problem_if(not cc.is_grease(first) or shares[0]["key_len"] != 1 or any(cc.is_grease(int(s["group"], 16)) for s in shares[1:]), "key_share does not lead with a 1-byte GREASE share only")
    problems += problem_if(first != int(ext[10]["decoded"]["groups"][0]["id"], 16), "the GREASE key share group differs from the GREASE supported_groups entry")
    return problems


def group_client_hello(suite, mod, cc, facts, ws):
    """Group B: the builder's first ClientHello, judged by the INDEPENDENT chrome_capture parser [G-d]."""
    src = "tests/files/chrome/154/README.md"
    built, problems = build_many(mod, cc, CAPTURE_HOST)
    again = outcome(lambda: build_hello(mod, CAPTURE_HOST, 0))
    problems += problem_if(again[0] != "value" or not built or again[1][1] != built[0][1], "seed 0 does not rebuild byte-identically")
    problems += problem_if(len(set(b[1] for b in built)) != CH_SEEDS, "%d distinct ClientHellos from %d seeds" % (len(set(b[1] for b in built)), CH_SEEDS))
    suite.record(GB, "ch-20-seeds-build-deterministically", problems,
                 detail=["%d seeds, host %s; the key shares are drawn bytes (only their lengths reach the comparison)" % (CH_SEEDS, CAPTURE_HOST)])

    want = facts["ext_types"]
    problems = problem_if(want is None or len(want) != 17, "%s no longer lists 17 non-GREASE extensions: %r" % (src, want))
    for i, b in enumerate(built):
        got = sorted(t for t in (e["type"] for e in b[3]["extensions"]) if not cc.is_grease(t))
        problems += problem_if(want is not None and got != sorted(want), "seed %d: non-GREASE extensions %r" % (i, got))
    suite.record(GB, "ch-17-non-grease-extensions", problems, detail=["the set %s reads: %r" % (src, want)])

    problems = []
    for i, b in enumerate(built):
        problems += ["seed %d: %s" % (i, p) for p in grease_problems(cc, b[3])]
    suite.record(GB, "ch-grease-positions", problems[:6],
                 detail=["cipher, supported_groups, signature_algorithms, supported_versions, the 1-byte key share: first entry only; an empty leading and a one-zero-byte trailing extension (RFC 8701, BoringSSL)"])

    orders = set(tuple(t for t in (e["type"] for e in b[3]["extensions"]) if not cc.is_grease(t)) for b in built)
    suite.record(GB, "ch-permutation-varies", problem_if(len(orders) != len(built), "%d distinct extension orders over %d ClientHellos" % (len(orders), len(built))),
                 detail=["%s: 20 distinct orders out of 20 in every set; here %d of %d" % (src, len(orders), len(built))])

    buckets = facts["ech_buckets"]
    payloads = [cc.client_hello_ech_payload_len(b[3]) for b in built]
    problems = problem_if(buckets is None, "%s no longer states the ECH buckets" % src)
    problems += ["seed %d: ECH payload %r outside %r" % (i, p, buckets) for i, p in enumerate(payloads) if buckets is not None and p not in buckets]
    problems += problem_if(len(set(payloads)) < 2, "the ECH payload length never varies: %r" % sorted(set(payloads)))
    suite.record(GB, "ch-ech-payload-in-buckets", problems, detail=["buckets %r (%s); drawn %r" % (buckets, src, sorted(set(payloads)))])

    base = facts["len_base_sni"]
    problems = problem_if(base is None, "%s no longer states the length formula" % src)
    for i, b in enumerate(built):
        want_len = None if base is None else base + len(CAPTURE_HOST) + payloads[i]
        problems += problem_if(base is not None and len(b[1]) != want_len, "seed %d: %d bytes, wanted %r" % (i, len(b[1]), want_len))
    suite.record(GB, "ch-length-base-plus-sni-plus-ech", problems,
                 detail=["handshake length = %r + len(SNI) + ECH payload (%s; docs/concepts/spec-ddg.md:482 measured 1759 over 109 connections)" % (base, src)])

    ja4 = facts["ja4_sni"]
    got = sorted(set(cc.fingerprints(b[3])["ja4"] for b in built))
    suite.record(GB, "ch-ja4-equals-readme", problem_if(ja4 is None or got != [ja4], "JA4 %r, the README says %r" % (got, ja4)),
                 detail=["JA4 by chrome_capture.fingerprints: %r" % got])

    ip_built, problems = build_many(mod, cc, "127.0.0.1", None, 100)
    base_ip = facts["len_base_ip"]
    for i, b in enumerate(ip_built):
        types = [e["type"] for e in b[3]["extensions"]]
        pay = cc.client_hello_ech_payload_len(b[3])
        problems += problem_if(0 in types, "seed %d: a server_name extension for an IP literal" % i)
        problems += problem_if(sum(1 for t in types if not cc.is_grease(t)) != 16, "seed %d: %d non-GREASE extensions" % (i, sum(1 for t in types if not cc.is_grease(t))))
        problems += problem_if(cc.fingerprints(b[3])["ja4"] != facts["ja4_ip"], "seed %d: JA4 %s, the README says %r" % (i, cc.fingerprints(b[3])["ja4"], facts["ja4_ip"]))
        problems += problem_if(base_ip is None or len(b[1]) != base_ip + pay, "seed %d: %d bytes, wanted %r + %d" % (i, len(b[1]), base_ip, pay))
    suite.record(GB, "ch-ip-literal-no-sni-set-7", problems[:6], detail=["host 127.0.0.1: no server_name, 16 non-GREASE extensions, JA4 %r, length %r + ECH payload (%s)" % (facts["ja4_ip"], base_ip, src)])

    for name, host, alpn in CH_SETS:
        source = ip_built if host != CAPTURE_HOST else built
        records = [{"client_hello_hex": b[0].hex(), "tls": {"alpn": alpn}} for b in source]
        cand = write_candidates(ws, name, records)
        run_row(suite, GB, "diff-vs-%s" % name, lambda: diff_problems(cc, os.path.join(FIXTURES, name), cand),
                "chrome_capture diff tests/files/chrome/154/%s/ vs %d built ClientHellos (host %s, ALPN %s): no FIXED difference, no unmatched group" % (name, len(source), host, alpn))

    profile = dict(mod.CHROME_PROFILE)
    removed = profile["sigalgs"][-1]
    profile["sigalgs"] = tuple(profile["sigalgs"][:-1])
    bad, problems = build_many(mod, cc, CAPTURE_HOST, profile)
    cand = write_candidates(ws, "negative-sigalg", [{"client_hello_hex": b[0].hex(), "tls": {"alpn": "h2"}} for b in bad])
    got = outcome(lambda: cc.diff_captures(os.path.join(FIXTURES, "navigate"), cand))
    fixed = [] if got[0] == "exc" else [f for _g, f in got[1]["fixed"]]
    problems += problem_if(got[0] == "exc", "diff raised %r" % (got[1],))
    problems += problem_if("ext.13" not in fixed, "the diff did not name ext.13 (signature_algorithms): FIXED %r" % fixed)
    problems += problem_if(bad and cc.fingerprints(bad[0][3])["ja4"] == facts["ja4_sni"], "the JA4 did not change")
    suite.record(GB, "negative-control-one-sigalg-removed", problems,
                 detail=["profile without sigalg 0x%04x vs navigate/: the comparison must FAIL; FIXED %r" % (removed, fixed)])


# --- C. TLS 1.3 --------------------------------------------------------------------

def server_context(curve=None):
    """A TLS 1.3-only Python ssl server context on the tests/files/tls leaf, ALPN h2."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(TLS_CERT, TLS_KEY)
    ctx.set_alpn_protocols(["h2"])
    if curve is not None:
        ctx.set_ecdh_curve(curve)
    return ctx


def memorybio_row(mod, s):
    """_ChTls against an in-memory ssl.SSLObject server over two MemoryBIOs [G-d]."""
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = server_context().wrap_bio(incoming, outgoing, server_side=True)
    profile = dict(mod.CHROME_PROFILE)
    profile["ciphers"] = (s,)
    tls = mod._ChTls("localhost", profile)
    incoming.write(tls.start())
    done = False
    for _ in range(8):
        if not done:
            try:
                server.do_handshake()
                done = True
            except ssl.SSLWantReadError:
                pass
        data = outgoing.read()
        if data:
            answer = tls.feed(data)
            if answer:
                incoming.write(answer)
        if done and tls.handshake_done and not incoming.pending:
            break
    if not (done and tls.handshake_done):
        return ["handshake incomplete: server %s, client %s" % (done, tls.handshake_done)], []
    ping, pong = b"ping from the stdlib client", b"pong from ssl.SSLObject"
    incoming.write(tls.send_app(ping))
    got = server.read(4096)
    server.write(pong)
    tls.feed(outgoing.read())
    back = tls.read_app()
    problems = problem_if(got != ping, "the server read %r" % got) + problem_if(back != pong, "the client read %r" % back)
    problems += problem_if(tls.cipher != s or server.cipher()[0] != suite_tag(s), "cipher: client 0x%04x, server %r" % (tls.cipher, server.cipher()[0]))
    problems += problem_if(server.version() != "TLSv1.3" or tls.alpn != "h2" or server.selected_alpn_protocol() != "h2", "version %s, ALPN client %r / server %r" % (server.version(), tls.alpn, server.selected_alpn_protocol()))
    return problems, ["server %s %s ALPN %s; client group=0x%04x cipher=0x%04x hrr=%d; %s" % (server.version(), server.cipher()[0], server.selected_alpn_protocol(), tls.group, tls.cipher, int(tls.hrr), ssl.OPENSSL_VERSION)]


def serve_one(ctx, lsock, state):
    """Thread body: accept one connection, echo 'pong:' + the request reversed, close_notify."""
    try:
        conn, _addr = lsock.accept()
        conn.settimeout(20)
        s = ctx.wrap_socket(conn, server_side=True)
        state["cipher"] = s.cipher()
        got = b""
        while len(got) < state["expect"]:
            chunk = s.recv(65536)
            if not chunk:
                break
            got += chunk
        state["got"] = got
        s.sendall(b"pong:" + got[::-1])
        try:
            s.unwrap()
        except (OSError, ValueError) as exc:
            state["unwrap"] = repr(exc)
        s.close()
    except (OSError, ValueError) as exc:
        state["error"] = repr(exc)


def run_client(tls, sock, request):
    """Handshake over `sock`, send `request`, read until the server's close_notify or EOF; the application bytes."""
    sock.sendall(tls.start())
    while not tls.handshake_done:
        data = sock.recv(65536)
        if not data:
            raise ConnectionError("EOF during the handshake")
        out = tls.feed(data)
        if out:
            sock.sendall(out)
    sock.sendall(tls.send_app(request))
    got = b""
    while not tls.closed:
        data = sock.recv(65536)
        if not data:
            break
        out = tls.feed(data)
        if out:
            sock.sendall(out)
        got += tls.read_app()
    try:
        sock.sendall(tls.close_notify())
    except OSError:
        pass
    return got


def loopback(mod, curve=None):
    """(tls, problems, detail) of one exchange with a loopback Python ssl server in a thread."""
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    request = os.urandom(40000)
    state = {"expect": len(request)}
    thread = threading.Thread(target=serve_one, args=(server_context(curve), lsock, state), daemon=True)
    thread.start()
    tls = mod._ChTls("localhost")
    try:
        with socket.create_connection(lsock.getsockname(), timeout=20) as sock:
            got = run_client(tls, sock, request)
        thread.join(20)
    finally:
        lsock.close()
    problems = problem_if(got != b"pong:" + request[::-1], "the client read %d bytes, wanted %d" % (len(got), len(request) + 5))
    problems += problem_if(state.get("got") != request, "the server read %d bytes of %d" % (len(state.get("got") or b""), len(request)))
    problems += problem_if("error" in state, "server: %s" % state.get("error"))
    problems += problem_if(not tls.closed, "no close_notify from the server")
    detail = ["group=0x%04x cipher=0x%04x alpn=%s hrr=%d t_hs=%sms; server %r; %s" % (tls.group or 0, tls.cipher or 0, tls.alpn, int(tls.hrr), tls.t_hs_ms, state.get("cipher"), ssl.OPENSSL_VERSION)]
    return tls, problems, detail


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def read_until(proc, needle, timeout):
    """What the child printed until `needle` appeared, EOF, or `timeout` seconds."""
    buf = b""
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno()
    while needle not in buf:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        ready, _w, _x = select.select([fd], [], [], left)
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk
    return buf


def s_server_row(mod, path, group_name, s, want_group, want_hrr):
    """_ChTls against `openssl s_server -www` restricted to one group and one suite."""
    port = free_port()
    argv = [path, "s_server", "-accept", "127.0.0.1:%d" % port, "-naccept", "1", "-cert", TLS_CERT, "-key", TLS_KEY,
            "-tls1_3", "-groups", group_name, "-ciphersuites", suite_tag(s), "-alpn", "h2", "-www"]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        banner = read_until(proc, b"ACCEPT", 20)
        if b"ACCEPT" not in banner:
            return ["s_server did not start: %r" % banner[-200:]], []
        tls = mod._ChTls("localhost")
        with socket.create_connection(("127.0.0.1", port), timeout=20) as sock:
            got = run_client(tls, sock, b"GET / HTTP/1.0\r\n\r\n")
    finally:
        proc.kill()
        proc.wait(10)
        proc.stdin.close()
        proc.stdout.close()
    problems = problem_if(not got.startswith(b"HTTP/1.0 200 ok"), "response %r" % got[:40])
    problems += problem_if(tls.group != want_group or tls.cipher != s or tls.hrr != want_hrr, "group 0x%04x cipher 0x%04x hrr %s, wanted 0x%04x 0x%04x %s" % (tls.group or 0, tls.cipher or 0, tls.hrr, want_group, s, want_hrr))
    return problems, ["group=0x%04x cipher=0x%04x alpn=%s hrr=%d t_hs=%sms; %d response bytes" % (tls.group, tls.cipher, tls.alpn, int(tls.hrr), tls.t_hs_ms, len(got))]


def rfc8448_offer(cc):
    """What the trace's ClientHello offered, read by chrome_capture's parser (not typed here)."""
    body = h(R8448_CH)[4:]
    ch = cc.parse_client_hello(body)
    ext = ext_by_type(ch)
    offer = {}
    offer["session_id"] = cc.client_hello_session_id(body)
    offer["ciphers"] = tuple(int(c["id"], 16) for c in ch["cipher_suites"])
    offer["groups"] = tuple(int(g["id"], 16) for g in ext[10]["decoded"]["groups"])
    offer["sigalgs"] = tuple(int(a["id"], 16) for a in ext[13]["decoded"]["algorithms"])
    offer["ext_types"] = frozenset(ext)
    offer["alpn"] = ()
    offer["alps"] = ()
    offer["cert_compression"] = ()
    return offer


def rfc8448_engine(mod, cc):
    """_ChTls entered through the _begin seam with the trace's ClientHello and client X25519 key.

    The trace's client sends no compat ChangeCipherSpec, so the engine is told
    one was already sent (`_ccs_sent`, a declared test seam).
    """
    tls = mod._ChTls("server")
    tls._begin(h(R8448_CH), {}, {0x001D: h(T_CLIENT_PRIV)}, rfc8448_offer(cc))
    tls._ccs_sent = True
    return tls


def rfc8448_flight_row(mod):
    """The trace's server flight decrypts under the published keys; both Finished values verify here [G-d]."""
    sha = hashlib.sha256
    ch, sh = h(R8448_CH), h(R8448_SH_REC)[5:]
    problems = problem_if(sha(ch + sh).digest() != h(T_HS_HASH), "CH || SH does not hash to the trace's handshake hash")
    ctype, content = PeerCipher(mod._ChAesGcm(h(T_S_HS_KEY)), h(T_S_HS_IV)).open(h(R8448_FLIGHT_REC)[5:])
    msgs = hs_messages(content)
    problems += problem_if(ctype != 22 or [t for t, _m in msgs] != [8, 11, 15, 20], "inner type %d, messages %r" % (ctype, [t for t, _m in msgs]))
    if problems:
        return problems, []
    before_fin = b"".join(m for _t, m in msgs[:3])
    want = hmac.new(h(T_S_FINISHED_KEY), sha(ch + sh + before_fin).digest(), sha).digest()
    problems += problem_if(msgs[3][1][4:] != want, "the server Finished does not verify under the trace's finished key")
    ctype, cfin = PeerCipher(mod._ChAesGcm(h(T_C_HS_KEY)), h(T_C_HS_IV)).open(h(R8448_CFIN_REC)[5:])
    key = expand_label(sha, h(T_C_HS), b"finished", b"", 32)
    want = hs_msg(20, hmac.new(key, sha(ch + sh + content).digest(), sha).digest())
    problems += problem_if((ctype, cfin) != (22, want), "the trace's client Finished does not verify")
    return problems, ["flight of %d bytes: EE %d, Certificate %d, CertificateVerify %d, Finished %d" % tuple([len(content)] + [len(m) for _t, m in msgs])]


def rfc8448_replay_row(mod, cc, upto):
    """The engine replays the trace: secrets, the client Finished record, app data, NST, close_notify."""
    tls = rfc8448_engine(mod, cc)
    problems = problem_if(tls.feed(h(R8448_SH_REC)) != b"", "the ServerHello produced output")
    problems += problem_if(tls._c_hs != h(T_C_HS) or tls._s_hs != h(T_S_HS), "handshake traffic secrets differ from the trace")
    out = tls.feed(h(R8448_FLIGHT_REC))
    problems += problem_if(out != h(R8448_CFIN_REC), "client Finished record %s, the trace's is %s" % (short(out), short(h(R8448_CFIN_REC))))
    problems += problem_if(tls._c_ap != h(T_C_AP) or tls._s_ap != h(T_S_AP), "application traffic secrets differ from the trace")
    problems += problem_if(not tls.handshake_done or tls.group != 0x001D or tls.cipher != 0x1301 or tls.alpn is not None, "state done=%s group=%r cipher=%r alpn=%r" % (tls.handshake_done, tls.group, tls.cipher, tls.alpn))
    if upto == "handshake":
        return problems, ["SH -> no output; flight -> exactly the trace's 58-octet client Finished record"]
    problems += problem_if(tls.send_app(R8448_APP_PAYLOAD) != h(R8448_C_APP_REC), "the client application record differs from the trace")
    problems += problem_if(tls.feed(h(R8448_NST_REC)) != b"" or tls.read_app() != b"", "the NewSessionTicket was not dropped silently")
    tls.feed(h(R8448_S_APP_REC))
    problems += problem_if(tls.read_app() != R8448_APP_PAYLOAD, "the server application record did not decrypt to 00..31")
    if upto == "app":
        return problems, ["client record == the trace's 72 octets; NST dropped; the server's 72 octets decrypt to 50 bytes 00..31"]
    problems += problem_if(tls.close_notify() != h(R8448_C_ALERT_REC), "the client close_notify differs from the trace")
    tls.feed(h(R8448_S_ALERT_REC))
    problems += problem_if(not tls.closed, "the server close_notify did not close")
    return problems, ["client alert record == the trace's 24 octets; the server's closes the connection"]


def rfc8448_rows(suite, mod, cc):
    src = "RFC 8448 section 3 (simple 1-RTT handshake), records pasted above"
    run_row(suite, GC, "rfc8448-server-flight-decrypts-finished-verifies", lambda: rfc8448_flight_row(mod), src + "; keys T_S_HS_KEY/T_C_HS_KEY; Finished by hmac here")
    run_row(suite, GC, "rfc8448-engine-replays-the-handshake", lambda: rfc8448_replay_row(mod, cc, "handshake"), src)
    run_row(suite, GC, "rfc8448-application-data-and-ticket", lambda: rfc8448_replay_row(mod, cc, "app"), src)
    run_row(suite, GC, "rfc8448-close-notify-both-ways", lambda: rfc8448_replay_row(mod, cc, "close"), src)

    def bytewise():
        tls = rfc8448_engine(mod, cc)
        out = b""
        for b in h(R8448_SH_REC) + h(R8448_FLIGHT_REC):
            out += tls.feed(bytes([b]))
        return problem_if(out != h(R8448_CFIN_REC), "fed one byte at a time the output differs"), []
    run_row(suite, GC, "rfc8448-byte-at-a-time", bytewise, src)

    def ccs():
        tls = rfc8448_engine(mod, cc)
        problems = problem_if(tls.feed(h(R8448_SH_REC) + b"\x14\x03\x03\x00\x01\x01" + h(R8448_FLIGHT_REC)) != h(R8448_CFIN_REC), "a server CCS between SH and the flight was not dropped")
        tls = rfc8448_engine(mod, cc)
        problems += refusal(lambda: tls.feed(h(R8448_SH_REC) + b"\x14\x03\x03\x00\x01\x02"), mod.ChromeClientError, "tls: unexpected_message: ChangeCipherSpec record outside the handshake", tls)
        return problems, ["RFC 8446 appendix D.4: a CCS of 0x01 is dropped, any other body refused"]
    run_row(suite, GC, "rfc8448-server-ccs-dropped-or-refused", ccs, src)

    def tampered():
        cipher = PeerCipher(mod._ChAesGcm(h(T_S_HS_KEY)), h(T_S_HS_IV))
        ctype, content = cipher.open(h(R8448_FLIGHT_REC)[5:])
        bad = content[:-1] + bytes([content[-1] ^ 1])
        tls = rfc8448_engine(mod, cc)
        tls.feed(h(R8448_SH_REC))
        return refusal(lambda: tls.feed(PeerCipher(mod._ChAesGcm(h(T_S_HS_KEY)), h(T_S_HS_IV)).seal(ctype, bad)), mod.ChromeClientError, "tls: decrypt_error: server Finished does not verify", tls), ["one verify_data bit flipped, re-sealed under the trace's server handshake key"]
    run_row(suite, GC, "rfc8448-bad-server-finished-refused", tampered, src)

    def flipped():
        rec = h(R8448_FLIGHT_REC)
        tls = rfc8448_engine(mod, cc)
        tls.feed(h(R8448_SH_REC))
        return refusal(lambda: tls.feed(rec[:-1] + bytes([rec[-1] ^ 1])), mod.ChromeClientError, "tls: bad record MAC", tls), ["the flight record's last tag byte flipped"]
    run_row(suite, GC, "rfc8448-flipped-tag-refused", flipped, src)


def peer_rows(suite, mod):
    src = "scripted TLS 1.3 peer (key schedule + record layer by hmac here; X25519/P-256/ML-KEM and the AEADs are the module's, proven in group A)"
    for group, gname in ((0x001D, "x25519"), (0x11EC, "x25519mlkem768")):
        for s in TLS13_SUITES:
            def normal(group=group, s=s):
                peer = ScriptedPeer(mod, s, group)
                problems = peer.handshake()
                tls = peer.tls
                problems += problem_if(not tls.handshake_done or tls.group != group or tls.cipher != s or tls.alpn != "h2" or tls.alps_negotiated or tls.hrr, "state done=%s group=%r cipher=%r alpn=%r alps=%s hrr=%s" % (tls.handshake_done, tls.group, tls.cipher, tls.alpn, tls.alps_negotiated, tls.hrr))
                return problems + peer.exchange(), ["CCS + Finished verified over the peer's transcript; application data both ways; ALPN h2, no ALPS"]
            run_row(suite, GC, "peer-%s-%s" % (gname, suite_tag(s)), normal, src)

    for s in TLS13_SUITES:
        def alps(s=s):
            peer = ScriptedPeer(mod, s, 0x001D, alps=True)
            problems = peer.handshake()
            tls = peer.tls
            problems += problem_if(not tls.alps_negotiated or tls.alps_server_settings != h(SERVER_ALPS), "ALPS negotiated=%s settings=%s" % (tls.alps_negotiated, short(tls.alps_server_settings or b"")))
            return problems + peer.exchange(), ["client EE == %s (empty h2 ALPS value, Chromium 43502f9b), sealed before the Finished; the Finished covers it" % CLIENT_ALPS_EE]
        run_row(suite, GC, "peer-alps-client-ee-%s" % suite_tag(s), alps, src)

    def tls12():
        peer = ScriptedPeer(mod)
        problems = refusal(lambda: peer.tls.feed(hello_record(peer.sid, 0xC02F, [(65281, b"\x00")], os.urandom(32))), mod.ChromeTls12Error, "tls: server chose TLS 1.2", peer.tls)
        peer = ScriptedPeer(mod)
        problems += refusal(lambda: peer.tls.feed(hello_record(peer.sid, 0x1301, [(43, b"\x03\x03"), (51, (0x001D).to_bytes(2, "big") + vec(2, os.urandom(32)))], os.urandom(32))), mod.ChromeTls12Error, "tls: server chose TLS 1.2", peer.tls)
        return problems, ["a TLS 1.2 ServerHello without supported_versions, and one whose supported_versions says 0x0303"]
    run_row(suite, GC, "peer-tls12-serverhello", tls12, src)

    limit = mod.CH_MAX_RECORD_BYTES

    def record_at_limit():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        return refusal(lambda: peer.tls.feed(b"\x17\x03\x03" + limit.to_bytes(2, "big") + bytes(limit)), mod.ChromeClientError, "tls: bad record MAC", peer.tls), ["a %d-byte protected record passes the length gate and reaches the AEAD (a valid one is impossible: its inner plaintext would exceed 2^14 + 1)" % limit]
    run_row(suite, GC, "peer-record-%d-passes-the-length-gate" % limit, record_at_limit, src)

    def record_over():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        return refusal(lambda: peer.tls.feed(b"\x17\x03\x03" + (limit + 1).to_bytes(2, "big")), mod.ChromeClientError, "tls: record_overflow: record of %d bytes exceeds %d" % (limit + 1, limit), peer.tls), ["refused from the 5-byte header alone (RFC 8446 section 5.2: 2^14 + 256)"]
    run_row(suite, GC, "peer-record-%d-record-overflow" % (limit + 1), record_over, src)

    plain = mod.CH_MAX_PLAINTEXT_BYTES

    def max_plaintext():
        peer = connected(mod)
        data = bytes(i & 0xFF for i in range(plain))
        rec = peer.s_ap_w.seal(23, data)
        out = peer.tls.feed(rec)
        return problem_if(out != b"" or peer.tls.read_app() != data, "a full %d-byte record was not delivered" % plain), ["record of %d bytes carrying %d bytes of application data (RFC 8446 section 5.1: 2^14)" % (len(rec) - 5, plain)]
    run_row(suite, GC, "peer-plaintext-%d-accepted" % plain, max_plaintext, src)

    def inner_over():
        peer = connected(mod)
        return refusal(lambda: peer.tls.feed(peer.s_ap_w.seal(23, bytes(plain), pad=1)), mod.ChromeClientError, "tls: record_overflow: inner plaintext of %d bytes exceeds %d" % (plain + 2, plain + 1), peer.tls), ["content %d + type 1 + one padding byte (RFC 8446 section 5.4)" % plain]
    run_row(suite, GC, "peer-inner-plaintext-%d-refused" % (plain + 2), inner_over, src)

    ceiling = mod.CH_MAX_HANDSHAKE_BYTES

    def hs_at_ceiling():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        out = peer.tls.feed(peer.s_hs_w.seal(22, b"\x08" + ceiling.to_bytes(3, "big")))
        return problem_if(out != b"" or peer.tls.failed, "a %d-byte handshake header was refused" % ceiling), ["an EE header announcing exactly %d bytes waits for its body" % ceiling]
    run_row(suite, GC, "peer-handshake-message-%d-waits" % ceiling, hs_at_ceiling, src)

    def hs_over():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        return refusal(lambda: peer.tls.feed(peer.s_hs_w.seal(22, b"\x08" + (ceiling + 1).to_bytes(3, "big"))), mod.ChromeClientError, "tls: handshake message of %d bytes exceeds %d" % (ceiling + 1, ceiling), peer.tls), ["refused from the 4-byte header, before any body is buffered"]
    run_row(suite, GC, "peer-handshake-message-%d-refused" % (ceiling + 1), hs_over, src)

    def bad_finished():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        return refusal(lambda: peer.tls.feed(peer.s_hs_w.seal(22, peer.flight(bad_finished=True))), mod.ChromeClientError, "tls: decrypt_error: server Finished does not verify", peer.tls), ["one verify_data bit flipped by the peer"]
    run_row(suite, GC, "peer-bad-server-finished", bad_finished, src)

    def flipped_tag():
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        rec = peer.s_hs_w.seal(22, peer.flight())
        return refusal(lambda: peer.tls.feed(rec[:-1] + bytes([rec[-1] ^ 1])), mod.ChromeClientError, "tls: bad record MAC", peer.tls), ["the flight record's last tag byte flipped"]
    run_row(suite, GC, "peer-flipped-aead-tag", flipped_tag, src)

    def alerts():
        peer = ScriptedPeer(mod)
        problems = refusal(lambda: peer.tls.feed(b"\x15\x03\x03\x00\x02\x02\x46"), mod.ChromeClientError, "tls: protocol_version: alert received from the server", peer.tls)
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        problems += refusal(lambda: peer.tls.feed(peer.s_hs_w.seal(21, b"\x02\x28")), mod.ChromeClientError, "tls: handshake_failure: alert received from the server", peer.tls)
        return problems, ["plaintext protocol_version (70) before the ServerHello; encrypted handshake_failure (40) after it"]
    run_row(suite, GC, "peer-fatal-alert", alerts, src)

    for s in TLS13_SUITES:
        def key_update(s=s):
            peer = connected(mod, s)
            peer.tls.feed(peer.s_ap_w.seal(23, b"before"))
            peer.c_ap_r.open(split_records(peer.tls.send_app(b"c-before"))[0][1])
            ku = peer.s_ap_w.seal(22, hs_msg(24, b"\x01"))
            peer.rekey_server()
            out = peer.tls.feed(ku + peer.s_ap_w.seal(23, b"after"))
            recs = split_records(out)
            problems = problem_if(len(recs) != 1 or peer.c_ap_r.open(recs[0][1]) != (22, hs_msg(24, b"\x00")), "no KeyUpdate(update_not_requested) under the old client key: %r" % [(t, len(b)) for t, b in recs])
            problems += problem_if(peer.tls.read_app() != b"beforeafter", "the read side did not move to the next server secret")
            peer.rekey_client()
            problems += problem_if(peer.c_ap_r.open(split_records(peer.tls.send_app(b"c-after"))[0][1]) != (23, b"c-after"), "the write side did not move to the next client secret")
            return problems, ["KeyUpdate(update_requested) then data in ONE feed; answered under the old key, both sides rekeyed (\"traffic upd\", RFC 8446 section 7.2)"]
        run_row(suite, GC, "peer-keyupdate-requested-%s" % suite_tag(s), key_update, src)

    count = mod.CH_MAX_KEY_UPDATES

    def ku_bound():
        peer = connected(mod)
        data = b""
        for _ in range(count):
            data += peer.s_ap_w.seal(22, hs_msg(24, b"\x00"))
            peer.rekey_server()
        problems = problem_if(peer.tls.feed(data) != b"" or peer.tls.failed, "%d KeyUpdates in a row were not all accepted" % count)
        problems += refusal(lambda: peer.tls.feed(peer.s_ap_w.seal(22, hs_msg(24, b"\x00"))), mod.ChromeClientError, "tls: unexpected_message: more than %d KeyUpdates without application data" % count, peer.tls)
        return problems, ["CH_MAX_KEY_UPDATES = %d read off the module: %d accepted, number %d refused" % (count, count, count + 1)]
    run_row(suite, GC, "peer-keyupdate-bound-%d" % count, ku_bound, src)

    def nst():
        peer = connected(mod)
        ticket = hs_msg(4, (7200).to_bytes(4, "big") + b"\x00\x00\x00\x01" + vec(1, b"n") + vec(2, b"ticket") + vec(2, b""))
        out = peer.tls.feed(peer.s_ap_w.seal(22, ticket) + peer.s_ap_w.seal(23, b"data"))
        return problem_if(out != b"" or peer.tls.read_app() != b"data", "the NewSessionTicket was not dropped silently"), ["resumption is out of scope: the ticket is read and dropped"]
    run_row(suite, GC, "peer-new-session-ticket-dropped", nst, src)

    def plaintext_alert():
        unprotected = "tls: unexpected_message: unprotected alert after the key change"
        peer = ScriptedPeer(mod)
        peer.tls.feed(peer.server_hello())
        problems = refusal(lambda: peer.tls.feed(b"\x15\x03\x03\x00\x02\x02\x28"), mod.ChromeClientError, unprotected, peer.tls)
        peer = connected(mod)
        problems += refusal(lambda: peer.tls.feed(b"\x15\x03\x03\x00\x02\x01\x00"), mod.ChromeClientError, unprotected, peer.tls)
        problems += problem_if(peer.tls.closed, "an unprotected close_notify closed the connection")
        return problems, ["a plaintext handshake_failure after the ServerHello, a plaintext close_notify after the handshake: both refused (RFC 8446 section 5: alerts after the key change are protected)"]
    run_row(suite, GC, "peer-unprotected-alert-after-the-key-change-refused", plaintext_alert, src)

    empty_max = mod._ChTls.MAX_EMPTY_RECORDS
    empty_message = "tls: unexpected_message: too many empty records (more than %d without application data)" % empty_max
    ticket = hs_msg(4, (7200).to_bytes(4, "big") + b"\x00\x00\x00\x01" + vec(1, b"n") + vec(2, b"ticket") + vec(2, b""))
    no_progress = (
        ("empty-application-data", lambda peer: peer.s_ap_w.seal(23, b"")),
        ("user-canceled-alert", lambda peer: peer.s_ap_w.seal(21, b"\x01\x5a")),
        ("new-session-ticket", lambda peer: peer.s_ap_w.seal(22, ticket)),
    )
    for cid, unit in no_progress:
        def empty_row(unit=unit):
            peer = connected(mod)
            peer.tls.feed(peer.s_ap_w.seal(23, b"reset"))
            data = b"".join(unit(peer) for _ in range(empty_max))
            problems = problem_if(peer.tls.feed(data) != b"" or peer.tls.failed, "%d in a row were not all accepted" % empty_max)
            problems += refusal(lambda: peer.tls.feed(unit(peer)), mod.ChromeClientError, empty_message, peer.tls)
            return problems, ["MAX_EMPTY_RECORDS = %d read off _ChTls (BoringSSL kMaxEmptyRecords): %d accepted, number %d refused" % (empty_max, empty_max, empty_max + 1)]
        run_row(suite, GC, "peer-no-progress-%s-%d-then-one-refused" % (cid, empty_max), empty_row, src)

    def empty_reset():
        peer = connected(mod)
        half = empty_max // 2
        data = peer.s_ap_w.seal(23, b"a")
        for i in range(empty_max):
            data += peer.s_ap_w.seal(22, ticket) if i < half else peer.s_ap_w.seal(21, b"\x01\x5a")
        data += peer.s_ap_w.seal(23, b"b")
        for _ in range(empty_max):
            data += peer.s_ap_w.seal(23, b"")
        out = outcome(lambda: peer.tls.feed(data))
        problems = expect_value(out, b"") + problem_if(peer.tls.read_app() != b"ab", "the application data was not delivered")
        return problems + problem_if(empty_max != 32, "MAX_EMPTY_RECORDS is %d, BoringSSL's kMaxEmptyRecords is 32" % empty_max), ["%d mixed tickets and user_canceled, data, %d empty records: application data resets the run" % (empty_max, empty_max)]
    run_row(suite, GC, "peer-no-progress-run-reset-by-application-data", empty_reset, src)

    def unknown_post():
        problems = []
        for msg_type in (13, 99):
            peer = connected(mod)
            problems += refusal(lambda: peer.tls.feed(peer.s_ap_w.seal(22, hs_msg(msg_type, b"\x00\x00\x00"))), mod.ChromeClientError, "tls: unexpected_message: post-handshake message type %d" % msg_type, peer.tls)
        return problems, ["CertificateRequest (13) and an unassigned type 99 after the handshake"]
    run_row(suite, GC, "peer-unknown-post-handshake-type", unknown_post, src)

    for group, gname, pre_hrr in ((0x001D, "x25519", False), (0x11EC, "x25519mlkem768", False), (0x0017, "p256-after-hrr", True)):
        def short_share(group=group, pre_hrr=pre_hrr):
            peer = ScriptedPeer(mod, 0x1301, 0x001D if pre_hrr else group)
            if pre_hrr:
                peer.send_hrr(group)
            want = len(peer.server_share()[0])
            return refusal(lambda: peer.tls.feed(peer.server_hello(share=lambda pub: pub[:-1])), mod.ChromeClientError, "tls: illegal_parameter: server key share for group 0x%04x is %d bytes, expected %d" % (group, want - 1, want), peer.tls), ["a server key share of %d bytes, one short" % (want - 1)]
        run_row(suite, GC, "peer-short-key-share-%s" % gname, short_share, src)

    def sequence():
        top = 2 ** 64 - 1
        cipher = mod._ChRecordCipher(mod._ChAesGcm(bytes(16)), bytes(12), top - 1)
        problems = expect_value(outcome(lambda: len(cipher.seal(23, b"x"))), 5 + 1 + 1 + 16)
        problems += expect_refusal(outcome(lambda: cipher.seal(23, b"x")), mod.ChromeClientError, "tls: record sequence number exhausted")
        opener = mod._ChRecordCipher(mod._ChAesGcm(bytes(16)), bytes(12), top)
        problems += expect_refusal(outcome(lambda: opener.open(23, bytes(32))), mod.ChromeClientError, "tls: record sequence number exhausted")
        peer = connected(mod)
        peer.tls._read.seq = top
        peer.s_ap_w.seq = top
        problems += refusal(lambda: peer.tls.feed(peer.s_ap_w.seal(23, b"late")), mod.ChromeClientError, "tls: record sequence number exhausted", peer.tls)
        return problems, ["RFC 8446 section 5.3: no wrap; seq 2^64-2 seals, 2^64-1 is refused on seal, open and inside the engine (its read cipher preset)"]
    run_row(suite, GC, "record-sequence-2^64-1-refused", sequence, src)


def group_tls(suite, mod, cc, openssl):
    """Group C: real TLS stacks, the published trace, the scripted peer, and the openssl matrix."""
    for s in TLS13_SUITES:
        run_row(suite, GC, "memorybio-ssl-server-%s" % suite_tag(s), lambda: memorybio_row(mod, s), "Python ssl.SSLObject over ssl.MemoryBIO, TLS 1.3 only, ALPN h2, tests/files/tls leaf; the client offers only this suite")

    def full():
        _tls, problems, detail = loopback(mod)
        return problems, detail
    run_row(suite, GC, "loopback-ssl-server-full-profile", full, "a Python ssl server thread on 127.0.0.1, TLS 1.3 only, ALPN h2; 40000 bytes each way (3 records), close_notify both ways")

    rfc8448_rows(suite, mod, cc)
    peer_rows(suite, mod)

    found, why = openssl
    ok = found is not None and found[1] >= (3, 5)
    reason = why if found is None else "%s is OpenSSL %d.%d; the X25519MLKEM768 matrix needs >= 3.5" % (found[0], found[1][0], found[1][1])
    matrix = [(g, gid, s, False) for g, gid in (("X25519MLKEM768", 0x11EC), ("X25519", 0x001D)) for s in TLS13_SUITES]
    matrix.append(("P-256", 0x0017, 0x1301, True))
    for gname, gid, s, want_hrr in matrix:
        cid = "openssl-s_server-%s-%s" % (gname, suite_tag(s))
        if not ok:
            skip(suite, GC, cid, reason)
            continue
        run_row(suite, GC, cid, lambda: s_server_row(mod, found[0], gname, s, gid, want_hrr),
                "%s s_server -tls1_3 -groups %s -ciphersuites %s -alpn h2 -www%s" % (found[0], gname, suite_tag(s), " (P-256 was offered without a share: HRR)" if want_hrr else ""))


# --- H. HelloRetryRequest --------------------------------------------------------------

def group_hrr(suite, mod, cc, facts, ws):
    src = "scripted TLS 1.3 peer; RFC 8446 section 4.1.4"
    for s in TLS13_SUITES:
        def complete(s=s):
            peer = ScriptedPeer(mod, s)
            peer.send_hrr(0x0017)
            problems = peer.handshake()
            tls = peer.tls
            problems += problem_if(not tls.hrr or tls.group != 0x0017 or tls.cipher != s, "state hrr=%s group=%r cipher=%r" % (tls.hrr, tls.group, tls.cipher))
            return problems + peer.exchange(), ["HRR(0x0017) -> CCS + CH2; P-256 ServerHello; the Finished verifies over message_hash(Hash(CH1)) || HRR || CH2 || ..."]
        run_row(suite, GH, "hrr-p256-completes-%s" % suite_tag(s), complete, src)

    def wire():
        cookie = b"\x01" * 28
        peer = ScriptedPeer(mod)
        out = peer.send_hrr(0x0017, cookie)
        recs = split_records(out)
        ch1, ch2 = peer.ch1, peer.ch2
        _sid1, _shares1, exts1 = peer_parse_ch(ch1)
        _sid2, shares2, exts2 = peer_parse_ch(ch2)
        problems = problem_if(len(recs) != 2 or recs[0] != (20, b"\x01") or recs[1][0] != 22, "records %r, wanted CCS then ONE handshake record" % [(t, len(b)) for t, b in recs])
        problems += problem_if(out[6:9] != b"\x16\x03\x03", "the CH2 record version is %s, the captures show 0303" % out[7:9].hex())
        problems += problem_if(ch1[4:hello_prefix_len(ch1)] != ch2[4:hello_prefix_len(ch2)], "random, session id, cipher list or compression differ from CH1")
        problems += problem_if(sorted(shares2) != [0x0017] or len(shares2.get(0x0017, b"")) != 65, "CH2 key_share %r" % dict((g, len(k)) for g, k in shares2.items()))
        problems += problem_if(dict(exts2).get(44) != vec(2, cookie), "the cookie is not echoed byte for byte")
        problems += problem_if([t for t, _b in exts2 if t != 44] != [t for t, _b in exts1], "the extension order differs from CH1's")
        problems += problem_if([(t, b) for t, b in exts2 if t not in (44, 51)] != [(t, b) for t, b in exts1 if t != 51], "an extension other than key_share / cookie changed")
        delta = facts["ch2_delta"]
        problems += problem_if(delta is None or len(ch2) != len(ch1) - delta[0] + delta[1] + len(cookie), "CH2 is %d bytes; README: CH1 (%d) - %r + %r + cookie (%d)" % (len(ch2), len(ch1), delta and delta[0], delta and delta[1], len(cookie)))
        return problems, ["tests/files/chrome/154/README.md set 9: CCS first, same random/session id/ciphers/GREASE/order, one 65-byte P-256 share, cookie echoed; length rule %r" % (delta,)]
    run_row(suite, GH, "hrr-ch2-on-the-wire", wire, src)

    def against_set_9():
        ref = os.path.join(FIXTURES, "hrr")
        cookies = []
        for name in sorted(n for n in os.listdir(ref) if n.endswith(".json")):
            with open(os.path.join(ref, name), encoding="utf-8") as fh:
                cookies.append(bytes.fromhex(json.load(fh)["hrr"]["cookie_hex"]))
        records, ja4s = [], set()
        for seed in range(CH_SEEDS):
            rand = seeded_rand(1000 + seed)
            rec1, _hs1, meta1 = mod._ch_client_hello(CAPTURE_HOST, {0x11EC: rand(1216), 0x001D: rand(32)}, None, rand)
            hrr = {"group": 0x0017, "cookie": cookies[seed % len(cookies)], "ch1_meta": meta1}
            rec2, hs2, _meta2 = mod._ch_client_hello(CAPTURE_HOST, {0x0017: b"\x04" + rand(64)}, None, rand, hrr)
            ja4s.add(cc.fingerprints(cc.parse_client_hello(hs2[4:]))["ja4"])
            records.append({"client_hello_hex": rec1.hex(), "hrr": {"client_hello_2_hex": rec2.hex()}})
        problems, detail = diff_problems(cc, ref, write_candidates(ws, "hrr", records))
        problems += problem_if(sorted(ja4s) != [facts["ja4_ch2"]], "CH2 JA4 %r, the README says %r" % (sorted(ja4s), facts["ja4_ch2"]))
        return problems, detail + ["CH2 JA4 %r; %d fixture cookies reused" % (sorted(ja4s), len(cookies))]
    run_row(suite, GH, "hrr-ch2-diff-vs-set-9", against_set_9, "chrome_capture diff tests/files/chrome/154/hrr/ (CH1 and CH2 features) vs 20 built CH1/CH2 pairs")

    def refused(group, message, extra=None):
        def fn():
            peer = ScriptedPeer(mod)
            g = group if group is not None else dict(peer.exts)[10][2:4]
            g = g if isinstance(g, int) else int.from_bytes(g, "big")
            text = message % g if "%" in message else message
            problems = refusal(lambda: peer.tls.feed(peer.hrr_record(g)), mod.ChromeClientError, text, peer.tls)
            return problems, ["HRR selecting 0x%04x" % g + (extra or "")]
        return fn
    run_row(suite, GH, "hrr-secp384r1-flag-2", refused(0x0018, "tls: HelloRetryRequest selected secp384r1 (not implemented)"), src + "; P-256 only (FLAG-2)")

    def p384_warning():
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger = logging.getLogger("chrome-client")
        propagate = logger.propagate
        logger.addHandler(handler)
        logger.propagate = False
        try:
            peer = ScriptedPeer(mod)
            problems = refusal(lambda: peer.tls.feed(peer.hrr_record(0x0018)), mod.ChromeClientError, "tls: HelloRetryRequest selected secp384r1 (not implemented)", peer.tls)
        finally:
            logger.removeHandler(handler)
            logger.propagate = propagate
        warnings = [r for r in records if r.levelno == logging.WARNING]
        text = warnings[0].getMessage() if len(warnings) == 1 else ""
        problems += problem_if(len(warnings) != 1, "%d WARNING record(s) on 'chrome-client', wanted 1" % len(warnings))
        problems += problem_if(not all(s in text for s in ("secp384r1", "P-384", "R-0048", peer.tls._host)), "warning %r does not name secp384r1, P-384, R-0048 and the host" % text)
        return problems, ["WARNING %r, then the unchanged refusal" % text]
    run_row(suite, GH, "hrr-secp384r1-logs-a-warning-naming-r-0048", p384_warning, src + "; roadmap R-0048: a real P-384 HRR must be noticed")
    run_row(suite, GH, "hrr-already-shared-x25519", refused(0x001D, "tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we already sent a key share for"), src)
    run_row(suite, GH, "hrr-already-shared-x25519mlkem768", refused(0x11EC, "tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we already sent a key share for"), src)
    run_row(suite, GH, "hrr-not-offered-0x0019", refused(0x0019, "tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we did not offer"), src)
    run_row(suite, GH, "hrr-grease-group", refused(None, "tls: illegal_parameter: HelloRetryRequest selected group 0x%04x we did not offer", ", the GREASE value CH1's supported_groups carried"), src)

    def cipher_changes():
        peer = ScriptedPeer(mod, 0x1301)
        peer.send_hrr(0x0017)
        return refusal(lambda: peer.tls.feed(peer.server_hello(cipher=0x1302)), mod.ChromeClientError, "tls: illegal_parameter: ServerHello cipher 0x1302 differs from the HelloRetryRequest's 0x1301", peer.tls), ["HRR with 0x1301, then a ServerHello with 0x1302"]
    run_row(suite, GH, "hrr-then-serverhello-cipher-differs", cipher_changes, src)

    def second():
        peer = ScriptedPeer(mod)
        peer.send_hrr(0x0017)
        return refusal(lambda: peer.tls.feed(peer.hrr_record(0x0017)), mod.ChromeClientError, "tls: second HelloRetryRequest", peer.tls), ["a second HRR after CH2"]
    run_row(suite, GH, "hrr-second-refused", second, src)

    def p256_server():
        tls, problems, detail = loopback(mod, "prime256v1")
        if problems:
            return problems, detail
        if not tls.hrr or tls.group != 0x0017:
            return None, detail + ["set_ecdh_curve('prime256v1') did not restrict the TLS 1.3 groups here (group 0x%04x, no HRR); the openssl s_server P-256 row covers the HRR" % (tls.group or 0)]
        return problems, detail
    cid = "hrr-loopback-ssl-server-p256-only"
    src = "a Python ssl server restricted with set_ecdh_curve('prime256v1'); INFO when that does not restrict TLS 1.3 groups (plan: UNVERIFIED)"
    got = outcome(p256_server)
    if got[0] == "exc":
        suite.record(GH, cid, ["raised %s(%r)" % (type(got[1]).__name__, str(got[1])[:200])], detail=[src])
    elif got[1][0] is None:
        suite.record(GH, cid, (), status=H.INFO, detail=[src] + got[1][1], brief="INFO | %s (no HRR from this ssl build)" % cid)
    else:
        suite.record(GH, cid, got[1][0], detail=[src] + got[1][1])


# --- D. HPACK, header profiles, caller-header rules ---------------------------------------

# The seven h2 capture sets (tests/files/chrome/154/README.md "Layout"). Every
# connection in them has an h2 layer; its HEADERS blocks are group D's oracle
# and its client frames group E's.
H2_SETS = ("navigate", "navigate-reload", "cors-post", "cors-get", "cors-head", "ip-literal", "cookie")

# The cors sets and whether the page's fetch() set an Accept (cors-head/ did
# not, so Chrome's default "*/*" rides in cors_order_default_accept). The fetch
# is the ONE stream whose captured sec-fetch-mode is "cors" (fixture_cors_stream;
# stream 5 in Chrome 154, stream 7 in 153, which also requested
# /.well-known/appspecific/com.chrome.devtools.json); stream 1 is every set's
# navigation; the other streams are no-cors subresources (/favicon.ico) the
# profile never generates, so only their bytes are re-encoded.
CORS_SETS = {"cors-get": True, "cors-post": True, "cors-head": False}

# RFC 7541 Appendix B, "code as hex aligned to LSB" / "len in bits" for the
# symbols 0..256 in order (EOS last). Copied out of the RFC text by a script,
# which also checked every row's "code as bits aligned to MSB" column against
# its hex before writing it here; never typed.
RFC7541_HUFFMAN_B = (
    '1ff8/13 7fffd8/23 fffffe2/28 fffffe3/28 fffffe4/28 fffffe5/28 fffffe6/28 fffffe7/28 fffffe8/28 ffffea/24 3ffffffc/30 fffffe9/28 fffffea/28 3ffffffd/30 fffffeb/28 fffffec/28 '
    'fffffed/28 fffffee/28 fffffef/28 ffffff0/28 ffffff1/28 ffffff2/28 3ffffffe/30 ffffff3/28 ffffff4/28 ffffff5/28 ffffff6/28 ffffff7/28 ffffff8/28 ffffff9/28 ffffffa/28 ffffffb/28 '
    '14/6 3f8/10 3f9/10 ffa/12 1ff9/13 15/6 f8/8 7fa/11 3fa/10 3fb/10 f9/8 7fb/11 fa/8 16/6 17/6 18/6 '
    '0/5 1/5 2/5 19/6 1a/6 1b/6 1c/6 1d/6 1e/6 1f/6 5c/7 fb/8 7ffc/15 20/6 ffb/12 3fc/10 '
    '1ffa/13 21/6 5d/7 5e/7 5f/7 60/7 61/7 62/7 63/7 64/7 65/7 66/7 67/7 68/7 69/7 6a/7 '
    '6b/7 6c/7 6d/7 6e/7 6f/7 70/7 71/7 72/7 fc/8 73/7 fd/8 1ffb/13 7fff0/19 1ffc/13 3ffc/14 22/6 '
    '7ffd/15 3/5 23/6 4/5 24/6 5/5 25/6 26/6 27/6 6/5 74/7 75/7 28/6 29/6 2a/6 7/5 '
    '2b/6 76/7 2c/6 8/5 9/5 2d/6 77/7 78/7 79/7 7a/7 7b/7 7ffe/15 7fc/11 3ffd/14 1ffd/13 ffffffc/28 '
    'fffe6/20 3fffd2/22 fffe7/20 fffe8/20 3fffd3/22 3fffd4/22 3fffd5/22 7fffd9/23 3fffd6/22 7fffda/23 7fffdb/23 7fffdc/23 7fffdd/23 7fffde/23 ffffeb/24 7fffdf/23 '
    'ffffec/24 ffffed/24 3fffd7/22 7fffe0/23 ffffee/24 7fffe1/23 7fffe2/23 7fffe3/23 7fffe4/23 1fffdc/21 3fffd8/22 7fffe5/23 3fffd9/22 7fffe6/23 7fffe7/23 ffffef/24 '
    '3fffda/22 1fffdd/21 fffe9/20 3fffdb/22 3fffdc/22 7fffe8/23 7fffe9/23 1fffde/21 7fffea/23 3fffdd/22 3fffde/22 fffff0/24 1fffdf/21 3fffdf/22 7fffeb/23 7fffec/23 '
    '1fffe0/21 1fffe1/21 3fffe0/22 1fffe2/21 7fffed/23 3fffe1/22 7fffee/23 7fffef/23 fffea/20 3fffe2/22 3fffe3/22 3fffe4/22 7ffff0/23 3fffe5/22 3fffe6/22 7ffff1/23 '
    '3ffffe0/26 3ffffe1/26 fffeb/20 7fff1/19 3fffe7/22 7ffff2/23 3fffe8/22 1ffffec/25 3ffffe2/26 3ffffe3/26 3ffffe4/26 7ffffde/27 7ffffdf/27 3ffffe5/26 fffff1/24 1ffffed/25 '
    '7fff2/19 1fffe3/21 3ffffe6/26 7ffffe0/27 7ffffe1/27 3ffffe7/26 7ffffe2/27 fffff2/24 1fffe4/21 1fffe5/21 3ffffe8/26 3ffffe9/26 ffffffd/28 7ffffe3/27 7ffffe4/27 7ffffe5/27 '
    'fffec/20 fffff3/24 fffed/20 1fffe6/21 3fffe9/22 1fffe7/21 1fffe8/21 7ffff3/23 3fffea/22 3fffeb/22 1ffffee/25 1ffffef/25 fffff4/24 fffff5/24 3ffffea/26 7ffff4/23 '
    '3ffffeb/26 7ffffe6/27 3ffffec/26 3ffffed/26 7ffffe7/27 7ffffe8/27 7ffffe9/27 7ffffea/27 7ffffeb/27 ffffffe/28 7ffffec/27 7ffffed/27 7ffffee/27 7ffffef/27 7fffff0/27 3ffffee/26 '
    '3fffffff/30'
)

# RFC 7541 Appendix C.2-C.6: (section, its "Hex dump of encoded data", its
# "Decoded header list", its dynamic "Table size" after decoding -- 0 where the
# RFC prints "Dynamic table (after decoding): empty."). Copied out of the RFC
# text by the same script, never typed. C.2.x are independent blocks; each of
# C.3-C.6 is three consecutive blocks on ONE connection; C.3/C.5 carry no
# Huffman, C.4/C.6 do; C.5/C.6 run with a 256-octet table (the C.5 preamble).
RFC7541_EXAMPLES = (
    ('C.2.1', '400a637573746f6d2d6b65790d637573746f6d2d686561646572',
     (('custom-key', 'custom-header'),), 55),
    ('C.2.2', '040c2f73616d706c652f70617468',
     ((':path', '/sample/path'),), 0),
    ('C.2.3', '100870617373776f726406736563726574',
     (('password', 'secret'),), 0),
    ('C.2.4', '82',
     ((':method', 'GET'),), 0),
    ('C.3.1', '828684410f7777772e6578616d706c652e636f6d',
     ((':method', 'GET'), (':scheme', 'http'), (':path', '/'), (':authority', 'www.example.com')), 57),
    ('C.3.2', '828684be58086e6f2d6361636865',
     ((':method', 'GET'), (':scheme', 'http'), (':path', '/'), (':authority', 'www.example.com'), ('cache-control', 'no-cache')), 110),
    ('C.3.3', '828785bf400a637573746f6d2d6b65790c637573746f6d2d76616c7565',
     ((':method', 'GET'), (':scheme', 'https'), (':path', '/index.html'), (':authority', 'www.example.com'), ('custom-key', 'custom-value')), 164),
    ('C.4.1', '828684418cf1e3c2e5f23a6ba0ab90f4ff',
     ((':method', 'GET'), (':scheme', 'http'), (':path', '/'), (':authority', 'www.example.com')), 57),
    ('C.4.2', '828684be5886a8eb10649cbf',
     ((':method', 'GET'), (':scheme', 'http'), (':path', '/'), (':authority', 'www.example.com'), ('cache-control', 'no-cache')), 110),
    ('C.4.3', '828785bf408825a849e95ba97d7f8925a849e95bb8e8b4bf',
     ((':method', 'GET'), (':scheme', 'https'), (':path', '/index.html'), (':authority', 'www.example.com'), ('custom-key', 'custom-value')), 164),
    ('C.5.1', '4803333032580770726976617465611d4d6f6e2c203231204f637420323031332032303a31333a323120474d546e1768747470733a2f2f7777772e6578616d706c652e636f6d',
     ((':status', '302'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:21 GMT'), ('location', 'https://www.example.com')), 222),
    ('C.5.2', '4803333037c1c0bf',
     ((':status', '307'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:21 GMT'), ('location', 'https://www.example.com')), 222),
    ('C.5.3', '88c1611d4d6f6e2c203231204f637420323031332032303a31333a323220474d54c05a04677a69707738666f6f3d4153444a4b48514b425a584f5157454f50495541585157454f49553b206d61782d6167653d333630303b2076657273696f6e3d31',
     ((':status', '200'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:22 GMT'), ('location', 'https://www.example.com'), ('content-encoding', 'gzip'), ('set-cookie', 'foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1')), 215),
    ('C.6.1', '488264025885aec3771a4b6196d07abe941054d444a8200595040b8166e082a62d1bff6e919d29ad171863c78f0b97c8e9ae82ae43d3',
     ((':status', '302'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:21 GMT'), ('location', 'https://www.example.com')), 222),
    ('C.6.2', '4883640effc1c0bf',
     ((':status', '307'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:21 GMT'), ('location', 'https://www.example.com')), 222),
    ('C.6.3', '88c16196d07abe941054d444a8200595040b8166e084a62d1bffc05a839bd9ab77ad94e7821dd7f2e6c7b335dfdfcd5b3960d5af27087f3672c1ab270fb5291f9587316065c003ed4ee5b1063d5007',
     ((':status', '200'), ('cache-control', 'private'), ('date', 'Mon, 21 Oct 2013 20:13:22 GMT'), ('location', 'https://www.example.com'), ('content-encoding', 'gzip'), ('set-cookie', 'foo=ASDJKHQKBZXOQWEOPIUAXQWEOIU; max-age=3600; version=1')), 215),
)

# RFC 7541 section 2.3.3 / Appendix A: 61 static entries, so 62 is the first
# dynamic index. Section 4.2 / RFC 9113 section 6.5.2: 4096 octets is the
# table size before any SETTINGS_HEADER_TABLE_SIZE.
HPACK_STATIC_ENTRIES = 61
HPACK_DEFAULT_TABLE = 4096

# _ch_check_caller_headers refusals: (row id, name, value, the exact one-line
# message as feature plan Step 7 specifies it). Rules 1-3 show the name as
# repr(name)[:40]; rules 4, 5 and the latin-1 rule the lowercased token.
CALLER_REFUSED = (
    ("fingerprint-pseudo-path", ":path", "/x", "headers: ':path' is fixed by the Chrome profile"),
    ("fingerprint-user-agent", "User-Agent", "x", "headers: user-agent is fixed by the Chrome profile"),
    ("fingerprint-sec-ch-ua-platform", "sec-ch-ua-platform", "x", "headers: sec-ch-ua-platform is fixed by the Chrome profile"),
    ("fingerprint-accept-encoding", "Accept-Encoding", "x", "headers: accept-encoding is fixed by the Chrome profile"),
    ("fingerprint-sec-fetch-mode", "Sec-Fetch-Mode", "x", "headers: sec-fetch-mode is fixed by the Chrome profile"),
    ("fingerprint-priority", "priority", "x", "headers: priority is fixed by the Chrome profile"),
    ("invalid-name-with-space", "X A", "1", "headers: invalid header name 'X A'"),
    ("invalid-name-empty", "", "1", "headers: invalid header name ''"),
    ("invalid-name-with-colon", "x:y", "1", "headers: invalid header name 'x:y'"),
    ("invalid-name-non-ascii", "\u00e9t", "1", "headers: invalid header name '\u00e9t'"),
    ("crlf-in-name", "X-A\r\nX-B", "1", "headers: 'X-A\\r\\nX-B' contains CR, LF or NUL"),
    ("crlf-injection-in-value", "X", "v\r\nX-Injected: 1", "headers: 'X' contains CR, LF or NUL"),
    ("lf-in-value", "X", "v\nx", "headers: 'X' contains CR, LF or NUL"),
    ("nul-in-value", "X", "v\x00", "headers: 'X' contains CR, LF or NUL"),
    ("framing-host", "Host", "h", "headers: host is connection framing, owned by the Chrome profile"),
    ("framing-content-length", "Content-Length", "1", "headers: content-length is connection framing, owned by the Chrome profile"),
    ("framing-transfer-encoding", "Transfer-Encoding", "chunked", "headers: transfer-encoding is connection framing, owned by the Chrome profile"),
    ("framing-connection", "Connection", "close", "headers: connection is connection framing, owned by the Chrome profile"),
    ("framing-te", "TE", "trailers", "headers: te is connection framing, owned by the Chrome profile"),
    ("framing-upgrade", "Upgrade", "h2c", "headers: upgrade is connection framing, owned by the Chrome profile"),
    ("framing-keep-alive", "Keep-Alive", "300", "headers: keep-alive is connection framing, owned by the Chrome profile"),
    ("framing-proxy-authorization", "Proxy-Authorization", "p", "headers: proxy-authorization is connection framing, owned by the Chrome profile"),
    ("value-not-latin-1", "X", "\u0151", "headers: x value is not latin-1"),
    # F27: every CTL but HTAB in a value, and edge SP/HTAB (RFC 9110 section 5.5); a CTL in a name fails rule 3.
    ("vt-in-value", "X", "v\x0bx", "headers: 'X' value contains a control character"),
    ("esc-in-value", "X", "\x1b[31mred", "headers: 'X' value contains a control character"),
    ("del-in-value", "X", "v\x7f", "headers: 'X' value contains a control character"),
    ("leading-space-in-value", "X", " v", "headers: 'X' value has leading or trailing whitespace"),
    ("trailing-htab-in-value", "X", "v\t", "headers: 'X' value has leading or trailing whitespace"),
    ("esc-in-name", "X\x1bA", "1", "headers: invalid header name 'X\\x1bA'"),
)

# Caller headers that must pass untouched: (name, value). An HTAB inside a value is legal (RFC 9110 section 5.5).
CALLER_ACCEPTED = (("Accept", "text/html"), ("Authorization", "Bearer t"), ("If-None-Match", "\"e1\""), ("Cookie", "a=1"), ("X-Inner-Tab", "a\tb"))

# Pseudo-header echo (M-1): names refused by rule 2 whose message must stay one
# short line. "headers: " + a 40-character repr + " is fixed by the Chrome
# profile" is 80 characters; 96 leaves slack and excludes a 10 KB echo.
PSEUDO_ECHO = (("vertical-tab", ":a\x0bb"), ("10240-byte-name", ":" + "a" * 10240))
PSEUDO_ECHO_MAX = 96


def huffman_b():
    """RFC7541_HUFFMAN_B as 257 (code, bit length) pairs in symbol order, EOS last."""
    items = "".join(RFC7541_HUFFMAN_B).split()
    return tuple((int(code, 16), int(bits)) for code, bits in (item.split("/") for item in items))


def huffman_bits(table, data):
    """The Appendix B code of every octet of `data` as one string of '0'/'1', MSB first."""
    return "".join(format(table[s][0], "0%db" % table[s][1]) for s in data)


def pack_bits(bits):
    """A '0'/'1' string whose length is a multiple of 8 -> bytes, MSB first."""
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def hp_int(value, prefix, flags=0):
    """An RFC 7541 section 5.1 integer in an N-bit prefix, written from the RFC's pseudo-code."""
    limit = (1 << prefix) - 1
    if value < limit:
        return bytes([flags | value])
    out = bytearray([flags | limit])
    value -= limit
    while value >= 128:
        out.append(value % 128 + 128)
        value //= 128
    out.append(value)
    return bytes(out)


def hp_string(raw):
    """An RFC 7541 section 5.2 string literal without Huffman (H = 0)."""
    return hp_int(len(raw), 7) + raw


def hp_literal(name, value, flags=0x00, prefix=4):
    """RFC 7541 6.2.2 literal without indexing (default) or 6.2.1 with incremental indexing (0x40, 6): a new name, raw strings."""
    return hp_int(0, prefix, flags) + hp_string(name.encode("latin-1")) + hp_string(value.encode("latin-1"))


def hp_block(pairs):
    """A header block of literals without indexing: no table and no Huffman, so it shares nothing with the module's encoder."""
    return b"".join(hp_literal(n, v) for n, v in pairs)


def hp_huffman_value(bits):
    """One literal-without-indexing field, name "x" raw, whose value is the Huffman bit string `bits` (a multiple of 8)."""
    coded = pack_bits(bits)
    return hp_int(0, 4) + hp_string(b"x") + hp_int(len(coded), 7, 0x80) + coded


def h2_fixtures(label):
    """[(file name, h2 record)] of every committed capture in tests/files/chrome/154/<label>/ with an h2 layer."""
    ref = os.path.join(FIXTURES, label)
    out = []
    for name in sorted(n for n in os.listdir(ref) if n.endswith(".json")):
        with open(os.path.join(ref, name), encoding="utf-8") as fh:
            h2 = json.load(fh).get("h2")
        if h2 and h2.get("streams"):
            out.append((name, h2))
    return out


def fixture_streams(h2):
    """The capture's streams in id order: the order their HEADERS left the client, so one encoder replays them."""
    return sorted(h2["streams"], key=lambda s: s["stream_id"])


def fixture_pairs(stream):
    return [(x["name"], x["value"]) for x in stream["headers"]]


def is_cors_stream(stream):
    """True for the page's fetch(): the stream whose captured sec-fetch-mode is "cors" (read from the fixture, never a typed stream id)."""
    return dict(fixture_pairs(stream)).get("sec-fetch-mode") == "cors"


def cc_pairs(decoder, block):
    """chrome_capture's independent decode of one block as (name, value) pairs; size updates dropped."""
    return [(e["name"], e["value"]) for e in decoder.decode(block) if "name" in e]


def first_difference(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return min(len(a), len(b))


def profile_for(mod, label, stream):
    """_ch_profile_headers called with what the page asked for, or None for a stream the profile does not generate."""
    sid = stream["stream_id"]
    d = dict(fixture_pairs(stream))
    extra = []
    if sid == 1:
        if "cache-control" in d:
            extra.append(("Cache-Control", d["cache-control"]))
        return mod._ch_profile_headers("navigate", d[":method"], d[":authority"], d[":path"], cookie=d.get("cookie"), extra=extra)
    if label in CORS_SETS and is_cors_stream(stream):
        if CORS_SETS[label]:
            extra.append(("Accept", d["accept"]))
        length = int(d["content-length"]) if "content-length" in d else None
        return mod._ch_profile_headers("cors", d[":method"], d[":authority"], d[":path"], referer=d["referer"], extra=extra, content_length=length)
    return None


def profile_problems(mod, label):
    """(problems, lists compared): every generated list equals the fixture's decoded list, and re-encodes byte-identical in place."""
    problems, n = [], 0
    for name, h2 in h2_fixtures(label):
        enc = mod._ChHpackEncoder()
        if label in CORS_SETS and sum(1 for st in h2["streams"] if is_cors_stream(st)) != 1:
            problems.append("%s: %d stream(s) with sec-fetch-mode cors, a cors set has exactly one" % (name, sum(1 for st in h2["streams"] if is_cors_stream(st))))
            break
        for st in fixture_streams(h2):
            want = fixture_pairs(st)
            built = outcome(lambda: profile_for(mod, label, st))
            if built[0] == "exc":
                problems.append("%s stream %d: raised %s(%r)" % (name, st["stream_id"], type(built[1]).__name__, str(built[1])[:120]))
                break
            pairs = want
            if built[1] is not None:
                if built[1] != want:
                    i = first_difference(built[1], want)
                    problems.append("%s stream %d: field %d is %r, the capture has %r" % (name, st["stream_id"], i, built[1][i][0] if i < len(built[1]) else None, want[i][0] if i < len(want) else None))
                    break
                n += 1
                pairs = built[1]
            if enc.encode(pairs) != bytes.fromhex(st["header_block_hex"]):
                problems.append("%s stream %d: the block does not re-encode byte-identical" % (name, st["stream_id"]))
                break
    return problems, n


def caller_row(mod, name, value, message):
    """expect_refusal on _ch_check_caller_headers plus the M-1 invariant: one line, no raw CR/LF, the injected text not echoed."""
    got = outcome(lambda: mod._ch_check_caller_headers([(name, value)]))
    problems = expect_refusal(got, mod.ChromeClientError, message)
    if got[0] == "exc":
        text = str(got[1])
        problems += problem_if(len(text.splitlines()) != 1 or "\r" in text or "\n" in text, "not one line: %r" % text[:120])
        problems += problem_if("Injected" in text, "the injected value is echoed: %r" % text[:120])
    return problems, [message.replace("\r", "\\r").replace("\n", "\\n")]


def group_hpack(suite, mod, cc):
    """Group D: RFC 7541 vectors, every committed HEADERS block, the profile lists, and the caller-header rules."""
    CE = mod.ChromeClientError
    table = huffman_b()

    def huffman_row():
        got = tuple(tuple(x) for x in mod._CH_HUFFMAN)
        bad = [s for s in range(len(table)) if s >= len(got) or got[s] != table[s]]
        return (problem_if(len(table) != 257, "the Appendix B table held here has %d rows" % len(table))
                + problem_if(len(got) != 257, "_CH_HUFFMAN has %d codes" % len(got))
                + problem_if(bad, "%d symbol(s) differ, first %r" % (len(bad), bad[:8]))), ["257 codes; EOS = 0x%x in %d bits" % table[256]]
    run_row(suite, GD, "huffman-derived-codes-equal-rfc7541-appendix-b", huffman_row, "RFC 7541 Appendix B, held here as RFC7541_HUFFMAN_B (copied from the RFC text by script)")

    decoders = {}
    for sec, dump, want, size in RFC7541_EXAMPLES:
        chapter = sec[:3]
        coding = "huffman" if chapter in ("C.4", "C.6") else "raw"

        def dec_row(dump=dump, want=want, size=size, chapter=chapter):
            if chapter == "C.2":
                dec = mod._ChHpackDecoder()
            else:
                dec = decoders.setdefault(chapter, mod._ChHpackDecoder(256 if chapter in ("C.5", "C.6") else HPACK_DEFAULT_TABLE))
            problems = expect_value(outcome(lambda: dec.decode(h(dump))), list(want))
            problems += problem_if(not problems and dec._size != size, "dynamic table size %d after decoding, the RFC says %d" % (dec._size, size))
            return problems, ["%d field(s); dynamic table size %d" % (len(want), size)]
        run_row(suite, GD, "rfc7541-%s-decode-%s" % (sec, coding), dec_row, "RFC 7541 Appendix %s (hex dump, decoded header list, table size; copied by script)" % sec)

    enc = mod._ChHpackEncoder()
    for sec, dump, want, _size in RFC7541_EXAMPLES:
        if sec.startswith("C.4."):
            run_row(suite, GD, "rfc7541-%s-encode-huffman" % sec, lambda dump=dump, want=want: (expect_value(outcome(lambda: enc.encode(list(want))), h(dump)), ["one encoder across C.4.1-C.4.3, the RFC's one connection"]),
                    "RFC 7541 Appendix %s; C.3/C.5/C.6 are not encode vectors for Chrome's choices (Huffman only when shorter, pseudo-headers but :authority never indexed)" % sec)

    totals = {"blocks": 0, "encoded": 0, "connections": 0}
    for label in H2_SETS:
        def decode_row(label=label):
            problems, n = [], 0
            for name, h2 in h2_fixtures(label):
                ours, theirs = mod._ChHpackDecoder(), cc.HpackDecoder()
                for st in fixture_streams(h2):
                    raw = bytes.fromhex(st["header_block_hex"])
                    want = fixture_pairs(st)
                    p = expect_value(outcome(lambda: ours.decode(raw)), want)
                    if not p and cc_pairs(theirs, raw) != want:
                        p = ["chrome_capture.HpackDecoder decodes it differently from the fixture"]
                    if p:
                        problems.append("%s stream %d: %s" % (name, st["stream_id"], p[0]))
                        break
                    n += 1
            return problems[:3] + problem_if(not n, "no h2 blocks under %s/" % label), ["%d HEADERS block(s) decoded equal to the fixture list and to chrome_capture.HpackDecoder" % n]
        run_row(suite, GD, "hpack-decode-every-%s-block" % label, decode_row, "tests/files/chrome/154/%s/*.json h2.streams[].header_block_hex, one decoder per connection" % label)

        def encode_row(label=label):
            problems, n, blocks, conns = [], 0, 0, 0
            for name, h2 in h2_fixtures(label):
                conns += 1
                coder = mod._ChHpackEncoder()
                for st in fixture_streams(h2):
                    blocks += 1
                    want = bytes.fromhex(st["header_block_hex"])
                    got = coder.encode(fixture_pairs(st))
                    if got != want:
                        problems.append("%s stream %d: first difference at byte %d (%d vs %d bytes)" % (name, st["stream_id"], first_difference(got, want), len(got), len(want)))
                        break
                    n += 1
            totals["blocks"] += blocks
            totals["encoded"] += n
            totals["connections"] += conns
            return problems[:3] + problem_if(not n, "no h2 blocks under %s/" % label), ["%d of %d HEADERS block(s) of %d connection(s) re-encoded byte-identical (one encoder per connection, streams in id order)" % (n, blocks, conns)]
        run_row(suite, GD, "hpack-reencode-every-%s-block" % label, encode_row, "_ChHpackEncoder vs tests/files/chrome/154/%s/*.json header_block_hex" % label)

    run_row(suite, GD, "hpack-reencode-every-committed-h2-block",
            lambda: (problem_if(totals["encoded"] != totals["blocks"] or not totals["blocks"], "%d of %d blocks byte-identical" % (totals["encoded"], totals["blocks"])),
                     ["%d of %d committed HEADERS blocks over %d h2 connection(s) reproduced byte-identical (counted from the directory, never typed)" % (totals["encoded"], totals["blocks"], totals["connections"])]),
            "reads ONLY tests/files/chrome/154/ (R2-L8)")

    for label in H2_SETS:
        def prof_row(label=label):
            problems, n = profile_problems(mod, label)
            return problems + problem_if(not n and not problems, "no generated stream under %s/" % label), ["%d _ch_profile_headers list(s) equal the fixture's decoded list and re-encode byte-identical in place" % n]
        run_row(suite, GD, "profile-headers-vs-%s" % label, prof_row, "stream 1 = navigate%s; the no-cors subresources are re-encoded only" % ("; the sec-fetch-mode cors stream = the fetch" if label in CORS_SETS else ""))

    T = mod.CH_HPACK_TABLE_BYTES
    cap = mod.CH_MAX_HPACK_INT
    cont = mod.CH_MAX_HPACK_INT_CONTINUATIONS
    a_bits = huffman_bits(table, b"a")
    eos_bits = format(table[256][0], "0%db" % table[256][1])
    candidates = [bytes([s]) for s in range(32, 127)] + [bytes([x, y]) for x in range(32, 127) for y in range(32, 127)]
    seven = next(c for c in candidates if len(huffman_bits(table, c)) % 8 == 1)
    fill = "1" * (-(len(eos_bits) + len(a_bits)) % 8)
    decode_refusals = (
        ("index-0", b"\x80", "h2: hpack index 0", "RFC 7541 section 6.1: index 0 is a decoding error"),
        ("index-past-both-tables", hp_int(HPACK_STATIC_ENTRIES + 1, 7, 0x80), "h2: hpack index %d out of range (%d entries)" % (HPACK_STATIC_ENTRIES + 1, HPACK_STATIC_ENTRIES), "section 2.3.3: an index past the static and (empty) dynamic table"),
        ("literal-name-index-past-both-tables", hp_int(HPACK_STATIC_ENTRIES + 1, 6, 0x40) + hp_string(b"v"), "h2: hpack index %d out of range (%d entries)" % (HPACK_STATIC_ENTRIES + 1, HPACK_STATIC_ENTRIES), "section 6.2.1 with an out-of-range name index"),
        ("integer-over-cap", hp_int(cap + 1, 7, 0x80), "h2: hpack integer exceeds %d" % cap, "section 5.1 integer overflow; CH_MAX_HPACK_INT read off the module"),
        ("integer-continuations-over-cap", b"\xff" + b"\x80" * cont + b"\x00", "h2: hpack integer longer than %d continuation bytes" % cont, "CH_MAX_HPACK_INT_CONTINUATIONS read off the module"),
        ("huffman-padding-not-all-ones", hp_huffman_value(a_bits + "110"), "h2: hpack Huffman padding is not all ones", "section 5.2: padding not the EOS prefix is a decoding error"),
        ("huffman-padding-over-7-bits", hp_huffman_value(a_bits + "111" + "11111111"), "h2: hpack Huffman padding longer than 7 bits", "section 5.2: padding over 7 bits is a decoding error"),
        ("huffman-eos-in-string", hp_huffman_value(eos_bits + a_bits + fill), "h2: hpack Huffman string contains EOS", "section 5.2: EOS in a string is a decoding error"),
        ("table-size-update-over-settings", hp_int(T + 1, 5, 0x20), "h2: hpack table size update %d exceeds %d" % (T + 1, T), "sections 4.2, 6.3: above OUR SETTINGS_HEADER_TABLE_SIZE (CH_HPACK_TABLE_BYTES)"),
        ("table-size-update-after-a-field", b"\x82" + hp_int(100, 5, 0x20), "h2: hpack table size update after a header field", "section 4.2: only at the start of a block"),
        ("string-truncated", hp_int(0, 4) + b"\x05a", "h2: hpack string truncated", "section 5.2: a length past the block"),
    )
    for cid, block, message, src in decode_refusals:
        run_row(suite, GD, "hpack-refuses-%s" % cid, lambda block=block, message=message: (expect_refusal(outcome(lambda: mod._ChHpackDecoder().decode(block)), CE, message), [message]), src)

    decode_accepts = (
        ("integer-at-cap", lambda: mod._ch_hpack_decode_int(hp_int(cap, 7, 0x80), 0, 7), (cap, len(hp_int(cap, 7, 0x80))), "CH_MAX_HPACK_INT itself decodes"),
        ("integer-at-continuation-cap", lambda: mod._ch_hpack_decode_int(b"\xff" + b"\x80" * (cont - 1) + b"\x00", 0, 7), (127, cont + 1), "%d continuation bytes decode" % cont),
        ("huffman-7-bit-padding", lambda: mod._ChHpackDecoder().decode(hp_huffman_value(huffman_bits(table, seven) + "1111111")), [("x", seven.decode("latin-1"))], "section 5.2: 7 bits of EOS prefix is the most padding allowed"),
        ("table-size-update-at-settings", lambda: mod._ChHpackDecoder().decode(hp_int(T, 5, 0x20)), [], "a size update of exactly CH_HPACK_TABLE_BYTES"),
    )
    for cid, fn, want, src in decode_accepts:
        run_row(suite, GD, "hpack-accepts-%s" % cid, lambda fn=fn, want=want: (expect_value(outcome(fn), want), ["%r" % (want,)]), src)

    def dead_row():
        dec = mod._ChHpackDecoder()
        first = expect_refusal(outcome(lambda: dec.decode(b"\x80")), CE, "h2: hpack index 0")
        return first + expect_refusal(outcome(lambda: dec.decode(b"\x82")), CE, "h2: hpack decoder unusable after an earlier error"), ["a half-updated table is never trusted again (RFC 9113 section 4.3)"]
    run_row(suite, GD, "hpack-decoder-unusable-after-a-refusal", dead_row, "COMPRESSION_ERROR is a connection error")

    src = "_ch_check_caller_headers; feature plan Step 7 rules 1-5 and the latin-1 rule"
    for cid, name, value, message in CALLER_REFUSED:
        run_row(suite, GD, "caller-header-%s-refused" % cid, lambda name=name, value=value, message=message: caller_row(mod, name, value, message), src)
    for cid, name in PSEUDO_ECHO:
        def echo_row(name=name):
            message = "headers: %s is fixed by the Chrome profile" % repr(name)[:40]
            problems, detail = caller_row(mod, name, "1", message)
            got = outcome(lambda: mod._ch_check_caller_headers([(name, "1")]))
            text = str(got[1]) if got[0] == "exc" else ""
            problems += problem_if("\x0b" in text, "the message carries a raw \\x0b")
            problems += problem_if(len(text) > PSEUDO_ECHO_MAX, "the message is %d characters, over %d" % (len(text), PSEUDO_ECHO_MAX))
            return problems, ["%d characters, one line" % len(text)]
        run_row(suite, GD, "caller-header-pseudo-echo-%s-one-short-line" % cid, echo_row, "M-1: rule 2 shows the name as repr(name)[:40]")
    for name, value in CALLER_ACCEPTED:
        run_row(suite, GD, "caller-header-%s-accepted" % name.lower(), lambda name=name, value=value: (expect_value(outcome(lambda: mod._ch_check_caller_headers([(name, value)])), None), []), src)

    def custom_row():
        hdrs = mod._ch_profile_headers("navigate", "GET", "a.test", "/", extra=[("X-Custom", "1")])
        problems = problem_if(("x-custom", "1") not in hdrs or any(n == "X-Custom" for n, _v in hdrs), "the list carries %r" % [n for n, _v in hdrs if n.lower() == "x-custom"])
        conn = mod._ChH2Connection()
        conn.preface()
        _sid, data = conn.open_stream(hdrs)
        frames = h2_frames(data)
        names = [n for n, _v in cc_pairs(cc.HpackDecoder(), header_fragment(frames[0]))]
        problems += problem_if("x-custom" not in names or "X-Custom" in names, "the HEADERS block carries %r" % [n for n in names if n.lower() == "x-custom"])
        return problems, ["the h2 HEADERS block, decoded by chrome_capture, names it x-custom (RFC 9113 section 8.2.1)"]
    run_row(suite, GD, "caller-header-x-custom-emitted-lowercase-on-h2", custom_row, "_ch_profile_headers(extra=[('X-Custom', '1')]) -> _ChH2Connection.open_stream")

    def control_row():
        original = mod.CHROME_PROFILE
        order = list(original["navigate_order"])
        i, j = order.index("user-agent"), order.index("accept")
        order[i], order[j] = order[j], order[i]
        patched = dict(original)
        patched["navigate_order"] = tuple(order)
        mod.CHROME_PROFILE = patched
        try:
            problems, _n = profile_problems(mod, "navigate")
        finally:
            mod.CHROME_PROFILE = original
        return problem_if(not problems, "a navigate_order with user-agent and accept swapped still matched every capture"), ["caught: %s" % (problems[0] if problems else "nothing")]
    run_row(suite, GD, "control-navigate-order-swap-must-fail", control_row, "planted: user-agent <-> accept swapped in CHROME_PROFILE['navigate_order'], restored afterwards")


# --- E. HTTP/2 against a test-side RFC 9113 responder --------------------------------------

# Chrome 154's Akamai HTTP/2 fingerprint (docs/concepts/spec-ddg.md:448; unchanged in tests/files/chrome/154/README.md):
# SETTINGS id:value in order | connection WINDOW_UPDATE | PRIORITY frames | pseudo-header order.
AKAMAI_CHROME = "1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p"

# RFC 9113 section 3.4: the client connection preface magic.
H2_MAGIC = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

# RFC 9113 section 6 frame types and flags, and section 7 error codes.
F_DATA, F_HEADERS, F_PRIORITY, F_RST_STREAM, F_SETTINGS, F_PUSH_PROMISE, F_PING, F_GOAWAY, F_WINDOW_UPDATE, F_CONTINUATION = range(10)
F_UNKNOWN = 0xFA
FL_END_STREAM = 0x01
FL_ACK = 0x01
FL_END_HEADERS = 0x04
FL_PADDED = 0x08
FL_PRIORITY = 0x20
E_PROTOCOL = 1
E_INTERNAL = 2
E_FLOW_CONTROL = 3
E_FRAME_SIZE = 6
E_REFUSED_STREAM = 7
E_CANCEL = 8
E_COMPRESSION = 9
E_CALM = 11

# RFC 9113 section 6.9.2: every flow-control window starts at 65535 octets;
# section 6.9.1: none may exceed 2^31-1.
H2_INITIAL_WINDOW = 65535
H2_MAX_WINDOW = 2 ** 31 - 1


def h2f(ftype, flags, sid, payload=b""):
    """One frame as RFC 9113 section 4.1 lays it out: 24-bit length, type, flags, R + 31-bit stream id, payload."""
    n = len(payload)
    return struct.pack("!BHBBI", n >> 16, n & 0xFFFF, ftype, flags, sid) + bytes(payload)


def h2_frames(data):
    """[(type, flags, sid, payload)] of a byte string of whole frames; a torn frame raises."""
    out, pos = [], 0
    data = bytes(data)
    while pos < len(data):
        if len(data) - pos < 9:
            raise ValueError("torn frame header at byte %d" % pos)
        n = int.from_bytes(data[pos:pos + 3], "big")
        if pos + 9 + n > len(data):
            raise ValueError("torn frame payload at byte %d" % pos)
        sid = int.from_bytes(data[pos + 5:pos + 9], "big") & 0x7FFFFFFF
        out.append((data[pos + 3], data[pos + 4], sid, data[pos + 9:pos + 9 + n]))
        pos += 9 + n
    return out


def header_fragment(frame):
    """The header block fragment of a HEADERS frame: after the pad length and the priority fields, before the padding (section 6.2)."""
    _ftype, flags, _sid, payload = frame
    start, pad = 0, 0
    if flags & FL_PADDED:
        pad = payload[0]
        start = 1
    if flags & FL_PRIORITY:
        start += 5
    return payload[start:len(payload) - pad]


def describe(frames):
    return "[%s]" % ", ".join("type %d flags 0x%x sid %d %dB" % (f[0], f[1], f[2], len(f[3])) for f in frames[:6])


def resp_headers(sid, pairs, end_stream=True):
    """A response HEADERS frame of literal fields (hp_block), END_HEADERS, END_STREAM when asked."""
    return h2f(F_HEADERS, FL_END_HEADERS | (FL_END_STREAM if end_stream else 0), sid, hp_block(pairs))


def header_frames(sid, block, end_stream, frame_max):
    """`block` as HEADERS plus as many CONTINUATIONs as frame_max needs (RFC 9113 section 6.10)."""
    chunks = [block[i:i + frame_max] for i in range(0, len(block), frame_max)] or [b""]
    out = bytearray()
    for i, chunk in enumerate(chunks):
        last = FL_END_HEADERS if i == len(chunks) - 1 else 0
        if i == 0:
            out += h2f(F_HEADERS, last | (FL_END_STREAM if end_stream else 0), sid, chunk)
        else:
            out += h2f(F_CONTINUATION, last, sid, chunk)
    return bytes(out)


def akamai_of(cc, data):
    """The Akamai string of a client byte stream (after the magic), computed here from its frames."""
    frames = h2_frames(data)
    settings = [f for f in frames if f[0] == F_SETTINGS and not f[1] & FL_ACK][0][3]
    s_part = ";".join("%d:%d" % struct.unpack_from("!HI", settings, i) for i in range(0, len(settings), 6))
    wu = [f for f in frames if f[0] == F_WINDOW_UPDATE and f[2] == 0]
    w_part = str(struct.unpack("!I", wu[0][3])[0] & 0x7FFFFFFF) if wu else "00"
    p_part = str(len([f for f in frames if f[0] == F_PRIORITY])) if any(f[0] == F_PRIORITY for f in frames) else "0"
    first = [f for f in frames if f[0] == F_HEADERS][0]
    letters = {":method": "m", ":authority": "a", ":scheme": "s", ":path": "p"}
    names = [n for n, _v in cc_pairs(cc.HpackDecoder(), header_fragment(first)) if n.startswith(":")]
    return "%s|%s|%s|%s" % (s_part, w_part, p_part, ",".join(letters.get(n, n) for n in names))


def fixture_client_bytes(h2):
    """The client's byte stream as the capture recorded it, rebuilt frame by frame (RFC 9113 section 4.1)."""
    streams = dict((s["stream_id"], s) for s in h2["streams"])
    data_seen = {}
    out = bytearray(H2_MAGIC)
    for fr in h2["frames"]:
        t = fr["type"]
        if t == F_SETTINGS:
            pl = b"".join(struct.pack("!HI", s["id"], s["value"]) for s in fr.get("settings", []))
        elif t == F_WINDOW_UPDATE:
            pl = struct.pack("!I", fr["increment"])
        elif t == F_HEADERS:
            p = fr["priority"]
            pl = struct.pack("!IB", (0x80000000 if p["exclusive"] else 0) | p["dep"], p["weight"] - 1) + bytes.fromhex(streams[fr["stream_id"]]["header_block_hex"])
        elif t == F_DATA:
            i = data_seen.get(fr["stream_id"], 0)
            data_seen[fr["stream_id"]] = i + 1
            pl = bytes.fromhex(streams[fr["stream_id"]]["data_frames"][i]["payload_hex"])
        else:
            raise ValueError("client frame type %d in the capture" % t)
        if len(pl) != fr["length"]:
            raise ValueError("rebuilt type-%d frame is %d bytes, the capture says %d" % (t, len(pl), fr["length"]))
        out += h2f(t, fr["flags"], fr["stream_id"], pl)
    return bytes(out)


def replay_client_bytes(mod, h2):
    """What _ChH2Connection writes for the capture's requests; the server preface SETTINGS is fed where the capture shows the client's ACK."""
    streams = dict((s["stream_id"], s) for s in h2["streams"])
    conn = mod._ChH2Connection()
    out = bytearray(conn.preface())
    for fr in h2["frames"]:
        if fr["type"] == F_HEADERS:
            st = streams[fr["stream_id"]]
            pairs = fixture_pairs(st)
            prio = None
            if dict(pairs).get("sec-fetch-mode") not in ("navigate", "cors"):
                p = fr["priority"]
                prio = (p["exclusive"], p["dep"], p["weight"])
            body = b"".join(bytes.fromhex(d["payload_hex"]) for d in st.get("data_frames") or []) or None
            sid, data = conn.open_stream(pairs, body, priority=prio)
            if sid != fr["stream_id"]:
                raise ValueError("stream id %d, the capture has %d" % (sid, fr["stream_id"]))
            out += data
        elif fr["type"] == F_SETTINGS and fr["flags"] & FL_ACK:
            out += conn.feed(h2f(F_SETTINGS, 0, 0))
    return bytes(out)


class H2Responder:
    """The server side of one h2 connection, written here from RFC 9113 -- never from the module.

    It frames with h2f(), writes every response head as RFC 7541 literals
    without indexing (hp_block: no table, no Huffman, nothing shared with the
    module's encoder), reads what the client wrote with h2_frames(), and
    decodes the client's header blocks with chrome_capture's independent
    HpackDecoder. The client under test is a _ChH2Connection driven sans-IO:
    the responder feeds it bytes and keeps every byte it wrote back, so each
    row is deterministic and needs no socket or clock; one row runs the same
    exchange over a socketpair with the responder in a thread.
    """

    def __init__(self, mod):
        self.mod = mod
        self.logs = []
        self.client = mod._ChH2Connection(log=self.logs.append)
        self.wire = bytearray(self.client.preface())

    def opening(self, settings=b""):
        """RFC 9113 section 3.4: the server preface SETTINGS, then the ACK of the client's SETTINGS (section 6.5.3)."""
        return self.send(h2f(F_SETTINGS, 0, 0, settings) + h2f(F_SETTINGS, FL_ACK, 0))

    def request(self, headers=None, body=None, max_bytes=0):
        if headers is None:
            headers = self.mod._ch_profile_headers("navigate", "GET", "responder.test", "/")
        sid, data = self.client.open_stream(headers, body, max_bytes)
        self.wire += data
        return sid

    def send(self, data):
        """feed() the client; the frames it wrote in answer. A refusal propagates."""
        out = self.client.feed(data)
        self.wire += out
        return h2_frames(out)

    def refusal(self, data, message, code):
        """Problems unless feeding `data` raises exactly `message`, exactly one GOAWAY(code) is queued, and the connection stays dead."""
        got = outcome(lambda: self.client.feed(data))
        problems = expect_refusal(got, self.mod.ChromeClientError, message)
        queued = h2_frames(self.client.data_to_send())
        problems += problem_if(queued != [(F_GOAWAY, 0, 0, struct.pack("!II", 0, code))], "queued %s, wanted one GOAWAY(last 0, code %d)" % (describe(queued), code))
        problems += problem_if(self.client.usable(), "usable() is True after a connection error")
        problems += problem_if(outcome(lambda: self.client.feed(b""))[0] != "exc", "a later feed() was accepted")
        return problems


def opened(mod, streams=1, **kw):
    """(responder, sid of the first stream) after the RFC-conformant opening and `streams` requests."""
    r = H2Responder(mod)
    r.opening()
    sid = r.request(**kw)
    for _ in range(streams - 1):
        r.request()
    return r, sid


def flood_row(mod, units, refused_unit, needs_stream=True, before=b""):
    """Feed `before`, then `units` (together N counted frames), all accepted; then `refused_unit` is the flood."""
    N = mod.CH_MAX_H2_CONTROL_FRAMES
    r = H2Responder(mod)
    r.opening()
    if needs_stream:
        r.request()
    got = outcome(lambda: r.send(before + units))
    problems = problem_if(got[0] == "exc", "refused before the ceiling: %s" % (got[1] if got[0] == "exc" else ""))
    problems += problem_if(not problems and not r.client.usable(), "not usable after N frames")
    if problems:
        return problems
    return r.refusal(refused_unit, "h2: control frame flood (more than %d per connection)" % N, E_CALM)


def group_h2(suite, mod, cc):
    """Group E: the h2 connection against H2Responder; first flight vs the captures, every ceiling at N and N+1."""
    CE = mod.ChromeClientError
    F = mod.CH_MAX_FRAME_BYTES
    N = mod.CH_MAX_H2_CONTROL_FRAMES
    EMPTY = mod.CH_MAX_H2_EMPTY_FRAMES
    CONT = mod.CH_MAX_H2_CONTINUATION_FRAMES
    BLOCK = mod.CH_MAX_HEADER_BLOCK_BYTES
    LIST = mod.CH_MAX_HEADER_LIST_BYTES

    for label in H2_SETS:
        def flight_row(label=label):
            problems, n, same = [], 0, 0
            for name, h2 in h2_fixtures(label):
                n += 1
                want = fixture_client_bytes(h2)
                got = outcome(lambda: replay_client_bytes(mod, h2))
                if got[0] == "exc":
                    problems.append("%s: raised %s(%r)" % (name, type(got[1]).__name__, str(got[1])[:120]))
                elif got[1] != want:
                    problems.append("%s: first difference at byte %d (%d vs %d bytes)" % (name, first_difference(got[1], want), len(got[1]), len(want)))
                else:
                    same += 1
            return problems[:3] + problem_if(not n, "no h2 connections under %s/" % label), ["%d of %d connection(s): the client's whole byte stream (magic, SETTINGS, WINDOW_UPDATE, every HEADERS and DATA, the SETTINGS ACK) byte-identical" % (same, n)]
        run_row(suite, GE, "first-flight-vs-%s" % label, flight_row, "tests/files/chrome/154/%s/*.json h2.frames rebuilt; no-cors streams take the capture's PRIORITY, navigate/cors the profile's" % label)

    def akamai_row():
        r = H2Responder(mod)
        r.request()
        wire = bytes(r.wire)
        problems = problem_if(not wire.startswith(H2_MAGIC), "no client magic")
        got = akamai_of(cc, wire[len(H2_MAGIC):])
        problems += problem_if(got != AKAMAI_CHROME, "akamai %r" % got)
        recorded = [h2.get("akamai") for label in H2_SETS for _n, h2 in h2_fixtures(label)]
        problems += problem_if(set(recorded) != set([AKAMAI_CHROME]), "the captures record %r" % sorted(set(recorded), key=str))
        return problems, [got, "every one of the %d h2 capture connections records the same string" % len(recorded)]
    run_row(suite, GE, "first-flight-akamai-equals-spec-ddg", akamai_row, "docs/concepts/spec-ddg.md:448; computed here from the frames, pseudo-headers decoded by chrome_capture")

    def size_ok():
        r, _sid = opened(mod)
        got = outcome(lambda: r.send(struct.pack("!BHBBI", F >> 16, F & 0xFFFF, F_DATA, 0, 1)))
        return expect_value(got, []) + problem_if(not r.client.usable(), "not usable"), ["a %d-byte header waits for its payload" % F]
    run_row(suite, GE, "frame-of-max-frame-bytes-header-accepted", size_ok, "RFC 9113 section 4.2; CH_MAX_FRAME_BYTES read off the module")

    def size_bad():
        r, _sid = opened(mod)
        head = struct.pack("!BHBBI", (F + 1) >> 16, (F + 1) & 0xFFFF, F_DATA, 0, 1)
        return r.refusal(head, "h2: frame of %d bytes exceeds %d" % (F + 1, F), E_FRAME_SIZE), ["refused from the 9-byte header alone, no payload byte sent"]
    run_row(suite, GE, "frame-over-max-frame-bytes-refused-before-payload", size_bad, "RFC 9113 section 4.2: FRAME_SIZE_ERROR")

    def cont_bytes():
        r, sid = opened(mod)
        full, rest = divmod(BLOCK, F)
        frames = full - 1 + (1 if rest else 0)
        if frames > CONT:
            return ["%d CONTINUATIONs would pass CH_MAX_H2_CONTINUATION_FRAMES first; the row cannot isolate the byte ceiling" % frames], []
        data = h2f(F_HEADERS, 0, sid, bytes(F)) + b"".join(h2f(F_CONTINUATION, 0, sid, bytes(F)) for _ in range(full - 1))
        if rest:
            data += h2f(F_CONTINUATION, 0, sid, bytes(rest))
        got = outcome(lambda: r.send(data))
        problems = expect_value(got, []) + problem_if(not r.client.usable(), "not usable at the ceiling")
        if problems:
            return problems, []
        return r.refusal(h2f(F_CONTINUATION, 0, sid, b"\x00"), "h2: header block exceeds %d bytes" % BLOCK, E_COMPRESSION), ["an open block of exactly %d bytes (HEADERS + %d CONTINUATION) accepted; one byte more refused" % (BLOCK, frames)]
    run_row(suite, GE, "continuation-flood-by-bytes-at-block-ceiling-then-one-over", cont_bytes, "CH_MAX_HEADER_BLOCK_BYTES read off the module")

    def cont_frames():
        r, sid = opened(mod)
        got = outcome(lambda: r.send(h2f(F_HEADERS, 0, sid, b"\x00") + h2f(F_CONTINUATION, 0, sid, b"\x00") * CONT))
        problems = expect_value(got, []) + problem_if(not r.client.usable(), "not usable at the ceiling")
        if problems:
            return problems, []
        return r.refusal(h2f(F_CONTINUATION, 0, sid, b"\x00"), "h2: CONTINUATION flood (more than %d in one header block)" % CONT, E_CALM), ["%d one-byte CONTINUATIONs accepted, the next refused" % CONT]
    run_row(suite, GE, "continuation-flood-by-frames-at-ceiling-then-one-over", cont_frames, "CH_MAX_H2_CONTINUATION_FRAMES read off the module")

    def cont_empty():
        r, sid = opened(mod)
        got = outcome(lambda: r.send(h2f(F_HEADERS, 0, sid, b"\x00") + h2f(F_CONTINUATION, 0, sid) * EMPTY))
        problems = expect_value(got, []) + problem_if(not r.client.usable(), "not usable at the ceiling")
        if problems:
            return problems, []
        return r.refusal(h2f(F_CONTINUATION, 0, sid), "h2: empty CONTINUATION flood on stream %d" % sid, E_CALM), ["%d zero-length CONTINUATIONs accepted, the next refused" % EMPTY]
    run_row(suite, GE, "continuation-flood-zero-length-run-at-ceiling-then-one-over", cont_empty, "CH_MAX_H2_EMPTY_FRAMES read off the module")

    ping = h2f(F_PING, 0, 0, bytes(8))
    unknown = h2f(F_UNKNOWN, 0, 0)
    settings = h2f(F_SETTINGS, 0, 0)
    rst = h2f(F_RST_STREAM, 0, 1, struct.pack("!I", E_CANCEL))
    q = N // 4
    floods = (
        ("settings", settings * N, settings, "non-ACK SETTINGS after the exempt preface SETTINGS and ACK"),
        ("ping", ping * N, ping, "PING (each answered with an ACK)"),
        ("rst-stream", rst * N, rst, "RST_STREAM on stream 1 (the first one resets it)"),
        ("unknown-type-0xfa", unknown * N, unknown, "frames of the unknown type 0xFA (RFC 9113 section 4.1: ignored, but counted)"),
        ("mixed-settings-ping-rst-unknown", settings * q + ping * q + rst * q + unknown * (N - 3 * q), ping, "%d SETTINGS + %d PING + %d RST_STREAM + %d unknown" % (q, q, q, N - 3 * q)),
    )
    for cid, units, last, what in floods:
        run_row(suite, GE, "control-flood-%s-%d-pass-then-one-refused" % (cid, N), lambda units=units, last=last: (flood_row(mod, units, last), ["%d counted frames accepted after the opening, frame %d refused with GOAWAY(ENHANCE_YOUR_CALM)" % (N, N + 1)]), what)
    run_row(suite, GE, "control-flood-unsolicited-second-ack-is-counted", lambda: (flood_row(mod, unknown * (N - 1), unknown, before=h2f(F_SETTINGS, FL_ACK, 0)), ["after the opening: one more SETTINGS ACK + %d unknown = %d counted, accepted; the next refused" % (N - 1, N)]),
            "the ACK exemption is one-shot (feature plan Step 7, S035)")

    def empty_data():
        r, sid = opened(mod)
        got = outcome(lambda: r.send(resp_headers(sid, [(":status", "200")], end_stream=False) + h2f(F_DATA, 0, sid) * EMPTY))
        problems = problem_if(got[0] == "exc", "refused before the ceiling: %s" % (got[1] if got[0] == "exc" else "")) + problem_if(not r.client.usable(), "not usable at the ceiling")
        if problems:
            return problems, []
        return r.refusal(h2f(F_DATA, 0, sid), "h2: empty DATA flood on stream %d" % sid, E_CALM), ["%d zero-length DATA without END_STREAM accepted, the next refused" % EMPTY]
    run_row(suite, GE, "empty-data-flood-at-ceiling-then-one-over", empty_data, "CH_MAX_H2_EMPTY_FRAMES read off the module")

    def list_block(total):
        """(block, fields) whose RFC 7541 section 4.1 size is exactly `total`: :status 200, one 4096-octet entry inserted and re-referenced, a tail literal."""
        status = 32 + len(":status") + len("200")
        entry = HPACK_DEFAULT_TABLE
        count = (total - status - 34) // entry
        tail = total - status - count * entry
        block = hp_literal(":status", "200") + hp_literal("x", "v" * (entry - 33), 0x40, 6) + hp_int(HPACK_STATIC_ENTRIES + 1, 7, 0x80) * (count - 1) + hp_literal("y", "w" * (tail - 33))
        return block, [("x", "v" * (entry - 33))] * count + [("y", "w" * (tail - 33))]

    def list_ok():
        r, sid = opened(mod)
        block, fields = list_block(LIST)
        if len(block) >= BLOCK:
            return ["the encoded block is %d bytes, not under CH_MAX_HEADER_BLOCK_BYTES" % len(block)], []
        r.send(header_frames(sid, block, True, F))
        got = outcome(lambda: r.client.response(sid))
        return expect_value(got, (200, fields)), ["a %d-byte list from a %d-byte block (%d indexed references to one dynamic entry) accepted" % (LIST, len(block), len(fields) - 2)]
    run_row(suite, GE, "header-list-of-exactly-max-header-list-bytes-accepted", list_ok, "RFC 7541 section 4.1 size; CH_MAX_HEADER_LIST_BYTES read off the module")

    def list_bad():
        r, sid = opened(mod)
        block, _fields = list_block(LIST + 1)
        return r.refusal(header_frames(sid, block, True, F), "h2: hpack header list exceeds %d bytes" % LIST, E_COMPRESSION), ["the same block with the tail one byte longer (%d bytes of list)" % (LIST + 1)]
    run_row(suite, GE, "header-list-one-byte-over-refused", list_bad, "the encoded block stays under CH_MAX_HEADER_BLOCK_BYTES, so the list ceiling alone refuses it")

    wu_rows = (
        ("zero-increment-connection", 0, [0], "h2: WINDOW_UPDATE with a zero increment", E_PROTOCOL),
        ("zero-increment-stream", 1, [0], "h2: WINDOW_UPDATE with a zero increment", E_PROTOCOL),
        ("connection-window-to-2-31", 0, [H2_MAX_WINDOW - H2_INITIAL_WINDOW, 1], "h2: WINDOW_UPDATE takes the connection window past 2^31-1", E_FLOW_CONTROL),
        ("stream-window-to-2-31", 1, [H2_MAX_WINDOW - H2_INITIAL_WINDOW, 1], "h2: WINDOW_UPDATE takes the window of stream 1 past 2^31-1", E_FLOW_CONTROL),
    )
    for cid, sid, incs, message, code in wu_rows:
        def wu_row(sid=sid, incs=incs, message=message, code=code):
            r, _s = opened(mod)
            problems = []
            for inc in incs[:-1]:
                got = outcome(lambda: r.send(h2f(F_WINDOW_UPDATE, 0, sid, struct.pack("!I", inc))))
                problems += expect_value(got, [])
            if problems:
                return ["the window at exactly 2^31-1 was refused: %s" % problems[0]], []
            return r.refusal(h2f(F_WINDOW_UPDATE, 0, sid, struct.pack("!I", incs[-1])), message, code), ["increments %r on stream %d from the initial %d" % (incs, sid, H2_INITIAL_WINDOW)]
        run_row(suite, GE, "window-update-%s-refused" % cid, wu_row, "RFC 9113 sections 6.9 (0 is PROTOCOL_ERROR), 6.9.1 (past 2^31-1 is FLOW_CONTROL_ERROR)")

    def credit_row():
        r = H2Responder(mod)
        promised = h2_frames(bytes(r.wire)[len(H2_MAGIC):])
        stream_window = dict(struct.unpack_from("!HI", promised[0][3], i) for i in range(0, len(promised[0][3]), 6)).get(4, H2_INITIAL_WINDOW)
        conn_window = H2_INITIAL_WINDOW + sum(struct.unpack("!I", f[3])[0] for f in promised if f[0] == F_WINDOW_UPDATE and f[2] == 0)
        r.opening()
        sid = r.request(max_bytes=mod.CH_MAX_BODY_BYTES)
        r.send(resp_headers(sid, [(":status", "200")], end_stream=False))
        credits = {0: [], sid: []}
        total = 0
        frames = conn_window // F + 1
        for _ in range(frames):
            for f in r.send(h2f(F_DATA, 0, sid, bytes(F))):
                if f[0] == F_WINDOW_UPDATE:
                    credits.setdefault(f[2], []).append(struct.unpack("!I", f[3])[0])
            total += len(r.client.pop_body(sid))
        problems = problem_if(total != frames * F, "%d body bytes drained, %d sent" % (total, frames * F))
        problems += problem_if(not credits[0] or not credits[sid], "WINDOW_UPDATE on stream 0: %d, on stream %d: %d" % (len(credits[0]), sid, len(credits[sid])))
        problems += problem_if(any(c <= 0 or c > H2_MAX_WINDOW for c in credits[0] + credits[sid]), "an increment out of 1..2^31-1")
        problems += problem_if(sum(credits[sid]) > total or sum(credits[0]) > total, "credited more than was received")
        return problems, ["%d bytes sent (more than the promised connection window %d and stream window %d); %d connection and %d stream WINDOW_UPDATE(s) written" % (frames * F, conn_window, stream_window, len(credits[0]), len(credits[sid]))]
    run_row(suite, GE, "window-update-credits-received-data-past-both-windows", credit_row, "RFC 9113 section 6.9: the windows the client's own preface promised")

    def goaway_row():
        r, s1 = opened(mod, streams=2)
        s3 = s1 + 2
        r.send(h2f(F_GOAWAY, 0, 0, struct.pack("!II", s1, 0)))
        got = outcome(lambda: r.client.response(s3))
        problems = expect_refusal(got, CE, "h2: stream %d not processed (GOAWAY last-stream-id %d, NO_ERROR)" % (s3, s1))
        problems += problem_if(got[0] == "exc" and getattr(got[1], "retry_safe", None) is not True, "retry_safe is not True")
        problems += problem_if(r.client.usable(), "usable() after GOAWAY")
        problems += expect_value(outcome(lambda: r.client.response(s1)), None)
        r.send(resp_headers(s1, [(":status", "204")]))
        problems += expect_value(outcome(lambda: r.client.response(s1)), (204, []))
        problems += expect_refusal(outcome(lambda: r.request()), CE, "h2: connection is not usable for a new stream")
        return problems, ["stream %d fails retry-safe, stream %d still completes, no new stream opens" % (s3, s1)]
    run_row(suite, GE, "goaway-last-stream-below-sid-is-retry-safe", goaway_row, "RFC 9113 sections 6.8, 8.7")

    for code, name, retry in ((E_REFUSED_STREAM, "REFUSED_STREAM", True), (E_INTERNAL, "INTERNAL_ERROR", False)):
        def rst_row(code=code, name=name, retry=retry):
            r, sid = opened(mod)
            r.send(h2f(F_RST_STREAM, 0, sid, struct.pack("!I", code)))
            got = outcome(lambda: r.client.pop_body(sid))
            problems = expect_refusal(got, CE, "h2: stream %d reset by the server (%s)" % (sid, name))
            problems += problem_if(got[0] == "exc" and bool(getattr(got[1], "retry_safe", False)) is not retry, "retry_safe is %r" % getattr(got[1], "retry_safe", None))
            problems += problem_if(not r.client.usable() or r.request() != sid + 2, "the connection did not stay usable")
            return problems, ["retry_safe=%s; the connection stays usable" % retry]
        run_row(suite, GE, "rst-stream-%s-fails-the-stream" % name.lower().replace("_", "-"), rst_row, "RFC 9113 sections 6.4, 8.7")

    pad_rows = (
        ("headers-pad-past-payload", lambda sid: h2f(F_HEADERS, FL_END_HEADERS | FL_PADDED, sid, b"\x05" + hp_literal(":status", "200")[:4]), "h2: padding and priority fields exceed the frame payload"),
        ("data-pad-equal-to-payload", lambda sid: resp_headers(sid, [(":status", "200")], end_stream=False) + h2f(F_DATA, FL_PADDED, sid, b"\x03ab"), "h2: padding and priority fields exceed the frame payload"),
        ("padded-without-pad-length", lambda sid: resp_headers(sid, [(":status", "200")], end_stream=False) + h2f(F_DATA, FL_PADDED, sid), "h2: padded frame without a pad length"),
    )
    for cid, build, message in pad_rows:
        run_row(suite, GE, "padding-abuse-%s-refused" % cid, lambda build=build, message=message: (opened_refusal(mod, build, message, E_PROTOCOL), [message]), "RFC 9113 sections 6.1, 6.2: padding not shorter than the payload is PROTOCOL_ERROR")

    def pad_ok():
        r, sid = opened(mod)
        block = hp_block([(":status", "200")])
        r.send(h2f(F_HEADERS, FL_END_HEADERS | FL_PADDED, sid, bytes([2]) + block + bytes(2)) + h2f(F_DATA, FL_PADDED | FL_END_STREAM, sid, b"\x03abc\x00\x00\x00"))
        problems = expect_value(outcome(lambda: r.client.response(sid)), (200, []))
        problems += expect_value(outcome(lambda: r.client.pop_body(sid)), b"abc")
        return problems, ["padding stripped from HEADERS and DATA"]
    run_row(suite, GE, "padding-within-the-payload-accepted-and-stripped", pad_ok, "RFC 9113 sections 6.1, 6.2")

    def max_bytes_row():
        r, sid = opened(mod, max_bytes=5)
        first = r.send(resp_headers(sid, [(":status", "200")], end_stream=False) + h2f(F_DATA, 0, sid, b"01234"))
        problems = problem_if(any(f[0] == F_RST_STREAM for f in first), "RST_STREAM at exactly max_bytes")
        over = r.send(h2f(F_DATA, 0, sid, b"5"))
        problems += problem_if((F_RST_STREAM, 0, sid, struct.pack("!I", E_CANCEL)) not in over, "no RST_STREAM(CANCEL) written: %s" % describe(over))
        got = outcome(lambda: r.client.pop_body(sid))
        problems += expect_refusal(got, mod.ChromeBodyTooLarge, "body: exceeds 5 bytes")
        problems += problem_if(got[0] == "exc" and getattr(got[1], "limit", None) != 5, "limit %r" % getattr(got[1], "limit", None))
        late = outcome(lambda: r.send(h2f(F_DATA, FL_END_STREAM, sid, b"late")))
        problems += problem_if(late[0] == "exc" or not r.client.usable(), "DATA on the reset stream broke the connection")
        return problems, ["5 bytes accepted, the 6th: RST_STREAM(CANCEL) written, then ChromeBodyTooLarge(limit=5); late DATA ignored"]
    run_row(suite, GE, "data-over-max-bytes-cancels-the-stream", max_bytes_row, "RFC 9113 section 6.4, CANCEL (0x8)")

    def informational_row():
        r, sid = opened(mod)
        r.send(resp_headers(sid, [(":status", "103"), ("link", "</a.css>; rel=preload")], end_stream=False)
               + resp_headers(sid, [(":status", "200"), ("content-type", "text/plain")], end_stream=False) + h2f(F_DATA, FL_END_STREAM, sid, b"hello"))
        problems = expect_value(outcome(lambda: r.client.response(sid)), (200, [("content-type", "text/plain")]))
        problems += expect_value(outcome(lambda: r.client.pop_body(sid)), b"hello")
        return problems + problem_if(not r.client.done(sid), "not done"), ["the 103 head is skipped; the final head and body are kept"]
    run_row(suite, GE, "informational-1xx-then-final-response", informational_row, "RFC 9113 section 8.1")

    for cid, head, message in (("1xx-with-end-stream", resp_headers(1, [(":status", "103")]), "h2: invalid informational response on stream 1"),
                               ("101-switching-protocols", resp_headers(1, [(":status", "101")], end_stream=False), "h2: invalid informational response on stream 1")):
        run_row(suite, GE, "informational-%s-refused" % cid, lambda head=head, message=message: (opened_refusal(mod, lambda sid: head, message, E_PROTOCOL), [message]), "RFC 9113 section 8.1 (1xx never ends a stream), 8.6 (no 101)")

    def trailers_row():
        r, sid = opened(mod)
        r.send(resp_headers(sid, [(":status", "200"), ("content-type", "text/plain")], end_stream=False) + h2f(F_DATA, 0, sid, b"abc") + resp_headers(sid, [("x-trailer", "t")]))
        problems = expect_value(outcome(lambda: r.client.response(sid)), (200, [("content-type", "text/plain")]))
        problems += expect_value(outcome(lambda: r.client.pop_body(sid)), b"abc")
        return problems + problem_if(not r.client.done(sid), "not done after the trailers"), ["trailers end the stream and are discarded"]
    run_row(suite, GE, "trailers-end-the-stream-and-are-discarded", trailers_row, "RFC 9113 section 8.1")

    for cid, trailer, message in (("without-end-stream", resp_headers(1, [("x-trailer", "t")], end_stream=False), "h2: malformed trailers on stream 1"),
                                  ("with-status", resp_headers(1, [(":status", "200")]), "h2: malformed trailers on stream 1")):
        run_row(suite, GE, "trailers-%s-refused" % cid,
                lambda trailer=trailer, message=message: (opened_refusal(mod, lambda sid: resp_headers(sid, [(":status", "200")], end_stream=False) + trailer, message, E_PROTOCOL), [message]),
                "RFC 9113 section 8.1: trailers carry END_STREAM and no pseudo-header")

    protocol_rows = (
        ("server-preface-not-settings", None, h2f(F_PING, 0, 0, bytes(8)), "h2: the server preface is not a SETTINGS frame", E_PROTOCOL, "RFC 9113 section 3.4"),
        ("push-promise", 1, h2f(F_PUSH_PROMISE, FL_END_HEADERS, 1, struct.pack("!I", 2)), "h2: PUSH_PROMISE refused (SETTINGS_ENABLE_PUSH is 0)", E_PROTOCOL, "RFC 9113 section 8.4"),
        ("data-on-a-stream-never-opened", 1, h2f(F_DATA, 0, 3, b"x"), "h2: DATA on stream 3, which the client never opened", E_PROTOCOL, "RFC 9113 section 5.1"),
        ("frame-inside-a-header-block", 1, h2f(F_HEADERS, 0, 1, b"\x00") + h2f(F_PING, 0, 0, bytes(8)), "h2: a frame interrupted a header block", E_PROTOCOL, "RFC 9113 section 6.10"),
    )
    for cid, streams, data, message, code, src in protocol_rows:
        def proto_row(streams=streams, data=data, message=message, code=code):
            r = H2Responder(mod)
            if streams is not None:
                r.opening()
                r.request()
            return r.refusal(data, message, code), [message]
        run_row(suite, GE, "protocol-%s-refused" % cid, proto_row, src)

    # F23: frames on a stream this client reset still count against the flood ceiling.
    def reset_stream_flood(unit):
        r, sid = opened(mod)
        r.send(resp_headers(sid, [(":status", "200")], end_stream=False))
        r.client.cancel(sid)
        got = outcome(lambda: r.send(unit(sid) * N))
        problems = problem_if(got[0] == "exc", "refused before the ceiling: %s" % (got[1] if got[0] == "exc" else ""))
        problems += problem_if(not problems and not r.client.usable(), "not usable after N frames")
        if problems:
            return problems
        return r.refusal(unit(sid), "h2: control frame flood (more than %d per connection)" % N, E_CALM)
    for cid, unit in (("data", lambda sid: h2f(F_DATA, 0, sid, b"x")), ("headers", lambda sid: resp_headers(sid, [(":status", "200")], end_stream=False))):
        run_row(suite, GE, "control-flood-%s-on-a-locally-reset-stream-%d-pass-then-one-refused" % (cid, N), lambda unit=unit: (reset_stream_flood(unit), ["after cancel(): %d one-byte frames accepted and counted, frame %d refused with GOAWAY(ENHANCE_YOUR_CALM)" % (N, N + 1)]),
                "CH_MAX_H2_CONTROL_FRAMES read off the module; a reset stream's frames carry no response byte")

    # F22: a regular response field the h1 reader would refuse fails ITS stream (RFC 9113 section 8.2.1), the connection lives.
    malformed = (
        ("uppercase-name", ("X-Upper", "1")),
        ("non-token-name", ("x a", "1")),
        ("name-with-colon", ("x:y", "1")),
        ("connection", ("connection", "close")),
        ("keep-alive", ("keep-alive", "300")),
        ("proxy-connection", ("proxy-connection", "close")),
        ("transfer-encoding", ("transfer-encoding", "chunked")),
        ("upgrade", ("upgrade", "h2c")),
        ("value-cr", ("x-a", "v\rX-Injected: 1")),
        ("value-lf", ("x-a", "v\nX-Injected: 1")),
        ("value-nul", ("x-a", "v\x00")),
    )
    for cid, field in malformed:
        def malformed_row(field=field):
            r, sid = opened(mod)
            out = outcome(lambda: r.send(resp_headers(sid, [(":status", "200"), field], end_stream=False)))
            if out[0] == "exc":
                return ["the connection refused it: %s" % out[1]], []
            got = outcome(lambda: r.client.response(sid))
            problems = expect_refusal(got, CE, "h2: malformed response header on stream %d" % sid)
            problems += problem_if((F_RST_STREAM, 0, sid, struct.pack("!I", E_CANCEL)) not in out[1], "no RST_STREAM on stream %d: %s" % (sid, describe(out[1])))
            problems += problem_if(not r.client.usable(), "the connection is not usable after a stream error")
            return problems, ["%r: the stream fails, RST_STREAM written, the connection stays usable" % (field[0],)]
        run_row(suite, GE, "response-field-%s-fails-the-stream" % cid, malformed_row, "RFC 9113 sections 8.2.1, 8.2.2; the h1 reader's rule")

    # F27: open_stream's own guard, behind _ch_check_caller_headers.
    base = mod._ch_profile_headers("navigate", "GET", "a.test", "/")
    for cid, extra, message in (("value-vt", ("x-a", "v\x0bx"), "h2: header contains a control character"),
                                ("value-del", ("x-a", "v\x7f"), "h2: header contains a control character"),
                                ("name-esc", ("x\x1ba", "1"), "h2: header contains a control character"),
                                ("value-leading-space", ("x-a", " v"), "h2: header value has leading or trailing whitespace"),
                                ("value-trailing-htab", ("x-a", "v\t"), "h2: header value has leading or trailing whitespace")):
        def guard_row(extra=extra, message=message):
            r = H2Responder(mod)
            r.opening()
            got = outcome(lambda: r.client.open_stream(base + [extra]))
            return expect_refusal(got, CE, message) + problem_if(r.client.usable() is not True, "the connection is not usable after a refused open_stream"), [message]
        run_row(suite, GE, "open-stream-guard-%s-refused" % cid, guard_row, "white-box: _ChH2Connection.open_stream with an unchecked list")

    def log_row():
        r = H2Responder(mod)
        r.opening()
        sid = r.request(mod._ch_profile_headers("navigate", "GET", "hidden-host.test", "/hidden-path?q=hidden"))
        r.send(resp_headers(sid, [(":status", "200"), ("set-cookie", "HIDDEN=1")], end_stream=False) + h2f(F_DATA, FL_END_STREAM, sid, b"HIDDEN-BODY"))
        leaks = [line for line in r.logs if "hidden" in line.lower()]
        return problem_if(not r.logs, "nothing logged") + problem_if(leaks, "%d log line(s) carry a host, path, header value or body byte" % len(leaks)), ["%d structure-only line(s): %r" % (len(r.logs), r.logs[:2])]
    run_row(suite, GE, "log-lines-are-structure-only", log_row, "ADR 0011")

    run_row(suite, GE, "loopback-socketpair-exchange-with-the-responder-in-a-thread", lambda: loopback_h2(mod, cc), "socket.socketpair(); the responder thread reads the client's flight and answers; 20 s socket timeouts, no timing assertion")


def opened_refusal(mod, build, message, code):
    """After the opening and one request (stream 1), feeding build(1) is refused with `message` and GOAWAY(code)."""
    r, sid = opened(mod)
    return r.refusal(build(sid), message, code)


def loopback_h2(mod, cc):
    """The same client over a real socket: the responder thread answers one navigation with a 200 and a body."""
    a, b = socket.socketpair()
    a.settimeout(20)
    b.settimeout(20)
    state = {}

    def serve():
        try:
            buf = b""
            while True:
                chunk = b.recv(65536)
                if not chunk:
                    return
                buf += chunk
                if len(buf) < len(H2_MAGIC):
                    continue
                body = buf[len(H2_MAGIC):]
                usable = 0
                while len(body) - usable >= 9:
                    n = int.from_bytes(body[usable:usable + 3], "big")
                    if len(body) - usable < 9 + n:
                        break
                    usable += 9 + n
                frames = h2_frames(body[:usable])
                heads = [f for f in frames if f[0] == F_HEADERS and f[1] & FL_END_HEADERS]
                if heads:
                    state["request"] = cc_pairs(cc.HpackDecoder(), header_fragment(heads[0]))
                    break
            b.sendall(h2f(F_SETTINGS, 0, 0) + h2f(F_SETTINGS, FL_ACK, 0) + resp_headers(1, [(":status", "200"), ("content-type", "text/plain")], end_stream=False) + h2f(F_DATA, FL_END_STREAM, 1, b"hello"))
            while b.recv(65536):
                pass
        except OSError as exc:
            state["error"] = repr(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    conn = mod._ChH2Connection()
    try:
        head = conn.preface()
        sid, data = conn.open_stream(mod._ch_profile_headers("navigate", "GET", "loopback.test", "/"))
        a.sendall(head + data)
        while not conn.done(sid):
            chunk = a.recv(65536)
            if not chunk:
                conn.close()
                break
            answer = conn.feed(chunk)
            if answer:
                a.sendall(answer)
        got = outcome(lambda: (conn.response(sid), conn.pop_body(sid)))
    finally:
        a.close()
        thread.join(20)
        b.close()
    problems = expect_value(got, ((200, [("content-type", "text/plain")]), b"hello"))
    problems += problem_if("error" in state, "responder: %s" % state.get("error"))
    problems += problem_if(dict(state.get("request") or []).get(":path") != "/", "the responder decoded %r" % (state.get("request") or [])[:4])
    return problems, ["the request decoded by chrome_capture on the far side; 200 + 'hello' read back"]


# --- F. HTTP/1.1 ----------------------------------------------------------------------

# The two HTTP/1.1 capture sets (tests/files/chrome/154/README.md "Layout"):
# set 5 over TLS with ALPN http/1.1, set 6 cleartext.
H1_SETS = (("h1-tls", "set 5"), ("h1-plain", "set 6"))

# Every refusal of _ch_split_url: (row id, URL, the exact one-line message).
SPLIT_REFUSALS = (
    ("scheme-ftp", "ftp://a.com/", "url: scheme ftp refused"),
    ("scheme-none", "a.com/x", "url: scheme (none) refused"),
    ("userinfo", "https://u:p@a.com/", "url: userinfo not supported"),
    ("userinfo-empty-at", "https://@a.com/", "url: userinfo not supported"),
    ("no-host", "https:///x", "url: no host"),
    ("port-0", "https://a.com:0/", "url: invalid port"),
    ("port-70000", "https://a.com:70000/", "url: malformed authority"),
    ("port-25-fetch-bad-port", "http://a.com:25/", "url: port 25 refused"),
    ("idna-deviation-sharp-s", "https://stra" + chr(0x00DF) + "e.test/", "url: host is not a valid IDNA name"),
    ("idna-deviation-final-sigma", "https://" + chr(0x03BF) + chr(0x03C2) + ".test/", "url: host is not a valid IDNA name"),
    ("idna-deviation-zwj", "https://a" + chr(0x200D) + "b.test/", "url: host is not a valid IDNA name"),
    ("idna-deviation-zwnj", "https://a" + chr(0x200C) + "b.test/", "url: host is not a valid IDNA name"),
    ("ipv6-zone-id", "https://[fe80::1%25en0]/", "url: IPv6 zone id not supported"),
    ("ipv6-invalid", "https://[v1.fe]/", "url: invalid IPv6 literal"),
    ("host-percent-cr", "https://a%0d.com/", "url: invalid host"),
    ("host-empty-label", "https://a..com/", "url: invalid host"),
    ("not-a-str", b"https://a.com/", "url: the URL must be a str"),
)

# What _ch_split_url returns: (row id, URL, (scheme, host, port, target)).
SPLIT_VALUES = (
    ("https-default-443-fragment-dropped", "HTTPS://Example.COM/a?b=1#frag", ("https", "example.com", 443, "/a?b=1")),
    ("http-default-80-empty-path", "http://example.com", ("http", "example.com", 80, "/")),
    ("explicit-port", "http://example.com:8080/x", ("http", "example.com", 8080, "/x")),
    ("explicit-port-443-not-a-bad-port", "http://example.com:443/x", ("http", "example.com", 443, "/x")),
    ("trailing-dot-kept", "https://example.com./", ("https", "example.com.", 443, "/")),
    ("bracketed-ipv6-compressed", "https://[2001:DB8:0:0::1]:8443/p", ("https", "2001:db8::1", 8443, "/p")),
    ("idna", "https://Bücher.de/", ("https", "xn--bcher-kva.de", 443, "/")),
    ("idna-trailing-dot-kept", "https://bücher.de./", ("https", "xn--bcher-kva.de.", 443, "/")),
    ("target-percent-encoded-utf8", "http://a.com/a b/é?q=ü x", ("http", "a.com", 80, "/a%20b/%C3%A9?q=%C3%BC%20x")),
    ("query-only", "http://a.com?x=1", ("http", "a.com", 80, "/?x=1")),
)

H1_CRLF = "h1: header contains CR, LF or NUL"


def h1_fixtures(label):
    """(file name, first request) of every committed capture in tests/files/chrome/154/<label>/."""
    ref = os.path.join(FIXTURES, label)
    out = []
    for name in sorted(n for n in os.listdir(ref) if n.endswith(".json")):
        with open(os.path.join(ref, name), encoding="utf-8") as fh:
            out.append((name, json.load(fh)["h1"]["requests"][0]))
    return out


def fixture_host(req):
    return [hd["value"] for hd in req["headers"] if hd["name"] == "Host"][0]


def h1_server_context():
    """server_context() with ALPN http/1.1 only: the ALPN h1-tls/ negotiated."""
    ctx = server_context()
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


def h1_serve(lsock, responses, state, ctx):
    """Thread body: accept one connection, answer each request head with the next canned response, record every byte read."""
    try:
        conn, _addr = lsock.accept()
        conn.settimeout(20)
        if ctx is not None:
            conn = ctx.wrap_socket(conn, server_side=True)
            state["alpn"] = conn.selected_alpn_protocol()
        buf = b""
        for resp in responses:
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                state["raw"] = state.get("raw", b"") + chunk
                buf += chunk
            if b"\r\n\r\n" not in buf:
                break
            head, _sep, buf = buf.partition(b"\r\n\r\n")
            state["heads"].append(head)
            if b"\r\nContent-Length: " in head:
                n = int(head.split(b"\r\nContent-Length: ")[1].split(b"\r\n")[0])
                while len(buf) < n:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    state["raw"] = state.get("raw", b"") + chunk
                    buf += chunk
                state["bodies"].append(buf[:n])
                buf = buf[n:]
            if resp is not None:
                conn.sendall(resp)
        if ctx is not None:
            try:
                conn.unwrap()
            except (OSError, ValueError):
                pass
        conn.close()
    except (OSError, ValueError) as exc:
        state["error"] = repr(exc)


def h1_request_headers(mod, method, authority, body):
    """The profile header list of one loopback request: a navigation, a cors HEAD, or a cors form POST of `body`."""
    if method == "HEAD":
        return mod._ch_profile_headers("cors", "HEAD", authority, "/")
    if method == "POST":
        return mod._ch_profile_headers("cors", "POST", authority, "/lite/", referer="https://%s/lite/" % authority,
                                       extra=[("Accept", "*/*")], content_length=len(body))
    return mod._ch_profile_headers("navigate", "GET", authority, "/")


def h1_exchange(mod, responses, nreq, ctx, max_bytes=0, method="GET", body=None):
    """(results, state) of `nreq` requests on ONE _ChH1Connection to a scripted peer; a refusal ends the list."""
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]
    state = {"heads": [], "bodies": [], "usable": [], "port": port, "logs": []}
    thread = threading.Thread(target=h1_serve, args=(lsock, responses, state, ctx), daemon=True)
    thread.start()
    results = []
    conn = None
    try:
        sock = socket.create_connection(lsock.getsockname(), timeout=20)
        if ctx is not None:
            transport = mod._ChTlsStream(sock, mod._ChTls("localhost"))
            state["client_alpn"] = transport.handshake()
        else:
            transport = sock
        conn = mod._ChH1Connection(transport, log=state["logs"].append)
        for _ in range(nreq):
            hdrs = h1_request_headers(mod, method, "localhost:%d" % port, body)
            results.append(conn.request(hdrs, body, max_bytes))
            state["usable"].append(conn.usable())
    except Exception as exc:  # the rows judge the type and message
        results.append(exc)
    finally:
        if conn is not None:
            conn.close()
        thread.join(20)
        lsock.close()
    return results, state


def h1_body(results, index, want):
    got = results[index] if index < len(results) else None
    if isinstance(got, Exception):
        return ["request %d raised %s(%r)" % (index, type(got).__name__, str(got)[:160])]
    if not isinstance(got, tuple):
        return ["request %d has no result" % index]
    return problem_if(got[3] != want, "request %d body %s, wanted %s" % (index, short(got[3]), short(want)))


def h1_refused(mod, results, message):
    last = results[-1] if results else None
    if not isinstance(last, Exception):
        return ["the last request returned %s instead of raising" % short(last)]
    return expect_refusal(("exc", last), mod.ChromeClientError, message)


def h1_too_large(mod, results, limit):
    last = results[-1] if results else None
    if type(last) is not mod.ChromeBodyTooLarge:
        return ["the last request gave %s, wanted ChromeBodyTooLarge" % (short(last) if not isinstance(last, Exception) else "%s(%r)" % (type(last).__name__, str(last)[:160]))]
    return problem_if(last.limit != limit, ".limit is %r, wanted %d" % (last.limit, limit))


def h1_reuse(state, want):
    return problem_if(state["usable"] != want, "usable() after each response %r, wanted %r" % (state["usable"], want))


def h1_cases(mod):
    """(row id, responses, requests, max_bytes, method, judge(results, state)) of every loopback exchange."""
    hb = mod.CH_MAX_H1_HEAD_BYTES
    nh = mod.CH_MAX_H1_HEADERS
    cl = mod.CH_MAX_H1_CHUNK_LINE_BYTES
    mb = mod.CH_MAX_BODY_BYTES
    ok = b"HTTP/1.1 200 OK\r\n"
    zero = b"Content-Length: 0\r\n"
    te = ok + b"Transfer-Encoding: chunked\r\n\r\n"
    lines = b"".join(b"X-%d: v\r\n" % i for i in range(nh - 1))
    ext_at = b"2;" + b"e" * (cl - 2)
    trailers_at = b"".join(b"T-%d: v\r\n" % i for i in range(nh - 1))
    chunked = te + b"5;ext=1;foo=\"bar\"\r\nhello\r\n6\r\n world\r\n0\r\nX-Trailer: 1\r\n\r\n"

    def head_of(total):
        pad = total - len(ok) - len(zero) - len(b"X-Pad: \r\n") - 2
        return ok + zero + b"X-Pad: " + b"a" * pad + b"\r\n\r\n"
    return (
        ("content-length-two-requests-one-connection",
         [ok + b"Content-Length: 5\r\n\r\nhello", ok + b"content-length: 3\r\n\r\nabc"], 2, 0, "GET",
         lambda r, s: h1_body(r, 0, b"hello") + h1_body(r, 1, b"abc") + h1_reuse(s, [True, True])
         + problem_if(len(s["heads"]) != 2, "the peer read %d request heads on the connection, wanted 2" % len(s["heads"]))),
        ("chunked-extensions-and-trailer-then-204",
         [chunked, b"HTTP/1.1 204 No Content\r\n\r\n"], 2, 0, "GET",
         lambda r, s: h1_body(r, 0, b"hello world") + h1_body(r, 1, b"") + h1_reuse(s, [True, True])),
        ("log-structure-only",
         [chunked], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"hello world") + problem_if(s["logs"] != ["h1: status=200 framing=chunked body=11B reuse=1"], "log lines %r" % s["logs"])),
        ("eof-delimited-body-not-reused",
         [ok + b"\r\nuntil eof"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"until eof") + h1_reuse(s, [False])),
        ("interim-100-and-103-consumed",
         [b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 103 Early Hints\r\nLink: </a>\r\n\r\n" + ok + b"Content-Length: 2\r\n\r\nok"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"ok") + h1_reuse(s, [True])),
        ("connection-close-not-reused",
         [ok + b"Content-Length: 5\r\nConnection: close\r\n\r\nhello"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"hello") + h1_reuse(s, [False])),
        ("http-1.0-not-reused",
         [b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"ok") + h1_reuse(s, [False])),
        ("head-response-has-no-body-reused",
         [ok + b"Content-Length: 5\r\n\r\n"], 1, 0, "HEAD",
         lambda r, s: h1_body(r, 0, b"") + h1_reuse(s, [True])),
        ("cors-post-body-on-the-wire",
         [ok + b"Content-Length: 2\r\n\r\nok"], 1, 0, "POST",
         lambda r, s: h1_body(r, 0, b"ok") + problem_if(s["bodies"] != [b"q=test&kl="], "the peer read the bodies %r" % s["bodies"])
         + problem_if(not s["heads"] or b"\r\nContent-Length: 10" not in s["heads"][0] or b"riority" in s["heads"][0], "the POST head carries no Content-Length: 10 or a Priority")),
        ("transfer-encoding-beats-content-length-not-reused",
         [ok + b"Transfer-Encoding: chunked\r\nContent-Length: 3\r\n\r\n3\r\nabc\r\n0\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"abc") + h1_reuse(s, [False])),
        ("leftover-bytes-not-reused",
         [ok + zero + b"\r\nEXTRA"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"") + h1_reuse(s, [False])),
        ("head-at-CH_MAX_H1_HEAD_BYTES-accepted",
         [head_of(hb)], 1, 0, "GET",
         lambda r, s: problem_if(len(head_of(hb)) != hb, "the planted head is %d bytes" % len(head_of(hb))) + h1_body(r, 0, b"")),
        ("head-over-CH_MAX_H1_HEAD_BYTES-refused",
         [head_of(hb + 1)], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: response head exceeds %d bytes" % hb)),
        ("CH_MAX_H1_HEADERS-lines-accepted",
         [ok + zero + lines + b"\r\n"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"")),
        ("CH_MAX_H1_HEADERS-plus-1-refused",
         [ok + zero + lines + b"X-last: v\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: more than %d header lines" % nh)),
        ("obs-fold-refused",
         [ok + b"X-A: 1\r\n folded\r\n" + zero + b"\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: obs-fold continuation line refused (RFC 9112 section 5.2)")),
        ("bad-chunk-size-refused",
         [te + b"zz\r\nab\r\n0\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: invalid chunk size")),
        ("chunk-line-with-extensions-at-CH_MAX_H1_CHUNK_LINE_BYTES-accepted",
         [te + ext_at + b"\r\nab\r\n0\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"ab")),
        ("chunk-line-with-extensions-over-CH_MAX_H1_CHUNK_LINE_BYTES-refused",
         [te + ext_at + b"e\r\nab\r\n0\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: chunk-size line exceeds %d bytes" % cl)),
        ("trailer-over-its-fresh-head-budget-refused",
         [te + b"0\r\nX-T: " + b"t" * hb + b"\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: trailer section exceeds %d bytes" % hb)),
        ("trailer-lines-at-the-shared-count-accepted",
         [te + b"0\r\n" + trailers_at + b"\r\n"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"")),
        ("trailer-lines-over-the-shared-count-refused",
         [te + b"0\r\n" + trailers_at + b"T-last: v\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: more than %d header lines" % nh)),
        ("content-length-at-max_bytes-accepted",
         [ok + b"Content-Length: 10\r\n\r\nhelloworld"], 1, 10, "GET",
         lambda r, s: h1_body(r, 0, b"helloworld")),
        ("content-length-over-max_bytes-too-large",
         [ok + b"Content-Length: 11\r\n\r\nhello world"], 1, 10, "GET",
         lambda r, s: h1_too_large(mod, r, 10)),
        ("chunked-over-max_bytes-too-large",
         [te + b"6\r\nhello \r\n5\r\nworld\r\n0\r\n\r\n"], 1, 10, "GET",
         lambda r, s: h1_too_large(mod, r, 10)),
        ("eof-delimited-over-max_bytes-too-large",
         [ok + b"\r\nhello world"], 1, 10, "GET",
         lambda r, s: h1_too_large(mod, r, 10)),
        ("content-length-over-CH_MAX_BODY_BYTES-too-large-before-reading",
         [ok + b"Content-Length: %d\r\n\r\n" % (mb + 1)], 1, 0, "GET",
         lambda r, s: h1_too_large(mod, r, mb)),
        ("chunk-size-over-CH_MAX_BODY_BYTES-too-large-before-reading",
         [te + b"%x\r\n" % (mb + 1)], 1, 0, "GET",
         lambda r, s: h1_too_large(mod, r, mb)),
        ("conflicting-content-length-refused",
         [ok + b"Content-Length: 3\r\nContent-Length: 4\r\n\r\nabcd"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: invalid or conflicting content-length")),
        ("transfer-encoding-gzip-chunked-refused",
         [ok + b"Transfer-Encoding: gzip, chunked\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: transfer-encoding other than chunked refused")),
        ("status-101-refused",
         [b"HTTP/1.1 101 Switching Protocols\r\n\r\n"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: unexpected 101 Switching Protocols")),
        ("short-content-length-body-refused",
         [ok + b"Content-Length: 10\r\n\r\nabc"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: connection closed before the body ended")),
        ("content-length-of-19-digits-accepted",
         [ok + b"Content-Length: 0000000000000000001\r\n\r\nx"], 1, 0, "GET",
         lambda r, s: h1_body(r, 0, b"x")),
        ("content-length-of-20-digits-refused-before-int",
         [ok + b"Content-Length: 00000000000000000001\r\n\r\nx"], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: invalid or conflicting content-length")),
        ("eof-before-the-response-refused",
         [None], 1, 0, "GET",
         lambda r, s: h1_refused(mod, r, "h1: connection closed before the response")),
    )


def h1_transport_rows(suite, mod, tag, ctx):
    """Every loopback exchange over one transport: `tls` (Python ssl, ALPN http/1.1) or `plain` (a TCP socket)."""
    src = ("a scripted HTTP/1.1 peer in a thread behind %s; RFC 9112" %
           ("a TLS 1.3-only Python ssl server (tests/files/tls leaf, ALPN http/1.1) and _ChTlsStream" if ctx is not None else "a cleartext loopback socket"))
    if ctx is not None:
        def alpn():
            _r, s = h1_exchange(mod, [ok_response()], 1, ctx)
            return problem_if(s.get("alpn") != "http/1.1" or s.get("client_alpn") != "http/1.1", "ALPN server %r / client %r" % (s.get("alpn"), s.get("client_alpn"))), ["server %r, client %r" % (s.get("alpn"), s.get("client_alpn"))]
        run_row(suite, GF, "h1-tls-alpn-http1.1-both-sides", alpn, src)
    label = "h1-tls" if ctx is not None else "h1-plain"

    def wire():
        _name, req = h1_fixtures(label)[0]
        r, s = h1_exchange(mod, [ok_response()], 1, ctx)
        want = req["request_head"].encode("latin-1") + b"\r\n\r\n"
        got = (s["heads"][0] + b"\r\n\r\n").replace(b"localhost:%d" % s["port"], fixture_host(req).encode("latin-1")) if s["heads"] else b""
        return h1_body(r, 0, b"") + problem_if(got != want, "the head on the wire differs from %s/%s: %r" % (label, _name, got[:160])), ["modulo host:port; %s/%s" % (label, _name)]
    run_row(suite, GF, "h1-%s-request-head-on-the-wire-vs-%s" % (tag, label), wire, src)
    for cid, responses, nreq, max_bytes, method, judge in h1_cases(mod):
        def row(responses=responses, nreq=nreq, max_bytes=max_bytes, method=method, judge=judge):
            body = b"q=test&kl=" if method == "POST" else None
            r, s = h1_exchange(mod, responses, nreq, ctx, max_bytes, method, body)
            return judge(r, s), ["%s; max_bytes=%d" % (method, max_bytes)]
        run_row(suite, GF, "h1-%s-%s" % (tag, cid), row, src)


def ok_response():
    return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"


class H1Recorder:
    """A transport that records every byte written and never answers."""

    def __init__(self):
        self.sent = b""
        self.closed = False

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        return b""

    def close(self):
        self.closed = True


def h1_guard_rows(suite, mod):
    """White-box: _ChH1Connection driven directly with header lists _ch_check_caller_headers never saw."""
    base = mod._ch_profile_headers("navigate", "GET", "a.com", "/")
    src = "white-box: _ChH1Connection(recorder).send_request(<an unchecked list>); its own guard, not _ch_check_caller_headers"
    cases = (
        ("value-crlf-injection", base + [("x-a", "v\r\nX-Injected: 1")], None, H1_CRLF),
        ("name-crlf-injection", base + [("x-a\r\nX-Injected", "1")], None, H1_CRLF),
        ("value-nul", base + [("x-a", "v\0")], None, H1_CRLF),
        ("authority-crlf", [(":method", "GET"), (":authority", "a.com\r\nX-Injected: 1"), (":path", "/")], None, H1_CRLF),
        ("path-lf", [(":method", "GET"), (":authority", "a.com"), (":path", "/\nX-Injected: 1")], None, H1_CRLF),
        ("caller-transfer-encoding-chunked", base + [("transfer-encoding", "chunked")], None, "h1: transfer-encoding is connection framing, owned by the h1 connection"),
        ("caller-content-length-0-without-body", base + [("content-length", "0")], None, "h1: content-length does not match the request body"),
        ("content-length-mismatch", base + [("content-length", "3")], b"ab", "h1: content-length does not match the request body"),
        ("body-without-content-length", base, b"ab", "h1: a request body needs its content-length"),
        ("caller-host", base + [("host", "evil")], None, "h1: host is connection framing, owned by the h1 connection"),
        ("no-path", [(":method", "GET"), (":authority", "a.com")], None, "h1: request has no :path"),
        ("value-vt", base + [("x-a", "v\x0bx")], None, "h1: header contains a control character"),
        ("value-esc", base + [("x-a", "\x1b[2J")], None, "h1: header contains a control character"),
        ("value-del", base + [("x-a", "v\x7f")], None, "h1: header contains a control character"),
        ("name-esc", base + [("x\x1ba", "1")], None, "h1: header contains a control character"),
        ("value-leading-space", base + [("x-a", " v")], None, "h1: header value has leading or trailing whitespace"),
        ("value-trailing-htab", base + [("x-a", "v\t")], None, "h1: header value has leading or trailing whitespace"),
    )
    for cid, hdrs, body, message in cases:
        def row(hdrs=hdrs, body=body, message=message):
            rec = H1Recorder()
            conn = mod._ChH1Connection(rec)
            got = outcome(lambda: conn.send_request(hdrs, body))
            problems = expect_refusal(got, mod.ChromeClientError, message)
            problems += problem_if(rec.sent != b"", "%d byte(s) were written before the refusal" % len(rec.sent))
            if got[0] == "exc":
                text = str(got[1])
                problems += problem_if("\r" in text or "\n" in text or "Injected" in text, "the message is not one non-echoing line: %r" % text)
            return problems, ["zero bytes written, one line, no header byte echoed"]
        run_row(suite, GF, "h1-guard-%s" % cid, row, src)

    def ast_guard():
        with open(SOURCE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ChH1Connection"]
        build = [n for n in (cls[0].body if cls else ()) if isinstance(n, ast.FunctionDef) and n.name == "_build"]
        if not build:
            return ["_ChH1Connection._build not found"], []
        asserts = [n.lineno for n in ast.walk(build[0]) if isinstance(n, ast.Assert)]
        guarded = [n.lineno for n in ast.walk(build[0]) if isinstance(n, ast.If)
                   for r in n.body if isinstance(r, ast.Raise)
                   for c in ast.walk(r) if isinstance(c, ast.Constant) and c.value == H1_CRLF]
        return (problem_if(asserts, "assert at line(s) %r: python3 -O strips it" % asserts)
                + problem_if(not guarded, "no explicit `if ...: raise ChromeClientError(%r)`" % H1_CRLF)), ["the guard `if` at line(s) %r" % guarded]
    run_row(suite, GF, "h1-guard-is-an-explicit-if-never-an-assert", ast_guard, "AST of _ChH1Connection._build; group K's no-assert rule covers the whole file")


def h1_injection_rows(suite, mod):
    """Request-side injection through the profile path: refused BEFORE any socket opens (R2-M4)."""
    src = "_ch_profile_headers(extra=...) -> socket.create_connection -> _ChH1Connection.request, in that order; a listening peer counts connections"
    cases = (
        ("value-crlf", [("x-a", "v\r\nX-Injected: 1")], "headers: 'x-a' contains CR, LF or NUL", True),
        ("name-crlf", [("x-a\r\nX-Injected", "1")], "headers: %s contains CR, LF or NUL" % repr("x-a\r\nX-Injected")[:40], False),
        ("value-nul", [("x-a", "v\0")], "headers: 'x-a' contains CR, LF or NUL", True),
        ("transfer-encoding-chunked", [("Transfer-Encoding", "chunked")], "headers: transfer-encoding is connection framing, owned by the Chrome profile", True),
        ("content-length-0", [("Content-Length", "0")], "headers: content-length is connection framing, owned by the Chrome profile", True),
    )
    for cid, extra, message, no_echo in cases:
        def row(extra=extra, message=message, no_echo=no_echo):
            lsock = socket.socket()
            lsock.bind(("127.0.0.1", 0))
            lsock.listen(1)
            addr = lsock.getsockname()

            def pipeline():
                hdrs = mod._ch_profile_headers("navigate", "GET", "127.0.0.1:%d" % addr[1], "/", extra=extra)
                conn = mod._ChH1Connection(socket.create_connection(addr, timeout=20))
                try:
                    return conn.request(hdrs)
                finally:
                    conn.close()
            try:
                got = outcome(pipeline)
                lsock.setblocking(False)
                try:
                    peer, _a = lsock.accept()
                except BlockingIOError:
                    peer = None
                seen = b""
                if peer is not None:
                    peer.settimeout(2)
                    try:
                        seen = peer.recv(65536)
                    except OSError:
                        pass
                    peer.close()
            finally:
                lsock.close()
            problems = expect_refusal(got, mod.ChromeClientError, message)
            problems += problem_if(peer is not None, "the peer accepted a connection and read %d byte(s)" % len(seen))
            if got[0] == "exc":
                text = str(got[1])
                problems += problem_if("\r" in text or "\n" in text or (no_echo and "Injected" in text), "the message is not one non-echoing line: %r" % text)
            return problems, ["no connection accepted, zero bytes on the wire"]
        run_row(suite, GF, "h1-caller-%s-refused-before-the-socket-opens" % cid, row, src)


def fin_without_close_notify(lsock, response, state):
    """Thread body: one TLS 1.3 connection (ALPN http/1.1) answered with `response`, then a TCP FIN and NO close_notify."""
    try:
        raw, _addr = lsock.accept()
        raw.settimeout(20)
        conn = h1_server_context().wrap_socket(raw, server_side=True)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        conn.sendall(response)
        # SSLSocket.shutdown drops the TLS object first: a bare TCP FIN, the truncation a party on the path forges.
        conn.shutdown(socket.SHUT_WR)
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass
        conn.close()
    except (OSError, ValueError) as exc:
        state["error"] = repr(exc)


def h1_truncation_rows(suite, mod):
    """F7: over the Chrome TLS engine an EOF-delimited body ended by a bare FIN is refused, never returned whole."""
    src = "_ChTlsStream.eof_without_close_notify read by _ChH1Connection.read_body; a Python ssl TLS 1.3 server that FINs without close_notify"

    def exchange(response):
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        state = {}
        thread = threading.Thread(target=fin_without_close_notify, args=(lsock, response, state), daemon=True)
        thread.start()
        conn = None
        try:
            sock = socket.create_connection(lsock.getsockname(), timeout=20)
            transport = mod._ChTlsStream(sock, mod._ChTls("localhost"))
            transport.handshake()
            conn = mod._ChH1Connection(transport)
            got = outcome(lambda: conn.request(mod._ch_profile_headers("navigate", "GET", "localhost", "/")))
            return got, transport
        finally:
            if conn is not None:
                conn.close()
            thread.join(20)
            lsock.close()

    def eof_body():
        got, transport = exchange(b"HTTP/1.1 200 OK\r\n\r\nuntil eof")
        problems = expect_refusal(got, mod.ChromeClientError, "h1: truncated: EOF-delimited body ended without close_notify")
        return problems + problem_if(not transport.eof_without_close_notify, "the stream did not record the bare FIN"), ["an EOF-delimited 200 then FIN, no close_notify: refused (the h1-tls eof-delimited row is the close_notify control)"]
    run_row(suite, GF, "h1-tls-eof-delimited-body-without-close-notify-refused", eof_body, src)

    def length_body():
        got, _transport = exchange(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
        return expect_value(("value", got[1][3]) if got[0] == "value" else got, b"hello"), ["a Content-Length body read whole before the FIN: the length, not close_notify, delimits it"]
    run_row(suite, GF, "h1-tls-content-length-body-then-fin-without-close-notify-accepted", length_body, src)


def group_h1(suite, mod):
    """Group F: HTTP/1.1 over TLS (ALPN http/1.1) and cleartext; request heads vs sets 5/6; the guards."""
    src = "_ch_split_url; tests/files/chrome/154/README.md (http:// is port 80, https:// 443)"
    for cid, url, want in SPLIT_VALUES:
        run_row(suite, GF, "split-url-%s" % cid, lambda url=url, want=want: (expect_value(outcome(lambda: mod._ch_split_url(url)), want), ["%r" % (want,)]), src)
    for cid, url, message in SPLIT_REFUSALS:
        run_row(suite, GF, "split-url-%s-refused" % cid, lambda url=url, message=message: (expect_refusal(outcome(lambda: mod._ch_split_url(url)), mod.ChromeClientError, message), [message]), src)

    for label, set_name in H1_SETS:
        def heads(label=label, set_name=set_name):
            problems = []
            fixtures = h1_fixtures(label)
            for name, req in fixtures:
                hdrs = mod._ch_profile_headers("navigate", "GET", fixture_host(req), req["target"])
                got = outcome(lambda: mod._ChH1Connection(None).request_head(hdrs))
                problems += expect_value(got, req["request_head"].encode("latin-1") + b"\r\n\r\n")
                if problems:
                    return ["%s/%s: %s" % (label, name, problems[0])], []
            return problem_if(not fixtures, "no %s fixtures" % label), ["%d of %d first requests byte-identical (%s)" % (len(fixtures), len(fixtures), set_name)]
        run_row(suite, GF, "request-head-vs-%s-%s" % (set_name.replace(" ", "-"), label), heads, "tests/files/chrome/154/%s/*.json h1.requests[0].request_head, byte for byte" % label)

    def post_head():
        hdrs = mod._ch_profile_headers("cors", "POST", "a.com", "/lite/", referer="https://a.com/lite/", extra=[("Accept", "*/*")], content_length=10)
        head = mod._ChH1Connection(None).request_head(hdrs, b"q=test&kl=")
        return (problem_if(not head.startswith(b"POST /lite/ HTTP/1.1\r\nHost: a.com\r\nConnection: keep-alive\r\n"), "request line / Host / Connection %r" % head[:80])
                + problem_if(b"\r\nContent-Length: 10\r\n" not in head, "no Content-Length: 10")
                + problem_if(b"\r\nContent-Type: %s\r\n" % mod.CHROME_PROFILE["cors_post_content_type"].encode("latin-1") not in head, "no profile Content-Type")
                + problem_if(b"riority" in head, "a Priority header over HTTP/1.1")), ["no h1 POST was captured: structure only"]
    run_row(suite, GF, "request-head-cors-post-content-length-no-priority", post_head, "CHROME_PROFILE h1_order; Chrome sends no Priority over HTTP/1.1")

    h1_guard_rows(suite, mod)
    h1_injection_rows(suite, mod)
    h1_transport_rows(suite, mod, "plain", None)
    h1_transport_rows(suite, mod, "tls", h1_server_context())
    h1_truncation_rows(suite, mod)


# --- G. session ---------------------------------------------------------------------

ENC = H.repo_path("tests", "files", "enc")
BROTLI_SOURCE = H.repo_path("Scripts", "_mcp_brotli.py")
ZSTD_SOURCE = H.repo_path("Scripts", "_mcp_zstd.py")

# Every loopback session call gets this many seconds: generous, never a verdict.
SESSION_TIMEOUT = 20

# The proxy variables a session must never read (R-0017: no *_PROXY, ever).
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


class SessionPeer:
    """A threaded loopback server for group G: h2 over TLS ("h2"), HTTP/1.1 over TLS ("h1-tls") or cleartext ("h1-plain").

    Written here from RFC 9113 and RFC 9112, not from the module: request
    heads are decoded by chrome_capture's HpackDecoder (one per connection,
    as HPACK state is), responses are hp_block literals and h2f frames.
    Every request is recorded as {conn, sid, method, path, authority,
    headers (ordered, pseudo-headers dropped), body}. `plan` maps a path to
    (status, fields, body); `/r/<code>?<url>` redirects to the query,
    `/chain/<k>` redirects k times before a 200 "end"; anything else is a
    200 "echo". Any host name reaches it: the connect policy answers
    127.0.0.1 and the client verifies no certificate.
    """

    def __init__(self, kind, cc):
        self.kind = kind
        self.cc = cc
        self.ctx = None if kind == "h1-plain" else (server_context() if kind == "h2" else h1_server_context())
        self.lsock = socket.socket()
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(32)
        self.lsock.settimeout(0.25)
        self.port = self.lsock.getsockname()[1]
        self.conns = 0
        self.requests = []
        self.errors = []
        self.plan = {}
        self.closed = False
        # Group I: the SNI of every TLS handshake, every RST_STREAM the client sent (conn, sid, code), the DATA
        # bytes written per (conn, sid), and the paths whose h2 body is paced frame by frame (on_headers rows).
        self.snis = []
        self.rsts = []
        self.data_sent = {}
        self.paced = set()
        if self.ctx is not None:
            self.ctx.sni_callback = self._sni
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _sni(self, _sslobj, name, _ctx):
        self.snis.append(name)

    def close(self):
        self.closed = True
        self.thread.join(5)
        self.lsock.close()

    def mark(self):
        return len(self.requests), self.conns

    def since(self, mark):
        return self.requests[mark[0]:], self.conns - mark[1]

    def _accept(self):
        while not self.closed:
            try:
                sock, _addr = self.lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.conns += 1
            threading.Thread(target=self._serve, args=(sock, self.conns), daemon=True).start()

    def _serve(self, sock, cid):
        try:
            sock.settimeout(SESSION_TIMEOUT)
            if self.ctx is not None:
                sock = self.ctx.wrap_socket(sock, server_side=True)
            if self.kind == "h2":
                self._h2(sock, cid)
            else:
                self._h1(sock, cid)
        except Exception as exc:  # a server thread records, never prints a traceback
            self.errors.append("%s(%r)" % (type(exc).__name__, str(exc)[:120]))
        finally:
            sock.close()

    def answer(self, path):
        base, _sep, query = path.partition("?")
        if base in self.plan:
            return self.plan[base]
        if base.startswith("/r/"):
            return int(base[3:]), [("location", query)], b""
        if base.startswith("/chain/"):
            left = int(base[7:])
            if left > 0:
                return 302, [("location", "/chain/%d" % (left - 1))], b""
            return 200, [("content-type", "text/plain")], b"end"
        return 200, [("content-type", "text/plain")], b"echo"

    def _record(self, cid, sid, method, path, authority, headers, body):
        self.requests.append({"conn": cid, "sid": sid, "method": method, "path": path, "authority": authority, "headers": headers, "body": body})

    def _h1(self, sock, cid):
        buf = b""
        while True:
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            head, _sep, buf = buf.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = [tuple(line.split(": ", 1)) for line in lines[1:]]
            n = int(dict((k.lower(), v) for k, v in headers).get("content-length", "0"))
            while len(buf) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            body, buf = buf[:n], buf[n:]
            self._record(cid, None, method, path, dict((k.lower(), v) for k, v in headers).get("host"), headers, body)
            status, fields, rbody = self.answer(path)
            out = "HTTP/1.1 %d X\r\n" % status + "".join("%s: %s\r\n" % (k, v) for k, v in fields) + "Content-Length: %d\r\n\r\n" % len(rbody)
            sock.sendall(out.encode("latin-1") + (b"" if method == "HEAD" else rbody))

    def _h2(self, sock, cid):
        decoder = self.cc.HpackDecoder()
        buf = b""
        while len(buf) < len(H2_MAGIC):
            chunk = sock.recv(65536)
            if not chunk:
                return
            buf += chunk
        if buf[:len(H2_MAGIC)] != H2_MAGIC:
            raise ValueError("no h2 connection preface")
        buf = buf[len(H2_MAGIC):]
        sock.sendall(h2f(F_SETTINGS, 0, 0))
        streams = {}
        while True:
            while len(buf) < 9 or len(buf) < 9 + int.from_bytes(buf[:3], "big"):
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            n = int.from_bytes(buf[:3], "big")
            frame = h2_frames(buf[:9 + n])[0]
            buf = buf[9 + n:]
            ftype, flags, sid, payload = frame
            if ftype == F_SETTINGS and not flags & FL_ACK:
                sock.sendall(h2f(F_SETTINGS, FL_ACK, 0))
            elif ftype == F_PING and not flags & FL_ACK:
                sock.sendall(h2f(F_PING, FL_ACK, 0, payload))
            elif ftype == F_GOAWAY:
                return
            elif ftype == F_RST_STREAM:
                self.rsts.append((cid, sid, int.from_bytes(payload[:4], "big")))
            elif ftype == F_HEADERS:
                streams[sid] = {"block": header_fragment(frame), "end": bool(flags & FL_END_STREAM), "body": b"", "fields": None}
                if flags & FL_END_HEADERS:
                    self._h2_head(sock, cid, sid, streams, decoder)
            elif ftype == F_CONTINUATION and sid in streams:
                streams[sid]["block"] += payload
                if flags & FL_END_HEADERS:
                    self._h2_head(sock, cid, sid, streams, decoder)
            elif ftype == F_DATA and sid in streams:
                streams[sid]["body"] += payload
                if flags & FL_END_STREAM:
                    self._h2_answer(sock, cid, sid, streams.pop(sid))

    def _h2_head(self, sock, cid, sid, streams, decoder):
        st = streams[sid]
        st["fields"] = cc_pairs(decoder, st["block"])
        if st["end"]:
            self._h2_answer(sock, cid, sid, streams.pop(sid))

    def _h2_answer(self, sock, cid, sid, st):
        pseudo = dict((k, v) for k, v in st["fields"] if k.startswith(":"))
        headers = [(k, v) for k, v in st["fields"] if not k.startswith(":")]
        method, path = pseudo.get(":method"), pseudo.get(":path")
        self._record(cid, sid, method, path, pseudo.get(":authority"), headers, st["body"])
        status, fields, rbody = self.answer(path)
        pairs = [(":status", str(status))] + [(k.lower(), v) for k, v in fields]
        if method == "HEAD" or not rbody:
            sock.sendall(resp_headers(sid, pairs, end_stream=True))
            return
        if path.partition("?")[0] in self.paced:
            self._h2_paced(sock, cid, sid, pairs, rbody)
            return
        out = resp_headers(sid, pairs, end_stream=False)
        for off in range(0, len(rbody), 16384):
            out += h2f(F_DATA, FL_END_STREAM if off + 16384 >= len(rbody) else 0, sid, rbody[off:off + 16384])
        sock.sendall(out)

    def _h2_paced(self, sock, cid, sid, pairs, rbody):
        """The head, then one 16 KiB DATA frame per PACE_S, stopping as soon as the client wrote anything (its RST_STREAM)."""
        sock.sendall(resp_headers(sid, pairs, end_stream=False))
        self.data_sent[(cid, sid)] = 0
        for off in range(0, len(rbody), 16384):
            if sock.pending() or select.select([sock], [], [], PACE_S)[0]:
                return
            chunk = rbody[off:off + 16384]
            sock.sendall(h2f(F_DATA, FL_END_STREAM if off + 16384 >= len(rbody) else 0, sid, chunk))
            self.data_sent[(cid, sid)] += len(chunk)


# Group I: the gap between two paced DATA frames; a pause, never a verdict.
PACE_S = 0.02


def values(req, name):
    """Every value of header `name` in one recorded request, case-insensitive, in wire order."""
    return [v for k, v in req["headers"] if k.lower() == name]


def one(req, name):
    got = values(req, name)
    return got[0] if got else None


def crumbs(req):
    """The cookie crumbs of a request: each h2 `cookie` field (RFC 9113 8.2.3), or the h1 Cookie value split on '; '."""
    got = values(req, "cookie")
    if req["sid"] is not None:
        return got
    return [c for v in got for c in v.split("; ")]


POLICY_REFUSAL = "policy: refused for the test"


def counting_policy(calls, mod=None, refuse_port=None):
    """A connect policy that records (host, port) and answers 127.0.0.1; with `refuse_port` it refuses that port as a real policy does."""
    def policy(host, port):
        calls.append((host, port))
        if refuse_port is not None and port == refuse_port:
            raise mod.ChromeClientError(POLICY_REFUSAL)
        return ["127.0.0.1"]
    return policy


def with_session(mod, fn, policy=None, **kw):
    """fn(session, policy calls) on a fresh session over a counting (or the given) policy; always closed.

    The session is on the Chrome path (transport="chrome") unless the row
    passes `transport` itself: groups G, I and J mean that path (D16 C1).
    """
    calls = []
    if policy is None:
        policy = counting_policy(calls)
    kw.setdefault("transport", "chrome")
    s = mod._ch_session_new(connect_policy=policy, timeout=SESSION_TIMEOUT, **kw)
    try:
        return fn(s, calls)
    finally:
        s.close()


def enc_bytes(name):
    with open(os.path.join(ENC, name), "rb") as fh:
        return fh.read()


def captured_cookie_slot():
    """(field before, field after) the cookie in the committed cookie/ capture: the slot the session must fill."""
    for _name, h2 in h2_fixtures("cookie"):
        for stream in fixture_streams(h2):
            names = [n for n, _v in fixture_pairs(stream)]
            if "cookie" in names:
                i = names.index("cookie")
                return names[i - 1], (names[i + 1] if i + 1 < len(names) else None)
    return None


def slot_of(req):
    names = [k.lower() for k, _v in req["headers"]]
    if "cookie" not in names:
        return None
    i = names.index("cookie")
    last = len(names) - 1 - names[::-1].index("cookie")
    return names[i - 1], (names[last + 1] if last + 1 < len(names) else None)


class EnvSpy(dict):
    """os.environ's stand-in for one row: every read is recorded."""

    def __init__(self, *args):
        dict.__init__(self, *args)
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        self.reads.append(key)
        return dict.get(self, key, default)

    def __contains__(self, key):
        self.reads.append(key)
        return dict.__contains__(self, key)


def session_decoders():
    """{"br": fn, "zstd": fn} loaded BY PATH from Scripts/_mcp_brotli.py and _mcp_zstd.py, plus {coding: reason} for an absent library."""
    br = H.load_module_from_path("brotli_for_session", BROTLI_SOURCE)
    zs = H.load_module_from_path("zstd_for_session", ZSTD_SOURCE)
    decoders = {"br": br._brotli_decompress, "zstd": zs._zstd_decompress}
    absent = {}
    page = enc_bytes("tf_page.html")
    for coding, suffix in (("br", "br"), ("zstd", "zst")):
        try:
            decoders[coding](enc_bytes("tf_page.html." + suffix), len(page))
        except LookupError as exc:
            absent[coding] = str(exc).splitlines()[0][:120] if str(exc) else "library absent"
    return decoders, absent


def session_plans(s2, s1t, s1p, n_domain, n_bytes):
    """The fixed routes of every group-G row, by server."""
    page_types = [("content-type", "text/html; charset=utf-8")]
    for coding, suffix in (("gzip", "gz"), ("br", "br"), ("zstd", "zst")):
        s2.plan["/enc-" + coding] = (200, page_types + [("content-encoding", coding)], enc_bytes("tf_page.html." + suffix))
    s2.plan["/sc1"] = (302, [("set-cookie", "s=2; Path=/"), ("location", "/echo")], b"")
    s2.plan["/ftp"] = (302, [("location", "ftp://a.test/x")], b"")
    s2.plan["/ip-set"] = (200, [("set-cookie", "ip=1; Domain=example.test; Path=/")], b"ok")
    s2.plan["/nsec-redir"] = (302, [("set-cookie", "n=1; Path=/"), ("location", "http://a.test:%d/echo" % s1p.port)], b"")
    s2.plan["/fill"] = (200, [("set-cookie", "c%d=1; Path=/" % i) for i in range(n_domain)], b"ok")
    s2.plan["/one-more"] = (200, [("set-cookie", "x=1; Path=/")], b"ok")
    s2.plan["/reset0"] = (200, [("set-cookie", "c0=2; Path=/")], b"ok")
    s2.plan["/tot-set"] = (200, [("set-cookie", "t=1; Path=/")], b"ok")
    s2.plan["/tot-reset"] = (200, [("set-cookie", "t=2; Path=/")], b"ok")
    s2.plan["/big-ok"] = (200, [("set-cookie", "k=" + "v" * (n_bytes - 1) + "; Path=/")], b"ok")
    s2.plan["/big-over"] = (200, [("set-cookie", "k2=" + "v" * (n_bytes - 1) + "; Path=/")], b"ok")
    s2.plan["/z9"] = (200, [("set-cookie", "z=9; Path=/")], b"ok")
    for srv in (s2, s1t):
        srv.plan["/a1"] = (200, [("set-cookie", "a=1; Path=/")], b"ok")
    s1p.plan["/sec-set"] = (200, [("set-cookie", "s=1; Secure; Path=/"), ("set-cookie", "p=1; Path=/")], b"ok")


def session_transport_rows(suite, mod, s2, s1t, s1p, opened):
    """Connection reuse, cross-origin connections, POST->303->GET per transport, the redirect bounds, the downgrade."""
    u2 = "https://a.test:%d" % s2.port
    b2 = "https://b.test:%d" % s2.port
    p1 = "http://a.test:%d" % s1p.port
    src = "SessionPeer (RFC 9113 / RFC 9112, heads decoded by chrome_capture); _ChSession class docstring"

    def reuse(s, _calls):
        mark, o0 = s2.mark(), len(opened)
        r = s.get(u2 + "/r/302?/echo")
        got, conns = s2.since(mark)
        return (problem_if(r.content != b"echo", "final body %r" % r.content[:40])
                + problem_if([q["sid"] for q in got] != [1, 3] or conns != 1 or len(opened) - o0 != 1,
                             "streams %r over %d new connection(s), %d socket(s) opened" % ([q["sid"] for q in got], conns, len(opened) - o0))), ["hop 1 stream 1, hop 2 stream 3, one connection, one _ch_open_socket call"]
    run_row(suite, GG, "h2-same-origin-redirect-reuses-the-connection-stream-1-then-3", lambda: with_session(mod, reuse), src)

    def cross(s, calls):
        mark, o0 = s2.mark(), len(opened)
        r = s.get(u2 + "/r/302?" + b2 + "/echo")
        got, conns = s2.since(mark)
        return (problem_if(r.status_code != 200, "status %r" % r.status_code)
                + problem_if([q["sid"] for q in got] != [1, 1] or conns != 2 or len(opened) - o0 != 2 or len(set(q["conn"] for q in got)) != 2,
                             "streams %r over %d new connection(s), %d socket(s) opened" % ([q["sid"] for q in got], conns, len(opened) - o0))
                + problem_if(calls != [("a.test", s2.port), ("b.test", s2.port)], "policy calls %r" % calls)), ["a.test then b.test (same port, other origin): two connections, each stream 1; the policy asked per hop; the late-bound _ch_open_socket wrapper saw both"]
    run_row(suite, GG, "h2-cross-origin-redirect-opens-a-new-connection", lambda: with_session(mod, cross), src)

    for tag, srv, scheme in (("h2", s2, "https"), ("h1-tls", s1t, "https"), ("h1-plain", s1p, "http")):
        def post303(s, _calls, srv=srv, scheme=scheme, tag=tag):
            mark = srv.mark()
            r = s.post("%s://a.test:%d/r/303?/echo" % (scheme, srv.port), data={"q": "a b"})
            got, conns = srv.since(mark)
            problems = problem_if(r.status_code != 200 or r.content != b"echo", "final %r %r" % (r.status_code, r.content[:40]))
            problems += problem_if([(q["method"], q["body"]) for q in got] != [("POST", b"q=a+b"), ("GET", b"")], "hops %r" % [(q["method"], q["body"]) for q in got])
            problems += problem_if(conns != 1, "%d connections, wanted the one reused" % conns)
            if tag == "h2":
                problems += problem_if([q["sid"] for q in got] != [1, 3], "streams %r" % [q["sid"] for q in got])
            else:
                problems += problem_if(values(got[-1], "content-length") if got else False, "the GET hop still carries Content-Length")
            return problems, ["POST q=a+b -> 303 -> GET without a body, one %s connection" % tag]
        run_row(suite, GG, "%s-post-303-becomes-get-on-the-same-connection" % tag, lambda post303=post303: with_session(mod, post303), src)

    n = mod.CH_MAX_REDIRECTS

    def chain_ok(s, _calls):
        mark = s2.mark()
        r = s.get(u2 + "/chain/%d" % n)
        got, _conns = s2.since(mark)
        return problem_if(r.content != b"end" or len(got) != n + 1, "body %r after %d request(s)" % (r.content[:20], len(got))), ["exactly CH_MAX_REDIRECTS=%d redirects followed (%d requests), read off the module" % (n, n + 1)]
    run_row(suite, GG, "redirect-chain-of-exactly-ch-max-redirects-followed", lambda: with_session(mod, chain_ok), "Fetch standard: a network error once the count exceeds 20")

    def chain_over(s, _calls):
        mark = s2.mark()
        problems = expect_refusal(outcome(lambda: s.get(u2 + "/chain/%d" % (n + 1))), mod.ChromeClientError, "redirect: more than %d hops" % n)
        got, _conns = s2.since(mark)
        return problems + problem_if(len(got) != n + 1, "%d request(s), wanted %d" % (len(got), n + 1)), ["redirect %d refused after %d requests" % (n + 1, n + 1)]
    run_row(suite, GG, "redirect-ch-max-redirects-plus-1-refused", lambda: with_session(mod, chain_over), "Fetch standard: a network error once the count exceeds 20")

    def ftp(s, calls):
        problems = expect_refusal(outcome(lambda: s.get(u2 + "/ftp")), mod.ChromeClientError, "redirect: scheme ftp refused")
        return problems + problem_if(calls != [("a.test", s2.port)], "policy calls %r: the ftp hop reached the policy" % calls), ["refused before any policy call for the ftp hop"]
    run_row(suite, GG, "redirect-scheme-ftp-refused-before-the-policy", lambda: with_session(mod, ftp), src)

    def down_refused(s, calls):
        mark = s1p.mark()
        problems = expect_refusal(outcome(lambda: s.get(u2 + "/r/302?" + p1 + "/echo")), mod.ChromeClientError, "redirect: https to http downgrade refused")
        got, conns = s1p.since(mark)
        return (problems + problem_if(calls != [("a.test", s2.port)], "policy calls %r" % calls)
                + problem_if(got or conns, "the http peer saw %d request(s) on %d connection(s)" % (len(got), conns))), ["a permit-everything policy cannot grant it: refused before the policy, zero bytes to the http peer"]
    run_row(suite, GG, "downgrade-refused-without-allow-downgrade-under-a-permit-all-policy", lambda: with_session(mod, down_refused), "R2-N3")

    def down_followed(s, calls):
        r = s.get(u2 + "/r/302?" + p1 + "/echo")
        return (problem_if(r.status_code != 200 or r.content != b"echo" or r.http_version != "HTTP/1.1" or r.tls is not None,
                           "final %r %r %r tls=%r" % (r.status_code, r.content[:20], r.http_version, r.tls))
                + problem_if(calls != [("a.test", s2.port), ("a.test", s1p.port)], "policy calls %r: the http hop was not vetted" % calls)), ["followed with allow_downgrade=True; the counting policy saw the http hop"]
    run_row(suite, GG, "downgrade-followed-with-allow-downgrade-and-the-http-hop-vetted", lambda: with_session(mod, down_followed, allow_downgrade=True), "R2-N3")

    def down_policy(s, calls):
        mark = s1p.mark()
        problems = expect_refusal(outcome(lambda: s.get(u2 + "/r/302?" + p1 + "/echo")), mod.ChromeClientError, POLICY_REFUSAL)
        got, conns = s1p.since(mark)
        return problems + problem_if(got or conns, "the http peer saw %d request(s) on %d connection(s)" % (len(got), conns)), ["allow_downgrade=True does not bypass the policy: the refusal propagates, no connection"]
    run_row(suite, GG, "downgrade-with-allow-downgrade-still-refused-by-a-refusing-policy",
            lambda: with_session(mod, down_policy, policy=counting_policy([], mod, s1p.port), allow_downgrade=True), "R2-N3")

    def plain_plain(s, _calls):
        r = s.get(p1 + "/r/302?http://b.test:%d/echo" % s1p.port)
        return problem_if(r.status_code != 200 or r.content != b"echo", "final %r %r" % (r.status_code, r.content[:20])), ["http->http followed with allow_downgrade=False"]
    run_row(suite, GG, "http-to-http-redirect-needs-no-flag", lambda: with_session(mod, plain_plain), "R2-N3")


def session_decoder_rows(suite, mod, s2, decoders, absent):
    """gzip built in; br and zstd loaded by path and injected; a missing decoder is decode_error, never raw bytes."""
    u2 = "https://a.test:%d" % s2.port
    page = enc_bytes("tf_page.html")
    src = "tests/files/enc/tf_page.html.{gz,br,zst} vs tf_page.html, byte for byte"
    for coding in ("gzip", "br", "zstd"):
        cid = "decode-%s-%s" % (coding, "built-in" if coding == "gzip" else "decoder-loaded-by-path-and-injected")
        if coding in absent:
            skip(suite, GG, cid, "%s library absent: %s" % (coding, absent[coding]))
            continue

        def row(s, _calls, coding=coding):
            r = s.get(u2 + "/enc-" + coding)
            return (problem_if(r.decode_error is not None, "decode_error %r" % r.decode_error)
                    + problem_if(r.decode_error is None and r.content != page, "decoded %d bytes, wanted %d byte-identical" % (len(r.content), len(page)))), ["%d bytes decoded" % len(page)]
        run_row(suite, GG, cid, lambda row=row, coding=coding: with_session(mod, row, decoders=None if coding == "gzip" else decoders), src)

    def missing(s, _calls):
        r = s.get(u2 + "/enc-br")
        want = "body: no br decoder available"
        problems = problem_if(r.decode_error != want, "decode_error %r, wanted %r" % (r.decode_error, want))
        problems += expect_refusal(outcome(lambda: r.content), mod.ChromeClientError, want)
        problems += expect_refusal(outcome(lambda: r.text), mod.ChromeClientError, want)
        return problems + problem_if(r.status_code != 200, "status %r" % r.status_code), ["decoders=None: the response returns, .content and .text raise the one-line reason"]
    run_row(suite, GG, "missing-br-decoder-is-decode-error-and-content-text-raise", lambda: with_session(mod, missing), "_ChResponse docstring")


def session_header_rows(suite, mod, s2, s1p):
    """FR-16 caller headers per origin, Referer per hop, 307/308 cross-origin fidelity."""
    u2 = "https://a.test:%d" % s2.port
    b2 = "https://b.test:%d" % s2.port
    p1 = "http://a.test:%d" % s1p.port
    caller = {"Authorization": "Bearer t", "Cookie": "c=3", "Accept": "application/json"}
    src = "FR-16; _ChSession class docstring (caller headers bind to the call's origin)"

    def dropped(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/r/302?" + b2 + "/echo", mode="cors", headers=caller)
        got, _conns = s2.since(mark)
        a, b = got[0], got[1]
        return (problem_if(one(a, "authorization") != "Bearer t" or crumbs(a) != ["c=3"] or values(a, "accept") != ["application/json"],
                           "hop 1 authorization=%r cookie=%r accept=%r" % (one(a, "authorization"), crumbs(a), values(a, "accept")))
                + problem_if(values(b, "authorization") or crumbs(b) or "application/json" in values(b, "accept"),
                             "hop 2 authorization=%r cookie=%r accept=%r" % (values(b, "authorization"), crumbs(b), values(b, "accept")))), ["hop 1 carries all three; the cross-origin hop 2 none (accept back to the profile's)"]
    run_row(suite, GG, "caller-authorization-cookie-accept-dropped-on-a-cross-origin-hop", lambda: with_session(mod, dropped), src)

    def kept(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/r/302?/echo", mode="cors", headers=caller)
        got, _conns = s2.since(mark)
        b = got[1]
        return problem_if(one(b, "authorization") != "Bearer t" or crumbs(b) != ["c=3"] or values(b, "accept") != ["application/json"],
                          "hop 2 authorization=%r cookie=%r accept=%r" % (one(b, "authorization"), crumbs(b), values(b, "accept"))), ["the same-origin hop 2 keeps every caller header"]
    run_row(suite, GG, "caller-headers-survive-a-same-origin-hop", lambda: with_session(mod, kept), src)

    def same_host_http(s, _calls):
        mark = s1p.mark()
        s.get(u2 + "/r/302?" + p1 + "/echo", mode="cors", headers=caller)
        got, _conns = s1p.since(mark)
        b = got[0]
        return problem_if(values(b, "authorization") or crumbs(b) or "application/json" in values(b, "accept"),
                          "http hop authorization=%r cookie=%r accept=%r" % (values(b, "authorization"), crumbs(b), values(b, "accept"))), ["https://a.test -> http://a.test (same host, other scheme and port) is cross-origin"]
    run_row(suite, GG, "caller-headers-dropped-on-https-to-http-same-host", lambda: with_session(mod, same_host_http, allow_downgrade=True), src)

    referer = u2 + "/p?q"
    rsrc = "_ch_referer_for; the referer carries the peer's port, so a.test:<port> is its origin"

    def ref_same(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/echo", mode="cors", referer=referer)
        got, _conns = s2.since(mark)
        return problem_if(values(got[0], "referer") != [referer], "referer %r" % values(got[0], "referer")), ["same-origin: the full URL %s" % referer]
    run_row(suite, GG, "referer-cors-same-origin-is-the-full-url", lambda: with_session(mod, ref_same), rsrc)

    def ref_cross(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/r/302?" + b2 + "/echo", mode="cors", referer=referer)
        got, _conns = s2.since(mark)
        a, b = got[0], got[1]
        return (problem_if(values(a, "referer") != [referer] or values(a, "origin"), "hop 1 referer %r origin %r" % (values(a, "referer"), values(a, "origin")))
                + problem_if(values(b, "referer") != [u2 + "/"] or values(b, "origin") != [u2], "hop 2 referer %r origin %r" % (values(b, "referer"), values(b, "origin")))), ["hop 1 full URL, no Origin; cross-origin hop 2 %s/ plus Origin %s" % (u2, u2)]
    run_row(suite, GG, "referer-cors-cross-origin-is-the-origin-only", lambda: with_session(mod, ref_cross), rsrc)

    def ref_down(s, _calls):
        mark = s1p.mark()
        s.get(u2 + "/r/302?" + p1 + "/echo", mode="cors", referer=referer)
        got, _conns = s1p.since(mark)
        return problem_if(values(got[0], "referer"), "http hop referer %r" % values(got[0], "referer")), ["https -> http: no Referer"]
    run_row(suite, GG, "referer-cors-https-to-http-is-none", lambda: with_session(mod, ref_down, allow_downgrade=True), rsrc)

    def ref_nav(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/r/302?" + b2 + "/echo")
        got, _conns = s2.since(mark)
        return problem_if(any(values(q, "referer") for q in got) or [one(q, "sec-fetch-site") for q in got] != ["none", "none"],
                          "referers %r sec-fetch-site %r" % ([values(q, "referer") for q in got], [one(q, "sec-fetch-site") for q in got])), ["a navigation without a referer: none on either hop, sec-fetch-site none"]
    run_row(suite, GG, "referer-navigate-none-on-every-hop", lambda: with_session(mod, ref_nav), rsrc)

    for code in (307, 308):
        def resend(s, _calls, code=code):
            mark = s2.mark()
            s.request("POST", u2 + "/r/%d?%s/echo" % (code, b2), body=b"k=v", mode="cors")
            got, _conns = s2.since(mark)
            hops = [(q["authority"], q["method"], q["body"]) for q in got]
            want = [("a.test:%d" % s2.port, "POST", b"k=v"), ("b.test:%d" % s2.port, "POST", b"k=v")]
            return problem_if(hops != want, "hops %r" % hops), ["%d cross-origin after a POST: method and body re-sent (Chrome fidelity, ADR 0026)" % code]
        run_row(suite, GG, "%d-cross-origin-post-re-sends-the-body" % code, lambda resend=resend: with_session(mod, resend), "RFC 9110 15.4.8/15.4.9; _ChSession class docstring")


def session_cookie_rows(suite, mod, s2, s1t, s1p):
    """Cookies over the wire: hop to hop, the captured slot, IP host-only, Secure, every cap at N and N+1, the caller merge."""
    u2 = "https://a.test:%d" % s2.port
    b2 = "https://b.test:%d" % s2.port
    src = "RFC 6265 section 5.3 / 6.1; _ChCookieJar docstring"
    slot = captured_cookie_slot()

    def hop_cookie(s, _calls):
        mark = s2.mark()
        s.get(u2 + "/sc1")
        got, _conns = s2.since(mark)
        a, b = got[0], got[1]
        return (problem_if(crumbs(a), "hop 1 already carries %r" % crumbs(a)) + problem_if(crumbs(b) != ["s=2"], "hop 2 cookie %r" % crumbs(b))
                + problem_if(slot is None, "no cookie in the cookie/ capture") + problem_if(slot is not None and slot_of(b) != slot, "slot %r, captured %r" % (slot_of(b), slot))), ["Set-Cookie on the 302 sent on hop 2 between %r and %r as in tests/files/chrome/154/cookie/" % (slot or (None, None))]
    run_row(suite, GG, "cookie-set-on-hop-1-sent-on-hop-2-in-the-captured-slot", lambda: with_session(mod, hop_cookie), src)

    def ip_literal(s, _calls):
        ip = "https://127.0.0.1:%d" % s2.port
        s.get(ip + "/ip-set")
        mark = s2.mark()
        s.get(ip + "/echo")
        s.get("https://example.test:%d/echo" % s2.port)
        got, _conns = s2.since(mark)
        return problem_if(crumbs(got[0]) != ["ip=1"] or crumbs(got[1]), "to the IP %r, to example.test %r" % (crumbs(got[0]), crumbs(got[1]))), ["Domain=example.test from 127.0.0.1: stored host-only"]
    run_row(suite, GG, "cookie-ip-literal-with-domain-stored-host-only", lambda: with_session(mod, ip_literal), src)

    def secure_http(s, _calls):
        s.get("http://a.test:%d/sec-set" % s1p.port)
        mark = s2.mark()
        s.get(u2 + "/echo")
        got, _conns = s2.since(mark)
        return problem_if(crumbs(got[0]) != ["p=1"], "https hop cookie %r" % crumbs(got[0])), ["`s=1; Secure` from http refused; the plain `p=1` beside it kept and sent"]
    run_row(suite, GG, "cookie-secure-from-http-refused", lambda: with_session(mod, secure_http), "RFC 6265bis 5.6 step 8")

    def nonsecure_down(s, _calls):
        mark = s1p.mark()
        s.get(u2 + "/nsec-redir")
        got, _conns = s1p.since(mark)
        return problem_if(not got or crumbs(got[0]) != ["n=1"], "http hop cookie %r" % (crumbs(got[0]) if got else None)), ["non-Secure n=1 set over https, sent on the same-host http hop"]
    run_row(suite, GG, "cookie-non-secure-from-https-sent-on-a-same-host-http-hop", lambda: with_session(mod, nonsecure_down, allow_downgrade=True), src)

    n = mod.CH_MAX_COOKIES_PER_DOMAIN
    full = ["c%d=1" % i for i in range(n)]

    def per_domain(s, _calls):
        s.get(u2 + "/fill")
        mark = s2.mark()
        s.get(u2 + "/echo")
        s.get(u2 + "/one-more")
        s.get(u2 + "/echo")
        got, _conns = s2.since(mark)
        first, after = crumbs(got[0]), crumbs(got[2])
        return (problem_if(sorted(first) != sorted(full), "%d crumb(s) at the cap, wanted %d" % (len(first), n))
                + problem_if("x=1" in after or sorted(after) != sorted(full), "after one more: x=1 %s, %d crumb(s)" % ("stored" if "x=1" in after else "absent", len(after)))
                + problem_if("c0=1" not in after, "the first cookie is gone")), ["CH_MAX_COOKIES_PER_DOMAIN=%d read off the module: N stored and sent, N+1 refused, c0 kept" % n]
    run_row(suite, GG, "cookie-per-domain-cap-n-stored-and-sent-next-refused-first-kept", lambda: with_session(mod, per_domain), "R2-M3; " + src)

    def per_domain_replace(s, _calls):
        s.get(u2 + "/fill")
        s.get(u2 + "/reset0")
        mark = s2.mark()
        s.get(u2 + "/echo")
        got, _conns = s2.since(mark)
        sent = crumbs(got[0])
        return problem_if("c0=2" not in sent or "c0=1" in sent or len(sent) != n, "%d crumb(s), c0 %r" % (len(sent), [c for c in sent if c.startswith("c0=")])), ["re-setting c0 at the cap of %d replaces it" % n]
    run_row(suite, GG, "cookie-per-domain-cap-re-set-replaces", lambda: with_session(mod, per_domain_replace), "R2-M3; " + src)

    def total(s, _calls):
        saved = mod.CH_MAX_COOKIES
        lowered = 3
        mod.CH_MAX_COOKIES = lowered
        try:
            for host in ("a", "b", "c", "d"):
                s.get("https://%s.test:%d/tot-set" % (host, s2.port))
            mark = s2.mark()
            s.get("https://d.test:%d/echo" % s2.port)
            s.get(u2 + "/echo")
            s.get(u2 + "/tot-reset")
            s.get(u2 + "/echo")
            got, _conns = s2.since(mark)
            problems = problem_if(crumbs(got[0]), "the jar kept a cookie past the total cap: d.test sends %r" % crumbs(got[0]))
            problems += problem_if(crumbs(got[1]) != ["t=1"], "a.test sends %r, wanted the first cookie kept" % crumbs(got[1]))
            problems += problem_if(crumbs(got[3]) != ["t=2"], "after the re-set at the cap a.test sends %r" % crumbs(got[3]))
        finally:
            mod.CH_MAX_COOKIES = saved
        problems += problem_if(mod.CH_MAX_COOKIES != saved, "CH_MAX_COOKIES not restored")
        return problems, ["CH_MAX_COOKIES lowered from %d to %d via the module attribute (read at call time), restored in finally: a/b/c stored, d refused, a kept, re-set at the cap replaces" % (saved, lowered)]
    run_row(suite, GG, "cookie-total-cap-lowered-n-stored-next-refused-re-set-replaces", lambda: with_session(mod, total), "R2-M3; " + src)

    nb = mod.CH_MAX_COOKIE_BYTES

    def size(s, _calls):
        s.get(u2 + "/big-ok")
        s.get(u2 + "/big-over")
        mark = s2.mark()
        s.get(u2 + "/echo")
        got, _conns = s2.since(mark)
        sent = crumbs(got[0])
        return problem_if(sent != ["k=" + "v" * (nb - 1)], "%d crumb(s) of %r bytes" % (len(sent), [len(c) - 1 for c in sent])), ["name+value of exactly CH_MAX_COOKIE_BYTES=%d stored and sent; %d refused" % (nb, nb + 1)]
    run_row(suite, GG, "cookie-exactly-ch-max-cookie-bytes-stored-plus-1-refused", lambda: with_session(mod, size), "RFC 6265 section 6.1; " + src)

    msrc = "_ch_profile_headers: the cookie slot carries the jar's cookies, then the caller's crumbs"
    for tag, srv, want in (("h2", s2, (["a=1", "b=2"], 2)), ("h1-tls", s1t, (["a=1", "b=2"], 1))):
        def merge(s, _calls, srv=srv, want=want):
            base = "https://a.test:%d" % srv.port
            s.get(base + "/a1")
            mark = srv.mark()
            s.get(base + "/echo", headers={"Cookie": "b=2"})
            got, _conns = srv.since(mark)
            fields = values(got[0], "cookie")
            return problem_if(crumbs(got[0]) != want[0] or len(fields) != want[1], "cookie field(s) %r" % fields), ["jar a=1 then caller b=2: %s" % ("two h2 crumbs" if want[1] == 2 else "one h1 field 'a=1; b=2'")]
        run_row(suite, GG, "caller-cookie-merged-after-the-jar-%s" % tag, lambda merge=merge: with_session(mod, merge), msrc)

    def merge_cross(s, _calls):
        s.get(u2 + "/a1")
        s.get(b2 + "/z9")
        mark = s2.mark()
        s.get(u2 + "/r/302?" + b2 + "/echo", headers={"Cookie": "b=2"})
        got, _conns = s2.since(mark)
        return problem_if(crumbs(got[0]) != ["a=1", "b=2"] or crumbs(got[1]) != ["z=9"], "hop 1 %r, hop 2 %r" % (crumbs(got[0]), crumbs(got[1]))), ["cross-origin hop 2: the caller's b=2 gone, only b.test's jar cookie z=9"]
    run_row(suite, GG, "caller-cookie-cross-origin-hop-carries-only-that-origins-jar", lambda: with_session(mod, merge_cross), msrc + "; FR-16")


def session_env_row(suite, mod, s2, s1p):
    """No call reads os.environ (no *_PROXY), with every proxy variable pointed at a dead port."""
    def row():
        real = os.environ
        spy = EnvSpy(real)
        for name in PROXY_VARS:
            spy[name] = "http://127.0.0.1:9"
        spy.reads = []
        s = mod._ch_session_new(connect_policy=counting_policy([]), timeout=SESSION_TIMEOUT, transport="chrome")
        os.environ = spy
        try:
            r2 = s.get("https://a.test:%d/echo" % s2.port)
            r1 = s.get("http://a.test:%d/echo" % s1p.port)
        finally:
            os.environ = real
            s.close()
        return (problem_if(spy.reads, "os.environ read during a call: %r" % sorted(set(spy.reads))[:6])
                + problem_if(r2.content != b"echo" or r1.content != b"echo", "the calls did not reach the peers")), ["one h2 and one cleartext h1 call under a spying os.environ; the Chrome path builds no ssl default context, so not even SSLKEYLOGFILE is read"]
    run_row(suite, GG, "no-os-environ-read-during-a-call-proxy-vars-ignored", row, "R-0017: no *_PROXY, no os.environ")


def sans_io_bound_rows(suite, mod):
    """F24 and F25 without a peer: an over-long Max-Age, and the charset a body is decoded with."""
    def max_age_row():
        problems, notes = [], []
        for label, attr, want_expiry in (("20 digits", "Max-Age=" + "9" * 20, None), ("-20 digits", "Max-Age=-" + "9" * 20, None), ("19 digits", "Max-Age=" + "1" * 19, 1000 + int("1" * 19))):
            jar = mod._ChCookieJar(clock=lambda: 1000)
            changed = outcome(lambda: jar.ingest("https", "a.test", "/", ["a=1; " + attr]))
            problems += expect_value(changed, 1)
            stored = [c["expiry"] for c in jar._cookies]
            problems += problem_if(stored != [want_expiry], "%s: stored expiry %r, wanted %r" % (label, stored, want_expiry))
            notes.append("%s -> expiry %r" % (label, want_expiry))
        return problems, ["a Max-Age of more than 19 digits is ignored (a session cookie, never int() of it); 19 digits still counts: " + "; ".join(notes)]
    run_row(suite, GG, "cookie-max-age-over-19-digits-ignored", max_age_row, "_ChCookieJar docstring (F24)")

    charset_rows = (
        ("punycode-decodes-as-utf-8", "punycode", "abc-é".encode("utf-8") * 4, "abc-é" * 4),
        ("idna-decodes-as-utf-8-never-raises", "idna", b"a" * 70, "a" * 70),
        ("unicode-escape-decodes-as-utf-8", "unicode_escape", b"\\u0041", "\\u0041"),
        ("raw-unicode-escape-decodes-as-utf-8", "raw_unicode_escape", b"\\u0041", "\\u0041"),
        ("utf-7-decodes-as-utf-8", "utf-7", b"+AEE-", "+AEE-"),
        ("rot13-decodes-as-utf-8", "rot13", b"uryyb", "uryyb"),
        ("unknown-decodes-as-utf-8", "x-no-such", b"ok", "ok"),
        ("iso-8859-2-honoured", "ISO-8859-2", b"\xb1", "ą"),
        ("shift-jis-honoured", "\"Shift_JIS\"", "日本".encode("shift_jis"), "日本"),
        ("windows-1252-honoured", "windows-1252", b"\x80", "€"),
        ("utf-8-invalid-byte-replaced", "utf-8", b"a\xffb", "a�b"),
    )
    for cid, charset, body, want in charset_rows:
        def text_row(charset=charset, body=body, want=want):
            r = mod._ChResponse(200, [("content-type", "text/html; charset=%s" % charset)], "https://a.test/", body)
            return expect_value(outcome(lambda: r.text), want), ["charset=%s" % charset]
        run_row(suite, GG, "response-text-charset-%s" % cid, text_row, "_ChResponse.TEXT_CHARSETS (F25): the WHATWG web charsets, else UTF-8 with errors=replace")

    def allowlist_row():
        missing = [label for label in mod._ChResponse.TEXT_CHARSETS if outcome(lambda label=label: codecs.lookup(label))[0] == "exc"]
        bad = [label for label in mod._ChResponse.TEXT_CHARSETS if label not in missing and not getattr(codecs.lookup(label), "_is_text_encoding", True)]
        hostile = [c for c in ("punycode", "idna", "unicode_escape", "raw_unicode_escape", "utf-7", "rot13", "hex", "base64") if codecs.lookup(c).name in set(codecs.lookup(x).name for x in mod._ChResponse.TEXT_CHARSETS if x not in missing)]
        return (problem_if(missing, "labels with no codec on this interpreter: %r" % missing) + problem_if(bad, "non-text codecs listed: %r" % bad)
                + problem_if(hostile, "hostile codecs reachable: %r" % hostile)), ["%d labels, every one a text codec here, none of the hostile eight" % len(mod._ChResponse.TEXT_CHARSETS)]
    run_row(suite, GG, "response-text-allowlist-resolves-and-excludes-hostile-codecs", allowlist_row, "_ChResponse.TEXT_CHARSETS")


def group_session(suite, mod, cc):
    """Group G: _ChSession against loopback h2, h1-over-TLS and cleartext h1 peers; every rule over the wire."""
    s2 = SessionPeer("h2", cc)
    s1t = SessionPeer("h1-tls", cc)
    s1p = SessionPeer("h1-plain", cc)
    opened = []
    real_open = mod._ch_open_socket

    def spy_open(addresses, port, deadline):
        opened.append((tuple(addresses), port))
        return real_open(addresses, port, deadline)

    mod._ch_open_socket = spy_open
    try:
        session_plans(s2, s1t, s1p, mod.CH_MAX_COOKIES_PER_DOMAIN, mod.CH_MAX_COOKIE_BYTES)
        decoders, absent = session_decoders()
        session_transport_rows(suite, mod, s2, s1t, s1p, opened)
        session_decoder_rows(suite, mod, s2, decoders, absent)
        session_header_rows(suite, mod, s2, s1p)
        session_cookie_rows(suite, mod, s2, s1t, s1p)
        session_env_row(suite, mod, s2, s1p)
        sans_io_bound_rows(suite, mod)
    finally:
        mod._ch_open_socket = real_open
        for srv in (s2, s1t, s1p):
            srv.close()


# --- I. limits and connect policy --------------------------------------------------

# The words rule 3 of _ch_address_refused may answer with (its docstring), optionally after "IPv4-mapped ". A row
# whose address a stdlib ipaddress table classifies accepts any of them: which one a table picks is the
# interpreter's business; that the address is refused, and that the stdlib property carrying it still reads
# as it did, is the rule.
REFUSAL_WORDS = ("loopback", "link-local", "private", "reserved", "multicast", "unspecified", "not globally routable")

# (row label, address, the exact reason -- or, where rule 3's stdlib tables choose the word, the stdlib property
# that carries the refusal and the value it must read: an interpreter whose table moved fails the row). The
# IPv4-compatible ::a.b.c.d forms are refused by is_reserved (::/8) and ARE is_global on 3.9; Teredo by
# is_private and not is_global. One row per form.
STDLIB_NOT_GLOBAL = ("is_global", False)
STDLIB_RESERVED = ("is_reserved", True)
ADDRESS_REFUSED = (
    ("ipv4-mapped-loopback", "::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ("ipv4-mapped-private", "::ffff:10.0.0.1", "IPv4-mapped private"),
    ("ipv4-mapped-metadata", "::ffff:169.254.169.254", "IPv4-mapped link-local"),
    ("ipv4-compatible-public", "::8.8.8.8", STDLIB_RESERVED),
    ("ipv4-compatible-loopback", "::127.0.0.1", STDLIB_RESERVED),
    ("nat64-96-of-public", "64:ff9b::808:808", "translation prefix 64:ff9b::/96"),
    ("nat64-96-of-metadata", "64:ff9b::a9fe:a9fe", "translation prefix 64:ff9b::/96"),
    ("nat64-96-of-private", "64:ff9b::a00:1", "translation prefix 64:ff9b::/96"),
    ("nat64-local-48-of-public", "64:ff9b:1::808:808", "translation prefix 64:ff9b:1::/48"),
    ("6to4-of-public", "2002:808:808::1", "translation prefix 2002::/16"),
    ("6to4-of-metadata", "2002:a9fe:a9fe::1", "translation prefix 2002::/16"),
    ("6to4-of-private", "2002:a00:1::1", "translation prefix 2002::/16"),
    ("teredo", "2001:0:4136:e378:8000:63bf:3fff:fdd2", STDLIB_NOT_GLOBAL),
    ("decimal-ipv4-spelling", "2130706433", "not an IP address"),
    ("octal-ipv4-spelling", "0177.0.0.1", "not an IP address"),
    ("hex-dotted-ipv4-spelling", "0x7f.0.0.1", "not an IP address"),
    ("hex-whole-ipv4-spelling", "0x7f000001", "not an IP address"),
    ("int-not-a-str", 2130706433, "not an IP address"),
    ("unparseable", "not-an-ip", "not an IP address"),
    ("empty-string", "", "not an IP address"),
    ("ipv4-unspecified", "0.0.0.0", STDLIB_NOT_GLOBAL),
    ("ipv6-unspecified", "::", STDLIB_NOT_GLOBAL),
    ("ipv4-link-local", "169.254.1.1", "link-local"),
    ("cloud-metadata", "169.254.169.254", "link-local"),
    ("ipv6-link-local", "fe80::1", "link-local"),
    ("ipv6-unique-local", "fd00::1", "private"),
    ("shared-address-space-cgnat", "100.64.0.1", "not globally routable"),
    ("ipv4-multicast", "224.0.0.1", "multicast"),
    ("ipv6-multicast", "ff02::1", "multicast"),
    ("ipv4-broadcast", "255.255.255.255", STDLIB_NOT_GLOBAL),
    ("ipv4-loopback", "127.0.0.1", "loopback"),
    ("ipv6-loopback", "::1", "loopback"),
    ("private-10", "10.1.2.3", "private"),
    ("private-172-16", "172.16.0.1", "private"),
    ("private-192-168", "192.168.1.1", "private"),
    ("ipv6-site-local", "fec0::1", "site-local"),
    ("ipv6-site-local-top", "feff:ffff::1", "site-local"),
)

ADDRESS_ALLOWED = (
    ("public-ipv4", "8.8.8.8"),
    ("public-ipv6", "2001:4860:4860::8888"),
    ("ipv4-mapped-public", "::ffff:8.8.8.8"),
    ("public-ipv4-address-object", ipaddress.IPv4Address("1.1.1.1")),
)

# The slow-loris peer: this response one byte per LORIS_GAP_S (about 11.5 s in all), against a call timeout of
# LORIS_TIMEOUT. Every gap is far under the timeout, so only ONE deadline for the whole call can stop it.
LORIS_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nabcdefgh"
LORIS_GAP_S = 0.25
LORIS_TIMEOUT = 2


class Swapped:
    """Replace attributes for one block and restore every one on exit, even on a raise (P13, as tests/test_sbx_seccomp.py _Stubbed).

    Each triple is (owner, name, value): a module global, a class attribute
    or property. A name the owner did not define ITSELF (inherited) is
    deleted again on exit instead of being pinned on the owner.
    """

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.triples:
            own = name in vars(owner)
            self.saved.append((owner, name, own, vars(owner).get(name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *_exc):
        for owner, name, own, old in reversed(self.saved):
            if own:
                setattr(owner, name, old)
            else:
                delattr(owner, name)
        self.saved = []
        return False


class CallbackRefusal(Exception):
    """What a refusing on_headers raises: the rows assert this very object comes back, unchanged."""


def refusing_resolver(calls):
    """A socket.getaddrinfo stand-in that records every call and resolves nothing."""
    def gai(*args, **_kw):
        calls.append(args[:2])
        raise socket.gaierror(socket.EAI_NONAME, "resolution refused by the test")
    return gai


def answering_resolver(calls, answer):
    """A socket.getaddrinfo stand-in answering the address strings `answer`, in order, recording (host, port, type)."""
    def gai(host, port, *_args, **kw):
        calls.append((host, port, kw.get("type")))
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in answer]
    return gai


def with_policy(mod, fn, make_policy, **kw):
    """fn(session, calls) on a fresh session over make_policy(calls); always closed. Chrome path unless `transport` is passed."""
    calls = []
    kw.setdefault("transport", "chrome")
    s = mod._ch_session_new(connect_policy=make_policy(calls), timeout=SESSION_TIMEOUT, **kw)
    try:
        return fn(s, calls)
    finally:
        s.close()


def too_large(result, mod, limit):
    """Problems unless `result` is exactly ChromeBodyTooLarge with its one-line message and `.limit == limit`."""
    problems = expect_refusal(result, mod.ChromeBodyTooLarge, "body: exceeds %d bytes" % limit)
    if not problems and result[1].limit != limit:
        problems.append(".limit %r, wanted %d" % (result[1].limit, limit))
    return problems


def refused_with_prefix(result, exc_type, prefix):
    """Problems unless `result` raised exactly `exc_type` whose one-line message starts with `prefix`."""
    kind, value = result
    if kind == "value":
        return ["returned %s instead of raising %s" % (short(value), exc_type.__name__)]
    if type(value) is not exc_type:
        return ["raised %s(%r), wanted %s" % (type(value).__name__, str(value)[:160], exc_type.__name__)]
    text = str(value)
    return problem_if(not text.startswith(prefix) or "\n" in text or "\r" in text, "message %r, wanted one line starting %r" % (text[:200], prefix))


def shown(result):
    kind, value = result
    if kind == "exc":
        return "raised %s(%r)" % (type(value).__name__, str(value)[:120])
    return "returned %s" % short(value)


def content_of(result):
    """The decoded body of an outcome() of a request; None when the call or its .content raised."""
    if result[0] == "exc":
        return None
    got = outcome(lambda: result[1].content)
    return got[1] if got[0] == "value" else None


def wait_for(probe, limit=SESSION_TIMEOUT):
    """probe() once truthy, polled every 20 ms for up to `limit` s: an event wait, never a verdict on time."""
    end = time.monotonic() + limit
    while True:
        got = probe()
        if got or time.monotonic() >= end:
            return got
        time.sleep(0.02)


def noise(n, label):
    """`n` deterministic, incompressible bytes: a SHA-256 chain over `label` (no module code, no randomness)."""
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(b"%s-%d" % (label, i)).digest()
        i += 1
    return bytes(out[:n])


def gz(data):
    """RFC 1952 gzip by the stdlib gzip module (the oracle for every gzip body here), mtime 0."""
    return gzip.compress(data, 9, mtime=0)


def raw_deflate(data):
    """RFC 1951 deflate with no zlib wrapper (zlib wbits -15): the non-conformant `deflate` RFC 9110 8.4.1.2 warns of."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def alabel(name):
    """`name` as its IDNA A-label (the stdlib codec), whether a peer reports the A- or the U-label."""
    return name.encode("idna").decode("ascii") if isinstance(name, str) else name


def trusted_context():
    """FLAG-4: a verifying context whose ONLY anchor is the committed test CA (never a trust store)."""
    return ssl.create_default_context(cafile=TLS_CA)


class RawPeer:
    """A cleartext loopback listener whose every connection runs `script(sock)` in its own thread (group I)."""

    def __init__(self, script):
        self.script = script
        self.lsock = socket.socket()
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(8)
        self.lsock.settimeout(0.25)
        self.port = self.lsock.getsockname()[1]
        self.conns = 0
        self.errors = []
        self.closed = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def close(self):
        self.closed = True
        self.thread.join(5)
        self.lsock.close()

    def _accept(self):
        while not self.closed:
            try:
                sock, _addr = self.lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.conns += 1
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        try:
            sock.settimeout(SESSION_TIMEOUT)
            self.script(sock)
        except Exception as exc:  # the client hanging up mid-trickle lands here; recorded, never printed
            self.errors.append("%s(%r)" % (type(exc).__name__, str(exc)[:120]))
        finally:
            sock.close()


def slow_loris(sock):
    """Read one request head, then answer LORIS_RESPONSE one byte per LORIS_GAP_S."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return
        buf += chunk
    for i in range(len(LORIS_RESPONSE)):
        sock.sendall(LORIS_RESPONSE[i:i + 1])
        time.sleep(LORIS_GAP_S)


class ReusePeer(SessionPeer):
    """An h2 SessionPeer that answers the FIRST request of each connection and meets the second with `mode`.

    "goaway": GOAWAY(last-stream-id = the stream before it, NO_ERROR) and
    no answer -- RFC 9113 section 6.8: a stream above last-stream-id was not
    processed, so any method may be retried; the connection stays open until
    the client drops it. "close": the connection is closed once the whole
    second request was read, no GOAWAY -- it may have been processed, so only
    an idempotent GET may be re-sent. Every request is recorded either way.
    """

    def __init__(self, cc, mode):
        self.mode = mode
        self.per_conn = {}
        SessionPeer.__init__(self, "h2", cc)

    def _h2_answer(self, sock, cid, sid, st):
        n = self.per_conn.get(cid, 0) + 1
        self.per_conn[cid] = n
        if n < 2:
            SessionPeer._h2_answer(self, sock, cid, sid, st)
            return
        pseudo = dict((k, v) for k, v in st["fields"] if k.startswith(":"))
        self._record(cid, sid, pseudo.get(":method"), pseudo.get(":path"), pseudo.get(":authority"), [(k, v) for k, v in st["fields"] if not k.startswith(":")], st["body"])
        if self.mode == "goaway":
            sock.sendall(h2f(F_GOAWAY, 0, 0, struct.pack("!II", sid - 2, 0)))
            return
        raise EOFError("closed after reading the second request, by design")


class Tls12Peer:
    """A threaded loopback HTTPS/1.1 server capped at TLS 1.2 (ssl `maximum_version`) on the tests/files/tls leaf.

    Written here on the stdlib ssl server, not from the module. It records
    the peer IP of every accepted connection, the version of every completed
    handshake, and every request as {method, path, fields, body}. `plan`
    maps a path to (status, fields, body), or "hang" (read the request,
    answer nothing until the client hangs up); anything else is a 200
    "hello fallback". Every response carries Content-Length and Connection:
    close. The Chrome path's TLS 1.3 attempt, refused by the ServerHello,
    ends inside wrap_socket and records no handshake.
    """

    def __init__(self):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        self.ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        self.lsock = socket.socket()
        self.lsock.bind(("127.0.0.1", 0))
        self.lsock.listen(32)
        self.lsock.settimeout(0.25)
        self.port = self.lsock.getsockname()[1]
        self.accepts = []
        self.versions = []
        self.requests = []
        self.plan = {}
        self.closed = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def close(self):
        self.closed = True
        self.thread.join(5)
        self.lsock.close()

    def mark(self):
        return len(self.requests), len(self.accepts)

    def since(self, mark):
        return self.requests[mark[0]:], self.accepts[mark[1]:]

    def _accept(self):
        while not self.closed:
            try:
                sock, addr = self.lsock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.accepts.append(addr[0])
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        try:
            sock.settimeout(SESSION_TIMEOUT)
            try:
                sock = self.ctx.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError):
                return
            self.versions.append(sock.version())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            head, _sep, rest = buf.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            fields = [tuple(line.split(": ", 1)) for line in lines[1:]]
            n = int(dict((k.lower(), v) for k, v in fields).get("content-length", "0"))
            while len(rest) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                rest += chunk
            self.requests.append({"method": method, "path": path, "fields": fields, "body": rest[:n]})
            route = self.plan.get(path.partition("?")[0], (200, [("Content-Type", "text/plain")], b"hello fallback"))
            if route == "hang":
                sock.recv(1)
                return
            status, hdrs, body = route
            out = "HTTP/1.1 %d X\r\n" % status + "".join("%s: %s\r\n" % (k, v) for k, v in hdrs) + "Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
            sock.sendall(out.encode("latin-1") + (b"" if method == "HEAD" else body))
        except (ssl.SSLError, OSError):  # socket.timeout included; a peer thread never prints
            pass
        finally:
            sock.close()


def limit_plans(s2, s1p, two, raw):
    """The fixed routes of the group-I limit rows."""
    for srv in (s2, s1p):
        for n in (1000, 1001):
            srv.plan["/k%d" % n] = (200, [("content-type", "text/plain")], b"k" * n)
    text = [("content-type", "text/plain")]
    s2.plan["/gz5000"] = (200, text + [("content-encoding", "gzip")], gz(b"a" * 5000))
    s2.plan["/gz4096"] = (200, text + [("content-encoding", "gzip")], gz(b"d" * 4096))
    s2.plan["/gz4097"] = (200, text + [("content-encoding", "gzip")], gz(b"d" * 4097))
    s2.plan["/gz-corrupt"] = (200, text + [("content-encoding", "gzip")], b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"\xff" * 24)
    s2.plan["/gz-x3"] = (200, [("content-type", "text/html"), ("content-encoding", "gzip, gzip, gzip")], enc_bytes("gzip-x3.gz"))
    s2.plan["/two-layer"] = (200, text + [("content-encoding", "gzip, gzip")], gz(gz(two)))
    s2.plan["/members-bomb"] = (200, text + [("content-encoding", "gzip")], enc_bytes("gzip-members-bomb.gz"))
    s2.plan["/raw-deflate"] = (200, text + [("content-encoding", "deflate, gzip")], gz(raw_deflate(raw)))


def limits_rows(suite, mod, s2, s1p):
    """max_bytes on the wire and decoded, CH_MAX_BODY_BYTES and CH_DEFAULT_DECODE_CAP at N and N+1 (module attributes)."""
    u2 = "https://a.test:%d" % s2.port
    p1 = "http://a.test:%d" % s1p.port
    src = "R2-M6: ChromeBodyTooLarge.limit is the limit that bit; _ChSession class docstring"
    for tag, base in (("h2", u2), ("h1-plain", p1)):
        def wire(s, _calls, base=base):
            return too_large(outcome(lambda: s.get(base + "/k1000")), mod, 100), ["max_bytes=100, a 1000-byte body: ChromeBodyTooLarge(.limit=100)"]
        run_row(suite, GI, "max-bytes-wire-refused-%s-limit-is-max-bytes" % tag, lambda wire=wire: with_session(mod, wire, max_bytes=100), src)

    def decoded(s, _calls):
        wire_len = len(s2.plan["/gz5000"][2])
        problems = problem_if(wire_len >= 100, "the fixture's wire body is %d bytes, not under max_bytes" % wire_len)
        return problems + too_large(outcome(lambda: s.get(u2 + "/gz5000")), mod, 100), ["max_bytes=100: a %d-byte gzip body decoding to 5000 bytes, refused on the decoded side (.limit=100)" % wire_len]
    run_row(suite, GI, "max-bytes-decoded-refused-limit-is-max-bytes", lambda: with_session(mod, decoded, max_bytes=100), src)

    for tag, base in (("h2", u2), ("h1-plain", p1)):
        def body_cap(s, _calls, base=base):
            saved = mod.CH_MAX_BODY_BYTES
            with Swapped((mod, "CH_MAX_BODY_BYTES", 1000)):
                at = outcome(lambda: s.get(base + "/k1000"))
                over = outcome(lambda: s.get(base + "/k1001"))
            problems = problem_if(content_of(at) != b"k" * 1000, "at N: %s" % shown(at))
            problems += too_large(over, mod, 1000)
            problems += problem_if(mod.CH_MAX_BODY_BYTES != saved, "CH_MAX_BODY_BYTES not restored")
            return problems, ["max_bytes=0, CH_MAX_BODY_BYTES lowered from %d to 1000 through the module attribute and restored: 1000 bytes accepted, 1001 refused with .limit=1000" % saved]
        run_row(suite, GI, "ch-max-body-bytes-lowered-n-accepted-n-plus-1-refused-%s" % tag, lambda body_cap=body_cap: with_session(mod, body_cap), src)

    def decode_cap(s, _calls):
        saved = mod.CH_DEFAULT_DECODE_CAP
        with Swapped((mod, "CH_DEFAULT_DECODE_CAP", 4096)):
            at = outcome(lambda: s.get(u2 + "/gz4096"))
            over = outcome(lambda: s.get(u2 + "/gz4097"))
        problems = problem_if(content_of(at) != b"d" * 4096, "exactly the cap: %s" % shown(at))
        problems += too_large(over, mod, 4096)
        problems += problem_if(mod.CH_DEFAULT_DECODE_CAP != saved, "CH_DEFAULT_DECODE_CAP not restored")
        return problems, ["max_bytes=0, CH_DEFAULT_DECODE_CAP lowered from %d to 4096 and restored: a gzip body decoding to 4096 accepted, 4097 refused with .limit=4096" % saved]
    run_row(suite, GI, "ch-default-decode-cap-lowered-exactly-cap-accepted-plus-1-refused", lambda: with_session(mod, decode_cap), "R2-M3; " + src)

    def corrupt(s, _calls):
        with Swapped((mod, "CH_DEFAULT_DECODE_CAP", 4096)):
            got = outcome(lambda: s.get(u2 + "/gz-corrupt"))
        if got[0] == "exc":
            return ["raised %s(%r): a corrupt body is a decode_error, not an exception" % (type(got[1]).__name__, str(got[1])[:120])], []
        r = got[1]
        problems = problem_if(not isinstance(r.decode_error, str) or not r.decode_error.startswith("body: gzip: "), "decode_error %r" % r.decode_error)
        problems += problem_if(outcome(lambda: r.content)[0] != "exc", ".content did not raise")
        return problems, ["a corrupt gzip body under the lowered cap: decode_error %r, never ChromeBodyTooLarge" % r.decode_error]
    run_row(suite, GI, "corrupt-gzip-under-the-lowered-cap-is-decode-error", lambda: with_session(mod, corrupt), "_ch_decode_body docstring (D16)")


def decode_limit_rows(suite, mod, s2, two, raw):
    """D16: more than CH_MAX_CONTENT_CODINGS, one cumulative budget across layers and gzip members, raw deflate on what remains."""
    u2 = "https://a.test:%d" % s2.port
    src = "_ch_decode_body docstring (D16): ONE cumulative budget, each layer called with what remains"

    def x3(s, _calls):
        r = s.get(u2 + "/gz-x3")
        want = "body: 3 content codings exceed the limit of %d" % mod.CH_MAX_CONTENT_CODINGS
        return (problem_if(r.decode_error != want, "decode_error %r, wanted %r" % (r.decode_error, want))
                + problem_if(outcome(lambda: r.content)[0] != "exc", ".content did not raise")), ["tests/files/enc/gzip-x3.gz as `gzip, gzip, gzip`: refused before any layer, CH_MAX_CONTENT_CODINGS=%d read off the module" % mod.CH_MAX_CONTENT_CODINGS]
    run_row(suite, GI, "three-content-codings-are-decode-error", lambda: with_session(mod, x3), src)

    mid = gz(two)
    total = len(mid) + len(two)

    def two_layer():
        at = with_session(mod, lambda s, _c: outcome(lambda: s.get(u2 + "/two-layer")), max_bytes=total)
        over = with_session(mod, lambda s, _c: outcome(lambda: s.get(u2 + "/two-layer")), max_bytes=total - 1)
        problems = problem_if(content_of(at) != two, "budget = both layers: %s" % shown(at))
        problems += too_large(over, mod, total - 1)
        return problems, ["gzip(gzip(%d noise bytes)): the outer layer yields %d, the inner %d, each alone under %d; max_bytes=%d decodes, %d refused" % (len(two), len(mid), len(two), total - 1, total, total - 1)]
    run_row(suite, GI, "two-layer-body-over-the-cumulative-budget-refused", two_layer, src)

    def bomb(s, _calls):
        return too_large(outcome(lambda: s.get(u2 + "/members-bomb")), mod, mod.CH_DEFAULT_DECODE_CAP), ["tests/files/enc/gzip-members-bomb.gz: 72 members of 1 MiB, each under CH_DEFAULT_DECODE_CAP=%d, refused on their sum (.limit is the cap)" % mod.CH_DEFAULT_DECODE_CAP]
    run_row(suite, GI, "gzip-members-bomb-refused-on-the-cumulative-budget", lambda: with_session(mod, bomb), src)

    rd = raw_deflate(raw)
    budget = len(rd) + len(raw)

    def raw_row():
        at = with_session(mod, lambda s, _c: outcome(lambda: s.get(u2 + "/raw-deflate")), max_bytes=budget)
        over = with_session(mod, lambda s, _c: outcome(lambda: s.get(u2 + "/raw-deflate")), max_bytes=budget - 1)
        problems = problem_if(content_of(at) != raw, "budget = both layers: %s" % shown(at))
        problems += too_large(over, mod, budget - 1)
        return problems, ["`deflate, gzip` with a RAW deflate inner layer (%d -> %d bytes): max_bytes=%d decodes; at %d the fallback gets %d remaining, not a fresh budget (which would fit), and is refused" % (len(rd), len(raw), budget, budget - 1, len(raw) - 1)]
    run_row(suite, GI, "raw-deflate-fallback-past-the-remaining-budget-refused", raw_row, src + "; RFC 9110 section 8.4.1.2")


def on_headers_rows(suite, mod, s2, s1p):
    """R2-M6: on_headers gets the final hop, its exception propagates unchanged, cancels the h2 stream, beats the size limit."""
    u2 = "https://a.test:%d" % s2.port
    p1 = "http://a.test:%d" % s1p.port
    src = "R2-M6; _ChSession class docstring: on_headers runs once on the final response, before any body byte is read or any size decided"
    for tag, base in (("h2", u2), ("h1-plain", p1)):
        def final(s, _calls, base=base):
            seen = []
            r = s.get(base + "/r/302?/echo", on_headers=lambda status, headers, url: seen.append((status, headers.get("content-type"), url)))
            want = [(200, "text/plain", base + "/echo")]
            return problem_if(seen != want or r.content != b"echo", "on_headers saw %r, body %r" % (seen, r.content[:20])), ["one call with the FINAL hop's status, headers and URL; the 302 is never reported"]
        run_row(suite, GI, "on-headers-gets-the-final-hop-only-%s" % tag, lambda final=final: with_session(mod, final), src)

    big = noise(4 * 1024 * 1024, b"paced")
    s2.plan["/paced"] = (200, [("content-type", "application/octet-stream")], big)
    s2.paced.add("/paced")

    def cancel(s, _calls):
        stop = CallbackRefusal("refused by the callback")
        mark = len(s2.rsts)

        def refuse(_status, _headers, _url):
            raise stop
        got = outcome(lambda: s.get(u2 + "/paced", on_headers=refuse))
        problems = problem_if(got[0] != "exc" or got[1] is not stop, "%s, wanted the callback's own exception" % shown(got))
        rst = wait_for(lambda: s2.rsts[mark:])
        problems += problem_if(not rst or rst[0][2] != E_CANCEL, "RST_STREAM seen by the peer: %r" % (rst,))
        sent = s2.data_sent.get(rst[0][:2]) if rst else None
        problems += problem_if(sent is None or sent >= len(big), "DATA bytes the peer wrote: %r of %d" % (sent, len(big)))
        return problems, ["the peer paces %d bytes in 16 KiB frames and stops when the client writes: RST_STREAM(CANCEL) after %r DATA bytes" % (len(big), sent)]
    run_row(suite, GI, "on-headers-raising-propagates-unchanged-and-cancels-the-h2-stream", lambda: with_session(mod, cancel), src + "; RFC 9113 section 8.1 (CANCEL)")

    for tag, base in (("h2", u2), ("h1-plain", p1)):
        def beats(s, _calls, base=base):
            stop = CallbackRefusal("refused by the callback")

            def refuse(_status, _headers, _url):
                raise stop
            got = outcome(lambda: s.get(base + "/k1000", on_headers=refuse))
            return problem_if(got[0] != "exc" or got[1] is not stop, "%s, wanted the callback's exception (headers are decided first)" % shown(got)), ["max_bytes=100, a 1000-byte body sent with its head: the refusing callback wins over ChromeBodyTooLarge"]
        run_row(suite, GI, "refusing-on-headers-beats-body-too-large-%s" % tag, lambda beats=beats: with_session(mod, beats, max_bytes=100), src)


def address_rows(suite, mod):
    """_ch_address_refused, one row per form (SEC-HIGH, R2-M2)."""
    src = "_ch_address_refused docstring: 1. IPv4-mapped classified on the IPv4 only, 2. translation prefixes whole, 3. stdlib classes + not is_global"
    for label, addr, want in ADDRESS_REFUSED:
        def refused(addr=addr, want=want):
            got = outcome(lambda: mod._ch_address_refused(addr))
            if got[0] == "exc":
                return ["raised %s(%r)" % (type(got[1]).__name__, str(got[1])[:120])], []
            reason = got[1]
            if isinstance(want, str):
                return problem_if(reason != want, "reason %r, wanted %r" % (reason, want)), ["%r -> %r" % (addr, reason)]
            prop, value = want
            word = reason[len("IPv4-mapped "):] if isinstance(reason, str) and reason.startswith("IPv4-mapped ") else reason
            seen = getattr(ipaddress.ip_address(addr), prop)
            problems = problem_if(word not in REFUSAL_WORDS, "reason %r is not one of rule 3's words" % (reason,))
            problems += problem_if(seen is not value, "the stdlib reads %s.%s=%r, wanted %r: an interpreter table moved under the rule" % (addr, prop, seen, value))
            return problems, ["%r -> %r (a stdlib table chose the word); ipaddress %s=%r" % (addr, reason, prop, seen)]
        run_row(suite, GI, "address-refused-" + label, refused, src)
    for label, addr in ADDRESS_ALLOWED:
        def allowed(addr=addr):
            return expect_value(outcome(lambda: mod._ch_address_refused(addr)), None), ["%r allowed" % (addr,)]
        run_row(suite, GI, "address-allowed-" + label, allowed, src)

    def patched():
        always = property(lambda self: True)
        with Swapped((ipaddress.IPv6Address, "is_reserved", always), (ipaddress.IPv6Address, "is_private", always)):
            took = ipaddress.ip_address("::ffff:8.8.8.8").is_private is True
            got = outcome(lambda: mod._ch_address_refused("::ffff:8.8.8.8"))
        restored = ipaddress.ip_address("2001:4860:4860::8888").is_private is False
        return (problem_if(not took, "the property patch did not take") + expect_value(got, None)
                + problem_if(not restored, "ipaddress.IPv6Address properties not restored")), ["IPv6Address.is_reserved/is_private patched to True (a 3.9.x without the mapped deferral), restored in finally: ::ffff:8.8.8.8 still allowed, the IPv6 form is never consulted"]
    run_row(suite, GI, "ipv4-mapped-public-allowed-with-the-ipv6-properties-patched-true", patched, src)


def public_policy_rows(suite, mod):
    """_ch_public_only_policy with socket.getaddrinfo replaced (R2-N2)."""
    CE = mod.ChromeClientError
    src = "_ch_public_only_policy docstring (R2-N2): resolved ONCE, any refused address refuses the hop, resolver order kept"
    cases = (
        ("public-answer-returned", ["8.8.8.8"], ["8.8.8.8"], None),
        ("one-metadata-address-refuses-the-hop", ["8.8.8.8", "169.254.169.254"], None, "policy: example.test resolves to 169.254.169.254 (link-local)"),
        ("empty-answer-refused", [], None, "policy: example.test resolves to no address"),
        ("unparseable-address-refused", ["8.8.8.8", "bogus"], None, "policy: example.test resolves to 'bogus' (not an IP address)"),
        ("ipv4-mapped-private-refused", ["::ffff:10.0.0.1"], None, "policy: example.test resolves to ::ffff:10.0.0.1 (IPv4-mapped private)"),
        ("nat64-of-public-refused", ["64:ff9b::808:808"], None, "policy: example.test resolves to 64:ff9b::808:808 (translation prefix 64:ff9b::/96)"),
        ("resolver-order-kept-duplicates-dropped", ["1.1.1.1", "8.8.8.8", "2001:4860:4860::8888", "8.8.8.8"], ["1.1.1.1", "8.8.8.8", "2001:4860:4860::8888"], None),
    )
    for label, answer, want, message in cases:
        def row(answer=answer, want=want, message=message):
            calls = []
            with Swapped((socket, "getaddrinfo", answering_resolver(calls, answer))):
                got = outcome(lambda: mod._ch_public_only_policy("example.test", 443))
            problems = expect_value(got, want) if message is None else expect_refusal(got, CE, message)
            problems += problem_if(calls != [("example.test", 443, socket.SOCK_STREAM)], "resolver calls %r, wanted exactly one SOCK_STREAM lookup" % calls)
            return problems, ["getaddrinfo answers %r" % (answer,)]
        run_row(suite, GI, "public-only-policy-" + label, row, src)

    def nxdomain():
        calls = []
        with Swapped((socket, "getaddrinfo", refusing_resolver(calls))):
            got = outcome(lambda: mod._ch_public_only_policy("example.test", 443))
        return refused_with_prefix(got, CE, "policy: example.test does not resolve: ") + problem_if(len(calls) != 1, "resolver calls %r" % calls), ["a name that does not resolve: one line, one lookup"]
    run_row(suite, GI, "public-only-policy-unresolvable-name-refused", nxdomain, src)


def open_socket_rows(suite, mod):
    """_ch_open_socket (H2): ordered, one deadline, never resolves a name."""
    CE = mod.ChromeClientError
    src = "_ch_open_socket docstring (H2)"
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(8)
    port = lsock.getsockname()[1]
    dead = free_port()
    try:
        def ordered():
            s = mod._ch_open_socket(["::1", "127.0.0.1"], port, time.monotonic() + SESSION_TIMEOUT)
            try:
                peer = s.getpeername()[:2]
            finally:
                s.close()
            return problem_if(peer != ("127.0.0.1", port), "connected to %r" % (peer,)), ["::1 has no listener on the port (the listener binds 127.0.0.1): it fails, the second address wins"]
        run_row(suite, GI, "open-socket-first-address-fails-second-wins", ordered, src)

        def all_fail():
            got = outcome(lambda: mod._ch_open_socket(["127.0.0.1", "::1"], dead, time.monotonic() + SESSION_TIMEOUT))
            return refused_with_prefix(got, CE, "connect: all 2 addresses failed: ::1: "), ["a closed port on both: the count and the LAST error"]
        run_row(suite, GI, "open-socket-every-address-failing-names-the-count-and-last-error", all_fail, src)

        def empty():
            return expect_refusal(outcome(lambda: mod._ch_open_socket([], port, time.monotonic() + SESSION_TIMEOUT)), CE, "connect: no address to connect to"), ["an empty list"]
        run_row(suite, GI, "open-socket-empty-list-refused", empty, src)

        def names():
            calls = []
            with Swapped((socket, "getaddrinfo", refusing_resolver(calls))):
                got = outcome(lambda: mod._ch_open_socket(["localhost", "127.0.0.1"], port, time.monotonic() + SESSION_TIMEOUT))
                only = outcome(lambda: mod._ch_open_socket(["localhost"], port, time.monotonic() + SESSION_TIMEOUT))
            if got[0] == "value":
                got[1].close()
            problems = problem_if(got[0] != "value", "with a literal behind the name: %s" % shown(got))
            problems += expect_refusal(only, CE, "connect: all 1 addresses failed: 'localhost' is not an IP address")
            return problems + problem_if(calls, "getaddrinfo called %r" % calls), ["a name entry is a failed entry, never a lookup: zero getaddrinfo calls"]
        run_row(suite, GI, "open-socket-never-resolves-a-name-entry", names, src)

        def past():
            return expect_refusal(outcome(lambda: mod._ch_open_socket(["127.0.0.1"], port, time.monotonic() - 1)), CE, "timeout: connect deadline passed after 0 of 1 addresses"), ["a deadline already past"]
        run_row(suite, GI, "open-socket-past-deadline-refused", past, src)

        def blackhole():
            t0 = time.monotonic()
            got = outcome(lambda: mod._ch_open_socket(["192.0.2.1", "192.0.2.2"], 80, time.monotonic() + 1.0))
            took = time.monotonic() - t0
            if got[0] == "value":
                got[1].close()
                return ["TEST-NET-1 (RFC 5737) accepted a connection"], []
            text = str(got[1])
            ok = type(got[1]) is CE and (text.startswith("timeout: connect deadline passed after ") or text.startswith("connect: all 2 addresses failed: 192.0.2.2: "))
            return problem_if(not ok, "%s" % shown(got)), ["one 1 s deadline across two TEST-NET-1 addresses: %r after %.2fs (measured, never judged: an offline host fails fast)" % (text, took)]
        run_row(suite, GI, "open-socket-one-deadline-across-unroutable-addresses", blackhole, src + "; RFC 5737 TEST-NET-1")
    finally:
        lsock.close()


def session_policy_rows(suite, mod, s2, s1p, t12):
    """The policy in the session: hop 2 refused, ordered addresses, the connect override, every path through it."""
    CE = mod.ChromeClientError
    u2 = "https://a.test:%d" % s2.port
    src = "_ChSession class docstring: connect_policy(host, port) on every hop and retry; a new connection only over its list"
    s2.plan["/to-meta"] = (302, [("location", "https://meta.test:%d/echo" % s2.port)], b"")

    def meta_policy(calls):
        def policy(host, port):
            calls.append((host, port))
            if host != "meta.test":
                return ["127.0.0.1"]
            with Swapped((socket, "getaddrinfo", answering_resolver([], ["169.254.169.254"]))):
                return mod._ch_public_only_policy(host, port)
        return policy

    def hop2(s, calls):
        mark = s2.mark()
        got = outcome(lambda: s.get(u2 + "/to-meta"))
        seen, _conns = s2.since(mark)
        return (expect_refusal(got, CE, "policy: meta.test resolves to 169.254.169.254 (link-local)")
                + problem_if(calls != [("a.test", s2.port), ("meta.test", s2.port)], "policy calls %r" % calls)
                + problem_if([q["path"] for q in seen] != ["/to-meta"], "the peer saw %r" % [q["path"] for q in seen])), ["hop 1 vetted and served; hop 2 resolves to the metadata address under _ch_public_only_policy and is refused, no byte sent for it"]
    run_row(suite, GI, "policy-refusal-on-hop-2-metadata-address", lambda: with_policy(mod, hop2, meta_policy), src)

    def fixed(answer):
        def make(calls):
            def policy(host, port):
                calls.append((host, port))
                return list(answer)
            return policy
        return make

    def ordered(s, calls):
        opens = []
        real = mod._ch_open_socket

        def spy(addresses, port, deadline):
            opens.append(tuple(addresses))
            return real(addresses, port, deadline)
        with Swapped((mod, "_ch_open_socket", spy)):
            got = outcome(lambda: s.get(u2 + "/echo"))
        return (problem_if(content_of(got) != b"echo", "%s" % shown(got))
                + problem_if(opens != [("::1", "127.0.0.1")], "_ch_open_socket got %r" % opens)), ["the policy answers [::1, 127.0.0.1]: ::1 has no listener, 127.0.0.1 serves, one call deadline"]
    run_row(suite, GI, "session-ordered-addresses-first-fails-second-serves", lambda: with_policy(mod, ordered, fixed(["::1", "127.0.0.1"])), src)

    dead = free_port()

    def all_fail(s, _calls):
        got = outcome(lambda: s.get("https://a.test:%d/echo" % dead))
        return refused_with_prefix(got, CE, "connect: all 2 addresses failed: ::1: "), ["every address of the list refuses: the count and the last error"]
    run_row(suite, GI, "session-every-address-failing-names-the-count-and-last-error", lambda: with_policy(mod, all_fail, fixed(["127.0.0.1", "::1"])), src)

    def override(s, calls):
        gai = []
        mark = s2.mark()
        with Swapped((socket, "getaddrinfo", refusing_resolver(gai))):
            got = outcome(lambda: s.get("https://example.test:%d/echo" % s2.port))
        seen, _conns = s2.since(mark)
        return (problem_if(content_of(got) != b"echo", "%s" % shown(got))
                + problem_if(gai, "getaddrinfo called %r" % gai)
                + problem_if(calls != [("example.test", s2.port)], "policy calls %r" % calls)
                + problem_if([q["authority"] for q in seen] != ["example.test:%d" % s2.port], "authorities %r" % [q["authority"] for q in seen])), ["URL example.test, peer on 127.0.0.1 by the policy's word; getaddrinfo replaced by a raising recorder: zero calls"]
    run_row(suite, GI, "connect-override-used-and-getaddrinfo-never-called-with-a-policy", lambda: with_session(mod, override), src)

    p1 = "http://a.test:%d" % s1p.port

    def cleartext(s, calls):
        r = s.get(p1 + "/echo")
        return problem_if(r.content != b"echo" or calls != [("a.test", s1p.port)], "body %r, policy calls %r" % (r.content[:20], calls)), ["a cleartext http hop is vetted like any other"]
    run_row(suite, GI, "policy-called-for-a-cleartext-hop", lambda: with_session(mod, cleartext), src)

    def fallback_hop(s, calls):
        mark = t12.mark()
        r = s.get("https://localhost:%d/" % t12.port)
        seen, accepts = t12.since(mark)
        return (problem_if(r.impersonated is not False or r.content != b"hello fallback", "impersonated=%r body %r" % (r.impersonated, r.content[:20]))
                + problem_if(calls != [("localhost", t12.port)], "policy calls %r" % calls)
                + problem_if(len(accepts) != 2 or len(seen) != 1, "%d connection(s), %d request(s)" % (len(accepts), len(seen)))), ["the TLS 1.2 hop: ONE policy call, its list serving both the Chrome attempt and the fallback"]
    run_row(suite, GI, "policy-called-for-a-tls12-fallback-hop", lambda: with_session(mod, fallback_hop, tls12_fallback=True, ssl_context_factory=trusted_context), src + "; D10")

    def again():
        calls = []
        policy = counting_policy(calls)
        mark = s2.mark()
        for _session in range(2):
            s = mod._ch_session_new(connect_policy=policy, timeout=SESSION_TIMEOUT, transport="chrome")
            try:
                s.get(u2 + "/echo")
                if _session == 0:
                    s.get(u2 + "/echo")
            finally:
                s.close()
        seen, conns = s2.since(mark)
        return problem_if(len(calls) != 3 or conns != 2 or len(seen) != 3, "%d policy call(s), %d connection(s), %d request(s)" % (len(calls), conns, len(seen))), ["two requests on one session (pooled connection, policy asked again) and one on a fresh session: 3 policy calls, 2 connections"]
    run_row(suite, GI, "policy-called-per-request-pooled-and-on-a-fresh-session", again, src)


def url_rows(suite, mod, s2):
    """Redirect schemes under a permit-everything policy; userinfo; IDNA, the trailing dot and the SNI against the policy host."""
    CE = mod.ChromeClientError
    u2 = "https://a.test:%d" % s2.port
    for label, location in (("ftp", "ftp://a.test/x"), ("file", "file:///etc/passwd"), ("gopher", "gopher://a.test:70/1"), ("data", "data:text/plain,x")):
        s2.plan["/to-" + label] = (302, [("location", location)], b"")

        def scheme(s, calls, label=label, location=location):
            got = outcome(lambda: s.get(u2 + "/to-" + label))
            return (expect_refusal(got, CE, "redirect: scheme %s refused" % label)
                    + problem_if(calls != [("a.test", s2.port)], "policy calls %r" % calls)), ["Location %s under a permit-everything policy: refused before any policy call for the hop" % location]
        run_row(suite, GI, "redirect-to-%s-refused-under-a-permit-all-policy" % label, lambda scheme=scheme: with_session(mod, scheme), "Fetch: only http(s) is followed; _ChSession class docstring")

    s2.plan["/to-bad-port"] = (302, [("location", "https://a.test:25/x")], b"")

    def bad_port(s, calls):
        got = outcome(lambda: s.get(u2 + "/to-bad-port"))
        return (expect_refusal(got, CE, "url: port 25 refused")
                + problem_if(calls != [("a.test", s2.port)], "policy calls %r" % calls)), ["Location https://a.test:25/x under a permit-everything policy: refused before any policy call for the hop"]
    run_row(suite, GI, "redirect-to-a-fetch-bad-port-refused-under-a-permit-all-policy", lambda: with_session(mod, bad_port), "Fetch standard port blocking; _ch_split_url vets every hop")

    src = "_ch_split_url docstring: the ONE normaliser; its host is what the policy vets, the SNI and :authority"

    def userinfo(s, calls):
        mark = s2.mark()
        got = outcome(lambda: s.get("https://user@example.test:%d/echo" % s2.port))
        _seen, conns = s2.since(mark)
        return expect_refusal(got, CE, "url: userinfo not supported") + problem_if(calls or conns, "policy calls %r, %d connection(s)" % (calls, conns)), ["refused before the policy and before any socket"]
    run_row(suite, GI, "url-userinfo-refused-before-the-policy", lambda: with_session(mod, userinfo), src)

    def sni_row(host, want_host, want_sni):
        def row(s, calls):
            mark, n0 = s2.mark(), len(s2.snis)
            r = s.get("https://%s:%d/echo" % (host, s2.port))
            seen, _conns = s2.since(mark)
            snis = [alabel(n) for n in s2.snis[n0:]]
            return (problem_if(r.content != b"echo", "body %r" % r.content[:20])
                    + problem_if(calls != [(want_host, s2.port)], "policy calls %r, wanted host %r" % (calls, want_host))
                    + problem_if(snis != [want_sni], "SNI the peer recorded %r, wanted %r" % (snis, want_sni))
                    + problem_if([q["authority"] for q in seen] != ["%s:%d" % (want_host, s2.port)], ":authority %r" % [q["authority"] for q in seen])), ["policy host %r, :authority %r, SNI %r" % (want_host, "%s:%d" % (want_host, s2.port), want_sni)]
        return row
    idna = alabel(u"b\u00fccher.test")
    run_row(suite, GI, "url-non-ascii-host-reaches-the-policy-as-its-idna-a-label", lambda: with_session(mod, sni_row(u"b\u00fccher.test", idna, idna)), src + "; A-label by the stdlib idna codec")
    run_row(suite, GI, "url-trailing-dot-kept-for-policy-and-authority-dropped-in-sni", lambda: with_session(mod, sni_row("example.test.", "example.test.", "example.test")), src + "; RFC 6066 section 3: HostName carries no trailing dot")
    run_row(suite, GI, "policy-host-equals-the-sni-the-peer-records", lambda: with_session(mod, sni_row("a.test", "a.test", "a.test")), src)


def deadline_reconnect_rows(suite, mod, cc):
    """The call deadline against a slow-loris peer; the reconnect after an idle GOAWAY is re-vetted (R2-L11)."""
    CE = mod.ChromeClientError
    loris = RawPeer(slow_loris)
    try:
        def row(s, _calls):
            t0 = time.monotonic()
            got = outcome(lambda: s.get("http://a.test:%d/echo" % loris.port, timeout=LORIS_TIMEOUT))
            took = time.monotonic() - t0
            return refused_with_prefix(got, CE, "timeout:"), ["%d-byte response one byte per %.2fs (%.1fs in all), call timeout %ds: %r after %.1fs (measured, not judged)" % (len(LORIS_RESPONSE), LORIS_GAP_S, len(LORIS_RESPONSE) * LORIS_GAP_S, LORIS_TIMEOUT, str(got[1])[:60] if got[0] == "exc" else None, took)]
        run_row(suite, GI, "call-deadline-holds-across-a-slow-loris-peer", lambda: with_session(mod, row), "D11: _ChDeadlineSocket, one deadline per call")
    finally:
        loris.close()

    src = "R2-L11; _ChSession class docstring (retry once per hop, on a fresh connection re-vetted by the policy)"
    for mode, method, retried in (("goaway", "GET", True), ("close", "GET", True), ("close", "POST", False)):
        def reuse(mode=mode, method=method, retried=retried):
            peer = ReusePeer(cc, mode)
            calls = []
            s = mod._ch_session_new(connect_policy=counting_policy(calls), timeout=SESSION_TIMEOUT, transport="chrome")
            base = "https://a.test:%d/echo" % peer.port
            try:
                first = outcome(lambda: s.get(base) if method == "GET" else s.post(base, data=b"k=1"))
                second = outcome(lambda: s.get(base) if method == "GET" else s.post(base, data=b"k=2"))
            finally:
                s.close()
                peer.close()
            problems = problem_if(content_of(first) != b"echo", "first request: %s" % shown(first))
            if retried:
                problems += problem_if(content_of(second) != b"echo", "second request: %s" % shown(second))
                problems += problem_if(len(calls) != 3 or peer.conns != 2, "%d policy call(s) over %d connection(s), wanted 3 over 2" % (len(calls), peer.conns))
                detail = "%s: the reused connection fails, ONE transparent retry on a new connection, re-vetted (policy calls 1, 2, 3)" % method
            else:
                problems += refused_with_prefix(second, CE, "h2:")
                problems += problem_if(len(calls) != 2 or peer.conns != 1 or len(peer.requests) != 2, "%d policy call(s), %d connection(s), %d request(s): the POST was re-sent" % (len(calls), peer.conns, len(peer.requests)))
                detail = "POST: the connection closed after the request was read (it may have been processed): no silent retry"
            return problems, ["peer mode %s: %s" % (mode, detail)]
        cid = {"goaway": "idle-goaway-get-reconnects-transparently-with-one-more-policy-call",
               "close": "closed-keep-alive-%s" % ("get-retried-once-re-vetted" if retried else "post-not-retried-silently")}[mode]
        run_row(suite, GI, cid, reuse, src)


def group_limits_policy(suite, mod, cc):
    """Group I: body and decode limits, on_headers, the SSRF classifier and policy, the opener, and every path through the policy."""
    s2 = SessionPeer("h2", cc)
    s1p = SessionPeer("h1-plain", cc)
    t12 = Tls12Peer()
    two = noise(3000, b"two-layer")
    raw = noise(2000, b"raw-deflate")
    try:
        limit_plans(s2, s1p, two, raw)
        limits_rows(suite, mod, s2, s1p)
        decode_limit_rows(suite, mod, s2, two, raw)
        on_headers_rows(suite, mod, s2, s1p)
        address_rows(suite, mod)
        public_policy_rows(suite, mod)
        open_socket_rows(suite, mod)
        session_policy_rows(suite, mod, s2, s1p, t12)
        url_rows(suite, mod, s2)
        deadline_reconnect_rows(suite, mod, cc)
    finally:
        for srv in (s2, s1p, t12):
            srv.close()


# --- J. TLS 1.2 fallback, environment, late binding --------------------------------------

LATE_NAME = "_ch_open_socket"


def docstring_ids(tree):
    """id() of every module/class/function docstring Constant: prose may name a path, code may not."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def trust_problems(tree):
    """SEC-LOW: no test-CA path in code, no cafile/capath/cadata, no load_verify_locations, nothing that builds an unverified context."""
    problems = []
    docs = docstring_ids(tree)
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and "tests/files" in node.value:
            problems.append("line %d: a tests/files string in code" % line)
        elif isinstance(node, ast.keyword) and node.arg in ("cafile", "capath", "cadata"):
            problems.append("line %d: %s= keyword" % (line, node.arg))
        elif isinstance(node, ast.keyword) and node.arg == "check_hostname" and isinstance(node.value, ast.Constant) and node.value.value is False:
            problems.append("line %d: check_hostname=False" % line)
        elif isinstance(node, ast.Attribute) and node.attr in ("load_verify_locations", "_create_unverified_context", "_create_stdlib_context", "CERT_NONE", "CERT_OPTIONAL"):
            problems.append("line %d: .%s" % (line, node.attr))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant) and node.value.value is False:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "check_hostname" for t in targets):
                problems.append("line %d: check_hostname = False" % line)
    return problems


def environ_problems(tree):
    """R-0017: no os.environ / getenv / putenv read or write, no urllib.request (whose getproxies reads *_PROXY)."""
    problems = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "environb", "getenv", "getenvb", "putenv", "unsetenv", "getproxies", "proxy_bypass"):
            problems.append("line %d: .%s" % (line, node.attr))
        elif isinstance(node, ast.Attribute) and node.attr == "request" and isinstance(node.value, ast.Name) and node.value.id == "urllib":
            problems.append("line %d: urllib.request" % line)
        elif isinstance(node, ast.Import) and any(a.name == "urllib.request" or a.name.startswith("urllib.request.") for a in node.names):
            problems.append("line %d: import urllib.request" % line)
        elif isinstance(node, ast.ImportFrom) and ((node.module or "").startswith("urllib.request") or (node.module == "urllib" and any(a.name == "request" for a in node.names))):
            problems.append("line %d: from urllib import request" % line)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and any(a.name in ("environ", "environb", "getenv", "getenvb", "putenv", "unsetenv") for a in node.names):
            problems.append("line %d: from os import an environment accessor" % line)
    return problems


def late_binding_problems(tree, name=LATE_NAME):
    """(problems, call sites): `name` is never a default, never stored on an attribute or a class body, only ever the func of a Call."""
    problems = []

    def refers(node):
        return node is not None and any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(node))
    funcs = set(id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call))
    calls = 0
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if any(refers(d) for d in list(node.args.defaults) + list(node.args.kw_defaults)):
                problems.append("line %d: a function default binds %s" % (line, name))
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and refers(stmt.value):
                    problems.append("line %d: a class-body assignment references %s" % (stmt.lineno, name))
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in targets) and refers(node.value):
                problems.append("line %d: an attribute assignment stores %s" % (line, name))
        if isinstance(node, ast.Name) and node.id == name:
            if id(node) in funcs:
                calls += 1
            else:
                problems.append("line %d: %s referenced without being called" % (line, name))
    return problems, calls


# Planted defects each AST checker of group J must catch (its negative control).
PLANTED_TRUST = "import ssl\nc = ssl._create_unverified_context()\nd = ssl.create_default_context(cafile='tests/files/tls/test-ca.pem')\nd.check_hostname = False\nd.verify_mode = ssl.CERT_NONE\n"
PLANTED_ENVIRON = "import os\nimport urllib.request\nfrom os import getenv\nx = os.environ.get('HTTPS_PROXY')\ny = urllib.request.getproxies()\n"
PLANTED_LATE = "def _ch_open_socket(a, p, d):\n    return None\n\n\ndef f(opener=_ch_open_socket):\n    return opener\n\n\nclass C:\n    o = _ch_open_socket\n\n    def g(self):\n        self.o = _ch_open_socket\n        return _ch_open_socket(1, 2, 3)\n"


def fallback_rows(suite, mod, t12):
    """D10 over a real TLS 1.2 peer: off, verified by the test CA, every verify refusal, limits, on_headers, redirect, HEAD, POST, timeout."""
    CE, T12 = mod.ChromeClientError, mod.ChromeTls12Error
    url = "https://localhost:%d" % t12.port
    src = "D10 / FLAG-4: _ChFallbackConnection and _ChSession docstrings; tests/files/tls (the test CA is the only anchor)"
    t12.plan["/big"] = (200, [("Content-Type", "text/plain")], b"x" * 1000)
    t12.plan["/gz"] = (200, [("Content-Type", "text/plain"), ("Content-Encoding", "gzip")], gz(b"a" * 5000))
    t12.plan["/r"] = (302, [("Location", "/final"), ("Set-Cookie", "k=v; Path=/")], b"")
    t12.plan["/final"] = (200, [("Content-Type", "text/plain")], b"final")
    t12.plan["/hang"] = "hang"
    trusted = {"tls12_fallback": True, "ssl_context_factory": trusted_context}

    def off(s, _calls):
        mark = t12.mark()
        got = outcome(lambda: s.get(url + "/"))
        seen, accepts = t12.since(mark)
        return expect_refusal(got, T12, "tls: server chose TLS 1.2") + problem_if(seen or len(accepts) != 1, "%d connection(s), %d request(s)" % (len(accepts), len(seen))), ["tls12_fallback=False: ChromeTls12Error from the ServerHello, one connection, no request"]
    run_row(suite, GJ, "tls12-server-without-the-flag-raises-chrome-tls12-error", lambda: with_session(mod, off, ssl_context_factory=trusted_context), src)

    def verified(s, calls):
        opens, gai = [], []
        real = mod._ch_open_socket

        def spy(addresses, port, deadline):
            opens.append((tuple(addresses), port))
            return real(addresses, port, deadline)
        mark = t12.mark()
        with Swapped((mod, "_ch_open_socket", spy), (socket, "getaddrinfo", refusing_resolver(gai))):
            got = outcome(lambda: s.get(url + "/"))
        seen, accepts = t12.since(mark)
        if got[0] == "exc":
            return ["raised %s(%r)" % (type(got[1]).__name__, str(got[1])[:160])], []
        r = got[1]
        problems = problem_if(r.status_code != 200 or r.content != b"hello fallback", "%r %r" % (r.status_code, r.content[:20]))
        problems += problem_if(r.impersonated is not False or r.cert_verified is not True, "impersonated=%r cert_verified=%r" % (r.impersonated, r.cert_verified))
        problems += problem_if(r.http_version != "HTTP/1.1" or not r.tls or r.tls.get("version") != "TLSv1.2", "version %r tls %r" % (r.http_version, r.tls))
        problems += problem_if(opens != [(("127.0.0.1",), t12.port)] * 2, "_ch_open_socket calls %r: wanted the policy's list twice (Chrome attempt, ONE re-issue)" % opens)
        problems += problem_if(len(accepts) != 2 or len(seen) != 1 or calls != [("localhost", t12.port)] or gai, "%d connection(s), %d request(s), policy %r, getaddrinfo %r" % (len(accepts), len(seen), calls, gai))
        return problems, ["test CA as the only anchor: impersonated=False, cert_verified=True, %s; exactly one re-issue over the policy's address list, zero getaddrinfo" % r.tls.get("version")]
    run_row(suite, GJ, "tls12-fallback-verified-by-the-test-ca-impersonated-false-cert-verified-true", lambda: with_session(mod, verified, **trusted), src)

    def default_ctx(s, _calls):
        mark = t12.mark()
        got = outcome(lambda: s.get(url + "/"))
        seen, _accepts = t12.since(mark)
        problems = refused_with_prefix(got, CE, "tls12-fallback: certificate verify failed:")
        return problems + problem_if(seen, "the peer served %d request(s)" % len(seen)), ["the default context (system anchors only) refuses the test leaf: %r" % (str(got[1])[:100] if got[0] == "exc" else None)]
    run_row(suite, GJ, "tls12-fallback-default-context-refuses-the-test-leaf", lambda: with_session(mod, default_ctx, tls12_fallback=True), src)

    def mismatch(s, calls):
        gai = []
        mark = t12.mark()
        with Swapped((socket, "getaddrinfo", refusing_resolver(gai))):
            got = outcome(lambda: s.get("https://example.test:%d/" % t12.port))
        seen, accepts = t12.since(mark)
        problems = refused_with_prefix(got, CE, "tls12-fallback: certificate verify failed:")
        problems += problem_if(accepts != ["127.0.0.1", "127.0.0.1"] or seen, "peer connections from %r, %d request(s)" % (accepts, len(seen)))
        return problems + problem_if(calls != [("example.test", t12.port)] or gai, "policy %r, getaddrinfo %r" % (calls, gai)), ["URL host example.test, the policy says 127.0.0.1: the fallback connected THERE (the peer saw 127.0.0.1 twice, no lookup) and verified the name against example.test, which the leaf does not carry"]
    run_row(suite, GJ, "tls12-fallback-connects-to-the-policy-list-and-verifies-the-url-host", lambda: with_session(mod, mismatch, **trusted), src)

    def unverified_factory():
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c

    def unverified(s, _calls):
        mark = t12.mark()
        got = outcome(lambda: s.get(url + "/"))
        seen, accepts = t12.since(mark)
        return (expect_refusal(got, CE, "tls12-fallback: the ssl context does not verify the certificate and the host name")
                + problem_if(seen or len(accepts) != 1, "%d connection(s), %d request(s)" % (len(accepts), len(seen)))), ["a factory returning CERT_NONE / check_hostname=False: refused before the fallback connects"]
    run_row(suite, GJ, "tls12-fallback-non-verifying-context-refused", lambda: with_session(mod, unverified, tls12_fallback=True, ssl_context_factory=unverified_factory), src)

    def wire(s, _calls):
        return too_large(outcome(lambda: s.get(url + "/big")), mod, 100), ["max_bytes=100, a 1000-byte body on the fallback: .limit=100"]
    run_row(suite, GJ, "tls12-fallback-max-bytes-wire-refused", lambda: with_session(mod, wire, max_bytes=100, **trusted), src)

    def decoded(s, _calls):
        return too_large(outcome(lambda: s.get(url + "/gz")), mod, 100), ["max_bytes=100, gzip decoding to 5000 bytes on the fallback: .limit=100"]
    run_row(suite, GJ, "tls12-fallback-max-bytes-decoded-refused", lambda: with_session(mod, decoded, max_bytes=100, **trusted), src)

    def callback(s, _calls):
        stop = CallbackRefusal("refused by the callback")

        def refuse(_status, _headers, _url):
            raise stop
        got = outcome(lambda: s.get(url + "/big", on_headers=refuse))
        return problem_if(got[0] != "exc" or got[1] is not stop, "%s, wanted the callback's own exception" % shown(got)), ["on_headers raising on the fallback path: the exception propagates unchanged"]
    run_row(suite, GJ, "tls12-fallback-on-headers-exception-propagates-unchanged", lambda: with_session(mod, callback, **trusted), src)

    def redirect(s, _calls):
        mark = t12.mark()
        r = s.get(url + "/r")
        seen, accepts = t12.since(mark)
        cookie = [v for k, v in seen[-1]["fields"] if k.lower() == "cookie"] if seen else None
        return (problem_if(r.content != b"final" or not r.url.endswith("/final") or r.impersonated is not False or r.cert_verified is not True, "final %r %r impersonated=%r" % (r.content[:20], r.url, r.impersonated))
                + problem_if(len(accepts) != 4 or len(seen) != 2, "%d connection(s), %d request(s): each hop tries the Chrome path first" % (len(accepts), len(seen)))
                + problem_if(cookie != ["k=v"], "hop 2 Cookie %r" % cookie)), ["302 + Set-Cookie on the fallback, followed: hop 2 tries Chrome again (4 connections), carries k=v"]
    run_row(suite, GJ, "tls12-fallback-redirect-cookie-carried-and-chrome-tried-again", lambda: with_session(mod, redirect, **trusted), src)

    def head(s, _calls):
        mark = t12.mark()
        r = s.head(url + "/big")
        seen, _accepts = t12.since(mark)
        return problem_if(r.status_code != 200 or r.content != b"" or [q["method"] for q in seen] != ["HEAD"], "%r %r %r" % (r.status_code, r.content[:20], [q["method"] for q in seen])), ["cors HEAD through the fallback: no body read"]
    run_row(suite, GJ, "tls12-fallback-head", lambda: with_session(mod, head, **trusted), src)

    def post(s, _calls):
        mark = t12.mark()
        r = s.post(url + "/final", data={"a": "1"})
        seen, _accepts = t12.since(mark)
        return problem_if(r.content != b"final" or [(q["method"], q["body"]) for q in seen] != [("POST", b"a=1")], "%r %r" % (r.content[:20], [(q["method"], q["body"]) for q in seen])), ["cors POST a=1 through the fallback"]
    run_row(suite, GJ, "tls12-fallback-cors-post", lambda: with_session(mod, post, **trusted), src)

    def hang(s, _calls):
        got = outcome(lambda: s.get(url + "/hang", timeout=LORIS_TIMEOUT))
        return refused_with_prefix(got, CE, "timeout:"), ["a peer that never answers: %r (the message, never the elapsed time)" % (str(got[1])[:60] if got[0] == "exc" else None)]
    run_row(suite, GJ, "tls12-fallback-silent-peer-times-out", lambda: with_session(mod, hang, **trusted), src)


def fallback_injection_rows(suite, mod, t12):
    """R2-M4: a CR/LF caller value never reaches the TLS 1.2 peer; the white-box fallback turns every ValueError into one line."""
    CE = mod.ChromeClientError
    url = "https://localhost:%d" % t12.port
    src = "R2-M4: _ch_check_caller_headers; _ChFallbackConnection docstring (never a bare ValueError, never the value)"

    def caller(s, calls):
        mark = t12.mark()
        got = outcome(lambda: s.get(url + "/", headers={"X-Test": "a\r\nInjected: 1"}))
        seen, accepts = t12.since(mark)
        return (expect_refusal(got, CE, "headers: 'X-Test' contains CR, LF or NUL")
                + problem_if(calls or accepts or seen, "policy %r, %d connection(s)" % (calls, len(accepts)))), ["refused before the first hop: no policy call, the TLS 1.2 peer sees no connection"]
    run_row(suite, GJ, "tls12-fallback-caller-crlf-refused-before-the-first-hop", lambda: with_session(mod, caller, tls12_fallback=True, ssl_context_factory=trusted_context), src)

    fields = [(":method", "GET"), (":authority", "localhost:%d" % t12.port), (":scheme", "https"), (":path", "/"), ("x-bad", "a\r\nInjected: 1")]

    def builder():
        mark = t12.mark()
        fb = mod._ChFallbackConnection("localhost", t12.port, ["127.0.0.1"], trusted_context(), time.monotonic() + SESSION_TIMEOUT)
        try:
            got = outcome(lambda: fb.start(fields))
        finally:
            fb.close()
        _seen, accepts = t12.since(mark)
        problems = refused_with_prefix(got, CE, "tls12-fallback:")
        problems += problem_if(got[0] == "exc" and "Injected" in str(got[1]), "the message echoes the value")
        return problems + problem_if(accepts, "%d connection(s) opened" % len(accepts)), ["an UNCHECKED CR/LF header handed to _ChFallbackConnection.start: %r, no connection" % (str(got[1])[:80] if got[0] == "exc" else None)]
    run_row(suite, GJ, "tls12-fallback-white-box-unchecked-crlf-is-a-one-line-refusal", builder, src)

    def mapped():
        def bad_put(self, header, *values):
            raise ValueError("Invalid header value %r" % (b"secret\r\n",))
        fb = mod._ChFallbackConnection("localhost", t12.port, ["127.0.0.1"], trusted_context(), time.monotonic() + SESSION_TIMEOUT)
        try:
            with Swapped((http.client.HTTPConnection, "putheader", bad_put)):
                got = outcome(lambda: fb.start([(":method", "GET"), (":authority", "localhost"), (":scheme", "https"), (":path", "/"), ("accept", "x")]))
        finally:
            fb.close()
        return (expect_refusal(got, CE, "tls12-fallback: Invalid header value")
                + problem_if(got[0] == "exc" and "secret" in str(got[1]), "the value leaked")), ["http.client's ValueError (putheader replaced for the row) cut before the first quoted value"]
    run_row(suite, GJ, "tls12-fallback-http-client-value-error-mapped-without-the-value", mapped, src)


def proxy_row(suite, mod, t12, s2):
    """Every *_PROXY variable at a closed port: the Chrome path and the fallback still reach their loopback peers directly."""
    def row():
        dead = free_port()
        saved = dict((k, os.environ.get(k)) for k in PROXY_VARS)
        calls = []
        try:
            for k in PROXY_VARS:
                os.environ[k] = "http://127.0.0.1:%d" % dead
            s = mod._ch_session_new(connect_policy=counting_policy(calls), timeout=SESSION_TIMEOUT, tls12_fallback=True, ssl_context_factory=trusted_context, transport="chrome")
            try:
                m2, m12 = s2.mark(), t12.mark()
                r2 = outcome(lambda: s.get("https://a.test:%d/echo" % s2.port))
                r12 = outcome(lambda: s.get("https://localhost:%d/" % t12.port))
                got2, _conns = s2.since(m2)
                got12, _accepts = t12.since(m12)
            finally:
                s.close()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        restored = all(os.environ.get(k) == v for k, v in saved.items())
        return (problem_if(content_of(r2) != b"echo" or len(got2) != 1, "Chrome path: %s, peer saw %d" % (shown(r2), len(got2)))
                + problem_if(content_of(r12) != b"hello fallback" or len(got12) != 1, "fallback: %s, peer saw %d" % (shown(r12), len(got12)))
                + problem_if(not restored, "the environment was not restored")), ["%s -> http://127.0.0.1:%d (closed) for the calls, restored in finally: both reached their peers directly" % ("/".join(PROXY_VARS), dead)]
    run_row(suite, GJ, "proxy-variables-at-a-closed-port-ignored-by-both-paths", row, "R-0017: no *_PROXY, ever")


def source_rows(suite, mod, tree):
    """The default context, trust material and the environment in the AST; the late binding of _ch_open_socket; planted controls."""
    def default_factory():
        s = mod._ch_session_new()
        try:
            factory = s._ssl_context_factory
        finally:
            s.close()
        problems = problem_if(factory is not ssl.create_default_context, "the default factory is %r" % (factory,))
        calls = [c for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "_fallback"
                 for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "_ssl_context_factory"]
        problems += problem_if(len(calls) != 1 or calls[0].args or calls[0].keywords, "_ChSession._fallback calls the factory %d time(s), with arguments on %r" % (len(calls), [c.lineno for c in calls if c.args or c.keywords]))
        return problems + trust_problems(tree), ["_ch_session_new(): ssl_context_factory is ssl.create_default_context, called once with NO argument; no tests/files string, cafile/capath/cadata, load_verify_locations, CERT_NONE/OPTIONAL, check_hostname=False or _create_unverified_context in code"]
    run_row(suite, GJ, "default-context-is-create-default-context-no-arguments-no-test-ca-in-code", default_factory, "SEC-LOW; _ch_session_new docstring")

    def environ():
        return environ_problems(tree), ["no os.environ / getenv / putenv, no urllib.request or getproxies anywhere in Scripts/_mcp_chrome.py"]
    run_row(suite, GJ, "ast-no-os-environ-read-and-no-urllib-request", environ, "R-0017: nothing is read from the environment")

    def late():
        problems, calls = late_binding_problems(tree)
        return problems + problem_if(calls < 2, "%d call site(s), wanted the session's _open and the fallback's connect" % calls), ["%s: never a default, an attribute or a class-body value; all %d references are direct calls by the global name" % (LATE_NAME, calls)]
    run_row(suite, GJ, "ast-ch-open-socket-only-ever-called-by-its-global-name", late, "_ch_open_socket docstring: late binding")

    def controls():
        trust = trust_problems(ast.parse(PLANTED_TRUST))
        env = environ_problems(ast.parse(PLANTED_ENVIRON))
        late_found, _n = late_binding_problems(ast.parse(PLANTED_LATE))
        problems = problem_if(len(trust) < 4, "the trust checker caught %d of 4: %r" % (len(trust), trust))
        problems += problem_if(len(env) < 4, "the environment checker caught %d of 4: %r" % (len(env), env))
        kinds = ("function default", "class-body", "attribute assignment")
        problems += problem_if(not all(any(k in p for p in late_found) for k in kinds), "the late-binding checker missed one of %r: %r" % (kinds, late_found))
        return problems, ["planted: unverified context, cafile, check_hostname=False, CERT_NONE; os.environ, getenv, urllib.request; a default, a class-body value, a self attribute"]
    run_row(suite, GJ, "control-ast-checkers-catch-planted-defects", controls, "negative control")


def late_binding_twin_row(suite, mod, t12, s2):
    """Behavioural twin: _ch_open_socket replaced AFTER the session and a fallback connection exist; both go through it."""
    def row():
        seen = []
        real = mod._ch_open_socket
        s = mod._ch_session_new(connect_policy=counting_policy([]), timeout=SESSION_TIMEOUT, tls12_fallback=True, ssl_context_factory=trusted_context, transport="chrome")
        fb = mod._ChFallbackConnection("localhost", t12.port, ["127.0.0.1"], trusted_context(), time.monotonic() + SESSION_TIMEOUT)

        def recorder(addresses, port, deadline):
            seen.append(port)
            return real(addresses, port, deadline)
        try:
            with Swapped((mod, "_ch_open_socket", recorder)):
                r2 = outcome(lambda: s.get("https://a.test:%d/echo" % s2.port))
                after_session = list(seen)
                fb.connect()
                after_connect = list(seen)
                r12 = outcome(lambda: s.get("https://localhost:%d/" % t12.port))
        finally:
            fb.close()
            s.close()
        problems = problem_if(content_of(r2) != b"echo" or after_session != [s2.port], "session request: %s, recorder saw %r" % (shown(r2), after_session))
        problems += problem_if(after_connect != [s2.port, t12.port], "_ChFallbackConnection.connect: recorder saw %r" % after_connect)
        problems += problem_if(content_of(r12) != b"hello fallback" or seen != [s2.port, t12.port, t12.port, t12.port], "fallback request: %s, recorder saw %r" % (shown(r12), seen))
        return problems + problem_if(mod._ch_open_socket is not real, "_ch_open_socket not restored"), ["the recorder saw the Chrome request, the pre-built fallback's connect(), then the TLS 1.2 hop's Chrome attempt and its fallback"]
    run_row(suite, GJ, "late-binding-twin-a-replaced-ch-open-socket-reaches-session-and-fallback", row, "_ch_open_socket docstring: late binding")


def group_fallback_proxy(suite, mod, cc, tree):
    """Group J: the verified TLS 1.2 fallback, *_PROXY ignored, the AST rows and the late binding of _ch_open_socket."""
    t12 = Tls12Peer()
    s2 = SessionPeer("h2", cc)
    try:
        fallback_rows(suite, mod, t12)
        fallback_injection_rows(suite, mod, t12)
        proxy_row(suite, mod, t12, s2)
        source_rows(suite, mod, tree)
        late_binding_twin_row(suite, mod, t12, s2)
    finally:
        for srv in (t12, s2):
            srv.close()


# --- P. transport ---------------------------------------------------------------

# This file, read for the call-site rule (D16 C1).
TEST_SOURCE = os.path.abspath(__file__)

# The two helpers whose `kw.setdefault("transport", "chrome")` stands in for a transport= keyword.
TRANSPORT_HELPERS = ("with_session", "with_policy")

# Functions whose _ch_session_new call means the DEFAULT on purpose (declared, hard-coded, D16 C1).
DECLARED_DEFAULT = ("default_factory", "default_transport")

# The planted source the call-site checker must catch: a row calling without transport=, a helper without the setdefault.
PLANTED_SESSION_NEW = "def planted_row(mod):\n    return mod._ch_session_new(connect_policy=None)\n\n\ndef fine_row(mod):\n    return mod._ch_session_new(transport='chrome')\n\n\ndef with_session(mod, **kw):\n    return mod._ch_session_new(**kw)\n"


class VerifiedPeer(SessionPeer):
    """An HTTP/1.1-over-TLS SessionPeer on the tests/files/tls leaf that also records each handshake's ALPN and version.

    Its context is h1_server_context (TLS 1.3, ALPN http/1.1): a client that
    offers ALPN gets "http/1.1" (the Chrome engine always offers h2 and
    http/1.1), one that offers none gets None. A handshake the client
    refuses (the default context against the test leaf) records nothing.
    With `stall` it reads after the handshake and never answers.
    """

    def __init__(self, cc, stall=False):
        self.stall = stall
        self.alpns = []
        self.versions = []
        SessionPeer.__init__(self, "h1-tls", cc)

    def _serve(self, sock, cid):
        try:
            sock.settimeout(SESSION_TIMEOUT)
            try:
                sock = self.ctx.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError):
                return
            self.alpns.append(sock.selected_alpn_protocol())
            self.versions.append(sock.version())
            if self.stall:
                while sock.recv(65536):
                    pass
                return
            self._h1(sock, cid)
        except Exception as exc:  # a server thread records, never prints a traceback
            self.errors.append("%s(%r)" % (type(exc).__name__, str(exc)[:120]))
        finally:
            sock.close()


def session_new_calls(tree):
    """[(call node, innermost enclosing function name or None)] for every call of _ch_session_new in `tree`."""
    out = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
                if name == "_ch_session_new":
                    out.append((child, owner))
            visit(child, owner)
    visit(tree, None)
    return out


def helper_sets_chrome(func):
    """True when `func` calls `kw.setdefault("transport", "chrome")`."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault" and len(node.args) == 2:
            a, b = node.args
            if isinstance(a, ast.Constant) and a.value == "transport" and isinstance(b, ast.Constant) and b.value == "chrome":
                return True
    return False


def call_site_problems(tree):
    """(problems, calls): every _ch_session_new call has a transport keyword, sits in a helper that sets it, or is declared default."""
    problems = []
    calls = session_new_calls(tree)
    for call, owner in calls:
        if any(k.arg == "transport" for k in call.keywords):
            continue
        if owner in TRANSPORT_HELPERS or owner in DECLARED_DEFAULT:
            continue
        problems.append("line %d in %s: _ch_session_new without transport=" % (call.lineno, owner or "<module>"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in TRANSPORT_HELPERS and not helper_sets_chrome(node):
            problems.append("line %d: helper %s does not setdefault transport to chrome" % (node.lineno, node.name))
    return problems, calls


def transport_default_problems(tree):
    """The `transport` parameter of _ChSession.__init__ and _ch_session_new is the LAST one and defaults to the string "verified"."""
    problems, seen = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_ChSession":
            funcs = [("_ChSession.__init__", f) for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"]
        elif isinstance(node, ast.FunctionDef) and node.name == "_ch_session_new":
            funcs = [("_ch_session_new", node)]
        else:
            continue
        for label, f in funcs:
            seen.append(label)
            args = f.args.args
            default = f.args.defaults[-1] if f.args.defaults else None
            if not args or args[-1].arg != "transport":
                problems.append("%s: the last parameter is %r, not transport" % (label, args[-1].arg if args else None))
            elif not (isinstance(default, ast.Constant) and default.value == "verified"):
                problems.append("%s: transport defaults to %s" % (label, ast.dump(default) if default is not None else "nothing"))
    return problems + problem_if(sorted(seen) != ["_ChSession.__init__", "_ch_session_new"], "found %r" % seen)


def post_capture_names():
    """(names, accept): the cors-post capture's POST stream fields, pseudo-headers and priority dropped, in wire order."""
    for _name, h2 in h2_fixtures("cors-post"):
        for stream in fixture_streams(h2):
            pairs = fixture_pairs(stream)
            if dict(pairs).get(":method") == "POST":
                return [n for n, _v in pairs if not n.startswith(":") and n != "priority"], dict(pairs).get("accept")
    return None, None


def transport_label_rows(suite, mod, tree, vp, stall, s2, s1p, t12):
    """The default, the verified GET, its two refusals, the chrome / tls12-fallback / cleartext labels, an unknown transport."""
    CE = mod.ChromeClientError
    src = "D15 / D16: _ch_session_new, _ChSession and _ChResponse docstrings; tests/files/tls (the test CA is the only anchor)"
    trusted = {"transport": "verified", "ssl_context_factory": trusted_context}
    vurl = "https://localhost:%d" % vp.port

    def default_transport():
        s = mod._ch_session_new()
        try:
            got = s.transport
        finally:
            s.close()
        return problem_if(got != "verified", "_ch_session_new().transport is %r" % got), ["_ch_session_new() with no argument: transport %r" % got]
    run_row(suite, GP, "default-session-transport-is-verified", default_transport, src)

    def ast_default():
        return transport_default_problems(tree), ["_ChSession.__init__ and _ch_session_new: transport is the LAST parameter, default the string 'verified'"]
    run_row(suite, GP, "ast-transport-default-is-the-string-verified-in-both-signatures", ast_default, src)

    def verified_get(s, calls):
        mark, a0 = vp.mark(), len(vp.alpns)
        r = s.get(vurl + "/echo")
        seen, conns = vp.since(mark)
        alpns = vp.alpns[a0:]
        problems = problem_if(r.content != b"echo" or r.transport != "verified", "body %r transport %r" % (r.content[:20], r.transport))
        problems += problem_if(r.impersonated is not False or r.cert_verified is not True, "impersonated=%r cert_verified=%r" % (r.impersonated, r.cert_verified))
        problems += problem_if(r.http_version != "HTTP/1.1" or not r.tls or not r.tls.get("version"), "http_version %r tls %r" % (r.http_version, r.tls))
        problems += problem_if(alpns != [None], "the peer's selected ALPN per handshake %r, wanted [None] (the Chrome engine offers h2)" % alpns)
        problems += problem_if(conns != 1 or len(seen) != 1 or calls != [("localhost", vp.port)], "%d connection(s), %d request(s), policy %r" % (conns, len(seen), calls))
        return problems, ["transport=verified, impersonated=False, cert_verified=True, %s, tls version %s, peer ALPN %r: the Chrome engine was not used" % (r.http_version, r.tls.get("version") if r.tls else None, alpns)]
    run_row(suite, GP, "verified-get-not-impersonated-cert-verified-http-1-1-no-alpn", lambda: with_session(mod, verified_get, **trusted), src)

    def default_ctx(s, _calls):
        mark = vp.mark()
        got = outcome(lambda: s.get(vurl + "/echo"))
        seen, _conns = vp.since(mark)
        return refused_with_prefix(got, CE, "verified: certificate verify failed: ") + problem_if(seen, "the peer served %d request(s)" % len(seen)), ["the DEFAULT context (system anchors only) against the test leaf: %r" % (str(got[1])[:100] if got[0] == "exc" else None)]
    run_row(suite, GP, "verified-default-context-refuses-the-test-leaf-with-the-verified-prefix", lambda: with_session(mod, default_ctx, transport="verified"), src + "; D16 M1")

    def stalled(s, _calls):
        got = outcome(lambda: s.get("https://localhost:%d/echo" % stall.port, timeout=LORIS_TIMEOUT))
        return refused_with_prefix(got, CE, "timeout: verified timed out"), ["a peer that completes the handshake and never answers: %r" % (str(got[1])[:60] if got[0] == "exc" else None)]
    run_row(suite, GP, "verified-stalled-peer-is-timeout-verified-timed-out", lambda: with_session(mod, stalled, **trusted), src + "; D16 M1 (the timeout: phase stays first)")

    def chrome_h2(s, _calls):
        r = s.get("https://a.test:%d/echo" % s2.port)
        return (problem_if(r.content != b"echo" or r.transport != "chrome" or r.http_version != "HTTP/2", "body %r transport %r %s" % (r.content[:20], r.transport, r.http_version))
                + problem_if(r.cert_verified is not False or r.impersonated is not True, "impersonated=%r cert_verified=%r" % (r.impersonated, r.cert_verified))), ["transport=\"chrome\" to an h2 peer: transport=chrome, cert_verified=False, impersonated=True"]
    run_row(suite, GP, "chrome-transport-h2-is-chrome-cert-not-verified", lambda: with_session(mod, chrome_h2, transport="chrome"), src)

    def chrome_t12(s, _calls):
        r = s.get("https://localhost:%d/" % t12.port)
        return (problem_if(r.content != b"hello fallback" or r.transport != "tls12-fallback", "body %r transport %r" % (r.content[:20], r.transport))
                + problem_if(r.cert_verified is not True or r.impersonated is not False, "impersonated=%r cert_verified=%r" % (r.impersonated, r.cert_verified))), ["transport=\"chrome\", tls12_fallback=True, a TLS 1.2-only peer: transport=tls12-fallback, cert_verified=True"]
    run_row(suite, GP, "chrome-transport-tls12-peer-with-the-flag-is-tls12-fallback", lambda: with_session(mod, chrome_t12, transport="chrome", tls12_fallback=True, ssl_context_factory=trusted_context), src)

    def inert():
        problems, seen_versions = [], []
        for flag in (False, True):
            def one_session(s, _calls, flag=flag):
                mark = t12.mark()
                r = s.get("https://localhost:%d/" % t12.port)
                seen, accepts = t12.since(mark)
                version = r.tls.get("version") if r.tls else None
                seen_versions.append(version)
                return (problem_if(r.content != b"hello fallback" or r.transport != "verified" or version != "TLSv1.2", "tls12_fallback=%s: body %r transport %r version %r" % (flag, r.content[:20], r.transport, version))
                        + problem_if(len(accepts) != 1 or len(seen) != 1, "tls12_fallback=%s: %d connection(s), %d request(s): the Chrome engine was tried" % (flag, len(accepts), len(seen))))
            problems += with_session(mod, one_session, tls12_fallback=flag, **trusted)
        return problems, ["transport=\"verified\" to the TLS 1.2-only peer with tls12_fallback False and True: transport=verified, tls %r, one connection each (inert)" % seen_versions]
    run_row(suite, GP, "verified-transport-tls12-peer-is-verified-whatever-tls12-fallback-says", inert, src)

    for mode in ("verified", "chrome"):
        def cleartext(s, _calls, mode=mode):
            r = s.get("http://a.test:%d/echo" % s1p.port)
            return (problem_if(r.content != b"echo" or r.transport != "cleartext" or r.tls is not None, "body %r transport %r tls %r" % (r.content[:20], r.transport, r.tls))
                    + problem_if(r.cert_verified is not False or r.impersonated is not True, "impersonated=%r cert_verified=%r" % (r.impersonated, r.cert_verified))), ["an http:// URL under transport=%r: transport=cleartext, cert_verified=False, impersonated=True (the Chrome h1 profile)" % mode]
        run_row(suite, GP, "http-url-is-cleartext-under-transport-%s" % mode, lambda cleartext=cleartext, mode=mode: with_session(mod, cleartext, transport=mode), src)

    def unknown():
        calls = []
        got = outcome(lambda: mod._ch_session_new(connect_policy=counting_policy(calls), transport="firefox"))
        other = outcome(lambda: mod._ch_session_new(connect_policy=counting_policy(calls), transport=None))
        return (expect_refusal(got, CE, "transport: must be 'verified' or 'chrome'")
                + expect_refusal(other, CE, "transport: must be 'verified' or 'chrome'")
                + problem_if(calls, "policy calls %r" % calls)), ["transport='firefox' and transport=None: the one-line refusal in the constructor, no policy call"]
    run_row(suite, GP, "unknown-transport-refused-before-any-connection", unknown, src)


def transport_parity_rows(suite, mod, vp, vp2, s1p):
    """The verified path shares everything above _hop: redirects, header binding, on_headers, limits, gzip, downgrade, POST, late binding."""
    CE = mod.ChromeClientError
    src = "D15: the verified hop sits under the same request() loop (_ChSession class docstring)"
    trusted = {"transport": "verified", "ssl_context_factory": trusted_context}
    vurl = "https://localhost:%d" % vp.port
    v2url = "https://localhost:%d" % vp2.port
    vp.plan["/n"] = (200, [("content-type", "text/plain")], b"n" * 100)
    vp.plan["/n1"] = (200, [("content-type", "text/plain")], b"n" * 101)
    vp.plan["/gz"] = (200, [("content-type", "text/plain"), ("content-encoding", "gzip")], gz(b"verified gzip " * 64))

    def same_origin(s, calls):
        mark = vp.mark()
        r = s.get(vurl + "/r/302?/echo")
        seen, conns = vp.since(mark)
        return (problem_if(r.content != b"echo" or r.transport != "verified" or r.cert_verified is not True, "body %r transport %r" % (r.content[:20], r.transport))
                + problem_if(len(seen) != 2 or conns != 2 or len(set(q["conn"] for q in seen)) != 2, "%d request(s) over %d connection(s): every verified hop is a NEW connection" % (len(seen), conns))
                + problem_if(calls != [("localhost", vp.port)] * 2, "policy calls %r" % calls)), ["same-origin 302: two hops, two connections, each hop vetted by the policy"]
    run_row(suite, GP, "verified-same-origin-redirect-new-connection-per-hop-re-vetted", lambda: with_session(mod, same_origin, **trusted), src)

    def cross_origin(s, calls):
        m1, m2 = vp.mark(), vp2.mark()
        r = s.get(vurl + "/r/302?" + v2url + "/echo", headers={"Authorization": "Bearer t"})
        got1, _c1 = vp.since(m1)
        got2, _c2 = vp2.since(m2)
        auth1 = values(got1[0], "authorization") if got1 else None
        auth2 = values(got2[0], "authorization") if got2 else None
        return (problem_if(r.content != b"echo" or r.transport != "verified", "body %r transport %r" % (r.content[:20], r.transport))
                + problem_if(auth1 != ["Bearer t"] or auth2 != [], "Authorization hop 1 %r, hop 2 %r" % (auth1, auth2))
                + problem_if(calls != [("localhost", vp.port), ("localhost", vp2.port)], "policy calls %r" % calls)), ["localhost:%d -> localhost:%d (another origin): the caller's Authorization on hop 1 only, each hop vetted" % (vp.port, vp2.port)]
    run_row(suite, GP, "verified-cross-origin-redirect-drops-authorization-and-re-vets", lambda: with_session(mod, cross_origin, **trusted), src + "; FR-16")

    def on_headers(s, _calls):
        stop = CallbackRefusal("refused by the callback")
        closes, reads = [], []
        real_close = http.client.HTTPConnection.close
        real_read = mod._ChFallbackConnection.read_body

        def rec_close(self):
            closes.append(1)
            return real_close(self)

        def rec_read(self, limit):
            reads.append(limit)
            return real_read(self, limit)

        def refuse(_status, _headers, _url):
            raise stop
        with Swapped((mod._ChFallbackConnection, "close", rec_close), (mod._ChFallbackConnection, "read_body", rec_read)):
            got = outcome(lambda: s.get(vurl + "/n", on_headers=refuse))
        return (problem_if(got[0] != "exc" or got[1] is not stop, "%s, wanted the callback's own exception" % shown(got))
                + problem_if(reads or not closes, "read_body called %d time(s), close %d time(s)" % (len(reads), len(closes)))), ["on_headers raising on the verified path: its exception unchanged, the connection closed, no body read"]
    run_row(suite, GP, "verified-on-headers-refusal-closes-before-the-body", lambda: with_session(mod, on_headers, **trusted), src)

    def max_bytes(s, _calls):
        ok = outcome(lambda: s.get(vurl + "/n"))
        over = outcome(lambda: s.get(vurl + "/n1"))
        return (problem_if(content_of(ok) != b"n" * 100, "100 bytes under max_bytes=100: %s" % shown(ok))
                + too_large(over, mod, 100)), ["max_bytes=100: a 100-byte body served, a 101-byte body ChromeBodyTooLarge(.limit=100)"]
    run_row(suite, GP, "verified-max-bytes-n-served-n-plus-1-refused", lambda: with_session(mod, max_bytes, max_bytes=100, **trusted), src)

    def gzip_row(s, _calls):
        r = s.get(vurl + "/gz")
        return problem_if(r.content != b"verified gzip " * 64 or r.transport != "verified", "body %r transport %r" % (r.content[:20], r.transport)), ["a gzip body (the stdlib gzip module is the oracle) decoded on the verified path"]
    run_row(suite, GP, "verified-gzip-body-decodes", lambda: with_session(mod, gzip_row, **trusted), src)

    def downgrade(s, _calls):
        mark = s1p.mark()
        got = outcome(lambda: s.get(vurl + "/r/302?http://a.test:%d/echo" % s1p.port))
        seen, conns = s1p.since(mark)
        return expect_refusal(got, CE, "redirect: https to http downgrade refused") + problem_if(seen or conns, "the cleartext peer saw %d connection(s)" % conns), ["a verified 302 to http:// without allow_downgrade: refused, the cleartext peer untouched"]
    run_row(suite, GP, "verified-https-to-http-redirect-refused-without-allow-downgrade", lambda: with_session(mod, downgrade, **trusted), src + "; R2-N3")

    def post(s, _calls):
        want, accept = post_capture_names()
        if want is None:
            return ["no POST stream in tests/files/chrome/154/cors-post/"], []
        mark = vp.mark()
        r = s.post(vurl + "/echo", data={"q": "test"}, headers={"Accept": accept}, referer=vurl + "/lite/")
        seen, _conns = vp.since(mark)
        if len(seen) != 1:
            return ["%d request(s) reached the peer" % len(seen)], []
        names = [k for k, _v in seen[0]["headers"]]
        low = [k.lower() for k in names]
        problems = problem_if(r.content != b"echo" or seen[0]["method"] != "POST" or seen[0]["body"] != b"q=test", "%r %r body %r" % (r.content[:20], seen[0]["method"], seen[0]["body"]))
        problems += problem_if(low[:2] != ["host", "connection"] or "priority" in low, "h1 head starts %r (Host and Connection first, no Priority)" % names[:3])
        problems += problem_if([n for n in low[2:] if n != "content-length"] != [n for n in want if n != "content-length"], "fields %r, the capture has %r" % (low[2:], want))
        problems += problem_if(values(seen[0], "content-length") != ["6"], "Content-Length %r" % values(seen[0], "content-length"))
        return problems, ["the POST's h1 head vs cors-post's POST stream (pseudo-headers and priority dropped): %s" % ", ".join(names)]
    run_row(suite, GP, "verified-cors-post-body-and-the-captured-header-list-in-h1-spelling", lambda: with_session(mod, post, **trusted), src + "; tests/files/chrome/154/cors-post/")

    def late(s, _calls):
        seen = []
        real = mod._ch_open_socket

        def recorder(addresses, port, deadline):
            seen.append(port)
            return real(addresses, port, deadline)
        with Swapped((mod, "_ch_open_socket", recorder)):
            got = outcome(lambda: s.get(vurl + "/echo"))
        return (problem_if(content_of(got) != b"echo" or seen != [vp.port], "%s, recorder saw %r" % (shown(got), seen))
                + problem_if(mod._ch_open_socket is not real, "_ch_open_socket not restored")), ["_ch_open_socket replaced AFTER _ch_session_new: the verified request went through the recorder"]
    run_row(suite, GP, "verified-late-binding-a-replaced-ch-open-socket-is-used", lambda: with_session(mod, late, **trusted), "_ch_open_socket docstring: late binding")


def transport_chain_rows(suite, mod, vp, vp2, s2, s1p, t12):
    """The chain label, weakest hop wins (D16 S3), and the decoded cap on the verified path (D16 S5)."""
    src = "D16 S3: _ChResponse docstring (transport and cert_verified are the chain's weakest hop, impersonated the final hop's)"
    trusted = {"transport": "verified", "ssl_context_factory": trusted_context}
    vurl = "https://localhost:%d" % vp.port

    def expect_label(r, transport, cert, impersonated):
        return problem_if((r.transport, r.cert_verified, r.impersonated) != (transport, cert, impersonated), "transport=%r cert_verified=%r impersonated=%r, wanted %r %r %r" % (r.transport, r.cert_verified, r.impersonated, transport, cert, impersonated))

    def chrome_to_t12(s, _calls):
        m12 = t12.mark()
        r = s.get("https://a.test:%d/r/302?https://localhost:%d/" % (s2.port, t12.port))
        seen, _accepts = t12.since(m12)
        return (problem_if(r.content != b"hello fallback" or len(seen) != 1, "body %r, the TLS 1.2 peer served %d" % (r.content[:20], len(seen)))
                + expect_label(r, "chrome", False, False)), ["chrome h2 302 -> the TLS 1.2 peer served by the fallback: transport=chrome, cert_verified=False, impersonated=False"]
    run_row(suite, GP, "chain-chrome-then-tls12-fallback-is-chrome-cert-not-verified", lambda: with_session(mod, chrome_to_t12, transport="chrome", tls12_fallback=True, ssl_context_factory=trusted_context), src)

    def verified_to_clear(s, _calls):
        r = s.get(vurl + "/r/302?http://a.test:%d/echo" % s1p.port)
        return problem_if(r.content != b"echo", "body %r" % r.content[:20]) + expect_label(r, "cleartext", False, True), ["verified 302 -> cleartext (allow_downgrade=True): transport=cleartext, cert_verified=False"]
    run_row(suite, GP, "chain-verified-then-cleartext-is-cleartext", lambda: with_session(mod, verified_to_clear, allow_downgrade=True, **trusted), src)

    def clear_to_verified(s, _calls):
        r = s.get("http://a.test:%d/r/302?%s/echo" % (s1p.port, vurl))
        return problem_if(r.content != b"echo", "body %r" % r.content[:20]) + expect_label(r, "cleartext", False, False), ["http:// 302 -> the verified peer: transport=cleartext, cert_verified=False, impersonated=False (the final hop's)"]
    run_row(suite, GP, "chain-cleartext-then-verified-is-cleartext", lambda: with_session(mod, clear_to_verified, **trusted), src)

    def verified_to_verified(s, _calls):
        r = s.get(vurl + "/r/302?https://localhost:%d/echo" % vp2.port)
        return problem_if(r.content != b"echo", "body %r" % r.content[:20]) + expect_label(r, "verified", True, False), ["verified 302 -> verified: transport=verified, cert_verified=True"]
    run_row(suite, GP, "chain-verified-then-verified-is-verified", lambda: with_session(mod, verified_to_verified, **trusted), src)

    cap = 4096
    vp.plan["/cap"] = (200, [("content-type", "text/plain"), ("content-encoding", "gzip")], gz(b"c" * cap))
    vp.plan["/cap1"] = (200, [("content-type", "text/plain"), ("content-encoding", "gzip")], gz(b"c" * (cap + 1)))

    def decoded_cap(s, _calls):
        before = mod.CH_DEFAULT_DECODE_CAP
        with Swapped((mod, "CH_DEFAULT_DECODE_CAP", cap)):
            ok = outcome(lambda: s.get(vurl + "/cap"))
            over = outcome(lambda: s.get(vurl + "/cap1"))
        problems = problem_if(content_of(ok) != b"c" * cap, "exactly the cap: %s" % shown(ok)) + too_large(over, mod, cap)
        problems += problem_if(mod.CH_DEFAULT_DECODE_CAP != before, "CH_DEFAULT_DECODE_CAP not restored")
        return problems, ["max_bytes=0, CH_DEFAULT_DECODE_CAP lowered from %d to %d and restored: %d decoded bytes served, %d refused with .limit=%d" % (before, cap, cap, cap + 1, cap),
                          "declared (R29): the verified path's response head is parsed by http.client (_MAXLINE %s, _MAXHEADERS %s), not by the CH_MAX_H1_* ceilings" % (getattr(http.client, "_MAXLINE", None), getattr(http.client, "_MAXHEADERS", None))]
    run_row(suite, GP, "verified-decoded-cap-parity-cap-served-cap-plus-1-refused", lambda: with_session(mod, decoded_cap, **trusted), "D16 S5: _ch_decode_body under CH_DEFAULT_DECODE_CAP on both transports")


def transport_rule_rows(suite):
    """D16 C1: every _ch_session_new call in this file says transport= or is declared; the checker catches a planted one."""
    def rule():
        with open(TEST_SOURCE, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        problems, calls = call_site_problems(tree)
        owners = sorted(set(o for _c, o in calls if o))
        helpers = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in TRANSPORT_HELPERS]
        problems += problem_if(sorted(helpers) != sorted(TRANSPORT_HELPERS), "helpers found %r" % helpers)
        return problems, ["%d _ch_session_new call(s) in %s; every one passes transport=, sits in %s (setdefault asserted) or in the declared-default %s" % (len(calls), ", ".join(owners), "/".join(TRANSPORT_HELPERS), "/".join(DECLARED_DEFAULT))]
    run_row(suite, GP, "ast-every-ch-session-new-call-says-transport-or-is-declared", rule, "D16 C1")

    def control():
        problems, _calls = call_site_problems(ast.parse(PLANTED_SESSION_NEW))
        caught_row = [p for p in problems if "planted_row" in p]
        caught_helper = [p for p in problems if "helper with_session" in p]
        return (problem_if(len(caught_row) != 1 or len(caught_helper) != 1 or len(problems) != 2, "the checker reported %r" % problems)), ["planted: a row calling without transport=, a with_session without the setdefault (both caught), a row with transport= (accepted)"]
    run_row(suite, GP, "control-call-site-checker-catches-a-planted-call-without-transport", control, "negative control")


class WrapRecorder(ssl.SSLContext):
    """A verifying client context whose wrap_socket records its keyword arguments and refuses (F8, white-box)."""

    def wrap_socket(self, sock, **kw):
        self.wrap_kwargs = dict(kw)
        raise CallbackRefusal("wrap_socket recorded")


class FallbackSpy:
    """Stands in for _ChFallbackConnection inside _ChSession._fallback: records the context's minimum_version, then refuses."""

    seen = []

    def __init__(self, host, port, addresses, context, deadline, label="tls12-fallback"):
        FallbackSpy.seen.append(context.minimum_version)
        raise CallbackRefusal("fallback recorded")


def floor_context(version):
    """trusted_context() with its minimum_version set to `version`."""
    ctx = trusted_context()
    ctx.minimum_version = version
    return ctx


def verified_hardening_rows(suite, mod):
    """F8 and F9: the verified transport's TLS floor, suppress_ragged_eofs=False, and the Content-Length check."""
    CE = mod.ChromeClientError
    src = "_ChFallbackConnection docstring (F8, F9); _ChSession._fallback"
    below = "verified: the ssl context allows a protocol version below TLS 1.2"
    for cid, version in (("tlsv1", ssl.TLSVersion.TLSv1), ("tlsv1-1", ssl.TLSVersion.TLSv1_1), ("minimum-supported", ssl.TLSVersion.MINIMUM_SUPPORTED)):
        def floor_row(version=version):
            got = outcome(lambda: mod._ChFallbackConnection("localhost", 443, ["127.0.0.1"], floor_context(version), time.monotonic() + 5, "verified"))
            return expect_refusal(got, CE, below), ["minimum_version %s refused by the constructor, before any socket" % version.name]
        run_row(suite, GP, "verified-context-floor-%s-refused" % cid, floor_row, src)

    def floor_ok():
        got = outcome(lambda: mod._ChFallbackConnection("localhost", 443, ["127.0.0.1"], floor_context(ssl.TLSVersion.TLSv1_2), time.monotonic() + 5, "verified"))
        return problem_if(got[0] == "exc", "refused: %s" % (got[1] if got[0] == "exc" else "")), ["minimum_version TLSv1_2: constructed (nothing connects until connect())"]
    run_row(suite, GP, "verified-context-floor-tlsv1-2-accepted", floor_ok, src)

    def raised():
        FallbackSpy.seen[:] = []

        def factory():
            return floor_context(ssl.TLSVersion.TLSv1)
        s = mod._ch_session_new(connect_policy=lambda host, port: ["127.0.0.1"], timeout=SESSION_TIMEOUT, ssl_context_factory=factory, transport="verified")
        with Swapped((mod, "_ChFallbackConnection", FallbackSpy)):
            got = outcome(lambda: s.get("https://localhost/"))
        s.close()
        problems = problem_if(got[0] != "exc" or not isinstance(got[1], CallbackRefusal), "%s, wanted the spy's refusal" % shown(got))
        return problems + problem_if(FallbackSpy.seen != [ssl.TLSVersion.TLSv1_2], "the fallback saw minimum_version %r" % FallbackSpy.seen), ["a factory context at TLSv1 reaches _ChFallbackConnection at TLSv1_2"]
    run_row(suite, GP, "verified-fallback-raises-the-factory-context-floor-to-tls1-2", raised, src)

    def ragged():
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        ctx = WrapRecorder(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            fc = mod._ChFallbackConnection("localhost", lsock.getsockname()[1], ["127.0.0.1"], ctx, time.monotonic() + SESSION_TIMEOUT, "verified")
            got = outcome(fc.connect)
        finally:
            lsock.close()
        problems = problem_if(got[0] != "exc" or not isinstance(got[1], CallbackRefusal), "%s, wanted the recorder's refusal" % shown(got))
        kw = getattr(ctx, "wrap_kwargs", {})
        return problems + problem_if(kw.get("suppress_ragged_eofs", True) is not False or kw.get("server_hostname") != "localhost", "wrap_socket keywords %r" % kw), ["wrap_socket(..., server_hostname='localhost', suppress_ragged_eofs=False)"]
    run_row(suite, GP, "verified-wrap-socket-suppress-ragged-eofs-false", ragged, src)

    def short_body():
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]
        state = {"heads": [], "bodies": []}
        thread = threading.Thread(target=h1_serve, args=(lsock, [b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc"], state, h1_server_context()), daemon=True)
        thread.start()
        try:
            fc = mod._ChFallbackConnection("localhost", port, ["127.0.0.1"], floor_context(ssl.TLSVersion.TLSv1_2), time.monotonic() + SESSION_TIMEOUT, "verified")
            head = outcome(lambda: fc.start(mod._ch_profile_headers("navigate", "GET", "localhost:%d" % port, "/")))
            got = outcome(lambda: fc.read_body(1000)) if head[0] == "value" else head
        finally:
            thread.join(20)
            lsock.close()
        return expect_refusal(got, CE, "verified: the body ended after 3 of its 10 Content-Length bytes"), ["Content-Length 10, 3 bytes, then close_notify and FIN: http.client's read(amt) returns b'' there; the check refuses"]
    run_row(suite, GP, "verified-body-shorter-than-content-length-refused", short_body, src)


def group_transport(suite, mod, cc, tree):
    """Group P: the verified default transport, transport="chrome", the chain label and the call-site rule (D15, D16)."""
    vp = VerifiedPeer(cc)
    vp2 = VerifiedPeer(cc)
    stall = VerifiedPeer(cc, stall=True)
    s2 = SessionPeer("h2", cc)
    s1p = SessionPeer("h1-plain", cc)
    t12 = Tls12Peer()
    try:
        transport_label_rows(suite, mod, tree, vp, stall, s2, s1p, t12)
        transport_parity_rows(suite, mod, vp, vp2, s1p)
        transport_chain_rows(suite, mod, vp, vp2, s2, s1p, t12)
        transport_rule_rows(suite)
        verified_hardening_rows(suite, mod)
    finally:
        for srv in (vp, vp2, stall, s2, s1p, t12):
            srv.close()


# --- M. hosts: search_github's generated copy ---------------------------------------

GH_HOST = H.repo_path("Scripts", "search_github.py")
GH_WARMUP_URL = "https://grep.app/"
GH_API_PATH = "/api/search"
GH_API_URL = "https://grep.app/api/search?q=q"
GH_ACCEPT = "application/json, text/plain, */*"
# The transport label the CLIs printed before the search sources were lifted; no section may carry it now.
LABEL_MARK = "**Transport**"
GH_BLOCKED = "  [Blocked by grep.app on: %s]"

# A grep.app answer written here from the API's shape (hits.hits[].repo/path/
# branch/content.snippet), and what parse_grep_results must make of it: the
# snippet's one <tr data-line> row with its <mark> tags stripped, the language
# by extension, the GitHub URL anchored at the first line.
GH_CANNED = {"hits": {"hits": [{"repo": "octo/demo", "path": "src/app.py", "branch": "main",
                                "content": {"snippet": "<table><tr data-line=\"7\"><td><pre>use<mark>Effect</mark>(fn)</pre></td></tr></table>"}}]}}
GH_CANNED_RESULTS = [{"repo": "octo/demo", "file_path": "src/app.py", "branch": "main", "language": "Python",
                      "code_lines": [(7, "useEffect(fn)")], "url": "https://github.com/octo/demo/blob/main/src/app.py#L7"}]
GH_EMPTY = b'{"hits": {"hits": []}}'

# The F32 oversized rows run create_session()'s REAL public-only policy:
# socket.getaddrinfo answers this public address for the endpoint, and only the
# _ch_open_socket wrapper maps it to 127.0.0.1 -- nothing ever connects to it.
GH_SENTINEL_ADDR = "93.184.216.34"

# NFR-5: the generated regions may add at most this much to a cold start.
NFR5_BUDGET_MS = 150.0
STARTUP_RUNS = 3

# The names group M swaps on the host for a row; the hygiene row asserts each is the original object afterwards.
GH_SWAPPED = ("_ch_open_socket", "_ch_session_new", "create_session", "random", "ROTATE_EVERY")

GH_MARKER_RX = re.compile(r"^# BEGIN GENERATED: (\S+) :: (.*)$")
GH_END_RX = re.compile(r"^# END GENERATED: ")

PLANTED_R14 = "# BEGIN GENERATED: _mcp_chrome.py :: _ch_open_socket\ndef _ch_open_socket():\n    pass\n# END GENERATED: 000000000000\n\n\ndef _ch_open_socket():\n    pass\n"
PLANTED_RAND = "# BEGIN GENERATED: _mcp_chrome.py :: _CH_X\n_CH_X = g(rand=1)\n# END GENERATED: 000000000000\n\n\ndef f():\n    return g(rand=2)\n"


class NoWait:
    """The host's `random` for one row: every jitter is 0 s (the generated client never uses random; only the host glue sleeps)."""

    @staticmethod
    def uniform(_a, _b):
        return 0.0


class PortMap:
    """The host's _ch_open_socket for one row: records (addresses, port), forwards with 443 mapped to the peer's port.

    The address list passes through unchanged unless `addr_map` names an
    address (the F32 oversized rows, whose policy is the real public-only
    one); the connect policy still sees the URL host and 443.
    """

    def __init__(self, host, port, addr_map=None):
        self.original = host._ch_open_socket
        self.port = port
        self.addr_map = dict(addr_map or {})
        self.calls = []

    def __call__(self, addresses, port, deadline):
        addrs = list(addresses)
        self.calls.append((addrs, port))
        return self.original([self.addr_map.get(a, a) for a in addrs], self.port if port == 443 else port, deadline)


def gh_resp(host, transport, spec):
    """A scripted answer as the host's generated _ChResponse: spec is (status, content-type, body[, url[, decode_error]])."""
    status, ctype, body = spec[:3]
    url = spec[3] if len(spec) > 3 and spec[3] else GH_API_URL
    decode_error = spec[4] if len(spec) > 4 else None
    return host._ChResponse(status, [("content-type", ctype)], url, body, decode_error=decode_error,
                            impersonated=transport == "chrome", cert_verified=transport != "chrome", transport=transport)


class StubSession:
    """A session the ladder rows hand the host: the warm-up answers 200 html, every API call answers `spec` (or raises it).

    `spec` may be a callable of the query (a per-query script).
    """

    def __init__(self, host, spec, warmup_exc=None):
        self.host = host
        self.transport = "chrome"
        self.spec = spec
        self.warmup_exc = warmup_exc
        self.last_navigation_url = None
        self.calls = []
        self.closed = 0

    def get(self, url, params=None, headers=None, timeout=None, mode="navigate", referer=None, on_headers=None):
        self.calls.append((url, mode, referer))
        if url == GH_WARMUP_URL:
            if self.warmup_exc is not None:
                raise self.warmup_exc
            self.last_navigation_url = url
            return gh_resp(self.host, self.transport, (200, "text/html; charset=utf-8", b"<html>grep</html>", url))
        spec = self.spec
        if callable(spec):
            spec = spec(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [None])[0])
        if isinstance(spec, Exception):
            raise spec
        return gh_resp(self.host, self.transport, spec)

    def api_calls(self):
        return [c for c in self.calls if c[0] != GH_WARMUP_URL]

    def close(self):
        self.closed += 1


class SessionRecorder:
    """The host's create_session for one row: called with no argument (Chrome-only), returns a fresh StubSession each time."""

    def __init__(self, host, spec, warmup_exc=None):
        self.host = host
        self.spec = spec
        self.warmup_exc = warmup_exc
        self.sessions = []
        self.args = []

    def __call__(self, *args, **kw):
        self.args.append((args, kw))
        s = StubSession(self.host, self.spec, self.warmup_exc)
        self.sessions.append(s)
        return s


def gh_canned(spec):
    """A (status, type, None) spec gets GH_CANNED as its body."""
    if isinstance(spec, tuple) and len(spec) == 3 and spec[2] is None:
        return (spec[0], spec[1], json.dumps(GH_CANNED).encode())
    return spec


def gh_drive(host, spec, queries=("q",), rotate=None, warmup_exc=None):
    """_run_github(queries, blocked=[]) over a SessionRecorder; (sessions, sections, has_results, stderr lines, blocked, create_session args)."""
    rec = SessionRecorder(host, gh_canned(spec), warmup_exc)
    err = io.StringIO()
    swaps = [(host, "create_session", rec), (host, "random", NoWait), (sys, "stderr", err)]
    if rotate is not None:
        swaps.append((host, "ROTATE_EVERY", rotate))
    blocked = []
    with Swapped(*swaps):
        sections, has = host._run_github(list(queries), blocked=blocked)
    return rec.sessions, sections, has, err.getvalue().splitlines(), blocked, rec.args


def gh_one_session_problems(run):
    """One session, created with no argument, warmed up once and asked once (cors, referer = its warm-up URL), closed; no section carries a label."""
    sessions, sections, _has, _lines, _blocked, args = run
    api = [len(s.api_calls()) for s in sessions]
    warm = [len(s.calls) - len(s.api_calls()) for s in sessions]
    problems = problem_if(api != [1] or warm != [1], "API calls per session %r, warm-ups %r, wanted one session asked once" % (api, warm))
    problems += problem_if(args != [((), {})], "create_session called with %r, wanted once with no argument" % args)
    problems += problem_if(any(s.closed < 1 for s in sessions), "closed counts %r" % [s.closed for s in sessions])
    problems += problem_if(any(LABEL_MARK in sec for sec in sections), "a section carries a transport label")
    if len(sessions) == 1 and sessions[0].api_calls():
        _url, mode, referer = sessions[0].api_calls()[0]
        problems += problem_if(mode != "cors" or referer != GH_WARMUP_URL, "the search went out mode=%r referer=%r" % (mode, referer))
    return problems


def gh_block_problems(run):
    """A block: one session asked once (no second attempt), exactly the one blocked line on stderr, the query in `blocked`, no results."""
    _sessions, sections, has, lines, blocked, _args = run
    problems = gh_one_session_problems(run)
    problems += problem_if(lines != [GH_BLOCKED % "q"], "stderr %r, wanted exactly %r" % (lines, GH_BLOCKED % "q"))
    problems += problem_if(blocked != ["q"], "blocked %r" % blocked)
    problems += problem_if(has or sections, "results %r" % sections[:1])
    return problems


def gh_not_block_problems(run, needle):
    """Not a block: one session asked once, today's line `needle`, no blocked line, nothing in `blocked`, no results."""
    _sessions, sections, has, lines, blocked, _args = run
    problems = gh_one_session_problems(run)
    problems += problem_if(not any(ln.startswith(needle) for ln in lines), "no stderr line starting %r in %r" % (needle, lines))
    problems += problem_if(any("Blocked" in ln for ln in lines) or blocked, "a block was reported: stderr %r blocked %r" % (lines, blocked))
    problems += problem_if(has or sections, "results %r" % sections[:1])
    return problems


def gh_profile_exchange(host, s2):
    """The Chrome-path happy path: warmup_code_session then search_github on a session of the host's own _ch_session_new, over the h2 peer."""
    s2.plan["/"] = (302, [("location", "/home")], b"")
    s2.plan["/home"] = (200, [("content-type", "text/html; charset=utf-8")], b"<html>grep</html>")
    s2.plan[GH_API_PATH] = (200, [("content-type", "application/json")], json.dumps(GH_CANNED).encode())
    calls = []
    pm = PortMap(host, s2.port)
    err = io.StringIO()
    mark = s2.mark()
    with Swapped((host, "_ch_open_socket", pm), (host, "random", NoWait), (sys, "stderr", err)):
        s = host._ch_session_new(decoders=host._DECODERS, connect_policy=counting_policy(calls), timeout=SESSION_TIMEOUT, transport="chrome")
        try:
            host.warmup_code_session(s)
            nav = s.last_navigation_url
            results = host.search_github("x", s)
        finally:
            s.close()
    seen, conns = s2.since(mark)
    return {"calls": calls, "pm": pm.calls, "nav": nav, "results": results, "seen": seen, "conns": conns, "stderr": err.getvalue()}


def cors_get_oracle(referer):
    """The cors-get capture's fetch (its sec-fetch-mode cors stream) without pseudo-headers, grep.app's Accept and `referer` in their captured slots."""
    for _name, h2 in h2_fixtures("cors-get"):
        for st in fixture_streams(h2):
            if is_cors_stream(st):
                out = []
                for n, v in fixture_pairs(st):
                    if n.startswith(":"):
                        continue
                    out.append((n, GH_ACCEPT if n == "accept" else (referer if n == "referer" else v)))
                return out
    return None


def gh_profile_rows(suite, host, s2):
    src = "Step 11 group M: Scripts/search_github.py's GENERATED copy, reached through a port-443 _ch_open_socket wrapper"
    ex = outcome(lambda: gh_profile_exchange(host, s2))

    def need():
        if ex[0] == "exc":
            raise ex[1]
        return ex[1]

    def headers_row():
        x = need()
        api = x["seen"][-1] if x["seen"] else None
        want = cors_get_oracle("https://grep.app/home")
        if api is None or want is None:
            return ["no API request recorded (%d) or no cors-get capture (%r)" % (len(x["seen"]), want is None)], []
        got = [(k, v) for k, v in api["headers"]]
        problems = problem_if(got != want, "field %d: emitted %r, the capture has %r" % (first_difference(got, want), got[first_difference(got, want)] if first_difference(got, want) < len(got) else None, want[first_difference(got, want)] if first_difference(got, want) < len(want) else None))
        problems += problem_if((api["method"], api["authority"], api["path"]) != ("GET", "grep.app", "/api/search?q=x"), "pseudo %r" % ((api["method"], api["authority"], api["path"]),))
        return problems, ["%d fields in the captured cors-get order (tests/files/chrome/154/cors-get, the sec-fetch-mode cors stream), accept %r, decoded by chrome_capture's HpackDecoder" % (len(got), GH_ACCEPT)]
    run_row(suite, GM, "github-chrome-cors-get-headers-equal-the-captured-profile-with-grep-app-accept", headers_row, src)

    def referer_row():
        x = need()
        api = x["seen"][-1] if x["seen"] else {"headers": []}
        ref = one(api, "referer")
        return (problem_if(x["nav"] != "https://grep.app/home" or ref != x["nav"], "warm-up final URL %r, the fetch's referer %r" % (x["nav"], ref))), ["the warm-up navigates / -> 302 /home; the cors fetch's referer is that final URL %r, not the fallback %r (R2-L4)" % (ref, GH_WARMUP_URL)]
    run_row(suite, GM, "github-chrome-referer-is-the-warmup-navigations-final-url", referer_row, src + "; R2-L4")

    def stream_row():
        x = need()
        got = [(q["method"], q["path"], q["sid"], q["conn"]) for q in x["seen"]]
        modes = [one(q, "sec-fetch-mode") for q in x["seen"]]
        return (problem_if([g[:3] for g in got] != [("GET", "/", 1), ("GET", "/home", 3), ("GET", "/api/search?q=x", 5)] or x["conns"] != 1, "requests %r over %d connection(s)" % (got, x["conns"]))
                + problem_if(modes != ["navigate", "navigate", "cors"], "sec-fetch-mode %r" % modes)), ["warm-up (2 hops) and the search on streams 1, 3, 5 of ONE h2 connection; modes %r" % modes]
    run_row(suite, GM, "github-chrome-warmup-and-search-share-one-h2-connection", stream_row, src)

    def json_row():
        x = need()
        return (problem_if(x["results"] != GH_CANNED_RESULTS, "parsed %r" % (x["results"],)) + problem_if(x["stderr"], "stderr %r" % x["stderr"][:200])), ["the canned grep.app JSON parsed to the one hand-written result (line 7, <mark> stripped, Python by extension)"]
    run_row(suite, GM, "github-chrome-canned-json-parsed", json_row, src)

    def port_row():
        x = need()
        return (problem_if(x["calls"] != [("grep.app", 443)] * 3, "policy calls %r" % x["calls"])
                + problem_if(x["pm"] != [(["127.0.0.1"], 443)], "_ch_open_socket calls %r" % x["pm"])), ["the policy saw ('grep.app', 443) on all 3 hops; the wrapper saw one open of ['127.0.0.1'] on 443 and forwarded it to the peer's port"]
    run_row(suite, GM, "github-port-443-wrapper-policy-sees-grep-app-443", port_row, src)


def create_session_identity_problems(host):
    """(problems, detail): create_session() returns ONE session on the Chrome path, public-only policy, no downgrade, no TLS 1.2 fallback."""
    s = host.create_session()
    try:
        problems = problem_if(isinstance(s, tuple), "create_session() returned a tuple %r, wanted the session alone" % (s,))
        s = s[0] if isinstance(s, tuple) else s
        problems += problem_if(s.transport != "chrome", "session transport %r, wanted 'chrome'" % s.transport)
        problems += problem_if(s.connect_policy is not host._ch_public_only_policy, "policy %r" % s.connect_policy)
        problems += problem_if(s.allow_downgrade is not False or s.tls12_fallback is not False, "allow_downgrade=%r tls12_fallback=%r" % (s.allow_downgrade, s.tls12_fallback))
        detail = ["create_session(): one session, transport %r, policy IS _ch_public_only_policy, allow_downgrade=%r, tls12_fallback=%r" % (s.transport, s.allow_downgrade, s.tls12_fallback)]
    finally:
        (s[0] if isinstance(s, tuple) else s).close()
    return problems, detail


def gh_policy_rows(suite, host, s2):
    src = "R2-N2: create_session() in Scripts/search_github.py (Chrome-only)"

    def identity():
        return create_session_identity_problems(host)
    run_row(suite, GM, "github-create-session-policy-is-public-only-and-transport-chrome", identity, src)

    def metadata():
        s2.plan[GH_API_PATH] = (302, [("location", "https://metadata.test/")], b"")
        calls, gai = [], []
        pm = PortMap(host, s2.port)

        def policy(h, port):
            calls.append((h, port))
            if h == "grep.app":
                return ["127.0.0.1"]
            return host._ch_public_only_policy(h, port)
        err = io.StringIO()
        mark = s2.mark()
        with Swapped((host, "_ch_open_socket", pm), (socket, "getaddrinfo", answering_resolver(gai, ["169.254.169.254"])), (sys, "stderr", err)):
            s = host._ch_session_new(decoders=host._DECODERS, connect_policy=policy, timeout=SESSION_TIMEOUT, transport="chrome")
            try:
                got = host.search_github("q", s, note=host._cli_note)
            finally:
                s.close()
        seen, _conns = s2.since(mark)
        lines = err.getvalue().splitlines()
        prefix = "  [grep.app error: policy: metadata.test resolves to 169.254.169.254 ("
        return (problem_if(got != [], "search_github returned %r" % (got,))
                + problem_if(len(lines) != 1 or not lines[0].startswith(prefix), "stderr %r, wanted one line starting %r" % (lines, prefix))
                + problem_if(any("169.254.169.254" in a for a, _p in pm.calls), "a connection was attempted to the metadata address: %r" % pm.calls)
                + problem_if(calls != [("grep.app", 443), ("metadata.test", 443)] or gai != [("metadata.test", 443, socket.SOCK_STREAM)], "policy %r resolver %r" % (calls, gai))
                + problem_if(len(seen) != 1, "the peer served %d request(s)" % len(seen))), ["302 Location: https://metadata.test/ -> %r; opens %r (none to 169.254.169.254)" % (lines[0][:90] if lines else None, pm.calls)]
    run_row(suite, GM, "github-redirect-into-metadata-is-a-policy-refusal-with-no-connect", metadata, src + "; _ch_public_only_policy delegated, getaddrinfo patched")


def gh_ladder_rows(suite, host):
    src = "D16 / D17: _grep_app_blocked, search_github and _run_github in Scripts/search_github.py (Chrome-only: a block is final)"
    CE = host.ChromeClientError
    html = b"<html><body>Access denied</body></html>"
    blocks = (
        ("github-block-403-is-blocked-with-no-second-attempt", (403, "text/html; charset=utf-8", html), "403 from grep.app"),
        ("github-block-200-text-html-is-blocked-with-no-second-attempt", (200, "text/html; charset=utf-8", b"<html>challenge</html>"), "200 with a non-JSON text/html body"),
        ("github-block-429-html-non-json-is-blocked-d17", (429, "text/html; charset=utf-8", b"<html>Too Many Requests</html>"), "429 with a non-JSON text/html body (D17, measured in task-038)"),
    )
    for cid, spec, what in blocks:
        def block(spec=spec, what=what):
            return gh_block_problems(gh_drive(host, spec)), ["%s -> blocked: one session asked once (cors, referer = its warm-up URL), no second attempt, exactly %r on stderr, the query in blocked" % (what, GH_BLOCKED % "q")]
        run_row(suite, GM, cid, block, src)

    not_blocks = (
        ("github-429-json-is-a-rate-limit-not-a-block", (429, "application/json", b'{"error": "rate limited"}'), "  [Rate limited for: q]", "429 with a JSON body"),
        ("github-500-is-not-a-block", (500, "text/html", html), "  [HTTP 500 for: q]", "500 (D16 M3)"),
        ("github-503-is-not-a-block", (503, "text/html", html), "  [HTTP 503 for: q]", "503 (D16 M3)"),
        ("github-403-with-decode-error-is-not-a-block-d17", (403, "text/html", b"", None, "br: corrupt input"), "  [grep.app body undecodable: br: corrupt input]", "403 whose body could not be decoded (D17)"),
        ("github-403-from-another-host-is-not-a-block-s2", (403, "text/html", html, "https://elsewhere.test/api/search?q=q"), "  [HTTP 403 for: q]", "403 whose final URL is https://elsewhere.test/ (D16 S2)"),
        ("github-transport-failure-is-never-a-block-m2", CE("connect: connection refused"), "  [grep.app error: connect: connection refused", "get raises connect: connection refused (D16 M2)"),
    )
    for cid, spec, needle, what in not_blocks:
        def not_block(spec=spec, needle=needle, what=what):
            return gh_not_block_problems(gh_drive(host, spec), needle), ["%s -> not a block: %r, nothing in blocked, no results" % (what, needle.strip())]
        run_row(suite, GM, cid, not_block, src)

    def warmup_fails():
        exc = CE("connect: connection refused")
        run = gh_drive(host, exc, warmup_exc=exc)
        return gh_not_block_problems(run, "  [grep.app error: connect: connection refused") + problem_if(run[0] and len(run[0][0].calls) != 2, "calls %r" % (run[0][0].calls if run[0] else None)), ["the warm-up AND the search raise connect: connection refused -> [] with the error line, not a block (D16 M2)"]
    run_row(suite, GM, "github-warmup-failure-is-never-a-block-m2", warmup_fails, src)

    def parsed_ok():
        run = gh_drive(host, (200, "application/json", json.dumps(GH_CANNED).encode()))
        _sessions, sections, has, lines, blocked, _args = run
        return (gh_one_session_problems(run)
                + problem_if(not has or len(sections) != 1 or "### Result 1: octo/demo - src/app.py" not in sections[0], "has=%r sections %r" % (has, sections[:1]))
                + problem_if(lines or blocked, "stderr %r blocked %r" % (lines, blocked))), ["200 JSON -> parsed, no label, nothing on stderr (the ladder's control)"]
    run_row(suite, GM, "github-200-json-is-parsed-and-unlabelled", parsed_ok, src)

    def no_label_empty():
        sessions, sections, has, lines, blocked, _args = gh_drive(host, (200, "application/json", GH_EMPTY), queries=("q1", "q2"))
        bare = host.format_code_results([])
        headed = host.format_code_results([], query="q1")
        want = ["## Query: q1\n\nNo results found.\n", "## Query: q2\n\nNo results found.\n"]
        return (problem_if([len(s.api_calls()) for s in sessions] != [2], "API calls per session %r" % [len(s.api_calls()) for s in sessions])
                + problem_if(sections != want or has, "sections %r" % sections)
                + problem_if(bare != "No results found." or headed != "No results found.", "format_code_results([]) %r / %r" % (bare, headed))
                + problem_if(lines or blocked, "stderr %r blocked %r" % (lines, blocked))), ["an answer that parses to [] -> the unlabelled 'No results found.' text, in the runner and in format_code_results (D16 L2)"]
    run_row(suite, GM, "github-no-results-carries-no-label-l2", no_label_empty, src)

    def rotation():
        before = host.ROTATE_EVERY
        sessions, sections, has, lines, blocked, args = gh_drive(host, (200, "application/json", None), queries=("q1", "q2", "q3"), rotate=2)
        api = [len(s.api_calls()) for s in sessions]
        warm = [len(s.calls) - len(s.api_calls()) for s in sessions]
        return (problem_if(api != [2, 1] or warm != [1, 1] or args != [((), {})] * 2, "API calls %r warm-ups %r create_session args %r" % (api, warm, args))
                + problem_if(len(sections) != 3 or not has or any(LABEL_MARK in sec for sec in sections), "sections %r" % [sec.splitlines()[:1] for sec in sections])
                + problem_if(lines or blocked or any(s.closed < 1 for s in sessions), "stderr %r blocked %r closed %r" % (lines, blocked, [s.closed for s in sessions]))
                + problem_if(host.ROTATE_EVERY != before, "ROTATE_EVERY %r after the row" % host.ROTATE_EVERY)), ["three queries, ROTATE_EVERY=2 for the run: API calls per session %r, each session warmed up once and closed" % api]
    run_row(suite, GM, "github-rotation-opens-a-fresh-session-every-rotate-every-queries", rotation, src)

    def dropped():
        def spec(query):
            return CE("connect: connection refused") if query == "q1" else (200, "application/json", json.dumps(GH_CANNED).encode())
        sessions, sections, has, lines, blocked, _args = gh_drive(host, spec, queries=("q1", "q2"))
        api = [len(s.api_calls()) for s in sessions]
        return (problem_if(api != [1, 1] or any(s.closed != 1 for s in sessions), "API calls %r closed %r: the failed session was not dropped" % (api, [s.closed for s in sessions]))
                + problem_if(len(lines) != 1 or not lines[0].startswith("  [grep.app error: connect: connection refused") or blocked, "stderr %r blocked %r" % (lines, blocked))
                + problem_if(not has or len(sections) != 2 or sections[0] != "## Query: q1\n\nNo results found.\n", "sections %r" % [sec.splitlines()[:1] for sec in sections])), ["q1's search raises -> that session is closed and dropped; q2 opens a fresh one and parses: API calls per session %r" % api]
    run_row(suite, GM, "github-transport-failure-drops-the-session-the-next-query-opens-a-fresh-one", dropped, src)


def host_regions(text):
    """[(BEGIN line, END line, source, names)] of every generated region in `text` (1-based lines)."""
    out, open_at = [], None
    for i, line in enumerate(text.splitlines(), 1):
        m = GH_MARKER_RX.match(line)
        if m:
            open_at = (i, m.group(1), [n.strip() for n in m.group(2).split(",") if n.strip()])
        elif GH_END_RX.match(line) and open_at is not None:
            out.append((open_at[0], i, open_at[1], open_at[2]))
            open_at = None
    return out


def hand_written(tree, regions):
    """The top-level statements of `tree` outside every generated region."""
    return [n for n in tree.body if not any(b <= n.lineno <= e for b, e, _s, _n in regions)]


def r14_problems(text):
    """R14: no module-level name the host binds by hand equals a name one of its regions carries."""
    regions = host_regions(text)
    bound = set()
    for node in hand_written(ast.parse(text), regions):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                bound.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
    names = set(n for r in regions for n in r[3])
    clash = sorted(bound & names)
    return problem_if(not regions, "no generated region found") + ["%s is bound by hand AND carried by a region" % n for n in clash], (len(regions), len(names), len(bound))


def rand_problems(text):
    """SEC-LOW: no call in the host's hand-written code passes rand=."""
    out = []
    for node in hand_written(ast.parse(text), host_regions(text)):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and any(k.arg == "rand" for k in sub.keywords):
                out.append("line %d passes rand=" % sub.lineno)
    return out


def gh_ast_rows(suite, text):
    src = "Scripts/search_github.py hand-written code (outside every # BEGIN/END GENERATED region)"

    def r14():
        problems, (nreg, nnames, nbound) = r14_problems(text)
        return problems, ["%d regions carrying %d names; %d module-level names bound by hand; no overlap (R14)" % (nreg, nnames, nbound)]
    run_row(suite, GM, "github-r14-no-hand-written-name-equals-a-region-name", r14, src + "; R14")

    def r14_control():
        problems, _counts = r14_problems(PLANTED_R14)
        return problem_if(problems != ["_ch_open_socket is bound by hand AND carried by a region"], "the checker reported %r" % problems), ["planted: a hand-written _ch_open_socket next to a region carrying it (caught)"]
    run_row(suite, GM, "control-r14-checker-catches-a-planted-shadow", r14_control, "negative control")

    def rand():
        return rand_problems(text), ["no hand-written call passes rand= (SEC-LOW: production draws from os.urandom)"]
    run_row(suite, GM, "github-ast-no-hand-written-call-passes-rand", rand, src + "; SEC-LOW")

    def rand_control():
        problems = rand_problems(PLANTED_RAND)
        return problem_if(problems != ["line 7 passes rand="], "the checker reported %r" % problems), ["planted: rand= in hand-written code (caught) and inside a region (ignored)"]
    run_row(suite, GM, "control-rand-checker-catches-a-planted-call-and-ignores-regions", rand_control, "negative control")


def gh_startup_row(suite):
    """NFR-5 as INFO: the host's cold `--help` minus a bare interpreter importing the host's own stdlib modules, best of STARTUP_RUNS."""
    base = [sys.executable, "-B", "-c", "import argparse, html, json, os, random, re, sys, time, urllib.parse"]
    full = [sys.executable, "-B", GH_HOST, "--help"]
    times, problems = {}, []
    for label, argv in (("baseline", base), ("host", full)):
        best = None
        for _ in range(STARTUP_RUNS):
            t0 = time.perf_counter()
            rc, _out, err = H.run_process(argv, timeout=60)
            dt = (time.perf_counter() - t0) * 1e3
            if rc != 0:
                problems.append("%s exited %d: %s" % (label, rc, err.strip().splitlines()[-1][:120] if err.strip() else ""))
            best = dt if best is None else min(best, dt)
        times[label] = best
    delta = times["host"] - times["baseline"]
    verdict = "within" if delta <= NFR5_BUDGET_MS else "OVER"
    detail = ["best of %d: host --help %.0f ms, baseline %.0f ms, delta %.0f ms -- %s the NFR-5 budget of %.0f ms; python %s"
              % (STARTUP_RUNS, times["host"], times["baseline"], delta, verdict, NFR5_BUDGET_MS, sys.version.split()[0]),
              "INFO, never a verdict: a wall-clock delta flaps with host load (docs/subsystems/tests.md)"]
    suite.record(GM, "github-startup-time-delta-nfr5", problems, status=None if problems else H.INFO, detail=detail,
                 brief="%s | github-startup-time-delta-nfr5 %.0f ms" % ("FAIL" if problems else "INFO", delta))


# --- M. hosts: search_duckduckgo's generated copy -----------------------------------

DDG_HOST = H.repo_path("Scripts", "search_duckduckgo.py")
DDG_URL = "https://lite.duckduckgo.com/lite/"
DDG_PATH = "/lite/"
BING_WARMUP_URL = "https://www.bing.com/"
BING_PATH = "/search"
BING_SERP = H.repo_path("tests", "files", "html", "tf_bing_serp.html")
BING_SERP_EXPECTED = H.repo_path("tests", "files", "html", "tf_bing_serp.expected.json")
HTML_TYPE = "text/html; charset=utf-8"

# Today's (pre-D16) CAPTCHA test, copied here from the plan (plan:1532) as the
# negative control of the structural-predicate rows: each of those rows asserts
# that THIS test is true on its page, so a row proves the change, not a tautology.
OLD_DDG_MARKERS = ("anomaly-modal", "Please complete the following")


def old_ddg_substring(text):
    return any(m in text for m in OLD_DDG_MARKERS)


def lite_page(query="test", snippet="A <b>tested</b> snippet.", results=True):
    """A DDG lite page written here from the lite markup parse_lite_results reads: the reflected query in the form, and one result (or none)."""
    form = "<form action=\"/lite/\" method=\"post\"><input class=\"query\" type=\"text\" name=\"q\" value=\"%s\"></form>" % query
    rows = ""
    if results:
        rows = ("<tr><td valign=\"top\">1.&nbsp;</td><td><a rel=\"nofollow\" href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fdoc&amp;rut=abc\" class='result-link'>Example <b>Doc</b></a></td></tr>"
                "<tr><td>&nbsp;</td><td class='result-snippet'>" + snippet + "</td></tr>")
    return ("<html><body>" + form + "<table>" + rows + "</table></body></html>").encode()


def lite_results(snippet="A tested snippet."):
    """What parse_lite_results must make of lite_page(): the uddg target unquoted, the <b> tags stripped."""
    return [{"url": "https://example.org/doc", "title": "Example Doc", "snippet": snippet}]


# NOT a measured page. task-038 (.claude/tmp/task038-live.txt) saw no anomaly
# page on either transport, so the challenge's real structure is unknown; this
# page exercises the plan's FALLBACK predicate (plan:1536): zero results, a
# marker in the page, the query not containing it. Status 202 as spec-ddg
# recorded it; since R-0052 that status alone is also a block (the
# ddg-predicate-202-* rows isolate it), and the marker rows judge a 200.
DDG_FALLBACK_CHALLENGE = (b"<html><body><form action=\"/lite/\" method=\"post\"><input name=\"q\" value=\"q\"></form>"
                          b"<div class=\"anomaly-modal\"><div class=\"anomaly-modal__title\">Please complete the following challenge to confirm this search was made by a human.</div></div></body></html>")
DDG_OK = (200, HTML_TYPE, lite_page("q"))
DDG_CHALLENGE = (202, HTML_TYPE, DDG_FALLBACK_CHALLENGE)
BING_403 = (403, HTML_TYPE, b"<html><body>Forbidden</body></html>")

DDG_CAPTCHA = "  [DDG CAPTCHA on: %s "
BING_BLOCKED = "  [Blocked by Bing on: %s]"


def bing_serp():
    with open(BING_SERP, "rb") as fh:
        return fh.read()


def bing_serp_expected():
    with open(BING_SERP_EXPECTED, encoding="utf-8") as fh:
        return json.load(fh)


def ddg_resp(host, transport, spec, default_url):
    """A scripted answer as the host's generated _ChResponse: spec is (status, content-type, body[, url[, decode_error]])."""
    status, ctype, body = spec[:3]
    url = spec[3] if len(spec) > 3 and spec[3] else default_url
    decode_error = spec[4] if len(spec) > 4 else None
    return host._ChResponse(status, [("content-type", ctype)], url, body, decode_error=decode_error,
                            impersonated=transport == "chrome", cert_verified=transport != "chrome", transport=transport)


class DdgStub:
    """A session the DDG/Bing ladder rows hand the host: warm-ups answer 200 html; a DDG POST answers specs["ddg"], a Bing GET specs["bing"].

    A spec may be a callable of the query (a per-query script), or an
    exception, which the call raises.
    """

    def __init__(self, host, transport, specs, warmup_exc=None):
        self.host = host
        self.transport = transport
        self.specs = specs
        self.warmup_exc = warmup_exc
        self.last_navigation_url = None
        self.calls = []
        self.warmups = []
        self.closed = 0

    def _answer(self, endpoint, query, default_url):
        spec = self.specs.get(endpoint)
        if callable(spec):
            spec = spec(query)
        if spec is None:
            raise AssertionError("no %s answer scripted for a %s session" % (endpoint, self.transport))
        if isinstance(spec, Exception):
            raise spec
        return ddg_resp(self.host, self.transport, spec, default_url)

    def get(self, url, params=None, headers=None, timeout=None, mode="navigate", referer=None, on_headers=None):
        if url in (DDG_URL, BING_WARMUP_URL):
            self.warmups.append(url)
            if self.warmup_exc is not None:
                raise self.warmup_exc
            self.last_navigation_url = url
            return ddg_resp(self.host, self.transport, (200, HTML_TYPE, b"<html>home</html>"), url)
        query = (params or {}).get("q")
        self.calls.append(("GET", url, mode, referer, query))
        return self._answer("bing", query, "https://www.bing.com/search?q=%s" % query)

    def post(self, url, data=None, headers=None, timeout=None, referer=None, on_headers=None):
        query = (data or {}).get("q")
        self.calls.append(("POST", url, "cors", referer, query))
        return self._answer("ddg", query, DDG_URL)

    def close(self):
        self.closed += 1


class DdgRecorder:
    """The host's create_session for one row: called with no argument (Chrome-only), returns a fresh DdgStub each time."""

    def __init__(self, host, specs, warmup_exc=None):
        self.host = host
        self.specs = specs
        self.warmup_exc = warmup_exc
        self.sessions = []
        self.args = []

    def __call__(self, *args, **kw):
        self.args.append((args, kw))
        s = DdgStub(self.host, "chrome", self.specs, self.warmup_exc)
        self.sessions.append(s)
        return s


def ddg_drive(host, specs, queries=("q",), rotate=None, warmup_exc=None):
    """_run_ddg_with_bing_fallback(queries, blocked=[]) over a DdgRecorder.

    Returns (sessions, sections, has_results, stderr lines, blocked, create_session args).
    """
    rec = DdgRecorder(host, specs, warmup_exc)
    err = io.StringIO()
    swaps = [(host, "create_session", rec), (host, "random", NoWait), (sys, "stderr", err)]
    if rotate is not None:
        swaps.append((host, "ROTATE_EVERY", rotate))
    blocked = []
    with Swapped(*swaps):
        sections, has = host._run_ddg_with_bing_fallback(list(queries), blocked=blocked)
    return rec.sessions, sections, has, err.getvalue().splitlines(), blocked, rec.args


def ddg_expect(run, searches, warmups, lines, blocked, nsections):
    """Problems of one ladder run against its expected shape.

    searches: [(method, ...)] per session; warmups: warm-up URLs per session;
    lines: stderr line PREFIXES in order; blocked: the queries reported
    blocked; nsections: the number of output sections. Every session must
    have been created with no argument and closed; no section carries a label.
    """
    sessions, sections, _has, got_lines, got_blocked, args = run
    got_s = [[c[0] for c in s.calls] for s in sessions]
    got_w = [s.warmups for s in sessions]
    problems = problem_if(got_s != searches, "search calls per session %r, wanted %r" % (got_s, searches))
    problems += problem_if(got_w != warmups, "warm-ups per session %r, wanted %r" % (got_w, warmups))
    problems += problem_if(args != [((), {})] * len(sessions), "create_session called with %r, wanted no argument each time" % args)
    problems += problem_if(len(got_lines) != len(lines) or any(not g.startswith(w) for g, w in zip(got_lines, lines)), "stderr %r, wanted lines starting %r" % (got_lines, lines))
    problems += problem_if(got_blocked != list(blocked), "blocked %r, wanted %r" % (got_blocked, list(blocked)))
    problems += problem_if(len(sections) != nsections or any(LABEL_MARK in sec for sec in sections), "sections %r, wanted %d unlabelled" % ([sec.splitlines()[:1] for sec in sections], nsections))
    problems += problem_if(any(s.closed < 1 for s in sessions), "closed counts %r" % [s.closed for s in sessions])
    return problems


class HeadsPeer(SessionPeer):
    """The h2 SessionPeer that also records (conn, stream, HEADERS END_STREAM) for each request head."""

    def __init__(self, cc):
        self.heads = []
        SessionPeer.__init__(self, "h2", cc)

    def _h2_head(self, sock, cid, sid, streams, decoder):
        self.heads.append((cid, sid, streams[sid]["end"]))
        SessionPeer._h2_head(self, sock, cid, sid, streams, decoder)


def cors_post_oracle(referer):
    """(pairs, DATA payload, HEADERS END_STREAM) of the cors-post capture's POST (its :method POST stream), pseudo-headers dropped, `referer` in its slot."""
    for _name, h2 in h2_fixtures("cors-post"):
        for st in fixture_streams(h2):
            if dict(fixture_pairs(st)).get(":method") == "POST":
                pairs = [(n, referer if n == "referer" else v) for n, v in fixture_pairs(st) if not n.startswith(":")]
                payload = b"".join(bytes.fromhex(f["payload_hex"]) for f in st["data_frames"])
                return pairs, payload, bool(st["headers_flags"] & FL_END_STREAM)
    return None, None, None


def navigate_oracle():
    """The navigate capture's stream 1 (a typed navigation), pseudo-headers dropped."""
    for _name, h2 in h2_fixtures("navigate"):
        for st in fixture_streams(h2):
            if st["stream_id"] == 1:
                return [(n, v) for n, v in fixture_pairs(st) if not n.startswith(":")]
    return None


def ddg_exchange(host, peer, endpoint):
    """The Chrome-path happy path on a session of the host's own _ch_session_new over the h2 peer: warm-up, then one search."""
    if endpoint == "ddg":
        peer.plan[DDG_PATH] = (200, [("content-type", HTML_TYPE)], lite_page("test"))
    else:
        peer.plan["/"] = (200, [("content-type", HTML_TYPE)], b"<html>bing</html>")
        peer.plan[BING_PATH] = (200, [("content-type", HTML_TYPE)], bing_serp())
    calls = []
    pm = PortMap(host, peer.port)
    err = io.StringIO()
    mark, h0 = peer.mark(), len(peer.heads)
    with Swapped((host, "_ch_open_socket", pm), (host, "random", NoWait), (sys, "stderr", err)):
        s = host._ch_session_new(decoders=host._DECODERS, connect_policy=counting_policy(calls), timeout=SESSION_TIMEOUT, transport="chrome")
        try:
            host.warmup_session(s, endpoint=endpoint)
            nav = s.last_navigation_url
            results = host.search_ddg("test", s) if endpoint == "ddg" else host.search_bing("x", s)
        finally:
            s.close()
    seen, conns = peer.since(mark)
    return {"calls": calls, "pm": pm.calls, "nav": nav, "results": results, "seen": seen, "conns": conns, "heads": peer.heads[h0:], "stderr": err.getvalue()}


def ddg_profile_rows(suite, host, peer):
    src = "Step 12 group M: Scripts/search_duckduckgo.py's GENERATED copy, reached through a port-443 _ch_open_socket wrapper"
    ddg = outcome(lambda: ddg_exchange(host, peer, "ddg"))
    bing = outcome(lambda: ddg_exchange(host, peer, "bing"))

    def need(ex):
        if ex[0] == "exc":
            raise ex[1]
        return ex[1]

    def post_headers():
        x = need(ddg)
        post = x["seen"][-1] if x["seen"] else None
        want, _payload, _end = cors_post_oracle(DDG_URL)
        if post is None or want is None:
            return ["no POST recorded (%d) or no cors-post capture (%r)" % (len(x["seen"]), want is None)], []
        got = [(k, v) for k, v in post["headers"]]
        i = first_difference(got, want)
        problems = problem_if(got != want, "field %d: emitted %r, the capture has %r" % (i, got[i] if i < len(got) else None, want[i] if i < len(want) else None))
        problems += problem_if((post["method"], post["authority"], post["path"]) != ("POST", "lite.duckduckgo.com", DDG_PATH), "pseudo %r" % ((post["method"], post["authority"], post["path"]),))
        return problems, ["%d fields in the captured cors-post order (tests/files/chrome/154/cors-post, the POST stream), referer the warm-up URL, decoded by chrome_capture's HpackDecoder" % len(got)]
    run_row(suite, GM, "ddg-chrome-cors-post-headers-equal-the-captured-profile", post_headers, src)

    def post_body():
        x = need(ddg)
        post = x["seen"][-1] if x["seen"] else {"body": None, "sid": None, "conn": None}
        _want, payload, end = cors_post_oracle(DDG_URL)
        head = [hd for hd in x["heads"] if (hd[0], hd[1]) == (post["conn"], post["sid"])]
        return (problem_if(post["body"] != payload, "POST body %r, the capture's DATA %r" % (post["body"], payload))
                + problem_if(head != [(post["conn"], post["sid"], end)], "HEADERS END_STREAM %r, the capture's %r" % (head, end))), ["HEADERS without END_STREAM, then the body %r -- the capture's DATA payload byte for byte (query 'test')" % payload]
    run_row(suite, GM, "ddg-chrome-post-headers-then-data-equal-the-captured-body", post_body, src + "; tests/files/chrome/154/cors-post POST stream data_frames")

    def referer_row():
        x = need(ddg)
        post = x["seen"][-1] if x["seen"] else {"headers": []}
        ref = one(post, "referer")
        return problem_if(x["nav"] != DDG_URL or ref != x["nav"], "warm-up final URL %r, the POST's referer %r" % (x["nav"], ref)), ["the POST's referer is the warm-up navigation's final URL %r (L8; set 2: the full page URL), not today's hard-coded https://lite.duckduckgo.com/" % ref]
    run_row(suite, GM, "ddg-chrome-referer-is-the-warmup-navigations-final-url", referer_row, src + "; L8")

    def stream_row():
        x = need(ddg)
        got = [(q["method"], q["path"], q["sid"]) for q in x["seen"]]
        modes = [one(q, "sec-fetch-mode") for q in x["seen"]]
        return (problem_if(got != [("GET", DDG_PATH, 1), ("POST", DDG_PATH, 3)] or x["conns"] != 1, "requests %r over %d connection(s)" % (got, x["conns"]))
                + problem_if(modes != ["navigate", "cors"], "sec-fetch-mode %r" % modes)), ["the warm-up navigation on stream 1 and the cors POST on stream 3 of ONE h2 connection (the R1 scenario)"]
    run_row(suite, GM, "ddg-chrome-warmup-stream-1-then-post-stream-3-one-connection", stream_row, src)

    def parsed():
        x = need(ddg)
        return (problem_if(x["results"] != lite_results(), "parsed %r" % (x["results"],)) + problem_if(x["stderr"], "stderr %r" % x["stderr"][:200])), ["the canned lite page parsed to the one hand-written result (uddg unquoted, <b> stripped)"]
    run_row(suite, GM, "ddg-chrome-canned-lite-page-parsed", parsed, src)

    def port_row():
        x = need(ddg)
        return (problem_if(x["calls"] != [("lite.duckduckgo.com", 443)] * 2, "policy calls %r" % x["calls"])
                + problem_if(x["pm"] != [(["127.0.0.1"], 443)], "_ch_open_socket calls %r" % x["pm"])), ["the policy saw ('lite.duckduckgo.com', 443) on both requests; one open of ['127.0.0.1'] on 443, forwarded to the peer's port"]
    run_row(suite, GM, "ddg-port-443-wrapper-policy-sees-lite-443", port_row, src)

    def bing_headers():
        x = need(bing)
        search = x["seen"][-1] if x["seen"] else None
        want = navigate_oracle()
        if search is None or want is None:
            return ["no Bing request recorded (%d) or no navigate capture (%r)" % (len(x["seen"]), want is None)], []
        got = [(k, v) for k, v in search["headers"]]
        i = first_difference(got, want)
        got_seq = [(q["method"], q["path"], q["sid"]) for q in x["seen"]]
        return (problem_if(got != want, "field %d: emitted %r, the capture has %r" % (i, got[i] if i < len(got) else None, want[i] if i < len(want) else None))
                + problem_if(got_seq != [("GET", "/", 1), ("GET", "/search?q=x", 3)] or x["conns"] != 1 or search["authority"] != "www.bing.com", "requests %r over %d connection(s), authority %r" % (got_seq, x["conns"], search["authority"]))), ["the Bing search GET: %d fields equal to the navigate capture's stream 1; warm-up / and the search on streams 1, 3 of one connection" % len(got)]
    run_row(suite, GM, "bing-chrome-search-get-headers-equal-the-captured-navigate-profile", bing_headers, src + "; tests/files/chrome/154/navigate stream 1")

    def bing_parsed():
        x = need(bing)
        want = bing_serp_expected()
        return (problem_if(x["results"] != want, "parsed %d result(s), the expected file has %d; first %r" % (len(x["results"] or []), len(want), (x["results"] or [None])[0]))
                + problem_if(x["stderr"], "stderr %r" % x["stderr"][:200])), ["search_bing parsed tests/files/html/tf_bing_serp.html served by the peer to the %d fields of tf_bing_serp.expected.json" % len(want)]
    run_row(suite, GM, "bing-chrome-serp-fixture-parsed-to-the-expected-fields", bing_parsed, src + "; tests/files/html/tf_bing_serp.expected.json")


def ddg_policy_rows(suite, host, peer):
    src = "R2-N2: create_session() in Scripts/search_duckduckgo.py (Chrome-only)"

    def identity():
        return create_session_identity_problems(host)
    run_row(suite, GM, "ddg-create-session-policy-is-public-only-and-transport-chrome", identity, src)

    def metadata(endpoint, status):
        path = DDG_PATH if endpoint == "ddg" else BING_PATH
        own = "lite.duckduckgo.com" if endpoint == "ddg" else "www.bing.com"
        peer.plan[path] = (status, [("location", "https://metadata.test/")], b"")
        calls, gai = [], []
        pm = PortMap(host, peer.port)

        def policy(h, port):
            calls.append((h, port))
            if h == own:
                return ["127.0.0.1"]
            return host._ch_public_only_policy(h, port)
        err = io.StringIO()
        mark = peer.mark()
        with Swapped((host, "_ch_open_socket", pm), (socket, "getaddrinfo", answering_resolver(gai, ["169.254.169.254"])), (sys, "stderr", err)):
            s = host._ch_session_new(decoders=host._DECODERS, connect_policy=policy, timeout=SESSION_TIMEOUT, transport="chrome")
            try:
                got = host.search_ddg("q", s, note=host._cli_note) if endpoint == "ddg" else host.search_bing("q", s, note=host._cli_note)
            finally:
                s.close()
        seen, conns = peer.since(mark)
        lines = err.getvalue().splitlines()
        prefix = "  [%s error: policy: metadata.test resolves to 169.254.169.254 (" % ("DDG" if endpoint == "ddg" else "Bing")
        problems = (problem_if(got != [], "returned %r" % (got,))
                    + problem_if(len(lines) != 1 or not lines[0].startswith(prefix), "stderr %r, wanted one line starting %r" % (lines, prefix))
                    + problem_if(any("169.254.169.254" in a for a, _p in pm.calls) or len(pm.calls) != 1, "opens %r: wanted one, none to the metadata address" % pm.calls)
                    + problem_if(calls != [(own, 443), ("metadata.test", 443)] or gai != [("metadata.test", 443, socket.SOCK_STREAM)], "policy %r resolver %r" % (calls, gai))
                    + problem_if(len(seen) != 1 or conns != 1, "the peer served %d request(s) over %d connection(s)" % (len(seen), conns)))
        if endpoint == "ddg":
            problems += problem_if(seen and (seen[0]["method"], seen[0]["body"]) != ("POST", b"q=q&kl="), "the one request %r" % ((seen[0]["method"], seen[0]["body"]),))
        return problems, ["%d Location: https://metadata.test/ -> %r; opens %r (none to 169.254.169.254); the peer saw %d request(s)" % (status, lines[0][:90] if lines else None, pm.calls, len(seen))]
    run_row(suite, GM, "bing-redirect-302-into-metadata-is-a-policy-refusal-with-no-connect", lambda: metadata("bing", 302), src + "; _ch_public_only_policy delegated, getaddrinfo patched")
    run_row(suite, GM, "ddg-post-307-into-metadata-is-a-policy-refusal-the-body-sent-nowhere-else", lambda: metadata("ddg", 307), src + "; a 307 keeps the POST body, so the refusal must come before any connect")


def ddg_ladder_rows(suite, host):
    src = "D16: _ddg_blocked, _bing_blocked, run_web and _run_ddg_with_bing_fallback in Scripts/search_duckduckgo.py (Chrome-only: a Bing block is final)"
    CE = host.ChromeClientError
    refused = CE("connect: connection refused")
    bing_ok = (200, HTML_TYPE, bing_serp())
    D, B = [DDG_URL], [BING_WARMUP_URL]

    def row(cid, what, specs, shape, **kw):
        def fn():
            return ddg_expect(ddg_drive(host, specs, **kw), *shape), [what]
        run_row(suite, GM, cid, fn, src)

    # The control, then the DDG -> Bing switch.
    row("ddg-ladder-results-are-parsed-and-unlabelled", "200 lite results -> parsed on the one DDG session, no label, nothing on stderr (the ladder's control)",
        {"ddg": DDG_OK}, ([["POST"]], [D], [], [], 1))
    row("ddg-ladder-challenge-switches-to-bing-on-a-fresh-chrome-session",
        "a DDG answer _ddg_blocked judges a block -> the [DDG CAPTCHA ...] line; the DDG session is closed and a NEW session (create_session(), Chrome) is warmed up for bing and asked once; results, no label",
        {"ddg": DDG_CHALLENGE, "bing": bing_ok}, ([["POST"], ["GET"]], [D, B], [DDG_CAPTCHA % "q"], [], 1))
    row("bing-403-after-the-ddg-switch-is-blocked-with-no-second-attempt",
        "DDG challenged, then a Bing 403 from www.bing.com -> ONE blocked line (FR-9: no [Bing HTTP 403] line before it), the query in blocked, no second attempt, no section",
        {"ddg": DDG_CHALLENGE, "bing": BING_403}, ([["POST"], ["GET"]], [D, B], [DDG_CAPTCHA % "q", BING_BLOCKED % "q"], ["q"], 0))

    def bing_partial(query):
        return BING_403 if query == "q1" else bing_ok
    row("bing-block-of-one-query-keeps-the-other-querys-results",
        "two queries, DDG challenged on q1, Bing blocks q1 only -> q1 blocked (one blocked line, its 'No results found.' section), q2 parsed on the same Bing session",
        {"ddg": DDG_CHALLENGE, "bing": bing_partial}, ([["POST"], ["GET", "GET"]], [D, B], [DDG_CAPTCHA % "q1", BING_BLOCKED % "q1"], ["q1"], 2), queries=("q1", "q2"))
    for status in (429, 500, 503):
        row("bing-%d-is-not-a-block-m3" % status, "DDG challenged, then Bing %d -> today's [Bing HTTP %d] line, nothing blocked (D16 M3)" % (status, status),
            {"ddg": DDG_CHALLENGE, "bing": (status, HTML_TYPE, b"<html>busy</html>")}, ([["POST"], ["GET"]], [D, B], [DDG_CAPTCHA % "q", "  [Bing HTTP %d for: q]" % status], [], 0))

    # D16 M2: a transport failure is never a block.
    row("ddg-transport-failure-is-never-a-block-m2",
        "the POST raises connect: connection refused -> [] with the [DDG error: ...] line, no switch to Bing, nothing blocked",
        {"ddg": refused}, ([["POST"]], [D], ["  [DDG error: connect: connection refused"], [], 0))
    row("ddg-warmup-and-post-transport-failure-is-never-a-block-m2",
        "the warm-up AND the POST raise connect: connection refused -> the warm-up is only swallowed, one [DDG error: ...] line, no Bing, nothing blocked",
        {"ddg": refused}, ([["POST"]], [D], ["  [DDG error: connect: connection refused"], [], 0), warmup_exc=refused)
    row("bing-transport-failure-is-never-a-block-m2",
        "DDG challenged, then the Bing GET raises connect: connection refused -> [] with the [Bing error: ...] line, nothing blocked",
        {"ddg": DDG_CHALLENGE, "bing": refused}, ([["POST"], ["GET"]], [D, B], [DDG_CAPTCHA % "q", "  [Bing error: connect: connection refused"], [], 0))
    # D16 S2: the endpoint's own host only.
    row("ddg-challenge-from-another-host-is-not-a-block-s2",
        "a zero-result challenge page whose final URL is https://elsewhere.test/lite/ -> not a block: no Bing switch, nothing blocked",
        {"ddg": DDG_CHALLENGE + ("https://elsewhere.test/lite/",)}, ([["POST"]], [D], [], [], 0))
    row("bing-403-from-another-host-is-not-a-block-s2",
        "DDG challenged, then a Bing 403 whose final URL is https://elsewhere.test/search -> the [Bing HTTP 403] line only, nothing blocked",
        {"ddg": DDG_CHALLENGE, "bing": BING_403 + ("https://elsewhere.test/search?q=q",)}, ([["POST"], ["GET"]], [D, B], [DDG_CAPTCHA % "q", "  [Bing HTTP 403 for: q]"], [], 0))
    row("ddg-undecodable-body-is-not-a-block",
        "a DDG answer whose body could not be decoded -> the [DDG body undecodable: ...] line and [], never taken for a CAPTCHA",
        {"ddg": (200, HTML_TYPE, b"", None, "br: corrupt input")}, ([["POST"]], [D], ["  [DDG body undecodable: br: corrupt input]"], [], 0))
    # D16 L2: an answer with no results carries no label.
    row("ddg-no-results-carries-no-label-l2",
        "two queries whose answers parse to [] -> the unlabelled per-query 'No results found.' sections on one session (D16 L2)",
        {"ddg": (200, HTML_TYPE, lite_page("q", results=False))}, ([["POST", "POST"]], [D], [], [], 2), queries=("q1", "q2"))

    # The switch holds for the rest of the run; pacing and rotation count queries, not endpoints (D16 L1).
    row("ddg-switch-moves-every-later-query-to-bing-and-rotation-counts-queries-l1",
        "three queries, ROTATE_EVERY=2: a challenge on q1 -> q1's switch opens the Bing session with no rotation count; q2 stays on it, never on DDG; the rotation before q3 opens a fresh Bing session warmed up for bing",
        {"ddg": DDG_CHALLENGE, "bing": bing_ok}, ([["POST"], ["GET", "GET"], ["GET"]], [D, B, B], [DDG_CAPTCHA % "q1"], [], 3), queries=("q1", "q2", "q3"), rotate=2)


def ddg_predicate_rows(suite, host):
    src = "D16 S1: _ddg_blocked in Scripts/search_duckduckgo.py (the plan's FALLBACK predicate, plan:1536; the structural marker is unmeasured)"

    def judged(query, page):
        resp = ddg_resp(host, "chrome", (200, HTML_TYPE, page), DDG_URL)
        return host._ddg_blocked(resp, query)

    reflected = (
        ("ddg-predicate-query-anomaly-modal-on-a-results-page-is-not-a-block", "anomaly-modal", lite_page("anomaly-modal"), lite_results(), "the QUERY is anomaly-modal (reflected in the form)"),
        ("ddg-predicate-query-please-complete-the-following-on-a-results-page-is-not-a-block", "Please complete the following", lite_page("Please complete the following"), lite_results(), "the QUERY is 'Please complete the following'"),
        ("ddg-predicate-snippet-anomaly-modal-is-not-a-block", "q", lite_page("q", snippet="The anomaly-modal div of a page"), lite_results("The anomaly-modal div of a page"), "a result SNIPPET carries anomaly-modal"),
        ("ddg-predicate-snippet-please-complete-the-following-is-not-a-block", "q", lite_page("q", snippet="Please complete the following form"), lite_results("Please complete the following form"), "a result SNIPPET carries 'Please complete the following'"),
    )
    for cid, query, page, want, what in reflected:
        def fn(query=query, page=page, want=want, what=what):
            sessions, sections, has, lines, blocked, _args = run = ddg_drive(host, {"ddg": (200, HTML_TYPE, page)}, queries=(query,))
            problems = ddg_expect(run, [["POST"]], [[DDG_URL]], [], [], 1)
            problems += problem_if(not has or "### Result 1: Example Doc" not in sections[0], "results %r" % sections[:1])
            problems += problem_if(host.parse_lite_results(page.decode()) != want, "the page parses to %r" % host.parse_lite_results(page.decode()))
            problems += problem_if(judged(query, page) is not False, "_ddg_blocked judged it a block")
            problems += problem_if(not old_ddg_substring(page.decode()), "negative control: today's substring test is NOT true on this page, so the row proves nothing")
            return problems, ["%s on a NORMAL lite page (one result): not a block, results returned on the DDG session; today's substring test IS true on the same page (the control)" % what]
        run_row(suite, GM, cid, fn, src)

    def positive():
        page = DDG_FALLBACK_CHALLENGE
        return (problem_if(judged("q", page) is not True, "_ddg_blocked judged the fallback challenge page not a block")
                + problem_if(host.parse_lite_results(page.decode()) != [], "the page parses to results")
                + problem_if(not old_ddg_substring(page.decode()), "the control")), ["zero results + the marker, query 'q' not containing it, own host -> a block (the FALLBACK predicate: no live challenge page was ever observed, task-038)"]
    run_row(suite, GM, "ddg-predicate-zero-results-and-a-marker-is-a-block-fallback-predicate", positive, src)

    def query_only():
        page = lite_page("anomaly-modal", results=False)
        run = ddg_drive(host, {"ddg": (200, HTML_TYPE, page)}, queries=("anomaly-modal",))
        return (ddg_expect(run, [["POST"]], [[DDG_URL]], [], [], 0)
                + problem_if(judged("anomaly-modal", page) is not False, "_ddg_blocked judged it a block")
                + problem_if(not old_ddg_substring(page.decode()), "the control")), ["a zero-result page whose only marker text is the reflected query -> NOT a block (the fallback predicate's last clause); today's substring test IS true on it"]
    run_row(suite, GM, "ddg-predicate-zero-results-marker-only-in-the-reflected-query-is-not-a-block", query_only, src)

    # R-0052: HTTP 202 is the one measured challenge signal (spec-ddg recorded
    # it; normal lite results answer 200). Own host + zero results + 202 is a
    # block with NO marker; each other row drops exactly one of the three.
    empty = lite_page("q", results=False)
    status_rows = (
        ("ddg-predicate-202-zero-results-no-marker-is-a-block", 202, empty, None, True,
         "202 + zero results + no marker, own host -> a block (the status is the signal, not the marker)"),
        ("ddg-predicate-202-with-results-is-not-a-block", 202, lite_page("q"), None, False,
         "202 on a page that parses to one result -> NOT a block"),
        ("ddg-predicate-202-zero-results-from-another-host-is-not-a-block", 202, empty, "https://elsewhere.test/lite/", False,
         "202 + zero results answered by a redirect target (elsewhere.test) -> NOT a block (the host check comes first)"),
        ("ddg-predicate-200-zero-results-no-marker-is-not-a-block", 200, empty, None, False,
         "200 + zero results + no marker -> NOT a block (a genuine empty search)"),
    )
    for cid, status, page, url, want, what in status_rows:
        def fn(status=status, page=page, url=url, want=want, what=what):
            resp = ddg_resp(host, "chrome", (status, HTML_TYPE, page, url), DDG_URL)
            got = host._ddg_blocked(resp, "q")
            return (problem_if(got is not want, "_ddg_blocked returned %r, want %r" % (got, want))
                    + problem_if(old_ddg_substring(page.decode()), "the control: the page carries a challenge marker, so the row does not isolate the status")), [what]
        run_row(suite, GM, cid, fn, src)


def ddg_ast_rows(suite, text):
    src = "Scripts/search_duckduckgo.py hand-written code (outside every # BEGIN/END GENERATED region)"

    def r14():
        problems, (nreg, nnames, nbound) = r14_problems(text)
        return problems, ["%d regions carrying %d names; %d module-level names bound by hand; no overlap (R14)" % (nreg, nnames, nbound)]
    run_row(suite, GM, "ddg-r14-no-hand-written-name-equals-a-region-name", r14, src + "; R14")

    def rand():
        return rand_problems(text), ["no hand-written call passes rand= (SEC-LOW: production draws from os.urandom)"]
    run_row(suite, GM, "ddg-ast-no-hand-written-call-passes-rand", rand, src + "; SEC-LOW")


# --- M. hosts: the F32 body cap and the single DDG parse -----------------------------

# Security review 20261001-082224 F32: every search session caps a body at 2 MiB
# (not the client's 64 MiB fallback), and DDG parses each response once.
F32_CAP = 2 * 1024 * 1024


class CallCounter:
    """A counting stand-in for one host function: forwards every call, counts them."""

    def __init__(self, fn):
        self.fn = fn
        self.n = 0

    def __call__(self, *args, **kw):
        self.n += 1
        return self.fn(*args, **kw)


def cap_identity_problems(host):
    """(problems, detail): create_session() carries max_bytes == SEARCH_MAX_BYTES == 2 MiB on its one (Chrome) transport, and that is the session's wire limit."""
    declared = getattr(host, "SEARCH_MAX_BYTES", None)
    problems = problem_if(declared != F32_CAP, "SEARCH_MAX_BYTES %r, wanted %d" % (declared, F32_CAP))
    s = host.create_session()
    try:
        seen = (s.transport, s.max_bytes, s._wire_limit())
        problems += problem_if(s.transport != "chrome", "session transport %r, wanted 'chrome'" % s.transport)
        problems += problem_if(s.max_bytes != declared or s._wire_limit() != declared, "max_bytes %r, wire limit %r, SEARCH_MAX_BYTES %r" % (s.max_bytes, s._wire_limit(), declared))
    finally:
        s.close()
    return problems, ["create_session(): (transport, max_bytes, wire limit) %r == SEARCH_MAX_BYTES -- never the 64 MiB fallback" % (seen,)]


# The oversized rows swap the host's SEARCH_MAX_BYTES down to this and send
# OVERSIZED_CAP + 1 bytes. At the real 2 MiB the body crosses the pure-Python TLS
# decrypt (~0.5 MiB/s floor) and, under fleet CPU load, the host's own per-call
# deadline fired first ("timeout: read timed out"), so the row measured the
# machine rather than the cap. The 2 MiB value itself is pinned by the
# create-session cap-identity rows, which never send a body.
OVERSIZED_CAP = 64 * 1024


def oversized_run(host, peer, warmup, path, ctype, runner, cap=OVERSIZED_CAP):
    """runner() over the h2 peer with host.SEARCH_MAX_BYTES swapped to `cap`, `path` answering cap + 1 bytes: (_ch_session_new calls, stderr lines, peer requests, runner's value).

    create_session() runs for real (Chrome path, the public-only policy) and
    reads SEARCH_MAX_BYTES at call time, so the swapped cap is the session's
    max_bytes; the row only maps the sentinel address to the peer.
    """
    if warmup is not None:
        peer.plan[warmup] = (200, [("content-type", HTML_TYPE)], b"<html>home</html>")
    peer.plan[path] = (200, [("content-type", ctype)], b"x" * (cap + 1))
    original_new = host._ch_session_new
    news, gai = [], []

    def session_new(*args, **kw):
        news.append((kw.get("transport"), kw.get("max_bytes")))
        return original_new(*args, **kw)
    pm = PortMap(host, peer.port, addr_map={GH_SENTINEL_ADDR: "127.0.0.1"})
    err = io.StringIO()
    mark = peer.mark()
    try:
        with Swapped((host, "SEARCH_MAX_BYTES", cap), (host, "_ch_session_new", session_new), (host, "_ch_open_socket", pm), (host, "random", NoWait),
                     (socket, "getaddrinfo", answering_resolver(gai, [GH_SENTINEL_ADDR])), (sys, "stderr", err)):
            got = runner()
    finally:
        peer.plan.pop(path, None)
    seen, _conns = peer.since(mark)
    return news, err.getvalue().splitlines(), seen, got


def oversized_problems(run, prefix, requests, cap=OVERSIZED_CAP):
    """Not a block: one Chrome session with max_bytes == the swapped SEARCH_MAX_BYTES (`cap`), one error line naming it, no results, no label."""
    news, lines, seen, (sections, has) = run
    want_line = "body: exceeds %d bytes" % cap
    got_requests = [(q["method"], q["path"]) for q in seen]
    return (problem_if(news != [("chrome", cap)], "_ch_session_new calls (transport, max_bytes) %r, wanted one chrome session with max_bytes == the swapped SEARCH_MAX_BYTES (%d); a second one is a re-issue or a Bing switch" % (news, cap))
            + problem_if(len(lines) != 1 or not lines[0].startswith(prefix) or want_line not in lines[0], "stderr %r, wanted one line starting %r naming %r" % (lines, prefix, want_line))
            + problem_if(has or any(LABEL_MARK in sec for sec in sections), "results %r" % (sections[:1],))
            + problem_if(got_requests != requests, "the peer saw %r, wanted %r" % (got_requests, requests)))


def gh_cap_rows(suite, host, peer):
    src = "F32: create_session() and search_github in Scripts/search_github.py; SEARCH_MAX_BYTES"

    def identity():
        return cap_identity_problems(host)
    run_row(suite, GM, "github-create-session-caps-the-body-at-2-mib-f32", identity, src)

    def oversized():
        run = oversized_run(host, peer, "/", GH_API_PATH, "application/json", lambda: host._run_github(["x"]))
        return oversized_problems(run, "  [grep.app error: ", [("GET", "/"), ("GET", "/api/search?q=x")]), ["SEARCH_MAX_BYTES swapped to %d: a grep.app answer of %d bytes (cap + 1) -> ChromeBodyTooLarge reported as %r, [] and no second attempt: a too-large body is a transport failure, never a block (D16 M2)" % (OVERSIZED_CAP, OVERSIZED_CAP + 1, (run[1] or [None])[0])]
    run_row(suite, GM, "github-oversized-body-is-a-transport-failure-not-a-block-f32", oversized, src + "; D16 M2")


def ddg_cap_rows(suite, host, peer):
    src = "F32: create_session(), search_ddg and _ddg_blocked in Scripts/search_duckduckgo.py; SEARCH_MAX_BYTES"

    def identity():
        return cap_identity_problems(host)
    run_row(suite, GM, "ddg-create-session-caps-the-body-at-2-mib-f32", identity, src)

    def oversized():
        # The warm-up GET shares /lite/ with the POST, so it is oversized too and only swallowed.
        run = oversized_run(host, peer, None, DDG_PATH, HTML_TYPE, lambda: host._run_ddg_with_bing_fallback(["test"]))
        return oversized_problems(run, "  [DDG error: ", [("GET", DDG_PATH), ("POST", DDG_PATH)]), ["SEARCH_MAX_BYTES swapped to %d: a DDG answer of %d bytes (cap + 1) -> %r, [], no second attempt and no switch to Bing: a too-large body is a transport failure, never a block (D16 M2)" % (OVERSIZED_CAP, OVERSIZED_CAP + 1, (run[1] or [None])[0])]
    run_row(suite, GM, "ddg-oversized-body-is-a-transport-failure-not-a-block-f32", oversized, src + "; D16 M2")

    def once():
        problems, detail = [], []
        bing_ok = (200, HTML_TYPE, bing_serp())
        cases = (("results", {"ddg": DDG_OK}, 1),
                 ("challenge, then Bing", {"ddg": DDG_CHALLENGE, "bing": bing_ok}, 1))
        for label, specs, responses in cases:
            counter = CallCounter(host.parse_lite_results)
            with Swapped((host, "parse_lite_results", counter)):
                sessions = ddg_drive(host, specs)[0]
            posts = sum(1 for s in sessions for c in s.calls if c[0] == "POST")
            problems += problem_if(posts != responses or counter.n != responses, "%s: %d DDG response(s), parse_lite_results ran %d time(s)" % (label, posts, counter.n))
            detail.append("%s: %d DDG response(s), parse_lite_results ran %d time(s)" % (label, posts, counter.n))
        return problems, detail
    run_row(suite, GM, "ddg-each-response-is-parsed-once-f32", once, src)

    def reuse():
        challenge = ddg_resp(host, "chrome", DDG_CHALLENGE, DDG_URL)
        ok = ddg_resp(host, "chrome", DDG_OK, DDG_URL)
        counter = CallCounter(host.parse_lite_results)
        with Swapped((host, "parse_lite_results", counter)):
            given_empty = host._ddg_blocked(challenge, "q", [])
            given_hits = host._ddg_blocked(challenge, "q", lite_results())
            n_given = counter.n
            own_challenge = host._ddg_blocked(challenge, "q")
            own_ok = host._ddg_blocked(ok, "q")
        return (problem_if(given_empty is not True or given_hits is not False, "with the caller's results: [] -> %r, one hit -> %r" % (given_empty, given_hits))
                + problem_if(n_given != 0, "the parser ran %d time(s) although the caller handed its results" % n_given)
                + problem_if(own_challenge is not True or own_ok is not False or counter.n != 2, "without results: challenge %r, results page %r, parser runs %d (wanted 2: the counter's control)" % (own_challenge, own_ok, counter.n))), \
            ["_ddg_blocked(resp, q, results) judges the caller's results and never parses (0 runs); without them it parses itself (2 runs for 2 calls, the control that the counter sees its parse)"]
    run_row(suite, GM, "ddg-blocked-reuses-the-callers-results-f32", reuse, src)


def host_hygiene_rows(suite, host, originals, tag):
    moved = [name for name in GH_SWAPPED if getattr(host, name) is not originals[name]]
    suite.record(GM, "hygiene-%s-host-ch-open-socket-is-the-original-after-the-group" % tag,
                 problem_if(host._ch_open_socket is not originals["_ch_open_socket"], "_ch_open_socket is %r" % host._ch_open_socket) + problem_if(moved, "not restored: %r" % moved),
                 detail=["`is` identity with the objects captured before any swap: %s" % ", ".join(GH_SWAPPED)])


# --- M. hosts: the command line answers -h / --help / an option before any network ---

class NetworkRefused(Exception):
    """Raised by every network entry point cli_run guards."""


def cli_run(host, script, argv):
    """host.main() with sys.argv = [script] + argv under a guard on every network entry point.

    create_session, _ch_session_new, socket.socket, socket.create_connection
    and socket.getaddrinfo each record their name and raise NetworkRefused, so
    a search attempt is stopped at its first step and no socket is ever
    opened. Returns (exit code, stdout, stderr, guarded calls);
    NetworkRefused escaping main is exit code None.
    """
    calls = []

    def guard(name):
        def refuse(*_args, **_kw):
            calls.append(name)
            raise NetworkRefused(name)
        return refuse
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with Swapped((host, "create_session", guard("create_session")), (host, "_ch_session_new", guard("_ch_session_new")),
                 (socket, "socket", guard("socket.socket")), (socket, "create_connection", guard("socket.create_connection")),
                 (socket, "getaddrinfo", guard("socket.getaddrinfo")),
                 (sys, "argv", [script] + list(argv)), (sys, "stdout", out), (sys, "stderr", err)):
        try:
            host.main()
            rc = 0
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except NetworkRefused:
            rc = None
    return rc, out.getvalue(), err.getvalue(), calls


def cli_rows(suite, host, script, tag):
    src = "Scripts/%s main(): the command line, judged before any network" % script

    def helps(flag):
        def fn():
            rc, out, err, calls = cli_run(host, script, [flag])
            first = out.splitlines()[0] if out else ""
            return (problem_if(rc != 0, "exit code %r, wanted 0" % rc)
                    + problem_if(not first.lower().startswith("usage: ") or script not in first, "stdout starts %r, wanted 'usage: ... %s'" % (first[:80], script))
                    + problem_if(err, "stderr %r" % err[:200])
                    + problem_if(calls, "network entry points reached: %r" % calls)), ["%s -> exit 0, %d stdout line(s) starting %r, nothing on stderr, no network entry point reached" % (flag, len(out.splitlines()), first[:60])]
        return fn
    run_row(suite, GM, "%s-cli-help-long-prints-usage-exit-0-no-network" % tag, helps("--help"), src)
    run_row(suite, GM, "%s-cli-help-short-prints-usage-exit-0-no-network" % tag, helps("-h"), src)

    def refused(argv, named):
        def fn():
            rc, out, err, calls = cli_run(host, script, argv)
            return (problem_if(rc != 2, "exit code %r, wanted 2" % rc)
                    + problem_if(out, "stdout %r" % out[:200])
                    + problem_if(not err.strip() or (named and "-x" not in err), "stderr %r, wanted a refusal%s" % (err[:200], " naming -x" if named else ""))
                    + problem_if(calls, "network entry points reached: %r" % calls)), ["%r -> exit 2, stderr %r, no network entry point reached" % (argv, err.strip().splitlines()[-1][:120] if err.strip() else "")]
        return fn
    run_row(suite, GM, "%s-cli-unknown-option-alone-is-refused-exit-2-no-network" % tag, refused(["-x"], False), src)
    run_row(suite, GM, "%s-cli-unknown-option-after-a-query-is-refused-exit-2-no-network" % tag, refused(["q", "-x"], True), src)

    def control():
        rc, _out, _err, calls = cli_run(host, script, ["q"])
        return problem_if(not calls or calls[0] != "create_session" or rc is not None, "a real query reached %r, exit code %r: the guard does not see a search" % (calls, rc)), \
            ["a plain query 'q' under the same guard: stopped at %r (exit code %r) -- the guard sees a real search, so the rows above prove its absence" % (calls[:1], rc)]
    run_row(suite, GM, "control-%s-cli-guard-catches-a-real-search" % tag, control, "negative control")


def cli_block_run(host, script, argv, recorder):
    """host.main() with sys.argv = [script] + argv, create_session swapped for `recorder`: (exit code, stdout, stderr lines)."""
    out, err = io.StringIO(), io.StringIO()
    with Swapped((host, "create_session", recorder), (host, "random", NoWait),
                 (sys, "argv", [script] + list(argv)), (sys, "stdout", out), (sys, "stderr", err)):
        try:
            host.main()
            rc = 0
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return rc, out.getvalue(), err.getvalue().splitlines()


def cli_block_rows(suite, host, script, tag, blocked_line, banner, result_mark, all_blocked, one_blocked):
    """FR-9: a query that ends blocked -> exit 1 and one blocked line; the other queries' results are still printed.

    all_blocked / one_blocked build the row's create_session stand-in: every
    query blocked, and q1 blocked while q2 answers.
    """
    src = "Scripts/%s main(): the exit code of a block (Chrome-only, no second attempt)" % script

    def single():
        rc, out, lines = cli_block_run(host, script, ["q"], all_blocked())
        return (problem_if(rc != 1, "exit code %r, wanted 1" % rc)
                + problem_if(out, "stdout %r" % out[:200])
                + problem_if([ln for ln in lines if ln.startswith("  [Blocked by ")] != [blocked_line % "q"] or (lines[-1:] != [blocked_line % "q"]), "stderr %r, wanted it to end in exactly one %r" % (lines, blocked_line % "q"))
                + problem_if("No results found for any query." in lines, "the no-results line was printed for a block: %r" % lines)), ["one query, blocked -> exit 1, nothing on stdout, stderr %r" % lines]
    run_row(suite, GM, "%s-cli-block-exits-1-with-one-blocked-line" % tag, single, src)

    def partial():
        rc, out, lines = cli_block_run(host, script, ["q1", "q2"], one_blocked())
        return (problem_if(rc != 1, "exit code %r, wanted 1" % rc)
                + problem_if(not out.startswith(banner) or result_mark not in out or LABEL_MARK in out, "stdout starts %r, wanted %r and %r, no label" % (out[:80], banner, result_mark))
                + problem_if([ln for ln in lines if ln.startswith("  [Blocked by ")] != [blocked_line % "q1"], "stderr %r, wanted exactly one %r" % (lines, blocked_line % "q1"))), ["q1 blocked, q2 answered -> q2's results on stdout under %r, exit 1, one blocked line for q1" % banner]
    run_row(suite, GM, "%s-cli-one-blocked-query-prints-the-others-results-and-exits-1" % tag, partial, src)


def group_hosts(suite, cc):
    """Group M: the GENERATED copies in search_github.py and search_duckduckgo.py driven over loopback, their policy, the Chrome-only block handling, the AST rules."""
    host = H.load_module_from_path("search_github_under_test", GH_HOST)
    with open(GH_HOST, encoding="utf-8") as fh:
        text = fh.read()
    originals = dict((name, getattr(host, name)) for name in GH_SWAPPED)
    s2 = SessionPeer("h2", cc)
    try:
        gh_profile_rows(suite, host, s2)
        gh_policy_rows(suite, host, s2)
        gh_ladder_rows(suite, host)
        gh_cap_rows(suite, host, s2)
        gh_ast_rows(suite, text)
        gh_startup_row(suite)
    finally:
        s2.close()
    gh_403 = (403, "text/html; charset=utf-8", b"<html><body>Access denied</body></html>")
    gh_ok = (200, "application/json", json.dumps(GH_CANNED).encode())
    cli_block_rows(suite, host, "search_github.py", "github", GH_BLOCKED, "# GitHub Search Results", "### Result 1: octo/demo - src/app.py",
                   lambda: SessionRecorder(host, gh_403), lambda: SessionRecorder(host, lambda q: gh_403 if q == "q1" else gh_ok))
    host_hygiene_rows(suite, host, originals, "github")
    cli_rows(suite, host, "search_github.py", "github")

    ddg = H.load_module_from_path("search_duckduckgo_under_test", DDG_HOST)
    with open(DDG_HOST, encoding="utf-8") as fh:
        ddg_text = fh.read()
    ddg_originals = dict((name, getattr(ddg, name)) for name in GH_SWAPPED)
    hp = HeadsPeer(cc)
    try:
        ddg_profile_rows(suite, ddg, hp)
        ddg_policy_rows(suite, ddg, hp)
        ddg_ladder_rows(suite, ddg)
        ddg_predicate_rows(suite, ddg)
        ddg_cap_rows(suite, ddg, hp)
        ddg_ast_rows(suite, ddg_text)
    finally:
        hp.close()
    bing_ok = (200, HTML_TYPE, bing_serp())
    cli_block_rows(suite, ddg, "search_duckduckgo.py", "ddg", BING_BLOCKED, "# DuckDuckGo Search Results", "### Result 1: ",
                   lambda: DdgRecorder(ddg, {"ddg": DDG_CHALLENGE, "bing": BING_403}),
                   lambda: DdgRecorder(ddg, {"ddg": DDG_CHALLENGE, "bing": lambda q: BING_403 if q == "q1" else bing_ok}))
    host_hygiene_rows(suite, ddg, ddg_originals, "ddg")
    cli_rows(suite, ddg, "search_duckduckgo.py", "ddg")

    group_webfetch(suite, cc)


# --- O. hosts: mcp-webfetch's generated copy --------------------------------------

WF_HOST = H.repo_path("Scripts", "mcp-webfetch.py")

# The two third-party modules webfetch imports at load (ADR 0024's declared
# exception); group O installs stubs for the load only and restores sys.modules.
WF_STUBBED = ("bs4", "markdownify")

# What the stub markdownify puts in front of the HTML it is handed: proof that
# the markdown branch ran, without bs4 / markdownify being installed.
WF_MD_MARK = "STUB-MARKDOWN"

# The names group O swaps on the webfetch module for a row; the hygiene row
# asserts each is the original object afterwards (`is` identity).
WF_SWAPPED = ("_ch_session_new", "_vet_host", "_ch_address_refused", "_ch_open_socket", "_check_host_allowed", "_cache_load", "random", "BeautifulSoup", "markdownify")

# _vet_host's way out, appended to an address-class refusal (Scripts/mcp-webfetch.py _vet_host).
WF_CONFUSED = ". Refusing: a URL-driven fetcher reaching the local network is the confused-deputy case. Pass allow_private=true if this is deliberate."

WF_FIREFOX = "profile: only 'chrome' (Chrome 154 fingerprint, certificate NOT verified) is available; omit profile for the verified default"
WF_HINT = "hint: this may be a bot block of the non-browser TLS client; profile=chrome retries with the Chrome 154 fingerprint (certificate NOT verified)"
WF_VERIFIED = "via verified (cert verified)"
WF_CHROME = "via chrome (cert NOT verified)"
WF_T12 = "via tls12-fallback (cert verified)"
WF_CLEAR = "via cleartext (no certificate)"
WF_UNKNOWN = "via unknown transport"
WF_HTML = (200, [("content-type", "text/html; charset=utf-8")], b"<html><body><p>hello webfetch</p></body></html>")
WF_PNG_BYTES = 1024 * 1024
WF_STALE_S = 100000
WF_UV_TIMEOUT = 180


class WfStubSoup:
    """bs4.BeautifulSoup's stand-in: no tags to drop, no main element, str() is the document."""

    def __init__(self, html, _parser=None):
        self.html = html

    def __call__(self, _names):
        return []

    def select_one(self, _selector):
        return None

    def __str__(self):
        return self.html


def wf_stub_markdownify(html, **_opts):
    return WF_MD_MARK + "\n" + html


def load_webfetch():
    """(module, sys.modules restored): Scripts/mcp-webfetch.py by path with stub bs4 / markdownify present for the load only."""
    missing = object()
    saved = dict((name, sys.modules.get(name, missing)) for name in WF_STUBBED)
    bs4 = types.ModuleType("bs4")
    bs4.BeautifulSoup = WfStubSoup
    md = types.ModuleType("markdownify")
    md.markdownify = wf_stub_markdownify
    sys.modules["bs4"] = bs4
    sys.modules["markdownify"] = md
    try:
        return H.load_module_from_path("mcp_webfetch_under_test", WF_HOST)
    finally:
        for name, old in saved.items():
            if old is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


class WebfetchPeer(VerifiedPeer):
    """A VerifiedPeer whose h1 answers can be paced: for a path in `paced` the head, then one 16 KiB chunk per PACE_S.

    The pacing stops as soon as the client wrote anything or hung up, and
    `data_sent[(conn, path)]` counts the body bytes written: the
    headers-before-size row reads it to prove the body was not downloaded.
    """

    def _h1(self, sock, cid):
        buf = b""
        while True:
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            head, _sep, buf = buf.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = [tuple(line.split(": ", 1)) for line in lines[1:]]
            n = int(dict((k.lower(), v) for k, v in headers).get("content-length", "0"))
            while len(buf) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
            body, buf = buf[:n], buf[n:]
            self._record(cid, None, method, path, dict((k.lower(), v) for k, v in headers).get("host"), headers, body)
            status, fields, rbody = self.answer(path)
            out = ("HTTP/1.1 %d X\r\n" % status + "".join("%s: %s\r\n" % (k, v) for k, v in fields) + "Content-Length: %d\r\n\r\n" % len(rbody)).encode("latin-1")
            if path.partition("?")[0] not in self.paced or method == "HEAD":
                sock.sendall(out + (b"" if method == "HEAD" else rbody))
                continue
            sock.sendall(out)
            self.data_sent[(cid, path)] = 0
            for off in range(0, len(rbody), 16384):
                if sock.pending() or select.select([sock], [], [], PACE_S)[0]:
                    return
                chunk = rbody[off:off + 16384]
                sock.sendall(chunk)
                self.data_sent[(cid, path)] += len(chunk)
            return


class DualPeer(SessionPeer):
    """An h2 SessionPeer whose TLS context offers ALPN h2 AND http/1.1: the Chrome engine gets h2, a client offering no ALPN gets HTTP/1.1.

    So one https URL is reachable by both transports: profile="chrome" lands
    on the scripted h2 server (RST_STREAM and paced bodies as group I), the
    verified default on the h1 one. `alpns` records each handshake's ALPN.
    """

    def __init__(self, cc):
        self.alpns = []
        SessionPeer.__init__(self, "h2", cc)
        self.ctx.set_alpn_protocols(["h2", "http/1.1"])

    def _serve(self, sock, cid):
        try:
            sock.settimeout(SESSION_TIMEOUT)
            try:
                sock = self.ctx.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError):
                return
            alpn = sock.selected_alpn_protocol()
            self.alpns.append(alpn)
            if alpn == "h2":
                self._h2(sock, cid)
            else:
                self._h1(sock, cid)
        except Exception as exc:  # a server thread records, never prints a traceback
            self.errors.append("%s(%r)" % (type(exc).__name__, str(exc)[:120]))
        finally:
            sock.close()


def wf_fetch(wf, root, params, trusted=True, news=None, swaps=()):
    """handle_fetch(params) with the module's _ch_session_new wrapped: each call's kwargs recorded in `news`, the test-CA factory injected when `trusted`.

    Production never passes ssl_context_factory; the wrapper adds it so the
    verified transport accepts the tests/files/tls leaf (the URL names
    127.0.0.1, which the leaf's SAN covers). The retry jitter is 0 s.
    """
    original = wf._ch_session_new
    seen = news if news is not None else []

    def session_new(*args, **kw):
        seen.append(dict(kw))
        if trusted:
            kw["ssl_context_factory"] = trusted_context
        return original(*args, **kw)
    with Swapped((wf, "_ch_session_new", session_new), (wf, "random", NoWait), *swaps):
        # root doubles as the cache directory: never the real XDG default (R-0054).
        return wf.handle_fetch(dict(params), root, root)


def wf_parts(result):
    """(title line, status line, the line after it, whole text) of a handle_fetch report; all None for an error or raw reply."""
    text = result.get("__raw_text__") if isinstance(result, dict) else None
    if not text or not text.startswith("# webfetch "):
        return None, None, None, text
    lines = text.split("\n")
    idx = next((i for i, line in enumerate(lines) if line.startswith("**status**:")), None)
    if idx is None:
        return lines[0], None, None, text
    return lines[0], lines[idx], (lines[idx + 1] if idx + 1 < len(lines) else None), text


def wf_title(url, tag=""):
    return "# webfetch — `%s`%s" % (url, " (%s)" % tag if tag else "")


def wf_report(result, url, status, label, tag=""):
    """Problems unless `result` is a report whose title line is exactly `url` + tag and whose status line is exactly `status` + label."""
    title, line, _nxt, _text = wf_parts(result)
    if title is None:
        return ["not a report: %s" % short(result)]
    want_line = "**status**: %d %s" % (status, label)
    return (problem_if(title != wf_title(url, tag), "title line %r, wanted %r" % (title, wf_title(url, tag)))
            + problem_if(line != want_line, "status line %r, wanted %r" % (line, want_line)))


def wf_error(result):
    return result.get("error") if isinstance(result, dict) else None


def wf_key(wf, url, headers=None, allow_private=True):
    """The cache key of GET `url`; allow_private defaults to True because every loopback row fetches with it."""
    return wf._cache_key(url, "GET", headers or {}, allow_private)


def wf_put(wf, root, url, headers=None, age=0, key_private=True, **fields):
    """Write a cache entry for GET `url` through the module's own writer; `fields` override the defaults, a value of None drops the key."""
    entry = {"url": url, "method": "GET", "status": 200, "final_url": url, "headers": {"content-type": "text/html; charset=utf-8"},
             "body": "<html><body>stored</body></html>", "size": 32, "etag": "", "last_modified": "", "fetched_at": int(time.time()) - age,
             "allow_private": key_private}
    entry.update(fields)
    entry = dict((k, v) for k, v in entry.items() if v is not None)
    wf._cache_save(root, wf_key(wf, url, headers, key_private), entry)


def wf_disk(wf, root, url, headers=None, allow_private=True):
    """(transport, cert_verified) of the on-disk entry for GET `url`, or None when there is none."""
    entry = wf._cache_load(root, wf_key(wf, url, headers, allow_private))
    return None if entry is None else (entry.get("transport"), entry.get("cert_verified"))


def counting_vet(wf, calls, answer=None):
    """A _vet_host stand-in recording (host, port, allow_private); it answers `answer` or delegates to the original."""
    original = wf._vet_host

    def vet(host, port, allow_private):
        calls.append((host, port, allow_private))
        if answer is not None:
            return list(answer)
        return original(host, port, allow_private)
    return vet


def recorder_of(calls, fn):
    def rec(*args, **kw):
        calls.append(args)
        return fn(*args, **kw)
    return rec


def wf_basic_rows(suite, wf, root, vp, vp2, dp):
    """200 HTML, the 403 ladder, profile=firefox, max_bytes, headers before size per transport, HEAD, the caller Cookie."""
    src = "Step 13 group O: handle_fetch in Scripts/mcp-webfetch.py over loopback, the verified default with the test-CA factory injected"
    vurl = "https://127.0.0.1:%d" % vp.port
    durl = "https://127.0.0.1:%d" % dp.port
    vp.plan["/page"] = WF_HTML
    vp.plan["/forbidden"] = (403, [("content-type", "text/html; charset=utf-8")], b"<html>denied</html>")
    vp.plan["/k1000"] = (200, [("content-type", "text/plain")], b"k" * 1000)
    vp.plan["/k1001"] = (200, [("content-type", "text/plain")], b"k" * 1001)
    png = (200, [("content-type", "image/png")], noise(WF_PNG_BYTES, b"webfetch-png"))
    for srv in (vp, dp):
        srv.plan["/png"] = png
        srv.paced.add("/png")

    def html():
        news = []
        mark, a0 = vp.mark(), len(vp.alpns)
        got = wf_fetch(wf, root, {"url": vurl + "/page", "allow_private": True, "cache_ttl": 0}, news=news)
        seen, conns = vp.since(mark)
        _title, _line, _nxt, text = wf_parts(got)
        problems = wf_report(got, vurl + "/page", 200, WF_VERIFIED)
        problems += problem_if(not text or WF_MD_MARK not in text or "hello webfetch" not in text, "the markdown branch did not run: %r" % (text or got)[:200])
        problems += problem_if(len(news) != 1 or news[0].get("transport") != "verified" or "ssl_context_factory" in news[0], "_ch_session_new kwargs %r" % news)
        problems += problem_if(news and (news[0].get("tls12_fallback") is not True or news[0].get("allow_downgrade") is not True), "tls12_fallback / allow_downgrade %r" % news[:1])
        problems += problem_if(conns != 1 or len(seen) != 1 or vp.alpns[a0:] != [None], "%d connection(s), %d request(s), ALPN %r" % (conns, len(seen), vp.alpns[a0:]))
        return problems, ["GET text/html over the verified default: the stub markdownify ran, one connection, no ALPN; production passed transport='verified', tls12_fallback=True, allow_downgrade=True and NO ssl_context_factory"]
    run_row(suite, GO, "webfetch-200-html-default-verified-markdown", html, src)

    def forbidden():
        news, calls = [], []
        mark, dmark = vp.mark(), dp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/forbidden", "allow_private": True, "cache_ttl": 0}, news=news,
                       swaps=((wf, "_vet_host", counting_vet(wf, calls)),))
        seen, conns = vp.since(mark)
        return {"got": got, "news": news, "calls": calls, "seen": seen, "conns": conns, "dconns": dp.since(dmark)[1]}
    ex = outcome(forbidden)

    def need():
        if ex[0] == "exc":
            raise ex[1]
        return ex[1]

    def retried():
        x = need()
        return (wf_report(x["got"], vurl + "/forbidden", 403, WF_VERIFIED)
                + problem_if(x["conns"] != 3 or len(x["seen"]) != 3, "the peer counted %d connection(s), %d request(s)" % (x["conns"], len(x["seen"])))
                + problem_if(len(x["news"]) != 3, "%d session(s) built" % len(x["news"]))), ["403 on every attempt: 3 attempts, 3 fresh sessions, the peer counted 3 connections"]
    run_row(suite, GO, "webfetch-403-retried-three-times-three-connections", retried, src + "; RETRY_STATUSES")

    def per_attempt():
        x = need()
        want = [("127.0.0.1", vp.port, True)] * 3
        return problem_if(x["calls"] != want, "_vet_host calls %r, wanted %r" % (x["calls"], want)), ["each of the 3 attempts ran the connect policy (a counting _vet_host wrapper): %r" % x["calls"]]
    run_row(suite, GO, "webfetch-each-retry-attempt-calls-the-policy", per_attempt, src + "; SEC-HIGH rule 6")

    def ladder():
        x = need()
        _title, line, nxt, text = wf_parts(x["got"])
        lines = (text or "").split("\n")
        retries = [ln for ln in lines if ln.startswith("**retries**:")]
        return (problem_if(nxt != WF_HINT, "the line after the status line is %r, wanted the hint" % nxt)
                + problem_if(retries != ["**retries**: verified → verified → verified"], "retries %r" % retries)
                + problem_if([n.get("transport") for n in x["news"]] != ["verified"] * 3, "transports %r" % [n.get("transport") for n in x["news"]])
                + problem_if(x["dconns"], "the h2 peer saw %d connection(s)" % x["dconns"])), ["status line %r, then the hint, then %r; no Chrome-path connection" % (line, retries[:1])]
    run_row(suite, GO, "webfetch-optin-default-403-attempts-verified-hint-no-chrome-connection", ladder, src + "; D15")

    def firefox():
        news = []
        mark, dmark = vp.mark(), dp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/page", "allow_private": True, "cache_ttl": 0, "profile": "firefox"}, news=news)
        direct = outcome(lambda: wf._create_session("firefox"))
        return (problem_if(wf_error(got) != WF_FIREFOX, "error %r" % (wf_error(got) or got,))
                + expect_refusal(direct, ValueError, WF_FIREFOX)
                + problem_if(news or vp.since(mark)[1] or dp.since(dmark)[1], "a session was built (%r) or a peer connected" % news)), ["profile='firefox' -> %r; no session, no connection" % WF_FIREFOX]
    run_row(suite, GO, "webfetch-optin-profile-firefox-refused-naming-the-verified-default", firefox, src + "; D15 _create_session")

    def over():
        got = wf_fetch(wf, root, {"url": vurl + "/k1001", "allow_private": True, "cache_ttl": 0, "max_bytes": 1000})
        want = "body exceeds max_bytes=1000. Raise max_bytes to convert it anyway."
        return problem_if(wf_error(got) != want, "%s, wanted %r" % (short(got), want)), ["1001 bytes, max_bytes=1000: %r" % wf_error(got)]
    run_row(suite, GO, "webfetch-max-bytes-one-byte-over-refused", over, src + "; R2-M6")

    def exact():
        got = wf_fetch(wf, root, {"url": vurl + "/k1000", "allow_private": True, "cache_ttl": 0, "max_bytes": 1000, "raw": True})
        text = got.get("__raw_text__") if isinstance(got, dict) else None
        return problem_if(text != "k" * 1000 + "\n", "%s" % short(got)), ["exactly max_bytes=1000 bytes: served (raw)"]
    run_row(suite, GO, "webfetch-max-bytes-exactly-served", exact, src)

    def negative():
        news = []
        got = wf_fetch(wf, root, {"url": vurl + "/k1001", "allow_private": True, "cache_ttl": 0, "max_bytes": -5, "raw": True}, news=news)
        text = got.get("__raw_text__") if isinstance(got, dict) else None
        return (problem_if(text != "k" * 1001 + "\n", "%s" % short(got))
                + problem_if([n.get("max_bytes") for n in news] != [0], "max_bytes handed to the session %r" % [n.get("max_bytes") for n in news])), ["max_bytes=-5 clamped to 0 (the session's own ceiling): 1001 bytes served"]
    run_row(suite, GO, "webfetch-negative-max-bytes-clamped-to-the-session-ceiling", negative, src)

    def png_message(url):
        return ("refusing to convert non-textual content-type 'image/png' from %s (status 200). resp.text on a binary "
                "body yields replacement-character mojibake, not content." % url)

    def size_default():
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/png", "allow_private": True, "cache_ttl": 0, "max_bytes": 1000})
        seen, _conns = vp.since(mark)
        keys = [(q["conn"], q["path"]) for q in seen]
        sent = wait_for(lambda: [vp.data_sent.get(k) for k in keys] if keys and all(k in vp.data_sent for k in keys) else None)
        return (problem_if(wf_error(got) != png_message(vurl + "/png"), "%s" % short(got))
                + problem_if(not sent or len(sent) != 1 or sent[0] is None or sent[0] >= WF_PNG_BYTES, "body bytes the peer wrote %r of %d" % (sent, WF_PNG_BYTES))), ["1 MiB image/png, max_bytes=1000, verified: the non-textual refusal (not the size one); the peer wrote %r of %d body bytes before the connection closed" % (sent, WF_PNG_BYTES)]
    run_row(suite, GO, "webfetch-headers-before-size-default-connection-closed-before-the-body", size_default, src + "; R2-M6, D15")

    def size_chrome():
        mark, r0 = dp.mark(), len(dp.rsts)
        got = wf_fetch(wf, root, {"url": durl + "/png", "allow_private": True, "cache_ttl": 0, "max_bytes": 1000, "profile": "chrome"})
        seen, _conns = dp.since(mark)
        keys = [(q["conn"], q["sid"]) for q in seen]
        rsts = wait_for(lambda: [r for r in dp.rsts[r0:] if (r[0], r[1]) in keys and r[2] == E_CANCEL])
        sent = [dp.data_sent.get(k) for k in keys]
        return (problem_if(wf_error(got) != png_message(durl + "/png"), "%s" % short(got))
                + problem_if(len(keys) != 1 or keys[0][1] is None, "requests %r" % keys)
                + problem_if(len(rsts) != 1, "RST_STREAM(CANCEL) recorded %r (all since: %r)" % (rsts, dp.rsts[r0:]))
                + problem_if(any(s is None or s >= WF_PNG_BYTES for s in sent), "DATA bytes written %r" % sent)), ["profile=chrome over h2: the non-textual refusal, RST_STREAM(CANCEL) %r, %r of %d DATA bytes written" % (rsts, sent, WF_PNG_BYTES)]
    run_row(suite, GO, "webfetch-headers-before-size-chrome-rst-stream-cancel", size_chrome, src + "; R2-M6, D15")

    def head():
        vp.plan["/headpage"] = WF_HTML
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/headpage", "method": "HEAD", "allow_private": True, "cache_ttl": 0})
        seen, _conns = vp.since(mark)
        req = seen[0] if seen else {"method": None, "headers": []}
        return (wf_report(got, vurl + "/headpage", 200, WF_VERIFIED)
                + problem_if(len(seen) != 1 or req["method"] != "HEAD", "the peer saw %r" % [(q["method"], q["path"]) for q in seen])
                + problem_if(one(req, "referer") != vurl + "/" or one(req, "sec-fetch-mode") != "cors", "referer %r sec-fetch-mode %r" % (one(req, "referer"), one(req, "sec-fetch-mode")))), ["HEAD over the verified default: one HEAD, the cors-HEAD profile with the target's own origin as Referer %r" % one(req, "referer")]
    run_row(suite, GO, "webfetch-head-default-transport", head, src + "; _issue")

    def cookie():
        vp.plan["/c1"] = (302, [("location", "/c2"), ("set-cookie", "a=1")], b"")
        vp.plan["/c2"] = (302, [("location", "https://127.0.0.1:%d/c3" % vp2.port)], b"")
        m1, m2 = vp.mark(), vp2.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/c1", "allow_private": True, "cache_ttl": 0, "headers": {"Cookie": "b=2"}, "output": "html"})
        one_two, _c = vp.since(m1)
        three, _c2 = vp2.since(m2)
        hops = [crumbs(q) for q in one_two] + [crumbs(q) for q in three]
        return (wf_report(got, "https://127.0.0.1:%d/c3" % vp2.port, 200, WF_VERIFIED)
                + problem_if(len(hops) != 3, "%d hop(s) recorded" % len(hops))
                + problem_if(len(hops) == 3 and (hops[0] != ["b=2"] or hops[1] != ["a=1", "b=2"] or "b=2" in hops[2]), "cookie crumbs per hop %r" % hops)), ["Cookie: b=2; hop 1 302 same-origin Set-Cookie a=1; hop 2 302 to another origin: crumbs per hop %r" % hops]
    run_row(suite, GO, "webfetch-caller-cookie-after-the-jar-and-dropped-cross-origin", cookie, src + "; FR-16, S035")


def wf_policy_rows(suite, wf, root, vp, vp2, dp, s1p):
    """Redirect re-vetting, the link-local redirect, _check_host_allowed's forms, the allow_private split, the downgrade."""
    src = "Step 13 group O: _vet_host / _connect_policy_for / _check_host_allowed in Scripts/mcp-webfetch.py (L2, R2-M7)"
    vurl = "https://127.0.0.1:%d" % vp.port
    vp2.plan["/page2"] = WF_HTML

    def second_port():
        calls = []
        got = wf_fetch(wf, root, {"url": vurl + "/r/302?https://127.0.0.1:%d/page2" % vp2.port, "allow_private": True, "cache_ttl": 0},
                       swaps=((wf, "_vet_host", counting_vet(wf, calls)),))
        want = [("127.0.0.1", vp.port, True), ("127.0.0.1", vp2.port, True)]
        return (wf_report(got, "https://127.0.0.1:%d/page2" % vp2.port, 200, WF_VERIFIED)
                + problem_if(calls != want, "_vet_host calls %r, wanted %r" % (calls, want))), ["302 to a second loopback port: the policy ran on both hops %r" % calls]
    run_row(suite, GO, "webfetch-redirect-to-a-second-port-goes-through-the-policy", second_port, src)

    def link_local():
        real_refused = wf._ch_address_refused
        real_gai = socket.getaddrinfo
        names, opens = [], []

        def refused(ip):
            return None if str(ip) == "127.0.0.1" else real_refused(ip)

        def gai(host, port, *args, **kw):
            names.append(host)
            if host == "metadata.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
            return real_gai(host, port, *args, **kw)
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/r/302?https://metadata.test/", "allow_private": False, "cache_ttl": 0},
                       swaps=((wf, "_ch_address_refused", refused), (socket, "getaddrinfo", gai), (wf, "_ch_open_socket", recorder_of(opens, wf._ch_open_socket))))
        seen, _conns = vp.since(mark)
        want = "fetch failed: ChromeClientError: policy: metadata.test resolves to 169.254.169.254 (link-local)" + WF_CONFUSED
        return (problem_if(wf_error(got) != want, "%s, wanted %r" % (short(got), want))
                + problem_if(any("169.254.169.254" in list(o[0]) for o in opens), "a connection was attempted: %r" % opens)
                + problem_if("metadata.test" not in names or len(seen) != 1, "resolved %r, the peer served %d" % (names, len(seen)))), ["allow_private=false, the loopback test host classified public by a patched _ch_address_refused: the 302 into metadata space refused by the policy, no connect to it"]
    run_row(suite, GO, "webfetch-allow-private-false-redirect-to-link-local-refused", link_local, src + "; getaddrinfo patched")

    forms = (
        ("public-plus-metadata-any-refused", ["93.184.216.34", "169.254.169.254"], "policy: public.test resolves to 169.254.169.254 (link-local)" + WF_CONFUSED),
        ("nat64-of-a-public-ipv4-refused", ["64:ff9b::808:808"], "policy: public.test resolves to 64:ff9b::808:808 (translation prefix 64:ff9b::/96)" + WF_CONFUSED),
        ("unparseable-answer-refused", ["not-an-ip"], "policy: public.test resolves to 'not-an-ip' (not an IP address)" + WF_CONFUSED),
        ("ipv4-mapped-public-allowed", ["::ffff:8.8.8.8"], None),
    )
    for cid, answer, want in forms:
        def form(answer=answer, want=want):
            calls = []
            with Swapped((socket, "getaddrinfo", answering_resolver(calls, answer))):
                got = wf._check_host_allowed("https://public.test/", False)
            return (problem_if(got != want, "returned %r, wanted %r" % (got, want))
                    + problem_if(calls != [("public.test", 443, socket.SOCK_STREAM)], "resolver calls %r" % calls)), ["resolver answer %r -> %r" % (answer, got)]
        run_row(suite, GO, "webfetch-check-host-allowed-%s" % cid, form, src + "; R2-M2")

    def file_scheme():
        calls = []
        got = wf_fetch(wf, root, {"url": vurl + "/r/302?file:///etc/passwd", "allow_private": True, "cache_ttl": 0},
                       swaps=((wf, "_vet_host", counting_vet(wf, calls)),))
        want = "fetch failed: ChromeClientError: redirect: scheme file refused"
        return (problem_if(wf_error(got) != want, "%s, wanted %r" % (short(got), want))
                + problem_if(calls != [("127.0.0.1", vp.port, True)], "_vet_host calls %r" % calls)), ["allow_private=true, 302 Location file:///etc/passwd: %r, the policy never saw it" % wf_error(got)]
    run_row(suite, GO, "webfetch-allow-private-true-redirect-to-file-refused", file_scheme, src)

    def guard_public():
        calls = []
        with Swapped((socket, "getaddrinfo", answering_resolver(calls, ["93.184.216.34"]))):
            got = wf._check_host_allowed("https://public.test/", False)
        return problem_if(got is not None or len(calls) != 1, "returned %r, resolver calls %r" % (got, calls)), ["allow_private=false, a public answer: the pre-cache guard returns None (the fetch is not refused)"]
    run_row(suite, GO, "webfetch-split-pre-cache-guard-public-host-returns-none", guard_public, src + "; R2-M7")

    def offline():
        url = vurl + "/offline"
        wf_put(wf, root, url, transport="verified", cert_verified=True)
        calls = []
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True}, swaps=((socket, "getaddrinfo", refusing_resolver(calls)),))
        return (wf_report(got, url, 200, WF_VERIFIED, "cached")
                + problem_if(calls or vp.since(mark)[1], "resolver calls %r, connections %d" % (calls, vp.since(mark)[1]))), ["allow_private=true: the guard resolves nothing (getaddrinfo patched to raise), so a fresh cached answer is served offline"]
    run_row(suite, GO, "webfetch-split-allow-private-guard-never-resolves-cache-served-offline", offline, src + "; R2-M7")

    def scheme():
        calls = []
        with Swapped((socket, "getaddrinfo", refusing_resolver(calls))):
            ftp = wf._check_host_allowed("ftp://x/", True)
            user = wf._check_host_allowed("https://u:p@x/", True)
        return (problem_if(ftp != "url: scheme ftp refused" or user != "url: userinfo not supported", "ftp %r userinfo %r" % (ftp, user))
                + problem_if(calls, "resolver calls %r" % calls)), ["allow_private=true still splits the URL: ftp %r, userinfo %r" % (ftp, user)]
    run_row(suite, GO, "webfetch-split-allow-private-true-still-refuses-ftp-and-userinfo", scheme, src + "; _ch_split_url")

    def sni():
        calls = []
        mark, n0 = dp.mark(), len(dp.snis)
        host = u"bücher.test."
        got = wf_fetch(wf, root, {"url": "https://%s:%d/echo" % (host, dp.port), "allow_private": True, "cache_ttl": 0, "profile": "chrome", "output": "html"},
                       swaps=((wf, "_vet_host", counting_vet(wf, calls, ["127.0.0.1"])),))
        want = alabel(u"bücher.test") + "."
        snis = [alabel(n) for n in dp.snis[n0:]]
        return (problem_if(wf_error(got) is not None, "%s" % short(got))
                + problem_if(calls != [(want, dp.port, True)], "_vet_host calls %r, wanted host %r" % (calls, want))
                + problem_if(snis != [want.rstrip(".")], "SNI %r, wanted %r" % (snis, want.rstrip(".")))
                + problem_if(len(dp.since(mark)[0]) != 1, "the peer served %d" % len(dp.since(mark)[0]))), ["_vet_host received %r (IDNA A-label, trailing dot kept); the SNI was %r -- the same name, the dot dropped as RFC 6066 section 3 requires" % (calls[0][0] if calls else None, snis)]
    run_row(suite, GO, "webfetch-split-vet-host-gets-the-normalised-host-of-the-sni", sni, src + "; R2-M7, group I url rows")

    def attrs():
        problems, seen = [], []
        for profile, want in ((None, "verified"), ("chrome", "chrome")):
            s = wf._create_session(profile)
            try:
                seen.append((profile, s.transport, s.allow_downgrade, s.tls12_fallback))
                problems += problem_if(s.transport != want or s.allow_downgrade is not True or s.tls12_fallback is not True or not callable(s.connect_policy),
                                       "profile %r: transport %r allow_downgrade %r tls12_fallback %r" % (profile, s.transport, s.allow_downgrade, s.tls12_fallback))
            finally:
                s.close()
        return problems, ["_create_session(profile) -> (profile, transport, allow_downgrade, tls12_fallback) %r" % seen]
    run_row(suite, GO, "webfetch-downgrade-create-session-flags-and-transport", attrs, src + "; R2-N3, D15")

    def follow():
        calls = []
        got = wf_fetch(wf, root, {"url": vurl + "/r/302?http://127.0.0.1:%d/echo" % s1p.port, "allow_private": True, "cache_ttl": 0},
                       swaps=((wf, "_vet_host", counting_vet(wf, calls)),))
        want = [("127.0.0.1", vp.port, True), ("127.0.0.1", s1p.port, True)]
        return (wf_report(got, "http://127.0.0.1:%d/echo" % s1p.port, 200, WF_CLEAR)
                + problem_if(calls != want, "_vet_host calls %r, wanted %r" % (calls, want))), ["https -> http 302 followed (allow_downgrade=True) and re-vetted; the chain label is cleartext"]
    run_row(suite, GO, "webfetch-downgrade-https-to-http-followed-and-re-vetted", follow, src + "; R2-N3, D16 S3")


def wf_header_rows(suite, wf, root, vp):
    """Caller-header refusals: before the SSRF guard, the cache read and any connection."""
    src = "Step 13 group O: _ch_check_caller_headers in handle_fetch before the guard and the cache (M5, R2-M4, R2-N1)"
    url = "https://127.0.0.1:%d/hdr" % vp.port
    cases = (
        ("user-agent-fixed-even-with-a-cache-entry", {"User-Agent": "x"}, "headers: user-agent is fixed by the Chrome profile"),
        ("crlf-injection", {"X-A": "v\r\nX-Injected: 1"}, "headers: 'X-A' contains CR, LF or NUL"),
        ("host-framing", {"Host": "evil.test"}, "headers: host is connection framing, owned by the Chrome profile"),
        ("content-length-framing", {"Content-Length": "0"}, "headers: content-length is connection framing, owned by the Chrome profile"),
        ("invalid-name", {"X A": "v"}, "headers: invalid header name 'X A'"),
    )
    for cid, headers, want in cases:
        def row(headers=headers, want=want):
            wf_put(wf, root, url, headers=headers, transport="verified", cert_verified=True)
            loads, guards = [], []
            mark = vp.mark()
            got = wf_fetch(wf, root, {"url": url, "allow_private": True, "headers": headers},
                           swaps=((wf, "_cache_load", recorder_of(loads, wf._cache_load)), (wf, "_check_host_allowed", recorder_of(guards, wf._check_host_allowed))))
            return (problem_if(wf_error(got) != want, "%s, wanted %r" % (short(got), want))
                    + problem_if(loads or guards or vp.since(mark)[1], "cache reads %d, guard calls %d, connections %d" % (len(loads), len(guards), vp.since(mark)[1]))), ["%r -> %r; a matching fresh cache entry exists, no cache read, no guard call, no connection" % (sorted(headers), want)]
        run_row(suite, GO, "webfetch-headers-%s-before-the-cache-and-any-connection" % cid, row, src)


def wf_label_rows(suite, wf, root, vp, dp, s1p, t12):
    """Every transport label, the status line and the title line checked apart (R2-M5, D15)."""
    src = "Step 13 group O: _format_response / _transport_label in Scripts/mcp-webfetch.py (S035: (cached) on the title line, the label on the status line)"
    vurl = "https://127.0.0.1:%d" % vp.port
    durl = "https://127.0.0.1:%d" % dp.port
    turl = "https://127.0.0.1:%d" % t12.port
    for srv in (vp, dp, s1p):
        srv.plan["/l"] = WF_HTML

    def fresh_default():
        got = wf_fetch(wf, root, {"url": vurl + "/l", "allow_private": True})
        _t, _l, nxt, _x = wf_parts(got)
        return wf_report(got, vurl + "/l", 200, WF_VERIFIED) + problem_if(nxt == WF_HINT, "a hint on a 200"), ["fresh default: %r, title line without a tag" % WF_VERIFIED]
    run_row(suite, GO, "webfetch-label-fresh-default-is-verified-no-tag", fresh_default, src)

    def chrome_pair():
        mark = dp.mark()
        first = wf_fetch(wf, root, {"url": durl + "/l", "allow_private": True, "profile": "chrome"})
        mid = dp.mark()
        second = wf_fetch(wf, root, {"url": durl + "/l", "allow_private": True, "profile": "chrome"})
        return {"first": first, "second": second, "seen": dp.since(mark)[0], "after": dp.since(mid)[1]}
    ex = outcome(chrome_pair)

    def need():
        if ex[0] == "exc":
            raise ex[1]
        return ex[1]

    def fresh_chrome():
        x = need()
        return (wf_report(x["first"], durl + "/l", 200, WF_CHROME)
                + problem_if([q["sid"] is not None for q in x["seen"]] != [True], "requests %r" % [(q["sid"], q["path"]) for q in x["seen"]])), ["fresh profile=chrome over h2: %r, no tag" % WF_CHROME]
    run_row(suite, GO, "webfetch-label-fresh-chrome-is-chrome-cert-not-verified-no-tag", fresh_chrome, src)

    def cached_chrome():
        x = need()
        return (wf_report(x["second"], durl + "/l", 200, WF_CHROME, "cached")
                + problem_if(x["after"], "the peer saw %d new connection(s)" % x["after"])), ["the same URL with profile=chrome within TTL: the Chrome-path answer from cache still says cert NOT verified, title (cached)"]
    run_row(suite, GO, "webfetch-label-cached-chrome-answer-still-cert-not-verified", cached_chrome, src)

    def reval(path, stored, want_label, want_disk):
        url = durl + path
        dp.plan[path] = (304, [("etag", "\"e1\"")], b"")
        wf_put(wf, root, url, age=WF_STALE_S, etag="\"e1\"", **stored)
        mark = dp.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True, "profile": "chrome"})
        seen, _conns = dp.since(mark)
        inm = [one(q, "if-none-match") for q in seen]
        disk = wf_disk(wf, root, url)
        return (wf_report(got, url, 200, want_label, "revalidated")
                + problem_if(inm != ["\"e1\""], "If-None-Match per request %r" % inm)
                + problem_if(disk != want_disk, "on-disk (transport, cert_verified) %r, wanted %r" % (disk, want_disk))), ["stale entry %r revalidated by a 304 over the Chrome path: %r, (revalidated), on disk now %r" % (stored, want_label, disk)]
    run_row(suite, GO, "webfetch-label-tls12-entry-revalidated-over-chrome-becomes-chrome",
            lambda: reval("/rv1", {"transport": "tls12-fallback", "cert_verified": True}, WF_CHROME, ("chrome", False)), src + "; the weaker label wins and is written back")
    run_row(suite, GO, "webfetch-label-pre-change-entry-revalidated-is-unknown-transport",
            lambda: reval("/rv2", {}, WF_UNKNOWN, (None, None)), src + "; an entry without the keys ranks lowest")

    def raw_cached():
        url = vurl + "/raw"
        wf_put(wf, root, url, transport="verified", cert_verified=True, body="<html>raw body</html>")
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True, "raw": True})
        text = got.get("__raw_text__") if isinstance(got, dict) else None
        return (problem_if(text != "<html>raw body</html>\n", "raw reply %r" % (text or got,))
                + problem_if(vp.since(mark)[1], "a connection was made")), ["raw=true on a cached answer: the body alone, no 'via' text"]
    run_row(suite, GO, "webfetch-label-raw-cached-shows-no-via", raw_cached, src + "; R2-M5")

    def t12_pair():
        first = wf_fetch(wf, root, {"url": turl + "/t12a", "allow_private": True, "profile": "chrome"})
        mark = t12.mark()
        second = wf_fetch(wf, root, {"url": turl + "/t12a", "allow_private": True, "profile": "chrome"})
        return {"first": first, "second": second, "after": t12.since(mark)}
    tx = outcome(t12_pair)

    def t12_need():
        if tx[0] == "exc":
            raise tx[1]
        return tx[1]

    def t12_chrome():
        x = t12_need()
        return wf_report(x["first"], turl + "/t12a", 200, WF_T12), ["profile=chrome to the TLS 1.2-only peer: served by the fallback, %r" % WF_T12]
    run_row(suite, GO, "webfetch-label-tls12-peer-under-chrome-is-tls12-fallback", t12_chrome, src + "; D10")

    def t12_cached():
        x = t12_need()
        reqs, accepts = x["after"]
        return (wf_report(x["second"], turl + "/t12a", 200, WF_T12, "cached")
                + problem_if(reqs or accepts, "the peer saw %d request(s)" % len(reqs))), ["the same URL with profile=chrome within TTL: the same label, (cached)"]
    run_row(suite, GO, "webfetch-label-tls12-fallback-cached-keeps-its-label", t12_cached, src)

    def t12_default():
        mark = t12.mark()
        got = wf_fetch(wf, root, {"url": turl + "/t12b", "allow_private": True, "cache_ttl": 0})
        reqs, accepts = t12.since(mark)
        return (wf_report(got, turl + "/t12b", 200, WF_VERIFIED)
                + problem_if(len(reqs) != 1 or len(accepts) != 1, "%d connection(s), %d request(s)" % (len(accepts), len(reqs)))), ["the default to the TLS 1.2-only peer: %r (one connection, no Chrome attempt)" % WF_VERIFIED]
    run_row(suite, GO, "webfetch-label-tls12-peer-under-the-default-is-verified", t12_default, src + "; D15")

    def clear():
        url = "http://127.0.0.1:%d/l" % s1p.port
        got = wf_fetch(wf, root, {"url": url, "allow_private": True, "cache_ttl": 0})
        return wf_report(got, url, 200, WF_CLEAR), ["an http:// loopback URL under the default: %r" % WF_CLEAR]
    run_row(suite, GO, "webfetch-label-http-url-is-cleartext", clear, src + "; D15")


def wf_admission_rows(suite, wf, root, vp, dp, s1p, t12):
    """Cache admission (D15) and the S3 chain-honesty rows (D16)."""
    src = "Step 13 group O: the admission rule after _cache_load in handle_fetch (D15 -> task-047; D16 S3)"
    vurl = "https://127.0.0.1:%d" % vp.port
    durl = "https://127.0.0.1:%d" % dp.port

    def fresh_chrome_entry():
        url = vurl + "/a1"
        vp.plan["/a1"] = WF_HTML
        wf_put(wf, root, url, transport="chrome", cert_verified=False)
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True})
        disk = wf_disk(wf, root, url)
        return (wf_report(got, url, 200, WF_VERIFIED)
                + problem_if(vp.since(mark)[1] != 1, "the verified peer saw %d connection(s)" % vp.since(mark)[1])
                + problem_if(disk != ("verified", True), "on disk %r" % (disk,))), ["a FRESH chrome entry and a default request: not served, one new verified connection, on disk now %r" % (disk,)]
    run_row(suite, GO, "webfetch-admission-fresh-chrome-entry-not-served-to-the-default", fresh_chrome_entry, src)

    def pre_change():
        problems, seen_inm = [], []
        for path, age, etag in (("/a2f", 0, "\"f1\""), ("/a2s", WF_STALE_S, "\"s1\"")):
            url = vurl + path
            vp.plan[path] = WF_HTML
            wf_put(wf, root, url, age=age, etag=etag)
            mark = vp.mark()
            got = wf_fetch(wf, root, {"url": url, "allow_private": True})
            seen, conns = vp.since(mark)
            inm = [one(q, "if-none-match") for q in seen]
            seen_inm.append(inm)
            problems += wf_report(got, url, 200, WF_VERIFIED)
            problems += problem_if(conns != 1 or inm != [None], "%s: %d connection(s), If-None-Match %r" % (path, conns, inm))
            problems += problem_if(wf_disk(wf, root, url) != ("verified", True), "%s: on disk %r" % (path, wf_disk(wf, root, url)))
        return problems, ["entries without transport/cert_verified (fresh, and stale with an ETag) and a default request: neither served nor revalidated (If-None-Match %r), overwritten verified/True" % seen_inm]
    run_row(suite, GO, "webfetch-admission-pre-change-entry-not-served-not-revalidated", pre_change, src)

    def verified_to_chrome():
        url = durl + "/a3"
        wf_put(wf, root, url, transport="verified", cert_verified=True)
        mark = dp.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True, "profile": "chrome"})
        return (wf_report(got, url, 200, WF_VERIFIED, "cached")
                + problem_if(dp.since(mark)[1], "a connection was made")), ["a fresh verified entry and a profile=chrome request: served (cached) with the entry's own label %r" % WF_VERIFIED]
    run_row(suite, GO, "webfetch-admission-verified-entry-served-to-chrome-with-its-own-label", verified_to_chrome, src)

    def cleartext_http():
        url = "http://127.0.0.1:%d/a4" % s1p.port
        wf_put(wf, root, url, transport="cleartext", cert_verified=False)
        mark = s1p.mark()
        got = wf_fetch(wf, root, {"url": url, "allow_private": True})
        return (wf_report(got, url, 200, WF_CLEAR, "cached")
                + problem_if(s1p.since(mark)[1], "a connection was made")), ["a cleartext entry for an http:// URL and a default request: served (cached), %r" % WF_CLEAR]
    run_row(suite, GO, "webfetch-admission-cleartext-entry-for-http-url-served-to-the-default", cleartext_http, src)

    def s3_downgrade():
        # Two downgraded entries under an https URL, each followed by a DEFAULT request within TTL.
        # (a) the plan's row: a profile=chrome https 302 -> http. The session's chain rule ranks chrome
        #     below cleartext (_ChSession: "chrome, then cleartext, then the hop's own label"), so the
        #     entry is chrome/False, not the cleartext/False the plan text names -- declared, asserted as built.
        # (b) the same downgrade on the DEFAULT transport stores cleartext/False under the https URL:
        #     admission clause (ii) must still refuse it, because the requested URL is https.
        problems, detail = [], []
        for path, profile, want_label, want_stored in (("/s3a", "chrome", WF_CHROME, ("chrome", False)), ("/s3c", None, WF_CLEAR, ("cleartext", False))):
            url = durl + path
            dp.plan[path] = (302, [("location", "http://127.0.0.1:%d/l" % s1p.port)], b"")
            params = {"url": url, "allow_private": True}
            if profile:
                params["profile"] = profile
            first = wf_fetch(wf, root, params)
            stored = wf_disk(wf, root, url)
            dp.plan[path] = WF_HTML
            mark, a0 = dp.mark(), len(dp.alpns)
            second = wf_fetch(wf, root, {"url": url, "allow_private": True})
            conns = dp.since(mark)[1]
            alpns = dp.alpns[a0:]
            after = wf_disk(wf, root, url)
            problems += [path + ": " + p for p in wf_report(first, "http://127.0.0.1:%d/l" % s1p.port, 200, want_label)]
            problems += problem_if(stored != want_stored, "%s: stored under the https URL %r, wanted %r" % (path, stored, want_stored))
            problems += [path + ": " + p for p in wf_report(second, url, 200, WF_VERIFIED)]
            problems += problem_if(conns != 1 or alpns != [None], "%s: the default made %d connection(s), ALPN %r" % (path, conns, alpns))
            problems += problem_if(after != ("verified", True), "%s: on disk after %r" % (path, after))
            detail.append("profile=%r https 302 -> http (followed) stored %r under the https URL; a later default request: not served, one new verified connection, on disk %r" % (profile, stored, after))
        detail.append("DEVIATION from the plan text (D16 S3 row 1): a chrome -> cleartext chain is labelled chrome, not cleartext (chrome is the weakest rank in the session)")
        return problems, detail
    run_row(suite, GO, "webfetch-s3-downgraded-entry-never-served-to-a-default-https-request", s3_downgrade, src + "; D16 S3 row 1")

    def s3_chain():
        url = durl + "/s3b"
        dp.plan["/s3b"] = (302, [("location", "https://127.0.0.1:%d/s3t" % t12.port)], b"")
        tmark = t12.mark()
        first = wf_fetch(wf, root, {"url": url, "allow_private": True, "profile": "chrome"})
        treqs, _ta = t12.since(tmark)
        stored = wf_disk(wf, root, url)
        mark, a0 = dp.mark(), len(dp.alpns)
        second = wf_fetch(wf, root, {"url": url, "allow_private": True})
        conns = dp.since(mark)[1]
        after = wf_disk(wf, root, url)
        final = "https://127.0.0.1:%d/s3t" % t12.port
        return (wf_report(first, final, 200, WF_CHROME)
                + problem_if(len(treqs) != 1, "the TLS 1.2 peer served %d" % len(treqs))
                + problem_if(stored != ("chrome", False), "stored %r" % (stored,))
                + wf_report(second, final, 200, WF_VERIFIED)
                + problem_if(conns != 1 or dp.alpns[a0:] != [None], "the default made %d connection(s), ALPN %r" % (conns, dp.alpns[a0:]))
                + problem_if(after != ("verified", True), "on disk after %r" % (after,))), ["profile=chrome h2 302 -> the TLS 1.2-only peer (the fallback): %r, stored %r; a later default request does not take it (on disk %r)" % (WF_CHROME, stored, after)]
    run_row(suite, GO, "webfetch-s3-chrome-then-fallback-chain-is-chrome-and-not-served-to-the-default", s3_chain, src + "; D16 S3 row 2")


def wf_optin_rows(suite, wf, root, vp, dp):
    """profile=chrome reaches the h2 peer and the default does not; the impersonate alias; the default verifies."""
    src = "Step 13 group O: _create_session's opt-in (D15 -> task-045/046)"
    vurl = "https://127.0.0.1:%d" % vp.port
    durl = "https://127.0.0.1:%d" % dp.port
    vp.plan["/o"] = WF_HTML
    dp.plan["/o"] = WF_HTML

    def reaches():
        news = []
        dmark, d0 = dp.mark(), len(dp.alpns)
        chrome = wf_fetch(wf, root, {"url": durl + "/o", "allow_private": True, "cache_ttl": 0, "profile": "chrome"}, news=news)
        dseen, _dc = dp.since(dmark)
        dalpns = dp.alpns[d0:]
        vmark, v0, dmark2 = vp.mark(), len(vp.alpns), dp.mark()
        default = wf_fetch(wf, root, {"url": vurl + "/o", "allow_private": True, "cache_ttl": 0}, news=news)
        vseen, _vc = vp.since(vmark)
        return (wf_report(chrome, durl + "/o", 200, WF_CHROME) + wf_report(default, vurl + "/o", 200, WF_VERIFIED)
                + problem_if(dalpns != ["h2"] or [q["sid"] is not None for q in dseen] != [True], "chrome: ALPN %r, requests %r" % (dalpns, [(q["sid"], q["path"]) for q in dseen]))
                + problem_if(vp.alpns[v0:] != [None] or len(vseen) != 1 or dp.since(dmark2)[1], "default: ALPN %r, %d request(s), h2 peer connections %d" % (vp.alpns[v0:], len(vseen), dp.since(dmark2)[1]))
                + problem_if([n.get("transport") for n in news] != ["chrome", "verified"], "transports %r" % [n.get("transport") for n in news])), ["profile=chrome: ALPN %r on the h2 peer; the default: the verified peer, ALPN %r, the h2 peer untouched" % (dalpns, vp.alpns[v0:])]
    run_row(suite, GO, "webfetch-optin-chrome-reaches-the-h2-peer-the-default-does-not", reaches, src)

    def alias():
        news = []
        original = wf._ch_session_new

        def session_new(*args, **kw):
            news.append(dict(kw))
            return original(*args, **kw)
        dmark = dp.mark()
        with Swapped((wf, "_ch_session_new", session_new)):
            got = wf.handle_webfetch_call({"function": "fetch", "params": {"url": durl + "/o", "impersonate": "chrome", "allow_private": True, "cache_ttl": 0}}, root, root)
        dseen, _dc = dp.since(dmark)
        return (wf_report(got, durl + "/o", 200, WF_CHROME)
                + problem_if([n.get("transport") for n in news] != ["chrome"] or [q["sid"] is not None for q in dseen] != [True], "transports %r, requests %r" % ([n.get("transport") for n in news], [(q["sid"], q["path"]) for q in dseen]))), ["webfetch_call fetch with impersonate='chrome' (the alias): transport chrome, the h2 peer answered, %r" % WF_CHROME]
    run_row(suite, GO, "webfetch-optin-impersonate-alias-behaves-as-profile-chrome", alias, src + "; PARAM_ALIASES")

    def verifies():
        news = []
        mark = vp.mark()
        got = wf_fetch(wf, root, {"url": vurl + "/o", "allow_private": True, "cache_ttl": 0}, trusted=False, news=news)
        err = wf_error(got) or ""
        seen, _conns = vp.since(mark)
        return (problem_if("verified: certificate verify failed" not in err or not err.startswith("fetch failed: ChromeClientError: verified: certificate verify failed: "), "%s" % short(got))
                + problem_if(seen, "the peer served %d request(s)" % len(seen))
                + problem_if(len(news) != 1 or "ssl_context_factory" in news[0], "kwargs %r" % news)), ["the default with production's own context (no factory) against the test-CA leaf: %r" % err[:120]]
    run_row(suite, GO, "webfetch-default-verifies-the-certificate", verifies, src + "; D15, D16 M1")


def wf_status_row(suite, wf, root):
    src = "Step 13 group O: handle_webfetch_call with no function (D15 -> task-048)"

    def status():
        finds, cdlls = [], []
        before = (dict(wf._BR_STATE), dict(wf._ZSTD_STATE))
        with Swapped((ctypes.util, "find_library", recorder_of(finds, ctypes.util.find_library)), (ctypes, "CDLL", recorder_of(cdlls, ctypes.CDLL))):
            got = wf.handle_webfetch_call({}, root, root)
        text = got.get("__raw_text__") or ""
        lines = text.split("\n")
        backend = [ln for ln in lines if ln.startswith("Default transport: verified (")]
        decoders = [ln for ln in lines if ln.startswith("br: ") or ln.startswith("zstd: ")]
        return (problem_if(len(backend) != 1 or "profile=chrome" not in backend[0] or "certificate NOT verified" not in backend[0] or "certificates verified" not in backend[0], "backend line %r" % backend)
                + problem_if(len(decoders) != 2, "decoder lines %r" % decoders)
                + problem_if(before != ({}, {}) or wf._BR_STATE != {} or wf._ZSTD_STATE != {}, "_BR_STATE %r _ZSTD_STATE %r (before %r)" % (wf._BR_STATE, wf._ZSTD_STATE, before))
                + problem_if(finds or cdlls, "find_library %r CDLL %r during the status reply" % (finds, cdlls))), ["%s" % (backend[0][:150] if backend else None)] + decoders
    run_row(suite, GO, "webfetch-status-reply-names-the-verified-default-and-loads-no-decoder", status, src + "; M1")


def wf_hardening_rows(suite, wf, root, vp):
    """Security review 20261001-082224 fix pass: F1 (cache vs allow_private), F4 (description), F39 (cache modes), F41 (printable URL echoes)."""
    src = "Step 13 group O: security review 20261001-082224 fix pass in Scripts/mcp-webfetch.py"
    vurl = "https://127.0.0.1:%d" % vp.port
    vp.plan["/ap"] = WF_HTML
    no_guard = (wf, "_check_host_allowed", lambda _url, _allow: None)

    def private_entry():
        # The requested host passes the pre-cache guard (patched to None: the "public page that
        # redirected into loopback" case), so only the cache key/admission can keep the
        # allow_private=true body from the default call. Pre-fix the key ignored the flag and
        # the second call was served (cached).
        url = vurl + "/ap"
        first = wf_fetch(wf, root, {"url": url, "allow_private": True})
        on_true, on_false = wf_disk(wf, root, url), wf_disk(wf, root, url, allow_private=False)
        stored = wf._cache_load(root, wf_key(wf, url)) or {}
        mark = vp.mark()
        second = wf_fetch(wf, root, {"url": url}, swaps=(no_guard,))
        err = wf_error(second) or ""
        return (wf_report(first, url, 200, WF_VERIFIED)
                + problem_if(on_true != ("verified", True) or stored.get("allow_private") is not True, "allow_private=true entry %r, flag %r" % (on_true, stored.get("allow_private")))
                + problem_if(on_false is not None, "an entry exists under the allow_private=false key: %r" % (on_false,))
                + problem_if(not err.startswith("fetch failed: ") or "policy: " not in err, "the default call was not refused by the policy: %s" % short(second))
                + problem_if(vp.since(mark)[1], "the default call connected")), ["allow_private=true fetch cached under its own key (flag stored); a default call for the same URL past the guard missed the cache and was refused by the connect policy: %r" % err[:120]]
    run_row(suite, GO, "webfetch-cache-allow-private-entry-not-served-to-a-default-call", private_entry, src + "; F1")

    def flag_mismatch():
        problems, detail = [], []
        for cid, flag in (("flag-true-under-the-default-key", True), ("flag-missing-under-the-default-key", None)):
            url = vurl + "/apm-" + cid
            wf_put(wf, root, url, key_private=False, allow_private=flag, transport="verified", cert_verified=True)
            mark = vp.mark()
            got = wf_fetch(wf, root, {"url": url}, swaps=(no_guard,))
            title, _l, _n, _t = wf_parts(got)
            problems += problem_if(title is not None or "policy: " not in (wf_error(got) or ""), "%s: served or not refused: %s" % (cid, short(got)))
            problems += problem_if(vp.since(mark)[1], "%s: the default call connected" % cid)
            detail.append("%s: %r" % (cid, (wf_error(got) or "")[:80]))
        return problems, ["an entry under the allow_private=false key whose own flag is True, or absent, is not served to a default call"] + detail
    run_row(suite, GO, "webfetch-cache-allow-private-flag-mismatch-or-missing-not-served", flag_mismatch, src + "; F1 defence in depth")

    def modes():
        sub = os.path.join(root, "f39")
        wf._cache_save(sub, "a" * 64, {"fetched_at": 0})
        cache_dir = wf._cache_dir(sub)
        dmode = os.stat(cache_dir).st_mode & 0o777
        fmode = os.stat(wf._cache_path(sub, "a" * 64)).st_mode & 0o777
        leftovers = [n for n in os.listdir(cache_dir) if n.endswith(".tmp")]
        return (problem_if(dmode != 0o700, "cache dir mode %o" % dmode)
                + problem_if(fmode != 0o600, "entry mode %o" % fmode)
                + problem_if(leftovers, "tmp files left %r" % leftovers)), ["a fresh cache dir is %o, a new entry %o, no tmp left behind" % (dmode, fmode)]
    run_row(suite, GO, "webfetch-cache-dir-0700-entry-0600", modes, src + "; F39")

    def printable():
        raw = "a\x00\x1b\x1f\x7f\x80\x9f`z\xa0\xe9"
        want = "a\\x00\\x1b\\x1f\\x7f\\x80\\x9f\\x60z\xa0\xe9"
        once = wf._printable(raw)
        return (problem_if(once != want, "%r, wanted %r" % (once, want))
                + problem_if(wf._printable(once) != once, "a second pass changed it")), ["%r -> %r (idempotent)" % (raw, once)]
    run_row(suite, GO, "webfetch-printable-escapes-c0-del-c1-and-backtick", printable, src + "; F41")

    def title():
        vp.plan["/esc"] = (302, [("location", "/esc2?a=\x1b`b")], b"")
        got = wf_fetch(wf, root, {"url": vurl + "/esc", "allow_private": True, "cache_ttl": 0})
        _t, _l, _n, text = wf_parts(got)
        return (wf_report(got, vurl + "/esc2?a=\\x1b\\x60b", 200, WF_VERIFIED)
                + problem_if(text is None or "\x1b" in text, "an ESC reached the report")), ["302 Location carrying ESC and a backtick: the title line shows them as \\x1b / \\x60"]
    run_row(suite, GO, "webfetch-final-url-esc-and-backtick-escaped-in-the-title", title, src + "; F41")

    def refusal():
        vp.plan["/escpng"] = (302, [("location", "/escimg?a=\x1b`b")], b"")
        vp.plan["/escimg"] = (200, [("content-type", "image/png")], b"\x89PNG")
        got = wf_fetch(wf, root, {"url": vurl + "/escpng", "allow_private": True, "cache_ttl": 0})
        want = ("refusing to convert non-textual content-type 'image/png' from %s/escimg?a=\\x1b\\x60b (status 200). resp.text on a binary "
                "body yields replacement-character mojibake, not content." % vurl)
        return problem_if(wf_error(got) != want, "%s, wanted %r" % (short(got), want)), ["the non-textual refusal after the same redirect: %r" % (wf_error(got) or "")[:120]]
    run_row(suite, GO, "webfetch-final-url-esc-and-backtick-escaped-in-the-refusal", refusal, src + "; F41")

    def description():
        text = wf.WEBFETCH_CALL_TOOL["description"]
        want = "true applies to the WHOLE redirect chain"
        return (problem_if(want not in text, "the description does not say %r" % want)
                + problem_if("Browser-emulated" in text, "the description still opens with Browser-emulated")), ["the allow_private entry names the whole redirect chain; the opener no longer claims browser emulation"]
    run_row(suite, GO, "webfetch-description-allow-private-names-the-whole-redirect-chain", description, src + "; F4")

    def header_block():
        # (a) white-box: a header name and value carrying ESC and a ``` run, rendered with show_headers.
        view = {"output": "html", "max_answer_chars": 0, "offset": 0, "raw": False, "show_headers": True,
                "save_to": "", "overwrite": False, "project_root": root}
        got = wf._format_response(200, vurl + "/h", {"x-ev\x1bil": "a\x1b```b", "etag": "\"e```1\""}, "<p>x</p>", view, None, "")
        text = (got or {}).get("__raw_text__") or ""
        block = text.split("## Response headers (all)\n\n", 1)[-1].split("\n\n## Body", 1)[0]
        inner = block[4:-4] if block.startswith("```\n") and block.endswith("\n```") else None
        want_lines = ["  etag: \"e\\x60\\x60\\x601\"", "  x-ev\\x1bil: a\\x1b\\x60\\x60\\x60b"]
        # (b) end to end: an ETag with a ``` run renders escaped, and the stored entry keeps the raw bytes for If-None-Match.
        url = vurl + "/hdr-etag"
        vp.plan["/hdr-etag"] = (200, [("content-type", "text/html; charset=utf-8"), ("etag", "\"t```1\"")], b"<html>e</html>")
        fresh = wf_fetch(wf, root, {"url": url, "allow_private": True})
        ftext = (fresh or {}).get("__raw_text__") or ""
        stored = (wf._cache_load(root, wf_key(wf, url)) or {}).get("etag")
        return (problem_if(inner is None or inner.split("\n") != want_lines, "header block %r" % block)
                + problem_if("\x1b" in text or text.count("```") != 2, "an ESC or an extra ``` in the report")
                + problem_if("  etag: \"t\\x60\\x60\\x601\"" not in ftext or ftext.count("```") != 2, "fresh report header block %r" % ftext[:400])
                + problem_if(stored != "\"t```1\"", "stored etag %r, wanted the raw bytes" % (stored,))), ["rendered %r; the stored etag stays raw %r" % (inner, stored)]
    run_row(suite, GO, "webfetch-header-block-esc-and-backtick-fence-escaped", header_block, src + "; F22b")


def wf_uv_row(suite, s1p, ws):
    cid = "webfetch-uv-stdio-test-cli-against-a-loopback-url"
    uv = shutil.which("uv")
    if uv is None:
        skip(suite, GO, cid, "uv not on PATH")
        return
    s1p.plan["/uv"] = (200, [("content-type", "text/html; charset=utf-8")], b"<html><body><h1>uv row</h1><p>hello from loopback</p></body></html>")
    url = "http://127.0.0.1:%d/uv" % s1p.port
    mark = s1p.mark()
    got = outcome(lambda: H.run_process([uv, "run", "--script", WF_HOST, "--test", url, "--allow-private", "--no-cache", "--project-root", ws, "--cache-root", ws], timeout=WF_UV_TIMEOUT, cwd=ws))
    seen, _conns = s1p.since(mark)
    if got[0] == "exc":
        suite.record(GO, cid, ["raised %s(%r)" % (type(got[1]).__name__, str(got[1])[:200])])
        return
    rc, out, err = got[1]
    if rc != 0 and not seen and not out.strip() and err.strip().startswith("error:"):
        skip(suite, GO, cid, "uv could not prepare the script environment: %s" % err.strip().splitlines()[0][:160])
        return
    status = [ln for ln in out.splitlines() if ln.startswith("**status**:")]
    suite.record(GO, cid, problem_if(rc != 0 or status != ["**status**: 200 " + WF_CLEAR] or "uv row" not in out or len(seen) != 1,
                                     "rc %d, status %r, %d request(s), stderr %r" % (rc, status, len(seen), err[-300:])),
                 detail=["uv run --script Scripts/mcp-webfetch.py --test %s --allow-private --no-cache: rc %d, %r (the real bs4/markdownify)" % (url, rc, status)])


def wf_hygiene_rows(suite, wf, originals, gai):
    moved = [name for name in WF_SWAPPED if getattr(wf, name) is not originals[name]]
    suite.record(GO, "hygiene-webfetch-swapped-names-are-the-originals-after-the-group",
                 problem_if(moved, "not restored: %r" % moved) + problem_if(socket.getaddrinfo is not gai, "socket.getaddrinfo is %r" % socket.getaddrinfo),
                 detail=["`is` identity with the objects captured before any swap: %s, socket.getaddrinfo" % ", ".join(WF_SWAPPED)])
    stubs = [name for name in WF_STUBBED
             if getattr(sys.modules.get(name), "BeautifulSoup", None) is WfStubSoup or getattr(sys.modules.get(name), "markdownify", None) is wf_stub_markdownify]
    suite.record(GO, "hygiene-webfetch-stub-modules-gone-from-sys-modules",
                 problem_if(stubs or wf.BeautifulSoup is not WfStubSoup or wf.markdownify is not wf_stub_markdownify, "stubs still in sys.modules %r, or the module was not loaded with them" % stubs),
                 detail=["bs4 / markdownify stubs were in sys.modules for the load only; the module kept its own references"])


def group_webfetch(suite, cc):
    """Group O: Scripts/mcp-webfetch.py's handle_fetch over loopback (D15, D16 M1/S3)."""
    gai = socket.getaddrinfo
    wf = load_webfetch()
    originals = dict((name, getattr(wf, name)) for name in WF_SWAPPED)
    ws = H.TempWorkspace("ph-webfetch-")
    vp = WebfetchPeer(cc)
    vp2 = WebfetchPeer(cc)
    dp = DualPeer(cc)
    s1p = SessionPeer("h1-plain", cc)
    t12 = Tls12Peer()
    # Every call below names its cache directory; HOME and XDG_CACHE_HOME are
    # pinned into the sandbox as well, so a call that forgot cannot reach the
    # real default cache (R-0054: $XDG_CACHE_HOME/web-fetch or ~/.cache/web-fetch).
    saved_env = dict((k, os.environ.get(k)) for k in ("HOME", "XDG_CACHE_HOME"))

    def restore_env():
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    try:
        os.environ["HOME"] = ws.subdir("home")
        os.environ["XDG_CACHE_HOME"] = ws.subdir("xdg-cache")
        root = ws.path
        wf_basic_rows(suite, wf, root, vp, vp2, dp)
        wf_policy_rows(suite, wf, root, vp, vp2, dp, s1p)
        wf_header_rows(suite, wf, root, vp)
        wf_label_rows(suite, wf, root, vp, dp, s1p, t12)
        wf_admission_rows(suite, wf, root, vp, dp, s1p, t12)
        wf_optin_rows(suite, wf, root, vp, dp)
        wf_status_row(suite, wf, root)
        wf_hardening_rows(suite, wf, root, vp)
        # Restored first: the uv child needs the real HOME for uv's own cache,
        # and it is handed --cache-root explicitly.
        restore_env()
        wf_uv_row(suite, s1p, root)
    finally:
        for srv in (vp, vp2, dp, s1p, t12):
            srv.close()
        restore_env()
        ws.cleanup()
    wf_hygiene_rows(suite, wf, originals, gai)


# --- N. negative control --------------------------------------------------------

TAG_NAMES = frozenset(("received", "tag", "_tag", "expected_tag", "want_tag"))


def tag_compare_problems(tree, classes):
    """Every AEAD's `open` goes through hmac.compare_digest; no ==/!=/is compares a tag."""
    problems = []
    seen = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in classes:
            continue
        seen.add(node.name)
        opens = [f for f in node.body if isinstance(f, ast.FunctionDef) and f.name == "open"]
        if len(opens) != 1:
            problems.append("%s: %d open() methods" % (node.name, len(opens)))
            continue
        digests = [n for n in ast.walk(opens[0]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "compare_digest" and isinstance(n.func.value, ast.Name) and n.func.value.id == "hmac"]
        if len(digests) != 1:
            problems.append("%s.open: %d hmac.compare_digest call(s), wanted exactly 1" % (node.name, len(digests)))
        for n in ast.walk(node):
            if isinstance(n, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)) for op in n.ops):
                names = set(x.id for x in ast.walk(n) if isinstance(x, ast.Name)) | set(x.attr for x in ast.walk(n) if isinstance(x, ast.Attribute))
                if names & TAG_NAMES:
                    problems.append("%s line %d: a tag is compared with ==/!=/is" % (node.name, n.lineno))
    missing = sorted(set(classes) - seen)
    if missing:
        problems.append("class(es) not found: %r" % missing)
    return problems


SYNTH_AEAD_BAD = (
    "import hmac\n\n\n"
    "class _ChAesGcm:\n"
    "    def open(self, nonce, aad, data):\n"
    "        received = data[-16:]\n"
    "        if self._tag(nonce, aad, data[:-16]) != received:\n"
    "            raise ValueError('bad')\n"
    "        return hmac.compare_digest(b'', b'')\n"
)


def group_negative(suite, mod, source_tree):
    CE = mod.ChromeClientError
    for tc, key, iv, aad, ct, tag in GCM_INVALID:
        result = outcome(lambda: mod._ChAesGcm(h(key)).open(h(iv), h(aad), h(ct) + h(tag)))
        suite.record(GN, "wycheproof-aes-gcm-tc%d-refused" % tc, expect_refusal(result, CE, "tls: bad record MAC"),
                     detail=["Wycheproof aes_gcm_test.json tcId %d, result invalid (ModifiedTag)" % tc])

    cc = mod._ChChaCha20Poly1305
    key282 = h(AEAD_282_KEY)
    nonce282 = h(AEAD_282_FIXED) + h(AEAD_282_IV)
    sealed = h(AEAD_282_CT) + h(AEAD_282_TAG)
    for cid, pos in (("tag-first-byte", len(sealed) - 16), ("tag-last-byte", len(sealed) - 1), ("ciphertext-first-byte", 0)):
        bad = bytearray(sealed)
        bad[pos] ^= 0x01
        result = outcome(lambda: cc(key282).open(nonce282, h(AEAD_282_AAD), bytes(bad)))
        suite.record(GN, "chacha20poly1305-flipped-%s-refused" % cid, expect_refusal(result, CE, "tls: bad record MAC"),
                     detail=["RFC 8439 section 2.8.2 vector with one bit flipped at byte %d" % pos])
    bad_aad = bytearray(h(AEAD_282_AAD))
    bad_aad[0] ^= 0x01
    suite.record(GN, "chacha20poly1305-flipped-aad-refused", expect_refusal(outcome(lambda: cc(key282).open(nonce282, bytes(bad_aad), sealed)), CE, "tls: bad record MAC"),
                 detail=["RFC 8439 section 2.8.2 vector, one AAD bit flipped"])

    gcm128 = GCM_VALID[0]
    good = h(gcm128[5]) + h(gcm128[6])
    flipped = good[:-1] + bytes([good[-1] ^ 0x80])
    suite.record(GN, "aes-gcm-flipped-tag-of-valid-vector-refused", expect_refusal(outcome(lambda: mod._ChAesGcm(h(gcm128[1])).open(h(gcm128[2]), h(gcm128[3]), flipped)), CE, "tls: bad record MAC"),
                 detail=["Wycheproof tcId 1 with the top bit of the last tag byte flipped"])

    aead_rows = (
        ("aes-gcm-short-input", lambda: mod._ChAesGcm(bytes(16)).open(bytes(12), b"", bytes(15)), "tls: AEAD input shorter than its tag"),
        ("aes-gcm-nonce-11", lambda: mod._ChAesGcm(bytes(16)).seal(bytes(11), b"", b""), "tls: invalid AES-GCM nonce length"),
        ("aes-gcm-key-20", lambda: mod._ChAesGcm(bytes(20)), "tls: invalid AES-GCM key length"),
        ("aes-block-15", lambda: mod._ChAesGcm(bytes(16)).encrypt_block(bytes(15)), "tls: AES block must be 16 bytes"),
        ("chacha-short-input", lambda: cc(bytes(32)).open(bytes(12), b"", bytes(15)), "tls: AEAD input shorter than its tag"),
        ("chacha-nonce-13", lambda: cc(bytes(32)).seal(bytes(13), b"", b""), "tls: invalid ChaCha20-Poly1305 nonce length"),
        ("chacha-key-16", lambda: cc(bytes(16)), "tls: invalid ChaCha20-Poly1305 key length"),
    )
    for cid, fn, message in aead_rows:
        suite.record(GN, "%s-refused" % cid, expect_refusal(outcome(fn), CE, message))

    # RFC 7748 section 6.1 lets a protocol refuse the all-zero output; RFC 8446
    # 7.4.2 says a TLS 1.3 client MUST. u = 0 and u = 1 are small-order points.
    scalar = h(X25519_61_A)
    for cid, u in (("u-zero", bytes(32)), ("u-one", b"\x01" + bytes(31))):
        suite.record(GN, "x25519-all-zero-shared-%s-refused" % cid, expect_refusal(outcome(lambda: mod._ch_x25519(scalar, u)), CE, "tls: X25519 shared secret is all zero"),
                     detail=["small-order peer point; RFC 7748 section 6.1 / RFC 8446 section 7.4.2"])
    suite.record(GN, "x25519-31-byte-share-refused", expect_refusal(outcome(lambda: mod._ch_x25519(scalar, bytes(31))), CE, "tls: invalid X25519 key share"))

    i_int = int.from_bytes(h(P256_I), "big")
    gx, gy = h(P256_GRX), h(P256_GRY)
    y_plus = (int.from_bytes(gy, "big") + 1).to_bytes(32, "big")
    p_bytes = mod._CH_P256_P.to_bytes(32, "big")
    p256_rows = (
        ("off-curve-y-plus-one", b"\x04" + gx + y_plus, "tls: invalid P-256 key share"),
        ("all-zero-point", b"\x04" + bytes(64), "tls: invalid P-256 key share"),
        ("compressed-form", b"\x02" + gx, "tls: invalid P-256 key share"),
        ("64-byte-share", gx + gy, "tls: invalid P-256 key share"),
        ("x-equal-to-p", b"\x04" + p_bytes + gy, "tls: invalid P-256 key share"),
    )
    for cid, share, message in p256_rows:
        suite.record(GN, "p256-%s-refused" % cid, expect_refusal(outcome(lambda: mod._ch_p256_shared(i_int, share)), CE, message),
                     detail=["RFC 5903 8.1 g^r altered; SP 800-56A 5.6.2.3.4 validation"])
    suite.record(GN, "p256-private-zero-refused", expect_refusal(outcome(lambda: mod._ch_p256_shared(0, b"\x04" + gx + gy)), CE, "tls: invalid P-256 private key"))

    kem = mod._ChMlKem768()
    ek, dk = kem.keygen_internal(bytes(32), bytes(range(32)))
    ct, _ss = kem.encaps_internal(ek, bytes(range(32)))
    mlkem_rows = (
        ("ek-1183", lambda: kem.encaps(ek[:-1]), "tls: invalid ML-KEM-768 encapsulation key"),
        ("ek-coefficient-over-q", lambda: kem.encaps(b"\xff\xff\xff" + ek[3:]), "tls: invalid ML-KEM-768 encapsulation key"),
        ("dk-2401", lambda: kem.decaps(dk + b"\x00", ct), "tls: invalid ML-KEM-768 decapsulation key"),
        ("dk-hash-check", lambda: kem.decaps(dk[:1152] + bytes([dk[1152] ^ 1]) + dk[1153:], ct), "tls: invalid ML-KEM-768 decapsulation key"),
        ("ct-1087", lambda: kem.decaps(dk, ct[:-1]), "tls: invalid ML-KEM-768 ciphertext"),
        ("seed-31", lambda: kem.keygen_internal(bytes(31), bytes(32)), "tls: invalid ML-KEM-768 seed"),
    )
    for cid, fn, message in mlkem_rows:
        suite.record(GN, "mlkem768-%s-refused" % cid, expect_refusal(outcome(fn), CE, message),
                     detail=["FIPS 203 sections 7.2/7.3 input checks"])

    sha = hashlib.sha256
    hkdf_rows = (
        ("hkdf-expand-over-255-hashlen", lambda: mod._ch_hkdf_expand(sha, bytes(32), b"", 255 * 32 + 1), "tls: HKDF output length out of range"),
        ("derive-secret-short-hash", lambda: mod._ch_derive_secret(sha, bytes(32), b"derived", bytes(5)), "tls: transcript hash length mismatch"),
        ("expand-label-over-255", lambda: mod._ch_hkdf_expand_label(sha, bytes(32), b"x" * 250, b"", 32), "tls: invalid HKDF label"),
    )
    for cid, fn, message in hkdf_rows:
        suite.record(GN, "%s-refused" % cid, expect_refusal(outcome(fn), CE, message),
                     detail=["RFC 5869 section 2.3 / RFC 8446 section 7.1 bounds"])
    kat_ok = outcome(lambda: len(mod._ch_hkdf_expand(sha, bytes(32), b"", 255 * 32)))
    suite.record(GN, "hkdf-expand-at-255-hashlen-accepted", expect_value(kat_ok, 255 * 32),
                 detail=["the boundary itself: 255 * HashLen is the RFC's maximum, not a refusal"])

    aead_classes = ("_ChAesGcm", "_ChChaCha20Poly1305")
    suite.record(GN, "ast-tag-compare-only-via-hmac-compare-digest", tag_compare_problems(source_tree, aead_classes),
                 detail=["SEC-LOW: open() in %s" % ", ".join(aead_classes)])
    control = tag_compare_problems(ast.parse(SYNTH_AEAD_BAD), ("_ChAesGcm",))
    suite.record(GN, "control-ast-tag-compare-catches-planted-ne",
                 problem_if(not any("compared with ==/!=/is" in p for p in control), "the planted `!= received` was not reported: %r" % control),
                 detail=["planted: `if self._tag(...) != received`"])


# --- K. structure ---------------------------------------------------------------

RULES = ("shapes", "duplicates", "prefixes", "exceptions", "tab-safe", "free-names", "no-assert")


def structure_problems(gen, label, text, prefixes, exceptions):
    """rule -> problems for one source text. Every rule is always present.

    The same rule set as tests/test_mcp_decoders.py group K. A module-level loop
    (the R-0017 PoC's `_HUFF_DEC` fill) is not a generator block shape, so the
    `shapes` rule is the one that refuses it.
    """
    rules = dict((r, []) for r in RULES)
    tree = ast.parse(text)
    blocks = gen.load_blocks_text(label, text)
    imports = set()
    shaped = []
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.add((alias.asname or alias.name).split(".")[0])
            continue
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shaped.append(node.name)
            continue
        name = gen.assign_name(node)
        if name is None:
            rules["shapes"].append("line %d: a %s is not a generator block shape" % (node.lineno, type(node).__name__))
            continue
        shaped.append(name)
    dupes = sorted(set(n for n in shaped if shaped.count(n) > 1))
    if dupes or len(shaped) != len(blocks):
        rules["duplicates"].append("%d block-shaped statements, %d blocks; duplicated: %r" % (len(shaped), len(blocks), dupes))
    stray = sorted(n for n in blocks if not n.startswith(tuple(prefixes)) and n not in exceptions)
    if stray:
        rules["prefixes"].append("undeclared prefix: %r" % stray)
    gone = sorted(n for n in exceptions if n not in blocks)
    if gone:
        rules["exceptions"].append("declared exception no longer exists: %r" % gone)
    unsafe = sorted(n for n, b in blocks.items() if not gen.block_is_tab_safe(b))
    if unsafe:
        rules["tab-safe"].append("not tab safe: %r" % unsafe)
    allowed = imports | set(blocks)
    leaks = dict((n, sorted(gen.free_names(b) - allowed)) for n, b in blocks.items() if gen.free_names(b) - allowed)
    if leaks:
        rules["free-names"].append("reads names outside its imports and blocks: %r" % leaks)
    asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if asserts:
        rules["no-assert"].append("assert at line(s) %r: python3 -O strips it" % asserts)
    return rules, len(blocks)


SYNTH_OK = "\"\"\"doc.\"\"\"\n\nimport os\n\n_CH_X = 1\n\n\ndef _ch_f():\n    return os.sep\n"

SYNTH_CONTROLS = (
    ("assert", SYNTH_OK + "\n\ndef _ch_g(x):\n    assert x\n    return x\n", (), "no-assert"),
    ("module-level-loop", SYNTH_OK + "\n_CH_T = []\nfor _i in range(4):\n    _CH_T.append(_i)\n", (), "shapes"),
    ("stray-statement", SYNTH_OK + "\nif os.sep:\n    _CH_Y = 2\n", (), "shapes"),
    ("bad-prefix", SYNTH_OK + "\n\ndef helper():\n    return 1\n", (), "prefixes"),
    ("duplicate-name", SYNTH_OK + "\n_CH_X = 2\n", (), "duplicates"),
    ("free-name-leak", SYNTH_OK + "\n\ndef _ch_h():\n    return json.dumps(1)\n", (), "free-names"),
    ("tab-unsafe", SYNTH_OK + "\n_CH_U = (1,\n         2)\n", (), "tab-safe"),
    ("vanished-exception", SYNTH_OK, ("ChromeGone",), "exceptions"),
)


def group_structure(suite, text):
    gen = H.load_module_from_path("amalgamate_under_test", GENERATOR)
    rules, count = structure_problems(gen, os.path.basename(SOURCE), text, PREFIXES, EXCEPTIONS)
    for rule in RULES:
        suite.record(GK, "chrome-%s" % rule, rules[rule],
                     detail=["%d generator blocks in Scripts/_mcp_chrome.py" % count] if rule == "duplicates" else ())

    rules, _count = structure_problems(gen, "synthetic.py", SYNTH_OK, PREFIXES, ())
    fired = sorted(r for r, p in rules.items() if p)
    suite.record(GK, "control-clean-synthetic-accepted", problem_if(fired, "a clean source tripped %r" % fired))
    for cid, synth, exceptions, rule in SYNTH_CONTROLS:
        rules, _count = structure_problems(gen, "synthetic.py", synth, PREFIXES, exceptions)
        fired = sorted(r for r, p in rules.items() if p)
        suite.record(GK, "control-%s-refused" % cid, problem_if(fired != [rule], "fired %r, wanted exactly [%r]" % (fired, rule)),
                     detail=["planted defect caught by rule %s" % rule])


# --- L. throughput + profile age ----------------------------------------------------

def mib_per_s(nbytes, seconds):
    return nbytes / seconds / 1048576.0 if seconds > 0 else float("inf")


def group_throughput(suite, mod):
    record = bytes((31 * i + 7) & 0xFF for i in range(RECORD_BYTES))
    aad = b"\x17\x03\x03\x40\x11"
    nonce = bytes(range(12))
    reps = THROUGHPUT_BYTES // RECORD_BYTES
    rows = (
        ("aes-128-gcm", lambda: mod._ChAesGcm(bytes(range(16))), False),
        ("aes-256-gcm", lambda: mod._ChAesGcm(bytes(range(32))), True),
        ("chacha20-poly1305", lambda: mod._ChChaCha20Poly1305(bytes(range(32))), False),
    )
    for name, make, gated in rows:
        cid = "%s-decrypt-1mib" % name
        t0 = time.perf_counter()
        aead = make()
        t_init = time.perf_counter() - t0
        t0 = time.perf_counter()
        sealed = aead.seal(nonce, aad, record)
        t_seal = time.perf_counter() - t0
        t0 = time.perf_counter()
        wrong = 0
        for _ in range(reps):
            if aead.open(nonce, aad, sealed) != record:
                wrong += 1
        t_open = time.perf_counter() - t0
        rate = mib_per_s(reps * RECORD_BYTES, t_open)
        detail = ["decrypt %d x %d B in %.2f s = %.3f MiB/s; seal one record %.1f ms (%.3f MiB/s); key setup %.2f ms; python %s"
                  % (reps, RECORD_BYTES, t_open, rate, t_seal * 1e3, mib_per_s(RECORD_BYTES, t_seal), t_init * 1e3, sys.version.split()[0])]
        if gated:
            # Three bands (see AES256_DECRYPT_FLOOR_MIB_S): only the catastrophic
            # one and a wrong plaintext can FAIL; a load-shaped shortfall is INFO.
            fail_below = AES256_DECRYPT_FLOOR_MIB_S / CATASTROPHE_FACTOR
            problems = problem_if(rate < fail_below, "%.3f MiB/s is below a tenth of the G3 floor (%.3f MiB/s): an algorithmic regression, not load"
                                  % (rate, fail_below))
            problems += problem_if(wrong, "%d of %d opens returned the wrong plaintext" % (wrong, reps))
            band = ["G3 floor %.1f MiB/s; FAIL only below %.3f MiB/s (the only group-L row that can FAIL)" % (AES256_DECRYPT_FLOOR_MIB_S, fail_below)]
            status = None
            if not problems and rate < AES256_DECRYPT_FLOOR_MIB_S:
                status = H.INFO
                band.append("measured %.3f MiB/s is under the %.1f MiB/s floor but within %gx of it: a wall-clock rate that flaps with host load "
                            "(re-run alone to judge the floor), so INFO, not FAIL" % (rate, AES256_DECRYPT_FLOOR_MIB_S, CATASTROPHE_FACTOR))
            verdict = "FAIL" if problems else ("INFO" if status else "PASS")
            suite.record(GL, cid, problems, status=status, detail=detail + band,
                         brief="%s | %s %.3f MiB/s" % (verdict, cid, rate))
        else:
            status = H.INFO if not wrong else None
            suite.record(GL, cid, problem_if(wrong, "%d of %d opens returned the wrong plaintext" % (wrong, reps)), status=status, detail=detail,
                         brief="INFO | %s %.3f MiB/s" % (cid, rate))

    kem = mod._ChMlKem768()
    n = 3
    t0 = time.perf_counter()
    pairs = [kem.keygen() for _ in range(n)]
    t_kg = (time.perf_counter() - t0) / n
    cts = [kem.encaps(p[0]) for p in pairs]
    t0 = time.perf_counter()
    for p, c in zip(pairs, cts):
        kem.decaps(p[1], c[0])
    t_dec = (time.perf_counter() - t0) / n
    suite.record(GL, "mlkem768-keygen-plus-decaps", (), status=H.INFO,
                 detail=["keygen %.1f ms + decaps %.1f ms = %.1f ms per handshake (mean of %d)" % (t_kg * 1e3, t_dec * 1e3, (t_kg + t_dec) * 1e3, n)],
                 brief="INFO | mlkem768-keygen-plus-decaps %.1f ms" % ((t_kg + t_dec) * 1e3))

    t0 = time.perf_counter()
    keys = [mod._ch_p256_keypair() for _ in range(n)]
    t_p_kg = (time.perf_counter() - t0) / n
    t0 = time.perf_counter()
    for i in range(n):
        mod._ch_p256_shared(keys[i][0], keys[(i + 1) % n][1])
    t_p_sh = (time.perf_counter() - t0) / n
    suite.record(GL, "p256-keygen-plus-shared", (), status=H.INFO,
                 detail=["keygen %.1f ms + shared %.1f ms = %.1f ms per HRR-to-P-256 handshake (mean of %d)" % (t_p_kg * 1e3, t_p_sh * 1e3, (t_p_kg + t_p_sh) * 1e3, n)],
                 brief="INFO | p256-keygen-plus-shared %.1f ms" % ((t_p_kg + t_p_sh) * 1e3))

    pinned = mod.CHROME_PROFILE.get("pinned_on")
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(pinned)).days
        text = "Chrome %s profile pinned on %s: %d day(s) old" % (mod.CHROME_PROFILE.get("major"), pinned, age)
    except (TypeError, ValueError) as exc:
        text = "pinned_on %r is not an ISO date (%s)" % (pinned, exc)
    suite.record(GL, "profile-age", (), status=H.INFO,
                 detail=[text, "INFO forever: an age is a measurement, never a verdict (ADR 0019)"],
                 brief="INFO | profile-age (%s)" % text)


# --- Z. hygiene -----------------------------------------------------------------

def group_hygiene(suite, pyc_before, tree_before, ws):
    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before) if pyc_after[k] != pyc_before[k])
    suite.record(GZ, "no .pyc written anywhere in the repo tree",
                 problem_if(new or touched, "new=%r touched=%r" % (new, touched)),
                 detail=["pyc before=%d after=%d" % (len(pyc_before), len(pyc_after))])
    added = sorted(H.repo_tree() - tree_before)
    suite.record(GZ, "no new repo paths", problem_if(added, "%d new path(s): %s" % (len(added), added[:5])),
                 detail=["the scratch area is excluded"])
    inside = os.path.realpath(ws.path).startswith(os.path.realpath(H.REPO_ROOT) + os.sep)
    suite.record(GZ, "openssl scratch lives in a mkdtemp outside the repo",
                 problem_if(inside, "the workspace %s is inside the repo" % ws.path),
                 detail=["workspace %s" % ws.path])


# ---------------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="the stdlib Chrome client's crypto and TLS 1.3 layers",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    mod = H.load_module_from_path("mcp_chrome_under_test", SOURCE)
    cc = H.load_module_from_path("chrome_capture_oracle", CAPTURE_TOOL)
    facts = readme_facts()
    openssl = find_openssl()
    ws = H.TempWorkspace("ph-mcp-chrome-", keep=opts.keep)
    try:
        group_kats(suite, mod, openssl, ws)
        group_client_hello(suite, mod, cc, facts, ws)
        group_tls(suite, mod, cc, openssl)
        group_hrr(suite, mod, cc, facts, ws)
        group_hpack(suite, mod, cc)
        group_h2(suite, mod, cc)
        group_h1(suite, mod)
        group_session(suite, mod, cc)
        group_limits_policy(suite, mod, cc)
        group_fallback_proxy(suite, mod, cc, ast.parse(text))
        group_transport(suite, mod, cc, ast.parse(text))
        group_hosts(suite, cc)
        group_negative(suite, mod, ast.parse(text))
        group_structure(suite, text)
        group_throughput(suite, mod)
    finally:
        ws.cleanup()
    group_hygiene(suite, pyc_before, tree_before, ws)
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
