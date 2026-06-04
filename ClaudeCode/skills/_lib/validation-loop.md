# Validation Loop Pattern (canonical)

**This file is a fragment** — not a user-callable skill. Other skills reference it as: *"follow the validation loop pattern in `_lib/validation-loop.md`, with these specifics: …"*. See `ClaudeCode/ARCHITECTURE.md` for the layer contract.

## When to use

Use this pattern for any iterative reviewer-driven loop inside a skill:

- `/p:feature-plan` Phase A (plan-inspector loop) and Phase B (security-review loop)
- `/p:implement` Phase A (impl-inspector loop) and Phase B (security-review loop)
- Any future validator-driven iteration

DO NOT inline the loop logic into your skill body. Reference this file and specify only the per-loop variables.

## The pattern

A validation loop runs a **reviewer** (a minion or a sub-skill) up to **5 iterations**, attempts fixes between iterations, and falls back to a user-facing escape hatch when the reviewer still won't approve after 5 rounds.

### Variables the caller must specify

When a skill references this pattern, it MUST declare these specifics:

1. **Reviewer**: which minion (or sub-skill via `Skill(...)`) is invoked each iteration.
2. **Target artifact**: which file gets validated and fixed (`docs/feature-implementation-plan.md`, branch diff, `requirements.yaml`, etc.).
3. **Verdict vocabulary**: what verdict values the reviewer returns (e.g. `APPROVE / REVISE / REJECT` for plan-inspector; `COMPLETE / INCOMPLETE` for impl-inspector; `APPROVE / REVISE / REJECT` for security-review).
4. **Fix-mode**: how this loop applies fixes between iterations. Options:
   - **`edit-and-retry`** — caller directly `Edit`s the target artifact based on findings, then re-invokes reviewer. (Used by feature-plan: edit the plan markdown to address inspector findings.)
   - **`delegate-fix`** — caller delegates a fix to another minion (e.g. `p:minion-builder` to apply a code change), then re-invokes reviewer. (Used by implement: re-build / re-implement based on impl-inspector gaps.)
   - **`escalate-immediately`** — certain verdict values (e.g. `REJECT` with CRITICAL severity) must NOT be auto-fixed; surface to user immediately.
5. **Loop name**: human label for per-iteration user messages (e.g. "Plan correctness", "Plan security review", "Implementation completeness", "Implementation security audit").

### Step sequence (every loop follows this)

**Step L.1 — Invoke the reviewer.**

Fresh sub-agent / fresh skill invocation each iteration — the reviewer has no memory of prior rounds. Always pass the iteration number and the target artifact path in the prompt.

**Step L.2 — Parse the reviewer's report.**

Extract: verdict, counts by severity (or by category, depending on reviewer), top one or two findings, action items.

**Step L.3 — Report to the user (ONE compact message per iteration).**

Format:
```
**<Loop name> — iteration N/5**

- Verdict: <APPROVE / REVISE / REJECT / COMPLETE / INCOMPLETE / …>
- Findings: <severity or category counts>
- Top issue: <one-liner from the highest-severity finding>
- Action: <what will be fixed this round, OR "no fixes needed — exiting Phase">
```

**Do NOT dump the full reviewer report at every iteration.** The compact summary is enough for the user to follow progress.

**Step L.4 — Branch on verdict.**

| Verdict | Action |
|---|---|
| `APPROVE` / `COMPLETE` (or only INFO/LOW findings) | Exit loop. Proceed to next phase. |
| `REVISE` / `INCOMPLETE`, iteration < 5 | Apply fixes (per fix-mode), increment counter, loop back to L.1. |
| `REJECT` (CRITICAL finding) | **Escalate immediately to the user**. CRITICAL findings cannot be silently auto-fixed. Present the report, ask: (a) authorize fix attempt, (b) accept the risk with explicit documentation, (c) halt. |
| `REVISE` / `INCOMPLETE`, iteration == 5 | Fall through to Step L.5 (escape hatch). |

**Step L.5 — Five-iteration escape hatch.**

Stop iterating and hand control back to the user:
```
**<Loop name> hit 5 iterations without <APPROVE/COMPLETE>.**

Final verdict: <REVISE / INCOMPLETE>
Remaining findings:
- <one-liner each, top 3-5>

How should we proceed?
1. One more iteration (I'll attempt fixes again)
2. Accept the current state and proceed to the next phase
3. Halt — needs offline rework before continuing
4. Other (custom direction)
```

On user choice:
- `1` → run one more iteration (reset for one extra round, then re-escape if still no approve).
- `2` → exit loop, record open findings under "Known limitations" in the target artifact, then proceed.
- `3` → halt with no further edits.
- `4` → act on the user's instructions.

## Invariants — every loop MUST honor

- **Reviewer is read-only.** It does not modify the target artifact. Only the loop caller does (per fix-mode).
- **Fresh context per iteration.** Reviewer is a new sub-agent or new skill invocation — no memory of prior rounds. Pass iteration number in the prompt.
- **No iteration without a fix.** Every iteration that ends in `REVISE`/`INCOMPLETE` must end with an actual edit / fix attempt before the next round. (Exception: at the escape hatch.)
- **No CRITICAL auto-fix.** `REJECT` with CRITICAL severity always escalates to the user — never silently iterated.
- **Counter is per-loop.** Phase A and Phase B counters are independent — track them separately, surface the active loop name in every iteration message.
- **Compact user message per round.** Do not dump full reviewer reports. The user sees verdict + counts + top issue + action.
- **No scope creep.** If a finding suggests work beyond the originally-requested change, document it under "Out of Scope" (for plans) or "Open items" (for implementations) — do not silently expand the target artifact.

## Skill-side reference template

When you reference this pattern in a skill, write something like:

```markdown
### Phase A — Plan correctness loop

Follow the validation loop pattern in `skills/_lib/validation-loop.md`, with these specifics:

- **Reviewer**: `Agent(p:minion-plan-inspector, prompt: "audit docs/feature-implementation-plan.md, iteration N")`
- **Target artifact**: `docs/feature-implementation-plan.md`
- **Verdict vocabulary**: `APPROVE / REVISE / REJECT`
- **Fix-mode**: `edit-and-retry` — directly `Edit` the plan file to address CRITICAL and HIGH findings; anchor every fix to the inspector's `file:line` evidence
- **Loop name**: "Plan correctness"

Exit condition: `APPROVE` → proceed to Phase B.
```

That's it. The rest is in the fragment — do not re-explain the loop mechanics inline.
