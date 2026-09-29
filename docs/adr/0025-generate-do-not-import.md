---
name: 0025-generate-do-not-import
type: adr
status: active
title: Generate the shared code into each file, do not import it
description: Retroactive record of the decision taken in 8effd2f that code the MCP fleet shares is generated into each single-file server between hashed markers rather than imported from a sibling module, with its four measured grounds, why Cog was not used, the hooks precedent, the re-check ADR 0023 made for a host that is not a server, the alternatives rejected, and the costs accepted.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_concurrency.py
  - Scripts/MCP_SKELETON.md
  - tests/test_generated_region.py
verified:
  commit: 0653fab
  date: 2026-09-29
links:
  - generated-regions
  - scripts
  - tests
  - 0009-the-first-reader-is-a-cold-model
  - 0014-a-canonical-source-is-a-domain
  - 0023-the-websocket-client-is-a-sixth-domain
---

# ADR 0025: Generate the shared code into each file, do not import it

**Status:** accepted, retroactively. The decision was taken and shipped in
`8effd2f` on 2026-09-09. It was argued in that commit's body and restated in
every canonical source's docstring, but it never had a page of its own.
[[0014-a-canonical-source-is-a-domain]] depends on it and says it does not
restate it. This page writes it down. It decides nothing new.

## Context

The MCP servers are single files. `8effd2f` measured the plumbing they
duplicate at about 3300 lines across 15 servers, a figure dated to that commit.
The wiki already recorded what the duplication costs: one broken read loop had
become thirteen, because the servers are what each other get copied from.

The obvious fix is one sibling module that every server imports. `8effd2f`
measured that fix and refused it.

## Decision

**Code the fleet shares is generated into each file that uses it. No server
imports a sibling.** A canonical source holds the code, and
`Scripts/amalgamate.py` pastes named blocks from it between a `BEGIN GENERATED`
and an `END GENERATED` marker. The END marker carries a hash of the body, so a
hand edit inside a region is refused, not overwritten. The pasted code is
committed, so a fresh clone runs with no build step and no `sys.path` entry
`Scripts/MCP_SKELETON.md:753-762`.

The canonical sources are imported exactly once: by the test fleet, which loads
each one as a module and exercises its blocks. A helper pasted into fifteen
files is unit-tested in one place `Scripts/_mcp_json.py:29-33`.

## The grounds

`8effd2f` gave four, each measured on the tree at that commit. The first three
are restated word for word in every canonical source, for example
`Scripts/_mcp_json.py:20-27` and `Scripts/_mcp_concurrency.py:49-56`, and in the
server skeleton `Scripts/MCP_SKELETON.md:753-762`.

1. **Bytecode.** The live servers launch with no `PYTHON*` environment
   variable, so the first sibling import writes `Scripts/__pycache__` into a
   tree the suites assert stays free of bytecode. The canonical sources now name
   the property, "a tree every suite that snapshots bytecode asserts stays
   empty", and not the count of suites, which `8effd2f` gave as four.
2. **`sys.path`.** The test harness loads each server with
   `spec_from_file_location`, which never extends `sys.path`. With an import,
   standalone suite runs would break while `forge test all` passed by accident.
3. **Module attributes.** `tests/test_mcp_footprint.py` reaches into each server
   for its helpers as module attributes and harvests string literals from its
   source. An import would move both out of all fifteen servers at once.
4. **The copied file.** A user who copies one server without its sibling gets
   an `ImportError`. This ground is not in the canonical docstrings. It is in the
   commit body, in ADR 0014's alternative 6, and in the generator's own comment
   on declared hosts `Scripts/amalgamate.py:DECLARED_HOSTS`.

## Why not Cog

The hash-in-the-END-marker idea is Cog's `-c` checksum, and the generator says
so `Scripts/amalgamate.py:20-25`. Cog itself was not used, because a
third-party tool this repo does not install degrades to a no-op. `8effd2f` gave
the live example: ruff was not on `PATH`, so the lint hook's ruff step did
nothing. A gate that does not run is worse than one that does not exist. The
generator is stdlib-only and lives in the tree, so it runs wherever the servers
do.

## The hooks precedent

`8effd2f` states that the repo had already rejected the same shape for the
hooks: a shared library beside them, for the same `ImportError` reason as
ground 4. That earlier rejection has no page or commit this record could find,
so the precedent rests on the `8effd2f` body alone. It is recorded here as that
commit's claim, not as a separate decision.

## Re-checked for a host that is not a server

[[0023-the-websocket-client-is-a-sixth-domain]] did not re-decide this, but it
had to re-check it. The DuckDuckGo search script took generated blocks, and it
is not a server. None of the grounds above is about servers as such, and two of
them apply to the script with more force. Agents run it by path and without
`-B`, so an imported sibling would write `Scripts/__pycache__` on every search.
A copy of the one file taken alone would stop at an `ImportError`. Both held, so
the script became a hand-declared host `Scripts/amalgamate.py:DECLARED_HOSTS`.
That re-check is the evidence that the rule is about single-file scripts, and
not only about the MCP fleet.

## Alternatives rejected

**1. One sibling module that every server imports.** Refused on the four grounds
above.

**2. Cog, or any third-party generator.** Refused because it degrades to a no-op
wherever it is not installed.

**3. Import the canonical source from a host that is not a server.** ADR 0023's
alternative 6, refused there on grounds 1 and 4: it writes `__pycache__` into
`Scripts/` on every search, and it breaks the single-file copy.

**4. Leave the duplication hand-written.** Refused because the thirteen-server
read-loop failure had already happened. Hand copies agree on disk only until
one is changed. The generator and its drift gate are what keep the copies
equal.

## Consequences

- **Duplication is the mechanism, not a defect.** Every host carries the code in
  full. [[generated-regions]] states this cost and shows what it buys.
- **Each file has one writer inside a region.** A hand edit is refused, not
  overwritten. [[0009-the-first-reader-is-a-cold-model]] reached the same
  one-writer conclusion for the checkpoint table of contents.
- **A shared helper must be pasteable.** It may read only builtins, the stdlib
  and names the host imports, because a pasted block cannot import on its own
  behalf. That is where the block contract and the tab rule come from. Both are
  mechanics of this decision, described in [[generated-regions]].
- **Adding a host is a deliberate edit.** A non-server script joins by name in
  `Scripts/amalgamate.py:DECLARED_HOSTS`, never by matching a glob.
- **The gate carries the rule.** `tests/test_generated_region.py` proves the
  copies match the canonical text and exercises each block once. Its module
  docstring states the single-file reason too.
