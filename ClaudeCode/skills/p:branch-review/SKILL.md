---
name: p:branch-review
description: Review the current branch's diff against its base via a multi-agent Find→Verify→Synthesize fan-out — the 8 shared code-review lenses plus 2 git-specific lenses (commit-hygiene, breaking-change/API), an independent verifier per candidate, then a ranked report with an APPROVED/CHANGES REQUESTED/REJECTED verdict. Use before PR creation or merge. Trigger: /p:branch-review [base-branch]
---

# Branch Review

Review the current branch's diff against its base branch with a **multi-agent fan-out**: the 8
shared code-review lenses plus 2 branch-specific git lenses surface candidates in parallel, an
independent verifier re-judges each one, then the skill body ranks, caps, and renders a report
with a merge-readiness verdict.

This is a **linear 4-Step pipeline orchestrated from this skill body** (Step 1 Scope → Step 2 Find
→ Step 3 Verify → Step 4 Synthesize). It is NOT a validation loop — see
`ClaudeCode/ARCHITECTURE.md` on "Step N" vs "Phase A/B/C".

> **Single source of truth.** The 8 shared lens texts, the recall verdict-ladder, the
> candidate/verdict schemas, the lens→dimension map, and the scoring weights live in
> `ClaudeCode/skills/_lib/code-review-lenses.md`. This skill references them. The **2 git lenses
> below are branch-review-specific and defined HERE** (not in `_lib`) — no other skill uses them.

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| base | auto-detect | Base branch to compare against (priority: `master` > `main` > `trunk`) |
| --output | console | `console`, `markdown`, or `both` |
| --severity | all | Minimum displayed severity: `high`, `medium`, `low` (filters the DISPLAYED list only; the verdict uses ALL verified findings) |

**`--severity` vocabulary:** canonical values are `high \| medium \| low`. The prior values are
accepted as **aliases**: `critical→high`, `warning→medium`, `info→low`.

**Out of scope (this round):** `--fix` and `--comment` are NOT implemented — this skill is strictly
**read-only**. The CC effort matrix and Sweep phase are out of scope (one fixed recall-biased
configuration: 10 lenses, ≤6 candidates each, 1-vote recall verify, cap 12).

## Instructions

### Step 1 — Scope

All git access goes through the **`mcp-git` `git_call`** tool — NEVER `Bash("git ...")` + `sed`.

1. Confirm a git repository (`git_call` `status`).
2. Detect the base branch, priority `master` > `main` > `trunk`; if `base` is provided, use it.
   Verify the current branch is not the base.
3. Materialize the diff and changed-file list:
   - `git_call(function: "diff", params: {args: "--name-only <base>...HEAD"})` → changed files.
   - `git_call(function: "diff", params: {args: "--stat <base>...HEAD"})` → stats (feeds the console `Ahead: … lines` header; keep it).
   - `git_call(function: "diff", params: {args: "<base>...HEAD"})` → the unified diff.
   - `git_call(function: "log", params: {args: "<base>..HEAD --oneline"})` → commit log (feeds the
     commit-hygiene lens).
4. Apply the non-code/test skip lists (same as `p:code-review` Step 1) to the changed-file set.
5. Gather applicable CLAUDE.md files for the changed paths (for the conventions lens).
6. Build the **scope block** (include the diff command, the changed files, the commit log, the
   applicable CLAUDE.md, and a one-paragraph summary). Emit ONE compact status:
   `Step 1: SCOPE — <base>...HEAD: <N> files, <K> commits`.

### Step 2 — Find (parallel lens finders, ONE message)

Fan out **10 lenses** = the 8 shared lenses from `_lib/code-review-lenses.md` § Lens Catalog
**plus the 2 git lenses defined below**. In a **single message**, issue 10 parallel `Agent` calls
to `p:minion-code-reviewer` — the minion is lens-agnostic, so the git lenses are passed exactly
like the shared ones (`LENS:` key + body + scope block + schema). Collect the candidate blocks.
Emit ONE compact status: `Step 2: FIND — 10 lenses → C candidates`.

The git lenses (branch-review-only; recall-biased, ≤6 candidates each, same candidate schema):

#### Lens: git-commit-hygiene
- **class:** correctness (for ranking)
- **dimension:** (none — git lenses do not feed the dimension bars)
- **severity cap:** **MEDIUM** — a commit-hygiene finding never escalates past MEDIUM, so on its own
  it yields at most **CHANGES REQUESTED**, never REJECTED. Hygiene is a clean-up ask, not a
  merge-blocking defect; only confirmed correctness bugs and genuine API/format breakage (the
  `git-breaking-change-api` lens) block a merge.
- **body:**
  Review the commit log for hygiene problems that should block a clean merge: WIP / fixup / temp /
  "wip"/"oops"/"." commits that should be squashed; non-atomic commits that mix unrelated changes;
  empty or non-descriptive commit messages; commits that revert each other within the branch
  (churn). For each, name the offending commit (short hash + subject) and the concrete cost
  (unreviewable history, hard to revert, bisect-hostile). `failure_scenario` = the maintenance
  consequence, not a crash.

