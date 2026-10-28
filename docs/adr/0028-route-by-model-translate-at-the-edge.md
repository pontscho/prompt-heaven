---
name: 0028-route-by-model-translate-at-the-edge
type: adr
status: active
title: Route by model, translate at the edge
description: Decision to put one stdlib-only HTTP server, Scripts/llm-router.py, between Claude Code and three kinds of LLM backend -- an Anthropic Messages front that routes each request by its exact model string to a passthrough, llama.cpp or Mistral adapter, with every translation pure and sans-IO behind one validated request -- and to build its HTTP front as a declared, adapted copy of mcp-proxy's rather than a canonical source; with the deviations of the copy (the Anthropic error body, the fixed-text send_error and the HTTP/0.9 guard), the declared hand copies, the upstream pump unblocked by shutdown and never close, KD-5 to KD-18, the M1-M5 measurements that were not run and the defaults applied instead, the red-first FAIL counts per group, the four alternatives rejected, the reserved anthropic kind, the implementation's deviations from its plan, a validation-round-1 security fix pass (deviations 10-29, each red first) with the limits it added and the four findings left to a canonical source or mcp-proxy, and the declared limits.
links:
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0022-a-someday-maybe-is-a-roadmap-item
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - 0027-the-proxy-relays-it-never-composes
  - generated-regions
  - scripts
  - tests
---

# ADR 0028: Route by model, translate at the edge

**Status:** accepted 2026-10-05. The implementation, `Scripts/llm-router.py`, its
suite `tests/test_llm_router.py` and its fixtures under `tests/files/llm_router/`
were in the working tree and not yet committed when this page was written, so
the page carries no `sources:` and no `verified:` — its anchors are inline only,
as on [[0027-the-proxy-relays-it-never-composes]]. Every number below is a
measurement or a count at the moment of the decision (§4b's frozen-record
carve-out), quoted from the feature plan and from the red-first record kept
next to it during implementation (`.claude/tmp/llm-router-capture/measurements.md`,
a working file, not committed). The live values are in the code and in the
`SUITES` table of `tests/run.py`.

**Validation round 1 (2026-10-06).** A security review of the uncommitted
router was answered before the commit by one fix pass: every in-scope LOW
finding fixed, red first, in the router and its suite. Its behaviour changes
are deviations 12-24 and 26-29 below (10, 11 and 25 record three earlier
deviations the review found undeclared), its new declared limits are under "Declared
limits", the findings whose fix belongs to a canonical source or to mcp-proxy
are under "Out of scope, deliberately", and the case counts after it are in the
final-state paragraph at the end of "Red first" (the table there keeps the
original red-run counts). The `V<n>` and `F<n>` labels are the review's finding
numbers.

The decision in one line: Claude Code speaks only the Anthropic Messages API,
so the router speaks only that API to it, and every difference between a backend
and Anthropic is absorbed at the edge — in a per-kind adapter that never touches
a socket — rather than leaking into the front, the transport or the client.

## Context — one client, three dialects

Claude Code can be pointed at any server through `ANTHROPIC_BASE_URL` and
authenticates with `ANTHROPIC_AUTH_TOKEN` as `Authorization: Bearer <token>`.
The user runs three kinds of backend:

- **inferNO** on the LAN, which speaks the Anthropic Messages API natively;
- **llama-server** (llama.cpp), which has an Anthropic-compatible
  `/v1/messages` with known deviations — tool-result ordering, list-form
  `system`, no `adaptive` thinking type, a `tool_use` block start without
  `input`, its own error shapes;
- **Mistral** (`api.mistral.ai`), which has no Anthropic endpoint at all, only
  OpenAI-style `/v1/chat/completions` with a 9-character tool-id rule and its
  own streaming chunks.

One process should serve all three at once, chosen per request, with the
security posture of the fleet: stdlib only, secrets never in a log or an
error, no CORS, SSRF-safe outbound, structure-only logging
([[0011-a-truncated-payload-carries-the-first-cookie]]).

The user decisions the plan was built on: routing is by the **exact** model
string, with an optional default route (D1); backend keys and the router token
live in one JSON config held to a 0600 rule (D2); inbound auth is the bearer
only, with a per-kind allow-list of forwarded headers and no CORS (D3);
count_tokens is forwarded where the backend has it and estimated locally for
Mistral (D4); the logging region is generated and the HTTP front is copied, not
shared (D5); an `anthropic` kind is reserved but not built (D6); and the kinds
are built in the order passthrough, llamacpp, mistral (D7).

## Decision

1. **One new single-file script, `Scripts/llm-router.py`,** pure Python 3.9 and
   the standard library ([[0024-pure-python-39-and-the-stdlib]]), listed in
   `NEW_39_SCOPE` (`tests/test_py_deps.py`). It is not an MCP server: no
   `mcp-*.py` name, no smoke row, not matched by `TARGET_GLOB`. Its only
   generated region is `_mcp_logging.py :: _configure_logging`, so
   `Scripts/amalgamate.py:DECLARED_HOSTS` grew from two hosts to three
   ([[0025-generate-do-not-import]], [[generated-regions]]).
2. **Hub and spoke.** The front parses the JSON body once into an
   `InboundRequest` (`Scripts/llm-router.py:_rt_parse_inbound`), resolves the
   route once (`Scripts/llm-router.py:_rt_route`: exact match, then the default,
   else 404 `not_found_error`), and hands the request to one adapter per kind.
   The internal representation is the Anthropic request itself, so there is no
   third schema to keep in sync.
3. **Adapters are classes with class attributes**
   (`Scripts/llm-router.py:Adapter`, `PassthroughAdapter`, `LlamacppAdapter`,
   `MistralAdapter`): `kind`, `FORWARDABLE`, `DEFAULT_FORWARD` and
   `ROUTE_OPTIONS` are data; `upstream_request`, `count_tokens`,
   `json_response` and `stream_translator` are behaviour. `KIND_CLASSES` is
   what the loader and the forward-table check read; `ADAPTERS` is what the
   dispatch reads.
4. **Translation is pure.** Units 2-5 of the file (config, inbound, the SSE
   toolkit, the adapters) reference no `socket`, `ssl`, `http`, `select`,
   `time` or `threading` name; static case J4(c) enforces it between the
   `# Unit 2:` and `# Unit 6:` banners. Every translator is sans-IO — it takes
   lines and returns bytes — so the riskiest code is tested in-process with
   literal input and literal output.
5. **One writer to the client.** The handler thread is the only thread that
   writes the response; a streaming request adds exactly one reader thread,
   the upstream pump (KD-4).
6. **The HTTP front is an adapted copy of mcp-proxy's,** declared here, with
   `Scripts/mcp-proxy.py` itself unmodified (D5). Lifting it into a canonical
   source is recorded as a roadmap item, not done
   ([[0022-a-someday-maybe-is-a-roadmap-item]]).

## The HTTP front is a copy

The front (unit 7) and the entry point (unit 8) were copied from mcp-proxy's
Streamable HTTP front and adapted. The source anchors below are the plan's,
read at HEAD `69c01f0`; `Scripts/mcp-proxy.py` was not modified by this change.

| Plan | Copied from | Router | How |
|---|---|---|---|
| P7 | `Scripts/mcp-proxy.py:1999-2050` `_ProxyHttpServer` | `_RouterHttpServer` | adapted: no `core` / `loop` (no asyncio); adds `inflight`, `inflight_lock`, `active` under a `Condition` for the drain |
| P8 | `Scripts/mcp-proxy.py:2053-2124` `_HeaderDeadlineReader`, `setup`, `handle_one_request`, `parse_request` | same names in `_RouterHandler` | `_HeaderDeadlineReader` verbatim until round 1, now adapted (deviation 17); the three phases as written there, with a shorter header bound before the first authentication (deviation 16) |
| P9 | `Scripts/mcp-proxy.py:2126-2132` `log_message` | `_RouterHandler.log_message` | the shape kept: method and path without query, structure only; an unrouted path and an unhandled method are masked (deviation 21) |
| P10 | `Scripts/mcp-proxy.py:2156-2175` `_refuse` | `_RouterHandler._refuse` | **deviates** (below) |
| P11 | `Scripts/mcp-proxy.py:2177-2183` `_single_header` | same | verbatim |
| P12 | `Scripts/mcp-proxy.py:2185-2223` `_precheck` | `_RouterHandler._precheck` | adapted: two endpoint paths, the misplaced-token check 4b, an allowed-query set |
| P13 | `Scripts/mcp-proxy.py:2296-2325` body framing | `_RouterHandler._post` | Content-Length only, bounded, exact read; limit from `--body-limit`; a total read deadline (deviation 17) and a `100 Continue` sent only after the checks (deviation 18) |
| P14 | `Scripts/mcp-proxy.py:2430-2464` `_stream_sse` | `_RouterHandler._relay_stream` | the queue shape kept; the timer moved from "queue empty" to "time since the last client write" (KD-4); `event: ping` in place of the `: keepalive` comment |
| P16 | `Scripts/mcp-proxy.py:753-775` `_log_value` | same, in unit 1 | verbatim, with `_LOG_VALUE_WIDTH = 80`, `_LOG_KEYS_SHOWN = 16` |
| P17 | `Scripts/mcp-proxy.py:1878-1894` ready file (a method there) | `_rt_write_ready_file` (a module function) | 0600, atomic; removed again on a clean shutdown by `_rt_remove_ready_file`, which mcp-proxy does not do (deviation 29) |
| P5 | `Scripts/mcp-proxy.py:631-637` `_http_token_value` | same, in unit 1 | verbatim |

Verbatim copies from mcp-proxy keep their mcp-proxy names
(`_http_token_value`, `_log_value`, `_HeaderDeadlineReader`), so a diff against
the source stays trivial and a later lift is a move without renames.
`_HeaderDeadlineReader` stopped being verbatim in validation round 1 — its
per-recv cap and its docstring differ (deviation 17) — and keeps its name, so
the diff stays readable. Every other new module-level name uses the `_rt_`
prefix (KD-11). Static case J1
pins the hardening the copy must keep: `_HeaderDeadlineReader`,
`headers.defects`, and `hmac.compare_digest` called exactly once, inside
`_precheck`.

**Why the front is not a canonical source yet.**
[[0014-a-canonical-source-is-a-domain]] admits a new source when a shareable
block's *domain* cannot live in an existing one, and
[[0025-generate-do-not-import]] generates code that is **shared**. What the two
files share verbatim is three small units; the rest is adapted, and the
adaptation is the substance — mcp-proxy's front bridges into an asyncio core
and answers empty-bodied refusals, the router's is synchronous and answers
Anthropic envelopes. A canonical "stdlib HTTP front" would first need a
decision on what that domain is and which of these divergences are parameters,
and mcp-proxy was out of scope for this change (D5). The router is the second
file to carry the front, which is exactly the revisit trigger: the lift is a
roadmap item (horizon `later`), and the copy's drift risk (R13) is held by this
table, by J1, and by that item.

### The deviations of the copy, each with its reason

- **An Anthropic JSON body in `_refuse`.** mcp-proxy refuses with an empty
  body; the Anthropic SDK shows the body's `error.message` to the user, so every
  refusal here is `{"type":"error","error":{"type":...,"message":...}}` with the
  type from `_STATUS_TO_TYPE` and `Connection: close`. A `HEAD` gets the head with
  `Content-Length: 0` and no body. (mcp-proxy's empty pre-auth refusals were left
  unfixed there as F42 in ADR 0027; the router does not inherit that gap.)
- **An overridden `send_error` with fixed texts.** The stdlib calls
  `send_error` itself before any `do_*` runs — malformed request line 400, 414,
  431, unknown method 501, 505. Its `message` / `explain` can quote the request
  line or the method (measured: `BREW` produces the stock reason
  `Unsupported method ('BREW')`), so `Scripts/llm-router.py:_RouterHandler.send_error`
  ignores both and answers `_RT_STDLIB_REFUSAL`'s fixed text per status
  through `_refuse`.
- **The HTTP/0.9 guard — and where it lives.** On Python 3.9
  `parse_request` sets `request_version` to `default_request_version`,
  `"HTTP/0.9"`, before it parses the version word, and `send_response_only` /
  `send_header` write nothing for HTTP/0.9 (`http/server.py`, 3.9.21, read for
  this page). So a refusal sent while the version is still `"HTTP/0.9"` goes out
  as a bare JSON body with **no status line and no headers**. Group B's
  measurement confirmed the plan's unverified claim on 3.9.21 and 3.14.0: a stock
  handler answers `POST /v1/messages?s FOO/1.1` with the HTML error body and no
  status line. The plan put the guard in `send_error`. **It lives at the top of
  `_refuse` instead**, because a bare two-word `GET /` is valid HTTP/0.9 to the
  stdlib: it never passes through `send_error`, reaches `do_GET`, and its 405
  would have had no status line either. Every refusal goes through `_refuse`, and
  `send_error` reaches it too, so one guard covers both paths. Case B21 checks
  that each stdlib refusal starts with an `HTTP/1.1 <status>` line and carries its
  headers.

## The hand copies

`Scripts/_mcp_chrome.py` is a WHOLE source (G-c): a host takes all of its
blocks or none. The router needs its address classifier and none of the Chrome
client, so three names are **hand-copied verbatim, with their source names**,
from `Scripts/_mcp_chrome.py:5770-5833`: `_CH_TRANSLATION_PREFIXES`,
`_ch_embedded_ipv4` and `_ch_address_refused`. Each has a `HAND_COPY_REASONS`
entry in `Scripts/amalgamate.py` with the reason "_mcp_chrome.py is a WHOLE
source (G-c); the router needs its address classifier and none of the client",
so the census shows no `[UNDECLARED]` row. Because the generated_region
suite's census case is INFO, the router's suite adds two FAIL-grade gates:
**J13** (no census row for `llm-router.py` without a reason) and **J20** (the
`ast.dump` of each copy equals its source's, docstrings included; its
sensitivity was checked by widening `2002::/16` to `/17` in memory, which J20
reported as drift).

The adapted policy and connect functions are named `_rt_resolve` and
`_rt_open_socket`, not copies, because their error type and messages differ
(`UpstreamError`, not `ChromeClientError`); the census therefore does not list
them. No other canonical block name (`_result`, `_error`, `_bool_param`,
`MAX_INFLIGHT_REQUESTS`, ...) is bound by the router.

## The upstream pump, and why it is unblocked by `shutdown`

**Why a thread.** A blocking `HTTPResponse.readline()` cannot be given a 15 s
timeout to emit a ping. The stdlib's buffered socket reader does not survive a
timeout: once a `recv_into` under it raises `socket.timeout`, `SocketIO`
records `_timeout_occurred` and every later read raises
`OSError("cannot read from timed out object")` (`socket.py`,
`SocketIO.readinto`, read in the local 3.9.21 and 3.14.0 sources for this page).
The documentation states the same constraint for `socket.makefile`: the socket
may have a timeout, but the file object's internal buffer may end up in an
inconsistent state if a timeout occurs
(<https://docs.python.org/3/library/socket.html#socket.socket.makefile>). The
design never resumes a read after a timeout, so it does not depend on the
wording: the upstream socket keeps `idle_timeout` as its timeout (a real
failure), and one daemon thread per streaming request,
`Scripts/llm-router.py:_rt_pump`, loops `readline(_SSE_LINE_LIMIT + 1)` into a
byte-bounded `_RtByteQueue(_RT_QUEUE_BYTES)`.

**The handler loop.** The handler waits on the queue in 1 s ticks
(`_RT_TICK_S`). On every tick or item it checks, in order: shutdown, upstream
silence past `idle_timeout`, the item, a ping if `_PING_INTERVAL_S` (15 s) have
passed since its **last write to the client**, and — when nothing has been
written for a tick — a client-EOF poll (`select` plus `recv(1, MSG_PEEK)`,
`Scripts/llm-router.py:_RouterHandler._rt_client_gone`). The timer is "since the
last client write", not "queue empty", because buffered Mistral tool-call
deltas keep the queue busy while nothing reaches the client (KD-5, case H8). No
ping precedes `message_start`.

**`shutdown(SHUT_RDWR)`, never `close()`.** `HTTPConnection.close()` does not
unblock a reader blocked in `HTTPResponse.readline()`: the fd is held by the
response's `makefile` (P24, the same fact as mcp-proxy's comment that an
unclosed makefile keeps the socket's fd alive), and for a chunked keep-alive
response `close()` would block on the lock the reader holds. So
`_RtHttpConnection.connect()` records the connected socket as `conn.raw_sock`,
`srv.inflight` holds it while the stream lives, and on idle expiry, client
disconnect or shutdown the handler (or the shutdown path) calls
`socket.socket.shutdown(raw_sock, socket.SHUT_RDWR)`. For TLS that is the
**base-class** method called on the `SSLSocket`, which avoids
`SSLSocket.shutdown`'s own side effect of dropping the SSL object under the
reader; the blocked `SSL_read` sees EOF and raises
(`suppress_ragged_eofs=False`). The pump then puts its terminal item and
performs the final `resp.close()` and `conn.close()` itself, on the thread that
owns the reader.

**Measured, not assumed.** The plan marked the TLS half of this UNVERIFIED.
Case H7 runs over **verified TLS** in-process with `max_connections=2`: five
streams each disconnect after their first event while the peer holds the
upstream open and silent. Against the reference shape (the task-039 probe) the
five peers saw their sockets closed after 1.00-1.01 s each, the `rt-pump`
thread count returned to 0 after each, `threading.active_count()` returned to
its baseline, and a sixth stream opened; on the same run H3 measured the peer
closed 1.01 s after the hang-up. So the base-class `shutdown` on an
`SSLSocket` does unblock a pump blocked in `SSL_read`. With the client-EOF poll
patched out, H7 leaked the slot, visible as the raw-cap 503 on stream 3.

**The join comes before the slot release.** The connection cap is a
`BoundedSemaphore` released in `process_request_thread`'s `finally`. The
handler joins the pump (in `_PUMP_JOIN_S` slices, bounded by
`backend.idle_timeout`) before it returns, so the slot covers the pump and a
client that connects and drops cannot accumulate pump threads and upstream
sockets past `--max-connections` (R17, CWE-400).

**Rejected: a heartbeat writer with a lock** (alternative D below), and
`conn.close()` as the unblocking call (the round-0 draft of KD-4), which P24
shows does not unblock the pump.

**Measured on the way: `readline(limit)` is not a hard cut.**
`HTTPResponse.readline(limit)` does not honour `limit` on a **chunked** body.
With `limit` 1048577, a chunked line of 1048577 + 1 bytes came back whole
(1048578 bytes, newline included); a chunked 4 MiB + 1 line returned 1056760
bytes on 3.9.21 and 1179640 on 3.14.0; the same 4 MiB line close-delimited
returned exactly 1048577. So the pump tests `len(line) > _SSE_LINE_LIMIT`
rather than a missing trailing newline (case C8 runs over chunked framing and
catches the newline heuristic), and the per-line memory bound on a chunked
upstream is the limit plus one read-ahead window, not the limit itself.

**Measured on the way: ragged EOF on 3.9.** On Python 3.9.21 with OpenSSL 3,
`ssl.create_default_context()` sets `OP_IGNORE_UNEXPECTED_EOF`, which would
turn a truncated TLS stream into a clean EOF. `_RtHttpsConnection` clears it,
so a ragged EOF before `message_stop` is an error ("the stream was cut"), and
D12's control half — the same ragged EOF before `message_stop` — proves the
clear is real on both interpreters.

## KD-5 to KD-18

- **KD-5 Mistral tool calls are buffered and emitted whole.** Text deltas stream
  live; tool-call deltas are collected by `index` (falling back to `id`) and
  emitted at the end of the turn, in index order, as
  `content_block_start(tool_use)`, one `input_json_delta` holding the complete
  re-validated JSON, and `content_block_stop`. An Anthropic stream cannot reopen
  a closed block, and Mistral may send arguments for index 0 after index 1 has
  started. The buffer is bounded per stream by `_RT_BUFFERED_TOOL_LIMIT`
  (8 MiB, counting the arguments and, since validation round 1, each tool's
  stored id and name) and by `_RT_BUFFERED_TOOL_COUNT` (at most 128 tool
  calls); an overrun of either is `event: error` at the chunk that crosses it,
  never at the end (G12, deviation 26). The cost — Claude Code sees a tool call only when it is
  complete — is acceptable, since it cannot run a half-built call.
- **KD-6 The tool-id map is a pure function of the request.** A Mistral id
  matching `^[A-Za-z0-9]{9}$` becomes `toolu_lr` + those 9 characters
  (`_MINTED_PREFIX`), and on the way back maps to **exactly** its 9 characters,
  so Mistral sees its own ids in a resent history. Every other id hashes to 9
  base62 characters of `sha256(b"llm-router/tool-id/v1\0" + id)`; minted ids are
  reserved first, the others are processed in sorted order, and a collision
  walks `\0<n>` until unique (`Scripts/llm-router.py:_rt_ms_tool_id_map`,
  `_rt_ms_anthropic_id`). Same answer on every resend, across restarts, with no
  table to persist. A forged `toolu_lr…` id is harmless: it maps to its own 9
  characters and the walk keeps the request consistent.
- **KD-7 No successful `message_stop` after an upstream failure.** The encoder
  closes the current event, emits `event: error` and closes; it never invents
  a stop. A fake stop would make Claude Code treat a truncated answer as
  complete, and possibly act on half a tool call.
- **KD-8 An upstream 401 or 403 is a 502 `api_error`** naming the backend,
  never a 401: a 401 from the router means only "your router token is wrong",
  and Claude Code must not take a misconfigured backend key for an invalid
  login (R14).
- **KD-9 Upstream error text is scrubbed** of every configured secret in
  each of its forms — the value, its URL-encoded form, standard and URL-safe
  base64 (padded and unpadded), and its first and last 8 characters when it is
  at least 16 long — matched longest first, then control-stripped and cut to
  300 characters. The scrubber is built once in unit 1
  (`Scripts/llm-router.py:_rt_make_scrubber`), stored as `RouterConfig.scrub`,
  and reaches units 4-5 **only** as `InboundRequest.scrub`; no adapter, relay or
  quirk reads `cfg` or a module global. The scrubbed text goes to the client
  only and is never logged.
- **KD-10 Passthrough relays per event, not per byte read.** The bytes of each
  event are unchanged, but whole events are forwarded, so a ping or a final
  `event: error` is injected at an event boundary and never inside a half
  `data:` line.
- **KD-11 Hand copies keep their canonical names and are declared; everything
  new is `_rt_*`** (`_rt_ll_*` for llama.cpp quirks, `_rt_ms_*` for Mistral). No
  `_lr_` prefix anywhere. See "The hand copies".
- **KD-12 The adapter is a class, not a dict of lambdas.** One class body holds
  everything about a kind. `KIND_CLASSES` maps every implemented kind to its
  class; `ADAPTERS` holds an instance per kind whose methods have landed;
  `RESERVED_KINDS = ("anthropic",)`. A kind in `KIND_CLASSES` but not in
  `ADAPTERS` answers 501 "backend kind <kind> is not wired yet" — unreachable
  today, kept as the guard for a future kind.
- **KD-13 llama.cpp quirks are registry rows, each idempotent.**
  `LLAMACPP_REQUEST_QUIRKS` (`tool-results-first`, `hoist-system`,
  `adaptive-thinking`, `sampling-override`) and `LLAMACPP_EVENT_QUIRKS`
  (`tool-use-input`, `error-shape`) are `(name, upstream_ref, fn)` rows. Every
  function returns a new value, is idempotent and is a no-op on conforming input
  (E2, E6); `quirks_off` in a route disables a row by name without a code change,
  so a suspected upstream fix can be tried live. Deleting a fixed quirk is
  deleting one row, its function and its test.
- **KD-14 Error mapping is two tables.** `_STATUS_TO_TYPE` maps a status the
  router answers to its Anthropic error type; `_UPSTREAM_STATUS` maps an
  upstream non-2xx status to the router's (401 and 403 to 502 per KD-8, 422 to
  400, 502/503/504 to 529, anything unlisted to 502). A passthrough upstream's own
  `error.type` is kept only when it is in `_ANTHROPIC_ERROR_TYPES` and the status
  was not remapped.
