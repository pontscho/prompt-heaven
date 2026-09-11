---
name: 0010-a-handler-failure-must-reach-iserror
type: adr
status: active
title: A handler failure must reach the caller with isError set
description: Decision to make the tool-result error envelope a fleet-wide invariant with a gate written first and run red, plus the four alternatives rejected on the way and the gate's permanent blind spot.
sources:
  - Scripts/MCP_SKELETON.md
  - Scripts/_mcp_smoke_test.py:error_envelope_checks
verified:
  commit: f80dc90
  date: 2026-09-11
links:
  - scripts
  - tests
  - generated-regions
---

# ADR 0010: A handler failure must reach the caller with isError set

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is section 3a of `Scripts/MCP_SKELETON.md` and the
error-envelope section of [[scripts]].

## Context

The fleet is fifteen MCP servers that share their plumbing by **generation, not
import** — a canonical block is rendered into each host file, so there is no
runtime dependency between servers and no import that could carry a convention
from one to another. A convention therefore holds only where someone wrote it,
and drifts silently everywhere else.

`Scripts/MCP_SKELETON.md` asserted as an **invariant** that handlers already
route their failures through `_tool_error`. That assertion was false for six of
the fifteen. Three returned a raw failure string that the transport wrapped in a
success envelope; three returned an `{"error": ...}` dict that a markdown
renderer flattened into a string before the transport could see its shape. Two of
fifteen servers had any suite asserting the flag at all.

The caller-visible consequence is the whole problem: a tool result that says
"here is your answer" and a tool result that says "this failed, here is why" were
the same shape on the wire. A model reading the reply has to infer failure from
prose, and prose is exactly what an untrusted page, an upstream API, or a user's
own argument can control.

The deeper fact is that six servers had drifted for as long as they had existed
**without violating anything a test could see**. That is what made this a
decision rather than a bug fix.

## Decision

**A handler failure MUST reach the caller with `isError` set.** One invariant,
fleet-wide, across all fifteen servers including the three that are never
launched.

The invariant is gated by check 7, `error_envelope_checks`
`Scripts/_mcp_smoke_test.py:error_envelope_checks`, which drives every server
over live JSON-RPC in two halves: a positive probe that must answer with the flag
set, and a negative control — an omitted function name — that must answer without
it. Both halves are load-bearing; without the control, a server that flagged
*everything* would pass.

**The gate was written first and run red before a single server was fixed.** Its
red run named exactly the six that a static reading had named, with zero
differences in either direction.

The gate lives inside the existing `smoke` suite rather than in a new one,
because `smoke`'s subject already is fleet plumbing invariants, it already starts
all fifteen servers offline, and its declared case count is data-derived — so a
fleet-wide behavioural check cost no roster, README, or forge churn.

**The gate's limit is written down beside the gate:** it lands on ONE failure
site per server, so it proves the wrap and not the sites.

## Alternatives Evaluated

### Option 1 — Fix the six servers first, add the gate afterwards
- **Pros:** the obvious order. Six servers were visibly wrong, the fix was
  mechanical, and a gate written afterwards still prevents the regression.
- **Cons:** restoring an unmeasured convention only resets the clock — it answers
  what broke, never why nothing noticed. A gate nobody has seen fail is not
  evidence that it can fail. Running it red first turned a static reading of
  fifteen servers and a runtime measurement into two independent methods agreeing
  with zero differences either way, which is a stronger claim than either alone.

### Option 2 — Convert jenkins' twenty-five `_err` call sites like the other five servers
- **Pros:** mechanical, uniform with the rest of the fleet, introduces no new
  concept.
- **Cons:** it fixes the sites and leaves the shape. jenkins rendered its reply
  from the payload's `error` key but flagged it from a separate sentinel, so a
  reply could render **as** an error and still report success; converting sites
  makes that merely currently-absent rather than impossible. The route was not
  even mechanically available: `_err`'s first parameter is named `message`, and
  seven of the payloads carry their own `message` key, so seven conversions raise
  `TypeError: got multiple values`. Replaced by one predicate
  `Scripts/mcp-jenkins.py:_is_error`, read by both the renderer and the
  transport. **Where two behaviours describe one condition, they must read one
  predicate.**

### Option 3 — Home `_ErrorText` as a canonical generated block
- **Pros:** it is the cleanest candidate the generator could be offered — three
  byte-identical copies, an empty `free_names`, safe under both tab checks. Every
  rule the generator applies would accept it.
- **Cons:** homing it would grant canonical status and drift protection to a
  **second** mechanism for one contract, in three servers that are never
  launched. Declaring a divergence as a divergence is the established treatment,
  and `_tool_error` is the standing precedent: eight copies, three variants,
  documented in `Scripts/MCP_SKELETON.md` and unhomeable anyway because its
  `free_names` holds `McpServer`. Not everything duplicated should be homed.

### Option 4 — Flag every returned string that reads like a failure
- **Pros:** maximal coverage with no per-site judgment, and no risk of missing a
  site.
- **Cons:** it inverts on the most common case. **An action that did not happen
  is a failure; a question answered with "none" is a success.** A session lister
  reporting no sessions, a browser server reporting the browser unreachable, a
  documentation server reporting that no library matched — all are true answers
  from a working tool, and all would have been flagged. Thirty-two string returns
  in one server alone were classified and deliberately left as successes on that
  rule. Flagging them would also have broken the gate's own negative control,
  which is what makes the rule structural rather than stylistic.

### Chosen — one invariant, one gate written first and run red, and a rule that decides per site
The gate comes first because the interesting failure was invisibility, not
incorrectness. The classification rule is what keeps the fix from acquiring an
opposite-sign twin, and it was drawn independently, in the same words, by three
agents working on different servers — which is the strongest evidence available
that it is the real boundary and not a convenience.

## Consequences

- **The gate's blind spot is permanent, not a TODO.** Check 7 proves the wrap and
  never the call sites below it, and jenkins passes it without any of its
  twenty-five sites being touched. An unstated scope is the same defect as the
  false invariant it replaced, which is why the limit is recorded beside the
  gate. The first real instance of that gap was found in
  `Scripts/mcp-gdc.py:_js_failure`, where four handlers passed injected page JS's
  own failure prose back as a successful reply — and it is **not gateable
  offline**, because both of the gate's probes return before
  `Scripts/mcp-gdc.py:_resolve_session` and so can never reach a handler that
  talks to a browser. Its proof is a recorded live probe, not a suite case.

- **Two routine jenkins negatives now report a failure on a standalone call** —
  pipeline-not-found and no-test-report. Accepted deliberately: the difference
  between "not found because you asked wrong" and "not found because there is
  nothing" is not one a caller can act on differently, and encoding it per site is
  exactly the subtlety that drifts back.

- **The fleet carries two predicate shapes, not one, and that is the end state.**
  Raising and returning are two routes into one predicate rather than two
  mechanisms — the wrap's `except` assigns the same dict a handler would have
  returned — and every server in that group does both, per site.

- **`_tool_error` is not the fleet convention it was described as.** It exists in
  eight of fifteen servers, and `Scripts/mcp-purity.py` — the file the earlier
  summary called the convention — is not one of them. A checklist demanding that
  helper would have demanded something the majority lacks.

- **A predicate over a payload must be top-level only.** jenkins' nested `error`
  keys are real upstream data, and the renderer already depended on the
  one-level-down convention; a recursive search would have reported data as
  failure.

- **An unregistered server is still gated.** Three servers have footprint zero and
  are never started in normal use, but they start offline in milliseconds and
  answer both probes, so they were driven rather than excused.
