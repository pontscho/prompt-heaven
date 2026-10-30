---
name: 0035-one-gpu-several-apps
type: adr
status: active
title: gpu-router -- one GPU, several apps, a streamed llm kind and llm-router's raw relay
description: Decision to generalise sd-router into gpu-router, a supervisor for one GPU's APPS -- an app is one child process serving one or more model ids, of kind sd (sd-server, buffered) or llm (inferNO-style, streamed) -- with an edge-only model-id index, one kind table, a readiness probe path instead of log parsing, an abort matrix in one pure function under which a client abort closes the upstream and kills nothing (D-12 amended), and a strict streamed-head allow-list; and to give llm-router a rawllm kind that relays /v1/chat/completions, /v1/embeddings, /v1/rerank and /v1/messages byte-exact to a fixed upstream path under per-endpoint body caps and its own in-flight cap; with the stage structure, why ADR 0034 stays active, the alternatives rejected, the measurements, every implementation deviation, the owner-unconfirmed defaults, the threat table outcomes, the declared limits, the code-mode security review of the rawllm diff (APPROVE, one LOW fixed red first) and the owner-only items left open.
sources:
  - Scripts/gpu-router.py
  - Scripts/gpu-router.py:_SD_KIND_DEFAULTS
  - Scripts/gpu-router.py:_sd_abort_plan
  - Scripts/gpu-router.py:_SdStream
  - Scripts/gpu-router.py:_sd_parse_stream_head
  - Scripts/gpu-router.py:_send_failed
  - Scripts/llm-router.py:RawllmAdapter
  - Scripts/llm-router.py:RawSseRelay
  - Scripts/llm-router.py:_rt_parse_raw_inbound
  - Scripts/llm-router.py:_rt_refuse_case_variants
  - tests/test_gpu_router.py
  - tests/files/gpu_router/tf_stub_llm.py
  - tests/test_llm_router.py
verified:
  commit: f43a052
  date: 2026-10-30
links:
  - 0034-sd-router-a-supervisor-and-proxy-for-sd-server
  - 0028-route-by-model-translate-at-the-edge
  - 0029-the-http-front-is-a-domain
  - 0024-pure-python-39-and-the-stdlib
  - 0015-ambiguity-is-the-defect
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0032-a-runner-behind-a-proxy
  - gpu-router
  - llm-router
  - generated-regions
  - scripts
  - tests
---

# ADR 0035: gpu-router -- one GPU, several apps, a streamed llm kind and llm-router's raw relay

Status: accepted 2026-10-30, recording code already committed on master:
`bde9876` (stage 1, the rename), `ef2ea0d` (stage 2a, the llm kind over a
buffered path), `da9eb1e` (stage 2b, streaming and the abort matrix) and
`f43a052` (stage 3, llm-router's `rawllm` kind). It generalises and partly
supersedes [[0034-sd-router-a-supervisor-and-proxy-for-sd-server]]: everything
that ADR decided for the sd kind stands except D-12 (a client abort kills the
child), which this ADR amends. The requirement is Zoltán's spec (roadmap item
R-0112, the spec and the plan were kept outside `docs/` and are not
committed). Every number below is a measurement or a count at the moment of the
decision. The component pages are [[gpu-router]] and [[llm-router]].

## Context: one GPU, two apps that never run at once

