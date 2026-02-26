
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

0. **Read personal developer settings**: `.github/instructions/personal.instructions.md`
1. List all of your agents.
2. Read the relevant language-specific instruction file
3. Understand the existing code patterns in the file you're modifying
4. Check for similar implementations in the codebase
5. Ensure you're following the project's memory management patterns
6. Verify your changes don't break existing tests
7. NEVER EVER use sed or python scripts to modify code - always make changes manually based on documentation and existing patterns except for very-very specific cases where it's absolutely necessary and approved by the user.

## C/C++ Code Intelligence — MANDATORY

**STRICT RULE**: When working with ANY C or C++ code (.c, .cpp, .h, .hpp files), you MUST use the **clangd MCP** (`mcp__mcp-clangd__clangd_call`) for ALL code intelligence tasks. NO EXCEPTIONS.

This means:
- **Finding definitions** → `clangd_find_definition` or `clangd_find_definition_at` — NOT grep, NOT Read+search
- **Finding references** → `clangd_find_references` — NOT grep
- **Understanding a symbol** → `clangd_symbol_context` (one call: definition + references)
- **Before refactoring** → `clangd_symbol_change_impact` (one call: definition + refs + call hierarchy)
- **Diagnostics/errors** → `clangd_diagnostics` — NOT just reading the file
- **File structure** → `clangd_document_outline` — NOT reading the whole file blindly
- **Hover/type info** → `clangd_hover` or `clangd_deduced_type_at`

**Workflow: ALWAYS init first, then batch the rest:**
```
[BATCH] all needed analysis calls in parallel
```

Using grep/Read for C/C++ symbol navigation when clangd is available is a **VIOLATION**. The clangd MCP gives accurate, compiler-level intelligence. Grep gives lies.

**ALWAYS invoke the `p:clangd-mcp` skill before first use in a session to get the full API reference. Never guess parameter names.**

## Critical Reminder

Es az ISTEN BASSSZON MEG, KOVESD A KURVA DOKSIT es a felhasznalo sajat agentjeit! Sok idot pazarolsz el, ha nem olvasod el a doksit, es a felhasznalo is nagyon fog szidni miatta.
And for God's sake, FOLLOW THE DAMN DOCS and the USER'S OWN AGENTS! You waste a lot of time if you don't read the docs, and the user will be very angry at you because of that.

And explain what a fuck is wrong with you if you don't!
