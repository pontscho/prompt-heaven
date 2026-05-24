# DuckDuckGo Bot Detection — Technical Analysis & Bypass Research

**Date**: 2026-05-23
**Status**: Active investigation
**Script**: `Scripts/search_duckduckgo.py`

---

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

### 7.2 curl_cffi Configuration (Minimal Headers)

```python
IMPERSONATIONS = ["chrome146", "chrome145", "chrome136", "safari260"]

session = requests.Session(impersonate=random.choice(IMPERSONATIONS))
# ONLY set Accept-Language — curl_cffi handles everything else
session.headers["Accept-Language"] = "en-US,en;q=0.9"

# Per-request Referer (not session-level)
resp = session.post(url, data=payload,
    headers={"Referer": "https://lite.duckduckgo.com/lite/"})
```

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

## Appendix B: Commit History

| Hash | Description |
|------|-------------|
| `383f298` | Add Bing fallback + CDP backend, fix header override issue |
| `a77aff7` | Fix CDP backend — skip devtools pages, remove Page.navigate |
