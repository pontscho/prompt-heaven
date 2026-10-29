---
name: 0034-sd-router-a-supervisor-and-proxy-for-sd-server
type: adr
status: active
title: sd-router -- a supervisor and reverse proxy for sd-server
description: Decision to put one stdlib-only file, Scripts/sd-router.py, in front of stable-diffusion.cpp sd-server as a supervisor and reverse proxy -- one scheduler thread and one Condition, an allowlisted route table, strict FIFO with one request in flight on the single GPU, process-group kill with a verified exit before an unload answers, and a declared host of the generated HTTP front (ADR 0029, 0025) -- with the alternatives rejected, the measurements, the planner decisions D-1 to D-17 as an overrule list with their costs (D-11 and D-12 awaiting user sign-off), the twelve implementation deviations from the plan, the threat table, the llm-router sdcpp credential phase that supersedes ADR 0028's no-credential rule, the declared limits, and the code-mode security review (APPROVE, nine LOW) with its red-first fix round and that round's three deviations.
sources:
  - Scripts/gpu-router.py
  - Scripts/gpu-router.py:Scheduler
  - Scripts/gpu-router.py:Supervisor
  - Scripts/gpu-router.py:decide
  - Scripts/gpu-router.py:ROUTES
  - Scripts/gpu-router.py:_sd_send
  - Scripts/gpu-router.py:_SdHandler
  - Scripts/llm-router.py:_rt_cfg_backend
  - Scripts/amalgamate.py:DECLARED_HOSTS
  - tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS
  - tests/test_gpu_router.py
verified:
  commit: 91ff40b
  date: 2026-10-28
links:
  - 0028-route-by-model-translate-at-the-edge
  - 0029-the-http-front-is-a-domain
  - 0025-generate-do-not-import
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0024-pure-python-39-and-the-stdlib
  - 0027-the-proxy-relays-it-never-composes
  - 0014-a-canonical-source-is-a-domain
  - llm-router
  - mcp-proxy
  - generated-regions
  - gpu-router
---

# ADR 0034: sd-router -- a supervisor and reverse proxy for sd-server

Status: accepted 2026-10-28 as the design the code in the working tree
implements, on top of `12eed32`, and not yet committed when this page was
written. The page stays `draft` until that commit exists; the commit is then
recorded in `verified` and the page promoted, after which it is append-only.
The functional requirements FR-1 to FR-12 are Zoltán's (roadmap item R-0112);
D-1 to D-17 are the planner's and are listed below so each can be overruled.
Two of them, D-11 and D-12, need explicit user sign-off. It supersedes one
statement of [[0028-route-by-model-translate-at-the-edge]] (the sdcpp kind
refuses every credential) and that ADR's remark that a separate `sd-router.py`
"is not being built now"; both get their pointer as a dated addendum there.
Every number below is a measurement or a count at the moment of the decision.
The component page is [[sd-router]] ([docs/components/sd-router.md](../components/sd-router.md)).

## Context: one GPU, several model files, a server that loads one

sd-server serves exactly one model, loaded at start, has no authentication and
no unload route. The operator wants several models on one GPU, switched on
demand, and the VRAM back when nothing is generating. llm-router's sdcpp kind
([[llm-router]]) speaks to one already-running sd-server and stops there by
decision ([[0028-route-by-model-translate-at-the-edge]]). Something has to own
the child process: start it from config, know when it is ready, kill it with
certainty, and serialize the GPU.

## Decision

### One file, one scheduler thread, one Condition

`Scripts/sd-router.py` is a single stdlib-only file
([[0024-pure-python-39-and-the-stdlib]]) in nine numbered units; units 1-3
(config, routing and sniffer, scheduler decisions) are pure and reference no
socket, subprocess, http or threading name, which group A pins by AST. One
scheduler thread owns the per-model state machine and the GPU
`Scripts/sd-router.py:Scheduler`; handlers only enqueue a ticket under the one
`threading.Condition` and wait on the ticket's Event outside the lock. The
decision itself is a pure function over plain snapshots
`Scripts/sd-router.py:decide`, so FIFO, swap and unload ordering are tested
without sockets or processes. No blocking call and no log call happen under the
lock.

