---
name: scripts
type: subsystem
status: active
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
  - Scripts/context-guard.sh
verified:
  commit: 19c3fc5
  date: 2026-10-08
links:
  - overview
  - mcp-proxy
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0027-the-proxy-relays-it-never-composes
  - llm-router
  - 0028-route-by-model-translate-at-the-edge
  - requirements-yaml
  - tests
  - generated-regions
  - spec-ddg
  - chrome-profile-refresh
  - 0001-purity-server-unification
  - 0004-never-pin-a-browser-impersonation-version
  - 0007-a-path-spelled-deny-protects-the-spelling
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0015-ambiguity-is-the-defect
  - 0017-a-silent-zero-is-the-defect
  - 0018-the-totals-must-describe-the-scope
  - 0021-contain-by-the-admitted-root
  - 0024-pure-python-39-and-the-stdlib
  - 0026-speak-chrome-from-the-stdlib-verify-by-default
  - 0029-the-http-front-is-a-domain
---

# Scripts & MCP Servers

`Scripts/` holds standalone scripts, Python 3.9+ unless named otherwise: the MCP servers, the canonical
sources their shared helpers are generated from `Scripts/amalgamate.py`, the
`requirements.yaml` task utilities, the search tools, one HTTP server that is
not an MCP server, the LLM router ("LLM router" below), and the shell scripts
behind a restartable session ("Restartable sessions" below). The servers share that
plumbing by generation rather than import; the canonical sources, the
rules deciding what may be a shared block, and the copies deliberately left in
place are [[generated-regions]].

**Nothing is deployed.** `~/.claude/scripts` is a *symlink* to this directory,
so there is no copy step and no second tree to fall out of sync — an edit here
is live in the next session with no install. Like the webfetch registration
below, this is a host-level fact with no in-repo anchor, so it was measured
rather than read: on the symlink itself, and on the live process list, where
every registered server runs from its absolute path under this repo and none
from `~/.claude/scripts/`.

## Dependencies — Python 3.9 and the stdlib

**Every script here runs on a bare Python 3.9 interpreter with the standard
library.** A third-party module is allowed only where the stdlib has no way to
do the job, and only from an allowlist: `yaml` (no stdlib YAML parser) and
`tomli` (the 3.9/3.10 backport of `tomllib`, which is stdlib only from 3.11 and
is guarded the same way). The decision, why each entry is unavoidable and the
alternatives rejected are [[0024-pure-python-39-and-the-stdlib]].

Absence is detected with `importlib.util.find_spec("<name>")` **before** the
import, never with `except ImportError` — `find_spec` does not import, so an
installed-but-broken package still fails with its own traceback instead of being
reported as missing. A required dependency that is absent produces one stderr
line naming the package and a `pip install` for the running interpreter, and
exit 2: `Scripts/task-validator.py` for PyYAML. An optional one degrades as it
always did: `Scripts/mcp-inspect.py:_v_yaml` / `_v_toml` answer LIMITED / SKIP.
The two search scripts no longer have a package to check: their HTTP client is
generated into them (below).

A **system shared library** loaded through `ctypes` is a separate dependency
category, not an allowlist entry: `libbrotlidec` and `libzstd`, the only way to
decode `br` and `zstd` bodies without a third-party package, registered by name
with their reason in `tests/test_py_deps.py:SYSTEM_LIBS`. Absence is not an exit:
the decoder raises `LookupError` on first use and the caller reports one decode
error line `Scripts/_mcp_brotli.py`.

`Scripts/mcp-webfetch.py` is the one **declared, temporary exception**: it keeps
`beautifulsoup4` + `markdownify` and its `uv run --script` launch (below), and
`lxml` as bs4's tree builder. lxml is kept on purpose: in a measured comparison
`html.parser`, which has no implied end tags, collapsed lists and tables
`Scripts/mcp-webfetch.py:_soup`. The PEP 723 block bounds each of the three to
its current major version, and `tests/test_py_deps.py:group_webfetch_deps`
gates the bounds and the declared builder. No other file carries a PEP 723
dependency or a `uv` launch.

The rule is gated by `tests/test_py_deps.py` over `Scripts/`, `ClaudeCode/` and
`tests/`: every non-stdlib import allowlisted and `find_spec`-guarded, no stdlib
module removed after 3.9, and every file parsing as 3.9 **syntax** — which does
not see 3.10+ stdlib API use; see [[tests]].

## MCP servers

Each server exposes its capability to Claude Code via a single dispatcher tool
routing to internal handlers by a `function` parameter — the pattern is best
seen in `Scripts/mcp-purity.py`. All use asyncio + JSON-RPC 2.0 over stdio.
`Scripts/mcp-proxy.py` (below) is the exception to both: it has no dispatcher
tool of its own but relays its children's tools under their own names
`Scripts/mcp-proxy.py:_handle_tool_call`, and with `--http` it serves Streamable
HTTP instead of stdio `Scripts/mcp-proxy.py:serve_http`.
The decision to fold `mcp-clangd` and `mcp-cuda` into `mcp-purity` behind the
`purity_call` entry point is recorded in [[0001-purity-server-unification]].

That shared read loop used to await the handler on the same line of control that
later awaits `sys.stdin.readline`, so one slow call made the server deaf to every
other request and read, from the caller's chair, as a connection that died and
needed a restart. **Every live server now dispatches each request as its own task,
with the stdin reader on an executor nothing else can take.** Do not copy the
locking from one server into another: the transport is uniform but the
concurrency decision is per-server, and some of them deliberately keep their
handlers as coroutines on one event loop because their id-counter safety depends
on it. The reasoning, the rejected alternatives, the per-server table and the six
pre-existing bugs the conversion exposed are in
[[0008-a-serialized-read-loop-looks-like-a-dead-server]].

**Every server also honours `notifications/cancelled`.** The read loop keeps an
id-to-task registry and cancels the named task before dispatching anything else,
and the cancelled request is never answered. What the cancel *reclaims* depends
on the server's class. For gdc, lldb and proxy (`task`), the coroutine stops;
the proxy's child client also forwards `notifications/cancelled` with the child's
own request id, from the cancel arm of `ChildClient.rpc` `Scripts/mcp-proxy.py:rpc`. For forge,
tshark, git, inspect and wiki (`kill`), the request's child process group is
signalled `Scripts/mcp-forge.py:_Reclaim`, except for declared exemptions such as
wiki's vendored `Scripts/mcp-wiki.py:git` and mcp-git's mutating stash
`Scripts/mcp-git.py:_run_git_mutating`, which is never killed mid-way and only
has its reply suppressed. For the LSP servers (`lsp-cancel`), the generated
`Scripts/_mcp_lsp.py:_request` sends `$/cancelRequest`. For postgres
(`pg-cancel`), a `CancelRequest` carrying the captured `BackendKeyData` goes out
on a second socket and the worker drains the error through `ReadyForQuery`
before releasing the connection `Scripts/mcp-postgres.py:send_cancel_request`.
For context7, jenkins, webfetch and search (`reply-only`), the reply is suppressed
and the worker thread runs to its own timeout — for search, a call waiting on or
holding an endpoint lock runs to its end. The addendum to [[0008-a-serialized-read-loop-looks-like-a-dead-server]]
records the decision, and `tests/test_cancel.py` gates it ([[tests]]).

