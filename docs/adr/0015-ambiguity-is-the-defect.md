---
name: 0015-ambiguity-is-the-defect
type: adr
status: active
title: Ambiguity is the defect, not the precedence
description: Decision to make an alias-key collision an error across all ten _resolve_aliases hosts rather than unify the fleet's six-to-four last-wins/first-wins split, with the reachability measurement that reframed the question, the seven alternatives rejected, the placement defect the red gate run measured on its own, and the gate's two declared blind spots.
sources:
  - Scripts/MCP_SKELETON.md
  - Scripts/_mcp_smoke_test.py:alias_collision_checks
  - Scripts/mcp-purity.py:_resolve_aliases
verified:
  commit: 386c0f2
  date: 2026-09-15
links:
  - scripts
  - tests
  - generated-regions
  - 0010-a-handler-failure-must-reach-iserror
---

# ADR 0015: Ambiguity is the defect, not the precedence

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is section 7c of `Scripts/MCP_SKELETON.md`.

## Context — a question that was asked about the wrong thing

Every server in the fleet accepts parameter aliases: `file`, `file_path` and
`path` all name one parameter. A resolver walks the caller's keys, maps each
through an alias table, and returns a dict of canonical names.

Nothing stopped a caller from sending **two** spellings of one parameter in a
single call. When that happened, every resolver silently kept one value and
discarded the other, and **which one it kept was decided by the order the keys
arrived on the wire.**

`docs/specs/spec-purity-unification.md:226-227` had already recorded a fix for
this, as bug fix number three: unify the fleet onto last-wins. That decision was
taken while looking at three servers, and it was framed as a precedence question
— *which of the two rules should everyone use.*

The measurement asked a different question, and the answer reframed the
decision: **is a collision reachable, and does anything in the fleet see it?**

- All **ten** hosts that define a `_resolve_aliases` are collision reachable.
- **No host inspects the raw key set** before resolving.
- The fleet's four unknown-parameter gates **all run after resolution**, where
  both spellings have already collapsed into one canonical key. They are
  structurally blind to this and always were.
- The split was **six last-wins to four first-wins** — not the five-to-three a
  carried-forward summary claimed.

The consequence that settled it: **neither rule is order-independent, and
neither prefers the canonical spelling.** On all ten servers, within one policy,
`{"path": A, "relative_path": B}` and `{"relative_path": B, "path": A}` were
already two different calls. Choosing between last-wins and first-wins would not
have fixed the defect class. It would have picked a coin.

This matters more than it would in a library because the caller is usually a
model, and a model that sends two spellings has not made a typo — it has
hedged. Silently honouring one half of a hedge teaches nothing and is
unobservable from the reply.

## Decision

**Two input keys that canonicalize to the same name are an ERROR.** Neither
last-wins nor first-wins, across all ten hosts.

The resolver raises a `ValueError` naming both spellings the caller wrote and
the canonical name they collide on. This required **no new plumbing at any of
the thirteen call sites**: section 7 of `Scripts/MCP_SKELETON.md` already
specifies the parameter normalizer as a place that raises, ADR 0010 already
established that raising and returning are two routes into one `isError`
predicate rather than two mechanisms, and every one of the thirteen sites
already sat inside an `except ValueError` that reaches the flag.

Four properties of the implementation are load-bearing, and each is a rejected
simpler version:

- **Detection happens in the loop, on the canonical name** — never against a
  reverse map built from the alias table. Three hosts overlay a per-function
  table on the global one and `Scripts/mcp-postgres.py` **inverts** it: globally
  `parameters` → `params`, but for `call_function` `parameters` → `args` *and*
  `params` → `args`. A reverse map from either table alone is wrong there.
- **A second dict records which spelling claimed each canonical name**, because
  `resolved` remembers only canonical names, and reporting that `relative_path`
  collided with `relative_path` is not actionable.
- **The reported pair is sorted**, so the message — not only the verdict — is
  order-independent. That is the property the whole decision is about.
