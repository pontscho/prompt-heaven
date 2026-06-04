---
name: p:security-review
description: Multi-mode security audit. CODE mode runs a 3-phase pipeline (triage → find → verify → assemble) with each phase in a fresh sub-agent context to break single-pass anchoring bias (OWASP Top 10, secrets, language-specific patterns, framework-aware FP suppression). PLAN mode runs a single-pass plan-audit inline (no sub-agents — markdown intent review). Mode is auto-inferred from target shape and overridable via `--mode plan|code`. Invoke as `/p:security-review <target> [--mode plan|code] [--branch] [--output console|markdown|both] [--severity high|medium|low] [--include-deps]`, or call from other skills (feature-plan Phase B, implement Phase B) via the Skill tool.
---

# Security Review — Multi-Mode

This skill performs security audits in one of two modes:

- **CODE mode** — audits source code (file / directory / branch diff) via a **3-phase pipeline** (triage → find → verify → assemble). Each phase runs in a fresh sub-agent context to break the anchoring bias of single-pass review. This is the 2025-2026 industry pattern (Sentry, Trail of Bits, Anthropic).
- **PLAN mode** — audits a markdown implementation plan via a **single-pass inline pass** (triage + plan-audit + assemble). No sub-agents — markdown intent review is fast enough to run in the host context. Used by `/p:feature-plan` Phase B before any code exists.

Mode is auto-inferred from the target shape (see Step 0); explicit `--mode` overrides.

See `ClaudeCode/ARCHITECTURE.md` for the layer contract this skill follows, and `skills/_lib/handoff-contracts.md` for the file I/O contract.

## Parameters

| Param | Default | Description |
|---|---|---|
| `target` | — (required unless `--branch`) | File, directory, list of paths, or a markdown plan file |
| `--mode` | auto | `plan`, `code`, or `auto` (infer from target). Auto: `.md` file with `plan` in the name → plan; everything else → code |
| `--branch` | off | Audit the current branch diff vs the main branch (implies `code` mode) |
| `--output` | `console` | `console`, `markdown`, or `both` |
| `--severity` | `all` | Minimum severity to surface: `critical`, `high`, `medium`, `low` |
| `--include-deps` | `false` | Include a dependency-manifest audit (code mode only) |

## Mode Overview

```
                              /p:security-review <target> [flags]
                                         │
                              ┌──────────┴──────────┐
                              ▼                     ▼
                         PLAN mode              CODE mode
                         (inline)               (3-phase pipeline)
                              │                     │
                              ▼                     ▼
                Step 1: Triage (inline)      Step 1: Triage  ─→ Agent(minion, PHASE: triage)
                              │                     │  (fresh context)
                              ▼                     ▼
                Step 2: Plan-Audit (inline)  Step 2: Find    ─→ Agent(minion, PHASE: find)
                              │                     │  (fresh context, writes findings tmp)
                              │                     ▼
                              │             Step 3: Verify  ─→ Agent(minion, PHASE: verify)
                              │                     │  (fresh context, writes verified tmp)
                              ▼                     ▼
                Step 4: Assemble (inline) ←─┴─→ Step 4: Assemble (inline)
                              │
                              ▼
                console block + optional docs/reviews/security-review-<ts>.md
```

## YOU LOVE YOUR MINIONS (code-mode only)

In CODE mode you are an orchestrator, not a security analyst. You do NOT inspect code yourself. **Every of Steps 1–3 delegates to `p:minion-security-officer` via the Agent tool**, each in a fresh sub-agent context. Your job is wiring: pass the right `PHASE:` directive and files, parse compact verdicts, write a final summary.

In PLAN mode there is no sub-agent — the plan audit is short enough to run inline in the host context (no anchoring bias to worry about because there is no code path tracing, only intent review).

## Instructions

### Step 0 — Setup (both modes)

1. **Resolve mode:**
   - If `--mode plan` or `--mode code` was passed explicitly → use it.
   - If `--branch` was passed → `mode = code` (force).
   - Otherwise (mode = auto):
     - If `target` is a single `.md` file AND its basename contains `plan` (case-insensitive, e.g. `feature-implementation-plan.md`, `architecture-plan.md`) → `mode = plan`.
     - Otherwise → `mode = code`.

