---
name: checkpoint
description: Persist the current session into `.claude/tmp/checkpoint.md` as a VERY detailed handoff document, then return a copy-paste activation prompt the user can use to resume the work in a fresh session. Prepends a new session block to the TOP of the file by default (append-only -- prior session blocks are never rewritten); only overwrites the whole file when the user explicitly asks for it. The file itself is ALWAYS written in English regardless of the conversation language, and carries a script-generated table of contents giving each block's start and end line.
model: opus
---

# Checkpoint Command

Capture the **entire useful state of the current session** into `.claude/tmp/checkpoint.md` and hand the user a short activation prompt they can paste into a new session to continue exactly where we left off.

This is a **session continuity tool**, not a memory tool. The AI Soul memory system (`/p:recap`) is for long-lived facts and preferences. `/p:checkpoint` is for *this specific piece of work* -- the in-flight context that would otherwise be lost when the session ends.

## The file model -- append-only, newest on top, header-addressable

The checkpoint is an **append-only stack of self-contained session blocks** above a **frozen mission tail**. Every section is a markdown header whose LEVEL and ENGLISH PREFIX TOKEN identify it, so a tiny helper script (`~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py`) can return any section without guessing byte offsets, and so a cold resume can pull only the sections it needs instead of reading a 150-block file whole. Read this before anything else -- it governs every rule below.

```
# Session Checkpoint                              <- H1 title, line 1, never grows

<!-- TOC:BEGIN ... -->                           <- generated region; rewritten after every write
| Session | When             | Branch | Start | End |
|---------|------------------|--------|-------|-----|
| S002    | 2026-08-04 12:30 | master |    10 |  94 |
| S001    | 2026-08-04 11:44 | master |    96 | 150 |
| MISSION |                  |        |   152 | 168 |
<!-- TOC:END -->

## SESSION S002 | 2026-08-04 12:30 | master      <- newest block; every checkpoint inserts here
### LOG            what happened this session
### FILES          files touched (table)
### DECISIONS      decisions this session
### STATE          where we stand now (authoritative in the TOP block)
### THREADS        open threads / risks
### NEXT           next steps (ordered)
### MODEL          mental model / non-obvious context
### ACTIVATION     activation prompt (plain text, no emoji)

## SESSION S001 | 2026-08-04 11:44 | master
### LOG
...

## MISSION                                       <- written once in S001, NEVER rewritten
### WHY
### SCOPE
```

**Header contract (the script depends on it -- do not drift):**

- `#` -- the one-line file title. Exactly one, always line 1.
- The **TOC region** -- marker comments plus a markdown table, between the H1 and the first block. It is GENERATED -- by `prepend` on every checkpoint, or by `toc --write` for a retrofit -- never hand-written, and it must never contain a `## ` line: the parser treats everything above the first `## ` as preamble, which is exactly what keeps the TOC invisible to `latest` / `session` / `mission`.
- `##` -- block level. Prefix `SESSION S<NNN> | <YYYY-MM-DD HH:MM> | <branch>` for a session block, or `MISSION` for the frozen tail. Fields are pipe-separated ASCII so `toc` can tabulate them -- which is also why a pipe must never appear inside a field.
- `###` -- subsection level, with a stable UPPERCASE ENGLISH prefix token: `LOG`, `FILES`, `DECISIONS`, `STATE`, `THREADS`, `NEXT`, `MODEL`, `ACTIVATION` inside a SESSION block; `WHY`, `SCOPE` inside MISSION. The prefix is a machine label; the body under it is written in English too (Critical Rule 8).
- Session ids are zero-padded to three digits (`S001`..`S999`) so they sort and grep cleanly and are easy to cite ("see S042").

Three properties make this cheap and safe, and every rule exists to protect them:

1. **One dumb write.** A normal checkpoint is a single `checkpoint.py prepend` call, which inserts the new block AND regenerates the TOC in one atomic replace. Nothing else moves: every existing block stays byte-for-byte as written, and no session block is ever regenerated or reconciled -- that is what made the old merge model slow and error-prone. The TOC is the only derived thing in the file, a script derives it, and there is no state in which the block is in but the table is stale, because there is no second step to forget.
2. **The append-only log never lies.** A block, once written, is immutable -- what it records happened, and it is never overwritten. There is no mutable "current state" section that can drift; the derived state lives inside the newest block by convention.
3. **Recency is truth.** The top-most block's `STATE` and `NEXT` are authoritative. Older blocks are historical archive. When two blocks disagree, the top one wins.

