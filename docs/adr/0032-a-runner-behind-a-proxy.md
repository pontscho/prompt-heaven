---
name: 0032-a-runner-behind-a-proxy
type: adr
status: active
title: A runner behind a proxy, a skill server as its only client
description: Decision to run untrusted skill scripts through four layers that each own one concern -- sbx confines, a stdio runner (mcp-sandbox) owns policy, a dedicated mcp-proxy --http instance owns HTTP, bearer and sessions, and a read-only skill server (mcp-skill) owns tiers, file hygiene, sanitizing and framing -- with a hand-written Streamable HTTP client inside a server, the websearch sanitizer kept as a declared hand copy under its canonical names, self-refreshing interpreter plans, the same-uid trust model under which the runner token is the boundary, the KD-17 timeout chain and the 95 s startup bound against startup_timeout 120, the contract with ai-soul and the ai-soul-side bounds this design relies on without re-checking, the alternatives rejected, and the declared limits -- among them that a deliberately hostile script can build a directory tree whose removal spills over several runs.
sources:
  - Scripts/mcp-sandbox.py
  - Scripts/mcp-skill.py:RunnerClient
  - Scripts/mcp-skill.py:_web_clean
  - Scripts/mcp-proxy.py:refresh_tools
  - Scripts/amalgamate.py:HAND_COPY_REASONS
  - tests/test_mcp_sandbox.py
  - tests/test_mcp_skill.py
verified:
  commit: c643221
  date: 2026-10-12
links:
  - 0031-sbx-restricted-mode
  - 0027-the-proxy-relays-it-never-composes
  - 0029-the-http-front-is-a-domain
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0014-a-canonical-source-is-a-domain
  - 0025-generate-do-not-import
  - 0010-a-handler-failure-must-reach-iserror
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0024-pure-python-39-and-the-stdlib
  - mcp-sandbox
  - mcp-skill
  - mcp-proxy
  - generated-regions
  - scripts
  - tests
---

# ADR 0032: A runner behind a proxy, a skill server as its only client

Status: accepted 2026-10-11 as the design the code in the working tree
implements, on top of `4d3640c`, and not yet committed when this page was
written. That is why the frontmatter says `draft` and carries `targets:`
instead of `sources:` and `verified:`, as [[0031-sbx-restricted-mode]] does: a
`verified.commit` cannot vouch for code no commit holds yet. Once the code
lands, the page is promoted to `active` with that commit and is append-only
from then on. Every number below is a constant read from the code, a
measurement, or a count at the moment of the decision; measurements were taken
on macOS 14.2.1 (Darwin 23.2.0, x86_64). The `sbx` restricted mode this design
stands on is ADR 0031's subject; the shape of each server is on
[[mcp-sandbox]] and [[mcp-skill]].

## Context: a household wants to run the scripts its skills carry

ai-soul lets household members approve **skills**: a short SKILL.md, reference
files and scripts. An agent loads them and, sometimes, runs one of the scripts.
The script was written by a skill author, approved by a person, and stored by
ai-soul; it was not chosen by the agent and it was not reviewed line by line.
Running it is therefore running untrusted code, and every concern that comes
with that has to land somewhere: confinement, limits and cleanup, an
authenticated transport between processes, and the text that comes back and is
read by a model.

The question was how many processes, which of them speak HTTP, which of them
read skill files, which of them spawn the script, and where the untrusted text
is cleaned. Two constraints were fixed by the user up front (decisions 1, 2 and
7 of the plan): the runner speaks stdio and contains no HTTP code, the skill
server is the runner's only client, and the runner returns output raw while the
skill server sanitizes it.

## Decision

### Four layers, each owning one concern

```
ai-soul daemon (HTTP, bearer per uid)
   -> mcp-proxy --http (per uid)                         relist on the skill child
        -> mcp-skill.py (stdio child)   --runner-url http://127.0.0.1:P/mcp --runner-token-file F
              -> Streamable HTTP POST, SSE answer with keepalive comments
                 -> mcp-proxy --http --port P --token-file F --require-token-file --max-body-bytes 8388608 --session-idle 900
                      -> mcp-sandbox.py (stdio child; one process, so one global concurrency cap)
                           -> sbx --restrict-reads --clean-env --timeout --status-fd --rlimit-* -- <interp> <entry>
```

1. **`sbx` confines.** Restricted reads, a filtered exec, a clean environment,
   a supervised timeout with a status protocol, strict rlimits
   ([[0031-sbx-restricted-mode]]). Each primitive is a flag any `sbx` caller can
   use and the gate of ADR 0005 can see.
2. **`mcp-sandbox` owns policy.** One strict JSON config file, the per-run
   limits, the workdir lifecycle, the capped output read, the macOS read set
   computed by a stdlib Mach-O reader, the RLIMIT_AS baselines, the plan
   refresh and the global concurrency cap `Scripts/mcp-sandbox.py:RunSlots`.
   It has no HTTP code; one tool, `sandbox_call`, with `run_script` and
   `capabilities` `Scripts/mcp-sandbox.py:handle_sandbox_call`.
3. **A dedicated `mcp-proxy --http` instance owns HTTP.** Bearer, Origin and
   Host checks, sessions, the body cap and the framing ceiling stay in the one
   audited front ([[0027-the-proxy-relays-it-never-composes]],
   [[0029-the-http-front-is-a-domain]]). Its body cap is the existing
   `--max-body-bytes`, its idle eviction the existing `--session-idle`
   `Scripts/mcp-proxy.py:_HTTP_SESSION_IDLE_S`. One flag was added for it in
   security review round 1 (T2): `--require-token-file` makes the proxy refuse
   to start when `MCP_PROXY_TOKEN` is set, even beside `--token-file`, so the
   runner token can only come from its file
   `Scripts/mcp-proxy.py:load_http_settings` (the ADR 0027 addendum of the same
   day). F is the runner token file; where it should live is the T3 limit
   below.
