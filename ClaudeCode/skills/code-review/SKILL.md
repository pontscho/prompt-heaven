---
name: code-review
description: >-
  Review code quality of files or directories via a multi-agent Find→Verify→Synthesize fan-out — one specialized lens finder per angle, an independent verifier per candidate, then a ranked/capped, finding-derived scored report. Use for code quality assessment, refactoring decisions, or learning unfamiliar code. Trigger: /p:code-review <file-or-directory>
---

# Code Review

Review code quality of a file or directory with a **multi-agent fan-out**: specialized lens
finders surface candidates in parallel, an independent verifier re-judges each one (breaking
single-pass anchoring bias), then the skill body ranks, caps, and renders a hybrid report
(ranked findings + finding-derived 1–8 dimension scoring + ASCII bars + verdict).

This is a **linear 4-Step pipeline orchestrated from this skill body** (Step 1 Scope → Step 2
Find → Step 3 Verify → Step 4 Synthesize). It is NOT a validation loop — see
`ClaudeCode/ARCHITECTURE.md` on the "Step N" vs "Phase A/B/C" distinction.

> **Single source of truth.** The 8 lens texts, the recall verdict-ladder, the candidate/verdict
> schemas, the lens→dimension map, and the scoring weights live in
> `ClaudeCode/skills/_lib/code-review-lenses.md`. This skill references them and copies each lens
> body into the finder prompt at runtime — it does NOT restate lens prose.

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| target | (required) | File or directory path |
| --output | console | `console`, `markdown`, or `both` |
| --severity | all | Minimum displayed severity: `high`, `medium`, `low` (filters the DISPLAYED list only; the score always uses ALL verified findings) |
| --depth | 3 | Max directory depth for recursive scan |

**`--severity` vocabulary:** canonical values are `high \| medium \| low`. The prior values are
accepted as **aliases** for backward compatibility: `critical→high`, `warning→medium`,
`info→low`. (The old monolith used `critical \| warning \| info`.)

**Out of scope (this round):** `--fix` (apply findings to the working tree) and `--comment` (post
to a PR) are intentionally NOT implemented — this skill is strictly **read-only**. The CC effort
matrix (`low…max`) and the Sweep phase are also out of scope: this skill runs one fixed
recall-biased configuration (8 lenses, ≤6 candidates each, 1-vote recall verify, cap 12).

## Instructions

### Step 1 — Scope

1. Resolve `target`. If a directory, list code files recursively up to `--depth`.
2. Filter out non-code files:
   - Skip dirs: `node_modules`, `vendor`, `dist`, `build`, `.git`.
   - Skip binaries, images, fonts.
   - Skip tests/fixtures: `test/`, `spec/`, `__tests__/`, `*_test.*`, `*.test.*`, `fixtures/`,
     `testdata/`.
3. Detect language(s) by extension.
4. Gather the applicable CLAUDE.md files (user-level `~/.claude/CLAUDE.md`, repo-root `CLAUDE.md`,
   plus any `CLAUDE.md`/`CLAUDE.local.md` in a directory that is an ancestor of a target file) so
   the conventions lens can cite exact rules.
5. Build a **scope block** — a reusable text snippet passed to every finder and verifier:
   ```
   ## Review scope
   Target: <path>   (files: <N>, language(s): <…>)
   Files:
     - <file>
     - …
   Applicable CLAUDE.md:
     - <path>  (or "(none)")
   ## What changed / under review
   <one-paragraph summary>
   ## Conventions
   <notes, or "(none noted)">
   ```
6. Emit ONE compact status line: `Step 1: SCOPE — <N> files (<lang>), <M> CLAUDE.md`.

If the target has no reviewable code files, stop and report "No code to review."

### Step 2 — Find (parallel lens finders, ONE message)

Fan out **all 8 lenses** from `_lib/code-review-lenses.md` § Lens Catalog. In a **single message**,
issue 8 parallel `Agent` calls (so the harness runs them concurrently — the fan-out mechanic from
`_lib/validation-loop.md` PL.1):

- `subagent_type`: `p:minion-code-reviewer`
- `description`: `"find: <lens-key>"`
- `prompt`:
  ```
  LENS: <lens-key>
  <the lens body, copied verbatim from _lib/code-review-lenses.md>

  <scope block from Step 1>

  <the § Candidate Schema + the recall "surface up to 6 / do not self-censor" instruction, from _lib>
  ```

Collect each finder's candidates block. Emit ONE compact status: `Step 2: FIND — 8 lenses → C candidates`.

### Step 3 — Verify (dedup → budget → parallel verifiers, ONE message)

1. **Dedup** near-identical candidates across lenses: same `file:line` + same mechanism → keep
   the one with the most concrete `failure_scenario`.
2. **Bound the fan-out.** If the deduped candidate count exceeds `VERIFY_BUDGET = 24`, truncate to
   24, correctness-class first (so the verifier count is hard-capped; worst-case Agent count for a
   review = `8 + 24`). If you truncate, say so in the status line — never silently drop.
3. In a **single message**, issue one `Agent` call per surviving candidate:
   - `subagent_type`: `p:minion-code-verifier`
   - `description`: `"verify: <file-basename>"`
   - `prompt`:
     ```
     <scope block from Step 1>

     ## Candidate finding
     File: <file>[:<line>]
     Summary: <summary>
     Failure scenario: <failure_scenario>

     <the § Verdict Ladder (recall), copied verbatim from _lib/code-review-lenses.md>
     ```
