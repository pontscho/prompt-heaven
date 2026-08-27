---
name: 0008-a-serialized-read-loop-looks-like-a-dead-server
type: adr
status: active
title: A serialized read loop makes one slow call look like a dead server
description: Decision to dispatch each MCP request as its own task with a reader thread nothing can take, and to stop treating an error payload as "keep waiting" — the two halves that turned one transient 503 into a restart-only outage — then to convert all ten live servers, with the concurrency decision audited per server rather than copied.
sources:
  - Scripts/mcp-jenkins.py
  - Scripts/mcp-forge.py
  - Scripts/mcp-git.py
  - Scripts/mcp-inspect.py
  - Scripts/mcp-context7.py
  - Scripts/mcp-purity.py
  - Scripts/mcp-lldb.py
  - Scripts/mcp-gdc.py
  - Scripts/mcp-postgres.py
  - Scripts/mcp-tshark.py
  - Scripts/mcp-wiki.py
  - ClaudeCode/skills/mcp-jenkins/SKILL.md
verified:
  commit: f1d117b
  date: 2026-08-27
links:
  - scripts
  - skills
  - 0001-purity-server-unification
---

# ADR 0008: A serialized read loop makes one slow call look like a dead server

**Status:** accepted (implemented, `f1d117b`). Append-only — the
WHY is frozen here; the living WHAT/HOW is the read loop in each
`Scripts/mcp-*.py` server, with `mcp-jenkins.py` as the reference implementation
the other nine were written against.

## Context

The report was: *"it can lose the connection to Jenkins somewhere and then
refuses to work at all until the MCP server is restarted."*

The intuitive diagnosis — a transient failure poisoning cached state — was
audited first and **refuted**, and recording that matters because it is what
redirected the search. `Scripts/mcp-jenkins.py` holds no `global`, no cache of
any kind, no crumb cache, no cookie jar, no "connected" flag, no on-disk token
cache. The crumb is refetched on every POST. `urllib.request.build_opener`'s
default class list carries no `HTTPCookieProcessor` and `do_open` forces
`Connection: close` before dropping the socket, so a stale `JSESSIONID` or a dead
keep-alive cannot persist even in principle. There is no lock in the file, so no
deadlock is reachable. **There was nothing to poison.**

The real mechanism was two independent defects that only bite when composed.

**One: the loop awaited the handler on the same line of control that later
awaited the readline.** No `create_task`, no dispatch. While a handler ran, the
server did not read stdin. That is not merely a failure path — a `run_and_wait`
on a 20-minute build is the *ordinary* case for this server.

**Two: an error payload was read as "keep waiting".** `handle_get_build_status`
never raises on a non-200; it returns a success-shaped payload carrying an
`error` key. The poll loop's exit test was
`if "error" not in status and not status.get("building")`, so a single failing
poll made **both** halves unreachable — a Jenkins answering nothing but 503 was
indistinguishable from a build that never finished. The loop then spun to the
full deadline: `timeout_sec`, default 1800 s and **unclamped**, while
`PARAM_ALIASES` maps `timeout` onto it, so a model writing `{"timeout": 7200}`
bought a two-hour freeze.

Composed, one 503 during a Jenkins restart (or a 502 from the proxy, or a 30x to
SSO — `follow_redirects=False`, so a login redirect surfaces as an error payload
too) became a 30-minute spin during which the server was deaf. Every later call
sat unread in the pipe and timed out client-side at ~60 s, i.e. 30× sooner, then
got answered in a burst against ids the client had already abandoned. The freeze
could not be interrupted: a `notifications/cancelled` is never read, and would be
dropped by the `msg_id is None` early return even if it were. Restarting was the
only lever the user had — which is exactly how the symptom was reported.

## Decision 1 — one task per request, and the reader gets a thread nothing can take

Each message becomes its own task; handlers run in a worker `ThreadPoolExecutor`;
`sys.stdin.readline` runs in a **separate single-thread executor**.

The second executor is the part that is easy to get wrong. With one shared pool,
saturating it starves the readline and reintroduces the same deafness with more
machinery. The reader must own a thread no handler can take.

This is safe to run concurrently *because of* the audit above: no mutable module
state, `JenkinsConfig` written once in `main()` before the loop starts, `_opener`
stateless. Replies need no lock either — handlers run in the pool, but `_serve`
resumes on the event-loop thread after its await, so writes serialize for free.
That property is load-bearing and is written down at the function.

