---
name: 0018-the-totals-must-describe-the-scope
type: adr
status: active
title: The totals must describe the scope the caller asked for
description: Decision to apply a report filter at the point the totals are computed rather than to the rendered rows, with the self-contradicting report that measured it, the cost half that agrees with it, the five alternatives rejected, the empty-scope refusal that is 0017's rule rather than a new one, and the prefix-is-not-a-boundary gap left declared.
sources:
  - Scripts/mcp-wiki.py
  - tests/test_wiki_recall.py
verified:
  commit: 35f4a89
  date: 2026-09-16
links:
  - 0017-a-silent-zero-is-the-defect
  - 0002-index-claims-no-freshness
  - wiki-engine
  - scripts
---

# ADR 0018: The totals must describe the scope

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is `Scripts/mcp-wiki.py` and [[wiki-engine]].

## Context

`wiki_call freshness` renders four things, and only the first is a list: the
rows naming each page and its git-measured state, a header, a one-line
`summary`, and the `ok:` and `gating:` verdict lines. The rows are what a reader
looks at. The other three are what a reader *quotes*.

A caller asked for `path_prefix`, wanting the freshness of one subtree. The
parameter did not exist, and the refusal was correct — `freshness` accepted
`head`, `root` and `max_answer_chars` and nothing else. Adding it looked like a
rendering concern, because the visible half of the report is a list of pages and
the obvious implementation is to drop the rows that do not match.

That implementation is wrong, and the reason is a derivation chain rather than a
preference. `summary` is derived from the page set, and `ok:` and `gating:` are
derived from `summary`. Filter the rows and those three lines keep counting the
whole corpus while the list above them shows a slice — a report that
contradicts itself on one screen, where the contradiction is invisible to the
reader who pasted it somewhere. On the live corpus at the time of this decision
the gap is not subtle: 31 pages with `gating: 19`, against a `subsystems/` slice
of 5 pages whose true figures are `stale (3)`, `ok: 2 current`, `gating: 3`. The
suite's synthetic case is built on the sharper end of the same gap, selecting a
slice with **zero** gating pages out of a corpus with four
`tests/test_wiki_recall.py`.

The cost half arrives at the same answer independently. Classifying a page is
the expensive step — one `git diff` per distinct `verified.commit` — so a page
the caller has excluded should not be paid for at all. When the honest answer
and the cheap answer agree, the remaining question is only where the earliest
honest point is.

## Decision

**A filter is applied where the totals are computed, not where the rows are
rendered.** Concretely: `path_prefix` reaches `freshness_analyze` and guards the
`iter_pages` comprehension `Scripts/mcp-wiki.py:freshness_analyze`, which is the
first point in the pipeline that knows a page's docs-relative path. Everything
downstream — `summary`, `ok:`, `gating:`, the header count — is then derived
from the set the caller actually asked about, with no line in the renderer aware
that a filter happened.

Two properties fall out of that placement and are deliberate:

- The report dict gains `path_prefix` **only when there is one**, so an
  unfiltered report is dict-for-dict what it always was, and the suite's
  byte-identity case for the unfiltered rendering keeps its meaning.
- The header states the scope it describes, so a pasted report cannot be
  mistaken for a corpus-wide one.

**A prefix that selects nothing is a refusal, not a report.** This half is *not*
a new decision and is recorded here only because it is where the rule lands:
[[0017-a-silent-zero-is-the-defect]] already holds that a silent zero is the
defect. A well-formed report reading `gating: 0` over a set nobody looked at
says *nothing is stale* — the strongest possible clean bill of health, issued
for an empty scope. The refusal names what was actually checked, which is
nothing.

## Alternatives Evaluated

### Option 1 — Filter the rendered rows
- **Pros:** the smallest possible diff; one function touched; no signature
  change; the visible half of the report is immediately right.
- **Cons:** the three derived lines keep describing the corpus. This is the
  measured defect above, and it is worse than no filter at all, because a wrong
  report is quotable while a refusal is not.

### Option 2 — Filter the rows and recompute the totals in the renderer
- **Pros:** keeps the analyser's signature untouched.
- **Cons:** two places would then know how `summary` is derived, and the
  renderer would have to re-implement a derivation it currently only formats —
  the classic second copy that agrees until it does not. The cost half is also
  untouched: every excluded page is still classified, `git diff` and all.

### Option 3 — Overload `root` as the scope
- **Pros:** no new parameter; `root` already takes a path.
- **Cons:** `root` is a *wiki-root override* — it re-points what the corpus is,
  through `safe_path`, with its own "wiki root not found" failure. Narrowing the
  root does not narrow a report over one corpus; it declares a different corpus,
  which changes what slug resolution, link checking and the orphan audit are
  computed against. One key would carry two meanings, which is the collision
  [[0002-index-claims-no-freshness]] and the fleet's alias rule both refuse in
  their own domains.

### Option 4 — Let the caller filter the output
- **Pros:** zero server change; the caller already has the text.
- **Cons:** the caller pays the full classification cost for every page it is
  about to discard, the totals still describe the corpus, and a large corpus can
  push the page the caller wanted past the output ceiling before the caller ever
  sees it.

