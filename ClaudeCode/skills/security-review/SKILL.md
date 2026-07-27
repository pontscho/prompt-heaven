---
name: security-review
description: Multi-mode security audit. CODE mode runs a parallel fan-out pipeline (triage → parallel per-lane find → parallel per-finding verify → assemble), every phase in fresh sub-agent contexts to break single-pass anchoring bias (OWASP Top 10, secrets, language-specific patterns, framework-aware FP suppression). Parallelism uses multiple Agent tool-uses per message — no Workflow tool. PLAN mode runs a single-pass plan-audit inline (no sub-agents — markdown intent review). Mode is auto-inferred from target shape and overridable via `--mode plan|code`. Invoke as `/p:security-review <target> [--mode plan|code] [--branch] [--output console|markdown|both] [--severity high|medium|low] [--include-deps]`, or call from other skills (feature-plan Phase B, implement Phase B) via the Skill tool.
---

# Security Review — Multi-Mode

This skill performs security audits in one of two modes:

- **CODE mode** — audits source code (file / directory / branch diff) via a **parallel fan-out pipeline** (triage → parallel per-lane find → parallel per-finding verify → assemble), every sub-agent in a fresh context to break the anchoring bias of single-pass review. This is the 2025-2026 industry pattern (Sentry, Trail of Bits, Anthropic).
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
                         (inline)               (parallel fan-out, no Workflow)
                              │                     │
                              ▼                     ▼
                Step 1: Triage (inline)      Step 1: Triage  ─→ Agent(minion, PHASE: triage)
                              │                     │  (fresh context)
                              ▼                     ▼
                Step 2: Plan-Audit (inline)  Step 2: Find    ─→ K PARALLEL Agents (PHASE: find)
                              │                     │  (one per lane, one message; per-lane tmp)
                              │                     ▼
                              │             Step 3: Verify  ─→ N PARALLEL Agents (PHASE: verify)
                              │                     │  (one per finding, one message; batch if N>8)
                              ▼                     ▼
                Step 4: Assemble (inline) ←─┴─→ Step 4: Assemble (inline)
                              │
                              ▼
                console block + optional docs/reviews/security-review-<ts>.md
```

## YOU LOVE YOUR MINIONS (code-mode only)

In CODE mode you are an orchestrator, not a security analyst. You do NOT inspect code yourself. **Every sub-agent across Steps 1–3 is a `p:minion-inspector-security-officer` invoked via the Agent tool**, each in a fresh context. Step 1 = one triage officer; Step 2 = **K find officers emitted as parallel Agent tool-uses in ONE message** (one per lane); Step 3 = **N verify officers emitted as parallel Agent tool-uses in ONE message** (one per finding, batched if large). Parallelism comes from multiple Agent calls per message — **never use the Workflow tool**. Your job is wiring: pass the right `PHASE:` directive + scope, parse compact return blocks, merge/dedup at the Find barrier, calibrate + assemble.

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

## CODE MODE WORKFLOW (parallel fan-out, fresh sub-agent per lane and per finding)

CODE mode runs a **parallel** pipeline. All sub-agents are `p:minion-inspector-security-officer`. **No Workflow tool** — parallelism = multiple `Agent` tool-uses emitted in a SINGLE assistant message; the harness runs them concurrently and you receive every result before composing the next message (an implicit phase barrier). Inter-phase data uses the disk-handoff contract (`.claude/tmp/...`) plus compact return blocks — only now per-lane (Find) and per-finding (Verify).

This is a **strict superset** of single-pass-per-phase review: a tiny target triages to one lane and a handful of findings, degenerating to ~1 finder + a few verifiers. Scale the lane count to the scope — do NOT spawn 8 lanes for a 50-line diff; collapse to the 2-3 dominant lanes.

### Lane catalog (Find-phase partition)

Triage maps the touched threat domains onto this catalog; each selected lane becomes one parallel find-officer in Step 2.

| Triage domain(s) | Lane key | Focus (CWE / OWASP) |
|---|---|---|
| memory-safety | `oob` | CWE-787/125/119/129 — out-of-bounds R/W, improper array index |
| memory-safety | `intovf` | CWE-190/191/194/369 — integer overflow / signedness, divide-by-zero |
| memory-safety | `memlife` | CWE-457/908/416/401/476/690 — uninit/stale read, UAF, leak, NULL deref, unchecked alloc |
| user-input, serialization | `parseval` | CWE-20/1284 — improper validation of untrusted parsed input |
| user-input | `injection` | A03 — SQL / command / template / header injection, eval-class sinks |
| authentication, authorization | `access-control` | A01/A04/A07 — broken access control, insecure design, auth failures |
| cryptography, secret storage | `crypto` | A02/A08 — weak crypto, key handling, insecure deserialization, integrity |
| network I/O | `ssrf-net` | A10 — SSRF, unvalidated outbound fetch |
| external dependencies, security misconfiguration, logging/telemetry | `config-deps` | A05/A06/A09 — misconfig, vulnerable/outdated deps, logging/PII |

The `oob` / `intovf` / `memlife` / `parseval` lanes are the C/C++ memory-safety specialization; the OWASP-grouped lanes cover managed/web targets. Activate only the lanes whose domains triage actually flagged. Lanes with no live domain are simply not spawned.

### Step 1 — Triage (code, 1 Agent invocation, fresh context)

Invoke the minion via the Agent tool with:

- `subagent_type`: `p:minion-inspector-security-officer`
- `description`: `"Security triage"`
- `prompt` (verbatim, substituting the bracketed values):

```
PHASE: triage

