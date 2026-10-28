#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""sd-router: a supervisor and reverse proxy for stable-diffusion.cpp `sd-server`.

One router instance owns one GPU. Its JSON config declares models; per model it
spawns an sd-server child from config data (argv list, never a shell), proxies
image requests to it, restarts it on a crash, swaps models when the queue head
targets another one, and stops it on /v1/unload to free VRAM. Strict FIFO, ONE
request in flight regardless of model; a cold start or a swap blocks and waits.
Requires only Python 3.9+ stdlib modules.

Config grammar (one 0600 JSON file owned by you, read through --config)
-----------------------------------------------------------------------
  {
    "token": "<bearer, required, >= _SD_TOKEN_MIN_LEN characters>",
    "models": {
      "<name>": {                         name: [A-Za-z0-9._~-]{1,64}
        "command": "/abs/path/sd-server", absolute, a regular executable file
        "args": ["--listen-port", "{port}", ...],
                                          a non-empty list of str with at least one
                                          {port}; {host} optional; no other
                                          placeholder; no fixed port/host flag
        "env": {"KEY": "value"},          optional; keys [A-Z_][A-Z0-9_]*
        "cwd": "/abs/dir",                optional; an absolute directory
        "start_timeout": 120,             optional; seconds, (1, 3600]
        "stop_grace": 5,                  optional; seconds, (0, 60]
        "retries": 1,                     optional; ADDITIONAL sends, 0..3
        "request_timeout": 600            optional; seconds, (1, 7200]
      }
    },
    "body_limit": 33554432,               optional; overrides --body-limit; 1 KiB..1 GiB
    "response_limit": 67108864            optional; the buffered answer cap; 1..1 GiB
  }
  Duplicate keys and unknown keys are refused; a refusal names the key and the
  rule, never the value. A NUL in command, args, env or cwd is refused. {port} and {host} are substituted by an explicit
  replace (never str.format: sd args may hold braces).