## The helper script -- ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py

Stdlib-only markdown section reader, and the ONLY writer of the TOC region. Use it INSTEAD OF guessing offsets or reading the whole file. Commands:

```
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py prepend           # insert the staged block + regenerate the TOC in ONE atomic write -- the ONLY way to add a session
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py next-number      # next session id (S003), or S001 if the file is missing/empty -- WRITE side
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py latest           # the newest SESSION block, whole -- the primary resume payload
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py mission          # the frozen MISSION block -- the "why"
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py toc              # table of contents: id | when | branch | start | end
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py toc --write      # retrofit or repair the TOC region -- NOT part of a normal checkpoint
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py list             # alias for `toc` -- kept so older activation prompts keep working
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py session S042     # one specific block by id -- on-demand history
```

Default target is `.claude/tmp/checkpoint.md`; override with `--file PATH`. This is why the header contract must not drift: the script keys entirely off header level + prefix.

**The TOC, and why it is in the file at all.** `toc` derives every block's start and end line from the file, so what it prints is never stale. The write path -- `prepend`, or `toc --write` for a retrofit -- additionally persists that table into the file, because the two readers are not the same reader. An agent that runs the script gets the ranges either way; a human opening the file in an editor, and a cold agent that would rather `Read offset=<Start> limit=<End - Start + 1>` than shell out, only get them if the table is IN the file. Both line numbers are 1-indexed and inclusive, and `End` excludes trailing blank lines, so that `limit` lands exactly on the block and nothing else.

Every prepend shifts every line number below it, which is exactly why the insert and the regeneration are the SAME command. `prepend` does both inside one atomic replace, so "a TOC nobody regenerated" is not a state this skill can reach. It was reachable when the two were separate steps -- an authoritative-looking table that nobody had refreshed -- and closing that hole is the whole reason `prepend` exists. `toc --write` survives only for the two cases outside the normal flow: retrofitting a file written before the region existed, and repairing one that was edited by hand. Both writes are atomic (temp file + `os.replace`) and touch ONLY the lines between the markers -- every byte of every session block is preserved.

## Usage

```
/p:checkpoint                # prepend a new session block to the top (or create the file if missing)
/p:checkpoint --overwrite    # replace the whole checkpoint.md from scratch
/p:checkpoint --note "..."    # attach a short user-supplied note to this session's block
```

Aliases for `--overwrite`: `--force`, `--fresh`, `felulir`, `felulirni`, `ujra`.

## Critical Rules

1. **NO EMOJIS -- STRICT, WHOLE FILE.** The checkpoint file and every activation prompt in it MUST be plain text with zero emojis or non-ASCII pictographs. Activation prompts get pasted straight into a terminal, where an emoji can corrupt the input line and break it -- but the ban is not limited to them: the whole file stays emoji-free so nothing ever leaks into a pasted block, and so this skill never contradicts itself by example. Strip every emoji from mission / status / next-step / decision text before writing it.
2. **Temporary files location -- STRICT**: the checkpoint file MUST live at `.claude/tmp/checkpoint.md`. NEVER write it anywhere else (no `/tmp/`, no project root, no `docs/`). The write tool creates `.claude/tmp/` if it does not exist -- do NOT `mkdir`.
3. **Default is PREPEND, never rewrite prior blocks**: if `.claude/tmp/checkpoint.md` already exists, insert ONE new session block at the top and leave every existing block and the mission tail byte-for-byte untouched. Learn the next session id from `checkpoint.py next-number`, not by regenerating the file.
4. **Overwrite only on explicit request**: only rebuild the whole file when the user passes `--overwrite` / `--force` / `--fresh` (or the Hungarian equivalents above). If unsure, ASK before overwriting -- never silently destroy a working checkpoint.
5. **The mission tail is write-once**: the `## MISSION` tail is written in S001 and NEVER rewritten. If the mission is clarified mid-stream, record that clarification inside the current session block (under `LOG` or `DECISIONS`), not by editing the frozen tail. This keeps the append-only invariant exception-free.
6. **Header contract is load-bearing**: emit exactly the header levels and prefix tokens from the file model above. The helper script parses them literally; a renamed or re-leveled header silently breaks `latest` / `session` / `mission`.
7. **Tool routing -- MANDATORY.** `checkpoint.md` has exactly ONE writer: `checkpoint.py`. Never write, edit or patch it with `purity_call`, with built-in Write/Edit, or with anything else -- not the blocks, and least of all the TOC, whose line numbers are computed from the file and are therefore wrong the moment they are typed by hand. What `mcp-purity` writes is the BLOCK FILE: `create_text_file` the new block's text under `.claude/tmp/`, then hand that path to `prepend`. Everything else routes as usual: `mcp-purity` for file ops, built-in Read for reading, `mcp-git` for status/log/diff, and no Bash for `git status` / `git log` / `git diff`.
8. **Language -- ENGLISH, WHOLE FILE, NO EXCEPTIONS.** Every line written into `checkpoint.md` is English, whatever language the conversation is in: `LOG`, `FILES`, `DECISIONS`, `STATE`, `THREADS`, `NEXT`, `MODEL`, `MISSION` and the `ACTIVATION` prompt alike. The file is a machine-reread artifact, not a chat message -- one language keeps it greppable, keeps a cold model on its strongest footing, and stops the language from drifting block to block when the conversation switches. Two carve-outs, both narrow: (a) **verbatim quotes are never translated** -- the `--note` value, a quoted user sentence, error output, commit messages, paths and identifiers stay exactly as they are; add a short English gloss beside a non-English quote if its meaning is not obvious; (b) the **Step 6 chat reply** stays in the conversation language, because that is a message to the user, not part of the file. Do NOT write the body in Hungarian just because the conversation is Hungarian -- and do not worry that an English `ACTIVATION` prompt will flip the next session to English: the working language comes from that session's own instructions, not from the language of the pasted text.
9. **Be VERY detailed**: the whole point is that the next session can pick up cold. Err on the side of more context, not less. The user explicitly asked for *NAGYON reszletes* -- honor that.
10. **No code modifications**: this command is READ-ONLY against the codebase. It only writes the single checkpoint file.

