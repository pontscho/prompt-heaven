#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for how many tool calls a server runs AT ONCE.

One constant, and a fifth domain to hold it. `CANONICAL_NAMES` is a hand-written
tuple exactly so that adding a source is a deliberate edit rather than a file
appearing in `Scripts/`, and this is that edit, so it owes an argument.
`MAX_INFLIGHT_REQUESTS` is the fleet's widest-shared constant -- nine live
servers wrote the same line -- and none of the four existing domains could hold
it without becoming the shelf each of them is written not to be. JSON-RPC
envelopes, logging configuration, LSP framing and output paging are four
questions; "how many handlers run concurrently" is a fifth, and filing it under
the nearest one would make a marker's source field decorative for every block in
that file.

**What stays OUT, which is nearly everything around the number.** The executors
are constructed per server and are not shareable at all: they read the host's own
`McpServer`, and `host_provides` offers a region only module-level imports. The
stdin reader thread sits OUTSIDE the pool in every server -- that split is what
stops a saturated pool from making the server deaf -- and it is wiring rather
than a value. Most importantly,
`docs/adr/0008-a-serialized-read-loop-looks-like-a-dead-server.md` records that
the concurrency decision was AUDITED PER SERVER rather than copied, and this file
must not be read as undoing that. What is shared here is the number those audits
agreed on. That they agreed is a measurement, not a policy, and nothing below
turns it into one.

**A server needing a different number keeps its own copy and says why.** That is
the rule `_mcp_paging.py` already applies to the output ceiling, and it is the
reason this is a plain constant block and not a helper that decides anything: the
suite's hand-copy census then NAMES the deviation instead of hiding it. What the
lift removes is nine copies agreeing by coincidence; what it must not remove is
the ability to disagree on purpose.

**The sentence above each host's marker did NOT travel, and that is the idiom
rather than an oversight.** Every host justifies the number in its own terms --
`mcp-forge` that a low number only buys fewer builds thrashing the same cores,
`mcp-tshark` that 8 also bounds how many tshark children can dissect at once,
`mcp-postgres` that requests on one connection serialize on that connection's
lock, so a fair share of the threads can be parked waiting on a single socket
while a long query holds it. That prose is host-specific, so the constant takes a
region of its OWN in each server and the explanation stays above the BEGIN marker
where it was written. This is the same split `_mcp_paging.py` records for its
three lifted constants.

**No server imports this module.** Its named block is inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is. That is not stylistic: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree every suite that snapshots bytecode
asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes where `tests/test_mcp_footprint.py` reaches for them.

There is no group E case for this block, by design rather than omission: a
constant has no behaviour to exercise, and group A pins its TEXT in all nine
hosts, which is the whole of what a value block can be wrong about. The block
reads nothing, so it places no import requirement on any host, and it has no
leading whitespace on any line, so the tab conversion is a no-op and the two
tab-indented hosts take it unchanged.
"""


# How many tool calls may be in flight at once. The stdin reader owns a thread of
# its own, OUTSIDE this pool, so saturating it delays queued CALLS and can never
# stop the server from READING -- that split is the whole point of the two
# executors in each host's run(). The per-server consequences of the number are
# written above each marker, where they were.
MAX_INFLIGHT_REQUESTS = 8
