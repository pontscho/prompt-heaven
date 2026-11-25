---
name: 0039-a-mounted-wiki-is-read-only-and-untrusted
type: adr
status: active
title: A mounted wiki is read-only and untrusted -- named instances beside the official wiki in mcp-wiki
description: Decision to let wiki_call read any absolute directory of wiki pages as a named, read-only, never-executed instance beside the official wiki -- mount, unmount and instances, a wiki param on the five read functions (search alone also takes * or a list of up to 5 names), measure, reindex, verify and freshness refused with one line, no git ever, a per-instance df/idf and 0.55 gate merged coverage-first, an fd-relative contained walk under page, byte, depth, entry and deadline caps, a per-reply nonce frame around every reply carrying mounted text, every echoed string escaped and clipped, a root that is not the official directory turned into an anonymous read-only instance (option B), and persistence that only the owner writes through --mounts-file; with the owner decisions, the alternatives rejected, the 42 plan-review dispositions, the deviations from the plan, the code-mode security review (APPROVE, one MEDIUM) and its two fix rounds, the owner-unconfirmed bounds and the declared limits -- among them that mount stays model-callable with no owner switch.
sources:
  - Scripts/mcp-wiki.py:Instance
  - Scripts/mcp-wiki.py:_MountRegistry
  - Scripts/mcp-wiki.py:_resolve_instance
  - Scripts/mcp-wiki.py:_refuse_mounted
  - Scripts/mcp-wiki.py:_mount_scan
  - Scripts/mcp-wiki.py:_mounted_corpus
  - Scripts/mcp-wiki.py:_mount_page_text
  - Scripts/mcp-wiki.py:_mount_text
  - Scripts/mcp-wiki.py:_frame_reply
  - Scripts/mcp-wiki.py:_search_targets
  - Scripts/mcp-wiki.py:_render_search_merged
  - Scripts/mcp-wiki.py:_fn_mount
  - Scripts/mcp-wiki.py:_fn_unmount
  - Scripts/mcp-wiki.py:_fn_instances
  - Scripts/mcp-wiki.py:_activate_persisted
  - Scripts/mcp-wiki.py:_load_mounts_file
  - Scripts/mcp-wiki.py:handle_wiki_call
  - tests/test_wiki_recall.py
  - tests/run.py:SUITES
verified:
  commit: 535ea8e
  date: 2026-11-25
links:
  - wiki-engine
  - scripts
  - tests
  - 0003-the-trigger-travels-with-the-tool
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0013-the-ceiling-is-a-payload-class
  - 0015-ambiguity-is-the-defect
  - 0016-a-cell-may-not-forge-a-boundary
  - 0019-only-gate-on-what-you-can-prove
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0032-a-runner-behind-a-proxy
---

# ADR 0039: A mounted wiki is read-only and untrusted -- named instances beside the official wiki in mcp-wiki

Status: draft 2026-11-24. It records code written on top of `b0e3962` and not
committed yet; on the commit that lands it, this page is promoted to `active`
and gets its `verified:` block. Every number below is a constant read from the
code or a count at the moment of the decision. The live values belong to
`Scripts/mcp-wiki.py`, and the living page is [[wiki-engine]].

## Context

