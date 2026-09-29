---
name: 0022-a-someday-maybe-is-a-roadmap-item
type: adr
status: active
title: A someday-maybe is a roadmap item
description: Decision to record known-but-unscheduled work as two new wiki page types written by one stdlib script in its own skill -- a live roadmap in horizon lanes and one immutable archive page per closed item, counted rather than listed in INDEX.md -- with the kept-versus-deferred rule, the priority model and its unargued WIP cap, the archive and optimistic-lock model, the staging rule that keeps harvested prose off the shell line, the git trust boundary, the export contract, every alternative rejected, and the limits declared rather than gated.
sources:
  - ClaudeCode/skills/roadmap/scripts/roadmap.py
  - ClaudeCode/skills/roadmap/SKILL.md
  - Scripts/mcp-wiki.py:render_index
  - ClaudeCode/skills/wiki/scripts/reindex.py:render_index
  - ClaudeCode/skills/wiki/scripts/_wikilib.py
  - tests/test_wiki_index.py
  - tests/test_roadmap.py
verified:
  commit: f581b1b
  date: 2026-09-28
links:
  - roadmap
  - spec-roadmap
  - 0002-index-claims-no-freshness
  - 0013-the-ceiling-is-a-payload-class
  - 0016-a-cell-may-not-forge-a-boundary
  - 0018-the-totals-must-describe-the-scope
  - 0019-only-gate-on-what-you-can-prove
  - skills
  - tests
  - wiki-engine
---

# ADR 0022: A someday-maybe is a roadmap item

**Status:** accepted (implemented, `f581b1b`). Append-only from here. The
living WHAT/HOW is `ClaudeCode/skills/roadmap/SKILL.md` and the docstring of
`ClaudeCode/skills/roadmap/scripts/roadmap.py`, described in [[skills]].

## Context — known work had nowhere to live

The repository already knows a good deal of work it has not scheduled. ADRs end
in declared limits. Spec pages carry `targets:` for code nobody has written.
Research pages close with open questions. And `requirements.yaml` holds exactly one
plan at a time, overwritten by the next, with no history. What sits between "a
limit an ADR wrote down" and "the one plan in flight" had no home: the wiki's own
anti-scope sent a vague someday-maybe to "an issue", and a plugin that runs in any
repository cannot assume that repository has an issue tracker, or that its agents
can reach one.

Two facts shaped every answer below.

**Not every declared limit is work.** Some are decisions. ADR 0010 calls its gate's
blind spot "permanent, not a TODO"; ADR 0015 calls its blind spots "declared, not
deferred". Harvesting every limit into a backlog would turn deliberate decisions
into nagging tasks, and the next reader would take the backlog as the authority
over the ADR that decided the opposite.

**`INDEX.md` is loaded into every session.** Anything added to it is paid for in
context on every session, forever. A roadmap that grows by one page per closed
item cannot be listed there one line per page.

## Decision

1. **The roadmap lives in the wiki, as two page types with one writer.** The live
   roadmap is one page of type `roadmap` (docs/roadmap/roadmap.md): frontmatter, a
   generated summary region, and the four lanes `now`, `next`, `later` and the
   inbox. Every closed item becomes its own page of type `roadmap-item` under
   docs/roadmap/archive/, named `NNNN-<slug>.md` where NNNN is the item id. Both
   are written ONLY by `ClaudeCode/skills/roadmap/scripts/roadmap.py`, a stdlib
   script in its own skill, and both are ordinary pages to `wiki_call` `search`.
   The p:wiki anti-scope is amended accordingly: a vague someday-maybe is a roadmap
   item, not a page and not an issue.

2. **`INDEX.md` counts archive pages and never lists them.** Both copies of the
   renderer render one count line for every `roadmap-item` page instead of a line
   each, so the index cost of the archive is O(1) in the number of closed items
   `Scripts/mcp-wiki.py:render_index`
   `ClaudeCode/skills/wiki/scripts/reindex.py:render_index`. Archive pages are
   exempt from the orphan rule, because they are unlinked by design; an unlinked
   `roadmap` page is not. Both types are freshness-untracked, which holds only while
   they carry no `sources:`, so roadmap.py never writes `sources`, `targets`,
   `verified` or `links`. The six page-type constants that encode this are homed
   once on the skill side, `ClaudeCode/skills/wiki/scripts/_wikilib.py`, and the
   server keeps its own copy, gated equal to it by the `wiki_index` suite
   `tests/test_wiki_index.py`.

