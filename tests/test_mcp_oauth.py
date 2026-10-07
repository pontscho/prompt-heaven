#!/usr/bin/env python3
"""The stdlib OAuth core -- `Scripts/_mcp_oauth.py`, the twelfth canonical source.

`Scripts/_mcp_oauth.py` is the canonical source for how a native public client
obtains, keeps and renews an OAuth 2.0 / OIDC grant: PKCE, the authorize
redirect, the code, refresh and device grants, what a token-endpoint answer and
an ID token mean, when a grant is dead, and the loopback redirect it listens on.
`Scripts/amalgamate.py` generates it into `Scripts/llm-router.py`. The drift gate
in `tests/test_generated_region.py` proves the copy MATCHES the source; this
suite proves the source is RIGHT.

THE ORACLE IS WRITTEN FROM THE RFCs, NOT FROM THE MODULE. The S256 challenge,
the unpadded base64url, the JWTs and the token-endpoint bodies below are this
file's own, and group A pins RFC 7636 Appendix B's published verifier and
challenge. A suite that built its expected values with the module's helpers
would only prove the module agrees with itself.

NO VENDOR DATA. Every provider row here is a TEST row built with the module's
own `OAuthProvider` against `*.tf.invalid` names; the real rows live in the
host (ADR 0014: the protocol is the domain, a vendor is not).

RED FIRST. This suite was written before `Scripts/_mcp_oauth.py` existed. A
module that does not load is ONE red problem per case, never a crash, and a
symbol a case needs that the module does not define is that case's red problem,
never a traceback (the `gj_missing` rule of `tests/test_llm_router.py`).

WRITES NOTHING. Groups A-G are pure: no socket, no file, no clock (`now` is
always an argument). Group H opens real sockets on 127.0.0.1 ONLY, on
ephemeral ports (H13's "fixed" port is one the kernel just handed out), drives
the acceptor on a daemon thread with a FAKE clock and a `threading.Event`
cancel, and bounds every client read and every join so a red or hung acceptor
cannot hang the run. Group I reads the source text and parses it; it imports
`Scripts/amalgamate.py` only to call `block_is_tab_safe`. Secret-shaped values
are sentinels minted with `secrets.token_hex` so a leak is searchable.

Groups:
  A. PKCE AND STATE:   the RFC 7636 vector, the verifier charset, base64url,
                       state, the uuid4 URN
  B. AUTHORIZE URL:    key order, the extras, nonce and resource, the redirect
                       rule, percent-encoding once
  C. REQUEST BUILDERS: exchange, refresh, device usercode and poll
  D. TOKEN RESPONSE:   happy path, refusals, relogin codes, status classes,
                       body cap, token shape, the error-code allow-list (S8)
  E. JWT AND ID TOKEN: claim decode and its refusals, iss/aud/exp/nonce,
                       scope membership, account claims
  F. CALLBACK PARSE:   path, state, error=, duplicates, issued client_id, the
                       returned keys (I3)
  G. DEVICE FLOW:      usercode parse, pending poll, granted poll, interval clamp
  H. CALLBACK LISTENER: the fixed pages (200/400/404/405/431), the slow head,
                       the bad-request budget, deadline, cancel, no reflected
                       input, closed listeners, SO_REUSEADDR and the
                       back-to-back re-bind, a matching-state denial
  I. CONTRACT:         ast over the source -- no typing annotation, no
                       clock/HTTP/asyncio/environ, tab-safe blocks, block-
                       shaped module statements

Usage:
  python3 tests/test_mcp_oauth.py            # standalone
  python3 tests/run.py mcp_oauth             # through the fleet runner
  python3 tests/test_mcp_oauth.py --brief    # one line per case

The case count lives in the SUITES table in tests/run.py, never here.
Exit code 0 iff every case passes.
"""

import ast
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import typing
import urllib.parse

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_oauth"

SOURCE = H.repo_path("Scripts", "_mcp_oauth.py")

GA = "A. PKCE AND STATE: RFC 7636 vector, verifier, b64url, state, uuid4 URN"
GB = "B. AUTHORIZE URL: key order, extras, nonce/resource, redirect, encoding"
GC = "C. REQUEST BUILDERS: exchange, refresh, device usercode and poll"
GD = "D. TOKEN RESPONSE: refusals, relogin, status classes, caps, S8 codes"
GE = "E. JWT AND ID TOKEN: decode, refusals, iss/aud/exp/nonce, scope, claims"
GF = "F. CALLBACK PARSE: path, state, error=, duplicates, client_id, keys"
GG = "G. DEVICE FLOW: usercode, pending, granted, interval clamp"
GH = "H. CALLBACK LISTENER: real loopback sockets, fake clock"
GI = "I. CONTRACT: ast over the source"

# RFC 7636 Appendix B -- typed from the RFC.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

