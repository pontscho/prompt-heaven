#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for the stdlib OAuth 2.0 / OIDC native public client.

The domain is ONE question: how a native public client obtains, keeps and
renews an OAuth 2.0 / OIDC grant -- the PKCE pair (RFC 7636), the authorize
redirect, the authorization-code, refresh and device grants (RFC 6749, RFC
8628), what a token-endpoint answer and an ID token mean, when a grant is dead
and has to be logged into again, and the loopback redirect the client listens
on (RFC 8252). It is a source of its own and not a corner of an existing one
because none of them can hold it without becoming a shelf: `_mcp_chrome.py`
answers "look like Chrome on the wire", `_mcp_websocket.py` answers a frame
protocol, and neither says what a grant is. The rule that decided it is ADR
0014's: the protocol is the domain, a vendor is not -- so the provider ROWS
(endpoints, client ids, scopes) live in the host, and this file only defines
the row type they are written in.

**One host.** `Scripts/llm-router.py` takes this source for its `codex` and
`openai` backends: it refreshes their tokens while it serves, and its `login`
subcommand runs the browser and the device flows.

**Sans-IO core, one thin wrapper of two blocks.** Everything that decides
anything is a pure function over its arguments: `_oauth_pkce_pair` /
`_oauth_new_state` / `_oauth_uuid4_urn` mint the one-time values,
`_oauth_authorize_url` and the four request builders return what to send,
`_oauth_parse_token_response` / `_oauth_parse_device_start` /
`_oauth_parse_device_poll` judge what came back, the JWT and claim helpers read
an ID token, and `_oauth_parse_callback` / `_oauth_callback_verdict` /
`_oauth_callback_page` decide a redirect and its answer. The wrapper only
moves bytes between that core and
the loopback: `_oauth_listen` opens the sockets and
`_oauth_sync_accept_callback` waits on them. A host takes the core plus the
wrapper, all on ONE marker, because `host_provides` offers a region only the
host's imports and never a name another region defines -- so every block that
calls another block must be co-listed with it, dependency first.

**Injected, not owned: the clock and the transport.** No function here reads a
clock -- `now` is always an argument (int seconds since the epoch) and the
acceptor's `clock` is a callable the host passes -- so a test pins time
instead of sleeping through it, and the host keeps the one clock its own
deadlines already use. No function here sends an HTTP request either: a
builder returns `(url, headers, body)` and the host POSTs it through its own
transport, so the address policy, the trust store, the timeouts and the
framing that already guard the host's upstream traffic guard the token
endpoint too, and a second HTTP stack with its own idea of those never exists.

**What the core refuses**, each as `OAuthError` with `.kind` from
`OAUTH_ERROR_KINDS`:

* `relogin` -- the grant is dead (a code from `OAUTH_RELOGIN_CODES`); only a
  new login revives it, so a host must not retry it;
* `transient` -- the token endpoint answered 5xx, or reported
  `server_error` / `temporarily_unavailable`; a later retry may succeed;
* `rate_limited` -- the token endpoint answered 429;
* `invalid_response` -- an answer this module will not act on: a body over
  `OAUTH_BODY_LIMIT`, not strict JSON, not an object, a token that is not
  printable ASCII of at most `OAUTH_TOKEN_LIMIT` characters, a missing field,
  an unexpected status, an ID token whose claims do not hold;
* `invalid_callback` -- a redirect on the wrong path, with a wrong or missing
  state, a duplicated parameter or a malformed issued client id;
* `denied` -- the authorization server reported `error=` on a redirect whose
  state matched;
* `callback_timeout` / `callback_abuse` -- the wait ran past its deadline, or
  spent its bad-request budget.

`.code` is the allow-listed `error` value (`OAUTH_ERROR_CODES`, anything else
is `"other"`) or a fixed claim name; `error_description`, `message` and every
other field of an error body are never read, so no text a server or a local
process chose can reach a log line, an exception message or a client envelope
through this module.

**Out of scope, deliberately:** ID-token signature verification (OIDC Core
3.1.3.7 item 6 permits skipping it when the token arrives straight from the
token endpoint over TLS, which is the only way this module receives one),
discovery documents, revocation, client secrets (a native client is public),
the implicit and hybrid flows, and every vendor row.

**Tab safety is a constraint on how this file is WRITTEN.** A host may indent
with tabs, and `Scripts/amalgamate.py:block_is_tab_safe` refuses a block that
joins a line inside an open bracket or indents by anything but whole 4-space
levels. So every call and literal here fits one physical line, and a multi-line
table would be a builder function plus one single-target assignment.

**Block contract.** A block reads only builtins, the stdlib names this module
imports (`base64`, `hashlib`, `hmac`, `json`, `secrets`, `select`, `socket`,
`urllib.parse`), its own arguments and the blocks co-listed on the same marker.
No annotation names a `typing` symbol -- there are no annotations at all -- so no
host has to import one for a block's sake.