2. **Resolve `<target>`:**
   - `--branch` → pass the literal flag `--branch` downstream; the minion will resolve via `git_call`.
   - Otherwise → pass the resolved path(s) verbatim.

3. **Compute `<ts>` = `YYYYMMDD-HHMMSS`** (e.g. `20260601-141507`). Use this same timestamp for all tmp / report files this run.

4. **Ensure `.claude/tmp/` exists** (global temp-file rule).

5. **Decide the target description string** for the report header:
   - `--branch` → `"Branch diff (<base>...HEAD)"`
   - One plan file → `"Plan: <path>"`
   - One code file → `"File: <path>"`
   - One directory → `"Directory: <path>"`
   - Multiple paths → `"Paths: <p1>, <p2>, ..."`

6. **Branch on mode:**
   - `mode = plan` → continue with **PLAN MODE WORKFLOW** below.
   - `mode = code` → continue with **CODE MODE WORKFLOW** below.

---

## PLAN MODE WORKFLOW (single-pass, inline, no sub-agents)

The plan audit is intent-review on markdown — fast enough to do inline. No `Agent` tool invocations. No `.claude/tmp/` intermediate files. Just Read the plan, run the checks in the host context, assemble the report.

### Step 1 — Triage (plan, inline)

Read the plan file via the `Read` tool. Run the 12-domain threat-surface checklist on the plan's **stated intentions**:

1. Authentication — login, signup, password handling, token issuance, session management
2. Authorization — access control, role checks, permission gates, ownership checks
3. Cryptography — hashing, encryption, signing, key handling, random generation
4. Network I/O — new HTTP endpoints, new outbound calls, websockets, gRPC, raw sockets
5. User input — form handlers, query params, request bodies, headers parsed, file uploads, URL parsing
6. File system / path handling — file reads/writes with user-influenced paths, archive extraction, temp files
7. IPC / concurrency — shared memory, pipes, mutexes, atomics, signal handlers
8. Secret storage — credentials, tokens, API keys, certificates
9. External dependencies — new third-party libraries, new external services
10. Logging / telemetry — anything that records data, potential PII exposure
11. Serialization / deserialization — JSON, XML, YAML, pickle, protobuf, custom binary formats
12. Memory safety (C/C++) — manual allocation, pointer arithmetic, buffer manipulation

**If NONE are touched** → fast-path APPROVE. Jump to Step 4 (Assemble) with the fast-path template.

**If ANY are touched** → record the list of triggered domains, continue to Step 2.

Emit ONE compact user message:

```
**Security review (plan-mode) — Step 1: TRIAGE**

- Verdict: NO_THREAT_SURFACE / THREAT_DOMAINS
- Domains: [list, or "none"]
- Next: [fast-path APPROVE → done | proceed to Plan-Audit]
```

### Step 2 — Plan-Audit (inline)

For each triggered domain, run the relevant OWASP Top 10 plan-mode questions on the plan's text. The plan is markdown — you cannot trace data flows or check framework defaults, so this is intent-level review: does the plan name an unsafe approach, omit a required mitigation, or commit to a choice with known vulnerabilities?

**A01 — Broken Access Control:** Are new endpoints/operations specified with explicit authorization rules? Are ownership checks called out? Are sensitive operations restricted?

**A02 — Cryptographic Failures:** What hash for passwords (bcrypt/scrypt/argon2/PBKDF2 only — anything else is HIGH)? Where are keys stored? What random source for security tokens (must be CSPRNG, not `Math.random()` / `rand()`)?

**A03 — Injection:** Does the plan reference string-concatenated SQL, `eval`-class functions, shell-out with user data, template engines without auto-escape, header construction from user input?

**A04 — Insecure Design:** Auth endpoint without rate-limit plan? Predictable IDs (sequential ints exposed)? No CAPTCHA / lockout strategy? Privileged operations without idempotency keys?

**A05 — Security Misconfiguration:** Debug flags toggled in prod paths? Default credentials referenced? Verbose error pages? Missing security headers (CSP, HSTS, X-Frame-Options)?

**A06 — Vulnerable & Outdated Components:** New deps with known CVEs? Pinned to vulnerable versions? Abandoned libraries? Flag as INFO unless the plan explicitly names a vulnerable version.

**A07 — Identification & Authentication Failures:** Weak password policy stated? No MFA option? Session fixation possible? Insecure token storage (localStorage for sensitive tokens; no httpOnly cookie)?