## Workflow

### Step 1 -- Parse arguments

- Detect `--overwrite` / `--force` / `--fresh` / `felulir*` / `ujra` flags in the user's invocation.
- Detect `--note "..."` and capture the note text verbatim.
- If no flag is present, default mode = PREPEND.

### Step 2 -- Inspect existing state

In parallel (single message, multiple tool calls):

1. `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py next-number` -- the id for the new block. `S001` means the file is missing or empty, so this doubles as the create-vs-prepend check; no separate existence probe is needed. You do NOT need the block's insertion line -- `prepend` computes it.
2. `mcp-git status` (porcelain) to capture working-tree state.
3. `mcp-git log` of the last 10 commits on the current branch.
4. `mcp-git diff` (unstaged + staged) -- short stat first, then full diff if not too large.

Only if you are in fresh/overwrite mode do you also need the frozen mission context; get it with `checkpoint.py mission`. Do NOT `mkdir` -- the write tool creates parent dirs.

### Step 3 -- Decide the write mode

All three write modes are the SAME command -- `prepend --block-file <path>`. What differs is only what goes into the block file, and whether `--overwrite` is passed:

- **No existing file** -> the block file holds the `S001` block AND the `## MISSION` tail (a segment may contain several `## ` blocks). No flag needed: `prepend` creates the file, writes the H1 title, and generates the TOC region itself.
- **Existing file + overwrite flag** -> same fresh content in the block file (`S001` + mission tail), plus `--overwrite`, which replaces everything below the H1. Mention in the output that the previous checkpoint was overwritten.
- **Existing file + NO overwrite flag** -> the block file holds ONE new `## SESSION <next-number>` block. `prepend` places it above the current top block and leaves everything else -- older blocks, mission tail -- byte-for-byte. You do NOT compute the insertion line; hand-computing it was the old failure mode.
- **Ambiguous case** (file exists, user invocation is ambiguous in the conversation language): ASK before overwriting. One short question, default to "prepend" if no clear answer.

### Step 4 -- Synthesize THIS session into one block

Walk the current session start to end and distill it into the single new block. You are NOT reconciling the whole document -- only capturing this session's delta plus an authoritative snapshot of where the work stands as of now. Map each subsection to its header:

- **LOG**: what the user asked, what was done, what came out of it -- turn by turn.
- **FILES**: every file written/edited/read that mattered, with status.
- **DECISIONS**: every choice made (architectural, scope, library, naming, deferred work), with the reason and who made it. If a prior decision was superseded this session, say so here -- do NOT edit the old block.
- **STATE**: done / in progress / not started / blocked, true as of the end of this session. The top block owns this snapshot.
- **THREADS**: anything flagged or unresolved, each item actionable.
- **NEXT**: the concrete, ordered TODO list.
- **MODEL**: things learned this session that are NOT obvious from the code alone (ownership, invariants, generated targets, tool gotchas). Highest-leverage content for a cold resume.