- **KD-15 Response head first, then decide.** The router sends its own head only
  after the upstream status (and, for a stream, its `Content-Type`) is known. A
  non-2xx upstream on a `stream: true` request is a JSON error envelope, never a
  200 `text/event-stream` carrying an error (H5); a 2xx that is not an event
  stream is a 502.
- **KD-16 Model echo.** Translated and normalized answers (mistral; llamacpp's
  `message_start` and non-stream body) carry the model string **the client
  requested**, also on the default route — never `route.name`, which is
  `"(default)"` and used in logs only (E13, G17). Passthrough rewrites nothing in
  the response and accepts that the upstream's model id is echoed (D11).
- **KD-17 Network settings on the command line, routing and secrets in the
  config.** `--bind`, `--port`, `--allow-remote`, `--allowed-origin`,
  `--ready-file`, `--body-limit`, `--max-connections`, `--debug`, `--log-file`
  mirror mcp-proxy's flags, so the copied front and its tests port with minimal
  change; the config holds only `auth_token`, `backends`, `routes`, `default`.
  One source per setting, no merge rule. There is no `--config-json`: argv is
  visible in `ps`, and every config here carries a secret.
- **KD-18 `ca_file`, `allow_private` and `allow_loopback` are explicit
  per-backend options for every kind.** `ca_file` is a trust anchor and is
  vetted like the config (`O_NOFOLLOW`, `fstat`, regular file, owned by the
  effective uid or root, no group/world write, at most 1 MiB), read once into
  `BackendSpec.ca_pem`, and passed as `cadata=` — which replaces the system trust
  store for that backend and never reopens the path. It is also what lets the
  suite test the **verified** TLS path against `tests/files/tls/test-ca.pem`
  (C10, D12, G15, H7). `allow_private` (default false for every kind) admits
  private, ULA, CGNAT and site-local addresses only, minus three networks
  inside them that `Scripts/llm-router.py:_RT_PRIVATE_REFUSED_NETS` refuses
  since validation round 1: the cloud metadata addresses that are not
  link-local — `fd00:ec2::254` (a ULA) and `100.100.100.200` (in CGNAT, also
  in its IPv4-mapped form) — and Teredo `2001::/32` (deviation 28). The rest of
  CGNAT and ULA still passes. Link-local (`169.254.169.254` included),
  multicast, unspecified, reserved and the translation prefixes stay refused
  under it (`Scripts/llm-router.py:_rt_private_policy_refused`, C12, C13), and
  loopback needs `allow_loopback` as well (C14).

KD-1 to KD-4 are covered above or in the plan's security perimeter: the token
lives in the config, not a token file or the environment (KD-1); duplicate JSON
keys are refused at every depth and route names colliding under `casefold()`
are refused ([[0015-ambiguity-is-the-defect]], KD-2); the upstream credential is
bound to the backend object and client headers pass through a per-kind
allow-list with an import-time disjointness check against `_NEVER_FORWARD`
(KD-3, J12); a forwardable client header sent more than once is refused with a
400 `invalid_request_error` naming the header, never its values, before anything
reaches the upstream (`Scripts/llm-router.py:_rt_parse_inbound`,
[[0015-ambiguity-is-the-defect]], D2, deviation 10) — in the parse rather than
the front's precheck, because which names are forwardable depends on the route
the body's `model` selects; and the pump (KD-4).

## M1-M5: not measured; the defaults applied

The plan's Step 1 was to retire the unknowns before any code depended on them:
M1-M3 capture Claude Code's own wire behaviour against a loopback listener, M4
captures a real llama-server, M5 a real Mistral account. **All three
measurement tasks are user-gated and none had run when this page was written.**
The code applies documented defaults in their place, and every place a default
stands in for a measurement is marked `G-M1 DEFAULT` or similar in the source.

| Gate | Question | Status | Default applied |
|---|---|---|---|
| G-M1 | chunked or Content-Length request bodies; largest body; path, query and Host | not measured | Content-Length framing only, `411` for `Transfer-Encoding` (B9; the suite's `GB_CHUNKED_DECODED = False`); `--body-limit` default 32 MiB (`_ROUTER_BODY_LIMIT`); allowed queries `{"", "beta=true"}` (`_RT_ALLOWED_QUERIES`, the suite's `GB_M1_QUERY`) |
| G-M1b | which HTTP methods Claude Code sends | not measured | every non-POST method is a 405 with `Allow: POST`; unknown methods are the stdlib's 501 through the `send_error` override |
| G-M2 | how often count_tokens is called | not measured | built for the hot-path case anyway: the mistral estimate translates the body once per request (see deviation 6) |
| G-M3 | the exact model strings | not measured | the documentation example's route keys (`claude-sonnet-4-5`, `claude-haiku-4-5`) are assumptions; E13 and G17 use `claude-unrouted-x` for the default route |
| G-M45 | the longest SSE line in M4/M5 | not measured | `_SSE_LINE_LIMIT` 1 MiB, `_SSE_EVENT_LIMIT` 2 MiB |
| G-M5 | which Mistral rules (name on the tool message, 9-character id, the bridge message) hold | not measured | all implemented as harmless defensive transforms; F3, F4, F5 print "defensive, not observed (A-7 Mistral rule, pending M5)" |

**Every `tf_*` fixture is synthetic** — "defensive, not observed" — pending M4
and M5: `tf_llamacpp_stream_tool.sse`, `tf_llamacpp_error.json`,
`tf_llamacpp_stream_error.sse`, `tf_mistral_stream_text.sse`,
`tf_mistral_stream_tool.sse`, `tf_mistral_stream_parallel.sse` and
`tf_mistral_error_extra_inputs.json` were hand-written in the shapes the plan
and the feature brief describe; E8, E10, E13, F11, G1, G2, G3 and G15 print
"defensive, not observed". When M4/M5 run, a capture that refutes a fixture's
shape replaces the fixture, and a rule M5 refutes stays as a labelled defensive
transform. The exact-value sweep of the user's real Mistral key is M5's, and
has not run either; J5 sweeps by pattern and sentinel only.

One measurement that did run, outside M1-M5: **C9** (a connect into a full
backlog) is a hard FAIL/PASS on this macOS, not the INFO the plan allowed for:
on Darwin 23.2.0 with 3.9.21, 129 non-blocking filler connects filled the
backlog and the next connect stayed pending 0.2 s, and the group C probe's
transport then failed with `socket.timeout`, mapped to a 504, after 1.00 s
(`connect_timeout` 1).

## Red first: what each group measured

SC-3 required every risk's case to be observed failing before its mitigation
landed. The counts, per group, as recorded (all on Python 3.9.21 and 3.14.0
unless noted):

| Group | Red run against | FAIL count |
|---|---|---|
| A (config, 24) | the skeleton (`main()` exits 2, no `load_config`) | 23 fail, 1 info (A22: not root, cannot chown) |
| B (front, 22) | the skeleton (argparse refuses every start) | 22 fail |
| I (secret leak, 7) | Step 4 landed, serving refused | 6 fail, 1 pass (I6) |
| C (outbound, 13) | Steps 4-5 landed, unit 6 empty | 13 fail |
| C8, J6, J7, J8, J12, J21 (6) | Steps 4-6 landed, no pump or encoder | 6 fail |
| D (13) + H1-H6 × passthrough + H7 (20) | every routed request 501 | 20 fail |
| E (13) + H1-H6 × llamacpp (19) | no llama.cpp registries, 501 | 19 fail |
| F (16) | `MistralAdapter` attribute-only | 16 fail |
| G (17) + H1-H6 × mistral + H8, H9 (25) | no `MistralStreamTranslator`, 501 | 25 fail |
| I7 widened to three kinds | a router copy whose scrubber is the identity (3.14.0) | I7 fail on every route, I2 fail as a by-product (I: 5 pass, 2 fail) |
| J1-J4, J13 via J15-J19 | each control's checker replaced by a no-finding stub (3.14.0) | 5 / 5 controls red |
| J5 via J22 | the sweep replaced by a no-finding stub (3.14.0) | J22 red, 2 problems |

**A missing symbol is not the defect.** Where a group's first red run could
only show "the module defines no `<name>`", a throw-away probe (C, C8/J, D/H,
E/H, F, G/H) injected or appended a minimal reference implementation with one
defect switched on per run, without editing `Scripts/llm-router.py`, so each
case was shown to **see** its defect. Examples: C1/C2/C14 against a naive
`http.client` transport ("the peer recorded 1 request; expected none"); C4/C5
against a `urllib` transport (redirect followed, `*_PROXY` honoured); D4's
comment-per-ping relay (3 pings in 16.5 s instead of 1); D12's relay that stays
open after `message_stop`; F7's walk in input order; F16's minted id hashed in
sorted order — which the first probe run measured **green** until F16's hashed
id was changed to sort before `toolu_lr...`; G12's "fails eventually" cap, which
only the crossing-line assertion separates from a bounded one; E13 and G17
against an echo of `route.name`, which E10 and G16 cannot see by construction.

**J1 was red against the real router.** Before the fix, rule J1 found two
`compare_digest` calls (the bearer check in `_precheck`, and the config-time
`api_key` vs `auth_token` check in `_rt_cfg_backend`). The router was changed
(deviation 5); J1 has been green since.

**Not observed red, and why:**

- **I6** (`repr` redaction) was green when group I was first run: Step 4's
  `__repr__` override landed before the case was written.
- **J14** (the per-request summary line) was green on its first run against the
  router of Step 7; no router change was needed.
- **The exemptions SC-3 declared:** C7 (pins the stdlib's chunked `readline`
  on 3.9); C11 and J20 (pin verbatim hand copies); J9-J11 (hygiene, copied from
  the proxy suite).

**Validation round 1, red first too.** Each of its seventeen behaviour
changes (deviations 12-24 and 26-29) was observed failing in its case before
its fix landed, then passing after it. Five cases are new — A25, A26, B23, B24
and D14 — and the rest are subcases added to existing cases (A13, A21, B13,
B14, B15, B22, C9, C12, C13, E8, G12, G14, I1).

**The final state, after validation round 1:** `llm_router` 174 cases, 173
pass and 1 info (A22), 0 fail; `generated_region` 97 (96 pass + 1 info);
`py_deps` 59 (54 pass + 5 info); `python3 Scripts/amalgamate.py --check`
exit 0. The case table: A 26, B 24, C 14, D 14, E 13, F 16, G 17, H 21 (H1-H6
× three kinds, H7, H8, H9), I 7, J 22. At the decision, before the round, the
suite had 169 cases (A 24, B 22, D 13), 168 pass and 1 info, on 3.9.21 and
3.14.0.

## Alternatives rejected

- **(A) A direct Anthropic↔OpenAI converter without an internal
  representation.** Each kind would parse, validate and dispatch on its own:
  three places to forget the `stream` flag, the model rewrite or the header
  allow-list, and the two security checks that could be dropped — header
  forwarding and the credential — copied three times. It does nothing for
  passthrough or llamacpp, which need no conversion; hub-and-spoke already
  contains that converter as one adapter.
- **(B) LiteLLM or the oh-my-pi gateway as an external process.** Breaks
  [[0024-pure-python-39-and-the-stdlib]] (a large third-party tree, or a
  Node/Bun runtime), adds a second credential store the router cannot audit,
  adds a supervision problem that mcp-proxy needed a whole restart budget to
  solve ([[0027-the-proxy-relays-it-never-composes]]), and inherits a security
  posture rather than owning one — omp's wildcard CORS is exactly what D3
  refuses. The router ports omp's *ideas* (normalizing Mistral tool ids, an
  assistant message after a tool result, an encoder that always closes its
  envelope) and none of its code.
- **(C) An asyncio server.** mcp-proxy is asyncio because it multiplexes
  long-lived child pipes; the router has no children, and one request is one
  upstream connection relayed to completion, which fits a thread. asyncio would
  need its own HTTP/1.1 server and its own streaming TLS client, and would reopen
  the serialized-read-loop hazard of
  [[0008-a-serialized-read-loop-looks-like-a-dead-server]] for no gain. The one
  thing a thread cannot do cheaply — a timed wait on a buffered socket — is
  solved by one reader thread.
- **(D) A heartbeat writer thread guarding `wfile` with a lock.** Both earlier
  drafts proposed it: the handler blocks in `readline()`, and a second thread
  writes `event: ping` under a lock shared with the relay. It puts **two
  writers** on the client socket: every write site must take the lock, a missed
  lock is a corrupted half event, and a disconnect is seen by whichever thread
  writes first. The pump keeps one writer and moves the blocking read to the side
  that has no output — the shape mcp-proxy already uses.

## The reserved `anthropic` kind

A fourth kind — real Anthropic, emulating Claude Code's own client identity —
is **reserved, not built** (D6). `_KINDS` includes `"anthropic"` and
`RESERVED_KINDS = ("anthropic",)`; `load_config` refuses it with
`backends.<name>.kind: "anthropic" is reserved and not implemented` (A14). The
seam is the enum value, the refusal, a row in the KD-3 header table ("decided by
its own plan"), `ROUTE_OPTIONS` for its own validators, and J4(b)'s rule that
only `_rt_upstream_headers` reads `client_headers`, so no other path can forward
a client header when it lands.

It is gated because its first question is not technical. Its full design is a
separate future plan, and no code is written until the user answers the first
question explicitly:

- **ToS gate.** Is emulating Claude Code's client identity (beta headers, user
  agent, system-prompt preamble) permitted under the Anthropic ToS/AUP for this
  account?
- **Credential and OAuth refresh.** API key or OAuth subscription token; if
  OAuth, where the refresh token lives, who refreshes it, and how two in-flight
  requests avoid a refresh race.
- **Beta union.** Is `anthropic-beta` forwarded verbatim, filtered, or sent as
  the union of the client's values and the ones the identity requires?
- **Session-id source.** The client's own header, or one minted per Claude Code
  process.
- **Fingerprint.** Which identity headers (`user-agent`, `x-app`,
  `anthropic-dangerous-direct-browser-access`) are forwarded, re-synthesised or
  dropped.
- Prompt caching (`cache_control` and `usage.cache_*` relayed), rate-limit
  headers relayed, whether count_tokens is forwarded, and whether this kind may
  share a process with LAN backends.

## Deviations from the plan, made during implementation

1. **The HTTP/0.9 guard is in `_refuse`, not `send_error`** — a bare `GET /` is
   HTTP/0.9 to the stdlib and reaches `do_GET` without passing `send_error`
   (above).
2. **Check order in `_precheck`.** The misplaced-token scan (4b) runs after the
   bearer check and **before** the allowed-query check: a token in the query is
   the misplaced-token 400, not a 404 (B7, FR-9), and an unauthenticated request
   with a bad query still gets the 401.
3. **Signal handlers are installed before the ready file is written,** so a
   supervisor that sends SIGTERM as soon as the file appears reaches the ordered
   shutdown and not the default action (`Scripts/llm-router.py:main`).
4. **`EventRelay` turns an upstream `:` comment into nothing.** The plan said a
   comment becomes `event: ping` under the ping rule; the handler's
   last-write timer is already a ping source, so the two would double up. The
   timer is now the only source; D4 measures exactly one ping in 16.5 s of
   comment-only upstream traffic.
5. **The config-time `api_key != auth_token` check is a plain `==`,** not
   `hmac.compare_digest`, so that `compare_digest` is called exactly once, in
   `_precheck`, as J1 requires. Both values come from the operator's own 0600
   file; no peer can time that comparison.
6. **`MistralAdapter` keeps a small dict keyed by `id(inbound)`** (at most
   `_RT_MS_PENDING_CAP` = 64 entries, evicted oldest first) holding only the
   integer token estimate between `upstream_request` and `stream_translator`, so
   the body is translated once per request (G-M2) without `threading.local`,
   which J4(c) forbids in units 2-5. A miss costs a second translation, never a
   wrong answer.
7. **The `TF_KEY_MISTRAL` sentinel is `tfkey-mistral-SENTINEL+` + hex,** not
   `SENTINEL-` (plan:1240). With only unreserved characters its URL-encoded form
   equals the value, so I7's URL-encoded message duplicated the value message;
   the reserved `+` (`%2B`) makes the two forms differ, and I7 now fails if they
   ever coincide again.
8. **The J14 summary line writes `_log_value` forms.** The plan's §9 example
   shows bare values (`endpoint=messages`) and its rule says "values through
   `_log_value`"; `_log_value` `repr()`s a string, so the router writes
   `endpoint='messages'`. The code follows the rule. Only a POST gets a `req`
   line (`do_POST` owns the summary), and that is by design: a non-POST is
   refused by the precheck or with the 405 before any body, route or upstream
   exists, so there is nothing to summarise, and the access line already
   records its method and path (masked per deviation 21).
