#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""LLM-Router: an Anthropic Messages API router in front of local LLM backends.

One router instance serves Claude Code. It accepts Anthropic Messages requests
(/v1/messages, /v1/messages/count_tokens), looks the request's `model` up in
its JSON config, and forwards the call to the backend that route names --
relayed as-is (passthrough), with llama.cpp quirks repaired (llamacpp), or
translated to and from the Mistral chat API (mistral). GET /v1/models and
GET /v1/models/{id} list the config's route keys (with an optional route
display_name) in the Anthropic model-list shape, never contacting a backend.
Every answer the client sees is Anthropic-shaped: a JSON body, an SSE stream
that always closes its envelope, or the {"type":"error","error":{...}}
envelope. Requires only Python 3.9+ stdlib modules.

Layout: one file, eight units, in file order (each has one reason to change)
-----------------------------------------------------------------------------
  1. ConfigError, ApiError, UpstreamError, _STATUS_TO_TYPE, _rt_error_body() -- the error classes and
     the Anthropic error envelope; plus the pure helpers units 2-5 share: _log_value, _http_token_value
     (verbatim mcp-proxy copies), _rt_dumps(), _rt_loads() and _rt_make_scrubber() (KD-9).
  2. BackendSpec, RouteSpec, RouterConfig, load_config() -- the config schema. Pure: reads one file,
     never opens a socket.
  3. InboundRequest, _rt_parse_inbound(), _rt_route() -- validate the Anthropic request once; route lookup;
     _rt_models_list() / _rt_model_entry() -- the GET /v1/models answer shapes.
  4. The SSE toolkit -- _rt_sse_events() (a line parser) and AnthropicSseEncoder (a pure state machine
     that always closes its envelope).
  5. Adapters -- Adapter (the interface), PassthroughAdapter, LlamacppAdapter (+ its quirk registry),
     MistralAdapter (+ the tool-id map, _rt_ms_translate, MistralStreamTranslator), KIND_CLASSES,
     ADAPTERS, RESERVED_KINDS. Pure: no socket, no clock.
  6. The outbound transport -- the hand-copied address classifier, _rt_resolve, _rt_open_socket,
     _RtHttpConnection / _RtHttpsConnection, _rt_send(). The only code that opens an outbound socket.
  7. The HTTP front -- an adapted copy of mcp-proxy's (ADR 0028): _RouterHttpServer,
     _HeaderDeadlineReader, _RouterHandler, the upstream pump (_rt_pump) and the relay loops.
  8. Entry point -- argparse, main(), signal handling and shutdown.

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

Exit codes
----------
  0  normal exit, including a signal-initiated shutdown (SIGTERM / SIGINT).
  2  a config, command-line or bind refusal: one `llm-router: <msg>` line on
     stderr. The message names the field and the rule, never the value.

Declared limits (not fixed; ADR 0028 carries the full list)
-----------------------------------------------------------
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
    every entry's created_at is the router's start time.
"""

import argparse
import base64
import collections
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
_ERROR_TEXT_WIDTH = 300           # upstream error text relayed to the client, cut here
# _log_value: width of one logged wire string (repr form) and of one logged key list (CWE-117).
_LOG_VALUE_WIDTH = 80
_LOG_KEYS_SHOWN = 16
_MINTED_PREFIX = "toolu_lr"       # prefix of tool-use ids the router mints for Mistral


# ---------------------------------------------------------------------------
# Unit 1: errors
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """A config or command-line refusal; the message names the field, never the value. Exit 2."""


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


def _log_value(value: Any) -> str:
    """A STRUCTURAL wire value (method, id, tool name, argument keys) as it may
    appear in a log line: no control character, bounded length (CWE-117).

    A str is cut to _LOG_VALUE_WIDTH characters BEFORE repr() (F6: a huge wire
    string is never copied whole), then logged as that repr() -- every control
    character escaped -- capped at 4 * _LOG_VALUE_WIDTH characters, since one
    escaped character can take up to ten; "..." follows whenever either cut
    happened (R3-F5), so a capped repr is never mistaken for a whole one. An
    int or None as itself
    (a huge int as its bit length); a list (the argument KEYS) item by item, at
    most _LOG_KEYS_SHOWN items; anything else as its type name only. Never
    handed a payload VALUE (ADR 0011).
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
        shown = [type(item).__name__ if isinstance(item, list) else _log_value(item)   # one level only
                 for item in value[:_LOG_KEYS_SHOWN]]
        if len(value) > _LOG_KEYS_SHOWN:
            shown.append("+%d more" % (len(value) - _LOG_KEYS_SHOWN))
        return "[" + ", ".join(shown) + "]"
    return type(value).__name__


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


def _rt_loads(text: Union[str, bytes]) -> Any:
    """json.loads for every inbound and upstream body: a non-finite number is a
    ValueError, so each caller's malformed-JSON path answers it (V19)."""
    return json.loads(text, parse_constant=_rt_no_constant, parse_float=_rt_finite_float)


