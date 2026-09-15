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
is spoken**: `Content-Length` framing for a message, the `file://` DocumentUri
for a path, and the client-side hops that put a message on that wire. None of
it is a JSON helper -- the framing carries bytes with no JSON anywhere in it,
the URI pair is the protocol's own spelling of a filename, and `_request` is a
correlation table and a timeout around a transport, not an envelope builder --
and a server that speaks no LSP has no business being able to ask a JSON source
for any of them. Four servers (`mcp-purity`, `mcp-clangd`, `mcp-cuda`,
`mcp-lua-lsp`) drive a language server over stdio and take the blocks below; the
other eleven never name this file at all.

**`_abs_path` is here on purpose, and it is the one worth arguing about.** Its
body mentions no LSP at all: it resolves a possibly-relative path against
`self.project_root` and hands back a string, which read alone is generic file
handling wearing an LSP-looking name. It is here because it is not alone. It
and `_abs_uri` are ONE resolution with two terminal spellings -- the local one a
client reads bytes with and runs its containment checks on, and the wire one it
puts in a `textDocument.uri` -- and the two differ by their last line and
nothing else. Measured across the fleet before the lift: both names are bound in
exactly the four LSP hosts and in no other server, so there is no non-LSP
consumer to be served the wrong domain. Splitting them would mean a sixth
canonical file whose consumer set is byte-identical to this one's and which
names the same protocol's hosts -- which splits a domain rather than separating
two, the same reading that put the DocumentUri pair here in `11a3542`.

**`_send` is NOT here, and the reason is the generator's contract rather than
the domain.** It belongs to this file more obviously than anything else in the
fleet -- it is `encode_lsp_message`'s only caller, and it is byte-identical in
all four hosts. It cannot be a block because its one free name is
`encode_lsp_message`, which each host holds as a module-level `def` (its own
generated region), and `host_provides` offers a region only the host's
module-level IMPORTS. That is the same refusal `log` earns for `_write` and the
host's server class earns for `_tool_error`. The documented remedy -- co-list
the dependency on the same marker -- does not apply to an INDENTED region: the
regions below sit inside a client class, so co-listing would emit a module-level
function as a class member, where the `encode_lsp_message(body)` call in `_send`
would not even reach it. So `_send` stays a hand copy in four files. It is
declared HERE rather than counted by the suite's hand-copy census, which
intersects each server against the canonical block NAMES and so can only ever
see a copy of something that already is a block: `_send` is not one, which puts
it in the same register as `_tool_error`.

The test fleet *does* import it -- `tests/test_generated_region.py` group E loads
it as a module and exercises every block directly, which is the point: a helper
inlined into four files is unit-tested once, here. Group A separately proves the
copies MATCH. The two claims are different, and a drift gate on its own would
only ever prove that four files agree on the same bug.

**Block contract.** A block may reference only builtins, the stdlib names this
module imports, and its own arguments -- never a name the host server defines.
`encode_lsp_message` reads `json`, `path_to_uri` reads `pathlib`, and
`uri_to_path` reads `urlparse` and `url2pathname`. Of the client half,
`_request` reads `asyncio` and `Any`, `_notify` reads `Any`, and `_abs_uri` and
`_abs_path` each read `pathlib`. `self` is an ARGUMENT, so `self._send`,
`self._pending`, `self._next_id` and `self.project_root` are attribute access
and not free names at all -- the receiver is what the four hosts are promising,
and the generator has nothing to check about it. The generator checks the rest
against the HOST's own imports rather than trusting this file, so a server that
dropped one is refused at generation time and not at run time -- which is how
the URI pair was paid for: lifting it cost `mcp-clangd`, `mcp-cuda` and
`mcp-lua-lsp` two `urllib` imports each, by hand, before their regions would
render at all. The client half cost nothing, because all four hosts already
imported `asyncio`, `pathlib` and `Any`; that was measured before the lift, not
assumed after it.

**Annotations are not free.** A block whose signature says `value: Any` needs
`Any` in the HOST's namespace, evaluated at def time, so a server that does not
import it dies at startup. This file used to say its blocks stay unannotated
beyond builtins, and `_request` and `_notify` end that: both annotate
`params: Any`. The rule they follow is the one `_mcp_json.py` has followed all
along -- `_result`'s `msg_id: Any` has demanded `typing.Any` of fourteen hosts
since long before -- and it is not "builtins only" but **a name every host of
the block already imports**. The distinction is what makes the bar affordable
here: these four blocks go to FOUR hosts, not fifteen, and all four import
`Any`. A block whose consumer set is the whole fleet still has to clear
fifteen.
"""

import asyncio
import json
import pathlib
from typing import Any
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


# --- The client half: the two JSON-RPC hops over the framed transport ---------
#
# A METHOD, not a function: these are emitted at an INDENTED marker inside each
# host's LSP client class, the same route `_result` and `_error` take into
# `McpServer`. They are written at module top level here because that is the
# only place `load_blocks_text` looks, and `self` is an ordinary first parameter
# -- the marker's own column is the whole of what places them.
#
# `_request` is the request/reply hop and `_notify` the fire-and-forget one, and
# the difference between them is one key: a notification carries NO `id`, which
# is what tells the language server not to reply. Give it one and the reply
# arrives with nothing in `_pending` waiting for it.
#
# The timeout arm is the half worth having one writer for. It does not raise --
# it POPS the pending future and answers with an error dict -- so a slow backend
# costs one call rather than leaking a future into a table that is never swept.
#
# `_next_id += 1` followed by `_pending[req_id] = fut` is also the pair
# `tests/test_read_loop.py` names as safe ONLY on the event-loop thread (ADR
# 0008). That invariant now has one writer instead of four; the block is the
# agreement, not the audit, and a server needing a different one keeps its own.
#
# Measured byte-identical across the four LSP hosts before the lift.

async def _request(self, method: str, params: Any, timeout: float = 10.0) -> dict:
    req_id = self._next_id
    self._next_id += 1
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    self._pending[req_id] = fut
    await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        self._pending.pop(req_id, None)
        return {"error": {"message": f"timeout waiting for {method}"}}


async def _notify(self, method: str, params: Any) -> None:
    await self._send({"jsonrpc": "2.0", "method": method, "params": params})


# --- The two spellings of a resolved path -------------------------------------
#
# One resolution, two endings. Both absolutise a caller's path against
# `self.project_root` and `resolve()` it; then `_abs_uri` spells the result the
# way the wire does (`as_uri()`, percent-encoded) and `_abs_path` the way the
# local filesystem does. They are not inverses of each other -- they are the
# same value in the protocol's spelling and in the OS's, which is why they share
# a source and why neither is the other's round trip.
#
# `_abs_uri` is NOT `path_to_uri` with a root bolted on, and the difference is
# one call: `path_to_uri` uses `absolute()`, which leaves symlinks and `..`
# alone, while these two use `resolve()`. The containment checks downstream read
# a resolved path, so converging the two would be a behaviour change, not a
# tidy-up. Left alone deliberately.
#
# Measured byte-identical across the four LSP hosts before the lift.

def _abs_uri(self, path: str) -> str:
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(self.project_root) / p
    return p.resolve().as_uri()


def _abs_path(self, path: str) -> str:
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = pathlib.Path(self.project_root) / p
    return str(p.resolve())