9. **Transport signatures.** `_rt_open_socket` maps a passed connect deadline to
   a 504 itself (the plan's error table already said "connect timeout → 504");
   `_rt_read_body` takes an optional `backend_name` for its messages.
10. **A forwardable client header sent twice is a 400.** In
    `Scripts/llm-router.py:_rt_parse_inbound`, a header the routed backend's
    `forward_headers` names that arrives more than once is refused as
    `invalid_request_error` naming the header, never either value
    ([[0015-ambiguity-is-the-defect]]; D2). The plan's allow-list does not say
    what a repeated header means. It sits in the parse, not the precheck,
    because the forwardable set depends on the route.
11. **`Content-Type` parameters are ignored.** The plan says "Content-Type
    exactly `application/json`". `_post` checks exactly one
    `Content-Type` header (B12) and compares only its media type — the part
    before the first `;`, stripped and lower-cased — with `application/json`, so
    `application/json; charset=utf-8` passes. A `charset` parameter is not read:
    the body is decoded as UTF-8 whatever it says, and a body that is not UTF-8
    is the 400 "request body is not valid JSON".

Validation round 1 (2026-10-06), one item per behaviour change, each with the
case that saw it red first:

12. **The bearer token must hold at least 8 distinct characters** (V1, A13).
    `Scripts/llm-router.py:_rt_cfg_token` applies `_http_token_value`'s rules
    (at least `_TOKEN_MIN_LEN`, 32, printable ASCII, no whitespace) and then
    `_TOKEN_MIN_DISTINCT` (8): 32 × `a` is long, not secret. Every `auth_token`
    refusal names the generator `secrets.token_urlsafe(32)`, never the value.
    There is no 401 throttle, so the loader refuses the obviously weak token
    instead; it is not an entropy measurement.
13. **A backend `api_key` must be at least 16 characters** (V15, A25,
    `_API_KEY_MIN_LEN`). KD-9 adds the first/last-8 scrub forms only to a
    secret of 16 or more characters, and a short key's own value would redact
    every common substring it happens to match.
14. **An `api_key` is not sent over a non-loopback `http://` URL without an
    opt-in** (V14, A26). With an `api_key` and an `http://` `base_url`, the
    loader refuses the backend unless its host, as parsed and never resolved,
    is `localhost` or a loopback literal
    (`Scripts/llm-router.py:_rt_is_loopback_host`), or the backend sets the new
    key `allow_cleartext_api_key: true` (a JSON bool, default false; any other
    type is refused). On a backend without an `api_key` the key is accepted and
    has no effect. The plan's own section-3 example (A17) sends its passthrough
    key over `http://192.168.1.20:8080`, so the example now carries the opt-in.
15. **`--allow-remote` refuses an unspecified or public bind** (V2, A21).
    `Scripts/llm-router.py:_rt_settings` refuses `0.0.0.0` and any
    `is_global` address with `--allow-remote`, so "a VPN interface, never a
    public one" is enforced, not only stated. `100.64.0.0/10`, the CGNAT range
    VPNs such as Tailscale use, is not `is_global` and stays allowed.
16. **A connection that has never authenticated gets a shorter header bound**
    (V3, B14). `_RouterHandler.handle_one_request` sets the total header
    deadline to `_HTTP_PREAUTH_TIMEOUT_S` (5 s) until a request on that
    connection has passed `_precheck`, and `_HTTP_HEADER_TIMEOUT_S` (10 s)
    after, so an unauthenticated socket gives its `--max-connections` slot back
    sooner. A mitigation only: see "Declared limits".
17. **The body read has a total deadline** (V4, B23). `_post` sets the
    reader's deadline to `_HTTP_BODY_TIMEOUT_S` (60 s) around the exact read,
    so a token holder that trickles its body cannot keep a slot; past it the
    connection is closed without an answer, as a header-phase timeout is. One
    recv is capped at `_HTTP_SOCKET_TIMEOUT_S` (30 s), where mcp-proxy caps it at
    its header timeout, so `_HeaderDeadlineReader` is no longer a verbatim copy
    (the header phase's own totals are shorter than either cap, so it is
    unchanged there).
18. **`100 Continue` is sent only after authentication and framing** (V37,
    B22). The stdlib sends the interim `100 Continue` from `parse_request`,
    before any `do_*` runs, so an unauthenticated client was invited to send
    its body. `_RouterHandler.handle_expect_100` now sends nothing, and `_post`
    sends `100 Continue` itself (on HTTP/1.1, for `Expect: 100-continue`) only
    after `_precheck`, the shutdown 529, the 415 and the 411/413 framing checks
    have passed.
19. **A forwardable header whose value holds CR or LF is a 400** (V29, B13).
    An obs-fold continuation or a bare CR survives the stdlib's header parse
    and `http.client`'s `putheader` check, and a lenient upstream could read it
    as a header of its own. `_rt_parse_inbound` refuses it next to the
    duplicate rule of deviation 10, naming the header, never the value.
20. **Non-finite numbers are refused, and `max_tokens` has a ceiling** (V19,
    B24). `Scripts/llm-router.py:_rt_loads` — `json.loads` with a
    `parse_constant` that raises on `NaN`, `Infinity` and `-Infinity` and a
    `parse_float` that refuses a literal overflowing to an infinity (`1e999`) —
    parses the inbound body and every upstream body or event the router
    parses, and `_rt_dumps` serialises with `allow_nan=False`. Inbound, a
    non-finite number is the 400 "request body is not valid JSON". In an
    upstream body the router parses whole — a non-stream answer, a Mistral
    stream chunk — it takes that path's malformed-answer route (a 502 or an
    `event: error`). `max_tokens` above `_RT_MAX_TOKENS_LIMIT` (1000000) is a
    400. The two relays are unchanged: passthrough parses only `event: error`,
    and an llama.cpp event whose data does not parse is relayed as received, so
    a non-finite number inside a relayed event reaches the client byte for byte
    (KD-10; "Passthrough trusts the backend's bytes").
21. **The access line masks what the router does not route** (V21, I1).
    `_RouterHandler.log_message` logs the path only when it is one of
    `_ENDPOINTS`, else `<unrouted>`, and a method without a `do_*` handler as
    `<other>`: both are the client's text, written before authentication, and
    a misplaced token in a path reached the DEBUG log. The shape copied from
    mcp-proxy (P9) is otherwise kept.
22. **The SSE line split matches a client's parser** (V30, D14).
    `Scripts/llm-router.py:_rt_sse_split` ends a line at CRLF, LF **or a bare
    CR**, as WHATWG EventSource does, and the relays drop a stream-leading
    UTF-8 BOM. Before, a bare CR could hide an `event: error` or a
    `message_stop` from the router that the client would still see. The dropped
    BOM is the one byte change to passthrough's byte-exact relay (KD-10); a
    comment that a bare CR used to hide is now seen and dropped, like every
    comment (deviation 4). The mistral translator relays no upstream bytes and
    is not affected.
23. **llama.cpp: an `{"error": ...}` event is an error whatever its name**
    (V16, E8). `LlamacppRelay._feed_line` sends any event whose data is an
    object with an `error` key through the `error-shape` row, the scrub and the
    error path, not only one named `error`. Only llamacpp: passthrough's
    `EventRelay` still treats only `event: error` as an error (passthrough
    trusts the backend's bytes).
24. **Mistral stream tool arguments are checked strictly** (F1 = V31, G12,
    G14). `MistralStreamTranslator.finish` parses each tool's joined arguments
    with `_rt_loads`, requires a JSON object, and re-serialises it with
    `_rt_dumps`, so the client gets exactly what was checked; anything else is
    `event: error` "backend returned malformed tool arguments". `_buffer_tool`
    refuses an `arguments` that is neither a string nor an object (a list is
    not an object). G14 holds the non-stream path, `_rt_ms_tool_use`, to the
    same rule.
25. **Mistral `arguments` may be an object as well as a string** (F2; an
    earlier deviation, recorded here next to the round-1 rule it meets). The
    plan treats arguments as JSON-string fragments that are appended, and says
    nothing of an object. A backend that sends the object is
    accepted on both paths: in the stream `_buffer_tool` serialises it and
    buffers it like a string fragment, and the non-stream `_rt_ms_tool_use`
    takes it as the input. A list or any other type is refused (deviation 24).
    The client never receives the upstream's argument bytes: the stream emits
    the re-serialised form, compact and ASCII-escaped (`_rt_dumps`), and the
    non-stream answer is serialised the same way by `_send_json`.
26. **The Mistral tool buffer counts ids and names, and at most 128 tools**
    (V33, G12). Each tool's stored id and name count against
    `_RT_BUFFERED_TOOL_LIMIT` with its arguments, and a stream may buffer at
    most `_RT_BUFFERED_TOOL_COUNT` (128) tool calls; an overrun is the same
    `event: error` at the crossing chunk as an argument overrun (KD-5).
27. **Resolution runs inside the connect deadline** (V9, C9).
    `Scripts/llm-router.py:_rt_resolve`, which now takes the deadline, runs the resolver
    (`_rt_getaddrinfo`) on a daemon thread, `rt-resolve`, joined with what
    remains of the deadline that `_rt_open_socket` and the TLS handshake then
    share, so resolve, connect and TLS together take at most `connect_timeout`.
    A resolver that has not answered is the 504 "backend timed out: ... did not
    resolve before the connect deadline", and its thread is abandoned (see
    "Declared limits"). Before, `getaddrinfo` ran before the deadline started
    and had no bound but the OS resolver's own.
28. **`allow_private` refuses three networks inside private space** (V10, C12,
    C13). `_RT_PRIVATE_REFUSED_NETS` holds the metadata addresses that are not
    link-local — `fd00:ec2::254/128` (AWS, a ULA) and `100.100.100.200/32`
    (Alibaba Cloud, in CGNAT) — and Teredo `2001::/32`, which tunnels to an
    embedded IPv4 like the translation prefixes. `_rt_private_policy_refused`
    checks them second, after the translation prefixes and before loopback;
    the IPv4 one is refused in its IPv4-mapped form too. The "classification
    order" limit below predates this and lists the order without them.
29. **The ready file is removed on a clean shutdown** (V24, B15).
    `Scripts/llm-router.py:_rt_remove_ready_file` runs in `_rt_shutdown` after
    the listener closes; it reads the file without following a symlink and
    unlinks it only while it still holds this process's pid, so a file another
    writer has replaced is left alone. Best effort, never raises; its limits
    are declared below.

## Declared limits

Accepted and declared, not gated. The first four are the plan's own; the rest
are §5 "Known security limitations" of the plan, carried verbatim.

- **The Mistral token count is an estimate** (`bytes // count_divisor` of the
  translated request), not Tekken or tiktoken; exact counting needs the `regex`
  module and a slow pure-Python BPE (D4).
- **No live tool-argument streaming** for Mistral: tool calls appear whole at
  the end of the turn (KD-5).
- **Byte equality is not promised for passthrough request bodies:** the body is
  re-serialized after the model rewrite, so it is equal after parsing, not byte
  for byte (D1). Response events are byte-exact.
- **No metrics endpoint;** the DEBUG `req` line is the only per-request record.

The implementation adds one:

- **The llama.cpp quirk references are unverified.** The issue numbers in
  `LLAMACPP_REQUEST_QUIRKS` / `LLAMACPP_EVENT_QUIRKS` (#29482, #27367, #27893,
  #22960) come from the feature brief and were not re-checked; the module
  docstring says so.

From §5 "Known security limitations", verbatim:

- **Host is unchecked under `--allow-remote`** (LOW). The Host check (P12) runs on a loopback bind only, as in
  mcp-proxy. A remote bind is meant for a VPN interface, where DNS rebinding through a local browser is not the threat;
  the bearer token is the boundary there. Checking Host against a configured name was not requested and is not added.
- **Passthrough trusts the backend's bytes** (INFO). inferNO's events are relayed byte for byte (KD-10); only event
  framing, the line/event caps and `message_stop` are checked. A hostile passthrough backend can say anything to Claude
  Code within those bounds. The user owns that backend.
- **`create_default_context()` honours `SSL_CERT_FILE` / `SSL_CERT_DIR`** (INFO). When no `ca_file` is set, OpenSSL's
  default verify paths read these two variables. This is the one environment influence on the router (J2 forbids
  every explicit read); a backend that sets `ca_file` is immune.
- **Pre-header and pre-first-event idle wait** (INFO). Under KD-15 nothing is sent to the client until the upstream
  status is known, so Claude Code sees no byte for up to `connect_timeout` + `idle_timeout` while a backend processes a
  long prompt. On the relay kinds (passthrough, llamacpp) pings are also held until the upstream's first event, so the
  same window extends to the first upstream event. The mistral kind emits `message_start` on the upstream 2xx and is
  pinged from then on.
- **The reserved `anthropic` kind is a seam only** (INFO). The seam (enum value, refusal, header table row,
  `ROUTE_OPTIONS`) is judged adequate; it carries no behaviour to review.
- **The KD-9 scrubber matches fixed encodings** (LOW). It catches the value, its URL-encoded and base64 forms and its
  first/last 8 characters. A secret base64-encoded at a non-zero offset inside a larger string, or split across
  lines, is not caught. The scrubbed text goes to the client only and is never logged.
- **J5 sweeps by pattern** (LOW). The committed suite cannot know the user's real key (see Out of Scope); the
  exact-value sweep runs once in Step 1.
- **Lone surrogates** (LOW, handled). JSON the router serializes for the wire uses `ensure_ascii=True`, so a lone
  surrogate parsed from a client or upstream body is re-emitted as a `\udXXX` escape rather than raising
  `UnicodeEncodeError` mid-stream (case J21). Passthrough events are relayed as received.
- **Cleartext bearer under `--allow-remote`** (LOW, A02 / CWE-319). On a remote bind the router token travels in
  plain HTTP; inbound HTTPS is out of scope (§1). The VPN is the confidentiality boundary.
- **No throttling of failed auth** (LOW, A07 / CWE-307). Repeated 401s are not rate-limited. Accepted because the token
  is ≥ 32 printable ASCII characters (A-9: brute force is an entropy problem, not a rate problem) and the listener is
  loopback or VPN only.
- **Authenticated worst-case memory** (LOW, A04 / CWE-770). One request can hold its body (`--body-limit`), its
  translated copy and a full pump queue (`_RT_QUEUE_BYTES`, 16 MiB); at the defaults that is roughly
  `max_connections × (32 MiB + ~32 MiB + 16 MiB)` ≈ 2-3 GiB. Only a caller holding the bearer token can reach it; lower
  `--max-connections` or `--body-limit` to shrink it.
- **Upstream JSON nesting** (INFO, A08). A deeply nested Mistral SSE `data:` chunk or non-stream body raises
  `RecursionError` in `json.loads`; the top-level handler (P7 shape, Step 7) catches it and the client gets
  `event: error` or a 502 envelope, with the type name logged and no traceback.
- **The shutdown drain does not cover JSON relays** (INFO). Only streaming upstream sockets are registered in
  `srv.inflight`; a non-stream `/v1/messages` or a forwarded count_tokens call in flight at SIGTERM keeps blocking for
  up to `idle_timeout` and is abandoned (daemon thread) when the 3 s drain ends.
- **`active` misses an accepted-but-not-started connection** (INFO). `srv.active` is incremented inside
  `process_request_thread`, so a connection accepted but whose thread has not yet run is uncounted by the drain. It is
  harmless: such a handler sees `srv.closing` and answers 529, or dies with the process.
- **Private-policy classification order is load-bearing** (INFO). The C12/C13 verdicts were checked against Python
  3.9's `ipaddress`: `0.0.0.0` is `is_unspecified`, `240.0.0.1` is `is_reserved`, `fd00::1` passes as private (ULA).
  Keep `_rt_private_policy_refused`'s order exactly as Step 6 writes it (translation prefixes, loopback, link-local,
  multicast, unspecified, reserved), so each address is refused for the class C12/C13 assert.

In the verbatim items, "Step 1", "Step 6", "Step 7", "§1" and "Out of Scope"
refer to the feature plan; the exact-value sweep of "Step 1" is M5's and has
not run (see "M1-M5: not measured").

Validation round 1 adds five, and the first replaces the memory formula of the
verbatim "Authenticated worst-case memory" item above:

- **Worst-case memory, with the upstream body** (LOW, CWE-770, V13). The
  verbatim formula counts the inbound body, its translated copy and the pump
  queue, but not the non-stream upstream answer, which `_relay_json` buffers
  whole up to `_UPSTREAM_BODY_LIMIT` (64 MiB) and the adapter then decodes and
  parses. A request streams or it does not, so per connection the bound is
  roughly `--body-limit` + its translated copy + the larger of the pump queue
  (`_RT_QUEUE_BYTES`, 16 MiB) and the upstream body plus its decoded text
  (2 × 64 MiB), plus the parsed objects of each body, which Python holds at
  several times the byte size (not measured). At the defaults the bytes alone
  are about `32 × (32 + ~32 + 128) MiB` ≈ 6 GiB before that expansion. Only a
  bearer holder can start the requests, but the upstream sets the size of its
  answers; lower `--max-connections` or `--body-limit` to shrink the bound.
- **The pre-auth header bound is a mitigation** (LOW, CWE-770, V3). A
  connection that has never authenticated holds its slot for at most
  `_HTTP_PREAUTH_TIMEOUT_S` (5 s) of header phase (deviation 16), but a peer
  that reconnects as soon as it is cut can still keep every
  `--max-connections` slot busy, at the cost of one reconnect per slot every
  5 s. There is no separate pre-auth slot budget.
- **An abandoned resolver thread lives until the OS resolver gives up** (INFO,
  V9). A resolver that misses the connect deadline (deviation 27) is answered
  504 and its `rt-resolve` daemon thread is left running, not killed — Python
  cannot interrupt `getaddrinfo`. Under a stalled resolver, abandoned threads
  pile up, at most one per timed-out request, each for at most one OS resolver
  timeout. Only a bearer holder's requests reach the resolver.
- **The ready-file removal is best effort** (INFO, V24). Between reading the
  pid and the unlink another writer can replace the file, and the router would
  then remove that writer's file (a TOCTOU window of one read). Nothing is
  removed on SIGKILL or a crash, since no handler runs; a supervisor must still
  treat a stale file's pid as possibly dead.
- **Failed authentication grows the log** (LOW, CWE-779, V26). Each 401 or 403
  writes one WARNING line (status and peer address only) at the default log
  level, and a refusal closes the connection, so log volume grows with an
  unauthenticated peer's connection rate, bounded only by
  `--max-connections` and the reconnect rate. There is no rate limit and no
  sampling, as in mcp-proxy (`Scripts/mcp-proxy.py` module docstring).

## Out of scope, deliberately

The `anthropic` kind (above); exact Tekken counting; `GET /v1/models`, Batches,
Files and every other Anthropic endpoint; retries, load balancing, fail-over
and response caching; HTTPS on the inbound side; modifying
`Scripts/mcp-proxy.py`; and lifting the HTTP front into a canonical source,
which is a roadmap item only.

Four validation-round-1 findings are real but their fix does not belong in the
router: two live in a canonical source the router only copies or generates
from, and two are the same defect in mcp-proxy, which this change does not
modify. Each is a roadmap item or candidate
([[0022-a-someday-maybe-is-a-roadmap-item]]): V11 is R-0074, V22 is R-0063,
and the two mcp-proxy counterparts are R-0075 and R-0076:

- **V11 — `192.0.0.0/24` under the public policy** (LOW, CWE-918). On Python
  3.9 `ipaddress` classifies only parts of `192.0.0.0/24` as non-global, so an
  address such as `192.0.0.192` passes `_ch_address_refused`. That function is
  a verbatim hand copy of `Scripts/_mcp_chrome.py`'s, which J20 holds equal to
  its source, so the refusal (the range, except the `.9` and `.10` anycast
  addresses) goes into the canonical source first and the copy is resynced
  after.
