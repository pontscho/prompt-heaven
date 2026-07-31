---
name: feature-implementation-plan
type: spec
status: active
title: Find-Verify-Synthesize refactor of p:code-review and p:branch-review
description: Multi-agent 4-Step fan-out replacing the monolithic review skills with a shared lens fragment plus two single-purpose leaf minions.
sources:
  - ClaudeCode/skills/_lib/code-review-lenses.md
  - ClaudeCode/agents/minion-code-reviewer.md
  - ClaudeCode/agents/minion-code-verifier.md
  - ClaudeCode/skills/code-review/SKILL.md
  - ClaudeCode/skills/branch-review/SKILL.md
  - ClaudeCode/skills/_lib/handoff-contracts.md
verified:
  commit: 2787c7f
  date: 2026-07-16
links:
  - skills
  - agents
---

# Feature Implementation Plan: Find→Verify→Synthesize Multi-Agent Refactor of `p:code-review` and `p:branch-review`

> **Canonical plan — synthesized from three perspective drafts (risk-first base + maintainability-first and mvp-first grafts).** This plan replaces the two monolithic single-pass review skills with a linear 4-Step fan-out (Scope → Find → Verify → Synthesize) orchestrated from each skill body, backed by a shared `_lib` single-source-of-truth fragment and two single-purpose leaf minions.
>
> **Build discipline (risk-first base, MVP overlay):** high-blast-radius shared assets and the manual acceptance gauntlet land FIRST; the scoring formula is pinned and table-tested for monotonicity before any skill renders it; the per-review fan-out is bounded by an explicit `VERIFY_BUDGET`. Steps are tagged **[MVP-CORE]** (the shortest path to a runnable end-to-end review) and **[DEFERRED-POLISH]** (the full hybrid scoring layer, the `p:branch-review` rewrite, and the handoff-contract entries). The MVP-core slice (Steps 1, 2, 4, 5, 6-skeleton) yields the first runnable review with a *coarse* verdict; the full hybrid scoring (Step 3) layers on top without changing the pipeline shape.

---

## 1. Requirements Summary

### Functional Requirements

- **[FR-1]** Replace the monolithic single-pass body of `p:code-review` (file/dir scope) with a linear 4-Step fan-out pipeline (Step 1 Scope → Step 2 Find → Step 3 Verify → Step 4 Synthesize) orchestrated from the skill body, modeled on Claude Code's `/code-review` workflow (`.claude/tmp/cc/code-review.workflow.js`, minus the effort matrix and the Sweep phase — see Out of Scope).
- **[FR-2]** Replace the monolithic single-pass body of `p:branch-review` (git-diff scope vs base `master` > `main` > `trunk`) with the same 4-Step fan-out, plus 2 additional git-specific lenses (commit-hygiene, breaking-change/API) defined IN the branch-review skill body (NOT in `_lib`).
- **[FR-3]** Create a shared fragment `ClaudeCode/skills/_lib/code-review-lenses.md` as the single source of truth for: the 8 shared lens texts (5 correctness + 3 quality), the recall verdict-ladder, the cleanup-precedence rule, the candidate / verdict / output schemas, the lens→dimension map, and the scoring-derivation weights. **Zero shared-lens prose is duplicated into either skill or either minion.**
- **[FR-4]** Create `ClaudeCode/agents/minion-code-reviewer.md` — the FINDER. Takes a single `LENS:` parameter (plain text, first line of prompt) + scope; surfaces up to 6 candidate findings `{file, line, summary, failure_scenario}`; MUST NOT self-censor half-believed candidates; returns a structured candidates block; does NOT verify or score.
- **[FR-5]** Create `ClaudeCode/agents/minion-code-verifier.md` — the VERIFIER. Takes ONE candidate + scope; re-reads the code FRESH (anchoring-bias break); returns exactly one verdict (CONFIRMED / PLAUSIBLE / REFUTED) + evidence quoting/citing the line. Recall-biased ladder: PLAUSIBLE by default, REFUTED only when constructible from the code.
- **[FR-6]** Step 4 Synthesize (skill body, no extra agent) dedups by root cause, ranks correctness > cleanup and CONFIRMED > PLAUSIBLE, caps at 12 findings, derives 1–8 dimension scores from the verified findings, and renders the HYBRID output (ranked findings list + 1–8 dimension scoring + ASCII bars + verdict).
- **[FR-7]** Support flags: `--output console|markdown|both` (markdown writes `docs/reviews/<skill>-<name>-<date>.md`, mirroring the current path convention) and `--severity high|medium|low` (filters DISPLAYED findings only — score derivation uses ALL verified findings). Both skills are read-only. **Vocabulary migration (REQUIRED, decision #8):** both skills currently document `--severity critical|warning|info` (`p:code-review/SKILL.md:16`, `p:branch-review/SKILL.md:16`); the rewrite changes the canonical vocabulary to `high|medium|low`. To preserve input-side UX parity (NFR-8), each skill MUST accept the OLD values as **aliases** — `critical→high`, `warning→medium`, `info→low` — mapping them silently to the new bucket (preferred), OR, if aliasing is not implemented, MUST explicitly document the vocabulary change in its Parameters table. The alias approach is the chosen requirement; the documentation-only fallback is the secondary option, not an open question.
- **[FR-8]** Preserve each skill's existing verdict vocabulary and ASCII-bar/verdict visual presentation: `p:code-review` → EXCELLENT/ACCEPTABLE/NEEDS IMPROVEMENT/POOR (from aggregate score); `p:branch-review` → APPROVED/CHANGES REQUESTED/REJECTED (from finding severity).
- **[FR-9]** Add a `### /p:code-review` and a `### /p:branch-review` entry to `ClaudeCode/skills/_lib/handoff-contracts.md` (Inputs / Outputs / Side effects; no intra-pipeline tmp files; optional `docs/reviews/` output) plus rows in the intermediate-files table.

### Non-Functional Requirements

- **[NFR-1] Single source of truth.** Adding/editing a shared lens, the verdict ladder, the candidate schema, the lens→dimension map, or the scoring weights is a **one-file edit** in `_lib/code-review-lenses.md`. The skills reference (never copy) these texts; the minions treat the lens text as a parameter and hold NO lens prose. *(Maintainability-first load-bearing NFR.)*
- **[NFR-2] Bounded fan-out cost.** A single review must not spawn an unbounded number of `Agent` calls. The worst-case Agent count must be a closed-form function of the fixed lens count and a documented per-review candidate cap: `lensCount + VERIFY_BUDGET` (see [Risk R-2] and Step 6 Step-3). *(Risk-first primary risk-driver.)*
- **[NFR-3] Score monotonicity & determinism.** The 1–8 dimension scores must be a deterministic, monotonic function of the verified-finding set: adding a verified finding to a dimension must never raise that dimension's score; CONFIRMED must deduct ≥ PLAUSIBLE; correctness must deduct ≥ quality for equal counts. No subjective re-scoring in the skill body. Same verified set → same scores → same verdict. *(Risk-first primary risk-driver.)*
- **[NFR-4] Language-agnostic lenses.** Every lens text must work across C / Lua / TypeScript; each names a C, a Lua, and a TS example, illustrative not gating. A finder handed an unfamiliar language must degrade to "return an empty list" rather than hallucinate. *(Risk-first primary risk-driver; anti-hallucination.)*
- **[NFR-5] Read-only.** Neither skill nor either minion modifies source code. The only writes permitted are the optional `docs/reviews/<...>.md` report (skill body) — no intra-pipeline tmp files (handoff is via Agent return values; the acceptance gauntlet scratch file is the only legitimate tmp file).
- **[NFR-6] Layer-contract / no-nesting compliance.** All fan-out happens from the skill body. The two minions list ONLY worker tools. The contract at `ClaudeCode/ARCHITECTURE.md:52` forbids ONLY `Agent` in a minion's `tools:` (it explicitly PERMITS `Skill`, since `Skill` loads instructions into the current context without spawning a sub-agent). These two minions deliberately list NEITHER `Agent` NOR `Skill` — but only the `Agent` omission is contract-mandated; the `Skill` omission is a design choice because neither minion needs it. Linear pipeline uses **"Step N"**, never "Phase". Per `ClaudeCode/ARCHITECTURE.md:10`, `:52`, `:54-63`, `:118`.
- **[NFR-7] No new in-band routing token.** The finder's `LENS:` is a plain content parameter, not a workflow-dispatch token like the security minion's `PHASE:` (`ARCHITECTURE.md:118`). Two separate single-purpose minions, not one MODE-switched minion.
- **[NFR-8] UX parity.** The console output of each refactored skill must be recognizably the same shape as the current monolith (same ASCII-bar block, same verdict line, same `Full report:` footer) so existing users see no regression.
- **[NFR-9] Future-extensibility, costed in edits.** The design must make (a) adding a 9th lens, (b) switching verify from 1-vote to a 3-vote panel, and (c) retuning scoring weights each a *localized* change. See §5 "Extensibility playbook."

### Success Criteria

- **[SC-1]** A known-buggy diff/target (seeded with an inverted condition + a removed guard + a missing free) yields ≥1 CONFIRMED/PLAUSIBLE finding per seeded bug (recall test).
- **[SC-2]** A clean, well-formed diff/target yields zero or only INFO-level findings and a high aggregate score (low-noise test).
- **[SC-3]** `--severity high` hides medium/low findings from the displayed list but the aggregate score is identical to an unfiltered run on the same diff (filter-vs-score independence).
- **[SC-4]** `--output markdown` writes exactly one file to `docs/reviews/` with the documented name pattern and nothing else.
- **[SC-5]** `p:code-review src/foo.ts` reviews only that file's scope; `p:branch-review` reviews only the base-vs-HEAD diff scope — the two skills' scoping never overlaps; both still share the lens texts via `_lib`.
- **[SC-6]** A single review of an N-file scope spawns no more than `lensCount + VERIFY_BUDGET` finder+verifier Agent calls (8 lenses for `p:code-review`, 10 for `p:branch-review`), with the per-review candidate cap enforced BEFORE the verify fan-out.
- **[SC-7]** Hand a finder a file in a language none of its examples mention (e.g. a Zig or Nim snippet) and it returns an empty candidates list rather than fabricated findings (anti-hallucination test).
- **[SC-8]** Grep for any shared-lens body text (e.g. `"line-by-line diff scan"`) finds it in **exactly one** file: `_lib/code-review-lenses.md`. Neither skill nor either minion contains a copy (SSOT test).
- **[SC-9]** Each minion's frontmatter matches the `ARCHITECTURE.md:41-52` shape; `Agent` is absent from `tools` (contract-mandated at `:52`), and `Skill` is also absent (by design — neither minion needs it, though the contract would permit it).

### Assumptions

- **[A-1]** Handoff between Steps is via `Agent` return values, NOT disk tmp files. Each finder returns a candidates block; each verifier returns a verdict block; the skill body holds the merged set in working memory. The verifier is still a *fresh context* that re-reads the code — freshness comes from being a separate Agent invocation, not from a disk round-trip. *(Verifier "fresh context" is enforced by prompt + invocation discipline, not infrastructure — see [Risk R-4].)*
- **[A-2]** Synthesis runs in the skill body (main context), like `p:security-review` Step 4 Assemble (`ClaudeCode/skills/security-review/SKILL.md:338-340`). No extra synthesis Agent.
- **[A-3]** Skill frontmatter stays minimal (`name` + `description` only — skills inherit host tools including `Agent`), per `ClaudeCode/ARCHITECTURE.md:30-37`. Agent frontmatter follows the required-fields shape at `:41-52`.
- **[A-4]** The two minions are single-purpose (NO `MODE` token) per LOCKED DECISION and `ClaudeCode/ARCHITECTURE.md:118`. The minion is lens-agnostic: the lens text arrives as a parameter, so a new lens is picked up with no minion change.
- **[A-5]** Verify is 1-vote recall (a single non-REFUTED vote carries the finding), trivially upgradeable to a 3-vote panel later by fanning out 3 verifier Agents per candidate and majority-voting (see §5 Extensibility playbook).
- **[A-6]** `docs/reviews/` does not yet exist (verified — the `docs/` listing has no `reviews/` subdir); skills create it on first markdown write, exactly as `p:security-review/SKILL.md:351` ("Create the directory if it doesn't exist").

### Out of Scope

- **`--fix`** (applying findings to the working tree) and **`--comment`** (posting to GitHub) — both present in CC's `/code-review` (`.claude/tmp/cc/code-review.md:10`) but explicitly deferred this round. Document a one-line note in each skill that these are intentionally not implemented yet; the read-only contract is a hard guarantee for now.
- The **effort matrix** (`low|medium|high|xhigh|max`) and the **Sweep phase** from CC's workflow (`.claude/tmp/cc/code-review.workflow.js:42-47`, `:342-363`). Our fan-out is fixed (8 lenses for code-review, 10 for branch-review) with no sweep. *(Risk note: dropping the Sweep removes CC's recall safety-net; the recall-biased verdict ladder + the "do not self-censor" finder rule are our compensating controls — see [Risk R-3].)*
- The 3-vote verifier panel (designed-for but not built — see [A-5] and the Extensibility playbook).