TARGET: [target description string from Step 0]
SCOPE: [either a list of paths, or the literal "--branch" flag]
TIMESTAMP: [<ts>]

Operate ONLY in triage mode. Run the 12-domain threat-surface checklist (auth, authz, crypto, network I/O, user input, file-system / path handling, IPC / concurrency, secret storage, external dependencies, logging / telemetry, serialization, memory safety). Do NOT run the full OWASP pass. Do NOT score severities. Do NOT write any files.

Then map the touched domains onto the orchestrator's LANE CATALOG and recommend the live lane partition — the subset of lane keys whose domains you flagged: oob, intovf, memlife (memory-safety); parseval (user-input / serialization); injection (user-input); access-control (authn / authz); crypto (crypto / secret storage); ssrf-net (network I/O); config-deps (external deps / misconfig / logging). Scale to scope — for a small target collapse to the 2-3 dominant lanes; do NOT recommend a lane whose domain you did not flag.

Return ONLY one of these two blocks:

  triage-verdict: NO_THREAT_SURFACE
  reason: <one short sentence>

  — OR —

  triage-verdict: THREAT_DOMAINS
  domains: [<comma-separated subset of the 12-domain list>]
  lanes: [<comma-separated subset of lane keys from the catalog>]
  scope-files: [<comma-separated list of files in scope>]
```

Parse the block:

- **`NO_THREAT_SURFACE`** → skip Step 2 and Step 3. Proceed to ASSEMBLE with the fast-path APPROVE template.
- **`THREAT_DOMAINS`** → capture `domains`, `lanes`, and `scope-files`, continue to Step 2 (one parallel find-officer per lane).

Emit ONE compact user message:

```
**Security review (code-mode) — Step 1: TRIAGE**

- Verdict: NO_THREAT_SURFACE / THREAT_DOMAINS
- Domains: [list, or "none"]
- Lanes: [lane keys, or "none"]
- Scope files: N
- Next: [fast-path APPROVE → done | proceed to FIND (K parallel lanes)]
```

### Step 2 — Find (code, K PARALLEL Agents in ONE message)

Emit **K `Agent` tool-uses in a SINGLE assistant message** — one per lane in triage's `lanes` list. They run concurrently; you receive all K results before composing the next message. Each is a fresh sub-agent with no memory of triage.

Per lane:

- `subagent_type`: `p:minion-inspector-security-officer`
- `description`: `"Security find — <lane>"`
- `prompt` (verbatim, substituting the lane's focus from the Lane Catalog):

```
PHASE: find

