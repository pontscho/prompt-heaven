---
name: 0023-the-websocket-client-is-a-sixth-domain
type: adr
status: active
title: The WebSocket client is a sixth canonical domain
description: Decision to lift mcp-gdc's hand-written RFC 6455 client into a sixth canonical source, Scripts/_mcp_websocket.py, as a sans-IO core with an asyncio and a blocking-socket wrapper, and to generate it into the DuckDuckGo search script through a hand-declared host list -- removing the third-party websocket-client dependency -- with the hardening that came with the lift, the red run that measured it, the alternatives rejected, and what stays out of scope.
sources:
  - Scripts/_mcp_websocket.py
  - Scripts/amalgamate.py
  - Scripts/mcp-gdc.py
  - Scripts/search_duckduckgo.py
  - tests/test_mcp_websocket.py
  - tests/test_generated_region.py
verified:
  commit: b9024a0
  date: 2026-09-29
links:
  - generated-regions
  - scripts
  - tests
  - spec-ddg
  - 0012-the-transport-tier-diverged
  - 0014-a-canonical-source-is-a-domain
  - 0024-pure-python-39-and-the-stdlib
---

# ADR 0023: The WebSocket client is a sixth canonical domain

**Status:** accepted. The decision is where the WebSocket client lives and how
its two consumers get it. The hardening list is recorded here because it
shipped with the lift and is what the lift's tests gate; it is not a separate
decision.

## Context — two clients, one of them not ours

The repo spoke the WebSocket wire in two places, both to the same peer — Chrome's
DevTools Protocol endpoint.

`Scripts/mcp-gdc.py` carried a stdlib client of about a hundred lines:
`_ws_connect`, `_ws_recv`, `_ws_send`. It worked against a well-behaved Chrome
and was wrong in ways a well-behaved Chrome never exercises. It found the
handshake's `101` and the `Sec-WebSocket-Accept` value by **substring** over the
whole response. It read `2 + len` bytes for whatever length a frame header
announced, so a 64-bit length would have been an attempt to read 2^63 bytes. It
returned `None` for a continuation frame, which its caller reads as "connection
closed". It skipped pings without answering them, by **recursing** once per
control frame. It accepted masked server frames, reserved bits and unknown
opcodes. It decoded text with `errors="replace"`. And it threw away any bytes
the server sent in the same segment as its `101`.

`Scripts/search_duckduckgo.py`'s opt-in `cdp` backend used the third-party
`websocket-client` package — `websocket.create_connection(ws_url,
suppress_origin=True, timeout=30)` — imported lazily and unguarded, and not
installed on the machine the change was made on.

## Decision

1. **A sixth canonical source, `Scripts/_mcp_websocket.py`,** holding one domain:
   how a client speaks the WebSocket wire.
2. **A sans-IO core plus two thin wrappers.** Every decision is a pure function
   over bytes — handshake request, header-block split, handshake verification,
   mask, frame encode, frame parse, message assembly, control-frame reply, and
   a `_ws_step` that advances a connection over what is buffered. The asyncio
   wrapper keeps gdc's three names; the blocking-socket wrapper adds four for
   the search script. A host takes the core and the one wrapper it uses.
3. **A hand-declared host list in the generator.**
   `Scripts/amalgamate.py:DECLARED_HOSTS` names `search_duckduckgo.py`, so the
   default run and `--check` cover it; the test suite mirrors the tuple.
4. **`websocket-client` is gone.** The search script's `cdp` backend runs on the
   stdlib, as [[0024-pure-python-39-and-the-stdlib]] asks of the whole tree.

## Why a sixth source passes ADR 0014's test

[[0014-a-canonical-source-is-a-domain]] allows a new source when a shareable
block's domain cannot be held by an existing source **without making it a
shelf**. The nearest candidate was `_mcp_lsp.py`, and it is the instructive
refusal: its domain is how the LSP wire is spoken — `Content-Length` framing
over stdio, DocumentUris, the client-side hops. A WebSocket frame is framing
too, but a different protocol's, and filing it there would make that source
"wire protocols we speak", which is a shelf with a better name. The JSON source
speaks envelopes and knows nothing of transports. So the domain is new, and the
rule's price is the one it names: a line in each of the two registries.