4. **`mcp-skill` owns the untrusted-text edge.** Tiers and their index, file
   hygiene, the rendered tool description, the sanitizer, the nonce framing and
   the runner client `Scripts/mcp-skill.py:handle_skill_call`. It is read-only:
   it never writes to a skill root.

Data flows down as JSON (files, args, stdin) and back up raw from the runner;
only `mcp-skill` sanitizes, because it is the edge the model reads. The runner
proxy passes its own environment to the runner minus the token variable
`Scripts/mcp-proxy.py:build_child_env`, so the runner never hands its
environment on: it spawns `sbx` with a fixed two-key environment and `sbx` sets
the target's exactly.

The cost is two HTTP hops per run, negligible against a 30 s default budget, and
a second proxy instance to deploy.

### The skill side

The skill proxy carries `"relist": true` on the `mcp-skill` child, so a
`tools/list` from upstream re-lists that child, coalesced and rate-bounded,
with its tool NAME SET frozen `Scripts/mcp-proxy.py:refresh_tools` (the ADR
0027 addendum of 2026-10-11). That is how the index, rendered into
`skill_call`'s description, stays fresh without a push channel.

### An MCP HTTP client inside a server

`mcp-skill` contains a hand-written, bounded Streamable HTTP MCP client
`Scripts/mcp-skill.py:RunnerClient`. That is a deviation from the fleet's
shape, where servers speak stdio and only the proxy and llm-router speak HTTP,
and it is declared here rather than hidden. Its bounds:

- **Loopback literals only** (R2:S-L3). `--runner-url` must be `http` with the
  host exactly `127.0.0.1` or `[::1]`, never `localhost` or any other name,
  whose answer a resolver controls; an explicit port and a path; no userinfo,
  query or fragment. Anything else is exit 2 at startup
  `Scripts/mcp-skill.py:_check_runner_url`.
- **The token file is re-read on every run** `Scripts/mcp-skill.py:_read_token_file`,
  so rotation needs no restart of `mcp-skill`. The runner proxy reads its own
  token file once at start `Scripts/mcp-proxy.py:_http_token_file`: rotation is
  rewrite the file and restart that proxy.
- **Bounded reads.** A redirect is refused; an answer is capped at
  `Scripts/mcp-skill.py:_RUNNER_MAX_RESPONSE`, the runner's own worst-case reply
  bound (16 MiB, a hand copy of `mcp-sandbox`'s `_RESPONSE_BOUND` pinned equal
  by `mcp_skill:I12`) plus a 64 KiB framing allowance for the JSON-RPC envelope
  and the SSE lines (it was 32 MiB until security review round 1, M2); every body and every SSE
  `data:` payload goes through `_strict_loads`; each read is bounded by
  `Scripts/mcp-skill.py:_RUNNER_SOCKET_TIMEOUT_S` (30 s, above the proxy's 15 s
  keepalive) and the time left of `--runner-timeout-s`
  `Scripts/mcp-skill.py:_read_sse`.
- **Sessions** (C-M2). One retry with a fresh session only on HTTP 404
  `Scripts/mcp-skill.py:call`. On stdin EOF or SIGTERM the held session gets one
  best-effort `DELETE` `Scripts/mcp-skill.py:close`
  `Scripts/mcp-skill.py:_shutdown`; a server that dies without it leaves its
  session to the runner proxy's `--session-idle 900`, instead of the default
  86400 s, so skill-server restarts cannot exhaust `--max-sessions`
  (`mcp_skill:I11`).