4. Keep candidates whose verdict is **CONFIRMED** or **PLAUSIBLE**; drop **REFUTED**.
5. Emit ONE compact status: `Step 3: VERIFY — C candidates → V kept (R refuted)`.

### Step 4 — Synthesize (skill body, no agent)

1. **Dedup by root cause**: when several kept findings describe the same defect, keep one
   representative; if any merged member is CONFIRMED, the representative is CONFIRMED.
2. **Rank** (lower = higher priority, from `_lib` P-5): `rank = (class=="quality" ? 2 : 0) +
   (verdict=="PLAUSIBLE" ? 1 : 0)` → correctness-CONFIRMED (0) < correctness-PLAUSIBLE (1) <
   quality-CONFIRMED (2) < quality-PLAUSIBLE (3). Correctness always outranks quality on the cut.
3. **Cap** at **12** findings (drop the lowest-priority beyond the cap; never silently drop while
   there is room).
4. **Derive severity** per finding and **derive the 5 dimension scores** from ALL verified
   findings, using `_lib/code-review-lenses.md` § Scoring Derivation (start at 8; deduct
   `base(class) × multiplier(verdict)`; floor 1; aggregate = half-up mean of the 5 raw dimension
   values). Severity: correctness+CONFIRMED → HIGH · correctness+PLAUSIBLE → MEDIUM ·
   quality+CONFIRMED → MEDIUM · quality+PLAUSIBLE → LOW. The final **Quality verdict** then applies
   the **severity floor** (any HIGH finding ⇒ band capped at NEEDS IMPROVEMENT — see Quality verdict
   below).
5. **Apply `--severity`** to the DISPLAYED findings list only — the scores above already used all
   verified findings.
6. Render the console report (below). If `--output` includes `markdown`, also write the report
   file (Step 5 layout); create `docs/reviews/` if it does not exist.

#### Console output format

```
p:code-review

Target: <path>
Files:  <N> (<language>)
Lines:  <total>

----------------------------------------

Quality Metrics:

  Correctness:          [######--] 6/8
  Consistency:          [#######-] 7/8
  Reuse/DRY:            [########] 8/8
  Simplicity/Altitude:  [#####---] 5/8
  Conventions:          [#######-] 7/8

  Overall:              <overall>/8

----------------------------------------

HIGH [<count>]

  [<file>:<line>] <summary>  (<verdict>)
  <failure_scenario>

MEDIUM [<count>]

  [<file>:<line>] <summary>  (<verdict>)
  <failure_scenario>

LOW [<count>]

  [<file>:<line>] <summary>  (<verdict>)
  <failure_scenario>

----------------------------------------

Summary: <H> high, <M> medium, <L> low  (from N verified, R refuted)
Quality: <EXCELLENT|ACCEPTABLE|NEEDS IMPROVEMENT|POOR> (<overall>/8)

Full report: docs/reviews/code-review-<name>-<date>.md
```

#### Quality verdict (from `overall`, with severity floor)

Map `overall` → band, then apply the **severity floor** (`_lib` § Scoring Derivation):

- 7–8: EXCELLENT
- 5–6: ACCEPTABLE
- 3–4: NEEDS IMPROVEMENT
- 1–2: POOR

**Severity floor:** any **HIGH** finding caps the band at **NEEDS IMPROVEMENT** (final = the worse
of the score-band and the cap; it only tightens). A target with any HIGH finding can never render
EXCELLENT or ACCEPTABLE, even when the per-dimension mean is high because the catastrophe is
confined to one dimension.

#### Progress bar rendering

For score X out of 8: `[` + `#`×X + `-`×(8−X) + `]`.
```
[########] = 8/8
[#######-] = 7/8
[######--] = 6/8
[#####---] = 5/8
[####----] = 4/8
[###-----] = 3/8
[##------] = 2/8
[#-------] = 1/8
```

### Step 5 — Markdown report (if `--output` includes markdown)

Create `docs/reviews/code-review-<target-name>-<YYYY-MM-DD>.md` (create the dir if missing):

```markdown
# Code Review: <target>

| Property | Value |
|----------|-------|
| Target | <path> |
| Files | <N> |
| Language | <lang> |
| Lines | <total> |
| Date | <YYYY-MM-DD HH:MM:SS> |
| Pipeline | Find→Verify→Synthesize (8 lenses, 1-vote recall verify) |

## Quality Assessment: <VERDICT> (<overall>/8)

### Metrics

| Dimension | Score | Notes |
|-----------|-------|-------|
| Correctness | X/8 | <brief note> |
| Consistency | X/8 | <brief note> |
| Reuse/DRY | X/8 | <brief note> |
| Simplicity/Altitude | X/8 | <brief note> |
| Conventions | X/8 | <brief note> |
| **Overall** | **X/8** | |

## Findings

### High

#### 1. <summary>  (<verdict>)
- **File:** <path>:<line>
- **Dimension:** <Correctness|Consistency|Reuse/DRY|Simplicity/Altitude|Conventions>
- **Failure scenario:** <failure_scenario>
- **Verifier evidence:** <evidence>

### Medium

...

### Low

...

## Refuted (verifier-rejected candidates, for transparency)

- <file>:<line> — <summary>

## Project Rules Applied

- <CLAUDE.md path>: <rules cited by the conventions lens>
```

## Examples

### Review a single file
```
/p:code-review src/auth.ts
```

### Review a directory
```
/p:code-review src/handlers/
```

### Full report, console + markdown
```
/p:code-review src/ --output both
```

### Only high/medium findings displayed (score unchanged)
```
/p:code-review src/utils.ts --severity medium
```

### Shallow directory scan
```
/p:code-review src/ --depth 1
```
