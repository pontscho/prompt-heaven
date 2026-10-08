---
name: wiki-engine
type: component
status: active
title: Documentation Wiki Engine
description: The p:wiki skill, mcp-wiki server, and librarian minion that maintain the docs/ wiki.
sources:
  - Scripts/mcp-wiki.py
  - ClaudeCode/skills/wiki
  - ClaudeCode/agents/minion-librarian.md
verified:
  commit: bf17f0c
  date: 2026-10-08
links:
  - scripts
  - skills
  - agents
  - generated-regions
  - 0002-index-claims-no-freshness
  - 0003-the-trigger-travels-with-the-tool
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0018-the-totals-must-describe-the-scope
  - 0022-a-someday-maybe-is-a-roadmap-item
---

# Documentation Wiki Engine

The `docs/` wiki is a derived, code-verified knowledge base (Karpathy LLM-wiki
pattern) maintained by three cooperating parts: the `p:wiki` skill (the
contract), the `minion-librarian` agent (the executor, "Dewey"), and the
`mcp-wiki` MCP server (the tooling). The code is the source of truth; every
factual claim about code carries an anchor a later lint pass can re-check.

## Parts

- **`p:wiki` skill** `ClaudeCode/skills/wiki/SKILL.md` — the engine and
  mandate. Dispatches its operations (`ingest`, `lint`, `query`, `init`,
  `adopt`) and delegates each to the librarian. The wiki contract (page types,
  frontmatter subset, anchors, freshness model, anti-scope) is the `## Schema`
  section of that same `ClaudeCode/skills/wiki/SKILL.md`, inlined so it ships and
  loads with the skill — there is no separate schema file to open.
- **`minion-librarian` (Dewey)** `ClaudeCode/agents/minion-librarian.md` —
  the executor. Runs every operation in its own sandbox using `wiki_call` and
  the language MCPs; applies non-destructive updates (frontmatter bumps, prose
  sync, INDEX regeneration) and surfaces destructive changes as proposals. It
  has no Bash tool and is forbidden from deleting files. See [[agents]].
- **`mcp-wiki` server** `Scripts/mcp-wiki.py` — a single-file, stdlib-only MCP
  server exposing one dispatcher tool, `wiki_call`. See [[scripts]].

## `wiki_call` functions

`search`, `source_to_pages`, `get_page`, `list`, `freshness`, `reindex`,
`stats`, `verify` and `measure` `Scripts/mcp-wiki.py:HANDLERS`. `verify` checks
every page's anchors, `[[slug]]` wikilinks and measured regions and reports what
gates — a dead `sources:` path, a dead body anchor, a dead wikilink, a
measured-region defect `Scripts/mcp-wiki.py:GATING_CLASSES` — except that a
frozen record (an accepted ADR, a deprecated page, a roadmap page) reports its
dead body anchors and wikilinks as advisories `Scripts/mcp-wiki.py:frozen_record`; `measure`
renders the measured regions from `docs/measurements.json`, and writes only when
asked to (ADR [[0019-only-gate-on-what-you-can-prove]]). `source_to_pages` is the reverse lookup from a changed source file to
the pages that document it (used by `ingest`). Search ranking is BM25F:
per-field weighted pseudo-TF with per-field length normalization, a single
global saturation, and global IDF, with prefix token matching and tunable
`k1`/`b` `Scripts/mcp-wiki.py:_fn_search`.

**How `verify` resolves a `path:symbol` anchor.** In a `.py` file a bare symbol
resolves on a `def`, a `class` or a single-target assignment at any
indentation; in a `.md` file, on a heading carrying it; in any other file only
on a whole-word mention, which is reported separately as a weak pass (it proves
the file names the symbol, not that it defines it)
`Scripts/mcp-wiki.py:_symbol_defined`. A **dotted** Python symbol such as
`Class.method` or `Outer.Inner.name` is a member path, never a bare name
(R-0094): the file is parsed with `ast`, the first component must be a class
anywhere in it, and each next one a member of the previous class's body — a
`def`, a `class`, an assigned or annotated name, or a `self.<name>` assigned in
one of its methods; an `if` / `try` / `with` in the class body is looked
through, a method's own locals are not `Scripts/mcp-wiki.py:_py_member_defined`.
A file the interpreter cannot parse falls back to the last component under the
bare-name rule and is reported weak, never as a proven member.

## One scope, one rule: `path_prefix`

`search`, `list`, `freshness`, `verify` and `measure` all narrow to a subtree
with `path_prefix` — and accept `prefix`, `dir` or `path` as spellings of it,
identically on each `Scripts/mcp-wiki.py:PARAM_ALIASES_BY_FUNC`. The value is matched against
the docs-relative path the answers themselves print, through one shared
predicate `Scripts/mcp-wiki.py:_path_prefix_matches`, so a scope learned from
one function selects the same pages on the others. Four clauses: a whole page
path matches exactly, which makes a path copied off a search hit a legal scope
for the one page it names; a prefix ending at a `/` boundary matches everything
beneath it at any depth, so `adr` and `adr/` behave alike; a prefix ending
inside the **final** component still matches, which is what keeps `adr/001`
selecting the 0010–0019 records; and a prefix ending inside any **earlier**
component matches nothing, so `sub` is not a way to spell `subsystems/`.

