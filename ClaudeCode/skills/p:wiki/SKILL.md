---
name: p:wiki
description: Build and maintain a project's documentation wiki under docs/ as a persistent, compounding, code-verified knowledge base (Karpathy LLM-wiki pattern). Use to keep docs in sync with code, audit stale/broken docs, answer questions from docs, or bootstrap docs for a repo. Trigger: /p:wiki, /p:wiki ingest, /p:wiki lint, /p:wiki query, /p:wiki init, /p:wiki adopt, "update the docs", "is the documentation stale", "document this subsystem", "onboard existing docs into the wiki", "dokumentacio frissites", "meglevo doksik beszervezese".
---

# Project Wiki Engine

Maintain `docs/` as a **persistent, compounding documentation wiki** that stays
verifiably in sync with the code. The wiki is a *derived* artifact: the code is
the source of truth, and every factual claim about code carries an anchor that a
lint pass can re-check.

This skill is the **engine** (reusable method). The **schema** (page types,
frontmatter anatomy, anchors, freshness rules, anti-scope) lives in
[SCHEMA.md](SCHEMA.md) — read it before doing any wiki work. A repo may override
it with its own `docs/SCHEMA.md`; if that exists, it wins.

## Golden rules

1. **Read [SCHEMA.md](SCHEMA.md) first.** It defines layout, page types, and the
   exact frontmatter subset the scripts can parse. Obey it literally.
2. **Cheap work is the scripts' job; semantic work is yours.** Run
   `freshness.py` / `reindex.py` to find *what* to look at before reading any
   page. Do not hand-scan the tree.
3. **MANDATORY MCP routing.** Resolve symbols and read code via clangd / cuda /
   luals / purity / git MCP — never grep / find / sed / cat. See SCHEMA §7.
4. **Never auto-delete or silently rewrite.** Propose changes; the human approves.
5. **The code wins.** When code and a page disagree, the page is stale.

## Operations

Dispatch on the argument: `ingest`, `lint`, `query`, `init`, `adopt`. With no
argument, ask which operation, or infer from the request.

### `/p:wiki ingest [<base-ref>]`
Code changed -> update affected pages.
1. Get the changed files: `git diff --name-only <base-ref>..HEAD` (default base:
   the merge-base with the main branch, or `HEAD~1` if unsure — ask if ambiguous).
2. Map changed files -> affected pages via each page's frontmatter `sources`.
   (`freshness.py` already computes this mapping — use its report.)
3. For each affected page: rewrite the prose to match reality, re-verify inline
   `path:symbol` anchors with the language MCP, then bump `verified.commit` and
   `verified.date` and set `status: current`.
4. Significant changed files mapping to **no** page -> propose new pages (list
   them, do not create silently).
5. Run `python scripts/reindex.py --root docs` to refresh `INDEX.md`.

### `/p:wiki lint`
Audit without any code change.
1. `python scripts/freshness.py --root docs` -> prose report: which pages are `stale` /
   `unverified` / `orphaned-source`.
2. `python scripts/reindex.py --root docs --check` -> orphans (no inbound link),
   duplicate slugs, malformed frontmatter.
3. **Only then** open the flagged pages. For each inline `path:symbol` anchor,
   resolve it via the language MCP: missing -> `broken`; signature changed ->
   `drifted`. Detect cross-page contradictions.
4. Report findings grouped by severity. Propose fixes; do not apply destructive
   ones without approval.

### `/p:wiki query "<question>"`
Answer from the wiki, fall back to code.
1. Search `docs/` pages first; fall back to clangd / luals / purity on the code.
2. Answer with citations: page slugs + code anchors.
3. If you derived something durable not yet captured, file it back into the
   right page (and reindex).

### `/p:wiki init [--root docs]`
Bootstrap a repo.
1. Create the `docs/` skeleton (see SCHEMA §1).
2. Offer to copy `SCHEMA.md` to `docs/SCHEMA.md` for per-repo customization.
3. Draft `overview.md` from the repo's top-level structure (use the MCP servers
   to survey it — consider delegating the survey to `p:minion-explorer`).
4. Run `python scripts/reindex.py --root docs`.

### `/p:wiki adopt [--root docs]`
Onboard a repo that *already* has hand-written docs into the wiki. Unlike
`ingest`, adopt **preserves the existing prose** — it backfills the frontmatter
contract and infers anchors; it does not rewrite the doc.

1. Point `--root` at the existing docs directory.
2. `python scripts/reindex.py --root <dir> --check` -> the `malformed` list is
   your worklist: every doc lacking frontmatter needs adopting.
3. For each doc, **without changing its body**:
   a. Classify it into a page type (SCHEMA §2).
   b. Infer `sources` anchors: read the doc and locate the code it describes via
      clangd / luals / purity. Delegate the code-location to `p:minion-explorer`
      for anything non-trivial. Mark anchors you are unsure about.
   c. Add frontmatter with `verified.commit: <HEAD>` and **`status: draft`**
      (NOT `current`). Add `[[links]]` to connect related pages.
4. **Verify pass (this is what earns `current`).** For each adopted page, check
   its claims against the code with the language MCP. `verified.commit = HEAD`
   is only an *assertion* until verified — a hand-written doc may already be
   stale. If the claims hold, flip `status: draft -> current`; otherwise leave
   it `draft` and report the discrepancies. Do not silently "fix" the prose.
5. A monolithic doc that spans multiple types -> **propose** a split; do not
   auto-split.
6. If there is no `overview.md`, draft one (as in `init`).
7. Run `python scripts/reindex.py --root <dir>` and `freshness.py` to confirm a
   clean structure.

For a repo with many docs, adopt in batches and let the human review the `draft`
pages before promoting them.

## Scripts

Both are stdlib-only, Python 3.9+, and never call an LLM or modify pages
(`reindex.py` only writes `INDEX.md`). Run them from the repo root.

```bash
python scripts/freshness.py --root docs [--head <ref>] [--quiet]
python scripts/reindex.py   --root docs [--check]
```

- `freshness.py` exits non-zero if any page is stale — usable as a pre-PR CI gate.
- `reindex.py` regenerates `INDEX.md` by default; `--check` audits without
  writing. It exits non-zero on duplicate slugs or malformed frontmatter.

For large surveys or multi-round exploration during `ingest`/`init`, delegate to
`p:minion-explorer` rather than scanning inline.

## Reference

- [SCHEMA.md](SCHEMA.md) — layout, page types, frontmatter anatomy, anchors,
  freshness model, lint rules, verification routing, anti-scope.