### Strict FIFO, one request in flight

One request runs at a time regardless of model. A swap happens when the queue
head targets another model; a cold start or swap blocks and waits, bounded per
model by `start_timeout`. "Resident" has one definition: STARTING, READY,
STOPPING, or DEAD with a live process group `Scripts/sd-router.py:ModelView`;
at most one model is resident. DISABLED is a per-model flag next to the crash
budget `Scripts/sd-router.py:Budget`, not a state.

### An allowlisted route table

Four synchronous image routes, each bare or under `/m/<model>/`, map to a kind
`Scripts/sd-router.py:ROUTES`; `GET /v1/models` and `POST /v1/unload` are the
control routes. Everything else is 404 `route_not_found`. The path is matched by
exact string equality, never percent-decoded, and the upstream request line is
rebuilt from the table's own key `Scripts/sd-router.py:route_request`. The
`/m/<model>/` prefix is authoritative; without it an OpenAI route falls back to
a strict, canonical-only body sniffer `Scripts/sd-router.py:sniff_model`.
Refusals come from a closed table of 20 codes rendered as
`{"error":{"message","type","code"}}` `Scripts/sd-router.py:_sd_error`.

### Group kill with a verified exit

The child is spawned with `start_new_session=True` from an argv list, never a
shell, in the only unit that calls `Popen` `Scripts/sd-router.py:Supervisor`.
Stopping it is SIGTERM to the group, `stop_grace`, SIGKILL to the group, then a
confirmation that the group is empty (signal 0 raising ESRCH). The group is
signalled while the leader is still an unreaped zombie
`Scripts/sd-router.py:_sd_wait_exit_unreaped`, so the pgid cannot have been
recycled; the wait is selected by `sys.platform` (kqueue `NOTE_EXIT` on darwin,
`os.waitid(WNOWAIT)` polling elsewhere), a declared adapted copy of
`ClaudeCode/skills/sandbox-run/scripts/sbx:_wait_exit_unreaped`. An unload answers
200 only after that confirmation; a group that survives SIGKILL makes the model
a zombie and the answer 500 `unload_failed`. The crash budget (5 crashes in
600 s, then DISABLED) and the backoff ladder are mcp-proxy's numbers
([[mcp-proxy]], [[0027-the-proxy-relays-it-never-composes]]).

### A declared host of the generated front

sd-router is not a canonical source. It is a hand-declared host
`Scripts/amalgamate.py:DECLARED_HOSTS` of eight generated regions from
`Scripts/_mcp_httpfront.py` and `Scripts/_mcp_logging.py`, the same block names
llm-router takes ([[0029-the-http-front-is-a-domain]],
[[0025-generate-do-not-import]], [[generated-regions]]). Its six hand-written
stdlib overrides on `_SdHttpServer` and `_SdHandler` are declared row by row
`tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`. Logging is
structure-only ([[0011-a-truncated-payload-carries-the-first-cookie]]): no body,
prompt, token or `env` value in a log line, an error body or a repr.

## Alternatives rejected

These are the plan's Section 4.

1. A per-model supervisor thread with its own lock. A cross-model swap needs a
   global lock anyway, and two lock tiers invite deadlock between a queued
   unload, a swap and a crash handler.
2. A `Semaphore(1)` with thread-per-request. No defined FIFO order, no place for
   swap-on-head-change, and unload ordering would be implicit.
3. An async or selectors event loop. The fleet's fronts are thread-based and
   generated from `Scripts/_mcp_httpfront.py`; an event loop cannot reuse them.
4. Lifting mcp-proxy's ChildSlot into a canonical source. Its child is a stdio
   MCP client; this one needs a port, readiness and a force abort, and one new
   consumer would need many seams ([[0014-a-canonical-source-is-a-domain]],
   [[0027-the-proxy-relays-it-never-composes]]). The supervisor is a declared
   adapted copy; revisit if a third supervisor appears.
