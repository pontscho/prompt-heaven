---
name: 0027-the-proxy-relays-it-never-composes
type: adr
status: active
title: The proxy relays; it never composes
description: Decision to put one stdlib-only MCP endpoint, Scripts/mcp-proxy.py, in front of a project root's child servers as a relay that adds nothing of its own -- unprefixed tool names with a duplicate refusing startup, payloads relayed verbatim under a framing ceiling and no answer ceiling, eager start, a bounded restart budget that disables, one protocol version on both transports, a cancel that travels one hop further through the child client's CancelledError arm, ThreadingHTTPServer with no second executor, and a hand-written child client rather than a seventh canonical source -- with the alternatives rejected, the three security-review rounds and what each fixed, the findings left unfixed on purpose, and the declared limits.
links:
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0012-the-transport-tier-diverged
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0022-a-someday-maybe-is-a-roadmap-item
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - scripts
  - tests
  - generated-regions
---

# ADR 0027: The proxy relays; it never composes

**Status:** accepted 2026-10-03. The implementation, `Scripts/mcp-proxy.py`, its
suite `tests/test_mcp_proxy.py` and its stub child
`tests/files/mcp_proxy/tf_stub_child.py` were in the working tree and not yet
committed when this page was written, so the page carries no `sources:` and no
`verified:` — its anchors are inline only. The decision is one rule and the
consequences that follow from it: the proxy forwards what its children say and
adds nothing a child did not say. Each section below is one place where
"adding something" was the easier design and was refused.

