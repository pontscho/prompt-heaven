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
envelopes, wire-value coercion, strict JSON parsing and emitting for a peer's
frames, and JSON error reporting: the blocks below, and nothing whose reason
for existing is a different concern.

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

Every block below except `_ensure_dict` and the six strict-JSON blocks was
lifted VERBATIM from
`Scripts/mcp-purity.py`, which is why a LIFT's first generated diff is marker
lines only, with zero changed body
lines — the evidence that the lift was faithful. The strict-JSON blocks
(R-0067, R-0068) are NEW code with no copy anywhere to lift from; their
comment block below says what they refuse and why each refusal is a
JSONDecodeError. (The fleet's one deliberate
exception to that rule was `_rows_note`, and the account of it travelled with
the block to `_mcp_paging.py`, where the code it describes now lives.) An
ADOPTION claims nothing of the kind: a server whose copy DIFFERED shows every
difference as a changed body line, which is precisely why it has to be declared
instead of read as a lift.

**`_ensure_dict` is the declared exception, and it is a RESHAPE: its first
generated diff shows changed body lines in all seven hosts.** `mcp-purity` never
had the function at all, so there was no copy to lift from. What forced the
reshape is `block_is_tab_safe`: every hand copy builds its message inside a
`raise ValueError(` that wraps across physical lines, which is an implicit line
join, and `mcp-forge` and `mcp-webfetch` are TAB-indented hosts, so the verbatim
text would have been refused BY NAME for exactly those two and quietly accepted
by the other five. Each message below is therefore assembled one physical line
at a time. The WORDING is untouched down to the trailing spaces, which is what
lets the suite's sentence census go on comparing it. Five of the seven copies
were already identical modulo the indent character; `mcp-inspect` had dropped
the docstring and gets it back, and `mcp-forge` carried a six-line comment that
is the next paragraph.

**`value` is still the STRING in the `except` branch, and the window depends on
that.** `value = _strict_loads(value)` binds nothing when it raises, so
the name still holds the text the caller sent and `_json_error_window` can be
handed it. `mcp-forge` alone wrote this down, on the stated reasoning that it
was the file the others were copied from. That reasoning now points here, so the
argument moves here -- into a docstring copied into nothing, rather than into a
block copied into seven servers.

**It must be CO-LISTED with `_json_error_window` on one marker, and that is
mechanics rather than tidiness.** `host_provides` offers a region only the
host's module-level IMPORTS, never the names another region defines, so a region
holding `_ensure_dict` alone is refused BY NAME in all seven hosts. The pair is
spelled `_mcp_json.py :: _json_error_window, _ensure_dict`, dependency first,
after `DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars` -- and since R-0067/R-0068
`_ensure_dict` parses through `_strict_loads`, so the six strict-JSON blocks
stand in front of the window on that same marker. The consequence lands in
the SUITE rather than here: `mcp-forge`, `mcp-git` and `mcp-inspect` had
`_ensure_dict` as their ONLY caller of the window, so the moment it moved inside
the region those three had no window call site visible from outside one, and the
liveness census had to start asking whether the PAIR is reached instead of
whether one name is.

Where a server's variant is deliberately different it simply never asks for the
block: `mcp-webfetch`'s allow-list `_bool_param` (an unrecognised string reads
False there and True here, and its flags are `allow_private` and `overwrite`),
`mcp-tshark`'s `(params, key, default)` signature, and `mcp-inspect`'s
`_int_param`, which takes a parameter NAME and raises where this one takes a
default and falls back.

`mcp-webfetch` hosts three of these blocks in tabs -- `_json_error_window`,
`_ensure_dict` and `_int_param` -- and INDENTATION IS NOT WHY it keeps the rest -- the emitter
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

import json
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


