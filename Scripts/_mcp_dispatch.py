#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for how a tool's dispatcher answers a NAME it does not know.

Every server routes one `<name>_call` tool to a table of functions, and the six
that refuse an unknown parameter route a function's params against a table of
accepted names. A caller -- usually a model -- that misspells either gets a
refusal listing what exists, and nothing pointing at the entry it almost typed.
`_did_you_mean` appends that pointer: " Did you mean 'X'?".

**Why a fourteenth source and not a block in an existing one.** ADR 0014's rule
is the test: a new source is warranted when no existing one can hold the block
without becoming a shelf. `_mcp_json.py` is the nearest, and it is the wrong
question -- it owns how a frame is enveloped and parsed and how a wire VALUE is
coerced (`_bool_param`, `_int_param`); a suggestion is about the caller's NAMES
and reads no JSON at all. `_mcp_paging.py` decides how much of a reply a caller
gets, `_mcp_logging.py` how a line is logged, and the protocol-client sources
speak a wire. "Which of my names did the caller mean" is none of those questions,
so it is a domain of its own: how a dispatcher resolves the names a caller sends
against the names it serves. The alias resolvers belong to the same question and
stay hand-written in each host anyway -- their free names are each host's own
tables (ADR 0015, Option 7) -- so this file holds what CAN be shared, the pure
half that takes the tables as arguments.

**The phrasing and the threshold are mcp-forge's.** forge already suggested a
build/test target with `difflib.get_close_matches(word, candidates, n=1,
cutoff=0.6)`; this block uses the same call, so a suggestion means the same
closeness wherever it appears. forge's own target suggestion keeps its
parenthetical wording -- it sits mid-sentence before a line break, where this
block's appended sentence would not read.

**What it never does is resolve.** A suggestion is a sentence in a refusal, not
an alias: `context_chars` is pointed at `context_lines` and still refused,
because a character count and a line count are different requests and quietly
answering one with the other is the defect ADR 0015 exists to prevent.

**No server imports this module.** Its named block is inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is: an imported sibling would write
`Scripts/__pycache__/*.pyc` into a tree every suite that snapshots bytecode
asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes where `tests/test_mcp_footprint.py` reaches for them.

The block reads `difflib`, which every host imports, and nothing else of the
host's. It is written one call per physical line with every indent a multiple
of four, so the two tab-indented hosts (mcp-forge, mcp-webfetch) take it
re-indented. Group E of `tests/test_generated_region.py` pins its behaviour;
`Scripts/_mcp_smoke_test.py:near_miss_checks` drives every host's copy live.
"""

import difflib


def _did_you_mean(words, candidates, aliases=None):
    """Return " Did you mean 'X'?" for the closest real name, or "".

    `words` is the one name a caller sent (a function) or a list of them (the
    unknown parameter keys of one call). `candidates` are the names actually on
    offer -- the callable functions, or the parameters this function accepts.
    `aliases` maps an alias to its canonical name: an alias of an offered name
    may be MATCHED, and the canonical name is what is suggested, so a near miss
    of an alias still points at the spelling the documentation uses. An alias
    whose canonical is not on offer is not a candidate.

    Only the server's own names are ever rendered for a single word; with
    several words each suggestion says which key it answers. A word that is
    not a string, is empty, is already a candidate, or resembles nothing gets
    no suggestion -- silence is half the contract, since a guess appended to
    every refusal would teach a caller to ignore it. The pool is sorted, so the
    answer depends on the sets, never on the order a table was written in.
    """
    if isinstance(words, str):
        words = [words]
    if not isinstance(words, (list, tuple)):
        return ""
    names = sorted(set(name for name in candidates if isinstance(name, str)))
    table = aliases if isinstance(aliases, dict) else {}
    extra = set(key for key, value in table.items() if isinstance(key, str) and value in names)
    pool = sorted(set(names) | extra)
    pairs = []
    for word in words:
        if not isinstance(word, str) or not word or word in pool:
            continue
        found = difflib.get_close_matches(word, pool, n=1, cutoff=0.6)
        if not found:
            continue
        name = found[0] if found[0] in names else table[found[0]]
        if (word, name) not in pairs:
            pairs.append((word, name))
    if not pairs:
        return ""
    if len(words) == 1:
        return " Did you mean '%s'?" % pairs[0][1]
    return " Did you mean " + ", ".join("'%s' for '%s'" % (name, word) for word, name in pairs) + "?"
