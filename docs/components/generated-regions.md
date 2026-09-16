---
name: generated-regions
type: component
status: active
title: Generated regions — how the MCP fleet shares plumbing without importing it
description: The amalgamate generator, its five canonical sources, the four rules that decide what may be a shared block, and the two registers of deliberate exclusion.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_concurrency.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_logging.py
  - Scripts/_mcp_lsp.py
  - Scripts/_mcp_paging.py
  - tests/test_generated_region.py
verified:
  commit: db63229
  date: 2026-09-16
links:
  - scripts
  - tests
  - 0009-the-first-reader-is-a-cold-model
  - 0010-a-handler-failure-must-reach-iserror
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
---

# Generated regions

The fifteen MCP servers in [[scripts]] share their plumbing by **generation, not
import**: a canonical function is pasted into each server between two comment
markers, and a generator re-renders it on demand. There is no runtime dependency
between servers, no shared package, and no import that could carry a helper from
one file to another.

**Scope note.** This page is about the *MCP fleet's* generated regions. The repo
contains a second, unrelated marker-delimited generation mechanism — the
checkpoint file's table of contents, whose one-writer design is recorded in
[[0009-the-first-reader-is-a-cold-model]]. The two share a pattern and nothing
else.

## Why generation rather than an import

Every server is a self-contained single file, and the rejection of a shared
import is argued identically in **every** canonical source
`Scripts/amalgamate.py:CANONICAL_NAMES`, in the suite
`tests/test_generated_region.py`, and in `Scripts/MCP_SKELETON.md` and
`project-forge.yaml`. Three reasons: an import would write
`Scripts/__pycache__` into *a tree every suite that snapshots bytecode asserts
stays empty*; it would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds; and it would move the helpers out of the
module attributes the footprint suite reaches for.

That middle clause is quoted rather than paraphrased, because its wording is
itself the decision. It names the **property** instead of tallying the suites,
and it is byte-identical at every site, so the family stays greppable and a
newly added suite cannot stale it. The tally it replaced said "four", which was
right only under an unstated reading of which check counts; the suites that
assert emptiness *absolutely* are five, and the ones that assert only a delta
are more — both sets open, both in [[tests]].

The cost is accepted openly — duplication is the *mechanism*, and the generator
plus its gate are what keep the copies from diverging.

## The mechanism

A region is a pair of comment markers, and the closing marker carries a truncated
digest of what sits between them `Scripts/amalgamate.py`. The opening marker
names a canonical source and one or more block names; the digest is a 12-character
SHA-256 over the **emitted body alone**, markers excluded `Scripts/amalgamate.py:body_hash`.

Markers are located with `tokenize` and only `COMMENT` tokens count, so a marker
quoted inside a docstring or a string literal is inert — the generator's own
docstring contains one, and the suite uses that as a live negative control.

A region is in one of three states `Scripts/amalgamate.py:Region`:

- **ok** — the body is byte-identical to a fresh render *and* the recorded digest
  matches it. `--check` requires both; either alone is not enough.
- **hand-edited** — a digest was recorded and the body no longer hashes to it.
  This is **refused, never silently overwritten**, unless forced. The file gets
  exactly one writer.
- **stale** — everything else, including an empty body and a never-written digest.

A `BEGIN` without an `END` is a hard error rather than a skip, because a region
that quietly stops being maintained is the whole failure the mechanism exists to
prevent `Scripts/amalgamate.py`.

Measured at the last edit to this page: **111 live regions across the 15 servers,
emitting 149 block instances from 22 canonical blocks** — a single region may
name several blocks, and that is the whole of the gap between the two counts.
Thirty-eight regions name two blocks each and every other names one: fourteen
`_result, _error`, seven `_json_error_window, _ensure_dict`, five
`DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars`, and four each of
`uri_to_path, path_to_uri`, `_request, _notify` and `_abs_uri, _abs_path`.
`_json_error_window` and `_configure_logging` are in
every server; the `_result` / `_error` pair is in fourteen of fifteen,
`mcp-webfetch.py` the one holdout.

Those three numbers are measured at each edit rather than incremented, and the
reason is the state they were just found in: the first two had read `70` and `84`
since before the logging source existed, and the third read `11` against a table
that summed to thirteen further down this same page.

## Five canonical sources, and why five