The source is the first whose domain is a **protocol client** rather than
server plumbing, and the first whose blocks mostly call one another. That second
fact has a mechanical consequence: `host_provides` offers a region only the
host's imports, so no websocket block can stand in a region alone. Every host
spells one marker, dependency first, and the generated-region suite's tab
fixture renders each block inside exactly that shape.

## Why generation and not an import — for a host that is not a server

The generation-versus-import decision was taken for the servers in `8effd2f` and
is not re-decided here. It had to be re-checked for the search script, because
none of the servers' reasons is about servers as such:

- Agents run the script by path and without `-B`, so an imported sibling would
  write `Scripts/__pycache__` on every search — into the tree the
  bytecode-snapshotting suites assert stays empty.
- A copy of the one file taken on its own would stop at an `ImportError`.

Both hold, so the search script takes generated blocks like a server does.

**Why a declared list and not a wider glob.** `TARGET_GLOB` is `mcp-*.py`.
Widening it to `*.py` would make every script in `Scripts/` a target by merely
existing — exactly the argument that keeps `CANONICAL_NAMES` hand-written — and
would also sweep in the canonical sources themselves. A tuple of one filename
is the smallest deliberate edit. The `--census fleet` count stays on the server
glob, because that census's first line says it counts MCP servers; the
declared host is gated by `fleet-ok` and `--check`, not counted there.

## The hardening that came with the lift

Each item is a `WebSocketError` — a subclass of `ConnectionError`, so gdc's
existing handlers, which catch `Exception` around the receive loop and the
session, keep working without a new clause:

- **Handshake.** The status line must be exactly `HTTP/1.1 101`. Headers are
  parsed into a case-folded map, repeated headers are joined, and each check
  compares one value exactly: `Upgrade` is `websocket`, `Connection` carries an
  `upgrade` token, and `Sec-WebSocket-Accept` equals the computed value. An
  extension or subprotocol nobody offered is refused. A host or path carrying
  whitespace or a control character is refused before the request is built,
  so a URL cannot write header lines. No `Origin` header is sent — what
  `suppress_origin=True` bought, and what Chrome's allow-list requires.
- **Frames.** A reserved bit, an unknown opcode, a masked server frame, a
  control frame over 125 bytes or without FIN, and a 64-bit length with its top
  bit set are each refused **from the header**, before any payload is read.
  Client frames are always masked. The mask is one big-integer XOR, not a
  per-byte generator.
- **Messages.** Fragments are assembled; a control frame may arrive between
  them. A continuation with nothing open, or a new data frame inside an open
  message, is refused. Text is strict UTF-8, validated once the message is
  whole, so a character split across two fragments is fine.
- **Control frames.** A ping is answered with a pong carrying the same payload.
  A close is answered with a close echoing the status code, after the payload
  is checked (one byte, a reserved code, or a non-UTF-8 reason is refused), and
  the reader returns `None`. All of it is a loop, so a peer streaming control
  frames cannot grow the stack.
- **Bytes after the `101`.** They stay in the connection's buffer and are parsed
  as the first frames.

## The caps, and the argument for each number

- `WS_MAX_HANDSHAKE_BYTES = 64 KiB`. Chrome's upgrade response is under 200
  bytes. The cap is three orders of magnitude of headroom and still a ceiling on
  a peer that streams header bytes forever.
- `WS_MAX_FRAME_BYTES = WS_MAX_MESSAGE_BYTES = 256 MiB`. CDP answers are single
  unfragmented text frames, and the largest are base64 screenshots and captured
  response bodies — tens of megabytes for a full-page capture of a long page at
  a high device-pixel ratio. Chromium's DevTools HTTP handler sizes its own
  send buffer at 256 MiB, so no message Chrome can send is larger. **That figure
  is recalled from `devtools_http_handler.cc`, not re-measured for this change**,
  and it is the weakest number on this page. The two caps are equal because
  Chrome does not fragment, so a smaller frame cap would refuse a message the
  message cap admits. The suite asserts that relation, frame ≥ message, and not
  the values.

## Behaviour changes a gdc caller can see

