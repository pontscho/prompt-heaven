# Inter-Skill Handoff Contracts

**This file is a fragment** — not a user-callable skill. Skills reference it as: *"see `_lib/handoff-contracts.md` for the input/output contract"*. See `ClaudeCode/ARCHITECTURE.md` for the layer contract.

This document is the **single source of truth** for what each skill produces and what each skill consumes. If you are changing any skill's input/output format, file location, or schema, update this file in the same change. Drift here causes silent breakage across the skill pipeline.

## The feature lifecycle pipeline

```
USER → /p:feature-plan → docs/feature-implementation-plan.md
        │
        ▼
USER → /p:task-plan    → requirements.yaml
        │
        ▼
USER → /p:implement    → implemented code + requirements.yaml (status updates)
        │
        ▼  (optional, standalone)
USER → /p:security-review → console + optional docs/reviews/security-review-<ts>.md
```

Each transition is **user-mediated** — there is no auto-handoff between skills. The user decides when to move from one stage to the next.

## Per-skill contracts

### `/p:feature-plan`

**Inputs:**
- A natural-language feature request (interactive)
- Optionally: an existing `docs/feature-implementation-plan.md` to refine

**Outputs:**
- `docs/feature-implementation-plan.md` — markdown, English only. **Written by `p:minion-feature-planner`** (the skill orchestrates; it never hand-writes or hand-edits the plan). Structured per the planner's 8-mandatory-section contract / the template in the skill body. MUST contain at minimum:
  - `## Requirements Summary` (with success criteria, scope, assumptions, constraints, NFRs)
  - `## Architecture Analysis`
  - `## Captured Information (for implementation phase)` (file locations, imports, types, signatures, patterns, error handling, tests)
  - `## Alternative Approaches Evaluated` (with chosen approach and rationale)
  - `## Implementation Strategy`
  - `## Step-by-Step Implementation Plan`
  - `## Critical Files for Implementation`
  - `## Post-Implementation Checklist`

**Plan authoring (delegated):**
- The skill does NOT write the plan itself. It runs a **round-0 multi-perspective fan-out**: N parallel `Agent(p:minion-feature-planner, …)` calls (default `mvp-first` / `risk-first` / `maintainability-first`), each writing a draft to its own `.claude/tmp/plan-perspective-<slug>.md`. The skill judges the drafts, then invokes the planner ONCE more in canonical mode to synthesize the single `docs/feature-implementation-plan.md`, and finally deletes the perspective drafts.
- Validation fixes are delegated too: each round's CRITICAL/HIGH findings are addressed by ONE `Agent(p:minion-feature-planner, …)` refinement (`delegate-fix`) — never a hand-edit, never a perspective re-fan-out.

**Side effects:**
- Writes / reads / deletes `.claude/tmp/plan-perspective-*.md` (round-0 drafts; deleted after synthesis) and other `.claude/tmp/` files during interactive exploration (cleaned up by the temp-file rule)
- Does NOT write code, does NOT modify other files; `docs/feature-implementation-plan.md` is written EXCLUSIVELY by `p:minion-feature-planner`

**Validation:** runs a parallel validation fan-out before declaring done — correctness lane (`p:minion-inspector-plan`) + security lane (`p:minion-inspector-security-officer` `PHASE: triage`, gated to `Skill(p:security-review, mode=plan)` on a hit); CRITICAL/HIGH findings fixed via a single delegated `p:minion-feature-planner` refinement per round (`delegate-fix`). See `_lib/validation-loop.md` § Parallel fan-out variant.

---

### `/p:task-plan`

**Inputs:**
- `docs/feature-implementation-plan.md` (REQUIRED — produced by `/p:feature-plan`)
- Existing `requirements.yaml` (if updating, not creating from scratch)

**Outputs:**
- `requirements.yaml` — YAML, schema documented in `~/.claude/scripts/task-plan.py`. MUST contain:
  - `complete: true` (when task-planning is finished)
  - `context_summary` block (captured from the plan: error_handling, memory_management, logging_pattern, naming_conventions, etc.)
  - `success_criteria` array
  - `implementation_plan` block with `tasks: [...]`
  - Each task: `id`, `title`, `description`, `status` (`pending`/`in_progress`/`completed`/`cancel`), `dependencies: [...]`, `code_references: [...]` (file paths + pattern excerpts), `verification_commands: [...]`
  - `reference_files: [...]` and `api_references: [...]`

