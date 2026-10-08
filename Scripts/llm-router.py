#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""LLM-Router: an Anthropic Messages API router in front of local and remote LLM backends.

One router instance serves Claude Code. It accepts Anthropic Messages requests
(/v1/messages, /v1/messages/count_tokens), looks the request's `model` up in
its JSON config, and forwards the call to the backend that route names. There
are five backend kinds (the sixth name, anthropic, is reserved and refused):
  passthrough  relayed as-is to an Anthropic-compatible upstream;
  llamacpp     llama-server's own /v1/messages, its quirks repaired;
  mistral      translated to and from the Mistral chat-completions API, with
               reasoning: thinking blocks out (signature "lms1."), back in on
               replay, and a reasoning_effort from the route, the thinking
               budget, the route's reasoning_effort_map and what a model's 400
               taught the router (learned per backend and model, one retry);
  codex        the OpenAI Responses API on the ChatGPT-subscription endpoint,
               OAuth only, sent with the omp client identity;
  openai       the OpenAI Responses API on the platform endpoint, with an
               api_key or an OAuth (Sign in with ChatGPT) login.
codex and openai share one adapter driven by a profile row per wire dialect;
they always stream upstream and aggregate for a non-stream client, and carry
reasoning as thinking blocks signed "lrs1.<backend>.". GET /v1/models and
GET /v1/models/{id} list the config's route keys (with an optional route
display_name and the route's optional advertised max_input_tokens / max_tokens,
plus context_window) in the Anthropic model-list shape, never contacting a backend.
Every answer the client sees is Anthropic-shaped: a JSON body, an SSE stream
that always closes its envelope, or the {"type":"error","error":{...}}
envelope. Requires only Python 3.9+ stdlib modules.

Layout: one file, eight units, in file order (each has one reason to change)
-----------------------------------------------------------------------------
  1. ConfigError, ApiError, UpstreamError, _STATUS_TO_TYPE, _rt_error_body() -- the error classes and
     the Anthropic error envelope; plus the pure helpers units 2-5 share: _log_value (generated from
     Scripts/_mcp_logging.py), _http_token_value (generated from Scripts/_mcp_httpfront.py),
     _rt_dumps(), _rt_loads() and _rt_make_scrubber() (KD-9).
  2. BackendSpec, RouteSpec, RouterConfig, load_config() -- the config schema. Pure: reads, and on
     login/refresh rewrites, one file; never opens a socket.
  3. InboundRequest, _rt_parse_inbound(), _rt_route() -- validate the Anthropic request once; route lookup;
     _rt_models_list() / _rt_model_entry() -- the GET /v1/models answer shapes.
  4. The SSE toolkit -- _rt_sse_events() (a line parser) and AnthropicSseEncoder (a pure state machine
     that always closes its envelope).
  5. Adapters -- Adapter (the interface), PassthroughAdapter, LlamacppAdapter (+ its quirk registry),
     MistralAdapter (+ the tool-id map, _rt_ms_translate, MistralStreamTranslator), the Responses
     vocabulary as data (_RT_RESPONSES_PROFILES, _RT_CODEX_IDENTITY), _rt_rs_translate,
     ResponsesStreamTranslator, ResponsesAdapter with CodexAdapter / OpenaiAdapter, the kind
     registry _RT_KIND_TABLE and what derives from it (_KINDS, RESERVED_KINDS,
     _RT_KIND_AUTH_PROFILES, KIND_CLASSES, ADAPTERS). Pure: no socket, no clock.
  6. The outbound transport and the credentials it presents -- the generated OAuth region (from
     Scripts/_mcp_oauth.py), the OAuth provider rows, _RtTokenStore, the config lock, the
     hand-copied address classifier, _rt_resolve, _rt_open_socket, _RtHttpConnection /
     _RtHttpsConnection, _rt_send(). The only code that opens an outbound socket.
  7. The HTTP front -- generated from Scripts/_mcp_httpfront.py (ADR 0029) plus the router's declared
     adaptations: _RouterHttpServer, _RouterHandler, the upstream pump (_rt_pump) and the relay loops.
  8. Entry point -- argparse, main(), signal handling and shutdown, and the login subcommand.

Units 2-5 reference no socket, ssl, http, select, time or threading name: every
module is imported at the top of the file, and the rule is on references.

llama.cpp quirks -- delete a row when its upstream fix is the minimum supported build
--------------------------------------------------------------------------------------
  Each row is (name, upstream ref, fn) in LLAMACPP_REQUEST_QUIRKS or
  LLAMACPP_EVENT_QUIRKS; a route turns rows off by name with quirks_off. The
  issue numbers are unverified.
    request  tool-results-first  llama.cpp #29482         tool_result blocks first in a user message
    request  hoist-system        llama.cpp #27367         list system -> one "\\n\\n"-joined string
    request  adaptive-thinking   no adaptive type         adaptive -> enabled, budget clamped
    request  sampling-override   llama.cpp #27893         route temperature/top_p/top_k win
    event    tool-use-input      llama.cpp #22960         tool_use block start gets "input": {}
    event    error-shape         llama.cpp error format   {code,message,type} -> Anthropic envelope

The login subcommand, and where the tokens live
-----------------------------------------------
  llm-router.py login --config FILE --backend NAME [--device] [--open-browser]
                      [--timeout S] [--debug] [--log-file PATH]
  logs in one backend whose profile uses OAuth (kind codex, or openai with an
  "oauth" object): the browser flow with a loopback redirect listener (codex on
  127.0.0.1:1455) or a pasted redirect URL, or --device (codex only). The
  refresh token, account id, expiry and, for openai, the issued client id and
  host id are written into that backend's backends.<n>.oauth object of the
  same 0600 config; the access and ID tokens are never written. The running
  router refreshes on demand and writes each rotation back the same way: the
  config directory is checked first (owned by you, not group- or
  other-writable), every file operation is relative to it, <config>.lock is
  flocked by the router and login alike, only backends.<n>.oauth is merged, and
  a temp file is renamed over the config. A login that lands on disk while the
  router refreshes the old grant is adopted, never overwritten.

Exit codes
----------
  0  normal exit, including a signal-initiated shutdown (SIGTERM / SIGINT);
     for login, the refresh token was written.
  2  a config, command-line or bind refusal, or a failed login: one
     `llm-router: <msg>` line on stderr. The message names the field and the
     rule, never the value.

Declared limits (not fixed; ADR 0028 carries the full list, and its 2026-10-07
addendum the limits of the codex/openai kinds, OAuth and Mistral reasoning)
------------------------------------------------------------------------------
  * Host is unchecked under --allow-remote; the bearer token is the boundary.
  * No TLS on the inbound side: under --allow-remote the bearer travels in
    plaintext, so a non-loopback bind belongs on a VPN interface only.
  * Passthrough trusts the backend's bytes within the line/event bounds.
  * create_default_context() honours SSL_CERT_FILE / SSL_CERT_DIR when a
    backend sets no ca_file.
  * There is no failed-auth throttling: a wrong bearer costs one 401 and a
    reconnect, bounded only by --max-connections, and each 401/403 writes one
    WARNING line, so log volume grows with an unauthenticated peer's request
    rate. Token strength is checked by length, charset and distinct
    characters only (_TOKEN_MIN_LEN, _TOKEN_MIN_DISTINCT), never by entropy.
  * The shorter pre-auth header bound (_HTTP_PREAUTH_TIMEOUT_S) is a
    mitigation: a peer that reconnects at once can still hold every slot.
  * Authenticated worst-case memory is roughly connections x (body + its
    translated copy + the larger of the pump queue (_RT_QUEUE_BYTES) and a
    non-stream upstream answer with its decoded text (2 x
    _UPSTREAM_BODY_LIMIT)), plus the parsed JSON objects, several times the
    bytes: about 6 GiB of bytes alone at the defaults.
  * A resolver that misses the connect deadline is abandoned, not stopped:
    its daemon thread lives until the OS resolver gives up.
  * The ready file is removed on a clean shutdown only, and only while it
    holds this pid (a replace between the check and the unlink is not seen).
  * GET /v1/models does not paginate: limit, before_id and after_id are
    accepted and ignored, the whole list is returned with has_more false, and
    every entry's created_at is the router's start time. A route's
    max_input_tokens / max_tokens are advertised there, never enforced.
  * codex sends the omp client's identity to the ChatGPT backend: a
    terms-of-service risk accepted by the user, measured from one omp release.
  * The ID token's claims are checked, its signature is not; the OAuth
    provider rows are static (no discovery).
  * Token persistence has windows: a crash or a timed-out refresh after the
    provider rotated, a shutdown while its write waits on the config flock
    (the wait gives up on `closing`), or a refresh answer that fails its
    checks (its rotated token is discarded), can need a re-login; a hand edit between the writer's
    re-check and its rename can be lost; the flock serializes the router and
    login only, and non-POSIX is unsupported.
  * The per-backend refresh lock is held across the token POST and the write;
    a waiter gives up with 503 after connect_timeout + _RT_OAUTH_IDLE_S. A
    shutdown interrupts a token POST from its connect on; the resolve and the
    TCP connect themselves are not interruptible (connect_timeout bounds them,
    and the drain may abandon such a handler).
  * The reasoning signatures (lrs1., lms1.) are plain, not MAC'd; renaming a
    backend loses reasoning continuity.
  * reasoning_effort_map and effort learning are mistral only; learned
    sampling and effort sets last as long as the process.
  * A non-stream answer's dropped_thinking is not on the req line.
"""

import argparse
import base64
import collections
import fcntl
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import os
import queue
import re
import secrets
import select
import signal
import socket
import socketserver
import ssl
import stat
import string
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    NamedTuple,
    NoReturn,
    Optional,
    Tuple,
    Type,
    Union,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("llm-router")


# Every client-chosen structural value (model, method, kind, argument keys) reaches a
# log line through _log_value: a line break in one would forge a line (CWE-117, R-0070).
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_logging.py :: _LOG_VALUE_WIDTH, _LOG_KEYS_SHOWN, _log_value
_LOG_VALUE_WIDTH = 80


_LOG_KEYS_SHOWN = 16


def _log_value(value: Any) -> str:
    """A peer-chosen STRUCTURAL value as a log line may carry it (CWE-117).

    For a method, an id, a tool name or the argument keys. A str is cut to
    _LOG_VALUE_WIDTH characters before repr(), so every control character is
    escaped, then capped at 4 * _LOG_VALUE_WIDTH; "..." follows whenever
    either cut happened. An int or None as itself (a huge int as its bit
    length); a list of keys item by item, at most _LOG_KEYS_SHOWN, one level
    deep; anything else as its type name. Never handed a payload VALUE (ADR
    0011). `Scripts/_mcp_logging.py` says why the block is here.
    """
    if isinstance(value, str):
        cut = len(value) > _LOG_VALUE_WIDTH
        r = repr(value[:_LOG_VALUE_WIDTH] if cut else value)
        text = r[:4 * _LOG_VALUE_WIDTH]
        return text + "..." if (cut or len(r) > 4 * _LOG_VALUE_WIDTH) else text
    if value is None or isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value) if value.bit_length() <= 64 else "int(%d bits)" % value.bit_length()
    if isinstance(value, list):
        shown = [type(item).__name__ if isinstance(item, list) else _log_value(item) for item in value[:_LOG_KEYS_SHOWN]]
        if len(value) > _LOG_KEYS_SHOWN:
            shown.append("+%d more" % (len(value) - _LOG_KEYS_SHOWN))
        return "[" + ", ".join(shown) + "]"
    return type(value).__name__
# END GENERATED: c1f9ba374621


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_logging.py :: _configure_logging
def _configure_logging(debug, log_file):
    """DEBUG to *log_file* (mode 0600) or to stderr, else WARNING. Never stdout.

    Either flag enables DEBUG: `--log-file` does not redirect the log, it turns
    it on. `Scripts/_mcp_logging.py` carries the rest -- why the 0600 pair needs
    both calls, and what deliberately stays out of this block.
    """
    level = logging.DEBUG if (debug or log_file) else logging.WARNING
    handlers = []
    if log_file:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a")))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
# END GENERATED: f77d402d6254


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# No name below may match tests/test_mcp_footprint.py's CAP_CONST_RX (a MAX_
# stem): inbound and stream bounds use a _LIMIT / _RANGE / _CAP stem instead.

# Default --body-limit: one inbound request body (G-M1).
_ROUTER_BODY_LIMIT = 32 * 1024 * 1024
# Accepted --body-limit values.
_BODY_LIMIT_RANGE = (1 << 20, 256 << 20)
_HTTP_CONNECTION_CAP = 32         # default --max-connections
_CONNECTION_CAP_RANGE = (1, 1024)  # accepted --max-connections values
_HTTP_HEADER_TIMEOUT_S = 10.0     # TOTAL bound on one request line + headers, and per-recv until auth
# TOTAL header bound while the connection has never authenticated (V3): an
# unauthenticated socket gives its slot back sooner; never above the header bound.
_HTTP_PREAUTH_TIMEOUT_S = 5.0
_HTTP_SOCKET_TIMEOUT_S = 30.0     # per-recv socket timeout after auth
_HTTP_BODY_TIMEOUT_S = 60.0       # TOTAL bound on reading one request body, after auth (V4)
_TOKEN_MIN_LEN = 32               # minimum router bearer token length (characters)
_TOKEN_MIN_DISTINCT = 8           # minimum distinct characters in the router bearer token (V1)
_API_KEY_MIN_LEN = 16             # minimum backend api_key length (characters, V15)
_RT_MAX_TOKENS_LIMIT = 1000000    # largest inbound max_tokens accepted (V19)
_PING_INTERVAL_S = 15.0           # SSE `event: ping` interval while a stream waits
_CONNECT_TIMEOUT_S = 10.0         # default backend connect_timeout: ONE deadline over resolve + connect + TLS (V9)
_IDLE_TIMEOUT_S = 300.0           # default backend idle_timeout (per upstream read)
_TIMEOUT_CEILING_S = 3600.0       # upper bound accepted for any configured timeout
_SSE_LINE_LIMIT = 1024 * 1024     # one upstream SSE line (G-M45 default)
_SSE_EVENT_LIMIT = 2 * 1024 * 1024  # one upstream SSE event (G-M45 default)
_RT_QUEUE_BYTES = 16 * 1024 * 1024  # pump queue bound, counted in bytes (NFR-2b)
_RT_BUFFERED_TOOL_LIMIT = 8 * 1024 * 1024  # Mistral tool ids, names and arguments buffered per stream (V33)
_RT_BUFFERED_TOOL_COUNT = 128     # Mistral tool calls buffered per stream (V33)
_RT_TICK_S = 1.0                  # relay loop wake-up: ping, idle and closing checks
_PUMP_JOIN_S = 2.0                # handler's bound on joining the upstream pump
_DRAIN_S = 3.0                    # shutdown drain for in-flight streams
_CA_FILE_LIMIT = 1024 * 1024      # a backend's ca_file size bound
_UPSTREAM_BODY_LIMIT = 64 * 1024 * 1024  # one non-stream upstream body
# F45: the largest upstream 400 body parsed for a learn (an unsupported sampling name, a
# supported reasoning_effort set); a real refusal is a few hundred bytes. A larger one is
# answered as any other 400, never learned from.
_RT_LEARN_BODY_LIMIT = 64 * 1024
_ERROR_TEXT_WIDTH = 300           # upstream error text relayed to the client, cut here
# _LOG_VALUE_WIDTH / _LOG_KEYS_SHOWN: generated with _log_value from _mcp_logging.py (R-0070).
_MINTED_PREFIX = "toolu_lr"       # prefix of tool-use ids the router mints for Mistral
_RT_SCRUB_DYNAMIC_CAP = 64        # unpinned runtime secrets the scrubber holds (KD-13)
# A JWT: base64url header "eyJ...", payload, optional signature. Its first 8 characters are
# the public '{"alg' header every JWT shares, so it gets no prefix form (KD-13).
_RT_JWT_SHAPE_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


# ---------------------------------------------------------------------------
# Unit 1: errors
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """A config or command-line refusal; the message names the field, never the value. Exit 2."""


class ConfigConflict(ConfigError):
    """The config file changed shape under the writer; nothing was written."""


class ApiError(Exception):
    """An Anthropic-shaped refusal (status, error type, message; no secret) -> an error envelope."""

    def __init__(self, status: int, err_type: str, message: str):
        super().__init__(message)
        self.status = status
        self.err_type = err_type
        self.message = message


class UpstreamError(Exception):
    """A backend transport failure, one line, status 502 | 504 -> an error envelope or `event: error`."""

    def __init__(self, status: int, err_type: str, message: str):
        super().__init__(message)
        self.status = status
        self.err_type = err_type
        self.message = message


# KD-14: the status the router answers -> its Anthropic error type. 414, 431 and
# 505 are raised by the stdlib's own send_error, which the router overrides (M9).
_STATUS_TO_TYPE = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    405: "invalid_request_error",
    411: "invalid_request_error",
    413: "request_too_large",
    414: "invalid_request_error",
    415: "invalid_request_error",
    429: "rate_limit_error",
    431: "invalid_request_error",
    500: "api_error",
    501: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
    505: "invalid_request_error",
    529: "overloaded_error",
}

# KD-14: an upstream non-2xx status -> the router's status; anything else maps to
# 502 at the call site (.get(status, 502)). 401/403 -> 502 is KD-8: an upstream
# credential rejection is the router's fault, never the client's.
_UPSTREAM_STATUS = {
    400: 400, 401: 502, 403: 502, 404: 404, 413: 413, 422: 400,
    429: 429, 500: 502, 502: 529, 503: 529, 504: 529, 529: 529,
}

# KD-14: the upstream `error.type` values a passthrough relay may keep; a type
# outside the set is re-derived from the status. billing_error and timeout_error
# come from the public Anthropic error list, not from a measurement.
_ANTHROPIC_ERROR_TYPES = frozenset({
    "invalid_request_error", "authentication_error", "billing_error",
    "permission_error", "not_found_error", "request_too_large",
    "rate_limit_error", "api_error", "timeout_error", "overloaded_error",
})


def _rt_error_body(err_type: str, message: str) -> dict:
    """The Anthropic error envelope every error answer carries."""
    return {"type": "error", "error": {"type": err_type, "message": message}}


def _rt_dumps(obj: Any) -> bytes:
    """Compact JSON as ASCII bytes. ensure_ascii=True turns a lone surrogate
    into a \\udXXX escape instead of raising on encode (J21); allow_nan=False
    raises ValueError on NaN/Infinity rather than emit a token JSON lacks (V19)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _rt_no_constant(name: str) -> NoReturn:
    """json parse_constant: NaN, Infinity and -Infinity are not JSON (V19)."""
    raise ValueError("non-finite number %s" % name)


def _rt_finite_float(text: str) -> float:
    """json parse_float: a literal that overflows to an infinity (1e999) is refused like NaN (V19)."""
    value = float(text)
    if abs(value) == float("inf"):
        raise ValueError("non-finite number")
    return value


# One JSON integer literal, in characters (sign included): CPython 3.11's own
# int_max_str_digits default, the bound 3.9.6 lacks -- int() is quadratic in the
# literal's length, so a body of digits would otherwise pin a core (F9).
_RT_INT_LITERAL_LIMIT = 4300


def _rt_bounded_int(text: str) -> int:
    """json parse_int: a literal over _RT_INT_LITERAL_LIMIT characters is refused before int() sees it (F9)."""
    if len(text) > _RT_INT_LITERAL_LIMIT:
        raise ValueError("integer literal too long")
    return int(text)


def _rt_unique_pairs(pairs: List[Tuple[str, Any]]) -> dict:
    """json object_pairs_hook: an object whose key appears twice is a ValueError (F10).

    json.loads keeps the last value of a repeated key; two values for one key are
    an ambiguity refused rather than resolved (ADR 0015), as load_config refuses
    one. The key is not named: a body's keys are client or upstream text.
    """
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _rt_loads(text: Union[str, bytes]) -> Any:
    """json.loads for every inbound and upstream body: a non-finite number, an
    over-long integer literal or a key repeated in one object is a ValueError, so
    each caller's malformed-JSON path answers it (V19, F9, F10)."""
    return json.loads(text, object_pairs_hook=_rt_unique_pairs, parse_constant=_rt_no_constant,
                      parse_float=_rt_finite_float, parse_int=_rt_bounded_int)


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_httpfront.py :: _http_token_value
def _http_token_value(value: str, where: str, min_len: int, error) -> bytes:
    """Validate the bearer token and return it as ASCII bytes; refusals never include it.

    *min_len* and the *error* class raised are the host's policy, passed in."""
    if len(value) < min_len:
        raise error(f"{where}: the bearer token must be at least {min_len} characters")
    if not all(0x21 <= ord(ch) <= 0x7E for ch in value):
        raise error(f"{where}: the bearer token must be printable ASCII with no whitespace")
    return value.encode("ascii")
# END GENERATED: 352c54a20b22


def _rt_secret_forms(raw: bytes) -> FrozenSet[str]:
    """Every scrubbed form of one secret (KD-9): the value itself, its URL-encoding, its
    standard and URL-safe base64 (padded and unpadded) and, when it is at least 16
    characters, its first and last 8 characters -- a backend may echo any of them in an
    error text the router relays. ONE exception (KD-13): a JWT-shaped value
    (_RT_JWT_SHAPE_RE.fullmatch) gets no first-8 form -- every JWT starts with the base64
    of the public '{"alg' header -- while its last-8 form is kept. Never an empty form."""
    s = raw.decode("ascii")
    std = base64.b64encode(raw).decode("ascii")
    safe = base64.urlsafe_b64encode(raw).decode("ascii")
    forms = {s, urllib.parse.quote(s, safe=""), std, std.rstrip("="), safe, safe.rstrip("=")}
    if len(s) >= 16:
        if _RT_JWT_SHAPE_RE.fullmatch(s) is None:
            forms.add(s[:8])
        forms.add(s[-8:])
    forms.discard("")
    return frozenset(forms)


class _RtScrubber:
    """scrub(text): every known secret form -> "[redacted]", then printable, bounded (KD-9).

    The secrets are the load-time ones (never evicted), the pinned current tokens, and at
    most _RT_SCRUB_DYNAMIC_CAP unpinned runtime secrets (KD-13). Forms are replaced
    longest first, so a short form never splits a longer one before that one is found;
    then every non-printable character becomes "?" and the text is cut to
    _ERROR_TEXT_WIDTH. add()/pin() never mutate in place: they build NEW _dynamic/_pins/
    _ordered objects and rebind them, _ordered last; __call__ reads _ordered once.
    Writers must be serialized by the CALLER (unit 6, KD-13/NEW-1); unit 1 stays pure and
    serializes nothing itself. Never logged or formatted: the repr is "<fn>".

    READ ORDER (R3-M1), binding for both add() and pin(): every container is read ONCE,
    up front, into locals -- before the first _rt_secret_forms call; the new containers
    are built from those locals only (pin() shares _grown with add() and never calls
    self.add(), which would re-read self._dynamic); then _dynamic, _pins and, last,
    _ordered are rebound.
    """

    __slots__ = ("_static", "_dynamic", "_pins", "_ordered")

    def __init__(self, secrets: Tuple[bytes, ...]) -> None:
        static = {}
        for raw in secrets:
            if raw and raw not in static:
                static[raw] = _rt_secret_forms(raw)
        self._static = static        # raw -> forms; never rebound after this
        self._dynamic = {}           # raw -> forms, oldest first; pinned values included
        self._pins = {}              # key -> frozenset of the raws currently pinned under it
        self._ordered = self._order(static, self._dynamic)

    @staticmethod
    def _order(static: Mapping[bytes, FrozenSet[str]],
               dynamic: Mapping[bytes, FrozenSet[str]]) -> Tuple[str, ...]:
        """The union of every form, longest first."""
        forms = set()
        for group in (static, dynamic):
            for value_forms in group.values():
                forms.update(value_forms)
        return tuple(sorted(forms, key=len, reverse=True))

    def _grown(self, dynamic: Mapping[bytes, FrozenSet[str]], pinned: FrozenSet[bytes],
               raws: Iterable[bytes]) -> Dict[bytes, FrozenSet[str]]:
        """A NEW dynamic map from the *dynamic* snapshot: each raw (not empty, not static)
        moved to newest, its forms reused when already held; then, past the cap, the oldest
        values outside *pinned* are evicted -- never a static, never a pinned one."""
        grown = dict(dynamic)
        for raw in raws:
            if not raw or raw in self._static:
                continue
            forms = grown.pop(raw, None)
            grown[raw] = forms if forms is not None else _rt_secret_forms(raw)
        unpinned = [raw for raw in grown if raw not in pinned]
        for raw in unpinned[:max(0, len(unpinned) - _RT_SCRUB_DYNAMIC_CAP)]:
            del grown[raw]
        return grown

    @staticmethod
    def _pinned(pins: Mapping[str, FrozenSet[bytes]]) -> FrozenSet[bytes]:
        return frozenset(raw for raws in pins.values() for raw in raws)

    def add(self, raw: bytes) -> None:
        """Redact *raw* (and every form of it) from now on; a no-op for an empty or a
        load-time value; an already-held value moves to newest."""
        if not raw or raw in self._static:
            return
        pins = self._pins            # R3-M1: snapshot first, before any _rt_secret_forms call
        dynamic = self._dynamic
        grown = self._grown(dynamic, self._pinned(pins), (raw,))
        self._dynamic = grown
        self._ordered = self._order(self._static, grown)   # last

    def pin(self, key: str, raws: Tuple[bytes, ...]) -> None:
        """Pin *raws* as the current secrets under *key* (a backend name, or "login"): they
        are never evicted while pinned. The values the new pin replaces stay as ordinary,
        evictable dynamic secrets."""
        pins = self._pins            # R3-M1: snapshot first, before any _rt_secret_forms call
        dynamic = self._dynamic
        new_pins = {**pins, key: frozenset(raw for raw in raws if raw)}
        grown = self._grown(dynamic, self._pinned(new_pins), raws)
        self._dynamic = grown
        self._pins = new_pins
        self._ordered = self._order(self._static, grown)   # last

    def __call__(self, text: str) -> str:
        ordered = self._ordered
        text = str(text)
        for form in ordered:
            text = text.replace(form, "[redacted]")
        # The strip is one character for one, so cutting first gives the same text.
        return "".join(c if c.isprintable() else "?" for c in text[:_ERROR_TEXT_WIDTH])

    def __repr__(self) -> str:
        return "<fn>"


def _rt_make_scrubber(secrets: Tuple[bytes, ...]) -> Callable[[str], str]:
    """Build scrub(text) over the load-time *secrets*: an _RtScrubber, which also has
    .add(raw) and .pin(key, raws) for runtime secrets (KD-9, KD-13). Pure: base64 and
    urllib.parse only."""
    return _RtScrubber(secrets)


# ---------------------------------------------------------------------------
# Unit 2: config
# ---------------------------------------------------------------------------

# A key-path segment shown as itself; any other key is shown as its _log_value repr.
_RT_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PEM_CERT_MARKER = "-----BEGIN CERTIFICATE-----"

# The backend kinds (_KINDS, RESERVED_KINDS, _RT_KIND_AUTH_PROFILES) derive from the one
# kind registry, _RT_KIND_TABLE in unit 5; the loader reads them at call time.


def _rt_redacted_repr(obj: tuple, shown: Mapping[str, str]) -> str:
    """NamedTuple-shaped repr of *obj* with every field named in *shown* printed as its fixed text.

    The secret-bearing fields are never formatted: their value is not even read.
    """
    parts = []
    for field in obj._fields:
        text = shown.get(field)
        parts.append("%s=%s" % (field, text if text is not None else repr(getattr(obj, field))))
    return "%s(%s)" % (type(obj).__name__, ", ".join(parts))


class _OAuthSeedBase(NamedTuple):
    refresh_token: Optional[str]    # SECRET; None = never logged in
    account_id: Optional[str]
    expires_at: Optional[int]       # informational
    client_id: Optional[str]        # openai: the issued oaiapp_ id; codex: None (the provider row's)
    host_id: Optional[str]          # openai: urn:uuid:<v4>; codex: None


class OAuthSeed(_OAuthSeedBase):
    """A backend's load-time oauth object (live state is the token store's). `refresh_token` is a
    secret: its repr is `<redacted>` (I6)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return _rt_redacted_repr(self, {"refresh_token": "<redacted>"})


class _BackendSpecBase(NamedTuple):
    name: str
    kind: str
    scheme: str                 # "https" | "http"
    host: str
    port: int
    base_path: str              # "" or "/prefix" without a trailing slash
    allow_private: bool         # default False for EVERY kind; never admits link-local, the metadata addresses in
                                # _RT_PRIVATE_REFUSED_NETS, Teredo, multicast or reserved
    allow_loopback: bool        # default False; valid only with allow_private (same-host llama-server, test peers)
    ca_file: Optional[str]      # PEM path as configured (shown in refusals; never reopened)
    ca_pem: Optional[str]       # the PEM text read once from the vetted descriptor; replaces the system trust store (KD-18)
    api_key: Optional[str]      # SECRET: never logged, formatted or repr'd
    auth_header: str            # "authorization" | "x-api-key" | "none"
    forward_headers: FrozenSet[str]  # subset of the kind's FORWARDABLE (default DEFAULT_FORWARD)
    connect_timeout: float
    idle_timeout: float
    # Trailing, defaulted: every existing constructor keeps working.
    profile: str = ""                   # "" | a _RT_RESPONSES_PROFILES key (codex, openai-oauth, openai-apikey)
    oauth: Optional[OAuthSeed] = None   # load-time seed; the live state is the token store's
    identity: Tuple[Tuple[str, str], ...] = ()    # backend overrides of _RT_CODEX_IDENTITY keys
    auth_base: Optional[Tuple[str, str, int, str]] = None   # auth_base_url; None = the provider row's host


class BackendSpec(_BackendSpecBase):
    """One validated backend. `api_key` and `oauth` hold secrets: their repr is `<redacted>` (I6)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return _rt_redacted_repr(self, {"api_key": "<redacted>", "oauth": "<redacted>"})


class RouteSpec(NamedTuple):
    name: str                   # the routes key, or "(default)" for the fallback route; used in logs only
    backend: str
    model: str                  # upstream model id
    options: Dict[str, Any]     # validated by the kind's option validators
    display_name: Optional[str] = None   # GET /v1/models's display_name; None -> the route key
    max_input_tokens: Optional[int] = None   # advertised by GET /v1/models only (+ context_window)
    max_tokens: Optional[int] = None         # advertised by GET /v1/models only; never enforced


class _RouterConfigBase(NamedTuple):    # from --config (routing + secrets)
    token: bytes                # SECRET
    backends: Dict[str, BackendSpec]
    routes: Dict[str, RouteSpec]
    default: Optional[RouteSpec]
    secrets: Tuple[bytes, ...]  # every secret, for the outbound scrubber (KD-9)
    scrub: Callable[[str], str]  # _rt_make_scrubber(secrets), built once by load_config (unit 1, KD-9)
    # scrub is an _RtScrubber: besides the call it also has .add(raw) and .pin(key, raws) (KD-13).
    source_digest: str = ""     # sha256 hex of the text load_config parsed (the write-back's re-check)


class RouterConfig(_RouterConfigBase):
    """The validated config. token and secrets are redacted in repr; scrub (a closure over every
    secret form) prints as `<fn>` (I6)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return _rt_redacted_repr(self, {"token": "<redacted>", "secrets": "<redacted>",
                                        "scrub": "<fn>"})


class RouterSettings(NamedTuple):   # from the command line (network)
    bind: str
    port: int
    allow_remote: bool
    origins: FrozenSet[str]
    body_limit: int
    max_connections: int
    ready_file: Optional[str]
    debug: bool
    log_file: Optional[str]


class _DupKey:
    """A duplicate JSON object key found by _rt_no_duplicate_keys (R5, ADR 0015).

    The hook cannot see where it sits in the document, so instead of raising it
    RETURNS this marker in place of the object; every enclosing object's hook
    passes the marker up and records its own key (or the list index that held
    it), so the path is complete by the time the outermost object is built.
    load_config must check `isinstance(data, _DupKey)` and raise data.refusal().
    The marker holds the key and the path only -- never a value.
    """

    __slots__ = ("key", "path")

    def __init__(self, key: str):
        self.key = key
        self.path: List[Union[str, int]] = []      # innermost segment first

    def refusal(self) -> ConfigError:
        """`<path>: duplicate key '<k>'` -- `config:` at the top level. Never a value."""
        parts: List[str] = []
        for seg in reversed(self.path):
            if isinstance(seg, int):
                parts.append("[%d]" % seg)
            else:
                shown = seg if _RT_PATH_SEGMENT_RE.fullmatch(seg) else _log_value(seg)
                parts.append(shown if not parts else "." + shown)
        where = "".join(parts) if parts else "config"
        return ConfigError("%s: duplicate key %s" % (where, _log_value(self.key)))


def _rt_find_dup(value: Any) -> Optional[_DupKey]:
    """The _DupKey inside *value* (itself, or nested in lists only), its list indices recorded.

    An object inside a list was already collapsed to a dict or a marker by its own
    hook call, so only lists are walked -- iteratively, never by recursion.
    """
    if isinstance(value, _DupKey):
        return value
    if not isinstance(value, list):
        return None
    stack: List[Tuple[list, List[int]]] = [(value, [])]
    while stack:
        items, indices = stack.pop()
        for index, item in enumerate(items):
            if isinstance(item, _DupKey):
                for seg in reversed(indices + [index]):
                    item.path.append(seg)
                return item
            if isinstance(item, list):
                stack.append((item, indices + [index]))
    return None


def _rt_no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Union[dict, _DupKey]:
    """json object_pairs_hook: a dict, or a _DupKey marker naming the repeated key and its path.

    json.loads silently keeps the last value of a repeated key (R5); a config
    whose two values for one key differ is ambiguous, and ambiguity is refused
    (ADR 0015). Every depth is covered: the marker travels up through every
    enclosing object. The key is shown, its values never.
    """
    out: Dict[str, Any] = {}
    for key, value in pairs:
        found = _rt_find_dup(value)
        if found is not None:
            found.path.append(key)
            return found
        if key in out:
            return _DupKey(key)
        out[key] = value
    return out


def _rt_read_config_file(path: str, dir_fd: Optional[int] = None) -> str:
    """Read the config file after checking the opened file (P3, the stricter model).

    O_NOFOLLOW refuses a symlink; the checks run on the opened descriptor
    (fstat), so the inode checked is the inode read. The config holds secrets
    (auth_token, api_key), so it must be the caller's and 0600-tight. Every
    refusal names the rule, never the content. dir_fd (the write-back's checked
    directory, KD-10) makes the open relative to that directory.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except OSError as exc:
        raise ConfigError(f"--config: cannot open the config file ({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError("--config: not a regular file")
        if st.st_uid != os.geteuid():
            raise ConfigError("--config: refusing a config file not owned by the current user")
        if st.st_mode & 0o077:
            raise ConfigError("--config: refusing a config file readable or writable by group/others "
                              "(chmod 600)")
        chunks: List[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise ConfigError(f"--config: cannot read the config file ({type(exc).__name__})") from None
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("--config: the config file is not valid UTF-8") from None


def _rt_read_ca_file(path: Any, where: str) -> str:
    """Vet a backend's ca_file as a trust anchor and return its PEM text (KD-18).

    Absolute path; O_NOFOLLOW; fstat on the descriptor: a regular file owned by
    the current user or root, with no group/other WRITE bit (a CA is public, so
    0644 is fine), at most _CA_FILE_LIMIT bytes. The text is read from THAT
    descriptor, once, and never reopened: the PEM the TLS context loads is the
    file that was checked. *where* is `backends.<name>.ca_file`.
    """
    if not isinstance(path, str) or not path:
        raise ConfigError(f"{where}: must be a non-empty string")
    if not os.path.isabs(path):
        raise ConfigError(f"{where}: must be an absolute path")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ConfigError(f"{where}: cannot open the CA file ({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError(f"{where}: not a regular file")
        if st.st_uid not in (os.geteuid(), 0):
            raise ConfigError(f"{where}: refusing a CA file not owned by the current user or root")
        if st.st_mode & 0o022:
            raise ConfigError(f"{where}: refusing a CA file writable by group/others (chmod 644)")
        if st.st_size > _CA_FILE_LIMIT:
            raise ConfigError(f"{where}: the CA file is larger than {_CA_FILE_LIMIT} bytes")
        chunks: List[bytes] = []
        total = 0
        while total <= _CA_FILE_LIMIT:          # the file may grow after fstat: bound the read too
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise ConfigError(f"{where}: cannot read the CA file ({type(exc).__name__})") from None
    finally:
        os.close(fd)
    if total > _CA_FILE_LIMIT:
        raise ConfigError(f"{where}: the CA file is larger than {_CA_FILE_LIMIT} bytes")
    try:
        text = b"".join(chunks).decode("ascii")
    except UnicodeDecodeError:
        raise ConfigError(f"{where}: the CA file is not ASCII PEM") from None
    if _PEM_CERT_MARKER not in text:
        raise ConfigError(f"{where}: the CA file holds no PEM certificate")
    return text


def _rt_cfg_timeout(value: Any, default: float, where: str) -> float:
    """A number of seconds (bool refused) with 0 < x <= _TIMEOUT_CEILING_S, else ConfigError (P6b)."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: must be a number of seconds")
    if not 0 < value <= _TIMEOUT_CEILING_S:
        raise ConfigError(f"{where}: must be > 0 and <= {_TIMEOUT_CEILING_S:g}")
    return float(value)


# Route option validators: each is (value, where) -> the validated value, and
# raises ConfigError naming *where* and the rule, never the value.
_RtOptionValidator = Callable[[Any, str], Any]


def _rt_opt_int(lo: int, hi: Optional[int] = None) -> _RtOptionValidator:
    """A validator for an int (bool refused) with lo <= x (<= hi when hi is given)."""
    rule = f"an integer from {lo} to {hi}" if hi is not None else f"an integer >= {lo}"

    def check(value: Any, where: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: must be {rule}")
        if value < lo or (hi is not None and value > hi):
            raise ConfigError(f"{where}: must be {rule}")
        return value

    return check


def _rt_opt_enum(choices: Tuple[str, ...]) -> _RtOptionValidator:
    """A validator for a str that is exactly one of *choices*; the refusal lists the choices, not the value."""
    rule = "one of " + ", ".join(choices)

    def check(value: Any, where: str) -> str:
        if not isinstance(value, str) or value not in choices:
            raise ConfigError(f"{where}: must be {rule}")
        return value

    return check


def _rt_opt_float(lo: float, hi: float) -> _RtOptionValidator:
    """A validator for a number (bool refused) with lo <= x <= hi, returned as float."""
    rule = f"a number from {lo:g} to {hi:g}"

    def check(value: Any, where: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}: must be {rule}")
        if not lo <= value <= hi:                  # NaN fails both comparisons
            raise ConfigError(f"{where}: must be {rule}")
        return float(value)

    return check


def _rt_opt_quirks(value: Any, where: str) -> Tuple[str, ...]:
    """quirks_off: a list of known llama.cpp quirk names (KD-13); an unknown name is refused by name."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{where}: must be a list of quirk names")
    # _RT_LL_QUIRK_NAMES is derived from the two registries in unit 5; this
    # validator only runs at load time, so the global is read at call time.
    known = _RT_LL_QUIRK_NAMES
    for item in value:
        if item not in known:
            raise ConfigError(f"{where}: unknown quirk {_log_value(item)}")
    return tuple(value)


_RT_TEMPERATURE = _rt_opt_float(0.0, 2.0)
_RT_TOP_P = _rt_opt_float(0.0, 1.0)

# Each kind's header table (KD-3) and option list live as class attributes on its
# adapter class in unit 5; the loader reads them through KIND_CLASSES at call time.

_RT_TOP_KEYS = frozenset({"auth_token", "backends", "routes", "default"})
_RT_BACKEND_KEYS = frozenset({"kind", "base_url", "allow_private", "allow_loopback", "ca_file",
                              "api_key", "auth_header", "forward_headers", "connect_timeout",
                              "idle_timeout", "allow_cleartext_api_key", "oauth", "identity",
                              "auth_base_url"})
_RT_ROUTE_KEYS = frozenset({"backend", "model", "options", "display_name",
                            "max_input_tokens", "max_tokens"})
_RT_ROUTE_LIMIT_KEYS = ("max_input_tokens", "max_tokens")   # a route's optional advertised caps
_RT_BACKEND_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")    # the _CHILD_NAME_RE shape
_RT_AUTH_HEADERS = ("authorization", "x-api-key", "none")
_RT_HOST_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")
_RT_URL_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_RT_MODEL_LIMIT = 256                     # a route key and an upstream model id, in characters
_RT_DISPLAY_NAME_LIMIT = 256              # a route's optional display_name, in characters
_RT_TOKEN_LIMIT_MIN = 1                   # a route's max_input_tokens / max_tokens, inclusive
_RT_TOKEN_LIMIT_MAX = 100_000_000
_RT_DEFAULT_PORTS = {"https": 443, "http": 80}

# The keys a backend's oauth object may hold, and the keys the write-back persists.
_RT_OAUTH_PERSIST_KEYS = ("refresh_token", "account_id", "expires_at", "client_id", "host_id")


# S5: the one header-value validator's rules. re.ASCII and explicit ranges, so a
# lone surrogate (a "\ud800" JSON escape) never matches and is refused by name at
# load, never a UnicodeEncodeError on the wire (L3/S10).
_RT_HEADER_VALUE_RULES = {
    "token": re.compile(r"[\x21-\x7e]{1,16384}", re.ASCII),    # == _oauth_token_ok's charset and cap
    "claim": re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII),    # chatgpt_account_id, residency
    "identity": re.compile(r"[\x20-\x7e]{1,256}", re.ASCII),   # KD-2 identity values
}


def _rt_header_value_ok(value: Any, rule: str) -> bool:
    """True iff *value* is a str that fully matches _RT_HEADER_VALUE_RULES[*rule*]."""
    return isinstance(value, str) and _RT_HEADER_VALUE_RULES[rule].fullmatch(value) is not None


def _rt_seg(key: str) -> str:
    """A config key as a path segment: itself when plain, else its bounded _log_value repr."""
    return key if _RT_PATH_SEGMENT_RE.fullmatch(key) else _log_value(key)


def _rt_unknown_keys(entry: dict, allowed: FrozenSet[str], where: str) -> None:
    """Refuse every key of *entry* outside *allowed*, by name (never a value)."""
    unknown = sorted(str(key) for key in entry if key not in allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(_rt_seg(k) for k in unknown)}")


def _rt_has_control(text: str) -> bool:
    """True when *text* holds a C0/C1 control character or DEL."""
    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _rt_cfg_bool(entry: dict, key: str, where: str) -> bool:
    """entry[key] as a JSON bool, default False; any other type is refused."""
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key}: must be true or false")
    return value


def _rt_is_loopback_host(host: str) -> bool:
    """True for `localhost` or a loopback IP literal (the base_url host as parsed); no resolution."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _rt_cfg_base_url(value: Any, where: str, field: str = "base_url") -> Tuple[str, str, int, str]:
    """(scheme, host, port, base_path) of a backend base_url; userinfo, query and fragment refused by name.

    urllib.parse is a pure string module (it reads no environment). The URL is
    never echoed: every refusal names the part and the rule. *field* is the key
    named in a refusal (base_url, or auth_base_url for the same rules, KD-19).
    """
    where = f"{where}.{field}"
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: must be a non-empty string")
    if _rt_has_control(value) or any(ch.isspace() for ch in value):
        raise ConfigError(f"{where}: must not contain whitespace or control characters")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise ConfigError(f"{where}: not a valid URL (scheme://host[:port][/path])") from None
    if parts.scheme not in ("http", "https"):
        raise ConfigError(f"{where}: the scheme must be http or https")
    if "@" in parts.netloc:
        raise ConfigError(f"{where}: userinfo (user:password@) is not allowed")
    if "?" in value:
        raise ConfigError(f"{where}: a query is not allowed")
    if "#" in value:
        raise ConfigError(f"{where}: a fragment is not allowed")
    host = parts.hostname or ""
    if not host:
        raise ConfigError(f"{where}: a host is required")
    if not _RT_HOST_RE.fullmatch(host):
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ConfigError(f"{where}: the host is not a valid host name or IP literal") from None
    if port is None:
        port = _RT_DEFAULT_PORTS[parts.scheme]
    elif port == 0:
        raise ConfigError(f"{where}: the port must be from 1 to 65535")
    path = parts.path.rstrip("/")
    if path and not all(_RT_URL_PATH_SEGMENT_RE.fullmatch(seg) for seg in path.split("/")[1:]):
        raise ConfigError(f"{where}: the path may hold only [A-Za-z0-9._~-] segments")
    return parts.scheme, host, port, path


# The issued OAuth client identity shapes (openai-oauth); ASCII-only, so a lone
# surrogate is refused by name (L3/S10).
_RT_OAUTH_CLIENT_ID_RE = re.compile(r"oaiapp_[A-Za-z0-9_-]{1,128}", re.ASCII)
_RT_OAUTH_HOST_ID_RE = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                                  re.ASCII)
# profile -> the oauth keys it refuses: codex uses its provider row's client id and sends no host id.
_RT_OAUTH_PROFILE_REFUSED: Dict[str, Tuple[str, ...]] = {"codex": ("client_id", "host_id")}


def _rt_cfg_profile(kind: str, has_api_key: bool, has_oauth: bool, where: str) -> str:
    """The backend's Responses profile name, looked up in _RT_KIND_AUTH_PROFILES (M5).

    A kind absent from the map has no profile (""). A kind with one auth mode
    requires it and refuses the other; a kind with both takes exactly one.
    """
    modes = _RT_KIND_AUTH_PROFILES.get(kind)
    if modes is None:
        return ""
    given = [mode for mode, on in (("api_key", has_api_key), ("oauth", has_oauth)) if on]
    if len(modes) == 1:
        (mode, profile), = modes.items()
        for other in given:
            if other != mode:
                raise ConfigError(f"{where}.{other}: kind {kind} authenticates with {mode}; "
                                  f"{other} is not allowed")
        if mode not in given:
            raise ConfigError(f"{where}.{mode}: required for kind {kind}")
        return profile
    if len(given) != 1:
        raise ConfigError(f"{where}: set exactly one of {' and '.join(sorted(modes))} (kind {kind})")
    return modes[given[0]]


def _rt_cfg_oauth(value: Any, where: str, profile: str, token: str) -> OAuthSeed:
    """backends.<n>.oauth as an OAuthSeed; `{}` is valid and means "not logged in".

    *where* is `backends.<n>`; *token* is the router's auth_token, which the
    refresh_token must differ from. Every refusal names the field and the rule.
    """
    where = f"{where}.oauth"
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be an object")
    _rt_unknown_keys(value, frozenset(_RT_OAUTH_PERSIST_KEYS), where)
    for key in _RT_OAUTH_PROFILE_REFUSED.get(profile, ()):
        if key in value:
            raise ConfigError(f"{where}.{key}: not allowed for profile {profile}")
    refresh_token = value.get("refresh_token")
    if refresh_token is not None:
        field = f"{where}.refresh_token"
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ConfigError(f"{field}: must be a non-empty string")
        if not _rt_header_value_ok(refresh_token, "token"):
            raise ConfigError(f"{field}: must be printable ASCII with no whitespace (the token rule)")
        if len(refresh_token) < _API_KEY_MIN_LEN:
            raise ConfigError(f"{field}: must be at least {_API_KEY_MIN_LEN} characters")
        # A plain comparison, as for api_key: both values come from the operator's own 0600 file.
        if refresh_token == token:
            raise ConfigError(f"{field}: must differ from auth_token")
    account_id = value.get("account_id")
    if account_id is not None and not _rt_header_value_ok(account_id, "claim"):
        raise ConfigError(f"{where}.account_id: must be 1 to 128 characters of [A-Za-z0-9_-]")
    expires_at = value.get("expires_at")
    if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, int)
                                   or expires_at < 0):
        raise ConfigError(f"{where}.expires_at: must be an integer >= 0")
    client_id = value.get("client_id")
    if client_id is not None and (not isinstance(client_id, str)
                                  or _RT_OAUTH_CLIENT_ID_RE.fullmatch(client_id) is None):
        raise ConfigError(f"{where}.client_id: must match {_RT_OAUTH_CLIENT_ID_RE.pattern}")
    host_id = value.get("host_id")
    if host_id is not None and (not isinstance(host_id, str)
                                or _RT_OAUTH_HOST_ID_RE.fullmatch(host_id) is None):
        raise ConfigError(f"{where}.host_id: must be a lower-case urn:uuid: version-4 UUID")
    return OAuthSeed(refresh_token=refresh_token, account_id=account_id, expires_at=expires_at,
                     client_id=client_id, host_id=host_id)


def _rt_cfg_identity(value: Any, where: str) -> Tuple[Tuple[str, str], ...]:
    """backends.<n>.identity: overrides of _RT_CODEX_IDENTITY keys, as pairs in the table's order.

    Each value passes the identity header-value rule (KD-2); *where* is `backends.<n>`.
    """
    where = f"{where}.identity"
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be an object")
    known = dict(_RT_CODEX_IDENTITY)             # unit 5, module global read at call time
    _rt_unknown_keys(value, frozenset(known), where)
    for key, item in value.items():
        if not _rt_header_value_ok(item, "identity"):
            raise ConfigError(f"{where}.{key}: must be 1 to 256 printable ASCII characters")
    return tuple((key, value[key]) for key in known if key in value)


def _rt_cfg_backend(name: str, entry: Any, token: str) -> BackendSpec:
    """Validate one backends.<name> entry and build its BackendSpec (Step 4).

    *token* is the router's auth_token: an api_key equal to it is refused (A-9).
    Every refusal names `backends.<name>.<field>` and the rule, never a value.
    """
    where = f"backends.{name}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be an object")
    _rt_unknown_keys(entry, _RT_BACKEND_KEYS, where)
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        known = ", ".join(k for k in _KINDS if k not in RESERVED_KINDS)
        raise ConfigError(f"{where}.kind: must be one of {known}")
    if kind in RESERVED_KINDS:
        raise ConfigError(f'{where}.kind: "{kind}" is reserved and not implemented')
    adapter_cls = KIND_CLASSES[kind]               # unit 5, module global read at call time
    forwardable, default_forward = adapter_cls.FORWARDABLE, adapter_cls.DEFAULT_FORWARD
    profile = _rt_cfg_profile(kind, entry.get("api_key") is not None, entry.get("oauth") is not None, where)
    row = _RT_RESPONSES_PROFILES[profile] if profile else None   # unit 5, read at call time
    if row is None:
        for key in ("oauth", "identity", "auth_base_url"):
            if key in entry:
                raise ConfigError(f"{where}.{key}: not allowed for kind {kind}")
    base_url = entry.get("base_url")
    if base_url is None and row is not None:
        base_url = row.default_base_url
    scheme, host, port, base_path = _rt_cfg_base_url(base_url, where)
    allow_private = _rt_cfg_bool(entry, "allow_private", where)
    if scheme == "http" and not allow_private:
        raise ConfigError(f"{where}.base_url: an http:// URL requires allow_private: true")
    allow_loopback = _rt_cfg_bool(entry, "allow_loopback", where)
    if allow_loopback and not allow_private:
        raise ConfigError(f"{where}.allow_loopback: requires allow_private")
    ca_file = entry.get("ca_file")
    ca_pem = None
    if ca_file is not None:
        if scheme == "http":
            raise ConfigError(f"{where}.ca_file: not allowed with an http:// base_url")
        ca_pem = _rt_read_ca_file(ca_file, f"{where}.ca_file")
    allow_cleartext = _rt_cfg_bool(entry, "allow_cleartext_api_key", where)
    api_key = entry.get("api_key")
    if api_key is not None:
        if not isinstance(api_key, str) or not api_key:
            raise ConfigError(f"{where}.api_key: must be a non-empty string")
        if not all(0x21 <= ord(ch) <= 0x7E for ch in api_key):
            raise ConfigError(f"{where}.api_key: must be printable ASCII with no whitespace")
        # V15: a short key gets no prefix/suffix scrub forms (KD-9) and would
        # redact every common substring it happens to match.
        if len(api_key) < _API_KEY_MIN_LEN:
            raise ConfigError(f"{where}.api_key: must be at least {_API_KEY_MIN_LEN} characters")
        # V14: an api_key over plain http:// is readable on the path; only a
        # loopback host, or the backend's explicit opt-in, may do that.
        if scheme == "http" and not allow_cleartext and not _rt_is_loopback_host(host):
            raise ConfigError(f"{where}.api_key: refusing to send it over a non-loopback http:// "
                              f"base_url (use https, or set allow_cleartext_api_key: true)")
        # A plain comparison on purpose: both values come from the operator's own
        # 0600 config at load time, so no peer can time it, and J1 pins the
        # file's one compare_digest to the bearer check in _precheck.
        if api_key == token:
            raise ConfigError(f"{where}.api_key: must differ from auth_token")
    elif kind == "mistral":
        raise ConfigError(f"{where}.api_key: required for kind mistral")
    auth_header = entry.get("auth_header")
    if auth_header is not None and auth_header not in _RT_AUTH_HEADERS:
        raise ConfigError(f"{where}.auth_header: must be one of {', '.join(_RT_AUTH_HEADERS)}")
    if row is not None and row.oauth_provider:
        # An OAuth profile's credential comes from the token store, never from a header choice.
        if "auth_header" in entry:
            raise ConfigError(f"{where}.auth_header: not allowed for profile {profile} "
                              f"(the credential comes from oauth)")
        # F12: the access token is a bearer to base_url, as readable on an http:// path as an
        # api_key (V14) -- the same rule and the same opt-in.
        if scheme == "http" and not allow_cleartext and not _rt_is_loopback_host(host):
            raise ConfigError(f"{where}.oauth: refusing to send its access token over a non-loopback "
                              f"http:// base_url (use https, or set allow_cleartext_api_key: true)")
        auth_header = "none"
    elif kind == "mistral" or row is not None:
        owner = f"profile {profile}" if profile else f"kind {kind}"
        if auth_header not in (None, "authorization"):
            raise ConfigError(f"{where}.auth_header: {owner} uses authorization only")
        auth_header = "authorization"
    elif auth_header is None:
        auth_header = "x-api-key" if api_key is not None else "none"
    if auth_header != "none" and api_key is None:
        raise ConfigError(f"{where}.auth_header: {auth_header} requires an api_key")
    oauth = None
    if entry.get("oauth") is not None:
        oauth = _rt_cfg_oauth(entry["oauth"], where, profile, token)
    identity: Tuple[Tuple[str, str], ...] = ()
    if "identity" in entry:
        if row is None or not row.identity:
            raise ConfigError(f"{where}.identity: not allowed for profile {profile}")
        identity = _rt_cfg_identity(entry["identity"], where)
    auth_base = None
    if "auth_base_url" in entry:
        if row is None or not row.oauth_provider:
            raise ConfigError(f"{where}.auth_base_url: not allowed for profile {profile} "
                              f"(it has no token endpoint)")
        auth_base = _rt_cfg_base_url(entry["auth_base_url"], where, "auth_base_url")
        # KD-19/S9: the refresh token travels there, so http only for an explicit loopback peer.
        if auth_base[0] != "https" and not (_rt_is_loopback_host(auth_base[1])
                                            and allow_private and allow_loopback):
            raise ConfigError(f"{where}.auth_base_url: must be https (http only for a loopback host "
                              f"with allow_private and allow_loopback)")
    headers = entry.get("forward_headers")
    if headers is None:
        forward = default_forward
    else:
        if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
            raise ConfigError(f"{where}.forward_headers: must be a list of header names")
        for header in headers:
            if header != header.lower():
                raise ConfigError(f"{where}.forward_headers: {_log_value(header)} must be lower-case")
            if header not in forwardable:
                raise ConfigError(f"{where}.forward_headers: {_log_value(header)} "
                                  f"may not be forwarded for kind {kind}")
        forward = frozenset(headers)
    return BackendSpec(
        name=name, kind=kind, scheme=scheme, host=host, port=port, base_path=base_path,
        allow_private=allow_private, allow_loopback=allow_loopback,
        ca_file=ca_file, ca_pem=ca_pem, api_key=api_key, auth_header=auth_header,
        forward_headers=forward,
        connect_timeout=_rt_cfg_timeout(entry.get("connect_timeout"), _CONNECT_TIMEOUT_S,
                                        f"{where}.connect_timeout"),
        idle_timeout=_rt_cfg_timeout(entry.get("idle_timeout"), _IDLE_TIMEOUT_S,
                                     f"{where}.idle_timeout"),
        profile=profile, oauth=oauth, identity=identity, auth_base=auth_base)


def _rt_cfg_route(name: str, entry: Any, backends: Mapping[str, BackendSpec],
                  where: Optional[str] = None) -> RouteSpec:
    """Validate one route (a routes.<name> entry, or the top-level default) into a RouteSpec.

    The backend must exist; model is a non-empty str of at most _RT_MODEL_LIMIT
    characters with no control character; display_name, when present, is a
    non-empty str of at most _RT_DISPLAY_NAME_LIMIT characters with no control
    character (GET /v1/models only; harmless on the default); max_input_tokens
    and max_tokens, when present, are each a JSON integer (bool refused) from
    _RT_TOKEN_LIMIT_MIN to _RT_TOKEN_LIMIT_MAX, independent of each other and
    only advertised by GET /v1/models, never enforced; options are checked by
    the backend kind's validators and an unknown option is refused by name.
    """
    if where is None:
        where = f"routes.{_rt_seg(name)}"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be an object")
    _rt_unknown_keys(entry, _RT_ROUTE_KEYS, where)
    backend = entry.get("backend")
    if not isinstance(backend, str) or backend not in backends:
        raise ConfigError(f"{where}.backend: must name a configured backend")
    model = entry.get("model")
    if not isinstance(model, str) or not model or len(model) > _RT_MODEL_LIMIT \
            or _rt_has_control(model):
        raise ConfigError(f"{where}.model: must be a non-empty string of at most "
                          f"{_RT_MODEL_LIMIT} characters with no control character")
    display_name = entry.get("display_name")
    if "display_name" in entry and (not isinstance(display_name, str) or not display_name
                                    or len(display_name) > _RT_DISPLAY_NAME_LIMIT
                                    or _rt_has_control(display_name)):
        raise ConfigError(f"{where}.display_name: must be a non-empty string of at most "
                          f"{_RT_DISPLAY_NAME_LIMIT} characters with no control character")
    limits: Dict[str, Optional[int]] = {}
    for key in _RT_ROUTE_LIMIT_KEYS:
        value = entry.get(key)
        if key in entry and (isinstance(value, bool) or not isinstance(value, int)
                             or not _RT_TOKEN_LIMIT_MIN <= value <= _RT_TOKEN_LIMIT_MAX):
            raise ConfigError(f"{where}.{key}: must be an integer "
                              f"{_RT_TOKEN_LIMIT_MIN}-{_RT_TOKEN_LIMIT_MAX}")
        limits[key] = value
    raw = entry.get("options", {})
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.options: must be an object")
    spec = backends[backend]
    row = _RT_RESPONSES_PROFILES[spec.profile] if spec.profile else None   # unit 5, read at call time
    if row is not None and not row.sampling:
        for option in ("temperature", "top_p", "max_tokens_cap"):
            if option in raw:
                raise ConfigError(f"{where}.options.{option}: not sent by this backend's auth mode "
                                  f"(profile {spec.profile})")
    kind = spec.kind
    options = KIND_CLASSES[kind].validate_options(raw, f"{where}.options")
    return RouteSpec(name=name, backend=backend, model=model, options=options,
                     display_name=display_name, max_input_tokens=limits["max_input_tokens"],
                     max_tokens=limits["max_tokens"])


_RT_TOKEN_HINT = "generate one with secrets.token_urlsafe(32)"


def _rt_cfg_token(token: str) -> bytes:
    """auth_token as ASCII bytes: _http_token_value's length and charset rules, then at
    least _TOKEN_MIN_DISTINCT distinct characters (V1: 32 x "a" is long, not secret).
    Every refusal names the generator; none names the value. The throttle-free 401
    leaves entropy to the operator, so the loader refuses the obviously weak."""
    try:
        token_bytes = _http_token_value(token, "auth_token", _TOKEN_MIN_LEN, ConfigError)
    except ConfigError as exc:
        raise ConfigError(f"{exc} ({_RT_TOKEN_HINT})") from None
    if len(set(token)) < _TOKEN_MIN_DISTINCT:
        raise ConfigError(f"auth_token: the bearer token must hold at least {_TOKEN_MIN_DISTINCT} "
                          f"distinct characters ({_RT_TOKEN_HINT})")
    return token_bytes


def load_config(path: str) -> RouterConfig:
    """Read, parse and validate the --config file into a RouterConfig (Step 4, FR-3/FR-7/FR-10).

    Every refusal is a ConfigError naming the JSON path and the rule, never a value.
    """
    text = _rt_read_config_file(path)
    try:
        data = json.loads(text, object_pairs_hook=_rt_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc.msg} at line {exc.lineno} "
                          f"column {exc.colno}") from None
    except (ValueError, RecursionError) as exc:
        # Deep nesting or an over-long integer: a refusal, never a traceback; the type only.
        raise ConfigError(f"config is not valid JSON: {type(exc).__name__}") from None
    if isinstance(data, _DupKey):
        raise data.refusal()
    if not isinstance(data, dict):
        raise ConfigError("config: the top level must be a JSON object")
    _rt_unknown_keys(data, _RT_TOP_KEYS, "config")

    token = data.get("auth_token")
    if not isinstance(token, str):
        raise ConfigError("auth_token: required, a string")
    token_bytes = _rt_cfg_token(token)

    raw_backends = data.get("backends")
    if not isinstance(raw_backends, dict) or not raw_backends:
        raise ConfigError("backends: required, a non-empty object")
    backends: Dict[str, BackendSpec] = {}
    for name, entry in raw_backends.items():
        if not _RT_BACKEND_NAME_RE.match(name):
            raise ConfigError(f"backends: the name {_log_value(name)} must match "
                              f"{_RT_BACKEND_NAME_RE.pattern}")
        backends[name] = _rt_cfg_backend(name, entry, token)

    raw_routes = data.get("routes", {})
    if not isinstance(raw_routes, dict):
        raise ConfigError("routes: must be an object")
    folded: Dict[str, str] = {}
    for name in raw_routes:
        if not name or len(name) > _RT_MODEL_LIMIT or _rt_has_control(name):
            raise ConfigError(f"routes: a route name must be a non-empty string of at most "
                              f"{_RT_MODEL_LIMIT} characters with no control character")
        key = name.casefold()
        if key == "default":
            raise ConfigError(f"routes: {_log_value(name)} is reserved for the top-level default route")
        if key in folded:
            raise ConfigError(f"routes: {_log_value(folded[key])} and {_log_value(name)} "
                              f"differ only by case")
        folded[key] = name
    routes = {name: _rt_cfg_route(name, entry, backends) for name, entry in raw_routes.items()}

    default = None
    if data.get("default") is not None:
        default = _rt_cfg_route("(default)", data["default"], backends, where="default")
    if not routes and default is None:
        raise ConfigError("config: needs at least one route or a default")

    secrets_ = (token_bytes,) + tuple(spec.api_key.encode("ascii")
                                      for spec in backends.values() if spec.api_key is not None)
    secrets_ += tuple(spec.oauth.refresh_token.encode("ascii") for spec in backends.values()
                      if spec.oauth is not None and spec.oauth.refresh_token is not None)
    # The write-back's re-check (Step 7): a UTF-8 round trip of a valid UTF-8 file is byte-exact.
    source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return RouterConfig(token=token_bytes, backends=backends, routes=routes, default=default,
                        secrets=secrets_, scrub=_rt_make_scrubber(secrets_), source_digest=source_digest)


# The atomic write-back (Plan Step 7, KD-10). Steps 0-0b (directory check, the
# KD-20 flock in unit 6, the sweep) are the caller's; _rt_config_update_oauth is
# steps 1-8. Every file operation after step 0 is relative to the checked
# directory descriptor, so a directory swapped after the check redirects nothing.
_RT_PERSIST_ATTEMPTS = 3
_rt_fsync = os.fsync        # seams: tests count and fail them, as _rt_getaddrinfo is a seam in unit 6
_rt_os_write = os.write


def _rt_cfg_parse(text: str, conflict: bool) -> dict:
    """P7 parse of config text: an object or a refusal (ConfigConflict when *conflict*)."""
    error = ConfigConflict if conflict else ConfigError
    try:
        data = json.loads(text, object_pairs_hook=_rt_no_duplicate_keys)
    except (ValueError, RecursionError) as exc:
        raise error(f"config is not valid JSON ({type(exc).__name__})") from None
    if isinstance(data, _DupKey):
        raise error(str(data.refusal())) from None
    if not isinstance(data, dict):
        raise error("config: the top level must be a JSON object")
    return data


def _rt_cfg_outside_equal(before: dict, after: dict, backend: str) -> bool:
    """True when every JSON value outside backends.<backend>.oauth is equal (== on parsed JSON)."""
    if set(before) != set(after):
        return False
    for key, value in before.items():
        if key != "backends":
            if value != after[key]:
                return False
            continue
        other = after[key]
        if not isinstance(value, dict) or not isinstance(other, dict) or set(value) != set(other):
            return False
        for name, entry in value.items():
            peer = other[name]
            if name != backend:
                if entry != peer:
                    return False
            elif not isinstance(entry, dict) or not isinstance(peer, dict):
                return False
            elif {k: v for k, v in entry.items() if k != "oauth"} != \
                    {k: v for k, v in peer.items() if k != "oauth"}:
                return False
    return True


def _rt_config_read_oauth(path: str, backend: str) -> Optional[Dict[str, Any]]:
    """The on-disk backends.<backend>.oauth object (P5 checks, P7 parse), or None -- the adoption read.
    Values are returned raw; the caller validates them with _rt_header_value_ok before adopting."""
    data = _rt_cfg_parse(_rt_read_config_file(path), conflict=False)
    backends = data.get("backends")
    entry = backends.get(backend) if isinstance(backends, dict) else None
    oauth = entry.get("oauth") if isinstance(entry, dict) else None
    return oauth if isinstance(oauth, dict) else None


def _rt_config_dir_check(path: str) -> int:
    """KD-10 step 0, run FIRST by every writer path (before the lock file can be created).
    Checks dir_fd support (fail closed), opens dirname(abspath(path)) O_RDONLY|O_DIRECTORY, fstat:
    a directory, owned by euid, mode & 0o022 == 0. Returns the descriptor; the caller closes it.
    ConfigError names the rule; OSError -> ConfigError("--config: cannot open the config directory (<type name>)")."""
    if not ({os.open, os.stat, os.unlink, os.rename} <= os.supports_dir_fd
            and os.listdir in os.supports_fd and os.stat in os.supports_follow_symlinks):
        raise ConfigError("--config: this Python lacks directory-relative file operations; refusing to rewrite")
    directory = os.path.dirname(os.path.abspath(path))
    try:
        dfd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise ConfigError(f"--config: cannot open the config directory ({type(exc).__name__})") from None
    try:
        st = os.fstat(dfd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise ConfigError("--config: refusing to rewrite a config whose directory is not owned by the "
                              "current user or is writable by group or others")
    except OSError as exc:
        os.close(dfd)
        raise ConfigError(f"--config: cannot open the config directory ({type(exc).__name__})") from None
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def _rt_config_sweep_temps(path: str, dir_fd: int) -> int:
    """KD-10 step 0b: unlink own-pattern stale temps (".<base>.<16 hex>.tmp", stat(dir_fd=,
    follow_symlinks=False) regular file, owned by euid, mode 0600) from os.listdir(dir_fd), each by
    os.unlink(name, dir_fd=dir_fd). Returns the count. Caller holds the flock. OSError per entry ignored."""
    own = re.compile(r"\.%s\.[0-9a-f]{16}\.tmp" % re.escape(os.path.basename(path)))
    try:
        names = os.listdir(dir_fd)
    except OSError as exc:
        raise ConfigError(f"--config: cannot list the config directory ({type(exc).__name__})") from None
    removed = 0
    for name in names:
        if not own.fullmatch(name):
            continue
        try:
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
                continue
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            continue
        removed += 1
    if removed:
        log.info("config sweep: removed %d stale temp file(s)", removed)
    return removed


def _rt_config_merge(data: dict, backend: str, kind: str, oauth: Dict[str, Any]) -> dict:
    """KD-10 steps 2-3: a NEW document with only backends.<backend>.oauth replaced (key position
    kept) by the persisted keys of *oauth*; ConfigConflict when the backend is gone or re-kinded."""
    backends = data.get("backends")
    entry = backends.get(backend) if isinstance(backends, dict) else None
    if not isinstance(entry, dict):
        raise ConfigConflict(f"--config: backends.{_rt_seg(backend)} is no longer on disk; nothing was written")
    if entry.get("kind") != kind:
        raise ConfigConflict(f"--config: backends.{_rt_seg(backend)}.kind changed on disk; nothing was written")
    persisted = {key: value for key, value in oauth.items() if key in _RT_OAUTH_PERSIST_KEYS}
    merged_entry = dict(entry)
    merged_entry["oauth"] = persisted
    merged_backends = dict(backends)
    merged_backends[backend] = merged_entry
    merged = dict(data)
    merged["backends"] = merged_backends
    return merged


def _rt_config_disk_refresh(data: dict, backend: str) -> Optional[str]:
    """The parsed document's backends.<backend>.oauth.refresh_token when it is a str, else None."""
    backends = data.get("backends")
    entry = backends.get(backend) if isinstance(backends, dict) else None
    oauth = entry.get("oauth") if isinstance(entry, dict) else None
    token = oauth.get("refresh_token") if isinstance(oauth, dict) else None
    return token if isinstance(token, str) else None


def _rt_config_update_oauth(path: str, dir_fd: int, backend: str, kind: str, oauth: Dict[str, Any],
                            expected_digest: str, started_from: Optional[str] = None,
                            dead: Optional[str] = None) -> str:
    """KD-10, steps 1-8 (steps 0-0b are the caller's); every file operation is relative to dir_fd.
    Returns the new digest. Keys outside _RT_OAUTH_PERSIST_KEYS are dropped.
    ConfigConflict / ConfigError name the rule, never the content; OSError (temp open included,
    e.g. a read-only directory) -> ConfigError("--config: cannot rewrite the config file (<type name>)");
    UnicodeEncodeError -> ConfigError (step 4). Caller holds the KD-20 flock.
    *started_from* (a refresh's rotation; None for `login`): a disk refresh_token that is a str and is
    none of *started_from*, the token being written and *dead* is a newer grant -- a `login` that ran
    while the refresh was out -- so nothing is written and ConfigConflict is raised (F40)."""
    base = os.path.basename(path)
    logged = False
    for _attempt in range(_RT_PERSIST_ATTEMPTS):
        # 1. Re-read relative to the checked directory (P5) and hash the text.
        text = _rt_read_config_file(base, dir_fd=dir_fd)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != expected_digest and not logged:
            logged = True
            log.info("config changed since load; merged backends.%s.oauth only", _log_value(backend))
        # 2-3. Parse (P7) and merge one sub-object.
        data = _rt_cfg_parse(text, conflict=True)
        if started_from is not None:
            on_disk = _rt_config_disk_refresh(data, backend)
            if on_disk is not None and on_disk not in (started_from, oauth.get("refresh_token"), dead):
                raise ConfigConflict(f"--config: backends.{_rt_seg(backend)}.oauth.refresh_token changed on disk "
                                     f"during the refresh (a login?); nothing was written")
        merged = _rt_config_merge(data, backend, kind, oauth)
        # 4. Serialize and verify.
        out = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        try:
            payload = out.encode("utf-8")
        except UnicodeEncodeError:
            raise ConfigError("--config: the config holds a string that is not valid Unicode; "
                              "cannot rewrite it") from None
        if json.loads(out) != merged or not _rt_cfg_outside_equal(data, merged, backend):
            raise ConfigError("--config: the rewritten config would not round-trip; nothing was written")
        tmp = None
        try:
            try:
                # 5. The temp file: a fresh random name, O_EXCL, 0600, relative to dir_fd.
                fd = -1
                for _name_try in range(_RT_PERSIST_ATTEMPTS):
                    name = ".%s.%s.tmp" % (base, secrets.token_hex(8))
                    try:
                        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                                     0o600, dir_fd=dir_fd)
                    except FileExistsError:
                        continue
                    tmp = name
                    break
                if tmp is None:
                    raise ConfigError("--config: cannot create a fresh temp file next to the config")
                try:
                    os.fchmod(fd, 0o600)
                    view = memoryview(payload)
                    while view:
                        view = view[_rt_os_write(fd, view):]
                    _rt_fsync(fd)
                finally:
                    os.close(fd)
                # 6. Re-check: a non-cooperating writer since step 1 means retry from step 1.
                again = _rt_read_config_file(base, dir_fd=dir_fd)
                if hashlib.sha256(again.encode("utf-8")).hexdigest() != digest:
                    continue
                # 7. renameat (atomic replace on POSIX; os.replace takes no dir_fd), then the directory.
                os.rename(tmp, base, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                tmp = None
                _rt_fsync(dir_fd)
            except OSError as exc:
                raise ConfigError(f"--config: cannot rewrite the config file ({type(exc).__name__})") from None
        finally:
            # 8. Cleanup: our own temp only, never a name we did not create.
            if tmp is not None:
                try:
                    os.unlink(tmp, dir_fd=dir_fd)
                except OSError:
                    pass
        return hashlib.sha256(payload).hexdigest()
    raise ConfigConflict(f"--config: the config kept changing during the rewrite of backends."
                         f"{_rt_seg(backend)}.oauth; nothing was written")


# ---------------------------------------------------------------------------
# Unit 3: inbound
# ---------------------------------------------------------------------------

# "system" inside messages is a mid-conversation system message (Claude Code 2.1.154+,
# Anthropic since Opus 4.8): never messages.0, text only; each adapter carries it its own way.
_RT_ROLES = ("user", "assistant", "system")


class InboundRequest(NamedTuple):   # unit 3: the validated request, built once
    """The validated Anthropic request. Keeps the default repr: body and client_headers are
    client content, so an InboundRequest is NEVER formatted into a log line, an error or an
    exception text (NFR-3)."""

    endpoint: str               # "messages" | "count_tokens"
    requested_model: str        # body["model"] as the client sent it; the model echo of KD-16
    body: dict                  # the parsed Anthropic body (validated minimum, rest untouched)
    stream: bool
    route: RouteSpec
    backend: BackendSpec
    client_headers: Dict[str, str]   # lower-case name -> value, only names in backend.forward_headers
    scrub: Callable[[str], str]  # cfg.scrub, copied in by _rt_parse_inbound: the KD-9 scrubber's only route
    #                              into units 4-5 (adapters, relays, quirk functions), which may not reach cfg
    # The sampling names (temperature / top_p) this backend + upstream model is known to refuse:
    # set by the handler from the server's learned table (Adapter.learns_sampling), never sent --
    # not even as a route override (unsupported wins). Empty for every kind that never learns.
    sampling_drop: FrozenSet[str] = frozenset()
    # The reasoning_effort values this backend + upstream model is known to support (a mistral
    # 400 listed them): set by the handler from the same learned table; a computed effort outside
    # it is remapped (_rt_ms_effort_remap). Empty: nothing learned, the effort sent as computed.
    effort_supported: FrozenSet[str] = frozenset()


def _rt_bad(field: str, rule: str) -> ApiError:
    """A 400 refusal naming the field and the rule; never the content."""
    return ApiError(400, "invalid_request_error", "%s: %s" % (field, rule))


def _rt_system_entry_text(message: dict, where: str) -> str:
    """The text of a mid-conversation role "system" entry at *where* (unit 3 admits a
    string or text blocks only): a string as is, else the blocks' texts joined with
    "\\n\\n" in order, cache_control dropped; a non-string text is a 400 naming its path."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "\n\n".join(_rt_ms_str(block.get("text"), "%s.content.%d.text" % (where, j))
                       for j, block in enumerate(content))


def _rt_route(cfg: RouterConfig, model: str) -> RouteSpec:
    """The route for `model`: exact match, then cfg.default, else a 404."""
    route = cfg.routes.get(model)
    if route is None:
        route = cfg.default
    if route is None:
        raise ApiError(404, "not_found_error", "model %s is not routed" % _log_value(model))
    return route


def _rt_model_entry(route: RouteSpec, created_at: str) -> dict:
    """One Anthropic model object for a named route: the route key is the id,
    display_name falls back to it. *created_at* is the router's start time
    (the front owns the clock; this unit never reads one). The route's token
    limits follow, null when unset: Anthropic's max_input_tokens and max_tokens,
    plus the non-standard context_window (= max_input_tokens) some clients read."""
    return {"type": "model", "id": route.name, "display_name": route.display_name or route.name,
            "created_at": created_at, "max_input_tokens": route.max_input_tokens,
            "max_tokens": route.max_tokens, "context_window": route.max_input_tokens}


def _rt_models_list(cfg: RouterConfig, created_at: str) -> dict:
    """GET /v1/models: one entry per route key in config order, never the default
    (it has no name). No pagination: always the whole list, has_more false."""
    data = [_rt_model_entry(route, created_at) for route in cfg.routes.values()]
    return {"data": data, "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None}


def _rt_parse_inbound(endpoint: str, body: Any, headers: Any, cfg: RouterConfig) -> InboundRequest:
    """Validate the minimum the router relies on and build the InboundRequest.

    model: a str of 1-_RT_MODEL_LIMIT characters; messages: a non-empty list of
    {role in (user, assistant, system), content: str | list of dicts with a str "type"},
    a system entry never at messages.0 and its blocks text only;
    stream: a bool or absent; max_tokens (messages only): an int from 1 to
    _RT_MAX_TOKENS_LIMIT, bool refused; tools: absent or a list of dicts with a
    str "name". Everything else
    is passed to the adapter untouched. Refusals are ApiError(400) naming the
    field and the rule, never the content (the shape check of mcp-proxy's _post).
    A header in the routed backend's forward_headers sent more than once is a
    400 naming the header, never a value (ADR 0015; _single_header's rule), and
    so is one whose value holds a CR or LF (V29).
    """
    if not isinstance(body, dict):
        raise _rt_bad("body", "must be a JSON object, got %s" % type(body).__name__)
    model = body.get("model")
    if not isinstance(model, str):
        raise _rt_bad("model", "must be a string")
    if not 1 <= len(model) <= _RT_MODEL_LIMIT:
        raise _rt_bad("model", "must be 1-%d characters" % _RT_MODEL_LIMIT)
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _rt_bad("messages", "must be a non-empty list")
    for i, message in enumerate(messages):
        where = "messages.%d" % i
        if not isinstance(message, dict):
            raise _rt_bad(where, "must be an object")
        role = message.get("role")
        # F15: the refusal's DEBUG line names the role's type only, never its value (ADR 0011).
        if role not in _RT_ROLES:
            log.debug("refused %s.role=%s", where, type(role).__name__)
            raise _rt_bad(where + ".role", "must be user, assistant or system")
        if role == "system" and i == 0:
            log.debug("refused %s.role=%s", where, type(role).__name__)
            raise _rt_bad(where + ".role", "must be user or assistant: a system message may only "
                                           "follow the first message (the top-level system comes first)")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise _rt_bad(where + ".content", "must be a string or a list of blocks")
        for j, block in enumerate(content):
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise _rt_bad("%s.content.%d" % (where, j), "must be an object with a string type")
            if role == "system" and block["type"] != "text":
                raise _rt_bad("%s.content.%d" % (where, j), "must be a text block in a system message")
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise _rt_bad("stream", "must be a boolean")
    if endpoint == "messages":
        max_tokens = body.get("max_tokens")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) \
                or not 1 <= max_tokens <= _RT_MAX_TOKENS_LIMIT:
            raise _rt_bad("max_tokens", "must be an integer from 1 to %d" % _RT_MAX_TOKENS_LIMIT)
    if "tools" in body:
        tools = body["tools"]
        if not isinstance(tools, list):
            raise _rt_bad("tools", "must be a list")
        for k, tool in enumerate(tools):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise _rt_bad("tools.%d" % k, "must be an object with a string name")
    route = _rt_route(cfg, model)
    backend = cfg.backends[route.backend]
    allowed = backend.forward_headers
    client_headers = {}
    for name, value in headers.items():
        key = name.lower()
        if key in allowed:
            if key in client_headers:
                # A forwardable header sent twice is ambiguous (ADR 0015): refused,
                # naming the header only -- never either value.
                raise _rt_bad("header %s" % _log_value(key), "must appear at most once")
            if "\r" in value or "\n" in value:
                # An obs-fold continuation or a bare CR survives the stdlib's header
                # parse and http.client's putheader check; a lenient upstream could
                # read it as a header of its own (V29). Refused, the value never shown.
                raise _rt_bad("header %s" % _log_value(key), "must not contain CR or LF")
            client_headers[key] = value
    return InboundRequest(endpoint=endpoint, requested_model=model, body=body, stream=stream,
                          route=route, backend=backend, client_headers=client_headers,
                          scrub=cfg.scrub)


# ---------------------------------------------------------------------------
# Unit 4: SSE toolkit
# ---------------------------------------------------------------------------

_SSE_BOM = b"\xef\xbb\xbf"
# One SSE line with its terminator: CRLF, LF or a bare CR (WHATWG EventSource), or an unterminated tail.
_RT_SSE_LINE_RE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def _rt_sse_split(line: bytes) -> List[bytes]:
    """One upstream read line -> the SSE lines a client's parser sees in it, each
    keeping its own terminator, so their join is *line* (V30). b"" stays [b""]."""
    return _RT_SSE_LINE_RE.findall(line) or [line]


def _rt_sse_events(lines: Iterable[Union[bytes, str]]) -> Iterator[Tuple[str, str, bytes]]:
    """Parse SSE *lines* into (event, data, raw_event_bytes) tuples.

    `event:` and `data:` fields accumulate (several data lines are joined with
    "\\n") and the event is yielded on the blank line, named "message" when it
    carried no `event:` field. A `:` line is yielded at once as
    ("comment", "", raw). Each item is first split at every CRLF, LF and bare
    CR, as a client's SSE parser splits it (V30); unknown fields (`id:`,
    `retry:`, ...) are ignored but kept in the raw bytes. A blank line that
    closes nothing yields nothing. A leading BOM is the caller's to drop (the
    relays drop it at the stream start). Text is decoded as UTF-8 with
    replacement; the raw bytes are never altered. Pure: no I/O.
    """
    event = ""
    data: List[str] = []
    raw: List[bytes] = []
    pending = False
    for item in lines:
        if isinstance(item, str):
            item = item.encode("utf-8", "surrogatepass")
        for line in _rt_sse_split(item):
            text = line.rstrip(b"\r\n").decode("utf-8", "replace")
            if not text:
                if pending:
                    raw.append(line)
                    yield (event or "message", "\n".join(data), b"".join(raw))
                event, data, raw, pending = "", [], [], False
                continue
            if text.startswith(":"):
                yield ("comment", "", line)
                continue
            raw.append(line)
            pending = True
            name, sep, value = text.partition(":")
            if sep and value.startswith(" "):
                value = value[1:]
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)


def _rt_message_id() -> str:
    """A fresh Anthropic-shaped message id: "msg_" + 24 hex digits."""
    return "msg_" + secrets.token_hex(12)


class AnthropicSseEncoder:
    """Sans-IO Anthropic SSE writer: every method returns bytes, never writes.

    Tracks the next content block index, the kind of the open streamed block
    (open_kind: "text" or "thinking", or None when no block is open; an open
    block is closed -- a thinking block with no signature -- before any other
    block starts), whether any tool block
    was emitted, and the started / closed flags. Every method returns b"" once
    closed (after finish() or error()).
    """

    def __init__(self, message_id: str, model: str) -> None:
        self.message_id = message_id
        self.model = model
        self.index = 0                      # the next content block index
        self.open_kind: Optional[str] = None
        self.any_tool = False
        self.started = False
        self.closed = False

    @staticmethod
    def _event(name: str, payload: dict) -> bytes:
        return b"event: " + name.encode("ascii") + b"\ndata: " + _rt_dumps(payload) + b"\n\n"

    def _close_open(self) -> bytes:
        """content_block_stop for the open streamed block, if any; the index moves on."""
        if self.open_kind is None:
            return b""
        self.open_kind = None
        out = self._event("content_block_stop", {"type": "content_block_stop", "index": self.index})
        self.index += 1
        return out

    def start(self, input_tokens: int = 0) -> bytes:
        """message_start, once."""
        if self.closed or self.started:
            return b""
        self.started = True
        return self._event("message_start", {
            "type": "message_start",
            "message": {"id": self.message_id, "type": "message", "role": "assistant",
                        "model": self.model, "content": [], "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": input_tokens, "output_tokens": 0}}})

    def text(self, delta: str) -> bytes:
        """A text_delta, opening a text block first when none is open."""
        if self.closed:
            return b""
        out = b""
        if self.open_kind != "text":
            out += self._close_open()
            self.open_kind = "text"
            out += self._event("content_block_start", {
                "type": "content_block_start", "index": self.index,
                "content_block": {"type": "text", "text": ""}})
        return out + self._event("content_block_delta", {
            "type": "content_block_delta", "index": self.index,
            "delta": {"type": "text_delta", "text": delta}})

    def thinking(self, delta: str) -> bytes:
        """A thinking_delta, closing an open text block and opening a thinking
        block (empty thinking and signature) first when none is open."""
        if self.closed:
            return b""
        out = b""
        if self.open_kind != "thinking":
            out += self._close_open()
            self.open_kind = "thinking"
            out += self._event("content_block_start", {
                "type": "content_block_start", "index": self.index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
        return out + self._event("content_block_delta", {
            "type": "content_block_delta", "index": self.index,
            "delta": {"type": "thinking_delta", "thinking": delta}})

    def thinking_end(self, signature: str) -> bytes:
        """Close the open thinking block: a signature_delta when *signature* is
        non-empty, then content_block_stop. Nothing when no thinking block is open."""
        if self.closed or self.open_kind != "thinking":
            return b""
        out = b""
        if signature:
            out += self._event("content_block_delta", {
                "type": "content_block_delta", "index": self.index,
                "delta": {"type": "signature_delta", "signature": signature}})
        return out + self._close_open()

    def redacted_thinking(self, data: str) -> bytes:
        """A whole redacted_thinking block: start, stop (no delta).
        An open block is closed first."""
        if self.closed:
            return b""
        out = self._close_open()
        i = self.index
        out += self._event("content_block_start", {
            "type": "content_block_start", "index": i,
            "content_block": {"type": "redacted_thinking", "data": data}})
        out += self._event("content_block_stop", {"type": "content_block_stop", "index": i})
        self.index += 1
        return out

    def tool_block(self, tool_id: str, name: str, args_json: str) -> bytes:
        """A whole tool_use block: start, one input_json_delta, stop.
        An open block is closed first."""
        if self.closed:
            return b""
        out = self._close_open()
        i = self.index
        out += self._event("content_block_start", {
            "type": "content_block_start", "index": i,
            "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}})
        out += self._event("content_block_delta", {
            "type": "content_block_delta", "index": i,
            "delta": {"type": "input_json_delta", "partial_json": args_json}})
        out += self._event("content_block_stop", {"type": "content_block_stop", "index": i})
        self.index += 1
        self.any_tool = True
        return out

    def ping(self) -> bytes:
        """event: ping."""
        if self.closed:
            return b""
        return self._event("ping", {"type": "ping"})

    def finish(self, stop_reason: str, usage: dict) -> bytes:
        """Close the open block, then message_delta and message_stop; closes
        the encoder. stop_reason is forced to tool_use after any tool block."""
        if self.closed:
            return b""
        out = self._close_open()
        if self.any_tool:
            stop_reason = "tool_use"
        out += self._event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": usage})
        out += self._event("message_stop", {"type": "message_stop"})
        self.closed = True
        return out

    def error(self, err_type: str, message: str) -> bytes:
        """One event: error with the Anthropic envelope; closes the encoder."""
        if self.closed:
            return b""
        self.closed = True
        return self._event("error", _rt_error_body(err_type, message))


class _RtMessageCollector:
    """The encoder's sibling sink for a non-stream client: the same methods with
    the same started / closed semantics, each returning b"" and accumulating the
    message instead (one translator, two sinks -- so the non-stream answer is the
    stream folded, never a second state machine). finish() stores stop_reason
    (forced to tool_use after a tool block, as the encoder does) and usage;
    error() sets failed = (type, message). message() is MistralAdapter._message's shape.
    """

    def __init__(self, message_id: str, model: str) -> None:
        self.message_id = message_id
        self.model = model
        self.content: List[dict] = []
        self.open_kind: Optional[str] = None
        self.parts: List[str] = []          # the open text/thinking block's deltas, joined once (F18)
        self.any_tool = False
        self.stop_reason: Optional[str] = None
        self.usage: Dict[str, Any] = {}
        self.failed: Optional[Tuple[str, str]] = None
        self.started = False
        self.closed = False

    def start(self, input_tokens: int = 0) -> bytes:
        """Marks the message started, once."""
        if not self.closed:
            self.started = True
        return b""

    def _flush(self) -> None:
        """Joins the open text/thinking block's pending deltas into it, once (F18: a `+=` per
        delta copies the whole block each time). Called before the open block changes and by
        message(); a later delta to the same block is joined by the next flush."""
        if self.parts and self.open_kind in ("text", "thinking"):
            block = self.content[-1]
            block[self.open_kind] = block[self.open_kind] + "".join(self.parts)
        self.parts = []

    def text(self, delta: str) -> bytes:
        """Appends to the open text block, opening one when none is open."""
        if self.closed:
            return b""
        if self.open_kind != "text":
            self._flush()
            self.open_kind = "text"
            self.content.append({"type": "text", "text": ""})
        self.parts.append(delta)
        return b""

    def thinking(self, delta: str) -> bytes:
        """Appends to the open thinking block, opening one (empty signature) when none is open."""
        if self.closed:
            return b""
        if self.open_kind != "thinking":
            self._flush()
            self.open_kind = "thinking"
            self.content.append({"type": "thinking", "thinking": "", "signature": ""})
        self.parts.append(delta)
        return b""

    def thinking_end(self, signature: str) -> bytes:
        """Closes the open thinking block with *signature*; nothing when none is open."""
        if self.closed or self.open_kind != "thinking":
            return b""
        self._flush()
        self.open_kind = None
        self.content[-1]["signature"] = signature
        return b""

    def redacted_thinking(self, data: str) -> bytes:
        """A whole redacted_thinking block."""
        if self.closed:
            return b""
        self._flush()
        self.open_kind = None
        self.content.append({"type": "redacted_thinking", "data": data})
        return b""

    def tool_block(self, tool_id: str, name: str, args_json: str) -> bytes:
        """A whole tool_use block (args_json is the checked, re-dumped JSON object)."""
        if self.closed:
            return b""
        self._flush()
        self.open_kind = None
        self.content.append({"type": "tool_use", "id": tool_id, "name": name, "input": _rt_loads(args_json)})
        self.any_tool = True
        return b""

    def ping(self) -> bytes:
        """Nothing: a collected answer has no keep-alive."""
        return b""

    def finish(self, stop_reason: str, usage: dict) -> bytes:
        """Stores stop_reason (tool_use after any tool block) and usage; closes."""
        if self.closed:
            return b""
        self._flush()
        self.open_kind = None
        self.stop_reason = "tool_use" if self.any_tool else stop_reason
        self.usage = dict(usage)
        self.closed = True
        return b""

    def error(self, err_type: str, message: str) -> bytes:
        """Records failed = (type, message); closes."""
        if self.closed:
            return b""
        self._flush()
        self.failed = (err_type, message)
        self.closed = True
        return b""

    def message(self) -> dict:
        """The collected Anthropic message (MistralAdapter._message's shape)."""
        self._flush()
        return {"id": self.message_id, "type": "message", "role": "assistant",
                "model": self.model, "content": self.content,
                "stop_reason": self.stop_reason, "stop_sequence": None, "usage": self.usage}


# ---------------------------------------------------------------------------
# Unit 5: adapters
# ---------------------------------------------------------------------------

# Client header names no backend may ever forward (KD-3): credentials, hop-by-hop
# and framing headers, and the four the router sets itself on every upstream
# request (_rt_upstream_headers: Content-Type, Accept, User-Agent; Accept-Encoding
# is never offered). Any name starting with _RT_NEVER_FORWARD_PREFIX is refused too.
_NEVER_FORWARD = frozenset({"authorization", "x-api-key", "host", "cookie", "content-length",
                            "transfer-encoding", "connection", "origin", "accept",
                            "accept-encoding", "content-type", "user-agent"})
_RT_NEVER_FORWARD_PREFIX = "proxy-"


class StreamPolicy(NamedTuple):
    """What the FRONT reads about a kind's upstream stream (L6).

    The event limit is deliberately absent: it has one source of truth, the
    translator's own (_SSE_EVENT_LIMIT, or ResponsesProfile.event_limit).
    """

    line_limit: int             # one upstream SSE line, enforced by the front's pump
    require_event_stream: bool  # a 2xx without text/event-stream -> 502 (False: "" also accepted)


class _CredentialBase(NamedTuple):
    access_token: str           # SECRET: never logged, formatted or repr'd
    account_id: Optional[str]   # chatgpt_account_id claim (codex), None when absent
    residency: Optional[str]    # data-residency claim, None when absent


class Credential(_CredentialBase):
    """One upstream bearer credential. `access_token` is a secret: its repr is `<redacted>`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return _rt_redacted_repr(self, {"access_token": "<redacted>"})


class ResponsesProfile(NamedTuple):
    """One Responses dialect as data (KD-1): every unit-5 function takes a profile, never a kind."""

    name: str                   # == its key in _RT_RESPONSES_PROFILES
    default_base_url: str
    path: str                   # "/codex/responses" | "/v1/responses"
    oauth_provider: str         # key into _RT_OAUTH_PROVIDERS; "" = api_key mode
    sampling: bool              # temperature / top_p / max_output_tokens sent
    account_header: bool        # chatgpt-account-id (+ residency) sent
    identity: bool              # originator / version / User-Agent from the identity row
    beta: str                   # OpenAI-Beta value, "" = none
    require_event_stream: bool  # 2xx without text/event-stream -> 502 (False: "" also accepted)
    line_limit: int             # SSE line cap -> StreamPolicy.line_limit (the front's pump)
    event_limit: int            # SSE event cap -> read ONLY by the Responses stream translator
    tool_shape: str             # a _RT_RS_TOOL_SHAPES value: "function" or "namespace"


class Adapter:
    """One subclass per backend kind (KD-12); instances are stateless.

    The class attributes are the kind's tables: FORWARDABLE (client headers a
    backend MAY forward, lower-case), DEFAULT_FORWARD (forwarded when the backend
    sets no forward_headers) and ROUTE_OPTIONS (option -> validator(value, where)).
    The four request/translator methods are sans-IO; the base raises
    NotImplementedError, so a kind without them is never in ADAPTERS. The two
    hooks upstream_head and stream_policy have safe defaults (P1) that keep
    every kind's behaviour as it was: no Accept override, no extra headers,
    the _SSE_LINE_LIMIT line cap and a required text/event-stream answer.
    """

    kind: str = ""
    FORWARDABLE: FrozenSet[str] = frozenset()
    DEFAULT_FORWARD: FrozenSet[str] = frozenset()
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {}

    @classmethod
    def validate_options(cls, options: dict, where: str) -> dict:
        """Each option through its ROUTE_OPTIONS validator; an unknown option is refused by name."""
        checked: Dict[str, Any] = {}
        for key, value in options.items():
            check = cls.ROUTE_OPTIONS.get(key)
            if check is None:
                raise ConfigError(f"{where}: unknown option {_rt_seg(key)} for kind {cls.kind}")
            checked[key] = check(value, f"{where}.{key}")
        return checked

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        """(path, body_bytes) to POST upstream for /v1/messages."""
        raise NotImplementedError

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        """(path, body_bytes) to forward, or a local answer {"input_tokens": N}."""
        raise NotImplementedError

    def json_response(self, inbound: InboundRequest, status: int, body: bytes) -> Tuple[int, dict]:
        """Map a non-stream upstream answer to (status, Anthropic JSON)."""
        raise NotImplementedError

    def stream_translator(self, inbound: InboundRequest) -> Any:
        """A sans-IO translator: begin / feed(line) / finish / fail(type, msg) / ping -> bytes."""
        raise NotImplementedError

    def upstream_head(self, inbound: InboundRequest,
                      cred: Optional[Credential]) -> Tuple[Optional[str], Tuple[Tuple[str, str], ...]]:
        """(Accept override or None, extra (name, value) header pairs) for the upstream request."""
        return None, ()

    def stream_policy(self, inbound: InboundRequest) -> StreamPolicy:
        """The front's stream limits for this request; the base keeps today's behaviour."""
        return StreamPolicy(_SSE_LINE_LIMIT, True)

    # The sampling hooks of the handler's learned drop (live finding 2026-10-07). The base
    # never learns, so a kind without them keeps its behaviour: an upstream 400 is relayed.
    def learns_sampling(self, inbound: InboundRequest) -> bool:
        """True when an upstream 400 naming temperature / top_p unsupported is learned for
        (backend, upstream model) and the request retried once without it."""
        return False

    def sampling_sent(self, inbound: InboundRequest) -> Tuple[str, ...]:
        """The sampling names (of temperature, top_p) this request's upstream body carries."""
        return ()

    def sampling_dropped(self, inbound: InboundRequest) -> Tuple[str, ...]:
        """The sampling names the client or the route asked for that the upstream body does not carry."""
        return ()

    # The effort hooks of the same learned table (live finding 2026-10-07, mistral): an upstream
    # 400 refusing the reasoning_effort sent is learned for (backend, upstream model) as the
    # supported set, and the request retried once with the remap. The base never learns.
    def effort_sent(self, inbound: InboundRequest) -> Optional[str]:
        """The learnable reasoning_effort this request's upstream body carries; None: never learned."""
        return None

    def effort_remapped(self, inbound: InboundRequest) -> Optional[str]:
        """The reasoning_effort sent when it differs from the one the request asked for (the req line)."""
        return None


class EventRelay:
    """The passthrough stream translator (KD-10): whole upstream events, byte-exact.

    Sans-IO. feed(line) accumulates lines until the blank line that closes an
    event, then returns that event's bytes unchanged -- never re-serialised.
    An event over _SSE_EVENT_LIMIT is dropped whole and the relay fails. A `:`
    comment line is never relayed: the handler's last-write ping timer (KD-4)
    is the one source of `event: ping`, so a comment-only upstream yields one
    ping per _PING_INTERVAL_S and never more (D4). An upstream `event: error`
    is re-emitted with its message through inbound.scrub (KD-9) and closes the
    relay; so does a relayed `event: message_stop` (D12). fail() emits one
    `event: error` at once and discards any half event (never sent, so the
    client stream is at an event boundary). Every method returns b"" once
    closed. A bare CR ends a line and a stream-leading BOM is dropped, as in a
    client's SSE parser (V30); the dropped BOM is never relayed.
    """

    def __init__(self, inbound: InboundRequest) -> None:
        self.scrub = inbound.scrub
        self.backend_name = inbound.backend.name
        self.lines: List[bytes] = []
        self.size = 0
        self.started = False
        self.closed = False
        self.fresh = True               # no line fed yet: a leading BOM is dropped (V30)

    def begin(self) -> bytes:
        """Nothing: the upstream's own message_start is the first event."""
        return b""

    def ping(self) -> bytes:
        """event: ping."""
        if self.closed:
            return b""
        return AnthropicSseEncoder._event("ping", {"type": "ping"})

    def fail(self, err_type: str, message: str) -> bytes:
        """One event: error, at once; the half event is discarded. Closes the relay."""
        if self.closed:
            return b""
        self.closed = True
        self.lines, self.size = [], 0
        return AnthropicSseEncoder._event("error", _rt_error_body(err_type, message))

    def finish(self) -> bytes:
        """Upstream EOF: without a relayed message_stop the stream ended early (KD-7)."""
        if self.closed:
            return b""
        return self.fail("api_error", "backend %s ended the stream early" % self.backend_name)

    def feed(self, line: bytes) -> bytes:
        """One upstream read line -> b"" or whole events' bytes. It is split at every
        CRLF, LF and bare CR and a stream-leading BOM is dropped, so the router sees
        the events a client's SSE parser sees (V30)."""
        if self.fresh:
            self.fresh = False
            if line.startswith(_SSE_BOM):
                line = line[len(_SSE_BOM):]
        return b"".join(self._feed_line(part) for part in _rt_sse_split(line))

    def _feed_line(self, line: bytes) -> bytes:
        """One SSE line -> b"" or one whole event's bytes."""
        if self.closed:
            return b""
        text = line.rstrip(b"\r\n")
        if text.startswith(b":"):
            return b""
        if text:
            self.lines.append(line)
            self.size += len(line)
            if self.size > _SSE_EVENT_LIMIT:
                return self.fail("api_error", "backend sent an oversized event")
            return b""
        if not self.lines:
            return b""
        accumulated = self.lines + [line]
        self.lines, self.size = [], 0
        out = b""
        for name, data, raw in _rt_sse_events(accumulated):
            if name == "error":
                return out + self._upstream_error(data)
            self.started = True
            out += raw
            if name == "message_stop":
                self.closed = True
        return out

    def _upstream_error(self, data: str) -> bytes:
        """An upstream `event: error`: its type when Anthropic's, its message scrubbed; closes."""
        err_type, message = "api_error", "backend %s sent an error" % self.backend_name
        try:
            err = _rt_loads(data)["error"]
            if err.get("type") in _ANTHROPIC_ERROR_TYPES:
                err_type = err["type"]
            if isinstance(err.get("message"), str):
                message = self.scrub(err["message"])
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            pass
        return self.fail(err_type, message)


class PassthroughAdapter(Adapter):
    """kind passthrough: an Anthropic-compatible upstream; only `model` is rewritten (KD-16) --
    plus, on /v1/messages, a route's temperature / top_p override."""

    kind = "passthrough"
    FORWARDABLE = frozenset({"anthropic-version", "anthropic-beta"})
    DEFAULT_FORWARD = frozenset({"anthropic-version", "anthropic-beta"})
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {
        "temperature": _RT_TEMPERATURE,
        "top_p": _RT_TOP_P,
    }

    @staticmethod
    def _rewrite(inbound: InboundRequest, sampling: bool = False) -> bytes:
        """The client body with `model` set to the route's upstream model -- and, with
        *sampling* (/v1/messages only), the route's temperature / top_p in place of the
        client's, also when the client sent none; count_tokens takes no sampling."""
        body = dict(inbound.body)
        body["model"] = inbound.route.model
        if sampling:
            for key in ("temperature", "top_p"):
                if key in inbound.route.options:
                    body[key] = inbound.route.options[key]
        return _rt_dumps(body)

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        return "/v1/messages", self._rewrite(inbound, sampling=True)

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        return "/v1/messages/count_tokens", self._rewrite(inbound)

    def json_response(self, inbound: InboundRequest, status: int, body: bytes) -> Tuple[int, dict]:
        """2xx: the parsed answer unchanged, no model rewrite (KD-16, D11). Non-2xx:
        the KD-14 status; an Anthropic error envelope keeps its type (unless KD-8
        remapped a 401/403) and its message, scrubbed (KD-9)."""
        name = inbound.backend.name
        if 200 <= status < 300:
            try:
                return status, _rt_loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                return 502, _rt_error_body("api_error", "backend %s answered malformed JSON" % name)
        mapped = _UPSTREAM_STATUS.get(status, 502)
        err_type = _STATUS_TO_TYPE.get(mapped, "api_error")
        if status in (401, 403):
            log.warning("upstream credential rejected: backend %s", _log_value(name))
            return mapped, _rt_error_body(err_type, "backend %s rejected the router's credential" % name)
        message = "backend %s answered %d" % (name, status)
        try:
            obj = _rt_loads(body.decode("utf-8"))
            if isinstance(obj, dict) and obj.get("type") == "error" and isinstance(obj.get("error"), dict):
                err = obj["error"]
                if err.get("type") in _ANTHROPIC_ERROR_TYPES:
                    err_type = err["type"]
                if isinstance(err.get("message"), str):
                    message = inbound.scrub(err["message"])
        except (UnicodeDecodeError, ValueError, RecursionError):
            pass
        return mapped, _rt_error_body(err_type, message)

    def stream_translator(self, inbound: InboundRequest) -> "EventRelay":
        return EventRelay(inbound)


# llama.cpp quirks (KD-13). Every quirk is pure: it never mutates its input,
# returns the input itself when it has nothing to repair (a no-op on conforming
# input) and a new value otherwise, and is idempotent (fn(fn(x)) == fn(x), E6).
# Request quirks are fn(body, route) -> body; event quirks are
# fn(event, data, scrub) -> data on one parsed SSE `data` object.

_RT_LL_THINKING_BUDGET = 8192     # adaptive-thinking's budget when the route sets no thinking_budget


def _rt_ll_is_tool_result(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def _rt_ll_tool_results_first(body: dict, route: RouteSpec) -> dict:
    """In each user message with list content: the tool_result blocks first, each group in its order."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    out: List[Any] = []
    changed = False
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and message.get("role") == "user":
            ordered = ([b for b in content if _rt_ll_is_tool_result(b)]
                       + [b for b in content if not _rt_ll_is_tool_result(b)])
            if ordered != content:
                message = dict(message)
                message["content"] = ordered
                changed = True
        out.append(message)
    if not changed:
        return body
    new = dict(body)
    new["messages"] = out
    return new


def _rt_ll_hoist_system(body: dict, route: RouteSpec) -> dict:
    """system as a list of text blocks -> one string joined with "\\n\\n"; cache_control dropped.

    A role "system" entry inside messages never gets here: LlamacppAdapter._rewrite
    collapses every one into `system` (_rt_ll_collapse_system) before the quirks run.
    """
    system = body.get("system")
    if not isinstance(system, list):
        return body
    new = dict(body)
    new["system"] = "\n\n".join(b["text"] for b in system
                                if isinstance(b, dict) and b.get("type") == "text"
                                and isinstance(b.get("text"), str))
    return new


def _rt_ll_collapse_system(body: dict) -> dict:
    """Every mid-conversation role "system" entry removed from messages and its text
    appended, in order, to the top-level system: after a string with "\\n\\n" (a new
    string when there is none), or as text blocks after a list (hoist-system then joins
    them with "\\n\\n"). Nothing else is reordered or merged: two same-role messages left
    adjacent go upstream as they are. Not a quirk row: llama.cpp's chat template takes one
    leading system message, so this runs on every route, quirks_off or not. The input is
    never mutated; a body without a system entry is returned as is."""
    messages = body["messages"]
    if not any(message["role"] == "system" for message in messages):
        return body
    texts = [_rt_system_entry_text(message, "messages.%d" % i)
             for i, message in enumerate(messages) if message["role"] == "system"]
    texts = [text for text in texts if text]
    new = dict(body)
    new["messages"] = [message for message in messages if message["role"] != "system"]
    system = body.get("system")
    if system is not None and not isinstance(system, (str, list)):
        raise _rt_bad("system", "must be a string or a list of text blocks")
    if isinstance(system, list):
        new["system"] = list(system) + [{"type": "text", "text": text} for text in texts]
    elif texts:
        new["system"] = "\n\n".join(([system] if isinstance(system, str) and system else []) + texts)
    return new


def _rt_ll_adaptive_thinking(body: dict, route: RouteSpec) -> dict:
    """thinking {"type":"adaptive"} -> enabled with min(thinking_budget or 8192, max_tokens - 1);
    max_tokens <= 1 drops thinking."""
    thinking = body.get("thinking")
    if not (isinstance(thinking, dict) and thinking.get("type") == "adaptive"):
        return body
    new = dict(body)
    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 1:
        del new["thinking"]
        return new
    budget = route.options.get("thinking_budget") or _RT_LL_THINKING_BUDGET
    new["thinking"] = {"type": "enabled", "budget_tokens": min(budget, max_tokens - 1)}
    return new


def _rt_ll_sampling_override(body: dict, route: RouteSpec) -> dict:
    """The route's temperature / top_p / top_k override the client's (Claude Code sends temperature 1)."""
    override = {key: route.options[key] for key in ("temperature", "top_p", "top_k") if key in route.options}
    if all(key in body and body[key] == value for key, value in override.items()):
        return body
    new = dict(body)
    new.update(override)
    return new


def _rt_ll_tool_use_input(event: str, data: dict, scrub: Callable[[str], str]) -> dict:
    """A content_block_start tool_use without "input" gets "input": {}; an existing input is kept."""
    if event != "content_block_start":
        return data
    block = data.get("content_block")
    if not (isinstance(block, dict) and block.get("type") == "tool_use") or "input" in block:
        return data
    new_block = dict(block)
    new_block["input"] = {}
    new = dict(data)
    new["content_block"] = new_block
    return new


def _rt_ll_error_shape(event: str, data: dict, scrub: Callable[[str], str]) -> dict:
    """An event: error in llama.cpp's shape -> the Anthropic envelope, its message scrubbed (KD-9).

    Flat {"code","message","type"} or nested {"error":{...}}: the type is
    _STATUS_TO_TYPE[_UPSTREAM_STATUS.get(code, 502)]. An envelope that is already
    type "error" with an Anthropic error.type keeps that type; only its message is
    scrubbed (so the row is idempotent).
    """
    if event != "error" or not isinstance(data, dict):
        return data
    err = data.get("error")
    if data.get("type") == "error" and isinstance(err, dict) and err.get("type") in _ANTHROPIC_ERROR_TYPES:
        message = err.get("message")
        return _rt_error_body(err["type"], scrub(message) if isinstance(message, str) else "backend sent an error")
    source = err if isinstance(err, dict) else data
    code = source.get("code")
    status = 502 if isinstance(code, bool) or not isinstance(code, int) else _UPSTREAM_STATUS.get(code, 502)
    message = source.get("message")
    return _rt_error_body(_STATUS_TO_TYPE.get(status, "api_error"),
                          scrub(message) if isinstance(message, str) else "backend sent an error")


# The registries: (name, upstream_ref, fn), run in this order unless the route
# names the row in quirks_off. Delete a row when its upstream fix is the minimum
# supported llama.cpp build. The issue numbers are unverified (plan Step 9).
LLAMACPP_REQUEST_QUIRKS: Tuple[Tuple[str, str, Callable[[dict, RouteSpec], dict]], ...] = (
    ("tool-results-first", "llama.cpp #29482", _rt_ll_tool_results_first),
    ("hoist-system", "llama.cpp #27367", _rt_ll_hoist_system),
    ("adaptive-thinking", "llama.cpp: no adaptive type", _rt_ll_adaptive_thinking),
    ("sampling-override", "llama.cpp #27893", _rt_ll_sampling_override),
)
LLAMACPP_EVENT_QUIRKS: Tuple[Tuple[str, str, Callable[[str, dict, Callable[[str], str]], dict]], ...] = (
    ("tool-use-input", "llama.cpp #22960", _rt_ll_tool_use_input),
    ("error-shape", "llama.cpp error format", _rt_ll_error_shape),
)

# The quirk names quirks_off accepts (unit 2's _rt_opt_quirks reads it at call time).
_RT_LL_QUIRK_NAMES = tuple(name for name, _, _ in LLAMACPP_REQUEST_QUIRKS + LLAMACPP_EVENT_QUIRKS)


class LlamacppAdapter(Adapter):
    """kind llamacpp: llama-server's /v1/messages plus the quirk rows (KD-13) and the model echo (KD-16)."""

    kind = "llamacpp"
    FORWARDABLE = frozenset({"anthropic-version"})
    DEFAULT_FORWARD = frozenset({"anthropic-version"})
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {
        "thinking_budget": _rt_opt_int(1),
        "temperature": _RT_TEMPERATURE,
        "top_p": _RT_TOP_P,
        "top_k": _rt_opt_int(1),
        "quirks_off": _rt_opt_quirks,             # reads the registry-derived _RT_LL_QUIRK_NAMES
    }

    @staticmethod
    def _apply_request_quirks(body: dict, route: RouteSpec) -> dict:
        """Every LLAMACPP_REQUEST_QUIRKS row the route does not name in quirks_off, in order."""
        off = route.options.get("quirks_off", ())
        for name, _ref, fn in LLAMACPP_REQUEST_QUIRKS:
            if name not in off:
                body = fn(body, route)
        return body

    def _rewrite(self, inbound: InboundRequest) -> bytes:
        """The client body with its mid-conversation system entries collapsed into `system`,
        through the request quirks, then `model` set to the route's upstream model."""
        body = dict(self._apply_request_quirks(_rt_ll_collapse_system(dict(inbound.body)), inbound.route))
        body["model"] = inbound.route.model
        return _rt_dumps(body)

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        return "/v1/messages", self._rewrite(inbound)

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        """Forwarded (D4) after the request quirks, so the count matches what /v1/messages would send."""
        return "/v1/messages/count_tokens", self._rewrite(inbound)

    def json_response(self, inbound: InboundRequest, status: int, body: bytes) -> Tuple[int, dict]:
        """2xx: tool-use-input on content[] and model = the requested model (KD-16).
        Non-2xx: the KD-14 status; llama.cpp's {"error":{"code","message","type"}} or
        flat shape becomes the envelope typed by that status, an Anthropic envelope
        keeps its type (unless KD-8 remapped a 401/403); the message is scrubbed (KD-9)."""
        name = inbound.backend.name
        if 200 <= status < 300:
            try:
                obj = _rt_loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                return 502, _rt_error_body("api_error", "backend %s answered malformed JSON" % name)
            if not isinstance(obj, dict):
                return 502, _rt_error_body("api_error", "backend %s answered a non-object" % name)
            answer = dict(obj)
            content = obj.get("content")
            if isinstance(content, list) and "tool-use-input" not in inbound.route.options.get("quirks_off", ()):
                answer["content"] = [
                    _rt_ll_tool_use_input("content_block_start", {"content_block": block},
                                          inbound.scrub)["content_block"]
                    for block in content]
            if inbound.endpoint == "messages":
                answer["model"] = inbound.requested_model
            return status, answer
        mapped = _UPSTREAM_STATUS.get(status, 502)
        err_type = _STATUS_TO_TYPE.get(mapped, "api_error")
        if status in (401, 403):
            log.warning("upstream credential rejected: backend %s", _log_value(name))
            return mapped, _rt_error_body(err_type, "backend %s rejected the router's credential" % name)
        message = "backend %s answered %d" % (name, status)
        try:
            obj = _rt_loads(body.decode("utf-8"))
            if isinstance(obj, dict):
                err = obj.get("error")
                source = err if isinstance(err, dict) else obj
                if obj.get("type") == "error" and isinstance(err, dict) and err.get("type") in _ANTHROPIC_ERROR_TYPES:
                    err_type = err["type"]
                if isinstance(source.get("message"), str):
                    message = inbound.scrub(source["message"])
        except (UnicodeDecodeError, ValueError, RecursionError):
            pass
        return mapped, _rt_error_body(err_type, message)

    def stream_translator(self, inbound: InboundRequest) -> "LlamacppRelay":
        return LlamacppRelay(inbound)


class LlamacppRelay(EventRelay):
    """The llama.cpp stream relay: EventRelay plus the enabled event quirks and the model echo.

    Each whole event's `data` is parsed; the event quirks the route does not
    switch off run on it, and message_start.message.model becomes the requested
    model (KD-16). Only an event that changed is re-serialised (`event: <e>` /
    `data: <json>`); every other event is relayed byte for byte, as is one whose
    data is not a JSON object. An upstream `event: error`, and any event whose
    data is an object with an "error" key whatever its name (V16), goes through
    the error-shape row first and then EventRelay's error path. Block-stop order and
    block nesting are not checked (llama.cpp may close every block at the end);
    the encoder is not used.
    """

    def __init__(self, inbound: InboundRequest) -> None:
        super().__init__(inbound)
        self.requested_model = inbound.requested_model
        off = inbound.route.options.get("quirks_off", ())
        self.quirks = tuple(fn for name, _ref, fn in LLAMACPP_EVENT_QUIRKS if name not in off)

    def _repair(self, name: str, data: dict) -> dict:
        """The enabled event quirks, then the model echo; the same object when nothing changed."""
        for fn in self.quirks:
            data = fn(name, data, self.scrub)
        if name == "message_start":
            message = data.get("message")
            if isinstance(message, dict) and message.get("model") != self.requested_model:
                message = dict(message)
                message["model"] = self.requested_model
                data = dict(data)
                data["message"] = message
        return data

    def _feed_line(self, line: bytes) -> bytes:
        """One SSE line -> b"" or one whole event's bytes (repaired when a quirk applied)."""
        if self.closed:
            return b""
        text = line.rstrip(b"\r\n")
        if text.startswith(b":"):
            return b""
        if text:
            self.lines.append(line)
            self.size += len(line)
            if self.size > _SSE_EVENT_LIMIT:
                return self.fail("api_error", "backend sent an oversized event")
            return b""
        if not self.lines:
            return b""
        accumulated = self.lines + [line]
        self.lines, self.size = [], 0
        out = b""
        for name, data, raw in _rt_sse_events(accumulated):
            try:
                parsed = _rt_loads(data)
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, dict) and "error" in parsed:
                # A data-only (or otherwise named) {"error": ...} is an error too: it
                # goes through the error-shape row and the scrub, never raw (V16).
                name = "error"
            repaired = self._repair(name, parsed) if isinstance(parsed, dict) else parsed
            if name == "error":
                return out + self._upstream_error(data if repaired is parsed else _rt_dumps(repaired).decode("ascii"))
            self.started = True
            if repaired is not parsed:
                raw = (b"event: " + name.encode("utf-8", "replace") + b"\ndata: "
                       + _rt_dumps(repaired) + b"\n\n")
            out += raw
            if name == "message_stop":
                self.closed = True
        return out


# --- Mistral tool ids (KD-6) ---
# The map is a pure function of the request: one request's tool_calls[].id and
# tool_call_id agree, and a resend of the same history gives the same ids
# across router restarts with no table to persist. sha256 is reached as
# hashlib.sha256 through the module global on every call (F7 swaps it).
# fullmatch, not match: `$` also matches before a trailing newline.

_B62 = string.digits + string.ascii_letters
_MINTED_RE = re.compile(r"^toolu_lr([A-Za-z0-9]{9})$")
_MS_ID_RE = re.compile(r"^[A-Za-z0-9]{9}$")
_TOOL_ID_PREFIX = b"llm-router/tool-id/v1\0"


def _rt_b62(digest: bytes, width: int) -> str:
    """Base62 of the big-endian integer of *digest*, left-padded with "0" and cut to *width*."""
    value = int.from_bytes(digest, "big")
    chars: List[str] = []
    while value:
        value, rem = divmod(value, 62)
        chars.append(_B62[rem])
    text = "".join(reversed(chars))
    return ("0" * (width - len(text)) + text)[:width]   # not str.rjust: table_cells' alignment sweep


def _rt_ms_tool_id_map(ids: Iterable[str]) -> Dict[str, str]:
    """Anthropic tool-use ids -> 9-alnum Mistral ids, unique within one request (KD-6).

    An id the router minted (toolu_lr + 9 alnum) maps back to exactly its 9
    characters, and those are reserved first. Every other distinct id, in
    sorted order, maps to 9 base62 characters of sha256(prefix + id); one whose
    characters are taken re-hashes sha256(prefix + id + NUL + n) for n = 1, 2,
    ... until unique. The result does not depend on the input order.
    """
    distinct = set(ids)
    mapping: Dict[str, str] = {}
    taken = set()
    others: List[str] = []
    for tool_id in distinct:
        match = _MINTED_RE.fullmatch(tool_id)
        if match:
            mapping[tool_id] = match.group(1)
            taken.add(match.group(1))
        else:
            others.append(tool_id)
    for tool_id in sorted(others):
        raw = _TOOL_ID_PREFIX + tool_id.encode("utf-8", "surrogatepass")
        candidate = _rt_b62(hashlib.sha256(raw).digest(), 9)
        n = 0
        while candidate in taken:
            n += 1
            candidate = _rt_b62(hashlib.sha256(raw + b"\0" + str(n).encode("ascii")).digest(), 9)
        mapping[tool_id] = candidate
        taken.add(candidate)
    return mapping


def _rt_ms_anthropic_id(mistral_id: str) -> str:
    """A Mistral tool-call id -> an Anthropic tool-use id (KD-6).

    A 9-alnum id becomes _MINTED_PREFIX + id, so it round-trips exactly through
    _rt_ms_tool_id_map; any other id (or an empty one, hashed from 16 random
    bytes) becomes "toolu_" + 24 base62 characters of its sha256.
    """
    if _MS_ID_RE.fullmatch(mistral_id):
        return _MINTED_PREFIX + mistral_id
    raw = mistral_id.encode("utf-8", "surrogatepass") if mistral_id else os.urandom(16)
    return "toolu_" + _rt_b62(hashlib.sha256(raw).digest(), 24)


# --- Mistral request translation (Step 10) ---
# _rt_ms_translate builds a NEW chat-completions body from an explicit key list;
# every Anthropic field it does not name (metadata, thinking, top_k,
# cache_control at any depth, service_tier, ...) is dropped by construction, so
# a new Anthropic field can never leak to Mistral (F11). Pure: no I/O, the input
# is never mutated. The Anthropic `thinking` request field is carried only as
# reasoning_effort (Mistral answers 422 extra_forbidden to a `thinking` key).

_RT_MS_ORPHAN = "[tool result for an earlier call]\n"
_RT_MS_IMAGE_OMITTED = "[image omitted]"
_RT_MS_BRIDGE = "Done."
_RT_MS_TOOL_CHOICE = {"auto": "auto", "any": "any", "none": "none"}
# reasoning_effort values Mistral's chat-completions API accepts (the reasoning_effort route option).
_RT_MS_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_RT_MS_EFFORT_MAP_RULE = ("an object of at most %d entries, each key and value one of %s"
                          % (len(_RT_MS_EFFORTS), ", ".join(_RT_MS_EFFORTS)))
# An upstream 400 refusing the reasoning_effort sent (measured live 2026-10-07, mistral-medium-3-5):
# "reasoning_effort low is not supported for this model, supported values: [<ReasoningEffort.high:
# 'high'>, <ReasoningEffort.none: 'none'>]". The supported names are the quoted ones in the list.
_RT_MS_EFFORT_REFUSED_RE = re.compile(r"reasoning_effort (\w{1,16}) is not supported for this model, "
                                      r"supported values: \[([^\]]*)\]")
_RT_MS_EFFORT_NAME_RE = re.compile(r"'([a-z]{1,16})'")
_RT_MS_SIG_PREFIX = "lms1."                 # a router-minted Mistral thinking signature
_RT_MS_SIGNATURE_LIMIT = 1024 * 1024        # longest Mistral signature (UTF-8 bytes) a signature carries
_RT_MS_SIG_RE = re.compile(r"(?:[A-Za-z0-9_-]{4})*(?:[A-Za-z0-9_-]{2}==|[A-Za-z0-9_-]{3}=)?")


def _rt_ms_signature(value: Any) -> Optional[str]:
    """A Mistral thinking chunk's `signature` -> the thinking signature the client keeps:
    _RT_MS_SIG_PREFIX + urlsafe base64 of its UTF-8 (an absent or empty one -> the bare
    prefix). None when it is not a string, not UTF-8 encodable or longer than
    _RT_MS_SIGNATURE_LIMIT bytes (the caller counts it and closes with the bare prefix)."""
    if value is None or value == "":
        return _RT_MS_SIG_PREFIX
    if not isinstance(value, str):
        return None
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(raw) > _RT_MS_SIGNATURE_LIMIT:
        return None
    return _RT_MS_SIG_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def _rt_ms_unsign(value: Any) -> Optional[str]:
    """A thinking signature -> the Mistral signature it carries ("" for the bare prefix), the
    inverse of _rt_ms_signature. None for a missing prefix (another backend's or Anthropic's
    signature) or a payload that is not padded urlsafe base64 of UTF-8 within the limit."""
    if not isinstance(value, str) or not value.startswith(_RT_MS_SIG_PREFIX):
        return None
    payload = value[len(_RT_MS_SIG_PREFIX):]
    if len(payload) > 4 * ((_RT_MS_SIGNATURE_LIMIT + 2) // 3) or not _RT_MS_SIG_RE.fullmatch(payload):
        return None
    try:
        return base64.urlsafe_b64decode(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _rt_ms_str(value: Any, where: str) -> str:
    """*value* when it is a str, else a 400 naming *where*."""
    if not isinstance(value, str):
        raise _rt_bad(where, "must be a string")
    return value


def _rt_ms_unsupported(block_type: str, where: str) -> ApiError:
    """A 400 naming the block type Mistral cannot take (bounded and escaped)."""
    return _rt_bad(where, "block type %s is not supported by a mistral backend" % _log_value(block_type))


def _rt_ms_result_text(block: dict, where: str) -> str:
    """A tool_result's content as one text: a str, or its text blocks joined with
    "\\n" and each image as "[image omitted]"; absent -> ""; is_error -> "Error: " prefix."""
    content = block.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: List[str] = []
        for k, inner in enumerate(content):
            inner_where = "%s.content.%d" % (where, k)
            inner_type = inner.get("type") if isinstance(inner, dict) else None
            if inner_type == "text":
                parts.append(_rt_ms_str(inner.get("text"), inner_where + ".text"))
            elif inner_type == "image":
                parts.append(_RT_MS_IMAGE_OMITTED)
            elif isinstance(inner_type, str):
                raise _rt_ms_unsupported(inner_type, inner_where)
            else:
                raise _rt_bad(inner_where, "must be an object with a string type")
        text = "\n".join(parts)
    else:
        raise _rt_bad(where + ".content", "must be a string or a list of blocks")
    if block.get("is_error") is True:
        text = "Error: " + text
    return text


def _rt_ms_image_part(block: dict, where: str) -> dict:
    """An Anthropic image block -> {"type":"image_url","image_url": data URL | url}."""
    source = block.get("source")
    if not isinstance(source, dict):
        raise _rt_bad(where + ".source", "must be an object")
    if source.get("type") == "base64":
        media = _rt_ms_str(source.get("media_type"), where + ".source.media_type")
        data = _rt_ms_str(source.get("data"), where + ".source.data")
        return {"type": "image_url", "image_url": "data:%s;base64,%s" % (media, data)}
    if source.get("type") == "url":
        return {"type": "image_url", "image_url": _rt_ms_str(source.get("url"), where + ".source.url")}
    raise _rt_bad(where + ".source.type", "must be base64 or url")


def _rt_ms_user_content(parts: List[dict]) -> Union[str, List[dict]]:
    """User parts -> a plain string when they are all text (joined with "\\n\\n"), else the list."""
    if all(part["type"] == "text" for part in parts):
        return "\n\n".join(part["text"] for part in parts)
    return parts


def _rt_ms_merge_user(first: dict, second: dict) -> dict:
    """Two consecutive user messages -> one: strings joined with "\\n\\n", else parts concatenated."""
    a, b = first["content"], second["content"]
    if isinstance(a, str) and isinstance(b, str):
        return {"role": "user", "content": a + "\n\n" + b}
    as_parts = [{"type": "text", "text": a}] if isinstance(a, str) else list(a)
    bs_parts = [{"type": "text", "text": b}] if isinstance(b, str) else list(b)
    return {"role": "user", "content": as_parts + bs_parts}


def _rt_ms_tool_choice(choice: Any) -> Any:
    """Anthropic tool_choice -> Mistral's: auto/any/none as strings, tool -> function by name."""
    kind = choice.get("type") if isinstance(choice, dict) else None
    if kind in _RT_MS_TOOL_CHOICE:
        return _RT_MS_TOOL_CHOICE[kind]
    if kind == "tool":
        return {"type": "function", "function": {"name": _rt_ms_str(choice.get("name"), "tool_choice.name")}}
    raise _rt_bad("tool_choice.type", "must be auto, any, none or tool")


def _rt_ms_opt_effort_map(value: Any, where: str) -> Dict[str, str]:
    """reasoning_effort_map: a JSON object of at most len(_RT_MS_EFFORTS) entries, every key and
    value one of _RT_MS_EFFORTS; the refusal names the path and the rule, never a key or value."""
    if not isinstance(value, dict) or len(value) > len(_RT_MS_EFFORTS) \
            or not all(isinstance(k, str) and k in _RT_MS_EFFORTS and isinstance(v, str) and v in _RT_MS_EFFORTS
                       for k, v in value.items()):
        raise ConfigError(f"{where}: must be {_RT_MS_EFFORT_MAP_RULE}")
    return dict(value)


def _rt_ms_effort_remap(wanted: str, supported: FrozenSet[str]) -> str:
    """*wanted* when *supported* holds it (or is empty); else the supported value nearest by rank in
    _RT_MS_EFFORTS, "none" excluded (thinking was asked for), a tie going to the higher; "none"
    only when it is all *supported* holds (pure)."""
    if not supported or wanted in supported:
        return wanted
    rank = _RT_MS_EFFORTS.index
    thinking = [name for name in supported if name != "none" and name in _RT_MS_EFFORTS]
    if not thinking:
        return "none" if "none" in supported else wanted
    return min(thinking, key=lambda name: (abs(rank(name) - rank(wanted)), -rank(name)))


def _rt_ms_effort(body: dict, route: RouteSpec, supported: FrozenSet[str] = frozenset()) -> Optional[str]:
    """A Mistral body's reasoning_effort: the route's fixed reasoning_effort option as is; else
    _rt_rs_effort's value (budget band, adaptive) through the route's reasoning_effort_map,
    then -- when (backend, model) learned its *supported* set -- _rt_ms_effort_remap. None: omitted."""
    effort = _rt_rs_effort(body, route)
    if effort is None or route.options.get("reasoning_effort") is not None:
        return effort
    effort = route.options.get("reasoning_effort_map", {}).get(effort, effort)
    return _rt_ms_effort_remap(effort, supported)


def _rt_ms_unsupported_effort(body: bytes, sent: Optional[str]) -> Optional[FrozenSet[str]]:
    """The supported set an upstream 400 body lists when it refuses *sent* as the reasoning_effort,
    else None (pure).

    The message is looked for wherever MistralAdapter.json_response finds one (a top-level
    message or detail string, error.message), its first _RT_UNSUPPORTED_SCAN characters
    searched for _RT_MS_EFFORT_REFUSED_RE naming *sent*; the supported set is the quoted names
    of _RT_MS_EFFORTS in its list. None -- relayed, never retried -- when nothing was sent,
    the 400 is another one, no name parses, or the list holds *sent* itself."""
    if sent is None:
        return None
    try:
        obj = _rt_loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    for message in (obj.get("message"), err.get("message") if isinstance(err, dict) else err, obj.get("detail")):
        if not isinstance(message, str):
            continue
        match = _RT_MS_EFFORT_REFUSED_RE.search(message[:_RT_UNSUPPORTED_SCAN])
        if match is None or match.group(1) != sent:
            continue
        names = frozenset(name for name in _RT_MS_EFFORT_NAME_RE.findall(match.group(2)) if name in _RT_MS_EFFORTS)
        return names if names and sent not in names else None
    return None


def _rt_ms_translate(body: dict, route: RouteSpec, effort_supported: FrozenSet[str] = frozenset()) -> dict:
    """The Anthropic /v1/messages body -> a NEW Mistral chat-completions body (_rt_ms_translate_counted's)."""
    return _rt_ms_translate_counted(body, route, effort_supported)[0]


def _rt_ms_translate_counted(body: dict, route: RouteSpec,
                             effort_supported: FrozenSet[str] = frozenset()) -> Tuple[dict, int]:
    """The Anthropic /v1/messages body -> (a NEW Mistral chat-completions body, dropped thinking) (plan Step 10).

    Only the keys built here are sent: model, messages, max_tokens, temperature,
    top_p, stop, stream, tools, tool_choice, parallel_tool_calls, reasoning_effort
    (_rt_ms_effort's value: the route's fixed option, else the Responses kinds' band table
    through reasoning_effort_map and the learned *effort_supported* remap; omitted when None).
    System text, then every mid-conversation system entry's in order ("\\n\\n"
    between), becomes the one first message (consecutive user messages left by a
    removed entry merge as any do); assistant tool_use blocks become tool_calls with
    KD-6 ids and JSON-string arguments; an assistant turn's thinking blocks whose
    signature _rt_ms_unsign accepts become ONE Mistral thinking chunk (texts joined
    with "\\n\\n", the last non-empty signature) before its text chunk -- the content
    is then a list, else a plain string; every other thinking and every
    redacted_thinking block is dropped and counted in the returned int. User
    tool_result blocks become tool messages, an orphan one (no tool_use in the
    history) a user text; a tool message directly followed by a user message gets
    an assistant "Done." bridge. A block type Mistral cannot take is a 400 naming the type.
    """
    options = route.options
    dropped = 0
    messages: List[dict] = []
    system_texts: List[str] = []
    system = body.get("system")
    if system is not None:
        if isinstance(system, str):
            system_text = system
        elif isinstance(system, list):
            system_text = "\n\n".join(
                _rt_ms_str(b.get("text"), "system.%d.text" % i) for i, b in enumerate(system)
                if isinstance(b, dict) and b.get("type") == "text")
        else:
            raise _rt_bad("system", "must be a string or a list of text blocks")
        if system_text:
            system_texts.append(system_text)

    history = body["messages"]
    # Every mid-conversation system entry collapses into the one leading system
    # message, in order; the history loop below skips them.
    system_texts += [_rt_system_entry_text(message, "messages.%d" % i)
                     for i, message in enumerate(history) if message["role"] == "system"]
    system_texts = [text for text in system_texts if text]
    if system_texts:
        messages.append({"role": "system", "content": "\n\n".join(system_texts)})
    tool_names: Dict[str, str] = {}
    ids: List[str] = []
    for i, message in enumerate(history):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for j, block in enumerate(content):
            where = "messages.%d.content.%d" % (i, j)
            if message.get("role") == "assistant" and block.get("type") == "tool_use":
                tool_id = _rt_ms_str(block.get("id"), where + ".id")
                tool_names[tool_id] = _rt_ms_str(block.get("name"), where + ".name")
                ids.append(tool_id)
            elif message.get("role") == "user" and block.get("type") == "tool_result":
                ids.append(_rt_ms_str(block.get("tool_use_id"), where + ".tool_use_id"))
    id_map = _rt_ms_tool_id_map(ids)

    for i, message in enumerate(history):
        role, content = message["role"], message["content"]
        if role == "system":
            continue                # collapsed into the leading system message above
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts: List[str] = []
            calls: List[dict] = []
            thoughts: List[str] = []
            signature = ""
            for j, block in enumerate(content):
                where = "messages.%d.content.%d" % (i, j)
                block_type = block["type"]
                if block_type == "text":
                    texts.append(_rt_ms_str(block.get("text"), where + ".text"))
                elif block_type == "tool_use":
                    tool_input = block.get("input")
                    if tool_input is None:
                        tool_input = {}
                    calls.append({"id": id_map[block["id"]], "type": "function",
                                  "function": {"name": block["name"],
                                               "arguments": json.dumps(tool_input, separators=(",", ":"),
                                                                       ensure_ascii=False)}})
                elif block_type == "thinking":
                    carried = _rt_ms_unsign(block.get("signature"))
                    if carried is None or not isinstance(block.get("thinking"), str):
                        dropped += 1
                        continue
                    thoughts.append(block["thinking"])
                    signature = carried or signature
                elif block_type == "redacted_thinking":
                    dropped += 1
                else:
                    raise _rt_ms_unsupported(block_type, where)
            out_message: Dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
            if thoughts:
                chunk: Dict[str, Any] = {"type": "thinking",
                                         "thinking": [{"type": "text", "text": "\n\n".join(thoughts)}]}
                if signature:
                    chunk["signature"] = signature
                parts_out = [chunk]
                if out_message["content"]:
                    parts_out.append({"type": "text", "text": out_message["content"]})
                out_message["content"] = parts_out
            if calls:
                out_message["tool_calls"] = calls
            messages.append(out_message)
            continue
        parts: List[dict] = []
        for j, block in enumerate(content):
            where = "messages.%d.content.%d" % (i, j)
            block_type = block["type"]
            if block_type == "tool_result":
                tool_use_id = block["tool_use_id"]
                text = _rt_ms_result_text(block, where)
                if tool_use_id not in tool_names:
                    parts.append({"type": "text", "text": _RT_MS_ORPHAN + text})
                    continue
                messages.append({"role": "tool", "tool_call_id": id_map[tool_use_id],
                                 "name": tool_names[tool_use_id], "content": text})
            elif block_type == "text":
                parts.append({"type": "text", "text": _rt_ms_str(block.get("text"), where + ".text")})
            elif block_type == "image":
                parts.append(_rt_ms_image_part(block, where))
            else:
                raise _rt_ms_unsupported(block_type, where)
        if parts:
            messages.append({"role": "user", "content": _rt_ms_user_content(parts)})

    # Drop a message with neither content nor tool_calls, merge consecutive user
    # messages, then bridge every tool -> user step with an assistant "Done.".
    final: List[dict] = []
    for message in messages:
        if not message.get("content") and not message.get("tool_calls"):
            continue
        if final and message["role"] == "user" and final[-1]["role"] == "user":
            final[-1] = _rt_ms_merge_user(final[-1], message)
            continue
        if final and message["role"] == "user" and final[-1]["role"] == "tool":
            final.append({"role": "assistant", "content": _RT_MS_BRIDGE})
        final.append(message)

    out: Dict[str, Any] = {"model": route.model, "messages": final}
    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        cap = options.get("max_tokens_cap")
        capped = cap is not None and isinstance(max_tokens, int) and not isinstance(max_tokens, bool)
        out["max_tokens"] = min(max_tokens, cap) if capped else max_tokens
    for key in ("temperature", "top_p"):
        value = options[key] if key in options else body.get(key)
        if value is not None:
            out[key] = value
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    if "stream" in body:
        out["stream"] = body["stream"]
    tools = body.get("tools")
    if tools:
        translated_tools = []
        for tool in tools:
            function: Dict[str, Any] = {"name": tool["name"]}
            if "description" in tool:
                function["description"] = tool["description"]
            function["parameters"] = tool.get("input_schema", {"type": "object", "properties": {}})
            translated_tools.append({"type": "function", "function": function})
        out["tools"] = translated_tools
    choice = body.get("tool_choice")
    if choice is not None:
        out["tool_choice"] = _rt_ms_tool_choice(choice)
        if choice.get("disable_parallel_tool_use") is True:
            out["parallel_tool_calls"] = False
    effort = _rt_ms_effort(body, route, effort_supported)
    if effort is not None:
        out["reasoning_effort"] = effort
    return out, dropped


# --- Mistral stream translation (Step 11) ---
# chat-completions SSE -> Anthropic SSE. Text streams through as it arrives;
# tool calls are buffered whole (KD-5) and emitted at finish() in key order,
# because Mistral may interleave the fragments of parallel calls.

_RT_MS_STOP_REASONS = {"stop": "end_turn", "length": "max_tokens",
                       "tool_calls": "tool_use", "model_length": "max_tokens"}


def _rt_ms_error_type(err: dict) -> str:
    """An upstream error object's Anthropic type: its own when Anthropic's, else
    derived from an HTTP-status-like `code` (KD-14), else api_error."""
    err_type = err.get("type")
    if err_type in _ANTHROPIC_ERROR_TYPES:
        return err_type
    code = err.get("code")
    if isinstance(code, str) and code.isdigit() and len(code) == 3:
        code = int(code)
    if isinstance(code, int) and not isinstance(code, bool) and code in _UPSTREAM_STATUS:
        return _STATUS_TO_TYPE.get(_UPSTREAM_STATUS[code], "api_error")
    return "api_error"


def _rt_ms_tool_key(key: Union[int, str]) -> Tuple[int, Union[int, str]]:
    """Sort key of the tool buffer: integer indices first, in order, then ids."""
    return (0, key) if isinstance(key, int) else (1, key)


_RT_UNOFFERED_TOOL = "backend returned a call to a tool that was not offered"


def _rt_offered_tools(inbound: InboundRequest) -> FrozenSet[str]:
    """F26: the tool names *inbound* offered. A translated tool call (mistral, codex, openai)
    naming any other is the backend's protocol violation, refused as _RT_UNOFFERED_TOOL --
    the name is never echoed. passthrough and llamacpp relay the backend's own Anthropic
    tool_use blocks and never mint one, so the rule is not theirs (R-0093)."""
    tools = inbound.body.get("tools") if isinstance(inbound.body, dict) else None
    return frozenset(tool["name"] for tool in (tools if isinstance(tools, list) else ())
                     if isinstance(tool, dict) and isinstance(tool.get("name"), str))


class _RtMsContent:
    """A chat-completions `content` (a str, or a list of typed chunks, in a stream delta or a
    whole message) fed into one sink -- an AnthropicSseEncoder or an _RtMessageCollector, so
    the stream and the non-stream answer share one rule. A text part is text (an empty one
    is ignored); a thinking chunk's inner text parts are thinking, merged into ONE thinking
    block until a text part arrives or close() runs (`closed` is no end marker). close()
    ends the open thinking block with _rt_ms_signature of the last non-empty Mistral
    signature it saw. dropped counts what cannot be carried: a thinking chunk whose
    `thinking` is not a list, a part of another type, an unusable signature."""

    def __init__(self, sink: Any) -> None:
        self.sink = sink
        self.signature: Any = None      # the open thinking block's last non-empty Mistral signature
        self.dropped = 0

    def feed(self, content: Any) -> bytes:
        """One content value -> the sink's bytes."""
        if isinstance(content, str):
            return self._text(content)
        if not isinstance(content, list):
            return b""
        out = b""
        for part in content:
            kind = part.get("type") if isinstance(part, dict) else None
            if kind == "text":
                if isinstance(part.get("text"), str):
                    out += self._text(part["text"])
            elif kind == "thinking" and isinstance(part.get("thinking"), list):
                text = "".join(inner["text"] for inner in part["thinking"]
                               if isinstance(inner, dict) and inner.get("type") == "text"
                               and isinstance(inner.get("text"), str))
                if text:
                    out += self.sink.thinking(text)
                if part.get("signature") not in (None, "") and self.sink.open_kind == "thinking":
                    self.signature = part["signature"]
            else:
                self.dropped += 1
        return out

    def _text(self, text: str) -> bytes:
        if not text:
            return b""
        return self.close() + self.sink.text(text)

    def close(self) -> bytes:
        """End the open thinking block (nothing when none is open) with its minted signature."""
        if self.sink.open_kind != "thinking":
            return b""
        signature = _rt_ms_signature(self.signature)
        self.signature = None
        if signature is None:
            self.dropped += 1
            signature = _RT_MS_SIG_PREFIX
        return self.sink.thinking_end(signature)


class MistralStreamTranslator:
    """The mistral stream translator (sans-IO, KD-5, KD-7, KD-16).

    Owns one AnthropicSseEncoder (a fresh msg_ id, the requested model). begin()
    is message_start; feed(line) takes one upstream SSE line; finish() runs once,
    on `data: [DONE]` or at EOF, and emits the buffered tool calls in key order
    before message_delta / message_stop. Every failure is one `event: error` and
    closes the stream -- never a message_stop after it. Every method returns b""
    once closed. delta.content goes through _RtMsContent: thinking streams as one
    thinking block, closed when a text part or a tool call arrives, or at finish().
    dropped_thinking starts at the request's dropped thinking blocks and adds the
    content parts _RtMsContent could not carry.
    """

    def __init__(self, inbound: InboundRequest, input_tokens: int, dropped: int = 0) -> None:
        self.encoder = AnthropicSseEncoder(_rt_message_id(), inbound.requested_model)
        self.content = _RtMsContent(self.encoder)
        self.scrub = inbound.scrub
        self.input_tokens = input_tokens
        self.tools: Dict[Union[int, str], Dict[str, Any]] = {}   # key -> {"id", "name", "args": [str]}
        self.args_bytes = 0
        self.finish_reason: Optional[str] = None
        self.usage: Dict[str, Any] = {}
        self.seen_chunk = False
        self.request_dropped = dropped
        self.offered = _rt_offered_tools(inbound)   # F26: a call naming another tool is refused

    @property
    def dropped_thinking(self) -> int:
        return self.request_dropped + self.content.dropped

    @property
    def started(self) -> bool:
        return self.encoder.started

    @property
    def closed(self) -> bool:
        return self.encoder.closed

    def begin(self) -> bytes:
        """message_start with the local input-token estimate."""
        return self.encoder.start(self.input_tokens)

    def ping(self) -> bytes:
        """event: ping."""
        return self.encoder.ping()

    def fail(self, err_type: str, message: str) -> bytes:
        """One event: error; closes the stream. The buffered tools are discarded."""
        if self.closed:
            return b""
        self.tools, self.args_bytes = {}, 0
        return self.encoder.error(err_type, message)

    def feed(self, line: bytes) -> bytes:
        """One upstream line -> the bytes to send (often b"")."""
        if self.closed:
            return b""
        text = line.rstrip(b"\r\n")
        if not text.startswith(b"data:"):
            return b""                      # blank line, `:` comment, event: / id: / retry:
        payload = text[len(b"data:"):]
        if payload.startswith(b" "):
            payload = payload[1:]
        if payload.strip() == b"[DONE]":
            return self.finish()
        try:
            chunk = _rt_loads(payload)
        except (UnicodeDecodeError, ValueError, RecursionError):
            chunk = None
        if not isinstance(chunk, dict):
            return self.fail("api_error", "backend sent a malformed stream chunk")
        error = self._chunk_error(chunk)
        if error is not None:
            return self.fail(*error)
        self.seen_chunk = True
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(choice, dict):
            return b""
        if isinstance(choice.get("finish_reason"), str):
            self.finish_reason = choice["finish_reason"]   # the last non-null wins
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return b""
        out = self.content.feed(delta.get("content"))
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            if tool_calls:
                out += self.content.close()
            for item in tool_calls:
                refusal = self._buffer_tool(item)
                if refusal is not None:
                    return out + self.fail("api_error", refusal)
        return out

    def _chunk_error(self, chunk: dict) -> Optional[Tuple[str, str]]:
        """(type, scrubbed message) when *chunk* is an upstream error, else None."""
        if chunk.get("object") == "error":
            err = chunk
        elif "error" in chunk:
            err = chunk["error"] if isinstance(chunk["error"], dict) else {"message": chunk["error"]}
        elif "message" in chunk and "choices" not in chunk:
            err = chunk
        else:
            return None
        message = err.get("message")
        if isinstance(message, str) and message:
            message = self.scrub(message)
        else:
            message = "backend sent an error"
        return _rt_ms_error_type(err), message

    def _buffer_tool(self, item: Any) -> Optional[str]:
        """Buffer one delta.tool_calls item; a refusal message, or None."""
        if not isinstance(item, dict):
            return "backend sent a malformed tool call"
        key = item.get("index")
        if not isinstance(key, int) or isinstance(key, bool):
            key = item.get("id")
            if not isinstance(key, str) or not key:
                return "backend sent a tool call without an index or id"
        function = item.get("function")
        function = function if isinstance(function, dict) else {}
        if key not in self.tools and len(self.tools) >= _RT_BUFFERED_TOOL_COUNT:
            return "backend sent too many tool calls"
        tool = self.tools.setdefault(key, {"id": "", "name": "", "args": []})
        for field, value in (("id", item.get("id")), ("name", function.get("name"))):
            if isinstance(value, str) and value:
                if field == "name" and value not in self.offered:
                    return _RT_UNOFFERED_TOOL       # F26: refused before it is buffered
                if not tool[field]:
                    # The stored id and name count against the arguments' cap (V33).
                    self.args_bytes += len(value.encode("utf-8", "surrogatepass"))
                    if self.args_bytes > _RT_BUFFERED_TOOL_LIMIT:
                        return "backend tool calls too large"
                    tool[field] = value
                elif tool[field] != value:
                    return "backend sent a conflicting tool call"
        args = function.get("arguments")
        if isinstance(args, dict):
            args = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
        elif args is not None and not isinstance(args, str):
            return "backend returned malformed tool arguments"      # a list is not an object (V31)
        if isinstance(args, str) and args:
            self.args_bytes += len(args.encode("utf-8", "surrogatepass"))
            if self.args_bytes > _RT_BUFFERED_TOOL_LIMIT:
                return "backend tool arguments too large"
            tool["args"].append(args)
        return None

    def finish(self) -> bytes:
        """The end of the stream ([DONE] or EOF), once: the tools in key order,
        then message_delta and message_stop -- or one event: error."""
        if self.closed:
            return b""
        if not self.seen_chunk:
            return self.fail("api_error", "backend returned an empty stream")
        if self.finish_reason is None:
            return self.fail("api_error", "backend ended the stream early")
        if self.finish_reason == "error":
            return self.fail("api_error", "backend reported an error")
        blocks: List[Tuple[str, str, str]] = []
        for key in sorted(self.tools, key=_rt_ms_tool_key):
            tool = self.tools[key]
            # A JSON object, strictly (no NaN/Infinity), as on the non-stream path;
            # re-dumped so the client gets exactly what was checked (V31).
            try:
                tool_input = _rt_loads("".join(tool["args"]) or "{}")
                args_json = _rt_dumps(tool_input).decode("ascii") if isinstance(tool_input, dict) else ""
            except (ValueError, RecursionError):
                args_json = ""
            if not args_json:
                return self.fail("api_error", "backend returned malformed tool arguments")
            if not tool["name"]:
                return self.fail("api_error", "backend returned a tool call without a name")
            blocks.append((_rt_ms_anthropic_id(tool["id"]), tool["name"], args_json))
        self.tools, self.args_bytes = {}, 0
        out = self.content.close()
        out += b"".join(self.encoder.tool_block(*block) for block in blocks)
        usage: Dict[str, int] = {"output_tokens": 0}
        completion = self.usage.get("completion_tokens")
        if isinstance(completion, int) and not isinstance(completion, bool):
            usage["output_tokens"] = completion
        prompt = self.usage.get("prompt_tokens")
        if isinstance(prompt, int) and not isinstance(prompt, bool):
            usage["input_tokens"] = prompt
        return out + self.encoder.finish(_RT_MS_STOP_REASONS.get(self.finish_reason, "end_turn"), usage)


def _rt_ms_estimate(translated: dict, route: RouteSpec) -> int:
    """The local input-token estimate (D4): UTF-8 bytes of the translated body //
    count_divisor (default 4), at least 1. These bytes are never sent, so
    surrogatepass cannot put invalid UTF-8 on the wire."""
    size = len(json.dumps(translated, separators=(",", ":"), ensure_ascii=False)
               .encode("utf-8", "surrogatepass"))
    return max(1, size // route.options.get("count_divisor", 4))


_RT_MS_DETAIL_NAMES = 16        # 422 detail: at most this many field names are named
_RT_MS_NAME_WIDTH = 64          # and each is cut to this many characters
_RT_MS_PENDING_CAP = 64         # MistralAdapter: stream estimates awaiting stream_translator (G-M2)


def _rt_ascii_name(name: str) -> str:
    """A field name as printable ASCII only (control and non-ASCII characters dropped)."""
    return "".join(ch for ch in name if " " < ch <= "~")


def _rt_ms_detail_names(detail: List[Any]) -> str:
    """A 422 `detail` list flattened to the rejected field names: the last string
    element of each entry's `loc`, deduplicated in order. Never a value, never the
    upstream's `msg` text (it may echo the request)."""
    names: List[str] = []
    for entry in detail:
        loc = entry.get("loc") if isinstance(entry, dict) else None
        if not isinstance(loc, list):
            continue
        name = next((part for part in reversed(loc) if isinstance(part, str) and part), None)
        if name is not None:
            name = _rt_ascii_name(name)[:_RT_MS_NAME_WIDTH]
            if name and name not in names:
                names.append(name)
    if not names:
        return ""
    shown = names[:_RT_MS_DETAIL_NAMES]
    return "invalid request fields: " + ", ".join(shown) + (", ..." if len(names) > len(shown) else "")


def _rt_ms_tool_use(call: Any) -> Optional[dict]:
    """One choices[0].message.tool_calls item as an Anthropic tool_use block
    (KD-6 id), or None when it is malformed (no name, arguments not a JSON object)."""
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
        return None
    args = function.get("arguments")
    if isinstance(args, dict):
        tool_input: Any = args
    elif args is None or isinstance(args, str):
        try:
            tool_input = _rt_loads(args or "{}")
        except (ValueError, RecursionError):
            return None
    else:
        return None
    if not isinstance(tool_input, dict):
        return None
    call_id = call.get("id") if isinstance(call.get("id"), str) else ""
    return {"type": "tool_use", "id": _rt_ms_anthropic_id(call_id), "name": function["name"], "input": tool_input}


class MistralAdapter(Adapter):
    """kind mistral: the chat-completions translation (KD-5, KD-6, KD-14, KD-16)."""

    kind = "mistral"
    FORWARDABLE: FrozenSet[str] = frozenset()
    DEFAULT_FORWARD: FrozenSet[str] = frozenset()
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {
        "count_divisor": _rt_opt_int(1, 16),
        "temperature": _RT_TEMPERATURE,
        "top_p": _RT_TOP_P,
        "max_tokens_cap": _rt_opt_int(1),
        "reasoning_effort": _rt_opt_enum(_RT_MS_EFFORTS),
        "reasoning_effort_map": _rt_ms_opt_effort_map,
    }

    def __init__(self) -> None:
        # G-M2: one translation per request. upstream_request leaves a stream
        # request's (estimate, dropped thinking) here, keyed by id(inbound), and
        # stream_translator (called next, on the same live inbound) pops it. Only
        # int pairs are kept, at most _RT_MS_PENDING_CAP of them: an entry orphaned
        # by a failed send is evicted oldest-first, and a miss only costs a second
        # translation. No lock and no threading (unit 5 is pure, J4): single dict
        # operations are atomic, and the eviction tolerates a concurrent change.
        self._pending: Dict[int, Tuple[int, int]] = {}

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        """POST /v1/chat/completions with _rt_ms_translate's body (the learned effort set applied)."""
        translated, dropped = _rt_ms_translate_counted(inbound.body, inbound.route, inbound.effort_supported)
        if inbound.stream:
            self._pending[id(inbound)] = (_rt_ms_estimate(translated, inbound.route), dropped)
            while len(self._pending) > _RT_MS_PENDING_CAP:
                try:
                    self._pending.pop(next(iter(self._pending)), None)
                except (StopIteration, RuntimeError):
                    break
        return "/v1/chat/completions", _rt_dumps(translated)

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        """A local estimate (D4, _rt_ms_estimate); one translation per request (G-M2)."""
        return {"input_tokens": _rt_ms_estimate(_rt_ms_translate(inbound.body, inbound.route), inbound.route)}

    def effort_sent(self, inbound: InboundRequest) -> Optional[str]:
        """The computed reasoning_effort the upstream body carries (learnable); None without one
        or under the route's fixed reasoning_effort option, which is sent as the operator chose it."""
        if inbound.route.options.get("reasoning_effort") is not None:
            return None
        return _rt_ms_effort(inbound.body, inbound.route, inbound.effort_supported)

    def effort_remapped(self, inbound: InboundRequest) -> Optional[str]:
        """The reasoning_effort sent when reasoning_effort_map or the learned set changed it; else None."""
        sent = self.effort_sent(inbound)
        return sent if sent is not None and sent != _rt_rs_effort(inbound.body, inbound.route) else None

    def json_response(self, inbound: InboundRequest, status: int, body: bytes) -> Tuple[int, dict]:
        """2xx: choices[0].message as an Anthropic message (model = the requested
        model, KD-16; tool ids by KD-6; malformed tool arguments -> 502).
        Non-2xx: the KD-14 status typed by _STATUS_TO_TYPE; a 422 detail list is
        flattened to field names (no values), any other message is scrubbed (KD-9)."""
        name = inbound.backend.name
        try:
            obj = _rt_loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            obj = None
        if 200 <= status < 300:
            return self._message(inbound, status, obj)
        mapped = _UPSTREAM_STATUS.get(status, 502)
        err_type = _STATUS_TO_TYPE.get(mapped, "api_error")
        if status in (401, 403):
            log.warning("upstream credential rejected: backend %s", _log_value(name))
            return mapped, _rt_error_body(err_type, "backend %s rejected the router's credential" % name)
        message = "backend %s answered %d" % (name, status)
        if isinstance(obj, dict):
            source = obj.get("message")
            err = obj.get("error")
            detail = source.get("detail") if isinstance(source, dict) else obj.get("detail")
            if isinstance(detail, list):
                message = _rt_ms_detail_names(detail) or message
            elif isinstance(source, str) and source:
                message = source
            elif isinstance(err, dict) and isinstance(err.get("message"), str) and err["message"]:
                message = err["message"]
            elif isinstance(detail, str) and detail:
                message = detail
        return mapped, _rt_error_body(err_type, inbound.scrub(message))

    @staticmethod
    def _message(inbound: InboundRequest, status: int, obj: Any) -> Tuple[int, dict]:
        """A 2xx chat-completions answer as an Anthropic message, or a 502 envelope."""
        name = inbound.backend.name
        choices = obj.get("choices") if isinstance(obj, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            return 502, _rt_error_body("api_error", "backend %s answered malformed JSON" % name)
        finish_reason = choice.get("finish_reason")
        if finish_reason == "error":
            return 502, _rt_error_body("api_error", "backend %s reported an error" % name)
        # The stream's content rule, folded into a collector: thinking chunks -> one thinking
        # block (lms1. signature) until a text part, text parts -> one text block.
        collector = _RtMessageCollector("", inbound.requested_model)
        parts = _RtMsContent(collector)
        parts.feed(message.get("content"))
        parts.close()
        content: List[dict] = collector.message()["content"]
        tool_calls = message.get("tool_calls")
        offered = _rt_offered_tools(inbound)
        for call in tool_calls if isinstance(tool_calls, list) else ():
            block = _rt_ms_tool_use(call)
            if block is None:
                return 502, _rt_error_body("api_error", "backend returned malformed tool arguments")
            if block["name"] not in offered:     # F26; the name is not echoed
                return 502, _rt_error_body("api_error", _RT_UNOFFERED_TOOL)
            content.append(block)
        if any(block["type"] == "tool_use" for block in content):
            stop_reason = "tool_use"
        else:
            stop_reason = _RT_MS_STOP_REASONS.get(finish_reason, "end_turn") \
                if isinstance(finish_reason, str) else "end_turn"
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
        counts = {}
        for key, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
            value = usage.get(field)
            counts[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        return status, {"id": _rt_message_id(), "type": "message", "role": "assistant",
                        "model": inbound.requested_model, "content": content,
                        "stop_reason": stop_reason, "stop_sequence": None, "usage": counts}

    def stream_translator(self, inbound: InboundRequest) -> "MistralStreamTranslator":
        """The stream translator with the local input-token estimate and the request's dropped
        thinking blocks, taken from upstream_request when it translated this very request (G-M2)."""
        pending = self._pending.pop(id(inbound), None)
        if pending is None:
            translated, dropped = _rt_ms_translate_counted(inbound.body, inbound.route)
            pending = (_rt_ms_estimate(translated, inbound.route), dropped)
        return MistralStreamTranslator(inbound, *pending)


# --- The Responses vocabulary as data (still unit 5; KD-1, KD-2) ---
# Per-dialect differences live in rows, never in kind branches: the three
# ResponsesProfile rows, one identity row, the effort bands and the error-code
# map. Pure data, checked at import; unit 5 stays sans-IO (J4c).

# M1 (measured): the omp client at tag v18.6.1 (commit 2a2c6dcbbb558c0f8145f67f28b3370984f2bf60)
# sends this User-Agent with originator "omp" and version "0.159.0".
_RT_OMP_USER_AGENT = "omp/18.6.1"
# KD-2: the identity headers a profile with identity=True sends; a backend may override a key.
_RT_CODEX_IDENTITY: Tuple[Tuple[str, str], ...] = (
    ("originator", "omp"),
    ("user-agent", _RT_OMP_USER_AGENT),
    ("version", "0.159.0"),
)

_RT_RS_SSE_LINE_LIMIT = 8 * 1024 * 1024     # one Responses SSE line (-> StreamPolicy.line_limit)
_RT_RS_SSE_EVENT_LIMIT = 16 * 1024 * 1024   # one Responses SSE event (read only by the translator)

# reasoning.effort values the Responses API accepts (the reasoning_effort route option).
_RT_RS_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
# thinking.budget_tokens -> effort: the first band whose bound the budget does not exceed;
# above the last bound the effort is "xhigh".
_RT_RS_EFFORT_BANDS: Tuple[Tuple[int, str], ...] = ((4096, "low"), (16384, "medium"), (32768, "high"))


def _rt_rs_error_codes() -> Dict[str, Tuple[int, str]]:
    """Upstream error code -> (status, Anthropic error type) for the codes the router types itself."""
    codes: Dict[str, Tuple[int, str]] = {}
    for code in ("usage_limit_reached", "usage_not_included", "rate_limit_exceeded",
                 "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"):
        codes[code] = (429, "rate_limit_error")
    codes["subscription_sharing_route_not_supported"] = (403, "permission_error")
    return codes


_RT_RS_ERROR_CODES = _rt_rs_error_codes()

_RT_RS_SIG_PREFIX = "lrs1."                 # a router-minted thinking signature
_RT_RS_SIGNATURE_LIMIT = 1024 * 1024        # longest signature decoded back to encrypted_content
_RT_RS_REASONING_CAP = 256                  # reasoning items carried back per request
_RT_RS_CALL_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RT_RS_ENC_RE = re.compile(r"[A-Za-z0-9+/=_-]+")
_RT_RS_HASH_PREFIX = "toolu_lrh_"
_RT_RS_CALL_PREFIX = b"llm-router/rs-call-id/v1\0"          # domain of the deterministic call-id hash
_RT_RS_CACHE_KEY_PREFIX = b"llm-router/prompt-cache/v1\0"   # domain of the prompt_cache_key hash
# tool_shape "function": the tools list is the function entries; "namespace": ONE
# namespace tool holding exactly those entries (openai-oauth, SIWC; K23/K24).
_RT_RS_TOOL_SHAPES = ("function", "namespace")
_RT_RS_NAMESPACE = "claude_code"
_RT_RS_NAMESPACE_DESCRIPTION = "The tools of the Claude Code session this request comes from."


def _rt_responses_profiles() -> Dict[str, ResponsesProfile]:
    """The three Responses dialects (Plan Step 5 table); the table is the contract (J27)."""
    line, event = _RT_RS_SSE_LINE_LIMIT, _RT_RS_SSE_EVENT_LIMIT
    rows = (
        ResponsesProfile(name="codex", default_base_url="https://chatgpt.com/backend-api",
                         path="/codex/responses", oauth_provider="codex", sampling=False,
                         account_header=True, identity=True, beta="responses=experimental",
                         require_event_stream=False, line_limit=line, event_limit=event,
                         tool_shape="function"),
        ResponsesProfile(name="openai-oauth", default_base_url="https://api.openai.com",
                         path="/v1/responses", oauth_provider="openai", sampling=False,
                         account_header=False, identity=False, beta="",
                         require_event_stream=False, line_limit=line, event_limit=event,
                         # User decision 2026-10-07 (SIWC preview: function tools must be
                         # grouped in a namespace; checkpoint E confirms it live).
                         tool_shape="namespace"),
        ResponsesProfile(name="openai-apikey", default_base_url="https://api.openai.com",
                         path="/v1/responses", oauth_provider="", sampling=True,
                         account_header=False, identity=False, beta="",
                         require_event_stream=True, line_limit=line, event_limit=event,
                         tool_shape="function"),
    )
    return {row.name: row for row in rows}


_RT_RESPONSES_PROFILES = _rt_responses_profiles()


def _rt_check_responses_profiles(profiles: Mapping[str, ResponsesProfile],
                                 provider_names: Optional[FrozenSet[str]]) -> None:
    """Refuse a profile row that could not work (KD-1); a RuntimeError names the row.

    Each row's name equals its key; its oauth_provider is "" or a known provider
    (the join is skipped when *provider_names* is None -- the provider rows land
    later and the check runs again with them); both limits are positive and the
    line limit does not exceed the event limit; tool_shape is a known shape.
    """
    for key, row in profiles.items():
        if row.name != key:
            raise RuntimeError(f"profile {key}: name {row.name} differs from its key")
        if row.oauth_provider and provider_names is not None and row.oauth_provider not in provider_names:
            raise RuntimeError(f"profile {key}: unknown oauth_provider {row.oauth_provider}")
        if row.line_limit <= 0 or row.event_limit <= 0 or row.line_limit > row.event_limit:
            raise RuntimeError(f"profile {key}: limits must be positive with line_limit <= event_limit")
        if row.tool_shape not in _RT_RS_TOOL_SHAPES:
            raise RuntimeError(f"profile {key}: unknown tool_shape {row.tool_shape}")


_rt_check_responses_profiles(_RT_RESPONSES_PROFILES, None)


# --- Responses request translation (Plan Step 9) ---
# _rt_rs_translate builds a NEW Responses body from an explicit key list, as
# _rt_ms_translate does: every Anthropic field it does not name (metadata,
# top_k, stop_sequences, service_tier, cache_control at any depth, ...) is
# dropped by construction. Pure: no I/O, the input is never mutated, and every
# dialect difference is read from the profile row, never from a kind (KD-1).
# Call ids follow P10: a pure function of the Anthropic id, so one request's
# function_call and function_call_output agree and a resend gives the same ids.

_RT_RS_NO_RESULT = "(no result)"     # the synthetic output of a call left unanswered
_RT_RS_TOOL_CHOICE = {"auto": "auto", "any": "required", "none": "none"}


def _rt_rs_call_id_map(ids: Iterable[str]) -> Dict[str, str]:
    """Anthropic tool-use ids -> Responses call_ids (P10).

    toolu_<x>, with x a safe call id (_RT_RS_CALL_ID_RE), maps to x, so a call id
    _rt_rs_anthropic_id handed out round-trips exactly; any other id maps to
    "call_" + 24 base62 characters of sha256(_RT_RS_CALL_PREFIX + id). Each value
    depends on its id alone, never on the input order.
    """
    mapping: Dict[str, str] = {}
    for tool_id in set(ids):
        rest = tool_id[len("toolu_"):] if tool_id.startswith("toolu_") else None
        if rest is not None and _RT_RS_CALL_ID_RE.fullmatch(rest):
            mapping[tool_id] = rest
        else:
            raw = _RT_RS_CALL_PREFIX + tool_id.encode("utf-8", "surrogatepass")
            mapping[tool_id] = "call_" + _rt_b62(hashlib.sha256(raw).digest(), 24)
    return mapping


def _rt_rs_anthropic_id(call_id: str) -> str:
    """A Responses call_id -> an Anthropic tool-use id (P10): "toolu_" + call_id when it
    is a safe call id (so _rt_rs_call_id_map maps it straight back), else
    _RT_RS_HASH_PREFIX + 22 base62 characters of sha256(_RT_RS_CALL_PREFIX + call_id)."""
    if _RT_RS_CALL_ID_RE.fullmatch(call_id):
        return "toolu_" + call_id
    raw = _RT_RS_CALL_PREFIX + call_id.encode("utf-8", "surrogatepass")
    return _RT_RS_HASH_PREFIX + _rt_b62(hashlib.sha256(raw).digest(), 22)


def _rt_rs_first_user_text(body: dict) -> str:
    """The first user text of the history (a string content or a text block), "" when none."""
    for message in body.get("messages") or ():
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                return block["text"]
    return ""


def _rt_rs_cache_key(body: dict, backend: str) -> str:
    """The prompt_cache_key: "lr-" + 24 base62 characters of sha256(_RT_RS_CACHE_KEY_PREFIX +
    backend + NUL + (metadata.user_id or the first user text)). Deterministic across
    restarts, so a resumed conversation lands on the same upstream cache."""
    metadata = body.get("metadata")
    seed = metadata.get("user_id") if isinstance(metadata, dict) else None
    if not isinstance(seed, str) or not seed:
        seed = _rt_rs_first_user_text(body)
    raw = _RT_RS_CACHE_KEY_PREFIX + (backend + "\0" + seed).encode("utf-8", "surrogatepass")
    return "lr-" + _rt_b62(hashlib.sha256(raw).digest(), 24)


def _rt_rs_identity(backend: BackendSpec) -> Tuple[Tuple[str, str], ...]:
    """The effective identity (KD-2): _RT_CODEX_IDENTITY's pairs in the table's order, each
    replaced by the backend's override when it sets one (validated at load)."""
    overrides = dict(backend.identity)
    return tuple((key, overrides.get(key, default)) for key, default in _RT_CODEX_IDENTITY)


def _rt_rs_unsupported(block_type: str, where: str) -> ApiError:
    """A 400 naming the block type a Responses backend cannot take (bounded and escaped)."""
    return _rt_bad(where, "block type %s is not supported by a responses backend" % _log_value(block_type))


def _rt_rs_image_part(block: dict, where: str) -> dict:
    """An Anthropic image block -> {"type":"input_image","image_url": data URL | url},
    validated by _rt_ms_image_part."""
    return {"type": "input_image", "image_url": _rt_ms_image_part(block, where)["image_url"]}


def _rt_rs_tool_choice(choice: Any) -> Any:
    """Anthropic tool_choice -> the Responses one: auto/any/none as auto/required/none, tool -> function by name."""
    kind = choice.get("type") if isinstance(choice, dict) else None
    if kind in _RT_RS_TOOL_CHOICE:
        return _RT_RS_TOOL_CHOICE[kind]
    if kind == "tool":
        return {"type": "function", "name": _rt_ms_str(choice.get("name"), "tool_choice.name")}
    raise _rt_bad("tool_choice.type", "must be auto, any, none or tool")


def _rt_rs_close(items: List[dict], open_calls: Dict[str, str]) -> None:
    """Answer every still-open call with a synthetic "(no result)" output, then forget them."""
    for call_id in open_calls.values():
        items.append({"type": "function_call_output", "call_id": call_id, "output": _RT_RS_NO_RESULT})
    open_calls.clear()


def _rt_rs_signature(backend: str, enc: Any) -> Optional[str]:
    """A reasoning item's encrypted_content -> the thinking signature the client keeps (KD-6):
    _RT_RS_SIG_PREFIX + backend + "." + enc. None when *enc* is absent, not a string,
    longer than _RT_RS_SIGNATURE_LIMIT or outside _RT_RS_ENC_RE (the caller counts it)."""
    if not isinstance(enc, str) or len(enc) > _RT_RS_SIGNATURE_LIMIT or not _RT_RS_ENC_RE.fullmatch(enc):
        return None
    return _RT_RS_SIG_PREFIX + backend + "." + enc


def _rt_rs_unsign(backend: str, value: Any) -> Optional[str]:
    """A thinking signature (or redacted_thinking data) -> its encrypted_content (KD-6), the
    inverse of _rt_rs_signature. None for a missing prefix (an Anthropic signature), another
    backend's name, no backend part, or content that is empty, outside _RT_RS_ENC_RE or longer
    than _RT_RS_SIGNATURE_LIMIT -- the limit is on the content, so every minted value round-trips."""
    if not isinstance(value, str) or not value.startswith(_RT_RS_SIG_PREFIX):
        return None
    name, dot, enc = value[len(_RT_RS_SIG_PREFIX):].partition(".")
    if not dot or name != backend:
        return None
    if len(enc) > _RT_RS_SIGNATURE_LIMIT or not _RT_RS_ENC_RE.fullmatch(enc):
        return None
    return enc


def _rt_rs_effort(body: dict, route: RouteSpec) -> Optional[str]:
    """reasoning.effort (KD-14): the route's reasoning_effort option always wins; else
    thinking.type "enabled" maps budget_tokens through _RT_RS_EFFORT_BANDS (above the
    last bound "xhigh"), "adaptive" gives "medium", and anything else None (omitted)."""
    option = route.options.get("reasoning_effort")
    if option is not None:
        return option
    thinking = body.get("thinking")
    kind = thinking.get("type") if isinstance(thinking, dict) else None
    if kind == "adaptive":
        return "medium"
    if kind != "enabled":
        return None
    budget = thinking.get("budget_tokens")
    if not isinstance(budget, int) or isinstance(budget, bool):
        return None
    for bound, effort in _RT_RS_EFFORT_BANDS:
        if budget <= bound:
            return effort
    return "xhigh"


def _rt_rs_text_message(role: str, part_type: str, texts: List[str]) -> dict:
    """One message input item: each text as a *part_type* part, in order."""
    return {"type": "message", "role": role, "content": [{"type": part_type, "text": t} for t in texts]}


# Regex constructs the Responses schema validator refuses (measured live 2026-10-07:
# "Invalid JSON schema: regex lookaround is not supported"): lookaround, backreferences.
_RT_RS_SCHEMA_REGEX_REFUSED = re.compile(r"\(\?<?[=!]|\\[1-9]|\\k<")
# The deepest tool input_schema translated, in nested containers (dict or list), the
# schema itself counted as 1 (F22). A real tool schema nests a handful of levels; 64 is
# an order of magnitude of headroom and keeps _rt_rs_schema's recursion (two frames a
# level) far below the interpreter's limit, so a deeper one is the client's 400, never a
# RecursionError the handler answers 500.
_RT_RS_SCHEMA_DEPTH_LIMIT = 64


def _rt_rs_schema(schema: Any, where: str = "input_schema", depth: int = 1) -> Any:
    """*schema* copied without the string `pattern` keywords upstream would refuse.

    One such pattern 400s the whole request, so a client MCP tool carrying one
    (ai-soul's source field) would make every turn fail. Only a string value is a
    regex keyword; a property NAMED "pattern" holds a dict and is kept. A container
    nested deeper than _RT_RS_SCHEMA_DEPTH_LIMIT is a 400 naming *where* (F22).
    """
    if isinstance(schema, (dict, list)) and depth > _RT_RS_SCHEMA_DEPTH_LIMIT:
        raise _rt_bad(where, "must nest at most %d levels" % _RT_RS_SCHEMA_DEPTH_LIMIT)
    if isinstance(schema, dict):
        return {key: _rt_rs_schema(value, where, depth + 1) for key, value in schema.items()
                if not (key == "pattern" and isinstance(value, str)
                        and _RT_RS_SCHEMA_REGEX_REFUSED.search(value))}
    if isinstance(schema, list):
        return [_rt_rs_schema(value, where, depth + 1) for value in schema]
    return schema


def _rt_rs_sampling_asked(body: dict, route: RouteSpec) -> Tuple[str, ...]:
    """The sampling names (of temperature, top_p) the route option or, without one, the client
    asks for -- a None value asks for nothing. What a sampling profile sends, less *unsupported*."""
    options = route.options
    return tuple(key for key in ("temperature", "top_p")
                 if (options[key] if key in options else body.get(key)) is not None)


_RT_UNSUPPORTED_PARAM_RE = re.compile(r"Unsupported parameter: '([A-Za-z_]{1,32})'")
_RT_UNSUPPORTED_SCAN = 1024        # characters of an upstream 400 message the pattern is searched in


def _rt_unsupported_sampling(body: bytes, sent: Tuple[str, ...]) -> Optional[str]:
    """The one name of *sent* an upstream 400 body refuses as unsupported, else None (pure).

    OpenAI's shape {"error": {"message": "Unsupported parameter: '<p>' is not supported
    with this model.", "param": "<p>", "code": "unsupported_parameter"}}: the message
    naming p, or param p with code unsupported_parameter. Any other 400 -- an invalid
    value for p included -- is None: it is relayed, never retried. A message naming p
    beside a present (non-null) param that is not p is None too (F23): the message may
    reflect other text, and the structured field contradicting it is not overruled."""
    if not sent:
        return None
    try:
        obj = _rt_loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(err, dict):
        return None
    param = err.get("param")
    message = err.get("message")
    if isinstance(message, str):
        match = _RT_UNSUPPORTED_PARAM_RE.search(message[:_RT_UNSUPPORTED_SCAN])
        if match is not None and match.group(1) in sent:
            return match.group(1) if param is None or param == match.group(1) else None
    if isinstance(param, str) and param in sent and err.get("code") == "unsupported_parameter":
        return param
    return None


def _rt_rs_translate(body: dict, route: RouteSpec, backend: str,
                     profile: ResponsesProfile, unsupported: FrozenSet[str] = frozenset()) -> Tuple[dict, int]:
    """The Anthropic /v1/messages body -> (a NEW Responses body, dropped signatures) (plan Step 9).

    Only the keys built here are sent: model, instructions, input, tools,
    tool_choice, parallel_tool_calls, store, stream, include, prompt_cache_key,
    reasoning, text, and -- only when profile.sampling -- temperature, top_p and
    max_output_tokens; a sampling name in *unsupported* (learned for this backend
    and model, InboundRequest.sampling_drop) is never sent, not even as the
    route's override. A mid-conversation system entry becomes a developer message
    with one input_text (its text blocks joined with "\\n\\n") at the same position.
    User text and images become input_text / input_image,
    assistant text output_text, tool_use a function_call (P10 call ids, JSON-string
    arguments), tool_result a function_call_output. Repair: a tool_result with no
    open call becomes user text prefixed with _RT_MS_ORPHAN; a call left unanswered
    by the next turn gets a synthetic "(no result)" output. A thinking signature or
    redacted_thinking data that _rt_rs_unsign accepts becomes a reasoning item
    (summary []) before that assistant turn's other items, in block order, at most
    _RT_RS_REASONING_CAP per request; the rest are dropped and counted in the
    returned int (KD-6). Thinking text is never sent. Any other block type is a 400.
    reasoning.effort comes from _rt_rs_effort (KD-14); summary "auto" and include
    are always sent. On a tool_shape "namespace" row the function entries are sent
    as ONE namespace tool (_RT_RS_NAMESPACE); tool_choice is the same either way.
    """
    options = route.options
    out: Dict[str, Any] = {"model": route.model}
    # The top-level `system` becomes instructions; a mid-conversation role "system"
    # entry becomes a "developer" input item at its own position (the history loop).
    system = body.get("system")
    if system is not None:
        if isinstance(system, str):
            system_text = system
        elif isinstance(system, list):
            system_text = "\n\n".join(
                _rt_ms_str(b.get("text"), "system.%d.text" % i) for i, b in enumerate(system)
                if isinstance(b, dict) and b.get("type") == "text")
        else:
            raise _rt_bad("system", "must be a string or a list of text blocks")
        if system_text:
            out["instructions"] = system_text

    history = body["messages"]
    ids: List[str] = []
    for i, message in enumerate(history):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for j, block in enumerate(content):
            where = "messages.%d.content.%d" % (i, j)
            if message.get("role") == "assistant" and block.get("type") == "tool_use":
                ids.append(_rt_ms_str(block.get("id"), where + ".id"))
            elif message.get("role") == "user" and block.get("type") == "tool_result":
                ids.append(_rt_ms_str(block.get("tool_use_id"), where + ".tool_use_id"))
    id_map = _rt_rs_call_id_map(ids)

    items: List[dict] = []
    open_calls: Dict[str, str] = {}     # Anthropic id -> call_id of the last assistant turn, unanswered
    kept = dropped = 0                  # reasoning items carried back / signatures dropped (KD-6)
    for i, message in enumerate(history):
        role, content = message["role"], message["content"]
        if role == "system":
            # At its own position, so the cached prefix before it is unchanged; it
            # answers no open call (a tool_result after it still pairs).
            text = _rt_system_entry_text(message, "messages.%d" % i)
            if text:
                items.append(_rt_rs_text_message("developer", "input_text", [text]))
            continue
        if role == "assistant":
            _rt_rs_close(items, open_calls)
            if isinstance(content, str):
                if content:
                    items.append(_rt_rs_text_message("assistant", "output_text", [content]))
                continue
            texts: List[str] = []
            turn_start = len(items)
            reasoning: List[dict] = []      # this turn's reasoning items, placed before its other items
            for j, block in enumerate(content):
                where = "messages.%d.content.%d" % (i, j)
                block_type = block["type"]
                if block_type in ("thinking", "redacted_thinking"):
                    # KD-6: the signature (or data) carries the encrypted_content back; the
                    # thinking text is never sent upstream.
                    enc = _rt_rs_unsign(backend, block.get("signature" if block_type == "thinking" else "data"))
                    if enc is None or kept >= _RT_RS_REASONING_CAP:
                        dropped += 1
                    else:
                        kept += 1
                        reasoning.append({"type": "reasoning", "encrypted_content": enc, "summary": []})
                elif block_type == "text":
                    texts.append(_rt_ms_str(block.get("text"), where + ".text"))
                elif block_type == "tool_use":
                    if texts:
                        items.append(_rt_rs_text_message("assistant", "output_text", texts))
                        texts = []
                    tool_input = block.get("input")
                    if tool_input is None:
                        tool_input = {}
                    call_id = id_map[block["id"]]
                    items.append({"type": "function_call", "call_id": call_id,
                                  "name": _rt_ms_str(block.get("name"), where + ".name"),
                                  "arguments": _rt_dumps(tool_input).decode("ascii")})
                    open_calls[block["id"]] = call_id
                else:
                    raise _rt_rs_unsupported(block_type, where)
            if texts:
                items.append(_rt_rs_text_message("assistant", "output_text", texts))
            items[turn_start:turn_start] = reasoning
            continue
        outputs: List[dict] = []
        parts: List[dict] = []
        if isinstance(content, str):
            if content:
                parts.append({"type": "input_text", "text": content})
        else:
            for j, block in enumerate(content):
                where = "messages.%d.content.%d" % (i, j)
                block_type = block["type"]
                if block_type == "tool_result":
                    text = _rt_ms_result_text(block, where)
                    call_id = open_calls.pop(block["tool_use_id"], None)
                    if call_id is None:
                        parts.append({"type": "input_text", "text": _RT_MS_ORPHAN + text})
                        continue
                    outputs.append({"type": "function_call_output", "call_id": call_id, "output": text})
                elif block_type == "text":
                    parts.append({"type": "input_text", "text": _rt_ms_str(block.get("text"), where + ".text")})
                elif block_type == "image":
                    parts.append(_rt_rs_image_part(block, where))
                else:
                    raise _rt_rs_unsupported(block_type, where)
        items.extend(outputs)
        _rt_rs_close(items, open_calls)
        if parts:
            items.append({"type": "message", "role": "user", "content": parts})
    _rt_rs_close(items, open_calls)
    out["input"] = items

    tools = body.get("tools")
    if tools:
        translated_tools = []
        for index, tool in enumerate(tools):
            entry: Dict[str, Any] = {"type": "function", "name": tool["name"]}
            if "description" in tool:
                entry["description"] = tool["description"]
            entry["parameters"] = _rt_rs_schema(tool.get("input_schema", {"type": "object", "properties": {}}),
                                                "tools.%d.input_schema" % index)
            translated_tools.append(entry)
        if profile.tool_shape == "namespace":
            out["tools"] = [{"type": "namespace", "name": _RT_RS_NAMESPACE,
                             "description": _RT_RS_NAMESPACE_DESCRIPTION, "tools": translated_tools}]
        else:
            out["tools"] = translated_tools
    choice = body.get("tool_choice")
    if choice is not None:
        out["tool_choice"] = _rt_rs_tool_choice(choice)
    out["parallel_tool_calls"] = not (isinstance(choice, dict) and choice.get("disable_parallel_tool_use") is True)

    out["store"] = False
    out["stream"] = True            # always streamed upstream; a non-stream client is collected (Step 10)
    out["include"] = ["reasoning.encrypted_content"]
    out["prompt_cache_key"] = _rt_rs_cache_key(body, backend)
    effort = _rt_rs_effort(body, route)
    out["reasoning"] = {"summary": "auto"} if effort is None else {"effort": effort, "summary": "auto"}
    verbosity = options.get("verbosity")
    if verbosity is not None:
        out["text"] = {"verbosity": verbosity}
    if profile.sampling:
        for key in _rt_rs_sampling_asked(body, route):
            if key not in unsupported:          # learned unsupported wins over a route override
                out[key] = options[key] if key in options else body[key]
        max_tokens = body.get("max_tokens")
        if max_tokens is not None:
            cap = options.get("max_tokens_cap")
            capped = cap is not None and isinstance(max_tokens, int) and not isinstance(max_tokens, bool)
            out["max_output_tokens"] = min(max_tokens, cap) if capped else max_tokens
    return out, dropped


# --- Responses stream translation (Plan Step 10) ---
# One sans-IO state machine for both client modes: a stream client gets its
# default AnthropicSseEncoder sink, a non-stream client the _RtMessageCollector
# (json_response feeds it the upstream SSE body), so the two answers cannot
# drift. Dispatch is data: event type -> method name, checked at import.

def _rt_rs_event_handlers() -> Dict[str, str]:
    """The Responses event types the translator handles -> its method names; any
    other type is ignored and counted in ResponsesStreamTranslator.ignored_events."""
    handlers: Dict[str, str] = {}
    for event in ("response.created", "response.in_progress"):
        handlers[event] = "_on_nothing"
    handlers["response.output_item.added"] = "_on_item_added"
    handlers["response.output_text.delta"] = "_on_text"
    handlers["response.function_call_arguments.delta"] = "_on_args_delta"
    handlers["response.reasoning_summary_part.added"] = "_on_summary_part"
    handlers["response.reasoning_summary_text.delta"] = "_on_summary_delta"
    handlers["response.output_item.done"] = "_on_item_done"
    for event in ("response.completed", "response.done"):
        handlers[event] = "_on_completed"
    handlers["response.incomplete"] = "_on_incomplete"
    for event in ("error", "response.failed"):
        handlers[event] = "_on_error"
    return handlers


_RT_RS_EVENT_HANDLERS = _rt_rs_event_handlers()


def _rt_rs_count(value: Any) -> int:
    """A usage count: a non-negative int, else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class ResponsesStreamTranslator:
    """The Responses stream translator (sans-IO, P9, KD-4, KD-7, KD-16).

    begin() / feed(line) / finish() / fail(type, msg) / ping() return the sink's
    bytes (b"" for the collector). feed accumulates SSE lines up to the profile
    row's event_limit -- the only reader of that limit (L6) -- and dispatches each
    whole event on its closing blank line through _RT_RS_EVENT_HANDLERS. Text
    deltas stream as they come; a function call is emitted whole at its
    output_item.done (the argument deltas are only counted against
    _RT_BUFFERED_TOOL_LIMIT). Every failure is one `event: error` and closes the
    stream, never a message_stop after it. Every method returns b"" once closed.
    A reasoning item's summary streams as one thinking block (its parts joined
    with "\\n\\n") closed at the item's output_item.done with the minted signature
    (_rt_rs_signature); an item without a summary but with a valid
    encrypted_content becomes a redacted_thinking block. dropped_thinking starts
    at the request's dropped signatures and counts each reasoning item whose
    encrypted_content is absent, oversize or of a bad charset.
    """

    def __init__(self, inbound: InboundRequest, profile: ResponsesProfile, input_tokens: int,
                 dropped: int = 0, sink: Any = None) -> None:
        self.sink = sink if sink is not None else AnthropicSseEncoder(_rt_message_id(), inbound.requested_model)
        self.scrub = inbound.scrub
        self.backend_name = inbound.backend.name
        self.event_limit = profile.event_limit
        self.input_tokens = input_tokens
        self.lines: List[bytes] = []
        self.size = 0
        self.fresh = True               # no line fed yet: a leading BOM is dropped (V30)
        self.calls = 0                  # function_call items announced (_RT_BUFFERED_TOOL_COUNT)
        self.args_bytes = 0             # argument delta bytes (_RT_BUFFERED_TOOL_LIMIT)
        self.dropped_thinking = dropped
        self.summary_parts = 0          # summary parts of the current reasoning item
        self.summary_streamed = False   # a summary delta of the current reasoning item was rendered
        self.ignored_events = 0         # unknown event types (debug only)
        self.error_status: Optional[int] = None   # an upstream error code's mapped status (json_response)
        # F26: the tool names this request offered; a function_call naming any other is refused.
        self.offered: FrozenSet[str] = _rt_offered_tools(inbound)

    @property
    def started(self) -> bool:
        return self.sink.started

    @property
    def closed(self) -> bool:
        return self.sink.closed

    def begin(self) -> bytes:
        """message_start with the local input-token estimate."""
        return self.sink.start(self.input_tokens)

    def ping(self) -> bytes:
        """event: ping."""
        return self.sink.ping()

    def fail(self, err_type: str, message: str) -> bytes:
        """One event: error; the half event is discarded. Closes the stream."""
        if self.closed:
            return b""
        self.lines, self.size = [], 0
        return self.sink.error(err_type, message)

    def finish(self) -> bytes:
        """Upstream EOF: without a terminal event the stream ended early (KD-7)."""
        if self.closed:
            return b""
        return self.fail("api_error", "backend ended the stream early")

    def feed(self, line: bytes) -> bytes:
        """One upstream read line -> the bytes to send (often b""). It is split at
        every CRLF, LF and bare CR and a stream-leading BOM is dropped (V30)."""
        if self.fresh:
            self.fresh = False
            if line.startswith(_SSE_BOM):
                line = line[len(_SSE_BOM):]
        return b"".join(self._feed_line(part) for part in _rt_sse_split(line))

    def _feed_line(self, line: bytes) -> bytes:
        """One SSE line -> b"" or the bytes of one whole event's translation."""
        if self.closed:
            return b""
        text = line.rstrip(b"\r\n")
        if text.startswith(b":"):
            return b""
        if text:
            self.lines.append(line)
            self.size += len(line)
            if self.size > self.event_limit:
                return self.fail("api_error", "backend sent an oversized event")
            return b""
        if not self.lines:
            return b""
        accumulated = self.lines + [line]
        self.lines, self.size = [], 0
        out = b""
        for name, data, _raw in _rt_sse_events(accumulated):
            if self.closed:
                break
            out += self._dispatch(name, data)
        return out

    def _dispatch(self, name: str, data: str) -> bytes:
        """One whole event: its JSON object through the handler table."""
        try:
            obj = _rt_loads(data)
        except (ValueError, RecursionError):
            obj = None
        if not isinstance(obj, dict):
            return self.fail("api_error", "backend sent a malformed stream event")
        event_type = obj.get("type") if isinstance(obj.get("type"), str) else name
        method = _RT_RS_EVENT_HANDLERS.get(event_type)
        if method is None:
            self.ignored_events += 1
            return b""
        return getattr(self, method)(obj)

    def _on_nothing(self, obj: dict) -> bytes:
        return b""

    def _on_item_added(self, obj: dict) -> bytes:
        """A function_call item counts against _RT_BUFFERED_TOOL_COUNT."""
        item = obj.get("item")
        if isinstance(item, dict) and item.get("type") == "function_call":
            self.calls += 1
            if self.calls > _RT_BUFFERED_TOOL_COUNT:
                return self.fail("api_error", "backend sent too many tool calls")
        return b""

    def _on_text(self, obj: dict) -> bytes:
        delta = obj.get("delta")
        if isinstance(delta, str) and delta:
            return self.sink.text(delta)
        return b""

    def _on_args_delta(self, obj: dict) -> bytes:
        """Counted only: the authoritative arguments arrive at output_item.done."""
        delta = obj.get("delta")
        if isinstance(delta, str):
            self.args_bytes += len(delta.encode("utf-8", "surrogatepass"))
            if self.args_bytes > _RT_BUFFERED_TOOL_LIMIT:
                return self.fail("api_error", "backend tool arguments too large")
        return b""

    def _on_summary_part(self, obj: dict) -> bytes:
        """A summary part after an earlier one of the same item adds a "\\n\\n" delta."""
        self.summary_parts += 1
        if self.summary_parts > 1 and self.summary_streamed:
            return self.sink.thinking("\n\n")
        return b""

    def _on_summary_delta(self, obj: dict) -> bytes:
        delta = obj.get("delta")
        if isinstance(delta, str) and delta:
            self.summary_streamed = True
            return self.sink.thinking(delta)
        return b""

    def _on_item_done(self, obj: dict) -> bytes:
        """A function_call is emitted whole; a reasoning item closes its thinking block with
        the minted signature, or becomes a redacted_thinking block (KD-6)."""
        item = obj.get("item")
        if not isinstance(item, dict):
            return b""
        if item.get("type") == "reasoning":
            streamed = self.summary_streamed
            self.summary_parts, self.summary_streamed = 0, False
            sig = _rt_rs_signature(self.backend_name, item.get("encrypted_content"))
            if sig is None:
                self.dropped_thinking += 1
            if streamed:
                return self.sink.thinking_end(sig or "")
            if sig is not None:
                return self.sink.redacted_thinking(sig)
            return b""
        if item.get("type") != "function_call":
            return b""
        args = item.get("arguments")
        if args is None:
            args = ""
        if not isinstance(args, str):
            return self.fail("api_error", "backend returned malformed tool arguments")   # V31
        # A JSON object, strictly (no NaN/Infinity), re-dumped so the client gets
        # exactly what was checked (V31).
        try:
            tool_input = _rt_loads(args or "{}")
            args_json = _rt_dumps(tool_input).decode("ascii") if isinstance(tool_input, dict) else ""
        except (ValueError, RecursionError):
            args_json = ""
        if not args_json:
            return self.fail("api_error", "backend returned malformed tool arguments")
        name, call_id = item.get("name"), item.get("call_id")
        if not isinstance(name, str) or not name:
            return self.fail("api_error", "backend returned a tool call without a name")
        if name not in self.offered:     # F26; the name is not echoed
            return self.fail("api_error", _RT_UNOFFERED_TOOL)
        if not isinstance(call_id, str) or not call_id:
            return self.fail("api_error", "backend returned a tool call without an id")
        return self.sink.tool_block(_rt_rs_anthropic_id(call_id), name, args_json)

    @staticmethod
    def _usage(obj: dict) -> Dict[str, int]:
        """response.usage -> {input_tokens: input - cached (never below 0), output_tokens,
        cache_read_input_tokens: cached}."""
        response = obj.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        details = usage.get("input_tokens_details")
        cached = _rt_rs_count(details.get("cached_tokens")) if isinstance(details, dict) else 0
        return {"input_tokens": max(0, _rt_rs_count(usage.get("input_tokens")) - cached),
                "output_tokens": _rt_rs_count(usage.get("output_tokens")),
                "cache_read_input_tokens": cached}

    def _on_completed(self, obj: dict) -> bytes:
        return self.sink.finish("end_turn", self._usage(obj))

    def _on_incomplete(self, obj: dict) -> bytes:
        """max_tokens when the output cap was hit, else end_turn."""
        response = obj.get("response")
        details = response.get("incomplete_details") if isinstance(response, dict) else None
        reason = details.get("reason") if isinstance(details, dict) else None
        return self.sink.finish("max_tokens" if reason == "max_output_tokens" else "end_turn", self._usage(obj))

    def _on_error(self, obj: dict) -> bytes:
        """`error` / response.failed: the code (code, error.code or response.error.code)
        typed through _RT_RS_ERROR_CODES (default api_error), the message scrubbed (KD-9)."""
        code, message = obj.get("code"), obj.get("message")
        nested = obj.get("error")
        response = obj.get("response")
        if not isinstance(nested, dict) and isinstance(response, dict):
            nested = response.get("error")
        if isinstance(nested, dict):
            if not isinstance(code, str):
                code = nested.get("code")
            if not isinstance(message, str) or not message:
                message = nested.get("message")
        mapped = _RT_RS_ERROR_CODES.get(code) if isinstance(code, str) else None
        err_type = mapped[1] if mapped is not None else "api_error"
        self.error_status = mapped[0] if mapped is not None else None
        if isinstance(message, str) and message:
            message = self.scrub(message)
        else:
            message = "backend %s reported an error" % self.backend_name
        return self.fail(err_type, message)


def _rt_check_event_handlers(handlers: Mapping[str, str]) -> None:
    """Refuse a handler row naming no translator method; a RuntimeError names the row."""
    for event, method in handlers.items():
        if not callable(getattr(ResponsesStreamTranslator, method, None)):
            raise RuntimeError(f"event handler {event}: no method {method}")


_rt_check_event_handlers(_RT_RS_EVENT_HANDLERS)


class ResponsesAdapter(Adapter):
    """The Responses translation shared by codex and openai; every difference is the profile row (KD-1).

    Registered through its CodexAdapter and OpenaiAdapter subclasses (KIND_CLASSES, ADAPTERS).
    """

    FORWARDABLE: FrozenSet[str] = frozenset()
    DEFAULT_FORWARD: FrozenSet[str] = frozenset()

    def __init__(self) -> None:
        # P15: the per-request cache upstream_request leaves for stream_translator,
        # keyed by id(inbound) and bounded like MistralAdapter._pending.
        self._pending: Dict[int, Any] = {}

    @staticmethod
    def _profile(inbound: InboundRequest) -> ResponsesProfile:
        """The backend's profile row, resolved once at load (BackendSpec.profile)."""
        return _RT_RESPONSES_PROFILES[inbound.backend.profile]

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        """POST the profile row's path with _rt_rs_translate's body (always a stream upstream)."""
        row = self._profile(inbound)
        translated, dropped = _rt_rs_translate(inbound.body, inbound.route, inbound.backend.name, row,
                                               inbound.sampling_drop)
        if inbound.stream:
            self._pending[id(inbound)] = (_rt_ms_estimate(translated, inbound.route), dropped)
            while len(self._pending) > _RT_MS_PENDING_CAP:
                try:
                    self._pending.pop(next(iter(self._pending)), None)
                except (StopIteration, RuntimeError):
                    break
        return row.path, _rt_dumps(translated)

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        """A local estimate (D4, _rt_ms_estimate) over the translated body; nothing is sent."""
        translated, _dropped = _rt_rs_translate(inbound.body, inbound.route, inbound.backend.name,
                                                self._profile(inbound), inbound.sampling_drop)
        return {"input_tokens": _rt_ms_estimate(translated, inbound.route)}

    def learns_sampling(self, inbound: InboundRequest) -> bool:
        """A sampling profile (openai-apikey) learns: which model refuses temperature / top_p is
        model-dependent (a reasoning model does); a profile without sampling never sends them."""
        return self._profile(inbound).sampling

    def sampling_sent(self, inbound: InboundRequest) -> Tuple[str, ...]:
        if not self._profile(inbound).sampling:
            return ()
        return tuple(key for key in _rt_rs_sampling_asked(inbound.body, inbound.route)
                     if key not in inbound.sampling_drop)

    def sampling_dropped(self, inbound: InboundRequest) -> Tuple[str, ...]:
        """Static (a profile without sampling drops every name asked for) or learned (sampling_drop)."""
        asked = _rt_rs_sampling_asked(inbound.body, inbound.route)
        if not self._profile(inbound).sampling:
            return asked
        return tuple(key for key in asked if key in inbound.sampling_drop)

    def upstream_head(self, inbound: InboundRequest,
                      cred: Optional[Credential]) -> Tuple[Optional[str], Tuple[Tuple[str, str], ...]]:
        """("text/event-stream", the profile row's extra pairs) -- always a stream upstream.

        In order: chatgpt-account-id and x-openai-internal-codex-residency
        (row.account_header, from *cred*), OpenAI-Beta (row.beta), the effective
        identity -- _RT_CODEX_IDENTITY merged with backend.identity, in the
        table's order; _rt_upstream_headers lifts the user-agent pair into its
        User-Agent slot -- and, with the identity, session_id (the body's
        prompt_cache_key). Every value passes _rt_header_value_ok first (S5): a
        failing account id or identity value is a 502, a failing residency is
        dropped (the claim rule's "ignored")."""
        row = self._profile(inbound)
        backend = inbound.backend
        pairs: List[Tuple[str, str]] = []
        refused = UpstreamError(502, "api_error", "backend %s: an upstream header value failed validation"
                                % backend.name)
        if row.account_header and cred is not None and cred.account_id is not None:
            if not _rt_header_value_ok(cred.account_id, "claim"):
                raise refused
            pairs.append(("chatgpt-account-id", cred.account_id))
            if cred.residency is not None and _rt_header_value_ok(cred.residency, "claim"):
                pairs.append(("x-openai-internal-codex-residency", cred.residency))
        if row.beta:
            pairs.append(("OpenAI-Beta", row.beta))
        if row.identity:
            for key, value in _rt_rs_identity(backend):
                if not _rt_header_value_ok(value, "identity"):
                    raise refused
                pairs.append((key, value))
            session = _rt_rs_cache_key(inbound.body, backend.name)
            if not _rt_header_value_ok(session, "claim"):
                raise refused
            pairs.append(("session_id", session))
        return "text/event-stream", tuple(pairs)

    def stream_policy(self, inbound: InboundRequest) -> StreamPolicy:
        """The profile row's line cap and event-stream requirement (L6: no event limit here)."""
        row = self._profile(inbound)
        return StreamPolicy(row.line_limit, row.require_event_stream)

    def json_response(self, inbound: InboundRequest, status: int, body: bytes) -> Tuple[int, dict]:
        """2xx: the upstream SSE body (always a stream upstream) through the stream
        translator with an _RtMessageCollector sink -> (200, the message); a failed
        stream -> its code's mapped status, else 502. Non-2xx: an error code in
        _RT_RS_ERROR_CODES gives its status and type; an OAuth 401 the re-login hint
        (KD-16 deviation); an api-key 401/403 P11's 502; anything else KD-14's
        status with the upstream message scrubbed (KD-9)."""
        name = inbound.backend.name
        row = self._profile(inbound)
        if 200 <= status < 300:
            collector = _RtMessageCollector(_rt_message_id(), inbound.requested_model)
            translator = ResponsesStreamTranslator(inbound, row, 0, sink=collector)
            translator.begin()
            for line in body.splitlines(keepends=True):
                translator.feed(line)
                if translator.closed:
                    break
            translator.finish()
            if collector.failed is not None:
                err_type, message = collector.failed
                if translator.error_status is not None:
                    return translator.error_status, _rt_error_body(err_type, message)
                return 502, _rt_error_body("api_error", message)
            return 200, collector.message()
        try:
            obj = _rt_loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            obj = None
        message: Any = None
        mapped: Optional[Tuple[int, str]] = None
        if isinstance(obj, dict):
            err = obj.get("error")
            if isinstance(err, dict):
                message = err.get("message")
                for code in (err.get("code"), err.get("type")):
                    if isinstance(code, str) and code in _RT_RS_ERROR_CODES:
                        mapped = _RT_RS_ERROR_CODES[code]
                        break
            elif isinstance(obj.get("detail"), str):
                message = obj["detail"]
        if not isinstance(message, str) or not message:
            message = "backend %s answered %d" % (name, status)
        if status == 403 and row.oauth_provider:
            # F30: an OAuth profile's 403 is about the login (plan, account, region); the
            # client gets a fixed text, as a 401 gets the re-login hint. The status stays.
            message = "backend %s refused this login (upstream 403)" % name
        if mapped is not None:
            return mapped[0], _rt_error_body(mapped[1], inbound.scrub(message))
        if status == 401 and row.oauth_provider:
            # KD-16 deviation: an OAuth profile's 401 is the client's to fix (a fresh
            # login), so it is answered 401 with the hint rather than KD-8's 502.
            log.warning("upstream login rejected: backend %s", _log_value(name))
            return 401, _rt_error_body("authentication_error",
                                       "backend %s: the login was rejected after a refresh; "
                                       "run llm-router.py login --backend %s" % (name, name))
        mapped_status = _UPSTREAM_STATUS.get(status, 502)
        err_type = _STATUS_TO_TYPE.get(mapped_status, "api_error")
        if status in (401, 403) and not row.oauth_provider:
            log.warning("upstream credential rejected: backend %s", _log_value(name))
            return mapped_status, _rt_error_body(err_type, "backend %s rejected the router's credential" % name)
        return mapped_status, _rt_error_body(err_type, inbound.scrub(message))

    def stream_translator(self, inbound: InboundRequest) -> "ResponsesStreamTranslator":
        """The stream translator with the (estimate, dropped signatures) upstream_request
        left for this very request (P15), else a second translation."""
        pending = self._pending.pop(id(inbound), None)
        if pending is None:
            translated, dropped = _rt_rs_translate(inbound.body, inbound.route, inbound.backend.name,
                                                   self._profile(inbound), inbound.sampling_drop)
            estimate = _rt_ms_estimate(translated, inbound.route)
        else:
            estimate, dropped = pending
        return ResponsesStreamTranslator(inbound, self._profile(inbound), estimate, dropped)


class CodexAdapter(ResponsesAdapter):
    """kind codex: the ChatGPT-subscription Responses endpoint (profile codex)."""

    kind = "codex"
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {
        "count_divisor": _rt_opt_int(1, 16),
        "reasoning_effort": _rt_opt_enum(_RT_RS_EFFORTS),
        "verbosity": _rt_opt_enum(("low", "medium", "high")),
    }


class OpenaiAdapter(ResponsesAdapter):
    """kind openai: the platform Responses endpoint (profiles openai-oauth / openai-apikey)."""

    kind = "openai"
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {
        "count_divisor": _rt_opt_int(1, 16),
        "reasoning_effort": _rt_opt_enum(_RT_RS_EFFORTS),
        "verbosity": _rt_opt_enum(("low", "medium", "high")),
        "temperature": _RT_TEMPERATURE,
        "top_p": _RT_TOP_P,
        "max_tokens_cap": _rt_opt_int(1),
    }


class _RtKindRow(NamedTuple):
    """One backend kind of _RT_KIND_TABLE (R-0084, NFR-2)."""
    kind: str                           # the config enum value (backends.<name>.kind)
    adapter: Optional[Type[Adapter]]    # None = reserved: in the enum, refused by the loader (D6)
    auth: Mapping[str, str]             # auth mode -> _RT_RESPONSES_PROFILES name (M5); {} = no profile


_RT_KIND_AUTH_MODES = ("api_key", "oauth")

# The kind registry: one row per kind, in the order the loader's refusal lists them.
# _KINDS, RESERVED_KINDS, _RT_KIND_AUTH_PROFILES, KIND_CLASSES and ADAPTERS all derive
# from it below, so a new kind is one row (J37-J39 gate the derivation).
_RT_KIND_TABLE: Tuple[_RtKindRow, ...] = (
    _RtKindRow("passthrough", PassthroughAdapter, {}),
    _RtKindRow("llamacpp", LlamacppAdapter, {}),
    _RtKindRow("mistral", MistralAdapter, {}),
    _RtKindRow("codex", CodexAdapter, {"oauth": "codex"}),
    _RtKindRow("openai", OpenaiAdapter, {"oauth": "openai-oauth", "api_key": "openai-apikey"}),
    _RtKindRow("anthropic", None, {}),      # reserved: decided by its own plan (D6)
)


def _rt_check_kind_table(rows: Tuple[_RtKindRow, ...]) -> None:
    """Refuse an inconsistent kind registry at import (R-0084): a RuntimeError naming the kind.

    Each kind is a non-empty string with one row; a reserved row (adapter None)
    has no auth profile; any other row's adapter is an Adapter subclass of that
    very kind, and each auth mode is api_key or oauth naming an _RT_RESPONSES_PROFILES row.
    """
    seen = set()
    for row in rows:
        kind = row.kind
        if not isinstance(kind, str) or not kind:
            raise RuntimeError("kind registry: a row's kind must be a non-empty string")
        if kind in seen:
            raise RuntimeError(f"kind {kind}: two registry rows")
        seen.add(kind)
        if row.adapter is None:
            if row.auth:
                raise RuntimeError(f"kind {kind}: a reserved kind has no auth profile")
            continue
        if not isinstance(row.adapter, type) or not issubclass(row.adapter, Adapter):
            raise RuntimeError(f"kind {kind}: the adapter is not an Adapter subclass")
        if row.adapter.kind != kind:
            raise RuntimeError(f"kind {kind}: adapter {row.adapter.__name__} is of kind {row.adapter.kind}")
        for mode, profile in row.auth.items():
            if mode not in _RT_KIND_AUTH_MODES:
                raise RuntimeError(f"kind {kind}: unknown auth mode {mode}")
            if profile not in _RT_RESPONSES_PROFILES:
                raise RuntimeError(f"kind {kind}: auth {mode} names no Responses profile {profile}")


_rt_check_kind_table(_RT_KIND_TABLE)

# Every kind the config enum knows, in table order; RESERVED_KINDS are in the enum but
# refused by the loader (D6: anthropic is decided by its own plan).
_KINDS = tuple(row.kind for row in _RT_KIND_TABLE)
RESERVED_KINDS = tuple(row.kind for row in _RT_KIND_TABLE if row.adapter is None)
# kind -> {auth mode -> profile name} (M5): the data behind _rt_cfg_profile. A kind
# absent here has no profile (""); a kind with one mode requires it; a kind with both
# takes exactly one.
_RT_KIND_AUTH_PROFILES: Dict[str, Dict[str, str]] = {
    row.kind: dict(row.auth) for row in _RT_KIND_TABLE if row.auth}
# kind -> adapter class: the single source of each kind's tables (load_config reads it).
KIND_CLASSES: Dict[str, Type[Adapter]] = {
    row.kind: row.adapter for row in _RT_KIND_TABLE if row.adapter is not None}


def _rt_check_forward_tables(classes: Mapping[str, Type[Adapter]]) -> None:
    """Refuse a forward table that could leak a credential or a router-owned header (KD-3).

    For every kind: FORWARDABLE is disjoint from _NEVER_FORWARD and holds no
    proxy-* name, and DEFAULT_FORWARD is a subset of FORWARDABLE. A violation is
    a RuntimeError naming the kind and the header; it runs at import, so a bad
    table stops the router before it binds.
    """
    for kind, cls in classes.items():
        forwardable = frozenset(cls.FORWARDABLE)
        for header in sorted(forwardable):
            if header in _NEVER_FORWARD or header.startswith(_RT_NEVER_FORWARD_PREFIX):
                raise RuntimeError(f"kind {kind}: header {header} may never be forwarded")
        extra = sorted(frozenset(cls.DEFAULT_FORWARD) - forwardable)
        if extra:
            raise RuntimeError(f"kind {kind}: default header {extra[0]} is not in FORWARDABLE")


_rt_check_forward_tables(KIND_CLASSES)

# kind -> adapter instance, one per kind of KIND_CLASSES (Steps 8, 9, 11).
ADAPTERS: Dict[str, Adapter] = {
    row.kind: row.adapter() for row in _RT_KIND_TABLE if row.adapter is not None}


# ---------------------------------------------------------------------------
# Unit 6: outbound transport
# ---------------------------------------------------------------------------

# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_oauth.py :: OAUTH_TOKEN_LIMIT, OAUTH_BODY_LIMIT, OAUTH_JWT_LIMIT, OAUTH_INT_LITERAL_LIMIT, OAUTH_REFRESH_SKEW_S, OAUTH_MIN_REFRESH_INTERVAL_S, OAUTH_CALLBACK_HEAD_LIMIT, OAUTH_CALLBACK_BAD_LIMIT, OAUTH_CALLBACK_CONN_TIMEOUT_S, OAUTH_ERROR_KINDS, OAUTH_RELOGIN_CODES, OAUTH_ERROR_CODES, OAUTH_TRANSIENT_CODES, OAUTH_CODE_ALPHABET, OAUTH_B64URL_ALPHABET, OAUTH_ID_ALPHABET, OAuthError, _oauth_pairs_ok, OAuthProvider, _oauth_b64url, _oauth_b64url_decode, _oauth_pkce_pair, _oauth_new_state, _oauth_uuid4_urn, _oauth_token_ok, _oauth_state_matches, _oauth_authorize_url, _oauth_form_headers, _oauth_json_headers, _oauth_exchange_request, _oauth_refresh_request, _oauth_device_start_request, _oauth_device_poll_request, _oauth_error_code, _oauth_no_duplicate_keys, _oauth_no_constant, _oauth_bounded_int, _oauth_json_object, _oauth_status_refusal, _oauth_error_refusal, _oauth_parse_token_response, _oauth_device_interval, _oauth_parse_device_start, _oauth_parse_device_poll, _oauth_id_ok, _oauth_jwt_claims, _oauth_check_id_token, _oauth_scope_has, _oauth_account_claims, _oauth_token_due, _oauth_parse_callback, _oauth_callback_page, _oauth_callback_verdict, _oauth_listen, _oauth_sync_accept_callback
OAUTH_TOKEN_LIMIT = 16384


OAUTH_BODY_LIMIT = 1024 * 1024


OAUTH_JWT_LIMIT = 65536


OAUTH_INT_LITERAL_LIMIT = 4300


OAUTH_REFRESH_SKEW_S = 300


OAUTH_MIN_REFRESH_INTERVAL_S = 60


OAUTH_CALLBACK_HEAD_LIMIT = 8192


OAUTH_CALLBACK_BAD_LIMIT = 16


OAUTH_CALLBACK_CONN_TIMEOUT_S = 5.0


OAUTH_ERROR_KINDS = ("relogin", "transient", "rate_limited", "invalid_response", "invalid_callback", "denied", "callback_timeout", "callback_abuse")


OAUTH_RELOGIN_CODES = frozenset({"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"})


OAUTH_ERROR_CODES = frozenset(OAUTH_RELOGIN_CODES | {"invalid_request", "invalid_client", "unauthorized_client", "unsupported_grant_type", "invalid_scope", "access_denied", "server_error", "temporarily_unavailable", "authorization_pending", "slow_down", "expired_token"})


OAUTH_TRANSIENT_CODES = frozenset({"server_error", "temporarily_unavailable"})


OAUTH_CODE_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz_")


OAUTH_B64URL_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


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


def _oauth_parse_token_response(status, body, now, previous_refresh, require_refresh=False, previous_scope=None):
    """Judge one token-endpoint answer (RFC 6749 5.1/5.2).

    The status is classified first (429, 5xx), then a non-200 is mapped by its
    error code, then a 200 body is parsed under OAUTH_BODY_LIMIT. Returns a dict
    with `access_token`, `refresh_token`, `expires_at`, `id_token`, `scope` and
    `earliest_refresh_at`. Every token present must pass _oauth_token_ok. A
    missing refresh_token keeps *previous_refresh* (6: the server may keep the
    old one), unless *require_refresh*, as for a code exchange, where it is a
    refusal. `expires_at` is now + expires_in, or None when the answer has no
    expires_in (it is OPTIONAL); `id_token` is None when absent. A missing
    `scope` is *previous_scope* (6: an omitted scope is the one granted before;
    F35), None when the caller knows none. `earliest_refresh_at` is *now* + OAUTH_MIN_REFRESH_INTERVAL_S: no provider
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
    if scope is None:
        scope = previous_scope
    elif not isinstance(scope, str):
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
    *now*, and `nonce` equals *nonce* when one was sent. An `aud` list of more
    than one audience needs an `azp`, and an `azp`, when present, must be
    *client_id* (OIDC Core 3.1.3.7 items 4-5; F35). The code names the first
    claim that failed and nothing else.
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
    azp = claims.get("azp")
    if (isinstance(aud, list) and len(aud) > 1 and azp is None) or (azp is not None and azp != client_id):
        raise OAuthError("invalid_response", "azp")
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
    _oauth_callback_verdict (405, 400, 404, 400). Every connection that does not
    end the wait -- a refusal, a head that never completed, and one that sent
    no byte at all (F6) -- counts against OAUTH_CALLBACK_BAD_LIMIT; spending it
    raises OAuthError callback_abuse.

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
                # F6: every connection that did not end the wait counts -- a refusal, a head
                # that never completed, and one that sent nothing at all before closing.
                bad += 1
                if bad >= OAUTH_CALLBACK_BAD_LIMIT:
                    raise OAuthError("callback_abuse")
    finally:
        for listener in listeners:
            listener.close()
# END GENERATED: 21389f15556a


def _rt_oauth_providers() -> Dict[str, OAuthProvider]:
    """The two OAuth provider rows (Plan Step 8): data, checked by OAuthProvider at import."""
    rows = (
        OAuthProvider(name="codex",
                      authorize_url="https://auth.openai.com/oauth/authorize",
                      token_url="https://auth.openai.com/oauth/token",
                      client_id="app_EMoamEEZ73f0CkXaXp7hrann",
                      scopes="openid profile email offline_access api.connectors.read api.connectors.invoke",
                      redirect_uri="http://localhost:1455/auth/callback",
                      authorize_extra=(("id_token_add_organizations", "true"),
                                       ("codex_cli_simplified_flow", "true"),
                                       ("originator", dict(_RT_CODEX_IDENTITY)["originator"])),
                      resource="", issuer="", required_scope="", use_nonce=False,
                      registration_client_id="",
                      device_usercode_url="https://auth.openai.com/api/accounts/deviceauth/usercode",
                      device_token_url="https://auth.openai.com/api/accounts/deviceauth/token",
                      device_verify_url="https://auth.openai.com/codex/device",
                      device_redirect_uri="https://auth.openai.com/deviceauth/callback"),
        OAuthProvider(name="openai",
                      authorize_url="https://auth.openai.com/api/accounts/authorize",
                      token_url="https://auth.openai.com/api/accounts/oauth/token",
                      client_id="",                          # issued at registration
                      scopes="openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
                      redirect_uri="",                       # ephemeral 127.0.0.1:<port>
                      authorize_extra=(),
                      resource="https://api.openai.com/v1", issuer="https://auth.openai.com",
                      required_scope="chatgpt.tokens.use.direct", use_nonce=True,
                      registration_client_id="dynamic_agent_client",
                      device_usercode_url="", device_token_url="", device_verify_url="",
                      device_redirect_uri=""),
    )
    return {row.name: row for row in rows}


_RT_OAUTH_PROVIDERS = _rt_oauth_providers()
# The join the unit-5 check skipped: every profile's oauth_provider names a row above (KD-1).
_rt_check_responses_profiles(_RT_RESPONSES_PROFILES, frozenset(_RT_OAUTH_PROVIDERS))

_RT_OAUTH_IDLE_S = 15.0          # the auth POST's idle timeout (KD-11 adds connect_timeout for the lock wait)
_RT_OAUTH_FAIL_CACHE_S = 10.0    # KD-11: a non-relogin refresh failure is shared this long (declared, unargued)
_RT_OAUTH_LOCK_SLICE_S = 0.25    # KD-11: one bounded-acquire slice; `closing` is checked between slices
_RT_OAUTH_HEADER_NAMES = frozenset({"content-type", "accept", "user-agent"})


def _rt_oauth_spec(backend: BackendSpec, url: str) -> Tuple[BackendSpec, str]:
    """(spec, path) of an auth-endpoint POST for *backend* to the provider *url* (KD-19, S9).

    The host is auth_base when the backend sets auth_base_url (its base_path
    prefixed, its own trust inputs kept), else the URL's host under the
    public-only policy and the system trust store -- trust never widens
    silently. No credential, no forwarded header, no oauth seed;
    connect_timeout is inherited and the idle timeout is _RT_OAUTH_IDLE_S.
    """
    parts = urllib.parse.urlsplit(url)
    path = parts.path
    if backend.auth_base is not None:
        scheme, host, port, base_path = backend.auth_base
        trust: Dict[str, Any] = {"allow_private": backend.allow_private, "allow_loopback": backend.allow_loopback,
                                 "ca_file": backend.ca_file, "ca_pem": backend.ca_pem}
    else:
        scheme, host, base_path = parts.scheme, parts.hostname or "", ""
        if scheme != "https" or not host:
            raise UpstreamError(502, "api_error", "backend %s: the auth endpoint is not an https URL" % backend.name)
        port = parts.port or 443
        trust = {"allow_private": False, "allow_loopback": False, "ca_file": None, "ca_pem": None}
    spec = backend._replace(name=backend.name + "-auth", scheme=scheme, host=host, port=port,
                            base_path=base_path, api_key=None, auth_header="none",
                            forward_headers=frozenset(), oauth=None, idle_timeout=_RT_OAUTH_IDLE_S, **trust)
    return spec, path


def _rt_oauth_row_for(backend: BackendSpec, provider: OAuthProvider) -> OAuthProvider:
    """*provider* as *backend* checks ID tokens against it (Plan Step 16, FR-6).

    A row with an issuer keeps it, unless the backend sets auth_base_url: then the
    issuer is that base's origin (scheme://host[:port], the port only when it is not
    the scheme's default), because the ID token comes from that host. A row with no
    issuer (codex) is returned unchanged.
    """
    if not provider.issuer or backend.auth_base is None:
        return provider
    scheme, host, port, _base_path = backend.auth_base
    netloc = "[%s]" % host if ":" in host else host
    if port != _RT_DEFAULT_PORTS[scheme]:
        netloc += ":%d" % port
    fields = {name: getattr(provider, name) for name in OAuthProvider.__slots__}
    fields["issuer"] = scheme + "://" + netloc
    return OAuthProvider(**fields)


def _rt_oauth_exchange_request(provider: OAuthProvider, client_id: str, code: str, verifier: str,
                               redirect: str) -> Tuple[str, Tuple[Tuple[str, str], ...], bytes]:
    """_oauth_exchange_request plus the row's `resource` (RFC 8707) when it has one (SIWC)."""
    url, headers, body = _oauth_exchange_request(provider, client_id, code, verifier, redirect)
    if provider.resource:
        body += b"&" + urllib.parse.urlencode([("resource", provider.resource)]).encode("ascii")
    return url, headers, body


def _rt_oauth_check_answer(provider: OAuthProvider, got: Dict[str, Any], client_id: str,
                           nonce: Optional[str], now: int) -> None:
    """FR-6/I3: the ID-token and scope checks of a token answer on a row with an issuer.

    The ID token must be present when a *nonce* was sent (a login), and when present
    pass _oauth_check_id_token (iss, aud = *client_id*, exp, nonce). The granted scope
    is read from the answer's `scope` field only and, when present, must hold the
    row's required_scope. Raises OAuthError invalid_response naming the failed check.
    A row with no issuer (codex) is not checked.
    """
    if not provider.issuer:
        return
    id_token = got["id_token"]
    if id_token is None:
        if nonce:
            raise OAuthError("invalid_response", "id_token")
    else:
        _oauth_check_id_token(_oauth_jwt_claims(id_token), provider, client_id, nonce, now)
    if got["scope"] is not None and not _oauth_scope_has(got["scope"], provider.required_scope):
        raise OAuthError("invalid_response", "scope")


def _rt_oauth_lock_wait(backend: BackendSpec) -> float:
    """The KD-11 bound on waiting for another thread's refresh: one auth POST's connect plus idle."""
    return backend.connect_timeout + _RT_OAUTH_IDLE_S


def _rt_oauth_headers(headers: Tuple[Tuple[str, str], ...], user_agent: str) -> List[Tuple[str, str]]:
    """The OAuth builder's *headers* plus User-Agent; any other name is a ValueError.

    The auth POST's only header builder (J4a): it never reads client_headers,
    so nothing the client sent reaches the token endpoint.
    """
    out = list(headers) + [("User-Agent", user_agent)]
    for name, _value in out:
        if name.lower() not in _RT_OAUTH_HEADER_NAMES:
            raise ValueError("oauth header %s is not content-type, accept or user-agent" % _log_value(name))
    return out


def _rt_oauth_post(backend: BackendSpec, request: Tuple[str, Tuple[Tuple[str, str], ...], bytes],
                   user_agent: str, track: Optional["_RtTrack"] = None) -> Tuple[int, bytes]:
    """POST one OAuth *request* (url, headers, body) -> (status, body) (KD-7: the second _rt_send site).

    *track* is (srv.inflight, srv.inflight_lock, srv.closing), handed to
    _rt_send (R-0083): the socket is registered from before its connect on --
    the connect (R-0095), the TLS handshake, the request write, the response
    head and the body read -- so a shutdown unblocks every stalled phase (P23,
    N25, N41); a stalled resolve is given up within one _RT_TICK_S. The
    body is capped at OAUTH_BODY_LIMIT and both the response and the connection
    are closed here, the socket deregistered first.
    """
    url, headers, body = request
    spec, path = _rt_oauth_spec(backend, url)
    conn, resp = _rt_send(spec, path, _rt_oauth_headers(headers, user_agent), body, track=track)
    try:
        return resp.status, _rt_read_body(resp, OAUTH_BODY_LIMIT, spec.name)
    finally:
        conn.untrack()
        for closer in (resp.close, conn.close):
            try:
                closer()
            except OSError:
                pass


# The scrubber-write entry point (KD-13, NEW-1): every production add/pin goes through
# these two. _RtScrubber reads its containers once, up front, and rebinds afterwards
# (R3-M1), so two unserialized writers would lose one set; the lock is held only for
# a form-tuple rebuild, never across I/O.
_RT_SCRUB_LOCK = threading.Lock()   # serializes every _RtScrubber writer in this process


def _rt_scrub_add(scrub: _RtScrubber, raw: bytes) -> None:
    """scrub.add(raw) under _RT_SCRUB_LOCK."""
    with _RT_SCRUB_LOCK:
        scrub.add(raw)


def _rt_scrub_pin(scrub: _RtScrubber, key: str, raws: Tuple[bytes, ...]) -> None:
    """scrub.pin(key, raws) under _RT_SCRUB_LOCK."""
    with _RT_SCRUB_LOCK:
        scrub.pin(key, raws)


class _RtTokenState:
    """One OAuth backend's live token state, guarded by its own `lock` (KD-11).

    `failed` is the KD-11 negative-cache 5-tuple (mono expiry, exception class,
    status, error type, message) or None. Holds secrets: the repr names nothing."""

    __slots__ = ("lock", "access", "expires_at", "earliest_refresh_at", "refresh", "dead_refresh",
                 "account_id", "residency", "client_id", "host_id", "scope", "failed")

    def __init__(self, seed: Optional[OAuthSeed]) -> None:
        self.lock = threading.Lock()
        self.access: Optional[Credential] = None
        self.expires_at: Optional[int] = None
        self.earliest_refresh_at: Optional[int] = None
        self.refresh: Optional[str] = seed.refresh_token if seed is not None else None
        self.dead_refresh: Optional[str] = None
        self.account_id: Optional[str] = seed.account_id if seed is not None else None
        self.residency: Optional[str] = None
        self.client_id: Optional[str] = seed.client_id if seed is not None else None
        self.host_id: Optional[str] = seed.host_id if seed is not None else None
        # The scope the last refresh answer granted (F35): an answer that omits scope keeps
        # it (RFC 6749 6). In memory only; None until a refresh answer names one.
        self.scope: Optional[str] = None
        self.failed: Optional[Tuple[float, type, int, str, str]] = None

    def __repr__(self) -> str:
        return "<token state>"


class _RtTokenStore:
    """The router's OAuth token store (unit 6; KD-10, KD-11, KD-12, KD-13, KD-16).

    One threading.Lock per backend whose profile has an oauth_provider, plus
    `write_lock`; the order is ALWAYS backend lock -> write_lock -> the KD-20
    flock, and no lock is held while relaying to the client. credential() is the
    only reader: a bounded acquire (503 when the wait runs out, 529 once
    `closing` is set), then -- under the backend lock -- the disk adoption of a
    login (M3), the stale re-check, the negative cache and at most one refresh
    POST through the `post` seam. Every token is validated (S5) and pinned in the
    scrubber (KD-13) before any header use or write; rotated refresh tokens are
    persisted via _rt_persist (never the access or ID token). A refresh-failure
    log line carries OAuthError.code only, never a token or a description (S8).
    `config_path` None disables persistence and the disk read (in-process tests).
    """

    def __init__(self, config_path: Optional[str], cfg: RouterConfig, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, post: Optional[Callable[..., Tuple[int, bytes]]] = None,
                 closing: Optional[threading.Event] = None,
                 track: Optional["_RtTrack"] = None) -> None:
        self.config_path = config_path
        self.cfg = cfg
        self.clock = clock
        self.mono = mono
        self.post = post if post is not None else _rt_oauth_post
        self.closing = closing if closing is not None else threading.Event()
        self.track = track
        self.write_lock = threading.Lock()
        self._digest = cfg.source_digest
        self._skip_logged = False
        self._states: Dict[str, _RtTokenState] = {}
        for name, spec in cfg.backends.items():
            if not spec.profile or not _RT_RESPONSES_PROFILES[spec.profile].oauth_provider:
                continue
            state = _RtTokenState(spec.oauth)
            self._states[name] = state
            if state.refresh:
                _rt_scrub_pin(cfg.scrub, name, (state.refresh.encode("ascii"),))

    def __repr__(self) -> str:
        return "<token store>"

    def handles(self, backend: BackendSpec) -> bool:
        """True when *backend* authenticates through this store (an OAuth profile)."""
        return backend.oauth is not None and backend.name in self._states

    def _acquire(self, backend: BackendSpec) -> _RtTokenState:
        """The backend's state with its lock held (KD-11): sliced acquires bounded by
        _rt_oauth_lock_wait (read at call time); `closing` -> 529, the bound -> 503."""
        state = self._states.get(backend.name)
        if state is None:
            raise UpstreamError(502, "api_error", "backend %s does not authenticate with OAuth" % backend.name)
        end = time.monotonic() + _rt_oauth_lock_wait(backend)
        while True:
            if self.closing.is_set():
                raise ApiError(529, "overloaded_error", "router shutting down")
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise UpstreamError(503, "overloaded_error",
                                    "backend %s: a token refresh is in progress; retry" % backend.name)
            if state.lock.acquire(timeout=min(_RT_OAUTH_LOCK_SLICE_S, remaining)):
                return state

    def credential(self, backend: BackendSpec, stale: Optional[Credential] = None) -> Credential:
        """The backend's current access Credential, refreshed when due or when *stale* (the
        token an upstream 401 answered) is still the current one. Raises ApiError /
        UpstreamError, the client's error envelope."""
        state = self._acquire(backend)
        try:
            return self._credential(backend, state, stale)
        finally:
            state.lock.release()

    def _login_needed(self, backend: BackendSpec) -> ApiError:
        return ApiError(401, "authentication_error", "backend %s needs a login: llm-router.py login --backend %s"
                        % (backend.name, backend.name))

    def _credential(self, backend: BackendSpec, state: _RtTokenState, stale: Optional[Credential]) -> Credential:
        # 1. Not logged in (M3): adopt a disk login, never the dead token (R2-L2); else the hint, no POST.
        if state.refresh is None and not self._adopt(backend, state):
            raise self._login_needed(backend)
        current = state.access
        # 2. Another thread already rotated the token the 401 answered.
        if stale is not None and current is not None and current is not stale:
            return current
        if stale is None and current is not None:
            now = int(self.clock())
            # 3. Not due; earliest_refresh_at defers a due refresh unless forced or no token.
            if not _oauth_token_due(state.expires_at, now, OAUTH_REFRESH_SKEW_S):
                return current
            if state.earliest_refresh_at is not None and now < state.earliest_refresh_at:
                return current
        # 4. Negative cache: a live failure is rebuilt, with no POST (KD-11).
        failed = state.failed
        if failed is not None and self.mono() < failed[0]:
            raise failed[1](failed[2], failed[3], failed[4])
        # 5-6. Refresh.
        return self._refresh(backend, state)

    def _adopt(self, backend: BackendSpec, state: _RtTokenState) -> bool:
        """KD-10/M3: take the disk's backends.<n>.oauth when its refresh token passes the token
        rule (S5), differs from the in-memory and the dead one, and its account_id, when
        present, passes the claim rule; pin it. False (nothing changed) otherwise."""
        if self.config_path is None:
            return False
        try:
            oauth = _rt_config_read_oauth(self.config_path, backend.name)
        except (ConfigError, OSError, ValueError) as exc:
            log.warning("token store: cannot read backends.%s.oauth (%s)", _log_value(backend.name),
                        type(exc).__name__)
            return False
        if oauth is None:
            return False
        refresh = oauth.get("refresh_token")
        if not _rt_header_value_ok(refresh, "token") or refresh in (state.refresh, state.dead_refresh):
            return False
        # F37: the disk object passes the rules load_config applies to it (_rt_cfg_oauth: the
        # minimum length, differing from auth_token, the key and field shapes) or is not adopted.
        try:
            _rt_cfg_oauth(oauth, "backends.%s" % backend.name, backend.profile, self.cfg.token.decode("ascii"))
        except ConfigError:
            log.warning("token store: backends.%s.oauth on disk not adopted: it fails the config rules",
                        _log_value(backend.name))
            return False
        account = oauth.get("account_id")
        if account is not None and not _rt_header_value_ok(account, "claim"):
            return False
        client_id, host_id = oauth.get("client_id"), oauth.get("host_id")
        _rt_scrub_pin(self.cfg.scrub, backend.name, (refresh.encode("ascii"),))
        state.refresh = refresh
        state.dead_refresh = None
        state.access = None
        state.scope = None              # another grant: its scope is not known yet (F35)
        state.failed = None
        if account is not None:
            state.account_id = account
        if isinstance(client_id, str) and _RT_OAUTH_CLIENT_ID_RE.fullmatch(client_id):
            state.client_id = client_id
        if isinstance(host_id, str) and _RT_OAUTH_HOST_ID_RE.fullmatch(host_id):
            state.host_id = host_id
        return True

    def _fail(self, state: _RtTokenState, exc: Union[ApiError, UpstreamError]) -> None:
        """Store the KD-11 negative-cache entry for *exc* (never for a relogin-class failure)."""
        state.failed = (self.mono() + _RT_OAUTH_FAIL_CACHE_S, type(exc), exc.status, exc.err_type, exc.message)

    def _refresh(self, backend: BackendSpec, state: _RtTokenState, adopted: bool = False) -> Credential:
        """Step 5 (one refresh POST, validate, pin, account claims, persist, swap) and step 6
        (the failure mapping) of credential(); the caller holds the backend lock. *adopted*: a
        disk grant was already adopted in this call (KD-10 relogin, or F40's login mid-refresh),
        so no second adoption happens."""
        name = backend.name
        row = _RT_RESPONSES_PROFILES[backend.profile]
        provider = _rt_oauth_row_for(backend, _RT_OAUTH_PROVIDERS[row.oauth_provider])
        user_agent = dict(_rt_rs_identity(backend)).get("user-agent", "llm-router") if row.identity \
            else "llm-router"
        while True:
            refresh = state.refresh
            client_id = state.client_id or provider.client_id
            if refresh is None or not client_id:
                raise self._login_needed(backend)
            try:
                request = _oauth_refresh_request(provider, client_id, refresh)
                status, body = self.post(backend, request, user_agent, self.track)
                got = _oauth_parse_token_response(status, body, int(self.clock()), refresh,
                                                  previous_scope=state.scope)     # F35: RFC 6749 6
                tokens = (got["access_token"], got["refresh_token"], got["id_token"])
                # KD-12: every returned token passes the one header-value validator (S5) ...
                if not all(t is None or _rt_header_value_ok(t, "token") for t in tokens):
                    raise OAuthError("invalid_response")
                # FR-6 (openai SIWC): an ID token's iss/aud (the ISSUED id)/exp and the granted
                # scope; a failure is invalid_response and the access token is never used.
                _rt_oauth_check_answer(provider, got, client_id, None, int(self.clock()))
            except OAuthError as exc:
                if exc.kind == "relogin":
                    # KD-10: adopt a different disk token once and retry; else drop the dead one (R2-L2).
                    if not adopted and self._adopt(backend, state):
                        adopted = True
                        continue
                    log.warning("token refresh refused: backend %s, relogin (code %s)", _log_value(name),
                                _log_value(exc.code or "none"))
                    state.dead_refresh = refresh
                    state.refresh = None
                    state.access = None
                    raise ApiError(401, "authentication_error", "backend %s: the login expired or was revoked; "
                                   "run llm-router.py login --backend %s" % (name, name)) from None
                log.warning("token refresh failed: backend %s, %s (code %s)", _log_value(name),
                            _log_value(exc.kind), _log_value(exc.code or "none"))     # F42: the kind too
                if exc.kind == "rate_limited":
                    err = UpstreamError(429, "rate_limit_error",
                                        "backend %s: the token endpoint is rate limiting" % name)
                else:
                    err = UpstreamError(502, "api_error", "backend %s: token refresh failed (%s)" % (name, exc.kind))
                self._fail(state, err)
                raise err from None
            except UpstreamError as exc:
                log.warning("token refresh failed: backend %s, transport (status %d)", _log_value(name), exc.status)
                self._fail(state, exc)
                raise
            break
        access, new_refresh, id_token = tokens
        # ... and is pinned before any header use or write (KD-13).
        _rt_scrub_pin(self.cfg.scrub, name, tuple(t.encode("ascii") for t in tokens if t))
        account, residency = state.account_id, None
        if row.account_header:
            try:
                claims = _oauth_jwt_claims(access)
            except OAuthError:
                claims = None
            claim_account, claim_residency = _oauth_account_claims(claims)
            if claim_account is not None and _rt_header_value_ok(claim_account, "claim"):
                account = claim_account
                if claim_residency is not None and _rt_header_value_ok(claim_residency, "claim"):
                    residency = claim_residency
            if account is None:
                # Neither a valid claim nor a seed (S5): keep a rotated refresh token, refuse the access one.
                if new_refresh != refresh:
                    if not self._persist_rotation(backend, state, self._persisted(state, new_refresh, None,
                                                                                  got["expires_at"]), refresh, adopted):
                        return self._refresh(backend, state, adopted=True)   # F40: refresh the adopted login
                    state.refresh = new_refresh
                log.warning("token refresh failed: backend %s, invalid_response (code %s)", _log_value(name),
                            _log_value("account_claim"))
                err = UpstreamError(502, "api_error", "backend %s: token refresh failed (invalid_response)" % name)
                self._fail(state, err)
                raise err
        if new_refresh != refresh or account != state.account_id:
            if not self._persist_rotation(backend, state, self._persisted(state, new_refresh, account, got["expires_at"]),
                                          refresh, adopted):
                return self._refresh(backend, state, adopted=True)   # F40: refresh the adopted login
        cred = Credential(access_token=access, account_id=account, residency=residency)
        state.access = cred
        state.expires_at = got["expires_at"]
        state.earliest_refresh_at = got["earliest_refresh_at"]
        state.refresh = new_refresh
        state.account_id = account
        state.residency = residency
        state.scope = got["scope"]
        state.failed = None
        return cred

    @staticmethod
    def _persisted(state: _RtTokenState, refresh: str, account: Optional[str],
                   expires_at: Optional[int]) -> Dict[str, Any]:
        """The backends.<n>.oauth object to write: _RT_OAUTH_PERSIST_KEYS only, never an access or ID token."""
        oauth: Dict[str, Any] = {"refresh_token": refresh}
        for key, value in (("account_id", account), ("expires_at", expires_at),
                           ("client_id", state.client_id), ("host_id", state.host_id)):
            if value is not None:
                oauth[key] = value
        return oauth

    def _rt_persist(self, backend: BackendSpec, oauth: Dict[str, Any], started_from: Optional[str] = None,
                    dead: Optional[str] = None) -> None:
        """KD-10 steps 0-8 under write_lock (after the backend lock, before the flock). A
        ConfigError logs one WARNING (backend and type name); the token stays in memory --
        except a ConfigConflict of a refresh's rotation (*started_from* set), which propagates
        so _persist_rotation can adopt a login that landed on disk meanwhile (F40)."""
        name = backend.name
        if self.config_path is None:
            if not self._skip_logged:
                self._skip_logged = True
                log.info("token store: no config path; backends.%s.oauth kept in memory only", _log_value(name))
            return
        try:
            self._rt_write(self.config_path, backend, oauth, started_from, dead)
        except ConfigError as exc:
            if started_from is not None and isinstance(exc, ConfigConflict):
                raise
            log.warning("token store: backends.%s.oauth not persisted (%s); the token is kept in memory",
                        _log_value(name), type(exc).__name__)

    def _persist_rotation(self, backend: BackendSpec, state: _RtTokenState, oauth: Dict[str, Any],
                          started_from: str, adopted: bool) -> bool:
        """Persist a refresh's result (F40). True when it stands: written, or kept in memory after a
        logged refusal. False when the disk's refresh token changed since the refresh began (a
        `login` while the router ran): nothing was written and the disk grant has been adopted
        (KD-10), so the caller must not install this refresh's tokens. *adopted* (the refresh
        already adopted once) bounds that to one adoption per credential() call."""
        try:
            self._rt_persist(backend, oauth, started_from, state.dead_refresh)
        except ConfigConflict as exc:
            if not adopted and self._adopt(backend, state):
                log.warning("token store: backends.%s.oauth changed on disk during a refresh; the disk login "
                            "was adopted and nothing was written", _log_value(backend.name))
                return False
            log.warning("token store: backends.%s.oauth not persisted (%s); the token is kept in memory",
                        _log_value(backend.name), type(exc).__name__)
        return True

    def _rt_write(self, config_path: str, backend: BackendSpec, oauth: Dict[str, Any],
                  started_from: Optional[str] = None, dead: Optional[str] = None) -> None:
        """KD-10 steps 0-8 under write_lock; a ConfigError (the rule, never the content) propagates."""
        with self.write_lock:
            dfd = _rt_config_dir_check(config_path)     # KD-10 step 0: FIRST, before any lock file exists
            try:
                fd = _rt_config_lock(config_path, dfd, self.closing)  # KD-20, step 0a; R-0083
                try:
                    _rt_config_sweep_temps(config_path, dfd)                      # step 0b
                    self._digest = _rt_config_update_oauth(config_path, dfd, backend.name, backend.kind,
                                                           oauth, self._digest, started_from, dead)  # steps 1-8
                finally:
                    _rt_config_unlock(fd)
            finally:
                os.close(dfd)

    def login_result(self, name: str, oauth: Dict[str, Any]) -> None:
        """Persist a `login` result for backend *name* through the same writer and flock as
        _rt_persist, but a ConfigError propagates: the login exits 2 naming the rule, and a
        login that wrote nothing never reports success."""
        if self.config_path is None:
            raise ConfigError("--config: login needs a config path to persist to")
        self._rt_write(self.config_path, self.cfg.backends[name], oauth)


# Hand copy, VERBATIM from Scripts/_mcp_chrome.py (_CH_TRANSLATION_PREFIXES,
# _ch_embedded_ipv4, _ch_address_refused): the public-only classifier. J20
# compares the three by ast.dump; never edit them here.

# The IPv6 prefixes that carry an IPv4 address to a gateway: NAT64 (RFC 6052), local-use NAT64 (RFC 8215) and
# 6to4 (RFC 3056). _ch_address_refused refuses the WHOLE prefix, whatever IPv4 it embeds: a translated address
# leaves this host through a gateway whose far side cannot be classified from here (fail closed, R2-M2).
_CH_TRANSLATION_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"), ipaddress.ip_network("2002::/16"))


def _ch_embedded_ipv4(ip):
    """The IPv4 address an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) carries, else None."""
    if ip.version != 6:
        return None
    return ip.ipv4_mapped


def _ch_address_refused(ip):
    """Why the public-only policy refuses the address `ip`, or None when it may be reached (R2-M2).

    `ip` is an address string (parsed with `ipaddress.ip_address`) or an
    IPv4Address/IPv6Address. Anything that does not parse -- including an
    int, which `ip_address` would read as an address, and a decimal, octal
    or hex IPv4 spelling -- is refused ("not an IP address"): fail closed.
    The verdict, in this order:

    1. IPv4-mapped (`::ffff:0:0/96`): ONLY the embedded IPv4 is classified,
        by rule 3. The IPv6 form's own is_reserved/is_private are not
        consulted, so `::ffff:8.8.8.8` is allowed and `::ffff:10.0.0.1`
        refused on every 3.9 patch release.
    2. Inside a _CH_TRANSLATION_PREFIXES network: refused as
        "translation prefix <net>", whatever it embeds.
    3. Otherwise refused when loopback, link-local, IPv6 site-local
        (fec0::/10, deprecated by RFC 3879 but still routed inside some
        networks, and is_global on 3.9), private, reserved, multicast,
        unspecified, or not is_global (the last catches 100.64.0.0/10,
        shared address space, which is_private misses).
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return "not an IP address"
    elif not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return "not an IP address"
    prefix = ""
    embedded = _ch_embedded_ipv4(ip)
    if embedded is not None:
        ip = embedded
        prefix = "IPv4-mapped "
    elif ip.version == 6:
        for net in _CH_TRANSLATION_PREFIXES:
            if ip in net:
                return "translation prefix %s" % net
    if ip.is_loopback:
        return prefix + "loopback"
    if ip.is_link_local:
        return prefix + "link-local"
    if ip.version == 6 and ip.is_site_local:
        return "site-local"
    if ip.is_private:
        return prefix + "private"
    if ip.is_reserved:
        return prefix + "reserved"
    if ip.is_multicast:
        return prefix + "multicast"
    if ip.is_unspecified:
        return prefix + "unspecified"
    if not ip.is_global:
        return prefix + "not globally routable"
    return None


# Refused by the allow_private policy although private/ULA/CGNAT otherwise pass
# (V10): the cloud metadata addresses that are NOT link-local -- AWS's IPv6
# IMDS (a ULA) and Alibaba Cloud's (in CGNAT) -- and Teredo (RFC 4380), which
# tunnels to an embedded IPv4 like the translation prefixes do.
_RT_PRIVATE_REFUSED_NETS = ((ipaddress.ip_network("fd00:ec2::254/128"), "metadata address"),
                            (ipaddress.ip_network("100.100.100.200/32"), "metadata address"),
                            (ipaddress.ip_network("2001::/32"), "Teredo prefix 2001::/32"))


def _rt_private_policy_refused(ip: Any, allow_loopback: bool) -> Optional[str]:
    """Why the allow_private policy refuses the address `ip`, or None when it may be reached.

    `ip` is an address string or an IPv4Address/IPv6Address; anything else,
    or a string that does not parse, is "not an IP address" (fail closed).
    An IPv4-mapped address is unwrapped with _ch_embedded_ipv4 and only the
    embedded IPv4 is classified. Refused, in THIS order (load-bearing: the
    C12/C13 verdicts were checked against 3.9's ipaddress in it): a
    _CH_TRANSLATION_PREFIXES network, a _RT_PRIVATE_REFUSED_NETS network
    (fd00:ec2::254, 100.100.100.200, Teredo 2001::/32; V10), loopback unless
    `allow_loopback`, link-local (169.254.0.0/16 -- the metadata address --
    and fe80::/10), multicast, unspecified, reserved. Every other private,
    ULA, CGNAT and site-local address passes.
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return "not an IP address"
    elif not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return "not an IP address"
    prefix = ""
    embedded = _ch_embedded_ipv4(ip)
    if embedded is not None:
        ip = embedded
        prefix = "IPv4-mapped "
    elif ip.version == 6:
        for net in _CH_TRANSLATION_PREFIXES:
            if ip in net:
                return "translation prefix %s" % net
    for net, what in _RT_PRIVATE_REFUSED_NETS:
        if ip.version == net.version and ip in net:
            return prefix + what
    if ip.is_loopback and not allow_loopback:
        return prefix + "loopback"
    if ip.is_link_local:
        return prefix + "link-local"
    if ip.is_multicast:
        return prefix + "multicast"
    if ip.is_unspecified:
        return prefix + "unspecified"
    if ip.is_reserved:
        return prefix + "reserved"
    return None


def _rt_getaddrinfo(host: str, port: int) -> List[Any]:
    """`socket.getaddrinfo(host, port, type=SOCK_STREAM)`: the one resolver call (a seam C9 replaces)."""
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _rt_resolve(backend: BackendSpec, deadline: Optional[float] = None,
                track: Optional["_RtTrack"] = None) -> List[str]:
    """The backend's host resolved ONCE and every address vetted -> the ordered address list.

    Called for every connect (no cache). _rt_getaddrinfo runs on a daemon
    thread joined with what remains of the absolute `time.monotonic()`
    `deadline` (default: connect_timeout from now) -- the same deadline
    _rt_open_socket and the TLS handshake then use (V9). A resolver that has
    not answered by then is 504 "backend timed out: ..."; its thread is
    abandoned and ends when the OS resolver gives up. With a shutdown
    registry *track* (R-0095) the join runs in _RT_TICK_S slices and gives up
    once `closing` is set (529 "router shutting down", the thread abandoned
    the same way): getaddrinfo itself cannot be interrupted, so a shutdown
    waits at most one tick for a stalled resolve, never connect_timeout. Each address must
    pass _ch_address_refused (public only) unless `allow_private`, in which
    case _rt_private_policy_refused with the backend's `allow_loopback`. ANY
    refused address refuses the whole hop: UpstreamError 502 "backend
    <name>: <host> resolves to a refused address (<reason class>)". Only the
    configured host is shown -- which is the address itself only when the
    host is an IP literal -- never raw resolver output. A name that does not
    resolve, or resolves to nothing, is a 502 too. Duplicates dropped,
    resolver order kept.
    """
    host = backend.host
    shown = host if isinstance(host, str) and host.isascii() and host.isprintable() and len(host) <= 253 else repr(host)[:60]
    where = "backend %s: %s" % (backend.name, shown)
    if deadline is None:
        deadline = time.monotonic() + backend.connect_timeout
    answer: Dict[str, Any] = {}

    def resolve() -> None:
        try:
            answer["infos"] = _rt_getaddrinfo(host, backend.port)
        except BaseException as exc:  # noqa: BLE001 -- handed to the waiting thread
            answer["exc"] = exc

    resolver = threading.Thread(target=resolve, name="rt-resolve", daemon=True)
    resolver.start()
    while resolver.is_alive():
        _rt_track_check(track)
        left = deadline - time.monotonic()
        if left <= 0:
            break
        resolver.join(left if track is None else min(left, _RT_TICK_S))
    if resolver.is_alive():
        raise UpstreamError(504, "api_error", "backend timed out: %s did not resolve before the connect deadline"
                            % where)
    exc = answer.get("exc")
    if isinstance(exc, (OSError, UnicodeError)):
        raise UpstreamError(502, "api_error", "%s does not resolve (%s)" % (where, type(exc).__name__)) from None
    if exc is not None:
        raise exc
    infos = answer.get("infos") or []
    addresses: List[str] = []
    for info in infos:
        addr = info[4][0] if len(info) > 4 and info[4] else None
        if not isinstance(addr, str):
            reason: Optional[str] = "not an IP address"
        elif backend.allow_private:
            reason = _rt_private_policy_refused(addr, backend.allow_loopback)
        else:
            reason = _ch_address_refused(addr)
        if reason is not None:
            raise UpstreamError(502, "api_error", "%s resolves to a refused address (%s)" % (where, reason))
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise UpstreamError(502, "api_error", "%s resolves to no address" % where)
    return addresses


def _rt_open_socket(addresses: Iterable[str], port: int, deadline: float,
                    track: Optional["_RtTrack"] = None) -> socket.socket:
    """A TCP socket connected to the first of `addresses` that accepts, all under ONE deadline.

    _ch_open_socket's logic, raising UpstreamError: each address (an IP
    literal _rt_resolve vetted) is tried in order with what remains of the
    absolute `time.monotonic()` deadline as its connect timeout; the socket
    is returned with that timeout still set, for the caller to re-arm. It
    NEVER resolves a name (a non-literal entry fails that entry), never uses
    socket.create_connection and reads no environment variable (no
    `*_PROXY`). Refusals: the deadline passing, or every address timing out,
    is 504 "backend timed out ..."; an empty list or any other failure of
    every address is 502 "connect: ...".

    *track* (R-0095): each socket joins the shutdown registry BEFORE its
    connect, so the sweep's shutdown(SHUT_RDWR) wakes a stalled connect
    (measured on Darwin 23: the connect returns at once, reporting success);
    `closing` is checked before each address and after the connect returns,
    and is the 529 "router shutting down". A returned socket is still
    registered; a failed one was deregistered and closed.
    """
    addrs = list(addresses or ())
    if not addrs:
        raise UpstreamError(502, "api_error", "connect: no address to connect to")
    last = None
    timed_out = True
    for tried, addr in enumerate(addrs):
        _rt_track_check(track)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpstreamError(504, "api_error", "backend timed out: connect deadline passed after %d of %d addresses"
                                % (tried, len(addrs)))
        try:
            ip = ipaddress.ip_address(addr) if isinstance(addr, str) else None
        except ValueError:
            ip = None
        if ip is None:
            last = "%s is not an IP address" % repr(addr)[:60]
            timed_out = False
            continue
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(remaining)
            _rt_track_add(track, sock)          # R-0095: registered before the connect
            sock.connect((addr, port))
            _rt_track_check(track)              # a shutdown may have woken the connect
            return sock
        except socket.timeout:
            last = "%s: timed out" % addr
        except OSError as exc:
            lines = str(exc).splitlines()
            last = "%s: %s" % (addr, lines[0][:120] if lines else type(exc).__name__)
            timed_out = False
        except BaseException:
            _rt_track_discard(track, sock)
            if sock is not None:
                sock.close()
            raise
        _rt_track_discard(track, sock)
        if sock is not None:
            sock.close()
    if timed_out:
        raise UpstreamError(504, "api_error", "backend timed out: all %d addresses timed out: %s" % (len(addrs), last))
    raise UpstreamError(502, "api_error", "connect: all %d addresses failed: %s" % (len(addrs), last))


_RT_RECV_BYTES = 65536    # one _rt_read_body read1() call

# The shutdown registry a token POST's socket joins (R-0083): (srv.inflight,
# srv.inflight_lock, srv.closing). _rt_shutdown sets `closing` BEFORE it sweeps
# `inflight` under the lock, so a socket added under the lock is either swept or
# sees `closing` here and is shut down at once -- never missed. shutdown(SHUT_RDWR),
# never close() (P24): the owner still closes the descriptor.
_RtTrack = Tuple[set, threading.Lock, threading.Event]


def _rt_track_add(track: Optional[_RtTrack], sock: Optional[socket.socket]) -> None:
    """Register *sock* in *track* (None: no registry, a no-op); once `closing` is set, shut it down."""
    if track is None or sock is None:
        return
    inflight, lock, closing = track
    with lock:
        inflight.add(sock)
        if closing.is_set():
            try:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)   # base class, also on an SSLSocket
            except OSError:
                pass


def _rt_track_discard(track: Optional[_RtTrack], sock: Optional[socket.socket]) -> None:
    """Deregister *sock* from *track*; a no-op for None or an unregistered socket."""
    if track is None or sock is None:
        return
    with track[1]:
        track[0].discard(sock)


_RT_SHUTTING_DOWN = "router shutting down"


def _rt_track_check(track: Optional[_RtTrack]) -> None:
    """529 "router shutting down" once *track*'s `closing` is set (R-0095); a no-op without a registry."""
    if track is not None and track[2].is_set():
        raise UpstreamError(529, "overloaded_error", _RT_SHUTTING_DOWN)


class _RtHttpConnection(http.client.HTTPConnection):
    """A plain-HTTP connection to ONE vetted backend: connect() is _rt_resolve, then _rt_open_socket.

    `deadline` is the absolute monotonic() connect deadline the resolve and
    the connect share (V9). After connect the socket's timeout is the
    backend's idle_timeout (one upstream read), and `raw_sock` keeps the
    connected socket: http.client drops `sock` once a Connection: close
    response is read, and the pump's shutdown(SHUT_RDWR) needs the
    descriptor (KD-4, P24). *track* (R-0083, R-0095) is the shutdown
    registry: the resolve gives up on `closing`, the socket joins it before
    its connect and leaves it only through untrack(), which the owner calls
    -- http.client's own close() on a Connection: close head does not.
    """

    def __init__(self, backend: BackendSpec, deadline: float, track: Optional[_RtTrack] = None):
        super().__init__(backend.host, backend.port)
        self._backend = backend
        self._deadline = deadline
        self._track = track
        self.raw_sock: Optional[socket.socket] = None

    def connect(self) -> None:
        addresses = _rt_resolve(self._backend, self._deadline, self._track)
        sock = _rt_open_socket(addresses, self.port, self._deadline, self._track)
        self.raw_sock = sock
        sock.settimeout(self._backend.idle_timeout)
        self.sock = sock

    def untrack(self) -> None:
        """Leave the shutdown registry (R-0083); idempotent."""
        _rt_track_discard(self._track, self.raw_sock)


class _RtHttpsConnection(http.client.HTTPSConnection):
    """A verified-TLS connection to ONE vetted backend (P20).

    The context is create_default_context(cadata=ca_pem) when the backend has
    a ca_file (which then REPLACES the system trust store, KD-18), else
    create_default_context(); the floor is raised to TLS 1.2 (3.9 leaves
    MINIMUM_SUPPORTED) and OP_IGNORE_UNEXPECTED_EOF is cleared, so a ragged
    EOF stays an error and a truncated upstream is detected (D12). Then
    CERT_REQUIRED, check_hostname and the floor are ASSERTED: any miss is an
    UpstreamError 502 before a socket is opened. connect() is _rt_resolve and
    _rt_open_socket plus the handshake under the same deadline,
    suppress_ragged_eofs=False; then the idle timeout, and `raw_sock` is the
    SSLSocket. *track* as for _RtHttpConnection: the TCP socket joins it
    before its connect, the SSLSocket replaces it before the handshake, so a
    shutdown interrupts a stalled connect or handshake too.
    """

    def __init__(self, backend: BackendSpec, deadline: float, track: Optional[_RtTrack] = None):
        if backend.ca_pem:
            context = ssl.create_default_context(cadata=backend.ca_pem)
        else:
            context = ssl.create_default_context()
        if int(context.minimum_version) < int(ssl.TLSVersion.TLSv1_2):
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        ignore_eof = getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
        if ignore_eof:
            context.options &= ~ignore_eof
        if (not isinstance(context, ssl.SSLContext) or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname):
            raise UpstreamError(502, "api_error", "backend %s: the ssl context does not verify the "
                                "certificate and the host name" % backend.name)
        if int(context.minimum_version) < int(ssl.TLSVersion.TLSv1_2):
            raise UpstreamError(502, "api_error", "backend %s: the ssl context allows a protocol "
                                "version below TLS 1.2" % backend.name)
        super().__init__(backend.host, backend.port, context=context)
        self._backend = backend
        self._rt_context = context
        self._deadline = deadline
        self._track = track
        self.raw_sock: Optional[socket.socket] = None

    def connect(self) -> None:
        addresses = _rt_resolve(self._backend, self._deadline, self._track)
        sock = _rt_open_socket(addresses, self.port, self._deadline, self._track)   # registered
        tls: Optional[ssl.SSLSocket] = None
        try:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("the connect deadline passed")
            sock.settimeout(remaining)
            # wrap_socket detaches `sock` before any handshake, so the SSLSocket replaces it
            # in the registry -- added first, so it is never absent -- and the handshake
            # runs only after it did (R-0083).
            tls = self._rt_context.wrap_socket(sock, server_hostname=self._backend.host,
                                               suppress_ragged_eofs=False, do_handshake_on_connect=False)
            self.raw_sock = tls
            _rt_track_add(self._track, tls)
            _rt_track_discard(self._track, sock)
            tls.do_handshake()
        except BaseException:
            _rt_track_discard(self._track, sock)
            if tls is not None:
                _rt_track_discard(self._track, tls)
                self.raw_sock = None
                tls.close()
            sock.close()
            raise
        tls.settimeout(self._backend.idle_timeout)
        self.sock = tls

    def untrack(self) -> None:
        """Leave the shutdown registry (R-0083); idempotent."""
        _rt_track_discard(self._track, self.raw_sock)


def _rt_one_line(text: Any) -> str:
    """The first line of `text`, cut before the first quoted value (never echo a header or URL),
    printable, bounded -- _ChFallbackConnection._one_line's rule."""
    lines = str(text).splitlines()
    line = lines[0] if lines else ""
    for mark in ("'", '"'):
        cut = line.find(mark)
        if cut >= 0:
            line = line[:cut]
    line = line.rstrip()
    if line.endswith(" b"):
        line = line[:-2]
    line = line.rstrip(" (:.,")
    line = "".join(c if c.isprintable() else "?" for c in line[:160])
    return line or "refused"


def _rt_upstream_refusal(exc: BaseException, backend_name: str) -> Optional[UpstreamError]:
    """`exc` as the one-line UpstreamError of an upstream hop (the P21 shape), or None to propagate it.

    An UpstreamError is returned as itself. A certificate failure is 502
    "backend <n>: certificate verify failed: <reason>"; a timeout 504
    "backend <n> timed out"; another ssl error 502 "...: tls: <reason>"; a
    malformed response 502 "...: malformed response (<type>)" with no peer
    byte; a ValueError 502 cut before its first quoted value; any other
    OSError 502 "...: transport error: ...". Anything else (KeyboardInterrupt,
    a programming error) is None: the caller re-raises it unchanged.
    """
    label = "backend %s" % backend_name
    if isinstance(exc, UpstreamError):
        return exc
    if isinstance(exc, ssl.SSLCertVerificationError):
        what = getattr(exc, "verify_message", None) or "the chain or the host name did not verify"
        return UpstreamError(502, "api_error", "%s: certificate verify failed: %s" % (label, _rt_one_line(what)))
    if isinstance(exc, socket.timeout):
        return UpstreamError(504, "api_error", "%s timed out" % label)
    if isinstance(exc, ssl.SSLError):
        reason = exc.reason if isinstance(exc.reason, str) and exc.reason else type(exc).__name__
        return UpstreamError(502, "api_error", "%s: tls: %s" % (label, _rt_one_line(reason)))
    if isinstance(exc, http.client.InvalidURL):
        return UpstreamError(502, "api_error", "%s: invalid URL" % label)
    if isinstance(exc, http.client.HTTPException):
        return UpstreamError(502, "api_error", "%s: malformed response (%s)" % (label, type(exc).__name__))
    if isinstance(exc, ValueError):
        return UpstreamError(502, "api_error", "%s: %s" % (label, _rt_one_line(str(exc))))
    if isinstance(exc, OSError):
        return UpstreamError(502, "api_error", "%s: transport error: %s"
                             % (label, _rt_one_line(exc.strerror or type(exc).__name__)))
    return None


def _rt_send(backend: BackendSpec, path: str, headers: List[Tuple[str, str]],
             body: bytes, track: Optional[_RtTrack] = None) -> Tuple[http.client.HTTPConnection,
                                                                     http.client.HTTPResponse]:
    """POST `body` to backend.base_path + path -> (conn, resp) with the final response head read.

    The ONLY putheader site in the file (J4a) and the single framing owner:
    the caller's `headers` (built by _rt_upstream_headers in _post, or by
    _rt_oauth_headers in _rt_oauth_post -- J4a's two paired sites) go through
    putheader one by one, so http.client's injection check is a second line
    of defence; then `Accept-Encoding: identity` and `Content-Length:
    str(len(body))` are added HERE, after them (endheaders(body) never adds a
    Content-Length; C3). The host is resolved and vetted per call, by the
    connection's connect() (R-0095), under the same connect deadline as the
    connect and the TLS handshake (V9). No
    redirect is followed (a 3xx is a 502), an interim 1xx is a 502, and a
    Content-Encoding other than identity is a 502. Every failure closes the
    connection and is raised as _rt_upstream_refusal's UpstreamError.
    The caller owns the returned pair and closes both.

    *track* (R-0083, J4a amended) is the shutdown registry, DATA handed only to
    the connection constructor -- never called, never given the connection --
    so it frames nothing: the resolve gives up on its `closing`, the socket
    joins it before its connect (and before a TLS handshake), a failure here
    leaves it, and on success the caller calls conn.untrack(). Both send
    sites pass it (R-0095: _post, as R-0083's _rt_oauth_post).
    """
    deadline = time.monotonic() + backend.connect_timeout
    cls: Type[Union[_RtHttpConnection, _RtHttpsConnection]] = (_RtHttpsConnection if backend.scheme == "https"
                                                               else _RtHttpConnection)
    conn = cls(backend, deadline, track)
    resp: Optional[http.client.HTTPResponse] = None
    try:
        conn.putrequest("POST", backend.base_path + path, skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        conn.putheader("Accept-Encoding", "identity")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        if 100 <= resp.status < 200:
            raise UpstreamError(502, "api_error", "backend %s: unexpected interim response %d"
                                % (backend.name, resp.status))
        if 300 <= resp.status < 400:
            raise UpstreamError(502, "api_error", "backend %s answered a redirect; redirects are not followed"
                                % backend.name)
        encoding = (resp.getheader("Content-Encoding") or "").strip().lower()
        if encoding not in ("", "identity"):
            raise UpstreamError(502, "api_error", "backend %s: answered with a Content-Encoding other "
                                "than identity" % backend.name)
    except BaseException as exc:
        if resp is not None:
            resp.close()
        conn.close()
        conn.untrack()
        refusal = _rt_upstream_refusal(exc, backend.name)
        if refusal is None or refusal is exc:
            raise
        raise refusal from None
    return conn, resp


def _rt_read_body(resp: http.client.HTTPResponse, limit: int, backend_name: str = "") -> bytes:
    """The whole upstream body under `limit`, read with read1() (the read_body pattern, P21).

    A declared Content-Length over `limit`, or more than `limit` bytes read,
    is a 502; a body that ends before its declared Content-Length is a 502;
    a read failure goes through _rt_upstream_refusal. The response is not
    closed here: its owner closes it.
    """
    label = "backend %s" % backend_name if backend_name else "the backend"
    body = bytearray()
    declared = resp.length
    try:
        if declared is not None and declared > limit:
            raise UpstreamError(502, "api_error", "%s: the body exceeds %d bytes" % (label, limit))
        while not resp.isclosed():
            chunk = resp.read1(min(_RT_RECV_BYTES, limit - len(body) + 1))
            if not chunk:
                break
            body += chunk
            if len(body) > limit:
                raise UpstreamError(502, "api_error", "%s: the body exceeds %d bytes" % (label, limit))
        if declared is not None and len(body) != declared:
            raise UpstreamError(502, "api_error", "%s: the body ended after %d of its %d Content-Length bytes"
                                % (label, len(body), declared))
    except BaseException as exc:
        refusal = _rt_upstream_refusal(exc, backend_name or "?")
        if refusal is None or refusal is exc:
            raise
        raise refusal from None
    return bytes(body)


def _rt_upstream_headers(inbound: Any, head: Optional[Tuple[Optional[str], Tuple[Tuple[str, str], ...]]] = None,
                         cred: Optional[Credential] = None) -> List[Tuple[str, str]]:
    """The upstream request headers of `inbound`, built from an ALLOW-list (KD-3).

    Duck-typed on inbound.backend, inbound.client_headers and inbound.stream;
    *head* is the adapter's upstream_head(inbound, cred) answer (accept or None,
    extra pairs) and *cred* the token store's Credential, both optional.
    Order: Content-Type, Accept (head's accept when not None, else
    text/event-stream when streaming, else application/json), User-Agent (the
    `user-agent` pair of head's extras when present, else "llm-router"), then
    each client header whose lower-case name is in backend.forward_headers
    (once), then head's remaining extra pairs in their order, then the
    credential LAST: `Authorization: Bearer <cred.access_token>` when *cred* is
    set (its value passes the `token` rule, S5, else a 502), else
    `Authorization: Bearer <api_key>` or `x-api-key: <api_key>` per
    auth_header, nothing for "none". The ONLY reader of `client_headers`
    (J4b) and the only builder of the credential line. Content-Length and
    Accept-Encoding are _rt_send's.
    """
    backend = inbound.backend
    accept, extra = head if head is not None else (None, ())
    if accept is None:
        accept = "text/event-stream" if inbound.stream else "application/json"
    user_agent = "llm-router"
    rest: List[Tuple[str, str]] = []
    for name, value in extra:
        if name.lower() == "user-agent":
            user_agent = value
        else:
            rest.append((name, value))
    headers = [("Content-Type", "application/json"),
               ("Accept", accept),
               ("User-Agent", user_agent)]
    allowed = backend.forward_headers
    seen = set()
    for name, value in (inbound.client_headers or {}).items():
        key = name.lower() if isinstance(name, str) else None
        if key is None or key not in allowed or key in seen:
            continue
        seen.add(key)
        headers.append((key, value))
    headers.extend(rest)
    if cred is not None:
        if not _rt_header_value_ok(cred.access_token, "token"):
            raise UpstreamError(502, "api_error", "backend %s: the access token is not a valid header value"
                                % backend.name)
        headers.append(("Authorization", "Bearer %s" % cred.access_token))
    elif backend.auth_header == "authorization" and backend.api_key:
        headers.append(("Authorization", "Bearer %s" % backend.api_key))
    elif backend.auth_header == "x-api-key" and backend.api_key:
        headers.append(("x-api-key", backend.api_key))
    return headers


# The cross-process config lock (KD-20): a sidecar "<config>.lock" flock that
# serializes the router and `login` around KD-10 steps 0b-8. It lives in unit 6
# because it polls with time.sleep (J4c keeps clocks out of unit 2).
_RT_CONFIG_LOCK_WAIT_S = 5.0
_RT_CONFIG_LOCK_POLL_S = 0.05


def _rt_config_lock(path: str, dir_fd: int, closing: Optional[threading.Event] = None) -> int:
    """KD-20: open "<base>.lock" relative to dir_fd (O_RDWR|O_CREAT|O_NOFOLLOW, 0600; OSError ->
    ConfigError "cannot open the config lock"), fstat (regular, euid, & 0o077 == 0),
    flock(LOCK_EX|LOCK_NB) polled every 0.05 s for _RT_CONFIG_LOCK_WAIT_S; ConfigError on any refusal.
    *closing* (R-0083, the router's): once set, the poll gives up with a ConfigError at its next
    turn, so a handler waiting on another process's lock never outlives the shutdown drain."""
    try:
        fd = os.open(os.path.basename(path) + ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                     0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise ConfigError(f"--config: cannot open the config lock ({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError("--config: the config lock is not a regular file")
        if st.st_uid != os.geteuid():
            raise ConfigError("--config: refusing a config lock not owned by the current user")
        if st.st_mode & 0o077:
            raise ConfigError("--config: refusing a config lock readable or writable by group/others (chmod 600)")
        deadline = time.monotonic() + _RT_CONFIG_LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConfigError("--config: another process holds the config lock") from None
                if closing is not None and closing.is_set():
                    raise ConfigError("--config: the router is shutting down; the config lock was not "
                                      "taken") from None
            time.sleep(_RT_CONFIG_LOCK_POLL_S)
    except OSError as exc:
        os.close(fd)
        raise ConfigError(f"--config: cannot lock the config lock ({type(exc).__name__})") from None
    except BaseException:
        os.close(fd)
        raise


def _rt_config_unlock(fd: int) -> None:
    """flock(LOCK_UN) then os.close; never raises; the lock file is never deleted."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _rt_startup_sweep(path: str, cfg: RouterConfig) -> None:
    """KD-20 startup sweep. Returns at once (no lock file, no sweep) unless some backend's profile
    has an oauth_provider (R2-L5). Otherwise: dfd = _rt_config_dir_check(path); then lock, sweep,
    unlock, close dfd. A ConfigError is logged as one WARNING (rule only) and swallowed."""
    if not any(spec.profile and _RT_RESPONSES_PROFILES[spec.profile].oauth_provider
               for spec in cfg.backends.values()):
        return
    try:
        dfd = _rt_config_dir_check(path)
        try:
            fd = _rt_config_lock(path, dfd)
            try:
                _rt_config_sweep_temps(path, dfd)
            finally:
                _rt_config_unlock(fd)
        finally:
            os.close(dfd)
    except ConfigError as exc:
        log.warning("config startup sweep skipped: %s", exc)


# ---------------------------------------------------------------------------
# Unit 7: HTTP front
# ---------------------------------------------------------------------------

# Pump sentinels: the upstream ended cleanly / sent one line over _SSE_LINE_LIMIT.
# A failed read is neither: it is the item ("exc", <type name>), so a truncated
# upstream (a ragged TLS EOF raises, OP_IGNORE_UNEXPECTED_EOF is cleared; a cut
# chunked body raises IncompleteRead) never reads as a clean end (KD-7, D12).
_EOF, _LINE_TOO_LONG = object(), object()


class _RtByteQueue:
    """A deque under a Condition, bounded by the BYTES it holds, not by items
    (NFR-2b). A sentinel or exception item is put with nbytes=0, so it never
    waits. The pump is the only producer, the relay loop the only consumer."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._items: collections.deque = collections.deque()
        self._cond = threading.Condition()
        self._used = 0

    def put(self, item: Any, nbytes: int, stop: threading.Event) -> bool:
        """Append `item`, waiting in _RT_TICK_S slices while it would overrun
        the budget. An item larger than the whole budget is still accepted
        into an EMPTY queue (one line is bounded by _SSE_LINE_LIMIT anyway).
        False once `stop` is set: the consumer is gone, stop reading."""
        with self._cond:
            while self._items and self._used + nbytes > self._budget:
                if stop.is_set():
                    return False
                self._cond.wait(_RT_TICK_S)
            if stop.is_set():
                return False
            self._items.append((item, nbytes))
            self._used += nbytes
            self._cond.notify_all()
            return True

    def get(self, timeout: float) -> Any:
        """The oldest item; raises queue.Empty after `timeout` seconds. Its
        bytes go back to the budget and a waiting producer is woken."""
        with self._cond:
            if not self._items:
                self._cond.wait(timeout)
                if not self._items:
                    raise queue.Empty
            item, nbytes = self._items.popleft()
            self._used -= nbytes
            self._cond.notify_all()
            return item


def _rt_pump(conn: Any, resp: Any, q: _RtByteQueue, stop: threading.Event,
             line_limit: int = _SSE_LINE_LIMIT) -> None:
    """The upstream reader thread (KD-4): the ONLY thread that reads `resp`.

    Started as threading.Thread(target=_rt_pump, name="rt-pump", daemon=True).
    `line_limit` is the kind's StreamPolicy.line_limit (default _SSE_LINE_LIMIT, M2).
    Puts each line, then exactly one terminal item: _EOF, _LINE_TOO_LONG or
    ("exc", type name) -- the last also when the handler unblocks it with
    socket.socket.shutdown(conn.raw_sock, SHUT_RDWR). Stops early once a put
    returns False. In its finally it performs the final resp.close() and
    conn.close() itself, on the thread that owns the reader (P24: closing
    from another thread neither unblocks nor releases a blocked readline).
    """
    try:
        while True:
            # readline(limit) is NOT a hard cut on a chunked body (measured,
            # task-033): a chunked line of limit+1 bytes comes back whole, a
            # 4 MiB one returns ~1.06 MB on 3.9 and ~1.18 MB on 3.14; only an
            # unchunked body is cut exactly at the limit. So an over-long line
            # is detected by its length, never by a missing trailing newline,
            # and the per-line memory bound is the limit plus one read-ahead
            # window, not the limit itself.
            line = resp.readline(line_limit + 1)
            if len(line) > line_limit:
                q.put(_LINE_TOO_LONG, 0, stop)
                return
            if not line:
                q.put(_EOF, 0, stop)
                return
            if not q.put(line, len(line), stop):
                return
    except BaseException as exc:  # noqa: BLE001 -- relayed as an item, by type name only
        q.put(("exc", type(exc).__name__), 0, stop)
    finally:
        try:
            resp.close()
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_httpfront.py :: _http_write_ready_file, _http_remove_ready_file
def _http_write_ready_file(path: str, port: int, error) -> None:
    """{"port", "pid"} written 0600 and atomically (.tmp + os.replace); a failure
    raises the host's *error* class naming the strerror or the type only."""
    tmp = path + ".tmp"
    try:
        if os.path.lexists(tmp):
            os.remove(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "pid": os.getpid()}, fh)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.remove(tmp)
    except OSError as exc:
        raise error(f"cannot write --ready-file: {exc.strerror or type(exc).__name__}") from None


def _http_remove_ready_file(path: str, logger) -> None:
    """On an ordered shutdown, unlink the ready file only while it still holds
    this process's pid (mcp-proxy's R-0075, the router's V24).

    Read without following a symlink (O_NOFOLLOW: a link at the path is
    refused, so neither it nor its target is touched); a file another writer
    has replaced (another pid, not JSON, unreadable) is left alone. Best
    effort: never raises; a refusal is logged on the host's *logger* at DEBUG
    by type only.

    Declared limits: a replace between the read and the unlink is not seen
    (the new file is removed); SIGKILL, or any exit that skips the ordered
    shutdown, leaves the file behind with a dead pid.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read(4096))
        pid = data.get("pid") if isinstance(data, dict) else None
        if type(pid) is int and pid == os.getpid():
            os.remove(path)
        else:
            logger.debug("ready file left: not this pid")
    except (OSError, ValueError) as exc:
        logger.debug("ready file left: %s", type(exc).__name__)
# END GENERATED: 1c2c5cd7b652


class _RouterHttpServer(ThreadingHTTPServer):
    """The listener: one daemon thread per connection, at most
    settings.max_connections of them (CWE-400); never joined on close (H1).
    The shutdown drain waits on `active` instead, and unblocks live upstream
    reads through the raw sockets in `inflight` (KD-4)."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    # The generated server methods log through this, never the module global (R-0072, PD-3).
    front_log = log

    def __init__(self, addr: Tuple[str, int], settings: RouterSettings, cfg: RouterConfig,
                 tokens: Optional[_RtTokenStore] = None) -> None:
        # Everything a handler reads is set BEFORE super().__init__, which
        # binds and listens (L4).
        self.settings = settings
        self.cfg = cfg
        self.conn_sem = threading.BoundedSemaphore(settings.max_connections)
        self.closing = threading.Event()
        self.loopback = ipaddress.IPv4Address(settings.bind).is_loopback
        # Raw upstream sockets, every one from its connect on -- a model call's
        # (R-0095) and a token POST's (R-0083): shutdown(SHUT_RDWR) unblocks a
        # connect or a read, never close() (P24). `track` is the registry _rt_send takes.
        self.inflight: set = set()
        self.inflight_lock = threading.Lock()
        self.track: _RtTrack = (self.inflight, self.inflight_lock, self.closing)
        # The OAuth token store, bound to this server: its lock waiters see
        # `closing` (529), its token POSTs register in `inflight` from the
        # connect on and its config-flock poll gives up on `closing` (N25, N41, N42).
        self.tokens = tokens if tokens is not None else _RtTokenStore(None, cfg)
        self.tokens.closing = self.closing
        self.tokens.track = self.track
        # Running handler threads, for the shutdown drain.
        self.active = 0
        self.active_cond = threading.Condition()
        # GET /v1/models's created_at for every entry: the start time, second precision, UTC.
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # The learned sampling drop: (backend name, upstream model) -> the sampling names that
        # model refused (an upstream 400 "Unsupported parameter"), for the process lifetime.
        # Keyed by configured names only, so bounded by the config's routes; under sampling_lock.
        self.sampling_unsupported: Dict[Tuple[str, str], FrozenSet[str]] = {}
        self.sampling_noted: set = set()     # (backend, model, name): a dropped override logged once
        # The learned effort set, beside it and under the same lock: (backend name, upstream model)
        # -> the reasoning_effort values that model's 400 listed as supported (mistral), replaced
        # by a later learn; bounded the same way.
        self.effort_supported: Dict[Tuple[str, str], FrozenSet[str]] = {}
        self.sampling_lock = threading.Lock()
        super().__init__(addr, _RouterHandler)

    def sampling_learned(self, backend: str, model: str) -> FrozenSet[str]:
        """The sampling names (backend, model) is known to refuse; empty when none."""
        with self.sampling_lock:
            return self.sampling_unsupported.get((backend, model), frozenset())

    def sampling_learn(self, backend: str, model: str, name: str) -> FrozenSet[str]:
        """Record that (backend, model) refuses *name*; the names it refuses now."""
        with self.sampling_lock:
            names = self.sampling_unsupported.get((backend, model), frozenset()) | {name}
            self.sampling_unsupported[(backend, model)] = names
        log.debug("sampling unsupported: backend=%s model=%s param=%s",
                  _log_value(backend), _log_value(model), _log_value(name))
        return names

    def effort_learned(self, backend: str, model: str) -> FrozenSet[str]:
        """The reasoning_effort values (backend, model) is known to support; empty when none learned."""
        with self.sampling_lock:
            return self.effort_supported.get((backend, model), frozenset())

    def effort_learn(self, backend: str, model: str, supported: FrozenSet[str]) -> FrozenSet[str]:
        """Record *supported* as the reasoning_effort values (backend, model) takes; logged at
        INFO once per change of the set (names of _RT_MS_EFFORTS only). Returns *supported*."""
        with self.sampling_lock:
            changed = self.effort_supported.get((backend, model)) != supported
            self.effort_supported[(backend, model)] = supported
        if changed:
            log.info("reasoning_effort learned: backend=%s model=%s supported=%s",
                     _log_value(backend), _log_value(model), _log_value(",".join(sorted(supported))))
        return supported

    def sampling_note_once(self, backend: str, model: str, name: str) -> bool:
        """True the first time (backend, model, name) is noted (a dropped route override)."""
        with self.sampling_lock:
            key = (backend, model, name)
            if key in self.sampling_noted:
                return False
            self.sampling_noted.add(key)
            return True

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
    # BEGIN GENERATED: _mcp_httpfront.py :: server_bind, process_request, handle_error
    def server_bind(self) -> None:
        # The stdlib HTTPServer.server_bind does a reverse-DNS getfqdn() the
        # host never uses and that can stall startup (I1).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address) -> None:
        # The slot taken here is given back by the host's hand-written
        # process_request_thread, in a finally (tests/test_generated_region.py GI1).
        if not self.conn_sem.acquire(blocking=False):
            self.front_log.warning("http connection refused: %d connections open", self.settings.max_connections)
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            # The handler thread did not start: give its slot back. The stdlib
            # caller then runs handle_error and shutdown_request.
            self.conn_sem.release()
            raise

    def handle_error(self, request, client_address) -> None:
        # Type only: the stdlib traceback's message text can quote request data (ADR 0011).
        self.front_log.warning("http handler failed: %s", type(sys.exc_info()[1]).__name__)
    # END GENERATED: 4aa2226407c5

    def process_request_thread(self, request, client_address) -> None:
        with self.active_cond:
            self.active += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.active_cond:
                self.active -= 1
                self.active_cond.notify_all()
            self.conn_sem.release()


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_httpfront.py :: _HeaderDeadlineReader
class _HeaderDeadlineReader(io.RawIOBase):
    """The raw reader under a handler's rfile: enforces a TOTAL header deadline.

    While `deadline` (a time.monotonic() value) is set, every recv gets at most
    the time left until it, never more than *cap* seconds, and none is
    attempted once it has passed: a client that trickles one header byte per
    recv can no longer hold its --max-connections slot past the host's header
    bound in total (F8). The socket.timeout raised is the one the stdlib's
    handle_one_request already answers by closing the connection. A host may
    set `deadline` again around its body read (the router's _post does, V4).
    With `deadline` None it is a plain recv under the socket's own timeout.
    """

    def __init__(self, sock: socket.socket, cap: float) -> None:
        super().__init__()
        self._sock = sock
        self._cap = cap
        self.deadline: Optional[float] = None

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("header deadline passed")
            self._sock.settimeout(min(left, self._cap))
        return self._sock.recv_into(buf)
# END GENERATED: b5cec0ec04cb


# Extra headers of the stdlib's own refusals (send_error: 400/414/431/501/505),
# answered fixed and body-less (R-0069).
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_httpfront.py :: _HTTP_STDLIB_REFUSAL_HEADERS
_HTTP_STDLIB_REFUSAL_HEADERS = (("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"))
# END GENERATED: 322ef814d1a3


# The fixed reason text of every refusal the stdlib raises itself, before any
# do_* runs (B21). The stdlib's own message/explain are never used: the 501
# text quotes the method and the 400 texts quote the request line.
_RT_STDLIB_REFUSAL = {
    400: "malformed request",
    414: "request line too long",
    431: "header block too large",
    501: "method not supported",
    505: "HTTP version not supported",
}

# The fixed paths the router answers -> the endpoint name the hub dispatches on.
# /v1/models/{id} is the one variable path: _rt_front_endpoint names it "model".
_ENDPOINTS = {"/v1/messages": "messages", "/v1/messages/count_tokens": "count_tokens",
              "/v1/models": "models"}
_RT_MODELS_PREFIX = "/v1/models/"
_RT_GET_ENDPOINTS = frozenset({"models", "model"})     # GET only; every other endpoint is POST only

# G-M1 DEFAULT: the query strings a client may send. Replace with exactly the set
# M1 records once it is measured; anything else is a 404.
_RT_ALLOWED_QUERIES = frozenset({"", "beta=true"})

# The query keys GET /v1/models[/{id}] accepts (values ignored: no pagination);
# any other key is a 400.
_RT_MODELS_QUERY_KEYS = frozenset({"limit", "before_id", "after_id", "beta"})


def _rt_front_endpoint(path: str) -> Tuple[Optional[str], Optional[str]]:
    """(endpoint, raw model id) of a request path without its query: a fixed
    path of _ENDPOINTS, or ("model", <segment>) for /v1/models/<exactly one
    non-empty segment>, still percent-encoded; (None, None) otherwise."""
    endpoint = _ENDPOINTS.get(path)
    if endpoint is not None:
        return endpoint, None
    if path.startswith(_RT_MODELS_PREFIX):
        segment = path[len(_RT_MODELS_PREFIX):]
        if segment and "/" not in segment:
            return "model", segment
    return None, None


def _rt_models_query_ok(query: str) -> bool:
    """True when every key of *query* is in _RT_MODELS_QUERY_KEYS; a query that
    does not parse as key=value pairs is refused too."""
    if not query:
        return True
    try:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return all(key in _RT_MODELS_QUERY_KEYS for key, _value in pairs)

_RT_MISPLACED_TOKEN = "the router token must appear only in the Authorization header"


class _RouterHandler(BaseHTTPRequestHandler):
    """The Anthropic Messages front: every answer is JSON or SSE, every error
    the Anthropic envelope. Runs on a listener thread and owns only its socket
    (and, while it streams, the upstream pump it started)."""

    protocol_version = "HTTP/1.1"
    server_version = "llm-router"
    sys_version = ""
    # Applied by StreamRequestHandler.setup(): a connection that stalls in the
    # request line or headers is closed by the stdlib (CWE-400).
    timeout = _HTTP_HEADER_TIMEOUT_S

    def setup(self) -> None:
        super().setup()
        # Replace the stdlib's rfile with one whose raw reader enforces the total
        # header deadline. The stdlib's own is closed at once: an unclosed
        # makefile keeps the socket's fd alive after close_request (P24).
        stock = self.rfile
        # the cap is the socket timeout: _post sets a body-read deadline on this reader too (V4)
        self._header_reader = _HeaderDeadlineReader(self.connection, _HTTP_SOCKET_TIMEOUT_S)
        self.rfile = io.BufferedReader(self._header_reader, io.DEFAULT_BUFFER_SIZE)
        stock.close()
        # True once a request on this connection passed _precheck (V3).
        self._rt_authed = False

    def handle_one_request(self) -> None:
        # Every keep-alive request starts in the header phase again: request
        # line + headers within _HTTP_HEADER_TIMEOUT_S in TOTAL (F8). A
        # connection that never authenticated gets _HTTP_PREAUTH_TIMEOUT_S (V3):
        # it holds a --max-connections slot for less time. A mitigation only --
        # a peer that reconnects can still keep every slot busy.
        bound = _HTTP_HEADER_TIMEOUT_S if self._rt_authed else _HTTP_PREAUTH_TIMEOUT_S
        self._header_reader.deadline = time.monotonic() + bound
        self.connection.settimeout(bound)
        try:
            super().handle_one_request()
        finally:
            self._header_reader.deadline = None

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
    # BEGIN GENERATED: _mcp_httpfront.py :: parse_request, handle_expect_100, _single_header
    def parse_request(self) -> bool:
        # The header phase ends here, before any do_* runs: the body read and
        # the SSE stream are never under the header deadline. The per-recv
        # timeout becomes the handler's class-level `timeout` (the header
        # bound) until the host's precheck passes -- longer than the router's
        # pre-auth header bound. Harmless: no socket read happens between
        # here and the precheck verdict (the precheck reads only the parsed
        # request line and headers), so the only socket operation under it
        # before authentication is a refusal's own write, the first on the
        # connection (measured, R-0091).
        try:
            return super().parse_request()
        finally:
            self._header_reader.deadline = None
            self.connection.settimeout(self.timeout)

    def handle_expect_100(self) -> bool:
        # Called from parse_request, before any auth: send nothing (mcp-proxy's
        # R-0076, the router's V37). The host's POST handler sends the 100
        # Continue itself once its precheck and framing checks pass.
        return True

    def _single_header(self, name: str):
        """(ok, value) of a header that must appear at most once (ADR 0015:
        ambiguity is the defect): ok False when it is repeated, value None when absent."""
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            return False, None
        return True, (values[0] if values else None)
    # END GENERATED: e4eaaa5ee6d4

    def log_message(self, format, *args) -> None:  # noqa: A002 -- stdlib signature
        # Structure only (ADR 0011): never the stdlib's `format % args` text,
        # which can quote the raw request line; never headers, query or body.
        # Called before command/path exist on a timeout or a malformed line.
        # Only a routed path and a method with a do_* handler are logged: any
        # other is the client's text, unauthenticated, and may carry a token (V21).
        # A /v1/models/{id} path is the literal /v1/models/<id>: the id is client text too.
        if log.isEnabledFor(logging.DEBUG):
            path_only = (getattr(self, "path", "") or "").split("?", 1)[0]
            if path_only in _ENDPOINTS:
                shown = _log_value(path_only)
            elif _rt_front_endpoint(path_only)[0] == "model":
                shown = _RT_MODELS_PREFIX + "<id>"
            else:
                shown = "<unrouted>"
            command = getattr(self, "command", None) or "-"
            if command != "-" and not hasattr(self, "do_" + command):
                command = "<other>"
            log.debug("http %s %s", _log_value(command), shown)

    def _refuse(self, status: int, err_type: Optional[str] = None, message: str = "",
                extra_headers=()) -> None:
        """Answer with the Anthropic error envelope and close: used for every
        response sent before the body is consumed, so unread body bytes are never
        parsed as the next request. A HEAD gets the head only (Content-Length: 0).

        A 401 or 403 is logged at WARNING with the status and the peer address
        only (F41) -- never a header value, never the presented token.

        M1 guard: parse_request sets request_version to "HTTP/0.9" before it
        parses the version word (and keeps it for a bare two-word `GET /`, which
        reaches do_GET), and send_response_only writes no status line and no
        header for HTTP/0.9 -- only the body would go out. Every refusal is
        therefore sent with an HTTP/1.1 head.
        """
        if getattr(self, "request_version", None) == "HTTP/0.9":
            self.request_version = "HTTP/1.1"
        head_only = getattr(self, "command", None) == "HEAD"
        body = b"" if head_only else _rt_dumps(_rt_error_body(err_type or _STATUS_TO_TYPE[status], message))
        self._rt_note(status, len(body))
        self.send_response(status)
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True
        if status in (401, 403):
            log.warning("http refused status=%d peer=%s", status, self.client_address[0])
        else:
            log.debug("http refused status=%d", status)

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        """The stdlib's own refusals (400/414/431/501/505), as Anthropic envelopes
        with a FIXED text: `message` and `explain` are ignored, since they can
        quote the request line or the method (B21). The HTTP/0.9 -> HTTP/1.1
        guard lives in _refuse, which covers these and every do_* refusal.
        nosniff and no-store are added here, as mcp-proxy's send_error does (D3).
        """
        self.close_connection = True
        self._refuse(code, _STATUS_TO_TYPE.get(code, "invalid_request_error"),
                     _RT_STDLIB_REFUSAL.get(code, "malformed request"),
                     extra_headers=_HTTP_STDLIB_REFUSAL_HEADERS)

    def _send_json(self, status: int, obj: Any, extra_headers=()) -> None:
        """One complete compact JSON answer, after the body was read. An
        unserialisable object becomes a 500 api_error envelope (type name logged)."""
        try:
            body = _rt_dumps(obj)
        except (TypeError, ValueError, RecursionError) as exc:
            log.warning("http answer was not JSON-serialisable: %s", type(exc).__name__)
            status = 500
            body = _rt_dumps(_rt_error_body("api_error", "internal error"))
        self._rt_note(status, len(body))
        self.send_response(status)
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _disconnected(self) -> None:
        """A write (or the body read) failed with OSError: the client left."""
        log.debug("client disconnected")
        self.close_connection = True

    def _rt_note(self, status: int, nbytes: int) -> None:
        """Record the answer's status and body size for the request's summary line."""
        rec = getattr(self, "_rt_rec", None)
        if rec is not None:
            rec["status"] = status
            rec["out"] += nbytes

    def _rt_log_summary(self, t0: float) -> None:
        """The one structure-only DEBUG `req` line of a POST (§9): every value
        through _log_value, never a header, a body byte or an upstream text.
        in/out are the client body bytes read/written; up_ms runs from the
        _rt_send call to the end of the upstream answer (0 when none was sent)."""
        if not log.isEnabledFor(logging.DEBUG):
            return
        rec = self._rt_rec
        now = time.monotonic()
        up_ms = 0
        if rec["up_t0"] is not None:
            up_ms = int(((rec["up_t1"] or now) - rec["up_t0"]) * 1000)
        extra = ""
        if rec["dropped_thinking"] is not None:
            extra = " dropped_thinking=%s" % _log_value(rec["dropped_thinking"])
        if rec["refreshed"]:
            # An upstream 401 forced one token refresh and a retry (KD-16).
            extra += " refreshed=%s" % _log_value(rec["refreshed"])
        if rec["sampling_dropped"]:
            # temperature / top_p not sent: a profile without sampling, or learned unsupported
            # for this backend + model (up front, or by the one retry after its 400).
            extra += " sampling_dropped=%s" % _log_value(",".join(rec["sampling_dropped"]))
        if rec["effort"] is not None:
            # The reasoning_effort sent when reasoning_effort_map or the learned supported set
            # (up front, or by the one retry after its 400) changed the computed one.
            extra += " effort=%s" % _log_value(rec["effort"])
        log.debug("req endpoint=%s route=%s kind=%s stream=%s status=%s up_status=%s in=%s out=%s "
                  "ms=%s up_ms=%s end=%s%s",
                  _log_value(rec["endpoint"]), _log_value(rec["route"]), _log_value(rec["kind"]),
                  _log_value(rec["stream"]), _log_value(rec["status"]), _log_value(rec["up_status"]),
                  _log_value(rec["in"]), _log_value(rec["out"]), _log_value(int((now - t0) * 1000)),
                  _log_value(up_ms), _log_value(rec["end"]), extra)

    def _precheck(self) -> Optional[str]:
        """The endpoint ("messages" | "count_tokens" | "models" | "model"), or
        None after refusing; for "model" the still-encoded id is _rt_model_id.

        Looks at the request line and the headers ONLY -- never the body, so an
        unauthenticated client cannot make the router read or wait for its
        declared body (B22). Order: 0 header defects, 1 the path without its
        query, 2 Host, 3 Origin, 4 bearer, 4b the misplaced token (path incl.
        query, and every header but the one Authorization), then the query: a
        token in the query is the misplaced-token 400, never a 404 (FR-9). On
        the two models paths the query is checked by key (_RT_MODELS_QUERY_KEYS,
        an unknown key a 400); on the messages paths by _RT_ALLOWED_QUERIES (404).
        """
        settings = self.server.settings
        if self.headers.defects:
            self._refuse(400, message="malformed header")
            return None
        raw_path = self.path or ""
        path, _sep, query = raw_path.partition("?")
        endpoint, self._rt_model_id = _rt_front_endpoint(path)
        if endpoint is None:
            self._refuse(404, message="not found")
            return None
        if self.server.loopback:
            hosts = self.headers.get_all("Host") or []
            port = self.server.server_port
            if len(hosts) != 1 or hosts[0].strip().lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self._refuse(403, message="Host not allowed")
                return None
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (origins and origins[0].strip() not in settings.origins):
            self._refuse(403, message="Origin not allowed")
            return None
        auths = self.headers.get_all("Authorization") or []
        presented = b""
        if len(auths) == 1 and auths[0].startswith("Bearer "):
            presented = auths[0][len("Bearer "):].strip().encode("ascii", "replace")
        if not hmac.compare_digest(presented, self.server.cfg.token):
            self._refuse(401, message="a valid bearer token is required",
                         extra_headers=[("WWW-Authenticate", "Bearer")])
            return None
        # 4b: the bearer already matched, so this need not be constant-time.
        tok = self.server.cfg.token.decode("ascii")
        misplaced = tok in raw_path or tok in urllib.parse.unquote(raw_path)
        if not misplaced:
            auth_skipped = False
            for name, value in self.headers.items():
                if not auth_skipped and name.lower() == "authorization":
                    auth_skipped = True
                    continue
                if tok in str(value):
                    misplaced = True
                    break
        if misplaced:
            self._refuse(400, message=_RT_MISPLACED_TOKEN)
            return None
        if endpoint in _RT_GET_ENDPOINTS:
            if not _rt_models_query_ok(query):
                self._refuse(400, message="query: only limit, before_id, after_id and beta are accepted")
                return None
        elif query not in _RT_ALLOWED_QUERIES:
            self._refuse(404, message="not found")
            return None
        self._rt_authed = True
        self.connection.settimeout(_HTTP_SOCKET_TIMEOUT_S)
        return endpoint

    def do_POST(self) -> None:  # noqa: N802 -- stdlib name
        t0 = time.monotonic()
        # Per request (a keep-alive connection reuses the handler): the summary fields.
        self._rt_rec = {"endpoint": "-", "route": "-", "kind": "-", "stream": 0, "status": 0,
                        "up_status": 0, "in": 0, "out": 0, "up_t0": None, "up_t1": None,
                        "end": "json", "dropped_thinking": None, "refreshed": 0,
                        "learn_retry": 0, "sampling_dropped": (), "effort": None}
        self._rt_head_sent = False
        try:
            self._post()
        except OSError:
            self._rt_rec["end"] = "disconnected"
            self._disconnected()
        finally:
            self._rt_log_summary(t0)

    def _post(self) -> None:
        """Checks, then the body: closing 529, Content-Type 415, framing 411/413,
        then any 100 Continue, an exact read under _HTTP_BODY_TIMEOUT_S, a JSON
        object with finite numbers (400) -- then the dispatch: inbound ->
        route -> backend -> adapter, and the ONE _rt_send call site (J4a)."""
        endpoint = self._precheck()
        if endpoint is None:
            return
        if endpoint in _RT_GET_ENDPOINTS:
            self._refuse(405, message="method not allowed", extra_headers=[("Allow", "GET")])
            return
        srv = self.server
        if srv.closing.is_set():
            self._refuse(529, message="router shutting down")
            return
        ok, ctype = self._single_header("Content-Type")
        if not ok or (ctype or "").split(";", 1)[0].strip().lower() != "application/json":
            self._refuse(415, message="Content-Type must be application/json")
            return
        # G-M1 DEFAULT: no chunked decoder until M1 shows a client sending it.
        if "Transfer-Encoding" in self.headers:
            self._refuse(411, message="Content-Length is required; Transfer-Encoding is not supported")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,19}", lengths[0].strip()):
            self._refuse(411, message="exactly one decimal Content-Length is required")
            return
        length = int(lengths[0].strip())
        if length > srv.settings.body_limit:
            self._refuse(413, message="request body too large")
            return
        # V37: the interim answer handle_expect_100 withheld, now that the
        # bearer and the framing passed (the stdlib's condition, minus its timing).
        if (self.headers.get("Expect") or "").strip().lower() == "100-continue" \
                and self.request_version >= "HTTP/1.1":
            self.send_response_only(100)
            self.end_headers()
            self.wfile.flush()
        # V4: the body read has a TOTAL deadline too, so a token holder that
        # trickles its body cannot keep the slot; a cut closes without an answer,
        # as a header-phase timeout does.
        reader = self._header_reader
        reader.deadline = time.monotonic() + _HTTP_BODY_TIMEOUT_S
        try:
            raw = self.rfile.read(length) if length else b""
        except socket.timeout:
            log.debug("http body deadline passed")
            self.close_connection = True
            return
        finally:
            reader.deadline = None
            self.connection.settimeout(_HTTP_SOCKET_TIMEOUT_S)
        if len(raw) != length:
            self.close_connection = True   # short body: the peer is gone or lying
            return
        try:
            body = _rt_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._refuse(400, message="request body is not valid JSON")
            return
        if not isinstance(body, dict):
            self._refuse(400, message="request body must be a JSON object")
            return
        rec = self._rt_rec
        rec["endpoint"] = endpoint
        rec["in"] = length
        inbound: Optional[InboundRequest] = None
        adapter: Optional[Adapter] = None
        sent: Any = None                 # the last upstream connection: untracked in finally (R-0095)
        try:
            inbound = _rt_parse_inbound(endpoint, body, self.headers, srv.cfg)
            backend = inbound.backend
            rec["route"], rec["kind"], rec["stream"] = inbound.route.name, backend.kind, int(inbound.stream)
            adapter = ADAPTERS.get(backend.kind)
            if adapter is None:
                # Unreachable: ADAPTERS and KIND_CLASSES derive from the same
                # _RT_KIND_TABLE rows. Kept as the guard for a kind that loads
                # without an adapter: it is answered 501 rather than half-served.
                self._send_json(501, _rt_error_body("api_error", "backend kind %s is not wired yet"
                                                    % backend.kind))
                return
            learns = endpoint == "messages" and adapter.learns_sampling(inbound)
            if learns:
                inbound = self._rt_sampling_inbound(inbound, srv.sampling_learned(backend.name,
                                                                                  inbound.route.model))
            if endpoint == "messages":
                supported = srv.effort_learned(backend.name, inbound.route.model)
                if supported:
                    inbound = inbound._replace(effort_supported=supported)
            if endpoint == "count_tokens":
                local = adapter.count_tokens(inbound)
                if isinstance(local, dict):
                    self._send_json(200, local)
                    return
                path, up_body = local
            else:
                path, up_body = adapter.upstream_request(inbound)
            # The credential, then the ONE send; an OAuth profile's first 401 closes the
            # answer unread, forces one refresh (stale=the rejected token) and retries
            # once. A second 401 reaches json_response: the KD-16 re-login 401. A learning
            # adapter's 400 "Unsupported parameter" naming a sampling name it sent -- read
            # before any client byte, on the stream path too -- is learned for (backend,
            # upstream model) and the request retried once without it; so is a 400 refusing
            # the computed reasoning_effort an adapter sent (effort_sent, mistral): the
            # supported set it lists is learned and the retry carries the remap. One learn
            # retry per request, whichever; any other 400 is answered from the body already read.
            tokens = srv.tokens
            cred = None
            refresh = False
            read: Optional[Tuple[int, str, bytes]] = None
            while True:
                if tokens is not None and tokens.handles(backend):
                    cred = tokens.credential(backend, stale=cred if refresh else None)
                rec["up_t0"] = time.monotonic()
                # R-0095: the socket is in the shutdown sweep from its connect on; every
                # path that closes conn below (or the finally) also untracks it.
                conn, resp = _rt_send(inbound.backend, path,
                                      _rt_upstream_headers(inbound, adapter.upstream_head(inbound, cred), cred),
                                      up_body, track=srv.track)
                sent = conn
                refresh = False
                if not rec["refreshed"] and cred is not None and resp.status == 401:
                    conn.untrack()
                    for closer in (resp.close, conn.close):   # the 401 body is discarded unread
                        try:
                            closer()
                        except OSError:
                            pass
                    rec["refreshed"] = 1
                    refresh = True
                    continue
                effort = adapter.effort_sent(inbound) if endpoint == "messages" else None
                if (learns or effort is not None) and not rec["learn_retry"] and resp.status == 400:
                    read = self._rt_read_upstream(conn, resp, inbound)
                    retry: Optional[InboundRequest] = None
                    # F45: a body over _RT_LEARN_BODY_LIMIT is never parsed for a learn; it is
                    # answered as any other 400 (the relay reads it under _UPSTREAM_BODY_LIMIT).
                    learnable = len(read[2]) <= _RT_LEARN_BODY_LIMIT
                    name = _rt_unsupported_sampling(read[2], adapter.sampling_sent(inbound)) \
                        if learns and learnable else None
                    if name is not None:
                        retry = self._rt_sampling_inbound(
                            inbound, srv.sampling_learn(backend.name, inbound.route.model, name))
                    elif learnable:
                        supported = _rt_ms_unsupported_effort(read[2], effort)
                        if supported is not None:
                            retry = inbound._replace(effort_supported=srv.effort_learn(
                                backend.name, inbound.route.model, supported))
                    if retry is not None:
                        pending = getattr(adapter, "_pending", None)
                        if isinstance(pending, dict):
                            pending.pop(id(inbound), None)   # the first translation's stream state
                        inbound = retry
                        path, up_body = adapter.upstream_request(inbound)
                        rec["learn_retry"] = 1
                        read = None
                        continue
                break
            if endpoint == "messages":
                rec["sampling_dropped"] = adapter.sampling_dropped(inbound)
                rec["effort"] = adapter.effort_remapped(inbound)
            if read is not None:
                self._answer_upstream_json(adapter, inbound, *read)
            elif inbound.stream and endpoint == "messages":
                self._relay_stream(conn, resp, adapter.stream_translator(inbound), inbound)
            else:
                self._relay_json(conn, resp, adapter, inbound)
        except (ApiError, UpstreamError) as exc:
            if self._rt_head_sent:       # never after the SSE head: _relay_stream answers in-band
                self.close_connection = True
                return
            if isinstance(exc, UpstreamError) and srv.closing.is_set():
                # R-0095: the shutdown sweep cut this upstream call (connect, head or body):
                # whatever the transport saw, the answer is the router's own 529.
                self.close_connection = True
                self._send_json(529, _rt_error_body("overloaded_error", _RT_SHUTTING_DOWN))
                return
            self._send_json(exc.status, _rt_error_body(exc.err_type, exc.message))
        except OSError:
            raise                        # a client write: do_POST's disconnect
        except Exception as exc:  # noqa: BLE001 -- the type name is logged, never a traceback (P7)
            log.warning("request failed: %s", type(exc).__name__)
            self.close_connection = True
            if not self._rt_head_sent:
                self._send_json(500, _rt_error_body("api_error", "internal error"))
        finally:
            # F27: an exit that never reached stream_translator -- a 400 answered from the
            # learn path's read, a failed send, a refusal after upstream_request -- leaves
            # this request's _pending entry; popped while the inbound is alive, so its id
            # cannot have been reused. A no-op once stream_translator took it.
            pending = getattr(adapter, "_pending", None)
            if inbound is not None and isinstance(pending, dict):
                pending.pop(id(inbound), None)
            if sent is not None:
                sent.untrack()           # idempotent: the relay paths untracked it already

    def _rt_sampling_inbound(self, inbound: InboundRequest, learned: FrozenSet[str]) -> InboundRequest:
        """*inbound* carrying *learned* as its sampling_drop; a route override that is dropped
        because the model refuses it is logged once per (backend, model, name), at DEBUG."""
        if learned == inbound.sampling_drop:
            return inbound
        route = inbound.route
        for name in sorted(learned):
            if name in route.options and self.server.sampling_note_once(inbound.backend.name, route.model, name):
                log.debug("sampling override dropped: route=%s param=%s (the upstream model refuses it)",
                          _log_value(route.name), _log_value(name))
        return inbound._replace(sampling_drop=learned)

    def _relay_json(self, conn: Any, resp: Any, adapter: Adapter, inbound: InboundRequest) -> None:
        """A non-stream upstream answer (or a stream request's non-2xx, KD-15):
        the whole body under _UPSTREAM_BODY_LIMIT, mapped by adapter.json_response,
        answered with _send_json. A 429's Retry-After is relayed when it is all
        digits. The handler owns conn and resp here (no pump): closed in finally."""
        status, retry_after, up_body = self._rt_read_upstream(conn, resp, inbound)
        self._answer_upstream_json(adapter, inbound, status, retry_after, up_body)

    def _rt_read_upstream(self, conn: Any, resp: Any, inbound: InboundRequest) -> Tuple[int, str, bytes]:
        """(status, Retry-After, the whole body under _UPSTREAM_BODY_LIMIT) of one upstream
        answer; conn and resp are closed in finally."""
        rec = self._rt_rec
        rec["up_status"] = resp.status
        try:
            retry_after = (resp.getheader("Retry-After") or "").strip()
            up_body = _rt_read_body(resp, _UPSTREAM_BODY_LIMIT, inbound.backend.name)
        finally:
            conn.untrack()               # R-0095: out of the shutdown sweep before the close
            for closer in (resp.close, conn.close):
                try:
                    closer()
                except OSError:
                    pass
        rec["up_t1"] = time.monotonic()
        return resp.status, retry_after, up_body

    def _answer_upstream_json(self, adapter: Adapter, inbound: InboundRequest, up_status: int,
                              retry_after: str, up_body: bytes) -> None:
        """An upstream answer already read: mapped by adapter.json_response, answered with
        _send_json; a 429's Retry-After relayed when it is all digits."""
        rec = self._rt_rec
        status, obj = adapter.json_response(inbound, up_status, up_body)
        extra = ()
        if status == 429 and re.fullmatch(r"[0-9]{1,10}", retry_after):
            extra = (("retry-after", retry_after),)
        rec["end"] = "json"
        self._send_json(status, obj, extra)

    def _relay_error_json(self, conn: Any, resp: Any, adapter: Adapter, inbound: InboundRequest) -> None:
        """A non-2xx upstream answer to a STREAM request: a JSON envelope via
        adapter.json_response (KD-14 status, scrubbed text), never a 200 SSE
        head (KD-15) -- the upstream status is known before the router's head."""
        self._relay_json(conn, resp, adapter, inbound)

    def _relay_stream(self, conn: Any, resp: Any, translator: Any, inbound: InboundRequest) -> None:
        """Relay one upstream SSE stream through `translator` (KD-4, KD-15).

        Status and Content-Type first; then the SSE head, after which every
        failure is ONE `event: error` from the translator and a close, never a
        message_stop (KD-7). The pump is the only reader of resp and does the
        final close; this thread is the only writer. Returns only after the
        pump was joined (or idle_timeout passed), so the connection slot
        covers the pump (S1, H7).
        """
        backend = inbound.backend
        srv = self.server
        rec = self._rt_rec
        rec["up_status"] = resp.status
        if not 200 <= resp.status < 300:
            self._relay_error_json(conn, resp, ADAPTERS[backend.kind], inbound)
            return
        policy = ADAPTERS[backend.kind].stream_policy(inbound)
        ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "text/event-stream" and (policy.require_event_stream or ctype != ""):
            log.debug("upstream not an event stream: content_type=%s", _log_value(ctype))
            for closer in (resp.close, conn.close):
                try:
                    closer()
                except OSError:
                    pass
            rec["up_t1"] = time.monotonic()
            raise UpstreamError(502, "api_error", "backend %s did not answer with an event stream"
                                % backend.name)
        stop = threading.Event()
        q = _RtByteQueue(_RT_QUEUE_BYTES)
        pump = threading.Thread(target=_rt_pump, args=(conn, resp, q, stop, policy.line_limit),
                                name="rt-pump", daemon=True)
        raw_sock = getattr(conn, "raw_sock", None)
        last_write = 0.0

        def write(chunk: bytes) -> None:
            nonlocal last_write
            if chunk:
                self.wfile.write(chunk)
                self.wfile.flush()
                last_write = time.monotonic()
                rec["out"] += len(chunk)

        end = "error"
        try:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self._rt_head_sent = True
            rec["status"] = 200
            # L8: the head is the first client write; the ping timer, the
            # client-EOF poll and the idle check all start here.
            last_write = last_line = time.monotonic()
            if raw_sock is not None:
                with srv.inflight_lock:
                    srv.inflight.add(raw_sock)
            write(translator.begin())
            pump.start()
            end = self._relay_loop(q, translator, backend, write, lambda: last_write, last_line,
                                   policy.line_limit)
        except OSError:
            end = "disconnected"
        except Exception as exc:  # noqa: BLE001 -- type name only; one event: error in-band
            log.warning("stream relay failed: %s", type(exc).__name__)
            end = "error"
            if self._rt_head_sent:
                try:
                    try:
                        chunk = translator.fail("api_error", "internal error")
                    except Exception:  # noqa: BLE001 -- a broken translator still closes the envelope
                        chunk = AnthropicSseEncoder("", "").error("api_error", "internal error")
                    write(chunk)
                except OSError:
                    end = "disconnected"
            else:
                raise
        finally:
            stop.set()
            if raw_sock is not None:
                try:
                    socket.socket.shutdown(raw_sock, socket.SHUT_RDWR)   # base class, also on an SSLSocket
                except OSError:
                    pass
                with srv.inflight_lock:
                    srv.inflight.discard(raw_sock)
            if pump.ident is not None:     # started: the pump owns resp and conn
                deadline = time.monotonic() + backend.idle_timeout
                while pump.is_alive() and time.monotonic() < deadline:
                    pump.join(_PUMP_JOIN_S)
                if pump.is_alive():
                    log.warning("pump did not exit: backend %s", _log_value(backend.name))
            else:
                # The pump never started: the handler still owns resp and conn.
                for closer in (resp.close, conn.close):
                    try:
                        closer()
                    except OSError:
                        pass
            rec["up_t1"] = rec["up_t1"] or time.monotonic()
            rec["end"] = end
            rec["dropped_thinking"] = getattr(translator, "dropped_thinking", None)
            if end == "disconnected":
                self._disconnected()

    def _relay_loop(self, q: _RtByteQueue, translator: Any, backend: BackendSpec,
                    write: Callable[[bytes], None], last_write: Callable[[], float],
                    last_line: float, line_limit: int = _SSE_LINE_LIMIT) -> str:
        """The relay ticks of _relay_stream -> how the stream ended (§9 `end`).

        `line_limit` is the pump's cap (StreamPolicy.line_limit), named in the
        over-long-line message.

        Every pass (an item or a _RT_TICK_S timeout), in order: shutdown,
        the upstream line clock, the idle check, the item, the ping, the
        client-EOF poll. A write's OSError propagates (a disconnect, P15).
        """
        srv = self.server
        rec = self._rt_rec
        while True:
            try:
                item = q.get(_RT_TICK_S)
            except queue.Empty:
                item = None
            now = time.monotonic()
            if srv.closing.is_set():
                write(translator.fail("overloaded_error", "router shutting down"))
                return "shutdown"
            if isinstance(item, bytes):
                # A pass that just got an upstream line has seen no silence:
                # reset BEFORE the idle check (a slow client write on the
                # previous pass must not read as a stalled backend).
                last_line = now
            if now - last_line > backend.idle_timeout:
                log.debug("backend stalled: %s", _log_value(backend.name))
                write(translator.fail("api_error", "backend %s stalled" % backend.name))
                return "error"
            if item is not None:
                if isinstance(item, bytes):
                    chunk = translator.feed(item)
                elif item is _EOF:
                    chunk = translator.finish()
                elif item is _LINE_TOO_LONG:
                    chunk = translator.fail("api_error", "backend %s sent an SSE line over %d bytes"
                                            % (backend.name, line_limit))
                elif isinstance(item, tuple) and item[1] in ("timeout", "TimeoutError"):
                    # The upstream socket's own idle_timeout fired first (3.9 names it "timeout").
                    log.debug("backend stalled: %s", _log_value(backend.name))
                    chunk = translator.fail("api_error", "backend %s stalled" % backend.name)
                else:
                    # ("exc", type name): a truncated upstream is never a clean end (KD-7, D12).
                    chunk = translator.fail("api_error", "backend %s: the stream was cut (%s)"
                                            % (backend.name, _rt_one_line(item[1])))
                write(chunk)
                if translator.closed or not isinstance(item, bytes):
                    rec["up_t1"] = time.monotonic()
                    return "message_stop" if b"event: message_stop" in chunk else "error"
            now = time.monotonic()
            if getattr(translator, "started", False) and now - last_write() >= _PING_INTERVAL_S:
                write(translator.ping())
            if now - last_write() >= _RT_TICK_S and self._rt_client_gone():
                return "disconnected"

    def _rt_client_gone(self) -> bool:
        """KD-4 client-EOF poll while nothing is written: readable + a peeked
        b"" (or an OSError) means the client left (H9). Never consumes a byte."""
        try:
            readable, _w, _x = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _refuse_405(self) -> None:
        """Every method a path does not take: the full precheck, then 405 with
        Allow: GET on the models paths, Allow: POST on the messages paths.
        OPTIONS and HEAD too -- there is no CORS preflight answer."""
        try:
            endpoint = self._precheck()
            if endpoint:
                allow = "GET" if endpoint in _RT_GET_ENDPOINTS else "POST"
                self._refuse(405, message="method not allowed", extra_headers=[("Allow", allow)])
        except OSError:
            self._disconnected()

    def _get_models(self, endpoint: str) -> None:
        """GET /v1/models and /v1/models/{id}, answered from the config alone --
        no body is read and no backend is ever contacted. A Transfer-Encoding or
        a malformed Content-Length is the POST framing 411; a body (Content-Length
        above 0) is a 400. An id that is not exactly a route key after
        percent-decoding (UTF-8, strict) is a 404 that never echoes it. A
        two-word HTTP/0.9 `GET` is a 400: _send_json would write no head for it
        (the M1 guard of _refuse)."""
        if self.request_version == "HTTP/0.9":
            self._refuse(400, message="HTTP/0.9 is not supported")
            return
        if "Transfer-Encoding" in self.headers:
            self._refuse(411, message="Content-Length is required; Transfer-Encoding is not supported")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if lengths:
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,19}", lengths[0].strip()):
                self._refuse(411, message="exactly one decimal Content-Length is required")
                return
            if int(lengths[0].strip()):
                self._refuse(400, message="a GET request must not carry a body")
                return
        cfg = self.server.cfg
        if endpoint == "models":
            self._send_json(200, _rt_models_list(cfg, self.server.started_at))
            return
        try:
            model_id = urllib.parse.unquote(self._rt_model_id or "", errors="strict")
        except UnicodeDecodeError:
            model_id = ""
        route = cfg.routes.get(model_id)
        if route is None:
            self._send_json(404, _rt_error_body("not_found_error", "model not found"))
            return
        self._send_json(200, _rt_model_entry(route, self.server.started_at))

    def do_GET(self) -> None:  # noqa: N802 -- stdlib name
        # No `req` summary line: only a POST gets one (deviation 8).
        self._rt_rec = None
        try:
            endpoint = self._precheck()
            if not endpoint:
                return
            if endpoint not in _RT_GET_ENDPOINTS:
                self._refuse(405, message="method not allowed", extra_headers=[("Allow", "POST")])
                return
            self._get_models(endpoint)
        except OSError:
            self._disconnected()

    def do_PUT(self) -> None:  # noqa: N802 -- stdlib name
        self._refuse_405()

    def do_PATCH(self) -> None:  # noqa: N802 -- stdlib name
        self._refuse_405()

    def do_DELETE(self) -> None:  # noqa: N802 -- stdlib name
        self._refuse_405()

    def do_OPTIONS(self) -> None:  # noqa: N802 -- stdlib name
        self._refuse_405()

    def do_HEAD(self) -> None:  # noqa: N802 -- stdlib name
        self._refuse_405()


# ---------------------------------------------------------------------------
# Unit 8: entry point
# ---------------------------------------------------------------------------

_RT_PORT_RANGE = (0, 65535)
# An exact browser Origin: scheme, host (a name, an IPv4 literal or a bracketed
# IPv6 literal) and an optional port -- no userinfo, path, query or fragment.
_RT_ORIGIN_RE = re.compile(r"(https?)://([A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::([0-9]{1,5}))?")


def _rt_refuse(reason: str) -> NoReturn:
    """The one refusal shape: one `llm-router: <reason>` stderr line, exit 2."""
    print("llm-router: " + " ".join(str(reason).splitlines()), file=sys.stderr)
    sys.exit(2)


class _RtArgumentParser(argparse.ArgumentParser):
    """argparse whose own errors are the one-line refusal: no usage block, exit 2."""

    def error(self, message: str) -> NoReturn:
        _rt_refuse(message)


def _rt_cli_int(value: str) -> int:
    """An argparse type: a decimal integer, refused without echoing the value."""
    try:
        return int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a decimal integer") from None


def _rt_cli_range(flag: str, value: int, bounds: Tuple[int, int]) -> None:
    lo, hi = bounds
    if not lo <= value <= hi:
        raise ConfigError(f"{flag}: must be an integer in [{lo}, {hi}]")


def _rt_cli_origin(value: str) -> str:
    """One --allowed-origin value, exact scheme://host[:port]; refused without echoing it."""
    m = _RT_ORIGIN_RE.fullmatch(value)
    if m is None:
        raise ConfigError("--allowed-origin: must be an exact scheme://host[:port] "
                          "(http or https, no path, query or trailing slash)")
    port = m.group(3)
    if port is not None and not 1 <= int(port, 10) <= 65535:
        raise ConfigError("--allowed-origin: the port must be in [1, 65535]")
    return value


def _rt_settings(args: argparse.Namespace) -> RouterSettings:
    """Validate the network flags into RouterSettings (refuse-to-start rules, P6).

    Bind is an IPv4 literal only; a non-loopback bind needs --allow-remote, and
    even then never 0.0.0.0 or a global address (V2).
    Every refusal is a ConfigError naming the flag and the rule.
    """
    try:
        addr = ipaddress.IPv4Address(args.bind)
    except ValueError:
        raise ConfigError("--bind takes an IPv4 literal (e.g. 127.0.0.1); "
                          "IPv6 and host names are not supported") from None
    if not addr.is_loopback and not args.allow_remote:
        raise ConfigError("--bind: a non-loopback address needs --allow-remote to listen on it "
                          "(localhost or a VPN interface only, never a public one)")
    # V2: "never a public one" is enforced, not only stated. 100.64.0.0/10 (the
    # carrier-grade NAT range VPNs such as Tailscale use) is not global, so it stays.
    if args.allow_remote and (addr.is_unspecified or addr.is_global):
        raise ConfigError("--bind: --allow-remote refuses an unspecified (0.0.0.0) or public "
                          "address; bind the VPN interface's own address")
    _rt_cli_range("--port", args.port, _RT_PORT_RANGE)
    _rt_cli_range("--body-limit", args.body_limit, _BODY_LIMIT_RANGE)
    _rt_cli_range("--max-connections", args.max_connections, _CONNECTION_CAP_RANGE)
    origins = frozenset(_rt_cli_origin(value) for value in (args.allowed_origin or ()))
    if args.ready_file is not None and not args.ready_file:
        raise ConfigError("--ready-file: must not be empty")
    return RouterSettings(
        bind=str(addr),
        port=args.port,
        allow_remote=bool(args.allow_remote),
        origins=origins,
        body_limit=args.body_limit,
        max_connections=args.max_connections,
        ready_file=args.ready_file,
        debug=bool(args.debug),
        log_file=args.log_file,
    )


# The --help epilogs: an operator cheat sheet, kept true to load_config and _precheck.
_RT_HELP_EPILOG = """\
example config (chmod 600; token: python3 -c 'import secrets;print(secrets.token_urlsafe(32))'):
  {
    "auth_token": "<the token above>",
    "backends": {
      "infernox": {"kind": "passthrough", "base_url": "https://infernox.lan", "allow_private": true,
                   "api_key": "<16+ chars>", "auth_header": "x-api-key"},
      "llama":    {"kind": "llamacpp", "base_url": "http://127.0.0.1:8080",
                   "allow_private": true, "allow_loopback": true},
      "mistral":  {"kind": "mistral", "base_url": "https://api.mistral.ai", "api_key": "<key>"},
      "codex":    {"kind": "codex", "oauth": {}},
      "openai":   {"kind": "openai", "api_key": "<sk-...>"},
      "openai-siwc": {"kind": "openai", "oauth": {}}
    },
    "routes": {
      "claude-opus-4-5":   {"backend": "codex", "model": "gpt-5-codex"},
      "claude-opus-4-1":   {"backend": "openai", "model": "gpt-5"},
      "claude-sonnet-4-5": {"backend": "mistral", "model": "mistral-large-latest",
                            "max_input_tokens": 262144},
      "claude-haiku-4-5":  {"backend": "llama", "model": "qwen3-coder"}
    },
    "default": {"backend": "infernox", "model": "local-model"}
  }
  routes are keyed by the exact model string Claude Code sends; "default" takes the rest.
  A route's optional max_input_tokens / max_tokens (integers 1-100000000) are only
  advertised by GET /v1/models (context_window too, = max_input_tokens), never enforced.
  Route options temperature (0-2) / top_p (0-1) override the client's; one a backend cannot take
  is dropped (codex, openai oauth: always; an openai api_key model answering 400 "Unsupported
  parameter": learned per backend and model, the request retried once without it).
  A mistral route's reasoning_effort option fixes the effort; else the thinking budget's band
  goes through its reasoning_effort_map (e.g. {"low": "high", "xhigh": "high"}), and a model
  answering 400 "reasoning_effort X is not supported ... supported values: [...]" has those
  values learned per backend and model, the request retried once with the nearest one.
  A codex backend needs `llm-router.py login` first (see `llm-router.py login --help`).
  An openai backend takes an api_key (base_url defaults to https://api.openai.com) or an
  "oauth" object (Sign in with ChatGPT), which needs `llm-router.py login` first too.

run:
  llm-router.py --config ~/mcp/llm-router.conf --port 8082 --debug

point Claude Code at it:
  unset ANTHROPIC_API_KEY        # the token in any header but Authorization is refused
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8082    # 127.0.0.1, not localhost: IPv4 only
  read -rs ANTHROPIC_AUTH_TOKEN && export ANTHROPIC_AUTH_TOKEN   # paste <auth_token>; sent as
                                                     # Authorization: Bearer, never in shell history
  export ANTHROPIC_MODEL=claude-sonnet-4-5           # a route key
  export ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-5 ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-4-5
  export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5 CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-4-5
  # without the last two lines background requests and subagents fall to "default";
  # /model only lasts the session.

smoke test (lists the route keys; the header goes in on stdin, so the token is not on curl's argv):
  printf 'Authorization: Bearer %s\\n' "$ANTHROPIC_AUTH_TOKEN" | curl -s -H @- http://127.0.0.1:8082/v1/models
"""


def _rt_parser() -> argparse.ArgumentParser:
    parser = _RtArgumentParser(
        prog="llm-router",
        description="Anthropic Messages API router for Claude Code "
                    "(localhost or VPN only, never a public interface).",
        epilog=_RT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # No --config-json: argv is visible in ps and every config carries a secret (FR-11).
    parser.add_argument("--config", required=True,
                        help="Path to the JSON config (must be owned by you, mode 0600)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="IPv4 literal to listen on (default 127.0.0.1); non-loopback needs --allow-remote")
    parser.add_argument("--port", type=_rt_cli_int, default=0, help="TCP port (default 0 = ephemeral)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Allow a non-loopback --bind (a VPN interface; never a public one)")
    parser.add_argument("--allowed-origin", action="append", default=None,
                        help="Allowed Origin header, exact scheme://host[:port] (repeatable)")
    parser.add_argument("--ready-file", default=None,
                        help="Written 0600 after bind: {\"port\": N, \"pid\": P}; removed on shutdown "
                             "while it still holds this pid")
    parser.add_argument("--body-limit", type=_rt_cli_int, default=_ROUTER_BODY_LIMIT,
                        help=f"Largest accepted request body in bytes, {_BODY_LIMIT_RANGE[0]}-"
                             f"{_BODY_LIMIT_RANGE[1]} (default {_ROUTER_BODY_LIMIT})")
    parser.add_argument("--max-connections", type=_rt_cli_int, default=_HTTP_CONNECTION_CAP,
                        help=f"Concurrent TCP connections, {_CONNECTION_CAP_RANGE[0]}-"
                             f"{_CONNECTION_CAP_RANGE[1]} (default {_HTTP_CONNECTION_CAP})")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", default=None, help="Log to file, mode 0600 (implies --debug)")
    return parser


# --- The login subcommand (Plan Step 12; KD-15, KD-17, S7) ---
# `llm-router.py login` runs the OAuth login of one backend and persists its refresh
# token through the token store's writer (KD-10, KD-20). Nothing is printed beyond the
# authorize URL (or the device user code) and the final line; every login secret
# (code_verifier, authorization code, device_auth_id, the returned tokens) is pinned in
# cfg.scrub under "login" the moment it is known (S7). state and the nonce are NOT
# pinned: they travel in the printed URL by design (L4).

_RT_LOGIN_TIMEOUT_S = 300                 # default --timeout of a login, in seconds
_RT_LOGIN_TIMEOUT_RANGE = (5, 1800)       # accepted --timeout values
_RT_LOGIN_CALLBACK = "/auth/callback"     # the redirect path both provider rows use
_RT_LOGIN_HOSTS = ("127.0.0.1", "::1")    # KD-17: 127.0.0.1 mandatory, [::1] best effort
_RT_LOGIN_PASTE_LIMIT = 64 * 1024         # one pasted stdin line; a longer one is dropped whole
_RT_LOGIN_READ_BYTES = 4096               # one os.read of the paste thread
_RT_LOGIN_PIN = "login"                   # the scrubber pin key of every login secret

_RT_LOGIN_HELP_EPILOG = f"""\
codex browser login (listens on 127.0.0.1:1455 for the redirect to localhost:1455/auth/callback):
  llm-router.py login --config ~/mcp/llm-router.conf --backend codex [--open-browser]
  the authorize URL is printed; if the redirect cannot reach the listener, paste the
  redirect URL from the browser's address bar on stdin.

codex device flow (no listener; open the printed URL and enter the printed code):
  llm-router.py login --config ~/mcp/llm-router.conf --backend codex --device

openai Sign in with ChatGPT (a backend {{"kind": "openai", "oauth": {{}}}}; browser only, no --device):
  llm-router.py login --config ~/mcp/llm-router.conf --backend openai-siwc [--open-browser]
  listens on an ephemeral 127.0.0.1 port for the redirect to 127.0.0.1:<port>/auth/callback;
  the first login registers the router and keeps the issued client_id and host_id.

--timeout: {_RT_LOGIN_TIMEOUT_RANGE[0]}-{_RT_LOGIN_TIMEOUT_RANGE[1]} seconds (default {_RT_LOGIN_TIMEOUT_S}).
Only backends.<name>.oauth refresh_token / account_id / expires_at / client_id / host_id are
written back to the config, atomically, and the file stays mode 0600.
"""


def _rt_login_parser() -> argparse.ArgumentParser:
    parser = _RtArgumentParser(
        prog="llm-router login",
        description="Log an OAuth backend (kind codex, or openai with an oauth object) in and store "
                    "its refresh token in the config.",
        epilog=_RT_LOGIN_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True,
                        help="Path to the JSON config (must be owned by you, mode 0600)")
    parser.add_argument("--backend", required=True, help="The backend to log in (an OAuth profile)")
    parser.add_argument("--device", action="store_true",
                        help="codex only: the device-code flow instead of the browser redirect")
    parser.add_argument("--open-browser", action="store_true", help="Open the authorize URL in a browser")
    parser.add_argument("--timeout", type=_rt_cli_int, default=_RT_LOGIN_TIMEOUT_S,
                        help=f"Seconds to wait for the login, {_RT_LOGIN_TIMEOUT_RANGE[0]}-"
                             f"{_RT_LOGIN_TIMEOUT_RANGE[1]} (default {_RT_LOGIN_TIMEOUT_S})")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", default=None, help="Log to file, mode 0600 (implies --debug)")
    return parser


def _rt_login_pin(cfg: RouterConfig, pinned: List[bytes], *values: str) -> None:
    """Add *values* to the login's secrets and re-pin ALL of them (pin replaces the key's set)."""
    pinned.extend(value.encode("ascii") for value in values if value)
    _rt_scrub_pin(cfg.scrub, _RT_LOGIN_PIN, tuple(pinned))


def _rt_login_paste_reader(fd: int, state: str, found: "queue.Queue[Any]", event: threading.Event) -> None:
    """The paste thread: the first stdin line that is the redirect URL ends the wait.

    Reads the raw descriptor (os.read, never sys.stdin's buffer, so a daemon thread
    blocked here holds no interpreter-level lock at exit). A line whose target passes
    _oauth_parse_callback is put on *found* and *event* set; a matching-state `error=`
    (OAuthError denied) is put there the same way (R3-L4); any other OAuthError -- a
    wrong state, a stray line -- is skipped. EOF or a read error ends the thread
    silently and never touches the listener.
    """
    def judge(raw: bytes) -> bool:
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            return False
        if not text:
            return False
        try:
            parsed = _oauth_parse_callback(text, _RT_LOGIN_CALLBACK, state)
        except OAuthError as exc:
            if exc.kind != "denied":
                return False
            found.put(exc)
            event.set()
            return True
        found.put(parsed)
        event.set()
        return True

    pending = b""
    dropping = False
    while True:
        try:
            chunk = os.read(fd, _RT_LOGIN_READ_BYTES)
        except OSError:
            return
        if not chunk:
            if pending and not dropping:
                judge(pending)
            return
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if dropping:
                dropping = False
                continue
            if judge(line):
                return
        if len(pending) > _RT_LOGIN_PASTE_LIMIT:
            pending = b""
            dropping = True


def _rt_login_exchange(cfg: RouterConfig, backend: BackendSpec, provider: OAuthProvider, row: ResponsesProfile,
                       user_agent: str, code: str, verifier: str, redirect: str,
                       pinned: List[bytes], client_id: str = "", nonce: Optional[str] = None) -> Dict[str, Any]:
    """Trade *code* + *verifier* at the token endpoint -> the backends.<n>.oauth object to persist.

    *client_id* is the id the code was issued to (openai SIWC: the issued oaiapp_ id;
    "" = the row's static one); the row's resource is sent when it has one. Parsed with
    require_refresh=True; every returned token passes the token rule and is pinned
    before anything else (S5, S7). On a row with an issuer the ID token (iss, aud =
    *client_id*, exp, *nonce*) and the granted scope are checked (FR-6). The account
    claims come from THIS response's access token only, each passing the claim rule
    (S5, I3). The access and ID tokens are never returned, so they are never written.
    """
    client_id = client_id or provider.client_id
    request = _rt_oauth_exchange_request(provider, client_id, code, verifier, redirect)
    status, body = _rt_oauth_post(backend, request, user_agent)
    now = int(time.time())
    got = _oauth_parse_token_response(status, body, now, None, require_refresh=True)
    tokens = tuple(t for t in (got["access_token"], got["refresh_token"], got["id_token"]) if t)
    if not all(_rt_header_value_ok(t, "token") for t in tokens):
        raise OAuthError("invalid_response")
    _rt_login_pin(cfg, pinned, *tokens)
    _rt_oauth_check_answer(_rt_oauth_row_for(backend, provider), got, client_id, nonce, now)
    oauth: Dict[str, Any] = {"refresh_token": got["refresh_token"]}
    if row.account_header:
        try:
            claims = _oauth_jwt_claims(got["access_token"])
        except OAuthError:
            claims = None
        account, _residency = _oauth_account_claims(claims)
        if account is None or not _rt_header_value_ok(account, "claim"):
            raise OAuthError("invalid_response", "account_claim")
        oauth["account_id"] = account
    if got["expires_at"] is not None:
        oauth["expires_at"] = got["expires_at"]
    return oauth


def _rt_login_browser(args: argparse.Namespace, cfg: RouterConfig, backend: BackendSpec, provider: OAuthProvider,
                      row: ResponsesProfile, user_agent: str, pinned: List[bytes]) -> Dict[str, Any]:
    """The browser flow: loopback listener, authorize URL, callback or paste, exchange (KD-17).

    A row with a fixed redirect_uri (codex) listens on its port on 127.0.0.1 and [::1].
    A row without one (openai SIWC, Plan Step 16) listens on an ephemeral 127.0.0.1
    port and redirects to http://127.0.0.1:<port>/auth/callback; its authorize URL
    carries the seed's issued client id or the row's registration id, a nonce,
    agent_name_hint and ext_agent_host_id (the seed's host id or a fresh urn:uuid v4).
    A first registration's callback must carry an issued oaiapp_ client_id, which the
    exchange then uses and the config keeps with the host id; else nothing is written.
    """
    dynamic = not provider.redirect_uri
    seed = backend.oauth
    nonce: Optional[str] = None
    if dynamic:
        seed_client = seed.client_id if seed is not None else None
        client_id = seed_client or provider.registration_client_id
        host_id = (seed.host_id if seed is not None else None) or _oauth_uuid4_urn()
        hosts, port = ("127.0.0.1",), 0
    else:
        seed_client, client_id, host_id = None, provider.client_id, ""
        hosts, port = _RT_LOGIN_HOSTS, urllib.parse.urlsplit(provider.redirect_uri).port or 0
    try:
        listeners = _oauth_listen(hosts, port)
    except OSError as exc:
        import errno     # function-local: only this refusal names the errno
        reason = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
        hint = "" if dynamic else " or use --device"
        _rt_refuse(f"cannot listen on 127.0.0.1:{port} ({reason}); stop the other login{hint}")
    if dynamic:
        port = listeners[0].getsockname()[1]
        redirect = "http://127.0.0.1:%d%s" % (port, _RT_LOGIN_CALLBACK)
        allowed_hosts = frozenset(("127.0.0.1:%d" % port,))
    else:
        redirect = provider.redirect_uri
        allowed_hosts = frozenset(("localhost:%d" % port, "127.0.0.1:%d" % port, "[::1]:%d" % port))
    try:
        verifier, challenge = _oauth_pkce_pair()
        state = _oauth_new_state()
        _rt_login_pin(cfg, pinned, verifier)          # before anything is printed (S7)
        url_extra: Tuple[Tuple[str, str], ...] = ()
        if dynamic:
            nonce = _oauth_new_state()                # travels in the URL by design, not pinned (L4)
            url_extra = (("agent_name_hint", "llm-router"), ("ext_agent_host_id", host_id))
        if row.identity:
            # KD-2: the backend's effective originator replaces the row's default.
            originator = dict(_rt_rs_identity(backend)).get("originator")
            extra = tuple((k, originator if k == "originator" and originator else v)
                          for k, v in provider.authorize_extra)
            if extra != provider.authorize_extra:
                fields = {name: getattr(provider, name) for name in OAuthProvider.__slots__}
                fields["authorize_extra"] = extra
                provider = OAuthProvider(**fields)
        url = _oauth_authorize_url(provider, client_id, redirect, challenge, state, nonce, url_extra)
        print("llm-router: open this URL to log in (or paste the redirect URL here): " + url,
              file=sys.stderr, flush=True)
        if args.open_browser:
            import webbrowser    # L8: function-local, so the server path never imports it
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 -- the printed URL is the fallback
                pass
        found: "queue.Queue[Any]" = queue.Queue()
        event = threading.Event()
        try:
            fd = sys.stdin.fileno() if sys.stdin is not None else -1
        except (OSError, ValueError, AttributeError):
            fd = -1
        if fd >= 0:
            threading.Thread(target=_rt_login_paste_reader, args=(fd, state, found, event),
                             name="rt-login-paste", daemon=True).start()
        parsed = _oauth_sync_accept_callback(listeners, _RT_LOGIN_CALLBACK, state, allowed_hosts,
                                             time.monotonic() + args.timeout, time.monotonic, event)
    finally:
        for listener in listeners:
            listener.close()
    if parsed is None:
        parsed = found.get_nowait()
        if isinstance(parsed, OAuthError):
            raise parsed
    code = parsed["code"]
    _rt_login_pin(cfg, pinned, code)                  # the moment it is parsed (S7)
    if not dynamic:
        return _rt_login_exchange(cfg, backend, provider, row, user_agent, code, verifier, redirect, pinned)
    # I3: the issued client_id is a client identifier the exchange binds, not an identity claim.
    issued = parsed.get("client_id") or seed_client
    if issued is None:
        _rt_refuse("login failed: incomplete registration (no issued client_id)")
    if not _RT_OAUTH_CLIENT_ID_RE.fullmatch(issued):
        raise OAuthError("invalid_callback", "client_id")
    oauth = _rt_login_exchange(cfg, backend, provider, row, user_agent, code, verifier, redirect, pinned,
                               client_id=issued, nonce=nonce)
    oauth["client_id"] = issued
    oauth["host_id"] = host_id
    return oauth


def _rt_login_device(args: argparse.Namespace, cfg: RouterConfig, backend: BackendSpec, provider: OAuthProvider,
                     row: ResponsesProfile, user_agent: str, pinned: List[bytes]) -> Dict[str, Any]:
    """The codex device flow: user code, polls at the endpoint's interval, exchange (S7)."""
    deadline = time.monotonic() + args.timeout
    status, body = _rt_oauth_post(backend, _oauth_device_start_request(provider, provider.client_id), user_agent)
    start = _oauth_parse_device_start(status, body)
    _rt_login_pin(cfg, pinned, start["device_auth_id"])     # immediately (S7); user_code is shown by design
    print("llm-router: open %s and enter the code %s" % (provider.device_verify_url, start["user_code"]),
          file=sys.stderr, flush=True)
    # F52: whoever started a device flow receives the grant its code approves -- a code
    # someone else sent you is a phishing attempt.
    print("llm-router: only enter this code if you just started this login yourself; "
          "if someone sent it to you, do not enter it", file=sys.stderr, flush=True)
    poll = _oauth_device_poll_request(provider, start["device_auth_id"], start["user_code"])
    while True:
        if time.monotonic() + start["interval"] > deadline:
            raise OAuthError("callback_timeout")
        time.sleep(start["interval"])
        status, body = _rt_oauth_post(backend, poll, user_agent)
        grant = _oauth_parse_device_poll(status, body)
        if grant is not None:
            break
    _rt_login_pin(cfg, pinned, grant["authorization_code"], grant["code_verifier"])   # before anything else
    return _rt_login_exchange(cfg, backend, provider, row, user_agent, grant["authorization_code"],
                              grant["code_verifier"], provider.device_redirect_uri, pinned)


def _rt_login(args: argparse.Namespace) -> int:
    """`llm-router.py login`: 0 after the refresh token is persisted; every refusal exits 2."""
    try:
        _configure_logging(args.debug, args.log_file)
    except OSError as exc:
        _rt_refuse(f"--log-file: cannot open it for append ({type(exc).__name__})")
    try:
        _rt_cli_range("--timeout", args.timeout, _RT_LOGIN_TIMEOUT_RANGE)
        cfg = load_config(args.config)
    except ConfigError as exc:
        _rt_refuse(str(exc))
    backend = cfg.backends.get(args.backend)
    row = _RT_RESPONSES_PROFILES.get(backend.profile) if backend is not None and backend.profile else None
    if backend is None or row is None or not row.oauth_provider:
        _rt_refuse("--backend: must name a backend of the config whose profile logs in with OAuth "
                   "(kind codex or openai with an oauth object)")
    provider = _RT_OAUTH_PROVIDERS[row.oauth_provider]
    if args.device and not provider.device_usercode_url:
        _rt_refuse(f"--device: a {backend.kind} backend has no device flow; use the browser login")
    user_agent = dict(_rt_rs_identity(backend)).get("user-agent", "llm-router") if row.identity else "llm-router"
    pinned: List[bytes] = []
    flow = _rt_login_device if args.device else _rt_login_browser
    try:
        oauth = flow(args, cfg, backend, provider, row, user_agent, pinned)
    except OAuthError as exc:
        print("llm-router: login failed: " + (f"{exc.kind} ({exc.code})" if exc.code else exc.kind),
              file=sys.stderr)
        return 2
    except UpstreamError as exc:
        print("llm-router: login failed: the auth endpoint: " + cfg.scrub(_rt_one_line(exc.message)),
              file=sys.stderr)
        return 2
    try:
        _RtTokenStore(args.config, cfg).login_result(backend.name, oauth)
    except ConfigError as exc:
        _rt_refuse(str(exc))
    print(f"llm-router: login ok: backend {backend.name}", file=sys.stderr)
    return 0


def main() -> None:
    if sys.argv[1:2] == ["login"]:
        sys.exit(_rt_login(_rt_login_parser().parse_args(sys.argv[2:])))
    args = _rt_parser().parse_args()

    try:
        _configure_logging(args.debug, args.log_file)
    except OSError as exc:
        _rt_refuse(f"--log-file: cannot open it for append ({type(exc).__name__})")

    try:
        settings = _rt_settings(args)
        cfg = load_config(args.config)
    except ConfigError as exc:
        _rt_refuse(str(exc))

    # KD-20: sweep stale writer temps under the config flock (a no-op without an
    # OAuth backend, R2-L5; a refusal is one WARNING), then the token store that
    # persists rotated refresh tokens to this very file.
    _rt_startup_sweep(args.config, cfg)
    tokens = _RtTokenStore(args.config, cfg)

    # Bind first (P18): nothing else has started, so a refusal has nothing to stop.
    try:
        srv = _RouterHttpServer((settings.bind, settings.port), settings, cfg, tokens)
    except OSError as exc:                       # EADDRINUSE, EADDRNOTAVAIL, EACCES ...
        _rt_refuse(f"cannot listen on {settings.bind}:{settings.port}: "
                   f"{exc.strerror or type(exc).__name__}")

    # The handlers go in before the ready file: a supervisor that signals as
    # soon as the file appears must reach the ordered shutdown, not the default
    # action.
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: stop.set())

    if settings.ready_file is not None:
        try:
            _http_write_ready_file(settings.ready_file, srv.server_port, ConfigError)
        except ConfigError as exc:
            srv.server_close()
            _rt_refuse(str(exc))

    # serve_forever on its own thread: shutdown() blocks until serve_forever
    # returns, so it is never called from the serving thread (as mcp-proxy's
    # _stop_http_server does).
    threading.Thread(target=srv.serve_forever, name="rt-serve", daemon=True).start()
    while not stop.is_set():
        stop.wait(_RT_TICK_S)
    _rt_shutdown(srv)
    sys.exit(0)


def _rt_shutdown(srv: _RouterHttpServer) -> None:
    """The ordered shutdown: closing -> unblock live pumps -> drain -> close -> ready file -> flush."""
    # 1. A POST accepted from here on gets 529; a streaming handler picks
    #    "router shutting down" on its next tick.
    srv.closing.set()
    # 2. Unblock every registered upstream socket -- a live pump's, and any model
    #    call or token POST still connecting, waiting for its head or reading its
    #    body (R-0083, R-0095) -- with a raw shutdown, never close() (P24, KD-4).
    with srv.inflight_lock:
        for sock in list(srv.inflight):
            try:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
            except OSError:
                pass
    # 3. Drain the running handlers for at most _DRAIN_S; the rest are daemon
    #    threads and are abandoned (count only).
    with srv.active_cond:
        srv.active_cond.wait_for(lambda: srv.active == 0, timeout=_DRAIN_S)
        left = srv.active
    if left > 0:
        log.warning("shutdown drain abandoned %d handler(s)", left)
    # 4. Close the listener, remove our ready file (V24), then flush every
    #    logging handler, each in its own try.
    srv.shutdown()
    srv.server_close()
    if srv.settings.ready_file is not None:
        _http_remove_ready_file(srv.settings.ready_file, log)
    for handler in logging.getLogger().handlers + log.handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 -- best effort on the way out
            pass


if __name__ == "__main__":
    main()
