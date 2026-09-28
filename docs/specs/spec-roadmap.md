---
name: spec-roadmap
type: spec
status: deprecated
title: p:roadmap — a single-writer roadmap skill wired into the wiki
description: SHIPPED in f581b1b (2026-09-28) and kept as a record rather than a live plan — a stdlib-only single-writer roadmap.py owning docs/roadmap/roadmap.md plus one immutable archive page per closed item, two new wiki page types that INDEX.md counts instead of listing, the page-type constants homed in _wikilib with a server parity gate, and two new test suites written red first. Its path:line anchors are not maintained; the decision lives in ADR 0022.
sources:
  - ClaudeCode/skills/roadmap/SKILL.md
  - ClaudeCode/skills/roadmap/scripts/roadmap.py
  - ClaudeCode/skills/wiki/scripts/_wikilib.py
  - ClaudeCode/skills/wiki/scripts/reindex.py
  - ClaudeCode/skills/wiki/scripts/freshness.py
  - ClaudeCode/skills/wiki/SKILL.md
  - ClaudeCode/skills/_lib/handoff-contracts.md
  - Scripts/mcp-wiki.py
  - tests/test_wiki_index.py
  - tests/test_roadmap.py
  - tests/test_table_cells.py
  - tests/run.py
  - tests/README.md
  - project-forge.yaml
verified:
  commit: f581b1b
  date: 2026-09-28
links:
  - 0022-a-someday-maybe-is-a-roadmap-item
  - wiki-engine
  - tests
  - skills
  - requirements-yaml
  - 0013-the-ceiling-is-a-payload-class
  - 0019-only-gate-on-what-you-can-prove
---

# Feature Implementation Plan: p:roadmap — a single-writer roadmap skill wired into the wiki

> **SHIPPED, AND NOT MAINTAINED. Read this as a record, not as instructions.**
> The plan was executed in `f581b1b` (2026-09-28); the decision is recorded in
> [[0022-a-someday-maybe-is-a-roadmap-item]], and the living WHAT/HOW is the roadmap
> SKILL.md and the roadmap.py docstring. Where the implementation deviated from this text
> (the dirty-flag FIFO case, the nested-JSON fixture size, the R3/R20 test groups, the
> display path of a new item), the code and ADR 0022 are right and this page is not.
> Its path:line anchors are left as they were written.
>
> **Status at writing: draft, not built.** The design decisions are locked. The source is the design
> session of 2026-09-28 plus "Decisions taken after exploration" 1-9, and nothing here reopens
> them. Where this plan picks an implementation detail the design left open, it is marked
> **[IMPL-CHOICE]**.
>
> **Sequencing is test-first.** Every gate is written first and observed RED before the code
> that turns it green. The risk register (5.5) ties every risk to the suite:group that gates it.
>
> **Anchor convention for this page.** `wiki_call verify` resolves every backticked body span
> shaped like a repo path (`Scripts/mcp-wiki.py:1561-1579`, used at `Scripts/mcp-wiki.py:1677`).
> A span naming a file that does not exist yet would therefore be a gating `broken-anchor`.
> So existing code is cited in backticks as `path:line` (line refs resolve while the file
> exists). Files and symbols this plan CREATES are written in plain text, or in bold on the
> "Files" lines, never in backticks. Bare basenames such as `roadmap.py` carry no slash and are
> never treated as anchors.

## 1. Requirements Summary

### Functional Requirements
- [FR-1] A generic plugin skill in the directory ClaudeCode/skills/roadmap/, with a SKILL.md and a bundled stdlib-only single-writer script scripts/roadmap.py.
- [FR-2] `roadmap.py` owns docs/roadmap/roadmap.md (type `roadmap`, live items only). It creates docs/roadmap/archive/NNNN-slug.md files (type `roadmap-item`), one per closed item, where NNNN is the item id.
- [FR-3] Commands:
  - `init`, `add` (value flags, OR `--item-file PATH`), `list [--state S] [--horizon H] [--untriaged] [--ready]`, `show <id>`;
  - `move <id> <lane> [--state S] (--reason R | --reason-file PATH)`, `rank <id> (--before <id> | --top)`;
  - `link <id>` (spec / origin / blocked_by / follows; value flags, OR `--item-file PATH` carrying `origin` / `spec` / `blocked_by`, S2-1);
  - `close <id> (--commit SHA | --reason TEXT | --reason-file PATH) [--slug SLUG]`, `render`;
  - `wip <N> (--reason R | --reason-file PATH)` (grafted, see 5.4);
  - `export [--out PATH] [--closed-since DATE | --open-only]`;
  - global options `--file PATH` and `--today YYYY-MM-DD`;
  - plus the skill-level `adopt` op. The main context exports the roadmap to a staged JSON file, `p:minion-explorer` (Scott, read-only, no Bash) harvests candidates and dedups them against that file, the user approves, and the main context stages each approved candidate and runs `add --item-file` (5.4, "Untrusted text never reaches a command line").
- [FR-4] Item model:
  - id `R-NNNN`, monotonic and never reused (next id = max over roadmap.md items AND archive filenames, plus 1);
  - `title`; `state` (`idea|planned|active|done|dropped`); `horizon` (`now|next|later|unset`);
  - `origin` (required, also the dedup key); optional `spec`, `follows` and `severity`;
  - `blocked_by` and `tags` (always written as inline lists);
  - `why` (markdown), an append-only log, and the close data.
- [FR-5] Invariants the writer enforces:
  1. no horizon => state `idea`;
  2. `active` => horizon `now`;
  3. `done`/`dropped` exist only in the archive;
  4. `planned` is allowed in any of `now` / `next` / `later` (never in the inbox, by invariant 1).
- [FR-6] Priority:
  - horizon lanes;
  - a WIP cap on `now`, stored as the flat frontmatter scalar `wip_now` (starting value 3, marked unargued);
  - the in-lane total order is the block order;
  - a Kahn cycle check on `blocked_by`;
  - every lane or state change requires `--reason` and appends one log line.
- [FR-7] Close:
  - `--commit` is verified via `git cat-file -e` and stored as the full sha (40 hex digits in a SHA-1 repository, 64 in a SHA-256 one);
  - `--reason` => dropped;
  - the archive file is created exclusively FIRST, then roadmap.md is replaced;
  - duplicate ids are detected on every run, and the archive copy wins;
  - no reopen.
- [FR-8] Optimistic lock over two digests: the roadmap.md bytes and the sorted archive listing. It is re-checked immediately before every durable write, and twice in `close`.
- [FR-9] JSON export `roadmap-export/1`:
  - read-only and deterministic (no wall clock, sorted keys, fixed order);
  - the counts describe the requested scope;
  - exactly one derived field, `ready`.
- [FR-10] Wiki integration:
  - the types `roadmap` and `roadmap-item` join TYPE_ORDER and UNTRACKED_TYPES;
  - INDEX.md never lists a `roadmap-item` page and renders ONE count line instead;
  - `roadmap-item` is exempt from the orphan rule;
  - the page-type constants live in `_wikilib.py` on the skill side, with a parity gate against `Scripts/mcp-wiki.py` on the server side. The gated constants are TYPE_ORDER, UNTRACKED_TYPES, INDEX_LABELLED, STATUS_FORBIDDEN, ORPHAN_EXEMPT_TYPES and INDEX_COUNTED_TYPES.
- [FR-11] Two new suites, `wiki_index` and `roadmap`, registered per `docs/subsystems/tests.md:229-230`.
- [FR-12] Documentation:
  - wiki SKILL.md §1/§2/§3/§5/§8;
  - ADR 0022;
  - a handoff-contracts entry plus file rows;
  - the skills.md and tests.md rosters, and tests/README.md;
  - the `wiki-engine.md` anchor move.

### Non-Functional Requirements
- [NFR-1] `roadmap.py` is stdlib only and needs Python 3.9+, with no version guard. It follows `checkpoint.py` house style: 4-space indent, %-formatting, no type hints. Edits to `_wikilib.py`, `reindex.py` and `freshness.py` keep THOSE files' style: TAB indent, `from __future__ import annotations`, type hints.
- [NFR-2] Every refusal has the same shape:
  - exactly one stderr line prefixed `roadmap: `, exit 2, no traceback;
  - target files byte-unchanged, and no `.roadmap-*.tmp` left behind.
- [NFR-3] `roadmap.py` never imports `_wikilib` (another skill's directory) and holds no copy of its SKIP_DIRS/SKIP_FILES. Everything it writes still parses identically under both wiki frontmatter parsers.
- [NFR-4] Strict UTF-8 on read and write, independent of locale:
  - `emit()` and `emit_raw()` are the only stdout writers and write UTF-8 bytes through `sys.stdout.buffer`;
  - `die()` and `note()` do the same on stderr, and are the only functions that name `sys.stderr`;
  - `check_line` and `check_why` refuse lone surrogates (U+D800-U+DFFF), which `json.loads` produces from a `\ud800` escape and Python's argv decoding produces from non-UTF-8 bytes, so no USER VALUE that reaches a file can make the strict UTF-8 encode in `write_atomic` / `create_exclusive` raise (M4, S2-3);
  - PATHS are not user values and are not refused: a `--file`, `--out` or staged-file path given as non-UTF-8 bytes arrives with surrogate escapes (U+DC80-U+DCFF), and so does a filesystem name. The stream route therefore encodes with `errors="backslashreplace"` (`_write_stream`, 5.1): a message naming such a path is written with the surrogate spelled as the text `\udcff`, never raises, and stays one line (round-3 M3). The strict encode stays on the FILE route; the backslash-replace applies only to what is written to stdout/stderr;
  - the U+00B7 in item headings must never depend on `LC_ALL`.
- [NFR-5] Every `git` spawn goes through the one `git(args, cwd)` helper, which:
  - prefixes `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c protocol.allow=never` (`GIT_SAFE_ARGV`) and sets `GIT_NO_LAZY_FETCH=1` in the child environment, as cheap, DECLARED hardening, not a gated guarantee (S-L1, S4-3). The trust boundary is stated in 5.4 ("The local git config is trusted"): the target repository's `.git/config` is trusted, because clone does not transfer it and write access to it already equals code execution as the user;
  - is never asked to run an index-refreshing command: roadmap.py never runs `git status`, `git diff` or `git add`. The export's `source.dirty` is computed in Python from `git ls-tree -r -z HEAD` and `git rev-parse --show-object-format` (5.3), so no clean/smudge filter driver and no fsmonitor hook configured in the target repository is ever reached (S2-2);
  - passes `stdin=subprocess.DEVNULL` and `timeout=GIT_TIMEOUT_SEC`;
  - puts `--` before every pathspec;
  - receives a sha only after `SHA_RE` has matched it, so no user value can reach git's argv as an option.
- [NFR-10] **No shell, anywhere.** roadmap.py spawns only `subprocess.run` with an argv LIST. It never passes `shell=True` and never calls `os.system`, `os.popen` or any other shell-backed API; gated by AST (roadmap:A). On the caller side, untrusted or multi-word text (adopt-harvested titles, whys, origins, and any multi-word reason) never appears on the Bash command line that invokes roadmap.py. It is staged in a file under `.claude/tmp/` and passed by path (`--item-file` on `add` and `link`, `--why-file`, `--reason-file`); SKILL.md makes this mandatory and states the EXACT allowlist of inline command-line values (Step 8, S2-1). The script backs the allowlist where it can: `--spec` and `--slug` must match `SLUG_RE` (`[a-z0-9-]`, kebab-case), which every wiki page name already fits.
- [NFR-11] **One line rule.** Every single-line user value (title, every reason, origin, spec, severity, each tag, slug) passes ONE function, `check_line(field, value)`, before it is written. It refuses line separators, other C0 controls, lone surrogates and the backtick (5.4). `check_why` refuses the same separator set except `"\n"`, and lone surrogates.
- [NFR-12] roadmap.py never follows a symlink it would write through: roadmap.md, the roadmap directory, the archive directory and `export --out` are refused when `os.path.islink` is true, and the realpath of the roadmap directory must lie inside the realpath of the git top-level (inside a work tree) or of the wiki root (outside one), so a symlinked ancestor cannot redirect a write out of the tree (`check_containment`, S4-1, 5.4). It never follows a symlink it READS either, and never blocks on a special file: every file read goes through the one section-3 `read_regular(path, cap)` (lstat, `S_ISREG`, `O_NOFOLLOW | O_NONBLOCK` open, fstat re-check, capped read; S3-1, 5.1).
- [NFR-13] **No traceback from IO (the IO error rule, 5.1).** Every IO CALL in a section-3 function (a forbidden callee not in `NONRAISING_IO_CALLS`, or a `.write` / `.flush` method call) sits inside the body of a `try` whose handlers name `OSError`, and the error is converted into one `die()` line of the form `cannot <op> %s: %s` naming the operation and the path, unless the function's contract is to REPORT the condition (`git()` returns an rc, `worktree_dirty` returns `True` / `None`, `wiki_page_names` skips with a note, `read_regular` returns a reason, `_discard` swallows it, `_write_stream` exits with its defined code), in which case it catches and translates explicitly. ALL IO is confined to section 3: the stream writer `_write_stream` lives there too (5.1), and the section-2 functions `die`, `note`, `emit` and `emit_raw` only name a stream and call it. The AST gate confines the IO and checks every call, so the traceback class is closed in one place (roadmap:A). There is deliberately NO catch-all in `main()`: a traceback raised outside section 3 is a bug and must stay visible.
- [NFR-6] No test may reach the live docs/roadmap/ tree. Tests write only under a system `mkdtemp` sandbox.
- [NFR-7] The INDEX.md cost is O(1) in the number of closed items.
- [NFR-8] The export is byte-identical across runs over the same inputs. Tests pin the date with `--today`.
- [NFR-9] Layer purity inside `roadmap.py` (5.1). Pure sections never touch the filesystem, processes or the clock, and this is gated by AST.

### Success Criteria
- [SC-1] `python3 tests/run.py wiki_index roadmap` passes with no case-count drift. `forge_call test all` is green, including `table_cells`, `name_existence`, `spawn_stdin`, `checkpoint` and `wiki_recall`.
- [SC-2] Each new gate was observed RED before its implementation landed. The step's verification line and the commit message record it.
- [SC-3] `render_index` in `Scripts/mcp-wiki.py:2287` and in `ClaudeCode/skills/wiki/scripts/reindex.py:78` produce byte-identical output on the same fixture corpus. Neither ever lists a `roadmap-item`, and both render the one archive-count line.
- [SC-4] A crash injected between the archive create and the roadmap.md replace is repaired on the next write (the archive wins) and reported.
- [SC-5] A concurrent write between read and replace aborts with exit 2 and leaves the concurrent writer's bytes in place.
- [SC-6] A golden export fixture is reproduced byte-for-byte, twice.
- [SC-7] The live docs/roadmap/ tree (if present) has the same digests before and after the `roadmap` suite.
- [SC-8] `roadmap.py show` of an item whose heading carries U+00B7 succeeds under `LC_ALL=C` / `PYTHONIOENCODING=ascii`.

### Assumptions
- The deployed script path is `~/.claude/skills/p/skills/roadmap/scripts/roadmap.py`, the same symlink scheme as checkpoint (`ClaudeCode/skills/checkpoint/SKILL.md:66-82`).
- `git` is present on developer machines. The suite records INFO (not FAIL) for its git-backed cases when it is absent.
- The running `mcp-wiki` server is restarted after Step 3 lands (Step 10).
- The wiki root of a roadmap file is the parent of its directory (docs/roadmap/roadmap.md -> docs). This is enforced, not assumed: a `--file` whose path is not shaped `<wiki root>/roadmap/roadmap.md` is refused, and so is one whose wiki root would be the filesystem root. A `--file` elsewhere (for example `~/Code/roadmap.md`) would otherwise make its grandparent the wiki root, and the name scan would walk it.
- `--file` omitted means DEFAULT_FILE joined to `git rev-parse --show-toplevel` of the cwd, falling back to the cwd.

### Out of Scope (post-implementation follow-ups)
These become the first roadmap items once the live roadmap exists.
- **Bootstrapping the live roadmap.** This is a follow-up that requires the user's approval. First `roadmap.py init` creates docs/roadmap/roadmap.md. Then `/p:roadmap adopt` produces candidates, the user approves them, and the main context stages each approved candidate as a file and runs one `add --item-file` per candidate. Neither is a step of this plan.
- Producer hooks, each a separate skill change with its own `_lib/handoff-contracts.md` entry:
  - ADR authoring (deferred limits);
  - Quint's "diagnosis only" reports;
  - the `p:checkpoint` / `p:recap` `open:` fields;
  - `p:code-review` / `p:branch-review` unfixed findings.
- A kanban board. Only the export contract ships.
- Commands that edit an item's title, why, tags or severity after `add`. The design's command list does not include them.
- Roadmap candidate: `_wikilib.git()` already passes `stdin=DEVNULL` (`ClaudeCode/skills/wiki/scripts/_wikilib.py:40`) but has NO timeout, unlike the server copy (`Scripts/mcp-wiki.py:363`, `:368-369`). The survey docstring at `tests/test_spawn_stdin.py:71-72` still says only the MCP copy got the `stdin=DEVNULL` fix, which is stale.
- The `GIT_SAFE_ARGV` / `GIT_NO_LAZY_FETCH` hardening that roadmap.py's `git()` gets (NFR-5) is NOT applied to `_wikilib.git()` or to the server's git helper (`Scripts/mcp-wiki.py:349-369`). Risk if left: those helpers run `git` in the wiki's own repository, whose `.git/config` the user controls, so the exposure is lower than for a generic plugin script run in any repository; a roadmap candidate, not this plan's work.
- The `DETAIL_STATUSES` duplication (`ClaudeCode/skills/wiki/scripts/freshness.py:40` vs `Scripts/mcp-wiki.py:146`).
- The forge `syntax` target does not compile `ClaudeCode/skills/**` (`project-forge.yaml:26`).
- `TYPE_SIGNAL_TOKENS` entries for the two new types (`Scripts/mcp-wiki.py:235`). An absent key is "silent, not wrong" (`tests/test_wiki_recall.py:4583-4598`).
- Known side issue, which the user decides: references to the old plan path, which meant the sandbox-run record (now `spec-sandbox-run`), and now resolve to THIS page:
  - `[[feature-implementation-plan]]` in `docs/subsystems/skills.md:15`, `:58`. The orchestrator fixes these links after planning; this plan only notes them.
  - `docs/reference/requirements-yaml.md:16`, `:38`.
  - `docs/concepts/layer-contract.md:120`, which names the path as the pinned handoff location. The sentence is about the location, not the sandbox-run content, so it may stay true as written; it is listed so that whoever audits the old-path references sees it.
  - The root requirements.yaml also still cites the old plan path.

  Risk if left: a reader following one of these links lands on this roadmap plan instead of the sandbox-run record. It costs a wrong page, not a wrong build.

## 2. Architecture Analysis

### Affected Subsystems
- **Skills.** A new directory ClaudeCode/skills/roadmap/ holds SKILL.md and scripts/roadmap.py. Skill discovery is by directory, so no manifest needs editing.
- **Wiki skill scripts.** `_wikilib.py` gains six page-type constants (after `SKIP_DIRS`, `ClaudeCode/skills/wiki/scripts/_wikilib.py:19-21`). `reindex.py` (`:28-38`, `:65`, `:73-74`, `:78-96`) and `freshness.py` (`:34-35`, `:129`) stop defining them and read them through `w.`.
- **mcp-wiki server.** Its own copies of the constants live at `Scripts/mcp-wiki.py:132-148`. The changes touch `reindex_collect` (orphan rule at `Scripts/mcp-wiki.py:2282-2283`) and `render_index` (`Scripts/mcp-wiki.py:2287-2304`). The server never imports a sibling.
- **Test fleet:**
  - two new suite modules, tests/test_wiki_index.py and tests/test_roadmap.py;
  - their wrappers and SUITES rows in `tests/run.py`, and forge targets in `project-forge.yaml`;
  - a roster change in `tests/test_table_cells.py`, because roadmap.py adds a padded pipe table.
- **Docs wiki:**
  - ADR 0022 and the wiki SKILL.md schema amendments;
  - the roster pages, the handoff contracts and `docs/components/wiki-engine.md`.

### Integration Points
- `roadmap.py` -> filesystem:
  - writes roadmap.md (temp + `os.replace`, lock re-checked just before) and archive files (fsynced temp + `os.link`, exclusive);
  - reads every `*.md` under the wiki root (frontmatter `name:` + basename) for the slug-collision scan, and validates `--spec` against the subset of those files that are wiki PAGES (a parsed frontmatter carrying a `name:`, L8);
  - reads the staged input files `--item-file` (on `add` and `link`), `--why-file` and `--reason-file` (strict UTF-8, size-capped, regular files only, never the roadmap or its archive);
  - reads, for export only, the bytes of every regular file under the roadmap directory to hash them as git blobs, and the link text of a tracked symlink (`worktree_dirty`, 5.3);
  - every one of these reads goes through `read_regular(path, cap)` (S3-1): no symlink is followed, no FIFO or device is opened, no file larger than its cap is read.
- `roadmap.py` -> `git`, all read-only, every call prefixed with `GIT_SAFE_ARGV` (NFR-5):
  - `rev-parse --show-toplevel` (default target, close, export);
  - `cat-file -e <sha>^{commit}` and `rev-parse --verify <sha>^{commit}` (close);
  - `rev-parse --short HEAD`, `rev-parse --show-object-format` and `ls-tree -r -z HEAD -- <roadmap dir>` (export; `source.dirty` is then computed in Python, 5.3). Never `git status`: an index refresh would run the target repository's clean filters and fsmonitor (S2-2).
- `mcp-wiki` / `reindex.py` / `freshness.py` -> roadmap pages, READ only:
  - search indexes them;
  - INDEX lists the roadmap page and one count line for the archive;
  - freshness reports `untracked`.
- The `p:roadmap` adopt op, in four hops (H1):
  1. The main context runs `roadmap.py export --out '.claude/tmp/roadmap-adopt-<ts>.json'` (default scope: live AND archived items; `<ts>` is chosen by the skill and matches `[a-z0-9-]+`, S4-2).
  2. It delegates the harvest to `p:minion-explorer` (Scott) and passes that path. Scott has no Bash (`ClaudeCode/agents/minion-explorer.md:5`, `:20`), so he never runs roadmap.py; he reads the export with Read and dedups candidates by origin against live AND archived items.
  3. Scott returns candidates; the user approves rows.
  4. The main context stages each approved candidate with `purity_call` `create_text_file` under `.claude/tmp/` and runs `roadmap.py add --item-file <path>`. roadmap.py's own origin dedup stays the authority (Scott's is advisory).
- Main context -> `roadmap.py`: the Bash command line carries only the allowlist of Step 8 (the command, flags, ids, lane and state names, a sha, a date, the integer for `wip`, staged-file and `--out` paths single-quoted and matching `.claude/tmp/roadmap-(stage|adopt)-[a-z0-9-]+\.(json|txt)` with names chosen by the skill, never derived from harvested text (S4-2), and `--spec` / `--slug` values single-quoted and matching `[a-z0-9-]+`). Every other value, including a `link` origin, travels only inside staged files (NFR-10, S2-1).
- Export JSON -> a future kanban consumer. The contract lives in SKILL.md, and HTML escaping is the consumer's job.

### Constraints
- `roadmap.py` may not import `_wikilib`. Its frontmatter subset is a deliberate re-implementation, gated differentially (Step 5 group C).
- `Scripts/mcp-wiki.py` may not import `_wikilib`:
  - fleet servers never import siblings;
  - `Scripts/amalgamate.py:75-77` is a closed list of `Scripts/_mcp_*.py` sources;
  - `Scripts/amalgamate.py:67` pins `TARGET_GLOB = "mcp-*.py"`.
- The wiki frontmatter subset allows top-level scalars, one nesting level (for `verified:` only, `ClaudeCode/skills/wiki/SKILL.md:660`), block or inline lists of scalars, and no list of dicts (`ClaudeCode/skills/wiki/scripts/_wikilib.py:1-10`). Hence `wip_now` is a flat scalar and `closed` / `commit` / `reason` are flat keys.
- `table_cells` sweeps `ClaudeCode/skills` for `.ljust(` / `.rjust(` and fails on an undeclared aligning file (`tests/test_table_cells.py:811-857`).
- `name_existence` scans every SKILL.md for MCP function names, so the roadmap SKILL.md may name only functions that exist.
- A page carrying `sources:` or `targets:` leaves the sourceless branch of freshness (`Scripts/mcp-wiki.py:2053-2058`). A roadmap page or item must therefore carry neither.

## 3. Captured Information (for implementation phase)

### Existing Patterns

**P1 — die(): one stderr line, exit 2** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:322-324`)
```python
def die(message):
    sys.stderr.write("checkpoint: %s\n" % message)
    sys.exit(2)
```
roadmap.py uses the prefix `roadmap: `, and writes through the same UTF-8 byte path as `emit` (5.1) so that a title with U+00B7 cannot crash the refusal route under an ASCII locale.

**P2 — require_file(): directory vs missing are different refusals** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:336-339`)
```python
    if not os.path.exists(path):
        die("file not found: %s" % path)
    if not os.path.isfile(path):
        die("not a regular file: %s" % path)
```

**P3 — strict UTF-8 read on the die route** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:148-153`)
```python
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except UnicodeDecodeError as exc:
        die("%s is not valid UTF-8 (byte 0x%02x at offset %d) -- refusing to "
            "read it" % (path, exc.object[exc.start], exc.start))
```
roadmap.py reads BYTES first, for the lock digest, then decodes them itself (`bytes.decode("utf-8")`). The refusal message is the same.

**P4 — write_atomic: mkstemp in the same dir, chmod, os.replace, unlink on BaseException** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:517-535`)
```python
    data = "\n".join(lines) + "\n"
    directory = os.path.dirname(os.path.abspath(path))
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".checkpoint-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.chmod(tmp, mode)  # mkstemp is 0600; the file we replace is not
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
```
Note that `mkstemp` and `os.stat` sit OUTSIDE the `try` here, so an unwritable directory or a missing one escapes as a traceback. roadmap.py's copy moves both under an `except OSError` that dies with a `cannot <op> %s: %s` line (the IO error rule, 5.1, NFR-13); rounds 2 and 3 of review found exactly these escapes in `init` and `close`.

roadmap.py uses the prefix `.roadmap-` and the suffix `.tmp`. The suffix is load-bearing: `iter_pages` yields only `*.md` (`ClaudeCode/skills/wiki/scripts/_wikilib.py:172`), so a crash-orphaned temp file is never mistaken for a page.

This is the lost-update hole: nothing between the read and `os.replace` re-checks the file. roadmap.py MUST re-check its lock inside this function, after the temp file is complete and immediately before `os.replace`. `die()` raises `SystemExit`, which is a `BaseException`, so the `except` clause also cleans the temp file on a lock refusal.

**P5 — generated region: prefix-matched markers, only inside the preamble** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:119-122`, `:441-470`)
```python
TOC_BEGIN_PREFIX = "<!-- TOC:BEGIN"
TOC_END_PREFIX = "<!-- TOC:END"
...
def strip_toc(lines):
    limit = preamble_end(lines)
    begin = find_marker(lines[:limit], TOC_BEGIN_PREFIX)
    ...
    if end < 0:
        die("unterminated TOC region -- refusing to guess where it ends")
```
roadmap.py uses the markers `<!-- ROADMAP:BEGIN` / `<!-- ROADMAP:END`, searched only above the first lane heading (`# now`). A why text that quotes a marker is never taken for the region, and `check_why` refuses one anyway.

**P6 — ADR 0016 cell escaper: escape the escape char first, reversible** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:389-404`), and escape-before-measure rendering (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:407-431`)
```python
def _toc_cell(value):
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
...
    table = [[_toc_cell(cell) for cell in row] for row in table]
    widths = [max(len(row[i]) for row in table) for i in range(len(TOC_COLUMNS))]
    ...
    rule = "|" + "|".join("-" * (width + 2) for width in widths) + "|"
```
roadmap.py implements `_md_cell` verbatim and a generic `render_table(headers, rows)`, which left-justifies every column.

**P7 — argparse parent parser** (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:991-1002`); `main()` dispatches on `args.command` and ends with `sys.exit(code)` (`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:1071-1094`)
```python
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--file", default=DEFAULT_FILE, help="checkpoint markdown file (default: %(default)s)"
    )
    ...
    sub = parser.add_subparsers(dest="command", required=True)
```
roadmap.py's parent parser carries `--file` (default `None`, which means "resolve DEFAULT_FILE against the git top-level") and `--today`.

**P8 — Kahn cycle check** (`Scripts/task-validator.py:336-357`, tab-indented; port it to 4 spaces and return the leftovers instead of calling `self.error`)
```python
		in_degree = {tid: 0 for tid in known_ids}
		adj = {tid: [] for tid in known_ids}
		for tid in known_ids:
			for dep in dep_map.get(tid, []):
				if dep in known_ids and dep != tid:
					adj[dep].append(tid)  # dep must finish before tid
					in_degree[tid] += 1
		queue = [tid for tid in known_ids if in_degree[tid] == 0]
		...
		if visited != len(known_ids):
			in_cycle = sorted(tid for tid in known_ids if in_degree[tid] > 0)
