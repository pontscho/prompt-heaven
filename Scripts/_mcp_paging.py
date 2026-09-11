#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the output-capping and paging helpers the servers share.

`_rows_note` renders `[showing rows 1-20 of 347; offset=20 for more]` onto the
LAST line of a text payload, and `_offset` reads back the number it printed.
That is a sentence for a reader and its reply, not a field in an envelope, so
neither belonged in a JSON source; what they share with the three CONSTANTS
below -- and with `_cap_text` and `_balance_fences`, still queued -- is the
question of how much of a result a caller gets and how it is told where the
rest is. This file is that domain's home, not a file invented for one
function.

**The constants arrived late, and the delay was mechanical, not editorial.**
`load_blocks_text` used to walk `tree.body` for `FunctionDef`,
`AsyncFunctionDef` and `ClassDef` only, so a module-level assignment could not
be a block however well it fitted the domain. It can now, for a single plain
`Name` target and nothing else -- `Scripts/amalgamate.py:assign_name` carries
the argument for that shape and against the four it turns down.

What is still queued is queued for a DIFFERENT reason, and the two must not be
confused: `_cap_text` and `_balance_fences` are extractable as written and
their copies are not byte-identical -- three `_balance_fences` hash three ways,
and every `_cap_text` differs (purity adds `repair_fences`, an anchor floor and
an in-line degenerate cut; jenkins and lldb carry a `bias` axis purity lacks).
Those are MERGES, where no existing copy is canonical. The constants were
lifts.

`_rows_note` and `_offset` only stay honest TOGETHER, which is the reason those
two share a file: a handler that prints `offset=<n> for more` and does not read
`offset`
has told the caller a lie, and one that reads it without ever printing the hint
has a knob nobody can discover. Changing the wording of one and not the reply
of the other is the drift this arrangement makes visible.

**No server imports this module.** Its named blocks are inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is. That is not stylistic: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree every suite that snapshots
bytecode asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes where `tests/test_mcp_footprint.py` reaches for them.

The test fleet *does* import it -- `tests/test_generated_region.py` group E loads
it as a module and exercises every block that HAS behaviour, which is the point:
a helper inlined into every server that asks for it is unit-tested once, here.
Group A separately proves the copies MATCH. The two claims are different, and a
drift gate on its own would only ever prove that every copy agrees on the same
bug.

Two of the three constants are outside that: `DEFAULT_MAX_ANSWER_CHARS` and
`PAGE_LINE_RESERVE` have no behaviour to exercise, and a case asserting `== 24000`
against a literal typed into the suite would be the same number written twice --
the defect this repo has shipped six times. Group A already pins their text in
every host, byte for byte, which is the whole of what there is to say about them.
`_FENCE_LINE_RE` is NOT in that position and has no case yet: its `re.M` is real
behaviour, and dropping the flag makes `findall` return at most one hit while the
pattern still compiles and still reads right. A group E case belongs there.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
The two FUNCTIONS need nothing but builtins. `_FENCE_LINE_RE` is the first
block here to spend that budget: it reads `re`, so this file imports `re`, and
every host that asks for it must import `re` too or `free_names` refuses the
region by name at END-marker time rather than emitting a server that dies on
its first import. All three hosts already did -- the requirement cost nothing
to introduce and is checked on every run regardless.

`_offset` keeps that property on purpose. Written as
`max(0, _int_param(args.get("offset", 0), 0))` -- which is exactly how its one
new host spelled it inline, four times -- the block would depend on another
block, and `_int_param` lives in `_mcp_json.py`. A marker names exactly ONE
source, so a cross-source block dependency is not expressible, and teaching the
generator to express it would buy nothing here: `int()` in a `try` is the same
four lines `_int_param` would have contributed.

**Provenance, and the one deliberate edit.** Blocks are lifted VERBATIM from
`Scripts/mcp-purity.py`, which is why a region's first generated diff is marker
lines only, with zero changed body lines -- the evidence that the lift was
faithful. `_rows_note` is the one exception, and deliberately: purity's copy was
the fleet's superset but its docstring named purity's own callers, which would
have been misleading prose in the four other servers that share it. The
caller-specific half now lives on purity's `_row_page`, where it describes the
code that actually decides it. That edit happened when the block was extracted;
the move to this file changed nothing, and the unchanged END hash in all five
hosts is the proof.