5. A shell launch (`command` string, `shell=True`) and a `--config-json` flag.
   An injection surface, and secrets visible in `ps` (Zoltán's decision 9).
6. A stream-through proxy. The retry decision needs the buffered request, a
   partial answer must never reach the client and then be retried, and
   sd-server answers are single JSON documents. The whole answer is buffered
   before the first client byte `Scripts/sd-router.py:_sd_send`.
7. Proxying every sd-server route and the async job routes in v1. The
   model-management routes are unauthenticated and would be exposed; the route
   table is one mapping, so a job route is a later additive row.
8. Idle auto-unload in v1. The unload route is the caller's lever.

## Measurements

- flux-2-klein-9b-Q8_0 on the target host: 1.7-3 s per request, cold start about
  20 s (measured before planning; recorded in the plan's assumptions). The
  cold start still has to be re-measured through sd-router in the live smoke
  (SC-7). llm-router's 300 s idle
  read `Scripts/llm-router.py:_IDLE_TIMEOUT_S` comfortably covers a cold start,
  provided sd-router sends nothing before the final answer head.
- Suite `sd_router`: 201 typed cases (A 17, D 22, G 6, L 29, P 34, Q-http 5,
  Q-sched 15, R 20, S 19, T 12, U 18, E2E 4), about 92.2 s measured, forge
  timeout 300 = max(300, ceil(1.5 x 92.2 / 60) x 60) `tests/run.py:run_sd_router`.
- Collateral counts: `llm_router` 459 to 460 (R14 new), `py_deps` 64 to 65 (the
  new `NEW_39_SCOPE` entry `tests/test_py_deps.py:NEW_39_SCOPE`).

## Planner decisions D-1 to D-17 (overrule list)

Each is the planner's choice, not Zoltán's; each can be overruled.

- D-1 Child environment: a minimal fixed environment (`PATH=/usr/bin:/bin`,
  `HOME`, `LANG`) plus the config `env`, never the router's `os.environ`
  `Scripts/sd-router.py:_sd_child_env`. Cost: GPU library paths
  (`LD_LIBRARY_PATH`, `DYLD_*`, CUDA/Metal variables) must be declared in `env`.
- D-2 Child stdout/stderr to `DEVNULL`. Cost: a load failure shows only as an
  early exit; `--debug` logs the exit status, never output.
- D-3 Force unload leaves queued normal requests queued; they cold-start the
  model again afterwards. Cost: one more cold start after a force.
- D-4 Only image tickets (queued plus running) count against
  `--max-connections`; queued unloads are bounded by the connection reserve.
- D-5 `POST /m/<model>/v1/unload` as an alias of `?model=<name>`.
- D-6 Response cap default 64 MiB `Scripts/sd-router.py:_SD_DEFAULT_RESPONSE_LIMIT`,
  the figure ADR 0028 records for llm-router's sd answer bound. Cost: unmeasured
  for sd-router; the live smoke must record the largest answer.
- D-7 Closed-table additions beyond Zoltán's list (`unauthorized`,
  `method_not_allowed`, `length_required`, `body_too_large`,
  `unsupported_media_type`, `invalid_request`) and `model_dead` folded into
  `model_crashed`.
- D-8 Scope cuts: no keep-alive, Content-Length required, no backoff for the
  first start after an unload, no `_did_you_mean`.
- D-9 The client's `Authorization` is never forwarded; the child sees no token.
- D-10 llm-router's sdcpp key travels only as `Authorization: Bearer`;
  `x-api-key` for sdcpp is refused.
- D-11 The `/m/<model>/` prefix is authoritative and the body `model` is not
  cross-checked; `model_mismatch` left the closed table. Amends FR-2, because
  llm-router sends its route key as the body `model`. Overrule path: restore the
  cross-check only together with a hard, documented naming rule.
- D-12 A client abort of a running request kills the child (stop ladder, group
  verified gone); chosen over tainting the model until idle, since sd-server
  exposes no reliable idle signal. Cost: every abandoned request costs the next
  caller a cold start, and it does not feed the crash budget.
- D-13 `--allow-remote` is refused without `--insecure-cleartext`; with both, one
  WARNING line at startup `Scripts/sd-router.py:_SD_CLEARTEXT_LINE`.
- D-14 Defaults: `--body-limit` 32 MiB, `--max-connections` 8, control reserve 4
  `Scripts/sd-router.py:_SD_CONTROL_RESERVE`, control bodies at most 4 KiB.
- D-15 Body-read total deadline 120 s `Scripts/sd-router.py:_SD_BODY_DEADLINE_S`;
  expiry is 400 `invalid_request` and releases the slot.
- D-16 A strict hand-written unload query splitter, no percent-decoding
  `Scripts/sd-router.py:_sd_unload_query`.
- D-17 One definition of resident; a non-resident force unload is executed by
  `submit_unload` under the lock in the handler thread and returns an
  already-finished ticket, a non-force unload always queues and `decide()`
  completes it at the head. Costs, both by design under the single-token trust
  model: `?force=1&model=B` clears B's disabled flag and crash budget for every
  client; repeated force on the resident model costs a cold start each time.

### Needs user sign-off

- D-11 amends Zoltán's FR-2 mismatch rule.
- D-12 changes visible behaviour: a client that hangs up costs the next caller a
  cold start of about 20 s.

## Implementation deviations from the plan

1. The `args` deny-list. A port or host flag (`--port`, `-p`, `--listen-port`,
   `--listen-ip`, `--host`, bare or `=value`) is refused unless its value is
   exactly `{port}` or `{host}` `Scripts/sd-router.py:_sd_cfg_args`. Reason: the
   plan's literal rule forbade the flag outright, which would refuse every real
   config. Agent decision.
2. Force unload of the resident model clears the disabled flag and budget only
   when the model was disabled; a healthy model keeps its crash history
   `Scripts/sd-router.py:Scheduler` (case T10). Non-resident force and a queued
   non-resident unload always clear. FR-5 said "always clear". Reason: stopping a
   healthy model is no reason to forgive its crashes.
3. Host and Origin. Check order is bearer, header defects, Host (on a loopback
   bind exactly `127.0.0.1:<port>` or `localhost:<port>`), Origin (any Origin
   refused), all after the bearer and all 400 `invalid_request` with fixed text
   `Scripts/sd-router.py:_SdHandler`. Reason: the closed table has no 403, and the
   bearer stays the first gate.
4. Stdlib refusals stay inside the closed table: 501 becomes 405
   `method_not_allowed`, 400/414/431/505 become 400 `invalid_request`
   `Scripts/sd-router.py:_SD_STDLIB_REFUSAL`; one exchange per connection,
   `Connection: close` always. Reason: no answer may leave with a code outside
   the table.
5. A bug inside the handler answers 502 `upstream_failed`, the table's only 500
   being `unload_failed`; HEAD gets the stdlib 405 before auth. Reason: the
   closed table, and a handler failure must still reach the caller.
6. An upstream answer of kind `failed` or `too_large` (content-encoded, 1xx,
   3xx, over the cap) is 502 `upstream_failed` after exactly one send, with no
   retry and no budget charge, and the model stays READY
   `Scripts/sd-router.py:_SD_ANSWER_REFUSED`. Reason: the child answered; it did
   not crash.
7. Plan gaps filled. `Decision` gained a third field `code`
   `Scripts/sd-router.py:Decision`; `ModelView(state, group_alive, disabled,
   restart_at)`; helpers `_sd_pick`, `_sd_requeue_head`, `_sd_may_retry`,
   `_sd_route_head`; route kinds `"openai"` and `"sdapi"`. A JSON body over
   64 KiB without a prefix is refused 400; a second multipart `model` part after
   64 KiB is undetected (declared, R19). A first start failure that trips the
   budget answers `model_start_failed` or `model_disabled`. CLI ranges: `--port`
   0-65535, `--body-limit` 1 KiB-1 GiB, `--max-connections` 1-1024; a bind
   failure exits 2 with one line; a non-loopback `--bind` without
   `--allow-remote` is refused in `main` `Scripts/sd-router.py:main`; SIGHUP is
   handled like SIGTERM. A config `body_limit` equal to the default lets the flag
   win, since the two are indistinguishable `Scripts/sd-router.py:_sd_settings`
   (to reconcile). Control routes never take an admission slot, their body limit
   is 4096, an unload body is empty or `{}` else 400, and an unload whose client
   leaves is not cancelled. `/m/<x>/v1/models` with an unknown `x` is 404
   `model_unknown`; `/v1/models` `created` is the server's start epoch. Reason:
   the plan left each of these open.
8. Error `type` strings: `invalid_request_error`, `authentication_error`,
   `not_found_error`, `server_error`, `service_unavailable_error`,
   `timeout_error` `Scripts/sd-router.py:_SD_ERROR_TYPE`. Reason: the plan named
   none.
9. Task order: the hand-written classes were written with empty marker pairs
   before the generator run. Reason: amalgamate seeds a new host from an empty
   marker pair, and a rendered region needs the host-provided names around it
   to exist already.
10. Test group A: A1 allows two `HTTPConnection` builders (`_sd_send` and the
    readiness probe `Scripts/sd-router.py:_sd_probe_status`, literal `GET /`
    only); A5 (units 1-3 read no clock and no randomness) and A16 (the child env
    never from `os.environ`) were added; A17 covers the `*_locked` helpers; group
    A has no hygiene case. Reason: the readiness probe is a second, legitimate
    HTTP client, and the added rules pin decisions the plan stated in prose.
11. The stub sd-server gained `--crash-on-start-first`, `--bind-fail-first`,
    `--answer-delay`, `--response-bytes` and `--control-file`. Reason: the cases
    for start failures, port collisions, timeouts, the answer cap and mid-run
    behaviour changes needed them.
12. In llm-router the sdcpp credential rule sits before the generic `x-api-key`
    default `Scripts/llm-router.py:_rt_cfg_backend`; the test rows for a key with
    the wrong header use the loopback backend. Reason: on a LAN `http://`
    backend the V14 cleartext rule answers first and would mask the rule under
    test.

## Threat table

Every row has a test (plan Section 5).

| Risk | Mitigation | Test |
|---|---|---|
| Orphaned GPU process after unload, crash or shutdown | group kill, sweep, ESRCH confirmation; unload 200 only after it; last-resort registry kill | S1-S6 |
| Child ignores SIGTERM | SIGKILL after `stop_grace` | S3 |
| Grandchild survives leader death | group sweep after the leader exits | S4 |
| Pid/pgid reuse kills a stranger | kill-before-reap; spawn registry | S5 |
| Deadlock: queued unload, swap, crash | one scheduler thread, one Condition, no blocking under the lock | Q-sched |
| Force unload races completion | `running` snapshot under the lock; set-once `finish()` | Q-sched |
| Client gone while queued | client-gone poll, ticket cancelled, skipped at pop, no swap | Q-sched, Q-http |
| Client gone while running | abort reason `client`, socket shutdown and child killed (D-12); not a crash | Q-sched, Q-http |
| Hung child | `request_timeout`, kill, 504 `upstream_timeout` | T5 |
| Image flood locks out control routes | `conn_sem` = max_connections + reserve; 503 `queue_full` | U, Q-http |
| Zombie group holds the GPU | requests fail 500 `unload_failed`; group re-probed at each pop | Q-sched |
| Loading child mistaken for ready | 503 means not ready | S17 |
| Poison prompt burns the shared budget | declared; unload clears | T7 |
| Pgid reuse in the last-resort sweep | registry entry dropped on ESRCH, re-probe before SIGKILL | S18 |
| Crash during the retry | `model_crashed`, counted, no third send | T4 |
| Retry replays a half-delivered answer | the answer is buffered whole first | T9 |
| Restart storm | 5 crashes in 600 s, DISABLED; backoff between automatic restarts | T8 |
| Request smuggling (CL/TE, duplicate CL, absolute URI, `//`) | generated front, `_single_header`, hand-written 411, rebuilt request line | P |
| Client bearer leaked to the child | `Authorization` never forwarded | P |
| Token or env value in logs, errors, argv, repr | `_log_value`, redacting repr, no `--config-json`, 0600 config | L, P |
| Env inheritance leaks router secrets | minimal env (D-1) | S14, A16 |
| Port race, foreign listener answering readiness | child-liveness check, port retry on an exit within 2 s | S7 |
| Spoofed body `model` selects another model | by design (one trust domain); unknown name 404 | R |
| Multipart sniffer abuse | canonical-only, bounded window, 400 never a guess | R |
| Memory exhaustion by buffering | `--body-limit`, admission cap, `response_limit` | P |
| Clashing port/host flag in `args` | load-time refusal naming the index only | L |
| llm-router credential change breaks pins | isolated phase; J12/J39/A14 re-run unchanged; R1/R6 changed red first | llm_router R |
| Structural drift (a second send site, a shell) | static AST group | A |

The plan's table gave the hung-child, poison-prompt, crash-in-retry and
restart-storm rows the labels T9, T10, T3 and T5; group T was numbered
differently when it was written, and the rows above carry the labels the suite
uses (`tests/test_sd_router.py`).

## Notes

- The child's own non-2xx answers are relayed verbatim, status and body; they
  are not router codes and are not rewritten.
- llm-router's 300 s idle read aborts before sd-router's default 600 s
  `request_timeout`, so a stuck request reached through llm-router normally ends
  through sd-router's client-abort path (D-12: the child is killed, not charged
  to the budget) rather than through 504 `upstream_timeout`.