The host `para` has one GPU and two applications for it: stable-diffusion.cpp
`sd-server` (images) and inferNO (an LLM, embedding and reranker engine; one
process serving `Qwen3.8-Flash-Next`, `bge-m3` and `bge-reranker-v2-m3`).
sd-router (ADR 0034) knew only sd-server and buffered image routes, so inferNO
traffic could not pass it. Loading is expensive (inferNO up to about a minute
cold, about 20 s warm, by the owner's statement), so a needless restart is the
cost to avoid. The owner wants both wired into the live ai-soul Safranek through
llm-router, with one place that serialises requests and starts and stops the
right app.

## Decision

### Stages

Four commits, each red first and gated on its own suite: (1) a pure rename of
sd-router to `Scripts/gpu-router.py` with tests, fixtures,
registries, the forge target and the wiki repoint, changing only externally
visible name strings (logger, stderr prefix, `prog`, `server_version`,
`owned_by`, refusal text); (2a) config, app identity, readiness and an
aggregated `/v1/models` over the BUFFERED path, so every identity and slow-bind
risk landed before any streaming (2a was not to be deployed for chat); (2b) the
stream seam and the no-kill abort; (3) llm-router's inbound OpenAI endpoints and
the raw-relay kind, threat-table first, with its own security review. Internal
`_Sd*` / `_sd_*` names were kept: the HTTP-front adaptation table and the static
gates pin them, and a rename mixed with behaviour hides the red run.

### Apps are ModelSpecs; a model-id index resolves at the edges only

The `models` config key now declares APPS, one child process each
`Scripts/gpu-router.py:ModelSpec`. New per-app keys, all defaulted so an
existing sd config loads byte-compatibly: `kind` (`sd` default, `llm`),
`model_ids`, `ready_path`, `ready_status`, `head_timeout`, `idle_timeout`
(kind llm only) and `child_port` `Scripts/gpu-router.py:_sd_cfg_model`. The
scheduler, its state dicts and the pure `decide` still key on ONE name per
child, the app name; a model id (or app name) to app index is built and checked
at load `Scripts/gpu-router.py:_sd_cfg_index` and read only by routing, unload
and `/v1/models` `Scripts/gpu-router.py:_sd_pipeline`. A load-time refusal
keeps it unambiguous: an id served by two apps, or an id equal to ANOTHER app's
name. `GET /v1/models` lists every configured id of every app from config,
never by discovery and never starting anything, with `owned_by: gpu-router` and
the kind's modality `Scripts/gpu-router.py:_sd_handle_models`. Unload names an
app through any of its ids `Scripts/gpu-router.py:_sd_handle_unload`.

### One kind table

Everything that differs between `sd` and `llm` is one row of
`Scripts/gpu-router.py:_SD_KIND_DEFAULTS` (`_SdKindRow`): modality, the
`ROUTES` kind tokens the kind accepts, whether ids are required, the
`start_timeout` / `request_timeout` defaults and the `request_timeout` ceiling,
whether `head_timeout` / `idle_timeout` exist and their defaults, the readiness
path and status, whether a timeout kills, and whether the answer streams. The
four inferNO POST routes `/v1/chat/completions`, `/v1/messages`,
`/v1/embeddings`, `/v1/rerank` joined `Scripts/gpu-router.py:ROUTES` with the
new tokens `chat` and `embed`; `/v1/models` stayed a control route, so gate A10
(one path-keyed table) is unedited. A route of the other kind is 404
`route_not_found` `Scripts/gpu-router.py:_sd_kind_accepts`. Per-token body caps
(16 MiB chat/messages, 4 MiB embeddings/rerank, effective `min(cap,
body_limit)`) are a table keyed by kind token, never by path
`Scripts/gpu-router.py:_SD_KIND_BODY_CAPS`. The chat token alone forwards
`anthropic-version` and `anthropic-beta`, once each, 1-256 printable ASCII
characters `Scripts/gpu-router.py:_SD_KIND_FORWARD`.

### Readiness by HTTP probe, not by a log line

The probe GETs the app's `ready_path` (llm default `/v1/models`, ready on 200
only; sd unchanged: `/`, any status but 503)
`Scripts/gpu-router.py:_sd_probe_status`, and liveness of OUR child is
re-checked after the answer, so a foreign listener answering for an exited
child is not ready `Scripts/gpu-router.py:wait_ready`. The child's liveness is
also checked before every send `Scripts/gpu-router.py:_exec_send`. inferNO does
not listen while it loads (owner statement), so a refused connect keeps the
probe polling. With a fixed `child_port` there is no port pick and no collision
retry: one spawn, an early exit is a failed start
`Scripts/gpu-router.py:launch`.

The spec offered a `[serve] Ready` log line as a second signal. It is NOT
implemented: reading it would need the child's stdio captured instead of
`DEVNULL`, a drain thread (a dead reader deadlocks a full pipe) and would strain
gate A3 (exactly one `Popen`). Revisit only if the probe proves insufficient on
`para`.

### Streaming as a ticket variant, one owner of the slot