Routes (every route requires the bearer)
----------------------------------------
  POST /v1/images/generations, /v1/images/edits, /sdapi/v1/txt2img,
       /sdapi/v1/img2img -- each also under /m/<model>/. The /m/<model>/ prefix
       is authoritative; without it the body `model` (JSON, or a multipart form
       field, canonical forms only) selects the model; /sdapi/* needs the prefix.
       Request and answer bodies are forwarded byte-exact (sd_cpp_extra_args too).
  GET  /v1/models -- the configured models and their state.
  POST /v1/unload[?model=<name>][&force=1], /m/<model>/v1/unload -- queued by
       default (200 only after the child process group is verified gone);
       ?force=1 is immediate and aborts a running request for that model only.
  Everything else: 404 route_not_found.

Closed error table: {"error": {"message", "type", "code"}}
----------------------------------------------------------
  401 unauthorized            404 route_not_found        405 method_not_allowed
  411 length_required         413 body_too_large         415 unsupported_media_type
  400 invalid_request         400 model_required         404 model_unknown
  502 model_crashed           502 model_start_failed     504 model_start_timeout
  503 model_disabled          503 unload_forced          500 unload_failed
  504 upstream_timeout        503 queue_full             502 upstream_failed
  503 shutting_down           503 queue_closed
  A message names a rule, never a value. The child's own non-2xx answers are
  relayed verbatim; they are not router codes.

Layout: one file, nine units, in file order (`# ---- unit N ----`)
------------------------------------------------------------------
  1. config / CLI model -- PURE (parse_config, render_args, _SdArgumentParser);
     load_config, the fd read, is the unit's only I/O.
  2. routing + sniffer -- PURE (ROUTES, route_request, sniff_model).
  3. scheduler decision logic -- PURE (decide, ticket queue helpers).
  4. supervisor -- subprocess (Supervisor, Budget, _signal_group,
     _sd_free_port); the ONLY place Popen appears.
  5. upstream forwarder -- the one send site, _sd_send.
  6. scheduler runtime -- the single scheduler thread, the Condition, tickets.
  7. HTTP front -- generated from Scripts/_mcp_httpfront.py plus declared
     hand-written adaptations.
  8. handlers -- image routes, /v1/models, /v1/unload, the error table.
  9. main, signals, shutdown.
  Units 1-3 reference no socket, subprocess, http or threading name.

Trust assumptions
-----------------
  * Operator `env` and `args` are TRUSTED config (LD_PRELOAD, PATH and the like
    are not denied: the file is 0600 and owner-checked).
  * Single-user host: the sd-server child listens unauthenticated on 127.0.0.1
    and is reachable by any local user.

Exit codes
----------
  0  normal exit, including a signal-initiated shutdown (SIGTERM / SIGINT).
  2  a config or command-line refusal, or a startup failure (the listener cannot
     bind, --log-file or --ready-file cannot be written): one `sd-router: <msg>`
     line on stderr, no traceback.

Declared limits (ADR 0034 carries the full list)
------------------------------------------------
  * Memory worst case: image bytes <= max_connections x (body_limit +
    response_limit) = 8 x (32 + 64) MiB, about 768 MiB at the defaults, plus
    control bodies of at most 4 KiB per connection (about 48 KiB).
  * SIGKILL of the router itself can orphan a child process group.
  * A poison prompt that kills the child on every send burns the shared
    per-model restart budget (5 crashes in 600 s) and can disable the model
    for every client until an unload.
  * The port/host flag check is a deny-list over flag names; readiness (a live
    child answering on the substituted port) is the real safety mechanism.
  * The free port is picked by bind-and-close; the race is mitigated by the
    child-liveness readiness rule, not removed.
  * No TLS; --allow-remote needs --insecure-cleartext. The control-route
    connection reserve only mitigates unauthenticated slot holders.
  * No keep-alive, Content-Length required (chunked refused 411), no restart
    backoff for the first start after an unload, no idle auto-unload, no async
    job routes.
  * The mask + /edits weakness of sd-server (no init_image) is not repaired.
"""

import argparse
import collections
import dataclasses
import enum
import errno
import hmac
import http.client
import io
import ipaddress
import json
import logging
import os
import pwd
import random
import re
import select
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Mapping,
    NamedTuple,
    NoReturn,
    Optional,
    Sequence,
    Tuple,
    Union,
)

log = logging.getLogger("sd-router")


class ConfigError(Exception):
    """A config or command-line refusal; the message names the key and the rule, never the value. Exit 2."""


# Module-level and patchable: tests set them on a privately loaded module.
_SD_TOKEN_MIN_LEN = 32  # the bearer floor (stricter than llm-router's 16)
_SD_CONTROL_RESERVE = 4  # connection slots kept for /v1/models and /v1/unload
_SD_BODY_DEADLINE_S = 120.0  # total time to read one request body
_SD_KILL_STEP_S = 2.0  # SIGKILL, reap, ESRCH confirm


# Every client-chosen structural value (model, method, path shape) reaches a
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
    it on. A symlink at the path, or anything but a regular file this euid
    owns, is refused with OSError (CWE-59). `Scripts/_mcp_logging.py` carries
    the rest -- why the 0600 pair needs both calls, and what stays out.
    """
    import stat
    level = logging.DEBUG if (debug or log_file) else logging.WARNING
    handlers = []
    if log_file:
        fd = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            os.close(fd)
            raise OSError("--log-file: not a regular file owned by this user")
        os.fchmod(fd, 0o600)
        handlers.append(logging.StreamHandler(os.fdopen(fd, "a")))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
# END GENERATED: 57e0eb3befd6


# ---- unit 1 ----
# config / CLI model -- PURE

_SD_TOKEN_MIN_DISTINCT = 8  # distinct characters a bearer token must hold (llm-router V1)
_SD_TOKEN_HINT = "generate one with secrets.token_urlsafe(32)"
_SD_DEFAULT_BODY_LIMIT = 32 * 1024 * 1024  # --body-limit default (D-14)
_SD_DEFAULT_MAX_CONNECTIONS = 8  # --max-connections default (D-14)
_SD_DEFAULT_RESPONSE_LIMIT = 64 * 1024 * 1024  # buffered answer cap (D-6, to be measured)
_SD_RESPONSE_LIMIT_MAX = 1024 * 1024 * 1024  # config response_limit ceiling (M20): 1 GiB
_SD_CONFIG_LIMIT = 1024 * 1024  # largest config file read, in bytes

_SD_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._~-]{1,64}")
_SD_ENV_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
# A placeholder is `{` + an identifier + `}`; any other brace is plain text.
_SD_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SD_PLACEHOLDERS = ("{port}", "{host}")
# A DENY-list over flag names that are NOT verified against the real sd-server
# (--listen-ip / --listen-port are the believed sd.cpp names): an element naming
# one of them is refused unless its value is exactly a placeholder. Readiness (a
# live child answering on the substituted port) is the real safety mechanism.
_SD_PORT_HOST_FLAGS = ("--port", "-p", "--listen-port", "--listen-ip", "--host")

_SD_TOP_KEYS = ("token", "models", "body_limit", "response_limit")
_SD_MODEL_KEYS = ("command", "args", "env", "cwd", "start_timeout", "stop_grace", "retries",
                  "request_timeout")
# key -> (default, low, high): the range is (low, high], low exclusive, high inclusive.
_SD_MODEL_FLOATS = {
    "start_timeout": (120.0, 1.0, 3600.0),
    "stop_grace": (5.0, 0.0, 60.0),
    "request_timeout": (600.0, 1.0, 7200.0),
}
_SD_RETRIES_DEFAULT = 1
_SD_RETRIES_MAX = 3


@dataclasses.dataclass(frozen=True, repr=False)
class ModelSpec:
    """One configured model: how to launch its sd-server child. `env` values are never in repr."""

    name: str  # [A-Za-z0-9._~-]{1,64}
    command: str  # absolute, a regular executable file at load
    args: Tuple[str, ...]  # at least one {port}; {host} optional
    env: Tuple[Tuple[str, str], ...]  # values never in repr
    cwd: Optional[str] = None  # an absolute directory if given
    start_timeout: float = 120.0  # (1, 3600]
    stop_grace: float = 5.0  # (0, 60]
    retries: int = 1  # 0..3, ADDITIONAL sends: retries=1 => at most 2 sends
    request_timeout: float = 600.0  # (1, 7200]; dispatch -> full answer buffered (H7)

    def __repr__(self) -> str:
        env_keys = ", ".join("%s=<redacted>" % key for key, _value in self.env)
        return ("ModelSpec(name=%r, command=%r, args=<%d items>, env={%s}, cwd=%r, start_timeout=%r, "
                "stop_grace=%r, retries=%r, request_timeout=%r)"
                % (self.name, self.command, len(self.args), env_keys, self.cwd, self.start_timeout,
                   self.stop_grace, self.retries, self.request_timeout))

    __str__ = __repr__


@dataclasses.dataclass(frozen=True, repr=False)
class Settings:
    """The validated config. The token is never in repr."""

    token: str
    models: Mapping[str, ModelSpec]
    body_limit: int = _SD_DEFAULT_BODY_LIMIT
    max_connections: int = _SD_DEFAULT_MAX_CONNECTIONS
    response_limit: int = _SD_DEFAULT_RESPONSE_LIMIT

    def __repr__(self) -> str:
        return ("Settings(token=<redacted>, models=[%s], body_limit=%r, max_connections=%r, "
                "response_limit=%r)"
                % (", ".join(repr(name) for name in self.models), self.body_limit,
                   self.max_connections, self.response_limit))

    __str__ = __repr__


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


def _sd_cfg_token(token: Any, where: str) -> str:
    """The bearer token: required, a string, the front's length/charset rule, then at least
    _SD_TOKEN_MIN_DISTINCT distinct characters. Every refusal names the generator, never the value."""
    if not isinstance(token, str):
        raise ConfigError(f"{where}: token: required, a string ({_SD_TOKEN_HINT})")
    try:
        _http_token_value(token, f"{where}: token", _SD_TOKEN_MIN_LEN, ConfigError)
    except ConfigError as exc:
        raise ConfigError(f"{exc} ({_SD_TOKEN_HINT})") from None
    if len(set(token)) < _SD_TOKEN_MIN_DISTINCT:
        raise ConfigError(f"{where}: token: the bearer token must hold at least {_SD_TOKEN_MIN_DISTINCT} "
                          f"distinct characters ({_SD_TOKEN_HINT})")
    return token


def _sd_is_number(value: Any) -> bool:
    """An int or a finite float, never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _sd_cfg_int_range(value: Any, where: str, low: int, high: int) -> int:
    """An int in [low, high] (never a bool): the config twin of _sd_cli_int (M20)."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{where}: must be an integer in {low}..{high}")
    return value


def _sd_cfg_no_nul(value: str, where: str) -> str:
    """Refuse a NUL: Popen raises ValueError on one in argv, env or cwd (M24)."""
    if "\0" in value:
        raise ConfigError(f"{where}: must not contain a NUL character")
    return value


def _sd_cfg_unknown(obj: Mapping[str, Any], allowed: Tuple[str, ...], where: str) -> None:
    """Refuse the first key not in *allowed*, named by key (a safe spelling) and never by value."""
    for key in obj:
        if key not in allowed:
            shown = key if _SD_MODEL_NAME_RE.fullmatch(key) else "<a key of an unsupported spelling>"
            raise ConfigError(f"{where}: unknown key {shown!r} (allowed: {', '.join(allowed)})")


def _sd_cfg_args(args: Any, where: str) -> Tuple[str, ...]:
    """`args`: a non-empty list of str holding {port}; only {port}/{host} placeholders; a port/host
    flag only with a placeholder value. A refusal names the element index, never its value."""
    if not isinstance(args, list) or not args:
        raise ConfigError(f"{where}: must be a non-empty list of strings")
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            raise ConfigError(f"{where}[{index}]: must be a string")
        _sd_cfg_no_nul(arg, f"{where}[{index}]")
    for index, arg in enumerate(args):
        for match in _SD_PLACEHOLDER_RE.finditer(arg):
            if match.group(0) not in _SD_PLACEHOLDERS:
                raise ConfigError(f"{where}[{index}]: unknown placeholder (only {{port}} and {{host}})")
        value: Optional[str]
        if arg in _SD_PORT_HOST_FLAGS:
            value = args[index + 1] if index + 1 < len(args) else None
        elif any(arg.startswith(flag + "=") for flag in _SD_PORT_HOST_FLAGS):
            value = arg.split("=", 1)[1]
        else:
            continue
        if value not in _SD_PLACEHOLDERS:
            raise ConfigError(f"{where}[{index}]: a port/host flag must take exactly {{port}} or {{host}} "
                              f"as its value (the router owns the child's address)")
    if not any("{port}" in arg for arg in args):
        raise ConfigError(f"{where}: must hold the {{port}} placeholder")
    return tuple(args)


def _sd_cfg_env(env: Any, where: str) -> Tuple[Tuple[str, str], ...]:
    """`env`: an object of [A-Z_][A-Z0-9_]* keys and str values; neither echoed. Trusted config:
    LD_PRELOAD, PATH and the like are not denied."""
    if not isinstance(env, dict):
        raise ConfigError(f"{where}: must be an object of string values")
    pairs = []
    for key, value in env.items():
        if not _SD_ENV_KEY_RE.fullmatch(key):
            raise ConfigError(f"{where}: a key does not match [A-Z_][A-Z0-9_]*")
        if not isinstance(value, str):
            raise ConfigError(f"{where}.{key}: must be a string")
        _sd_cfg_no_nul(value, f"{where}.{key}")
        pairs.append((key, value))
    return tuple(pairs)


def _sd_cfg_model(name: str, entry: Any, where: str) -> ModelSpec:
    """One `models.<name>` entry as a ModelSpec. *name* is already validated."""
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be an object")
    _sd_cfg_unknown(entry, _SD_MODEL_KEYS, where)
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        raise ConfigError(f"{where}.command: required, a non-empty string")
    _sd_cfg_no_nul(command, f"{where}.command")
    if not os.path.isabs(command):
        raise ConfigError(f"{where}.command: must be an absolute path")
    if not os.path.isfile(command) or not os.access(command, os.X_OK):
        raise ConfigError(f"{where}.command: must be an existing regular executable file")
    if "args" not in entry:
        raise ConfigError(f"{where}.args: required")
    args = _sd_cfg_args(entry["args"], f"{where}.args")
    env = _sd_cfg_env(entry["env"], f"{where}.env") if "env" in entry else ()
    cwd = entry.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise ConfigError(f"{where}.cwd: must be an absolute path")
        _sd_cfg_no_nul(cwd, f"{where}.cwd")
        if not os.path.isdir(cwd):
            raise ConfigError(f"{where}.cwd: must be an existing directory")
    floats: Dict[str, float] = {}
    for key, (default, low, high) in _SD_MODEL_FLOATS.items():
        value = entry.get(key, default)
        if not _sd_is_number(value) or not low < value <= high:
            raise ConfigError(f"{where}.{key}: must be a number of seconds in ({low:g}, {high:g}]")
        floats[key] = float(value)
    retries = entry.get("retries", _SD_RETRIES_DEFAULT)
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= _SD_RETRIES_MAX:
        raise ConfigError(f"{where}.retries: must be an integer in 0..{_SD_RETRIES_MAX} (additional sends)")
    return ModelSpec(name=name, command=command, args=args, env=env, cwd=cwd,
                     start_timeout=floats["start_timeout"], stop_grace=floats["stop_grace"],
                     retries=retries, request_timeout=floats["request_timeout"])


def parse_config(raw: object, where: str = "config") -> Settings:
    """Validate the decoded config object into Settings (PURE: no socket, subprocess or http).

    Every refusal is a one-line ConfigError naming the key and the rule, never a value.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a JSON object")
    _sd_cfg_unknown(raw, _SD_TOP_KEYS, where)
    if "token" not in raw:
        raise ConfigError(f"{where}: token: required ({_SD_TOKEN_HINT})")
    token = _sd_cfg_token(raw["token"], where)
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise ConfigError(f"{where}: models: required, a non-empty object of model entries")
    models: Dict[str, ModelSpec] = {}
    for name, entry in models_raw.items():
        if not _SD_MODEL_NAME_RE.fullmatch(name):
            raise ConfigError(f"{where}: models: a model name must match [A-Za-z0-9._~-]{{1,64}}")
        models[name] = _sd_cfg_model(name, entry, f"{where}: models.{name}")
    body_limit = _SD_DEFAULT_BODY_LIMIT
    if "body_limit" in raw:
        body_limit = _sd_cfg_int_range(raw["body_limit"], f"{where}: body_limit", *_SD_BODY_LIMIT_RANGE)
    response_limit = _SD_DEFAULT_RESPONSE_LIMIT
    if "response_limit" in raw:
        response_limit = _sd_cfg_int_range(raw["response_limit"], f"{where}: response_limit",
                                           1, _SD_RESPONSE_LIMIT_MAX)
    return Settings(token=token, models=types.MappingProxyType(models), body_limit=body_limit,
                    max_connections=_SD_DEFAULT_MAX_CONNECTIONS, response_limit=response_limit)


def render_args(spec: ModelSpec, host: str, port: int) -> List[str]:
    """The child's argv tail: {port} and {host} substituted by an explicit replace (never
    str.format, sd args may hold braces); every other brace is left as it is."""
    port_text = str(int(port))
    return [arg.replace("{port}", port_text).replace("{host}", host) for arg in spec.args]


def _sd_no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """json object_pairs_hook: a repeated key at any depth is refused, named by key, never by value
    (json.loads would silently keep the last value; ambiguity is refused, ADR 0015)."""
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            shown = key if _SD_MODEL_NAME_RE.fullmatch(key) else "<a key of an unsupported spelling>"
            raise ConfigError(f"--config: duplicate key {shown!r}")
        out[key] = value
    return out


def _sd_no_constant(name: str) -> Any:
    """json parse_constant: NaN and the infinities are not JSON numbers here."""
    raise ConfigError("--config: NaN and Infinity are not accepted")


def _sd_read_config_file(path: str) -> str:
    """Read the config file after checking the OPENED file (an adapted copy of llm-router's
    _rt_read_config_file): O_NOFOLLOW, a regular file owned by the effective uid, no
    group/other bits, at most _SD_CONFIG_LIMIT bytes, UTF-8. A refusal names the rule only."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ConfigError(f"--config: cannot open the config file ({type(exc).__name__})") from None
    total = 0
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
        while total <= _SD_CONFIG_LIMIT:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise ConfigError(f"--config: cannot read the config file ({type(exc).__name__})") from None
    finally:
        os.close(fd)
    if total > _SD_CONFIG_LIMIT:
        raise ConfigError(f"--config: the config file is larger than {_SD_CONFIG_LIMIT} bytes")
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("--config: the config file is not valid UTF-8") from None


def load_config(path: str) -> Settings:
    """Read the --config file (the unit's only I/O), decode it with duplicate keys refused,
    then validate it with parse_config."""
    text = _sd_read_config_file(path)
    try:
        raw = json.loads(text, object_pairs_hook=_sd_no_duplicate_keys, parse_constant=_sd_no_constant)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"--config: not valid JSON (line {exc.lineno}, column {exc.colno})") from None
    except RecursionError:
        raise ConfigError("--config: the config file is nested too deeply") from None
    return parse_config(raw, "--config")


def _sd_refuse(reason: str) -> NoReturn:
    """The one refusal shape: one `sd-router: <reason>` stderr line, exit 2."""
    print("sd-router: " + " ".join(str(reason).splitlines()), file=sys.stderr)
    sys.exit(2)


class _SdArgumentParser(argparse.ArgumentParser):
    """argparse whose own errors are the one-line refusal: no usage block, exit 2.

    argparse quotes an unrecognized argument verbatim (`--config-json <a secret>`),
    so that message is replaced by the rule alone; no value ever reaches stderr.
    """

    def error(self, message: str) -> NoReturn:
        if message.startswith("unrecognized arguments"):
            message = "unrecognized arguments (not echoed; see --help; there is no --config-json)"
        _sd_refuse(message)


def _sd_cli_int(low: int, high: int) -> Callable[[str], int]:
    """An argparse type: a decimal integer in [low, high], refused without echoing the value."""

    def convert(value: str) -> int:
        try:
            number = int(value, 10)
        except ValueError:
            raise argparse.ArgumentTypeError("must be a decimal integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be an integer in [{low}, {high}]")
        return number

    return convert


_SD_PORT_RANGE = (0, 65535)
_SD_BODY_LIMIT_RANGE = (1024, 1024 * 1024 * 1024)
_SD_CONNECTIONS_RANGE = (1, 1024)


def _sd_parser() -> argparse.ArgumentParser:
    parser = _SdArgumentParser(
        prog="sd-router",
        description="Supervisor and proxy for stable-diffusion.cpp sd-server children "
                    "(localhost by default; no TLS).",
        allow_abbrev=False)
    # No --config-json: argv is visible in ps and every config carries the bearer token.
    parser.add_argument("--config", required=True,
                        help="Path to the JSON config (must be owned by you, mode 0600)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Address to listen on (default 127.0.0.1); non-loopback needs "
                             "--allow-remote --insecure-cleartext")
    parser.add_argument("--port", type=_sd_cli_int(*_SD_PORT_RANGE), default=0,
                        help="TCP port (default 0 = ephemeral)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Allow a non-loopback --bind; refused without --insecure-cleartext")
    parser.add_argument("--insecure-cleartext", action="store_true",
                        help="Acknowledge that the bearer token and images travel unencrypted")
    parser.add_argument("--ready-file", default=None,
                        help="Written 0600 after bind: {\"port\": N, \"pid\": P}; removed on shutdown")
    parser.add_argument("--body-limit", type=_sd_cli_int(*_SD_BODY_LIMIT_RANGE),
                        default=_SD_DEFAULT_BODY_LIMIT,
                        help=f"Largest accepted request body in bytes, {_SD_BODY_LIMIT_RANGE[0]}-"
                             f"{_SD_BODY_LIMIT_RANGE[1]} (default {_SD_DEFAULT_BODY_LIMIT})")
    parser.add_argument("--max-connections", type=_sd_cli_int(*_SD_CONNECTIONS_RANGE),
                        default=_SD_DEFAULT_MAX_CONNECTIONS,
                        help=f"Concurrent TCP connections, {_SD_CONNECTIONS_RANGE[0]}-"
                             f"{_SD_CONNECTIONS_RANGE[1]} (default {_SD_DEFAULT_MAX_CONNECTIONS})")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", default=None, help="Log to file, mode 0600 (implies --debug)")
    return parser


def _sd_parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the command line. Every refusal is one stderr line and exit 2 (D-13: --allow-remote
    without --insecure-cleartext is refused here; the WARNING/INFO lines are main()'s)."""
    parser = _sd_parser()
    args = parser.parse_args(argv)
    if args.allow_remote and not args.insecure_cleartext:
        parser.error("--allow-remote needs --insecure-cleartext: sd-router has no TLS, the bearer "
                     "token and images would cross the network in cleartext")
    if args.ready_file is not None and not args.ready_file:
        parser.error("--ready-file: must not be empty")
    return args


# ---- unit 2 ----
# routing + sniffer -- PURE
#
# Strings and bytes in, one value out: no socket, subprocess, http or threading
# name. The path is matched by exact string equality against ROUTES (bare or
# after /m/<model>/), never percent-decoded or normalised; the upstream path
# handed on is the table's own key, never the client's string. The /m/<model>/
# prefix is authoritative (D-11): with it the body is not sniffed and its
# `model` is ignored. Without it an "openai" route falls back to the body
# `model` through sniff_model, which reads at most _SD_SNIFF_LIMIT leading
# bytes, never modifies the body, and refuses (SniffError, a 400) every
# spelling two readers could read two ways -- the behaviour of llm-router's
# _rt_image_fields family, re-written smaller (a declared hand copy of the
# behaviour, not of the code; ADR 0015). Messages name a rule, never a value.
# /v1/models and /v1/unload are control routes the handler dispatches before
# routing; route_request answers them route_not_found like any other path.

_SD_ROUTE_METHOD = "POST"
_SD_PREFIX = "/m/"
# allowlisted upstream path -> kind; "openai" may take the body model, "sdapi" the prefix only
ROUTES: Mapping[str, str] = types.MappingProxyType({
    "/v1/images/generations": "openai",
    "/v1/images/edits": "openai",
    "/sdapi/v1/txt2img": "sdapi",
    "/sdapi/v1/img2img": "sdapi",
})
# path -> the very key object of ROUTES, so a Target never carries the client's string
_SD_ROUTE_KEYS: Mapping[str, str] = types.MappingProxyType({path: path for path in ROUTES})

_SD_SNIFF_LIMIT = 64 * 1024  # leading body bytes the sniffer reads, at most
_SD_SNIFF_PART_CAP = 64  # multipart parts scanned, at most (llm-router _RT_IMAGE_PART_CAP)
_SD_SNIFF_PART_HEAD_CAP = 8192  # one part's header block, in bytes
_SD_SNIFF_MODEL_LIMIT = 256  # a sniffed model value, in characters (llm-router _RT_MODEL_LIMIT)
_SD_SNIFF_TEXT_TYPES = (None, "", "text/plain", "text/plain; charset=utf-8")
_SD_SNIFF_BOUNDARY_TOKEN_RE = re.compile(r"[A-Za-z0-9'+_.-]{1,70}")
_SD_SNIFF_BOUNDARY_QUOTED_RE = re.compile(r"[A-Za-z0-9'()+_,./:=? -]{1,70}")
# Content-Disposition in its one canonical spelling (llm-router _RT_IMAGE_DISPOSITION_RE)
_SD_SNIFF_DISPOSITION_RE = re.compile(
    r'form-data; name="([\x20\x21\x23\x24\x26-\x5b\x5d-\x7e]{1,256})"'
    r'(?:; filename="([\x20\x21\x23-\x5b\x5d-\x7e\x80-\xff]{0,1024})")?')


class Target(NamedTuple):
    """Where an image request goes: the model, the upstream path (a ROUTES key) and
    how the model was chosen, "prefix" or "body"."""

    model: str
    upstream_path: str
    via: str


class RouteError(NamedTuple):
    """A routing refusal: a closed-table code and a message naming the rule."""

    code: str
    message: str


class SniffError(Exception):
    """A body the model sniffer refuses (400 invalid_request); the text names a rule, never a value."""


_SD_NO_ROUTE = RouteError("route_not_found", "no such route")


def _sd_route_head(method: str, raw_path: str,
                   models: Any) -> Union[Tuple[Optional[str], str], RouteError]:
    """(prefix model or None, upstream path) of an image request, or a RouteError: every
    routing decision that needs no body, so the handler can take it before admission.

    Order: the path (an exact ROUTES key, bare or after /m/<model>/; any `%`, `//`, `.`
    or `..` segment is 404), the method (405), the query (an image route takes none,
    400), the prefix model (exact membership in *models*, 404 model_unknown), and an
    "sdapi" route without a prefix (400 model_required)."""
    path, qmark, _query = raw_path.partition("?")
    if "%" in path or "//" in path or any(seg in (".", "..") for seg in path.split("/")):
        return _SD_NO_ROUTE
    model: Optional[str] = None
    rest = path
    if path.startswith(_SD_PREFIX):
        model, sep, tail = path[len(_SD_PREFIX):].partition("/")
        if not model or not sep:
            return _SD_NO_ROUTE
        rest = "/" + tail
    upstream = _SD_ROUTE_KEYS.get(rest)
    if upstream is None:
        return _SD_NO_ROUTE
    if method != _SD_ROUTE_METHOD:
        return RouteError("method_not_allowed", "an image route takes POST only")
    if qmark:
        return RouteError("invalid_request", "an image route takes no query string")
    if model is not None:
        if model not in models:
            return RouteError("model_unknown", "the /m/<model>/ prefix names no configured model")
        return model, upstream
    if ROUTES[upstream] == "sdapi":
        return RouteError("model_required", "an /sdapi/ route needs the /m/<model>/ prefix")
    return None, upstream


def route_request(method: str, raw_path: str, content_type: str, body: bytes,
                  models: Any) -> Union[Target, RouteError]:
    """Target or RouteError for one image request; PURE, never raises for a bad request.

    The /m/<model>/ prefix is authoritative and the body is then not looked at (D-11);
    without it the body model (sniff_model) chooses: a refused body is 400
    invalid_request, no model 400 model_required, a name not in *models* 404
    model_unknown. Neither *models* nor *body* is modified."""
    head = _sd_route_head(method, raw_path, models)
    if isinstance(head, RouteError):
        return head
    model, upstream = head
    if model is not None:
        return Target(model, upstream, "prefix")
    try:
        sniffed = sniff_model(content_type, body)
    except SniffError as exc:
        return RouteError("invalid_request", str(exc))
    if sniffed is None:
        return RouteError("model_required", "no model: use the /m/<model>/ prefix or a body model field")
    if sniffed not in models:
        return RouteError("model_unknown", "the body model names no configured model")
    return Target(sniffed, upstream, "body")


def _sd_has_ctl(value: str) -> bool:
    """True when *value* holds a control character (< 0x20, HT and an obs-fold CRLF
    included, or 0x7F). The ONE Content-Type control check: the sniffer, the inbound
    framing on every path and the child's answer all use it (M6, M23)."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _sd_sniff_ctype(content_type: str) -> Tuple[str, str]:
    """(media type, boundary): application/json (parameters ignored) gives ("application/json",
    ""); multipart/form-data takes exactly one parameter, boundary, a 1-70 character token or
    quoted. Anything else, or a control character, is a SniffError."""
    if _sd_has_ctl(content_type):
        raise SniffError("the Content-Type must not contain a control character")
    head, *params = [part.strip() for part in content_type.split(";")]
    media = head.lower()
    if media == "application/json":
        return media, ""
    if media != "multipart/form-data":
        raise SniffError("the Content-Type must be application/json or multipart/form-data")
    if len(params) != 1:
        raise SniffError("multipart/form-data takes exactly one parameter, boundary")
    name, sep, value = params[0].partition("=")
    if not sep or name.lower() != "boundary":
        raise SniffError("the one multipart parameter must be boundary")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        boundary = value[1:-1]
        valid = _SD_SNIFF_BOUNDARY_QUOTED_RE.fullmatch(boundary) is not None and not boundary.endswith(" ")
    else:
        boundary = value
        valid = _SD_SNIFF_BOUNDARY_TOKEN_RE.fullmatch(boundary) is not None
    if not valid:
        raise SniffError("the boundary must be 1-70 RFC 2046 characters, a token or quoted")
    return media, boundary


def _sd_sniff_name(value: Any) -> str:
    """A sniffed model value: a string of 1-_SD_SNIFF_MODEL_LIMIT characters, valid UTF-8
    text without control characters."""
    if not isinstance(value, str):
        raise SniffError("model must be a string")
    if not 1 <= len(value) <= _SD_SNIFF_MODEL_LIMIT:
        raise SniffError("model must be 1-%d characters" % _SD_SNIFF_MODEL_LIMIT)
    if any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value):
        raise SniffError("model must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SniffError("model must be valid UTF-8 text") from None
    return value


def _sd_sniff_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """json object_pairs_hook: a repeated key at any depth is refused."""
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise SniffError("a JSON key appears more than once")
        out[key] = value
    return out


def _sd_sniff_constant(_name: str) -> Any:
    raise SniffError("NaN and Infinity are not JSON")


def _sd_sniff_json(window: bytes) -> Optional[str]:
    """The `model` of a whole JSON body (the caller checked it fits the window)."""
    try:
        obj = json.loads(window.decode("utf-8"), object_pairs_hook=_sd_sniff_pairs,
                         parse_constant=_sd_sniff_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SniffError("the body is not valid UTF-8 JSON") from None
    if not isinstance(obj, dict):
        raise SniffError("the JSON body must be an object")
    for key in obj:
        if key != "model" and key.casefold() == "model":
            raise SniffError('a key that case-folds to "model" must be spelled exactly "model"')
    if "model" not in obj:
        return None
    return _sd_sniff_name(obj["model"])


def _sd_sniff_part_head(block: bytes) -> Tuple[str, Optional[str]]:
    """(field name, filename or None) of one part's header block: exactly one canonical
    Content-Disposition and at most one Content-Type (a text part: absent, empty or
    text/plain[; charset=utf-8]); any other header, a duplicate or a bare CR/LF is refused."""
    text = block.decode("latin-1")
    lines = text.split("\r\n") if text else []
    if any("\r" in line or "\n" in line for line in lines):
        raise SniffError("a part header must end in CRLF")
    seen = set()
    disposition: Optional[str] = None
    part_type: Optional[str] = None
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep or not key or key != key.strip():
            raise SniffError("malformed part header")
        lname = key.lower()
        if lname in seen:
            raise SniffError("a part header appears more than once")
        seen.add(lname)
        if lname == "content-disposition":
            disposition = value.strip(" \t")
        elif lname == "content-type":
            part_type = value.strip(" \t").lower()
        else:
            raise SniffError("a part may carry Content-Disposition and Content-Type only")
    match = _SD_SNIFF_DISPOSITION_RE.fullmatch(disposition) if disposition is not None else None
    if match is None:
        raise SniffError('a part needs Content-Disposition: form-data; name="..."[; filename="..."] '
                         'in that canonical spelling')
    if match.group(2) is None and part_type not in _SD_SNIFF_TEXT_TYPES:
        raise SniffError("a text part's Content-Type must be text/plain (charset=utf-8) or absent")
    return match.group(1), match.group(2)


def _sd_sniff_cut(model: Optional[str]) -> str:
    """The answer when the window ends before the body does: the model found so far, or a refusal."""
    if model is None:
        raise SniffError("no model field within the first %d bytes; use the /m/<model>/ prefix"
                         % _SD_SNIFF_LIMIT)
    return model


def _sd_sniff_multipart(window: bytes, boundary: str, truncated: bool) -> Optional[str]:
    """The `model` text part of a multipart/form-data body, scanned inside *window* only.

    Delimiters are CRLF-led (a bare-LF + "--" + boundary is refused: a reader taking LF
    for a line end would split there); the closing delimiter may be followed by one CRLF
    only. A part whose name case-folds to "model" must be spelled so, be a text part and
    appear once. When *truncated* (the body is longer than the window) a model part found
    in the window is the answer, later bytes are never read (declared), and a window
    without one is refused."""
    delim = b"--" + boundary.encode("ascii")
    sep = b"\r\n" + delim
    if window.count(b"\n" + delim) != window.count(sep):
        raise SniffError("a bare-LF delimiter: every boundary delimiter must start with CRLF")
    if window.startswith(delim):
        pos = 0
    else:
        at = window.find(sep)
        if at < 0:
            if truncated:
                return _sd_sniff_cut(None)
            raise SniffError("no multipart delimiter for the declared boundary")
        pos = at + 2
    model: Optional[str] = None
    parts = 0
    while True:
        after = pos + len(delim)
        if after + 2 > len(window):
            if truncated:
                return _sd_sniff_cut(model)
            raise SniffError("the closing boundary is missing")
        tail = window[after:after + 2]
        if tail == b"--":
            rest = len(window) - (after + 2)
            if truncated or rest not in (0, 2) or (rest == 2 and not window.endswith(b"\r\n")):
                raise SniffError("the closing boundary is followed by an epilogue")
            return model
        if tail != b"\r\n":
            raise SniffError("a boundary delimiter must be followed by CRLF or --")
        parts += 1
        if parts > _SD_SNIFF_PART_CAP:
            raise SniffError("more than %d parts" % _SD_SNIFF_PART_CAP)
        start = after + 2
        if window.startswith(b"\r\n", start):
            raise SniffError("a part needs a Content-Disposition header")
        head_end = window.find(b"\r\n\r\n", start, start + _SD_SNIFF_PART_HEAD_CAP + 4)
        if head_end < 0:
            if truncated and start + _SD_SNIFF_PART_HEAD_CAP + 4 > len(window):
                return _sd_sniff_cut(model)
            raise SniffError("a part header block is over %d bytes or unterminated" % _SD_SNIFF_PART_HEAD_CAP)
        data_start = head_end + 4
        if window.startswith(delim, data_start):
            raise SniffError("a part with an empty body shares its CRLF with the next delimiter")
        name, filename = _sd_sniff_part_head(window[start:head_end])
        if name != "model" and name.casefold() == "model":
            raise SniffError('a part name that case-folds to "model" must be spelled exactly "model"')
        nxt = window.find(sep, data_start)
        if nxt < 0:
            if truncated:
                return _sd_sniff_cut(model)
            raise SniffError("the closing boundary is missing")
        if name == "model":
            if filename is not None:
                raise SniffError("model must be a text field, not a file part")
            if model is not None:
                raise SniffError("model appears more than once")
            try:
                text = window[data_start:nxt].decode("utf-8")
            except UnicodeDecodeError:
                raise SniffError("model must be valid UTF-8 text") from None
            model = _sd_sniff_name(text)
        pos = nxt + 2


def sniff_model(content_type: str, body: bytes) -> Optional[str]:
    """The body `model` of an un-prefixed image request: None when absent, SniffError (a
    400) for any malformed or ambiguous form. PURE; reads at most the first
    _SD_SNIFF_LIMIT bytes and never modifies *body*. A JSON body must fit that window
    whole (a longer one is refused, never half-parsed)."""
    media, boundary = _sd_sniff_ctype(content_type)
    window = bytes(body[:_SD_SNIFF_LIMIT])
    truncated = len(body) > _SD_SNIFF_LIMIT
    if media == "application/json":
        if truncated:
            raise SniffError("a JSON body over %d bytes cannot be sniffed; use the /m/<model>/ prefix"
                             % _SD_SNIFF_LIMIT)
        return _sd_sniff_json(window)
    return _sd_sniff_multipart(window, boundary, truncated)


# ---- unit 3 ----
# scheduler decision logic -- PURE
#
# Plain data in, one Decision out: no socket, subprocess, http or threading
# name, and no clock (the caller passes `now`). The runtime Ticket, which owns
# Events, lives in unit 6; unit 6 copies TicketView / ModelView snapshots under
# the scheduler lock and executes what decide() returns.


class ModelState(enum.Enum):
    """The per-model lifecycle. DISABLED is NOT a state: it is a per-model flag
    (ModelView.disabled) beside the Budget; `group_alive` marks a DEAD zombie."""

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    DEAD = "dead"


_SD_RESIDENT_STATES = frozenset((ModelState.STARTING, ModelState.READY, ModelState.STOPPING))
_SD_ACTIONS = frozenset(("run", "swap", "start", "stop", "fail", "idle"))
# Decision.code of a "stop" whose target is not resident: answer 200
# {"unloaded": false} at once, clear the disabled flag and the Budget, no ladder.
_SD_NOT_RESIDENT = "not_resident"


class ModelView(NamedTuple):
    """One model's snapshot, copied by unit 6 under the lock."""

    state: ModelState
    group_alive: bool = False  # DEAD only: the group survived the ladder (a zombie)
    disabled: bool = False  # the restart budget tripped; only an unload clears it
    restart_at: float = 0.0  # earliest `now` of an automatic restart (backoff); 0.0 = at once


class TicketView(NamedTuple):
    """A ticket as decide() sees it: plain data, no Events."""

    kind: str  # "request" | "unload"
    model: Optional[str]  # None: an unload's default target (the resident model at the head)
    force: bool
    cancelled: bool
    attempts: int  # sends so far


class Decision(NamedTuple):
    """What the scheduler does next. action is one of _SD_ACTIONS; code is the closed-table
    error code of a "fail", _SD_NOT_RESIDENT or None (run the ladder) for a "stop", else None."""

    action: str
    model: Optional[str]
    code: Optional[str] = None


def _sd_is_resident(view: ModelView) -> bool:
    """RESIDENT, defined once (D-17): STARTING, READY or STOPPING, or DEAD with a live
    group. The disabled flag never changes it; DEAD without a group is not resident."""
    if view.state in _SD_RESIDENT_STATES:
        return True
    return view.state is ModelState.DEAD and bool(view.group_alive)


def _sd_resident_model(states: Mapping[str, ModelView]) -> Optional[str]:
    """The resident model's name, or None (the single-GPU invariant allows at most one)."""
    for name, view in states.items():
        if _sd_is_resident(view):
            return name
    return None


def _sd_pick(force_slots: Sequence[TicketView],
             queue: Sequence[TicketView]) -> Tuple[Optional[str], int]:
    """Which ticket is the head: ("force", 0) while a force unload waits (it jumps the
    FIFO, which stays untouched); else ("fifo", i), queue[i] the first ticket not
    cancelled and queue[:i] cancelled ones to drop; (None, len(queue)) when none is live."""
    if force_slots:
        return "force", 0
    for index, ticket in enumerate(queue):
        if not ticket.cancelled:
            return "fifo", index
    return None, len(queue)


def _sd_requeue_head(queue: Sequence[TicketView], ticket: TicketView) -> Tuple[TicketView, ...]:
    """A retry re-queues the SAME ticket at the head, never the tail (FIFO preserved)."""
    return (ticket,) + tuple(queue)


def _sd_may_retry(attempts: int, retries: int, disabled: bool) -> bool:
    """A crashed send may be re-sent while attempts (sends so far) <= retries and the
    budget did not disable the model: retries=1 allows a second send, never a third."""
    return not disabled and attempts <= retries


def decide(head: Optional[TicketView], states: Mapping[str, ModelView],
           resident: Optional[str], now: float) -> Decision:
    """The scheduler's next step for *head* (PURE: reads no clock, writes nothing).

    *resident* is the resident model's name (or None) and *now* the caller's clock.
    A cancelled head is never decided on (idle): it can never swap, start or stop.
    """
    if head is None or head.cancelled:
        return Decision("idle", None)
    if head.kind == "unload":
        target = head.model if head.model is not None else resident
        if target is None:
            return Decision("stop", None, _SD_NOT_RESIDENT)
        if target not in states:
            return Decision("fail", target, "model_unknown")
        if target == resident:
            return Decision("stop", target)  # the ladder; also re-run on a zombie
        return Decision("stop", target, _SD_NOT_RESIDENT)
    if head.kind != "request":
        raise ValueError("decide: unknown ticket kind")
    target = head.model
    view = states.get(target) if target is not None else None
    if view is None:
        return Decision("fail", target, "model_unknown")
    if view.disabled:
        return Decision("fail", target, "model_disabled")
    if any(v.state is ModelState.DEAD and v.group_alive for v in states.values()):
        return Decision("fail", target, "unload_failed")  # a zombie group holds the GPU (M2)
    if resident is not None:
        if resident != target:
            return Decision("swap", target)
        if view.state is ModelState.READY:
            return Decision("run", target)
        return Decision("idle", target)  # STARTING / STOPPING: a transition is in progress
    if now < view.restart_at:
        return Decision("idle", target)  # backoff before an automatic restart
    return Decision("start", target)


# ---- unit 4 ----
# supervisor -- subprocess
#
# Declared adapted copies (not imports): _signal_group and the stop ladder from
# Scripts/mcp-proxy.py, _sd_wait_exit_unreaped from the sbx helper's
# _wait_exit_unreaped (ClaudeCode/skills/sandbox-run/scripts/sbx). Every constant
# and seam below is module-level and read at call time: tests patch them.

_SD_RESTART_BUDGET = 5  # crashes inside the window that disable a model (mcp-proxy _RESTART_BUDGET)
_SD_RESTART_WINDOW_S = 600.0  # the sliding crash window (mcp-proxy _RESTART_WINDOW_S)
_SD_STOP_GRACE_S = 5.0  # SIGTERM -> SIGKILL grace when a spec carries none
_SD_READY_TICK_S = 0.1  # readiness poll tick; also the connect select tick
_SD_PROBE_TIMEOUT_S = 1.0  # longest wait for one GET / answer (bounded by the deadline too)
_SD_PORT_TRIES = 3  # spawns per launch when the child exits early (a port collision)
_SD_EARLY_EXIT_S = 2.0  # an exit this soon after the spawn is a port collision, not a crash
_SD_KQ_POLL_S = 0.1  # darwin: longest single kqueue wait
_SD_WAITID_POLL_S = 0.02  # elsewhere: waitid poll interval; also the signal-0 probe tick
_SD_CHILD_HOST = "127.0.0.1"  # the child always listens on loopback
_SD_CHILD_PATH = "/usr/bin:/bin"  # the child's PATH unless the config env sets one (D-1)
_SD_CHILD_LANG = "C.UTF-8"
# Delay before the n-th CONSECUTIVE automatic restart (n >= 1), plus <= 20 % jitter;
# the first start after an unload or an idle model is immediate (D-8).
_SD_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0)
_SD_BACKOFF_JITTER = 0.2


def _sd_now() -> float:
    """The clock seam: time.monotonic. Every supervisor deadline is on this scale."""
    return time.monotonic()


def _sd_jitter() -> float:
    """The jitter seam: a float in [0, 1)."""
    return random.random()


def _sd_backoff_s(restarts: int) -> float:
    """Seconds to wait before the *restarts*-th consecutive automatic restart.

    0.0 for restarts <= 0 (the first start after an unload or idle is immediate);
    otherwise the schedule step (the last one repeats) plus up to _SD_BACKOFF_JITTER of it.
    """
    if restarts <= 0:
        return 0.0
    schedule = _SD_BACKOFF_S
    base = schedule[min(restarts, len(schedule)) - 1]
    return base * (1.0 + _SD_BACKOFF_JITTER * min(max(_sd_jitter(), 0.0), 1.0))


class Budget:
    """The per-model crash budget: _SD_RESTART_BUDGET crashes inside _SD_RESTART_WINDOW_S
    disable the model until clear() (an unload).

    No lock of its own (round-3 H2): its callers hold the scheduler Condition lock.
    """

    def __init__(self) -> None:
        self.crashes: Deque[float] = collections.deque()

    def record(self, now: Optional[float] = None) -> bool:
        """Record one crash at *now* (default _sd_now()); True while the crashes inside the
        window number _SD_RESTART_BUDGET or more (DISABLED)."""
        now = _sd_now() if now is None else now
        self.crashes.append(now)
        window = _SD_RESTART_WINDOW_S
        while self.crashes and now - self.crashes[0] > window:
            self.crashes.popleft()
        return len(self.crashes) >= _SD_RESTART_BUDGET

    def clear(self) -> None:
        """Forget every crash (an unload re-enables the model)."""
        self.crashes.clear()


def _sd_free_port() -> int:
    """A free loopback port: bind to port 0, read it, close. The race until the child binds
    is declared; the readiness rule (a live child answering on it) mitigates it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((_SD_CHILD_HOST, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a child's whole process group; False once it must not be signalled again.

    Declared adapted copy of mcp-proxy's _signal_group. pgid is captured at spawn
    (start_new_session=True: pgid == the child's pid). POSIX does not reuse an id as a
    pid or pgid while a group with that id has a member, so this never reaches a
    stranger while a member survives. False means ESRCH (group observed empty) or
    EPERM (no longer ours to signal; S-L6).
    """
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        log.warning("process group signal refused: %s", type(exc).__name__)
        return False
    return True


def _sd_wait_exit_unreaped(pid: int, deadline: float) -> bool:
    """True once *pid* (our child) has EXITED, WITHOUT reaping it; False at *deadline*.

    Declared adapted copy of sbx _wait_exit_unreaped (kill before reap, H5): the
    leader stays an unreaped zombie, so its pid -- and the pgid -- cannot be recycled
    while the group is signalled. The branch is chosen by sys.platform, NEVER by
    hasattr(os, "waitid") (Python >= 3.13 has waitid on darwin). darwin registers a
    kqueue EVFILT_PROC / NOTE_EXIT event (ESRCH on registration: already exited);
    everything else polls os.waitid(P_PID, pid, WEXITED | WNOWAIT | WNOHANG). The
    module's `select` and `os` are looked up at call time.
    """
    if sys.platform == "darwin":
        kq = select.kqueue()
        try:
            event = select.kevent(
                pid, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT)
            try:
                kq.control([event], 0, 0)
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
                return True  # already exited, still unreaped
            while True:
                remaining = deadline - _sd_now()
                if remaining <= 0:
                    return False
                if kq.control(None, 1, min(remaining, _SD_KQ_POLL_S)):
                    return True
        finally:
            kq.close()
    while True:
        try:
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        except ChildProcessError:
            return True
        if info is not None:
            return True
        if _sd_now() >= deadline:
            return False
        time.sleep(_SD_WAITID_POLL_S)


def _sd_child_env(spec: ModelSpec) -> Dict[str, str]:
    """The child's environment: a fixed minimal set (PATH, HOME, LANG) plus the config env.
    Never os.environ, never the router token (D-1)."""
    try:
        home = pwd.getpwuid(os.geteuid()).pw_dir or "/"
    except KeyError:
        home = "/"
    env = {"PATH": _SD_CHILD_PATH, "HOME": home, "LANG": _SD_CHILD_LANG}
    env.update(dict(spec.env))
    return env


def _sd_probe_status(port: int, deadline: float, stop: threading.Event) -> Optional[int]:
    """One readiness probe: a select-tick non-blocking connect (model: mcp-sandbox
    _default_connector), then a minimal GET / over http.client. The status code, or None
    when nothing answered (refused, timed out, stopped, malformed)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn: Optional[http.client.HTTPConnection] = None
    try:
        sock.setblocking(False)
        err = sock.connect_ex((_SD_CHILD_HOST, port))
        if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
            return None
        while err != 0:
            if stop.is_set():
                return None
            left = deadline - _sd_now()
            if left <= 0:
                return None
            _r, ready, _x = select.select([], [sock], [], min(left, _SD_READY_TICK_S))
            if ready:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err != 0:
                    return None
        left = deadline - _sd_now()
        if left <= 0 or stop.is_set():
            return None
        timeout = min(left, _SD_PROBE_TIMEOUT_S)
        sock.settimeout(timeout)
        conn = http.client.HTTPConnection(_SD_CHILD_HOST, port, timeout=timeout)
        conn.sock = sock  # already connected: http.client must not connect again
        conn.request("GET", "/", headers={"Connection": "close"})
        response = conn.getresponse()
        status = int(response.status)
        response.close()
        return status
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        if conn is not None:
            conn.close()
        sock.close()


class Child:
    """One spawned sd-server: the Popen, its pid and pgid (== pid, start_new_session), the
    port it was told to listen on and the argv it was spawned with."""

    def __init__(self, spec: ModelSpec, proc: Any, port: int, argv: List[str]) -> None:
        self.spec = spec
        self.proc = proc
        self.pid: int = proc.pid
        self.pgid: int = proc.pid  # start_new_session=True: the child leads its own group
        self.port = port
        self.argv = argv
        self.spawned = _sd_now()
        # True once a poll() reaped the leader (M14): the zombie that pinned the pgid is
        # gone, so stop() cannot wait on it and gives the group the grace itself.
        self.reaped = False

    def __repr__(self) -> str:
        return "Child(model=%r, pid=%r, port=%r)" % (self.spec.name, self.pid, self.port)


class Supervisor:
    """Spawns, probes and stops sd-server children. No lock of its own: the single
    scheduler thread drives it; `registry` holds every pgid spawned and not yet seen gone."""

    def __init__(self) -> None:
        self.registry: set = set()

    def start(self, spec: ModelSpec) -> Child:
        """ONE spawn on a fresh loopback port -- the only Popen in the file. argv is a list
        (never a shell); the child leads a new session; stdio is DEVNULL."""
        port = _sd_free_port()
        argv = [spec.command] + render_args(spec, _SD_CHILD_HOST, port)
        proc = subprocess.Popen(argv, shell=False, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=_sd_child_env(spec),
                                cwd=spec.cwd, close_fds=True)
        child = Child(spec, proc, port, argv)
        self.registry.add(child.pgid)
        log.info("sd child spawned: model=%s pid=%d port=%d", spec.name, child.pid, port)
        return child

    def _poll(self, child: Child) -> Optional[int]:
        """child.proc.poll(); a non-None answer REAPED the leader (M14, S-L6), so the group
        is probed with signal 0 at once: observed empty (ESRCH, or EPERM: no longer ours)
        drops the pgid from the registry so nothing signals it again; otherwise the Child
        is marked reaped and stop() gives the survivors the grace itself."""
        code = child.proc.poll()
        if code is not None and not child.reaped:
            child.reaped = True
            if not _signal_group(child.pgid, 0):
                self.registry.discard(child.pgid)
        return code

    def wait_ready(self, child: Child, deadline: float, stop: threading.Event) -> bool:
        """Poll GET / every _SD_READY_TICK_S until any status EXCEPT 503 answers (503 is
        still loading, H2). True only while the child is alive; False at once when it
        exits, at *deadline*, or when *stop* is set. It never kills."""
        while True:
            if stop.is_set() or self._poll(child) is not None:
                return False
            if _sd_now() >= deadline:
                return False
            status = _sd_probe_status(child.port, deadline, stop)
            if status is not None and status != 503:
                return self._poll(child) is None  # liveness re-checked after the answer
            left = deadline - _sd_now()
            if left <= 0:
                return False
            if stop.wait(min(_SD_READY_TICK_S, left)):
                return False

    def launch(self, spec: ModelSpec, deadline: float,
               stop: threading.Event) -> Tuple[str, Optional[Child]]:
        """start() then wait_ready(); an exit within _SD_EARLY_EXIT_S of the spawn is a port
        collision and respawns on a new port, at most _SD_PORT_TRIES spawns. Outcome
        "ready" (the live Child), "failed", "timeout" or "stopped" (child None, every
        group spawned here stopped)."""
        tries = _SD_PORT_TRIES
        for attempt in range(1, tries + 1):
            try:
                child = self.start(spec)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                # ValueError: Popen refuses a NUL in argv, env or cwd (M24); the config
                # loader refuses one too, so this is the failure path, not a crash of the step.
                log.warning("sd child spawn failed: model=%s error=%s", spec.name, type(exc).__name__)
                return "failed", None
            if self.wait_ready(child, deadline, stop):
                log.info("sd child ready: model=%s pid=%d attempt=%d", spec.name, child.pid, attempt)
                return "ready", child
            code = self._poll(child)
            early = code is not None and _sd_now() - child.spawned < _SD_EARLY_EXIT_S
            self.stop(child)
            if stop.is_set():
                return "stopped", None
            if code is None:
                log.warning("sd child start timed out: model=%s", spec.name)
                return "timeout", None
            log.warning("sd child exited before ready: model=%s exit=%s attempt=%d",
                        spec.name, code, attempt)
            if not early or _sd_now() >= deadline:
                return "failed", None
        return "failed", None

    def _wait_group_empty(self, pgid: int, deadline: float) -> bool:
        """True once a signal-0 probe of *pgid* finds the group empty (ESRCH/EPERM) before
        *deadline*; False when it still has a member at the deadline. The grace of a
        group whose leader was already reaped."""
        while True:
            if not _signal_group(pgid, 0):
                return True
            if _sd_now() >= deadline:
                return False
            time.sleep(_SD_WAITID_POLL_S)

    def _confirm_gone(self, pgid: int) -> bool:
        """Probe signal 0 until ESRCH/EPERM within _SD_KILL_STEP_S; on that the pgid leaves
        the registry at once (M3) and is never signalled again."""
        end = _sd_now() + _SD_KILL_STEP_S
        while True:
            if not _signal_group(pgid, 0):
                self.registry.discard(pgid)
                return True
            if _sd_now() >= end:
                return False
            time.sleep(_SD_WAITID_POLL_S)

    def stop(self, child: Child) -> bool:
        """The kill-before-reap ladder (D-12, H5): SIGTERM the group, wait stop_grace for the
        leader WITHOUT reaping it, SIGKILL the group (the leader is still an unreaped
        zombie, so the pgid cannot be recycled; stragglers die too), reap through
        child.proc, then confirm the group gone with signal 0. True once confirmed (the
        pgid is unregistered); False when the group survives (it stays registered). A
        child whose pgid is no longer registered is not signalled: True. A leader that a
        poll() already reaped (child.reaped, M14) has no zombie to wait on: the group gets
        the full grace from a signal-0 watch before its SIGKILL, and none once empty."""
        pgid = child.pgid
        if pgid not in self.registry:
            return True
        grace = child.spec.stop_grace or _SD_STOP_GRACE_S
        t0 = _sd_now()
        if _signal_group(pgid, signal.SIGTERM):
            if child.reaped:
                # No zombie to wait on (M14): the survivors get the grace from a signal-0
                # watch, and an emptied group is not SIGKILLed at all.
                emptied = self._wait_group_empty(pgid, _sd_now() + grace)
            else:
                _sd_wait_exit_unreaped(child.pid, _sd_now() + grace)
                emptied = False
            if not emptied:
                _signal_group(pgid, signal.SIGKILL)
        try:
            child.proc.wait(timeout=_SD_KILL_STEP_S)
        except subprocess.TimeoutExpired:
            pass
        gone = self._confirm_gone(pgid)
        if gone:
            log.info("sd child stopped: model=%s pid=%d exit=%s seconds=%.2f", child.spec.name,
                     child.pid, child.proc.returncode, _sd_now() - t0)
        else:
            log.warning("sd child group survived the stop ladder: model=%s pgid=%d",
                        child.spec.name, pgid)
        return gone

    def kill_all(self) -> None:
        """The last-resort shutdown sweep over the registry: each entry is probed with
        signal 0 RIGHT BEFORE its SIGKILL; ESRCH or EPERM drops it unsignalled (M3, S-L6)."""
        for pgid in sorted(self.registry):
            if not _signal_group(pgid, 0):
                self.registry.discard(pgid)
                continue
            if not _signal_group(pgid, signal.SIGKILL):
                self.registry.discard(pgid)


# ---- unit 5 ----
# upstream forwarder
#
# _sd_send is the ONE upstream send site. Model: llm-router _rt_send (refuse 1xx,
# 3xx and a non-identity Content-Encoding, close on every failure). The request is
# formatted by http.client (putrequest/putheader validate the method, the path and
# every header) into a capture, then written and the whole answer read through a
# non-blocking socket in select ticks of at most _SD_SEND_TICK_S, so the absolute
# deadline and the abort Event are both observed on every tick; a force, a client
# abort or a shutdown additionally shut the socket down from another thread
# (on_socket hands it out). The answer is buffered whole (Connection: close, read to
# EOF) and parsed by http.client.HTTPResponse from the buffer.

_SD_SEND_TICK_S = 0.1  # select tick of the upstream exchange (<= 0.2 s)
_SD_SEND_CHUNK = 64 * 1024  # largest single send()/recv()
_SD_HEAD_LIMIT = 64 * 1024  # slack over response_limit for the status line and headers
_SD_UPSTREAM_KINDS = frozenset(("connect", "eof", "aborted", "timeout", "too_large", "failed"))


class UpstreamError(Exception):
    """An upstream exchange that produced no relayable answer. `.kind` is one of
    _SD_UPSTREAM_KINDS: connect (nothing listened), eof (the link broke or closed before a
    whole answer), aborted (abort_evt was set), timeout (the deadline passed), too_large
    (over response_limit), failed (1xx, 3xx, an encoded or malformed answer). The message
    is the kind only: never a body, a header value or an address."""

    def __init__(self, kind: str) -> None:
        if kind not in _SD_UPSTREAM_KINDS:
            raise ValueError("UpstreamError: unknown kind")
        super().__init__(kind)
        self.kind = kind


def _sd_forward_headers(headers: Sequence[Tuple[str, str]]) -> Tuple[Tuple[str, str], ...]:
    """The client headers that may cross the proxy hop: the FIRST Content-Type only (D-9).
    Authorization, Cookie, Host, Expect, Content-Length and every hop-by-hop header are
    dropped; Content-Length, Host, Accept and Accept-Encoding are set by _sd_send itself."""
    for name, value in headers:
        if isinstance(name, str) and name.strip().lower() == "content-type":
            return (("Content-Type", str(value)),)
    return ()


class _SdWireCapture:
    """A write-only stand-in for http.client's socket: collects what HTTPConnection would
    send (the formatted head, then the body) without any I/O."""

    def __init__(self) -> None:
        self.chunks: List[bytes] = []

    def sendall(self, data: Any) -> None:
        self.chunks.append(bytes(data))

    def close(self) -> None:
        pass


class _SdReplay:
    """A read-only stand-in for a socket: HTTPResponse reads the buffered answer from it."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def makefile(self, mode: str) -> Any:
        return io.BytesIO(self.data)


def _sd_check_go(deadline: float, abort_evt: threading.Event) -> float:
    """Seconds left before *deadline*; raise when aborted (first) or past it."""
    if abort_evt.is_set():
        raise UpstreamError("aborted")
    left = deadline - _sd_now()
    if left <= 0:
        raise UpstreamError("timeout")
    return left


def _sd_connect(sock: socket.socket, port: int, deadline: float, abort_evt: threading.Event) -> None:
    """A tick-bounded non-blocking connect to the child (sock already non-blocking)."""
    err = sock.connect_ex((_SD_CHILD_HOST, port))
    if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
        raise UpstreamError("connect")
    while err != 0:
        left = _sd_check_go(deadline, abort_evt)
        _r, ready, _x = select.select([], [sock], [], min(left, _SD_SEND_TICK_S))
        if ready:
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                raise UpstreamError("connect")


def _sd_write(sock: socket.socket, chunks: Sequence[bytes], deadline: float,
              abort_evt: threading.Event) -> bool:
    """Write *chunks* in select ticks; False when the peer stopped reading (the caller
    still reads: a child may answer and close before taking the whole body)."""
    for chunk in chunks:
        view = memoryview(chunk)
        offset = 0
        while offset < len(view):
            left = _sd_check_go(deadline, abort_evt)
            _r, ready, _x = select.select([], [sock], [], min(left, _SD_SEND_TICK_S))
            if not ready:
                continue
            try:
                offset += sock.send(view[offset:offset + _SD_SEND_CHUNK])
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                if abort_evt.is_set():
                    raise UpstreamError("aborted")
                return False
    return True


def _sd_read_all(sock: socket.socket, cap: int, deadline: float, abort_evt: threading.Event) -> bytes:
    """Read the whole answer to EOF in select ticks; at most *cap* bytes."""
    parts: List[bytes] = []
    size = 0
    while True:
        left = _sd_check_go(deadline, abort_evt)
        ready, _w, _x = select.select([sock], [], [], min(left, _SD_SEND_TICK_S))
        if not ready:
            continue
        try:
            data = sock.recv(_SD_SEND_CHUNK)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            if abort_evt.is_set():
                raise UpstreamError("aborted")
            raise UpstreamError("eof")
        if not data:
            if abort_evt.is_set():
                raise UpstreamError("aborted")
            return b"".join(parts)
        size += len(data)
        if size > cap:
            raise UpstreamError("too_large")
        parts.append(data)


def _sd_parse_answer(raw: bytes, limit: int) -> Tuple[int, Tuple[Tuple[str, str], ...], bytes]:
    """Parse a buffered HTTP/1.x answer: (status, relayed headers, body)."""
    if not raw:
        raise UpstreamError("eof")  # the child closed without a status line
    resp = http.client.HTTPResponse(_SdReplay(raw))  # type: ignore[arg-type]
    try:
        resp.begin()
        status = int(resp.status)
        if 100 <= status < 200 or 300 <= status < 400:
            raise UpstreamError("failed")
        encoding = (resp.getheader("Content-Encoding") or "").strip().lower()
        if encoding not in ("", "identity"):
            raise UpstreamError("failed")
        body = resp.read()
    except http.client.IncompleteRead:
        raise UpstreamError("eof")  # a truncated answer: the link broke mid-body
    except (http.client.HTTPException, ValueError):
        raise UpstreamError("failed")
    finally:
        resp.close()
    if len(body) > limit:
        raise UpstreamError("too_large")
    ctype = resp.getheader("Content-Type")
    if ctype and _sd_has_ctl(ctype):
        raise UpstreamError("failed")  # an obs-fold or control byte is never relayed (M6)
    relay = (("Content-Type", ctype),) if ctype else ()
    return status, relay, body


def _sd_send(port: int, method: str, path: str, headers: Sequence[Tuple[str, str]], body: bytes,
             deadline: float, abort_evt: threading.Event,
             on_socket: Optional[Callable[[socket.socket], None]] = None,
             response_limit: Optional[int] = None) -> Tuple[int, Tuple[Tuple[str, str], ...], bytes]:
    """The ONE upstream send site: one exchange with the child on 127.0.0.1:*port*.

    *method* and *path* come from the route table (never the client's request line);
    only the client's Content-Type crosses (_sd_forward_headers); the body is sent
    byte-exact with a recomputed Content-Length, Accept: */*, Accept-Encoding:
    identity, Host 127.0.0.1:<port> and Connection: close. *deadline* is absolute on the
    _sd_now() scale; *abort_evt* is checked every tick; on_socket(sock) is called once
    with the connected socket. The answer is buffered whole under *response_limit*
    (default _SD_DEFAULT_RESPONSE_LIMIT). Returns (status, (("Content-Type", v),) or (),
    body); every failure raises UpstreamError, the socket is always closed.
    """
    limit = _SD_DEFAULT_RESPONSE_LIMIT if response_limit is None else int(response_limit)
    payload = bytes(body)
    capture = _SdWireCapture()
    conn = http.client.HTTPConnection(_SD_CHILD_HOST, port)
    try:
        conn.sock = capture  # type: ignore[assignment] -- format only, never connect
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", "%s:%d" % (_SD_CHILD_HOST, port))
        for name, value in _sd_forward_headers(headers):
            conn.putheader(name, value)
        conn.putheader("Accept", "*/*")
        conn.putheader("Accept-Encoding", "identity")
        conn.putheader("Content-Length", str(len(payload)))
        conn.putheader("Connection", "close")
        conn.endheaders()
    except (http.client.HTTPException, ValueError):
        raise UpstreamError("failed")  # an illegal method, path or header value
    finally:
        conn.sock = None
        conn.close()
    chunks = capture.chunks + ([payload] if payload else [])
    _sd_check_go(deadline, abort_evt)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        _sd_connect(sock, port, deadline, abort_evt)
        if on_socket is not None:
            on_socket(sock)
        _sd_write(sock, chunks, deadline, abort_evt)
        raw = _sd_read_all(sock, limit + _SD_HEAD_LIMIT, deadline, abort_evt)
        return _sd_parse_answer(raw, limit)
    except UpstreamError:
        raise
    except OSError:
        raise UpstreamError("aborted" if abort_evt.is_set() else "eof")
    finally:
        sock.close()


# ---- unit 6 ----
# scheduler runtime
#
# The data side: Ticket, TicketResult, the admission counter and its reservations,
# and the submit / cancel API, all under ONE threading.Condition (plan Section 5,
# "Lock / thread model"). Every write of a model's state, group_alive, disabled flag,
# Budget and restart_at, of `running`, of the queue and of `admitted` is a short
# non-blocking section under `cond`. No logging and no blocking call under the lock;
# socket.shutdown(SHUT_RDWR) is the one exemption. Ticket.finish() (which sets
# `done`) is always called AFTER the locked section.
#
# The scheduler thread (_run) takes the head under the lock (force_slots first,
# then the FIFO), asks decide() what to do, and executes it with the lock
# RELEASED around every blocking step (spawn, readiness, send, stop ladder). The
# head pop and the STOPPED -> STARTING write are one locked section (final M3).
# Abort classification reads ticket.abort_reason under the lock: None is a crash,
# "timeout" is charged like one, "force" / "client" / "shutdown" never are.

_SD_SCHED_TICK_S = 0.05  # the scheduler's wait tick (0 < tick <= 0.2)
_SD_SHUTDOWN_SLACK_S = 5.0  # shutdown(): join bound over the longest stop ladder

# The closed error-code table: code -> HTTP status (plan Section 3). Every refusal the
# router writes uses one of these codes; the child's own answers are relayed verbatim.
_SD_ERROR_STATUS = {
    "unauthorized": 401,
    "route_not_found": 404,
    "method_not_allowed": 405,
    "length_required": 411,
    "body_too_large": 413,
    "unsupported_media_type": 415,
    "invalid_request": 400,
    "model_required": 400,
    "model_unknown": 404,
    "model_crashed": 502,
    "model_start_failed": 502,
    "model_start_timeout": 504,
    "model_disabled": 503,
    "unload_forced": 503,
    "unload_failed": 500,
    "upstream_timeout": 504,
    "queue_full": 503,
    "upstream_failed": 502,
    "shutting_down": 503,
    "queue_closed": 503,
}
_SD_ABORT_REASONS = ("force", "client", "shutdown", "timeout")
# UpstreamError kinds that are a refused ANSWER, not a crash: 502 upstream_failed, one send.
_SD_ANSWER_REFUSED = frozenset(("failed", "too_large"))


class TicketResult(NamedTuple):
    """A finished ticket's answer. A relayed child answer: code None, the child's status
    and body byte-exact. A refusal: the closed-table code and its status, body None (the
    handler renders the error body). An unload's 200: code None, JSON body."""

    status: int
    code: Optional[str]
    headers: Tuple[Tuple[str, str], ...]
    body: Optional[bytes]


