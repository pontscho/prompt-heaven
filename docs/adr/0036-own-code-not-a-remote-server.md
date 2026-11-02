---
name: 0036-own-code-not-a-remote-server
type: adr
status: draft
title: Own code, not a remote server, for Google Drive, Calendar and Sheets
description: Decision to reach Google Drive, Calendar and Sheets through the repo's own stdlib MCP server, Scripts/mcp-gd.py behind one gd_call tool, rather than through a remote Google MCP server -- an installed-app OAuth client with PKCE whose client secret is a new optional argument of the _mcp_oauth canonical source, a token store under a thread lock, an flock and a reread, a host-local verified-TLS transport to a fixed host table, default-closed owner switches against invitation mail, formula injection, untrusted remote text and local-file abuse (D11-D16), the 24000 COMPOSED ceiling, download and upload roots as launch flags with no default directory, the three review rounds and what each changed, the alternatives rejected, the owner-unconfirmed defaults, and the declared limits -- among them the fleet-wide stdin gaps this server closed for itself only.
targets:
  - Scripts/mcp-gd.py
  - Scripts/_mcp_oauth.py:_oauth_exchange_request
  - Scripts/_mcp_oauth.py:_oauth_refresh_request
  - tests/test_mcp_gd.py
  - tests/test_mcp_oauth.py
links:
  - mcp-gd
  - generated-regions
  - llm-router
  - mcp-proxy
  - scripts
  - tests
  - 0010-a-handler-failure-must-reach-iserror
  - 0011-a-truncated-payload-carries-the-first-cookie
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0016-a-cell-may-not-forge-a-boundary
  - 0024-pure-python-39-and-the-stdlib
  - 0025-generate-do-not-import
  - 0026-speak-chrome-from-the-stdlib-verify-by-default
  - 0028-route-by-model-translate-at-the-edge
  - 0030-point-at-the-near-miss-never-resolve-it
  - 0032-a-runner-behind-a-proxy
---

# ADR 0036: Own code, not a remote server, for Google Drive, Calendar and Sheets

Status: accepted 2026-11-01 as the design the code in the working tree
implements, on top of `83ef082`, and not yet committed when this page was
written. That is why the frontmatter says `draft` and carries `targets:`
instead of `sources:` and `verified:`, as [[0032-a-runner-behind-a-proxy]] did:
a `verified.commit` cannot vouch for code no commit holds yet. Once the code
lands, the page is promoted to `active` with that commit and is append-only from
then on. Every number below is a constant read from the code or a count at the
moment of the decision. The shape of the server is [[mcp-gd]]; the model-facing
reference is `ClaudeCode/skills/mcp-gd/SKILL.md`.

## Context

The owner wanted an agent to read and change their Google Drive files, calendar
events and spreadsheets from Claude Code on the Mac, and from the Linux host
through an [[mcp-proxy]] child. Two kinds of ready-made remote server exist: the
connectors claude.ai offers, and Google's own remote MCP endpoints for Drive,
Docs and Sheets. By the owner's account neither is usable from Claude Code
through the proxy — the connectors belong to claude.ai, and the proxy relays
stdio children, not a remote OAuth-gated endpoint — and the owner asked for the
repo's own code. This record does not re-measure that claim; it is the owner's
premise.

Own code in this repo has fixed rules: one single-file server, Python 3.9 and the
stdlib only ([[0024-pure-python-39-and-the-stdlib]]), shared plumbing generated
rather than imported ([[0025-generate-do-not-import]]), a handler failure that
reaches `isError` ([[0010-a-handler-failure-must-reach-iserror]]),
structure-only wire logs ([[0011-a-truncated-payload-carries-the-first-cookie]]),
a declared ceiling class ([[0013-the-ceiling-is-a-payload-class]]) and a
near-miss pointer that never resolves ([[0030-point-at-the-near-miss-never-resolve-it]]).

## Decision