Only kind llm streams, and only a 2xx that is `text/event-stream` or has no
`Content-Length` `Scripts/gpu-router.py:_sd_head_streams`; a JSON answer with a
length, and every non-2xx, stays on the buffered path under `response_limit`,
so the closed error table is unchanged. The scheduler thread keeps pumping the
upstream into a bounded byte queue `Scripts/gpu-router.py:_SdStream` and calls
`_finish` at the end; the handler is released by a head-ready Event that is not
`done`, so `_sd_settle` still cancels a ticket whose client leaves mid-stream.
The handler thread alone writes the client socket, chunked for an HTTP/1.1
client, and polls for a departed client between pieces
`Scripts/gpu-router.py:_sd_write_stream`. A chunked child answer is decoded by a
strict incremental de-chunker `Scripts/gpu-router.py:_SdDechunker`. An abnormal
end (the child drops, an idle abort, a force unload, shutdown) closes WITHOUT the
terminating chunk, so a truncated answer cannot read as complete.

The streamed head is an ALLOW-list `Scripts/gpu-router.py:_sd_parse_stream_head`:
only the child's validated `Content-Type` crosses; `Cache-Control: no-cache`,
`X-Content-Type-Options: nosniff` and `Connection: close` are the router's;
1xx/3xx, a non-identity `Content-Encoding`, `Content-Length` with
`Transfer-Encoding`, a repeated framing header and any malformed or control-byte
header line are refused; `Set-Cookie`, `Location`, `Trailer`, `Upgrade` and
every other header are dropped. The head must fit in
`Scripts/gpu-router.py:_SD_HEAD_LIMIT` (64 KiB).

### The abort matrix, and D-12 amended

What a failed or aborted send does is one pure function, one row per reason x
kind x bytes relayed `Scripts/gpu-router.py:_sd_abort_plan`, and the scheduler
holds no policy of its own `Scripts/gpu-router.py:_send_failed`:

| Reason | kind sd | kind llm |
|---|---|---|
| client abort | close the upstream, no ladder, no charge, the child stays READY | same |
| timeout | ladder + charge + 504 `upstream_timeout` (unchanged) | close the upstream, no ladder, no charge; 504 before bytes, a truncated stream after |
| force | ladder, 503 `unload_forced` | ladder, the stream truncated |
| shutdown | ladder | ladder |
| crash, nothing relayed | ladder, one charge, the same ticket re-queued while `_sd_may_retry` allows | same |
| crash, bytes relayed | n/a (buffered) | never re-queued, truncated, one charge, ladder |

D-12 of ADR 0034 (a client abort kills the child) is amended for both kinds: the
owner states that sd-server drops the connection when the client leaves, and a
kill throws away a 20-60 s load. The sd half sits behind one switch,
`Scripts/gpu-router.py:_SD_ABORT_KILLS_SD`, `False`, so a `para` measurement
can flip one value. The llm kind's `request_timeout`, `head_timeout` and
`idle_timeout` abort without killing (owner decision); the sd kind keeps
kill-on-timeout.

### llm-router: a rawllm kind that relays raw

llm-router gained inbound `POST /v1/chat/completions`, `/v1/embeddings` and
`/v1/rerank` and one new kind row, `rawllm`, in its single kind registry
`Scripts/llm-router.py:RawllmAdapter`. No translation, ever
([[0028-route-by-model-translate-at-the-edge]]): the client's bytes go to a
fixed upstream path per endpoint, never derived from client input, a 2xx JSON
object comes back untouched, an SSE answer as whole events byte for byte
`Scripts/llm-router.py:RawSseRelay`. `/v1/messages` to a rawllm route enters
through the Anthropic parser for validation and is relayed raw by the same
adapter. The new endpoints are validated by a sibling parser that runs the
strict JSON loader for validation only and keeps the raw bytes
`Scripts/llm-router.py:_rt_parse_raw_inbound`. A separate in-flight cap,
`--max-llm-requests` (default 8, range 1-64), keyed on the BACKEND's kind so
`/v1/messages` to a rawllm route is counted too, answers 429 with
`Retry-After: 1` before any upstream contact: gpu-router queues without a wait
limit, so an uncapped relay would hand every connection slot to the GPU queue.
Swap logic stays out of llm-router (owner decision).

### ADR 0034 stays active