**A08 — Software & Data Integrity Failures:** Insecure deserialization (`pickle`, Java native serialization, `Marshal`, `unserialize`, `yaml.load` without safe loader)? Auto-update without signature verification?

**A09 — Security Logging & Monitoring Failures:** Auth events logged? Sensitive operations audited? PII filtered from logs? Log injection considered (user input concatenated into log strings)?

**A10 — Server-Side Request Forgery (SSRF):** Any feature that fetches a user-provided URL? Plan to validate hostnames / restrict to allowlist? Cloud metadata endpoint protection (`169.254.169.254`)?

For each finding, record:
- OWASP category
- CWE ID
- Severity (CVSS bands: see Severity table below)
- Plan-section anchor (which `## Section` or `### Subsection` the issue lives in)
- Remediation recommendation (concrete, not "consider …")

**Severity bands (CVSS 3.1):**
- CRITICAL (9.0–10.0): RCE-class, exploitable injection, auth bypass, hardcoded production secret
- HIGH (7.0–8.9): Stored XSS, SSRF, path traversal with file read, insecure deserialization, privilege escalation, exposed admin endpoint without auth
- MEDIUM (4.0–6.9): Reflected XSS, CSRF, missing rate-limit on auth, weak crypto (MD5/SHA1 for non-password), info disclosure
- LOW (0.1–3.9): Missing security headers, verbose errors, outdated non-critical dep, minor info leak
- INFO: Observation only, no action required

Emit ONE compact user message:

```
**Security review (plan-mode) — Step 2: PLAN-AUDIT**

- Findings: C=<n_c>, H=<n_h>, M=<n_m>, L=<n_l>, I=<n_i>
- Top issue: <one-liner from highest-severity finding, with OWASP/CWE>
- Next: assemble final report
```

### Step 4 — Assemble (plan mode) → jump to the common ASSEMBLE section below

PLAN mode skips Step 3 (Verify) entirely — there is no Find phase to verify, and intent review is single-pass by nature. Go straight to the **Assemble** section below.

---

## CODE MODE WORKFLOW (3-phase pipeline, fresh sub-agent per phase)

### Step 1 — Triage (code, Agent invocation, fresh context)

Invoke the minion via the Agent tool with:

- `subagent_type`: `p:minion-security-officer`
- `description`: `"Security triage"`
- `prompt` (verbatim, substituting the bracketed values):

```
PHASE: triage

TARGET: [target description string from Step 0]
SCOPE: [either a list of paths, or the literal "--branch" flag]
TIMESTAMP: [<ts>]

Operate ONLY in triage mode. Run the 12-domain threat-surface checklist (auth, authz, crypto, network I/O, user input, file-system / path handling, IPC / concurrency, secret storage, external dependencies, logging / telemetry, serialization, memory safety). Do NOT run the full OWASP pass. Do NOT score severities. Do NOT write any files.

Return ONLY one of these two blocks:

  triage-verdict: NO_THREAT_SURFACE
  reason: <one short sentence>

  — OR —

  triage-verdict: THREAT_DOMAINS
  domains: [<comma-separated subset of the 12-domain list>]
  scope-files: [<comma-separated list of files in scope>]
```

Parse the block:

- **`NO_THREAT_SURFACE`** → skip Step 2 and Step 3. Proceed to ASSEMBLE with the fast-path APPROVE template.
- **`THREAT_DOMAINS`** → capture `domains` and `scope-files`, continue to Step 2.

Emit ONE compact user message:

```
**Security review (code-mode) — Step 1: TRIAGE**

- Verdict: NO_THREAT_SURFACE / THREAT_DOMAINS
- Domains: [list, or "none"]
- Scope files: N
- Next: [fast-path APPROVE → done | proceed to FIND]
```

### Step 2 — Find (code, fresh Agent invocation)

NEW `Agent` invocation — fresh sub-agent, no memory of triage.

- `subagent_type`: `p:minion-security-officer`
- `description`: `"Security find pass"`
- `prompt` (verbatim):