UNRESERVED_RX = re.compile(r"[A-Za-z0-9._~-]+\Z")       # RFC 7636 4.1
URLSAFE_RX = re.compile(r"[A-Za-z0-9_-]+\Z")
UUID4_URN_RX = re.compile(
    r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")

# The six refresh-token death codes, typed from the plan's surface (D5).
RELOGIN_CODES = ("invalid_grant", "invalid_refresh_token", "token_expired",
                 "refresh_token_expired", "refresh_token_invalidated",
                 "refresh_token_reused")

NOW = 1700000000


def sentinel():
    return "TF_" + secrets.token_hex(12)


# --- the oracle ---------------------------------------------------------------

def oracle_b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def oracle_challenge(verifier):
    return oracle_b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def oracle_jwt(payload, header=None):
    """A hand-built JWT with a junk signature segment; *payload* may be raw bytes."""
    head = json.dumps(header or {"alg": "none", "typ": "JWT"}).encode()
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return "%s.%s.%s" % (oracle_b64url(head), oracle_b64url(body), "AAAA")


def raw_query(url):
    """[(key, raw_value)] of *url*'s query, in order, NOT decoded."""
    query = urllib.parse.urlsplit(url).query
    pairs = []
    for item in query.split("&") if query else ():
        key, _sep, value = item.partition("=")
        pairs.append((key, value))
    return pairs


def header_map(headers):
    pairs = headers.items() if isinstance(headers, dict) else headers
    return {str(k).lower(): v for k, v in pairs}


def body_text(body):
    return body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body


def form_fields(body):
    pairs = urllib.parse.parse_qsl(body_text(body), keep_blank_values=True,
                                   strict_parsing=True)
    return pairs, dict(pairs)


def problem_if(condition, message):
    return [message] if condition else []


def oauth_error(mod, fn):
    """The OAuthError *fn* raised, or None. Any OTHER exception propagates."""
    try:
        fn()
    except mod.OAuthError as exc:
        return exc
    return None


def refused(mod, fn):
    """The OAuthError or ValueError *fn* raised, or None."""
    try:
        fn()
    except (mod.OAuthError, ValueError) as exc:
        return exc
    return None


def expect_kind(mod, fn, kind, what, code=None):
    """Problems unless *fn* raises OAuthError of *kind* (and *code*, when given)."""
    exc = oauth_error(mod, fn)
    if exc is None:
        return ["%s: no OAuthError raised (wanted %s)" % (what, kind)]
    problems = problem_if(getattr(exc, "kind", None) != kind,
                          "%s: kind %r, wanted %r" % (what, getattr(exc, "kind", None), kind))
    if code is not None:
        problems += problem_if(getattr(exc, "code", None) != code,
                               "%s: code %r, wanted %r" % (what, getattr(exc, "code", None), code))
    return problems


def leaks(exc, needles):
    """The needles found in str(), repr() or .code of *exc*."""
    hay = "%s\n%r\n%s" % (exc, exc, getattr(exc, "code", ""))
    return [n for n in needles if n in hay]


def provider(mod, **over):
    """A TEST provider row, codex-shaped unless *over* says otherwise."""
    fields = dict(
        name="tf-codex",
        authorize_url="https://auth.tf.invalid/oauth/authorize",
        token_url="https://auth.tf.invalid/oauth/token",
        client_id="app_tf_codex",
        scopes="openid profile email offline_access",
        redirect_uri="http://localhost:1455/auth/callback",
        authorize_extra=(("id_token_add_organizations", "true"),
                         ("codex_cli_simplified_flow", "true"),
                         ("originator", "tf")),
        resource="",
        issuer="",
        required_scope="",
        use_nonce=False,
        registration_client_id="",
        device_usercode_url="https://auth.tf.invalid/deviceauth/usercode",
        device_token_url="https://auth.tf.invalid/deviceauth/token",
        device_verify_url="https://auth.tf.invalid/device",
        device_redirect_uri="https://auth.tf.invalid/deviceauth/callback",
    )
    fields.update(over)
    return mod.OAuthProvider(**fields)


def openai_provider(mod, **over):
    """A TEST provider row, openai-shaped: issued client_id, nonce, resource."""
    fields = dict(
        name="tf-openai",
        authorize_url="https://auth.tf.invalid/accounts/authorize",
        token_url="https://auth.tf.invalid/accounts/oauth/token",
        client_id="",
        scopes="openid profile email offline_access tf.direct",
        redirect_uri="",
        authorize_extra=(),
        resource="https://api.tf.invalid/v1",
        issuer="https://auth.tf.invalid",
        required_scope="tf.direct",
        use_nonce=True,
        registration_client_id="dynamic_agent_client",
        device_usercode_url="",
        device_token_url="",
        device_verify_url="",
        device_redirect_uri="",
    )
    fields.update(over)
    return provider(mod, **fields)


class Checker:
    """Records one case: a load failure is one red problem, an exception is one too."""

    def __init__(self, suite, mod, load_error):
        self.suite = suite
        self.mod = mod
        self.load_error = load_error

    def case(self, group, cid, fn):
        if self.mod is None:
            self.suite.record(group, cid, ["Scripts/_mcp_oauth.py did not load: %s"
                                           % self.load_error])
            return
        detail = []
        try:
            got = fn(self.mod)
        except Exception as exc:  # a missing symbol or a wrong exception type is red, never a crash
            self.suite.record(group, cid, ["raised %s: %s" % (type(exc).__name__, exc)])
            return
        if isinstance(got, tuple):
            got, detail = got
        self.suite.record(group, cid, got, detail=detail)


# --- A. PKCE and state ----------------------------------------------------------

def group_a(chk):
    def a1(mod):
        entropy = base64.urlsafe_b64decode(RFC_VERIFIER + "=")
        problems = problem_if(len(entropy) != 32 or oracle_challenge(RFC_VERIFIER) != RFC_CHALLENGE,
                              "the test's own oracle disagrees with RFC 7636 Appendix B")
        verifier, challenge = mod._oauth_pkce_pair(entropy=entropy)
        problems += problem_if(verifier != RFC_VERIFIER, "verifier %r, wanted %r" % (verifier, RFC_VERIFIER))
        problems += problem_if(challenge != RFC_CHALLENGE, "challenge %r, wanted %r" % (challenge, RFC_CHALLENGE))
        return problems

    def a2(mod):
        v1, c1 = mod._oauth_pkce_pair()
        v2, _c2 = mod._oauth_pkce_pair()
        problems = problem_if(len(v1) != 43, "default verifier is %d characters, wanted 43" % len(v1))
        problems += problem_if(not UNRESERVED_RX.match(v1), "verifier outside the unreserved charset: %r" % v1)
        problems += problem_if(c1 != oracle_challenge(v1), "challenge is not S256 of the verifier")
        problems += problem_if(v1 == v2, "two calls returned the same verifier")
        return problems

    def a3(mod):
        problems = []
        for size in range(0, 7):
            raw = bytes(range(250, 250 + size)) if size < 6 else b"\xfb\xff\xfe\x00?>"
            text = mod._oauth_b64url(raw)
            problems += problem_if("=" in text, "size %d: padded %r" % (size, text))
            problems += problem_if(text != oracle_b64url(raw), "size %d: %r, oracle %r" % (size, text, oracle_b64url(raw)))
            problems += problem_if(mod._oauth_b64url_decode(text) != raw, "size %d: no round trip" % size)
        problems += problem_if(refused(mod, lambda: mod._oauth_b64url_decode("!!!*")) is None,
                               "junk base64 was not refused with ValueError")
        return problems

    def a4(mod):
        s1, s2 = mod._oauth_new_state(), mod._oauth_new_state()
        problems = problem_if(len(s1) != 43 or not URLSAFE_RX.match(s1), "state %r is not 43 url-safe characters" % s1)
        problems += problem_if(s1 == s2, "two calls returned the same state")
        urns = {mod._oauth_uuid4_urn(), mod._oauth_uuid4_urn()}
        problems += problem_if(len(urns) != 2, "two uuid4 URNs were equal")
        problems += ["not a v4 URN: %r" % u for u in sorted(urns) if not UUID4_URN_RX.match(u)]
        pinned = {b"\x00" * 16: "urn:uuid:00000000-0000-4000-8000-000000000000",
                  b"\xff" * 16: "urn:uuid:ffffffff-ffff-4fff-bfff-ffffffffffff"}
        for raw, want in sorted(pinned.items()):
            got = mod._oauth_uuid4_urn(entropy=raw)
            problems += problem_if(got != want, "entropy %r gave %r, wanted %r" % (raw[:1], got, want))
        return problems

    chk.case(GA, "A1 rfc7636-appendix-b", a1)
    chk.case(GA, "A2 default-verifier", a2)
    chk.case(GA, "A3 b64url-unpadded-roundtrip", a3)
    chk.case(GA, "A4 state-and-uuid4-urn", a4)


# --- B. authorize URL -----------------------------------------------------------

BASE_KEYS = ["response_type", "client_id", "redirect_uri", "scope",
             "code_challenge", "code_challenge_method", "state"]


def group_b(chk):
    def b1(mod):
        row = provider(mod)
        url = mod._oauth_authorize_url(row, row.client_id, None, "chal", "st8", None, ())
        pairs = raw_query(url)
        keys = [k for k, _v in pairs]
        want = BASE_KEYS + [k for k, _v in row.authorize_extra]
        values = {k: urllib.parse.unquote(v) for k, v in pairs}
        problems = problem_if(not url.startswith(row.authorize_url + "?"), "URL does not start with the row's authorize_url")
        problems += problem_if(keys != want, "key order %r, wanted %r" % (keys, want))
        expect = {"response_type": "code", "client_id": "app_tf_codex",
                  "redirect_uri": "http://localhost:1455/auth/callback", "scope": row.scopes,
                  "code_challenge": "chal", "code_challenge_method": "S256", "state": "st8",
                  "originator": "tf"}
        problems += ["%s is %r, wanted %r" % (k, values.get(k), v) for k, v in sorted(expect.items()) if values.get(k) != v]
        return problems, ["keys: %s" % ", ".join(keys)]

    def b2(mod):
        row = openai_provider(mod)
        url = mod._oauth_authorize_url(row, "dynamic_agent_client", "http://127.0.0.1:5555/auth/callback",
                                       "chal", "st8", "nonce_tf",
                                       (("agent_name_hint", "llm-router"), ("ext_agent_host_id", "host_tf")))
        values = {k: urllib.parse.unquote(v) for k, v in raw_query(url)}
        expect = {"nonce": "nonce_tf", "resource": "https://api.tf.invalid/v1",
                  "agent_name_hint": "llm-router", "ext_agent_host_id": "host_tf",
                  "client_id": "dynamic_agent_client",
                  "redirect_uri": "http://127.0.0.1:5555/auth/callback"}
        return ["%s is %r, wanted %r" % (k, values.get(k), v) for k, v in sorted(expect.items()) if values.get(k) != v]

    def b3(mod):
        codex = provider(mod)
        keys = [k for k, _v in raw_query(mod._oauth_authorize_url(codex, codex.client_id, None, "c", "s", "nonce_tf", ()))]
        problems = problem_if("nonce" in keys, "a nonce was sent although use_nonce is False")
        no_res = openai_provider(mod, resource="")
        keys = [k for k, _v in raw_query(mod._oauth_authorize_url(no_res, "cid", "http://127.0.0.1:1/cb", "c", "s", "n", ()))]
        problems += problem_if("resource" in keys, "a resource was sent although the row's resource is empty")
        problems += problem_if("nonce" not in keys, "no nonce although use_nonce is True")
        return problems

    def b4(mod):
        row = openai_provider(mod)
        problems = []
        for arg in (None, ""):
            try:
                mod._oauth_authorize_url(row, "cid", arg, "c", "s", "n", ())
            except ValueError:
                continue
            problems.append("redirect_uri %r with an empty row value did not raise ValueError" % (arg,))
        return problems

    def b5(mod):
        row = provider(mod, scopes="openid profile email")
        weird = "a b/c&d=e%f"
        url = mod._oauth_authorize_url(row, row.client_id, None, "c", "s", None, (("tf_extra", weird),))
        problems = []
        wanted = dict(row.authorize_extra)
        wanted.update({"scope": "openid profile email", "tf_extra": weird,
                       "redirect_uri": "http://localhost:1455/auth/callback"})
        for key, raw in raw_query(url):
            problems += problem_if("+" in raw, "%s holds a '+': %r (quote_plus, not quote)" % (key, raw))
            if key in wanted:
                problems += problem_if(urllib.parse.unquote(raw) != wanted[key],
                                       "%s decodes to %r, wanted %r" % (key, urllib.parse.unquote(raw), wanted[key]))
        values = dict(raw_query(url))
        problems += problem_if("%20" not in values.get("scope", ""), "scope space is not %%20: %r" % values.get("scope"))
        problems += problem_if("%25" in values.get("scope", "") or "%2520" in values.get("tf_extra", ""),
                               "a value was percent-encoded twice")
        return problems

    chk.case(GB, "B1 codex-key-order", b1)
    chk.case(GB, "B2 openai-nonce-resource-extras", b2)
    chk.case(GB, "B3 no-nonce-no-resource", b3)
    chk.case(GB, "B4 empty-redirect-valueerror", b4)
    chk.case(GB, "B5 percent-encoded-once", b5)


# --- C. request builders ----------------------------------------------------------

def group_c(chk):
    def c1(mod):
        row = provider(mod)
        url, headers, body = mod._oauth_exchange_request(row, "app_tf_codex", "code_tf", "ver_tf", "http://localhost:1455/auth/callback")
        pairs, fields = form_fields(body)
        ctype = header_map(headers).get("content-type", "")
        problems = problem_if(url != row.token_url, "URL %r, wanted the token_url" % url)
        problems += problem_if(ctype != "application/x-www-form-urlencoded", "content type %r" % ctype)
        expect = {"grant_type": "authorization_code", "code": "code_tf", "code_verifier": "ver_tf",
                  "client_id": "app_tf_codex", "redirect_uri": "http://localhost:1455/auth/callback"}
        problems += ["%s is %r, wanted %r" % (k, fields.get(k), v) for k, v in sorted(expect.items()) if fields.get(k) != v]
        problems += problem_if(len(pairs) != len(fields), "a duplicated form field")
        return problems, ["fields: %s" % ", ".join(k for k, _v in pairs)]

    def c2(mod):
        problems = []
        for row, resource in ((provider(mod), None), (openai_provider(mod), "https://api.tf.invalid/v1")):
            url, headers, body = mod._oauth_refresh_request(row, "cid_tf", "rt_tf")
            _pairs, fields = form_fields(body)
            problems += problem_if(url != row.token_url, "%s: URL %r" % (row.name, url))
            problems += problem_if(header_map(headers).get("content-type") != "application/x-www-form-urlencoded",
                                   "%s: not a form body" % row.name)
            problems += problem_if(fields.get("grant_type") != "refresh_token" or fields.get("refresh_token") != "rt_tf",
                                   "%s: fields %r" % (row.name, sorted(fields)))
            problems += problem_if("scope" in fields, "%s: refresh carries scope" % row.name)
            problems += problem_if(fields.get("resource") != resource,
                                   "%s: resource %r, wanted %r" % (row.name, fields.get("resource"), resource))
        return problems

    def c3(mod):
        row = openai_provider(mod)
        _url, _headers, body = mod._oauth_refresh_request(row, "issued_tf", "rt_tf")
        _pairs, fields = form_fields(body)
        problems = problem_if(fields.get("client_id") != "issued_tf", "client_id %r, wanted the issued one" % fields.get("client_id"))
        problems += problem_if("dynamic_agent_client" in body_text(body), "registration_client_id appears in the refresh body")
        return problems

    def c4(mod):
        row = provider(mod)
        url, headers, body = mod._oauth_device_start_request(row, "app_tf_codex")
        problems = problem_if(url != row.device_usercode_url, "URL %r" % url)
        problems += problem_if(not str(header_map(headers).get("content-type", "")).startswith("application/json"),
                               "not a JSON body")
        problems += problem_if(json.loads(body_text(body)) != {"client_id": "app_tf_codex"},
                               "body %r" % body_text(body))
        return problems

    def c5(mod):
        row = provider(mod)
        url, headers, body = mod._oauth_device_poll_request(row, "da_tf", "ABCD-EFGH")
        problems = problem_if(url != row.device_token_url, "URL %r" % url)
        problems += problem_if(not str(header_map(headers).get("content-type", "")).startswith("application/json"),
                               "not a JSON body")
        problems += problem_if(json.loads(body_text(body)) != {"device_auth_id": "da_tf", "user_code": "ABCD-EFGH"},
                               "body %r" % body_text(body))
        return problems

    chk.case(GC, "C1 exchange-form", c1)
    chk.case(GC, "C2 refresh-no-scope-resource", c2)
    chk.case(GC, "C3 refresh-issued-client-id", c3)
    chk.case(GC, "C4 device-usercode-json", c4)
    chk.case(GC, "C5 device-poll-json", c5)


# --- D. token response ------------------------------------------------------------

def jbody(obj):
    return json.dumps(obj).encode()


class no_int_digit_limit:
    """Lift the 3.11+ int_max_str_digits guard for a block, restoring it after:
    3.9.6 has none, so the module's own bound is what a case must measure."""

    def __enter__(self):
        self.saved = getattr(sys, "get_int_max_str_digits", lambda: None)()
        if self.saved is not None:
            sys.set_int_max_str_digits(0)
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            sys.set_int_max_str_digits(self.saved)
        return False


def group_d(chk):
    def parse(mod, status, body, previous=None, require=False):
        return mod._oauth_parse_token_response(status, body, NOW, previous, require_refresh=require)

    def d1(mod):
        tok = {"access_token": "at_" + secrets.token_hex(8), "refresh_token": "rt_" + secrets.token_hex(8),
               "expires_in": 3600, "id_token": oracle_jwt({"sub": "x"}), "scope": "openid tf.direct"}
        got = parse(mod, 200, jbody(tok), "rt_prev")
        expect = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
                  "expires_at": NOW + 3600, "id_token": tok["id_token"], "scope": tok["scope"]}
        return ["%s is %r, wanted %r" % (k, got.get(k), v) for k, v in sorted(expect.items()) if got.get(k) != v]

    def d2(mod):
        return expect_kind(mod, lambda: parse(mod, 200, jbody({"refresh_token": "rt", "expires_in": 60})),
                           "invalid_response", "no access_token")

    def d3(mod):
        got = parse(mod, 200, jbody({"access_token": "at_tf", "expires_in": 60}), "rt_prev")
        return problem_if(got.get("refresh_token") != "rt_prev",
                          "refresh_token %r, wanted the previous one" % got.get("refresh_token"))

    def d4(mod):
        return expect_kind(mod, lambda: parse(mod, 200, jbody({"access_token": "at_tf", "expires_in": 60}), "rt_prev", True),
                           "invalid_response", "require_refresh with no refresh_token")

    def d5(mod):
        problems = problem_if(set(mod.OAUTH_RELOGIN_CODES) != set(RELOGIN_CODES),
                              "OAUTH_RELOGIN_CODES is %r" % sorted(mod.OAUTH_RELOGIN_CODES))
        for code in RELOGIN_CODES:
            problems += expect_kind(mod, lambda c=code: parse(mod, 400, jbody({"error": c})), "relogin",
                                    "error=%s" % code, code)
            problems += expect_kind(mod, lambda c=code: parse(mod, 401, jbody({"error": {"code": c}})), "relogin",
                                    "error.code=%s" % code, code)
        return problems

    def d6(mod):
        problems = expect_kind(mod, lambda: parse(mod, 429, b"{}"), "rate_limited", "429")
        problems += expect_kind(mod, lambda: parse(mod, 500, b"{}"), "transient", "500")
        problems += expect_kind(mod, lambda: parse(mod, 503, b"not json"), "transient", "503 non-JSON")
        return problems

    def d7(mod):
        big = b'{"access_token":"' + b"a" * mod.OAUTH_BODY_LIMIT + b'"}'
        problems = expect_kind(mod, lambda: parse(mod, 200, big), "invalid_response", "body over OAUTH_BODY_LIMIT")
        problems += expect_kind(mod, lambda: parse(mod, 200, b"not json"), "invalid_response", "non-JSON body")
        problems += expect_kind(mod, lambda: parse(mod, 200, b"[1]"), "invalid_response", "non-object body")
        return problems

    def d8(mod):
        mark = sentinel()
        bad = (("CR/LF", mark + "\r\nX-Injected: 1"), ("space", mark + " tail"),
               ("over 16 KiB", mark + "a" * 16384))
        problems = []
        for what, value in bad:
            for field in ("access_token", "refresh_token"):
                tok = {"access_token": "at_tf", "refresh_token": "rt_tf", "expires_in": 60}
                tok[field] = value
                exc = oauth_error(mod, lambda t=tok: parse(mod, 200, jbody(t)))
                if exc is None or getattr(exc, "kind", None) != "invalid_response":
                    problems.append("%s %s: %r, wanted invalid_response" % (field, what, getattr(exc, "kind", exc)))
                    continue
                problems += ["%s %s: the sentinel is in the error text" % (field, what) for _n in leaks(exc, [mark])]
        return problems

    def d9(mod):
        unknown, desc, msg = sentinel(), sentinel(), sentinel()
        body = jbody({"error": unknown, "error_description": desc, "message": msg})
        exc = oauth_error(mod, lambda: parse(mod, 400, body))
        problems = problem_if(exc is None, "an unknown error raised no OAuthError")
        if exc is not None:
            problems += problem_if(getattr(exc, "code", None) != "other", "unknown error code is %r, wanted 'other'" % getattr(exc, "code", None))
            problems += ["a sentinel leaked into the OAuthError (%s)" % n[:3] for n in leaks(exc, [unknown, desc, msg])]
        exc = oauth_error(mod, lambda: parse(mod, 400, jbody({"error": "invalid_scope", "error_description": desc})))
        problems += problem_if(exc is None or getattr(exc, "code", None) != "invalid_scope",
                               "an allow-listed code did not surface as itself: %r" % getattr(exc, "code", exc))
        problems += problem_if(exc is not None and bool(leaks(exc, [desc])), "error_description leaked")
        problems += problem_if(mod._oauth_error_code({"error": unknown}) != "other", "_oauth_error_code passed an unknown code")
        problems += problem_if(mod._oauth_error_code({"error": "invalid_scope"}) != "invalid_scope",
                               "_oauth_error_code refused an allow-listed code")
        problems += problem_if("invalid_scope" not in mod.OAUTH_ERROR_CODES, "invalid_scope is not in OAUTH_ERROR_CODES")
        return problems

    def d10(mod):
        # F2: an expires_in of 0 or 1 makes the token due at once; without a floor on the
        # refresh deadline every request would cost one refresh POST.
        problems = []
        for expires_in in (0, 1):
            got = parse(mod, 200, jbody({"access_token": "at_tf", "refresh_token": "rt_tf", "expires_in": expires_in}))
            floor = got.get("earliest_refresh_at")
            problems += problem_if(not isinstance(floor, int) or floor < NOW + 60,
                                   "expires_in %d: earliest_refresh_at is %r, wanted >= now + 60" % (expires_in, floor))
        got = parse(mod, 200, jbody({"access_token": "at_tf", "refresh_token": "rt_tf", "expires_in": 0}))
        problems += problem_if(got.get("expires_at") != NOW, "expires_in 0: expires_at is %r, wanted now" % got.get("expires_at"))
        return problems

    def d11(mod):
        # F9: Python 3.9.6 has no int_max_str_digits, so int() over a huge literal is
        # quadratic; a 5000-digit integer must be refused as malformed, a normal one kept.
        # The interpreter's own limit (3.11+) is lifted for the call, so the module's
        # bound is what is measured, as on 3.9.6.
        problems = []
        huge = b'{"access_token":"at_tf","expires_in":' + b"9" * 5000 + b"}"
        started = time.monotonic()
        with no_int_digit_limit():
            problems += expect_kind(mod, lambda: parse(mod, 200, huge), "invalid_response", "a 5000-digit expires_in")
            problems += expect_kind(mod, lambda: mod._oauth_json_object(b'{"n":-' + b"1" * 5000 + b"}"),
                                    "invalid_response", "a 5000-digit negative integer")
        problems += problem_if(time.monotonic() - started > 5.0, "refusing a 5000-digit integer took over 5 s")
        try:
            obj = mod._oauth_json_object(b'{"n":123456789012345678901234567890,"m":-7}')
        except Exception as exc:
            return problems + ["a normal integer was refused: %s" % type(exc).__name__]
        problems += problem_if(obj != {"n": 123456789012345678901234567890, "m": -7}, "normal integers parsed as %r" % obj)
        return problems

    chk.case(GD, "D1 happy-path-expires-at", d1)
    chk.case(GD, "D2 missing-access-token", d2)
    chk.case(GD, "D3 keeps-previous-refresh", d3)
    chk.case(GD, "D4 require-refresh", d4)
    chk.case(GD, "D5 relogin-codes-both-shapes", d5)
    chk.case(GD, "D6 status-classes", d6)
    chk.case(GD, "D7 body-cap-and-non-json", d7)
    chk.case(GD, "D8 token-shape-no-sentinel", d8)
    chk.case(GD, "D9 error-code-allow-list", d9)
    chk.case(GD, "D10 refresh-floor-expires-in-zero", d10)
    chk.case(GD, "D11 huge-integer-literal-refused", d11)