## The llm-router sdcpp credential phase

A backend of kind `sdcpp` now accepts an optional `api_key`, sent as
`Authorization: Bearer <key>` `Scripts/llm-router.py:_rt_cfg_backend`.
`auth_header` must be omitted or `authorization`; `none` or `x-api-key` together
with a key is a config error naming `backends.<n>.auth_header`. V14 (a key over
a non-loopback `http://` base URL) is still refused, and the key never reaches a
log, a repr or an echoed upstream error (case R14). Without a key nothing
changes: no credential is sent. This supersedes ADR 0028's statement that an
sdcpp backend refuses `api_key` and any `auth_header` but `none`; the reason
given there (a key would travel to a server that ignores it) no longer holds
once an sd-router sits in front.

## Declared limits

- Unverified against a real sd-server (live smoke SC-7 pending): the host and
  port flag names (believed `--listen-ip` and `--listen-port`), the status `GET /`
  returns while loading (503 assumed) `Scripts/sd-router.py:_sd_probe_status`, and
  grandchild behaviour. Release step: run `sd-server --help`.
- Single-user host assumed: the child listens unauthenticated on 127.0.0.1 and
  any local user can reach it; one INFO line says so at startup
  `Scripts/sd-router.py:_SD_TRUST_LINE`. Operator `env` and `args` are trusted
  config.
