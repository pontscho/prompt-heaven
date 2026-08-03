---
name: 0003-the-trigger-travels-with-the-tool
type: adr
status: active
title: The wiki trigger lives in the tool description, not in CLAUDE.md
description: Decision to move the wiki-consultation trigger into the wiki_call tool description so it travels with the plugin, leaving CLAUDE.md only what is project-specific.
sources:
  - Scripts/mcp-wiki.py
  - CLAUDE.md
verified:
  commit: e4cdd48
  date: 2026-08-03
links:
  - wiki-engine
---

# ADR 0003: The trigger travels with the tool

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the `wiki_call` description itself and [[wiki-engine]].

## Context

A knowledge base whose contents nobody knows does not get consulted, and for
three sessions nothing named this one.

That is measured, not assumed. Three sessions went into making `search` answer
well — a calibrated relevance gate, query stopwords, section slices, line
windows, git-measured freshness labels, a type ranking signal, an alias synonym
field. The answers got much better and the wiki still went unasked. In the very
session that shipped the last of those, **zero** wiki calls were made while the
wiki's own server code was being read all day, and the `search` memoization
model — which [[wiki-engine]] describes — was re-derived from source instead.
The hypothesis that answer quality was the blocker is therefore falsified: the
quality improved and the call rate stayed at nothing.

The fix at the time was a project-root `CLAUDE.md` section naming the wiki as the
WHY store, with a sharp trigger rather than "always ask". It worked — the trigger
fired unprompted three times in the next session, twice getting a gate refusal
and correctly moving on.

But it only works **here**. `ClaudeCode/` is a plugin that travels to every
project; a project-root `CLAUDE.md` does not. Every other route in this
fleet — code search, C/C++ symbols, build, test, git, file writes — carries its
categorical rule inside the MCP tool description, which is loaded on every
request in every project. The wiki was the one exception, and it was the one
that went unasked.

One counter-argument had to be answered before moving it, because the tool
description **already contained a directive** and that directive had already
failed: `PREFER THIS over Bash grep/find over docs/`. It was present throughout
the measured session. The reason it did not fire is that it answers a different
question. It is a ROUTING rule — how to read the wiki once you have decided to
touch `docs/` — and the observed failure was upstream of any tool choice: the
task was framed as "read the source", so no docs-related tool was ever
considered. A routing rule cannot pre-empt a reflex that decides before routing
begins. What was missing was a TRIGGER: a named class of question that obliges
the call.

## Decision

The trigger moves into the `wiki_call` tool description `Scripts/mcp-wiki.py`,
written as a class-of-question rule rather than a Bash prohibition, because
nothing here is forbidden — the failure mode is not reaching for the wrong tool,
it is reaching for no tool at all.

`CLAUDE.md` keeps only what a tool description cannot know: that `docs/` is
*this* repo's wiki, the `@docs/INDEX.md` catalogue, and the note that the index
claims no freshness ([[0002-index-claims-no-freshness]]). It drops from 42 lines
to 22.

## Alternatives Evaluated

### Option 1 — Leave it in CLAUDE.md only
- **Pros:** measured to work; read as a standing project instruction, which is
  the right register for "reframe your task", and stronger than tool metadata.
- **Cons:** does not travel. This repo is the plugin's source, so a rule that
  works only in the repo that authors it is the one place it is least needed.

### Option 2 — Keep both copies
- **Pros:** repetition may be what makes a rule stick; the two are read in
  different registers and might reinforce.
- **Cons:** 21 duplicated lines, paid on every request of this project, for one
  rule. Unmeasurable benefit against a measurable cost.

### Chosen — Trigger in the description, project binding in CLAUDE.md
Splits the rule along the line that actually divides it: the *class of question*
is universal and belongs with the tool; *which wiki, and what is in it* is
project-specific and belongs in the project file.

## Consequences

- **Positive:** the trigger now reaches every project the plugin is installed
  in, and `CLAUDE.md` stops paying twice. The rule sits beside the tool that
  satisfies it, matching how every other route in this fleet is already
  specified.
- **Paid for, not added:** the description grew 4340 → 5448 chars (+1108). The
  same change deleted 18 decorative rule lines — twelve in `forge_call`, six in
  `purity_call` — 1296 characters of repeated `=` and `═` carrying no
  information. Net fleet change: **−188 chars**, for a trigger that did not
  previously exist anywhere. The `chars/4` token estimate understates the
  saving: the purity rules are U+2550, three bytes each, and box glyphs do not
  compress the way an ASCII run does.
- **Two numbers in `e4cdd48`'s commit message are wrong**, and are corrected
  here rather than left to be quoted as fact: it says CLAUDE.md dropped "44 ->
  16" lines (measured: 42 → 22, cross-checked against the hunk header
  `@@ -1,36 +1,16 @@` plus a six-line unchanged tail), and it prices the rule
  deletion at "1440 characters" while enumerating twelve plus six plus two —
  eighteen lines were deleted, twenty were counted. The two rules written into
  the new banner were taken out again before it shipped, so they never reached
  the tree. Both errors are the same shape as the one this work removed: a
  hand-written number that reads as a measurement.
- **Unresolved, and stated rather than hidden:** it is not known whether the
  CLAUDE.md copy was load-bearing. Removing it may weaken the trigger in this
  repo even as it strengthens it everywhere else. No cheap measurement exists —
  the signal is a call rate over whole sessions — so this is a judgement, not a
  result. If the wiki starts going unasked here again, this is the first thing
  to reverse.
- **Not changed:** the `PREFER THIS over Bash grep/find over docs/` routing
  clause stays. It was never wrong, only insufficient, and it still answers the
  question it was written for.
