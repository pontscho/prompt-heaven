---
name: 0016-a-cell-may-not-forge-a-boundary
type: adr
status: active
title: A cell may not forge a column boundary
description: Decision to give the fleet one escape vocabulary across two table formats rather than copy the nearer precedent, to treat structural safety as a first-class answer rather than an exemption, and to require an escaping scheme be documented where the model reads it — with the renderer census that reframed one bug as a divergence and then had to be corrected for the same scope error one level up, the six alternatives rejected, the instrument bug the gate found in its own oracle, the renderer that was safe only because its parser was lossy, and the deviation left declared rather than fixed.
sources:
  - Scripts/mcp-tshark.py:_md_cell
  - Scripts/mcp-postgres.py:_escape_cell
  - ClaudeCode/skills/checkpoint/scripts/checkpoint.py:render_toc
  - tests/test_table_cells.py
verified:
  commit: 6d887aa
  date: 2026-09-15
links:
  - scripts
  - tests
  - 0013-the-ceiling-is-a-payload-class
---

# ADR 0016: A cell may not forge a column boundary

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the declared table in `tests/test_table_cells.py`.

## Context — one bug that turned out to be a map

`Scripts/mcp-tshark.py` rendered a markdown table and passed every value through
untouched. The carried-forward note called that a missing escape.

The failure mode is worse than the note suggests, and the difference is the
whole reason this became a decision. A pipe inside a cell does not produce a
**broken cell**, which somebody would eventually notice. It produces an extra
**column**. The row stops matching its header, every later value shifts one
place left, and the output remains perfectly valid markdown — so the model reads
one field's value under another field's name, and nothing anywhere reports a
problem. A newline is worse still: it ends the row outright.

Three things the census established that the note did not contain.

**The defect is reachable without any exotic input.** The cheapest reproducer
needs no capture file and no hostile packet: the session table echoes back the
caller's own BPF filter, and the canonical `pcap-filter(7)` expression for
SYN-or-ACK is `tcp[tcpflags] & (tcp-syn|tcp-ack) != 0`. Start a capture with it,
list the sessions, and the filter you typed splits its own row. The packet table
is a second, independent direction — fields arrive from `-T fields` under `-E
quote=n`, raw and unquoted, and `_ws.col.Info` carries the verbatim HTTP request
line while `dns.qry.name` and the TLS server name carry whatever the sender
chose. A capture is untrusted input by construction.

**There are five renderers, not one, and they were never one convention.** Two —
`Scripts/mcp-jenkins.py` and `Scripts/mcp-inspect.py` — delimit on whitespace
inside a code fence, where a pipe is an ordinary character. One —
`Scripts/mcp-postgres.py` — was already reversible. One — the Jira CLI — escapes
the pipe but not the backslash and documents none of it. tshark was the only one
with no escaping at all.

**tshark was strictly worse than a mutant that already exists in this repo as a
negative control.** `tests/test_jira_cli.py` plants a `naive_escaper` that
"collapses newlines and leaves the pipe alone -- the live defect" and asserts its
oracle rejects it. tshark did not even collapse the newline.

## Decision

**A rendered cell must not be able to forge a column boundary**, and a renderer
satisfies that by exactly one of two routes:

- **ESCAPING** — it neutralises its own delimiter inside a cell, reversibly,
  **and its server documents the scheme where the model reads it.**
- **STRUCTURE** — the delimiter cannot be a column boundary in that renderer's
  output at all. **This is a first-class answer, not an exemption**, and the
  declared row must state why it holds rather than assert that it does.

Three sub-decisions carry most of the weight.

**One vocabulary, two formats.** tshark took `Scripts/mcp-postgres.py`'s
scheme — `\\`, `\|`, then `\n`/`\r`/`\t` — rather than the Jira CLI's
one-liner, even though jira solves the nearer problem (a GFM table) and postgres
solves a different one (a custom delimited format). The order is load-bearing:
the escape character is escaped **first**, because escaping the delimiter first
lets the backslash pass re-escape what it just wrote, turning a literal pipe into
a backslash followed by a real separator. That is the hole jira still has.

