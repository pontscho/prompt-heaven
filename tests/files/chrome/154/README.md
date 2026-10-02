# tests/files/chrome/154 — Chrome 154 capture fixtures (R-0051)

These files are **recorded measurements**, not code. Each JSON file is one TLS
(or plaintext) connection that a real Chrome 154 opened against the loopback
capture server `Scripts/chrome_capture.py serve`, reduced by
`Scripts/chrome_capture.py export`. They are the oracle for the Chrome profile
in `Scripts/_mcp_chrome.py` (`_chrome_profile()`): tests read these files, never
the module under test, to decide what Chrome sends. Never regenerate a file
from the current client; refresh the whole set from a real browser (below).

## Environment

| field | value |
|---|---|
| captured | 2026-10-02, about 14:37-14:57 CEST (`meta.captured_on` is the UTC date) |
| browser | Google Chrome 154.0.8037.58 -- the running browser process (`Versions/154.0.8037.58` framework, `userAgentData` uaFullVersion). The app bundle on disk already reported `Google Chrome 154.0.8037.97` (`--version`), but the browser had not been relaunched since that update, so no request here was sent by .97 |
| `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36` |
| `userAgentData` fullVersionList | `Chromium` 154.0.8037.58, `Google Chrome` 154.0.8037.58, `Not A(Brand` 99.0.0.0 (`getHighEntropyValues`; platform `macOS`, platformVersion `14.2.1`, architecture `x86`, bitness `64`, mobile false) |
| OS | macOS 14.2.1 (23C71), Darwin 23.2.0, architecture x86 |
| origin | `https://capture.localhost:<port>/` (sets 1-5, 8, 9), `http://capture.localhost:<port>/` (set 6), `https://127.0.0.1:<port>/` (set 7); one port per set, 48533-48544 (48532 and 48536 were aborted runs whose records were discarded, see Layout) |
| certificate | untrusted self-signed loopback leaf. **No interstitial was shown** for `capture.localhost` or `127.0.0.1` in this browser session, so `#details-button` / `#proceed-link` were never clicked; Chrome still rejected the certificate at the TLS layer on the failed attempts (see the four-record phenomenon) |
| driver | the user's Chrome over CDP (gdc MCP, 127.0.0.1:9222, profile `--user-data-dir=/tmp/dev-session`, launched with `--auto-open-devtools-for-tabs --disable-web-security`), user-authorized, loopback only |

## JA4 values the tests read

| where | JA4 |
|---|---|
| every fresh, successful ClientHello with an SNI (sets 1-5, 8, 9 CH1; 140 of 140, plus 20 of 20 CH1 in set 9) | `t13d1517h2_8daaf6152771_cb7bf5808d99` |
| IP-literal origin, no SNI (set 7, 20 of 20) | `t13i1516h2_8daaf6152771_cb7bf5808d99` |
| second ClientHello after a HelloRetryRequest (set 9 CH2, 20 of 20) | `t13d1518h2_8daaf6152771_6ba8dc3d6269` |
| refused resumption attempts (NOT exported, see below) | `t13d1518h2_8daaf6152771_e2d80978ab2e` (SNI), `t13i1517h2_8daaf6152771_e2d80978ab2e` (IP literal) |

All four exported values are unchanged from Chrome 153. `t13d1517h2_8daaf6152771_cb7bf5808d99`
is the same JA4 R-0017 recorded (`docs/concepts/spec-ddg.md` section 2.9) and
the value the G6 loopback check compares the client against.

## The four-record phenomenon (why only every fourth record was exported)

Chrome 153 produced three records per connection against this certificate;
Chrome 154 produces **four**. In every TLS set except the first connection to
an origin, each fresh connection in the raw capture is:

1. and 2. two handshakes the server logged as failed,
   `SSLError ... SSLV3_ALERT_CERTIFICATE_UNKNOWN`, no ALPN negotiated, each
   carrying a `pre_shared_key` extension (41): 18 extensions, JA4
   `t13d1518h2_8daaf6152771_e2d80978ab2e` (`t13i1517h2_..._e2d80978ab2e` for
   the IP literal);
3. one more failed handshake (same alert) **without** a PSK -- the full-handshake
   JA4 `..._cb7bf5808d99` (`t13i1516h2_...` for the IP literal). This record is
   new in 154;