def _sd_ticket_error(code: str) -> TicketResult:
    """A refusal result for closed-table *code* (KeyError on a code outside the table)."""
    return TicketResult(_SD_ERROR_STATUS[code], code, (), None)


def _sd_unload_result(unloaded: bool, model: Optional[str]) -> TicketResult:
    """An unload's 200: {"unloaded": bool, "model": name | null}."""
    body = json.dumps({"unloaded": bool(unloaded), "model": model}).encode("utf-8")
    return TicketResult(200, None, (("Content-Type", "application/json"),), body)


class Reservation:
    """One image admission slot from Scheduler.try_admit(); released at most once."""

    __slots__ = ("released",)

    def __init__(self) -> None:
        self.released = False


class Ticket:
    """One unit of scheduler work: an image request or an unload.

    attempts counts SENDS so far (the first send makes it 1, FR-10 M9). abort_reason
    (None | "force" | "client" | "shutdown" | "timeout") is written under the scheduler
    lock BEFORE the upstream socket is shut down; abort_evt is set with it and is the
    Event _sd_send watches. `sock` is the upstream socket of a running send (set
    through on_socket under the lock). finish() is set-once: its own small lock makes
    the first call win; it is never taken together with the scheduler Condition.
    """

    def __init__(self, kind: str, model: Optional[str], force: bool = False,
                 method: Optional[str] = None, path: Optional[str] = None,
                 headers: Tuple[Tuple[str, str], ...] = (), body: Optional[bytes] = None,
                 reservation: Optional[Reservation] = None) -> None:
        if kind not in ("request", "unload"):
            raise ValueError("Ticket: unknown kind")
        self.kind = kind
        self.model = model
        self.force = bool(force)
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
        self.reservation = reservation
        self.attempts = 0
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.abort_evt = threading.Event()
        self.abort_reason: Optional[str] = None
        self.result: Optional[TicketResult] = None
        self.sock: Optional[socket.socket] = None
        self._finish_lock = threading.Lock()

    def finish(self, result: Optional[TicketResult]) -> bool:
        """Store *result* and set `done`; True the first time, False (no change) after."""
        with self._finish_lock:
            if self.done.is_set():
                return False
            self.result = result
            self.done.set()
            return True

    def view(self) -> TicketView:
        """The plain-data snapshot decide() reads (built under the scheduler lock)."""
        return TicketView(self.kind, self.model, self.force, self.cancelled.is_set(), self.attempts)

    def __repr__(self) -> str:
        return "Ticket(kind=%r, model=%r, force=%r, attempts=%r)" % (
            self.kind, self.model, self.force, self.attempts)