- **One serialization, sized before sending** (C-M2b). Each bundle file goes
  as `utf8` or `base64`, whichever serializes smaller
  `Scripts/mcp-skill.py:_encode_file`; the whole request body is serialized once
  `Scripts/mcp-skill.py:_runner_body`, refused `invalid_params` when it exceeds
  `--runner-max-body` (default 8388608, the runner proxy's `--max-body-bytes`)
  before any connection is opened, and those same bytes are what is sent
  `Scripts/mcp-skill.py:prepare` (`mcp_skill:J9`, `mcp_skill:J10`).

It is not a canonical source. Generation buys drift protection between two
hosts; this client has one production host, and the code it grew from is test
code, not a generation host. It is lifted when a second host appears (roadmap).

### The sanitizer is a declared hand copy

`mcp-skill` carries `_WEB_LINE_SEPARATORS` and `_web_clean` copied byte for
byte from `Scripts/_mcp_websearch.py:_web_clean`, under their canonical names,
outside every generated region `Scripts/mcp-skill.py:_web_clean`, and declared
by two rows of `Scripts/amalgamate.py:HAND_COPY_REASONS` with the reason
"`_mcp_websearch.py` is a WHOLE source (G-c); mcp-skill needs its sanitizer and
none of the search". The evidence that decided it:

- `_mcp_websearch.py` is in `Scripts/amalgamate.py:WHOLE_SOURCES`: a host
  carrying some of its blocks and not others is a violation, never a choice.
- `tests/test_generated_region.py:whole_source_problems` and its FAIL row
  `whole-source-regions` enforce that on every `mcp-*.py` and declared host.
- Both existing hosts take the whole source, so there is no partial-region
  precedent to follow.

A two-name generated region would therefore fail the generated-region suite,
and taking the whole source would drag the search engine, its parsers and its
network code into a read-only skill server. Drift is pinned by two parity rows
`tests/test_mcp_skill.py:_run_sanitizer`: `H1` compares the copy's source text
with the canonical blocks as `Scripts/amalgamate.py:load_blocks` reads them,
`H2` runs both on the search suite's corpus with `keep` in `("", "\t\n")`. A fix
is made in the canonical source and re-copied. The census on
[[generated-regions]] lists both names as DECLARED.

### Interpreter plans refresh themselves (KD-13)

No manual step after `brew upgrade`. Each interpreter plan carries a signature,
`(path, st_ino, st_mtime_ns)` of every symlink hop, the realpath, every closure
image, every keg root, the framework launcher and every `extra_reads` entry
`Scripts/mcp-sandbox.py:_chain_signature` `Scripts/mcp-sandbox.py:plan_interpreter`.
Each run re-walks it; on a change the plan (closure, VSZ, `--version`,
self-test) is recomputed under a per-interpreter lock
`Scripts/mcp-sandbox.py:PlanCache`. A failed refresh makes that interpreter
refuse runs with `sandbox_unavailable` and `capabilities` reports the state; the
next run retries. A cancel during a refresh is not a failure: the prior state
stays, no failure timestamp is recorded and the 5 s suppression does not start
(`mcp_sandbox:G13`). The slot is taken before the signature check, so a refresh
counts against the concurrency cap (`mcp_sandbox:G12`).

**One plan term per run (R4:M2).** The run fixes one `plan_deadline` at its
plan-phase start and that one deadline covers a wait on another run's refresh
and any refresh this run then performs itself; it is never re-armed. Without it
a waiter whose refresh owner was cancelled could spend a second 30 s term, a
204.5 s bound above the runner proxy's `call_timeout` of 190. Measured: `G13`'s
waiter answered "refresh deadline" at 30.01 s against its 32 s bound
(IMPL:063).

### The timeout chain (KD-17) and the startup bound

The terms are module constants of `mcp-sandbox`, one block, each enforced
rather than a target `Scripts/mcp-sandbox.py:_RUN_FIXED_TERMS_S`:

| term | constant | seconds |
|---|---|---|
| degraded pre-run quarantine retry, only while degraded | `_DEGRADED_RETRY_S` | 2 |
| plan refresh or refresh wait, one per run | `_REFRESH_DEADLINE_S` | 30 |
| the script's own timeout, at most `max_timeout_ms` | config, refused above 120000 | 120 |
| hard-deadline slack over sbx's own timeout | `_HARD_DEADLINE_SLACK_S` | 10 |
| SIGTERM to SIGKILL, the one grace | `_KILL_GRACE_S` | 2 |
| pipe drain after sbx exits | `_DRAIN_S` | 0.5 |
| cleanup, incl. the opportunistic quarantine retry | `_CLEANUP_DEADLINE_S` | 10 |

"Enforced" had one hole until security review round 1 (C1): the plan term did
not reach the stdlib Mach-O closure walk, whose rpath stack and node_modules
walk were also unbounded. The walk now checks the request's cancel and the
plan deadline per image and per directory
`Scripts/mcp-sandbox.py:dylib_closure`, and its stack, directory count, depth
and per-directory entries are capped, each cap a refusal rather than a
truncation ([[mcp-sandbox]]; `mcp_sandbox:B21`-`B23`).

The sum is 174.5 s, under `Scripts/mcp-sandbox.py:_RUN_DEADLINE_S` (175). The
chain above it is strictly increasing: 175 < the runner proxy's `call_timeout`
190 < `mcp-skill`'s `--runner-timeout-s` 200 (its default, range 20 to 900) <
the skill proxy's `call_timeout` 220. The 190 and 220 live in deployment
configs, not in this repo's code; the plan's example configs carry them.

**Startup (R3:M4).** The runner must answer `initialize` within the runner
proxy's `startup_timeout` of 120 s, or the proxy restarts it and its restart
budget eventually disables the child: a permanent outage. Startup is the
work-root sweep under one shared
`Scripts/mcp-sandbox.py:_STARTUP_SWEEP_DEADLINE_S` (20 s) plus every
interpreter plan under one shared
`Scripts/mcp-sandbox.py:_STARTUP_PLANS_DEADLINE_S` (75 s) = 95 s, a margin of
about 25 s under 120; an exhausted plans deadline refuses start, exit 1.
Measured (task-066, time to the `initialize` reply, n=5): one interpreter
(`/bin/bash`, the smoke config) median 0.938 s (0.930-0.944); three
interpreters (Homebrew python3, `/bin/bash`, node with its openssl.cnf read)
median 2.699 s (2.679-2.732). Lowering `startup_timeout` below roughly 100 s
re-opens the crash loop.

### The rest of the shape

- **Refusals are tool results** with `isError` and a symbolic `error.code` --
  `busy` (with `retryAfterMs`), `sandbox_unavailable`, `invalid_params`,
  `internal` (exception type only) -- never JSON-RPC errors, in both servers
  ([[0010-a-handler-failure-must-reach-iserror]]). A non-object `params` on
  `tools/call` stays a protocol-level `-32602`. A failing script is a normal
  result.
- **An omitted or empty `function` is an unflagged status reply (R4:M1)** in
  both dispatchers `Scripts/mcp-sandbox.py:_status_text`
  `Scripts/mcp-skill.py:_status_text`: the function list and counts, never a
  path, a token or skill text.
- **`params` may arrive as a JSON-encoded object string (R3:M2)**, decoded by
  the generated `_ensure_dict` `Scripts/mcp-sandbox.py:_ensure_dict` like the
  rest of the fleet; unknown functions and members get the did-you-mean pointer
  that never resolves ([[0030-point-at-the-near-miss-never-resolve-it]]).
- **The work root has one owner (C-M1).** An exclusive `flock` on
  `<work_root>/.lock` is taken at startup and held for the process lifetime; a
  held lock refuses start `Scripts/mcp-sandbox.py:prepare_work_root`
  (`mcp_sandbox:G11`).
- **Quarantine instead of a global outage** (approved 2026-10-11). A workdir
  the remover cannot delete inside its bounds is moved to
  `<work_root>/quarantine/` and the runner keeps serving
  `Scripts/mcp-sandbox.py:Quarantine`; it goes `degraded` only above
  `Scripts/mcp-sandbox.py:_QUARANTINE_MAX` (16) entries. Every retry is bounded
  by one shared deadline. The remover itself is ADR 0031's.
- **Linux is built and verified offline.** `capabilities` reports
  `liveVerified` per platform `Scripts/mcp-sandbox.py:LIVE_VERIFIED`, `false`
  on Linux until the branch runs on its target.
- **Concurrency** follows [[0008-a-serialized-read-loop-looks-like-a-dead-server]]:
  both servers are in the pool group (its two 2026-10-11 addenda).
  `mcp-sandbox`'s cancel class is kill; `mcp-skill`'s is reply-only.
- **Logging is metadata only**, and wire debug logging structure-only
  ([[0011-a-truncated-payload-carries-the-first-cookie]]). `mcp-skill` logs
  function, skill, tier, interpreter, exit and duration -- never the token, a
  body, args, stdin or output.

## Same-uid trust

ai-soul, both proxies, the runner and the skill server run as one OS uid
(decision 4). Isolation between household members is by configuration: one
skill proxy per ai-soul uid, each with that member's `--private-root`, the
household tier shared read-only. The household threat model is **accidents**,
not a hostile member. In scope: arbitrary script content, args, stdin and file
names; arbitrary text in a tier, including prompt injection; a local network
peer reaching a loopback port without the token. Out of scope: a hostile
process already running as the same uid.

Two consequences are declared rather than gated:

- **The runner token IS the boundary (S-INFO).** Anyone who can read the runner
  token file can call `run_script` directly and so bypasses `mcp-skill`'s
  `--allow-private-scripts`. `mcp-skill` is a policy layer for its own callers,
  not a gate in front of the runner.
- **`capabilities` exposes the computed read set (R2:S-INFO).** It reports the
  absolute paths of interpreter kegs, framework images and `extra_reads`
  `Scripts/mcp-sandbox.py:capabilities` to its caller. The caller is the
  trusted runner-proxy client: `mcp-skill`, or a same-uid holder of the token,
  which is already the boundary. `mcp-skill` never forwards `capabilities` to
  the model.

ADR 0031's E28 adds a third: a restricted run can read the argv and environment
of any same-uid process. So the runner token belongs in its 0600 file and in no
process environment; the E28 probe read `MCP_PROXY_TOKEN` out of the runner's
environment. Since security review round 1 that is enforced rather than
advised: the runner proxy is launched with `--require-token-file`, which
refuses to start while `MCP_PROXY_TOKEN` is set (T2). Two more consequences
of the token being the boundary, T1 and T3, were found by the same review and
are declared below by decision.

## The contract with ai-soul

These are the items ai-soul was told; item 4 was confirmed by ai-soul on
2026-10-11 (M0:12).

1. **Refusals are `isError` tool results with `error.code`**, never JSON-RPC
   errors; the four codes above; ai-soul maps `busy` to 503.
2. **`run` lists files only inside one skill's `scripts/` and `refs/`**, never
   the tier root, which is enumerated only through `skills.json`.
3. **The runner proxy reads its token file at start**; `mcp-skill` re-reads it
   on every run. A per-request re-read in the proxy is a roadmap item.
4. **The leaf-name grammar** for `scripts/` and `refs/` files is
   `^[a-z0-9][a-z0-9._-]{0,63}$`, no nesting, and a run carries at most 64
   files and 4 MiB decoded `Scripts/mcp-skill.py:_BUNDLE_MAX_FILES`
   `Scripts/mcp-skill.py:_SCRIPT_MAX_BYTES` `Scripts/mcp-skill.py:_collect_bundle`.
   ai-soul's own bounds are narrower (below) and a run bundles exactly one
   skill, so 64 / 4 MiB is a runner-side backstop, never the binding limit for
   an approved skill.
5. **Result field shapes on the runner**: `signal` is a signal name or `null`;
   on a timeout `exitCode` is `null` and `timedOut` is `true`. An engine-stage
   failure after `spawned` is a normal result (ADR 0031, KD-16).
6. **`mcp-skill`'s `run` answer is sanitized, nonce-framed text**, not the
   runner's raw object `Scripts/mcp-skill.py:_sanitize_run_result`; the raw
   object is visible only on the runner's own endpoint. Since security review
   round 1 the whole answer is held to 100000 characters (ADR 0013's verbatim
   class) with a cut named after the frame it cut, and a runner refusal reads
   `the runner refused the run (<code>).` with the runner's message only inside
   a `kind=runner-error` frame; the shape is on [[mcp-skill]].
7. **Group- or world-writable skill files and their `scripts/` / `refs/`
   directories are refused** as `writable-by-others`.
8. **No `notifications/tools/list_changed`.** A client sees a new or changed
   skill by calling `tools/list` again, which triggers the relist.

The description unit is settled too: both ai-soul's per-tier approve budget
(1800) and `mcp-skill`'s ceiling (`--max-description`, default and maximum
4096) are UTF-16 code units, matching ai-soul's JavaScript `.length` cut.

