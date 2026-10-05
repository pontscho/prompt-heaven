# Feature Implementation Plan: mcp-search — web and code search as an MCP server

## 1. Requirements Summary

### Functional Requirements
- [FR-1] New stdio MCP server `Scripts/mcp-search.py` with one dispatcher tool `search_call` (`function` + `params`, short forms `f` / `p`).
- [FR-2] `web`: params `{queries: list[str] | str, limit?: int = 10}`, aliases `query` / `q` -> `queries`. DDG lite first; a DDG block switches this and every remaining query of the call to Bing.
- [FR-3] `code`: params `{queries: list[str] | str, lang?, repo?, path?, limit?: int = 10}` against grep.app.
- [FR-4] No function -> status text. Unknown function -> `Unknown function: X. Available: code, web` (one line, comma list — parsed by the name_existence suite). Unknown params refused. Alias collisions refused (ADR 0015).
- [FR-5] Output is the markdown the CLIs print today, with no transport label.
- [FR-6] A query that ends blocked at the end of its ladder (Bing blocked for `web`, grep.app blocked for `code`) makes the call `isError: true`; the text still carries every successful query's results plus a block notice per blocked query. No results is a success. Bad params -> `isError`. Per-query notices are FIXED strings only — `blocked by Bing`, `blocked by grep.app`, `transport error` (a transport error stays a success, its query showing that notice) — never `ChromeClientError` text, paths or tracebacks.
- [FR-10] Parameter caps (server): at most 10 queries per call, each at most 512 chars, `limit` 1..50; a non-`str` query item, `lang`, `repo` or `path` -> `isError`. Each breach answers a fixed message (e.g. `queries: at most 10 per call`). `max_answer_chars` is clamped (section 5).
- [FR-7] The shared search code moves into two NEW canonical sources, generated into the server and into the two CLIs.
- [FR-8] Every endpoint uses the Chrome transport. The verified-first ladder, the sticky switch, the transport label and the whole CDP backend are removed from the CLIs.
- [FR-9] CLI: a Bing / grep.app block -> exit 1 with one stderr line.

### Non-Functional Requirements
- [NFR-1] Stdlib only, plain `python3`, PEP 723 header with `dependencies = []` (ADR 0024).
- [NFR-2] Concurrent callers never burst one endpoint: one `_ChSession` per endpoint, used only under that endpoint's lock (acquired with a 120 s timeout -> `isError` `endpoint busy, retry later`); pacing is process-wide per endpoint.
- [NFR-3] Ceiling: COMPOSED 24000 (generated `DEFAULT_MAX_ANSWER_CHARS`), `max_answer_chars` param, truncation note; no offset paging.
- [NFR-4] Server logs through the generated `_configure_logging` logger, never `print` to stderr; wire logging structure-only (ADR 0011). The search `note` log lines are structure-only too: endpoint, query index, event, status code or exception CLASS name — never the query text, a response body or an exception message (CWE-532). The CLIs keep their current stderr lines.
- [NFR-5] All fleet gates green: generated_region, amalgamate_check, read_loop, cancel, wire_log, handler_crash, protocol_version, mcp_footprint, name_existence, smoke, mcp_chrome, mcp_websocket, search_parsers (18, unchanged), py_deps.

### Success Criteria
- [SC-1] `python3 -B Scripts/amalgamate.py --check` prints nothing; `python3 tests/run.py` all suites pass with updated typed counts.
- [SC-2] New `tests/test_mcp_search.py` passes with no network (stub sessions), including the cap, busy-endpoint, fence, fixed-notice, log-structure and session-kwargs rows (Step 4).
- [SC-3] `Scripts/_mcp_smoke_test.py` handshakes mcp-search and refuses the alias-collision probe.
- [SC-4] `search_duckduckgo.py "q"` and `search_github.py "q" --lang Python` behave as before for consumers (positional queries, `--lang/--repo/--path/--limit`), minus the label.

### Assumptions
- Consumers of the CLIs (agents, skills, OpenCode) use only positional queries and `--lang/--repo/--path/--limit` (verified by the explorer); removing `DDG_BACKEND` breaks nobody.
- The block predicates measured under ADR 0026 D16/D17 stay valid on the Chrome path.

### Out of Scope
- `ClaudeCode/scripts/search_*.py` stale duplicates; mcp-proxy changes (central serving is later, config only); certificate verification on the Chrome path (file a roadmap item); any cache; offset paging.

## 2. Architecture Analysis

### Affected Subsystems
- Generator — `Scripts/amalgamate.py` (`CANONICAL_NAMES` :77-79, `WHOLE_SOURCES` :102, `DECLARED_HOSTS` :92 unchanged) and its mirror `tests/test_generated_region.py` (:167, :188).
- CLI hosts — `Scripts/search_duckduckgo.py`, `Scripts/search_github.py`: hand-written search code replaced by generated regions; CDP / verified / sticky / label code deleted.
- Canonical source `Scripts/_mcp_websocket.py` — loses its DDG host (docstring only).
- New server `Scripts/mcp-search.py` — modelled on `Scripts/mcp-webfetch.py`.
- Fleet gates and smoke — registration rows (section 6, Step 5).
- Docs — listed only (Step 6), ingested later via p:wiki.

