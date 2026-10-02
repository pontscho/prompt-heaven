---
name: tests
type: subsystem
status: active
title: Test Fleet
description: The stdlib-only functional test fleet — one explicit entry point, a two-layer harness, case counts written down once and machine-checked, and a severity model where FAIL is reserved for rules that cannot flap.
sources:
  - tests
  - project-forge.yaml
verified:
  commit: 87e478e
  date: 2026-10-02
links:
  - agents
  - scripts
  - generated-regions
  - layer-contract
  - 0005-approve-the-wrapper-not-the-command
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0009-the-first-reader-is-a-cold-model
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0016-a-cell-may-not-forge-a-boundary
  - 0020-the-prompt-is-its-own-block
  - 0022-a-someday-maybe-is-a-roadmap-item
  - 0023-the-websocket-client-is-a-sixth-domain
  - 0024-pure-python-39-and-the-stdlib
  - chrome-profile-refresh
---

# Test Fleet

`tests/` is a stdlib-only, zero-dependency functional test fleet: one entry point
(`tests/run.py`), one shared plumbing module (`tests/_harness.py`), the in-process
suite modules its `SUITES` table registers plus a subprocess smoke check, and a
committed fixture tree under `tests/files` — C/Lua sources for the language
servers, and recorded measurements for the HTTP client and its decoders. Two organising ideas explain
nearly every design choice in the tree, and both exist because the repo already
paid for the alternative.

## Idea 1 — every typed number is machine-checked

The `SUITES` table in `tests/run.py` is the single place a suite's case count is
written down. The banner prints it, and the runner asserts it against what the
suite actually reported; a mismatch is a hard failure with an error that tells
you to fix the table. The reason is stated where the table is defined: *"A count
that lives in prose and is checked by nobody is a lie waiting to happen — this
repo shipped six of them."*

A declared count of `None` is not laziness — it means the count is **data-derived
rather than a fixed case table**. `spawn_stdin` emits one case per spawn site it
finds, so the gate is the invariant (every site passes an explicit `stdin=`), and
a drift line reading `265 != 262` would be a strictly worse error message than
naming the new site. The rule the tree follows: *"What is typed gets checked;
what is derived gets derived."* `tests/README.md` The same instinct removes the
fleet size from human hands — `tests/run.py` computes it from
`Scripts/_mcp_smoke_test.py`'s own table, because a hand-maintained copy was
wrong within a day of being written.

`None` is **not** the default for a per-server suite, though, and the per-server
rows say so where somebody would go looking: `read_loop`, `wire_log`,
`handler_crash` and `protocol_version` each emit a fixed multiple of the fleet size and each **types** its count anyway
`tests/run.py`. The reason is written beside every one of them, and it is the
exact inverse of `mcp_footprint`'s: there a moved count would fire on a
legitimately added server, so pinning it would buy a false failure; here *a
server appearing without a declared row is the defect*, so a count that moves
when the roster moves is the alarm working rather than noise. Same arithmetic,
opposite verdict — which is why the choice is argued per suite instead of read
off the shape.

Note what this gate does **not** cover: it compares the `SUITES` table against
the run, and nothing else. A case count repeated in a module docstring is
outside it — which is why the convention is to never write one there, and why a
docstring in `tests/test_inspect_validate.py` claiming 94 cases for a suite then
declaring 119 survived fully green runs until `57c1bda` removed it.

## Idea 2 — severity is argued, not assumed

Every rule's severity is a written decision, and the criterion is whether the
rule can **flap on ordinary work**. The clearest statement of the test is in
`tests/test_name_existence.py`, defending a FAIL: the rule fires on a structural
frontmatter key, the corpus now carries zero of them, so *"the rule has no live
subject, so it cannot flap on an ordinary prompt edit. It can only fire on a
REGRESSION — precisely the event worth breaking a build for."*

The inverse governs everything that fires on prose. Rule 3b in the same suite
scans agent bodies for tool prescriptions, and prose editing is this repo's main
activity, so a false FAIL there would fire constantly. INFO is likewise chosen
where a finding is a measurement rather than a verdict (`mcp_footprint` is a
measuring tape almost everywhere), or a knowingly-open gap that has not been
decided yet — turning those into gates *"would report a decision that has not
been made yet as a regression"* (`tests/test_mcp_footprint.py`).

