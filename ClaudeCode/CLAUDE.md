
## Temporary Files

**STRICT RULE**: Temporary files MUST be placed ONLY in the `.claude/tmp/` directory. NO EXCEPTIONS. NO EXCUSES. If you create a temporary file anywhere else, you are violating this rule. Create the directory if it doesn't exist.

## Minion Agents — ALWAYS Delegate Iterative Work

**STRICT RULE**: Never run iterative loops (build+fix, script retries, broad exploration) directly in the main context. Always delegate to the appropriate minion agent via the Task tool.

| Agent | When to use |
|---|---|
| `p:minion-explore` | Multi-round file search, broad codebase exploration, subsystem understanding — INSTEAD of Glob/Grep/Read loops |
| `p:minion-runner` | Run-fix-retry loops for scripts/commands — INSTEAD of inline script iteration |
| `p:minion-builder` | Build+test+fix cycles — INSTEAD of inline compile/test iteration |

The main context sees only the final result. **Never run-fix-retry or explore-explore-explore directly here.**

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
