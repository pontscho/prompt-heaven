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
file is a domain, and the domain has to be true. This one is **how the LSP wire
is spoken**: `Content-Length` framing for a message, and the `file://`
DocumentUri for a path. Neither is a JSON helper -- the framing carries bytes
with no JSON anywhere in it, and the URI pair is the protocol's own spelling of
a filename -- and a server that speaks no LSP has no business being able to ask
a JSON source for either. Four servers (`mcp-purity`, `mcp-clangd`, `mcp-cuda`,
`mcp-lua-lsp`) drive a language server over stdio and take the blocks below; the
other eleven never name this file at all.

The test fleet *does* import it -- `tests/test_generated_region.py` group E loads
it as a module and exercises every block directly, which is the point: a helper
inlined into four files is unit-tested once, here. Group A separately proves the
copies MATCH. The two claims are different, and a drift gate on its own would
only ever prove that four files agree on the same bug.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
`encode_lsp_message` reads `json`, `path_to_uri` reads `pathlib`, and
`uri_to_path` reads `urlparse` and `url2pathname`. The generator checks each
against the HOST's own imports rather than trusting this file, so a server that
dropped one is refused at generation time and not at run time -- which is how
the URI pair was paid for: lifting it cost `mcp-clangd`, `mcp-cuda` and
`mcp-lua-lsp` two `urllib` imports each, by hand, before their regions would
render at all.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. `dict`/`bytes`/`str` below are builtins, so every
block here is safe as written -- but a future block wanting a `typing` name has
to carry that requirement to fifteen hosts, which is why the canonical blocks
stay unannotated beyond builtins.
"""

import json
import pathlib
from urllib.parse import urlparse
from urllib.request import url2pathname


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


# --- LSP DocumentUri <-> filesystem path --------------------------------------
#
# The other half of the same wire: `Content-Length` above frames a MESSAGE, this
# pair frames a PATH. They are inverses or they are nothing -- `path_to_uri`
# percent-ENCODES through `as_uri()`, so only a `uri_to_path` that DECODES
# undoes it, and three of the four hosts carried a prefix-strip that did not.
#
# Measured before the lift: FOUR copies of `uri_to_path` with THREE distinct
# bodies -- two byte-identical prefix-strips, a third with the docstring dropped,
# and this one -- against four copies of `path_to_uri` with two bodies differing
# only by that same dropped docstring. So the encode side already agreed fleet-
# wide while the decode side did not, which is the shape that makes a round-trip
# defect survive review: every copy looks right beside its own neighbour.
#
# The body below is `mcp-purity`'s, verbatim; the other three converge onto it
# rather than the reverse, because the divergence was not stylistic. Both
# failures it fixes are stated in the docstring, and the second is a security
# property rather than a correctness one -- see there.

def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute filesystem path - the exact inverse
    of pathlib.Path.as_uri() / path_to_uri(). Percent-DECODES the path
    (url2pathname is platform-aware: drive letters on Windows, plain unquote on
    POSIX), so a path containing spaces or other reserved chars round-trips
    losslessly instead of carrying literal %20 back into open_document/realpath.

    SECURITY: decoding also normalises encoded traversal - an LSP-returned
    file:///root/%2e%2e/escape decodes to /root/../escape, which the downstream
    realpath / _path_within_root containment checks then collapse and reject.
    The old prefix-strip left %2e%2e literal, slipping past those checks.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    return url2pathname(parsed.path)


def path_to_uri(path: str) -> str:
    """Convert an absolute path to a file:// URI."""
    return pathlib.Path(path).absolute().as_uri()
