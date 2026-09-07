---
name: 0009-the-first-reader-is-a-cold-model
type: adr
status: active
title: The checkpoint file's first reader is a cold model, not a human
description: Decision to write the whole checkpoint file in English regardless of the conversation language, to persist a script-generated line-range table of contents inside it so one block can be read by offset, and then to give the file exactly one writer -- a prepend command that lands the block and the regenerated table in a single atomic replace.
sources:
  - ClaudeCode/skills/checkpoint/SKILL.md
  - ClaudeCode/skills/checkpoint/scripts/checkpoint.py
  - tests/test_checkpoint.py
verified:
  commit: a98b3ea
  date: 2026-09-07
links:
  - skills
  - tests
---

# ADR 0009: The checkpoint file's first reader is a cold model

**Status:** accepted (implemented, `a98b3ea`). Append-only from here — the WHY is
frozen on this page; the living WHAT/HOW is the skill contract
`ClaudeCode/skills/checkpoint/SKILL.md` and its helper script
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py`, catalogued in [[skills]].

The freeze arrived with this promotion, and one detail of how the page got here is
worth keeping. An ADR is append-only *after acceptance*, and in this wiki that
event is the promotion — so while the page was still `draft`, the decision was
still taking shape, and the `prepend` command below (decided after the page was
first written) was edited into it rather than split off into a second ADR.
Splitting would have left a false claim standing here — that a skipped `toc --write`
is held back by discipline — and scattered one decision across two pages. The code
wins, so the page followed the code while it could; now it stops.

## Context

Three complaints about `.claude/tmp/checkpoint.md` that look unrelated share one
root: the rules were written for a human reader, while the file's primary reader —
and, on the write side, its only author — is a model. A cold one, resuming a work
stream from `checkpoint.py latest` / `mission`.

### The language rule pointed the wrong way

Critical Rule 8 used to *prescribe* the conversation's language for the body, on
the grounds that the file is "a human handoff, not project documentation", and it
said so in as many words: *"Do NOT translate the body to English just because the
content is technical"* (`ClaudeCode/skills/checkpoint/SKILL.md` at `c23c402`).
What that reasoning left out is the reader that actually consumes the file. Two
consequences follow, and both are properties of the corpus rather than matters of
taste:

- **The language tracks the conversation**, so in a long multi-block file the same
  work stream ends up documented partly in Hungarian and partly in English — the
  language drifts from block to block, in a file whose whole model is that blocks
  are uniform and self-contained.
- **A bilingual corpus halves what a search finds**, because the searcher has to
  guess which language the concept was written down in.

### A block could not be located without reading the whole file

The file is an append-only stack with the newest block on top. Every prepend
shifts the line number of every line beneath it, and nothing recorded where a
block begins and ends. A targeted read was therefore impossible: either run the
script, or read the whole file. At 150 blocks that is precisely the cost the
header-addressable model exists to avoid.

### Adding one block took four steps and two writers

The write side was split in half: `next-number` plus `toc` to read the insertion
line out of the file, the model synthesizing the block, `purity_call
insert_at_line` at that line, then `checkpoint.py toc --write` to regenerate the
table. Three seams, each a defect rather than a hypothetical — an insertion line
mistyped or already stale, a `toc --write` that never ran, and, between the two
writes, an inconsistent file that simply stays that way if the session ends there.
The first version of this decision accepted those seams and defended them with
rules; finishing the decision meant removing them.

## Decision

1. **The whole checkpoint file is English**, whatever language the conversation
   is in — `MISSION` and the `ACTIVATION` prompt included. Two narrow carve-outs:
   **verbatim quotes are never translated** (the `--note` value, a quoted user
   sentence, error output, commit messages, paths, identifiers), and the
   **user-facing chat reply** stays in the conversation's language, because it is
   not part of the file `ClaudeCode/skills/checkpoint/SKILL.md`.
2. **The file carries a generated TOC region** between marker comments
   (`<!-- TOC:BEGIN ... -->` / `<!-- TOC:END -->`), sitting between the H1 and the
   first block: a markdown table with the `Start` and `End` line of every
   `SESSION` block and of `MISSION` `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:toc_rows`.
   Both numbers are 1-indexed and inclusive, and `End` is the block's last
   non-empty line `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:end_of`, so
   `Read offset=Start limit=End-Start+1` lands exactly on the block and nothing
   else. The region is written by the script and never by a hand — by `prepend` on
   every checkpoint, or by `toc --write` for a retrofit or a repair, both of them
   rendering through one shared generator
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:with_toc`.
3. **`checkpoint.md` has exactly one writer: `checkpoint.py`.** A normal checkpoint
   is a single `prepend` call. The skill stages the block text into
   `.claude/tmp/session-block.md` with `purity_call create_text_file` — the only
   file it writes by hand — and `prepend` inserts that segment at the computed
   position and regenerates the TOC inside one `os.replace`
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_prepend`. This
   **restores the "one dumb write" invariant** rather than merely preserving it:
   the weaker wording an earlier version of this decision needed — one write *plus*
   one regenerated region — is gone, and with it the state where the block is in
   the file but the table still describes the old one. There is no second step left
   to forget `ClaudeCode/skills/checkpoint/SKILL.md`.
4. **The insertion line is computed by the script, never by the caller.** It is the
   current top block's own start line
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_prepend`; "just below the
   H1" would land inside the generated region. Hand-computing it was one of the
   three seams, so the caller no longer computes it at all.