4. one successful **full** handshake (no PSK, JA4 `..._cb7bf5808d99`) that
   carries the HTTP traffic.

The first connection to each origin is three failed full handshakes then the
successful one (#001-#003 failed, #004 kept). So the kept records are #004,
#008 ... #080 of each set. Only the successful fourth record was exported; the
failed attempts carry no HTTP layer and would mix a resumption-shaped
ClientHello into a set of fresh ones. What this directory therefore does NOT
contain is a successful resumed (PSK) handshake: against this certificate
Chrome never completed one. The records show what Chrome sent; they do not
show why it retried.

The raw captures (not committed) were `.claude/tmp/r0051/raw/<n>-<set>/`;
the fixtures were exported one record at a time (`export --from <record> --name <label>-NNN`),
so `meta.source` / `meta.index` name the raw record.

## Layout

One subdirectory per set, 20 connections per set. Names are `<label>-NNN.json`
in capture order; `meta.source` names the raw record and `meta.index` its
position in the raw set.

| dir | set | label | files | raw records exported | server flags |
|---|---|---|---|---|---|
| `navigate/` | 1a, typed navigation (CDP `Page.navigate`) | navigate | 20 | #004, #008 ... #080 of 80 | default |
| `navigate-reload/` | 1b, reload of an open page | navigate | 20 | #008, #012 ... #084 of 84 (#004 was the one typed load) | default |
| `cors-post/` | 2, navigate `/lite/`, then a same-origin form POST | cors-post | 20 | #004 ... #080 of 80 | default |
| `cors-get/` | 3, navigate `/`, then a cors GET | cors-get | 20 | #004 ... #080 of 80 | default |
| `cors-head/` | 4, navigate `/`, then a cors HEAD | cors-head | 20 | #004 ... #080 of 80 | default |
| `h1-tls/` | 5, navigation over TLS with ALPN http/1.1 | h1-tls | 20 | #004 ... #080 of 80 | `--alpn http/1.1` |
| `h1-plain/` | 6, `http://` navigation | h1-plain | 20 | the 20 non-empty of 23 (#003, #013, #023 were preconnect sockets that sent nothing) | `--plain` |
| `ip-literal/` | 7, navigation to `https://127.0.0.1:<port>/` | ip-literal | 20 | #004 ... #080 of 80 | default |
| `cookie/` | 8, navigations after the server set a cookie | cookie | 20 | #004 ... #080 of 80 | `--set-cookie tc=1` |
| `hrr/` | 9, CH1 answered with a HelloRetryRequest | hrr | 20 | #001 ... #020 of 30 | `--hrr-group 0x0017` |

Two runs were discarded whole and are not in any count above: set 1a's first
server (port 48532), whose first successful connection went to a different CDP
target and carried only the navigation stream, and set 2's first server (port
48536), which Chrome hit before it listened -- the net-error page's automatic
reload then put two navigations on its first successful connection.

Every connection of sets 2-4 is a navigate-then-fetch pair on ONE connection.
The streams are 1 = navigation, 3 = `GET /favicon.ico` (`sec-fetch-mode: no-cors`,
`sec-fetch-dest: image`, `priority: u=1, i`, weight 220), 5 = the page's
`fetch`. Sets 1, 7 and 8 carry streams 1 and 3; the h1 sets carry the same two
requests on one keep-alive connection.

**Changed from 153:** the 153 captures also carried
`GET /.well-known/appspecific/com.chrome.devtools.json` on stream 3 (and the
favicon on 5, the fetch on 7). No 154 connection requested it, so the fetch is
on stream 5 and every connection carries one request fewer. Whether that is a
Chrome 154 change or a difference in the DevTools state of the driven tab was
not determined.

## Exact JavaScript evaluated

Each navigation was followed by a 3 s wait (`new Promise(r=>setTimeout(r,3000))`
evaluated over CDP, or the 3 s inside the fetch below) so the server
(`--idle 2.0`) closed the connection and the next navigation opened a new one.
Set 1b reloads with CDP `Page.reload` (gdc `navigate {action: reload}`).

- set 2 (page at `/lite/`):
  `fetch("/lite/", {method:"POST", body:new URLSearchParams({q:"test", kl:""}), headers:{"Accept":"*/*"}}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`
- set 3 (page at `/`):
  `fetch("/api/search?q=x", {headers:{"Accept":"application/json, text/plain, */*"}}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`
- set 4 (page at `/`):
  `fetch("/", {method:"HEAD"}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`

The server answered every request with its default `200 text/plain` "ok" body
(no `--page`); every fetch resolved with status 200.

## Profile table

HPACK notation: `idx N` = indexed field; `inc` = literal with incremental
indexing; `noidx` = literal without indexing; `name N` = indexed name, `new` =
literal name (always Huffman-coded); `H` = Huffman value, `raw` = raw value. The
representations are those of the FIRST connection's HPACK state; within one set
every connection's HEADERS block is byte-identical (checked by searching each
set for one block's hex: 20/20 for stream 1 of `navigate/` and for stream 5 of
`cors-post/`, `cors-get/`, `cors-head/`). `weight` is the parser's value, wire byte + 1.

