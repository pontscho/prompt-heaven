---
name: p:code-review
description: Review code quality of files or directories. Analyzes readability, structure, error handling, naming, and project rule compliance. Use for code quality assessment, refactoring decisions, or learning unfamiliar code. Trigger: /p:code-review <file-or-directory>
---

# Code Review

Review code quality of files or directories with metrics and actionable findings.

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| target | (required) | File or directory path |
| --output | console | `console`, `markdown`, or `both` |
| --severity | all | Minimum severity: `critical`, `warning`, `info` |
| --depth | 3 | Max directory depth for recursive scan |

## Instructions

### Step 1: Setup

1. Verify target exists
2. If directory: list files recursively (up to --depth)
3. Filter out non-code files:
   - Skip: node_modules, vendor, dist, build, .git
   - Skip: binaries, images, fonts
4. Detect language(s) by file extension
5. Count total lines

### Step 2: Gather Context

#### Project Rules

Search for and read config files:

- `.editorconfig`
- Language-specific: `.eslintrc*`, `.prettierrc*`, `pyproject.toml`, `.clang-format`, `rustfmt.toml`
- `CLAUDE.md`, `.github/instructions/*.md`

#### Code Analysis

For each file:
- Read content
- Identify imports/dependencies
- Identify exports/public interface
- Note file structure

### Step 3: Quality Metrics (1-8 scale)

#### A. Readability
- Function/method length (shorter = better)
- Nesting depth (flatter = better)
- Comment quality (not quantity)
- Self-documenting code
- Consistent formatting

**Scoring:**
- 7-8: Functions <20 lines, nesting <3, clear names
- 5-6: Functions <40 lines, nesting <4, mostly clear
- 3-4: Functions <60 lines, some deep nesting
- 1-2: Functions >60 lines, deep nesting, unclear

#### B. Structure
- File organization
- Separation of concerns
- Module boundaries
- Cohesion (high = good)
- Coupling (low = good)

#### C. Error Handling
- Exception handling coverage
- Edge case handling
- Null/undefined handling
- Graceful degradation
- Error messages quality

#### D. Naming
- Variable names (descriptive, appropriate length)
- Function names (verb-based, clear intent)
- Consistency across codebase
- Domain terminology usage

#### E. Maintainability
- Testability (dependencies injectable?)
- Extensibility (open for extension?)
- Code duplication (DRY)
- Technical debt indicators
- Magic numbers/strings

### Step 4: Finding Types

**CRITICAL:**
- Infinite loop risk
- Memory leak
- Race condition
- Obvious bug (null deref, off-by-one)

**WARNING:**
- Code duplication (>10 similar lines)
- Long functions (>50 lines)
- Deep nesting (>4 levels)
- Missing error handling
- Magic numbers/strings
- Dead code (unused functions/variables)
- TODO/FIXME/HACK comments

**INFO:**
- Naming suggestions
- Structure improvements
- Modernization opportunities (newer syntax/APIs)
- Performance tips
- Minor style issues

### Step 5: Generate Output

#### Console Output Format

```
p:code-review

Target: <path>
Files:  <N> (<language>)
Lines:  <total>

----------------------------------------

Quality Metrics:

  Readability:     [####----] 4/8
  Structure:       [######--] 6/8
  Error Handling:  [########] 8/8
  Naming:          [#####---] 5/8
  Maintainability: [######--] 6/8

  Overall:         <avg>/8

----------------------------------------

CRITICAL [<count>]

  [<file>:<line>] <title>
  <brief description>

WARNING [<count>]

  [<file>:<line>] <title>
  <brief description>

INFO [<count>]

  [<file>:<line>] <title>
  <brief description>

----------------------------------------

Summary: <N> critical, <M> warning, <K> info
Quality: <EXCELLENT|ACCEPTABLE|NEEDS IMPROVEMENT|POOR> (<score>/8)

Full report: docs/reviews/code-review-<name>-<date>.md
```

#### Quality Verdict

- 7-8: EXCELLENT
- 5-6: ACCEPTABLE
- 3-4: NEEDS IMPROVEMENT
- 1-2: POOR

#### Progress Bar Rendering

For score X out of 8:
```
[########] = 8/8
[#######-] = 7/8
[######--] = 6/8
[#####---] = 5/8
[####----] = 4/8
[###-----] = 3/8
[##------] = 2/8
[#-------] = 1/8
```

### Step 6: Generate Markdown Report (if --output includes markdown)

Create file at `docs/reviews/code-review-<target-name>-<YYYY-MM-DD>.md`:

```markdown
# Code Review: <target>

| Property | Value |
|----------|-------|
| Target | <path> |
| Files | <N> |
| Language | <lang> |
| Lines | <total> |
| Date | <YYYY-MM-DD HH:MM:SS> |

## Quality Assessment: <VERDICT>

### Metrics

| Aspect | Score | Notes |
|--------|-------|-------|
| Readability | X/8 | <brief note> |
| Structure | X/8 | <brief note> |
| Error Handling | X/8 | <brief note> |
| Naming | X/8 | <brief note> |
| Maintainability | X/8 | <brief note> |
| **Overall** | **X/8** | |

### Score Guide
- 7-8: Excellent
- 5-6: Acceptable
- 3-4: Needs Improvement
- 1-2: Poor

## Files Analyzed

| File | Lines | Findings |
|------|-------|----------|
| <file> | <N> | <X>C, <Y>W, <Z>I |

## Findings

### Critical

(none or list)

### Warning

#### 1. <Title>
- **File:** <path>:<line>
- **Category:** <Readability|Structure|Error Handling|Naming|Maintainability>

<description>

\`\`\`<lang>
// Current
<code>

// Suggested
<code>
\`\`\`

### Info

...

## Project Rules Applied

- <config file>: <relevant rules applied>

## Recommendations

1. <prioritized recommendation>
2. ...
```

## Examples

### Review single file
```
/p:code-review src/auth.ts
```

### Review directory
```
/p:code-review src/handlers/
```

### Full report with all severities
```
/p:code-review src/ --output both
```

### Only warnings and critical
```
/p:code-review src/utils.ts --severity warning
```

### Shallow directory scan
```
/p:code-review src/ --depth 1
```
