---
name: 0025-generate-do-not-import
type: adr
status: active
title: Generate the shared code into each file, do not import it
description: Retroactive record of the decision taken in 8effd2f that code the MCP fleet shares is generated into each single-file server between hashed markers rather than imported from a sibling module, with its four measured grounds, why Cog was not used, the hooks precedent, the re-check ADR 0023 made for a host that is not a server, the alternatives rejected, and the costs accepted.
sources:
  - Scripts/amalgamate.py
  - Scripts/_mcp_json.py
  - Scripts/_mcp_concurrency.py
  - Scripts/MCP_SKELETON.md
  - tests/test_generated_region.py
verified:
  commit: 0653fab
  date: 2026-09-29
links:
  - generated-regions
  - scripts
  - tests
  - 0009-the-first-reader-is-a-cold-model
  - 0014-a-canonical-source-is-a-domain
  - 0023-the-websocket-client-is-a-sixth-domain
---

# ADR 0025: Generate the shared code into each file, do not import it

**Status:** accepted, retroactively. The decision was taken and shipped in
`8effd2f` on 2026-09-09. It was argued in that commit's body and restated in
every canonical source's docstring, but it never had a page of its own.
[[0014-a-canonical-source-is-a-domain]] depends on it and says it does not
restate it. This page writes it down. It decides nothing new.

## Context

The MCP servers are single files. `8effd2f` measured the plumbing they
duplicate at about 3300 lines across 15 servers, a figure dated to that commit.
The wiki already recorded what the duplication costs: one broken read loop had
become thirteen, because the servers are what each other get copied from.

The obvious fix is one sibling module that every server imports. `8effd2f`
measured that fix and refused it.

## Decision

**Code the fleet shares is generated into each file that uses it. No server
imports a sibling.** A canonical source holds the code, and
`Scripts/amalgamate.py` pastes named blocks from it between a `BEGIN GENERATED`
and an `END GENERATED` marker. The END marker carries a hash of the body, so a
hand edit inside a region is refused, not overwritten. The pasted code is
committed, so a fresh clone runs with no build step and no `sys.path` entry
`Scripts/MCP_SKELETON.md:753-762`.

The canonical sources are imported exactly once: by the test fleet, which loads
each one as a module and exercises its blocks. A helper pasted into fifteen
files is unit-tested in one place `Scripts/_mcp_json.py:29-33`.

## The grounds

`8effd2f` gave four, each measured on the tree at that commit. The first three
are restated word for word in every canonical source, for example
`Scripts/_mcp_json.py:20-27` and `Scripts/_mcp_concurrency.py:49-56`, and in the
server skeleton `Scripts/MCP_SKELETON.md:753-762`.

1. **Bytecode.** The live servers launch with no `PYTHON*` environment
   variable, so the first sibling import writes `Scripts/__pycache__` into a
   tree the suites assert stays free of bytecode. The canonical sources now name
   the property, "a tree every suite that snapshots bytecode asserts stays
   empty", and not the count of suites, which `8effd2f` gave as four.
2. **`sys.path`.** The test harness loads each server with
   `spec_from_file_location`, which never extends `sys.path`. With an import,
   standalone suite runs would break while `forge test all` passed by accident.
3. **Module attributes.** `tests/test_mcp_footprint.py` reaches into each server
   for its helpers as module attributes and harvests string literals from its
   source. An import would move both out of all fifteen servers at once.
4. **The copied file.** A user who copies one server without its sibling gets
   an `ImportError`. This ground is not in the canonical docstrings. It is in the
   commit body, in ADR 0014's alternative 6, and in the generator's own comment
   on declared hosts `Scripts/amalgamate.py:DECLARED_HOSTS`.

## Why not Cog

The hash-in-the-END-marker idea is Cog's `-c` checksum, and the generator says
so `Scripts/amalgamate.py:20-25`. Cog itself was not used, because a
third-party tool this repo does not install degrades to a no-op. `8effd2f` gave
the live example: ruff was not on `PATH`, so the lint hook's ruff step did
nothing. A gate that does not run is worse than one that does not exist. The
generator is stdlib-only and lives in the tree, so it runs wherever the servers
do.

## The hooks precedent

`8effd2f` states that the repo had already rejected the same shape for the
hooks: a shared library beside them, for the same `ImportError` reason as
ground 4. That earlier rejection has no page or commit this record could find,
so the precedent rests on the `8effd2f` body alone. It is recorded here as that
commit's claim, not as a separate decision.

## Re-checked for a host that is not a server

[[0023-the-websocket-client-is-a-sixth-domain]] did not re-decide this, but it
had to re-check it. The DuckDuckGo search script took generated blocks, and it
is not a server. None of the grounds above is about servers as such, and two of
them apply to the script with more force. Agents run it by path and without
`-B`, so an imported sibling would write `Scripts/__pycache__` on every search.
A copy of the one file taken alone would stop at an `ImportError`. Both held, so
the script became a hand-declared host `Scripts/amalgamate.py:DECLARED_HOSTS`.
That re-check is the evidence that the rule is about single-file scripts, and
not only about the MCP fleet.

## Alternatives rejected

**1. One sibling module that every server imports.** Refused on the four grounds
above.

**2. Cog, or any third-party generator.** Refused because it degrades to a no-op
wherever it is not installed.

