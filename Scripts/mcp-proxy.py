#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""MCP-Proxy: one upstream MCP endpoint in front of many child MCP servers.

One proxy instance serves one project root. It spawns every child named in its
JSON config (stdio, newline-delimited JSON-RPC 2.0), lists their tools and
exposes all of them through ONE upstream endpoint, under their original,
unprefixed names, with `description` and `inputSchema` relayed verbatim. The
consumer is ai-soul (its MCP SDK client); the proxy is deliberately NOT
registered in ~/.claude.json. Requires only Python 3.9+ stdlib modules.

Layout: one file, seven units, in file order (each has one reason to change)
-----------------------------------------------------------------------------
  1. ConfigError, ChildStartError, ChildUnavailable -- the error classes.
  2. ChildSpec, ProxyConfig, load_config(), build_child_argv(),
     build_child_env() -- config parsing and validation, `{root}` and
     `{scripts}` substitution, argv assembly. Never touches processes or asyncio.
  3. ChildClient -- one OS-process incarnation of a child: spawn (records the
     process-group id), rpc / notify_child, the stdout reader, an idempotent
     death, and a serialized, resumable stop ladder plus group sweep.
  4. ChildSlot -- one config entry across incarnations: state, the single
     restart loop (backoff, budget, tool-set equality), id and token counters.
  5. McpServer -- the router and the stdio front: tool table, request
     dispatch, the cancel registry, run / _serve / _write.
  6. HttpSession -- a plain record for one Streamable HTTP session.
  7. The HTTP front -- a ThreadingHTTPServer and its request handler.

The deliberate exception to "exactly one tool"
----------------------------------------------
Every other fleet server exposes exactly one dispatcher tool
(Scripts/MCP_SKELETON.md, the smoke invariants). The proxy aggregates: its
`tools/list` is the union of its children's tools. The smoke row runs it with
ONE stub child that has ONE tool, so the fleet invariant still holds there;
aggregation itself is tested in tests/test_mcp_proxy.py.

Framing ceiling, not answer ceiling (ADR 0013)
----------------------------------------------
_MAX_FRAME (64 MiB) bounds ONE newline-delimited message read from a child (the
StreamReader `limit=`). It is a framing bound, not a reply ceiling: a child's
payload is relayed verbatim and is never cut a second time. The proxy has no
answer ceiling and no per-call output cap. A line over the frame limit
means a desynced stream: the child's pending calls fail and it is restarted.

Why the child MCP client is hand-written
----------------------------------------
There is no canonical source for an MCP client, and exactly one host would
carry one, so a generated block would buy zero drift protection (ADR 0014,
ADR 0025). The generated `_mcp_lsp.py` request helper is not reused either: it
speaks LSP (`$/cancelRequest {id}`), while MCP needs
`notifications/cancelled {requestId}` and a notice to the peer on timeout.
Hence the hand-written methods are named rpc / notify_child.

The HTTP transport (--http): ThreadingHTTPServer bridged to the loop
--------------------------------------------------------------------
The Streamable HTTP front is the stdlib http.server.ThreadingHTTPServer, not a
hand-rolled HTTP/1.1 parser on asyncio.start_server. Its handler threads own
only their socket and a queue.Queue; everything else (sessions, the cancel
registry, the tool table, child state) lives on the event-loop thread and is
reached through asyncio.run_coroutine_threadsafe / call_soon_threadsafe with
every wait bounded by _HTTP_BRIDGE_TIMEOUT_S. The server runs on plain
threading.Thread, so the stdin reader stays the file's only executor (ADR 0008).

One protocol version on both transports. McpServer.PROTOCOL_VERSION is what the
proxy answers on stdio and on HTTP and what it sends to its children; there is
no negotiation and no per-transport subclass. The HTTP layer only validates the
client's MCP-Protocol-Version header against SUPPORTED_PROTOCOL_VERSIONS, which
starts with PROTOCOL_VERSION; a missing header assumes the oldest Streamable
HTTP revision, which is in that set.

Answer modes. Every tools/call is answered as text/event-stream: the headers go
out as soon as the loop has started the call, a `: keepalive` comment follows
every _HTTP_KEEPALIVE_S while it waits, then progress events and the response.
No event carries an `id:` line (an event id would mark the stream resumable,
and a stream closed without a response would then make the SDK reconnect).
Every other request is answered as JSON, a notification with 202.

Cancel versus disconnect. An HTTP client that disconnects does NOT cancel its
call: the call runs on and its result is discarded. Only notifications/cancelled
and DELETE (which ends the session and cancels its in-flight calls) cancel; so
do idle eviction and shutdown. A cancelled tools/call closes its stream with no
response event; 202 with no body is kept for a cancelled non-tools/call request.

Access. The listener takes an IPv4 literal, loopback by default; any other
address needs --allow-remote. It is bound before any child is spawned. A bearer
token is required, from --token-file (owner-only, mode 0600) or from
MCP_PROXY_TOKEN, which is popped from the environment before any spawn and is
never logged; it is compared in constant time. Host (loopback binds) and Origin
are checked, the header phase runs under its own timeout until auth passes, and
--max-connections, --max-sessions, --max-inflight and --session-idle bound the
connections, sessions, live request tasks and idle sessions.

Timeouts on the ai-soul side
----------------------------
The ai-soul SDK aborts a request after 60 s by default. Long forge or jenkins
calls therefore need `timeout` and/or `resetTimeoutOnProgress` on the ai-soul
side; progress notifications are forwarded, so reset-on-progress is effective.
The `: keepalive` comments are not progress notifications and do NOT reset that
timeout. Node's fetch (undici) is assumed -- not verified -- to default to a
300 s headersTimeout and bodyTimeout; the immediate SSE headers and the
keepalive comments keep both from firing on a long call. The proxy's own
per-child call timeout (default 3600 s) is independent of all of these.

Exit codes
----------
  0  normal end (upstream stdin EOF, or an orderly signal-initiated shutdown)
  1  startup failure (a child failed to spawn, initialize or list its tools)
  2  configuration or command-line refusal (one stderr line `mcp-proxy: <msg>`)

Declared limits (accepted, not gated)
-------------------------------------
  * The upstream stdout write is a blocking write on the loop thread, the same
    as the rest of the fleet: an upstream that stops reading stalls the proxy.
  * The upstream stdin line is uncapped (the read_loop gate forbids a capped
    readline); only the child side and the HTTP request (headers by a total
    deadline, body by --max-body-bytes) are bounded.
  * Children are spawned with start_new_session=True and swept by process
    group, so a grandchild that put itself in its OWN session survives; on a
    proxy SIGKILL children see stdin EOF and exit, their own-session
    grandchildren do not.
  * A grandchild that keeps a child's stdout open delays the EOF-based death
    detection of that child until the grandchild exits.
  * Group-sweep residuals: if the group empties between two probes and the
    kernel wraps the pid space back to that exact id before the next probe, a
    stray signal could land; and a member that survives SIGKILL past the final
    poll (uninterruptible sleep) keeps `pgid` set, so a later stop signals
    that still-non-empty group again.
  * The post-SIGKILL wait for a group leader in uninterruptible sleep is
    unbounded.
  * ChildSlot.stop() waits at most _STOP_WAIT_S for a cancelled restart task
    before it stops the incarnations itself.
  * A child whose tool NAMES are unchanged after a restart but whose schemas
    changed is not detected; only the name set is compared.
  * On Python 3.9 `asyncio.wait_for` can swallow a cancellation that races the
    inner awaitable's completion, so an upstream cancel landing in the same
    loop iteration as the child's reply may let the reply through (harmless:
    a client may ignore a response that arrives after its cancel).
  * Every frame is parsed by _strict_loads (R-0067, R-0068): an integer literal
    over 4300 characters and NaN/Infinity are refused as unparseable on every
    Python, so 3.9.6's missing int-digit limit (CVE-2020-10735) no longer
    reaches int(). A CHILD line so refused is dropped like any non-JSON line,
    so the call it answered waits for its call_timeout. The config and the
    ready file are read with plain json.loads: neither is a peer's frame.
  * Child-side logging is outside the wire_log gate. It logs structure only:
    a child-controlled method or id goes through _log_value (repr, bounded,
    CWE-117), never a params value; the proxy's own suite (K3) gates every
    ChildClient method that handles wire data.
  * Children inherit the proxy's stderr unfiltered: whatever a child writes
    there (its own log, a traceback quoting a payload) lands in the proxy's
    stderr or --log-file target as-is, outside every gate above.
  * 3.10+ APIs outside the fleet's fixed api_310 list and the suite's own K4
    case are a blind spot.
  * Exception text (`<Type>: <msg>`) reaches the upstream caller in the
    -32603 reply and in the isError tool result (CWE-209), the fleet-wide
    shape. Its reader is the upstream parent on stdio and, with --http, any
    holder of the bearer token -- the same principal that can call the tools.
  * The config's `env` is trusted operator input (it can set LD_PRELOAD,
    PYTHONPATH, ... for a child), the same trust the config holds by naming
    the command; the config file's owner and mode are checked. Only
    MCP_PROXY_TOKEN is refused there.
  * The HTTP listener is IPv4-only: --bind takes an IPv4 literal; IPv6 and
    host names (including localhost) are refused at startup. The Host check
    still accepts `localhost:<port>`, but a client should be pointed at
    http://127.0.0.1:<port>/mcp: one that resolves localhost to ::1 first
    would hand the bearer to whatever listens on [::1]:<port>.
  * A notifications/cancelled whose request has not yet reached the loop (two
    handler threads parse independently) names an unknown id and is ignored,
    as the MCP cancellation rules allow.
  * A disconnected HTTP client holds its handler thread and --max-connections
    slot until the next write fails: up to _HTTP_KEEPALIVE_S plus one socket
    write timeout after it went away.
  * A request the loop does not start within _HTTP_BRIDGE_TIMEOUT_S is
    answered 503, but may still run later with nobody reading its result; an
    initialize stalled that way creates a session no client learns of, which
    holds a --max-sessions slot until --session-idle evicts it; the same holds
    when _refuse_orphan's drop of a refused initialize's session cannot reach
    a stalled loop.
  * Because a disconnect is not a cancel, up to --max-inflight abandoned calls
    may keep their children busy until they finish or hit the per-child call
    timeout.
  * The wire half of the 202 answer for a cancelled non-tools/call request
    needs a cross-thread race to drive live; only its loop half is tested.
  * The undici timeout defaults above are an assumption, not verified.
  * The Accept header is not enforced.
  * HTTP logging, like child-side logging, is outside the wire_log gate; it is
    structure-only (wire values through _log_value) and covered by the
    proxy's own suite (J26). A 401/403 is logged at WARNING with the status
    and peer address only.
  * The header phase -- request line plus headers, of every request on a
    keep-alive connection -- has a TOTAL deadline of _HTTP_HEADER_TIMEOUT_S
    (each recv gets only the time left), so a header trickle is cut there.
    Until the bearer check passes the timeout is also per recv; a slow body
    after auth is bounded per recv only (_HTTP_SOCKET_TIMEOUT_S).
  * There is no failed-auth throttling: a wrong bearer costs one 401 and a
    reconnect, bounded only by --max-connections, and each refusal writes one
    WARNING line, so log volume grows with an unauthenticated peer's request
    rate. Token strength is checked by length and charset only
    (_TOKEN_MIN_LEN), never by entropy.
  * The Host check (DNS-rebinding defence) runs only on a loopback bind; with
    --allow-remote the bearer token, plus Origin when sent, is the control.
  * No TLS: with --allow-remote the bearer travels in plaintext, so a
    non-loopback bind belongs on a VPN interface, never a public one.
  * Progress queued for one HTTP response is capped at _HTTP_SINK_PROGRESS_CAP
    (the newest progress item is dropped, never the response); a client that
    stops reading holds its thread until the next write times out.
  * A token taken from MCP_PROXY_TOKEN stays readable in /proc/<pid>/environ
    (Linux, same uid) for the proxy's lifetime: popping it from os.environ does
    not rewrite the initial environment block. --token-file is recommended.
  * One token is one principal: sessions are not bound to the token that
    opened them, so any bearer holder can use or DELETE a session whose id it
    knows. Session ids are 256-bit random and never logged in full.
