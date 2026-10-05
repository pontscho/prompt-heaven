---
name: mcp-proxy
type: component
status: active
title: mcp-proxy — one MCP endpoint relaying a project root's child servers
description: The stdlib-only relay in front of a project root's child MCP servers -- unprefixed tool names with a duplicate refusing startup, verbatim payloads under a framing ceiling, eager start, a restart budget that disables, a cancel forwarded one hop to the child, and a Streamable HTTP front for ai-soul; how it is tested and what it declares rather than gates.
sources:
  - Scripts/mcp-proxy.py
  - tests/test_mcp_proxy.py
  - tests/files/mcp_proxy/tf_stub_child.py
verified:
  commit: 3bedc59
  date: 2026-10-03
links:
  - 0027-the-proxy-relays-it-never-composes
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - scripts
  - tests
  - generated-regions
  - 0010-a-handler-failure-must-reach-iserror
  - 0013-the-ceiling-is-a-payload-class
  - 0015-ambiguity-is-the-defect
---

# mcp-proxy

`Scripts/mcp-proxy.py` is one MCP endpoint in front of the child MCP servers of
one project root: it spawns every child its JSON config names, lists their
tools, exposes all of them upstream under their own names, and routes each
`tools/call` to the child that owns the tool `Scripts/mcp-proxy.py:McpServer`.
Its consumer is ai-soul, not Claude Code, so it is deliberately not registered
— the smoke table carries it as an unregistered server
`Scripts/_mcp_smoke_test.py:SERVERS`. Several roots mean several instances.

Everything below is the *shape*. Why it relays and never composes — and every
alternative that lost — is frozen in [[0027-the-proxy-relays-it-never-composes]];
where it sits in the fleet is [[scripts]].

## The shape

**Configuration never touches a process.** The config comes from exactly one of
`--config` or `--config-json`, unknown keys and duplicate child names are
refused, and every refusal names the key or child but never echoes a value
`Scripts/mcp-proxy.py:load_config`. A child argument that is exactly `{root}`
becomes the project root; an optional top-level `scripts` directory (realpath,
must exist) replaces `{scripts}` — the whole string or a leading `{scripts}/` —
in a child's `command`, `wrapper` and `args`, and a child using it without the
key is refused `Scripts/mcp-proxy.py:_cfg_subst_scripts`. A child's argv is a list, never a shell
string `Scripts/mcp-proxy.py:build_child_argv`, and its environment is the
proxy's own minus the bearer-token variable `Scripts/mcp-proxy.py:build_child_env`.
Each child is spawned in its own session with the project root as its cwd
`Scripts/mcp-proxy.py:start`.

**Eager start: the whole toolset or none of it.** Every child is initialized,
sent `notifications/initialized` and listed to the end of its `nextCursor`
chain `Scripts/mcp-proxy.py:_bring_up` before the proxy reads its first
upstream message `Scripts/mcp-proxy.py:_amain`. A repeated cursor or too many
pages is a cursor loop and refuses the start
`Scripts/mcp-proxy.py:_list_all_tools`; any child failing stops every child and
the proxy exits with a startup error.

**Unprefixed names; a duplicate refuses startup.** The upstream `tools/list` is
the union of the children's tools in config order, each tool dict relayed as
the child sent it and never paginated upstream
`Scripts/mcp-proxy.py:_handle_message`. A tool name two children expose is not
resolved by precedence: every child is stopped and the error names the tool and
both children `Scripts/mcp-proxy.py:start_children` —
[[0015-ambiguity-is-the-defect]].

**Payloads are relayed verbatim.** A child's result or JSON-RPC error goes
upstream unchanged; a child that cannot be reached, or a failure while
forwarding, is a tool result with `isError` set, never a protocol error
`Scripts/mcp-proxy.py:_handle_tool_call` — [[0010-a-handler-failure-must-reach-iserror]].

