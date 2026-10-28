---
name: sd-router
type: component
status: draft
title: sd-router — a supervisor and reverse proxy for stable-diffusion.cpp sd-server
description: The stdlib-only HTTP front that owns one GPU's stable-diffusion.cpp sd-server children -- a 0600 JSON config of models launched from an argv list, one strict FIFO with one request in flight and a swap when the head names another model, a per-model state machine with one definition of resident, queued and forced unloads that answer 200 only once the child's process group is verified gone, retries and a restart budget that disables, a closed table of OpenAI-shaped error codes, the llm-router sdcpp integration through a bearer, how it is tested against an adversarial stub child, the code-mode security review and its fix round, and what it declares rather than gates.
sources:
  - Scripts/sd-router.py
  - tests/test_sd_router.py
  - tests/files/sd_router/tf_stub_sd.py
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
---

# sd-router

`Scripts/sd-router.py` stands between image clients and stable-diffusion.cpp
`sd-server`, for a host with one GPU. Its JSON config declares models; for each
one it spawns an `sd-server` child from config data, relays image requests to
it byte-exact, restarts it after a crash, swaps it out when the queue head
names another model, and stops it on `/v1/unload` so the VRAM is freed
`Scripts/sd-router.py`. Requests run in strict FIFO order with one in flight
whatever the model, and a cold start or a swap makes the caller wait. It is
pure Python 3.9 and the standard library ([[0024-pure-python-39-and-the-stdlib]]),
and it is not an MCP server.

This page is the *shape*. The decision, the alternatives that lost and the
planner decisions D-1 to D-17 are recorded in
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]]. The image endpoints of
[[llm-router]] are its intended client, and its child supervision is a declared
adapted copy of [[mcp-proxy]]'s.

The file is nine numbered units in file order (`# ---- unit N ----`): config
and CLI, routing and the model sniffer, the scheduler's decision logic, the
supervisor, the upstream forwarder, the scheduler runtime, the HTTP front, the
handlers, and `main`. Units 1-3 are pure and name no socket, subprocess, http or
threading module `tests/test_sd_router.py:GA_CASES`.

## Config

**One 0600 file, read through `--config`.** The file is opened without
following a symlink and must be a regular file owned by the effective uid with
no group or other permission bits, at most `Scripts/sd-router.py:_SD_CONFIG_LIMIT`
bytes of UTF-8 `Scripts/sd-router.py:_sd_read_config_file`. A repeated key at
any depth is refused, as are `NaN` and the infinities
`Scripts/sd-router.py:load_config` ([[0015-ambiguity-is-the-defect]]). There is
no `--config-json`, since argv is visible in `ps` and every config holds the
bearer token `Scripts/sd-router.py:_sd_parser`. Every refusal is one
`sd-router: <reason>` stderr line and exit 2, naming the key and the rule and
never a value `Scripts/sd-router.py:_sd_refuse`.

**Top level.** `token` is required: the generated front's length and charset
rule with the floor `Scripts/sd-router.py:_SD_TOKEN_MIN_LEN`, then at least
`Scripts/sd-router.py:_SD_TOKEN_MIN_DISTINCT` distinct characters
`Scripts/sd-router.py:_sd_cfg_token`. `models` is a non-empty object whose
names match `Scripts/sd-router.py:_SD_MODEL_NAME_RE`. The optional
`body_limit` takes the command line's range
`Scripts/sd-router.py:_SD_BODY_LIMIT_RANGE` (1 KiB to 1 GiB) and the optional
`response_limit` 1 to `Scripts/sd-router.py:_SD_RESPONSE_LIMIT_MAX` (1 GiB);
unknown keys are refused by name `Scripts/sd-router.py:parse_config`. The token never appears in
a repr `Scripts/sd-router.py:Settings`.

**One model entry** `Scripts/sd-router.py:_sd_cfg_model`:

