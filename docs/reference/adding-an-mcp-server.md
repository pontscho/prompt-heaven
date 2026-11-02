---
name: adding-an-mcp-server
type: reference
status: active
title: Adding an MCP server -- building and registering Scripts/mcp-<x>.py
description: The runbook for a new single-file MCP server -- what to build from the skeleton, which generated regions it carries, which fleet tables it must join (smoke SERVERS, the read_loop/cancel/wire_log/handler_crash rosters, the typed SUITES counts), its own suite, the skill doc and wiki pages, and deployment -- each step with the forge target that proves it and what goes red if it is skipped, using mcp-skill in 7b603d2 as the worked example.
sources:
  - Scripts/MCP_SKELETON.md
  - Scripts/amalgamate.py
  - Scripts/_mcp_smoke_test.py:SERVERS
  - tests/test_read_loop.py:FLEET
  - tests/test_cancel.py:FLEET
  - tests/test_wire_log.py:WIRE
  - tests/test_handler_crash.py:FLEET
  - tests/run.py:SUITES
  - tests/test_mcp_footprint.py:undeclared_ceiling
  - tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS
  - project-forge.yaml
verified:
  commit: cef0940
  date: 2026-11-01
links:
  - scripts
  - tests
  - generated-regions
  - mcp-skill
  - mcp-sandbox
  - mcp-proxy
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - 0027-the-proxy-relays-it-never-composes
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0032-a-runner-behind-a-proxy
---

# Adding an MCP server

A new server is one stdlib-only file, Scripts/mcp-&lt;x&gt;.py, exposing one dispatcher
tool &lt;x&gt;_call. Writing the file is the small half: the fleet is held together by
per-server tables in the smoke harness and in four AST gates, by typed case counts
in `tests/run.py:SUITES`, and by generated regions, and a server that skips any of
them is either red or — worse — silently invisible to a gate. This page lists every
step in order, with the forge target that proves it and what goes red when it is
skipped. The worked example throughout is `Scripts/mcp-skill.py`, added in
`7b603d2` together with mcp-sandbox; that commit touches every file a new server
touches ([[mcp-skill]], [[mcp-sandbox]]).

All forge targets below are `build.syntax` or `test.<suite>` in `project-forge.yaml`;
every `test.<suite>` target is `python3 tests/run.py <suite>` and `requires: [syntax]`.

## A. Build the server

**1. The file, from the skeleton.** Copy the shape of `Scripts/MCP_SKELETON.md`
sections 1–7 and walk its section 9 convergence checklist. In short: shebang, a
PEP 723 block with `dependencies = []`, a docstring; a hand-written module logger
`log = logging.getLogger("mcp-<x>")`; `class McpServer` with `PROTOCOL_VERSION` as
its first member and the initialize reply reading it; `ping` answers `{}`,
notifications get no reply, an unknown method is `-32601`; `tools/list` returns
exactly one tool. The read loop is ADR 0008's shape — stdin readline on its own
single-worker executor, one task per message — and `notifications/cancelled` is
handled on the loop before dispatch
([[0008-a-serialized-read-loop-looks-like-a-dead-server]]). A server whose handlers
block (HTTP, subprocess) is the `pool` variant; copy it from a pool server such as
`Scripts/mcp-skill.py:McpServer` or `Scripts/mcp-jenkins.py`.
The error envelope is ADR 0010's: a handler failure raises or returns
`{"error": ...}` and reaches `isError`; the catch-all logs `log.exception("literal")`
([[0010-a-handler-failure-must-reach-iserror]]).
*Proof:* `build.syntax`, then `test.smoke`, `test.protocol_version`,
`test.read_loop`, `test.handler_crash`. *Skipped:* smoke fails initialize / ping /
`-32601`; protocol_version and read_loop are red on the shape; a `log.debug`
catch-all is red in handler_crash.

**2. The generated regions.** Paste empty marker pairs and let the generator fill
them: `python3 Scripts/amalgamate.py` (writes) — never hand-edit inside a region.
A new file matches `Scripts/amalgamate.py:TARGET_GLOB` and is a target by merely
existing; it must NOT be added to `Scripts/amalgamate.py:DECLARED_HOSTS`, which is
only for hosts outside the glob (`target-glob` in
`tests/test_generated_region.py:group_contract` fails a declared host the glob
already covers). The marker set a pool server carries, as in `Scripts/mcp-skill.py`:

| Source | Blocks on one marker |
|---|---|
| `_mcp_logging.py` | `_LOG_VALUE_WIDTH, _LOG_KEYS_SHOWN, _log_value` |
| `_mcp_logging.py` | `_configure_logging` |
| `_mcp_json.py` | `JSON_INT_LITERAL_LIMIT, _json_no_constant, _json_finite_float, _json_bounded_int, _strict_loads, _strict_dumps, _json_error_window, _ensure_dict` |
| `_mcp_dispatch.py` | `_did_you_mean, _unknown_params_refusal` |
| `_mcp_concurrency.py` | `MAX_INFLIGHT_REQUESTS` |
| `_mcp_json.py` (indented inside `McpServer`) | `_result, _error` |

A server that pages or caps its answer also takes the `_mcp_paging.py` blocks, as
`Scripts/mcp-jenkins.py` does. The block contract — a block may use only builtins,
its own source's imports and blocks co-listed on the same marker, dependency first —
is `Scripts/MCP_SKELETON.md` section 8 and [[generated-regions]]; why shared code is
generated rather than imported is [[0025-generate-do-not-import]]. A canonical
source in `Scripts/amalgamate.py:WHOLE_SOURCES` is taken whole or not at all.
*Proof:* `test.amalgamate_check`, `test.generated_region`. *Skipped:* a BEGIN with
no END is a hard error; a stale or hand-edited region exits 1 under `--check`; a
bare `json.load`/`json.loads` anywhere in the host is a group H failure unless it
has a reasoned row in `tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS`
(mcp-skill's duplicate-key second pass `Scripts/mcp-skill.py:_refuse_duplicate_keys_parse`
has one).

**3. A canonical source the server needs but that does not fit.** If an existing
source almost fits (the OAuth core `Scripts/_mcp_oauth.py` is the live case),
either take its blocks on one marker with their dependencies co-listed, or extend
the source first. Extending is a domain decision under
[[0014-a-canonical-source-is-a-domain]], with cases in that source's own suite, and
the source's docstring naming its hosts must be updated. *Proof:* the source's
suite (`test.mcp_oauth` for OAuth) and `test.generated_region`.

**4. Transport and dependencies.** Stdlib only
([[0024-pure-python-39-and-the-stdlib]]); a third-party import must be allowlisted
and `find_spec`-guarded. For HTTPS the verified-TLS precedent is
`Scripts/llm-router.py:_RtHttpsConnection` (`ssl.create_default_context`, a TLS 1.2
floor, `CERT_REQUIRED` asserted). *Proof:* `test.py_deps`. *Skipped:* an unguarded
or unlisted import is a FAIL.

**5. Config and secrets.** One strict JSON file; refuse a symlink, a file not owned
by the euid, and any group/other mode bit; refuse an unknown or missing key naming
the key, never the value. Precedents: `Scripts/mcp-sandbox.py:load_config` with
`Scripts/mcp-sandbox.py:_check_owned_file`, and for a secrets file the stricter
`Scripts/llm-router.py:_rt_read_config_file`; a token written back goes through a
locked, re-read, atomic replace as in `Scripts/llm-router.py:_rt_config_update_oauth`.
mcp-skill re-reads its runner token file per run and never logs it
`Scripts/mcp-skill.py:RunnerClient`. *Proof:* the server's own suite (step 9) — no
fleet gate covers a new config reader.

**6. The parameter layer.** A per-function accepted table
(`Scripts/mcp-skill.py:ACCEPTED`) and the generated
`Scripts/_mcp_dispatch.py:_unknown_params_refusal`: an unknown key is refused with a
pointer that never resolves ([[0030-point-at-the-near-miss-never-resolve-it]]).
`arguments` and `params` may arrive as JSON strings; booleans go through
`_bool_param`. Add a `_resolve_aliases` only if the server really has aliases, and
then the same parameter spelled twice is refused, not chosen
([[0015-ambiguity-is-the-defect]]). *Proof:* `test.smoke` (the near-miss and alias
rows of step 10).

**7. The reply ceiling.** Declare a `DEFAULT_MAX_*CHARS` constant in one of ADR
0013's three classes, `tests/test_mcp_footprint.py:RATIFIED_CLASSES` — COMPOSED for a
reply the server built, VERBATIM or SEQUENCE only with the reason written beside it
([[0013-the-ceiling-is-a-payload-class]]). *Proof:* `test.mcp_footprint`.
*Skipped:* `every-registered-server-declares-a-ceiling`
`tests/test_mcp_footprint.py:undeclared_ceiling` fails — but only once the smoke
row says `registered: True`. An unregistered server is exempt, which is why
mcp-skill and mcp-sandbox carry no such constant today: the gate turns red the day
either is registered.