# --- E. JWT and ID token ------------------------------------------------------------

def group_e(chk):
    def good_claims():
        return {"iss": "https://auth.tf.invalid", "aud": "issued_tf", "exp": NOW + 600, "nonce": "nonce_tf"}

    def check(mod, claims, nonce="nonce_tf", row=None):
        row = row if row is not None else openai_provider(mod)
        return mod._oauth_check_id_token(claims, row, "issued_tf", nonce, NOW)

    def e1(mod):
        claims = {"sub": "user_tf", "n": 1, "nested": {"k": "v"}}
        got = mod._oauth_jwt_claims(oracle_jwt(claims))
        return problem_if(got != claims, "claims %r, wanted %r" % (got, claims))

    def e2(mod):
        good = oracle_jwt({"a": 1})
        h, p, s = good.split(".")
        bad = ["%s.%s" % (h, p), "%s.%s.%s.%s" % (h, p, s, s), h]
        return ["%d parts were not refused" % (t.count(".") + 1) for t in bad
                if refused(mod, lambda t=t: mod._oauth_jwt_claims(t)) is None]

    def e3(mod):
        h, _p, s = oracle_jwt({"a": 1}).split(".")
        return problem_if(refused(mod, lambda: mod._oauth_jwt_claims("%s.%s.%s" % (h, "!!*$", s))) is None,
                          "a payload of junk base64 was not refused")

    def e4(mod):
        return ["payload %r was not refused" % p for p in (b"[1, 2]", b'"str"', b"not json")
                if refused(mod, lambda p=p: mod._oauth_jwt_claims(oracle_jwt(p))) is None]

    def e5(mod):
        big = jbody({"pad": "a" * mod.OAUTH_JWT_LIMIT})
        return problem_if(refused(mod, lambda: mod._oauth_jwt_claims(oracle_jwt(big))) is None,
                          "a %d-byte payload was not refused" % len(big))

    def e6(mod):
        claims = good_claims()
        claims["iss"] = "https://evil.tf.invalid"
        problems = expect_kind(mod, lambda: check(mod, claims), "invalid_response", "iss mismatch", "iss")
        check(mod, claims, row=openai_provider(mod, issuer=""))  # "" = no iss check: must not raise
        check(mod, good_claims())                                 # the baseline passes
        return problems

    def e7(mod):
        problems = []
        for aud in ("issued_tf", ["other_tf", "issued_tf"]):
            claims = good_claims()
            claims["aud"] = aud
            exc = oauth_error(mod, lambda c=claims: check(mod, c))
            problems += problem_if(exc is not None, "aud %r was refused: %r" % (aud, getattr(exc, "code", exc)))
        for aud in ("other_tf", ["other_tf"], []):
            claims = good_claims()
            claims["aud"] = aud
            problems += expect_kind(mod, lambda c=claims: check(mod, c), "invalid_response", "aud %r" % (aud,), "aud")
        return problems

    def e8(mod):
        problems = []
        for exp in (NOW, NOW - 1):
            claims = good_claims()
            claims["exp"] = exp
            problems += expect_kind(mod, lambda c=claims: check(mod, c), "invalid_response", "exp=now%+d" % (exp - NOW), "exp")
        claims = good_claims()
        claims["exp"] = NOW + 1
        exc = oauth_error(mod, lambda: check(mod, claims))
        problems += problem_if(exc is not None, "exp one second ahead was refused")
        return problems

    def e9(mod):
        claims = good_claims()
        claims["nonce"] = "nonce_other"
        problems = expect_kind(mod, lambda: check(mod, claims), "invalid_response", "nonce mismatch", "nonce")
        needed = "tf.direct"
        problems += problem_if(mod._oauth_scope_has("openid email", needed), "a missing required_scope was accepted")
        problems += problem_if(mod._oauth_scope_has("openid tf.directX", needed), "a scope prefix was accepted")
        problems += problem_if(mod._oauth_scope_has("tf", needed), "a scope substring was accepted")
        problems += problem_if(not mod._oauth_scope_has("openid  tf.direct email", needed),
                               "a present required_scope was not found")
        return problems

    def e10(mod):
        ns = "https://api.openai.com/auth"
        cases = [
            ({ns: {"chatgpt_account_id": "acc_tf-1", "chatgpt_data_residency": "us",
                   "chatgpt_compute_residency": "eu"}}, ("acc_tf-1", "us")),
            ({ns: {"chatgpt_account_id": "acc_tf", "chatgpt_compute_residency": "eu"}}, ("acc_tf", "eu")),
            ({ns: {"chatgpt_account_id": "acc tf", "chatgpt_data_residency": "r" * 129}}, (None, None)),
            ({ns: {"chatgpt_account_id": "acc\r\nx", "chatgpt_data_residency": 7}}, (None, None)),
            ({ns: {"chatgpt_account_id": "a" * 128}}, ("a" * 128, None)),
            ({ns: "not an object"}, (None, None)),
            ({}, (None, None)),
        ]
        problems = []
        for claims, want in cases:
            got = tuple(mod._oauth_account_claims(claims))
            problems += problem_if(got != want, "%r gave %r, wanted %r" % (claims, got, want))
        return problems

    chk.case(GE, "E1 claims-decode", e1)
    chk.case(GE, "E2 bad-part-count", e2)
    chk.case(GE, "E3 bad-base64", e3)
    chk.case(GE, "E4 non-object-payload", e4)
    chk.case(GE, "E5 payload-over-limit", e5)
    chk.case(GE, "E6 iss-mismatch", e6)
    chk.case(GE, "E7 aud-str-list-mismatch", e7)
    chk.case(GE, "E8 exp-against-now", e8)
    chk.case(GE, "E9 nonce-and-required-scope", e9)
    chk.case(GE, "E10 account-claims", e10)


