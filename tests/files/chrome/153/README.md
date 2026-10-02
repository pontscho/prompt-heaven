# tests/files/chrome/153 — Chrome 153 capture fixtures (Gate G1, R-0044)

These files are **recorded measurements**, not code. Each JSON file is one TLS
(or plaintext) connection that a real Chrome 153 opened against the loopback
capture server `Scripts/chrome_capture.py serve`, reduced by
`Scripts/chrome_capture.py export`. They are the oracle for the Chrome profile
in `Scripts/_mcp_chrome.py` (`_chrome_profile()`): tests read these files, never
the module under test, to decide what Chrome sends. Never regenerate a file
from the current client; refresh the whole set from a real browser (below).

## Environment

| field | value |
|---|---|
| captured | 2026-09-30, about 12:01-12:16 CEST (`meta.captured_on` is the UTC date) |
| browser | Google Chrome 153.0.8010.37 |
| `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36` |
| `userAgentData` fullVersionList | `Google Chrome` 153.0.8010.37, `Not_A Brand` 8.0.0.0, `Chromium` 153.0.8010.37 |
| OS | macOS 14.2.1 (23C71), Darwin 23.2.0, architecture x86 |
| origin | `https://capture.localhost:<port>/` (sets 1-5, 8, 9), `http://capture.localhost:<port>/` (set 6), `https://127.0.0.1:<port>/` (set 7); one port per set, 48432-48441 |
| certificate | untrusted self-signed loopback leaf; the interstitial was passed once per host with `#details-button` then `#proceed-link` |
| driver | the user's Chrome over CDP (gdc MCP, 127.0.0.1:9222), user-authorized, loopback only |

## JA4 values the tests read

| where | JA4 |
|---|---|
| every fresh, successful ClientHello with an SNI (sets 1-5, 8, 9 CH1) | `t13d1517h2_8daaf6152771_cb7bf5808d99` |
| IP-literal origin, no SNI (set 7) | `t13i1516h2_8daaf6152771_cb7bf5808d99` |
| second ClientHello after a HelloRetryRequest (set 9 CH2) | `t13d1518h2_8daaf6152771_6ba8dc3d6269` |
| refused resumption attempts (NOT exported, see below) | `t13d1518h2_8daaf6152771_e2d80978ab2e` (SNI), `t13i1517h2_8daaf6152771_e2d80978ab2e` (IP literal) |

`t13d1517h2_8daaf6152771_cb7bf5808d99` is the same JA4 R-0017 recorded
(`docs/concepts/spec-ddg.md` section 2.9) and the value the G6 loopback check
compares the client against.

## The three-record phenomenon (why only every third record was exported)

Against this untrusted certificate every fresh TLS connection in the raw
capture shows up as **three** records:

1. and 2. two handshakes the server logged as failed,
   `SSLError ... SSLV3_ALERT_CERTIFICATE_UNKNOWN`, with no ALPN negotiated. On
   the first connection to an origin they carry the full-handshake JA4
   (`..._cb7bf5808d99`, no `pre_shared_key`); on every later connection they
   carry a `pre_shared_key` extension (41), which makes 18 extensions and the
   JA4 `t13d1518h2_8daaf6152771_e2d80978ab2e`
   (`t13i1517h2_..._e2d80978ab2e` for the IP literal);
3. one successful **full** handshake (no PSK, JA4 `..._cb7bf5808d99`) that
   carries the HTTP traffic.

Only the successful third record of each triple was exported; the failed
attempts are excluded because they carry no HTTP layer and would mix a
resumption-shaped ClientHello into a set of fresh ones. What this directory
therefore does NOT contain is a successful resumed (PSK) handshake: against this
certificate Chrome never completed one. The records show what Chrome sent; they
do not show why it retried.

The raw captures (not committed) were `.claude/tmp/chrome153_capture/<set>/`;
the exported records were copied first into `.claude/tmp/chrome153_filtered/`.

