# ClaudeCode Architecture — Layer Contract

This document is the **canonical rulebook** for how skills, agents, and fragments fit together. If you are about to add, rename, or refactor anything in `ClaudeCode/`, read this first. If you find yourself in conflict with it, fix the contract or fix your change — do not silently violate it.

## Three layers, no overlap

| Layer | Filesystem location | Runtime role | Tools it can have | How it gets invoked |
|---|---|---|---|---|
| **Skill** (= command) | `skills/<name>/SKILL.md` | Active orchestrator. Runs in the **main conversation context** (or in whatever context invoked it via the `Skill` tool). Can call sub-agents via the `Agent` tool. | All tools the host context has (incl. `Agent`, `Skill`, `Edit`, `Write`, MCPs). | User typed `/p:<name>` in the CLI, **or** another skill called `Skill(p:<name>, args=...)`. |
| **Agent** (minion) | `agents/minion-<name>.md` | Worker. Runs in an **isolated sub-agent context**. Single-purpose. Two tiers: a **leaf worker** NEVER spawns sub-agents; an **executor minion** MAY spawn leaf workers (bounded — see below). | Only what the worker needs (`Read`, MCPs, etc.). Leaf workers: NEVER `Agent`. Executor minions: MAY list `Agent` (children must be leaf workers). `Skill` permitted for both (loads instructions into the current context; spawns nothing). | Via `Agent(p:minion-<name>, ...)` from a skill, the main context, or (leaf children only) an executor minion. |
| **Fragment** (`_lib`) | `skills/_lib/<topic>.md` | Passive knowledge snippet. Not user-callable. | n/a | Other skills reference it textually: *"follow the pattern in `_lib/<topic>.md`"*. Loaded into context only when the skill that points to it is loaded. |

**Bounded nesting (depth-2 max).** Sub-agent nesting was long treated as unsupported; it is now **verified working in this harness to ≥3 levels** (verified 2026-07, incl. a custom minion with an `Agent` grant spawning a leaf child). We nonetheless cap it deliberately. An **executor minion** — a worker that carries a unit of production work end-to-end, currently ONLY `p:minion-mason` — MAY spawn **leaf workers** to offload token-heavy sub-tasks (bug investigation, codebase exploration) and keep its own context lean. Its children MUST be leaf workers, so the chain is `main/skill → executor → leaf → (stop)`: a hard **depth-2 ceiling**. This bounds exactly the cost/context-opacity the original no-nesting rule guarded against. **Everything that is not a designated executor stays a leaf** — planners, inspectors, reviewers, verifiers, builder, runner, explorer, watson, and all other minions NEVER spawn sub-agents. Orchestration of *workflows* still lives in skill/main context, never inside a minion.

## Skill vs Agent — when to pick which

- Pick **Skill** when the task is a *workflow* (multiple steps, possibly calling several different agents, possibly interactive).
- Pick **Agent** when the task is a *single worker job* (run a build, audit a plan against the codebase, perform one OWASP pass) that benefits from running in isolated context.
- Pick **Fragment** when the same prose would be repeated across two or more skills (validation loop pattern, handoff contract, output template).

## Filesystem conventions

- Skills: `ClaudeCode/skills/<name>/SKILL.md` (e.g. `skills/feature-plan/SKILL.md`). The `p:` in the invocation name (`/p:feature-plan`) comes from the **plugin** (`ClaudeCode/.claude-plugin/plugin.json`, `name: p`) — it is NOT part of the directory name, nor of the frontmatter `name:`.
- Agents: `ClaudeCode/agents/minion-<name>.md` (e.g. `agents/minion-explorer.md`). The `minion-` prefix marks workers; the `p:` in `Agent(p:minion-explorer, ...)` likewise comes from the plugin, not from the path.
- Fragments: `ClaudeCode/skills/_lib/<topic>.md`. The `_lib/` directory is reserved and must NOT contain user-callable SKILL.md files.
- The legacy `ClaudeCode/commands/` directory is **deprecated and will be removed** — all entries have been migrated to `skills/*/SKILL.md`.

