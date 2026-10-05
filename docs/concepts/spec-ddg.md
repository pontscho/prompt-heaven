---
name: spec-ddg
type: concept
status: active
title: DuckDuckGo Bot Detection Research
description: Why DDG blocks Python HTTP clients, and the DDG-first/Bing-fallback strategy used by the search script.
sources:
  - Scripts/search_duckduckgo.py
  - Scripts/search_github.py
  - Scripts/_mcp_chrome.py
  - Scripts/_mcp_websearch.py
  - Scripts/mcp-search.py
verified:
  commit: 8dde3a6
  date: 2026-10-05
links:
  - scripts
  - tests
  - generated-regions
  - chrome-profile-refresh
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0024-pure-python-39-and-the-stdlib
  - 0004-never-pin-a-browser-impersonation-version
  - 0026-speak-chrome-from-the-stdlib-verify-by-default
---

# DuckDuckGo Bot Detection — Technical Analysis & Bypass Research

## Executive Summary

DuckDuckGo employs multi-layered bot detection that effectively blocks all known Python HTTP clients (curl_cffi, primp, requests, httpx) from scraping search results, regardless of TLS impersonation quality. Even with virtually identical TLS/HTTP2 fingerprints to a real Chrome browser, DDG's server-side detection catches non-browser clients on the lite endpoint. The most popular DDG search library (deedy5/duckduckgo_search v8.1.1) has abandoned DDG entirely, switching to Bing as default backend. SearXNG (the leading open-source metasearch engine) reports intermittent DDG CAPTCHA failures that remain unresolved as of May 2025.

Our script, and the `web` function of the `mcp-search` MCP server that carries the same generated code, use a **DDG-first with Bing auto-fallback** strategy (§7.1). Since R-0044 neither carries a third-party HTTP client, and since `8dde3a6` every DDG and Bing session opens on the Chrome-154-shaped transport (the pinned profile; Chrome 153 until R-0051), whose certificate is not verified. The certificate-verified stdlib transport the search sessions started on from R-0044, and the optional CDP backend that drove a real Chrome over the DevTools Protocol, were both removed then (§7.2, §7.3).

The first paragraph is the research position as it stood before R-0044, and it is kept as written. Two later live runs (§2.9, 2026-09-30 and 2026-10-01) got DDG lite results unblocked over a Python TLS stack; two samples from one IP do not overturn it, but they do mean "blocked on the first query" is not a given.

---

## 1. DDG Endpoints & Their Properties

| Endpoint | URL | JavaScript | Anti-bot |
|----------|-----|-----------|----------|
| **Lite** | `lite.duckduckgo.com/lite/` | None (0 scripts) | Server-side only: TLS, HTTP/2, IP, behavioral |
| **HTML** | `html.duckduckgo.com/html/` | None | Server-side (same as lite, SearXNG prefers this) |
| **Main** | `duckduckgo.com/?q=` | 51+ scripts (Next.js + dist/) | Server-side + JS fingerprint (`window.__sc__`) |
| **JSON API** | `links.duckduckgo.com/d.js` | N/A (API) | Requires valid VQD token |
| **Instant Answer** | `api.duckduckgo.com/?q=&format=json` | N/A | No full search results |

**Key insight**: The lite and HTML endpoints have **zero JavaScript**. All bot detection on these endpoints is purely server-side — no DOM parsing, no canvas fingerprinting, no browser API probing.

---

## 2. TLS/HTTP2 Fingerprint Analysis

### 2.1 Test Setup

Compared `curl_cffi` (impersonate="chrome146") against real Chrome 148 using `tls.peet.ws/api/all`.

### 2.2 Results

| Signal | curl_cffi chrome146 | Real Chrome 148 | Match? |
|--------|---------------------|-----------------|--------|
| **JA4** | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | **EXACT** |
| **Peetprint hash** | `1d4ffe9b0e34acac0bd883fa7f79d7b5` | `1d4ffe9b0e34acac0bd883fa7f79d7b5` | **EXACT** |
| **Akamai HTTP/2** | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | **EXACT** |
| **Akamai hash** | `52d84b11737d980aef856699f885ca86` | `52d84b11737d980aef856699f885ca86` | **EXACT** |
| **Cipher suites** | 16 ciphers | 16 ciphers (identical set) | **EXACT** |
| **TLS extensions** | 18 extensions | 18 extensions (identical set) | **EXACT** |
| **Supported groups** | GREASE, X25519MLKEM768, X25519, P-256, P-384 | GREASE, X25519MLKEM768, X25519, P-256, P-384 | **EXACT** |
| **JA3 hash** | `d5c429a14718597d2e28c9996512f142` | `67cf67a15ba3f9f088aca2fa3484ec75` | **DIFFERS** |
| **JA3 extension order** | `43-13-17613-11-65037-0-10-...` | `13-45-0-18-23-43-65037-16-...` | **DIFFERS** (same set, different order) |
| **User-Agent version** | Chrome/146 | Chrome/148 | **DIFFERS** (version lag) |
| **Sec-CH-UA brand** | `"Not-A.Brand";v="24"` | `"Not/A)Brand";v="99"` | **DIFFERS** |

### 2.3 Key Finding

The JA3 hash difference is **expected and harmless** — Chrome 110+ deliberately randomizes TLS extension order via GREASE, so the JA3 hash changes on every connection even for the same browser. JA4, which normalizes extension order, matches exactly.

### 2.4 Skepticism — Hash Match ≠ Byte-level Match

> **FIGYELEM: Erős kétségeink vannak, hogy a TLS fingerprinting valóban megfelelő.**

A fenti összehasonlítás **magas szintű hash-eket** vetett össze (JA4, Akamai hash, peetprint). Ezek aggregált, normalizált értékek — **nem a nyers TLS handshake byte-ok**. Ami a hash-ek mögött eltérhet:

- **TLS extension payload-ok**: A hash-ek az extension ID-ket számolják, de nem az extension-ök belső tartalmát (pl. `supported_versions` extension tartalma, `key_share` csoport méretei, ECH payload struktúra)
- **ClientHello record framing**: Record layer fragment határok, padding, record verziószám (`0x0301` vs `0x0303`)
- **GREASE értékek**: A hash-ek `GREASE`-ként összesítik, de a konkrét GREASE byte-értékek eltérhetnek (`0x1A1A` vs `0xCACA` vs `0xBABA`) — DDG szerver-oldalon az egzakt értékeket látja
- **HTTP/2 frame timing**: A SETTINGS és WINDOW_UPDATE frame-ek küldési sorrendje, timing-ja, TCP segment határai
- **TLS Encrypted Client Hello (ECH)**: curl_cffi és Chrome eltérő ECH implementációval rendelkezhet — a `tls.peet.ws` kimutatja a jelenlétet, de a payload struktúra eltérhet
- **ALPN/NPN negotiáció részletei**: Mikroszintű eltérések a protocol negotiation-ben
- **TCP segment coalescing**: A TLS ClientHello hány TCP szegmensben érkezik — egy nagy vs több kisebb szegmens más fingerprint-et ad egyes rendszereknél

**A `tls.peet.ws` összehasonlítás szükséges de NEM elégséges.** Igazi byte-szintű összehasonlítás `tcpdump` / Wireshark capture-ökkel kell, közvetlenül a wire-on, mindkét forrásból (real Chrome és curl_cffi) ugyanarra a célszerverre. Ez a tervezett következő lépés a tcpdump MCP szerverrel.

### 2.5 Wire-level Comparison (tcpdump)

**Date**: 2026-05-24
**Captures**:
- `ddg.pcap` frame #896, #3176 — real Chrome → `duckduckgo.com` (40.114.177.156)
- `ddg-curl.pcap` frame #5 — curl_cffi chrome146 → `lite.duckduckgo.com` (40.114.177.156)

#### Identical between curl_cffi and real Chrome

| Field | Value |
|-------|-------|
| **JA4** | `t13d1516h2_8daaf6152771_d8a2da3f94cd` (byte-identical) |
| **JA4_r** | Identical (same sorted ciphers + extensions + sig algos) |
| **TLS legacy_version** | `0x0303` |
| **Cipher Suites** | 16 ciphers in identical order (GREASE + AES-128/256-GCM + CHACHA20 + ECDHE + RSA) |
| **Compression Methods** | `null` only |
| **Extension count** | 18 (16 + 2 GREASE wrappers) |
| **Extension set** | Identical 18 extensions present |
| **Signature Algorithms** | 8 algos identical: ECDSA-P256/384, RSA-PSS-256/384/512, RSA-PKCS1-256/384/512 |
| **Supported Groups** | GREASE + X25519MLKEM768 + x25519 + secp256r1 + secp384r1 |
| **Key Share** | X25519MLKEM768 (1216 byte PQ key) + x25519 (32 byte) |
| **ALPN** | h2, http/1.1 |
| **ECH cipher** | HKDF-SHA256/AES-128-GCM |
| **Compress Certificate** | brotli |
| **application_settings (ALPS)** | h2 |
| **PSK Key Exchange Modes** | psk_dhe_ke (1) |
| **EC Point Formats** | uncompressed (0) |