### Integration Points
- `_mcp_websearch.py` / `_mcp_codesearch.py` -> generated into `mcp-search.py`, `search_duckduckgo.py` (websearch), `search_github.py` (codesearch).
- `_mcp_chrome.py` (WHOLE, already generated into both CLIs) -> also into `mcp-search.py` together with `_mcp_brotli.py`, `_mcp_zstd.py`.
- Search blocks receive a session object from the host; the host owns `create_session` / `_DECODERS` (hand-written, as today: `search_duckduckgo.py:7373`, `:7383`; `search_github.py:7097`, `:7106`).

### Constraints
- A generated block may reference only names of its own source plus the host's import aliases (`free_names` `amalgamate.py:291`, `host_provides` :334). So search blocks never call `_ch_session_new`, `_ch_public_only_policy`, `_DECODERS`, nor catch `ChromeClientError` — catch `ConnectionError` (its base, `_mcp_chrome.py:128`) or `Exception`.
- Names disjoint across sources (`tests/test_generated_region.py:720` `sources-disjoint`). `ROTATE_EVERY` (`search_duckduckgo.py:83`, `search_github.py:73`) and `SEARCH_MAX_BYTES` (`:91`, `:80`) exist in both scripts: either give them distinct names per source (e.g. `WEB_ROTATE_EVERY` / `CODE_ROTATE_EVERY`) or keep them hand-written per host. Recommended: keep `SEARCH_MAX_BYTES` and `ROTATE_EVERY` hand-written per host (they are used by host-owned `create_session` and loop code; `tests/test_search_parsers.py:415` reads `SEARCH_MAX_BYTES` off each script; `tests/test_mcp_chrome.py:6308` `GH_SWAPPED` swaps `ROTATE_EVERY` on the host).
- Every block tab-safe (`block_is_tab_safe` `amalgamate.py:377`): no implicit line joins inside brackets, no backslash continuations. `tests/test_generated_region.py:1783` pins the unsafe set to exactly `["_rows_note"]`.
- `tests/test_py_deps.py:1099-1104` swaps `mod._START_CLOSE` on the loaded host: it must stay a top-level name of the host (a generated top-level assignment satisfies this).
- The generator keys blocks only on `def`, `class` and single-target `NAME = ...` (`assign_name` `amalgamate.py:249-252`, rationale :228-229); `load_blocks_text` silently skips every other module-level statement (:284-286). A module-level `_START_CLOSE["p"] = ...` row would vanish from the generated copy. Hence the builder-function pattern in section 3.
- Both new sources are `WHOLE_SOURCES`: each host carries ONE marker naming every name of the source, in source order (`tests/test_generated_region.py:617-653` `whole-source-regions`).
- `_ChSession` is not thread-safe (h2 HPACK state shared) — never touch a session outside its endpoint lock.
- Do NOT change `_ch_session_new`'s default `transport="verified"` (`_mcp_chrome.py:6683`; webfetch depends on it). Hosts pass `transport="chrome"` explicitly.

## 3. Captured Information

### Existing Patterns
- **Tab-safe tables = builder functions** — one key per line, never a multi-line literal (rule `_mcp_chrome.py:84-89`). `_mcp_chrome.py:176-206` works ONLY because its `p["..."] = ...` rows sit inside `def _chrome_profile()`; at module level they would be dropped (section 2). Lift every multi-line table as a builder plus a single-target top-level assignment:
  ```python
  def _start_close():
      t = {}
      t["form"] = frozenset(("form", "p", "hr") + _H + ("dl", "ul", "ol", "menu", "dir") + _LISTING)
      t["title"] = frozenset(("p",))
      return t

  _START_CLOSE = _start_close()
  ```
  Same for `_END_PRIORITY` (`_end_priority()`) and `EXT_TO_LANG` (`_ext_to_lang()`); a short set may instead be one physical line (`_VOID_TAGS = frozenset(("area", "base", ...))`). `_START_CLOSE` stays a top-level name (py_deps swap). Other multi-line constructs to reshape: `search_duckduckgo.py` `session.post(...)` :7472-7478, `session.get(...)` :7515-7519, :186-190, :224-227, :458-462; `search_github.py` :130-131, :170-176, :183-185, :7182-7188. `amalgamate.py` refuses an unsafe block by name, so the generator itself finds any line missed here.
- **Current host session factory** (`search_duckduckgo.py:7383-7392`) — becomes Chrome-only, returns the session alone:
  ```python
  def create_session():
      return _ch_session_new(decoders=_DECODERS, connect_policy=_ch_public_only_policy, timeout=20, max_bytes=SEARCH_MAX_BYTES, transport="chrome")
  ```
  (CLI hosts are tab-indented; keep their indentation. `tests/test_mcp_chrome.py:6308` keeps the name `create_session`.)
