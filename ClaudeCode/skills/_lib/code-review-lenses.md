# Code-review lenses — shared fragment (single source of truth)

> **This file is a fragment** — not a user-callable skill. It is referenced textually by
> `p:code-review` and `p:branch-review`, which copy each in-scope lens body into the
> finder prompt at runtime. The authoritative copy of every shared lens, the recall
> verdict-ladder, the candidate/verdict schemas, the lens→dimension map, and the scoring
> weights lives HERE and NOWHERE else. The two skills reference these; the two minions
> (`p:minion-code-reviewer`, `p:minion-code-verifier`) treat the lens text as a *parameter*
> and hold no lens prose. To add a lens, edit only this file. See `ClaudeCode/ARCHITECTURE.md`.

Consumed by: `p:code-review`, `p:branch-review`, and (indirectly, as a prompt parameter)
`p:minion-code-reviewer` + `p:minion-code-verifier`.

---

## § Lens Catalog (8 shared lenses — fixed, recall-biased)

Each lens is a uniform record: a stable `key`, a `class` (`correctness` | `quality`), the
scoring `dimension` it feeds, and a `body` (the angle prose the finder applies). The catalog
is append-only: a 9th lens is one new record here (plus, if it needs a new dimension, one row
in § Lens→Dimension Map). A skill selects lenses by `class` — `p:code-review` uses all 8;
`p:branch-review` uses all 8 plus its own 2 git lenses (defined in that skill, not here).

**Recall stance (applies to every lens):** surface up to **6** candidate findings. Pass every
candidate with a nameable failure scenario through — do NOT silently drop half-believed
candidates; an independent verifier judges them next. If nothing qualifies, return an empty list.

**Language-agnostic rule (every lens):** the examples below name C / Lua / TypeScript, but the
lens applies across languages. *If the target's language is one you cannot reason about, return
an empty list rather than guessing — the verifier cannot rescue a hallucinated finding.*

---

### Lens: correctness-diff-scan
- **class:** correctness
- **dimension:** Correctness
- **body:**
  Read every changed hunk line by line. Then read the enclosing function for each hunk — bugs
  in unchanged lines of a touched function are in scope (the change re-exposes or fails to fix
  them). For every line ask: what input, state, timing, or platform makes this line wrong? Look
  for inverted/wrong conditions, off-by-one, null/undefined dereference, swallowed errors in a
  catch that should propagate, wrong-variable copy-paste, unescaped regex metacharacters, and
  missing resource/await discipline. C: a missing `free()` on an error path, dereferencing a
  pointer a prior line may have set to `NULL`. Lua: `nil` arithmetic on an optional table field,
  a `1`-based-index off-by-one. TS: a missing `await` on a promise, a falsy-`0` treated as absent.

### Lens: correctness-removed-behavior
- **class:** correctness
- **dimension:** Correctness
- **body:**
  For every line the change DELETES or replaces, name the invariant or behavior it enforced,
  then look in the new code for where that invariant is re-established. If you cannot find it,
  that is a candidate: a removed guard, a dropped error path, a narrowed validation, a deleted
  early-return, a test that was covering a real case. C: a removed `if (!ptr) return;` guard.
  Lua: a dropped `assert(type(x) == "table")` precondition. TS: a removed `if (!user) throw`
  check, or a narrowed input-validation regex.

### Lens: correctness-cross-file
- **class:** correctness
- **dimension:** Correctness
- **body:**
  For each function the change modifies, find its callers and callees with `find_references`
  (purity/clangd for C/C++, luals for Lua) — never a text grep — and check whether the change
  breaks any call site: a new precondition, a changed return shape or type, a new error/exception
  it may now raise, a timing/ordering dependency. Also check callees: does a parallel change in
  the same set of edits make a call unsafe? C: a struct field reordered/removed that a caller in
  another translation unit reads. Lua: a returned table that lost a key a caller indexes. TS: a
  function whose return type narrowed from `T | undefined` to `T` while a caller still guards.

### Lens: correctness-language-footgun
- **class:** correctness
- **dimension:** Correctness
- **body:**
  Scan for the classic footguns of the target's language. C: use-after-free, double-free, memory
  leak on an error path, missing `NULL` check after allocation, integer overflow, signed/unsigned
  comparison, `sizeof` on a pointer instead of the array. Lua: metatable/`__index` pitfalls,
  `nil` vs `false` confusion, `nil` arithmetic, accidental global (missing `local`), `pcall`
  swallowing an error. TS: falsy-`0`/empty-string treated as missing, `==` coercion,
  closure-captured loop variable, missing `await`, `as` casts hiding a real type mismatch. Flag
  any instance the change introduces.

### Lens: correctness-wrapper-proxy
- **class:** correctness
- **dimension:** Correctness
- **body:**
  When the change adds or modifies a type that wraps another (cache, proxy, decorator, adapter):
  check that every method routes to the wrapped instance and not back through a
  registry/session/global — e.g. a caching layer holding a `delegate` that resolves IDs via
  `session.get(...)` instead of `delegate.get(...)` will re-enter the cache or recurse. Check
  that the wrapper forwards ALL the methods its callers actually use, and that it preserves the
  wrapped object's contract (error behavior, ordering, identity). C: a function-pointer table
  with a missing/wrong entry. Lua: a proxy `__index` that forwards reads but not writes. TS: a
  decorator class that overrides one method and silently drops the rest of the interface.

