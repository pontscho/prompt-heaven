---
name: 0002-index-claims-no-freshness
type: adr
status: active
title: The index claims no freshness; frontmatter carries only editorial intent
description: Decision to rename the hand-written status value current to active, suppress it in INDEX.md, and forbid the two values that collide with git-measured freshness states.
sources:
  - ClaudeCode/skills/wiki/SKILL.md
  - ClaudeCode/skills/wiki/scripts/reindex.py
  - ClaudeCode/agents/minion-librarian.md
  - Scripts/mcp-wiki.py
verified:
  commit: 546f145
  date: 2026-08-06
links:
  - wiki-engine
  - scripts
---

# ADR 0002: The index claims no freshness

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is [[wiki-engine]] and the schema itself.

## Context

Two enums described the state of a wiki page, and they overlapped on exactly the
two words that mattered.

The **hand-written** frontmatter field `status:` was `current / draft / stale /
deprecated`. The **git-measured** classifier `_classify_page`
`Scripts/mcp-wiki.py` produces eight states: `current`, `stale`,
`orphaned-source`, `unverified`, `promotable`, `planned`, `untracked`,
`no-sources`. `current` and `stale` appear in both.

`reindex` copied the hand-written field into `INDEX.md` verbatim — no git input
on that path at all `ClaudeCode/skills/wiki/scripts/reindex.py`. The result was
measurable and wrong: `INDEX.md` printed `` `[current]` `` for all ten pages
while `freshness` measured **nine of them stale**. `INDEX.md` is `@`-imported by
the project `CLAUDE.md`, so that false claim was loaded into the context of
every single session, and the only defence was a five-line prose warning telling
the reader not to believe the file directly above it.

The failure is structural, not a bug: a generated file cannot track HEAD. It is
regenerated only when someone runs reindex, while freshness changes on every
commit.

## Decision

Split the two axes by vocabulary, and let the index stop claiming the one it
cannot know.

1. The frontmatter enum becomes **`draft | active | deprecated`** — editorial
   intent only, the axis a human owns and git cannot measure: *is this page
   finished, and does it still describe a live design?*
2. `current` and `stale` become **forbidden values**, rejected by
   `reindex --check` as malformed. A hand-written field may not borrow HEAD's
   vocabulary.
3. `INDEX.md` renders the field **only** for `draft` and `deprecated`. `active`
   is the unmarked normal state, so a healthy page carries no label — the index
   is a catalogue of what exists, and it emits nothing a reader could mistake
   for a measurement.
4. Freshness is asked, never stored: `wiki_call search` labels every hit against
   git at query time, and `wiki_call freshness` audits the corpus.

## Alternatives Evaluated

### Option 1 — Measure the label at reindex time
Call `_classify_page` from `render_index` and print the real, git-measured state
into `INDEX.md`.
- **Pros:** ~5 lines. `_classify_page` is parameter-pure, has no MCP-server
  entanglement, and already sits sixty lines above `render_index` in the same
  module; `_fn_reindex` already holds the `abs_root` it needs.
- **Cons:** materializes a git-derived fact into a file that is loaded into
  every session's context and refreshed only on an explicit reindex. Correct at
  write time, wrong two commits later — the same bug, arriving more slowly and
  now wearing the authority of a measurement.

### Option 2 — Drop the status field for normal pages
Delete `current`; treat an absent field as the healthy state.
- **Pros:** zero code change — the emit was already conditional on the field
  being non-empty.
- **Cons:** `current` was not purely a freshness claim. It is also the
  counterpart of `draft` in the promotion ritual, which the schema prescribes in
  three places (`draft → current` at promotion, ingest, and adopt) and on which
  a `spec` page's anchor requirements depend. Deleting the value would destroy a
  legitimate editorial axis, and an absent field conflates *deliberately
  promoted* with *someone forgot*.

### Chosen — Rename the value, suppress the unmarked state, lint the collision
Keeps the editorial axis and its required-field discipline intact, removes the
word collision at its root rather than papering over it, and makes the index
silent on freshness instead of wrong about it. `active` appears in neither
measured enum, so the two vocabularies are now disjoint.

## Consequences

- **Positive:** `INDEX.md` no longer carries ten false claims into every
  session's context. The `CLAUDE.md` warning that existed only to contradict
  those labels is deleted, shrinking the always-loaded prompt. The two enums are
  disjoint, so a reader can no longer confuse a hand-written assertion with a
  measurement.
- **Regression-proofed:** without the lint the librarian minion would helpfully
  re-add `status: current` on the next ingest, since the schema's own ingest
  step used to prescribe exactly that. `reindex --check` now fails it. Verified
  against a two-page probe: the `current` page is flagged and exits 1, the
  `active` page beside it is not — the rule discriminates rather than flagging
  any status.
- **Costs / risks:** the change had to land in **two worlds**. `Scripts/mcp-wiki.py`
  does not import the skill's CLI scripts, it vendors them by hand, so
  `reindex` / `freshness` / the frontmatter helpers each exist as near-duplicate
  bodies that must be kept in step. This ADR adds a fourth pair to that burden
  and does not address the underlying duplication. A running MCP server also
  keeps the old code until restart, so the CLI is the only fresh-code path
  immediately after an edit.
- **Fallout, found by measurement and not by review:** `search` and
  `source_to_pages` each printed a header notice when the hand-written field
  disagreed with the measured state — a signal that was meaningful only while the
  two shared a vocabulary. Making the enums disjoint means they now *always*
  differ, so the notice would have fired on every reply forever; the first search
  after the rename printed it. Both notices are removed. What they guarded is
  caught earlier and harder by the lint: a forbidden value is now rejected before
  it enters the corpus, instead of being annotated on every answer afterwards.
- **Not changed:** `status:` remains a required field. `wiki_call list` still
  renders the raw field for every page — it is an explicit query, not a
  catalogue, so showing `active` there is informative rather than misleading.
  The eight measured states are untouched.