### What ai-soul guarantees, and this design relies on without re-checking

ai-soul published these bounds of its own. They are ai-soul-side guarantees,
taken from the ai-soul peer and not read in this repo; on their side they are
Increment A of their security round S28, uncommitted there when they reported it
(2026-10-11):

- a skill file name matches `SKILL_FILE_NAME_RE`,
  `^(refs|scripts)/[a-z0-9][a-z0-9._-]{0,63}$` -- the same leaf grammar as item
  4, and the same pattern `mcp-skill` uses for `load`'s `file`
  `Scripts/mcp-skill.py:_LOAD_FILE_RE`;
- at most 16 files (`maxFiles`) and 262144 bytes (`maxTotalBytes`) per skill
  version;
- a SKILL.md body of at most 32768 bytes, and a body that starts with `---` is
  refused;
- base64 content only under `refs/`;
- a file's content capped at `SKILL_CONTENT_UNITS_CEILING`, 349528 characters
  (which is the base64 length of 262144 bytes);
- the publisher writes files with `O_EXCL|O_NOFOLLOW`;
- approve-time text cleanliness widened: every skill text is refused if it
  holds a code point in `Cc`, `Cf`, `Cs`, `Co`, `Default_Ignorable_Code_Point`
  or `Noncharacter_Code_Point` (`SKILL_UNSAFE_CATEGORY_RE` in their
  `src/types/skills.ts`), on top of their `UNSAFE_TEXT_RANGES`, with tab and
  newline allowed only in body and files.

