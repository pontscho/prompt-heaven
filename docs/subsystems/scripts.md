---
name: scripts
type: subsystem
status: active
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
verified:
  commit: 095db60
  date: 2026-08-10
links:
  - overview
  - requirements-yaml
  - 0001-purity-server-unification
  - 0004-never-pin-a-browser-impersonation-version
---

# Scripts & MCP Servers

`Scripts/` holds standalone Python 3.9+ scripts in three groups. They are
deployed to `~/.claude/scripts/` or run directly.

## MCP servers

Each server exposes its capability to Claude Code via a single dispatcher tool
routing to internal handlers by a `function` parameter — the pattern is best
seen in `Scripts/mcp-purity.py`. All use asyncio + JSON-RPC 2.0 over stdio.
The decision to fold `mcp-clangd` and `mcp-cuda` into `mcp-purity` behind the
`purity_call` entry point is recorded in [[0001-purity-server-unification]].

| Script | Server | Domain |
|--------|--------|--------|
| `Scripts/mcp-purity.py` | mcp-purity | File ops: list, search, read, write |
| `Scripts/mcp-forge.py` | mcp-forge | `project-forge.yaml` build targets |
| `Scripts/mcp-git.py` | mcp-git | Git operations |
| `Scripts/mcp-lldb.py` | mcp-lldb | LLDB debugger integration |
| `Scripts/mcp-context7.py` | mcp-context7 | Context7 documentation lookup |
| `Scripts/mcp-wiki.py` | mcp-wiki | Wiki freshness / reindex / search / page reads over `docs/` |
| `Scripts/mcp-inspect.py` | mcp-inspect | Read-only host/process/network inspection, file digests, syntax validation |
| `Scripts/mcp-webfetch.py` | mcp-webfetch | Browser-emulated URL fetching with HTML→Markdown extraction, disk cache |

Superseded, not registered — their capabilities were folded into `purity_call`,
which is the live route for all three: `mcp-clangd.py` (C/C++), `mcp-lua-lsp.py`
(Lua, via the `luals_*` functions) and `mcp-cuda.py` (CUDA). The tools
`clangd_call`, `luals_call` and `cuda_call` are not registered and cannot be
called `Scripts/_mcp_smoke_test.py`; see [[0001-purity-server-unification]].

`mcp-webfetch.py` is registered at **user scope**, so it is live in every project
rather than only this one. That registration is the one claim on this page with
no in-repo anchor: it lives in `~/.claude.json`, outside the tree and mode 0600.
The recorded launch line is
`uv run --script Scripts/mcp-webfetch.py --project-root ~/.claude`.

The server was near-totally rewritten on 2026-08-04 and registered immediately
after. It carries browser impersonation by bare alias only — never a pinned
version, and the two backends are validated asymmetrically on purpose, frozen in
[[0004-never-pin-a-browser-impersonation-version]] —
`Scripts/mcp-webfetch.py:_create_session`, with a retry ladder that escalates by
browser *engine* on 403/429/503, a content-type gate that refuses non-textual
bodies instead of mojibaking them, main-content extraction
(nav/header/footer/aside decomposed, `main`/`article`/`[role=main]` preferred,
`markdown_full` to opt out) `Scripts/mcp-webfetch.py:_main_content`, line-based
paging on the fleet's shared `_rows_note` wording, ETag/If-None-Match
revalidation that refreshes a cache entry in place on a 304, and an SSRF guard
refusing loopback/private/link-local unless `allow_private=true`
`Scripts/mcp-webfetch.py:_check_host_allowed`. The fetch runs in an executor
thread behind a stdout write lock, because one fetch can hold the full 30s
timeout `Scripts/mcp-webfetch.py:McpServer`.

Two things about that launch line are deliberate, not incidental:

- **`uv run --script` is mandatory, not stylistic.** The PEP-723 block is the only
  place `beautifulsoup4` is declared and a bare `python3` lacking it dies at
  import `Scripts/mcp-webfetch.py`. The smoke harness now starts it the same way
  the registration does, through a per-server `launcher` argv prefix
  `Scripts/_mcp_smoke_test.py:launch_prefix` — so what the test measures is what
  actually runs, and this server's smoke went from SKIP to a full pass. The
  fallback is deliberate rather than absent: a `launcher` whose binary is not on
  `PATH` degrades to the interpreter, reproducing the old SKIP with a stderr tail
  instead of killing the run on a missing `uv`.
- **`--project-root` is pinned to `~/.claude` rather than left at its cwd
  default** `Scripts/mcp-webfetch.py`, which puts the cache at
  `~/.claude/.cache/webfetch/`. A user-scope server starts in every project, so
  the default would scatter a `.cache/webfetch/` into every repo it was launched
  from — and only this repo ignores that path `.gitignore`. A fetch cache is also
  keyed by URL, not by project, so one shared tree makes a second project's fetch
  of the same page a cache hit instead of a duplicate download.