3. **Kept is not deferred.** A declared limit becomes an item only when it is
   deferred work. A kept limit is a decision and never becomes one. The verdict is
   a judgment on the sentence that states the limit, never a pattern match: during
   `adopt` the explorer proposes it with the quoted sentence as evidence, and the
   user decides.

4. **Priority is a horizon plus the order of the blocks.** The lane is the coarse
   priority; within a lane the order of the blocks in the file is a total order,
   changed only by `rank`. There is no numeric priority, no label and no score.
   `severity` is producer information and never priority. `now` is capped by one
   flat frontmatter scalar, `wip_now`, which counts every item whose horizon is
   `now` whatever its state: `active` is the state that must be in `now`, but a
   `planned` item sitting in `now` occupies the lane just as much. The cap is
   checked only on transitions into `now` and by the `wip` command, never on load
   (`ClaudeCode/skills/roadmap/scripts/roadmap.py:validate`), so a lowered cap can
   never block moving an item out.

   **The value 3 is unargued.** Nobody measured it; it is a starting value. The
   file says so in the comment above the key, and the only route to change it is
   `wip N` with a reason, which rewrites that comment with the old value, the date
   and the reason. This is the lesson of [[0013-the-ceiling-is-a-payload-class]]:
   a number nobody argued for must not be ratified by being typed often enough.

5. **`ready` means every blocker is closed, done or dropped.** A dropped blocker is
   a void dependency: the work it stood for will never happen, so an item waiting
   on it would wait forever. `ready` and the cycle check run over live and archived
   items together. Archived edges are frozen, so a new cycle can only be introduced
   through a live item. `ready` is the one derived field in the export.

6. **One immutable archive page per closed item, created first, and the archive
   wins.** `close` writes the archive page before it removes the live block. The
   page is published by writing a temp file, fsyncing it and hard-linking it to its
   final name (`ClaudeCode/skills/roadmap/scripts/roadmap.py:_publish_link`), which
   fails on an existing name exactly as an exclusive create does and can never
   leave a truncated page behind; where hard links are unavailable the fallback is
   an exclusive create (`ClaudeCode/skills/roadmap/scripts/roadmap.py:create_exclusive`).
   A crash between the two writes leaves the id in both places. Every run detects
   it, the archive copy wins (`ClaudeCode/skills/roadmap/scripts/roadmap.py:build_state`), a mutating
   command persists the repair and says `repaired:`, and a read command repairs in
   memory and says `note:`. There is no reopen: the archive is immutable, and a
   regression is a new item that names its closed predecessor in `follows:` and
   carries its own origin. `close --commit` verifies the commit exists and stores
   the full sha, because an abbreviated one can become ambiguous later in a file
   that can never be corrected.

7. **An optimistic lock over two digests.** There is no lock file. A command
   remembers the sha256 of the roadmap bytes it read and of the sorted archive
   listing, and the atomic writer
   (`ClaudeCode/skills/roadmap/scripts/roadmap.py:write_atomic`) re-takes both after
   the temp file is complete and
   immediately before the replace; a mismatch refuses with nothing written and the
   temp file removed. The archive digest is in the lock because `add` derives the
   next id from the archive listing, so a concurrent close must invalidate it.
   `close` checks twice: before the archive create, and again inside the replace,
   against the snapshot with exactly one archive name added. The precedent's
   read-then-replace, which re-checks nothing, is the lost-update hole this closes.

8. **One line rule.** Every single-line value (title, every reason, origin, spec,
   severity, each tag, the slug) passes one check,
   `ClaudeCode/skills/roadmap/scripts/roadmap.py:check_line`, before it is written. It refuses every character the running Python's `str.splitlines`
   splits on, every other C0 control, every lone surrogate and the backtick. Each
   of these values is written onto exactly one line, and a separator forges a
   second one: a `wip` reason is written into the frontmatter comment above
   `wip_now`, where a newline would forge a frontmatter key such as
   `status: deprecated`. A lone surrogate is refused because the strict UTF-8
   encode of the write would otherwise raise a traceback instead of a refusal, and
   both a `\ud800` escape in a JSON item file and non-UTF-8 argv bytes produce one.
   The backtick is refused so that everything roadmap.py generates is
   backtick-free: `wiki_call` `verify` reads a backticked path in a page body as an
   anchor.