### h2 navigation (stream 1; sets 1a, 1b, 2, 3, 4, 7, 8)

HEADERS flags `0x25` (END_STREAM | END_HEADERS | PRIORITY); priority
exclusive, dep 0, weight 256 (wire 255). Preface: SETTINGS
`1:65536;2:0;4:6291456;6:262144`, WINDOW_UPDATE 15663105; Akamai
`1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p` on every connection.

| # | name | representation | note |
|---|---|---|---|
| 1 | `:method` | idx 2 | GET |
| 2 | `:authority` | inc, name 1, H | |
| 3 | `:scheme` | idx 7 | https |
| 4 | `:path` | idx 4 for `/`; noidx, name 4, H for any other path | |
| - | `cache-control` | inc, name 24, H | `max-age=0`, **reload only** (1b); a typed navigation (1a) omits it. No set-7 connection was a reload in 154 |
| 5 | `sec-ch-ua` | inc, new, H | `"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"` -- brand order and GREASE brand changed from 153 |
| 6 | `sec-ch-ua-mobile` | inc, new, raw | `?0` |
| 7 | `sec-ch-ua-platform` | inc, new, raw | `"macOS"` |
| 8 | `upgrade-insecure-requests` | inc, new, raw | `1` |
| 9 | `user-agent` | inc, name 58, H | `... Chrome/154.0.0.0 Safari/537.36` |
| 10 | `accept` | inc, name 19, H | navigation accept |
| 11 | `sec-fetch-site` | inc, new, H | `none` |
| 12 | `sec-fetch-mode` | inc, new, H | `navigate` |
| 13 | `sec-fetch-user` | inc, new, raw | `?1` |
| 14 | `sec-fetch-dest` | inc, new, H | `document` |
| 15 | `accept-encoding` | inc, name 16, H | `gzip, deflate, br, zstd` |
| 16 | `accept-language` | inc, name 17, H | `en-GB,en-US;q=0.9,en;q=0.8` |
| - | `cookie` | inc, name 32, H | set 8 only, once the server has set it: **after accept-language, before priority** |
| 17 | `priority` | inc, new, H | `u=0, i` |

1a and 1b differ ONLY in `cache-control: max-age=0` (diff: `h2.header_order_raw`
and `hdr.cache-control`); `sec-fetch-site`, `sec-fetch-user` and every other
value are identical.

### h2 cors POST (set 2, stream 5)

HEADERS flags `0x24` (END_HEADERS | PRIORITY, **no END_STREAM**); priority
exclusive, dep 0, weight 220 (wire 219). Body: exactly ONE DATA frame, length
10, flags `0x01` (END_STREAM), payload `q=test&kl=`. No `origin` header was sent.

The representations below differ from 153's because the HPACK state differs:
the stream before the fetch is now the favicon request (which already carried
`sec-fetch-site: same-origin` and `referer`), not the devtools.json request.

| # | name | representation (conn state after streams 1, 3) | value |
|---|---|---|---|
| 1 | `:method` | idx 3 | POST |
| 2 | `:authority` | idx | |
| 3 | `:scheme` | idx 7 | |
| 4 | `:path` | noidx, name 4, H | `/lite/` |
| 5 | `content-length` | inc, name 28, raw | `10` |
| 6 | `sec-ch-ua-platform` | idx | |
| 7 | `user-agent` | idx | |
| 8 | `accept` | inc, name 19, raw | `*/*` (set by the page) |
| 9 | `sec-ch-ua` | idx | |
| 10 | `content-type` | inc, name 31, H | `application/x-www-form-urlencoded;charset=UTF-8` |
| 11 | `sec-ch-ua-mobile` | idx | |
| 12 | `sec-fetch-site` | idx | `same-origin` |
| 13 | `sec-fetch-mode` | inc, name (dynamic), H | `cors` |
| 14 | `sec-fetch-dest` | inc, name (dynamic), H | `empty` (was idx in 153) |
| 15 | `referer` | idx | `https://capture.localhost:<port>/lite/` -- the full page URL |
| 16 | `accept-encoding` | idx | |
| 17 | `accept-language` | idx | |
| 18 | `priority` | idx | `u=1, i` |