The unregistered servers below were converted too, even though they never
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
| `Scripts/mcp-webfetch.py` | mcp-webfetch | URL fetching (verified TLS by default, Chrome emulation opt-in) with HTML→Markdown extraction, disk cache |
| `Scripts/mcp-jenkins.py` | mcp-jenkins | Jenkins CI: jobs, builds, console + stage logs, artifacts, queue, test reports |
| `Scripts/mcp-postgres.py` | mcp-postgres | PostgreSQL over the native v3 wire protocol — stdlib only, no libpq |
| `Scripts/mcp-gdc.py` | mcp-gdc | Chrome DevTools: navigation, DOM, network, screenshots, JS evaluation |
| `Scripts/mcp-tshark.py` | mcp-tshark | Packet capture and PCAP analysis via tshark |

One more live server sits outside that table on purpose. `Scripts/mcp-proxy.py`
is not registered by design: it is the one endpoint ai-soul connects to, one
instance per project root, relaying its children's tools under their own names
— a duplicate name refuses startup — over stdio or, with `--http`, Streamable
HTTP `Scripts/mcp-proxy.py:serve_http`. Its handlers are coroutines on the read
loop's event loop, because each child client keeps its ids, pending futures and
progress routes on the loop thread `Scripts/mcp-proxy.py:ChildClient`; the smoke
check runs it as an unregistered server against a stub child
`Scripts/_mcp_smoke_test.py`. Its shape — eager start, verbatim payloads under a
framing ceiling, the restart budget, the forwarded cancel — the HTTP front's
access rules, how to run it for ai-soul, how it is tested and its declared
limits are [[mcp-proxy]]; why it relays and never composes is
[[0027-the-proxy-relays-it-never-composes]]. Its HTTP front's identical pieces
are generated from `Scripts/_mcp_httpfront.py` into it and into the LLM router's
`Scripts/llm-router.py:_RouterHttpServer`, each keeping declared adaptations
([[0029-the-http-front-is-a-domain]]; see "LLM router" below).

`Scripts/mcp-search.py` (`search_call`: web search over DDG lite and Bing, code
search over grep.app) is the other live server outside the table: it is **not
registered in a client config yet**, and the smoke check runs it as unregistered
`Scripts/_mcp_smoke_test.py`. It is the two search CLIs as one server, with a
worker pool and one locked session per endpoint; see "mcp-search" under Search
below.

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
and across the fleet it has only two shapes. Most servers test a top-level
`error` key on a structured result — `is_error = "error" in result` in
`Scripts/mcp-purity.py`, spelled `isinstance(result, dict) and "error" in
result` where a non-dict can arrive. Others test the **type of the text**,
because their dispatcher has already flattened the dict and cannot be
restructured: `isinstance(result, _ErrorText)` in `Scripts/mcp-clangd.py`,
`Scripts/mcp-cuda.py` and `Scripts/mcp-lua-lsp.py`. `mcp-jenkins.py` is the
same condition with a name on it `Scripts/mcp-jenkins.py:_is_error`. There is
no other shape; the illegal one this contract removed was returning a
pre-rendered failure **string** the wrap cannot distinguish from a success.

Four things about the predicate are decisions, not details:

- **Raising and returning are two routes to one flag — but only some servers
  route them through the predicate.** In those that carry `_handle_tool_call`
  the wrap's own `except Exception` assigns the same `{"error": …}` dict a
  handler would have returned `Scripts/mcp-webfetch.py:McpServer`, so both routes
  meet at the predicate above. The `_dispatch_tool` servers return
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
otherwise lose `Scripts/mcp-clangd.py:_ErrorText`. It exists as
byte-identical hand copies in `mcp-clangd.py`, `mcp-cuda.py` and
`mcp-lua-lsp.py`, and is **declared** in `Scripts/MCP_SKELETON.md`
rather than homed as a canonical block, even though it qualifies as one:
blessing a second mechanism as generated infrastructure — in servers that
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
`Scripts/_mcp_smoke_test.py:error_envelope_checks`, which drives every server
over live JSON-RPC. It has two halves and both are load-bearing: a
positive probe (`function="__no_such_function__"` must come back `isError:
True`, asserted on the **flag** and never the text, since several servers answer
by listing their whole catalogue) and a negative control (omitting `function`
entirely, which most servers answer with a status reply by design, must stay
unflagged — without it, a server that flagged *everything* would pass). It
ignores the `registered` flag on purpose: the unregistered servers start
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
`uv run --script Scripts/mcp-webfetch.py --project-root . --cache-root /Users/zoltan.ponekker/.cache/web-fetch`.

The server was near-totally rewritten on 2026-08-04 and registered immediately
after. Its HTTP client is the stdlib one generated from `Scripts/_mcp_chrome.py`
(see Search, below), and the transport is the caller's choice, never an
escalation: the default is the **verified** transport (certificates and host
names checked, Chrome 154's headers over Python's own TLS, HTTP/1.1), and
`profile="chrome"` opts into the Chrome 154 fingerprint, whose certificate is
NOT verified `Scripts/mcp-webfetch.py:_create_session`. A 403/429/503 is retried
on a fresh session over the **same** transport `Scripts/mcp-webfetch.py:RETRY_STATUSES`;
a likely bot block earns a one-line hint naming `profile=chrome` rather than a
silent switch, and every answer's status line names its transport, fresh or
cached `Scripts/mcp-webfetch.py:_transport_label`. The impersonation branch
[[0004-never-pin-a-browser-impersonation-version]] governed is superseded by
[[0026-speak-chrome-from-the-stdlib-verify-by-default]]. Alongside it: a content-type gate that refuses non-textual
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
  import `Scripts/mcp-webfetch.py`. That makes this server the fleet's one
  exception to the stdlib-only rule, declared as temporary and listed by name in
  `tests/test_py_deps.py` [[0024-pure-python-39-and-the-stdlib]]. The smoke harness now starts it the same way
  the registration does, through a per-server `launcher` argv prefix
  `Scripts/_mcp_smoke_test.py:launch_prefix` — so what the test measures is what
  actually runs, and this server's smoke went from SKIP to a full pass. The
  fallback is deliberate rather than absent: a `launcher` whose binary is not on
  `PATH` degrades to the interpreter, reproducing the old SKIP with a stderr tail
  instead of killing the run on a missing `uv`.