def _sd_shut_socket(sock: Optional[socket.socket]) -> None:
    """shutdown(SHUT_RDWR) an upstream socket (non-blocking; allowed under the lock)."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # already closed or never connected


class Scheduler:
    """The FIFO scheduler: ONE Condition `cond` guards queue, force_slots, running,
    admitted, budgets and every per-model state / group_alive / disabled / restart_at."""

    def __init__(self, settings: Settings, supervisor: Supervisor) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.cond = threading.Condition()
        self.queue: Deque[Ticket] = collections.deque()
        self.force_slots: List[Ticket] = []
        self.running: Optional[Ticket] = None
        self.admitted = 0
        self.budgets: Dict[str, Budget] = {name: Budget() for name in settings.models}
        self.thread: Optional[threading.Thread] = None
        self._started = False
        self.child: Optional[Child] = None  # the resident child (scheduler thread writes it)
        self.closing = False
        # The launch `stop` Event: set only by a force for the STARTING model or by shutdown.
        self.launch_stop = threading.Event()
        self._state: Dict[str, ModelState] = {name: ModelState.STOPPED for name in settings.models}
        self._group_alive: Dict[str, bool] = {name: False for name in settings.models}
        self._disabled: Dict[str, bool] = {name: False for name in settings.models}
        self._restart_at: Dict[str, float] = {name: 0.0 for name in settings.models}
        self._restarts: Dict[str, int] = {name: 0 for name in settings.models}

    # -- helpers; the caller holds `cond` ------------------------------------------------

    def _views_locked(self) -> Dict[str, ModelView]:
        return {name: ModelView(self._state[name], self._group_alive[name], self._disabled[name],
                                self._restart_at[name]) for name in self._state}

    def _clear_model_locked(self, name: str) -> None:
        """An unload re-enables *name*: disabled flag, Budget, backoff (D-8) all cleared."""
        self._disabled[name] = False
        self.budgets[name].clear()
        self._restart_at[name] = 0.0
        self._restarts[name] = 0

    def _release_locked(self, reservation: Optional[Reservation]) -> None:
        if reservation is not None and not reservation.released:
            reservation.released = True
            self.admitted -= 1

    def _abort_locked(self, ticket: Ticket, reason: str) -> None:
        """Abort a popped ticket: abort_reason (first writer wins) and abort_evt BEFORE the
        upstream socket is shut down. force and client also set `cancelled`."""
        if ticket.abort_reason is None:
            ticket.abort_reason = reason
        if reason in ("force", "client"):
            ticket.cancelled.set()
        ticket.abort_evt.set()
        _sd_shut_socket(ticket.sock)

    # -- the public API ------------------------------------------------------------------

    def start(self) -> None:
        """Start the ONE scheduler thread (idempotent; a no-op after shutdown()). The
        thread is started outside the lock and published in `thread` afterwards, so
        shutdown() never joins a thread that was not started."""
        with self.cond:
            if self._started or self.closing:
                return
            self._started = True
        thread = threading.Thread(target=self._run, name="sd-scheduler", daemon=True)
        thread.start()
        with self.cond:
            self.thread = thread

    def states(self) -> Dict[str, ModelView]:
        """Every configured model's ModelView, snapshotted under the lock."""
        with self.cond:
            return self._views_locked()

    def try_admit(self) -> Optional[Reservation]:
        """The ONE image admission (H2): atomic under the lock; None once queued + running
        image tickets already equal settings.max_connections."""
        with self.cond:
            if self.admitted >= self.settings.max_connections:
                return None
            self.admitted += 1
            return Reservation()

    def release(self, reservation: Optional[Reservation]) -> None:
        """Give an admission slot back; idempotent per reservation (None is a no-op)."""
        with self.cond:
            self._release_locked(reservation)

    def submit_request(self, model: str, method: str, path: str,
                       headers: Sequence[Tuple[str, str]], body: bytes,
                       reservation: Optional[Reservation]) -> Ticket:
        """Queue an image request holding an ALREADY-admitted *reservation* (never admits
        by itself). After shutdown() the ticket comes back finished 503 shutting_down."""
        ticket = Ticket("request", model, False, method, path, _sd_forward_headers(headers), body,
                        reservation)
        closed = False
        with self.cond:
            if self.closing:
                closed = True
            else:
                self.queue.append(ticket)
                self.cond.notify_all()
        if closed:
            ticket.finish(_sd_ticket_error("shutting_down"))
        return ticket

    def submit_unload(self, model: Optional[str], force: bool) -> Ticket:
        """An unload; ALWAYS a Ticket with `done`.

        Non-force: queued (FIFO); decide() resolves it at the head. Force with a RESIDENT
        target (*model* None = the resident one): the ticket goes to force_slots, a running
        request for that model is aborted ("force") and a start in progress is stopped;
        the scheduler finishes it after the ladder. Force with a NON-resident target
        (D-17): executed here under the lock -- the running request is untouched, the
        target's disabled flag, Budget and backoff are cleared, nothing is queued -- and
        the ticket returns already finished 200 {"unloaded": false, "model": target}.

        Non-force coalescing (M19): an unload survives its client, so a repeated one is not
        allowed to grow the queue. When the queue's LAST ticket is a non-force unload naming
        the same *model* (None included), that ticket is returned instead of a new one:
        both callers wait on it and read its one result. Only the tail is matched, so FIFO
        order is unchanged (an unload behind a later request still gets its own ticket),
        and the queue holds at most one unload between two requests, which admission bounds.
        """
        ticket = Ticket("unload", model, force)
        result: Optional[TicketResult] = None
        with self.cond:
            if self.closing:
                result = _sd_ticket_error("shutting_down")
            elif not force:
                tail = self.queue[-1] if self.queue else None
                if (tail is not None and tail.kind == "unload" and not tail.force
                        and tail.model == model):
                    return tail
                self.queue.append(ticket)
                self.cond.notify_all()
            else:
                views = self._views_locked()
                target = model if model is not None else _sd_resident_model(views)
                if target is not None and target not in views:
                    result = _sd_ticket_error("model_unknown")
                elif target is None or not _sd_is_resident(views[target]):
                    if target is not None:
                        self._clear_model_locked(target)
                    result = _sd_unload_result(False, target)
                else:
                    ticket.model = target
                    self.force_slots.append(ticket)
                    running = self.running
                    if running is not None and running.kind == "request" and running.model == target:
                        self._abort_locked(running, "force")
                    if self._state[target] is ModelState.STARTING:
                        self.launch_stop.set()
                    self.cond.notify_all()
        if result is not None:
            ticket.finish(result)
        return ticket

    def cancel(self, ticket: Ticket) -> None:
        """The client left. A QUEUED ticket leaves the queue, drops its body, releases its
        reservation and is finished with result None -- one locked section, done before
        this returns. A popped (`running`) ticket gets abort_reason "client"; the scheduler
        drops it and owns its release. A finished ticket is left alone."""
        dropped = False
        with self.cond:
            ticket.cancelled.set()
            if ticket in self.queue:
                self.queue.remove(ticket)
                ticket.body = None
                self._release_locked(ticket.reservation)
                dropped = True
            elif ticket is self.running:
                self._abort_locked(ticket, "client")
            self.cond.notify_all()
        if dropped:
            ticket.finish(None)

    def shutdown(self) -> None:
        """Close the scheduler; idempotent and bounded. Under the lock: no new tickets,
        every queued ticket is finished 503 queue_closed (body dropped, slot released),
        waiting force unloads 503 shutting_down, the popped ticket aborted ("shutdown")
        and a start in progress told to stop. Then the thread finishes any ladder in
        progress, stops the resident child and is joined (bounded); last,
        supervisor.kill_all() sweeps every group still registered."""
        finished: List[Tuple[Ticket, TicketResult]] = []
        with self.cond:
            self.closing = True
            while self.queue:
                ticket = self.queue.popleft()
                ticket.body = None
                self._release_locked(ticket.reservation)
                finished.append((ticket, _sd_ticket_error("queue_closed")))
            while self.force_slots:
                finished.append((self.force_slots.pop(0), _sd_ticket_error("shutting_down")))
            if self.running is not None:
                self._abort_locked(self.running, "shutdown")
            self.launch_stop.set()
            self.cond.notify_all()
            thread = self.thread
        for ticket, result in finished:
            ticket.finish(result)
        if thread is not None and thread is not threading.current_thread():
            grace = max((spec.stop_grace for spec in self.settings.models.values()), default=0.0)
            thread.join(grace + 2.0 * _SD_KILL_STEP_S + _SD_SHUTDOWN_SLACK_S)
        self.supervisor.kill_all()

    # -- the scheduler thread ------------------------------------------------------------

    def _run(self) -> None:
        """The ONE scheduler thread: take the head, execute the decision, repeat until
        shutdown(); then stop the resident child (a ladder in progress is finished first,
        because each step below returns only after its own ladder)."""
        while True:
            with self.cond:
                if self.closing:
                    break
                if not (self.force_slots or self.queue):
                    self.cond.wait(_SD_SCHED_TICK_S)
                    continue
                zombie = self._zombie_locked()
            if zombie is not None:
                self._reprobe(zombie[0], zombie[1])
            step = self._take()
            if step is None:
                continue
            head, decision = step
            try:
                self._execute(head, decision)
            except Exception as exc:  # noqa: BLE001 -- the thread must survive one bad step
                log.error("sd scheduler step failed: action=%s error=%s",
                          decision.action, type(exc).__name__)
                self._finish(head, _sd_ticket_error("upstream_failed"))
        self._exit_stop()

    def _zombie_locked(self) -> Optional[Tuple[str, int]]:
        """(model, pgid) of a DEAD model whose group survived its ladder, or None."""
        child = self.child
        if child is None:
            return None
        name = child.spec.name
        if self._state.get(name) is ModelState.DEAD and self._group_alive.get(name):
            return name, child.pgid
        return None

    def _reprobe(self, name: str, pgid: int) -> None:
        """Re-probe a zombie group with signal 0 (outside the lock, M2): once it is gone
        (ESRCH, or EPERM: no longer ours) the zombie clears and the model is STOPPED."""
        if _signal_group(pgid, 0):
            return
        self.supervisor.registry.discard(pgid)
        cleared = False
        with self.cond:
            child = self.child
            if (child is not None and child.pgid == pgid and self._state[name] is ModelState.DEAD
                    and self._group_alive[name]):
                self._state[name] = ModelState.STOPPED
                self._group_alive[name] = False
                self.child = None
                cleared = True
        if cleared:
            log.info("sd zombie group gone: model=%s pgid=%d", name, pgid)

    def _take(self) -> Optional[Tuple[Ticket, Decision]]:
        """Pop the head and decide on it in ONE locked section; a "start" also writes
        STOPPED -> STARTING there (final M3). None when nothing is runnable now (an idle
        decision, e.g. a restart backoff, waits one tick with the head left queued)."""
        dropped: List[Ticket] = []
        taken: Optional[Tuple[Ticket, Decision]] = None
        with self.cond:
            if self.closing:
                return None
            views = self._views_locked()
            resident = _sd_resident_model(views)
            source, index = _sd_pick([t.view() for t in self.force_slots],
                                     [t.view() for t in self.queue])
            if source != "force":
                for _ in range(index):
                    ticket = self.queue.popleft()  # a cancelled ticket: never decided on
                    ticket.body = None
                    self._release_locked(ticket.reservation)
                    dropped.append(ticket)
            if source is None:
                self.cond.wait(_SD_SCHED_TICK_S)
            else:
                candidate = self.force_slots[0] if source == "force" else self.queue[0]
                decision = decide(candidate.view(), views, resident, _sd_now())
                if decision.action == "idle":
                    self.cond.wait(_SD_SCHED_TICK_S)
                else:
                    head = self.force_slots.pop(0) if source == "force" else self.queue.popleft()
                    self.running = head
                    if decision.action == "start" and decision.model is not None:
                        self._state[decision.model] = ModelState.STARTING
                        self.launch_stop.clear()
                    taken = (head, decision)
        for ticket in dropped:
            ticket.finish(None)
        return taken

    def _finish(self, ticket: Ticket, result: Optional[TicketResult]) -> None:
        """Hand *result* to the waiting handler (which releases the reservation after it
        wrote the answer); the body is dropped and `running` cleared under the lock. A
        client abort that landed after the caller's last abort check means the handler is
        gone: the ticket is then dropped instead (released here, result None), so the
        reservation never outlives the departed client."""
        with self.cond:
            ticket.body = None
            ticket.sock = None
            if self.running is ticket:
                self.running = None
            if ticket.abort_reason == "client":
                self._release_locked(ticket.reservation)
                result = None
        ticket.finish(result)

    def _drop(self, ticket: Ticket) -> None:
        """A departed client's popped ticket: body dropped and the reservation released BY
        THE SCHEDULER (the handler is gone), result None."""
        with self.cond:
            ticket.body = None
            ticket.sock = None
            self._release_locked(ticket.reservation)
            if self.running is ticket:
                self.running = None
        ticket.finish(None)

    def _ladder(self, name: str, after: ModelState = ModelState.STOPPED,
                stopping: bool = True) -> bool:
        """Run the stop ladder on *name*'s resident child, lock released. True once the
        group is confirmed gone: the model becomes *after* (group_alive False) and every
        force unload waiting for it is finished 200 {"unloaded": true} (coalesced; a
        disabled model is re-enabled with it, _reenable_locked). False: the group
        survived -- DEAD with group_alive True, a zombie that stays resident (M2)."""
        with self.cond:
            if stopping:
                self._state[name] = ModelState.STOPPING
            child = self.child
        ok = True
        if child is not None and child.spec.name == name:
            ok = self.supervisor.stop(child)
        forced: List[Ticket] = []
        with self.cond:
            if ok:
                self._state[name] = after
                self._group_alive[name] = False
                if self.child is child:
                    self.child = None
                keep: List[Ticket] = []
                for ticket in self.force_slots:
                    (forced if ticket.model == name else keep).append(ticket)
                self.force_slots[:] = keep
                if forced:
                    self._reenable_locked(name)
            else:
                self._state[name] = ModelState.DEAD
                self._group_alive[name] = True
        for ticket in forced:
            ticket.finish(_sd_unload_result(True, name))
        if not ok:
            log.warning("sd model zombie: model=%s", name)
        return ok

    def _execute(self, head: Ticket, decision: Decision) -> None:
        """Execute one decide() outcome for the popped *head* (lock NOT held)."""
        action, target = decision.action, decision.model
        if action == "fail":
            self._finish(head, _sd_ticket_error(decision.code or "model_unknown"))
            return
        if action == "stop":
            self._exec_unload(head, decision)
            return
        if target is None:
            self._finish(head, _sd_ticket_error("model_unknown"))
            return
        if action == "swap":
            if not self._exec_swap(head, target):
                return
            action = "start"
        if action == "start" and not self._exec_start(head, target):
            return
        self._exec_send(head, target)

    def _exec_unload(self, head: Ticket, decision: Decision) -> None:
        """An unload at the head: a non-resident target answers {"unloaded": false} at once
        and is re-enabled; a resident one (a zombie too) runs the ladder and answers
        {"unloaded": true} only after the group is confirmed gone, else 500 unload_failed."""
        target = decision.model
        if decision.code == _SD_NOT_RESIDENT or target is None:
            with self.cond:
                if target is not None:
                    self._clear_model_locked(target)
            self._finish(head, _sd_unload_result(False, target))
            return
        ok = self._ladder(target)
        if ok:
            with self.cond:
                self._reenable_locked(target)
        self._finish(head, _sd_unload_result(True, target) if ok else _sd_ticket_error("unload_failed"))

    def _exec_swap(self, head: Ticket, target: str) -> bool:
        """Stop the resident model, then write STOPPED -> STARTING for *target* in one
        locked section. False when the head was finished or dropped instead (the group
        survived, shutdown, or the client left during the swap-wait: no start then)."""
        with self.cond:
            old = _sd_resident_model(self._views_locked())
        if old is not None and old != target and not self._ladder(old):
            self._finish(head, _sd_ticket_error("unload_failed"))
            return False
        outcome = "start"
        with self.cond:
            if self.closing or head.abort_reason == "shutdown":
                outcome = "shutdown"
            elif head.cancelled.is_set():
                outcome = "drop"
            else:
                self._state[target] = ModelState.STARTING
                self.launch_stop.clear()
        if outcome == "shutdown":
            self._finish(head, _sd_ticket_error("shutting_down"))
            return False
        if outcome == "drop":
            self._drop(head)
            return False
        return True

    def _reenable_locked(self, name: str) -> None:
        """After a RESIDENT model's ladder confirmed its group gone: a disabled model (a
        disabled zombie) is re-enabled -- flag, Budget and backoff cleared. A healthy one
        keeps its crash history: stopping it is not a reason to forgive crashes (T10);
        the non-resident unload paths always clear (D-17, decide's not_resident)."""
        if self._disabled[name]:
            self._clear_model_locked(name)

    def _charge_locked(self, name: str) -> bool:
        """One crash for *name*: Budget.record, the disabled flag when it trips, and the
        backoff before the next automatic restart. True when the budget tripped."""
        tripped = self.budgets[name].record()
        if tripped:
            self._disabled[name] = True
        self._restarts[name] += 1
        self._restart_at[name] = _sd_now() + _sd_backoff_s(self._restarts[name])
        return tripped

    def _exec_start(self, head: Ticket, target: str) -> bool:
        """Launch *target* (already STARTING) and wait for readiness, lock released. True
        when the head may be sent; otherwise it was finished or dropped here. A client
        cancel never stops the start: the started model stays READY (final M1)."""
        spec = self.settings.models[target]
        outcome, child = self.supervisor.launch(spec, _sd_now() + spec.start_timeout,
                                                self.launch_stop)
        if outcome == "ready" and child is not None:
            with self.cond:
                self._state[target] = ModelState.READY
                self._group_alive[target] = False
                self.child = child
                reason = head.abort_reason
                closing = self.closing
            if closing or reason == "shutdown":
                self._finish(head, _sd_ticket_error("shutting_down"))
                return False
            if reason == "force":
                self._ladder(target)
                self._finish(head, _sd_ticket_error("unload_forced"))
                return False
            if head.cancelled.is_set():
                self._drop(head)
                return False
            return True
        forced: List[Ticket] = []
        tripped = False
        with self.cond:
            reason = head.abort_reason
            crash = not (reason in ("force", "shutdown") or self.closing)
            if crash:
                self._state[target] = ModelState.DEAD  # launch() already ran the ladder
                self._group_alive[target] = False
                tripped = self._charge_locked(target)
            else:
                self._state[target] = ModelState.STOPPED
                keep: List[Ticket] = []
                for ticket in self.force_slots:
                    (forced if ticket.model == target else keep).append(ticket)
                self.force_slots[:] = keep
                if forced:
                    self._reenable_locked(target)
        for ticket in forced:
            ticket.finish(_sd_unload_result(True, target))
        if not crash:
            code = "unload_forced" if reason == "force" else "shutting_down"
            self._finish(head, _sd_ticket_error(code))
            return False
        log.warning("sd model start failed: model=%s outcome=%s attempts=%d disabled=%s",
                    target, outcome, head.attempts, tripped)
        if head.cancelled.is_set():
            self._drop(head)
        elif tripped:
            self._finish(head, _sd_ticket_error("model_disabled"))
        elif outcome == "timeout":
            self._finish(head, _sd_ticket_error("model_start_timeout"))
        else:
            self._finish(head, _sd_ticket_error("model_start_failed"))
        return False

    def _exec_send(self, head: Ticket, target: str) -> None:
        """Send the head to the READY child (attempts += 1 in the same locked section that
        re-checks cancel/abort: a ticket dropped before its send is never charged)."""
        spec = self.settings.models[target]
        with self.cond:
            reason = head.abort_reason
            cancelled = head.cancelled.is_set()
            closing = self.closing
            child = self.child
            if reason is None and not cancelled and not closing:
                head.attempts += 1
        if reason == "force":
            self._ladder(target)
            self._finish(head, _sd_ticket_error("unload_forced"))
            return
        if closing or reason == "shutdown":
            self._finish(head, _sd_ticket_error("shutting_down"))
            return
        if reason is not None or cancelled:
            self._drop(head)  # the client left before the send: no kill, no charge
            return

        def on_socket(sock: socket.socket) -> None:
            with self.cond:
                head.sock = sock

        try:
            if child is None or child.spec.name != target:
                raise UpstreamError("connect")
            answer = _sd_send(child.port, head.method or "POST", head.path or "/",
                              head.headers, head.body or b"",
                              _sd_now() + spec.request_timeout, head.abort_evt,
                              on_socket=on_socket, response_limit=self.settings.response_limit)
        except UpstreamError as exc:
            self._send_failed(head, target, spec, exc)
            return
        with self.cond:
            head.sock = None
            self._restarts[target] = 0
            reason = head.abort_reason
        if reason == "client":
            self._drop(head)  # the answer is complete: the child is idle, nothing to kill
            return
        status, headers, body = answer
        self._finish(head, TicketResult(status, None, headers, body))

    def _send_failed(self, head: Ticket, target: str, spec: ModelSpec, exc: UpstreamError) -> None:
        """Classify a failed send by abort_reason (H6, H7, M1). None: a CRASH -- DEAD with
        group_alive True, one Budget charge, the ladder, then the SAME ticket back at the
        head while _sd_may_retry allows, else 502 model_crashed. "timeout" (the deadline
        from dispatch): one charge, the ladder, 504 upstream_timeout, never retried.
        "force" / "client" / "shutdown": the ladder only (D-12), never a charge.
        An answer the relay refuses (kind "failed": 1xx, 3xx, encoded, malformed; or
        "too_large": over response_limit) with no abort is NOT a crash: the child
        answered and stays READY, nothing is charged, retried or stopped, and the
        ticket is finished 502 upstream_failed after that one send (closed table, P28)."""
        tripped = False
        with self.cond:
            head.sock = None
            reason = head.abort_reason
            if reason is None and exc.kind == "timeout":
                reason = head.abort_reason = "timeout"
                head.abort_evt.set()
            refused = reason is None and exc.kind in _SD_ANSWER_REFUSED
            crash = reason is None and not refused
            if refused:
                self._restarts[target] = 0  # the child answered: it is alive
            if crash:
                self._state[target] = ModelState.DEAD
                self._group_alive[target] = True
            if crash or reason == "timeout":
                tripped = self._charge_locked(target)
        if refused:
            log.warning("sd upstream answer refused: model=%s kind=%s attempts=%d",
                        target, exc.kind, head.attempts)
            self._finish(head, _sd_ticket_error("upstream_failed"))
            return
        if crash:
            ok = self._ladder(target, after=ModelState.DEAD, stopping=False)
        else:
            ok = self._ladder(target)
        retry = False
        with self.cond:
            late = head.abort_reason
            if (crash and ok and late is None and not self.closing
                    and _sd_may_retry(head.attempts, spec.retries, self._disabled[target])):
                retry = True
                self.queue.appendleft(head)  # the SAME ticket, at the head (FR-10 M9)
                if self.running is head:
                    self.running = None
                self.cond.notify_all()
            closing = self.closing
        if crash or reason == "timeout":
            log.warning("sd upstream failed: model=%s kind=%s attempts=%d retry=%s disabled=%s",
                        target, exc.kind if crash else "timeout", head.attempts, retry, tripped)
        if retry:
            return
        if late == "force":
            self._finish(head, _sd_ticket_error("unload_forced"))
        elif late == "client":
            self._drop(head)
        elif late == "shutdown" or (crash and closing):
            self._finish(head, _sd_ticket_error("shutting_down"))
        elif late == "timeout":
            self._finish(head, _sd_ticket_error("upstream_timeout"))
        else:
            self._finish(head, _sd_ticket_error("model_crashed"))

    def _exit_stop(self) -> None:
        """The thread's last act: stop the resident child, if any (a ladder, lock released)."""
        with self.cond:
            child = self.child
        if child is not None:
            self._ladder(child.spec.name)


