---
name: 0013-the-ceiling-is-a-payload-class
type: adr
status: active
title: The ceiling is a payload class, not a number
description: Decision to ratify the fleet's three output ceilings as three declared payload classes rather than collapse them to one value, with the census that dissolved the "three anonymous tiers" premise, the same-name-different-meaning divergence no value census would have found, the five alternatives rejected, and the retroactive ratification of two numbers nobody ever argued for.
sources:
  - Scripts/_mcp_paging.py
  - tests/test_mcp_footprint.py
verified:
  commit: abb49af
  date: 2026-09-14
links:
  - scripts
  - tests
  - generated-regions
  - 0012-the-transport-tier-diverged
---

# ADR 0013: The ceiling is a payload class, not a number

**Status:** accepted. The decision is the three CLASSES; the three numbers are
incumbents ratified retroactively, and this page is careful about which of those
two things it claims.

## Context — a rename-shaped task that was not a rename

Four session handoffs carried the same item: the fleet has three output
ceilings — `24000`, `100_000`, `500_000` — they diverge, nobody said why, unify
them. The last handoff went further and named the shape of the answer: *"this is
a DECISION deserving an ADR, not a rename"*, and it gave one instruction —
**ask why 100_000 and 500_000 were chosen rather than inventing a reason.**

That instruction was right, and it was aimed at the wrong half of the premise.

## The census

Taken over the tree at `abb49af`.