---

## 2. Architecture Analysis

### Affected Subsystems

| Subsystem | How it's affected | Key files |
|---|---|---|
| Skills layer (`skills/p:*`) | Two monolithic skill bodies fully rewritten into 4-Step fan-out orchestrators | `ClaudeCode/skills/code-review/SKILL.md`, `ClaudeCode/skills/branch-review/SKILL.md` |
| Fragment layer (`skills/_lib`) | New shared SSOT fragment for lens texts/ladder/schema/scoring; new handoff entries | `ClaudeCode/skills/_lib/code-review-lenses.md` (new), `ClaudeCode/skills/_lib/handoff-contracts.md` (modify) |
| Agent layer (`agents/p`) | Two new single-purpose worker minions | `ClaudeCode/agents/minion-code-reviewer.md` (new), `ClaudeCode/agents/minion-code-verifier.md` (new) |
| MCP routing | Both minions use `purity`/`clangd`/`luals` for symbol nav + `Read` for files + `git`/`forge`; no grep/sed/cat hacks | n/a (governed by the agent frontmatter `tools:` + `mcpServers:`) |
| Output convention | New report path family under `docs/reviews/` | `docs/reviews/code-review-<name>-<date>.md`, `docs/reviews/branch-review-<date>.md` |

### Integration Points

- **Skill → Minion fan-out (one direction, no return-into-skill nesting):** Step 2 launches 8 (code-review) or 10 (branch-review) `Agent(p:minion-code-reviewer, prompt: "LENS: <key>\n\n<scope block>\n\n<lens body verbatim from _lib>")` calls in ONE message (parallel per `_lib/validation-loop.md:118` PL.1 mechanics). Step 3 launches one `Agent(p:minion-code-verifier, prompt: "<scope block>\n\n## Candidate\n<fields>\n\n<verdict ladder from _lib>")` per surviving candidate, in parallel. Data flows skill → minion (prompt) and minion → skill (structured return). No minion → minion edges (no-nesting, `ARCHITECTURE.md:10`).
- **Skill body ← fragment (textual reference):** Both skills quote the lens texts / ladder / schema from `_lib/code-review-lenses.md` rather than duplicating them — a *passive* reference per the fragment contract (`ARCHITECTURE.md:11`). The skill copies the lens text into the finder prompt at runtime, but the authoritative copy lives only in `_lib`.
- **Skill → filesystem (optional, terminal):** Step 4 optionally writes `docs/reviews/code-review-<name>-<date>.md` / `docs/reviews/branch-review-<date>.md` (mirroring `p:code-review/SKILL.md:186` and `p:branch-review/SKILL.md:159`). Read-only otherwise; create the dir on demand (mirror `p:security-review/SKILL.md:351`).
- **`p:branch-review` → git MCP:** Step 1 scope uses `git_call(function: "diff", params: {args: "--name-only <base>...HEAD"})` to materialize the file list — the exact shape used at `minion-inspector-security-officer.md:156`. Base-branch detection priority is `master` > `main` > `trunk` (preserved from `p:branch-review/SKILL.md:23`). The current monolith shells out via `git symbolic-ref … | sed …` (`p:branch-review/SKILL.md:33-48`); the rewrite MUST route through `git_call`, NEVER `Bash("git ...")` + `sed`.

### Constraints

- **No-nesting (hard):** minions NEVER list `Agent` in `tools:` (`ARCHITECTURE.md:10`, `:52`); the contract at `:52` permits `Skill`, but these two minions list NEITHER `Agent` NOR `Skill` — the `Agent` omission is contract-mandated, the `Skill` omission is a design choice (neither minion needs it). All orchestration is in the skill body. This is why the verifier is a separate minion the *skill* spawns, not a sub-agent the finder spawns.
- **No new in-band routing token (hard):** the `LENS:` parameter is a *plain prompt parameter* (a lens text), NOT a routing token in the `PHASE:`-style sense the `ARCHITECTURE.md:118` anti-pattern warns about. Two separate minions instead of one MODE-switched minion is the explicit decision that keeps us clear of that anti-pattern.
- **Step vs Phase nomenclature (hard):** this pipeline is LINEAR — use **Step 1–4**, never "Phase". "Phase A/B/C" is reserved for validation loops (`ARCHITECTURE.md:54-63`). This is NOT a validation loop; we borrow only the parallel-`Agent` fan-out *mechanics* from `_lib/validation-loop.md:118`.
- **MCP routing (hard):** C/C++ → purity (clangd-backed); Lua → luals; git → `mcp-git` (`git_call`), never `Bash("git ...")`; file discovery/search → purity. No grep/sed/cat hacks in the minions (mirror the routing stance at `minion-inspector-security-officer.md:47-66`).
- **Handoff contract (hard):** any new file a skill produces/consumes must be documented in `_lib/handoff-contracts.md` in the same change (`ARCHITECTURE.md:65-75`).
- **Read-only (hard):** no source modifications; only `docs/reviews/` output. No intra-pipeline tmp files (handoff via Agent return values).

---

## 3. Captured Information

### Existing Patterns

**P-1 — CC finder prompt construction (MIRROR this, minus effort/sweep).** From `.claude/tmp/cc/code-review.workflow.js:292-299`:

```js
const FINDER_PROMPT = f =>
  "## Code-review finder — " + f.label + "\n\n" + SCOPE_BLOCK + "\n" +
  "Run the diff command above and review ONLY through the lens of your assigned angle:\n\n" +
  f.text + "\n" +
  (f.kind === "cleanup" ? CLEANUP_PRECEDENCE + "\n" : "") +
  "Surface up to " + P.perAngle + " candidate findings, each with file, line, a one-line summary, and a concrete failure_scenario — the user-visible consequence (error, wrong output, data loss), not an intermediate state ... " +
  "Pass every candidate with a nameable failure scenario through — do not silently drop half-believed candidates; an independent verifier judges them next. " +
  "If nothing qualifies, return an empty list.\n\nStructured output only."
```
Our finder minion restates this in prose (it receives `LENS:` + scope, surfaces ≤6 candidates, never self-censors). The "do not silently drop half-believed candidates" sentence is the recall safety control — keep it verbatim in the minion body. Replace `f.label`/`f.text` with the `LENS:` key + the lens body pulled from `_lib`; replace `P.perAngle` with the fixed `6`.

**P-2 — CC verifier prompt (MIRROR; re-passes scope = the anchoring-bias break).** From `code-review.workflow.js:301-309`:

```js
const VERIFIER_PROMPT = c =>
  "## Code-review verifier\n\n" + SCOPE_BLOCK + "\n" +
  "## Candidate finding\n" +
  "File: " + c.file + (c.line != null ? ":" + c.line : "") + "\n" +
  "Summary: " + c.summary + "\n" +
  "Failure scenario: " + c.failure_scenario + "\n\n" +
  "Run the diff command above, read the relevant file(s), and return exactly one verdict:\n\n" +
  VERDICT_LADDER + "\n\n" + VERDICT_LADDER_RECALL + "\n\n" +
  "Structured output only. Evidence must quote or cite the relevant line(s)."
```
The verifier gets the scope + ONE candidate, re-reads, returns one verdict + evidence. CC re-passes `SCOPE_BLOCK` so the verifier independently re-derives from source — this IS the anchoring-bias break ([Risk R-4]); our minion must do the same (re-read fresh, not trust the finder's summary).

**P-3 — Recall verdict-ladder (REUSE verbatim in `_lib`).** Base ladder from `code-review.workflow.js:176-181`:

```
- **CONFIRMED** — can name the inputs/state that trigger it and the wrong
  output or crash. Quote the line.
- **PLAUSIBLE** — mechanism is real, trigger is uncertain (timing, env,
  config). State what would confirm it.
- **REFUTED** — factually wrong (code doesn't say that) or guarded elsewhere.
  Quote the line that proves it.
```
Recall addendum (`code-review.workflow.js:183-192`):
```
**PLAUSIBLE by default** — do not refute a candidate for being "speculative" or
"depends on runtime state" when the state is realistic: concurrency races,
nil/undefined on a rare-but-reachable path (error handler, cold cache, missing
optional field), falsy-zero treated as missing, off-by-one on a boundary the
code does not exclude, retry storms / partial failures, regex/allowlist that
lost an anchor. These are PLAUSIBLE.

**REFUTED** only when constructible from the code: factually wrong (quote the
actual line); provably impossible (type/constant/invariant — show it); already
handled in this diff (cite the guard); or pure style with no observable effect.
```

**P-4 — Cleanup precedence (REUSE verbatim in `_lib`).** From `code-review.workflow.js:194-199`:
```
Cleanup, altitude, and conventions candidates use the same
`file`/`line`/`summary` shape; in `failure_scenario`, state the concrete
cost (what is duplicated, wasted, harder to maintain, or which CLAUDE.md rule
is broken) instead of a crash. Correctness bugs always outrank cleanup,
altitude, and conventions findings when the output cap forces a cut.
```

**P-5 — Deterministic synthesis/rank/cap assembler (MIRROR the invariants).** From `code-review.workflow.js:386-450`. The rank function (`:390`) is the deterministic-assembly contract:
```js
const rank = c => (c.kind === "cleanup" ? 2 : 0) + (c.verdict === "PLAUSIBLE" ? 1 : 0)
```
→ correctness-CONFIRMED (0) < correctness-PLAUSIBLE (1) < cleanup-CONFIRMED (2) < cleanup-PLAUSIBLE (3). Lower rank = higher priority = survives the cap. Assembler invariants (`:409-415`): no silent drops while there is room; the displayed primary is the synthesizer's chosen representative; verdict escalates to CONFIRMED if any merged member is CONFIRMED; the summary describes the report actually returned. Our skill-body synthesis (no JS runtime) restates these as prose rules + a worked example, reuses the rank fn for the cut, then derives scores from the surviving set.

**P-6 — In-house skill-orchestrates-fan-out template (MIRROR the Step structure + compact messages).** `ClaudeCode/skills/security-review/SKILL.md` is the canonical in-house pattern: a skill running multi-Step fan-out from the skill body with fresh-context workers and ONE compact status message per Step. Key anchors:
- Step 4 Assemble runs inline in host context, pure formatting, no analysis: `SKILL.md:338-340`.
- `--severity` filters DISPLAYED findings but counts always report in full: `SKILL.md:349` ("Apply the `--severity` filter to the displayed-findings list. Always report counts in full.") — our skills mirror this exact behavior for score-vs-display independence ([SC-3]).
- `--output markdown|both` writes `docs/reviews/...`, creating the dir if missing: `SKILL.md:351`.
- Compact per-step status message template (one block per Step, e.g. `Step 2: FIND — 8 lenses fanned out → C candidates`); do NOT dump full finder/verifier output between Steps.

**P-7 — Worker restates its own expected output format.** From `minion-inspector-security-officer.md` — the worker's body owns its OUTPUT FORMAT section (`:555-575` shows the emit-ONLY-a-block discipline: "Emit ONLY one of these two blocks. No prose, no markdown report ..."). Our two minions each restate their structured-block output format + a short self-check checklist, so a caller that forgets to brief them still gets the right shape (the "own your routing" stance at `:47-49`). We do NOT copy the `PHASE:` token (decision #2).

**P-8 — Current monolith ASCII-bar + verdict presentation (KEEP).** `p:code-review/SKILL.md:119-182`: the `Quality Metrics:` block with `[####----] 4/8` bars, the EXCELLENT/ACCEPTABLE/NEEDS IMPROVEMENT/POOR verdict mapping (`:164-168`), and the bar-rendering table (`:170-182`):
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
`p:branch-review/SKILL.md:119-156`: the CRITICAL/WARNING/INFO blocks and the APPROVED/CHANGES REQUESTED/REJECTED verdict logic (`:151-156`: REJECTED on any critical · CHANGES REQUESTED on any warning · APPROVED otherwise). These are preserved in the new Step 4 render; only the dimension *labels* change (to the 5 lens-aligned ones) and the subjective scoring is replaced with finding-derived scoring.

**P-9 — Parallel `Agent` fan-out mechanics (BORROW, not the loop).** `_lib/validation-loop.md:118` (PL.1): "Launch ALL reviewer lanes in a single message so the harness runs them concurrently." We borrow this single-message-parallel mechanism for Step 2 (finders) and Step 3 (verifiers). We do NOT borrow the 5-round loop — our pipeline is linear (the `ARCHITECTURE.md:54-63` Step-vs-Phase distinction).

**P-10 — Non-code / test-file skip list (KEEP).** `p:code-review/SKILL.md:25-30` (skip `node_modules, vendor, dist, build, .git`, binaries/images/fonts) plus the CC low-effort test-skip list `.claude/tmp/cc/code-review.md:228-230` (`test/`, `spec/`, `__tests__/`, `*_test.*`, `*.test.*`, `fixtures/`, `testdata/`). Step 1 Scope applies both lists.

### Type Definitions (schemas — defined ONCE in `_lib`, restated as prose; no JS runtime)

From `code-review.workflow.js:207-237`:

- **Candidate block** (finder output, `CANDIDATES_SCHEMA`, `:217-230`), per candidate, up to 6:
  ```
  - file: path/to/file.ext           (required)
    line: <integer or omitted>       (optional)
    summary: <one-sentence statement of the issue>                              (required)
    failure_scenario: <concrete inputs/state → user-visible consequence;
                       for QUALITY lenses, the concrete cost>                   (required)
  ```
- **Verdict block** (verifier output, `VERDICT_SCHEMA`, `:231-237`), exactly one:
  ```
  verdict: CONFIRMED | PLAUSIBLE | REFUTED   (required)
  evidence: <quote or cite the relevant line(s)>   (required)
  ```
- **Scope** (`SCOPE_SCHEMA`, `:207-216`): `{ diffCommand, files[], claudeMdFiles[], summary, conventions }`. For `p:code-review` (file/dir) there is no diff command — adapt to `{ target, files[], claudeMdFiles[], summary, conventions }`.

### Build System

- No build target involved — these are markdown skill/agent/fragment assets. `forge_call` is NOT needed for the implementation itself. The minions still *carry* `mcp__mcp-forge__forge_call` in `tools:` per decision #2, so they can inspect build targets while reviewing build-affecting C diffs. No `project-forge.yaml` target is added or changed.
- Validation is manual acceptance scenarios (Step 1 of this plan), since there is no runnable unit test for prompt assets. The CC reference (`code-review.workflow.js`) is a JavaScript *structural model only* — no JS is ported; our pipeline is prose-orchestrated from the skill body.

---

## 4. Alternative Approaches

### Selected: Two single-purpose minions + linear 4-Step skill-body fan-out, lens fragment as single source of truth

**Rationale:** Mirrors CC's proven Find→Verify→Synthesize recall architecture (`code-review.workflow.js`) and the in-house `p:security-review` Step 1–4 orchestration, while complying with the in-house no-nesting + Step-nomenclature + handoff contracts (`ARCHITECTURE.md`). Two minions (finder, verifier) avoid the in-band-routing-token anti-pattern (`ARCHITECTURE.md:118`). A shared `_lib` fragment keeps the lens texts in one place so the two skills can't drift — a future maintainer edits one file. The finder/verifier split gives the anchoring-bias break (fresh verifier context) that single-pass subjective scoring lacks, and keeps each minion a single job.

**Trade-offs:** More Agent calls per review than the old single inline pass (cost — bounded in [Risk R-2]). Two skills duplicate the 4-Step orchestration prose (mitigated by both quoting the shared fragment, and by `p:branch-review` differing only in Step 1 scope + 2 extra lenses). The skill body must pull the lens text from `_lib` into each finder prompt at runtime (a trivial assembly cost, paid back every time a lens changes).

### Rejected: One minion with a `MODE: finder|verifier` (or `LENS-vs-VERIFY`) routing token

**Reason:** Adds a new in-band routing token to a minion — exactly the anti-pattern flagged at `ARCHITECTURE.md:118` ("In-band routing tokens inside minions, ad-hoc … new ones require an explicit decision"). The `PHASE:` token on the security minion is the one accepted exception; we do not add a second. A MODE token would also make the single minion carry two responsibilities, breaking single-purpose; every prompt change risks bleeding finder logic into verify mode. Two clean single-purpose minions are clearer and keep each body focused. *(LOCKED DECISION #2.)*

### Rejected: A subjective "scoring agent" that reads the diff and emits 1–8 scores directly

**Reason:** Reintroduces the very subjectivity this refactor removes (the current monolith's hand-waved 1–8 dimensions, `p:code-review/SKILL.md:49-90`). Scores must be a *deterministic function of verified findings* ([NFR-3]) so they are reproducible, monotonic, and tunable in one place. A scoring agent would be non-deterministic, unauditable, and a third minion to maintain. Derivation lives in the skill body as a fixed formula instead. *(LOCKED DECISION #7.)*

### Rejected: Disk-based handoff between Steps (write candidates/verdicts to `.claude/tmp/`)

**Reason:** Unnecessary for this pipeline — Agent return values carry the structured candidate/verdict blocks directly, and the fresh-context property the verifier needs comes from being a *separate Agent invocation* that re-reads the source code, not from a disk round-trip ([A-1]). `p:security-review` uses disk handoff (`handoff-contracts.md:129-130`) because its phases pass large findings *files* across fresh contexts; our finders/verifiers are parallel and light, and the verifier's fresh-context re-read is of the *source code*, not of a findings file. Disk handoff would add I/O, a cleanup obligation, and an undocumented-intermediate-file risk (`handoff-contracts.md:136`) for no benefit. *(LOCKED ASSUMPTION.)*

---

## 5. Implementation Strategy

### Overview

Build the shared, high-blast-radius assets and their validation FIRST (the acceptance gauntlet, the lens fragment, the pinned scoring formula, and the fan-out budget), then the two minions, then wire the two skills, then document the handoff. Every later artifact quotes the fragment, so getting the fragment and the scoring spec right up front is the single biggest lever on correctness. The MVP-core slice (acceptance gauntlet + lens fragment + both minions + a minimal `p:code-review` whose Step 4 ranks/caps/renders with a coarse verdict) yields the first runnable review; the full hybrid scoring layer, the `p:branch-review` rewrite, and the handoff entries are layered on top as deferred polish.

### SSOT boundary (the heart of the maintainability lens — stated explicitly)

| Lives in… | Content | Why here |
|---|---|---|
| `_lib/code-review-lenses.md` | 8 shared lens bodies; recall verdict-ladder; cleanup-precedence; candidate schema; verdict schema; **lens→dimension map**; scoring-deduction weights | Shared by BOTH skills + referenced by the finder/verifier prompt assembly. One edit propagates everywhere. |
| `p:code-review/SKILL.md` | File/dir scope logic; the 4-Step orchestration; score render + EXCELLENT/…/POOR verdict bands; `--output`/`--severity` handling | Skill-specific scope + presentation; NO lens prose. |
| `p:branch-review/SKILL.md` | Git-diff scope logic; base detection; the 4-Step orchestration; the **2 git lenses** (commit-hygiene, breaking-change); APPROVED/…/REJECTED verdict; flags | Git lenses are branch-only, so they live with the only skill that uses them — NOT in `_lib` (no false sharing). |
| `minion-code-reviewer.md` | Generic finder behavior: how to read scope, apply *whatever* `LENS:` text it's given, emit ≤6 candidates, never self-censor | The minion is lens-agnostic; the lens text arrives as a parameter, so the minion file holds NO lens prose. |
| `minion-code-verifier.md` | Generic verify behavior: re-read fresh, apply the verdict ladder it's given, emit one verdict | Same — ladder arrives in the prompt. |

**Net result:** shared-lens prose exists in exactly one file (`_lib`). The minions are *mechanisms*; the lenses are *data* passed to them. This is the single most important maintainability property of the design.

### Uniform lens-record format in `_lib` (designed for one-place extension)

Define each lens as a uniform record so the catalog is a flat, append-only list:

```markdown
### Lens: <key>            <!-- stable machine key, e.g. correctness-diff-scan -->
- **class:** correctness | quality
- **dimension:** <one of the 5 scoring dimensions>   <!-- the lens→dimension link, SSOT -->
- **body:**
  <the language-agnostic angle prose, naming C / Lua / TS examples>
```

Adding a 9th lens = appending one such record + (if it introduces a new dimension) one row in the lens→dimension map. **No skill edit, no minion edit** — the skills iterate "every lens in the catalog whose `class` is in scope," so a new record is picked up automatically by both finders.

### Key Design Decisions

- **Fragment-first ordering:** `_lib/code-review-lenses.md` is built before any minion or skill — it is the single source of truth every downstream artifact depends on. *Rationale: a defect in the fragment propagates to 4 files; a defect in a skill is local.*
- **Scoring formula pinned + validated before any full render:** the exact lens→dimension mapping and deduction weights are specified and table-tested for monotonicity in Step 3, before either skill renders a full hybrid score. The MVP ships a *coarse* verdict (finding count + max severity) so the slice is usable before Step 3 lands. *Rationale: [NFR-3] / [Risk R-1] is the highest-uncertainty item; the coarse verdict de-risks over-deferral.*
- **Per-review candidate cap before the verify fan-out:** Step 3 of each skill dedups + truncates candidates to a documented `VERIFY_BUDGET` BEFORE launching verifiers, bounding the verifier Agent count. *Rationale: [NFR-2] / [Risk R-2].*
- **Recall controls compensate for the dropped Sweep:** the recall verdict-ladder (P-3) + the "never self-censor" finder rule (P-1) are the recall safety-net replacing CC's Sweep phase. *Rationale: [Risk R-3].*
- **Verifier freshness is a prompt + invocation discipline:** the verifier re-reads the code from scratch (re-passed scope, P-2) and the skill launches it as a separate fresh Agent. *Rationale: [Risk R-4].*
- **`LENS:` is a content parameter, not a routing token** (`ARCHITECTURE.md:118`). The finder does the same thing regardless of lens value; the lens is just the angle text. This is the deliberate difference from the security minion's `PHASE:`.

### Conventions-vs-Consistency dimension routing (RESOLVED — SSOT)

The single `quality-consistency-conventions` lens produces findings; each finding is routed to a dimension by whether it cites a CLAUDE.md rule:

- **A finding that quotes an exact CLAUDE.md rule → Conventions dimension.**
- **A consistency finding with no CLAUDE.md cite → Consistency dimension.**

This routing rule is documented ONCE in `_lib/code-review-lenses.md` § Lens→Dimension Map as the single source of truth. (Resolves the gap flagged in the maintainability draft; do not re-flag.)

### Extensibility playbook (maintainer's how-to — each future change is a localized diff)

**(a) Add a 9th lens.**
1. Append one `### Lens: <key>` record to `_lib/code-review-lenses.md` § Lens Catalog (class, dimension, body).
2. If it maps to a brand-new dimension, add one row to the lens→dimension map + one deduction-weight entry in the same file.
*Files touched: 1 (`_lib`). Skills/minions: 0.* The skills already loop over the catalog by `class`, so both finders pick it up.

**(b) Switch verify from 1-vote to a 3-vote panel.**
1. In each skill's Step 3, change "spawn ONE verifier per candidate" to "spawn THREE verifiers per candidate (same prompt) and take the majority verdict (CONFIRMED if ≥2 CONFIRMED; REFUTED only if ≥2 REFUTED; else PLAUSIBLE)."
*Files touched: 2 (the two skill bodies, Step 3 paragraph only). Verifier minion: 0 — it is already a stateless single-candidate judge; three copies just vote.* To keep even this DRY, the majority-vote rule can itself be a one-paragraph fragment in `_lib` referenced by both skills.

**(c) Retune scoring weights.**
1. Edit the deduction-weight table in `_lib/code-review-lenses.md` § Scoring Map.
*Files touched: 1 (`_lib`).* Both skills read the table at synthesis time, so the retune applies to both review types identically.

### Risk Mitigation

| Risk | Failure mode | Mitigation | Addressed in |
|---|---|---|---|
| **R-1 Scoring formula arbitrary / non-monotonic** | A finding *raises* a score, or two reviews of the same diff score differently, or the formula is hand-wavy → users distrust the score and the refactor fails its core promise. | Pin a concrete formula: each dimension starts at **8**, subtract a weighted deduction per verified finding mapped to that dimension (deduction = base(class) × multiplier(verdict)); floor at **1**, round half-up. Mapping + weights table specified in Step 3, weights live in `_lib`. **Validate monotonicity** with a hand-computed truth table (Step 1 acceptance + Step 3 self-check): fixed verdict ⇒ more findings ⇒ score non-increasing; CONFIRMED deducts ≥ PLAUSIBLE; correctness deducts ≥ quality for equal counts. | **Step 1** (truth table), **Step 3** (formula spec + monotonicity self-check), consumed in **Steps 6–7** |
| **R-2 Unbounded parallel-Agent fan-out / cost** | 8–10 finders × up-to-6 candidates each = up to 48–60 verifier Agents per review → cost blowout, rate-limit, latency. | **Closed-form bound:** finders are FIXED (8 code-review / 10 branch-review). Finder `perAngleCap = 6` (matches CC `high`, `code-review.md:18`). Each skill's Step 3: (a) collect all candidates, (b) dedup near-identical (same file:line + mechanism → keep the most concrete), (c) **truncate to a per-review `VERIFY_BUDGET` (default 24)** correctness-first, THEN fan out one verifier each. Worst case = `lensCount + VERIFY_BUDGET` Agents. Document the budget knob in each skill. | **Step 2** (budget constant in fragment context), **Steps 6–7** (skills enforce dedup+truncate before verify), **Step 1** (acceptance: count Agents on a fat diff) |
| **R-3 Lens collapse / loss of recall (no Sweep)** | Without CC's Sweep phase, second-pass-only defects (moved guards, config flips) are missed; recall regresses vs CC. | Keep the recall verdict-ladder (P-3) and the "do not silently drop half-believed candidates" finder rule (P-1) verbatim — these are CC's primary recall levers, independent of Sweep. Document the Sweep omission under Out of Scope with this compensating-control note. Recall is measured by [SC-1]. | **Step 2** (ladder+rule in fragment), **Step 1** (recall acceptance scenario) |
| **R-4 Verifier doesn't actually re-read (anchoring bias not broken)** | Verifier trusts the finder's `summary`/`failure_scenario` and rubber-stamps → the independent-verification value is fake. | Verifier minion MUST re-pass the scope and re-read the cited file(s) FRESH before judging (P-2 re-passes `SCOPE_BLOCK`); minion body forbids judging from the candidate text alone; evidence MUST quote/cite the actual line (schema `:231-237` requires non-empty `evidence`). Skill launches each verifier as a *separate* fresh Agent. Self-check item in the minion's checklist: "evidence quotes a line I read this invocation, not the finder's summary." | **Step 5** (verifier minion body + checklist), **Step 1** (acceptance: feed a verifier a candidate citing a deliberately wrong line → must REFUTE, proving it re-read) |
| **R-5 UX regression vs monolith** | Refactored output looks different → users perceive a downgrade. | Preserve the exact ASCII-bar block, verdict vocabulary, and `Full report:` footer (P-8). Step 6 renders compatibly with `p:code-review/SKILL.md:119-182`; branch-review with `p:branch-review/SKILL.md:119-156`. | **Step 6/7** (render spec), **Step 1** (acceptance: visual diff of output vs current) |
| **R-6 Fragment/skill drift** | The two skills' shared-lens texts diverge from `_lib`. | Skills QUOTE the fragment, never re-author shared-lens texts; `p:branch-review`'s 2 git lenses are the ONLY lens texts defined in a skill body. SC-8 grep check catches any drift. Handoff doc records the dependency. | **Step 2** (fragment), **Step 8** (handoff), **Steps 6–7** (skills reference, not duplicate) |

---

## 6. Step-by-Step Plan

> **Build-order note:** Step 1 (acceptance gauntlet) and the scoring spec (Step 3) come BEFORE the skills are wired — the two highest-uncertainty items (the scoring formula and the fan-out budget) are pinned and validated before downstream effort compounds. Each step is tagged **[MVP-CORE]** or **[DEFERRED-POLISH]**. The MVP-core slice (Steps 1, 2, 4, 5, 6-skeleton with the coarse verdict) yields the first end-to-end runnable review; the full hybrid scoring render (Step 3 folded into Step 6/7), the `p:branch-review` rewrite, and the handoff entries are deferred polish layered on top.

### Step 1: Author the manual-acceptance gauntlet (definition of done, FIRST)  **[MVP-CORE — it gates everything]**

**Files**: `.claude/tmp/code-review-acceptance.md` (create — a scratch checklist, NOT a committed asset; per the temp-file rule this is the only legitimate tmp file here)
**Dependencies**: none
**Description**: Before writing any asset, write down the concrete acceptance scenarios the finished refactor must pass, derived from [SC-1..SC-9] and [Risk R-1..R-6]. There are no runnable unit tests for prompt assets, so these scenarios ARE the test suite. Capture at minimum:
1. **Recall** ([SC-1], R-3): a seeded diff with (a) an inverted `if` condition, (b) a removed null-guard, (c) a missing `free()` in C → expect ≥1 CONFIRMED/PLAUSIBLE per seed.
2. **Low-noise** ([SC-2]): a clean diff → expect 0 findings or INFO-only, high score.
3. **Filter-vs-score independence** ([SC-3]): same diff with `--severity high` vs unfiltered → displayed lists differ, aggregate scores identical.
4. **Fan-out budget** ([SC-6], R-2): a fat diff that makes every finder return 6 candidates → count the verifier Agents, assert ≤ `VERIFY_BUDGET`; total Agents ≤ `lensCount + VERIFY_BUDGET`.
5. **Anti-hallucination** ([SC-7], NFR-4): a finder handed a Zig/Nim snippet → empty candidates list.
6. **Verifier freshness** (R-4): hand a verifier a candidate citing a line number that does NOT contain the claimed bug → expect REFUTED with evidence quoting the real line.
7. **Scoring monotonicity** (R-1): a hand-computed truth table — fixed verdict, increasing finding count per dimension, assert score non-increasing; CONFIRMED deduction ≥ PLAUSIBLE; correctness ≥ quality.
8. **UX parity** (R-5): visual diff of new output vs `p:code-review/SKILL.md:119-161` and `p:branch-review/SKILL.md:119-149`.
9. **Scope separation** ([SC-5]): `p:code-review` reviews file/dir scope only; `p:branch-review` reviews diff scope only.
10. **SSOT** ([SC-8]): grep each shared-lens body → exactly one hit (`_lib`).

**Pattern to follow**: Captured Information §"Build System" (no runnable tests → manual scenarios). Mirror the example-driven validation style of the security minion's EXAMPLES section.
**Verification**: This step's output is the rubric every later step is checked against. Self-check: every [SC-x] and [Risk R-x] maps to at least one scenario.

### Step 2: Create the shared SSOT lens fragment `_lib/code-review-lenses.md`  **[MVP-CORE]**

**Files**: `ClaudeCode/skills/_lib/code-review-lenses.md` (create)
**Dependencies**: Step 1
**Description**: Author the single source of truth. Open with the fragment disclaimer ("**This file is a fragment** — not a user-callable skill…", mirroring `_lib/handoff-contracts.md:3`) + a pointer to `ARCHITECTURE.md`. Sections, in order:
1. **§ Lens Catalog (8, fixed, recall-bias)** — each lens a uniform `### Lens: <key>` record (class / dimension / body, per the format in §5). Bodies adapted verbatim from CC's angle texts (`code-review.workflow.js:60-174`), made language-agnostic across C/Lua/TS, with the two MCP-routing rewrites ("Grep for the symbol" → "use `find_references` via purity/clangd for C, luals for Lua"):
   - **CORRECTNESS (5):**
     - `correctness-diff-scan` ← CC Angle A (`:63-71`): inverted/wrong condition, off-by-one, null/undefined deref, missing free/await, swallowed error in catch, unescaped regex metachars.
     - `correctness-removed-behavior` ← CC Angle B (`:75-81`): for every deleted/replaced line, name the invariant; if not re-established → candidate (dropped guard/validation/error-path).
     - `correctness-cross-file` ← CC Angle C (`:85-91`): for each changed function, trace callers/callees via `find_references` (NOT grep); flag broken call sites.
     - `correctness-language-footgun` ← CC Angle D (`:95-101`): C — UAF/double-free/leak/missing NULL check; Lua — metatable/`__index` pitfalls, `nil` arithmetic, 1-based-index off-by-one; TS — falsy-zero, `==` coercion, closure-captured loop var, missing `await`.
     - `correctness-wrapper-proxy` ← CC Angle E (`:105-113`): wrapper/proxy/decorator/cache routing to the wrapped instance vs back through a registry/session/global; all forwarded methods present.
   - **QUALITY (3):**
     - `quality-reuse` ← CC Reuse (`:118-121`): flag new code re-implementing an existing helper; **name the existing helper** (grep → `search_for_pattern`/`find_references`).
     - `quality-consistency-conventions` ← CC Conventions (`:160-172`): flag a violation ONLY when you can quote the exact CLAUDE.md rule AND the exact offending line — no vague "spirit"; name the CLAUDE.md path. (Routed to Conventions vs Consistency per §5 routing rule.)
     - `quality-simplification-altitude` ← CC Simplification (`:128-133`) + Altitude (`:150-157`) **merged**: unnecessary complexity / bandaid-vs-deep-fix; name the simpler form or the deeper mechanism.
   - Each correctness lens names a C, Lua, and TS example for [NFR-4].
2. **§ Verdict Ladder (recall)** — paste `VERDICT_LADDER` + `VERDICT_LADDER_RECALL` verbatim (P-3, `code-review.workflow.js:176-192`).
3. **§ Cleanup Precedence** — paste `CLEANUP_PRECEDENCE` verbatim (P-4, `:194-199`).
4. **§ Candidate Schema** and **§ Verdict Schema** — the markdown contracts from §3 Type Definitions.
5. **§ Lens→Dimension Map** — the SSOT mapping (table in Step 3) + the Conventions-vs-Consistency routing rule (§5 RESOLVED).
6. **Language-agnostic rule (NFR-4, R-3):** each lens ends with "If the diff's language is one you cannot reason about, return an empty list rather than guessing — the verifier cannot rescue a hallucinated finding."

**Pattern to follow**: P-1, P-3, P-4; fragment disclaimer style from `_lib/handoff-contracts.md:1-3`; uniform lens-record format (§5).
**Verification**: All 8 lens records present with `class`/`dimension`/`body`; ladder + precedence + schemas + lens→dimension map present; each lens carries the language-agnostic fallback sentence (Step 1 scenario 5); grep for a lens body returns this file only (SC-8).

### Step 3: Specify and validate the scoring-derivation formula (appended to the fragment)  **[DEFERRED-POLISH — the MVP uses the coarse verdict; full scoring layers here]**

**Files**: `ClaudeCode/skills/_lib/code-review-lenses.md` (extend with a `## Scoring derivation` / § Scoring Map section)
**Dependencies**: Step 2
**Description**: Pin the exact, deterministic, monotonic formula ([NFR-3], R-1). Weights live in `_lib` (one-place retune).
- **Dimensions (5, lens-aligned):** Correctness · Consistency · Reuse/DRY · Simplicity/Altitude · Conventions.
- **Lens→dimension map:**
  | Dimension | Fed by lenses |
  |---|---|
  | Correctness | `correctness-diff-scan`, `correctness-removed-behavior`, `correctness-cross-file`, `correctness-language-footgun`, `correctness-wrapper-proxy` |
  | Consistency | `quality-consistency-conventions` (findings with NO CLAUDE.md cite) |
  | Reuse/DRY | `quality-reuse` |
  | Simplicity/Altitude | `quality-simplification-altitude` |
  | Conventions | `quality-consistency-conventions` (findings that quote a CLAUDE.md rule) |
- **Per-dimension formula:** `score(d) = max(1, 8 − Σ deduction(f) over verified findings f mapped to d)`, rounded half-up.
- **Deduction:** `deduction(f) = base(f.class) × multiplier(f.verdict)` where `base(correctness) = 3`, `base(quality) = 1.5`; `multiplier(CONFIRMED) = 1.0`, `multiplier(PLAUSIBLE) = 0.5`. (REFUTED never reaches scoring — dropped in Verify.) Equivalent weights: correctness+CONFIRMED −3 / correctness+PLAUSIBLE −1.5 / quality+CONFIRMED −1.5 / quality+PLAUSIBLE −0.75.
- **Severity derivation (for the displayed buckets + branch verdict):** correctness+CONFIRMED → HIGH; correctness+PLAUSIBLE → MEDIUM; quality+CONFIRMED → MEDIUM; quality+PLAUSIBLE → LOW. A quality finding that quotes a hard CLAUDE.md violation may escalate to MEDIUM.
- **Aggregate (code-review verdict):** `overall = round(mean of the 5 dimension scores)`, where the mean is rounded **half-up** to the nearest integer — the SAME rounding mode as the per-dimension formula above (e.g. a mean of 4.5 → 5), so the aggregate is deterministic and NFR-3 holds end-to-end. (Matches "Overall: <avg>/8" at `p:code-review/SKILL.md:136`.) Map via P-8 (`:164-168`): 7–8 EXCELLENT · 5–6 ACCEPTABLE · 3–4 NEEDS IMPROVEMENT · 1–2 POOR.
- **branch-review verdict (severity-driven, NOT score-driven):** preserve `p:branch-review/SKILL.md:151-156`: any HIGH (= correctness-CONFIRMED) ⇒ REJECTED; else any MEDIUM (correctness-PLAUSIBLE OR quality-CONFIRMED) ⇒ CHANGES REQUESTED; else (only LOW or none) ⇒ APPROVED. Dimension scores + bars still render for context.
- **`--severity` filter** applies to the DISPLAYED findings list AFTER scoring — score derivation always uses ALL verified findings (decision #8).
- **Monotonicity proof obligation:** include a worked truth table (0/1/2/3 findings per dimension at each verdict) showing score is non-increasing in finding count and that CONFIRMED ≥ PLAUSIBLE deduction and correctness ≥ quality — satisfying Step 1 scenario 7. The table MUST also exercise at least one **fractional aggregate mean** (e.g. a 5-dimension set averaging exactly 4.5) and show it resolves deterministically via half-up rounding (4.5 → 5) and lands in the expected verdict band — confirming the aggregate band boundary is deterministic.

**Pattern to follow**: LOCKED DECISION #7 (start-at-8, subtract weighted deductions, floor at 1); P-8 verdict mappings.
**Verification**: Hand-run the Step 1 scenario 7 truth table — assert monotonicity and verdict-threshold boundaries. Same verified set → identical scores on repeat (NFR-3). <!-- This is the single highest-risk artifact; the truth table is the gate. -->

### Step 4: Create the FINDER minion `minion-code-reviewer.md`  **[MVP-CORE]**

**Files**: `ClaudeCode/agents/minion-code-reviewer.md` (create)
**Dependencies**: Step 2
**Description**: Single-purpose FINDER. Frontmatter per `ARCHITECTURE.md:41-52` and decision #2:
```yaml
---
name: p:minion-code-reviewer
description: <single-lens code-review finder; receives a LENS: parameter (first line) + scope; surfaces up to 6 candidate findings {file,line,summary,failure_scenario}; MUST NOT self-censor half-believed candidates — an independent verifier filters them; returns a structured candidates block; does NOT verify or score; read-only>
model: opus
color: <choice, e.g. cyan>
tools: Read, mcp__mcp-clangd__clangd_call, mcp__mcp-luals__luals_call, mcp__mcp-purity__purity_call, mcp__mcp-forge__forge_call, mcp__mcp-git__git_call
mcpServers:
  - mcp-clangd
  - mcp-luals
  - mcp-purity
  - mcp-forge
  - mcp-git
---
```
Body sections:
- **ROLE** — "You are a code-review FINDER. You receive ONE `LENS:` (first line) + a review scope. You surface up to 6 candidate findings through that lens only." The lens text itself is NOT inlined — the body says "apply the lens passed in `LENS:`, whose text the orchestrator supplies from `_lib/code-review-lenses.md`."
- **MCP TOOL ROUTING (own your routing)** — mirror `minion-inspector-security-officer.md:47-66`: symbol nav via clangd/luals/purity, files via Read, git via `git_call` (never `Bash("git ...")`), NO grep/sed/cat.
- **INPUT HANDLING** — first line is `LENS:` — a plain lens text, NOT a routing token (cite `ARCHITECTURE.md:118`); rest is the scope block + the lens body.
- **WORKFLOW / TASK** — review ONLY through the assigned lens; for each finding produce `{file, line, summary, failure_scenario}` per the § Candidate Schema; **do NOT silently drop half-believed candidates** — paraphrase P-1's "do not silently drop … an independent verifier judges them next" verbatim; cap at 6; carry the language-agnostic fallback (empty list on unfamiliar language — R-3/NFR-4).
- **OUTPUT FORMAT** — emit ONLY the candidates block (mirror the emit-only discipline of P-7); restate the schema.
- **QUALITY CHECKLIST** (P-7) — used the right MCP per language; no source modified; ≤6 candidates; nothing self-censored.
**Tools list NEITHER `Agent` NOR `Skill`** — `ARCHITECTURE.md:52` mandates only the `Agent` omission (it permits `Skill`); `Skill` is omitted here purely because this finder needs neither tool (NFR-6).

**Pattern to follow**: P-1 (finder semantics), P-7 (worker restates output + checklist), minion frontmatter at `minion-inspector-security-officer.md:1-35`.
**Verification**: Step 1 scenarios 1 (recall) and 5 (anti-hallucination). Self-check: frontmatter has all 6 fields and lists neither `Agent` nor `Skill` (`Agent` omission is contract-mandated per `ARCHITECTURE.md:52`; `Skill` omitted by design); `LENS:` documented as a plain parameter; body restates the candidate block + no-self-censor rule.

### Step 5: Create the VERIFIER minion `minion-code-verifier.md`  **[MVP-CORE]**

**Files**: `ClaudeCode/agents/minion-code-verifier.md` (create)
**Dependencies**: Step 2
**Description**: Single-purpose VERIFIER. Same frontmatter shape/tools/mcpServers as Step 4 (`name: p:minion-code-verifier`, model opus, color your choice — e.g. magenta; lists **neither `Agent` nor `Skill`** — `Agent` omission contract-mandated per `ARCHITECTURE.md:52`, `Skill` omitted by design). Body:
- **ROLE** — "You are a code-review VERIFIER. You receive ONE candidate + the scope. You re-read the code FRESH (anchoring-bias break — you have no memory of how the finder reasoned) and return exactly one verdict."
- **ANCHORING-BIAS-BREAK MANDATE (R-4)** — "Re-read the cited file(s) FRESH this invocation. Do NOT trust the finder's summary or failure_scenario — they are a hypothesis to test, not a fact. Your evidence MUST quote a line you read this invocation."
- **MCP TOOL ROUTING** — identical to the finder.
- **INPUT HANDLING** — scope + ONE candidate (file/line/summary/failure_scenario, P-2 shape).
- **WORKFLOW / TASK** — re-read the cited file(s) and the diff; return exactly one verdict using the recall ladder (quote the fragment, P-3) + recall addendum; PLAUSIBLE by default, REFUTED only when constructible from code; produce `{verdict, evidence}` per § Verdict Schema; evidence must quote/cite the line.
- **OUTPUT FORMAT** — emit ONLY the verdict block.
- **QUALITY CHECKLIST** — re-read fresh (the R-4 self-check: "evidence quotes a line I read this invocation, not the finder's summary"); exactly one verdict; verdict enum exactly CONFIRMED/PLAUSIBLE/REFUTED; ladder quoted from `_lib`, not re-authored.
**Tools list NEITHER `Agent` NOR `Skill`** — `ARCHITECTURE.md:52` mandates only the `Agent` omission (it permits `Skill`); `Skill` is omitted here purely because this verifier needs neither tool.

**Pattern to follow**: P-2 (verifier prompt re-passes scope), P-3 (recall ladder), P-7 (output + checklist).
**Verification**: Step 1 scenario 6 (freshness — wrong line number ⇒ REFUTED). Self-check: frontmatter lists neither `Agent` nor `Skill` (`Agent` omission contract-mandated per `ARCHITECTURE.md:52`; `Skill` omitted by design); evidence requirement non-optional; ladder quoted from `_lib`.

### Step 6: Rewrite `p:code-review/SKILL.md` as a 4-Step fan-out (file/dir scope)  **[MVP-CORE for the skeleton + coarse verdict; the full hybrid scoring render is DEFERRED-POLISH]**

**Files**: `ClaudeCode/skills/code-review/SKILL.md` (modify — full body rewrite, frontmatter preserved/trimmed to name+description per `ARCHITECTURE.md:30-37`)
**Dependencies**: Steps 2, 4, 5 for the MVP skeleton; Step 3 for the full hybrid scoring render
**Description**: Keep the `Parameters` table (`target`, `--output`, `--severity` updated to `high|medium|low` per decision #8, `--depth` retained for dir scope). **`--severity` vocabulary migration (FR-7, REQUIRED):** the canonical values become `high|medium|low`; the skill MUST accept the prior `critical|warning|info` values as aliases (`critical→high`, `warning→medium`, `info→low`) so existing invocations keep working (NFR-8). If aliasing is omitted, the Parameters table MUST explicitly document the changed vocabulary — aliasing is the preferred outcome. Add a **Reference note**: "Lens texts, verdict ladder, schemas, and scoring weights live in `skills/_lib/code-review-lenses.md` (single source of truth). This skill references them; it does not restate them." Replace Steps 1–6 of the monolith with:
- **Step 1 Scope (skill body):** resolve target (file/dir; if directory, list recursively up to `--depth`), apply the skip lists (P-10, `p:code-review/SKILL.md:25-30` + the test-skip list), detect language(s), gather applicable CLAUDE.md (user/repo-root/ancestor-dir per the Conventions lens), build the scope block (adapted SCOPE_SCHEMA, no diffCommand). Emit ONE compact status message (P-6).
- **Step 2 Find (fan out 8 finders in ONE message):** for each of the 8 lenses in `_lib` § Lens Catalog, launch `Agent(p:minion-code-reviewer, prompt: "LENS: <key>\n\n<scope block>\n\n<lens body verbatim from _lib>\n\n<schema + ≤6 / no-self-censor instruction>")` — all in a single message (P-9 / `_lib/validation-loop.md:118`). Collect candidate blocks. Emit a compact Step 2 status (`8 lenses → C candidates`).
- **Step 3 Verify (dedup → budget → fan out verifiers):** dedup near-identical candidates (same file:line + mechanism → keep the most concrete, R-2), truncate to `VERIFY_BUDGET=24` correctness-first, then launch one `Agent(p:minion-code-verifier, …)` per surviving candidate in ONE message. Keep CONFIRMED + PLAUSIBLE, drop REFUTED. Emit a compact Step 3 status.
- **Step 4 Synthesize (skill body, no agent):** dedup by root cause + rank (correctness > cleanup, CONFIRMED > PLAUSIBLE — P-5 rank fn), cap 12. Render the HYBRID output: the ASCII-bar block + verdict (P-8, `p:code-review/SKILL.md:119-182`) PLUS the ranked findings list (file:line / summary / failure_scenario / verdict).
  - **MVP form (ships first):** render the ranked findings list reusing the console block frame (`p:code-review/SKILL.md:119-161`) + a **coarse verdict** derived from total surviving finding count + max severity → EXCELLENT / ACCEPTABLE / NEEDS IMPROVEMENT / POOR. This makes the slice usable before Step 3's full scoring lands (R-1 over-deferral mitigation).
  - **Full form (folds in once Step 3 lands):** derive the 5 dimension scores via the Step-3 formula, render the bars with the 5 lens-aligned dimension labels, compute the aggregate verdict from the bands.
  - Apply `--severity` to the DISPLAYED list only; score uses ALL verified findings (P-6, `p:security-review/SKILL.md:349`). `--output markdown|both` → write `docs/reviews/code-review-<name>-<date>.md` (path from `:186`); create the dir on demand (`p:security-review/SKILL.md:351`).
- Add an **Out of Scope** note: `--fix`/`--comment` intentionally deferred; effort matrix + Sweep out of scope; read-only this round.

**Pattern to follow**: P-6 (security-review Step structure + compact messages), P-8 (render), P-9 (fan-out mechanics), R-2 (budget), R-5 (UX parity).
**Verification**: MVP — `/p:code-review <buggy-file>` surfaces ≥1 CONFIRMED/PLAUSIBLE (SC-1); `/p:code-review <clean-file>` low-noise (SC-2) — **first end-to-end runnable review.** Full — Step 1 scenarios 3, 4, 7, 8 and budget scenario 4. Self-check: uses "Step" not "Phase"; frontmatter is name+description only; one compact message per Step; no shared-lens prose in this file (SC-8).

### Step 7: Rewrite `p:branch-review/SKILL.md` as a 4-Step fan-out (+2 git lenses)  **[DEFERRED-POLISH]**

**Files**: `ClaudeCode/skills/branch-review/SKILL.md` (modify — full body rewrite)
**Dependencies**: Step 6 (reuse its 4-Step structure); Step 3 (full scoring)
**Description**: Same 4-Step pipeline as Step 6, differing only in:
- **Step 1 Scope:** detect base branch priority `master` > `main` > `trunk` (preserve `p:branch-review/SKILL.md:23`); if base provided, use it; verify current ≠ base. Materialize the diff via `git_call(function: "diff", params: {args: "--name-only <base>...HEAD"})` (verified shape, `minion-inspector-security-officer.md:156`); also gather the commit log via `git_call` for the git lenses. **NOT** inline `Bash("git ...")` + `sed` (the current `:33-48` violates MCP routing — P-9). Use the full SCOPE_SCHEMA (with `diffCommand`). Apply the skip lists (P-10).
- **Step 2 Find:** fan out **10** finders = the 8 shared lenses (from `_lib`) + **2 git lenses defined IN THIS SKILL BODY** (NOT `_lib`, per decision #4): **commit-hygiene** (adapt `p:branch-review/SKILL.md:62-66`: message quality, atomic commits, no WIP/fixup) and **breaking-change/API** (adapt `:92-96`: API/public-interface/config-format changes). Each git lens is passed as a `LENS:` text to `minion-code-reviewer` just like the shared lenses — the minion is lens-agnostic, so no minion change is needed.
- **Step 3 Verify:** identical to Step 6; `VERIFY_BUDGET` accounts for 10 lenses (worst case `10 + 24`).
- **Step 4 Synthesize:** render with the **branch-review** verdict vocabulary (APPROVED/CHANGES REQUESTED/REJECTED, severity-driven — `:151-156`, severity derivation from Step 3) and the CRITICAL/WARNING/INFO blocks (`:119-149`). Dimension bars still render for context. `--output markdown|both` → `docs/reviews/branch-review-<date>.md` (path from `:159`).
- **`--severity` vocabulary migration (FR-7, REQUIRED):** like `p:code-review`, the canonical `--severity` values become `high|medium|low` (the table at `:16` currently reads `critical|warning|info`); the skill MUST accept the old values as aliases (`critical→high`, `warning→medium`, `info→low`) to preserve input-side UX parity (NFR-8), or, failing that, explicitly document the changed vocabulary in its Parameters table. Aliasing is the preferred outcome.
- Out of Scope note: same `--fix`/`--comment` deferral.

**Pattern to follow**: Step 6 (shared structure), the 2 git lens sources at `p:branch-review/SKILL.md:62-66` and `:92-96`, git MCP shape at `minion-inspector-security-officer.md:156`.
**Verification**: Step 1 scenarios 1–4, 8, 9; scope-separation [SC-5]; base auto-detect uses `git_call` not `Bash`. Self-check: git lenses live in THIS body, not `_lib`; verdict vocabulary is APPROVED/CHANGES REQUESTED/REJECTED; shared-lens prose still absent (SC-8).

### Step 8: Add handoff-contract entries for both skills  **[DEFERRED-POLISH]**

**Files**: `ClaudeCode/skills/_lib/handoff-contracts.md` (modify)
**Dependencies**: Steps 6, 7
**Description**: Add a `### /p:code-review` and a `### /p:branch-review` entry in the per-skill contracts section (after `### /p:security-review`, `:98`), each in the entry format used throughout the file (`_lib/handoff-contracts.md:26-52` style):
- **Inputs:** code-review → file/dir path + flags; branch-review → base branch (auto/explicit) + flags.
- **Outputs:** console hybrid report (always) + optional `docs/reviews/<skill>-<name>-<date>.md` when `--output` includes markdown.
- **Side effects:** read-only; writes only the optional `docs/reviews/` report; **NO intra-pipeline tmp files** (handoff via Agent return values — explicitly state this to satisfy the "no undocumented intermediate files" rule `:136`). Note explicitly: NO `.claude/tmp/` files (contrast `p:security-review`).
- Add rows to the intermediate-files table (`:124-131`) for the two `docs/reviews/` report families (producer = each skill Step 4; consumer = end-user audit trail).

**Pattern to follow**: existing per-skill entries `_lib/handoff-contracts.md:26-119`; the table format `:124-131`; the rules at `:133-138`.
**Verification**: Both entries present with Inputs/Outputs/Side effects bullets + the table rows; the "no intra-pipeline tmp" assumption is explicit (satisfies `ARCHITECTURE.md:65-75` handoff requirement).

---

## 7. Critical Files

| File | Role | Action |
|---|---|---|
| `.claude/tmp/code-review-acceptance.md` | Manual acceptance gauntlet (scratch, not committed; the only legitimate tmp file) | create (tmp, Step 1) |
| `ClaudeCode/skills/_lib/code-review-lenses.md` | SSOT: 8 lens texts + recall ladder + cleanup-precedence + schemas + lens→dimension map + scoring-derivation weights | create (Step 2; scoring appended Step 3) |
| `ClaudeCode/agents/minion-code-reviewer.md` | FINDER minion — one lens per invocation, ≤6 candidates, never self-censors | create (Step 4) |
| `ClaudeCode/agents/minion-code-verifier.md` | VERIFIER minion — re-reads fresh, one verdict (CONFIRMED/PLAUSIBLE/REFUTED) + evidence | create (Step 5) |
| `ClaudeCode/skills/code-review/SKILL.md` | 4-Step fan-out skill, file/dir scope, EXCELLENT/ACCEPTABLE/NEEDS IMPROVEMENT/POOR verdict | modify — full body rewrite (Step 6; full scoring Step 3) |
| `ClaudeCode/skills/branch-review/SKILL.md` | 4-Step fan-out skill, git-diff scope, +2 git lenses, APPROVED/CHANGES REQUESTED/REJECTED verdict | modify — full body rewrite (Step 7) |
| `ClaudeCode/skills/_lib/handoff-contracts.md` | Add `### /p:code-review` and `### /p:branch-review` entries + intermediate-files rows | modify (Step 8) |

---

## 8. Post-Implementation Checklist (the acceptance gauntlet is the definition of done)

- [ ] **MVP slice runs end-to-end** (Steps 1, 2, 4, 5, 6-skeleton): `/p:code-review <buggy-file>` surfaces a CONFIRMED/PLAUSIBLE finding (SC-1); `/p:code-review <clean-file>` is low-noise (SC-2) with a coarse verdict.
- [ ] **R-1 / NFR-3:** Scoring formula in `_lib/code-review-lenses.md` is concrete (start-at-8, weighted deductions `base×multiplier`, floor 1) and the monotonicity truth table (Step 1 scenario 7 / Step 3) passes — no finding raises a score; CONFIRMED deducts ≥ PLAUSIBLE; correctness ≥ quality; same verified set → identical scores.
- [ ] **R-2 / NFR-2:** Each skill dedups + truncates candidates to `VERIFY_BUDGET` (default 24) BEFORE the verify fan-out; worst-case Agent count = `lensCount + VERIFY_BUDGET` and is documented in the skill body (Step 1 scenario 4).
- [ ] **R-3 / NFR-4:** Recall verdict-ladder + "never self-censor" finder rule present verbatim; every lens carries the language-agnostic empty-list fallback and names C/Lua/TS examples; recall scenario [SC-1] passes and anti-hallucination [SC-7] passes.
- [ ] **R-4:** Verifier minion mandates a FRESH re-read; evidence must quote a line read this invocation; freshness scenario (wrong line ⇒ REFUTED) passes.
- [ ] **R-5 / NFR-8:** Console output of both skills is recognizable vs the current monolith (ASCII bars, verdict line, `Full report:` footer) — UX-parity visual diff passes.
- [ ] **R-6 / NFR-1 / SC-8:** Every shared-lens body, the verdict ladder, and both schemas appear in `_lib/code-review-lenses.md` and NOWHERE else (grep each lens body → exactly one hit). The two skills reference `_lib` at runtime; the two minions treat the lens as a parameter.
- [ ] **Decision #2/#4 / NFR-6/#7:** Two separate minions (no MODE token); `LENS:` is a plain parameter; `p:branch-review`'s 2 git lenses live in its own body, not `_lib`.
- [ ] **NFR-6 / SC-9:** Neither minion lists `Agent` in `tools:` (contract-mandated at `ARCHITECTURE.md:52`); neither lists `Skill` either (by design — the contract permits `Skill`, but neither minion needs it); both have all 6 frontmatter fields; all fan-out is from the skill body (no nesting); Step 2 and Step 3 each issue their parallel `Agent` calls in ONE message.
- [ ] **NFR-5:** Both skills + both minions are read-only; only `docs/reviews/<...>.md` is written; no intra-pipeline tmp files.
- [ ] **MCP routing:** base-branch detection and diff use `git_call` (`mcp-git`), never `Bash`/`sed` (fixes the current `p:branch-review/SKILL.md:33-48`); C symbols via purity, Lua via luals.
- [ ] Skill frontmatter is `name` + `description` only; pipelines use "Step 1–4" (never "Phase"); one compact status message per Step.
- [ ] **`--severity`** filters the displayed list only; score derivation uses ALL verified findings ([SC-3]).
- [ ] **`--output markdown|both`** writes exactly one `docs/reviews/<skill>-<name>-<date>.md` per run and nothing else ([SC-4]); `docs/reviews/` created on first markdown write.
- [ ] Verdict vocabularies preserved: code-review = EXCELLENT/ACCEPTABLE/NEEDS IMPROVEMENT/POOR; branch-review = APPROVED/CHANGES REQUESTED/REJECTED, with thresholds per Step 3.
- [ ] Both new skill entries added to `_lib/handoff-contracts.md` with Inputs/Outputs/Side effects + the intermediate-files rows + the no-tmp assumption.
- [ ] Out-of-scope note for `--fix`/`--comment` (and the effort matrix + Sweep) present in both skills.
- [ ] Scope separation verified: code-review = file/dir, branch-review = diff ([SC-5]).
- [ ] **Extensibility verified by walkthrough:** the §5 playbook (add lens = 1 file; 3-vote = 2 skill paragraphs; retune weights = 1 file) is reflected in the actual structure — a reviewer can trace each change to its localized site.