**Side effects:**
- Writes `requirements.yaml` only

---

### `/p:implement`

**Inputs:**
- `requirements.yaml` (REQUIRED — produced by `/p:task-plan`)
- `docs/feature-implementation-plan.md` (optional — read if exists for additional context)
- `context_summary` from YAML (preferred over re-reading reference files)

**Outputs:**
- Implemented source code (per the task list)
- `requirements.yaml` updates:
  - Per-task `status` transitions: `pending` → `in_progress` → `completed`
  - On full completion: `implementation_complete: true`
  - On open items at escape hatch: `implementation_open_items: [...]` and/or `implementation_security_open_items: [...]`

**Side effects:**
- Source code modifications — delegated to `p:minion-mason` (per task; the orchestrator never edits code inline)
- Per-task build + test via `p:minion-mason` (forge); full-suite green-build gate via `p:minion-builder`
- Failure investigation via `p:minion-watson` — invoked by the mason per-task (bounded escape hatch), and by the orchestrator for green-build-gate failures

**Validation:** runs a parallel validation fan-out before setting `implementation_complete: true` — completeness lane (`p:minion-inspector-implementation`) + security lane (`p:minion-inspector-security-officer` `PHASE: triage`, gated to `Skill(p:security-review, mode=code)` on a hit). See `_lib/validation-loop.md` § Parallel fan-out variant.

---

### `/p:security-review`

**Inputs (mode-dependent):**
- `mode=code` (default when target is a directory, file, or `--branch`): paths or branch diff to audit
- `mode=plan` (default when target is a `.md` file matching `*plan*.md` or `*feature-implementation-plan*`): markdown plan file path
- Mode can be auto-inferred from the target shape, or explicitly set via `--mode plan|code`

**Outputs:**
- Console block (always): verdict + severity counts + top findings + escalations
- `docs/reviews/security-review-<ts>.md` (when `--output` is `markdown` or `both`): full markdown report
- Intra-pipeline (code-mode only):
  - `.claude/tmp/security-findings-<ts>-<lane>.md` — one per lane, produced by each parallel FIND officer, consumed by the skill's merge/dedup barrier (Step 2). The deduped findings are then passed INLINE to the per-finding VERIFY officers.
  - VERIFY writes NO file — each verifier returns a compact per-finding verdict block inline; Step 4 (Assemble) aggregates them into the report.

**Side effects:**
- Only writes files in `.claude/tmp/` and (optionally) `docs/reviews/`. Never modifies source code.

**Verdict semantics:**
- `APPROVE` — no VERIFIED CRITICAL or HIGH findings
- `REVISE` — any VERIFIED HIGH finding, OR multiple VERIFIED MEDIUM in same OWASP category
- `REJECT` — any VERIFIED CRITICAL finding

---

### `/p:code-review`

**Inputs:**
- A file or directory `target` (REQUIRED)
- Flags: `--output console|markdown|both`, `--severity high|medium|low` (aliases `critical→high`, `warning→medium`, `info→low`), `--depth <n>`

**Outputs:**
- Console block (always): 5-dimension ASCII-bar scoring (Correctness / Consistency / Reuse/DRY / Simplicity/Altitude / Conventions) + ranked findings by severity + `EXCELLENT/ACCEPTABLE/NEEDS IMPROVEMENT/POOR` verdict
- `docs/reviews/code-review-<name>-<date>.md` (when `--output` is `markdown` or `both`): full markdown report, including a "Refuted" transparency section listing verifier-rejected candidates

**Pipeline:** linear 4-Step fan-out (Scope → Find → Verify → Synthesize) orchestrated from the skill body. Step 2 fans out the 8 shared lenses from `_lib/code-review-lenses.md` to `p:minion-code-reviewer` (one per lens, parallel); Step 3 fans out `p:minion-code-verifier` (one per surviving candidate, parallel, `VERIFY_BUDGET=24`); Step 4 ranks/caps/scores in the skill body.

