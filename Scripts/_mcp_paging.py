#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the output-capping and paging helpers the servers share.

`_rows_note` renders `[showing rows 1-20 of 347; offset=20 for more]` onto the
LAST line of a text payload, and `_offset` reads back the number it printed.
That is a sentence for a reader and its reply, not a field in an envelope, so
neither belonged in a JSON source; what they share with the blocks queued to
join them -- `_cap_text`, `_FENCE_LINE_RE`, `_balance_fences`,
`DEFAULT_MAX_ANSWER_CHARS`, `PAGE_LINE_RESERVE` -- is the question of how much
of a result a caller gets and how it is told where the rest is. This file is
that domain's home, not a file invented for one function.

The two halves only stay honest TOGETHER, which is the reason they share a
file: a handler that prints `offset=<n> for more` and does not read `offset`
has told the caller a lie, and one that reads it without ever printing the hint
has a knob nobody can discover. Changing the wording of one and not the reply
of the other is the drift this arrangement makes visible.

**No server imports this module.** Its named blocks are inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is. That is not stylistic: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree four suites assert is empty, would
need a `sys.path` entry the test harness's `spec_from_file_location` never adds,
and would move the helpers out of the module attributes where
`tests/test_mcp_footprint.py` reaches for them.

The test fleet *does* import it -- `tests/test_generated_region.py` group E loads
it as a module and exercises every block directly, which is the point: a helper
inlined into every server that asks for it is unit-tested once, here. Group A
separately proves the copies MATCH. The two claims are different, and a drift
gate on its own would only ever prove that every copy agrees on the same bug.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
Both blocks below need nothing but builtins, which is why there is no import
here; a block added later that needs one must import it here too, or the
generator's free-name check refuses the region in every host that does not
already have it.

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

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. Both blocks annotate with `int`, `bool`, `str` and
`dict` -- builtins, present everywhere -- so they are safe as written; a block
wanting a `typing` name would be carrying that requirement to every host that
asks for it.
"""


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
# only wrong to generalise. The other seven canonical blocks contain no bracket
# continuation at all, so nothing in them can move.

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