- **Search result contract** — `search_ddg` :7461 / `search_bing` :7512 / `search_github` `search_github.py:7166`: `list` = results (may be empty, also on transport error), `None` = blocked. Keep it. Today each catches `Exception` and returns `[]` (`search_duckduckgo.py:7488-7493`, `:7529-7532`; `search_github.py:7202-7206`), so a caller cannot see a transport failure. Two optional callable parameters replace the `print(..., file=sys.stderr)` lines:
  - `note(event, query, detail)` (default no-op) — `event` a fixed token (`ddg_undecodable`, `ddg_error`, `ddg_captcha` (emitted by `run_web` at the Bing switch), `bing_http`, `bing_undecodable`, `bing_error`, `grep_undecodable`, `grep_rate_limited`, `grep_http`, `grep_error`), `detail` the status code, decode error or exception object. The CLI's note renders today's exact stderr lines; the server's note logs structure-only (NFR-4: `type(detail).__name__` unless it is an `int`). `log` is not a CLI name, hence the callable.
  - `on_transport_error(exc)` (default no-op) — called in the `except Exception` arm before `return []`. The caller-visible result stays `[]`.
- **Block predicates** — `_ddg_blocked` :7420, `_bing_blocked` :7500, `_grep_app_blocked` `search_github.py:7143`: lifted verbatim, logic unchanged.
- **Error envelope** — handler returns `{"error": ...}`; `_handle_tool_call` sets `is_error = "error" in result` (`mcp-webfetch.py:8807`); `_tool_error` :8823. MCP_SKELETON §3a (`Scripts/MCP_SKELETON.md:162`). For FR-6 the handler returns `{"error": text}` where `text` is the full rendered body (successful results + block notices) — the body IS the error text, not a pre-rendered failure string inside a success.
- **Params** — `ACCEPTED_FETCH_PARAMS` `mcp-webfetch.py:7623`, `PARAM_ALIASES` :7631, `_resolve_aliases` :7674 (hand copy, refuses collisions), `HANDLERS` :8225, `handle_webfetch_call` :8311, tool schema `WEBFETCH_CALL_TOOL` :8391; `max_answer_chars` read with `_int_param(params.get("max_answer_chars"), DEFAULT_MAX_ANSWER_CHARS)` :8022; clamp pattern `min(MAX, max(1, _int_param(...)))` :8021; cap note pattern :7483. `_int_param` and `_json_error_window, _ensure_dict` are GENERATED regions from `_mcp_json.py` (`mcp-webfetch.py:7645`, `:8247`), not hand copies.
- **Pool server** — `MAX_INFLIGHT_REQUESTS = 8` region :8496-8499, `class McpServer` :8502 with `PROTOCOL_VERSION` first member :8505, `run` :8520 (reader executor + worker pool + drain on shutdown). MCP_SKELETON §5a :358, §6 main :516, §7c alias refusal :679, §8 generated regions :759.
- **Logging** — generated `_configure_logging(debug, log_file)` (`Scripts/_mcp_logging.py:95`).

### Type Definitions
- `_ch_session_new(decoders=None, connect_policy=None, timeout=30, max_bytes=0, log=None, tls12_fallback=False, allow_downgrade=False, rand=None, ssl_context_factory=None, transport="verified")` — `_mcp_chrome.py:6683`.
- `_ChSession.get(self, url, params=None, headers=None, timeout=None, mode="navigate", referer=None, on_headers=None)` :6335; `.post(self, url, data=None, headers=None, timeout=None, referer=None, on_headers=None)` :6350; `.close()` :6457; `.last_navigation_url`.
- `ChromeClientError(ConnectionError)` :128.
- `DEFAULT_MAX_ANSWER_CHARS = 24000` — `Scripts/_mcp_paging.py:160`; `_max_answer_chars(args)` :177.

### Build System
- Forge targets: `generated_region` (`project-forge.yaml:199-205`), `mcp_websocket` (:207-213), `mcp_chrome` (:223-229), `amalgamate_check` (:239-245). Descriptions name the source count ("one of the nine" :200), `CDPSearcher` (:208), the verified-first ladders (:224) and the declared hosts (:240).
- Suite counts in `tests/run.py` `SUITES` (:254): generated_region 97 (:441), mcp_websocket 42 (:446), mcp_chrome 855 (:536), read_loop 49 (:544), cancel 86 (:555), wire_log 55 (:564), handler_crash 60 (:574), protocol_version 74 (:609), search_parsers 18 (:625).

## 4. Alternative Approaches

### Selected: two domain sources, Chrome-only, per-endpoint locked sessions
**Rationale**: ADR 0014's rule (`docs/adr/0014-a-canonical-source-is-a-domain.md:86-87`, `:105-106`): web search and code search are two domains; neither fits an existing source. Generation (ADR 0025) keeps the server and CLIs single-file. One session per endpoint under a lock is the only safe use of a non-thread-safe `_ChSession` and gives process-wide pacing for free.
**Trade-offs**: two more registry lines and test mirrors; the CLIs lose the verified-first transport (ADR 0026 D15 reversed for search — recorded in an addendum); certificate verification on search endpoints is a follow-up.

All other design questions were decided by the user; no further alternatives are evaluated.

## 5. Implementation Strategy

### Overview
Lift the shared search logic out of the two CLIs into two tab-safe canonical sources, regenerate them back into the CLIs while stripping the CDP / verified / sticky / label machinery, then build `mcp-search.py` from MCP_SKELETON on top of the same regions with a per-endpoint session+lock layer, and register it across the fleet gates.

