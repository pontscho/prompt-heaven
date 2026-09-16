# Migrating a wiki off typed numbers

A procedure for moving an existing `docs/` wiki onto measured regions, written
from the first real migration rather than from a design. Every trap below cost
something the first time.

Read `SKILL.md` first for the schema and the marker format. This document is
about the **order of operations**, because most of what goes wrong here is a
step done in the wrong order rather than a step done wrong.

## Why

A page's value is its reasoning, and reasoning does not rot. What rots is every
number typed into prose, and every number creates an upkeep obligation that no
gate enforces — so it is paid in attention instead of in CI.

That is measured, not assumed. On the repo this mechanism was built in, as of
2026-09-16: across the last twelve commits touching `docs/`, **31 of 44
work-items were re-measurement or stamp bookkeeping and 5 were new reasoning**,
with seven of the twelve adding no reasoning at all. The corpus carried **352
typed tree-measurements across 7,569 lines of prose** — one every twenty-one
lines — and for the ten densest pages, **six were numbers a script or suite in
that repo already computed**.

The same reading rounds produced **seven defects in code and config that no test
could have caught**. That is the half worth protecting, and it is why the goal
is not "write less wiki". It is: **delete the bookkeeping, keep the reading.**

Those figures are a measurement of one corpus at one moment. Measure your own
before deciding this is worth a day — the procedure's first step exists for
exactly that.

## The rule

> A number describing the tree is never typed into a page. It is a measured
> region, or it is an anchor pointing at what computes it.

Three carve-outs, each found the hard way:

- **A claim about what a page *used to say* is history, not a measurement.** It
  was true when written, no command can render it, and it must survive verbatim.
- **A frozen record keeps its dated numbers.** An ADR's figures are the
  measurement at the moment of the decision, not a claim about now.
- **A section heading keeps a typed number.** Changing a heading breaks section
  links, and the number in a heading is usually load-bearing in the argument
  rather than a tally.

## Before you start: the bootstrap

**The first migration cannot use the tool it migrates to.** The wiki server
process a session is talking to predates the mechanism you are about to add, so
`measure` and `verify` do not exist on the live tool until the server restarts —
and restarting mid-migration is not always yours to do.

Use `scripts/measure_cli.py`, which loads the server module and calls its
functions directly. It is a bootstrap, not a second implementation: it contains
no rendering or digest logic of its own, deliberately, because a hand-mirrored
copy of server logic is a cost this design already regrets elsewhere.

## The procedure

### 1. Measure your corpus before you commit to this

Count what you would be buying. If your pages carry few typed numbers, or the
numbers are ones nothing computes, the payoff is not there and the honest answer
is to stop here.

The three questions worth answering:

- how many numbers in the prose describe the *tree* rather than a value in the code;
- for the densest pages, how many of those numbers a script or suite **already** computes;
- how much of your recent documentation history was re-measurement rather than reasoning.

### 2. Pick the first page by density, not by importance

The best first subject is the page carrying the most typed measurements whose
numbers something already computes. It gives the largest return and it exercises
every part of the mechanism on one file.

### 3. Write the registry entry first — its description is the migration spec

Before touching prose, add the entry to `measurements.json`: the name, the argv,
and a description written for a person. Do this first because the description
forces you to say *which prose this replaces*, so the page edit has a written
target before it begins. The description is also echoed on every run, which
makes it the only explanation a reviewer gets of what the block means.

### 4. If nothing computes the number yet, put the command where the knowledge is

Add a census mode to **the tool that already knows** — the generator, the linter,
the suite runner. Do not write a wiki-specific script that re-derives it, or you
have created a second source of truth to keep in sync, which is the disease.

Three requirements on any command you point a region at:

- **Deterministic.** Sort every iteration over a set or a dict. The digest is
  compared against the next render, so an output whose line order wobbles turns
  the page into a permanent false positive. *Sorted*, not merely *stable on this
  machine*.
- **Renders as page content.** What lands between the markers is prose or a small
  table a human reads inside the page — not a JSON blob, not a debug dump.
- **Every number carries its subject.** A count with no subject is not a
  measurement. `111` is not an answer; `live generated regions in them: 111` is.

And one consequence to design for: **the rendered body joins the page's anchor
surface.** A command that prints a path which does not exist will gate
verification on every page that renders it. The command's standard output is
held to the same anchor contract as prose.

### 5. Sweep the page for back-references before deleting anything

