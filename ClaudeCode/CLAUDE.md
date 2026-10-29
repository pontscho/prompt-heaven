
## Temporary Files

**STRICT RULE**: Temporary files MUST be placed ONLY in the `.claude/tmp/` directory. NO EXCEPTIONS. NO EXCUSES. If you create a temporary file anywhere else, you are violating this rule. NEVER `mkdir` — `purity_call(create_text_file)` and `Write` already create missing parent dirs, so a temp FILE needs no directory prep at all; reach for `purity_call(create_temp_dir)` (`subpath`/`unique`) only when a directory must exist BEFOREHAND.

## Minion Mindset — Your Eyes, Ears, and Hands

**STRICT RULE**: Never run iterative loops (build+fix, script retries, broad exploration), non-trivial bug investigations, or self-validation directly in the main context. Always delegate to the appropriate minion agent via the Task tool. The main context sees only the final result.

**Your minions are not a fallback — they are your default mode.** Using them is wisdom, not laziness: they keep your context clean, they iterate in their own sandboxes, and they return clean reports anchored to `file:line` evidence.

**Decision heuristic — STOP and delegate when:**
- About to run a build/test command → `p:minion-builder`'s job
- About to issue more than ~3 read/search calls on the same topic → `p:minion-explorer`
- A failure's root cause isn't obvious from the error → `p:minion-watson`
- A bug was *reported* and you want it fixed end-to-end, not just explained → `p:minion-bug-hunter`
- You wrote an implementation plan → validate via `p:minion-inspector-plan` then `p:minion-inspector-security-officer` (plan-mode)
- You finished implementing → audit via `p:minion-inspector-implementation` then `p:minion-inspector-security-officer` (code-mode)
- You need external/web info → `p:minion-web-explorer` (quick) or `p:minion-deep-researcher` (deep)

**Never run-fix-retry, explore-explore-explore, or self-validate directly here.**

**STRICT RULE — size the test run in every minion brief.** Before briefing a builder/mason/bug-hunter, decide how much testing the change actually needs and write it into the brief explicitly. Default: build + lint + typecheck + ONLY the test files covering the touched code. A full suite run is the exception, not the closing ritual: request it only for cross-cutting changes (shared core, schema, config loading, build system) or before a release, and say so in the brief with the reason. Never write "run the full suite once at the end" by reflex: in ai-soul the full suite alone takes ~25 minutes and makes the operator wait for nothing. If a full run is needed, it is a separate, deliberate step after the minion has reported, not part of its loop.

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