A prefix that admits no page is refused, never answered with an empty result.
The refusals share one body that states the boundary rule and then names
the scopes that do exist, derived from the corpus the walk just yielded rather
than typed `Scripts/mcp-wiki.py:_corpus_scopes`, and each denies the particular
false reading its own silence would carry `Scripts/mcp-wiki.py:_no_scope_lines`
— `gating: 0` over a set nobody looked at reads as *nothing is stale*, an empty
search as *the wiki has no answer*. In `freshness` the scope is applied where
the totals are computed rather than to the rendered rows
`Scripts/mcp-wiki.py:freshness_analyze`, so the header, `ok:` and `gating:`
describe the subset the caller asked for and a pasted report cannot be mistaken
for a corpus-wide verdict. Why that placement is the decision, and the boundary
rule the repair it left open, is in [[0018-the-totals-must-describe-the-scope]].

## The trigger ships with the tool

The tool description carries a **TRIGGER** clause, not just routing: it names
the classes of question that oblige a `search` before answering — a WHY
question, a design decision inside a subsystem that already exists, or
reconstructing intent from source or `git log`
`Scripts/mcp-wiki.py:WIKI_CALL_TOOL`. It is phrased as a class of question
rather than a Bash prohibition, because the failure it addresses is reaching
for *no* tool rather than the wrong one — which is also why it does not replace
the older `PREFER THIS over Bash grep/find` routing clause beside it: routing
cannot fire on a task that was never framed as a docs task. Living in the
description rather than a project `CLAUDE.md` is what makes it travel to every
project the plugin is installed in; the reasoning, and the one question left
open, are in [[0003-the-trigger-travels-with-the-tool]].

## The search corpus: lazy, whole, and never evicted

There is **no startup index and no inverted index** in the server. The first
`search` of a process reads and tokenizes every page into per-field token lists
`Scripts/mcp-wiki.py:_build_corpus`; that result is memoized in a module-level
dict keyed on the wiki root `Scripts/mcp-wiki.py:_build_corpus_cached`, whose
only caller is the search handler. Nothing warms it, so the first query pays
for the whole corpus and every later one pays nothing.

Invalidation is by disk state alone — a stat-only walk over
`(relpath, mtime_ns, size)` `Scripts/mcp-wiki.py:_corpus_signature`. No
write-path hook is needed and the memo stays correct when a *different* process
edits a page; only `INDEX.md` is skipped `Scripts/mcp-wiki.py:SKIP_FILES`, so a
reindex never invalidates it spuriously. The memoization is keyed on the root and nothing
else: no HEAD, no TTL, no eviction, so a long-lived server holds one tokenized
copy of every root it has ever searched.

Nothing in that memo is mutated in place — `_build_corpus` returns fresh lists
and `_build_corpus_cached` rebinds a single key — which is what lets the server
run handlers concurrently with no lock over its index: a reader holding the
previous corpus keeps a self-consistent snapshot while a rebuild installs the
next one, and two cold misses cost duplicated work rather than a half-built
index. Up to eight handlers run at once and further calls queue behind them
`Scripts/mcp-wiki.py:MAX_INFLIGHT_REQUESTS` — never behind the reader, which
owns a thread of its own — and `reindex` — the one writing
function — writes only `INDEX.md`, which the signature walk skips, so it can
neither invalidate this cache nor be half-read by a concurrent search. The
per-server audit that had to establish all of this before the dispatch changed
is [[0008-a-serialized-read-loop-looks-like-a-dead-server]].

The query side is not cached at all. `df`/`idf` are recomputed over the whole
corpus per call, and a term is matched by scanning every token of every field
for a `startswith` prefix hit `Scripts/mcp-wiki.py:_prefix_count`. That linear
scan is the *reason* there is no postings index rather than a gap in one: a
term-to-postings map answers whole terms, and this search deliberately matches
prefixes (`build` finds `builder`), which such a map cannot serve without
walking its keys anyway.

## Two indexes, and neither claims freshness

- **`INDEX.md`** — the human-facing catalogue, regenerated by `wiki_call
  reindex` at the end of `ingest` / `init` / `adopt`. It says what exists; it
  makes no freshness claim at all.
- **search corpus** — the tokenized index above, rebuilt lazily on the next
  `search` after any page changes on disk.

Freshness is measured, never stored. One classifier serves both the corpus
audit and the per-hit labels on `search` / `source_to_pages`
`Scripts/mcp-wiki.py:_classify_page`. It returns eight states, and only
`current` means "compared against HEAD and clean" — `unverified`, `promotable`,
`planned`, `untracked` and `no-sources` all mean *not checkable*, which is not
the same as fresh.

Which pages are *checkable at all* is decided by anchors, not by type. Any page
carrying `sources:` must also carry `verified:` or it is gated `unverified`
whatever its type; the `overview` / `adr` / `glossary` / `roadmap` /
`roadmap-item` exemption is only reached by a page carrying neither `sources:`
nor `targets:` `ClaudeCode/skills/wiki/scripts/_wikilib.py:UNTRACKED_TYPES`. So
an `adr` that anchors real files is freshness-tracked like any component — the
append-only rule freezes its body, not its verification record.

