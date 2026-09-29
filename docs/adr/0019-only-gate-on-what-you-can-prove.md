---
name: 0019-only-gate-on-what-you-can-prove
type: adr
status: active
title: The wiki may only gate on what it can prove
description: Decision to split the wiki's freshness report into a provable verdict and an advisory measurement, and to render a tree-describing number rather than type it, with the upkeep census that measured the problem, the finding it refused to give up, the six alternatives rejected, the three carve-outs, and the gate that was found carrying the disease it exists to cure.
sources:
  - Scripts/mcp-wiki.py
  - Scripts/amalgamate.py
  - tests/test_wiki_recall.py
  - docs/measurements.json
verified:
  commit: baa3a68
  date: 2026-09-16
links:
  - 0002-index-claims-no-freshness
  - 0018-the-totals-must-describe-the-scope
  - wiki-engine
  - generated-regions
---

# ADR 0019: Only gate on what you can prove

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is `Scripts/mcp-wiki.py`, [[wiki-engine]] and the migration
procedure shipped beside the skill.

## Context

The complaint that started this was that the wiki costs too much to keep
correct. It was put as a judgement, and it was answered with a census rather
than an opinion, because the same complaint has two incompatible remedies
depending on what the cost actually is.

Measured on this corpus at the time of the decision: across the last twelve
commits touching `docs/`, **31 of 44 work-items were re-measurement or stamp and
index bookkeeping, and 5 were new reasoning**; seven of those twelve commits
added no reasoning at all. The prose carried **352 typed tree-measurements
across 7,569 lines**, one every twenty-one lines, and for the ten densest pages
**six were numbers a script or suite in this repo already computes**, two more
computable by a script nobody had wired to a page. Meanwhile **22 of 32 pages
were gating, at a median lag of 61 commits**.

Two findings cut against the obvious conclusion, and both had to be kept.

**The discipline was working.** Of 24 typed numbers spot-checked against the
tree, **only 2 were wrong** — and both sat in one sentence on one page that the
wiki's own gate already labelled stale. Whatever this costs, it is not buying
inaccuracy.

**The re-reading is what finds defects.** The same rounds that produced the
bookkeeping produced **seven findings in code and config that no test could have
caught**: six servers silently discarding a traceback, a forge target
description a model reads *before* choosing a test target, a `$HOME` boundary
claimed in five places and binding in none, a roster missing a whole suite while
the fleet reported all green. Each was found by a human or agent reading a page
against the code — never by a gate.

So the cost is real and the yield is real, and they come from different halves of
the same activity. The problem is not that the wiki is maintained; it is *what*
is being maintained.

Underneath both sits one defect. **Git lag cannot tell a moved comma from a
reversed decision.** A page gates because a file it lists moved, which is a
measurement of the repository and not a judgement about the page — and calling
it `gating` asked a human to adjudicate 22 pages on evidence that could not
distinguish the two cases. That is the same over-claim
[[0002-index-claims-no-freshness]] removed from the generated index, one layer
further in than that decision reached.

## Decision

**The wiki gates only on what something can prove, and a number describing the
tree is not typed.** Two halves of one rule.

**Half one — the verdict.** `gating` becomes what verification can demonstrate:
a broken anchor, or a measured region whose body no longer matches its digest
`Scripts/mcp-wiki.py:verify_analyze`. Git lag keeps every figure it published,
page by page, under the same status names, and moves to an `advisory:` line that
says in its own words that a human read may be owed and that nothing is being
claimed wrong `Scripts/mcp-wiki.py:freshness_render`. Nothing stops being
measured; one line stops calling itself a verdict.

**Half two — the numbers.** A number describing the tree is a **measured
region** — a body rendered from a command, digest in the closing marker, hand
edits refused — or it is an **anchor** pointing at whatever computes it. It is
not typed. The registry maps a name to a command `docs/measurements.json`, and
the command belongs in the tool that already knows the answer
`Scripts/amalgamate.py:census_fleet`, never in a wiki-specific re-derivation.

Three carve-outs, each measured on a real page rather than reasoned:

- **History is not measurement.** "The first two had read 70 and 84 since before
  the logging source existed" is a claim about what a page *used to say*. It was
  true when written, no command can render it, and it survives verbatim.
- **A frozen record keeps its dated numbers.** An ADR's figures are the
  measurement at the moment of the decision. [[generated-regions]] already made
  this distinction explicitly for the figures in 0014.
- **A heading keeps a typed number**, because changing a heading breaks section
  links and the number there is load-bearing in the argument.

## Alternatives Evaluated

### Option 1 — Type the numbers, add a suite that checks them
- **Pros:** no new mechanism; the repo already gates typed counts this way in
  `tests/run.py`, where a suite's declared case count is asserted against the run.
- **Cons:** an oracle per number, each hand-written, each a second implementation
  of the thing it checks. It costs what the rendering mechanism costs and stops
  one step short: the number is still typed, so it must still be *edited* by a
  human who has now been told by a red gate that it moved. Rendering removes the
  edit, not just the error.

### Option 2 — Delete every number, cite the source instead
- **Pros:** the cheapest possible rule, nothing to build.
- **Cons:** it makes the reader leave the page to learn what the page is about.
  Kept as the **fallback** for numbers no command produces, which is what the
  anchor half of the rule is, but a rule that answers every question with a
  pointer is a table of contents rather than a wiki.

### Option 3 — Generate whole pages from the tree
- **Pros:** nothing to maintain, ever.
- **Cons:** deletes the reasoning, which is the only part that cannot be
  re-derived and the only part the census found worth the money. This is the
  remedy that would answer the complaint by destroying the asset.

### Option 4 — Keep gating on git lag and work harder at it
- **Pros:** no change; the existing labels stay meaningful to anyone already
  reading them.
- **Cons:** measured refutation — 22 of 32 pages gating at a median 61 commits
  says the work is not being done, and a gate nobody can clear teaches its reader
  to ignore it. The failure mode is not neglect, it is that the gate asks for a
  judgement it cannot supply evidence for.

### Option 5 — Drop freshness entirely
- **Pros:** removes the whole treadmill at a stroke.
- **Cons:** "a source this page cites has moved" is genuinely worth knowing, and
  it is the one thing git *can* prove. The problem was never the measurement.

### Option 6 — A second stamp: "numbers reviewed at"
- **Pros:** keeps typed numbers and dates them honestly.
- **Cons:** a second hand-maintained field with the same rot as the first, and
  now two stamps that can disagree about the same page.

## Consequences

- **Positive:** on this corpus the report went from 22 gating to **3 gating plus
  20 advisory**, and a gated page now names a defect somebody can act on rather
  than a commit range they must adjudicate. The three it names are real.
- **Paid for:** rendering runs commands a repository file names. The boundary is
  structural rather than conventional — one spawn site, a capability argument
  with no default, and read paths that never hold a rendered body — and the suite
  asserts it **in both directions**, because a one-directional assertion goes
  green the moment someone deletes the mechanism
  `tests/test_wiki_recall.py`.
- **Known gap — the read path under-reports region staleness, by construction.**
  Proving a region stale requires rendering, and rendering is execution, so the
  read-only answer covers only what needs no subprocess: hand-edited,
  unregistered, malformed, never rendered. Verification says which regions it
  did not check, and the caller opts in.
- **Known gap — the anchor matcher reports a residue it cannot remove.** A
  frontmatter source is a claim and is checked as written; a path-shaped span in
  a body is a guess and must begin at a real repository entry to count. That took
  the first run's 30 unresolved anchors to 4. What survives is the illustrative
  path under a real top-level directory, which is indistinguishable by shape from
  a dead one and is reported.
- **A new obligation on anything that computes a number for a page:**
  deterministic output, sorted rather than merely stable, because the digest is
  compared against the next render and a wobbling line order makes a permanent
  false positive.
- **The gate was found carrying the disease it cures.** The census's own negative
  control — seven mutants of a copy of the generator — showed that a region count
  **typed as a literal** was invisible to it. The mechanism built to delete typed
  numbers had one inside its own verifier. It is closed by re-rendering over a
  subset and requiring every headline number to move with its input.