**That last reason can expire, and watching one expire is the useful half.** The
sentence names its own precondition — a decision — so a landed decision does not
merely permit the promotion, it withdraws the argument for the INFO.
[[0013-the-ceiling-is-a-payload-class]] ratified the fleet's three output
ceilings as three payload classes, and exactly one server finding in
`mcp_footprint` became a gated FAIL: `ceiling-deviation-says-why`, which asserts
that a server carrying a non-default ceiling wrote its reason down. It clears the
flap test for the ordinary reason — an int and a comment, both already in the
repo, no environment, no binary, no clock — but the part worth copying is that
the promotion was a **reading** of a condition the suite had itself recorded,
not a fresh argument. Conformance as a whole stays INFO, because that ADR
licenses a deviation on the default alone and leaves the other two criteria open.
The same decision later carried a second finding over:
`every-registered-server-declares-a-ceiling` fails a registered server with no
reply-ceiling constant, or one outside the three classes — the blind spot where
`mcp-forge` and `mcp-gdc` had sat, invisible to the deviation gate because it
could only judge a constant that existed.

INFO rows are printed, not swallowed, and that is the point: a knowingly-open
gap stays **visible**, so promoting a tree to gated later is a scope decision on
printed data rather than a fresh audit (`tests/test_spawn_stdin.py`).

## The suppressor — and the one that was removed

A **suppressor** is a documented contextual condition that demotes a candidate
FAIL to an INFO-with-evidence row. The live ones are documented in
`tests/test_name_existence.py`:
a tool whose server is not registered cannot be granted, so naming it is a
retirement note; an illustrative context on the mention line (`e.g.`, `for
example`, `such as`, `etc.`); and a negative or delegating context — a
"does-not-exist" sentence nearby, or another agent named on the line, where the
agent's own name pointedly does not count. The window is asymmetric on purpose:
negative context counts within ±1 line, the other two must be line-exact, so a
neighbouring `e.g.` cannot excuse a prescription of its own.

The subsystem's cautionary tale is the **fourth suppressor, since removed**. It
excused a missing grant whenever the agent's frontmatter disagreed, and its only
live subject turned out to be a real defect it was hiding — leaving one minion
silently unable to perform the documentation lookup its own routing table
prescribed. The conclusion is recorded in the source and is the reason the
related rule now fails on the key rather than reasoning about it: *"That is the
cost of answering a platform question with a suppressor instead of a fact."*

Suppressors are themselves negative-controlled. A synthetic agent corpus pins
the exact expected severity of every case, and **INFO and silence are asserted as
hard as FAIL** `tests/test_name_existence.py` — because an over-eager suppressor
produces exactly the same "no FAIL" as a correct one.

## The harness contract

`tests/_harness.py` is deliberately two layers rather than one abstraction,
*"because the suites do NOT speak the same protocol and must not be forced
through one"*: a protocol-agnostic layer (options, suites, results, temp
workspaces, process running) and a JSON-RPC client used only by suites that
drive an MCP server child.

The single most load-bearing detail: **status is the display verdict, `problems`
is the actual one.** Pass/fail accounting is driven by `problems` alone, so an
INFO case that trips a hard invariant — non-zero exit, dirty stderr — still
counts as a failure while keeping its informational label.

There is also an undeclared fourth status. `SKIP` is not a harness constant;
`name_existence` and `mcp_footprint` define it locally as a plain string, and because a `SKIP` row carries no
problems and is not `INFO`, the group tally counts it as a **PASS**. `purity_lsp`
does the opposite, recording its skips as `INFO` so a host without clangd or
lua-language-server stays green without inflating the pass count. Two
incompatible conventions — worth knowing before reading a tally.

Rendering has two modes so neither ported driver lost its output shape: streamed
one-line-per-case, or buffered and grouped into `=== group (pass N, fail N, info
N) ===` blocks.

The harness also owns the fleet's **repo-pollution detectors**, and they are a
pair with a deliberate asymmetry: `pycache_snapshot` for bytecode
`tests/_harness.py:pycache_snapshot` and `repo_tree` for path names
`tests/_harness.py:repo_tree`. Both walk from the repo root and both skip
`.git`, which churns on its own. Only `repo_tree` also skips the scratch area,
and that difference is the point rather than an oversight: a write under
`.claude/tmp` is legitimate work by a suite that owns a sandbox there, while a
`.pyc` under it is litter like any other — so the bytecode half deliberately
sees `ClaudeCode/`, `docs/` and the scratch area too.

A suite takes the snapshot at its start and again in its hygiene group, failing
on `after - before`. Neither set of users is written down here, and both are
open: every suite that snapshots path names also snapshots bytecode, but not the
reverse — the bytecode half is strictly the larger set. The case id is not a
reliable handle either, because the same check is registered as
`no-new-repo-paths`, `k-no-new-repo-paths` and `no new repo paths` in different
suites; read the hygiene group, do not grep the name. `repo_tree` lived as four
hand copies until one of them independently grew the scratch-area exclusion and
the other three never learned it; homing it beside its twin is what ended that.
The `SUITES` table was not touched by that move `tests/run.py`, so Idea 1's own
gate is what certifies those four kept their case counts.