# ---- unit 7 ----
# HTTP front
#
# The host half of the stdlib front contract (ADR 0029): the generated members
# (server_bind / process_request / handle_error in the server, parse_request /
# handle_expect_100 / _single_header in the handler) are rendered INSIDE the two
# classes below from Scripts/_mcp_httpfront.py; everything else here is
# hand-written. The seams the generated members read: `front_log`, `settings`
# (its max_connections names the cap in the refusal log line) and `conn_sem` on
# the server; `_header_reader` and the class `timeout` on the handler.
#
# One exchange per connection: every answer carries Connection: close and the
# socket is closed after it (no keep-alive, D-8). Every connection is therefore
# unauthenticated during its header phase, so the one header bound is also the
# pre-auth bound. Routing reads the RAW request target (_sd_raw_target), never
# self.path, which the stdlib of Python 3.11+ normalises (a leading "//").
#
# Host and Origin (decision of task-025; the closed table has no 403): checked
# AFTER the bearer, so the bearer stays the first gate (plan Step 5 order), and
# refused as 400 invalid_request -- a request whose Host does not name this
# loopback listener, or that carries any Origin (sd-router configures no
# allowed origin, so the allowlist llm-router keeps is empty here), is treated
# as a malformed request. The stdlib's own refusals map into the closed table
# too: 501 (an unknown method) is 405 method_not_allowed, every other one
# (400/414/431/505) is 400 invalid_request -- no answer leaves the router with a
# code or a status outside _SD_ERROR_STATUS.