```

**P9 — stdlib git call with a timeout** (`Scripts/mcp-wiki.py:349-369`, the copy WITH a timeout; `_wikilib.py:26-45` has `stdin=DEVNULL` at `:40` but no timeout)
```python
        proc = subprocess.run(
            ["git"] + args, cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
            timeout=GIT_TIMEOUT_SEC,   # never wait forever; see the constant
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git %s timed out after %ds" % (args[0] if args else "", GIT_TIMEOUT_SEC)
```
`GIT_TIMEOUT_SEC = 30` (`Scripts/mcp-wiki.py:130`). roadmap.py copies this shape (4-space, no hints) with three deliberate differences. (a) The argv is `list(GIT_SAFE_ARGV) + args`, never `["git"] + args`, and the child environment is `dict(os.environ, GIT_NO_LAZY_FETCH="1")`, built at CALL time so a `PATH` a test sets in `os.environ` is honoured (NFR-5, S-L1, S4-3). (b) It never passes `universal_newlines`: it always captures BYTES, and `git(args, cwd, binary=False)` decodes stdout with `os.fsdecode` (which cannot raise on POSIX: undecodable bytes become surrogate escapes, the same way Python spells filesystem names) and stderr with `errors="replace"`; `binary=True` returns stdout as bytes, which `worktree_dirty` uses for `ls-tree -z`, so no `\r` in a path name is translated and no decode can fail mid-record (round-3 M4). (c) After `FileNotFoundError` (127) and `TimeoutExpired` (124) it also catches `OSError` (a git binary that cannot be executed, a vanished cwd) and returns `(126, "", "cannot run git: <strerror>")`, so its contract, "report through the rc, never raise", holds for every spawn failure (NFR-13). The server copy runs in the wiki's own repository; roadmap.py is a generic plugin script that runs in whatever repository the user is in, whose `.git/config` it must not trust. The prefix is defense in depth: the primary guard is that roadmap.py never runs an index-refreshing command (`git status`, `diff`, `add`), because a refresh is what runs a planted `core.fsmonitor` AND a planted clean-filter driver, and a `-c` override exists only for the former (S2-2). Every call roadmap.py makes (`rev-parse`, `cat-file -e`, `ls-tree`) reads objects or refs and never the index.

**P10 — skill-local sibling import** (`ClaudeCode/skills/wiki/scripts/reindex.py:25-26`, `ClaudeCode/skills/wiki/scripts/freshness.py:31-32`)
```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wikilib as w  # noqa: E402
```
Loading `reindex.py` in a test therefore also imports `_wikilib` into `sys.modules` AND leaves the scripts directory on `sys.path`. Parity tests read `reindex_mod.w`, the object `reindex` actually uses, rather than a second copy. Every suite that loads `reindex.py` / `freshness.py` in-process snapshots `sys.path` (a list copy) before the load and, in a `finally`, restores it and pops `_wikilib` from `sys.modules`, so no later suite in the same `run.py` process inherits either (L8).

**P11 — the wiki frontmatter subset parser** (`ClaudeCode/skills/wiki/scripts/_wikilib.py:71-153`; the server copy is `_unquote` `Scripts/mcp-wiki.py:392`, `_parse_scalar` `:398`, `parse_frontmatter` `:444`). The writer must respect four behaviours:
- `_parse_scalar` (`_wikilib.py:71-78`): a value starting with `[` and ending with `]` becomes a LIST, split on `,` with no escape. `[]` parses as `[]`.
- `_unquote` (`_wikilib.py:81-84`): a value wrapped in matching `"` or `'` loses them.
- `parse_frontmatter` (`_wikilib.py:127-153`):
  - a `key:` with an EMPTY value starts a block, so an empty value parses as `[]` or `{}` and never as `None`;
  - keys split at the FIRST `:`.
- Full-line `#` comments are ignored (`_wikilib.py:137`). An inline `#` is NOT a comment.

**P12 — INDEX rendering, identical in both copies today** (`Scripts/mcp-wiki.py:2287-2304` / `ClaudeCode/skills/wiki/scripts/reindex.py:78-96`)
```python
    order = TYPE_ORDER + sorted(t for t in groups if t not in TYPE_ORDER)
    lines = ["# Wiki Index", "",
             "_Generated by the p:wiki reindex tool — refresh with `/p:wiki`. Do not edit by hand._", ""]
    for typ in order:
        bucket = groups.get(typ)
        if not bucket:
            continue
        lines.append("## %s" % typ)
```
Unknown types are APPENDED, not dropped. A server that predates this change would therefore list every archive page, which is the INDEX-bloat regression the restart step exists to prevent.

**P13 — orphan rule** (`Scripts/mcp-wiki.py:2282-2283` / `ClaudeCode/skills/wiki/scripts/reindex.py:73-74`)
```python
    orphans = [e for e in entries
               if e["name"] and e["name"] not in referenced and e["type"] != "overview"]
```

**P14 — freshness short-circuit for sourceless pages** (`Scripts/mcp-wiki.py:2053-2058`, inside `_classify_page` at `:2019`; skill copy `ClaudeCode/skills/wiki/scripts/freshness.py:118-131`)
```python
    if not sources:
        if materialized:
            return dict(base, status="promotable", materialized=materialized)
        if targets:
            return dict(base, status="planned")
        return dict(base, status="untracked" if typ in UNTRACKED_TYPES else "no-sources")
```
A roadmap page or item must therefore NEVER carry `sources:`, `targets:`, `verified:` or `links:`.

**P15 — verify walks backticked body spans** (`Scripts/mcp-wiki.py:1561-1579`, used by `verify_analyze` at `:1677`). Any backticked span shaped like a repo path, whose first component is a repo-root entry, is resolved, and a missing path is a GATING `broken-anchor`. For an IMMUTABLE archive page, a backticked anchor that later disappears can never be fixed. Hence generated text is backtick-free (5.4).

**P16 — suite skeleton, sandbox guard, CLI runner** (`tests/test_checkpoint.py`)
- Constants (`tests/test_checkpoint.py:157-163`): `NAME`, `TARGET = H.repo_path(...)`, `LIVE_CHECKPOINT`. Group labels are at `:165-177`.
- `set_sandbox` / `sandbox_path` (`tests/test_checkpoint.py:1039-1058`):
```python
    real = os.path.realpath(path)
    if _SANDBOX is None:
        raise AssertionError("no sandbox is active; refusing to touch %r" % (path,))
    if real != _SANDBOX and not real.startswith(_SANDBOX + os.sep):
        raise AssertionError("refusing to touch %r: outside the sandbox %r" % (path, _SANDBOX))
    return path
```
- `run_cli` (`tests/test_checkpoint.py:1111-1127`) refuses an argv without `--file`. It runs with `input=""`, `cwd=` the target's dir and `env=H.child_env(env_extra)`.
- `captured` / `call_module` (`tests/test_checkpoint.py:1130-1150`) run in-process; `die()` -> SystemExit -> exit code.
- `problem_if` / `missing_tokens` (`:1153-1160`), `temp_leftovers` / `stale_temp_files` (`:1209-1225`).
- `refusal()` (`tests/test_checkpoint.py:1980`) asserts exit 2, sha256 unchanged, no temp leftovers, tokens present/absent, and a one-line stderr.
- Hygiene `group_k` (`tests/test_checkpoint.py:3528`); `run()` (`tests/test_checkpoint.py:3608-3676`) has the target-exists short-circuit, `H.load_module_from_path`, `with H.TempWorkspace(...)`, and hygiene in `finally`.

**P17 — harness** (`tests/_harness.py`):
- `repo_path` `:61`, `child_env` `:66`, `Suite` `:196`;
- `Suite.record(group, cid, problems=(), status=None, detail=(), ...)` `:220`;
- `TempWorkspace` `:335-379` (`subdir`, `write_text`, `write_bytes`);
- `load_module_from_path` `:396-407` (pins `dont_write_bytecode`);
- `repo_tree` `:435`, `pycache_snapshot` `:459`, `sha256_file` `:475`, `file_digests` `:483`.

**P18 — SUITES row + wrapper** (`tests/run.py:71-74`, `:125-126`, `:309-327`)
```python
def run_checkpoint(opts):
    return run_python_suite("test_checkpoint", opts)
...
    ("checkpoint", run_checkpoint,
     "checkpoint.py section reader + TOC writer: ...",
     <count>),
```
A typed count is checked against the run (drift report at `tests/run.py:450`). `None` is reserved for data-derived counts (`tests/run.py:182-197`), so a new suite gets a typed count.

**P19 — forge target** (`project-forge.yaml:141-147`)
```yaml
  checkpoint:
    description: "The checkpoint section reader and TOC writer. Writes only inside a mkdtemp sandbox -- ..."
    requires: [syntax]
    commands:
      - python3 tests/run.py checkpoint
    filter:
      grep: 'SUITE|AGGREGATE|cases|ALL|FAIL|INFO|SKIP'
```

**P20 — table_cells roster**:
- the `Row` class (`tests/test_table_cells.py:196-211`) and the `checkpoint` row (`:261-278`);
- totals `DECLARED_ESCAPED` / `DECLARED_STRUCTURE` (`:192-193`) and the sweep set (`:813-818`);
- the generic renderer call `got.renderer(RND_HEADERS, data)` for any key without an adapter (`:618-619`).

**P21 — skill script invocation** (`ClaudeCode/skills/checkpoint/SKILL.md:66-82`):
- the invocation shape is `python3 ~/.claude/skills/p/skills/<name>/scripts/<script> <cmd>`;
- the single-writer rule is at `:112`, and the refusal-handling paragraph at `:170`;
- the staged-file hand-off is at `:165`: the caller `create_text_file`s the text under `.claude/tmp/` and passes the path (`--block-file`). roadmap.py's `--item-file` / `--why-file` / `--reason-file` follow the same shape, and for the same reason the staging file names avoid the target's own temp prefix (`.roadmap-`);
- the frontmatter keys are `name`, `description`, `model` (`:1-5`).

**P22 — ADR frontmatter/header** (`docs/adr/0021-contain-by-the-admitted-root.md:1-26`):
- frontmatter: `name: 0021-<slug>`, `type: adr`, `status: active`, title, description, `sources:` + `verified:` (commit, date), `links:`;
- body: `# ADR 0021: <title>`, then `**Status:** accepted ...`.

**P23 — handoff contract entry** (`ClaudeCode/skills/_lib/handoff-contracts.md:26-51` entry shape: Inputs / Outputs / Side effects). The last entry is `/p:branch-review` at `:144-159`, the file table is at `:163-173`, and the rules are at `:177-182`.

**P24 — wiki adopt op shape** (`ClaudeCode/skills/wiki/SKILL.md:174-193`), which the roadmap `adopt` op mirrors in prose.

### Naming Conventions
- roadmap.py names:
  - commands are `cmd_<verb>`;
  - private helpers take a leading underscore (`_md_cell`, `_publish_link`);
  - constants are UPPER_SNAKE;
  - regexes end in `_RE`.
- Suite modules use the `test_<suite>.py` pattern; group labels are `GA = "A. ..."` constants; case ids are kebab-case strings.
- Wiki constant names: a set or tuple of type names ends in `_TYPES`.

### Type Definitions (roadmap.py data model — new; plain namedtuples, no hints)

```python
Item = collections.namedtuple(
    "Item",
    "id title state horizon origin spec blocked_by follows severity tags why log closed path")
# id: int (R-0014 -> 14); horizon: "now"|"next"|"later"|"unset"
# spec/follows/severity: None or str/int (follows is an int id); blocked_by: tuple of int (sorted, unique)
# tags: tuple of str (sorted, unique); why: str (markdown, "" when absent)
# log: tuple of LogEntry; closed: None or Closed
# path: path of the file the item was read from, relative to the wiki root's parent
LogEntry = collections.namedtuple("LogEntry", "date lane_from lane_to state_from state_to reason")
# lane_from: None for "new"; state_from/state_to: both None unless the state changed
Closed = collections.namedtuple("Closed", "date commit reason")
Snapshot = collections.namedtuple("Snapshot", "digest archive_digest")
# digest: sha256 hex of roadmap.md bytes (None if missing);
# archive_digest: sha256 hex of "\n".join(sorted(non-temp names in archive/))
State = collections.namedtuple("State", "path meta lanes archived snapshot text repairs")
# meta: dict name/type/status/title/description/wip_now/wip_note
# lanes: dict horizon -> list of Item in rank order; archived: dict int id -> Item
# text: the decoded roadmap.md text as read; repairs: list of (id, archive relpath)
ABSENT = object()     # write_atomic expectation: the target must not exist (init)
UNLOCKED = object()   # write_atomic expectation: no lock (export --out)
```

Constants (section 1 of the module):
```python
DEFAULT_FILE = "docs/roadmap/roadmap.md"
ARCHIVE_DIRNAME = "archive"
ROADMAP_TYPE = "roadmap"
ITEM_TYPE = "roadmap-item"
HORIZONS = ("now", "next", "later", "unset")
# horizon -> its H1 lane heading, in file order. The ONE place that knows inbox means unset.
LANE_HEADINGS = (("now", "# now"), ("next", "# next"), ("later", "# later"), ("unset", "# inbox"))
LANE_LABELS = {"now": "now", "next": "next", "later": "later", "unset": "inbox"}
OPEN_STATES = ("idea", "planned", "active")
CLOSED_STATES = ("done", "dropped")
# Key-line order of a live item; the archive frontmatter uses the same order for these keys.
ITEM_KEYS = ("state", "horizon", "origin", "spec", "blocked_by", "follows", "severity", "tags")
LIST_KEYS = ("blocked_by", "tags")            # always written inline, [] when empty
OPTIONAL_KEYS = ("spec", "follows", "severity")  # omitted when unset
ROADMAP_KEYS = ("name", "type", "status", "title", "description", "wip_now")
EDITORIAL_STATUSES = ("draft", "active", "deprecated")
WIP_DEFAULT = 3
WIP_NOTE_UNARGUED = ("# wip_now is an unargued starting value (adr 0022, after adr 0013)"
                     " -- change it only with roadmap.py wip N --reason TEXT")
SUMMARY_BEGIN_PREFIX = "<!-- ROADMAP:BEGIN"
SUMMARY_END_PREFIX = "<!-- ROADMAP:END"
SUMMARY_BEGIN = SUMMARY_BEGIN_PREFIX + " -- generated by roadmap.py; do not edit by hand -->"
SUMMARY_END = SUMMARY_END_PREFIX + " -->"
SUMMARY_COLUMNS = ("Lane", "Id", "State", "Title", "Ready")
LIST_COLUMNS = ("Lane", "Id", "State", "Title", "Ready", "Blocked by")
UNTRIAGED_COLUMNS = ("Id", "Title", "Age (days)", "Origin")
TMP_PREFIX = ".roadmap-"
TMP_SUFFIX = ".tmp"
EXPORT_SCHEMA = "roadmap-export/1"
GIT_TIMEOUT_SEC = 30
# Every git spawn starts with these, and runs with GIT_NO_LAZY_FETCH=1 in its env.
# Cheap hardening, DECLARED, not a gated guarantee (adr 0022): the local .git/config
# is trusted (clone does not transfer it; write access to it already equals code
# execution as the user). What IS relied on: roadmap.py never runs an index-refreshing
# command (git status/diff/add); the export's dirty flag is hashed in Python
# (worktree_dirty). hooksPath is pinned so a future write-ish call inherits the guard;
# protocol.allow=never and GIT_NO_LAZY_FETCH keep a read from reaching the network.
GIT_SAFE_ARGV = ("git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                 "-c", "protocol.allow=never")
# --item-file: a JSON object; the allowed and required keys depend on the command (S2-1).
ITEM_FILE_KEYS = {
    "add": ("title", "origin", "why", "tags", "severity", "horizon", "reason"),
    "link": ("origin", "spec", "blocked_by"),
}
ITEM_FILE_REQUIRED = {"add": ("title", "origin"), "link": ()}   # link: at least one key
STAGED_MAX_BYTES = 65536   # --item-file / --why-file / --reason-file: small by contract
# Cap for every other read_regular: roadmap.md, each archive entry, each wiki page of the
# name scan, each worktree file worktree_dirty hashes. An UNARGUED starting value, like
# wip_now (adr 0022, after adr 0013): it bounds memory against a planted huge file, and
# no measurement chose it. Change it with a reason, never silently.
PAGE_MAX_BYTES = 4 * 1024 * 1024
# read_regular's reasons (returned, never raised); each caller maps them to its own
# refusal, skip or verdict. An OSError is returned as the exception itself. A caller
# that must report READ_MISSING through io_fail passes a REAL exception,
# FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path), so io_fail's
# contract ("exc is an OSError") has no special case (L1).
READ_MISSING = "missing"
READ_NOT_REGULAR = "not a regular file"
READ_TOO_LARGE = "too large"
# The lane spellings a caller may type (move <lane>, add/list --horizon, the item file's
# horizon): inbox is the spelling of unset. Validated in code, never via argparse choices.
LANE_INPUT = {"now": "now", "next": "next", "later": "later", "inbox": "unset", "unset": "unset"}
SLUG_MAX = 50
# Every pattern: re.ASCII (so \d is [0-9], never an Arabic-Indic or other Unicode
# digit that int() would still accept) and NO ^ / $ anchors -- every use is
# .fullmatch(), because $ also matches before a trailing "\n" (M3).
ID_RE = re.compile(r"[Rr]-?0*(\d{1,4})|0*(\d{1,4})", re.ASCII)   # R-0014, r-0014, R14, 14, 0014
ITEM_HEADING_RE = re.compile(u"## R-(\\d{4}) \u00b7 (\\S.*)", re.ASCII)
ARCHIVE_NAME_RE = re.compile(r"(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md", re.ASCII)
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
TAG_RE = SLUG_RE
SHA_RE = re.compile(r"[0-9a-f]{7,64}", re.ASCII)                # user input: SHA-1 or SHA-256 repos
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)   # the stored archive `commit`
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)       # shape only; check_date adds the calendar
LOG_RE = re.compile(r"- (\d{4}-\d{2}-\d{2}) (new|now|next|later|unset)->"
                    r"(now|next|later|unset|done|dropped)"
                    r"(?: \[(idea|planned|active)->(idea|planned|active)\])?: (\S.*)", re.ASCII)
WIP_NOTE_RE = re.compile(r"# wip_now \S.*", re.ASCII)
```
**Every date is a calendar date (M3).** One pure section-4 helper, `check_date(where, value)`, validates every date roadmap.py accepts or reads: log-line dates (roadmap.md and archive), the archive `closed:` value, `--today` and `--closed-since`. It runs `DATE_RE.fullmatch` (shape refusal: `%s %r is not a YYYY-MM-DD date`), then `datetime.date(int(y), int(m), int(d))` under `try` / `except ValueError` (calendar refusal: `%s: %r is not a calendar date`), and returns the value. `where` is the flag (`--today`, `--closed-since`) or the file position (`<path>: line N` for a log line, `archive/<name> closed` for the close date). `datetime.date(...)` is a constructor, not a clock read, so the "only `today()` reads the clock" rule is untouched. Group A's `regex-ascii` case gates the pattern rules: every `re.compile` call in roadmap.py passes `re.ASCII`, no pattern literal starts with `^` or ends with `$`, and no `*_RE` name is used with `.match(` or `.search(` (only `.fullmatch(`); control: a planted copy with `DATE_RE.match(` is detected.
roadmap.py deliberately holds NO copy of `_wikilib`'s `SKIP_DIRS` / `SKIP_FILES` (5.4, "wiki-wide name scan").

Wiki constants after Step 3 (`_wikilib.py`, tab-indented; the same values in `Scripts/mcp-wiki.py`, 4-space):
```python
TYPE_ORDER = ["overview", "subsystem", "component", "reference", "analysis",
	"concept", "spec", "runbook", "adr", "glossary", "roadmap", "roadmap-item"]
INDEX_LABELLED = ("draft", "deprecated")
STATUS_FORBIDDEN = ("current", "stale")
UNTRACKED_TYPES = {"overview", "adr", "glossary", "roadmap", "roadmap-item"}
ORPHAN_EXEMPT_TYPES = ("overview", "roadmap-item")   # archive pages are unlinked by design
INDEX_COUNTED_TYPES = ("roadmap-item",)              # INDEX.md counts these, never lists them
```

### Build System
- There is no compile step. `forge_call build syntax` compiles only `Scripts`, `tests` and `ClaudeCode/hooks` (`project-forge.yaml:22-28`). What catches a syntax error in `roadmap.py` / `reindex.py` is the suites' `load_module_from_path`.
- Two new forge targets, `wiki_index` and `roadmap`, go under `test:`, each with `requires: [syntax]`.
- `PYTHONDONTWRITEBYTECODE: "1"` is already set globally (`project-forge.yaml:19-20`).

## 4. Alternative Approaches Evaluated

### Selected: a stdlib single-writer script in its own skill, plus read-only wiki integration
**Rationale:**
- It matches the checkpoint precedent: one writer, an atomic replace, and the generated table in the same write.
- It keeps `mcp-wiki` write-free except for INDEX.md and measured regions.
- Roadmap pages are ordinary searchable wiki pages.

**Trade-offs:**
- There is a second frontmatter-subset implementation, gated differentially.
- The server needs a restart to learn two types.
- The page-type constants live in two places, gated by parity.

### Rejected (from the design; recorded, not re-argued)
- **Building the roadmap into mcp-wiki** (`Scripts/mcp-wiki.py`). It would break the server's write-free character, since the server writes only INDEX.md and measured regions.
- **A root ROADMAP.md outside the wiki.** `wiki_call search` could not see it.
- **A single archive file** (roadmap-archive.md). It grows without bound and every close rewrites it. It is replaced by one immutable file per closed item.
- **Listing every `roadmap-item` in INDEX.md.** INDEX.md is @-included on every session, so N closed items would cost N context lines per session. One rendered count line replaces them.
- **Fenced YAML inside roadmap.md for item fields.** It is a third format for the same data. The stdlib subset in two places is one format.
- **P0-P3 labels, RICE/WSJF scores, producer severity as priority:**
  - labels are a second priority axis, and they inflate;
  - scores are false precision and typed numbers (ADR 0019);
  - severity is producer information, not priority.
- **A separate archive sequence number.** NNNN = item id makes resolving `R-0014` to `archive/0014-*.md` trivial. Gaps are informative, and the closing order is in `closed`.
- **Reopening a closed item.** The archive is immutable; a regression is a new item with `follows:`.
- **Auto-dropping stale inbox items.** Dropping is a decision (`close --reason`).
- **The librarian as the adopt executor:**
  - it has no Bash and a closed op table (`ClaudeCode/agents/minion-librarian.md:69-77`), whose `adopt` means frontmatter onboarding;
  - orchestration stays in the skill (`ClaudeCode/ARCHITECTURE.md:13`).
- **Extending amalgamate to skill hosts to share the constants.** It would widen the generator's scope, disturb the pinned `TARGET_GLOB` (`tests/test_generated_region.py:600-602`) and add a new ADR-0014 source domain, all for six constants.
- **Runtime import of `_wikilib` from the server.** The fleet rule forbids it, and the deploy symlink complicates it.
- **A JSON Schema file for the export.** The stdlib cannot validate it, so no gate would run it. The contract lives in SKILL.md and in a golden export.
- **Copying checkpoint.py's read-then-replace verbatim.** It has a lost-update hole, and parallel sessions exist.

### Rejected (implementation level, this plan)
- **A lock file with `fcntl.flock`:**
  - the design locked the optimistic lock;
  - flock is POSIX-only-ish, leaves stale lock files after a crash, and would be a second on-disk artefact.

  The residual window between the re-check and `os.replace` is declared in ADR 0022 instead.
- **A nested `wip_limit:` / `now:` frontmatter block.** The wiki SKILL.md §5 allows nesting for `verified:` only (`ClaudeCode/skills/wiki/SKILL.md:660`). A flat `wip_now` stays inside the documented subset, and the export still renders `"wip_limit": {"now": N}`.
- **Lanes as `## NOW` headings (the same level as items).** Lanes and items would then share one heading level, and the parser would have to tell them apart by token. H1 lanes (`# now`) with H2 items make the level itself the discriminator.
- **A page H1 (`# Roadmap`) in roadmap.md.** The title already lives in frontmatter, which is what INDEX.md and search use. A second H1 besides the four lane H1s would be one more generated line with no reader.
- **Writing empty lists as absent keys.** One spelling is better: lists are always inline and `[]` when empty, while optional scalars are omitted.
- **A declared copy of `_wikilib.SKIP_DIRS` in roadmap.py, gated by value.** The collision scan reads MORE than the wiki does (every `*.md` except INDEX.md and dot-directories). A false refusal costs only `--slug`, and there is no copy to drift.
- **An `ARCHIVE_TYPE` wiki constant.** It is replaced by `ORPHAN_EXEMPT_TYPES` and `INDEX_COUNTED_TYPES`, which name the two behaviours instead of the type.
- **"A dropped blocker keeps an item unready".** It would strand an item forever, because a dropped item can never become done. A dropped blocker is a void dependency.
- **The WIP cap checked in `validate()` on every load.** A hand-lowered cap would then block every command, including moving items OUT of `now`. WIP is a gate on transitions into `now` and on `wip` itself.
- **Scott runs `roadmap.py list` / `export` himself during adopt.** He cannot: `p:minion-explorer` has no Bash (`ClaudeCode/agents/minion-explorer.md:5`, `:20`). The main context exports to a staged file and Scott reads it (H1).
- **Passing adopt candidates as `add --title "..." --why "..."` flags.** Harvested repository prose would be interpolated into a Bash command line (CWE-78). Replaced by `add --item-file` (S-H1).
- **A `key: value` item file instead of JSON.** The subset refuses quote-wrapped values and has no multi-line value, so a harvested why could not be carried, and the stager would have to know roadmap.py's refusal rules to encode a title. JSON has one escaping for every character and `json.loads` is stdlib.
- **Normalizing a line separator in a value (replacing it with a space) instead of refusing it.** It would silently change user text; a refusal names the character and the field, and the caller fixes the input (NFR-2).
- **`git status --porcelain -- <roadmap dir>` for the export's `source.dirty`.** It refreshes the index, which runs a clean-filter driver configured in the target repository's `.git/config` + `.gitattributes`; `-c core.fsmonitor=false` has no per-driver counterpart. Replaced by hashing the working files as git blobs against `ls-tree` (5.3, S2-2), which can only over-report.
- **`load_state` refusing a roadmap whose `archive/` is missing.** git drops empty directories, so every fresh clone without a closed item would be refused on its first `close`. `create_exclusive` creates the directory instead (M5).
- **Writing roadmap.md before creating `roadmap/archive/` in `init`.** `mkstemp` needs `roadmap/` to exist, so init died with a traceback on a bare wiki root. All refusals are decided first, then the directories, then the file (H1).
- **Refusing a read command's duplicate-id repair by writing it.** A read command that writes would surprise the caller and would need the lock. Read commands repair in memory and print a note.

## 5. Implementation Strategy

### Overview
Land the wiki side first, behind gates written red. Home the constants (a behaviour-neutral refactor), write the `wiki_index` suite and watch it fail, then teach both copies the two types, the INDEX count line and the orphan exemption. Only then build `roadmap.py`. Its suite's safety guards are scaffolded BEFORE the first line of the writer exists, and the writer grows group by group, with each group's cases written first. The skill prose and the wiki docs come last. The plan ends with a restart of the running `mcp-wiki` server, a full-fleet run, and a reindex check.

### 5.1 Module layout inside roadmap.py (one section per concern, in this order)

```
"""docstring: the WHY of each mechanism (single writer, whole-file render,
optimistic two-digest lock, archive-first close order, archive-wins repair,
deterministic export). Points to SKILL.md for the operator contract and to
ADR 0022 for the decision; does not restate the field table."""

# 1. constants & grammar   DEFAULT_FILE ... WIP_NOTE_RE (section 3 above), ABSENT, UNLOCKED
# 2. errors & output       die(message), note(message), emit(text), emit_raw(text), today()
#                          (they name a stream and call _write_stream; no IO of their own)
# 3. io                    _write_stream(stream, text, role), _silence(stream),
#                          io_fail(op, path, exc), _discard(tmp),
#                          read_regular(path, cap), read_archive_entry(path),
#                          decode(path, data), digest(data),
#                          take_snapshot(path), check_snapshot(path, expect, conflict),
#                          write_atomic(path, text, expect, conflict=None),
#                          create_exclusive(path, text), _publish_link(tmp, final, text, exists_message),
#                          git(args, cwd, binary=False), verify_commit(sha, cwd), resolve_target(file_arg),
#                          check_containment(path),
#                          refuse_symlink(path, what), refuse_existing(path),
#                          ensure_archive_dir(path),
#                          archive_names_for_id(archive_dir, n), check_out_path(out, roadmap_path),
#                          read_state_files(path), wiki_page_names(root),
#                          read_staged_file(flag, path, roadmap_path),
#                          worktree_dirty(roadmap_dir)
# 4. frontmatter subset    parse_kv_lines(lines, first_lineno, where) -> list of (key, raw, lineno)
#                          fm_value(key, raw, where), render_kv(key, value) -> line
#                          check_line(field, value), check_scalar(field, value) -> stripped value,
#                          check_reason(value) -> stripped value,
#                          check_list_item(field, value), check_why(text) -> text,
#                          check_date(where, value) -> value,
#                          parse_item_file(text, where, command) -> dict,
#                          reason_from_file(text, where) -> text
# 5. parse                 parse_roadmap(text, path) -> (meta, lanes)
#                          parse_item(lines, first_lineno, path, lane) -> Item
#                          parse_log_line(line, where) -> LogEntry
#                          parse_archive(text, name) -> Item
# 6. model & invariants    parse_id(raw), fmt_id(n), next_id(state), resolve(state, n)
#                          cycle_members(graph), validate(state), ready(item, state)
#                          slugify(title), build_state(raw) (parse + archive-wins repair)
#                          load_state(path) = build_state(read_state_files(path))
#                          check_target_shape(path), wiki_root_of(path), archive_dir_of(path)
# 7. render                _md_cell(value), render_table(headers, rows) -> str
#                          render_log_line(entry), render_item(item) -> lines
#                          render_summary(state) -> lines, render_roadmap(state) -> str
#                          render_archive(item, name) -> str
# 8. write                 commit(state, conflict=None)
#                          (validate -> render -> re-parse self-check -> no-op check
#                           -> write_atomic with the lock re-check)
# 9. commands              cmd_init, cmd_add, cmd_list, cmd_show, cmd_move, cmd_rank,
#                          cmd_link, cmd_close, cmd_render, cmd_wip
#                          (each mutating command: load -> mutate -> commit -> emit;
#                           cmd_init is the one exception, see the rules below)
# 10. export               build_export(state, scope, source) -> dict, export_text(obj) -> str,
#                          cmd_export
# 11. cli                  COMMANDS dict, build_parser(), main()
```

Rules that keep the layers separable. Each is gated by AST in `roadmap:A` and repeated in the checklist.
- Sections 4, 5 and 7, plus `validate`, `cycle_members`, `ready`, `slugify` and `check_target_shape`, are PURE: no IO, no clock, no git. They take strings or objects and either return strings or objects or call `die`.
- **The IO boundary is a named set, and the AST gate uses exactly this set (H2).**
  - `IO_FUNCTIONS` = every function listed under section 3 above, and nothing else.
  - The FORBIDDEN names, anywhere outside `IO_FUNCTIONS` (whether called or merely referenced), are:
    - the builtin `open`;
    - any attribute chain rooted at `tempfile`, `subprocess`, `shutil` or `glob`;
    - any attribute chain rooted at `os`, EXCEPT exactly these pure names: `os.path.join`, `os.path.basename`, `os.path.dirname`, `os.path.splitext`, `os.path.normpath` and `os.sep` (`PURE_OS_NAMES`). Everything else under `os` is forbidden outside section 3, including `os.path.exists`, `os.path.isdir`, `os.path.islink`, `os.path.realpath`, `os.path.abspath` (it reads the cwd), `os.path.relpath` (it reads the cwd for a relative argument), `os.makedirs`, `os.listdir`, `os.stat`, `os.link`, `os.replace`, `os.fsync`, `os.environ`, `os.system` and `os.popen`.
  - Consequently the commands, `commit`, `load_state` and `main` perform no IO themselves. `cmd_init` calls `refuse_symlink`, `refuse_existing` and `ensure_archive_dir`; `cmd_close` calls `archive_names_for_id` for the twin pre-check; `cmd_export` calls `check_out_path` and `worktree_dirty`; `cmd_show` reads a closed item through `read_archive_entry`; `main` calls `check_containment` after the pure `check_target_shape` (S4-1); `read_state_files` computes the display paths relative to the wiki root's parent, so pure code never needs `relpath`. A path derived from a display path (the archive path `cmd_close` emits) is built by `os.path.join` onto that display path's directory, never by `os.path.relpath` (L9).
- **The IO error rule (structural, NFR-13).** Rounds 2 and 3 of review each found another traceback of one class: an `OSError` or `UnicodeError` escaping an IO call (`mkstemp` in `init` and in `close`, `export --out` into a missing parent, a missing staged file, `worktree_dirty` on an unreadable file or undecodable git output). Patching them one at a time does not close the class; one rule does, and it can live in one place because the purity rule above already confines ALL IO to section 3:
  - the rule is PER CALL (H1, round 4): in every section-3 function, EVERY IO call sits, at any nesting depth, inside the `body` of an `ast.Try` whose handlers name `OSError`. An IO call is a call whose dotted callee is FORBIDDEN by the purity rule and not in `NONRAISING_IO_CALLS`, or a method call `.write(` / `.flush(` (the stream and file-object writes). A call in a `try`'s `handlers`, `orelse` or `finalbody` is NOT guarded by that `try`; it needs a `try` of its own (a nested one inside the handler counts). The precise AST definition, with its `NONRAISING_IO_CALLS` and `IO_HANDLER_EXEMPT` sets, is roadmap group A's `io-handler` case (Step 4). A per-function rule ("the function has some `except OSError`") was rejected: a `write_atomic` whose `mkstemp` sits ABOVE its `try` would pass it with one guarded and one unguarded IO site, which is exactly the P4 escape;
  - the handler must SPELL `OSError`, as a bare name or inside a tuple. `IOError` and `EnvironmentError` are aliases of `OSError` on Python 3 and would work at run time, but the gate matches the name, and one spelling keeps it a name check (I3). A handler of type `BaseException`, or a bare `except`, does not count: that is the clean-up-and-re-raise shape, and it re-raises the traceback;
  - a helper whose contract is to FAIL converts the error into one `die()` line through ONE formatter:
```python
def io_fail(op, path, exc):
    """The backstop refusal for an IO error. op names the operation in plain
    words ("read", "list", "create directory", "create a temporary file in",
    "write", "link", "replace", "stat", "resolve the working directory")."""
    reason = getattr(exc, "strerror", None) or (
        os.strerror(exc.errno) if getattr(exc, "errno", None) else exc.__class__.__name__)
    die("cannot %s %s: %s" % (op, path, reason))
```
    A decode or encode error on the helper's OWN data (`UnicodeError`) is converted the same way, except where a friendlier message already exists (P3's "not valid UTF-8" for a decode, which stays);
  - a helper whose contract is to REPORT a condition catches and translates explicitly and never dies on it: `git()` returns an rc (124, 126, 127; P9), `read_regular` returns a reason (`READ_MISSING`, `READ_NOT_REGULAR`, `READ_TOO_LARGE` or the `OSError`), `worktree_dirty` returns `True` for a per-file read failure and `None` for a git or record-decode failure, `wiki_page_names` skips an unreadable file with a note, `_discard(tmp)` (the best-effort temp unlink every cleanup path uses) swallows the error because the refusal already under way is the one worth reporting, and `_write_stream` ends the run with its defined exit code (M2, below);
  - the specific, friendlier refusals the plan already defines (`file not found`, `not a regular file`, `--out ... does not exist`, the staged-file refusals, `already exists`) stay: each is checked BEFORE the syscall, so it wins whenever the condition is visible up front. The `cannot <op>` line is the backstop for what only the syscall can reveal (a permission, a full disk, a race);
  - there is NO catch-all in `main()`. A traceback raised outside section 3 is a bug in pure code and must stay visible, not be flattened into a refusal that hides it.
- Only `today()` reads the clock (`datetime.date.today`, `datetime.datetime.now` and anything under `time` are forbidden elsewhere).
- **Write routes.** The allowed callers of `write_atomic` are exactly {`commit`, `cmd_init`, `cmd_export`}:
  - every mutating command ends in `commit(state)`, with ONE exception: `cmd_init`, which writes the empty roadmap through `write_atomic(path, text, ABSENT)`, because there is no prior state to lock against;
  - `cmd_export --out` calls `write_atomic(out, text, UNLOCKED)` and writes a file that is not the roadmap;
  - `create_exclusive` is called only from `cmd_close`, which then ends in `commit`.
- No shell: the only spawn site is `git()`, whose argv is `list(GIT_SAFE_ARGV) + args`; there is no `shell=` keyword anywhere (NFR-10).
- `emit(text)` and `emit_raw(text)` are the only stdout writers, both through `_write_stream`, and there is no `print(` anywhere in the module. `sys.stdout` is referenced ONLY inside `emit` and `emit_raw`, and `sys.stderr` ONLY inside `die` and `note`; `_write_stream` never names either, because it receives the stream as a parameter (M1). `emit_raw` writes its text verbatim with no added newline; `show` uses it for an archived item so the bytes on stdout equal the archive file's bytes (L2), and `export` uses it on stdout because `export_text` already ends in `"\n"` (L5).
- **Stream IO is IO (M2, round 4).** `_write_stream` and its helper `_silence` live in SECTION 3 and are members of `IO_FUNCTIONS`, so the `io-handler` gate checks their `.write` / `.flush` calls like any other IO call; section 2 keeps only the four functions that choose a stream. A write to stdout or stderr can fail, and the defined outcomes are:
  - **`BrokenPipeError` (EPIPE) on stdout**: the reader closed early (`export | head`). The run exits **0**, silently: for a read command nothing was changed, and for a mutating command `emit` is the last act, after the write already succeeded, so 0 is the truthful status. Before exiting, `_silence(sys.stdout)` points file descriptor 1 at `os.devnull`, so the interpreter's shutdown flush of the still-buffered bytes cannot print `Exception ignored ... BrokenPipeError`;
  - **any other `OSError` on stdout** (EIO, ENOSPC on a redirected file): one best-effort line through `die("cannot write to standard output: %s")`, exit 2;
  - **any `OSError` on stderr, `BrokenPipeError` included** (from `note` or `die`): `_silence(sys.stderr)` and exit **2**. No message is possible, because stderr is the broken channel, and 2 is kept even for a `note` because the channel refusals travel on is gone and the caller could no longer see one. Calling `die` here would recurse, so this branch never does.
```python
def _write_stream(stream, text, role):
    """UTF-8 bytes through .buffer when the stream has one (a real terminal or
    pipe); plain text otherwise (the StringIO that tests' captured() installs).
    The U+00B7 in item headings must not depend on the locale's encoding.
    backslashreplace: a PATH decoded from non-UTF-8 argv or filesystem bytes
    carries lone surrogates; naming it in a refusal must never raise (NFR-4).
    role is "stdout" or "stderr"; it picks the exit code of a failed write (M2)."""
    buffer = getattr(stream, "buffer", None)
    try:
        if buffer is not None:
            stream.flush()
            buffer.write(text.encode("utf-8", "backslashreplace"))
            buffer.flush()
        else:
            stream.write(text)
    except OSError as exc:
        _silence(stream)                # no "Exception ignored" at shutdown
        if role == "stderr":
            sys.exit(2)                 # the refusal channel itself is gone
        if isinstance(exc, BrokenPipeError):
            sys.exit(0)                 # the reader stopped reading: not our failure
        die("cannot write to standard output: %s"
            % (exc.strerror or exc.__class__.__name__))


def _silence(stream):
    """Point the stream's descriptor at /dev/null, best effort (M2)."""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, stream.fileno())
        os.close(fd)
    except (OSError, ValueError):       # no real descriptor (StringIO): nothing to silence
        pass


def emit(text):
    _write_stream(sys.stdout, text + "\n", "stdout")


def emit_raw(text):
    """Verbatim: `show` of an archived item must reproduce the file's bytes,
    and `export` on stdout must equal `export --out` byte for byte."""
    _write_stream(sys.stdout, text, "stdout")


def note(message):
    _write_stream(sys.stderr, "roadmap: %s\n" % message, "stderr")


def die(message):
    _write_stream(sys.stderr, "roadmap: %s\n" % message, "stderr")
    sys.exit(2)
```
  `_silence`'s `os.open` / `os.dup2` / `os.close` sit in the body of `_silence`'s own `try`, so they are guarded by the per-call rule even though `_write_stream` calls `_silence` from inside a handler (a call to a local function is not an IO call; the IO calls are judged where they are written).
