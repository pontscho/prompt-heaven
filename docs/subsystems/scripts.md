---
name: scripts
type: subsystem
status: active
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
verified:
  commit: 7459e17
  date: 2026-09-10
links:
  - overview
  - requirements-yaml
  - tests
  - generated-regions
  - 0001-purity-server-unification
  - 0004-never-pin-a-browser-impersonation-version
  - 0007-a-path-spelled-deny-protects-the-spelling
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
---

# Scripts & MCP Servers

`Scripts/` holds standalone Python 3.9+ scripts: the MCP servers, the canonical
sources their shared helpers are generated from `Scripts/amalgamate.py`, the
`requirements.yaml` task utilities, and the search tools. The servers share that
plumbing by generation rather than import; the five canonical sources, the
rules deciding what may be a shared block, and the copies deliberately left in
place are [[generated-regions]].

**Nothing is deployed.** `~/.claude/scripts` is a *symlink* to this directory,
so there is no copy step and no second tree to fall out of sync — an edit here
is live in the next session with no install. Like the webfetch registration
below, this is a host-level fact with no in-repo anchor, so it was measured
rather than read: on the symlink itself, and on the live process list, where
every registered server runs from its absolute path under this repo and none
from `~/.claude/scripts/`.

## MCP servers

Each server exposes its capability to Claude Code via a single dispatcher tool
routing to internal handlers by a `function` parameter — the pattern is best
seen in `Scripts/mcp-purity.py`. All use asyncio + JSON-RPC 2.0 over stdio.
The decision to fold `mcp-clangd` and `mcp-cuda` into `mcp-purity` behind the
`purity_call` entry point is recorded in [[0001-purity-server-unification]].

That shared read loop used to await the handler on the same line of control that
later awaits `sys.stdin.readline`, so one slow call made the server deaf to every
other request and read, from the caller's chair, as a connection that died and
needed a restart. **Every live server now dispatches each request as its own task,
with the stdin reader on an executor nothing else can take.** Do not copy the
locking from one server into another: the transport is uniform but the
concurrency decision is per-server, and four of them deliberately keep their
handlers as coroutines on one event loop because their id-counter safety depends
on it. The reasoning, the rejected alternatives, the per-server table and the six
pre-existing bugs the conversion exposed are in
[[0008-a-serialized-read-loop-looks-like-a-dead-server]].

The three unregistered servers below were converted too, even though they never
launch: they are the template others get copied from, which is how one broken read
loop became thirteen in the first place.

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
| `Scripts/mcp-jenkins.py` | mcp-jenkins | Jenkins CI: jobs, builds, console + stage logs, artifacts, queue, test reports |
| `Scripts/mcp-postgres.py` | mcp-postgres | PostgreSQL over the native v3 wire protocol — stdlib only, no libpq |
| `Scripts/mcp-gdc.py` | mcp-gdc | Chrome DevTools: navigation, DOM, network, screenshots, JS evaluation |
| `Scripts/mcp-tshark.py` | mcp-tshark | Packet capture and PCAP analysis via tshark |

Superseded, not registered — their capabilities were folded into `purity_call`,
which is the live route for all three: `mcp-clangd.py` (C/C++), `mcp-lua-lsp.py`
(Lua, via the `luals_*` functions) and `mcp-cuda.py` (CUDA). The tools
`clangd_call`, `luals_call` and `cuda_call` are not registered and cannot be
called `Scripts/_mcp_smoke_test.py`; see [[0001-purity-server-unification]].

### The error-envelope contract

**A handler failure must reach the caller with `isError` set.** It is the one
contract every server in the table above satisfies, and it is not per-server
taste: the defect it forbids is a reply the caller cannot tell from a success,
which no amount of well-written failure text repairs, because prose is not the
channel the flag is. The contract is written up as section 3a of
`Scripts/MCP_SKELETON.md`; the decision behind it — why the gate was written
first and run red, the four alternatives rejected on the way, and the blind spot
the gate keeps permanently — is frozen in
[[0010-a-handler-failure-must-reach-iserror]].

