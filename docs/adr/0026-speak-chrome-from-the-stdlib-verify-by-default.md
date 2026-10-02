---
name: 0026-speak-chrome-from-the-stdlib-verify-by-default
type: adr
status: active
title: Speak Chrome from the stdlib, and verify by default
description: Decision to replace primp and curl_cffi with a pure-stdlib Chrome 153 client generated from three canonical sources (the client, and brotli and zstd decoders the host injects), and -- by user decision D15 -- to make the certificate-verifying stdlib transport the default while the unverified Chrome path is an explicit opt-in in webfetch and a once-per-host block fallback in the search scripts; with the six gates and what they measured, the block rules and their declared limits (D16, D17), the SSRF, redirect, header and body rules the client now owns, the decoder load order, the deviations, what stayed unverified, the alternatives rejected, and the refusals and declared limits the security review added.
sources:
  - Scripts/_mcp_chrome.py
  - Scripts/_mcp_brotli.py
  - Scripts/_mcp_zstd.py
  - Scripts/chrome_capture.py
  - Scripts/amalgamate.py:WHOLE_SOURCES
  - Scripts/search_github.py:_grep_app_blocked
  - Scripts/search_duckduckgo.py:_ddg_blocked
  - Scripts/search_duckduckgo.py:_bing_blocked
  - Scripts/mcp-webfetch.py:_create_session
  - Scripts/mcp-webfetch.py:_vet_host
  - Scripts/mcp-webfetch.py:handle_fetch
  - tests/test_mcp_chrome.py
  - tests/test_mcp_decoders.py
  - tests/test_chrome_capture.py
  - tests/files/chrome/153/README.md
verified:
  commit: 3fbe5bf
  date: 2026-10-02
links:
  - 0004-never-pin-a-browser-impersonation-version
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0014-a-canonical-source-is-a-domain
  - 0019-only-gate-on-what-you-can-prove
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - chrome-profile-refresh
  - spec-ddg
  - generated-regions
  - scripts
  - tests
---

# ADR 0026: Speak Chrome from the stdlib, and verify by default

**Status:** accepted; shipped in `3fbe5bf` (2026-10-02, roadmap R-0044). Two
decisions are recorded together because they shipped together and neither reads
correctly without the other: the fleet's three HTTP consumers stop depending on
third-party browser-impersonation packages and speak Chrome 153 from the
standard library; and, because that Chrome path does not verify certificates,
it is **not** the default — a verified stdlib transport is.