**`24000` was never anonymous.** Its reasoning sits in `Scripts/_mcp_paging.py`
above the constant (~6k tokens at ~4 chars/token, "a reply ONE call may spend,
not a reply that eats the session"), it is restated in each of the six hosts'
own terms above their BEGIN markers — purity's reaches for a measured
511,617-character call, jenkins names the console log, lldb says long output is
its NORMAL output — it is pinned by `tests/test_mcp_footprint.py` as
`V1_DEFAULT`, and the commit that took it fleet-wide, `bc974e9`, argues for it
in the body.

**Two of the three deviations were not anonymous either.** `mcp-wiki.py` and
`mcp-tshark.py` both carry a justification above their constant. The handoff did
not know because those two comments were written *the session before it*, by the
session that named the problem — so the note reported the state of the tree as
the author had found it, not as he had left it.

**The real gap was two files.** `Scripts/mcp-git.py` and `Scripts/mcp-inspect.py`
carried a bare constant with no comment at all, violating a rule
`Scripts/_mcp_paging.py` had been stating the whole time: *a server that
genuinely needs a different ceiling keeps its own copy and says why.* Four
servers deviated; two said why. **A rule with a measured 50% compliance rate is
not a rule**, and the second clause was gated nowhere.

## The finding a value census could not have produced

`mcp-git.py` and `mcp-wiki.py` carried the same constant name and the same
number, and they were **not the same ceiling.**

wiki's `DEFAULT_MAX_CHARS` bounded the whole reply. git's bounded the captured
subprocess stdout *only* — the envelope and the entirety of stderr were appended
afterwards, unmeasured, so a reply could exceed the number by however much
stderr cared to be. A ceiling named after the answer bounded a part of it.

Two things follow, and the second is why this page exists rather than a commit
message:

1. Collapsing the three values would have made git's and wiki's ceilings agree
   on a number while still meaning different things. The divergence that
   mattered was invisible to the axis the handoff was measuring.
2. Sweeping for the *shape* rather than the value turned up two more defects in
   the same handler, neither of them looked for: the ceiling was read straight
   off the params dict with **no type coercion at all** — a string
   `max_answer_chars` reached `max_chars > 0` and raised `TypeError`, which is
   why the fleet-wide `OverflowError` sweep of the preceding sessions never
   touched this file, there being no `int()` in it to widen — and the camelCase
   `maxAnswerChars`, which the argv loop deliberately normalises so it is
   stripped as meta rather than sent to git as a bogus flag, was then read from
   the raw dict and **silently discarded**, so a caller who spelled it that way
   was accepted and ignored.

## Decision

**A server does not pick a ceiling. It declares which class of payload its
handlers return, and the number follows.** Three classes are ratified.

### 24000 — the server COMPOSED the reply

It chose the rows, rendered the table, summarised the upstream answer. Narrowing
the query is therefore a cheap and **lossless** move available to the caller: a
cut costs one better-aimed call, and the caller can tell from the reply how to
aim it. This is the fleet default and needs no argument per server.

Hosts: purity, lldb, context7, postgres, jenkins, webfetch.

### 100_000 — the payload is a VERBATIM artefact the server did not compose

The recovery the first class relies on is not available here, and for three
different reasons — which is the evidence that this is one class and not three
coincidences:

- **Lossy** (`mcp-git`): a diff cut in half is not a smaller diff, it is a diff
  that lies about the file, and nothing in the reply tells the caller which half
  arrived.
- **Non-idempotent** (`mcp-inspect`): a process table cut at 24000 chars cannot
  be resumed, only retaken — against a machine that has moved. The second `ps`
  is a different measurement, not the remainder of the first.
- **Redundant** (`mcp-wiki`): the payload is a document the caller named and the
  model is already reading; a cut buys a round trip to finish it.

### 500_000 — the payload is a SEQUENCE whose meaning is in the row COUNT

A packet dissection is a wide fixed-width table where a cut after twenty packets
answers a question nobody asked.

Host: tshark.

**This is the weakest of the three and this page says so rather than dressing it
up.** A code search's hits are independent — twenty of them are twenty answers —
while a capture's rows are ordered and the interesting packet is routinely not
in the first twenty. That distinction is real, but it is thinner than the two
above, and it is the one a future reader should attack first. What would settle
it: a measurement of where in a real capture the answering packet actually
falls. Nobody has taken it.

## What a class does NOT license

The class bounds the **default** and nothing else.

`tests/test_mcp_footprint.py` judges conformance on three criteria bundled into
one verdict — the wire parameter name, the default, and the closing truncation
line. **The model licenses a deviation on the default alone.** A deviating
server is expected to conform on the other two, and `mcp-git` was brought to the
fleet's closing line as part of this change for exactly that reason; its old
notice said only that stdout had been cut, which told the caller nothing about
what to do next.

`mcp-tshark`'s `max_output_chars` is the fleet's one divergent parameter
spelling. It is **not** a fourth class and it is not ratified here — it is a
recorded open thread, deliberately not folded in, because renaming it is a
caller-visible change with its own blast radius (`mcp-webfetch` and `mcp-wiki`
both accept that spelling as an alias today).

## Alternatives rejected

1. **One ceiling: 24000 everywhere.** Refuted by three reasons already written
   in the code it would overrule, and it cuts git, inspect and wiki 4x and
   tshark 20x on servers under live use. Uniformity here buys a table that reads
   tidily and answers worse.
2. **Keep the tiers, document each deviation per file, declare no model.** This
   is precisely what `_mcp_paging.py` already demanded, and the measurement is
   what it produced: two of four. A reason invented independently four times
   yields four incompatible reasons, and the fifth server's author has nothing
   to read. The model is what makes the next answer a *reading* rather than an
   invention.
3. **Choose the tier at runtime from the payload's shape.** The tier is a
   property of the HANDLER, known when it is written. Computing it per reply
   gives the caller a ceiling it cannot predict and cannot coherently override.
4. **Render all three constants in the shared generated block and let each host
   select one.** Refused on a constraint measured in the preceding session:
   `host_provides` offers a region only the host's module-level imports, and a
   marker names exactly one canonical source — which is why the block already
   pairs `DEFAULT_MAX_ANSWER_CHARS` with its reader in a single marker (see
   [[generated-regions]]). A host would render three constants to use one, and
   the reader would still have to choose between them at author time. The
   deviating servers hand-write the reader, and that duplication is the price,
   named here rather than hidden — the same trade [[0012-the-transport-tier-diverged]]
   records for the transport tier.
5. **Invent a rationale for 100_000.** Rejected explicitly; see below.

## The provenance of the two numbers, stated plainly

`100_000` and `500_000` were never argued for anywhere. The commits that
introduced them — `092644a` for `mcp-git`, `cc09804` for `mcp-inspect`,
`ed7c0f2` for `mcp-tshark` — do not mention the numbers at all, no `docs/` page
or ADR referenced them before this one, and the engineer who directed all three
confirms there was no reason to recover.

So the ratification is **class-first and retroactive**: the class is the
decision, and the number is the incumbent the class happens to fit. Nothing here
claims either value was measured, tuned, or derived. Anyone proposing to change
one is arguing with an incumbent, not with a measurement — which is a lower bar
than this page would like to offer and an honest statement of what is known.

## The gate

`ceiling-deviation-says-why` in `tests/test_mcp_footprint.py` group C asserts the
second clause of the rule: a reply-ceiling constant whose value is not the fleet
default carries a contiguous comment block directly above it, and that comment
names the default it departs from.

Three decisions inside it are worth keeping:

- **It is the one finding about a server that file gates.** That file declares
  every server finding INFO, and gives its reason: gating "would report a
  decision that has not been made yet as a regression". The precondition it
  names is a decision. This page is that decision, and satisfying it cost two
  comments rather than the seven server fixes that blocked the idea before.
- **Naming the default is the test, not a length floor.** A length floor is
  arbitrary; a justification that never mentions what it deviates from is not a
  justification of the deviation. Both servers that already complied passed it
  as written, which is the check that the rule was fitted to the evidence rather
  than to the two offenders.
- **It has its own negative control.** After the two fixes the live tree has no
  offender, and a checker that silently matches nothing is indistinguishable
  from a clean tree. Three planted defects must be flagged — including the exact
  shape `mcp-git` carried, a section banner separated from the constant by a
  blank line — and four baits must be ignored.

## What this page does not settle

- **It asserts that a reason was written down, never that the reason is good.**
  The gate is satisfiable by boilerplate that mentions 24000. That is the
  accepted floor: the next reader finds *something* at the point of decision.
- **Two registered servers have no reply ceiling at all.** `mcp-forge` bounds
  only a subprocess's captured bytes; `mcp-gdc`'s bounds are function-local
  literals. The class model says nothing about them, the gate cannot see them
  because they carry no deviating constant, and `bc974e9`'s body claim that "no
  server in the fleet is uncapped" is **false as of HEAD**. Separate work.
- **The divergent wire spelling** (`max_output_chars`) is untouched, above.
- **Line-boundary truncation** is a runtime property. `mcp-git`'s cut lands on
  the pre-fence text, which makes an unbalanced fence impossible whatever the
  payload holds, but it is not a line-boundary cut and nothing here claims one.

## Addendum (2026-09-28): forge and gdc join the verbatim class

Two registered servers had no reply ceiling at all, so this page's class model did not reach them, and the body of `bc974e9` ("No server in the fleet is uncapped.") was false. mcp-forge carried only `MAX_OUTPUT_BYTES`, which bounds one command's captured bytes, and mcp-gdc only function-local limits (`_MAX_HTML`, the snapshot line limit, the response-body limit). The footprint instrument read both as const-only, and the deviation rule could judge only a constant that existed, so it skipped them. Closed as roadmap item R-0009.

Both now declare the verbatim class, 100_000, on the non-idempotent reason. Asking a build again means re-running it: minutes, and a new measurement rather than the rest of the first. A live page moves between calls, so a second DevTools snapshot is a new measurement too. forge keeps the tail for `test` and the head otherwise, with fence repair; gdc keeps the head without fence repair, because gdc fences nothing itself and a repair fence would invent page content. No new class and no new number.

The gate now fails a registered, non-inert server that declares no ceiling or declares a value outside the three classes, with a negative control that must flag the old forge and gdc shapes.

## Addendum (2026-09-29): tshark takes the fleet spelling; the old one stays an alias

This page left `mcp-tshark`'s `max_output_chars` as an open thread. It was the fleet's one divergent spelling of the per-call reply ceiling. Closed as roadmap item R-0008.

The canonical name is now `max_answer_chars`, as on every other server that takes a per-call ceiling. `analyze`, `statistics` and `follow_stream` all read it. `max_output_chars` stays accepted as a declared alias of it, next to `max_chars` and `max_len`, which used to point at the old name. A caller who learned the old spelling keeps working. That was the blast radius this page named, and an alias removes it. `mcp-webfetch` and `mcp-wiki` already accepted `max_output_chars` as an alias of `max_answer_chars`, so all three servers now read it the same way. The tools/list description advertises `max_chars/max_output_chars→max_answer_chars`.

Sending both spellings in one call is refused. This is not a new rule. It is [[0015-ambiguity-is-the-defect]] applied to one more alias row, and the collision message names `max_answer_chars` as the key both spellings set.

The rename covers the wire name only. The default stays 500_000, the sequence class above. The constant keeps its name, `DEFAULT_MAX_OUTPUT_CHARS`, because no caller sees it and the deviation gate's pattern already matches it. The closing truncation line is still tshark's own `**(truncated — showing first N chars of M)**`, not the fleet's line. That is the third criterion this page expects a deviating server to meet, and it is still unmet. It is separate work and was not folded in here.

The gate is `tshark_ceiling_param_checks` in the `smoke` suite, written first and run red. It has two layers. The first is offline and needs no tshark binary: both collision pairs must be refused on `max_answer_chars`, and the description must name both spellings. The second writes a five-frame pcap under `.claude/tmp` and cuts it at 64 characters, once with the new name and once with the old one. A control with neither name must come back uncut. The red run failed the three offline checks and the new-name cut. The old-name cut and the control passed. When tshark is absent, the second layer adds no check, so an unmeasured effect is never scored as a pass.

## Addendum (2026-09-29): Addendum (2026-09-29): tshark closes a cut with the fleet's line

The previous addendum left one criterion unmet. `mcp-tshark` still ended a cut reply with its own `**(truncated — showing first N chars of M)**`. It now ends with the fleet's line, `[truncated: kept <n> of <total> chars from the head; raise max_answer_chars or narrow the query]`. All three criteria of this page are now met: the wire name since R-0008, the closing line now, and the default, which stays 500_000 as the sequence class allows.

The shape changed with the wording. `_truncate` keeps the head, cuts on a line boundary where there is one, and reserves room for the closing line so the whole reply fits under the ceiling. If the ceiling is smaller than the closing line, the reply is the closing line alone. Every reply tshark cuts is a fenced block, so an open fence is closed after the count is taken. The fence pattern is generated in from `_mcp_paging.py` (`_FENCE_LINE_RE`). tshark now imports `re` so it can host that block.

The closing line itself is written by hand, as on every other host. No canonical source holds it. `_mcp_paging.py` calls each host's `_cap_text` and `_balance_fences` merges, not lifts, because the copies have diverged. The one place the spelling is written once is the `V1_TRUNC_*` constants in `tests/test_mcp_footprint.py`.

The gate is `tshark_ceiling_param_checks` in the `smoke` suite. It was extended first and run red. The two 64-character cuts, one per parameter spelling, must now end with the fleet's line. That line must name the uncut control reply's length as its total, and it must appear exactly once. The test does not type the wording. It reads it from the footprint suite's `V1_TRUNC_*` constants with `ast`. The red run failed both cuts on the old line (`**(truncated — showing first 64 chars of 717)**`). The control and the three offline checks passed.