This is the step most likely to be skipped and the most expensive to repair
later. Prose refers to tallies by ordinal position: *"the first two had read 70
and 84"*, *"the third read 11"*, *"those three numbers"*, *"that count"*, *"the
paging row read 5"*. Delete the tally and every one of those is orphaned.

Re-establish each referent **by name** — "the first two" becomes "live regions
and block instances" — so the historical clause still has antecedents and can
stay byte-verbatim. Do this as its own pass, before the deletion.

### 6. Check whether the page censuses itself

Migrating a page can falsify a claim that has nothing to do with numbers. The
first page migrated said the repo contained *"a second, unrelated
marker-delimited generation mechanism"*. After the migration there were three,
and the third's markers were in that very file.

**A page that documents marker-delimited generation is the likeliest page in any
repo to census itself.** Check before, not after.

### 7. Insert the markers

Open a region by hand with an empty digest. Copy this literally:

```markdown
<!-- BEGIN MEASURED: your-entry-name -->
<!-- END MEASURED:  -->
```

**The empty END takes two spaces.** One space is a hard refusal. It is the
easiest thing in this mechanism to mistype, it is invisible in every rendered
view of the page, and the refusal only arrives when someone runs `measure`. The
loudness is deliberate — a near miss is an error, not a skip, because a silently
skipped region is a region nobody updates again.

Put each region **where its numbers are argued**, not in a block at the end. A
region is a contiguous line span, so two facts fifty lines apart are two regions.
That is usually the right answer anyway: bodies that move on different events
should have different digests, or the diff stops naming which fact changed.

### 8. Render: check → write → re-check

`measure` with no write reports what would change and writes nothing. Then write.
Then re-check: it must report `ok` and the page must be **byte-identical** to
what the write produced.

Polishing prose **outside** the markers afterwards is free — the digest covers
the emitted body alone. The inverse is the trap: tidying a blank line before the
END marker, or re-wrapping a rendered table, is a **hand edit**. It will be
refused, not overwritten, until someone passes force. That refusal is the
mechanism working.

### 9. If the page shows a marker as an example, prove it is inert

A clean `measure` does **not** prove this. A broken fence rule would not raise —
it would quietly treat the example as a third region and render into it.

Ask the scanner directly: list the page's regions with their fenced flag
(`measure_cli.py` has the diagnostic). Every specimen line must report fenced,
and the live region count must be exactly what you inserted.

Inertness rules, in short: a marker is only a marker as the **whole line at
column zero**, and a fenced code block is inert. The cost is that a measured
region cannot live inside a list item. The declared hole is that HTML comments do
not nest, so a marker at column zero inside a multi-line comment is **not** inert.

### 10. Verify, then stamp — and only then

Run verification over the whole corpus and compare the anchor numbers before and
after. New anchors are expected: they come from the rendered body.

**`not-rendered` is not a defect and does not gate.** The read-only path cannot
re-render, because rendering executes commands, so it reports what it can prove
without executing. A reviewer who reads `not-rendered` as breakage will "fix" a
working page. Run verification with rendering enabled for the `ok` verdict.

Bump `verified:` only when the page **and** the code its regions render from are
both in HEAD. A stamp claims someone checked this page against that commit;
setting it on an uncommitted tree is a false claim in the one field that may not
carry one.

## What not to migrate

- Anything with no registry entry. **Do not invent an entry mid-migration** to
  catch one more number — that is a second change riding along inside the first.
  Leave it typed, name it in your report, migrate it deliberately later.
- The three carve-outs above: history, frozen records, headings.
- A number that is genuinely only knowable by a human reading the tree. Those
  exist. Leave them and say so.

## The trust boundary

Rendering runs commands named by a file in the repository. That is the same trust
level as a build config, and it is fenced accordingly: rendering is reachable
**only** from an explicit refresh call, never from any read path — not search,
not page fetch, not listing, not freshness, not reindex.

If you extend this mechanism, keep that boundary structural rather than
conventional, and assert it in **both** directions: no read path may reach the
spawn, *and* the granted paths must. A one-directional assertion goes green when
someone deletes the mechanism.

## What the first migration actually cost

One page, 414 lines, roughly forty-two typed measurements. Twenty-six lines of
tally deleted, two regions added, every reason on the page kept unreworded, two
sentences re-hung on named referents, one self-census claim repaired, three
measurements deliberately left typed because no entry rendered them.

The parts that took the longest were steps 5 and 6 — the back-reference sweep
and the self-census check. Neither is about the mechanism at all. Both are about
the fact that prose written around a number **points at it**, and moving the
number without moving the pointers leaves a page that parses and lies.
