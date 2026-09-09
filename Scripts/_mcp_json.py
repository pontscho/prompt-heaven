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

The test fleet *does* import it, which is the point: a helper inlined into
fifteen files is unit-tested once, here.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
`_canonical_function` is the near miss that shows why the rule needs writing
down: its two lines are identical in three servers, but its body reads a
per-server `FUNCTION_ALIASES` table, so sharing it means sharing a promise about
the host's globals. Until that promise has a form and a check, it stays out.
"""


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