def _http_token_value(value: str, where: str) -> bytes:
    """Validate the bearer token and return it as ASCII bytes; refusals never include it."""
    if len(value) < _TOKEN_MIN_LEN:
        raise ConfigError(f"{where}: the bearer token must be at least {_TOKEN_MIN_LEN} characters")
    if not all(0x21 <= ord(ch) <= 0x7E for ch in value):
        raise ConfigError(f"{where}: the bearer token must be printable ASCII with no whitespace")
    return value.encode("ascii")


def _rt_make_scrubber(secrets: Tuple[bytes, ...]) -> Callable[[str], str]:
    """Build scrub(text): every known secret form -> "[redacted]", then printable, bounded (KD-9).

    Per secret the forms are the value itself, its URL-encoding, its standard and
    URL-safe base64 (padded and unpadded) and, when it is at least 16 characters,
    its first and last 8 characters -- a backend may echo any of them in an error
    text the router relays. Forms are replaced longest first, so a short form never
    splits a longer one before that one is found. Then every non-printable
    character becomes "?" and the text is cut to _ERROR_TEXT_WIDTH. Pure: base64
    and urllib.parse only. The closure is never logged or formatted.
    """
    forms = set()
    for raw in secrets:
        if not raw:
            continue
        s = raw.decode("ascii")
        std = base64.b64encode(raw).decode("ascii")
        safe = base64.urlsafe_b64encode(raw).decode("ascii")
        forms.update((s, urllib.parse.quote(s, safe=""),
                      std, std.rstrip("="), safe, safe.rstrip("=")))
        if len(s) >= 16:
            forms.add(s[:8])
            forms.add(s[-8:])
    ordered = tuple(sorted((f for f in forms if f), key=len, reverse=True))

    def scrub(text: str) -> str:
        text = str(text)
        for form in ordered:
            text = text.replace(form, "[redacted]")
        # The strip is one character for one, so cutting first gives the same text.
        return "".join(c if c.isprintable() else "?" for c in text[:_ERROR_TEXT_WIDTH])

    return scrub


# ---------------------------------------------------------------------------
# Unit 2: config
# ---------------------------------------------------------------------------

# A key-path segment shown as itself; any other key is shown as its _log_value repr.
_RT_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PEM_CERT_MARKER = "-----BEGIN CERTIFICATE-----"

# Every backend kind the config enum knows; RESERVED_KINDS are in the enum but
# refused by the loader (D6: anthropic is decided by its own plan).
_KINDS = ("passthrough", "llamacpp", "mistral", "anthropic")
RESERVED_KINDS = ("anthropic",)


def _rt_redacted_repr(obj: tuple, shown: Mapping[str, str]) -> str:
    """NamedTuple-shaped repr of *obj* with every field named in *shown* printed as its fixed text.

    The secret-bearing fields are never formatted: their value is not even read.
    """
    parts = []
    for field in obj._fields:
        text = shown.get(field)
        parts.append("%s=%s" % (field, text if text is not None else repr(getattr(obj, field))))
    return "%s(%s)" % (type(obj).__name__, ", ".join(parts))


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


class BackendSpec(_BackendSpecBase):
    """One validated backend. `api_key` is a secret: its repr is `<redacted>` (I6)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return _rt_redacted_repr(self, {"api_key": "<redacted>"})


class RouteSpec(NamedTuple):
    name: str                   # the routes key, or "(default)" for the fallback route; used in logs only
    backend: str
    model: str                  # upstream model id
    options: Dict[str, Any]     # validated by the kind's option validators
    display_name: Optional[str] = None   # GET /v1/models's display_name; None -> the route key


class _RouterConfigBase(NamedTuple):    # from --config (routing + secrets)
    token: bytes                # SECRET
    backends: Dict[str, BackendSpec]
    routes: Dict[str, RouteSpec]
    default: Optional[RouteSpec]
    secrets: Tuple[bytes, ...]  # every secret, for the outbound scrubber (KD-9)
    scrub: Callable[[str], str]  # _rt_make_scrubber(secrets), built once by load_config (unit 1, KD-9)


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


def _rt_read_config_file(path: str) -> str:
    """Read the config file after checking the opened file (P3, the stricter model).

    O_NOFOLLOW refuses a symlink; the checks run on the opened descriptor
    (fstat), so the inode checked is the inode read. The config holds secrets
    (auth_token, api_key), so it must be the caller's and 0600-tight. Every
    refusal names the rule, never the content.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
                              "idle_timeout", "allow_cleartext_api_key"})