- **V22 — `--log-file` follows a symlink** (LOW, CWE-59). The log file is
  opened without `O_NOFOLLOW`, and `fchmod` acts on whatever it opened. The
  code is the generated `_mcp_logging.py :: _configure_logging` region, shared
  with every other host of that block, so the fix is in
  `Scripts/_mcp_logging.py` and a regeneration, fleet-wide. Filed as R-0063
  (from mcp-proxy's review, finding F33).
- **mcp-proxy's ready file is not removed on shutdown** — the counterpart of
  V24 and deviation 29.
- **mcp-proxy sends `100 Continue` before authentication** — the counterpart
  of V37 and deviation 18: it does not override `handle_expect_100`.

## Acceptance

Not run yet. The manual acceptance against real backends — for each kind, a
real Claude Code session that reads a file, runs `ls` and edits a scratch file,
then a resumed turn that resends the tool history (the live proof of KD-6 on
Mistral), with the log checked for keys and Ctrl-C checked for orphaned upstream
connections — is user-gated, and its outcome will be recorded here.

## Addendum (2026-10-06): GET /v1/models after all: the route keys as the Anthropic model list

The plan left `/v1/models` out of scope. On 2026-10-06 the operator asked for it, and the router now answers `GET /v1/models` and `GET /v1/models/{id}` from the config alone -- no body is read and no backend is ever contacted.

**Shape.** `GET /v1/models` is 200 with the Anthropic list shape: `data`, `has_more`, `first_id`, `last_id`. `data` holds one `{"type": "model", "id", "display_name", "created_at"}` per route key, in config order. The `default` route has no name and is never listed. With no routes, `data` is `[]` and `first_id` / `last_id` are null (`Scripts/llm-router.py:_rt_models_list`). `GET /v1/models/{id}` is 200 with the single entry when the percent-decoded segment (UTF-8, strict, exactly one segment) equals a route key. Otherwise it is a 404 `not_found_error` that never echoes the id (`Scripts/llm-router.py:_get_models`).

**Front rules.** Both paths pass the POST precheck unchanged: header defects, Host, Origin, the constant-time bearer, the misplaced token and the pre-auth deadline. The query policy differs by path. On the models paths, `limit`, `before_id`, `after_id` and `beta` are accepted with any value; any other key is a 400 (`Scripts/llm-router.py:_RT_MODELS_QUERY_KEYS`). The messages paths keep `_RT_ALLOWED_QUERIES` and its 404. A GET with `Content-Length` above 0 is a 400, `Transfer-Encoding` is the POST framing 411, and a two-word HTTP/0.9 GET is a 400. These are the rules that matter for the models paths. POST on a models path, and HEAD or OPTIONS on one, is a 405 with `Allow: GET`. GET on a messages path stays a 405 with `Allow: POST`. The access line logs `/v1/models/{id}` as the literal `/v1/models/<id>`. As before, only a POST gets the `req` summary line.

**display_name.** This is a new optional route key: a string of 1-256 characters (`_RT_DISPLAY_NAME_LIMIT`) with no control character. It is validated in `_rt_cfg_route`, and a refusal names the key, never the value. It is allowed on `default`, where it is unused. When it is absent, the list shows the route key.

**Declared limit.** There is no pagination. The pagination keys are ignored, the whole list is always returned with `has_more` false, and every `created_at` is the router's start time (UTC, second precision) rather than a per-model date.

**Cases.** The `llm_router` suite goes from 174 to 180 cases. A27 covers the display_name refusals and defaults. B25-B29 cover the list shape and order, the single-entry hit and the non-echoing 404, auth, Host and Origin, the query keys and the method matrix, and the empty route table, each asserting that the scripted peer was never contacted. All six ran red first, before the router changed, and then green.

## Addendum (2026-10-07): The codex and openai kinds: Responses-API backends behind an OAuth source, a security-review round, and Mistral reasoning

On 2026-10-06 and 2026-10-07 the router gained two backend kinds that speak the OpenAI Responses API -- `codex` (the ChatGPT-subscription endpoint, OAuth only) and `openai` (the platform endpoint, with an API key or a Sign-in-with-ChatGPT (SIWC) OAuth login) -- plus a `login` subcommand, a token store that writes rotated refresh tokens back into the config, and a twelfth canonical source, `Scripts/_mcp_oauth.py`, generated into the router at the head of unit 6. Why the OAuth protocol is a canonical source of its own, and why it is generated rather than imported, belongs to [[0014-a-canonical-source-is-a-domain]] and [[0025-generate-do-not-import]]. The plan was `docs/feature-implementation-plan.md` (validated in four rounds before any code) and its task graph `requirements.yaml`. The red-first record and every live result were kept in a working file, `.claude/tmp/oauth-measurements.md`, which is not committed. As in the record above, every number here is a count or a measurement at the moment it was taken, every `path:line` is the working tree's on 2026-10-07, and nothing was committed when this addendum was written. The live values are in the code and in `tests/run.py:SUITES`.

**Finding ids.** Plan-validation finding ids repeat across rounds, so every one of them carries its round prefix (`R1-` to `R4-`). The code-mode security review of 2026-10-07 numbers its findings `F1` to `F53`; they are not the `F<n>` of the round-1 review above. Test case ids (`A28`, `N22`, mcp_oauth `H13`, ...) and this feature's measurement ids `M1` / `M2` are never prefixed. This `M1` / `M2` are the plan's Step 1 desk measurements, not the M1-M5 of the section above.

### The two kinds, the profile table and KD-1 to KD-20

`_KINDS` now holds passthrough, llamacpp, mistral, codex, openai and the reserved anthropic `Scripts/llm-router.py:502`. Both new kinds are one class, `ResponsesAdapter`; `CodexAdapter` and `OpenaiAdapter` set only `kind` and `ROUTE_OPTIONS` `Scripts/llm-router.py:4550-4572`. Every wire difference is one row of `_rt_responses_profiles` `Scripts/llm-router.py:3653-3675`, and the kind and auth mode pick the row through `_RT_KIND_AUTH_PROFILES` `Scripts/llm-router.py:871-883`:

| Profile | Kind, auth | Base URL, path | Sampling | Account header, omp identity | Event-stream Content-Type required | Tool shape |
|---|---|---|---|---|---|---|
| `codex` | codex, oauth | `https://chatgpt.com/backend-api`, `/codex/responses` | no | yes, yes (plus `OpenAI-Beta: responses=experimental`) | no (measured at checkpoint B) | function |
| `openai-oauth` | openai, oauth | `https://api.openai.com`, `/v1/responses` | no | no, no | no | namespace (user decision 2026-10-07) |
| `openai-apikey` | openai, api_key | `https://api.openai.com`, `/v1/responses` | yes | no, no | yes | function |

The decisions, as they were implemented:

- **KD-1 Profile rows over kind branches.** `ResponsesAdapter` reads `inbound.backend.profile`, resolved once at load, and holds no kind branch. `_rt_check_responses_profiles` refuses a bad row at import with a `RuntimeError` naming it, before the bind.
- **KD-2 The omp identity is one row.** `_RT_CODEX_IDENTITY` is `originator: omp`, `user-agent: omp/18.6.1`, `version: 0.159.0` `Scripts/llm-router.py:3606-3614`. A backend's `identity` object may override exactly these keys, each value 1-256 printable ASCII characters; the codex provider row's authorize `originator` reads the same row, so the two cannot drift.
- **KD-3 Two optional Adapter hooks.** `upstream_head(inbound, cred)` and `stream_policy(inbound)` default to the old behaviour, so the front never asks whether a kind is a Responses kind.
- **KD-4 One translator, two sinks.** `ResponsesStreamTranslator` writes to `AnthropicSseEncoder` when the client streams and to `_RtMessageCollector` when it does not; J26 holds the two sinks equal for one call sequence.
- **KD-5 The OAuth source gets its transport and clock injected.** The router binds `_rt_oauth_post` to the backend and passes `time.time` / `time.monotonic`; the source names no host function.
- **KD-6 The reasoning signature is plain and bound to the backend.** `lrs1.` + backend name + `.` + `encrypted_content`, with no HMAC (orchestrator decision: the content is opaque and validated upstream, and the name binding already stops a cross-backend replay). On the way back a missing prefix, another backend's name, a bad charset, a part over `_RT_RS_SIGNATURE_LIMIT` (1 MiB) or a reasoning item beyond the 256th (`_RT_RS_REASONING_CAP`) is dropped and counted in `dropped_thinking`; the summary is not replayed. Implementation decision (task-046): the 1 MiB limit applies to the `encrypted_content` part only, on mint and on unsign, so a signature minted from exactly 1 MiB round-trips (case M5) and 1 MiB + 1 is dropped (cases K18 and M5).
- **KD-7 J1 and J4a are amended minimally** (section below).
- **KD-8 The generated region sits at the head of unit 6**, before the provider rows, `_rt_oauth_*` and `_RtTokenStore`, which instantiate `OAuthProvider` at import.
- **KD-9 The write-back is unit-2 code; the decision and the locks are unit 6.** Unit 2 now reads, and on login or refresh rewrites, one file, and still opens no socket.
- **KD-10 The rewrite algorithm.** Directory check first, then the flock, the stale-temp sweep, a re-read relative to the checked directory descriptor, a duplicate-key-refusing parse, the merge of the one sub-object `backends.<n>.oauth` (`_RT_OAUTH_PERSIST_KEYS`: `refresh_token`, `account_id`, `expires_at`, `client_id`, `host_id`; never an access or ID token), a serialization that must round-trip and leave every value outside that object equal, a 0600 `O_EXCL` temp file, a hash re-check (at most `_RT_PERSIST_ATTEMPTS` = 3 tries), `os.rename` relative to the descriptor and an `fsync` of the directory `Scripts/llm-router.py:_rt_config_update_oauth`. Amended by F40 (below).
- **KD-11 Lock order, bounded wait, stale-aware refresh.** Backend lock, then `write_lock`, then the flock. The backend lock is taken in 0.25 s slices (`_RT_OAUTH_LOCK_SLICE_S`) up to `connect_timeout` + `_RT_OAUTH_IDLE_S` (15 s), then 503; the server closing during the wait is 529. A non-relogin failure is shared for `_RT_OAUTH_FAIL_CACHE_S` = 10 s; a relogin failure is never cached (R2-L2). A forced refresh after a 401 runs only if the current access token is still the rejected one.
- **KD-12 One header-value validator** `Scripts/llm-router.py:_rt_header_value_ok`, rules `token`, `claim` and `identity` `Scripts/llm-router.py:888-892`, for every value that can reach a header: config seeds, identity overrides, adopted disk tokens, every token of a token response and the JWT account and residency claims.
- **KD-13 The scrubber gains `add()` and `pin()`** and never mutates a container in place; its production writers are serialized by `_RT_SCRUB_LOCK` through `_rt_scrub_add` / `_rt_scrub_pin`; at most 64 unpinned dynamic values (`_RT_SCRUB_DYNAMIC_CAP`); a JWT-shaped secret gets no 8-character prefix form.
- **KD-14 Effort bands.** The route's `reasoning_effort` wins; else `budget_tokens` up to 4096 is `low`, up to 16384 `medium`, up to 32768 `high`, above it `xhigh`, `adaptive` is `medium`, and absent or disabled thinking omits `reasoning.effort` `Scripts/llm-router.py:3619-3623`.
- **KD-15 Static OAuth endpoints, no runtime discovery,** and `login` dispatched at the top of `main()` before the server's parser, so the server command line is unchanged `Scripts/llm-router.py:main`.
- **KD-16 The deviation from KD-8** (section below).
- **KD-17 Callback listener hardening.** Loopback only; codex binds `127.0.0.1:1455` (mandatory) and `[::1]:1455` (best effort); `SO_REUSEADDR` on POSIX, never `SO_REUSEPORT`; Host and constant-time state checks; a callback `error=` honoured only with a matching state; a 5 s head deadline, an 8 KiB head cap, a budget of 16 bad requests, single use and fixed pages that reflect no input.
- **KD-18 The H group covers `openai` with an API key only;** codex's deadline path is the same adapter and relay loop, and its OAuth stall is N13's.
- **KD-19 The auth spec does not inherit the backend's trust.** Without `auth_base_url` the token endpoint gets the public-only address policy and the system trust store, only `connect_timeout` inherited; with it the backend's policy is inherited, and `auth_base_url` must be https except an http loopback host under `allow_private` and `allow_loopback`.
- **KD-20 The cross-process config lock.** `<config>.lock`, opened relative to the checked directory, flock polled every 0.05 s for at most `_RT_CONFIG_LOCK_WAIT_S` = 5 s, never deleted. The startup sweep and its lock file happen only when some backend's profile logs in with OAuth (R2-L5). Non-POSIX is unsupported.

### The plan's four validation rounds (2026-10-06)

- **Round 1.** Correctness REVISE: R1-H1 to R1-H5 (groups K-O must run before the I and J sweeps; the tab-host fixture lacked its `secrets` / `select` imports; port 1455 in TIME_WAIT after a login; the forge timeout; an unbounded refresh-lock queue), R1-M1 to R1-M8 and R1-L1 to R1-L8. Security, plan mode, REVISE with no HIGH and five MEDIUM -- R1-S1 a cross-process lost update, R1-S2 a lock held across the token POST, R1-S3 directory permissions, R1-S4 the scrubber evicting live secrets, R1-S5 header validation on every path -- plus R1-S6 to R1-S11 and R1-I1 to R1-I3. One refinement applied all of them: the cross-process lock (KD-20), the bounded refresh lock with a shared failure (KD-11), the one header-value validator (KD-12), the pinned scrubber (KD-13) and the POSIX listener with `SO_REUSEADDR` (KD-17). Totals then: 318 `llm_router` and 61 `mcp_oauth` cases, 25 declared limits.
- **Round 2.** Correctness APPROVE with R2-M1, R2-M2 and R2-L1 to R2-L7; security R2-S3/S6-gap (the directory check must run first, and every later file operation must be relative to its descriptor) and R2-NEW-1 (a scrubber add/pin race, CWE-362). R2-M1 dropped the callback linger: the plan meant a half-close and linger to move the TIME_WAIT onto the browser's side, but a `shutdown(SHUT_WR)` sends the listener's FIN first, so the listener stays the active closer and the linger changed nothing; the listener now just closes, and `SO_REUSEADDR` is what lets the next login bind. R2-M2 added Step 7 as a dependency of Steps 9 and 10. R2-L2 drops a dead refresh token instead of caching its failure; R2-L5 made the startup sweep conditional and mapped a lock-open `OSError` (L19). I16, L19 and L20 were added: 318 to 321 cases, 26 declared limits.
- **Round 3.** Correctness REVISE: R3-C1 (critical) -- the guard required `os.replace in os.supports_dir_fd`, which is false on every measured interpreter, so no token would ever have been persisted; R3-H1 to R3-H4 (`os.replace` with directory descriptors), R3-M1 (the scrubber read order that makes I16 red first), R3-M2 (the guard asserted by name in L20), R3-L1 to R3-L6 (among them R3-L3, the round-prefix convention used here; R3-L4, a matching-state `error=` ends the wait at the acceptor, mcp_oauth H14; R3-L5, `os.stat in os.supports_follow_symlinks` joins the guard; R3-L6, what H13 does and does not prove). Security NO_THREAT_SURFACE: the guard fails closed and has no path-based fallback. Measured on 2026-10-06, darwin, on `/usr/local/bin/python3.9` 3.9.21 and on 3.14.0: `os.supports_dir_fd` is {access, chmod, chown, link, mkdir, mkfifo, mknod, open, readlink, rename, rmdir, stat, symlink, unlink, utime} -- `rename`, not `replace` -- and `os.listdir in os.supports_fd` holds. The writer therefore renames with `os.rename(tmp, base, src_dir_fd=dfd, dst_dir_fd=dfd)`, a `renameat` that replaces the destination atomically on POSIX. `mcp_oauth` went from 61 to 62 cases.
- **Round 4.** Correctness APPROVE (R4-M1 `_oauth_callback_page(ok, status)` with an allow-listed status and a 200 carrying the failure body for a matching-state denial; R4-L1 a paste-thread denial sub-assertion in O9; R4-L2 a head-of-group guard check in `group_l`; R4-I1 to R4-I3), security NO_THREAT_SURFACE. Applied after approval with no count change.

### J1 and J4a, amended (KD-7)

J4a now requires exactly two `_rt_send` call sites -- `_post` with `_rt_upstream_headers(...)` and `_rt_oauth_post` with `_rt_oauth_headers(...)` -- and J1 exactly two `compare_digest` sites, `_precheck` and the generated `_oauth_state_matches`. New planted controls prove each amended rule still fires on a third site, a swapped header builder and a third digest: J23 for J4a, and J15 with a three-call needle. Red first (task-025): 2 FAIL -- J1 found 1 `compare_digest` call where it expects 2, and J4 found 1 `_rt_send` site where it expects 2. J23 was green on its first run with both needles firing; it is a control, not a red-first case. The later send loop in `_post` (below) kept the one call site.

### The KD-16 deviation from KD-8

For the two OAuth profiles an upstream 401 forces one refresh (stale = the rejected access token) and one resend. A 401 after that is answered 401 `authentication_error` -- `backend <n>: the login was rejected after a refresh; run llm-router.py login --backend <n>` -- rather than KD-8's 502, because only the client can fix it, with a fresh login `Scripts/llm-router.py:ResponsesAdapter` (its `json_response`). A relogin-class refresh failure is a 401 with the same command (`the login expired or was revoked`). `openai-apikey` keeps KD-8: its 401 and 403 are 502. Only allow-listed OAuth `error` codes are ever logged or surfaced, never `error_description` (S8). The DEBUG `req` line carries `refreshed=` only for this forced refresh; the proactive refresh before expiry leaves no trace on it (seen at checkpoint B).

### Responses streams: always a stream upstream, and the per-kind limits

The Responses kinds always ask for a stream: the body carries `stream: true` and `store: false` and the request `Accept: text/event-stream`, whatever the client asked for. N29 measured the request line, headers (order included) and body bytes equal in both client modes. A non-stream client request is answered by running the same translator into `_RtMessageCollector` over the whole SSE body, read under `_UPSTREAM_BODY_LIMIT` `Scripts/llm-router.py:ResponsesAdapter` (its `json_response`); a failed stream maps to its code's status, else 502. One Responses SSE line may be 8 MiB (`_RT_RS_SSE_LINE_LIMIT`, the `StreamPolicy.line_limit` the front's pump reads) and one event 16 MiB (`_RT_RS_SSE_EVENT_LIMIT`, read only by the translator) `Scripts/llm-router.py:3616-3617`, against the 1 MiB and 2 MiB of the other kinds. The event-stream `Content-Type` is required on `openai-apikey` only.

### Token persistence and the merge rules

- The persisted keys are `_RT_OAUTH_PERSIST_KEYS`; an access or ID token is never written (J29), so every start refreshes once, and a rotating provider costs one rewrite per start.
- **Merge, never clobber.** The flock serializes the cooperating writers (the router and `login`); the hash re-check exists for a non-cooperating one (L13), and a changed digest is logged at INFO and merged.
- **Adoption.** After a relogin-class refresh error, a different refresh token on disk -- another writer rotated it, or `login` ran -- is adopted once, if it passes the `token` rule, pinned in the scrubber, and refreshed. With no refresh token in memory, `credential()` reads the disk seed the same way (M3). After an unresolved relogin failure the dead token is dropped and remembered as `dead_refresh`, never re-adopted, so a fresh `login` is picked up on the next request (R2-L2).
- **KD-10 amended by F40.** KD-10 said our successful rotation is the newest and is always written. It is not when a `login` lands on disk while a refresh of the old grant is in flight: the router's next write would have replaced the new grant with the old identity's rotation. Under the flock `_rt_config_update_oauth` now receives the refresh token the refresh started from (`started_from`) and the dead one; a disk `refresh_token` that is none of those and not the token being written raises `ConfigConflict`, and nothing is written `Scripts/llm-router.py:1478-1504`. `_RtTokenStore._persist_rotation` then adopts the disk login and refreshes that one -- at most one adoption per `credential()` call; when no adoption is possible the rotation is kept in memory with one WARNING `Scripts/llm-router.py:5904-5920`. `login` passes no `started_from` and always writes. Case N33.
- **F38: a refresh answer that fails a post-parse check discards its rotated refresh token.** When a token of the answer fails the `token` rule, or the openai ID-token and scope check fails (`_rt_oauth_check_answer`), the refresh is `invalid_response` (502) and none of its tokens is kept, so the next refresh resends the old refresh token `Scripts/llm-router.py:_RtTokenStore` (its `_refresh`). The security review judged discarding the safer choice, which closes the task-050 open thread; it sits next to declared limit (17), because on a rotating provider the old token can then be refused as reused and need a re-login. The codex account-claim path differs on purpose: with neither a valid `chatgpt_account_id` claim nor a seed `account_id`, the rotated refresh token is persisted and kept and only the access token is refused, 502 `invalid_response`.
- **F2: a refresh floor.** `earliest_refresh_at` is now the answer time plus `OAUTH_MIN_REFRESH_INTERVAL_S` (60 s) `Scripts/_mcp_oauth.py:148`, so an `expires_in` of 0 can no longer make every request refresh (mcp_oauth D10). Honoured in-process only (declared limit 15).

### Red first: what each new case group measured

Every count below is a `forge_call test llm_router` run before the code it tests, from the working record. Each run also printed the case-count drift against the declared 180 until task-052 moved `tests/run.py`.

| Task | New cases | FAIL | What the red run showed |
|---|---|---|---|
| task-012 | I8-I12 | 5 | the scrubber had no `add()` / `pin()`; I12 also caught today's static JWT prefix form redacting an unrelated `eyJhbGci` text |
| task-018 | A28-A41 | 14 | every loader half refused by the old schema (`oauth`, `identity`, `auth_base_url` unknown keys, `openai` an unknown kind); still 14 after the types landed, for the next missing piece |
| task-021 | L1-L15 | 15 | one missing-symbol problem per case (`_rt_config_dir_check`, `_rt_config_update_oauth`, ...) |
| task-022 | L16-L20 and the head-of-group guard | 20 (L1-L20) | the guard held; L20's named guard line read all six `dir_fd` memberships hold on 3.14.0 |
| task-025 | J1, J4 amended; J23 | 2 | above |
| task-028 | K1-K13, K20 | 14 | no `_rt_rs_translate` |
| task-031 | M1, M6-M20 | 16 | no `ResponsesStreamTranslator`; M20 no `json_response` |
| task-034 | I16 and eight `TF_*` OAuth sentinels | 1 | missing `_RT_SCRUB_LOCK` / `_rt_scrub_pin` |
| task-037 | I16, unlocked stage (R3-M1) | 1 | with the lock defined but not taken: B's pin returned while A was inside its own, and one pin lost the other backend's set -- the stale-snapshot rebind measured, not just a missing name |
| task-035 | N1-N7, N9-N13 | 12 | every codex request answered 501 (no adapter, no token store) |
| task-036 | N14-N17 codex half, N20-N28 | 12 of 13 | N27 green (below) |
| task-041 | O1, O4-O9, O11-O16 | 13 | argparse refused `login` |
| task-044 | K14-K19, M2-M5, J24-J26 | 13 | no `_rt_rs_effort` / `_rt_rs_unsign`; every reasoning item dropped; no encoder `thinking` |
| task-047 | the six `oai:` H cases, N19 | 7 | 501, the openai adapter not wired |
| task-049 | J27, K23, K24, N18, O2, O3, O10 | 7 | J27 flipped on purpose by the namespace decision; N18 let a wrong-audience ID token through |

