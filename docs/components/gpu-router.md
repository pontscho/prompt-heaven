---
name: gpu-router
type: component
status: active
title: gpu-router — a supervisor and reverse proxy for one GPU's apps (sd-server and inferNO-style LLM engines)
description: The stdlib-only HTTP front that owns one GPU's apps -- a 0600 JSON config of apps, each one child process serving one or more model ids, of kind sd (stable-diffusion.cpp sd-server, buffered answers) or llm (inferNO-style, streamed answers), with the listener keys under CLI over config over default; an edge-only model-id index, one kind table, a readiness probe path and an optional fixed child port; one strict FIFO with one request in flight and a swap when the head names another app; a per-app state machine with one definition of resident, queued and forced unloads through any model id that answer 200 only once the child's process group is verified gone; streamed llm answers through a bounded queue with an allow-listed head and a strict de-chunker; the abort matrix under which a client abort closes the upstream and kills nothing; retries and a restart budget that disables; a closed table of OpenAI-shaped error codes; the llm-router sdcpp and rawllm integration through a bearer; how it is tested against two adversarial stub children; the code-mode security review and its fix round; and what it declares rather than gates.
sources:
  - Scripts/gpu-router.py
  - tests/test_gpu_router.py
  - tests/files/gpu_router/tf_stub_sd.py
  - tests/files/gpu_router/tf_stub_llm.py
verified:
  commit: f43a052
  date: 2026-10-30
links:
  - llm-router
  - mcp-proxy
  - generated-regions
  - scripts
  - tests
  - 0028-route-by-model-translate-at-the-edge
  - 0029-the-http-front-is-a-domain
  - 0027-the-proxy-relays-it-never-composes
  - 0025-generate-do-not-import
  - 0024-pure-python-39-and-the-stdlib
  - 0015-ambiguity-is-the-defect
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0034-sd-router-a-supervisor-and-proxy-for-sd-server
  - 0035-one-gpu-several-apps
---

# gpu-router

`Scripts/gpu-router.py` stands between clients and the apps of a host with one
GPU: stable-diffusion.cpp `sd-server` (kind `sd`) and inferNO-style LLM,
embedding and reranker engines (kind `llm`). Its JSON config declares apps; an
app is ONE child process that serves one or more model ids. For each app it
spawns the child from config data, relays requests to it byte-exact, restarts
it after a crash, swaps it out when the queue head names another app, and stops
it on `/v1/unload` so the VRAM is freed `Scripts/gpu-router.py`. Requests run in
strict FIFO order with one in flight whatever the app, and a cold start or a
swap makes the caller wait. An sd answer is buffered; an llm answer streams. It
is pure Python 3.9 and the standard library
([[0024-pure-python-39-and-the-stdlib]]), and it is not an MCP server. It was named
sd-router until the rename in commit `bde9876`.

This page is the *shape*. The original decision, the alternatives that lost and
the planner decisions D-1 to D-17 are recorded in
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]]; the generalisation to
apps, the llm kind, streaming, the abort matrix (which amends D-12) and
llm-router's `rawllm` kind are [[0035-one-gpu-several-apps]]. [[llm-router]]'s
image and rawllm routes are its intended client, and its child supervision is a
declared adapted copy of [[mcp-proxy]]'s.

The file is nine numbered units in file order (`# ---- unit N ----`): config
and CLI, routing and the model sniffer, the scheduler's decision logic (with the
abort matrix and the de-chunker), the supervisor, the upstream forwarder, the
scheduler runtime, the HTTP front, the handlers, and `main`. Units 1-3 are pure
and name no socket, subprocess, http or threading module
`tests/test_gpu_router.py:GA_CASES`.

**Kept names.** The internal `_Sd*` / `_sd_*` prefix and the `models` config
key survived the rename: the HTTP-front adaptation table and the static gates
pin the symbols, and `models` now means apps. Every externally visible name
string changed: the logger, the `gpu-router:` stderr prefix, `prog`,
`server_version` and `owned_by: gpu-router` `Scripts/gpu-router.py:_sd_models_entry`.

## Config

**One 0600 file, read through `--config`.** The file is opened without
following a symlink and must be a regular file owned by the effective uid with
no group or other permission bits, at most `Scripts/gpu-router.py:_SD_CONFIG_LIMIT`
bytes of UTF-8 `Scripts/gpu-router.py:_sd_read_config_file`. A repeated key at
any depth is refused, as are `NaN` and the infinities
`Scripts/gpu-router.py:load_config` ([[0015-ambiguity-is-the-defect]]). There is
no `--config-json`, since argv is visible in `ps` and every config holds the
bearer token `Scripts/gpu-router.py:_sd_parser`. Every refusal is one
`gpu-router: <reason>` stderr line and exit 2, naming the key and the rule and
never a value `Scripts/gpu-router.py:_sd_refuse`.

**Top level.** `token` is required: the generated front's length and charset
rule with the floor `Scripts/gpu-router.py:_SD_TOKEN_MIN_LEN`, then at least
`Scripts/gpu-router.py:_SD_TOKEN_MIN_DISTINCT` distinct characters
`Scripts/gpu-router.py:_sd_cfg_token`. `models` is a non-empty object of APPS
whose names match `Scripts/gpu-router.py:_SD_MODEL_NAME_RE`. The optional
`body_limit` takes the command line's range
`Scripts/gpu-router.py:_SD_BODY_LIMIT_RANGE` (1 KiB to 1 GiB) and the optional
`response_limit` 1 to `Scripts/gpu-router.py:_SD_RESPONSE_LIMIT_MAX` (1 GiB).
The listener options are config keys too, since `ea5bdc8`: `bind` (a non-empty
string without a NUL, or a non-empty list of unique such strings, one listener
each `Scripts/gpu-router.py:_sd_cfg_bind`), `port` (`Scripts/gpu-router.py:_SD_PORT_RANGE`),
`allow_remote` and `insecure_cleartext` (real booleans) and `max_connections`
(`Scripts/gpu-router.py:_SD_CONNECTIONS_RANGE`), and so are the process
options `ready_file` and `log_file` (non-empty path strings without a NUL
`Scripts/gpu-router.py:_sd_cfg_path`) and `debug` (a real boolean); unknown keys are refused by
name, the refusal listing the allowed ones `Scripts/gpu-router.py:_SD_TOP_KEYS`
`Scripts/gpu-router.py:parse_config`. The token never appears in a repr
`Scripts/gpu-router.py:Settings`.

**One app entry** `Scripts/gpu-router.py:_sd_cfg_model`:

