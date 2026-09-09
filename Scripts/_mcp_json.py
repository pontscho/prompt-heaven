#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the JSON/param helpers the MCP servers share.

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
inlined into fifteen files is unit-tested once, here. Group A separately proves
the copies MATCH. The two claims are different, and a drift gate on its own
would only ever prove that fifteen files agree on the same bug.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
`_canonical_function` is the near miss that shows why the rule needs writing
down: its two lines are identical in three servers, but its body reads a
per-server `FUNCTION_ALIASES` table, so sharing it means sharing a promise about
the host's globals. Until that promise has a form and a check, it stays out.

Every block below was lifted VERBATIM from `Scripts/mcp-purity.py`, which is why
each region's first generated diff is marker lines only, with zero changed body
lines — the evidence that the lift was faithful. `_rows_note` is the one
exception, and deliberately: purity's copy was the fleet's superset but its
docstring named purity's own callers, which would have been misleading prose in
the four other servers that share it. The caller-specific half now lives on
purity's `_row_page`, where it describes the code that actually decides it.

Where a server's variant is deliberately different it simply never asks for the
block: `mcp-webfetch`'s allow-list `_bool_param` (an unrecognised string reads
False there and True here, and its flags are `allow_private` and `overwrite`),
`mcp-tshark`'s `(params, key, default)` signature, and `mcp-inspect`'s
`_int_param`, which takes a parameter NAME and raises where this one takes a
default and falls back.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. That is a host dependency the contract above does not
yet cover and nothing yet checks, which is why the blocks here stay unannotated
even though four servers annotate their copies.
"""

import json


# --- scalar coercion ----------------------------------------------------------

def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive bool("false") would wrongly yield True.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


def _int_param(value, default: int) -> int:
    """Coerce a wire value to int, falling back instead of raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- row accounting -----------------------------------------------------------

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


# --- LSP framing (Content-Length over stdio) ----------------------------------

def encode_lsp_message(body: dict) -> bytes:
    """Encode a dict as an LSP message with Content-Length framing."""
    text = json.dumps(body)
    encoded = text.encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n"
    return header.encode("ascii") + encoded


# --- JSON error reporting -----------------------------------------------------

def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    """Return a repr'd slice of *text* centred on *pos*.

    A JSONDecodeError reports a character offset ("char 1530"), which the
    caller that produced the string cannot count to; the one broken escape is
    only actionable if it is shown. ``repr`` is what makes it visible -- the
    typical defect is a quote escaped one level too shallow, and a raw slice
    renders that identically to a correct one.
    """
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    lead = "..." if start > 0 else ""
    tail = "..." if end < len(text) else ""
    return f"{lead}{text[start:end]!r}{tail}"
