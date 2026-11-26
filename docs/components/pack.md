---
name: pack
type: component
status: active
title: pack — the p:pack skill and pack.py, the Pack board client of one repo
description: The p:pack skill and its stdlib client ClaudeCode/skills/pack/scripts/pack.py, which projects a repo's live roadmap items and requirements-*.yaml plans onto the ai-soul Pack board (POST /pack/sync), reads a member's cards and queued events fenced as data (/pack/pull), claims and moves cards (/pack/claim, /pack/update), and applies the board's decided roadmap events back through roadmap.py with a validate, pre-check, apply, verify and ack loop; the pack.json and token file rules, member and project resolution, the sync digest that is the script's one file write, the exit codes, the worker and maintainer loops, the hand-registered hook lines, how it is tested against the stub hub, and what it declares rather than gates.
sources:
  - ClaudeCode/skills/pack/SKILL.md
  - ClaudeCode/skills/pack/scripts/pack.py
  - tests/test_pack_apply.py
  - tests/test_pack.py
  - tests/_stub_pack_hub.py
verified:
  commit: 24bc918
  date: 2026-11-26
links:
  - skills
  - scripts
  - tests
  - hooks
  - mcp-messagebox
  - generated-regions
  - roadmap
  - 0022-a-someday-maybe-is-a-roadmap-item
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
---

# pack

`ClaudeCode/skills/pack/scripts/pack.py` is the client of the ai-soul Pack board
in one repo. The board is a kanban kept by the ai-soul hub: one card per live
roadmap item of a project, a squad of one maintainer and one or two worker
sessions per project, and an owner who decides every gate in the hub.
`ClaudeCode/skills/pack/SKILL.md` is the operator contract the model reads; this
page is for whoever maintains the script. The board's own code lives in ai-soul
and is not described here.

Data flows one way. `sync` publishes the repo's live items and task plans,
`pull` reads a member's work, and the only writes to the repo are the
maintainer's `roadmap.py` runs that `apply` makes from the board's queued events
([[roadmap]], [[0022-a-someday-maybe-is-a-roadmap-item]]). The script is
standard-library Python ([[0024-pure-python-39-and-the-stdlib]]).

## Commands

`HANDLERS` maps eight commands to their functions
`ClaudeCode/skills/pack/scripts/pack.py:HANDLERS`; every one but `pull` changes
the board `ClaudeCode/skills/pack/scripts/pack.py:WRITE_COMMANDS`. The global
flags `--config`, `--member` and `--project` come before the command
`ClaudeCode/skills/pack/scripts/pack.py:build_parser`.

| Command | Route | What it does |
|---|---|---|
| `sync [--plans-only] [--force]` | `/pack/sync` | the export part (the live items) and the plan part (every plan file) `ClaudeCode/skills/pack/scripts/pack.py:cmd_sync` |
| `pull [--compact] [--json]` | `/pack/pull` | the member's cards and, for the maintainer, the queued events, fenced `ClaudeCode/skills/pack/scripts/pack.py:cmd_pull` |
| `claim R-NNNN` | `/pack/claim` | the assignee claims its planning card `ClaudeCode/skills/pack/scripts/pack.py:cmd_claim` |
| `assign R-NNNN WORKER` | `/pack/update` | the maintainer assigns a planning card `ClaudeCode/skills/pack/scripts/pack.py:cmd_assign` |
| `plan-ready R-NNNN` | `/pack/update` | the plan awaits review `ClaudeCode/skills/pack/scripts/pack.py:cmd_plan_ready` |
| `impl-ready R-NNNN` | `/pack/update` | the implementation awaits closing `ClaudeCode/skills/pack/scripts/pack.py:cmd_impl_ready` |
| `ack pv_... applied\|failed` | `/pack/update` | one event acked by hand, `--code` for a failure, `--commit` for an applied close `ClaudeCode/skills/pack/scripts/pack.py:_ack_fields` |
| `apply [--commit pv_...=SHA]...` | `/pack/pull`, `/pack/update` | every queued event applied and acked `ClaudeCode/skills/pack/scripts/pack.py:cmd_apply` |

`claim`, `assign`, `plan-ready`, `impl-ready`, `ack`, `apply` and `pull` take
`--json`. The board's hub-session routes (the board view, card acts, squads) are
not the client's: every owner act happens in the hub.

