#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for how a server CONFIGURES logging -- not what it logs.

The fleet's logging is three unrelated surfaces, and only one of them is
duplicated. This file is that one: the twenty lines in every `main()` that
decide a LEVEL from two flags and point a handler at stderr or at a 0600 file.
The other two stay where they are, and the reasons differ.

**The wire log stays out, and the generator is why.** What `McpServer.run` and
`McpServer._write` may log is a security invariant, decided in
`docs/adr/0011-a-truncated-payload-carries-the-first-cookie.md` and gated by
`tests/test_wire_log.py`. That ADR already rejected lifting `_write` into a
region, on a mechanical ground that has not moved: `host_provides` offers a
region only the host's module-level IMPORTS, and `log` is a module-level
ASSIGNMENT in all fifteen servers, so any block reading it is refused by name at
END-marker time. The block below is a different shape for exactly that reason --
it is a module-level `def` whose free names are `logging`, `os` and `sys`, three
imports every server already has, and it never reads `log`.

**The logger object stays out.** `log = logging.getLogger("mcp-git")` is a
single-target assignment, so `assign_name` would happily make it a block -- and
that is the trap. The one thing that differs between the fifteen copies is the
string, so a shared block would paste the same server NAME into every host and
every log line in the fleet would claim to come from one server.

**The argparse flags stay out, on evidence rather than on principle.** Three
help texts and one flag surface differ, and one of those differences is a
feature: `mcp-webfetch` declares `-v` and `--verbose` as aliases of `--debug`.
Sharing the declarations means deleting that to please a generator, which is
the tail wagging the dog. The flags are declarations; this block is the logic.

**Domain logging stays out** because it is not duplicated at all -- CUDA SDK
probing, tshark's argv lines and purity's compile-flag warnings are per-server
by nature, and nothing here would ever be asked for by two files.

**THIS IS NOT A LIFT, AND THE FIRST GENERATED DIFF WILL SAY SO.** Where
`_mcp_json.py` records that a faithful lift shows marker lines only, this block
was RESHAPED on the way in, and declaring that up front is the point: the hand
copies wrap `os.open(...)` and `logging.basicConfig(...)` across several
physical lines, which is an implicit line join, which `block_is_tab_safe`
refuses -- and `mcp-forge` and `mcp-webfetch` are TAB-indented hosts, so the
verbatim text would have been refused by name for exactly those two. Every call
below therefore fits one physical line, and every leading run is a whole
four-space level, including inside this docstring. That constraint is the
reason the body reads the way it does; it is not style.

**The duplication has already cost a fleet-wide fix once.** `c8b74d0` -- "every
log file was created world-readable in a world-writable directory" -- had to
touch every server to change one security property, because the property was
written down fifteen times. That commit is the argument for this file, and it
is measured rather than speculative.

**The 0600 pair needs both halves, and the block's own docstring is too short to
say why.** The mode argument to `os.open` applies only when the file is CREATED,
so on its own it does nothing to a log an earlier run already left 0644 under a
laxer umask; `os.fchmod` is what tightens that one. fchmod takes the descriptor
just opened rather than the path, so nothing can swap the path underneath it
between the two calls. `logging.FileHandler` is deliberately not used: it passes
no mode at all, so the file lands at `0o666 & ~umask` and the permissions become
a property of whoever launched the server. The sink is stderr when no file is
given, never stdout, which carries the JSON-RPC frames.

**The docstring inside the block is kept SHORT on purpose.** It is copied into
fifteen servers; this module docstring is copied into none. The long form of an
argument belongs on the side of the line that is written once -- the same split
`_mcp_paging.py` uses, where `_max_answer_chars` carries one sentence and the
reasoning for the number sits above it in the canonical file.

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
"""

import logging
import os
import sys


def _configure_logging(debug, log_file):
    """DEBUG to *log_file* (mode 0600) or to stderr, else WARNING. Never stdout.

    Either flag enables DEBUG: `--log-file` does not redirect the log, it turns
    it on. `Scripts/_mcp_logging.py` carries the rest -- why the 0600 pair needs
    both calls, and what deliberately stays out of this block.
    """
    level = logging.DEBUG if (debug or log_file) else logging.WARNING
    handlers = []
    if log_file:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a")))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