- **The rule is presence-based, not value-based.** Two spellings carrying the
  same value are refused too.

**The gate was written first and run red.** It named exactly ten servers with
zero differences in either direction: the ten that define a resolver failed the
refusal probe, the five that do not passed everything, and both the control and
coverage halves passed everywhere. It lives in the existing `smoke` suite for
ADR 0010's reason — that suite's subject already is fleet plumbing invariants,
it already starts all fifteen servers offline, and its case count is
data-derived, so a fleet-wide behavioural check cost no roster or forge churn.

## Alternatives Evaluated

### Option 1 — Unify on last-wins, as the Phase 0 spec recorded
- **Pros:** already decided and written down; the smallest possible diff; three
  of the ten servers were already there.
- **Cons:** the recorded decision was scoped to three servers and never asked
  the other seven, and it was taken as a precedence question before anyone had
  measured that both rules read the wire order. It makes the fleet consistent
  about an answer that is wrong on all ten. **A decision that was right for the
  servers it was made about does not automatically extend to the ones nobody
  looked at.**

### Option 2 — Unify on first-wins
- **Pros:** arguably the safer bias — the caller's first word wins, and an alias
  cannot overwrite a canonical name supplied later.
- **Cons:** identical objection, and it is the minority shape. Neither bias is
  principled: both are artefacts of where a key landed in a JSON object.

### Option 3 — Prefer the canonical spelling, alias only as a fallback
- **Pros:** genuinely order-independent, unlike Options 1 and 2, and it resolves
  silently so no caller is broken. The strongest alternative on this list.
- **Cons:** it only decides collisions where one of the two keys *is* the
  canonical one. Two aliases of the same canonical — and
  `Scripts/mcp-purity.py` has five keys landing on `relative_path`, six on
  `needle` and six on `repl` — still have no principled winner, so the coin
  comes back for exactly the cases with the most spellings. It also still
  discards a value the caller wrote, which is the original defect wearing a
  better hat.

### Option 4 — Refuse only when the two values DIFFER
- **Pros:** strictly fewer callers broken; a genuinely redundant call keeps
  working; nothing is ever silently dropped.
- **Cons:** the same call shape would error or pass depending on the data, so
  the rule could not be stated without naming the values. ADR 0010 already
  names this pattern: encoding the distinction per site is exactly the subtlety
  that drifts back. **A rule a caller cannot predict from its own call is not a
  contract.**

### Option 5 — Detect before resolution, with a reverse alias map
- **Pros:** one check at the top of the function; no second dict; reads as a
  precondition rather than a loop invariant.
- **Cons:** wrong on the three two-table hosts, and silently so. The effective
  mapping is the per-function table overlaid on the global one, which postgres
  inverts; a reverse map built from either table alone would miss real
  collisions and invent false ones. The loop already computes the effective
  mapping.

### Option 6 — Gate it statically, by AST, like the read-loop and wire-log gates
- **Pros:** no subprocesses, instant, and it would prove the shape in all ten
  bodies rather than one failure site per server.
- **Cons:** it proves the source contains a check, never that the error reaches
  the caller with the flag set — and the live run is what earned this change its
  one real discovery. `Scripts/mcp-lua-lsp.py` answered *lua-language-server not
  initialized* to the collision probe, because it alone resolved parameters
  inside the backend session, below the lock and below a ninety-second auto-init
  wait. A caller who misspelled a parameter there needed a working language
  server before anybody would tell them so. **An AST gate would have passed that
  server, green, with the defect in place.**

### Option 7 — Home `_resolve_aliases` as a generated block so the rule is written once
- **Pros:** it is the fleet's widest hand-copied non-generated function, and
  writing one rule nine times is exactly what the generator exists to prevent.