**The scheme is documented on the wire.** An escaping scheme the reader does not
know about is a different bug, not a fix. `Scripts/mcp-postgres.py` had always
said so in its `tools/list` description, and it teaches the decode ORDER rather
than only the character set. tshark now pays the same tax for the same reason —
about a hundred tokens on every request, reported by `tests/test_mcp_footprint.py`
as INFO against no budget.

**The gate was written first and run red**, on tshark alone, failing three
independent measurements: no escaper, a rendered row that gained a column, and a
description that never mentioned escaping. Every other renderer stayed green in
the same run.

## Alternatives Evaluated

### Option 1 — Copy the Jira CLI's escaper verbatim
- **Pros:** it solves exactly this problem, for exactly this output format, in
  one line, and it is already gated by a suite in this repo.
- **Cons:** it does not escape the escape character, so a value containing the
  two literal characters `\|` renders as a backslash followed by a real column
  separator. Copying it would have propagated a live hole into a second server
  and made it the fleet's shape by weight of numbers. The one-line version is
  also lossy by construction, and jira's cells are human prose while tshark's
  are `_ws.col.Info` and a TLS server name — a backslash before a pipe is a
  stretch in a ticket title and routine in a packet.

### Option 2 — Escape the pipe only, and leave newlines and backslashes alone
- **Pros:** the minimum that fixes the reported symptom; smallest diff; no
  vocabulary to document.
- **Cons:** the newline is the worse half of the same defect — it ends the row
  rather than extending it — and leaving it is what made tshark worse than a
  planted negative control in the first place. A fix that leaves the second half
  of a two-part defect is how the first half comes back.

### Option 3 — Collapse newlines to a space rather than encoding them
- **Pros:** jira's choice, argued in jira's own docstring, and it renders more
  cleanly than a literal `\n` in a cell.
- **Cons:** it is lossy in a way the reader cannot detect: `"a\nb"` and `"a b"`
  become the same string, so a model cannot tell a wrapped value from a spaced
  one. Encoding costs two characters and makes the whole scheme reversible,
  which is what lets the gate assert `decode(encode(x)) == x` instead of
  asserting a list of spellings.

### Option 4 — Drop the padding instead, as `Scripts/mcp-postgres.py` argues
- **Pros:** postgres's own comment is the strongest argument available:
  alignment serves a human eye scanning a column, a model parses on the
  delimiter, and every row pays for the single longest value in its column. It
  would have made the table both safer and cheaper.
- **Cons:** it does not fix the defect — an unescaped pipe forges a boundary in
  an unpadded table exactly as it does in a padded one — and it changes the
  output shape of a live server far beyond the reported problem. Recorded as an
  open question rather than folded in: **tshark still pays for alignment its
  reader does not use.**

### Option 5 — Fix the Jira CLI in the same change
- **Pros:** three escapers, one vocabulary, no exceptions, and jira's hole is
  real.
- **Cons:** jira's lossiness is a **decision with an argument attached** — its
  docstring explains the newline collapse as deliberate — and its suite gates
  the current behaviour. Overturning an argued, gated decision belongs in a
  change that argues against it, not in one that happens to be nearby. It is
  declared as a deviation in the gate's own table instead, with both facts
  named, so the row trips if jira is ever made reversible or ever gets worse.

### Option 6 — Exempt the whitespace renderers instead of declaring them
- **Pros:** jenkins and inspect have no pipe problem, and a table with two
  fewer rows is a table with two fewer things to maintain.
- **Cons:** an exemption records that somebody decided not to look; a declared
  row records what was measured and why it holds. The distinction paid for
  itself immediately — the roster group now refuses a row that declares
  STRUCTURE for a renderer whose delimiter the analyser finds to be the
  escaping-relevant one, so a failing renderer cannot be "fixed" by
  re-declaring it.

### Chosen — one vocabulary, two routes, and a documentation clause
Because the interesting property was never the escape characters. It was that a
corrupted table is **indistinguishable from a correct one** at every layer below
the model reading it: valid markdown, no exception, no flag, no log line. When
nothing downstream can detect the failure, the only place to stop it is the
renderer, and the only way to know the renderer still stops it is a gate that
decodes what it encoded.

## Consequences

