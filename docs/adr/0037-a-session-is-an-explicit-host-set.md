---
name: 0037-a-session-is-an-explicit-host-set
type: adr
status: active
title: webfetch POST and a session cookie jar -- host-side only, verified transport only, an explicit host set
description: Decision (R-0114) to give mcp-webfetch a POST method and an opt-in session that keeps one cookie jar across calls, entirely in the host file because the canonical _mcp_chrome.py _ChSession already posts, keeps a jar and rewrites redirects -- an explicit 1-8 exact-host set per session, 4 live, a 600 s idle timeout, the verified transport only with profile=chrome refused, no cache and no retry for a POST or a session call, a credentialed GET kept out of the cache, a str or dict body under 64 KiB; Set-Cookie made visible to the model by a tape on the jar's ingest (user decision) and a cookies param to send them; the cross-origin 307/308 body guard and its coupling to the canonical client's redirect log line, the private jar reads, the canonical IDNA encoder for the host set; the alternatives rejected, the code-mode security review (APPROVE, one LOW fixed) and the limits declared rather than gated.
sources:
  - Scripts/mcp-webfetch.py:_HopGuard
  - Scripts/mcp-webfetch.py:_SetCookieTape
  - Scripts/mcp-webfetch.py:_FetchSession
  - Scripts/mcp-webfetch.py:handle_session_open
  - Scripts/mcp-webfetch.py:handle_fetch
  - Scripts/mcp-webfetch.py:_jar_line
  - Scripts/_mcp_chrome.py:_ChCookieJar
  - tests/test_mcp_chrome.py
verified:
  commit: e168e3b
  date: 2026-11-12
links:
  - scripts
  - tests
  - generated-regions
  - 0013-the-ceiling-is-a-payload-class
  - 0014-a-canonical-source-is-a-domain
  - 0025-generate-do-not-import
  - 0026-speak-chrome-from-the-stdlib-verify-by-default
  - 0032-a-runner-behind-a-proxy
---

# ADR 0037: webfetch POST and a session cookie jar -- host-side only, verified transport only, an explicit host set

Status: accepted 2026-11-12, implemented in `e168e3b` (roadmap R-0114). Every
number below is a constant read from the code or a count at the moment of the
decision; the live values belong to `Scripts/mcp-webfetch.py` and the living
page [[scripts]].

## Context

`webfetch_call fetch` was GET/HEAD only, and every call built a fresh
`_ChSession` that died with the call, so nothing carried a cookie from one call
to the next. An OAuth/OIDC login (an Entra ID / oauth2-proxy flow was the report)
is a credential POST followed by Set-Cookie lines carried across redirects, and
it could not be driven at all. The roadmap item recorded the opposing pull too: a
POST body and a cookie jar widen the exfiltration and CSRF surface of a
model-driven fetcher, and the Chrome path does not verify certificates (R-0047).

The canonical client already had every primitive the feature needs:
`Scripts/_mcp_chrome.py` `_ChSession.post` sends the captured cors POST, the
session owns a `Scripts/_mcp_chrome.py:_ChCookieJar`, and the redirect loop
rewrites a 301/302 after a POST and a 303 into a bodiless GET while re-sending the
body on a 307/308, as Chrome does ([[0026-speak-chrome-from-the-stdlib-verify-by-default]],
Redirect hygiene). What it lacks is policy a model-driven caller needs and Chrome
does not have.

## Decision

The whole feature lives in the host, `Scripts/mcp-webfetch.py`. No canonical
source changed and `Scripts/amalgamate.py` was not touched: the generated region
is byte-identical, so the other hosts of `_mcp_chrome.py` (the search scripts,
`Scripts/fetch.py`) are unaffected ([[generated-regions]], [[0025-generate-do-not-import]]).

`fetch` gains `method=POST` with `body`, `cookies` and `session`; two new
functions, `session_open` and `session_close`, manage sessions
`Scripts/mcp-webfetch.py:handle_session_open`, `Scripts/mcp-webfetch.py:handle_session_close`.

1. **An explicit host set per session, 1 to 8 exact names.** `session_open`
   takes `hosts`, a list of at most `SESSION_MAX_HOSTS = 8` exact host names:
   subdomains do not count, no scheme, port or path
   `Scripts/mcp-webfetch.py:_normalise_session_host`. A URL whose host is outside
   the set is refused before it is resolved `Scripts/mcp-webfetch.py:_session_host_refusal`,
   and a redirect hop outside it is refused in the connect policy before anything
   is sent `Scripts/mcp-webfetch.py:_HopGuard`.
