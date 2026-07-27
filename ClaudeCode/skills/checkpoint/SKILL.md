---
name: checkpoint
description: Persist the current session into `.claude/tmp/checkpoint.md` as a VERY detailed handoff document, then return a copy-paste activation prompt the user can use to resume the work in a fresh session. Updates the file in place by default; only overwrites when the user explicitly asks for it.
model: opus
---

# Checkpoint Command

Capture the **entire useful state of the current session** into `.claude/tmp/checkpoint.md` and hand the user a short activation prompt they can paste into a new session to continue exactly where we left off.

This is a **session continuity tool**, not a memory tool. The AI Soul memory system (`/p:recap`) is for long-lived facts and preferences. `/p:checkpoint` is for *this specific piece of work* — the in-flight context that would otherwise be lost when the session ends.

## Usage

```
/p:checkpoint                # update existing checkpoint.md (or create if missing)
/p:checkpoint --overwrite    # replace any existing checkpoint.md from scratch
/p:checkpoint --note "..."   # add a short user-supplied note at the top
```

Aliases for `--overwrite`: `--force`, `--fresh`, `felulir`, `felulirni`, `ujra`.

## Critical Rules

1. **Temporary files location — STRICT**: the checkpoint file MUST live at `.claude/tmp/checkpoint.md`. NEVER write it anywhere else (no `/tmp/`, no project root, no `docs/`). Create the `.claude/tmp/` directory if it does not exist.
2. **Default is UPDATE, not OVERWRITE**: if `.claude/tmp/checkpoint.md` already exists, READ it first and MERGE the new session data into it. The user gets ONE checkpoint that grows over time, not a file that loses prior context every time.
3. **Overwrite only on explicit request**: only blow away the existing file when the user passes `--overwrite` / `--force` / `--fresh` (or the Hungarian equivalents above). If unsure, ASK before overwriting — never silently destroy a working checkpoint.
4. **Tool routing — MANDATORY**: use `mcp-purity` for ALL file ops (`read_file`, `create_text_file`, `replace_content`). Do NOT use built-in Read/Write/Edit. Use `mcp-git` for git status/log/diff. Do NOT use Bash for `git status` / `git log` / `git diff`.
5. **Language**: the checkpoint file is written in the **language of the current conversation** (it is for human handoff, not project documentation). The activation prompt at the end follows the same language. Do NOT translate to English just because the file is technical.
6. **Be VERY detailed**: the whole point of this command is that the next session can pick up cold. Err on the side of more context, not less. The user explicitly asked for *NAGYON reszletes* — honor that.
7. **No code modifications**: this command is READ-ONLY against the codebase. It only writes the single checkpoint file.

## Workflow

### Step 1 — Parse arguments

- Detect `--overwrite` / `--force` / `--fresh` / `felulir*` / `ujra` flags in the user's invocation.
- Detect `--note "..."` and capture the note text verbatim.
- If no flag is present, default mode = UPDATE.

### Step 2 — Inspect existing state

In parallel (single message, multiple tool calls):

1. `mcp-purity find_file` for `checkpoint.md` under `.claude/tmp/` to confirm whether one exists.
2. `mcp-purity read_file` on `.claude/tmp/checkpoint.md` IF it exists.
3. `mcp-git status` (porcelain) to capture working-tree state.
4. `mcp-git log` of the last 10 commits on the current branch.
5. `mcp-git diff` (unstaged + staged) — short stat first, then full diff if not too large.

If the `.claude/tmp/` directory does not exist, create it via `mcp-purity create_text_file` when you write the checkpoint (the tool will create parent dirs). Do NOT run `mkdir` via Bash.

### Step 3 — Handle the overwrite decision