**One server, one tool.** `Scripts/mcp-gd.py` exposes `gd_call` with twenty
functions — seven Drive, seven Calendar, six Sheets — over Drive v3, Calendar v3
and Sheets v4 REST. Every function has a hand-written row of the parameters it
reads, and argument validation runs before the token file is read and before any
socket is opened, so the smoke probes never dial Google and a malformed call
never costs a refresh.

**D1 — the client secret is a new argument of the canonical OAuth source.**
`Scripts/_mcp_oauth.py` already held PKCE, the loopback callback and the
token-answer parser for llm-router. Google's installed-app clients still require
a `client_secret` at the token endpoint, so the code and refresh request builders
gained a keyword-only `client_secret=""`, appended after the existing fields when
non-empty, validated as a string with no whitespace or control character, and
refused with a `ValueError` naming the field and never the value. With it empty
the body is byte-identical to the one built before, so llm-router's requests did
not change; a golden-bytes case pins that. Client authentication by form body is
RFC 6749 section 2.3.1, generic OAuth, not a Google quirk — the domain argument of
[[0014-a-canonical-source-is-a-domain]]. The secret is never a field of the
provider row, which holds no secret and is printed into logs. mcp-gd takes the
source on the marker identical to llm-router's, so the device and JWT blocks
ride along unused ([[generated-regions]]). `mcp_oauth` moved from 68 to 77 typed
cases with this change (group C 5 to 14).

**D2 — the HTTPS transport is host code.** `_GdHttpsConnection` connects only to
a value of a fixed host table on port 443, with a verifying context whose
certificate, host-name and TLS 1.2 floor checks are asserted before any socket.
It ignores proxy variables and, after the code review, `SSL_CERT_FILE` and
`SSL_CERT_DIR` too (F48). It is not a canonical source: it has one consumer, and
llm-router's own verified connection carries backend-specific seams. A third
verified-HTTPS host is the trigger to lift a canonical `_mcp_https.py`.

**D3 — three layers keep two refreshes from racing.** A thread lock, an `flock` on
a `<token_file>.lock` sidecar and a reread of the token file after both, so the
second arrival finds the first one's token. The canonical constants decide timing:
due `OAUTH_REFRESH_SKEW_S` (300 s) before expiry, never again before
`OAUTH_MIN_REFRESH_INTERVAL_S` (60 s) except for the one forced refresh after a
401. The write-back is llm-router's KD-10 sequence relative to a checked
directory fd. A 401 is retried once only when no body was sent; a mutation is
never sent twice.

**D4 and D6 — bounded transfers and one server-supplied URL.** Downloads stream in
256 KiB chunks, uploads send at most one 8 MiB chunk at a time, multipart up to
5 MiB and resumable above. The resumable session `Location` is the only URL Google
supplies, and it must be `https` on `www.googleapis.com`, port 443, under the
Drive upload path with no `%` (F21). A 3xx is never followed.

**D5 and D14 — no secret in a reply or a log.** The `Authorization` header is built
last from a local that no log call sees; Google's error body is reduced to status,
reason and message, scrubbed of every secret the store holds, cleaned to one line
and capped.

**D8 — the ceiling is the generated 24000, class COMPOSED.** The server composes
every reply (an envelope around trimmed Google fields and list rows), and a caller
recovers more by asking narrower or by passing `max_answer_chars`. The earlier
draft wanted 100 000 VERBATIM; rejected because a hand constant cannot share the
generated marker (a hand copy of `_max_answer_chars` and a declaration), and
because the one verbatim payload, a file body, has its own escape: `save_to`. An
inline download is therefore small — `inline_download_bytes` 16 000 raw bytes by
default, 21 336 base64 characters — and a base64 body is refused, never cut.
Sheets values are JSON arrays, not a rendered table, so
[[0016-a-cell-may-not-forge-a-boundary]] does not apply; a table output would
have to join its sweep.

**D11-D16 — default-closed owner controls.** The plan-mode security review found
that an agent with this tool is also a channel out of the account, and every one
of its findings became a control that is closed unless the owner opens it:

- **D11, invitation mail.** `send_updates` defaults to `none` and anything else is
  refused before any other check unless the config sets `allow_send_updates`.
  `attendee_domains`, when non-empty, restricts attendees; an event with attendees
  and no invitations carries a fixed note.
- **D12, formula injection (CWE-1236).** Sheets writes default to `RAW`; under
  `USER_ENTERED` a cell starting with `=`, `+`, `-` or `@` is refused unless
  `allow_formulas` is set, and even then `IMPORT*`, `IMAGE` and — after the code
  review — `HYPERLINK`, `QUERY`, `GOOGLEFINANCE`, `GOOGLETRANSLATE` stay refused
  (F14, best effort).
- **D13, untrusted remote text.** Every success carries `untrusted: true` and a
  fixed data-not-instructions note, and Google strings are cleaned of controls,
  bidi, zero-width, tag and separator characters by the server's own cleaner,
  named so that it needs no hand-copy row and claims no parity with the websearch
  sanitizer.
- **D14, error text and logs (CWE-117)**: one clean, capped line.
- **D15, local files.** The download directory and the upload roots are launch
  flags, `--download-dir` and a repeatable `--upload-root` inside
  `--project-root`, not config keys — the owner controls the launch line — and
  there is **no default download directory**, in the project root or anywhere
  (owner decision). Without a flag the local path is refused and the inline path
  still works. Secret classes are refused for reads and writes, code classes for
  writes, the server's own config, token, lock, temp and log files always, plus
  the config's `protected_paths`. A download never overwrites; the `overwrite`
  parameter was removed.
- **D16, login.** The callback listener binds `127.0.0.1` explicitly, the attempt
  has an overall `--timeout` (300 s default), and it ends at the first valid
  callback. *Deviation from the plan:* D16 asked that a second connection end the
  attempt; the implementation uses the generated acceptor's own bound, which ends
  it after `OAUTH_CALLBACK_BAD_LIMIT` (16) connections that made no valid
  callback (see F42 below).

## The review history

1. **Plan inspection.** The plan inspector's round produced the findings labelled
   H, M, L and P in the plan, among them H1 (take the generated 24000 COMPOSED
   pair, D8), H2 (validate before any token read or socket, and the `{gd_config}`
   smoke placeholder), M1 (the canonical refresh constants, D3a), M3 (the
   identical marker), M6 (land every roster gate row with the skeleton, so no
   wave ends red) and P-H1 (the smoke near-miss and documented-call rows from the
   first commit). All were folded into the plan before code.
2. **Plan-mode security review: REVISE.** One HIGH (S-H1, invitation mail as an
   exfiltration channel), MEDIUMs for formula injection, untrusted remote content
   and local-file confinement, LOWs for error-text forging, local paths and login.
   Each became D11-D16 above, and the plan was revised before code.
