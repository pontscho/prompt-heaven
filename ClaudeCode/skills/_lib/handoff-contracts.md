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
- `docs/feature-implementation-plan.md` — markdown, English only, structured per the template in the skill body. MUST contain at minimum:
  - `## Requirements Summary` (with success criteria, scope, assumptions, constraints, NFRs)
  - `## Architecture Analysis`
  - `## Captured Information (for implementation phase)` (file locations, imports, types, signatures, patterns, error handling, tests)
  - `## Alternative Approaches Evaluated` (with chosen approach and rationale)
  - `## Implementation Strategy`
  - `## Step-by-Step Implementation Plan`
  - `## Critical Files for Implementation`
  - `## Post-Implementation Checklist`

**Side effects:**
- Writes `.claude/tmp/` files only during interactive exploration (cleaned up by the temp-file rule)
- Does NOT write code, does NOT modify other files

**Validation:** runs Phase A (plan-inspector loop) and Phase B (security-review skill in `mode=plan`) before declaring done.

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
- Source code modifications (Edit / Write tools)
- Test execution via `p:minion-builder`
- Failure investigation via `p:minion-watson`

**Validation:** runs Phase A (impl-inspector loop) and Phase B (security-review skill in `mode=code`) before setting `implementation_complete: true`.

---

### `/p:security-review`

**Inputs (mode-dependent):**
- `mode=code` (default when target is a directory, file, or `--branch`): paths or branch diff to audit
- `mode=plan` (default when target is a `.md` file matching `*plan*.md` or `*feature-implementation-plan*`): markdown plan file path
- Mode can be auto-inferred from the target shape, or explicitly set via `--mode plan|code`

**Outputs:**
- Console block (always): verdict + severity counts + top findings + escalations
- `docs/reviews/security-review-<ts>.md` (when `--output` is `markdown` or `both`): full markdown report
- Intra-pipeline (code-mode only — written by the 3-phase pipeline workers):
  - `.claude/tmp/security-findings-<ts>.md` — produced by FIND phase, consumed by VERIFY phase
  - `.claude/tmp/security-verified-<ts>.md` — produced by VERIFY phase, consumed by ASSEMBLE step

**Side effects:**
- Only writes files in `.claude/tmp/` and (optionally) `docs/reviews/`. Never modifies source code.

**Verdict semantics:**
- `APPROVE` — no VERIFIED CRITICAL or HIGH findings
- `REVISE` — any VERIFIED HIGH finding, OR multiple VERIFIED MEDIUM in same OWASP category
- `REJECT` — any VERIFIED CRITICAL finding

---

## Per-pipeline intermediate files

| File | Producer | Consumer | Format |
|---|---|---|---|
| `docs/feature-implementation-plan.md` | `/p:feature-plan` | `/p:task-plan`, `/p:implement` (reads for context), `/p:security-review mode=plan` | markdown, structured |
| `requirements.yaml` | `/p:task-plan` | `/p:implement` | YAML, schema in `~/.claude/scripts/task-plan.py` |
| `.claude/tmp/security-findings-<ts>.md` | `p:minion-security-officer PHASE: find` | `p:minion-security-officer PHASE: verify` | markdown, `[Fn]` ID format |
| `.claude/tmp/security-verified-<ts>.md` | `p:minion-security-officer PHASE: verify` | `/p:security-review` Step 4 (Assemble) | markdown, VERIFIED/SUPPRESSED/ESCALATED sections |
| `docs/reviews/security-review-<ts>.md` | `/p:security-review` Step 4 | end-user (audit trail) | markdown, full report |

## Rules

- **Never break a documented contract silently.** If you change a file location, schema, or required field, update this document in the same change.
- **No undocumented intermediate files.** If skill A starts writing a file that skill B reads, add an entry to the table above.
- **No format drift.** If a skill's output is supposed to be a markdown report with section X, every code path that produces the output must include section X.
- **Temp file location is always `.claude/tmp/`.** No exceptions, per the global temp-file rule.
