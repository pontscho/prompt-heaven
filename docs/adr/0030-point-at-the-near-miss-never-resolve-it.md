---
name: 0030-point-at-the-near-miss-never-resolve-it
type: adr
status: active
title: Point at the near miss, never resolve it
description: Decision to append one fleet-wide "Did you mean 'X'?" pointer to every refusal of a name a caller sent -- an unknown function on sixteen servers, an unknown parameter on the six that refuse one, the proxy's unknown tool -- from a fourteenth canonical source, Scripts/_mcp_dispatch.py, whose one block takes the host's tables as arguments; with the ADR 0014 domain argument that kept it out of _mcp_json.py, the rule that a suggestion never resolves (context_chars is pointed at context_lines and still refused), the behaviour changes it brought, the five alternatives rejected or deferred, the gate and its red run, and the declared limits -- the unmeasured 0.6 cutoff and the eleven hosts that ignore an unknown parameter.
sources:
  - Scripts/_mcp_dispatch.py
  - Scripts/amalgamate.py
  - Scripts/_mcp_smoke_test.py:near_miss_checks
  - tests/test_generated_region.py:group_dispatch_blocks
verified:
  commit: eb1dd9e
  date: 2026-10-08
links:
  - generated-regions
  - scripts
  - tests
  - 0010-a-handler-failure-must-reach-iserror
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0025-generate-do-not-import
  - 0027-the-proxy-relays-it-never-composes
  - 0029-the-http-front-is-a-domain
---

# ADR 0030: Point at the near miss, never resolve it

Status: accepted 2026-10-08. Implemented in the working tree on top of `da3f746`
and not yet committed when this page was written, which is why its frontmatter
says `draft`: a `verified.commit` cannot vouch for code no commit holds yet. Once
the code lands, the page is promoted to `active` with that commit, and from then
on it is append-only. Every number below is a count at the moment of the
decision. The live counts are in the `SUITES` table of `tests/run.py` and in the
measured regions of [[generated-regions]].

## Context: a refusal that lists everything and points at nothing

Every server routes one `<name>_call` tool to a table of functions, and six of
them also check a function's params against a table of accepted names. A caller
that misspells either gets a refusal listing everything on offer, and nothing
pointing at the entry it almost typed. The caller is usually a model. A model
reading a list of forty names after sending `serch_for_pattern` has to diff the
list itself, and it often reaches for the next plausible spelling instead.

One server already did better. `mcp-forge` answered an unknown build or test
target with the closest real one, through `difflib.get_close_matches(name,
candidates, n=1, cutoff=0.6)` `Scripts/mcp-forge.py:_suggest`. Nothing else in
the fleet did, not even forge's own unknown-function refusal.

The case that set the rule was a purity call carrying `context_chars`. Purity
refused it and listed its accepted params, `context_lines` among them. The
obvious fix is an alias, and it is the wrong one: a character count and a line
count are different requests, so answering one with the other silently is
exactly the defect [[0015-ambiguity-is-the-defect]] exists to prevent. The
caller needed to be pointed at the name, not handed it.

## Decision

1. **One pointer, one sentence, fleet-wide.** A refusal of a name the caller
   sent ends with ` Did you mean 'X'?` when one real name is close enough, and
   with nothing when none is. It is appended to:
   - the unknown-function refusal on the sixteen servers that have a function
     layer;
   - the unknown-parameter refusal on the six that refuse an unknown parameter:
     purity, postgres, search, webfetch, inspect and wiki
     `Scripts/_mcp_smoke_test.py:NEAR_MISS_PARAM`;
   - the proxy's unknown-tool refusal, its one name lookup
     `Scripts/mcp-proxy.py:_handle_tool_call`. That sentence is the proxy's own
     and not a relayed payload, so a child's reply still passes through
     untouched ([[0027-the-proxy-relays-it-never-composes]]).
2. **The threshold and the call are forge's.** `difflib.get_close_matches` with
   `n=1, cutoff=0.6` `Scripts/_mcp_dispatch.py:_did_you_mean`, so a suggestion
   means the same closeness wherever it appears.
3. **A suggestion never resolves.** It is a sentence in a refusal and never an
   alias. The call is still refused with the flag set
   ([[0010-a-handler-failure-must-reach-iserror]]). `context_chars` is pointed at
   `context_lines` and refused.
4. **An alias may be matched, but the canonical name is what is suggested.** A
   near miss of an alias still points at the spelling the documentation uses.
   An alias whose canonical name is not on offer is not a candidate. On the
   three hosts with a per-function alias table (purity, postgres, wiki), the
   table handed in is the per-function one overlaid on the global one, which
   is the effective mapping `_resolve_aliases` reads
   `Scripts/mcp-purity.py:handle_purity_call`. A reverse map built from either
   table alone is wrong there, for the reason ADR 0015's Option 5 gives.