The registry is a hand-written tuple, not a glob `Scripts/amalgamate.py:CANONICAL_NAMES`,
because a glob would let an unrelated file become a generation source by merely
existing — and the marker naming it would look exactly as legitimate as the ones
that belong. `Scripts/_mcp_smoke_test.py` is the standing example: an `_mcp_*.py`
file that is deliberately not a source.

`Scripts/_mcp_concurrency.py` is the other side of that coin — the case where the
deliberate edit was actually made, and the only source so far added for a constant
rather than for code. It holds one line, `MAX_INFLIGHT_REQUESTS = 8`, which nine
live servers had each written out: the fleet's widest-shared constant. It got a
domain of its own because none of the four existing ones could hold it without
becoming the shelf each of them is written not to be — JSON-RPC envelopes,
logging configuration, LSP framing and output paging are four questions, and "how
many handlers run at once" is a fifth. Filing it under the nearest of them would
have made the marker's source field decorative for every block in that file,
which is the one property the multi-source design exists to protect.

What that source deliberately does **not** take is the concurrency *decision*.
[[0008-a-serialized-read-loop-looks-like-a-dead-server]] records that the decision
was audited per server rather than copied, and the shared block is the number
those audits agreed on, not the agreeing. A server needing a different one keeps
its own copy and says why, exactly as the output ceiling works — at which point
the hand-copy census names it rather than hiding it.

Each source is **a domain, not a shelf**, and the JSON source has been narrowed
twice — framing left for the LSP source, row accounting for the paging source —
with both departures asserted as departures in the suite. The rule itself, the
naming complaint that measurement redirected at the container, and the test that
decides when a sixth source is warranted are
[[0014-a-canonical-source-is-a-domain]].

| Source | Blocks | Domain |
|---|---|---|
| `Scripts/_mcp_concurrency.py` | 1 | how many tool calls a server runs at once |
| `Scripts/_mcp_json.py` | 6 | JSON-RPC envelopes, wire-value coercion, JSON error reporting |
| `Scripts/_mcp_logging.py` | 1 | how a server CONFIGURES logging — level, sink, file mode |
| `Scripts/_mcp_lsp.py` | 7 | how the LSP wire is spoken — `Content-Length` framing for a message, the `file://` DocumentUri for a path, the client-side hops that put a message on that wire, and the two spellings of a resolved path |
| `Scripts/_mcp_paging.py` | 7 | how much of a result a caller gets, and how it is told where the rest is |

Twenty-two blocks across five sources, which the suite asserts as a disjointness
check rather than a count. The paging row read `5` until the edit that added the
logging row: the number was left behind when `_max_answer_chars` was lifted, and
a stale figure sitting beside a new row is worse than one sitting alone. The
four moves since are genuine arrivals rather than corrections — `_ensure_dict`
in the JSON row, `DEFAULT_MAX_CHARS` in the paging one, and the DocumentUri pair
then the client half in the LSP one.

`DEFAULT_MAX_CHARS` is worth naming here because of the state its three hosts
were found in. `mcp-git.py`, `mcp-inspect.py` and `mcp-wiki.py` each wrote
`100_000` — ADR 0013's verbatim-artefact class — and each additionally asserted
*in prose* that its number and spelling matched the other two. That is agreement
claimed on disk in triplicate, and it is the costly form: correcting one of the
three leaves two comments lying about it. The constant is generated now and the
cross-references are gone; what stayed above each marker is the only
host-specific half, why that server is in the class at all. None of the three
takes the `DEFAULT_MAX_ANSWER_CHARS, _max_answer_chars` pair, and that is not an
oversight — the pair renders the reader together with its own `24000`, so taking
the marker would take the value.

The logging source is the newest and the only one whose domain is defined by
what it EXCLUDES. Configuring logging is not the same question as what gets
logged: the wire log is a security invariant decided in
[[0011-a-truncated-payload-carries-the-first-cookie]] and gated by
`tests/test_wire_log.py`, and that ADR already rejected lifting `_write` into a
region on a ground that still holds — `host_provides` offers only the host's
module-level imports, `log` is a module-level *assignment*, so any block reading
it is refused by name. `_configure_logging` clears that bar by never reading
`log`: it is a module-level `def` whose free names are `logging`, `os` and `sys`.
The logger object itself stays hand-written because its NAME is the one thing
that differs across the fifteen copies, and the `--debug` / `--log-file`
declarations stay hand-written because `mcp-webfetch`'s `-v` / `--verbose`
aliases are a feature, not drift.

The source name on a marker **selects the block map**: a name is resolved against
that source and no other, and an unknown source is refused by name with the known
ones listed `Scripts/amalgamate.py:audit_text`.

