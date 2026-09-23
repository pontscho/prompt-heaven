---
name: 0020-the-prompt-is-its-own-block
type: adr
status: active
title: The activation prompt is its own block
description: Decision to promote the checkpoint's activation prompt from a ### subsection inside each session to its own ## ACTIVATION S<NNN> block with its own TOC row, so one command can resume and the reply can quote the file -- with the in-block alternative that was recommended and overruled, the four review findings that shaped the prepend shape gate, the read-only legacy fallback, and the three limits left declared. Supersedes the in-block ACTIVATION part of ADR 0009.
sources:
  - ClaudeCode/skills/checkpoint/scripts/checkpoint.py
  - ClaudeCode/skills/checkpoint/SKILL.md
  - tests/test_checkpoint.py
verified:
  commit: e8b4909
  date: 2026-09-23
links:
  - 0009-the-first-reader-is-a-cold-model
  - skills
  - tests
---

# ADR 0020: The activation prompt is its own block

**Status:** accepted (implemented, `0598b54`; `migrate` in `e8b4909`). Append-only
from here. Supersedes one part of [[0009-the-first-reader-is-a-cold-model]]:
the activation prompt as a `### ACTIVATION` subsection inside each SESSION block,
and resuming via `latest` + `mission`. Everything else in 0009 stands: English
throughout, the persisted TOC, one writer. The living WHAT/HOW is
`ClaudeCode/skills/checkpoint/SKILL.md` and
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py`, catalogued in [[skills]].

## Context

The activation prompt was the one part of the checkpoint a user acts on directly,
and it was the one part the script could not point at.

- **The script could not address the prompt.** It lived inside the session block,
  so `latest` printed it buried in the whole session and the TOC had no row for it.
  0009 made every block readable by offset; the prompt was the exception.
- **Resuming took two commands.** The template told a cold session to run
  `mission` and then `latest`. That is two calls and two chances to run only one.
- **The Step 6 echo was retyped.** The chat reply after a checkpoint was the
  prompt typed out again rather than read back from the file. That second copy
  can drift from the one the file carries, and the file is supposed to be the
  single source.

## Decision

1. The prompt becomes a top-level block, `## ACTIVATION S<NNN>`. It carries the id
   of the session it belongs to, there is exactly one per session, and it sits
   **immediately** below its SESSION block. Headers are recognised by
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:ACTIVATION_RE`.
   `next-number` still counts SESSION ids only.
2. The block gets its own TOC row, labelled `A<NNN>` and never `S<NNN>`. The labels
   feed the duplicate gate, so a session and its own prompt must not share one
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:toc_rows`. `session A<NNN>`
   reads that row back verbatim, which makes every TOC label a valid argument to
   the reader `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_session`.
   One id parser serves `session` and `activate`, so they cannot disagree on what
   `S042` means `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:parse_session_id`.
3. `nexts` prints MISSION, the newest SESSION and its ACTIVATION block, verbatim
   and in that order. It computes everything before printing anything, so a refusal
   prints nothing at all `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_nexts`.
   It is the one command the activation template now names.
4. `activate [ID]` prints the prompt paste-ready: the header is dropped, a leading
   `> ` or `>` is stripped from each line, and blank lines are trimmed at both ends
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:dequote`. The Step 6 reply
   pastes this output instead of retyping the prompt.
5. `prepend` enforces the shape on the new segment only, in every write mode
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:check_segment_shape`.
6. A file that is not UTF-8 is refused through `die` (exit 2) rather than crashing
   with a traceback `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:read_lines`.
   Every reader goes through that path, including the two new ones.

## The alternative that lost

**Keep the `### ACTIVATION` subsection inside the session and teach the script to
read it.** This was the recommended option. It needed no new TOC rows, no pairing
logic and no shape gate. `activate` would have been a subsection extractor, and
`nexts` would have been `mission` + `latest` joined together. The user overruled
it: the prompt should be formalised the way `MISSION` is, as a block of its own.
That is the only form that gives the prompt a TOC row, and so an offset, which is
0009's own reason for keeping a TOC in the file. The extractor did not go away: it
lives on as the read-only legacy fallback.