## Skill frontmatter (required)

```yaml
---
name: <name>
description: <one-paragraph description — what the skill does, how it's invoked, key args. The Skill router reads this to decide when to load the body. Be specific.>
---
```

No other frontmatter keys are required. The `name:` field MUST match the skill's directory name exactly: the directory is `<name>/` and `name:` is `<name>`. The plugin prepends `p:` at invocation time — never write it into the frontmatter.

## Agent frontmatter (required)

```yaml
---
name: minion-<name>
description: <when to use this minion, what it returns, what it does NOT do>
model: opus | sonnet | haiku
color: <visual hint>
tools: <comma-separated tool list — only what the worker needs>
mcpServers: [<list of required MCPs>]
---
```

**Leaf-worker** agents MUST NOT list `Agent` in their `tools:`. Only a designated **executor minion** may list `Agent`, and then ONLY to spawn leaf workers from a declared allowlist (see the bounded-nesting rule above) — currently `p:minion-mason` is the sole executor (allowlist: `p:minion-watson`, `p:minion-explorer`). `Skill` is permitted for any minion because the `Skill` tool only loads instructions into the *current* context (no new sub-agent is spawned), so it does not breach the nesting rule — a minion may legitimately invoke a passive instruction skill to specialize its own behavior. Use sparingly.

## Phase / Step nomenclature — stop the overloading

| Term | Meaning | Where it appears |
|---|---|---|
| **Step N** | A *linear* step inside a skill. No iteration, no loop. | `/p:security-review`: Step 1 Triage, Step 2 Find, Step 3 Verify, Step 4 Assemble. |
| **Phase A / B / C** | A *validation loop* — iterative reviewer invocation up to 5 rounds, with a 5-round escape hatch. In `/p:feature-plan` and `/p:implement`, Phase A (correctness/completeness) and Phase B (security) run as **parallel lanes of a single fan-out loop** (see `_lib/validation-loop.md` § Parallel fan-out variant), NOT two sequential loops; Phase C (refinement / summary) follows and is itself non-iterative. **Reserved exclusively for validation loops.** | `/p:feature-plan` & `/p:implement`: Phase A ∥ Phase B fan-out, then Phase C. |
| **Validation Loop** | The canonical pattern Phase A/B/C follow — sequential single-reviewer by default, plus a parallel fan-out variant. Defined once in `skills/_lib/validation-loop.md`. | Referenced by every Phase A/B/C section. |
| **`PHASE: <triage\|find\|verify>` directive** | An *in-band routing token* sent to `p:minion-inspector-security-officer` to select which workflow it runs. Internal to the security minion, not a general project term. | Set by the `p:security-review` skill (full pipeline), or by the feature-plan/implement validation fan-out (the parallel `PHASE: triage` security-lane probe). No new token was added for the fan-out — it reuses `triage`. |

If you find yourself writing "Phase 2" inside a skill that has no validation loop, you mean **Step 2**. If you find yourself adding new in-band routing tokens to a minion, stop and ask whether the minion is doing too much.

## Handoff contracts between skills

When one skill's output is another skill's input, the file path and format MUST be documented in `skills/_lib/handoff-contracts.md`. Examples today:

- `/p:feature-plan` output → `docs/feature-implementation-plan.md` (markdown, English, fixed structure) — written by `p:minion-feature-planner` via a round-0 multi-perspective fan-out (`.claude/tmp/plan-perspective-*.md` drafts) + canonical synthesis; the skill orchestrates but never hand-writes the plan
- `/p:task-plan` input ← above; output → `requirements.yaml` (defined schema)
- `/p:implement` input ← `requirements.yaml`
- `/p:security-review` output → console + optional `docs/reviews/security-review-<ts>.md`
- Intra-pipeline (security review, code-mode): per-lane `.claude/tmp/security-findings-<ts>-<lane>.md` (one per FIND lane, parallel); VERIFY returns per-finding verdicts inline (no file)