_RT_ROUTE_KEYS = frozenset({"backend", "model", "options", "display_name"})
_RT_BACKEND_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")    # the _CHILD_NAME_RE shape
_RT_AUTH_HEADERS = ("authorization", "x-api-key", "none")
_RT_HOST_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")
_RT_URL_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_RT_MODEL_LIMIT = 256                     # a route key and an upstream model id, in characters
_RT_DISPLAY_NAME_LIMIT = 256              # a route's optional display_name, in characters
_RT_DEFAULT_PORTS = {"https": 443, "http": 80}


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


def _rt_cfg_base_url(value: Any, where: str) -> Tuple[str, str, int, str]:
    """(scheme, host, port, base_path) of a backend base_url; userinfo, query and fragment refused by name.

    urllib.parse is a pure string module (it reads no environment). The URL is
    never echoed: every refusal names the part and the rule.
    """
    where = f"{where}.base_url"
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
    scheme, host, port, base_path = _rt_cfg_base_url(entry.get("base_url"), where)
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
    if kind == "mistral":
        if auth_header not in (None, "authorization"):
            raise ConfigError(f"{where}.auth_header: kind mistral uses authorization only")
        auth_header = "authorization"
    elif auth_header is None:
        auth_header = "x-api-key" if api_key is not None else "none"
    if auth_header != "none" and api_key is None:
        raise ConfigError(f"{where}.auth_header: {auth_header} requires an api_key")
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
                                     f"{where}.idle_timeout"))


def _rt_cfg_route(name: str, entry: Any, backends: Mapping[str, BackendSpec],
                  where: Optional[str] = None) -> RouteSpec:
    """Validate one route (a routes.<name> entry, or the top-level default) into a RouteSpec.

    The backend must exist; model is a non-empty str of at most _RT_MODEL_LIMIT
    characters with no control character; display_name, when present, is a
    non-empty str of at most _RT_DISPLAY_NAME_LIMIT characters with no control
    character (GET /v1/models only; harmless on the default); options are
    checked by the backend kind's validators and an unknown option is refused
    by name.
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
    raw = entry.get("options", {})
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.options: must be an object")
    kind = backends[backend].kind
    options = KIND_CLASSES[kind].validate_options(raw, f"{where}.options")
    return RouteSpec(name=name, backend=backend, model=model, options=options,
                     display_name=display_name)


_RT_TOKEN_HINT = "generate one with secrets.token_urlsafe(32)"


def _rt_cfg_token(token: str) -> bytes:
    """auth_token as ASCII bytes: _http_token_value's length and charset rules, then at
    least _TOKEN_MIN_DISTINCT distinct characters (V1: 32 x "a" is long, not secret).
    Every refusal names the generator; none names the value. The throttle-free 401
    leaves entropy to the operator, so the loader refuses the obviously weak."""
    try:
        token_bytes = _http_token_value(token, "auth_token")
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
    return RouterConfig(token=token_bytes, backends=backends, routes=routes, default=default,
                        secrets=secrets_, scrub=_rt_make_scrubber(secrets_))


# ---------------------------------------------------------------------------
# Unit 3: inbound
# ---------------------------------------------------------------------------

_RT_ROLES = ("user", "assistant")    # a "system" entry inside messages is a 400, as on the Anthropic API


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


def _rt_bad(field: str, rule: str) -> ApiError:
    """A 400 refusal naming the field and the rule; never the content."""
    return ApiError(400, "invalid_request_error", "%s: %s" % (field, rule))


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
    (the front owns the clock; this unit never reads one)."""
    return {"type": "model", "id": route.name, "display_name": route.display_name or route.name,
            "created_at": created_at}


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
    {role in (user, assistant), content: str | list of dicts with a str "type"};
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
        if message.get("role") not in _RT_ROLES:
            raise _rt_bad(where + ".role", "must be user or assistant")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise _rt_bad(where + ".content", "must be a string or a list of blocks")
        for j, block in enumerate(content):
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise _rt_bad("%s.content.%d" % (where, j), "must be an object with a string type")
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

    Tracks the next content block index, an open text block, whether any tool
    block was emitted, and the started / closed flags. Every method returns
    b"" once closed (after finish() or error()).
    """

    def __init__(self, message_id: str, model: str) -> None:
        self.message_id = message_id
        self.model = model
        self.index = 0                      # the next content block index
        self.text_open = False
        self.any_tool = False
        self.started = False
        self.closed = False

    @staticmethod
    def _event(name: str, payload: dict) -> bytes:
        return b"event: " + name.encode("ascii") + b"\ndata: " + _rt_dumps(payload) + b"\n\n"

    def _close_text(self) -> bytes:
        if not self.text_open:
            return b""
        self.text_open = False
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
        if not self.text_open:
            self.text_open = True
            out += self._event("content_block_start", {
                "type": "content_block_start", "index": self.index,
                "content_block": {"type": "text", "text": ""}})
        return out + self._event("content_block_delta", {
            "type": "content_block_delta", "index": self.index,
            "delta": {"type": "text_delta", "text": delta}})

    def tool_block(self, tool_id: str, name: str, args_json: str) -> bytes:
        """A whole tool_use block: start, one input_json_delta, stop.
        An open text block is closed first."""
        if self.closed:
            return b""
        out = self._close_text()
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
        out = self._close_text()
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


class Adapter:
    """One subclass per backend kind (KD-12); instances are stateless.

    The class attributes are the kind's tables: FORWARDABLE (client headers a
    backend MAY forward, lower-case), DEFAULT_FORWARD (forwarded when the backend
    sets no forward_headers) and ROUTE_OPTIONS (option -> validator(value, where)).
    The four methods are sans-IO request builders and translators; the base
    raises NotImplementedError, so a kind without them is never in ADAPTERS.
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
    """kind passthrough: an Anthropic-compatible upstream; only `model` is rewritten (KD-16)."""

    kind = "passthrough"
    FORWARDABLE = frozenset({"anthropic-version", "anthropic-beta"})
    DEFAULT_FORWARD = frozenset({"anthropic-version", "anthropic-beta"})
    ROUTE_OPTIONS: Mapping[str, _RtOptionValidator] = {}

    @staticmethod
    def _rewrite(inbound: InboundRequest) -> bytes:
        """The client body with only `model` set to the route's upstream model."""
        body = dict(inbound.body)
        body["model"] = inbound.route.model
        return _rt_dumps(body)

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        return "/v1/messages", self._rewrite(inbound)

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

    A role "system" entry inside messages never gets here: unit 3 answers it 400.
    """
    system = body.get("system")
    if not isinstance(system, list):
        return body
    new = dict(body)
    new["system"] = "\n\n".join(b["text"] for b in system
                                if isinstance(b, dict) and b.get("type") == "text"
                                and isinstance(b.get("text"), str))
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
        """The client body through the request quirks, then `model` set to the route's upstream model."""
        body = dict(self._apply_request_quirks(dict(inbound.body), inbound.route))
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
    return "".join(reversed(chars)).rjust(width, "0")[:width]


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
# is never mutated.