- **Two roots, and neither is under `~/.claude`.** `--cache-root DIR` names the
  cache directory itself — entries are `DIR/<sha256>.json`, nothing appended
  `Scripts/mcp-webfetch.py:_cache_dir` — and `--project-root` contains `save_to`;
  they are separate flags `Scripts/mcp-webfetch.py:main`, and the cache root
  reaches nothing but the cache `Scripts/mcp-webfetch.py:handle_fetch`. Without the
  flag the cache is `$XDG_CACHE_HOME/web-fetch` when that variable is set,
  non-empty and absolute, otherwise `~/.cache/web-fetch` — never the project root
  `Scripts/mcp-webfetch.py:_default_cache_root`. The server expands `~` in the flag
  itself, so a `~` spelling holds whether or not a shell sits in between
  `Scripts/mcp-webfetch.py:_resolve_cache_root`. The registration passes the
  default explicitly, by user request; the value equals what the flag-less server
  would pick. One per-user cache outside every project is the point: a user-scope
  server starts in every project, and a project-relative cache would scatter a
  cache directory into every repo it was launched from; a fetch cache is also keyed
  by URL, not by project, so one shared directory makes a second project's fetch
  of the same page a cache hit instead of a duplicate download. The project root
  is `.`, the Claude Code session's project directory (the process cwd), the
  convention the rest of the fleet registers with. Until R-0054 a single
  `--project-root ~/.claude` carried both jobs — the cache at
  `~/.claude/.cache/webfetch/` — so `save_to` inherited the cache's root by
  accident: a relative `save_to` landed under `~/.claude` in every project, and the
  project actually being worked in sat outside containment
  `Scripts/mcp-webfetch.py:_resolve_save_path`. The split first kept the cache
  under `~/.claude` behind its own flag; on 2026-10-02 the user moved it out to the
  XDG cache directory, and `--cache-root` stopped meaning a parent of
  `.cache/webfetch` and became the directory itself.
- **Eviction is least recently used, and mtime means "last used".** Past
  `CACHE_MAX_BYTES` the entries with the oldest mtime go first
  `Scripts/mcp-webfetch.py:_cache_evict`, and every fresh hit refreshes its entry's
  mtime `Scripts/mcp-webfetch.py:_cache_touch`. Before this a hit wrote nothing, so
  eviction was FIFO by write time: the moved 180-entry cache sat at the 64 MiB cap
  dropping entries in write order, a hit one included. Freshness does not read
  mtime — an entry's age is its stored `fetched_at` `Scripts/mcp-webfetch.py:_cache_age`
  — so the touch cannot make a stale entry look fresh. Filesystem atime was
  rejected as the signal: `noatime`/`relatime` mounts drop or coarsen it, and
  indexers and backups move it without a fetch.

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

Containment alone still let a fetched page reach code execution inside the tree:
with `overwrite=true` and raw output the saved bytes are the server's, so a
prompt-injected page could ask to be saved over `.git/config` (`core.hooksPath`,
`core.fsmonitor` run on the next git command) or an existing executable hook.
Since R-0062 the resolved target is also refused when, relative to the project
root and case-folded, it lies under `.git/` or `.claude/hooks/`, is a
`.claude/settings*.json`, or is the root `.mcp.json` — on creation as well as
overwrite, because creating `.git/config` or `.mcp.json` is already enough
`Scripts/mcp-webfetch.py:_protected_save_class`. Judging the resolved path makes a
symlink into `.git` hit the same refusal; case-folding closes `.GIT/config` on
macOS's case-insensitive default volume. A nested `sub/.mcp.json` is allowed:
only the root one is Claude Code's configuration. Gated by
`tests/test_webfetch_roots.py:group_s`.

`--test` now exits **1** on a refusal, where it used to print the error and exit 0
`Scripts/mcp-webfetch.py:main`. `save_to` is what made that load-bearing: a shell
caller whose write was refused saw a success exit and went on to read a file that
was never written.

**Measured** over nine cases through the `--test` CLI (the evidence was a CLI run,
not a suite — the server had none then): all nine passed, including the
containment case, where a save to `.claude/tmp/webfetch-verify/outlink/escaped.md`
was refused because it resolved to `/private/tmp/escaped.md`. Nothing was written
outside the project root. The server now has a suite, `webfetch_roots`
`tests/test_webfetch_roots.py` ([[tests]]), but it gates the two-root split rather
than this whole section: `--cache-root` naming the cache directory itself, the
flag-less default resolving per the XDG rule above (set, unset or empty, relative)
`tests/test_webfetch_roots.py:group_x`, a relative `save_to` landing under the
project root, and a `save_to` into the cache directory — absolute or `../` —
refused as outside the project root, before any fetch
`tests/test_webfetch_roots.py:group_b`, plus the protected-path refusals above.
The out-of-tree symlink, overwrite and non-2xx rules above are gated by no suite.

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
used to deliver implicitly. Every call site answers through one predicate
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

`search_for_pattern` also accepts the ripgrep-style flags callers reach for, and
none of them is ever silently ignored
`Scripts/mcp-purity.py:handle_search_for_pattern`. `regex:false` is a real
literal mode: the pattern is `re.escape`d and skips the `\|` → `|` rewrite the
regex path applies for callers who over-escape alternation, so `size(` is five
characters rather than an unterminated group, and a pattern that fails to compile
as a regex is answered with a pointer to it. It used to be a hard error, which was
the honest answer while no literal mode existed — silently ignoring it is how a
pattern meant literally quietly becomes a regex — but it left every caller
hand-escaping metacharacters. `line_numbers` is a no-op when **true**, since
`content` rows always carry `path:line:`, and is still refused when **false** in
`content` mode: purity cannot drop them, and ignoring the request would hand back
exactly what the caller asked to remove. `only_matching` is ripgrep's `-o` — each
non-empty match becomes its own `path:line: <match>` row, so `offset` and
`head_limit` page by match, not by line, and the header names both counts so the
number being paged cannot be read as the line count `match(es)` means everywhere
else. It is refused beside `count` / `files_with_matches`, whose rows carry no
text to trim, and beside nonzero context lines, which ripgrep drops silently under
`-o` — the one precedent this server declines to copy. `query` is an alias for
`substring_pattern` per-function only, never in the global table, because `symbol`
owns `query` as its own canonical parameter
`Scripts/mcp-purity.py:PARAM_ALIASES_BY_FUNC`. The same table carries the opposite
case: `pattern` is global (`-> substring_pattern`), but `list_dir` has no such
parameter, so there the global row could only ever answer a name filter with the
accepted-name dump — the per-function row aims it at `filter` instead, the fnmatch
name match that `pattern` already means in `find_file`. A global alias is only
global when every handler can honour it; the two escapes are *owned elsewhere* and
*does not exist here*. `max_results` / `max` take the second escape: globally they
name the semantic handlers' cap, but `search_for_pattern`, `find_file` and
`list_dir` cap with `head_limit`, so there they re-point instead of dying as
unknown params — while `count` is deliberately not re-pointed, because beside
`output_mode: count` it would be ambiguous. `paths_include` / `paths_exclude` are
plain global aliases of the two `*_glob` filters `Scripts/mcp-purity.py:PARAM_ALIASES`,
and sending an alias beside its canonical name is refused like any other collision
`Scripts/mcp-purity.py:_resolve_aliases` [[0015-ambiguity-is-the-defect]]. The contract is pinned by the
`purity_file_ops` suite `tests/test_purity_file_ops.py` and restated
model-facing in `ClaudeCode/skills/mcp-purity/SKILL.md`. (The case count is not
repeated here: the figure that stood in this sentence said **39** against a
declared 53, and the suite has grown again since — it is declared once in
`tests/run.py:SUITES` and asserted on every run against what the suite actually
recorded, which is the arrangement a prose copy exists to avoid.)