```
PHASE: find

TARGET: [target description string]
SCOPE: [paths or --branch]
TIMESTAMP: [<ts>]
TRIAGE_DOMAINS: [<comma-separated domain list from triage>]
SCOPE_FILES: [<comma-separated file list from triage>]
INCLUDE_DEPS: [true | false]

You are the GENEROUS FINDER. Run the OWASP Top 10 pass and language-specific patterns scoped to the triage domains. For EVERY plausible sink:

1. Identify the sink (file:line via clangd/luals/purity — never text search where an LSP applies).
2. Trace UPSTREAM: source → propagator(s) → sink. Use clangd_find_references / luals_find_references / purity search_for_pattern. Record the trace.
3. Flag the finding if it is even PLAUSIBLY exploitable — the Verifier filters false positives, not you.

DO NOT assign severities. DO NOT suppress based on framework defaults — the Verifier handles both.

Write the findings to:
  .claude/tmp/security-findings-[<ts>].md

(See your prompt body for the exact FIND OUTPUT format.)

After writing the file, return ONLY this block:

  find-result: COMPLETE
  findings-file: .claude/tmp/security-findings-[<ts>].md
  count: <integer>
  domains-covered: [<list>]
```

Capture `findings_file` and `count`. If `count == 0` → skip Step 3 → ASSEMBLE with clean APPROVE.

Emit ONE compact user message:

```
**Security review (code-mode) — Step 2: FIND**

- Findings file: `.claude/tmp/security-findings-<ts>.md`
- Raw findings: N
- Top hypothesis: [one-liner from [F1], or "none — clean find pass"]
- Next: [proceed to VERIFY | skip to ASSEMBLE]
```

### Step 3 — Verify (code, fresh Agent invocation)

NEW `Agent` invocation — fresh sub-agent, no memory of triage OR find. Reads the findings file from disk and re-judges every item from scratch.

- `subagent_type`: `p:minion-security-officer`
- `description`: `"Security verify pass"`
- `prompt` (verbatim):

```
PHASE: verify

TIMESTAMP: [<ts>]
FINDINGS_FILE: .claude/tmp/security-findings-[<ts>].md
TARGET: [target description string]

You are the PARANOID VERIFIER. You have NO memory of how the Finder reasoned. Read the findings file with `read_file`, then for EACH finding [Fn], run:

1. REACHABILITY check — does the trace bottom out at real untrusted input?
2. FRAMEWORK-DEFAULT SUPPRESSION check — consult the FP-SUPPRESSION LIBRARY in your prompt body.
3. CONFIDENCE score (HIGH / MEDIUM / LOW).
4. SEVERITY assignment (only here, using CVSS bands).

Verdict per finding: VERIFIED | SUPPRESSED | ESCALATED.

Write the verified report to:
  .claude/tmp/security-verified-[<ts>].md

(See your prompt body for the exact VERIFY OUTPUT format with VERIFIED / SUPPRESSED / ESCALATED sections, preserving original [Fn] IDs.)

After writing the file, return ONLY this block:

  verify-result: COMPLETE
  verified-file: .claude/tmp/security-verified-[<ts>].md
  verified: N_v
  suppressed: N_s
  escalated: N_e
  critical: N_c
  high: N_h
  medium: N_m
  low: N_l
  info: N_i
```

Capture the counts and `verified_file`.

Emit ONE compact user message:

```
**Security review (code-mode) — Step 3: VERIFY**

- Verified file: `.claude/tmp/security-verified-<ts>.md`
- Verified: <N_v>   Suppressed: <N_s>   Escalated: <N_e>
- Severity mix: C=<n_c> H=<n_h> M=<n_m> L=<n_l> I=<n_i>
- Next: assemble final report
```

---

## Step 4 — Assemble (BOTH modes, inline)

The only step that runs in the host context regardless of mode. Pure formatting — no analysis.

1. **Read the source of findings:**
   - PLAN mode → use the findings recorded inline during Step 2 Plan-Audit
   - CODE mode → Read `.claude/tmp/security-verified-<ts>.md`
2. **Compute the overall verdict:**
   - **REJECT** if any VERIFIED CRITICAL finding
   - **REVISE** if any VERIFIED HIGH finding, OR ≥2 VERIFIED MEDIUM findings in the same OWASP category
   - **APPROVE** otherwise
3. **Apply the `--severity` filter** to the displayed-findings list. Always report counts in full.
4. **Emit the console block** (template below).
5. **If `--output` is `markdown` or `both`** → write `docs/reviews/security-review-<ts>.md` (template below). Create the directory if it doesn't exist.