- `today()` honours `--today`:
```python
_TODAY = None   # set once by main() from --today; in-process tests may set it directly


def today():
    if _TODAY is not None:
        return _TODAY
    return datetime.date.today().isoformat()
```
`main()` validates `--today` with `check_date("--today", value)` (section 4, M3): `DATE_RE.fullmatch` (ASCII digits, no trailing newline) and then a real calendar date. The refusals are `--today %r is not a YYYY-MM-DD date` and `--today: %r is not a calendar date`.

### 5.2 Data model — exact on-disk formats

#### docs/roadmap/roadmap.md (full example; every byte is produced by `render_roadmap`, and the file ends with one `\n`)

```markdown
---
name: roadmap
type: roadmap
status: active
title: Roadmap
description: Known-but-unscheduled work by horizon (now, next, later, inbox), written only by roadmap.py; closed items are archived one file each under roadmap/archive/.
# wip_now is an unargued starting value (adr 0022, after adr 0013) -- change it only with roadmap.py wip N --reason TEXT
wip_now: 3
---

<!-- ROADMAP:BEGIN -- generated by roadmap.py; do not edit by hand -->
WIP now: 1 of 3. Archive: 2 closed items (1 done, 1 dropped).

| Lane  | Id     | State   | Title                                    | Ready |
|-------|--------|---------|------------------------------------------|-------|
| now   | R-0003 | active  | Give _wikilib.git() the server's timeout | yes   |
| next  | R-0004 | planned | Unify the skill and server git helpers   | no    |
| inbox | R-0005 | idea    | ADR authoring proposes deferred limits   | yes   |
<!-- ROADMAP:END -->

# now

## R-0003 · Give _wikilib.git() the server's timeout

state: active
horizon: now
origin: tests/test_spawn_stdin.py#skills-survey
blocked_by: []
tags: [wiki]

The skill-side git helper passes stdin=DEVNULL but has no timeout; the server copy has both.

### Log

- 2026-09-28 new->later: harvested by adopt
- 2026-09-29 later->now [idea->active]: picked up

# next

## R-0004 · Unify the skill and server git helpers

state: planned
horizon: next
origin: docs/adr/0012-the-transport-tier-diverged.md#consequences
blocked_by: [R-0003]
follows: R-0001
severity: low
tags: [scripts, wiki]

Two copies of one helper drift; ADR 0012 declined a shared transport tier, not this helper.

### Log

- 2026-09-28 new->next [idea->planned]: harvested by adopt

# later

# inbox

## R-0005 · ADR authoring proposes deferred limits

state: idea
horizon: unset
origin: user:2026-09-28:adr-producer
blocked_by: []
tags: [adr, producer]

### Log

- 2026-09-28 new->unset: added
```

Grammar. The reader is strict and refuses anything else with `path: line N: ...`.

1. **Frontmatter.** Line 1 is `---`, followed by exactly the keys `name, type, status, title, description, wip_now` and a closing `---`.
   - `type` must be `roadmap`.
   - `status` must be one of `draft|active|deprecated`, and it is preserved as read.
   - `wip_now` must be a positive integer.
   - The only comment allowed is one full-line comment immediately above `wip_now:` that matches `WIP_NOTE_RE`. It is preserved verbatim.
   - Any other key or comment is refused.
2. **Preamble.** After the frontmatter come a blank line, then the summary region, then a blank line. There is NO page H1. The preamble is regenerated on every write and never parsed for data; only an unterminated region is refused. The markers are searched only above the first lane heading. Any other non-blank text before `# now` is refused.
3. **Lanes.** The four H1 lane headings `# now`, `# next`, `# later`, `# inbox` appear in that fixed order, exactly once each and always all four. An empty lane is a bare heading followed by one blank line.
4. **Items.** An item block consists of, in order:
   - the heading `## R-NNNN · <title>` (U+00B7 with exactly one space on each side), then one blank line;
   - contiguous `key: value` lines in `ITEM_KEYS` order:
     - `state`, `horizon`, `origin`, `blocked_by` and `tags` are always present;
     - `spec`, `follows` and `severity` are omitted when unset;
     - `blocked_by` and `tags` are inline lists, sorted and de-duplicated, `[]` when empty;
   - one blank line, then optional why prose followed by one blank line;
   - `### Log`, one blank line, and one or more `LOG_RE` lines.

   The item ends at the next `## ` or `# ` line or at EOF. Trailing blank lines are ignored.
5. **Horizon matches lane.** The item's `horizon:` must equal its lane (`unset` for `# inbox`). A disagreement means a hand edit and is refused.
6. **Log.** The TO lane `done|dropped` is refused in roadmap.md, because only an archive log ends in a closing line.
7. **Line endings.** A CR anywhere in the file is refused (`carriage return in the file`), never silently normalized. **[IMPL-CHOICE]**
8. **Why prose.** It may not contain a line matching `^#{1,3} ` or a line starting with a summary marker prefix. This is refused at write time by `check_why`, because such a line would split the item on re-read. `check_why` also refuses every line separator other than `"\n"` (so `"\r"` is a user refusal, not the internal round-trip error), every C0 control other than `"\n"` and `"\t"`, and every lone surrogate (U+D800-U+DFFF, M4), and it strips leading and trailing blank lines before the text is written (5.4).

Log line grammar (both files): `- YYYY-MM-DD FROM->TO[ [SFROM->STO]]: REASON`, matched with `LOG_RE.fullmatch`; the date must also be a calendar date (`check_date`, M3), so a planted `2026-02-30` is refused at parse time, not carried into the export.
- FROM is `new` for the creation line, otherwise a lane.
- TO is a lane, or `done`/`dropped` on an archive's closing line only.
- The bracket appears only when the state changes between open states.
- The creation line of `add` is `new-><lane>`, with ` [idea-><state>]` when the item is created in a state other than `idea`.
- Item age (for `list --untriaged`) is the number of days from the first log line's date to `today()`. There is no separate `created` field.

Origin conventions (documented in SKILL.md; the script only checks `check_scalar`, which runs `check_line` first):
- `<repo-relative path>#<heading-slug>` for a harvested item;
- `user:<YYYY-MM-DD>:<kebab-key>` for a manual idea.

#### docs/roadmap/archive/0001-home-the-wiki-page-type-constants-in-wikilib.md (full example, done)

```markdown
---
name: 0001-home-the-wiki-page-type-constants-in-wikilib
type: roadmap-item
status: active
title: R-0001 · Home the wiki page-type constants in _wikilib
description: Closed roadmap item R-0001 (done 2026-09-27).
id: R-0001
state: done
horizon: now
origin: user:2026-09-20:wiki-constants-home
blocked_by: []
tags: [wiki]
closed: 2026-09-27
commit: 0123456789abcdef0123456789abcdef01234567
---

# R-0001 · Home the wiki page-type constants in _wikilib

## Why

Four page-type constants were typed in three files.

## Log

- 2026-09-20 new->next [idea->planned]: harvested by adopt
- 2026-09-22 next->now [planned->active]: started
- 2026-09-27 now->done: commit 0123456789abcdef0123456789abcdef01234567
```

A dropped item differs in these ways:
- it has `state: dropped` and `reason: <text>` instead of `commit:`;
- its description reads `Closed roadmap item R-0002 (dropped 2026-09-28).`;
- its last log line is `- 2026-09-28 later->dropped: <reason>`.

For example, archive/0002-p0-p3-priority-labels.md carries `horizon: later`, `tags: []`, `closed: 2026-09-28` and `reason: a second priority axis (adr 0022)`.

The archive frontmatter has these keys, in this canonical order: `name, type, status, title, description, id, state, horizon, origin, spec?, blocked_by, follows?, severity?, tags, closed, commit|reason`.
- `name` is `"%04d-%s" % (n, slug)`, which equals the file stem. The slug is generated from the title AT CLOSE time, or taken from `--slug`.
- `status: active` is the wiki's editorial field; the item state is `state:`.
- `title` is `"R-NNNN · <title>"`. The raw title is recovered by stripping that prefix; a title without it is refused.
- `horizon` is the lane the item was closed from.
- `closed`, `commit` and `reason` are FLAT keys, so no new nesting is introduced. The reader validates `closed` with `check_date("archive/<name> closed", value)` (M3).
- `commit` is the FULL sha resolved by `git rev-parse --verify <sha>^{commit}` **[IMPL-CHOICE]**. An abbreviated sha can become ambiguous later, and the archive is immutable. The reader accepts 40 hex digits (SHA-1 object format) or 64 (SHA-256 object format), via `FULL_SHA_RE` (L4).
- `description` is generated, deterministic, and never user prose.
- An empty why is written as `_(none)_` under `## Why` and read back as `""`, so the shape never varies.
- Never written: `sources`, `targets`, `verified`, `links` (P14).
- Generated archive text contains no backticks at all (P15).

### 5.3 Export JSON (`roadmap-export/1`)

Serialization is `json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"`, written as UTF-8. The full export of the fixture above (outside git, default scope):

```json
{
  "counts": {
    "done": 1,
    "dropped": 1,
    "later": 0,
    "next": 1,
    "now": 1,
    "untriaged": 1
  },
  "items": [
    {
      "blocked_by": [],
      "closed": null,
      "follows": null,
      "horizon": "now",
      "id": "R-0003",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "harvested by adopt",
          "state": null,
          "to": "later"
        },
        {
          "date": "2026-09-29",
          "from": "later",
          "reason": "picked up",
          "state": {
            "from": "idea",
            "to": "active"
          },
          "to": "now"
        }
      ],
      "origin": "tests/test_spawn_stdin.py#skills-survey",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": true,
      "severity": null,
      "spec": null,
      "state": "active",
      "tags": [
        "wiki"
      ],
      "title": "Give _wikilib.git() the server's timeout",
      "why": "The skill-side git helper passes stdin=DEVNULL but has no timeout; the server copy has both."
    },
    {
      "blocked_by": [
        "R-0003"
      ],
      "closed": null,
      "follows": "R-0001",
      "horizon": "next",
      "id": "R-0004",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "harvested by adopt",
          "state": {
            "from": "idea",
            "to": "planned"
          },
          "to": "next"
        }
      ],
      "origin": "docs/adr/0012-the-transport-tier-diverged.md#consequences",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": false,
      "severity": "low",
      "spec": null,
      "state": "planned",
      "tags": [
        "scripts",
        "wiki"
      ],
      "title": "Unify the skill and server git helpers",
      "why": "Two copies of one helper drift; ADR 0012 declined a shared transport tier, not this helper."
    },
    {
      "blocked_by": [],
      "closed": null,
      "follows": null,
      "horizon": "unset",
      "id": "R-0005",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "added",
          "state": null,
          "to": "unset"
        }
      ],
      "origin": "user:2026-09-28:adr-producer",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": true,
      "severity": null,
      "spec": null,
      "state": "idea",
      "tags": [
        "adr",
        "producer"
      ],
      "title": "ADR authoring proposes deferred limits",
      "why": ""
    },
    {
      "blocked_by": [],
      "closed": {
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "date": "2026-09-27",
        "reason": null
      },
      "follows": null,
      "horizon": "now",
      "id": "R-0001",
      "log": [
        {
          "date": "2026-09-20",
          "from": null,
          "reason": "harvested by adopt",
          "state": {
            "from": "idea",
            "to": "planned"
          },
          "to": "next"
        },
        {
          "date": "2026-09-22",
          "from": "next",
          "reason": "started",
          "state": {
            "from": "planned",
            "to": "active"
          },
          "to": "now"
        },
        {
          "date": "2026-09-27",
          "from": "now",
          "reason": "commit 0123456789abcdef0123456789abcdef01234567",
          "state": null,
          "to": "done"
        }
      ],
      "origin": "user:2026-09-20:wiki-constants-home",
      "path": "docs/roadmap/archive/0001-home-the-wiki-page-type-constants-in-wikilib.md",
      "rank": null,
      "ready": null,
      "severity": null,
      "spec": null,
      "state": "done",
      "tags": [
        "wiki"
      ],
      "title": "Home the wiki page-type constants in _wikilib",
      "why": "Four page-type constants were typed in three files."
    },
    {
      "blocked_by": [],
      "closed": {
        "commit": null,
        "date": "2026-09-28",
        "reason": "a second priority axis (adr 0022)"
      },
      "follows": null,
      "horizon": "later",
      "id": "R-0002",
      "log": [
        {
          "date": "2026-09-21",
          "from": null,
          "reason": "added",
          "state": null,
          "to": "later"
        },
        {
          "date": "2026-09-28",
          "from": "later",
          "reason": "a second priority axis (adr 0022)",
          "state": null,
          "to": "dropped"
        }
      ],
      "origin": "user:2026-09-21:priority-labels",
      "path": "docs/roadmap/archive/0002-p0-p3-priority-labels.md",
      "rank": null,
      "ready": null,
      "severity": null,
      "spec": null,
      "state": "dropped",
      "tags": [],
      "title": "P0-P3 priority labels",
      "why": "Proposed as a finer priority axis inside a lane."
    }
  ],
  "schema": "roadmap-export/1",
  "scope": {
    "closed": true,
    "closed_since": null,
    "open": true
  },
  "source": {
    "dirty": null,
    "head": null
  },
  "wip_limit": {
    "now": 3
  }
}
```

Pinned semantics:
- **`source`**: `{"head": <git rev-parse --short HEAD> | null, "dirty": <bool> | null}`.
  - `dirty` is whether the WORKING-TREE content of the roadmap directory differs from HEAD's tree for that directory. **[IMPL-CHOICE]** It is scoped to the directory the export describes. It is computed filter-free by `worktree_dirty(roadmap_dir)` (section 3, S2-2), never by `git status`:
    1. `git rev-parse --show-toplevel` (cwd = the roadmap dir) gives the top-level; the pathspec is the realpath of the roadmap dir made relative to it (`os.path.relpath`, inside section 3).
    2. `git rev-parse --show-object-format` gives `sha1` or `sha256`; anything else, or a nonzero rc, makes `dirty` null.
    3. `git ls-tree -r -z HEAD -- <pathspec>` (cwd = the top-level), run through `git(..., binary=True)` so stdout stays BYTES (no universal-newline translation, so a `\r` in a name survives, and no decode can fail mid-record; round-3 M4), lists the tracked entries as NUL-terminated `<mode> <type> <oid>\t<path>` records. The output is split on `b"\0"`; because every record is TERMINATED (not separated) by NUL, the split yields one trailing empty element, which is dropped, not treated as a malformed record (an empty output, which is an empty directory in HEAD, is likewise zero records; I2). Each record is then split on the first `b"\t"`; the path is turned into a `str` with `os.fsdecode` (surrogate escapes for non-UTF-8 bytes, exactly how Python spells the names `os.walk` yields). A record that does not split into four fields, or whose oid is not hex of the object format's length, makes `dirty` null.
    4. For each entry, the working-tree entry is `os.lstat`-ed first and NEVER followed (S3-1):
       - mode `120000` (a tracked symlink): the entry must itself be a symlink (`S_ISLNK`); its target is read with `os.readlink` on the `os.fsencode`-d path (bytes, which is what git stores as the symlink blob) and hashed; the link is never opened;
       - mode `100644` / `100755`: the entry must be a regular file (`S_ISREG`) no larger than `PAGE_MAX_BYTES`, and its bytes come from `read_regular(path, PAGE_MAX_BYTES)`; the executable bit must match the mode;
       - anything else (a submodule `160000`, an unknown mode) is dirty.

       Each blob is hashed in Python: `hashlib.new(fmt, b"blob %d\x00" % len(data) + data).hexdigest()`, compared with `<oid>`. A missing entry, a type mismatch (a tracked file swapped for a symlink, a FIFO or a directory; a tracked symlink swapped for a file), an oversized file, a hash mismatch or a mode mismatch is dirty, decided WITHOUT reading the non-regular or oversized entry.
    5. Any entry under the directory on disk (`os.walk`, `followlinks=False`, names as `str`, and an `onerror` callback that RE-RAISES the `OSError`, so a subdirectory that cannot be listed makes `dirty` true instead of being silently skipped, which would under-report; L3) that ls-tree did not list (an untracked name, including an ignored one and a `.roadmap-*.tmp`, and an untracked symlink) is dirty. Both sides are compared as `os.fsdecode`-d relative paths joined with `"/"`, so a non-UTF-8 name compares equal to itself.

    **Failures are results, never tracebacks (NFR-13).** A git call that fails (any nonzero rc, including 124/126/127) or a malformed/undecodable `ls-tree` record makes `dirty` null; an `OSError` while stat-ing, reading or walking a single entry (a permission, a race) makes `dirty` true, because a file that cannot be proven equal is reported as possibly different, the declared over-report direction.

    No command roadmap.py runs refreshes the index, so no clean filter, smudge filter or fsmonitor configured in the target repository is ever executed. The index itself is not consulted: a change staged and then reverted in the working tree is not dirty, because the exported bytes then equal HEAD's.
  - **Declared direction of error.** The comparison is raw bytes against HEAD's stored blob. It can only OVER-report dirty (for example under `core.autocrlf` or a clean filter, where git would call the file clean because the stored blob is the filtered form; or an ignored file under the directory), never under-report: any byte difference between the exported content and HEAD is reported. That is acceptable for an advisory flag, and SKILL.md's export contract says so (Step 8).
  - Both values are `null` outside a git work tree, and `dirty` is `null` when HEAD does not exist yet (an unborn branch) or when the installed git does not know `rev-parse --show-object-format` (an old git; the rc is nonzero, step 2).
- **`scope`**: `{"open": true, "closed": bool, "closed_since": null | "YYYY-MM-DD"}`.
  - Open items are always included.
  - `closed` is an ADDITIVE key **[IMPL-CHOICE]**: `--open-only` gives `closed: false`, and without it `closed` is `true`. `--closed-since D` keeps only closed items with `closed >= D`.
- **`counts`**: always all six keys (`untriaged` = live `unset`; `later`, `next`, `now`; `done`, `dropped`), counted over the items IN the output (ADR 0018). `--open-only` yields `done: 0, dropped: 0`.
- **`wip_limit`**: `{"now": meta["wip_now"]}`. The flat frontmatter key is rendered in the design's shape.
- **`items` order**: live items in file order (now, next, later, inbox; within a lane, block order), then closed items by ascending id.
- **`rank`**: 0-based position within the lane for live items, `null` for closed items.
- **`ready`** (the ONE derived field): for a live item, `true` iff every `blocked_by` id names a CLOSED item, whether `done` or `dropped`. A dropped blocker is a void dependency; a live blocker keeps the item `false`. For closed items it is `null`.
- **`horizon`**: the lane for live items; for closed items, the lane it was closed from (a raw field).
- **`log[]`**: `{"date", "from" (null for new), "to", "reason", "state": {"from","to"} | null}`. `state` is an ADDITIVE key relative to the design example.
- **`closed`**: `null`, or `{"date", "commit" | null, "reason" | null}`.
- **`path`**: relative to the parent of the wiki root (docs/roadmap/...), never to the cwd. This is deterministic without git.
  - **A path that is not UTF-8 refuses the export, on both routes alike (L4).** Archive names are ASCII by `ARCHIVE_NAME_RE`, so the only place a lone surrogate can enter a path field is the wiki root's own directory name (a non-UTF-8 byte, decoded with a surrogate escape). Without a rule the two routes would diverge: stdout would carry the surrogate backslash-escaped (`_write_stream`), while `--out` would hit the strict encode in `write_atomic` and die. So `build_export` (pure) checks every `path` field BEFORE either route writes anything and refuses with `export path %r carries a lone surrogate (U+%04X; a directory name that is not UTF-8) -- rename it; the export is UTF-8 JSON`. Escaping the surrogate instead was rejected: the JSON would then carry a path that names no file. Result: `export` and `export --out` both exit 2 with the same one line, stdout is empty and `--out` is not created.
- **`why`**: raw markdown, `""` when absent. HTML escaping is the consumer's job (SKILL.md says so).
- **Versioning**: an additive field keeps `/1`; a breaking change bumps the version.

### 5.4 Key Design Decisions
- **The lock is two digests, re-checked twice for close.**
  - `Snapshot = (sha256(roadmap.md bytes), sha256("\n".join(sorted archive listing)))`, where the listing excludes `TMP_PREFIX` names.
  - `write_atomic` re-takes the snapshot AFTER the temp file is complete and chmod-ed and IMMEDIATELY before `os.replace`. A mismatch is a `die`, and the temp file is unlinked.
  - `close` re-checks before `create_exclusive`, then again inside `write_atomic`. The second time, the expected snapshot is S with the archive digest recomputed for exactly one added name.
  - Rationale: `add` derives the next id from the archive listing, so a concurrent close must invalidate it.
```python
def write_atomic(path, text, expect, conflict=None):
    try:
        data = text.encode("utf-8")     # user values are check_line-clean; a PATH-derived
    except UnicodeError as exc:         # export field is not, so this is the backstop
        io_fail("encode the text for", path, exc)
    directory = os.path.dirname(path)   # pure; replaced by the absolute form below
    try:
        directory = os.path.dirname(os.path.abspath(path))   # abspath reads the cwd (H1)
        mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=TMP_PREFIX, suffix=TMP_SUFFIX)
    except OSError as exc:              # unwritable or vanished directory: no traceback
        io_fail("create a temporary file in", directory or path, exc)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        if expect is ABSENT:
            _publish_link(tmp, path, data, "%s already exists -- init never overwrites" % path)
        else:
            if expect is not UNLOCKED:
                check_snapshot(path, expect, conflict)   # die() here -> except below unlinks tmp
            os.replace(tmp, path)
    except OSError as exc:              # ENOSPC, EACCES, EXDEV...: clean up, then one line
        _discard(tmp)
        io_fail("write", path, exc)
    except BaseException:               # SystemExit from die(), KeyboardInterrupt: clean up, re-raise
        _discard(tmp)
        raise
```
`io_fail` raises `SystemExit` from inside the `except OSError` clause; a sibling `except BaseException` clause does not see an exception raised in another handler, which is why each clause does its own `_discard(tmp)`. `_discard(tmp)` is `os.unlink` under an `except OSError: pass` (a missing temp is already clean).

Under the per-call `io-handler` rule (5.1, H1) every IO call above is in a guarded `try` body: `os.path.abspath`, `os.stat` and `tempfile.mkstemp` in the first, `os.fdopen`, `.write`, `.flush`, `os.fsync`, `os.chmod` and `os.replace` in the second; `os.path.exists` is in `NONRAISING_IO_CALLS`, `os.path.dirname` is pure, and the handlers call only local functions (`_discard`, `io_fail`). The first line exists only so that `directory` is bound when `abspath` itself fails (a deleted cwd); the refusal then names the path as given.
- **Exclusive create of a COMPLETE archive file.** **[IMPL-CHOICE; same semantics as the locked O_EXCL decision]**
  1. Write the text to `mkstemp` in `archive/` and `fsync` it.
  2. `os.link(tmp, final)`, which fails with `FileExistsError` exactly like `O_EXCL`.
  3. `fsync` the directory (best effort) and unlink the temp file.

  A SIGKILL mid-write can therefore never leave a truncated `NNNN-*.md` that "archive wins" would then trust. When `os.link` raises an `OSError` other than `FileExistsError` (a filesystem without hard links), the fallback is `os.open(final, O_CREAT|O_EXCL|O_WRONLY, 0o644)` + write + `fsync`, unlinking `final` on exception. As a backstop for the fallback path, the reader refuses an archive file that does not parse completely.
  `_publish_link(tmp, final, data, exists_message)` is shared by `create_exclusive` and by `write_atomic(..., ABSENT)` (init). It receives the encoded bytes `data` because the fallback branch must write the content into `final` itself; re-reading the temp file would be a second read of something that may already be gone (L1). On `FileExistsError` from either branch it `die`s with `exists_message`; any other `OSError` of the fallback branch unlinks `final` (via `_discard`) and dies through `io_fail("create", final, exc)`. **The temp file never survives `_publish_link` (L2).** After a successful `os.link` the temp name is a SECOND hard link to the published file, so `_publish_link` unlinks it (`_discard(tmp)`) as its last step; on the fallback branch it unlinks the temp both when the fallback succeeds and when it fails. A leftover would be invisible otherwise: the archive listing excludes `TMP_PREFIX` names and `ARCHIVE_NAME_RE` never sees them. The directory `fsync` of step 3 (`os.open` of the directory, `os.fsync`, `os.close`) is best effort and sits in its own `try` / `except OSError: pass`, so it is guarded under the per-call rule. `create_exclusive` holds its own `mkstemp` / write / `fsync` under the same `except OSError` -> `_discard(tmp)` + `io_fail` shape as `write_atomic` (NFR-13). Only `cmd_close` calls `create_exclusive`.
  **A missing `archive/` is created, not refused (M5).** `create_exclusive` calls `ensure_archive_dir(os.path.dirname(final))` before its `mkstemp`. The alternative, `load_state` refusing a roadmap without `archive/`, was rejected: git does not track an empty directory, so EVERY fresh clone of a repository whose roadmap has no closed item lacks `archive/`, and a refusal would break the first `close` in every such clone (a hand-deleted empty `archive/` is the same state; `cmd_init` itself no longer produces it, because it now creates `archive/` before roadmap.md, H1). `read_state_files` already reads a missing `archive/` as an empty listing, and creating the empty directory leaves the listing empty, so the archive digest of snapshot S is unchanged and the lock re-check before `create_exclusive` still holds.
- **Repair placement.** A duplicate id (live + archive) is repaired in `build_state`: the archive wins and the live copy is dropped from `lanes`.
  - Mutating commands persist the repair inside their own `commit` and print `roadmap: repaired: ...` to stderr. The command's exit status is unaffected.
  - Read-only commands (`list`, `show`, `export`) repair in memory and print `roadmap: note: ... -- run roadmap.py render to persist the repair`. **[IMPL-CHOICE]**
- **The default target resolves against the git top-level, not the cwd.** `resolve_target(None)` joins DEFAULT_FILE to `git rev-parse --show-toplevel` of the cwd, falling back to the cwd. Invoking from a subdirectory must not create a second docs/roadmap/ there.
- **The target's shape is checked, and symlinks are refused (L5, S-L2).**
  - `main()` passes the resolved absolute path to the pure `check_target_shape(path)`: the basename must be `roadmap.md`, its directory's basename must be `roadmap`, and the wiki root (the parent of `roadmap/`) must not be the filesystem root. Only then is the wiki root well defined, and the name scan can never walk an unrelated tree.
  - `refuse_symlink(path, what)` is called on roadmap.md and on the archive directory by `read_state_files`; by `cmd_init` on the archive directory as its FIRST step, before anything is created (H1); again inside `ensure_archive_dir` (which `cmd_init` and `create_exclusive` call); and by `check_out_path` on `--out`. A symlinked roadmap.md is read THROUGH the link while `os.replace` replaces the link itself, so the file read and the file written would differ. A symlinked archive directory or `--out` would put an exclusive create or an export write outside the tree the caller named.
- **The roadmap directory must resolve inside its tree (S4-1, CWE-59).** `refuse_symlink` on roadmap.md and `archive/` does not cover a symlinked `roadmap/` directory or a symlinked ancestor (`docs -> /elsewhere`): every write would then land outside the repository while every per-file check passed. `main()` therefore calls the section-3 `check_containment(path)` right after the pure `check_target_shape`, before ANY command runs (init included, so nothing is created on a refusal):
  1. `refuse_symlink(roadmap_dir, "roadmap directory")` on the roadmap directory itself (`os.path.islink` is false for a missing directory, so `init` into a fresh tree passes; a dangling symlink is refused);
  2. `base` = the realpath of `git rev-parse --show-toplevel`, run with cwd = the nearest EXISTING lexical ancestor of the wiki root's parent, when that call succeeds (the tree is inside a work tree); otherwise (not a work tree, or any git failure: 124/126/127) `base` = the realpath of the wiki root;
  3. `os.path.realpath(roadmap_dir)` must equal `base` or start with `base + os.sep`; otherwise `%s resolves to %s, outside %s -- refusing to write through a symlinked directory` (the lexical roadmap directory, its realpath, `base`).

  Realpaths are compared on BOTH sides, so a symlinked ancestor ABOVE the tree (macOS `/var -> /private/var`, which is where every mkdtemp sandbox lives) is harmless: `git rev-parse --show-toplevel` answers the resolved spelling too. `os.path.realpath` and `os.path.isdir` run under `except OSError` -> `io_fail("resolve", ...)`. **Declared limits (ADR 0022):** outside a work tree there is no containing tree to measure an ancestor against, so a symlinked `docs/` there is accepted (the caller named that path); and a symlink swapped in between the check and the write is the same one-syscall race class as the lock window.
- **The local git config is trusted (S4-3; the trust boundary, stated).** roadmap.py runs in whatever repository the user is in, and it treats that repository's `.git/config` as TRUSTED: `git clone` does not transfer it, and anyone who can write it can already run code as the user (an alias, `core.pager`, a hook) the next time the user runs `git` there. What a cloned repository CAN carry is `.gitattributes`, and an attribute alone runs nothing without a driver defined in config. The guarantee roadmap.py makes, and gates, is therefore about its OWN behaviour: it never runs an index-refreshing command (`status`, `diff`, `add`), so a clean filter or fsmonitor is never reached through it (group H plants both and proves neither fires; S2-2). The `GIT_SAFE_ARGV` settings (`core.fsmonitor=false`, `core.hooksPath=/dev/null`, `protocol.allow=never`) and `GIT_NO_LAZY_FETCH=1` in the child environment are cheap defense in depth against a config that is hostile anyway, DECLARED, not a gated guarantee: no suite case exercises a promisor remote or a lazy fetch, and none is added.
- **Generated text never contains a backtick.** Keys, log lines, the summary, archive headings and descriptions contain none. Every single-line user value (title, reasons, origin, spec, severity, tags, slug) is refused by `check_line` if it carries one. That makes user `why` prose the ONLY place a backticked anchor can enter, and only because `check_line` closes every other route; before it, a title or a reason could carry one too (M6). SKILL.md tells authors to put anchors in `origin:` instead. ADR 0022 declares the residual: an archived why with a backticked anchor that later vanishes is a permanent `verify` finding that the archive cannot fix.
- **One line rule: `check_line(field, value)` (S-M1).** It is the ONE place that decides which characters a single-line value may hold, and it is applied to EVERY single-line user value before anything is rendered: title, every reason (`add`, `move`, `wip`, `close --reason`, and so the dropped closing log line), origin, spec, severity, each tag, and the slug. It refuses:
  - any character `ch` for which `len(("a" + ch + "a").splitlines()) != 1`, computed at run time rather than typed, so it tracks the running Python's `str.splitlines`. On Python 3.9+ that is `\n`, `\r`, `\v` (`\x0b`), `\f` (`\x0c`), `\x1c`, `\x1d`, `\x1e`, `\x85`, U+2028 and U+2029;
  - every other C0 control (`\x00`-`\x1f`, so the tab too);
  - every lone surrogate, U+D800-U+DFFF (M4, S2-3). `json.loads` turns a `\ud800` escape in an item file into one, and Python decodes non-UTF-8 argv bytes into U+DC80-U+DCFF; either would make the UTF-8 encode in `write_atomic` or `_write_stream` raise a traceback instead of a refusal;
  - the backtick.

  Why it matters: each of these values is written onto exactly one line (a heading, a `key: value` line, a log line). The `wip` reason is written into the frontmatter comment line `# wip_now set to N on <date> (was M): <reason>`, so a newline there would start a new line and forge a frontmatter key (`status: deprecated`); a `\r` or U+2028 splits the line for any reader that honours it. Refusal messages render the value with `%r`, which escapes every refused character, so a refusal can never break the one-line stderr rule (NFR-2).