A third escape had to be carved from the *value* side rather than the key side.
`relative_path` is a global alias of `path`/`paths`/`file`/`root`, so a caller
who sends a **list** reaches every handler through one name — and only two of
them can honestly serve several roots.
`Scripts/mcp-purity.py:MULTI_PATH_FUNCTIONS` is that whitelist, hand-written and
two names long, while `Scripts/mcp-purity.py:_resolve_aliases` keeps the refusal
as the default for everything else. The default is not caution, it is the only
honest answer available: every other handler reads `relative_path` as a scalar,
so a list would be stringified into a path nobody named —
`os.path.join(root, ['a','b'])` raises, but `str(['a','b'])` inside a glob or an
error message does not, and either way the caller is told something false. Two
list lengths stay exempt at *every* function, because neither can mean more than
one root: a one-element list collapses to its element, byte-identical to the
scalar it means, and an empty one is dropped to the downstream default. Two
further details are decisions rather than polish — the refusal text is BUILT
from the whitelist instead of repeating it, so widening the set cannot leave a
message naming the old one; and the function name goes through
`Scripts/mcp-purity.py:_sanitize_log` first, because the resolver runs BEFORE
the `HANDLERS` lookup, which makes an unknown function name unsanitized caller
input on its way into that string (CWE-117).

The handler half is where the shape was decided. `search_for_pattern`
concatenates its roots through a generator `Scripts/mcp-purity.py:_walk_roots`
rather than wrapping the scan in a per-root loop, so the scan stays **one** loop
over **one** stream: every accumulator it owns — the offset, the head limit, the
row budget, the wall-clock deadline — keeps spanning the whole call with no
second `break` to forget, and the paging that was already there sees a single
result stream. The generator yields `os.walk`'s `dirnames` list untouched, so
the consumer's in-place `dirnames[:] = …` pruning still reaches `os.walk` —
exactly the contract `os.walk` documents. With one root it walks precisely what
`os.walk(root)` walked before, in the same order: unchanged, not merely
equivalent. Overlapping roots (`src`, `src/lib`) reach the same file twice, so a
hit is deduped on `os.path.realpath` — two spellings of one file are one file —
and the dedupe is **armed only above one root**, so a scalar call pays no
realpath per file for a collision it cannot have. `clang_tidy` needed no code
change to join the whitelist: its list branch was already written, and the
resolver had simply made it unreachable for more than one element.

Each root the generator yields also carries the **bound** its files are
contained by, and that is a fix, not a feature. The scan re-contains every
walked file through `realpath`, so a file symlink resolving outside the tree is
never opened (F6 / CWE-22) `Scripts/mcp-purity.py:handle_search_for_pattern` —
but it used to measure against the project root alone, while without `--strict`
`Scripts/mcp-purity.py:safe_path` deliberately admits an out-of-root root for a
read-only handler. Every file of such a root then failed the gate, and the reply
was `0 match(es)` for a scope that was never read: the silent zero
[[0017-a-silent-zero-is-the-defect]] refuses, reached through containment
instead of a glob. The bound is now the project root when the search root lies
inside it and the admitted root itself otherwise
`Scripts/mcp-purity.py:_walk_roots`, so an out-of-root root is searched and a
symlink escaping *that* root is still dropped. `--strict` is untouched:
`safe_path` refuses the escape before any walk, so the per-file gate is never
the thing that says no. The fixture that pins it carries one link and two roots
`tests/test_purity_file_ops.py:group_k` — rooted at `src` the link escapes and is
dropped, rooted one level up the same link is inside and is read — because a
drop with no matching read cannot tell an enforced boundary from a fixture that
was never built. The decision, the refusal that was implemented first and
overruled, the gate-removal rejected beside it, and the symlink back into the
project that an out-of-root search drops on purpose are frozen in
[[0021-contain-by-the-admitted-root]].

The two globs, `paths_include_glob` and `paths_exclude_glob`, take a **string or
a list of strings** `Scripts/mcp-purity.py:_glob_list`. The list is what a
ripgrep-taught caller writes, since `-g` repeats, and before it was accepted the
reported call `exclude: ["build/**", ".git/**", "vendor/**"]` died as a bare
`TypeError` naming no parameter. Include keeps a file matching **any** element,
exclude drops a file matching **any** element, and `[]` means no filter, the same
way an empty `relative_path` list means the project root. Inside a list a
non-string or an **empty** element is refused rather than skipped: an empty
include element matches nothing, which is the silent zero again. That refusal
is recorded as a decision of its own in [[0021-contain-by-the-admitted-root]].
Every element still goes through `Scripts/mcp-purity.py:_reject_brace_glob`, so a list is not a
way around the brace rule. The widening is search-only on purpose. `find_file`'s
mask and `list_dir`'s filter stay one glob each, and a non-string handed to
either is now a `ValueError` naming the parameter, raised by that same function
before anything reaches `re.search`.

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
refusing `path` while already filtering on the docs-relative path
`Scripts/mcp-wiki.py:_fn_search`, so a whole docs-relative path was a legal
*value* of path_prefix there too — it selects the single page it names — and the
trainer is the same one: a hit line prints a path, and no answer the server
renders ever utters path_prefix. `path` therefore reaches **several** canonical
names — `slug`, `source`, and path_prefix on every function that takes a scope
`Scripts/mcp-wiki.py:PARAM_ALIASES_BY_FUNC` — which is the global row's
epitaph: one table cannot spell one word two ways, so the row that was declined
on diagnostics grounds would by now be declined on arithmetic. The case pins each
spelling against the UNFILTERED answer rather than only against path_prefix's —
two spellings that agree prove the alias *resolved*, and only a narrower answer
proves it reached the filter. All of it is pinned by `wiki_recall` group N
`tests/test_wiki_recall.py`. (The case count is deliberately not repeated here:
the figure that stood in this sentence said 115 against a declared 116 when
`d56d138` removed it, and it was labelling a GROUP with a SUITE total either way.)

### A scope that selects nothing is a refusal, not an empty report

`freshness` gained that path_prefix alongside the alias row, and the filter went
on the **input** side — inside `Scripts/mcp-wiki.py:freshness_analyze`, before
the corpus walk — never on the rendered rows. Both halves of that placement are
load-bearing. *Honesty*: `summary` is derived from `pages`, and every count the
report prints (`ok:`, `gating:`) is derived from `summary`, so filtering the rows
afterwards would leave those two lines counting the whole corpus above a list
describing a slice of it — totals answering a question nobody asked is the one
way this function can lie. *Cost*: `_classify_page` is the expensive step, one
`git diff` per distinct `verified.commit` `Scripts/mcp-wiki.py:_classify_page`,
so a page the caller excluded must never be paid for — and the filter sits at the
earliest point the data allows, on the `relpath` that `iter_pages` yields,
because nothing before it knows which page it is looking at.

A prefix matching **nothing** returns a refusal rather than a report
`Scripts/mcp-wiki.py:freshness_render`, which is that same defect one level
further out: a report with no rows still renders `gating: 0`, and that reads as
*nothing is stale* when what actually happened is that nothing was looked at. It
declines in the caller's own word and names the value the prefix is matched
against. When the prefix does select pages the scope goes in the **header**,
because every number under it — each bucket size, `ok:`, `gating:` — counts the
filtered set alone.

The derivation chain that decided the placement, the five alternatives rejected
on the way — filtering the rendered rows, recomputing the totals in the
renderer, overloading `root` as the scope, leaving the filtering to the caller,
and a second `freshness_subtree` function — and the gap that decision left
**declared rather than fixed**, that `str.startswith` is not a path boundary and
correcting one filter alone would make the filters disagree about what a prefix
means, are frozen in [[0018-the-totals-must-describe-the-scope]].