`os._exit(0)` after `asyncio.run` is deliberate, not a shortcut. The handler
threads live in the server's own executors rather than the loop's default one, so
asyncio does not join them — but `concurrent.futures` registers an atexit hook
that would, and one handler mid-poll would hold the process open for the rest of
its deadline. Every reply is flushed as it is written and logging flushes per
record, so there is nothing left to drain. Measured: 0.009 s to exit 0 with a
handler genuinely mid-flight.

**Rejected: a per-handler timeout ceiling.** `Scripts/mcp-gdc.py` already does
this — `asyncio.wait_for(..., timeout=90.0)`, with the comment *"no single
handler may wedge the (serial) server… the observed 15-minute silent hang"*. It
caps the outage instead of removing it, and it cannot tell a legitimately long
build from a hang, so any ceiling honest about `run_and_wait` is longer than the
client's own timeout anyway.

**Rejected: cooperative cancellation threaded through the handlers.** It needs a
cancel token in every handler signature, and once dispatch is concurrent a stuck
handler no longer blocks anything — it buys only the earlier reclaim of one of
eight worker slots. Cost across the whole handler surface, benefit at the margin.

## Decision 2 — an error payload is not "keep waiting"

The poll loop now **counts consecutive failures** (`MAX_POLL_ERRORS = 3`, reset
on any success) instead of testing for the absence of an error, and reports a new
`polling_failed` phase with the failing poll's own reply embedded as evidence.
The log tail is skipped in that case: the log endpoint sits behind the same
Jenkins and would only add a fourth failure. `timeout_sec` is clamped to 3600,
mirroring the clamp `handle_get_queue_item` already applied to its own 600.

Counting, rather than bailing on the first error, is the whole point: a single
failed poll during a rolling restart is a blip, and three polls give it ~10 s of
grace at the default interval. A fourth failure tells the caller nothing the
third did not.

**Rejected: retry with backoff inside the poll.** A poll loop already *is* a
retry loop; a second retry layer inside it re-spends the same deadline more
slowly and reports later.

**Rejected: making `handle_get_build_status` raise on a non-200.** That would fix
the exit test by changing the payload shape every other caller branches on — a
much wider blast radius than the loop that misread it.

## Consequences

- **Replies may arrive out of order relative to requests.** JSON-RPC ids carry
  that, and no client here assumes FIFO, but it is a real change to the wire
  behaviour and belongs in this list rather than in a footnote.
- **One client can now put 8 concurrent requests on Jenkins.** The old shape
  rate-limited by accident. `MAX_INFLIGHT_REQUESTS` is where that is traded.
- **`notifications/cancelled` is still ignored.** Now merely wasteful — one
  worker slot until its own timeout — rather than the reason a cancel could not
  land.
- **A >32 MiB response is a visibly truncated prefix, not an OOM.** The cap is a
  crash guard, not a paging mechanism: nothing above it would have fitted in a
  reply anyway. But a truncated *artifact* is a prefix of a file rather than the
  file, so it reports `truncatedBytes` instead of looking whole.
- **The shape was fleet-wide, and the whole live fleet was converted in the same
  pass.** See the next section; this consequence is what turned a one-server fix
  into a ten-server one.

## The fleet-wide rollout — and why "apply the same patch ten times" was the wrong plan

Every `Scripts/mcp-*.py` server grew from one read-loop template, so 13 of 15
carried this defect. Six were *worse* than mcp-jenkins had been — `mcp-forge`,
`mcp-git`, `mcp-postgres`, `mcp-inspect`, `mcp-wiki` and `mcp-tshark` called
`self._handle_message(msg)` with no `await` and no executor at all, freezing the
event-loop **thread** rather than merely the reading.

**All 13 were converted, after this decision was reversed once.** The first call
was to skip three of them: `mcp-clangd`, `mcp-cuda` and `mcp-lua-lsp` are
unregistered — their capabilities live behind `purity_call`
([[0001-purity-server-unification]]) — so they never launch, and fixing a
transport nobody starts is churn.