_SD_HEADER_TIMEOUT_S = 5.0  # TOTAL bound on the request line + headers (every connection is pre-auth)
_SD_SOCKET_TIMEOUT_S = 30.0  # per-recv / per-send socket timeout once the precheck passed
_SD_CONTROL_PATHS = ("/v1/models", "/v1/unload")  # the control routes (dispatched by the handlers)

# The stdlib's own refusals (send_error) -> (closed-table code, FIXED message). The
# stdlib's message / explain are never used: they can quote the request line or the method.
_SD_STDLIB_REFUSAL = {
    400: ("invalid_request", "malformed request"),
    414: ("invalid_request", "request line too long"),
    431: ("invalid_request", "header block too large"),
    501: ("method_not_allowed", "method not supported"),
    505: ("invalid_request", "HTTP version not supported"),
}
_SD_STDLIB_REFUSAL_DEFAULT = ("invalid_request", "malformed request")

# Header-parser defects (by class name; email.errors is not imported) a multipart
# Content-Type records on a header block that has no body: they say nothing about
# the headers, so they are tolerated when the Content-Type is multipart (llm-router
# _RT_MULTIPART_DEFECTS). Any other defect is a malformed header (CWE-444).
_SD_MULTIPART_DEFECTS = frozenset({"NoBoundaryInMultipartDefect", "StartBoundaryNotFoundDefect",
                                   "MultipartInvariantViolationDefect"})
_SD_BEARER_SCHEME = "bearer "  # matched case-insensitively, then the token (exactly one space)


def _sd_is_loopback(host: str) -> bool:
    """True when *host* (the bind address) is a loopback IP address."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_httpfront.py :: _http_write_ready_file, _http_remove_ready_file
def _http_write_ready_file(path: str, port: int, error) -> None:
    """{"port", "pid"} written 0600 and atomically (a unique temp + os.replace); a
    failure raises the host's *error* class naming the strerror or the type only.

    The temp is `<path>.<pid>.<8 hex>` in the same directory, created O_EXCL and
    O_NOFOLLOW, so a planted name is never removed, followed or reused (CWE-377);
    only this call's own temp is removed when anything before the replace fails."""
    tmp = "%s.%d.%s" % (path, os.getpid(), os.urandom(4).hex())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"port": port, "pid": os.getpid()}, fh)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
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
# END GENERATED: 0bbed5d158d9


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


class _SdHttpServer(ThreadingHTTPServer):
    """The listener: one daemon thread per connection, at most settings.max_connections
    + _SD_CONTROL_RESERVE of them (H8: image traffic is capped at max_connections by the
    scheduler's admission, so the reserve stays free for /v1/models and /v1/unload);
    never joined on close. It binds in __init__ (port 0 = ephemeral; `server_port`
    after the bind). It never starts or stops `scheduler`: the caller does both.
    The shutdown drain waits on `active` / `active_cond`; `closing` tells handlers
    the router is stopping."""

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    # The generated server methods log through this, never the module global (R-0072, PD-3).
    front_log = log

    def __init__(self, server_address: Tuple[str, int], settings: Settings, scheduler: "Scheduler") -> None:
        # Everything a handler reads is set BEFORE super().__init__, which binds and listens.
        self.settings = settings
        self.scheduler = scheduler
        self.conn_sem = threading.BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE)
        self.closing = threading.Event()
        self.loopback = _sd_is_loopback(server_address[0])
        # The /v1/models `created` of every entry (llm-router's started_epoch).
        self.started_epoch = int(time.time())
        # Running handler threads, for the shutdown drain.
        self.active = 0
        self.active_cond = threading.Condition()
        super().__init__(server_address, _SdHandler)

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
        # The slot the generated process_request took is given back here, in a
        # finally, whatever the handler did (tests/test_generated_region.py GI1).
        with self.active_cond:
            self.active += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.active_cond:
                self.active -= 1
                self.active_cond.notify_all()
            self.conn_sem.release()