### Lens: quality-reuse
- **class:** quality
- **dimension:** Reuse/DRY
- **body:**
  Flag new code that re-implements something the codebase already has. Use `search_for_pattern`
  / `find_references` (not a blind grep) over shared/utility modules and files adjacent to the
  change, and NAME the existing helper to call instead. C: a hand-rolled string-dup where a
  `*_strdup` util exists. Lua: a re-implemented `table` deep-copy/merge where a util module has
  one. TS: a bespoke debounce/clamp where a shared helper exists. State the concrete cost in
  `failure_scenario` (what is duplicated and why it will drift), not a crash.

### Lens: quality-consistency-conventions
- **class:** quality
- **dimension:** Consistency / Conventions (routed — see § Lens→Dimension Map)
- **body:**
  Two jobs in one lens. (1) **Conventions:** find the CLAUDE.md files that govern the changed
  code (the user-level `~/.claude/CLAUDE.md`, the repo-root `CLAUDE.md`, plus any `CLAUDE.md` /
  `CLAUDE.local.md` in a directory that is an ancestor of a changed file). Flag a violation ONLY
  when you can quote the EXACT rule AND the EXACT offending line — no style preferences, no vague
  "spirit of the doc". Name the CLAUDE.md path. (2) **Consistency:** flag where the change departs
  from the surrounding code's own established idiom (naming, error-handling style, return
  conventions, ordering) WITHOUT a CLAUDE.md rule to cite. State the concrete cost in
  `failure_scenario`. If no CLAUDE.md applies and the code is internally consistent, return
  nothing for this lens.

### Lens: quality-simplification-altitude
- **class:** quality
- **dimension:** Simplicity/Altitude
- **body:**
  Two related jobs. (1) **Simplification:** flag unnecessary complexity the change adds —
  redundant or derivable state, copy-paste with slight variation, deep nesting, dead code left
  behind. Name the simpler form that does the same job. (2) **Altitude:** check that each change
  sits at the right depth, not as a fragile bandaid. Special cases layered on shared
  infrastructure are a sign the fix is not deep enough — prefer generalizing the underlying
  mechanism over adding special cases. Name the deeper mechanism. State the concrete cost in
  `failure_scenario` (what is harder to maintain), not a crash.

---

## § Verdict Ladder (recall-biased)

Base ladder:

- **CONFIRMED** — can name the inputs/state that trigger it and the wrong output or crash.
  Quote the line.
- **PLAUSIBLE** — mechanism is real, trigger is uncertain (timing, env, config). State what
  would confirm it.