Every number below is a measurement at the moment of the decision, quoted from
the run that produced it (§4b's frozen-record carve-out). The run logs named
here lived under `.claude/tmp/` and are not committed; the dates and hosts are.

## Context — two packages that impersonated, and neither verified what we needed

`Scripts/search_github.py`, `Scripts/search_duckduckgo.py` and
`Scripts/mcp-webfetch.py` reached the web through `primp` or `curl_cffi`, the
only two Python packages that put a browser's TLS ClientHello on the wire.
[[spec-ddg]] §2.9 measured fixed tells in primp that no version string fixes,
and curl_cffi lagged the Chrome it named; [[0004-never-pin-a-browser-impersonation-version]]
recorded why neither could be pinned honestly. R-0017's proof of concept showed
a pure-Python Chrome ClientHello and h2 preface were reachable, and
[[0024-pure-python-39-and-the-stdlib]] asks the tree to need nothing outside the
stdlib where the stdlib suffices. User decision D2 dropped both packages.

What the proof of concept could not do, and what this port does not do either,
is verify the server's certificate chain and CertificateVerify: that needs an
X.509/ASN.1 parser, RSA-PSS and ECDSA verification and a trust store, none of
which the stdlib exposes to a hand-written TLS engine. The first three planning
rounds made that unverified path the default. A peer review (finding 2) objected,
and the user decided (D15, 2026-09-30) that it must not be.

## Decision

1. **Three new canonical sources, generated whole into three hosts.**
   `Scripts/_mcp_chrome.py` holds the client; `Scripts/_mcp_brotli.py` and
   `Scripts/_mcp_zstd.py` each hold one ctypes decoder. Every host takes every
   block of each, in source order (`Scripts/amalgamate.py:WHOLE_SOURCES`), and
   `search_github.py` joins `search_duckduckgo.py` as a declared host.
2. **The decoders are injected, never named by the client.** A host passes
   `decoders={"br": _brotli_decompress, "zstd": _zstd_decompress}` to
   `Scripts/_mcp_chrome.py:_ch_session_new`.
3. **One pinned profile: Chrome 153 on macOS**, written only in
   `Scripts/_mcp_chrome.py:_chrome_profile` and measured from committed loopback
   captures; the refresh procedure is [[chrome-profile-refresh]].
4. **The verified stdlib transport is the default (D15).** The unverified Chrome
   path is an explicit opt-in in webfetch (`profile="chrome"`) and, in the two
   search scripts, a fallback taken once per host per process after an observed
   bot block. Every Chrome-path answer a consumer returns is labelled
   "certificate NOT verified".
5. **primp and curl_cffi leave the tree**, and the client takes over the
   duties they used to own silently: SSRF on every hop, redirect hygiene, header
   injection and request smuggling, body and decompression ceilings.

## Three domains, and a decoder the host injects

[[0014-a-canonical-source-is-a-domain]] decides the split. "Make the bytes a
server receives indistinguishable from Chrome's, and survive what a hostile
server sends back" is one question; "decode one compression format through the
system shared library" is another, asked twice. A merged source would make
`_mcp_chrome.py` a shelf for "things an HTTP client needs". A fourth
`_mcp_ctypes.py` for the shared library-search logic would be a shelf too ("where
libraries live"), so the two directory tuples are duplicated on purpose and a
test pins them equal.

Injection is not a style choice. A region's names resolve only against the
source its own marker names, so a `_mcp_chrome` block calling
`_brotli_decompress` by free name would be refused by the generator. The
generation-versus-import question is [[0025-generate-do-not-import]]'s and is
not re-decided; [[0023-the-websocket-client-is-a-sixth-domain]] already
re-checked it for a host that is not a server, and both of its reasons hold for
`search_github.py`.

## The gates, and what they measured

Six gates, each run before the work that depended on it; no consumer lost its
old backend before G6 had passed.

| Gate | What it proves | Measured |
|---|---|---|
| G1 profile | Chrome 153's bytes, captured, not guessed | 2026-09-30, Chrome 153.0.8010.37 on macOS 14.2.1, against `Scripts/chrome_capture.py serve` on loopback. Nine sets captured (set 1 twice: typed and reload), 196 fixtures under `tests/files/chrome/153/`, 20 connections per set and 16 for the plaintext set. Every planned set was capturable, so none of the plan's fallbacks fired: the cors-HEAD profile, the HTTP/1.1 order and casing and the post-HRR second ClientHello are all measured, not declared. JA4 `t13d1517h2_8daaf6152771_cb7bf5808d99` on every fresh SNI ClientHello (the value R-0017 recorded); `t13d1518h2_8daaf6152771_6ba8dc3d6269` on the CH2 after an HRR. `tests/files/chrome/153/README.md` |
| G2 decoders | the ctypes ABI on two platforms | `mcp_decoders` green on the dev Mac and on one Linux host (`t42`: Ubuntu 24.04, Python 3.12.3, libbrotli1 1.1.0, libzstd1 1.5.5): 133 cases, 131 pass, 0 fail, 2 info; the Linux zero-`find_library` rows passed on the real platform. t42 has no Python 3.9. (session S036 record) |
| G3 crypto/perf | KATs, and decrypt fast enough for webfetch's 5 MiB inside 30 s | KATs pass, cited per row (RFC 7748, RFC 5903, Wycheproof AES-GCM, RFC 8439, RFC 5869, RFC 8448). Floor: AES-256-GCM decrypt ≥ 0.5 MiB/s (the plan's proposal, adopted). Measured: 0.617 MiB/s on the dev Mac under real Python 3.9.21 (task-050's 3.9 run), 0.636 MiB/s on t42 under 3.12.3 (task-037 Linux run); ML-KEM-768 keygen + decaps 13.5 ms (Mac, 3.9.21) and 11.4 ms (t42); P-256 keygen + shared 3.8 ms and 4.1 ms. The row flapped to 0.27-0.48 MiB/s under concurrent load in four runs, so it is PASS at ≥ 0.5, INFO between a tenth of the floor and the floor, and FAIL only below 0.05 MiB/s — a FAIL is reserved for a rule that cannot flap. |
| G4 TLS | the engine against real and scripted peers, every refusal | RFC 8448 §3 replayed byte-exact; OpenSSL 3.6 loopback on all three cipher suites and HRR→P-256; handshake 17-26 ms on loopback against a 250 ms budget (session S036 record). Every specified refusal has a row. |
| G5 E2E loopback | the whole request API on h2, h1-over-TLS and cleartext h1 | Mac: green under the dev interpreter and real Python 3.9.21. Linux (task-037, t42, Python 3.12.3, OpenSSL 3.0.13, run after the transport selector so group P was in it): `mcp_chrome` 627 cases, 612 pass, 0 fail, 15 info — ten of the INFO rows are ML-KEM or `s_server` rows that need OpenSSL ≥ 3.5; `mcp_decoders` 133 green; `chrome_capture` 44 green. |
| G6 live | the real web, user-authorized | 2026-09-30, 18 requests of a 24-request budget, no redirect hop, retry or transport error. Chrome path: DDG GET and POST on one pooled connection (POST 10 lite results), Bing 200 with 10 parsed results, grep.app cors GET 200 JSON with 10 hits, one ordinary site (www.cloudflare.com) 200 br-decoded, www.google.com with ALPS negotiated (`alps=True`); all X25519MLKEM768. Loopback JA4 matched `t13d1517h2_8daaf6152771_cb7bf5808d99` by the independent `chrome_capture` parser. The curl_cffi baseline answered the same rows the same way. Verified transport: below. |

At the time of writing the suites stand at `mcp_chrome` 755, `generated_region`
97, `py_deps` 52, `mcp_decoders` 133, `chrome_capture` 44 and `mcp_websocket`
42 cases; the 755-case `mcp_chrome` run under real Python 3.9.21 was 748 pass,
0 fail, 7 info. After the security review's fix pass (2026-10-01) the table read
`mcp_chrome` 833, `mcp_decoders` 150 and `chrome_capture` 51. These are dated
quotations, not live counts — the live counts are the `SUITES` table in
`tests/run.py`.

## The certificate gap, and what it costs

On the Chrome path the chain and CertificateVerify are **not** verified
(`cert_verified=False`), and the module docstring says so in its first sentence.
A party on the path can therefore answer as any server, and what it receives is
not only the chance to feed content into a model's context: it receives the
session's cookie jar and every POST body — for the DDG search script, the
queries. That is why the gap is labelled everywhere the Chrome path answers:
webfetch's status line and cached report header, the response flag, and the
search scripts' per-result `**Transport**: chrome (certificate NOT verified)`
line. Round two's "the search scripts ignore the flag on purpose" was overruled
by D15's "the label stays wherever the Chrome path is used".

## D15 — the verified transport is the default

*User, 2026-09-30, answering peer-review finding 2.* A default that does not
verify hands every caller unauthenticated content, and a MITM the jar and the
POST bodies, without the caller having chosen that. The fingerprint is worth
that price only where a host has shown it blocks the verified client, or where
the caller asks for it.

- **The transport selector.** `_ch_session_new(..., transport="verified")` is
  the default; every https hop of such a session goes through the verified
  stdlib transport. `transport="chrome"` is the `_ChTls` path. An http hop is
  cleartext h1 under either.
- **webfetch opts in, never escalates.** `profile="chrome"` (alias
  `impersonate`) selects the Chrome path `Scripts/mcp-webfetch.py:_create_session`.
  A 403/429/503 after the retries is reported with a one-line hint naming
  `profile=chrome` and its cost; the caller decides.
- **The search scripts fall back once.** Each starts verified; on its endpoint's
  block signal it prints one line, re-issues the query **once** over a Chrome
  session, and adds the host to a per-process set so every later session of
  that process opens on Chrome (`Scripts/search_github.py:_transport_for`).
  The switch is never persisted: a process that did not observe a block never
  takes the Chrome path.
- **Both search hosts keep the classification-only connect policy** although
  the default transport verifies: the Chrome fallback does not, so a MITM there
  can answer with a redirect into private or metadata space, and the policy
  refuses it. On the verified transport it is defence in depth.

**The per-host block signals**, as D16 fixed them and D17 amended them, and what
G6's verified rows found (2026-09-30):

| Endpoint | Signal | Verified-transport answer, measured |
|---|---|---|
| grep.app | D17 (below): 403, a 429 with a non-JSON text/html body, or a non-JSON 200 | 429 text/html, 33944 bytes, on the very first cold `GET /` **and** on the API GET, while the Chrome path got 200 JSON seconds apart — a fingerprint block, not a rate limit |
| DDG lite | zero results AND the challenge marker, structurally (D16 S1) | 200, the warm-up GET then the POST with 10 lite results, no anomaly marker — **no block** |
| Bing | a 403 from www.bing.com itself | 200 with 10 parsed results — **no block** |
| ordinary site | — | www.cloudflare.com 200, TLS 1.3, `cert_verified=True` |

**The cost, argued before it was measured.** [[spec-ddg]] recorded that DDG
blocks every Python HTTP client regardless of TLS quality, and the verified
transport is the least coherent client there is. If that held, the sticky switch
makes a run pay **one** extra round on DDG — verified warm-up and POST, then
Chrome warm-up and POST: four requests where the Chrome-default design sent two
— and every later query costs what it did before, one Chrome POST. Without the
switch every query would cost three requests (blocked verified POST, Chrome
warm-up, Chrome POST), tripling the volume from one IP, which spec-ddg lists as
a CAPTCHA trigger in itself.

**What the live samples then recorded:**

- grep.app, task-042 (2026-10-01): the verified client was blocked, one Chrome
  re-issue, 10 results labelled `chrome (certificate NOT verified)`, 4 requests.
- DDG and Bing, task-044 (2026-10-01): 2 verified requests each (warm-up and
  search), no block, 10 results each, unlabelled; no Chrome-path request was
  made. DDG's expected first-query block did **not** materialise in this run or
  in G6 — the case D15 names as "what the probe buys": the queries travelled
  over an authenticated channel.
- webfetch default, task-050 (2026-10-01): one request to
  `https://www.iana.org/help/example-domains`, `200 via verified (cert
  verified)`. The first attempt, `https://example.com/`, resolved to 127.0.0.1
  in that shell and the SSRF guard refused it before any connection.
- DDG, the user-requested acceptance run (2026-10-01): 23 of 23 queries
  returned results — 15 sequential runs, one process per query about 4 s
  apart; one accidental `--help` run, which the script reads as a query like
  any other argument `Scripts/search_duckduckgo.py:main`; and one 7-query
  batch in a single process. 0 blocked, 0 empty, 0 errors; every answer came
  over the verified transport — no `chrome (certificate NOT verified)` label
  anywhere, no Chrome-path request, the sticky switch never flipped.
  Concurrent multi-process bursts from one IP were not tested. The raw output
  stayed under `.claude/tmp/` and is not committed.

**The cache admission rule.** webfetch's cache entry records `transport` and
`cert_verified`. A request without a profile is served an entry only if it is
`cert_verified=True`, or `cleartext` for a URL that is itself `http://`; anything
else is neither served nor revalidated, and the fresh verified answer overwrites
it `Scripts/mcp-webfetch.py:handle_fetch`. A Chrome answer can therefore never be
served as, or instead of, a verified one. Since security review 20261001-082224
(2026-10-01; its finding ids are the F-numbers cited on this page) the key also
carries `allow_private`, and an entry whose stored flag is missing or differs is
dropped at load (F1): the redirect chain of an `allow_private=true` fetch may
have run through loopback or metadata space, and the pre-cache guard vets only
the requested host. The cache directory is created `0700` and every entry
written `0600` through an exclusive temp file (F39), because an entry is an
authenticated body.

**Why `_ChFallbackConnection` keeps its name.** The class built for the TLS 1.2
fallback already *is* the verified transport: it connects to the policy's
address list, refuses a context that is not `CERT_REQUIRED` with
`check_hostname`, sends the profile's header list in h1 spelling, and honours
`on_headers` and `max_bytes` `Scripts/_mcp_chrome.py:_ChFallbackConnection`. A
second class would duplicate it; a rename churns the block name in every
whole-source marker and every test that finds it; an alias is a second block
name for one class. The name now under-describes the role, and its docstring
and this page say so.

**The verified transport has its own tell.** It is Python's `ssl` ClientHello
under Chrome 153's headers, over HTTP/1.1 where Chrome would speak h2, one
connection per request and never pooled. A fingerprinting host sees an
incoherent client — grep.app did. An honest non-Chrome User-Agent was rejected
(below); the incoherence is declared instead, and the fallback answers a host
that minds it.

**The follow-up that ends the trade-off** is a roadmap item: implement
certificate verification in `_ChTls` so fingerprint and authenticity stop being
a trade-off. D15 made the verified transport the default *because* of this gap;
closing it is what would let the Chrome path become the default again — a
decision that item must re-take, not assume.

## D16 — the block rules, and the limits declared rather than gated

*Orchestrator, 2026-09-30, resolving the round-three validator reports on D15.*
Each rule with its reason:

- **A block is judged only on the endpoint's own host** (S2): the final URL's
  host must be the endpoint's. Otherwise a public redirect target answering 403
  would flip the sticky switch. *Declared limit:* a block delivered through a
  cross-host redirect is missed and gets no fallback.
- **A transport failure on the verified path is never a block** (M2, S-Q1b).
  Any exception — certificate verify failure, alert, reset, timeout — returns
  `[]` with an error line, opens no Chrome session and never sets the switch;
  `None`, the block value, is returned only by the predicates. Otherwise an
  active MITM could force the downgrade to the unverified transport just by
  breaking the verified handshake. *Declared limit:* a WAF that resets the Python
  TLS fingerprint instead of answering gets no Chrome fallback.
- **429 and 5xx are never a block; 403 is, on grep.app and Bing** (M3). A 429 is
  a verdict on volume and re-issuing over another transport adds load to a host
  that asked for less; a 5xx is a server fault. D17 amends this for grep.app.
  *Deferred and declared (R25):* a Bing challenge served as a 200 that parses to
  `[]` is not detected `Scripts/search_duckduckgo.py:_bing_blocked`.
- **DDG's block is structural, never a body substring** (S1). The lite page
  reflects the query and third-party snippets, so the old `"anomaly-modal" in
  resp.text` test let whoever controlled a snippet, or the caller's query, move
  the process onto the unverified transport. The rule is zero parsed results AND
  the challenge marker as a structural element outside the results container.
  **No challenge page was observed** — not in G6 and not in task-044 — so the
  element was never measured and the plan's **fallback predicate** shipped: own
  host AND zero results AND a marker in the page AND the query does not contain
  that marker `Scripts/search_duckduckgo.py:_ddg_blocked`. A snippet cannot trip
  it (a page with a snippet has a result) and the query cannot (the last
  clause). Measuring the real element is an open follow-up.
- **The label is the redirect chain's weakest hop** (S3). A MITM on an
  unverified hop chose where the chain went next, so a verified final hop does
  not make the body's provenance verified: `cert_verified` is True only if every
  hop was `verified` or `tls12-fallback`, and any Chrome hop labels the chain
  `chrome`. `impersonated` still describes the final hop, because it states
  which fingerprint delivered the body.
- **A refusal's prefix is the hop's transport label** (M1): `verified:` on the
  default transport, `tls12-fallback:` on the fallback, and a timeout keeps its
  phase first (`timeout: verified timed out`).
- **The environment (S4).** See "No proxies" below.
- **The verified transport's response-head limits are http.client's** (S5,
  R29): `_MAXLINE` and `_MAXHEADERS`, not the `CH_*` h1 ceilings. The body and
  decoded-size limits are the session's own on both transports, and a group P
  row proves the decoded cap on the verified transport with `max_bytes=0`.
  *Declared, not gated.*

## D17 — grep.app's block shape is what was measured

*Orchestrator, session S038, after task-038.* grep.app's block signal is: the
final hop is on grep.app AND the body decoded AND (403, OR a 429 whose body is a
non-JSON `text/html` page, OR a 200 whose body is not JSON). A 429 with a JSON
body stays a rate-limit verdict `Scripts/search_github.py:_grep_app_blocked`.

**Why.** D16's "429 is never a block" was a proxy written before anything was
measured. G6 measured it: the verified fingerprint got a 429 HTML page of 33944
bytes on the very first cold request and on the API, while the Chrome path got
200 JSON. That is a fingerprint verdict dressed as a rate limit. task-042's live
search confirmed the ladder end to end. D17 amends D16 for grep.app only: Bing
and DDG keep 429 as never-a-block, because neither was seen answering that way.

## The TLS 1.2 fallback, and why its trust runs the other way

The Chrome client stays TLS 1.3 and raises `ChromeTls12Error` on a TLS 1.2
ServerHello. A session opened with `tls12_fallback=True` re-issues that hop once
through `_ChFallbackConnection` with `ssl.create_default_context()`, connecting to
the same vetted address list. The two paths trade the same two properties in
opposite directions: the Chrome path is impersonated and unverified, the
fallback verified and not impersonated (`impersonated=False`,
`cert_verified=True`). A MITM cannot profit from forcing it, because the path it
forces verifies. Since D15 the fallback exists only under `transport="chrome"`
(webfetch's `profile="chrome"`); the verified transport negotiates TLS 1.2 by
itself. It is off for both search scripts. urllib was refused for it because it
honours `*_PROXY`.

**Both stdlib transports have a TLS 1.2 floor (F9).** The session raises the
fallback context's `minimum_version` to TLS 1.2, and `_ChFallbackConnection`
refuses any context whose floor is lower, so Python 3.9 can no longer negotiate
TLS 1.0 or 1.1 on the verified transport or the fallback. Both wrap the socket
with `suppress_ragged_eofs=False` and refuse a body shorter than its
Content-Length (F8): a TCP FIN without close_notify is an error, never a quiet
end of the body `Scripts/_mcp_chrome.py:_ChFallbackConnection`.

## No proxies, and the environment the stdlib still reads

The client ignores every `*_PROXY` variable and reads no environment variable
for networking itself: a proxy would terminate the TLS session and the
fingerprint with it, and would route a fetch through a party the caller never
chose (D12). The corrected sentence (S4): `ssl.create_default_context()`, which
the verified transport and the TLS 1.2 fallback use in production, loads
OpenSSL's default trust store, which honours `SSL_CERT_FILE` / `SSL_CERT_DIR`,
and CPython's `create_default_context()` honours `SSLKEYLOGFILE` (3.8+, unless
`-E`). Those reads are the stdlib's, on those two transports only; the Chrome
path reads none of them. They are the same trust boundary as the interpreter's
own environment — declared, not gated.

## The per-hop SSRF policy

primp and curl_cffi followed redirects that webfetch's guard never saw, and the
guard resolved a name and then let the library resolve it again. The client now
owns the rule, enforced on every path — the TLS 1.3 path, cleartext h1, the TLS
1.2 fallback, the verified transport, every redirect hop and every retry attempt:

1. **One normaliser.** `Scripts/_mcp_chrome.py:_ch_split_url` lowercases, refuses
   userinfo and any scheme but http/https, IDNA-encodes, keeps a trailing dot,
   unwraps a bracketed IPv6 literal and defaults the port. The host it returns is
   exactly what the policy vets and what the session connects for.
2. **An ordered, all-vetted address list.** The policy resolves once and returns
   a non-empty ordered list or refuses; the session connects only to those
   addresses and never resolves the name itself, which closes the resolve/connect
   TOCTOU.
3. **Any refused address refuses the hop**, and the parse fails closed: an
   address `ipaddress` cannot parse, or an empty answer, is refused (the old guard
   skipped an unparseable one).
4. **The embedded-IPv4 verdicts, made explicit (R2-M2).** An IPv4-mapped address
   is classified on its embedded IPv4 only; NAT64 (`64:ff9b::/96`,
   `64:ff9b:1::/48`) and 6to4 (`2002::/16`) are refused as whole prefixes, because
   a translated address leaves through a gateway whose far side cannot be
   classified; `not is_global` catches 100.64.0.0/10
   `Scripts/_mcp_chrome.py:_ch_address_refused`. **This closed no bypass.**
   Measured on 3.9.21's `ipaddress.py`, the old guard already refused NAT64
   (`64:ff9b::` lies in the reserved `::/8`, `:2346-2347`) and 6to4 (`2002::/16`
   is in the private list, `:2330`), and already allowed a public mapped address
   because `is_reserved` / `is_private` defer to the embedded IPv4 (`:2001`,
   `:2048-2050`). The rewrite makes the verdicts explicit and independent of the
   patch release; which 3.9.x added the mapped deferral was not measured.
   An IPv6 site-local address (`fec0::/10`, deprecated, and passed by the
   checks above) is refused as well (F2).
5. **The scheme check and the downgrade sit outside the policy.** A `Location`
   whose scheme is not http/https is refused by the session, so webfetch's
   `allow_private=true` — which only skips address classification — cannot skip
   it. An https→http hop needs the session flag `allow_downgrade=True` (webfetch
   sets it; the search scripts do not); no policy can grant it (R2-N3).
6. **Every host has a policy.** The search hosts pass
   `Scripts/_mcp_chrome.py:_ch_public_only_policy`; webfetch passes its own.
   webfetch's guard is split (R2-M7): `Scripts/mcp-webfetch.py:_vet_host` is the
   session's policy and refuses by raising, and `_check_host_allowed` stays a
   thin URL wrapper for the pre-cache check, keeping its signature and its
   no-resolve `allow_private` short-circuit.

## Redirect hygiene

- Caller headers bind to the origin they were given for; on a cross-origin hop
  every one is dropped, `Authorization` and a caller `Cookie` included, and they
  stay dropped for the rest of the chain.
- `Referer` follows Chrome's `strict-origin-when-cross-origin`: the full URL
  same-origin, the origin only cross-origin, none on https→http; a typed
  navigation sends none `Scripts/_mcp_chrome.py:_ch_referer_for`.
- **A 307/308 cross-origin redirect of a POST re-sends the body to the new
  origin, as Chrome does.** Kept on purpose, for fidelity. Only the DDG script
  POSTs, and only to DuckDuckGo.
- **A non-Secure cookie set over https is sent on an https→http hop to the same
  host**, as RFC 6265 and Chrome do; a `Secure` cookie never travels over http,
  and one set by an http response is ignored.
- Twenty redirects at most (`CH_MAX_REDIRECTS`).

## Caller headers: refused, not dropped

`Scripts/_mcp_chrome.py:_ch_check_caller_headers` runs before any connection,
per name and value, first failure wins: a CR, LF or NUL anywhere, then any other
control character but HTAB and a value starting or ending with SP or HTAB
(F27); a pseudo-header;
a name that is not an RFC 9110 token; a fingerprint-bearing name (`user-agent`,
`accept-encoding`, `priority`, `sec-ch-ua*`, `sec-fetch-*`); a framing or
connection name (`host`, `content-length`, `transfer-encoding`, `connection`,
`te`, `upgrade`, `keep-alive`, `proxy-*`); then a value that is not latin-1. Each
is a one-line refusal that never echoes a value or an unvalidated name. Header
injection (CWE-113) and request smuggling (CWE-444) were the packages' to
prevent; with a hand-written h1/h2 encoder they are this function's. Overriding a
fingerprint-bearing header is refused rather than dropped or honoured (M5):
dropping hides the caller's intent, honouring it rebuilds the incoherent
fingerprint the profile exists to avoid. It is a behaviour change for webfetch,
which used to forward any header. A caller `Cookie` is allowed: the jar's cookies
first, then the caller's crumbs, in the profile's cookie slot.

## The cookie jar refuses rather than evicts

The jar caps cookies per domain (180, Chromium's own figure, not re-verified
against its source), in total (3000, RFC 6265's minimum) and per cookie (4096
bytes), and a cookie over a cap is **refused**, not made room for (R2-M3) — a
deliberate deviation from Chromium's LRU collection, so a hostile site cannot
flood a session cookie out `Scripts/_mcp_chrome.py:_ChCookieJar`. A Domain with
fewer than two labels or on a 52-entry public-suffix "lite" list is refused (the
PoC's matcher accepted `Domain=com`); cookies for an IP literal are host-only.
The lite list is a declared limit: a registrable suffix missing from it reads two
registrants as one site.

## Body limits: `ChromeBodyTooLarge`, and headers before size

Wire bytes over `max_bytes` abort the stream; decoded bytes over the budget raise
too, and that budget is **one** cumulative figure shared by at most two stacked
codings, every gzip member and the raw-deflate retry, so `gzip, gzip, gzip` or a
multi-member bomb cannot multiply it. Both raise
`Scripts/_mcp_chrome.py:ChromeBodyTooLarge`, which carries `.limit` (R2-M6), and
webfetch maps it to `body exceeds max_bytes=N`. `max_bytes=0` means
`CH_MAX_BODY_BYTES` on the wire and `CH_DEFAULT_DECODE_CAP` decoded, never
unbounded. webfetch's non-textual content-type refusal keeps its place ahead of
any size decision through an `on_headers` callback, called with the final head
before any body byte is read; a callback was chosen over a two-phase response
object because the blocking API returns one finished response.

Rules the security review added here:

- **A body's charset comes from an allowlist (F25).** `.text` decodes with the
  content-type charset only when it names the same codec as one of the WHATWG
  Encoding Standard names in `TEXT_CHARSETS`; anything else is UTF-8, and a
  `UnicodeError` from the codec is caught like a `LookupError`
  `Scripts/_mcp_chrome.py:_ChResponse`. A server-chosen codec used to reach
  `codecs.lookup`: punycode is quadratic (minutes on a 5 MiB body) and idna
  raised an uncaught `UnicodeError` even under `errors="replace"`.
- **A digit string is at most 19 digits (F24).** Content-Length and a cookie's
  Max-Age are refused (Content-Length) or ignored (Max-Age) past 19 digits, so
  no unbounded `int()` runs on a server's value.
- **The search scripts read at most 2 MiB (F32).** Both search sessions pass
  `max_bytes` = `SEARCH_MAX_BYTES` (2 MiB) rather than the 64 MiB ceiling; an
  oversized answer is `ChromeBodyTooLarge`, which returns `[]` and is never a
  block. The DDG lite page is parsed once and the result handed to the block
  predicate `Scripts/search_duckduckgo.py:_ddg_blocked`.

## HelloRetryRequest: P-256 only, and the CH2 Chrome sends

Chrome offers P-384 alongside P-256 in `supported_groups`. The client answers an
HRR for P-256 only (FLAG-2, user, S035); one selecting P-384 fails with
`tls: HelloRetryRequest selected secp384r1 (not implemented)`, a group we already
sent a share for or never offered is `illegal_parameter`, a ServerHello whose
cipher differs from the HRR's is refused, and a second HRR is refused. P-384 is
a roadmap follow-up. The second ClientHello is built in the shape set 9
measured on 20 of 20 connections: a compat ChangeCipherSpec first, the same
random, session id, ciphers and GREASE, the same extension order with the cookie
inserted at a varying position, one P-256 key share, the cookie echoed, and the
ECH GREASE unchanged `Scripts/_mcp_chrome.py:_ch_client_hello`.

## ALPS: the client's value is empty, and the source says so

No loopback capture can show the client's ALPS EncryptedExtensions — they are
encrypted, and no stdlib server negotiates ALPS. A first implementation sent a
full SETTINGS frame, written from memory. Checking the source contradicted it:
Chromium main `43502f9b0389c0b1e81b6e379180086d637bf7be`,
`net/http/http_network_session.cc`, sets the h2 application settings to an empty
value ("Enable ALPS for HTTP/2 with empty data"), and BoringSSL main
`98df68178dcf8a75b230503951b8a3b4f5c51aa6`, `ssl/tls13_client.cc`, copies the
local settings verbatim into the client EE. The client therefore sends a
zero-length extension 17613 body; only the server's ALPS data is h2 frames, and
that side is read, never sent. The same BoringSSL revision's `ssl/tls13_both.cc`
gives the `CH_MAX_KEY_UPDATES` figure of 32. G6 negotiated ALPS live on
www.google.com and the request completed. Omitting ALPS was the documented
fallback; it changes the JA4 (`t13d1517` → `t13d1516`) and was not needed.

## HTTP/1.1

In scope in two forms (D9): over TLS when the server selects ALPN `http/1.1`,
and in cleartext for `http://` URLs. The request head is byte-identical to the
captured sets (20 of 20 over TLS, 16 of 16 in cleartext): `sec-ch-*` lowercase,
every other name canonical case, no `Priority`.

## What the engine and the readers refuse since the security review

The security review's fixes on the Chrome path, each a refusal with a group row:

- **TLS records.** A plaintext alert after the key change is
  `unexpected_message` (F6; RFC 8446 §5 encrypts every later alert). More than
  32 records in a row that carry no application data (BoringSSL's
  `kMaxEmptyRecords`) are refused (F21) `Scripts/_mcp_chrome.py:_ChTls`.
- **Truncation.** An EOF-delimited h1 body that ends on a bare TCP EOF, without
  close_notify, is an error rather than a complete body (F7)
  `Scripts/_mcp_chrome.py:_ChH1Connection`.
- **h2 response fields are validated like h1's (F22).** A field name that is
  empty, uppercase, not a token or connection-specific, or a value carrying CR,
  LF or NUL, makes the response malformed (RFC 9113 §8.2.1): that stream is
  reset with "malformed response header" and the connection is kept
  `Scripts/_mcp_chrome.py:_ChH2Connection`.
- **Request fields are checked three times (F27).** The caller check (above),
  the h1 encoder and the h2 encoder each refuse a control character other than
  HTAB and a value with edge whitespace, before anything is written.
- **The h2 flood counter counts frames on a stream this client reset (F23)**,
  so a peer cannot stream unlimited frames at an abandoned stream.
- **webfetch echoes only printable text (F41).** The final URL, a refusal's URL
  and every rendered response header name and value pass through `_printable`
  `Scripts/mcp-webfetch.py:_format_response`, so a server cannot put an escape
  sequence or a fence-closing backtick run into the report.
- **chrome_capture exports nothing secret (F44, F45).** Export refuses a
  credential-bearing header (a denylist plus `*-token` and `*auth*` names), a
  cookie value the capture server did not set itself, and any request body
  outside a scripted allowlist; records are written `0600` in a `0700`
  directory `Scripts/chrome_capture.py:write_private_json`. A pseudo-header name
  in the akamai summary is printed terminal-safe (F34).

## The decoders: load order, planting, the lazy load, the window

- **Load order (FLAG-1, user, S035; darwin half superseded by F36, user,
  S040).** Linux: the bare soname, then explicit
  absolute paths — `ctypes.util.find_library` is **never** called, because on
  Linux it spawns gcc/cc, objdump and ld without `stdin=` (3.9.21
  `ctypes/util.py:124-127`, `:311-313`), and inside mcp-webfetch fd 0 is the
  JSON-RPC pipe. Its one non-spawning half, `ldconfig -p`, reads the cache the
  bare-soname `dlopen` already consulted, so it bought almost no reach. The
  hazard is eliminated, not mitigated. macOS loads **absolute paths only**:
  `find_library` first, because there it is `dyld_find`, pure Python, and
  spawns nothing — its answer is accepted only if it is absolute — then the
  explicit absolute paths; there is **no** bare-soname step on macOS (F36, next
  bullet; the plan's FLAG-1 order had one, see Deviations).
  Other platforms use the bare soname only (on the BSDs and SunOS
  `find_library` spawns `ldconfig -r` / `crle`). The price: a library installed
  where neither the loader's default search nor the path list reaches is not
  found on Linux, and the `LookupError` names every path tried.
- **No library is loaded from the working directory on macOS (security review
  F36, its one HIGH).** The first build tried the bare soname on macOS too,
  and dyld searches the process's cwd for a name without a slash. Measured
  (2026-10-01): a bare-name `CDLL` from a cwd holding a planted dylib loaded it
  and ran its constructor on Homebrew Python 3.14.0, Homebrew 3.9.21 and Apple
  `/usr/bin/python3` 3.9.6 — and the real loaders ran the planted constructor
  before their symbol checks, so opening a br or zstd response inside a cloned
  repository was enough. The bare step is removed on darwin, a `find_library`
  answer is taken only if absolute, and the `CDLL` stub hook refuses any
  non-absolute name on darwin as a last line (`Scripts/_mcp_brotli.py:_br_cdll`,
  `Scripts/_mcp_zstd.py:_zstd_cdll`). After the fix the same probe loaded the
  `/usr/local/lib` copy on all three interpreters. Linux keeps its bare
  sonames: glibc's `dlopen` does not search the cwd.
- **The remaining library planting is accepted.** The explicit lists include
  `/usr/local/lib` and `/opt/homebrew/lib`, writable by the admin user on a
  Homebrew Mac, and `dlopen` honours `LD_LIBRARY_PATH` / `DYLD_*`. Anyone who can
  write there, or set the loader environment, can already replace the `python3`
  those prefixes provide, so the decoders add no new principal. Declared, not
  gated.
- **The lazy load is race-free without a lock (M1).** webfetch runs handlers on
  a pool. The first br or zstd body walks the attempts, configures every
  `argtypes` / `restype` on a local handle, and publishes the configured handle —
  or the one-line failure — with **one** dict item store
  (`Scripts/_mcp_brotli.py:_br_load`, `Scripts/_mcp_zstd.py:_zstd_load`). Under
  the GIL a reader sees no result or a complete one, never a handle without its
  `restype`. Two racing threads may both `dlopen` the same file, which is
  reference-counted and harmless. The race group runs 50 rounds of two threads.
- **The output buffer is ours.** Output goes to a buffer we allocate; the
  produced count and the monotonic counters are validated after every call, and
  a library that lies about them raises `ValueError` — never bytes. Lying-stub
  rows are the negative control. The decoded output accumulates in one
  `bytearray`, copying only the produced bytes of each pass (F18); the earlier
  list-of-chunks join held about twice the cap at its peak.
- **The zstd window is capped at log 23 (8 MiB) (M2)**, as RFC 9659 permits an
  HTTP decoder to refuse more; the library default would accept up to log 27.
  A committed `window-27.zst` fixture is refused. Brotli's large-window extension
  stays off.
- **Only zstd1 and skippable frames reach the library (F13).** A legacy v0.x
  frame is decoded by a library path that never consults the window limit, so
  a legacy or unknown frame magic is refused at byte 0 and at every later frame
  boundary `Scripts/_mcp_zstd.py:_zstd_check_magic`.
- **Advertising stays fixed.** `accept-encoding` is part of the fingerprint, so a
  machine without a decoder library still advertises `br, zstd` and reports the
  body undecodable in one line, rather than becoming a per-machine tell.

## Non-constant-time crypto

Every primitive is pure Python and **not** constant-time: AES-GCM with its
GHASH, ChaCha20-Poly1305, X25519, P-256 and ML-KEM-768 (the first draft named
only AES and P-256; widened after F12). A timing side channel
needs an observer co-located with our process, and every key is ephemeral per
connection, so the exposure is local-only. Accepted, and written into the module
docstring so nobody mistakes this for a general-purpose TLS library.

## Deviations the implementation made, recorded

- webfetch's cache admission tests `profile != "chrome"` rather than `is None`,
  so an unknown profile gets the strict default admission instead of opening the
  cache wider.
- A `verified` or `tls12-fallback` entry that does not carry `cert_verified=True`
  shows `via unknown transport`: the label never claims more than was recorded.
- A Chrome https→302→http chain is stored `chrome` / `cert_verified=False` (the
  chain rank puts `chrome` below `cleartext`), not `cleartext`. Neither is served
  to a default request.
- webfetch clamps a negative `max_bytes` to 0, which means the `CH_*` ceilings,
  not unbounded.
- `_vet_host` receives the IDNA host with its trailing dot, while the SNI drops
  one trailing dot (RFC 6066: a HostName carries none).
- Accepted during the build, each security-positive: the h2 flood counter also
  counts PRIORITY, WINDOW_UPDATE, GOAWAY and 1xx frames; a separate
  `CH_MAX_H2_CONTINUATION_FRAMES`; the policy runs on every hop, a pooled reuse
  included; a navigation carrying a body is refused.
- Where the plan and a capture disagreed, the capture won: Chrome 153 sends
  **no** `Origin` on a same-origin fetch (re-decoded from every POST, GET and
  HEAD connection), although the plan derived one from the referer.
- The darwin decoder load order deviates from the plan's FLAG-1 order (S035),
  which tried the bare soname first: F36 measured a planted dylib in the cwd
  being loaded, so darwin now tries absolute paths only (see the decoders
  section above). The user accepted the supersession on 2026-10-01 (S040). The
  Linux order is unchanged.

## What stayed unverified, or open

Declared, not claimed:

- **DDG's structural challenge element** — never observed; the fallback
  predicate shipped (D16). No live run sent concurrent multi-process bursts
  from one IP.
- **Bing's block shape** — a 403 was never observed, and the 200 challenge is
  deferred (R25).
- **NFR-5, the host start-up budget of 150 ms.** One reading put
  `search_github.py --help` 159 ms over baseline on Python 3.14 under concurrent
  load; a later INFO row under Python 3.9.21 read a 91 ms delta, best of three,
  with the load not recorded. Neither is an unloaded 3.9 measurement, so no
  result is claimed: the budget is open.
- **Profile slots no capture carries:** a custom (non-Accept) caller header's
  slot, `referer` on a navigation, `cookie` on a fetch, `origin` on a
  cross-origin fetch, `Cache-Control` / `Cookie` / `Referer` on an h1
  navigation, and the h1 order of a fetch. Each is marked UNVERIFIED in
  `_chrome_profile`.
- **Behaviours no capture or source check covers:** the multi-crumb cookie
  split, the HPACK table-size-update path, the nghttp2/Chromium flood-counter
  values (the `CH_MAX_H2_*` numbers are this module's), BoringSSL's handshake
  message limit, the compat CCS placement without an HRR, Chrome's 4096-character
  referrer cap (not applied), and SameSite (not enforced).
- **Other declared limits:** an h2 request body larger than one window (16384
  bytes) is refused; the resolver call is not bounded by the call deadline; a
  session built without a policy uses an unvetted resolver — no production host
  does; there is no session resumption, so Chrome's resumed JA4 is never
  produced; there is one profile, so a Linux host announces macOS over a Linux
  TCP stack (the [[0004-never-pin-a-browser-impersonation-version]] addendum
  accepts it).
- **Declared after the security review** (its verified findings that were not
  fixed, or were fixed with a residue):
  - the h2 per-connection control-frame counter never resets (F23): a pooled
    connection that abandons several large downloads can reach the flood limit
    and be closed with GOAWAY;
  - the decoders still make one final `bytes()` copy, and `_ChResponse`
    re-`bytes()` the result anyway, so a moment of about twice the decoded size
    remains (F18);
  - `raw=true` with `show_headers=true` prints the response header values
    unescaped, by design: raw is the wire shape, and only the report is
    escaped;
  - a planted webfetch cache entry's `cert_verified` is self-asserted; the
    cache lives under `~/.claude` in the documented launch, so planting one
    needs that user's write access (F40);
  - there is no destination-port restriction (F3) — roadmap R-0055;
  - the host is normalised with IDNA2003, which is transitional (`ß` becomes
    `ss`) (F5) — roadmap R-0056;
  - the DDG and grep.app regex parsers stay super-linear on a hostile body,
    bounded now by the 2 MiB cap (F32) — a single-pass parser is roadmap
    R-0057;
  - mcp-webfetch's PEP 723 dependencies are unpinned (F47) — roadmap R-0058;
  - the verified transport's ragged-EOF refusal (F8) may surface on OpenSSL 3
    as an `ssl` error whatever the client does, so its message is not
    guaranteed to be the client's own.

## Alternatives rejected

1. **Keep primp / curl_cffi and fix their tells.** spec-ddg measured tells no
   version string fixes, and curl_cffi lags Chrome. D2 drops both.
2. **One merged source with the decoders inside.** [[0014-a-canonical-source-is-a-domain]]:
   loading a compression library is not the TLS client's domain.
3. **A fourth `_mcp_ctypes.py` for the library search.** A shelf; the tuples are
   duplicated and pinned equal instead.
4. **`_mcp_chrome` calling `_brotli_decompress` directly.** The generator refuses
   a region whose names do not resolve against its own source.
5. **A `:: *` wildcard marker.** It would change the generator's explicit-name
   contract for one source; the whole-source check gives the same safety.
6. **Keep `find_library` on Linux as a mitigated step.** FLAG-1: it buys almost
   no reach at the cost of the stdin hazard.
7. **Drop or honour a fingerprint-bearing caller header.** Refused above.
8. **Advertise only `gzip, deflate` when a library is missing.** A per-machine
   fingerprint tell.
9. **urllib for the TLS 1.2 fallback.** It honours `*_PROXY`.
10. **The Chrome path as the default transport (rounds 0-2).** D15.
11. **A separate verified-transport class, or renaming `_ChFallbackConnection`.**
    Duplication or churn; the name stays and is explained.
12. **A per-query verified probe with no sticky switch.** Three requests per DDG
    query if DDG blocks, and burst traffic is itself a CAPTCHA trigger.
13. **Persisting the sticky switch across processes.** It would take the Chrome
    path in a process that never saw a block, and add state to two CLI scripts
    that have none.
14. **A new webfetch `transport=` argument.** `profile` already means "which
    browser to impersonate"; a second argument creates a combination that has to
    be refused.
15. **Automatic escalation to the Chrome path in webfetch.** D15 makes it an
    explicit opt-in; a hint line names it instead.
16. **An honest non-Chrome User-Agent on the verified transport.** A second
    header source to maintain; the incoherence is declared and the fallback
    answers a host that minds it.
17. **Switch all three consumers in one commit.** One consumer per commit, so
    each can be reverted alone.
18. **Omit ALPS from the ClientHello.** Kept only as the fallback had the ALPS
    work failed; it changes the JA4.

## Out of scope, deliberately

Certificate validation on the Chrome path (the roadmap follow-up above); pooling
on the verified transport; session resumption and 0-RTT; HTTP/3 and QUIC;
proxies; multipart and JSON POST; streaming responses; HRR to P-384; a second
browser profile; cancelling an in-flight webfetch; and the DDG script's `cdp`
backend, which drives a real Chrome and has no transport to choose.

## Addendum (2026-10-02): NFR-5 closed by acceptance, not by measurement

The user closed NFR-5 (the 150 ms host start-up budget) on 2026-10-02 by accepting the cost rather than measuring it on an unloaded Python 3.9. The two readings above stand as the only data: 159 ms over baseline on Python 3.14 under concurrent load, and 91 ms on Python 3.9.21, best of three, load not recorded.

The reasons for accepting it: only the search scripts pay it per call, because they start a fresh process per invocation; the webfetch MCP server pays it once per session. A DDG query in the 2026-10-01 acceptance run took 2-3 s and the batch mode waits 2.5-5 s between queries, so the start-up cost is a few percent of a call. The cost is structural: the generated regions live in a script run as `__main__`, which gets no cached bytecode, and the only fix -- an importable shared module -- is what [[0025-generate-do-not-import]] rejects. A later measurement above 150 ms therefore changes nothing in the code.

## Addendum (2026-10-02): F3 and F5 closed, a P-384 HelloRetryRequest now warns, DDG's 202 is a block, three follow-ups dropped

**IDNA deviation characters are refused (F5, R-0056, `aae2643`).** The stdlib `idna` codec is IDNA2003, which is transitional. It maps ß (U+00DF) to `ss`, and final sigma (U+03C2), ZWJ (U+200D) and ZWNJ (U+200C) to sigma or to nothing. Chrome (UTS-46 nontransitional) keeps all four, so the client could reach a different host than Chrome would. One helper, `Scripts/_mcp_chrome.py:_ch_idna_encode`, raises the codec's own `UnicodeError` before encoding a name that carries any of the four. All four IDNA sites call it: `_ch_split_url`, `_ch_sni_name`, `_ch_origin_of` and the cookie jar's `Domain=` handling in `_ChCookieJar`. Each caller keeps its existing failure path; in the normaliser that is `url: host is not a valid IDNA name`. A UTS-46 mapping table was not ported. These four code points are exactly where transitional and nontransitional processing differ, so refusing them removes the divergence with no table to keep current. The consequence is declared: a host that really contains one of the four is refused rather than reached under a different name. This closes the F5 item under *What stayed unverified, or open*.

**The Fetch bad-port list is refused (F3, R-0055, `aae2643`).** `Scripts/_mcp_chrome.py:_CH_BAD_PORTS` holds the Fetch standard's port-blocking list. `Scripts/_mcp_chrome.py:_ch_split_url` refuses any port on it with `url: port <n> refused`. The initial URL and every redirect target both pass through that normaliser, so a `Location` pointing at a bad port is refused before the hop's connect policy is called. This is Chrome's denylist, not an allowlist: a port that is not on it is still reached. This closes the F3 item ("no destination-port restriction").

**A P-384 HelloRetryRequest is still refused, but it now logs a WARNING first (FLAG-2, R-0048, `422e5a6`).** The refusal `tls: HelloRetryRequest selected secp384r1 (not implemented)` is unchanged, and FLAG-2 (P-256 only) stays declared. Before it raises, `Scripts/_mcp_chrome.py:_ChTls` logs a WARNING on the stdlib logger `chrome-client` that names the host and R-0048. The user decided on 2026-10-02 to keep the item recorded and to warn when a real server shows up, so that a server preferring P-384 gets noticed and is not read as one more failed fetch. If logging is not configured, the record reaches stderr through `logging.lastResort`. The client now imports `logging`, and `Scripts/search_duckduckgo.py` and `Scripts/search_github.py` import it for the generated region. R-0048 stays open.

**DDG: an HTTP 202 with zero results is a block (part of D16 S1; R-0052, `92f1d38`).** `Scripts/search_duckduckgo.py:_ddg_blocked` used to ignore the status. Now it also reports a block when all of these hold: the answer came from `lite.duckduckgo.com`, the body decoded, it parses to zero results, and the status is 202. No marker is needed for this branch. The marker branch beside it is unchanged. 202 is the one challenge answer [[spec-ddg]] recorded (§2.6), and the user has seen it live; lite results come back as 200. So a challenge page with reworded text can no longer pass as an empty search. Only the status signal is measured. The structural element that S1 asks for is still unmeasured, because no challenge page has been recorded, so the marker branch remains the plan's fallback and R-0052 stays open (horizon later). The predicate implies a cost, declared here: a genuine empty search answered with 202 now counts as a block and costs one Chrome-path re-issue.

**Three follow-ups dropped by the user (2026-10-02).**

- R-0049, TLS 1.3 session resumption (PSK) on the Chrome path: "resumption only pays off for several fetches over one connection within one process, and even then its value is doubtful." The lack of session resumption stays a declared limit, so the resumed JA4 is never produced.
- R-0053, a Bing challenge served as a 200 that parses to no results (R25): "a Bing block has never been observed; nothing to measure against." R25 stays declared on `Scripts/search_duckduckgo.py:_bing_blocked`.
- R-0050, a Linux Chrome profile: "not needed -- the macOS Chrome profile is enough; a Linux-specific profile would leak the platform." One macOS profile, announced on every host, is the design and not a gap.

## Addendum (2026-10-02): F32 closed, the DDG lite and grep.app result parsers are single html.parser passes

**The search result parsers are linear (F32, R-0057, `a8c3443`).** *What stayed unverified, or open* says the DDG and grep.app regex parsers stay super-linear on a hostile body, bounded by the 2 MiB cap, and that a single-pass parser is roadmap R-0057. That parser shipped. `Scripts/search_duckduckgo.py:parse_lite_results` cut the page into rows with `re.findall(r'<tr[^>]*>(.*?)</tr>')` and searched each row for the `result-link` anchor and the `result-snippet` cell; `Scripts/search_github.py:extract_code_from_snippet` ran `re.finditer` over `<tr data-line="(\d+)">.*?<pre>(.*?)</pre>`, both DOTALL. A lazy scan with no closer in sight rescans to the end of the input for every opener. Both are now one `html.parser` pass, the shape `Scripts/search_duckduckgo.py:parse_bing_results` already had: `Scripts/search_duckduckgo.py:_LiteParser` and `Scripts/search_github.py:_SnippetParser`. Each is keyed on start tags and attributes and never relies on an implied end tag, which html.parser does not have. `Scripts/search_duckduckgo.py:_raw_href` reads the href off the raw start tag, because html.parser decodes attribute values while the regex kept a direct link's `&amp;` as written. `clean_html_tags`, itself a quadratic `<[^>]+>` pass, was left with no caller and was deleted from both scripts; `ClaudeCode/scripts/search_duckduckgo.py` has its own unrelated copy, which was not touched.

**Measured red first.** On the old parsers an unterminated-`<tr>` DDG body took 6.4 s at 64 KiB (about quadratic), and a grep.app body of rows with an unclosed `<pre>` took 18 s at 16 KiB (about cubic). The new parsers take 0.5-1.3 s on the same shapes at the 2 MiB body cap. The gate is the new suite `search_parsers` (`tests/test_search_parsers.py`): it pins the fields the regex parsers produced as literals, then parses four hostile bodies shaped from the old patterns' worst case, each just under the module's own `SEARCH_MAX_BYTES`, in a child process with a 10 s bound. The regex parsers ran past the 60 s child timeout on all four. The 2 MiB cap stays; it now bounds memory and time, not a quadratic scan.

**Output equivalence, and the declared limit.** Beyond the pinned literals, 900 of 900 fuzzed pages parsed to identical output on Python 3.9. One divergence is declared rather than fixed: CPython 3.14's html.parser treats an unclosed `<title>` (and by the same rule `<textarea>`, `<script>`, `<style>`) as raw text to the end of the page, as a browser does, so a page with a dropped `</title>` yields no results where the regex still found some. Python 3.9's html.parser treats only `<script>` and `<style>` that way. A case-folded tag (`<TR>`) or a `<pre>` hidden in a comment is likewise read as a browser reads it, not as the regex did. This closes the F32 item under *What stayed unverified, or open*.
