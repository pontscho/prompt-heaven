---
name: 0014-a-canonical-source-is-a-domain
type: adr
status: active
title: A canonical source is a domain, not a shelf
description: Decision that each canonical generation source holds one domain and that a region's names resolve only against the source its own marker names, with the naming complaint that measurement redirected to the container, the rule that decides when a sixth source is warranted, the six alternatives rejected, and the two hand-written registries that have to agree.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_concurrency.py
  - tests/test_generated_region.py
verified:
  commit: 8d56b6d
  date: 2026-09-14
links:
  - scripts
  - tests
  - generated-regions
  - 0012-the-transport-tier-diverged
  - 0013-the-ceiling-is-a-payload-class
---

# ADR 0014: A canonical source is a domain, not a shelf

**Status:** accepted. The decision is the RULE for what may be a source. The five
sources that exist are its worked examples, and this page is careful about which
of them were decided under the rule and which are ratified retroactively.

## Context — a naming complaint that was about the container

The generation mechanism shipped with exactly one canonical source. `8effd2f`
created `Scripts/_mcp_json.py` as a deliberately narrow pilot — one region, one
function, in one server — and the file took its name from the first thing
extracted into it, the `JSONDecodeError` window. Everything shared afterwards was
dropped in behind that name.

Five and a half hours later, on the same day, `d65132a` split it. The complaint
that surfaced the problem was **about the block names**: they carried no `_json_`
prefix, and a reader wanted to know why the contents of a file called
`_mcp_json.py` did not announce themselves as JSON.

The measurement redirected the complaint at the container.

## What one source had become, measured

Of the seven blocks in the file, **four were not JSON at all.**
`encode_lsp_message` is `Content-Length` framing — bytes on a wire, no JSON
anywhere in it. `_rows_note` renders a human-readable pager sentence onto the
last line of a text payload. A `_json_` prefix would have been **false on more
blocks than it was true.**

The name described the seed, not the contents. The proposed fix would have
propagated the seed's name onto four blocks it did not describe, which is how a
container defect gets mistaken for a naming defect and then made worse.

**The fix cost almost nothing, and that is the finding rather than an aside.**
The marker format already spelled its source — `BEGIN GENERATED: <source> ::
<names>` — and `parse_names` already parsed that field. A single line rejected
everything that was not the one hardcoded filename. Finishing that stub is the
whole of what made the source field load-bearing. The third source afterwards
cost exactly one line; so did the fourth, and so did the fifth.

## Decision

**One source per domain, and the source named on a marker selects the block map.**
A region's names resolve against that file and no other.

Two consequences follow, and both are asserted rather than assumed:

- An unknown source is refused **by name**, and the refusal lists what is on
  offer — the reader of that message is somebody who mistyped a filename.
- A name defined in **another** canonical file does not resolve. This is the
  dangerous direction and the reason the rule needs a gate: a block served out of
  the wrong domain compiles, runs, and looks correct in every server carrying it,
  so nothing downstream would ever report it.

**A source must EARN the label.** `_mcp_json.py` has been narrowed twice to keep
it — framing left for `_mcp_lsp.py`, row accounting for `_mcp_paging.py` — and
both departures are asserted **as departures** in the suite
(`json-source-drops-framing`, `json-source-drops-row-accounting`). A block that
came back would otherwise be tested in its new home while still sitting in its
old one, and the assertion sits where a reader goes looking for the function.

## The rule for a sixth source

> A new canonical source is warranted when a shareable block's domain cannot be
> held by an existing source **without making that source a shelf.**

Not when the block is large. Not when it is shared widely. Not when it is
convenient.

`Scripts/_mcp_concurrency.py` is the worked example and the reason this page
exists now rather than a session earlier. `MAX_INFLIGHT_REQUESTS = 8` was the
fleet's **widest-shared constant** — nine live servers had each written the same
line — and it had gone unlifted for a reason that had nothing to do with the
constant: there was no domain to put it in. JSON-RPC envelopes, logging
configuration, LSP framing and output paging are four questions, and *how many
handlers run at once* is a fifth. Filing it under whichever of the four it sat
nearest would have made the source field decorative **for every block in that
file** — surrendering the one property the multi-source design exists to protect,
to avoid writing one line.