The predicate is tested at the **wrap**, on whatever the handler handed back,
and across fifteen servers it has only two shapes. Eleven test a top-level
`error` key on a structured result — `is_error = "error" in result` in
`Scripts/mcp-purity.py`, spelled `isinstance(result, dict) and "error" in
result` where a non-dict can arrive. Three test the **type of the text**,
because their dispatcher has already flattened the dict and cannot be
restructured: `isinstance(result, _ErrorText)` in `Scripts/mcp-clangd.py`,
`Scripts/mcp-cuda.py` and `Scripts/mcp-lua-lsp.py`. `mcp-jenkins.py` is the
same condition with a name on it `Scripts/mcp-jenkins.py:_is_error`. There is
no fourth shape; the illegal one this contract removed was returning a
pre-rendered failure **string** the wrap cannot distinguish from a success.

Four things about the predicate are decisions, not details:

- **Raising and returning are two routes to one flag — but only nine servers
  route them through the predicate.** In the nine that carry `_handle_tool_call`
  the wrap's own `except Exception` assigns the same `{"error": …}` dict a
  handler would have returned `Scripts/mcp-webfetch.py:McpServer`, so both routes
  meet at the predicate above. The six `_dispatch_tool` servers return
  `self._tool_error(...)` straight out of the except block
  `Scripts/mcp-clangd.py:McpServer` — **skipping** the predicate rather than
  failing it. Section 3a of `Scripts/MCP_SKELETON.md` asserted the first shape of
  all fifteen until `3949317` corrected it, and the sentence survived being false
  for six servers only because both routes land on the same `isError` envelope
  anyway. That is the part that is not optional: not which route, but that one of
  them happens.
- **It reads the TOP level only.** A nested `error` is often real data copied
  out of an upstream JSON — Jenkins' per-stage `wfapi` records are the worked
  example — and a recursive search would flag data as failure, which is this
  same defect wearing the opposite sign `Scripts/mcp-jenkins.py:_is_error`.
- **Where two behaviours describe one condition, they read one predicate.**
  Jenkins' bug was that rendering keyed on the payload's `error` key while the
  flag keyed on a separate `__is_error__` sentinel only its `_err` helper set,
  so 25 handler sites rendered *as* errors and reported `isError: False`. One
  predicate read by both the renderer and the transport makes that shape
  impossible; converting the 25 sites would only have made it *currently*
  absent.
- **An action that did not happen is a failure; a question answered with
  "none" is a success.** This is what stops the sign inverting on the rebound.
  `lldb_list_sessions` reporting no active sessions, `gdc_status` reporting
  Chrome unreachable, context7 reporting that no library matched — all
  successes. They were asked, and they answered.

`_ErrorText` is a `str` subclass carrying the verdict a flattened reply would
otherwise lose `Scripts/mcp-clangd.py:_ErrorText`. It exists as three
byte-identical hand copies and is **declared** in `Scripts/MCP_SKELETON.md`
rather than homed as a canonical block, even though it qualifies as one:
blessing a second mechanism as generated infrastructure — in three servers that
are never launched — would buy drift protection *for* the divergence instead of
removing it. Record a divergence as a divergence.
Everything above is about what the wrap **reports**. What it **records** is a
second invariant with its own gate, and the two are not the same channel: the
reply carries the exception's type and text to the caller, so the one artefact
that exists nowhere else is the traceback. Six of the fifteen logged that
catch-all at `log.debug` with the traceback discarded, under a default level of
`WARNING` `Scripts/_mcp_logging.py:_configure_logging`, so a crashed handler was
recorded nowhere at all — the same six `_dispatch_tool` servers that skip the
predicate above, which is a coincidence of shape rather than a common cause.
`3949317` converged them on `log.exception` and
`tests/test_handler_crash.py` now gates both site layers per server. Raising the
level means a traceback is written by default, which looks like it should
collide with ADR 0011's structure-only rule and does not: that rule binds the two
wire sites and names handler logging as a declared blind spot, and the nine
correct servers were already emitting tracebacks at ERROR from this exact site
when that page swept the fleet. One clause travelled anyway — the format string
must be a literal, because the one log guaranteed to be written is the worst
place to interpolate a payload [[0011-a-truncated-payload-carries-the-first-cookie]].


#### The gate, and the layer below it that the gate does not reach

The contract is enforced by check 7 of the smoke harness,
`Scripts/_mcp_smoke_test.py:error_envelope_checks`, which drives all fifteen
servers over live JSON-RPC. It has two halves and both are load-bearing: a
positive probe (`function="__no_such_function__"` must come back `isError:
True`, asserted on the **flag** and never the text, since several servers answer
by listing their whole catalogue) and a negative control (omitting `function`
entirely, which most servers answer with a status reply by design, must stay
unflagged — without it, a server that flagged *everything* would pass). It
ignores the `registered` flag on purpose: the three unregistered servers start
offline in milliseconds, so they are driven rather than excused.