_RT_MS_ORPHAN = "[tool result for an earlier call]\n"
_RT_MS_IMAGE_OMITTED = "[image omitted]"
_RT_MS_BRIDGE = "Done."
_RT_MS_TOOL_CHOICE = {"auto": "auto", "any": "any", "none": "none"}


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


def _rt_ms_translate(body: dict, route: RouteSpec) -> dict:
    """The Anthropic /v1/messages body -> a NEW Mistral chat-completions body (plan Step 10).

    Only the keys built here are sent: model, messages, max_tokens, temperature,
    top_p, stop, stream, tools, tool_choice, parallel_tool_calls. System text
    becomes the first message; assistant tool_use blocks become tool_calls with
    KD-6 ids and JSON-string arguments (thinking dropped); user tool_result blocks
    become tool messages, an orphan one (no tool_use in the history) a user text;
    a tool message directly followed by a user message gets an assistant "Done."
    bridge. A block type Mistral cannot take is a 400 naming the type.
    """
    options = route.options
    messages: List[dict] = []
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
            messages.append({"role": "system", "content": system_text})

    history = body["messages"]
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
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts: List[str] = []
            calls: List[dict] = []
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
                elif block_type in ("thinking", "redacted_thinking"):
                    continue
                else:
                    raise _rt_ms_unsupported(block_type, where)
            out_message: Dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
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
    return out


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


class MistralStreamTranslator:
    """The mistral stream translator (sans-IO, KD-5, KD-7, KD-16).

    Owns one AnthropicSseEncoder (a fresh msg_ id, the requested model). begin()
    is message_start; feed(line) takes one upstream SSE line; finish() runs once,
    on `data: [DONE]` or at EOF, and emits the buffered tool calls in key order
    before message_delta / message_stop. Every failure is one `event: error` and
    closes the stream -- never a message_stop after it. Every method returns b""
    once closed. dropped_thinking counts the non-text content parts dropped.
    """

    def __init__(self, inbound: InboundRequest, input_tokens: int) -> None:
        self.encoder = AnthropicSseEncoder(_rt_message_id(), inbound.requested_model)
        self.scrub = inbound.scrub
        self.input_tokens = input_tokens
        self.tools: Dict[Union[int, str], Dict[str, Any]] = {}   # key -> {"id", "name", "args": [str]}
        self.args_bytes = 0
        self.finish_reason: Optional[str] = None
        self.usage: Dict[str, Any] = {}
        self.seen_chunk = False
        self.dropped_thinking = 0

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
        out = b""
        content = self._content_text(delta.get("content"))
        if content:
            out += self.encoder.text(content)
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
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

    def _content_text(self, content: Any) -> str:
        """delta.content as text: a str as is, a list's "text" parts joined
        (every other part dropped and counted)."""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
            else:
                self.dropped_thinking += 1
        return "".join(parts)

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
        out = b"".join(self.encoder.tool_block(*block) for block in blocks)
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


