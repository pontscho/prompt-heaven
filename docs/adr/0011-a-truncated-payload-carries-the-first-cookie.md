---
name: 0011-a-truncated-payload-carries-the-first-cookie
type: adr
status: active
title: A truncated payload still carries the first cookie
description: Decision to make MCP wire debug logging structure-only across all fifteen servers and the template they are copied from, gated by a suite run red first, plus the five alternatives rejected on the way, the live exposure that set the bound, and the gate's two declared blind spots.
sources:
  - Scripts/MCP_SKELETON.md
  - Scripts/mcp-purity.py
  - Scripts/mcp-gdc.py
  - tests/test_wire_log.py
  - tests/run.py
verified:
  commit: e6d1496
  date: 2026-09-11
links:
  - scripts
  - tests
  - generated-regions
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0010-a-handler-failure-must-reach-iserror
  - 0012-the-transport-tier-diverged
---

# ADR 0011: A truncated payload still carries the first cookie

**Status:** accepted (implemented, `ae31685`; gated by `bdfe3e8`). Append-only —
the WHY is frozen here; the living WHAT/HOW is the pair of wire sites in every
`Scripts/mcp-*.py`, section 5 of `Scripts/MCP_SKELETON.md`, and the suite
`tests/test_wire_log.py`.

## Context — a redaction that was never weighed, only unpropagated

In June, audit finding **F12 / CWE-532** (insertion of sensitive information
into a log file) was closed for exactly **one** server. `abb19328` touched a
single file and added fifty lines: `mcp-purity`'s inbound wire log became
method, id, tool name and argument **keys**, and its outbound log became id plus
outcome `Scripts/mcp-purity.py:run` `Scripts/mcp-purity.py:_write`. Both sites
still carry the comment naming the finding.

In August, `f1d117b` rewrote `_write` in all fifteen servers at once for an
entirely unrelated reason — the read-loop conversion of
[[0008-a-serialized-read-loop-looks-like-a-dead-server]]. It copied
`mcp-purity`'s body into `mcp-lua-lsp`, purity's sibling LSP server, so lua-lsp
silently **inherited** the June redaction. The other thirteen were written from
`Scripts/MCP_SKELETON.md`, which had never been told about F12, and received the
pre-fix form.

That is the whole shape of the lesson, and it is why this is a decision and not
a bug fix: **the redaction was never weighed and rejected anywhere.** It simply
was not in the file the rollout copied from. A convention that lives in one
server plus a commit message propagates by luck.

## The measured state before the fix

Counted at `ae31685^` across all fifteen servers.

**Outbound**, in `McpServer._write`: two servers logged structure only
(`mcp-purity`, `mcp-lua-lsp`); nine logged the serialised reply truncated to two
hundred characters (`out[:200]`); and four logged the frame **whole** —
`mcp-clangd`, `mcp-cuda`, `mcp-gdc`, `mcp-lldb`, with a bare
`log.debug("→ RAW: %s", out)`.

**Inbound**, in `McpServer.run`, was worse in two of those four: `mcp-clangd` and
`mcp-cuda` logged the raw stdin line **untruncated**, so a request and its reply
were both on disk in full. Nine logged `json.dumps(msg)[:200]`. `mcp-gdc` and
`mcp-lldb` had no read-loop wire log at all.

`Scripts/MCP_SKELETON.md` taught the defect at **both** sites — line 340 inbound
and line 371 outbound, the two lines `ae31685` replaced. Nothing in `docs/`
mentioned F12, CWE-532, or the phrase "structure only": zero hits across the
whole wiki. Nothing in `tests/` gated it. The one redaction in the fleet was held
in place by nothing.

## Why it mattered — enablement, not the slice

What decides the exposure is destination and enablement, not the truncation
bound: `level = logging.DEBUG if (parsed.debug or parsed.log_file) else
logging.WARNING` `Scripts/mcp-gdc.py`. **`--log-file` does not redirect the log,
it turns it on**, and the handler it installs is a plain `logging.FileHandler`
with the default mode.

Measured live on this machine while the finding was being written: **four**
`mcp-gdc` processes were running with `--log-file`, the oldest for four days and
three hours, all appending to one unrotated world-readable file,
`/tmp/mcp-gdc.log` — 3.3 MB, mode 0644, in a 1777 sticky directory. It has been
removed. That server's handlers return cookie values in cleartext
`Scripts/mcp-gdc.py:handle_get_cookies`, full DOM, and arbitrary JS evaluation
results.

**The bound, stated honestly, because an overstated finding is its own defect.**
Only debug level is affected. Every `log.warning` and above in the fleet was
swept, and the only payload-adjacent line anywhere is the non-object guard, which
logs `type(msg).__name__` and never a value; at the thirty wire sites every
non-`debug` call takes a literal, an exception name, or exactly that
`tests/test_wire_log.py`. `mcp-lldb` ran with no flags, so it sat at WARNING and
its debug line was a no-op, and `mcp-clangd` / `mcp-cuda` are unregistered —
their capabilities live behind `purity_call`
([[0001-purity-server-unification]]). **The live exposure was one server.**

## Decision — structure only, fleet-wide, and the deliverable is the gate

Wire debug logging is **structure only** at both ends, in all fifteen servers and
in the template: method, id, tool name and argument **keys** inbound; id and
outcome outbound. Thirteen servers and `Scripts/MCP_SKELETON.md` took
`mcp-purity`'s two forms verbatim, including the defensive `isinstance`
re-binding of `params` / `arguments` — those may be a non-dict on a malformed
message, and a debug log must never be the thing that kills the read loop.