That was wrong, and the counter-argument is the better one: **those three files
are the template.** Every server in this fleet grew from one read loop, which is
how one defect became thirteen. Leaving three unlaunched copies of the broken
shape in the tree is not neutral — it is the mechanism by which the next server
inherits it. Dead code that gets copied is not dead. The reversal is recorded
rather than tidied away because the reasoning, not the outcome, is what a future
reader needs: "it never runs" is a good argument about *risk* and a bad one about
*propagation*.

Converting them paid for itself immediately — two of the sharpest findings in the
whole sweep came out of those three files (the cold-start race and the
four-backend spawn, both below).

**The transport pattern is uniform; the concurrency decision is not.** Copying
mcp-jenkins' "no locks needed" conclusion along with its code would have shipped
data corruption, because that conclusion was earned by an audit of a module that
happens to hold no state. Each server got its own audit first, with authority to
stop and change nothing. What that produced:

| Server | Dispatch | Serialization |
|---|---|---|
| jenkins, forge, git, inspect | thread pool | none needed (git: one lock over the mutating `stash` door; inspect: one over `warnings.catch_workings`) |
| context7, purity, lldb, gdc | **coroutine, no pool** | per-backend / per-session / per-browser-target lock |
| postgres | thread pool | per-**connection** lock across the whole exchange |
| tshark, wiki | thread pool | tshark: registry + config locks; wiki: none — its index is already build-fresh-and-swap |
| clangd, cuda, lua-lsp | **coroutine, no pool** | one lock (single backend, so no registry to key on) |

The four "coroutine, no pool" rows are the important ones. Handlers there were
already coroutines whose per-client `_next_id += 1` and `_pending[id] = fut` sit
between the same two awaits — safe *only* on a single event loop thread. Moving
them into a worker pool, faithfully copying the reference, would have broken the
very invariant that made them safe. `mcp-gdc` and `mcp-lldb` both refused the
pool for exactly this reason, and said so.

Lock **granularity** was the second divergence, and it is not a detail: per-command
locking is wrong in `mcp-lldb` because its composite handlers issue two or three
sequential commands (parse a breakpoint number out of one reply, modify *that*
number in the next) and a foreign command in between answers about a different
breakpoint — silently. Ordering matters in `mcp-gdc` for the same reason at a
different layer: perfect wire locking still lets a screenshot overtake a
navigation, so the lane is the browser target and `asyncio.Lock`'s FIFO waiter
queue is what replays a lane in arrival order.

Each conversion was proven with a negative control — the probe re-run against the
pre-fix loop, and against a deliberately lock-neutered build. Two of those are
worth keeping: without its lock `mcp-lldb` spliced one reply into another
(`MARK_A\r\n(lldb) MARK_B`) and reported a fabricated timeout for a command that
had succeeded; without its lock `mcp-postgres` desynced the wire, left a request
permanently unanswered, and its reconnect-on-error path **re-executed the
statement** — a duplicate write, had it been a write.

## Six pre-existing bugs the concurrency exposed

None of these were caused by the change; all were found because introducing
concurrency forced someone to read the state honestly.

- **`mcp-tshark` lost every saved config, and reported success.** The save path
  truncates with `open(..., "w")` before `json.dump` refills it, so a concurrent
  reader parses garbage, falls into `except → {}`, and the next save persists
  the empty dict.
- **`mcp-tshark` session ids could collide.** `int(time.time())` plus four random
  bytes with no counter: two captures in one second share a `-w` path, and the
  second registry write orphans a live tshark nobody can stop.
- **`mcp-purity`'s `diagnostics` always burned its full 10 s timeout.** An
  edge-triggered wait on a level-shaped signal: `_prime_index` opens the file at
  startup, clangd publishes, and `get_diagnostics` then calls `ev.clear()` on the
  signal that already fired — while `_sync_document` sees an unchanged mtime and
  sends no `didChange`, so nothing re-publishes. The answer was always correct
  (it came from the cache), which is why it read as slow rather than broken.
  Replaced by a per-URI publish generation counter: **12.18 s → 0.004 s**, with
  freshness preserved by sampling the document's own version to answer "is a
  publish owed?" separately from "has it landed?" — the counter alone cannot
  distinguish those, and a counter-only fix would have returned a stale cache.
- **`mcp-gdc` misattributed timeouts.** Since Python 3.11 `asyncio.TimeoutError
  is TimeoutError`, so a CDP command's own 30 s budget was reported as the 90 s
  global cap.
