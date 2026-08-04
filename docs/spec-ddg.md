---
name: spec-ddg
type: concept
status: active
title: DuckDuckGo Bot Detection Research
description: Why DDG blocks Python HTTP clients, and the DDG-first/Bing-fallback strategy used by the search script.
sources:
  - Scripts/search_duckduckgo.py
verified:
  commit: 1bade65
  date: 2026-08-04
links:
  - scripts
---

# DuckDuckGo Bot Detection — Technical Analysis & Bypass Research

## Executive Summary

DuckDuckGo employs multi-layered bot detection that effectively blocks all known Python HTTP clients (curl_cffi, primp, requests, httpx) from scraping search results, regardless of TLS impersonation quality. Even with virtually identical TLS/HTTP2 fingerprints to a real Chrome browser, DDG's server-side detection catches non-browser clients on the lite endpoint. The most popular DDG search library (deedy5/duckduckgo_search v8.1.1) has abandoned DDG entirely, switching to Bing as default backend. SearXNG (the leading open-source metasearch engine) reports intermittent DDG CAPTCHA failures that remain unresolved as of May 2025.

Our script uses a **DDG-first with Bing auto-fallback** strategy, plus an optional CDP backend that routes searches through a real Chrome browser via DevTools Protocol.

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

The mitigation is why the Linux branch exists at all: curl_cffi has no OS knob, so
Linux switches to primp with `impersonate_os="linux"` `Scripts/search_duckduckgo.py:create_session`.
That reasoning is unchanged — only the mechanism moved. As of 2026-08-04 that one
argument is the *whole* mechanism: primp derives the `X11; Linux x86_64` UA and the
matching `sec-ch-ua-platform` from it, and the script supplies no UA and no headers
by hand. Previously the coherence was hand-built — a pinned Chrome major plus a
matching Linux Chrome header dict — and both are gone (see §2.8).

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
| 3  | UA claims macOS, OS is Linux | 🟢 SOLVED (primp Linux mode) | Done in `create_session()` |
| 4  | Stale GREASE brand strings | 🟢 LOW | Maintained by curl_cffi upstream |

**The user's question answered**: curl_cffi DOES use HTTP/2 (`* using HTTP/2`). After the byte-equivalent TLS handshake, the divergence is in the HTTP/2 HEADERS frame — specifically the Sec-Fetch header coherence and OS/platform consistency with the underlying network stack.

#### Actionable fixes for the script