TARGET: [target description string]
SCOPE: [paths or --branch]
TIMESTAMP: [<ts>]
LANE: <lane key> — focus EXCLUSIVELY on <lane focus + CWE/OWASP list from the catalog>.
TRIAGE_DOMAINS: [<domain list from triage>]
SCOPE_FILES: [<file list from triage>]
INCLUDE_DEPS: [true | false]

You are the GENEROUS FINDER for THIS ONE lane. Maximize recall WITHIN your lane only; the other lanes are covered by sibling officers running in parallel right now. For EVERY plausible sink in your lane:

1. Identify the sink (file:line via clangd/luals/purity — never text search where an LSP applies).
2. Trace UPSTREAM: source → propagator(s) → sink. Use clangd_find_references / luals_find_references / purity search_for_pattern. Record the trace.
3. Flag the finding if it is even PLAUSIBLY exploitable — the Verifier filters false positives, not you.

DO NOT assign severities. DO NOT suppress based on framework defaults / existing guards — the Verifier handles both. Stay strictly in your lane.

Write your lane's findings to:
  .claude/tmp/security-findings-[<ts>]-<lane>.md

(See your prompt body for the exact FIND OUTPUT format.)

After writing the file, return ONLY this block:

  find-result: COMPLETE
  lane: <lane>
  findings-file: .claude/tmp/security-findings-[<ts>]-<lane>.md
  count: <integer>
```

**Merge + dedup barrier.** After all K results arrive, read the K lane files, MERGE into one candidate list, and DEDUP by `(file, line, root-cause)` — a single bug surfaced by two lanes must collapse to ONE candidate. Assign stable IDs `F1..FN`. This barrier is mandatory: Verify must see the merged, deduped set.

If the merged total `count == 0` → skip Step 3 → ASSEMBLE with clean APPROVE.

Emit ONE compact user message:

```
**Security review (code-mode) — Step 2: FIND (K parallel lanes)**

- Per-lane: <lane1> N1, <lane2> N2, ...
- Merged + deduped: N candidate(s)  (D duplicates collapsed)
- Top hypothesis: [one-liner from [F1], or "none — clean find pass"]
- Next: [proceed to VERIFY (N parallel) | skip to ASSEMBLE]
```

### Step 3 — Verify (code, N PARALLEL Agents in ONE message, one per finding)

Emit **N `Agent` tool-uses in a SINGLE assistant message** — one per deduped candidate `F1..FN`. If N > 8, split into sequential batches of ≤8 (one message per batch). Each verifier is a fresh sub-agent with NO memory of triage, find, or its siblings, and re-judges its ONE finding from scratch.

Per finding:

- `subagent_type`: `p:minion-inspector-security-officer`
- `description`: `"Security verify — F<k>"`
- `prompt` (verbatim, embedding the ONE finding inline):

```
PHASE: verify

TIMESTAMP: [<ts>]
TARGET: [target description string]

You are the PARANOID VERIFIER. You have NO memory of how the Finder reasoned. Re-judge this ONE finding from scratch by reading the cited code FRESH this invocation.

FINDING [F<k>] (lane <lane>):
  title: <title>
  file: <file>   line: <line>
  cwe (claimed): <cwe>
  sink: <sink>
  claimed trace: <source → propagator(s) → sink>
  finder hypothesis: <why_plausible>