The owner asked for ADR 0034 to be "marked superseded". It already is a frozen
record (`type: adr` + `status: active` is the frozen pair, so its body anchors
are advisory). `deprecated` would label a design this ADR only PARTLY supersedes
as gone in `INDEX.md`; `superseded` is not in the documented status enum, no
code refuses it, and it matches neither frozen pair, so it would silently
unfreeze the ADR and its body anchors naming the old sd-router file would start
gating. The supersession is therefore carried by this ADR's links and one dated
addendum on ADR 0034 through `addendum.py`. The component page was moved in
stage 1 (`git mv`, history kept) so no commit left the wiki gate red.

## Alternatives rejected

1. A second `apps` tree beside `models`: two namespaces, every state dict
   doubled, app-name versus model-id confusion more likely.
2. Keying the state machine by model id, or a separate `LlmApp` hierarchy or
   second supervisor: three ids share one inferNO process and would
   double-start it; it duplicates the budget, ladder and early-exit logic the
   D/S/Q groups and gate A3 pin.
3. Buffering LLM answers: a 20-minute chat would sit in memory and show the
   client nothing.
4. A separate streaming thread with its own slot accounting: two owners of the
   in-flight slot is the lifecycle bug the design exists to avoid.
5. `[serve] Ready` log parsing, as primary or at all (above).
6. Killing the child on an LLM idle or head timeout: a kill throws away a load
   (owner decision).
7. Swap logic in llm-router, or one router per app (spec Alternative A, owner
   decision).
8. Renaming the internal `_Sd*` symbols, or renaming in the same step as a
   behaviour change.

## Measurements

- Suite `gpu_router`, typed in the `tests/run.py` ledger: 211 when it landed
  (`91ff40b`), 220 after the listener options (`ea5bdc8`, never mirrored into
  the forge description until stage 1 fixed the drift), 220 kept by the rename,
  239 after stage 2a, 251 after stage 2b, 252 after stage 3 (A 18, L 45, R 23,
  D 26, S 22, Q-sched 16, Q-http 6, T 17, P 46, U 21, G 6, E2E 6); about
  124.6 s measured, forge timeout 300.
- Suite `llm_router`: 460 to 475, group V of 15 cases (A 50, B 32, C 16, D 17,
  E 15, F 31, G 23, H 27, I 16, J 44, K 29, L 20, M 23, N 42, O 18, P 4, Q 18,
  R 14, S 5, T 13, U 3, V 15); about 206 s measured, forge timeout 480.
- Not measured: whether sd-server stops generating when its client closes
  (A-2), whether inferNO stops on a closed connection (A-1), and any live run on
  `para`. Every inferNO fact used is the owner's statement; the stub child
  `tests/files/gpu_router/tf_stub_llm.py` models them.

## Implementation deviations from the plan

gpu-router:

1. Gate A1 (no clock, socket, subprocess or threading names where they do not
   belong) was relaxed narrowly: a literal `GET`, and as path either a literal
   or the `path` parameter of `_sd_probe_status`, so the probe can take the
   configured `ready_path` `tests/test_gpu_router.py:GA_CASES`.
2. The routing pure functions keep their shape; the app-name resolution and the
   kind check live in `_sd_pipeline`, not in `_sd_route_head` /
   `route_request`.
3. A liveness check runs before EVERY send, not only before the first one after
   readiness (R4).
4. The llm `request_timeout`, `head_timeout` and `idle_timeout` ceilings are
   604800 s `Scripts/gpu-router.py:_SD_DEADLINE_CAP`; sd keeps (1, 7200].
5. `head_timeout` / `idle_timeout` on a kind sd app are refused, not ignored.
6. An app's `model_ids` may repeat its own name.
7. Not every llm 2xx streams: only `text/event-stream` or an answer without a
   `Content-Length`; a JSON answer with a length stays buffered under
   `response_limit`.
8. `bytes_relayed` is set when the head is published, before the first body
   byte reaches the client `Scripts/gpu-router.py:_publish_stream`: from then on
   a ticket is never re-queued.
9. `AbortPlan.answer` is not read by the scheduler: the answer codes are spelled
   out at the call sites because gate A9 reads the closed table against those
   calls; D23 pins that the two agree.
10. The 2b scratch drafts file the plan named never existed: the red set was
    written directly into the suite at Step 12.
11. `Cache-Control: no-cache` is fixed on every stream.
12. The strict de-chunker takes at most 12 hex digits per size line
    `Scripts/gpu-router.py:_SD_CHUNK_DIGITS` and refuses any trailer.