**A framing ceiling, never an answer ceiling.** The child stdout reader's
`limit=` is `Scripts/mcp-proxy.py:_MAX_FRAME`, or the config's `frame_limit`
inside `Scripts/mcp-proxy.py:_FRAME_RANGE`. It bounds one newline-delimited
message, not how much of an answer the caller gets: a line over it is a
desynced stream, so the incarnation dies and is restarted, while a non-JSON
line under it is pollution and is dropped `Scripts/mcp-proxy.py:_reader_loop`.
There is no per-call output cap — the child already applied its own
[[0013-the-ceiling-is-a-payload-class]].

**Restart is bounded, and the end state is DISABLED.** The death of the current
incarnation while the slot is `ready` is the only entry into recovery
`Scripts/mcp-proxy.py:on_child_death`, and it starts exactly one
`Scripts/mcp-proxy.py:_restart_loop`, which owns the backoff ladder
`Scripts/mcp-proxy.py:_RESTART_BACKOFF_S` (with jitter) and every retry. Each
death is charged once against `Scripts/mcp-proxy.py:_RESTART_BUDGET` in a
sliding `Scripts/mcp-proxy.py:_RESTART_WINDOW_S` window
`Scripts/mcp-proxy.py:_charge_budget`; past it, or when a restarted child lists
a different tool-name set, the slot is disabled until the proxy restarts. A
fresh incarnation is swapped in only after it initialized, listed the same name
set and is still alive. A call is never retried for the caller: one made while
the slot restarts or is disabled is refused at once with a message saying so
`Scripts/mcp-proxy.py:call`.

**Ids and progress tokens stay where the call is.** A child's request ids come
from a per-slot counter that a restart never resets, so a late reply from a dead
incarnation cannot match a new call `Scripts/mcp-proxy.py:ChildSlot`. The
upstream id never reaches a child; a routable upstream `progressToken` is
replaced by a per-child `px:<child>:<n>` token on a copy of the params
`Scripts/mcp-proxy.py:rpc`, and progress comes back carrying the caller's own
token `Scripts/mcp-proxy.py:_handle_tool_call`.

**A cancel travels one hop further.** Upstream `notifications/cancelled` is
handled on the loop thread before anything is dispatched and only cancels the
request's task `Scripts/mcp-proxy.py:_cancel_request`. The `CancelledError`
lands on the await inside `ChildClient.rpc`, whose cancel arm sends
`notifications/cancelled` with the **child's** own request id — unless the child
already answered, and never for `initialize` — then re-raises; the timeout arm
does the same on a per-child `call_timeout` (default
`Scripts/mcp-proxy.py:_DEFAULT_CALL_TIMEOUT_S`) `Scripts/mcp-proxy.py:rpc`.
The fleet's cancel gate records it in the `task` reclaim class
`tests/test_cancel.py:FLEET`.

**One reader executor, coroutine dispatch.** Every request runs as its own task,
and the stdin reader is the file's only executor `Scripts/mcp-proxy.py:run`:
the handlers are coroutines because each child client keeps its ids, pending
futures and progress routes on the loop thread
`Scripts/mcp-proxy.py:ChildClient` — the per-server dispatch decision
[[0008-a-serialized-read-loop-looks-like-a-dead-server]] requires be audited,
not copied.

**Shutdown leaves no orphan.** Signal handlers are installed before the first
child is spawned, so a signal during a slow startup still stops what was
started `Scripts/mcp-proxy.py:_amain`. Each incarnation's stop ladder closes
stdin, signals the child's process group, reaps, and sweeps the group until it
is empty `Scripts/mcp-proxy.py:stop` — which is what reaches a grandchild of a
child that exited cleanly. The exit codes are listed in the module docstring
`Scripts/mcp-proxy.py`.

The plumbing it shares with the fleet is generated, not imported: the
`_mcp_logging.py` and `_mcp_json.py` regions only `Scripts/mcp-proxy.py:_configure_logging`
`Scripts/mcp-proxy.py:_result` ([[generated-regions]]). The child MCP client is
hand-written, and its methods are named `rpc` / `notify_child` so the hand-copy
census never mistakes them for the LSP request block.

## The Streamable HTTP front