## What may be a block — four constraints

**A function, a class, or a single-target constant.** `load_blocks_text` walks a
source's `tree.body` and takes `FunctionDef`, `AsyncFunctionDef`, `ClassDef` —
and, since the constants were lifted, an `ast.Assign` whose one target is a bare
`ast.Name` `Scripts/amalgamate.py:load_blocks_text`. Every other assignment shape
is turned down, each on its own grounds `Scripts/amalgamate.py:assign_name`:
`A = B = 1` and `A, B = f()` bind several names from one indivisible slice, so
the region would define a name its marker never mentions; `x.attr = 1` and
`x[0] = 1` bind no name to key the map on. **`X += 1` is the load-bearing
refusal**, and it is structural rather than stylistic — its target carries a
`Store` context, so `free_names` *binds* `X` and reports no requirement at all,
while the statement cannot run unless the host already defines `X`. A block whose
precondition is invisible to the contract check is the exact failure that check
exists to catch, so the shape is refused rather than handed to a check that
cannot see it. `X: int = 1` is out on demand rather than on principle: no queued
block is one, and admitting the form would also admit a bare `X: int`, which
binds nothing at run time and fails at the host's first read. Both arms — what is
taken and what is turned down — are pinned by `single-name-assign-only`
`tests/test_generated_region.py:group_control`.

An unextractable shape is **skipped, not raised on**, and that follows from the
loader having two callers. It is not only the canonical-source reader: the
hand-copy census points it at every server in the fleet
`tests/test_generated_region.py:group_gate`, so raising would turn an ordinary
module-level statement in any server into a traceback naming the wrong subject
entirely. Nothing is lost to the skip, because a name only enters the system by
being written on a marker, and an unknown one is already refused by name with the
source it was asked for `Scripts/amalgamate.py:render`.

A method inside a class still cannot be a block — the walk is over `tree.body`
and nothing else — which is why every block that lands inside a class body is
written at module top level in its canonical source: `_result` and `_error` in
the JSON source, and the four client-side methods in the LSP source, whose `self`
is an ordinary first parameter the generator has nothing to check about
`Scripts/_mcp_lsp.py`.

**The host must already import every free name.** `free_names` over-reports
deliberately, and it catches two holes that were once live: a block calling
another canonical block, and a block whose *annotation* needs an import — an
annotation is evaluated at def time, so a host missing the name dies at startup
while the block looks fine where it is written. The host half,
`host_provides`, returns **module-level import aliases only** — not defs, not
assignments. A free name the host *defines* is therefore still a refusal, and the
region is rejected by name at END-marker time `Scripts/amalgamate.py:host_provides`.

The remedy is not to import the name but to **co-list it on the same marker**:
`free_names` runs over the whole rendered region, so a block defined beside its
caller is *bound* rather than free `Scripts/amalgamate.py:audit_text`. That is
why `_max_answer_chars` travels with its constant and `_ensure_dict` travels with
`_json_error_window`, dependency first in both. The second pairing is the
instructive one, because it moved a cost into the test suite rather than the
generator: in `mcp-forge.py`, `mcp-git.py` and `mcp-inspect.py`, `_ensure_dict`
was the *only* caller of the window, so the moment it went inside the region
those three had no window call site outside one — and the liveness census, which
deliberately ignores calls inside a region, had to start asking whether the
**pair** is reached instead of whether one name is
`tests/test_generated_region.py:group_gate`. Co-listing is cheap at the marker
and is not free further out.

The remedy has a hard boundary, and it follows from where the blocks land rather
than from any check: co-listing works for a **module-level** region and cannot
work for an indented one. Every block in a region is emitted at the marker's own
column `Scripts/amalgamate.py:render`, so a dependency co-listed into a class
body arrives as a class member — where the block's own unqualified global lookup
would never find it. An indented region's free names must therefore be satisfied
by the host's imports alone, and a block whose dependency is a module-level `def`
has no remedy at that column at all.

Admitting constants needed nothing added here, and that is a property of the walk
rather than luck: `ast.walk` descends the whole tree and every binder is matched
by node type, so a module-level statement is analysed on the same terms as a
`def` body `Scripts/amalgamate.py:free_names`. `_FENCE_LINE_RE` is the first
*constant* to spend the budget — it reads `re`, and carries that import
requirement to each of its three hosts. It is not the first block to carry one at
all: `_result`'s `msg_id: Any` has demanded `typing.Any` of fourteen hosts since
long before, which is the annotation hole named above. What is new is the route,
not the requirement — a plain read in an executable statement rather than an
annotation. The refusal used to fire only incidentally, because every live region
passes it; `free-name-refusal` now drives it directly, on a constant, against one
host that imports `re` and one that does not
`tests/test_generated_region.py:group_control`.