9. **Untrusted text never reaches a command line.** `adopt` turns repository prose
   into titles, whys and origins, and this plugin runs in whatever repository the
   user is in. If the main context typed that prose into the Bash line that invokes
   roadmap.py, a `$(...)`, a backtick or a `"` inside it would run as the user. So
   the skill's staging rule, stated beside its single-writer rule, allows EXACTLY
   these inline values on that line: the command name and flags; ids, lane names
   and state names; a hex sha and a `YYYY-MM-DD` date; the integer for `wip`;
   staged-file paths, including the export's `--out`, only single-quoted and only
   when they match the pattern .claude/tmp/roadmap-(stage|adopt)-[a-z0-9-]+\.(json|txt)
   with a name the skill chose and never one derived from harvested text; and
   `--spec` / `--slug` values, only single-quoted and only when kebab-case. Every
   other value is staged in a file and passed by path: a whole item as a JSON
   object through `add --item-file`, an origin, spec or blocker change through
   `link --item-file`, and whys and reasons through `--why-file` and
   `--reason-file`. roadmap.py backs what it can: `--spec` and `--slug` are refused
   outside kebab-case, the item file goes through exactly the checks the flags do
   (`ClaudeCode/skills/roadmap/scripts/roadmap.py:parse_item_file`), and the script
   spawns no shell anywhere (one spawn site,
   `ClaudeCode/skills/roadmap/scripts/roadmap.py:git`, an argv list, gated by AST in
   `tests/test_roadmap.py`).

10. **An IO error is one refusal line, and the rule that says so is structural.**
    Three review rounds each found another traceback of one class, an OS or
    Unicode error escaping an IO call, and patching them one at a time did not
    close the class. So all IO is confined to one section of the module, and in
    that section every IO call sits in the body of a `try` whose handler names
    `OSError`, checked per call by AST. The error becomes one `cannot <op> <path>:
    <reason>` line, unless the function's contract is to report the condition (the
    git helper `ClaudeCode/skills/roadmap/scripts/roadmap.py:git` returns an exit
    code, the capped reader `ClaudeCode/skills/roadmap/scripts/roadmap.py:read_regular`
    returns a reason, the dirty check
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:worktree_dirty` returns true or
    null). The per-call form is the decision; a per-function
    form ("the function has some `except OSError`") was rejected because a writer
    whose temp-file creation sits above its `try` passes it with one guarded site
    and one unguarded one, which is exactly the escape that started this. There is
    deliberately no catch-all in
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:main`: a traceback raised outside the IO section
    is a bug in pure code and must stay visible. Writing to the standard streams is
    IO too, with defined exits: a reader that closes early (EPIPE on stdout) ends
    the run with 0 and no noise, any other stdout error is one line and exit 2, and
    any stderr error exits 2 silently, because the refusal channel itself is gone.

11. **Every file read is capped, regular and unfollowed.** One reader,
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:read_regular`, reads every byte
    roadmap.py reads: it lstats, refuses a non-regular file, opens
    with no-follow and non-blocking flags, re-checks with fstat, and reads at most
    the cap. A symlink, a FIFO or an oversized file is refused, skipped with a note,
    or counted dirty; it is never followed or opened blocking. The cap,
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:PAGE_MAX_BYTES` (4 MiB), is **unargued** exactly like `wip_now`: it bounds
    memory against a planted huge file, and no measurement chose it.