That gap has since been closed for every scoped function at once: `35f4a89` gave
them one shared predicate,
`Scripts/mcp-wiki.py:_path_prefix_matches`. A whole page path matches exactly, a
prefix ending at a `/` boundary matches everything beneath it, a prefix ending
inside the final component still matches (so `adr/001` selects the 0010-0019
records), and a prefix ending inside an earlier component matches nothing —
`sub` is not a way to spell `subsystems/`.

`status` resolves to `stats` `Scripts/mcp-wiki.py:FUNCTION_ALIASES` on the usual
grounds: it is the spelling a caller reaches for, and as a FUNCTION name it can
only mean the census, so there is nothing for it to be ambiguous against — the
word is already in this server's vocabulary three times over (a search/list
param, the frontmatter field, and the git-measured state) and none of those is a
function. What it does **not** take is the no-function reply's job: `wiki_call()`
with no function at all stays the liveness answer, and a caller asking for
`status` wants the corpus's state rather than the process's.

### `validate`'s two shell-outs, and the answer that may not degrade

`validate` picks a parser from the file extension, and shell scripts are now
covered: `.sh` and `.bash` both map to the **bash** validator
`Scripts/mcp-inspect.py:_VALIDATE_EXT`, reached as a thin handler
`Scripts/mcp-inspect.py:h_bash` and as the function aliases `sh` / `shell`
`Scripts/mcp-inspect.py:ALIASES`. `-n` is bash's own read-but-do-not-execute
mode `Scripts/mcp-inspect.py:_v_bash`; inline content has no path, so it is fed
on **stdin** rather than written to a temp file, which would break this server's
read-only contract. `.zsh`/`.fish`/`.ksh` stay unmapped on purpose — bash would
report their own-dialect syntax as a bogus FAIL.

**Three spellings, one parser, and nothing lies about which one ran.** `bash`,
`sh` and `shell` all resolve to the same validator
`Scripts/mcp-inspect.py:_VALIDATORS`: the word a caller reaches for must not be
the word that gets rejected, and an `unsupported format 'sh'` for a file bash
reads perfectly is a refusal the caller can do nothing useful with. Every verdict
names `bash -n` and the `format` column echoes the spelling that was asked for.
The asymmetry that buys is declared rather than hidden: bash's grammar is a
superset of POSIX sh's, so an `sh` script can earn a **false OK** (a bashism like
`[[ … ]]` passes here and would die under dash) but can never earn a **false
FAIL**. A missed defect degrades to *not checked*; an invented one sends the
caller after a bug that is not there.

**A missing `bash` binary is FAIL, not SKIP**, and that is read straight off the
`javascript` precedent's own comment `Scripts/mcp-inspect.py:_v_javascript`
rather than re-derived. An absent **parser** may degrade — "this host's Python
cannot read TOML" is an honest thing for a SKIP row to say — but an absent
**answer** may not: the caller asked whether a script parses and got nothing at
all, and a SKIP row in a mixed batch leaves the verdict at **PASSED** over a file
nobody checked, named only in a "not verified" tail. (A SKIP/LIMITED-only call
reads **NOT VERIFIED**.) FAIL is the only rung that cannot be mistaken for success.

Both interesting properties here were **measured, not assumed**, and the first is
measured twice over, in two places, by two different instruments. The recorded
probe goes at the one startup file a non-interactive bash *does* read: with
`BASH_ENV` pointed at a marker-writing script, a plain `bash <file>` produced the
marker and `bash -n <file>` did not, and a `$(…)` in the validated source
produced nothing under `-n` either `Scripts/mcp-inspect.py:_v_bash`. The
**suite** does not set `BASH_ENV` at all — it reaches the same property from the
inside, with a fixture that is valid bash whose only statement writes a marker
file, so a validator that executed anything leaves evidence. Both entry points
are driven, because the path form hands bash a filename while the content form
hands it stdin, and the marker's absence is asserted in a **later group, after
the server child has exited**, so a late write cannot slip past the check
`tests/test_inspect_validate.py`. The other is why only the
**first** diagnostic line is reported `Scripts/mcp-inspect.py:_v_bash_error`:
this host carries both bash 5.2 and bash 3.2, and on an unterminated quote 5.2
prints one line at the opening quote while 3.2 prints that line and then adds a
second complaint at the last line of the file. The first points AT the defect on
both bashes; the last demonstrably does not, so reporting the first is what keeps
the answer stable across versions.

### mcp-git's passthrough slot, and the one name reserved in that file

`args` is the raw passthrough slot — a list handed to git verbatim behind the
semantic flags — and it had the failure every positional slot on this server has
had: a caller reaching for a different word does not get the slot, it gets the
generic fall-through, and the fall-through is a bogus flag. Measured:
`{"max_count": 6, "extra_args": ["--stat", "master..HEAD"]}` rendered
`--extra-args=--stat --extra-args=master..HEAD`, so each list element became its
own flag and even the revision range was lost. `Scripts/mcp-git.py:_ARGS_KEYS`
now claims `extra_args`, `extra`, `argv` and `git_args` beside `args`, folded
into `_META_KEYS` so that **no** spelling falls through — membership in that set
is the whole of what stops the conversion loop turning a key into a flag, and
camelCase comes free because `extraArgs` normalises first. None of the four is a
real git flag on any subcommand of this whitelist, so claiming the name costs no
working behaviour. The value contract is deliberately unchanged
`Scripts/mcp-git.py:_passthrough_args`: a list, or a string that is
`shlex`-split (a model writing `"--oneline -5"` means two arguments, not one),
at the argv position it always had. The generic unknown-key-becomes-a-flag
fall-through is **kept** rather than closed, with a negative control pinning it
`tests/test_mcp_git_params.py`.

**Two spellings at once is an error naming both**, per
[[0015-ambiguity-is-the-defect]]: last-wins and first-wins both read the WIRE
ORDER, so the same two keys sent the other way round would silently make a
different call, and a model that sends two spellings has hedged rather than
mistyped. The rule is presence-based, so `{"args": null, "extra": […]}` is
refused as well — a caller who wrote both keys still has to be told which one is
read. The message names the spellings the caller WROTE rather than their
normalised forms (reporting that `extra_args` collided with `extra_args` is not
actionable) and sorts them, so the sentence is order-independent and not just the
verdict.

One line of this belongs to the fleet's own record rather than to this server,
because it is a property of the **gate**. mcp-git has no `_resolve_aliases` at
all — its aliasing is structural, `_camel_to_snake` plus the key sets above —
and the smoke harness decides whether to run the fleet's alias-collision probe
against a server by TEXT-SEARCHING its source for that resolver's **definition
line** `Scripts/_mcp_smoke_test.py:alias_collision_checks`. A text search cannot
tell a definition from a quotation of one, so writing that line into a docstring
turned the gate red on its own consistency row (`resolver=True row=False`) in a
server that defines no resolver. Quoting it in full is therefore reserved in this
file, which is why the docstring spells the name around rather than out
`Scripts/mcp-git.py:_passthrough_args`.

## Task utilities

