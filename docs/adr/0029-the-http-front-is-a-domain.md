---
name: 0029-the-http-front-is-a-domain
type: adr
status: active
title: The stdlib HTTP front is a canonical domain
description: Decision to lift the identical half of mcp-proxy's and llm-router's stdlib HTTP/1.1 server fronts into a thirteenth canonical source, Scripts/_mcp_httpfront.py -- connection admission, the total header deadline, request-line hardening, the bearer token value check, the ready file and the stdlib-refusal header pair -- generated into both hosts with the server and handler members rendered inside the host classes, every host policy injected through an argument or a self seam, and every hand-written stdlib override declared row by row in a table a gate reads; with the agent decisions taken overnight for user review, the policy seams, the behaviour changes, the adaptation table and its gates, the risk table, the red runs and the drill, the alternatives rejected, and the declared limits.
sources:
  - Scripts/_mcp_httpfront.py
  - Scripts/amalgamate.py
  - Scripts/mcp-proxy.py
  - Scripts/llm-router.py
  - tests/test_generated_region.py
  - tests/test_llm_router.py
  - tests/test_mcp_proxy.py
verified:
  commit: 18f2b24
  date: 2026-10-07
links:
  - generated-regions
  - mcp-proxy
  - llm-router
  - tests
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0012-the-transport-tier-diverged
  - 0014-a-canonical-source-is-a-domain
  - 0022-a-someday-maybe-is-a-roadmap-item
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0025-generate-do-not-import
  - 0027-the-proxy-relays-it-never-composes
  - 0028-route-by-model-translate-at-the-edge
---

# ADR 0029: The stdlib HTTP front is a canonical domain

Status: accepted 2026-10-07, implemented in `18f2b24` ("feat(fleet): the stdlib
HTTP front is a canonical source, _mcp_httpfront.py (R-0072)"), roadmap item
R-0072 closed in `d3fca9e`. The implementation ran overnight without the user;
every decision it took on its own is labelled "Agent decision (overnight, for
user review)" and is open to reversal. Every number below is a measurement or a
count at the moment of the decision, quoted from the red-first record kept
during implementation (a working file, not committed). The live counts are in
the `SUITES` table of `tests/run.py`.

## Context: two fronts, one copy table, no gate

`Scripts/mcp-proxy.py` serves Streamable HTTP for ai-soul, and
`Scripts/llm-router.py` serves the Anthropic Messages API for Claude Code. Both
are stdlib `ThreadingHTTPServer` listeners. The router's front was built as a
declared, adapted copy of the proxy's, and
[[0028-route-by-model-translate-at-the-edge]] recorded the copy row by row in
its table P5 to P17. Its first 2026-10-07 addendum ended on the sentence that
"the two copies are still hand-held and no gate holds them equal". Its R-0070
addendum named the two verbatim units still hand-held, `_http_token_value` and
`_HeaderDeadlineReader`, as the drift risk the lift (R-0072) would have to
reconcile. Three ports from the router to the proxy on the same day (R-0069,
R-0075, R-0076, recorded in [[0027-the-proxy-relays-it-never-composes]]) had
already narrowed the divergence by hand, which is exactly the work a generated
region does once.

## Decision

1. Agent decision D1 (overnight, for user review): a mechanism-only lift into
   one new canonical source, `Scripts/_mcp_httpfront.py`, whose domain is the
   stdlib HTTP/1.1 server front. Not a parameterised base class: an inherited
   method leaves `_ProxyHttpHandler`'s direct class body, and the proxy's static
   rules J25 and J26 walk only that body.
2. Agent decision D2 (overnight, for user review): the methods are generated
   into each host class through in-class markers, everything else through
   module-level regions, and every name keeps the proxy's spelling (ADR 0028:
   "a later lift is a move without renames"). Policy -- the logger, the error
   class, the timeouts, the minimum token length -- is passed as an argument or
   read through `self`; a block never reads a host global.
3. Agent decision D3 (overnight, for user review): `send_error` converges. The
   router's stdlib refusals (400, 414, 431, 501, 505) gain
   `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`, as the
   proxy's already carried.
