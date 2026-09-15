---
name: 0017-a-silent-zero-is-the-defect
type: adr
status: active
title: A silent zero is the defect, not the glob
description: Decision to refuse a brace-alternation glob across all three of purity's matchers rather than answer it with an empty result, and to widen globstar to every position in the one matcher that promised it and delivered it only at position zero, with the three-engine census that reframed the question, the list_dir measurement that settled it, the six alternatives rejected, and the gate's declared blind spot.
sources:
  - Scripts/mcp-purity.py:_reject_brace_glob
  - Scripts/mcp-purity.py:_glob_matches
  - Scripts/mcp-purity.py:_compile_path_glob
  - ClaudeCode/skills/mcp-purity/SKILL.md
  - tests/test_purity_file_ops.py
verified:
  commit: 6894952
  date: 2026-09-15
links:
  - scripts
  - tests
  - 0003-the-trigger-travels-with-the-tool
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0014-a-canonical-source-is-a-domain
  - 0015-ambiguity-is-the-defect
  - 0016-a-cell-may-not-forge-a-boundary
---

# ADR 0017: A silent zero is the defect, not the glob

**Status:** accepted (implemented). Append-only — the WHY is frozen here; the
living WHAT/HOW is the `_reject_brace_glob` and `_glob_matches` docstrings and
the glob paragraph of the `purity_call` tool description.

## Context — one parameter name, three different questions

The handoff that opened this work described a single hazard in a single helper:
`fnmatch` has no globstar, so a caller's glob can silently match nothing. The
census found something else. **purity does not have one glob surface. It has
three, and the same glob text means three different things:**

| matcher | serves | matched against | globstar |
|---|---|---|---|
| `_glob_matches` (`Scripts/mcp-purity.py:1405`) | `search_for_pattern`, both globs | project-root-relative path **or** basename | position 0 only |
| `_compile_path_glob` (`Scripts/mcp-purity.py:1130`) | `find_file` path-style masks | **search-root**-relative path | real, every position |
| raw `fnmatch` (`Scripts/mcp-purity.py:1037`) | `list_dir`'s filter | bare name, files only | none |

The wiki had nothing to say: the token `globstar` was **unknown to the corpus**,
and the single hit cleared the relevance gate on coverage alone. The WHY did not
exist, so this rests on measurement rather than on a prior argument.

Two spellings, both measured against CPython's `fnmatch`:

- **Embedded globstar.** `fnmatch.translate("tests/**/*.py")` is
  `tests/(?>.*?/).*\.py` — an *atomic* group that demands a separator. So the
  pattern matches `tests/sub/foo.py` and silently misses `tests/foo.py`. The
  zero-directory case is exactly the one a caller writing `**` means to include.
  `find_file` is immune; its engine emits `(?:.*/)?`.
- **Brace alternation.** `fnmatch.translate("*.{js,py}")` is `.*\.\{js,py\}` —
  the braces are escaped to **literals**, and `_compile_path_glob`'s `re.escape`
  fallthrough does the same. `*.{js,py}` matches only a file actually named
  `x.{js,py}`. Broken in all three, and unfixable inside `fnmatch`.

The framing that settled the decision is not *which glob dialect should purity
speak.* It is **what a search tool may return when it cannot answer at all.**

A silent zero is the one wrong answer that propagates. A wrong match is noticed
— the caller reads the hit and discards it. A wrong *absence* is acted on: the
caller concludes the code does not exist and reports it. In this repo that
caller is usually a minion whose whole output is a finding, so an unmatchable
glob does not produce a bad search, it produces a confident false report.

`list_dir` sharpened this past argument and into measurement. Its filter applies
to files only, so directories bypass it unconditionally. The red run recorded a
brace filter returning `src/`, `tests/`, `src/deep/` — **a zero result that does
not even look empty.** No amount of documentation reaches a caller who was not
shown an empty list.

## Decision

1. **`**/` means "zero or more directories" at EVERY position** in
   `_glob_matches`, not only at position zero. Implemented by also trying the
   pattern with every `**/` removed. This *deletes* a special case: the helper
   already promised globstar semantics in its own docstring and delivered them
   only at the start of the string.
2. **Brace alternation is REFUSED** by all three glob-accepting handlers, via
   one shared `_reject_brace_glob`, with a `ValueError` naming the parameter,
   quoting the glob, and stating the two workarounds. Refusing costs one retry
   and cannot be misread; an empty list can only be misread.