Each file kept its own arrow glyph — forge and jenkins spell them `<-` / `->`,
the rest use arrows — because the format string is a constant the analyser never
reads, so homogenising it would have been a change no gate could see the point
of. The word `RAW` was dropped from the five labels that carried it: after this
commit nothing raw is logged and the label would be false.

## Alternatives evaluated

### Rejected — truncate the four raw servers to `[:200]` like the other nine
Uniform, one-line-per-file, and it would have made the fleet consistent. It lost
because **truncation is not a mitigation and was never claimed to be one**: the
first two hundred characters of a `get_cookies` reply are cookies, and a JSON-RPC
frame puts the method, the ids and the first fields of the payload at the FRONT.
A fix that merely lowers the slice bound does not pass the gate either, by
construction.

### Rejected — extract `_write` into a generated region so it cannot drift again
The intuitive answer, and the one the generator's own contract refuses.
`host_provides` returns module-level **import aliases only** — not defs, not
assignments `Scripts/amalgamate.py:host_provides` — and `log` is a module-level
*assignment* (`log = logging.getLogger(...)`) in all fifteen servers, so any
block reading it is rejected by name at END-marker time. The block walk is over
`tree.body` as well, so a method is not a block; the `_result` / `_error` escape
works only because they are module-level `@staticmethod`s in the canonical
source, which does not transfer to a method taking `self`. Prior art records the
same shape one register over: `_tool_error` cannot be a block because its free
names include the host's own server class, and is documented as a divergence
rather than homed ([[generated-regions]]). **This is why the gate, and not the
lift, is the deliverable.**

### Rejected — survey the template at INFO, as `tests/test_read_loop.py` does
The sibling suite reports on `Scripts/MCP_SKELETON.md` and never fails on it, on
the ground that gating a Markdown page is a scope decision for a human. Here the
evidence has already made that decision: **this template is the measured
propagation vector for this exact defect, and it carried it at both sites.** It
is gated instead — the sample is lifted out of the markdown by script, spliced
into a minimal host and handed to the same analyser the live fleet gets, with
host line numbers mapped back to the page. That is the technique `a7b165e`
proved; hand-checking prose is how the template stayed wrong through two edits
after the read-loop rollout.

### Rejected — infer each server's shape at runtime instead of declaring it
It loses for `read_loop`'s reason, and the counter-example is this suite's own
subject: a suite that infers the fleet's current shape only proves the fleet
agrees with itself, and **it would have passed green on the very state it was
written to catch.** The expected shape is therefore a hand-written table, one row
per server, that a reader can check line by line.

### Rejected — give `mcp-gdc` and `mcp-lldb` an inbound log for uniformity
Two servers log nothing on the read loop. Adding a log so the table has no holes
would be adding logging nobody asked for in order to make a checker's life
easier. **Silence is a correct, declarable shape** — it is declared `SILENT` in
the table, so a server that quietly *grows* a wire log has to be classified
rather than accepted.

## The gate

`tests/test_wire_log.py`, **53 cases in six groups**, the count typed in the
suite table `tests/run.py`: A gates outbound `_write` and B inbound `run`, one
case per server; C is the roster (the table covers the tree exactly and cannot
itself declare a payload); D is the negative control; E gates the template; F is
hygiene.

**It was run red first, against the fleet as it stood: 26 failures — 13 outbound,
11 inbound, and 2 in the template**, each naming its offending expression at its
own line. Those splits are not a coincidence and are worth recording as
corroboration: 13 = the nine truncated plus the four whole; 11 = the nine
truncated plus the two raw. A static reading of fifteen servers and a runtime
measurement named the same set with no differences in either direction — the
same two-methods-agree standard [[0010-a-handler-failure-must-reach-iserror]] set
when it ran its own gate red before fixing anything.

The analyser is an AST walk rather than a regex because every shape that matters
hides from a line reader: a slice is a `Subscript`, an f-string keeps its names
in `FormattedValue`, and `json.dumps(msg)` is a call. **Its rule is on the VALUE
an expression reaches, not on its spelling** — so `msg["method"]` passes and
`_args.get("content")` does not, and a correct refactor that changes `.get` to a
subscript does not fail.

**Two declared blind spots, on the page because an unstated scope is the same
defect as a false invariant.** An `except ... as exc` name is not treated as a
payload root, so `exc.doc` would slip through — a judgement, taken because
`JSONDecodeError.__str__` renders message plus line/column and never `.doc`. And
only `run` and `_write` are read, so a handler that logs its own arguments is out
of scope by design; six servers log method and id from inside `_handle_message`,
which this gate does not read and does not need to.

## Consequences

- **The fleet now has one wire-logging shape and a gate that fails on the
  second.** A refactor that re-converges `_write` on the template can no longer
  silently revert the redaction, which is precisely what happened to thirteen
  servers in August.
- **The template is gated, not surveyed, and that asymmetry with
  `test_read_loop.py` is deliberate** — the two suites read the same Markdown
  file at different severities, on evidence rather than on style. If a third
  suite reads it, it should say which precedent it follows and why.
- **Open thread, recorded rather than solved:** the MCP registration still passes
  `--log-file`, so a fresh log file is created on every restart — four `mcp-gdc`
  processes carry that flag on this box today. After this change its contents are
  structure only, but the file is still created world-readable by
  `FileHandler`'s default mode, in a world-writable directory. Nobody has decided
  whether that matters, and this page does not decide it either.
