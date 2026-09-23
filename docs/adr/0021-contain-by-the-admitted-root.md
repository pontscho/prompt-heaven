---
name: 0021-contain-by-the-admitted-root
type: adr
status: active
title: A walked file is contained by the root it was admitted under
description: Decision to measure search_for_pattern's per-file symlink gate against the root each search root was admitted under rather than against the project root, after the second silent zero in purity arrived through containment instead of a glob -- with the refusal that was implemented first and overruled, the gate-removal rejected on the way, the symlink-back-into-the-project drop kept on purpose, --strict left untouched, and a second, smaller decision that an empty or non-string element in a list of globs is refused.
sources:
  - Scripts/mcp-purity.py:handle_search_for_pattern
  - Scripts/mcp-purity.py:safe_path
  - Scripts/mcp-purity.py:_glob_list
  - Scripts/mcp-purity.py:_reject_brace_glob
  - ClaudeCode/skills/mcp-purity/SKILL.md
  - tests/test_purity_file_ops.py
verified:
  commit: 328285e
  date: 2026-09-23
links:
  - 0017-a-silent-zero-is-the-defect
  - 0010-a-handler-failure-must-reach-iserror
  - scripts
  - tests
---

# ADR 0021: A walked file is contained by the root it was admitted under

**Status:** accepted (implemented, `328285e`). Append-only from here. This is the
second silent zero in purity, after [[0017-a-silent-zero-is-the-defect]], and it
did not come through a glob, so it gets its own record rather than a paragraph in
a Decision that only ever covered matchers. The living WHAT/HOW is the
`_walk_roots` and `_glob_list` docstrings and the Security paragraph of
`ClaudeCode/skills/mcp-purity/SKILL.md`, described in [[scripts]].

## Context — the same wrong answer, through a different door

Without `--strict`, `safe_path` admits a path outside the project root for a
read-only handler, and has since `fa4ef2e`: that commit opened reads, search, list,
glob and the semantic functions outside the root and kept every destructive
function inside it `Scripts/mcp-purity.py:safe_path`. The admission is gated per
call by a context flag the dispatcher sets from an allowlist of handlers
`Scripts/mcp-purity.py:_ALLOW_OUTSIDE_ROOT` `Scripts/mcp-purity.py:_READONLY_HANDLERS`.
`read_file`, `list_dir` and `find_file` honoured it.

`search_for_pattern` admitted the root too, and then read none of it. Its walk
re-contains every file through `realpath`, so a file symlink that resolves out of
the tree is never opened (F6 / CWE-22) `Scripts/mcp-purity.py:handle_search_for_pattern`.
That gate measured every walked file against the **project** root. For an
out-of-root search root, every file fails that test by construction. Each was
dropped with a debug line, and the reply was `0 match(es)` for a scope that had not
been read, although the file held the needle.

That is 0017's defect exactly. A wrong absence is acted on: the caller concludes
the code is not there and reports it. 0017 found it in three glob matchers and
refused the question. This one came in through a containment check, which 0017's
Decision does not reach.

**With a list of roots it does not even look empty.** `relative_path` may be a list
for search. A list mixing an in-root root with an out-of-root one returned the
in-root hits and dropped the other root without a word. A partial answer is harder
to catch than an empty one, for the same reason 0017's `list_dir` measurement gave:
a caller who sees rows has no moment at which to suspect a missing root.

## Decision

1. **Each search root carries the bound its files are contained by.** The bound is
   the project root when the search root lies inside it, and the search root itself
   (already resolved by `safe_path`) otherwise. `_walk_roots` yields it beside every
   directory it walks `Scripts/mcp-purity.py:_walk_roots`, and the F6 gate measures
   each file against it `Scripts/mcp-purity.py:_path_within_root`. The rule is
   unchanged; only the root it is measured against moved.
2. **A file symlink escaping its own search root is still dropped.** Containment is
   kept, just re-anchored to what the call was actually given.
3. **`--strict` is untouched.** `safe_path` refuses an out-of-root or absolute path
   there before any walk begins, so the gate is never the thing that says no.

## Alternatives Evaluated

1. **Refuse an out-of-root search root.** This was implemented first, and it did
   remove the silent zero, by turning it into an error. *Overruled by the user:*
   it contradicts `fa4ef2e`, which deliberately opened non-destructive calls
   outside the root, and it adds no protection, because `read_file` returns the
   same file in one call `Scripts/mcp-purity.py:handle_read_file`. A search
   refusing what a read hands over is not a boundary. It is one handler
   disagreeing with the other three about where the boundary is.
2. **Drop the per-file gate for an out-of-root root.** *Rejected:* the gate is the
   only thing between a file symlink and whatever it points at. Without it, a link
   inside an admitted directory carries the walk anywhere the process can read,
   which is a scope the caller never named. Admitting a root is a decision about
   that root, not about everything reachable from it.

## Consequences

- **An out-of-root search root is searched.** As a file, as a directory, and as one
  element of a list beside an in-root root, which now answers from both.
- **Kept on purpose: in an out-of-root search, a symlink pointing back INTO the
  project is dropped too.** It leaves the root the search was given, and the
  project root is not that root. The bound is the root the call was given, with
  no exception for where a link lands.
- **The Security paragraph of the skill was false, and was corrected in the same
  commit.** It said absolute paths are always rejected. That had been untrue since
  `fa4ef2e`, and it now states the real rules `ClaudeCode/skills/mcp-purity/SKILL.md`.
- **The gate is one link and two roots.** Group K of `purity_file_ops` builds an
  out-of-root tree whose symlink escapes a search rooted at its inner directory but
  stays inside one rooted a level up `tests/test_purity_file_ops.py:make_outside_fixture`.
  The first search must drop the link and the second must read it
  `tests/test_purity_file_ops.py:group_k`. A drop with no matching read cannot tell
  an enforced boundary from a fixture that was never built. A second server
  child started with `--strict` pins the refusal. Every new case was run red
  before its fix. The suite's size is in the `SUITES` row of `tests/run.py`, not
  here.

## A second, smaller decision — an empty glob in a list is refused

The same commit let `search_for_pattern`'s `paths_include_glob` and
`paths_exclude_glob` take a list of strings as well as a string. The list is what a
ripgrep-taught caller writes, since `-g` repeats, and the reported call carrying one
died as a `TypeError` naming no parameter `Scripts/mcp-purity.py:_glob_list`.
Include keeps a file matching any element, exclude drops a file matching any
element, and an empty list means no filter.

**Inside a list, a non-string or an empty element is refused rather than skipped.**
Skipping is the tempting tolerant reading and it is the wrong one. An empty include
element matches nothing, so a list that is all empty elements, or a caller who
meant something there, gets 0017's silent zero back through a new parameter shape.
The refusal names the parameter and the element index. Every element still goes
through the brace rule, so a list is not a way around 0017 either
`Scripts/mcp-purity.py:_reject_brace_glob`. `find_file`'s mask and `list_dir`'s
filter stay one glob each. A non-string handed to either used to surface as a raw
`TypeError`, and is now a `ValueError` naming the parameter, which reaches the
caller with `isError` set through the existing path
([[0010-a-handler-failure-must-reach-iserror]]). All of this is pinned in-process by
group G `tests/test_purity_file_ops.py:group_g`, for the reason 0017 recorded:
over the wire, a refusal and an empty match can render alike.
