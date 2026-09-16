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
  commit: 35f4a89
  date: 2026-09-16
links:
  - scripts
  - skills
  - agents
  - generated-regions
  - 0002-index-claims-no-freshness
  - 0003-the-trigger-travels-with-the-tool
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0018-the-totals-must-describe-the-scope
---

# Documentation Wiki Engine

The `docs/` wiki is a derived, code-verified knowledge base (Karpathy LLM-wiki
pattern) maintained by three cooperating parts: the `p:wiki` skill (the
contract), the `minion-librarian` agent (the executor, "Dewey"), and the
`mcp-wiki` MCP server (the tooling). The code is the source of truth; every
factual claim about code carries an anchor a later lint pass can re-check.

## Parts

- **`p:wiki` skill** `ClaudeCode/skills/wiki/SKILL.md` — the engine and
  mandate. Dispatches five operations (`ingest`, `lint`, `query`, `init`,
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

`search`, `source_to_pages`, `get_page`, `list`, `freshness`, `reindex`, and
`stats`. `source_to_pages` is the reverse lookup from a changed source file to
the pages that document it (used by `ingest`). Search ranking is BM25F:
per-field weighted pseudo-TF with per-field length normalization, a single
global saturation, and global IDF, with prefix token matching and tunable
`k1`/`b` `Scripts/mcp-wiki.py:_fn_search`.

## One scope, one rule: `path_prefix`

`search`, `list` and `freshness` all narrow to a subtree with `path_prefix` —
and accept `prefix`, `dir` or `path` as spellings of it, identically on all
three `Scripts/mcp-wiki.py:PARAM_ALIASES_BY_FUNC`. The value is matched against
the docs-relative path the answers themselves print, through one shared
predicate `Scripts/mcp-wiki.py:_path_prefix_matches`, so a scope learned from
one function selects the same pages on the other two. Four clauses: a whole page
path matches exactly, which makes a path copied off a search hit a legal scope
for the one page it names; a prefix ending at a `/` boundary matches everything
beneath it at any depth, so `adr` and `adr/` behave alike; a prefix ending
inside the **final** component still matches, which is what keeps `adr/001`
selecting the 0010–0019 records; and a prefix ending inside any **earlier**
component matches nothing, so `sub` is not a way to spell `subsystems/`.

A prefix that admits no page is refused, never answered with an empty result.
The three refusals share one body that states the boundary rule and then names
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
whatever its type; the `overview` / `adr` / `glossary` exemption is only reached
by a page carrying neither `sources:` nor `targets:`
`ClaudeCode/skills/wiki/scripts/freshness.py:UNTRACKED_TYPES`. So an `adr` that
anchors real files is freshness-tracked like any component — the append-only
rule freezes its body, not its verification record.

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
`ClaudeCode/skills/wiki/scripts/reindex.py` carry the same freshness/index
logic — including the same forbidden-status lint
`ClaudeCode/skills/wiki/scripts/reindex.py:collect` — and remain as a pre-PR CI
gate (non-zero exit on stale pages, duplicate slugs, or malformed frontmatter);
`wiki_call` is the interactive path. That pair is hand-maintained: the server
vendors the script logic instead of importing it, and the fleet's generator
renders only into `Scripts/mcp-*.py` `Scripts/amalgamate.py:TARGET_GLOB`, so it
never reaches the skill's copies — a change to either copy must still be
mirrored in the other by hand.

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