- No TLS; `--allow-remote` needs `--insecure-cleartext` (D-13).
- No keep-alive; chunked or any `Transfer-Encoding` is refused 411.
- No restart backoff for the first start after an unload; no idle auto-unload;
  no async job routes.
- Free-port race: the port is picked by bind-and-close
  `Scripts/sd-router.py:_sd_free_port`; mitigated by the child-liveness
  readiness rule and up to three port tries, not removed.
- SIGKILL of the router can orphan a child process group (its own session).
- The mask plus `/v1/images/edits` weakness of sd-server (image damaged when
  `init_image` is missing) is not repaired; `init_image` in
  `sd_cpp_extra_args`, or `/sdapi/v1/img2img`, is the workaround.
- `sd_cpp_extra_args` is forwarded unfiltered, byte-exact.
- A poison prompt that kills the child on every send burns up to `retries + 1`
  of the shared per-model budget per request and can disable the model for every
  client until an unload.
- `queue_full` counts reservations: a running ticket and a completed one still
  being written count toward `--max-connections`, so a slow reader or a slow
  body sender (up to the 120 s body deadline) holds image capacity.
- The control reserve is a mitigation: slots are taken before authentication,
  so unauthenticated slow-header or idle connections can fill them until the
  5 s header deadline; effective on the loopback bind, weaker with
  `--allow-remote`.