**Exit codes.** 0 ok; 1 refused by the board, any HTTP error status, printed as
the status only and never the body; 2 usage or configuration, including a token
file refusal and a request over 2 MiB; 3 transport: unreachable, a redirect, a
malformed or oversize answer `ClaudeCode/skills/pack/scripts/pack.py:hub_exit`.
A local refusal is one `pack: ` line on stderr with every unsafe character
escaped `ClaudeCode/skills/pack/scripts/pack.py:die`.

## Configuration and identity

The configuration is `~/.config/ai-soul/pack.json`
`ClaudeCode/skills/pack/scripts/pack.py:CONFIG_PATH`: `hubUrl` and `tokenFile`
required, `member` and `workDir` optional, nothing else. It is opened
`O_NOFOLLOW | O_NONBLOCK` and judged on the descriptor: a regular file of the
user, mode exactly `0600`, at most 2048 bytes, strict JSON. A refusal names the
rule, never a value `ClaudeCode/skills/pack/scripts/pack.py:load_config`. The hub
URL follows the messagebox's rules, re-implemented
`ClaudeCode/skills/pack/scripts/pack.py:_check_hub_url`.

The token file is the machine's ai-soul client token, the same file as the
messagebox's `MESSAGEBOX_TOKEN_FILE`, so the board can resolve a member to that
token's live peer ([[mcp-messagebox]]). It is re-read at every request with the
messagebox's rules: `0600`, one link, at most 4096 bytes, one printable-ASCII line
starting `aisc1_` `ClaudeCode/skills/pack/scripts/pack.py:read_token_file`.

**The hub client** is the messagebox's shape: stdlib `urllib`, an empty proxy
handler, every redirect refused so the bearer token never follows one, a 1 MiB
answer cap, strict JSON, a 15 s timeout, and fixed error phrases
`ClaudeCode/skills/pack/scripts/pack.py:hub_post`. A 404 is retried once after 5 s
(the messagebox may still be registering at SessionStart) and a 429 once after
`Retry-After`, clamped to 0-10 s with 5 s for anything that is not a plain integer
`ClaudeCode/skills/pack/scripts/pack.py:_post_retrying`
`ClaudeCode/skills/pack/scripts/pack.py:_retry_after_delay`.

**The member** is, first match wins: `--member`, pack.json `member`,
`CLAUDE_LOOP_ENTITY` when it matches `[a-z0-9-]{1,64}`, the working directory's
basename reduced to the messagebox's name grammar
`ClaudeCode/skills/pack/scripts/pack.py:resolve_member`. `Scripts/claude-loop.sh
--entity NAME` exports the entity, so a squad session's member name is its peer
name. The board resolves the name to one of the calling token's live peers named
`NAME` or `NAME-<n>`, so a session restarted after a crash (registered as
`NAME-2` while the dead peer is still live) keeps its member.

**The project** is `--project`, else the git toplevel's basename lowercased and
mapped to `[a-z0-9][a-z0-9._-]{0,63}`
`ClaudeCode/skills/pack/scripts/pack.py:project_of`. The toplevel is found
without spawning git: a walk up from the working directory's realpath to the
first `.git` that `lstat` shows as a directory or a regular file; a symlinked
`.git` does not count `ClaudeCode/skills/pack/scripts/pack.py:find_toplevel`.

## sync

`sync` builds one `pack-sync/1` body `{member, project, export, plans}`
`ClaudeCode/skills/pack/scripts/pack.py:cmd_sync`:

- **The export part** is `roadmap.py export --open-only`, refused unless it is
  `roadmap-export/2`, projected to id, title, state, horizon, rank, ready,
  blockedBy, tags and severity of each live item, with no why and no log
  `ClaudeCode/skills/pack/scripts/pack.py:project_items`. Titles, severities and
  task texts pass `sanitise_row`: NFC, one line, every refused code point U+FFFD,
  a marker neutralised, then clipped
  `ClaudeCode/skills/pack/scripts/pack.py:sanitise_row`. The board takes the
  export only from the squad's maintainer; `--plans-only` sends `export: null`.