**8. Writes and binary data.** No fleet gate covers a new writer. The precedents:
webfetch's `save_to` resolves the target by realpath and contains it in
`--project-root` (`Scripts/mcp-webfetch.py:_resolve_save_path`), refuses a protected
path class (`Scripts/mcp-webfetch.py:_protected_save_class`) and creates
exclusively unless overwrite is asked (`Scripts/mcp-webfetch.py:_save_body`) — text
only; binary inline goes back base64 under a byte cap (mcp-jenkins). *Proof:* the
server's own suite.

**9. The server's own suite.** `tests/test_<x>.py` exposing `run(opts) -> Suite`,
written and run red before the server exists, every row failing for one stated
reason (`tests/test_mcp_skill.py` is the precedent and says so in its docstring);
fixtures under `.claude/tmp/`, any peer faked on loopback. *Proof:* `test.<x>`.

## B. Register in the repo

**10. The smoke harness.** Add a row to `Scripts/_mcp_smoke_test.py:SERVERS`:
`{"file", "tool", "args", "registered"}`, plus `forced_exc_code` if the server
refuses a non-object `params` as `-32602` before any handler (mcp-skill does). Then
the near-miss rows: `Scripts/_mcp_smoke_test.py:NEAR_MISS_FUNCTION` always;
`Scripts/_mcp_smoke_test.py:NEAR_MISS_PARAM` iff the source carries the
`Unknown params for` sentence; `Scripts/_mcp_smoke_test.py:DOCUMENTED_CALL` iff it
is a later parameter refuser (every new one is); and
`Scripts/_mcp_smoke_test.py:ALIAS_COLLISION` iff the source defines
`_resolve_aliases`. mcp-skill has the first three and no alias row.
*Proof:* `test.smoke`. *Skipped:* the coverage checks in
`Scripts/_mcp_smoke_test.py:near_miss_checks` and
`Scripts/_mcp_smoke_test.py:alias_collision_checks` fail. Worse, with no SERVERS row
smoke never starts the server, and mcp_footprint and name_existence, which read the
same table, never see it.

**11. The four per-server AST rosters.** Each gate has a table keyed by file and a
declared total per class, and its group C fails a server missing from the table
(`SERVER-NOT-IN-TABLE`); the totals are compared with what was measured, so a new
row means raising its class total in the same edit:

- `tests/test_read_loop.py:FLEET` — read-loop shape, `pool` or `coroutine`; totals
  `DECLARED_POOL` / `DECLARED_COROUTINE`. *Proof:* `test.read_loop`.
- `tests/test_cancel.py:FLEET` — what a cancel reclaims (`task`, `kill`,
  `lsp-cancel`, `pg-cancel`, `reply-only`; mcp-skill is `reply-only`); one
  `DECLARED_*` total per class. *Proof:* `test.cancel`.
- `tests/test_wire_log.py:WIRE` — the shape each wire-log site may log;
  `DECLARED_FULL` / `DECLARED_MINIMAL` / `DECLARED_SILENT`
  ([[0011-a-truncated-payload-carries-the-first-cookie]]). *Proof:* `test.wire_log`.
- `tests/test_handler_crash.py:FLEET` — which catch-all layers exist;
  `DECLARED_W` / `DECLARED_D`. *Proof:* `test.handler_crash`.

*Skipped:* a missing row or an unraised total is red in each.

**12. The typed case counts.** Add a `tests/run.py:SUITES` row for the new suite
(a `run_<x>` wrapper calling `run_python_suite`, as `tests/run.py:run_mcp_skill`),
and a `test.<x>` target in `project-forge.yaml` with `requires: [syntax]`. The
counts of read_loop, cancel, wire_log, handler_crash and protocol_version grow per
server by design — a server arriving without a row is the defect they exist to
catch — so re-MEASURE each one from a run, never compute it. *Proof:* `test.all`
with `RUN_ARGS` naming only the touched suites. *Skipped:* `tests/run.py` prints
`CASE COUNT DRIFT` and exits 1 ([[tests]]).

**13. Optional rows, when they apply.** `tests/test_table_cells.py:SWEEP_EXPECTED`
if the server renders tables; `tests/test_param_contract.py:HOSTS` if it should be
held to the accepted-table contract (that suite's count in `tests/run.py:SUITES`
then moves); `tests/test_generated_region.py:STRICT_SITES` to drive a peer-JSON
parse site.