**Approve-time is a superset of load-time cleanliness (R4:CONTRACT).**
`_web_clean` drops `Cc`, `Cf` and `Cs`, the variation selectors U+FE00-U+FE0F
and the block U+E0000-U+E01EF, and rewrites U+2028, U+2029 and U+0085 to a
newline `Scripts/mcp-skill.py:_web_clean`. ai-soul's widened set covers every
dropped class, so an approved text is already clean when loaded, and
`mcp-skill`'s `description == _web_clean(description, "")` check
`Scripts/mcp-skill.py:_description_clean` should never fire as
`description-not-clean` on an approved skill. Our copy is the byte-for-byte
canonical hand copy with its two parity rows, and their set is the larger one,
so **nothing changes on our side**.

## Alternatives rejected

1. **A sanitizer generated as a two-name region (S2).** *Rejected:* its premise,
   that hosts already take partial name lists from a source, is false for a
   WHOLE source; the region fails `whole-source-regions`.
2. **A renamed hand copy (`_SKILL_LINE_SEPARATORS`, `_skill_clean`).**
   *Rejected:* the hand-copy census walks canonical block names, so a renamed
   copy is invisible to it and the third copy would never reach the census
   page. Keeping the canonical names makes it a declared row
   ([[0025-generate-do-not-import]]).
3. **A new canonical source for the sanitizer.** *Rejected for now:* it would
   have exactly one host. The two existing copies cannot migrate, because their
   sibling blocks call the sanitizer from inside WHOLE sources and a region may
   not resolve names from another source ([[0014-a-canonical-source-is-a-domain]]).
   Kept as a roadmap item.
4. **Lift the Streamable HTTP client into a canonical source now.** *Rejected:*
   one production host; generation protects against drift between two.
5. **A runner with its own HTTP front.** *Rejected:* it duplicates the hardened
   front ADR 0029 lifted, and decision 1 forbids HTTP code in the runner.
6. **The skill server calling `sbx` directly, no runner.** *Rejected:* the
   concurrency cap must be global across every per-uid skill proxy, the
   Mach-O and VSZ plans would be computed once per skill server, and one
   long-lived process would both read untrusted skill files and spawn them.
7. **JSON-RPC errors for `busy` and `sandbox_unavailable`, as the external spec
   suggested.** *Rejected:* ADR 0010's fleet invariant and the smoke harness; a
   symbolic `error.code` in an `isError` result carries the same distinction.
8. **Relist by polling, or by honouring `notifications/tools/list_changed`.**
   *Rejected:* the proxy has no upstream push channel and drops `list_changed`;
   polling creates load nobody reads. Pull on `tools/list` is the one moment a
   fresh description is consumed.
9. **A background reaper for quarantined workdirs.** *Rejected:* see the
   hostile-depth limit below; the only trigger is deliberate hostility of an
   approved skill.

## The gates and their first runs

Two new suites carry this design, each typed in `tests/run.py`: `mcp_sandbox`
141 (A 10, B 20, C 22, D 4, E 34, F 8, G 13, H 19, I 11) and `mcp_skill` 75
(A 5, B 8, C 6, D 8, E 3, F 14, G 4, H 6, I 11, J 10), both as planned.
Validation round 1 then added live layer-D evidence, because the smoke probe
answers -32602 now and no longer reaches the catch-all: `mcp_sandbox` `F9` and
`mcp_skill` group K (`K1` in-process, `K2` through `McpServer.run()`), each
answering `internal` with `isError` and still serving the next request. That
makes 142 and 77. The relist work added `mcp_proxy` group M, 112 to 123.
Security review round 1 added, each row run red first: `mcp_sandbox` `A11`,
`B21`-`B23` and `H20`-`H24`, so 151 (ADR 0031 names them); `mcp_skill` `B9` and
`B10` (the script entry cap and the fstat-only check), `I12` (the response cap),
`J11` (the bounded bundle listing), `J12` (the answer ceiling) and `J13` (the
framed runner message), so 83; and `mcp_proxy` `J50`
(`--require-token-file`), so 124. The client rows run against
a real `mcp-proxy --http` in front of a stub runner child
`tests/test_mcp_skill.py:_run_client`; `J1` runs end to end through the real
sandbox `tests/test_mcp_skill.py:_run_run`, INFO off macOS and under a nested
Seatbelt, counted either way.

Every `mcp_sandbox` group whose code did not exist yet was written and run red
first, and which rows were recorded red, and where each turned green, is in the
ledger comment of `tests/run.py`; group E, the live escape suite, was written
after the code it probes and pairs each attempt with a positive control instead
(ADR 0031). The `mcp_skill` ledger records the final table, not a red count.
Per-group red counts were not written down as numbers for either suite.

## Declared limits

Accepted and declared, not gated beyond what is named.