### h2 cors GET (set 3, stream 5)

Flags `0x25`, weight 220. Order: `:method` (idx 2), `:authority`, `:scheme`,
`:path` (noidx, name 4, H, `/api/search?q=x`), `sec-ch-ua-platform`,
`user-agent`, `accept` (inc, name 19, H, `application/json, text/plain, */*`,
set by the page), `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-fetch-site`
(`same-origin`), `sec-fetch-mode` (`cors`), `sec-fetch-dest` (`empty`),
`referer` (`https://capture.localhost:<port>/`), `accept-encoding`,
`accept-language`, `priority` (`u=1, i`). No `origin`, no `content-type`.
Order and values are identical to 153.

### h2 cors HEAD (set 4, stream 5)

Flags `0x25`, weight 220. `:method` is noidx, name 2, **raw** `HEAD`; `:path`
idx 4 (`/`). Order: `:method`, `:authority`, `:scheme`, `:path`,
`sec-ch-ua-platform`, `user-agent`, `sec-ch-ua`, `sec-ch-ua-mobile`, `accept`
(inc, name 19, raw, `*/*` -- Chrome's default, not set by the page),
`sec-fetch-site`, `sec-fetch-mode` (`cors`), `sec-fetch-dest` (`empty`),
`referer`, `accept-encoding`, `accept-language`, `priority` (`u=1, i`).

The `accept` slot is the one measured difference between sets 3/2 and set 4: a
page-set Accept sits between `user-agent` and `sec-ch-ua`; Chrome's default
`*/*` sits between `sec-ch-ua-mobile` and `sec-fetch-site`. Unchanged from 153.

### HTTP/1.1 navigation (sets 5 and 6, identical)

Request line `GET / HTTP/1.1`, then in this order and casing:
`Host`, `Connection: keep-alive`, `sec-ch-ua`, `sec-ch-ua-mobile`,
`sec-ch-ua-platform`, `Upgrade-Insecure-Requests`, `User-Agent`, `Accept`,
`Sec-Fetch-Site`, `Sec-Fetch-Mode`, `Sec-Fetch-User`, `Sec-Fetch-Dest`,
`Accept-Encoding`, `Accept-Language`. No `Priority` header over HTTP/1.1. The
`sec-ch-*` names stay lowercase; every other name is canonical case. The
plaintext `http://` navigation (set 6) sends the same list, including
`Accept-Encoding: gzip, deflate, br, zstd`. Neither set carries
`Cache-Control` or `Cookie`, so their h1 slots are not measured here. (`diff`
against the 153 sets compares `h1.header_order` with its casing and reports
only `sec-ch-ua` and `user-agent` as changed.) Each connection carries two
requests, `/` and `/favicon.ico`.

### ClientHello

17 non-GREASE extensions (0, 5, 10, 11, 13, 16, 18, 23, 27, 35, 43, 45, 51,
17613, 51764, 65037, 65281) plus a leading and a trailing GREASE extension, in a
per-connection permuted order (20 distinct orders out of 20 in every set, CH2
included). `application_settings` (17613) payload `0003026832` = `["h2"]`.

**Changed from 153:** extension 51764 (`trust_anchors`, draft codepoint
0xca34) has the same length (186) but a different payload; the 154 payload
(sha256 prefix `333d01647d772aa6`, starts `00b80582df130201...`) is
byte-identical on all 180 TLS connections and all 20 CH2s (no fixed
difference inside any set; the same digest in every set's diff against 153).
This is the one ClientHello difference `diff` reports against 153.

ECH GREASE payload length drawn from {144, 176, 208, 240}. ClientHello
handshake length = 1759 + len(SNI) + ECH payload length
(`tls.clienthello_len_minus_sni` 1903/1935/1967/1999, so `capture.localhost`:
1920/1952/1984/2016). Set 7 (IP literal): no `server_name` extension, 16
non-GREASE extensions, length = 1750 + ECH payload length (1894/1926/1958/1990). Sets 5/6: h1-tls still
offers ALPN `h2, http/1.1`; the server chose http/1.1.

### HelloRetryRequest (set 9, 20 of 20 connections)

Server: HRR selecting `0x0017` (secp256r1, offered in supported_groups without a
key share) with a 28-byte cookie. Chrome then sends:

- a ChangeCipherSpec record (type 20, 1 byte) BEFORE CH2, every time
  (`ccs_before_client_hello_2` true on 20/20);
- CH2 in one record, handshake length = CH1 length - 1192 + 6 + cookie length
  (`hrr-001.json`: CH1 1948 with ECH payload 176, CH2 790;
  `ch2.tls.clienthello_len_minus_sni` 745/777/809/841 over the set, so 858
  for ECH payload 240);
- the same random and legacy session id as CH1 (bytes compared on
  `hrr-001.json`; cipher list identical; GREASE values not re-compared);
- the same extension order as CH1, with `cookie` (44) inserted at a position
  that varies per connection (20 distinct CH2 orders of 20);
- `key_share` with ONE entry, `0x0017` with a 65-byte key (`key_share_groups_2`
  `["0017"]`); `supported_groups` unchanged;
- the cookie echoed byte for byte (`cookie_echoed` true on 20/20);
- the ECH GREASE extension byte-identical to CH1's on `hrr-001.json`; the
  same-ECH-length-as-CH1 property over all 20 was not re-checked.

## Consistency checks run for R-0051

- `diff` of every set against itself: no fixed difference; the only varying
  HTTP feature is set 8 (connection 1 has no cookie yet on its navigation;
  its favicon request already carries `tc=1`).
- set 2 vs set 1a (stream 1): one fixed difference, `h2.hpack_reps`, because
  `:path` `/lite/` is a literal where `/` is indexed.
- 1a vs 1b: exactly `h2.header_order_raw` and `hdr.cache-control`.
- every 154 set vs the same 153 set: `ext.51764`, `hdr.sec-ch-ua` and
  `hdr.user-agent` (h1 sets: `h1.hdr.sec-ch-ua`, `h1.hdr.user-agent`, plus
  `ext.51764` for h1-tls; hrr: `ext.51764` and `ch2.ext.51764` only). Header
  order, HPACK representation of stream 1, h2 SETTINGS / WINDOW_UPDATE /
  priority, cipher list, extension set, ECH lengths and ClientHello lengths are
  unchanged. `diff` judges stream 1 only; the cors-stream representation
  changes above were read from the fixtures.
- 1b (reload) as reference vs R-0017 (`.claude/tmp/chdump/chrome/`, 12 fresh +
  18 PSK-resumed connections) as candidate: exit 1, the same three
  version-bearing differences (`ext.51764`, `hdr.sec-ch-ua`,
  `hdr.user-agent`); the 18 resumed connections are reported
  `UNMATCHED resumed: present only in candidate (18 connections); not compared`.
- 1a (typed) as reference vs R-0017: exit 1, those three plus
  `h2.header_order_raw` (a typed navigation omits `cache-control: max-age=0`).

## Refresh

1. `python3 -B Scripts/chrome_capture.py serve --port <p> --out .claude/tmp/<dir> --label <label> --count 80 [--alpn http/1.1 | --plain | --set-cookie tc=1 | --hrr-group 0x0017]`
   (84 for 1b: one typed load plus 20 reloads; 30 for hrr; a few more than 20 for `--plain`).
   Wait for the `listening on` line before the first navigation.
2. In Chrome, navigate to the origin, pass the interstitial once if one is
   shown (`#details-button`, then `#proceed-link`), then navigate (or reload,
   or run the set's JavaScript) once per connection with a 3 s pause between them.
3. Keep only the records without `handshake_error` (the fourth of each group
   in 154) and without an empty request list.
4. `python3 -B Scripts/chrome_capture.py export --from <record or scratch dir> --to tests/files/chrome/<major>/<set> --label <label> --chrome-version <full version> [--name <label>-NNN]`
   (`export` refuses any non-loopback identifier or a cookie the server did not set).
5. Re-run the checks above and update this README, including the JA4 table.