| Key | Rule |
|---|---|
| `command` | required; an absolute path to an existing regular executable file |
| `args` | required; a non-empty list of strings holding at least one `{port}` (`{host}` is optional, no other placeholder is accepted) `Scripts/sd-router.py:_sd_cfg_args` |
| `env` | optional; keys match `Scripts/sd-router.py:_SD_ENV_KEY_RE`, values are strings and never appear in a repr `Scripts/sd-router.py:ModelSpec` |
| `cwd` | optional; an absolute existing directory |
| (all four above) | a NUL anywhere in `command`, an `args` element, an `env` value or `cwd` is refused at load, since `Popen` would raise `ValueError` on it `Scripts/sd-router.py:_sd_cfg_no_nul` |
| `start_timeout`, `stop_grace`, `request_timeout` | optional seconds, each with its default and its `(low, high]` range in `Scripts/sd-router.py:_SD_MODEL_FLOATS` |
| `retries` | optional; the number of ADDITIONAL sends after a crash, default `Scripts/sd-router.py:_SD_RETRIES_DEFAULT`, at most `Scripts/sd-router.py:_SD_RETRIES_MAX` |

`{port}` and `{host}` are substituted by an explicit replace, never
`str.format`, because sd-server arguments may hold braces
`Scripts/sd-router.py:render_args`. The router owns the child's address: an
element naming one of the port or host flags in
`Scripts/sd-router.py:_SD_PORT_HOST_FLAGS`, bare or as `flag=value`, is refused
unless its value is exactly `{port}` or `{host}`. That list is a deny-list over
flag names believed to be sd-server's and not verified against it; the real
safety mechanism is readiness, a live child answering on the substituted port.
The plan's literal rule (no fixed port or host flag at all) would have refused
every real config, so the placeholder exception is an implementation decision
recorded in ADR 0034.

**Precedence with the command line.** `--max-connections` always comes from the
flag. `body_limit` from the config overrides `--body-limit`, except that a
config value equal to the default cannot be told apart from an absent one, so
the flag wins then `Scripts/sd-router.py:_sd_settings`. That precedence (G4) is
pinned by case L32 `tests/test_sd_router.py:GL_CASES`.

## Command line and startup

The flags are `--config`, `--bind` (default `127.0.0.1`), `--port` (default 0,
ephemeral), `--allow-remote`, `--insecure-cleartext`, `--ready-file`,
`--body-limit`, `--max-connections`, `--debug` and `--log-file`, with the integer
ranges `Scripts/sd-router.py:_SD_PORT_RANGE`,
`Scripts/sd-router.py:_SD_BODY_LIMIT_RANGE` and
`Scripts/sd-router.py:_SD_CONNECTIONS_RANGE` `Scripts/sd-router.py:_sd_parser`.
An unrecognized argument is refused without echoing it, because argparse would
quote it verbatim `Scripts/sd-router.py:_SdArgumentParser`.

There is no TLS. `--allow-remote` without `--insecure-cleartext` is refused
(D-13) `Scripts/sd-router.py:_sd_parse_args`, a non-loopback `--bind` without
`--allow-remote` is refused, and a bind failure exits 2 with one line
`Scripts/sd-router.py:main`. The listener binds before anything else starts; the
signal handlers (SIGTERM, SIGINT, SIGHUP, all three an ordered shutdown) are
installed before the ready file is written. A handler only sets an attribute
on `Scripts/sd-router.py:_SdStopFlag`, never `Event.set`, whose lock the main
thread may hold inside `Event.wait` when the signal lands; main polls the flag
one tick at a time `Scripts/sd-router.py:_sd_wait_stop`. At startup the router always logs
the fixed single-user-host line `Scripts/sd-router.py:_SD_TRUST_LINE`, and with
both remote flags the cleartext warning `Scripts/sd-router.py:_SD_CLEARTEXT_LINE`.
The exit codes are 0 for a normal or signal-initiated exit and 2 for any config,
command-line or startup refusal (module docstring `Scripts/sd-router.py`).

The ordered shutdown marks the server closing (new requests get 503
`shutting_down`), closes the scheduler (queued tickets 503 `queue_closed`, the
running one aborted, a ladder in progress finished, the resident child stopped,
then a last-resort sweep of the spawn registry), drains handler threads for at
most `Scripts/sd-router.py:_SD_DRAIN_S`, closes the listener and removes the
ready file only while it still holds this pid
`Scripts/sd-router.py:_sd_shutdown`.