- **Hostile depth (M0:14, R-13; deferred here by ADR 0031).** The 100-level
  depth cap does not bound what rename-chaining builds. A deliberately hostile
  script can build roughly 1 000 000 directory levels in a 120 s run, at about
  11 ms per 99-level round sandboxed. That is a linear extrapolation,
  unmeasured past 4059 levels. Removal is linear at about 0.14 ms per level
  (M0 item 4b), so such a tree needs on the order of 140 s of removal, far more
  than one 10 s cleanup term. Cleanup therefore spills into quarantine and is
  finished over several later retries -- the 20 s startup sweep, the 2 s
  degraded pre-run retry, the post-run retry -- each making monotone progress,
  because lifted segments persist under their top-level names
  `Scripts/mcp-sandbox.py:remove_tree`. The 16-entry quarantine ceiling makes
  repeated abuse visible as `degraded`. **No background reaper**: decided,
  because the only trigger is deliberate hostility of an approved skill.
  Untested: a cut through a `uchg` directory (the remover clears flags with
  `lchflags(0)` before the cut rename), a cut through an ACL'd directory, and
  trees with non-default modes under flatten. Linux has no depth cap and its
  mutation cost by depth is unmeasured.
- **The Unicode version skew (R4:CONTRACT residual).** ai-soul classifies with
  Node's ICU, `mcp-skill` with Python's `unicodedata`. When the two sit on
  different Unicode versions, a newly assigned code point can fall in a refused
  category on one side and stay unassigned (`Cn`, kept) on the other. If
  `mcp-skill` reports `description-not-clean` on an approved skill, check that
  skew first.
- **Two of the rewritten separators rest on ai-soul's ranges.** U+2028 and
  U+2029 are `Zl` and `Zp`, in none of the six classes ai-soul names, and
  `_web_clean` rewrites them, so a description holding one is not clean. The
  superset holds for them only through ai-soul's `UNSAFE_TEXT_RANGES`. The
  ai-soul peer confirmed it on 2026-10-11 (not read in this repo): the range
  `[0x2028, 0x202E]` at their `src/invariants/evidence.ts:66-68` covers both
  and the bidi controls, `src/types/skills.ts:126` applies it to the name,
  description, body and text files, and the description must additionally be
  one line with no `\n`, `\r`, C1 (U+0085 included) or U+2028-U+202E. The
  limit left is the usual one: a later change on their side would not show
  here.
- **The ai-soul-side bounds are trusted, not re-checked.** `mcp-skill` enforces
  its own grammar and its own caps (64 files, 4 MiB, a 256 KiB load cap per
  file); ai-soul's 16 files, 262144 bytes, 32768-byte body and base64-only-
  under-`refs/` are not verified here.
- **The runner token is the boundary (S-INFO)**, and **`capabilities` shows the
  read set to its caller (R2:S-INFO)**; see Same-uid trust.
- **The token goes to whatever listens on the runner port (T1; Zoltán's
  decision, 2026-10-11).** `mcp-skill` checks only that `--runner-url` is a
  loopback literal `Scripts/mcp-skill.py:_check_runner_url`; it sends its
  `Authorization: Bearer` header to whichever process answers on that port. While
  the runner proxy is down, another LOCAL uid can bind the port first and
  harvest the token from the next run: a cross-uid port squat, outside the
  same-uid model above because the squatter is a different uid. Not fixed, by
  decision. The options weighed were a unix socket in a 0700 directory owned by
  the euid (a protocol change on both ends, so a design decision), a check of
  the listener's uid before each call, and a challenge-response. The unix-socket
  transport is a roadmap item.
- **`sbx`'s deny set is fixed, so the runner token file belongs under
  `~/.claude` (T3).** The secret paths `sbx` denies are a fixed list
  `ClaudeCode/skills/sandbox-run/scripts/sbx:secret_paths`, with no
  operator-supplied extension. A token file elsewhere, for example next to the
  proxy config, is readable by any LEGACY `sbx` run, and the gate auto-allows
  `sbx -- cat <that file>` without a prompt. The recommended location is
  therefore under `~/.claude`, which `sbx` denies for read and write outside
  its two read-only carve-outs (`skills/`, `scripts/`), for example
  `~/.claude/mcp-sandbox/runner.token`, mode 0600. Neither `mcp-skill` nor
  `mcp-proxy` checks where the file is; the location is a deployment rule,
  stated in the launch docs ([[mcp-sandbox]], [[mcp-skill]], [[mcp-proxy]]).
- **Semantic injection is not prevented.** Framing marks skill text as data and
  the sanitizer removes invisible and display-steering characters; neither
  stops an approved skill's plain words from misleading the model.
- **A cancel does not stop a run through `mcp-skill`.** Its cancel class is
  reply-only: the runner HTTP call runs to its own deadline in the worker
  thread. Hanging up on the runner when the request is cancelled is a roadmap
  item; the runner proxy already turns a dropped connection into a child
  cancel.
- **No HTTPS to the runner.** Loopback literals only, by design.
- **Token rotation on the runner proxy needs a restart.** It reads its token
  file once at start.
- **Content logging is off.** The runner logs one metadata line per run, and
  content only when the config's `log_content` is true; a debug
  content-logging switch beyond that is a roadmap item.
- **Linux is `liveVerified: false`** until the branch runs on its target, which
  this work never touched.
- **No dedicated sandbox uid.** A fork bomb can starve the user's other
  processes up to the wall clock (ADR 0031); a dedicated uid is a roadmap item.

## Deferred to the roadmap

- A canonical home for the sanitizer, replacing the hand copy and its parity
  rows.
- A canonical Streamable HTTP MCP client, lifted when a second host appears.
- `mcp-skill` hanging up on the runner on cancel.
- A debug content-logging switch beyond `log_content`.
- A per-request token re-read in `mcp-proxy` (an ADR 0027 item).
- A dedicated sandbox OS uid for the runner (shared with ADR 0031).
- A unix-socket transport between `mcp-skill` and the runner proxy, in a 0700
  directory owned by the euid, which closes the cross-uid port squat (T1).

## Addendum (2026-10-11): the code landed in 7b603d2; the page is active and append-only