Run, against the ACTUAL current code (Read + clangd/luals/purity — do not trust the finder's quote):
1. REACHABILITY check — does the trace bottom out at real untrusted input?
2. FRAMEWORK-DEFAULT / EXISTING-GUARD SUPPRESSION check — consult the FP-SUPPRESSION LIBRARY in your prompt body; cite the concrete guard (file:line) when you suppress.
3. CONFIDENCE score (HIGH / MEDIUM / LOW).
4. SEVERITY assignment (only here, using CVSS bands).

Do NOT write any files. Return ONLY this block:

  verify-result: <VERIFIED | SUPPRESSED | ESCALATED>
  id: F<k>
  severity: <CRITICAL | HIGH | MEDIUM | LOW | INFO>
  confidence: <HIGH | MEDIUM | LOW>
  cwe: <CWE-xxx>   owasp: <A0x or "N/A">
  evidence: <one actual line you read this invocation, with its line number>
  reason: <one-line justification>
```

Collect the N verdict blocks (one per finding). They are the verified result set — no separate `security-verified` file is written; the consolidated `docs/reviews/...` report (Step 4) is the audit trail.

Emit ONE compact user message:

```
**Security review (code-mode) — Step 3: VERIFY (N parallel, one per finding)**

- Verified: <N_v>   Suppressed: <N_s>   Escalated: <N_e>
- Severity mix: C=<n_c> H=<n_h> M=<n_m> L=<n_l> I=<n_i>
- Next: assemble final report
```

---

## Step 4 — Assemble (BOTH modes, inline)

The only step that runs in the host context regardless of mode. Pure formatting — no analysis.

1. **Collect the source of findings:**
   - PLAN mode → use the findings recorded inline during Step 2 Plan-Audit
   - CODE mode → the N compact verdict blocks returned by the parallel verifiers in Step 3 (already deduped at the Find barrier)
2. **Calibrate severity (CODE mode only).** The N verifiers scored independently — reconcile CVSS bands so near-identical findings (same CWE + adjacent location) don't diverge; on a tie take the higher band and note the reconciliation. Do NOT re-judge the VERIFIED / SUPPRESSED / ESCALATED verdicts themselves.
3. **Compute the overall verdict:**
   - **REJECT** if any VERIFIED CRITICAL finding
   - **REVISE** if any VERIFIED HIGH finding, OR ≥2 VERIFIED MEDIUM findings in the same OWASP category
   - **APPROVE** otherwise
4. **Apply the `--severity` filter** to the displayed-findings list. Always report counts in full.
5. **Emit the console block** (template below).
6. **If `--output` is `markdown` or `both`** → write `docs/reviews/security-review-<ts>.md` (template below). Create the directory if it doesn't exist.

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
Reports: .claude/tmp/security-findings-<ts>-<lane>.md (one per lane)
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
| Pipeline | <plan single-pass | code: triage → parallel per-lane find → parallel per-finding verify → assemble> |

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

- Find phase raw findings, one per lane: `.claude/tmp/security-findings-<ts>-<lane>.md`
- Verify phase verdicts: returned inline as compact blocks (no separate verified file); consolidated into this report

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
/p:security-review ClaudeCode/skills/mcp-purity/SKILL.md
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

- **Parallel fan-out, fresh context per sub-agent (CODE mode only).** 1 triage officer + **K** parallel find officers (one per lane, one message) + **N** parallel verify officers (one per finding, one message; batch if N>8). Never collapse a phase; never run the audit inline in CODE mode.
- **No Workflow tool.** Parallelism = multiple `Agent` tool-uses in one assistant message; the harness runs them concurrently and the next message composes only after all results return (implicit phase barrier).
- **Merge + dedup barrier between Find and Verify.** Collapse `(file, line, root-cause)` duplicates across lanes before verifying; assign stable `F1..FN` IDs.
- **PLAN mode is inline, no Agent invocations.** A markdown intent audit doesn't benefit from sub-agent isolation.
- **Disk handoff at the Find phase (CODE mode only).** Each lane writes `.claude/tmp/security-findings-<ts>-<lane>.md`. Verify returns compact verdict blocks INLINE (no per-finding files); the consolidated `docs/reviews/security-review-<ts>.md` is the verified audit trail.
- **Compact per-step user message.** One short status block per step — never dump full reports.
- **Severity is assigned in VERIFY (code) or in Plan-Audit (plan).** Finders must not score; each verifier scores its own finding; the orchestrator **calibrates** bands across verifiers in Assemble but does not re-judge.
- **Verdict math is consistent.** REJECT on any VERIFIED CRITICAL; REVISE on HIGH or duplicated MEDIUM; otherwise APPROVE.
- **ESCALATED findings (code-mode) always surfaced.** They don't affect verdict counts, but the user must see them.
- **Tmp files survive the run.** Do not delete `.claude/tmp/security-*` — they are the audit trail.
- **Step nomenclature.** This skill has Steps 1–4 (linear). It has NO Phase A/B/C (no validation loops). See `ClaudeCode/ARCHITECTURE.md`.