5. **Silence is half the contract.** A word that is not a string, is empty, is
   already a candidate, or resembles nothing gets no suggestion. A guess
   appended to every refusal would teach a caller to ignore it.
6. **The answer depends on the sets, never on table order.** The candidate pool
   is sorted before matching. A single unknown word renders only the server's
   own name. Several unknown parameter keys render `'name' for 'key'` pairs and
   leave out a key with no near miss.
7. **One block, generated, never imported.** It lives in a new canonical
   source, `Scripts/_mcp_dispatch.py`, registered in
   `Scripts/amalgamate.py:CANONICAL_NAMES` and generated into all seventeen
   `Scripts/mcp-*.py` servers between hashed markers
   ([[0025-generate-do-not-import]]). The block reads `difflib` and nothing
   else of the host's, so every host gained `import difflib`. It is written one
   call per physical line with every indent a multiple of four, so the two
   tab-indented hosts, mcp-forge and mcp-webfetch, take it re-indented.

## The domain and its boundary

The source answers one question: how a tool's dispatcher answers a name it
does not know. That passes [[0014-a-canonical-source-is-a-domain]]'s test on
the only question that test asks: whether an existing source could hold the
block without becoming a shelf.

- `Scripts/_mcp_json.py` is the nearest, and it asks a different question. It
  owns how a frame is enveloped and parsed and how a wire **value** is coerced
  `Scripts/_mcp_json.py:_bool_param` `Scripts/_mcp_json.py:_int_param`. A
  suggestion is about the caller's **names** and reads no JSON at all.
- `Scripts/_mcp_paging.py` decides how much of a reply a caller gets, and
  `Scripts/_mcp_logging.py` how a line is logged. The protocol-client sources
  speak a wire.

The alias resolvers belong to the same question, and they stay hand-written in
each host anyway: their free names are each host's own tables, and
[[0015-ambiguity-is-the-defect]]'s Option 7 refused to generate them. The new
source therefore holds what can be shared: the pure half that takes the tables
as arguments. A source of one block has a precedent in
`Scripts/_mcp_concurrency.py`, which holds one constant ([[generated-regions]]).

## Behaviour changes, intended and declared

- **mcp-forge answers an unknown function before it reads its config.** The
  answer depends on the function table alone, so a caller that misspelled
  `build` now hears that, with the near miss, rather than about a missing or
  broken `project-forge.yaml` it never got as far as using
  `Scripts/mcp-forge.py:handle_forge_call`. The table is spelled once
  `Scripts/mcp-forge.py:FORGE_FUNCTIONS`, and `status`, which is folded to the
  empty call before anything routes it, is offered as a suggestion too
  `Scripts/mcp-forge.py:_unknown_function_error`.
- **mcp-gdc's `open_pages` is a real alias of `list_pages`.** It is the name
  another Chrome DevTools server gives the same function, and a caller who knew
  that one reached for it here and was refused. It is an `ALL_HANDLERS` entry,
  as `execute_js` already was, and it is lock-free like `list_pages`
  `Scripts/mcp-gdc.py:LOCK_FREE_FUNCTIONS`. This is the one name the change
  resolved, and it was not a near miss: it is an exact spelling another server
  uses, with the same meaning.
- **mcp-gdc's unknown-function list no longer offers `gdc_call`.** The table
  holds it for the direct-tool path, and the dispatcher refuses it as a
  recursion, so the list offered a name that can never be called
  `Scripts/mcp-gdc.py:handle_gdc_call`.
- **mcp-git suggests only from its read-only whitelist** `Scripts/mcp-git.py:handle_git_call`.
  A real git subcommand it does not expose is already covered by the
  refusal's own sentence, so the near miss worth naming is a typo of an allowed
  one.
- **Loose suggestions are possible at 0.6.** Two were seen during the
  implementation: `git rebase` draws `rev-parse`, and postgres'
  `__no_such_function__` draws `call_function` (a ratio of 0.67, because they
  share `_function`). Both are still refusals; the pointer is the only thing that
  is wrong.

Two output formats were held in place on purpose. The unknown-function lists
of mcp-search, mcp-jenkins and mcp-webfetch are read by `name_existence` as the
live inventory: plain names, comma-separated, on one line, with only the first
token of each comma chunk kept. A single-word suggestion carries no comma and
sits after the list, so it never enters that inventory.

## Alternatives rejected

1. **Make `context_chars` (and near misses like it) an alias.** *Rejected:* an
   alias resolves, and a character count resolved to a line count is a
   different request answered without a word. The caller sees a result and has
   no moment at which to suspect the substitution. Pointing teaches the right
   name. Aliasing hides the wrong one. ADR 0015 already decided that silently
   honouring one reading of an ambiguous call is the defect. `open_pages` is not
   a counter-example: it is an exact spelling with an identical meaning, not a
   near miss.