**Tab safety is decided per block, mechanically.** `block_is_tab_safe` requires
both that no line join happens while a bracket is open and that every leading
whitespace run is a whole multiple of four; anything unprovable is unsafe
`Scripts/amalgamate.py:block_is_tab_safe`. The host's style is read from its own
indent tokens, not from the marker's column. This **narrowed an earlier per-file
rule** that refused tab hosts outright and cost one server three hand copies that
were byte-identical modulo the indent character.

Neither the unsafe set nor the tab hosts are tallied here, because the suite
measures both on every run: `tab-safety-real-blocks` asserts the unsafe set is
exactly `_rows_note` over the real canonical text, and `host-indent-from-tokens`
that the tab-indented servers are exactly `mcp-forge.py` and `mcp-webfetch.py`
`tests/test_generated_region.py:group_tabs`. A block added to a source moves that
set or fails there. The refusal therefore bites in one place: `mcp-webfetch` is
the only tab host that carries `_rows_note` at all, and it keeps its own —
excluded twice over, tab-unsafe *and* body-diverged, so clearing the tab hazard
alone would not make it adoptable. Lifting the constants did not widen that
surface even though it grew the block count, because a constant has no leading
whitespace on any line and the tab conversion is a no-op for it.

The rule's larger effect is not the refusals it issues but the **code it shapes
before one is ever issued**. Both blocks extracted most recently had to be
rewritten on the way in: `_configure_logging` and `_ensure_dict` each built a
call across several physical lines in every hand copy, which is an implicit line
join, and each has a tab-indented host — so the verbatim text would have been
refused by name for `mcp-forge.py` and `mcp-webfetch.py` and quietly accepted
everywhere else. Both now fit one call per physical line, and both say so in
their canonical source, because a **reshape** and a **lift** make different
promises: a lift's first generated diff is marker lines only, and reading a
reshape as a botched lift is the misreading the declaration exists to prevent
`Scripts/_mcp_json.py`.

**A name must resolve against the source that was named.** Cross-source
resolution is refused, and the suite gates both halves — the refusal *and* a
positive case proving the right body was selected, because "refused" alone could
be satisfied by a merged namespace that got lucky.

## Two registers of deliberate exclusion

The rule is not "duplication is bad". Two distinct kinds of copy are left in
place on stated grounds, and they are counted differently.

**Hand copies of things that ARE blocks** are censused by the suite: it
intersects every server's top-level names with the canonical block names,
subtracts what regions cover, and reports the remainder as INFO rather than FAIL
— "this server keeps its own" is a legitimate answer, but an *undeclared* copy
cannot appear without landing on that line. Four survive, each declared with its
reason: a parameter-name-and-raise variant, an older signature, an allow-list
where the canonical is a deny-list, and one that is both tab-unsafe and a body
divergence.

**Things that are not blocks at all** are documented in `Scripts/MCP_SKELETON.md`
rather than censused, because the census cannot see them. `_tool_error` *cannot*
be a block — its free names include the host's own server class, and host
provision is imports-only. `_ErrorText` *could* be one — byte-identical copies,
empty free names — and deliberately is not, because blessing a second mechanism
as generated infrastructure would buy drift protection for a divergence; see
[[0010-a-handler-failure-must-reach-iserror]]. `_send` is the third entry and
fails on a third rule: it is byte-identical in all four LSP servers and squarely
inside the LSP source's domain — it is `encode_lsp_message`'s only caller — but
that is exactly what refuses it, since the name it needs is a module-level `def`
in every host rather than an import, and the co-listing remedy above cannot reach
an indented region `Scripts/MCP_SKELETON.md`.