**No server imports this module**, for the reasons `_mcp_json.py` gives. The test
fleet does: `tests/test_mcp_oauth.py` loads it and exercises the core and the
listener against real loopback sockets with a fake clock.
"""

import base64
import hashlib
import hmac
import json
import secrets
import select
import socket
import urllib.parse


# One token -- access, refresh or ID -- in characters. The JWTs real providers
# issue are 1-3 KB; 16 KiB is five times the largest seen and still small
# enough that a value which reaches a header or the config file cannot bloat
# either. It is also the cap of the router's `token` header rule, so a token
# this module accepts is one the router can send.
OAUTH_TOKEN_LIMIT = 16384


# One token-endpoint (or device-endpoint) answer, in bytes. A real answer is a
# handful of tokens plus a few numbers -- well under 64 KiB even with three
# 16 KiB tokens. 1 MiB is an order of magnitude of headroom and still a ceiling
# on what a broken or hostile endpoint can make the host parse.
OAUTH_BODY_LIMIT = 1024 * 1024


# One decoded JWT payload, in bytes. A claim set is a few hundred bytes; a JWT
# that passed OAUTH_TOKEN_LIMIT cannot decode to more than ~12 KiB, so this cap
# only binds a caller that hands in a token from elsewhere -- 64 KiB keeps that
# path bounded too.
OAUTH_JWT_LIMIT = 65536


# One JSON integer literal, in characters (sign included). No field this module
# reads needs more than a dozen digits; 4300 is CPython 3.11's own
# int_max_str_digits default, the bound 3.9.6 lacks -- int() is quadratic in the
# literal's length, so a 1 MiB answer of digits would otherwise pin a core (F9).
OAUTH_INT_LITERAL_LIMIT = 4300


# Seconds before `expires_at` at which an access token counts as due. Five
# minutes covers clock skew between host and provider plus the longest request
# the token is about to carry, so a token is never sent in its last seconds.
OAUTH_REFRESH_SKEW_S = 300


# Seconds after a token answer before a clock-driven refresh may run again. An
# `expires_in` of 0 or 1 (or anything under the skew) makes the token due at
# once, so without a floor every request would cost one refresh POST (F2). One
# minute bounds that to one POST a minute; a forced refresh -- the token an
# upstream 401 answered -- is not deferred by it.
OAUTH_MIN_REFRESH_INTERVAL_S = 60


# The callback's request head, in bytes. A browser's GET with the redirect
# query (code, state, a few ids) and its usual headers is 1-3 KB; 8 KiB is the
# common server default and refuses a client that streams header bytes forever.
OAUTH_CALLBACK_HEAD_LIMIT = 8192


# Bad requests the callback listener answers before it gives up. A browser
# sends one redirect plus perhaps a favicon fetch and a retry; 16 leaves room
# for those and stops a local process from keeping the wait alive by spraying.
OAUTH_CALLBACK_BAD_LIMIT = 16


# Seconds one callback connection may take to deliver its head. A browser on
# loopback delivers it in milliseconds; 5 s is generous and stops a connection
# that never finishes its head from holding the single-threaded acceptor.
OAUTH_CALLBACK_CONN_TIMEOUT_S = 5.0


# Every `OAuthError.kind` this module raises. The module docstring argues each.
OAUTH_ERROR_KINDS = ("relogin", "transient", "rate_limited", "invalid_response", "invalid_callback", "denied", "callback_timeout", "callback_abuse")


# The `error` codes that mean the refresh token is dead: RFC 6749 5.2's
# `invalid_grant` plus the five spellings providers use for an expired,
# revoked, rotated-away or replayed refresh token.
OAUTH_RELOGIN_CODES = frozenset({"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"})


# S8: the only `error` values ever surfaced -- the relogin codes, RFC 6749 5.2
# and 4.1.2.1, and RFC 8628 3.5. Anything else surfaces as "other".
OAUTH_ERROR_CODES = frozenset(OAUTH_RELOGIN_CODES | {"invalid_request", "invalid_client", "unauthorized_client", "unsupported_grant_type", "invalid_scope", "access_denied", "server_error", "temporarily_unavailable", "authorization_pending", "slow_down", "expired_token"})


# The two allow-listed codes that say "try again later" rather than "this
# request is wrong" -- they classify as `transient` whatever status carried them.
OAUTH_TRANSIENT_CODES = frozenset({"server_error", "temporarily_unavailable"})


# The characters an `OAuthError.code` may hold. Every allow-listed code and
# every claim name fits; a token, a URL or a free-text description cannot.
OAUTH_CODE_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz_")


# The unpadded base64url alphabet (RFC 4648 5) -- what a PKCE verifier, a state
# and a JWT segment are written in.
OAUTH_B64URL_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


# The characters of an identifier this module surfaces -- an issued client id,
# an account id, a residency -- the [A-Za-z0-9_-] class; at most 128 of them
# (_oauth_id_ok). Such a value reaches a header or the config file, so nothing
# that could split either (space, CR/LF, a quote) is let through.
OAUTH_ID_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class OAuthError(Exception):
    """One refusal: `.kind` from OAUTH_ERROR_KINDS, `.code` a short code or "".

    The message is built from the kind and the code ONLY (S8). A code outside
    OAUTH_CODE_ALPHABET or over 64 characters is replaced by "other" rather than
    carried, so a caller that passes server text by mistake still cannot put a
    token or a description into `str()`, `repr()` or `.code`. An unknown kind
    is a programming error and raises ValueError at the raise site.
    """

    def __init__(self, kind, code=""):
        if kind not in OAUTH_ERROR_KINDS:
            raise ValueError("OAuthError: unknown kind")
        if not isinstance(code, str) or len(code) > 64 or not set(code) <= OAUTH_CODE_ALPHABET:
            code = "other"
        Exception.__init__(self, "oauth %s (%s)" % (kind, code) if code else "oauth %s" % kind)
        self.kind = kind
        self.code = code


def _oauth_pairs_ok(value):
    """True when *value* is a tuple of (str, str) tuples -- an extra-parameter list."""
    if not isinstance(value, tuple):
        return False
    return all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str) for item in value)


class OAuthProvider:
    """One provider row: endpoints, the public client id, scopes and flags.

    The row is data the HOST writes (ADR 0014: a vendor is not the domain);
    this class is only its shape. Every field is a keyword argument with no
    default, so a row names all sixteen and a missing or misspelled one is a
    TypeError at import. Each value is type-checked -- `use_nonce` a bool,
    `authorize_extra` a tuple of (str, str) tuples, every other field a str --
    and a wrong type raises ValueError naming the field. An empty string means
    "none": `redirect_uri` "" lets the host pick 127.0.0.1:<port>, `resource`
    "" sends none, `issuer` "" skips the iss check, `required_scope` "" needs
    none, `registration_client_id` "" means the client id is static, and an
    empty `device_usercode_url` means the provider has no device flow.

    The repr is the name only: a row holds no secret, but it is printed into
    logs and nobody needs the URLs there.
    """

    __slots__ = ("name", "authorize_url", "token_url", "client_id", "scopes", "redirect_uri", "authorize_extra", "resource", "issuer", "required_scope", "use_nonce", "registration_client_id", "device_usercode_url", "device_token_url", "device_verify_url", "device_redirect_uri")

    def __init__(self, *, name, authorize_url, token_url, client_id, scopes, redirect_uri, authorize_extra, resource, issuer, required_scope, use_nonce, registration_client_id, device_usercode_url, device_token_url, device_verify_url, device_redirect_uri):
        values = (name, authorize_url, token_url, client_id, scopes, redirect_uri, authorize_extra, resource, issuer, required_scope, use_nonce, registration_client_id, device_usercode_url, device_token_url, device_verify_url, device_redirect_uri)
        for field, value in zip(self.__slots__, values):
            if field == "use_nonce":
                ok = isinstance(value, bool)
            elif field == "authorize_extra":
                ok = _oauth_pairs_ok(value)
            else:
                ok = isinstance(value, str)
            if not ok:
                raise ValueError(field)
            object.__setattr__(self, field, value)

    def __repr__(self):
        return "OAuthProvider(%r)" % (self.name,)


def _oauth_b64url(raw):
    """*raw* bytes as unpadded base64url text (RFC 7636 Appendix A)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _oauth_b64url_decode(text):
    """Unpadded base64url *text* back to bytes; ValueError on anything else.

    The alphabet is checked first because the stdlib decoder silently drops
    characters outside it, and a length of 4n+1 can encode no byte string.
    """
    if not isinstance(text, str) or len(text) % 4 == 1 or not set(text) <= OAUTH_B64URL_ALPHABET:
        raise ValueError("not unpadded base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _oauth_pkce_pair(entropy=None):
    """(verifier, S256 challenge) per RFC 7636 4.1-4.2.

    32 random bytes give a 43-character verifier of the unreserved charset;
    *entropy* replaces them for a test vector, and must yield the 43-128
    characters 4.1 allows.
    """
    raw = secrets.token_bytes(32) if entropy is None else entropy
    verifier = _oauth_b64url(raw)
    if not 43 <= len(verifier) <= 128:
        raise ValueError("entropy")
    return verifier, _oauth_b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _oauth_new_state(entropy=None):
    """A fresh single-use `state`: 32 random bytes as 43 base64url characters."""
    return _oauth_b64url(secrets.token_bytes(32) if entropy is None else entropy)


def _oauth_uuid4_urn(entropy=None):
    """A `urn:uuid:<v4>` from 16 random bytes, with the version and variant bits set.

    Written out rather than imported from `uuid` so the block contract stays
    within the imports above.
    """
    raw = bytearray(secrets.token_bytes(16) if entropy is None else entropy)
    if len(raw) != 16:
        raise ValueError("entropy")
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    text = bytes(raw).hex()
    return "urn:uuid:%s-%s-%s-%s-%s" % (text[:8], text[8:12], text[12:16], text[16:20], text[20:])


def _oauth_token_ok(value):
    """True when *value* is a str of 1..OAUTH_TOKEN_LIMIT printable ASCII, no space.

    This is what lets a token reach a header (no CR/LF, no space, no control
    character) and the config file (no lone surrogate) without further checks.
    """
    if not isinstance(value, str) or not 0 < len(value) <= OAUTH_TOKEN_LIMIT:
        return False
    return all("!" <= ch <= "~" for ch in value)


def _oauth_state_matches(expected, got):
    """True when the callback's *got* state equals *expected*, compared in constant time.

    The comparison runs on ASCII bytes through hmac.compare_digest, so a local
    process probing the listener learns nothing from the time a guess takes.
    A non-str, an empty expected value or a non-ASCII value never matches.
    """
    if not isinstance(expected, str) or not isinstance(got, str) or not expected:
        return False
    try:
        want = expected.encode("ascii")
        have = got.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(want, have)


def _oauth_authorize_url(provider, client_id, redirect_uri, challenge, state, nonce, extra):
    """The URL the user opens to log in (RFC 6749 4.1.1 with RFC 7636 S256).

    Key order: response_type, client_id, redirect_uri, scope, code_challenge,
    code_challenge_method, state; then `nonce` (only when the row sets
    use_nonce) and `resource` (only when the row has one); then the row's
    authorize_extra in row order; then *extra*, the caller's own pairs. A
    *redirect_uri* of None or "" falls back to the row's; an empty result is a
    ValueError. A key that would appear twice is a ValueError rather than a
    choice of which one wins. Every value is percent-encoded once, a space as
    %20 (quote, not quote_plus).
    """
    redirect = redirect_uri or provider.redirect_uri
    if not redirect:
        raise ValueError("redirect_uri")
    if not client_id:
        raise ValueError("client_id")
    if not _oauth_pairs_ok(extra):
        raise ValueError("extra")
    pairs = [("response_type", "code"), ("client_id", client_id), ("redirect_uri", redirect), ("scope", provider.scopes), ("code_challenge", challenge), ("code_challenge_method", "S256"), ("state", state)]
    if provider.use_nonce:
        if not isinstance(nonce, str) or not nonce:
            raise ValueError("nonce")
        pairs.append(("nonce", nonce))
    if provider.resource:
        pairs.append(("resource", provider.resource))
    pairs.extend(provider.authorize_extra)
    pairs.extend(extra)
    keys = [key for key, _value in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("authorize_extra")
    return provider.authorize_url + "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)


def _oauth_form_headers():
    """The headers of a form-encoded token-endpoint POST (RFC 6749 4.1.3, 6)."""
    return (("Content-Type", "application/x-www-form-urlencoded"), ("Accept", "application/json"))


def _oauth_json_headers():
    """The headers of a JSON device-endpoint POST."""
    return (("Content-Type", "application/json"), ("Accept", "application/json"))


def _oauth_exchange_request(provider, client_id, code, verifier, redirect_uri):
    """(url, headers, body) of the authorization-code exchange (RFC 6749 4.1.3, RFC 7636 4.5).

    *redirect_uri* is the one the authorize URL carried -- the server compares
    them -- and *client_id* the one it was issued to.
    """
    if not client_id:
        raise ValueError("client_id")
    if not redirect_uri:
        raise ValueError("redirect_uri")
    fields = [("grant_type", "authorization_code"), ("code", code), ("redirect_uri", redirect_uri), ("client_id", client_id), ("code_verifier", verifier)]
    return provider.token_url, _oauth_form_headers(), urllib.parse.urlencode(fields).encode("ascii")


def _oauth_refresh_request(provider, client_id, refresh_token):
    """(url, headers, body) of a refresh-token grant (RFC 6749 6).

    It never sends `scope`: omitted, the new token carries the scope the grant
    already has, and a narrower or wider one is not this module's call. It
    sends `resource` when the row has one (RFC 8707), and *client_id* is the
    id the grant was issued to -- for a dynamically registered client the
    issued one, never the row's `registration_client_id`.
    """
    if not client_id:
        raise ValueError("client_id")
    fields = [("grant_type", "refresh_token"), ("refresh_token", refresh_token), ("client_id", client_id)]
    if provider.resource:
        fields.append(("resource", provider.resource))
    return provider.token_url, _oauth_form_headers(), urllib.parse.urlencode(fields).encode("ascii")


def _oauth_device_start_request(provider, client_id):
    """(url, headers, body) asking the device endpoint for a user code."""
    if not provider.device_usercode_url:
        raise ValueError("device_usercode_url")
    return provider.device_usercode_url, _oauth_json_headers(), json.dumps({"client_id": client_id}).encode("utf-8")


def _oauth_device_poll_request(provider, device_auth_id, user_code):
    """(url, headers, body) polling the device endpoint for the user's approval."""
    if not provider.device_token_url:
        raise ValueError("device_token_url")
    return provider.device_token_url, _oauth_json_headers(), json.dumps({"device_auth_id": device_auth_id, "user_code": user_code}).encode("utf-8")


def _oauth_error_code(obj):
    """The allow-listed error code an error body carries, else "other" (S8).

    The candidates, in order: `error` as a string, `error.code`, `error.type`,
    `detail.code`. The first one in OAUTH_ERROR_CODES is returned; with none,
    "other". `error_description`, `message` and every other field are never
    read, so no server-chosen text leaves this function.
    """
    if not isinstance(obj, dict):
        return "other"
    candidates = []
    error = obj.get("error")
    if isinstance(error, dict):
        candidates.append(error.get("code"))
        candidates.append(error.get("type"))
    else:
        candidates.append(error)
    detail = obj.get("detail")
    if isinstance(detail, dict):
        candidates.append(detail.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in OAUTH_ERROR_CODES:
            return candidate
    return "other"


def _oauth_no_duplicate_keys(pairs):
    """object_pairs_hook: a JSON object, refusing a key that appears twice.

    Two values for one key are an ambiguity no parser should resolve by
    picking one (ADR 0015).
    """
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate key")
    return obj


def _oauth_no_constant(name):
    """parse_constant: refuse NaN and the infinities, which strict JSON does not have."""
    raise ValueError("non-JSON constant")


def _oauth_bounded_int(text):
    """parse_int: an integer literal of at most OAUTH_INT_LITERAL_LIMIT characters.

    Python 3.9.6 has no int_max_str_digits, and int() over a long literal is
    quadratic in its length; a longer literal is refused before int() sees it (F9).
    """
    if len(text) > OAUTH_INT_LITERAL_LIMIT:
        raise ValueError("integer literal too long")
    return int(text)


def _oauth_json_object(body):
    """*body* (bytes or str) parsed as one strict JSON object, else invalid_response.

    The size cap comes before the parse; a duplicate key, NaN/Infinity, an
    integer literal over OAUTH_INT_LITERAL_LIMIT characters, a nesting too deep
    for the parser and a non-object top level are refused.
    """
    if not isinstance(body, (bytes, bytearray, str)) or len(body) > OAUTH_BODY_LIMIT:
        raise OAuthError("invalid_response")
    try:
        obj = json.loads(body, object_pairs_hook=_oauth_no_duplicate_keys, parse_constant=_oauth_no_constant, parse_int=_oauth_bounded_int)
    except (ValueError, RecursionError):
        raise OAuthError("invalid_response") from None
    if not isinstance(obj, dict):
        raise OAuthError("invalid_response")
    return obj


def _oauth_status_refusal(status):
    """Raise for a status that decides the outcome on its own: 429 or 5xx.

    Called BEFORE the body is parsed: a 503 from a proxy carries an HTML page,
    and that is still a transient failure, not an invalid response.
    """
    if status == 429:
        raise OAuthError("rate_limited")
    if 500 <= status <= 599:
        raise OAuthError("transient")


def _oauth_error_refusal(status, body):
    """Raise the OAuthError a non-200 answer that is not 429/5xx stands for.

    A relogin code is `relogin`, server_error / temporarily_unavailable is
    `transient`, anything else -- including a body that is not a JSON object --
    is `invalid_response`; the code is the allow-listed one or "other".
    """
    try:
        obj = _oauth_json_object(body)
    except OAuthError:
        raise OAuthError("invalid_response", "other") from None
    code = _oauth_error_code(obj)
    if code in OAUTH_RELOGIN_CODES:
        raise OAuthError("relogin", code)
    if code in OAUTH_TRANSIENT_CODES:
        raise OAuthError("transient", code)
    raise OAuthError("invalid_response", code)


def _oauth_parse_token_response(status, body, now, previous_refresh, require_refresh=False):
    """Judge one token-endpoint answer (RFC 6749 5.1/5.2).

    The status is classified first (429, 5xx), then a non-200 is mapped by its
    error code, then a 200 body is parsed under OAUTH_BODY_LIMIT. Returns a dict
    with `access_token`, `refresh_token`, `expires_at`, `id_token`, `scope` and
    `earliest_refresh_at`. Every token present must pass _oauth_token_ok. A
    missing refresh_token keeps *previous_refresh* (6: the server may keep the
    old one), unless *require_refresh*, as for a code exchange, where it is a
    refusal. `expires_at` is now + expires_in, or None when the answer has no
    expires_in (it is OPTIONAL); `id_token` and `scope` are None when absent.
    `earliest_refresh_at` is *now* + OAUTH_MIN_REFRESH_INTERVAL_S: no provider
    this module serves sends a refresh-not-before hint, so the floor is the
    issue time plus one interval -- a tiny `expires_in` cannot make every
    request a refresh POST (F2).
    """
    _oauth_status_refusal(status)
    if status != 200:
        _oauth_error_refusal(status, body)
    obj = _oauth_json_object(body)
    access = obj.get("access_token")
    if not _oauth_token_ok(access):
        raise OAuthError("invalid_response")
    refresh = obj.get("refresh_token")
    if refresh is None:
        if require_refresh:
            raise OAuthError("invalid_response")
        refresh = previous_refresh
    elif not _oauth_token_ok(refresh):
        raise OAuthError("invalid_response")
    id_token = obj.get("id_token")
    if id_token is not None and not _oauth_token_ok(id_token):
        raise OAuthError("invalid_response")
    scope = obj.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise OAuthError("invalid_response")
    expires_in = obj.get("expires_in")
    if expires_in is None:
        expires_at = None
    elif isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in < 0:
        raise OAuthError("invalid_response")
    else:
        expires_at = now + expires_in
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires_at, "id_token": id_token, "scope": scope, "earliest_refresh_at": now + OAUTH_MIN_REFRESH_INTERVAL_S}


def _oauth_device_interval(value):
    """The poll interval in seconds, clamped to [1, 60]; 5 when absent (RFC 8628 3.2).

    An int, or a string of up to six digits (some device endpoints send the
    number quoted); anything else is invalid_response.
    """
    if value is None:
        return 5
    if isinstance(value, str) and 0 < len(value) <= 6 and value.isdigit() and value.isascii():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise OAuthError("invalid_response")
    return max(1, min(60, value))


def _oauth_parse_device_start(status, body):
    """Judge the device endpoint's user-code answer.

    Returns a dict with `device_auth_id`, `user_code` and `interval`; both ids
    must pass _oauth_token_ok, since one is shown to the user and the other is
    sent back on every poll. The user_code is printed for the user to type, so
    one over 64 characters -- the bound OAuthError puts on a code; a real one is
    about 9 -- is refused too (F51).
    """
    _oauth_status_refusal(status)
    if status != 200:
        _oauth_error_refusal(status, body)
    obj = _oauth_json_object(body)
    device_auth_id = obj.get("device_auth_id")
    user_code = obj.get("user_code")
    if not _oauth_token_ok(device_auth_id) or not _oauth_token_ok(user_code) or len(user_code) > 64:
        raise OAuthError("invalid_response")
    return {"device_auth_id": device_auth_id, "user_code": user_code, "interval": _oauth_device_interval(obj.get("interval"))}


def _oauth_parse_device_poll(status, body):
    """Judge one device poll: None while pending (403 or 404), else the grant.

    A granted poll returns a dict with `authorization_code` and
    `code_verifier`, which the host then trades through
    _oauth_exchange_request; nothing else the answer carries is surfaced.
    """
    if status in (403, 404):
        return None
    _oauth_status_refusal(status)
    if status != 200:
        _oauth_error_refusal(status, body)
    obj = _oauth_json_object(body)
    code = obj.get("authorization_code")
    verifier = obj.get("code_verifier")
    if not _oauth_token_ok(code) or not _oauth_token_ok(verifier):
        raise OAuthError("invalid_response")
    return {"authorization_code": code, "code_verifier": verifier}


def _oauth_id_ok(value):
    """True when *value* is a str of 1..128 characters from OAUTH_ID_ALPHABET."""
    return isinstance(value, str) and 0 < len(value) <= 128 and set(value) <= OAUTH_ID_ALPHABET


def _oauth_jwt_claims(token):
    """The claim set of a JWT, as a dict -- with NO signature check.

    The token must be three dot-separated segments; the payload segment must be
    unpadded base64url decoding to at most OAUTH_JWT_LIMIT bytes of one strict
    JSON object (no duplicate key, no NaN). Anything else raises OAuthError
    invalid_response. Skipping the signature is OIDC Core 3.1.3.7 item 6: the
    only tokens this module reads arrived straight from the token endpoint over
    TLS, so the channel, not the signature, vouches for them.
    """
    if not isinstance(token, str):
        raise OAuthError("invalid_response")
    parts = token.split(".")
    if len(parts) != 3 or len(parts[1]) > (OAUTH_JWT_LIMIT * 4 + 2) // 3:
        raise OAuthError("invalid_response")
    try:
        raw = _oauth_b64url_decode(parts[1])
    except ValueError:
        raise OAuthError("invalid_response") from None
    if len(raw) > OAUTH_JWT_LIMIT:
        raise OAuthError("invalid_response")
    return _oauth_json_object(raw)


def _oauth_check_id_token(claims, provider, client_id, nonce, now):
    """Raise OAuthError("invalid_response", code=<claim>) unless the ID token's claims hold.

    In order: `iss` equals the row's issuer (skipped when the row's issuer is
    ""), `aud` is *client_id* or a list holding it, `exp` is an int later than
    *now*, and `nonce` equals *nonce* when one was sent. The code names the
    first claim that failed and nothing else.
    """
    if not isinstance(claims, dict):
        raise OAuthError("invalid_response")
    if provider.issuer and claims.get("iss") != provider.issuer:
        raise OAuthError("invalid_response", "iss")
    aud = claims.get("aud")
    if isinstance(aud, list):
        aud_ok = bool(client_id) and client_id in aud
    else:
        aud_ok = bool(client_id) and aud == client_id
    if not aud_ok:
        raise OAuthError("invalid_response", "aud")
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int) or exp <= now:
        raise OAuthError("invalid_response", "exp")
    # A plain comparison on purpose: the nonce comes back inside the ID token of
    # the TLS-protected token response, so no peer can time it, and the module's
    # one compare_digest stays with the callback state in _oauth_state_matches.
    if nonce and claims.get("nonce") != nonce:
        raise OAuthError("invalid_response", "nonce")


def _oauth_scope_has(scope, needed):
    """True when the space-separated *scope* holds *needed* as a whole word.

    A prefix or a substring is not a match ("a.bX" does not hold "a.b"). An
    empty *needed* is "no scope required" and always holds; a *scope* that is
    not a str holds nothing.
    """
    if not needed:
        return True
    if not isinstance(scope, str) or not isinstance(needed, str):
        return False
    return needed in scope.split()


def _oauth_account_claims(claims):
    """(account_id, residency) from the "https://api.openai.com/auth" claim namespace.

    `account_id` is `chatgpt_account_id`; `residency` is
    `chatgpt_data_residency`, or `chatgpt_compute_residency` when that is
    absent. A value that fails _oauth_id_ok -- not a str, empty, over 128
    characters, or holding anything outside [A-Za-z0-9_-] -- is None, and so is
    every value of a namespace that is missing or not an object.
    """
    space = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else None
    if not isinstance(space, dict):
        return None, None
    account = space.get("chatgpt_account_id")
    residency = space.get("chatgpt_data_residency")
    if residency is None:
        residency = space.get("chatgpt_compute_residency")
    return (account if _oauth_id_ok(account) else None), (residency if _oauth_id_ok(residency) else None)


def _oauth_token_due(expires_at, now, skew):
    """True when an access token expiring at *expires_at* must be refreshed at *now*.

    Due from *skew* seconds before expiry on. An *expires_at* of None -- the
    token answer had no `expires_in`, which RFC 6749 5.1 makes optional -- is
    never due by the clock: the host learns of its end from a 401 instead.
    """
    if expires_at is None:
        return False
    return now >= expires_at - skew


def _oauth_parse_callback(target, path, state):
    """Judge one redirect target; returns {"code", "state", "client_id"} or raises OAuthError.

    The path must equal *path* exactly, no parameter may appear twice (ADR
    0015), and the `state` must match *state* through _oauth_state_matches --
    checked BEFORE `error=` is looked at, so an `error=` with a wrong or missing
    state is `invalid_callback` and a local process cannot abort a login.
    With the state matched, `error=` raises `denied` with the allow-listed code
    or "other" (`error_description` is never read). Otherwise `code` must pass
    _oauth_token_ok and an issued `client_id`, when present, _oauth_id_ok
    (None when absent). Nothing else the redirect carries -- scope, id_token,
    account ids -- is surfaced: identity comes only from the token endpoint.
    """
    if not isinstance(target, str):
        raise OAuthError("invalid_callback")
    try:
        parts = urllib.parse.urlsplit(target)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise OAuthError("invalid_callback") from None
    if parts.path != path:
        raise OAuthError("invalid_callback")
    params = dict(pairs)
    if len(params) != len(pairs):
        raise OAuthError("invalid_callback")
    if not _oauth_state_matches(state, params.get("state")):
        raise OAuthError("invalid_callback")
    error = params.get("error")
    if error is not None:
        raise OAuthError("denied", error if error in OAUTH_ERROR_CODES else "other")
    code = params.get("code")
    if not _oauth_token_ok(code):
        raise OAuthError("invalid_callback")
    client_id = params.get("client_id")
    if client_id is not None and not _oauth_id_ok(client_id):
        raise OAuthError("invalid_callback")
    return {"code": code, "state": params["state"], "client_id": client_id}


def _oauth_callback_page(ok, status):
    """The whole HTTP/1.1 response the callback listener sends, as bytes.

    *status* is one of 200, 400, 404, 405 and 431, anything else a ValueError,
    and a success page (*ok*) is only ever a 200. The body is fixed per *ok* --
    a success or a failure page -- and reflects no input, so nothing a request
    carried can come back in it. Every page says Connection: close (the
    listener closes after it), Cache-Control: no-store (the page follows a URL
    that held a code) and Referrer-Policy: no-referrer (no link out of it
    carries that URL); a 405 adds the Allow header RFC 9110 15.5.6 requires.
    """
    reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 431: "Request Header Fields Too Large"}
    if isinstance(status, bool) or not isinstance(status, int) or status not in reasons or (ok and status != 200):
        raise ValueError("status")
    if ok:
        body = b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>Login complete</title></head><body><p>Login complete. You can close this tab and return to the terminal.</p></body></html>\n"
    else:
        body = b"<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>Login failed</title></head><body><p>Login failed. Return to the terminal for details.</p></body></html>\n"
    allow = "Allow: GET\r\n" if status == 405 else ""
    head = "HTTP/1.1 %d %s\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\n%sCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n" % (status, reasons[status], len(body), allow)
    return head.encode("ascii") + body