**Green at red time -- regression pins, not red-first cases:** N27 (the auth spec's trust inheritance had already landed; A41 covers the same rule), N29 and N30 (the reported stream defect did not exist in the code), N8, the openai-oauth half of N17 and M21 (already true when written), K22 (written after its fix), and I13, I14, I15 and J29 (no router change). J28's first run failed on its own anchor -- the close of the rows tuple was found twice -- and went green once the anchor was scoped to `_rt_responses_profiles`, with no router edit; it pins the translator half of SC-4 only.

**A deviation in group L.** When the head-of-group guard misses, L1-L19 record their skip text as INFO detail, not as a problem: the harness counts any record carrying a problem as a failure whatever its status, so a problem would have given 20 failures instead of the plan's one.

**Not recorded.** The `mcp_oauth` suite (62 cases: A 4, B 5, C 5, D 9, E 10, F 7, G 4, H 14, I 4) and its H14 were written red first, but no red count was kept in the working record, so none is quoted. The same holds for the cases of the security fixes and of the two Mistral features below: each was written and run red before its code, and the per-run counts were not kept.

**The totals.** `llm_router` went from 180 to 341 by the end of the plan (A 45, B 31, C 14, D 16, E 14, F 17, G 17, H 27, I 16, J 30, K 25, L 20, M 21, N 32, O 16) -- the plan's 321 plus 20 out-of-plan cases (B30, D15, E14, F17, K21; K22; A42, A43, B31; A44, A45, D16, K25, N31, N32; N29, N30; K23, K24, M21) -- then to 345 with the security fixes (J31-J33, N33), 355 with Mistral thinking and 363 with the Mistral effort map: A 48, B 31, C 14, D 16, E 14, F 25, G 22, H 27, I 16, J 33, K 25, L 20, M 21, N 35, O 16. `mcp_oauth` went from 62 to 64 (D10, D11). `generated_region` stayed at 97. `py_deps` went from 59 to 60, a plan gap: every `NEW_39_SCOPE` entry adds one case, and the plan did not move the declared count. The forge `llm_router` timeout followed the plan's own rule rather than its provisional 540: max(480, ceil(1.5 x 316 / 60) x 60) = 480 from the 316 s measured at 341 cases.

### M1, M2 and the omp gateway (2026-10-06)

- **M1, measured offline** from the installed omp binary (`omp/18.6.1`), pinned on the web to tag v18.6.1 = commit `2a2c6dcbbb558c0f8145f67f28b3370984f2bf60`: `User-Agent: omp/18.6.1` (the template interpolates omp's own version only), `originator: omp`, `version: 0.159.0`. It moves with every omp release.
- **M2, documentation only, never live.** The SIWC preview-limitations page says function and custom tools must be grouped in a namespace or sent as `additional_tools` input items, so bare top-level function tools are probably refused (`subscription_sharing_unsupported_capability`). On that evidence the user chose the namespace shape for `openai-oauth` on 2026-10-07: one namespace tool `claude_code` holding the function entries `Scripts/llm-router.py:3646-3650`; a `tool_choice` for one tool is sent as a plain function choice; a returned call carrying a namespace that is not ours is stripped, not refused. How a namespaced tool is targeted, and whether a replayed call needs its namespace, stay live unknowns for checkpoint E.
- **The omp gateway.** The router's passthrough kind in front of `omp auth-gateway serve` answered every `/v1/messages` case 200, but `count_tokens` was a 404, thinking was dropped silently, cache tokens were always 0, the streamed `input_tokens` was 0, and tool ids looked like `call_...|fc_...`. That is why a native kind won over the gateway; it is kept only as a measurement record.

### Live checkpoints A to E

- **A (2026-10-07, the operator's own config).** Browser login on 1455; the config stayed 0600 and its `oauth` object gained a `refresh_token` (211 characters), an `account_id` (36) and an `expires_at` about ten days out. The access token carried the `chatgpt_account_id` claim.
- **B (2026-10-07, `gpt-6-astra`, router on 127.0.0.1:8082).** A non-stream request with no system prompt answered 200, so codex does not require `instructions`. One plain top-level function tool was accepted. `thinking`, `temperature`, `metadata`, `tool_choice`, `cache_control` and `anthropic-beta` were each harmless. The M1 User-Agent was accepted. A streamed 2xx carries no `Content-Type`, so the codex row's `require_event_stream` went from true to false (N17 and J27 updated; a wrong non-empty type is still a 502). Claude Code streamed text and ran a tool round trip once mid-conversation `system` messages were handled (below). Cache: the same 2.4k-token request twice gave input 2413 / cache_read 0, then input 237 / cache_read 2176, so `prompt_cache_key` works and `input_tokens` excludes cached tokens, as Anthropic counts them. The router was restarted three times and codex worked after each, so the start-up refresh works live. `message_start`'s `input_tokens` is the router's estimate (73) and `message_delta`'s usage the upstream's measurement (8), by design. A router token sent in `x-api-key` as well (Claude Code with `ANTHROPIC_API_KEY` set) is refused 400 by the documented one-header rule. A codex 400 on a tool schema with a regex lookaround led to the schema filter below. At every Claude Code start the access log shows a `HEAD` to an unrouted path answered 404; no `GET /v1/models` has been seen in the router log so far.
- **C (2026-10-07 about 15:00, `gpt-6-astra` and `gpt-5.6-luna`, the operator's live router).** Text, a forced tool, a tool replay, thinking non-stream and its replay, and streamed thinking at budgets 2048, 10000, 20000 and 40000 and adaptive (efforts low, medium, high, xhigh and medium) with a replay -- all 200. The thinking text is the short summary heading (20-30 characters); the encrypted reasoning rides in the `lrs1.codex.` signature (about 1.4-1.6k characters); a `redacted_thinking` block appears when no summary is returned, and it replays fine; luna at low effort sometimes returns no reasoning item. The effort sent is derived from the code, not observed on the wire: the codex `req` line has no effort field.
- **D (2026-10-07, an openai API key, `gpt-5.5`).** Authentication, path and headers were right, but the account has no credits: the billing error was relayed as `api_error`, and a stream was `message_start` plus one error event. The reasoning model refused `temperature`, which led to the sampling rule below. Full success needs credits and is unmeasured.
- **E (a SIWC login of an `openai` backend with an `oauth` object, then text, tool and thinking).** Not run; still open. The namespace shape, the `tool_choice` form, the namespace on replayed calls, the ID token's `iss`, the `chatgpt.tokens.use.direct` scope and the `resource` parameter are verified against documentation and fakes only.

### Changes made during implementation, outside the plan

- **Mid-conversation `system` (user decision; review F16).** Claude Code 2.1.154 and later send `role: system` messages after the first. The validator accepts them (text only); passthrough relays them verbatim; the Responses kinds send a `developer` input item at the same position; llamacpp and mistral fold them into the leading or top-level system. Cases B30, D15, E14, F17, K21, red 5. Live, passthrough relayed a 163 KB Claude Code body with one, and the Anthropic upstream accepted it.
- **Tool-schema regex filter.** `_rt_rs_schema` drops a string `pattern` holding a lookaround or a backreference (`_RT_RS_SCHEMA_REGEX_REFUSED`) at any depth of a Responses tool's parameters, and keeps a property that is merely named `pattern` `Scripts/llm-router.py:_rt_rs_schema`. The user's goal was to keep MCP schemas usable. K22 is its regression case for all three profiles.
- **Advertised route limits (user decision; review F17).** Optional route keys `max_input_tokens` and `max_tokens`, integers from 1 to 100000000, are advertised by `GET /v1/models` with `context_window` = `max_input_tokens` (null when unset) and never enforced. Cases A42, A43, B31, red 3. Whether Claude Code reads them is unmeasured.
- **The sampling rule (user decision after checkpoint D).** Drop what is unsupported automatically, pass what is known to work, and let a config override win. Passthrough gained `temperature` / `top_p` route options; `openai-oauth` refuses them at load; `openai-apikey` learns from a 400 whose message is `Unsupported parameter: '<p>'`, or whose `param` is the name and `code` is `unsupported_parameter` -- a `param` match alone, such as `invalid_value`, is relayed (N32) -- retries once without it, remembers it per (backend, upstream model) for the process lifetime and reports it as `sampling_dropped=` on the `req` line. Cases A44, A45, D16, K25, N31, N32, red 6; A16's unknown-option probe moved to `top_k` / `max_tokens_cap`. `_post`'s send became a `while True` loop of at most three sends -- one 401 refresh resend, one learn retry -- around the one `_rt_send` site.
- **The namespace tool shape** for `openai-oauth` (M2 above; K23, K24, M21, J27).
- **Help epilogs (user request).** `_RT_HELP_EPILOG` and `_RT_LOGIN_HELP_EPILOG` carry config, run, Claude Code environment and curl examples, each example loaded through the real `load_config`.
- **`login` (task-042).** `_RtTokenStore._rt_write` lets a `ConfigError` reach `login`, which exits 2, while `_rt_persist` still swallows it for the server; an `originator` override is carried by a copied provider row, because a duplicate `authorize_extra` key would raise; the paste reader reads descriptor 0 with `os.read`.
- **SIWC host-side additions (task-050).** The exchange's `resource` is appended host-side, outside the generated region (`_rt_oauth_exchange_request`), because the source's `_oauth_exchange_request` sends none; the ID-token issuer under `auth_base_url` is the auth base's origin, through a host-side copy of the provider row. Review F36: drop the raw append when `resource` moves into the canonical source.
- **Two DEBUG lines are kept:** `upstream not an event stream: content_type=...` in `_relay_stream` and `refused messages.N.role=...` in the validator, each value through `_log_value`.
- **Accepted while implementing:** an OAuth profile's 403 with no known error code is a 502 `api_error` carrying the scrubbed message; an unset `auth_base_url` with a provider URL that is not https is a fail-closed 502.
- **Measured where the plan said unmeasured:** `SO_REUSEADDR` is necessary on darwin -- mcp_oauth H13 failed without it in the scratch controls -- and `os.stat in os.supports_follow_symlinks` holds on 3.14.0, darwin (L20's guard line).

### The code-mode security review of 2026-10-07

Scope: `Scripts/_mcp_oauth.py`, `Scripts/llm-router.py` and `Scripts/amalgamate.py`, the uncommitted diff since `76fa06a`. Six lanes found 66 candidates (parseval 16, injection 13, access-control 11, config-deps 10, crypto 9, ssrf-net 7), merged to 53; 5 were VERIFIED, all LOW (F2, F9, F18, F40, F47), 48 SUPPRESSED and none escalated. Verdict APPROVE. The report is `docs/reviews/security-review-20261007-123500.md`; it declares its pipeline deviations: the triage of the previous session was reused, the merge was done by a minion, the verifiers read their own finding from a file in batches of 8, and no numeric CVSS was recorded.

All five were fixed, each red first, and the suites ended green at `llm_router` 345, `mcp_oauth` 64, `generated_region` 97 and `amalgamate.py --check` exit 0:

- **F2** (CWE-1284): the 60 s refresh floor above; mcp_oauth D10.
- **F9** (CWE-1284, CWE-407): an integer literal longer than 4300 characters is refused before `int()` sees it, in `_rt_loads` (`_rt_bounded_int`, `_RT_INT_LITERAL_LIMIT`) `Scripts/llm-router.py:344-349` and in `_oauth_json_object` (`_oauth_bounded_int`, `OAUTH_INT_LITERAL_LIMIT`) `Scripts/_mcp_oauth.py:134`. The macOS system Python 3.9.6 has no `int_max_str_digits`. J31 and mcp_oauth D11; the tests switch off 3.14's own limit so that they measure the module's.
- **F18** (CWE-407): `_RtMessageCollector` keeps a per-block parts list and joins it once (`_flush`) instead of `+=` on a dict item. J32 asserts the source shape, which is what makes it red.
- **F40** (CWE-362): the KD-10 amendment above; N33.
- **F47** (CWE-214): the help epilog no longer puts the router token on the curl argv or into shell history -- `read -rs ANTHROPIC_AUTH_TOKEN` and the header through stdin with `curl -H @-`. J33.

The generated OAuth region was regenerated from the canonical source, and `tests/test_generated_region.py`'s `OAUTH_CORE` mirror gained the three new names. Decisions the review asked to record: F16 and F17 are the two user decisions above; F19 -- the default copy of the Codex/omp client identity is a user decision (KD-2), and its terms-of-service risk is a declared limit (12); F38 is recorded with the merge rules; F36 is the `resource` note above.

**Optional hardenings not done**, roadmap candidates from the review: F6 (count zero-byte callback connections against the budget), F10 (refuse duplicate keys in `_rt_loads`), F12 (below, limit 32), F15 (log only the role's type name in the validator's DEBUG line), F22 (bound the tool-schema recursion and answer 400 rather than a 500), F23 (require `err.param` to match when present before learning), F26 (refuse an upstream tool name that was not offered), F27 (pop the adapter's pending entry in a `finally` on error exits), F30 (fixed text for an OAuth backend's 403), F33 (test the codex login without the `api.connectors.*` scopes), F35 (a scope fallback on refresh; check `azp` when `aud` has several values), F37 (reuse the load-time length and auth-token checks on adoption), F39 (limit 23's wording), F41 (an OS keychain for the refresh token), F42 (`kind` through `_log_value` in one log line), F45 (a smaller cap when reading an upstream 400 body), F51 (refuse a device `user_code` longer than about 64 characters) and F52 (a line telling the user to enter the device code only for a login they just started).

### Mistral reasoning (user decision 2026-10-07)

Until now the mistral kind dropped thinking both ways. The user decided to build it into this feature.

- **Request.** A route's `reasoning_effort` option -- one of none, minimal, low, medium, high, xhigh, max (`_RT_MS_EFFORTS`) `Scripts/llm-router.py:2753` -- is sent as is. Otherwise the effort is `_rt_rs_effort`'s band (KD-14, adaptive is medium), passed through the route's `reasoning_effort_map` and then through the learned remap `Scripts/llm-router.py:_rt_ms_effort`. Absent or disabled thinking sends no key. A `thinking` key or a `reasoning_content` field is never sent, because Mistral answers both with a 422.
- **Response.** Mistral thinking chunks become one Anthropic thinking block whose signature is `lms1.` plus the URL-safe base64 of Mistral's own `signature` (`_rt_ms_signature`); Mistral sent none live, so the signature is the bare `lms1.`. The stream and non-stream paths share `_RtMsContent` `Scripts/llm-router.py:3158`.
- **Replay.** The client's own `lms1.` blocks become one thinking chunk before the text, their texts joined with a blank line and carrying the last non-empty signature. A foreign signature, a `redacted_thinking` block and an undecodable signature are dropped and counted, so `dropped_thinking` now counts only what cannot be carried, not every thinking chunk.
- **`reasoning_effort_map`** (mistral only): at most seven pairs, each key and value one of the seven efforts; anything else is refused at load with the choice list. Precedence: the fixed `reasoning_effort` option, else the computed effort, then the map, then the learned remap. The map and the remap never touch the fixed option.
- **Learning from a 400.** Live, `mistral-medium-3-5` accepts only high and none, and answers low or medium with a 400 `reasoning_effort low is not supported for this model, supported values: [...]`. A 400 matching `_RT_MS_EFFORT_REFUSED_RE` for the computed effort sent is learned as the supported set of (backend, upstream model) -- beside the learned sampling table and under its lock, for the process lifetime, bounded by the config's routes `Scripts/llm-router.py:6703-6743` -- and the request is retried once with the remap: the nearest supported value by rank none < minimal < low < medium < high < xhigh < max, none excluded because thinking was asked for, a tie going to the higher, and none only when it is all the model takes (`_rt_ms_effort_remap`). The learn retry is one per request, shared with the sampling learn (`learn_retry`); a 400 after it, an unparseable supported list and a 400 refusing the fixed option are relayed. One INFO line `reasoning_effort learned: backend=... model=... supported=...` is logged per change of the set, and the `req` line carries `effort=<sent>` when the map or the learned set changed the computed effort `Scripts/llm-router.py:7059-7062`.
- **Verified live** on a second router instance (127.0.0.1:8083, the operator's config): the first stream request sent low, got the 400, learned high and none, retried and got 200; later requests went straight to high; non-stream, stream and replay all 200 with `dropped_thinking=0`.
- **Cases.** Thinking: A46, F18-F21 and G18-G22, with F2, F11 and G5 rewritten for the new semantics (345 to 355). The effort map and the learning: A47, A48, F22-F25, N34, N35 (355 to 363).

### The suite at millisecond deadlines (test only)

The `llm_router` suite had grown to 341 s; at the user's request the cases that wait on one of the router's own deadline constants -- H1-H3, H7-H9, D4, B14, B23 and N13 -- now run on an in-process router (`FastRouter`, and `HFastRig` for the H cases) of a privately loaded module with those constants patched to millisecond scale, the class-level `_RouterHandler.timeout` included, because it is bound to `_HTTP_HEADER_TIMEOUT_S` when the class is defined `tests/test_llm_router.py:FastRouter`. The router's log goes at DEBUG to a 0600 file in the sandbox that I1 sweeps like a subprocess's stderr. H4 and N25, which prove a real signal and a real process exit, stay subprocesses at the production values, and so do H5, H6, O12, O14 and O16. `Scripts/llm-router.py` did not change. The run went from 341 s to about 125-140 s, and the forge description now says about 130 s, under the unchanged 480 s timeout. The plan's note that N13 cannot be shortened is superseded.

### Declared limits added by this feature

The plan's twenty-six, in substance:

1. No ID-token signature check (OIDC Core 3.1.3.7 item 6).
2. Re-serialization may change the config's formatting; the content is verified equal.
3. No runtime OIDC discovery; the provider rows are static, dated 2026-10-06.
4. The rotate-then-crash window needs a re-login.
5. Every start performs one refresh, and one rewrite on a rotating provider.
6. The per-backend lock is held across the token POST and the persist: the POST (`connect_timeout`, then `_RT_OAUTH_IDLE_S` = 15 s per read, an idle bound rather than a total one), the `write_lock` wait, the flock poll (up to `_RT_CONFIG_LOCK_WAIT_S` = 5 s) and the persist's file I/O. Waiters give up with 503 after `connect_timeout` + `_RT_OAUTH_IDLE_S`, so one can give up while the holder is still legitimately persisting. A non-relogin failure is shared for `_RT_OAUTH_FAIL_CACHE_S` = 10 s, an unargued default; relogin failures are not cached (R2-L2). At shutdown a token POST still inside `_rt_send`, or a handler polling the flock, can outlive `_DRAIN_S` (3 s) and is abandoned.
7. The dynamic scrub cap is 64 unpinned values; at about 3 secrets per rotation a superseded token survives at least 21 rotations of one backend. Current tokens are pinned and never evicted. A JWT-shaped secret gets no prefix form. That every production write goes through `_rt_scrub_add` / `_rt_scrub_pin` is listed, not gated. `state` and the nonce are not scrubbed: both are single use and printed in the authorize URL by design, and PKCE protects the code.
8. The reasoning signature is plain, not MAC'd, and bound to the backend name: renaming a backend drops continuity, and the summary is not replayed.
9. Hashed tool ids are consistent within a request and not reversible to the upstream id.
10. The H group covers `openai` with an API key only; codex timeouts are covered through N13.
11. The effort bands are unargued defaults.
12. The omp identity is copied by user decision, and its User-Agent was measured on 2026-10-06 (M1). Impersonating the Codex/omp client on the ChatGPT backend carries a terms-of-service risk the user accepted (review F19).
13. `[::1]:1455` is best effort.
14. A disk token is adopted only after a relogin-class refresh error, or when no refresh token is in memory, and only if it passes the `token` rule; a dead token is never re-adopted, so a fresh `login` is picked up on the next request.
15. `earliest_refresh_at` (now with its 60 s floor) is honoured in-process only.
16. The flock serializes cooperating writers only; a hand edit landing between the hash re-check and the rename can be lost. Non-POSIX is unsupported.
17. A refresh that times out after a server-side rotation loses the rotation, which means a re-login (M6). F38's discard of a rotated token that fails a post-parse check sits beside this one.
18. The config directory and the lock file must be owned by the user and not group- or other-writable; the directory is checked first, before the lock file is created, and every later file operation is relative to the checked descriptor. Otherwise tokens are kept in memory only, with one WARNING.
19. Stale temps are swept only when they match the router's own pattern, are owned by `euid`, are mode 0600 and regular files, and only under the flock; the startup sweep and its lock file happen only when some backend logs in with OAuth.
20. `SO_REUSEADDR` is set on the callback listener on POSIX, `SO_REUSEPORT` never; the listener closes each callback connection first, and no half-close or linger is used (R2-M1). On BSD/macOS a specific-address bind can coexist with another process's wildcard listener. The control against an interceptor is PKCE; the busy-port refusal is UX.
21. The auth spec uses the public-only policy and the system trust store unless `auth_base_url` is set explicitly.
22. Only allow-listed OAuth `error` codes are ever logged or surfaced; `error_description` never is.
23. Identity claims are read only from the direct token-endpoint response; the access token's account claims are trusted on that TLS channel, not under OIDC Core 3.1.3.7 (review F39).
24. The reasoning-signature caps sit inside the 32 MiB inbound body limit.
25. The four kind registries (`_KINDS`, `_RT_KIND_AUTH_PROFILES`, `KIND_CLASSES`, `ADAPTERS`) are listed, not gated, by SC-4.
26. Directory-relative file operations: an interpreter whose `os.supports_dir_fd` / `os.supports_fd` / `os.supports_follow_symlinks` lacks one of them gets no rewrite at all (fail closed, tokens in memory only, no path-based fallback). Path components above the config directory are not checked, the same as at load, and the adoption read stays path-based and takes no flock.

Added after the plan:

27. The non-stream path computes `dropped_thinking` but does not put it on the `req` line, for mistral and for the Responses kinds.
28. `reasoning_effort_map` and effort learning are mistral only; the Responses kinds learn no effort (codex accepted low to xhigh live).
29. The learned sampling and effort sets live for the process lifetime and are never persisted; a learned sampling refusal is never unlearned, and a learned effort set changes only with a later 400.
30. A route's `max_input_tokens` / `max_tokens` are advertised, never enforced, and whether Claude Code reads them is unmeasured.
31. The proactive refresh leaves no trace on the `req` line, and `message_start`'s `input_tokens` is the router's estimate while `message_delta` carries the upstream's measurement.
32. An OAuth access token goes to a non-loopback `http://` `base_url` under `allow_private` with no cleartext opt-in of its own, unlike an `api_key`'s `allow_cleartext_api_key` (review F12, CWE-319).
33. J10 (no new repo path) can fail when another writer works in the same repository during a run.

## Addendum (2026-10-07): mcp-proxy now carries the router's three fixes

Three statements above that mcp-proxy lacks a fix the router has are no longer true at the commits named here; the body above stays as written on its date.

- **Pre-auth refusals and the HTTP/0.9 guard** (c69b643, R-0069, mcp_proxy J43). mcp-proxy now overrides `send_error` with a fixed, body-less answer (the status table's reason phrase, `Content-Length: 0`, `Connection: close`, nosniff, no-store; the request line is never reflected) and carries the HTTP/0.9 guard at the top of its `_refuse`. The parenthesis in "An Anthropic JSON body in `_refuse`" that calls F42 unfixed in ADR 0027 is closed; the router's JSON error body stays a deviation, the fixed-text `send_error` and the guard's placement are now shared.
- **`100 Continue` after auth** (6e91e1d, R-0076, mcp_proxy J44). mcp-proxy's `handle_expect_100` sends nothing and its `_post` sends the 100 only after the bearer, Origin/Host, media type and body-cap checks, so the out-of-scope bullet "mcp-proxy sends `100 Continue` before authentication" no longer holds, and deviation 18 is no longer a difference between the two copies.
- **The ready file removed on shutdown** (1748f46, R-0075, mcp_proxy J45). mcp-proxy unlinks its ready file on an ordered shutdown only while it holds its own pid, opened with `O_NOFOLLOW`, with the same declared read-then-unlink race and SIGKILL limit as the router's V24. Row P17's "which mcp-proxy does not do" and the out-of-scope bullet "mcp-proxy's ready file is not removed on shutdown" no longer hold; deviation 29 is no longer a difference between the two copies.

The two copies are still hand-held and no gate holds them equal, but these ports narrow the divergence the canonical-source lift (roadmap R-0072) would have to reconcile.

## Addendum (2026-10-07): R-0085, R-0083 and R-0084 -- hardenings, the interruptible token POST and one kind registry

Three roadmap items closed on 2026-10-07 close or narrow declared limits recorded above; the body above stays as written on its date. A limit number here is one of "Declared limits added by this feature" in the codex/openai addendum; a finding id `F<n>` is the 2026-10-07 code-mode security review's. Case counts are the commits' own.

### R-0085 -- the review's optional hardenings (a5a27bb, 225466c)

The review's verifiers recorded optional hardenings beside SUPPRESSED verdicts. Each was done red first, in two commits.

a5a27bb, `llm_router` 363 -> 371 (J34 J35 K26 N36-N39 O17), `mcp_oauth` 64 -> 65 (G5):

- **F10** `_rt_loads` refuses a key repeated in one object, with a fixed text that never echoes the key.
- **F15** the role refusal DEBUG lines log the role's type name only.
- **F22** a tool schema nested deeper than 64 levels (`_RT_RS_SCHEMA_DEPTH_LIMIT`) is a 400, never a `RecursionError` answered 500.
- **F23** a message-matched unsupported sampling parameter is learned only when `err.param` is null or names the same parameter.
- **F27** the Responses adapter's `_pending` entry is popped in a `finally` on every error exit (a learn-path 400, an upstream that closes without an answer).
- **F42** the `OAuthError` kind passes through `_log_value` on the refresh-failure line.
- **F45** the learn path parses an upstream 400 body only up to 64 KiB (`_RT_LEARN_BODY_LIMIT`); a larger one is relayed as before and not learned from. It caps what the learn path parses, not what it reads: the body is still read under `_UPSTREAM_BODY_LIMIT`.
- **F51** `_mcp_oauth` refuses a device `user_code` over 64 characters (regenerated into the router).
- **F52** the device login prints a phishing warning beside the code.

225466c, `llm_router` 371 -> 376 (A49 M22 M23 N40 O18), `mcp_oauth` 65 -> 68 (D12 E11 H15):

- **F6** an empty callback connection counts against the login acceptor's bad-connection budget.
- **F12** an OAuth access token over a non-loopback `http://` `base_url` needs the same `allow_cleartext_api_key` opt-in as an `api_key`; without it the backend is refused at load. **Limit 32 is closed**, and the component page no longer lists it.
- **F26** on the Responses kinds only, a call to a tool the request did not offer is refused on the existing "backend returned a tool call" path (502, or one error event on a stream), the name not echoed. Chat-completions (mistral) is not covered: its fixtures offer no tools.
- **F30** an OAuth backend's upstream 403 relays a fixed text; the status stays.
- **F33** test only: the login works without the `api.connectors` scopes.
- **F35** a refresh answer that omits `scope` falls back to the previously granted one; an ID token whose `aud` holds more than one audience needs an `azp`, and a present `azp` must equal the `client_id`. Limit 1 (no signature check) is unchanged.
- **F37** an adopted disk token must pass `_rt_cfg_oauth`'s checks (the minimum length, differing from `auth_token`, the key and field shapes) or it is not adopted, with one WARNING. **Limit 14 is narrowed**: where it says a disk token is adopted only if it passes the `token` rule, it now has to pass the loader's `oauth` rules.
- **F39** documentation only, on the component page: the access token's claims are trusted on the TLS channel. Limit 23 already said so.

### R-0083 -- a token POST inside `_rt_send` is interruptible on shutdown (0267c3b)

`_rt_send` takes a registration hook, `track` (`srv.inflight`, `srv.inflight_lock`, `srv.closing`), handed only to the connection constructor. The plain socket is registered right after its connect; the TLS socket is wrapped with `do_handshake_on_connect=False` and registered before its handshake, so a shutdown during the handshake, the request write or the response head interrupts it, as it already did the body read. `_rt_track_add` shuts down at once a socket registered after `closing` is set. The config flock poll gives up on `closing`: a `ConfigError`, the token kept in memory, one WARNING.

**J4a is amended a second time** (after KD-7's amendment above): it pins `_rt_send`'s exact parameters, allows the hook only as a `cls(...)` argument, and allows only `_rt_oauth_post` to pass it. J36 plants three violations. N41 and N42 ran red first: a 3.5 s abandoned drain became a 0.5 s exit. `llm_router` 376 -> 379.

**Limit 6 is narrowed.** Its last sentence -- a token POST still inside `_rt_send`, or a handler polling the flock, can outlive `_DRAIN_S` (3 s) and is abandoned -- now holds only as follows:

- the resolve and the TCP connect are still unregistered: `connect_timeout` bounds them, and the drain abandons such a handler after `_DRAIN_S`;
- the flock wait no longer outlives the drain, but a rotated token whose write it was waiting for is kept in memory only and lost with the process, so a shutdown that hits the flock wait can need a re-login -- a window beside limits 4 and 17;
- non-stream upstream calls are unchanged.

### R-0084 -- one kind registry (82a4e1a)

One `_RtKindRow` table, `_RT_KIND_TABLE` (kind, adapter class or `None` for a reserved kind, auth mode -> Responses profile), sits after the adapter classes. `_KINDS`, `RESERVED_KINDS`, `_RT_KIND_AUTH_PROFILES`, `KIND_CLASSES` and `ADAPTERS` are derived from it with the values and order they had, and `_rt_check_kind_table` refuses a bad row at import. The unused `_rt_kind_auth_profiles()` is removed; the A14 refusal texts are unchanged. J37 (each name bound once from the table, never a literal, never mutated), J38 (six planted violations) and J39 (the values pinned, six broken tables refused, the A14 texts byte-exact) ran red first. `llm_router` 379 -> 382.

**Limit 25 is closed**, and with it the M5 note R-0084 was filed under: the four registries are no longer listed, not gated -- they are derived from one table and gated by J37-J39, the registry half of NFR-2 that J28 did not cover. KD-12's case of a kind in `KIND_CLASSES` but not in `ADAPTERS` can no longer come from the table, since both derive from the same rows; the 501 guard stays, for a kind that loads without an adapter. **Still a code edit for a new kind:** mistral's two auth special cases.

## Addendum (2026-10-07): _log_value is generated, no longer a copy of the proxy's (R-0070)

"The HTTP front is a copy" lists row P16: `_log_value`, copied verbatim from `Scripts/mcp-proxy.py` with `_LOG_VALUE_WIDTH = 80` and `_LOG_KEYS_SHOWN = 16`. It also names `_log_value` as one of the three verbatim units the two files share. `72ed9b4` (R-0070) lifted that unit into the canonical logging source `Scripts/_mcp_logging.py:_log_value` and generated it into all 17 servers and into the router. The router's hand copy and its two constants were deleted. The router now carries `_LOG_VALUE_WIDTH, _LOG_KEYS_SHOWN, _log_value` as one generated region beside `_configure_logging` `Scripts/llm-router.py:_log_value`. The behaviour is byte-identical, and router case J11 stays green.

### What this changes on this page

- **Row P16 is no longer a hand-held copy.** `amalgamate.py --check` now holds it equal to the proxy's, through the canonical source. "A later lift is a move without renames" turned out to be true: the name was kept, and so were the two constant names the tests read.
- **Two verbatim units remain hand-held:** `_http_token_value` and `_HeaderDeadlineReader`. The second is adapted since validation round 1. These and the adapted front are still the drift risk (R13) that the canonical-source lift of the front (roadmap R-0072) would have to reconcile.
- **The router's declared hand copies are unchanged.** They are still the three `Scripts/_mcp_chrome.py` address-classifier names in "The hand copies". `_log_value` was never among them, because it is not a canonical-block name the hand-copy census could see until this commit made it one.

### Not taken

The router did not take the six strict-JSON blocks that `d4a241b` (R-0067, R-0068) generated into the MCP fleet. Its request bodies are still parsed by its own `_rt_loads` (deviation 20 and F9 above), which already refuses non-finite numbers and integer literals over 4300 characters. The fleet's `_strict_loads` mirrors its bound and refusal style.

## Addendum (2026-10-07): R-0072: the front's identical pieces are generated

`18f2b24` (R-0072) lifted the identical half of this page's copied front into the canonical source `Scripts/_mcp_httpfront.py`, generated into both the router and `Scripts/mcp-proxy.py` ([[0029-the-http-front-is-a-domain]]). The body above, its copy table and its anchors stay as written on their date.

### What this changes on this page

- Now generated: row P5 (`_http_token_value`), row P11 (`_single_header`), the `parse_request` half of P8, `_HeaderDeadlineReader`, the `server_bind` / `process_request` / `handle_error` part of P7, and row P17 (the ready file). `handle_expect_100` and the stdlib-refusal header pair are generated too.
- `_rt_write_ready_file` and `_rt_remove_ready_file` are renamed: they are the generated `_http_write_ready_file` and `_http_remove_ready_file`, which take the error class and the logger as arguments. The removal now logs the proxy's two type-only DEBUG lines (KD-6 of ADR 0029).
- Deviation 17, the reader's per-recv cap, is now a constructor argument of the generated `_HeaderDeadlineReader`: the router passes its socket timeout, the proxy its header bound.
- D3 of ADR 0029 converged the stdlib refusals: the router's `send_error` now adds `nosniff` and `no-store`, through the generated constant.
- Still hand-held, as declared adaptations pinned by `tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`: the rest of P7 (the constructor, `process_request_thread` with the drain count) and of P8 (`setup`, `handle_one_request` with the shorter pre-auth bound), and P9, P10, P12, P13 and P14.
- The first 2026-10-07 addendum's "the two copies are still hand-held and no gate holds them equal" and the R-0070 addendum's "two verbatim units remain hand-held" no longer hold for the lifted blocks: `amalgamate.py --check` holds them equal through the source.

### Anchor superseded

Deviation 29 anchors `Scripts/llm-router.py:_rt_remove_ready_file`. That anchor no longer resolves: the function is now `_http_remove_ready_file`, generated from `Scripts/_mcp_httpfront.py:_http_remove_ready_file` into the router. The original line stays as written. This is the only ADR anchor the lift breaks: ADR 0027's `Scripts/mcp-proxy.py:_HeaderDeadlineReader` still resolves, this page's `Scripts/llm-router.py:_rt_cfg_token` is unchanged, and row P17 names `_rt_write_ready_file` / `_rt_remove_ready_file` in a table cell without a path, which is not an anchor.

## Addendum (2026-10-08): R-0093 and R-0095: F26 on mistral and every upstream call interruptible on shutdown; R-0091 measured; two dead anchors

Two roadmap items closed on 2026-10-08 in `51eced1`, and `a0e2d27` settled a third. Each narrows or closes something recorded above. The body and the earlier addenda stay as written on their dates.

### R-0093: F26 covers the chat-completions kind

The R-0085 addendum's "Chat-completions (mistral) is not covered" no longer holds. A Mistral tool call naming a tool the request did not offer is refused with the text the Responses kinds already used `Scripts/llm-router.py:_RT_UNOFFERED_TOOL`, and the name is not echoed:

- On a stream, the call is refused before it is buffered, as one error event `Scripts/llm-router.py:MistralStreamTranslator`.
- On the non-stream path, it is a 502 `api_error` `Scripts/llm-router.py:MistralAdapter`.
- One unoffered call refuses the whole answer, including any offered call beside it.

The offered set is computed once for all three translating kinds, and the Responses translator now uses the same helper `Scripts/llm-router.py:_rt_offered_tools`. passthrough and llamacpp are declared exempt in that helper's docstring: they relay the backend's own Anthropic `tool_use` blocks and never mint one. This is a behaviour change for mistral. An answer that calls a tool the client never offered was relayed before; now it is an error. Router cases H8 and H9, whose streams call `Read`, now offer it.

### R-0095: resolve, connect and every model call are interruptible on shutdown

Limit 6 as narrowed in the R-0083 addendum, and the body's declared limit "The shutdown drain does not cover JSON relays", are closed except for the one piece below that cannot be interrupted.

- **The resolve moved into `connect()`** of both connection classes, whose constructors no longer take an address list `Scripts/llm-router.py:_RtHttpConnection`. With the shutdown registry, the resolver thread is joined in `_RT_TICK_S` slices and the call gives up once `closing` is set `Scripts/llm-router.py:_rt_resolve`. `getaddrinfo` itself cannot be interrupted, so a shutdown waits at most one tick for a stalled resolve, never `connect_timeout`. The resolver thread is abandoned as before.
- **Each socket is registered before its TCP connect** `Scripts/llm-router.py:_rt_open_socket`. `closing` is checked before each address and after the connect returns, and a failed socket is deregistered and closed. The TLS socket is added to the registry before the TCP socket leaves it, so the connection is never missing from the registry.
- **Both send sites pass the hook.** `_post` hands `srv.track` to `_rt_send` as R-0083's `_rt_oauth_post` already did `Scripts/llm-router.py:_RouterHttpServer`. Every path that closes the connection untracks it first, and the handler's `finally` untracks the last one, which is idempotent.
- **The sweep in `_rt_shutdown`** now reaches a model call that is connecting, waiting for its head or reading its body, not only a live pump `Scripts/llm-router.py:_rt_shutdown`.

Three behaviour changes follow:

1. A non-stream call in flight at SIGTERM is cut at once and answered 529 "router shutting down". Before, it blocked for up to `idle_timeout` and was abandoned when the `_DRAIN_S` drain ended.
2. A stream request still waiting for its upstream head is answered 529 the same way. Before, only a live pump was registered.
3. Any `UpstreamError` raised while `closing` is set and before the SSE head is relabelled 529 "router shutting down" `Scripts/llm-router.py:_RT_SHUTTING_DOWN`, whatever the transport reported: a 502 connect error or a 504 timeout caused by the sweep is no longer shown to the client as a backend fault.

**J4a is amended a third time, and inverted.** R-0083's rule allowed only `_rt_oauth_post` to pass the registration hook. The rule now requires every `_rt_send` site to pass it. The allowed-site list is gone, and J36's planted control now plants `_post` *not* passing the hook `tests/test_llm_router.py:rule_j4`. The hook is still data handed only to `cls(...)`.

**Not measured on Linux.** That `shutdown(SHUT_RDWR)` wakes a stalled TCP connect was measured on Darwin 23 only; the connect returns at once, reporting success, and the post-connect check catches it. Case C15 runs its connect half only where the kernel leaves a connect into a full `listen(0)` backlog pending. Elsewhere that half is skipped and the case reports INFO, with its resolve half still run.

The flock bullet of narrowed limit 6 stands: a rotated token whose write was waiting on the config flock is kept in memory only and lost with the process. `llm_router` went from 384 to 387 cases: C15 and C16 (group C 14 -> 16) and G23 (group G 22 -> 23).

### R-0091: the router's post-parse per-recv timeout (`a0e2d27`)

When a router connection that has not authenticated leaves the generated `parse_request`, its per-recv timeout is the 10 s header bound, not the 5 s pre-auth bound. Measured: no socket read happens between the parsed head and the precheck verdict, so the 5 s bound still covers every pre-auth read. The comment was reworded in the canonical source, and the behaviour is unchanged. The decision record is the 2026-10-08 addendum of [[0029-the-http-front-is-a-domain]].

### Two anchors on this page no longer resolve

- Deviation 29's anchor to the router's `_rt_remove_ready_file` is dead. The R-0072 addendum above already records this: the function is the generated `_http_remove_ready_file` `Scripts/_mcp_httpfront.py:_http_remove_ready_file`.
- The codex/openai addendum names its plan as feature-implementation-plan.md at the docs root. That file is no longer in the tree: the path is the `/p:feature-plan` slot ([[layer-contract]]), and the OAuth plan has been moved out of `docs/`. The red-first record that addendum quotes was never committed either.

Neither line is edited. A dead body anchor on an accepted ADR is an advisory, not a gate ([[0019-only-gate-on-what-you-can-prove]]).

## Addendum (2026-10-27): Image endpoints, Phase 1: OpenAI-shaped generations and edits on an openai api_key route

The plan of 2026-10-27 adds two OpenAI-shaped endpoints for clients other than Claude Code, in four phases. Phase 1 is built: `POST /v1/images/generations` and `POST /v1/images/edits` on kind `openai` with an `api_key` backend, without streaming. Phases 2-4 are planned only, at the end of this addendum. The shape lives on [[llm-router]], section Image endpoints.

**What changed.** A route key `modality` (`chat`, the default, or `image`) marks an image route (`Scripts/llm-router.py:_rt_cfg_route`). An image route is refused at load on a kind whose adapter does not list `image` in `MODALITIES` (every kind but `openai`), on the `openai-oauth` profile, on `default`, and when its `model` differs from its route key. The two paths take no query; `/v1/images/edits` takes JSON or `multipart/form-data`, generations JSON only (`Scripts/llm-router.py:_RT_ENDPOINT_MEDIA`). A hand-written, canonical-only sniffer reads `model` and the guard fields `stream`, `n`, `size`, `output_format` and `response_format` (`Scripts/llm-router.py:_rt_image_fields`); the model is looked up by exact name with no `default` fallback (`Scripts/llm-router.py:_rt_route_named`); `stream: true` is a 400. The client's bytes go upstream unchanged under a rebuilt `Content-Type`, through the existing single `_rt_send` call, and a 2xx JSON answer comes back unchanged (`Scripts/llm-router.py:_relay_image`). Errors on the image paths are the OpenAI envelope `{error: {message, type, param, code}}` (`Scripts/llm-router.py:_rt_openai_error_body`). A new flag `--max-image-requests` (default 4, range 1-64) caps the image requests in flight with an immediate 429 and `Retry-After: 1`. `GET /v1/models` entries gain `object`, `created`, `owned_by` and `modality`, the list gains `object`, and `?modality=chat|image|all` filters it; this amends the Shape paragraph of the 2026-10-06 models addendum above.

**Decision: a route marker, not the endpoint alone.** Rejected: inferring the modality from the endpoint. Nothing would then stop a chat model being sent to an image endpoint -- a silent misroute answered by a confusing upstream error -- and `/v1/models` could not mark image models. With the marker a wrong-endpoint request is a 400 naming the route's modality, both ways, and an image path never falls back to `default`, so a typo cannot send a prompt to an unintended model.

**Decision: the OpenAI envelope on the image endpoints only.** Image clients are typed against OpenAI and branch on `code`, which the Anthropic body lacks. The flag that picks the envelope is set by the precheck as soon as the path is known and reset for every request of a keep-alive connection. Every other path keeps the Anthropic envelope, and so do the two refusals answered before the path is known: a header-defect 400 and an unknown-path 404.

**Decision: one send site.** Rejected: a second handler with its own `_rt_send`, which breaks J4a and would need a rule change for no gain. An image request selects `image_request` and `image_head` inside the one call's argument expressions; J4a and J4b are unchanged.

**Decision: a raw relay.** Rejected: parsing and re-serializing the answer, a multi-MiB base64 re-encode for nothing. The openai 2xx is checked -- media type `application/json`, first non-space byte `{`, the 64 MiB bound -- and relayed byte for byte, neither parsed nor scrubbed; only a kind that must change the body would parse it.

**Decision: no multipart rewriting, so the model equals the route key.** Rejected: a boundary-aware rewriter of the `model` part -- new bug surface, buffering and a changed Content-Length -- for a cosmetic alias. The loader requires an image route's `model` to equal its key (`IMAGE_MODEL_IS_KEY`); an operator who wants an alias configures a second route key.

**Decision: a canonical-only sniffer.** The router vets the bytes the upstream will read, so every spelling the two could read two ways is refused rather than resolved (ADR 0015): one `boundary` parameter, CRLF-only delimiters, a closing delimiter with no epilogue, only `Content-Disposition` and `Content-Type` part headers and the first in one spelling, no `filename` on a wanted field, no repeat, exact-case JSON keys, one spelling per guard value, and caps of 64 parts, an 8 KiB part head, a 4 KiB field and a 32 KiB prompt. api.openai.com's multipart parser could not be measured, so the sniffer is canonical-only by design rather than matched to it.

**Decision, for Phase 2: sd.cpp as a new kind `sdcpp`, not an `openai` profile.** The `openai` kind is a Responses adapter bound to profile rows with OAuth and API-key modes; sd-server has no chat, no auth, different errors and guards of its own, and a second set of conditionals inside the openai adapter would couple two unrelated backends. A kind is one registry row; the static pins it moves (J12, J39, A14) are listed in the plan. Not built.

**Deviations from the plan.** (1) A `multipart/form-data` Content-Type on `/v1/messages` is a 400 `malformed header`, not the 415 the plan assumed: the stdlib's header parser records multipart defects for a header block that has no body. Q5 pins it as stdlib-dependent. (2) Hence a precheck tolerance: on an image path, and only when the Content-Type's main type is multipart, the three defects `NoBoundaryInMultipartDefect`, `StartBoundaryNotFoundDefect` and `MultipartInvariantViolationDefect` -- matched by class name, since `email.errors` is not imported -- are not a malformed header; any other defect still is (`Scripts/llm-router.py:_RT_MULTIPART_DEFECTS`). (3) The relayed upstream error text has its non-printable characters replaced through `str.isprintable`, not `_rt_one_line`. (4) The `prompt` field allows tab, CR and LF; every other sniffed field is control-free. (5) A parameter on an `application/json` Content-Type, such as `charset`, is dropped rather than refused, since the upstream is sent the rebuilt value; on multipart any parameter but the one `boundary` is still a 400.

**Security review of the plan (2026-10-27, verdict REVISE, one HIGH).** S1 (HIGH): sd-server reads native controls embedded in the prompt, so the planned sdcpp guards are bypassable through it -- the gate of Phase 2, below. S2 (MEDIUM): a parse differential between the sniffer and the upstream -- answered by the canonical-only rules. S3 (MEDIUM): upstream error text relays masked key fragments -- answered by the fixed 401/403 credential text, text relayed for 400/404/413/422/429 only, the 300-character cut and the pattern strip. S4 (MEDIUM): image waits starve the shared connection cap -- answered by `--max-image-requests`. S5 (MEDIUM): an echoed client Content-Type widens the header channel -- answered by the rebuilt value and the new `content_type` header-value rule. S6 (LOW): `InboundRequest.raw` and `fields` hold prompts and images -- J43. S7 (LOW): a client-chosen upstream path -- fixed `IMAGE_PATHS`, J44 and Q18; refusing a JSON edit for a multipart-only kind is the `IMAGE_EDIT_JSON` attribute. S8 (LOW): codex specifics -- Phase 3. S9 (INFO): a JSON edit's `image_url` is fetched by OpenAI -- declared.

**Security review of the code (2026-10-27, verdict APPROVE).** Five lanes, 16 deduplicated candidates. To keep them apart from the suite's group F cases they are called code-review findings here. Verified and fixed in the hardening round: code-review F1 (LOW), a `filename` part under a wanted name was skipped and the repeat check sat inside the no-filename branch -- a `filename` on a wanted name is now a 400 and every wanted part is checked for a repeat; F2 (LOW), an empty-body part let its header terminator double as the next delimiter's CRLF, and a bare-LF delimiter in skipped data or the preamble passed -- both are now a 400; F4 (LOW), JSON keys were read exact-case, so `Model` reached a possibly case-insensitive upstream unvetted -- a key that case-folds to a wanted name without being spelled so is now a 400; F7 (LOW), the redaction patterns were case-sensitive and missed `org-`, `proj-` and `req-` ids -- they are now case-insensitive and cover `org`, `proj`, `req` and `sess` ids and `****abcd` key tails; F10 (INFO), a `--max-image-requests` not below `--max-connections` makes the cap vacuous -- one startup WARNING says so. Suppressed, with the review's reasons: F3 (the guard fields are vocabulary-restricted), F5 (the three defects arise only from a multipart Content-Type), F6 (one strict query parser in the precheck and the handler; route ids are already disclosed), F8 (the slot is released on every path; declared), F9 (memory at parity with /v1/messages, inside the declared worst case), F11 (the raw 2xx relay is the documented design), F12 (`image_url` fetched by OpenAI, declared), F13 (no live sink formats an InboundRequest), F14 (no credential-less image route can be built), F15 (the rebuilt boundary cannot hold a CR, LF, NUL, semicolon or double quote), F16 (Retry-After digits only; the model echo is escaped). Three of the review's optional hardenings landed anyway: a text part's Content-Type allow-list (F3), the multipart main-type gate on the precheck tolerance (F5), and `repr` and `str` in J43's set of format calls with a read-only `fields` default (F13). Not built: a per-route switch to refuse JSON edits (F12).

**Cases.** The `llm_router` suite goes from 404 to 425 typed cases: group Q, 18 cases (Q1-Q18 with Q8b; the plan's Q13 is folded into Q1), and group J from 41 to 44 with J42-J44 (`tests/run.py:SUITES`). Group Q runs before I and J. The B-group model-list pins were edited in place for the new keys.

**Declared limits.**

- `--body-limit` (32 MiB by default) bounds an upload; large edits need it raised, and there is no per-image limit. A chunked upload is a 411.
- `--max-image-requests` bounds the image requests in flight on top of `--max-connections`, which chat and image requests share; a value not below it refuses nothing. The memory figure is the component page's existing worst case, about 6 GiB of bytes at the defaults; image answers are the first that approach its 64 MiB per-answer bound in practice, one peaking near twice that while it is copied.
- `idle_timeout` is per read, not a total deadline, and bounds the wait for the answer's head; nothing notices a departed client on the synchronous image path, so the upstream call runs to its end and holds its slot.
- Upstream error text is relayed for 400, 404, 413, 422 and 429 only, scrubbed, pattern-stripped and cut; the strip matches the listed shapes only.
- The 2xx relay is checked, not parsed and not scrubbed.
- A JSON edit's `image_url` values are fetched by OpenAI, outside the router's address policy.
- Header-defect 400s and unknown-path 404s keep the Anthropic envelope.
- Image routes are listed by default, so they appear in Claude Code's model picker.
- No model aliasing on an image route, and no streaming.

**Not verified.** No call has reached api.openai.com: the SDK smoke has not run. Whether it accepts the JSON edit form on gpt-image models (OQ-1) is open, and how its multipart parser reads an odd body is unknown -- the reason the sniffer is canonical-only. Which clients upload chunked (OQ-8) is not measured.

**Planned, not built.**

- *Phase 2, sd.cpp (kind `sdcpp`).* Gated by the plan review's S1. The extraction rule was read from source at sd.cpp commit `be486498eadc29dc3cd234d944c2180ed42b1415` (master of 2026-10-26): the marker is the literal tag pair `<sd_cpp_extra_args>...</sd_cpp_extra_args>`, matched by a regex that does not cross a line end; a bare JSON object is not recognised; the block overrides the top-level fields, and some of its keys (custom sigmas, hires steps, the base64 media arrays) have no upper bound after the server's own clamps. The rule that will ship: the sdcpp adapter sniffs `prompt` (32 KiB cap) and refuses with a 400 any prompt holding the case-insensitive substring `sd_cpp_extra_args` -- wider than the upstream's case-sensitive regex, failing closed -- while the brace rule the plan first proposed is dropped. On sd-server's OpenAI routes a `lora` in that block takes a raw server-side path with no resolver, so sd-server must be reachable only through the router (a loopback bind or a firewall); what it does with a path that is not a model file is open (OQ-10). sd-server has no auth, and the other prompt syntaxes it executes are not inspected. JSON edits to an sdcpp route will be refused at the router. The source must be re-read whenever the sd.cpp version in use changes.
- *Phase 3, codex images.* The ChatGPT backend's image endpoint is a private, unstable API. Gated by a live smoke against a real codex login -- the path, the body fields, whether `model` belongs, the answer and error shapes, whether headers such as `originator` and `x-codex-image-turn-id` are required, a 401 refresh retry; if any of it is unstable or refused it is recorded as deferred and no code lands. A 401 refresh retry would replay the raw edit body, user images included, once.
- *Phase 4, streaming.* `stream: true` stays a 400 until then. Gated by the line and event limits: a partial image is one base64 `data:` line, so an image stream needs a line limit far above the chat one (the plan starts at 48 MiB) and an event limit above 2 MiB (starting at 64 MiB), both held below the memory budget times the concurrent streams; the event names, and whether a terminal marker follows `*.completed`, are unmeasured (OQ-5).

## Addendum (2026-10-27): Image endpoints, Phases 2-4: the sdcpp kind, codex image routes and streamed image answers; plus the image error passthrough, tool_result images, the vision key and output_config.effort

The three remaining phases of the image-endpoints plan are built, in the order Phase 3 (`1a05ebd`), Phase 2 (`8a84cbc`, hardened in `8394e1e`), Phase 4 (`8a2b571`, hardened in `89b26a0`), with the account-id scrub of `ea255a0` closing a gap Phase 3 declared. Before them, `5448fca` made the image error relay carry the upstream's own error tokens. Two earlier router changes that preceded Phase 1 had no addendum and are recorded here too: images inside tool results with the route key `vision` (`75909bc`), and `output_config.effort` as a source of the reasoning effort (`af3ca10`). The shape of everything below lives on [[llm-router]], sections Backends and Image endpoints; this addendum keeps the decisions, the measurements and the review outcomes.

### The upstream error tokens on image routes (5448fca)

For the statuses whose upstream message is relayed (400, 404, 413, 422, 429) the upstream error object's `type`, `code` and `param` now ride along, each only when it fully matches `[A-Za-z0-9_.-]{1,64}` and the KD-9 scrubber and the secret-pattern strip leave it unchanged; anything else falls back to the status-derived value, and 401, 403 and 5xx keep their fixed text and derived tokens (`Scripts/llm-router.py:_rt_image_token`). The reason is a client decision, not cosmetics: an SDK branches on `code`, and before this change a quota error reached it as `rate_limit_exceeded` instead of `insufficient_quota`. The same commit made the image paths' duplicate-JSON-key refusal say that a key appears more than once -- the rule, never the key; the `/v1/messages` wording is unchanged. Pinned inside Q8b and Q10.

### Phase 3: image routes on a codex backend (1a05ebd, ea255a0)

**What was built.** A `codex` backend accepts `modality: image` routes and relays them, raw, to the ChatGPT backend's private `POST <base>/codex/images/generations` and `/edits` beside the profile row's `/codex/responses` (`Scripts/llm-router.py:CodexAdapter`). The image hooks the openai kind had are lifted into one shared class, `Scripts/llm-router.py:ResponsesImageAdapter`, which both Responses kinds now extend; the profile row's fixed header pairs -- account id and residency, `OpenAI-Beta`, the identity -- are factored out of the chat head into `Scripts/llm-router.py:_rt_rs_profile_pairs`, so a chat request's head is byte-identical to before and an image request gets the same pairs with `Accept: application/json` and no `session_id` (that one derives from a chat body's cache key, which an image body lacks). The `openai-oauth` refusal for image routes now applies to kind `openai` only; the kind's `MODALITIES` decide the rest. One upstream 401 forces one token refresh and resends the same raw body once -- user images included, as the plan declared -- and a second 401 is the re-login hint in the OpenAI envelope; a 403 is the fixed refused-this-login text with status 502.

**Deviation from the plan's gate.** The Phase 1 addendum gated Phase 3 on a live smoke against a real codex login, with no code landing if the endpoint proved unstable or refused. The code landed first, marked experimental, with the smoke pending; the smoke ran afterwards and passed. Recorded here rather than smoothed over.

**Measured live on 2026-10-27.** Through the running router with `gpt-image-2` on a codex login: a generation answered 200 in about 22 s, and a JSON edit carrying `images[].image_url` answered 200 in about 16 s. Both answers held the keys `background`, `created`, `data` (`[{b64_json, generation_id}]`), `output_format`, `quality`, `safety_identifier`, `size` and `usage`; a requested `quality` of `auto` came back resolved to `low`, a `size` of `auto` resolved to `1254x1254`. Not tried on codex: a multipart edit and `output_format` `jpeg`. No quota or limit information appears in a successful answer, and the quota-exhausted error shape was not seen. The S group's fixtures stay synthetic.

**The account id is scrubbed (ea255a0).** Phase 3 shipped with a declared gap: an account id echoed inside a relayed upstream 400 or 429 message was not scrubbed, only the tokens were. It is now pinned in the KD-9 scrubber under its own key, apart from the tokens' pin a refresh replaces, from the token store's seed, a login adopted from disk and every refresh (`Scripts/llm-router.py:_rt_scrub_pin_account`). An id shorter than `Scripts/llm-router.py:_RT_ACCOUNT_PIN_MIN` (8) characters is not pinned, since it would shred ordinary words out of every relayed message. Chat and image relays share the scrubber. S4 pins a 400 and a 429 that echo it.

### Phase 2: the sdcpp kind (8a84cbc, 8394e1e)

**A kind, not an openai profile** -- the decision the Phase 1 addendum recorded, now built as one registry row (`Scripts/llm-router.py:_RT_KIND_TABLE`) and `Scripts/llm-router.py:SdcppAdapter`, with the static pins J12, J39, A14 and J42 moved deliberately. Its `MODALITIES` is image only; its chat hooks are the base ones and are never reached.

**The prompt marker rule.** sd-server reads native arguments from a tag pair embedded in the prompt, and that block overrides every guard the router applies and, on its OpenAI routes, takes a raw server-side LoRA path. The router refuses any prompt that holds the substring `sd_cpp_extra_args` after `casefold()`, in the decoded JSON string or the multipart `prompt` field, with a 400 that names the rule and never the prompt. Measured at sd.cpp `be486498`: only the tag pair is recognised, so the plan's brace rule is dropped -- braces, quotes and JSON-looking text pass byte-exact. The substring is deliberately wider than the server's case-sensitive regex: `casefold()` also maps U+017F (long s) to `s`, so a spelling the server would not match is refused too (failing closed; R10 pins it). The order-of-operations check the code review asked for was done by the lead: extraction of the block is the first and only transformation of the prompt on these routes, and `<lora:...>` tags are off there, so no split-marker bypass exists -- no other rewrite can join two harmless halves into the tag after the router has looked. There is no opt-in; `allow_prompt_args` is reserved and refused as an unknown option.

**The other guards,** each a router-side 400 naming a rule: `stream` true (the server would ignore it silently); a JSON-form edit (`IMAGE_EDIT_JSON` false; the server takes multipart only); `n` outside 1 to the route's `max_n`; `response_format` `url`; `output_format` `webp`; a `WxH` `size` over `max_pixels`; an edit without an explicit `WxH` `size` (8394e1e: the server sizes an edit from the uploaded image when `size` is absent or `auto`, clamped to 4096 per side, which would sidestep `max_pixels`; a generation keeps the server's operator-set default); and an `output_compression` that is not an integer 0-100. The `size` vocabulary became canonical router-wide in 8394e1e: `auto` or a `WxH` with no zero side and no leading zero.

**The answer is rebuilt, not relayed.** A 2xx is parsed under the 64 MiB bound and must be an object with a `data` list of 1 to n objects (n the request's, one when absent) each holding a string `b64_json`; it is answered 200 whatever 2xx the server used, as `{created, output_format, data: [{b64_json}]}` with `created` and `output_format` kept only when well typed, and `generation_info`, any `usage` and every other key dropped (`Scripts/llm-router.py:_rt_sd_answer`). No upstream header reaches the client. Errors take the image mapping with sd-server's shapes: a string `error`, or `{error: server_error, message}` whose message becomes the text for the relayed statuses; an empty or non-JSON body is the fixed per-status text; 401 and 403 carry fixed text with no credential wording; a 503 `model_not_loaded` or `model_loading` stays a 503 with a fixed text and that code.

**Route options and config constraints.** `max_n` (1-8, default 4) and `max_pixels` (65536 to 4096 x 4096, default 2048 x 2048), bounded in 8394e1e to what the server accepts. A backend of kind `sdcpp` refuses `api_key` and any `auth_header` but `none`, and its `auth_header` is forced to `none`: sd-server has no authentication, so a key could only travel in the clear to a server that ignores it. The address policy is `llamacpp`'s with no exemption -- a LAN host needs `allow_private`, a loopback one `allow_loopback` as well. The route key may differ from the upstream `model`, which sd-server ignores (`IMAGE_MODEL_IS_KEY` false).

**sd-server must be reachable only through the router.** Its OpenAI routes take a raw server-side LoRA path in the extra-args block, outside the server's sandboxing resolver, so a client that reaches the server directly has every power the prompt rule withholds; what the server does with a path that is not a model file is open (OQ-10). The operator's sd-server at `192.168.2.245:8082` is reachable on the LAN today; that exposure is accepted by the operator for now.

**Measured live (2026-10-27)** against a real sd-server, FLUX Klein at sd.cpp `be486498`, through the router: a 512 x 512 generation in about 2.7 s and a multipart edit in about 5.3 s. Also measured: an edit with no `size` takes the uploaded image's size; this build refuses `webp` on both endpoints; a malformed `size` silently falls back to 1024 x 1024; `n` is clamped to 1-8; a 404 has an empty body; every answer carries `Access-Control-Allow-Origin: *`; an unparseable JSON body is a 500 `{error: server_error, message}`; an invalid extra-args tag is a 400. The R group's fixtures follow this probe.

**Code review of 8a84cbc (2026-10-27, verdict APPROVE, no CRITICAL, HIGH or MEDIUM).** Two find lanes, 13 raw candidates deduplicated to 11, each verified; an implementation audit ran in parallel. The sd.cpp source facts the verdicts rest on: the OpenAI routes read only `prompt`, `n`, `size`, `output_format` and `output_compression` (plus the image and mask parts of an edit); native parameters reach the server's JSON reader only through the prompt-embedded tag pair; `size` is parsed with base-10 `stoi`; an edit without `size` or with `auto` takes the uploaded image's dimensions. Verified and fixed in 8394e1e: V3 (LOW), an edit's absent or `auto` size bypassed `max_pixels` -- an explicit size is now required on edits; V7 (LOW), an empty or over-long `data` list passed as 200 -- now a 502 unless it holds 1 to n entries; V9 (LOW), an `api_key` on an sdcpp backend was sent as `x-api-key` to the unauthenticated server -- now a config error, with `auth_header` forced to `none`; V10 (INFO), the upstream's 2xx status was relayed verbatim -- now always 200 with the rebuilt body. Suppressed, with the review's reasons: V1, multipart field-name case (the upstream's lookups are exact); V2, blind forwarding of unread fields (the handlers read only the listed keys); V4, a leading-zero size (base-10 parsing, no divergence); V5, a JSON parser differential (no case where the two accept differently); V6, 2xx parse amplification (at parity with chat, inside the declared worst case); V8, error text paths (the server's texts are fixed strings, a 500 gets fixed text); V11, route option bounds (operator-set). The optional hardenings of V1 (a case-folded multipart name is a 400, as the JSON path already did), V2 (`output_compression` 0-100), V4 (the canonical size spelling) and V11 (`max_n` at most 8, `max_pixels` at most 4096 x 4096) landed anyway in 8394e1e.

### Phase 4: streamed image answers (8a2b571, 8394e1e, 89b26a0)

**Only the openai kind streams.** `IMAGE_STREAMING` is set on the `openai` adapter alone, which also overrides the two stream hooks; codex (its stream is unmeasured) and sdcpp (sd-server has none) keep the 400. J42 pins it. A streaming request's non-2xx answer arrives as JSON and is mapped to the OpenAI envelope before any SSE head; a 2xx that is not `text/event-stream` is a 502.

**A new sans-IO translator, `Scripts/llm-router.py:ImageEventRelay`,** relays whole events byte for byte -- never re-serializing a base64 image -- in the Images API's own vocabulary, with nothing Anthropic in the stream. Its limits: a 48 MiB `data:` line (`Scripts/llm-router.py:_RT_IMAGE_SSE_LINE_LIMIT`, enforced by the pump), a 64 MiB event (`Scripts/llm-router.py:_RT_IMAGE_SSE_EVENT_LIMIT`, enforced by the translator) and 256 lines per event (`Scripts/llm-router.py:_RT_IMAGE_SSE_EVENT_LINES`), the size and line checks made before a line is appended and the event emitted with one join. Every failure after the head is one in-band OpenAI-shaped `event: error` and a close: a stall past `idle_timeout`, an over-long line or event, an upstream that ends before the completed event, a cut connection; an upstream `event: error` is re-emitted with its message through the scrubber and the pattern strip, its type, code and param only as short tokens. Upstream comment lines are dropped; the router's keep-alive is the SSE comment `: ping`, never Anthropic's `event: ping`, available from the start because the first partial image can be a minute away.

**The clean end is a flag, not a substring.** The translator owns a `completed` flag, set only after a real completion event, and the relay loop reads that instead of searching the written chunk; the Anthropic and Responses translators have no such attribute and keep the `message_stop` test. Completion is an event whose `event:` name ends in `.completed`, or (8394e1e) an event with no `event:` line whose `data:` JSON `type` ends in `.completed` within the data line's first 512 bytes (`Scripts/llm-router.py:_RT_IMAGE_TYPE_WINDOW`) -- a regex over that slice, never a parse of a multi-MiB line; a `type` further in is not read, and the stream then ends as an early EOF. An image stream's idle clock restarts only when a line made the relay emit bytes, so an upstream that trickles comments is a stall.

**Memory.** One stream can hold roughly four to five times its largest line at once -- the queue item or one accepted oversize line, the pump's next line, the held lines, the one join copy -- and up to `--max-image-requests` streams run together, on top of the component page's existing worst-case figure.

**Code review of 8a2b571 (2026-10-27).** One find lane, stream-relay, 10 candidates. Fixed in 89b26a0: S1, memory amplification -- the size check moved before the append, a 256-line cap per event and one join, the memory note corrected from about three to four to five times the largest line; S3, a bare CR inside a line over 64 KiB, which a client splits and the router did not -- now an in-band `malformed line`; S4, an event's name taken from its first `event:` line while a client uses the last, and arbitrary fields relayed -- a second `event:` field or any field but `event:` and `data:` is now an in-band `unexpected event field`. The clean-end substring test, which recorded an upstream error whose message held `.completed` as a success, was replaced by the `completed` flag in the same commit. Declared: S7, a slow-reading client holds an image slot, bounded only by the 30 s per-write socket timeout and by `--max-image-requests`; there is no cumulative write budget. Suppressed: S6, no total stream deadline -- none was added, though the comment-trickle half of it is closed by the idle-clock rule above (T13 pins it); and S10, a trailing half event at EOF is discarded and replaced by an error, which fails closed. The multipart field-name case finding of this lane was fixed with Phase 2's V1 hardening in 8394e1e.

### Images in tool results and the vision route key (75909bc)

**Responses kinds.** A `tool_result` holding at least one image becomes a `function_call_output` whose `output` is a list of `input_text` and `input_image` parts in block order; `is_error` puts `Error: ` in front of the first text part, or a leading `Error:` part when the first part is an image; a result without an image keeps its plain string `output`. The list form is a profile-row switch, `output_parts`, true on all three rows, so a backend that refuses it can be turned back to the string form with `[image omitted]` without a code change (`Scripts/llm-router.py:ResponsesProfile`). Live acceptance by codex and openai-oauth is unverified.

**Mistral.** The same `tool_result` becomes a tool message whose `content` is a list of `text` and `image_url` chunks. The route option `tool_result_images` (default true) set to false restores the text `[image omitted]`. One live probe (2026-10-27) showed mistral-small-2603 and mistral-large-4 read such an image; the documentation does not promise it, the OpenAPI schema and mistral-common only permit it.

**The vision key.** A new route key, `vision` (a JSON boolean, default true): false makes the router refuse, centrally and before any adapter runs, any request holding an image -- in a top-level system list, a message, or nested in a `tool_result` -- with a 400 naming the image's path and the route, never the data, on both POST paths, with no backend contacted (`Scripts/llm-router.py:_rt_first_image`). Refused rather than stripped, the principle of [[0017-a-silent-zero-is-the-defect]]: a route that declares it cannot see images must not answer as if no image had been sent.

**The estimate ignores image bytes.** The local token estimate (mistral, codex, openai) empties each translated image part's `image_url` before measuring and charges a fixed 1500 tokens' worth of bytes per image (`Scripts/llm-router.py:_RT_IMAGE_TOKEN_ESTIMATE`) instead. The charge passes through `count_divisor` like every other byte, so it is 1500 tokens only at the default divisor 4 and about 750 at 8 -- declared, not corrected. Cases A50, B32, D17, E15, F26-F29 and K27.

### The client's effort (af3ca10)

**What was measured.** Claude Code 2.1.299, captured on loopback on 2026-10-27, sends `thinking` `{type: adaptive, display: omitted}` with no `budget_tokens`, plus `output_config.effort` in `low`, `medium`, `high`, `xhigh` or `max` (default `high`), the same for passthrough, mistral and codex model names; with `MAX_THINKING_TOKENS=0` the `thinking` key is absent while `output_config.effort` stays. The router ignored `output_config` and so sent `medium` for every such request.

**The precedence now** (`Scripts/llm-router.py:_rt_rs_effort`, shared by mistral, codex and openai): the route's fixed `reasoning_effort` option; then, only while thinking is `adaptive` or `enabled`, the client's `output_config.effort` when it is one of the five levels; then an `enabled` budget through the effort bands; then `adaptive` gives `medium`. Absent or disabled thinking gives no effort whatever `output_config` says. An `output_config` that is not an object, or an effort outside the set, is ignored and the next rule decides -- never a 400 -- so a level a newer client adds is harmless. `max`, which the Responses API lacks, is clamped to `xhigh` when the Responses body is built, not in the shared function; on mistral it goes through `reasoning_effort_map` and the learned remap like any other effort. passthrough and llamacpp compute no effort and forward `output_config` as is. The capture is recorded in the code's comments only, not as a fixture. Cases F30, F31, K28 and K29.

### Cases

The `llm_router` suite moved 400 (75909bc), 404 (af3ca10), 425 (5448fca), 430 (Phase 3, group S, S1-S5), 443 (Phase 2, group R, R1-R13), 453 (Phase 4, group T, T1-T10), 454 (T11) and 456 (T12, T13); the account-id cases were added inside S4. Groups R, S and T run before I and J. Every count above is the commit's own; the live one is declared once in `tests/run.py:SUITES`.

### Declared limits added

- sd-server has no authentication; the router's bearer protects only the router, and the server must be reachable through the router alone. The operator's LAN exposure is accepted for now (above).
- Prompt syntaxes of sd-server other than the extra-args block are not inspected; the sdcpp limits are measured at one sd.cpp commit and one model, and the source must be re-read when the build changes; `size: auto` on the live server and its request-size cap are unmeasured.
- The codex image endpoint is private and unstable; a successful answer carries no quota information and the quota-exhausted error shape is unseen. The codex image stream is not relayed.
- A streamed image answer can hold four to five times its largest line per stream, times `--max-image-requests`; there is no total stream deadline and no cumulative client-write budget.
- An image's token charge is a fixed figure scaled by `count_divisor`.
- The account id is not pinned when shorter than eight characters.

### Not verified

- No OpenAI Images API call has succeeded: the API-key account used had no credits. Whether api.openai.com accepts the JSON edit form (OQ-1) remains open, and how its multipart parser reads an odd body is unknown.
- The live SSE event names, payload fields and sizes of a streamed image answer are the documented ones; none has been observed.
- On codex: a multipart edit, `output_format` `jpeg`, the quota-exhausted error and any stream.
- Live acceptance of the list-form `function_call_output` by codex and openai-oauth.
- What sd-server does with a LoRA path that is not a model file (OQ-10).

## Addendum (2026-10-28): Live findings on the codex image route: output_format, multipart edits, size and quality

The codex image route (the ChatGPT backend's private images endpoints, reached through a codex backend with modality image) was probed live on 2026-10-27 and 2026-10-28 through the running router on the `gpt-image-2` route. The earlier addendum recorded the first measurement; this one records what the follow-up probes found and what the router now does about it.

### Measured

- A generation asked for `output_format` jpeg answered 200 with a PNG (magic 89504E47) and the answer echoed `output_format` png: the backend ignores the field.
- A hand-built multipart `/v1/images/edits` (fields model, prompt, quality, background and an `image[]` file part) was rejected upstream with a 400; the router had relayed only the fixed text `backend codex answered 400`. The JSON edit form (`images[].image_url` data URLs) is the working one.
- A sibling session (ai-soul) measured that `size` and `quality` are ignored as well: a request for 1024x1024 at quality high answered 200 with a 1370x1148 PNG, the answer echoing quality low and size 1370x1148. `auto` values resolve the same way (quality low, size 1254x1254 in the first probe).

### Decisions

- `output_format` other than png is a router 400 on codex image routes (rule-only message `this backend always answers png`), in the codex adapter's image_request guard, as the sdcpp kind refuses webp: a silently ignored format is a defect because the client decodes by what it asked for. Absent or png pass.
- Multipart edits are a router 400 on codex image routes (`multipart edits are not supported on this route; send the JSON form`), through a new adapter attribute `IMAGE_EDIT_MULTIPART` (default true, false on codex only, pinned by J42) checked next to `IMAGE_EDIT_JSON` in the image parser; the check follows the stream check so a streaming request keeps its own refusal.
- `size` and `quality` are NOT refused. The alternative, refusing them as the sdcpp kind refuses a bad size, would break the one known client (ai-soul sends a size enum and quality auto to every image route) for a difference in the result, not in correctness; the limit is declared instead: a client reads the answer's size, quality and output_format echoes and decodes by the bytes.
- The shared upstream error extraction now also reads a top-level string `detail` (the ChatGPT backend's probable error shape) for the relayed statuses, with the same scrub, secret strip, control-character filter and 300-character cut; a list or dict `detail` keeps the fixed text; 401, 403 and 5xx are unchanged.

### Declared limits added

- The codex image backend picks the image size and quality itself and ignores output_format; it is private, unstable and not available on Free plans, and its quota-exhausted error shape has not been seen.
- Multipart edits on codex are refused at the router on the strength of one upstream 400 whose reason was not visible; if the backend ever accepts them, the flag is one attribute.

### Tests

Group S cases S2 (output_format guard, multipart refusal, byte-exact relay of png and absent) and S4 (the `detail` extraction and its scrubbing), the J42 pin on `IMAGE_EDIT_MULTIPART`, and one adjustment of T1 (a multipart edit on codex is now refused before it can be streamed). The llm_router suite stays at 456 cases.

## Addendum (2026-10-28): Advertised image sizes: the declarative sizes and default_size route keys

A client of an image route, and a model choosing the call's arguments, had no way to learn which resolutions a route is meant for, so it guessed. The guess matters on two of the three image kinds: an sd.cpp model is trained for a few specific resolutions and degrades away from them, and the codex backend (`gpt-image-2`) ignores `size` and picks the image size itself (the 2026-10-28 addendum), so the only honest statement there is `auto`.

### Decisions

- Two optional route keys, `sizes` and `default_size`, allowed on a `modality` image route only (kinds openai, sdcpp and codex); a chat route and the `default` route refuse either by path (`only allowed on a modality image route`).
- `sizes` is a non-empty list of at most 32 unique strings (`_RT_SIZES_LIMIT`), each `auto` or `WIDTHxHEIGHT` matched whole against `[1-9][0-9]{1,4}x[1-9][0-9]{1,4}` (`_RT_SIZE_RE` through `fullmatch`, so a trailing newline is no size): two to five digits per side, no leading zero, a lower-case `x`. `default_size` is one string by the same grammar, a member of `sizes` when both are set, and allowed alone. A refusal names the key and the rule and never echoes the value (`_rt_check_sizes`, `_rt_size_ok`, `_rt_cfg_route`).
- Both are advertised by `GET /v1/models` and `GET /v1/models/{id}`, after `context_window`, only when configured: the key is absent, not null, otherwise (`_rt_model_entry`). The `?modality` filter is unchanged.
- Both are declarative only. Nothing reads them on the request path: a `size` outside `sizes` still reaches the upstream, and a request without `size` is not given `default_size`, so the image body stays the client's bytes, byte for byte, as the image-endpoints addendum requires. The request's own `size` keeps its existing checks (the shared vocabulary, and `max_pixels` on sdcpp).

### Alternatives rejected

- Enforcing the list (a 400 for an unlisted size). Rejected for the reason the 2026-10-28 addendum keeps `size` unrefused on codex: the one known client sends one size enum to every image route, and a refusal would break it for a difference in the result, not in correctness.
- Injecting `default_size` into a request without `size`. Rejected: it would end the byte-exact relay of the image body, the property every image kind's tests pin, and the upstream already has a default of its own (sd-server's operator-set default, codex's own choice).
- One free-form string or a WxH-only grammar. Rejected: a free-form value cannot be checked, and `auto` must be expressible because it is the accurate entry for codex.

### Declared limits added

- `sizes` and `default_size` are advertised only, never enforced and never injected; the list is the operator's statement and is not measured against the model, and whether a given client reads the two keys is unmeasured.

### Tests

Group U, three cases, run after T and before I and J: U1 the config rules in-process (accepted on each image kind, the cap, `default_size` alone; every malformed shape, a `default_size` outside `sizes`, a chat and the default route refused without echoing the value), U2 the models entry keys and their order under each `?modality` filter, U3 a live `openai` router relaying a size outside the list and a body with no size byte-identical to the peer and showing the keys over HTTP. The llm_router suite moved from 456 to 459 cases; the live count is declared once in `tests/run.py:SUITES`.

## Addendum (2026-10-28): Masks on the sdcpp kind: a live inpaint measurement, and where the router's sd.cpp integration stops

The sdcpp edit path forwards the client's multipart byte for byte (`Scripts/llm-router.py:SdcppAdapter`, `image_request` returns `inbound.raw`), so whatever parts the client sends, a `mask` among them, reach sd-server unchanged. Whether sd-server's OpenAI edit route honours that mask had never been measured. It was measured on 2026-10-28, directly against one sd-server (`http://192.168.2.245:8082`, `flux-2-klein-9b-Q8_0`, a Qwen3-8B text encoder, the `flux2_ae` VAE, 4 steps, euler, seed 42; the build's commit was not recorded), with the router not involved: a 512 x 512 source (a grey gradient, a red circle, a blue square), a one-channel mask white over the circle (radius 100), and the prompt "a green star". The probe script and its images were kept in the untracked scratch directory `.claude/tmp/sdmask/` (`probe.py`, `source.png`, `mask.png`, `out_*.png`), so they are not durable evidence: the numbers below are this addendum's record, and a re-run is the only check of them.

### Measurements

Mean absolute RGB error (0-255) against the source.

| Case | Request | Outside the mask | Blue square | Verdict |
|---|---|---|---|---|
| A | `POST /v1/images/edits`, multipart `image[]` + `mask`; HTTP 200 in 3.0 s | 48.6 (72.3 inside) | 103.3 | damaged: a flat khaki-grey, the gradient and the square lost, a faint green blob in the mask |
| B | A without `mask` (control) | 34.5 | 128.7 | redrawn from scratch: the upload is only a loose reference (edit models take it as a ref image) |
| C | `POST /sdapi/v1/img2img` JSON, `init_images[]` + `mask`, `denoising_strength` 1.0 | 3.3 | 3.9 | a correct inpaint; at 0.75, 3.5 outside but only a partial star; the answer is `{images: [raw base64], info, parameters}` |
| D | A plus `<sd_cpp_extra_args>{"init_image": "<raw base64 PNG>", "strength": 1.0}</sd_cpp_extra_args>` in the prompt | 4.2 | 4.1 | a correct inpaint; the server strips the tag from the prompt (`generation_info.prompt` is "a green star", `generation_info.strength` 1.0) |
| E | `POST /sdcpp/v1/img_gen`, asynchronous (202 `{id, poll_url, status}`, then `GET /sdcpp/v1/jobs/{id}`, the image at `result.images[0].b64_json`), body `{prompt, init_image, mask_image, strength, width, height, batch_count}` in raw base64 | 4.1 | not recorded | as D |

Measured: a mask on `/v1/images/edits` without an `init_image` does not preserve the upload. The server answered in about 1.7-3 s per request on this build, direct; the 2026-10-27 figures in the Phases 2-4 addendum (2.7 s for a generation, 5.3 s for an edit) went through the router and stand as measured then.

### Decisions

- The router will not generate masks and will not decode images. Mask generation is the client's concern, or an MCP tool's; the router stays a relay of the client's image bytes, as the image-endpoints addendum requires.
- The sd.cpp integration inside llm-router stops at roughly its current state. A separate, future `sd-router.py` is to handle sd.cpp properly; it is not being built now. The input it starts from is the three routes that inpaint correctly above: C (`/sdapi/v1/img2img`), D (`init_image` through the extra-args block on `/v1/images/edits`) and E (`/sdcpp/v1/img_gen` with its job poll).
- No router change was made. The sdcpp edit path keeps forwarding the multipart byte for byte, so a mask part on an sdcpp `/edits` request still reaches sd-server and gives case A.

### Declared limits added

- A mask on an sdcpp edit is unsafe: it is forwarded, neither refused nor rewritten, and the answer does not preserve the uploaded image (case A).
- None of the three working routes is reachable through the router: D needs the `sd_cpp_extra_args` tag, which the router's prompt rule refuses, and C and E are upstream paths outside the adapter's fixed `IMAGE_PATHS`. Inpainting through the router is therefore not available on sdcpp.
- Even a correct inpaint (C, D, E) leaves an error of about 3-4 outside the mask; pixel-exact preservation of the unmasked area needs compositing by the client.
- The measurement is one server, one build whose commit was not recorded, one model, one seed and one source image; its scripts and images are not committed.

### Unverified

- The cause of case A is inferred from a source read and was not measured: sd.cpp's OpenAI edit route (`routes_openai.cpp`) puts the uploads in `ref_images` and never in `init_image`, and the diffusion engine's `generate_init_latent` then returns zeros.
- That the 3-4 residual outside the mask is the VAE round trip is inferred, not measured.
- Read in the sd.cpp source and not run: white regenerates and black keeps; the mask is one channel; dedicated 9-channel inpaint, FLUX Fill and Flex.2 checkpoints get real mask conditioning, and every other model only a latent-blend mask; there is no native outpainting (the client pads the image and the mask, the new border white); and edit models (Kontext, Qwen-Image-Edit, FLUX.2 Klein) use reference images without a mask.

## Addendum (2026-10-28): sd-router.py is built; kind sdcpp accepts an api_key as a bearer

Two statements of this ADR no longer hold. The sentence that a backend of kind sdcpp refuses api_key and any auth_header but none is superseded: an sdcpp backend now accepts an optional api_key, sent as `Authorization: Bearer <key>` (auth_header omitted or authorization; none or x-api-key together with a key is a configuration error naming the backend's auth_header; a key over a non-loopback http:// base_url stays refused by the existing rule; the key never reaches a log, a repr or an echoed upstream error). The sentence that Scripts/sd-router.py is not being built now is superseded: the supervisor and reverse proxy in front of sd-server exists, and its decision, alternatives, deviations and declared limits are recorded in [[0034-sd-router-a-supervisor-and-proxy-for-sd-server]]; the operational description is in [[sd-router]].