- **Values are validated against the wiki parser before they are written** (`check_scalar(field, value)`). It calls `check_line(field, value)` FIRST (so the character rule lives in one place), then strips the value and refuses it if it is empty, wrapped in `[...]`, or wrapped in matching quotes. **It RETURNS the stripped value, and every caller writes that return value, never its own argument (M2, round 5):** title, origin, spec, severity and the close reason are all stored as `check_scalar`'s result. The reason is the " x" title: the checks run on the stripped value, so a title `" x"` passes them, but writing the UNSTRIPPED argument would render `## R-NNNN ·  x` / `title: R-NNNN ·  x`, which the wiki parser and the strict re-read both read back as `x`, and the run would end in the `internal:` round-trip error instead of storing `x`. **Every reason is also stripped and refused when empty, with ONE message (M1, round 5):** `check_reason(value)` (section 4) calls `check_line("reason", value)`, strips, refuses an empty result with `reason is empty (after stripping whitespace)`, and returns the stripped text. It is the one entry for the `add` reason (flag or item file), `move`, `wip` and `close`, on both the `--reason` and the `--reason-file` route, so an empty reason never reaches a log line (`LOG_RE` requires `\S` after `": "`) or the internal round-trip error. **The close reason additionally passes `check_scalar("reason", ...)` (H1, round 5)**, after `check_reason`, because it is also written as the archive frontmatter scalar `reason:`; a quote- or bracket-wrapped close reason is therefore refused with the scalar message, while the same text stays legal in a `move` / `wip` reason, which is written only into a log or comment line. `check_list_item` also calls `check_line` first, then validates ids with `ID_RE` and tags with `TAG_RE`, so inline lists never need escaping. `check_why(text)` shares `check_line`'s separator test (one helper, `_line_break(ch)`), refuses every separator except `"\n"`, every C0 control except `"\n"` and `"\t"`, and every lone surrogate, and returns the text with leading and trailing blank lines stripped (L3).
- **Untrusted text never reaches a command line (S-H1).** The adopt flow turns repository prose into titles, whys and origins. If the main context built `python3 roadmap.py add --title "<harvested>"` in Bash, a `$(...)`, a backtick or a `"` in that prose would execute as the user, and this is a generic plugin run in any repository. So:
  - `add --item-file PATH` reads the whole item from a staged file. **[IMPL-CHOICE] The format is a JSON object** `{"title", "origin", "why"?, "tags"?, "severity"?, "horizon"?, "reason"?}`, parsed with `json.loads` (stdlib). Why JSON rather than the `key: value` subset: JSON has one well-defined escaping for every character, including newlines in `why` and quotes anywhere, so the stager never has to know roadmap.py's refusal rules to encode a value; the `key: value` subset refuses quote-wrapped values and has no multi-line value, so a harvested why could not be carried in it. `parse_item_file(text, where, command)` takes its allowed and required keys from `ITEM_FILE_KEYS[command]` / `ITEM_FILE_REQUIRED[command]`, and refuses a non-object, an unknown key for that command, a duplicate key (`object_pairs_hook`), a missing required key (for `link`: an empty object), a non-string scalar, and a `tags` / `blocked_by` that is not a list of strings. Every value then goes through the same `check_line` / `check_scalar` / `check_list_item` / `check_why` (and, for `spec`, `SLUG_RE`) as the flag route; the item file bypasses nothing.
  - On `add`, `--item-file` is exclusive with `--title`, `--origin`, `--why`, `--why-file`, `--tags`, `--severity`, `--horizon` and `--reason`. The token-shaped flags `--state`, `--spec`, `--blocked-by` and `--follows` may accompany it.
  - **`link --item-file PATH` (S2-1).** Before this, `link --origin` was the one free-text value with no staged route: an origin whose heading slug carries `$(id)` had to be typed on the Bash line. `link --item-file` reads a JSON object with the keys `origin`, `spec` and `blocked_by` (a list of id strings), at least one of them. It is exclusive with `--origin`, `--spec`, `--no-spec` and `--blocked-by`; the token-shaped `--unblock`, `--follows` and `--no-follows` may accompany it. **[IMPL-CHOICE]** `tags` and `severity` are NOT link keys: `link` has no flag that edits them, and editing them after `add` is out of scope (section 1); the security finding named them, and this plan declines that part as a scope widening.
  - **`--spec` and `--slug` are kebab slugs (S2-1).** Both must match `SLUG_RE` after `check_line`, so the only inline free-form values SKILL.md allows are provably shell-inert inside single quotes. This loses nothing: every wiki page name and stem is a kebab slug, ADRs included (`0021-contain-by-the-admitted-root`; all `name:` values under docs/ match `[a-z0-9-]` as of this plan).
  - `--reason-file PATH` exists on `move`, `close` and `wip`, exclusive with `--reason`, mirroring `--why-file`. `reason_from_file` strips exactly one trailing `"\n"` and then calls `check_reason(...)` (which runs `check_line("reason", ...)` first); a file that still holds a line break is refused by `check_line`'s own line-separator message, so there is ONE message for a multi-line reason whatever its route (L2), and a file holding only `"\n"` (or only blanks) is refused with the one empty-reason message (M1, round 5).
  - `read_staged_file(flag, path, roadmap_path)` is the one reader for all three staged files: it first refuses a path that resolves to the roadmap or into its archive, then reads through `read_regular(path, STAGED_MAX_BYTES)` and maps its reason to the P2 pair plus the size refusal: `READ_MISSING` -> `%s %s does not exist`, `READ_NOT_REGULAR` (a directory, a FIFO, a symlink: the stager writes plain files) -> `%s %s is not a regular file`, `READ_TOO_LARGE` -> the existing size refusal, an `OSError` -> `io_fail("read", path, exc)` (round-3 M2). Then strict UTF-8 (P3).
  - roadmap.py itself never uses a shell (NFR-10); the staged file is data, read through `read_regular`, never evaluated.
- **Lists are always inline** (`blocked_by: []`, `tags: [adr, producer]`), sorted and de-duplicated. Optional scalars (`spec`, `follows`, `severity`) are omitted when unset. `origin` is required on `add` because it is the dedup key.
- **`move` carries state changes.** `move <id> <lane> [--state idea|planned|active] --reason R`:
  - a same-lane move is allowed only with a real state change;
  - the log bracket records the state change;
  - a lane change appends the item at the END of the target lane, while a same-lane state change keeps its rank;
  - `inbox` is accepted as an alias for `unset`. The lane argument of `move`, `--horizon` on `add` and `list`, and the item file's `horizon` are validated IN CODE through `LANE_INPUT` (never argparse `choices=`, whose usage error is multi-line, L6): an unknown value is refused with `lane %r is not one of now, next, later, inbox` (move) or `--horizon %r is not one of now, next, later, inbox` (add, list, item file).
- **The WIP cap is a flat scalar `wip_now`.** It counts every item whose horizon is `now`, whatever its state.
  - It is checked on transitions INTO `now` (`add --horizon now`, `move ... now`) and by `wip` itself. It is never checked on load, so a hand-lowered cap cannot block moves out of `now`.
  - The refusal names the occupying items.
  - `wip <N> (--reason R | --reason-file PATH)` is the single-writer route for changing it. It refuses a no-op, a non-positive N, and an N below the current `now` occupancy (naming the occupants). It rewrites the note line above `wip_now:` to `# wip_now set to N on <today()> (was M): <reason>` and ends in `commit(state)` like every other write.
- **`ready` is true iff every blocker is closed** (done OR dropped). `list --ready` and the export agree. `ready` and the cycle check run over live + archive; archive edges are frozen, so a new cycle can only be created through a live item.
- **The wiki-wide name scan** `wiki_page_names(root)` reads every `*.md` under the wiki root except INDEX.md and dot-directories (`os.walk` with `followlinks=False`). Each file is read through `read_regular(path, PAGE_MAX_BYTES)` (S3-1), so a symlinked, special or oversized `*.md` is SKIPPED with one `note:` line naming it and the reason, never followed, opened or crash-read; an `os.walk` error is skipped the same way (its `onerror` callback notes and returns). `os.walk` is in the gate's `NONRAISING_IO_CALLS` for exactly this reason: it does not raise a listing error, it hands it to `onerror`, and the gate requires every `os.walk` call to pass `onerror=` so that route is always explicit (M1). `worktree_dirty` passes an `onerror` that re-raises (L3), and its walk sits in its guarded body. The text is decoded with `errors="replace"` because this scan reads files roadmap.py does not own. It returns TWO things:
  - `names`: every file's basename stem AND its top-level frontmatter `name:` (when it has one), each mapped to the file it came from. This is the slug-collision map; it is deliberately wider than the wiki's SKIP_DIRS, so roadmap.py holds no copy of it, and a false refusal costs only `--slug`;
  - `pages`: the frontmatter `name:` values of the files that are wiki PAGES, meaning the file opens with a `---` frontmatter that closes, and carries a non-empty top-level `name:` line between the two fences (the same lenient look the stem/name map already takes, NOT the strict `parse_kv_lines`, which would `die` on a page roadmap.py does not own; a file without that shape is simply not a page). A bare stem does NOT make a page (L8).

  `--spec` is validated against `pages`, after `SLUG_RE` (S2-1): a `--spec` naming the stem of a frontmatter-less `*.md` (a source note, a README) is refused with the existing `--spec %r is not a wiki page under %s`.
- **Whole-file ownership => whole-file render.** `commit(state)` runs in this order:
  1. `validate`;
  2. `text = render_roadmap(state)`;
  3. re-parse `text` and compare the parsed model with the in-memory one; a mismatch is `internal: the rendered file does not parse back to the model (%s) -- nothing was written`;
  4. if `text == state.text`, write nothing (mtime untouched);
  5. otherwise `write_atomic(path, text, state.snapshot, conflict)`.
- **The summary renderer is generic.** `render_table(headers, rows) -> str` uses `_md_cell` (P6 vocabulary), so `table_cells` can call it with no adapter (P20). The region's first line, `WIP now: A of B. Archive: N closed items (D done, X dropped).`, is rendered from the model, never typed.
- **Wiki constants.** Two constants join the four that decision 5 moves: `ORPHAN_EXEMPT_TYPES` and `INDEX_COUNTED_TYPES`. They replace inline literals in four function bodies across two files, and all six are parity-gated. The count line's section (`## roadmap`) and wording are roadmap-specific and are commented as such.

### 5.5 Risk Mitigation (the risk register — each row is gated by a named test written first)