### Option 5 — A separate `freshness_subtree` function
- **Pros:** the unfiltered path is provably untouched.
- **Cons:** a second function with the same body and one extra parameter,
  doubling the surface the model has to know in order to ask one question. The
  fleet's direction is the opposite one: fewer names, more spellings that reach
  them.

### Rejected non-option — leave the refusal standing
This is what the session measured: the caller asked a reasonable question, got a
correct refusal, and the answer it wanted was a few lines away. A refusal that is
right about the vocabulary and wrong about the intent costs a round trip every
time it is issued.

## Consequences

- **Positive:** `search`, `list` and `freshness` now spell the same scope the
  same way, with the same aliases, so the spelling a caller learns on one
  transfers to the other two. The header carries the scope, so the report
  cannot be quoted as more than it is.
- **Paid for:** `freshness_analyze` grew a third parameter. Its existing
  two-argument callers, including the suite's byte-identity baseline, are
  unaffected by construction.
- **Known gap, declared rather than fixed:** the filter is `str.startswith` on
  the docs-relative path, copied verbatim from `search` and `list` rather than
  diverging from them. A prefix is therefore not a path boundary: `sub` selects
  `subsystems/`, and a future directory sharing a leading substring with another
  would be silently merged into it. This repo has already paid for exactly this
  shape once, in the test harness, where a `startswith` check on path names ate
  `.gitignore` because it starts with `.git` — the fix there was to match whole
  path components `tests/_harness.py:_is_scratch_dir`. It is not fixed here
  because it is not one function's bug: correcting `freshness` alone would make
  the three filters disagree about what a prefix means, which is a worse defect
  than the one it repairs. The three moving together is a separate decision.
- **Unresolved:** the empty-scope refusal does not name the prefixes that would
  have matched, where the comparable refusal in `purity_call` builds its
  suggestion from the set it is refusing against. Listing 31 page paths would be
  noise, but the top-level directories are few and cheap. Nobody has argued it
  either way yet.

## Update — both open items are closed in the commit carrying this note

Appended rather than rewritten: the decision above stands as taken, and this
records what happened to the two things it left open.

**The separate decision got made.** The known gap said the three filters must
move together and that moving them was not this decision's business. It was put
to the reader as an open item, and the reader's answer was to ask why it was
still open — which is the right answer, because it was never a decision. Three
call sites carrying one copy-pasted rule is one repair, and framing a repair as
a decision is how work gets parked.

The rule is now written once and used by all three
`Scripts/mcp-wiki.py:_path_prefix_matches`, in four clauses: an exact path
matches; a prefix ending at a component boundary matches everything under it, so
`adr` and `adr/` behave alike; a prefix ending **inside the final component**
matches, which keeps `adr/001` selecting the 0010–0019 records — a real spelling
that a component-only rule would have taken away; and a prefix ending inside any
**earlier** component matches nothing, so `sub` no longer selects `subsystems/`.
The empty-scope refusal now names the scopes that do exist, built from the
corpus the walk just yielded `Scripts/mcp-wiki.py:_corpus_scopes`, which closes
the unresolved item above on the terms it asked for.

**The half worth keeping is why the gap survived.** No existing case in the
suite encoded the old behaviour, and that is not luck: the substring bug needs
two directories whose names share a leading run, and **every fixture in the file
was flat**. A path with no separator has one component, and a one-component path
cannot demonstrate a rule about ending inside an earlier one. The suite could
not have caught this defect at any point in its history, and the new group's
fixture is the file's first tree. The clause pinning the negative case was
checked against a deliberately reverted predicate rather than assumed to bite —
`sub` selects four pages across both directories with the old rule, and the case
reports three problems.

Three statements of the old rule were found and repaired as part of this, two in
the suite and one in a comment beside the alias table. That is the cost of
writing an implementation detail into prose: the rule had four homes and only
one of them was executable.

## Update 2 — the Decision named a shape, and the shape changed

Found by a reader auditing this page against the code, and appended rather than
repaired in place.

The Decision above says the filter guards the `iter_pages` **comprehension**.
There is no comprehension any more: the same commit that closed the gap
materializes the walk and filters in an explicit loop, because the refusal has
to name the scopes that do exist and therefore needs the paths the filter
rejected — which a comprehension throws away. The code states that reason where
it is `Scripts/mcp-wiki.py:freshness_analyze`.

The decision itself is intact. The filter still runs before `_classify_page`,
which is where the totals are computed, and that position is the whole of what
was decided. What went stale is one word.

It is recorded because the failure is this page's own, and it is the same one
the page criticises two paragraphs above. A decision record named an
implementation **shape** where it only had to name a **position**. The shape
belongs to the code and may change whenever the code has a reason; the position
is the decision and may not. Every shape-word in a frozen record is a claim that
must eventually age, and this one aged in the very commit that closed the gap
the record had declared.

One claim in the Decision was checked in the same audit and survives: an
unfiltered report is still dict-for-dict what it always was. The report dict now
gains a second conditional key, `scopes`, but only inside the prefix branch and
only when the selection came back empty, so neither the unfiltered guarantee nor
the suite's byte-identity case is touched.