- `_ws_connect` returns a `_WsConnection`, not a `(reader, writer)` pair, and
  `_ws_recv` / `_ws_send` take it. `CdpSession` holds `self.ws` instead of
  `self.reader` / `self.writer`. Nothing outside `CdpSession` touched either.
- A fragmented CDP reply is delivered instead of ending the session.
- A ping is answered; a masked server frame, a protocol violation or invalid
  UTF-8 ends the session with a `WebSocketError`, where it used to be accepted
  or decoded with replacement.
- A link that ends without a close frame raises `WebSocketError`, where it used
  to raise `IncompleteReadError`. Both end the receive loop the same way.
- A `wss://` debugger URL is refused by name. The old parser stripped the scheme
  and dialled port 80 in clear text.
- The handshake's drain and header read now share one deadline of `timeout`
  (10 s), and a pong or close reply is drained under the same bound. A failed
  handshake closes the stream; the old one left it open.
- The one debug line `_ws_connect` logged moved to `CdpSession.connect`. A block
  cannot read the host's `log`, which is an assignment, not an import.
- Binary messages are still decoded with replacement, for compatibility. CDP
  never sends one.

## Red first

`tests/test_mcp_websocket.py` group E drives both hosts end to end against a
loopback CDP peer written from the RFC. It was written and run before either
host was switched, against the hand-written client. **All four host cases
failed there**, and the 38 core cases passed:

- `gdc-fragmented-reply`: `ConnectionError('CDP connection closed')`. The
  continuation frame came back as `None`, and the session read that as a close.
- `gdc-ping-answered`: the same `ConnectionError`, with no pong ever seen by the
  peer. The ping travels in the `101`'s own segment, which the old client threw
  away with its header buffer.
- `gdc-masked-server-frame-refused`: the session connected and answered through
  frames RFC 6455 forbids a server to send.
- `search-cdp-backend`: `ModuleNotFoundError: No module named 'websocket'`.

After the switch, all 42 pass. The generated-region suite kept its 92 cases:
the new registry entries, the declared host and the tab pairing were folded
into existing cases rather than added as new ones.

## Alternatives rejected

**1. Leave gdc's client where it was and give the search script a copy.** Two
hand copies of a protocol client is the drift the generator exists to prevent,
and the first one was already wrong in the ways listed above.

**2. Keep `websocket-client` for the search script.** It was not installed, it
was imported unguarded, and [[0024-pure-python-39-and-the-stdlib]] asks the
tree to need nothing outside the stdlib where the stdlib suffices. For a ws://
client to a local Chrome, it does.

**3. Put the blocks in `_mcp_lsp.py`.** Refused above: that makes it a shelf.

**4. One block — a client class with methods.** That shrinks the marker to one
name, but the core would no longer be testable as functions over bytes, and the
two wrappers would have to share one class across asyncio and blocking I/O.
The sans-IO split is what makes one core serve two I/O models.

**5. Widen `TARGET_GLOB`.** Refused above: a file would become a target by
existing.

**6. Import `_mcp_websocket.py` from the search script.** Refused above: it
writes `__pycache__` into `Scripts/` on every search, and it breaks the
single-file copy.

## Out of scope, deliberately

- **`wss://` / TLS.** Refused by name, never silently downgraded. Chrome's
  debugging endpoint is `ws://` on loopback or a LAN address.
- **Extensions**, including permessage-deflate. None are offered, so a server
  that negotiates one is refused. **Subprotocols**, likewise.
- **A client-initiated ping** and any keep-alive. gdc's per-command timeouts
  already turn a dead link into an eviction.
- **Close-handshake completion.** The sync wrapper sends a close and does not
  wait for the answer. The async wrapper sends none of its own — `CdpSession`
  closes the stream as before.
- **The minimal-length rule.** A peer that uses the 16- or 64-bit form for a
  length that fits a shorter one is accepted.
- **The close-code registry beyond the never-sent codes.** Below 1000, and
  1004/1005/1006/1015, are refused; the reserved 1016-2999 range is not, so a
  future RFC's code does not end a session.
- **The census.** `--census fleet` counts only the server glob. A block
  generated only into the declared host shows there as defined but not named on
  a server marker.
