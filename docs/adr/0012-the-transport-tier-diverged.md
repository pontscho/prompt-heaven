---
name: 0012-the-transport-tier-diverged
type: adr
status: active
title: The transport tier was not copied, it diverged
description: Decision NOT to extract the MCP transport tier into shared plumbing, with the census that refuted the 1600-duplicated-lines premise, the three independent reasons the extraction fails, and the half of the transport claim that survives.
sources:
  - Scripts/amalgamate.py
  - Scripts/mcp-jenkins.py
  - Scripts/mcp-purity.py
  - tests/test_read_loop.py
verified:
  commit: e6d1496
  date: 2026-09-11
links:
  - scripts
  - tests
  - generated-regions
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0011-a-truncated-payload-carries-the-first-cookie
---

# ADR 0012: The transport tier was not copied, it diverged

**Status:** accepted (a rejection, recorded). Append-only — the WHY is frozen
here. There is no implementation to point at, which is exactly why the page
exists.

## Context — the premise that died

The session handoff notes carried one item forward for four sessions as the
highest-value work remaining: *"the transport tier is ~1600 duplicated lines
across fifteen servers, the highest remaining value and the highest risk."* It
was described as blocked only by `mcp-webfetch.py` still sitting on the
pre-[[0008-a-serialized-read-loop-looks-like-a-dead-server]] dispatch shape;
once `5acb033` finished that conversion and `2518d06` gated the shape across all
fifteen, the item read as unblocked and ready.

**The premise was never measured.** This page records its refutation, because a
rejection that leaves no artefact is indistinguishable from work nobody got to —
and the next session, reading the same handoff, resurrects it.

## The census

A throwaway AST measurement over all fifteen `Scripts/mcp-*.py`, taken over the
tree at `5acb033` — the commit `ae31685` was later written against. The script
and its report were scratch and are not in the repository, so the numbers are
recorded here or nowhere.

The fleet at that commit: **34,015 source lines, 1,084 units** (module functions
plus class methods, with 46 nested closures seen and deliberately not counted),
**733 distinct qualified names**, **70 units inside generated regions**, and
**0 units straddling a region boundary** — the last one is the check that the
region exclusion actually fired, so the generated copies of
[[generated-regions]] are not counted as duplication a second time.

Duplication is not one number, it is a ladder, and the premise is true only of
the loosest rung. Keying on a shared qualified name, `lines_per_copy x
(servers - 1)` over every group present in two or more servers, gives **6,186**
lines. Dropping the groups whose copies agree on nothing but their name (worst
pairwise similarity below 0.80) leaves **1,747** — the only rung on which "~1600"
is roughly right, and it is the wrong rung, because it still counts every line of
a copy that merely resembles its sibling. Counting instead the lines every copy
of a group *shares* gives a ceiling of **3,510**. And counting what an extraction
would actually collect — the lines duplicated among the largest set of servers
whose copies are **identical to one another** — gives **883**.

**Of those 883, the transport tier proper (`McpServer.*`) contributes 135.**
Byte-identical hand copies across the whole fleet, the only unarguable
duplication, come to 222 lines. The generator has already de-duplicated a further
643, excluded from every figure above.

The two headline items are worse than small, they are **zero**. `McpServer.run`
exists in all fifteen servers as **fifteen distinct bodies, no two alike**, worst
pairwise similarity 0.55, largest agreeing set 1, collectable lines **zero** —
against a naive figure of 896, which is more than half the premise on its own.
`module:main` is likewise fifteen distinct, similarity 0.16, collectable lines
**zero**.

**The empty normalization bucket is itself a finding.** Not one group in the
fleet is identical only *after* whitespace and comments are taken out: copies
here either agree byte for byte or they differ in real code. Cosmetic drift is
not a phenomenon in this codebase, which is the opposite of what the brief
anticipated — and it means a count keyed on a shared NAME is not a count of
duplication at all.

## Three independent reasons, all measured