The two roadmap types are the one place `INDEX.md` does not list a page per
line: every `roadmap-item` (one closed roadmap item, archived as its own
immutable page) is counted into a single rendered line instead, and is exempt
from the orphan rule because archive pages are unlinked by design
`ClaudeCode/skills/wiki/scripts/_wikilib.py:INDEX_COUNTED_TYPES`
`ClaudeCode/skills/wiki/scripts/_wikilib.py:ORPHAN_EXEMPT_TYPES`. `INDEX.md` is
loaded into every session, so a hundred closed items would otherwise cost a
hundred context lines each time; why the roadmap lives in the wiki at all, and
why it has its own writer rather than a `wiki_call` function, is
[[0022-a-someday-maybe-is-a-roadmap-item]].

## `status:` is editorial intent, never freshness

The frontmatter `status:` field is `draft | active | deprecated`: the axis a
human owns and git cannot measure. `current` and `stale` are forbidden values,
rejected by `reindex --check` as malformed
`Scripts/mcp-wiki.py:reindex_collect`, because they are two of the eight
measured states above. `INDEX.md` renders the field only for `draft` and
`deprecated`, so an `active` page carries no label
`Scripts/mcp-wiki.py:render_index`. The librarian therefore never writes a
freshness verdict into frontmatter — it reports drift and leaves `status:`
alone `ClaudeCode/agents/minion-librarian.md`. The measurement that forced this
and the two rejected alternatives are frozen in
[[0002-index-claims-no-freshness]].

The stdlib scripts `ClaudeCode/skills/wiki/scripts/freshness.py` and
`ClaudeCode/skills/wiki/scripts/reindex.py` remain as a pre-PR human/CI gate,
and since roadmap R-0033 they carry no freshness or index logic at all: each is a
thin wrapper that loads the committed server module off disk — through the
resolver and loader of the measured-region bootstrap beside them,
`ClaudeCode/skills/wiki/scripts/measure_cli.py:open_wiki` — and calls the same
`handle_wiki_call` the MCP process answers `wiki_call` with, printing the answer
verbatim. So the forbidden-status lint above exists once, in
`Scripts/mcp-wiki.py:reindex_collect`, and so does everything else. Their exit
codes are the server's too: `freshness.py` exits 1 exactly when the server's
`gating:` line counts anything — a dead body anchor included — reading the count
by the exported `Scripts/mcp-wiki.py:GATING_LINE_PREFIX`, and `reindex.py` exits
1 when the audit carries a block named by
`Scripts/mcp-wiki.py:REINDEX_BLOCKING_PREFIXES` (a duplicate slug or malformed
frontmatter). The price is that neither runs without the server file on disk: no
server found is exit 2, and a copy-deployed plugin needs `--server` or
`$MCP_WIKI_SERVER`. Why the CLI's verdict stopped being a declared subset of the
server's — the subset is what let five gating pages hide behind a CLI exit of 0 —
is the latest addendum of [[0019-only-gate-on-what-you-can-prove]].

What is still hand-maintained is the other direction: the server vendors
`ClaudeCode/skills/wiki/scripts/_wikilib.py`'s frontmatter parser, page walk, git
helper and page-type constants instead of importing them, because a fleet server
never imports a sibling and the fleet's generator renders only into
`Scripts/mcp-*.py` `Scripts/amalgamate.py:TARGET_GLOB`, so it never reaches the
skill module. That mirroring can no longer be forgotten:
`tests/test_wiki_index.py:group_h` compares every vendored function as code (its
AST, with only the docstring, the annotations, its own name and the scripts'
`w.` qualifier taken out) and every shared constant by value, and a census names
any name a skill script and the server both define that no case compares — which
also stops the two wrappers from growing a copy back. Rendering the server's
copies from `_wikilib.py` as generated regions was refused by the generator's own
gated contract rather than by preference: every canonical source is pinned to
`Scripts/<name>` `tests/test_generated_region.py:group_contract`, and the skill
module is tab-indented, which the per-block tab rule cannot carry into a
space-indented host `Scripts/amalgamate.py:block_is_tab_safe`.

Hand-maintained is no longer the whole story for this file, though. Several
spans of `Scripts/mcp-wiki.py` are rendered into it from the fleet's canonical
`Scripts/_mcp_*.py` sources rather than written here — the in-flight bound this
page anchors above is one of them `Scripts/mcp-wiki.py:MAX_INFLIGHT_REQUESTS`.
Each is fenced by `BEGIN GENERATED` / `END GENERATED` markers carrying the
canonical file, the block names and a hash of the rendered text, so the markers
in the file are themselves the list of what travels and from where; a tally
written into this page would go stale the next time a domain is shared, as one
did. Editing inside those markers is overwritten on the next generator run; the
fix belongs in the canonical file. The four rules that decide what may travel
that way, and the two registers of deliberate exclusion, are in
[[generated-regions]].