| Key | Rule |
|---|---|
| `command` | required; an absolute path to an existing regular executable file |
| `args` | required; a non-empty list of strings holding at least one `{port}` (`{host}` is optional, no other placeholder is accepted) `Scripts/gpu-router.py:_sd_cfg_args` |
| `env` | optional; keys match `Scripts/gpu-router.py:_SD_ENV_KEY_RE`, values are strings and never appear in a repr `Scripts/gpu-router.py:ModelSpec` |
| `cwd` | optional; an absolute existing directory |
| (all four above) | a NUL anywhere in `command`, an `args` element, an `env` value or `cwd` is refused at load, since `Popen` would raise `ValueError` on it `Scripts/gpu-router.py:_sd_cfg_no_nul` |
| `kind` | optional; `sd` (default) or `llm` `Scripts/gpu-router.py:_SD_KINDS`; the kind's row of `Scripts/gpu-router.py:_SD_KIND_DEFAULTS` supplies every default below |
| `model_ids` | the ids the app serves: a list of distinct ids of the app-name form `Scripts/gpu-router.py:_sd_cfg_model_ids`; required and non-empty for `llm`; an `sd` app without it is addressed by its name. An id may repeat its own app's name, but an id served by two apps, or equal to ANOTHER app's name, is refused at load `Scripts/gpu-router.py:_sd_cfg_index` |
| `start_timeout`, `stop_grace` | optional seconds, each with its default and its `(low, high]` range in `Scripts/gpu-router.py:_SD_MODEL_FLOATS`; the `start_timeout` default is the kind row's |
| `request_timeout` | optional seconds from dispatch to the whole answer; default and ceiling per kind: `sd` 600 in (1, 7200], `llm` 86400 in (1, 604800] (the kind row) |
| `head_timeout`, `idle_timeout` | `llm` only, seconds in (0, `Scripts/gpu-router.py:_SD_DEADLINE_CAP`], default 1800 each: no response head, and no byte from the child, within that. On an `sd` app either key is refused, not ignored |
| `ready_path` | optional; the readiness probe path, origin-form with no query, fragment or space `Scripts/gpu-router.py:_SD_READY_PATH_RE`; default `/` for `sd`, `/v1/models` for `llm` |
| `ready_status` | optional; 100-599: ready only on exactly that status. Without it `sd` is ready on any status but 503 and `llm` on 200 |
| `child_port` | optional; a fixed child port in `Scripts/gpu-router.py:_SD_CHILD_PORT_RANGE`, never the listener's port and never another app's `Scripts/gpu-router.py:_sd_cfg_child_ports`; without it a free port is picked |
| `retries` | optional; the number of ADDITIONAL sends after a crash, default `Scripts/gpu-router.py:_SD_RETRIES_DEFAULT`, at most `Scripts/gpu-router.py:_SD_RETRIES_MAX` |

Every key added for the llm kind is defaulted, so a config written for
sd-router loads unchanged. The defaults of the llm kind (the 120 s start, the
1800 s head and idle deadlines, the 86400 s request deadline, the `/v1/models`
probe) are suggestions the owner has not confirmed
([[0035-one-gpu-several-apps]]).

`{port}` and `{host}` are substituted by an explicit replace, never
`str.format`, because sd-server arguments may hold braces
`Scripts/gpu-router.py:render_args`. The router owns the child's address: an
element naming one of the port or host flags in
`Scripts/gpu-router.py:_SD_PORT_HOST_FLAGS`, bare or as `flag=value`, is refused
unless its value is exactly `{port}` or `{host}`. That list is a deny-list over
flag names believed to be sd-server's and not verified against it; the real
safety mechanism is readiness, a live child answering on the substituted port.
The plan's literal rule (no fixed port or host flag at all) would have refused
every real config, so the placeholder exception is an implementation decision
recorded in ADR 0034.

**Precedence with the command line.** For `bind`, `port`, `allow_remote`,
`insecure_cleartext`, `max_connections`, `body_limit`, `ready_file`, `log_file`
and `debug` an explicit flag wins, then the config key, then the built-in
default; `response_limit` is config-only `Scripts/gpu-router.py:_sd_settings`. The listener rules run on the MERGED
result, so a config `bind` is refused exactly like `--bind`, and a command-line
`--port` equal to an app's `child_port` is refused too. Cases L33 to L39 pin the
listener keys and their precedence, L32 the `body_limit` one, and L46 to L48
the three process keys (accepted, wrong types refused, a flag beating the key)
`tests/test_gpu_router.py:GL_CASES`.

**Several listeners.** `--bind` is repeatable, and any `--bind` flag replaces
the config `bind` as a whole rather than adding to it. The rules run per
address on the merged result: every non-loopback address needs `allow_remote`
AND `insecure_cleartext`, a repeated address is refused, and more than one
address with port 0 or no port is refused (`several --bind addresses need an
explicit port`), because an ephemeral port is picked per socket and the
addresses would not share one; no refusal echoes an address. A one-element list
collapses to the plain string, so one address behaves exactly as before
`Scripts/gpu-router.py:_sd_settings`. All the addresses share the one port, the
one process and the one scheduler. L49 pins the grammar, the replacing flag, the
per-address rule and the two refusals; L34 no longer lists a one-element list as
a bad type `tests/test_gpu_router.py:l49`.

## Command line and startup

The flags are `--config`, `--bind`, `--port`, `--allow-remote`,
`--insecure-cleartext`, `--ready-file`, `--body-limit`, `--max-connections`,
`--debug` and `--log-file`; every flag but `--config` defaults to "absent", so
the config key or the built-in default (`127.0.0.1`, port 0 = ephemeral, no
ready file, no log file, debug off) applies `Scripts/gpu-router.py:_sd_parser`.
The config is read and merged before logging is set up, because `debug` and
`log_file` may come from it; a config refusal still goes to stderr as one line,
but nothing logged while the config loads reaches `--debug` or the log file
`Scripts/gpu-router.py:main`. An unrecognized argument
is refused without echoing it, because argparse would quote it verbatim
`Scripts/gpu-router.py:_SdArgumentParser`.

There is no TLS. `allow_remote` without `insecure_cleartext` (flag or key) is
refused (D-13), and so is a non-loopback bind without `allow_remote`; both run
on the merged settings `Scripts/gpu-router.py:_sd_settings`, and a bind failure
exits 2 with one line `Scripts/gpu-router.py:main`. `main` builds one server per
address: the first is the primary, every later one is constructed with
`peer=` the primary and shares its shutdown flag, start time (`started_epoch`)
and drain condition, while keeping its own socket, `loopback` flag (so the
`Host` check follows the address it listens on), `active` count and connection
semaphore `Scripts/gpu-router.py:_SdHttpServer`. A bind failure on any address
closes the sockets already bound before the one-line exit 2; the ready file is
written only after every bind, with unchanged content (`{port, pid}`), and the
shutdown drains the sum of the listeners' `active` counts, then shuts down and
closes each listener `Scripts/gpu-router.py:_sd_shutdown`. The listeners bind before
anything else starts; the signal handlers (SIGTERM, SIGINT, SIGHUP, all three an
ordered shutdown) are installed before the ready file is written. A handler only
sets an attribute on `Scripts/gpu-router.py:_SdStopFlag`, never `Event.set`,
whose lock the main thread may hold inside `Event.wait` when the signal lands;
main polls the flag one tick at a time `Scripts/gpu-router.py:_sd_wait_stop`.
At startup the router always logs the fixed single-user-host line
`Scripts/gpu-router.py:_SD_TRUST_LINE`, and with both remote options the
cleartext warning `Scripts/gpu-router.py:_SD_CLEARTEXT_LINE`. The exit codes are
0 for a normal or signal-initiated exit and 2 for any config, command-line or
startup refusal (module docstring `Scripts/gpu-router.py`).

The ordered shutdown marks the server closing (new requests get 503
`shutting_down`), closes the scheduler (queued tickets 503 `queue_closed`, the
running one aborted, a ladder in progress finished, the resident child stopped,
then a last-resort sweep of the spawn registry), drains handler threads for at
most `Scripts/gpu-router.py:_SD_DRAIN_S`, closes the listener and removes the
ready file only while it still holds this pid
`Scripts/gpu-router.py:_sd_shutdown`.

