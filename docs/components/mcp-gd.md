---
name: mcp-gd
type: component
status: active
title: mcp-gd — the Google Drive, Calendar and Sheets server
description: The stdio MCP server behind one gd_call tool for Google Drive, Calendar and Sheets -- twenty functions validated before any token read or socket, a strict 0600 config with default-closed owner switches, CLI-flag download and upload roots with name-based protected classes, an installed-app OAuth login with PKCE and a client secret through the generated _mcp_oauth core, a token store under a thread lock, an flock and a reread, verified TLS to a fixed host table with no redirect and no proxy, the untrusted reply envelope under the 24000 COMPOSED ceiling, how it is tested and what it declares rather than gates.
sources:
  - Scripts/mcp-gd.py
  - tests/test_mcp_gd.py
  - ClaudeCode/skills/mcp-gd/SKILL.md
verified:
  commit: cfc5590
  date: 2026-11-02
links:
  - 0036-own-code-not-a-remote-server
  - generated-regions
  - scripts
  - tests
  - mcp-skill
  - mcp-proxy
  - llm-router
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0008-a-serialized-read-loop-looks-like-a-dead-server
  - 0014-a-canonical-source-is-a-domain
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - 0030-point-at-the-near-miss-never-resolve-it
  - adding-an-mcp-server
---

# mcp-gd

`Scripts/mcp-gd.py` is a stdio MCP server that gives an agent Google Drive,
Google Calendar and Google Sheets through one tool, `gd_call`, whose twenty
functions are the keys of one hand-written table
`Scripts/mcp-gd.py:ACCEPTED` `Scripts/mcp-gd.py:handle_gd_call`. It is the
repo's own code, stdlib only on Python 3.9, talking to Google's REST APIs over
verified TLS; why it is not one of the remote Google MCP servers, and every
decision below, is [[0036-own-code-not-a-remote-server]]. The model-facing
reference — every function, parameter and refusal code — is the skill
`ClaudeCode/skills/mcp-gd/SKILL.md`. The operator half — the one-time Google
Cloud setup, every config key with its default, the launch flags and what
`check_flags` refuses, the `login` procedure, the 7-day Testing-status expiry,
a troubleshooting table of the operator-side refusal codes and the registration
shapes — is the sibling file `ClaudeCode/skills/mcp-gd/setup.md`, split out by
owner decision so it is not loaded into the model's context on every skill
activation; SKILL.md keeps one paragraph listing the operator-side codes and
pointing at it. That file is not yet in this page's `sources`: it is
uncommitted, and every source must exist at `verified.commit`, so it joins the
list when the page is next verified against the commit that contains it.

**Status of this page.** The server and its suite landed in `7787308` and the
skill in `cfc5590`; the page was written while the code was uncommitted and was
promoted to `active` against those commits. The smoke table
carries the server as unregistered `Scripts/_mcp_smoke_test.py:SERVERS`; the
registration in `~/.claude.json` and the mcp-proxy child on the Linux host are
the owner's steps, described for the operator in `setup.md` and not on this
page beyond the launch line.

## The launch line and the config

`main()` takes `--config PATH` (required), `--project-root`, `--download-dir`,
a repeatable `--upload-root`, `--debug` and `--log-file`; `login` is a
subcommand handled before the server loop `Scripts/mcp-gd.py:main`. A bad config
or flag exits 2 before anything is read from stdin, and the message names the key
or flag and the rule, never a value. The token file is **not** read at startup:
a valid config with no token yet starts and answers `initialize` and
`tools/list`, which is what lets the smoke probes run without a Google account.

**The config is a secret file.** It holds the OAuth client secret, so it is read
the way llm-router reads its own: `O_NOFOLLOW`, then `fstat` on the descriptor —
a regular file this euid owns with no group or other bit, under a size cap — and
its directory must be this euid's and not group/other writable
`Scripts/mcp-gd.py:_gd_read_secret_file`. The JSON is strict (`_strict_loads`,
then a second parse whose only job is to refuse a repeated key,
`Scripts/mcp-gd.py:_refuse_duplicate_keys_parse`), and an unknown key, a missing
required key or a wrongly typed optional one is refused by name
`Scripts/mcp-gd.py:load_config`.