5. **`prepend` refuses a segment whose ids already exist in the target** — a
   duplicate `S0xx`, or a second `## MISSION` against the write-once tail — matched
   on content against the whole file rather than just the top block
   `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:block_labels`. `--overwrite`
   suspends the gate deliberately: there the old content is being discarded anyway.
   The gate exists because the staging file survives between checkpoints, so a
   `prepend` run without a fresh synthesis would otherwise re-insert the previous
   block, silently, under a duplicate id.

## Alternatives Evaluated

### On the language axis

**Mixed language per section** — for instance `MODEL` and `NEXT` in English, `LOG`
in the conversation's language. Rejected: it yields a bilingual block, the worst
of both worlds. The drift survives, and the rule has to be re-decided at every
single section.

**Leave the `ACTIVATION` prompt in the conversation's language.** This was the
alternative actually offered, on the reasonable ground that a human is the one
who pastes `ACTIVATION`. Rejected for two reasons. A language island inside the
file weakens the rule itself — a language rule with an exception is not one
anybody can hold — and the premise that motivated it does not hold: a new
session's working language comes from that session's own instructions, not from
the language of the pasted text, so an English activation prompt does **not** flip
the next session to English.

### On the table-of-contents axis

**Option 1 — script-computed only** (`toc` prints to stdout, the file itself
unchanged).
- **Pros:** zero staleness risk — what is not in the file cannot go out of date;
  the append-only invariant stays literally untouched; and materially less code:
  no write path, no atomicity, no `strip_toc`, no offset arithmetic.
- **Cons:** the ranges exist only when somebody runs the script. A file opened in
  an editor, and a cold agent that works through `Read`, get nothing at all — and
  script-free targeted reading is the entire point of the feature. This was the
  serious contender.

**Option 2 — TOC in the file, converged by fixed-point iteration**: write it,
recompute the line numbers, repeat until stable. Rejected because it solves a
problem that does not exist. The region's **height** depends only on the **number**
of rows, never on the **values** of the line numbers
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:render_toc`. Two renders are
therefore sufficient — the first measures the region's height, the second renders
with that height as its offset
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:with_toc` — and convergence is
not a question, because there is no loop. Iteration would introduce a convergence
question where plain arithmetic already sits.

**Option 3 — TOC at the end of the file, below `MISSION`**, so that it does not
shift the blocks. Rejected: it buys nothing. The prepend happens at the *front* of
the file, so every range shifts on every checkpoint regardless; this would merely
move the table of contents to where nobody looks for it.

**Chosen — TOC in the file, two renders, atomic write.** It keeps the accuracy of
the script-computed variant, because the table is re-derived from the file on
every write `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:strip_toc`, and
adds the one thing only an in-file table can offer: a targeted read without
running the script.

### On closing the stale-TOC gap

Once the table lived in the file, a checkpoint could carry one that nobody had
regenerated. Three ways to stop that, and they differ in *what* they promise.

**Option 1 — a PostToolUse hook** watching writes to `checkpoint.md` and running
`toc --write` afterwards, leaving both the skill and the script untouched.
Rejected: the protection would then hang on the hook's **registration** — a manual
step in the local settings, the way `ClaudeCode/hooks/sbx-gate.py` is wired — so
the discipline would move rather than disappear. And it does not cover the
miscomputed insertion line at all: by the time the hook runs, the block is already
in the wrong place.