def _rt_ms_text(content: Any) -> str:
    """A chat-completions message `content` as text: a str as is, a list's
    "text" parts joined (every other part, such as "thinking", dropped)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(part["text"] for part in content
                   if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))


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
    }

    def __init__(self) -> None:
        # G-M2: one translation per request. upstream_request leaves a stream
        # request's estimate here, keyed by id(inbound), and stream_translator
        # (called next, on the same live inbound) pops it. Only ints are kept, at
        # most _RT_MS_PENDING_CAP of them: an entry orphaned by a failed send is
        # evicted oldest-first, and a miss only costs a second translation. No
        # lock and no threading (unit 5 is pure, J4): single dict operations
        # are atomic, and the eviction tolerates a concurrent change.
        self._pending: Dict[int, int] = {}

    def upstream_request(self, inbound: InboundRequest) -> Tuple[str, bytes]:
        """POST /v1/chat/completions with _rt_ms_translate's body."""
        translated = _rt_ms_translate(inbound.body, inbound.route)
        if inbound.stream:
            self._pending[id(inbound)] = _rt_ms_estimate(translated, inbound.route)
            while len(self._pending) > _RT_MS_PENDING_CAP:
                try:
                    self._pending.pop(next(iter(self._pending)), None)
                except (StopIteration, RuntimeError):
                    break
        return "/v1/chat/completions", _rt_dumps(translated)

    def count_tokens(self, inbound: InboundRequest) -> Union[Tuple[str, bytes], dict]:
        """A local estimate (D4, _rt_ms_estimate); one translation per request (G-M2)."""
        return {"input_tokens": _rt_ms_estimate(_rt_ms_translate(inbound.body, inbound.route), inbound.route)}

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
        content: List[dict] = []
        text = _rt_ms_text(message.get("content"))
        if text:
            content.append({"type": "text", "text": text})
        tool_calls = message.get("tool_calls")
        for call in tool_calls if isinstance(tool_calls, list) else ():
            block = _rt_ms_tool_use(call)
            if block is None:
                return 502, _rt_error_body("api_error", "backend returned malformed tool arguments")
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
        """The stream translator with the local input-token estimate, taken from
        upstream_request when it translated this very request (G-M2)."""
        estimate = self._pending.pop(id(inbound), None)
        if estimate is None:
            estimate = _rt_ms_estimate(_rt_ms_translate(inbound.body, inbound.route), inbound.route)
        return MistralStreamTranslator(inbound, estimate)


# kind -> adapter class: the single source of each kind's tables (load_config reads it).
KIND_CLASSES: Dict[str, Type[Adapter]] = {
    "passthrough": PassthroughAdapter,
    "llamacpp": LlamacppAdapter,
    "mistral": MistralAdapter,
}


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
# A future kind in KIND_CLASSES but not here loads fine and is answered 501.
ADAPTERS: Dict[str, Adapter] = {}
ADAPTERS["passthrough"] = PassthroughAdapter()
ADAPTERS["llamacpp"] = LlamacppAdapter()
ADAPTERS["mistral"] = MistralAdapter()


# ---------------------------------------------------------------------------
# Unit 6: outbound transport
# ---------------------------------------------------------------------------

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


def _rt_resolve(backend: BackendSpec, deadline: Optional[float] = None) -> List[str]:
    """The backend's host resolved ONCE and every address vetted -> the ordered address list.

    Called for every connect (no cache). _rt_getaddrinfo runs on a daemon
    thread joined with what remains of the absolute `time.monotonic()`
    `deadline` (default: connect_timeout from now) -- the same deadline
    _rt_open_socket and the TLS handshake then use (V9). A resolver that has
    not answered by then is 504 "backend timed out: ..."; its thread is
    abandoned and ends when the OS resolver gives up. Each address must
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
    resolver.join(max(0.0, deadline - time.monotonic()))
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


def _rt_open_socket(addresses: Iterable[str], port: int, deadline: float) -> socket.socket:
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
    """
    addrs = list(addresses or ())
    if not addrs:
        raise UpstreamError(502, "api_error", "connect: no address to connect to")
    last = None
    timed_out = True
    for tried, addr in enumerate(addrs):
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
            sock.connect((addr, port))
            return sock
        except socket.timeout:
            last = "%s: timed out" % addr
        except OSError as exc:
            lines = str(exc).splitlines()
            last = "%s: %s" % (addr, lines[0][:120] if lines else type(exc).__name__)
            timed_out = False
        if sock is not None:
            sock.close()
    if timed_out:
        raise UpstreamError(504, "api_error", "backend timed out: all %d addresses timed out: %s" % (len(addrs), last))
    raise UpstreamError(502, "api_error", "connect: all %d addresses failed: %s" % (len(addrs), last))