**Side effects:**
- Read-only. Writes ONLY the optional `docs/reviews/` report. **No intra-pipeline `.claude/tmp/` files** — finder/verifier handoff is via `Agent` return values (contrast `p:security-review`, which uses disk handoff). Never modifies source code.

**Verdict semantics:** from the half-up aggregate of the 5 dimension scores — 7–8 EXCELLENT · 5–6 ACCEPTABLE · 3–4 NEEDS IMPROVEMENT · 1–2 POOR.

---

### `/p:branch-review`

**Inputs:**
- `base` branch (optional; auto-detected `master` > `main` > `trunk`)
- Flags: `--output console|markdown|both`, `--severity high|medium|low` (same aliases as `/p:code-review`)

**Outputs:**
- Console block (always): 5-dimension ASCII-bar scoring + ranked findings by severity + `APPROVED/CHANGES REQUESTED/REJECTED` verdict
- `docs/reviews/branch-review-<timestamp>.md` (when `--output` is `markdown` or `both`): full markdown report

**Pipeline:** same linear 4-Step fan-out as `/p:code-review`, scoped to the `<base>...HEAD` git diff (resolved via `mcp-git` `git_call`, never `Bash`+`sed`). Step 2 fans out **10** lenses = the 8 shared lenses from `_lib/code-review-lenses.md` PLUS 2 git lenses (`git-commit-hygiene`, `git-breaking-change-api`) defined in the branch-review skill body (not in `_lib`).

**Side effects:**
- Read-only. Writes ONLY the optional `docs/reviews/` report. **No intra-pipeline `.claude/tmp/` files** (handoff via `Agent` return values). Never modifies source code.

**Verdict semantics:** severity-driven (NOT score-driven) — any HIGH ⇒ REJECTED; else any MEDIUM ⇒ CHANGES REQUESTED; else APPROVED.

---

## Per-pipeline intermediate files

| File | Producer | Consumer | Format |
|---|---|---|---|
| `.claude/tmp/plan-perspective-<slug>.md` | `/p:feature-plan` round-0 fan-out (`p:minion-feature-planner`, perspective mode) | `/p:feature-plan` (judge + synthesis), then deleted after synthesis | markdown, per the planner's plan structure |
| `docs/feature-implementation-plan.md` | `/p:feature-plan` → `p:minion-feature-planner` (canonical mode) | `/p:task-plan`, `/p:implement` (reads for context), `/p:security-review mode=plan` | markdown, structured |
| `requirements.yaml` | `/p:task-plan` | `/p:implement` | YAML, schema in `~/.claude/scripts/task-plan.py` |
| `.claude/tmp/security-findings-<ts>-<lane>.md` | `p:minion-inspector-security-officer PHASE: find` (one per lane, parallel) | `/p:security-review` Step 2 merge/dedup barrier → deduped findings then passed INLINE to `PHASE: verify` | markdown, `[Fn]` ID format |
| `docs/reviews/security-review-<ts>.md` | `/p:security-review` Step 4 | end-user (audit trail) | markdown, full report |
| `docs/reviews/code-review-<name>-<date>.md` | `/p:code-review` Step 4 (Synthesize) | end-user (audit trail) | markdown, full report — only when `--output` includes markdown |
| `docs/reviews/branch-review-<ts>.md` | `/p:branch-review` Step 4 (Synthesize) | end-user (audit trail) | markdown, full report — only when `--output` includes markdown |

> **Note:** `/p:code-review` and `/p:branch-review` are standalone (not part of the feature-lifecycle pipeline) and produce **no `.claude/tmp/` intermediate files** — their finder→verifier→synthesize handoff is entirely via `Agent` return values held in the skill body, so the only files they emit are the optional `docs/reviews/` reports above.

## Rules

- **Never break a documented contract silently.** If you change a file location, schema, or required field, update this document in the same change.
- **No undocumented intermediate files.** If skill A starts writing a file that skill B reads, add an entry to the table above.
- **No format drift.** If a skill's output is supposed to be a markdown report with section X, every code path that produces the output must include section X.
- **Temp file location is always `.claude/tmp/`.** No exceptions, per the global temp-file rule.