**Option 2 — `toc --check`**, exiting non-zero when the table in the file disagrees
with a fresh computation, wired into the fleet or CI. Rejected: it **detects after
the fact instead of preventing**, and a false table goes on looking authoritative
until somebody runs the check. It was the smallest change of the three and the
weakest guarantee. An earlier version of this page named exactly this as the
obvious continuation; the gap was closed by a different route, so that sentence is
gone rather than fulfilled.

**Chosen — the script takes over the write.** It removes the error's *possibility*
instead of reporting its symptom. It is the only one of the three that also rules
out a wrong insertion line, and the only one that needs no external registration to
be in force.

## Consequences

- **Positive:** a single block is addressable with `Read offset/limit`; the file
  is navigable raw, in an editor or on a diff view; the language no longer drifts
  block to block, and a single-language corpus is searchable. The insertion line
  is now a computed value rather than an estimate, which retires the failure mode
  the old "just below the H1" wording carried.
- **The tool-routing rule got simpler, not harder.** It used to read "purity for
  every file op, except the TOC"; it now reads "`checkpoint.md` is written by
  `checkpoint.py`, and `purity_call` writes the staging file"
  `ClaudeCode/skills/checkpoint/SKILL.md`. Two failure modes stopped being
  *possible* rather than merely discouraged — a skipped `toc --write` and a
  hand-computed insertion line — and one genuinely new one took their place:
  reaching for `purity_call` out of reflex and thereby walking back onto the
  two-writer path.
- **`toc --write` survives, for the two cases outside the normal flow:**
  retrofitting a file written before the region existed, and repairing a region
  somebody edited by hand `ClaudeCode/skills/checkpoint/SKILL.md`. It cannot drift
  away from `prepend`, because both call the same single generator
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:with_toc`. In the same spirit
  `prepend` **echoes the region it just wrote** rather than rendering a second time
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:toc_region_of`, so its stdout
  and the table in the file are the same bytes by construction, not by agreement —
  which is also where the skill's user-facing reply gets the new block's line range,
  with no extra call.
- **The duplicate-id gate turns one more discipline item into a machine gate — and
  it is worth being exact about which one.** "Wrong session id" used to be a rule
  in prose; a *collision* is now refused before anything is written. But the gate
  catches collisions, not **gaps**: `S001` followed by `S003` still goes through
  `ClaudeCode/skills/checkpoint/SKILL.md`.
- **Why two calls and not one.** A block is some sixty lines of markdown carrying a
  `>` blockquote (the `ACTIVATION` prompt), backticks, and pipes inside the `FILES`
  table. Handing that to a CLI argument is shell-quoting roulette — `>` is a
  redirect character — and heredocs and redirects are forbidden here by project
  rule, so the text *has* to travel through a file. What the question did change is
  the ergonomics: `--block-file` carries a default, so the script call needs no path
  argument at all
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:DEFAULT_BLOCK_FILE`.
- **The staging file's name is load-bearing.** It is `session-block.md`, and it
  deliberately does not share the `checkpoint` stem: the target is
  `.claude/tmp/checkpoint.md` and the atomic write stages through `.checkpoint-*.tmp`
  in that same directory, so a `checkpoint-`prefixed staging file would have sat one
  glob away from being mistaken for the real thing. The suite pins it — one case
  asserts the default basename does not start with `checkpoint`
  `tests/test_checkpoint.py`.
- **`list` is now an alias for `toc`**
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:build_parser`. Its output
  changed — a markdown table instead of raw header lines — but it is a superset,
  and that is exactly why the command was kept: activation prompts written into
  earlier checkpoints call `list`, and those have to keep working.
- **This is the script's first writing operation — and, since `prepend`, its only
  one.** Hence the atomic swap (`tempfile.mkstemp` + `os.replace`) and the preserved
  file mode `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:write_atomic`: the
  target file is the user's single context artifact, and a half-written checkpoint
  is worse than a stale one. The same atomicity is what lets `prepend` promise that
  the block and the table it describes either both land or neither does
  `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_prepend`.