def _oauth_callback_verdict(head, path, state, allowed_hosts):
    """Judge one complete callback request head: (status, parsed dict or None).

    Sans-IO: the acceptor reads the bytes, this decides. The checks run in
    order -- the request line (malformed: 400), the method (not GET: 405), the
    Host header (missing, repeated or not exactly one of *allowed_hosts*, port
    included: 400), the path (not *path*: 404) -- and then
    _oauth_parse_callback: success is (200, parsed), an `invalid_callback` is
    (400, None), and a matching-state `denied` propagates for the acceptor to
    answer and end the wait on.
    """
    lines = head.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    request = lines[0].split(" ")
    if len(request) != 3 or not request[2].startswith("HTTP/1."):
        return 400, None
    if request[0] != "GET":
        return 405, None
    hosts = [value.strip() for name, sep, value in (line.partition(":") for line in lines[1:]) if sep and name.strip().lower() == "host"]
    if len(hosts) != 1 or hosts[0] not in allowed_hosts:
        return 400, None
    try:
        target_path = urllib.parse.urlsplit(request[1]).path
    except ValueError:
        return 400, None
    if target_path != path:
        return 404, None
    try:
        parsed = _oauth_parse_callback(request[1], path, state)
    except OAuthError as exc:
        if exc.kind == "denied":
            raise
        return 400, None
    return 200, parsed