With `--http` the same core serves Streamable HTTP instead of stdio
`Scripts/mcp-proxy.py:serve_http`, on ONE endpoint (`/mcp` unless `--http-path`
says otherwise) `Scripts/mcp-proxy.py:_HTTP_DEFAULT_PATH`, with ONE protocol
version on both transports — `PROTOCOL_VERSION` is answered on each and sent to
the children, and the HTTP layer only validates the client's
`MCP-Protocol-Version` header against `SUPPORTED_PROTOCOL_VERSIONS`
`Scripts/mcp-proxy.py:McpServer`. The listener is the stdlib
`ThreadingHTTPServer` `Scripts/mcp-proxy.py:_ProxyHttpServer`; handler threads
own only their socket and a queue and reach loop state through a bridge whose
every wait is bounded `Scripts/mcp-proxy.py:_bridge`.

**Access.** The bind is an IPv4 literal, loopback by default (`127.0.0.1`); any
other address needs `--allow-remote`, and IPv6 and host names are refused
`Scripts/mcp-proxy.py:load_http_settings`. The listener is bound before any child
is spawned `Scripts/mcp-proxy.py:_amain`. A bearer token is required, from
exactly one of `--token-file` — opened without following a symlink, owned by
you, with no group or other permission bits `Scripts/mcp-proxy.py:_http_token_file`
— or `MCP_PROXY_TOKEN`, which `main()` pops from the environment before anything
is spawned, so no child inherits it `Scripts/mcp-proxy.py:main`. The token must
be at least `Scripts/mcp-proxy.py:_TOKEN_MIN_LEN` printable ASCII characters
`Scripts/mcp-proxy.py:_http_token_value`, is compared in constant time, and is
never logged. HTTP mode refuses `--config-json`, since argv is visible in `ps`
`Scripts/mcp-proxy.py:_http_cli_defaults`.

**Every request, before its body.** A malformed header line, a wrong path, a
`Host` other than the loopback bind's own (checked on a loopback bind only), an
`Origin` not named by `--allowed-origin` (an absent one is accepted) and a bad
bearer are each refused before anything is read
`Scripts/mcp-proxy.py:_precheck`. The request line and headers must arrive
within `Scripts/mcp-proxy.py:_HTTP_HEADER_TIMEOUT_S` **in total**, each `recv`
getting only the time left `Scripts/mcp-proxy.py:_HeaderDeadlineReader`, and the
per-recv timeout stays that short until the bearer check passes. `Content-Type`
must be exactly `application/json` (a `;` parameter aside); a repeated
`Content-Type`, `Mcp-Session-Id` or `MCP-Protocol-Version` header is refused;
chunked bodies and batches are refused; an `initialize` without an id is
refused; and an id that is neither a string nor an integer is refused `-32600`
with id null, before any session is touched `Scripts/mcp-proxy.py:_post` —
stdio refuses it the same way `Scripts/mcp-proxy.py:_bad_request_id`.
Connections, sessions and in-flight requests are capped (`--max-connections`,
`--max-sessions`, `--max-inflight`), and idle sessions are evicted after
`--session-idle` `Scripts/mcp-proxy.py:_HTTP_ONLY_FLAGS`.

**Answer modes.** Every `tools/call` is answered as `text/event-stream`: the
headers go out at once, a `: keepalive` comment follows every
`Scripts/mcp-proxy.py:_HTTP_KEEPALIVE_S` while the call waits, then progress
events and the response; no event carries an `id:` line, so the SDK never
treats a stream as resumable `Scripts/mcp-proxy.py:_stream_sse`. Every other
request is answered as JSON `Scripts/mcp-proxy.py:_answer_json`. A client that
disconnects does NOT cancel its call `Scripts/mcp-proxy.py:_disconnected`;
`notifications/cancelled` and DELETE do `Scripts/mcp-proxy.py:do_DELETE`. A
cancelled `tools/call` closes its stream with no response; `202` with no body is
kept only for a cancelled non-`tools/call` request.