**Its limit is stated beside it, because an unstated scope is the same defect as
the false invariant it replaced:** *"it lands on ONE failure site per server, so
it proves the wrap and not the sites"* `Scripts/MCP_SKELETON.md`. Jenkins passes
the gate without exercising any of the 25 sites its predicate fixed, because the
unknown-function path was always an error.

`mcp-gdc.py` is the first case of that gap being found real rather than
hypothetical. Injected page JS reported "element not found" as a bare prose
**string**, which four handlers passed straight back as a successful reply — a
wrap-conformant server failing at the call sites below the wrap. Failure is now
reported by **shape**: a `{gdcError: …}` object recognised by
`Scripts/mcp-gdc.py:_js_failure`, which folds in two further faults that arrived
on the same reply — a thrown JS exception (Chrome sends no `value` key at all,
so a defaulted `.get("value", "")` turned it into an empty string inside a
success envelope: no flag *and* no text) and an absent value where these
snippets always return a string. The shape test is not stylistic: a prefix test
on the string is **forgeable by the page**, because `select_option` answers
`'Selected: ' + opt.value` and `opt.value` is page-authored, so an
`<option value="Element not found: #x">` makes a successful call read exactly
like a failed one. A page cannot make `opt.value` a Python dict.

That second layer is **not** gateable by check 7 — both of its probes return
before `_resolve_session` `Scripts/mcp-gdc.py:_resolve_session`, so neither ever
reaches a page — and the evidence for it is a recorded live-Chrome probe rather
than a suite case. Treat the gate as proof that each server's wrap is wired, and
the call sites beneath it as unproven until measured.

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

### webfetch's reply shape: `raw`, `show_headers`, `save_to`

The default reply is a markdown report — url, status, a selected header block, a
paged body. Three parameters reshape it and a fourth guards one of them. All four
are presentation decisions, so they are resolved once in `handle_fetch` and handed
to the formatter as a single `view` dict rather than re-derived at each of its
three call sites — a cached entry, a revalidated one, a fresh response
`Scripts/mcp-webfetch.py:_format_response`.

`raw=true` drops the envelope: the reply becomes the body alone. It also flips the
*default* conversion to `html` — unprocessed — which an explicit `output` still
overrides, because raw is about the wrapper, not the conversion
`Scripts/mcp-webfetch.py:handle_fetch`. What it does **not** drop is the line-based
ceiling or the trailing `[showing rows …; offset=N]` note: both are emitted by the
pager that the report and the raw reply now share
`Scripts/mcp-webfetch.py:_paged_reply`, because a silently severed body is
indistinguishable from a complete one, and an uncapped 5 MB document would cost
more context than a whole session's tool descriptions.

`show_headers=true` widens the header block from the seven
`Scripts/mcp-webfetch.py:_NOTABLE_HEADERS` to every header the response carried;
`response_headers` is an alias `Scripts/mcp-webfetch.py:PARAM_ALIASES`. The
load-bearing detail is that `headers` is now **polymorphic**, and deliberately so:
the obvious spelling for "show me the response headers" was already taken by the
request-header dict, so a **bool** now means the former while a **dict** still
means the latter `Scripts/mcp-webfetch.py:handle_fetch`. The two cannot collide,
because nobody expresses a request-header dict by writing `true`. Combined with
`raw` the reply is wire-shaped, like `curl -i` — status line, headers, blank line,
body.

`save_to=PATH` writes the WHOLE converted document to disk, never the paged
window, byte-exact with no trailing newline appended
`Scripts/mcp-webfetch.py:_save_body` — a saved artefact that stopped at
`max_answer_chars` would carry nothing to say its tail is missing. Containment is
the part that matters: the path is model-authored while the process runs as the
developer, which is the SSRF guard's confused-deputy reasoning pointed at the disk
instead of the network `Scripts/mcp-webfetch.py:_resolve_save_path`. It judges the
**resolved** target rather than the spelling, so a writable in-tree symlink cannot
smuggle an out-of-tree write past an in-tree-looking path — and collapsing `..`
textually with `normpath` would have been unsound here, because `a/..` is not the
parent of `a` when `a` is a link. That is the second surface in this repo to reach
the same conclusion: [[0007-a-path-spelled-deny-protects-the-spelling]] froze it for
the sandbox profile's write denies, where the file to protect and the name used to
reach it had diverged. Here it governs a write DESTINATION rather than a deny rule —
same principle, opposite direction. The path is resolved twice on purpose: once
before the fetch, so a destination outside the tree costs no round trip and does
not read as a network failure, and again at write time. A non-2xx status and an
empty body are both refused rather than written, since an error page where a
document was expected is worse than no file at all. `overwrite=true` is required to
replace an existing file, enforced by an exclusive `open(..., "x")` rather than an
`exists()` check — the same guard without the window between looking and writing.