- **The plan part** is the sender's full plan set: every
  `requirements-[a-z0-9._-]{1,64}.yaml` directly in the repo root and in the work
  dir (pack.json `workDir`, default `.claude/tmp/pack`), never a symlink or a
  descent; the work-dir copy wins a name clash; at most
  `ClaudeCode/skills/pack/scripts/pack.py:PACK_MAX_PLANS_PER_ITEM` files per item
  `ClaudeCode/skills/pack/scripts/pack.py:collect_plans`. The work dir must lie
  inside the toplevel by `os.path.commonpath` and must not be reached through a
  symlink `ClaudeCode/skills/pack/scripts/pack.py:resolve_work_dir`. Each file is
  read by regex, the `Scripts/task-update.py` family: the top-level
  `roadmap_item` (a file without one is skipped with a note) and each task's id,
  status and description first line; a status outside
  `pending | in_progress | completed | cancel` becomes `unknown`
  `ClaudeCode/skills/pack/scripts/pack.py:read_task_yaml`. A full sync drops the
  plans of items not open in the export.

On the board, a card's plan comes from its assignee while it has one, else from
the maintainer; a plan the sender synced before and no longer sends is deleted.

**The digest.** The request is not sent when
`sha256(hubUrl, member, clock hour, body bytes)` equals the last one recorded
`ClaudeCode/skills/pack/scripts/pack.py:sync_digest`, so a Stop hook on every turn
costs no request; `--force` sends anyway. It is recorded only when the board took
the export (or none was sent) and left no plan pending. The digest is
`<toplevel>/.claude/tmp/pack/last-sync.sha256`, walked fd-relative with
`O_NOFOLLOW` from the toplevel on both sides; the write goes through an `O_EXCL`
`0600` temp file, fsync and rename, and any failure is a note, never a non-zero
exit `ClaudeCode/skills/pack/scripts/pack.py:_write_sync_digest`
`ClaudeCode/skills/pack/scripts/pack.py:_read_sync_digest`. It is the script's
only file-system write. A digest planted by the same OS user can only suppress
syncs, and `--force` recovers.

A second 429 is `sync: busy, skipped`, exit 0.

## pull and the fence

`pull` prints every board text between `<<pack-data nonce=N>>` and
`<<end-pack-data nonce=N>>`, a fresh 16-hex nonce per call, after the line
`ClaudeCode/skills/pack/scripts/pack.py:FENCE_PREFACE`. A text whose marker fold
(NFKD, then white space, `_`, dashes and combining marks dropped) carries a fence,
`system-reminder` or `peer-message` marker has every `<`-bearing character turned
into U+2039, in one pass that a second pass does not change
`ClaudeCode/skills/pack/scripts/pack.py:neutralise`. Every answer field is judged
against its closed set or grammar before it is printed; an unknown key is never
printed `ClaudeCode/skills/pack/scripts/pack.py:parse_pull`. Three forms:
markdown (cards, and for the maintainer the queued events), `--compact` (the head
line and at most 20 cards, the SessionStart form) and `--json`, whose `data`
member is the fenced serialisation `ClaudeCode/skills/pack/scripts/pack.py:render_pull`.

## apply

`apply` is the maintainer's loop: pull, then for each queued event validate,
pre-check, run `roadmap.py`, verify and ack
`ClaudeCode/skills/pack/scripts/pack.py:cmd_apply`.

- **Refusals before anything runs:** a member that is not the maintainer
  (exit 1), a repo that is not the squad's project, a `--commit` naming no queued
  close event (both exit 2) `ClaudeCode/skills/pack/scripts/pack.py:_commit_flags`.
- **Validation.** Every field must pass the board's grammar before any of it can
  reach an argv: the `pv_` id, the kind (`start`, `rank`, `move`, `close`,
  `add`), exactly the kind's keys, item ids `R-NNNN`, the lane, a 7-64 hex commit,
  an `add` title with `roadmap.py`'s scalar rules mirrored. A refused event is
  acked `failed` `refused`; an event with a malformed id is skipped and counted,
  because it cannot be acked `ClaudeCode/skills/pack/scripts/pack.py:validate_event`.
- **Pre-checks on a fresh export decide the outcome**, because every
  `roadmap.py` refusal exits 2. An event whose effect already holds is acked
  `applied` without running `roadmap.py`; a full `now` is `wip`; a lane or state
  conflict is `lane` or `state` `ClaudeCode/skills/pack/scripts/pack.py:apply_event`.
  A refusal after a passing pre-check is `wip` when stderr starts
  `roadmap: now is full (`, else `refused`
  `ClaudeCode/skills/pack/scripts/pack.py:_classify`.