# --- strict JSON on the wire (R-0067, R-0068) ---------------------------------
#
# The stdlib's json is lax in two ways a peer can reach. It reads NaN, Infinity
# and -Infinity (and 1e999 as an infinity) and writes them back again, which no
# strict JSON peer can parse (R-0067). And the int-digit limit that answers
# CVE-2020-10735 exists only from Python 3.9.14: on the macOS system Python
# 3.9.6 a multi-megabyte run of digits is parsed at quadratic cost (R-0068).
# Every host parses each frame a peer sends with `_strict_loads` and writes each
# frame with `_strict_dumps`; tests/test_generated_region.py group H gates that
# no host does otherwise without a declared reason.
#
# The six travel as ONE run on one marker, dependency first: `_strict_loads`
# reads the three hooks and the hook reads the bound, and a region may reach
# only names its host imports or the region itself defines. The bound and the
# refusal style mirror `_rt_loads` in llm-router.py and `_oauth_json_object` in
# _mcp_oauth.py: 4300 is CPython 3.11's own int_max_str_digits default, counted
# here in characters with the sign included, and the literal is refused before
# int() ever sees it.
#
# Every refusal leaves `_strict_loads` as a json.JSONDecodeError, not a bare
# ValueError, because that is the one exception every host's frame loop and
# `_ensure_dict` already catch: a refused number then gets exactly the -32700
# answer an unparseable line gets, and nothing new can escape as a crash. Each
# hook passes the literal it refused as a second argument so the position can
# be FOUND rather than reported as 0 -- `exc.pos` is what a host hands
# `_json_error_window`. `find` returns the literal's first occurrence, so the
# same characters inside an earlier string would move the window there; the
# refusal itself does not depend on it.

JSON_INT_LITERAL_LIMIT = 4300


def _json_no_constant(name: str) -> float:
    """json parse_constant: NaN, Infinity and -Infinity are not JSON (R-0067)."""
    raise ValueError(f"non-finite number {name} is not JSON", name)


def _json_finite_float(text: str) -> float:
    """json parse_float: a literal that overflows to an infinity (1e999) is refused like NaN."""
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(f"number {text[:32]} overflows to an infinity", text)
    return value


def _json_bounded_int(text: str) -> int:
    """json parse_int: a literal over JSON_INT_LITERAL_LIMIT characters is refused before int() sees it (R-0068)."""
    if len(text) > JSON_INT_LITERAL_LIMIT:
        raise ValueError(f"integer literal of {len(text)} characters exceeds {JSON_INT_LITERAL_LIMIT}", text)
    return int(text)


def _strict_loads(text):
    """json.loads for a peer's frame: every refusal is a json.JSONDecodeError.

    *text* is str or bytes, as json.loads takes it. A NaN or infinity token, a
    float that overflows to one and an integer literal over
    JSON_INT_LITERAL_LIMIT characters are refused at the literal's position;
    any other ValueError (an undecodable byte string) is re-raised as a
    JSONDecodeError at position 0. RecursionError is not converted: a host
    that answers deep nesting catches it itself.
    """
    try:
        return json.loads(text, parse_constant=_json_no_constant, parse_float=_json_finite_float, parse_int=_json_bounded_int)
    except json.JSONDecodeError:
        raise
    except ValueError as exc:
        doc = text if isinstance(text, str) else bytes(text).decode("utf-8", "replace")
        if len(exc.args) == 2 and isinstance(exc.args[1], str):
            raise json.JSONDecodeError(exc.args[0], doc, max(0, doc.find(exc.args[1]))) from None
        raise json.JSONDecodeError(str(exc), doc, 0) from None


def _strict_dumps(obj) -> str:
    """json.dumps for a frame to a peer: NaN and the infinities raise ValueError (R-0067).

    allow_nan=False is the only difference: separators and ensure_ascii stay
    json.dumps' defaults, so every frame that serialised before is
    byte-identical. A caller that can be handed a non-finite float keeps the
    ValueError arm it already has for a value json cannot serialise.
    """
    return json.dumps(obj, allow_nan=False)


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


# The window's only in-file caller, and the reason the two travel as a pair: a
# marker naming `_ensure_dict` on its own is refused in every host, because the
# name it reads is not an import anywhere. See the module docstring for the
# reshape, and for why `value` still holds the caller's string below. Since
# R-0067/R-0068 it parses through `_strict_loads` too, so its marker carries the
# six strict blocks in front of the window.

def _ensure_dict(value: Any, name: str = "params") -> dict:
    """Coerce *value* to a dict.

    Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
    Raises ValueError on a non-JSON string, JSON that is not an object,
    or any other type.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = _strict_loads(value)
        except json.JSONDecodeError as exc:
            msg = f"'{name}' was a string but not valid JSON: {exc}. "
            msg += f"Near the failure: {_json_error_window(value, exc.pos)}. "
            msg += f"Pass '{name}' as an object, not a JSON-encoded string."
            raise ValueError(msg)
    if not isinstance(value, dict):
        msg = f"'{name}' must be an object (dict) or a JSON-encoded object string; "
        msg += f"got {type(value).__name__}."
        raise ValueError(msg)
    return value
