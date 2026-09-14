#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the JSON/param helpers the MCP servers share.

**This is one canonical source of several, not the shelf.** The generator's
registry (`CANONICAL_SOURCES` in `Scripts/amalgamate.py`) lists them, and the
source named on a region's BEGIN line is load-bearing: that region's names are
resolved against THAT file's blocks and no other, so a name defined next door
does not resolve here. Each file is therefore a domain and has to earn the
label, and this one has been narrowed twice: LSP `Content-Length` framing was
not a JSON helper and now lives in `_mcp_lsp.py`, and `_rows_note` renders a
sentence for a human onto the last line of a text payload -- paging, not JSON --
so it now lives in `_mcp_paging.py`. What belongs here is the JSON-RPC
envelopes, wire-value coercion, and JSON error reporting: the blocks below, and
nothing whose reason for existing is a different concern.

**No server imports this module.** Its named blocks are inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is. That is not stylistic: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree every suite that snapshots
bytecode asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes where `tests/test_mcp_footprint.py` reaches for them.

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
a LIFT's first generated diff is marker lines only, with zero changed body
lines — the evidence that the lift was faithful. (The fleet's one deliberate
exception to that rule was `_rows_note`, and the account of it travelled with
the block to `_mcp_paging.py`, where the code it describes now lives.) An
ADOPTION claims nothing of the kind: a server whose copy DIFFERED shows every
difference as a changed body line, which is precisely why it has to be declared
instead of read as a lift.

Where a server's variant is deliberately different it simply never asks for the
block: `mcp-webfetch`'s allow-list `_bool_param` (an unrecognised string reads
False there and True here, and its flags are `allow_private` and `overwrite`),
`mcp-tshark`'s `(params, key, default)` signature, and `mcp-inspect`'s
`_int_param`, which takes a parameter NAME and raises where this one takes a
default and falls back.

`mcp-webfetch` hosts two of these blocks in tabs, `_json_error_window` and
`_int_param`, and INDENTATION IS NOT WHY it keeps the rest -- the emitter
re-indents a block whose indentation is purely structural, and `mcp-forge`
proved that by adopting three copies with a marker-line-only diff. What webfetch
declines, it declines on body differences that survive any amount of
re-indenting: the `_bool_param` polarity above, and a `_result` that annotates
`result: dict` where this one says `result: Any`. Its `_error` alone IS
byte-identical modulo the indent character, so that one is a live candidate
whenever somebody wants to split the pair; nobody has asked, and a region
holding half of a pair that reads as a pair is a decision, not a cleanup.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. A builtin annotation has no such price -- `text: str`
and `default: int` appear above -- and `Any` is what a wire value would want, so
`value` stays bare in both coercions above. The generator now computes that
dependency rather than trusting it, `free_names` against `host_provides`, so the
envelope pair's `msg_id: Any` is refused BY NAME in a host that lacks the import
instead of killing it. What the annotation still never buys is a check, because
nothing reads `__annotations__`: it was therefore the whole of a cheap
difference, and the four copies that used to illustrate the tension were adopted
on that reading, dropping theirs.
"""

from typing import Any


# --- JSON-RPC envelopes -------------------------------------------------------
#
# These two are METHODS at their destination -- `@staticmethod` members of each
# server's McpServer -- and the region that hosts them sits in a class body, which
# is why the emitter places a block at its marker's own column. Here they are
# top-level, so `load_blocks` can address them by name, and that makes this file a
# text SOURCE for these two rather than a usable module: a module-level
# `staticmethod` is a descriptor object, not a callable, so the suite reaches
# through `__func__`.
#
# Do NOT "tidy" the decorator away. Without it the emitted member becomes an
# instance method whose first parameter eats `self`, so `_result(msg_id, result)`
# silently becomes `_result(self=server, msg_id=..., result=...)` -- every reply
# malformed, on every server, from the first call.
#
# Measured byte-identical across 14 servers (one 123-byte body), `mcp-forge`
# included: it indents with TABS, and the emitter now re-indents a block whose
# indentation is purely structural, which this pair's is. `mcp-webfetch` is the
# one holdout, and not over tabs -- see the exclusions paragraph in the module
# docstring for the two real reasons.

@staticmethod
def _result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


@staticmethod
def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


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
    """Coerce a wire value to int, falling back instead of raising.

    `OverflowError` is in the list because the wire can carry a float infinity:
    `json.loads` reads both `1e999` and the bare `Infinity` token as one, and
    `int()` on an infinity raises an error that is neither a TypeError nor a
    ValueError. Without it the one value a caller is most likely to send as
    "no limit" was the only bad value that did not fall back -- it escaped the
    handler as an opaque internal error. NaN needs no entry: `int(nan)` raises
    ValueError, which this already catches.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


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