3. **Code-mode security review** (verdict 20261101-200000): six find lanes
   produced 75 raw findings, merged to 52 (F1-F52) and verified in seven themed
   batches. Verdict APPROVE after fixes: no HIGH, one MEDIUM (F1), the LOWs
   below fixed, the rest INFO, refuted or declared. Every fix was written red
   first as a row of `mcp_gd` group S, and the F1 and F52 rows were
   mutation-checked by reverting the fix. Fixed:
   - F1 a `RecursionError` from a deeply nested frame answered -32700; F3 a
     frame that is not UTF-8 answered -32700 and the server keeps reading; F2 a
     stdin line bounded at `MAX_FRAME`, answered once and drained.
   - F7/F8 integers parsed only from short ASCII digit strings; F10 lone
     surrogates refused; F12 a Drive `q` cannot close its parentheses; F13 `*`
     dropped from the `fields` mask; F14 the formula list widened; F15 encoded
     `.`, `/` and `\` refused in a path and in a quoted A1 sheet name.
   - F16 reply keys cleaned before the reserved-key check, the envelope's
     `function` and `ok` winning; F17 the cleaner drops by Unicode category, and
     colliding cleaned keys get a suffix; F20 no exception text in a log or reply
     beyond the type and a cleaned capped message.
   - F21 the resumable `Location` check tightened; F52 a repeated
     `Content-Length` or `Location` header fails closed.
   - F24 (its cheap half) an upload source with more than one hard link refused;
     F25 more name classes (agent instruction files, shell rc files, `.kube`,
     `.docker`, `.config/gcloud`); F26/F27 the flag roots checked against the
     token directory, secret and system directories, `/` and `$HOME`; F28 the
     token temp files and the `--log-file` protected too.
   - F31/F32 `token_file` normalised, no `.lock`/`.tmp` ending, not the config;
     F33/F34 the token directory opened `O_NOFOLLOW` and fixed by `fchmod` on the
     fd, the config's directory owner- and mode-checked.
   - F40 a refreshed scope filtered to the three known URLs and the expiry
     clamped; F41 a `token_type` other than Bearer refused — in the host, so the
     canonical source did not change for it; F44 a failed write-back is not fatal
     for the call that refreshed; F45 the token POST under a total deadline; F47
     stale temp files of this uid swept; F48 the trust store no longer honours
     `SSL_CERT_FILE` / `SSL_CERT_DIR`.

`mcp_gd` closed at 222 typed cases in groups A to S (A 13, B 8, C 9, D 9, E 5,
F 10, G 2, H 16, I 11, J 2, K 18, L 10, M 9, N 15, O 19, P 18, Q 15, R 7, S 26),
one of them INFO where it cannot run (A4 needs root).

## Alternatives rejected

1. **A remote Google MCP server or a claude.ai connector.** Not usable from Claude
   Code through the proxy, by the owner's account, and the owner wanted own code.
2. **The official `google-api-python-client` / `google-auth`.** Third-party
   ([[0024-pure-python-39-and-the-stdlib]]).
3. **A host-local OAuth fork in mcp-gd.** A second PKCE, loopback and token-parse
   implementation duplicates security-critical code the canonical source already
   holds, for a parameter that is generic OAuth.
4. **The client secret as an `OAuthProvider` row field.** The row holds no secret
   and is printed into logs.
5. **A canonical `_mcp_https.py` now.** One consumer; revisit at the third.
6. **The urllib opener (mcp-jenkins style) or the Chrome client.** urllib follows
   redirects and honours proxy variables by default, both wrong for a fixed
   allowlist; the Chrome client ([[0026-speak-chrome-from-the-stdlib-verify-by-default]])
   is a fingerprinting client for scraping.
7. **Three tools (drive, calendar, sheets).** The owner asked for one; one tool
   keeps the description and the proxy's name set small.
8. **A permanent delete or bulk helpers.** Owner decision; the code path does not
   exist and the suite scans for one.
9. **100 000 VERBATIM** as the ceiling (D8 above).
10. **`download_dir` / `upload_roots` as config keys, or a default download
    directory in the project root.** Replaced by launch flags with no default
    (owner decision, D15).

## Owner-unconfirmed defaults

These were chosen by the plan and the implementation and are recorded for the
owner to confirm or change:

- **Upload roots must lie inside `--project-root`**, so `--upload-root` needs
  `--project-root`; a directory outside the project can never be an upload source.
- **The 24000 ceiling** (D8) and its consequence, a 16 000-byte inline download.
- **The new config keys and their defaults**: `allow_send_updates` false,
  `attendee_domains` empty (which means *no* restriction once attendees are given),
  `allow_formulas` false, `protected_paths` empty; also `login_port` 0,
  `inline_download_bytes` 16 000, `max_transfer_bytes` 256 MiB, `request_timeout`
  60 s.

## Declared limits

Not fixed, by decision; each comes from the code-mode review's "declared, not
fixed" list or from the plan.

- **F4** Unknown top-level `gd_call` arguments beside `function` / `params` are
  ignored, and an empty `params` beside a filled `p` silently picks `p`. Unknown
  keys inside `params` are refused.
- **F5** A cancel or an abandoned request keeps its worker thread, sockets and
  mutation running (reply-only cancel), and `os._exit` after stdin EOF skips
  cleanup, so an in-flight download can leave a `.part` file in the download
  directory and an in-flight write-back a 0600 token temp (the latter is swept on
  the next write-back, F47).
- **F6a** The stdout write blocks the event loop; a stalled client pipe freezes
  the server. Fleet template behaviour.
- **F9** `_bool_param` reads any string outside a short false list as true. The
  generated fleet-wide block; not changed here.
- **F11** `cal_create` / `cal_update` do not cross-check start before end or a
  matching `date` / `dateTime` kind, and `time_zone` is a charset check, not an
  IANA name; Google validates.
- **F18** Visible text in Google content still reaches the model; the untrusted
  flag marks it and cannot stop it. Inherent.
- **F19** Secret scrubbing is exact-substring over known secrets only; an encoded
  or cut secret, a token another process rotated, the authorization code and the
  upload session URL are not in the set, and a 2xx body is only cleaned.
- **F22** The resumable loop budget allows on the order of two thousand bearer
  PUTs for the largest permitted file against a peer that reports tiny progress.
- **F23** Download parent TOCTOU: containment is checked by realpath and the
  file is written through unpinned parent directories a network round trip later;
  a same-uid actor can swap an intermediate directory. A `dir_fd` walk is the fix.
- **F24 (rest)** The upload read path has the same intermediate-component swap,
  and the secret classes are names: a hard link to a secret is now refused (the
  cheap half, `st_nlink`), but a copy of one under an innocuous name inside an
  upload root is uploaded.
- **F29** No download quota: the transfer cap is configurable up to 16 GiB and the
  number of files is unbounded.
- **F30** Download integrity rests on the HTTP framing; the reported sha256 is
  over the received bytes and is not compared with Drive's checksum.
- **F35** Process-wide globals set in `main()` with fail-open fallbacks when unset
  (unreachable through the normal launch).
- **F36** `attendee_domains` defaults to unrestricted, and an event with
  attendees lands on their calendars even with no mail sent; public suffixes are
  accepted as domains. Documented.
- **F37 / F39** No owner switch for the other destructive operations
  (`cal_delete`, `sheets_clear`, `sheets_write`, a content-replacing
  `drive_upload`) and no allowlist of calendars, spreadsheets or files: Google's
  ACLs are the boundary. By design.
- **F38** Only the full scopes are offered; no read-only or `drive.file` scope,
  and the granted scope is not enforced locally.
- **F42** The login listener on a fixed `login_port` is predictable and
  `SO_REUSEADDR` leaves a local interception window; a local process can abort a
  login with junk connections. PKCE and state keep the code useless to it.
- **F43** The authorize URL (state, code challenge, client id) is printed to
  stderr and passed to the browser launcher; the verifier and the code are not.
- **F46** The store's known-secret set is read outside its lock and grows by two
  entries per refresh for the process lifetime.
- **F49** The TLS hardening is the floor only, and clearing
  `OP_IGNORE_UNEXPECTED_EOF` is a no-op where the constant does not exist
  (Python 3.9).
- **F50** No total deadline on body reads: the timeout is per socket operation,
  so a slow-drip peer keeps a read alive far past `request_timeout`.
- **F51** DNS is neither bounded by the timeout nor pinned, and resolved addresses
  are not classified; TLS verification against the fixed host names is the only
  barrier.
- From the plan: no proxy support, no pinning beyond the system CA, same-uid
  processes can read the token file, no retry beyond the 401 refresh and the
  resumable cases, Testing-status refresh tokens expire after 7 days, Mac and
  Linux only.

**The fleet-wide follow-up is not done.** F1, F2 and F3 were fixed in mcp-gd only.
The other servers built from the same skeleton still let a `RecursionError` from
a deeply nested frame escape `run()`, still read stdin with an unbounded
`readline`, and still shut down on a `UnicodeDecodeError` from stdin. That is a
roadmap follow-up, not a property of the fleet this record claims.