"""

import argparse
import asyncio
import collections
import concurrent.futures
import hmac
import io
import ipaddress
import json
import logging
import os
import queue
import random
import re
import secrets
import shutil
import signal
import socket
import socketserver
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Deque, Dict, FrozenSet, List, NamedTuple, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("mcp-proxy")


# Every peer-chosen method, id, tool name or argument key reaches a log line
# through _log_value: a line break in one would forge a line (CWE-117, R-0070).
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


# Strict JSON for every frame the proxy reads or writes, on both fronts and both
# directions (R-0067, R-0068): a NaN/Infinity token, a float that overflows to
# one and an integer literal over 4300 characters are refused as a
# json.JSONDecodeError, and an emit refuses a non-finite float with ValueError.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: JSON_INT_LITERAL_LIMIT, _json_no_constant, _json_finite_float, _json_bounded_int, _strict_loads, _strict_dumps
JSON_INT_LITERAL_LIMIT = 4300


def _json_no_constant(name: str) -> float:
    """json parse_constant: NaN, Infinity and -Infinity are not JSON (R-0067)."""
    raise ValueError(f"non-finite number {name} is not JSON", name)


def _json_finite_float(text: str) -> float:
    """json parse_float: a literal that overflows to an infinity (1e999) is refused like NaN."""
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError(f"number {text[:32]} overflows to an infinity", text)
    return value


def _json_bounded_int(text: str) -> int:
    """json parse_int: a literal over JSON_INT_LITERAL_LIMIT characters is refused before int() sees it (R-0068)."""
    if len(text) > JSON_INT_LITERAL_LIMIT:
        raise ValueError(f"integer literal of {len(text)} characters exceeds {JSON_INT_LITERAL_LIMIT}", text)
    return int(text)


def _strict_loads(text):
    """json.loads for a peer's frame: every refusal is a json.JSONDecodeError.

    *text* is str or bytes, as json.loads takes it. A NaN or infinity token, a
    float that overflows to one and an integer literal over
    JSON_INT_LITERAL_LIMIT characters are refused at the literal's position;
    any other ValueError (an undecodable byte string) is re-raised as a
    JSONDecodeError at position 0. RecursionError is not converted: a host
    that answers deep nesting catches it itself.
    """
    try:
        return json.loads(text, parse_constant=_json_no_constant, parse_float=_json_finite_float, parse_int=_json_bounded_int)
    except json.JSONDecodeError:
        raise
    except ValueError as exc:
        doc = text if isinstance(text, str) else bytes(text).decode("utf-8", "replace")
        if len(exc.args) == 2 and isinstance(exc.args[1], str):
            raise json.JSONDecodeError(exc.args[0], doc, max(0, doc.find(exc.args[1]))) from None
        raise json.JSONDecodeError(str(exc), doc, 0) from None


def _strict_dumps(obj) -> str:
    """json.dumps for a frame to a peer: NaN and the infinities raise ValueError (R-0067).

    allow_nan=False is the only difference: separators and ensure_ascii stay
    json.dumps' defaults, so every frame that serialised before is
    byte-identical. A caller that can be handed a non-finite float keeps the
    ValueError arm it already has for a value json cannot serialise.
    """
    return json.dumps(obj, allow_nan=False)
# END GENERATED: 37665b5ce9ad


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Framing bound for ONE newline-delimited message from a child, the same size as
# mcp-purity's _LSP_MAX_MESSAGE (Scripts/mcp-purity.py:2451). NOT a reply ceiling:
# the proxy relays a child's payload verbatim and must never cut it a second time
# (ADR 0013). Named without a CHARS/BYTES suffix on purpose, so
# tests/test_mcp_footprint.py's CAP_CONST_RX does not read it as an output cap.
_MAX_FRAME = 64 * 1024 * 1024
# Allowed values of the config key "frame_limit". The key is deliberately NOT
# spelled max_*_bytes: tests/test_mcp_footprint.py's CAP_PARAM_RX would read a
# .get("max_frame_bytes", ...) as a per-call output cap (R-24).
_FRAME_RANGE = (1024 * 1024, 512 * 1024 * 1024)
# Bound on one child stdin drain; a child that consumes nothing this long is wedged.
_DRAIN_TIMEOUT_S = 30.0
# Bound on sending a best-effort notice (e.g. notifications/cancelled) to a child.
_NOTICE_TIMEOUT_S = 1.0
# Per-child tools/call timeout when the config gives none.
_DEFAULT_CALL_TIMEOUT_S = 3600.0
# Upper bound accepted for any configured timeout (call or startup).
_MAX_CALL_TIMEOUT_S = 86400.0
# Per-child bound on spawn + initialize + tools/list when the config gives none.
_DEFAULT_STARTUP_TIMEOUT_S = 60.0
# Restart backoff ladder (seconds); the last value is the cap, plus up to 20% jitter.
_RESTART_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
# Restarts allowed within _RESTART_WINDOW_S before the child is DISABLED.
_RESTART_BUDGET = 5
# Sliding window (seconds) the restart budget is counted over.
_RESTART_WINDOW_S = 600.0
# Grace per step of the stop ladder and of the process-group sweep.
_SHUTDOWN_GRACE_S = 3.0
_STOP_WAIT_S = 4 * _SHUTDOWN_GRACE_S + 1.0   # ChildSlot.stop()'s bound on the cancelled restart task (H1)
# Upper bound on tools/list pages followed via nextCursor per child.
_MAX_TOOL_PAGES = 100
# The bearer token's environment variable; popped in main() before any spawn (FR-11).
_TOKEN_ENV = "MCP_PROXY_TOKEN"
# Allowed child names (config "name"): lowercase, digits, '_' and '-', 1-32 chars.
_CHILD_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Phase 2: the Streamable HTTP front (--http). Localhost/VPN only, never public.
_HTTP_DEFAULT_PATH = "/mcp"
# Default --max-body-bytes: one POST body. Named without a MAX_ prefix so
# tests/test_mcp_footprint.py's CAP_CONST_RX does not read it as an output cap.
_HTTP_BODY_LIMIT = 4 * 1024 * 1024
_HTTP_SESSION_CAP = 16            # default --max-sessions
_HTTP_CONNECTION_CAP = 32         # default --max-connections
_HTTP_INFLIGHT_CAP = 32           # default --max-inflight: HTTP request tasks alive at once (CWE-770, R-34)
_HTTP_INFLIGHT_RANGE = (1, 1024)  # accepted --max-inflight values (load_http_settings)
_HTTP_SESSION_IDLE_S = 86400.0    # default --session-idle
_HTTP_HEADER_TIMEOUT_S = 10.0     # TOTAL bound on one request line + headers, and per-recv until auth (NFR-4)
_HTTP_SOCKET_TIMEOUT_S = 30.0     # per-recv socket timeout after auth
_HTTP_KEEPALIVE_S = 15.0          # SSE ": keepalive" comment interval while a tools/call waits
_HTTP_BRIDGE_TIMEOUT_S = 5.0      # bound on every handler-thread wait on the loop (H1, round 3)
_HTTP_SINK_PROGRESS_CAP = 1024    # queued progress items per HTTP response; response, markers + sentinel never dropped
# Extra headers of the stdlib's own refusals (send_error: 400/414/431/501/505),
# answered fixed and body-less (R-0069).
_HTTP_STDLIB_REFUSAL_HEADERS = (("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"))
_SESSION_GONE = object()          # sink marker: the session closed before _http_start ran -> 404
_REQUEST_STARTED = object()       # sink marker: _http_start created and registered the task
_BUSY = object()                  # sink marker: --max-inflight reached -> 503 + Connection: close (R-34)
_TOKEN_MIN_LEN = 32               # minimum bearer token length (characters)
_ASSUMED_HEADER_VERSION = "2025-03-26"  # MCP-Protocol-Version assumed when the header is absent


# ---------------------------------------------------------------------------
# Unit 1: errors
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """A config or command-line refusal; the message names the key or child. Exit 2."""


class ChildStartError(Exception):
    """A child failed to spawn, initialize or list its tools at startup. Exit 1."""


class ChildUnavailable(Exception):
    """The child is dead, restarting, disabled, wedged or timed out -> an isError result."""


# ---------------------------------------------------------------------------
# Unit 2: config -- parsing, validation, `{root}` substitution, argv and env
# ---------------------------------------------------------------------------

class ChildSpec(NamedTuple):
    """One validated config entry: what to spawn and how long to wait for it."""
    name: str
    argv: Tuple[str, ...]
    env: Dict[str, str]
    call_timeout: float
    startup_timeout: float


class ProxyConfig(NamedTuple):
    """The validated config: the project root, the children, the child framing bound."""
    root: str
    children: Tuple[ChildSpec, ...]
    max_frame: int


_TOP_KEYS = frozenset({"root", "scripts", "children", "frame_limit"})
_CHILD_KEYS = frozenset({"name", "command", "args", "env", "wrapper", "call_timeout", "startup_timeout"})


def _cfg_executable(value: Any, where: str) -> str:
    """Validate a command (or wrapper[0]) and return the path that will execute.

    Absolute paths are kept; a bare name is resolved with shutil.which; a
    relative path containing "/" is refused as ambiguous (it would depend on
    the proxy's cwd). The refusal names *where*, never the value.
    """
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: must be a non-empty string")
    if os.path.isabs(value):
        return value
    if "/" in value:
        raise ConfigError(f"{where}: a relative path is ambiguous; use an absolute path or a bare name on PATH")
    resolved = shutil.which(value)
    if resolved is None:
        raise ConfigError(f"{where}: not found on PATH")
    return resolved


def _cfg_str_list(value: Any, where: str) -> List[str]:
    """A list of strings (default empty), else ConfigError naming *where*."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{where}: must be a list of strings")
    return list(value)


def _cfg_timeout(value: Any, default: float, where: str) -> float:
    """A number (bool refused) with 0 < x <= _MAX_CALL_TIMEOUT_S, else ConfigError."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: must be a number of seconds")
    if not 0 < value <= _MAX_CALL_TIMEOUT_S:
        raise ConfigError(f"{where}: must be > 0 and <= {_MAX_CALL_TIMEOUT_S:g}")
    return float(value)


def _cfg_read_file(path: str) -> str:
    """Read the config file after checking it is a regular, uid-owned, not group/world-writable file.

    The checks run on the opened descriptor (fstat), so the file that was
    checked is the file that is read.
    """
    try:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise ConfigError("--config: not a regular file")
            if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ConfigError("--config: refusing a group- or world-writable config file")
            if st.st_uid != os.geteuid():
                raise ConfigError("--config: refusing a config file not owned by the current user")
            raw = fh.read()
    except OSError as exc:
        raise ConfigError(f"--config: cannot read the config file ({type(exc).__name__})") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("--config: the config file is not valid UTF-8") from None


def _cfg_root(config_root: Any, cli_root: Optional[str]) -> str:
    """Resolve the project root from --project-root and/or the config's "root" (ADR 0015)."""
    if config_root is not None and (not isinstance(config_root, str) or not config_root):
        raise ConfigError("root: must be a non-empty string")
    if cli_root is not None and config_root is not None:
        if os.path.realpath(cli_root) != os.path.realpath(config_root):
            raise ConfigError("--project-root and the config's root name different directories")
    chosen = cli_root if cli_root is not None else config_root
    if chosen is None:
        raise ConfigError("no project root: pass --project-root or set root in the config")
    root = os.path.realpath(chosen)
    if not os.path.isdir(root):
        raise ConfigError("the project root is not an existing directory")
    return root


