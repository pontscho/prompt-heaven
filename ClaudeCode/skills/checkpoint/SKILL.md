---
name: checkpoint
description: Persist the current session into `.claude/tmp/checkpoint.md` as a VERY detailed handoff document, then return a copy-paste activation prompt the user can use to resume the work in a fresh session. Prepends a new session block to the TOP of the file by default (append-only -- prior session blocks are never rewritten); only overwrites the whole file when the user explicitly asks for it.
model: opus
---

# Checkpoint Command

Capture the **entire useful state of the current session** into `.claude/tmp/checkpoint.md` and hand the user a short activation prompt they can paste into a new session to continue exactly where we left off.

This is a **session continuity tool**, not a memory tool. The AI Soul memory system (`/p:recap`) is for long-lived facts and preferences. `/p:checkpoint` is for *this specific piece of work* -- the in-flight context that would otherwise be lost when the session ends.

## The file model -- append-only, newest on top, header-addressable

The checkpoint is an **append-only stack of self-contained session blocks** above a **frozen mission tail**. Every section is a markdown header whose LEVEL and ENGLISH PREFIX TOKEN identify it, so a tiny helper script (`~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py`) can return any section without guessing byte offsets, and so a cold resume can pull only the sections it needs instead of reading a 150-block file whole. Read this before anything else -- it governs every rule below.

```
# Session Checkpoint                              <- H1 title, line 1, never grows

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
- `##` -- block level. Prefix `SESSION S<NNN> | <YYYY-MM-DD HH:MM> | <branch>` for a session block, or `MISSION` for the frozen tail. Fields are pipe-separated ASCII so `list` can tabulate them.
- `###` -- subsection level, with a stable UPPERCASE ENGLISH prefix token: `LOG`, `FILES`, `DECISIONS`, `STATE`, `THREADS`, `NEXT`, `MODEL`, `ACTIVATION` inside a SESSION block; `WHY`, `SCOPE` inside MISSION. The prefix is a machine label; the rest of the file body is written in the conversation language.
- Session ids are zero-padded to three digits (`S001`..`S999`) so they sort and grep cleanly and are easy to cite ("see S042").

Three properties make this cheap and safe, and every rule exists to protect them:

1. **One dumb write.** A normal checkpoint is a single `insert_at_line` of the new block just below the H1 title. Nothing above or below the insertion point moves. No full-document regeneration, no section reconciliation -- that is what made the old merge model slow and error-prone.
2. **The append-only log never lies.** A block, once written, is immutable -- what it records happened, and it is never overwritten. There is no mutable "current state" section that can drift; the derived state lives inside the newest block by convention.
3. **Recency is truth.** The top-most block's `STATE` and `NEXT` are authoritative. Older blocks are historical archive. When two blocks disagree, the top one wins.

## The helper script -- ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py

Stdlib-only markdown section reader. Use it INSTEAD OF guessing offsets or reading the whole file. Commands:

```
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py next-number      # next session id (S003), or S001 if the file is missing/empty -- WRITE side
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py latest           # the newest SESSION block, whole -- the primary resume payload
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py mission          # the frozen MISSION block -- the "why"
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py list             # one line per SESSION header (id | date | branch) -- table of contents
python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py session S042     # one specific block by id -- on-demand history
```

Default target is `.claude/tmp/checkpoint.md`; override with `--file PATH`. This is why the header contract must not drift: the script keys entirely off header level + prefix.

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
7. **Tool routing -- MANDATORY**: use `mcp-purity` for ALL file ops. First-ever write or `--overwrite` -> `create_text_file`. Normal checkpoint -> `insert_at_line` to prepend the new block. Do NOT use built-in Read/Write/Edit. Use `mcp-git` for status/log/diff. Do NOT use Bash for `git status` / `git log` / `git diff`.
8. **Language**: the checkpoint body is written in the **language of the current conversation** (it is a human handoff, not project documentation). Only the header prefix tokens are fixed English labels. The activation prompt follows the conversation language. Do NOT translate the body to English just because the content is technical.
9. **Be VERY detailed**: the whole point is that the next session can pick up cold. Err on the side of more context, not less. The user explicitly asked for *NAGYON reszletes* -- honor that.
10. **No code modifications**: this command is READ-ONLY against the codebase. It only writes the single checkpoint file.

## Workflow

### Step 1 -- Parse arguments

- Detect `--overwrite` / `--force` / `--fresh` / `felulir*` / `ujra` flags in the user's invocation.
- Detect `--note "..."` and capture the note text verbatim.
- If no flag is present, default mode = PREPEND.