Other servers: `mcp-tshark.py`, `mcp-jenkins.py`, `mcp-gdc.py`,
`mcp-postgres.py`.

### What `purity_call`'s ignore filter will and will not hide

`search_for_pattern` and `list_dir` both drop gitignored entries, and both exempt
one subtree: the fleet's scratch area `.claude/tmp`
`Scripts/mcp-purity.py:IGNORE_EXEMPT_PATHS`. It is gitignored on purpose — it must
never be committed — but it is also where every minion drops the artifacts the
next search goes looking for, so honouring the ignore rule there hides files the
caller wrote seconds earlier, and an empty result that should have had hits costs
far more to diagnose than the skipped scan ever saved. The boundary that moved is
"would git commit this", **not** the sandbox: path containment is untouched.

The exemption cannot be a single membership test, because the walk *prunes*. An
ignored `.claude` would stop `os.walk` before it ever reached `.claude/tmp`, so
the exemption must un-prune every ancestor of an exempt path
`Scripts/mcp-purity.py:_ignore_exempt` — and that alone would hand out the
un-pruned ancestor's *other* children, surfacing `.claude/agents/**` merely
because `.claude/tmp` is exempt. `Scripts/mcp-purity.py:_ignore_inherited` narrows
it back by asking whether any ancestor is itself ignored, restoring what pruning
used to deliver implicitly. All six call sites answer through one predicate
`Scripts/mcp-purity.py:_ignore_skips`.

**Measured — and it is why the exemption looks like a no-op in this repo.** The
matcher is handed a bare basename at the walk-prune sites
`Scripts/mcp-purity.py:_is_ignored`, so a slash-bearing `.gitignore` pattern is
inert *there* — and this repo's own pattern is the path-shaped `.claude/tmp`
`.gitignore`. The un-pruning half of the exemption therefore changes nothing here,
while being load-bearing in a repo whose ignore file carries a bare `tmp` or
`.claude` line. What the exemption does change here was measured on a recursive
`list_dir`: 569 → 622 rows, all 53 new rows inside `.claude/tmp`, nothing outside
it newly visible and nothing newly hidden — the contract is a boundary, not a row
count. The suite records that basename asymmetry as an explicit limitation rather
than a guarantee, carrying one pattern of each shape in a single fixture
`tests/test_purity_file_ops.py`.

`search_for_pattern` also accepts two ripgrep-style flags it does not need.
`regex` and `line_numbers` are no-ops when **true** — the pattern is always
regex-compiled and `content` rows always carry `path:line:` — and a hard error
when **false** `Scripts/mcp-purity.py:handle_search_for_pattern`. Tolerating the
true polarity spares a round trip on a call that asked for what it was already
getting; rejecting the false one is the whole point, because silently ignoring it
is how a pattern meant literally quietly becomes a regex. `query` is an alias for
`substring_pattern` per-function only, never in the global table, because `symbol`
owns `query` as its own canonical parameter
`Scripts/mcp-purity.py:PARAM_ALIASES_BY_FUNC`. The contract is pinned by the
`purity_file_ops` suite (31 cases, no external binary, ~2 s)
`tests/test_purity_file_ops.py` and restated model-facing in
`ClaudeCode/skills/mcp-purity/SKILL.md`.

## Task utilities

Operate on the `requirements.yaml` workflow (see [[overview]]):
`Scripts/task-plan.py` (status + dependency analysis), `task-update.py`,
`task-show-all.py`, `task-show-details.py`, `task-batch-planner.py`,
`task-implementation-plan.py` (token-efficient plan extraction), and
`task-validator.py` (requirements.yaml schema validation). What the file they all
operate on is *for*, and why the plan-to-implement handoff needs a validated task
graph beside the prose plan, is [[requirements-yaml]].

## Search

`Scripts/search_duckduckgo.py` (DDG-first with Bing fallback) and
`Scripts/search_github.py` (code search via grep.app). Both impersonate a browser,
picking the backend by platform: primp with `impersonate_os="linux"` on Linux,
curl_cffi elsewhere `Scripts/search_duckduckgo.py:create_session`. Since
2026-08-04 the primp side accepts **only bare aliases**
`Scripts/search_duckduckgo.py:PRIMP_ALIASES` — a pinned major rots silently there
into a random browser — while the curl_cffi side keeps its pinned list
`Scripts/search_duckduckgo.py:CURL_CFFI_PROFILES`, which rots loudly with an
`ImpersonateError`. The DDG bot-detection research log is documented in
[[spec-ddg]].