## Request flow

client → sd-router (HTTP/1.1, bearer) → one scheduler thread → supervisor →
`sd-server` child on `127.0.0.1:<ephemeral>` (no auth).

**Routes.** The image routes are one mapping, `POST /v1/images/generations`,
`/v1/images/edits`, `/sdapi/v1/txt2img` and `/sdapi/v1/img2img`, each also under
`/m/<model>/` `Scripts/sd-router.py:ROUTES`. The control routes are `GET
/v1/models` and `POST /v1/unload` (also under `/m/<model>/`). Every route needs
the bearer; anything else is 404 `route_not_found`. Async job routes and
sd-server's own model-management routes are not proxied.

**The pipeline, in order** `Scripts/sd-router.py:_sd_pipeline`:

1. `_precheck`: the bearer first — exactly one `Authorization` header, scheme
   `Bearer ` matched case-insensitively, the token compared as UTF-8 bytes with
   `hmac.compare_digest` `Scripts/sd-router.py:_sd_bearer_ok`; a failure is 401
   with `WWW-Authenticate: Bearer`. Then header defects, then on a loopback bind
   a `Host` that must be exactly `127.0.0.1:<port>` or `localhost:<port>`, then
   any `Origin` at all; those three are 400 `invalid_request`
   `Scripts/sd-router.py:_precheck`. A stopping router answers 503
   `shutting_down`.
2. A control path leaves the pipeline here for the control handler, before
   routing and without an admission slot `Scripts/sd-router.py:_sd_control`.
3. Routing on the raw request target, never the stdlib's normalised `self.path`:
   any `%`, `//`, `.` or `..` segment is 404, a wrong method 405, any query on an
   image route 400, an unknown prefix model 404 `model_unknown`, an `/sdapi/`
   route without a prefix 400 `model_required`
   `Scripts/sd-router.py:_sd_route_head`.
4. Header-only framing: any `Transfer-Encoding` is 411, so is anything but
   exactly one decimal `Content-Length`; over the body limit is 413; a
   Content-Type holding any control character (an obs-fold CRLF, a tab and DEL
   included) is 400 `invalid_request` on every image path, the `/m/<model>/`
   prefix included, because that value crosses the hop
   `Scripts/sd-router.py:_sd_has_ctl`; a Content-Type the route does not take
   is 415 `Scripts/sd-router.py:_sd_framing`
   `Scripts/sd-router.py:_SD_ROUTE_MEDIA`.
   No body byte has been read and no slot is held.
5. Admission: `try_admit()` refuses with 503 `queue_full` and `Retry-After: 1`
   once queued plus running image tickets fill `--max-connections`
   `Scripts/sd-router.py:try_admit`.
6. `Expect: 100-continue` is answered only now.
7. The body is read under the total deadline
   `Scripts/sd-router.py:_SD_BODY_DEADLINE_S`; expiry or a short body is 400
   `invalid_request` with the slot released `Scripts/sd-router.py:_sd_read_body`.
8. Without a prefix the body's `model` picks the model
   `Scripts/sd-router.py:route_request`.
9. The ticket is submitted with its reservation, the handler waits on it while
   polling for a departed client every `Scripts/sd-router.py:_SD_CLIENT_POLL_S`,
   then writes the answer.

**Model selection.** The `/m/<model>/` prefix is authoritative: with it the body
is never sniffed and its `model` is ignored (D-11), which is what lets
llm-router send its own route key as `model`. Without a prefix, an OpenAI-style
route falls back to the body: a JSON `model`, or exactly one canonical
`name="model"` multipart part, read from at most the first
`Scripts/sd-router.py:_SD_SNIFF_LIMIT` bytes and never modified; every spelling
two readers could read two ways is a 400, and so is a JSON body longer than the
window `Scripts/sd-router.py:sniff_model`. It is a smaller hand-written analogue
of llm-router's image field readers: it copies their behaviour, none of their
code.