- **Cons:** its free names are each host's own alias tables, and
  `Scripts/mcp-forge.py` takes the table as an argument instead. These are not
  copies that drifted but **ten shapes that never agreed**: nine distinct bodies
  over ten files, only `Scripts/mcp-clangd.py` and `Scripts/mcp-cuda.py`
  byte-identical, two tab-indented. ADR 0014's rule refuses it and ADR 0010's
  Option 3 is the standing precedent — not everything duplicated should be
  homed. Declaring the divergence and gating the behaviour is the treatment.

### Chosen — refuse, with a gate written first and run red
Because the interesting property was never which value won. It was that
**nothing could observe that a choice had been made at all** — not the caller,
not the unknown-parameter gates, not any suite. An error is computable from the
alias table alone, so it works identically in the six hosts that have no
accepted-parameter table; it matches ADR 0010's precedent of surfacing rather
than swallowing; and it teaches a caller that is usually a model.

## Consequences

- **This is a wire-visible behaviour change, deliberately.** A caller that was
  relying on last-wins — or on first-wins, on four servers — now gets an error.
  That is the point: it was relying on wire position.

- **`Scripts/mcp-lua-lsp.py` now answers parameter errors without a backend.**
  Resolution moved above the lock and above the auto-init wait, to where that
  file's own comment says the lock-free answers belong. A side effect worth
  stating: on that server an unknown function sent together with a bad parameter
  now reports the parameter, where it used to report the function. That is what
  the other nine already did.

- **The gate's blind spots are declared, not deferred.** It proves the resolver
  REFUSES; it does not prove the ten alias TABLES are free of collisions a
  caller could not have caused, and it does not cover the envelope-level
  `function`/`f` and `params`/`p` or-chains in every dispatcher, which are
  or-expressions rather than table lookups, a different mechanism at a different
  layer, and remain first-wins.

- **Forge's YAML is now subject to the rule too.** `_merge_filter` resolves a
  target's declared filter and the call's filter as separate dicts, so a
  `project-forge.yaml` target declaring both `pattern` and `grep` is now an
  error. Overriding across the two dicts is untouched and still works, because
  they are resolved independently and merged afterwards.

- **`_resolve_aliases` was invisible to the hand-copy census** and appeared in
  neither register of deliberate exclusion, because the census intersects
  top-level names with canonical BLOCK names and this was never one. Option 7 is
  the answer that was missing; the register now carries it.

- **A recorded decision can be right and still not travel.** Bug fix number
  three was a reasonable call about three servers. It became wrong the moment it
  was read as a fleet rule, and nothing in the spec said which it was.

## Postscript — two figures, as measured

Appended rather than edited, on the standing rule that a measurement is dated by
a change and never falsified by it, and that a frozen argument is not rewritten
to match a later tree.

**Option 7's "nine distinct bodies over ten files, only `Scripts/mcp-clangd.py`
and `Scripts/mcp-cuda.py` byte-identical" was measured BEFORE implementation and
is eight and three at `386c0f2`.** The cause is the decision itself:
`Scripts/mcp-tshark.py` held the fleet's last first-wins resolver, and replacing
its body with the shared refusal converged it onto the clangd/cuda body. **A
supporting figure was invalidated by the very change it was written to
describe.** Nothing follows for Option 7. Its disqualifier is the free names —
each host's own alias tables, and forge's table-as-argument — which no amount of
body convergence touches; the body count was colour, and it is now recorded as
such in section 7c of `Scripts/MCP_SKELETON.md`, which is living text and
carries the current number.

**Option 3's spelling counts are off by one and mix two counting bases.** On the
global table alone, `Scripts/mcp-purity.py:148-193` lands **six** keys on
`relative_path` (not five), **five** on `needle`, and **six** on `repl`; the
per-function overlay adds one more to `needle` (`old_content`) and one to `repl`
(`new_content`). The argument is
unaffected in the direction that matters — more spellings per canonical name
makes Option 3's objection stronger, because the case it cannot decide is
exactly the one where neither of the two colliding keys is the canonical one.