- Memory worst case about 768 MiB at the defaults
  (8 x (32 + 64) MiB) plus at most 4 KiB per control body.
- The response cap default of 64 MiB is unmeasured (D-6).
- Head-of-line wait: a request behind a swap can wait up to the target's
  `start_timeout` (default 120 s) plus the request ahead of it.
- Registry TOCTOU (S-L6), lowered by the fix round below: `Supervisor.wait_ready`
  calls `child.proc.poll()`, which can reap an exited leader while the group is
  still non-empty. The poll now marks the child reaped and drops an emptied
  group, and the ladder gives a surviving member its full grace
  `Scripts/sd-router.py:Supervisor`; the residual is a stale pgid for a group
  that survives, which another group could take only after a full pid wrap,
  same uid.
- No failed-auth throttle (review M2); the token is checked before any body
  read, and the host is a single-user loopback one.
- What the M10 and M11 fixes (F-H, F-I below) leave declared, both on an
  operator-chosen path: a FIFO at the `--log-file` path can still block the
  open, which passes no `O_NONBLOCK` and rejects a non-regular file only after
  the open returns `Scripts/_mcp_logging.py:_configure_logging`; and the ready
  file's parent directory mode is not checked, while a replace between the
  shutdown's read and unlink stays unseen
  `Scripts/_mcp_httpfront.py:_http_remove_ready_file`.