### Key Design Decisions
- **Session injection — ONE host hook `with_session(endpoint, fn)`**: `endpoint` is `"ddg"`, `"bing"` or `"grep.app"`; `fn(session, on_transport_error)` runs one search and returns its result. The host does everything around it: (server) take the endpoint lock with the 120 s timeout, pace, create / warm up / rotate the session, call `fn(session, broken.append)`, and if `broken` is non-empty close and drop that endpoint's session (lazily recreated next call) — all under the lock; (CLI) a plain call with its own sleep + rotate-every-`ROTATE_EVERY` and the same drop-on-transport-error. Hosts own `create_session`, `_DECODERS`, the lock/pacing layer.
- **Run loop contract** (websearch): `run_web(queries, with_session, note_for)` -> list of `(query, results_or_None, transport_failed)`; each query is one `with_session("ddg" | "bing", fn)` call where `fn` wraps the host's `bad` callable so `run_web` itself records the failure before calling through:
  ```python
  failed = []
  def fn(s, bad):
      def on_err(exc):
          failed.append(exc)
          bad(exc)
      return search_ddg(q, s, note_for(i), on_err)
  results = with_session("ddg", fn)
  transport_failed = bool(failed)
  ```
  (`failed` is fresh per query attempt; the same wrapping applies to the Bing call.) Without the wrap only the host would know, and `transport_failed` could not be computed; a DDG block -> this and every remaining query go to Bing; Bing `None` -> that query is recorded blocked. `note_for(i)` lets the server bind the query index into the log line. One `with_session` call per query, so the DDG lock is always released before the Bing lock is taken — locks are never nested. `code` has no shared loop: the host iterates `with_session("grep.app", ...)` per query. Exact signature is the implementer's call as long as the block references no host-only name.
- **Fence safety** (shared `format_code_results`, so CLI and server): a snippet fence is one backtick longer than the longest backtick run in the snippet, minimum 3 (`_code_fence(lines)` in `_mcp_codesearch.py`; today a bare "```" at `search_github.py:7237`, `:7240`).
- **Parameter caps** (FR-10): checked in the handler before any lock is taken; fixed messages; `max_answer_chars` clamped `min(100000, max(1, _int_param(..., DEFAULT_MAX_ANSWER_CHARS)))` (webfetch clamp pattern :8021).
- **Output strings** (FR-6): the handler renders only fixed notices for blocked / transport-failed queries; exception detail goes to the log as a class name.
- **format_results** loses its `transport` parameter in both sources. The two sources' `format_results` collide by name — rename to `format_web_results` / `format_code_results` (disjointness gate).
- **Server indentation: SPACES.** The closest analogue (webfetch) is tab-indented, but spaces need no re-indentation of generated blocks and leave the tab-server pin (`tests/test_generated_region.py:1914-1917`, `["mcp-forge.py", "mcp-webfetch.py"]`) unchanged.
- **Endpoint layer (server only)**: `_Endpoint` with `lock`, `session` (lazy), `last_request` (monotonic), `count`, `jitter` (`(2.5, 5.0)` ddg; `(1.5, 3.0)` bing, grep.app); `ENDPOINT_LOCK_TIMEOUT = 120` s. `with_session` implements it: `lock.acquire(timeout=ENDPOINT_LOCK_TIMEOUT)` or raise an internal busy signal the handler turns into `isError` `endpoint busy, retry later`; create + warm up if none; sleep the remaining jitter since `last_request`; rotate (close + recreate) every `ROTATE_EVERY` queries; call `fn`; if `on_transport_error` fired, close and drop the session. Every session comes from `_create_session()` with `connect_policy=_ch_public_only_policy, max_bytes=SEARCH_MAX_BYTES, allow_downgrade=False, transport="chrome"`.
- **Cancel class**: reply-only (a call waiting on a lock finishes; no cancelled flag); shutdown drains in-flight like webfetch. Limit: all 8 workers (`MAX_INFLIGHT_REQUESTS`) can wait on one endpoint lock and starve the pool — bounded by the 120 s lock timeout and the 10-query cap.
- **Ceiling**: render all queries, then if `len > max_answer_chars` cut and append `[output capped at N chars; use fewer queries or a lower limit]`.

### Risk Mitigation
- Tab-unsafe lines missed during the lift -> the generator refuses the block by name; run `amalgamate.py --check` after Step 1.
- A block referencing a host-only name -> generated_region free-name gate fails red; inject via parameters.
- Group M of `test_mcp_chrome.py` is large (:6279-7717) -> delete rows by name first, then rewrite ladders, then recount 855.
- Concurrency regressions -> the lock test in `test_mcp_search.py` runs two threads against a stub session that records overlapping entry.
- Pool starvation (8 workers parked on one endpoint lock) -> 120 s acquire timeout + 10-query cap; accepted residual, no cancelled flag.
- A foreign-source name in a block (`ChromeClientError` in `_body_is_json` `search_github.py:7138`) -> catch `(ValueError, ConnectionError)` instead.

### Known security limitations
- Search results are third-party content entering model context unmarked; accepted (per-result labels rejected as token waste).
- Certificate verification on the Chrome path is a follow-up (roadmap item, Step 6).

## 6. Step-by-Step Plan