_RT_RECV_BYTES = 65536    # one _rt_read_body read1() call


class _RtHttpConnection(http.client.HTTPConnection):
    """A plain-HTTP connection to ONE vetted backend: connect() uses _rt_open_socket, never a resolver.

    `addresses` is _rt_resolve's list and `deadline` the absolute monotonic()
    connect deadline. After connect the socket's timeout is the backend's
    idle_timeout (one upstream read), and `raw_sock` keeps the connected
    socket: http.client drops `sock` once a Connection: close response is
    read, and the pump's shutdown(SHUT_RDWR) needs the descriptor (KD-4, P24).
    """

    def __init__(self, backend: BackendSpec, addresses: List[str], deadline: float):
        super().__init__(backend.host, backend.port)
        self._backend = backend
        self._addresses = list(addresses)
        self._deadline = deadline
        self.raw_sock: Optional[socket.socket] = None

    def connect(self) -> None:
        sock = _rt_open_socket(self._addresses, self.port, self._deadline)
        sock.settimeout(self._backend.idle_timeout)
        self.sock = sock
        self.raw_sock = sock


class _RtHttpsConnection(http.client.HTTPSConnection):
    """A verified-TLS connection to ONE vetted backend (P20).

    The context is create_default_context(cadata=ca_pem) when the backend has
    a ca_file (which then REPLACES the system trust store, KD-18), else
    create_default_context(); the floor is raised to TLS 1.2 (3.9 leaves
    MINIMUM_SUPPORTED) and OP_IGNORE_UNEXPECTED_EOF is cleared, so a ragged
    EOF stays an error and a truncated upstream is detected (D12). Then
    CERT_REQUIRED, check_hostname and the floor are ASSERTED: any miss is an
    UpstreamError 502 before a socket is opened. connect() is _rt_open_socket
    plus the handshake under the same deadline, suppress_ragged_eofs=False;
    then the idle timeout, and `raw_sock` is the SSLSocket.
    """

    def __init__(self, backend: BackendSpec, addresses: List[str], deadline: float):
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
        self._addresses = list(addresses)
        self._deadline = deadline
        self.raw_sock: Optional[socket.socket] = None

    def connect(self) -> None:
        sock = _rt_open_socket(self._addresses, self.port, self._deadline)
        try:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("the connect deadline passed")
            sock.settimeout(remaining)
            tls = self._rt_context.wrap_socket(sock, server_hostname=self._backend.host,
                                               suppress_ragged_eofs=False)
        except BaseException:
            sock.close()
            raise
        tls.settimeout(self._backend.idle_timeout)
        self.sock = tls
        self.raw_sock = tls


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
             body: bytes) -> Tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """POST `body` to backend.base_path + path -> (conn, resp) with the final response head read.

    The ONLY putheader site in the file (J4a) and the single framing owner:
    the caller's `headers` (built by _rt_upstream_headers) go through
    putheader one by one, so http.client's injection check is a second line
    of defence; then `Accept-Encoding: identity` and `Content-Length:
    str(len(body))` are added HERE, after them (endheaders(body) never adds a
    Content-Length; C3). The host is resolved and vetted per call, under the
    same connect deadline as the connect and the TLS handshake (V9). No
    redirect is followed (a 3xx is a 502), an interim 1xx is a 502, and a
    Content-Encoding other than identity is a 502. Every failure closes the
    connection and is raised as _rt_upstream_refusal's UpstreamError.
    The caller owns the returned pair and closes both.
    """
    deadline = time.monotonic() + backend.connect_timeout
    addresses = _rt_resolve(backend, deadline)
    cls: Type[http.client.HTTPConnection] = (_RtHttpsConnection if backend.scheme == "https"
                                            else _RtHttpConnection)
    conn = cls(backend, addresses, deadline)
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


def _rt_upstream_headers(inbound: Any) -> List[Tuple[str, str]]:
    """The upstream request headers of `inbound`, built from an ALLOW-list (KD-3).

    Duck-typed on inbound.backend, inbound.client_headers and inbound.stream.
    Order: Content-Type, Accept (text/event-stream when streaming, else
    application/json), User-Agent, then each client header whose lower-case
    name is in backend.forward_headers (once), then the credential LAST:
    `Authorization: Bearer <api_key>` or `x-api-key: <api_key>` per
    auth_header, nothing for "none". The ONLY reader of `client_headers`
    (J4b) and the only builder of the credential line. Content-Length and
    Accept-Encoding are _rt_send's.
    """
    backend = inbound.backend
    headers = [("Content-Type", "application/json"),
               ("Accept", "text/event-stream" if inbound.stream else "application/json"),
               ("User-Agent", "llm-router")]
    allowed = backend.forward_headers
    seen = set()
    for name, value in (inbound.client_headers or {}).items():
        key = name.lower() if isinstance(name, str) else None
        if key is None or key not in allowed or key in seen:
            continue
        seen.add(key)
        headers.append((key, value))
    if backend.auth_header == "authorization" and backend.api_key:
        headers.append(("Authorization", "Bearer %s" % backend.api_key))
    elif backend.auth_header == "x-api-key" and backend.api_key:
        headers.append(("x-api-key", backend.api_key))
    return headers


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