### Step 2 -- Inspect existing state

In parallel (single message, multiple tool calls):

1. `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py next-number` -- the id for the new block (`S001` if the file is missing). This replaces reading the file just to find the top block's number.
2. `mcp-purity find_file` for `checkpoint.md` under `.claude/tmp/` to confirm whether one exists (decides create vs insert).
3. `mcp-git status` (porcelain) to capture working-tree state.
4. `mcp-git log` of the last 10 commits on the current branch.
5. `mcp-git diff` (unstaged + staged) -- short stat first, then full diff if not too large.

Only if you are in fresh/overwrite mode do you also need the frozen mission context; get it with `checkpoint.py mission`. Do NOT `mkdir` -- the write tool creates parent dirs.

### Step 3 -- Decide the write mode

- **No existing file** -> create fresh with `create_text_file`: H1 title, then the `S001` block, then the `## MISSION` tail at the very bottom.
- **Existing file + overwrite flag** -> rebuild the whole file from scratch (`create_text_file`); mention in the output that the previous checkpoint was overwritten.
- **Existing file + NO overwrite flag** -> PREPEND: build ONE new `## SESSION <next-number>` block and `insert_at_line` it immediately below the H1 title (above the current top block), so it becomes the new newest block. Touch NOTHING else -- not the older blocks, not the mission tail.
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

**Prepend mode** -- `insert_at_line` this block just below the H1 title, above the current top block. **Fresh / overwrite mode** -- `create_text_file` the whole file: H1 title, the `S001` block, then the mission tail at the bottom.

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
> Folytatjuk a `<branch>` agon megkezdett munkat. A kontextus a `.claude/tmp/checkpoint.md`-ben.
> Kerd le a lenyeget:
> `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py mission` es `... latest`.
> Reszletekert ugyanezzel a scripttel: `list`, majd `session S0xx`.
> Roviden: <one-sentence mission>. Utolso allapot: <one-sentence status>.
> Kovetkezo lepes: <one-sentence next step>. Ha valami nem tiszta, kerdezz, ne talalgass.
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

After writing, produce a SHORT user-facing message in the conversation language:

```
**Checkpoint frissitve:** `.claude/tmp/checkpoint.md`
**Mod:** prepend (S0xx) | overwrite | new
**Meret:** ~<N> sor, ~<M> kB

**Aktivalo prompt -- masold be uj sessionbe:**

```
<the verbatim ACTIVATION block from the block you just wrote>
```

**Tipp:** uj sessionben eloszor `python3 ~/.claude/skills/p/skills/checkpoint/scripts/checkpoint.py latest` (es `... mission`) -- ez adja a lenyeget a teljes fajl beolvasasa nelkul.
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

1. **Rewriting the whole file on a normal checkpoint** -> a normal checkpoint is ONE `insert_at_line` at the top. Regenerating the document is the slow, lossy anti-pattern this model exists to kill.
2. **Editing or reconciling an existing block** -> append-only. Newer truth goes in the new top block; the old block stays as written. Superseded decisions are annotated forward, never rewritten backward.
3. **Breaking the header contract** -> renamed / re-leveled headers or a missing prefix token silently break `checkpoint.py`. Emit the exact levels and UPPERCASE prefixes from the file model.
4. **Touching the frozen mission tail** after S001 -> mission clarifications go inside the current session block.
5. **Silent overwrite** of a useful existing checkpoint -> ALWAYS check first, default to prepend, ASK when ambiguous.
6. **Writing to the wrong directory** -> ONLY `.claude/tmp/checkpoint.md`. Never `/tmp/`, never project root.
7. **Emojis anywhere in the file** -> the whole file is plain text (Critical Rule 1). Activation prompts especially get pasted into a terminal where an emoji corrupts the input line; strip every emoji before writing.
8. **Wrong session id** -> the new block's id is `checkpoint.py next-number`; do not hand-guess it.
9. **Too terse** -> if a non-trivial session's block is under ~40 lines, you are under-documenting. Go deeper into `MODEL` and `NEXT`.
10. **Too verbose with no signal** -> do not embed raw `git diff` output, raw test logs, or minion paste-dumps. Reference them by path / commit / line.
11. **Activation prompt too long** -> keep it to 4-6 lines. It is a pointer, not a re-summary.
12. **Using Bash for git** when `mcp-git` is connected, or built-in Read/Write when `mcp-purity` is.