- **REFUTED** — factually wrong (code doesn't say that) or guarded elsewhere. Quote the line
  that proves it.

Recall addendum:

**PLAUSIBLE by default** — do not refute a candidate for being "speculative" or "depends on
runtime state" when the state is realistic: concurrency races, nil/undefined on a
rare-but-reachable path (error handler, cold cache, missing optional field), falsy-zero treated
as missing, off-by-one on a boundary the code does not exclude, retry storms / partial failures,
regex/allowlist that lost an anchor. These are PLAUSIBLE.

**REFUTED** only when constructible from the code: factually wrong (quote the actual line);
provably impossible (type/constant/invariant — show it); already handled in this change (cite
the guard); or pure style with no observable effect.

---

## § Cleanup Precedence

Cleanup, altitude, and conventions candidates use the same `file`/`line`/`summary` shape; in
`failure_scenario`, state the concrete cost (what is duplicated, wasted, harder to maintain, or
which CLAUDE.md rule is broken) instead of a crash. Correctness bugs always outrank cleanup,
altitude, and conventions findings when the output cap forces a cut.

---

## § Candidate Schema (finder → skill)

Each finder returns a candidates block — up to 6 entries, each:

```
- file: path/to/file.ext            # required
  line: <integer>                   # optional (omit if not line-specific)
  summary: <one-sentence statement of the issue>          # required
  failure_scenario: <concrete inputs/state → user-visible consequence;
                     for QUALITY lenses, the concrete cost>   # required
```

If nothing qualifies, return an empty list. Do not pad.

## § Verdict Schema (verifier → skill)

Each verifier returns exactly one verdict block:

```
verdict: CONFIRMED | PLAUSIBLE | REFUTED      # required
evidence: <quote or cite the relevant line(s) read THIS invocation>   # required
```

---

## § Lens→Dimension Map (SSOT)

The Synthesize step derives 5 dimension scores from the verified findings. Each verified finding
maps to exactly one dimension via its originating lens:

| Dimension | Fed by |
|---|---|
| **Correctness** | `correctness-diff-scan`, `correctness-removed-behavior`, `correctness-cross-file`, `correctness-language-footgun`, `correctness-wrapper-proxy` |
| **Consistency** | `quality-consistency-conventions` — findings that do NOT quote a CLAUDE.md rule |
| **Reuse/DRY** | `quality-reuse` |
| **Simplicity/Altitude** | `quality-simplification-altitude` |
| **Conventions** | `quality-consistency-conventions` — findings that DO quote an exact CLAUDE.md rule |

**Conventions-vs-Consistency routing (SSOT):** the single `quality-consistency-conventions` lens
feeds two dimensions. Route each of its findings by whether it cites a CLAUDE.md rule — **a
finding that quotes an exact CLAUDE.md rule → Conventions; a consistency finding with no CLAUDE.md
cite → Consistency.** (The git lenses `p:branch-review` adds in its own body — commit-hygiene,
breaking-change/API — are not in this map and do NOT feed the dimension bars; branch-review uses
them for ranking, severity, and verdict only, as Correctness-class — **except `git-commit-hygiene`,
which is capped at MEDIUM**. See `p:branch-review`.)

---

## § Scoring Derivation (deterministic, monotonic)

Computed in the skill's Synthesize step (no extra agent). Uses ALL verified findings, regardless
of any `--severity` display filter.

**Per-dimension raw value** (a real number; each dimension starts at 8):

```
raw(d) = max(1, 8 − Σ deduction(f)  over verified findings f whose lens maps to d)
deduction(f) = base(f.class) × multiplier(f.verdict)
  base(correctness) = 3        base(quality) = 1.5
  multiplier(CONFIRMED) = 1.0  multiplier(PLAUSIBLE) = 0.5
```

Equivalent per-finding deductions: correctness+CONFIRMED −3 · correctness+PLAUSIBLE −1.5 ·
quality+CONFIRMED −1.5 · quality+PLAUSIBLE −0.75. (REFUTED never reaches scoring — dropped in
Verify.)

**Displayed dimension score** (for the ASCII bar): `display(d) = round_half_up(raw(d))`, an
integer 1–8.

**Aggregate:** `overall = round_half_up( mean( raw(d) for the 5 dimensions ) )` — computed from
the RAW per-dimension values (not the rounded displays, to avoid double-rounding drift), using
the SAME half-up rounding as `display`. A mean of exactly 4.5 → 5. Deterministic: same verified
set → same `raw` values → same `overall`.

**Severity derivation** (for the displayed buckets and the branch-review verdict):

| finding | severity |
|---|---|
| correctness + CONFIRMED | HIGH |
| correctness + PLAUSIBLE | MEDIUM |
| quality + CONFIRMED | MEDIUM |
| quality + PLAUSIBLE | LOW |

A quality finding that quotes a hard CLAUDE.md violation may escalate to MEDIUM.

**Verdicts:**

- **`p:code-review`** (score-band with a severity floor): first map `overall` → band (7–8
  EXCELLENT · 5–6 ACCEPTABLE · 3–4 NEEDS IMPROVEMENT · 1–2 POOR), then apply a **severity floor —
  any HIGH finding caps the band at NEEDS IMPROVEMENT** (final = the worse of the score-band and the
  cap; it only tightens, never loosens — a score-band of POOR stays POOR). This stops a single
  catastrophic dimension (e.g. Correctness floored to 1 by several HIGH bugs) from reading as
  ACCEPTABLE just because the other four dimensions are clean.
- **`p:branch-review`** (severity-driven, NOT score-driven): any HIGH ⇒ REJECTED; else any MEDIUM
  ⇒ CHANGES REQUESTED; else (only LOW or none) ⇒ APPROVED. Dimension bars still render for context.

### Monotonicity truth table (the gate — hand-run this)

Single dimension, starting at 8 (raw values; `display` in parentheses):

| findings in dimension | raw | display | note |
|---|---|---|---|
| none | 8.0 | 8 | baseline |
| 1× correctness-CONFIRMED | 5.0 | 5 | −3 |
| 2× correctness-CONFIRMED | 2.0 | 2 | −6, non-increasing ✓ |
| 3× correctness-CONFIRMED | 1.0 | 1 | −9 → floored at 1 ✓ |
| 1× correctness-PLAUSIBLE | 6.5 | 7 | −1.5; CONFIRMED (5) deducts ≥ PLAUSIBLE (6.5) ✓ |
| 1× quality-CONFIRMED | 6.5 | 7 | −1.5; correctness-CONFIRMED (5) deducts ≥ quality-CONFIRMED (6.5) ✓ |
| 1× quality-PLAUSIBLE | 7.25 | 7 | −0.75 (lightest) ✓ |

Invariants confirmed: more findings ⇒ raw non-increasing; CONFIRMED deducts ≥ PLAUSIBLE;
correctness deducts ≥ quality for equal counts/verdict; floored at 1.

**Fractional aggregate boundary:** five dimensions all at `raw = 4.5` → `mean = 4.5` →
`round_half_up(4.5) = 5` → ACCEPTABLE. The half-up rule resolves the `.5` boundary
deterministically (never banker's rounding, never truncation), so repeat runs on the same
verified set always land in the same band.