#### Detected differences

| Field | curl_cffi chrome146 | Real Chrome | Comment |
|-------|---------------------|-------------|---------|
| **Extension ORDER** | `GREASE,11,45,65281,13,35,16,43,27,23,5,18,17613,51,0,65037,10,GREASE` | `GREASE,35,16,5,17613,23,51,13,65281,11,43,18,45,10,65037,27,0,GREASE` | Both random per connection (Chrome 110+ behavior). Could be statistically distinguishable across many connections. |
| **JA3 hash** | `9834aa9a4eb053665948f2e3555c8d11` | `e5c9b4890495471d8d69d39f25f21afa` | Consequence of extension order randomization. **Not** the detection vector. |
| **GREASE byte values** | 0x3A3A, 0xCACA, 0x2A2A, 0xEAEA | 0x4A4A, 0xBABA, 0x1A1A, 0xBABA | Both pick GREASE values from the spec range (`0x?A?A`). Both random. |
| **ECH Config ID** | **4** | **243** (#896), **218** (#3176) | DDG publishes multiple ECH configs. curl_cffi's BoringSSL may use a stale or hardcoded one. Mild concern, probably not detection. |
| **🔴 TCP Window Size** | **502** (scale 128 → eff. **64256**) | **2070** (scale 64 → eff. **132480**) | **TCP-level fingerprint difference.** Set via `SO_RCVBUF`. Distinct between libcurl and Chrome. **Captured by JA4T fingerprint.** |
| **TCP Window Scale Factor** | 128 | 64 | Kernel-derived from `SO_RCVBUF`, app-controllable |
| **SNI** | lite.duckduckgo.com | duckduckgo.com | Different endpoint used for testing — not a fingerprint issue |

#### Smoking gun candidates (ranked)

1. **🔴 TCP window size / scaling factor (JA4T)** — clear, deterministic difference
   - Real Chrome: window 2070, scale 64
   - curl_cffi/libcurl: window 502, scale 128
   - Detectable on every connection at L4 (TCP), independent of TLS layer
   - **Fixable** via `setsockopt(SO_RCVBUF)` in curl_cffi callback

2. **🟡 Extension order randomization PRNG** — possible weak signal
   - Both libraries randomize, but PRNG bias could differ
   - Not detectable per-connection, possibly aggregated detection

3. **🟢 ECH Config ID** — unlikely main vector
   - DDG accepts multiple configs simultaneously
   - More of a "stale software" indicator

4. **🟢 ALPN/HTTP2 frame ordering** — needs further investigation
   - HTTP/2 SETTINGS frame contents match (Akamai hash identical)
   - Timing/frame coalescing patterns untested

#### Key conclusion

**At the TLS handshake layer, curl_cffi chrome146 is byte-equivalent to real Chrome 146** within the bounds of expected randomization (extension order, GREASE values, random/session bytes, ephemeral keys). The JA4 fingerprint matches identically.

**The only deterministic, byte-level difference is at the TCP layer** — specifically the receive window size and scaling factor, which derive from the application's `SO_RCVBUF` socket option setting. This is a known JA4T detection vector.

**Next step**: Patch curl_cffi to set `SO_RCVBUF` to match Chrome's window (~132480 bytes effective = ~16KB buffer with scale 64), recapture, verify JA4T alignment, and test against DDG.

### 2.6 SO_RCVBUF Shim Experiment (Failed)

**Date**: 2026-05-24

#### Implementation

LD_PRELOAD C shim (`/tmp/tcp_window_shim.c`) wrapping `socket()` to call `setsockopt(SO_RCVBUF)` before `connect()`. Configurable via `TCP_WINDOW_SHIM_RCVBUF` env var.

```bash
TCP_WINDOW_SHIM_RCVBUF=262144 LD_PRELOAD=/tmp/tcp_window_shim.so python3 script.py
```

#### Wire-level results (ddg-curl-shim.pcap)

| Source | Window | Scale | Effective | JA4 |
|--------|--------|-------|-----------|-----|
| Real Chrome | 2070 | 64 | 132480 | `t13d1516h2_8daaf6152771_d8a2da3f94cd` |
| curl_cffi (no shim) | 502 | 128 | 64256 | `t13d1516h2_8daaf6152771_d8a2da3f94cd` |
| curl_cffi (SO_RCVBUF=262144) | 16384 | 4 | 65536 | `t13d1516h2_8daaf6152771_d8a2da3f94cd` |

The shim changed the window/scale combination but **did not match Chrome's exact (2070, 64) values**. Reason: Linux kernel auto-selects window scale shift based on `sk_rcvbuf` size, capped by `net.core.rmem_max`. On the test system `rmem_max=212992` caps actual `sk_rcvbuf` at 425984 regardless of `SO_RCVBUF` value, yielding shift=2 (scale=4). To get Chrome's shift=6 (scale=64), `sk_rcvbuf` needs to be ~4MB, requiring `sysctl net.core.rmem_max=4194304` (root privilege needed).

#### CAPTCHA test outcome — STILL BLOCKED

```
Status: 202, len: 14235, CAPTCHA: True
```

That 202 is still the only challenge answer this page has recorded, and it was judged by a body substring. The R-0044 live runs of 2026-09-30 and 2026-10-01 (§2.9) never received a challenge page on any transport, so the structural location of the challenge element — the thing a block predicate should match instead of a substring — is **unmeasured**; the predicate that shipped is the fallback described in §2.9, and since R-0052 that 202 is itself a block signal there.

**DDG still returned the anomaly modal even with:**
- Identical JA4 (`t13d1516h2_8daaf6152771_d8a2da3f94cd`)
- Identical JA4_r (sorted cipher/extension/sigalg sets)
- Identical HTTP/2 SETTINGS frame (Akamai hash)
- Modified TCP window/scale closer to Chrome
- All curl_cffi auto-generated headers preserved (no manual overrides)
- Only `Accept-Language` and per-request `Referer` set by us

#### Conclusion: TCP window is NOT the primary detection vector

**Three independent test outcomes:**
1. curl_cffi default (window 502/scale 128) → CAPTCHA
2. curl_cffi with shim (window 16384/scale 4) → CAPTCHA
3. Real Chrome (window 2070/scale 64) → SUCCESS

Window size and scale differ across all three, yet Chrome succeeds while both curl_cffi variants fail. If TCP window were the discriminator, at least one of the variants should have produced a different DDG response. Both got CAPTCHA.

**This rules out**:
- JA4T fingerprinting as the primary detection mechanism (TCP-level)
- All known TLS-layer fingerprints (JA3, JA4, JA4_r, Akamai HTTP/2)
- HTTP-layer header values (Sec-Fetch already correct in curl_cffi auto-generated)
- TLS extension contents (byte-identical at the parsed-field level)

#### What's left as the detection vector

Given that byte-level TLS impersonation is **demonstrably insufficient**, the remaining candidates are:

1. **IP reputation / behavioral pattern across past requests**
   - Our IP has been making curl_cffi requests for days
   - DDG may track per-IP request "personality" (timing, retry patterns, success/failure ratios)
   - A single request from a clean IP might pass; a flagged IP will fail regardless of TLS

2. **Subtle TLS-layer behavior we haven't measured**
   - TLS extension randomization PRNG fingerprint (statistical, requires many connections)
   - HTTP/2 frame timing/coalescing patterns (sub-millisecond)
   - TCP-layer behavior: retransmission patterns, ACK timing, MSS clamping
   - Connection reuse vs fresh handshake patterns

3. **DDG maintains a behavioral signature database of known scraping tools**
   - May correlate libcurl version + curl_cffi patch signatures
   - This would be a "known-tool blocklist" rather than a fingerprint test

4. **HTTP/2 layer differences not yet captured**
   - We have not byte-diffed the encrypted HTTP/2 frames after handshake
   - SETTINGS frame ordering, WINDOW_UPDATE timing, HEADERS frame structure
   - Akamai hash matches at the aggregate level but individual frames could differ

#### Final assessment

**Byte-level TLS impersonation from Python is not achievable to a level that bypasses DDG.** The detection operates at a higher abstraction than JA4/JA4T. Pursuing this path further (HTTP/2 frame analysis, sub-millisecond timing, statistical PRNG analysis) is unlikely to yield a Python-pure solution.

**Pragmatic conclusion confirmed**: The script's current architecture (DDG-first with Bing auto-fallback + opt-in CDP via real Chrome) remains the correct approach. byte-level TLS work is interesting forensically but does NOT unlock DDG access.

### 2.7 HTTP/2 Header Layer Analysis (Bug Found)

**Date**: 2026-05-24

After confirming TLS handshake is byte-equivalent, we enabled `CURLOPT_VERBOSE=1` to inspect the **HTTP/2 frames that curl_cffi sends** after the TLS handshake completes.

#### Connection establishment (from `curl -v` output)

```
* Cipher selection: TLS_AES_128_GCM_SHA256:... (matches Chrome list)
* ALPS: offers h2                              ← Application-Layer Protocol Settings
* ECH: requested but no ECHConfig available
* ECH: falling back to GREASE                  ← curl_cffi sends FAKE ECH
* ALPN: curl offers h2,http/1.1
* SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384
* ALPN: server accepted h2                     ← HTTP/2 confirmed
* using HTTP/2
```

#### HTTP/2 HEADERS frame sent by curl_cffi (verbose dump)

```
:method: POST
:authority: lite.duckduckgo.com
:scheme: https
:path: /lite/
sec-ch-ua: "Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "macOS"
upgrade-insecure-requests: 1
user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36
            (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36
accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,
        image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7
sec-fetch-site: none                           ← 🔴 BUG (see below)
sec-fetch-mode: navigate
sec-fetch-user: ?1
sec-fetch-dest: document
accept-encoding: gzip, deflate, br, zstd
accept-language: en-US,en;q=0.9
priority: u=0, i
referer: https://lite.duckduckgo.com/lite/    ← Referer is set
content-length: 18
content-type: application/x-www-form-urlencoded
```

#### Discovered issues at HTTP layer

##### 🔴 Issue 1: Sec-Fetch-Site / Referer contradiction

`Sec-Fetch-Site: none` means **no referrer / direct user navigation** (typed URL, bookmark, app launch). But the same request includes `Referer: https://lite.duckduckgo.com/lite/`.

**A real Chrome browser submitting a form on lite.duckduckgo.com to itself would send:**
- `Sec-Fetch-Site: same-origin` (because Referer matches destination)
- `Sec-Fetch-Mode: navigate`
- `Sec-Fetch-User: ?1`
- `Sec-Fetch-Dest: document`

curl_cffi's chrome146 profile **hard-codes `Sec-Fetch-Site: none`** regardless of context. This creates a header coherence violation — SearXNG documented Sec-Fetch checks as one of DDG's detection mechanisms.

**Test outcome**: Overriding `Sec-Fetch-Site: same-origin` per-request did NOT bypass CAPTCHA in our test environment, but the IP was already heavily rate-limited. This is still a real bug that should be fixed for first-contact requests on clean IPs.

##### 🟡 Issue 2: ECH GREASE vs real ECHConfig

```
* ECH: requested but no ECHConfig available
* ECH: falling back to GREASE
```

curl_cffi sends **GREASE-ECH** (fake camouflage payload) because it didn't fetch the real ECHConfig from DNS HTTPS records. Real Chrome:
1. Performs DNS HTTPS query (`HTTPS` resource record) for the target hostname
2. Extracts the `ech=` value (real ECHConfig)
3. Uses real ECH in ClientHello

In our captures:
- curl_cffi: ECH Config ID 4 / 145 (varying — GREASE) with 208-byte fake payload
- Real Chrome: ECH Config ID 243 / 218 (DDG's actual configs) with 208-byte real payload

While both have the same payload SIZE (the GREASE algorithm matches Chrome's behavior), the **Config ID space** differs. Real Chrome uses IDs from DDG's published list; curl_cffi's GREASE uses random IDs.

DDG could potentially detect "GREASE ECH" vs "real ECH" by checking if the Config ID matches one of its currently published configs. **This is a structural difference, not just timing or randomness.**

##### 🟡 Issue 3: User-Agent / OS / TCP stack mismatch

curl_cffi running on Linux but claims to be Chrome on macOS:
- `User-Agent: Macintosh; Intel Mac OS X 10_15_7 ... Chrome/146.0.0.0`
- `sec-ch-ua-platform: "macOS"`
- **Actual OS: Linux** (visible via TCP TTL=64, TCP options ordering, kernel-derived window scaling)

A real Chrome on macOS would have TCP TTL=64 (same as Linux) but different TCP option ordering and kernel behavior. A real Chrome on Linux would have UA showing X11/Linux. This **UA-vs-network-stack inconsistency** is detectable via:
- p0f-style passive OS fingerprinting
- TTL initial value (macOS=64, Linux=64, Windows=128 — Mac/Linux indistinguishable on TTL alone)
- TCP options order (different per OS)
- Initial window size (we documented this is different)

This mitigation was why the scripts had a Linux branch at all: curl_cffi had no OS
knob, so Linux switched to primp with `impersonate_os="linux"`, which derived the
`X11; Linux x86_64` UA and the matching `sec-ch-ua-platform` from that one argument
(as of 2026-08-04; before that the coherence was hand-built — a pinned Chrome major
plus a matching Linux Chrome header dict). **Since R-0044 there is no Linux branch**:
the stdlib client sends one measured macOS Chrome profile (154 since R-0051) on every platform, so on
Linux this tell is back, accepted as a declared cost (§7.2).

##### 🟢 Issue 4: sec-ch-ua brand string format

| Source | sec-ch-ua value |
|--------|-----------------|
| curl_cffi chrome146 | `"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"` |
| Real Chrome 148 | `"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"` |

Chrome uses **randomized GREASE brand names** that rotate over versions: `"Not-A.Brand"`, `"Not/A)Brand"`, `"Not_A Brand"`, `"Not?A_Brand"`, etc. The brand list order also differs (curl_cffi: Chromium → Not-A.Brand → Chrome; Chrome 148: Chromium → Chrome → Not/A)Brand).

This is a **per-version GREASE pattern** that curl_cffi's chrome146 profile may have slightly stale. Probably not the main detection vector but contributes to fingerprint mismatch in aggregate.

#### Summary of HTTP layer findings

| #  | Issue | Severity | Fix difficulty |
|----|-------|----------|----------------|
| 1  | `sec-fetch-mode: navigate` on POST (see §2.8) | 🔴 CRITICAL (the actual bot signature) | Easy: per-request XHR pattern override |
| 1' | `Sec-Fetch-Site: none` with Referer set | 🟡 SECONDARY (coherence violation, subsumed by #1's fix) | Easy: per-request override |
| 2  | GREASE-ECH vs real ECHConfig | 🟡 MEDIUM | Hard: requires DNS HTTPS query |
| 3  | UA claims macOS, OS is Linux | 🟡 REOPENED on Linux since R-0044 (was solved by primp's Linux mode) | Accepted, §7.2 |
| 4  | Stale GREASE brand strings | 🟢 LOW | Maintained by curl_cffi upstream |

**The user's question answered**: curl_cffi DOES use HTTP/2 (`* using HTTP/2`). After the byte-equivalent TLS handshake, the divergence is in the HTTP/2 HEADERS frame — specifically the Sec-Fetch header coherence and OS/platform consistency with the underlying network stack.

#### Actionable fixes for the script

1. **PRIMARY (done)**: Switch DDG POST headers to the Chrome XHR pattern — `cors` / `empty` / `*/*` / `u=1`. See §2.8.
2. **Secondary (done)**: Override `Sec-Fetch-Site: same-origin` and Referer to a same-origin URL on the same POST (subsumed by #1).
3. **Done, then undone**: on Linux hosts the Linux Chrome UA came from primp's
   `impersonate_os="linux"`; since R-0044 one macOS profile is sent everywhere (§7.2).
4. **Optional**: Add DNS HTTPS query for real ECHConfig (complex, may need patches to curl_cffi).

### 2.8 Breakthrough: `sec-fetch-mode: navigate` vs `cors` — The Real Discriminator

**Date:** 2026-05-24

After exhausting TLS-layer hypotheses (JA4 match, TCP window shim, Akamai HTTP/2 match) and the Section 2.7 `Sec-Fetch-Site` coherence fix alone, the script still hit CAPTCHA. We captured a **real Chrome lite POST** via Chrome DevTools Protocol Network domain (`Network.requestWillBeSentExtraInfo` event) on a host where Chrome **passes lite without challenge**:

| Header                       | Real Chrome (XHR fetch)            | curl_cffi/primp default (navigation)         |
|------------------------------|------------------------------------|----------------------------------------------|
| `accept`                     | `*/*`                              | `text/html,application/xhtml+xml,…`          |
| `sec-fetch-mode`             | **`cors`**                         | **`navigate`**                               |
| `sec-fetch-dest`             | `empty`                            | `document`                                   |
| `sec-fetch-user`             | (absent)                           | `?1`                                         |
| `upgrade-insecure-requests`  | (absent)                           | `1`                                          |
| `priority`                   | `u=1, i`                           | `u=0, i`                                     |
| `referer`                    | `https://lite.duckduckgo.com/`     | `https://lite.duckduckgo.com/lite/`          |

Chrome's POST originates from `fetch()` inside the lite page's JavaScript (the lite UI intercepts the `<form>` submit and re-issues the POST as an XHR). This produces a textbook XHR pattern: `cors` / `empty` / `*/*` / `u=1`.

curl_cffi and primp, by default, emit navigation-pattern headers for any POST because their Chrome impersonation profile assumes a top-level navigation / form submit. **DDG treats `sec-fetch-mode: navigate` POST as a bot signature** — most headless scrapers and HTTP clients emit exactly this pattern, while real-world humans on lite end up sending XHR via the page JS.

This finally reconciles SearXNG's earlier-discovered "Sec-Fetch-Mode is one method DDG uses to block bots" (cf. Section 3.1) with the actual mechanics: it is not that `Sec-Fetch-Mode` must be present — it is that for **POSTs from `/lite/`** the value must be `cors`, not `navigate`.

#### Fix

`search_ddg()` overrides per-request headers to the Chrome XHR pattern:

```python
headers={
    "Accept": "*/*",
    "Referer": "https://lite.duckduckgo.com/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Priority": "u=1, i",
}
```

#### Measured Impact

- **Before XHR fix**: 100% CAPTCHA on lite POSTs (curl_cffi chrome146 / primp Linux mode with navigation defaults).
- **After XHR fix**: ~80% pass-through on lite POST; remaining ~20% (typically after session rotation or rapid sequential queries) degrade gracefully via Bing fallback (`_run_ddg_with_bing_fallback`).

#### Remaining Coherence Gaps (Empirically Resolved: Keep the Leak)

Two navigation-only headers ride along into the XHR POST, because a per-request override cannot *delete* a header the session level already carries:

- `upgrade-insecure-requests: 1` — Chrome XHR omits this entirely (UIR is a navigation-only directive).
- `sec-fetch-user: ?1` — only emitted on user-initiated top-level navigation.

**Empirical finding (2026-05-24)**: removing both was tested and **degraded** the DDG success rate from the post-fix ~80% baseline, so the committed code (256c6ae) kept them. Likely mechanism: the warmup `GET https://lite.duckduckgo.com/lite/` is a top-level navigation — there a real Chrome would emit UIR + sec-fetch-user, so their absence in the warmup phase becomes a *worse* coherence violation than their unwanted presence on the subsequent XHR POST. Conclusion: in this script's two-request pattern (nav warmup → XHR POST), navigation-mode defaults at the session level plus a per-request XHR override on the POST is the right shape.

#### Where Those Two Headers Come From Now (2026-08-04)

**The measurement above still stands and both headers are still on the wire.** What changed is their source: the hand-written `_linux_chrome_headers()` dict that the 2026-05-24 test edited **no longer exists** in any of the three impersonating files `Scripts/search_duckduckgo.py` `Scripts/search_github.py` `Scripts/mcp-webfetch.py`. Read every mention of that identifier above as history, not as live code. It was deleted for being redundant and inert — *not* for being unwanted:

- **Redundant**: measured against primp 1.3.1, primp auto-injects all 13 of the dict's keys, 11 of them character-identical — *including* these two. The dict's stated premise ("primp does not auto-inject over HTTP/2") is false for 1.3.1, which injects a complete navigation set plus `sec-ch-ua-platform` derived from `impersonate_os`.
- **Inert**: re-measured with sentinel values, client-level `headers=` loses *every* conflict against `impersonate=`. Neither override reached the wire even before the deletion. So the 2026-05-24 degradation was never evidence that our dict *supplied* those headers — only that primp's navigation defaults, which the dict happened to duplicate, are load-bearing for DDG `Scripts/search_duckduckgo.py:create_session`.

One staged value is rewritten in flight and it is **not ours to fix**: primp stages `accept-encoding: gzip, deflate, br, zstd` (character-identical to the old dict) and its transport then rewrites the outgoing value to `gzip, br`, matching what it can actually decode. No header we set changes that.

**If DDG throughput ever regresses, suspect these two before suspecting a missing key**: that `accept-encoding` rewrite, and header **order** — order is itself a fingerprint, and an echo endpoint cannot reveal it, so "11 of 13 character-identical" says nothing about the sequence DDG sees them in.

**Since R-0044 this subsection is history.** primp is gone, and with it both the
`accept-encoding` rewrite and the unmeasurable order: the stdlib client sends the
navigation and cors profiles in the order and values captured from the pinned
Chrome (153 at R-0044, 154 since R-0051; §2.9), `accept-encoding: gzip, deflate, br, zstd` included, because it now decodes
all four. The navigation warm-up still carries `upgrade-insecure-requests` and
`sec-fetch-user`, which the 2026-05-24 measurement found load-bearing; the cors POST
no longer does, because Chrome's captured `fetch` does not send them, so the "leak"
that subsection decided to keep no longer exists to be kept. Whether DDG's pass rate
moved with it is not measured.
### 2.9 Loopback Fingerprint Measurement vs Chrome 153, and a Pure-Python PoC (R-0017)

**Date**: 2026-09-29/30
**Host**: macOS; python3 3.14 / OpenSSL 3.6.0, curl_cffi 0.15.0, primp 1.3.1, real Chrome 153.0

§2.2–§2.5 compared hashes from `tls.peet.ws` and an off-host capture that cannot see inside TLS. This round put a TLS + h2 server on loopback that terminates the connection itself, so it sees the whole ClientHello **and** the decrypted h2 preface, SETTINGS, WINDOW_UPDATE and HEADERS (HPACK decoded). The dump server, the diff report and the PoC began as throwaway artefacts under the git-ignored `.claude/tmp/` scratch directory (`clienthello_dump.py`, `chdump/diff_report.md`, `chdump/sni/sni_report.md`, `chpoc/chrome_poc.py`). Since R-0044 two of them are in the repo: the dump server became the capture harness `Scripts/chrome_capture.py`, and the PoC became the canonical source `Scripts/_mcp_chrome.py`, generated into `Scripts/search_duckduckgo.py`, `Scripts/search_github.py` and `Scripts/mcp-webfetch.py` ([[generated-regions]]). The subsections down to "Live result — one sample" are the R-0017 measurement as it was taken; the ones after it are the R-0044 captures and live results.

#### Method

- Real Chrome was driven through the gdc MCP: 30 connections (12 fresh, 18 resumed; 4 of them top-level document navigations). Every client configuration got 20 connections.
- Chrome's captures are GREASE-normalised and compared per feature as *constant* vs *varying* across connections; a difference counts as **fixed** only when it holds on every connection.
- **JA3 is useless for Chrome-family clients**: Chrome shuffles the extension order per connection, so JA3 (and JA4_o) took a distinct value on every one of its 12 fresh connections. **JA4**, which sorts the extensions, is the stable key and is what the tables below use.

#### Chrome 153 reference

| Signal | Value |
|--------|-------|
| JA4, fresh | `t13d1517h2_8daaf6152771_cb7bf5808d99` |
| JA4, resumed | `t13d1518h2_8daaf6152771_e2d80978ab2e` — adds `pre_shared_key` (41), always last |
| Akamai h2 | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` |
| HEADERS frame (navigation) | flags `0x25`, priority exclusive, weight 256 |

Chrome sends 17 non-GREASE extensions, not the 16 of §2.5: the newcomer is **51764 / `0xca34`**, Trust Anchor Identifiers, with a 186-byte payload that was byte-constant across all 30 Chrome ClientHellos. `signature_algorithms` now starts with `GREASE, 0904, 0905, 0906` (ML-DSA) before the eight classic algorithms.

#### curl_cffi 0.15.0 (`chrome146`, its newest Chrome target)

At the TLS layer it differs from Chrome 153 in exactly two fixed ways, and everything else (ciphers, groups, key shares, ALPN, ALPS `h2`, ECH GREASE lengths, extension shuffling) falls inside Chrome's own variation:

1. extension 51764 (Trust Anchor Identifiers) is absent, and
2. `signature_algorithms` lacks the `GREASE, 0904, 0905, 0906` prefix.

That is why its JA4 is `t13d1516h2_8daaf6152771_d8a2da3f94cd` — the value §2.2 and §2.5 recorded as an *exact* match against Chrome 146/148, and which Chrome 153 no longer produces. Its h2 SETTINGS, WINDOW_UPDATE, HEADERS flags/priority and pseudo-header order match Chrome's navigation exactly; the remaining h2 differences are the version-bearing header values (`user-agent`, `sec-ch-ua`) and `accept-language`. `chrome136` and `chrome145` show the same TLS picture.

#### primp 1.3.1 — fixed tells

These are properties of primp's impersonation itself, visible on every connection and not fixable by picking another version string:

- **Frozen extension order.** Chrome shuffles per connection; primp sends one constant order, so JA3 is constant across all 20 connections — a static signature Chrome never produces.
- **ALPS payload `000403c9bb32`.** Chrome sends `0003026832` (`h2`); primp advertises a bogus protocol id instead of `h2`.
- **ECH GREASE payload lengths are not bucketed.** Chrome only ever uses {144, 176, 208, 240}; primp's `chrome` group produced 16 distinct lengths in 20 connections, and 18 of those 20 connections used a length Chrome never sends.
- **HPACK Huffman-codes every literal.** Chrome sends several values raw (e.g. `sec-ch-ua-mobile`, `sec-ch-ua-platform`, `upgrade-insecure-requests`, `sec-fetch-user`); primp Huffman-codes all of them.
- **Header order is wrong for `chrome_148`** (`user-agent` at position 5, `sec-ch-ua` at 11, where Chrome has 8 and 4); the bare `chrome` / `edge` aliases hit that order on part of their connections.
- **The `chrome` / `edge` aliases pick a random major per `Client`** (5 distinct UAs in 20 connections) — by design for the bare-alias whitelist the search script then kept, but it means no two clients look alike.
- `verify=False` is ignored; reaching a self-signed loopback server needs `ca_cert_file`.

When this was measured it mattered for the Linux branch of both impersonating scripts, which was primp by design, and it added a reason beyond silent version rot to [[0004-never-pin-a-browser-impersonation-version]]'s distrust of primp: its fingerprint carries tells a pin cannot fix. Since R-0044 no script uses primp or curl_cffi; see §7.2.

#### ECH GREASE length vs SNI length

Measured separately over 109 Chrome connections (48 fresh, 61 resumed) with SNI lengths from 11 to 70 bytes:

- the ECH GREASE payload is **always one of {144, 176, 208, 240}**, regardless of SNI length;
- ECH extension length = payload + 42, `enc` length 32;
- fresh ClientHello length (handshake message incl. its 4-byte header) = **1759 + len(SNI) + payload**, with no padding extension.

So Chrome does not size its GREASE ECH by the SNI; a client reproducing it needs only the four-value choice, not an SNI-dependent size.

#### The PoC: a pure-stdlib Chrome 153 client

A single stdlib-only Python file (Python 3.9 compatible, roughly 2200 lines on 2026-09-30), everything in it copied from the Chrome captures rather than guessed:

- pure-Python X25519, **ML-KEM-768** (FIPS 203; cross-checked in both directions against OpenSSL 3.6 — OpenSSL encapsulates / PoC decapsulates and the reverse), AES-GCM, ChaCha20-Poly1305 and the TLS 1.3 key schedule;
- the Chrome 153 ClientHello, including extension shuffling, the ECH GREASE buckets, ALPS `h2` and extension 51764;
- an HPACK encoder following quiche's decisions (Huffman only when strictly shorter) that reproduces all 12 captured Chrome header blocks byte-identical;
- an h2 client that follows same-origin redirects as new streams on the same connection, with a basic cookie jar.

**Loopback result**: JA4 matched Chrome on every connection. The diff report lists **no fixed TLS difference** against Chrome and, at the h2 level, only `cache-control: max-age=0` — which Chrome sent because its capture was a reload, and which the PoC omits on purpose because a typed navigation does not send it. The only other residue is `peetprint_md5`, a hash over the *random* GREASE sigalg value, which fell outside the 12-sample Chrome set on 6 of 20 connections — a sample-size artefact, not a structural difference. An `openssl s_server` matrix (X25519MLKEM768, X25519, all three TLS 1.3 suites) passed. Handshake median ~115 ms, of which ML-KEM decapsulation is ~58 ms.

**Not done, and it matters**: the certificate chain and CertificateVerify are **not verified** (loopback-only by intent); the client-side ALPS EncryptedExtensions message is not implemented (DDG did not negotiate ALPS, so it was not exercised); `br` / `zstd` bodies cannot be decoded with the stdlib.

#### Live result — one sample

User-authorized, 3 requests in total. `lite.duckduckgo.com` negotiated X25519MLKEM768 + `TLS_AES_256_GCM_SHA384`, ALPS not negotiated. `GET /lite/?q=test` answered 302; following the redirect returned a brotli-encoded 200, "test at DuckDuckGo", with 10 result links and no anomaly / CAPTCHA markers.

**This is a single sample.** It shows a Chrome-153-shaped ClientHello and h2 preface from pure Python *can* get a result page; it does not show that the fingerprint is what DDG gates on — §2.6 and §2.8 found request shape and IP history mattering more than TLS, and one GET from an unburnt session tests neither.

#### The Chrome 153 capture set (R-0044, 2026-09-30)

The R-0017 dump was one navigation set. Before the PoC became `Scripts/_mcp_chrome.py`, Google Chrome 153.0.8010.37 on macOS 14.2.1 was captured again on loopback with `Scripts/chrome_capture.py serve`, driven over CDP, in ten sets: typed navigation, reload, a navigation followed on the same connection by a same-origin form POST, a cors GET and a cors HEAD, HTTP/1.1 over TLS, plaintext `http://`, an IP-literal origin, navigations carrying a server-set cookie, and a ClientHello answered with a HelloRetryRequest. Every set was committed, one reduced fixture per connection, under `tests/files/chrome/` as its `153/` subdirectory, whose README carried the full profile table; R-0051 retired that directory when the Chrome 154 set below replaced it. What the capture added to the R-0017 picture:

- **The fresh JA4 did not move**: `t13d1517h2_8daaf6152771_cb7bf5808d99`, the R-0017 value, on every fresh ClientHello with an SNI. An IP-literal origin sends no `server_name` and fingerprints as `t13i1516h2_8daaf6152771_cb7bf5808d99`; the second ClientHello after a HelloRetryRequest is `t13d1518h2_8daaf6152771_6ba8dc3d6269`.
- **Extension 51764's payload is byte-identical** on every TLS connection of every set and on every post-HRR ClientHello, so the client may send it as a constant.
- **The HRR answer has a fixed shape**: a ChangeCipherSpec record before the second ClientHello, every time; ONE key share for the group the server asked for; the server's cookie echoed, inserted at a position that varies per connection; random, session id, ciphers, GREASE values and the ECH GREASE payload unchanged from the first ClientHello.
- **A page's fetch is not stream 3.** On a navigate-then-fetch connection Chrome first opens stream 3 (`/.well-known/appspecific/com.chrome.devtools.json`) and stream 5 (`/favicon.ico`), so the page's `fetch` was stream 7 in 153, with priority weight 220 and **no `origin` header** on the same-origin POST. (Under 154 the devtools.json request is gone and the fetch is stream 5; see below.)
- **HTTP/1.1 carries no `priority` header**, keeps the `sec-ch-*` names lowercase and every other name in canonical case.
- **No resumed handshake was ever completed** against the untrusted loopback certificate, so the capture contains no PSK connection — and the client implements no resumption.

#### The Chrome 154 capture set (R-0051, 2026-10-02)

The current pin. Google Chrome 154.0.8037.58 on macOS 14.2.1 (x86) was captured on loopback over gdc/CDP in the same ten sets, and the set is committed under `tests/files/chrome/154/`, whose README carries the profile table and every comparison against 153. Against the 153 sets `chrome_capture.py diff` reports exactly three fixed differences, the same in every set:

- `user-agent`: `Chrome/154.0.0.0`;
- `sec-ch-ua`: `"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"` — the brand order and the GREASE brand changed, not only the version;
- extension 51764 (Trust Anchor Identifiers): the same 186-byte length, a different payload, again byte-identical on every TLS connection and every post-HRR ClientHello.

The JA4 values did not move: `t13d1517h2_8daaf6152771_cb7bf5808d99` fresh with an SNI, `t13i1516h2_8daaf6152771_cb7bf5808d99` for the IP literal, `t13d1518h2_8daaf6152771_6ba8dc3d6269` for the CH2 after an HRR. The h2 SETTINGS, WINDOW_UPDATE, priority, header orders, cipher list, extension set and ClientHello lengths are unchanged. Two capture-side changes: no 154 connection requested `/.well-known/appspecific/com.chrome.devtools.json`, so the page's `fetch` moved from stream 7 to stream 5 (whether that is Chrome or the driven tab's DevTools state was not determined); and each kept connection was preceded by three failed attempts instead of two. Both are in [[chrome-profile-refresh]], with the test bug the moved stream exposed.

Refreshing the set when Chrome moves is [[chrome-profile-refresh]].

#### Live results over both transports (R-0044 Gate G6, 2026-09-30)

User-authorized, 18 live requests out of a budget of 24, run by a scratch driver whose record is `.claude/tmp/task038-live.txt` (git-ignored, like the R-0017 artefacts). Each request was sent three ways: over the Chrome path (`transport="chrome"`), over the session's default verified transport (`transport="verified"`, Python's own `ssl` ClientHello under Chrome 153's headers, HTTP/1.1, certificate checked), and over the curl_cffi `chrome146` backend the scripts still used that day, as a baseline. The verified rows are the (a′)–(c′) records the block signals were decided on.

| Request | Chrome path | Verified transport | curl_cffi baseline |
|---|---|---|---|
| (a) DDG `GET /lite/` (warm-up) | 200, 0 results | (a′) 200, 0 results | 200, 0 results |
| (a) DDG `POST /lite/` `q=test` | 200, **10 results** | (a′) 200, **10 results**, no challenge marker | 200, 10 results |
| (b) Bing search `GET` | 200, 10 results | (b′) 200, **10 results** | 200, 10 results |
| (c) grep.app API `GET` (cors) | 200 JSON, 10 hits | (c′) **429 `text/html`, not JSON**, 33944 bytes | 200 JSON, 10 hits |
| grep.app `GET /` | not sent | (c′) **429 `text/html`**, 33944 bytes | 200 `text/html` |
| (d) `GET https://www.cloudflare.com/` | 200 HTML | 200 HTML, `cert_verified=True` | 200 HTML |
| (e) `GET https://www.google.com/` | 200 HTML, **ALPS negotiated** | not sent | not sent |

- **Loopback**: against `chrome_capture serve` the client's JA4 was `t13d1517h2_8daaf6152771_cb7bf5808d99`, equal to Chrome's, as computed by the capture harness's own parser.
- **Chrome path**: every connection negotiated X25519MLKEM768 and h2; DDG's GET and POST travelled on one pooled connection; only Google negotiated ALPS, and the client-side ALPS answer §2.9's PoC lacked completed.
- **No DDG challenge page anywhere.** Neither `anomaly-modal` nor "Please complete" appeared in any DDG answer, on any of the three transports.
- **grep.app is the one endpoint that told the transports apart**: the verified transport's first cold request got a 429 `text/html` page that is not JSON, while the Chrome path and the baseline got 200 JSON seconds apart. A rate limit would not discriminate by TLS stack, so this was read as a fingerprint block.

What each search script now treats as a block was decided from those rows, and only an answer from the endpoint's own host is ever judged; a transport failure on the verified path never counts, so breaking the verified handshake cannot force the unverified path:

- **grep.app** — measured: a 403, OR a 429 whose body is a non-JSON `text/html` page, OR a 200 whose body is not JSON, provided the body decoded; a 429 carrying JSON stays a rate limit `Scripts/search_github.py:_grep_app_blocked`.
- **Bing** — not measured: Bing answered the verified transport with results, so the 403 rule is declared rather than observed, and a challenge served as a 200 that parses to nothing is not detected `Scripts/search_duckduckgo.py:_bing_blocked`.
- **DDG** — partly measured, and still not the structural match the plan wanted: since no challenge page was observed, the challenge *element* could not be located. What shipped requires the answer to come from `lite.duckduckgo.com` and to parse to **zero** results. On top of that it needs one of two signals. The first is an HTTP **202**, the one challenge answer this page recorded (§2.6), which the user has since seen live; lite results answer 200 (R-0052). The second is the **fallback** predicate: a challenge marker the query does not itself contain. Neither a reflected query nor a marker inside a third-party snippet (a page with a snippet has a result) can trip it `Scripts/search_duckduckgo.py:_ddg_blocked`. Replacing the marker test with the element is open until a real challenge page is recorded.

#### The fallback, live (R-0044, grep.app)

One grep.app query run through `Scripts/search_github.py` as shipped (`.claude/tmp/task042-live.txt`): the verified client was blocked, the script said so on one stderr line, re-issued the query **once** over the Chrome path and printed 10 results under `**Transport**: chrome (certificate NOT verified)`.

#### What the verified-first probe costs, live (R-0044, 2026-10-01)

User-authorized, both legs of `Scripts/search_duckduckgo.py` as shipped, one query each, every request hop counted by a scratch driver (`.claude/tmp/task044-live.txt`):

| Leg | Requests | Transport | Block | Results |
|---|---|---|---|---|
| DDG (default backend) | 2 (warm-up GET, POST) | verified | no | 10 |
| Bing (`DDG_BACKEND=bing`) | 2 (warm-up GET, search GET) | verified | no | 10 |

No Chrome-path request was made and no "certificate NOT verified" label was printed. That is the case the probe exists for: when an endpoint does not block, a run costs exactly the requests the Chrome-default design would have sent, and every one of them was authenticated. When DDG did block, the arithmetic was one extra round for the run, then the Chrome path for the rest of it. That probe no longer exists: since `8dde3a6` every search session is on the Chrome path from the start (§7.1).

**An observation, not a finding.** The executive summary's claim that DDG blocks every Python client did not reproduce in these two runs: on 2026-09-30 and again on 2026-10-01 the verified transport — Python's TLS stack, not Chrome's — got lite results with no block. That is two samples from one IP on two days, and the history in §2.6–§2.8 says IP history and request volume decide more than any single request; it does not show that DDG has stopped blocking, only that a Python TLS stack is not blocked on sight.


---

## 3. What DDG Actually Detects (Server-side, Lite/HTML Endpoints)

Based on SearXNG documentation, library source code analysis, and community research:

### 3.1 Sec-Fetch Header Coherence

SearXNG discovered this as a key detection signal (PR #3965, Oct 2024):

> "Sec-Fetch-Mode is one method DDG uses to block bots .. it was the first place I have seen Sec-Fetch-Mode."

Required headers:
```
Sec-Fetch-Dest: document
Sec-Fetch-Mode: navigate
Sec-Fetch-Site: same-origin
Sec-Fetch-User: ?1
```

These are `Sec-` prefixed headers that real browsers generate automatically with correct values. DDG checks for **coherence** — do the values match the actual request context?

### 3.2 VQD Token Lifecycle

- VQD = Validation Query Digest, generated from hash of **query + User-Agent** combined
- Required for all pagination (page 2+)
- Cached with 3600-second expiration
- Requesting pagination without valid VQD = immediate bot detection
- Changing User-Agent between requests for the same query session invalidates VQD

### 3.3 IP-Based Rate Limiting (Sliding Window)

From SearXNG documentation:

> "In the past, the IP blocking was implemented as a 'sliding window' (unblock after about 1 hour without requests from this IP)"

- Burst traffic from same IP triggers CAPTCHA
- Block lifts after ~1 hour of inactivity
- Opening DDG in a real browser from the blocked IP can sometimes clear the block (not always)
- DDG does NOT use client sessions — blocking is purely IP-based

### 3.4 Behavioral Analysis (Suspected)

Evidence suggesting behavioral detection:
- Rapid pagination triggers blocks more than single-page searches
- Request timing patterns matter
- Advanced search syntax queries trigger CAPTCHA more frequently
- Chinese locales are blocked from pagination entirely (hardcoded in SearXNG)

### 3.5 TCP/IP Fingerprinting (Measured, Not the Deciding Factor)

This section used to say that a Python script and Chrome on the same host send **identical** TCP SYNs. The page's own measurements say otherwise, so only the measured part is kept:

- **What is OS-level and therefore shared on one host**: initial TTL and TCP option ordering (§2.7 Issue 3). These tell the OS, not the client.
- **What is not**: the receive window and window scale derive from the application's `SO_RCVBUF`. The 2026-05-24 captures measured curl_cffi at window 502 / scale 128 and real Chrome at 2070 / 64 (§2.5), so JA4T *can* separate them.
- **Why it still is not the lever**: a shim moved curl_cffi to 16384 / 4 and DDG's CAPTCHA verdict did not change, while Chrome with yet another window passed (§2.6). Nothing in this page measured TCP behaviour beyond the SYN (retransmission, ACK timing), and the 2026-09-29/30 loopback work of §2.9 does not see TCP at all.

### 3.6 curl_cffi Header Override Problem

From `curl-cffi` documentation (krowdev.com):

> "Do NOT override these headers — user-generated values frequently get ordering or values wrong"
> "curl_cffi does not set `Accept-Language` by default" — this is the only header that needs manual setting

**Critical finding**: When `impersonate=` is set, curl_cffi auto-generates all headers (User-Agent, Sec-CH-UA, Sec-Fetch-*, Accept, Accept-Encoding) with correct values AND correct ordering. Manually overriding these headers **breaks the fingerprint** by disrupting the header order. Our original script was doing exactly this — overriding all headers, sabotaging the impersonation.

**Fix applied**: Only set `Accept-Language` on the session, pass `Referer` per-request.

### 3.7 Remaining Unknown

Even with perfect TLS/HTTP2 fingerprinting and minimal header overrides, curl_cffi still gets CAPTCHA'd. The exact server-side detection mechanism beyond the above factors remains unknown. Hypotheses:
- DDG maintains a blocklist of known curl_cffi/libcurl behavioral signatures at a deeper protocol level
- HTTP/2 frame timing or connection reuse patterns differ from real browsers
- DDG uses IP reputation databases that flag datacenter/VPS IPs
- Some undocumented server-side heuristic we haven't identified

---

## 4. JavaScript Fingerprint on Main Site (`duckduckgo.com`)

### 4.1 The `window.__sc__` Object

The main DDG site (not lite/HTML) includes a JavaScript fingerprint mechanism:

```javascript
window.__sc__ = {
    h: "efe990d26717b7fac84c8c60a2e9ae44",  // hash (MD5)
    d: "mYAj1xYSJpqKcBTIiP_He2IOf...",       // encrypted data (dp parameter)
    s: function() { ... },                     // fingerprint generator function
    r: 86732                                   // computed fingerprint result (number)
}
```

Sent to DDG as: `{jsa: String(r), jsa_hash: h, dp: d}`

### 4.2 How the Fingerprint Works — DOM Parsing Fingerprint

The `s()` function is a **browser engine fingerprint** based on how the HTML parser handles malformed markup:

```javascript
function() {
    let jsa = 154;  // initial seed
    try {
        // Each function creates a div, sets malformed innerHTML,
        // reads back the browser-corrected HTML, uses its length

        // Test 1: unclosed nested divs
        el.innerHTML = '<div><div></div><div></div';
        jsa = jsa + el.innerHTML.length;  // Chrome: 33

        // Test 2: mismatched p/div nesting
        el.innerHTML = '<p><div></p><p></div';
        jsa = jsa + el.innerHTML.length;  // Chrome: 32

        // Test 3: br/div mismatch
        el.innerHTML = '<br><div></br><br></div';
        jsa = jsa + el.innerHTML.length;  // Chrome: 23

        // Interspersed with multiplications (*5, *3)
        // Random function names per page load (obfuscation)
    } catch(e) { jsa = -1; }
    return jsa;  // e.g. 86732
}
```

### 4.3 Why This Fingerprint is Clever

- Different browser engines (Blink, Gecko, WebKit) handle malformed HTML differently
- The corrected `innerHTML.length` varies per engine → unique numeric result
- No canvas, WebGL, or AudioContext needed — just the DOM parser
- Cannot be replicated by HTTP clients (no DOM parser)
- Function names are randomized per page load (anti-static-analysis)

### 4.4 Chrome (Blink) Reference Values

| Malformed HTML | Input Length | Chrome Output | Output Length |
|---------------|-------------|---------------|---------------|
| `<div><div></div><div></div` | 26 | `<div><div></div><div></div></div>` | 33 |
| `<p><div></p><p></div` | 20 | `<p></p><div><p></p><p></p></div>` | 32 |
| `<br><div></br><br></div` | 23 | `<br><div><br><br></div>` | 23 |

### 4.5 Relevance to Lite Endpoint

**None.** The lite endpoint has zero JavaScript. The `__sc__` fingerprint only runs on the main `duckduckgo.com` site. The lite endpoint's bot detection is entirely server-side (see Section 3).

---

## 5. DDG Telemetry & Tracking

### 5.1 ATB Token

- 120+ references in the main app JS
- DDG's internal tracking/attribution token
- Used across search sessions for analytics

### 5.2 Telemetry Pixels

Sent to `improving.duckduckgo.com/t/` via `navigator.sendBeacon()` or `Image.src`:

```
page_home_ssg_impression     — homepage load
page_home_ssg_search         — search submitted
page_home_ssg_scroll         — user scrolled
page_home_ssg_download       — download clicked
page_home_ssg_error          — SSG error
```

Parameters include: `hydrated`, `cached`, `experiment_turbo`

### 5.3 Cookies

From SearXNG documentation:

> "Except `Cookie: kl=..; df=..` DDG does not use cookies in any of its services"

- `kl` — Keyboard language/region (default: `wt-wt`)
- `df` — Time filter (`d`, `w`, `m`, `y`)
- DDG does NOT have client sessions

---

## 6. Community Status (as of May 2026)

### 6.1 deedy5/duckduckgo_search (→ ddgs)

- **v8.1.1**: `backends = ["bing"]  # temporaly disable html and lite backends`
- Library renamed to `ddgs`, evolved into multi-engine metasearch (10 backends: Bing, Brave, Google, Mojeek, StartPage, Yandex, Yahoo, Wikipedia, etc.)
- Uses `primp` with `impersonate="random"`, `impersonate_os="random"` — still gets rate-limited on DDG
- Multiple closed issues (#211, #271, #272, #290, #304) document persistent 202 Ratelimit errors

### 6.2 SearXNG

| Date | Event | Reference |
|------|-------|-----------|
| Oct 2024 | DDG CAPTCHA blocking reported | [#3927](https://github.com/searxng/searxng/issues/3927) |
| Oct 2024 | Switch to `html.duckduckgo.com/html/` endpoint | [PR #3955](https://github.com/searxng/searxng/pull/3955) |
| Oct 2024 | Add Sec-Fetch headers → "100% reliability" (temporary) | [PR #3965](https://github.com/searxng/searxng/pull/3965) |
| May 2025 | DDG CAPTCHA still occurring | [#4824](https://github.com/searxng/searxng/issues/4824) |
| Mar 2026 | Rate limiter proposal abandoned | [PR #5839](https://github.com/searxng/searxng/issues/5839) |

Current: DDG works intermittently. Engine raises `SearxEngineCaptchaException` with `suspended_time=0`.

### 6.3 Other Reports

- **Dr Frost AI** (Feb 2026): DDG worked "for exactly one day" then IP became "radioactive". Switched to self-hosted SearXNG.
- **OpenClaw**: "For production use, consider Brave Search (free tier available)"
- **All commercial scraping services** (ScrapFly, BrightData, ZenRows): Acknowledge DDG anti-bot, sell proxy/API solutions

---

## 7. Our Solution Architecture

### 7.1 Script: `Scripts/search_duckduckgo.py`

One run, no backend switch: every argument is one query, and the script reads no
environment variable `Scripts/search_duckduckgo.py:main`. The search itself is not
written in the script. It is generated from the web-search canonical source
`Scripts/_mcp_websearch.py`, the same blocks the MCP server `Scripts/mcp-search.py`
carries, and the HTTP client is generated from `Scripts/_mcp_chrome.py`
([[generated-regions]]). The script keeps by hand only its session factory, its
per-query session hook and its stderr notes.

DDG lite first, Bing for the rest of the run after a DDG block, every session on the
Chrome path `Scripts/_mcp_websearch.py:run_web`:

```
create_session()   Chrome path, public-only connect policy, 2 MiB body cap
   │
warmup GET lite.duckduckgo.com/lite/  (navigation profile)
   │
   ▼  per query (2.5-5 s pacing; new session + warmup every ROTATE_EVERY queries)
DDG Lite POST /lite/ (cors profile, §2.8)
   │
   ├─ results ──→ parse_lite_results → results
   │
   ├─ error / undecodable body ── NOT a block ──→ no results for this query (no Bing);
   │                                              a transport error drops the session
   │
   └─ block = host is lite.duckduckgo.com AND zero parsed results
              AND (status 202 OR a challenge marker the query does not contain)
                                          (_ddg_blocked; 202 since R-0052, marker = fallback predicate, §2.9)
                │
                └─→ Bing GET /search for this AND all remaining queries
                     (new session + Bing warmup, no extra pacing for the switched query)
                       │
                       └─ a 403 from www.bing.com = blocked: one stderr line, the query has
                          no results, the run goes on, and the script exits 1
```

There is no second attempt anywhere: no verified-first probe, no re-issue on another
transport and no sticky switch, so a DDG block costs exactly one Bing warm-up and the
Bing requests that replace the remaining DDG ones. A block, the switch and every other
event are reported by the generated search functions through a `note` callable that
the script renders as its historical stderr lines
`Scripts/search_duckduckgo.py:_cli_note`. A query Bing blocks prints
`[Blocked by Bing on: <query>]`, and `main` exits 1 after printing every other query's
results `Scripts/search_duckduckgo.py:_run_ddg_with_bing_fallback`. Bing's only block
signal is a 403 from `www.bing.com` itself; any other non-200 answer yields no results
`Scripts/_mcp_websearch.py:search_bing`, and a challenge served as a 200 is not
detected, so "always works" is an observation, not something the code guarantees.

The same loop answers the MCP server's `web` function, with the server's own session
hook around it: one session per endpoint under a lock, and a Bing block making the call
`isError` while the other queries' results stay in the text
`Scripts/mcp-search.py:with_session` `Scripts/mcp-search.py:handle_web`; see
[[scripts]].

The lite answer is parsed by one `html.parser` pass keyed on the `result-link`
anchor and the `result-snippet` cell, never on an implied end tag
`Scripts/_mcp_websearch.py:_LiteParser`. Until R-0057 it was a regex `findall`
over `<tr>` rows that went super-linear on a hostile body (F32); the new pass
returns the same fields and is gated in [[tests]] (`search_parsers`). Its result
count is one input of the DDG block predicate ("zero parsed results" above), so a
parser change is a block-rule change `Scripts/_mcp_websearch.py:parse_lite_results`.

The `cdp` backend that used to be a third run is gone (§7.3).

### 7.2 Impersonation Configuration (Minimal Headers)

Until R-0044 this section described two third-party backends picked by platform —
curl_cffi with a pinned profile list off Linux, primp with a bare alias on Linux — and
the asymmetric pin rule [[0004-never-pin-a-browser-impersonation-version]] froze for
them. Both packages are gone. From R-0044 until `8dde3a6` the factory chose between
two transports, a certificate-verified stdlib one by default and the Chrome path after
a block. That choice is gone too: every search session, in both CLIs and in the MCP
server, opens on the **Chrome path** — the Chrome 154 TLS 1.3 ClientHello and h2
preface measured in §2.9, with the certificate **not** verified
`Scripts/search_duckduckgo.py:create_session` `Scripts/search_github.py:create_session`
`Scripts/mcp-search.py:_create_session`. Each passes the classification-only connect
policy `Scripts/_mcp_chrome.py:_ch_public_only_policy`, so a redirect into private or
metadata space is still refused, and `SEARCH_MAX_BYTES` as the body cap. No result
carries a transport label any more, because there is only one transport; the gap is
stated in the session factories' docstrings and the server's tool description
`Scripts/mcp-search.py:SEARCH_CALL_TOOL`. Why D15's verified default was reversed for
search, and what that costs, is the `8dde3a6` addendum to
[[0026-speak-chrome-from-the-stdlib-verify-by-default]].

The one header profile is read from the committed captures rather than supplied by
the caller `Scripts/_mcp_chrome.py:_chrome_profile`: the warm-up `GET` goes out with
the navigation profile and the lite `POST` with the cors profile §2.8 found to be the
discriminator, the search adding only `Accept: */*` and the warm-up page as `Referer`
`Scripts/_mcp_websearch.py:search_ddg`. There is nothing left to pin, and a
fingerprint-bearing header passed by a caller is refused rather than merged
`Scripts/_mcp_chrome.py:_ch_check_caller_headers`. Session rotation every few queries
is unchanged `Scripts/search_duckduckgo.py:ROTATE_EVERY`.

The profile is macOS Chrome 154 on every platform, so on Linux the user agent no longer
matches the TCP stack — §2.7 Issue 3's tell, which primp's Linux mode used to avoid. It
is accepted as a declared cost of having one measured profile rather than two; the
decision and the follow-up are [[0026-speak-chrome-from-the-stdlib-verify-by-default]], and moving the profile to a newer Chrome is
[[chrome-profile-refresh]]. `Scripts/mcp-webfetch.py:_create_session` still has both
transports: there the verified one is the default and the Chrome path is the caller's
explicit `profile="chrome"` opt-in, never an automatic fallback.

### 7.3 CDP Backend (removed)

The opt-in `cdp` backend (`DDG_BACKEND=cdp`), which ran the lite `POST` as a `fetch()`
from a page of a real Chrome over the DevTools WebSocket, was removed in `8dde3a6`
together with `CDPSearcher`, `_run_cdp` and `_discover_chrome`. The script no longer
takes the WebSocket client generated from `Scripts/_mcp_websocket.py`; `Scripts/mcp-gdc.py`
is that source's only host now ([[0023-the-websocket-client-is-a-sixth-domain]] and its
addendum). The lessons it recorded — no `Origin` header on the DevTools socket, filter
out `devtools://` and `chrome://` targets, never `Page.navigate` (it resets session
state and triggers CAPTCHA), `Runtime.evaluate` with `fetch()` from an already open
page — still describe how to drive DDG through a real browser, which `mcp-gdc` can do
by hand.

### 7.4 Bing Result Parsing

Originally `lxml` XPath (same approach as deedy5/ddgs):
```python
tree = document_fromstring(html_text)
elements = tree.xpath("//li[contains(@class, 'b_algo')]")
# Decode Bing's base64 redirect URLs
href = base64.urlsafe_b64decode(u_param[2:] + padding).decode()
```

Since 2026-09-29 the same four XPath expressions are evaluated by a stdlib
`html.parser` tree builder (`Scripts/search_duckduckgo.py:parse_bing_results`),
measured equal to the lxml version and pinned to its recorded output — see
[[0024-pure-python-39-and-the-stdlib]]. The base64 URL decoding is unchanged.

### 7.5 Environment Variables

None. `DDG_BACKEND` (`ddg` / `bing` / `cdp`) and `CHROME_CDP_URL` were removed with
the `cdp` backend in `8dde3a6`. The script reads no environment variable, the HTTP
client reads none for networking either (no `*_PROXY`), and every command-line
argument is one query `Scripts/search_duckduckgo.py:main`. The MCP server takes its
queries as parameters instead `Scripts/mcp-search.py:handle_web`.

---

## 8. Open Questions & Next Steps

### 8.1 Unsolved

- What exactly does DDG's server-side detection check beyond TLS/HTTP2 that distinguishes curl_cffi from a real browser?
- Is there a way to make curl_cffi indistinguishable at the TCP/protocol level?
- Can the `html.duckduckgo.com/html/` endpoint with SearXNG-style headers work reliably on a fresh IP?

### 8.2 Planned Investigation

- **Packet capture via `mcp-tshark`**: the repo's capture server is `Scripts/mcp-tshark.py` (`tshark_call` — `start_capture` / `stop_capture` / `analyze` / `follow_stream`); there is no tcpdump MCP server. It can compare raw TCP/TLS packets between real Chrome and a Python client on the wire. For the ClientHello and h2 layers the loopback dump of §2.9 has since answered the question more directly than an off-host capture could (it sees the decrypted h2 frames); what a capture would still add is the TCP layer of §2.5/§3.5 against the real DDG endpoint.
- **Custom TLS library**: *done, see §2.9* — first as a throwaway PoC, then (R-0044) as `Scripts/_mcp_chrome.py`, generated into both search scripts, `Scripts/mcp-search.py` and webfetch. Since `8dde3a6` it is the only transport of the three search hosts and remains webfetch's explicit opt-in (§7.1, §7.2). Still open: certificate verification on that path, so that fingerprint and authenticity stop being a trade-off — and the search endpoints, which no longer have a verified path at all, need it most (roadmap R-0047).
- **Browser engine fingerprint replication**: Potentially use the `__sc__` DOM parsing fingerprint values for the main site endpoint

### 8.3 Assessed as Non-viable

- Waiting for curl_cffi/primp to improve — at the level tls.peet.ws hashes measure (§2.2) they were near-perfect for Chrome 146/148; against Chrome 153 on loopback (§2.9) curl_cffi lags by one extension and the ML-DSA sigalgs, and primp carries fixed tells no upgrade of the version string fixes
- JA3/Akamai string overrides — Akamai h2 already matches; JA3 is meaningless for Chrome (shuffled per connection, §2.9)
- Random UA/fingerprint rotation — DDG doesn't use JA3 hash matching (JA3 changes per connection due to GREASE)
- DDG Instant Answer API — doesn't provide full search results
- TCP/IP fingerprint spoofing — not because TCP is identical (the receive window is app-controlled and was measured different, §2.5), but because changing it did not change DDG's verdict (§2.6); see §3.5

---

## Appendix A: Key Source URLs

| Source | URL |
|--------|-----|
| SearXNG DDG engine docs | https://docs.searxng.org/dev/engines/online/duckduckgo.html |
| SearXNG PR #3955 (endpoint switch) | https://github.com/searxng/searxng/pull/3955 |
| SearXNG PR #3965 (Sec-Fetch headers) | https://github.com/searxng/searxng/pull/3965 |
| SearXNG Issue #4824 (ongoing CAPTCHA) | https://github.com/searxng/searxng/issues/4824 |
| curl_cffi impersonate guide | https://curl-cffi.readthedocs.io/en/v0.11.0/impersonate.html |
| curl_cffi fingerprinting article | https://krowdev.com/note/tls-fingerprinting-curl-cffi/ |
| HTTP/2 fingerprinting guide | https://dataresearchtools.com/http2-fingerprinting-scraping/ |
| Akamai fingerprinting in curl_cffi | https://deepwiki.com/lexiforest/curl_cffi/4.3-akamai-fingerprinting |
| Sec-Fetch bot detection analysis | https://blog.sicuranext.com/sec-fetch-and-client-hints-a-powerful-tool-against-automation/ |
| Cloudflare JA4 docs | https://developers.cloudflare.com/bots/additional-configurations/ja3-ja4-fingerprint/ |
| TLS fingerprint comparison tool | https://tls.peet.ws/api/all |
| DDG scraping methods guide | https://roundproxies.com/blog/scrape-duckduckgo/ |
