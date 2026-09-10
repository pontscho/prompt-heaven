---
name: generated-regions
type: component
status: active
title: Generated regions — how the MCP fleet shares plumbing without importing it
description: The amalgamate generator, its three canonical sources, the four rules that decide what may be a shared block, and the two registers of deliberate exclusion.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_lsp.py
  - Scripts/_mcp_paging.py
  - tests/test_generated_region.py
verified:
  commit: 7459e17
  date: 2026-09-10
links:
  - scripts
  - tests
  - 0009-the-first-reader-is-a-cold-model
  - 0010-a-handler-failure-must-reach-iserror
---

# Generated regions

The fifteen MCP servers in [[scripts]] share their plumbing by **generation, not
import**: a canonical function is pasted into each server between two comment
markers, and a generator re-renders it on demand. There is no runtime dependency
between servers, no shared package, and no import that could carry a helper from
one file to another.

**Scope note.** This page is about the *MCP fleet's* generated regions. The repo
contains a second, unrelated marker-delimited generation mechanism — the
checkpoint file's table of contents, whose one-writer design is recorded in
[[0009-the-first-reader-is-a-cold-model]]. The two share a pattern and nothing
else.

## Why generation rather than an import

Every server is a self-contained single file, and the rejection of a shared
import is argued identically in all three canonical sources and in the suite
`Scripts/_mcp_json.py`, `Scripts/_mcp_lsp.py`, `Scripts/_mcp_paging.py`,
`tests/test_generated_region.py`. Three reasons: an import would write bytecode
into a tree the fleet asserts is empty; it would need a `sys.path` entry the test
harness's loader never adds; and it would move the helpers out of the module
attributes the footprint suite reaches for.

The cost is accepted openly — duplication is the *mechanism*, and the generator
plus its gate are what keep the copies from diverging.

## The mechanism

A region is a pair of comment markers, and the closing marker carries a truncated
digest of what sits between them `Scripts/amalgamate.py`. The opening marker
names a canonical source and one or more block names; the digest is a 12-character
SHA-256 over the **emitted body alone**, markers excluded `Scripts/amalgamate.py:body_hash`.

Markers are located with `tokenize` and only `COMMENT` tokens count, so a marker
quoted inside a docstring or a string literal is inert — the generator's own
docstring contains one, and the suite uses that as a live negative control.

A region is in one of three states `Scripts/amalgamate.py:Region`:

- **ok** — the body is byte-identical to a fresh render *and* the recorded digest
  matches it. `--check` requires both; either alone is not enough.
- **hand-edited** — a digest was recorded and the body no longer hashes to it.
  This is **refused, never silently overwritten**, unless forced. The file gets
  exactly one writer.
- **stale** — everything else, including an empty body and a never-written digest.

A `BEGIN` without an `END` is a hard error rather than a skip, because a region
that quietly stops being maintained is the whole failure the mechanism exists to
prevent `Scripts/amalgamate.py`.

As of the verified commit: **56 live regions across the 15 servers, emitting 70
block instances** — a single region may name several blocks. `_json_error_window`
is in every server; the `_result` / `_error` pair is in fourteen of fifteen.

## Three canonical sources, and why three

The registry is a hand-written tuple, not a glob `Scripts/amalgamate.py:CANONICAL_NAMES`,
because a glob would let an unrelated file become a generation source by merely
existing — and the marker naming it would look exactly as legitimate as the ones
that belong. `Scripts/_mcp_smoke_test.py` is the standing example: an `_mcp_*.py`
file that is deliberately not a source.

Each source is **a domain, not a shelf**, and the JSON source has been narrowed
twice — framing left for the LSP source, row accounting for the paging source —
with both departures asserted as departures in the suite.

| Source | Blocks | Domain |
|---|---|---|
| `Scripts/_mcp_json.py` | 5 | JSON-RPC envelopes, wire-value coercion, JSON error reporting |
| `Scripts/_mcp_lsp.py` | 1 | LSP `Content-Length` framing over stdio |
| `Scripts/_mcp_paging.py` | 2 | the two halves of the pager protocol |