The Status paragraph above was written while the code sat uncommitted on top of `4d3640c` and says the frontmatter is `draft` with `targets:`. That is no longer true. mcp-sandbox, mcp-skill, the mcp-proxy relist and `--require-token-file`, the security-review fixes and their tests were committed as `7b603d2` (2026-10-11), and the page was promoted to `status: active` with `sources:` and `verified.commit: 7b603d2`. From that commit on this ADR is append-only: a later change to the runner, the skill server or their contract lands as a dated addendum here, never as an edit of the body. The shipped plan is kept as a record in [[spec-sbx-restricted-runner]].

## Addendum (2026-10-12): the runner is a sibling child of the member's own proxy; a missing tier root is an empty tier

Two changes, both decided by Zoltán on this date while wiring the household deployment.

### The runner is not behind a dedicated proxy

The Decision put `mcp-sandbox` behind its own `mcp-proxy --http` instance because one runner was to be shared by every per-uid skill proxy, with one global concurrency cap (alternative 6). That premise does not hold: every household member gets an own proxy instance, so there is nothing to share. The deployed shape is one more child in the member's own proxy config, beside `mcp-skill`: the runner child has no `sbx` wrapper (it spawns `sbx` itself and Seatbelt does not nest), `call_timeout` 190 and `startup_timeout` 120; `mcp-skill` reaches it by calling back into that same proxy, `--runner-url http://127.0.0.1:<proxy port>/mcp` with the proxy's own token file and `--runner-max-body` equal to the proxy's `--max-body-bytes`. No code changed for it: the runner client already spoke Streamable HTTP to an mcp-proxy, and the proxy's threaded front serves the nested call on its own connection. The KD-17 chain is unchanged (175 < 190 < 200 < the skill child's 220).

What this gives up, accepted: `sandbox_call` is now in the member's tool list, so the model can run an arbitrary inline script under sbx restricted mode (no network, an allowlist of reads, writes only in the run's workdir) and not only indexed, approved skill scripts; and the runner token is the member proxy's token, which lives where that proxy keeps it, not under `~/.claude` (T3 now applies to that file). T1 is unchanged in kind: the token goes to whatever listens on the proxy's loopback port. Two runners must not share a `work_root`: the second refuses to start on the work-root lock, and a child that fails startup takes the whole proxy down. The dedicated-proxy shape stays valid where one runner really is shared; [[mcp-sandbox]] carries both.

### A missing tier root is an empty tier

ai-soul creates each tier root lazily on its first publish (0700) and writes `skills.json` atomically. `mcp-skill` used to refuse startup when a configured root did not exist and to report a missing root or a missing `skills.json` as a tier `error`. Now ENOENT on the root (including a missing parent) is accepted at startup, and at runtime a missing root or a missing `skills.json` is the empty tier with no error, never cached, so a root that appears later is read on the next call `Scripts/mcp-skill.py:RootMissing` `Scripts/mcp-skill.py:load_tier`. ENOTDIR, a non-directory, a symlink, a foreign owner, a group or other write bit and every other index refusal stay as they were. Rows `mcp_skill:A6`-`A9`, `E4`, `E5`, run red first; the suite went from 83 to 89 cases.

## Addendum (2026-10-12): a python3 interpreter may opt out of -S to use a venv's packages

Decided by Zoltán: the deployed runner's python3 is the venv interpreter the household's mcp-webfetch already needs, so there is one Python environment, and skill scripts must be able to import that venv's packages. The runner started python3 as `-I -S -B` `Scripts/mcp-sandbox.py:_ARGV_FLAGS`; `-S` skips the site module, so a venv's `site-packages` never reached `sys.path`.

An interpreter entry now takes an optional `site`, a real JSON bool, default false, accepted only on `python3` (`Scripts/mcp-sandbox.py:_SITE_INTERPRETERS`); `site: true` makes the prefix `-I -B` `Scripts/mcp-sandbox.py:_SITE_ARGV_FLAGS`, and that one prefix feeds the run, the `--version` self-test and the VSZ probe. `capabilities` reports `site` per interpreter. The deployment is `{"path": "<venv>/bin/python", "extra_reads": ["<venv>"], "site": true}`.

What it costs, accepted: with `site` on, the venv's `.pth` files run at every interpreter start inside the sandbox. The venv is not writable from a run (restricted mode writes only the workdir), and it is the same tree mcp-webfetch already executes, so it adds no writer; it does make every package in it importable by an approved skill script. `-I` stays, so `PYTHON*` variables and the user site are still ignored. Rows `mcp_sandbox:A12` (config), `D5` (golden argv) and `I12` (live: a venv behind a Homebrew framework symlink imports with `site: true` and gets `ImportError` without it), run red first; the suite went from 151 to 154 cases.

## Addendum (2026-10-12): the runner may reach an allowlist of hosts through a per-run egress proxy

Decided by Zoltán with ADR 0033 ([[0033-egress-through-an-allowlisting-proxy]]), which carries the reasoning, the security review and the measurements. The sandbox still has no network; the runner now owns a way out that it vets.

- **Config.** An optional top-level `network` object, `{allow, ports?, nat64?}`, global for the runner; absent means no network and an unchanged run `Scripts/mcp-sandbox.py:_cfg_network`. Refusals name the key, never the value.
- **A proxy per run.** `Scripts/mcp-sandbox.py:_RunEgress` starts a CONNECT-only allowlisting proxy before sbx is spawned and closes it in the run's `finally`, bounded by the cleanup term so the KD-17 chain is unchanged `Scripts/mcp-sandbox.py:EgressProxy`. It is reached through `--egress-port` on macOS and `--egress-socket` on Linux, and the script gets `HTTPS_PROXY` / `HTTP_PROXY` (both cases) pointing at it `Scripts/mcp-sandbox.py:build_sbx_argv`. A proxy that cannot start refuses the run `sandbox_unavailable`.
- **`egress/` is a work-root entry** beside `quarantine/`: created and vetted at startup, stale entries swept, a planted file or symlink refuses the start; each Linux run's socket is `egress/<32-hex>/s` in a 0700 directory `Scripts/mcp-sandbox.py:_open_egress_root`.
- **`extra_reads` may not cover `work_root`.** An entry equal to or above it, spelled or realpath'd, is refused at load: it would expose sibling runs' workdirs and sockets `Scripts/mcp-sandbox.py:_refuse_reads_over_work_root`.
- **Visibility.** `capabilities` carries `network: {enabled, allow, ports}`, and a run result carries `egress: {tunnels, refused}` only when `network` is configured `Scripts/mcp-sandbox.py:capabilities`.