def _oauth_listen(hosts, port):
    """Listening TCP sockets for the loopback redirect (RFC 8252 7.3): one per host.

    A host holding ":" is IPv6 (bound v6-only, so it never collides with the
    IPv4 socket). The first host is mandatory -- its OSError, such as a port a
    live listener holds, propagates -- and later hosts are best effort, bound
    to the port the first one got (so *port* 0 picks one port for all).

    SO_REUSEADDR is set on POSIX (detected as: socket has no
    SO_EXCLUSIVEADDRUSE, i.e. not Windows) so a second login right after the
    first binds over the TIME_WAIT the first one's server-side close left on
    the port; on POSIX it still refuses a port with a live listener. On
    Windows the same option would let another socket steal a live port, so it
    is not set there. SO_REUSEPORT is NEVER set: it would let a second process
    bind the live port and share its connections. Neither choice is the
    control against an interceptor that wins the port anyway -- PKCE is: a
    stolen code is useless without the verifier, which never leaves the host.
    """
    socks = []
    for index, host in enumerate(hosts):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            if index == 0:
                raise
            continue
        try:
            if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((host, port))
            sock.listen(8)
        except OSError:
            sock.close()
            if index == 0:
                raise
            continue
        if index == 0:
            port = sock.getsockname()[1]
        socks.append(sock)
    return socks