## Layout

One subdirectory per set, 20 connections per set where Chrome produced them.
Names are `<label>-NNN.json` in capture order; `meta.source` names the raw
record and `meta.index` its position in the raw set.

| dir | set | label | files | raw records exported | server flags |
|---|---|---|---|---|---|
| `navigate/` | 1a, typed navigation (CDP `Page.navigate`) | navigate | 20 | #003, #006 ... #060 of 66 | default |
| `navigate-reload/` | 1b, reload of an open page | navigate | 20 | #006, #009 ... #063 of 66 (#003 was the one typed load) | default |
| `cors-post/` | 2, navigate `/lite/`, then a same-origin form POST | cors-post | 20 | #003 ... #060 of 66 | default |
| `cors-get/` | 3, navigate `/`, then a cors GET | cors-get | 20 | #003 ... #060 of 66 | default |
| `cors-head/` | 4, navigate `/`, then a cors HEAD | cors-head | 20 | #003 ... #060 of 66 | default |
| `h1-tls/` | 5, navigation over TLS with ALPN http/1.1 | h1-tls | 20 | #003 ... #060 of 66 | `--alpn http/1.1` |
| `h1-plain/` | 6, `http://` navigation | h1-plain | 16 | the 16 non-empty of 20 (4 were preconnect sockets that sent nothing) | `--plain` |
| `ip-literal/` | 7, navigation to `https://127.0.0.1:<port>/` | ip-literal | 20 | #005, #008 ... #062 of 65 | default |
| `cookie/` | 8, navigations after the server set a cookie | cookie | 20 | #003 ... #060 of 66 | `--set-cookie tc=1` |
| `hrr/` | 9, CH1 answered with a HelloRetryRequest | hrr | 20 | #001 ... #020 of 30 | `--hrr-group 0x0017` |

Every connection of sets 2-4 is a navigate-then-fetch pair on ONE connection.
The streams are 1 = navigation, 3 = `GET /.well-known/appspecific/com.chrome.devtools.json`
(`sec-fetch-mode: no-cors`, `priority: u=4, i`), 5 = `GET /favicon.ico`
(`priority: u=1, i`), 7 = the page's `fetch`. Sets 1, 7 and 8 carry streams 1,
3 and 5; the h1 sets carry the same three requests on one keep-alive connection.
The plan expected the fetch on stream 3; it is on stream 7 because of the two
requests Chrome issued first.

## Exact JavaScript evaluated

Each navigation was followed by a 3 s wait so the server (`--idle 2.0`) closed
the connection and the next navigation opened a new one.

- set 2 (page at `/lite/`):
  `fetch("/lite/", {method:"POST", body:new URLSearchParams({q:"test", kl:""}), headers:{"Accept":"*/*"}}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`
- set 3 (page at `/`):
  `fetch("/api/search?q=x", {headers:{"Accept":"application/json, text/plain, */*"}}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`
- set 4 (page at `/`):
  `fetch("/", {method:"HEAD"}).then(r=>new Promise(res=>setTimeout(()=>res(r.status),3000)))`

The server answered every request with its default `200 text/plain` "ok" body
(no `--page`).

## Profile table

HPACK notation: `idx N` = indexed field; `inc` = literal with incremental
indexing; `noidx` = literal without indexing; `name N` = indexed name, `new` =
literal name (always Huffman-coded); `H` = Huffman value, `raw` = raw value. The
representations are those of the FIRST connection's HPACK state; within one set
every connection's HEADERS block is byte-identical (checked by searching each
set for one block's hex: 20/20). `weight` is the parser's value, wire byte + 1.

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
| - | `cache-control` | inc, name 24, H | `max-age=0`, **reload only** (1b; 1 of 20 in set 7); a typed navigation (1a) omits it |
| 5 | `sec-ch-ua` | inc, new, H | |
| 6 | `sec-ch-ua-mobile` | inc, new, raw | `?0` |
| 7 | `sec-ch-ua-platform` | inc, new, raw | `"macOS"` |
| 8 | `upgrade-insecure-requests` | inc, new, raw | `1` |
| 9 | `user-agent` | inc, name 58, H | |
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

