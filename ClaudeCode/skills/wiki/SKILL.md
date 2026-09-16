---
name: wiki
description: >-
  Build and maintain a project's documentation wiki under docs/ as a persistent, compounding, code-verified knowledge base (Karpathy LLM-wiki pattern). Use to keep docs in sync with code, audit stale/broken docs, answer questions from docs, or bootstrap docs for a repo. Trigger: /p:wiki, /p:wiki ingest, /p:wiki lint, /p:wiki query, /p:wiki init, /p:wiki adopt, "update the docs", "is the documentation stale", "document this subsystem", "onboard existing docs into the wiki", "dokumentacio frissites", "meglevo doksik beszervezese".
---

# Project Wiki Engine

**MANDATE — read before any wiki work.** This skill governs ALL documentation
written under `docs/`. The rules below are NOT preferences. If you write a
page, run an operation, or answer a query without satisfying them, you have
violated this skill.

- You MUST delegate the body of every operation (`ingest`, `lint`, `query`,
  `init`, `adopt`) to the `p:minion-librarian` agent. Executing the steps
  inline in the main context — opening pages, running scripts, verifying
  anchors, rewriting prose — wastes the main context for work the minion was
  built to do. The skill is the **contract**; the janitor is the **executor**.
  See [Delegation](#delegation-mandatory) below.
- You MUST obey the [Schema](#schema-the-contract) at the bottom of this file —
  it is law. The schema ships inside this skill, so it is already loaded the
  moment the skill loads; there is no separate file to open. (The janitor reads
  it too — from this same SKILL.md — so it can act; you obey it so you can
  validate its report.)
- You MUST end every operation with the [Self-check](#self-check-must-run-before-reporting-any-operation-done)
  at the bottom of this file. The janitor includes its own self-check in the
  report; you verify it before declaring the operation done.
- You MUST resolve every code anchor via the language MCP (clangd / cuda /
  luals / purity / git), and do wiki search / freshness / reindex via the
  `mcp-wiki` `wiki_call` tool. Using grep / find / sed / cat / awk / head / tail
  to locate or read code is a violation. (Enforced inside the janitor — but the
  same rule applies to any follow-up work you do in the main context.)
- You MUST NOT rewrite, restyle, or "improve" existing prose during `adopt`.
  The body is sacred — frontmatter only.
- You MUST NOT paste code blocks larger than a single signature. Anchor and
  link to the source; never inline a function body.
- You MUST NOT auto-delete pages or silently fix discrepancies. The janitor
  surfaces these as proposals; the human approves; you (main agent) execute
  the approved deletion.

No "consider", no "try to", no "should". MUST and NEVER are the only modal
verbs that apply to this skill.

Maintain `docs/` as a **persistent, compounding documentation wiki** that stays
verifiably in sync with the code. The wiki is a *derived* artifact: the code is
the source of truth, and every factual claim about code carries an anchor that a
lint pass can re-check.

This skill is both the **engine** (reusable method) and the **schema** (page
types, frontmatter anatomy, anchors, freshness rules, anti-scope). The engine is
the sections that follow; the schema is the [Schema](#schema-the-contract)
section at the bottom of this file. Both are law.

## Golden rules (violations are failures, not preferences)

1. **DELEGATE TO `p:minion-librarian`.** Every operation runs in the janitor's
   sandbox, not the main context. The janitor reads pages, verifies anchors,
   runs scripts, and applies non-destructive changes. The main context only
   sees the result. Executing the work inline is a violation.
2. **OBEY THE [SCHEMA](#schema-the-contract).** Every operation, every time. It
   is the section at the bottom of this file — already loaded with the skill,
   no separate read. It defines layout, page types, and the exact frontmatter
   subset the scripts can parse. Obey it literally — deviation is a violation.
   (The janitor obeys it too, reading it from this same file; you know it so you
   can sanity-check the report.)
3. **Freshness/index checks run first, you read second.** ALWAYS run
   `wiki_call` `freshness` / `reindex` (via the janitor) to find *what* to look
   at before opening any page. The janitor enforces this — if its report skips
   them, reject the report.
4. **MCP routing is mandatory.** Resolve symbols and read code via clangd /
   cuda / luals / purity / git MCP, and search / freshness / index the wiki via
   the `mcp-wiki` `wiki_call` tool — NEVER grep / find / sed / cat / awk /
   head / tail. See Schema §7. No exceptions, no fallbacks. Applies to the
   janitor and to any follow-up work in the main context.
5. **NEVER auto-delete or silently rewrite.** The janitor surfaces deletions
   and other destructive changes as proposals; the human approves; you (main
   agent) execute the approved deletion. Touching a page without approval is
   a violation.
6. **The code wins.** When code and a page disagree, the page is stale — full
   stop. Do not "harmonize" the two; the janitor marks the page stale and
   surfaces the conflict.
7. **End every operation with the [Self-check](#self-check-must-run-before-reporting-any-operation-done).**
   The janitor includes its own self-check in the report; verify it before
   declaring done. Skipping is a violation.

## Delegation (MANDATORY)

Every operation delegates execution to **`p:minion-librarian`** via the Task
tool. Pass:

- `op` — one of `ingest`, `lint`, `query`, `init`, `adopt`.
- Operation-specific params (see [Operations](#operations) below).
- Optional: a one-line note about the context the human gave you (e.g. "after
  feature/auth merge", "preparing release").

The janitor returns a structured report with these sections:

| Section | Your handling |
|---|---|
| **Summary** | Relay to the human as the headline. |
| **Applied Changes** | Already applied by the janitor (frontmatter bumps, prose updates during ingest, INDEX regeneration, anchor re-verifications). Surface the list; no action needed. |
| **Proposed Changes** | Each item is one of `[PROPOSE-DELETE]`, `[PROPOSE-NEW-PAGE]`, `[PROPOSE-SPLIT]`, `[PROPOSE-STATUS-DOWNGRADE]`, etc. The janitor did NOT apply these. Present them to the human, get approval, and execute approved deletions / page creations yourself via `purity_call`. |
| **Findings** | For `lint` / `query`: severity-grouped issues (broken anchors, drift, contradictions). Relay to the human. |
| **Self-check** | The janitor's own checklist. Verify every applicable item is ticked (or explicitly explained). If any `[ ]` or `[!]` remains and the report does not justify it, **reject the report and re-invoke the janitor with a follow-up**. |

**Inline exception (very narrow):** for `/p:wiki query` with a question that is
clearly answerable from a single already-read page in the current conversation,
you MAY answer inline without delegating — but only if no anchor verification
or write-back is required. When in doubt, delegate.

The janitor is forbidden from deleting files. File deletion is YOUR job, only
after explicit human approval, via `purity_call` (or `Bash("rm <path>")` if the
human prefers — but only with approval).

## Operations

Dispatch on the argument: `ingest`, `lint`, `query`, `init`, `adopt`. With no
argument, ask which operation, or infer from the request.

For each operation: (1) apply the Schema (the section at the bottom of this
file), (2) invoke `p:minion-librarian` with the params below, (3) review the
report and surface it to the human, (4) execute any approved destructive
proposals yourself.

### `/p:wiki ingest [<base-ref>]`
Code changed -> update affected pages.

Delegate with `op=ingest`, `base=<base-ref>` (default: merge-base with main, or
`HEAD~1` if unsure — ask if ambiguous).

The janitor will: diff against base; map changed files to pages via
frontmatter `sources`; for each affected page rewrite the prose, re-verify
inline `path:symbol` anchors via MCP, bump `verified.commit` / `verified.date`,
set `status: active`; reindex. Significant changed files mapping to NO page
are surfaced as `[PROPOSE-NEW-PAGE]` items — your job to confirm with the
human and create them.

### `/p:wiki lint`
Audit without any code change.

Delegate with `op=lint`.

The janitor will: run `wiki_call freshness` (stale / unverified /
orphaned-source report) and `wiki_call reindex` with `check: true` (orphans,
dup slugs, malformed frontmatter);
open only flagged pages; resolve every inline anchor via MCP (missing →
**broken**; signature changed → **drifted**); detect cross-page contradictions.
It writes no frontmatter status for code drift — freshness is measured per
call by `wiki_call freshness` — and surfaces every fix (including deletions)
as a proposal.

### `/p:wiki query "<question>"`
Answer from the wiki, fall back to code.

Delegate with `op=query`, `question="<question>"`.

The janitor will: search pages first via `wiki_call search` (frontmatter-aware,
ranked) and read hits via `wiki_call get_page`; fall back to clangd / luals /
purity on the code; answer with citations (page slugs + code anchors). If the answer
required deriving something durable not yet captured, the janitor files it
back into the right page (and reindexes); if no page is a clean home, a
`[PROPOSE-NEW-PAGE]` is surfaced.

### `/p:wiki init [--root docs]`
Bootstrap a repo.

Delegate with `op=init`, `root=<dir>`.

The janitor will: create the `docs/` skeleton (Schema §1); draft `overview.md`
from the repo's top-level structure (batched survey, not unbounded
exploration); reindex. Your job: review the drafted overview and confirm with
the human.

### `/p:wiki adopt [--root docs]`
Onboard a repo that *already* has hand-written docs into the wiki. Unlike
`ingest`, adopt **preserves the existing prose verbatim** — it backfills the
frontmatter contract and infers anchors. It does NOT rewrite, restyle, or
"improve" the body. Touching the body during `adopt` is a violation (enforced
in the janitor).

Delegate with `op=adopt`, `root=<dir>`, optional `batch_size=<n>`.

The janitor will: run `wiki_call reindex` (`check: true`) to find malformed docs; for each
doc, without changing its body, classify into a page type, infer `sources`
anchors via MCP, add frontmatter with `verified.commit: <HEAD>` and
`status: draft`. Then verify pass: where claims hold, flip `draft → active`;
otherwise leave `draft` and record discrepancies. Multi-type monoliths are
surfaced as `[PROPOSE-SPLIT]` items. If `overview.md` is missing, the janitor
drafts one (as in `init`). Reindex + freshness confirm a clean structure.

For a repo with many docs, ask the human about batching: the janitor can
adopt in batches (`batch_size=N`) and let the human review the `draft` pages
before promoting them.

## Tooling: the `mcp-wiki` server (primary) + the CLI scripts (CI gate)

The janitor drives the wiki through the **`mcp-wiki` MCP server** (one tool,
`wiki_call`), NOT by shelling out to the scripts. Functions:

| function | purpose |
|---|---|
| `search` | frontmatter-aware, ranked token search over pages (type/status/prefix filters); returns `path#section` anchors |
| `source_to_pages` | reverse lookup: a changed source file → the pages whose `sources`/`targets` cover it |
| `get_page` | read one page (whole or a single section) by slug/path |
| `list` | list pages grouped by type, with filters |
| `freshness` | git-only staleness report (stale / unverified / orphaned-source, with a `gating:` count) |
| `reindex` | regenerate `INDEX.md`; `check: true` audits (dup slugs, malformed) without writing |
| `stats` | page counts by type/status + dup/orphan/malformed audit |
| `verify` | resolve every `sources:` entry and every inline `path` / `path:symbol` span against the repo, and classify every measured region; params: `path_prefix`, `measure` (default `false`). `measure: true` additionally RE-RENDERS the regions, which runs the commands `docs/measurements.json` names — without it nothing is executed and the answer says which regions it therefore did **not** check. The symbol matcher is stdlib text and declares its own limits; purity_call stays the real resolver (Schema §7) |
| `measure` | re-render the measured regions (Schema §4b); params: `write` (default `false` = check only, writes nothing), `force`, `name`, `path_prefix`. A body that no longer hashes to its recorded digest was edited by hand and is **REFUSED**, never overwritten, unless `force` says otherwise |

`measure` and `verify` are the only two functions that may execute anything; no
read path can reach a child process. Both are still the janitor's to run — they
are `wiki_call` functions like every other.

The underlying logic still lives in two stdlib-only, Python 3.9+ scripts that
never call an LLM and only ever write `INDEX.md`. They remain as a **CLI CI
gate** (exit codes drive PR checks); `wiki_call` is the interactive/agentic path:

```bash
~/.claude/skills/p/skills/wiki/scripts/freshness.py --root docs [--head <ref>] [--quiet]
~/.claude/skills/p/skills/wiki/scripts/reindex.py   --root docs [--check]
```

- `freshness.py` exits non-zero if any page is stale — usable as a pre-PR CI gate.
- `reindex.py` regenerates `INDEX.md` by default; `--check` audits without
  writing. It exits non-zero on duplicate slugs or malformed frontmatter.

Use the CLI scripts ONLY for a CI gate or a quick main-context sanity check.
All interactive work (freshness, reindex, search, page reads, anchor
verification) goes through the janitor and `wiki_call`.

**The bootstrap, and it is a third thing — neither the server nor a CI gate.**

```bash
python3 -B ~/.claude/skills/p/skills/wiki/scripts/measure_cli.py --help
```

A repo's FIRST measured-region migration cannot use `measure` / `verify`,
because the `mcp-wiki` process the session is talking to was started before that
code existed, and a migration cannot restart the server it is running inside.
`measure_cli.py` closes that one gap: it loads the **committed**
`Scripts/mcp-wiki.py` off disk and calls the same `handle_wiki_call` the MCP
process would (`measure`, `verify`, plus two diagnostics `wiki_call` has no
function for — `regions`, which lists a page's regions **with each marker line's
fenced flag**, and `body`, which prints one region verbatim). Pass `--server` if
the server does not sit under the project root; `server` prints the search
order.

Unlike `freshness.py` and `reindex.py`, it is **not** a hand-mirrored copy and
must never become one: it restates no marker grammar, digest, fence or state
rule — it calls every one of them on the module it loads. After the first
migration, use `wiki_call`; a session whose server already has `measure` has no
use for this file.

## Self-check (MUST run before reporting any operation done)

Most of the operational checklist runs **inside the janitor** — its report
includes a Self-check section against the items below. Your job in the main
context is to **verify** the janitor's self-check honestly covered every
applicable item, and to handle the two items the janitor cannot certify (the
delegation itself, and execution of approved destructive proposals).

Before you say "done" on `ingest` / `lint` / `init` / `adopt` / `query`, you
MUST be able to answer YES to every applicable item below. If any answer is
NO, fix it or surface it explicitly — do NOT silently ship.

**You (main agent):**

- [ ] I delegated the operation to `p:minion-librarian` (or used the narrow
      inline-query exception and stated so explicitly).
- [ ] I validated the janitor's report against the Schema (the Schema section
      of this skill).
- [ ] The janitor's Self-check section is filled in honestly — no missing
      ticks, no unjustified `[ ]` or `[!]`. If any item is incomplete, I
      either re-invoked the janitor with a follow-up or surfaced the gap to
      the human.
- [ ] I surfaced every Proposed Change to the human and got explicit approval
      before executing any deletion / split / new-page creation / status
      downgrade.

**The janitor's report MUST be able to claim** (you verify, you do not re-run):

- [ ] The janitor read the Schema (from this SKILL.md) at the start of the
      operation.
- [ ] Every page the janitor wrote or modified has the full frontmatter from
      Schema §3, using the constrained subset from Schema §5.
- [ ] Every inline factual claim about code carries an anchor (`path` or
      `path:symbol`).
- [ ] Every anchor the janitor added was resolved via the language MCP —
      NOT grep / find / sed / cat. Anchors it could not resolve are explicitly
      flagged in its report.
- [ ] No code block in any page is larger than a signature or a few essential
      lines.
- [ ] For `adopt`: the body prose of every adopted page is byte-identical to
      what was there before. The janitor only touched the frontmatter.
- [ ] `wiki_call reindex` (`check: true`) reported no duplicate slugs / malformed
      frontmatter (or they are surfaced in the janitor's report).
- [ ] `wiki_call freshness` reported `gating: 0`, or every remaining
      stale / unverified / orphaned-source page is reported.
- [ ] The janitor proposed (did not silently apply) every destructive change:
      deletions, splits, body rewrites, status downgrades unrelated to
      code-drift.
- [ ] No discrepancy between code and page was silently "harmonized". Where
      they disagreed, the page was marked stale and the conflict was
      surfaced.

If an item is not applicable (e.g. `query` does not write pages), state so
explicitly. Silence is a violation.

## Schema (the contract)

This section is the **configuration layer** of the wiki: the rules the engine
obeys when building and maintaining `docs/`. It ships as part of the `p:wiki`
skill and is loaded with it; the LLM applies it on every ingest / query / lint
pass and obeys it literally.

### 0. First principles

- **Source of truth is the code, not the wiki.** A wiki page is a *derived*
  artifact. When the code and the wiki disagree, the code wins and the page is
  stale.
- **Every claim about code is verifiable.** If a page states a fact about a
  symbol, it carries an anchor pointing at that symbol so a later lint pass can
  re-check it with clangd / luals / purity.
- **The wiki compounds.** Pages are updated in place, never re-derived from
  scratch. New information is integrated into existing pages.
- **Humans curate, the LLM maintains.** The human decides *what matters* and
  *what is wrong*; the LLM does summarization, cross-linking, and verification.

### 1. Layout

The wiki root is `docs/` by default (override with the skill `--root`
argument). The whole documentation tree lives here, so the wiki can grow to
cover the entire project — and Karpathy's immutable `sources/` raw-document
layer can be slotted in later without restructuring.

```
docs/
  INDEX.md             # generated index of all pages (one line per page) -- never hand-edited
  overview.md          # single root page: what the project is + page map
  subsystems/<name>.md
  components/<name>.md
  reference/<name>.md
  analysis/<name>.md
  concepts/<name>.md
  specs/<name>.md
  runbooks/<name>.md
  adr/NNNN-<slug>.md    # append-only, never edited after acceptance
  glossary.md
  sources/             # (future) Karpathy raw-source layer: immutable ingested documents
```

`INDEX.md` is regenerated by `reindex.py`, never hand-written, and is excluded
from page processing.

**Script location:** The wiki scripts (`freshness.py`, `reindex.py`) live in the
skill directory at `~/.claude/skills/p/skills/wiki/scripts/` (a symlink into the
repo's `ClaudeCode/skills/wiki/scripts/`). They are the **CI gate only**: a shell
invocation MUST use that absolute path, and MUST never assume the scripts exist
in the project's `scripts/` directory. **Agents do not shell out** — the janitor
has no `Bash` tool. Every interactive freshness/index run goes through `wiki_call`
(§7). Where an operation in §6 says "reindex" or "freshness", it means the
`wiki_call` function, not the script.

A third script sits beside them, `measure_cli.py`, and it is neither a CI gate
nor a mirrored copy: it is the **one-time bootstrap** for a repo whose running
`mcp-wiki` server predates `measure` / `verify` (§4b). It loads the committed
server and calls it; it restates none of its rules. Once the session's server has
the functions, use `wiki_call`.

### 2. Page types

| Type        | Purpose                                          | Source binding                  | Mutability  |
|-------------|--------------------------------------------------|---------------------------------|-------------|
| `overview`  | Project identity + map to all pages              | repo root, entry points         | living      |
| `subsystem` | A cohesive area (a directory / module cluster)   | a directory or set of dirs      | living      |
| `component` | A single unit inside a subsystem                 | one file / module / class       | living      |
| `reference` | API / symbol reference                           | specific symbols (verifiable)   | living      |
| `analysis`  | Performance, network, or behavioral investigation| capture data, measurements      | living      |
| `concept`   | A cross-cutting idea spanning subsystems         | multiple anchors                | living      |
| `spec`      | Forward/living design for a planned or freshly built feature | intended modules (`targets`) → real anchors (`sources`) | living |
| `runbook`   | Operational how-to (run X, debug Y, release Z)   | commands, scripts, config       | living      |
| `adr`       | A decision: what, why, alternatives, consequences| frozen at decision time         | append-only |
| `glossary`  | Domain terms                                     | none required                   | living      |

### 3. Page anatomy

Every page begins with frontmatter. To stay parseable by the stdlib-only
scripts, the frontmatter MUST use the constrained subset described in section 5.

```markdown
---
name: stream-proxy
type: component
status: active
title: RTMP stream proxy
description: One-line summary used in INDEX.md.
sources:
  - src/stream-proxy.c
  - src/stream-proxy.c:rtmp_read_packet
verified:
  commit: 0f7ddf7
  date: 2026-05-27
links:
  - codec-table
  - rtmp-flow
---
```

Frontmatter fields:

| Field         | Required | Meaning                                                        |
|---------------|----------|----------------------------------------------------------------|
| `name`        | yes      | Unique kebab slug; matches filename (without `.md`).           |
| `type`        | yes      | One of section 2.                                              |
| `status`      | yes      | `draft` / `active` / `deprecated`. Editorial intent only, never freshness — two hard rules below. |
| `title`       | yes      | Human title for INDEX.md.                                      |
| `description` | yes      | One-line summary for INDEX.md.                                 |
| `sources`     | type-dep | Code anchors the page derives from (paths or `path:symbol`).   |
| `targets`     | type-dep | Intended code anchors for not-yet-built code (`path` or `path:symbol`). The forward pair of `sources`. **NOT** freshness-verified. Promotes to `sources` once the code exists. |
| `verified`    | type-dep | `commit` + `date` of last successful verification.             |
| `links`       | no       | Related page slugs; also rendered inline as `[[slug]]`.        |
| `aliases`     | no       | Alternative words a searcher would use for this page's topic, when the prose does not use them. **Indexed** as a weighted search field (weight 5, the anchor's), and it DOES count toward the relevance gate's coverage. Two hard rules below. |

**`status:` — editorial intent, and it may never claim freshness.**

It answers *"is this page finished, and does it still describe a live design?"* —
`draft` (being written, or awaiting promotion), `active` (promoted), `deprecated`
(describes a design that is gone). Only a human can know any of the three.
Freshness is a **different axis**, measured against git per query by `freshness` /
`search` (§4), and it is never hand-written here.

1. **`current` and `stale` are forbidden values.** They collide word-for-word
   with two of the eight measured states, and that collision is not theoretical:
   `INDEX.md` printed `` `[current]` `` for all ten pages while git measured nine
   of them stale. A hand-written field cannot track HEAD, so it must not use
   HEAD's vocabulary.
2. **`INDEX.md` renders this field only when it is `draft` or `deprecated`.** An
   `active` page carries no label at all — the index is a catalogue of *what
   exists*, and it makes no claim a reader could mistake for a freshness
   measurement. Ask `search` or `freshness` for that; both label every page
   against git at the moment you ask.

**`aliases:` — the synonym layer, and it has two rules that are not style.**

The problem it solves, measured: `adr/0001-purity-server-unification` records a
*merge* decision but its prose says `fold` / `unify` / `unification` throughout, so
a searcher asking about `merge` lost the one page that answers — it sat at 38–54%
coverage, below the 55% gate, and was deleted from the results. The document is
written by whoever knows the answer; the question is asked by whoever does not.

1. **An alias may never introduce a word the corpus does not already carry in
   prose.** Measured: adding `merge` (which two other pages write) left the gate's
   calibration window bit-identical; adding `verbosity` (which NO page writes)
   *closed* it at a single word — a word no page carries earns the maximum idf, and
   that is exactly what makes the search able to say "I don't know". An alias
   RE-ROUTES vocabulary; it never invents it. Not machine-checked yet.
2. **Aliases come from OBSERVATION, never from imagination.** Two channels
   qualify: a **failed query** (a word someone actually searched for and got
   nothing), or a **sibling page** anchoring the same source file that uses the
   word about the same code. Guessing is measurably worthless — Furnas et al.
   (CACM 1987) found expert authors' keywords "fared no better than average", and
   that one person rarely produces more than a half dozen of the hundred names a
   population would use. Three guessed aliases are worth about one good title.

Note the asymmetry with `type`: a page's *type* only reorders results (a category
is not a claim about content), while an alias *is* a claim about content and can
therefore admit a page whose text never writes the word.

`sources`/`verified` are required for `subsystem`, `component`, `reference`,
`concept`, `runbook`. They are optional for `overview`, `analysis`, `adr`,
`glossary`. Analysis pages are investigative captures (network traces,
performance measurements) typically not tied to specific source files.

**The sources⇒verified invariant is type-agnostic.** Any page of ANY type that
carries `sources:` MUST also carry `verified:`. `freshness` gates such a page as
`unverified` the moment `verified.commit` is absent, regardless of type — the
`overview` / `adr` / `glossary` exemption in `UNTRACKED_TYPES` is only reached by
a page carrying NEITHER `sources:` nor `targets:`
(`ClaudeCode/skills/wiki/scripts/freshness.py`). So "optional" above means
*optional to carry sources at all*, never *optional to verify the sources you
did carry*. Where a page's sources genuinely cannot be verified yet — the code
exists only in an uncommitted working tree, so any `verified.commit` would be a
false claim — the correct expression is `status: draft` (§3, "awaiting
promotion"), not an omitted `verified:` under `status: active`.

A `spec` page is the same genre as a `subsystem`/`component` design, but it may
exist *before* its code does. Its anchor requirements depend on `status`
(documentation-only — no script enforces these; they are a curation rule):

- `status: draft` → `targets:` required; `sources:`/`verified:` absent or optional.
- `status: active` → `sources:` + `verified:` required (like a subsystem/component);
  `targets:` only for the parts not yet built.
- **Invariant**: any `spec` that carries `sources:` MUST also carry `verified:`
  (otherwise `freshness.py` gates it as `unverified`).
- An anchor MUST NOT appear in both `targets:` and `sources:` at once
  (documentation-only, not machine-checked — future lint work).

Body rules:

- Lead with a 1-3 sentence **purpose** statement — what this thing is, plainly.
- State a fact, then anchor it inline: `` the proxy reuses one buffer per stream `src/stream-proxy.c:rtmp_read_packet` ``.
- Link related pages liberally with `[[slug]]`.
- **Do not** paste large code blocks — link to the source. Quote at most a
  signature or a few lines when essential.
- **Do not** duplicate what an inline docstring already says verbatim —
  summarize the *why* and the *shape*, point to the code for the *what*.

### 4. Source anchors (the verifiable contract)

An anchor is `path` or `path:symbol`, with `path` **relative to the repo root**.
Anchors live in two places:

1. The frontmatter `sources:` list — what the whole page is about.
2. Inline in the body, next to a claim — what *that sentence* depends on.

A page's freshness is defined against its anchors:

- A page is **stale** if any `sources` path changed in git since `verified.commit`.
- An anchor is **orphaned-source** if its `path` no longer exists in the tree.
- An inline anchor is **broken** if the `symbol` no longer resolves
  (clangd / luals / purity workspace-symbol lookup returns nothing).
- An anchor is **drifted** if the symbol exists but its signature/type changed
  since last verification — flag for human review, do not silently rewrite.

Division of labor: file-level freshness (`stale`, `orphaned-source`) is
detected cheaply by `wiki_call` `freshness` (git only; the `freshness.py` CI gate
runs the same logic). Symbol-level checks
(`broken`, `drifted`) require the language MCP servers and happen during the
LLM lint pass — never with grep/find.

#### Forward anchors (`targets`)

A `targets:` anchor names code that is *intended* but does **not exist yet**, so
it is the forward pair of `sources:` and is **not freshness-tracked**: it never
produces `orphaned-source` (the code is deliberately absent), and `_evaluate()`
never inspects it. It lets a forward `spec` carry a module signal before the
code lands, so a code-scoped search can find the design via its target path.

When a `targets:` path **materializes** (the file appears in the tree),
`freshness.py` reports the page as `promotable`. Promotion is then a manual
curation step: move the anchor `targets:` → `sources:`, set
`verified.commit`/`verified.date`, and flip `draft → active`.

`freshness.py` adds two non-gating statuses for forward specs:

- **`planned`** — the page has `targets:` but no `sources:`, and no target has
  materialized yet (forward spec, code still absent).
- **`promotable`** — at least one `targets:` path now exists; the page is ready
  to promote. Listed page-by-page (actionable); `planned` is summarized as a count.

**Status precedence**: a gating status (`stale` / `orphaned-source` /
`unverified`) > `promotable` > `current`. `promotable` only surfaces when the
`sources:` side is otherwise `current` — so a materialized target can never mask
a real gating condition on a page's sources. Neither `planned` nor `promotable`
gates CI.

### 4b. Measured regions (a number is never typed)

**The rule, and it is law like the rest of this schema: a number that describes
the tree is NEVER typed into a page.** It is one of exactly two things — a
**measured region**, whose body a command renders, or an **anchor** pointing at
the code that computes it. There is no third option, and "I checked it just now"
is not one.

This is §0's "every claim about code is verifiable", applied to arithmetic. An
unanchored sentence about a symbol is already a violation; a count of servers,
regions, pages or call sites is the same kind of claim, differing only in that no
lint pass can re-check it by resolving a name. A typed count is right on the day
it is written and silently wrong forever after — and measured on this corpus, it
does not even survive the day: two numbers in one page had already been
hand-corrected once and were stale again, and `INDEX.md` printed `[current]` for
ten pages while git measured nine of them stale.

**Three carve-outs, measured on the real corpus. A number stays typed when:**

1. **It is history, not a measurement.** A claim about what a page or a file
   *used to say* — "this line read `116 cases` until the commit that added the
   freshness cases", "the paging row read `5` for as long as it was typed by
   hand" — is a **quotation**. Re-rendering it would destroy the very thing it
   reports. Verbatim, always, and never inside a measured region.
2. **It is a frozen record.** An `adr` is append-only (§2) and its numbers are
   the measurement *at the moment of the decision* — which is what makes the
   decision auditable years later. An ADR whose census silently re-rendered would
   be today's data arguing for a choice made against different data. ADRs keep
   their dated numbers; if the LIVE value matters, it belongs in a measured
   region on the living page the ADR links to.
3. **It is a section heading.** A heading is an anchor surface: `#heading-slug`
   links, `get_page`'s `section` param and the section index all key on its text,
   so re-rendering a number inside one breaks every link that names it. Put the
   measured region in the body *under* the heading; leave the heading alone.

Everything else — a count in a sentence, a table cell, a bullet — is a measured
region or an anchor.

**The registry.** `docs/measurements.json`: `{"version": 1, "measurements":
{"<name>": {"description": ..., "command": [...]}}}`. `command` is an argv LIST
(no shell), run with the repo root as its working directory. `description` is
REQUIRED, and it is the one field here that is not plumbing: a rendered block
with no sentence saying what it counts is a number nobody can check, which is the
defect this mechanism removes rather than automates.

**The markers.** A region is an HTML-comment pair at **column 0**, each on a line
of its own, the END carrying the first 12 hex chars of the SHA-256 of the body
between them:

```markdown
<!-- BEGIN MEASURED: generated-region-census -->
…rendered body…
<!-- END MEASURED: b9b14f6e3338 -->
```

**Opening a region by hand — copy this literal, do not retype it:**

```
<!-- END MEASURED:  -->
```

**That is TWO spaces between the colon and `-->`.** The digest field is empty and
the surrounding single spaces are both still required, so the empty spelling has
two. One space is not a lenient near-miss, it is a **hard refusal**: the scanner
reads `<!-- END MEASURED: -->` as a marker it cannot parse and aborts the whole
page by name and line — deliberately, because a marker that is ALMOST right, if
skipped, is a region nobody ever updates again. It is also the single easiest
thing to mistype and it is **invisible in every rendered view**, so verify it in
the raw bytes, never in a preview. An empty digest classifies `stale` without
executing anything, which is the correct opening state: nothing has rendered this
region yet.

**Two inertness rules, and a page documenting this mechanism depends on both:**

1. A marker must start at **column 0** and be the whole line. A leading space, a
   backtick, a `- ` bullet or any prose before it, and it is not a marker.
2. A marker **inside a fenced code block is inert** — which is exactly what lets
   a page show the syntax (as this section does) without opening a region.

A clean `measure` does NOT prove that a marker the page *shows* is inert; it only
proves the scanner did not accept it as a region, which is also what a silently
malformed marker looks like. Check the flag explicitly — `regions` in the
bootstrap CLI prints every marker-shaped line with its verdict.

**The states.** `ok` (digest matches the body AND the body matches what the
command produced) / `stale` / `hand-edited` (the body no longer hashes to its
recorded digest — a human typed inside the region, so it is **REFUSED**, never
silently overwritten, unless `force` says otherwise) / `unregistered` (no
registry entry) / `failed` (the command could not answer) / `not-rendered`
(nobody authorized execution, so the region is UNCHECKED — which is neither a
pass nor a defect and is the one state that must never be dressed up as either).
`stale`, `hand-edited`, `unregistered` and `failed` GATE. `ok` and
`not-rendered` do not — and they do not for opposite reasons, which is why they
are two words and not one.

### 5. Frontmatter format (stdlib-parseable subset)

The scripts use a minimal hand-written parser, not a full YAML engine. Pages
MUST keep frontmatter within this subset:

- Top-level `key: value` scalars.
- One level of nesting for `verified:` only:
  ```
  verified:
    commit: 0f7ddf7
    date: 2026-05-27
  ```
- Block lists for `sources:`, `targets:`, `links:`, and `aliases:`:
  ```
  sources:
    - src/a.c
    - src/a.c:func
  ```
- Inline lists are also accepted: `links: [codec-table, rtmp-flow]`.
- No inline `#` comments inside values. Full-line `#` comments and blank lines
  are ignored.
- Indentation is two spaces. No tabs.

### 6. Operations

#### Ingest (code changed -> update pages)
1. Take a git diff (changed file list), e.g. `git diff --name-only <base>..HEAD`.
2. Map changed files -> affected pages via frontmatter `sources`.
3. For each affected page: update the prose, re-verify inline anchors with the
   language MCP, bump `verified.commit` / `verified.date`, set `status: active`.
4. If a changed file maps to no page and looks significant, **propose** a new
   page — do not create silently; list it for the human.
5. Refresh `INDEX.md` via `wiki_call` `reindex`.

#### Query (answer + write-back)
1. Search pages first; fall back to code via clangd / luals / purity.
2. Answer with citations (page slugs + code anchors).
3. If the answer required deriving something durable not yet in the wiki, file
   it back into the right page.

#### Lint (periodic audit, no code change required)
1. `wiki_call` `freshness` -> report listing stale / unverified / orphaned-source pages, with a `gating:` count.
2. `wiki_call` `reindex` with `check: true` -> dup slugs, malformed frontmatter, link-graph orphans; writes nothing. **Note the two senses of "orphan":** this one is *link-graph* orphan (nothing links to the page), while `freshness`'s `orphaned-source` means a `sources:` path no longer exists. A clean `reindex` audit does NOT imply a clean `freshness` — read both.
3. **Only then** the LLM looks at the flagged pages: resolve inline symbol
   anchors with the language MCP, detect drift and contradictions, propose fixes.
4. Never auto-delete. Fixes are proposed; the human approves.

#### Init (bootstrap a repo)
1. Create the `docs/` skeleton from section 1.
2. Generate `overview.md` from the repo's top-level structure.
3. `wiki_call` `reindex`.

#### Adopt (onboard an existing docs tree)
For a repo that already has hand-written docs. Adopt **preserves the prose** and
backfills the contract; it does not rewrite content.
1. `wiki_call` `reindex` with `check: true` -> the `malformed` list is the worklist.
2. Per doc, without touching its body: classify into a page type, infer
   `sources` anchors (locate the described code via the language MCP), add
   frontmatter with `verified.commit: <HEAD>` and `status: draft`.
3. Verify pass: check each adopted page's claims against the code. Only on
   success flip `draft -> active`; a hand-written doc may already be stale, so
   `verified.commit = HEAD` is an *assertion* until verified.
4. Propose splits for docs spanning multiple types; never auto-split.
5. Reindex and re-check.

### 7. Verification routing (MANDATORY)

Anchor resolution and code reading use the MCP servers, never grep / find / sed:

- C / C++ / Objective-C symbols -> purity MCP (`symbol`, `find_definition`, `type_at`).
- CUDA symbols -> purity MCP (same functions; the clangd backend is CUDA SDK aware).
- Lua symbols -> purity MCP (`luals_workspace_symbols`, `luals_find_definition`, `luals_hover`).
- File existence / text presence -> purity MCP (`find_file`, `search_for_pattern`).
- Wiki page search / read / structure -> `mcp-wiki` `wiki_call` (`search`, `get_page`, `list`, `source_to_pages`, `stats`).
- Git freshness / index (did sources change since a commit; regenerate INDEX) -> `mcp-wiki` `wiki_call` (`freshness`, `reindex`). The CLI `~/.claude/skills/p/skills/wiki/scripts/freshness.py` / `reindex.py` remain as the pre-PR CI gate.
- Corpus-wide anchor resolution + measured-region state (§4b) -> `mcp-wiki` `wiki_call` (`verify`, `measure`). `verify` is the cheap sweep that says WHICH anchors to re-resolve properly; the language MCP above is still the authority on any symbol it flags. Only when the session's server predates those two functions, `~/.claude/skills/p/skills/wiki/scripts/measure_cli.py` drives the committed server instead.

### 8. Anti-scope (do NOT document)

- Trivial getters/setters or self-evident code.
- Anything already in CLAUDE.md (link to it instead).
- Git history / who-changed-what (git is authoritative).
- Step-by-step fix recipes for one-off bugs (those belong in commit messages).
- Speculative future plans. A concrete forward **design** → a `spec` page with
  `targets:` and `status: draft`; a contested **decision** with alternatives and
  consequences → an `adr` (ADR = frozen WHY; spec = living WHAT/HOW). A vague
  someday-maybe → an issue, not a page.

## Reference

- [Schema](#schema-the-contract) (above) — layout, page types, frontmatter
  anatomy, anchors, freshness model, lint rules, verification routing,
  anti-scope.
- `p:minion-librarian` (`ClaudeCode/agents/minion-librarian.md`) — the
  executor agent. Reads the schema (from this SKILL.md), runs `wiki_call`
  (freshness / reindex / search), opens pages, verifies anchors via MCP, applies
  non-destructive updates, surfaces destructive
  proposals. Forbidden from deleting files — that stays with the main agent
  under explicit human approval.