# --- F. callback parse ----------------------------------------------------------------

CB_PATH = "/auth/callback"


def group_f(chk):
    def cb(mod, target, state):
        return mod._oauth_parse_callback(target, CB_PATH, state)

    def f1(mod):
        st = "st_" + secrets.token_hex(8)
        got = cb(mod, "%s?code=code_tf&state=%s" % (CB_PATH, st), st)
        problems = problem_if(got.get("code") != "code_tf", "code %r" % got.get("code"))
        problems += problem_if(got.get("state") != st, "state %r" % got.get("state"))
        return problems

    def f2(mod):
        st = "st_tf"
        return ["path %r was not refused" % p for p in ("/auth/callback/", "/auth/callbackX", "/auth", "/")
                if expect_kind(mod, lambda p=p: cb(mod, "%s?code=c&state=%s" % (p, st), st), "invalid_callback", p)]

    def f3(mod):
        problems = expect_kind(mod, lambda: cb(mod, CB_PATH + "?code=c&state=wrong", "st_tf"), "invalid_callback", "state mismatch")
        problems += expect_kind(mod, lambda: cb(mod, CB_PATH + "?code=c", "st_tf"), "invalid_callback", "state missing")
        return problems

    def f4(mod):
        st = "st_tf"
        problems = expect_kind(mod, lambda: cb(mod, CB_PATH + "?error=access_denied&state=" + st, st),
                               "denied", "error= with the matching state", "access_denied")
        mark = sentinel()
        exc = oauth_error(mod, lambda: cb(mod, CB_PATH + "?error=access_denied&error_description=%s&state=%s" % (mark, st), st))
        problems += problem_if(exc is not None and bool(leaks(exc, [mark])), "error_description leaked into the denial")
        problems += expect_kind(mod, lambda: cb(mod, CB_PATH + "?error=access_denied&state=wrong", st),
                                "invalid_callback", "error= with a wrong state")
        problems += expect_kind(mod, lambda: cb(mod, CB_PATH + "?error=access_denied", st),
                                "invalid_callback", "error= with no state")
        return problems

    def f5(mod):
        st = "st_tf"
        bad = ["?code=a&code=b&state=" + st, "?code=a&state=%s&state=%s" % (st, st)]
        return ["%r was not refused" % q for q in bad
                if expect_kind(mod, lambda q=q: cb(mod, CB_PATH + q, st), "invalid_callback", q)]

    def f6(mod):
        st = "st_tf"
        got = cb(mod, "%s?code=c&state=%s&client_id=app_issued_tf" % (CB_PATH, st), st)
        problems = problem_if(got.get("client_id") != "app_issued_tf", "client_id %r, wanted the issued one" % got.get("client_id"))
        for bad in ("bad%20id", "bad%0D%0Aid", "x" * 300):
            problems += expect_kind(mod, lambda b=bad: cb(mod, "%s?code=c&state=%s&client_id=%s" % (CB_PATH, st, b), st),
                                    "invalid_callback", "client_id %r" % bad[:20])
        return problems

    def f7(mod):
        st = "st_tf"
        mark = sentinel()
        target = "%s?code=c&state=%s&scope=%s&id_token=%s&chatgpt_account_id=%s" % (CB_PATH, st, mark, mark, mark)
        got = cb(mod, target, st)
        problems = problem_if(set(got) != {"code", "state", "client_id"}, "keys %r" % sorted(got))
        problems += problem_if(mark in repr(got), "a non-surfaced parameter's value is in the result")
        return problems

    chk.case(GF, "F1 good-callback", f1)
    chk.case(GF, "F2 exact-path", f2)
    chk.case(GF, "F3 state-mismatch", f3)
    chk.case(GF, "F4 error-only-with-matching-state", f4)
    chk.case(GF, "F5 duplicate-parameter", f5)
    chk.case(GF, "F6 issued-client-id", f6)
    chk.case(GF, "F7 only-code-state-client-id", f7)