### h2 cors POST (set 2, stream 7)

HEADERS flags `0x24` (END_HEADERS | PRIORITY, **no END_STREAM**); priority
exclusive, dep 0, weight 220 (wire 219). Body: exactly ONE DATA frame, length
10, flags `0x01` (END_STREAM), payload `q=test&kl=`. No `origin` header was sent.

| # | name | representation (conn state after streams 1, 3, 5) | value |
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
| 14 | `sec-fetch-dest` | idx | `empty` |
| 15 | `referer` | idx | `https://capture.localhost:<port>/lite/` -- the full page URL |
| 16 | `accept-encoding` | idx | |
| 17 | `accept-language` | idx | |
| 18 | `priority` | idx | `u=1, i` |

### h2 cors GET (set 3, stream 7)

Flags `0x25`, weight 220. Order: `:method` (idx 2), `:authority`, `:scheme`,
`:path` (noidx, name 4, H, `/api/search?q=x`), `sec-ch-ua-platform`,
`user-agent`, `accept` (inc, name 19, H, `application/json, text/plain, */*`,
set by the page), `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-fetch-site`
(`same-origin`), `sec-fetch-mode` (`cors`), `sec-fetch-dest` (`empty`),
`referer` (`https://capture.localhost:<port>/`), `accept-encoding`,
`accept-language`, `priority` (`u=1, i`). No `origin`, no `content-type`.

### h2 cors HEAD (set 4, stream 7)