class _SdHandler(BaseHTTPRequestHandler):
    """One exchange per connection; every answer is OpenAI-shaped JSON or a relayed
    child answer, every refusal a closed-table code. Runs on a listener thread and
    owns only its socket; the scheduler thread never touches it."""

    protocol_version = "HTTP/1.1"
    server_version = "sd-router"
    sys_version = ""
    # Applied by StreamRequestHandler.setup(): a connection that stalls in the
    # request line or headers is closed by the stdlib (CWE-400). parse_request
    # (generated) resets the per-recv timeout to it after the header phase.
    timeout = _SD_HEADER_TIMEOUT_S

    def setup(self) -> None:
        super().setup()
        # Replace the stdlib's rfile with one whose raw reader enforces the total
        # header deadline. The stdlib's own is closed at once: an unclosed
        # makefile keeps the socket's fd alive after close_request (P24).
        stock = self.rfile
        # the cap is the socket timeout: the body read sets its own deadline on this reader too
        self._header_reader = _HeaderDeadlineReader(self.connection, _SD_SOCKET_TIMEOUT_S)
        self.rfile = io.BufferedReader(self._header_reader, io.DEFAULT_BUFFER_SIZE)
        stock.close()

    def handle_one_request(self) -> None:
        # The request line + headers within _SD_HEADER_TIMEOUT_S in TOTAL (F8),
        # read at call time so a test rig can patch it. ONE exchange per
        # connection: close_connection is forced, so the stdlib's handle() loop
        # never parses a pipelined second request.
        bound = _SD_HEADER_TIMEOUT_S
        self._header_reader.deadline = time.monotonic() + bound
        self.connection.settimeout(bound)
        try:
            super().handle_one_request()
        finally:
            self._header_reader.deadline = None
            self.close_connection = True

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

    def _sd_raw_target(self) -> str:
        """The request target exactly as the client sent it: the second word of the
        request line (never self.path, which the stdlib may normalise); "" when absent."""
        words = (getattr(self, "requestline", "") or "").split()
        return words[1] if len(words) >= 2 else ""

    def _sd_log_path(self) -> str:
        """The request path as a log may show it: a routed shape only (a ROUTES key, a
        control path, either under /m/<configured model>/), else "<unrouted>" -- any
        other target is unauthenticated client text and may carry a token."""
        path = self._sd_raw_target().split("?", 1)[0]
        settings = getattr(self.server, "settings", None)
        models = settings.models if settings is not None else {}
        rest = path
        prefix = ""
        if path.startswith(_SD_PREFIX):
            model, sep, tail = path[len(_SD_PREFIX):].partition("/")
            if not sep or model not in models:
                return "<unrouted>"
            prefix = _SD_PREFIX + model
            rest = "/" + tail
        if rest in ROUTES or rest in _SD_CONTROL_PATHS:
            return prefix + rest
        return "<unrouted>"

    def log_message(self, format, *args) -> None:  # noqa: A002 -- stdlib signature
        # Structure only (ADR 0011): never the stdlib's `format % args` text,
        # which can quote the raw request line; never headers, query or body.
        # Called before command / requestline exist on a timeout or a malformed line.
        if log.isEnabledFor(logging.DEBUG):
            command = getattr(self, "command", None) or "-"
            if command != "-" and not hasattr(self, "do_" + command):
                command = "<other>"
            log.debug("http %s %s", _log_value(command), _log_value(self._sd_log_path()))

    def _refuse(self, code: str, message: str, extra_headers: Sequence[Tuple[str, str]] = ()) -> None:
        """Answer closed-table *code* (the _sd_error envelope and status) and close: used
        for every refusal, so unread body bytes are never parsed as another request.
        *message* is a FIXED rule text, never a value. A HEAD gets the head only.

        A 401 is logged at WARNING with the status and the peer address only -- never a
        header value, never the presented token.

        HTTP/0.9 guard (llm-router M1): parse_request sets request_version to "HTTP/0.9"
        before it parses the version word, and send_response_only writes no status line
        and no header for HTTP/0.9; every refusal is therefore sent with an HTTP/1.1 head.
        """
        if getattr(self, "request_version", None) == "HTTP/0.9":
            self.request_version = "HTTP/1.1"
        status, body = _sd_error(code, message)
        if getattr(self, "command", None) == "HEAD":
            body = b""
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
        if status == 401:
            log.warning("http refused status=%d peer=%s", status, _log_value(self.client_address[0]))
        else:
            log.debug("http refused status=%d code=%s", status, _log_value(code))

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        """The stdlib's own refusals (400/414/431/501/505), mapped into the closed table
        (_SD_STDLIB_REFUSAL) with a FIXED text: `message` and `explain` are ignored, since
        they can quote the request line or the method. nosniff and no-store are added;
        the HTTP/0.9 guard, Content-Length and Connection: close come from _refuse."""
        self.close_connection = True
        sd_code, text = _SD_STDLIB_REFUSAL.get(code, _SD_STDLIB_REFUSAL_DEFAULT)
        self._refuse(sd_code, text, _HTTP_STDLIB_REFUSAL_HEADERS)

    def _sd_bearer_ok(self) -> bool:
        """Exactly ONE Authorization header whose value is `Bearer ` (scheme case-insensitive,
        then exactly one space) + the token, compared as UTF-8 bytes with hmac.compare_digest
        (S-L1, S-L2). Absent, repeated, another scheme, empty, extra whitespace or text, a
        non-ASCII byte: False, never an exception. The token is never read from the query
        or the path."""
        ok, value = self._single_header("Authorization")
        presented = b""
        if ok and value is not None and value[:len(_SD_BEARER_SCHEME)].lower() == _SD_BEARER_SCHEME:
            try:
                presented = value[len(_SD_BEARER_SCHEME):].encode("utf-8", "surrogateescape")
            except UnicodeEncodeError:
                presented = b""
        expected = self.server.settings.token.encode("utf-8")
        # compare_digest runs on every path (an empty presented value never matches: the
        # token is at least _SD_TOKEN_MIN_LEN characters).
        matched = hmac.compare_digest(presented, expected)
        return matched and bool(presented)

    def _precheck(self) -> bool:
        """The checks every route shares, on the request line and headers ONLY -- never
        the body, so an unauthenticated client cannot make the router read or wait for
        its declared body. Order: 1 bearer (401 unauthorized, WWW-Authenticate: Bearer),
        2 header defects, 3 Host (loopback bind only), 4 Origin (2-4: 400
        invalid_request). Answers the refusal itself and returns False on the first
        failure; on success the header phase is over and the socket timeout is raised."""
        if not self._sd_bearer_ok():
            self._refuse("unauthorized", "a valid bearer token is required", (("WWW-Authenticate", "Bearer"),))
            return False
        tolerated = _SD_MULTIPART_DEFECTS if self.headers.get_content_maintype() == "multipart" else frozenset()
        if any(type(defect).__name__ not in tolerated for defect in self.headers.defects):
            self._refuse("invalid_request", "malformed header")
            return False
        if self.server.loopback:
            hosts = self.headers.get_all("Host") or []
            port = self.server.server_port
            if len(hosts) != 1 or hosts[0].strip().lower() not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
                self._refuse("invalid_request", "the Host header must name this loopback listener")
                return False
        if self.headers.get_all("Origin"):
            self._refuse("invalid_request", "an Origin header is not accepted")
            return False
        self.connection.settimeout(_SD_SOCKET_TIMEOUT_S)
        return True


    # The verbs: every one runs the same pipeline (unit 8), so a wrong method on an image
    # path is a 405 AFTER the bearer, never the stdlib's pre-auth 501.
    def do_POST(self) -> None:  # noqa: N802 -- stdlib dispatch name
        _sd_handle(self, "POST")

    def do_GET(self) -> None:  # noqa: N802
        _sd_handle(self, "GET")

    def do_PUT(self) -> None:  # noqa: N802
        _sd_handle(self, "PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        _sd_handle(self, "DELETE")


# ---- unit 8 ----
# handlers
#
# The closed error table renderer and the image-route pipeline. _SdHandler's verbs
# (unit 7) call _sd_handle, which runs, in this order (plan Step 5, round-2 H2):
#   1 _precheck: the bearer first (401), then header defects, Host, Origin;
#     a stopping router answers 503 shutting_down;
#   2 control paths (/v1/models, /v1/unload, bare or under /m/<model>/) leave the
#     pipeline here for _sd_control, BEFORE routing: 405 on a wrong verb, the 4 KiB
#     control framing (D-14), the strict unload query (D-16), never a reservation;
#   3 _sd_route_head on the RAW target: 404 / 405 / 400 query / 404 prefix model /
#     400 model_required;
#   4 header-only framing: any Transfer-Encoding 411, exactly one decimal
#     Content-Length 411, > body_limit 413, the route's Content-Types 415 -- no
#     reservation is held for any of these;
#   5 scheduler.try_admit(): None is 503 queue_full + Retry-After: 1, body unread;
#   6 Expect: 100-continue answered only now;
#   7 the body read under _SD_BODY_DEADLINE_S (read at call time): expiry 400
#     invalid_request, slot released, nothing sent upstream;
#   8 un-prefixed: route_request (the sniffer) picks the model;
#   9 submit_request with the reservation, then the wait on ticket.done with a
#     _SD_CLIENT_POLL_S client-gone poll (cancel on departure), then the answer.
# Release rule (D-12, final M4): the handler releases the reservation in its finally
# when the ticket was never submitted or is finished (a completed ticket: after the
# answer was written; release() is idempotent, so a ticket cancel() or the scheduler
# already released is a no-op). A ticket still running when the handler leaves is
# cancelled and its release is the scheduler's (it drops the ticket after the ladder).

_SD_CLIENT_POLL_S = 0.5  # the client-gone poll while a ticket waits
_SD_LENGTH_RE = re.compile(r"[0-9]{1,19}")  # one ASCII decimal Content-Length

# The Content-Types each image route takes (media type, parameters ignored here; the
# sniffer checks a multipart boundary itself).
_SD_ROUTE_MEDIA: Mapping[str, Tuple[str, ...]] = types.MappingProxyType({
    "/v1/images/generations": ("application/json",),
    "/v1/images/edits": ("application/json", "multipart/form-data"),
    "/sdapi/v1/txt2img": ("application/json",),
    "/sdapi/v1/img2img": ("application/json",),
})

# The OpenAI `type` of each closed-table status class; every status of the table maps.
_SD_ERROR_TYPE = {
    400: "invalid_request_error",
    401: "authentication_error",
    404: "not_found_error",
    405: "invalid_request_error",
    411: "invalid_request_error",
    413: "invalid_request_error",
    415: "invalid_request_error",
    500: "server_error",
    502: "server_error",
    503: "service_unavailable_error",
    504: "timeout_error",
}

# The FIXED message of a code the scheduler hands back (a TicketResult carries the code
# only); a message names a rule, never a value.
_SD_ERROR_TEXT = {
    "model_unknown": "the model names no configured model",
    "model_crashed": "the model server crashed and the retries are spent",
    "model_start_failed": "the model server exited before it became ready",
    "model_start_timeout": "the model server did not become ready within start_timeout",
    "model_disabled": "the model is disabled after repeated crashes; an unload re-enables it",
    "unload_forced": "the request was aborted by a forced unload",
    "unload_failed": "a model server process group survived SIGKILL; the GPU is not free",
    "upstream_timeout": "the model server did not answer within request_timeout",
    "queue_full": "too many image requests queued; retry shortly",
    "upstream_failed": "the model server answer could not be relayed",
    "shutting_down": "the router is shutting down",
    "queue_closed": "the router closed the queue",
}
_SD_ERROR_TEXT_DEFAULT = "the request was refused"


def _sd_error(code: str, message: str) -> Tuple[int, bytes]:
    """(status, body) of closed-table *code*: the body is the OpenAI envelope
    {"error": {"message", "type", "code"}} as UTF-8 JSON. A code outside
    _SD_ERROR_STATUS raises KeyError: no answer leaves the router with another code."""
    status = _SD_ERROR_STATUS[code]
    body = json.dumps({"error": {"message": str(message), "type": _SD_ERROR_TYPE[status],
                                 "code": code}}).encode("utf-8")
    return status, body


def _sd_reply_refuse(handler: "_SdHandler", code: str, message: str,
                     extra_headers: Sequence[Tuple[str, str]] = ()) -> None:
    """A pipeline refusal: marks the exchange answered, then _refuse."""
    handler._sd_replied = True
    handler._refuse(code, message, extra_headers)


def _sd_write_result(handler: "_SdHandler", result: Optional[TicketResult]) -> None:
    """Write a finished ticket's answer. A refusal (code set) through _sd_error with its
    fixed text; a relayed answer as its status, its Content-Type, X-Content-Type-Options:
    nosniff, our own Content-Length and Connection: close, the body byte-exact. None (a dropped ticket: the client left)
    writes nothing."""
    if result is None:
        handler.close_connection = True
        return
    if result.code is not None:
        extra = (("Retry-After", "1"),) if result.code == "queue_full" else ()
        _sd_reply_refuse(handler, result.code, _SD_ERROR_TEXT.get(result.code, _SD_ERROR_TEXT_DEFAULT), extra)
        return
    body = result.body or b""
    handler._sd_replied = True
    handler.send_response(result.status)
    for name, value in result.headers:
        if name.lower() == "content-type":
            handler.send_header("Content-Type", value)
            break
    handler.send_header("X-Content-Type-Options", "nosniff")  # M7: no sniffing a relayed body
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if body and handler.command != "HEAD":
        handler.wfile.write(body)
    handler.close_connection = True
    log.debug("http answer status=%d", result.status)


def _sd_client_gone(handler: "_SdHandler") -> bool:
    """Client-EOF poll while nothing is written (adapted copy of llm-router's
    _rt_client_gone): readable plus a peeked b"" (or an OSError) means the client left.
    Never consumes a byte, so a pipelined request's bytes do not count as a departure."""
    try:
        readable, _w, _x = select.select([handler.connection], [], [], 0)
        if not readable:
            return False
        return handler.connection.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _sd_control_target(raw: str) -> Optional[Tuple[Optional[str], str, str]]:
    """(prefix model or None, control path, query) when the raw target names a control
    route (/v1/models, /v1/unload, bare or under /m/<name>/), else None. The prefix
    name is NOT validated here: the control handler owns that (404 model_unknown)."""
    path, _qmark, query = raw.partition("?")
    if path in _SD_CONTROL_PATHS:
        return None, path, query
    if path.startswith(_SD_PREFIX):
        model, sep, tail = path[len(_SD_PREFIX):].partition("/")
        if model and sep and ("/" + tail) in _SD_CONTROL_PATHS:
            return model, "/" + tail, query
    return None


_SD_CONTROL_BODY_LIMIT = 4096  # D-14: the largest control request body, in bytes
_SD_CONTROL_METHOD: Mapping[str, str] = types.MappingProxyType({
    "/v1/models": "GET",
    "/v1/unload": "POST",
})
_SD_UNLOAD_BODIES = (b"", b"{}")  # the only unload bodies: the body never selects a model
_SD_UNLOAD_BAD_QUERY = RouteError("invalid_request", "the unload query takes model=<name> and force=1, each at most once")


def _sd_unload_query(query: str) -> Union[Tuple[Optional[str], bool], RouteError]:
    """(model or None, force) of a /v1/unload query, or a RouteError; PURE (D-16).

    The raw query text (never percent-decoded) is split on `&`, each element on its
    first `=`. Only the keys `model` and `force`, each at most once; `force` exactly
    `1`; `model` of the config's name form ([A-Za-z0-9._~-]{1,64}). Any `%`, an empty
    element, an element without `=`, an empty value, a duplicate or unknown key, or a
    value of another form is 400 invalid_request. An empty query is the default (no
    model, no force). Membership of the name in the config is the handler's check."""
    if query == "":
        return None, False
    if "%" in query:
        return _SD_UNLOAD_BAD_QUERY
    model: Optional[str] = None
    force = False
    seen = set()
    for element in query.split("&"):
        key, eq, value = element.partition("=")
        if not eq or not value or key not in ("model", "force") or key in seen:
            return _SD_UNLOAD_BAD_QUERY
        seen.add(key)
        if key == "force":
            if value != "1":
                return _SD_UNLOAD_BAD_QUERY
            force = True
        else:
            if not _SD_MODEL_NAME_RE.fullmatch(value):
                return _SD_UNLOAD_BAD_QUERY
            model = value
    return model, force


def _sd_json_answer(handler: "_SdHandler", doc: Any) -> None:
    """A 200 application/json answer of *doc* (our own Content-Length, Connection: close)."""
    body = json.dumps(doc).encode("utf-8")
    _sd_write_result(handler, TicketResult(200, None, (("Content-Type", "application/json"),), body))


def _sd_control_body(handler: "_SdHandler") -> Optional[bytes]:
    """The control-route framing and body read (D-14): any Transfer-Encoding, or a
    repeated or malformed Content-Length, is 411; a declared length over
    _SD_CONTROL_BODY_LIMIT is 413 with no body byte read; no Content-Length is an empty
    body. Never a reservation (H8). None after a refusal."""
    if handler.headers.get_all("Transfer-Encoding"):
        _sd_reply_refuse(handler, "length_required", "Content-Length is required; Transfer-Encoding is not supported")
        return None
    lengths = handler.headers.get_all("Content-Length") or []
    if len(lengths) > 1 or (lengths and not _SD_LENGTH_RE.fullmatch(lengths[0].strip())):
        _sd_reply_refuse(handler, "length_required", "at most one decimal Content-Length is accepted")
        return None
    length = int(lengths[0].strip()) if lengths else 0
    if length > _SD_CONTROL_BODY_LIMIT:
        _sd_reply_refuse(handler, "body_too_large", "a control request body is over the control body limit")
        return None
    if length and ((handler.headers.get("Expect") or "").strip().lower() == "100-continue"
                   and handler.request_version >= "HTTP/1.1"):
        handler.send_response_only(100)
        handler.end_headers()
        handler.wfile.flush()
    return _sd_read_body(handler, length, time.monotonic())


def _sd_models_entry(name: str, view: ModelView, created: int) -> Dict[str, Any]:
    """One /v1/models entry: llm-router's image-route keys (id, object, created,
    owned_by, modality) plus `state` -- "disabled" when the flag is set and the model
    is not resident, else the ModelState name lower-cased. Never command, args, env,
    cwd, port or pid."""
    state = "disabled" if view.disabled and not _sd_is_resident(view) else view.state.name.lower()
    return {"id": name, "object": "model", "created": created, "owned_by": "sd-router",
            "modality": "image", "state": state}


def _sd_handle_models(handler: "_SdHandler") -> None:
    """GET /v1/models: every configured model in config order, its state read from
    scheduler.states() (one locked snapshot)."""
    server = handler.server
    views = server.scheduler.states()
    created = server.started_epoch
    data = [_sd_models_entry(name, views[name], created) for name in server.settings.models]
    _sd_json_answer(handler, {"object": "list", "data": data})


def _sd_handle_unload(handler: "_SdHandler", prefix_model: Optional[str], query: str, body: bytes) -> None:
    """POST /v1/unload and /m/<model>/v1/unload (FR-5, D-5, D-16, D-17).

    Order: the body (empty or exactly `{}`, else 400), the strict query (400), a prefix
    and a ?model= naming different models (400), an unknown target (404 model_unknown,
    BEFORE submit_unload, so it is never a 200). Then submit_unload(target, force) and
    a wait on ticket.done; the scheduler's result is written as built (200 {"unloaded",
    "model"} only after the group is gone, 500 unload_failed, 503 shutting_down). A
    client that leaves while waiting does not cancel the unload: it runs to its end and
    its answer is dropped."""
    if body not in _SD_UNLOAD_BODIES:
        _sd_reply_refuse(handler, "invalid_request", "the unload body must be empty or {}")
        return
    parsed = _sd_unload_query(query)
    if isinstance(parsed, RouteError):
        _sd_reply_refuse(handler, parsed.code, parsed.message)
        return
    query_model, force = parsed
    if prefix_model is not None and query_model is not None and prefix_model != query_model:
        _sd_reply_refuse(handler, "invalid_request", "the /m/<model>/ prefix and ?model= name different models")
        return
    target = prefix_model if prefix_model is not None else query_model
    if target is not None and target not in handler.server.settings.models:
        _sd_reply_refuse(handler, "model_unknown", "the unload names no configured model")
        return
    ticket = handler.server.scheduler.submit_unload(target, force)
    while not ticket.done.wait(_SD_CLIENT_POLL_S):
        if _sd_client_gone(handler):
            log.debug("http client gone while an unload waits: model=%s", _log_value(target))
            handler.close_connection = True
            return
    _sd_write_result(handler, ticket.result)


def _sd_control(handler: "_SdHandler", method: str, model: Optional[str], path: str, query: str) -> None:
    """The control routes (FR-6), reached only after _precheck (bearer first, S-L3) and
    never through try_admit() (H8: they run on the connection reserve). /v1/models takes
    GET, /v1/unload and its /m/<model>/ alias POST; another verb is 405. Then the control
    framing (_sd_control_body), then the route's handler. A prefixed /m/<x>/v1/models
    names no route the plan declares and is answered like the bare one once <x> is a
    configured model (404 model_unknown otherwise)."""
    if method != _SD_CONTROL_METHOD[path]:
        _sd_reply_refuse(handler, "method_not_allowed", "this control route takes %s only" % _SD_CONTROL_METHOD[path])
        return
    body = _sd_control_body(handler)
    if body is None:
        return
    if path == "/v1/models":
        if model is not None and model not in handler.server.settings.models:
            _sd_reply_refuse(handler, "model_unknown", "the /m/<model>/ prefix names no configured model")
            return
        _sd_handle_models(handler)
        return
    _sd_handle_unload(handler, model, query, body)


def _sd_framing(handler: "_SdHandler", upstream: str) -> Optional[Tuple[int, str]]:
    """Step 4, the header-only checks: (Content-Length, Content-Type) or None after a
    refusal (411 / 413 / 400 for a control character in Content-Type / 415). Nothing is
    read and no reservation is held."""
    if handler.headers.get_all("Transfer-Encoding"):
        # The generated front does not refuse it; chunked bodies are not supported (D-8).
        _sd_reply_refuse(handler, "length_required", "Content-Length is required; Transfer-Encoding is not supported")
        return None
    lengths = handler.headers.get_all("Content-Length") or []
    if len(lengths) != 1 or not _SD_LENGTH_RE.fullmatch(lengths[0].strip()):
        _sd_reply_refuse(handler, "length_required", "exactly one decimal Content-Length is required")
        return None
    length = int(lengths[0].strip())
    if length > handler.server.settings.body_limit:
        _sd_reply_refuse(handler, "body_too_large", "the request body is over the body limit")
        return None
    ok, ctype = handler._single_header("Content-Type")
    if ok and ctype and _sd_has_ctl(ctype):
        # Every path, the /m/<model>/ prefix included: that value crosses the hop (M6, M23).
        _sd_reply_refuse(handler, "invalid_request", "the Content-Type must not contain a control character")
        return None
    media = (ctype or "").split(";", 1)[0].strip().lower() if ok else ""
    allowed = _SD_ROUTE_MEDIA[upstream]
    if media not in allowed:
        _sd_reply_refuse(handler, "unsupported_media_type", "Content-Type must be %s" % " or ".join(allowed))
        return None
    return length, ctype or ""


def _sd_read_body(handler: "_SdHandler", length: int, started: float) -> Optional[bytes]:
    """Step 7: exactly *length* body bytes under the TOTAL deadline _SD_BODY_DEADLINE_S
    (the module value at call time, D-15) measured from *started*, the end of the header
    phase. Expiry or a short body: 400 invalid_request (best effort) and None."""
    reader = handler._header_reader
    reader.deadline = started + _SD_BODY_DEADLINE_S
    expired = False
    raw = b""
    try:
        raw = handler.rfile.read(length) if length else b""
    except socket.timeout:
        expired = True
    finally:
        reader.deadline = None
        handler.connection.settimeout(_SD_SOCKET_TIMEOUT_S)
    if expired:
        log.debug("http body deadline passed")
        _sd_reply_refuse(handler, "invalid_request", "the request body did not arrive within the body deadline")
        return None
    if len(raw) != length:
        handler.close_connection = True  # a short body: the peer is gone or lying
        _sd_reply_refuse(handler, "invalid_request", "the request body is shorter than its Content-Length")
        return None
    return raw


def _sd_handle(handler: "_SdHandler", method: str) -> None:
    """One request through the pipeline (the order is in the unit comment). Every
    failure reaches the client as a closed-table answer while the socket is open (ADR
    0010 spirit): a bug here is 502 upstream_failed, never a silent drop."""
    started = time.monotonic()
    handler._sd_replied = False
    handler._sd_reservation = None
    handler._sd_ticket = None
    try:
        _sd_pipeline(handler, method, started)
    except OSError:
        handler.close_connection = True  # the client went away mid-exchange
    except Exception as exc:  # noqa: BLE001 -- a handler bug must still answer
        log.error("http handler step failed: %s", type(exc).__name__)
        if not handler._sd_replied:
            try:
                _sd_reply_refuse(handler, "upstream_failed", "internal error")
            except OSError:
                pass
        handler.close_connection = True
    finally:
        _sd_settle(handler)


def _sd_settle(handler: "_SdHandler") -> None:
    """The release rule, exactly once per exit path: a ticket still unfinished is
    cancelled (queued: dropped and released at once; running: the scheduler releases
    when it drops it); a ticket never submitted or finished has its reservation
    released here (idempotent)."""
    scheduler = handler.server.scheduler
    ticket = handler._sd_ticket
    reservation = handler._sd_reservation
    if ticket is not None and not ticket.done.is_set():
        scheduler.cancel(ticket)
    if ticket is None or ticket.done.is_set():
        scheduler.release(reservation)


def _sd_pipeline(handler: "_SdHandler", method: str, started: float) -> None:
    """Steps 1-9 for one request (see the unit comment); answers every refusal itself."""
    if not handler._precheck():
        handler._sd_replied = True
        return
    server = handler.server
    if server.closing.is_set():
        _sd_reply_refuse(handler, "shutting_down", _SD_ERROR_TEXT["shutting_down"])
        return
    raw = handler._sd_raw_target()
    control = _sd_control_target(raw)
    if control is not None:
        _sd_control(handler, method, control[0], control[1], control[2])
        return
    models = server.settings.models
    head = _sd_route_head(method, raw, models)
    if isinstance(head, RouteError):
        _sd_reply_refuse(handler, head.code, head.message)
        return
    prefix_model, upstream = head
    framing = _sd_framing(handler, upstream)
    if framing is None:
        return
    length, ctype = framing
    scheduler = server.scheduler
    reservation = scheduler.try_admit()
    if reservation is None:
        _sd_reply_refuse(handler, "queue_full", _SD_ERROR_TEXT["queue_full"], (("Retry-After", "1"),))
        return
    handler._sd_reservation = reservation
    # The interim answer handle_expect_100 withheld, now that auth and admission passed (P32).
    if ((handler.headers.get("Expect") or "").strip().lower() == "100-continue"
            and handler.request_version >= "HTTP/1.1"):
        handler.send_response_only(100)
        handler.end_headers()
        handler.wfile.flush()
    body = _sd_read_body(handler, length, started)
    if body is None:
        return
    model = prefix_model
    if model is None:
        target = route_request(method, raw, ctype, body, models)
        if isinstance(target, RouteError):
            _sd_reply_refuse(handler, target.code, target.message)
            return
        model = target.model
    ticket = scheduler.submit_request(model, "POST", upstream, list(handler.headers.items()), body, reservation)
    handler._sd_ticket = ticket
    body = b""
    while not ticket.done.wait(_SD_CLIENT_POLL_S):
        if _sd_client_gone(handler):
            log.debug("http client gone while waiting: model=%s", _log_value(model))
            scheduler.cancel(ticket)
            handler.close_connection = True
            return
    _sd_write_result(handler, ticket.result)


# ---- unit 9 ----
# main, signals, shutdown
#
# Modelled on llm-router's main / _rt_shutdown: bind first (nothing else has
# started, so a refusal has nothing to stop), the signal handlers before the
# ready file, serve_forever on a daemon thread, main polls the stop flag the
# signal handlers raise (a plain attribute, never an Event.set: M18).
# The ordered shutdown adds the scheduler's own: it finishes a ladder in
# progress, stops the resident child and sweeps the spawn registry.

_SD_MAIN_TICK_S = 0.2  # main's stop-flag poll tick (_sd_wait_stop)
_SD_DRAIN_S = 5.0  # longest wait for running handler threads at shutdown
_SD_TRUST_LINE = ("single-user host assumed: the sd-server child listens unauthenticated on "
                  "127.0.0.1 and is reachable by any local user")
_SD_CLEARTEXT_LINE = "remote bind without TLS: bearer is sent in cleartext"


class _SdStopFlag:
    """The stop request a signal handler raises (M18). The handler only stores an
    attribute: a threading.Event.set() there could deadlock on the Event's own lock,
    which the main thread may hold inside Event.wait() when the signal lands (CPython
    runs the handler in the main thread between bytecodes)."""

    __slots__ = ("raised",)

    def __init__(self) -> None:
        self.raised = False

    def handler(self, _signum: int, _frame: Any) -> None:
        self.raised = True


def _sd_wait_stop(flag: _SdStopFlag, tick: threading.Event) -> None:
    """main's wait: one tick at a time until the signal handler raised *flag*. *tick* is
    never set by a handler; it only paces the loop (_SD_MAIN_TICK_S, read at call time)."""
    while not flag.raised:
        tick.wait(_SD_MAIN_TICK_S)


def _sd_settings(args: argparse.Namespace, cfg: Settings) -> Settings:
    """The config's Settings plus the CLI's limits: --max-connections always; --body-limit
    unless the config carries its own body_limit (the config overrides the flag). A
    config body_limit equal to the default is indistinguishable from an absent one, so
    the flag applies then (declared)."""
    body_limit = cfg.body_limit if cfg.body_limit != _SD_DEFAULT_BODY_LIMIT else args.body_limit
    return dataclasses.replace(cfg, body_limit=body_limit, max_connections=args.max_connections)


def main(argv: Optional[List[str]] = None) -> None:
    args = _sd_parse_args(argv)

    try:
        _configure_logging(args.debug, args.log_file)
    except OSError as exc:
        _sd_refuse(f"--log-file: cannot open it for append ({type(exc).__name__})")

    try:
        settings = _sd_settings(args, load_config(args.config))
    except ConfigError as exc:
        _sd_refuse(str(exc))
    if not args.allow_remote and not _sd_is_loopback(args.bind):
        _sd_refuse("--bind: a non-loopback address needs --allow-remote --insecure-cleartext")

    supervisor = Supervisor()
    scheduler = Scheduler(settings, supervisor)
    # Bind first: nothing else has started, so a refusal has nothing to stop.
    try:
        srv = _SdHttpServer((args.bind, args.port), settings, scheduler)
    except OSError as exc:                       # EADDRINUSE, EADDRNOTAVAIL, EACCES ...
        _sd_refuse(f"--bind/--port: cannot listen ({exc.strerror or type(exc).__name__})")

    # The handlers go in before the ready file: a supervisor that signals as soon
    # as the file appears must reach the ordered shutdown, not the default action.
    stop_flag = _SdStopFlag()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop_flag.handler)

    # Fixed text, no values (S-M1, D-13).
    log.info(_SD_TRUST_LINE)
    if args.allow_remote and args.insecure_cleartext:
        log.warning(_SD_CLEARTEXT_LINE)

    scheduler.start()
    if args.ready_file is not None:
        try:
            _http_write_ready_file(args.ready_file, srv.server_port, ConfigError)
        except ConfigError as exc:
            srv.server_close()
            scheduler.shutdown()
            _sd_refuse(str(exc))

    # serve_forever on its own thread: shutdown() blocks until serve_forever returns,
    # so it is never called from the serving thread.
    threading.Thread(target=srv.serve_forever, name="sd-serve", daemon=True).start()
    log.info("sd-router listening: port=%d models=%d", srv.server_port, len(settings.models))
    _sd_wait_stop(stop_flag, threading.Event())
    _sd_shutdown(srv, scheduler, args.ready_file)
    sys.exit(0)