Two details of the homed version are recorded decisions rather than incidental.
The scratch area is excluded **at any depth** `tests/_harness.py:SCRATCH_DIR`,
mirroring `.gitignore`'s deliberately unanchored `**/.claude/tmp`, because a
root-anchored skip would be *narrower than the concept it is named after* — and
that mirroring is a **declared** divergence, not a silent hand-copy: the check
is an `os.walk` over path names that deliberately never consults git, since a
git dependency would change what it measures into "did some future `.gitignore`
excuse this mess". And the match is on whole path components, never
`str.startswith` `tests/_harness.py:_is_scratch_dir` — `".gitignore"` starts
with `".git"`, and the prefix test this replaced silently ate that file out of
the snapshot.

## The roster

One registry entry per suite in `tests/run.py`: the in-process Python suites
listed below plus `smoke`, which runs `Scripts/_mcp_smoke_test.py` as a
subprocess and reports *servers* rather than cases. Every suite has a matching
`forge` target in `project-forge.yaml`, each requiring the `syntax` prerequisite.
That direction does **not** invert, and the table below is keyed on suites only:
forge's `test` group also carries targets that are not suites and have no row
here — `test.amalgamate_check` wraps `Scripts/amalgamate.py --check`, the
generator's own CLI staleness gate, which takes the same `syntax` prerequisite
but reports an exit code rather than a case count `project-forge.yaml`.

There is no suite count in that sentence on purpose. The one it used to carry was
two suites behind by the time anyone noticed — Idea 1's own failure mode, showing
up in the page that argues for it. The table below is the roster, `tests/run.py`
is the registry, and the run is the only thing that knows the totals.