# --- G. device flow ----------------------------------------------------------------

def group_g(chk):
    def g1(mod):
        got = mod._oauth_parse_device_start(200, jbody({"device_auth_id": "da_tf", "user_code": "ABCD-EFGH", "interval": 5}))
        want = {"device_auth_id": "da_tf", "user_code": "ABCD-EFGH", "interval": 5}
        return ["%s is %r, wanted %r" % (k, got.get(k), v) for k, v in sorted(want.items()) if got.get(k) != v]

    def g2(mod):
        return ["status %d was not pending: %r" % (s, got) for s in (403, 404)
                for got in [mod._oauth_parse_device_poll(s, jbody({"error": "authorization_pending"}))] if got is not None]

    def g3(mod):
        got = mod._oauth_parse_device_poll(200, jbody({"authorization_code": "ac_tf", "code_verifier": "cv_tf",
                                                       "code_challenge": "ch_tf"}))
        got = got or {}
        return problem_if(got.get("authorization_code") != "ac_tf" or got.get("code_verifier") != "cv_tf",
                          "granted poll gave %r" % got)

    def g4(mod):
        problems = []
        for given, want in ((0, 1), (-5, 1), (1, 1), (7, 7), (60, 60), (61, 60), (3600, 60)):
            got = mod._oauth_parse_device_start(200, jbody({"device_auth_id": "da", "user_code": "U", "interval": given}))
            problems += problem_if(got.get("interval") != want, "interval %r gave %r, wanted %r" % (given, got.get("interval"), want))
        return problems

    def g5(mod):
        # F51: the user_code is printed for the user to type, so an endpoint cannot make the
        # login print a screenful: more than 64 characters is invalid_response (64 is the
        # bound OAuthError puts on a code; a real code is 9).  64 itself is accepted.
        problems = []
        for size in (9, 64):
            code = "U" * size
            got = mod._oauth_parse_device_start(200, jbody({"device_auth_id": "da_tf", "user_code": code, "interval": 5}))
            problems += problem_if(got.get("user_code") != code, "a %d-character user_code gave %r" % (size, got.get("user_code")))
        for size in (65, 4096):
            problems += expect_kind(mod, lambda s=size: mod._oauth_parse_device_start(200, jbody({"device_auth_id": "da_tf", "user_code": "U" * s, "interval": 5})),
                                    "invalid_response", "a %d-character user_code" % size)
        return problems

    chk.case(GG, "G1 usercode-parse", g1)
    chk.case(GG, "G2 poll-pending-403-404", g2)
    chk.case(GG, "G3 poll-granted", g3)
    chk.case(GG, "G4 interval-clamped", g4)
    chk.case(GG, "G5 usercode-length-capped", g5)