- The readiness probe does not prove that the answering socket belongs to the
  child's pid (M17 residual); the same trust class as the single-user host.
- A second multipart `model` part after the 64 KiB sniff window is undetected
  (R19); an un-prefixed JSON body over 64 KiB is refused instead.
- The port/host flag check is a deny-list over flag names; readiness on the
  substituted port is the real safety mechanism.
- macOS logs a WARNING "process group signal refused: PermissionError" when
  SIGTERM hits a group whose only member is an unreaped zombie; EPERM is treated
  as gone, so the behaviour is correct and the line is noise.
- A config `body_limit` equal to the default cannot override `--body-limit`
  (deviation 7); the precedence is pinned by case L32.

## Security review and fix round (2026-10-28)

Written into the draft page, not through `addendum.py`, because the page has
not been accepted yet.

**The review.** A code-mode security review of `Scripts/sd-router.py` produced
30 raw candidates, merged to 25, each verified independently. Verdict
APPROVE: no HIGH, no MEDIUM, nine LOW and a few INFO; the rest were suppressed
as declared limits or operator-trusted config. The nine LOW:

- M2: no throttle on failed authentication, with a token policy of 32
  characters and 8 distinct.
- M6 and M23: a Content-Type carrying CR/LF or an obs-fold.
- M10: `--log-file` opened without `O_NOFOLLOW` (fixed later, F-H).
- M11: the ready file's predictable `.tmp` name (fixed later, F-I).
- M14: the stale pgid of S-L6.
- M18: a signal handler that could deadlock the main thread.
- M19: non-force unload tickets without a bound.
- M24: a NUL in the config reaching `Popen`, whose `ValueError` was not caught.

**Fixed in this round, each red first.** The `sd_router` suite went from 201
to 211 typed cases (A 17, L 32, R 20, D 22, S 20, Q-sched 16, Q-http 5, T 13,
P 38, U 18, G 6, E2E 4), the ledger above the suite's row in
`tests/run.py:SUITES`.

- F-A (M6, M23): a Content-Type with any control character, an obs-fold, a tab
  and DEL included, is refused 400 `invalid_request` on every path, the
  `/m/<model>/` prefix included, and a child answer with such a Content-Type
  becomes 502 `upstream_failed`; one helper decides both
  `Scripts/sd-router.py:_sd_has_ctl`.
- F-B (M7): relayed child answers and `/v1/models` carry
  `X-Content-Type-Options: nosniff` `Scripts/sd-router.py:_sd_write_result`.