3. **Only a brace group carrying a comma is refused.** A literal `{{...}}` in a
   filename stays matchable. The discrimination is asserted by a control case,
   not assumed.
4. **The three matchers are NOT unified.** See alternative 4.

The refusal rides the existing error path, so it reaches the caller with
`isError` set without new plumbing (ADR 0010).

## Alternatives Evaluated

1. **Document the hazard in `_glob_matches`'s docstring and stop** — the
   original mandate. *Rejected:* nobody who calls the tool reads that docstring.
   ADR 0003 and ADR 0016 both already decided that a rule the model must act on
   belongs where the model reads it. The census also found the shipped tool
   description asserting that `find_file`'s pattern "is fnmatch-style", which is
   **false** for the path-style branch and has been for as long as that branch
   has existed — so the documentation site was not merely silent, it was wrong.
2. **Implement brace expansion in all three matchers.** *Rejected:* a partially
   correct expander — nesting, escaping, empty alternatives — is a new bug
   surface inside a *matcher*, and a matcher's bugs are silent by construction.
   Nothing has asked for an alternation that a second call cannot serve.
3. **Ban every `{`.** *Rejected:* it would refuse a legitimate literal-brace
   filename to catch a spelling that always carries a comma. The narrower rule
   costs one regex and is covered by its own control.
4. **Unify the three matchers behind one engine.** *Rejected:* they answer three
   different questions. `search_for_pattern` matches the **project**-relative
   path *or* the basename, so a bare filename hits at any depth; `find_file`
   matches relative to the **search root**, which is what makes its
   `relative_path` mean anything; `list_dir` matches a bare name because it is
   listing one directory. The divergence is load-bearing, and ADR 0014's domain
   rule argues against collapsing three domains onto one shelf.
5. **Point `search_for_pattern` at `_compile_path_glob`.** *Rejected:* it would
   silently drop the basename-OR-path rule, which exists to fix a *different*
   documented footgun (a bare `requirements.yaml` matching only the root-level
   file). Trading one silent miss for another is not a fix.
6. **Keep the silent zero, raise the documentation ceiling instead.**
   *Rejected by the `list_dir` measurement above:* the caller is not always
   shown an empty result, so there is no moment at which the documentation gets
   consulted.

## Consequences

- **A behaviour change, deliberately.** A brace glob that previously returned an
  empty list now raises. This is a search tool refusing a question it cannot
  answer, which is the same move ADR 0015 made for alias collisions: the defect
  was never *which* answer to give, it was answering at all.
- **The three-matcher divergence is now stated on the wire**, in the
  every-request tool description and in the three `SKILL.md` parameter rows, so
  it is discoverable without reading the source.
- **The gate can only be written in-process, and that is a real blind spot.**
  Over the wire, a refusal and a glob that matched nothing render to the same
  shape, so a wire-level assertion would be asserting the defect. Group G of
  `purity_file_ops` therefore loads the server and calls `_glob_matches` and the
  three handlers directly. Nothing gates the rendering layer.
- **One divergence is declared, not fixed.** The `re.escape` fallthrough at
  `Scripts/mcp-purity.py:1155` turns `fnmatch` character classes into literals
  in the path-style branch while the bare-mask branch at `:1188` honours them,
  so `[ab].py` and `src/[ab].py` disagree inside one handler and the mask's slash
  is what picks the answer. It is recorded as an INFO row naming both lines.
  Fixing it means extending the translator, and nothing has been observed
  reaching for it.
- `purity_file_ops` grew from 39 cases to 53.

## Postscript — the gate was observed red, and the controls are the deliverable

Unlike the gate in ADR 0011's family, this one *could* be watched failing, and
was: five failures before the fix, one for the embedded globstar and one for each
of the four refusal sites.

The seven controls matter more than the five failures. (Seven, not the eight the
commit body claims: the eighth case that was green throughout is the one-or-more
directory half of the embedded globstar, which is the fix's own other half and
not a control at all. The commit body is left as written and corrected here.) A rule that widens `**/`
at every position is one careless step from turning a path-scoped glob into a
match-everything: two controls assert that `tests/**/*.py` still misses
`other/foo.py` and `a/tests/foo.py`, and both were green before the change and
after it. A gate that only proves the new thing works cannot tell you what the
new thing broke.