def _oauth_sync_accept_callback(listeners, path, state, allowed_hosts, deadline, clock, cancel):
    """Wait on *listeners* for the one valid redirect; the parsed dict, or None on cancel.

    A select loop in slices of at most 0.25 s: each slice returns None once
    *cancel* (a threading.Event) is set and raises OAuthError callback_timeout
    once *clock()* reaches *deadline*. Connections are served one at a time,
    each with the per-recv timeout OAUTH_CALLBACK_CONN_TIMEOUT_S (read at call
    time) and the same bound on the whole head by *clock*; a head that never
    completes is dropped and the wait continues. A head over
    OAUTH_CALLBACK_HEAD_LIMIT gets 431, the rest is judged by
    _oauth_callback_verdict (405, 400, 404, 400). Every refusal -- and a
    non-empty head that never completed -- counts against
    OAUTH_CALLBACK_BAD_LIMIT; spending it raises OAuthError callback_abuse.

    A valid callback gets _oauth_callback_page(True, 200); a matching-state
    `error=` gets _oauth_callback_page(False, 200) and the `denied` OAuthError
    is re-raised. Every page is sent whole and the connection closed right
    after it (no half-close, no linger). The acceptor owns *listeners*: on
    every way out -- the result, None, or a raised OAuthError (denied,
    callback_timeout, callback_abuse) -- each one is closed, so a connect
    after the wait is refused.
    """
    bad = 0
    try:
        for listener in listeners:
            listener.setblocking(False)
        while True:
            if cancel.is_set():
                return None
            remaining = deadline - clock()
            if remaining <= 0:
                raise OAuthError("callback_timeout")
            readable, _writable, _failed = select.select(listeners, [], [], min(0.25, remaining))
            for listener in readable:
                try:
                    conn, _peer = listener.accept()
                except OSError:
                    continue
                try:
                    status = None
                    parsed = None
                    head = b""
                    try:
                        conn.settimeout(OAUTH_CALLBACK_CONN_TIMEOUT_S)
                        conn_end = clock() + OAUTH_CALLBACK_CONN_TIMEOUT_S
                        while b"\r\n\r\n" not in head and len(head) <= OAUTH_CALLBACK_HEAD_LIMIT and clock() < conn_end:
                            chunk = conn.recv(OAUTH_CALLBACK_HEAD_LIMIT + 1 - len(head))
                            if not chunk:
                                break
                            head += chunk
                    except OSError:
                        pass
                    end = head.find(b"\r\n\r\n")
                    if (end < 0 and len(head) > OAUTH_CALLBACK_HEAD_LIMIT) or end + 4 > OAUTH_CALLBACK_HEAD_LIMIT:
                        status = 431
                    elif end >= 0:
                        try:
                            status, parsed = _oauth_callback_verdict(head, path, state, allowed_hosts)
                        except OAuthError:
                            try:
                                conn.sendall(_oauth_callback_page(False, 200))
                            except OSError:
                                pass
                            raise
                    if status is not None:
                        try:
                            conn.sendall(_oauth_callback_page(parsed is not None, status))
                        except OSError:
                            pass
                    if parsed is not None:
                        return parsed
                finally:
                    conn.close()
                if status is not None or head:
                    bad += 1
                if bad >= OAUTH_CALLBACK_BAD_LIMIT:
                    raise OAuthError("callback_abuse")
    finally:
        for listener in listeners:
            listener.close()