**The proxy hop.** `Scripts/sd-router.py:_sd_send` is the one upstream send
site. Only the client's first `Content-Type` crosses; `Authorization`, `Cookie`,
`Host`, `Expect` and hop-by-hop headers are dropped, so the router token never
reaches the child `Scripts/sd-router.py:_sd_forward_headers`. The request line
is rebuilt from the route table, the body sent byte-exact with a recomputed
`Content-Length`, `Accept-Encoding: identity` and `Connection: close`. The whole
answer is buffered under `response_limit` before any byte reaches the client, so
a retry can never replay half an answer. A 1xx, a 3xx, an encoded or malformed
answer, one over the limit, or one whose Content-Type holds a control
character, is 502 `upstream_failed` `Scripts/sd-router.py:_sd_parse_answer`.
The child's own answers, its non-2xx ones included, are relayed verbatim with
the router's own `Content-Length`, `Connection: close` and
`X-Content-Type-Options: nosniff` `Scripts/sd-router.py:_sd_write_result`;
`/v1/models` and the unload answers go through the same writer. Every exchange is
one request per connection: no keep-alive.

## The scheduler and the state machine

**One thread, one Condition.** A single scheduler thread owns the per-model
state and the GPU; handlers only enqueue a ticket and wait on it. Every write of
a model's state, its zombie flag, its disabled flag, its budget and backoff, of
`running`, of the queue and of the admission counter happens in a short section
under one `threading.Condition`, with no logging and no blocking call inside
`Scripts/sd-router.py:Scheduler`; the lock discipline is a static gate
`tests/test_sd_router.py:GA_CASES` (A17). The decision itself is a pure function
of a head ticket, the model snapshots, the resident model and a clock value
passed in `Scripts/sd-router.py:decide`, which is why it is tested without
sockets or processes.

**States.** `STOPPED`, `STARTING`, `READY`, `STOPPING`, `DEAD`
`Scripts/sd-router.py:ModelState`. Disabled is a per-model flag beside the
budget, and `group_alive` marks a DEAD model whose group survived its ladder
(a zombie) `Scripts/sd-router.py:ModelView`.

**Resident, defined once (D-17).** A model is resident when it is `STARTING`,
`READY` or `STOPPING`, or `DEAD` with a live group; a disabled flag never changes
that `Scripts/sd-router.py:_sd_is_resident`. At most one model is resident at a
time (QS13 pins it).

**Transitions** `Scripts/sd-router.py:_take` `Scripts/sd-router.py:_execute`:

- The head is popped and `STOPPED → STARTING` written in one locked section, so
  nothing looks non-resident while a start is in progress. A head for another
  model than the resident one is a swap: the resident model's stop ladder runs
  first `Scripts/sd-router.py:_exec_swap`.
- `STARTING → READY` once the child's port answers `GET /` with any status but
  503, with the child still alive after the answer; 503 means "still loading"
  `Scripts/sd-router.py:wait_ready`. A child that exits within
  `Scripts/sd-router.py:_SD_EARLY_EXIT_S` of its spawn is taken for a port
  collision and respawned on a new port, at most
  `Scripts/sd-router.py:_SD_PORT_TRIES` spawns `Scripts/sd-router.py:launch`.
  A spawn that `Popen` refuses (`OSError`, `SubprocessError` or `ValueError`)
  is a failed start, not a crash of the scheduler step. A start that fails or
  times out leaves the model `DEAD`, never `STARTING`, charges the budget and
  fails the head 502 `model_start_failed` or 504 `model_start_timeout`
  `Scripts/sd-router.py:_exec_start`.
- A crash during a send (connection error or child exit with no abort reason)
  makes the model `DEAD` with `group_alive` set, charges the budget, runs the
  ladder, and re-queues the SAME ticket at the head while
  `Scripts/sd-router.py:_sd_may_retry` allows; otherwise 502 `model_crashed`
  `Scripts/sd-router.py:_send_failed`. `retries=1` means at most two sends.
- `request_timeout` runs from dispatch to the fully buffered answer; its expiry
  kills the child, answers 504 `upstream_timeout`, is never retried and is
  charged like a crash.