`wiki_call` served exactly one wiki, the project's `docs/` (`--wiki-root`). A
per-call `root` param could point every function at another directory. That
included `measure` and `verify`, which run the commands that directory's
`measurements.json` names, and `reindex`, which writes its `INDEX.md`. Nothing
marked text read from such a directory as foreign. The owner wanted to read other
wikis (a sibling project's `docs/`, a cloned reference) through the same ranked
search and section reads, without handing them the official wiki's trust.

There were three real risks: execution (a foreign `measurements.json`), git (a
foreign `.git/config` is not trusted; `GIT_SAFE_ARGV` trusts only the local one),
and prompt injection (the model reads mounted page text as tool output). Reading
is a small risk on its own: the model's other tools run as the same uid and can
already read any `.md`.

## Decision

1. **Named instances, one door.** `mount` registers an absolute directory as a
   named instance, `unmount` drops a session mount and `instances` lists them all
   `Scripts/mcp-wiki.py:_fn_mount`, `Scripts/mcp-wiki.py:_fn_unmount`,
   `Scripts/mcp-wiki.py:_fn_instances`. `search`, `get_page`, `list`, `stats` and
   `source_to_pages` take `wiki=<name>`. If `wiki` is absent, the instance is the
   official wiki, and the reply is byte-identical to what it was before. Every
   handler gets its instance from one resolver, which decides official vs mounted
   vs anonymous exactly once `Scripts/mcp-wiki.py:_resolve_instance`. It is the
   only function allowed to name `_resolve_root`, and a static case enforces that.
   Names match `^[a-z0-9][a-z0-9_-]{0,31}$`. `official`, `*`, `all` and `default`
   are reserved. If no name is given, the default is the realpath's basename
   squeezed to that pattern. An invalid or taken name is refused and never
   auto-suffixed. An unknown instance name is refused with the fleet's
   did-you-mean pointer, and the suggestion is never resolved
   ([[0030-point-at-the-near-miss-never-resolve-it]]). `instance` is a
   per-function alias of `wiki`, and sending two spellings is refused as
   ambiguous ([[0015-ambiguity-is-the-defect]]). All of it is stdlib-only, like
   the rest of the server ([[0024-pure-python-39-and-the-stdlib]]).
2. **Read-only and never executed.** `measure`, `reindex` (`check: true`
   included), `verify` (`measure: true` included) and `freshness` refuse a
   mounted or anonymous instance with one line. The resolver refuses it before
   a persisted entry is activated, a root is stat'ed or walked, or an anonymous
   instance is admitted `Scripts/mcp-wiki.py:_resolve_instance`, and the first
   statement after resolving repeats the check as a backstop, before any
   registry load, subprocess or write `Scripts/mcp-wiki.py:_refuse_mounted`. A mounted instance
   never reaches git, `_repo_root_cached`, the diff cache or `_classify_page`.
   `search` and `source_to_pages` print the page's own editorial `status` instead
   of a measured state, and a `status` filter excludes mounted hits and says so.
   `source_to_pages` compares a mount's anchors as text and states that it does.
   The three registry functions sit in `READ_ONLY_FUNCTIONS`. That list means
   "may never reach an execution", and changing the in-memory registry is not an
   execution `Scripts/mcp-wiki.py:READ_ONLY_FUNCTIONS`.
3. **Each instance is scored on its own.** Every instance has its own df/idf
   space and its own relevance gate (default 0.55)
   `Scripts/mcp-wiki.py:_search_instance`. `search` alone also takes `wiki:"*"`
   (official plus every registered mount, in registry order) or a list of at most
   `MAX_WIKI_LIST = 5` distinct names `Scripts/mcp-wiki.py:_search_targets`. The
   merge key is (coverage descending, rank inside the instance, instance order
   with official first), and `limit` is applied after the merge. Scores are
   printed but never compared across instances. Every hit line starts with its
   instance, `(wiki: <name>, mounted)` or `(wiki: official)`, before any text a
   page wrote, and a mounted snippet line starts with `   · ` and the same tag. An
   instance that contributed nothing is named with its own silence, or with the
   reason a persisted entry was unavailable
   `Scripts/mcp-wiki.py:_render_search_merged`.
4. **A contained walk.** A mounted tree is read only through one fd-relative
   walk `Scripts/mcp-wiki.py:_mount_scan`:
   - The root is opened once with `O_DIRECTORY|O_NOFOLLOW` and its
     `(st_dev, st_ino)` is compared with the pin taken at mount. Every component
     below is opened relative to its parent's fd with `O_NOFOLLOW`.
   - Files are opened with the `_mcp_spool` flag set (`O_NONBLOCK`, `O_NOCTTY`,
     `O_NOFOLLOW`) and read in a loop to EOF, because one `os.read` can return
     short `Scripts/mcp-wiki.py:_read_regular_fd`. That function is a declared
     hand copy ([[0025-generate-do-not-import]]).
   - The walk skips and counts symlinked pages, non-regular files, pages with
     `st_nlink > 1`, entries on another device, pages larger than the per-page
     cap, undecodable pages and unreadable directories. It reports those counts
     and never drops anything silently.
   - Every entry seen counts toward the entry cap, depth is pruned before
     descent, and the wall-clock deadline is checked per entry.
   - A single page (for a line window or a section index) is read through the
     same contained path `Scripts/mcp-wiki.py:_mount_page_text`. A plain `open`
     of a path under a mount appears nowhere.
5. **A memo per instance.** A mounted or anonymous instance keeps its corpus in
   its own `memo`, one tuple replaced by one store. A bounded stat walk keyed on
   `(relpath, mtime_ns, size, st_ino, st_ctime_ns)` invalidates it. The cold
   build is single-flight under the instance's `build_lock`. A refusal partway
   through memoises nothing `Scripts/mcp-wiki.py:_mounted_corpus`. The official
   wiki keeps the module `_CORPUS_CACHE` unchanged.
6. **The nonce frame.** Every reply that carries text from a mounted or
   anonymous wiki is wrapped in one frame `Scripts/mcp-wiki.py:_frame_reply`:
   - The first line reads `> [mounted:<nonce>] untrusted reference text ...`
     and the last line reads `> [end mounted:<nonce>]`. That covers `search`,
     `get_page`, `list`, `stats` and `source_to_pages`.
   - `nonce = secrets.token_hex(16)` (32 hex characters) is drawn per reply,
     so page text cannot spell the end marker.
   - The body is truncated under a `max_answer_chars` reduced by the frame's
     cost plus `FRAME_SLACK = 100` for the truncation note, and the end marker
     is appended after that cut. If the caller's ceiling
     is smaller than the frame, the frame still wins and the reply says so
     ([[0013-the-ceiling-is-a-payload-class]]).
   - Official hits that the merge interleaves sit inside the frame, tagged
     `(wiki: official)`.
   - `mount`, `unmount` and `instances` replies carry only escaped names,
     paths and counts and no page text, so they are not framed. A mounted
     description line in `source_to_pages` starts with `   · `.
7. **Every echo is escaped and clipped.** Every untrusted string a mounted
   instance makes the server print goes through
   `Scripts/mcp-wiki.py:_mount_text`. That covers frontmatter scalars, relpaths,
   headings, snippets, anchors, type and status group keys, scope names, the
   mounted path, error text, the mounts-file path and its log line. The order is
   fixed:
   - clip to `MOUNT_TEXT_CLIP = 200` characters;
   - escape in the [[0016-a-cell-may-not-forge-a-boundary]] vocabulary
     (backslash first, then the pipe, `\n`, `\r` and `\t`), neutralise `[ ] < >`
     and turn a backtick into `'`;
   - render the invisible and format characters as `\xNN` / `\uXXXX`. That is
     the Unicode categories Cc, Cf, Cs, Co, Cn, Zl and Zp plus an explicit set
     of bidi, zero-width, variation-selector and tag characters, and ten
     code points that render blank (U+3164, U+115F, U+1160, U+FFA0, U+2800,
     U+17B4, U+17B5, U+180B-U+180D);
   - append the ellipsis last.

   The vocabulary is hand-written per host, as ADR 0016 requires. A mounted
   page's five scalar fields are also coerced to strings at build time, because a
   hostile page can write `title: [a, b]`, and a blank value counts as missing
   `Scripts/mcp-wiki.py:_mount_scalars`.
8. **`root` becomes an anonymous instance (option B).** If a `root` resolves to
   the official directory by `(st_dev, st_ino)` identity, not by spelling, it is
   the official wiki. Its path is the canonical wiki path, which the registry,
   git and the writers are keyed on, and only its label is the raw string the
   caller wrote, which keeps replies byte-identical. Any other `root` is an
   anonymous read-only instance named `(root)`. That name cannot match the name
   pattern, so it can never be registered or forged. It passes the same path and
   identity rules as a named mount (`_mount_vet_path`, `_mount_vet_identity`).
   Its hit tag is `root:<8 hex>`, the first 8 hex characters of the SHA-256 of
   its resolved path, so the banner never echoes a directory name the tree's
   author chose. It is held in a
   registry-owned cache of `MAX_ANON_ROOTS = 1` entry, and a new key evicts the
   old one `Scripts/mcp-wiki.py:_MountRegistry.admit_anon`. `root` and `wiki`
   together are refused. `--strict` still rejects a root outside the project.
   The `wiki_root` argument of `handle_wiki_call` is always the official wiki,
   wherever it points `Scripts/mcp-wiki.py:handle_wiki_call`. That is what the
   CLIs pass, so `freshness.py`, `reindex.py` and `measure_cli.py` are unchanged.
9. **Persistence belongs to the owner, and the server only reads it.** A model
   `mount` lives for the server process. `persist` is refused, through
   `_bool_param`, so `"true"`, `"1"` and `"yes"` are refused too. Only the owner
   makes an entry outlive the process, by editing the file named by
   `--mounts-file`, which has no default. The file must be an absolute path, and
   a non-absolute one exits 1 at launch. Its shape is
   `{"version": 1, "mounts": [{"name": ..., "path": ...}]}`, at most 64 KiB and
   at most `MAX_MOUNTS` entries `Scripts/mcp-wiki.py:_load_mounts_file`.

   The file is read once at start, and only if all of these hold:
   - it is a regular file owned by this euid, with no group or world bits;
   - its parent is held by fd, owned by this euid or root, and not group- or
     world-writable;
   - its realpath equals its path, so no symlink lies anywhere in it;
   - it is outside the project root, the wiki directory and every entry's
     directory.

   It is parsed with the fleet's strict JSON hooks plus an `object_pairs_hook`
   that refuses duplicate keys. Any failure, `RecursionError` included, disables
   persistence with one sanitised log line, and the server still starts. The
   server never writes the file.

   Entries are reserved as pending names at start, and no tree is walked. The
   first use validates an entry with the same rules as `mount`
   `Scripts/mcp-wiki.py:_activate_persisted`. A missing directory (`ENOENT`,
   `ENOTDIR`) or a rule failure marks it `invalid` for good, and the name stays
   reserved. A retryable failure (the root cannot be opened, a stat fails with
   another errno, the walk hits its deadline) leaves it `pending`, and the next
   use tries again. A persisted entry cannot be unmounted, and its name cannot
   be taken by a model `mount`, whatever path it names.
10. **Identity decides the dedupe.** A mount is refused in any of these cases
   `Scripts/mcp-wiki.py:_mount_vet_identity`,
   `Scripts/mcp-wiki.py:_MountRegistry._overlap`:
   - its `(st_dev, st_ino)` equals the official directory's, another mount's or
     a persisted entry's (this covers case variants, symlinks and bind aliases);
   - it lies inside the official wiki directory or contains it;
   - it is the project root or one of its ancestors;
   - it lies inside another mount or contains one.

   A pending persisted entry has no identity yet, so it is compared by the
   realpath it declared in the mounts file. The owner's entry always wins: when
   it activates, a session mount that conflicts with it (one admitted under an
   alias spelling the realpath check could not see) is evicted copy-on-write,
   and a call already holding that mount finishes on its snapshot. A conflict
   with another persisted entry or the official wiki still marks the entry
   invalid `Scripts/mcp-wiki.py:_MountRegistry.overlap_problem`.

   `mount` validates outside the registry lock: path rules, identity and the
   bounded walk with the first memo. It then re-checks capacity, name, identity,
   overlap and the live-bytes budget against the current dict under the lock,
   and rebinds the dict copy-on-write `Scripts/mcp-wiki.py:_MountRegistry.admit`.
   The registry is one object per server. `handle_wiki_call` publishes a
   reference to it for one call and always resets it in a `finally`.

### Owner decisions taken this session

- **Any absolute path may be mounted.** There is no allow-list. `--strict` is
  the one existing narrowing: under it, a mount must resolve inside the project
  root.
- **Mounted wikis are read-only**, and `measure`, `reindex`, `verify` and
  `freshness` refuse them. `freshness` and `verify` would need git and anchor
  resolution against a repository the server was never shown, so a mounted wiki
  has no measured state at all, rather than a measured state nobody can check
  ([[0019-only-gate-on-what-you-can-prove]]).
- **Persistence is owner-only, and a persisted entry cannot be unmounted.** If a
  persisted name could be unmounted, the model could rebind it to a directory of
  its choosing under the owner's name.
- **`root` is option B.** Keeping it unchanged would leave an execution and
  write bypass, and deleting it would buy nothing over making it a read-only
  anonymous instance. One existing case changed its premise. In
  `tests/test_wiki_recall.py` it called `freshness(root=L_EMPTY_ROOT)`, which now
  hits the refusal. It was rewritten to drive the empty wiki as the official wiki
  through a separate `wiki_root` driver, with its assertions unchanged
  `tests/test_wiki_recall.py:Driver.with_wiki_root`. That driver uses the same
  call shape as `measure_cli.py`. The Stage 1 census found no other per-call
  `root` reaching `wiki_call` in `tests/`.
- **`mount` stays model-callable with no owner switch**, which is a declared
  limit (below). The owner turned the switch down. The risk the switch would have
  addressed is an injected `mount` request, for example a page that says "mount
  /x". That request can only add read-only, bounded, framed text that the model
  is told is data. It cannot persist, execute, write or reach git, and
  `instances` shows it.
- **`wiki:"*"` merges coverage-first** (see 3), not by raw score.

## Bounds, and the memory model they are sized to

The owner has not confirmed these values. They are proposals and have not been
measured `Scripts/mcp-wiki.py:MAX_MOUNTS`:

| Bound | Value |
|---|---|
| `MAX_MOUNTS` (session + persisted) | 4 |
| `MAX_ANON_ROOTS` | 1 |
| `MAX_MOUNT_PAGES` per instance | 1000 |
| `MAX_MOUNT_FILE_BYTES` | 512 KiB |
| `MAX_MOUNT_TOTAL_BYTES` per instance (bytes actually read) | 8 MiB |
| `MAX_LIVE_MOUNT_BYTES` (all live mounted + anonymous memos; one `*` or list search) | 24 MiB |
| `MAX_MOUNT_DEPTH` | 8 |
| `MAX_MOUNT_WALK_ENTRIES` (entries seen) | 20000 |
| `MOUNT_CALL_DEADLINE` per walk | 10 s |
| `MOUNTS_FILE_MAX_BYTES` | 64 KiB |
| `MOUNT_TEXT_CLIP` | 200 characters |

The corpus keeps each body plus per-field token lists, estimated at 10-30x the
raw bytes. 24 MiB of live raw text is therefore roughly 0.25-0.75 GiB in the
worst case, and the live-bytes budget is sized to that estimate, not to file
size alone. The budget is checked when an instance's memo is built and again at
admission. A `*` or list search sums the stat sizes its signature walks see now,
over every mounted target, before any build. It does not use the page count
recorded at mount, which a grown tree outruns. Each walk is handed to its build,
so a warm `*` search costs one bounded walk per mount. There is no separate page
bound for `*`. Caps are re-checked on every walk, and a tree that grew past one
is refused by name. `unmount` and `instances` never walk, so a grown tree can
always be listed and dropped.

## Alternatives rejected

- **One shared df/idf corpus.** A foreign wiki would shift the official idf and
  move the calibrated 0.55 gate under every official query.
- **A launch-flag root allow-list.** Rejected by owner decision: any absolute
  path may be mounted.
- **An owner switch that forbids a model `mount`.** Rejected by owner decision
  and declared instead (see Declared limits).
- **Model-writable persistence, and unmounting a persisted entry.** A file the
  server writes is a write surface. A persisted name that can be unmounted can be
  rebound by the model.
- **Git classification, `freshness` and `verify` for mounts.** A foreign
  `.git/config` is not trusted, a mount's anchors name a repository nobody showed
  the server, and a second repo would thrash the one-key `_FRESH_CACHE`.
- **Keep `root` as it was, or delete it.** Keeping it is an execution and write
  bypass, and deleting it buys nothing over option B.
- **Containment by path, with `os.path.realpath` per file.** That costs about
  1000 files times depth 8 of `lstat` per signature call. It also leaves a
  check-then-use gap on every intermediate directory. The fd-relative walk needs
  no per-file realpath and closes the gap.
- **Merge by raw BM25F score.** Scores from different idf spaces are not
  comparable. The merge key is coverage first.

## Review dispositions

The plan went through two Poirot rounds and 42 Elliot candidates in three
lanes: FS F1-F14, PR F1-F12 and EX F1-F16.

- **Fixed: 37**, each folded into a step with a test and a threat-table row.
- **Declared only: 3.** PR F1: the persist ban against same-uid writes is
  mitigated by the `persisted` label and by refusing a file inside the project or
  wiki directory. PR F11: a reader that is still running when `unmount` returns.
  EX F14: the CLIs run with `strict` false.
- **Refuted, with a declared residue: 2.** EX F4: `McpServer.__init__` already
  stores the project root's realpath, and the CLI passes its own project root and
  fails closed. EX F7: no mounted or anonymous path reaches git (dynamic tests
  per handler). The official `iter_pages` following a symlinked `.md` predates
  this feature, and the official tree is trusted.

Several fixed items also leave a declared residue:
- FS F7: a writer to a tree can deny that one mount;
- FS F8: inode reuse;
- FS F9: the official signature is still unbounded;
- FS F14: a hung syscall cannot be interrupted;
- PR F3: ancestors above the mounts file's parent are not checked;
- PR F10: same-uid and root writers.

Poirot round 2 (H1, M1, M5, M6, L1, L2, L4-L6) is folded in. M2, M3, M4, L3 and
I3 were not in the message the plan received and are not itemised.

## Deviations from the plan

- **No page bound for `*`.** The plan's draft had a `MAX_STAR_PAGES`. The code
  has none: the `*` and list bound is the read-time byte bound
  `MAX_LIVE_MOUNT_BYTES` alone.
- **A directory below the project root may be mounted.** The plan refused
  descendants of the project root as well as ancestors. The code refuses only
  the project root and its ancestors. Under `--strict` a mount must lie inside
  the project, so the planned rule would have refused every strict mount. The
  official wiki directory keeps both refusals.
- **An `Instance` carries more state than the plan typed.** `state`, `reason`,
  `declared` and an `activate_lock` carry the lazy validation of persisted
  entries, and `info` holds the counts `instances` prints
  `Scripts/mcp-wiki.py:Instance`.
- **Scalar coercion.** `_mount_scalars` was not in the plan. It was added so
  that a hostile non-string title, type or status cannot crash grouping, sorting
  or tokenising.
- **The platform gate is decided once, at import.** The dir_fd support check
  matches the platform functions by name, so a wrapped `os.open` does not change
  it. A platform without dir_fd refuses every mounted read.
- **Test arithmetic.** The plan's case lists for groups V, X and Y did not match
  their stated counts. The tests were written to the stated counts: two V items
  merged, two X items merged and one Y item split.

## Tests

The new cases are groups U-Z in `wiki_recall`
`tests/test_wiki_recall.py:uz_group_u` through
`tests/test_wiki_recall.py:uz_group_z`:

| Group | Cases | What it covers |
|---|---|---|
| U | 30 | registry, names, resolver, tables, static gates |
| V | 33 | mount validation, the contained walk, bounds, echo hygiene |
| W | 23 | `wiki` per function, `root` folding, anonymous instances |
| X | 28 | refusals and the execution boundary, incl. dynamic per-handler no-git checks |
| Y | 18 | search merge and the frame |
| Z | 30 | the mounts-file loader and concurrency |

That makes 162 new cases, and the typed count moved from 173 to 335 in the same
change `tests/run.py:SUITES`. The rewritten freshness case is an existing case and
is not one of the 162. Two cases record INFO when the platform cannot build their
premise: a page on another device and a mounts file with the wrong owner, which
both need root. The harness counts INFO rows against the typed count.

Measured red first: 335 cases ran as 173 pass (A-T, including the rewritten case),
160 fail and 2 info.

Other suites that changed:
- `mcp_footprint` gained four description cases (untyped);
- `read_loop` names the registry lock, the per-instance build lock and the
  single-tuple memo `tests/test_read_loop.py`;
- `generated_region` declares the mounts-file `json.loads` as a strict-JSON
  exception `tests/test_generated_region.py:STRICT_JSON_EXCEPTIONS`, and since
  the security review a driven `STRICT_SITES` row proves that the loader passes
  the strict hooks `tests/test_generated_region.py:_site_wiki_mounts`.

The concurrency audit in the server's `run` covers the registry, the memo, the
instance state and the ContextVar ([[0008-a-serialized-read-loop-looks-like-a-dead-server]]).

The tool description gained the three functions and one paragraph on `wiki`.
The trigger paragraph is byte-intact
([[0003-the-trigger-travels-with-the-tool]]).

## Code-mode security review and its fix rounds

The implementation went through a code-mode security review on 2026-11-24
(run 20261124-1700). Four find lanes (parseval, injection,
fs-containment-toctou, prompt-injection-rendering) produced 35 raw candidates,
deduped to 32 (F1-F32). A fresh verifier checked each one by reasoning from the
code; no measurement was run during verification. Verdict: APPROVE with fixes,
no HIGH, one MEDIUM.

Fixed, each red first in group AA of `wiki_recall`
`tests/test_wiki_recall.py:uz_group_aa` (25 cases, plus 3 in the second round):
- **F2 (MEDIUM).** The wikilink regex was quadratic: a 512 KiB flood of `[[`
  took 1100.6 s in the red run. A `find()` scan replaces it and takes 0.000 s on
  the same input, and its equivalence with the regex was fuzzed on 4000
  strings. The same scan is mirrored in
  `ClaudeCode/skills/wiki/scripts/_wikilib.py`, because `wiki_index` compares
  the two as AST.
- **F1.** A dict-valued `links` no longer breaks `reindex_collect`.
- **F3.** A blank frontmatter value counts as missing in `_mount_scalars`.
- **F13.** A non-string root or one containing NUL is refused in
  `_resolve_root`.
- **F10, F11, F12.** The four official-only functions refuse a mounted or
  anonymous target before activation, stat, walk or `admit_anon`, and a root
  that is official by identity returns the canonical wiki path (Decision 2, 8).
- **F15.** Retryable activation failures keep a persisted entry pending, and a
  pending entry is compared by its declared realpath. In the second round a
  persisted entry always wins over a session mount (Decision 9, 10).
- **F25.** The anonymous root label is `root:<8 hex>` (Decision 8).
- **F27, F28.** Mounted snippet and description lines start with `   · `, and
  merged hit lines start with their `(wiki: ...)` tag (Decision 3, 6).
- **F16.** A driven `STRICT_SITES` case proves the strict JSON hooks of
  `_load_mounts_file` (Tests).

Hardened, though verified INFO or suppressed:
- F4: `get_page` on a mount tries the exact relpath first.
- F5: an anonymous root passes `_mount_vet_path` and `_mount_vet_identity`.
- F6: `_activate_persisted` catches `Exception`.
- F7: ten blank-rendering code points are shown escaped (Decision 7).
- F8: a numeric string longer than 64 characters is not parsed.
- F9: an invalid reason is escaped once.
- F26: the tool description says a framed reply is one carrying page text, and
  that `mount`, `unmount` and `instances` carry only escaped names, paths and
  counts.
- F29: dispatcher error text goes through `_mount_text` when a registry is
  attached.
- F30: `FRAME_SLACK = 100` is part of the frame overhead.
- F31: the dead `skipped_at_load` register is deleted.
- F32: the nonce is `token_hex(16)`, and the frame head regex expects 32 hex
  characters.

Suppressed after verification:
- F4-F8, F9, F12, F26, F29, F30, F31: suppressed by the verifier and hardened
  anyway (above).
- F14: the CLI path has no registry, and it is an operator-run single shot.
- F23: the identity pin is a tripwire; containment comes from the fd-relative
  walk.
- F24: the registry is copy-on-write; the one unlocked read in `_overlap` only
  feeds the dedupe.
- F32: the nonce already made the end marker unforgeable at a 2^-64 guess.

Deviations in the fix rounds:
- **F15's retry rule is narrower than first proposed.** `ENOENT` and `ENOTDIR`
  stay final, so an owner's entry for a deleted directory does not retry on
  every call.
- **F8's cap is 64 characters.** The first draft capped numeric strings at 12,
  which broke the official wiki's byte identity for 13-character zero-padded
  input. A fresh review caught it, and the cap was raised to 64.
- **Persisted-wins eviction was added after review.** The first F15 fix latched
  the owner's entry `invalid` when a session mount had taken an alias of its
  directory first. The second round evicts the session mount instead.

After both rounds the typed `wiki_recall` count is 363
`tests/run.py:SUITES`, which ran as 361 pass, 0 fail and 2 info.
`WIKI_DESC_CEILING = 12500` in `tests/test_mcp_footprint.py` is an agent
proposal the owner has not confirmed.

## Declared limits

- **`mount` is model-callable, with no switch.** The mitigations are the nonce
  frame, the sanitised echoes, the persist ban, `MAX_MOUNTS`, the byte budget and
  `instances`.
- **"Owner-only" binds this tool and nothing else.** The model's other tools
  run as the same uid and can write the mounts file. The server labels an entry
  `persisted (listed in the mounts file; the server cannot tell who wrote it)`,
  never "vetted".
- **Same-uid and root writers.** They can change a tree under a mount, and inode
  reuse after a delete and recreate passes the `(st_dev, st_ino)` pin (F23).
- **`unmount` is not a synchronisation point.** A call already reading an
  instance finishes on its snapshot.
- **Hung syscalls cannot be interrupted.** Python cannot interrupt a hung
  network-mount syscall, because the deadline is checked between entries. A
  single directory with millions of entries is read lazily and the cap fires, but
  its kernel cost is not bounded. A hung network filesystem blocks the calling
  worker and holds that instance's `build_lock`; `O_NONBLOCK` does not help a
  stalled mount. FIFO leaves are safe (`O_NONBLOCK` plus `S_ISREG`) (F18).
- **The mounts-file loader can stall startup.** It realpaths each entry
  synchronously at start, so a dead NFS path in the owner's file can stall
  `initialize` (F19).
- **The live-bytes budget is a soft check-then-act.** Concurrent builds of
  different instances can overshoot `MAX_LIVE_MOUNT_BYTES`, bounded by
  `MAX_MOUNTS` x `MAX_MOUNT_TOTAL_BYTES` (32 MiB raw against 24 MiB) (F17).
- **A warm call re-walks every mounted tree it reads** once for its signature,
  bounded by 20000 entries and 10 s (F22).
- **One narrow unlocked read.** `_overlap` reads activation fields without the
  lock (F24).
- **A persisted activation can fail for the process lifetime.** A
  `MemoryError` or `RecursionError` during activation marks the entry `invalid`
  until restart, and the `activate_lock` is held for a full walk on a persisted
  tree that always hits the deadline.
- **Directory names still reach the registry replies.** The banner carries no
  directory name, but a name the tree's author chose can still appear, escaped,
  in `mount` and `instances` output.
- **Hostile pages inside the project can be mounted.** A model can mount a
  project-internal directory that holds attacker-written `.md`, a cloned repo for
  example, even under `--strict`.
- **Hard links, other devices and symlinks are skipped, not read.** Pages with
  more than one hard link, pages on another device and symlinked pages are lost
  to the mount. They are counted and reported.
- **The official wiki's own residues are unchanged.** Its signature walk is
  unbounded, its `iter_pages` follows a symlinked `.md`, and an unknown-function
  error echoes the model-supplied `function` raw.
- **The mounts file's ancestors are only partly checked.** Ancestors above the
  file's parent directory are not permission-checked one by one.
- **The mounts-file location refusal compares strings.** The inside-project,
  inside-wiki and inside-entry refusal compares canonical path strings after
  symlinks are resolved, so a case-different spelling on a case-insensitive
  filesystem and a hard link or bind mount of the file are not detected (F20).
- **The mounts-file open races on intermediate components** between the
  realpath and the path-based open of the parent. The fd-based owner and mode
  checks on the parent and the leaf still bind the result, and a same-uid
  attacker can edit the file anyway (F21).
- **No cross-instance `[[slug]]` resolution, no remote wikis, no
  `tools/list_changed`.**

## Open for the owner

- The bounds table above, and the 10-30x memory model behind it.
- `WIKI_DESC_CEILING = 12500` in `tests/test_mcp_footprint.py`.
- Whether `--strict` containment of `mount` is enough as the only narrowing.
- Whether losing hard-linked and cross-device pages is acceptable.