| # | Risk (what can silently go wrong) | Mitigation | Gate (suite:group) |
|---|---|---|---|
| R1 | Lost update between parallel sessions | two-digest lock re-checked before every durable write | roadmap:F lock cases + control with the re-check disabled |
| R2 | Crash between archive create and roadmap.md replace | archive-first order; duplicate-id repair, archive wins | roadmap:G planted "both" fixture; repair reported; read commands do not write |
| R3 | Truncated archive file trusted by "archive wins" | link from an fsynced temp; strict archive reader | roadmap:G planted truncated archive refused; `os.link` failure leaves nothing |
| R4 | roadmap.py writes something the wiki parser reads differently | `check_scalar`; inline lists always; optional scalars omitted; differential parse against BOTH wiki parser copies | roadmap:C (edge-value corpus) + control (planted quoted title detected) |
| R5 | roadmap.py's type names drift from the wiki's constants | `ROADMAP_TYPE` / `ITEM_TYPE` asserted members of `_wikilib` TYPE_ORDER, UNTRACKED_TYPES, ORPHAN_EXEMPT_TYPES, INDEX_COUNTED_TYPES; AST: no SKIP_DIRS/SKIP_FILES copy in roadmap.py | roadmap:A |
| R6 | INDEX bloat (archive pages listed) | INDEX_COUNTED_TYPES filter + one count line in both copies | wiki_index:B; its control lives in wiki_index:E (E1: INDEX_COUNTED_TYPES emptied -> archive paths listed) |
| R7 | Constant drift `mcp-wiki.py` vs `_wikilib` | parity gate on the SIX names + AST check that reindex/freshness define none locally | wiki_index:A; its control lives in wiki_index:E (E2: UNTRACKED_TYPES minus `roadmap` -> the A checker fails) |
| R8 | The two `render_index` / `reindex_collect` copies drift in behaviour, not just constants | byte-identical render + equal collect tuples on one fixture corpus | wiki_index:B/C |
| R9 | The running MCP server, still on old code, lists every archive page | explicit restart step; SKILL.md symptom note | Step 10 discriminating probe: `wiki_call reindex` (check: true) on a staged fixture root holding one unlinked `roadmap-item` page reports 0 orphans (an old server reports 1) |
| R10 | Non-deterministic export | no clock (`--today`); sorted keys; fixed order; golden file | roadmap:H golden twice + outside-git null source |
| R11 | An invariant violated by a command combination | every mutation goes through `commit()` -> `validate()` + re-parse self-check | roadmap:D matrix incl. refusals |
| R12 | Id reuse | next id = max(live ids ∪ archive filename ids) + 1 | roadmap:G "gap preserved", "max from archive when roadmap is empty" |
| R12b | Id reuse after an archive file is DELETED by hand | out of contract; declared in ADR 0022 (git history is the backstop) | — (declared limit) |
| R13 | Archive slug collides wiki-wide (ADRs are also NNNN-slug) | wiki-wide scan of every `*.md` name + stem (wider than SKIP_DIRS) before close; refuse, ask for `--slug` | roadmap:G collision with a planted adr/0014-x.md AND with one under a sources/ dir |
| R14 | A test touches the live docs/roadmap | sandbox_path, run_cli requires --file, child cwd = sandbox, GIT_CEILING_DIRECTORIES, live digests before/after | roadmap:K |
| R15 | A backticked anchor in generated text becomes a permanent verify finding | generated text is backtick-free | roadmap:C `verify_analyze` on the generated corpus = zero gating |
| R16 | `table_cells` fails on a new undeclared aligning renderer | roster row + sweep entry (Step 7, right after Step 5) | table_cells:E stays green |
| R17 | SKILL.md prescribes a non-existent MCP function | only verified names; run `name_existence` | name_existence |
| R18 | `git` hangs (credential prompt, stuck lock) | stdin=DEVNULL + 30 s timeout -> refusal | roadmap:I "git timeout" via a fake git on PATH **[IMPL-CHOICE]**; roadmap:A AST stdin+timeout |
| R19 | A `--commit` value used as a git option (`-...`) | SHA_RE (7-64 hex) before any spawn; `cat-file -e <sha>^{commit}` | roadmap:G commit refusals (`-x`, non-hex, 65-hex; the value never reaches git) |
| R20 | Two archive files for one id (concurrent close with different `--slug`) | reader refuses; pre-close glob check | roadmap:G planted twin |
| R21 | Locale-dependent crash on U+00B7 (`show`, refusal messages naming a title) | `emit` / `note` / `die` write UTF-8 bytes through `.buffer` | roadmap:L under `LC_ALL=C` + `PYTHONIOENCODING=ascii` |
| R22 | Layer purity erodes (IO or clock inside parse/render; a second stdout writer) | module layout rules (5.1): `IO_FUNCTIONS`, the FORBIDDEN names, `PURE_OS_NAMES`, the `write_atomic` caller set | roadmap:A AST purity cases + controls (planted `open(` in `parse_roadmap`, planted `os.path.exists` in `cmd_init` detected; the real module passes) |
| R23 | An item is stranded forever behind a dropped blocker | `ready` = every blocker closed (done OR dropped) | roadmap:E dropped-blocker case + roadmap:H export `ready` |
| R24 | The WIP cap is changed by a hand edit (breaking the single-writer rule), or a lowered cap blocks moves out | `wip <N> --reason` command; WIP checked on transitions into `now` only | roadmap:D `wip` cases + "cap below occupancy still allows move out" |
| R25 | Harvested repo prose executes as the user through the Bash command line that invokes roadmap.py (`$(...)`, backticks, `"`) — CWE-78 | `add --item-file` (JSON) and `--reason-file`; SKILL.md makes staging mandatory for adopt-derived and multi-word values; roadmap.py spawns argv lists only, never a shell | roadmap:I item-file injection cases (the marker file is never created) + roadmap:A AST no-shell; the SKILL.md rule is prose-only (the caller side cannot be gated by a suite) |
| R26 | A line separator in a single-line value forges a line: a frontmatter key via the `wip` reason comment, an item heading via a title, a log line via a reason — CWE-93; a lone surrogate crashes the encode with a traceback — CWE-20 | ONE `check_line` on every single-line value (separators, C0, lone surrogates, backtick); `check_why` refuses every separator but `"\n"`, and lone surrogates | roadmap:C one case per separator + the forged-key attempt + the lone-surrogate cases; roadmap:G the close-route values (`close --reason`, `--slug`); control: a planted `check_line` that admits U+2028 is detected |
| R27 | roadmap.py itself triggers repository-configured code (`core.fsmonitor`, a clean-filter driver) by running an index-refreshing git command — CWE-78/829. Scope (S4-3): the local `.git/config` is TRUSTED (clone does not transfer it; write access to it already equals code execution as the user), so this row does NOT claim protection against a hostile local config | roadmap.py never runs an index-refreshing command; `source.dirty` is hashed in Python from `ls-tree` (S2-2). Declared, ungated hardening on top: the `GIT_SAFE_ARGV` prefix (`core.fsmonitor=false`, `core.hooksPath=/dev/null`, `protocol.allow=never`) and `GIT_NO_LAZY_FETCH=1` | roadmap:H planted fsmonitor + planted clean filter (neither marker is ever created) + their controls; roadmap:A AST (`git()` argv starts with `GIT_SAFE_ARGV`; every `git(` call passes a list literal whose first element is one of `rev-parse`, `cat-file`, `ls-tree`) |
| R28 | A symlinked roadmap.md, archive directory or `--out` redirects a write — CWE-59 | `refuse_symlink` before every read-for-write and write | roadmap:I roadmap.md, archive directory and `init` cases; roadmap:H the symlinked `--out` case |
| R29 | Free text in `link --origin` (or a `--spec` / `--slug` value) is typed onto the Bash command line and executed by the shell — CWE-78 | `link --item-file` stages `origin` / `spec` / `blocked_by`; the script refuses a `--spec` / `--slug` outside `SLUG_RE`; SKILL.md's exact inline allowlist (Step 8), including the single-quoted staged-path pattern `.claude/tmp/roadmap-(stage\|adopt)-[a-z0-9-]+\.(json\|txt)` with skill-chosen names (S4-2) | roadmap:I `link --item-file` injection round trip (the marker is never created), link item-file key refusals, `--spec` outside `SLUG_RE` refused; the SKILL.md allowlist is prose-only |
| R30 | A write route meets a missing directory and dies with a traceback (init into a wiki root without `roadmap/`; close in a fresh clone, where git dropped the empty `archive/`), or a refused init leaves debris | `cmd_init` decides every refusal, then `ensure_archive_dir`, then writes (H1); `create_exclusive` calls `ensure_archive_dir` (M5) | roadmap:B init into a bare wiki root + re-run over the empty-directory crash state; roadmap:I a refused init creates nothing; roadmap:G close with no `archive/` |
| R31 | A symlinked, special (FIFO, device) or huge file where roadmap.py READS (an archive entry, roadmap.md, a staged file, a wiki page in the name scan, a worktree file hashed for `dirty`) redirects the read outside the tree, hangs the process on a FIFO, or exhausts memory — CWE-59/CWE-400/CWE-200 | ONE section-3 `read_regular(path, cap)`: `lstat` + `S_ISREG`, `O_NOFOLLOW` + `O_NONBLOCK` open, `fstat` re-check, read at most `cap` bytes (`STAGED_MAX_BYTES`, `PAGE_MAX_BYTES`, both unargued); `worktree_dirty` hashes a tracked symlink via `os.readlink` and calls any non-regular or oversized entry dirty without reading it (S3-1) | roadmap:I symlinked archive entry + FIFO archive entry (refused, no hang, under a test-side timeout) + oversized archive entry + staged-file missing / not-regular; roadmap:G `show` of a closed item reads through `read_regular`; roadmap:H tracked file swapped for a symlink to a FIFO reads dirty and is never opened (test-side timeout), a legitimately tracked symlink hashes clean; roadmap:A AST `single-reader`: the builtin `open` appears nowhere in roadmap.py, `os.read` only in `read_regular`, `os.open` only in `read_regular`, `_publish_link` (the O_EXCL fallback, the directory `fsync`) and `_silence` (`os.devnull`, write-only), `os.readlink` only in `worktree_dirty` |
| R32 | An `OSError` / `UnicodeError` escapes an IO call as a traceback instead of a one-line refusal (the class rounds 2 and 3 kept finding one instance at a time: `mkstemp` in init and close, `--out` into a missing parent, a missing staged file, `worktree_dirty` on an unreadable file or undecodable git output) | the IO error rule (5.1, NFR-13), PER CALL: every IO call in section 3 (including `_write_stream`'s stream writes) sits in the body of a `try` whose handlers spell `OSError`, which dies through `io_fail` (`cannot <op> %s: %s`) or, where the contract is to report, translates explicitly; the specific refusals stay in front as pre-checks; NO catch-all in `main()` | roadmap:A AST `io-handler` case + its planted controls (a copy of `archive_names_for_id` without its handler; a handler of `except BaseException:`; a `write_atomic` with `mkstemp` moved above its `try`, one guarded and one unguarded IO site; a call placed in a `finally`) + the `os.walk` `onerror=` sub-check + the named exemption list; runtime witnesses, INFO-skipped as root: roadmap:I `init` under a read-only wiki root, roadmap:G `close` with a read-only `archive/` (the `create_exclusive` `mkstemp` backstop) and roadmap:H `export --out` into a read-only directory each exit 2 with one `cannot ...` line; roadmap:H `export --out` into a missing parent (the specific pre-check, H1); roadmap:H `worktree_dirty` on an unreadable tracked file and on an unlistable untracked subdirectory reads `dirty: true` (L3) |
| R33 | A symlinked roadmap directory, or a symlinked ancestor inside the work tree (`docs -> /elsewhere`), redirects `init` / `close` / `commit` writes out of the tree — CWE-59 (S4-1) | `check_containment` before every command: `refuse_symlink` on the roadmap directory; the realpath of the roadmap directory must lie under the realpath of the git top-level (inside a work tree) or of the wiki root (outside one); realpaths on both sides so the macOS `/var` symlink is harmless | roadmap:I symlinked `docs/roadmap` -> an outside directory: `init` and `close` refused, nothing written outside (the outside directory's digests unchanged); symlinked `docs` -> outside inside a git sandbox refused (INFO-skipped without git); every other case, run under the symlinked mkdtemp root, is the control that a symlink ABOVE the tree passes. Declared: an ancestor symlink outside any work tree is accepted |
| R34 | A write to a closed or failing stdout / stderr (`export \| head`, a full disk behind a redirect) escapes as a `BrokenPipeError` traceback or an `Exception ignored` shutdown message (M2) | `_write_stream` in section 3 under the per-call rule: EPIPE on stdout exits 0 silently, another stdout `OSError` is one `cannot write to standard output` line and exit 2, any stderr `OSError` exits 2; `_silence` points the descriptor at `os.devnull` first | roadmap:L the early-closing reader case (exit 0, no `Traceback`, no `Exception ignored`) + in-process stream fakes for the EIO-on-stdout and broken-stderr branches; roadmap:A `io-handler` covers `_write_stream` |
| R35 | A date or token that only LOOKS valid (`2026-02-30`, Arabic-Indic digits that `\d` and `int()` accept, a value ending in `"\n"` that `$` accepts) is admitted and carried into the files or the export (M3) | every validating regex is `re.ASCII` and used with `fullmatch`; every date goes through `check_date` (shape, then `datetime.date(...)`) at parse time | roadmap:I date and token cases (`--today` / `--closed-since` `2026-02-30`, Arabic-Indic digits, a trailing newline; a planted log line and a planted archive `closed:` with `2026-02-30`; an id and a `--commit` with Arabic-Indic digits or a trailing newline); roadmap:A `regex-ascii` AST case + its control |
| R36 | A non-UTF-8 wiki root name makes `export` and `export --out` disagree (escaped text on one route, an encode refusal on the other) (L4) | `build_export` refuses a lone surrogate in any `path` field before either route writes | roadmap:H the non-UTF-8 wiki root case on both routes (same one line, stdout empty, `--out` not created; INFO-skipped where the filesystem refuses the name) |

### 5.6 Migration
- There is no data migration: all roadmap files are new, and `docs/INDEX.md` changes only when `reindex` runs.
- **Restart the running `mcp-wiki` server after Step 3** (`/mcp` reconnect or a new session). Until then, its `render_index` appends unknown types (P12): it would list every archive page and report each one as an orphan. The skill-side `reindex.py` / `freshness.py` are read from the symlinked tree and are current immediately. Before the restart, regenerate INDEX with the CLI (`python3 ~/.claude/skills/p/skills/wiki/scripts/reindex.py --root docs`), not through `wiki_call`.
- The live docs/roadmap/roadmap.md is NOT created by this plan. `init` and `adopt` are a post-implementation follow-up requiring the user's approval (section 1, Out of Scope).

## 6. Step-by-Step Implementation Plan

### Step 1: Home the page-type constants in `_wikilib.py`; rewire reindex.py, freshness.py and the server's orphan predicate (behaviour-neutral)
**Files**:
- `ClaudeCode/skills/wiki/scripts/_wikilib.py:19-21` (modify: add constants after `SKIP_DIRS`)
- `ClaudeCode/skills/wiki/scripts/reindex.py:28-38` (modify: delete the local constants), plus `:65`, `:73-74`, `:82`, `:93` (modify: use `w.`)
- `ClaudeCode/skills/wiki/scripts/freshness.py:34-35` (modify: delete), `:129` (modify)
- `Scripts/mcp-wiki.py:132` (modify: comment), `:148` (modify: add the two new constants below `UNTRACKED_TYPES`), `:2282-2283` (modify: orphan predicate)
- `docs/components/wiki-engine.md:158` (modify: move the anchor)

**Dependencies**: none

**Failure mode introduced**: a value silently changes during the move (e.g. a type dropped from TYPE_ORDER), altering INDEX order or freshness classification.

**Test first**: no case can be written in the new suite yet, because Step 2 builds it. The gate for THIS step is byte identity. Before editing, capture the stdout of `python3 -B ClaudeCode/skills/wiki/scripts/reindex.py --root docs --check` and `python3 -B ClaudeCode/skills/wiki/scripts/freshness.py --root docs` into .claude/tmp/roadmap-step1-before-reindex.txt and .claude/tmp/roadmap-step1-before-freshness.txt. After the edit, both outputs must be byte-identical. (`--check` writes nothing.)

**Description**:
- `_wikilib.py`: add the block below, tab-indented, in the file's own style. Move the `status:` rationale comment verbatim from `reindex.py:31-36` above `INDEX_LABELLED`, and the comment from `freshness.py:34` above `UNTRACKED_TYPES`:
  ```python
  # Page-type vocabulary for the p:wiki scripts. Scripts/mcp-wiki.py keeps its own
  # copy on purpose -- a fleet server never imports a sibling, and amalgamate
  # cannot reach a skill script -- and tests/test_wiki_index.py gates the two
  # copies equal. Change both, or the gate fails.
  TYPE_ORDER = ["overview", "subsystem", "component", "reference", "analysis",
  	"concept", "spec", "runbook", "adr", "glossary"]
  INDEX_LABELLED = ("draft", "deprecated")
  STATUS_FORBIDDEN = ("current", "stale")
  UNTRACKED_TYPES = {"overview", "adr", "glossary"}
  ORPHAN_EXEMPT_TYPES = ("overview",)
  INDEX_COUNTED_TYPES = ()
  ```
- `reindex.py`:
  - delete `:28-38`;
  - `collect` uses `w.STATUS_FORBIDDEN` (`:65`);
  - the orphan predicate becomes `e["type"] not in w.ORPHAN_EXEMPT_TYPES` (`:74`);
  - `render_index` uses `w.TYPE_ORDER` (`:82`, both uses) and `w.INDEX_LABELLED` (`:93`).
- `freshness.py`: delete `:34-35`; `:129` becomes `status = "untracked" if typ in w.UNTRACKED_TYPES else "no-sources"`. Do not touch `DETAIL_STATUSES` (`:40`).
- `Scripts/mcp-wiki.py` (4-space):
  - the comment at `:132` becomes `# Page-type ordering for INDEX / list rendering (mirrors ClaudeCode/skills/wiki/scripts/_wikilib.py; tests/test_wiki_index.py gates parity).`;
  - add `ORPHAN_EXEMPT_TYPES = ("overview",)` and `INDEX_COUNTED_TYPES = ()` after `:148`;
  - the orphan predicate at `:2283` becomes `e["type"] not in ORPHAN_EXEMPT_TYPES`.
- `docs/components/wiki-engine.md:158`: the anchor currently names the freshness.py symbol, which this step deletes. Replace it with the text below (in the page it stays a backticked anchor, which now resolves, because an assignment at any indentation counts, per `Scripts/mcp-wiki.py:1586-1593`):
  ```
  ClaudeCode/skills/wiki/scripts/_wikilib.py:UNTRACKED_TYPES
  ```
- Keep tabs and the `from __future__ import annotations` style in the three skill files (NFR-1).

**Pattern to follow**: P10, P12, P13.

**Verification**:
- The before/after outputs are byte-identical.
- `forge_call test wiki_recall` is green (it loads the server in-process).
- `forge_call test all` is green.

### Step 2: Write the `wiki_index` suite and register it — run RED
**Files**:
- **tests/test_wiki_index.py** (create)
- `tests/run.py:113-114` (modify: add the `run_wiki_index` wrapper after `run_wiki_recall`), `tests/run.py:232-237` (modify: add the SUITES row after `wiki_recall`)
- `project-forge.yaml:117-123` (modify: add a `wiki_index` target after `wiki_recall`)

**Dependencies**: Step 1

**Failure mode introduced**: a gate that silently matches nothing. The tests.md invariant is that every scanner suite must carry a negative control (`docs/subsystems/tests.md:283-285`).

**Description**: the module skeleton follows P16/P17.
- Constants: `NAME = "wiki_index"`; `SERVER = H.repo_path("Scripts", "mcp-wiki.py")`; `REINDEX` and `FRESHNESS` under `ClaudeCode/skills/wiki/scripts/`; `LIVE_INDEX = H.repo_path("docs", "INDEX.md")`.
- `REINDEX` and `FRESHNESS` are loaded with `H.load_module_from_path`. The shared `_wikilib` is taken as `reindex_mod.w` (P10). `run()` snapshots `saved_path = list(sys.path)` before the first load and, in its outer `finally`, restores `sys.path[:] = saved_path` and does `sys.modules.pop("_wikilib", None)` (L8).
- Fixture wiki: `H.TempWorkspace("ph-wiki-index-")`, containing:
  - `overview.md`, one `adr`, one `spec`;
  - `roadmap/roadmap.md` (type `roadmap`, linked from the adr);
  - three `roadmap/archive/NNNN-*.md` (type `roadmap-item`, unlinked);
  - one unlinked `concept` (the orphan control), and one page of an unknown type.

  Expected counts are derived from the fixture list and never typed.
- Groups (no counts in prose or in the docstring):
  - **A. constant parity.** For each of `TYPE_ORDER, INDEX_LABELLED, STATUS_FORBIDDEN, UNTRACKED_TYPES, ORPHAN_EXEMPT_TYPES, INDEX_COUNTED_TYPES`, one case per constant so a failure names it: the server value equals the `_wikilib` value, with the same type and TYPE_ORDER order-sensitive. Further cases:
    - SINGLE HOME: an `ast` walk of `reindex.py` and `freshness.py` finds no module-level assignment to any of the six names;
    - `roadmap` and `roadmap-item` are in TYPE_ORDER and UNTRACKED_TYPES of both copies;
    - `roadmap-item` is in ORPHAN_EXEMPT_TYPES and INDEX_COUNTED_TYPES of both copies.
  - **B. render_index, both copies.**
    - Server output equals skill output, byte for byte, on each corpus variant.
    - No line contains any fixture archive page path.
    - Exactly one line equals `- Roadmap archive: 3 closed items -> roadmap/archive/`, with the 3 derived from the fixture.
    - The roadmap page is listed under `## roadmap`, and there is no `## roadmap-item` heading.
    - The singular form is used for one item (`1 closed item`).
    - With zero archive pages there is NO archive line.
    - An archive-only corpus (no roadmap page) still renders the `## roadmap` heading with only the count line.
    - CLI: `reindex.py --root <sandbox>/docs` writes `<sandbox>/docs/INDEX.md` equal to `render_index` of the same corpus; `--check` writes nothing.
  - **C. reindex_collect, both copies.** On the same corpus:
    - `(entries, dups, orphans, malformed)` are equal between the copies;
    - no `roadmap-item` is among the orphans, and `overview` is still exempt;
    - the unlinked `concept` IS an orphan;
    - an unlinked `roadmap` page IS an orphan (only the item type is exempt, decision 4);
    - there are zero malformed pages.
  - **D. freshness.** `freshness.analyze(root, "HEAD")` and the server `_classify_page` classify `roadmap` and `roadmap-item` pages as `untracked`. A sourceless `concept` stays `no-sources` (control). A `roadmap-item` that wrongly carries `sources:` is NOT untracked, which documents why roadmap.py never writes that key.
  - **E. negative controls.** Each control asserts that the oracle FIRES:
    1. A second load of the server module with `INDEX_COUNTED_TYPES = ()`, and `reindex_mod.w` swapped for a `types.SimpleNamespace` copy with the same change (restored in `finally`), makes the B checker report the archive paths.
    2. A copy of the server namespace with `UNTRACKED_TYPES` minus `roadmap` makes the A checker fail.
    3. A copy with `ORPHAN_EXEMPT_TYPES = ("overview",)` makes the C checker report the archive pages as orphans.
  - **F. hygiene** (last, in `finally`): live `docs/INDEX.md` sha256 unchanged; `H.repo_tree()` delta empty; `H.pycache_snapshot()` shows zero new files; the workspace is outside the repo.
- Wrapper and row:
  ```python
  def run_wiki_index(opts):
      return run_python_suite("test_wiki_index", opts)
  ```
  ```python
      ("wiki_index", run_wiki_index,
       "INDEX.md rendering and the page-type constants: both render_index copies "
       "byte-identical, a roadmap-item never listed and counted in one rendered "
       "line, the orphan exemption, and the server/skill parity of six constants",
       0),   # PLACEHOLDER until the first green run (Step 3) -- DRIFT is expected
  ```
- **Interim SUITES value (M3).** The row is registered in THIS step with the count `0`, an explicit placeholder. `tests/run.py:425-426` checks every non-`None` declared count, so every run of `wiki_index` from now until Step 3 reports `CASE COUNT DRIFT ... declared 0` in addition to the red cases. That DRIFT is expected and is not a defect. `None` is not used as the placeholder, because `None` means "data-derived" (`tests/run.py:182-197`) and would silently disable the count check. Step 3 replaces the `0` with the count of the first green run.
- Forge target: copy P19, with `python3 tests/run.py wiki_index` and a description stating that it writes only a mkdtemp corpus and never the live `docs/INDEX.md`. Its filter is `grep: 'SUITE|AGGREGATE|cases|ALL|FAIL|INFO|SKIP|DRIFT|declared'`, NOT P19's shorter pattern, so the placeholder's `CASE COUNT DRIFT ... declared 0` line is visible in the forge output rather than filtered away (L2; precedent: the `all` target at `project-forge.yaml:41` and `bitbucket_cli` at `project-forge.yaml:139`).

**Pattern to follow**: P16, P17, P18, P19; the negative-control style of `tests/test_table_cells.py` (its control group).

**Verification**: `forge_call test wiki_index`, EXPECTED RED at this step:
- group A fails on the missing roadmap types and the empty INDEX_COUNTED_TYPES / ORPHAN_EXEMPT_TYPES values;
- group B fails on the listed archive pages and the missing count line;
- group C fails on the archive orphans;
- group D fails on `no-sources`;
- group E controls pass (they prove the oracles fire);
- the aggregate reports `CASE COUNT DRIFT` for `wiki_index` (declared 0), as expected (M3).

Record which cases were red. The count committed in SUITES is taken from the first GREEN run (Step 3), never from this run and never typed from prose.

### Step 3: Teach both wiki copies the two types, the INDEX count line and the orphan exemption — turn `wiki_index` GREEN
**Files**:
- `ClaudeCode/skills/wiki/scripts/_wikilib.py` (modify: the Step 1 constants)
- `ClaudeCode/skills/wiki/scripts/reindex.py:78-96` (modify: `render_index`)
- `Scripts/mcp-wiki.py:133-148` (modify: constants), `Scripts/mcp-wiki.py:2287-2304` (modify: `render_index`)
- `tests/run.py` (modify: set the `wiki_index` count)

**Dependencies**: Step 2

**Failure mode introduced**: the two copies diverge (one gets the filter, the other does not). Step 2 groups B/C catch it.

**Description**:
- Constants (both files): the Step 3 values in section 3 (Type Definitions).
- `render_index`: identical logic in both copies, with tabs and `w.` prefixes in `reindex.py` and 4 spaces with module globals in the server. `os` is already imported (`ClaudeCode/skills/wiki/scripts/reindex.py:22`, `Scripts/mcp-wiki.py:71`).
  ```python
  def render_index(entries) -> str:
      groups: dict = {}
      counted = []
      for entry in entries:
          if entry["type"] in INDEX_COUNTED_TYPES:
              counted.append(entry)
              continue
          groups.setdefault(entry["type"], []).append(entry)
      order = TYPE_ORDER + sorted(t for t in groups if t not in TYPE_ORDER)
      lines = [<header unchanged>]
      for typ in order:
          bucket = groups.get(typ) or []
          # The count line and its section are roadmap's (adr 0022); the
          # constant only names WHICH types are counted instead of listed.
          tail = _counted_lines(counted) if typ == "roadmap" else []
          if not bucket and not tail:
              continue
          lines.append("## %s" % typ)
          for entry in sorted(bucket, key=lambda e: e["title"].lower()):
              <entry line unchanged>
          lines.extend(tail)
          lines.append("")
      return "\n".join(lines).rstrip() + "\n"


  def _counted_lines(counted):
      """ONE line for every closed roadmap item, never one line per item:
      INDEX.md is @-included by CLAUDE.md and loaded every session. N is
      rendered, never typed (adr 0019)."""
      if not counted:
          return []
      dirs = sorted({os.path.dirname(e["path"]).replace(os.sep, "/") + "/"
                     for e in counted})
      return ["- Roadmap archive: %d closed item%s -> %s"
              % (len(counted), "" if len(counted) == 1 else "s", ", ".join(dirs))]
  ```
  `order` contains `"roadmap"` (it is in TYPE_ORDER, and the parity gate keeps it there). The count line therefore appears under `## roadmap` even when no roadmap page exists.
- `TYPE_SIGNAL_TOKENS` (`Scripts/mcp-wiki.py:235`) is NOT extended. **[IMPL-CHOICE]** The reasons are in section 1, Out of Scope.

**Pattern to follow**: P12, P13.

**Verification**:
- `forge_call test wiki_index` is GREEN apart from the expected placeholder DRIFT. Replace the placeholder `0` in its SUITES row with this run's case count, then re-run: green with no drift.
- `forge_call test wiki_recall` is green (group O reports the new types as "silent, not wrong").
- `reindex.py --root docs --check` on the live tree is unchanged, since no roadmap pages exist.
- Record the restart note for Step 10.

### Step 4: Scaffold the `roadmap` suite: safety guards, hygiene and the format contract — run RED (target missing)
**Files**:
- **tests/test_roadmap.py** (create)
- `tests/run.py:125-126` (modify: add the `run_roadmap` wrapper after `run_checkpoint`), `tests/run.py:309-327` (modify: add the SUITES row after `checkpoint`)
- `project-forge.yaml:141-147` (modify: add a `roadmap` target after `checkpoint`, with the filter `grep: 'SUITE|AGGREGATE|cases|ALL|FAIL|INFO|SKIP|DRIFT|declared'` so the placeholder DRIFT stays visible, L2, same precedent as Step 2: `project-forge.yaml:41`, `:139`)

**Dependencies**: Step 3

**Failure mode prevented**: R14. The suite must be incapable of touching the live roadmap BEFORE any case that writes exists.

**Description**:
- Constants:
  - `NAME = "roadmap"`;
  - `TARGET = H.repo_path("ClaudeCode", "skills", "roadmap", "scripts", "roadmap.py")`;
  - `LIVE_ROADMAP = H.repo_path("docs", "roadmap", "roadmap.md")`, `LIVE_ARCHIVE = H.repo_path("docs", "roadmap", "archive")`;
  - `WIKILIB`, `SERVER`, `REINDEX`, `FRESHNESS`;
  - the on-disk contract spelled independently of the module: `BEGIN_PREFIX = "<!-- ROADMAP:BEGIN"`, `END_PREFIX`, `COLUMNS`, `TMP_PREFIX = ".roadmap-"`, `DEFAULT_REL = "docs/roadmap/roadmap.md"`, `LANES = (("now", "# now"), ("next", "# next"), ("later", "# later"), ("unset", "# inbox"))`, `SCHEMA = "roadmap-export/1"`.
- Copy (do not import) from `tests/test_checkpoint.py`:
  - `set_sandbox` / `sandbox_path` (`:1039-1058`);
  - `run_cli` (`:1111-1127`), always passing `--today 2026-09-28` unless a case overrides it. The git-isolation defaults `GIT_ISOLATION = {"GIT_CEILING_DIRECTORIES": <sandbox parent>, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}` are always applied, and a case's `env_extra` is MERGED over them (`env = dict(GIT_ISOLATION); env.update(env_extra or {})`), never substituted for them, so a case that passes `LC_ALL` (group L) still cannot discover the repo or the user's git config (L8);
  - `captured` / `call_module` (`:1130-1150`);
  - `refusal` (`:1980`), `temp_leftovers` / `stale_temp_files` (`:1209-1225`, with `TMP_PREFIX = ".roadmap-"`), `problem_if` / `missing_tokens`.
- Add a second runner, `run_cli_default(cwd, *args)`, for the default-target cases only. It asserts that `cwd` is inside the sandbox, that the argv has NO `--file`, and that `GIT_CEILING_DIRECTORIES` is set to the sandbox's parent.
- Helper `stage_roadmap(workspace, case, text, archive=None)` creates `<case>/docs/roadmap/roadmap.md` and `archive/` files inside the sandbox. Each case gets its own wiki root, so the name scan sees only that case's pages.
- **Group A (format contract):**
  - `mod.DEFAULT_FILE == DEFAULT_REL`; the markers start with the prefixes; `mod.SUMMARY_COLUMNS == COLUMNS`; `mod.LANE_HEADINGS == LANES`; `mod.EXPORT_SCHEMA == SCHEMA`; `mod.TMP_PREFIX == TMP_PREFIX`.
  - Type membership (R5): `mod.ROADMAP_TYPE` and `mod.ITEM_TYPE` are in `_wikilib.TYPE_ORDER` and `UNTRACKED_TYPES`, and `mod.ITEM_TYPE` is in `_wikilib.ORPHAN_EXEMPT_TYPES` and `INDEX_COUNTED_TYPES`.
  - AST, stdlib: `roadmap.py` imports only an allowlist of stdlib modules, never names `_wikilib`, never touches `sys.path`, and defines no `SKIP_DIRS` / `SKIP_FILES` (NFR-3).
  - AST, git (R18, R27): every `subprocess.run` call passes `stdin=` and `timeout=`; `subprocess.run` is called only inside `git`, and its first argument is built from `GIT_SAFE_ARGV`; `mod.GIT_SAFE_ARGV` equals the test's own spelling `("git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never")` (a value check on the declared hardening, S4-3; the `GIT_NO_LAZY_FETCH` environment entry is declared and not gated); every call `git(...)` passes a list literal whose first element is a string constant in the test's own set `{"rev-parse", "cat-file", "ls-tree"}` (so no `status`, `diff` or `add`, the index-refreshing commands that would run a planted clean filter, S2-2). Control: a planted source copy calling `git(["status", "--porcelain"], ...)` is detected.
  - AST, no shell (R25, NFR-10): no call anywhere carries a `shell=` keyword, and none of `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `subprocess.getoutput`, `subprocess.getstatusoutput`, `subprocess.Popen` appears.
  - AST purity (R22), against test-declared sets that spell 5.1 independently (H2):
    - `IO_FUNCTIONS` = the section-3 names of 5.1, verbatim;
    - `PURE_OS_NAMES = {"os.path.join", "os.path.basename", "os.path.dirname", "os.path.splitext", "os.path.normpath", "os.sep"}`;
    - outside `IO_FUNCTIONS`, the walk flags every `ast.Name` `open`, every attribute chain rooted at `tempfile`, `subprocess`, `shutil` or `glob`, and every attribute chain rooted at `os` whose dotted name is not in `PURE_OS_NAMES` (called or merely referenced; the chain is taken at its outermost `ast.Attribute`, so `os.path.exists` is judged as `os.path.exists`, not as `os.path`);
    - `datetime.date.today`, `datetime.datetime.now` and anything under `time` occur only in `today`;
    - the attribute chain `sys.stdout` (including `sys.stdout.buffer`) occurs only in `emit` and `emit_raw`, and `sys.stderr` only in `die` and `note`; `_write_stream` names neither, since it takes the stream as a parameter (M1). Control: a planted source copy with `sys.stdout.write(` inside `cmd_list` is detected;
    - there is no `print(`;
    - `create_exclusive` has exactly one caller, `cmd_close`; the callers of `write_atomic` are EXACTLY {`commit`, `cmd_init`, `cmd_export`}. **These two cases are EXACT set equalities and stay exact (round-3 M1).** They are written exact here; the ONE concession is temporal: while `cmd_close` and `cmd_export` do not exist yet (Step 5), each case checks that the OBSERVED caller set is a SUBSET of its declared set and records which declared callers are still absent; from the end of Step 6 on, the case asserts equality. **The switch is tied to a durable signal (L5, round 4):** the case reads the `roadmap` row's declared count from `tests/run.py` at test time, by AST (the `SUITES` list at `tests/run.py:198`; each row is a literal tuple whose fourth element is the int count, so run.py is parsed, never imported), and runs EXACT whenever that count is not the `0` placeholder. The placeholder is replaced by the first green run at the end of Step 6, so from then on the case is EXACT for good: a caller later renamed or deleted fails the equality instead of silently dropping back to SUBSET, because SUBSET mode no longer depends on whether a function happens to be defined. A row that cannot be found or whose count is not an int literal fails the case. Nobody weakens the declared sets to make Step 5 green;
    - every `cmd_*` in `COMMANDS` except `cmd_init`, `cmd_list`, `cmd_show` and `cmd_export` has a `commit(` call;
    - the real module passes all of the above. An earlier draft of this plan would not have: its caller set omitted `cmd_init` and its `cmd_init` / `cmd_close` / `cmd_export` did `makedirs`, the archive glob and the `--out` checks inline (H2). Those now live in section-3 helpers;
    - controls: a planted source copy with `open(` inside `parse_roadmap` is detected, and one with `os.path.exists(` inside `cmd_init` is detected.
  - AST, the IO error rule (R32, NFR-13), case `io-handler`, PER CALL (H1, round 4): for every function in `IO_FUNCTIONS` that is defined, the walk visits every call in its own body (nested defs excluded). A call is an IO CALL when either
    - its dotted callee is FORBIDDEN by the purity rule (the builtin `open`; anything under `tempfile`, `subprocess`, `shutil`, `glob`; anything under `os` outside `PURE_OS_NAMES`) and is not in the test's own `NONRAISING_IO_CALLS`, or
    - it is a method call whose attribute name is `write` or `flush` (`stream.write`, `buffer.flush`: the stream route and file objects, M2).

    Every IO call must lie, at any nesting depth, inside the `body` of an `ast.Try` that has a handler whose type is the NAME `OSError` or a tuple containing that name (`IOError` / `EnvironmentError` do not count, I3; a handler of type `BaseException` or a bare `except` does not count). A call inside a `try`'s `handlers`, `orelse` or `finalbody` is NOT guarded by that `try`; only an enclosing `try` whose BODY contains it counts. Each unguarded call is reported with its callee and line, so the failure names the site.
    - `NONRAISING_IO_CALLS = {"os.path.islink", "os.path.lexists", "os.path.exists", "os.path.isdir", "os.path.isfile", "os.fsencode", "os.fsdecode", "os.strerror", "os.walk"}`, each with its reason written beside it in the test source: the five predicates return False on any `OSError` by their documented contract; `os.fsencode`, `os.fsdecode` and `os.strerror` do no IO; `os.walk` does not raise a listing error, its error route is the `onerror` callback (M1). `os.path.realpath` and `os.path.abspath` are NOT in it, because both can reach `os.getcwd`; `os.close` is NOT in it either (see `read_regular`, Step 5: its `finally` is restructured instead).
    - Sub-check `walk-onerror` (M1): every `os.walk(...)` call in roadmap.py passes an `onerror=` keyword. Without it, a listing error would vanish silently, which is exactly what membership in `NONRAISING_IO_CALLS` must not hide.
    - A function with an unguarded IO call must be a key of the test's `IO_HANDLER_EXEMPT` dict, whose value is the reason. The dict starts EMPTY, because in the code this plan specifies every IO call is guarded: `write_atomic` moves `os.path.abspath` into its first `try` (5.4), `read_regular` wraps its `finally: os.close(fd)` inside an outer guarded `try` (Step 5), `_publish_link`'s directory `fsync` has its own `try`, `_silence` guards its own calls, `refuse_symlink` / `refuse_existing` call only `NONRAISING_IO_CALLS`, and `wiki_page_names`' walk reports through `onerror` while its reads go through `read_regular` (a local call, not an IO call). Any future entry must carry its reason in the test source. A key naming a function that no longer has an unguarded IO call fails the case too, so the list cannot rot. Once the `roadmap` SUITES count is not the `0` placeholder, every name in `IO_FUNCTIONS` must be defined (the same durable switch as the caller sets above, L5).
    - controls, each a planted source copy that must be detected: `archive_names_for_id` with its `try` / `except OSError` removed; the same with the handler spelled `except BaseException:`; **a `write_atomic` with `tempfile.mkstemp` moved ABOVE its `try`** (two IO sites, one guarded, one not: a per-function "has some handler" rule would pass it, so this control is what proves the gate is per call, H1); a `read_regular` whose `os.close(fd)` sits in a bare `finally` with no enclosing guarded `try` (a call in `finalbody` is not guarded); a `wiki_page_names` whose `os.walk` has no `onerror=` (the sub-check).
  - AST, single reader (R31, S3-1), case `single-reader`: the builtin `open` appears nowhere in roadmap.py; `os.read` appears only in `read_regular`; `os.open` only in `read_regular`, `_publish_link` (the O_EXCL fallback and the directory `fsync`) and `_silence` (write-only, on `os.devnull`); `os.readlink` only in `worktree_dirty`. Control: a planted copy of `wiki_page_names` calling `open(` is detected.
  - AST, patterns (R35, M3), case `regex-ascii`: every `re.compile(` call passes `re.ASCII` in its flags; no pattern string literal starts with `^` or ends with `$`; no name ending in `_RE` is used as the receiver of `.match(` or `.search(` (only `.fullmatch(`). Control: a planted copy with `DATE_RE.match(` is detected.
- **Group K (hygiene, runs in `finally`):**
  - the live roadmap's sha256 and the live archive's `H.file_digests` are unchanged (INFO if absent);
  - the `repo_tree` delta is empty and `pycache` shows zero new files;
  - the workspace is outside the repo, and there is no `.roadmap-*` name (temp file or leftover hard link) anywhere in the sandbox, which covers every `init` and `close` case run before it: the link route leaves the temp name as a second hard link to the published file unless `_publish_link` unlinks it, and nothing else would notice, because the archive listing skips `TMP_PREFIX` names (L2). Group B's init cases and group G's close cases also assert it per case, so a leftover names its case;
  - the guards bite: `sandbox_path(LIVE_ROADMAP)` raises, `run_cli("list")` raises, and `run_cli_default(<repo root>, "list")` raises.
  - Group K needs no `mod`: every case in it is about the SUITE's guards and the live tree, so it can and must run when roadmap.py does not exist.
- `run()` mirrors P16 with one deliberate difference (M1). `tests/test_checkpoint.py:3621-3625` records the missing target and RETURNS, which skips hygiene. Here the missing target is recorded as one failing `target-exists` case, every group that needs `mod` is skipped, and group K still runs, from the `finally` that wraps the whole body. The guards are therefore proven to bite BEFORE the first line of roadmap.py exists, which is the point of this step.
- `run()` also applies L8: `saved_path = list(sys.path)` before loading `WIKILIB` / `REINDEX` / `FRESHNESS`, and in the `finally` `sys.path[:] = saved_path` and `sys.modules.pop("_wikilib", None)`.
- SUITES row: registered in THIS step with the count `0` and the comment `# PLACEHOLDER until the first green run (end of Step 6) -- DRIFT is expected` (M3, same reasoning as Step 2). Every `roadmap` run until the end of Step 6 reports `CASE COUNT DRIFT ... declared 0`, and so does `forge_call test all`; that is expected.

**Pattern to follow**: P16, P17, P18, P19.

**Verification**: `forge_call test roadmap`, EXPECTED RED with "the script under test is missing" plus the placeholder DRIFT (visible because of the target's `DRIFT|declared` filter, L2). Group K RUNS and passes (M1): if the report shows no group K cases, `run()` returned early and the step is not done. The SUITES count is set from the first green run (end of Step 6).

### Step 5: roadmap.py core — sections 1-9 and 11 of 5.1; commands init/add/list/show/move/rank/link/render/wip
**Files**:
- **ClaudeCode/skills/roadmap/scripts/roadmap.py** (create)
- **tests/test_roadmap.py** (modify: groups B, C, D, E, F, I, L are written FIRST and run red against a stub whose `main()` refuses everything, then turned green)

**Red-first against a stub, without crashing (M2).** The stub defines only `main()` and the section-1 constants, so most in-process cases would hit an `AttributeError` on their first `mod.parse_roadmap` and abort the whole suite, which is a crash, not a red run. The suite therefore gets one helper, used by every in-process case before it touches the module:
```python
def need(suite, group, cid, mod, *names):
    """Return the named module attributes, or record ONE failing case naming the
    missing ones and return None -- a missing function is a red case, never a
    crash of the suite."""
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        suite.record(group, cid, ["roadmap.py has no %s (not implemented yet)"
                                  % ", ".join(missing)])
        return None
    return [getattr(mod, n) for n in names]
```
A case does `got = need(suite, GB, "round-trip", mod, "parse_roadmap", "render_roadmap")` and returns when `got is None`. CLI cases need no helper: the stub's `main()` refuses, and the case fails on its own assertion. Each group body is additionally wrapped so that an unexpected exception is recorded as one failing case for that group (the traceback's last line as the detail) and the next group still runs.

**Dependencies**: Step 4

**Description**: functions in section order. The style is 4-space, %-formatting, no hints, with a module docstring explaining WHY, like checkpoint.py's.
- **Section 2**: `die`, `note`, `emit`, `emit_raw`, `today` (5.1). They choose a stream and call `_write_stream`; they perform no IO of their own.
- **Section 3 (io).** Every function below follows the IO error rule (5.1, NFR-13), per call: every IO call in its own body sits in the body of a `try` whose handlers spell `OSError`, and the handler either dies through `io_fail(op, path, exc)` or, where the contract is to report, translates explicitly. Group A's `io-handler` case gates it.
  - `_write_stream(stream, text, role)` and `_silence(stream)` (5.1, M2): the stream route, with its defined exits (EPIPE on stdout -> 0, other stdout `OSError` -> one line + 2, any stderr `OSError` -> 2);
  - `io_fail(op, path, exc)` (5.1) and `_discard(tmp)` (best-effort unlink, swallows `OSError`). `io_fail` always receives a real exception: a caller that maps `READ_MISSING` to it constructs `FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)` (L1), which is why only section-3 code maps `READ_MISSING` to `io_fail`;
  - `read_regular(path, cap)` (S3-1), the ONE reader of file content in the module:
```python
def read_regular(path, cap):
    """Read a REGULAR file of at most cap bytes. Never follows a final symlink,
    never blocks on a FIFO, never reads past cap. Returns (data, None) or
    (None, why): why is READ_MISSING, READ_NOT_REGULAR, READ_TOO_LARGE or the
    OSError itself. It never raises OSError; each caller maps `why` to its own
    refusal (die), skip (note) or verdict (dirty)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, READ_MISSING
    except OSError as exc:
        return None, exc
    if not stat.S_ISREG(st.st_mode):
        return None, READ_NOT_REGULAR
    if st.st_size > cap:
        return None, READ_TOO_LARGE
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)       # ELOOP if swapped for a symlink since the lstat
    except FileNotFoundError:
        return None, READ_MISSING
    except OSError as exc:
        return None, exc
    try:                                # outer: guards the reads AND the close (H1)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):   # swapped for a FIFO/device since the lstat
                return None, READ_NOT_REGULAR
            chunks, left = [], cap + 1
            while left > 0:
                chunk = os.read(fd, min(left, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                left -= len(chunk)
        finally:
            os.close(fd)
    except OSError as exc:
        return None, exc
    data = b"".join(chunks)
    if len(data) > cap:                 # grew since the lstat
        return None, READ_TOO_LARGE
    return data, None
```
    `O_NONBLOCK` is what makes a FIFO swapped in between the `lstat` and the `open` harmless: the open returns at once and the `fstat` re-check refuses it. `getattr(..., 0)` keeps the module importable where a flag does not exist (the `lstat` check still applies there).

    **Why the `finally` is nested, not `os.close` declared non-raising (H1, round 4).** `os.close` CAN raise (`EIO` on a network filesystem that reports a deferred write error at close, `EINTR`), so putting it in `NONRAISING_IO_CALLS` would make that set mean "calls we chose not to guard" rather than "calls that cannot raise", and the set is only checkable while it means the latter. Nesting the `try` / `finally` inside an outer `try` / `except OSError` keeps the close unconditional AND guarded: the per-call gate sees `os.close` inside the outer `try`'s body, and a close error becomes the function's reported reason like any other read error, never a traceback. The fd is closed exactly once on every path (the inner `finally` runs before the outer handler);
  - `decode(path, data)` (strict UTF-8, P3 message);
  - `digest` (sha256 hex), `take_snapshot`, `check_snapshot(path, expect, conflict)`. `take_snapshot` reads roadmap.md through `read_regular(path, PAGE_MAX_BYTES)` (`READ_MISSING` -> digest `None`; any other reason is a changed file, so the snapshot cannot match and the lock refuses) and lists `archive/` under `except OSError` like `read_state_files`;
  - `write_atomic` (5.4), `_publish_link(tmp, final, data, exists_message)` (L1), `create_exclusive(path, text)`, whose first call is `ensure_archive_dir(os.path.dirname(path))` (M5, 5.4);
  - `read_archive_entry(path)` (L1): the ONE mapping of an archive entry read, shared by `read_state_files` and `cmd_show` so `cmd_show` stays free of `os` names: `read_regular(path, PAGE_MAX_BYTES)`, then `READ_NOT_REGULAR` -> `archive entry %s is not a regular file`, `READ_TOO_LARGE` -> `archive entry %s is larger than %d bytes -- refusing to read it`, `READ_MISSING` -> `io_fail("read", path, FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path))` (the entry vanished after the listing; `io_fail` gets a real `OSError`, not a reason string), an `OSError` -> `io_fail("read", path, exc)`; returns the bytes;
  - `git(args, cwd, binary=False)` (P9 copy with `GIT_SAFE_ARGV` and the `GIT_NO_LAZY_FETCH=1` child environment, NFR-5, S4-3, and P9's three differences): bytes captured, stdout `os.fsdecode`-d unless `binary=True`; 124 on timeout, 127 if git is missing, 126 on any other spawn `OSError`. On a failure the stdout slot is empty of the mode's type (`""` or `b""`). Every caller passes a list literal whose first element is the subcommand (`rev-parse`, `cat-file` or `ls-tree`; group A checks it);
  - `resolve_target(file_arg)`: `None` -> top-level + DEFAULT_FILE, falling back to the cwd; returns an absolute, normalized path. `os.path.abspath` reads the cwd, so a deleted cwd is an `OSError` -> `io_fail("resolve the working directory for", file_arg or DEFAULT_FILE, exc)`;
  - `check_containment(path)` (S4-1, 5.4): `refuse_symlink` on the roadmap directory, then the realpath of the roadmap directory must lie under `base` (the realpath of the git top-level found from the nearest existing lexical ancestor of the wiki root's parent, or the realpath of the wiki root when that is not a work tree or git fails); refusal `%s resolves to %s, outside %s -- refusing to write through a symlinked directory`. Called by `main()` after `check_target_shape`, before any command, so a refused `init` or `close` creates and writes nothing;
  - `refuse_symlink(path, what)`: `die` when `os.path.islink(path)` (S-L2);
  - `refuse_existing(path)`: `die("%s already exists -- init never overwrites" % path)` when `os.path.lexists(path)`; lets `cmd_init` decide that refusal before it creates anything (H1);
  - `ensure_archive_dir(path)`: `refuse_symlink`, then `os.makedirs(path, exist_ok=True)`, which also creates `roadmap/` (and any missing parent) when absent. Called by `cmd_init` and by `create_exclusive` (M5). An `OSError` (a read-only wiki root, a file where a directory should be) -> `io_fail("create directory", path, exc)`;
  - `read_state_files(path)`: `refuse_symlink` on roadmap.md and on the archive directory, then returns (roadmap bytes, {archive name: bytes}, snapshot, display paths). roadmap.md is read through `read_regular(path, PAGE_MAX_BYTES)`: `READ_MISSING` -> the P2 `file not found` refusal, `READ_NOT_REGULAR` -> `not a regular file: %s`, `READ_TOO_LARGE` -> `%s is larger than %d bytes -- refusing to read it`, an `OSError` -> `io_fail("read", ...)`. The archive listing is `os.listdir` under `except OSError`: `FileNotFoundError` is an empty listing (M5), anything else `io_fail("list", ...)`. It refuses unexpected archive names (anything `ARCHIVE_NAME_RE.fullmatch` rejects, excluding temp names), and reads EVERY archive entry through `read_archive_entry(entry)`, i.e. `read_regular(entry, PAGE_MAX_BYTES)` (S3-1): `READ_NOT_REGULAR` (a symlink, a FIFO, a directory) -> `archive entry %s is not a regular file`, `READ_TOO_LARGE` -> `archive entry %s is larger than %d bytes -- refusing to read it`, `READ_MISSING` -> `io_fail` with a constructed `FileNotFoundError` (L1), an `OSError` -> `io_fail("read", entry, exc)` (an entry that vanished after the listing is a concurrent change the next run sees). No archive entry is ever followed or opened blocking;
  - `wiki_page_names(root)` (5.4): returns `(names, pages)`; every file through `read_regular(path, PAGE_MAX_BYTES)`, a failure of any kind is a `note:` and a skip, never a refusal (the scan reads files roadmap.py does not own); its `os.walk` passes an `onerror` that notes the unlistable directory and returns (M1);
  - `read_staged_file(flag, path, roadmap_path)`: the one reader of `--item-file`, `--why-file` and `--reason-file` (S-H1): refused when it resolves to the roadmap or into its archive (`os.path.realpath` under `except OSError`), then `read_regular(path, STAGED_MAX_BYTES)` mapped to `%s %s does not exist` / `%s %s is not a regular file` / the size refusal / `io_fail` (round-3 M2, 5.4), then strict UTF-8. It replaces the earlier `read_why_file`.
- **Section 4:**
  - `parse_kv_lines`;
  - `fm_value(key, raw, where)`: list keys must be `[...]` inline, split on `,` exactly like `_wikilib._parse_scalar`; scalars go through `_unquote` semantics, but a quoted value is refused on READ because the writer never produces one;
  - `render_kv(key, value)`: lists render as `[a, b]` or `[]`;
  - `check_line(field, value)` (S-M1, 5.4): the one character rule for single-line values (separators, C0 controls, lone surrogates `"\ud800" <= ch <= "\udfff"`, backtick); `_line_break(ch)` is its separator test, shared with `check_why`;
  - `check_scalar(field, value) -> str` (calls `check_line` first; returns the STRIPPED value, which every caller writes, M2 round 5), `check_reason(value) -> str` (`check_line("reason", value)`, strip, an empty result dies with `reason is empty (after stripping whitespace)`, returns the stripped text; the one entry for every reason on every route, M1 round 5), `check_list_item(field, value)` (calls `check_line` first; ids via `ID_RE`, tags via `TAG_RE`), `check_why(text) -> text` (separators, C0 controls, lone surrogates, headings, markers; strips leading and trailing blank lines, L3);
  - `parse_item_file(text, where, command) -> dict` (S-H1, S2-1, 5.4): `json.loads` with an `object_pairs_hook` that refuses duplicate keys; `ValueError` AND `RecursionError` (a deeply nested array such as 100000 `[`, which the stdlib decoder recurses into) are both caught and refused as `--item-file %s is not a JSON object (%s)`, the detail being the decoder message or `nested too deeply` (L7); the key, type and required-key checks against `ITEM_FILE_KEYS[command]` / `ITEM_FILE_REQUIRED[command]`; the `horizon` value through `LANE_INPUT` (L6); then every value through the same `check_*` as the flag route;
  - `reason_from_file(text, where)`: strips exactly one trailing `"\n"`, then returns `check_reason(...)` (so `check_line("reason", ...)` runs first and an empty file is the one empty-reason message, M1 round 5). There is no separate multi-line message: a remaining `"\n"` is refused by `check_line` with its line-separator message (L2).
  - `check_date(where, value)` (M3; see the constants block under Type Definitions): `DATE_RE.fullmatch`, then `datetime.date(int(y), int(m), int(d))` under `try` / `except ValueError`; refusals `%s %r is not a YYYY-MM-DD date` and `%s: %r is not a calendar date`. Every regex match in the module is a `.fullmatch` on a `re.ASCII` pattern (group A `regex-ascii`).
- **Section 5:**
  - `parse_roadmap(text, path)`: the strict grammar (5.2). Every refusal is `die("%s: line %d: %s -- refusing to guess (roadmap.md has one writer; was it edited by hand?)" % (path, n, detail))`.
  - `parse_item`, `parse_log_line` (`LOG_RE.fullmatch`, then `check_date("%s: line %d" % (path, n), date)`, M3).
  - `parse_archive(text, name)`: strict. The frontmatter keys are the canonical set; `name` equals the stem; `id` matches the filename; `title` has the `R-NNNN · ` prefix; `state` is in CLOSED_STATES; `commit` (`FULL_SHA_RE`: 40 or 64 hex, L4) appears iff done and `reason` iff dropped; `closed` and every log date pass `check_date` (M3); the body has `# R-NNNN · <title>`, `## Why` and `## Log`; the last log line's TO equals `state`. Anything incomplete dies with `archive/%s is incomplete (%s) -- a close interrupted mid-write? inspect it by hand`.
- **Section 6:**
  - `parse_id(raw) -> int`, `fmt_id(n)` -> `"R-%04d" % n`;
  - `next_id(state)`: `max(live ∪ archive filename ids) + 1`; beyond 9999 it dies with `id space exhausted (R-9999)`;
  - `resolve(state, n)`: live item, archived item, or `die("unknown id %s")`;
  - `cycle_members(graph)`: a straight P8 port. It returns the sorted ids whose in-degree is still positive after Kahn's algorithm, which is the cycle members AND every item downstream of them (an item blocked by a cycle member never reaches in-degree 0). **[IMPL-CHOICE]** (L3) The report says exactly that rather than trimming to strongly-connected nodes: the port stays line-for-line comparable to P8, and naming the downstream items is honest, since they are the items the cycle strands. `ready(item, state)`;
  - `validate(state)`:
    - invariants 1-2;
    - unknown ids in `blocked_by` / `follows`;
    - `follows` must name a closed item;
    - self-block and cycles over live + archive;
    - duplicate origins across live + archive.

    It does NOT check WIP (5.4).
  - `slugify(title)`: `unicodedata.normalize("NFKD")`, ASCII-drop, lowercase, `[^a-z0-9]+` -> `-`, strip `-`, cut at `SLUG_MAX` on a hyphen boundary. An empty result makes the caller refuse and ask for `--slug`.
  - `build_state(raw)`: parse roadmap.md + every archive file; refuse twin ids in the archive; compute `repairs` = ids present in both, where the archive wins and the live copy is dropped.
  - `load_state(path)`, `wiki_root_of(path)`, `archive_dir_of(path)`;
  - `check_target_shape(path)` (L5, pure): refuses a path whose basename is not `roadmap.md`, whose directory's basename is not `roadmap`, or whose wiki root (`os.path.dirname(os.path.dirname(path))`) equals its own `os.path.dirname`, i.e. the filesystem root.
- **Section 7:** `_md_cell` (P6 verbatim), `render_table(headers, rows)` (P6 layout, generic signature, all columns `ljust`), `render_log_line`, `render_item`, `render_summary`, `render_roadmap`, `render_archive`.
- **Section 8:** `commit(state, conflict=None)` (5.4). It emits the repaired lines via `note("repaired: ...")` when `state.repairs` is non-empty and the write happened.
- **Section 9.** Each command returns an exit code, and `main()` does `sys.exit(code)`. Each mutating command prints exactly one stdout line via `emit`.
  - `cmd_init(path)`, in this order, with no inline filesystem call (H2). The order matters (H1): `write_atomic` runs `mkstemp` in the target's directory, so `roadmap/` must exist before it, and an earlier draft that wrote roadmap.md first died with a `FileNotFoundError` traceback on any wiki root without `roadmap/`.
    1. `refuse_symlink(archive_dir_of(path), "archive directory")` — a check with no side effect;
    2. `refuse_existing(path)` — an existing roadmap.md (or anything else at that path, a dangling symlink included) is refused with `%s already exists -- init never overwrites`, again with no side effect;
    3. `ensure_archive_dir(archive_dir_of(path))` (section 3) creates `roadmap/archive/`, and with it `roadmap/` and any missing parent;
    4. the text is `render_roadmap` of an empty state with `wip_now = WIP_DEFAULT` and `WIP_NOTE_UNARGUED`, written via `write_atomic(path, text, ABSENT)`. This is the one route by which init writes a file, and init is the one mutating command that does not end in `commit` (there is no prior state to lock against). `_publish_link`'s `FileExistsError` route stays as the backstop for a concurrent init that lands between 2 and 4.

    **What a refused or interrupted init guarantees.** Every refusal init can decide from its inputs (the target shape in `main`, a symlinked archive directory, an existing target) is decided in steps 1-2, before anything is created, so a REFUSED init creates nothing (NFR-2). After step 3, only a crash, an IO error or the concurrent-init backstop can stop it, and each leaves at most empty directories on the path to `roadmap/archive/`. A re-run of init accepts that state: `refuse_existing` looks only at roadmap.md, and `ensure_archive_dir` is `exist_ok`.

    Emits `initialized: %s`.
  - `cmd_add(path, item_file, title, origin, horizon, state, spec, blocked_by, follows, severity, tags, why, why_file, reason)`:
    - with `item_file`, the values come from `parse_item_file(read_staged_file("--item-file", item_file, path), item_file, "add")`; the exclusivity with the value flags is checked first (5.4);
    - defaults are `horizon="unset"`, `state="idea"`, `reason="added"`;
    - `--title` and `--origin` (or the item file's `title` / `origin`) are checked in code, every single-line value through `check_line` via `check_scalar` / `check_list_item`, and the why through `check_why`; the item stores the RETURN values of `check_scalar` (title, origin, severity) and `check_reason` (the reason, flag or item file), never the raw arguments (M1, M2 round 5);
    - dedup by origin against live AND archive;
    - `--spec` must match `SLUG_RE` (after `check_line`) and be in the `pages` half of `wiki_page_names` (a file with a parsed frontmatter `name:`, not a bare stem; S2-1, L8);
    - `--horizon` (flag or item file) is validated through `LANE_INPUT` with the catalogued one-line message (L6);
    - WIP is checked when `horizon == "now"`;
    - the item is appended at the END of its lane with the first log line `- <today()> new-><lane>[ [idea-><state>]]: <reason>`;
    - emits the new id.
  - `cmd_list(path, state, horizon, untriaged, ready_only)`: `--horizon` is validated through `LANE_INPUT` (so `inbox` and `unset` both select the inbox; an unknown value is the catalogued one-line refusal, L6); `render_table` over the filtered live items (LIST_COLUMNS). `--untriaged` switches to UNTRIAGED_COLUMNS with the age in days. Zero rows emit `no live item matches`.
  - `cmd_show(path, raw_id)`: for a live item, emits its block (`render_item`) via `emit`; for a closed item, reads the archive file's bytes through the section-3 `read_archive_entry(archive path)` (round-3 L1, round-4 L1, S3-1; the archive path is joined from the wiki root and the archived item's name; the helper calls `read_regular(path, PAGE_MAX_BYTES)` and maps every reason to the same `archive entry %s ...` refusals / `io_fail` as `read_state_files`), decodes them strictly (`decode`) and writes the text via `emit_raw`, so stdout carries exactly the file's bytes with no added newline (L2). `cmd_show` itself makes no filesystem call and names no `os` attribute; `read_archive_entry` is the section-3 helper it calls, as `cmd_init` calls `ensure_archive_dir`.
  - `cmd_move(path, raw_id, lane, reason, reason_file, state)`: validates `lane` through `LANE_INPUT` in code (`inbox` is the spelling of `unset`; an unknown lane is `lane %r is not one of now, next, later, inbox`, one line, L6); requires exactly one of `--reason` / `--reason-file` (the file via `reason_from_file`); the reason passes `check_reason` (so `check_line` first, then the one empty-reason refusal) and its return value is what the log line carries (M1 round 5); refuses a true no-op; the WIP check names the occupants; appends the log line. Emits `R-0003: later->now [idea->active]`.
  - `cmd_rank(path, raw_id, before, top)`: same lane only. Emits `R-0004: position %d of %d in %s`.
  - `cmd_link(path, raw_id, item_file, spec, no_spec, origin, blocked_by, unblock, follows, no_follows)`:
    - with `item_file` (S2-1), `origin` / `spec` / `blocked_by` come from `parse_item_file(read_staged_file("--item-file", item_file, path), item_file, "link")`; its exclusivity with `--origin`, `--spec`, `--no-spec` and `--blocked-by` is checked first (5.4);
    - at least one option (or a non-empty item file) is required;
    - `origin` passes `check_scalar` and its return value is stored (M2 round 5); `spec` passes `check_line`, `SLUG_RE` and the `pages` half of `wiki_page_names` (L8);
    - ids are validated and the cycle check re-runs;
    - an `origin` change re-runs dedup.

    Emits `R-0004: linked (%s)` with the changed keys.
  - `cmd_render(path)`: re-renders and persists repairs. Emits `rendered: %s`, or `unchanged: %s` with nothing written (mtime untouched).
  - `cmd_wip(path, n, reason, reason_file)` (5.4). The reason passes `check_reason` (so `check_line` first, S-M1; then the one empty-reason refusal, M1 round 5), and its return value is what is written into the frontmatter comment line. Emits `wip_now: %d -> %d`.
- **Section 11 (cli):**
  - `build_parser()` has the P7 parent with `--file` (default `None`) and `--today`;
  - exclusivity (`--why`/`--why-file`, `--item-file` against the value flags of 5.4 on `add` and on `link`, `--reason`/`--reason-file`, `--before`/`--top`, `--spec`/`--no-spec`, `--follows`/`--no-follows`, `--commit`/`--reason`/`--reason-file` on close, `--closed-since`/`--open-only`) and required values are checked IN CODE, so the refusal stays one `roadmap:` line; argparse's own usage errors (an unknown flag) remain multi-line exit 2, as in checkpoint;
  - `--state` is validated in code (`OPEN_STATES`), and so are the `move` lane positional and every `--horizon` (`LANE_INPUT`, L6): the parser declares them as plain strings with NO `choices=`, because an argparse choices error is a multi-line usage text, not one `roadmap:` line;
  - `COMMANDS` maps each command name to its `cmd_*`;
  - `main()` resolves the target, runs `check_target_shape` on it (L5) and then `check_containment` (S4-1), validates `--today` through `check_date` (M3) and sets `_TODAY`, dispatches, and exits.

**Tests written first (roadmap suite)**:
- **B. round trip.**
  - `init` -> `add` x3 across lanes -> `move` -> `rank` -> `link` -> `wip`, all with `--today`: the file equals a hand-written expected file byte for byte. The expected file is the section 5.2 example shape, minus the archive.
  - `parse(render(model)) == model`, and `render_roadmap(parse_roadmap(t)) == t` for the canonical fixture.
  - A second `render` is a no-op down to the mtime, and every byte outside the summary region survives a `render`.
  - Init into a bare wiki root (H1, R30): `init --file <case>/docs/roadmap/roadmap.md` where `<case>/docs/` holds only an `overview.md` and no `roadmap/` exits 0 with no traceback and creates `roadmap/`, `roadmap/archive/` and roadmap.md, and leaves no `.roadmap-*` name in `roadmap/` (the init link route unlinks its temp, L2). Crash state: a case with an empty `roadmap/archive/` (and so `roadmap/`) but no roadmap.md, the leftover of an init interrupted after step 3, is accepted by a re-run of `init`.
- **C. wiki-parser compatibility (R4, R15).** The corpus covers titles, origins and reasons containing a colon, `#`, a pipe, inner quotes, inner brackets, non-ASCII and `·`, plus empty lists.
  - `_wikilib.parse_frontmatter` and the server `parse_frontmatter` each return exactly the expected dict for every archive file and for roadmap.md's frontmatter, including `blocked_by: []` -> `[]`. The archive files of this group are rendered in-process by `render_archive` (Step 5 code), not produced by `close` (M2).
  - `reindex.collect` and the server `reindex_collect` over the case's wiki root report zero malformed pages, zero dups and no `roadmap-item` orphan.
  - `freshness.analyze` reports `untracked`.
  - The server `verify_analyze(root)` reports `gating == []` for generated content.
  - Controls: a title wrapped in quotes, a title `[x]` and a two-line title are each REFUSED by `add`. A planted hand-written file with `title: "quoted"` is shown to be misparsed by the wiki oracle, proving the oracle fires.
  - **Stripped scalars round-trip (M2 round 5).** `add --title '  x  '` (leading AND trailing spaces) exits 0 (no `internal:` error) and stores the title `x`: roadmap.md's heading is `## R-NNNN · x` and re-reads as `x` through `parse_roadmap` and both wiki parsers; the archive file rendered in-process by `render_archive` from that item carries `title: R-NNNN · x` and `# R-NNNN · x` and `parse_archive` returns the same title. The same holds for an origin with surrounding spaces.
  - **Line-structure injection (S-M1, R26).** The test spells the separator list itself, as Python escapes in its source (never as raw characters, here or there): newline `"\n"`, carriage return `"\r"`, `"\x0b"`, `"\x0c"`, `"\x1c"`, `"\x1d"`, `"\x1e"`, `"\x85"`, and the LINE SEPARATOR and PARAGRAPH SEPARATOR code points U+2028 and U+2029 (spelled with the backslash-u escape in the test). It first asserts that each one satisfies `len(("a" + ch + "a").splitlines()) != 1` on the running Python (so the list cannot silently diverge from the rule). Then, ONE case per separator: the separator embedded mid-value in a title (`add`), in a `move` reason and in a `wip` reason is refused with the `check_line` message naming the field, exit 2, file byte-unchanged. Further cases:
    - the forged-key attempt: `wip 4 --reason-file <f>` where the file holds `ok\nstatus: deprecated` is refused, and after the refusal both wiki parsers still read `status: active` from the untouched file;
    - a forged-heading attempt: a title `x\n## R-0099 · forged` is refused;
    - C0: a title with `"\x00"`, `"\x1b"` and `"\t"` is refused; a title with U+00B7 is accepted;
    - backtick: a title, an origin and a `move` reason holding a backtick are each refused (M6); a why holding a backtick is accepted. (The `close --reason` backtick case needs `close` and lives in Step 6 group G, M2);
    - the same separator refusals reach `origin`, `spec`, `severity` and a tag, one case per field (the field list is the test's, so a field that bypasses `check_line` fails its case). The close-route fields (`close --reason`, `--slug`) are in Step 6 group G (M2), so every case in THIS group runs against Step 5 code alone and the Step 5 green claim holds;
    - lone surrogates (M4, S2-3): an item file whose `title` is the JSON text `"a\ud800b"` (a `\ud800` escape, which `json.loads` decodes to a lone surrogate) is refused with the `check_line` lone-surrogate message, exit 2, one stderr line, no traceback; the same for an item file whose `why` carries `\udfff` (the `check_why` message); an in-process `check_line("title", "a" + chr(0xDC80) + "b")` (the shape argv decoding produces from a non-UTF-8 byte) dies with the same message;
    - `check_why`: each separator except `"\n"` is refused, `"\r"` with the user refusal message (not the internal round-trip error, L3); a why with two leading and three trailing blank lines is written without them, and `show` returns it stripped;
    - control: a planted module copy whose `_line_break` returns False for U+2028 lets the U+2028 title through, and the oracle (both wiki parsers plus the strict re-read) detects the split line.
- **D. invariants, WIP, move, rank, log, wip (R11, R24).**
  - Refusals:
    - `unset` + `planned` on `add`, and `active` outside `now`;
    - `now` at the cap (the message names all occupants);
    - a move without `--reason`, and a true no-op move;
    - `rank` across lanes, and `--before` itself.
  - Log behaviour: a same-lane move with `--state` keeps the rank and writes the bracket. The log is appended, never rewritten: the previous lines stay byte-identical.
  - Rank ordering: `--top` / `--before`.
  - `wip`:
    - raise, and lower-to-occupancy, are accepted and the note line is rewritten;
    - a no-op, zero or a negative value, and a cap below occupancy are refused;
    - a missing `--reason` is refused.
  - A planted file whose `wip_now` sits below occupancy still allows a move OUT of `now`.
- **E. dependency graph + ready (R23).**
  - Self-block, an unknown id, a 2-cycle and a 3-cycle are refused, and the message names exactly the cycle members plus the items downstream of them (L3): with A and B blocking each other and C blocked by A, the refused link names A, B and C, sorted; a D that blocks A (upstream) is NOT named.
  - `list --ready` excludes an item blocked by a live item.
  - It INCLUDES an item blocked by a done item AND one blocked by a dropped item, using planted archive files.
  - `follows` naming a live item is refused.
- **F. optimistic lock (R1).** In-process: `load_state`, then mutate the roadmap.md bytes behind the module's back, then `commit` -> exit 2. The file holds the CONCURRENT bytes, and no temp file is left.
  - The same happens with a new archive file appearing (the archive digest).
  - Control A: with no mutation, the write succeeds.
  - Control B: monkeypatching `check_snapshot` to a no-op makes the oracle detect the lost update (the concurrent bytes are gone).
- **I. refusals.** Via `refusal()`, every Step 5 message in section 7, each asserting exit 2, the file unchanged, no temp, one stderr line and its distinguishing tokens. Includes a "git timeout" case (R18, L7): in-process, with a fake `git` script first on `PATH` (set in `os.environ` and restored in `finally`) whose body is `#!/bin/sh` + `exec sleep 5` (L5: `exec`, so the process `subprocess.run` kills on timeout IS the sleeper and no orphaned `sleep` outlives the case), written with `H.TempWorkspace.write_text` and made executable with `os.chmod(path, 0o755)`, and a monkeypatched `GIT_TIMEOUT_SEC = 1`, the case calls `mod.git(["rev-parse", "--show-toplevel"], <sandbox dir>)` DIRECTLY and asserts the return `(124, "", "git rev-parse timed out after %ds")`. It does not go through `resolve_target` (which falls back to the cwd on any failure and would hide the 124) or through `close`. Also:
  - **Staged input (S-H1, R25).** Each case runs with the child cwd set to a fresh sandbox directory:
    - an item file whose title is `Fix $(touch PWNED) and "quoted" and 'single' parts` and whose why holds `$(touch PWNED2)` and a backticked `touch PWNED3` round-trips through `add --item-file`: `show` returns the title and why byte-exact, and none of `PWNED`, `PWNED2`, `PWNED3` exists anywhere in the sandbox afterwards;
    - an item file whose title holds a backticked `touch PWNED4` is REFUSED by `check_line` (the backtick rule, S-M1), and `PWNED4` does not exist either. This is the reconciliation of S-H1 with S-M1: `$(...)` and quotes are legal title text and must round-trip; the backtick is refused in every single-line value;
    - a `--reason-file` holding `$(touch PWNED5)` round-trips into the log line of `move`, and `PWNED5` does not exist;
    - refusals: an item file that is not JSON, is a JSON array, has an unknown key, has a duplicate key, lacks `origin`, has a non-string `title`, has `tags` as a string; `--item-file` together with `--title`; a `--reason-file` holding `one\ntwo\n` (refused with the `check_line` line-separator message naming field `reason` and U+000A; there is no separate multi-line message, L2), while a file holding `one\n` is accepted as `one`; `move R-0001 next --reason-file <f>` where `<f>` holds only `"\n"`, and `move R-0001 next --reason '   '`, are each refused with `reason is empty (after stripping whitespace)` (M1 round 5: never the `internal:` round-trip error, and no log line is written); `--reason` together with `--reason-file`; a staged file over `STAGED_MAX_BYTES`; a staged file path that is the roadmap itself.
    - `link --item-file` (S2-1, R29): an item file `{"origin": "user:2026-09-28:a-$(touch PWNED6)-\"q\""}` round-trips through `link --item-file` (`show` returns the origin byte-exact) and `PWNED6` does not exist; one with `{"blocked_by": ["R-0003"]}` adds the blocker. Refusals: a link item file with `title` (not a link key, message names the link key set), an empty object `{}`, `blocked_by` as a string, `--item-file` together with `--origin`; `link --spec Foo_Bar` and `add --spec "a b"` are refused by `SLUG_RE` before the page lookup.
  - **Staged file shape (round-3 M2, R31).** `--why-file` naming a path that does not exist is refused with `--why-file %s does not exist`; `--item-file` naming a directory, and `--reason-file` naming a symlink to a regular sandbox file, are each refused with `%s %s is not a regular file`; `--item-file` naming a FIFO (`os.mkfifo`, INFO-skipped where unavailable) is refused with the same message and the child exits within a test-side `timeout=` on `subprocess.run` (a hang fails the case, never the suite). Each is exit 2, one line, no traceback.
  - **Archive entries (S3-1, R31).** Each in its own case root, all INFO-skipped where `os.symlink` / `os.mkfifo` is unavailable: an archive entry `0001-x.md` that is a symlink to a well-formed archive file elsewhere in the sandbox is refused with `archive entry %s is not a regular file` (the link target byte-unchanged; `list` is the command); an archive entry that is a FIFO is refused with the same message and the child exits within a test-side `timeout=` (no hang: `lstat` refuses it before any open); an archive entry one byte larger than a monkeypatched `PAGE_MAX_BYTES` (in-process through `call_module`, the constant restored in `finally`) is refused with `archive entry %s is larger than %d bytes -- refusing to read it`.
  - **Lane and horizon spelling (L6).** `move R-0001 soon` is refused with `lane 'soon' is not one of now, next, later, inbox`; `list --horizon soon` and `add --horizon soon` with `--horizon 'soon' is not one of now, next, later, inbox`; an item file with `"horizon": "soon"` with the same message; each is ONE stderr line (argparse would have printed a usage block). `move R-0001 inbox` and `list --horizon unset` are accepted.
  - **Nested JSON (L7).** An item file holding 100000 `[` characters is refused with `--item-file %s is not a JSON object (nested too deeply)`, exit 2, one line, no traceback.
  - **`--spec` names a page, not a stem (L8).** With `<case>/docs/sources/notes.md` holding no frontmatter and `<case>/docs/concepts/real-page.md` holding `name: real-page`, `link R-0001 --spec notes` is refused with `--spec 'notes' is not a wiki page under %s`, and `--spec real-page` is accepted. A symlinked `*.md` in the wiki root is skipped by the scan with one `note:` line and does not abort the command.
  - **The IO error rule at run time (R32, NFR-13).** A witness of the generic backstop, INFO-skipped when the suite runs as root (root ignores the permission bits), restoring the mode in `finally`: `init --file <case>/docs/roadmap/roadmap.md` where `<case>/docs` is chmod `0o500` exits 2 with one line starting `roadmap: cannot create directory` and no traceback. (Its `export --out` twin needs `export` and lives in Step 6 group H.)
  - **Refused init creates nothing (H1, R30).** `init` over an existing roadmap.md whose `archive/` is missing is refused with `already exists` and does NOT create `archive/` (the refusal is decided before `ensure_archive_dir`).
  - **Symlinks (S-L2, R28).** A roadmap.md that is a symlink to a sandbox file, and an archive directory that is a symlink to a sandbox directory, are each refused (exit 2, the link target byte-unchanged). `init` with a symlinked archive directory is refused and creates neither roadmap.md nor any directory (H1). INFO-skipped where `os.symlink` is unavailable.
  - **Target shape (L5).** `--file <case>/notes/roadmap.md`, `--file <case>/roadmap.md` and `--file <case>/docs/roadmap/other.md` are refused, and nothing is created; `--file <case>/docs/roadmap/roadmap.md` is accepted.
  - **Containment (S4-1, R33).** INFO-skipped where `os.symlink` is unavailable; the outside directory is a sibling of the case root inside the sandbox, and its `H.file_digests` are taken before and compared after:
    - `<case>/docs/roadmap` is a symlink to `<sandbox>/outside-roadmap/` (empty): `init --file <case>/docs/roadmap/roadmap.md` is refused with `roadmap directory %s is a symlink -- refusing`, and `outside-roadmap/` stays empty (no roadmap.md, no `archive/`);
    - the same symlink, with `outside-roadmap/` holding a well-formed roadmap.md with one live item: `move R-0001 next --reason x` is refused with the same message and `outside-roadmap/` is byte-unchanged (its `close` twin needs `close` and lives in Step 6 group G, M2);
    - inside a git sandbox (INFO-skipped without git): `<case>/docs` is a symlink to `<sandbox>/outside-docs/`, so the roadmap directory itself is not a symlink; `init` is refused with `%s resolves to %s, outside %s -- refusing to write through a symlinked directory`, and `outside-docs/` is unchanged;
    - the control is every other case of the suite: the mkdtemp sandbox lives under a symlinked ancestor on macOS (`/var`), and those cases pass, which proves a symlink ABOVE the tree is not refused.
  - **Dates and tokens (M3, R35).** Each exit 2, one line, nothing written:
    - `--today 2026-02-30` -> `--today: '2026-02-30' is not a calendar date` (the `--closed-since` twins need `export` and live in Step 6 group H, M2);
    - `--today` spelled with Arabic-Indic digits (U+0660-U+0669, written as escapes in the test source) -> `--today %r is not a YYYY-MM-DD date` (the ASCII shape refusal, not the calendar one: `int()` would have accepted those digits);
    - `--today` with a trailing newline (`"2026-09-28\n"`, passed as an argv element) -> the shape refusal (`fullmatch`, where `$` would have matched);
    - a planted roadmap.md whose log line carries `2026-02-30` -> `%s: line %d: '2026-02-30' is not a calendar date`; a planted archive whose `closed:` is `2026-02-30` -> `archive/%s closed: '2026-02-30' is not a calendar date`;
    - `show` of an id spelled with Arabic-Indic digits, and `show R-0001` with a trailing newline, are refused as `%s is not an id (want R-NNNN)`; `rank R-0002 --before` given an id in Arabic-Indic digits is refused with the same message; `add --spec` whose value ends in a newline is refused by `check_line`'s line-separator message, before `SLUG_RE` is consulted. The trailing-newline `--commit` case needs `close` and lives in Step 6 group G.
- **L. CLI surface (R21).**
  - `--today` drives every generated date, and a bad `--today` is refused.
  - `run_cli_default` from `<sandbox git repo>/sub/dir` resolves to `<sandbox git repo>/docs/roadmap/roadmap.md`: `list` refuses naming THAT path, and `init` creates it there, not under `sub/dir`. Outside any repo it falls back to the cwd. These cases are INFO-skipped without git. Every path comparison in these cases compares `os.path.realpath` of BOTH sides (the path named in the refusal, extracted from stderr, and the expected path; the file `init` created and the expected file), because on macOS the mkdtemp sandbox lives under `/var`, a symlink to `/private/var`, and `git rev-parse --show-toplevel` answers with the resolved spelling (L9).
  - `show` of an item with U+00B7 under `env_extra={"LC_ALL": "C", "PYTHONIOENCODING": "ascii"}` exits 0, and its stdout bytes decode as UTF-8 and contain `\u00b7`.
  - A refusal whose message names such a title behaves the same on stderr.
  - **A non-UTF-8 path on the refusal route (round-3 M3, NFR-4).** The case bypasses `run_cli` (whose argv is `str`) and calls `subprocess.run` itself with a BYTES argv: `[sys.executable.encode(), TARGET.encode(), b"--file", os.fsencode(<case dir>) + b"/x\xff/docs/roadmap/roadmap.md", b"--today", b"2026-09-28", b"list"]`, `cwd` = the case dir, `env=` the same merged git-isolation env, `input=b""`, under `LC_ALL=C` + `PYTHONIOENCODING=ascii`. The path passes `check_target_shape` and does not exist, so the refusal is `file not found` on a filesystem that stores arbitrary bytes, or the `io_fail` backstop `cannot read ...` where the filesystem rejects the name itself (APFS can answer `EILSEQ`). The case asserts exit 2, EXACTLY one stderr line, no `Traceback`, and that the line contains the text `\udcff` (the backslash-replaced surrogate) and one of `file not found` / `cannot read`. The `--file` path lies inside the sandbox (the guard is asserted on its `os.fsencode` form) and nothing is created.
  - **A reader that closes early (M2, R34).** The CLI witness: a staged roadmap large enough that `list` output exceeds any pipe buffer (items whose titles fill well over 1 MiB of table rows in total, derived in the test from a pipe-size constant it states, never typed as a count), run with `subprocess.Popen(stdout=PIPE, stderr=PIPE)` and a test-side `timeout=`; the test reads one line from stdout, closes its end, then collects stderr and the exit status. Asserted: exit 0, and stderr contains neither `Traceback` nor `Exception ignored`. (`export`, the command the finding names, needs Step 6; its twin lives in group H.) In-process, with fake streams installed by `captured()`'s pattern and restored in `finally`: a stdout whose `buffer.write` raises `OSError(errno.EIO, ...)` makes `emit` exit 2 with one stderr line starting `roadmap: cannot write to standard output`; a stderr whose `buffer.write` raises `BrokenPipeError` makes `note` and `die` both exit 2 with no recursion (the fake records exactly one write attempt each).

**Pattern to follow**: P1-P9, P11, P16.

**Verification**:
- `forge_call test roadmap`: groups A, B, C, D, E, F, I, K, L are green. None of their cases needs `close` or `export` (the close-route line-rule cases live in group G, M2); groups G, H and J do not exist yet.
- Group A's two caller-set cases (`create_exclusive` callers == {`cmd_close`}; `write_atomic` callers == {`commit`, `cmd_init`, `cmd_export`}) pass here as SUBSET checks, because the `roadmap` SUITES count is still the `0` placeholder (L5); the report names the still-absent declared callers. They become EXACT the moment the end of Step 6 replaces the placeholder with the green count, so the declared sets are never edited to make this step green (round-3 M1). The `io-handler` case likewise covers only the section-3 functions that exist, per call; its controls (including the planted `write_atomic` with `mkstemp` above its `try`) already fire here.
- `forge_call test table_cells` is EXPECTED RED on "undeclared aligning file(s): ClaudeCode/skills/roadmap/scripts/roadmap.py". This proves R16 is real. Execute Step 7 immediately after this step.

### Step 6: close + archive + crash recovery + export
**Files**:
- **ClaudeCode/skills/roadmap/scripts/roadmap.py** (modify: section 3 `verify_commit`, `archive_names_for_id`, `check_out_path`, `worktree_dirty`; `cmd_close`; section 10 `build_export` / `export_text` / `cmd_export`)
- **tests/test_roadmap.py** (modify: groups G, H, J are written first; the golden export is an inline string constant in the module **[IMPL-CHOICE]**, so there is no committed fixture file to drift from its generator)
- `tests/run.py` (modify: set the `roadmap` count from the first green run)

**Dependencies**: Step 5

**Description**:
- `verify_commit(sha, cwd)` (section 3), in this order:
  1. `SHA_RE` first, then `git rev-parse --show-toplevel`: rc 124/126/127 (timeout, cannot run, no git) -> `cannot verify commit %s: git failed (%s)` with git()'s stderr text, so a missing git is never misreported as "not a repository"; any other nonzero rc -> `cannot verify commit %s: %s is not inside a git repository`.
  2. `git cat-file -e <sha>^{commit}`: rc 124/126/127 -> `cannot verify commit %s: git failed (%s)`; any other nonzero rc -> `commit %s does not exist in %s (git cat-file -e)`.
  3. `git rev-parse --verify --quiet <sha>^{commit}` -> the full sha, which must match `FULL_SHA_RE` (40 or 64 hex, L4); anything else is `cannot verify commit %s: git failed (unexpected rev-parse output)`.

  Every one of these calls goes through `git()`, so each carries `GIT_SAFE_ARGV` (NFR-5).
- `archive_names_for_id(archive_dir, n)` (section 3): the sorted names in `archive/` that start with `"%04d-" % n` and end in `.md`. `archive_names_for_id` lists under `except OSError`: `FileNotFoundError` is "no names", anything else `io_fail("list", archive_dir, exc)`. `check_out_path(out, roadmap_path)` (section 3), in this order: `refuse_symlink(out, "--out")`; refuse a directory; refuse the roadmap itself or anything inside its archive (by `realpath`, under `except OSError`); then refuse a parent that does not exist or is not a directory, `--out %s: directory %s does not exist` (round-3 H1: `mkstemp` in a missing parent was a traceback; the pre-check gives the friendly line, and `write_atomic`'s `io_fail` stays the backstop for a parent that vanishes after it). Both exist so that `cmd_close` / `cmd_export` do no filesystem call themselves (H2).
- `worktree_dirty(roadmap_dir)` (section 3, S2-2): the filter-free dirty computation of 5.3, steps 1-5. Its `os.walk` passes `onerror=_reraise` (a nested callback that raises the `OSError` it is given), so an unlistable subdirectory lands in the body's `except OSError` -> `True` and is never silently skipped (L3); the trailing empty element of the `b"\0"` split is dropped (I2). It returns `None` (unknown) when the directory is not in a work tree, HEAD is unborn, the object format is not `sha1` / `sha256` (or git cannot report it), any git call fails, or an `ls-tree` record is malformed; `True` for any per-entry `OSError` (stat, readlink, read, walk) and for every non-regular, oversized or mismatched entry; otherwise `False`. It never raises and never dies (its contract is to report, NFR-13): the whole body sits under `except OSError` -> `True`, with the git and record failures returning `None` before that. It spawns only `rev-parse` and `ls-tree`, through `git()` (`ls-tree` with `binary=True`), reads regular files only through `read_regular(path, PAGE_MAX_BYTES)`, reads a tracked symlink's target only through `os.readlink` on the `os.fsencode`-d path, and never opens a non-regular entry (S3-1); it never runs `git status`, `diff` or `add`. The record split is on `b"\0"`, then on the first `b"\t"`; paths from `ls-tree -z` are not quoted, so no unquoting is needed, and they are `os.fsdecode`-d before being compared with the `os.walk` names (round-3 M4, 5.3).
- `cmd_close(path, raw_id, commit, reason, reason_file, slug)`:
  1. `load_state` (snapshot S). The item must be live. When it is already archived, refuse with `%s is closed (%s) -- closed items are immutable; a regression is a new item with --follows %s`.
  2. Exactly one of `--commit` / `--reason` / `--reason-file`, then verify the commit. The reason (either route) goes through `check_reason` (`check_line` first, then the one empty-reason refusal, M1 round 5) and then through `check_scalar("reason", ...)` (H1 round 5): it becomes the archive frontmatter scalar `reason:` AND the text after `": "` in the closing log line, so it must survive both the wiki frontmatter parser and `LOG_RE`. The stored reason is `check_scalar`'s RETURN value (M2 round 5).
  3. name = `"%04d-%s" % (n, slug or slugify(title))` (a `--slug` passes `check_line`, then `SLUG_RE`). Pre-check that `archive_names_for_id(archive_dir, n)` is empty. Check the name against `wiki_page_names(wiki root)` (name or stem), refusing on a hit with `archive name %s collides with %s -- pass --slug`.
  4. Build the archive item: the lane at close time goes into `horizon`, `closed = Closed(today(), full_sha, None)` or `Closed(today(), None, reason)`, and the closing log line is `- <today()> <lane>->done: commit <full sha>` or `<lane>->dropped: <reason>`. Render it with `render_archive`.
  5. **Archive self-check (H1 round 5), before anything is created:** `parse_archive(render_archive(item, name), name)` must compare equal to the archive item built in step 4; a mismatch dies with the SAME catalogued refusal as `commit`'s step 3, `internal: the rendered file does not parse back to the model (%s) -- nothing was written`, with the detail `archive/<name>`. As in `commit`'s re-parse, a rendered text that `parse_archive` refuses outright surfaces through `parse_archive`'s own one-line message. Either way the self-check runs before `create_exclusive`, so no archive file exists after it fails; an archive file is immutable once created, so this is the last point where a render/parse divergence is still harmless. Then `check_snapshot(path, S)`, then `create_exclusive(archive path, text)`.
  6. Remove the item from its lane, then `commit(state', conflict=...)`. `state'.snapshot` is S with the archive digest recomputed for the one new name, and `conflict` is `%s was archived to %s but roadmap.md or the archive listing changed underneath -- the next run removes the live copy (archive wins)` (L6: the re-check compares BOTH digests, so the message names both).
  7. Emit the archive path, relative to the parent of the wiki root. It is derived by joining onto the display directory `read_state_files` already computed for roadmap.md: `os.path.join(os.path.dirname(item.path), ARCHIVE_DIRNAME, name)`, where `item.path` is the closing item's display path. Never `os.path.relpath`, which is forbidden outside section 3 (L9, 5.1).
- `cmd_export(path, out, closed_since, open_only)`:
  - `load_state` WITHOUT the lock; in-memory repair + `note`;
  - `source`: `head` from `git rev-parse --short HEAD` in the roadmap dir, `dirty` from `worktree_dirty(roadmap_dir)` (S2-2), with nulls outside a work tree;
  - build the dict per 5.3 and serialize it with `export_text`, whose result already ends in `"\n"`;
  - `--closed-since` goes through `check_date("--closed-since", value)` (M3);
  - `build_export` refuses a lone surrogate in any `path` field before either route writes (L4, 5.3);
  - `--out` goes through `check_out_path`: refused when it is a symlink (S-L2), resolves to roadmap.md, lies inside `archive/`, is a directory, or its parent is missing or not a directory (round-3 H1);
  - it is written through `write_atomic(out, text, UNLOCKED)` (emitting `exported: %s`), or to stdout via `emit_raw(text)`, never `emit`, which would add a second newline (L5). Both routes therefore carry the same bytes.

**Tests written first**:
- **G. close / archive (R2, R3, R12, R13, R20).**
  - Git sandbox: `git -c init.defaultBranch=main init`, then `git -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit --allow-empty -m x`, with `stdin=DEVNULL`, `env=H.child_env(...)` and the git env of Step 4. These cases are INFO-skipped when git is absent.
  - Close done with the real short sha:
    - the archive equals the golden rendered from the fixture, with `commit` holding the FULL sha;
    - the live block is gone and the closing log line is present;
    - the spec page named by `spec:` is byte-unchanged.
  - Close dropped with `--reason`, and again with `--reason-file`.
  - `show` of a closed item: its stdout bytes equal the archive file's bytes exactly, with no extra trailing newline (L2). In-process, a spy wrapped around `mod.read_regular` (restored in `finally`) records that the archive path was read through it (round-3 L1, R31).
  - SHA-256 repository (L4): in a sandbox repo created with `git init --object-format=sha256`, `close --commit <short sha>` stores the 64-hex full sha, and the archive re-reads cleanly. INFO-skipped when that `git init` fails (git without SHA-256 support).
  - Commit refusals (R19): a missing commit, a non-hex value, a 65-hex value, and `-x` as the sha (it never reaches git).
  - Named `close` refusal cases (L3), each asserting exit 2, one stderr line with its catalogued message, roadmap.md byte-unchanged and no archive file created:
    - `close-not-a-repo`: `close R-0001 --commit abc1234` on a staged roadmap OUTSIDE any repository (the case dir under `GIT_CEILING_DIRECTORIES`) -> `cannot verify commit abc1234: %s is not inside a git repository`;
    - `close-git-127`: the same with `PATH` set to an empty sandbox directory (so no `git` is found; the child is started by absolute `sys.executable`) -> `cannot verify commit abc1234: git failed (git executable not found)`;
    - `close-git-124`: in-process through `call_module`, with a fake `git` first on `PATH` whose body is `#!/bin/sh` + `exec sleep 5`, chmod `0o755` (L5), and `GIT_TIMEOUT_SEC` monkeypatched to 1 (both restored in `finally`) -> `cannot verify commit abc1234: git failed (git rev-parse timed out after 1s)`;
    - `close-unexpected-full-sha`: a fake `git` (chmod `0o755`) that answers `rev-parse --show-toplevel` with the case dir, exits 0 for `cat-file`, and prints `nothex` for `rev-parse --verify` -> `cannot verify commit abc1234: git failed (unexpected rev-parse output)`; the script dispatches on its argv AFTER the `GIT_SAFE_ARGV` `-c` pairs, which it also asserts are present;
    - `close-already-archived`: in-process, `archive_names_for_id` observes a planted `archive/0001-other.md` that appears after `load_state` (the case wraps `load_state` to plant it after the read) -> `R-0001 is already archived at %s`;
    - `close-flags`: `close R-0001` with none of the three flags, and with both `--commit` and `--reason` -> `close needs exactly one of --commit SHA (done), --reason TEXT or --reason-file PATH (dropped)`.
  - Close-route line rule (moved from group C, M2; R26): `close --reason` holding a backtick is refused with the `check_line` backtick message; each separator of the group C list embedded in `close --reason` and in `--slug` is refused with the `check_line` line-separator message naming the field, exit 2, roadmap.md byte-unchanged and no archive file created. The separator list is the one group C already asserted against `str.splitlines`.
  - **Close reason as a frontmatter scalar (H1, M1, M2 round 5).** Each refusal asserts exit 2, one stderr line, no traceback, roadmap.md byte-unchanged and NO file (and no `.roadmap-*` name) in `archive/`:
    - empty reason: `close R-0001 --reason-file <f>` where `<f>` holds only `"\n"` -> `reason is empty (after stripping whitespace)` (never the `internal:` message);
    - quote-wrapped reason: `--reason '"x"'` -> the `check_scalar` message with `wrapped in matching quotes`, field `reason`;
    - bracket-wrapped reason: `--reason '[x]'` -> the `check_scalar` message with `wrapped in brackets`, field `reason`;
    - leading-space reason: `--reason ' x'` on the CLI is STRIPPED, not refused (M1/M2: every caller writes the stripped value), so this case asserts exit 0, `reason: x` in the archive frontmatter, `dropped: x` in the closing log line, and a clean re-read. Its REFUSAL half is the self-check witness: in-process through `call_module`, with `check_reason` and `check_scalar` monkeypatched to return their argument unstripped (restored in `finally`), the same `close R-0001 --reason ' x'` exits 2 with one line (the `internal:` round-trip refusal, or `parse_archive`'s own refusal of the log line, since `LOG_RE` requires `\S` after `": "`) and no archive file is created, which proves the step-5 self-check runs before `create_exclusive`.
  - Close with no `archive/` (M5, R30): a git-sandbox case whose roadmap.md has live items but NO `archive/` directory (the fresh-clone state: git drops an empty directory) closes an item with exit 0 and no traceback; `archive/` now exists and holds exactly the one new file (no `.roadmap-*` name beside it: the link route unlinked its temp, L2), and the roadmap re-reads cleanly.
  - **The `create_exclusive` backstop at run time (I3 round 4, R32).** INFO-skipped as root, mode restored in `finally`: a staged roadmap with one live item and an EXISTING `archive/` chmod `0o500`; `close R-0001 --reason x` (no git needed) exits 2 with one line starting `roadmap: cannot create a temporary file in` naming the archive directory, no traceback, roadmap.md byte-unchanged, and `archive/` still empty. `ensure_archive_dir`'s `makedirs(exist_ok=True)` succeeds on the existing directory, so the refusal comes from `create_exclusive`'s own guarded `mkstemp`, which is the site this case witnesses.
  - **Containment on close (S4-1, R33).** The `close` twin of group I's containment case: `<case>/docs/roadmap` is a symlink to an outside directory holding a well-formed roadmap.md with one live item; `close R-0001 --reason x` is refused with `roadmap directory %s is a symlink -- refusing`, the outside directory is byte-unchanged and no `archive/` is created there. INFO-skipped where `os.symlink` is unavailable.
  - **Token shape on close (M3, R35).** `close R-0001 --commit` with the value `"abc1234\n"` (a trailing newline) and with a sha spelled in Arabic-Indic digits are each refused with the sha shape message, and a fake `git` first on `PATH` that records its argv proves git was never spawned.
  - Every case in this group that closes an item successfully also asserts that no `.roadmap-*` name is left in `archive/` or `roadmap/` (L2).
  - Slug refusals:
    - collisions with a planted docs/adr/0014-<slug>.md AND with a planted docs/sources/0014-<slug>.md (the scan is wider than SKIP_DIRS);
    - `--slug` then resolves the collision;
    - a bad `--slug`, and an empty slug (a title of punctuation only).
  - A second close is refused, with the archive bytes unchanged. An archive file is byte-unchanged by every later command.
  - Planted archive defects: twin archive files are refused naming both; a truncated archive is refused; a stray `README.md` in the archive is refused.
  - Planted id-in-both:
    - `render` repairs it and prints `repaired:`;
    - `list` / `show` / `export` print `note:` and write nothing (sha unchanged);
    - the export contains the id once, archived.
  - Ids: the next id after closing the highest id with an empty roadmap is max+1, and gaps are preserved (a gap is never reused).
  - `create_exclusive` crash simulation: `os.link` is monkeypatched to raise `OSError(EIO)` after the temp file exists, forcing the fallback. Monkeypatching both paths to fail leaves no `NNNN-*.md` and no temp file.
  - Close lock: a concurrent roadmap.md change before `create_exclusive` refuses with nothing created. A change between the create and the replace, either to roadmap.md or to the archive listing (a second planted archive name), yields the "archived ... roadmap.md or the archive listing changed underneath" message (L6), and the next `render` repairs it.
- **H. export (R10).**
  - The golden constant (the 5.3 document) is reproduced byte for byte from the staged fixture (outside git, `source` nulls), twice, on BOTH routes: the stdout bytes of `export` and the bytes of the file written by `export --out <f>` each equal the golden exactly, so stdout carries no extra trailing newline (L5). Keys are sorted at every level.
  - `--open-only`: counts `done: 0, dropped: 0` and `scope.closed: false`.
  - `--closed-since`: filters closed items only and counts only what it emits; a date in the future yields zero closed items. `--closed-since 2026-02-30` is refused with `--closed-since: '2026-02-30' is not a calendar date`, and `--closed-since` with Arabic-Indic digits or a trailing newline with the shape message (M3, R35).
  - **Non-UTF-8 wiki root (L4, R36).** INFO-skipped where the filesystem refuses the name (APFS may answer `EILSEQ`): the fixture is staged under a wiki root whose directory name holds the byte `\xff` (created through `os.fsencode` paths), and the CLI is called with a bytes argv as in group L's non-UTF-8 case. `export` and `export --out <case>/out.json` each exit 2 with the SAME one line (`export path ... carries a lone surrogate ...`), the stdout of the first is empty, and `<case>/out.json` does not exist.
  - **A reader that closes early, on export (M2, R34).** The group L witness repeated with `export` on stdout over a fixture whose export exceeds any pipe buffer: exit 0, no `Traceback`, no `Exception ignored` on stderr.
  - `ready` is derived through the archive, with done and dropped blockers both true.
  - In a sandbox git repo with the fixture committed, `source.head` equals `git rev-parse --short HEAD` and `dirty` is `false`. This clean case is also what proves that the installed git parses `ls-tree ... HEAD -- <dir>` with `--` as a separator: had it taken `--` as a path, nothing would be listed and every file would read as untracked. `dirty` flips to `true` when roadmap.md is modified, when an untracked file appears under the roadmap directory, and when a tracked archive file is deleted; it stays `false` for a change OUTSIDE the roadmap directory (scope). In a SHA-256 sandbox repo (INFO-skipped without support) the clean case is `false` too, which proves the blob hash follows the object format. With an unborn HEAD (`git init`, no commit) `dirty` is `null`.
  - **`--show-object-format` gate (L10).** Before the first case that asserts a non-null `dirty`, the suite runs its own `git rev-parse --show-object-format` in the sandbox repo; when that fails (a git that predates the option), every case that asserts `dirty: true` / `false` is recorded INFO with the reason, and ONE case instead asserts that `dirty` is `null` for that git (the defined result, never a traceback).
  - **Symlinks and unreadable entries under the roadmap directory (S3-1, R31; round-3 M4).** INFO-skipped where `os.symlink` / `os.mkfifo` is unavailable; each run under a test-side `timeout=` so a blocking open fails the case rather than hanging the suite:
    - a tracked archive file is replaced in the working tree by a symlink to a FIFO in the sandbox: `dirty` is `true`, and the export returns within the timeout, which proves the entry was never opened (opening the FIFO would block);
    - a symlink committed AS a symlink (mode `120000`) under the roadmap directory, unchanged: `dirty` stays `false`, which proves the `os.readlink` bytes hash to git's symlink blob; retargeting that symlink makes `dirty` `true`;
    - a tracked file made unreadable (chmod `0o000`, INFO-skipped as root, mode restored in `finally`): `dirty` is `true`, exit 0, no traceback (a per-entry `OSError` is "possibly different");
    - an untracked subdirectory under the roadmap directory holding one file, made unlistable (chmod `0o000`, INFO-skipped as root, mode restored in `finally`): `dirty` is `true` through the re-raising `onerror` (L3). This case discriminates: with a silent `onerror`, the walk would skip the directory, never see the untracked file inside it, and report `false`;
    - a roadmap-directory name holding a `\r` (INFO-skipped where the filesystem refuses it), committed: `dirty` stays `false`, which proves the bytes-mode `ls-tree` path is not newline-translated and compares equal to the `os.walk` name.
  - **`--out` parent (round-3 H1).** `export --out <case>/missing/out.json` is refused with `--out %s: directory %s does not exist` (exit 2, one line, nothing created), and so is `export --out <case>/file.txt/out.json` where `file.txt` is a regular file. The generic backstop (R32, NFR-13): `export --out <case>/ro/out.json` where `<case>/ro` is chmod `0o500` (INFO-skipped as root, mode restored in `finally`) exits 2 with one line starting `roadmap: cannot create a temporary file in`, no traceback, and nothing left in `<case>/ro`.
  - Export writes nothing (the directory digests are unchanged). `--out` writes only that path, atomically, and its refusals are covered, including a symlinked `--out` (S-L2, R28): refused, the link target byte-unchanged.
  - Planted repository config (S-L1, S2-2, R27): in a sandbox git repo with the fixture committed, `.git/config` gets `core.fsmonitor = <sandbox>/fsmon.sh` AND a clean-filter driver `[filter "pwn"] clean = <sandbox>/clean.sh`, and `.gitattributes` gets `*.md filter=pwn`. `fsmon.sh` creates `<sandbox>/FSMON_RAN`; `clean.sh` creates `<sandbox>/CLEAN_RAN` and copies stdin to stdout. Both start with `#!/bin/sh` and are made executable with `os.chmod(path, 0o755)` right after they are written (L5): a planted script git cannot execute would make the negative result prove nothing, and the controls below are what catch that. roadmap.md is then modified (so any index refresh would have to re-hash it through the filter). `export` exits 0, reports `dirty: true`, and NEITHER `FSMON_RAN` nor `CLEAN_RAN` exists afterwards. (`core.hooksPath` gets no runtime case: none of roadmap.py's read-only git calls runs a hook, so a planted hook could not fire either way; the AST check on `GIT_SAFE_ARGV` covers it.) Controls, one per marker, run AFTER the export's assertions: the test's own `git status` in the same repo, WITHOUT any `-c` override, creates `FSMON_RAN`, and its own `git diff -- <roadmap.md>` (which must convert the modified working file through the clean filter to produce a patch) creates `CLEAN_RAN`, proving each planted setting is live. A control that does not fire (a git that ignores that form) records its half of the case as INFO rather than PASS, because it would then prove nothing.
- **J. negative controls.**
  - A planted writer that emits `title: "<quoted>"` is detected by the C oracle.
  - A planted `render_item` that drops `severity` is detected by the B round-trip oracle.
  - A planted close that writes roadmap.md BEFORE the archive (monkeypatched order) loses the item under an injected crash, and the G oracle detects it.
  - A planted export using the wall clock in `source` is detected by the twice-identical oracle.

**Pattern to follow**: P4, P9, P16.

**Verification**:
- `forge_call test roadmap` is fully green apart from the expected placeholder DRIFT (M3). Replace the placeholder `0` in its SUITES row with this first green run's case count, then re-run: green with no drift.
- Group A's caller-set cases now run as EXACT equalities (`create_exclusive` callers == {`cmd_close`}; `write_atomic` callers == {`commit`, `cmd_init`, `cmd_export`}), because the `roadmap` SUITES count is no longer the `0` placeholder (L5): the re-run after replacing the placeholder is the first EXACT run, and the report must no longer name any absent caller. Likewise every name in `IO_FUNCTIONS` is defined and covered, per call, by `io-handler`. If either case still reports SUBSET mode after the count is set, Step 6 is not done (round-3 M1).

### Step 7: Declare roadmap.py's table renderer in the `table_cells` roster
**Files**:
- `tests/test_table_cells.py:192` (modify: `DECLARED_ESCAPED` +1)
- `tests/test_table_cells.py:261-278` (modify: add a `"roadmap"` Row after `checkpoint`)
- `tests/test_table_cells.py:802-818` (modify: the census comment, and `SWEEP_EXPECTED` gains the roadmap script's repo-relative path, ClaudeCode/skills/roadmap/scripts/roadmap.py; written without backticks here because the file does not exist yet, M5)
- `tests/test_table_cells.py:12` (modify: the census sentence "six renderers exist" -> seven)
- `tests/run.py:358-369` (modify: the count comment's arithmetic and the declared count, both taken from the green run)
- `project-forge.yaml:190` (modify: the `table_cells` description's "Six renderers exist" sentence -> seven, naming roadmap.py beside checkpoint.py)

**Dependencies**: Step 5 (execute right after it; Step 6 does not depend on this step)

**Description**: the Row.
```python
    "roadmap": Row(
        path="ClaudeCode/skills/roadmap/scripts/roadmap.py", cls=ESCAPED,
        renderer="render_table", escaper="_md_cell", delim="|",
        reversible=True,
        desc_const=None, desc_tokens=(),
        why="the roadmap summary region and `list`: a padded GFM table whose "
            "Title cell is free user text. Same vocabulary as checkpoint's "
            "_toc_cell; out of group C like checkpoint, because a skill script "
            "has no tools/list -- the scheme is documented in the skill body"),
```
`render_table(headers, rows)` has the generic signature, so no adapter is needed in `group_rendered` (`tests/test_table_cells.py:616-619`).

**Pattern to follow**: P20.

**Verification**: `forge_call test table_cells` is green with the new declared count, taken from the run and never from prose.

### Step 8: The roadmap SKILL.md
**Files**: **ClaudeCode/skills/roadmap/SKILL.md** (create)

**Dependencies**: Step 6

**Description**: English throughout. The frontmatter is `name: roadmap` and a `description` with the triggers `/p:roadmap`, `/p:roadmap adopt`, "add to the roadmap", "what is next", "close R-0014". `model` is omitted. Sections:
- **Model.** What a roadmap item is and is not.
  - Kept vs deferred: `docs/adr/0010-a-handler-failure-must-reach-iserror.md:132` "permanent, not a TODO" and `docs/adr/0015-ambiguity-is-the-defect.md:192` "declared, not deferred" are KEPT decisions and never become items.
  - Invariants 1-4.
  - Priority = horizon + block order.
  - The WIP cap (`wip_now`, which counts every `now` item whatever its state), its unargued starting value, and the `wip` command.
  - `ready` = every blocker closed, done or dropped.
- **Field table.** The key lines, lists always inline and `[]` when empty, optional scalars omitted, and origin conventions.
- **The helper script.** A command block with `python3 ~/.claude/skills/p/skills/roadmap/scripts/roadmap.py <cmd>` for every command (P21 shape), including `add --item-file` and the `--reason-file` forms, the default target (resolved against the git top-level), `--file` (which must be shaped `<wiki root>/roadmap/roadmap.md`), and `--today`.
- **Field value rules.** Single-line values may not contain a line break, a control character or a backtick; the why may contain backticks but SKILL.md steers anchors into `origin:` (below).
- **Single-writer rule.** roadmap.md and archive/ have ONE writer: never Write/Edit/`purity_call` them. This is the analogue of checkpoint's Rule 7 (`ClaudeCode/skills/checkpoint/SKILL.md:112`).
- **Staging rule, stated directly next to the single-writer rule (S-H1, S2-1, MANDATORY).** The inline values the Bash command line that invokes roadmap.py may carry are EXACTLY these, and nothing else:
  - the command name and the flags;
  - ids (`R-0014`), lane names (`now`, `next`, `later`, `inbox`, `unset`) and state names (`idea`, `planned`, `active`);
  - a sha (hex) and a date (`YYYY-MM-DD`);
  - the integer for `wip`;
  - staged-file paths, which include the adopt export's `--out` path, ONLY single-quoted and ONLY when they match `.claude/tmp/roadmap-(stage|adopt)-[a-z0-9-]+\.(json|txt)` (S4-2, CWE-78). The name part is chosen by the skill (a timestamp plus a counter, e.g. `roadmap-stage-20260928t1412-3.json`), NEVER derived from harvested text: a title or origin must not become part of a path the shell sees. roadmap.py does not check this pattern (a manual caller may stage anywhere); it is the skill's contract, and like the rest of the caller half it is prose-only;
  - `--spec` and `--slug` values, ONLY single-quoted and ONLY when they match `[a-z0-9-]+` (roadmap.py refuses anything else for these two flags, so the quoting is belt and braces).

  The skill never passes `--file`: it relies on the default target, resolved against the git top-level. `--file` is documented for manual use and is what the tests use.

  Every other value is staged with `purity_call` `create_text_file` under `.claude/tmp/` and passed by path:
  - every adopt-derived value (title, origin, why, tags, severity) goes into ONE JSON item file per candidate and reaches roadmap.py only as `add --item-file <path>`; never as `--title "..."`;
  - every title, origin, tag, severity, why and reason, whatever its source and even when it is one word: a why via `--why-file`, a reason via `--reason-file` (on `move`, `close`, `wip`) or via the item file's `reason` key (on `add`), an origin, spec or blocker change on an existing item via `link --item-file <path>` (keys `origin`, `spec`, `blocked_by`); `link --origin "..."` is never typed;
  - the reason, spelled out in the skill: a `$(...)`, a backtick or a `"` inside repository prose would otherwise be executed by the shell as the user, in whatever repository the plugin is running in;
  - staging file names start with `roadmap-stage-` (the adopt export: `roadmap-adopt-`), never with `.roadmap-` (the writer's temp prefix), mirroring checkpoint's staging-name rule (`ClaudeCode/skills/checkpoint/SKILL.md:165`), and match the path pattern above in full: a `.json` item file, a `.txt` why or reason file;
  - the item-file format (keys, types, which keys are required, per command: `add` and `link`) is given as a literal example for each.
- **Refusals.**
  - A refusal is exit 2 with one line, and the file is unchanged. Fix the input and call again, and never route around a refusal by hand-editing (the analogue of `ClaudeCode/skills/checkpoint/SKILL.md:170`).
  - The lock-conflict message means "re-run".
  - `repaired:` / `note:` mean a close was interrupted; running `render` persists the repair.
- **The escape scheme.** Table cells escape `\\`, `\|`, `\n`, `\r`, `\t` (ADR 0016 vocabulary).
- **Anchors.** Put anchors in `origin:`, and avoid backticked `path:symbol` in why prose. Archived text is immutable, and `wiki_call` `verify` would flag a vanished anchor forever.
- **adopt op (H1).** `p:minion-explorer` (Scott) is read-only and has no Bash (`ClaudeCode/agents/minion-explorer.md:5`, `:20`), so he never runs roadmap.py. The op is four hops:
  1. **Main context: export.** Run `roadmap.py export --out '.claude/tmp/roadmap-adopt-<ts>.json'` (single-quoted), with the default scope (live AND archived items, so dropped items are in it). `<ts>` is a timestamp the main context picks, lowercase `[a-z0-9-]` only (S4-2), so parallel sessions do not share the file.
  2. **Scott: harvest and dedup.** Delegate with a verbatim prompt that passes the export path and says:
     - harvest sources:
       - ADR declared-limit sections;
       - draft spec pages with planned `targets:`;
       - "Open Questions" / "Next Steps" sections (e.g. `docs/concepts/spec-ddg.md:711`);
       - pending `requirements.yaml` leftovers;
     - classify each ADR limit as kept or deferred, with the quoted sentence that decides it;
     - read the export file with Read and drop every candidate whose origin equals the `origin` of ANY item in it, live or closed; dropped items do not resurrect;
     - return a candidate table: title, origin anchor, proposed state `idea` with horizon unset, a why draft, and the kept/deferred verdict with its evidence.
  3. **User: approve.** The skill shows the table; the USER approves rows.
  4. **Main context: stage and add.** For each approved row, `create_text_file` one JSON item file at `.claude/tmp/roadmap-stage-<ts>-<n>.json` (`<n>` a counter, never a slug of the candidate's title) and run `roadmap.py add --item-file '<path>'` (the staging rule above). roadmap.py's own origin dedup is the authority: a candidate Scott missed is refused there, not written twice.

  The machine proposes, a human decides, the script writes.
- **Regressions (L7).** A regression is a new item with `--follows R-NNNN`, and it takes its OWN origin (the anchor where the regression was observed, or `user:<YYYY-MM-DD>:<kebab-key>`). Reusing the predecessor's origin is refused, because origin dedup covers the archive too.
- **Export contract.** The JSON shape (5.3), the determinism guarantees, and the schema versioning rule. `why` is raw markdown, and HTML escaping is the CONSUMER's job. `source.dirty` is ADVISORY: it compares the roadmap directory's raw working-tree bytes with HEAD's stored blobs without running `git status` (so no repository-configured filter or hook runs), which means it can over-report `true` (autocrlf, a clean filter, an ignored or temp file under the directory) but never under-report; `null` means unknown (S2-2). The kanban mapping: columns = horizon (inbox | later | next | now | done | dropped), with state as a card badge.
- **Server restart note.** If INDEX.md lists archive pages, the running mcp-wiki server predates the roadmap types: restart it.
- Name only verified MCP functions: `wiki_call` `search` / `reindex` / `verify` / `freshness`, and `purity_call` `create_text_file`.

**Pattern to follow**: P21, P24.

**Verification**: `forge_call test name_existence` is green, and `forge_call test all` is green.

### Step 9: Documentation — wiki schema, ADR 0022, handoff contracts, rosters
**Files**:
- `ClaudeCode/skills/wiki/SKILL.md`:
  - §1 layout, `:338-352` (modify): add `roadmap/roadmap.md` and `roadmap/archive/NNNN-<slug>.md`;
  - `:340` (amend "one line per page" to "one line per page, except closed roadmap items, which are counted in one line");
  - §2, `:374-385` (add two rows):
    - `roadmap`: live known-but-unscheduled work, one writer (p:roadmap), source binding none, generated;
    - `roadmap-item`: one closed item, none, immutable;
  - §3, `:411-424` (add a note): roadmap pages carry extra keys owned by roadmap.py (`wip_now`; `id`, `state`, `horizon`, `origin`, `spec`, `blocked_by`, `follows`, `severity`, `tags`, `closed`, `commit` / `reason`) and never `sources` / `targets` / `verified` / `links`;
  - `:471-474` (add the roadmap types to the optional-sources sentence), and `:479-481` (point `UNTRACKED_TYPES` at `_wikilib.py`);
  - §5, `:654-675`: one sentence saying roadmap pages use only flat scalars and inline lists, so no new nesting;
  - §8, `:737-740`: "A vague someday-maybe -> a roadmap item (`/p:roadmap add`), not a page."
- `docs/components/wiki-engine.md:154-160` (modify: add the roadmap types to the exemption sentence and one sentence on INDEX counting `roadmap-item` pages; that sentence carries the inbound wiki link `[[0022-a-someday-maybe-is-a-roadmap-item]]`, M3).
- **docs/adr/0022-a-someday-maybe-is-a-roadmap-item.md** (create; P22 shape). It records:
  - why not in the wiki MCP, why not a root file, and the new types + the §8 amendment;
  - kept vs deferred, and the priority model: horizon, block order, the flat `wip_now` counting every `now` item, the value 3 as UNARGUED (the ADR 0013 lesson), and the `wip` command;
  - `ready` = every blocker closed, and why a dropped blocker is void;
  - the archive model (exclusive create via link, archive wins);
  - the optimistic lock and its declared one-syscall window;
  - id reuse after a hand-deleted archive file (declared);
  - backticked anchors in archived why prose (declared);
  - the wiki-wide name scan being wider than SKIP_DIRS;
  - the export contract, and every rejected alternative from section 4.

  It also records the S-H1 staging rule with its exact inline allowlist and `link --item-file` (S2-1), and its declared limit (the caller half is SKILL.md prose that no suite can gate), the `check_line` rule (why a separator in a single-line value forges a line, and why a lone surrogate is refused), the IO error rule (why the traceback class is closed structurally in section 3 and why `main()` has no catch-all), `read_regular` with `PAGE_MAX_BYTES` declared UNARGUED like `wip_now`, the `GIT_SAFE_ARGV` prefix, and why the export never runs `git status` (a refresh runs repository-configured clean filters and fsmonitor; `source.dirty` is hashed in Python and may only over-report, S2-2).

  It states the git TRUST BOUNDARY explicitly (S4-3, 5.4): the local `.git/config` is trusted, because clone does not transfer it and write access to it already equals code execution as the user; `GIT_SAFE_ARGV` (`core.fsmonitor=false`, `core.hooksPath=/dev/null`, `protocol.allow=never`) and `GIT_NO_LAZY_FETCH=1` are cheap hardening, declared and NOT a gated guarantee against a hostile local config; the gated claim is only that roadmap.py never runs an index-refreshing command. It also records the containment rule (S4-1) with its two declared limits (an ancestor symlink outside any work tree is accepted; a symlink swapped in after the check is the lock window's race class), the per-call form of the IO error rule and why a per-function form was rejected (H1), the defined exit codes of the stream route (M2), and the calendar-date and `re.ASCII` / `fullmatch` rule (M3).

  `sources:` = the roadmap script and SKILL.md, `Scripts/mcp-wiki.py:render_index`, `ClaudeCode/skills/wiki/scripts/reindex.py:render_index`, `ClaudeCode/skills/wiki/scripts/_wikilib.py`, and the two new suite modules. `links:` include `roadmap` (so the roadmap page is not an orphan once it exists), `0002-index-claims-no-freshness`, `0013-the-ceiling-is-a-payload-class`, `0016-a-cell-may-not-forge-a-boundary`, `0018-the-totals-must-describe-the-scope`, `0019-only-gate-on-what-you-can-prove`, `skills`, `tests`, `wiki-engine`.

  **Status (M7).** The ADR is written with `status: draft` and NO `verified:` block, because the code it cites exists only in the uncommitted working tree when it is written; a `verified.commit` would be a false claim, and `ClaudeCode/skills/wiki/SKILL.md:483-486` names `status: draft` as the correct expression of exactly this state (a sourced page without `verified:` under `status: active` is gated as `unverified`). After the implementation commit exists, the ADR is promoted in a follow-up edit: `status: active`, a `verified:` block with `commit:` = that commit and `date:`, and the body's `**Status:**` line updated to accepted. P22's `status: active` shape applies from that promotion on.
- `ClaudeCode/skills/_lib/handoff-contracts.md`:
  - a new `### /p:roadmap` entry after `/p:branch-review` (`:144-159`), in the Inputs / Outputs / Side effects shape (`:26-51`);
  - file-table rows (`:163-173`) for docs/roadmap/roadmap.md (producer `/p:roadmap` via roadmap.py; consumers `p:wiki` search/INDEX and `/p:roadmap export`) and docs/roadmap/archive/NNNN-slug.md (producer `roadmap.py close`, immutable; consumer `p:wiki`).
- `docs/subsystems/skills.md:49-63`: add a `p:roadmap` row whose Purpose cell links the decision as `[[0022-a-someday-maybe-is-a-roadmap-item]]` (M3). With the wiki-engine link above, the new ADR has two inbound links, so it is not an orphan and a reader of either roster page can reach it.
- `docs/subsystems/tests.md:205-227`: add `wiki_index` and `roadmap` rows, with no counts. `:313-317` is unchanged, since both new suites use system temp.
- `tests/README.md`: the suite table (`:61-83`, group ranges), the standalone commands (`:98-120`), and the Layout block (`:209` onward).

**Dependencies**: Step 8

**Verification**:
- ADR 0022 carries `status: draft` and no `verified:` until the implementation commit exists; after that commit it is promoted (M7), and the promotion is its own edit.
- The skill-side `python3 -B ClaudeCode/skills/wiki/scripts/reindex.py --root docs --check` (current as soon as Step 3 landed, no restart needed) shows no malformed pages and no new orphans; in particular ADR 0022 is not an orphan (M3). The `wiki_call reindex` check on the RESTARTED server belongs to Step 10 (L1).
- `wiki_call verify` shows no new gating anchors from these pages.
- `forge_call test name_existence` is green.

### Step 10: Restart the mcp-wiki server, full-fleet run, reindex check
**Files**: `docs/INDEX.md` (regenerated by `wiki_call reindex`, never by hand)

**Dependencies**: Steps 3, 9

**Description**:
1. Restart the running `mcp-wiki` server (`/mcp` reconnect or a new session).
2. **Prove the restart took (M4).** A probe must give a different answer on the old server than on the new one. `wiki_call list` with a `roadmap-item` type filter does not: the filter is a plain string comparison (`Scripts/mcp-wiki.py:2916`, `:2930`), so an old server also answers with an empty list. The orphan rule does discriminate, so the probe is:
   1. stage a fixture wiki with `purity_call` `create_text_file`: .claude/tmp/roadmap-restart-probe/overview.md (type `overview`) and .claude/tmp/roadmap-restart-probe/roadmap/archive/0001-probe.md (type `roadmap-item`, well-formed per 5.2, linked from nowhere);
   2. `wiki_call reindex` with `check: true` and `root: .claude/tmp/roadmap-restart-probe` (the `root` param resolves under the project root, `Scripts/mcp-wiki.py:2368-2374` via `safe_path` `:335-341`; `SKIP_DIRS` holds `.claude` but prunes only directories BELOW the root, `:196`, `:481-484`, so a root inside .claude/tmp/ is walked; `check` writes nothing, `Scripts/mcp-wiki.py:3076-3086`). The new server reports `0 orphan`; an old server reports `1 orphan` naming `0001-probe`, because its predicate exempts only `overview`;
   3. the report of a check run does not include the rendered INDEX (`Scripts/mcp-wiki.py:2307-2329`), so the count line is not claimed from it; the byte-level INDEX behaviour is already proven by `wiki_index` group B against the server's own `render_index`. The probe's only job is to show that the RUNNING process has the new code, and the orphan count is sufficient for that.
   Chosen over dropping the claim because the restart is the one step no suite can observe: `wiki_index` loads the server module from disk, not the running process. The fixture lives under .claude/tmp/ and is left for the user to discard.
3. Run `forge_call test all`.
4. Run `wiki_call reindex` with `check: true` on the real wiki, on the RESTARTED server: it shows no malformed pages and no new orphans (this check moved here from Step 9, because only after item 1 does the running server have the new code, L1). Then run it without `check` to regenerate `docs/INDEX.md`. The new ADR and this page appear there.

`init` and `adopt` are NOT run here. They are the post-implementation follow-up that requires the user's approval (section 1, Out of Scope).

**Verification**:
- `forge_call test all` is green with no drift.
- The regenerated `docs/INDEX.md` contains no `roadmap-item` line and no archive-count line, because no archive pages exist.
- `wiki_call freshness` reports no new gating pages.
- The restart probe (item 2) reports 0 orphans on the fixture root. A report of 1 orphan (`0001-probe`) means the running server still predates Step 3: restart again before any live `close`.

## 7. Error Handling and Edge Cases

Every refusal is `roadmap: <message>`, exit 2, and one line. Placeholders are %-format. The table lists the text after the `roadmap: ` prefix.

**File / format** (`load_state`, all commands):

| Situation | Exact message |
|---|---|
| missing roadmap | `file not found: %s -- run roadmap.py init first` |
| directory, FIFO or device in its place (`READ_NOT_REGULAR`) | `not a regular file: %s` |
| roadmap too large (`READ_TOO_LARGE`, S3-1) | `%s is larger than %d bytes -- refusing to read it` (the cap is `PAGE_MAX_BYTES`) |
| archive entry not regular (S3-1) | `archive entry %s is not a regular file` (a symlink, a FIFO, a directory: refused before any open) |
| archive entry too large (S3-1) | `archive entry %s is larger than %d bytes -- refusing to read it` |
| IO error backstop (NFR-13, R32) | `cannot %s %s: %s` (the operation, the path, `exc.strerror` or `os.strerror(errno)`), e.g. `cannot read %s: Permission denied`, `cannot list %s: ...`, `cannot create directory %s: ...`, `cannot create a temporary file in %s: ...`, `cannot write %s: No space left on device`, `cannot create %s: ...`, `cannot encode the text for %s: ...`, `cannot resolve the working directory for %s: ...`, `cannot resolve %s: ...` (`check_containment`). Emitted only by `io_fail`, only from section 3, and only for what a pre-check could not see; the specific refusals in these tables win whenever they apply. A vanished archive entry reaches it as a constructed `FileNotFoundError` (`cannot read %s: No such file or directory`, L1) |
| stdout write failure (M2) | `cannot write to standard output: %s` (an `OSError` other than EPIPE; EPIPE on stdout exits 0 silently, and any stderr failure exits 2 with no message, because stderr is the broken channel) |
| symlinked roadmap directory (S4-1) | `roadmap directory %s is a symlink -- refusing` |
| roadmap directory outside its tree (S4-1) | `%s resolves to %s, outside %s -- refusing to write through a symlinked directory` (the roadmap directory, its realpath, the realpath of the git top-level or, outside a work tree, of the wiki root) |
| date not on the calendar (M3; a log line, an archive `closed:`) | `%s: %r is not a calendar date` (`%s` is `<path>: line N` for a log line, `archive/<name> closed` for the close date) |
| target shape (L5) | `--file %s is not <wiki root>/roadmap/roadmap.md -- the wiki root is the parent of the roadmap/ directory` / `--file %s would make the filesystem root the wiki root` |
| symlinked roadmap (S-L2) | `%s is a symlink -- roadmap.py reads and replaces only a regular file it owns` |
| symlinked archive dir (S-L2) | `archive directory %s is a symlink -- refusing` |
| not UTF-8 (roadmap, archive, any staged file) | `%s is not valid UTF-8 (byte 0x%02x at offset %d) -- refusing to read it` |
| grammar violation | `%s: line %d: <detail> -- refusing to guess (roadmap.md has one writer; was it edited by hand?)` |
| archive stray file | `unexpected file in archive/: %s (want NNNN-slug.md)` |
| archive id mismatch | `archive/%s: frontmatter id %s does not match its filename` |
| archive name mismatch | `archive/%s: frontmatter name %r does not match its filename` |
| archive incomplete | `archive/%s is incomplete (%s) -- a close interrupted mid-write? inspect it by hand` |
| twin archive | `R-%04d is archived twice (%s, %s) -- keep one by hand` |

The grammar `<detail>` is one of:
- frontmatter: `no frontmatter`, `carriage return in the file`, `frontmatter type is %r, want roadmap`, `unknown frontmatter key %r`, `frontmatter key %r is missing`, `status %r is not one of draft, active, deprecated`, `wip_now must be a positive integer, got %r`, `unexpected comment line %r (the only comment roadmap.py writes is the wip_now note)`;
- preamble and lanes: `unterminated summary region`, `unexpected text before the first lane heading: %r`, `unknown lane heading %r`, `lane heading %r out of order (want # now, # next, # later, # inbox)`, `lane heading %r is missing`;
- item headings and keys: `malformed item heading %r`, `unknown key %r`, `duplicate key %r`, `key %r out of order (want state, horizon, origin, spec, blocked_by, follows, severity, tags)`, `R-%04d has no %s line`, `%s must be an inline list [...], got %r`, `quoted value %r (roadmap.py never writes one)`;
- item consistency: `R-%04d sits in lane %s but says horizon: %s`, `R-%04d appears twice`, `state %r is not one of idea, planned, active`;
- log: `missing ### Log`, `malformed log line %r`, `a closing log line (%s) in roadmap.md`.

**Ids, invariants, commands:**

| Situation | Exact message |
|---|---|
| bad id | `%s is not an id (want R-NNNN)` |
| unknown id | `unknown id %s` |
| closed id mutated | `%s is closed (%s) -- closed items are immutable; a regression is a new item with --follows %s` |
| invariant 1 | `an untriaged item must be an idea (invariant 1) -- %s would be %s in the inbox; give it a lane or --state idea` |
| invariant 2 | `an active item must be in now (invariant 2) -- %s would be active in %s` |
| bad --state | `--state %r is not one of idea, planned, active -- done and dropped are reached only through close (invariant 3)` |
| WIP | `now is full (%d of %d: %s) -- move one out, close one, or change the cap with roadmap.py wip` |
| no reason | `a lane or state change needs --reason or --reason-file` |
| reason flags | `--reason and --reason-file are exclusive` |
| no-op move | `%s is already in %s as %s -- nothing to move` |
| rank lanes | `%s (%s) and %s (%s) are in different lanes -- rank orders within a lane` |
| rank self | `an item cannot be ranked before itself` |
| rank flags | `rank needs exactly one of --before ID or --top` |
| self block | `an item cannot block itself` |
| cycle | `blocked_by would create a cycle: %s` (the sorted ids of the cycle members and the items downstream of them, L3) |
| unknown ref | `unknown id in --blocked-by: %s` / `unknown id in --follows: %s` |
| unblock absent | `%s is not blocked by %s` |
| follows live | `--follows must name a closed item; %s is live` |
| dedup | `origin %r is already recorded by %s (%s)` (path, plus `, dropped` for a dropped archive item) |
| line separator (`check_line`, S-M1) | `%s value %r carries a line separator (U+%04X) -- it would split the line it is written on` |
| control character (`check_line`) | `%s value %r carries a control character (U+%04X)` |
| lone surrogate (`check_line`, M4) | `%s value %r carries a lone surrogate (U+%04X) -- it cannot be written as UTF-8` |
| backtick (`check_line`, M6) | `%s value %r carries a backtick -- generated roadmap text is backtick-free (verify would read it as an anchor)` |
| scalar | `%s value %r would not survive the wiki frontmatter parser (%s)`, where the reason is one of `empty`, `wrapped in brackets`, `wrapped in matching quotes` (line breaks and tabs are `check_line`'s, above); applies to title, origin, severity and the `close` reason (H1 round 5) |
| empty reason (M1 round 5) | `reason is empty (after stripping whitespace)` (`check_reason`: every reason, `add` / `move` / `wip` / `close`, on the `--reason`, `--reason-file` and item-file routes) |
| tag | `tag %r is not kebab-case` |
| spec shape (S2-1) | `--spec %r is not a wiki page name (want kebab-case [a-z0-9-])` |
| spec | `--spec %r is not a wiki page under %s` |
| why heading | `the why text carries a heading on line %d (%r) -- a level 1-3 heading would split the item` |
| why marker | `the why text carries a summary marker on line %d` |
| why separator (L3) | `the why text carries a line separator other than a newline (U+%04X) on line %d -- use plain newlines` |
| why control | `the why text carries a control character (U+%04X) on line %d` |
| why surrogate (M4) | `the why text carries a lone surrogate (U+%04X) on line %d -- it cannot be written as UTF-8` |
| why flags | `--why and --why-file are exclusive` |
| staged file is the roadmap | `%s %s is the roadmap or its archive -- refusing` (the flag, then the path) |
| staged file size | `%s %s is larger than %d bytes -- a staged file is small by contract` |
| staged file missing (round-3 M2) | `%s %s does not exist` (the flag, then the path) |
| staged file not regular (round-3 M2, S3-1) | `%s %s is not a regular file` (a directory, a FIFO, a symlink: the stager writes plain files) |
| lane spelling (L6) | `lane %r is not one of now, next, later, inbox` (`move`) |
| horizon spelling (L6) | `--horizon %r is not one of now, next, later, inbox` (`add`, `list`, the item file's `horizon`) |
| item file JSON (S-H1) | `--item-file %s is not a JSON object (%s)` (the detail is the decoder's message, or `nested too deeply` for a `RecursionError`, L7) |
| item file key | `--item-file %s: unknown key %r for %s (want %s)` (the command, then `ITEM_FILE_KEYS[command]` joined with `, `) / `--item-file %s: duplicate key %r` / `--item-file %s: %s is missing` / `--item-file %s: link needs at least one of origin, spec, blocked_by` |
| item file type | `--item-file %s: %s must be a string` / `--item-file %s: %s must be a list of strings` (`tags`, `blocked_by`) |
| item file flags | `--item-file excludes --title, --origin, --why, --why-file, --tags, --severity, --horizon and --reason` (add) / `--item-file excludes --origin, --spec, --no-spec and --blocked-by` (link, S2-1) |
| add required | `add needs --title and --origin` |
| link nothing | `link needs at least one of --item-file, --spec, --no-spec, --origin, --blocked-by, --unblock, --follows, --no-follows` |
| link flags | `--spec and --no-spec are exclusive` / `--follows and --no-follows are exclusive` |
| id space | `id space exhausted (R-9999)` |
| init exists | `%s already exists -- init never overwrites` |
| wip value | `wip needs a positive integer, got %r` |
| wip no-op | `wip_now is already %d -- nothing to change` |
| wip below | `now holds %d items (%s); a cap of %d would already be exceeded -- move items out first` |
| wip reason | `a WIP change needs --reason or --reason-file` |
| --today | `--today %r is not a YYYY-MM-DD date` (shape: ASCII digits, `fullmatch`) / `--today: %r is not a calendar date` (M3) |

**close:**

| Situation | Exact message |
|---|---|
| flags | `close needs exactly one of --commit SHA (done), --reason TEXT or --reason-file PATH (dropped)` |
| sha shape | `%r is not a commit sha (want 7-64 lowercase hex digits)` |
| unexpected full sha (L4) | `cannot verify commit %s: git failed (unexpected rev-parse output)` |
| not a repo | `cannot verify commit %s: %s is not inside a git repository` |
| no such commit | `commit %s does not exist in %s (git cat-file -e)` |
| git failure (124/126/127, on `rev-parse --show-toplevel` or `cat-file`) | `cannot verify commit %s: git failed (%s)` |
| empty slug | `the title of %s yields no slug -- pass --slug` |
| bad slug | `--slug %r is not kebab-case` |
| collision | `archive name %s collides with %s -- pass --slug` |
| already archived | `%s is already archived at %s` |

**Writes and export:**

| Situation | Exact message |
|---|---|
| lock | `%s changed since it was read (another session?) -- nothing written; re-run the command` |
| lock after archive (L6) | `%s was archived to %s but roadmap.md or the archive listing changed underneath -- the next run removes the live copy (archive wins)` |
| internal round trip | `internal: the rendered file does not parse back to the model (%s) -- nothing was written` (`commit` step 3, and `cmd_close`'s archive self-check before `create_exclusive` with the detail `archive/<name>`, H1 round 5) |
| export date | `--closed-since %r is not a YYYY-MM-DD date` / `--closed-since: %r is not a calendar date` (M3) |
| export path not UTF-8 (L4) | `export path %r carries a lone surrogate (U+%04X; a directory name that is not UTF-8) -- rename it; the export is UTF-8 JSON` (both routes, before anything is written) |
| export flags | `--closed-since and --open-only are exclusive` |
| export out | `--out %s is the roadmap or its archive -- refusing` / `--out %s is a directory` / `--out %s is a symlink -- refusing` (S-L2) / `--out %s: directory %s does not exist` (the parent is missing or not a directory, round-3 H1) |

Non-refusal stderr lines do not change the exit status:
- `roadmap: repaired: %s was in both roadmap.md and %s (a close interrupted between its two writes); removed the live copy`
- `roadmap: note: %s is in both roadmap.md and %s; the archive wins -- run roadmap.py render to persist the repair`
- `roadmap: note: wiki name scan skipped %s (%s)` (a symlinked, special, oversized or unreadable `*.md`, or an unreadable directory; `wiki_page_names` never refuses on a file roadmap.py does not own, S3-1)

Edge cases that are gated:
- Empty lanes render as a bare heading.
- A title containing ` · `, `|`, `:` or `#` round-trips.
- An item with no why prose is handled (roadmap: no prose; archive: `_(none)_` read back as `""`).
- A log reason containing `: ` round-trips (LOG_RE splits at the first `: ` after the transition).
- `--closed-since` in the future yields zero closed items with matching counts.
- `move` to `inbox` is accepted as `unset`.
- `list --untriaged` on an empty inbox emits `no live item matches`.
- A title whose slug exceeds `SLUG_MAX` is cut on a hyphen boundary.
- A why with leading or trailing blank lines is stored without them (L3).
- A title or reason containing `$(...)`, `"` or `'` round-trips through a staged file and is never evaluated (S-H1).
- `show` of a closed item reproduces the archive bytes exactly (L2).
- `export` on stdout and `export --out` carry the same bytes (L5).
- `init` into a wiki root with no `roadmap/` directory creates it (H1); `close` in a roadmap with no `archive/` directory creates it (M5).
- A FIFO or a symlink anywhere roadmap.py reads is refused (roadmap.md, an archive entry, a staged file), skipped with a note (the name scan) or counted dirty (the export's `worktree_dirty`), and is never opened blocking (S3-1).
- A path given as non-UTF-8 bytes is named in a refusal with its surrogate backslash-escaped, on one line (round-3 M3).
- `export | head` (a reader that closes early) exits 0 with nothing on stderr (M2).
- `2026-02-30`, Arabic-Indic digits and a trailing newline are refused wherever a date, id or sha is accepted (M3).
- A symlinked roadmap directory, or a symlinked ancestor inside the work tree, is refused before anything is written (S4-1).
- A non-UTF-8 wiki root name refuses the export identically on stdout and `--out` (L4).

## 8. Testing Strategy

Counts live ONLY in the `tests/run.py` SUITES rows, taken from the first green run.

- **wiki_index** (Steps 2-3):
  - A: constant parity over the six names, plus no local copies and the roadmap types present;
  - B: render_index in both copies (byte-identical output, the filter, the count line singular/plural/absent, the archive-only corpus, the CLI write);
  - C: reindex_collect in both copies (equal tuples, the exemption, overview, the roadmap-page orphan documented);
  - D: freshness `untracked` in both copies, plus the sourced-item control;
  - E: negative controls (counted types emptied, constant drift, missing exemption);
  - F: hygiene.
- **roadmap** (Steps 4-6):
  - A: format contract, type membership, and AST checks (stdlib only, no `_wikilib`, no SKIP_DIRS copy, stdin+timeout, `GIT_SAFE_ARGV`, the `git(` subcommand allowlist `rev-parse` / `cat-file` / `ls-tree`, no shell, `sys.stdout` only in `emit` / `emit_raw` and `sys.stderr` only in `die` / `note`, layer purity over the exact FORBIDDEN / `PURE_OS_NAMES` / `write_atomic`-caller sets + controls, the caller sets SUBSET while the `roadmap` SUITES count is the `0` placeholder and EXACT from then on (round-3 M1, L5); `io-handler`, PER CALL: every IO call in section 3 (including `_write_stream`'s `.write` / `.flush`) sits in the body of a `try` whose handlers spell `OSError`, `NONRAISING_IO_CALLS` with written reasons (incl. `os.walk`), the `walk-onerror` sub-check, a reasoned `IO_HANDLER_EXEMPT` (empty), + controls incl. the planted `write_atomic` with `mkstemp` above its `try` (R32, H1, M1); `single-reader`: no builtin `open`, `os.read` / `os.open` / `os.readlink` confined (R31); `regex-ascii`: `re.ASCII` on every pattern, `fullmatch` only, + control (R35));
  - B: round trip, idempotence and byte preservation; init into a bare wiki root and over the empty-directory crash state (H1);
  - C: wiki-parser compatibility (both parser copies, collect, freshness, verify) plus refusal controls, and the line-structure corpus (one case per separator, the forged-key and forged-heading attempts, C0, lone surrogates, backtick, why separators and blank-line stripping, plus the `_line_break` control); only values that Step 5 commands carry (M2);
  - D: invariants, WIP, move, rank, log and the `wip` command;
  - E: dependency graph and ready (a dropped blocker counts as closed); the cycle report names the cycle members and the items downstream of them (L3);
  - F: the optimistic lock plus two controls;
  - G: close, archive, crash recovery, id monotonicity, slug collision across the wiki-wide scan, verbatim `show` of a closed item, the SHA-256 repository (INFO-skip without support), commit refusals, the close-route line rule (`close --reason`, `--slug`; M2), close with no `archive/` (M5), close with a read-only `archive/` (the `create_exclusive` `mkstemp` backstop, INFO-skipped as root), containment on close (S4-1), trailing-newline and Arabic-Indic `--commit` values (M3), no `.roadmap-*` leftover after any close (L2), and the named close refusals `close-not-a-repo`, `close-git-127`, `close-git-124`, `close-unexpected-full-sha`, `close-already-archived`, `close-flags` (L3);
  - H: export golden on both routes (stdout and `--out`, L5), scope, counts and source (the filter-free `dirty`: clean, modified, untracked, deleted, out-of-scope, SHA-256, unborn HEAD), the symlinked `--out`, the `--out` missing / non-directory parent (round-3 H1) and read-only parent (R32), the planted fsmonitor + clean-filter case with its two controls (S2-2), the tracked-file-swapped-for-a-symlink-to-a-FIFO / tracked-symlink / unreadable-file / unlistable-subdirectory / `\r`-name `dirty` cases (S3-1, round-3 M4, L3), `--closed-since` calendar and shape refusals (M3), the non-UTF-8 wiki root refused identically on both routes (L4), the early-closing reader on `export` (M2), and INFO-skips when git lacks `--show-object-format` (L10);
  - I: the refusal table, the git-timeout case calling `git()` directly (L7), the staged-input injection cases (`add --item-file`, `link --item-file`, `--reason-file`), the `--spec` shape, symlinked roadmap and archive, a refused init creating nothing (H1), the `--file` shape, the staged-file missing / not-regular / FIFO refusals (round-3 M2), symlinked / FIFO / oversized archive entries (S3-1), lane and horizon spelling (L6), nested JSON (L7), `--spec` against pages not stems (L8), the read-only `init` witness of the IO error rule (R32), containment (a symlinked `docs/roadmap` refused on `init` and `move`, a symlinked `docs` inside a git sandbox refused; S4-1), and the date / id / token shape cases (`2026-02-30`, Arabic-Indic digits, trailing newline; planted log line and archive `closed:`; M3);
  - J: negative controls;
  - K: hygiene (the live roadmap and archive untouched; no `.roadmap-*` name anywhere in the sandbox after the init and close cases, L2); runs even when roadmap.py is missing (M1);
  - L: CLI surface (`--today`, default target against the git top-level compared by `realpath` on both sides (L9), UTF-8 output under an ASCII locale, a non-UTF-8 `--file` on the refusal route via a bytes argv (round-3 M3), a reader that closes early exits 0 with no traceback, and the in-process EIO-on-stdout / broken-stderr stream branches (M2)).
- Existing suites touched:
  - `table_cells`: the roster row (Step 7);
  - `wiki_recall`: must stay green after TYPE_ORDER grows;
  - `name_existence`: the new SKILL.md;
  - `checkpoint`: untouched, since its patterns are copied, not imported;
  - `spawn_stdin`: skill scripts are INFO-surveyed, and roadmap.py passes `stdin=` anyway.
- Red-first protocol: Steps 2, 4, 5 and 6 record the red run in the step's verification before the implementing edit, and the commit message states which cases were red. A red run's case count is never committed. A missing module attribute is a failing case through `need()`, never a crash of the suite (M2).
- Interim SUITES values (M3): `wiki_index` is registered at Step 2 and `roadmap` at Step 4, each with the explicit placeholder count `0`. The resulting `CASE COUNT DRIFT` is expected until the first green run sets the real count (Step 3 for `wiki_index`, the end of Step 6 for `roadmap`). Until then `forge_call test all` is red by design.

## 9. Documentation Updates

| Contract | Home | Not duplicated in |
|---|---|---|
| Commands, flags, file format, field table, invariants, refusal handling, escape scheme, export contract + HTML-escaping duty, kanban mapping, adopt prompt | ClaudeCode/skills/roadmap/SKILL.md (Step 8) | the module docstring (it points here) |
| Why: placement, types, §8 amendment, kept vs deferred, priority model, `wip_now` as unargued, ready semantics, archive model, lock limit, alternatives | docs/adr/0022-a-someday-maybe-is-a-roadmap-item.md (Step 9) | SKILL.md (links the ADR) |
| Who writes and reads which file | `ClaudeCode/skills/_lib/handoff-contracts.md` | — |
| Page types, layout, the frontmatter keys roadmap pages carry, anti-scope | `ClaudeCode/skills/wiki/SKILL.md` §1/§2/§3/§5/§8 | — |
| Where UNTRACKED_TYPES lives | `docs/components/wiki-engine.md:158` (Step 1) | — |
| Suite roster | `docs/subsystems/tests.md`, `tests/README.md` (no counts) | — |
| Skill roster | `docs/subsystems/skills.md` | — |

`docs/INDEX.md` is regenerated, never edited (Step 10).

## 10. Dependencies and Sequencing

```
Step 1 -> Step 2 (RED) -> Step 3 (GREEN; restart pending)
                               |
                               v
                          Step 4 (RED) -> Step 5 -> Step 6 -> Step 8 -> Step 9 -> Step 10
                                             |
                                             +-> Step 7 (table_cells roster; run it right after Step 5)
```
The maximum dependency depth is 9 (the chain 1-2-3-4-5-6-8-9-10); Step 7 is a side branch off Step 5. There are no cycles, and every step depends only on lower-numbered steps. Step 10 also depends on Step 3 directly, through the server restart.

## 11. Challenges
- **Two parsers, one format.** roadmap.py's strict reader is a different program from the wiki's lenient subset parser. The only honest proof of compatibility is differential (Step 5 group C), and it covers only what the corpus exercises. The corpus must therefore include every character class `check_line` and `check_scalar` admit.
- **The lock's residual window.** The window between the re-check and `os.replace` cannot be closed without a lock file. It is one syscall wide and declared.
- **Immutable archive vs `verify`.** User prose can carry an anchor that later vanishes. This is declared, and mitigated by SKILL.md guidance.
- **Server restart ordering.** Before the restart, the only protection against INDEX bloat is sequencing: no archive page exists in the live tree until the post-implementation `init`/`close`.
- **`table_cells` coupling.** It is non-obvious, and it fails the fleet the moment roadmap.py pads a cell. Step 5's verification expects that red on purpose, and Step 7 follows immediately.
- **In-process module loading.** Loading `reindex.py` mutates `sys.path` and caches `_wikilib`. Controls must swap `reindex_mod.w` for a namespace copy and restore it in `finally`, never mutate the shared module. Both new suites snapshot `sys.path` and, in `finally`, restore it and pop `_wikilib` from `sys.modules` (L8).
- **The caller side of S-H1 cannot be gated.** roadmap.py can prove that it never evaluates a staged value, but no suite can prove that the model building the Bash command line follows the staging rule. That half rests on SKILL.md prose (Step 8) and is declared in ADR 0022.
- **Git in the sandbox.** A developer's global git config (hooks, signing, `init.defaultBranch`) could leak into sandbox repos. The runner passes `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and explicit `-c` settings.

## 12. Critical Files for Implementation

| File | Role | Action |
|---|---|---|
| ClaudeCode/skills/roadmap/scripts/roadmap.py | the single writer | create |
| ClaudeCode/skills/roadmap/SKILL.md | skill contract, adopt op, export contract | create |
| `ClaudeCode/skills/wiki/scripts/_wikilib.py` | the six page-type constants (skill side) | modify |
| `ClaudeCode/skills/wiki/scripts/reindex.py` | INDEX render + orphan rule (skill copy) | modify |
| `ClaudeCode/skills/wiki/scripts/freshness.py` | untracked types read from `_wikilib` | modify |
| `Scripts/mcp-wiki.py` | the server copy of the constants, `render_index`, `reindex_collect` | modify |
| tests/test_wiki_index.py | parity + INDEX gate | create |
| tests/test_roadmap.py | the writer gate | create |
| `tests/run.py` | wrappers + SUITES rows (+ the table_cells count) | modify |
| `project-forge.yaml` | forge targets `wiki_index`, `roadmap`; the table_cells description | modify |
| `tests/test_table_cells.py` | renderer roster | modify |
| `ClaudeCode/skills/wiki/SKILL.md` | schema §1/§2/§3/§5/§8 | modify |
| `docs/components/wiki-engine.md` | the UNTRACKED_TYPES anchor + exemption sentence + the inbound link to ADR 0022 (M3) | modify |
| docs/adr/0022-a-someday-maybe-is-a-roadmap-item.md | the decision record | create |
| `ClaudeCode/skills/_lib/handoff-contracts.md` | producer/consumer contract | modify |
| `docs/subsystems/skills.md` | skill roster: the `p:roadmap` row, with the inbound link to ADR 0022 (M3) | modify |
| `docs/subsystems/tests.md`, `tests/README.md` | suite rosters | modify |
| `docs/INDEX.md` | regenerated | regenerate (Step 10) |

## 13. Post-Implementation Checklist

- [ ] `wiki_index` was observed RED after Step 2 and GREEN after Step 3; `roadmap` was RED after Step 4
- [ ] `render_index` / `reindex_collect` byte/tuple parity holds between the server and skill copies; the six constants are parity-gated
- [ ] There is no `roadmap-item` line in `docs/INDEX.md`, and exactly one rendered count line when items exist
- [ ] The anchor at `docs/components/wiki-engine.md:158` names `_wikilib.py`, and `wiki_call verify` shows no new gating finding
- [ ] roadmap.py imports no `_wikilib`, only stdlib, and holds no SKIP_DIRS copy; every git spawn goes through `git()` with `GIT_SAFE_ARGV` (incl. `protocol.allow=never`), `GIT_NO_LAZY_FETCH=1` in its environment, `stdin=DEVNULL`, a timeout and `--` before pathspecs; `SHA_RE` runs before any sha reaches git; ADR 0022 and 5.4 state that the local `.git/config` is trusted and that this hardening is declared, not gated (S4-3)
- [ ] roadmap.py never runs `git status`, `diff` or `add` (only `rev-parse`, `cat-file`, `ls-tree`); `source.dirty` is hashed in Python from `ls-tree` + `--show-object-format`, over-reports only, and SKILL.md's export contract calls it advisory; the planted clean filter and fsmonitor never ran in group H
- [ ] No shell anywhere in roadmap.py (no `shell=`, no `os.system` / `os.popen`); every value outside the exact inline allowlist reaches roadmap.py only through staged files (`add --item-file`, `link --item-file`, `--why-file`, `--reason-file`), `--spec` / `--slug` are refused outside `SLUG_RE`, and SKILL.md states the allowlist next to the single-writer rule
- [ ] ONE `check_line` guards every single-line value (title, every reason, origin, spec, severity, tags, slug), refusing separators, C0 controls, lone surrogates and the backtick; `check_scalar` and `check_list_item` call it first; `check_why` refuses every separator but `"\n"` and lone surrogates, and strips leading/trailing blank lines; a multi-line `--reason-file` is refused by `check_line`'s own message; `check_scalar` / `check_reason` RETURN the stripped value and every caller stores it; an empty reason on any route is the one `reason is empty` refusal; the `close` reason also passes `check_scalar`, and `cmd_close` round-trips the rendered archive through `parse_archive` before `create_exclusive` (H1, M1, M2 round 5)
- [ ] `cmd_init` decides every refusal before `ensure_archive_dir` and writes roadmap.md last; `create_exclusive` creates a missing `archive/`; neither route can die with a traceback on a missing directory
- [ ] roadmap.md, the roadmap directory, the archive directory and `--out` are refused when they are symlinks; `--file` must be shaped `<wiki root>/roadmap/roadmap.md`; `check_containment` keeps the realpath of the roadmap directory under the realpath of the git top-level (or the wiki root outside a work tree) before any command runs (S4-1)
- [ ] Layer purity holds: the FORBIDDEN names appear only in section 3 (`PURE_OS_NAMES` excepted), the clock only in `today()`, `sys.stdout` only in `emit()` / `emit_raw()` and `sys.stderr` only in `die()` / `note()`, no `print(`; the `export` stdout route uses `emit_raw` and equals the `--out` bytes; `create_exclusive` is called only from `cmd_close`; the callers of `write_atomic` are exactly `commit`, `cmd_init` and `cmd_export`; every mutating command except `cmd_init` ends in `commit(state)`
- [ ] Every file roadmap.py writes parses identically with both wiki frontmatter parsers; lists are inline (`[]` when empty); optional scalars are omitted; there are no `sources` / `targets` / `verified` / `links` keys and no backticks in generated text
- [ ] roadmap.md has no page H1, and the four H1 lanes are in fixed order; every item's `horizon:` matches its lane
- [ ] `wip_now` is a flat scalar with its unargued-value note; the export renders `"wip_limit": {"now": N}`; `wip <N> --reason` is the only way to change it
- [ ] `ready` is true iff every blocker is closed (done or dropped), in `list --ready` and in the export alike
- [ ] Every refusal is one `roadmap:` stderr line, exit 2, files byte-unchanged, no `.roadmap-*.tmp`
- [ ] The IO error rule holds PER CALL (NFR-13, R32, H1): every IO call in section 3 (forbidden callees outside `NONRAISING_IO_CALLS`, and `.write` / `.flush`) sits in the body of a `try` whose handlers spell `OSError` (not `IOError` / `EnvironmentError`), never only in a handler, `else` or `finally`; the handler dies through `io_fail` (`cannot <op> %s: %s`) or, for `git()` / `read_regular` / `worktree_dirty` / `wiki_page_names` / `_discard` / `_write_stream`, translates explicitly; `write_atomic`'s `abspath` is inside its first `try`; `read_regular`'s `os.close` is inside the outer guarded `try`; every `os.walk` passes `onerror=` (M1); `io_fail` always receives a real exception (L1); `IO_HANDLER_EXEMPT` is empty; `main()` has NO catch-all
- [ ] The stream route has defined exits (M2): EPIPE on stdout exits 0 with no `Exception ignored`, another stdout `OSError` is one `cannot write to standard output` line + exit 2, any stderr `OSError` exits 2; `_write_stream` and `_silence` live in section 3
- [ ] Every validating regex is compiled with `re.ASCII` and used with `fullmatch`; every date (log lines, archive `closed:`, `--today`, `--closed-since`) passes `check_date`, so `2026-02-30`, Arabic-Indic digits and a trailing newline are refused (M3)
- [ ] `_publish_link` unlinks its temp after a successful link and on both fallback outcomes; no `.roadmap-*` name survives an `init` or a `close` (L2)
- [ ] `export` refuses a lone surrogate in a `path` field before either route writes, so stdout and `--out` behave identically (L4)
- [ ] Every file content read goes through `read_regular` (lstat + `S_ISREG`, `O_NOFOLLOW | O_NONBLOCK`, fstat re-check, capped): roadmap.md, archive entries (including `show` of a closed item), staged files, the name scan, the worktree hash; `worktree_dirty` hashes a tracked symlink via `os.readlink` and never opens a non-regular entry; `PAGE_MAX_BYTES` is marked unargued (S3-1, R31)
- [ ] `_write_stream` encodes with `errors="backslashreplace"`; a non-UTF-8 path on the refusal route is one line with no traceback (round-3 M3)
- [ ] `git()` captures bytes, `ls-tree -z` runs in `binary=True` mode, paths are `os.fsdecode`-d on both sides of the `worktree_dirty` comparison; a git or record failure makes `dirty` null and a per-entry `OSError` makes it true (round-3 M4)
- [ ] The group A caller-set cases ran as EXACT equalities after Step 6, switched by the `roadmap` SUITES count in `tests/run.py` no longer being the `0` placeholder (read by AST at test time, L5), not by which functions happen to be defined (SUBSET only during Step 5; the declared sets were never edited)
- [ ] SKILL.md's staging rule allows only single-quoted staged / `--out` paths matching `.claude/tmp/roadmap-(stage|adopt)-[a-z0-9-]+\.(json|txt)`, with names chosen by the skill and never derived from harvested text (S4-2)
- [ ] Lanes and horizons are validated in code through `LANE_INPUT` (no argparse `choices=`); `--spec` is checked against wiki pages with a frontmatter `name:`, not bare stems; `--out` refuses a missing or non-directory parent; deeply nested item-file JSON is a one-line refusal
- [ ] Both new forge targets filter with `DRIFT|declared`, so the placeholder drift was visible while it existed
- [ ] The lock re-check sits immediately before `os.replace`, and runs twice in `close`
- [ ] Archive files are created exclusively and complete (link from an fsynced temp), and never rewritten
- [ ] The export golden is reproduced twice; counts are scope-true; `--today` makes every date reproducible
- [ ] The `table_cells` roster declares roadmap.py; `name_existence` is green
- [ ] SUITES counts are taken from the first green run, never typed from prose; no count appears in any docstring; no `0` placeholder is left in either new SUITES row
- [ ] Group K of `roadmap` ran (and passed) on the Step 4 red run, with roadmap.py still missing
- [ ] ADR 0022 declares: WIP 3 unargued, `PAGE_MAX_BYTES` unargued, the lock window, hand-deleted archive id reuse, archived backticked anchors, the wide name scan, the ungateable caller half of the staging rule, the git trust boundary (S4-3), and the two containment limits (an ancestor symlink outside a work tree; a symlink swapped in after the check; S4-1)
- [ ] ADR 0022 is linked as `[[0022-a-someday-maybe-is-a-roadmap-item]]` from `docs/subsystems/skills.md` and `docs/components/wiki-engine.md`, and is not an orphan
- [ ] ADR 0022 was written as `status: draft` without `verified:`, and promoted to `status: active` with `verified.commit` only after the implementation commit existed
- [ ] The mcp-wiki server is restarted before the first live `close`, and the Step 10 fixture probe reported 0 orphans
- [ ] `forge_call test all` is green with no drift
- [ ] Follow-ups are handed to the user and not done here: `init` + `adopt` of the live roadmap; the `_wikilib.git()` timeout and the stale `tests/test_spawn_stdin.py:71-72` docstring as the first roadmap candidate; the references to the old plan path that now resolve to this page (section 1, Out of Scope, including `docs/concepts/layer-contract.md:120`)

## Implementer checklist — round-5 non-blocking items

These did not block the plan; each is to be honoured during implementation (Steps 4-8) and ticked here.

- [ ] **M3 — stream-failure fakes.** The in-process fakes for the stdout/stderr failure branches raise `io.UnsupportedOperation` from `fileno()`; they never wrap a real file descriptor (so `_silence` can never `dup2` over the test runner's own fd 1 / fd 2). `_silence` also tolerates a stream without the attribute, via `getattr(stream, "fileno", None)`, catching `AttributeError` as well as `io.UnsupportedOperation` / `OSError`. Reason: `captured()` at `tests/test_checkpoint.py:1130-1134` installs a plain `io.StringIO` with no `.buffer` and a `fileno()` that raises, which is the shape the fakes copy.
- [ ] **M4 — EPIPE fixture size.** The early-closing-reader fixtures (group L `list`, group H `export`) are sized as a small multiple (e.g. 4x) of the pipe-buffer constant the test states, not "well over 1 MiB" open-ended; the test also asserts that the staged roadmap.md stays under `mod.PAGE_MAX_BYTES`, so the fixture can never trip the read cap instead of the pipe.
- [ ] **L1 — caller-set case counts.** The SUBSET and EXACT modes of the group A caller-set cases and of the `IO_FUNCTIONS` coverage case record the SAME number of cases; the mode (and the still-absent callers in SUBSET mode) appears only in the detail lines, so the `roadmap` SUITES count does not change when the mode flips.
- [ ] **L2 — `regex-ascii` coverage.** The group A `regex-ascii` case also flags module-level `re.match` / `re.search` / `re.sub` / `re.split` / `re.fullmatch` calls with literal patterns, OR every pattern in roadmap.py (including the one in `slugify`) is compiled with `re.ASCII` and used through its compiled object; pick one and state it in the case's docstring.
- [ ] **L3 — `refuse_symlink` message.** `refuse_symlink` takes a message parameter (e.g. `refuse_symlink(path, what, message=None)`), so the roadmap.md shape in section 7 (`%s is a symlink -- roadmap.py reads and replaces only a regular file it owns`) can be produced alongside the `archive directory %s is a symlink -- refusing` / `--out %s is a symlink -- refusing` / `roadmap directory %s is a symlink -- refusing` shapes.
- [ ] **L4 — archive `closed:` shape message.** Catalogue in section 7 (or reword) the SHAPE refusal for an archive `closed:` value (`archive/<name> closed %r is not a YYYY-MM-DD date`, from `check_date`); only the calendar half is catalogued today.
- [ ] **L5 — `_silence` prose.** In the `_write_stream` / `_silence` description (5.1, "Stream IO is IO"), the call is `_silence(stream)`, not `_silence(sys.stdout)` / `_silence(sys.stderr)`: `_write_stream` passes the stream it was given, and `sys.stdout` / `sys.stderr` are named only in `emit` / `emit_raw` / `die` / `note`.
- [ ] **Security LOW (round-5 triage) — harvested prose.** Prompt injection from harvested repository prose into the explorer / main context is the declared CALLER half, gated by user approval of the adopt candidates; keep SKILL.md's wording explicit that candidate text is DATA, never instructions.
- [ ] **Security LOW (round-5 triage) — `note:` paths.** Multi-line (or otherwise control-bearing) file names in `note:` stderr lines: render every path in a `note:` line with `%r` (e.g. `roadmap: note: wiki name scan skipped %r (%s)`), so a file name cannot break the one-line stderr rule.