- **Each kind:** `start` is `roadmap.py start` with the reason
  `pack board start (<eventId>)`; `rank` to the end of a lane ranks every later
  item before it, since `rank` has no bottom
  `ClaudeCode/skills/pack/scripts/pack.py:_rank_to`; `move` is `roadmap.py move`
  then an optional rank; `close` needs the maintainer's `--commit pv_...=SHA`,
  passes `--slug r<NNNN>` when the title slugs to nothing, and acks the archive's
  FULL sha; a close without a commit is left queued with a note unless the item
  is already archived done `ClaudeCode/skills/pack/scripts/pack.py:_apply_close`;
  `add` writes an inbox item with the origin `pack <eventId>`, which makes it
  idempotent `ClaudeCode/skills/pack/scripts/pack.py:_apply_add`.
- **Verify.** After the run a fresh export must show the change, else `verify`.

So a crash between `roadmap.py` and the ack is repaired by running `apply` again:
the second run's pre-check sees the effect and acks it, and `roadmap.md` stays
byte-identical to one application.

**The one subprocess site.** `roadmap.py` is run by this Python with an argv
list, never a shell, `stdin` `/dev/null`, a 60 s bound; a run that cannot start or
times out is a fixed refusal `ClaudeCode/skills/pack/scripts/pack.py:_roadmap`.
The script is the installed skill's
`ClaudeCode/skills/pack/scripts/pack.py:ROADMAP_PY`; `PACK_ROADMAP_PY` replaces it
for tests only `ClaudeCode/skills/pack/scripts/pack.py:_roadmap_py`.

## The generated region

The text rule, the strict JSON parser and the file-read helpers are the
messagebox's spool source `Scripts/_mcp_spool.py`, taken whole through one
generated region. `pack.py` is a hand-declared non-server host
`Scripts/amalgamate.py:DECLARED_HOSTS` ([[generated-regions]],
[[0025-generate-do-not-import]]). The owner-walk blocks ride along inert; the
script names none of them outside the region. Refresh with
`python3 Scripts/amalgamate.py`.

## Hooks

Skills never register hooks. The SKILL.md gives the operator the SessionStart
line (`pack.py pull --compact`) and the Stop line (`pack.py sync --plans-only` for
a worker, `pack.py sync` for the maintainer) to add to `~/.claude/settings.json`
by hand, each ending `|| true` so that a refusal (exit 2 outside a git repo) does
not reach the session ([[hooks]]).

## How it is tested

Two suites, case counts in `tests/run.py:SUITES` ([[tests]]):

- **`pack_apply`** is the offline core, tested first because it touches
  `roadmap.md` `tests/test_pack_apply.py`: every event kind through the real
  `roadmap.py` in a temp git repo, applied twice and after a crash with
  `roadmap.md` byte-identical; `validate_event` against hostile fields;
  `sanitise_row` and the text judge; the readers; the digest's round trip and its
  symlink, hardlink, temp-name, FIFO and size refusals; and an AST gate over
  `pack.py` outside its generated region that pins `_roadmap` as the only
  subprocess site and `_write_sync_digest` as the only file-system write.
- **`pack_cli`** runs `main()` in-process against `tests/_stub_pack_hub.py` on
  loopback, in a temp sandbox, with a fake export and a recorded sleep
  `tests/test_pack.py`: the exact sync body, paths, the digest rules, retries and
  `Retry-After`, exit codes, pack.json and token refusals, member precedence, the
  fence and the token in no output.

The stub hub takes the sync body as strictly as the board: bearer check, 2 MiB
cap, strict JSON, exact keys, at most 8 plans per item, the server's raw and
folded marker rule on every row; plus minimal pull, claim and update routes and
per-route fault injection `tests/_stub_pack_hub.py:StubPackHub`.

## Declared, not gated

- **The member check is not a boundary.** A member is a name in the body,
  resolved against the calling token's live peers. Any session on the same token
  can name any live member; the board's member matrix guards against a confused
  session, not a prompt-injected one. Every irreversible act is the owner's, in
  the hub.
- **The stub is not the board.** The suites prove the client against
  `tests/_stub_pack_hub.py`; the real board's rules live in ai-soul.
- **The WIP classifier pins a wording.** A late WIP refusal is recognised by
  `roadmap.py`'s `now is full (` stderr prefix. A wording change turns it into
  `refused`; the `pack_apply` row fed the real refusal catches it.
- **Plain `http` is accepted for any host**, as in the messagebox
  `ClaudeCode/skills/pack/scripts/pack.py:_check_hub_url`.

## Related

- [[skills]]: where the skill sits. [[mcp-messagebox]]: the peer whose name is
  the member. [[roadmap]]: the single-writer rule `apply` respects.