The three constants are lifts on the same terms, and the matrix was measured
before a marker was typed rather than after: `DEFAULT_MAX_ANSWER_CHARS` is one
byte-identical line in six servers, `PAGE_LINE_RESERVE` in five (jenkins caps
but does not page by row, so it never had one), `_FENCE_LINE_RE` in three. No
host's body changed. What did NOT travel is the sentence above each one: every
host justifies the same number in its own terms -- purity's reaches for a
measured 511617-character call, jenkins names the console log, webfetch says
"Fleet default (mcp-purity.py)" outright -- and that prose is host-specific, so
each constant was given a region of its OWN and the explanation stayed above
the BEGIN marker where it was written.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. The two functions annotate with `int`, `bool`, `str`
and `dict` -- builtins, present everywhere -- so they are safe as written; a
block wanting a `typing` name would be carrying that requirement to every host
that asks for it. The three constants carry no annotation at all, so the only
requirement any of them places on a host is `_FENCE_LINE_RE`'s plain read of
`re` -- which `free_names` sees on exactly the same terms, because it matches
binders by node type rather than assuming a function scope.
"""

import re


# --- the output ceiling (how much of a result a caller gets) ------------------
#
# 24000 chars is ~6k tokens at the usual ~4 chars/token: a reply ONE call may
# spend, not a reply that eats the session. The number is a FLEET convention,
# not a per-server tuning knob that happened to converge -- six servers wrote
# the same line, and one of them says so in its own comment. A server that
# genuinely needs a different ceiling keeps its own copy and says why, which
# the suite's hand-copy census then names rather than hides.
DEFAULT_MAX_ANSWER_CHARS = 24000

# Room kept free for the accounting line while a row pager fills its budget:
# the pager stops taking rows while this many characters are still unspent, so
# `_rows_note`'s sentence has somewhere to go. It is the companion of the print
# side above, which is why it lives here and not with whatever calls it.
PAGE_LINE_RESERVE = 80


# --- fence accounting (a cut that lands INSIDE a fenced block) ----------------
#
# `re.M` is the whole of it: the pattern must match a fence at the start of any
# LINE, not only at the start of the payload. Drop the flag and `findall`
# quietly returns at most one hit, the fence count comes out even, and a reply
# truncated mid-fence is handed to the reader with the block still open.
#
# The readers of this pattern -- each host's `_balance_fences` and `_cap_text`
# -- are deliberately NOT blocks: their copies have diverged, so they are
# merges rather than lifts. The constant went first because it had not.
_FENCE_LINE_RE = re.compile(r"^(`{3,})", re.M)


# --- the resume offset (the read side of the page line) -----------------------

def _offset(args: dict) -> int:
    """First item to display, 0-based -- the value the page line hands back.

    Display-level paging over what THIS call already produced, never an upstream
    cursor: the work is redone on every call, so a caller walking a large result
    pays for it each time. What the item IS depends on the payload -- a row, a
    record, an output line -- and the calling pager is where that is written
    down, along with which of `_rows_note`'s forms the handler can reach.

    The 0 floor is the reason this is not a bare ``int()``. A negative offset
    would index a list from its END, so `offset=-5` would quietly return the
    LAST five items to a caller who asked for a position before the first one --
    a wrong answer that looks like a right one, where a floor gives the caller
    the start of the payload they asked for.
    """
    try:
        return max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


# --- row accounting (the print side) ------------------------------------------
#
# THE ONE BLOCK THAT CANNOT GO TO A TAB-INDENTED HOST, and the reason is three
# lines below: the `else` aligns under an open paren.
#
#     total_disp = (str(total) if exact
#                   else f"...")
#
# That 18-space run is ALIGNMENT, not nesting. One tab per level would put the
# `else` in a column nobody chose, inside a region no human is supposed to read
# closely. `block_is_tab_safe` in the generator decides this mechanically -- an
# implicit line join, or a leading run that is not a whole 4-space level -- and
# refuses this block BY NAME for a tab host rather than emitting it with spaces
# into a file that indents with tabs.
#
# This is the LAST survivor of a rule that used to be per FILE ("tab
# re-indentation refused", which cost `mcp-forge` three hand copies). The old
# reasoning was drawn from exactly this function and was right about it; it was
# only wrong to generalise. EVERY OTHER canonical block -- the three constants
# below included -- contains no bracket continuation at all, so nothing in them
# can move. The suite asserts that set is exactly `_rows_note`, so this is a
# measured claim rather than a number typed here and checked by nobody.

def _rows_note(start: int, shown: int, total: int, exact: bool = True) -> str:
    """Row accounting for a row-shaped payload; goes on its LAST line.

    Display indices are 1-based inclusive, which makes the 1-based last row equal
    to the 0-based ``offset`` of the next one — so the hint is literally the value
    to pass back. ``offset=`` is therefore only ever emitted for a payload whose
    handler ACCEPTS ``offset``: a resume hint the handler would reject is worse
    than no hint at all, so block-shaped payloads get a character cap and no hint.

    ``exact=False`` is for a total that is a LOWER BOUND — a scan curtailed at a
    ceiling, where the files it never opened may hold more matches. It says so
    rather than presenting the count it happens to have reached as the total.

    The four canonical forms. This WORDING is fleet-wide, and keeping it from
    drifting is the whole reason the function is shared rather than reimplemented
    once per server:
        [3 rows]                                        whole set delivered
        [showing rows 1-20 of 347; offset=20 for more]  rows remain
        [showing rows 5-6 of 6; no rows left]           window ends at the end
        [no rows at offset 99 of 6]                     offset past the end
    A branch no current caller can reach is not dead code here. It is the wording
    the next caller must not invent differently, and each server reaches a
    different subset — see the calling pager for which ones and why.
    """
    last = start + shown
    total_disp = (str(total) if exact
                  else f"{total}+ (scan stopped at the ceiling; true total unknown)")
    if shown <= 0:
        # Spelled out rather than as a 1-based range, which would INVERT
        # ("rows 100-99 of 10") when the caller offsets past the end.
        return (f"[no rows at offset {start} of {total_disp}]" if start
                else f"[{total} rows]")
    if last < total or not exact:
        return (f"[showing rows {start + 1}-{last} of {total_disp}; "
                f"offset={last} for more]")
    if start > 0:
        return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
    return f"[{total} row{'s' if total != 1 else ''}]"
