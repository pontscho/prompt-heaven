# Validation Loop Pattern (canonical)

**This file is a fragment** — not a user-callable skill. Other skills reference it as: *"follow the validation loop pattern in `_lib/validation-loop.md`, with these specifics: …"*. See `ClaudeCode/ARCHITECTURE.md` for the layer contract.

## When to use

Use this pattern for any iterative reviewer-driven loop inside a skill:

- `/p:feature-plan` Phase A (correctness) + Phase B (security) — run as **parallel lanes of one fan-out loop** (see "Parallel fan-out variant")
- `/p:implement` Phase A (completeness) + Phase B (security) — run as **parallel lanes of one fan-out loop** (see "Parallel fan-out variant")
- Any future validator-driven iteration

DO NOT inline the loop logic into your skill body. Reference this file and specify only the per-loop variables.

## The pattern

A validation loop runs a **reviewer** (a minion or a sub-skill) up to **5 iterations**, attempts fixes between iterations, and falls back to a user-facing escape hatch when the reviewer still won't approve after 5 rounds.

### Variables the caller must specify

When a skill references this pattern, it MUST declare these specifics:

1. **Reviewer**: which minion (or sub-skill via `Skill(...)`) is invoked each iteration.
2. **Target artifact**: which file gets validated and fixed (`docs/feature-implementation-plan.md`, branch diff, `requirements.yaml`, etc.).
3. **Verdict vocabulary**: what verdict values the reviewer returns (e.g. `APPROVE / REVISE / REJECT` for inspector-plan; `COMPLETE / INCOMPLETE` for inspector-implementation; `APPROVE / REVISE / REJECT` for security-review).
4. **Fix-mode**: how this loop applies fixes between iterations. Options:
   - **`edit-and-retry`** — caller directly `Edit`s the target artifact based on findings, then re-invokes reviewer. (Generic in-place mode for text/markdown artifacts. The feature/task pipelines no longer use it — they delegate all plan writing to a dedicated writer minion; see `delegate-fix`.)
   - **`delegate-fix`** — caller delegates a fix to another minion, then re-invokes reviewer. (Used by implement: `p:minion-mason` re-implements the re-opened task based on inspector-implementation gaps, and `p:minion-builder` confirms the full-suite green build. Used by feature-plan: a single `p:minion-feature-planner` refinement per round addresses the inspector + security findings — the planner is the SOLE writer of `docs/feature-implementation-plan.md`, so even markdown fixes are delegated, never hand-edited.)
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

## Parallel fan-out variant

The base pattern runs ONE reviewer per loop and chains loops sequentially (Phase A fully approves before Phase B starts). When two reviewers audit the **same artifact along independent dimensions** (correctness AND security), run them as **parallel lanes of a single fan-out loop** instead of two sequential loops. This trades a little redundant review for wall-clock latency — both lanes see the artifact at once instead of security waiting for correctness.

Use the fan-out variant ONLY when all of these hold:

- The reviewers are mutually independent — neither's findings gate the other's.
- They audit the same target artifact.
- Each parallelized reviewer is a sub-agent. The harness parallelizes `Agent` calls issued in one message; `Skill(...)` calls run in the main context and CANNOT be parallelized this way.

### Extra variables the caller must specify

In addition to the base variables:

1. **Reviewer lanes** — the reviewers fanned out each round (e.g. `p:minion-inspector-plan` + `p:minion-inspector-security-officer PHASE: triage`).
2. **Aggregate exit condition** — the loop exits only when EVERY lane returns its approving verdict. Any lane in `REVISE` / `INCOMPLETE` / `hit` keeps the loop open.
3. **Gated deep-review lane** (optional) — a lane that is a cheap parallel probe which, on a hit, triggers an expensive sequential follow-up. See "Gated lanes" below.

### Step sequence (fan-out)

**Step PL.1 — Fan out (one message, parallel `Agent` calls).** Launch ALL reviewer lanes in a single message so the harness runs them concurrently. Each lane is a fresh sub-agent and receives the CURRENT state of the target artifact plus the round number. NEVER put a `Skill(...)` call in the fan-out — it serializes the whole round.

**Step PL.2 — Collect & merge.** Gather every lane's verdict + findings. Merge across lanes; de-duplicate where two lanes flag the same `file:line` from different angles. Keep each finding's originating lane so fixes anchor to lane-specific evidence.

**Step PL.3 — Report (ONE compact message per round).**
```
**<Loop name> — round N/5**

- Lanes: <lane1>=<verdict>, <lane2>=<verdict>, …
- Findings: <merged severity / category counts>
- Top issue: <one-liner from the highest-severity finding across all lanes>
- Action: <unified fix this round, OR "all lanes approve — exiting">
```

**Step PL.4 — Branch on the aggregate verdict.**