# --- H. callback listener ------------------------------------------------------------

LOOPBACK = "127.0.0.1"
CLIENT_TIMEOUT_S = 5.0      # one client read; a hung acceptor costs this, never the run
JOIN_TIMEOUT_S = 5.0        # one join after the event that must end the acceptor
SETTLE_S = 0.3              # "the wait continues": the acceptor is still alive after this
SLOW_HEAD_PATCH_S = 0.5     # H6 lowers OAUTH_CALLBACK_CONN_TIMEOUT_S to this for its run
SLOW_HEAD_READ_S = 8.0      # H6's client read: covers the unpatched 5 s as well


class FakeClock:
    """The injected clock: frozen until a case advances it."""

    def __init__(self, start=1000.0):
        self._now = float(start)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self._now

    def advance(self, seconds):
        with self._lock:
            self._now += seconds


def http_request(target, host, method="GET"):
    text = "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (method, target, host)
    return text.encode("latin-1")


def exchange(port, payload, timeout=CLIENT_TIMEOUT_S):
    """Send *payload* to the loopback *port* and read until EOF.

    Returns (response bytes, eof): eof is True when the server closed the
    connection (recv returned b"" or the connection was reset), False when a
    read timed out. A refused connect raises OSError for the caller.
    """
    sock = socket.create_connection((LOOPBACK, port), timeout=timeout)
    try:
        try:
            sock.sendall(payload)
        except OSError:
            pass  # the server may answer (431) and close before it has read everything
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                return b"".join(chunks), False
            except (ConnectionResetError, ConnectionAbortedError):
                return b"".join(chunks), True
            if not data:
                return b"".join(chunks), True
            chunks.append(data)
    finally:
        sock.close()


def split_response(raw):
    """(status int or None, {lowercased header: value}, body bytes)."""
    head, _sep, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ", 2) if lines and lines[0] else []
    status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return status, headers, body


