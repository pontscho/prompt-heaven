#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the LSP wire helpers the MCP servers share.

**No server imports this module.** Its named blocks are inlined into each server
between `# BEGIN GENERATED` / `# END GENERATED` markers by
`Scripts/amalgamate.py`, so every server stays the self-contained single file
`MCP_SKELETON.md` says it is. That is not stylistic: an imported sibling would
write `Scripts/__pycache__/*.pyc` into a tree every suite that snapshots
bytecode asserts stays empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes where `tests/test_mcp_footprint.py` reaches for them.

**Why this is a second file rather than more of `_mcp_json.py`.** The source
named on a region's BEGIN line is not decoration -- the generator resolves that
region's names against THAT file's block map and no other. So each canonical
file is a domain, and the domain has to be true: LSP `Content-Length` framing is
not a JSON helper, and a server that speaks no LSP has no business being able to
ask a JSON source for it. Four servers (`mcp-purity`, `mcp-clangd`, `mcp-cuda`,
`mcp-lua-lsp`) drive a language server over stdio and take the block below; the
other eleven never name this file at all.

The test fleet *does* import it -- `tests/test_generated_region.py` group E loads
it as a module and exercises every block directly, which is the point: a helper
inlined into four files is unit-tested once, here. Group A separately proves the
copies MATCH. The two claims are different, and a drift gate on its own would
only ever prove that four files agree on the same bug.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
`encode_lsp_message` reads `json`, and the generator checks that against each
host's imports rather than trusting it; a host that dropped `import json` would
be refused at generation time, not at run time.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. `dict`/`bytes` below are builtins, so the one block
here is safe as written -- but a future block wanting a `typing` name has to
carry that requirement to fifteen hosts, which is why the canonical blocks stay
unannotated beyond builtins.
"""

import json


# --- LSP framing (Content-Length over stdio) ----------------------------------
#
# The header counts BYTES, not characters, which is the whole reason this is
# shared rather than retyped: `len(text)` reads correct on ASCII and truncates
# the first non-ASCII payload, and the language server on the other end then
# desynchronises rather than erroring -- a hang, at a point in the stream far
# from the message that caused it.
#
# Measured byte-identical across the four LSP servers before it was lifted.

def encode_lsp_message(body: dict) -> bytes:
    """Encode a dict as an LSP message with Content-Length framing."""
    text = json.dumps(body)
    encoded = text.encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n"
    return header.encode("ascii") + encoded