def _rt_pump(conn: Any, resp: Any, q: _RtByteQueue, stop: threading.Event) -> None:
    """The upstream reader thread (KD-4): the ONLY thread that reads `resp`.

    Started as threading.Thread(target=_rt_pump, name="rt-pump", daemon=True).
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
            line = resp.readline(_SSE_LINE_LIMIT + 1)
            if len(line) > _SSE_LINE_LIMIT:
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


def _rt_write_ready_file(path: str, port: int) -> None:
    """{"port", "pid"} written 0600 and atomically (.tmp + os.replace)."""
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
        raise ConfigError(f"cannot write --ready-file: {exc.strerror or type(exc).__name__}") from None


def _rt_remove_ready_file(path: str) -> None:
    """On shutdown, unlink the ready file only while it still holds this process's pid (V24).

    Read without following a symlink; a file another writer has replaced
    (another pid, not JSON, unreadable) is left alone. Best effort: never raises.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read(4096))
        pid = data.get("pid") if isinstance(data, dict) else None
        if type(pid) is int and pid == os.getpid():
            os.remove(path)
    except (OSError, ValueError):
        pass


class _RouterHttpServer(ThreadingHTTPServer):
    """The listener: one daemon thread per connection, at most
    settings.max_connections of them (CWE-400); never joined on close (H1).
    The shutdown drain waits on `active` instead, and unblocks live upstream
    reads through the raw sockets in `inflight` (KD-4)."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], settings: RouterSettings, cfg: RouterConfig) -> None:
        # Everything a handler reads is set BEFORE super().__init__, which
        # binds and listens (L4).
        self.settings = settings
        self.cfg = cfg
        self.conn_sem = threading.BoundedSemaphore(settings.max_connections)
        self.closing = threading.Event()
        self.loopback = ipaddress.IPv4Address(settings.bind).is_loopback
        # Raw upstream sockets of live streams: shutdown(SHUT_RDWR) unblocks
        # their pumps, never close() (P24).
        self.inflight: set = set()
        self.inflight_lock = threading.Lock()
        # Running handler threads, for the shutdown drain.
        self.active = 0
        self.active_cond = threading.Condition()
        # GET /v1/models's created_at for every entry: the start time, second precision, UTC.
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        super().__init__(addr, _RouterHandler)

    def server_bind(self) -> None:
        # The stdlib HTTPServer.server_bind does a reverse-DNS getfqdn() the
        # router never uses and that can stall startup (I1).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address) -> None:
        if not self.conn_sem.acquire(blocking=False):
            log.warning("http connection refused: %d connections open", self.settings.max_connections)
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
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

    def handle_error(self, request, client_address) -> None:
        # Type only: the stdlib traceback's message text can quote request data (ADR 0011).
        log.warning("http handler failed: %s", type(sys.exc_info()[1]).__name__)


class _HeaderDeadlineReader(io.RawIOBase):
    """The raw reader under a handler's rfile: enforces a TOTAL header deadline.

    While `deadline` (a time.monotonic() value) is set, every recv gets at most
    the time left until it, and none is attempted once it has passed: a client
    that trickles one header byte per recv can no longer hold its
    --max-connections slot past _HTTP_HEADER_TIMEOUT_S in total (F8), or past
    _HTTP_PREAUTH_TIMEOUT_S while it has never authenticated (V3). The
    socket.timeout raised is the one the stdlib's handle_one_request already
    answers by closing the connection. _post sets it again around the body
    read (_HTTP_BODY_TIMEOUT_S, V4); one recv never waits past
    _HTTP_SOCKET_TIMEOUT_S, which the header phase's shorter totals never reach.
    With `deadline` None it is a plain recv under the socket's own timeout.
    """

    def __init__(self, sock: socket.socket) -> None:
        super().__init__()
        self._sock = sock
        self.deadline: Optional[float] = None

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("header deadline passed")
            self._sock.settimeout(min(left, _HTTP_SOCKET_TIMEOUT_S))
        return self._sock.recv_into(buf)


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
        self._header_reader = _HeaderDeadlineReader(self.connection)
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

    def parse_request(self) -> bool:
        # The header phase ends here, before any do_* runs: the body read and
        # the SSE stream are never under the header deadline. The per-recv
        # pre-auth timeout stays until _precheck passes.
        try:
            return super().parse_request()
        finally:
            self._header_reader.deadline = None
            self.connection.settimeout(_HTTP_HEADER_TIMEOUT_S)

    def handle_expect_100(self) -> bool:
        # Called from parse_request, before any auth: send nothing (V37). _post
        # sends the 100 Continue itself once _precheck and the framing checks pass.
        return True

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
        """
        self.close_connection = True
        self._refuse(code, _STATUS_TO_TYPE.get(code, "invalid_request_error"),
                     _RT_STDLIB_REFUSAL.get(code, "malformed request"))

    def _single_header(self, name: str):
        """(ok, value) of a header that must appear at most once (ADR 0015:
        ambiguity is the defect): ok False when it is repeated, value None when absent."""
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            return False, None
        return True, (values[0] if values else None)

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
                        "end": "json", "dropped_thinking": None}
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
        try:
            inbound = _rt_parse_inbound(endpoint, body, self.headers, srv.cfg)
            backend = inbound.backend
            rec["route"], rec["kind"], rec["stream"] = inbound.route.name, backend.kind, int(inbound.stream)
            adapter = ADAPTERS.get(backend.kind)
            if adapter is None:
                # Unreachable for the three kinds (all in ADAPTERS). Kept as the
                # guard for a future kind added to KIND_CLASSES before its adapter
                # methods exist: it is answered 501 rather than half-served.
                self._send_json(501, _rt_error_body("api_error", "backend kind %s is not wired yet"
                                                    % backend.kind))
                return
            if endpoint == "count_tokens":
                local = adapter.count_tokens(inbound)
                if isinstance(local, dict):
                    self._send_json(200, local)
                    return
                path, up_body = local
            else:
                path, up_body = adapter.upstream_request(inbound)
            rec["up_t0"] = time.monotonic()
            conn, resp = _rt_send(inbound.backend, path, _rt_upstream_headers(inbound), up_body)
            if inbound.stream and endpoint == "messages":
                self._relay_stream(conn, resp, adapter.stream_translator(inbound), inbound)
            else:
                self._relay_json(conn, resp, adapter, inbound)
        except (ApiError, UpstreamError) as exc:
            if self._rt_head_sent:       # never after the SSE head: _relay_stream answers in-band
                self.close_connection = True
                return
            self._send_json(exc.status, _rt_error_body(exc.err_type, exc.message))
        except OSError:
            raise                        # a client write: do_POST's disconnect
        except Exception as exc:  # noqa: BLE001 -- the type name is logged, never a traceback (P7)
            log.warning("request failed: %s", type(exc).__name__)
            self.close_connection = True
            if not self._rt_head_sent:
                self._send_json(500, _rt_error_body("api_error", "internal error"))

    def _relay_json(self, conn: Any, resp: Any, adapter: Adapter, inbound: InboundRequest) -> None:
        """A non-stream upstream answer (or a stream request's non-2xx, KD-15):
        the whole body under _UPSTREAM_BODY_LIMIT, mapped by adapter.json_response,
        answered with _send_json. A 429's Retry-After is relayed when it is all
        digits. The handler owns conn and resp here (no pump): closed in finally."""
        rec = self._rt_rec
        rec["up_status"] = resp.status
        try:
            retry_after = (resp.getheader("Retry-After") or "").strip()
            up_body = _rt_read_body(resp, _UPSTREAM_BODY_LIMIT, inbound.backend.name)
        finally:
            for closer in (resp.close, conn.close):
                try:
                    closer()
                except OSError:
                    pass
        rec["up_t1"] = time.monotonic()
        status, obj = adapter.json_response(inbound, resp.status, up_body)
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
        ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "text/event-stream":
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
        pump = threading.Thread(target=_rt_pump, args=(conn, resp, q, stop), name="rt-pump", daemon=True)
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
            end = self._relay_loop(q, translator, backend, write, lambda: last_write, last_line)
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
                    last_line: float) -> str:
        """The relay ticks of _relay_stream -> how the stream ended (§9 `end`).

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
                                            % (backend.name, _SSE_LINE_LIMIT))
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


def _rt_parser() -> argparse.ArgumentParser:
    parser = _RtArgumentParser(
        prog="llm-router",
        description="Anthropic Messages API router for Claude Code "
                    "(localhost or VPN only, never a public interface).")
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


def main() -> None:
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

    # Bind first (P18): nothing else has started, so a refusal has nothing to stop.
    try:
        srv = _RouterHttpServer((settings.bind, settings.port), settings, cfg)
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
            _rt_write_ready_file(settings.ready_file, srv.server_port)
        except ConfigError as exc:
            srv.server_close()
            _rt_refuse(str(exc))

    # serve_forever on its own thread: shutdown() blocks until serve_forever
    # returns, so it is never called from the serving thread (mcp-proxy.py:1992).
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
    # 2. Unblock every live pump with a raw shutdown, never close() (P24, KD-4).
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
        _rt_remove_ready_file(srv.settings.ready_file)
    for handler in logging.getLogger().handlers + log.handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 -- best effort on the way out
            pass


if __name__ == "__main__":
    main()