1. **PRIMARY (done)**: Switch DDG POST headers to the Chrome XHR pattern — `cors` / `empty` / `*/*` / `u=1`. See §2.8.
2. **Secondary (done)**: Override `Sec-Fetch-Site: same-origin` and Referer to a same-origin URL on the same POST (subsumed by #1).
3. **Done**: On Linux hosts the Linux Chrome UA comes from primp's `impersonate_os="linux"`
   — nothing is supplied by hand `Scripts/search_duckduckgo.py:create_session`.
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

### 3.5 TCP/IP Fingerprinting (NOT a Factor)

TCP fingerprinting (JA4T) operates at the OS level, not the application level. A Python script and Chrome running on the same Linux machine produce **identical** TCP SYN packets (same TTL=64, window size, TCP options). This cannot distinguish curl_cffi from a real browser on the same host.

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

Three backends with auto-fallback:

```
┌─────────────────┐
│  DDG Lite (POST) │ ──CAPTCHA──→ ┌──────────────┐
│  curl_cffi       │              │  Bing (GET)   │
│  chrome146       │              │  curl_cffi    │
└────────┬────────┘              │  always works │
         │OK                      └──────────────┘
         ▼
    Return results
```

**Optional CDP backend** (`DDG_BACKEND=cdp`):
```
┌──────────────────┐
│  Chrome (real)    │
│  CDP WebSocket    │
│  fetch() from JS  │ ── always works, requires running Chrome
└──────────────────┘
```

### 7.2 Impersonation Configuration (Minimal Headers)

One factory picks the backend by platform `Scripts/search_duckduckgo.py:create_session`:

- **non-Linux** — `curl_cffi.requests.Session(impersonate=...)` with a name drawn at
  random from a four-entry pinned list (`chrome146`, `chrome145`, `chrome136`,
  `safari260`) `Scripts/search_duckduckgo.py:CURL_CFFI_PROFILES`. That list is the
  exact configuration the ~80% pass-through of §2.8 was measured with, which is why
  it stays pinned. Only `Accept-Language` is set by hand — overriding anything else
  breaks the fingerprint (§3.6) — and `Referer` goes per-request, never on the session.
- **Linux** — `primp.Client(impersonate="chrome", impersonate_os="linux")` with a
  *bare alias*, never a pinned major, validated against a whitelist
  `Scripts/search_duckduckgo.py:PRIMP_ALIASES`. No headers are supplied at all (§2.8).

A pin is tolerable on curl_cffi and forbidden on primp, and the asymmetry is
deliberate: curl_cffi raises `ImpersonateError` on an unknown name, whereas primp
prints one line to stderr and silently substitutes a **random** browser — so a rotted
pin there produces an arbitrary fingerprint rather than an error. Session rotation
every few queries is unchanged `Scripts/search_duckduckgo.py:ROTATE_EVERY`.

The same two-branch shape, for the same reason, is used by
`Scripts/mcp-webfetch.py:_create_session`.

### 7.3 CDP Backend Implementation

Key details:
- Connects via `websocket-client` with `suppress_origin=True` (required for Chrome's CORS)
- Must filter out `devtools://` and `chrome://` page targets
- Must NOT use `Page.navigate` — it resets session state and triggers CAPTCHA
- Uses `Runtime.evaluate` with `fetch()` from existing page context
- Chrome must have an existing page open (any URL works as fetch origin)

### 7.4 Bing Result Parsing

Uses `lxml` XPath (same approach as deedy5/ddgs):
```python
tree = document_fromstring(html_text)
elements = tree.xpath("//li[contains(@class, 'b_algo')]")
# Decode Bing's base64 redirect URLs
href = base64.urlsafe_b64decode(u_param[2:] + padding).decode()
```

### 7.5 Environment Variables

| Variable | Values | Default |
|----------|--------|---------|
| `DDG_BACKEND` | `cdp`, `bing`, `ddg` | `ddg` (with bing fallback) |
| `CHROME_CDP_URL` | e.g. `http://192.168.2.2:9222` | Auto-discover |

---

## 8. Open Questions & Next Steps

### 8.1 Unsolved

- What exactly does DDG's server-side detection check beyond TLS/HTTP2 that distinguishes curl_cffi from a real browser?
- Is there a way to make curl_cffi indistinguishable at the TCP/protocol level?
- Can the `html.duckduckgo.com/html/` endpoint with SearXNG-style headers work reliably on a fresh IP?

### 8.2 Planned Investigation

- **tcpdump MCP server**: Capture and compare raw TCP/TLS packets between real Chrome and curl_cffi to find the exact divergence point
- **Custom TLS library**: Build a solution with an external library that produces byte-identical TLS handshakes to a real browser
- **Browser engine fingerprint replication**: Potentially use the `__sc__` DOM parsing fingerprint values for the main site endpoint

### 8.3 Assessed as Non-viable

- Waiting for curl_cffi/primp to improve — both are already near-perfect at TLS/HTTP2 level
- JA3/Akamai string overrides — the fingerprints already match
- Random UA/fingerprint rotation — DDG doesn't use JA3 hash matching (JA3 changes per connection due to GREASE)
- DDG Instant Answer API — doesn't provide full search results
- TCP/IP fingerprint spoofing — TCP is OS-level, already matches between curl_cffi and Chrome on same host

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