`--test` now exits **1** on a refusal, where it used to print the error and exit 0
`Scripts/mcp-webfetch.py:main`. `save_to` is what made that load-bearing: a shell
caller whose write was refused saw a success exit and went on to read a file that
was never written.

**Measured** over nine cases through the `--test` CLI (the evidence is a CLI run,
not a suite — this server has none): all nine passed, including the containment
case, where a save to `.claude/tmp/webfetch-verify/outlink/escaped.md` was refused
because it resolved to `/private/tmp/escaped.md`. Nothing was written outside the
project root.

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
`Scripts/mcp-purity.py:PARAM_ALIASES_BY_FUNC`. The same table carries the opposite
case: `pattern` is global (`-> substring_pattern`), but `list_dir` has no such
parameter, so there the global row could only ever answer a name filter with the
accepted-name dump — the per-function row aims it at `filter` instead, the fnmatch
name match that `pattern` already means in `find_file`. A global alias is only
global when every handler can honour it; the two escapes are *owned elsewhere* and
*does not exist here*. The contract is pinned by the
`purity_file_ops` suite (39 cases, no external binary, ~3 s)
`tests/test_purity_file_ops.py` and restated model-facing in
`ClaudeCode/skills/mcp-purity/SKILL.md`.

**A name a tool prints in its answers is a name it has to accept in its asks.**
mcp-wiki reached the same table from a third direction: `get_page` refused `path`
as an unknown param while `_fn_get_page` already matched `relpath == slug`
`Scripts/mcp-wiki.py:_fn_get_page`, so a docs-relative path was always a legal
*value* of `slug` and only its key was being turned away — and both ways in taught
that key, a `search` hit rendering `subsystems/scripts.md#mcp-servers` and the
page's own frontmatter header rendering `- **path**: subsystems/scripts.md`
`Scripts/mcp-wiki.py:_render_frontmatter`. A spelling the answers hand out is not
a caller error, it is a reflex the server trained, which is why it is fixed with
an alias rather than with a doc telling the caller to read more carefully — the
skill had been describing `get_page` as "by slug/path" all along
`ClaudeCode/skills/wiki/SKILL.md`. The row stays per-function for a reason the
purity cases do not cover. `source_to_pages` does own `path` as its own spelling
of `source`, but per-function aliases resolve first, so it was never at risk; what
a global row would break is the *diagnostics*, telling a caller who typed `path`
on a `freshness` call `Unknown params for 'freshness': slug` — a word they never
sent. So the escape list gains a third entry beside *owned elsewhere* and *does
not exist here*: **renames the caller's word in another handler's rejection**. The
function-name half is the same shape one level up — the registry names the
handler, the caller reaches for the verb — so `read` joins the `page` and `get`
rows aimed at `get_page`. The two halves compose only because the dispatcher
canonicalizes the function *before* it resolves per-function params
`Scripts/mcp-wiki.py:handle_wiki_call`: resolved against the raw `read`, `path`
would miss `get_page`'s row and be rejected on a call whose every half was right.

The same word came back a second time from the *filter* side, and settled the
scope question by construction instead of by argument. `search` and `list` were
refusing `path` while filtering on `relpath.startswith(prefix)`
`Scripts/mcp-wiki.py:_fn_search`, so a whole docs-relative path was a legal
*value* of path_prefix there too — it selects the single page it names — and the
trainer is the same one: a hit line prints a path, and no answer the server
renders ever utters path_prefix. `path` therefore reaches **three** canonical
names across four rows (`slug`, `source`, path_prefix), which is the global row's
epitaph: one table cannot spell one word two ways, so the row that was declined
on diagnostics grounds would by now be declined on arithmetic. The case pins each
spelling against the UNFILTERED answer rather than only against path_prefix's —
two spellings that agree prove the alias *resolved*, and only a narrower answer
proves it reached the filter. All of it is pinned by `wiki_recall` group N
(115 cases) `tests/test_wiki_recall.py`.

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