The rule cuts the other way too, and that is what stops it becoming a licence.
`_mcp_lsp.py` holds one block. `_mcp_logging.py` holds one block.
`_mcp_concurrency.py` holds one constant. **Block count is not the test**; domain
is. A source that would hold a second domain is not cheaper than a second source.

The price of the rule is one line in each of two hand-written registries. A block
that does not justify one line is not a block.

## Alternatives rejected

**1. Prefix the block names instead (`_json_error_window`, `_json_result`, …).**
The original proposal. Refused on measurement: false on four of seven blocks. It
treats a container defect as a naming defect and spreads the wrong name further.

**2. Keep one file and rename it neutrally — `_mcp_shared.py`.** This makes the
source field decorative and gives up the cross-domain refusal entirely. It also
does not scale in the direction that matters: the reason to know which file a
name comes from is exactly the reason to have more than one file.

**3. Flatten every source into one namespace at load time.** Refused in
`load_all_blocks`'s own docstring, and the reason is the failure mode rather than
the tidiness: the marker's source field becomes decorative, and the first
collision between two domains resolves to whichever file loaded last — a silent
choice nobody wrote down.

**4. Glob `_mcp_*.py` into the registry rather than writing it by hand.** Refused
because a file would become a generation source **by merely existing**, and a
marker naming it would read exactly as legitimate as the ones that belong.
`Scripts/_mcp_smoke_test.py` is the standing counter-example: an `_mcp_*.py` file
that is deliberately not a source. Adding a source must be a deliberate edit.

**5. Leave `MAX_INFLIGHT_REQUESTS` duplicated rather than open a fifth domain.**
Considered seriously, because a fifth source for one integer is the shape
over-engineering takes. Refused: nine hand copies is the widest agreement on a
single line anywhere in the fleet, and agreement on disk is precisely what rots.
The generation mechanism exists for that case or it exists for none.

**6. Import the sources instead of generating them.** Not re-decided here. It was
settled in `8effd2f` on four measured grounds — `__pycache__` in a tree the
suites assert is bytecode-free, a `sys.path` entry the harness never adds,
helpers relocated out of the module attributes `test_mcp_footprint.py` reaches
for, and the `ImportError` a user copying one file without its sibling gets.
That decision has never had a page of its own; see below.

## The two registries that have to agree

`CANONICAL_NAMES` lives in `Scripts/amalgamate.py` and is **mirrored, not
imported,** in `tests/test_generated_region.py` — for the same reason the marker
prefixes are spelled out there: they are an on-disk format contract, and a test
that imported the tuple would sail through any change to it.

The mirror earns its keep on exactly the edit this page describes. A source added
to the generator and not to the mirror fails `sources-registered` **by name**
instead of being adopted silently. `every-source-in-use` closes the other
direction: a registered source no live region names is a shelf the drift gate
cannot reach, so a block rotting inside it would read green forever.

Measured at `8d56b6d`: five sources, 16 blocks, 99 live regions emitting 125
block instances across the 15 servers.

## What this page does not settle

- **The `_json_` prefix question for the blocks that genuinely are JSON.** Open
  since `d65132a`, which noted the argument against it for `_result` and
  `_error`: they are methods, and the receiver already supplies the context a
  prefix would repeat. Nobody has ruled on the rest.
- **The single-file rule itself** — generate rather than import. Decided in
  `8effd2f` with the measurements quoted above and never written up as a page.
  This ADR depends on it and does not restate it.
- **The per-block tab rule**, narrowed from a per-file refusal in the same commit
  that split the sources, and likewise unrecorded.
- **Whether a one-block domain should be merged if it never grows.** Three of the
  five sources hold a single block today. The rule above says domain rather than
  count, so none of the three is wrong — but nothing has revisited them, and
  nothing schedules a revisit.

## Update — `11a3542`: the LSP source took the DocumentUri pair