Operate on the `requirements.yaml` workflow (see [[overview]]):
`Scripts/task-plan.py` (status + dependency analysis), `task-update.py`,
`task-show-all.py`, `task-show-details.py`, `task-batch-planner.py`,
`task-implementation-plan.py` (token-efficient plan extraction), and
`task-validator.py` (requirements.yaml schema validation — the one task utility
that needs PyYAML, checked with `find_spec` at startup: absent, it prints one
line and exits 2). What the file they all
operate on is *for*, and why the plan-to-implement handoff needs a validated task
graph beside the prose plan, is [[requirements-yaml]].

## Search

Two CLIs and one MCP server answer search, and all three carry the same generated
search code rather than copies of it ([[generated-regions]]).
`Scripts/search_duckduckgo.py` (web search: DDG lite first, Bing after a DDG block)
takes `Scripts/_mcp_websearch.py`; `Scripts/search_github.py` (code search via
grep.app) takes `Scripts/_mcp_codesearch.py`; `Scripts/mcp-search.py` takes both
(below). None imports an HTTP package: each carries the stdlib client generated
from `Scripts/_mcp_chrome.py`, and since `8dde3a6` every search session opens on
its **Chrome path**, with no verified transport and no fallback ladder
`Scripts/search_duckduckgo.py:create_session` `Scripts/search_github.py:create_session`:

- **One transport, one attempt.** There is no verified-first probe, no re-issue over
  a second transport and no sticky switch, and the DDG script's `cdp` backend and its
  `DDG_BACKEND` switch are gone; every argument is one query
  `Scripts/search_duckduckgo.py:main`. Each session passes the public-only connect
  policy `Scripts/_mcp_chrome.py:_ch_public_only_policy`, so a redirect into private
  or metadata space is refused although the certificate is not checked.
- **No transport label.** A result carries no `**Transport**` line, because every
  answer comes over the same transport; the certificate gap is stated in each
  session factory's docstring and in the server's tool description.
- **What is a block is judged per endpoint, structurally, and only on that
  endpoint's own host**, and a transport failure is never a block. grep.app: a
  403, a 429 whose body is a non-JSON `text/html` page, or a 200 that is not JSON;
  a 429 carrying JSON stays a rate limit `Scripts/_mcp_codesearch.py:_grep_app_blocked`.
  Bing: a 403 `Scripts/_mcp_websearch.py:_bing_blocked`. DDG: zero parsed results
  plus either an HTTP 202 (the one challenge status recorded) or a challenge marker
  the query does not itself contain, so neither a reflected query nor a
  third-party snippet can trip it `Scripts/_mcp_websearch.py:_ddg_blocked`.
- **What a block does.** A DDG block moves that query and the rest of the run to
  Bing `Scripts/_mcp_websearch.py:run_web`. A Bing or grep.app block ends that
  query: the CLI prints one stderr line, `[Blocked by Bing on: <query>]` or
  `[Blocked by grep.app on: <query>]`, still prints every other query's results,
  and exits 1 `Scripts/search_duckduckgo.py:main` `Scripts/search_github.py:main`.
  Every other event the search functions report — an undecodable body, a non-block
  HTTP status, a rate limit, a grep.app answer in the wrong shape, a transport
  error, the DDG-to-Bing switch — is one stderr line rendered from a `note`
  callback, with the query and the detail passed through the render sanitizer first
  `Scripts/search_duckduckgo.py:_cli_note` `Scripts/search_github.py:_cli_note`.
- **Pacing and rotation are per query.** A random sleep before every query but the
  first, a fresh session every `ROTATE_EVERY` queries, and a session dropped after a
  transport error so the next query opens a new one
  `Scripts/search_duckduckgo.py:with_session` `Scripts/search_github.py:with_session`.
  The DDG-to-Bing switch replaces the session for the same query, with no extra
  sleep and no extra rotation count.
- **A search body is capped at 2 MiB**, not the client's 64 MiB ceiling: every
  search session passes `SEARCH_MAX_BYTES` as `max_bytes`, and an oversized
  answer is no results, never a block `Scripts/search_duckduckgo.py:SEARCH_MAX_BYTES`
  `Scripts/search_github.py:SEARCH_MAX_BYTES` `Scripts/mcp-search.py:SEARCH_MAX_BYTES`.

Why the search sessions moved to the Chrome path although it verifies no
certificate, what that costs, the measurements behind each block signal and the
declared limits are [[0026-speak-chrome-from-the-stdlib-verify-by-default]] and its
`8dde3a6` addendum; the live records are in [[spec-ddg]], which is also the DDG
bot-detection research log.

The client is three generated sources taken whole
([[generated-regions]]): `Scripts/_mcp_chrome.py`, the TLS 1.3 / HTTP/2 /
HTTP/1.1 client with its one profile table `Scripts/_mcp_chrome.py:_chrome_profile`
and the verified stdlib transport `Scripts/_mcp_chrome.py:_ChFallbackConnection`
that webfetch still uses by default, and the `ctypes` decoders
`Scripts/_mcp_brotli.py` and `Scripts/_mcp_zstd.py` it is handed as `decoders=`.
Its Chrome path verifies no certificate, and the module says so in its first line.

`Scripts/chrome_capture.py` is the **independent oracle** that client is measured
with, and deliberately shares no code with it: a loopback-only capture server
that records what a real Chrome puts on the wire (ClientHello, the post-HRR
second ClientHello, the h2 preface and HEADERS blocks, the HTTP/1.1 request
head), a JA3/JA4 parser, an HPACK decoder, a `diff` between two capture sets and
an `export` that refuses any non-loopback identifier before it writes a fixture
`Scripts/chrome_capture.py:main`. The committed captures it exported are the
Chrome 154 profile's only source of truth — see [[tests]] for the fixtures and
[[chrome-profile-refresh]] for refreshing them when Chrome moves.

The Bing results are parsed by a stdlib `html.parser` tree
builder `Scripts/_mcp_websearch.py:parse_bing_results` that evaluates the four
XPath expressions the old lxml parser used, including libxml2's implicit-close
and end-tag-priority rules; it is pinned to lxml's recorded output on
`tests/files/html/tf_bing_serp.html` [[0024-pure-python-39-and-the-stdlib]].

The DDG lite results and the grep.app snippets are parsed the same way since
R-0057: each is one `html.parser` pass keyed on start tags and attributes, never
on an implied end tag (`html.parser` has none)
`Scripts/_mcp_websearch.py:_LiteParser` `Scripts/_mcp_codesearch.py:_SnippetParser`.
They replaced regex `findall` / `finditer` scans whose lazy DOTALL patterns
rescanned to the end of the input for every unterminated opener — super-linear
on a hostile body, which the endpoint or a MITM on the unverified Chrome path
controls (security finding F32). The body cap above therefore bounds memory and
time, no longer a quadratic scan. The lite parser reads a result link's href off
the raw start tag `Scripts/_mcp_websearch.py:_raw_href`, because `html.parser`
decodes attribute values and the regex kept a direct link's `&amp;` as written.
Both parsers return exactly the fields the regex parsers produced, pinned as
literals, and a hostile body just under the cap is gated in wall time by the
`search_parsers` suite [[tests]]. One divergence is declared rather than fixed:
on Python 3.14 `html.parser` treats an unclosed `<title>` as raw text to the end
of the page, so a page that drops its `</title>` parses to no results
`tests/test_search_parsers.py`. The record of the change is the R-0057 addendum
to [[0026-speak-chrome-from-the-stdlib-verify-by-default]].