def _cfg_scripts(value: Any) -> Optional[str]:
    """Resolve the config's optional "scripts" directory (realpath; must exist)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError("scripts: must be a non-empty string")
    scripts = os.path.realpath(os.path.expanduser(value))
    if not os.path.isdir(scripts):
        raise ConfigError("scripts: not an existing directory")
    return scripts


def _cfg_subst_scripts(arg: str, scripts: Optional[str], where: str) -> str:
    """Replace "{scripts}" (whole arg) or a leading "{scripts}/" prefix with the scripts dir."""
    if arg != "{scripts}" and not arg.startswith("{scripts}/"):
        return arg
    if scripts is None:
        raise ConfigError(f"{where}: uses {{scripts}} but the config has no scripts key")
    return scripts + arg[len("{scripts}"):]


def _cfg_child(entry: Any, index: int, root: str, scripts: Optional[str] = None) -> ChildSpec:
    """Validate one children[] entry and build its ChildSpec."""
    where = f"children[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be an object")
    unknown = sorted(str(key) for key in entry if key not in _CHILD_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")
    name = entry.get("name")
    if not isinstance(name, str) or not _CHILD_NAME_RE.match(name):
        raise ConfigError(f"{where}.name: must match {_CHILD_NAME_RE.pattern}")
    where = f"child {name!r}"
    command = entry.get("command")
    if isinstance(command, str):
        command = _cfg_subst_scripts(command, scripts, f"{where}: command")
    command = _cfg_executable(command, f"{where}: command")
    args = _cfg_str_list(entry.get("args"), f"{where}: args")
    wrapper = [_cfg_subst_scripts(item, scripts, f"{where}: wrapper")
               for item in _cfg_str_list(entry.get("wrapper"), f"{where}: wrapper")]
    if wrapper:
        wrapper[0] = _cfg_executable(wrapper[0], f"{where}: wrapper[0]")
    env = entry.get("env")
    if env is None:
        env = {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ConfigError(f"{where}: env must be an object of string values")
    if _TOKEN_ENV in env:
        raise ConfigError(f"{where}: env must not set {_TOKEN_ENV}")
    call_timeout = _cfg_timeout(entry.get("call_timeout"), _DEFAULT_CALL_TIMEOUT_S, f"{where}: call_timeout")
    startup_timeout = _cfg_timeout(entry.get("startup_timeout"), _DEFAULT_STARTUP_TIMEOUT_S,
                                   f"{where}: startup_timeout")
    args = [root if arg == "{root}" else _cfg_subst_scripts(arg, scripts, f"{where}: args") for arg in args]
    return ChildSpec(
        name=name,
        argv=build_child_argv(wrapper, command, args),
        env=dict(env),
        call_timeout=call_timeout,
        startup_timeout=startup_timeout,
    )


def load_config(path: Optional[str], inline: Optional[str], cli_root: Optional[str]) -> ProxyConfig:
    """Parse and validate the proxy config from a file (*path*) or a JSON string (*inline*).

    Exactly one source must be given. Every refusal is a ConfigError whose
    message names the key or child and never echoes a value (an env value or
    an argument may be a secret). Never touches processes or asyncio.
    """
    if (path is None) == (inline is None):
        raise ConfigError("give exactly one of --config or --config-json")
    text = _cfg_read_file(path) if path is not None else inline
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}") from None
    except (ValueError, RecursionError) as exc:
        # Deep nesting or an over-long integer: a refusal, never a traceback. The
        # type only -- the message of either never needs the config's text.
        raise ConfigError(f"config is not valid JSON: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise ConfigError("config: the top level must be a JSON object")
    unknown = sorted(str(key) for key in data if key not in _TOP_KEYS)
    if unknown:
        raise ConfigError(f"config: unknown key(s): {', '.join(unknown)}")

    root = _cfg_root(data.get("root"), cli_root)
    scripts = _cfg_scripts(data.get("scripts"))

    frame_limit = data.get("frame_limit", _MAX_FRAME)
    if (isinstance(frame_limit, bool) or not isinstance(frame_limit, int)
            or not _FRAME_RANGE[0] <= frame_limit <= _FRAME_RANGE[1]):
        raise ConfigError(f"frame_limit: must be an integer in [{_FRAME_RANGE[0]}, {_FRAME_RANGE[1]}]")

    children = data.get("children")
    if not isinstance(children, list) or not children:
        raise ConfigError("children: must be a non-empty list")
    specs: List[ChildSpec] = []
    seen: Set[str] = set()
    for index, entry in enumerate(children):
        spec = _cfg_child(entry, index, root, scripts)
        if spec.name in seen:
            raise ConfigError(f"children[{index}]: duplicate name {spec.name!r}")
        seen.add(spec.name)
        specs.append(spec)
    return ProxyConfig(root=root, children=tuple(specs), max_frame=frame_limit)


def build_child_argv(wrapper: List[str], command: str, args: List[str]) -> Tuple[str, ...]:
    """The exec argv: wrapper + [command] + args. Never a shell string (no splitting, no quoting)."""
    return tuple(list(wrapper) + [command] + list(args))


def build_child_env(extra: Dict[str, str]) -> Dict[str, str]:
    """The child's environment: the proxy's own, minus _TOKEN_ENV, updated with *extra*.

    main() already popped _TOKEN_ENV before any spawn (FR-11); the pop here is
    defensive, and load_config refuses an *extra* that names it.
    """
    env = dict(os.environ)
    env.pop(_TOKEN_ENV, None)
    env.update(extra)
    return env


class HttpSettings(NamedTuple):
    """The validated --http settings. `token` is the bearer secret: never logged or formatted."""
    bind: str
    port: int
    path: str
    allow_remote: bool
    token: bytes
    origins: FrozenSet[str]
    max_body: int
    max_sessions: int
    max_connections: int
    max_inflight: int
    session_idle: float
    ready_file: Optional[str]


def _http_token_file(path: str) -> str:
    """Read the bearer token from *path* after checking the opened file.

    O_NOFOLLOW refuses a symlink; the checks run on the opened descriptor
    (fstat), so the inode checked is the inode read. Every refusal names the
    rule, never the content. One trailing newline is stripped. The bytes are
    decoded as latin-1 (never fails) so _http_token_value can refuse anything
    outside printable ASCII without echoing it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ConfigError(f"--token-file: cannot open the token file ({type(exc).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError("--token-file: not a regular file")
        if st.st_uid != os.geteuid():
            raise ConfigError("--token-file: refusing a token file not owned by the current user")
        if st.st_mode & 0o077:
            raise ConfigError("--token-file: refusing a token file readable or writable by group/others "
                              "(chmod 600)")
        chunks: List[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise ConfigError(f"--token-file: cannot read the token file ({type(exc).__name__})") from None
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    return raw.decode("latin-1")


def _http_token_value(value: str, where: str) -> bytes:
    """Validate the bearer token and return it as ASCII bytes; refusals never include it."""
    if len(value) < _TOKEN_MIN_LEN:
        raise ConfigError(f"{where}: the bearer token must be at least {_TOKEN_MIN_LEN} characters")
    if not all(0x21 <= ord(ch) <= 0x7E for ch in value):
        raise ConfigError(f"{where}: the bearer token must be printable ASCII with no whitespace")
    return value.encode("ascii")


def load_http_settings(args, env_token: Optional[str]) -> HttpSettings:
    """Validate the --http flags and the bearer token into HttpSettings (refuse-to-start rules).

    *env_token* is the MCP_PROXY_TOKEN value main() popped before any spawn.
    Bind is an IPv4 literal only; a non-loopback bind needs --allow-remote.
    Exactly one token source (ADR 0015). Every refusal is a ConfigError
    (exit 2) and never contains the token.
    """
    try:
        addr = ipaddress.IPv4Address(args.bind)
    except ValueError:
        raise ConfigError("--bind takes an IPv4 literal (e.g. 127.0.0.1); "
                          "IPv6 and host names are not supported") from None
    if not addr.is_loopback and not args.allow_remote:
        raise ConfigError(f"--bind {addr} is not a loopback address; pass --allow-remote to listen on it "
                          "(localhost or a VPN interface only, never a public one)")

    if args.token_file is not None and env_token is not None:
        raise ConfigError(f"give exactly one bearer token source: --token-file or {_TOKEN_ENV}, not both")
    if args.token_file is None and env_token is None:
        raise ConfigError("HTTP mode requires a bearer token")
    if args.token_file is not None:
        token = _http_token_value(_http_token_file(args.token_file), "--token-file")
    else:
        token = _http_token_value(env_token, _TOKEN_ENV)

    if not 0 <= args.port <= 65535:
        raise ConfigError("--port must be between 0 and 65535")
    if not args.http_path.startswith("/"):
        raise ConfigError("--http-path must start with '/'")
    for flag, value in (("--max-body-bytes", args.max_body_bytes), ("--max-sessions", args.max_sessions),
                        ("--max-connections", args.max_connections)):
        if value < 1:
            raise ConfigError(f"{flag} must be at least 1")
    if not _HTTP_INFLIGHT_RANGE[0] <= args.max_inflight <= _HTTP_INFLIGHT_RANGE[1]:
        raise ConfigError(f"--max-inflight must be between {_HTTP_INFLIGHT_RANGE[0]} and {_HTTP_INFLIGHT_RANGE[1]}")
    if not 0 < args.session_idle < float("inf"):
        raise ConfigError("--session-idle must be a positive number of seconds")

    return HttpSettings(
        bind=str(addr),
        port=args.port,
        path=args.http_path,
        allow_remote=bool(args.allow_remote),
        token=token,
        origins=frozenset(args.allowed_origin or ()),
        max_body=args.max_body_bytes,
        max_sessions=args.max_sessions,
        max_connections=args.max_connections,
        max_inflight=args.max_inflight,
        session_idle=args.session_idle,
        ready_file=args.ready_file,
    )


# ---------------------------------------------------------------------------
# Module helpers (used by the child client, the slot and the server)
# ---------------------------------------------------------------------------

def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a child's whole process group; False once it must not be signalled again.

    pgid is captured at spawn (start_new_session=True: pgid == the child's pid).
    POSIX does not reuse an id as a pid or pgid while a group with that id has
    a member, so this never reaches a stranger while a member survives, and it
    never signals the reaped leader's pid as a pid (NFR-5). False means ESRCH
    (group observed empty) or EPERM (not ours to signal); the caller then sets
    ChildClient.pgid = None and never uses that id again.
    """
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        log.warning("process group signal refused: %s", type(exc).__name__)
        return False
    return True


def _with_progress_token(params: dict, token: Any) -> dict:
    """Return a copy of *params* whose `_meta.progressToken` is *token*.

    The one owner of the progressToken rewrite (M3). A non-None *token* is set
    (the per-child token on the way down). A None *token* strips it: the key is
    removed and an emptied `_meta` is dropped. Shallow copies of `params` and of
    `params["_meta"]` (a non-dict `_meta` is replaced by a new dict), so the
    caller's dict is never mutated.
    """
    copy = dict(params)
    old_meta = params.get("_meta")
    meta = dict(old_meta) if isinstance(old_meta, dict) else {}
    if token is not None:
        meta["progressToken"] = token
        copy["_meta"] = meta
    else:
        meta.pop("progressToken", None)
        if meta:
            copy["_meta"] = meta
        else:
            copy.pop("_meta", None)
    return copy


def _log_task_failure(task: asyncio.Task) -> None:
    """Done-callback for every fire-and-forget task: retrieve and log its failure.

    Retrieving the exception is what prevents asyncio's "Task exception was
    never retrieved"; only the exception TYPE is logged (ADR 0011).
    """
    if not task.cancelled() and task.exception() is not None:
        log.debug("background task failed: %s", type(task.exception()).__name__)


_BAD_ID_MESSAGE = "Invalid Request: id must be a string or an integer"


def _bad_request_id(msg: dict) -> bool:
    """True for a request (it has "method") whose id is present, not null, and
    not a string or an integer (R3-F1/F2, CWE-674/CWE-20).

    The test is McpServer._request_key's: a bool or a float is refused, and any
    string -- "" included, as JSON-RPC and MCP allow -- is an id. A response
    (no "method") and an id-less or null-id message are not judged here.
    """
    if "method" not in msg or msg.get("id") is None:
        return False
    return McpServer._request_key(msg["id"]) is None


def _fallback_id(rid: Any) -> Any:
    """*rid* for the error that replaces a reply json.dumps refused, or None
    when *rid* itself cannot be serialised (R3-F1, defence in depth behind
    _bad_request_id): re-embedding a too-deep id made the fallback raise the
    same RecursionError, uncaught. A serialisable id is kept, so the caller
    still learns which request failed."""
    try:
        _strict_dumps(rid)
    except (TypeError, ValueError, RecursionError):
        return None
    return rid


# ---------------------------------------------------------------------------
# Unit 3: ChildClient -- one OS-process incarnation of a child
# ---------------------------------------------------------------------------

class ChildClient:
    """One OS-process incarnation of a child MCP server (newline-delimited JSON-RPC).

    Constructed INSIDE the running loop: on Python 3.9 an asyncio.Lock binds to
    the loop current at creation. Every field is loop-thread owned. A recovered
    child is always a freshly constructed ChildClient; `dead` is never reset.
    The methods are named rpc / notify_child, never _request / _notify: those
    are canonical generated-block names (`_mcp_lsp.py`) that speak LSP, not MCP.
    """

    def __init__(self, slot: "ChildSlot") -> None:
        self.slot = slot
        self.process: Optional[asyncio.subprocess.Process] = None
        # == process.pid, captured right after spawn (start_new_session=True). Set
        # to None once the group sweep observes ESRCH (or gets EPERM); no signal
        # is ever sent to that id afterwards (NFR-5).
        self.pgid: Optional[int] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._routes: Dict[int, str] = {}          # child id -> per-child progress token
        self._send_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()           # serializes stop(): one ladder/sweep at a time
        self._reader_task: Optional[asyncio.Task] = None
        self.dead = False                          # set once, by _die or stop
        self._side_tasks: Set[asyncio.Task] = set()   # replies to child->proxy requests; strong refs

    async def start(self) -> None:
        """Spawn the child in its own session and start the stdout reader.

        `limit=` is the framing bound (_MAX_FRAME or the config's frame_limit):
        asyncio's 64 KiB default would break a large result (R-5).
        """
        self.process = await asyncio.create_subprocess_exec(
            *self.slot.spec.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            cwd=self.slot.root,
            env=build_child_env(self.slot.spec.env),
            limit=self.slot.max_frame,
            start_new_session=True,
        )
        self.pgid = self.process.pid          # start_new_session=True: the child leads its own group
        log.debug("child=%s spawned pid=%d", self.slot.spec.name, self.process.pid)
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        """Read the child's stdout, one JSON-RPC message per line, until EOF or death.

        A line over the frame limit is a desynced stream (restart); a non-JSON or
        non-object line under it is pollution (dropped, the reader continues).
        Any unexpected exception ends this incarnation through _die, so a live
        child is never left behind a dead reader (M2). Logs are structure only.
        """
        name = self.slot.spec.name
        limit = self.slot.max_frame
        try:
            while True:
                try:
                    line = await self.process.stdout.readline()
                except ValueError:
                    log.warning("child=%s frame over %d bytes; restarting", name, limit)
                    self._die("oversize frame")
                    break
                if not line:
                    self._die("stdout EOF")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = _strict_loads(line)
                except (ValueError, RecursionError):
                    log.debug("child=%s non-JSON stdout line (%d bytes) dropped", name, len(line))
                    continue
                if not isinstance(msg, dict):
                    log.debug("child=%s non-JSON stdout line (%d bytes) dropped", name, len(line))
                    continue

                # Shape guards (M2): a non-string method or a non-object params
                # never reaches a dispatch branch.
                method = msg.get("method")
                if "method" in msg and not isinstance(method, str):
                    log.warning("child=%s message with a non-string method dropped", name)
                    if "id" in msg:
                        self._reply_to_child({"jsonrpc": "2.0", "id": msg["id"],
                                              "error": {"code": -32600, "message": "invalid request"}})
                    continue
                params = msg.get("params", {})
                params_ok = isinstance(params, dict)

                if method is None:
                    if "id" in msg and ("result" in msg or "error" in msg):
                        self._on_response(msg)
                    else:
                        log.debug("child=%s message without method or result dropped", name)
                    continue

                if "id" in msg:
                    if method == "ping":
                        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                    else:
                        log.warning("child=%s asked for %s; refused (-32601)", name, _log_value(method))
                        reply = {"jsonrpc": "2.0", "id": msg["id"],
                                 "error": {"code": -32601,
                                           "message": f"mcp-proxy does not serve {method} to children"}}
                    self._reply_to_child(reply)
                    continue

                if not params_ok:
                    log.warning("child=%s %s with non-object params dropped", name, _log_value(method))
                    continue
                if method == "notifications/progress":
                    token = params.get("progressToken")
                    emit = self.slot.progress.get(token) if isinstance(token, str) else None
                    if emit is not None:
                        emit(params)
                    else:
                        log.debug("child=%s %s for an unknown progress token dropped", name, _log_value(method))
                elif method == "notifications/tools/list_changed":
                    log.warning("child=%s %s ignored", name, _log_value(method))
                else:
                    log.debug("child=%s notification %s ignored", name, _log_value(method))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("child=%s reader failed: %s", name, type(exc).__name__)
            self._die("reader failed")

    def _on_response(self, msg: dict) -> None:
        """Route one child response to its pending future; pop its progress route."""
        raw_id = msg.get("id")
        key = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
        fut = self._pending.pop(key, None) if key is not None else None
        token = self._routes.pop(key, None) if key is not None else None
        if token:
            self.slot.progress.pop(token, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)
        else:
            log.debug("child=%s late reply id=%s", self.slot.spec.name, _log_value(key))

    def _reply_to_child(self, reply: dict) -> None:
        """Answer a child->proxy request as a strongly referenced side task.

        The reader never awaits its own child's stdin. A dead child's
        ChildUnavailable is retrieved by _log_task_failure (M4).
        """
        task = asyncio.ensure_future(self._send_line(reply))
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)
        task.add_done_callback(_log_task_failure)

    def _die(self, why: str) -> None:
        """Mark this incarnation dead, fail its pending calls, report ONE death (M1).

        Idempotent: a wedged write, an oversize frame and the EOF that follows
        report a single death. The slot is told in `finally`, so a failure while
        failing the futures can never skip recovery (L1).
        """
        if self.dead:
            return
        self.dead = True
        name = self.slot.spec.name
        try:
            log.debug("child=%s died: %s (pending=%d)", name, why, len(self._pending))
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ChildUnavailable(f"child '{name}' {why}"))
            self._pending.clear()
            for token in list(self._routes.values()):
                self.slot.progress.pop(token, None)
            self._routes.clear()
        finally:
            self.slot.on_child_death(self, why)

    async def _send_line(self, obj: dict) -> None:
        """Write one message to the child's stdin: serialized, with a bounded drain (R-6)."""
        payload = (_strict_dumps(obj) + "\n").encode("utf-8")
        name = self.slot.spec.name
        async with self._send_lock:
            stdin = self.process.stdin if self.process is not None else None
            if self.dead or stdin is None or stdin.is_closing():
                raise ChildUnavailable(f"child '{name}' is not running")
            try:
                stdin.write(payload)
                await asyncio.wait_for(stdin.drain(), _DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("child=%s stdin not drained in %gs; restarting", name, _DRAIN_TIMEOUT_S)
                self._die(f"wedged: stdin not drained in {_DRAIN_TIMEOUT_S:g}s")
                raise ChildUnavailable(f"child '{name}' wedged: stdin not drained") from None
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                log.debug("child=%s stdin write failed: %s", name, type(exc).__name__)
                self._die("stdin closed")
                raise ChildUnavailable(f"child '{name}' stdin closed") from None

    async def notify_child(self, method: str, params: dict) -> None:
        """Send a notification to the child; best effort, bounded, never raises."""
        try:
            await asyncio.wait_for(
                self._send_line({"jsonrpc": "2.0", "method": method, "params": params}),
                _NOTICE_TIMEOUT_S,
            )
        except Exception as exc:
            log.debug("child=%s notice %s not sent: %s", self.slot.spec.name, _log_value(method),
                      type(exc).__name__)

    async def rpc(self, method: str, params: Any, timeout: float,
                  on_progress: Optional[Callable[[dict], None]] = None) -> dict:
        """Send one request to the child and wait for its response message.

        The child id comes from the slot's counter (never reset across
        incarnations); the upstream id never reaches the child. With
        *on_progress*, a per-child progress token "px:<name>:<n>" replaces the
        caller's (a copy: the caller's params are never mutated) and is routed
        back to *on_progress* until this call ends.

        asyncio.shield keeps wait_for's timeout from cancelling the future, so
        fut.done() still tells whether the child answered. The timeout arm and
        the cancel arm are the only places a notifications/cancelled is sent,
        always with the child's own id (never for initialize, and never for an
        already answered call). The finally pops the pending entry and the
        progress route on every exit path and retrieves a done future's
        exception (no "Future exception was never retrieved").

        Declared limit (Assumption 17): on Python 3.9, wait_for can swallow a
        cancellation that races the child's reply in the same loop iteration
        and return the reply instead. Harmless: a client may ignore a response
        that arrives after its cancel, and no notice is sent for an answered call.
        """
        child_id = self.slot.next_id
        self.slot.next_id += 1
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[child_id] = fut
        if on_progress is not None:
            token = "px:%s:%d" % (self.slot.spec.name, self.slot.next_token)
            self.slot.next_token += 1
            self._routes[child_id] = token
            self.slot.progress[token] = on_progress
            params = _with_progress_token(params, token)   # COPY, never mutate the caller's dict
        try:
            await self._send_line({"jsonrpc": "2.0", "id": child_id, "method": method, "params": params})
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(child_id, None)
            if method != "initialize":
                await self.notify_child("notifications/cancelled",
                                        {"requestId": child_id, "reason": "mcp-proxy: call timed out"})
            raise ChildUnavailable("child '%s' did not answer %s within %.0fs"
                                   % (self.slot.spec.name, method, timeout))
        except asyncio.CancelledError:
            answered = fut.done()
            self._pending.pop(child_id, None)
            if not answered and method != "initialize":
                await self.notify_child("notifications/cancelled",
                                        {"requestId": child_id, "reason": "upstream cancelled"})
            raise
        finally:
            self._pending.pop(child_id, None)              # every exit path, incl. _send_line raising (M2)
            if fut.done() and not fut.cancelled():
                fut.exception()                            # retrieve: no "Future exception was never retrieved"
            token = self._routes.pop(child_id, None)
            if token is not None:
                self.slot.progress.pop(token, None)

    async def _group_lingers(self, pgid: int) -> bool:
        """Poll the group up to the grace; True if a member is still there at the deadline."""
        deadline = asyncio.get_running_loop().time() + _SHUTDOWN_GRACE_S
        while _signal_group(pgid, 0):
            if asyncio.get_running_loop().time() >= deadline:
                return True
            await asyncio.sleep(0.05)
        return False                            # ESRCH (or EPERM): observed empty

    async def stop(self) -> None:
        """Stop this incarnation: close stdin, killpg TERM, killpg KILL, reap, then sweep the group.

        Safe on a dead, a live or a never-started incarnation, and more than
        once. There is NO "already stopped" early return on the ladder: every
        call walks it and each step is a no-op once its work is done. The whole
        body runs under _stop_lock, so concurrent callers never run two ladders.

        The group sweep runs once per incarnation after the leader exited and
        completes only when killpg reports ESRCH (or EPERM), which clears
        self.pgid; a later stop() with pgid None never signals (NFR-5). A
        CancelledError inside the sweep leaves pgid set, so the next stop()
        resumes from SIGTERM. Group members are not reaped: they are not the
        proxy's children. Declared limit (L9): the post-SIGKILL proc.wait() is
        unbounded for a leader in uninterruptible sleep.
        """
        async with self._stop_lock:
            name = self.slot.spec.name
            # 1. A deliberate stop is not a death: the EOF it causes makes _die a no-op.
            self.dead = True
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ChildUnavailable(f"child '{name}' stopped"))
            self._pending.clear()
            for token in list(self._routes.values()):
                self.slot.progress.pop(token, None)
            self._routes.clear()

            # 2. Never spawned: there is no group.
            proc = self.process
            if proc is None:
                return

            # 3. Reader cancel (P10), then close stdin.
            task = self._reader_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait({task})
            if proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except OSError:
                    pass

            # 4. Leader ladder (the leader is unreaped here, so pgid is set).
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), _SHUTDOWN_GRACE_S)
                except asyncio.TimeoutError:
                    if self.pgid is not None:
                        _signal_group(self.pgid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), _SHUTDOWN_GRACE_S)
                    except asyncio.TimeoutError:
                        if self.pgid is not None:
                            _signal_group(self.pgid, signal.SIGKILL)
                        await proc.wait()              # unbounded: declared limit L9

            # 5. Group sweep after the leader exited (NFR-5).
            pgid = self.pgid
            if pgid is not None:
                lingers = (_signal_group(pgid, signal.SIGTERM) and await self._group_lingers(pgid)
                           and _signal_group(pgid, signal.SIGKILL) and await self._group_lingers(pgid))
                if not lingers:
                    self.pgid = None                    # the sweep is complete; never signal this id again

            # 6. Structure only.
            log.debug("child=%s stopped rc=%s swept=%s", name, proc.returncode, self.pgid is None)


# ---------------------------------------------------------------------------
# Unit 4: ChildSlot -- one config entry across incarnations
# ---------------------------------------------------------------------------

class ChildSlot:
    """One config entry across incarnations: state, counters, tools, recovery.

    Constructed INSIDE the running loop, like ChildClient. Every field is
    loop-thread owned (NFR-8). `state` is "starting" | "ready" | "restarting"
    | "disabled". The child id and progress-token counters live here, not on
    the incarnation, so they are never reset by a restart (R-1).
    """

    def __init__(self, spec: ChildSpec, root: str, max_frame: int) -> None:
        self.spec = spec
        self.root = root
        self.max_frame = max_frame
        self.state = "starting"
        self.stopping = False                      # set by stop(); every later death is ignored
        self.client: Optional[ChildClient] = None
        self.tools: List[dict] = []
        self.tool_names: FrozenSet[str] = frozenset()
        self.next_id = 1
        self.next_token = 1
        self.progress: Dict[str, Callable[[dict], None]] = {}
        self.restarts: Deque[float] = collections.deque()   # loop.time() of each charged death
        self.restart_task: Optional[asyncio.Task] = None    # the ONE _restart_loop, or None
        # The incarnation _restart_loop is bringing up; cleared after the swap or
        # after its stop() completed (H1).
        self.pending_client: Optional[ChildClient] = None
        self.retry_at = 0.0
        self.init_params: dict = {}                # stored by start_initial, reused by restarts
        self.disabled_reason = ""                  # quoted by call()

    async def _bring_up(self, client: ChildClient, init_params: dict) -> List[dict]:
        """Spawn *client*, run the MCP handshake and list its tools.

        Shared by startup and restart; never touches self.client or self.state.
        A failure reply or a malformed initialize result is a ChildStartError;
        the refusal names the child and the error code, never a payload.
        """
        name = self.spec.name
        await client.start()
        resp = await client.rpc("initialize", init_params, self.spec.startup_timeout)
        if "error" in resp:
            err = resp.get("error")
            code = err.get("code") if isinstance(err, dict) else None
            raise ChildStartError(f"child '{name}': initialize failed: error reply (code {code})")
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise ChildStartError(f"child '{name}': initialize failed: no protocolVersion in the result")
        await client._send_line({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return await self._list_all_tools(client)

    async def start_initial(self, init_params: dict) -> None:
        """Bring the first incarnation up within the startup timeout, or refuse.

        self.client is set before the first await on the spawn, so a signal
        during startup can always reach this incarnation from its slot (L7).
        Deaths while "starting" are ignored by on_child_death: this coroutine
        owns them. Any failure stops the incarnation and becomes a
        ChildStartError naming the child; a CancelledError stops it and
        propagates unchanged (if that stop is itself cancelled, pgid stays set
        and McpServer.stop_children() resumes the sweep, NFR-5).
        """
        name = self.spec.name
        self.init_params = init_params
        client = ChildClient(self)
        self.client = client
        try:
            tools = await asyncio.wait_for(self._bring_up(client, init_params), self.spec.startup_timeout)
            if client.dead:
                raise ChildStartError(f"child '{name}': died right after listing its tools")
            # No await between the dead check above and the state change below.
            self.tools = tools
            self.tool_names = frozenset(tool["name"] for tool in tools)
            self.state = "ready"
        except asyncio.CancelledError:
            await client.stop()
            raise
        except ChildStartError:
            await client.stop()
            raise
        except asyncio.TimeoutError:
            await client.stop()
            raise ChildStartError(f"child '{name}': did not start within {self.spec.startup_timeout:g}s") from None
        except Exception as exc:
            await client.stop()
            raise ChildStartError(f"child '{name}': startup failed: {type(exc).__name__}: {exc}") from None
        log.debug("child=%s ready tools=%d", name, len(tools))

    async def _list_all_tools(self, client: ChildClient) -> List[dict]:
        """Follow tools/list's nextCursor to the end; keep the tool dicts verbatim.

        Each tool must be an object with a non-empty string name, unique within
        this child. A repeated cursor or more than _MAX_TOOL_PAGES pages is a
        cursor loop and refuses the start.
        """
        name = self.spec.name
        tools: List[dict] = []
        seen_names: Set[str] = set()
        seen_cursors: Set[str] = set()
        cursor: Optional[str] = None
        for _page in range(_MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            resp = await client.rpc("tools/list", params, self.spec.startup_timeout)
            if "error" in resp:
                err = resp.get("error")
                code = err.get("code") if isinstance(err, dict) else None
                raise ChildStartError(f"child '{name}': tools/list failed: error reply (code {code})")
            result = resp.get("result")
            page = result.get("tools") if isinstance(result, dict) else None
            if not isinstance(page, list):
                raise ChildStartError(f"child '{name}': tools/list result has no tools list")
            # The cursor is judged before the page's tools: a looping child repeats
            # its tools too, and the refusal must name the loop, not the repeat.
            next_cursor = result.get("nextCursor")
            if next_cursor is not None:
                if not isinstance(next_cursor, str):
                    raise ChildStartError(f"child '{name}': tools/list nextCursor is not a string")
                if next_cursor in seen_cursors:
                    raise ChildStartError(f"child '{name}': tools/list cursor loop (a cursor repeated)")
                seen_cursors.add(next_cursor)
            for tool in page:
                if not isinstance(tool, dict):
                    raise ChildStartError(f"child '{name}': tools/list returned a non-object tool")
                tool_name = tool.get("name")
                if not isinstance(tool_name, str) or not tool_name:
                    raise ChildStartError(f"child '{name}': tools/list returned a tool without a name")
                if tool_name in seen_names:
                    raise ChildStartError(f"child '{name}': tools/list returned tool {tool_name!r} twice")
                seen_names.add(tool_name)
                tools.append(tool)                 # verbatim: the same object, never a copy
            if next_cursor is None:
                log.debug("child=%s listed tools=%d pages=%d", name, len(tools), _page + 1)
                return tools
            cursor = next_cursor
        raise ChildStartError(f"child '{name}': tools/list cursor loop (more than {_MAX_TOOL_PAGES} pages)")

    def on_child_death(self, client: ChildClient, why: str) -> None:
        """The ONLY entry into recovery; it only ever starts it (sync, loop thread, no await).

        A death is ignored while stopping, for a stale incarnation, and in every
        state but "ready": "starting" is owned by start_initial, "restarting" by
        the running _restart_loop (which sees the failure through _bring_up
        raising or new.dead), "disabled" is final. The ready -> restarting
        transition happens once per death, so at most one restart loop exists
        per slot.
        """
        name = self.spec.name
        if self.stopping or client is not self.client or self.state != "ready":
            log.debug("child=%s death ignored (state=%s stopping=%s current=%s)",
                      name, self.state, self.stopping, client is self.client)
            return
        self.state = "restarting"
        log.warning("child=%s died (%s); restarting", name, why)
        self.restart_task = asyncio.ensure_future(self._restart_loop(client))
        self.restart_task.add_done_callback(_log_task_failure)

    def _charge_budget(self) -> bool:
        """Charge one death against the sliding window; True while within the budget.

        Called exactly once per death (M1): once for the death that started the
        restart loop, and once for each failed attempt inside it.
        """
        now = asyncio.get_running_loop().time()
        self.restarts.append(now)
        while self.restarts and now - self.restarts[0] > _RESTART_WINDOW_S:
            self.restarts.popleft()
        return len(self.restarts) <= _RESTART_BUDGET

    async def _restart_loop(self, old: ChildClient) -> None:
        """Own backoff, budget and every retry for one death (H1); never recurse.

        Ends with state "ready" or "disabled", or by self.stopping / a
        cancellation from stop(); never stuck in "restarting" by its own
        failure (M1). `new` is stopped on every exit path that did not swap it
        in; pending_client names it from before its first await until the swap
        or until its stop() returned, so a cancelled stop is resumed by
        ChildSlot.stop() (NFR-5). The startup tool descriptors are kept
        (declared limit: a schema-only change is not detected).
        """
        new: Optional[ChildClient] = None                  # the incarnation THIS loop must stop if not swapped in
        try:
            await old.stop()                               # reap the dead incarnation + sweep its group
            while True:
                if not self._charge_budget():              # this death exceeds the budget
                    self.state = "disabled"
                    self.disabled_reason = "%d crashes in %.0fs" % (len(self.restarts), _RESTART_WINDOW_S)
                    log.error("child=%s exceeded restart budget; disabled", self.spec.name)
                    return
                n = len(self.restarts)
                delay = _RESTART_BACKOFF_S[min(n - 1, len(_RESTART_BACKOFF_S) - 1)] * (1 + random.uniform(0, 0.2))
                self.retry_at = asyncio.get_running_loop().time() + delay
                await asyncio.sleep(delay)
                if self.stopping:                          # (a) stop() arrived during the backoff
                    return
                new = ChildClient(self)                    # FRESH incarnation, never reset in place
                self.pending_client = new                  # visible to ChildSlot.stop() from here on
                try:
                    tools = await asyncio.wait_for(self._bring_up(new, self.init_params),
                                                   self.spec.startup_timeout)
                except asyncio.CancelledError:
                    raise                                  # the finally below stops `new`
                except Exception as exc:                   # crash at init, timeout, bad listing
                    log.warning("child=%s restart attempt failed: %s", self.spec.name, type(exc).__name__)
                    await new.stop()
                    self.pending_client = new = None
                    continue                               # this failure is the next death: charged at the loop top
                if self.stopping:                          # (b) stop() arrived during _bring_up
                    return                                 # the finally below stops `new`
                if new.dead:                               # died after tools/list, before the swap
                    await new.stop()
                    self.pending_client = new = None
                    continue
                names = frozenset(t["name"] for t in tools)
                if names != self.tool_names:
                    self.state = "disabled"
                    self.disabled_reason = "tool set changed across restart"
                    log.error("child=%s tool set changed across restart (added=%s removed=%s); disabled",
                              self.spec.name, _log_value(sorted(names - self.tool_names)),
                              _log_value(sorted(self.tool_names - names)))
                    return                                 # the finally below stops `new`
                if self.stopping:                          # (c) right before the swap; no await since (b),
                    return                                 # kept so a future edit cannot open the window
                self.client = new                          # THE swap: same synchronous block as the
                self.state = "ready"                       # dead check above, no await in between
                self.pending_client = new = None           # swapped in: owned by self.client now
                log.warning("child=%s restarted", self.spec.name)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:                           # M1: an unexpected failure disables, never sticks
            self.state = "disabled"
            self.disabled_reason = "restart failed: %s" % type(exc).__name__
            log.error("child=%s restart loop failed: %s; disabled", self.spec.name, type(exc).__name__)
        finally:
            if new is not None:                            # every exit path that did not swap `new` in
                await new.stop()                           # cancelled here -> pending_client stays set and
                if self.pending_client is new:             # ChildSlot.stop() resumes this stop (NFR-5)
                    self.pending_client = None

    async def call(self, method: str, params: dict,
                   on_progress: Optional[Callable[[dict], None]]) -> dict:
        """Forward one request to the current incarnation, or refuse with ChildUnavailable.

        The refusal is decided on `state` before self.client is ever read, so a
        dead incarnation that has not been swapped out yet is never used.
        """
        name = self.spec.name
        if self.state == "restarting":
            wait = max(0.0, self.retry_at - asyncio.get_running_loop().time())
            raise ChildUnavailable(f"child '{name}' is restarting (retry in ~{wait:.0f}s); the "
                                   "interrupted call, if any, may or may not have taken effect")
        if self.state == "disabled":
            raise ChildUnavailable(f"child '{name}' is disabled ({self.disabled_reason}); restart mcp-proxy")
        if self.state != "ready" or self.client is None:
            raise ChildUnavailable(f"child '{name}' is starting")
        return await self.client.rpc(method, params, self.spec.call_timeout, on_progress)

    async def stop(self) -> None:
        """Stop the slot: the restart loop (bounded), then every incarnation it names.

        `stopping` is set FIRST, so every later death is ignored and
        _restart_loop returns at its next check. The wait on the cancelled
        restart task is bounded by _STOP_WAIT_S; on timeout the slot stops
        pending_client itself (serialized by its _stop_lock). Repeated stops are
        safe by the per-incarnation sweep state, not by an early return (NFR-5).
        The final "disabled" makes a call during shutdown answer "disabled".
        """
        self.stopping = True                               # FIRST: every later death is ignored, and
        task = self.restart_task                           # _restart_loop returns at its next check
        if task is not None and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=_STOP_WAIT_S)
            if not done:
                log.warning("child=%s restart loop still running after %.0fs; stopping its incarnation directly",
                            self.spec.name, _STOP_WAIT_S)
        for c in (self.pending_client, self.client):
            if c is not None:
                await c.stop()                             # serialized by c._stop_lock; resumes an interrupted sweep
        self.pending_client = None
        if self.state != "disabled":
            self.state = "disabled"
            self.disabled_reason = "mcp-proxy is shutting down"


# ---------------------------------------------------------------------------
# Unit 5: McpServer -- the router and the stdio front
# ---------------------------------------------------------------------------

class McpServer:
    """The router and the stdio front (JSON-RPC 2.0, one JSON object per line).

    Owns the slots (one per config entry, config order), the aggregated tool
    table and the tool -> slot route. Constructed INSIDE the running loop
    (_amain): its slots create asyncio primitives. Every field is loop-thread
    owned (NFR-8).
    """

    PROTOCOL_VERSION = "2024-11-05"
    SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION, "2025-03-26", "2025-06-18", "2025-11-25")

    def __init__(self, cfg: ProxyConfig) -> None:
        self.cfg = cfg
        self.slots: Dict[str, ChildSlot] = {c.name: ChildSlot(c, cfg.root, cfg.max_frame) for c in cfg.children}
        self.tools: List[dict] = []                # aggregated, config order
        self.route: Dict[str, ChildSlot] = {}      # tool name -> the slot that exposes it
        self.sessions: Dict[str, "HttpSession"] = {}
        self.http: Optional["HttpSettings"] = None
        self._http_tasks: Set[asyncio.Task] = set()   # strong refs to every HTTP request task (L5)
        self._http_closing = False                      # set first in serve_http's finally (M4 round 4)

    def _child_init_params(self) -> dict:
        """The initialize params sent to every child (read, never restated: P4)."""
        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-proxy", "version": "1.0.0"},
        }

    async def start_children(self) -> None:
        """Start every child concurrently, then build the tool table in config order.

        Any failure -- including a CancelledError returned as a value by one
        child's coroutine (L2) -- stops every child and raises ChildStartError
        naming every failed child. A tool name exposed by two children is a
        refusal, never a precedence rule (ADR 0015). On any BaseException from
        the gather itself (a startup signal included) every child is stopped
        before re-raising, so no child is left behind.
        """
        try:
            results = await asyncio.gather(
                *(slot.start_initial(self._child_init_params()) for slot in self.slots.values()),
                return_exceptions=True,
            )
        except BaseException:
            await self.stop_children()
            raise
        failed = [(name, r) for name, r in zip(self.slots, results) if isinstance(r, BaseException)]
        if failed:
            await self.stop_children()
            raise ChildStartError("; ".join(
                str(r) if isinstance(r, ChildStartError) else f"child '{name}': startup failed: {type(r).__name__}"
                for name, r in failed))
        for slot in self.slots.values():
            for tool in slot.tools:
                tool_name = tool["name"]
                other = self.route.get(tool_name)
                if other is not None:
                    await self.stop_children()
                    raise ChildStartError(
                        "tool %r is exposed by both '%s' and '%s'; refusing to start (ambiguity is the defect, ADR 0015)"
                        % (tool_name, other.spec.name, slot.spec.name))
                self.route[tool_name] = slot
                self.tools.append(tool)
        log.debug("children ready=%d tools=%d", len(self.slots), len(self.tools))

    async def stop_children(self) -> None:
        """Stop every slot concurrently; safe to repeat (each slot's stop is resumable)."""
        await asyncio.gather(*(slot.stop() for slot in self.slots.values()), return_exceptions=True)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting, children=%d", len(self.slots))
        # One task per request, and a reader executor of its OWN, on purpose
        # (ADR 0008). A loop that awaited the handler on the same line of
        # control it later awaited the readline would not READ for the whole
        # duration of one call: every other request would sit unread in the
        # pipe, time out client-side and be answered against an id the client
        # had already abandoned. From the caller's chair that is a dead server.
        #
        # The executor must be dedicated: a readline parked on the DEFAULT
        # executor competes with anything else parked there.
        #
        # Why concurrent dispatch is safe HERE: every handler is a coroutine
        # (a child call is an await on a pipe, never a blocking call), and every
        # child's ids / _pending / progress routes and the tool table are
        # touched only on the loop thread. There is no worker pool on purpose:
        # this reader is the ONE executor in the file.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proxy-stdin")
        inflight: set = set()
        by_id: dict = {}  # R-0006: request id -> its in-flight task
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed/detached stdin raises rather than returning "";
                    # unguarded it escaped run() as a traceback.
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = _strict_loads(line)
                except (ValueError, RecursionError) as exc:
                    # Answering is not optional: a bare `continue` would leave
                    # the caller's id unanswered until it timed out, which is
                    # indistinguishable from a hung server. ValueError covers
                    # JSONDecodeError, which is also how _strict_loads refuses
                    # NaN/Infinity and an integer literal over
                    # JSON_INT_LITERAL_LIMIT characters on every Python (R-0067,
                    # R-0068: the CVE-2020-10735 int-digit limit exists only
                    # from 3.9.14); RecursionError a deeply nested line --
                    # unguarded, either escaped run().
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    # `5` is valid JSON. Unguarded it would reach msg.get()
                    # below and take the process down with an AttributeError
                    # escaping run(). This guard is also what lets the debug
                    # log and _serve treat msg as a dict unconditionally.
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue
                if _bad_request_id(msg):
                    # R3-F1/F2: an id that is not a string or an integer is
                    # never dispatched and never echoed -- a deep list id made
                    # the reply's json.dumps raise, and so did its fallback.
                    log.warning("Request id was %s, not a string or an integer",
                                type(msg["id"]).__name__)
                    self._write(self._error(None, -32600, _BAD_ID_MESSAGE))
                    continue

                # F12/CWE-532: log protocol structure only, never payload
                # values (arguments and results can carry file contents).
                # Defensive: params/arguments may be a non-dict on a malformed
                # message; this is a debug log and must never crash the loop.
                # F6: nothing is built unless DEBUG is on.
                if log.isEnabledFor(logging.DEBUG):
                    _p = msg.get("params")
                    _p = _p if isinstance(_p, dict) else {}
                    _args = _p.get("arguments")
                    _args = _args if isinstance(_args, dict) else {}
                    log.debug(
                        "← method=%s id=%s fn=%s keys=%s",
                        _log_value(msg.get("method")), _log_value(msg.get("id")),
                        _log_value(_p.get("name")), _log_value(list(_args.keys())),
                    )
                # R-0006: a cancel is handled HERE, on the loop thread and
                # before anything is dispatched -- never as a task of its own.
                # It is a notification, so nothing is written back.
                if (msg.get("method") == "notifications/cancelled"
                        and msg.get("id") is None):
                    self._cancel_request(msg.get("params"), by_id)
                    continue
                task = loop.create_task(self._serve(msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                self._track_request(msg, task, by_id)
        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            log.info("MCP server shutting down")

    # R-0006: notifications/cancelled. Hand-written in every server on purpose
    # -- ADR 0012 keeps the transport tier per server -- and held to one shape
    # by tests/test_cancel.py and the smoke harness's cancel checks.
    @staticmethod
    def _request_key(value):
        """`value` as an in-flight registry key, or None if it cannot be one.

        A JSON-RPC id is a string or an integer. A bool is refused although
        Python calls it an int: True == 1 and they hash alike, so accepting it
        would let `requestId: true` cancel request 1. A float is refused for
        the same reason (1.0 == 1), and anything else is not an id.
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, (str, int)):
            return value
        return None

    def _track_request(self, msg: dict, task, by_id: dict) -> None:
        """Register `task` under its request id; forget it once it is done.

        `initialize` is never registered, so the handshake cannot be cancelled.
        """
        if msg.get("method") == "initialize":
            return
        key = self._request_key(msg.get("id"))
        if key is None:
            return
        by_id[key] = task

        def _forget(done) -> None:
            # Only while the slot is still THIS task: a client that reused the
            # id for a newer request owns it now.
            if by_id.get(key) is done:
                del by_id[key]

        task.add_done_callback(_forget)

    def _cancel_request(self, params, by_id: dict) -> None:
        """notifications/cancelled, on the loop thread. Never replies.

        An unknown, finished or malformed requestId is ignored silently: the
        request may simply have finished before the notice arrived.
        """
        if not isinstance(params, dict):
            return
        key = self._request_key(params.get("requestId"))
        if key is None:
            return
        task = by_id.get(key)
        if task is not None:
            log.debug("cancelling id=%s", _log_value(key))
            task.cancel()

    async def _serve(self, msg: dict, sink: Optional[Callable[[dict], None]] = None) -> None:
        """One request, from dispatch to written reply. Runs as its own task.

        *sink* receives the response and any progress notification for it:
        self._write on stdio, a bounded queue sink on HTTP.
        """
        sink = sink or self._write
        try:
            response = await self._handle_message(msg, sink)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}: {exc}",
            )
        if response is not None:
            sink(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message (a response or a progress notification).

        Called only from the event-loop thread: _serve and the progress
        callbacks run on the loop, so two messages cannot interleave on stdout
        and this needs no lock.
        """
        try:
            out = _strict_dumps(response)
        except (TypeError, ValueError, RecursionError) as exc:   # F4: a too-deep reply too
            # A non-serialisable payload must not kill the loop mid-write, i.e.
            # after some bytes were already on the wire.
            log.exception("Response was not JSON-serialisable")
            out = _strict_dumps(self._error(_fallback_id(response.get("id")), -32603,
                                            f"Response not serialisable: {exc}"))
        # F12/CWE-532: structure only (id + outcome), no body.
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "→ id=%s %s", _log_value(response.get("id")),
                "error" if "error" in response else "ok",
            )
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            # Unguarded, a client that hung up mid-reply escaped run().
            log.warning("stdout write failed: %s", exc)

    async def _handle_message(self, msg: dict, sink) -> Optional[dict]:
        """Route one request. A notification (no id) gets no response.

        notifications/cancelled never reaches here: it is handled on the loop
        thread before dispatch (run, _cancel_request).
        """
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        # Notifications (no id) -- no response
        if msg_id is None:
            log.debug("Notification: %s", _log_value(method))
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-proxy", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            # The aggregated table, config order; never paginated upstream.
            return self._result(msg_id, {"tools": self.tools})

        if method == "tools/call":
            return await self._handle_tool_call(msg_id, params, sink)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    async def _handle_tool_call(self, msg_id: Any, params: dict, sink) -> dict:
        """Forward one tools/call to the child that exposes the tool; relay its reply verbatim.

        A child that cannot be reached, or any failure while forwarding, is a
        tool result with isError set (ADR 0010, layer W) -- never a JSON-RPC
        error. A child's own JSON-RPC error is relayed as-is.
        """
        # Deliberately OUTSIDE the try (P5, R-19): a non-dict params raises
        # here and becomes _serve's -32603, a protocol error, not a tool result.
        name = params.get("name", "")
        slot = self.route.get(name)
        if slot is None:
            return self._error(msg_id, -32602, f"Unknown tool: {name}")

        meta = params.get("_meta")
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        if self._request_key(token) is not None:
            def on_progress(p: dict, _t=token) -> None:
                # The child saw the proxy's own token; the caller gets ITS token back.
                sink({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": dict(p, progressToken=_t)})
        else:
            on_progress = None
            if isinstance(meta, dict) and "progressToken" in meta:
                # M3: an unroutable upstream token (bool, float, dict, null)
                # never reaches a child -- it would emit progress nobody routes.
                params = _with_progress_token(params, None)

        try:
            reply = await slot.call("tools/call", params, on_progress)
        except ChildUnavailable as exc:
            return self._result(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:
            log.exception("Unhandled exception forwarding a tool call to child %s", slot.spec.name)
            return self._result(msg_id, {"content": [{"type": "text",
                "text": f"mcp-proxy internal error: {type(exc).__name__}: {exc}"}], "isError": True})
        if "error" in reply:
            return {"jsonrpc": "2.0", "id": msg_id, "error": reply["error"]}   # verbatim relay
        return self._result(msg_id, reply.get("result"))                    # verbatim relay

    # -- Step 11: the HTTP loop side. Every method below runs on the loop thread
    # (NFR-8); handler threads reach them only through _bridge or
    # call_soon_threadsafe.

    def _http_drop(self, session: "HttpSession") -> None:
        """The ONE session close path (L1): DELETE, idle eviction and shutdown.

        Cancels every in-flight request of the session and marks it closed, so
        a handler that looked it up just before gets _SESSION_GONE -> 404.
        """
        for task in list(session.by_id.values()):
            task.cancel()
        session.closed = True
        self.sessions.pop(session.sid, None)
        log.info("session %s... closed", session.sid[:6])

    async def _http_new_session(self) -> Optional["HttpSession"]:
        """Evict idle sessions, then open a new one; None once --max-sessions is reached."""
        now = time.monotonic()
        # A snapshot (M1 round 4): _http_drop pops from self.sessions.
        for s in list(self.sessions.values()):
            if now - s.last_seen > self.http.session_idle:
                self._http_drop(s)
        if len(self.sessions) >= self.http.max_sessions:
            return None
        sid = secrets.token_urlsafe(32)
        session = HttpSession(sid)
        self.sessions[sid] = session
        log.info("session %s... opened", sid[:6])
        return session

    async def _http_session(self, sid: str) -> Optional["HttpSession"]:
        """The live session for *sid* (its last_seen touched), or None."""
        session = self.sessions.get(sid)
        if session is None or session.closed:
            return None
        session.last_seen = time.monotonic()
        return session

    def _http_start(self, session: "HttpSession", msg: dict, sink: Callable[[Any], None]) -> None:
        """Create AND register one HTTP request task in one loop callback (L7).

        Every exit posts exactly one first item (_SESSION_GONE, _BUSY, None or
        _REQUEST_STARTED) synchronously, and every exit posts the None sentinel.
        """
        if self._http_closing:                       # M4 round 4: shutdown began; start nothing new
            sink(None)                               # first item None -> the handler answers 503
            return
        if session.closed:                           # L6: closed between the handler's lookup and here
            sink(_SESSION_GONE)                      # first item -> the handler answers 404
            sink(None)
            return
        if len(self._http_tasks) >= self.http.max_inflight:   # R-34, CWE-770: calls outlive
            log.warning("http request refused: %d in flight", len(self._http_tasks))  # connections
            sink(_BUSY)                              # first item -> the handler answers 503 + close
            sink(None)
            return
        try:
            task = asyncio.ensure_future(self._serve(msg, sink))   # the SAME _serve (P3 with a sink)
        except Exception as exc:                     # L6: the handler thread must never wait forever
            log.warning("http request not started: %s", type(exc).__name__)
            sink(None)                               # first item None -> the handler answers 503
            return
        self._http_tasks.add(task)                   # L5: strong ref (MCP_SKELETON.md §5 item 2)
        task.add_done_callback(self._http_tasks.discard)
        self._track_request(msg, task, session.by_id)   # same callback as the creation (L7)
        sink(_REQUEST_STARTED)                       # first item: SSE headers may go out now; the
                                                     # task has not run a step yet, so nothing precedes it
        task.add_done_callback(lambda _t: sink(None))   # end sentinel, even if cancelled before step 1
        task.add_done_callback(_log_task_failure)       # M4

    async def _http_close(self, sid: str) -> bool:
        """DELETE: close the session (L1). False if *sid* is unknown."""
        session = self.sessions.get(sid)
        if session is None:
            return False
        self._http_drop(session)
        return True

    def _http_write_ready_file(self, path: str, port: int) -> None:
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

    @staticmethod
    def _http_remove_ready_file(path: str) -> None:
        """On an ordered shutdown, unlink the ready file only while it still holds
        this process's pid (R-0075, the router's V24).

        Read without following a symlink (O_NOFOLLOW: a link at the path is
        refused, so neither it nor its target is touched); a file another writer
        has replaced (another pid, not JSON, unreadable) is left alone. Best
        effort: never raises; a refusal is logged at DEBUG by type only.

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
                log.debug("ready file left: not this pid")
        except (OSError, ValueError) as exc:
            log.debug("ready file left: %s", type(exc).__name__)

    async def serve_http(self, settings: "HttpSettings", srv: "_ProxyHttpServer") -> None:
        """Serve the bound listener until cancelled, then shut down in order (H1, round 3).

        The finally's order is what prevents the deadlock: refuse new work,
        end every request (each sentinel wakes its handler) while the loop
        still runs, and only then stop the listener -- on a stopper thread the
        loop polls with a bound, never on the loop thread itself.
        """
        self.http = settings                         # FIRST: before any handler thread can exist
        loop = asyncio.get_running_loop()
        srv.core = self
        srv.loop = loop
        threading.Thread(target=srv.serve_forever, name="proxy-http", daemon=True).start()
        try:
            if settings.ready_file:
                self._http_write_ready_file(settings.ready_file, srv.server_port)
            await asyncio.Event().wait()             # ends only by cancellation
        finally:
            # 1. Refuse new work: _http_start posts None (-> 503); handlers stop waiting.
            self._http_closing = True
            srv.closing.set()
            # 2. End every request while the loop can still serve in-flight bridges.
            for s in list(self.sessions.values()):   # M1 round 4: _http_drop pops
                self._http_drop(s)
            for task in list(self._http_tasks):
                task.cancel()
            if self._http_tasks:
                await asyncio.wait(set(self._http_tasks), timeout=_SHUTDOWN_GRACE_S)
            # 3. Stop the listener off the loop thread; poll it with a bound.
            stopper = threading.Thread(target=_stop_http_server, args=(srv,),
                                       name="proxy-http-stop", daemon=True)
            stopper.start()
            deadline = loop.time() + _HTTP_BRIDGE_TIMEOUT_S
            while stopper.is_alive() and loop.time() < deadline:
                await asyncio.sleep(0.05)
            if stopper.is_alive():
                log.warning("http listener did not stop within %.0fs", _HTTP_BRIDGE_TIMEOUT_S)
            # 4. Remove our ready file (R-0075): only while it holds our pid,
            #    never through a symlink. SIGKILL skips this and leaves it.
            if settings.ready_file:
                self._http_remove_ready_file(settings.ready_file)

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
    # BEGIN GENERATED: _mcp_json.py :: _result, _error
    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    # END GENERATED: 0a5c31c9ccd8


# ---------------------------------------------------------------------------
# Units 6-7: the HTTP front -- session record, listener, request handler
# ---------------------------------------------------------------------------

class HttpSession:
    """One Streamable-HTTP session. Loop-thread owned (NFR-8): handler threads
    reach it only through the loop (_bridge / call_soon_threadsafe)."""

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.created = self.last_seen = time.monotonic()
        self.by_id: dict = {}   # request id -> its in-flight task (the per-session cancel registry)
        self.closed = False


class _Closing(Exception):
    """A handler-thread wait on the loop gave up: shutdown began, the loop is
    gone, or the bounded bridge timed out. Every do_* answers it with 503."""


def _bounded_sink(q: "queue.Queue") -> Callable[[Any], None]:
    """The HTTP sink for one request: puts items on *q* (called on the loop thread only).

    The response (a dict with an "id"), the sentinel None and the first-item
    markers are never dropped. A progress notification is dropped (newest
    first) once the queue holds _HTTP_SINK_PROGRESS_CAP items: progress is
    advisory and cumulative, so a later item supersedes a dropped one (R-27).
    *q* is unbounded, so a put never blocks the loop.
    """
    dropped = [0]

    def sink(item: Any) -> None:
        if (item is None or item is _SESSION_GONE or item is _BUSY or item is _REQUEST_STARTED
                or (isinstance(item, dict) and "id" in item)):
            q.put(item)
            return
        if q.qsize() >= _HTTP_SINK_PROGRESS_CAP:
            dropped[0] += 1
            if dropped[0] == 1:
                log.debug("http progress dropped: queue at cap")
            return
        q.put(item)

    return sink


def _stop_http_server(srv: "_ProxyHttpServer") -> None:
    """Stop the listener. Runs on the proxy-http-stop thread, never on the loop:
    shutdown() blocks until serve_forever returns."""
    srv.shutdown()
    srv.server_close()


class _ProxyHttpServer(ThreadingHTTPServer):
    """The --http listener: one daemon thread per connection, at most
    settings.max_connections of them (CWE-400); never joined on close (H1)."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, handler, settings: HttpSettings) -> None:
        # Everything a handler reads is set BEFORE super().__init__, which
        # binds and listens (L4).
        self.settings = settings
        self.conn_sem = threading.BoundedSemaphore(settings.max_connections)
        self.closing = threading.Event()
        self.loopback = ipaddress.IPv4Address(settings.bind).is_loopback
        self.core: Optional["McpServer"] = None                     # set by serve_http
        self.loop: Optional[asyncio.AbstractEventLoop] = None       # set by serve_http
        super().__init__(address, handler)

    def server_bind(self) -> None:
        # The stdlib HTTPServer.server_bind does a reverse-DNS getfqdn() the
        # proxy never uses and that can stall startup (I1).
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
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.conn_sem.release()

    def handle_error(self, request, client_address) -> None:
        # Type only: the stdlib traceback's message text can quote request data (ADR 0011).
        log.warning("http handler failed: %s", type(sys.exc_info()[1]).__name__)


class _HeaderDeadlineReader(io.RawIOBase):
    """The raw reader under a handler's rfile: enforces a TOTAL header deadline.

    While `deadline` (a time.monotonic() value) is set, every recv gets at most
    the time left until it, and none is attempted once it has passed: a client
    that trickles one header byte per recv can no longer hold its
    --max-connections slot past _HTTP_HEADER_TIMEOUT_S in total (F8). The
    socket.timeout raised is the one the stdlib's handle_one_request already
    answers by closing the connection. With `deadline` None (the body, after the
    header phase) it is a plain recv under the socket's own timeout.
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
            self._sock.settimeout(min(left, _HTTP_HEADER_TIMEOUT_S))
        return self._sock.recv_into(buf)


class _ProxyHttpHandler(BaseHTTPRequestHandler):
    """Streamable HTTP: POST /mcp carries JSON-RPC, DELETE /mcp ends a session.

    Runs on a listener thread: it owns only its socket and its sink queue and
    touches loop state solely through _bridge / call_soon_threadsafe (NFR-8).
    """

    protocol_version = "HTTP/1.1"
    server_version = "mcp-proxy"
    sys_version = ""
    # Applied by StreamRequestHandler.setup(): a connection that stalls in the
    # request line or headers is closed by the stdlib (CWE-400, NFR-4).
    timeout = _HTTP_HEADER_TIMEOUT_S

    def setup(self) -> None:
        super().setup()
        # Replace the stdlib's rfile with one whose raw reader enforces the total
        # header deadline. The stdlib's own is closed at once: an unclosed
        # makefile keeps the socket's fd alive after close_request.
        stock = self.rfile
        self._header_reader = _HeaderDeadlineReader(self.connection)
        self.rfile = io.BufferedReader(self._header_reader, io.DEFAULT_BUFFER_SIZE)
        stock.close()

    def handle_one_request(self) -> None:
        # Every keep-alive request starts in the header phase again: request
        # line + headers within _HTTP_HEADER_TIMEOUT_S in TOTAL (F8).
        self._header_reader.deadline = time.monotonic() + _HTTP_HEADER_TIMEOUT_S
        self.connection.settimeout(_HTTP_HEADER_TIMEOUT_S)
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
        # Called from parse_request, before any auth: send nothing (R-0076, the
        # router's V37). _post sends the 100 Continue itself once _precheck and
        # the media-type and framing checks pass.
        return True

    def log_message(self, format, *args) -> None:  # noqa: A002 -- stdlib signature
        # Structure only (ADR 0011): never the stdlib's `format % args` text,
        # which can quote the raw request line; never headers, query or body.
        # Called before command/path exist on a timeout or a malformed line.
        if log.isEnabledFor(logging.DEBUG):
            path_only = (getattr(self, "path", "") or "").split("?", 1)[0]
            log.debug("http %s %s", _log_value(getattr(self, "command", None) or "-"), _log_value(path_only))

    def _bridge(self, fn, *args):
        """Run the coroutine fn(*args) on the loop; wait at most _HTTP_BRIDGE_TIMEOUT_S (H1)."""
        if self.server.closing.is_set():
            raise _Closing()   # no coroutine object is created, so none is left un-awaited
        coro = fn(*args)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self.server.loop)
        except RuntimeError:   # the loop is closed
            coro.close()
            raise _Closing()
        try:
            return fut.result(timeout=_HTTP_BRIDGE_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise _Closing()
        except concurrent.futures.CancelledError:   # the loop cancelled it on the way down
            raise _Closing()
        except Exception as exc:   # G2: the coroutine itself raised; do_* catches only OSError
            # Type only, like _ProxyHttpServer.handle_error: the text can quote request data.
            log.warning("http bridge failed: %s", type(exc).__name__)
            raise _Closing() from None

    def _refuse(self, status: int, extra_headers=(), body: bytes = b"") -> None:
        """Answer and close: used for every response sent before the body is
        consumed, so unread body bytes are never parsed as the next request.

        A 401 or 403 is logged at WARNING with the status and the peer address
        only (F41) -- never a header value, never the presented token.

        HTTP/0.9 guard (the llm-router's M1, R-0069): parse_request sets
        request_version to "HTTP/0.9" before it parses the version word (and
        keeps it for a bare two-word `GET /`, which reaches do_GET), and
        send_response_only writes no status line and no header for HTTP/0.9.
        Every refusal is therefore sent with an HTTP/1.1 head.
        """
        if getattr(self, "request_version", None) == "HTTP/0.9":
            self.request_version = "HTTP/1.1"
        self.send_response(status)
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True
        if status in (401, 403):
            log.warning("http refused status=%d peer=%s", status, self.client_address[0])
        else:
            log.debug("-> http id=%s %s status=%d", None, "refused", status)

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        """The stdlib's own refusals (400/414/431/501/505, and any other it
        routes here) as a fixed, body-less answer (R-0069): `message` and
        `explain` are ignored, since they can quote the request line, the
        method or a header; the reason phrase is the fixed one send_response
        takes from the status. nosniff and no-store are added here;
        Content-Length 0, Connection: close and the HTTP/0.9 guard come from _refuse.
        """
        self.close_connection = True
        self._refuse(code, _HTTP_STDLIB_REFUSAL_HEADERS)

    def _single_header(self, name: str):
        """(ok, value) of a header that must appear at most once (ADR 0015:
        ambiguity is the defect): ok False when it is repeated, value None when absent."""
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            return False, None
        return True, (values[0] if values else None)

    def _precheck(self) -> bool:
        """Checks 0-4 shared by every method: header syntax, path, Host, Origin, bearer.

        Answers the refusal itself and returns False on the first failure. On
        success the header phase ends: the socket timeout is raised.
        """
        settings = self.server.settings
        # 0. a malformed header line (F1, CWE-444): http.client.parse_headers stops
        # there and records a defect, so every later header -- a second
        # Content-Type, Transfer-Encoding, Content-Length -- would escape the checks.
        if self.headers.defects:
            self._refuse(400)
            return False
        # 1. path
        if (self.path or "").split("?", 1)[0] != settings.path:
            self._refuse(404)
            return False
        # 2. Host (loopback bind only; the listener is IPv4-only, so no [::1] form)
        if self.server.loopback:
            hosts = self.headers.get_all("Host") or []
            port = self.server.server_port
            if len(hosts) != 1 or hosts[0].strip().lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self._refuse(403)
                return False
        # 3. Origin: absent is accepted (A-6); present must be allowed
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (origins and origins[0].strip() not in settings.origins):
            self._refuse(403)
            return False
        # 4. bearer token, compared in constant time; the body is never read on failure
        auths = self.headers.get_all("Authorization") or []
        presented = b""
        if len(auths) == 1 and auths[0].startswith("Bearer "):
            presented = auths[0][len("Bearer "):].strip().encode("ascii", "replace")
        if not hmac.compare_digest(presented, settings.token):
            self._refuse(401, [("WWW-Authenticate", "Bearer")])
            return False
        self.connection.settimeout(_HTTP_SOCKET_TIMEOUT_S)
        return True

    def _refuse_405(self) -> None:
        if self._precheck():
            self._refuse(405, [("Allow", "POST, DELETE")])

    def do_GET(self) -> None:  # noqa: N802 -- stdlib dispatch name
        self._refuse_405()

    def do_PUT(self) -> None:  # noqa: N802 -- stdlib dispatch name
        self._refuse_405()

    def do_PATCH(self) -> None:  # noqa: N802 -- stdlib dispatch name
        self._refuse_405()

    def _send_json(self, status: int, obj: Optional[dict], extra_headers=(), close: bool = False) -> None:
        """One complete JSON answer (or an empty one when *obj* is None), after the body was read."""
        body = b""
        if obj is not None:
            try:
                body = _strict_dumps(obj).encode("utf-8")
            except (TypeError, ValueError, RecursionError) as exc:   # F4: a too-deep reply too
                log.warning("http response was not JSON-serialisable: %s", type(exc).__name__)
                body = _strict_dumps(McpServer._error(_fallback_id(obj.get("id")), -32603,
                                                      "Response not serialisable")).encode("utf-8")
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("-> http id=%s %s status=%d", _log_value(obj.get("id")) if obj is not None else None,
                      "error" if obj is not None and "error" in obj else "ok", status)

    def _refuse_orphan(self, session: "HttpSession", is_init: bool) -> None:
        """503 for a request that was not started; for an initialize, also drop the
        session it already opened (G1): no client ever learns its id, so it would
        hold a --max-sessions slot until --session-idle evicted it. The drop runs
        on the loop through the ONE close path (_http_close -> _http_drop)."""
        if is_init:
            try:
                self._bridge(self.server.core._http_close, session.sid)
            except _Closing:
                pass   # shutdown drops every session anyway; a stalled loop is the declared limit
        self._refuse(503)

    def _disconnected(self) -> None:
        """A write failed (OSError, L2 round 4): the client left. The call is NOT cancelled (R-16)."""
        log.info("client disconnected; call continues")
        self.close_connection = True

    def do_POST(self) -> None:  # noqa: N802 -- stdlib dispatch name
        """Checks 0-12 (first failure wins; 8a is the id shape), then the request in SSE or JSON mode."""
        try:
            self._post()
        except OSError:
            # Any write (or the body read) failing means the client is gone.
            self._disconnected()

    def _post(self) -> None:
        # 0-4. header syntax, path, Host, Origin, bearer
        if not self._precheck():
            return
        settings = self.server.settings
        core = self.server.core
        loop = self.server.loop
        # 5. Content-Type: exactly once, and the media type (before any ';'
        # parameter) is exactly application/json -- never a prefix match (F28)
        ok, ctype = self._single_header("Content-Type")
        if not ok or (ctype or "").split(";", 1)[0].strip().lower() != "application/json":
            self._refuse(415)
            return
        # 6. framing: Content-Length only, within --max-body-bytes
        if "Transfer-Encoding" in self.headers:
            self._refuse(411)
            return
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,19}", lengths[0].strip()):
            self._refuse(411)
            return
        length = int(lengths[0].strip())
        if length > settings.max_body:
            self._refuse(413)
            return
        # 6a. the interim answer handle_expect_100 withheld, now that the bearer,
        # Origin/Host, media type and framing passed (the stdlib's condition,
        # minus its timing; R-0076). Any other Expect value is ignored, as the
        # router does. The version test keeps an HTTP/0.9 head out of it.
        if (self.headers.get("Expect") or "").strip().lower() == "100-continue" \
                and self.request_version >= "HTTP/1.1":
            self.send_response_only(100)
            self.end_headers()
            self.wfile.flush()
        # 7. read exactly Content-Length bytes; parse
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            self.close_connection = True   # short body: the peer is gone or lying
            return
        try:
            msg = _strict_loads(raw.decode("utf-8"))
        # F16: deep nesting is a parse error. ValueError covers JSONDecodeError,
        # which is also how _strict_loads refuses NaN/Infinity and an integer
        # literal over JSON_INT_LITERAL_LIMIT characters on every Python
        # (R-0067, R-0068; the int-digit limit exists only from 3.9.14).
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._send_json(400, McpServer._error(None, -32700, "Parse error"))
            return
        # 8. one JSON-RPC object
        if isinstance(msg, list):
            self._send_json(400, McpServer._error(None, -32600, "Invalid Request: batches are not supported"))
            return
        if not isinstance(msg, dict):
            self._send_json(400, McpServer._error(
                None, -32600, f"Invalid Request: expected a JSON object, got {type(msg).__name__}"))
            return
        # 8a. a request id is a string or an integer (R3-F1/F2), checked before
        # any session is created or looked up, and never echoed
        if _bad_request_id(msg):
            self._send_json(400, McpServer._error(None, -32600, _BAD_ID_MESSAGE))
            return
        # Structure only (ADR 0011): the P1 field set, never a value; nothing
        # is built unless DEBUG is on (F6).
        method = msg.get("method")
        if log.isEnabledFor(logging.DEBUG):
            _p = msg.get("params")
            _p = _p if isinstance(_p, dict) else {}
            _args = _p.get("arguments")
            _args = _args if isinstance(_args, dict) else {}
            log.debug("<- http method=%s id=%s fn=%s keys=%s",
                      _log_value(method), _log_value(msg.get("id")), _log_value(_p.get("name")),
                      _log_value(list(_args.keys())))
        # 9. session: Mcp-Session-Id and MCP-Protocol-Version at most once (F28)
        ok_sid, sid = self._single_header("Mcp-Session-Id")
        ok_version, version = self._single_header("MCP-Protocol-Version")
        if not ok_sid or not ok_version:
            self._send_json(400, McpServer._error(msg.get("id"), -32600,
                                                  "duplicate Mcp-Session-Id or MCP-Protocol-Version"))
            return
        is_init = method == "initialize"
        try:
            if is_init:
                # F7: an initialize must be a request. Without an id it would take
                # step 11's notification branch AFTER opening a session no client
                # learns of, holding a --max-sessions slot until --session-idle.
                if msg.get("id") is None:
                    self._send_json(400, McpServer._error(None, -32600,
                                                          "Invalid Request: initialize must carry an id"))
                    return
                if sid:
                    self._send_json(400, McpServer._error(msg.get("id"), -32600,
                                                          "initialize must not carry Mcp-Session-Id"))
                    return
                session = self._bridge(core._http_new_session)
                if session is None:
                    self._refuse(503)
                    return
            else:
                if not sid:
                    self._send_json(400, McpServer._error(msg.get("id"), -32600, "missing Mcp-Session-Id"))
                    return
                session = self._bridge(core._http_session, sid)
                if session is None:
                    self._send_json(404, McpServer._error(msg.get("id"), -32001, "Session not found"))
                    return
        except _Closing:
            self._refuse(503)
            return
        # 10. protocol version (non-initialize)
        if not is_init:
            if (version or _ASSUMED_HEADER_VERSION) not in core.SUPPORTED_PROTOCOL_VERSIONS:
                self._send_json(400, McpServer._error(msg.get("id"), -32600,
                                                      "Unsupported MCP-Protocol-Version"))
                return
        # 11. notification or client response
        if msg.get("id") is None or "method" not in msg:
            if method == "notifications/cancelled" and not self.server.closing.is_set():
                try:
                    loop.call_soon_threadsafe(core._cancel_request, msg.get("params"), session.by_id)
                except RuntimeError:   # the loop is closed
                    pass
            else:
                log.debug("http notification dropped: method=%s", _log_value(method))
            self._send_json(202, None)
            return
        # 12. request
        if self.server.closing.is_set():
            self._refuse_orphan(session, is_init)
            return
        sink_q: "queue.Queue" = queue.Queue()
        sink = _bounded_sink(sink_q)
        is_call = method == "tools/call"   # the ONLY mode switch
        try:
            loop.call_soon_threadsafe(core._http_start, session, msg, sink)
        except RuntimeError:
            self._refuse_orphan(session, is_init)
            return
        try:
            first = sink_q.get(timeout=_HTTP_BRIDGE_TIMEOUT_S)
        except queue.Empty:
            first = None
        if first is _SESSION_GONE:
            self._send_json(404, McpServer._error(msg.get("id"), -32001, "Session not found"))
            return
        if first is not _REQUEST_STARTED:   # _BUSY, None (shutdown / not started) or a timeout
            self._refuse_orphan(session, is_init)
            return
        if is_call:
            self._stream_sse(sink_q)
        else:
            self._answer_json(sink_q, session.sid if is_init else None)

    def _stream_sse(self, sink_q: "queue.Queue") -> None:
        """SSE mode (every tools/call): headers now, then each item as one event; never an id: line (R-17)."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        while True:
            try:
                item = sink_q.get(timeout=_HTTP_KEEPALIVE_S)
            except queue.Empty:
                if self.server.closing.is_set():
                    return
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                continue
            if item is None:
                return
            if not isinstance(item, dict):
                continue
            try:
                data = _strict_dumps(item)
            except (TypeError, ValueError, RecursionError) as exc:   # F4: a too-deep event too
                log.warning("http event was not JSON-serialisable: %s", type(exc).__name__)
                if "id" not in item:
                    continue
                data = _strict_dumps(McpServer._error(_fallback_id(item.get("id")), -32603,
                                                      "Response not serialisable"))
            self.wfile.write(b"event: message\ndata: " + data.encode("utf-8") + b"\n\n")
            self.wfile.flush()
            if "id" in item and log.isEnabledFor(logging.DEBUG):
                log.debug("-> http id=%s %s status=%d", _log_value(item.get("id")),
                          "error" if "error" in item else "ok", 200)

    def _answer_json(self, sink_q: "queue.Queue", sid: Optional[str]) -> None:
        """JSON mode (every other request): wait for the sentinel, answer the response or 202."""
        response = None
        while True:
            try:
                item = sink_q.get(timeout=_HTTP_KEEPALIVE_S)
            except queue.Empty:
                if self.server.closing.is_set():
                    self._refuse(503)
                    return
                continue
            if item is None:
                break
            if isinstance(item, dict) and "id" in item:
                response = item
        if response is None:   # cancelled before it answered (Assumption 9)
            self._send_json(202, None)
            return
        self._send_json(200, response, [("Mcp-Session-Id", sid)] if sid else ())

    def do_DELETE(self) -> None:  # noqa: N802 -- stdlib dispatch name
        if not self._precheck():
            return
        ok, sid = self._single_header("Mcp-Session-Id")   # F28: a repeated id is refused, never first-wins
        if not ok or not sid:
            self._refuse(400)
            return
        try:
            ok = self._bridge(self.server.core._http_close, sid)
        except _Closing:
            self._refuse(503)
            return
        if not ok:
            self._refuse(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        if self.headers.get("Content-Length", "0").strip() not in ("", "0") or "Transfer-Encoding" in self.headers:
            # A DELETE body is never read: do not let it be parsed as the next request.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        log.debug("-> http id=%s %s status=%d", None, "deleted", 200)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _flush_before_exit() -> None:
    """Flush every logging handler and stdout before os._exit; each in its own try."""
    for handler in logging.getLogger().handlers + log.handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 -- best effort on the way out
            pass
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 -- a closed pipe must not block the exit
        pass


async def _amain(cfg: ProxyConfig, args, settings: Optional["HttpSettings"]) -> int:
    """The startup/shutdown driver: start the children, serve, stop the children.

    Signal handlers are installed BEFORE start_children (L7), so a SIGTERM
    during a slow startup stops the children already spawned instead of
    killing the proxy with the default disposition and orphaning them. A
    signal cancels the current phase task ONCE; a repeat is logged and
    ignored (L4) so it cannot interrupt the teardown the first one started.
    On the signal path the process ends with os._exit(0) from the OUTER
    finally (L5): the stdin reader thread is parked in readline and would
    otherwise hang interpreter exit, even if the teardown raised.
    In HTTP mode the listener is bound FIRST (L4, Assumption 21): before the
    signal handlers and before any child is spawned, so a busy port is one
    stderr line and exit 2 with nothing to stop.
    """
    loop = asyncio.get_running_loop()
    server = McpServer(cfg)
    srv = None
    if settings is not None:
        try:
            srv = _ProxyHttpServer((settings.bind, settings.port), _ProxyHttpHandler, settings)
        except OSError as exc:                       # EADDRINUSE, EADDRNOTAVAIL, EACCES ...
            print(f"mcp-proxy: cannot listen on {settings.bind}:{settings.port}: "
                  f"{exc.strerror or type(exc).__name__}", file=sys.stderr)
            return 2                                 # no child has been spawned: nothing to stop
    signalled: List[int] = []
    phase: List[asyncio.Task] = []                     # the task a signal must cancel right now

    def _on_signal() -> None:
        if signalled:                                  # L4: cancel ONCE; a repeat must not
            log.warning("signal received again; shutdown already in progress")
            return                                     # interrupt the teardown the first one started
        signalled.append(1)
        if phase:
            phase[-1].cancel()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, _on_signal)
    try:                                               # L5: outer try, so os._exit runs even
        try:                                           # if the teardown below raises
            phase.append(asyncio.ensure_future(server.start_children()))
            if signalled:                              # a signal before the first task existed
                phase[-1].cancel()
            try:
                await phase[-1]
            except ChildStartError as exc:
                log.error("startup failed: %s", exc)
                print(f"mcp-proxy: {exc}", file=sys.stderr)
                return 1
            if srv is not None:
                phase.append(asyncio.ensure_future(server.serve_http(settings, srv)))
            else:
                phase.append(asyncio.ensure_future(server.run()))
            if signalled:
                phase[-1].cancel()
            await phase[-1]
        except asyncio.CancelledError:
            pass                                       # only a signal cancels these tasks
        except ConfigError as exc:                     # the --ready-file refusal (L5 round 4)
            print(f"mcp-proxy: {exc}", file=sys.stderr)
            return 2
        finally:
            await server.stop_children()               # stops every slot, incl. ones still starting
            if srv is not None:
                srv.server_close()                     # idempotent; never shutdown() here
    finally:
        if signalled:
            _flush_before_exit()
            os._exit(0)                                # Assumption 13
    return 0


_HTTP_ONLY_FLAGS = (
    ("--bind", "bind", "127.0.0.1"),
    ("--port", "port", 0),
    ("--http-path", "http_path", _HTTP_DEFAULT_PATH),
    ("--allow-remote", "allow_remote", False),
    ("--token-file", "token_file", None),
    ("--allowed-origin", "allowed_origin", None),
    ("--max-body-bytes", "max_body_bytes", _HTTP_BODY_LIMIT),
    ("--max-sessions", "max_sessions", _HTTP_SESSION_CAP),
    ("--max-connections", "max_connections", _HTTP_CONNECTION_CAP),
    ("--max-inflight", "max_inflight", _HTTP_INFLIGHT_CAP),
    ("--session-idle", "session_idle", _HTTP_SESSION_IDLE_S),
    ("--ready-file", "ready_file", None),
)


def _http_cli_defaults(args) -> None:
    """Refuse HTTP-only flags without --http and --http with --config-json, then fill the defaults.

    Every HTTP-only flag parses with default None, so a non-None value means
    the flag was given. --config-json puts the config in argv (visible in ps)
    next to a network listener, so HTTP mode requires --config.
    """
    given = [flag for flag, dest, _default in _HTTP_ONLY_FLAGS if getattr(args, dest) is not None]
    if not args.http and given:
        raise ConfigError(f"{given[0]} is an HTTP-only flag and requires --http")
    if args.http and args.config_json is not None:
        raise ConfigError("--http refuses --config-json (argv secrets next to a network listener); use --config")
    for _flag, dest, default in _HTTP_ONLY_FLAGS:
        if getattr(args, dest) is None:
            setattr(args, dest, default)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP proxy: one upstream MCP endpoint in front of many child MCP servers",
    )
    parser.add_argument("--project-root", default=None,
                        help="Project root; substituted for every \"{root}\" child argument")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", default=None,
                        help="Path to the JSON config (must be owned by you and not group/world-writable)")
    source.add_argument("--config-json", default=None,
                        help="The JSON config inline. argv is visible in ps: never put secrets here")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    # Phase 2. Every HTTP-only flag defaults to None so "explicitly given" is
    # detectable: one given without --http is refused (a flag that does
    # nothing is ambiguity); the real defaults are filled in below.
    http = parser.add_argument_group("Streamable HTTP (localhost or VPN only, never a public interface)")
    http.add_argument("--http", action="store_true",
                      help="Serve MCP over Streamable HTTP instead of stdio (requires a bearer token)")
    http.add_argument("--bind", default=None,
                      help="IPv4 literal to listen on (default 127.0.0.1); non-loopback needs --allow-remote")
    http.add_argument("--port", type=int, default=None, help="TCP port (default 0 = ephemeral)")
    http.add_argument("--http-path", default=None, help=f"Endpoint path (default {_HTTP_DEFAULT_PATH})")
    http.add_argument("--allow-remote", action="store_true", default=None,
                      help="Allow a non-loopback --bind (a VPN interface; never a public one)")
    http.add_argument("--token-file", default=None,
                      help="Bearer token file (recommended source; owned by you, mode 0600, >= "
                           f"{_TOKEN_MIN_LEN} printable ASCII chars). {_TOKEN_ENV} is the alternative, "
                           "but the environment is readable in /proc/<pid>/environ")
    http.add_argument("--allowed-origin", action="append", default=None,
                      help="Allowed Origin header, exact scheme://host[:port] (repeatable)")
    http.add_argument("--max-body-bytes", type=int, default=None,
                      help=f"Largest accepted POST body (default {_HTTP_BODY_LIMIT})")
    http.add_argument("--max-sessions", type=int, default=None,
                      help=f"Concurrent MCP sessions (default {_HTTP_SESSION_CAP})")
    http.add_argument("--max-connections", type=int, default=None,
                      help=f"Concurrent TCP connections (default {_HTTP_CONNECTION_CAP})")
    http.add_argument("--max-inflight", type=int, default=None,
                      help=f"HTTP requests in flight at once, {_HTTP_INFLIGHT_RANGE[0]}-"
                           f"{_HTTP_INFLIGHT_RANGE[1]} (default {_HTTP_INFLIGHT_CAP})")
    http.add_argument("--session-idle", type=float, default=None,
                      help=f"Seconds an idle session lives (default {_HTTP_SESSION_IDLE_S:g})")
    http.add_argument("--ready-file", default=None,
                      help="Written 0600 after bind: {\"port\": N, \"pid\": P}")
    args = parser.parse_args()

    _configure_logging(args.debug, args.log_file)

    # FR-11: the bearer token leaves the environment BEFORE anything is spawned,
    # so no child can inherit it. Only the HTTP front consumes it.
    env_token = os.environ.pop(_TOKEN_ENV, None)

    settings: Optional[HttpSettings] = None
    try:
        _http_cli_defaults(args)
        cfg = load_config(args.config, args.config_json, args.project_root)
        if args.http:
            settings = load_http_settings(args, env_token)
    except ConfigError as exc:
        print(f"mcp-proxy: {exc}", file=sys.stderr)
        sys.exit(2)
    del env_token

    sys.exit(asyncio.run(_amain(cfg, args, settings)))


if __name__ == "__main__":
    main()