### Fast-path output (Triage NO_THREAT_SURFACE)

When Step 1 returns `NO_THREAT_SURFACE`, skip remaining steps and emit:

```
p:security-review

Target: <target description>
Mode:   <PLAN | CODE>

----------------------------------------

Triage:   NO_THREAT_SURFACE
Verdict:  APPROVE (fast-path)
Reason:   <reason from triage>

No further audit performed — no security-relevant surface introduced.
```

If `--output` includes `markdown`, still write a short report at `docs/reviews/security-review-<ts>.md` carrying the fast-path verdict for the audit trail.

### Standard console output

```
p:security-review

Target: <target description>
Mode:   <PLAN | CODE>
Files:  <N in scope> (<language(s)>, <framework(s) if detected>)

----------------------------------------

Triage:   THREAT_DOMAINS [<domains>]
Verdict:  <APPROVE | REVISE | REJECT>

Findings (verified only):
  CRITICAL: <n_c>
  HIGH:     <n_h>
  MEDIUM:   <n_m>
  LOW:      <n_l>
  INFO:     <n_i>

<code-mode only:>
Suppressed by Verifier: <n_s>  (false-positive filter applied)
Escalated for human triage: <n_e>

----------------------------------------

VERIFIED — CRITICAL [<count>]

  [<file>:<line>] <title>  (OWASP A0X | CWE-XXX | CVSS X.X | Confidence HIGH/MEDIUM/LOW)
  Impact: <one line>
  Fix:    <one line>

VERIFIED — HIGH [<count>]
  ... (same per-finding format)

VERIFIED — MEDIUM [<count>]
  ...

VERIFIED — LOW [<count>]
  ...

(omit any severity section with 0 findings; apply --severity filter)

----------------------------------------

<code-mode only — if any:>
SUPPRESSED [<count>]  (transparent for review)

  [<file>:<line>] <title> — <suppression reason>
  ...

ESCALATED [<count>]  (REQUIRES HUMAN TRIAGE)

  [<file>:<line>] <title>
  → Why I couldn't decide: <one line>
  → What to check: <one line>

----------------------------------------

Verdict: <APPROVE | REVISE | REJECT>
<code-mode artifacts:>
Reports: .claude/tmp/security-findings-<ts>.md
         .claude/tmp/security-verified-<ts>.md
<if --output includes markdown:>
Full report: docs/reviews/security-review-<ts>.md
```

### Markdown output

When `--output` is `markdown` or `both`, write `docs/reviews/security-review-<ts>.md` with:

```markdown
# Security Review: <target description>

| Property | Value |
|---|---|
| Target | <target> |
| Mode | <PLAN | CODE> |
| Files in scope | <N> |
| Languages | <list> |
| Framework(s) | <list, if detected> |
| Date | <YYYY-MM-DD HH:MM:SS> |
| Pipeline | <plan single-pass | code: triage → find → verify → assemble (3-phase)> |

## Verdict: <APPROVE | REVISE | REJECT>

<one-line justification anchored to highest-severity verified finding, or "no threat surface identified" / "all findings suppressed">

## Triage

- Verdict: <NO_THREAT_SURFACE | THREAT_DOMAINS>
- Domains touched: <list, or "none">
- Scope files: <N>

## Risk Summary (VERIFIED only)

| Severity | Count | Action |
|---|---|---|
| Critical | <n_c> | Must fix before merge |
| High | <n_h> | Fix before deployment |
| Medium | <n_m> | Fix soon |
| Low | <n_l> | Consider fixing |
| Info | <n_i> | Awareness only |

<code-mode only:>
## False-Positive Filter

| Bucket | Count |
|---|---|
| Verified (real) | <n_v> |
| Suppressed (FP filtered by Verifier) | <n_s> |
| Escalated (human triage needed) | <n_e> |

## Verified Findings

### Critical
<for each VERIFIED CRITICAL:>
#### [F<id>] <title>
- **File / Plan-section:** `path:line` (code) or `## Section` (plan)
- **OWASP:** A0X:2021 — <category>
- **CWE:** CWE-XXX
- **CVSS:** X.X (<severity>)
<code-mode only:>
- **Confidence:** HIGH / MEDIUM / LOW
- **Confirmed trace:** source → propagator(s) → sink (1-3 lines)
- **Evidence:**
  ```<lang>
  <vulnerable code, 1-10 lines>
  ```
