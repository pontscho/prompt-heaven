---
name: scripts
type: subsystem
status: active
title: Scripts & MCP Servers
description: Standalone Python scripts -- MCP servers and requirements.yaml task utilities.
sources:
  - Scripts
verified:
  commit: db63229
  date: 2026-09-16
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
  - 0015-ambiguity-is-the-defect
  - 0017-a-silent-zero-is-the-defect
  - 0018-the-totals-must-describe-the-scope
  - 0021-contain-by-the-admitted-root
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
refusing `path` while filtering on `relpath.startswith(prefix)`
`Scripts/mcp-wiki.py:_fn_search`, so a whole docs-relative path was a legal
*value* of path_prefix there too — it selects the single page it names — and the
trainer is the same one: a hit line prints a path, and no answer the server
renders ever utters path_prefix. `path` therefore reaches **three** canonical
names across **five** rows (`slug`, `source`, and path_prefix three times over
— `search`, `list` and now `freshness`), which is the global row's
epitaph: one table cannot spell one word two ways, so the row that was declined
on diagnostics grounds would by now be declined on arithmetic. The case pins each
spelling against the UNFILTERED answer rather than only against path_prefix's —
two spellings that agree prove the alias *resolved*, and only a narrower answer
proves it reached the filter. All of it is pinned by `wiki_recall` group N
`tests/test_wiki_recall.py`. (The case count is deliberately not repeated here:
the figure that stood in this sentence said 115, the declared count is 116, and
it was labelling a GROUP with a SUITE total either way.)

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
and a second `freshness_subtree` function — and the gap left **declared rather
than fixed**, that `str.startswith` is not a path boundary and correcting one of
the three filters alone would make them disagree about what a prefix means, are
frozen in [[0018-the-totals-must-describe-the-scope]].

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
all, and a SKIP row leaves the batch verdict at **PASSED** over a file nobody
checked. FAIL is the only rung that cannot be mistaken for success.

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