- An abort by a force, a departed client or shutdown runs the ladder and is
  never a crash: no charge, no retry. A refused answer (`upstream_failed`) is no
  crash either: the child answered, so it stays `READY`.
- A group that survives SIGKILL leaves the model `DEAD` with `group_alive` set.
  While such a zombie exists every request that reaches the head fails 500
  `unload_failed`, and the group is re-probed with signal 0 before each pop
  until it is gone `Scripts/sd-router.py:_reprobe`.

**The restart budget.** `Scripts/sd-router.py:_SD_RESTART_BUDGET` crashes inside
a sliding `Scripts/sd-router.py:_SD_RESTART_WINDOW_S` window set the model's
disabled flag `Scripts/sd-router.py:Budget`, and its tickets fail 503
`model_disabled` until an unload clears it. Between consecutive automatic
restarts the backoff `Scripts/sd-router.py:_SD_BACKOFF_S` applies with up to
`Scripts/sd-router.py:_SD_BACKOFF_JITTER` jitter; the first start after an
unload or an idle period is immediate `Scripts/sd-router.py:_sd_backoff_s`.

**Admission and memory.** One counter of reservations bounds queued plus
running image tickets by `--max-connections` `Scripts/sd-router.py:try_admit`.
The handler releases a completed ticket's reservation only after it wrote the
answer, a queued cancel releases at once, and a running ticket whose client left
is released by the scheduler after its ladder `Scripts/sd-router.py:_sd_settle`
`Scripts/sd-router.py:cancel`. The connection semaphore holds
`--max-connections` plus `Scripts/sd-router.py:_SD_CONTROL_RESERVE` slots, so
the control routes keep connections while image traffic is at its cap
`Scripts/sd-router.py:_SdHttpServer`. The module docstring declares the memory
worst case as `max_connections x (body_limit + response_limit)` plus control
bodies of at most `Scripts/sd-router.py:_SD_CONTROL_BODY_LIMIT` per connection,
about 768 MiB at the defaults `Scripts/sd-router.py:_SD_DEFAULT_MAX_CONNECTIONS`
`Scripts/sd-router.py:_SD_DEFAULT_BODY_LIMIT`
`Scripts/sd-router.py:_SD_DEFAULT_RESPONSE_LIMIT`; the operator sizes both flags
against RAM.

**Children.** A child is spawned in its own session from an argv list, never a
shell, with stdio on `DEVNULL` — the file's only `Popen`
`Scripts/sd-router.py:start`. Its environment is `PATH`, `HOME` and `LANG` plus
the config `env`, never the router's own and never the token (D-1)
`Scripts/sd-router.py:_sd_child_env`. The port is picked by binding port 0 on
loopback and closing it `Scripts/sd-router.py:_sd_free_port`.

**The stop ladder: kill before reap.** SIGTERM to the group, a wait of
`stop_grace` for the leader WITHOUT reaping it, SIGKILL to the group while the
leader is still an unreaped zombie (so its pid and the pgid cannot be recycled),
reap, then confirm the group gone with signal 0 within
`Scripts/sd-router.py:_SD_KILL_STEP_S` `Scripts/sd-router.py:stop`. The
unreaped wait is a declared adapted copy of sbx's, chosen by `sys.platform`: a
kqueue `NOTE_EXIT` on darwin, `os.waitid(..., WNOWAIT)` elsewhere
`Scripts/sd-router.py:_sd_wait_exit_unreaped`. A leader that a readiness
`poll()` already reaped has no zombie left to pin the pgid: the poll marks the
child reaped and probes the group with signal 0 at once, dropping an emptied
group from the registry `Scripts/sd-router.py:_poll`, and the ladder for such a
child gives a surviving member the full `stop_grace` by watching the group
before its SIGKILL, and sends none once it is empty
`Scripts/sd-router.py:_wait_group_empty`. A confirmed-gone pgid leaves the
spawn registry at once; the shutdown sweep re-probes each remaining entry right
before its SIGKILL, and ESRCH or EPERM drops it unsignalled
`Scripts/sd-router.py:kill_all`.

## Unload