- Required: `client_id`, `client_secret`, `token_file`, `scopes`
  `Scripts/mcp-gd.py:_REQUIRED_KEYS`. `scopes` is a non-empty, repeat-free subset
  of the three full scope URLs `Scripts/mcp-gd.py:GD_SCOPES`, never a short name.
  `token_file` is absolute, already normalised, not ending `.lock` or `.tmp`
  (the server's own sidecar names), not the config file itself, and its directory
  is owned by this euid and not group/other writable
  `Scripts/mcp-gd.py:_check_token_dir`.
- Optional, each with a default `Scripts/mcp-gd.py:GdConfig`: `login_port`,
  `inline_download_bytes`, `max_transfer_bytes`, `request_timeout`, and four
  **owner switches, all default-closed**: `allow_send_updates` (false),
  `attendee_domains` (empty, which means no domain restriction once attendees
  are given at all), `allow_formulas` (false) and `protected_paths` (empty, extra
  project-relative paths never read or written). Booleans must be JSON booleans,
  never integers `Scripts/mcp-gd.py:_cfg_bool`.

**Local files are bounded by the launch line, not the config.** There is no
`download_dir` and no `upload_roots` config key, and no default directory:
without `--download-dir` every `save_to` is refused with a fixed message while an
inline download still works, and without `--upload-root` (which needs
`--project-root`) every `local_path` upload is refused while `content` and
`content_base64` still work `Scripts/mcp-gd.py:_MSG_NO_DOWNLOAD_DIR`
`Scripts/mcp-gd.py:_MSG_NO_UPLOAD_ROOT`. The flags are checked once at startup
`Scripts/mcp-gd.py:check_flags`: every path absolute with no `..`; the download
directory created at 0700 one level if missing, opened `O_NOFOLLOW` and checked
on the descriptor `Scripts/mcp-gd.py:_open_checked_dir`, and refused when it is
`/`, `$HOME`, the token directory or above or inside it, or a secret, control or
system directory `Scripts/mcp-gd.py:_gd_sensitive_dir`; an upload root must
exist inside `--project-root` and must not hold the token directory; the project
root must not be `/` or `$HOME`.

## The dispatcher

The order is fixed and pinned by the suite: the `function`/`f` and
`params`/`p` pick (both spellings set is refused as ambiguous), the function
lookup with a did-you-mean pointer, the unknown-parameter refusal through the
generated `_unknown_params_refusal` with its pointer that never resolves
([[0030-point-at-the-near-miss-never-resolve-it]]), the per-function shape
checks — ids, A1 ranges, RFC 3339 times, enums, local paths resolved but not
opened — and **only then** the token read and the socket, inside the handler
`Scripts/mcp-gd.py:handle_gd_call` `Scripts/mcp-gd.py:_check_drive_list`. So a
malformed call never costs a token refresh and never reaches Google, and the
smoke probes `Scripts/_mcp_smoke_test.py:NEAR_MISS_PARAM`
`Scripts/_mcp_smoke_test.py:DOCUMENTED_CALL` run against a token file that does
not exist.

A call with no function gets an unflagged status reply: the function list,
whether local downloads and uploads are enabled (yes or no, never a path) and the
ceiling `Scripts/mcp-gd.py:_status_text`. A refusal is a tool result with
`isError` and `{"error": {"code", "message"}}`, the message cleaned to one line
`Scripts/mcp-gd.py:_tool_refusal` ([[0010-a-handler-failure-must-reach-iserror]]).
The codes are `invalid_params`, `internal` and the transport's own short words
`Scripts/mcp-gd.py:GdError`; the skill lists them all. A handler crash is
answered `internal` with the exception type only, logged with `log.exception`
at layer D, and the `McpServer` wrap does the same at layer W
`Scripts/mcp-gd.py:_handle_tool_call`.

**The default-closed policy checks run in the shape layer.** The three calendar
mutations call `Scripts/mcp-gd.py:_gd_send_updates` before any other check:
`send_updates` defaults to `none`, is always sent, and any other value is refused
unless the config sets `allow_send_updates`. An attendee outside a non-empty
`attendee_domains` is refused naming its position, never the address or the list
`Scripts/mcp-gd.py:_gd_attendees`; an event created with attendees and no
invitations carries a fixed note `Scripts/mcp-gd.py:_CAL_ATTENDEE_NOTE`. Sheets
writes default to `RAW`; under `USER_ENTERED` a string cell whose first non-space
character is `=`, `+`, `-` or `@` is refused unless `allow_formulas` is set, and
even then a best-effort scan keeps the fetching and linking functions refused
`Scripts/mcp-gd.py:_gd_cell_formula` `Scripts/mcp-gd.py:_FORMULA_DANGER_RE`. A
Drive `q` must keep its parentheses and quotes balanced, so it cannot close the
group the server AND-s with the folder and trashed clauses
`Scripts/mcp-gd.py:_gd_q_balanced`.

**Drive is never permanently deleted.** `drive_trash` is a PATCH of
`trashed: true` `Scripts/mcp-gd.py:fn_drive_trash`; no code path issues a file
DELETE or empties the trash, and the suite scans the source and the wire for one.

## Local files

`save_to` is a name relative to `--download-dir`; `local_path` is
project-relative or absolute and must resolve inside one `--upload-root`. Both
are resolved with `realpath`, must stay inside their root, must not be a
directory, and then pass the protected classes
`Scripts/mcp-gd.py:_gd_resolve_local_path` `Scripts/mcp-gd.py:_gd_protected_class`,
evaluated against the path relative to its governing root, relative to
`--project-root` when inside it, and by basename anywhere:

- **Secret classes, for reads and writes** `Scripts/mcp-gd.py:_gd_parts_class`:
  `.ssh`, `.aws`, `.gnupg`, `.claude`, `.git`, `.kube`, `.docker` and
  `.config/gcloud` directories, `.env` and `.env.*`, credential files such as
  `.netrc` and `.git-credentials`, key and certificate suffixes, `id_*` key
  prefixes `Scripts/mcp-gd.py:_GD_SECRET_DIRS`.
- **Code classes, for writes**: `Scripts/`, `tests/`, `.github/`, build and
  agent-instruction files, `.sh`, `.py`, `.pth` `Scripts/mcp-gd.py:_GD_CODE_NAMES`,
  so a download directory pointed at the repo still cannot plant something that
  runs later.
- **The server's own files**, by case-folded realpath and by device and inode so
  a hard link is caught: the config, the token file, its lock, the write-back's
  temp files and the `--log-file` `Scripts/mcp-gd.py:_gd_is_own_file`.
- The config's `protected_paths`.

A download streams to an exclusively created 0600 temp next to the target and is
linked into place without replacing anything; an existing target is refused
`file_exists`, and there is no `overwrite` parameter
`Scripts/mcp-gd.py:_gd_save_stream` `Scripts/mcp-gd.py:_gd_link_exclusive`. An
upload opens its file `O_NOFOLLOW`, regular only, and refuses a file with more
than one hard link `Scripts/mcp-gd.py:_gd_open_for_read`.

## OAuth and the token store

**The OAuth core is generated, not written here.** The server takes the
`_mcp_oauth` blocks on the marker identical to llm-router's — the generator takes
a source's blocks whole or not at all, so the device and JWT blocks ride along
unused ([[generated-regions]], [[0025-generate-do-not-import]]). Its Google row
is built with the source's row type and holds no secret
`Scripts/mcp-gd.py:_gd_provider`; the client secret reaches the token endpoint
only as the optional keyword argument the two request builders gained for this
host `Scripts/_mcp_oauth.py:_oauth_exchange_request`
`Scripts/_mcp_oauth.py:_oauth_refresh_request`.

**`login`** is the installed-app flow: a loopback listener bound to `127.0.0.1`
only (on `login_port` or an ephemeral port), PKCE S256 and a state value, the
authorize URL printed to stderr and a browser opened unless `--no-browser`, the
code exchanged with the client secret, a refresh token required, and the token
file written 0600 `Scripts/mcp-gd.py:run_login`. The attempt ends at the first
valid callback, a denial, the `--timeout` deadline, or after the generated
acceptor's bound of connections that made no valid callback
`Scripts/mcp-gd.py:OAUTH_CALLBACK_BAD_LIMIT`. It prints only the granted scope
names. A headless host logs in through an SSH port forward with a fixed
`login_port`, or takes a token file copied from another machine.

**`TokenStore`** hands every API call an access token `Scripts/mcp-gd.py:TokenStore`.
Three layers keep two refreshes from racing: a thread lock in the process, an
`flock` on the `<token_file>.lock` sidecar across processes, and a reread of the
token file after both, so whoever arrives second uses the first one's token and
makes no network call. A token is due `OAUTH_REFRESH_SKEW_S` before expiry but is
not refreshed again before the `OAUTH_MIN_REFRESH_INTERVAL_S` floor; a 401 forces
one refresh of exactly the token it condemned. The write-back is the KD-10
sequence relative to the checked token directory — an `O_EXCL` 0600 temp, fsync,
rename, fsync of the directory — and a failed write-back is not fatal for the
call that refreshed. A rejected refresh token is answered `relogin` with the
7-day Testing-status hint `Scripts/mcp-gd.py:_gd_oauth_refusal`.

## The transport

Every request goes through one chokepoint `Scripts/mcp-gd.py:_gd_request`. The
host is a key of a fixed table — Drive and Calendar on `www.googleapis.com`,
Sheets on `sheets.googleapis.com`, the token endpoint on `oauth2.googleapis.com`
— checked before the token file is read and again before a socket exists
`Scripts/mcp-gd.py:_GD_HOSTS`; there is no override by config, flag or
environment. The connection class dials exactly that host on port 443, with a
verifying context whose certificate, host name and TLS 1.2 floor are asserted
before any socket and which ignores `SSL_CERT_FILE` / `SSL_CERT_DIR`
`Scripts/mcp-gd.py:_GdHttpsConnection` `Scripts/mcp-gd.py:_gd_tls_context`. No
proxy variable is honoured. A 3xx is an error and is never followed, so the
`Authorization` header goes to the allowlisted host and nowhere else; a 401 is
refreshed and retried once **only when no body was sent**, so a mutation is never
sent twice. Google's error body is reduced to the status, the first reason and
the message, scrubbed of every secret the store has seen and cleaned to one line
`Scripts/mcp-gd.py:_gd_reduce_error`. Logs are structure only
([[0011-a-truncated-payload-carries-the-first-cookie]]).

Downloads are read in chunks and uploads go out in at most one 8 MiB chunk at a
time; above the multipart limit an upload is resumable, and the session URL
Google returns — the only server-supplied URL — must be `https` on
`www.googleapis.com`, port 443, a path under the Drive upload path and no `%`
`Scripts/mcp-gd.py:_gd_session_target`.

## The reply envelope and the ceiling

A success is `{function, ok, <data>, untrusted, note[, truncated]}`
`Scripts/mcp-gd.py:_gd_reply`. Every one of the twenty functions returns
Google-supplied text, so every success is flagged `untrusted: true` with the
fixed data-not-instructions note `Scripts/mcp-gd.py:GD_UNTRUSTED_NOTE`, and its
strings pass `Scripts/mcp-gd.py:_gd_clean`: C0/C1 controls, bidi controls,
zero-width characters, the tag block and every `Cf`, `Zl` and `Zp` character are
dropped, newlines and tabs kept only in multi-line fields. Keys are cleaned too
and the envelope's `function` and `ok` always win over the body. The cleaner is
the server's own; parity with the websearch sanitizer is not claimed, and because
it does not reuse the canonical sanitizer's names it needs no hand-copy row
([[mcp-skill]] is the server that does).

The ceiling is the generated `DEFAULT_MAX_ANSWER_CHARS` / `_max_answer_chars`
pair, ADR 0013's COMPOSED class `Scripts/mcp-gd.py:DEFAULT_MAX_ANSWER_CHARS`
([[0013-the-ceiling-is-a-payload-class]]): the server composes every reply, and
a caller recovers the rest by asking narrower (`fields`, `page_size`,
`page_token`) or by passing `max_answer_chars`. Over it, the largest top-level
list loses items from the tail and `truncated` names how many; a value that
cannot be cut by whole items, a base64 body above all, is refused `too_large`
rather than cut. An inline download is bounded twice — by
`inline_download_bytes` and by whether its base64 plus envelope fits the
effective ceiling — and the refusal points at `save_to`
`Scripts/mcp-gd.py:_gd_inline_refusal`.

## Concurrency and cancel

Handlers are synchronous and run in a worker pool beside a dedicated stdin
reader thread ([[0008-a-serialized-read-loop-looks-like-a-dead-server]]; the row
is `tests/test_read_loop.py:FLEET`). A stdin frame is bounded
`Scripts/mcp-gd.py:MAX_FRAME`: an overlong line is answered once and drained,
never buffered whole `Scripts/mcp-gd.py:_FrameReader`, and a frame nested too
deeply or not valid UTF-8 is answered -32700 while the server keeps reading
`Scripts/mcp-gd.py:McpServer` (security review F1-F3; the other servers do not
do this yet, see the ADR).
A cancel is **reply-only**: it drops the reply while the HTTPS call runs to its
own deadline `tests/test_cancel.py:FLEET`, so a cancelled trash, move, event
change or sheet write still completes at Google.

## How it is tested

The `mcp_gd` suite `tests/test_mcp_gd.py` imports the server in-process and
points it at a plain-HTTP loopback fake Google by swapping the connection
factory, the one seam, which can choose neither a host nor a URL
`Scripts/mcp-gd.py:_GD_CONNECTION_FACTORY`; no real Google host is contacted.
Its groups A to S cover the config `tests/test_mcp_gd.py:_run_a`, the flags
`tests/test_mcp_gd.py:_run_b`, the protocol, the dispatcher, validation before
any token read or socket (trapped) `tests/test_mcp_gd.py:_run_e`, the transport
`tests/test_mcp_gd.py:_run_f`, a handler crash, the token store
`tests/test_mcp_gd.py:_run_h`, `login` `tests/test_mcp_gd.py:_run_i`, a canary
scan for planted secrets `tests/test_mcp_gd.py:_run_j`, all twenty functions'
parameter layer `tests/test_mcp_gd.py:_run_k`, local files
`tests/test_mcp_gd.py:_run_l`, the envelope `tests/test_mcp_gd.py:_run_m`, the
Drive, transfer, Calendar and Sheets handlers against the fake peer
`tests/test_mcp_gd.py:_run_n` `tests/test_mcp_gd.py:_run_o`
`tests/test_mcp_gd.py:_run_p` `tests/test_mcp_gd.py:_run_q`, the live
`McpServer.run()` behaviour the static gates cannot see — cancel, crash, a
debug-level wire-log canary across every function `tests/test_mcp_gd.py:_run_r`
— and one row per theme of the code-mode security review's fix list, each written
red before its fix `tests/test_mcp_gd.py:_run_s`. Its case count is declared once
in `tests/run.py:SUITES`; the forge target is `mcp_gd`. The `client_secret`
argument of the OAuth builders is gated in `tests/test_mcp_oauth.py`.

The server also sits in every fleet gate that discovers `Scripts/mcp-*.py`: the
read-loop, cancel, wire-log, handler-crash and protocol-version tables, the 3.10+
API scope of `tests/test_py_deps.py:NEW_39_SCOPE`, and a smoke row launched
against a config generated at runtime in place of the `{gd_config}` placeholder
`Scripts/_mcp_smoke_test.py:GD_CONFIG_TOKEN`, with near-miss and documented-call
probes `Scripts/_mcp_smoke_test.py:NEAR_MISS_FUNCTION`.

## Declared limits

The full list, with the review finding each one comes from, is in
[[0036-own-code-not-a-remote-server]]. In short:

- **A cancel does not stop a mutation**; only the reply is dropped.
- **The `drive` scope is the full one**, owner-accepted; the token can read and
  change everything the account can.
- **The protected classes are name-based and not exhaustive.** A secret in an
  arbitrarily named file inside an upload root is uploadable.
- **The owner switches reopen what they close**: `allow_send_updates`,
  `allow_formulas` and an empty or wide `attendee_domains`. With
  `attendee_domains` empty, attendees are unrestricted (invitations still need
  `allow_send_updates`). The formula scan under `allow_formulas` is best effort.
- **Prompt injection is marked, not prevented.** The `untrusted` flag and the
  cleaner cannot stop plain text in a document from saying something misleading.
- **No resolved-address policy, no proxy, no pinning beyond the system CA.** Every
  name is verified by TLS against a fixed host set; a poisoned resolver can deny
  service or reach a host with a valid Google certificate.
- **Same-uid trust.** Anyone running as this user can read the 0600 token file.
- **No retry** beyond the one refresh after a 401 and the resumable-upload cases;
  quota and rate errors are relayed.
- **Testing-status refresh tokens expire after 7 days**; the fix is `login`.
- **Mac and Linux only** (`fcntl`).