2. **At most 4 live, a 600 s idle timeout.** `SESSION_MAX_LIVE = 4`,
   `SESSION_IDLE_MAX_SEC = 600`; `idle_timeout` may lower it (1-600) and every
   use restarts it. Expired sessions no call holds are reaped on every registry
   access `Scripts/mcp-webfetch.py:_sessions_reap_locked`. The id is
   `secrets.token_urlsafe(32)`.
3. **One call at a time per session.** A `_ChSession` is not thread-safe and the
   server runs several workers, so a call takes the session's lock without waiting
   and a second concurrent call is refused as busy rather than holding a worker
   `Scripts/mcp-webfetch.py:_session_acquire`, `Scripts/mcp-webfetch.py:_FetchSession`.
4. **The verified transport only.** A POST and a session call never use the
   Chrome path, whose certificate is not verified: `profile=chrome` is refused
   with either, and a guarded session is built with `allow_downgrade=False`, so
   an https->http redirect is refused `Scripts/mcp-webfetch.py:_create_session`.
   Nothing escalates to Chrome, and the opt-in hint is not printed for a call it
   cannot serve.
5. **No cache and no retry for a POST or a session call.** A replayed POST
   repeats a side effect; a cached or retried session answer would skip or repeat
   the cookie exchange the session exists for. Such a call makes one attempt and
   neither reads nor writes the disk cache `Scripts/mcp-webfetch.py:handle_fetch`.
6. **A credentialed GET bypasses the cache.** A GET carrying the `cookies` param
   or a caller `Cookie`, `Authorization` or `Proxy-Authorization` header
   (`Scripts/mcp-webfetch.py:_CREDENTIAL_HEADERS`) gets an authenticated body: it
   is neither served from, stored in nor revalidated against the cache. It is
   still retried like any GET.
7. **The body is a str or a dict, at most 64 KiB.** A str is sent as UTF-8 as
   given; a dict of str to str is urlencoded; anything else, and a body with
   GET/HEAD, is refused; the cap `FETCH_BODY_MAX_BYTES = 64 * 1024` is on the
   encoded bytes, and no refusal echoes the body `Scripts/mcp-webfetch.py:_encode_body`.
8. **Set-Cookie is visible to the model on the verified transport** (user
   decision). The canonical client stores a Set-Cookie in its jar and drops it
   from the response it returns, and a login's Set-Cookie sits on a redirect hop
   the final response never shows. `Scripts/mcp-webfetch.py:_SetCookieTape`
   wraps the jar's `ingest` on the instance (the generated class is not edited)
   and copies every raw line, every hop, before the jar sees it; the report lists
   them in a fenced `## Set-Cookie` block bounded by line count, line length and a
   third of `max_answer_chars` ([[0013-the-ceiling-is-a-payload-class]])
   `Scripts/mcp-webfetch.py:_set_cookie_display`. A cached answer lists none, the
   Chrome path keeps stripping them `Scripts/mcp-webfetch.py:_attach_tape`, values
   are never logged, and the cache still never stores one. A session report adds
   one line naming the cookies the jar holds, names only
   `Scripts/mcp-webfetch.py:_jar_line`.
9. **`cookies` lets the model send cookies.** A dict of at most 32 RFC 6265
   name/value pairs is appended as crumbs to a caller `Cookie` header (or added as
   one) and then rides the canonical client's caller-header road: after the jar's
   own cookies, dropped on the first cross-origin hop
   `Scripts/mcp-webfetch.py:_cookie_crumbs`, `Scripts/mcp-webfetch.py:_merge_cookie_header`.

## The cross-origin 307/308 body guard, and its coupling

The canonical client re-sends a POST body on a 307/308 to another origin. A
model-driven POST must not hand its body, or a credential in it, to whatever a
redirect names, so `_HopGuard.policy` refuses a hop to a different `(host, port)`
while the body is still being sent; a 301/302/303 clears the flag for the rest
of the chain because the client turns it into a bodiless GET. The policy only
sees `(host, port)`, so the redirect status reaches it through the session's log
callback: `_HopGuard.log` matches the canonical client's structure-only line
`session: redirect hop=N status=S` (emitted at `Scripts/_mcp_chrome.py` line 6437
at decision time) with `_HopGuard._REDIRECT_LINE`. A hop whose status is unknown
is treated like a 307 -- fail closed.