## Request flow

client → gpu-router (HTTP/1.1, bearer) → one scheduler thread → supervisor →
the app's child on `127.0.0.1:<port>` (no auth).

**Routes.** The request routes are one mapping from path to a kind token
`Scripts/gpu-router.py:ROUTES`: the sd routes `POST /v1/images/generations`,
`/v1/images/edits` (token `openai`), `/sdapi/v1/txt2img` and
`/sdapi/v1/img2img` (`sdapi`), and the llm routes `/v1/chat/completions`,
`/v1/messages` (`chat`), `/v1/embeddings` and `/v1/rerank` (`embed`), each also
under `/m/<x>/`, where `x` is an app name or any of its model ids. The token
decides which app kind serves the route: a route of the other kind is 404
`route_not_found` `Scripts/gpu-router.py:_sd_kind_accepts`. The control routes
are `GET /v1/models` and `POST /v1/unload` (also under `/m/<x>/`); `/v1/models`
is not in `ROUTES`, so the table stays the one path-keyed literal beside
`Scripts/gpu-router.py:_SD_ROUTE_MEDIA`. Every route needs the bearer; anything
else is 404 `route_not_found`. Async job routes and sd-server's own
model-management routes are not proxied.

**The pipeline, in order** `Scripts/gpu-router.py:_sd_pipeline`:

1. `_precheck`: the bearer first — exactly one `Authorization` header, scheme
   `Bearer ` matched case-insensitively, the token compared as UTF-8 bytes with
   `hmac.compare_digest` `Scripts/gpu-router.py:_sd_bearer_ok`; a failure is 401
   with `WWW-Authenticate: Bearer`. Then header defects, then on a loopback bind
   a `Host` that must be exactly `127.0.0.1:<port>` or `localhost:<port>`, then
   any `Origin` at all; those three are 400 `invalid_request`
   `Scripts/gpu-router.py:_precheck`. A stopping router answers 503
   `shutting_down`.
2. A control path leaves the pipeline here for the control handler, before
   routing and without an admission slot `Scripts/gpu-router.py:_sd_control`.
3. Routing on the raw request target, never the stdlib's normalised `self.path`:
   any `%`, `//`, `.` or `..` segment is 404, a wrong method 405, any query 400,
   a prefix naming neither an app nor a model id 404 `model_unknown`, an
   `/sdapi/` route without a prefix 400 `model_required`
   `Scripts/gpu-router.py:_sd_route_head`; a prefix whose app's kind does not
   serve the route is 404 `route_not_found`. The routing functions stay pure;
   the app resolution and the kind check live in the pipeline.
4. Header-only framing: any `Transfer-Encoding` is 411, so is anything but
   exactly one decimal `Content-Length`; over the route's body cap is 413 —
   `min(cap, body_limit)`, with 16 MiB for the `chat` token and 4 MiB for
   `embed`, and the plain `body_limit` for the image tokens
   `Scripts/gpu-router.py:_SD_KIND_BODY_CAPS` `Scripts/gpu-router.py:_sd_body_cap`;
   a Content-Type holding any control character (an obs-fold CRLF, a tab and DEL
   included) is 400 `invalid_request` on every path, the `/m/<x>/` prefix
   included, because that value crosses the hop
   `Scripts/gpu-router.py:_sd_has_ctl`; a Content-Type the route does not take
   is 415 `Scripts/gpu-router.py:_SD_ROUTE_MEDIA`; on a `chat` route an
   `anthropic-version` or `anthropic-beta` that is repeated or not 1-256
   printable ASCII characters is 400 `Scripts/gpu-router.py:_sd_framing`.
   No body byte has been read and no slot is held.
5. Admission: `try_admit()` refuses with 503 `queue_full` and `Retry-After: 1`
   once queued plus running request tickets fill `max_connections`
   `Scripts/gpu-router.py:try_admit`.
6. `Expect: 100-continue` is answered only now.
7. The body is read under the total deadline
   `Scripts/gpu-router.py:_SD_BODY_DEADLINE_S`; expiry or a short body is 400
   `invalid_request` with the slot released `Scripts/gpu-router.py:_sd_read_body`.
8. Without a prefix the body's `model` picks the app
   `Scripts/gpu-router.py:route_request`, and the kind check runs again.
9. The ticket is submitted under the APP name with its reservation; the handler
   waits for the ticket's head-ready Event (set by a stream's head and by the
   finish alike) while polling for a departed client every
   `Scripts/gpu-router.py:_SD_CLIENT_POLL_S`, then writes the stream or the
   buffered answer.

**Model selection.** The `/m/<x>/` prefix is authoritative: with it the body
is never sniffed and its `model` is ignored (D-11), which is what lets
llm-router send its own route key as `model`. Without a prefix, every route but
`/sdapi/` falls back to the body: a JSON `model`, or exactly one canonical
`name="model"` multipart part, read from at most the first
`Scripts/gpu-router.py:_SD_SNIFF_LIMIT` bytes and never modified; every spelling
two readers could read two ways is a 400, and so is a JSON body longer than the
window `Scripts/gpu-router.py:sniff_model`, which is why a long chat body needs
the prefix. It is a smaller hand-written analogue of llm-router's image field
readers: it copies their behaviour, none of their code.

**The proxy hop.** `Scripts/gpu-router.py:_sd_send` is the one upstream send
site. The client's first `Content-Type` crosses, and on a `chat` route the first
`anthropic-version` and `anthropic-beta` too
`Scripts/gpu-router.py:_SD_KIND_FORWARD`; `Authorization`, `Cookie`, `Host`,
`Expect` and hop-by-hop headers are dropped, so the router token never reaches
the child `Scripts/gpu-router.py:_sd_forward_headers`. The request line is
rebuilt from the route table, the body sent byte-exact with a recomputed
`Content-Length`, `Accept-Encoding: identity` and `Connection: close`. The
child's liveness is checked right before every send: a child that exited after
it was declared ready leaves a port anyone could hold, so nothing is sent and the
send counts as a crash `Scripts/gpu-router.py:_exec_send`.

**Buffered answers (every sd answer, and an llm answer that does not stream).**
The whole answer is buffered under `response_limit` before any byte reaches the
client, so a retry can never replay half an answer. A 1xx, a 3xx, an encoded or
malformed answer, one over the limit, or one whose Content-Type holds a control
character, is 502 `upstream_failed` `Scripts/gpu-router.py:_sd_parse_answer`.
The child's own answers, its non-2xx ones included, are relayed verbatim with
the router's own `Content-Length`, `Connection: close` and
`X-Content-Type-Options: nosniff` `Scripts/gpu-router.py:_sd_write_result`;
`/v1/models` and the unload answers go through the same writer. Every exchange is
one request per connection: no keep-alive.