- **No existing file** → create fresh.
- **Existing file + overwrite flag** → replace entirely; mention in the output message that the previous checkpoint was overwritten.
- **Existing file + NO overwrite flag** → MERGE:
  - Preserve the original "session origin" / "mission" sections (the user's original goal).
  - Append a NEW dated entry under "Session timeline" with this session's events.
  - Update "Current state" / "Files touched" / "Open threads" / "Next steps" to reflect the LATEST reality (not a chronological log).
  - Preserve previously documented decisions, even if they are now historical — mark superseded ones explicitly.
- **Ambiguous case** (file exists, user invocation is ambiguous in the conversation language): ASK the user before overwriting. One short question, default to "merge" if no clear answer.

### Step 4 — Synthesize the session

Walk the full conversation from start to end. Extract:

- **Mission**: what does the user ultimately want?
- **Why**: the motivation / constraints / past pain points referenced.
- **Decisions**: every choice that was made (architectural, scope, library, naming, deferred work).
- **Open questions**: anything the user was asked or anything still ambiguous.
- **Actions taken**: every file written/edited/read, every command run that mattered, every minion delegated to.
- **State of the world right now**: what is done, what is half-done, what is untouched.
- **Mental model**: things you learned about the codebase / system / domain that are NOT obvious from the code alone (e.g., "X module owns Y resource", "build target Z is auto-generated").
- **Blockers / risks**: anything that could derail the continuation.
- **Next steps**: the concrete TODO list, ordered.

For non-trivial multi-file work, consider delegating the conversation review to `p:minion-explorer` ONLY if the conversation is too long to summarize in the main context — otherwise do it inline.

### Step 5 — Write the checkpoint

Use `mcp-purity create_text_file` (overwrite mode) or `mcp-purity replace_content` (merge mode) to write to `.claude/tmp/checkpoint.md`.

Use the following structure. Every section is mandatory; if a section has no content, write the heading and `_(none)_` rather than removing it — consistent shape makes future merges trivial.

```markdown
# Session Checkpoint

**Last updated:** <YYYY-MM-DD HH:MM local>
**Branch:** <current git branch>
**Working directory:** <absolute path>
**Session count:** <N> (incremented on every merge)
**User note:** <--note value or "none">

---

## 1. Mission

<1–3 paragraphs: what does the user want to achieve in this work stream? Be specific.
Reference the original phrasing from the user where useful. If this checkpoint has
been merged across multiple sessions, preserve the original mission and only append
clarifications below it.>

### Why this matters
<Constraints, past incidents, deadlines, stakeholder asks. Anything the user said
that explains the motivation behind the work.>

### Scope
- **In scope:** <bullets>
- **Out of scope:** <bullets — explicit non-goals to prevent scope creep>

---

## 2. Current state

<Snapshot of "where are we right now". This section is REWRITTEN on every merge —
it always reflects the latest reality.>

### Completed
- [ ] / [x] <each major milestone, with brief evidence (commit hash, file path, etc.)>

### In progress
- <each item being actively worked on, with file:line anchors where applicable>

### Not started
- <items planned but untouched>

### Blocked
- <items waiting on user decision, external dependency, etc., with the blocker named>

---

## 3. Decisions log

<Append-only. Every decision the user or the agent made during the session(s),
in chronological order. NEVER delete entries; mark superseded ones with
`~~strikethrough~~` and a `→ superseded by #N` reference.>

1. **<YYYY-MM-DD>** — <decision> — *Why:* <reason> — *Source:* <user / agent>
2. ...

---

## 4. Files touched

<Every file that was read, written, or edited during the session(s). Group by status.>

### Written / created
| Path | What | Status |
|------|------|--------|
| `path/to/file` | brief description | created / modified / deleted |

### Read (for context)
- `path/to/file` — why it was read

### Pending edits (planned but not yet applied)
- `path/to/file` — what change is planned

---

## 5. Session timeline

<Chronological log of session turns. Append-only across merges. Each session gets
its own subsection.>

### Session <N> — <YYYY-MM-DD HH:MM>
1. <turn-level summary: what the user asked, what the agent did, what came out of it>
2. ...

---

## 6. Mental model / non-obvious context

<Things learned during the session that are NOT obvious from reading the code alone.
This is the highest-leverage section for resuming cold — it captures the implicit
knowledge built up during the work.>

- <e.g. "The `forge_call` tool requires `function` + `params` top-level keys; passing
  `targets=[...]` at top level silently fails.">
- <e.g. "The CI build is configured to skip the `tests/integration/` folder on macOS;
  do not assume green CI means integration tests passed.">
- <…>

---

## 7. Tooling & environment

- **Active MCP servers:** <list>
- **Active minions used this session:** <list with one-line purpose each>
- **Build/test commands of record:** <exact commands or `forge_call` invocations>
- **Key env vars / config flags relevant to the work:** <list>

---

## 8. Open questions / threads

<Things the agent flagged or that the user asked but didn't resolve yet. Each item
should be actionable — either "ask the user" or "investigate X" or "decide between
A and B".>

- <…>

---

## 9. Risks & watch-outs

<Anything that could derail the continuation. Past mistakes worth remembering.
Edge cases the agent noticed but didn't address yet.>

- <…>

---

## 10. Next steps (ordered)

<The concrete TODO list for the next session, ordered by priority/dependency. Be
specific — "fix the bug" is useless; "add `NULL` check at src/foo.c:142 before
dereferencing `cfg->handler`" is useful.>

1. <…>
2. <…>
3. <…>

---

## 11. Activation prompt (paste into new session)

<This block is the user-facing payload. It is what the user copies and pastes into
a fresh session to resume. Keep it short — the new session will Read the full
checkpoint file. Same language as the rest of the file.>

**NO EMOJIS — STRICT.** The activation prompt is pasted directly into a terminal,
where emojis (and other non-ASCII pictographs) can corrupt the input line and break
the terminal. Compose this block as PLAIN TEXT ONLY: strip every emoji from the
mission / status / next-step sentences before placing them here. This rule applies to
the activation prompt SPECIFICALLY (Section 11 and its verbatim echo in Step 6) — the
rest of the checkpoint file is read by the tooling, not pasted into a terminal, so it
is unaffected and may keep its status emojis.

> Folytatjuk a `<branch>` ágon megkezdett munkát. A teljes session-kontextus
> a `.claude/tmp/checkpoint.md` fájlban van — olvasd el TELJESEN, mielőtt
> bármihez hozzányúlnál. Röviden: <one-sentence mission>. Az utolsó állapot:
> <one-sentence status>. A következő lépés: <one-sentence next step>.
> Ha bármi nem tiszta, kérdezz vissza, ne találgass.
```

### Step 6 — Output to the user

After writing the file, produce a SHORT user-facing message in the conversation language:

```
**Checkpoint frissítve / létrehozva:** `.claude/tmp/checkpoint.md`
**Mód:** merge (N. session) | overwrite | new
**Méret:** ~<N> sor, ~<M> kB

**Aktiváló prompt — másold be új sessionbe:**

```
<the verbatim contents of Section 11 — Activation prompt — from the checkpoint>
```

**Tipp:** új session indításakor először `/p:requirements` vagy `git status` lefuttatása sosem árt, csak hogy lásd a valós állapotot a checkpoint-ban dokumentált mellett.
```

The triple-backtick wrapping around the activation prompt is intentional — it makes
copy-paste trivial in the terminal.

## Quality bar

- **Detail level**: a competent dev who has NEVER seen this session should be able to pick up the work from the checkpoint alone, without re-reading the prior conversation.
- **Honesty over polish**: if something is half-done or unclear, say so. Do not paper over gaps with optimistic phrasing.
- **No invented progress**: only document what actually happened. If a file was planned but not written, it goes under "pending edits" / "not started", NOT under "completed".
- **No backwards-compatibility hacks in the file**: when superseding a decision, mark it superseded — do not silently rewrite history.
- **Same language as the conversation**: do not translate. The checkpoint is a continuity artifact for the user, not a documentation deliverable.

## What this command is NOT

- ❌ It is NOT `/p:recap` — it does not propose long-lived memories.
- ❌ It is NOT a PR description — it does not summarize for an external audience.
- ❌ It is NOT a commit message — it captures *in-flight* state, including half-done work.
- ❌ It is NOT a CLAUDE.md update — it is per-task ephemeral context, not project-wide guidance.

## Failure modes to avoid

1. **Silent overwrite** of a useful existing checkpoint → ALWAYS check first, default to merge, ASK when ambiguous.
2. **Writing to the wrong directory** → ONLY `.claude/tmp/checkpoint.md`. Never `/tmp/`, never project root.
3. **Too terse** → if the file is under ~100 lines for a non-trivial session, you are under-documenting. Go deeper into Section 6 (mental model) and Section 10 (next steps).
4. **Too verbose with no signal** → do not include raw `git diff` output, raw test logs, or paste-dumps from minions. Reference them by path / commit / line, do not embed them.
5. **Activation prompt too long** → keep Section 11 to 4–6 lines. The new session will read the full file; the activation prompt is a pointer, not a re-summary.
6. **Forgetting to increment session count** on merge.
7. **Using Bash for git** when `mcp-git` is connected. Same for `mcp-purity` vs built-in Read/Write.
8. **Emojis in the activation prompt** → the Section 11 block is pasted straight into a terminal; emojis can corrupt the input line and break it. The activation prompt MUST be plain text — strip every emoji before composing it (see the NO EMOJIS rule under Section 11).