`POST /v1/unload` selects its target from `?model=<name>`, the
`/m/<model>/v1/unload` prefix, or by default the resident model. The body must
be empty or `{}`; the query is split by hand with no percent-decoding, and only
`model` and `force=1`, each at most once, are accepted (D-16)
`Scripts/sd-router.py:_sd_unload_query`. A prefix and a `?model=` naming
different models are 400, and an unknown model is 404 `model_unknown` before
anything is queued `Scripts/sd-router.py:_sd_handle_unload`. Control requests
read at most `Scripts/sd-router.py:_SD_CONTROL_BODY_LIMIT` bytes and never take
an admission slot `Scripts/sd-router.py:_sd_control_body`. A client that leaves
while its unload waits does not cancel it.

**Queued (default).** The unload ALWAYS enters the FIFO, so it waits for every
earlier request; its default target is the resident model when it reaches the
head. A resident target runs the ladder and answers 200 `{"unloaded": true,
"model": ...}` only after the group is confirmed gone, or 500 `unload_failed`;
a non-resident target answers 200 `{"unloaded": false}` at once and has its
disabled flag, budget and backoff cleared `Scripts/sd-router.py:_exec_unload`.
Because an unload survives its client, a repeated one may not grow the queue: a
non-force unload naming the same model (the default included) as the queue's
LAST ticket, itself a non-force unload, is attached to that ticket and both
callers read its one result. Only the tail is matched, so FIFO order is
unchanged and an unload behind a later request still gets its own ticket
`Scripts/sd-router.py:submit_unload`.

**`?force=1` (scoped to its target).** A force for the RESIDENT model jumps the
FIFO through a separate list, aborts a running request for that model (503
`unload_forced`), stops a start in progress, and answers 200 only after the
ladder; concurrent forces for one model share one ladder. A force for a
non-resident model is executed by `submit_unload` itself under the lock: the
running request is untouched, the target's disabled flag, budget and backoff
are cleared, and the ticket comes back already finished 200 `{"unloaded":
false}` `Scripts/sd-router.py:submit_unload`. Queued requests stay queued and
cold-start the model again after a forced stop (D-3).

A stop of the resident model clears the crash history only when the model was
disabled; a healthy model keeps its budget (T10)
`Scripts/sd-router.py:_reenable_locked`. The plan's FR-5 said unload always
clears; this deviation is recorded in ADR 0034.

**`GET /v1/models`** lists every configured model in config order with
llm-router's image-route keys (`id`, `object`, `created`, `owned_by`,
`modality: "image"`) plus `state`: `disabled` when the flag is set and the model
is not resident, else the state name. Command, args, env, cwd, port and pid are
never shown `Scripts/sd-router.py:_sd_models_entry`.

## The closed error table

Every refusal is `{"error": {"message", "type", "code"}}` with a code from one
table `Scripts/sd-router.py:_SD_ERROR_STATUS`, a `type` per status
`Scripts/sd-router.py:_SD_ERROR_TYPE`, and a fixed message that names a rule and
never a value `Scripts/sd-router.py:_sd_error`. A code outside the table raises
`KeyError`, so no answer can carry one.

| Code | Status | When |
|---|---|---|
| `unauthorized` | 401 | missing, repeated or wrong bearer |
| `route_not_found` | 404 | no such route |
| `method_not_allowed` | 405 | a known path, a wrong method |
| `length_required` | 411 | `Transfer-Encoding`, or not exactly one decimal `Content-Length` |
| `body_too_large` | 413 | over the body limit, or a control body over its cap |
| `unsupported_media_type` | 415 | a Content-Type the route does not take |
| `invalid_request` | 400 | header defects, `Host`, `Origin`, a control character in the Content-Type, a query on an image route, a refused sniff, a body deadline or short body, a bad unload body or query |
| `model_required` | 400 | no model resolvable |
| `model_unknown` | 404 | a model not configured |
| `model_crashed` | 502 | the child died and the retries are spent |
| `model_start_failed` | 502 | the child exited before it became ready |
| `model_start_timeout` | 504 | `start_timeout` elapsed |
| `model_disabled` | 503 | the restart budget is spent; an unload clears it |
| `unload_forced` | 503 | the request was aborted by `?force=1` |
| `unload_failed` | 500 | a group survived SIGKILL; also every request while a zombie holds the GPU |
| `upstream_timeout` | 504 | `request_timeout` elapsed before the whole answer |
| `queue_full` | 503 | the image cap is full; `Retry-After: 1` |
| `upstream_failed` | 502 | a 1xx, 3xx, encoded, malformed or oversized answer, or one whose Content-Type holds a control character; also a bug inside the handler |
| `shutting_down` | 503 | the router is stopping |
| `queue_closed` | 503 | the scheduler closed while the ticket was queued |