- **Unresolved:** the overwhelming majority of those 352 numbers are still typed.
  Migration is per page and deliberate; the procedure and its traps are written
  down beside the skill, because the two steps that cost the most on the first
  page were not about the mechanism at all — prose points at a number, and moving
  the number without moving the pointers leaves a page that parses and lies.

## Addendum (2026-09-28): the CLI takes up half one

Half one was implemented in the server only. `ClaudeCode/skills/wiki/scripts/freshness.py` kept the pre-decision exit code, stale plus orphaned-source plus unverified, while the wiki skill said the CI gate ran the same logic. Found while fixing roadmap item R-0025, closed as R-0032.

The CLI now exits non-zero only on an `orphaned-source` page: a `sources:` path gone from the tree on a page whose `verified.commit` resolves. That is a filesystem fact and the same broken anchor the server's `gating:` line counts, so it stays on the verdict side. `stale` and `unverified` are listed and counted on an `advisory:` line in the server's own wording, whatever the page's `status:`. R-0025's draft exemption was folded into that rule and removed.

A clean CLI exit is a declared subset of the server's verdict. Symbol anchors, body anchors and measured regions are checked only by `wiki_call verify`: carrying them into the CLI would copy the verifier and the measured-region stack, the duplication roadmap item R-0002 tracks. A missing source path on a page with no resolvable `verified.commit` classifies as `unverified`, so it is advisory in the CLI; `verify` still catches it. On the live corpus the day this landed the CLI reported gating 0 and 28 advisory pages, where the old code would have exited 1.

## Addendum (2026-09-29): a frozen record does not gate on a dead body anchor

Half one had one case it could not honour. `verify` gated on every dead inline anchor, and three pages were red for that reason alone: ADR 0017, ADR 0022 and the deprecated sandbox-run spec. Each one's dead spans were correct when the page was written. Found while triaging roadmap item R-0036.

The new rule is `Scripts/mcp-wiki.py:frozen_record`. A frozen record is an accepted ADR (`type: adr`, `status: active`) or any page with `status: deprecated`. A body span on a frozen record that does not resolve is now reported as an advisory, under a `frozen records` block in `verify` and a second `advisory:` line in `freshness`, and it is not counted in `gating:`. It is still counted as unresolved on the `anchors:` line. Both answers read the same report, so the two gating numbers still agree.

There are two reasons. First, the schema freezes an adr at decision time and makes it append-only (§2), and §4's argument that an ADR keeps its dated numbers applies to its anchors too: they quote the tree as it was when the decision was taken, and are not a claim about HEAD. Second, the only legal write to an accepted ADR is `ClaudeCode/skills/wiki/scripts/addendum.py`, which only appends, so it can never remove a body span. Gating on such a span would leave a page red forever, with no sanctioned action that clears it. That breaks this ADR's own rule: a gate must name a defect somebody can act on. A deprecated page has the same shape, because it quotes a design that is gone.

These still gate on every page, frozen or not: a dead frontmatter `sources:` entry, which is a declared claim and not a quotation, and every measured-region defect. Draft ADRs and every page of any other type keep gating on their body spans as before. `tests/test_wiki_recall.py` group S pins one page per clause, and all five cases were run red before the change.

Three alternatives were rejected. The first was a sanctioned body edit that removes the backticks from a dead anchor: it is the smallest diff, but it breaks append-only, and it would be the first exception to the rule that addendum.py makes the only write. The second was a list of retired anchors declared in an addendum: it needs new syntax and a parser to get exactly what the type-and-status test already gets. The third was leaving the pages red: a gate that no sanctioned action can clear is noise, and it trains readers to ignore `gating:`.

The cost is that when an anchor on a frozen record dies, nothing forces anyone to look at it any more. It stays visible, one advisory line in `freshness` and one named row per span in `verify`, but it is no longer a verdict. `freshness.py` is not affected: it gates only on `orphaned-source`, which is a `sources:` fact, and never reads body spans. On the live corpus the day this landed, gating went from 3 to 0, and the advisory listed 5 dead body anchors on those three pages.