| Aggregate state | Action |
|---|---|
| EVERY lane `APPROVE` / `COMPLETE` (or only INFO/LOW) | Exit loop. Proceed to the next phase. |
| Any lane `REVISE` / `INCOMPLETE`, round < 5 | Apply ONE unified fix pass (each finding per its own fix-mode), then re-fan-out (PL.1). |
| Any lane `REJECT` (CRITICAL) | Escalate immediately — same as base Step L.4. |
| Any lane still open, round == 5 | Escape hatch (PL.5). |

**Step PL.5 — Escape hatch.** Same as base Step L.5, but list remaining findings grouped by lane.

### Gated lanes (cheap parallel probe → expensive sequential follow-up)

A lane whose full audit CANNOT run as a single sub-agent — because it would need to spawn its own sub-agents, and the reviewer minion (`p:minion-inspector-security-officer`) is a **leaf worker** that never nests (per the bounded-nesting rule in `ARCHITECTURE.md`: only executor minions may spawn children, and inspectors are not executors) — runs as a **gated lane**:

1. In the fan-out (PL.1) the lane runs only its cheap single-context probe (e.g. `p:minion-inspector-security-officer PHASE: triage` — a threat-surface checklist).
2. Probe reports **no hit** → the lane counts as APPROVE for this round; the expensive audit is skipped.
3. Probe **hits** → AFTER the fan-out round closes, run the full multi-phase reviewer **sequentially in the main context** via its canonical skill (e.g. `Skill(p:security-review, mode=code)`), which is then free to spawn its own fresh-context phase sub-agents. Its verdict feeds the next round's aggregate.

This preserves the canonical skill's pipeline quality (the fresh-context anchoring-bias break) while still parallelizing the cheap probe with the other lanes. It introduces NO new in-band routing token — the probe reuses the reviewer's existing `PHASE: triage`.

### Fan-out invariants (in addition to the base invariants)

- **Every lane re-runs on the CURRENT artifact each round.** This is what makes parallel review safe when fixes mutate the artifact (markdown edits, or code edits via `delegate-fix`): a lane never audits a stale version, because it re-runs after every fix pass.
- **One unified fix pass per round.** Address all lanes' CRITICAL/HIGH findings together, then re-fan-out — do not fix-and-re-run one lane at a time.
- **No `Skill(...)` inside the fan-out.** Skills run in main context and serialize the round. Only `Agent` lanes parallelize; gated deep-reviews run AFTER the fan-out, sequentially.
- **One shared round counter** for the whole fan-out (not per-lane), capped at 5.

## Skill-side reference template

When you reference this pattern in a skill, write something like:

```markdown
### Phase A — Plan correctness loop

Follow the validation loop pattern in `skills/_lib/validation-loop.md`, with these specifics:

- **Reviewer**: `Agent(p:minion-inspector-plan, prompt: "audit docs/feature-implementation-plan.md, iteration N")`
- **Target artifact**: `docs/feature-implementation-plan.md`
- **Verdict vocabulary**: `APPROVE / REVISE / REJECT`
- **Fix-mode**: `delegate-fix` — delegate a single `Agent(p:minion-feature-planner, …)` refinement to address CRITICAL and HIGH findings; anchor every fix to the inspector's `file:line` evidence. The planner is the SOLE writer of the plan file — never hand-edit it.
- **Loop name**: "Plan correctness"

Exit condition: `APPROVE` → proceed to Phase B.
```

For the **parallel fan-out variant**, declare the lanes instead of a single reviewer:

```markdown
### Validation fan-out (Phase A correctness + Phase B security, in parallel)

Follow the parallel fan-out variant in `skills/_lib/validation-loop.md`, with these specifics:

- **Reviewer lanes** (fanned out each round, one message, parallel `Agent` calls):
  - correctness: `Agent(p:minion-inspector-plan, prompt: "audit docs/feature-implementation-plan.md, round N")`
  - security (gated): `Agent(p:minion-inspector-security-officer, prompt: "PHASE: triage … plan-mode, round N")`
- **Target artifact**: `docs/feature-implementation-plan.md`
- **Aggregate exit**: both lanes APPROVE (security triage = no hit counts as APPROVE).
- **Fix-modes**: both lanes → `delegate-fix` — one `Agent(p:minion-feature-planner, …)` refinement per round addresses the merged correctness + security findings (security fixes fold in after the gated deep-review resolves). The planner is the SOLE writer; the skill never hand-edits the plan, and the round-0 perspective lenses do NOT re-run during validation.
- **Gated deep-review**: on a triage hit, after the round closes run `Skill(p:security-review, mode=plan)` sequentially, then feed its verdict into the next round.
- **Loop name**: "Plan validation fan-out"
```

That's it. The rest is in the fragment — do not re-explain the loop mechanics inline.