- F-C (M24): a NUL in `command`, `args`, `env` or `cwd` is refused at config
  load `Scripts/sd-router.py:_sd_cfg_no_nul`, and `Supervisor.launch` also
  catches `ValueError`, so a failed `Popen` leaves the model `DEAD` with a
  budget charge (`model_start_failed`) instead of stuck in `STARTING`
  `Scripts/sd-router.py:Supervisor`.
- F-D (M18): the signal handler no longer calls `Event.set`; it sets an
  attribute on `Scripts/sd-router.py:_SdStopFlag`, and main polls it each tick
  `Scripts/sd-router.py:_sd_wait_stop`.
- F-E (M14, S-L6): `Supervisor._poll` marks a reaped child (`child.reaped`) and
  probes the group with signal 0, dropping an emptied group from the registry;
  `stop()` of a reaped child watches the group for the full `stop_grace`
  before its SIGKILL, so a surviving grandchild gets its grace. The residual,
  a stale pgid for a group that survives, reused only after a full pid wrap
  and by the same uid, stays a declared limit at a lower severity.
- F-F (M19): `submit_unload` coalesces a non-force unload into the last queued
  ticket when that is a non-force unload for the same model
  `Scripts/sd-router.py:Scheduler`.
- F-G (M20): a config `body_limit` takes the command line's 1 KiB to 1 GiB
  range, and `response_limit` 1 to 1 GiB
  `Scripts/sd-router.py:_SD_RESPONSE_LIMIT_MAX`. The G4 precedence (a config
  value that differs from the 32 MiB default wins over `--body-limit`; one equal
  to the default, or none, lets the flag win) is unchanged and now pinned by
  case L32.

**Fixed afterwards in the canonical sources, on the owner's decision.** M10 and
M11 live in `Scripts/_mcp_logging.py` and `Scripts/_mcp_httpfront.py`, which
every host shares ([[generated-regions]]), so the round first left them open
as a cross-cutting change; the owner then decided to fix them there, not in
the router. The fixed blocks were regenerated into 21 hosts (the logging block
into 18 servers plus mcp-proxy, llm-router and sd-router, which also take the
httpfront block), and the fleet smoke passed on 19 servers.

- F-H (M10): `_configure_logging` opens `--log-file` with `O_NOFOLLOW`, then
  `fstat`s the descriptor and raises `OSError` unless it is a regular file
  owned by the effective uid; only then does it `fchmod` 0600
  `Scripts/_mcp_logging.py:_configure_logging`. Case
  `log-file-symlink-refused` in group E of `generated_region` plants a symlink
  and checks that its target is neither written nor re-moded
  `tests/test_generated_region.py`.
- F-I (M11): `_http_write_ready_file` writes through a unique temp name, the
  path plus the pid plus 8 hex characters, created `O_EXCL|O_NOFOLLOW`, with no
  pre-remove, and removes only its own temp on any failure before the replace
  `Scripts/_mcp_httpfront.py:_http_write_ready_file`. Case
  `httpfront-ready-file-unique-tmp` in group E keeps a planted `<path>.tmp`
  untouched and checks that a failed replace leaves no temp behind.
  `generated_region` went from 140 to 142 typed cases.

What both fixes leave declared is in the declared limits above.

**Open, declared, not fixed.** M2 (a single-user loopback host, and the token
is checked before any body read); the M17 residual, a readiness probe that
does not prove the answering socket belongs to the child pid. Each is in the
declared limits above.

**Deviations of the fix round.**

1. F-A answers 400 `invalid_request`, not 415 `unsupported_media_type`: the
   defect is a malformed header value, which the closed table files under
   `invalid_request` with the other header defects.
2. F-E is tested with a fake process world (case S20), not the stub, because
   the stub's grandchild dies on SIGTERM and so could never show that a
   surviving member gets its grace.
3. F-F coalesces at the queue's tail only. Matching any queued unload would
   pull a later caller's unload ahead of requests queued before it; the tail
   rule keeps FIFO order, and the queue then holds at most one unload between
   two requests, which admission bounds.