### mcp-search — the same search as one MCP server

`Scripts/mcp-search.py` serves the two CLIs' search over stdio behind one
dispatcher tool, `search_call` `Scripts/mcp-search.py:SEARCH_CALL_TOOL`. Its answers
are the markdown the CLIs print, because the blocks that render them are the same
generated regions. It is **not registered in any client config yet**: the smoke
check runs it as an unregistered server `Scripts/_mcp_smoke_test.py`, and nothing
installs it — it is live under `~/.claude/scripts` only through the symlink described
at the top of this page.

**Functions and params** `Scripts/mcp-search.py:handle_search_call`:

- `web` — `queries` (a string or a list of strings; aliases `query` and `q`),
  `limit`, `max_answer_chars` `Scripts/mcp-search.py:ACCEPTED_WEB_PARAMS`.
- `code` — the same plus the grep.app filters `lang`, `repo` and `path`
  `Scripts/mcp-search.py:ACCEPTED_CODE_PARAMS`.
- No `function` answers the status text: the function list and the searches each
  endpoint has served, with no transport line `Scripts/mcp-search.py:_status_text`.
  An unknown function answers the one-line `Unknown function: X. Available: code, web`
  that `name_existence` parses. Unknown params are refused, and so is an alias
  set beside its canonical name `Scripts/mcp-search.py:_resolve_aliases`
  ([[0015-ambiguity-is-the-defect]]).

**Caps, checked before any lock is taken**, each answered with a fixed message:
how many queries a call may carry and how long each may be
`Scripts/mcp-search.py:MAX_QUERIES` `Scripts/mcp-search.py:MAX_QUERY_CHARS`, the
`limit` range `Scripts/mcp-search.py:MAX_LIMIT`, and each filter's length
`Scripts/mcp-search.py:MAX_FILTER_CHARS` plus its shape — `lang` and `repo` by a
regex, `path` by the refused-character rule below `Scripts/mcp-search.py:_filter_param`.
`max_answer_chars` is read by the generated fleet reader, so a value that is not
an integer falls back to the COMPOSED default `Scripts/mcp-search.py:_max_answer_chars`;
this server then maps a value of zero or below — the fleet's "no cut" — and any value
above `MAX_ANSWER_CHARS_CEILING` to that ceiling, so it never returns an unbounded
answer `Scripts/mcp-search.py:_answer_cap`. A JSON-RPC `params` that is not an object
is refused `-32602` before any handler runs `Scripts/mcp-search.py:McpServer`.

**One session per endpoint, used only under its lock** `Scripts/mcp-search.py:with_session`.
A `_ChSession` is not thread-safe and the pool runs several calls at once, so each
endpoint — ddg, bing and grep.app — owns one session `Scripts/mcp-search.py:_Endpoint`,
and the lock is also what makes the pacing process-wide. Under the lock, in order:
rotate every `ROTATE_EVERY` searches, sleep what is left of the pacing gap, create and
warm up a session if there is none, run the search, and close and drop the session
if the search reported a transport failure. The pacing comes **before** the
warm-up, so the warm-up GET is paced like a search. `run_web` makes one hook call per
endpoint per query, so the DDG and Bing locks are never held together
`Scripts/_mcp_websearch.py:run_web`. Every session comes from the same Chrome-path
factory as the CLIs', with no downgrade `Scripts/mcp-search.py:_create_session`.

**Two time bounds.** A lock not acquired within `ENDPOINT_LOCK_TIMEOUT` answers
`endpoint busy, retry later` `Scripts/mcp-search.py:ENDPOINT_LOCK_TIMEOUT`. The whole
call has `CALL_DEADLINE`, checked before every lock wait (which it cuts to what is
left) and before every pacing sleep `Scripts/mcp-search.py:CALL_DEADLINE`. A search
already running is not interrupted, so a call can end past its deadline by **at most
one in-flight search plus a warm-up** — a declared overrun, not a defect. The
residual the pool cannot avoid, every worker parked on one endpoint's lock, is
bounded by those two constants and accepted.

**The reply contract** `Scripts/mcp-search.py:_reply`:

- A query blocked at the end of its ladder — Bing for `web`, grep.app for `code` —
  makes the call `isError`, and the text still carries every other query's results
  beside a fixed `blocked by Bing` / `blocked by grep.app` notice
  `Scripts/mcp-search.py:handle_web` `Scripts/mcp-search.py:handle_code`.
- A busy endpoint or the call deadline stops the call with the results gathered so
  far and a fixed stop notice, also as `isError` `Scripts/mcp-search.py:_stop_notice`.
- No results is a success. A transport failure is the fixed notice `transport error`
  and stays a success.
- Only the body is capped, and never left inside an open code fence
  `Scripts/mcp-search.py:_cap`. A block notice the cut removed, and the stop notice,
  are appended **after** the cap note, outside the cap, so truncation never hides
  why a call failed.
- Every per-query notice is a fixed string; no exception text, path or traceback
  reaches the reply. A handler crash answers its exception's class name only.

**Logging is structure only.** A search note is logged as the endpoint, the fixed
event token, the query index and either a status code or an exception class name —
never the query text, a body or an exception message `Scripts/mcp-search.py:note_for`.
Wire values in the debug log are bounded and escaped by the generated
`_log_value` from `Scripts/_mcp_logging.py`, no longer a hand copy (R-0070)
`Scripts/mcp-search.py:_log_value`
([[0011-a-truncated-payload-carries-the-first-cookie]]). A grep.app answer that is
JSON but not in grep.app's shape is a parse failure — a `grep_schema` note and no
results, with the session kept — not a transport error
`Scripts/_mcp_codesearch.py:_grep_hits`. The Bing tree builder is bounded on a
hostile page by a nesting depth past which it stops pushing elements and a
per-document scan budget for end tags `Scripts/_mcp_websearch.py:_TreeBuilder`.

**The Unicode policy, rendering side.** Every third-party field that is rendered —
title, snippet, URL, repo, path, branch and the code itself — passes through the
render sanitizer `Scripts/_mcp_websearch.py:_web_clean`
`Scripts/_mcp_codesearch.py:_code_clean`. It drops every `Cc` control except the tab
and line breaks a field explicitly keeps; every `Cf` format character, which takes
the bidi controls, the zero-width characters, the BOM and the tag characters with
it; lone surrogates (`Cs`); the variation selectors U+FE00–FE0F; and the whole
U+E0000–E01EF block. U+2028, U+2029 and U+0085 become a line break, which a
single-line field then collapses to a space `Scripts/_mcp_websearch.py:_web_line`.
A URL is rendered only as an absolute http(s) link with a host. A non-ASCII host is
rendered in its IDNA (punycode) form, and the link is dropped when it has none
`Scripts/_mcp_websearch.py:_web_url`. A snippet's fence is one backtick longer than
the longest backtick run inside it `Scripts/_mcp_codesearch.py:_code_fence`. Why the
rule lives twice, once per search source, is [[generated-regions]].

