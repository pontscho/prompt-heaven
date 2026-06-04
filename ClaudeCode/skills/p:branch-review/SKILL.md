---
name: p:branch-review
description: Review current branch changes against base branch (master/main). Analyzes diff, commits, code quality, and project rule compliance. Use when reviewing branch before PR creation or merge. Trigger: /p:branch-review [base-branch]
---

# Branch Review

Review the current branch against a base branch (master/main) before PR creation or merge.

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| base | auto-detect | Base branch to compare against (master/main/trunk) |
| --output | console | `console`, `markdown`, or `both` |
| --severity | all | Minimum severity: `critical`, `warning`, `info` |

## Instructions

### Step 1: Setup

1. Verify we are in a git repository
2. Detect base branch (priority: master > main > trunk)
3. If base explicitly provided, use that
4. Verify current branch is not the base branch

### Step 2: Gather Context

#### Git Information

Run these commands to gather diff and commit info:

```bash
# Get base branch
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "master"

# Diff stats
git diff <base>...HEAD --stat

# Full diff
git diff <base>...HEAD

# Commit log
git log <base>..HEAD --oneline

# Changed files list
git diff <base>...HEAD --name-only
```

#### Project Rules

Search for and read these config files if they exist:

- `.editorconfig`
- `.eslintrc*`, `.prettierrc*`, `pyproject.toml`, `.clang-format`
- `CLAUDE.md`, `.github/instructions/*.md`

Apply these rules during review.

### Step 3: Analysis Categories

#### A. Commit Hygiene
- Commit message quality (clear, descriptive)
- Logical commit structure (atomic commits)
- No WIP/fixup/temp commits that should be squashed

#### B. Code Quality
- Clean code principles
- DRY violations (duplicated code)
- SOLID principles (where relevant)
- Complexity (functions >50 lines, deep nesting >4 levels)
- Dead code

#### C. Style Compliance
- Project lint rules
- Naming conventions
- Formatting (if no auto-formatter)

#### D. Error Handling
- Unhandled exceptions
- Missing error feedback
- Swallowed errors

#### E. Tests
- Tests for new/modified code
- Test coverage impact

#### F. Security (basic)
- Obvious vulnerabilities (injection, hardcoded secrets)
- For deeper analysis, recommend /p:security-review

#### G. Breaking Changes
- API changes
- Public interface modifications
- Config format changes

### Step 4: Severity Classification

**CRITICAL:**
- Security vulnerability
- Data loss risk
- Production crash risk

**WARNING:**
- Code quality problems
- Missing tests
- Style violations (project rules)
- Potential bugs

**INFO:**
- Suggestions
- Minor improvements
- Stylistic notes

### Step 5: Generate Output

#### Console Output Format

```
p:branch-review

Branch: <current-branch>
Base:   <base-branch>
Ahead:  <N> commits | <M> files | +<added> -<removed> lines

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
Verdict: <APPROVED | CHANGES REQUESTED | REJECTED>

Full report: docs/reviews/branch-review-<timestamp>.md
```

#### Verdict Logic

- REJECTED: Any critical finding
- CHANGES REQUESTED: Any warning finding
- APPROVED: Only info or no findings

### Step 6: Generate Markdown Report (if --output includes markdown)

Create file at `docs/reviews/branch-review-<YYYY-MM-DD-HHMMSS>.md`:

```markdown
# Branch Review

| Property | Value |
|----------|-------|
| Branch | <current-branch> |
| Base | <base-branch> |
| Commits | <N> |
| Files | <M> |
| Changes | +<added> -<removed> |
| Date | <YYYY-MM-DD HH:MM:SS> |

## Verdict: <VERDICT>

| Severity | Count |
|----------|-------|
| Critical | <N> |
| Warning | <M> |
| Info | <K> |

## Commits

| Hash | Message | Files |
|------|---------|-------|
| <hash> | <message> | <N> |

## Findings

### Critical

#### 1. <Title>
- **File:** <path>:<line>
- **Category:** <category>

<description>

\`\`\`<lang>
// Current (problematic)
<code>

// Suggested fix
<code>
\`\`\`

### Warning

...

### Info

...

## Project Rules Applied

- <rule source>: <relevant rules>

## Checklist

- [ ] Critical issues fixed
- [ ] Warnings reviewed
- [ ] Tests added
- [ ] Docs updated (if needed)
```

## Examples

### Basic usage
```
/p:branch-review
```
Reviews current branch against auto-detected base (master/main).

### Specify base branch
```
/p:branch-review develop
```
Reviews current branch against develop.

### Full report
```
/p:branch-review --output both
```
Outputs to console and creates markdown report.

### Filter severity
```
/p:branch-review --severity warning
```
Only shows warning and critical findings.