| Suite | What it verifies |
|---|---|
| `inspect_validate` | the `mcp-inspect` validation family against fixtures, including the reported error line number |
| `mcp_first_guard` | the deny-guard hook: DENY iff the permission decision says so |
| `sbx_gate` | the grant-only gate — the guard suite's inverted mirror, where empty stdout is the safe outcome |
| `sbx_seccomp` | `sbx --seccomp`: the x86_64 BPF allowlist run through the suite's own BPF interpreter against an independent copy of the syscall header, on any OS; `_bwrap_argv` kept a pure `Scope -> argv` function whose golden argv carries the fresh `/dev`, the empty `/run`, `/tmp` and `/var/snap` tmpfs mounts and their read-only remounts, the `--ro-bind-data` file masks, `--unshare-ipc` and `--new-session`, refusing a FILE secret without its mask fd; `main()` in-process with exec stubbed, refusing off Linux x86_64 and wiring an inheritable, empty mask fd; the live prctl probe on Linux (INFO elsewhere); and the probe's header cross-check, where a name an older header lacks is INFO and only a different number FAILs `tests/test_sbx_seccomp.py` — [[0005-approve-the-wrapper-not-the-command]] |
| `purity_lsp` | that `purity_call` really absorbed the retired clangd/luals servers, driven against live language servers |
| `purity_file_ops` | the gitignore-aware file handlers: the `.claude/tmp` exemption **and** its narrowness, now across `find_file` too (off by default, `skip_ignored_files` / its inverse `no_ignore` to turn it on, `.git` never listed); `read_file`'s paging — `limit` as a line count from the resolved start, a resume hint that round-trips from a negative offset, a fractional float refused rather than truncated, and an offset past EOF answered with the past-the-end note instead of an inverted range, the same note `list_dir` and `find_file` now give past their last row; a missing directory — or a missing `search_for_pattern` root, whose file roots stay legal — reaching the caller as an error rather than as an empty reply; and a walk rooted at or inside `.git` refused in `list_dir`, `find_file` and `search_for_pattern`, with `.github` / `x.git` and `read_file .git/HEAD` as the controls; a `search_for_pattern` root **outside** the project root — which the server admitted and then read nothing of — now searched as a file, a directory and one element of a list, with a symlink escaping *that* root still dropped (paired with the control rooted one level up, where the same link is read) and the escape refused under `--strict` by a second, strict server child `tests/test_purity_file_ops.py:group_k`; and search's two globs taken as a string or a list of strings, a non-string, empty or brace element refused by name, and a non-string `find_file` mask or `list_dir` filter refused the same way instead of surfacing as a raw `TypeError` `tests/test_purity_file_ops.py:group_g` — [[0017-a-silent-zero-is-the-defect]]; `regex:false` as a real literal search, `max_results` / `max` as `head_limit` and `paths_include` / `paths_exclude` as the glob filters, each refused beside its canonical spelling `tests/test_purity_file_ops.py:group_d`; and `only_matching` — one row per match, paged by match rows, refused beside `count` / `files_with_matches` or context lines `tests/test_purity_file_ops.py:group_n`; `find_file`'s path-style mask honouring character classes exactly as the bare mask does `tests/test_purity_file_ops.py:group_o`; and a catastrophic caller regex answered with an error inside the call's time budget, the same server answering the next search `tests/test_purity_file_ops.py:group_p`; and `replace_content`'s regex mode bounded the same way, the file's bytes untouched on overrun and backrefs / match-count answers unchanged `tests/test_purity_file_ops.py:group_q` |
| `mcp_git_params` | named params → `git` argv, fully offline with `subprocess` stubbed; also that a mutating stash is never adopted by the cancel reclaim `Scripts/mcp-git.py:_run_git_mutating` |
| `name_existence` | prompt corpus + server text ↔ live MCP inventory, plus agent grants vs their own prescriptions |
| `spawn_stdin` | every spawn site under `Scripts/` passes an explicit `stdin=` — AST-based, one case per site |
| `mcp_footprint` | fleet token cost: description tax, result ceilings, boilerplate. A tape measure, not a gate — with two exceptions, the ceiling-deviation and ceiling-declaration rules of [[0013-the-ceiling-is-a-payload-class]], each with its own negative control |
| `wiki_recall` | the wiki search relevance gate on a synthetic corpus — silence, calibration, type signal, aliases |
| `wiki_index` | the server's INDEX renderer and orphan rule, the page-type vocabulary and helpers the server vendors from `_wikilib.py` compared side by side with it, and the `freshness.py` / `reindex.py` wrappers over the server: the six page-type constants equal between the copies and homed once on the skill side, a `roadmap-item` page counted in one line and never listed, the orphan exemption for archive pages but not for an unlinked `roadmap` page, both roadmap types freshness-untracked only while they carry no `sources:`, the `freshness.py` exit code equal to the server's `gating:` number (a dead body anchor gates, git lag never does), `reindex.py` failing on a duplicate slug through the server's answer, `reindex.py:render_index` kept only as a delegate, and exit 2 with no server, each oracle with a negative control that proves it can fire — [[0022-a-someday-maybe-is-a-roadmap-item]], [[0019-only-gate-on-what-you-can-prove]] |
| `wiki_addendum` | `addendum.py`, the one legal write to an accepted ADR, driven as a child against a `mkdtemp` wiki root: the appended bytes exact and every old byte kept, the file mode kept and the inode fresh (temp file + `os.replace`); only an `active` `type: adr` page under the git top-level's `docs/` is written — a draft, another type, a missing page, and a path or symlink out of the root are refused; the staged item is exactly `title` + `body`, and a body line that is a level 1-2 heading (ATX or setext) is refused while `###` passes; the same heading is never appended twice; every refusal is exit 2, one stderr line, the page byte-identical `ClaudeCode/skills/wiki/scripts/addendum.py` |
| `jira_cli` | the Jira CLI fully offline with the transport injected: auth mode, context-path URL join, lazy deployment probe, **every** pager — the Cloud token model and the DC offset model behind one iterator, the agile `isLast`/`startAt` envelope that serves boards, sprints and create metadata, and `list_projects`, which is that same envelope hand-rolled a second time and was never counted with its siblings, walked past page one with the caller's query re-sent every time and **bounded**, because a server that ignores `startAt` defeats all three stop conditions at once and the unbounded walk was measured at 5.58 GB before it was killed — `JIRA_READ_ONLY`, `--dry-run`, the error mappings and the byte-pinned multipart body |
| `bitbucket_cli` | the Bitbucket Server/DC CLI fully offline with the transport injected: the context-path URL join, the refusals at the door — an `http://` scheme that would hand the bearer PAT to the network in cleartext, and a userinfo base URL whose refusal is asserted **not** to reproduce the secret — the same-origin redirect handler gated component by component, `BITBUCKET_READ_ONLY` at **both** layers with `WRITE_COMMANDS` compared against the set *measured* by driving every handler through a verb-recording transport rather than against a second typed list, the merge refusals each measured as **zero requests** rather than asserted as statement order, `pr-builds`' exit code pinned in Markdown **and** `--json` in one case, and byte parity with `jira.py`'s `_profile_path` — a copy that is deliberate and is gated on identity so the eventual unification stays mechanical, which is exactly what it bought: the copy was taken carrying a `$HOME` boundary that bound on neither side, and the identity gate is what forced the repair through **both** files in one change rather than one, with the boundary itself then *driven* through a real symlinked `$HOME` because identical text is not the same claim as identical behaviour |
| `checkpoint` | `checkpoint.py` as a **writer**: `Start`/`End` land on the block and nothing else, the numbers describe the file *after* the region was inserted, `prepend` lands the block and the table it describes in one `os.replace`, a stale or duplicate-id segment is refused on content, and every refusal exits 2 leaving the file alone — [[0009-the-first-reader-is-a-cold-model]]. It also pins the **shape** of a new segment: every session must be followed at once by its own `## ACTIVATION S<NNN>` block, in fresh and `--overwrite` mode too, and the order in which the refusals fire is fixed `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:check_segment_shape`. A `### ACTIVATION` quoted inside a fenced code block does not count as the old in-block form. A group of its own `tests/test_checkpoint.py:group_l` covers the activation block's `A<NNN>` row read back through `session`, `activate`'s de-quoting, `nexts`' order with nothing printed on a refusal, and the read-only fallback to the old subsection. An empty prompt is refused on write and on read alike, and a file that is not UTF-8 is refused through the exit-2 route rather than crashing `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:read_lines` |
| `roadmap` | `roadmap.py` as the single writer of the roadmap and its archive, run only inside a `mkdtemp` sandbox — the live roadmap is unreachable by construction, with the guards proven to bite before the script existed: round trip against a hand-written expected file, both wiki frontmatter parsers reading what it writes, the invariants and the WIP cap, the dependency graph and `ready`, the optimistic lock with its controls, `close`'s archive-first order and the archive-wins repair of a crash between its two writes, a golden export reproduced byte for byte, the section-7 refusals as one stderr line with the files byte-unchanged, and the CLI under an ASCII locale, a reader that closes early and an IO error on stdout; the security rules (stdlib only, one git spawn site with read-only subcommands, no shell, exact write-route callers) are gated on the source by AST — [[0022-a-someday-maybe-is-a-roadmap-item]] |
| `roadmap_board` | `board.py`, the read-only kanban board, run as a child in a `mkdtemp` sandbox `tests/test_roadmap_board.py`: any schema but `roadmap-export/2` refused with one line, the markdown board byte for byte with its ADR 0016 cells decoded back and the reverse `blocks` edges, an HTML page whose embedded data cannot close its script block and which renders without `v-html`, and `--out` held to the `roadmap-board-*.html` name and refused through a symlink; the live roadmap is digested to prove it is never touched — [[0022-a-someday-maybe-is-a-roadmap-item]] |
| `generated_region` | every live generated region still matches its canonical source, and the source named on a region's `BEGIN` line is the one its names resolve against; a whole source (the Chrome client and its two decoders) taken by every host whole, in source order, on one marker, and every top-level statement of it a shape the generator can take — plus a behavioural group so a shared helper is unit-tested once rather than only drift-checked — [[generated-regions]] |
| `mcp_chrome` | the stdlib HTTP client in `Scripts/_mcp_chrome.py` against oracles that are not the module: crypto known-answer vectors pasted from the RFCs and Wycheproof, ML-KEM cross-checked both ways with OpenSSL where it is new enough; the ClientHello, HPACK blocks, h2 first flight and HTTP/1.1 head judged by `Scripts/chrome_capture.py`'s independent parser against the committed Chrome 153 captures; real TLS stacks and scripted TLS 1.3, h2 and HTTP/1.1 peers written in the suite; every `CH_*` ceiling read off the module and probed at its bound; the per-hop SSRF policy one row per bypass form; the TLS 1.2 fallback and the default verified transport against a loopback server whose only trust anchor is the committed test CA, and the label a redirect chain across transports earns. A host without the needed `openssl` records INFO, never a missing row `tests/test_mcp_chrome.py` |
| `mcp_decoders` | the `ctypes` brotli and zstd decoders: the per-platform load order under a stubbed platform (Linux never calls `find_library`; darwin hands `dlopen` only absolute paths, so a library planted in the working directory is never loaded), the committed fixtures byte-exact, decode bombs, corrupt input and an over-window zstd frame refused, a legacy or unknown zstd frame magic refused at the start and mid-stream, a missing library as one line, lying stub libraries refused, and two threads racing the first load. Rows that need the real library are INFO on a host without it `tests/test_mcp_decoders.py` |
| `chrome_capture` | the capture harness checked as an oracle in its own right, against a ClientHello walker and JA4 written from the RFC and the FoxIO definition rather than from the module: one fixed fixture, the RFC 7541 HPACK vectors, `serve` on loopback for h2, h1 over TLS, plaintext h1 and a scripted HelloRetryRequest, owner-only records and control bytes escaped in the summary, `diff` and `export` with their refusals — a foreign cookie name or value, a credential header, an unscripted request body — an `export` that a planted `.tmp` symlink cannot redirect and whose fixture lands `0600`, and every non-loopback bind refused `tests/test_chrome_capture.py` |
| `mcp_websocket` | the stdlib WebSocket client in `Scripts/_mcp_websocket.py` against an oracle written from RFC 6455 rather than from the module — the upgrade request with no `Origin`, the response checked by **exact** status line, `Upgrade`, `Connection` token and accept key (the RFC's own worked example pinned), every frame length form, the mask against a per-byte oracle, every refusal a frame header earns, fragments assembled around a ping, ping answered with its own payload, close echoed, the size caps and strict UTF-8 — then both hosts' generated copies, `mcp-gdc`'s `CdpSession` and the search script's `CDPSearcher`, driven against a loopback CDP peer; that host group was run red against the hand-written client first — [[0023-the-websocket-client-is-a-sixth-domain]] |
| `read_loop` | every server's read loop carries the shape [[0008-a-serialized-read-loop-looks-like-a-dead-server]] decided: a single-thread reader executor no handler can take, and one task per message — with the pool/coroutine split declared per server rather than inferred |
| `cancel` | every server honours `notifications/cancelled` on its read loop, by AST `tests/test_cancel.py`: an id-to-task registry filled after dispatch and emptied by a done-callback, the hook on the loop thread before the task factory, a `requestId` that is a str or a non-bool int, `initialize` never registered, nothing written in reply, and a dispatch target that lets the `CancelledError` through. What a cancel **reclaims** is declared per server as one class `tests/test_cancel.py:CLASSES`: `task` (the coroutine stops at its next await), `kill` (the request's child process group is signalled, with a declared exemption list `tests/test_cancel.py:KILL_EXEMPT` held to its word — an exempt spawn that adopts its child, or an entry naming no spawning function, fails), `lsp-cancel` (the generated `_request` drops its pending entry and sends `$/cancelRequest`), `pg-cancel` (a `CancelRequest` on a second socket stops the statement, checked by AST `tests/test_cancel.py:analyse_pg` and end to end against a loopback fake PostgreSQL `tests/test_cancel.py:group_behaviour`), and `reply-only` (the reply is suppressed and the work runs on). Every class but `reply-only` is measured before a row may claim it. Planted defects prove each problem code can fire, the suite was written red, and `Scripts/MCP_SKELETON.md`'s sample is lifted by script and analysed like a server. `_request`'s cancel arm is exercised behaviourally in `generated_region` `tests/test_generated_region.py:group_blocks` — [[0008-a-serialized-read-loop-looks-like-a-dead-server]] |
| `wire_log` | wire logging is structure only at both sites — protocol metadata and argument *keys*, never a payload body or value (F12/CWE-532) — with the shape each site may log declared per server, and `Scripts/MCP_SKELETON.md`'s own sample lifted by script and run through the same analyser — [[0011-a-truncated-payload-carries-the-first-cookie]] |
| `handler_crash` | every tool-handler catch-all leaves a traceback at a level the default WARNING configuration emits, at **both** site layers — the `McpServer` wrap, which every server has, and the module-level dispatcher, in the servers that carry one — each declared per server, plus one security clause: the format string must be a literal, so a payload cannot be interpolated into the one log that IS written |
| `table_cells` | every table renderer either escapes its own delimiter and documents the scheme where the model reads it, or is whitespace-delimited and has none to escape — one declared row per renderer carrying the class and the reason, the real escapers imported and round-tripped rather than restated, and reversibility as a **separate** clause because an encoder that does not escape its own escape character still passes a column count — [[0016-a-cell-may-not-forge-a-boundary]] |
| `protocol_version` | every server declares the handshake protocol version **once**, as the first member of `class McpServer`, and the `initialize` reply *reads* that member instead of restating the literal — the shape the live smoke handshake structurally cannot see, since a server inlining the **right** string is indistinguishable on the wire from one reading the constant, with the fleet's agreement asserted *between* the files so the suite never holds a copy of the number it polices |
| `forge_dispatch` | `forge_call`'s own dispatcher, in-process: `status` answers exactly what the empty call answers on **every one** of its paths — missing config, parse error, validation errors, the ordinary reply — each with a control proving the fixture took that path, and the alias is named wherever the function list is — asserted as a parsed list item or exact spelling, because every one of those texts already said "status" before the alias existed |
| `py_deps` | the fleet is pure Python 3.9 + stdlib: every import under `Scripts/`, `ClaudeCode/` and `tests/` whose top-level name is neither embedded 3.9 stdlib nor a repo module must be allowlisted and preceded by a literal `find_spec` guard, never `except ImportError`; no stdlib module removed since 3.9; every file parses with `feature_version=(3, 9)` — **syntax only**; `mcp-webfetch.py` a per-name declared exception, with every requirement in its PEP 723 block bounded `>=` / `<` and every `BeautifulSoup()` call naming a declared `lxml` builder `tests/test_py_deps.py:group_webfetch_deps`; every `ctypes`-loaded system library, a literal `CDLL` / `find_library` name or a `*SONAMES*` / `*FILES*` tuple element, registered with its reason `tests/test_py_deps.py:SYSTEM_LIBS`, a declared stem no file names failing as a stale licence; **no 3.10+ stdlib API** in the Chrome client, its two decoders and the capture tool, checked against a *list* of known APIs (`int.bit_count`, `zip(strict=)`, the 3.11 optional `to_bytes` / `from_bytes` arguments, `bisect` `key=`, `itertools.pairwise`, `X | Y` annotations, ...) with planted controls — 3.10+ API use outside those four files, and any API the list does not name, stay a declared blind spot; and the stdlib Bing parser pinned to lxml's recorded fields on `tests/files/html/` — [[0024-pure-python-39-and-the-stdlib]] |
| `webfetch_roots` | `mcp-webfetch`'s two roots (R-0054), in-process with the network stubbed: `--cache-root` names the cache directory itself, defaulting to `$XDG_CACHE_HOME/web-fetch` (absolute values only) else `~/.cache/web-fetch` and never the project root, while `save_to` stays contained by the project root — a `save_to` into the cache dir, absolute or `../`, refused before any fetch — and the dispatcher, the status reply, the tool description and both CLI entry points (`~` expanded) carry it; `HOME` and `XDG_CACHE_HOME` are pinned into the sandbox and the real default directory is asserted untouched `tests/test_webfetch_roots.py` |
| `smoke` | JSON-RPC plumbing invariants across every server file, including the error-envelope contract ([[scripts]]) |

There is **no auto-discovery**: adding a suite is three edits — the module, a
wrapper function, and the `SUITES` row `tests/run.py`.

### Why the smoke check is still standalone

`Scripts/_mcp_smoke_test.py` sits outside `tests/`, and the runner treats its
**interface** as frozen: it invokes the script as a subprocess, consumes its
exit code, and surfaces its output only on failure `tests/run.py`. That is also
why it reports *servers* rather than cases — the aggregate sums the two units
separately instead of inventing a case count for it.

A frozen interface is not a frozen file. Its launch table has since gained a
`registered` flag per entry, recording whether Claude Code actually starts that
server as opposed to merely whether the file exists
`Scripts/_mcp_smoke_test.py`. The table enumerates server *files*, the two sets
are **not** equal, and conflating them has already produced one wrong fleet-wide
conclusion here — which is why `mcp_footprint` sums only over registered servers
while the smoke check deliberately ignores the flag: its job is that every server
file still speaks the protocol, registered or not.

The same harness carries the **live** half of the cancel gate, which `cancel`
cannot reach because it only reads the AST. Every server gets malformed or unknown
`notifications/cancelled` messages, each followed by a ping, and must answer the
ping and never the cancel `Scripts/_mcp_smoke_test.py:cancel_notification_checks`.
Two probes then cancel a call that is really in flight. On jenkins, two calls are
held open by a loopback HTTP peer: the one cancelled for real is never answered,
and a sibling targeted only by ill-typed ids (`true`, `1.0`, `"1"`) still is
`Scripts/_mcp_smoke_test.py:inflight_cancel_probe`. This proves reply suppression
only, because jenkins is `reply-only`. On forge and on wiki, a cancelled call's
child pid (a test command, a `measure` command) must be gone well before its sleep
ends, and the cancelled id must never be answered
`Scripts/_mcp_smoke_test.py:inflight_kill_probe`. Nothing proves live that the
`task` servers stop their work or that the tshark, git or inspect children die:
those are declared and AST-checked only, and `pg-cancel` is proven against a fake
server in `cancel`, not a live PostgreSQL.

The smoke check is also the fleet's one deliberate exception to the child-env
rule, and the exception is argued rather than inherited: its `Popen` passes no
`env=` and no `-B` `Scripts/_mcp_smoke_test.py`. That is safe by **architecture**
— a server runs as `__main__`, which CPython never caches, and the
generated-region design means it imports no sibling from this repo
[[generated-regions]], so there is nothing here for CPython to write bytecode
for. Measured, not reasoned: a standalone run with `PYTHONDONTWRITEBYTECODE`
unset starts every server in the launch table and leaves the tree bytecode-free.
The note records its own expiry condition — the first server to grow a
repo-local import breaks the premise, and the suites asserting zero absolutely
are what would say so.

## Fixtures

`tests/files` holds committed fixtures, not code: nothing there is compiled,
linked, shipped or imported. Two kinds live side by side, and they follow
different rules `tests/files/README.md`.

The C and Lua sources under `c/` and `lua/` are for `purity_lsp`.

The `tf` prefix on every symbol is an **invariant, not a style choice** — a
repo-wide search for a real symbol must never match this directory.
`tests/files/c/tf_broken.c` and `tests/files/lua/tf_broken.lua` are
**deliberately broken** and carry a header telling you so: *"Do not fix this
file. A test asserts that clangd reports a problem here; repairing it would
silently disable that assertion."* Their defects are asserted at their planted
lines.

No `compile_commands.json` is committed, and that omission is load-bearing twice
over: it is why the language server emits no progress notifications against this
repo, and why a cache-exception list can stay empty while its group asserts
strictly.

Everything else is a **recorded measurement**, and the rule for those is the
opposite of "keep it working": never regenerate one from the code it judges,
because a fixture rebuilt from the module would agree with whatever the module
does. `html/` pins the stdlib Bing parser to what lxml returned (`py_deps`);
`enc/` holds `br` / `zst` / `gz` bodies produced once by the `brotli`, `zstd`
and `gzip` CLIs, with bombs, truncated input and an over-window zstd frame
(`mcp_decoders`); `chrome_capture/` is ONE Chrome 153 ClientHello read by name,
so its suite's case count cannot move when captures are added
(`chrome_capture`); `chrome/153/` is the growing set — one reduced JSON fixture
per connection, one subdirectory per capture set, exported from a real Chrome on
loopback — which is the only place `mcp_chrome` learns what Chrome sends, and is
refreshed from a browser as [[chrome-profile-refresh]] describes. `tls/` is not
a measurement but test-only loopback material: a test CA whose private key is
deliberately not committed and a localhost leaf signed by it, never to be added
to a trust store `tests/files/tls/README.md`.

## Invariants a newcomer breaks

- **Never write a case count into a module docstring.** It is written once, in
  `tests/run.py`, where it is checked against the run. A second copy is a number
  nobody verifies.
- **Every scanner suite must carry a negative control.** *"A checker that
  silently matches nothing is indistinguishable from a clean tree."*
  `tests/test_spawn_stdin.py`
- **AST, never regex, for source-scanning suites** — both regex false-positive
  shapes are live in this repo, so the justification is empirical, not aesthetic.
- **Bytecode discipline comes in two kinds, and they are not interchangeable.**
  `sys.dont_write_bytecode` is set before the first repo import `tests/run.py`,
  and every child process inherits `PYTHONDONTWRITEBYTECODE=1`
  `tests/_harness.py:child_env`. What backs that up at runtime is two different
  assertions. Most snapshotting suites check a **delta** — nothing created,
  nothing touched since the suite started — which stays silent on a file that
  was already there, because it reads as "1 before, 1 after". A smaller set
  asserts **zero, absolutely**, so a pre-existing artifact is a finding no
  matter who wrote it; `tests/test_purity_lsp.py:_pyc_problems` is that check
  with its reasoning attached, and it reports pre-existing files separately from
  newly written ones because the fix differs — delete the litter versus stop
  producing it. The delta sites in `tests/test_mcp_footprint.py` and
  `tests/test_spawn_stdin.py` carry a note saying which kind they are and where
  the absolute form lives. A third shape exists in exactly one place:
  `name_existence` pairs its delta FAIL with an INFO row listing stale `.pyc`
  for the modules it imports `tests/test_name_existence.py:IMPORTED_MODULES` —
  unattributable to this run, but it must stay visible.
- **Neither bytecode set gets a count written down here.** Both move whenever a
  suite is added, and the number this page used to carry was not a measurement
  but one stale ancestor copied three ways — the same wrong "two" sat in
  `project-forge.yaml` and `tests/README.md`. The fleet's sources now state it
  structurally instead: *a tree every suite that snapshots bytecode asserts
  stays empty* `project-forge.yaml` `Scripts/MCP_SKELETON.md`, and
  `tests/README.md` names one example of each kind with no count at all. This
  is also why `py_compile` must never be reintroduced `project-forge.yaml`.
- **Two sandbox conventions coexist and are not interchangeable.** Some suites
  use a system temp workspace; others use a per-run `.claude/tmp/<suite>/`
  directory whose escape is structurally gated — a single write path and a single
  child launcher record every target, and the group fails if any recorded path
  leaves the sandbox.
- **No clean target wipes `.claude/tmp`.** It holds the session handoff document
  and is gitignored, so it is not recoverable; suites clean their own
  subdirectories in a `finally`.
- **Nothing numeric may be typed if the module under test publishes it** —
  `wiki_recall` reads the live constants, because a hardcoded copy would still
  pass after somebody swapped the real values out, which is the exact edit its
  group exists to catch.