`_resolve_aliases` is the fourth entry and the widest of them: a hand copy in all
ten hosts that define it, which makes it the fleet's widest hand-copied
non-generated function. It fails on the same rule `_tool_error` does — its free
names are each host's own alias tables, `PARAM_ALIASES` plus
`PARAM_ALIASES_BY_FUNC` in three of them, and those are module-level
*assignments*, while `host_provides` offers only the host's module-level imports
`Scripts/amalgamate.py:host_provides`. `Scripts/mcp-forge.py` sidesteps the
tables entirely by taking one as an argument
`Scripts/mcp-forge.py:_resolve_aliases`, and that is the tell: these are not
copies that drifted from one original but **ten shapes that never agreed**.
Measured at the last edit to this page: **eight distinct bodies over the ten
files**, only `Scripts/mcp-clangd.py`, `Scripts/mcp-cuda.py` and
`Scripts/mcp-tshark.py` byte-identical, and two of the ten tab-indented — the
same `mcp-forge.py` / `mcp-webfetch.py` pair `host-indent-from-tokens` pins
above. The rule all ten now implement — a collision is an error, not a
precedence question — is frozen in [[0015-ambiguity-is-the-defect]], and it is
gated behaviourally rather than structurally, by
`Scripts/_mcp_smoke_test.py:alias_collision_checks` driving all ten over live
JSON-RPC.

Which ten, though, is decided **textually**: that gate pairs its per-server probe
row against a search of the server's source for the resolver's *definition line*,
and asserts the two agree. A text search cannot tell a definition from a
quotation of one, so a server that defines no resolver can be made to look like a
host merely by writing that line into a docstring — measured, on the one server
that had reason to: `Scripts/mcp-git.py` reaches the same collision rule through
no resolver at all, its aliasing being structural rather than table-driven, and
explaining that in prose tripped the gate's own consistency row until the name
was spelled around rather than out `Scripts/mcp-git.py:_passthrough_args`. So the
ten is a census of a *spelling*, not of a mechanism, and the page-level claim it
supports is the narrower one: ten files write this function, while the contract
it implements is met by more — see [[scripts]].

That count is why the entry earns its space. ADR 0015 and section 7c of
`Scripts/MCP_SKELETON.md` both record **nine** bodies with only clangd and cuda
identical, and both were right when they were written: `Scripts/mcp-tshark.py`
held the fleet's last first-wins resolver, and the very commit that made
collisions an error is what converged it onto the clangd/cuda body. The ADR is
frozen at its decision and keeps its number; this page re-measures, on the same
grounds as the region counts above.

## The gate

`tests/test_generated_region.py` gates the mechanism in six groups: A the live
tree against the canonical sources, B the marker and hash contract, C the
negative controls, D hygiene, E what each shared block actually *does*, and F tab
safety. Exactly one case is informational — the hand-copy census — and every
other is a gated failure.

The case count is deliberately **not** repeated here. It is declared once, in the
suite table, and asserted on every run against what the suite actually recorded
`tests/run.py:SUITES`; a count that lives in prose and is checked by nobody is
precisely the defect that table exists to prevent.

Group E is not optional — the suite argues that a drift gate on its own would
only ever prove that every copy agrees on the same bug `Scripts/_mcp_paging.py`.
It loads each canonical source as a module and exercises every block that *has*
behaviour, which is why the two value constants sit outside it by design, and
`_FENCE_LINE_RE` outside it by omission — see Known gaps.

The format contract is spelled out **independently** of the generator rather than
imported from it: a test that imported the marker constants would sail through a
rename that orphaned every region already written into a server.

## Known gaps

- **`_FENCE_LINE_RE` has no behavioural case.** Group E exercises every block
  that *has* behaviour, and this one does: `re.M` is the whole of it. Drop the
  flag and the pattern still compiles and still reads right, while `findall`
  quietly returns at most one hit — the fence count comes out even and a reply
  cut mid-fence reaches the reader with the block still open
  `Scripts/_mcp_paging.py`. Group A pins the constant's *text* in all three
  hosts; nothing yet pins what it does. The other two constants are deliberately
  not in this position: `DEFAULT_MAX_ANSWER_CHARS` and `PAGE_LINE_RESERVE` have
  no behaviour to exercise, and a case asserting `== 24000` against a literal
  typed into the suite would be the same number written twice.
- **A name that is present but unextractable is reported as absent.** The loader
  skips a shape it cannot take, so a marker naming `X` where the source writes
  `X += 1` is refused with "defines no top-level `X`"
  `Scripts/amalgamate.py:render`. That is true of the block map and misleading
  about the cause; the refusal cannot yet tell the two apart.
- **The census cannot see a hand copy that lives inside a class**, because the
  block loader walks module top level only. At least one server's `_result` and
  `_error` are exactly that: genuine hand copies, declared in
  `Scripts/MCP_SKELETON.md`, absent from the census's count of four.