def _sd_shutdown(srv: _SdHttpServer, scheduler: Scheduler, ready_file: Optional[str]) -> None:
    """The ordered shutdown: closing -> scheduler (ladder, resident stop, registry sweep)
    -> drain -> close -> ready file -> flush. Every step is bounded."""
    log.info("sd-router shutting down")
    # 1. A request accepted from here on gets 503 shutting_down.
    srv.closing.set()
    # 2. Queued tickets are finished queue_closed, the running one is aborted (its
    #    upstream socket shut down), the scheduler finishes any ladder in progress and
    #    stops the resident child (bounded join), then kill_all sweeps the registry
    #    with a signal-0 re-probe before each SIGKILL (NFR-3, M3).
    scheduler.shutdown()
    # 3. Drain the running handlers for at most _SD_DRAIN_S; the rest are daemon
    #    threads and are abandoned (count only).
    with srv.active_cond:
        srv.active_cond.wait_for(lambda: srv.active == 0, timeout=_SD_DRAIN_S)
        left = srv.active
    if left > 0:
        log.warning("shutdown drain abandoned %d handler(s)", left)
    # 4. Close the listener, remove our ready file, then flush every logging handler.
    srv.shutdown()
    srv.server_close()
    if ready_file is not None:
        _http_remove_ready_file(ready_file, log)
    log.info("sd-router stopped")
    for handler in logging.getLogger().handlers + log.handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 -- best effort on the way out
            pass


if __name__ == "__main__":
    main()