That is a text coupling to a generated region. It is pinned by a test row,
`webfetch-session-guard-receives-the-redirect-status-from-the-client-log` in
`tests/test_mcp_chrome.py`, which drives the real client through a same-origin
307 and a 302 and fails naming the regex if the line changes. Because the
coupling fails closed, a changed log line makes the guard refuse more, not less.

## Two more reads of the canonical client

- **The jar's private list.** `_ChCookieJar` has no listing and no clear API.
  `Scripts/mcp-webfetch.py:_jar_names` reads `jar._cookies` for the name line and
  `Scripts/mcp-webfetch.py:_session_finalize` empties it in place on close. If
  the canonical shape changes, both degrade silently: the name line says none
  held, and close drops the session object without emptying the list first. No
  test pins the shape beyond the rows that exercise them.
- **The canonical IDNA encoder for the host set.** A non-ASCII host is encoded
  with `Scripts/_mcp_chrome.py:_ch_idna_encode`, not the stdlib codec, so U+00DF,
  U+03C2, U+200C and U+200D are refused exactly as the URL side refuses them,
  and a host in the set and the same host in a URL cannot name different hosts.

## Alternatives rejected

- **Change the canonical source.** A `post_redirect_body` switch, a jar listing
  API or a Set-Cookie hook in `_mcp_chrome.py` would remove the log-line coupling
  and the private reads. Rejected: the policy is webfetch's alone (the search
  scripts want Chrome fidelity), every primitive already existed, and a canonical
  change regenerates every host and re-gates the fingerprint for a feature one
  host uses ([[0014-a-canonical-source-is-a-domain]]).
- **Allow `profile=chrome` for sessions.** A login form is exactly what a bot
  wall guards, so the fingerprint would help. Rejected: the Chrome path does not
  verify certificates, and a credential POST or a session cookie is precisely
  what must not cross an unauthenticated connection.
- **Derive the host set automatically** (from the first URL, or by following
  redirects and adding each host). Rejected: an OIDC flow crosses an identity
  provider and an application host, so a one-host set fails, and a learned set
  is whatever a redirect names -- the exfiltration path the set exists to close.
  The caller names the hosts up front.

## Security review

`p:security-review` in code mode, three lanes (ssrf-net, access-control,
injection): 25 raw findings, 12 verified, verdict APPROVE. The audit trail is
`.claude/tmp/security-findings-20261112-1100-ssrf-net.md`,
`.claude/tmp/security-findings-20261112-1100-access-control.md` and
`.claude/tmp/security-findings-20261112-1100-injection.md` (not tracked).

- **LOW, fixed: hostile cookie names rendered as live markdown in the jar
  line.** The jar line is unfenced and a name is the server's text. Since the
  fix a name is echoed only when it matches `[A-Za-z0-9_.-]+`; every other name
  is counted under one `<non-token name>` entry `Scripts/mcp-webfetch.py:_jar_line`.
- **Hardening, fixed:** the cache bypass for a GET with credentials (decision 6);
  the host set encoded with `_ch_idna_encode` instead of the raw codec; the
  `SESSION_BUSY` text now says a cancelled call may still be finishing; the jar
  and the tape cleared in place on close.

## Declared limits

- **The host set is port- and scheme-agnostic**, like the RFC 6265 jar under it:
  every port of a listed host, and its cleartext http, is reachable and shares the
  jar's non-Secure cookies.
- **The session id is a bearer.** Any caller of the same server holding the id
  can use the session; the trust model is the same-uid one of
  [[0032-a-runner-behind-a-proxy]].
- **A cancelled call keeps the session busy** until its worker thread ends; the
  busy refusal says so.
- **The tape records more than the jar keeps**: Set-Cookie lines of cross-origin
  hops and of cookies the jar rejects.
- **Fenced Set-Cookie rows still show raw hostile names**; only the unfenced jar
  line filters them.
- **U+00A0 / U+00AD look-alikes stay visible** in rows and names; `_printable`
  removes controls only.

Not implemented: refusing a `cookies` name that duplicates a jar name; an
absolute session lifetime cap (the idle refresh lets a busy session live
forever); LRU eviction when 4 are live (a fifth `session_open` is refused).

## Tests

The rows live in the `mcp_chrome` suite ([[tests]]), which went from 848 to
883 cases: the body and its cap, the session lifecycle, limits and idle clock
(driven through `_session_clock`), cookie continuity and visible Set-Cookie, the
host set, the 307 body rule and the log-line pin above, concurrency, and no
secret in logs or errors `tests/test_mcp_chrome.py`.