4. Agent decision D4 (overnight, for user review): the gates that already held
   the copies -- router J1 and J13, proxy J25 and J26, `generated_region`,
   `amalgamate_check` -- stay green throughout; the source is registered in the
   suite's mirror; every changed or new test is observed red first.

The plan-level decisions under them:

- PD-1. `_HeaderDeadlineReader` takes its per-recv cap as a constructor
  argument, `cap`. Each host keeps the cap it had: the proxy its header bound,
  the router its socket timeout. Nothing is harmonised.
- PD-2. The generated `parse_request` reads `self.timeout`, the handler's class
  attribute, already equal to the header bound in both hosts. The router's
  in-process test rig patches that attribute in step with the module constant;
  a new attribute would not be patched, and B14 would silently run against a
  10 s bound.
- PD-3. Each server class carries `front_log = log`, and the generated server
  members log through `self.front_log`. The logger object, and with it the
  record name, is unchanged; the rig attaches its handler to that object and
  never rebinds the module's `log`.
- PD-4. `_http_remove_ready_file(path, logger)` takes the logger as a required
  argument, so both hosts log the same two DEBUG lines (KD-6, below).
- PD-5. `_HTTP_STDLIB_REFUSAL_HEADERS` is a canonical constant block, so
  generation holds D3's convergence equal rather than two hand copies agreeing.
- PD-6. `setup`, `handle_one_request` and `process_request_thread` are not
  canonical names. A canonical name a host keeps by hand is an UNDECLARED
  hand-copy row, and router J13 fails on such a row.
- PD-7. No policy may be a class attribute that snapshots a constant the
  router's rig patches at run time. A policy reaches a generated member through
  `self.timeout` (the one rig-patched attribute), through an argument evaluated
  at call time, or through a hand-written `_front_*` hook method that reads the
  host constant at call time. This lift needs no hook, because the only members
  that would (`setup`, `handle_one_request`) stay hand-written under PD-6.

## The domain and its boundary

The source answers one question: how a stdlib `http.server` listener takes a
connection and gets it through the header phase safely, plus the two
process-level pieces every such listener carries beside it, the ready file a
supervisor waits on and the bearer token value it compares against
`Scripts/_mcp_httpfront.py`. Its eleven blocks:

| Region | Blocks | Where it renders |
|---|---|---|
| refusal headers | `_HTTP_STDLIB_REFUSAL_HEADERS` | module level |
| token | `_http_token_value` | module level |
| ready file | `_http_write_ready_file`, `_http_remove_ready_file` | module level |
| header deadline | `_HeaderDeadlineReader` | module level |
| server members | `server_bind`, `process_request`, `handle_error` | inside the host's `ThreadingHTTPServer` subclass |
| handler members | `parse_request`, `handle_expect_100`, `_single_header` | inside the host's `BaseHTTPRequestHandler` subclass |

It passes [[0014-a-canonical-source-is-a-domain]]'s test on the only question
that test asks. `Scripts/_mcp_chrome.py` is the client side of the wire.
`Scripts/_mcp_oauth.py` opens one socket, a loopback redirect listener for one
protocol, and says nothing about connection admission. `Scripts/_mcp_logging.py`
decides how a log line is written, not when a server stops reading headers.
Filing the front in any of them would make that source a shelf.

No block names another block, so each region refreshes on its own and no
`*_CORE` tuple is needed. The six members are top-level `def`s in the source
and methods in the hosts: their zero-argument `super()` works only once the
generator renders them inside a class, so the source is a text source for them,
and group E drives them rendered inside a synthetic class. It is not the first
source rendered into a host class: `Scripts/_mcp_lsp.py`'s `_request`,
`_notify`, `_abs_uri` and `_abs_path` already take `self` at a 4-space marker
column (the correction on [[0012-the-transport-tier-diverged]]). It is the
first whose in-class blocks override stdlib members, call `super()`, and read
host policy through `self` seams one host's test rig patches at run time,
which is what PD-2, PD-3 and PD-7 are about.