- **`mcp-inspect`'s Python validator was never thread-safe.** `warnings.catch_warnings`
  swaps the *process-global* filter list; the stdlib documents it as unsafe, and
  Python 3.14 only makes it context-local under free-threading or
  `-X context_aware_warnings`, neither of which applies at this script's 3.9 floor.
- **`mcp-forge` used `preexec_fn`.** It runs Python in the forked child and is
  documented unsafe once other threads exist — which is precisely what this change
  introduces. Replaced with `start_new_session=True`, which is semantically
  identical for the `killpg` escalation that depends on it.
- **`mcp-cuda`'s cold start failed two different ways depending on a race**, and
  the second is a protocol violation rather than a delay. The auto-init gate tests
  `(_client is None or _client.process is None)`, but `_client` is assigned before
  `start()` fills `.process`. A request landing in the gap **skips the gate** and
  issues a semantic query into a session that has not sent `initialized` — proven
  from clangd's own log, `workspace/symbol` on line 13 ahead of `initialized` on
  line 15. Landing before the gap instead awaited the 90 s gate *on the read loop*
  and went deaf for the whole 60 s cold start. Both modes reproduced; neither
  survives.
- **`mcp-lua-lsp` would spawn one language server per concurrent `init`.** With
  its lock neutered, four concurrent `luals_init` calls produced **four**
  `lua-language-server` processes all replying `status: ok`, three of them
  unreachable behind a global pointing at one. This is the clearest evidence in
  the sweep that per-backend locking is load-bearing and not decoration.

Unclamped caller-supplied timeouts were closed in the same pass wherever they
existed (`mcp-git` 300 s, `mcp-inspect` 120 s, `mcp-tshark` 600 s), and
`mcp-wiki`'s `subprocess.run(["git", ...])` — which carried **no** timeout at all
— got one. An unclamped caller timeout is the same defect class as the loop: one
request decides how long the server is unavailable, bounded by nothing the server
controls.

**One residual, deliberately not fixed.** The thread-pool servers share their
worker pool with `ping` / `initialize`, so eight requests queued behind one
blocked connection would still delay a control call. That is queueing, not
deafness — the reader never stops reading — and a control-method fast path in
five servers is speculative until someone actually hits it.

## What the measurements changed

Three things were believed before they were measured, and all three were wrong.

- *"A transient failure poisons cached state."* The reported mental model, and
  the reason the first search looked in the wrong place. There is no cached
  state; see Context.
- *"A routine timeout is reported as a network error."* It was not. A socket
  **read** timeout raises `TimeoutError`, which is an `OSError` but **not** a
  `urllib.error.URLError`, so it missed the network clause and landed in the
  last-resort guard — emitting a full traceback at ERROR for an expected
  outcome, once per in-flight call now that calls run concurrently. Measured:
  child stderr over the transport suite dropped from **177 lines to 2**, and the
  two survivors are the intended one-line warnings. The same failure also had
  two spellings, because a *connect* timeout does get wrapped in `URLError`.
- *"`TimeoutError` is enough to catch a socket timeout."* Only from Python 3.10,
  where `socket.timeout` became an alias for it. This script's header declares
  `requires-python = ">=3.9"`, where `socket.timeout` is a distinct `OSError`
  subclass — so on the declared floor a bare `TimeoutError` would have caught
  nothing. Both are in the tuple, redundantly on modern interpreters and
  correctly on the oldest supported one.

One more defect was found by reading rather than measuring, and fixed in the same
pass: the `HTTPError` branch never closed its response. The 2xx path closes
through `with`, but `HTTPError` keeps the live response in `.fp`, defines no
`__del__`, and participates in an exception → traceback → frame → exception
reference cycle, so refcounting alone never reclaims it — only a cyclic GC sweep
does. A poll loop hammering a 503 for 30 minutes held roughly 360 sockets in the
meantime, and fd exhaustion is itself a restart-only failure.

The fix is verified by an offline harness (a blackhole TCP listener that accepts
and never answers, so a call blocks in a real socket read): a ping is answered in
**0.001 s** while a handler sits **8.0 s** deep in that read, four blocking calls
collapse into **one 8.02 s window** instead of four sequential ones, malformed
input is answered (`-32700` for unparseable, `-32600` for valid-JSON-but-not-an-
object, where a bare `5` previously killed the process with an `AttributeError`
escaping `run()`), and the process exits 0 in **0.009 s** with a handler still in
flight.