13. The stream queue holds `Scripts/gpu-router.py:_SD_STREAM_CAP` (256 KiB)
    before the pump stops reading the child; the head limit is 64 KiB.
14. The per-kind token body caps apply as `min(cap, body_limit)`.
15. The image-worded refusal texts were generalised ("a route takes POST only",
    "too many requests queued; retry shortly"); the codes are unchanged (R23).

llm-router:

16. On a rawllm route the route key must equal its `model` (the body is never
    rewritten), and a rawllm route is refused as the default route or with
    options `Scripts/llm-router.py:_rt_cfg_route`.
17. `anthropic-*` values are 1-256 printable ASCII characters, single-valued,
    forwarded on `/v1/messages` only.
18. `Cache-Control: no-store` on raw JSON answers
    `Scripts/llm-router.py:_relay_raw`.
19. An SSE answer ends at `data: [DONE]` (chat) or `event: message_stop`
    (messages); nothing after the terminator is relayed; an upstream EOF before
    it yields one in-band error event; comment lines are dropped; an upstream
    `event: error` is replaced by a fixed error event.
20. A 2xx JSON answer must start with `{` after whitespace -- a first-byte check,
    not a parse -- else 502; any 2xx is answered 200.
21. 2xx JSON and SSE content are not scrubbed; only relayed error text is.
22. `/v1/messages` to a rawllm route takes an in-flight slot; `count_tokens`
    takes none and is refused 400 (the upstream is not asked, nothing is
    estimated).

## Owner-unconfirmed defaults

Each is the plan's or the agent's number or name, not the owner's; each changes
by editing one value.

- `child_port` as an optional per-app key.
- The llm `ready_path` default `/v1/models`, ready on HTTP 200 only.
- The llm `start_timeout` default 120 s.
- The llm deadline defaults: head 1800 s, idle 1800 s, request 86400 s (the
  owner said "a big default"; the numbers are a suggestion).
- The kind name `rawllm` and the constant name `_SD_ABORT_KILLS_SD`.
- The body caps 16 MiB (chat, messages on gpu-router) and 4 MiB (embeddings,
  rerank), on both routers. They can cut a large embedding batch or a
  base64-image vision chat.
- The fixed per-endpoint `Content-Type` and router-fixed `Cache-Control` on both
  routers' raw relays, and llm-router buffering JSON answers under its 64 MiB
  `_UPSTREAM_BODY_LIMIT`.
