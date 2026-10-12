---
name: mcp-skill
type: component
status: active
title: mcp-skill — the read-only skill loader and runner client
description: The stdio MCP server that serves household and private skill tiers through one skill_call tool -- the skill index rendered as the tool description and refused whole over a UTF-16 ceiling, fd-relative file hygiene with fixed refusal reasons, sanitized and unframed text through a declared hand copy of the websearch sanitizer, and run through a bounded loopback HTTP client to the sandbox runner behind its own proxy; the ai-soul contract it relies on, how it is tested and what it declares rather than gates.
sources:
  - Scripts/mcp-skill.py
  - tests/test_mcp_skill.py
  - tests/files/mcp_skill/stub_runner.py
verified:
  commit: 0668eb3
  date: 2026-10-12
links:
  - mcp-proxy
  - mcp-sandbox
  - generated-regions
  - scripts
  - tests
  - 0010-a-handler-failure-must-reach-iserror
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0027-the-proxy-relays-it-never-composes
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0025-generate-do-not-import
---

# mcp-skill

`Scripts/mcp-skill.py` is a stdio MCP server that serves **skills** — short
instructions, reference files and scripts a household writes once and an agent
reuses — through one tool, `skill_call`, whose functions are `load`, `run`,
`list` and `validate` `Scripts/mcp-skill.py:FUNCTIONS`
`Scripts/mcp-skill.py:handle_skill_call`. It never writes
to a skill root. Its consumer is ai-soul, through an [[mcp-proxy]] child entry
with `relist` on, so it is not registered with Claude Code — the smoke table
carries it as an unregistered server `Scripts/_mcp_smoke_test.py:SERVERS`.

Everything below is the *shape*. The decision record — the four layers, the
runner behind a dedicated proxy, the sanitizer kept as a hand copy, the
same-uid trust model — is [[0032-a-runner-behind-a-proxy]]; its 2026-10-12
addendum records the deployment where the runner is a sibling child of the same
per-uid proxy instead, and a later one the reversal of its KD-10 nonce framing:
since `0668eb3` neither `load` nor `run` wraps its text in a frame. Where the
server sits in the fleet is [[scripts]].

## Tiers and the index

**Two tiers, one index file each.** `--household-root` is shared by every
member, `--private-root` is one member's own; either may be omitted, and with
neither the index is empty `Scripts/mcp-skill.py:TIERS`. A root is resolved and
checked once at startup — a directory owned by the euid or by root with no
group or other write bit — and a refusal names the flag and the rule, never the
path `Scripts/mcp-skill.py:_check_root`. One state is not a refusal: a root
that does not exist yet — `ENOENT` on the root or on any parent, which is also
what a dangling symlink resolves to — is accepted, because ai-soul creates each
root lazily on its first publish (see the ai-soul contract below). `_open_root`
raises it as `Scripts/mcp-skill.py:RootMissing`, a subclass of
`Scripts/mcp-skill.py:RootRefused`, so every other refusal is unchanged: a path
through a regular file (`ENOTDIR`), a non-directory, a symlink, a foreign
owner, a group or other write bit or any other errno still refuses start.
Every later operation reopens that realpath with `O_NOFOLLOW` and re-checks it
on the descriptor, so a root swapped for a symlink since startup is refused
`Scripts/mcp-skill.py:_open_root`.