**One: there is nothing to collect.** The prize is 135 lines, not 1,600, and the
two largest candidates are exactly zero. An extraction whose payoff is 135 lines
across the dispatch layer of fifteen live servers is not a favourable trade at
any risk level.

**Two: the generator's contract refuses it**, on three separate rules, each of
which is enough on its own. `host_provides` returns module-level **import
aliases only** `Scripts/amalgamate.py:host_provides`, and `log` is a module-level
*assignment* in all fifteen servers, so any transport block that logs is rejected
by name. The block walk is over `tree.body`, so a method cannot be a block at
all `Scripts/amalgamate.py:load_blocks_text` — the `_result` / `_error` escape
works only because those are module-level `@staticmethod`s in the canonical
source placed at the marker's column, and that trick does not transfer to a
method taking `self`. And `block_is_tab_safe` refuses any block that joins a line
while a bracket is open `Scripts/amalgamate.py:block_is_tab_safe`, which a
`run()` body is full of, while `mcp-forge.py` and `mcp-webfetch.py` are the
fleet's two tab-indented hosts.

**Three: what remained would have been an ADOPTION, not a lift.** Lifting
`_write` meant choosing one log line on behalf of fifteen servers — and the
choice on offer, the majority form, would have silently reverted an annotated
CWE-532 mitigation that only one server carried. That question became
[[0011-a-truncated-payload-carries-the-first-cookie]], and **finding it is the
only thing this investigation produced that was worth the time.** A duplication
census is a good way to discover which of your copies disagree; it is a bad way
to decide what to do about it.

## What survives — the half of the claim that is true

This page must not be read as "the transport is fine".

`tests/test_read_loop.py` says in its own docstring that the concurrency decision
diverges per server while the transport does not — the reason the read loop is
deliberately *not* a generated region. **That is half true, and the half that is
false is load-bearing for anyone who tries this again.**

The **wire** half really is the same code in a pool server and a coroutine
server. Verified against `mcp-jenkins.py` (thread pool) and `mcp-purity.py`
(coroutine, no pool): the readline is the same line in both
(`await loop.run_in_executor(reader, sys.stdin.readline)`), the framing and
blank-line handling are the same, and both malformed-input replies are the same
code — `-32700` for an unparseable line and `-32600` for valid JSON that is not
an object `Scripts/mcp-jenkins.py:run` `Scripts/mcp-purity.py:run`.

The **colour** half is not, and these are transport responsibilities rather than
concurrency code. `_serve` differs in arity: jenkins takes the loop and the
worker pool as parameters, purity takes only the message
`Scripts/mcp-jenkins.py:_serve` `Scripts/mcp-purity.py:_serve`. `_handle_message`
differs in async/def colour — sync in jenkins, a coroutine in purity — and so
does `_handle_tool_call` `Scripts/mcp-jenkins.py:_handle_tool_call`
`Scripts/mcp-purity.py:_handle_tool_call`, which changes the call sites too:
`return self._handle_tool_call(...)` against
`return await self._handle_tool_call(...)`. Nine servers carry a
`_handle_tool_call` at all, in nine distinct bodies; fourteen carry a `_serve`,
in ten.

So the honest statement is narrower than the docstring's: *the wire is uniform;
the dispatch signature is not*, and any future extraction has to name which of
the two it means before it names a line count.

## Consequences

- **The transport tier is closed as a work item.** If it is reopened, it is
  reopened against these numbers, not against the handoff note's.
- **The census's `_write` row is the one figure that has moved.** It was measured
  before `ae31685` re-converged that method across the fleet, so its agreeing set
  is larger today. This does not reopen anything: `_write` is precisely the method
  [[0011-a-truncated-payload-carries-the-first-cookie]] shows cannot be homed, for
  the `log` reason above, and whose one-line-for-fifteen-servers choice is the
  adoption this page declines.
- **A duplication figure needs its rung named.** 6,186 / 1,747 / 3,510 / 883 are
  all defensible answers to differently-worded questions about the same fifteen
  files. A future claim of the form "N duplicated lines" should say which one it
  means, or it will be re-measured from scratch.