### Step 1: Create the two canonical sources and register them
**Files**: `Scripts/_mcp_websearch.py` (create), `Scripts/_mcp_codesearch.py` (create), `Scripts/amalgamate.py:77-79,102` (modify), `tests/test_generated_region.py:4-8,151-171,188-189,298-363` (modify)
**Dependencies**: none
**Description**:
- `_mcp_websearch.py` — EVERY name, in this source order (the host marker lists exactly these; all from `search_duckduckgo.py`): `_normalize` :98, `decode_duckduckgo_url` :106, `_raw_href` :118, `_has_class` :141, `_LiteParser` :155, `parse_lite_results` :208, `_decode_bing_url` :247, `_VOID_TAGS` :272 (one line), `_H` :283, `_LISTING` :284, `_FONTSTYLE` :285, `_start_close` (new builder), `_START_CLOSE` :286, `_end_priority` (new builder), `_END_PRIORITY` :337, `_END_PRIORITY_DEFAULT` :341, `_Node` :344, `_TreeBuilder` :353, `_child_elements` :389, `_descendant_text` :393, `_iter_elements` :411, `parse_bing_results` :420, `warmup_session` :7399, `_DDG_CHALLENGE_MARKERS` :7417, `_ddg_blocked` :7420, `search_ddg` :7461, `_bing_blocked` :7500, `search_bing` :7512, `run_web` (from `_run_ddg_with_bing_fallback` :8133, section 5 contract), `format_web_results` (from `format_results` :8032, no transport). `search_ddg` / `search_bing` gain `note` and `on_transport_error` (section 3).
- `_mcp_codesearch.py` — EVERY name, in this source order (from `search_github.py`): `_ext_to_lang` (new builder), `EXT_TO_LANG` :87, `detect_language` :101, `_SnippetParser` :114, `extract_code_from_snippet` :149, `build_github_url` :159, `parse_grep_results` :166, `warmup_code_session` (renamed from `warmup_session` :7122 — the websearch source owns that name, sources-disjoint), `_body_is_json` :134 (catch `(ValueError, ConnectionError)`, not `ChromeClientError` :7138 — a foreign-source name), `_grep_app_blocked` :7143, `search_github` :7166 (session passed in, no re-issue; gains `note` / `on_transport_error`), `_code_fence` (new, section 5 fence safety), `format_code_results` (from :7213, no transport, uses `_code_fence`).
- Space-indented, module docstring + block contract like `_mcp_websocket.py`; every literal/call one physical line; multi-line tables as builder functions (section 3); no `assert`, no module-level loop, no module-level subscript assignment.
- Add both names to `CANONICAL_NAMES` and `WHOLE_SOURCES`; mirror in `tests/test_generated_region.py` (constants :151-171, `WHOLE_SOURCES` :188, docstring :4-8). `tab_host` fixture imports (:339-372) add `import random`, `from urllib.parse import parse_qs, urlencode`, `from html.parser import HTMLParser` (`base64`, `json`, `os`, `re`, `time`, `urllib.parse`, `urlparse` are already there).
**Pattern to follow**: section 3 "Tab-safe tables = builder functions", "Search result contract".
**Verification**: `python3 -B Scripts/amalgamate.py --check` refuses no block as tab-unsafe. `generated_region` between Step 1 and Step 2: sources-disjoint passes and the unsafe set stays exactly `["_rows_note"]`, but `every-source-in-use` (`tests/test_generated_region.py:605-615`) is EXPECTED RED until Step 2 makes a host request the sources — the full suite is the Step 2 gate.