**The Unicode policy, input side.** A query or filter is the caller's own text, sent
to the endpoint and echoed in the reply, so it is **refused** with a fixed message
rather than rewritten when it carries a C0 or C1 control or DEL (tab and newline
included), a bidi control, a tag character, or U+2028 / U+2029
`Scripts/mcp-search.py:_refused_char`. ZWNJ, ZWJ and the variation selectors are
accepted: ZWNJ is Persian orthography and ZWJ / VS16 are emoji. The endpoint receives
them as written; the echo drops them.

**Declared limits, not gated.** Rendered as they arrive: homoglyph letters in titles
and snippets, an ASCII look-alike domain (punycode only exposes a non-ASCII one),
userinfo that makes a URL read as another host, stacked combining marks (Zalgo),
private-use and unassigned code points; there is no NFKC normalisation. Some
refusals echo caller text — an unknown function's name, the alias pair, the JSON
error window. Results are third-party content entering the model's context
unmarked, over a transport whose certificate is not verified
([[0026-speak-chrome-from-the-stdlib-verify-by-default]], `8dde3a6` addendum).

**Concurrency, cancel and the fleet gates.** Handlers run on a worker pool beside a
reader thread no handler can take ([[0008-a-serialized-read-loop-looks-like-a-dead-server]]).
A cancel is `reply-only`: a search waiting on or holding an endpoint lock runs to its
end, and only its reply is suppressed; shutdown drains every call in flight
`Scripts/mcp-search.py:McpServer`. The smoke check handshakes the server and drives
its alias-collision probe. Its forced-exception probe is declared to answer `-32602`,
because a non-object `params` is now refused before any handler runs and so can no
longer reach the `run()` catch-all `Scripts/_mcp_smoke_test.py`; the catch-all's
`-32603` is proven live by the `run-catch-all-live-32603` row of the server's own
suite `tests/test_mcp_search.py:group_g`, `mcp_search` in [[tests]].

## LLM router

`Scripts/llm-router.py` is **not an MCP server**: it is a stdlib-only HTTP server
that Claude Code talks to instead of Anthropic, run by path with
`--config <file>` and reached by pointing `ANTHROPIC_BASE_URL` at its loopback
port `Scripts/llm-router.py:_rt_parser`. It accepts the Anthropic Messages API,
routes each request by its exact `model` string, then the config's `default`
`Scripts/llm-router.py:_rt_route`, and hands it to one of the backend kinds
`Scripts/llm-router.py:KIND_CLASSES`: `passthrough` (an Anthropic-compatible
upstream, relayed as-is), `llamacpp` (llama-server's own `/v1/messages`, its
deviations repaired by named quirk rows), `mistral` (translated to and from
chat completions), and `codex` and `openai`, which speak the OpenAI Responses
API through one adapter `Scripts/llm-router.py:ResponsesAdapter` driven by a
profile row `Scripts/llm-router.py:ResponsesProfile` — codex over a ChatGPT
OAuth login, openai over an API key or OAuth. Routing and secrets live in one
JSON config that must be a regular file owned by the user with mode `0600`,
opened without following a symlink
`Scripts/llm-router.py:_rt_read_config_file`; network settings are flags, and
there is no inline-config flag because argv is visible in `ps`. An OAuth backend
is signed in by the `login` subcommand (browser or device flow)
`Scripts/llm-router.py:_rt_login`, which writes its tokens back into that same
config. Its HTTP front is generated from `Scripts/_mcp_httpfront.py`, shared
with [[mcp-proxy]], plus the router's declared adaptations, and its generated
regions are the two logging regions (`_configure_logging`, and `_log_value` with
its two bounds, which replaced the router's hand copy in R-0070), the OAuth core
from `Scripts/_mcp_oauth.py` and the HTTP front from `Scripts/_mcp_httpfront.py`
([[generated-regions]], [[0029-the-http-front-is-a-domain]]). The
command line, the config schema and its OAuth write-back, the per-kind header
allow-lists, the security rules, the `llm_router` suite and the declared limits
are [[llm-router]]; why it routes by model and translates at the edge is
[[0028-route-by-model-translate-at-the-edge]].

## Restartable sessions: `context-guard.sh` and `loop-restart.sh`

Shell scripts, not Python, let a long session checkpoint itself and come
back in a fresh process when its context fills. `Scripts/claude-loop.sh` runs a
Claude Code session in a loop through the `safranek` launcher, exports `CLAUDE_LOOP_PID` (its own pid) and
`CLAUDE_LOOP_FLAG`, and starts a new round only when the flag file exists after
the process ends; every round's prompt resumes from the newest checkpoint session
`Scripts/claude-loop.sh`. `Scripts/context-guard.sh` is the Claude Code hook that
decides *when*, and `Scripts/loop-restart.sh` is what the model runs to end the
round.

**The guard is inert outside the loop.** It exits silently unless
`CLAUDE_LOOP_PID` is set, so registering it in a normal session costs one
`exit 0` per tool call `Scripts/context-guard.sh`. Its header asks for two
registrations, PostToolUse and PreToolUse, and it tells them apart by the hook
input's `hook_event_name`; it parses that input with `jq`
`Scripts/context-guard.sh`.

**What it measures (PostToolUse).** Main session only: any hook input carrying an
`agent_id` exits at once, so a subagent's tool calls are never measured. It reads
the tail of the session transcript named by `transcript_path`, takes the last
`assistant` entry that is not a sidechain and carries `message.usage`, and sums
`input_tokens`, `cache_creation_input_tokens` and `cache_read_input_tokens` into
the current context size. That is compared with `CLAUDE_CTX_LIMIT`, default
`300000` `Scripts/context-guard.sh` (the same default `Scripts/claude-loop.sh`
prints at the start of each round). The transcript JSONL and its `usage` field are
an undocumented internal format, which the script's header says in so many words
`Scripts/context-guard.sh`. Nothing latches: once over the limit, every later tool
call in the main session repeats the message.

**What it injects.** Over the limit, it returns a PostToolUse `additionalContext`
headed `CONTEXT GUARD (main session only; subagents and forks ignore this)` with
the measured size and the limit, and an ordered instruction: finish the current
atomic step; wait until every background minion and subagent the session launched
has finished and reported, never checkpointing or restarting while one runs, and
capture their results in the checkpoint; do not wait on background shell tasks,
but stop one that never exits on its own (a server, watcher, `tail`, a command
waiting for input) with `TaskStop` and record the command and output file of any
other in the checkpoint; then run the `p:checkpoint` skill; then run
`~/.claude/scripts/loop-restart.sh` `Scripts/context-guard.sh`.

**What it refuses (PreToolUse).** When a subagent issues a command that names
`loop-restart.sh`, the guard exits `2` with a stderr reason, so the tool call is
blocked: only the main session may end the process `Scripts/context-guard.sh`. The
main session's own call passes, and so does any command from a session not
under the loop.

**Its partner.** `Scripts/loop-restart.sh` refuses to run without
`CLAUDE_LOOP_PID`, walks up the process tree from its parent to the process whose
parent is the loop, touches `CLAUDE_LOOP_FLAG` when it is set, and sends that
process `SIGTERM` `Scripts/loop-restart.sh`. The flag is what turns the exit into
a restart rather than the end of the loop, which is why a plain `/exit` or Ctrl-C
stops `Scripts/claude-loop.sh` instead of restarting it. The guard's PreToolUse
refusal exists because a subagent running `loop-restart.sh` would find the same
ancestor and kill the main session mid-step.
