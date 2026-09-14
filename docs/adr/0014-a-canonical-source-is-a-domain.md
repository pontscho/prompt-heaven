---
name: 0014-a-canonical-source-is-a-domain
type: adr
status: active
title: A canonical source is a domain, not a shelf
description: Decision that each canonical generation source holds one domain and that a region's names resolve only against the source its own marker names, with the naming complaint that measurement redirected to the container, the rule that decides when a sixth source is warranted, the six alternatives rejected, and the two hand-written registries that have to agree.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_concurrency.py
  - tests/test_generated_region.py
verified:
  commit: 8d56b6d
  date: 2026-09-14
links:
  - scripts
  - tests
  - generated-regions
  - 0012-the-transport-tier-diverged
  - 0013-the-ceiling-is-a-payload-class
---

# ADR 0014: A canonical source is a domain, not a shelf

**Status:** accepted. The decision is the RULE for what may be a source. The five
sources that exist are its worked examples, and this page is careful about which
of them were decided under the rule and which are ratified retroactively.

## Context — a naming complaint that was about the container

The generation mechanism shipped with exactly one canonical source. `8effd2f`
created `Scripts/_mcp_json.py` as a deliberately narrow pilot — one region, one
function, in one server — and the file took its name from the first thing
extracted into it, the `JSONDecodeError` window. Everything shared afterwards was
dropped in behind that name.

Five and a half hours later, on the same day, `d65132a` split it. The complaint
that surfaced the problem was **about the block names**: they carried no `_json_`
prefix, and a reader wanted to know why the contents of a file called
`_mcp_json.py` did not announce themselves as JSON.

The measurement redirected the complaint at the container.

## What one source had become, measured

Of the seven blocks in the file, **four were not JSON at all.**
`encode_lsp_message` is `Content-Length` framing — bytes on a wire, no JSON
anywhere in it. `_rows_note` renders a human-readable pager sentence onto the
last line of a text payload. A `_json_` prefix would have been **false on more
blocks than it was true.**

The name described the seed, not the contents. The proposed fix would have
propagated the seed's name onto four blocks it did not describe, which is how a
container defect gets mistaken for a naming defect and then made worse.

**The fix cost almost nothing, and that is the finding rather than an aside.**
The marker format already spelled its source — `BEGIN GENERATED: <source> ::
<names>` — and `parse_names` already parsed that field. A single line rejected
everything that was not the one hardcoded filename. Finishing that stub is the
whole of what made the source field load-bearing. The third source afterwards
cost exactly one line; so did the fourth, and so did the fifth.

## Decision

**One source per domain, and the source named on a marker selects the block map.**
A region's names resolve against that file and no other.

Two consequences follow, and both are asserted rather than assumed:

- An unknown source is refused **by name**, and the refusal lists what is on
  offer — the reader of that message is somebody who mistyped a filename.
- A name defined in **another** canonical file does not resolve. This is the
  dangerous direction and the reason the rule needs a gate: a block served out of
  the wrong domain compiles, runs, and looks correct in every server carrying it,
  so nothing downstream would ever report it.

**A source must EARN the label.** `_mcp_json.py` has been narrowed twice to keep
it — framing left for `_mcp_lsp.py`, row accounting for `_mcp_paging.py` — and
both departures are asserted **as departures** in the suite
(`json-source-drops-framing`, `json-source-drops-row-accounting`). A block that
came back would otherwise be tested in its new home while still sitting in its
old one, and the assertion sits where a reader goes looking for the function.

## The rule for a sixth source

> A new canonical source is warranted when a shareable block's domain cannot be
> held by an existing source **without making that source a shelf.**

Not when the block is large. Not when it is shared widely. Not when it is
convenient.

`Scripts/_mcp_concurrency.py` is the worked example and the reason this page
exists now rather than a session earlier. `MAX_INFLIGHT_REQUESTS = 8` was the
fleet's **widest-shared constant** — nine live servers had each written the same
line — and it had gone unlifted for a reason that had nothing to do with the
constant: there was no domain to put it in. JSON-RPC envelopes, logging
configuration, LSP framing and output paging are four questions, and *how many
handlers run at once* is a fifth. Filing it under whichever of the four it sat
nearest would have made the source field decorative **for every block in that
file** — surrendering the one property the multi-source design exists to protect,
to avoid writing one line.