If the session is too long to summarize in the main context, delegate the conversation review to `p:minion-explorer`; otherwise do it inline.

### Step 5 -- Write the block

Two calls, always the same two, in this order:

1. `purity_call create_text_file` the block text to `.claude/tmp/session-block.md` -- the `--block-file` default, and the only file you write by hand. The name deliberately avoids the `checkpoint` stem: the target is `.claude/tmp/checkpoint.md` and the atomic write stages through `.checkpoint-*.tmp`, so a `checkpoint-`prefixed staging file in the same directory would be one glob away from being mistaken for the real thing.
2. `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py prepend` -- plus `--overwrite` in overwrite mode. No path argument: the default is the file you just staged.

The second call inserts the segment at the right line and regenerates the TOC inside ONE atomic replace, then prints the fresh table -- which is where Step 6 gets the new block's line range, with no extra call needed.

`prepend` refuses, exits 2, and leaves the target untouched if the segment does not start with a `## ` header, contains a `# ` H1 line, is empty, if `--block-file` and `--file` resolve to the same path, or if any id in the segment ALREADY EXISTS in the target -- a duplicate `S0xx`, or a second `## MISSION`. That last gate is what makes a stale block file harmless: the block file survives between checkpoints, so a `prepend` that ran without a fresh step 1 above would otherwise re-insert the previous block, silently and with a duplicate id. A refusal means the BLOCK FILE is wrong: fix it and call again. Never route around a refusal by writing `checkpoint.md` yourself (Critical Rule 7).

Each session block is self-contained -- it carries its own `STATE` snapshot and its own `ACTIVATION` prompt, so the newest block alone is enough to resume from. Emit every `###` subsection in order; if one has no content, write the heading and `_(none)_` rather than dropping it, so blocks stay uniform and the script always finds them.

```markdown
## SESSION S002 | 2026-08-04 12:30 | master
_note: <--note value, omit this line if none>_

### LOG
1. <turn-level summary: what was asked, what was done, what came out of it>
2. ...

### FILES
| Path | What | Status |
|------|------|--------|
| `path/to/file` | brief description | created / modified / deleted / read |

### DECISIONS
1. <decision> -- *Why:* <reason> -- *Source:* <user / agent>. If it supersedes an earlier one, name which and why.
2. ...

### STATE
- **Done:** <milestones, with evidence -- commit hash, file path>
- **In progress:** <items being worked, with file:line anchors>
- **Not started:** <planned but untouched>
- **Blocked:** <waiting on decision / dependency, with the blocker named>

### THREADS
- <actionable: "ask the user X", "investigate Y", "decide A vs B", "edge case Z not handled">

### NEXT
1. <specific -- "add NULL check at src/foo.c:142 before dereferencing cfg->handler", not "fix the bug">
2. ...

### MODEL
- <implicit knowledge built this session, e.g. "forge_call needs function + params top-level keys; targets=[...] at top level silently fails">

### ACTIVATION
> Resuming the work started on branch `<branch>`. The context lives in `.claude/tmp/checkpoint.md`.
> Pull the essentials:
> `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py mission` and `... latest`.
> For details, same script: `toc`, then `session S0xx`.
> In short: <one-sentence mission>. Last state: <one-sentence status>.
> Next step: <one-sentence next step>. If anything is unclear, ask -- do not guess.
```

The **mission tail** (written once in fresh/overwrite mode, then frozen forever):

```markdown
## MISSION

<1-3 paragraphs: what does the user ultimately want in this work stream? Be specific,
reference the user's original phrasing where useful.>

### WHY
<Constraints, past incidents, deadlines, stakeholder asks -- whatever explains the motivation.>

### SCOPE
- **In scope:** <bullets>
- **Out of scope:** <bullets -- explicit non-goals to prevent scope creep>
```

### Step 6 -- Output to the user

After writing, produce a SHORT user-facing message in the conversation language. This reply is the ONE thing that is not English (Critical Rule 8b) -- the file it describes is:

```
**Checkpoint frissitve:** `.claude/tmp/checkpoint.md`
**Mod:** prepend (S0xx) | overwrite | new
**Blokk:** S0xx = <Start>-<End>. sor (a `prepend` kimenetebol) | **Fajl:** ~<N> sor, ~<M> kB

**Aktivalo prompt -- masold be uj sessionbe:**

```
<the verbatim ACTIVATION block from the block you just wrote>
```

**Tipp:** uj sessionben eloszor `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py latest` (es `... mission`) -- ez adja a lenyeget a teljes fajl beolvasasa nelkul; `... toc` a blokk-terkep, ha egy regebbi sessionra kell visszanezni.
```