**3. Import the canonical source from a host that is not a server.** ADR 0023's
alternative 6, refused there on grounds 1 and 4: it writes `__pycache__` into
`Scripts/` on every search, and it breaks the single-file copy.

**4. Leave the duplication hand-written.** Refused because the thirteen-server
read-loop failure had already happened. Hand copies agree on disk only until
one is changed. The generator and its drift gate are what keep the copies
equal.

## Consequences

- **Duplication is the mechanism, not a defect.** Every host carries the code in
  full. [[generated-regions]] states this cost and shows what it buys.
- **Each file has one writer inside a region.** A hand edit is refused, not
  overwritten. [[0009-the-first-reader-is-a-cold-model]] reached the same
  one-writer conclusion for the checkpoint table of contents.
- **A shared helper must be pasteable.** It may read only builtins, the stdlib
  and names the host imports, because a pasted block cannot import on its own
  behalf. That is where the block contract and the tab rule come from. Both are
  mechanics of this decision, described in [[generated-regions]].
- **Adding a host is a deliberate edit.** A non-server script joins by name in
  `Scripts/amalgamate.py:DECLARED_HOSTS`, never by matching a glob.
- **The gate carries the rule.** `tests/test_generated_region.py` proves the
  copies match the canonical text and exercises each block once. Its module
  docstring states the single-file reason too.

## Addendum (2026-10-07): a second canonical source in a host that is not a server

`Scripts/llm-router.py` is the third declared host `Scripts/amalgamate.py:DECLARED_HOSTS`, and until its `codex` and `openai` kinds it took one block, `_configure_logging`. It now takes a second canonical source, `Scripts/_mcp_oauth.py`, on one marker that names the OAuth core and its two-block loopback wrapper. The declared-host tuple did not grow: it holds the same three names, because the router was already in it.

### The grounds, re-checked for the router

The router is run by path, as the DDG search script was when [[0023-the-websocket-client-is-a-sixth-domain]] re-checked this decision. Grounds 1 and 4 apply to it in the same way. An imported `_mcp_oauth.py` would write `Scripts/__pycache__` the first time the router started without `-B`. A copy of the router taken on its own would stop at an `ImportError` the moment a `codex` or `openai` backend, or the `login` subcommand, needed the OAuth code. Taking a second source changes neither answer, so the rule held for the router with no exception written for it.

### An injected transport and clock keep a protocol source host-neutral

The consequence above, that a shared helper must be pasteable, limits a block to builtins, the stdlib and names the host imports. A protocol client has two further dependencies that a pasted block cannot own without choosing them for every host: a transport and a clock. The OAuth source owns neither `Scripts/_mcp_oauth.py`:

- **The transport is the host's.** Every request builder returns `(url, headers, body)`, and the host sends it. In the router that is the single framing owner `Scripts/llm-router.py:_rt_send`, so the address policy, the trust store and the timeouts that guard the upstream traffic guard the token endpoint too. The alternative, a source that performs its own HTTP with `http.client`, was refused: it would have generated a second header-writing site and a second TLS and SSRF path into the host, outside the router's own static rules.
- **The clock is the host's.** No block reads the time. `now` is an argument, and the callback acceptor takes a `clock` callable. A test pins the time instead of sleeping through it, and the host keeps the one clock its own deadlines already use.

This is the rule the search sources already followed for their session, which is injected and never created `Scripts/_mcp_websearch.py:run_web`, now stated for a protocol source. Generation copies code, not policy, so a canonical source that would otherwise carry a policy takes it as an argument. A second host gets the protocol without the router's choices.

## Addendum (2026-10-07): Generation copies code, not policy -- for members taking self

R-0072 generated `Scripts/_mcp_httpfront.py` into `Scripts/mcp-proxy.py` and `Scripts/llm-router.py` ([[0029-the-http-front-is-a-domain]]). Six of its blocks are rendered as methods inside each host's server and handler class, override stdlib members and call `super()`. The previous addendum's rule, that a canonical source takes as an argument any policy it would otherwise carry, extends to them, with one refinement a method needs and a function does not.

### Three ways in, and one way out

A generated method can reach host policy in three ways only: through `self.timeout`, the stdlib handler's own attribute; through an argument evaluated at call time (the reader's `cap`, the token check's `min_len` and `error`, the ready file's `error` and `logger`); or through a small hand-written `_front_*` hook method in the host that reads the host constant at call time. What it may not do is read a class attribute that snapshots a module constant at class definition. The one exception is `front_log`, bound to the logger object rather than to a value.

### The measurement behind the rule

The router's in-process test rig `tests/test_llm_router.py:FastRouter` patches module constants with `setattr` while a test runs, and it patches `_RouterHandler.timeout` together with `_HTTP_HEADER_TIMEOUT_S`, because the stdlib's `setup` applies the class attribute. A new class attribute holding the header bound would not be patched: the deadline test B14 would then run `parse_request`'s per-recv bound at 10 s instead of the patched value, and stay green. So the generated `parse_request` reads `self.timeout`, the one attribute the rig already patches, and the router's static rule J40 clause (e) refuses any other class-level assignment reading an `_HTTP_` or `_TOKEN_` name. The logger is safe as a class attribute because the rig attaches its handler to the logger object and never rebinds the module's `log`.

This lift needed no hook method: the only members that would, `setup` and `handle_one_request`, stay hand-written in each host as declared adaptations. The rule is recorded in the source's docstring for the next member that does.