## What the review changed

The plan review found four defects in the first gate design, and each shaped the
gate that shipped:

- **Adjacency, not just pairing.** "Every session has an activation block
  somewhere in the segment" allowed SESSION S005, SESSION S006, ACTIVATION S005,
  ACTIVATION S006. The rule became: every SESSION S<n> is *immediately* followed by
  ACTIVATION S<n>, and every ACTIVATION S<n> is *immediately* preceded by its
  session.
- **Fence awareness.** A session that documents the format quotes `### ACTIVATION`
  inside a code fence. A naive scan refused that correct segment, and on an old
  file it would have handed the user the quoted line as their prompt. The
  `### ACTIVATION` scans skip fenced lines
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:unfenced`.
- **An empty prompt was accepted on write and refused on read.** The readers
  refuse an empty prompt, because exit 0 with nothing printed reads as "nothing to
  paste". The first gate still wrote one, so the defect would have surfaced only at
  resume time, when the append-only block can no longer be repaired. Both sides now
  judge emptiness through `dequote`, so a `>`-only body is empty on both.
- **Refusal order.** A segment can be wrong in more than one way, so the order the
  checks run in decides which error the user sees. `read_segment`'s existing checks
  run first. Then the shape checks: a misspelled header (otherwise it would be
  reported as its symptom, a session without its activation), the old in-block
  form, an id repeated inside the segment, adjacency, and an empty prompt. Last,
  and only when not overwriting, the duplicate check against the file. A segment
  that is both malformed and stale is therefore reported for the fault in the
  segment itself.

## Legacy fallback

Every checkpoint written before this change carries only the `### ACTIVATION`
form. When a session has no `## ACTIVATION` block, `activate` and `nexts` read that
subsection, read-only and fence-aware
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:legacy_activation`. In that
mode `nexts` prints only MISSION + SESSION, because the session already carries the
prompt and printing it twice would duplicate it. Content already in the file is
never shape-checked; only a new segment is held to the new shape.

The fallback reads the old form; it does not retire it. Retiring it takes a
command, `migrate`, because the alternative is a hand edit of every legacy block
plus a TOC nobody regenerated -- the two-writer path 0009 closed
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_migrate`. It moves each
legacy subsection into its own block below its session and writes once. That
makes it the one command that edits blocks already written, so the exception to
append-only is named where the rule is stated `ClaudeCode/skills/checkpoint/SKILL.md`,
and it is explicit and user-invoked, never part of a checkpoint. It moves lines and
never changes what a block says. A session it cannot convert unambiguously is
skipped and reported rather than half converted, and the one case review caught
converting halfway -- two subsections in one session, where the shared locator
stops at the second -- is among the skips
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:migrate_session`. Migrate and
the legacy reader find the subsection through the same locator, so `activate`
cannot disagree with what `migrate` moves
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:legacy_span`.

## Consequences

- A resume is one command, and the chat reply quotes the file rather than a
  retyped copy.
- The TOC grows by one row per session.
- A template written before the change now fails at `prepend` with a message that
  names the fix, instead of producing a file that is half in the old form.
- The test suite grew a group of its own for this, group L
  `tests/test_checkpoint.py:group_l`. Its size is in the `SUITES` row of
  `tests/run.py`, not here.

**Declared limits, kept on purpose:**

1. **`parse_blocks` does not know about fences.** A `## ` line inside a fence is
   still a block header, so a session cannot quote a `## ACTIVATION S0xx` or
   `## SESSION …` header even inside a fence: the quote becomes a real block and the
   shape gate refuses the segment. Changing this would move the block boundaries of
   files already on disk.
2. **The fence parser is looser than CommonMark.** Any indentation opens or closes
   a fence, and neither the closer's length nor an info string is checked, so a
   four-backtick fence can be closed by three. It only has to tell a quoted
   `### ACTIVATION` from a real one.
3. **The gate catches collisions, not gaps.** A guessed id that collides with an
   existing one is refused. A guessed id that skips one (S001, then S003) collides
   with nothing and goes through. Following `next-number` is still the caller's job.