The rule cuts the other way too, and that is what stops it becoming a licence.
`_mcp_lsp.py` holds one block. `_mcp_logging.py` holds one block.
`_mcp_concurrency.py` holds one constant. **Block count is not the test**; domain
is. A source that would hold a second domain is not cheaper than a second source.

The price of the rule is one line in each of two hand-written registries. A block
that does not justify one line is not a block.

## Alternatives rejected

**1. Prefix the block names instead (`_json_error_window`, `_json_result`, …).**
The original proposal. Refused on measurement: false on four of seven blocks. It
treats a container defect as a naming defect and spreads the wrong name further.

**2. Keep one file and rename it neutrally — `_mcp_shared.py`.** This makes the
source field decorative and gives up the cross-domain refusal entirely. It also
does not scale in the direction that matters: the reason to know which file a
name comes from is exactly the reason to have more than one file.

**3. Flatten every source into one namespace at load time.** Refused in
`load_all_blocks`'s own docstring, and the reason is the failure mode rather than
the tidiness: the marker's source field becomes decorative, and the first
collision between two domains resolves to whichever file loaded last — a silent
choice nobody wrote down.

**4. Glob `_mcp_*.py` into the registry rather than writing it by hand.** Refused
because a file would become a generation source **by merely existing**, and a
marker naming it would read exactly as legitimate as the ones that belong.
`Scripts/_mcp_smoke_test.py` is the standing counter-example: an `_mcp_*.py` file
that is deliberately not a source. Adding a source must be a deliberate edit.

**5. Leave `MAX_INFLIGHT_REQUESTS` duplicated rather than open a fifth domain.**
Considered seriously, because a fifth source for one integer is the shape
over-engineering takes. Refused: nine hand copies is the widest agreement on a
single line anywhere in the fleet, and agreement on disk is precisely what rots.
The generation mechanism exists for that case or it exists for none.

**6. Import the sources instead of generating them.** Not re-decided here. It was
settled in `8effd2f` on four measured grounds — `__pycache__` in a tree the
suites assert is bytecode-free, a `sys.path` entry the harness never adds,
helpers relocated out of the module attributes `test_mcp_footprint.py` reaches
for, and the `ImportError` a user copying one file without its sibling gets.
That decision has never had a page of its own; see below.

## The two registries that have to agree

`CANONICAL_NAMES` lives in `Scripts/amalgamate.py` and is **mirrored, not
imported,** in `tests/test_generated_region.py` — for the same reason the marker
prefixes are spelled out there: they are an on-disk format contract, and a test
that imported the tuple would sail through any change to it.

The mirror earns its keep on exactly the edit this page describes. A source added
to the generator and not to the mirror fails `sources-registered` **by name**
instead of being adopted silently. `every-source-in-use` closes the other
direction: a registered source no live region names is a shelf the drift gate
cannot reach, so a block rotting inside it would read green forever.

Measured at `8d56b6d`: five sources, 16 blocks, 99 live regions emitting 125
block instances across the 15 servers.

## What this page does not settle

- **The `_json_` prefix question for the blocks that genuinely are JSON.** Open
  since `d65132a`, which noted the argument against it for `_result` and
  `_error`: they are methods, and the receiver already supplies the context a
  prefix would repeat. Nobody has ruled on the rest.
- **The single-file rule itself** — generate rather than import. Decided in
  `8effd2f` with the measurements quoted above and never written up as a page.
  This ADR depends on it and does not restate it.
- **The per-block tab rule**, narrowed from a per-file refusal in the same commit
  that split the sources, and likewise unrecorded.
- **Whether a one-block domain should be merged if it never grows.** Three of the
  five sources hold a single block today. The rule above says domain rather than
  count, so none of the three is wrong — but nothing has revisited them, and
  nothing schedules a revisit.