The source name on a marker **selects the block map**: a name is resolved against
that source and no other, and an unknown source is refused by name with the known
ones listed `Scripts/amalgamate.py:audit_text`.

## What may be a block — four constraints

**Only top-level functions and classes.** `load_blocks_text` walks a source's
`tree.body` and maps `FunctionDef`, `AsyncFunctionDef` and `ClassDef` only
`Scripts/amalgamate.py:load_blocks_text`. Two consequences: a module-level
assignment cannot be a block — which is why the paging source's own list of
queued additions contains three names that are not extractable as written — and a
method inside a class cannot be one either, which is why `_result` and `_error`
are defined at module top level in the JSON source despite being methods at their
destination.

**The host must already import every free name.** `free_names` over-reports
deliberately, and it catches two holes that were once live: a block calling
another canonical block, and a block whose *annotation* needs an import — an
annotation is evaluated at def time, so a host missing the name dies at startup
while the block looks fine where it is written. The host half,
`host_provides`, returns **module-level import aliases only** — not defs, not
assignments. A free name the host *defines* is therefore still a refusal, and the
region is rejected by name at END-marker time `Scripts/amalgamate.py:host_provides`.

**Tab safety is decided per block, mechanically.** `block_is_tab_safe` requires
both that no line join happens while a bracket is open and that every leading
whitespace run is a whole multiple of four; anything unprovable is unsafe
`Scripts/amalgamate.py:block_is_tab_safe`. The host's style is read from its own
indent tokens, not from the marker's column. This **narrowed an earlier per-file
rule** that refused tab hosts outright and cost one server three hand copies that
were byte-identical modulo the indent character. Live scope today is a single
(block, host) pair: both tab-indented servers host regions, and exactly one of
the eight blocks is unsafe.

**A name must resolve against the source that was named.** Cross-source
resolution is refused, and the suite gates both halves — the refusal *and* a
positive case proving the right body was selected, because "refused" alone could
be satisfied by a merged namespace that got lucky.

## Two registers of deliberate exclusion

The rule is not "duplication is bad". Two distinct kinds of copy are left in
place on stated grounds, and they are counted differently.

**Hand copies of things that ARE blocks** are censused by the suite: it
intersects every server's top-level names with the canonical block names,
subtracts what regions cover, and reports the remainder as INFO rather than FAIL
— "this server keeps its own" is a legitimate answer, but an *undeclared* copy
cannot appear without landing on that line. Four survive, each declared with its
reason: a parameter-name-and-raise variant, an older signature, an allow-list
where the canonical is a deny-list, and one that is both tab-unsafe and a body
divergence.

**Things that are not blocks at all** are documented in `Scripts/MCP_SKELETON.md`
rather than censused, because the census cannot see them. `_tool_error` *cannot*
be a block — its free names include the host's own server class, and host
provision is imports-only. `_ErrorText` *could* be one — byte-identical copies,
empty free names — and deliberately is not, because blessing a second mechanism
as generated infrastructure would buy drift protection for a divergence; see
[[0010-a-handler-failure-must-reach-iserror]].

## The gate

`tests/test_generated_region.py` declares 73 cases in six groups: A gates the
live tree against the canonical sources, B the marker and hash contract, C the
negative controls, D hygiene, E what each shared block actually *does*, and F tab
safety. Seventy-two are gated failures; the hand-copy census is the one
informational case.

Group E is not optional — the suite argues that a drift gate on its own would
only ever prove that fourteen files agree on the same bug.

The format contract is spelled out **independently** of the generator rather than
imported from it: a test that imported the marker constants would sail through a
rename that orphaned every region already written into a server.

## Known gaps

- **Two of the generator's rules have no test case.** Nothing asserts that a
  module-level assignment is skipped by the block loader, and nothing exercises
  the `free_names` refusal directly — it fires only incidentally, because every
  live region passes it. If either check silently stopped working, the suite
  would stay green.
- **The census cannot see a hand copy that lives inside a class**, because the
  block loader walks module top level only. At least one server's `_result` and
  `_error` are exactly that: genuine hand copies, declared in
  `Scripts/MCP_SKELETON.md`, absent from the census's count of four.