**Streamed answers (kind llm).** An llm app's answer head is read first, under
`head_timeout` `Scripts/gpu-router.py:_sd_relay`. Only a 2xx that is
`text/event-stream` or carries no `Content-Length` streams
`Scripts/gpu-router.py:_sd_head_streams`; a JSON answer with a length and every
non-2xx stay on the buffered path, so the closed error table is unchanged. The
streamed head is an allow-list `Scripts/gpu-router.py:_sd_parse_stream_head`:
the status line must be HTTP/1.x; 1xx, 3xx, a non-identity `Content-Encoding`,
`Content-Length` together with `Transfer-Encoding`, a `Transfer-Encoding` other
than chunked, a repeated framing header and any header line that is not `token:
value` (an obs-fold, a bare CR or LF, a control byte) are `upstream_failed`; the
head must fit in `Scripts/gpu-router.py:_SD_HEAD_LIMIT`; and only the child's
`Content-Type` survives — `Set-Cookie`, `Location`, `Trailer`, `Upgrade`,
`Connection` and every other header are dropped. The scheduler thread then pumps
the body into a bounded byte queue `Scripts/gpu-router.py:_SdStream`, which
holds at most `Scripts/gpu-router.py:_SD_STREAM_CAP` before the pump stops
reading the child (back-pressure), each read under `idle_timeout`
`Scripts/gpu-router.py:_sd_pump`; a chunked body is decoded by a strict
incremental de-chunker — hex digits only, at most
`Scripts/gpu-router.py:_SD_CHUNK_DIGITS` of them, exact CRLFs, no trailer
section `Scripts/gpu-router.py:_SdDechunker`. The handler thread alone writes
the client: the status, the child's `Content-Type`, a router-fixed
`Cache-Control: no-cache`, `nosniff`, `Connection: close`, chunked framing for an
HTTP/1.1 client (close-delimited otherwise) and never a `Content-Length`; it
polls for a departed client between pieces and while nothing arrives, and a
write error or a departed client cancels the ticket
`Scripts/gpu-router.py:_sd_write_stream`. A clean end writes the terminating
chunk; an abnormal end (the child drops, an idle abort, a force unload,
shutdown) closes WITHOUT it, so a truncated answer cannot pass as a complete
one. The moment the head is published the ticket counts as having relayed bytes
and is never re-queued `Scripts/gpu-router.py:_publish_stream`.

## The scheduler and the state machine

**One thread, one Condition.** A single scheduler thread owns the per-app state
and the GPU; handlers only enqueue a ticket and wait on it. The state is keyed by
APP name, never by model id. Every write of an app's state, its zombie flag, its
disabled flag, its budget and backoff, of `running`, of the queue and of the
admission counter happens in a short section under one `threading.Condition`,
with no logging and no blocking call inside `Scripts/gpu-router.py:Scheduler`;
the lock discipline is a static gate `tests/test_gpu_router.py:GA_CASES` (A17).
The decision itself is a pure function of a head ticket, the app snapshots, the
resident app and a clock value passed in `Scripts/gpu-router.py:decide`, which
is why it is tested without sockets or processes.

**States.** `STOPPED`, `STARTING`, `READY`, `STOPPING`, `DEAD`
`Scripts/gpu-router.py:ModelState`. Disabled is a per-app flag beside the
budget, and `group_alive` marks a DEAD app whose group survived its ladder
(a zombie) `Scripts/gpu-router.py:ModelView`.

**Resident, defined once (D-17).** An app is resident when it is `STARTING`,
`READY` or `STOPPING`, or `DEAD` with a live group; a disabled flag never changes
that `Scripts/gpu-router.py:_sd_is_resident`. At most one app is resident at a
time (QS13 pins it), whatever its kind.

**Transitions** `Scripts/gpu-router.py:_take` `Scripts/gpu-router.py:_execute`:

- The head is popped and `STOPPED → STARTING` written in one locked section, so
  nothing looks non-resident while a start is in progress. A head for another
  app than the resident one is a swap: the resident app's stop ladder runs
  first `Scripts/gpu-router.py:_exec_swap`.
- `STARTING → READY` once the child answers a GET of the app's `ready_path` with
  its ready status (with no `ready_status`: any status but 503 for `sd`, where
  503 means "still loading"; 200 for `llm`), and the child is still alive after
  the answer, so a foreign listener answering for an exited child is not ready
  `Scripts/gpu-router.py:wait_ready` `Scripts/gpu-router.py:_sd_probe_status`.
  A refused connect keeps the probe polling, which is how an inferNO-style
  engine that does not listen while it loads is waited for. Readiness is never
  read from the child's output: its stdio stays `DEVNULL`. A child that exits
  within `Scripts/gpu-router.py:_SD_EARLY_EXIT_S` of its spawn is taken for a
  port collision and respawned on a new port, at most
  `Scripts/gpu-router.py:_SD_PORT_TRIES` spawns — unless the app has a fixed
  `child_port`: then there is one spawn and an early exit is a failed start
  `Scripts/gpu-router.py:launch`. A spawn that `Popen` refuses (`OSError`,
  `SubprocessError` or `ValueError`) is a failed start, not a crash of the
  scheduler step. A start that fails or times out leaves the app `DEAD`, never
  `STARTING`, charges the budget and fails the head 502 `model_start_failed` or
  504 `model_start_timeout` `Scripts/gpu-router.py:_exec_start`.
- What a failed or aborted send does is one row of the abort matrix, a pure
  function of the abort reason, the app's kind and whether a byte was relayed
  `Scripts/gpu-router.py:_sd_abort_plan`, acted on by
  `Scripts/gpu-router.py:_send_failed`; the matrix itself is below.
- An answer the relay refuses (`upstream_failed`: 1xx, 3xx, encoded, malformed,
  over `response_limit`) is no crash: the child answered, so it stays `READY`,
  nothing is charged, retried or stopped.
- A group that survives SIGKILL leaves the app `DEAD` with `group_alive` set.
  While such a zombie exists every request that reaches the head fails 500
  `unload_failed`, and the group is re-probed with signal 0 before each pop
  until it is gone `Scripts/gpu-router.py:_reprobe`.

**The abort matrix** ([[0035-one-gpu-several-apps]]):

| Reason | kind `sd` | kind `llm` |
|---|---|---|
| a crash or EOF, nothing relayed | `DEAD` with `group_alive`, one charge, the ladder, the SAME ticket back at the head while `Scripts/gpu-router.py:_sd_may_retry` allows (`retries=1` means at most two sends), else 502 `model_crashed` | same |
| a crash or EOF after the head was relayed | n/a (buffered) | never re-queued, one charge, the ladder; the stream ends truncated |
| `request_timeout` (dispatch to the whole answer) | the ladder, one charge, 504 `upstream_timeout`, never retried | no ladder, no charge: the upstream socket is closed and the child stays `READY`; 504 before any byte, a truncated stream after |
| `head_timeout` / `idle_timeout` | n/a (refused at load) | as `request_timeout` |
| a departed client | the upstream is closed, NOTHING is killed, no charge | same |
| `?force=1` | the ladder, no charge, 503 `unload_forced` | the ladder, no charge; 503 `unload_forced` before any byte, a truncated stream after |
| shutdown | the ladder, no charge, 503 `shutting_down` | the ladder, no charge; 503 `shutting_down` before any byte, a truncated stream after |