#### Lens: git-breaking-change-api
- **class:** correctness (for ranking/severity)
- **dimension:** Correctness
- **body:**
  Scan the diff for changes that break consumers of a public surface: a changed/removed exported
  function signature, struct/record field, return type or shape; a renamed or removed public
  symbol; a changed config-file format, env-var name, CLI flag, or on-disk/wire format; a changed
  default that alters behavior. Use `find_references` (purity/clangd for C, luals for Lua) to
  confirm there ARE external callers before flagging. `failure_scenario` = the concrete breakage a
  caller/operator hits (compile error, runtime error, silent behavior change).

### Step 3 — Verify (dedup → budget → parallel verifiers, ONE message)

Identical to `p:code-review` Step 3: dedup near-identical candidates; if the deduped count exceeds
`VERIFY_BUDGET = 24`, truncate correctness-first (worst-case Agent count for a review = `10 + 24`)
and say so; then one `Agent(p:minion-code-verifier, …)` per surviving candidate in ONE message,
each with the scope block + the candidate + the recall verdict-ladder from `_lib`. Keep CONFIRMED
+ PLAUSIBLE, drop REFUTED. Emit ONE compact status: `Step 3: VERIFY — C → V kept (R refuted)`.

### Step 4 — Synthesize (skill body, no agent)

1. Dedup by root cause; **rank** with the `_lib` P-5 rank fn (correctness > quality, CONFIRMED >
   PLAUSIBLE); **cap** at 12.
2. **Derive severity** per finding and the 5 **dimension scores** from ALL verified findings, per
   `_lib/code-review-lenses.md` § Scoring Derivation. The **2 git lenses are NOT in the
   lens→dimension map** — they drive ranking, severity, and the verdict only, and do NOT feed the
   dimension bars (which score code quality). They rank/severity as Correctness-class, **except
   `git-commit-hygiene` is capped at MEDIUM** (see its lens definition): a hygiene finding never
   becomes HIGH and so alone cannot force REJECTED, whereas `git-breaking-change-api` keeps full
   correctness-class severity (a real breaking change is a legitimate HIGH ⇒ REJECTED).
3. **Verdict (severity-driven, NOT score-driven):** any **HIGH** finding ⇒ **REJECTED**; else any
   **MEDIUM** ⇒ **CHANGES REQUESTED**; else (only LOW or none) ⇒ **APPROVED**. The dimension bars
   still render for context.
4. Apply `--severity` to the DISPLAYED findings list only.
5. Render the console report. If `--output` includes markdown, write the report (Step 5); create
   `docs/reviews/` if missing.

#### Console output format

```
p:branch-review

Branch: <current-branch>
Base:   <base-branch>
Ahead:  <K> commits | <N> files | +<added> -<removed> lines

----------------------------------------

Quality Metrics:

  Correctness:          [######--] 6/8
  Consistency:          [#######-] 7/8
  Reuse/DRY:            [########] 8/8
  Simplicity/Altitude:  [#####---] 5/8
  Conventions:          [#######-] 7/8

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
Verdict: <APPROVED | CHANGES REQUESTED | REJECTED>

Full report: docs/reviews/branch-review-<timestamp>.md
```

Progress bar rendering is identical to `p:code-review` (`[` + `#`×X + `-`×(8−X) + `]`).

### Step 5 — Markdown report (if `--output` includes markdown)

Create `docs/reviews/branch-review-<YYYY-MM-DD-HHMMSS>.md` (create the dir if missing):

```markdown
# Branch Review

| Property | Value |
|----------|-------|
| Branch | <current-branch> |
| Base | <base-branch> |
| Commits | <K> |
| Files | <N> |
| Changes | +<added> -<removed> |
| Date | <YYYY-MM-DD HH:MM:SS> |
| Pipeline | Find→Verify→Synthesize (10 lenses, 1-vote recall verify) |

## Verdict: <VERDICT>

| Severity | Count |
|----------|-------|
| High | <N> |
| Medium | <M> |
| Low | <K> |

### Metrics

| Dimension | Score |
|-----------|-------|
| Correctness | X/8 |
| Consistency | X/8 |
| Reuse/DRY | X/8 |
| Simplicity/Altitude | X/8 |
| Conventions | X/8 |

## Commits

| Hash | Message |
|------|---------|
| <hash> | <message> |

## Findings

### High

#### 1. <summary>  (<verdict>)
- **File:** <path>:<line>
- **Dimension:** <dimension>
- **Failure scenario:** <failure_scenario>
- **Verifier evidence:** <evidence>

### Medium

...

### Low

...

## Checklist

- [ ] High findings resolved
- [ ] Medium findings reviewed
- [ ] Commit history clean (squash WIP/fixup)
- [ ] Breaking changes documented / versioned
```

## Examples

### Basic usage (auto-detect base)
```
/p:branch-review
```

### Specify base branch
```
/p:branch-review develop
```

### Full report, console + markdown
```
/p:branch-review --output both
```

### Only high/medium findings displayed (verdict unchanged)
```
/p:branch-review --severity medium
```