`uri_to_path` and `path_to_uri` were lifted into `Scripts/_mcp_lsp.py` from four
hand copies, three of which could not decode what the fourth encoded. That
widened the source's domain from the `Content-Length` framing of a message to how
the LSP wire is spoken — framing **and** the `file://` DocumentUri of a path
`Scripts/_mcp_lsp.py`. The rule above decided that placement rather than being
bent by it: a separate source for the pair would have been two canonical files
with a byte-identical consumer set naming the same protocol, which splits a
domain instead of separating two.

Two counts above are superseded, and neither of them is an argument.
`_mcp_lsp.py` holds three blocks rather than one ("The rule for a sixth source"),
and **two** of the five sources hold a single block rather than three ("What this
page does not settle") — so the open question there now has two members. Block
count is still not the test; the source took the pair because the pair is the
same domain.

The figure stamped at `8d56b6d` stands for the commit it names. Re-measured at
`11a3542`: five sources, 18 blocks, 103 live regions emitting 133 block instances
across the 15 servers.

## Addendum (2026-09-29): the first canonical source proposed outside Scripts

Roadmap item R-0002 asked whether the p:wiki helper module could become a canonical source rendered into `Scripts/mcp-wiki.py`. The domain rule would have admitted it: the wiki helpers are one domain, not a shelf. What refused it was location and indentation. The gated contract anchors every canonical source to a file under `Scripts/`, and the module is tab-indented, which the tab-safety rule rejects for a block; the generator converts spaces to tabs, never back. The CLI functions also call module-qualified or renamed names and so could not be blocks without rewriting the CLI. Lifting either rule is a change to this decision, not a registry entry, so R-0002 settled for parity gates instead.

## Addendum (2026-09-29): the four open threads, settled

Roadmap item R-0010 closes the four threads this page left open under "What this page does not settle". Three are settled below. The fourth, generate rather than import, now has its own page, [[0025-generate-do-not-import]].

### 1. No retroactive `_json_` prefix

The blocks that genuinely are JSON keep their names. Four reasons, each already on record:

- The marker's source field names the domain. That is this page's own decision, so a prefix would repeat what `BEGIN GENERATED: _mcp_json.py :: ...` already says.
- Every precedent set its prefix when the name was first written, never afterwards. The `_ws_*` names arrived with the websocket source ([[0023-the-websocket-client-is-a-sixth-domain]]), and tshark's `_md_cell` took the fleet's `_md_` prefix when it was written ([[0016-a-cell-may-not-forge-a-boundary]]). A prefix added later renames code that already works.
- The prefix would be true on some blocks and false on others. `_ensure_dict` decodes with `json.loads`, and `_json_error_window` already carries the prefix. `_bool_param` and `_int_param` coerce wire values and hold no JSON at all. This is the measurement that refused Alternative 1, applied to the blocks that stayed. `_result` and `_error` are methods, and `d65132a` already noted that their receiver supplies the context a prefix would repeat.
- A rename moves every call site across the fleet. A search at `0653fab` found 123 lines calling `_bool_param`, `_int_param`, `_ensure_dict` or `_json_error_window` in the servers alone. It would also drop the hand copies out of the hand-copy census, which is keyed on the canonical names `Scripts/amalgamate.py:hand_copies`, until each copy was renamed too.

### 2. The per-block tab rule

`d65132a` narrowed the tab refusal from per file to per block. The per-file rule refused every tab-indented host outright, which cost `mcp-forge` three hand copies that were byte-identical to the canonical text except for the indent character. The rule now decides each block with two independent checks `Scripts/amalgamate.py:block_is_tab_safe`: no implicit line join while a bracket is open, and every leading whitespace run a whole multiple of four spaces. Anything the checks cannot prove is unsafe: a tokenizer failure, a stray tab, a backslash continuation. An unsafe block in a tab host is refused by name `Scripts/amalgamate.py:render`. There is no fallback to spaces, because a file that mixes both indent styles is worse than either.

R-0002 found the consequence for the sources themselves (`43140ae`, and this page's previous addendum). A canonical source must itself be space-indented. The generator converts spaces to tabs `Scripts/amalgamate.py:to_tabs` and never converts tabs to spaces, and a leading tab fails the check. The mechanics are in [[generated-regions]]. The gate is `tests/test_generated_region.py:group_tabs`.

### 3. One-block sources stay separate

`Scripts/_mcp_concurrency.py` and `Scripts/_mcp_logging.py` each hold one block, and neither is merged into another source. Block count never triggers a merge. This page already says so: "Block count is not the test; domain is." A merge would make the receiving source hold two domains, which is the shelf this page refuses. The question is revisited only if a source's domain argument fails, never because the source stayed small.

## Addendum (2026-10-07): the twelfth registry entry, the OAuth client source

The llm-router `codex` and `openai` kinds added `Scripts/_mcp_oauth.py` to the registry `Scripts/amalgamate.py:CANONICAL_NAMES` and to its hand mirror in `tests/test_generated_region.py`. This is a domain decided under this page's rule, not a source ratified after the fact.

### Two counts, and which one is a measurement

The registry now holds twelve names, counted in both tuples (the generator's and the suite's mirror) on the working tree over `76fa06a`. The plan called the source the seventh domain decision, continuing the ordinal [[0023-the-websocket-client-is-a-sixth-domain]] started; [[0027-the-proxy-relays-it-never-composes]] used the same ordinal for the `_mcp_mcpclient.py` it refused. That ordinal is a label, not a count anything reproduces: the Chrome client and its two decoders arrived as one decision and the two search sources as another, so no rule turns eleven files into six decisions. The number `sources-registered` checks is the registry entry, twelve, and that is the number [[generated-regions]] uses. The ordinal is recorded here only so the plan's word can be traced.

### Why neither the Chrome nor the WebSocket source holds it

- `Scripts/_mcp_chrome.py` answers how a client's bytes look like Chrome's on the wire. The OAuth source puts no bytes on a wire of its own: every request builder returns `(url, headers, body)` and the host posts it through its own transport `Scripts/_mcp_oauth.py:_oauth_refresh_request`. What to send to a token endpoint and what its answer means is not a wire question. Filing it there would also have put it out of its one host's reach: the Chrome source is in `WHOLE_SOURCES`, and the router takes none of the client. Its address classifier is three declared hand copies `Scripts/amalgamate.py:HAND_COPY_REASONS`.
- `Scripts/_mcp_websocket.py` answers a frame protocol: the upgrade, the frames, the control frames a client must answer. A grant has no frames. The one socket the OAuth source opens is the loopback redirect listener, which reads one HTTP request head and answers one page.
- Neither says what a grant is, when it is dead and needs a new login, or what an ID token's claims mean. Under the rule above, a source that would hold a second domain is not cheaper than a second source.

### Why the provider rows stay in the host

The protocol is the domain; a vendor is not. Each provider's endpoints, client id and scopes are one vendor's data. Written into the canonical source, they would make it a shelf of vendors the moment a second provider arrived. The source defines only the row type, `OAuthProvider` `Scripts/_mcp_oauth.py:OAuthProvider`, and the host builds its rows with it `Scripts/llm-router.py:_rt_oauth_providers`.

### Why it is not WHOLE

`WHOLE_SOURCES` still holds five names `Scripts/amalgamate.py:WHOLE_SOURCES`, and `_mcp_oauth.py` is not among them. The source follows the websocket precedent rather than the Chrome one: a sans-IO core plus a thin I/O wrapper of two blocks, `_oauth_listen` and `_oauth_sync_accept_callback`, which the router takes on one marker with the core first. A future host that receives the redirect through its own HTTP front can take the core without the wrapper, and a WHOLE declaration would forbid exactly that. The cost is the one the websocket source pays: the core's block list is mirrored by hand as `OAUTH_CORE` in `tests/test_generated_region.py`, beside `WEBSOCKET_CORE`. The mirror has already had to move once: the security review's F2 and F9 fixes added `OAUTH_MIN_REFRESH_INTERVAL_S`, `OAUTH_INT_LITERAL_LIMIT` and `_oauth_bounded_int` to the source, and the same three names to `OAUTH_CORE` and to the router's marker.

## Addendum (2026-10-07): two domains widened in place, strict JSON and the log-value renderer (R-0067, R-0068, R-0070)

Two fleet-wide changes on 2026-10-07 put new blocks into existing sources instead of opening new ones. The rule above decided both placements: each new block answers a question its source already owns, so neither source becomes a shelf.

### `_mcp_json.py` took strict parsing and emitting (`d4a241b`)

Six new blocks: `JSON_INT_LITERAL_LIMIT`, the three `json.loads` hooks `_json_no_constant`, `_json_finite_float` and `_json_bounded_int`, then `_strict_loads` and `_strict_dumps` `Scripts/_mcp_json.py:_strict_loads`. The source docstring widens the domain from "JSON-RPC envelopes, wire-value coercion, and JSON error reporting" to include "strict JSON parsing and emitting for a peer's frames". This is how a frame is parsed and written, which is what the envelope source is for. The blocks are new code, not a lift, and the docstring says so, so their first generated diff is not marker-only. `_ensure_dict` now parses through `_strict_loads`. Its marker therefore carries all eight names: the six first, then `_json_error_window, _ensure_dict`.

Alternative 1's measurement still holds for the new names. `_strict_loads` and `_strict_dumps` carry no `_json_` prefix, and no prefix was added retroactively (the 2026-09-29 addendum, thread 1).

### `_mcp_logging.py` took `_log_value` (`72ed9b4`)

`_log_value` renders a peer-chosen structural value for a log line (CWE-117). It is written once with its two bounds, `_LOG_VALUE_WIDTH` and `_LOG_KEYS_SHOWN`, and it replaced three identical hand copies in `mcp-proxy`, `mcp-search` and `llm-router` `Scripts/_mcp_logging.py:_log_value`. The logging source is the right home because the block decides how a value is written into a log line. It reads no `log` and calls no logger, so it does not cross into what gets logged, the wire log of [[0011-a-truncated-payload-carries-the-first-cookie]], which stays out of the source. The source docstring now names it as the second block.

### Counts superseded

The statement under "The rule for a sixth source" that `_mcp_logging.py` holds one block is superseded, and so is thread 3 of the 2026-09-29 addendum, which lists it as a one-block source. The source now defines four blocks: `_configure_logging`, `_LOG_VALUE_WIDTH`, `_LOG_KEYS_SHOWN` and `_log_value`. `_mcp_concurrency.py` is the only one-block source left. As before, block count is not the test, and the source took the renderer because the renderer belongs to the same domain. The rendered per-source table in [[generated-regions]] is the authority for the current counts.

## Addendum (2026-10-07): the thirteenth registry entry, the stdlib HTTP server front

R-0072 added `Scripts/_mcp_httpfront.py` to the registry `Scripts/amalgamate.py:CANONICAL_NAMES` and to its hand mirror in `tests/test_generated_region.py`. This is a domain decided under this page's rule, not a source ratified after the fact ([[0029-the-http-front-is-a-domain]]).

### The count

The registry now holds thirteen names, counted in both tuples (the generator's and the suite's mirror) at `18f2b24`. This supersedes the twelve stated in the 2026-10-07 addendum on the OAuth source, which stays as written for its date. `sources-registered` checks the number, and `every-source-in-use` checks that a live region requests the new source; both were observed red before the registration.

### Why none of the existing sources holds it

- `Scripts/_mcp_chrome.py` answers how a client's bytes look like Chrome's. The HTTP front is the server side of HTTP/1.1: when a listener admits a connection, how long it waits for headers, which request lines it refuses before any handler runs. Filing it there would also put it out of its hosts' reach, because the Chrome source is whole and neither host takes the client.
- `Scripts/_mcp_oauth.py` opens one socket, the loopback redirect listener of one protocol, which reads one request head and answers one page. It says nothing about connection admission or a header deadline for a long-lived server.
- `Scripts/_mcp_logging.py` decides how a log line is written. The front's server members log, but the logger reaches them as a `self` seam, and when a server stops reading headers is not a logging question.

Under the rule above, a source that would hold a second domain is not cheaper than a second source.

### The price

One line in each of the two registries, as this page says it should be. No `WHOLE_SOURCES` entry and no `*_CORE` mirror: no block of the source names another, so each of its six regions per host refreshes on its own.