- **Test position:** `checkpoint.py` had no test suite whatsoever. One landed with
  this change — `tests/test_checkpoint.py`, registered in the `SUITES` table whose
  declared count the runner asserts against the actual run `tests/run.py`, with a
  matching `checkpoint` forge target that requires the `syntax` prerequisite like
  every other suite `project-forge.yaml`. It drives the script's writer
  exclusively inside a `mkdtemp` sandbox, and its last group digests the live
  `.claude/tmp/checkpoint.md` before and after the run, failing if a single byte
  moved `tests/test_checkpoint.py`. `prepend` brought its own group — the atomicity
  (asserted so that an inert write cannot pass, because the region must actually
  have *changed*), the inserted block's range, the byte-identity of the older blocks
  and the `MISSION` tail, a following `toc --write` as a byte-for-byte no-op, the
  fresh and `--overwrite` paths, and every segment refusal
  `tests/test_checkpoint.py`. The [[tests]] roster carries the suite. What was
  **not** closed is the gap
  underneath: the `syntax` target compiles only what it globs — `Scripts`, `tests`
  and `ClaudeCode/hooks` `project-forge.yaml` — so the `ClaudeCode/skills/**` tree
  stays outside it and a syntax error in this script still passes a fully green
  fleet. That is a separate decision.
- **Not changed:** the append-only block discipline, the write-once `MISSION`
  rule, the NO EMOJI rule, and the header contract of level plus UPPERCASE prefix
  token `ClaudeCode/skills/checkpoint/SKILL.md`.

### Found by measurement, not by review

The suite caught four real defects in freshly reviewed code. The first two are
properties of this decision rather than ordinary slips; the last two were
imprecisions in the design of the very gates that close it.

**The region markers were matched against the whole file.** A session block that
*quotes* the marker comments therefore counted as the region — and the very first
block, the one documenting this feature, is exactly such a block. `toc --write`
exited 0 and silently deleted three lines from the middle of an immutable block:
the documentation would have destroyed itself. The fix bounds the marker search at
the first `## ` line `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:preamble_end`,
which is precisely where the contract puts the region anyway — between the H1 and
the first block `ClaudeCode/skills/checkpoint/scripts/checkpoint.py:strip_toc`.
Pinned by group F, `f-a-marker-quoted-in-a-block-is-not-the-region`, and again on
the `prepend` path, where that same self-documenting block is inserted for real —
`i-a-segment-quoting-the-markers-survives` `tests/test_checkpoint.py`. The lesson
generalizes past this file: the pattern "a generated region is delimited by
markers" fails on its own documentation unless the marker search has a bound.

**Reading and writing used the locale's encoding.** Under `LC_ALL=C` the script
raised `UnicodeDecodeError` on a checkpoint whose entire non-ASCII content was the
word `árvíztűrő`. That is not a contrived case, and it connects straight back to
the *language* half of this decision: Rule 8a **guarantees** non-English verbatim
quotes inside the file, while every `toc --write` re-encodes the whole document. On
a lenient but non-UTF-8 platform it would have silently rewritten bytes *outside*
the TOC region too — breaking the exact byte-preservation contract this decision
promises. The fix is an explicit `encoding="utf-8"` on both sides
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:read_lines`
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:write_atomic`, pinned by group
D under a deliberately non-UTF-8 locale — `LC_ALL=C` with PEP 538 coercion and PEP
540 UTF-8 mode both switched off `tests/test_checkpoint.py`. The lesson: deciding
that the file is English does not remove non-ASCII content from it, it only
confines it to the sanctioned part. Explicit encoding is therefore not incidental
hygiene but a consequence of the language decision.

**`os.path.abspath` was not enough for the self-target check.** `abspath` does not
resolve symlinks, so a symlinked path naming the same inode would have slipped past
the very gate meant to stop `--block-file` and `--file` from pointing at one file —
which would have read the whole checkpoint and inserted a copy of it into itself.
Now `realpath`, a strict superset in every case that is not a symlink
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:read_segment`.

**The `--overwrite` path skipped `require_file`.** A target that was a directory
reached `os.replace` and produced a traceback with exit 1 — reopening precisely the
second error path that the earlier `require_file` work had closed one round before.
Both paths are gated now
`ClaudeCode/skills/checkpoint/scripts/checkpoint.py:cmd_prepend`. Two of these four
defects are the same shape: a guard that was *nearly* right, and only a written
case could tell the difference.