Every number on this page is a measurement or a review count at the moment of
the decision (§4b's frozen-record carve-out), quoted from the requirements file
and the review records of 2026-10-03. The live values are in the code and in
the `SUITES` table of `tests/run.py`.

## Context — one client, a fleet of servers, one root

ai-soul connects to MCP servers through its SDK client
(`@modelcontextprotocol/sdk` 1.25.3, `StreamableHTTPClientTransport`). The fleet
in `Scripts/` is a set of single-file stdio servers, each exposing one
dispatcher tool. ai-soul wanted one connection per project root that reaches
all of them: spawn the configured children, learn their tools, expose them
outward, route each incoming call to the right child — first over stdio, and
in the end over HTTP.

Two shapes were available. A **composing** gateway owns a namespace (it
prefixes or renames tools), shapes answers (it caps or rewrites them), and
hides failures (it retries, it spawns lazily). A **relaying** proxy owns none of
that: its children's names, schemas, payloads and errors pass through, and the
proxy's own contribution is routing, lifetime and transport. Every fleet rule
the proxy inherits — [[0010-a-handler-failure-must-reach-iserror]],
[[0011-a-truncated-payload-carries-the-first-cookie]],
[[0013-the-ceiling-is-a-payload-class]], [[0015-ambiguity-is-the-defect]] — is
easier to keep honest for the second shape, and the brief's settled decisions
(original names, eager start, a deterministic toolset) already pointed there.

## Decision

1. **One new single-file server, `Scripts/mcp-proxy.py`,** pure Python 3.9 and
   the standard library ([[0024-pure-python-39-and-the-stdlib]]), shared
   plumbing only through the two generated regions it actually uses
   (`_mcp_json.py :: _result, _error` and `_mcp_logging.py :: _configure_logging`,
   per [[0025-generate-do-not-import]]). One proxy instance serves one project
   root; several roots mean several instances.
2. **Names are unprefixed and a collision refuses startup.** `tools/list` is
   the union of the children's tools in config order, each with its
   `description` and `inputSchema` verbatim. A tool name two children expose
   stops every child and exits with an error naming the tool and both children
   (`Scripts/mcp-proxy.py:start_children`).
3. **Payloads are relayed verbatim.** A child's result or JSON-RPC error goes
   upstream unchanged (`Scripts/mcp-proxy.py:_handle_tool_call`); a child that
   cannot be reached is a tool result with `isError` set.
4. **Eager start, bounded restart, then DISABLED.** No lazy spawn and no
   automatic retry of a call.
5. **One protocol version on both transports,** no negotiation.
6. **A cancel is the `task` reclaim class,** forwarded to the child by the child
   client's own `CancelledError` arm, not by a new class.
7. **The HTTP front is `ThreadingHTTPServer`,** bridged into the event loop; the
   stdin reader stays the file's only executor.
8. **The child MCP client is hand-written** in the proxy, not a seventh
   canonical source, and `_mcp_lsp.py`'s request block is not reused.
9. **The proxy is not registered in `~/.claude.json`.** Its consumer is ai-soul;
   the smoke table carries it as `registered: False`.

## The relay rule, and what it costs

**Unprefixed names.** A prefix (`purity__purity_call`) would make every name
unique by construction, and it is the composing choice: it changes the name the
model sees, so a prompt or skill written against a server stops matching
through the proxy. Keeping the names means a collision is possible, and
[[0015-ambiguity-is-the-defect]] settles what to do with one: refuse, never
pick. A config that puts two servers with one tool name behind one proxy is a
config error, and it surfaces before the first upstream read rather than as a
call that silently reached the wrong child.

**The deliberate exception to "exactly one tool".** Every other fleet server
exposes one dispatcher tool, and the smoke harness asserts it. The proxy's
`tools/list` is a union, so its smoke row runs it with one stub child that has
one tool (`tf_stub_call`); the fleet invariant still holds there, and
aggregation is tested in the proxy's own suite.

**A framing ceiling, never an answer ceiling.** `Scripts/mcp-proxy.py:_MAX_FRAME`
(64 MiB, the child `StreamReader`'s `limit=`, configurable through the
`frame_limit` key within 1 MiB to 512 MiB) bounds one newline-delimited message
read from a child. It is how big a frame may be, not how much of an answer the
caller gets: a child has already applied its own ceiling
([[0013-the-ceiling-is-a-payload-class]]), and a second cut in the proxy would
truncate twice under two policies nobody declared together. A line over the
limit is a desynced stream — `readline` has discarded the buffer, so the rest of
the line would parse as garbage — and the child's pending calls fail and it is
restarted. The 5 MiB relay case of the success criteria exists because asyncio's
default 64 KiB limit would otherwise break large results.

**The naming is a gate constraint, not taste.** `tests/test_mcp_footprint.py`
reads any module constant matching `CAP_CONST_RX` as an output cap and any
`.get("<literal>")` matching `CAP_PARAM_RX` as a per-call cap. So the frame
constant is `_MAX_FRAME` (no `BYTES` suffix), the body cap is
`_HTTP_BODY_LIMIT`, and the config key is `frame_limit`, never
`max_frame_bytes`. Spelled the other way, the gate would measure a transport
bound as an answer ceiling — the exact confusion the ceiling ADR removed.

**No retry, no lazy spawn.** Tools are not idempotent: a forge build or a
Jenkins trigger re-sent after a child died may run twice. A call interrupted by
a death answers `isError` naming the child (`Scripts/mcp-proxy.py:_die`), and a
call made while the child is restarting is refused at once with a message that
says the interrupted call may or may not have taken effect
(`Scripts/mcp-proxy.py:ChildSlot`). Lazy spawn would make the
tool set depend on which tools had been called, which is not a deterministic
toolset.

## Eager start, restart and the disable rule

At startup every child is spawned with the root as its cwd, initialized, sent
`notifications/initialized`, and listed to the end of its `nextCursor` chain.
Any failure — a spawn error, an initialize timeout, a bad listing, a duplicate
name — stops every child and exits non-zero before the first upstream read. A
proxy that starts serves its whole declared toolset or none of it.

After startup, the death of the **current** incarnation while the slot is
`ready` is the only entry into recovery
(`Scripts/mcp-proxy.py:on_child_death`); it flips the slot to `restarting` and
starts exactly one `Scripts/mcp-proxy.py:_restart_loop`, which owns backoff,
budget and every retry and never recurses back through the death hook. The
ladder is 1, 2, 4, 8, 16, 32 s, then a 60 s cap, each plus up to 20 % jitter
(`Scripts/mcp-proxy.py:_RESTART_BACKOFF_S`). Each death is charged once against
a budget of 5 in a sliding 600 s window; exceeding it DISABLES the child until
the proxy restarts. A fresh `ChildClient` is swapped in only after it
initialized, listed a tool **name** set equal to the startup set, and is still
alive, in one synchronous block with `state = "ready"`; a changed name set
disables the child, because routing to a tool that vanished is worse than
saying the child is gone. Child request ids come from a per-slot counter that
is never reset, so a late reply from a previous incarnation cannot match a new
call.

Each child runs with `start_new_session=True`, so its pid is its process group.
The stop ladder closes stdin, waits, signals the group with TERM and then KILL,
reaps, and then sweeps the group once per incarnation until an observed `ESRCH`
clears the recorded `pgid`. The sweep is what reaches a grandchild of a child
that exited cleanly on stdin EOF, which a "never signal a reaped pid" guard
alone would leave running.

## One protocol version on both transports

`Scripts/mcp-proxy.py:McpServer` declares `PROTOCOL_VERSION = "2024-11-05"` as
its first member — the fleet literal — and answers it on stdio and on HTTP, and
sends it to its children through
`Scripts/mcp-proxy.py:_child_init_params`. The HTTP layer only validates the
client's `MCP-Protocol-Version` header against `SUPPORTED_PROTOCOL_VERSIONS`,
which begins with `PROTOCOL_VERSION` by name and adds `2025-03-26`,
`2025-06-18` and `2025-11-25`; a missing header assumes `2025-03-26`, which is
in the set. As a client of its children the proxy accepts whatever version a
child answers, since the payloads pass through anyway.

The ai-soul SDK accepts `2024-11-05`, and its Streamable HTTP behaviour is not
keyed to the negotiated version, so nothing is gained by answering a newer one.
And `tests/test_protocol_version.py` requires every dict carrying a
`"protocolVersion"` key to read `self.PROTOCOL_VERSION`, with the literal
appearing nowhere else. **Negotiation** would mean either overwriting the reply
downstream — evading the gate — or changing the gate. **A per-transport
`HttpMcpServer` subclass** overriding the member would put a second version
declaration in the file that the gate does not analyse, and would answer a
different version per transport for no client benefit. Both were rejected.

## Cancel: the `task` class, and the arm that tells the child

An upstream `notifications/cancelled` is handled on the loop thread before
dispatch and only calls `task.cancel()` on the request's task — the shape
`tests/test_cancel.py`'s group A gates, in which nothing reachable from the
hook may write a reply. The `CancelledError` lands at the `await` on the child's
reply inside `Scripts/mcp-proxy.py:rpc`, whose `except asyncio.CancelledError`
arm sends `notifications/cancelled` with the **child's** request id to the
owning child, if the child has not answered yet, and re-raises. `initialize`
never gets a notice. The timeout arm does the same on a per-child
`call_timeout` (default 3600 s) and answers the caller `isError`. So a cancel
travels one hop further than in any other fleet server, and the child then
applies its own reclaim class. The ADR 0008 addendum of 2026-10-03 records the
same fact from the read-loop side
([[0008-a-serialized-read-loop-looks-like-a-dead-server]]).

**Why no `forward` reclaim class.** `task` is accurate and measured:
`tests/test_cancel.py` checks a `task` row against coroutine dispatch, which
holds. Forwarding the notice is a property of the child client's arm, and it is
proven end to end against the stub child in the proxy's own suite (case D1). A
sixth class would add an AST analyser, its controls and its documentation for
one server. **Revisit trigger:** a second server that forwards cancels to an
MCP peer.

**Ids and tokens stay where the call is.** The upstream id never leaves the
awaiting task's frame and never reaches a child, and `_pending` maps only
`child_id -> Future`. A shared `upstream_id <-> child_id` table was rejected:
two HTTP sessions may use the same id, and such a table must be cleaned on
cancel, timeout, death and restart, each a race. A routable upstream
`progressToken` is replaced by a per-slot `px:<slot>:<n>` token and progress is
routed back with the caller's original token and JSON type; an unroutable one
is stripped before the call.

## The HTTP front: ThreadingHTTPServer, and no second executor

The Streamable HTTP endpoint (Phase 2) is the stdlib
`http.server.ThreadingHTTPServer` (`Scripts/mcp-proxy.py:_ProxyHttpServer`),
bridged into the asyncio core with `run_coroutine_threadsafe` and
`call_soon_threadsafe`, every handler-thread wait bounded by
`_HTTP_BRIDGE_TIMEOUT_S`. Handler threads own only their socket and a
`queue.Queue`; sessions, the cancel registry, the tool table and child state
live on the loop thread. The server runs on plain `threading.Thread`s, so the
stdin reader — `ThreadPoolExecutor(max_workers=1, thread_name_prefix="proxy-stdin")`
in `McpServer.run`, the shape `tests/test_read_loop.py` gates — stays the
file's only executor ([[0008-a-serialized-read-loop-looks-like-a-dead-server]]).

**`asyncio.start_server` with a hand-rolled HTTP/1.1 parser** was rejected:
every byte of request parsing — header folding, duplicate `Content-Length`,
`Transfer-Encoding` precedence, request-line limits, slow-header timeouts —
would be new, unaudited, security-relevant code with no repo precedent, on an
authenticated endpoint. Not having threads does not justify that surface. **A
worker-pool dispatch like mcp-jenkins** was rejected because a cancel cannot
reach a worker thread (the proxy would become `reply-only` and children would
compute abandoned calls), and the child id counter would need a lock the loop
already provides.

Two answer-shape choices follow from the SDK, not from taste. **Every
`tools/call` is answered as `text/event-stream`:** headers as soon as the call
started, a `: keepalive` comment every 15 s while it waits, progress events,
then the response, so a long call fires neither the client's header timeout nor
its body timeout. **No event carries an `id:` line:** an event id marks the
stream resumable, and a stream closed without a response — which is what a
cancelled call produces — would then make the SDK reconnect with a GET the
proxy answers 405. **A disconnect is not a cancel;** only
`notifications/cancelled`, DELETE, idle eviction and shutdown cancel, because a
dropped connection must not abort a forge build.

The endpoint requires a bearer token (from a mode-0600, owner-only
`--token-file`, or from `MCP_PROXY_TOKEN`, which `main()` pops from the
environment before any child is spawned), compared in constant time and never
logged; binds an IPv4 literal, loopback by default, anything else only with
`--allow-remote`; checks Host on a loopback bind and Origin when sent; and
bounds connections, sessions, in-flight request tasks, body size and the header
phase.

## No canonical source for the child client

[[0014-a-canonical-source-is-a-domain]] allows a new source when a shareable
block's domain cannot live in an existing one without making it a shelf, and
[[0025-generate-do-not-import]] generates code that is **shared**. A
`_mcp_mcpclient.py` would have exactly one host. The other MCP clients in the
tree — the test harness's and the smoke harness's — are synchronous
one-request clients and are not region hosts. One host buys a canonical file, a
`CANONICAL_NAMES` entry in `Scripts/amalgamate.py`, a census row and a
generated-region test, for zero drift protection. **Revisit trigger:** the day
a second `Scripts/` file needs an MCP client, lift it then.
[[0012-the-transport-tier-diverged]] keeps the server transport tier per
server; this is the client side and is not bound by that refusal, nor
re-decided by it.

**`_mcp_lsp.py`'s `_request` block is not reused** (`Scripts/_mcp_lsp.py`). It
speaks LSP: it cancels with `$/cancelRequest {id}`, while MCP needs
`notifications/cancelled {requestId}`, and its timeout arm returns an error
without telling the peer, while the MCP rules say the sender should cancel on
timeout. 0014 also forbids asking the LSP source for MCP code. Because the
hand-copy census flags any top-level or class-member name that is a canonical
block name, the hand-written methods are `rpc` and `notify_child`, never
`_request` / `_notify`. The same census is why the proxy takes no further
regions "for consistency": it never decodes tool arguments, reads no boolean
param and has no worker pool, so `_json_error_window`, `_ensure_dict`,
`_bool_param` and `MAX_INFLIGHT_REQUESTS` would be dead code. The accepted
consequence is that the measured sentence on [[generated-regions]] naming the
blocks generated into every server changed when it was re-rendered.

## What was measured

The proxy joined every fleet gate in the same commit as the server — each turns
red the moment an undeclared `mcp-*.py` exists — and got its own suite against
an adversarial stub child that can crash, stall, flood, pollute stdout,
paginate, emit progress after its result and ask the proxy for sampling. The
expected roster moves were read_loop 47→49, cancel 84→86, wire_log 53→55,
handler_crash 58→60, protocol_version 71→74, py_deps 57→58, generated_region
unchanged at 97, smoke 15→16 servers. `mcp_proxy` was 57 cases after Phase 1
(stdio), 90 after Phase 2 (group J, HTTP), and 103 after the three
security-review rounds (A13, G6, G7, J34-J42, L4). The final gate of the
round-3 pass: `mcp_proxy` 103/103 on three consecutive runs, and the full fleet
35 suites, 5074 cases, 0 fail, 204 info, in 568.6 s. The implementation
inspection closed COMPLETE in its third round.

## The security review, three rounds

Three `p:security-review` rounds in code mode, all **APPROVE**, all 2026-10-03:

- **Round 1** — 44 candidates, 23 verified: 0 HIGH, 1 MEDIUM. The MEDIUM was a
  pre-auth slowloris (F8): the header phase had a per-`recv` timeout only, so a
  client trickling one header byte at a time held a `--max-connections` slot
  indefinitely, and enough of them held them all. Fixed by a **total** header
  deadline: the request line plus headers of every request, keep-alive
  included, must arrive within `_HTTP_HEADER_TIMEOUT_S` (10 s) in total, each
  `recv` getting only the time left
  (`Scripts/mcp-proxy.py:_HeaderDeadlineReader`), gated by J34. The other
  verified findings were closed in the fix pass by code or by a declared limit
  in the module docstring, except those listed in the next section.
- **Round 2**, over the round-1 hunks — 13 candidates, 6 verified, LOW or INFO,
  0 HIGH, 0 MEDIUM; fixed.
- **Round 3**, over the round-2 hunks — 6 candidates, 3 verified LOW, fixed:
  - a request id that is not a string or an integer is refused at intake on
    both transports and never dispatched or echoed
    (`Scripts/mcp-proxy.py:_bad_request_id`) — a deeply nested list id had made
    the reply's `json.dumps` raise, and its fallback with it;
  - the serialisation fallback re-embeds the id only if the id itself
    serialises (`Scripts/mcp-proxy.py:_fallback_id`);
  - `Scripts/mcp-proxy.py:_log_value` appends a `...` cut marker whenever either
    of its two cuts happened, so a capped value is never mistaken for a whole one.

**A fourth round was skipped by user decision** (2026-10-03): three consecutive
APPROVE verdicts, and the remaining findings LOW on a loopback,
single-principal tool.

## Verified, and deliberately not fixed

Eight round-1 findings were verified and left unfixed on purpose, and one more
was deferred out of this change:

- **F1** — sessions are not bound to the token that opened them. Declared: one
  token is one principal, session ids are 256-bit random and never logged in
  full, so knowing one means the holder was given it.
- **F4** — no TLS; with `--allow-remote` the bearer travels in plaintext.
  Declared: a non-loopback bind belongs on a VPN interface, never a public one.
- **F9** — `--max-inflight` is one global budget, so abandoned calls can hold
  all of it until their call timeout. Roadmap R-0064 (a per-session share).
- **F10** — the session table can be exhausted and a session holds its slot for
  a full idle period. Roadmap R-0065.
- **F15** — `--config-json` puts the child config, `env` included, on the argv
  in stdio mode, where every local user can read it; HTTP mode already refuses
  the flag. Roadmap R-0066.
- **F19** — `NaN` and `Infinity` literals are accepted and re-emitted as
  non-JSON. Roadmap R-0067.
- **F20** — huge integer literals parse at quadratic cost on Python before
  3.9.14 (CVE-2020-10735). Declared in the module docstring; roadmap R-0068.
- **F42** — pre-auth refusals use the stdlib's error pages. Roadmap R-0069.
- **F33, deferred** — `--log-file` is opened without `O_NOFOLLOW` and
  `fchmod`ed without an owner check. The defect lives in the canonical
  `Scripts/_mcp_logging.py` and is generated into every server, so the fix is
  fleet-wide, not the proxy's; the user deferred it on 2026-10-03 as roadmap
  R-0063 ([[0022-a-someday-maybe-is-a-roadmap-item]]).

## Alternatives rejected

1. **Prefixed tool names.** The composing choice: it renames what the model
   sees. Unprefixed names plus a refused collision instead.
2. **First-wins or last-wins on a duplicate tool name.** A precedence rule is
   the ambiguity [[0015-ambiguity-is-the-defect]] refuses.
3. **An answer ceiling in the proxy.** A second cut under an undeclared policy
   ([[0013-the-ceiling-is-a-payload-class]]); the proxy has a framing ceiling
   only.
4. **Lazy spawn.** The toolset would depend on call history.
5. **Automatic retry after a child's death.** Tools are not idempotent.
6. **Protocol-version negotiation, or a per-transport subclass.** Evades or
   changes `tests/test_protocol_version.py`, for no client benefit.
7. **A `forward` cancel reclaim class.** `task` is accurate; the forwarding is
   the arm's property. Revisit on a second server that forwards cancels.
8. **A shared `upstream_id <-> child_id` remap table.** Collides across
   sessions and must be cleaned on every exit path.
9. **`asyncio.start_server` with a hand-rolled HTTP/1.1 parser.** New
   security-relevant parsing code with no precedent.
10. **A worker-pool dispatch.** A cancel cannot reach a worker thread.
11. **A seventh canonical source, `_mcp_mcpclient.py`.** One host, zero drift
    protection. Revisit when a second `Scripts/` file needs an MCP client.
12. **Reusing `_mcp_lsp.py :: _request`.** Wrong protocol and wrong domain.
13. **Taking more generated regions for consistency.** Dead code in the proxy.

## Declared limits

Accepted and declared in the module docstring of `Scripts/mcp-proxy.py`, not
gated:

- **Inherited from the fleet:** the upstream stdout write is blocking on the
  loop thread, and the upstream stdin line is uncapped because the read-loop
  gate forbids a capped `readline`. Exception text (`<Type>: <msg>`) reaches the
  caller in the `-32603` reply and the `isError` result (CWE-209); its reader is
  the stdio parent or a bearer holder, the principal that can call the tools.
- **Process lifetime:** a grandchild that put itself in its own session
  survives the group sweep, and on a proxy SIGKILL only the children see stdin
  EOF; a grandchild holding a child's stdout delays that child's death
  detection; the sweep keeps a pid-wrap residual and a D-state survivor keeps
  `pgid` set; the post-SIGKILL wait for a D-state leader is unbounded; and
  `ChildSlot.stop()` waits at most `_STOP_WAIT_S` for a cancelled restart task.
- **Restart and cancel:** a schema-only change after a restart is not detected,
  since only the name set is compared; on Python 3.9 `asyncio.wait_for` can
  swallow a cancel that races the child's reply, which lets the reply through,
  harmlessly.
- **Logging:** child-side and HTTP logging are outside the `wire_log` gate and
  structure-only by construction, gated by the proxy's own suite; children
  inherit the proxy's stderr unfiltered.
- **HTTP:** IPv4 only, so a client should be pointed at `127.0.0.1`, not
  `localhost`; the Host check runs only on a loopback bind; no failed-auth
  throttling, and the token is checked by length and charset, not entropy; an
  env-sourced token stays readable in `/proc/<pid>/environ` on Linux; a
  disconnected client holds its thread and connection slot until the next write
  fails; a request the loop does not start in time is answered 503 but may still
  run, and an `initialize` stalled that way holds a session slot until idle
  eviction; up to `--max-inflight` abandoned calls can keep children busy; a
  cancel that overtakes its request across two handler threads is ignored; the
  wire half of the 202 for a cancelled non-`tools/call` request is not driven
  live; the `Accept` header is not enforced; queued progress per response is
  capped, newest dropped; and the undici 300 s timeout defaults are an
  assumption, not verified.
- **Configuration:** the config's `env` is trusted operator input, as trusted
  as the `command` it already names; only `MCP_PROXY_TOKEN` is refused there.
  3.10+ APIs outside the fleet's fixed list and the suite's K4 case are a blind
  spot.

## Out of scope, deliberately

TLS on the listener; per-server endpoints; the legacy HTTP+SSE `/sse` transport
and the stateless 2026-07-28 revision; resources and prompts (`-32601`); SSE
resumability and server-initiated GET streams; child→client requests other than
`ping` (refused with `-32601` and logged); acting on `tools/list_changed`
(logged, ignored); Linux `PR_SET_PDEATHSIG`; IPv6 on the listener (a revisit
item, not a gap); and registering the proxy in `~/.claude.json`.

## Addendum (2026-10-07): three ports from llm-router (R-0069, R-0076, R-0075)

Three fixes the llm-router's copy of this HTTP front already carried ([[0028-route-by-model-translate-at-the-edge]]) were ported back into `Scripts/mcp-proxy.py`, each with a `mcp_proxy` case written red first; the suite went from 103 to 106 cases, group J from J1-J42 to J1-J45.

### R-0069: the stdlib's pre-auth refusals are fixed and body-less (c69b643, J43)

`send_error` is overridden: the refusals the stdlib sends itself before any proxy check (400, 414, 431, 501, 505) ignore `message` and `explain`, which can quote the request line, the method or a header, and go out through `_refuse` with the fixed reason phrase from the status table, no body, `Content-Length: 0`, `Connection: close`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. The router's HTTP/0.9 guard was ported into `_refuse`: `parse_request` leaves `request_version` at `HTTP/0.9` before it parses the version word (and keeps it for a bare two-word `GET /`, which reaches `do_GET`), and the stdlib writes no status line or header for HTTP/0.9, so every refusal is now sent with an HTTP/1.1 head. J43 sends nine raw requests (bad version word, four-word line, HTTP/0.9 non-GET, HTTP/2.0, an over-long request line, an unknown method, an over-long header line, too many headers, a bare HTTP/0.9 `GET`) and checks the status, the empty body, the headers, the fixed reason phrase and that no byte of the request's marker is reflected.

This closes **F42** ("pre-auth refusals use the stdlib's error pages") in "Verified, and deliberately not fixed" above: it is no longer an open finding.

### R-0076: 100 Continue only after auth and framing pass (6e91e1d, J44)

`handle_expect_100`, which the stdlib calls from `parse_request` before any auth, now sends nothing. `_post` sends the interim `100 Continue` itself after the bearer, Origin/Host, media type and body-cap checks, right before the body read, and only for `Expect: 100-continue` (case-insensitive) on HTTP/1.1; any other `Expect` value is ignored, with no 100 and no 417, as the router does (its V37, ADR 0028 deviation 18). J44 checks that a wrong bearer, a foreign Origin, a wrong media type and a `Content-Length` over the cap each get only their final status with no interim 100, that an accepted request gets exactly `[100, 200]` with its body sent after the 100, and that `Expect: tf-other` gets a plain 200.

### R-0075: the ready file is removed on an ordered shutdown, own pid only (1748f46, J45)

`serve_http`'s shutdown now ends by calling `_http_remove_ready_file`, which opens the ready file with `O_NOFOLLOW`, reads its JSON, and unlinks it only when its `pid` equals `os.getpid()`; a symlink at the path, another pid, non-JSON or an unreadable file is left alone and logged at DEBUG, structure only. It never raises. This is the router's V24 fix (ADR 0028 deviation 29). J45 checks three modes after SIGTERM: the proxy's own file is removed; a file rewritten with a foreign pid is left unchanged; and a symlink at the ready path pointing at a sentinel that holds the proxy's OWN pid leaves both the link and the sentinel, so only `O_NOFOLLOW`, not the pid check, can be what protects them.

Declared, not gated: a file replaced between the read and the unlink is not seen, and the replacement is removed; SIGKILL, or any exit that skips the ordered shutdown, leaves the file behind with a dead pid. These join the declared limits above.

## Addendum (2026-10-07): strict JSON on both fronts and the generated _log_value (R-0067, R-0068, R-0070)

Two fleet-wide commits on 2026-10-07 changed the proxy. They closed two findings listed under "Verified, and deliberately not fixed" and replaced one hand-written helper with generated code.

### R-0067 and R-0068: every frame is parsed and written strictly (`d4a241b`, G8, J46)

The proxy took the six strict-JSON blocks from `Scripts/_mcp_json.py` as a new generated region `Scripts/mcp-proxy.py:_strict_loads`. It routes every frame it reads or writes through them, on both fronts and in both directions:

- the stdio read loop;
- the child reader;
- `_send_line` to a child;
- `_write` and its fallback;
- `_fallback_id`;
- the HTTP `_post`, `_send_json` and `_stream_sse`.

`NaN`, `Infinity`, `-Infinity`, a float that overflows to an infinity (`1e999`) and an integer literal over 4300 characters, sign included, are each refused as a `json.JSONDecodeError` on every Python. They are answered `-32700` on both fronts. A non-finite float is never emitted.

- **F19 / R-0067 is closed.** `NaN` and `Infinity` are no longer accepted and re-emitted as non-JSON.
- **F20 / R-0068 is closed.** The module docstring's old declared limit is replaced: "on Python < 3.9.14 a huge integer literal parses at quadratic cost (CVE-2020-10735)". The literal is now refused before `int()` sees it, so 3.9.6's missing int-digit limit no longer matters on a frame.

The config and the ready file are still read with plain `json.loads`, because neither is a peer's frame. `tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS` declares both sites, and so does the ready-file write. `mcp_proxy` went from 106 to 108 cases.

### A new declared limit: a refused child line is dropped

The child reader treats a line the strict parse refuses exactly like any other non-JSON stdout line under the frame limit. The line is dropped and logged at DEBUG with the child's name and the line's length only. The upstream call that line would have answered is not failed at once. It waits for the child's `call_timeout`. This is declared in the module docstring and not gated.

### R-0070: `_log_value` is the generated block (`72ed9b4`)

Round 3 above credits `Scripts/mcp-proxy.py:_log_value` with the `...` cut marker. The proxy's hand-written copy and its two bounds are gone. The name now resolves to the block generated from `Scripts/_mcp_logging.py`, which has the same behaviour, cut marker included, and proxy case L4 stays green. The "Logging" declared limit stands as written. Child-side and HTTP logging remain outside the `wire_log` gate, and a child-controlled method or id goes through `_log_value`.

### Not changed

Alternative 13 ("Taking more generated regions for consistency") is not reversed. Each of the two new regions has live call sites in the proxy and is required fleet-wide by a gate: `tests/test_generated_region.py` group H for the strict blocks, and `tests/test_wire_log.py` group C for `_log_value`.