The triple-backtick wrapping around the activation prompt is intentional -- it makes copy-paste trivial. The activation prompt you echo here is the one from the block you just prepended (the new top block), never an older one.

## Quality bar

- **Detail level**: a competent dev who has NEVER seen this session should be able to resume from `checkpoint.py latest` + `checkpoint.py mission` alone, without re-reading the prior conversation.
- **Self-contained blocks**: the newest block must stand on its own -- `STATE`, `NEXT`, and `ACTIVATION` all inside it. Do not write a block that only makes sense once you have read the ones below it.
- **Honesty over polish**: if something is half-done or unclear, say so. Do not paper over gaps with optimistic phrasing.
- **No invented progress**: only document what actually happened. A file planned but not written goes under `STATE` "Not started" / `THREADS`, NOT under "Done".
- **Append-only discipline**: never rewrite, reconcile, or reorder an existing block. Superseded decisions are annotated in the NEW block, not edited in the old one.

## What this command is NOT

- It is NOT `/p:recap` -- it does not propose long-lived memories.
- It is NOT a PR description -- it does not summarize for an external audience.
- It is NOT a commit message -- it captures *in-flight* state, including half-done work.
- It is NOT a CLAUDE.md update -- it is per-task ephemeral context, not project-wide guidance.

## Failure modes to avoid

1. **Rewriting the whole file on a normal checkpoint** -> a normal checkpoint is ONE `prepend` call. Regenerating the document is the slow, lossy anti-pattern this model exists to kill.
2. **Editing or reconciling an existing block** -> append-only. Newer truth goes in the new top block; the old block stays as written. Superseded decisions are annotated forward, never rewritten backward.
3. **Breaking the header contract** -> renamed / re-leveled headers or a missing prefix token silently break `checkpoint.py`. Emit the exact levels and UPPERCASE prefixes from the file model.
4. **Touching the frozen mission tail** after S001 -> mission clarifications go inside the current session block.
5. **Silent overwrite** of a useful existing checkpoint -> ALWAYS check first, default to prepend, ASK when ambiguous.
6. **Writing to the wrong directory** -> ONLY `.claude/tmp/checkpoint.md`. Never `/tmp/`, never project root.
7. **Emojis anywhere in the file** -> the whole file is plain text (Critical Rule 1). Activation prompts especially get pasted into a terminal where an emoji corrupts the input line; strip every emoji before writing.
8. **Wrong session id** -> the new block's id comes from `checkpoint.py next-number`; do not hand-guess it. A DUPLICATE id is now refused by `prepend` instead of being written silently -- but a *skipped* id (S001 then S003) still goes through. The gate catches collisions, not gaps.
9. **Too terse** -> if a non-trivial session's block is under ~40 lines, you are under-documenting. Go deeper into `MODEL` and `NEXT`.
10. **Too verbose with no signal** -> do not embed raw `git diff` output, raw test logs, or minion paste-dumps. Reference them by path / commit / line.
11. **Activation prompt too long** -> keep it to 4-6 lines. It is a pointer, not a re-summary.
12. **Using Bash for git** when `mcp-git` is connected, or built-in Read/Write when `mcp-purity` is.
13. **Writing the file in the conversation language** -> the file is English, whole, always (Critical Rule 8). The only non-English text inside it is a verbatim quote; the only non-English text in the whole interaction is the Step 6 chat reply.
14. **Writing `checkpoint.md` with `purity_call`, Write or Edit** -> that is the old two-writer workflow, and it is precisely the hole `prepend` closed. `purity_call` writes the BLOCK FILE; `checkpoint.py` writes the checkpoint. If a `prepend` refuses, fix the block file -- routing around a refusal re-creates the stale-TOC state by hand.
15. **Hand-editing the TOC region** -> hand-typed line numbers are wrong the moment they are typed, and a `## ` line left inside the region would break `latest` / `session` / `mission` outright. If it looks wrong, run `toc --write`; never patch it.
16. **Reaching for `toc --write` during a normal checkpoint** -> `prepend` already regenerated the TOC, atomically. The extra call is a harmless no-op, but wanting it means you took the two-step path somewhere -- go back and find where.