What stays hand-written in each host is what differs: `_refuse` and the
`send_error` table (each host answers a refusal in its own envelope),
`_precheck`, request framing and the relay (each host's protocol),
`log_message` (each masks different client text), `setup` (it picks the
reader's cap, and the router starts every connection unauthenticated),
`handle_one_request` (the router's pre-auth bound), `process_request_thread`
(the router counts its drain around it) and both constructors.

## Where each policy reaches a generated block

| Policy | Generated reader | Seam | mcp-proxy | llm-router | Read at |
|---|---|---|---|---|---|
| logger of the server members | `process_request`, `handle_error` | `self.front_log`, a class attribute bound to the logger object | `front_log = log` | `front_log = log` | definition; allowed because the rig mutates that object and never rebinds `log` |
| connection cap in the 503 log line | `process_request` | `self.settings.max_connections` | set in `__init__` | set in `__init__` | call time |
| per-recv timeout after the header phase | `parse_request` | `self.timeout`, the stdlib's own attribute | header bound | header bound | call time; the rig patches it in step |
| per-recv cap of the reader | `_HeaderDeadlineReader.readinto` | constructor argument `cap`, passed by the hand-written `setup` | `_HTTP_HEADER_TIMEOUT_S` | `_HTTP_SOCKET_TIMEOUT_S` | per connection |
| pre-auth header bound | none: stays in the hand-written `handle_one_request` | the host global | none | `_HTTP_PREAUTH_TIMEOUT_S` | call time |
| token minimum length, error class | `_http_token_value` | arguments `min_len`, `error` | `_TOKEN_MIN_LEN`, `ConfigError` | the same | call time |
| ready-file error class, logger | `_http_write_ready_file`, `_http_remove_ready_file` | arguments `error`, `logger` | `ConfigError`, `log` | `ConfigError`, `log` | call time |

## What the lift changed

The lift ran twins first: both hosts were rewritten by hand into byte-identical
twins with their policy injected, gated at each step, and only then wrapped in
markers, so the generator's rewrite was a no-op on every body. The body
differences from the pre-lift code are therefore all in the twin steps:

- The injected seams of the table above: `cap`, `self.timeout`, `front_log`,
  `min_len` / `error`, `error` / `logger`.
- `process_request`'s 503 `sendall` joined onto one physical line. The two-line
  implicit join was tab-unsafe, and the canonical copy is born tab-safe
  (`tab-safety-real-blocks` still reports exactly `_rows_note`).
- Host-neutral comments: "the host never uses" instead of "the proxy" or "the
  router", one docstring for `_HeaderDeadlineReader`.
- The proxy's `McpServer._http_write_ready_file` and `_http_remove_ready_file`
  left the class and became the module-level generated pair; the router's
  `_rt_write_ready_file` and `_rt_remove_ready_file` were renamed to the same
  pair.
- `process_request_thread` moved below `handle_error`, and `_single_header`
  below `handle_expect_100`, in both hosts, because a region is one contiguous
  run of named blocks.

Two behaviour changes are intended and declared:

- KD-6. The router's ready-file removal, silent before, now logs the proxy's
  two DEBUG lines, "ready file left: not this pid" and "ready file left: %s"
  with an exception type name. Both are structure-only
  ([[0011-a-truncated-payload-carries-the-first-cookie]]).
- D3. The router's stdlib refusals carry nosniff and no-store through the
  generated `_HTTP_STDLIB_REFUSAL_HEADERS`. Router B21 asserts both and was
  observed red before the change.

## The adaptation table and its gates

Every stdlib member a host front class overrides by hand is a row of
`tests/test_generated_region.py:HTTPFRONT_ADAPTATIONS`, with its measured
reason. Twelve rows, six per host: each server's `__init__` and
`process_request_thread`, each handler's `setup`, `handle_one_request`,
`log_message` and `send_error`. Group I of `generated_region` reads it
`tests/test_generated_region.py:group_httpfront_pins`:

- GI1 `httpfront-adaptations-declared`. The override set is computed, never
  typed: the `FunctionDef`s in the class body that are attributes of
  `http.server.ThreadingHTTPServer` or `http.server.BaseHTTPRequestHandler` by
  `dir()`. Each must be generated or have a row. A row is stale unless it names
  a hand-written def outside every region in that class that is a stdlib
  override; a row naming a hand helper the stdlib base does not have counts as
  stale. No in-class `_mcp_httpfront.py` region may sit in another class. Each
  server's `process_request_thread` must call `self.conn_sem.release()` inside a
  `finally`, because the generated `process_request` acquires the slot.
- GI2 `httpfront-seams-provided`. The server class assigns `front_log`, its
  `__init__` assigns `self.settings` and `self.conn_sem`; the handler class
  assigns `timeout` and its `setup` assigns `self._header_reader`. Presence
  only; the values are J40's and K5's.
- GI3 `httpfront-pin-controls`. A synthetic host carrying the real rendered
  regions must be silent, and five plants must each be reported: (a) an
  undeclared `log_request` override, (b) a stale `handle_one_request` row, (c) a
  release outside any `finally`, (d) no `front_log`, (e) a row naming a
  non-override helper. Plant (e) was added after the completeness review (G1),
  when GI1's stale-row rule was tightened, and was observed red first.

The values of the seams are pinned per host:

- Router J40 "front policy bound" `tests/test_llm_router.py:rule_j40`: (a)
  `front_log = log` in `_RouterHttpServer`; (b) `timeout =
  _HTTP_HEADER_TIMEOUT_S` in `_RouterHandler`; (c) every `_HeaderDeadlineReader`
  call passes `_HTTP_SOCKET_TIMEOUT_S` as its cap; (d) no read of `log` in the
  three server members and none of `_HTTP_HEADER_TIMEOUT_S` in `parse_request`;
  (e) PD-7: the only class-level assignment reading an `_HTTP_` or `_TOKEN_`
  name is `timeout = _HTTP_HEADER_TIMEOUT_S`; (f) every `_http_token_value`
  call passes the names `_TOKEN_MIN_LEN` and `ConfigError` as its third and
  fourth arguments. J41 is its planted control, including a call with a literal
  `8`.
- Proxy K5 "front policy bound" `tests/test_mcp_proxy.py:k5_front_policy`:
  clauses (a) to (d) and (f) over `_ProxyHttpServer` / `_ProxyHttpHandler`,
  with the cap expected to be `_HTTP_HEADER_TIMEOUT_S`. Clause (b) uses its own
  `K5_TIMEOUT` constant (G3). K5 carries its own plant, `K5_PLANT`, with the
  same literal-`8` token call.
- Proxy J47 "weak token -> rc 2": a 31-character token and a 48-character
  token with a space are each refused at start with exit 2, one stderr line
  naming `--token-file`, the token never echoed, no child spawned. It is the
  behavioural backstop of clause (f) in the proxy, as router A13 is in the
  router.

## Risks and their gates

| # | Risk | Gate |
|---|---|---|
| R1 | A block reads a host global, a `NameError` at run time in-class | the generator's free-name audit refuses the region; GE `httpfront-in-class-blocks` execs the rendered members with no host name present |
| R2 | Zero-argument `super()` works only once rendered inside a class | GE `httpfront-in-class-blocks` calls `parse_request` / `process_request` through `super()` in a synthetic class; GA `httpfront-in-both-hosts` places each member region inside its `ClassDef` |
| R3 | Logger identity or record name changes | J40(a), K5(a) |
| R4 | A census FAIL, or a silent duplicate (a hand def beside a generated one in one class) | PD-6, router J13, and GA `httpfront-in-both-hosts`, which counts exactly one binding per name inside its region's span, with an in-case control |
| R5 | Tab safety of the 503 `sendall` | joined in the twin step; `tab-safety-real-blocks` unchanged |
| R6 | The router rig's seams (constructor signatures, class names, the `timeout` patch) | constructors untouched; PD-2; J40(b), K5(b) |
| R7 | The reader's cap semantics | PD-1; J40(c), K5(c), a J41 plant; GE `httpfront-header-deadline` checks `min(left, cap)` against a fake socket |
| R8 | Python 3.9 compatibility | `NEW_39_SCOPE` gained the source; `py_deps` 60 -> 61 |
| R9 | `STRICT_JSON_EXCEPTIONS` stale or undeclared when the proxy's ready file moved | re-keyed red first in Step 3, deleted red first in Step 4 |
| R10 | A name collides with another source | `sources-disjoint` |
| R11 | Router J1 loses `_HeaderDeadlineReader` or its use | the class stays a module-level `ClassDef`; the hand `setup` keeps its use |
| R12 | D3 is a behaviour change | B21 extended and observed red first |
| R13 | The router's ready-file removal starts logging | accepted and declared (KD-6) |
| R14 | J26's handler-log scan changes | the lifted handler members contain no log call |
| R15 | A stale `self._http_*_ready_file` call left behind | proxy J45 drives both calls; a search after Step 3 returned none |
| R16 | The generator rewrites a body differently from its twin | the Step 3 hosts staged as the baseline, then `git diff -U0` after the generator run (below) |
| R17 | Measured wiki regions drift | re-measured through the wiki; advisory, not a forge gate |
| R18 | An undeclared override, a stale row, a release outside a `finally` | GI1, GI2, GI3 |
| R19 | A future policy written as a class attribute snapshotting a rig-patched constant | J40(e) with a J41 plant |
| R20 | A caller weakens the token floor once it is an argument (A07, CWE-521) | J40(f), K5(f) with their literal-`8` plants; behaviourally router A13 and proxy J47 |

## Red first

The baseline before Step 1: `llm_router` 382, `mcp_proxy` 108,
`generated_region` 110, `py_deps` 60, `wire_log` 64, `amalgamate_check` silent.

- Step 1, D3. `llm_router` red, 382 cases, 380 pass, 1 fail, 1 info: B21
  reported `header X-Content-Type-Options None, expected 'nosniff'` and
  `header Cache-Control None, expected 'no-store'` for each of the three refused
  requests (an unknown method `BREW`, a request line over 64 KiB, the version
  `FOO/1.1`). Green once `send_error` passed the refusal headers.
- Step 2, the front twins. Router red, 384 cases, 382 pass, 1 fail, 1 info:
  J40 named the missing `front_log = log`, the reader's cap given as one
  argument, `process_request` and `handle_error` reading the host global `log`,
  and `parse_request` reading the host global `_HTTP_HEADER_TIMEOUT_S`. J41
  passed with its four plants flagged; clause (e) was silent on the live router.
  Proxy red, 109 cases, 108 pass, 1 fail: K5 named the same four defects in
  the proxy, and `K5_PLANT` was flagged four times. Green after the twins,
  `llm_router` 384 and `mcp_proxy` 109; the seven bodies were byte-identical
  modulo the class indent.
- Step 3, the token and the ready file. Router clause (f) red: "1334:
  _http_token_value called with 2 args, expected _TOKEN_MIN_LEN, ConfigError",
  the J41 literal-`8` plant flagged. Proxy clause (f) red, 110 cases, 109 pass,
  1 fail, on both callers. J47 was green on arrival, a characterisation; a
  scratch mutation passing `8` at the proxy's caller turned it red ("j47-short:
  exit code None, expected 2", no `mcp-proxy:` stderr line, a stub spawned)
  and made K5 flag the literal `8`; the mutation was reverted exactly. Group H
  red, 110 cases, 107 pass, 2 fail, 1 info: two bare json calls at the new
  module-level sites, and two `McpServer.*` rows with no site; re-keyed, green.
- Step 4, the lift. `generated_region` red, 118 cases, 107 pass, 10 fail,
  1 info, no traceback: `sources-registered` (12 against 13),
  `every-source-in-use` (registered but unrequested `_mcp_httpfront.py`),
  `census-sources-is-the-registry` (12 rows for 13 sources), the four GE cases
  (the source did not load, or the generator raised `KeyError`), GA (eleven
  names in each host in no region), GI1 (ten findings: the five hand overrides
  per host neither generated nor declared, no stale row) and GI3. GI2 was green
  on arrival; a scratch mutation commenting out `front_log` in
  `_ProxyHttpServer` turned it red ("_ProxyHttpServer assigns no class-level
  front_log"), and the revert left the diff against the index empty. After the
  markers, `strict-exceptions-not-stale` was red on the two re-keyed rows whose
  sites now sat in a region, and the rows were deleted.

The R16 proof. The Step 3 hosts were staged as the baseline; the generator then
updated six regions per host, and `git diff -U0` against that index showed 36
added lines and none removed, no body line among them: per host six Refresh,
six BEGIN and six END lines. The region hashes are identical in both hosts:

| Region | Hash |
|---|---|
| `_HTTP_STDLIB_REFUSAL_HEADERS` | `322ef814d1a3` |
| `_http_token_value` | `352c54a20b22` |
| `_http_write_ready_file, _http_remove_ready_file` | `1c2c5cd7b652` |
| `server_bind, process_request, handle_error` (in-class) | `4aa2226407c5` |
| `_HeaderDeadlineReader` | `b5cec0ec04cb` |
| `parse_request, handle_expect_100, _single_header` (in-class) | `cc86d6f0648d` |

The drill. One comment word changed in the source's `handle_error` ("quote" to
"echo") made `amalgamate_check` exit 1 with:

```
CHANGED: mcp-proxy.py [server_bind, process_request, handle_error] -- run: python3 Scripts/amalgamate.py
CHANGED: llm-router.py [server_bind, process_request, handle_error] -- run: python3 Scripts/amalgamate.py
```

Reverted, it was silent again.

The final state: `generated_region` 118, `llm_router` 384, `mcp_proxy` 110,
`py_deps` 61, `wire_log` 64, `handler_crash` 62, `mcp_oauth` 68, all green; the
hand-copy census lists ten declared copies and no `_mcp_httpfront.py` name.

Two checks were added beyond the plan during implementation: GE
`httpfront-header-deadline` also checks a cap of 30 with 5 s left (the
`settimeout` gets about 5), and GE `httpfront-in-class-blocks` drives the
admitted path of `process_request` (one acquire, no release, the stdlib called
once).

## Process decisions (overnight, for user review)

- Agent decision (overnight, for user review): the validation fan-out
  converged in round 1. The completeness review returned COMPLETE, 31 of 31;
  the security triage found no weakening, so no deep security review ran.
- Agent decision (overnight, for user review): the completeness review's three
  LOW findings (G1 the stale-row rule and plant (e), G2, G3 the `K5_TIMEOUT`
  constant) were fixed and verified by re-running `generated_region` and
  `mcp_proxy` only, not the full `forge test all`, because both changes are
  test-only.
- Agent decision (overnight, for user review): the code and the records are
  separate commits; this page and the addenda are not in `18f2b24`.

## Alternatives rejected

1. A parameterised base class (`_FrontServer`, `_FrontHandler`), imported or
   generated as a class. Inherited methods leave the host's class body, so
   proxy J25 / J26 and the hand-copy census's `bound_names` would stop seeing
   them, and the MRO under the router's rig would change.
2. Lift everything, `setup`, `handle_one_request` and `process_request_thread`
   included, behind `_front_*` hooks. These three differ today; lifting them
   needs three hooks and a renamed router state flag, which is policy in code
   and wider than "identical today". A canonical name kept by hand would also
   be an undeclared J13 row (PD-6).
3. A separate behaviour suite and forge target for the source. The existing
   suites already drive the behaviour end to end (proxy J45 and router B15/V24
   the ready file, router B14 and proxy J34 the deadline), and generation
   proves both hosts run the source text; the pure parts fit group E in memory.
4. One shared reader cap of 30 s. A no-op for the proxy today, but a semantic
   change nobody asked for (PD-1).
5. `parse_request` reading a new class attribute such as `header_timeout_s`.
   The rig would not patch it, and B14 would silently run at 10 s (PD-7).
6. `_http_remove_ready_file(path, logger=None)` with the router passing
   nothing. A second behaviour switch inside one canonical block; the copies
   converge on the more informative version instead (KD-6).
7. The router spelling its own refusal-header constant. It converges the
   behaviour but keeps two hand copies (PD-5).
8. Dropping the `Optional` annotation from `_HeaderDeadlineReader`. A body diff
   with no benefit; both hosts already import it.
9. Growing the source one block per step. It mixes policy injection with
   generation in every step; twins first, then one atomic generation step, lets
   R16 prove the move byte-identical.
10. Leaving the front hand-copied. ADR 0028's own addenda named it the drift
    risk.

## Declared limits

Accepted and declared, not gated beyond what is named.

- `setup`, `handle_one_request`, `process_request_thread`, `_refuse`, the
  `send_error` table and `log_message` stay hand-held in each host, pinned by
  GI1 as declared adaptations, not held equal by generation.
- The token policy is host-side. Both hosts pass `_TOKEN_MIN_LEN` and
  `ConfigError` to the generated `_http_token_value` (J40(f), K5(f)). The router
  keeps one more check by hand: at least `_TOKEN_MIN_DISTINCT` distinct
  characters `Scripts/llm-router.py:_TOKEN_MIN_DISTINCT` (8, V1), checked in
  `Scripts/llm-router.py:_rt_cfg_token`, which wraps the generated call. The
  proxy has no distinct-character floor, so a 32-character run of one letter is
  refused by the router and accepted by the proxy.
- The proxy has no pre-auth header bound (the router's V3): its
  `handle_one_request` applies one header bound to every request, the router a
  shorter one until the connection authenticates. Porting it is work, not part
  of a lift.
- The proxy's reader cap of 10 s is a provable no-op: its only deadline is the
  header bound, so the time left never exceeds the cap.
- The router's two new DEBUG lines (KD-6) have no router-side case. They are
  held equal to the proxy's by generation.
- The rule against snapshotting a rig-patched constant in a class attribute
  (PD-7, J40(e)) is gated only for the router, the one host with such a rig.
- Clause (f) of J40 and K5 is syntactic. It matches a `Call` whose function is
  the bare name `_http_token_value` and whose third and fourth arguments are the
  bare names `_TOKEN_MIN_LEN` and `ConfigError`. A call through an alias, a
  `functools.partial`, or a lowered `_TOKEN_MIN_LEN` (the constant itself
  redefined smaller) passes it. The behavioural backstop is proxy J47 and
  router A13, which refuse a short token end to end against the live constant.
- The census consequence. `server_bind`, `process_request`, `handle_error`,
  `parse_request`, `handle_expect_100` and `_single_header` are now fleet-wide
  canonical names: `Scripts/amalgamate.py:hand_copies_text` treats every block
  name of every source as known, and `Scripts/amalgamate.py:bound_names` walks
  every top-level class body. A future host class that overrides one of these
  stdlib members by hand becomes an UNDECLARED hand-copy row (INFO in
  `hand-copies-are-named`, FAIL under router J13) until it takes the region or
  is declared in `Scripts/amalgamate.py:HAND_COPY_REASONS`.
- A pre-existing observation, not changed by this lift. In the router, after
  `parse_request` a connection that has not authenticated runs at the 10 s
  per-recv header timeout (`self.timeout`), not the 5 s pre-auth one
  (`_HTTP_PREAUTH_TIMEOUT_S`, which only `handle_one_request` applies, for the
  header phase), although the `parse_request` comment -- identical in both
  hosts before the lift, generated now -- speaks of "the per-recv pre-auth
  timeout" staying until the host's precheck passes. The comment is host-neutral
  in what it names, the handler's class-level `timeout`, the header bound, which
  is what both hosts run; in the router the words "pre-auth timeout" read as the
  5 s bound, which it is not. The behaviour is the same before and after the
  lift. Whether the router should keep its pre-auth timeout after
  `parse_request` is staged as a roadmap candidate, not decided here.

R-0072 closed with rows P9, P10 and P12 to P14 of ADR 0028's copy table, and
the rest of P7 and P8, still declared adaptations. The three pieces of work the
limits above leave open -- the proxy's pre-auth bound, the proxy's
distinct-character token floor, and the router's post-`parse_request` per-recv
timeout -- are proposed as roadmap candidates for the user's approval
([[0022-a-someday-maybe-is-a-roadmap-item]]), not added.
