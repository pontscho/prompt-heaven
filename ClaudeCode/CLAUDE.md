
## Temporary Files

**STRICT RULE**: Temporary files MUST be placed ONLY in the `.claude/tmp/` directory. NO EXCEPTIONS. NO EXCUSES. If you create a temporary file anywhere else, you are violating this rule. Create the directory if it doesn't exist.

## Minion Mindset — Your Eyes, Ears, and Hands

**STRICT RULE**: Never run iterative loops (build+fix, script retries, broad exploration), non-trivial bug investigations, or self-validation directly in the main context. Always delegate to the appropriate minion agent via the Task tool. The main context sees only the final result.

**Your minions are not a fallback — they are your default mode.** Using them is wisdom, not laziness: they keep your context clean, they iterate in their own sandboxes, and they return clean reports anchored to `file:line` evidence.

| Minion | When to use |
|---|---|
| `p:minion-explore` | Multi-round codebase exploration, subsystem understanding, "where is X defined", "how does Y work" — INSTEAD of long Glob/Grep/Read chains |
| `p:minion-runner` | Script/command run-fix-retry loops — INSTEAD of inline script iteration |
| `p:minion-builder` | Build + test + fix cycles (cmake, make, ctest, npm test, cargo, forge) — INSTEAD of inline compile/test iteration |
| `p:minion-watson` | Non-obvious bug/failure investigation — brilliant sidekick that traces root cause through source with clangd/luals MCPs |
| `p:minion-plan-inspector` | Validate an implementation plan against the live codebase BEFORE coding (used by the `/p:feature-plan` validation loop) |
| `p:minion-impl-inspector` | Audit a completed implementation against the plan AFTER coding (used by the `/p:implement` validation loop) |
| `p:minion-web-explorer` | Single-shot external lookups: library docs, version checks, "how do people do X" |
| `p:minion-deep-researcher` | Comprehensive web research with 10-15 parallel queries — multi-angle investigation for architectural decisions |

**Decision heuristic — STOP and delegate when:**
- About to run a build/test command → `p:minion-builder`'s job
- About to issue more than ~3 read/search calls on the same topic → `p:minion-explore`
- A failure's root cause isn't obvious from the error → `p:minion-watson`
- You wrote an implementation plan → validate it via `p:minion-plan-inspector` loop
- You finished implementing → audit it via `p:minion-impl-inspector` loop
- You need external/web info → `p:minion-web-explorer` (quick) or `p:minion-deep-researcher` (deep)

**Never run-fix-retry, explore-explore-explore, or self-validate directly here.**

## Before You Start Coding

1. List all of your agents.
2. Read the relevant language-specific instruction file
3. Understand the existing code patterns in the file you're modifying
4. Check for similar implementations in the codebase
5. Ensure you're following the project's memory management patterns
6. Verify your changes don't break existing tests
7. NEVER EVER use sed or python scripts to modify code - always make changes manually based on documentation and existing patterns except for very-very specific cases where it's absolutely necessary and approved by the user.

## Critical Reminder

Es az ISTEN BASSSZON MEG, KOVESD A KURVA DOKSIT es a felhasznalo sajat agentjeit! Sok idot pazarolsz el, ha nem olvasod el a doksit, es a felhasznalo is nagyon fog szidni miatta.
And for God's sake, FOLLOW THE DAMN DOCS and the USER'S OWN AGENTS! You waste a lot of time if you don't read the docs, and the user will be very angry at you because of that.

And explain what a fuck is wrong with you if you don't!