The stdlib's own refusals are mapped into the same table with fixed text: 501
becomes 405 `method_not_allowed`, 400, 414, 431 and 505 become 400
`invalid_request` `Scripts/sd-router.py:_SD_STDLIB_REFUSAL`. A `HEAD` has no
handler, so it gets that 405 before the bearer is checked. The table has no 403
and its only 500 is `unload_failed`, so a handler bug answers 502
`upstream_failed` `Scripts/sd-router.py:_sd_handle`. The one answer outside the
envelope comes from the generated front: a connection over the semaphore's
capacity gets a body-less 503 and is closed
`Scripts/sd-router.py:process_request`. The table is pinned twice, by P34 and by
A9, which requires it to equal the set of codes the source uses
`tests/test_sd_router.py:GA_CASES`.

## Generated regions

The front's identical pieces, the token check, the ready file, the total header
deadline reader, the stdlib refusal headers and the logging blocks, are
generated from `Scripts/_mcp_httpfront.py` and `Scripts/_mcp_logging.py` into
the file between hashed markers ([[generated-regions]],
[[0025-generate-do-not-import]], [[0029-the-http-front-is-a-domain]]). sd-router
is a hand-declared host of the generator `Scripts/amalgamate.py:DECLARED_HOSTS`,
and each hand-written stdlib override has its row in
`tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`. Logging is structure
only: a log line carries a routed path shape or `<unrouted>`, never a header,
query, body, token or env value `Scripts/sd-router.py:log_message`
([[0011-a-truncated-payload-carries-the-first-cookie]]).

## llm-router integration

A [[llm-router]] backend of kind `sdcpp` points at one sd-router model with
`base_url` `http://<host>:<port>/m/<model>`, `allow_private` (plus
`allow_loopback` on loopback) and an `api_key` equal to the sd-router token.
For `sdcpp` with a key, `auth_header` must be omitted or `authorization`, and
the key goes out as `Authorization: Bearer <key>`; `none` or `x-api-key` with a
key is a config error naming `backends.<n>.auth_header`
`Scripts/llm-router.py:_rt_cfg_backend`. llm-router sends its route key as the
body `model`; the prefix is authoritative, so the route key need not equal the
sd-router model name. llm-router refuses a prompt carrying sd-server's
`sd_cpp_extra_args` tag `Scripts/llm-router.py:_RT_SD_MARKER`; sd-router itself
never inspects or filters it. Through llm-router an sd-router `503
unload_forced` reaches the client as 529, and a wrong key as 502, both with
llm-router's fixed text (E3, E4).

## How it is tested

The `sd_router` suite `tests/test_sd_router.py` drives the router against an
adversarial stub child `tests/files/sd_router/tf_stub_sd.py`, as a subprocess
for the CLI, exit-code, ready-file and signal cases and as a privately loaded
module, constants and seams patched, for the cases whose observable is one of
the router's own timing windows. The stub serves the four image routes with
canned sd-shaped JSON, records each body and its SHA-256, and takes behaviour
flags: `--crash-on-start`, `--crash-on-start-first`, `--start-delay`,
`--bind-fail`, `--bind-fail-first`, `--crash-after`, `--crash-on-marker` (the
poison prompt), `--ignore-term`, `--spawn-grandchild`, `--hang`,
`--hang-after-accept`, `--keep-generating`, `--answer-delay`,
`--response-bytes`, `--ready-503-for`, `--dump-env` and `--control-file`, which
picks one POST's behaviour, including the raw answers the relay must refuse.