Flags `0x25`, weight 220. `:method` is noidx, name 2, **raw** `HEAD`; `:path`
idx 4 (`/`). Order: `:method`, `:authority`, `:scheme`, `:path`,
`sec-ch-ua-platform`, `user-agent`, `sec-ch-ua`, `sec-ch-ua-mobile`, `accept`
(inc, name 19, raw, `*/*` -- Chrome's default, not set by the page),
`sec-fetch-site`, `sec-fetch-mode` (`cors`), `sec-fetch-dest` (`empty`),
`referer`, `accept-encoding`, `accept-language`, `priority` (`u=1, i`).

The `accept` slot is the one measured difference between sets 3/2 and set 4: a
page-set Accept sits between `user-agent` and `sec-ch-ua`; Chrome's default
`*/*` sits between `sec-ch-ua-mobile` and `sec-fetch-site`.

### HTTP/1.1 navigation (sets 5 and 6, identical)

Request line `GET / HTTP/1.1`, then in this order and casing:
`Host`, `Connection: keep-alive`, `sec-ch-ua`, `sec-ch-ua-mobile`,
`sec-ch-ua-platform`, `Upgrade-Insecure-Requests`, `User-Agent`, `Accept`,
`Sec-Fetch-Site`, `Sec-Fetch-Mode`, `Sec-Fetch-User`, `Sec-Fetch-Dest`,
`Accept-Encoding`, `Accept-Language`. No `Priority` header over HTTP/1.1. The
`sec-ch-*` names stay lowercase; every other name is canonical case. The
plaintext `http://` navigation (set 6) sends the same list, including
`Accept-Encoding: gzip, deflate, br, zstd`. Neither set carries
`Cache-Control` or `Cookie`, so their h1 slots are not measured here.

### ClientHello

17 non-GREASE extensions (0, 5, 10, 11, 13, 16, 18, 23, 27, 35, 43, 45, 51,
17613, 51764, 65037, 65281) plus a leading and a trailing GREASE extension, in a
per-connection permuted order (20 distinct orders out of 20 in every set).
`application_settings` (17613) payload `0003026832` = `["h2"]`. Extension 51764
payload byte-identical on all 180 TLS connections and all 20 CH2s. ECH GREASE
payload length drawn from {144, 176, 208, 240}. ClientHello handshake length =
1759 + len(SNI) + ECH payload length (`capture.localhost`: 1920/1952/1984/2016).
Set 7 (IP literal): no `server_name` extension, 16 non-GREASE extensions,
length = 1750 + ECH payload length (1894/1926/1958/1990). Sets 5/6: h1-tls still
offers ALPN `h2, http/1.1`; the server chose http/1.1.

### HelloRetryRequest (set 9, 20 of 20 connections)

Server: HRR selecting `0x0017` (secp256r1, offered in supported_groups without a
key share) with a 28-byte cookie. Chrome then sends:

- a ChangeCipherSpec record (type 20, 1 byte) BEFORE CH2, every time;
- CH2 in one record, handshake length = CH1 length - 1192 + 6 + cookie length
  (858 for SNI `capture.localhost`, ECH payload 240, 28-byte cookie);
- the same random, legacy session id, cipher list and GREASE values as CH1
  (bytes compared on `hrr-001.json`);
- the same extension order as CH1, with `cookie` (44) inserted at a position
  that varies per connection (anywhere from third to last among the 18
  non-GREASE extensions in these 20);
- `key_share` with ONE entry, `0x0017` with a 65-byte key (no GREASE share, no
  X25519MLKEM768, no X25519); `supported_groups` unchanged;
- the cookie echoed byte for byte;
- the ECH GREASE extension byte-identical to CH1's on `hrr-001.json`, and the
  same ECH payload length as CH1 on all 20.

## Consistency checks run for G1

- `diff` of every set against itself: no fixed difference; the only varying
  HTTP features are set 7 (1 of 20 connections is a reload with
  `cache-control`) and set 8 (connection 1 has no cookie yet).
- set 2 vs set 1a (stream 1): one fixed difference, `h2.hpack_reps`, because
  `:path` `/lite/` is a literal where `/` is indexed. Header order and values
  are identical.
- 1b (reload) as reference vs R-0017 (`.claude/tmp/chdump/chrome/`, also
  reloads; 12 fresh + 18 PSK-resumed connections) as candidate: exit 0, no
  fixed difference. The header order, values and HPACK representations of the
  navigation match. The ClientHello length is compared as
  `tls.clienthello_len_minus_sni` (handshake length minus the SNI hostname
  bytes), so the +8 of `capture.localhost` over R-0017's `localhost` is no
  longer a difference. R-0017's 18 resumed connections (pre_shared_key, 41)
  have no counterpart here -- these sets deliberately contain no resumed
  connection -- and `diff` reports them as
  `UNMATCHED resumed: present only in candidate (18 connections); not compared`
  instead of comparing them with the fresh group. The reverse direction gives
  exit 0 with the same group reported `present only in reference`.
- 1a (typed) as reference vs R-0017: exit 1, one fixed difference,
  `h2.header_order_raw` in the fresh group, because a typed navigation omits
  `cache-control: max-age=0`; the resumed group is reported unmatched as above.

## Refresh

1. `python3 -B Scripts/chrome_capture.py serve --port <p> --out .claude/tmp/<dir> --label <label> --count 66 [--alpn http/1.1 | --plain | --set-cookie tc=1 | --hrr-group 0x0017]`
2. In Chrome, navigate to the origin, pass the interstitial once
   (`#details-button`, then `#proceed-link`), then navigate (or reload, or run
   the set's JavaScript) once per connection with a 3 s pause between them.
3. Keep only the records without `handshake_error` (the third of each triple)
   and without an empty request list; copy them into one scratch directory per
   set.
4. `python3 -B Scripts/chrome_capture.py export --from <scratch dir> --to tests/files/chrome/<major>/<set> --label <label> --chrome-version <full version>`
   (`export` refuses any non-loopback identifier or a cookie the server did not set).
5. Re-run the checks above and update this README, including the JA4 table.