- **Impact:** <attack scenario>
- **Remediation:**
  ```<lang>
  <fixed code or design change>
  ```

### High / Medium / Low / Info — same structure

<code-mode only:>
## Suppressed Findings (transparency log)

| ID | Title | File:Line | Suppression reason |
|---|---|---|---|
| [F<id>] | <title> | `path:line` | <one-line reason, w/ FP-SUPPRESSION-LIB entry name where applicable> |

## Escalated Findings (HUMAN TRIAGE REQUIRED)

### [F<id>] <title>
- **File:** `path:line`
- **Why I couldn't decide:** <one-liner>
- **What a human should check:** <one-liner>
- **Original Finder hypothesis:** <copy from findings file>

## Pipeline Artifacts (code-mode only)

- Phase 2 (Find) raw findings: `.claude/tmp/security-findings-<ts>.md`
- Phase 3 (Verify) report: `.claude/tmp/security-verified-<ts>.md`

## Checklist

- [ ] All VERIFIED CRITICAL findings resolved
- [ ] All VERIFIED HIGH findings resolved
- [ ] ESCALATED findings reviewed by a human (decision recorded)
- [ ] Any secrets detected → rotated, removed, git history audited
- [ ] Suppression decisions spot-checked (Verifier sometimes over-suppresses)
```

## Examples

### Code-mode: scan a directory

```
/p:security-review src/auth/
```

→ Triage hits (auth domain). Find produces ~6 candidate findings. Verify suppresses 3 (Django ORM, Jinja2 autoescape, parameterized query), escalates 1 (dynamic SpEL evaluation), confirms 2 (HIGH JWT-in-localStorage, MEDIUM missing rate-limit). Verdict: REVISE.

### Code-mode: scan branch

```
/p:security-review --branch
```

→ Triage detects which domains the branch touched; find/verify scoped accordingly.

### Plan-mode (auto-detected)

```
/p:security-review docs/feature-implementation-plan.md
```

→ Auto-mode detects `.md` + `plan` in basename → `mode=plan`. Single-pass inline audit. No `.claude/tmp/` files.

### Plan-mode (explicit)

```
/p:security-review docs/architecture-proposal.md --mode plan
```

→ Force plan mode on a non-pattern-matching markdown file.

### Code-mode (explicit on a `.md` file)

```
/p:security-review README.md --mode code
```

→ Force code-mode audit on a markdown file (e.g. it contains embedded code blocks).

### Pure-markdown skill — fast-path

```
/p:security-review ClaudeCode/skills/p:purity-mcp/SKILL.md
```

→ Triage returns NO_THREAT_SURFACE (markdown documentation, no code). Verdict APPROVE (fast-path).

### Full report + dependency audit (code-mode only)

```
/p:security-review src/ --output both --include-deps
```

### Filtered display

```
/p:security-review src/auth/ --severity high
```

→ All counts reported; displayed VERIFIED list filtered to HIGH/CRITICAL only. Suppressed and Escalated always shown in full.

## Invariants

- **Three Agent invocations, three fresh contexts (CODE mode only).** Never collapse phases. Never run the audit inline in CODE mode.
- **PLAN mode is inline, no Agent invocations.** A markdown intent audit doesn't benefit from sub-agent isolation.
- **Disk handoff between phases (CODE mode only).** Inter-phase data lives in `.claude/tmp/security-{findings,verified}-<ts>.md`.
- **Compact per-step user message.** One short status block per step — never dump full reports.
- **Severity is assigned in VERIFY (code) or in Plan-Audit (plan).** The Finder must not score (code). The orchestrator must not re-score.
- **Verdict math is consistent.** REJECT on any VERIFIED CRITICAL; REVISE on HIGH or duplicated MEDIUM; otherwise APPROVE.
- **ESCALATED findings (code-mode) always surfaced.** They don't affect verdict counts, but the user must see them.
- **Tmp files survive the run.** Do not delete `.claude/tmp/security-*` — they are the audit trail.
- **Step nomenclature.** This skill has Steps 1–4 (linear). It has NO Phase A/B/C (no validation loops). See `ClaudeCode/ARCHITECTURE.md`.