- **The fleet has one escape vocabulary and two table formats**, and the symbol
  spelling deliberately does not follow. tshark's escaper is `_md_cell`, not
  postgres's `_escape_cell`, because it sits beside `_markdown_table` and the
  fleet's other markdown helpers already carry the `_md_` prefix. The suite
  judges the scheme, never the symbol.

- **`Scripts/mcp-postgres.py`'s escaper has a test for the first time.** It was
  the fleet's only reversible cell codec and nothing anywhere asserted it. The
  gate written for tshark covers it at no extra cost, which is an argument for
  writing a fleet gate rather than a fix.

- **The gate found an instrument bug in its own oracle, and the correction is
  the more useful artefact.** Counting pipes a backslash does not escape is
  right in a pipe-delimited world and wrong the moment the delimiter is
  whitespace, where a literal pipe resting in a jenkins cell reads as a
  separator and fails a row for being exactly what its row declares. The
  structure renderers are judged on whether they emit a pipe they were **not
  handed**, which is the only thing "the pipe is not my delimiter" actually
  claims.

- **The Jira CLI documents its escaping to nobody, and that is now recorded.**
  Its `md_escape` docstring is excellent and lives in a script the model never
  imports; the model reads stdout, and stdout never says that a `\|` in a cell
  is an escaped literal. It is mitigated only because `\|` is the GFM-standard
  spelling a markdown-literate reader guesses correctly — which is precisely why
  postgres spells out `\NULL` and `\\`, since those are unguessable.

- **Two open questions are left open rather than answered quietly:** tshark's
  padding (Option 4), and whether jenkins' two-space delimiter has the same
  defect wearing different clothes — a cell containing two consecutive spaces is
  ambiguous against its separator, which is not the pipe question and was not
  measured here.

## Corrections — three, all of scope rather than substance

Appended rather than edited, on the standing rule that a frozen argument is
dated by a later measurement and not rewritten to match it. None of the three
changes the decision; all three were found by reading this page against the tree
after it was accepted.

**"There are five renderers, not one" was a `Scripts/`-only count stated as a
tree-wide one.** There are SIX, and the sixth is
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:render_toc` — a padded,
pipe-delimited GFM table. The gate's own backstop could not see it, because its
sweep root was `Scripts/` while its roster already reached outside by hand for
the Jira CLI; the sweep now spans both roots and returns repo-relative paths, so
two files with the same basename cannot vouch for each other. **The census that
reframed this decision had the same scope error as the note it replaced, one
level up.**

**That sixth renderer was safe, and the reason is the interesting part.** No
pipe could reach a cell, because `toc_rows` split the block header on the pipe
and took the field between two of them — so a branch named `feat|x` arrived as
`feat`. **The table was safe only because the parser was lossy**, in the one
artefact whose whole job is telling a cold reader where a block starts. Both
halves were fixed together, and a four-way control now asserts why they could
not be split: bounding the split alone is not a partial fix but a REGRESSION —
with the branch surviving the parse and the renderer still raw, a reader
splitting on the unescaped pipe recovers `feat` out of a forged column, so that
combination fails both assertions where the lossy original failed only one.

**"It teaches the decode ORDER" names a different order than the sentence before
it implies.** The preceding clause argues the ENCODE order — the escape
character first — and that rule lives in a source comment in
`Scripts/mcp-postgres.py`, not in anything on the wire. What its description
publishes is the NULL-versus-unescape order, which belongs to the layer the gate
declares out of scope. `Scripts/mcp-tshark.py`'s new description teaches the
character set and the separator rule and no order at all, so "pays the same tax
for the same reason" is true of the tax and loose about the reason.

One blind spot this page should have carried from the gate and did not: the
STRUCTURE rows are measured on the module OWNING a fence helper, not on its call
sites applying one. Call sites are not surveyed.

And one process note, recorded because the alternative is letting a green run
stand in for evidence it never produced: **the sixth renderer's fix was never
observed red.** The gate extension and the fix landed concurrently, so the first
run of the new rows was already green. The four-way pairing control replaces
that observation and is stronger in one respect — it asserts which combinations
are unsafe rather than only that today's is safe — but it is a replacement, not
the thing itself.