- `--max-llm-requests` default 8, range 1-64.
- Unload across apps via any model id, and "no target = the resident app" (the
  spec's own agent default).
- The neutral refusal wording (deviation 15).

## Threat table outcomes

Each row of the plan's risk table has a named case or is declared below.

| Risk | Outcome |
|---|---|
| R1 a client leaves mid-stream, slot held | `done` only at `_finish`; QH6 |
| R2 an abort kills a healthy load | the matrix; QS8 and QH2, which pinned D-12's kill, now pin no kill; A18 (a stream aborts, never kills) |
| R3 a mid-stream EOF replayed | P46 |
| R4 slow bind, a squatter on the picked port | S21, S22, T14 |
| R5 app name versus model id | L41, U20 |
| R6 the 64 KiB sniff window | the prefix wins; declared (A-5) |
| R7 header smuggling to the child | P43; llm-router V8 |
| R8 SSRF through the raw relay | fixed `RAW_PATHS`; V7, J44 |
| R9 a bearer or key leak | V7, V14; E6 checks no `Authorization` at the child |
| R10 body-limit bypass, chunked smuggling | P42; V3 (411 before 413) |
| R11 unbounded stream memory | D26 (queue level only, see limits) |
| R12 de-chunking errors | D24 |
| R13 `count_tokens` behind the raw relay | refused 400; V13 |
| R14 force unload mid-stream | U21 |
| R15 shutdown mid-stream | T16 |
| R16 inferNO ignores a closed connection | declared (A-1) |
| R17 count drift 220 vs 211 | fixed in stage 1 |
| R20 response splitting through the streamed head | D25, P44 |
| R21 upstream head junk through the raw relay | V1 (the first red case of group V) |
| R22 a truncated second hop read as complete | V2 |
| R23 queued GPU requests starve llm-router connections | V6 |

## Declared limits

- **S3 (LOW): one client can hold the GPU** up to `request_timeout` (86400 s
  for kind llm) and the queue has no wait limit (both owner decisions). The
  escape hatch is `?force=1`, which kills the child and truncates the stream. A
  long stream blocks embeddings (accepted by the owner).
- **S4 (INFO): the children listen unauthenticated on loopback**, reachable by
  any same-uid process (the same-uid trust model of ADR 0034 and
  [[0032-a-runner-behind-a-proxy]]); `child_port` makes the port predictable,
  not more reachable.
- **A-1: inferNO stops its work on a closed connection** (owner statement,
  unmeasured). If false, a client abort leaves the GPU busy and the router cannot
  see it.
- **A-2: whether sd-server stops generating on a closed connection is
  unmeasured** (ADR 0034 assumed it does not). After a no-kill sd abort the slot
  is freed at once, so the next request can reach a busy GPU; the
  `--keep-generating` stub case pins the pessimistic case.
- **A-5: the 64 KiB sniff window.** A chat body whose `model` lies beyond it
  needs the `/m/<x>/` prefix; llm-router always sends it.
- **inferNO loads silently**; nothing reports progress while the probe polls.
- **The port-pick window** stays without `child_port`: an exit after
  `_SD_EARLY_EXIT_S` is a charged crash, never a collision.
- **Queue memory** is bounded by count, not by wait: `max_connections` x the
  route's body cap; llm-router's `--max-llm-requests` is the practical bound on
  the llm path.
- **Untested paths:** no live test for a slow reader (R11 is pinned at the
  queue level only, D26), for the handler's write-timeout path, or for the
  HTTP/1.0 close-delimited branch of the stream writer.
- **llm-router, buffered JSON path:** no client-gone detection and no total
  wall-clock deadline (idle_timeout is per read, so a rawllm backend in front of
  a gpu-router needs an idle_timeout above the longest queue wait it should
  survive).
- **llm-router, relayed 4xx error text** is scrubbed by a pattern-based filter:
  a partial or encoded key echo, or a private LAN name, could pass.
- **llm-router parses up to 16 MiB of JSON before the slot is taken**, the same
  exposure class as the existing body limit.
- **A hostile upstream is outside the model**: the backend is operator-configured.

## Security review (2026-10-30, code mode)

A code-mode review ran over the llm-router rawllm diff, triaged in three lanes
(parseval, ssrf-net, access-control). It produced 11 deduplicated candidates:
one VERIFIED LOW and ten SUPPRESSED as INFO or LOW. Verdict APPROVE.

- **F1 (LOW, CWE-444), fixed red first.** A case-variant key (`"Model"`,
  `"STREAM"`) on `/v1/messages` to a rawllm route: the router reads the exact
  lower-case keys while the raw bytes go upstream, whose reader may fold case,
  so the two could read two different requests. Such a key is now refused 400,
  shared with the raw endpoints' parser
  `Scripts/llm-router.py:_rt_refuse_case_variants`
  ([[0015-ambiguity-is-the-defect]]); pinned by V15, other kinds unchanged.
- The ten suppressed candidates are not itemised in this record.

## Owner-only items, not done here

- Re-copy the changed servers to `~/mcp` and restart the live processes.
- The `para` measurement: does sd-server stop generating on a client close (A-2).
- The live smoke on `para`, then ai-soul Safranek.
- Whether ai-soul or Safranek scrape the renamed strings (the `gpu-router:`
  stderr prefix, the logger name, `owned_by`).
- Production `child_port` values and sd-server's new loopback port, with
  gpu-router on :8082.

## Addendum (2026-10-30): Process and listener options move into the config of both routers

gpu-router gained the optional top-level keys ready_file, log_file and debug, and llm-router gained bind, port, allow_remote, allowed_origin, ready_file, log_file, debug, body_limit, max_connections, max_image_requests and max_llm_requests, all with the precedence explicit flag, then config key, then built-in default that commit ea5bdc8 set for the gpu-router listener keys. Four decisions came with it. A bad config value is refused at load even when a flag would override that key, so a broken file is caught at once. The llm-router bind rule (an IPv4 literal, non-loopback needs allow_remote) runs on the merged result, and the config key itself is only checked as a non-empty string at load. A command-line --allowed-origin replaces the config list instead of adding to it. Both routers now read and merge the config before logging is set up, so nothing logged while the config loads reaches --debug or --log-file; a config refusal is still the one-line exit-2 message on stderr. The llm-router OAuth write-back copies the whole document and replaces only backends.<name>.oauth, so the new keys survive it. Suites: gpu_router 252 to 255 (L46-L48), llm_router 475 to 479 (A51-A54); the old A9 unknown-key example body_limit became max_requests because body_limit is now valid. Code: Scripts/gpu-router.py:_sd_settings, Scripts/llm-router.py:_rt_settings and _rt_cfg_listener.

## Addendum (2026-10-30): Several listen addresses in one process

Both routers accept a list for the bind config key and a repeatable --bind, so one process listens on e.g. 127.0.0.1 and a LAN address on the same port. The reason is operational: loopback-pinned clients and LAN clients had to share one process, because two processes would race on the llm-router OAuth write-back. The rules run per address on the merged result (llm-router: IPv4 literal, a non-loopback address needs allow_remote, an unspecified or global one stays refused; gpu-router: a non-loopback address needs allow_remote and insecure_cleartext), duplicates are refused, a flag list replaces the config value whole, and several addresses need an explicit non-zero port (the alternative, the first bind picking an ephemeral port for the rest, could leave the ready file and the sockets disagreeing). One address behaves exactly as before. main builds one server per address (the first is the primary, the others take a peer argument) and each runs its own serve_forever thread; the generated HTTP-front regions are untouched, so mcp-proxy needs no regeneration. Peers share the router state (gpu-router: closing, started_epoch, active_cond and the single scheduler; llm-router: the image and raw-relay semaphores, closing, the upstream registry, the token store with its one OAuth writer, and the learned sampling and effort sets) and keep their own socket, loopback flag, active count and conn_sem. Declared consequences: max_connections is per listener, because gpu-router's G2 gate pins exactly one BoundedSemaphore assignment and because a flood on the LAN address then cannot starve 127.0.0.1, so total connections can reach N times the cap; gpu-router's memory worst case does not scale by N for request bodies (a body is read only after the scheduler's process-wide admission), only the small per-connection control bodies do, while llm-router's per-connection figure does scale. The Host-header rule follows the listening address: it applies to a loopback listener only and requires a Host naming that loopback listener, so on Linux a request to 127.0.0.2 with the default Host is refused 400 and with Host 127.0.0.2:<port> it passes; a LAN listener has no Host rule. A startup failure on any address closes the sockets already bound and exits 2; llm-router's socket-level message names host and port, an accepted exception to the no-echo rule of the config refusals. Suites: gpu_router 255 to 259 (L49, P47-P49), llm_router 479 to 483 (A55, B33-B35). Code: Scripts/gpu-router.py:_sd_cfg_bind and main, Scripts/llm-router.py:_rt_settings and main.

## Addendum (2026-10-30): Route modalities embedding and rerank on rawllm routes (behaviour change)

llm-router gains two route modalities, `embedding` and `rerank`, accepted on a `rawllm` route only (any other kind is refused with "kind X does not serve embedding routes"), refused on `default`, and subject to the existing rawllm rule that a route's model equals its key; `sizes` and `default_size` stay image-only. The reason: ai-soul's `GET /v1/models` listing showed the bge-m3 and bge-reranker-v2-m3 routes as chat models, because a rawllm route could only be `chat`.

This is a deliberate behaviour change. Each raw endpoint now admits only its own modality: `/v1/embeddings` serves `embedding` routes, `/v1/rerank` serves `rerank` routes, `/v1/chat/completions` and `/v1/messages` serve `chat` routes. A mismatch is a 400 in the endpoint's usual envelope (OpenAI-shaped on the raw endpoints, Anthropic on `/v1/messages`). An existing config that routes a bge model as `chat` must add `"modality": "embedding"` or `"modality": "rerank"`, or `/v1/embeddings` and `/v1/rerank` are refused for that route. `GET /v1/models` echoes the modality and `?modality=` accepts `chat`, `image`, `embedding`, `rerank` or `all`; any other value stays a 400. Embedding and rerank routes are never picked up by the Claude Code Messages path or the image endpoints. No suite case was added: the llm_router count stays 483 (cases A55, V5, V12, V13 were extended).