2. **Put the block in `Scripts/_mcp_json.py`.** *Rejected:* that source is the
   nearest by habit and the wrong one by domain (above). Filing a names question
   under envelopes and wire values would make the marker's source field
   decorative for every block in that file, which is the one property
   [[0014-a-canonical-source-is-a-domain]] protects.
3. **Hand copies, one per host.** *Rejected:* seventeen hosts, two of them
   tab-indented, and a wording that is a contract (the smoke gate asserts the
   sentence word for word). The block has no free name but `difflib`, so none of
   the reasons that keep `_resolve_aliases` hand-written applies. Writing one
   rule seventeen times is what the generator exists to prevent.
4. **Copy forge's parenthetical wording.** *Rejected:* forge's target suggestion
   sits mid-sentence before a line break, where a parenthetical reads. Appended
   at the end of a refusal that already ends in a list, a separate sentence is
   the readable form, and one fixed sentence is what lets the gate assert the
   words and not only the flag. Forge's own target suggestion keeps its wording
   `Scripts/mcp-forge.py:_suggest`; it is not rewritten to the new block.
5. **Make the hosts that ignore an unknown parameter refuse it, so they get a
   parameter suggestion too.** *Deferred, not rejected.* Eleven hosts silently
   ignore an unknown key (and mcp-git turns one into a flag by design). There is
   no refusal there to append a pointer to, and adding one is a wire-visible
   policy change per host. A suggestion cannot carry that change in. Whether
   those hosts should refuse is a separate question, and it is undecided.

## The gate and its red run

Two layers.

- **Behaviour, in-process.** Group E of `generated_region` drives the canonical
  block: a near miss suggests (`buidl` to `build`, `context_chars` to
  `context_lines`), a far miss and every non-string or empty word are silent, an
  alias is matched and its canonical name suggested, several keys are each
  answered or left out, and two equally close candidates resolve the same way in
  either table order. Group A walks every server and requires a
  `_mcp_dispatch.py` region naming `_did_you_mean` **and** a call to it outside
  the region: a region nobody calls is dead code the drift gate would faithfully
  prove seventeen files agree on `tests/test_generated_region.py:group_dispatch_blocks`.
  Six new cases: `generated_region` 131 to 137.
- **Wire, live.** The smoke harness sends every server a function one typo away
  from a real one and requires `isError` **and** the sentence naming the real
  one; on the proxy it sends a near-miss tool name. A far miss, `qxqxqxqx`,
  must draw no suggestion. On every host that refuses an unknown parameter, a
  near-miss key must draw the canonical name. A coverage row derives "this host
  refuses unknown params" from the source, so a host that learns to refuse one
  cannot join the fleet without a probe `Scripts/_mcp_smoke_test.py:near_miss_checks`.
  `gdc_open_pages_checks` proves `open_pages` dispatches as `list_pages` and that
  `gdc_call` is gone from the list `Scripts/_mcp_smoke_test.py:gdc_open_pages_checks`.

The near-miss and far-miss rows were run red first on all seventeen servers.
The control found one thing on its first run: the far-miss word was first the
envelope probe's `__no_such_function__`, which on postgres scores 0.67 against
`call_function` and therefore draws a suggestion. The control now uses a word
that resembles nothing any host serves `Scripts/_mcp_smoke_test.py:FAR_MISS`, and
the loose postgres pointer is a declared limit below, not a defect.

## Declared limits

Accepted and declared, not gated beyond what is named.

- **The 0.6 cutoff was copied from forge, not measured.** No corpus of real
  caller misspellings was scored against it. The two loose pointers above are
  what it costs at the low end; what it misses at the high end is unknown.
- **Eleven hosts get no parameter suggestion,** because they have no
  unknown-parameter refusal to append one to (Alternative 5).
- **The live gate proves one row per host:** one near-miss function, and on the
  six refusing hosts one near-miss key. The several-keys form and the alias
  matching are proven on the canonical block in group E, not at every call site.
- **The several-keys form echoes the caller's keys** (`'limit' for 'limitt'`).
  The `Unknown params for ...` sentence it is appended to already lists the
  same keys, so it adds no caller text the refusal did not already carry. A
  single-word suggestion renders only the server's own name.
- **Forge carries two phrasings.** Its target suggestion keeps its own wording
  and its own `_suggest`, beside the generated block that answers its
  unknown function (Alternative 4).
- **The alias resolvers stay hand-written,** each host's own, as ADR 0015's
  Option 7 left them. The suggestion reads the same tables they read; it does
  not hold them equal.