A client abort used to kill the child (ADR 0034's D-12); it now closes the
upstream for both kinds, because a kill throws away a 20-60 s load. The sd half
is one switch, `Scripts/gpu-router.py:_SD_ABORT_KILLS_SD` (`False`), so a
measurement of whether sd-server stops generating on a closed connection can
flip one value.

**The restart budget.** `Scripts/gpu-router.py:_SD_RESTART_BUDGET` crashes inside
a sliding `Scripts/gpu-router.py:_SD_RESTART_WINDOW_S` window set the app's
disabled flag `Scripts/gpu-router.py:Budget`, and its tickets fail 503
`model_disabled` until an unload clears it. Between consecutive automatic
restarts the backoff `Scripts/gpu-router.py:_SD_BACKOFF_S` applies with up to
`Scripts/gpu-router.py:_SD_BACKOFF_JITTER` jitter; the first start after an
unload or an idle period is immediate `Scripts/gpu-router.py:_sd_backoff_s`.

**Admission and memory.** One counter of reservations bounds queued plus
running request tickets of either kind by `max_connections`
`Scripts/gpu-router.py:try_admit`. The handler releases a completed ticket's
reservation only after it wrote the answer, a queued cancel releases at once, and
a running ticket whose client left is released by the scheduler when it drops
the ticket `Scripts/gpu-router.py:_sd_settle` `Scripts/gpu-router.py:cancel`
`Scripts/gpu-router.py:_drop`. The connection semaphore holds
`max_connections` plus `Scripts/gpu-router.py:_SD_CONTROL_RESERVE` slots, so
the control routes keep connections while request traffic is at its cap
`Scripts/gpu-router.py:_SdHttpServer`. With several `--bind` addresses that
semaphore is per listener, while the reservation counter is the one
scheduler's, so request tickets stay capped at `max_connections` for the whole
process and only the connection slots multiply. The module docstring declares the memory
worst case as `max_connections x (body_limit + response_limit)` plus control
bodies of at most `Scripts/gpu-router.py:_SD_CONTROL_BODY_LIMIT` per connection,
about 768 MiB at the defaults `Scripts/gpu-router.py:_SD_DEFAULT_MAX_CONNECTIONS`
`Scripts/gpu-router.py:_SD_DEFAULT_BODY_LIMIT`
`Scripts/gpu-router.py:_SD_DEFAULT_RESPONSE_LIMIT`; a stream adds its bounded
queue. The operator sizes the limits against RAM.

**Children.** A child is spawned in its own session from an argv list, never a
shell, with stdio on `DEVNULL` — the file's only `Popen`
`Scripts/gpu-router.py:start`. Its environment is `PATH`, `HOME` and `LANG` plus
the config `env`, never the router's own and never the token (D-1)
`Scripts/gpu-router.py:_sd_child_env`. The port is the app's `child_port`, or
else picked by binding port 0 on loopback and closing it
`Scripts/gpu-router.py:_sd_free_port`.

**The stop ladder: kill before reap.** SIGTERM to the group, a wait of
`stop_grace` for the leader WITHOUT reaping it, SIGKILL to the group while the
leader is still an unreaped zombie (so its pid and the pgid cannot be recycled),
reap, then confirm the group gone with signal 0 within
`Scripts/gpu-router.py:_SD_KILL_STEP_S` `Scripts/gpu-router.py:stop`. The
unreaped wait is a declared adapted copy of sbx's, chosen by `sys.platform`: a
kqueue `NOTE_EXIT` on darwin, `os.waitid(..., WNOWAIT)` elsewhere
`Scripts/gpu-router.py:_sd_wait_exit_unreaped`. A leader that a readiness
`poll()` already reaped has no zombie left to pin the pgid: the poll marks the
child reaped and probes the group with signal 0 at once, dropping an emptied
group from the registry `Scripts/gpu-router.py:_poll`, and the ladder for such a
child gives a surviving member the full `stop_grace` by watching the group
before its SIGKILL, and sends none once it is empty
`Scripts/gpu-router.py:_wait_group_empty`. A confirmed-gone pgid leaves the
spawn registry at once; the shutdown sweep re-probes each remaining entry right
before its SIGKILL, and ESRCH or EPERM drops it unsignalled
`Scripts/gpu-router.py:kill_all`.

## Unload

`POST /v1/unload` selects its target from `?model=<id>`, the
`/m/<id>/v1/unload` prefix, or by default the resident app. A target names an
APP through any of its model ids or its name: the handler resolves it through
the model-id index and the scheduler stops the app
`Scripts/gpu-router.py:_sd_handle_unload`. The body must be empty or `{}`; the
query is split by hand with no percent-decoding, and only `model` and `force=1`,
each at most once, are accepted (D-16) `Scripts/gpu-router.py:_sd_unload_query`.
A prefix and a `?model=` resolving to different apps are 400 (two ids of one app
are one target), and an unknown id is 404 `model_unknown` before anything is
queued. Control requests read at most
`Scripts/gpu-router.py:_SD_CONTROL_BODY_LIMIT` bytes and never take an admission
slot `Scripts/gpu-router.py:_sd_control_body`. A client that leaves while its
unload waits does not cancel it. Unload through any id and "no target = the
resident app" are agent defaults the owner has not confirmed
([[0035-one-gpu-several-apps]]).

**Queued (default).** The unload ALWAYS enters the FIFO, so it waits for every
earlier request; its default target is the resident model when it reaches the
head. A resident target runs the ladder and answers 200 `{"unloaded": true,
"model": ...}` only after the group is confirmed gone, or 500 `unload_failed`;
a non-resident target answers 200 `{"unloaded": false}` at once and has its
disabled flag, budget and backoff cleared `Scripts/gpu-router.py:_exec_unload`.
Because an unload survives its client, a repeated one may not grow the queue: a
non-force unload naming the same model (the default included) as the queue's
LAST ticket, itself a non-force unload, is attached to that ticket and both
callers read its one result. Only the tail is matched, so FIFO order is
unchanged and an unload behind a later request still gets its own ticket
`Scripts/gpu-router.py:submit_unload`.

**`?force=1` (scoped to its target).** A force for the RESIDENT model jumps the
FIFO through a separate list, aborts a running request for that model (503
`unload_forced`), stops a start in progress, and answers 200 only after the
ladder; concurrent forces for one model share one ladder. A force for a
non-resident model is executed by `submit_unload` itself under the lock: the
running request is untouched, the target's disabled flag, budget and backoff
are cleared, and the ticket comes back already finished 200 `{"unloaded":
false}` `Scripts/gpu-router.py:submit_unload`. Queued requests stay queued and
cold-start the model again after a forced stop (D-3).

A stop of the resident model clears the crash history only when the model was
disabled; a healthy model keeps its budget (T10)
`Scripts/gpu-router.py:_reenable_locked`. The plan's FR-5 said unload always
clears; this deviation is recorded in ADR 0034.

**`GET /v1/models`** is aggregated from the config, never discovered: one entry
per configured model id of every app, apps in config order (an app without
`model_ids` is listed by its name), nothing started or asked
`Scripts/gpu-router.py:_sd_handle_models`. Each entry carries llm-router's route
keys (`id`, `object`, `created`, `owned_by: "gpu-router"`, and the kind's
`modality`, `image` for `sd` and `chat` for `llm`) plus `state` of the OWNING
app: `disabled` when the flag is set and the app is not resident, else the state
name. Command, args, env, cwd, port and pid are never shown
`Scripts/gpu-router.py:_sd_models_entry`. `/m/<x>/v1/models` answers the same
list once `x` is a configured app or id, else 404 `model_unknown`.

## The closed error table

Every refusal is `{"error": {"message", "type", "code"}}` with a code from one
table `Scripts/gpu-router.py:_SD_ERROR_STATUS`, a `type` per status
`Scripts/gpu-router.py:_SD_ERROR_TYPE`, and a fixed message that names a rule and
never a value `Scripts/gpu-router.py:_sd_error`. A code outside the table raises
`KeyError`, so no answer can carry one.

| Code | Status | When |
|---|---|---|
| `unauthorized` | 401 | missing, repeated or wrong bearer |
| `route_not_found` | 404 | no such route |
| `method_not_allowed` | 405 | a known path, a wrong method |
| `length_required` | 411 | `Transfer-Encoding`, or not exactly one decimal `Content-Length` |
| `body_too_large` | 413 | over the route's body cap (`min(cap, body_limit)`), or a control body over its cap |
| `unsupported_media_type` | 415 | a Content-Type the route does not take |
| `invalid_request` | 400 | header defects, `Host`, `Origin`, a control character in the Content-Type, a repeated or malformed `anthropic-*` header on a `chat` route, a query on a route, a refused sniff, a body deadline or short body, a bad unload body or query |
| `model_required` | 400 | no model resolvable |
| `model_unknown` | 404 | neither a configured app nor a configured model id |
| `model_crashed` | 502 | the child died and the retries are spent |
| `model_start_failed` | 502 | the child exited before it became ready |
| `model_start_timeout` | 504 | `start_timeout` elapsed |
| `model_disabled` | 503 | the restart budget is spent; an unload clears it |
| `unload_forced` | 503 | the request was aborted by `?force=1` |
| `unload_failed` | 500 | a group survived SIGKILL; also every request while a zombie holds the GPU |
| `upstream_timeout` | 504 | `request_timeout` elapsed before the whole answer; for `llm` also `head_timeout` or `idle_timeout`, before any byte was relayed |
| `queue_full` | 503 | the request cap is full; `Retry-After: 1` |
| `upstream_failed` | 502 | a 1xx, 3xx, encoded, malformed or oversized answer, one whose Content-Type holds a control character, or a streamed head the allow-list parser refuses; also a bug inside the handler |
| `shutting_down` | 503 | the router is stopping |
| `queue_closed` | 503 | the scheduler closed while the ticket was queued |

The stdlib's own refusals are mapped into the same table with fixed text: 501
becomes 405 `method_not_allowed`, 400, 414, 431 and 505 become 400
`invalid_request` `Scripts/gpu-router.py:_SD_STDLIB_REFUSAL`. The fixed messages
are route-neutral ("a route takes POST only", "too many requests queued; retry
shortly"), since the table now serves both kinds
`Scripts/gpu-router.py:_sd_route_head` `Scripts/gpu-router.py:_SD_ERROR_TEXT`. A `HEAD` has no
handler, so it gets that 405 before the bearer is checked. The table has no 403
and its only 500 is `unload_failed`, so a handler bug answers 502
`upstream_failed` `Scripts/gpu-router.py:_sd_handle`. The one answer outside the
envelope comes from the generated front: a connection over the semaphore's
capacity gets a body-less 503 and is closed
`Scripts/gpu-router.py:process_request`. The table is pinned twice, by P34 and by
A9, which requires it to equal the set of codes the source uses
`tests/test_gpu_router.py:GA_CASES`.

## Generated regions

The front's identical pieces, the token check, the ready file, the total header
deadline reader, the stdlib refusal headers and the logging blocks, are
generated from `Scripts/_mcp_httpfront.py` and `Scripts/_mcp_logging.py` into
the file between hashed markers ([[generated-regions]],
[[0025-generate-do-not-import]], [[0029-the-http-front-is-a-domain]]). gpu-router
is a hand-declared host of the generator `Scripts/amalgamate.py:DECLARED_HOSTS`,
and each hand-written stdlib override has its row in
`tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`. Logging is structure
only: a log line carries a routed path shape or `<unrouted>`, never a header,
query, body, token or env value `Scripts/gpu-router.py:log_message`
([[0011-a-truncated-payload-carries-the-first-cookie]]).

## llm-router integration

Two [[llm-router]] kinds point at gpu-router, each with a `base_url` of
`http://<host>:<port>/m/<x>`, `allow_private` (plus `allow_loopback` on
loopback) and an `api_key` equal to the gpu-router token. For both, with a key,
`auth_header` must be omitted or `authorization`, and the key goes out as
`Authorization: Bearer <key>`; `none` or `x-api-key` with a key is a config
error naming `backends.<n>.auth_header` `Scripts/llm-router.py:_rt_cfg_backend`.

- **`sdcpp`, for an sd app.** llm-router sends its route key as the body
  `model`; the prefix is authoritative, so the route key need not equal the app
  name. llm-router refuses a prompt carrying sd-server's `sd_cpp_extra_args` tag
  `Scripts/llm-router.py:_RT_SD_MARKER`; gpu-router itself never inspects or
  filters it. Through llm-router a gpu-router `503 unload_forced` reaches the
  client as 529, and a wrong key as 502, both with llm-router's fixed text (E3,
  E4).
- **`rawllm`, for an llm app.** `/v1/chat/completions`, `/v1/embeddings`,
  `/v1/rerank` and `/v1/messages` on a `rawllm` route are relayed to the same
  path under the prefix, the client's bytes untouched, and the answer comes back
  untouched (JSON) or as whole SSE events `Scripts/llm-router.py:RawllmAdapter`.
  The route key must equal its `model`, because the body is never rewritten and
  gpu-router sniffs nothing when the prefix is there. llm-router caps how many
  such requests are in flight (`--max-llm-requests`), since gpu-router queues
  without a wait limit; and a rawllm backend in front of gpu-router needs an
  `idle_timeout` above the longest queue wait it should survive, because a
  queued request sees no byte. The details are [[llm-router]]'s *Raw LLM relay*.

The end-to-end group drives both: an `sdcpp` round trip, a swap, a forced unload
and a wrong key (E1-E4), chat, image and embedding requests interleaved across
the two kinds in strict FIFO (E5), and the rawllm chain for chat (JSON and SSE),
embeddings, rerank and messages with byte-exact bodies and no `Authorization` at
the child (E6) `tests/test_gpu_router.py:GE_CASES`.

## How it is tested

The `gpu_router` suite `tests/test_gpu_router.py` drives the router against two
adversarial stub children, as a subprocess for the CLI, exit-code, ready-file
and signal cases and as a privately loaded module, constants and seams patched,
for the cases whose observable is one of the router's own timing windows.

`tests/files/gpu_router/tf_stub_sd.py` stands in for sd-server: it serves the
four image routes with canned sd-shaped JSON, records each body and its SHA-256,
and takes behaviour flags: `--crash-on-start`, `--crash-on-start-first`,
`--start-delay`, `--bind-fail`, `--bind-fail-first`, `--crash-after`,
`--crash-on-marker` (the poison prompt), `--ignore-term`, `--spawn-grandchild`,
`--hang`, `--hang-after-accept`, `--keep-generating`, `--answer-delay`,
`--response-bytes`, `--ready-503-for`, `--dump-env` and `--control-file`, which
picks one POST's behaviour, including the raw answers the relay must refuse.

`tests/files/gpu_router/tf_stub_llm.py` stands in for ONE inferNO process: it
does not listen while it "loads" (`--start-delay`), lists its `--ids` on
`GET /v1/models` (a 404 for `--ready-404-for` seconds after the bind: bound,
not loaded), and answers the four llm routes with canned JSON or, for
`"stream": true`, an SSE stream of `--events` events `--event-delay` apart after
a silent `--think-gap`, chunked unless `--close-delimited`; `--drop-after` cuts a
stream, `--head-extra` and `--head-pad` plant hostile or oversized head lines,
and `--answer-status`, `--response-bytes`, `--hang-after-accept`,
`--crash-on-start`, `--bind-fail`, `--dump-env` and `--ignore-term` mirror the
sd stub. It records starts, pids and one JSON event line per start, request,
client departure and peer close, which is how the no-kill cases prove the SAME pid
and an unchanged start count after an abort.

The groups, in run order `tests/test_gpu_router.py:GROUP_ORDER`:

| Group | What it pins | Cases |
|---|---|---|
| L | config and CLI refusals and defaults: the listener keys and their precedence (L33-L39), the kind and its defaults, `model_ids` and the index collisions, `ready_path` / `ready_status`, the per-kind deadline caps, `child_port`, the new keys in the repr (L40-L45), the `ready_file` / `log_file` / `debug` keys and their precedence (L46-L48), a `bind` list and a repeatable `--bind` (L49) | `tests/test_gpu_router.py:GL_CASES` |
| R | routing and the sniffer (pure); the llm routes' tokens, caps, ids and kind check, the neutral refusal wording (R21-R23) | `tests/test_gpu_router.py:GR_CASES` |
| D | the pure FIFO decision; the abort matrix (D23), the strict de-chunker (D24), the stream head parser (D25), the bounded stream queue (D26) | `tests/test_gpu_router.py:GD_CASES` |
| S | spawn, readiness, the stop ladder, the group sweep, the budget; the probe path and status (S21), a fixed `child_port` and one spawn (S22) | `tests/test_gpu_router.py:GS_CASES` |
| Q-sched | one in flight, queued and forced unloads, cancel (QS8: cancelling a running request kills nothing), the admission race | `tests/test_gpu_router.py:GQS_CASES` |
| T | retries, `request_timeout`, the poison prompt, the restart budget; a squatter never sees a request (T14), the llm timeouts abort without a kill (T15, T17), shutdown mid-stream (T16) | `tests/test_gpu_router.py:GT_CASES` |
| P | auth, routing, framing, the byte-exact relay, startup lines, the error table; the config `port` and `bind` (P39, P40), the llm routes (P41), the per-kind caps with 411 first (P42), the `anthropic-*` headers (P43), the stream head allow-list (P44), an incremental chunked stream (P45), a mid-stream EOF never replayed (P46), several listeners: every address answers (P47), one bind failure aborts startup (P48), what a `peer` listener shares and keeps (P49) | `tests/test_gpu_router.py:GP_CASES` |
| Q-http | departed clients, the disconnect loop, the over-cap race, racing unloads; a client that leaves a stream kills nothing (QH6) | `tests/test_gpu_router.py:GQH_CASES` |
| U | `/v1/unload`, `?force=1`, `/v1/models`; every id listed (U19), unload through any id (U20), a force mid-stream (U21) | `tests/test_gpu_router.py:GU_CASES` |
| G | the injected front policy (ADR 0029) | `tests/test_gpu_router.py:GG_CASES` |
| E2E | llm-router (`sdcpp` and `rawllm`, a bearer key) → gpu-router → the stub apps (E1-E6, above) | `tests/test_gpu_router.py:GE_CASES` |
| A | static AST rules over the router source; a stream aborts, never kills (A18) | `tests/test_gpu_router.py:GA_CASES` |

The security review's fix round (below) added its cases red first, each in the
group of the behaviour it pins: L30 to L32 (NUL refusal, the limit ranges, the
G4 precedence), S20 (an early-reaped leader keeps its grace), QS16 (repeated
unloads coalesce), T13 (`Popen` `ValueError` leaves the app `DEAD`) and P35
to P38 (control characters in an inbound and a relayed Content-Type, `nosniff`,
the flag-only signal handler). S20 drives a fake process world rather than the
stub, because the stub's grandchild dies on SIGTERM and so could never show
the grace being given. QS8 and QH2 pinned D-12's kill on a client abort; they
were turned to pin the no-kill rule of ADR 0035.

The case count is typed once, with its per-group split, in the ledger comment
above the suite's row in `tests/run.py:SUITES`, and checked against the run;
the forge target is `gpu_router` in `project-forge.yaml`, whose description
carries the measured runtime and the timeout derived from it. As history: the
ledger read 211 when sd-router landed, the listener options moved it to 220,
and the three stage commits to 239 (2a), 251 (2b) and 252 (stage 3, E6); the
`ready_file`, `log_file` and `debug` config keys moved it to 255 (L46-L48), and
several listeners on one port to 259 (L49, P47-P49). P47 and P48 bind
`127.0.0.2` only where the host can (Linux, where all of 127/8 is up); on macOS
they exercise `127.0.0.1` alone and say so in their detail, and P48 then fails
on the unbindable second address instead of a held port. P49 needs no second
address: it builds two listeners on ephemeral `127.0.0.1` ports in-process. Gate
A1 was relaxed narrowly for the probe: a literal `GET`, with the path a literal
or the `path` parameter of `_sd_probe_status`. There is no live case for a slow
reader (D26 pins the queue only), for the handler's write-timeout path, or for
the HTTP/1.0 close-delimited branch of the stream writer. The router also sits
in the 3.10+ API scope of the dependency gate
`tests/test_py_deps.py:NEW_39_SCOPE`.

## Security review

A code-mode security review ran on sd-router on 2026-10-28: 30 raw
candidates, merged to 25, each verified independently. Verdict APPROVE, no
HIGH or MEDIUM, nine LOW and a few INFO; the rest were suppressed as declared
limits or operator-trusted config. The fix round closed the Content-Type
control characters in both directions (M6, M23), `nosniff` on relayed answers
(M7), the NUL in config and the `Popen` `ValueError` (M24), the signal-handler
deadlock (M18), most of the stale-pgid residual (M14, S-L6), the unbounded
non-force unload tickets (M19) and the config limit ranges (M20), each red
first. M10 and M11 were then fixed too, on the owner's decision, in the
canonical sources every host shares ([[generated-regions]]) rather than in the
router: `--log-file` is opened `O_NOFOLLOW` and refused unless it is a regular
file the effective uid owns, before its 0600 `fchmod`
`Scripts/_mcp_logging.py:_configure_logging`; the ready file is written through
a unique `O_EXCL|O_NOFOLLOW` temp name with no pre-remove, and only that temp
is removed on failure `Scripts/_mcp_httpfront.py:_http_write_ready_file`. Both
are pinned once on the canonical modules by group E of `generated_region`
(`log-file-symlink-refused`, `httpfront-ready-file-unique-tmp`)
`tests/test_generated_region.py`. M2 and the M17 residual stay open as declared
limits below, beside the two bits M10 and M11 leave declared.
The finding-by-finding record and the fix round's deviations are in
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]]. The stage-3 review of
2026-10-30 covered llm-router's rawllm diff, which is where its one fixed
finding lives ([[llm-router]], [[0035-one-gpu-several-apps]]).