def free_port():
    """A port the kernel just handed out on loopback, released again (never connected)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class Session:
    """One acceptor run: _oauth_listen sockets, a daemon thread, a fake clock, a cancel event.

    allowed_hosts is the Host header values the browser may send for this
    redirect, port included: ("127.0.0.1:<port>", "localhost:<port>").
    """

    def __init__(self, mod, port=0):
        self.mod = mod
        self.listeners = mod._oauth_listen((LOOPBACK,), port)
        self.port = self.listeners[0].getsockname()[1]
        self.host = "%s:%d" % (LOOPBACK, self.port)
        self.state = "st_" + secrets.token_hex(16)
        self.clock = FakeClock()
        self.cancel = threading.Event()
        self.deadline = self.clock() + 60
        self.result = None
        self.error = None
        allowed = (self.host, "localhost:%d" % self.port)
        args = (self.listeners, CB_PATH, self.state, allowed, self.deadline, self.clock, self.cancel)
        self.thread = threading.Thread(target=self._run, args=args, daemon=True)
        self.thread.start()

    def _run(self, *args):
        try:
            self.result = self.mod._oauth_sync_accept_callback(*args)
        except BaseException as exc:  # judged by the case, never re-raised on the thread
            self.error = exc

    def send(self, target, host=None, method="GET"):
        return exchange(self.port, http_request(target, host or self.host, method))

    def good_target(self, code="code_tf"):
        return "%s?code=%s&state=%s" % (CB_PATH, code, self.state)

    def still_waiting(self):
        self.thread.join(SETTLE_S)
        return self.thread.is_alive()

    def finish(self):
        self.thread.join(JOIN_TIMEOUT_S)
        return not self.thread.is_alive()

    def listeners_closed(self):
        return all(sock.fileno() == -1 for sock in self.listeners)

    def close(self):
        self.cancel.set()
        self.thread.join(JOIN_TIMEOUT_S)
        for sock in self.listeners:
            sock.close()


def finish_good(sess, what, code="code_tf"):
    """Problems unless a good callback now ends *sess* with the parsed dict and a server close."""
    _raw, eof = sess.send(sess.good_target(code))
    problems = problem_if(not eof, "%s: the server did not close the connection after the page" % what)
    problems += problem_if(not sess.finish(), "%s: the acceptor did not return after a good callback" % what)
    problems += problem_if(sess.error is not None, "%s: the acceptor raised %s" % (what, type(sess.error).__name__))
    got = sess.result if isinstance(sess.result, dict) else {}
    problems += problem_if(got.get("code") != code or got.get("state") != sess.state,
                           "%s: returned keys %r, not the good callback" % (what, sorted(got)))
    return problems


def acceptor_error(mod, sess, kind, code=None):
    """Problems unless *sess* ended with OAuthError of *kind* (and *code*, when given)."""
    exc = sess.error
    if not isinstance(exc, mod.OAuthError):
        ended = type(exc).__name__ if exc is not None else "a return of %s" % type(sess.result).__name__
        return ["the acceptor ended with %s, wanted OAuthError %s" % (ended, kind)]
    problems = problem_if(getattr(exc, "kind", None) != kind, "kind %r, wanted %r" % (getattr(exc, "kind", None), kind))
    if code is not None:
        problems += problem_if(getattr(exc, "code", None) != code, "code %r, wanted %r" % (getattr(exc, "code", None), code))
    return problems


def bad_then_good(mod, payload_of, status):
    """A bad request gets _oauth_callback_page(False, status), the wait continues, a good one ends it."""
    page = mod._oauth_callback_page(False, status)
    sess = Session(mod)
    try:
        raw, eof = exchange(sess.port, payload_of(sess))
        got, _headers, _body = split_response(raw)
        problems = problem_if(raw != page, "status %r: the response is not _oauth_callback_page(False, %d)" % (got, status))
        problems += problem_if(not eof, "the server did not close the bad request's connection")
        problems += problem_if(not sess.still_waiting(), "the wait ended on the bad request")
        problems += finish_good(sess, "after the %d" % status)
        return problems
    finally:
        sess.close()


def group_h(chk):
    def h1(mod):
        page = mod._oauth_callback_page(True, 200)
        sess = Session(mod)
        try:
            raw, eof = sess.send(sess.good_target())
            status, headers, _body = split_response(raw)
            problems = problem_if(raw != page, "the response is not _oauth_callback_page(True, 200) byte for byte")
            problems += problem_if(status != 200, "status %r, wanted 200" % status)
            for name, want in (("connection", "close"), ("cache-control", "no-store"), ("referrer-policy", "no-referrer")):
                problems += problem_if(headers.get(name, "").lower() != want, "%s is %r, wanted %r" % (name, headers.get(name), want))
            problems += problem_if(not eof, "no EOF after the page: the server did not close the connection")
            problems += problem_if(not sess.finish(), "the acceptor did not return after a good callback")
            problems += problem_if(sess.error is not None, "the acceptor raised %s" % type(sess.error).__name__)
            got = sess.result if isinstance(sess.result, dict) else {}
            problems += problem_if(got.get("code") != "code_tf" or got.get("state") != sess.state,
                                   "returned keys %r, not the good callback" % sorted(got))
            problems += problem_if(not sess.listeners_closed(), "a listener is still open after success")
            return problems
        finally:
            sess.close()

    def h2(mod):
        return bad_then_good(mod, lambda s: http_request(CB_PATH + "X?code=c&state=" + s.state, s.host), 404)

    def h3(mod):
        return bad_then_good(mod, lambda s: http_request(s.good_target(), s.host, "POST"), 405)

    def h4(mod):
        return bad_then_good(mod, lambda s: http_request(s.good_target(), "evil.tf.invalid:%d" % s.port), 400)

    def h5(mod):
        limit = mod.OAUTH_CALLBACK_HEAD_LIMIT

        def oversize(sess):
            head = ("GET %s HTTP/1.1\r\nHost: %s\r\nX-Pad: " % (sess.good_target(), sess.host)).encode("latin-1")
            return head + b"a" * (limit + 1 - len(head))   # limit + 1 bytes, no end of head

        return bad_then_good(mod, oversize, 431), ["sent %d head bytes with no blank line" % (limit + 1)]

    def h6(mod):
        old = mod.OAUTH_CALLBACK_CONN_TIMEOUT_S
        mod.OAUTH_CALLBACK_CONN_TIMEOUT_S = SLOW_HEAD_PATCH_S
        sess = None
        try:
            sess = Session(mod)
            partial = ("GET %s HTTP/1.1\r\nHost: %s\r\n" % (sess.good_target(), sess.host)).encode("latin-1")
            start = time.monotonic()
            _raw, eof = exchange(sess.port, partial, SLOW_HEAD_READ_S)
            took = time.monotonic() - start
            problems = problem_if(not eof, "the slow head was not closed within %.1fs" % SLOW_HEAD_READ_S)
            problems += problem_if(took < SLOW_HEAD_PATCH_S * 0.6, "closed after %.2fs, before the per-connection deadline" % took)
            problems += problem_if(not sess.still_waiting(), "the wait ended on a slow head")
            problems += finish_good(sess, "after the slow head")
            return problems, ["OAUTH_CALLBACK_CONN_TIMEOUT_S patched to %.1fs (was %r); closed after %.2fs" % (SLOW_HEAD_PATCH_S, old, took)]
        finally:
            mod.OAUTH_CALLBACK_CONN_TIMEOUT_S = old
            if sess is not None:
                sess.close()

    def h7(mod):
        limit = mod.OAUTH_CALLBACK_BAD_LIMIT
        if not isinstance(limit, int) or not 1 <= limit <= 256:
            return ["OAUTH_CALLBACK_BAD_LIMIT is %r, not a small positive int" % (limit,)]
        sess = Session(mod)
        try:
            bad = CB_PATH + "X?code=c&state=" + sess.state
            for _i in range(limit - 1):
                sess.send(bad)
            sent = limit - 1
            problems = problem_if(not sess.still_waiting(), "the wait ended after %d bad requests, under the budget of %d" % (sent, limit))
            for _extra in range(2):
                sess.thread.join(SETTLE_S)
                if not sess.thread.is_alive():
                    break
                try:
                    sess.send(bad)
                except OSError:
                    break
                sent += 1
            problems += problem_if(not sess.finish(), "the acceptor still waits after %d bad requests" % sent)
            problems += acceptor_error(mod, sess, "callback_abuse")
            return problems, ["%d bad requests sent; budget %d" % (sent, limit)]
        finally:
            sess.close()

    def h8(mod):
        sess = Session(mod)
        try:
            problems = problem_if(not sess.still_waiting(), "the wait ended before the fake clock moved")
            sess.clock.advance(61)
            problems += problem_if(not sess.finish(), "the acceptor did not return after the deadline passed")
            problems += acceptor_error(mod, sess, "callback_timeout")
            return problems
        finally:
            sess.close()

    def h9(mod):
        sess = Session(mod)
        try:
            problems = problem_if(not sess.still_waiting(), "the wait ended before cancel was set")
            sess.cancel.set()
            problems += problem_if(not sess.finish(), "the acceptor did not return after cancel was set")
            problems += problem_if(sess.error is not None, "the acceptor raised %s on cancel" % type(sess.error).__name__)
            problems += problem_if(sess.result is not None, "cancel returned a %s, wanted None" % type(sess.result).__name__)
            return problems
        finally:
            sess.close()

    def h10(mod):
        mark = sentinel()
        sess = Session(mod)
        try:
            pages = [
                sess.send("/auth/%s?code=c&state=%s&x=%s" % (mark, sess.state, mark))[0],
                sess.send(sess.good_target(), host="%s.tf.invalid:%d" % (mark, sess.port))[0],
                sess.send("%s?code=c&state=%s" % (CB_PATH, mark))[0],
                sess.send("%s?code=%s&state=%s&junk=%s" % (CB_PATH, mark, sess.state, mark))[0],
            ]
            problems = ["page %d is empty" % i for i, raw in enumerate(pages) if not raw]
            problems += ["page %d reflects the query/host sentinel" % i for i, raw in enumerate(pages)
                         if mark.encode() in raw or mark.lower().encode() in raw.lower()]
            problems += problem_if(not sess.finish(), "the acceptor did not return after the good callback")
            got = sess.result if isinstance(sess.result, dict) else {}
            problems += problem_if(got.get("code") != mark, "the good callback did not end the wait")
            return problems, ["404, 400 (host), 400 (state) and 200 pages checked"]
        finally:
            sess.close()

    def h11(mod):
        sess = Session(mod)
        try:
            problems = finish_good(sess, "the first callback")
            try:
                sock = socket.create_connection((LOOPBACK, sess.port), timeout=CLIENT_TIMEOUT_S)
            except OSError:
                return problems
            sock.close()
            return problems + ["a second connect after success was accepted"]
        finally:
            sess.close()

    def h12(mod):
        problems = []
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind((LOOPBACK, 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]
            try:
                stolen = mod._oauth_listen((LOOPBACK,), port)
            except OSError:
                stolen = None
            if stolen is not None:
                for sock in stolen:
                    sock.close()
                problems.append("_oauth_listen bound a port that has a LIVE listener")
        finally:
            blocker.close()
        socks = mod._oauth_listen((LOOPBACK,), 0)
        try:
            problems += problem_if(not socks, "_oauth_listen returned no socket")
            if os.name == "posix":
                for sock in socks:
                    problems += problem_if(sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0, "SO_REUSEADDR is not set on POSIX")
                    if hasattr(socket, "SO_REUSEPORT"):
                        problems += problem_if(sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0, "SO_REUSEPORT is set")
        finally:
            for sock in socks:
                sock.close()
        return problems, ["posix=%s SO_REUSEPORT defined=%s" % (os.name == "posix", hasattr(socket, "SO_REUSEPORT"))]

    def h13(mod):
        port = free_port()
        problems = []
        for rnd in (1, 2):
            try:
                sess = Session(mod, port=port)
            except OSError as exc:
                problems.append("round %d: _oauth_listen on port %d raised %s (errno %s)" % (rnd, port, type(exc).__name__, exc.errno))
                break
            try:
                problems += finish_good(sess, "round %d" % rnd)
                problems += problem_if(not sess.listeners_closed(), "round %d: a listener is still open" % rnd)
            finally:
                sess.close()
        return problems, ["proves the immediate re-bind over the listener-side TIME_WAIT succeeds with SO_REUSEADDR set "
                          "(H12 checks it is set); it does NOT prove the option is needed: no case re-binds without it"]

    def h14(mod):
        deny, good = mod._oauth_callback_page(False, 200), mod._oauth_callback_page(True, 200)
        problems = problem_if(deny == good, "_oauth_callback_page(False, 200) equals the success page")
        sess = Session(mod)
        try:
            raw, eof = sess.send("%s?error=access_denied&state=%s" % (CB_PATH, sess.state))
            status, _headers, _body = split_response(raw)
            problems += problem_if(raw != deny, "matching state: the response is not _oauth_callback_page(False, 200)")
            problems += problem_if(status != 200, "matching state: status %r, wanted 200" % status)
            problems += problem_if(not eof, "matching state: the server did not close the connection")
            problems += problem_if(not sess.finish(), "matching state: the wait did not end on the denial")
            problems += ["matching state: " + p for p in acceptor_error(mod, sess, "denied", "access_denied")]
            problems += problem_if(not sess.listeners_closed(), "matching state: a listener is still open")
        finally:
            sess.close()
        sess = Session(mod)
        try:
            raw, _eof = sess.send("%s?error=access_denied&state=wrong_%s" % (CB_PATH, sess.state))
            problems += problem_if(raw != mod._oauth_callback_page(False, 400), "wrong state: the response is not _oauth_callback_page(False, 400)")
            problems += problem_if(not sess.still_waiting(), "wrong state: the wait ended")
            problems += finish_good(sess, "wrong state")
        finally:
            sess.close()
        return problems

    chk.case(GH, "H1 good-callback-200-close-eof", h1)
    chk.case(GH, "H2 wrong-path-404-continues", h2)
    chk.case(GH, "H3 post-405", h3)
    chk.case(GH, "H4 wrong-host-400", h4)
    chk.case(GH, "H5 oversize-head-431", h5)
    chk.case(GH, "H6 slow-head-closed-continues", h6)
    chk.case(GH, "H7 bad-budget-callback-abuse", h7)
    chk.case(GH, "H8 deadline-callback-timeout", h8)
    chk.case(GH, "H9 cancel-returns-none", h9)
    chk.case(GH, "H10 page-reflects-no-input", h10)
    chk.case(GH, "H11 second-connect-refused", h11)
    chk.case(GH, "H12 live-port-oserror-reuseaddr", h12)
    chk.case(GH, "H13 back-to-back-rebind", h13)
    chk.case(GH, "H14 matching-state-denied-200", h14)


# --- I. contract (ast over the source) ------------------------------------------------

GENERATOR = H.repo_path("Scripts", "amalgamate.py")
TYPING_NAMES = frozenset(getattr(typing, "__all__", ())) | {"typing"}
FORBIDDEN_DOTTED = ("urllib.request", "os.environ")
FORBIDDEN_MODULES = ("time", "asyncio", "urllib.request")


def source_tree():
    """(text, ast) of SOURCE; a SyntaxError is the case's red problem."""
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    return text, ast.parse(text, filename="_mcp_oauth.py")


def dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def annotation_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def annotation_names(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            yield sub.id
        elif isinstance(sub, ast.Attribute):
            yield sub.attr
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                inner = ast.parse(sub.value, mode="eval")
            except SyntaxError:
                continue
            for name in annotation_names(inner):
                yield name


def group_i(chk):
    def i1(mod):
        _text, tree = source_tree()
        problems = []
        count = 0
        for ann in annotation_nodes(tree):
            count += 1
            problems += ["line %d: annotation names typing's %s" % (ann.lineno, n) for n in annotation_names(ann) if n in TYPING_NAMES]
        return problems, ["%d annotations walked" % count]

    def i2(mod):
        _text, tree = source_tree()
        problems = []
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Import):
                problems += ["line %d: import %s" % (line, a.name) for a in node.names
                             if a.name in FORBIDDEN_MODULES or a.name.startswith(("asyncio.", "urllib.request."))]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [a.name for a in node.names]
                if base in FORBIDDEN_MODULES or base.startswith(("asyncio.", "urllib.request.")):
                    problems.append("line %d: from %s import" % (line, base))
                if (base == "urllib" and "request" in names) or (base == "os" and "environ" in names):
                    problems.append("line %d: from %s import %s" % (line, base, ", ".join(names)))
            elif isinstance(node, ast.Name) and node.id in ("time", "asyncio", "environ"):
                problems.append("line %d: a reference to %s" % (line, node.id))
            elif isinstance(node, ast.Attribute) and dotted_name(node) in FORBIDDEN_DOTTED:
                problems.append("line %d: a reference to %s" % (line, dotted_name(node)))
        return problems

    def i3(mod):
        text, _tree = source_tree()
        gen = H.load_module_from_path("amalgamate_for_oauth", GENERATOR)
        blocks = gen.load_blocks_text("_mcp_oauth.py", text)
        problems = problem_if(not blocks, "the source defines no block")
        problems += ["%s is not tab-safe" % name for name, block in sorted(blocks.items()) if not gen.block_is_tab_safe(block)]
        return problems, ["%d blocks checked" % len(blocks)]

    def i4(mod):
        _text, tree = source_tree()
        problems = []
        for index, node in enumerate(tree.body):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                continue
            if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            problems.append("line %d: a module-level %s" % (node.lineno, type(node).__name__))
        return problems, ["%d module-level statements" % len(tree.body)]

    chk.case(GI, "I1 no-typing-annotation", i1)
    chk.case(GI, "I2 no-clock-http-asyncio-environ", i2)
    chk.case(GI, "I3 blocks-tab-safe", i3)
    chk.case(GI, "I4 module-statements-block-shaped", i4)


# The groups in run order.  A group whose step has not landed yet is absent
# from this module and is simply not called.
GROUP_ORDER = ("group_a", "group_b", "group_c", "group_d", "group_e",
               "group_f", "group_g", "group_h", "group_i")


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="the stdlib OAuth core: PKCE, grants, tokens, callback",
                    opts=opts, mode="grouped", cid_width=34)
    try:
        mod, load_error = H.load_module_from_path("mcp_oauth_under_test", SOURCE), ""
    except Exception as exc:  # a missing or broken source is red per case, never a crash
        mod, load_error = None, "%s: %s" % (type(exc).__name__, exc)
    chk = Checker(suite, mod, load_error)
    for name in GROUP_ORDER:
        group = globals().get(name)
        if group is not None:
            group(chk)
    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