The groups, in run order `tests/test_sd_router.py:GROUP_ORDER`:

| Group | What it pins | Cases |
|---|---|---|
| L | config and CLI refusals and defaults | `tests/test_sd_router.py:GL_CASES` |
| R | routing and the sniffer (pure) | `tests/test_sd_router.py:GR_CASES` |
| D | the pure FIFO decision | `tests/test_sd_router.py:GD_CASES` |
| S | spawn, readiness, the stop ladder, the group sweep, the budget | `tests/test_sd_router.py:GS_CASES` |
| Q-sched | one in flight, queued and forced unloads, cancel, the admission race | `tests/test_sd_router.py:GQS_CASES` |
| T | retries, `request_timeout`, the poison prompt, the restart budget | `tests/test_sd_router.py:GT_CASES` |
| P | auth, routing, framing, the byte-exact relay, startup lines, the error table | `tests/test_sd_router.py:GP_CASES` |
| Q-http | departed clients, the disconnect loop, the over-cap race, racing unloads | `tests/test_sd_router.py:GQH_CASES` |
| U | `/v1/unload`, `?force=1`, `/v1/models` | `tests/test_sd_router.py:GU_CASES` |
| G | the injected front policy (ADR 0029) | `tests/test_sd_router.py:GG_CASES` |
| E2E | llm-router (`sdcpp`, a bearer key) → sd-router → two stub models | `tests/test_sd_router.py:GE_CASES` |
| A | static AST rules over the router source | `tests/test_sd_router.py:GA_CASES` |

The security review's fix round (below) added its cases red first, each in the
group of the behaviour it pins: L30 to L32 (NUL refusal, the limit ranges, the
G4 precedence), S20 (an early-reaped leader keeps its grace), QS16 (repeated
unloads coalesce), T13 (`Popen` `ValueError` leaves the model `DEAD`) and P35
to P38 (control characters in an inbound and a relayed Content-Type, `nosniff`,
the flag-only signal handler). S20 drives a fake process world rather than the
stub, because the stub's grandchild dies on SIGTERM and so could never show
the grace being given.

The case count is typed once, with its per-group split, in the ledger comment
above the suite's row in `tests/run.py:SUITES`, and checked against the run;
the forge target is `sd_router` in `project-forge.yaml`. The router also sits in
the 3.10+ API scope of the dependency gate `tests/test_py_deps.py:NEW_39_SCOPE`.

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
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]].

## Declared limits

The module docstring lists the declared limits `Scripts/sd-router.py`, and
[[0034-sd-router-a-supervisor-and-proxy-for-sd-server]] carries the full list
with its reasoning.

- **Single-user host.** The child listens unauthenticated on `127.0.0.1`, so
  any local uid can reach it directly and bypass the bearer
  (`Scripts/sd-router.py:_SD_TRUST_LINE`, logged at every start).
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
- **`queue_full` counts reservations.** A completed ticket keeps its slot until
  its answer is written, so a slow reader or a slow body sender (up to the
  120 s body deadline `Scripts/sd-router.py:_SD_BODY_DEADLINE_S`) holds image
  capacity.
- **Head-of-line wait.** There is no queue-wait deadline: a queued request waits
  behind the head, whose cold start may take up to `start_timeout` and whose run
  up to `request_timeout`.
- **A client abort costs a cold start (D-12).** Closing the socket may not stop
  sd-server generating, so a departed client's running request kills the child
  and the next request starts it again; it is not charged as a crash. A client
  whose answer was already complete kills nothing.
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
  `Scripts/sd-router.py:_SD_DEFAULT_RESPONSE_LIMIT` is to be revised from the
  largest real answer.
- **The free-port race.** Between closing the probe socket and the child's bind
  another process can take the port; the readiness rule and the early-exit
  respawn mitigate it.
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
  `Scripts/sd-router.py:_SD_TOKEN_MIN_LEN` characters with
  `Scripts/sd-router.py:_SD_TOKEN_MIN_DISTINCT` distinct, and the host is a
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
  the first `Scripts/sd-router.py:_SD_SNIFF_LIMIT` bytes is not detected (R19).
