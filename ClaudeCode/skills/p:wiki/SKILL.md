---
name: p:wiki
description: Build and maintain a project's documentation wiki under docs/ as a persistent, compounding, code-verified knowledge base (Karpathy LLM-wiki pattern). Use to keep docs in sync with code, audit stale/broken docs, answer questions from docs, or bootstrap docs for a repo. Trigger: /p:wiki, /p:wiki ingest, /p:wiki lint, /p:wiki query, /p:wiki init, /p:wiki adopt, "update the docs", "is the documentation stale", "document this subsystem", "onboard existing docs into the wiki", "dokumentacio frissites", "meglevo doksik beszervezese".
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
- You MUST read [SCHEMA.md](SCHEMA.md) (or `docs/SCHEMA.md` if present) BEFORE
  any operation — not after, not "if relevant". First. The schema is law. (The
  janitor reads the schema too; you read it so you can validate its report.)
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

This skill is the **engine** (reusable method). The **schema** (page types,
frontmatter anatomy, anchors, freshness rules, anti-scope) lives in
[SCHEMA.md](SCHEMA.md) — read it before doing any wiki work. A repo may override
it with its own `docs/SCHEMA.md`; if that exists, it wins.

## Golden rules (violations are failures, not preferences)

1. **DELEGATE TO `p:minion-librarian`.** Every operation runs in the janitor's
   sandbox, not the main context. The janitor reads pages, verifies anchors,
   runs scripts, and applies non-destructive changes. The main context only
   sees the result. Executing the work inline is a violation.
2. **READ [SCHEMA.md](SCHEMA.md) FIRST.** Every operation, every time. The
   schema defines layout, page types, and the exact frontmatter subset the
   scripts can parse. Obey it literally — deviation is a violation. (The
   janitor reads it too; you read it so you can sanity-check the report.)
3. **Freshness/index checks run first, you read second.** ALWAYS run
   `wiki_call` `freshness` / `reindex` (via the janitor) to find *what* to look
   at before opening any page. The janitor enforces this — if its report skips
   them, reject the report.
4. **MCP routing is mandatory.** Resolve symbols and read code via clangd /
   cuda / luals / purity / git MCP, and search / freshness / index the wiki via
   the `mcp-wiki` `wiki_call` tool — NEVER grep / find / sed / cat / awk /
   head / tail. See SCHEMA §7. No exceptions, no fallbacks. Applies to the
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

For each operation: (1) read SCHEMA.md, (2) invoke `p:minion-librarian` with
the params below, (3) review the report and surface it to the human, (4)
execute any approved destructive proposals yourself.

### `/p:wiki ingest [<base-ref>]`
Code changed -> update affected pages.

Delegate with `op=ingest`, `base=<base-ref>` (default: merge-base with main, or
`HEAD~1` if unsure — ask if ambiguous).

The janitor will: diff against base; map changed files to pages via
frontmatter `sources`; for each affected page rewrite the prose, re-verify
inline `path:symbol` anchors via MCP, bump `verified.commit` / `verified.date`,
set `status: current`; reindex. Significant changed files mapping to NO page
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
It applies the automatic `status: stale` flip for code-drift cases, and
surfaces every other fix (including deletions) as a proposal.

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

The janitor will: create the `docs/` skeleton (SCHEMA §1); surface a proposal
to copy `SCHEMA.md` to `docs/SCHEMA.md` for per-repo customization; draft
`overview.md` from the repo's top-level structure (batched survey, not
unbounded exploration); reindex. Your job: review the drafted overview,
approve the schema copy proposal, and confirm with the human.

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
`status: draft`. Then verify pass: where claims hold, flip `draft → current`;
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

The underlying logic still lives in two stdlib-only, Python 3.9+ scripts that
never call an LLM and only ever write `INDEX.md`. They remain as a **CLI CI
gate** (exit codes drive PR checks); `wiki_call` is the interactive/agentic path:

```bash
~/.claude/skills/p:wiki/scripts/freshness.py --root docs [--head <ref>] [--quiet]
~/.claude/skills/p:wiki/scripts/reindex.py   --root docs [--check]
```

- `freshness.py` exits non-zero if any page is stale — usable as a pre-PR CI gate.
- `reindex.py` regenerates `INDEX.md` by default; `--check` audits without
  writing. It exits non-zero on duplicate slugs or malformed frontmatter.

Use the CLI scripts ONLY for a CI gate or a quick main-context sanity check.
All interactive work (freshness, reindex, search, page reads, anchor
verification) goes through the janitor and `wiki_call`.

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
- [ ] I read SCHEMA.md (or `docs/SCHEMA.md` if present) before validating the
      janitor's report.
- [ ] The janitor's Self-check section is filled in honestly — no missing
      ticks, no unjustified `[ ]` or `[!]`. If any item is incomplete, I
      either re-invoked the janitor with a follow-up or surfaced the gap to
      the human.
- [ ] I surfaced every Proposed Change to the human and got explicit approval
      before executing any deletion / split / new-page creation / status
      downgrade.

**The janitor's report MUST be able to claim** (you verify, you do not re-run):

- [ ] The janitor read SCHEMA.md (or `docs/SCHEMA.md` if present) at the start
      of the operation.
- [ ] Every page the janitor wrote or modified has the full frontmatter from
      SCHEMA §3, using the constrained subset from SCHEMA §5.
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

## Reference

- [SCHEMA.md](SCHEMA.md) — layout, page types, frontmatter anatomy, anchors,
  freshness model, lint rules, verification routing, anti-scope.
- `p:minion-librarian` (`ClaudeCode/agents/p/minion-librarian.md`) — the
  executor agent. Reads the schema, runs `wiki_call` (freshness / reindex /
  search), opens pages, verifies anchors via MCP, applies non-destructive
  updates, surfaces destructive
  proposals. Forbidden from deleting files — that stays with the main agent
  under explicit human approval.