**Running it for ai-soul.** Start
`python3 Scripts/mcp-proxy.py --http --config <config.json> --project-root <root>
--token-file <token> --port <N>` (`--port 0`, the default, picks an ephemeral
port; `--ready-file` reports it), then point the SDK's
`StreamableHTTPClientTransport` at `http://127.0.0.1:<N>/mcp` — `127.0.0.1`, not
`localhost`, because the listener is IPv4-only — with
`Authorization: Bearer <token>` in `requestInit.headers`. Two client-side
timeouts bound a long call, and the proxy can lift neither. The SDK's own
request timeout is 60 s by default, and `: keepalive` comments are not progress
notifications and do not reset it, so long forge or jenkins calls need
`timeout` and/or `resetTimeoutOnProgress` on the ai-soul side (progress is
forwarded, so reset-on-progress is effective). Node's `fetch` (undici) is
ASSUMED, not verified, to default to a 300 s `headersTimeout` and `bodyTimeout`;
the immediate SSE headers and the keepalive comments keep both from firing
however long the call runs. The proxy's own per-child call timeout is
independent of both.

## How it is tested

The `mcp_proxy` suite `tests/test_mcp_proxy.py` drives the proxy live as a
subprocess over stdio and Streamable HTTP in front of an adversarial stub child
`tests/files/mcp_proxy/tf_stub_child.py`, which crashes, stalls, floods,
pollutes its stdout, paginates, emits progress after its result and asks the
proxy for sampling on command; nothing imports it. The groups follow the shape
above: config refusals in-process `tests/test_mcp_proxy.py:group_a`, eager
start and the duplicate refusal `tests/test_mcp_proxy.py:group_b`, routing
`tests/test_mcp_proxy.py:group_c`, the cancel forwarded with the child's own id
`tests/test_mcp_proxy.py:group_d`, progress mapping
`tests/test_mcp_proxy.py:group_e`, death, backoff and the restart budget
`tests/test_mcp_proxy.py:group_f`, framing `tests/test_mcp_proxy.py:group_g`,
`call_timeout` `tests/test_mcp_proxy.py:group_h`, orphan-free shutdown
`tests/test_mcp_proxy.py:group_i`, the HTTP front in three groups
`tests/test_mcp_proxy.py:group_j` `tests/test_mcp_proxy.py:group_j_hardening`
`tests/test_mcp_proxy.py:group_j_strict`, static AST rules over the proxy source
`tests/test_mcp_proxy.py:group_k` and sandbox hygiene
`tests/test_mcp_proxy.py:group_l`. Its case count is declared once, in
`tests/run.py:SUITES`, and checked against the run; the per-group detail is the
suite's row in [[tests]].

The proxy also sits in every fleet gate, each of which turns red on an
undeclared `mcp-*.py`: its own row in the read-loop, cancel, wire-log,
handler-crash and protocol-version tables, the 3.10+ API scope of the
dependency gate `tests/test_py_deps.py:NEW_39_SCOPE`, and one smoke row that
runs it with ONE stub child exposing ONE tool, so the fleet's one-tool
invariant still holds there `Scripts/_mcp_smoke_test.py:SERVERS`. That row makes
`Scripts/` depend on the stub through the repo layout: without `tests/` the
stub fails to start and the smoke harness reports SKIP, not FAIL.

## Declared limits

The module docstring lists what is accepted rather than gated
`Scripts/mcp-proxy.py`, and [[0027-the-proxy-relays-it-never-composes]] carries
the reasoning and the review findings left unfixed on purpose. The ones a user
of the proxy meets first:

- A restarted child whose tool **names** are unchanged but whose schemas changed
  is not detected; only the name set is compared.
- A disconnect is not a cancel, so up to `--max-inflight` abandoned calls can
  keep children busy until they finish or hit their call timeout.
- There is no TLS: with `--allow-remote` the bearer travels in plaintext, so a
  non-loopback bind belongs on a VPN interface, never a public one. There is no
  failed-auth throttling, and the token is checked by length and charset, never
  by entropy.
- One token is one principal: sessions are not bound to the token that opened
  them.
- Child-side and HTTP logging are outside the fleet's wire-log gate; they are
  structure-only by construction and gated by the proxy's own suite instead,
  and children inherit the proxy's stderr unfiltered.
- A grandchild that put itself in its own session survives the group sweep.