### Step 2: Regenerate into the CLIs; strip CDP, verified-first, sticky switch, label
**Files**: `Scripts/search_duckduckgo.py` (modify: :7373-7532, :7535-8204, :8207-8224), `Scripts/search_github.py` (modify: :87-185, :7097-7294), `Scripts/_mcp_websocket.py:17-21,182` (modify docstring)
**Dependencies**: Step 1
**Description**:
- Replace the lifted hand-written code with `# BEGIN GENERATED: _mcp_websearch.py :: ...` / `_mcp_codesearch.py` markers, run `python3 Scripts/amalgamate.py`.
- Hand-written per host: `_DECODERS`, `create_session()` (Chrome-only, section 3), `SEARCH_MAX_BYTES`, `ROTATE_EVERY`, the CLI `with_session(endpoint, fn)`, the CLI note renderer `_cli_note(event, query, detail)`, the CLI entry point (below), `main`.
- `_cli_note(event, query, detail)` renders today's exact stderr text, one line per event, `print(..., file=sys.stderr)`. The `*_error` events print `str(detail)` verbatim, exactly as today: `ddg_error` -> `"  [DDG error: {detail}]"` (`search_duckduckgo.py:7492`), `bing_error` -> `"  [Bing error: {detail}]"` (:7531), `grep_error` -> `"  [grep.app error: {detail}]"` (`search_github.py:7205`); the other events reproduce `search_github.py:7190` (`"  [grep.app body undecodable: {detail}]"`), :7195 (`"  [Rate limited for: {query}]"`), :7198 (`"  [HTTP {detail} for: {query}]"`) and the DDG/Bing undecodable / HTTP lines of `search_duckduckgo.py:7461-7532` verbatim. The structure-only rule (NFR-4) applies to the server's note, not to `_cli_note`.
- CLI `with_session(endpoint, fn)` semantics: pacing sleep and the `ROTATE_EVERY` count are PER QUERY, as today (`search_duckduckgo.py:8157-8171`): before every query but the first, sleep `random.uniform(2.5, 5.0)` (ddg) or `random.uniform(1.5, 3.0)` (bing / grep.app), and every `ROTATE_EVERY`-th query close + recreate + warm up the session. An endpoint change (the DDG -> Bing switch, today :8185-8192) closes the session and opens a new one + warm-up for the new endpoint with NO extra sleep and NO extra rotation count — the switched query is the same query, not a new one. A transport error (`on_transport_error` fired) closes and drops the session; the next query creates a new one.
- Kept hand-written CLI entry points, so test call targets survive: `search_duckduckgo.py` keeps `_run_ddg_with_bing_fallback(queries)` (called by `tests/test_mcp_chrome.py:7003` DdgRecorder and `:7554` F32 row), a thin wrapper that calls `run_web(queries, with_session, lambda i: _cli_note)` (`run_web` emits a `ddg_captcha` note at the switch, which `_cli_note` renders as today's `"  [DDG CAPTCHA on: {query} — switching to Bing fallback]"`, :8187), and returns `(output_sections, has_results)` built with `format_web_results` as today. `search_github.py` keeps `_run_github(queries)` (called by `tests/test_mcp_chrome.py:6417`, `:6628`, `:7540`), iterating `with_session("grep.app", ...)` per query with `note=_cli_note`, verified-first probe and re-issue removed.
- Imports: `search_duckduckgo.py` keeps `import base64` (`_decode_bing_url` needs it) when the websocket region goes; drop the "(cdp backend)" wording from the `hashlib` / `socket` import comments (:66, :71). `search_github.py` already imports what the codesearch blocks use (`os`, `json`, `random`, `time`, `urllib.parse`, `urlencode`, `HTMLParser`, :48-67); the free-name gate flags anything missed.
- Delete from `search_duckduckgo.py`: `_CHROME_AFTER_BLOCK` :7380, `_transport_for` :7395, `_discover_chrome` :7539, the websocket generated region (~:7575-7971), `CDPSearcher` :7974, `_run_cdp` :8055, `_bing_query` :8080, `_run_bing` :8104, the `DDG_BACKEND` dispatch in `main` :8224 and `_USAGE` :8207 mentions. From `search_github.py`: verified-first probe and re-issue in `_run_github` :7253.
- CLI exit codes: Bing / grep.app block -> exit 1 with one stderr line.
- `_mcp_websocket.py` docstring: drop `search_duckduckgo.py` as a host (:17-21 "Two hosts" paragraph, any later "both hosts" phrasing, e.g. :81, and "bought the search script" :182); the `_ws_sync_*` blocks stay (hostless, census INFO).
**Pattern to follow**: section 3 host session factory; ADR 0025 regions.
**Verification**: `amalgamate_check` silent; `generated_region` fully green (every-source-in-use now passes); `search_parsers` 18/18 unchanged; `py_deps` green (`_START_CLOSE` still top-level).

### Step 3: Fix the CLI-facing test suites
**Files**: `tests/test_mcp_chrome.py:6279-7717,7613-7640` (modify), `tests/test_mcp_websocket.py:6,39-40,72,78,877-904` (modify), `tests/run.py:441-446,490-536` (modify), `project-forge.yaml:200,208,224,240` (modify)
**Dependencies**: Step 2
**Description**:
- Group M: keep parser / predicate / policy / headers / R14 / rand / CLI-main rows and `GH_SWAPPED` (:6308, `create_session` stays). Delete: verified end-to-end, ladder re-issue, sticky switch, `create_session('chrome')`-site AST gates, `_run_cdp` gates, chrome-after-block hygiene. Rewrite ladder rows: DDG block -> Bing on chrome; Bing 403 -> blocked/exit 1; grep.app block -> blocked/exit 1; `create_session()` always `transport="chrome"`; `format_*_results` without transport. F32 rows (:7475-7546) assert `max_bytes == SEARCH_MAX_BYTES` for the single transport. The CLI-main helper pops and restores `DDG_BACKEND` (:7613-7640): remove that pop/restore and its docstring sentence.
- Kept policy rows that call the search functions directly and assert exactly one stderr line — `tests/test_mcp_chrome.py:6576-6583` (`host.search_github("q", s)`, prefix `"  [grep.app error: policy: ..."`) and `:7205-7212` (`host.search_ddg` / `host.search_bing`, prefix `"  [DDG error: ..."` / `"  [Bing error: ..."`) — must pass `note=host._cli_note` explicitly, because the block's default `note` is a no-op and would print nothing.
- `tests/test_mcp_chrome.py:6472` (`gh_profile_exchange`, host `search_github.py`) calls `host.warmup_session(s)` -> change to `host.warmup_code_session(s)` (Step 1 rename). The DDG-host call at `:7082` is unaffected.
- `test_mcp_websocket.py`: delete group E `search-cdp-backend` (:877-904), fix docstring/host lists (:6, :39-40, :72, :78); `tests/run.py` mcp_websocket 42 -> 41 and description.
- Recount group M; update `mcp_chrome` count 855 and description (:513-526); generated_region count (:441) if the new sources add cases.
- `project-forge.yaml`: "one of the nine" -> "eleven" (:200), remove `CDPSearcher` (:208), group M wording (:224), `:240` host description if it changes.
**Verification**: `forge_call test` for `mcp_chrome`, `mcp_websocket`, `generated_region` green with the new typed counts.

### Step 4: Write `Scripts/mcp-search.py` and its suite (red first)
**Files**: `tests/test_mcp_search.py` (create), `Scripts/mcp-search.py` (create), `tests/run.py:254` SUITES (modify), `project-forge.yaml` (add `mcp_search` target next to `:247`)
**Dependencies**: Step 2
**Description**:
- Write `tests/test_mcp_search.py` first (stdlib harness like the other suites, loads `mcp-search.py` via importlib, no network): dispatcher (no function -> status; unknown function exact one-line message; `f`/`p` forms); params (unknown refused, `query`/`q` aliases, collision `query`+`queries` refused, `queries` as str or list, bad `limit` -> isError); FR-6 isError with partial results (stub: query 1 ok, query 2 Bing `None`); DDG block switches the remaining queries to Bing; no results -> success; two threads on one endpoint never overlap inside a stub session; transport error (stub `get`/`post` raises `ConnectionError`) closes the endpoint session, the query shows `transport error`, and the next query creates a new one; truncation note at a small `max_answer_chars`; caps — one row each for 11 queries, a 513-char query, `limit` 0 and 51, a non-`str` query item, a non-`str` `lang`/`repo`/`path` -> `isError` with the fixed message; busy endpoint — the endpoint lock held by the test and the timeout patched small -> `isError` `endpoint busy, retry later`; fence — a snippet containing "```" renders inside a 4-backtick fence and cannot close it; fixed notices — a stub raising `ConnectionError("SECRET /path")` leaves neither `SECRET` nor `/path` in the tool output; log structure — a captured log handler sees endpoint, query index and `ConnectionError` but never the query text or the exception message; session kwargs — `_ch_session_new` swapped with a recorder, the first, rotated and post-transport-error sessions all carry `connect_policy=_ch_public_only_policy, max_bytes=SEARCH_MAX_BYTES, allow_downgrade=False, transport="chrome"`. Run red, then implement.
- Server, space-indented, from MCP_SKELETON: §1 header (`#!/usr/bin/env python3`, PEP 723 `dependencies = []`), §2 generated `_configure_logging`, §3 `McpServer` with `PROTOCOL_VERSION` first member, §3a envelope, §5a pool (`MAX_INFLIGHT_REQUESTS` region), §6 `main`, §7 `_resolve_aliases` hand copy + `ACCEPTED_*_PARAMS`, §8 generated regions: `_mcp_brotli.py`, `_mcp_zstd.py`, `_mcp_chrome.py` (whole), `_mcp_websearch.py`, `_mcp_codesearch.py`, `_mcp_paging.py :: DEFAULT_MAX_ANSWER_CHARS`, `_mcp_json.py :: _int_param` and `_mcp_json.py :: _json_error_window, _ensure_dict` (as `mcp-webfetch.py:7645`, `:8247` — generated, not hand copies), `_mcp_concurrency.py`, logging. Imports include `base64`, `json`, `os`, `random`, `re`, `threading`, `time`, `urllib.parse`, `from urllib.parse import parse_qs, urlencode, urlparse`, `from html.parser import HTMLParser`. Hand-written: `_DECODERS`, `_create_session()` (Chrome, `_ch_public_only_policy`, `allow_downgrade=False`, `max_bytes=SEARCH_MAX_BYTES`, `timeout=20`), the `_Endpoint` layer + `with_session` + `ENDPOINT_LOCK_TIMEOUT = 120` (section 5), the structure-only `note_for(i)` (NFR-4), the cap checks (FR-10), `handle_search_call`, `HANDLERS = {"web": ..., "code": ...}`, `SEARCH_CALL_TOOL` schema.
- Add `("mcp_search", run_mcp_search, "<description>", <exact count>)` to SUITES plus `run_mcp_search` like `tests/run.py:202`.
**Pattern to follow**: `mcp-webfetch.py` anchors in section 3; webfetch shutdown drain inside `run` (:8520).
**Verification**: `forge_call test mcp_search` green; `python3 Scripts/mcp-search.py` answers `initialize` / `tools/list`.

### Step 5: Fleet registration
**Files**: `Scripts/_mcp_smoke_test.py:95,357-368`, `tests/test_read_loop.py:190,231`, `tests/test_cancel.py:199,249`, `tests/test_wire_log.py:249,297`, `tests/test_handler_crash.py:194,245-246`, `tests/test_mcp_footprint.py:548`, `tests/run.py:544,555,564,574,609`, `project-forge.yaml:232,248`, `tests/test_mcp_chrome.py:6`, `tests/test_mcp_decoders.py:6` (all modify)
**Dependencies**: Step 4
**Description**:
- Smoke `SERVERS` row (`{"file": "mcp-search.py", "tool": "search_call", "args": [], "registered": ...}` as at :101); `ALIAS_COLLISION["mcp-search.py"] = ("web", "query", "queries")`.
- `test_read_loop.py` FLEET row (pool) + `DECLARED_POOL` 8 -> 9; `test_cancel.py` FLEET row reply-only + `DECLARED_REPLY_ONLY` 3 -> 4; `test_wire_log.py` WIRE row + `DECLARED_FULL` 13 -> 14 (or the class the server actually logs); `test_handler_crash.py` FLEET row + `DECLARED_W` / `DECLARED_D` per the server's catch-all sites; `test_mcp_footprint.py` — 24000 is already the COMPOSED class in `RATIFIED_CLASSES` (:548); add whatever per-server row the suite requires.
- `tests/run.py` typed counts: read_loop 49 (+1 per server), cancel 86 (+2), wire_log 55 (+2), handler_crash 60 (+1 server + its sites), protocol_version 74 (+3). Take the exact deltas from each suite's own count comment.
- If the server is not added to any client config, `name_existence` still parses the unknown-function line; confirm it is green.
- Host-list text: `project-forge.yaml:232` (decoders "generated into mcp-webfetch.py and both search scripts" -> add mcp-search.py), `:248` (read_loop "eight servers run handlers in a worker pool" -> nine); `tests/test_mcp_chrome.py:6` and `tests/test_mcp_decoders.py:6` docstrings (three hosts -> four, add mcp-search.py).
**Verification**: every listed suite green; `Scripts/_mcp_smoke_test.py` passes for mcp-search.

### Step 6: Documentation (implementation time; wiki ingest afterwards via p:wiki)
**Files**: `docs/adr/0026-speak-chrome-from-the-stdlib-verify-by-default.md` (addendum via addendum.py), `docs/adr/0023-the-websocket-client-is-a-sixth-domain.md` (addendum), `docs/concepts/spec-ddg.md` (§7.1-7.3, §7.5), `docs/components/generated-regions.md`, `docs/subsystems/scripts.md`, `docs/subsystems/tests.md`, `Scripts/MCP_SKELETON.md:12-15,759-777`
**Dependencies**: Steps 1-5
**Description**:
- ADR 0026 addendum: D15 reversed for the search scripts (Chrome always), label rule removed, D16/D17 consequences now Bing / error, alternative 13 moot, cost note. Amend spots per the explorer: :6, :86-90, :136-147, :149-174, :256-316, :720-724, :743-744, :760.
- ADR 0023 addendum: the DDG script no longer hosts the websocket client.
- generated-regions.md: sources table (eleven), host lists, census. scripts.md: search scripts section (explorer anchor :818-849) + new mcp-search section. tests.md: new suite and changed counts. MCP_SKELETON.md source lists.
- Roadmap item: certificate verification on the Chrome path for search endpoints.
**Verification**: `wiki_call freshness` shows no new gating anchor failures.

## 7. Critical Files

| File | Role | Action |
|---|---|---|
| `Scripts/_mcp_websearch.py` | DDG lite + Bing canonical source | create |
| `Scripts/_mcp_codesearch.py` | grep.app canonical source | create |
| `Scripts/mcp-search.py` | the MCP server | create |
| `tests/test_mcp_search.py` | server behaviour suite | create |
| `Scripts/amalgamate.py` | source registries | modify |
| `Scripts/search_duckduckgo.py` | web CLI host | modify (large deletions) |
| `Scripts/search_github.py` | code CLI host | modify |
| `Scripts/_mcp_websocket.py` | docstring host list | modify |
| `tests/test_generated_region.py` | registry mirrors, tab fixture | modify |
| `tests/test_mcp_chrome.py` | group M | modify |
| `tests/test_mcp_websocket.py` | drop CDP host case | modify |
| `tests/test_read_loop.py`, `test_cancel.py`, `test_wire_log.py`, `test_handler_crash.py`, `test_mcp_footprint.py` | fleet declarations | modify |
| `Scripts/_mcp_smoke_test.py` | smoke + alias probe | modify |
| `tests/run.py`, `project-forge.yaml` | counts, suite + target | modify |
| docs listed in Step 6 | rationale | modify |

## 8. Post-Implementation Checklist

- [ ] `python3 -B Scripts/amalgamate.py --check` silent; unsafe-block set still `["_rows_note"]`
- [ ] No search block references `_ch_session_new`, `_ch_public_only_policy`, `_DECODERS`, `ChromeClientError`, `log`
- [ ] `_ch_session_new` default still `transport="verified"`; every search `create_session` passes `transport="chrome"`
- [ ] No "Transport" label in any output; no `DDG_BACKEND` left in `Scripts/` or `tests/`; no `CDPSearcher`, `_CHROME_AFTER_BLOCK`, `_transport_for` left
- [ ] No module-level subscript assignment in either new source; every multi-line table is a builder function; `_START_CLOSE` top-level
- [ ] Each host marker lists every name of its WHOLE source in source order
- [ ] `_ChSession` touched only under its endpoint lock (120 s acquire timeout); DDG and Bing locks never nested; session dropped and recreated after a transport error
- [ ] Handler returns `{"error": ...}` for blocks, bad params, caps and busy endpoint (MCP_SKELETON §3a); partial results present in the error text
- [ ] Tool output carries only fixed per-query notices; no exception text, paths or tracebacks
- [ ] Code fences longer than any backtick run in the snippet
- [ ] Server logs via `log`, no `print` to stderr; wire log and `note` lines structure-only (no query text, bodies or exception messages)
- [ ] All typed counts in `tests/run.py` updated; new suite has a typed count
- [ ] `search_parsers` 18/18 unchanged; full `python3 tests/run.py` green
- [ ] Roadmap item filed for certificate verification on the Chrome path