## Declared limits

The module docstring lists the declared limits `Scripts/gpu-router.py`;
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]] carries the sd list
with its reasoning and [[0035-one-gpu-several-apps]] the apps, llm and stream
additions.

- **Single-user host.** The child listens unauthenticated on `127.0.0.1`, so
  any local uid can reach it directly and bypass the bearer
  (`Scripts/gpu-router.py:_SD_TRUST_LINE`, logged at every start).
- **Operator `env` and `args` are trusted config.** `LD_PRELOAD`, `PATH` and
  the like are not denied; the file is 0600 and owner-checked.
- **No TLS.** A non-loopback bind needs `--allow-remote --insecure-cleartext`,
  and then the bearer and the images cross the network in cleartext (D-13).
- **Scope cuts (D-8).** No keep-alive, `Content-Length` required (chunked and any
  `Transfer-Encoding` refused 411), no backoff for the first start after an
  unload, no idle auto-unload, no async job routes, no "Did you mean"
  pointer on a refused name.
- **The control reserve is a mitigation.** Connection slots, the reserve
  included, are taken before authentication, so unauthenticated slow-header or
  idle connections can hold them until the header deadline; on the default
  loopback bind only local processes can, with `--allow-remote` anyone can.
- **The connection cap is per listener.** With several `--bind` addresses each
  listener has its own `max_connections` plus the control reserve, so the
  process can hold N times that many connections. It is per listener on
  purpose: gate G2 demands exactly one `BoundedSemaphore` assignment in
  `Scripts/gpu-router.py:_SdHttpServer` `tests/test_gpu_router.py:rule_g_cap`,
  and a flood on a LAN address cannot starve `127.0.0.1`. Request admission
  stays process-wide (the one scheduler's `try_admit`), so the request-body and
  answer part of the memory worst case does not multiply; the per-connection
  control-body part does, and the docstring's figure is written for one
  listener.
- **`queue_full` counts reservations.** A completed ticket keeps its slot until
  its answer is written, so a slow reader or a slow body sender (up to the
  120 s body deadline `Scripts/gpu-router.py:_SD_BODY_DEADLINE_S`) holds request
  capacity. Queue memory is bounded by ticket count, not by wait: up to
  `max_connections` buffered bodies, each at most its route's cap.
- **Head-of-line wait (S3).** There is no queue-wait deadline: a queued request
  waits behind the head, whose cold start may take up to `start_timeout` and
  whose run up to `request_timeout` — 86400 s by default for an llm app, so one
  long stream blocks embeddings and images (accepted by the owner). The escape
  hatch is `?force=1`, which kills the child and truncates the stream.
- **A client abort kills nothing, and nothing proves the GPU is free.** The
  upstream is closed and the child stays `READY` (ADR 0035, D-12 amended).
  Whether sd-server stops generating on the closed connection is unmeasured, so
  after an sd abort the next request can reach a busy GPU (the
  `--keep-generating` stub case pins that pessimistic case); that an
  inferNO-style engine stops is the owner's statement, and the router cannot see
  it if it does not.
- **An llm deadline aborts, it does not kill.** `request_timeout`,
  `head_timeout` and `idle_timeout` close the upstream and keep the child; a
  stuck engine shows as a hung client until `?force=1`. The llm defaults are
  suggestions, not owner-confirmed numbers.
- **Not every llm 2xx streams.** A JSON answer with a `Content-Length` is
  buffered under `response_limit`, so a very large one is refused 502 rather
  than streamed.
- **A long chat body needs the prefix.** The body sniffer reads only the first
  `Scripts/gpu-router.py:_SD_SNIFF_LIMIT` bytes and refuses a longer JSON body
  without the `/m/<x>/` prefix; llm-router always sends the prefix.
- **An inferNO-style engine loads silently.** Nothing reports progress while the
  probe polls; the `[serve] Ready` log line is not read.
- **A force unload leaves queued work to cold-start again (D-3)**, and repeated
  forces on the resident model each cost a cold start.
- **A poison prompt can burn the shared restart budget.** A prompt that kills
  the child on every send is charged up to `retries + 1` times per request and
  can disable the model for every client until an unload.
- **`sd_cpp_extra_args` is forwarded unfiltered, by design.** Bodies are relayed
  byte-exact; only llm-router refuses the tag.
- **The mask + `/edits` weakness of sd-server is not repaired.** An edit with a
  mask and no `init_image` damages the image; the workaround is `init_image`
  inside `sd_cpp_extra_args`, or `/sdapi/v1/img2img` (see the ADR 0028
  addendum).
- **Readiness against the real sd-server is unverified.** The host and port
  flag names (believed `--listen-ip` / `--listen-port`), the `GET /` status while
  loading (503 assumed) and grandchild behaviour are confirmed only by the live
  smoke test (SC-7), which has not run; the release checklist runs
  `sd-server --help` first.
- **The response cap default is unmeasured (D-6).**
  `Scripts/gpu-router.py:_SD_DEFAULT_RESPONSE_LIMIT` is to be revised from the
  largest real answer.
- **The free-port race.** Without a `child_port`, between closing the probe
  socket and the child's bind another process can take the port; the readiness
  rule, the liveness check before every send and the early-exit respawn
  mitigate it. An engine that binds late (inferNO, up to about a minute) widens
  the window; a fixed `child_port` removes it and makes the port predictable,
  not more reachable (S4).
- **SIGKILL of the router can orphan a child group**; the ordered shutdown
  covers SIGTERM, SIGINT and SIGHUP only.
- **Pid-reuse residuals (S-L6), lowered.** `wait_ready` polls the child,
  which can reap an exited leader while its group still has members. The poll
  now marks the child reaped and drops an emptied group at once, and the ladder
  gives the survivors their grace (see the stop ladder above); what remains is
  a stale pgid for a group that SURVIVES, which another group could take only
  after a full pid wrap, and between the shutdown sweep's signal-0 probe and
  its SIGKILL a group could change identity (same-user processes only; EPERM
  drops other users' groups).
- **No failed-auth throttle (review M2).** Wrong bearers are not rate-limited.
  The token is checked before any body byte is read, its policy is at least
  `Scripts/gpu-router.py:_SD_TOKEN_MIN_LEN` characters with
  `Scripts/gpu-router.py:_SD_TOKEN_MIN_DISTINCT` distinct, and the host is a
  single-user loopback one.
- **What M10 and M11 leave declared.** Both are fixed in the canonical
  sources (see the security review above); two operator-chosen paths remain
  trusted. A FIFO at the `--log-file` path can still block the open, which
  passes no `O_NONBLOCK` and only rejects a non-regular file after the open
  returns `Scripts/_mcp_logging.py:_configure_logging`. The ready file's
  directory mode is not checked, and a replace between the shutdown's read and
  unlink is not seen `Scripts/_mcp_httpfront.py:_http_remove_ready_file`.
- **Readiness does not prove ownership (review M17 residual).** The probe
  checks that the child is alive after an answer on its port, not that the
  answering socket belongs to the child's pid; the same trust class as the
  single-user host above.
- **macOS log noise.** SIGTERM to a group whose only member is an unreaped
  zombie raises EPERM, logged as a WARNING and treated as gone; the behaviour
  is correct.
- **The multipart sniffer stops at its window.** A second `model` part after
  the first `Scripts/gpu-router.py:_SD_SNIFF_LIMIT` bytes is not detected (R19).