The Declared limits gain two lines that belong to this runner: TLS is not inspected, so a script can send anything to an allowed host; and on macOS the proxy's TCP endpoint cannot see the peer uid, so another local uid can use the allowlisted tunnels during a run. `mcp_sandbox` went from 154 to 192 cases (A13-A19, D6-D7, H25-H26, I13-I17, group N), every row red first.

## Addendum (2026-10-12): a schema key the called function does not read is dropped, not refused

Decided by Zoltán and shipped in `a3601b1`. The body's dispatcher rule (unknown functions and members get the did-you-mean pointer that never resolves, [[0030-point-at-the-near-miss-never-resolve-it]]) is narrowed for `skill_call` only.

### What broke

`skill_call` advertises one flat `params` object whose properties are `name`, `file`, `script`, `args`, `stdin` and `timeoutMs`, offered to all four functions alike. A model fills every field it is shown: `list` arrived with `name`, `file` and `script` set to `dummy`, `args` `[]`, `stdin` `""` and `timeoutMs` `0`, was answered `Unknown params for 'list': ... Accepted: (none).`, and called again the same way. The refusal named keys the tool's own schema had offered, so the model had no way to read it as its mistake, and it looped.

### The rule now

The set of schema keys is read from the advertised schema itself, not typed a second time `Scripts/mcp-skill.py:SCHEMA_PARAMS`. Before dispatch, a key that is in that set but not in the called function's accepted row is dropped; a key outside the schema is still refused `invalid_params` with the fleet's `Unknown params for` sentence, the accepted list and the near-miss pointer `Scripts/mcp-skill.py:ACCEPTED` `Scripts/mcp-skill.py:handle_skill_call`. The drop happens before the member type check, so a filled-in `timeoutMs` of `0` on `load` is neither refused as unknown nor type-checked. A dropped key is not resolved into anything; it is simply not read, so 0030's rule that a suggestion never resolves is untouched.

### What it costs, accepted

For the six schema keys this is the silent drop that 0030's 2026-10-08 addendum retired elsewhere: a caller who puts `file` on `run`, or `script` on `load`, meaning it, gets the function's answer without being told the key was ignored. Accepted because each of those keys is one the tool itself offered, while a misspelt or invented key, the case the near-miss pointer exists for, is still refused. The rule is `skill_call`'s alone: `sandbox_call` advertises a `params` object with no properties, so it has no schema key to drop and still refuses every key its function does not read.

Rows `mcp_skill:L1` (`list` with all six schema keys filled answers the plain list), `L2` (`load` with `run`'s keys filled serves the plain load) and `L3` (`nmae` is refused naming the key and pointing at `name`, while the `timeoutMs` beside it is not), run red first against the server that refused every schema key; the suite went from 89 to 92 cases.

## Addendum (2026-10-12): a schema key holding its type's empty value counts as omitted

Shipped in `731e149`; it extends the previous addendum's drop rule for `skill_call` only.

### What broke

With unread schema keys dropped, the next live call filled every field with its type's empty value instead of a placeholder: `load` arrived as `name` `idokep-weather`, `file` `""`, `script` `""`, `args` `[]`, `stdin` `""`, `timeoutMs` `0`. `file` is a key `load` reads, so it was kept, and `""` failed the `refs/<leaf>` / `scripts/<leaf>` check: the load was refused for a field the model meant to leave out.

### The rule now

Each schema key's type is read from the advertised schema, as the key set already was `Scripts/mcp-skill.py:SCHEMA_TYPES`. In the same filter, before the unknown-key refusal and the member checks, a schema key is also dropped when its value is the empty value of its type: `""` for a string, `[]` for an array, `0` for an integer `Scripts/mcp-skill.py:_is_schema_empty` `Scripts/mcp-skill.py:handle_skill_call`. The test is the exact type, so a bool `false` is not taken for `0` and an empty value of the wrong type (`timeoutMs` `""`) is not dropped; both still reach the member type check and are refused there. A key outside the schema is never dropped, empty or not, so the near-miss pointer of [[0030-point-at-the-near-miss-never-resolve-it]] is untouched.

### What changes for a caller

A required key left empty is now a missing key: `name` `""` on `load` or `run` answers `name is required` (it used to answer `Unknown skill name.`), and `script` `""` on `run` answers `script is required` `Scripts/mcp-skill.py:_check_members`. `run` with `args` `[]`, `stdin` `""` or `timeoutMs` `0` behaves exactly as if they were omitted.

### What it costs, accepted

An empty value can no longer be sent on purpose: an explicit `timeoutMs` `0` is not forwarded to the runner, so the runner's own default applies, and an explicit empty `stdin` is the same as no `stdin`. Accepted because a model fills unused fields with exactly these values, so honouring them would keep refusing or misreading the common call to serve a rare deliberate one; a caller that wants a short timeout sends a positive one.

Rows `mcp_skill:L4` (`load` with all six keys empty but a valid `name` serves the plain load), `L5` (`run` with the same fill reaches a recording runner-client stand-in with exactly the plain run's params, no `timeoutMs` `0`) and `L6` (`load` with `name` `""` is refused `name is required`), run red first; the suite went from 92 to 95 cases.