12. **The git helper never refreshes the index, and the local git config is
    trusted.** Every git spawn goes through one helper,
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:git`, whose argv starts with the
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:GIT_SAFE_ARGV` prefix (`core.fsmonitor=false`, `core.hooksPath=/dev/null`,
    `protocol.allow=never`, `safe.bareRepository=explicit`) and whose child
    environment sets `GIT_NO_LAZY_FETCH=1`.
    roadmap.py never runs `git status`, `git diff` or `git add`: an index refresh
    runs a repository-configured clean filter and fsmonitor, and a `-c` override
    exists only for the latter. That is why the export's `source.dirty` is computed
    in Python (`ClaudeCode/skills/roadmap/scripts/roadmap.py:worktree_dirty`), by hashing the roadmap directory's working files as git blobs
    against `git ls-tree` of HEAD, never by `git status`.

    The trust boundary, stated: the target repository's .git/config is TRUSTED.
    Clone does not transfer it, and anyone who can write it can already run code as
    the user the next time the user runs git there. What a clone can carry is
    `.gitattributes`, and an attribute runs nothing without a driver defined in
    config. The gated claim is therefore only about roadmap.py's own behaviour: it
    never runs an index-refreshing command (the suite plants a clean filter and an
    fsmonitor hook and proves neither fires). The `GIT_SAFE_ARGV` prefix and
    `GIT_NO_LAZY_FETCH=1` are cheap hardening against a config that is hostile
    anyway, declared here and NOT a gated guarantee.

    One config the premise above does not cover: a bare repository planted in the
    working tree. git's discovery from the roadmap directory tests each directory
    for a HEAD, an `objects/` and a `refs/` before it reaches the outer `.git`, so
    those files tracked under `docs/roadmap` are adopted as the repository, and
    their `config` is ordinary tracked content that clone DOES transfer.
    `safe.bareRepository=explicit` closes that discovery: git then refuses the
    directory rather than falling through to the outer repository, so the export
    reports `head` and `dirty` as null there instead of the planted repository's
    values. This entry alone is exercised: the suite plants such a layout, with a
    `core.abbrev` whose effect would be visible in `head`, and proves the export
    never reports it (after first proving the suite's own git, unpinned, does).

13. **The roadmap directory must resolve inside its tree.** Refusing a symlinked
    roadmap.md or archive directory does not cover a symlinked `roadmap/` or a
    symlinked ancestor, through which every write would land outside the
    repository while every per-file check passed. So before any command runs, the
    realpath of the roadmap directory must lie inside the realpath of the git
    top-level, or, outside a work tree, of the wiki root
    (`ClaudeCode/skills/roadmap/scripts/roadmap.py:check_containment`). Realpaths on both sides
    keep a symlinked ancestor above the tree (macOS `/var`) harmless.

14. **Every date is a calendar date, and every pattern is ASCII and anchored by
    fullmatch.** Log dates, the archive `closed:` value, `--today` and
    `--closed-since` pass one check,
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:check_date`: the shape, then a
    real calendar date, so a
    planted `2026-02-30` is refused at parse time instead of travelling into the
    export. Every regex is compiled with `re.ASCII`, so `\d` never admits an
    Arabic-Indic digit that `int()` would still accept, and is used only through
    `fullmatch`, because `$` also matches before a trailing newline.

15. **The export is a contract, not a schema file.** `roadmap-export/1` is
    read-only, takes no lock, and is byte-identical across runs over the same
    inputs: no wall clock, sorted keys, a fixed item order. Its `counts` describe
    the items in the output, not the whole roadmap, the rule of
    [[0018-the-totals-must-describe-the-scope]]. `ready` is its one derived field.
    `why` is raw markdown, and escaping it is the consumer's job. `source.dirty` is
    advisory and can only over-report, never under-report. An additive field keeps
    the version; a breaking change bumps it. The contract lives in the skill and in
    a golden export the suite reproduces byte for byte, twice.

## Alternatives Evaluated

Placement and format:

1. **Build the roadmap into `Scripts/mcp-wiki.py`.** *Rejected:* the server writes
   only `INDEX.md` and measured regions; a roadmap writer would end its write-free
   character. The server only reads roadmap pages.
2. **A root ROADMAP.md outside the wiki.** *Rejected:* `wiki_call` `search` could
   not see it.
3. **A single archive file.** *Rejected:* it grows without bound and every close
   rewrites it. One immutable file per closed item replaces it.
4. **List every `roadmap-item` in `INDEX.md`.** *Rejected:* N closed items would
   cost N context lines in every session. One rendered count line replaces them.
5. **Fenced YAML inside roadmap.md for the item fields.** *Rejected:* a third
   format for the same data. The stdlib frontmatter subset, in two places, is one.
6. **A separate archive sequence number.** *Rejected:* NNNN = the item id makes
   resolving `R-0014` to its archive page trivial. Gaps are informative, and the
   closing order is in `closed:`.
7. **The librarian as the `adopt` executor.** *Rejected:* it has no Bash, its op
   table is closed and its `adopt` already means frontmatter onboarding, and
   orchestration stays in the skill. The read-only explorer harvests; the main
   context writes.
8. **Extend the amalgamate generator to skill hosts to share the constants.**
   *Rejected:* it widens the generator's scope, disturbs its pinned target glob and
   adds a new source domain, all for six constants. A parity gate does the job.
9. **Import `_wikilib` from the server at run time.** *Rejected:* the fleet rule
   forbids a server importing a sibling, and the deploy symlink complicates it.
10. **A JSON Schema file for the export.** *Rejected:* the stdlib cannot validate
    it, so no gate would ever run it. The contract lives in the skill and a golden
    export.
11. **An `ARCHIVE_TYPE` wiki constant.** *Rejected:* two constants that name the two
    behaviours, one exempting from the orphan rule and one counting in the index,
    replace one that names the type and leaves each reader to infer the behaviour.

Priority and lifecycle:

12. **P0-P3 labels, RICE / WSJF scores, producer severity as priority.**
    *Rejected:* labels are a second priority axis and they inflate; scores are
    false precision and typed numbers ([[0019-only-gate-on-what-you-can-prove]]);
    severity is producer information, not priority.
13. **Reopening a closed item.** *Rejected:* the archive is immutable. A regression
    is a new item with `follows:`.
14. **Auto-dropping stale inbox items.** *Rejected:* dropping is a decision, taken
    with `close` and a reason.
15. **"A dropped blocker keeps an item unready."** *Rejected:* a dropped item can
    never become done, so the item would be stranded forever.
16. **Check the WIP cap in validation on every load.** *Rejected:* a hand-lowered
    cap would then block every command, including moving items out of `now`.
17. **A nested `wip_limit:` block in the frontmatter.** *Rejected:* the wiki subset
    allows nesting for `verified:` only. The flat `wip_now` stays inside it, and the
    export still renders `{"now": N}`.

File format and writer:

18. **Copy checkpoint.py's read-then-replace verbatim.** *Rejected:* nothing between
    its read and its replace re-checks the file, and parallel sessions exist.
19. **A lock file with `fcntl.flock`.** *Rejected:* the optimistic lock was the
    design's decision; flock is POSIX-only in practice, leaves stale lock files
    after a crash, and is a second on-disk artefact. The residual window is declared
    below instead.
20. **Lanes as `## NOW` headings, the level of the items.** *Rejected:* the parser
    would have to tell lanes from items by token. H1 lanes over H2 items make the
    level the discriminator.
21. **A page H1 (`# Roadmap`).** *Rejected:* the title already lives in the
    frontmatter, which is what the index and search use; a fifth H1 beside the four
    lanes would be a generated line with no reader.
22. **Write empty lists as absent keys.** *Rejected:* one spelling is better. Lists
    are always inline and `[]` when empty; optional scalars are omitted.
23. **A declared copy of `_wikilib`'s SKIP_DIRS in roadmap.py, gated by value.**
    *Rejected:* the archive-slug collision scan reads more than the wiki does, and a
    false refusal costs only a `--slug`, while a copy is one more thing to drift.
24. **Refuse a roadmap whose `archive/` is missing.** *Rejected:* git drops empty
    directories, so every fresh clone without a closed item would be refused on its
    first `close`. The archive directory is created instead.
25. **Write roadmap.md before creating `archive/` in `init`.** *Rejected:* the temp
    file needs `roadmap/` to exist, so `init` died with a traceback on a bare wiki
    root. Every refusal is decided first, then the directories, then the file.
26. **Let a read command persist a duplicate-id repair.** *Rejected:* a read command
    that writes would surprise its caller and would need the lock. Read commands
    repair in memory and print a note.

Untrusted input and the character rule:

27. **The explorer runs `list` / `export` himself during `adopt`.** *Rejected:* he
    has no Bash. The main context exports to a staged file and he reads it.
28. **Pass `adopt` candidates as `add --title "..." --why "..."`.** *Rejected:*
    harvested repository prose would be interpolated into a shell command line
    (CWE-78). Replaced by `add --item-file`.
29. **A `key: value` item file instead of JSON.** *Rejected:* the subset refuses
    quote-wrapped values and has no multi-line value, so a harvested why could not
    be carried, and the stager would have to know roadmap.py's refusal rules to
    encode a title. JSON has one escaping for every character and `json.loads` is
    stdlib.
30. **Normalize a line separator in a value to a space instead of refusing it.**
    *Rejected:* it would silently change user text. A refusal names the character
    and the field, and the caller fixes the input.
31. **Escape a lone surrogate in an export path instead of refusing the export.**
    *Rejected:* the JSON would then carry a path that names no file. Both routes,
    stdout and `--out`, refuse identically before writing anything.

IO and git:

32. **A per-function IO error rule.** *Rejected* in favour of the per-call rule, for
    the reason in Decision 10.
33. **`git status --porcelain` for `source.dirty`.** *Rejected:* it refreshes the
    index, which runs a clean-filter driver configured in the target repository,
    and no `-c` override exists for a filter driver. Hashing the working files as
    blobs replaces it, and can only over-report.

## Consequences

- **A vague someday-maybe now has a destination,** and it is searchable with the
  rest of the wiki. [[wiki-engine]] records the index and orphan rules; [[skills]]
  and [[tests]] carry the roster rows.
- **The server needs a restart to learn the two types.** A server that predates
  them appends unknown types to the index rather than dropping them, so it would
  list every archive page: the regression the count line exists to prevent. The
  skill says so.
- **Two implementations of the frontmatter subset exist,** the wiki's and
  roadmap.py's writer and strict reader. Everything roadmap.py writes is gated to
  parse identically under both wiki parsers `tests/test_roadmap.py`.
- **Declared, not gated:**
  - **The lock window.** The re-check and the replace are two syscalls; a writer
    that lands between them is not detected. The window is one syscall wide.
  - **Id reuse after a hand-deleted archive file.** The next id is one more than
    the highest id in roadmap.md and in the archive filenames. If the highest
    archive page is deleted by hand, its id can be issued again. The archive is
    immutable by contract, and deleting a page breaks that contract first.
  - **A backticked anchor in archived why prose.**
    `ClaudeCode/skills/roadmap/scripts/roadmap.py:check_line` keeps backticks out
    of every single-line value, so why prose is the only route an anchor can take
    into an archive page, and the skill steers anchors into `origin:`. An archived
    why whose backticked anchor later vanishes is a permanent `verify` finding the
    archive cannot fix.
  - **The name scan is wider than the wiki's SKIP_DIRS.** The archive-slug
    collision scan reads every `*.md` under the wiki root except INDEX.md and dot
    directories. It can refuse a slug the wiki itself would never see; the cost is
    a `--slug`.
  - **The caller half of the staging rule.** The inline allowlist is prose in the
    skill, followed by a model; no suite can gate what a caller types. roadmap.py
    gates its own half (kebab-case `--spec` / `--slug`, no shell, the item file
    checked like the flags), and approval of every `adopt` candidate by the user is
    the other barrier. Harvested text is data, never instructions, and the skill
    says so.
  - **The git hardening.** `GIT_SAFE_ARGV` and `GIT_NO_LAZY_FETCH=1` are defense in
    depth against a local config the trust boundary already treats as trusted. No
    suite exercises a promisor remote or a lazy fetch.
  - **Two containment limits.** Outside a work tree there is no containing tree to
    measure an ancestor against, so a symlinked ancestor of the wiki root there is
    accepted: the caller named that path. And a symlink swapped in after the check
    and before the write is the same one-syscall race class as the lock window.
  - **Two unargued numbers:** `wip_now` = 3 and `PAGE_MAX_BYTES` = 4 MiB. Each is
    marked as such where it is defined, and each changes only with a reason.
- **Out of scope, and the first items once the live roadmap exists:** the producer
  hooks (ADR authoring, bug-hunt "diagnosis only" reports, checkpoint and recap
  open threads, unfixed review findings), a kanban board (only the export contract
  ships), and commands that edit an item's title, why, tags or severity after
  `add`. Bootstrapping the live roadmap (`init`, then `adopt`) needs the user's
  approval and is not part of this decision.
- **No count is written here.** The suites' sizes are in the `SUITES` rows of
  `tests/run.py`.

## Addendum (2026-09-28): the containment window is not one syscall wide

The second of the two containment limits above files a symlink swapped in after the check and before the write under the same one-syscall race class as the lock window. That overclaims. The lock window is one syscall wide: the re-check and the replace sit next to each other. The containment check does not: it runs once, in `main` before the command is dispatched `ClaudeCode/skills/roadmap/scripts/roadmap.py:main`, and the write comes at the end of `commit`, so the window spans argument parsing, the git spawns, the wiki scan and rendering. The limit itself stands and stays declared, not gated: the path is the caller's own and the trust boundary already treats the local tree as trusted. Only the width was wrong. Recorded as roadmap item R-0026.

## Addendum (2026-09-29): planned means has a plan, and one item owns the plan slot

The Context above says `requirements.yaml` holds exactly one plan at a time, while the now lane holds up to three items. Nothing linked the two, and `planned` had structural invariants but no meaning; the skill's example even showed adopt producing it, which adopt never does. Closed as roadmap item R-0030.

`idea` is known but not worked out, `planned` is an item with a plan (a `spec:` design page, or a requirements.yaml naming it), `active` is work in progress. A small item still goes from idea to active in one `start`.

The only link is an optional top-level `roadmap_item: R-NNNN` in requirements.yaml, one-way: the roadmap never mirrors task status. The task validator checks the shape only (`Scripts/task-validator.py:ROADMAP_ITEM_RE`), never whether the id exists. Before overwriting a requirements.yaml whose `roadmap_item` is still live, /p:task-plan stops and asks, so at most one item owns the slot; when it writes a plan it moves an idea to planned (an inbox idea to next). /p:implement does not commit, so its last handoff prints the `close` command for after the user's commit and never runs it.

Declared, not gated: the slot check and the move are skill prose followed by a model, like the caller half of the staging rule, and a plan abandoned mid-way leaves its item planned. roadmap.py did not change.

## Addendum (2026-09-29): roadmap.py sized to its job (R-0024)

Closed as roadmap item R-0024, after a review that measured every mechanism of roadmap.py and its suite against this ADR's own trust boundary.

(a) Alternative 33 rejected `git status` for `source.dirty` because it would run a clean-filter driver configured in the target repository. Decision 12 declares that repository's .git/config trusted, and a filter or fsmonitor comes only from there, so the rejection reason contradicted Decision 12. `worktree_dirty` also opened a path-joining surface of its own, and the field had no consumer. `worktree_dirty` and `source.dirty` are removed; the export is `roadmap-export/2`, whose `source` carries `head` only. `safe.bareRepository=explicit` (the planted bare repository, F19) stays, and so does the rule that roadmap.py runs only read-only git subcommands (now `rev-parse` and `cat-file`).

(b) Decision 10 is refined. The per-call rule and its AST gate are replaced by ONE `except OSError` in `main`, which turns an IO error nothing nearer mapped into one `roadmap: IO error ...` line and exit 2. This is not the catch-all Decision 10 rejected: pure code raises no OSError, so a bug there still surfaces as a traceback. Callers that have something better to say still map their own errors, and the writers keep their temp-file cleanup. A reader that closes early is one `except BrokenPipeError` in `main` (stdout pointed at /dev/null, exit 0); the UTF-8 byte route for the standard streams stays. A broken stderr no longer has a defined exit code: the refusal channel is gone.

(c) The architecture lint gates of the suite (IO in one section, the clock in `today()`, the stream owners, no print, one reader, `re.ASCII`/fullmatch, commands ending in commit, the per-call IO gate with its planted controls, the SUBSET/EXACT count switch) are removed: they defended no threat. The security gates stay: stdlib only, no shell, the one git spawn site with its argv prefix and read-only subcommands, the format contract, the wiki type membership, and the exact caller sets of the two write routes.

(d) Refusals that duplicated a later one are removed: the `export --out` symlink, directory and missing-parent pre-checks (a symlinked `--out` is replaced by `os.replace`, never written through; the rest is the writer's one-line failure; the refusal to write over the roadmap or its archive stays), the staged-file is-the-roadmap check, the roadmap.md symlink pre-check (the capped reader's lstat refuses it), and the export's lone-surrogate path refusal, so Alternative 31's claim that both export routes refuse identically no longer holds. The capped reader keeps lstat, the regular-file check, the cap and no-follow; the fstat and growth re-checks and the non-blocking open of Decision 11 are dropped, since a FIFO or a swap after lstat needs a local actor.

(e) `verify_commit` and the link-publish of Decision 6 are intentionally unchanged.