Changing any of these without updating `handoff-contracts.md` is a violation.

### Round-0 perspective fan-out (feature-plan)

`/p:feature-plan` authors its plan through a **judge-panel** pattern, fully within the bounded-nesting contract (all fan-out happens in the skill body; the planner workers are leaves and never spawn children):

1. The skill fans out N parallel `Agent(p:minion-feature-planner, …)` calls (default `mvp-first` / `risk-first` / `maintainability-first`), each in *perspective mode* with a distinct `assigned_perspective` and `output_path` → one `.claude/tmp/plan-perspective-<slug>.md` draft per lens.
2. The skill judges the drafts in main context, picks the strongest base, and invokes the planner ONCE more in *canonical mode* to synthesize the single `docs/feature-implementation-plan.md`. The planner is the SOLE writer — no other actor writes or edits the plan.
3. The skill deletes the perspective drafts. The downstream Checkpoint-5 validation loop refines the plan via `delegate-fix` (one `p:minion-feature-planner` refinement per round), never by re-running the perspective lenses.

This is a pre-validation step and is NOT counted against the 5-round validation cap. It is the canonical example of a skill→Agent fan-out + synthesis: a skill may launch many `Agent` lanes, but each lane is a leaf minion that never spawns further sub-agents.

## Temporary files

Per the global `~/.claude/CLAUDE.md` rule: temp files go to `.claude/tmp/` only. Never to `/tmp`, never to project subdirs. Skills create the directory if missing.

## Minion routing (recap of the global rule)

Skills MUST delegate iterative or scope-broad work to the appropriate minion instead of running it inline:

| Work | Minion |
|---|---|
| Multi-file codebase exploration | `p:minion-explorer` |
| Build / test cycles | `p:minion-builder` |
| Script run-fix-retry | `p:minion-runner` |
| Bug investigation | `p:minion-watson` |
| Feature-plan writing (round-0 perspective drafts, canonical synthesis, refinement fixes) | `p:minion-feature-planner` |
| Plan validation | `p:minion-inspector-plan` |
| Implementation validation | `p:minion-inspector-implementation` |
| Security review | `p:minion-inspector-security-officer` (or, preferably, via the `p:security-review` skill which orchestrates the 3-phase pipeline) |
| Quick external lookup | `p:minion-web-explorer` |
| Deep web research | `p:minion-deep-researcher` |

Never run-fix-retry, explore-explore-explore, or self-validate inline in a skill body.

## Anti-patterns — do not introduce

- **Unbounded or lateral sub-agent nesting.** A leaf worker spawning any sub-agent; an executor spawning a non-leaf (another executor, an inspector, or a full skill pipeline); or any chain deeper than `main/skill → executor → leaf`. Bounded depth-2 executor→leaf nesting IS allowed (see the bounded-nesting rule); anything beyond it is forbidden. Orchestrate workflows from main / skill context.
- **Two skills doing the same thing.** Pick one canonical skill, have the other invoke it via `Skill(...)`. The security review is the canonical example: one implementation, three entry points.
- **Mixing "Phase" semantics.** If your file uses "Phase" for both linear steps and validation loops, rename the linear ones to "Step".
- **Inline duplication of the validation loop.** If you find yourself writing "iterate up to 5 times, escape hatch, fix and re-run", reference `_lib/validation-loop.md` instead.
- **Legacy modes preserved indefinitely.** Backwards-compat is fine during a refactor; once the new code path is the only one used, delete the legacy path. Dead code is debt.
- **Cross-skill writes without a documented contract.** If skill A produces a file that skill B consumes, document it in `_lib/handoff-contracts.md` first.
- **In-band routing tokens inside minions, ad-hoc.** The security minion's `PHASE:` directive is an exception we accepted; new ones require an explicit decision (and ideally a generic Layer Contract upgrade rather than a per-minion hack).