**`skills.json` is the only enumeration.** A tier root is opened, never listed
`Scripts/mcp-skill.py:load_tier`. The index is parsed strictly — the generated
`_strict_loads`, then a second parse whose only job is to refuse a repeated key
`Scripts/mcp-skill.py:_refuse_duplicate_keys_parse` — and must be exactly
`{version, skills}` with version 1 and at most
`Scripts/mcp-skill.py:_INDEX_MAX_SKILLS` entries
`Scripts/mcp-skill.py:_parse_index`. A top-level violation makes the whole tier
an `error`, and so does a root or index refused on the descriptor — but a root
that does not exist, or exists without a `skills.json`, is the **empty tier
with no error**, and neither state is cached, so a root or index that appears
later is read on the next call `Scripts/mcp-skill.py:load_tier`. Every other
`skills.json` refusal is still `skills.json refused: <reason>`. A bad entry makes only that entry `invalid`, with a fixed reason and
never the offending text `Scripts/mcp-skill.py:INVALID_REASONS`. An entry is
exactly `{name, dir, description, scripts}`; `name` and `dir` are one lower-case
component, every script is exactly `{path, interpreter}` with the path under
`scripts/` and the interpreter one of `Scripts/mcp-skill.py:INTERPRETERS`, a name
listed twice invalidates every copy, and each indexed script must already pass
the leaf rules below `Scripts/mcp-skill.py:_build_tier`
`Scripts/mcp-skill.py:_check_scripts`. An entry indexes at most
`Scripts/mcp-skill.py:_INDEX_MAX_SCRIPTS` (64, the run bundle's file cap)
scripts, else it is `scripts-too-many`, and a path indexed twice in one entry is
`script-duplicate`. The leaf check opens each script and checks it with `fstat`
only; its body is never read while the index is built
`Scripts/mcp-skill.py:_check_leaf` (security review round 1, K3; before it,
each entry's scripts were read whole, up to 4 MiB each, with no cap on how
many).

**SKILL.md frontmatter is four lines, not YAML.** `---`, `name: <name>`,
`description: <rest of line>`, `---`, then the body. The description is
non-empty, at most `Scripts/mcp-skill.py:_DESCRIPTION_MAX_CHARS` code points, and
must already equal its own sanitized form, so a description carrying a bidi
control or a zero-width character is `description-not-clean` rather than quietly
cleaned `Scripts/mcp-skill.py:parse_frontmatter`
`Scripts/mcp-skill.py:_description_clean`. The frontmatter's name and
description must match the index entry, else `frontmatter-mismatch`
`Scripts/mcp-skill.py:_check_entry`.

**The private tier shadows the household one** on a name clash
`Scripts/mcp-skill.py:merge`.

**The index is cached on stat keys.** A tier is rebuilt when the
`(st_dev, st_ino, st_mtime_ns, st_size)` key of `skills.json`, of an indexed
skill dir or of its SKILL.md changes, so an atomic rename is seen on the next
call; the keys are taken before the reads they describe, so a file swapped in
between leaves a stale key and forces a reload rather than being trusted
`Scripts/mcp-skill.py:load_tier`.

## The tool description is the index

Every `tools/list` re-renders the tool from both tiers
`Scripts/mcp-skill.py:_current_view`: a constant preamble that calls the lines
labels written by household members, not instructions
`Scripts/mcp-skill.py:PREAMBLE`, then one sorted `name - description` line per
valid skill, a private one marked `[private]`
`Scripts/mcp-skill.py:_index_lines`. The `name` parameter carries the valid
names as its enum `Scripts/mcp-skill.py:_tool_definition`.

**The ceiling is in UTF-16 code units, and the index is refused whole, never
cut.** ai-soul cuts a tool description by JavaScript `.length`, so every size
here is counted the same way `Scripts/mcp-skill.py:_utf16_units`.
`--max-description` bounds the rendered description
(`Scripts/mcp-skill.py:_MAX_DESCRIPTION_MIN` to
`Scripts/mcp-skill.py:_MAX_DESCRIPTION_MAX`, which is ai-soul's own cut); over
it, the description becomes the preamble plus a `SKILL INDEX REFUSED` line naming
the rendered size, the ceiling, and each tier's budget sum, and the enum is
dropped `Scripts/mcp-skill.py:_render_description`. `tools/list` therefore
never fails because of skill content — the refusal travels inside the
description, and `list` and `validate` keep working
`Scripts/mcp-skill.py:fn_list`. A tier's budget sum is the UTF-16 size of its
own valid lines, the number ai-soul's per-tier approval budget is counted in
`Scripts/mcp-skill.py:TierIndex`.

## The dispatcher

A refusal is a tool result with `isError` and an `error.code` — `busy`,
`sandbox_unavailable`, `invalid_params` or `internal` — never a JSON-RPC error
`Scripts/mcp-skill.py:_tool_refusal` ([[0010-a-handler-failure-must-reach-iserror]]).
A call with no `function` gets an unflagged status reply: the function list,
per-tier counts, the description's size against the ceiling and whether a
runner is configured — counts only, never a path, a token or skill text
`Scripts/mcp-skill.py:_status_text`. An unknown function is refused with a
did-you-mean pointer that never resolves the call
`Scripts/mcp-skill.py:FUNCTIONS` ([[0030-point-at-the-near-miss-never-resolve-it]]).
Parameters split in two by the advertised schema. The tool's `params` object is
one flat schema offering `name`, `file`, `script`, `args`, `stdin` and
`timeoutMs` to every function, and the set is read from that schema itself
`Scripts/mcp-skill.py:SCHEMA_PARAMS`. A model fills every field it is shown —
`list` arrived with `name`/`file`/`script` set to `dummy`, `args` `[]`, `stdin`
`""` and `timeoutMs` `0`, was refused `Unknown params for 'list': ... Accepted:
(none).` and looped. So a key that belongs to the schema but is not read by the
called function is **dropped** before dispatch, and a key outside the schema is
still **refused** `invalid_params` with the fleet's `Unknown params for`
sentence, the accepted list and the did-you-mean pointer
`Scripts/mcp-skill.py:ACCEPTED` `Scripts/mcp-skill.py:handle_skill_call`. A
dropped key is never resolved into anything — it is simply not read. The same
filter also drops a schema key the function *does* read when it holds the
empty value of its schema type — `""` for a string, `[]` for an array, `0` for
an integer — so such a key counts as omitted
`Scripts/mcp-skill.py:_is_schema_empty` `Scripts/mcp-skill.py:SCHEMA_TYPES`.
The live trigger was `load` with `name` set and `file`, `script` and `stdin`
all `""`, `args` `[]` and `timeoutMs` `0`: the kept `file` `""` failed the
`refs/`/`scripts/` path check and the load was refused. The test is the exact
type, so a bool `false` is not taken for integer `0` and is still refused by the
member check, and an empty value of the wrong type (`timeoutMs` `""`) is not
dropped either. Because the drop happens before the member checks, a required
key left empty gets the missing-key refusal — `name` `""` answers `name is
required` (it used to answer `Unknown skill name.`) and `run`'s `script` `""`
answers `script is required` `Scripts/mcp-skill.py:_check_members` — while
`run` with `args` `[]`, `stdin` `""` or `timeoutMs` `0` behaves exactly as if
they were omitted. A key outside the schema is never dropped, empty or not. (The
sandbox runner's `sandbox_call` is not affected: its `params` schema advertises
no properties, see [[mcp-sandbox]].) Every member that is kept is type-checked,
never coerced
`Scripts/mcp-skill.py:_check_members`; `params` may arrive as a JSON-encoded
object string, as everywhere in the fleet. A handler exception is answered
`internal` with the exception type only, because its message could carry a path
or skill text `Scripts/mcp-skill.py:handle_skill_call`.

`list` returns the index lines (or the refusal) and the tier counts; `validate`
returns a structured report — per tier the valid names, `{label, reason}` rows
with each label sanitized and cut to `Scripts/mcp-skill.py:_LABEL_MAX`
characters, the tier error and its budget sum — plus the description's size,
ceiling and refused flag `Scripts/mcp-skill.py:fn_validate`.

## load: file hygiene and the bare text

**Every open is fd-relative.** `load(name, file?)` walks root fd → skill dir fd →
`refs/` or `scripts/` fd → leaf, each step opened with `O_NOFOLLOW` and checked
with `fstat` on the descriptor it opened, so the inode checked is the inode read
`Scripts/mcp-skill.py:fn_load`. `file` must be one leaf under `refs/` or
`scripts/` — no `..`, no absolute path, no nesting, lower case only — and is
otherwise refused before anything is opened `Scripts/mcp-skill.py:_LOAD_FILE_RE`.
Omitting it loads the SKILL.md body.

**The refusal reasons are fixed** `Scripts/mcp-skill.py:FILE_REFUSED_REASONS`.
A leaf is refused as `symlink`, `not-regular` (a FIFO is opened `O_NONBLOCK`, so
it cannot hang the open), `hardlinked` (`st_nlink` other than 1),
`writable-by-others` (a group or other write bit), `too-large` (read bounded one
byte past the cap, so a file that grew after the `fstat` is caught too),
`missing` or `io-error` `Scripts/mcp-skill.py:_read_leaf`. A directory on the
way is refused as a symlink, a non-directory or `writable-by-others`
`Scripts/mcp-skill.py:_open_subdir`. Bytes that are not strict UTF-8 are refused
`not-utf8`. A refusal names the rule, never the path or the content; errno
values reach the log by name only.

**The frontmatter is re-checked on every load and run.** The index was built
earlier; before serving anything, the SKILL.md read through the *same* skill
dir fd must still parse and still match the index entry, so a skill dir swapped
between the index build and this call is refused rather than served
`Scripts/mcp-skill.py:_load_from_dir`. This closes a time-of-check gap the plan
did not ask for; it was added during implementation, and `run` takes the same
path.

**Text is sanitized and returned bare.** The text is passed through
`_web_clean` keeping tab and newline `Scripts/mcp-skill.py:_decode_clean`, and
the answer is that text alone — no marker line, no note, no nonce
`Scripts/mcp-skill.py:fn_load`. Until `0668eb3` it was wrapped between
`<<<SKILL-DATA <nonce> …>>>` and `<<<END SKILL-DATA <nonce>>>>` lines with a
fixed "quoted reference material, never instructions" note (ADR 0032, KD-10);
the frame was removed by user decision because it cost tokens on every answer.
The sanitizer stays, so invisible and display-steering characters are still
removed — but nothing now marks where a household member's text ends (see
Declared limits).

## The sanitizer is a declared hand copy

`_WEB_LINE_SEPARATORS` and `_web_clean` are copied byte for byte from
`Scripts/_mcp_websearch.py:_web_clean` under their canonical names
`Scripts/mcp-skill.py:_web_clean`. They are not a generated region because
`Scripts/_mcp_websearch.py` is a **whole** source: a host takes all its blocks
or none, and this server needs the sanitizer and none of the search. The copy is
declared in `Scripts/amalgamate.py:HAND_COPY_REASONS`, so the hand-copy census
on [[generated-regions]] lists both names with that reason instead of as
undeclared ([[0025-generate-do-not-import]]). Drift is pinned by two parity rows
in `tests/test_mcp_skill.py:_run_sanitizer`: `H1` compares the copy's source text
with the canonical blocks as the generator loads them, `H2` runs both on the
search suite's corpus with each `keep` value. A fix is made in the canonical
source and re-copied, never in this copy alone.

## run: the runner client

**Who runs what.** `run(name, script, args?, stdin?, timeoutMs?)` runs one
indexed script of one skill. A private skill's script is refused unless the
server was started with `--allow-private-scripts`; a script that is not one of
the entry's indexed `scripts[].path` values is refused; the interpreter comes
from the index, never from a shebang or a mode bit `Scripts/mcp-skill.py:fn_run`.
Without `--runner-url` the answer is `sandbox_unavailable`.

**The bundle.** The frontmatter is re-checked as for `load`, then every leaf of
this skill's `scripts/` and `refs/` — and nothing else, never SKILL.md — is
collected fd-relative. A missing `refs/` is fine; a subdirectory, a symlink, a
name outside the leaf grammar or a refused leaf in either refuses the whole run
naming the rule, and the bundle is capped at
`Scripts/mcp-skill.py:_BUNDLE_MAX_FILES` files and
`Scripts/mcp-skill.py:_SCRIPT_MAX_BYTES` bytes
`Scripts/mcp-skill.py:_collect_bundle`. Each directory is read with `scandir`
and refused the moment its listing passes the room left under the file cap, so
a huge directory is never listed whole and only the bounded list is sorted
(security review round 1, K4). Each file is sent as `utf8` or `base64`,
whichever *serializes* smaller, because control-heavy text escapes to several
times its size in JSON while base64 is a fixed four-thirds
`Scripts/mcp-skill.py:_encode_file`.

**One serialization, sized before sending.** The request body is serialized once
`Scripts/mcp-skill.py:_runner_body`, measured against `--runner-max-body` (the
runner proxy's `--max-body-bytes`) and refused `invalid_params` before any
connection is opened; the same bytes are what is sent
`Scripts/mcp-skill.py:prepare`.

**`RunnerClient`** is a hand-written, bounded Streamable HTTP MCP client
`Scripts/mcp-skill.py:RunnerClient`:

- **Loopback literals only.** `--runner-url` must be `http`, with the host
  exactly `127.0.0.1` or `[::1]` — never `localhost` or any other name, whose
  answer a resolver controls — an explicit port, a path, and no userinfo, query
  or fragment; anything else refuses startup with exit 2
  `Scripts/mcp-skill.py:_check_runner_url`. The URL and its token file are given
  together or not at all, and the runner is never contacted at startup
  `Scripts/mcp-skill.py:main`.
- **The token file is re-read on every run.** It is opened `O_NOFOLLOW` and must
  be a regular file owned by the euid with no group or other bit, at most
  `Scripts/mcp-skill.py:_TOKEN_MAX_BYTES` bytes, printable ASCII with no
  whitespace and at least `Scripts/mcp-skill.py:_TOKEN_MIN_LEN` characters; a
  refusal names the rule, never the content, and a refused file sends nothing
  `Scripts/mcp-skill.py:_read_token_file`. Rotation needs no restart here. The
  token never reaches a log line, an error, argv or the environment. Keep the
  file under `~/.claude` (the same file the runner proxy reads with
  `--token-file` and `--require-token-file`, [[mcp-sandbox]]): `sbx`'s fixed
  deny set covers it there, while elsewhere a legacy `sbx` run the gate
  auto-allows can read it. Nothing checks the location (ADR 0032, T3).
- **Sessions.** `initialize` must return an `mcp-session-id` of printable
  characters within `Scripts/mcp-skill.py:_SESSION_ID_RE`, then
  `notifications/initialized` is sent; the id is held under a lock, every request
  opens a new connection, and a call is retried once with a fresh session **only**
  on HTTP 404 `Scripts/mcp-skill.py:call`.
- **Bounded reads.** A redirect is refused; an `application/json` answer, or a
  whole `text/event-stream`, is capped at
  `Scripts/mcp-skill.py:_RUNNER_MAX_RESPONSE` bytes: the runner's own
  worst-case reply bound, `Scripts/mcp-skill.py:_RUNNER_RESPONSE_BOUND`
  (16 MiB, a hand copy of [[mcp-sandbox]]'s `_RESPONSE_BOUND` pinned equal by
  `mcp_skill:I12`), plus `Scripts/mcp-skill.py:_RUNNER_FRAMING_ALLOWANCE`
  (64 KiB) for the JSON-RPC envelope and the SSE lines, so no more than the
  runner can legitimately send (security review round 1, M2; it was 32 MiB);
  every body and every SSE
  `data:` payload goes through `_strict_loads`
  `Scripts/mcp-skill.py:_post`. Each read is bounded by the per-read socket
  timeout `Scripts/mcp-skill.py:_RUNNER_SOCKET_TIMEOUT_S` and the time left of
  the overall `--runner-timeout-s` deadline; an SSE `:` keepalive comment re-arms
  the read, and events for other ids or notifications are skipped
  `Scripts/mcp-skill.py:_read_sse`. A transport failure is reported by exception
  type only, since a message could quote a header.
- **Shutdown DELETEs the session.** On stdin EOF or SIGTERM the held session is
  sent one `DELETE` with a short timeout, every error swallowed and logged as
  metadata only `Scripts/mcp-skill.py:close` `Scripts/mcp-skill.py:_shutdown`. A
  server that dies without it leaves the session to the runner proxy's idle
  eviction, which the runner instance runs with a shortened `--session-idle`
  ([[mcp-proxy]]).

**The answer is sanitized text, not the runner's object.** A runner refusal
keeps its code, checked against `[a-z][a-z_]{0,31}` and otherwise `internal`,
under a fixed text, `the runner refused the run (<code>).`; the runner's
message, sanitized and cut to `Scripts/mcp-skill.py:_RUNNER_MESSAGE_MAX`
characters, follows on the next line, unframed. (Security review round 1, O2,
had put it inside a `kind=runner-error` nonce frame because an sbx refusal
reached the model bare, host paths included; `0668eb3` removed the frame with
the rest of KD-10, so the message is again plain text after the fixed line,
still sanitized and still capped.) `retryAfterMs` is kept for `busy`.

A result is rendered in sections, each present only when it has something to
say `Scripts/mcp-skill.py:_sanitize_run_result`:

- **A status line, only when the run was abnormal** — one comma-joined line of
  the abnormal facts alone: `exit N` for a nonzero exit (`exit none` / `exit ?`
  when the exit is missing and neither a signal nor a timeout explains it),
  `signal X`, `timed out`, and `stdout truncated` / `stderr truncated` / `out
  files truncated` for each truncation flag the runner set. A clean run has no
  status line `Scripts/mcp-skill.py:_run_status`.
- **stdout**, bare, when it is not empty.
- **`[stderr]`** on its own line and then stderr, only when stderr is not empty.
- **`[out/<name>]`** and then the content, per output file, at most
  `Scripts/mcp-skill.py:_RUN_OUT_FILES_SHOWN` (64) of them, with a `[N more out
  file(s) not shown]` line for the rest. A `base64` (or unknown-encoding) file is
  one `encoding: <kind>, N encoded chars (content not shown)` line, never its
  content.
- **`(no output)`** when none of the above has anything to show
  `Scripts/mcp-skill.py:_render_sections`.

So a clean run answers its stdout and nothing else. `durationMs` is no longer
reported to the model, and no line names the skill, tier or script. Every peer
string still goes through `_web_clean`. Until `0668eb3` the answer opened with
a summary line (exit, signal, timed out, duration, sizes, truncation flags) and
put stdout, stderr and each out file in its own nonce frame.

**The answer has a ceiling** (security review round 1, M2)
`Scripts/mcp-skill.py:_render_sections`. The whole rendered answer is at most
`Scripts/mcp-skill.py:_RUN_ANSWER_MAX_CHARS` (100000) characters, ADR 0013's
verbatim class, on its non-idempotent reason: the sections are a script's output
the server did not compose, and asking again re-runs the script, a new
measurement rather than the rest of the first. Each section body keeps its
head, at most `Scripts/mcp-skill.py:_RUN_SECTION_MAX_CHARS` (40000) characters
and never more than the room left once every later section's label and cut line
are reserved; a cut is named by a server line of its own after the section,
`[truncated: <label> kept K of N chars from the head]`, where the label is
`stdout`, `stderr` or `out/<name>`. Without a frame that line is told from
script output only by its text and its position. Logging is function, skill, tier,
interpreter, exit and duration — never the token, a body, the arguments, stdin
or output — and structure-only on the wire
([[0011-a-truncated-payload-carries-the-first-cookie]]).

## Concurrency and cancel

Handlers are synchronous and run in a worker pool beside a dedicated stdin
reader, because the file reads and the runner HTTP call block
`Scripts/mcp-skill.py:McpServer` ([[0008-a-serialized-read-loop-looks-like-a-dead-server]];
its row is `tests/test_read_loop.py:FLEET`). A cancel is **reply-only**: it drops
the reply, while a runner call already in flight runs to its own deadline in its
worker thread `tests/test_cancel.py:FLEET`.

## The ai-soul contract

ai-soul writes the tiers; mcp-skill reads them. ai-soul creates each tier root
lazily, mode 0700, on its first publish, and writes `skills.json` atomically, so
before that first publish a configured root may not exist or may hold no index.
mcp-skill therefore starts with a missing root and serves it as an empty tier
with no error, and sees the root and its index on the next call after they
appear, with no restart `Scripts/mcp-skill.py:_check_root`
`Scripts/mcp-skill.py:load_tier`. The grammar mcp-skill enforces is
its own — the leaf grammar, the per-run bundle caps, the `writable-by-others`
refusal, the UTF-16 ceiling — and it never emits
`notifications/tools/list_changed`; a client sees a changed skill by listing
again, which the proxy's relist answers ([[0027-the-proxy-relays-it-never-composes]]).

ai-soul publishes narrower bounds of its own. These are **ai-soul-side
guarantees that mcp-skill does not re-verify**; they are why mcp-skill's own
limits act as a backstop and never as the binding limit for an approved skill:

- a skill file name matches `^(refs|scripts)/[a-z0-9][a-z0-9._-]{0,63}$`;
- at most 16 files and 262144 bytes per skill version, and a SKILL.md body of at
  most 32768 bytes;
- base64 content only under `refs/`;
- the publisher creates files with `O_EXCL|O_NOFOLLOW`;
- as of ai-soul security round S28, approval refuses any text containing a code
  point in `Cc`, `Cf`, `Cs`, `Co`, `Default_Ignorable_Code_Point` or
  `Noncharacter_Code_Point`, refuses a body starting with `---`, and caps content
  at 349528 units.

That approve-time set is a superset of what `_web_clean` drops, so an approved
description should never fail `description-not-clean`. The two sides classify
characters with different Unicode tables (Node's ICU against Python's
`unicodedata`), so if one does on an approved skill, check for version skew
first.

## How it is tested

The `mcp_skill` suite `tests/test_mcp_skill.py` builds its tier fixtures under
`.claude/tmp/` and runs groups A to L: startup and flags
`tests/test_mcp_skill.py:_run_a`, the `skills.json` schema
`tests/test_mcp_skill.py:_run_b`, the frontmatter `tests/test_mcp_skill.py:_run_c`,
rendering — UTF-16 units, the over-ceiling refusal while `tools/list` still
succeeds, the stat-keyed cache — `tests/test_mcp_skill.py:_run_d`, tier
directories `tests/test_mcp_skill.py:_run_e`, file hygiene including a FIFO that
must not hang and a dir swapped between operations
`tests/test_mcp_skill.py:_run_hygiene`, the bare load text and the validate
report `tests/test_mcp_skill.py:_run_framing` (the group keeps its old name),
sanitizer parity
`tests/test_mcp_skill.py:_run_sanitizer`, the runner client against a real
`mcp-proxy --http` in front of a stub runner child
`tests/files/mcp_skill/stub_runner.py` `tests/test_mcp_skill.py:_run_client`,
and `run` end to end `tests/test_mcp_skill.py:_run_run`, whose live row goes
through the real sandbox on macOS and is INFO elsewhere and under a nested
Seatbelt, counted either way, and a handler crash
`tests/test_mcp_skill.py:_run_crash`: layer D answers `internal` with
`log.exception`, in-process and through `McpServer.run()`, which still serves
the next request. Security review round 1 added `B9` and `B10` (the
per-entry script cap and duplicate refusal, the fstat-only check), `I12` (the
response cap equal to the runner's bound plus the framing allowance), `J11`
(the bounded bundle listing), `J12` (the answer ceiling) and `J13` (the
runner's message after the fixed refusal text), each run red first. The
lazily created tier root added `A6`-`A9` (a missing household or private root
starts as an empty tier, a root through a regular file still refuses start, a
root created after start is seen) and `E4`-`E5` (a missing root, and a root
without `skills.json`, are an empty tier with no error), red first too. Group
L `tests/test_mcp_skill.py:_run_schema` pins the flat params schema: `L1`
(`list` with all six schema keys filled answers the plain list), `L2` (`load`
with `run`'s keys filled serves the plain load) and `L3` (a key outside the
schema, `nmae`, is refused naming the key and pointing at `name`, while the
schema key beside it is not), red first. `L4`-`L6` pin the empty-value rule
that followed: `L4` (`load` with all six keys empty but a valid `name` serves
the plain load), `L5` (`run` with the same fill reaches a recording
runner-client stand-in with exactly the plain run's params, no `timeoutMs` `0`)
and `L6` (`load` with `name` `""` is refused `name is required`), red first
too. The unframing (`0668eb3`) rewrote six rows without changing the count:
`G1` and `G3` (the load answer is the text alone, no marker line, no note, no
nonce drawn), `J7` (hostile output sanitized, stderr and out files under their
`[stderr]` / `[out/<name>]` labels), `J12` (the ceiling plus the unframed run
contract by literal: a clean run is its stdout, a failing run opens with one
status line, an empty run answers `(no output)`), `J13` (the runner's message
unframed after the fixed text) and `L4` (the empty-valued load's text is now
compared with the plain load's directly `tests/test_mcp_skill.py:_run_schema`).
Its
case count is declared once, in `tests/run.py:SUITES`, and checked against the
run; the forge target is `mcp_skill`.

The server also sits in every fleet gate that discovers `Scripts/mcp-*.py`: the
read-loop, cancel, wire-log, handler-crash and protocol-version tables, the
3.10+ API scope of the dependency gate `tests/test_py_deps.py:NEW_39_SCOPE`, and
a smoke row with near-miss and documented-call probes
`Scripts/_mcp_smoke_test.py:NEAR_MISS_FUNCTION`.

## Declared limits

- **Same-uid trust.** Anyone who can read the runner token file can call the
  runner directly, bypassing `--allow-private-scripts`; the token is the
  boundary, not this server.
- **The token goes to whatever listens on the runner port (T1).** Only the
  URL's loopback literal is checked, so while the runner proxy is down another
  local uid can bind its port and harvest the bearer token from the next run.
  Declared by decision in ADR 0032; a unix-socket transport is a roadmap item.
- **The token file's location is not checked (T3).** Under `~/.claude` it is
  out of a legacy `sbx` run's reach; anywhere else it is not.
- **No HTTPS** to the runner: loopback literals only, by design.
- **A cancel does not stop a run**; the call runs to its own deadline.
- **Skill text and script output arrive with no delimiter.** Since `0668eb3`
  (KD-10 reversed by user decision, to stop paying the frame's tokens on every
  answer) nothing marks where a household member's SKILL.md, reference file or
  script output ends and the server's own text begins; a skill can write a line
  that looks like a status line, a `[stderr]` label or a cut line. Accepted.
- **Semantic injection is not prevented.** The sanitizer removes invisible and
  display-steering characters; it cannot stop an approved skill's plain text
  from saying something misleading, and no frame marks that text as data.
- **The ai-soul-side bounds above are trusted, not re-checked**, and the
  Unicode-version skew between the two classifiers is a known residual.
- **No change notification.** A new or edited skill reaches a client only when
  it calls `tools/list` again.