**14. The skill doc and grants.** `ClaudeCode/skills/mcp-<x>/SKILL.md` in the shape
of `ClaudeCode/skills/mcp-gdc/SKILL.md` (name, a description naming the tool,
triggers; then the tool, its params and aliases, the empty-function behaviour, the
`max_answer_chars` default, call examples); grant the `mcp__` tool name to the
agents that should call it. A server consumed only through [[mcp-proxy]] may have
none: mcp-sandbox and mcp-skill have no skill doc. *Proof:* `test.name_existence`.
*Skipped:* a prescribed name no server exposes, or a grant to an unregistered
server, fails (its rules R1 and R5).

**15. The wiki.** A component page `docs/components/mcp-<x>.md` (precedents
[[mcp-skill]], [[mcp-sandbox]]); an ADR for every real decision (precedents
[[0027-the-proxy-relays-it-never-composes]], [[0032-a-runner-behind-a-proxy]]); the
server named in the MCP servers section of [[scripts]]; its suite in the roster of
[[tests]]; then `wiki_call reindex` — `docs/INDEX.md` is never hand-edited.
*Proof:* `test.wiki_index`, and `wiki_call freshness` with `gating: 0`.

## C. Deploy (outside the tree)

**16. For Claude Code (stdio).** Add an `mcpServers` entry in `~/.claude.json`
with the absolute repo path — `~/.claude/scripts` is a symlink to `Scripts/`, so
nothing is copied ([[scripts]]) — then flip the smoke row to `registered: True`
(group F of `tests/test_mcp_footprint.py` cross-checks the flag against the live
`~/.claude.json`). A new server is picked up by a new session or `/mcp` reconnect.
Registering is also what arms the ceiling gate of step 7.

**17. Behind mcp-proxy.** Add a child entry with the keys
`Scripts/mcp-proxy.py:_CHILD_KEYS` accepts (`name`, `command`, `args`, `env`,
`wrapper`, `call_timeout`, `startup_timeout`, `relist`), then restart the proxy:
children start eagerly, and a tool name duplicated across children refuses startup
([[mcp-proxy]], [[0027-the-proxy-relays-it-never-composes]]).

## Where to look

Line numbers are a quotation of the tree at `cef0940`, not maintained; the symbol
is the anchor.

| What | Anchor | Line at cef0940 |
|---|---|---|
| Smoke server table | `Scripts/_mcp_smoke_test.py:SERVERS` | 105 |
| Alias-collision probes | `Scripts/_mcp_smoke_test.py:ALIAS_COLLISION` | 421 |
| Near-miss function probes | `Scripts/_mcp_smoke_test.py:NEAR_MISS_FUNCTION` | 530 |
| Near-miss param probes | `Scripts/_mcp_smoke_test.py:NEAR_MISS_PARAM` | 575 |
| Documented-call probes | `Scripts/_mcp_smoke_test.py:DOCUMENTED_CALL` | 612 |
| Read-loop roster / totals | `tests/test_read_loop.py:FLEET`, `DECLARED_POOL` | 190 / 239 |
| Cancel roster / totals | `tests/test_cancel.py:FLEET`, `DECLARED_TASK` | 199 / 259 |
| Wire-log roster / totals | `tests/test_wire_log.py:WIRE`, `DECLARED_FULL` | 270 / 326 |
| Handler-crash roster / totals | `tests/test_handler_crash.py:FLEET`, `DECLARED_W` | 194 / 254 |
| Suite table, mcp_skill row | `tests/run.py:SUITES` | 302, 928 |
| Generator target glob / hosts | `Scripts/amalgamate.py:TARGET_GLOB`, `DECLARED_HOSTS` | 69 / 102 |
| Strict-JSON exceptions | `tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS` | 450 |
| Ceiling classes | `tests/test_mcp_footprint.py:RATIFIED_CLASSES` | 562 |
| Proxy child keys | `Scripts/mcp-proxy.py:_CHILD_KEYS` | 501 |
| Skeleton checklist | `Scripts/MCP_SKELETON.md` section 9 | 969 |

## Not verified

These came from the recon behind this page and were not proven from the code:

- Whether any wiki gate fails on a server with no component page; `test.wiki_index`
  checks the index, not coverage.
- Whether any gate checks the `project-forge.yaml` targets against
  `tests/run.py:SUITES`; no such check was found, so a missing forge target is
  probably silent.
- The exact `claude mcp add` syntax and scope for step 16.
- A copy deployment of the proxy on another host (copy steps, restart): no page
  describes one, and the owner restarts a live proxy.
- That the `tests/README.md` roster is maintained; it looked stale.
- Whether a provider's token endpoint needs a `client_secret` — an external fact
  about the provider, not a property of this tree. The OAuth builders in
  `Scripts/_mcp_oauth.py` send one only when the host passes the optional
  keyword-only `client_secret`.
