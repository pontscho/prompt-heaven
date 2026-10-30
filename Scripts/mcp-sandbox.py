#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""mcp-sandbox — run a caller's script under sbx's restricted mode.

Single-tool dispatcher pattern: one MCP tool (sandbox_call) routes to internal
handlers via the 'function' parameter -- `run_script` materialises the caller's
files into a fresh work directory, runs ONE configured interpreter (python3,
bash or node) on the entry file through `sbx --restrict-reads` (Seatbelt on
macOS, bwrap on Linux), and answers the exit status, the capped stdout/stderr
and the files the script left under `out/`; `capabilities` reports the
configured interpreters, their plan state, the limits and the backend.

A failing script is a NORMAL result (exitCode != 0, isError false). isError is
reserved for the runner's own refusals, each a top-level {"error": {"code",
"message"}} object (ADR 0010): busy, sandbox_unavailable, invalid_params,
internal.

One JSON config file and no policy flags (ADR 0015: no two spellings of one
setting). The config must be a regular file owned by the euid and not group- or
world-writable; unknown keys are refused at every level and every numeric key
is range-checked.

Usage:
  python3 mcp-sandbox.py --config <path> [--debug] [--log-file <path>]
"""

import argparse
import asyncio
import base64
import binascii
import codecs
import contextvars
import difflib
import errno
import fcntl
import ipaddress
import json
import logging
import math
import os
import platform
import re
import secrets
import select
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

log = logging.getLogger("mcp-sandbox")


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


# Strict JSON on every parse and every frame (P24): finite numbers only and
# bounded integer literals in, allow_nan=False out.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_json.py :: JSON_INT_LITERAL_LIMIT, _json_no_constant, _json_finite_float, _json_bounded_int, _strict_loads, _strict_dumps, _json_error_window, _ensure_dict
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


def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    """Return a repr'd slice of *text* centred on *pos*.

    A JSONDecodeError reports a character offset ("char 1530"), which the
    caller that produced the string cannot count to; the one broken escape is
    only actionable if it is shown. ``repr`` is what makes it visible -- the
    typical defect is a quote escaped one level too shallow, and a raw slice
    renders that identically to a correct one.
    """
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    lead = "..." if start > 0 else ""
    tail = "..." if end < len(text) else ""
    return f"{lead}{text[start:end]!r}{tail}"


def _ensure_dict(value: Any, name: str = "params") -> dict:
    """Coerce *value* to a dict.

    Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
    Raises ValueError on a non-JSON string, JSON that is not an object,
    or any other type.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = _strict_loads(value)
        except json.JSONDecodeError as exc:
            msg = f"'{name}' was a string but not valid JSON: {exc}. "
            msg += f"Near the failure: {_json_error_window(value, exc.pos)}. "
            msg += f"Pass '{name}' as an object, not a JSON-encoded string."
            raise ValueError(msg)
    if not isinstance(value, dict):
        msg = f"'{name}' must be an object (dict) or a JSON-encoded object string; "
        msg += f"got {type(value).__name__}."
        raise ValueError(msg)
    return value
# END GENERATED: 0e6942e0207e


# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_dispatch.py :: _did_you_mean, _unknown_params_refusal
def _did_you_mean(words, candidates, aliases=None):
    """Return " Did you mean 'X'?" for the closest real name, or "".

    `words` is the one name a caller sent (a function) or a list of them (the
    unknown parameter keys of one call). `candidates` are the names actually on
    offer -- the callable functions, or the parameters this function accepts.
    `aliases` maps an alias to its canonical name: an alias of an offered name
    may be MATCHED, and the canonical name is what is suggested, so a near miss
    of an alias still points at the spelling the documentation uses. An alias
    whose canonical is not on offer is not a candidate.

    Only the server's own names are ever rendered for a single word; with
    several words each suggestion says which key it answers. A word that is
    not a string, is empty, is already a candidate, or resembles nothing gets
    no suggestion -- silence is half the contract, since a guess appended to
    every refusal would teach a caller to ignore it. The pool is sorted, so the
    answer depends on the sets, never on the order a table was written in.
    """
    if isinstance(words, str):
        words = [words]
    if not isinstance(words, (list, tuple)):
        return ""
    names = sorted(set(name for name in candidates if isinstance(name, str)))
    table = aliases if isinstance(aliases, dict) else {}
    extra = set(key for key, value in table.items() if isinstance(key, str) and value in names)
    pool = sorted(set(names) | extra)
    pairs = []
    for word in words:
        if not isinstance(word, str) or not word or word in pool:
            continue
        found = difflib.get_close_matches(word, pool, n=1, cutoff=0.6)
        if not found:
            continue
        name = found[0] if found[0] in names else table[found[0]]
        if (word, name) not in pairs:
            pairs.append((word, name))
    if not pairs:
        return ""
    if len(words) == 1:
        return " Did you mean '%s'?" % pairs[0][1]
    return " Did you mean " + ", ".join("'%s' for '%s'" % (name, word) for word, name in pairs) + "?"


def _unknown_params_refusal(function, params, accepted, aliases=None):
    """Return the refusal for the keys of `params` not in `accepted`, or "".

    The sentence is the one the six hosts that refused an unknown parameter
    first already wrote by hand -- "Unknown params for '<fn>': <keys>.
    Accepted: <names>." -- followed by `_did_you_mean`'s pointer, so a caller
    reads the fleet one way. `function` is the canonical name the caller's
    spelling resolved to, `params` the call's params AFTER alias resolution,
    `accepted` the canonical names that function reads, and `aliases` the
    alias table the host's resolver read, so a near miss of an alias is
    answered with the canonical spelling. Both lists are sorted, so the
    answer depends on the sets, never on wire order.

    An empty string means every key is known, so a host calls this
    unconditionally and refuses only on a non-empty answer. It never
    resolves: a near miss is pointed at, and the call is still refused.
    """
    unknown = sorted(str(key) for key in params if key not in accepted)
    if not unknown:
        return ""
    offered = ", ".join(sorted(accepted)) or "(none)"
    head = "Unknown params for '%s': %s. Accepted: %s." % (function, ", ".join(unknown), offered)
    return head + _did_you_mean(unknown, accepted, aliases)
# END GENERATED: 7b6f3ce0b3db


# ---------------------------------------------------------------------------
# KD-17: the timing chain (module constants, never config keys)
# ---------------------------------------------------------------------------
# Every term of a run's wall time is ENFORCED, not a target, and the sum must
# stay strictly inside the next hop's timeout:
#
#   _DEGRADED_RETRY_S (only while degraded) + _REFRESH_DEADLINE_S (one per-run
#   plan_deadline, never re-armed) + max_timeout_ms (<= 120 s) +
#   _HARD_DEADLINE_SLACK_S + _KILL_GRACE_S + _DRAIN_S + _CLEANUP_DEADLINE_S
#   = 2 + 30 + 120 + 10 + 2 + 0.5 + 10 = 174.5 s  <=  _RUN_DEADLINE_S (175)
#   < runner proxy call_timeout 190 < mcp-skill --runner-timeout-s 200
#   < skill proxy call_timeout 220.
#
# Startup is bounded the same way: _STARTUP_SWEEP_DEADLINE_S (20) +
# _STARTUP_PLANS_DEADLINE_S (75) = 95 s < the runner proxy's startup_timeout 120.

# SIGTERM -> SIGKILL grace: the ONE grace, shared by the cancel escalation and
# the hard deadline.
_KILL_GRACE_S = 2.0
# A plan refresh (VSZ probe + --version + self-test), or the wait on another
# run's refresh, measured from the run's plan-phase start.
_REFRESH_DEADLINE_S = 30.0
# Workdir removal (and the opportunistic post-run quarantine retry), checked
# before every mutating operation; a hit deadline quarantines, never blocks.
_CLEANUP_DEADLINE_S = 10.0
# The pre-run quarantine retry, only while the runner is degraded.
_DEGRADED_RETRY_S = 2.0
# What sbx's own timeout may overrun the caller's timeout by before the runner
# kills the group itself.
_HARD_DEADLINE_SLACK_S = 10.0
# Pipe drain after sbx exits (KD-8: never wait for EOF).
_DRAIN_S = 0.5
# The run bound the sum above must fit under.
_RUN_DEADLINE_S = 175.0
# Startup: the work-root sweep, then planning every configured interpreter.
_STARTUP_SWEEP_DEADLINE_S = 20.0
_STARTUP_PLANS_DEADLINE_S = 75.0


# ---------------------------------------------------------------------------
# Types (plan section 3)
# ---------------------------------------------------------------------------

class InterpreterSpec(NamedTuple):
    """One configured interpreter, as the config file names it."""
    name: str                        # "python3" | "bash" | "node"
    path: str                        # absolute spelling, e.g. /usr/local/bin/python3
    extra_reads: Tuple[str, ...]
    site: bool = False               # python3 only: run the site module (no -S)


class InterpreterPlan(NamedTuple):
    """What a run of one interpreter needs; computed at startup, refreshed
    when its signature changes."""
    name: str
    path: str
    realpath: str
    version: str
    argv_prefix: Tuple[str, ...]     # e.g. (path, "-I", "-S", "-B"); no -S when site
    reads: Tuple[str, ...]           # spellings passed as --read (every hop + every target)
    execs: Tuple[str, ...]           # passed as --exec; REALPATHS only (M0:3) + /bin, /usr/bin
    signature: Tuple[Tuple[str, int, int], ...]  # (path, st_ino, st_mtime_ns) per closure file (lstat)
    vsz_baseline: int                # bytes
    as_limit: int                    # bytes


class PlanState(NamedTuple):
    plan: Optional[InterpreterPlan]  # None while refresh-failed
    state: str                       # "ready" | "refresh-failed"
    reason: Optional[str]


class Limits(NamedTuple):
    timeout_ms: int
    max_timeout_ms: int
    as_budget: int
    as_mode: str                     # "baseline" | "absolute"
    nproc_headroom: int
    fsize: int
    nofile: int
    stdout_bytes: int
    stderr_bytes: int
    max_files: int
    max_files_bytes: int
    max_out_files: int
    max_out_bytes: int
    max_stdin_bytes: int
    max_args: int
    max_arg_chars: int
    max_concurrent: int
    min_free_bytes: int


class NetworkConfig(NamedTuple):
    allow: Tuple[str, ...]           # exact hosts and "*.suffix" patterns
    ports: Tuple[int, ...]           # default (443,)
    nat64: Tuple[Any, ...] = ()      # configured NAT64 prefixes (IPv6Network), refused like 64:ff9b::/96
    internal: Tuple[str, ...] = ()   # exact "IPv4:port" literals admitted without the public-address check


class SandboxConfig(NamedTuple):
    sbx: str
    work_root: str
    interpreters: Dict[str, InterpreterSpec]
    limits: Limits
    seccomp: str                     # "auto" | "on" | "off"
    log_content: bool
    network: Optional[NetworkConfig] = None   # None: no egress at all


class RunRequest(NamedTuple):
    files: Tuple[Tuple[str, bytes, bool], ...]   # (relpath, data, executable)
    entry: str
    interpreter: str
    args: Tuple[str, ...]
    stdin: Optional[bytes]
    timeout_ms: int


class MachO(NamedTuple):
    dylibs: Tuple[Tuple[int, str], ...]   # (load command, install name)
    rpaths: Tuple[str, ...]


class Closure(NamedTuple):
    """What dylib_closure found: every spelling read on the way (each with
    its symlink hops), the Homebrew kegs the images live in, and every image
    realpath (for the KD-13 signature). Ordered, no repeats."""
    spellings: Tuple[str, ...]
    kegs: Tuple[str, ...]
    images: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Request-scoped children (a cancel kills the run; ADR 0008, R-0043)
# ---------------------------------------------------------------------------

class _Reclaim:
    """One request's children, so cancelling the request kills them.

    A host-local copy of mcp-inspect's _Reclaim with ONE grace, _KILL_GRACE_S
    (KD-17: the cancel escalation and the hard deadline share it), plus
    adopt_pgid. McpServer._serve makes one per request and publishes it
    through _REQUEST_RECLAIM into the context the worker thread runs in.
    _spawn_sbx and _run_child ADOPT every child they start; _serve's
    CancelledError arm calls cancel() on the LOOP thread while the worker is
    still blocked reading the child -- the signal is what unblocks it, and
    the thread's result is then discarded.

    The lock orders adopt() against cancel(): a child adopted after the
    cancel is killed on the spot, so a cancelled request's child cannot
    escape.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: List[subprocess.Popen] = []
        self._pgids: List[int] = []
        self.cancelled = False

    def adopt(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if not self.cancelled:
                self._procs.append(proc)
                return
        # Already cancelled: nobody wants this child's output.
        _signal_group(proc, signal.SIGKILL)

    def adopt_pgid(self, pgid: int) -> None:
        """Record the TARGET's process group, read from sbx's `spawned` line.

        Recorded ONLY, for the run thread: cancel() never signals it (KD-15).
        sbx's own group is what cancel() reaches, and sbx forwards the signal
        to its target; the run thread kills this pgid itself when sbx died
        without a terminal `exit` event (KD-1/KD-2). A pgid is a bare number
        with no reaped/unreaped handle behind it, so signalling it from the
        loop thread could hit a recycled group.
        """
        with self._lock:
            self._pgids.append(pgid)

    def adopted_pgids(self) -> Tuple[int, ...]:
        with self._lock:
            return tuple(self._pgids)

    def cancel(self, loop: asyncio.AbstractEventLoop) -> None:
        """SIGTERM every adopted group now, SIGKILL survivors after the grace.

        Never blocks: it runs on the event-loop thread, which is why the
        escalation is a call_later and not a wait.
        """
        with self._lock:
            self.cancelled = True
            procs = list(self._procs)
        for proc in procs:
            _signal_group(proc, signal.SIGTERM)
        if procs:
            loop.call_later(_KILL_GRACE_S, self._escalate, procs)

    @staticmethod
    def _escalate(procs: List[subprocess.Popen]) -> None:
        for proc in procs:
            _signal_group(proc, signal.SIGKILL)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal *proc*'s process group, if the child has not been reaped yet.

    The group id is the child's pid (start_new_session makes it the leader).
    No os.getpgid() round trip, on purpose: once the child is reaped
    (returncode set) that pid may name somebody else's process, and while
    returncode is None the worker has not reaped it.
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        pass


# The current request's _Reclaim, or None outside a request (a direct caller).
_REQUEST_RECLAIM: "contextvars.ContextVar[Optional[_Reclaim]]" = contextvars.ContextVar(
    "sandbox_request_reclaim", default=None
)


class RequestCancelled(Exception):
    """The request was cancelled before this child could start."""


# sbx and every child it runs get this environment and nothing else: the
# runner's own environment (a token, a proxy, a DYLD_* variable) never
# reaches a sandboxed script.
_CHILD_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def _spawn_sbx(argv: List[str], stdin_pipe: bool, status_w: int,
               cwd: str) -> subprocess.Popen:
    """Start one sbx invocation, adopted by the current request.

    Every sbx spawn goes through here -- the run, the VSZ probe, --version and
    the self-test -- so a cancel can always find it. sbx leads its own session
    (start_new_session) so a group signal reaches everything under it; the
    only fd it inherits beyond 0-2 is `status_w`, the write end of the
    --status-fd pipe. The caller reads the status and output pipes itself
    (no communicate(): the output is capped WHILE reading, KD-7) and closes
    its copy of `status_w` once this returns.
    """
    reclaim = _REQUEST_RECLAIM.get(None)
    if reclaim is not None and reclaim.cancelled:
        raise RequestCancelled()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(status_w,),
        close_fds=True,
        start_new_session=True,
        cwd=cwd,
        env=dict(_CHILD_ENV),
    )
    if reclaim is not None:
        reclaim.adopt(proc)
    return proc


def _run_child(cmd: List[str], timeout: float, stdin_text: Optional[str],
               env: Dict[str, str]) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
    stdin=subprocess.DEVNULL, env=env)` -- or `input=stdin_text` in place of
    the DEVNULL -- with the child adopted by the current request.

    mcp-inspect's _run_child plus one argument, `env`, handed to Popen. Used
    only for /bin/ps (the VSZ samples) with env={}. The timeout path is
    run()'s, verbatim: kill the child, reap it, re-raise TimeoutExpired.
    """
    reclaim = _REQUEST_RECLAIM.get()
    if reclaim is not None and reclaim.cancelled:
        raise RequestCancelled(cmd[0])
    stdin = subprocess.DEVNULL if stdin_text is None else subprocess.PIPE
    with subprocess.Popen(
        cmd,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    ) as proc:
        if reclaim is not None:
            reclaim.adopt(proc)
        try:
            stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            # POSIX communicate already put the output so far on the exception.
            proc.wait()
            raise
        except BaseException:
            proc.kill()
            raise
        retcode = proc.poll()
    return subprocess.CompletedProcess(proc.args, retcode, stdout, stderr)


# ---------------------------------------------------------------------------
# Config (plan section 5: one JSON config file, no policy flags)
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """A config refusal; the message names the KEY, never its value (P8). Exit 2."""


_KIB = 1024
_MIB = 1024 * _KIB
_GIB = 1024 * _MIB

# The config file is a few hundred bytes; anything this large is not one.
_CONFIG_MAX_BYTES = 1 * _MIB

_CONFIG_KEYS = frozenset({"version", "sbx", "work_root", "seccomp", "log_content",
                          "interpreters", "limits"})
# Optional top-level keys: absent is today's runner.
_CONFIG_OPTIONAL_KEYS = frozenset({"network"})
_NETWORK_KEYS = frozenset({"allow", "ports", "nat64", "internal"})
_NETWORK_ALLOW_MAX = 64
_NETWORK_INTERNAL_MAX = 16
_NETWORK_PORTS_MAX = 8
_NETWORK_DEFAULT_PORTS = (443,)
_NETWORK_NAT64_MAX = 8
_NAT64_PREFIX_LENGTHS = (32, 40, 48, 56, 64, 96)   # RFC 6052 section 2.2
_INTERPRETER_NAMES = frozenset({"python3", "bash", "node"})
_INTERPRETER_KEYS = frozenset({"path", "extra_reads", "site"})
# "site" drops python3's -S so a venv interpreter's site-packages reaches
# sys.path; it means nothing to bash or node, where the key is refused.
_SITE_INTERPRETERS = frozenset({"python3"})
_EXTRA_READS_MAX = 64
_SECCOMP_MODES = ("auto", "on", "off")
_AS_MODES = ("baseline", "absolute")

# Every integer limit: (config key, inclusive minimum, inclusive maximum).
# timeout_ms is checked apart: its maximum is the config's own max_timeout_ms.
# Where plan section 5 states only a maximum, the minimum is the smallest
# value a run can still use (0 where "none at all" is meaningful).
_LIMIT_RANGES: Tuple[Tuple[str, int, int], ...] = (
    ("max_timeout_ms", 100, 120000),
    ("as_budget_bytes", 64 * _MIB, 16 * _GIB),
    ("nproc_headroom", 8, 512),
    ("fsize_bytes", 1 * _MIB, 1 * _GIB),
    ("nofile", 32, 4096),
    ("stdout_bytes", 1 * _KIB, 4 * _MIB),
    ("stderr_bytes", 1 * _KIB, 4 * _MIB),
    ("max_files", 1, 256),
    ("max_files_bytes", 1, 16 * _MIB),
    ("max_out_files", 0, 64),
    ("max_out_bytes", 0, 4 * _MIB),
    ("max_stdin_bytes", 0, 4 * _MIB),
    ("max_args", 0, 256),
    ("max_arg_chars", 1, 65536),
    ("min_free_bytes", 0, 1 << 50),
)
# max_concurrent is checked apart too: its ceiling is MAX_INFLIGHT_REQUESTS,
# the fleet's in-flight number, whose generated region sits further down.
_LIMIT_KEYS = frozenset({"timeout_ms", "as_mode", "max_concurrent"}
                        | {key for key, _, _ in _LIMIT_RANGES})

# A9: the largest sandbox_call reply the limits allow must fit in 16 MiB.
_RESPONSE_BOUND = 16 * _MIB
# Chars one capped byte can cost the reply: 6 in structuredContent + 7 in
# the text copy (validation round 1; see _cfg_limits).
_REPLY_BYTE_WORST = 13

# KD-17: every fixed term of the run bound; max_timeout_ms is the one variable.
_RUN_FIXED_TERMS_S = (_DEGRADED_RETRY_S + _REFRESH_DEADLINE_S + _HARD_DEADLINE_SLACK_S
                      + _KILL_GRACE_S + _DRAIN_S + _CLEANUP_DEADLINE_S)


def _errno_name(exc: OSError) -> str:
    """An OSError as its errno NAME, never its message (which carries the path)."""
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def _key_list(keys) -> str:
    """Peer-chosen key names for a refusal: sorted, each escaped, bounded."""
    return _log_value(sorted(str(key) for key in keys))


def _check_owned_file(path: str, where: str, allow_root: bool = False) -> int:
    """Open *path* for reading and vet the OPENED file; returns the fd.

    O_NOFOLLOW refuses a symlink as the last component, and every check is
    made on fstat of the fd that was opened, so nothing can be swapped in
    between the check and the read. The file must be regular, owned by the
    euid (or by root when *allow_root*, which sbx may be), and neither group-
    nor world-writable. The caller owns the fd and closes it.
    """
    if "\0" in path:
        raise ConfigError(f"{where}: must not contain a NUL character")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        hint = " (a symlink is refused)" if exc.errno == errno.ELOOP else ""
        raise ConfigError(f"{where}: cannot open the file ({_errno_name(exc)}){hint}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError(f"{where}: not a regular file")
        owners = {os.geteuid(), 0} if allow_root else {os.geteuid()}
        if st.st_uid not in owners:
            whom = "the current user or root" if allow_root else "the current user"
            raise ConfigError(f"{where}: refusing a file not owned by {whom}")
        if st.st_mode & 0o022:
            raise ConfigError(f"{where}: refusing a group- or world-writable file (chmod go-w)")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> dict:
    """json object_pairs_hook: a key given twice is ambiguous, refused (ADR 0015)."""
    keys = [key for key, _ in pairs]
    dup = {key for key in keys if keys.count(key) > 1}
    if dup:
        raise ConfigError(f"config: duplicate key(s): {_key_list(dup)}")
    return dict(pairs)


def _refuse_duplicate_keys_parse(text: str) -> Any:
    """A second parse of config text _strict_loads ALREADY accepted, only to
    refuse a duplicate key: _strict_loads takes no object_pairs_hook. The one
    bare json.loads in this file (declared in STRICT_JSON_EXCEPTIONS)."""
    return json.loads(text, object_pairs_hook=_no_duplicate_keys)


def _read_config_text(path: str) -> str:
    fd = _check_owned_file(path, "config")
    try:
        chunks: List[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > _CONFIG_MAX_BYTES:
                raise ConfigError(f"config: larger than {_CONFIG_MAX_BYTES} bytes")
            chunks.append(chunk)
    except OSError as exc:
        raise ConfigError(f"config: cannot read the file ({_errno_name(exc)})") from None
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("config: not UTF-8") from None


def _cfg_int(value: Any, where: str, lo: int, hi: int) -> int:
    """An integer in lo..hi, never coerced: a bool, a float or a string is refused."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{where}: must be an integer")
    if not lo <= value <= hi:
        raise ConfigError(f"{where}: must be in {lo}..{hi}")
    return value


def _cfg_abs_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: must be a non-empty string")
    if "\0" in value:
        raise ConfigError(f"{where}: must not contain a NUL character")
    if not os.path.isabs(value):
        raise ConfigError(f"{where}: must be an absolute path")
    return value


def _cfg_keys(obj: Any, where: str, allowed: frozenset, required: frozenset) -> dict:
    """*obj* must be an object whose keys are all allowed and all required present."""
    if not isinstance(obj, dict):
        raise ConfigError(f"{where}: must be an object")
    unknown = [key for key in obj if key not in allowed]
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {_key_list(unknown)}")
    missing = [key for key in required if key not in obj]
    if missing:
        raise ConfigError(f"{where}: missing key(s): {_key_list(missing)}")
    return obj


def _cfg_interpreters(value: Any) -> Dict[str, InterpreterSpec]:
    if not isinstance(value, dict):
        raise ConfigError("interpreters: must be an object")
    unknown = [name for name in value if name not in _INTERPRETER_NAMES]
    if unknown:
        raise ConfigError(f"interpreters: unknown interpreter name(s): {_key_list(unknown)}; "
                          f"accepted: {', '.join(sorted(_INTERPRETER_NAMES))}")
    if not value:
        raise ConfigError("interpreters: at least one interpreter is required")
    out: Dict[str, InterpreterSpec] = {}
    for name in sorted(value):
        where = f"interpreters.{name}"
        entry = _cfg_keys(value[name], where, _INTERPRETER_KEYS, frozenset({"path"}))
        path = _cfg_abs_path(entry["path"], f"{where}.path")
        reads = entry.get("extra_reads", [])
        if not isinstance(reads, list):
            raise ConfigError(f"{where}.extra_reads: must be a list")
        if len(reads) > _EXTRA_READS_MAX:
            raise ConfigError(f"{where}.extra_reads: more than {_EXTRA_READS_MAX} entries")
        extra = tuple(_cfg_abs_path(item, f"{where}.extra_reads[{i}]") for i, item in enumerate(reads))
        site = entry.get("site", False)
        if "site" in entry and name not in _SITE_INTERPRETERS:
            raise ConfigError(f"{where}.site: only {', '.join(sorted(_SITE_INTERPRETERS))} "
                              f"accepts this key")
        if type(site) is not bool:
            raise ConfigError(f"{where}.site: must be true or false")
        out[name] = InterpreterSpec(name=name, path=path, extra_reads=extra, site=site)
    return out


def _cfg_limits(value: Any) -> Limits:
    limits = _cfg_keys(value, "limits", _LIMIT_KEYS, _LIMIT_KEYS)
    ints: Dict[str, int] = {}
    for key, lo, hi in _LIMIT_RANGES:
        ints[key] = _cfg_int(limits[key], f"limits.{key}", lo, hi)
    ints["max_concurrent"] = _cfg_int(limits["max_concurrent"], "limits.max_concurrent",
                                      1, MAX_INFLIGHT_REQUESTS)
    timeout_ms = limits["timeout_ms"]
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
        raise ConfigError("limits.timeout_ms: must be an integer")
    if not 100 <= timeout_ms <= ints["max_timeout_ms"]:
        raise ConfigError("limits.timeout_ms: must be in 100..limits.max_timeout_ms")
    as_mode = limits["as_mode"]
    if not isinstance(as_mode, str) or as_mode not in _AS_MODES:
        raise ConfigError(f"limits.as_mode: must be one of {', '.join(_AS_MODES)}")
    if as_mode == "absolute" and sys.platform == "darwin":
        # A10: on macOS a process's VSZ is dominated by the shared cache, so
        # an absolute RLIMIT_AS cannot be chosen per interpreter; the limit
        # is always the measured baseline plus the budget there.
        raise ConfigError("limits.as_mode: must be baseline on darwin")
    # KD-17: the run bound must stay inside _RUN_DEADLINE_S. Unreachable with
    # the 120000 cap above; it pins the arithmetic in code.
    if ints["max_timeout_ms"] / 1000.0 + _RUN_FIXED_TERMS_S > _RUN_DEADLINE_S:
        raise ConfigError("limits.max_timeout_ms: the run bound would exceed the run deadline")
    # A9: the worst-case reply -- every capped stream and every out/ file at
    # its cap, base64 and JSON escaping included -- must fit in 16 MiB.
    # Validation round 1: a capped byte travels TWICE, at most 6 chars in
    # structuredContent ("\u0001"; an undecodable byte becomes U+FFFD)
    # and at most 7 in the text copy, which is that JSON dumped again inside
    # a JSON string ("\\u0001") -- 13 per byte, not 2 x 6. An out/ file's
    # path is at most _OUT_PATH_MAX_BYTES (collect_out skips a longer one),
    # carried at the same 13 x, plus its keys in both copies; the envelope
    # (the other members, the JSON-RPC frame) is 8 KiB per copy.
    worst = (_REPLY_BYTE_WORST * (ints["stdout_bytes"] + ints["stderr_bytes"]
                                  + ints["max_out_bytes"])
             + ints["max_out_files"] * (_REPLY_BYTE_WORST * _OUT_PATH_MAX_BYTES + 256)
             + 2 * 8192)
    if worst > _RESPONSE_BOUND:
        raise ConfigError("limits: stdout_bytes, stderr_bytes, max_out_bytes and max_out_files "
                          f"allow a worst-case response above {_RESPONSE_BOUND} bytes")
    return Limits(
        timeout_ms=timeout_ms,
        max_timeout_ms=ints["max_timeout_ms"],
        as_budget=ints["as_budget_bytes"],
        as_mode=as_mode,
        nproc_headroom=ints["nproc_headroom"],
        fsize=ints["fsize_bytes"],
        nofile=ints["nofile"],
        stdout_bytes=ints["stdout_bytes"],
        stderr_bytes=ints["stderr_bytes"],
        max_files=ints["max_files"],
        max_files_bytes=ints["max_files_bytes"],
        max_out_files=ints["max_out_files"],
        max_out_bytes=ints["max_out_bytes"],
        max_stdin_bytes=ints["max_stdin_bytes"],
        max_args=ints["max_args"],
        max_arg_chars=ints["max_arg_chars"],
        max_concurrent=ints["max_concurrent"],
        min_free_bytes=ints["min_free_bytes"],
    )


_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HEX_LABEL_RE = re.compile(r"0x[0-9a-f]*")


def _hostname_problem(host: Any) -> Optional[str]:
    """Why *host* is not a lower-case DNS hostname this runner will name, or
    None. Never quotes the host. An IP literal in any inet_aton spelling (a
    numeric or hex last label, or anything inet_aton accepts) is refused."""
    if not isinstance(host, str) or not host:
        return "must be a non-empty string"
    if len(host) > 253:
        return "must be at most 253 characters"
    if host.endswith("."):
        return "a trailing dot is refused"
    labels = host.split(".")
    for label in labels:
        if not _HOST_LABEL_RE.fullmatch(label):
            return ("must be dot-separated lower-case labels of a-z, 0-9 and '-' "
                    "(1-63 characters, no leading or trailing '-')")
    last = labels[-1]
    if last.isdigit() or _HEX_LABEL_RE.fullmatch(last):
        return "an IP literal is refused"
    try:
        socket.inet_aton(host)
    except (OSError, ValueError):
        return None
    return "an IP literal is refused"


def _cfg_network(value: Any) -> NetworkConfig:
    """The optional "network" object: an allowlist of exact hosts or "*."
    patterns (strict subdomains) and the ports they may be reached on."""
    net = _cfg_keys(value, "network", _NETWORK_KEYS, frozenset())
    internal = _cfg_internal(net["internal"]) if "internal" in net else ()
    if "allow" not in net and not internal:
        raise ConfigError("network: allow is required unless internal is given")
    allow = net.get("allow", [])
    if not isinstance(allow, list):
        raise ConfigError("network.allow: must be a list")
    if not allow and not internal:
        raise ConfigError("network.allow: at least one pattern is required")
    if len(allow) > _NETWORK_ALLOW_MAX:
        raise ConfigError(f"network.allow: more than {_NETWORK_ALLOW_MAX} patterns")
    for i, pattern in enumerate(allow):
        if not isinstance(pattern, str):
            raise ConfigError(f"network.allow[{i}]: must be a string")
        host = pattern[2:] if pattern.startswith("*.") else pattern
        problem = _hostname_problem(host)
        if problem is not None:
            raise ConfigError(f"network.allow[{i}]: {problem} (a pattern is a host or "
                              f"'*.' followed by a host)")
        if pattern.startswith("*.") and "." not in host:
            # "*.com" would allow every name under a whole top-level domain.
            raise ConfigError(f"network.allow[{i}]: a '*.' pattern needs a host of at least "
                              f"two labels")
    if len(set(allow)) != len(allow):
        raise ConfigError("network.allow: duplicate patterns")
    ports = net.get("ports", list(_NETWORK_DEFAULT_PORTS))
    if not isinstance(ports, list):
        raise ConfigError("network.ports: must be a list")
    if not ports:
        raise ConfigError("network.ports: at least one port is required")
    if len(ports) > _NETWORK_PORTS_MAX:
        raise ConfigError(f"network.ports: more than {_NETWORK_PORTS_MAX} ports")
    out = tuple(_cfg_int(port, f"network.ports[{i}]", 1, 65535) for i, port in enumerate(ports))
    if len(set(out)) != len(out):
        raise ConfigError("network.ports: duplicate ports")
    return NetworkConfig(allow=tuple(allow), ports=out, nat64=_cfg_nat64(net.get("nat64", [])),
                         internal=internal)


def _internal_entry_problem(entry: str) -> Optional[str]:
    """Why *entry* is not an exact canonical "IPv4:port" this runner may name
    as an internal egress target, or None. Never quotes the entry."""
    host, sep, port_text = entry.rpartition(":")
    if not sep or not host or not port_text.isascii() or not port_text.isdigit():
        return "must be IPv4:port"
    if port_text != str(int(port_text)) or not 1 <= int(port_text) <= 65535:
        return "port must be 1-65535 with no leading zeros"
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return "the host must be an IPv4 literal in canonical dotted form"
    if str(ip) != host:
        return "the host must be an IPv4 literal in canonical dotted form"
    if (ip.is_unspecified or ip.is_multicast or ip.is_link_local
            or ip in ipaddress.ip_network("0.0.0.0/8")
            or ip == ipaddress.IPv4Address("255.255.255.255")):
        return "an unspecified, link-local, multicast or broadcast address is refused"
    return None


def _cfg_internal(value: Any) -> Tuple[str, ...]:
    """The optional "network.internal" list: exact IPv4:port targets a script
    may reach without the public-address check (loopback and private ranges)."""
    if not isinstance(value, list):
        raise ConfigError("network.internal: must be a list")
    if not value:
        raise ConfigError("network.internal: at least one entry is required")
    if len(value) > _NETWORK_INTERNAL_MAX:
        raise ConfigError(f"network.internal: more than {_NETWORK_INTERNAL_MAX} entries")
    for i, entry in enumerate(value):
        if not isinstance(entry, str):
            raise ConfigError(f"network.internal[{i}]: must be a string")
        problem = _internal_entry_problem(entry)
        if problem is not None:
            raise ConfigError(f"network.internal[{i}]: {problem}")
    if len(set(value)) != len(value):
        raise ConfigError("network.internal: duplicate entries")
    return tuple(value)


def _cfg_nat64(value: Any) -> Tuple[Any, ...]:
    """The optional "network.nat64" list: the site's own NAT64 prefixes
    (RFC 6052 lengths, IPv6 only), refused as egress targets like the
    well-known ones."""
    if not isinstance(value, list):
        raise ConfigError("network.nat64: must be a list")
    if len(value) > _NETWORK_NAT64_MAX:
        raise ConfigError(f"network.nat64: more than {_NETWORK_NAT64_MAX} prefixes")
    nets = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"network.nat64[{i}]: must be a string")
        try:
            net = ipaddress.ip_network(item, strict=True)
        except ValueError:
            raise ConfigError(f"network.nat64[{i}]: must be an IPv6 prefix with no host "
                              f"bits set") from None
        if net.version != 6:
            raise ConfigError(f"network.nat64[{i}]: must be an IPv6 prefix")
        if net.prefixlen not in _NAT64_PREFIX_LENGTHS:
            raise ConfigError(f"network.nat64[{i}]: prefix length must be one of "
                              f"{', '.join(str(n) for n in _NAT64_PREFIX_LENGTHS)}")
        nets.append(net)
    if len(set(nets)) != len(nets):
        raise ConfigError("network.nat64: duplicate prefixes")
    return tuple(nets)


def _default_work_root() -> str:
    """A6: the work root when the config says null."""
    if sys.platform == "darwin":
        return os.path.join(os.path.realpath(tempfile.gettempdir()), f"mcp-sandbox-{os.geteuid()}")
    return os.path.join(os.path.expanduser("~"), ".cache", "mcp-sandbox", "work")


def _path_covers(outer: str, inner: str) -> bool:
    """True when *outer* equals *inner* or is one of its ancestors."""
    if outer == inner:
        return True
    return inner.startswith(outer.rstrip("/") + "/")


def _refuse_reads_over_work_root(interpreters: Dict[str, InterpreterSpec], work_root: str) -> None:
    """An extra_reads entry that equals or contains the work root would let a
    run read sibling runs' workdirs and egress sockets. Compared spelled and
    resolved (realpath resolves the existing prefix of a path not created
    yet, so a work root under a symlinked /tmp is still caught)."""
    roots = {os.path.normpath(work_root), os.path.realpath(work_root)}
    for name in sorted(interpreters):
        for i, entry in enumerate(interpreters[name].extra_reads):
            forms = {os.path.normpath(entry), os.path.realpath(entry)}
            if any(_path_covers(form, root) for form in forms for root in roots):
                raise ConfigError(f"interpreters.{name}.extra_reads[{i}]: must not be the "
                                  f"work root or one of its ancestors")


def load_config(path: str) -> SandboxConfig:
    """Read, parse and validate the runner's config file.

    Every refusal is a ConfigError naming the KEY, never its value (P8):
    unknown and missing keys at every level, every integer range-checked
    without coercion, the worst-case response bound (A9) and the KD-17 run
    bound. The file itself must pass _check_owned_file; so must sbx (which
    may also be root's). Filesystem facts about the interpreters and the
    work root are startup's (plans, the work-root lock), not this function's.
    """
    text = _read_config_text(path)
    try:
        obj = _strict_loads(text)
    except json.JSONDecodeError as exc:
        # Position only: the parser's message can quote the offending literal.
        raise ConfigError(f"config: not valid JSON (line {exc.lineno}, column {exc.colno})") from None
    except RecursionError:
        raise ConfigError("config: nested too deeply") from None
    _refuse_duplicate_keys_parse(text)
    cfg = _cfg_keys(obj, "config", _CONFIG_KEYS | _CONFIG_OPTIONAL_KEYS, _CONFIG_KEYS)

    version = cfg["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ConfigError("version: must be 1")

    sbx = _cfg_abs_path(cfg["sbx"], "sbx")
    os.close(_check_owned_file(sbx, "sbx", allow_root=True))

    work_root = cfg["work_root"]
    if work_root is None:
        work_root = _default_work_root()
    else:
        work_root = _cfg_abs_path(work_root, "work_root")

    interpreters = _cfg_interpreters(cfg["interpreters"])
    _refuse_reads_over_work_root(interpreters, work_root)

    seccomp = cfg["seccomp"]
    if not isinstance(seccomp, str) or seccomp not in _SECCOMP_MODES:
        raise ConfigError(f"seccomp: must be one of {', '.join(_SECCOMP_MODES)}")

    log_content = cfg["log_content"]
    if type(log_content) is not bool:
        raise ConfigError("log_content: must be true or false")

    return SandboxConfig(
        sbx=sbx,
        work_root=work_root,
        interpreters=interpreters,
        limits=_cfg_limits(cfg["limits"]),
        seccomp=seccomp,
        log_content=log_content,
        network=_cfg_network(cfg["network"]) if "network" in cfg else None,
    )


# ---------------------------------------------------------------------------
# Egress proxy: a per-run allowlisting CONNECT proxy (stdlib threads)
# ---------------------------------------------------------------------------
# CONNECT only; a strict head under a TOTAL deadline and a size bound; a host
# that is a DNS name on the allowlist and a port on the port list; ONE lookup
# whose every answer must be a global unicast address; a connect to the vetted
# address itself (never a re-resolution); a concurrent tunnel cap; idle and
# connect timeouts; close() ends every tunnel and thread. The log carries
# metadata (host, port, status, byte counts), never a payload byte.
# Module globals are read at use, so a test can patch them.

_EGRESS_HEAD_MAX = 8192            # bytes in a request head, terminator included
_EGRESS_HEADERS_MAX = 64           # header lines after the request line
_EGRESS_HEAD_DEADLINE_S = 10.0     # TOTAL time to deliver the head
_EGRESS_MAX_TUNNELS = 8            # concurrent tunnels per run
_EGRESS_MAX_CONNECTIONS = 32       # concurrent client connections (head + tunnel)
_EGRESS_GLOBAL_CONNECTIONS = 64    # the same, summed over every run's proxy in this process
_EGRESS_GLOBAL_BUDGET = threading.BoundedSemaphore(_EGRESS_GLOBAL_CONNECTIONS)
_EGRESS_CONNECT_TIMEOUT_S = 10.0
_EGRESS_IDLE_S = 120.0             # a tunnel with no byte either way this long ends
_EGRESS_SEND_TIMEOUT_S = 30.0
_EGRESS_TICK_S = 0.1
_EGRESS_BIND_ATTEMPTS = 8           # ports tried for a free 127.0.0.1:N + [::1]:N pair
_EGRESS_LINGER_S = 0.25            # drain after a refusal so the answer is not reset
_EGRESS_CLOSE_S = 1.0              # a run's proxy close bound, after sbx is reaped
_EGRESS_CHUNK = 64 * _KIB
_EGRESS_REASONS = {200: "Connection established", 400: "Bad Request", 403: "Forbidden",
                   405: "Method Not Allowed", 408: "Request Timeout",
                   431: "Request Header Fields Too Large", 502: "Bad Gateway",
                   503: "Service Unavailable"}
_CONNECT_PORT_RE = re.compile(r"[0-9]{1,5}")
_CONNECT_HOST_RE = re.compile(r"[A-Za-z0-9._\-\[\]:]+")
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEADER_VALUE_BAD_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
# 64:ff9b::/96 and 64:ff9b:1::/48 (NAT64) carry an IPv4 address inside.
_NAT64_NETS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
# IANA IPv4 special-purpose ranges that are not globally reachable, checked
# before is_global: the interpreter's own tables differ by patch level (the
# CVE-2024-4032 fix), this list does not.
_EGRESS_V4_DENY = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "255.255.255.255/32"))


class EgressRefusal(Exception):
    """A refused CONNECT: `status` is the HTTP status the client is sent."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_connect_head(head: bytes) -> Tuple[str, int]:
    """The (lower-cased host, port) of a CONNECT request head, or EgressRefusal:
    405 for any other method, 431 over the size or header-count bound, 400 for
    every malformed head. The host is NOT vetted here (egress_host_refusal)."""
    if len(head) > _EGRESS_HEAD_MAX:
        raise EgressRefusal(431, "request head too large")
    if not head.endswith(b"\r\n\r\n"):
        raise EgressRefusal(400, "request head not terminated by CRLF CRLF")
    lines = head[:-4].split(b"\r\n")
    if any(b"\r" in line or b"\n" in line for line in lines):
        raise EgressRefusal(400, "bare CR or LF in the request head")
    if len(lines) - 1 > _EGRESS_HEADERS_MAX:
        raise EgressRefusal(431, "too many header lines")
    request = lines[0].decode("latin-1")
    parts = request.split(" ")
    if parts[0] != "CONNECT":
        raise EgressRefusal(405, "only CONNECT is served")
    if len(parts) != 3:
        raise EgressRefusal(400, "malformed request line")
    _method, authority, version = parts
    if version not in ("HTTP/1.1", "HTTP/1.0"):
        raise EgressRefusal(400, "unsupported HTTP version")
    host, sep, port_text = authority.rpartition(":")
    if not sep or not host or not _CONNECT_PORT_RE.fullmatch(port_text):
        raise EgressRefusal(400, "the target must be host:port")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise EgressRefusal(400, "port out of range")
    if not _CONNECT_HOST_RE.fullmatch(host):
        raise EgressRefusal(400, "malformed target host")
    for raw in lines[1:]:
        line = raw.decode("latin-1")
        name, colon, value = line.partition(":")
        if not colon or not _HEADER_NAME_RE.fullmatch(name):
            raise EgressRefusal(400, "malformed header line")
        if _HEADER_VALUE_BAD_RE.search(value):
            raise EgressRefusal(400, "control character in a header value")
    return host.lower(), port


def egress_host_refusal(host: str) -> Optional[str]:
    """Why *host* may not be a CONNECT target (an IP literal in any spelling, a
    trailing dot, a malformed label), or None. Never quotes the host."""
    return _hostname_problem(host)


def egress_host_allowed(host: str, patterns) -> bool:
    """True when *host* equals an exact pattern or is a STRICT subdomain of a
    "*.suffix" pattern (the apex never matches)."""
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]                      # ".suffix"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def egress_address_public(addr: str, nat64: Tuple[Any, ...] = ()) -> bool:
    """True only for a global unicast address; an address that embeds another
    (v4-mapped, 6to4, Teredo, NAT64 -- well-known or one of the configured
    *nat64* prefixes) is refused outright, as is anything that does not
    parse."""
    try:
        ip = ipaddress.ip_address(addr)
    except (ValueError, TypeError):
        return False
    if isinstance(ip, ipaddress.IPv4Address) and any(ip in net for net in _EGRESS_V4_DENY):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and any(ip in net for net in nat64):
        return False
    if (ip.is_multicast or ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_unspecified or ip.is_reserved):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if (ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
                or ip.is_site_local or any(ip in net for net in _NAT64_NETS)):
            return False
    return bool(ip.is_global)


def _default_resolver(host: str, port: int) -> list:
    # Looked up absolute: a relative name lets resolv.conf search/ndots turn
    # an NXDOMAIN allowlisted name into name.<search-domain>. The client's
    # own trailing dot is refused before this (egress_host_refusal).
    return socket.getaddrinfo(host + ".", port, type=socket.SOCK_STREAM,
                              proto=socket.IPPROTO_TCP)


def _default_connector(family: int, sockaddr: tuple, timeout: float,
                       stop: Optional[threading.Event] = None) -> socket.socket:
    """Connect within *timeout*, in _EGRESS_TICK_S ticks so a set *stop*
    abandons the attempt (ECANCELED) instead of holding close() for the
    whole connect timeout."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setblocking(False)
        err = sock.connect_ex(sockaddr)
        if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
            raise OSError(err, os.strerror(err))
        deadline = time.monotonic() + timeout
        while err != 0:
            if stop is not None and stop.is_set():
                raise OSError(errno.ECANCELED, "egress proxy closing")
            left = deadline - time.monotonic()
            if left <= 0:
                raise OSError(errno.ETIMEDOUT, "connect timed out")
            _r, ready, _x = select.select([], [sock], [], min(left, _EGRESS_TICK_S))
            if ready:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err != 0:
                    raise OSError(err, os.strerror(err))
        sock.settimeout(timeout)
    except BaseException:
        sock.close()
        raise
    return sock


# sbx's in-netns forwarder port on Linux (sbx EGRESS_FORWARD_PORT; a hand
# copy pinned equal by row D7): the script's proxy URL there.
_EGRESS_LINUX_PORT = 3128


# AF_UNIX sun_path bytes usable for a NUL-terminated path (108 / 104 - 1).
_SUN_PATH_MAX = 103 if sys.platform == "darwin" else 107


class EgressSocketTooLong(ValueError):
    """<sock_dir>/s is over the AF_UNIX limit and no short alias exists here."""


class EgressProxy:
    """One run's CONNECT proxy. `kind` "tcp" listens on 127.0.0.1 (endpoint
    ("port", N)); "unix" binds <sock_dir>/s (endpoint ("socket", path)), the
    caller owning a 0700 sock_dir. start() once, close(deadline) once."""

    def __init__(self, network: NetworkConfig, kind: str, resolver=None, connector=None,
                 sock_dir: Optional[str] = None) -> None:
        if kind not in ("tcp", "unix"):
            raise ValueError("kind must be tcp or unix")
        if kind == "unix" and not sock_dir:
            raise ValueError("a unix endpoint needs sock_dir")
        self._network = network
        self._kind = kind
        self._resolver = resolver or _default_resolver
        self._sock_dir = sock_dir
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connector = connector or (
            lambda family, sockaddr, timeout: _default_connector(family, sockaddr, timeout,
                                                                 self._stop))
        self._listener: Optional[socket.socket] = None
        self._listener6: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._threads: List[threading.Thread] = []
        self._socks: set = set()
        self._active = 0
        self._connections = 0
        self._budget = _EGRESS_GLOBAL_BUDGET   # bound at construction: acquire and release pair up
        self._tunnels = 0
        self._refused = 0
        self._closed = False
        self.endpoint: Optional[Tuple[str, Any]] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._kind == "tcp":
            lsock, self._listener6 = self._bind_tcp()
            self.endpoint = ("port", lsock.getsockname()[1])
        else:
            st = os.lstat(self._sock_dir)
            if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
                    or st.st_mode & 0o077):
                raise ValueError("sock_dir must be a 0700 directory owned by the current user")
            path = os.path.join(self._sock_dir, "s")
            lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self._bind_unix(lsock, path, st)
                lsock.listen(16)
            except BaseException:
                lsock.close()
                raise
            self.endpoint = ("socket", path)
        self._listener = lsock
        self._accept_thread = threading.Thread(target=self._accept_loop,
                                               name="sandbox-egress-accept", daemon=True)
        self._accept_thread.start()
        log.info("egress: proxy listening (%s)", self._kind)

    def _bind_tcp(self) -> Tuple[socket.socket, Optional[socket.socket]]:
        """Listen on 127.0.0.1:N and [::1]:N together. sbx's Seatbelt rule
        (remote tcp "localhost:N") admits both loopbacks, so a [::1]:N left
        free could be bound by another local uid and receive the script's
        un-allowlisted connection. A taken [::1]:N retries with a fresh
        port, then fails closed; a host with no IPv6 loopback stays v4-only
        (nobody can bind ::1 there either)."""
        for _attempt in range(_EGRESS_BIND_ATTEMPTS):
            v4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            v6: Optional[socket.socket] = None
            try:
                v4.bind(("127.0.0.1", 0))
                port = v4.getsockname()[1]
                try:
                    v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                    v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    v6.bind(("::1", port))
                except OSError as exc:
                    if v6 is not None:
                        v6.close()
                        v6 = None
                    if exc.errno == errno.EADDRINUSE:
                        v4.close()
                        continue
                    if exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL,
                                         errno.EPROTONOSUPPORT):
                        raise
                v4.listen(16)
                if v6 is not None:
                    v6.listen(16)
            except BaseException:
                if v6 is not None:
                    v6.close()
                v4.close()
                raise
            return v4, v6
        raise OSError(errno.EADDRINUSE, "no port free on both 127.0.0.1 and ::1")

    def _bind_unix(self, lsock: socket.socket, path: str, dir_st: os.stat_result) -> None:
        """Bind *lsock* at *path* (<sock_dir>/s). A path over the AF_UNIX
        sun_path limit (a deep work_root) is bound on Linux through the short
        alias /proc/self/fd/<dirfd>/s: the socket inode still lands at *path*,
        which is what sbx --egress-socket is given and checks. The alias is
        used only when needed, so the common case never depends on /proc.
        Elsewhere a too-long path is refused by name."""
        if len(os.fsencode(path)) <= _SUN_PATH_MAX:
            lsock.bind(path)
            return
        if not sys.platform.startswith("linux"):
            raise EgressSocketTooLong(
                "the egress socket path exceeds the AF_UNIX limit; shorten work_root")
        dfd = os.open(self._sock_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                      | os.O_CLOEXEC)
        try:
            fst = os.fstat(dfd)
            if (fst.st_dev, fst.st_ino) != (dir_st.st_dev, dir_st.st_ino):
                raise ValueError("sock_dir changed while the egress socket was bound")
            lsock.bind("/proc/self/fd/%d/s" % dfd)
        finally:
            os.close(dfd)

    def close(self, deadline: float) -> None:
        """End every tunnel and thread by *deadline* (time.monotonic())."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        if self._accept_thread is not None:
            self._accept_thread.join(max(0.0, deadline - time.monotonic()))
        if self._listener is not None:
            self._listener.close()
        if self._listener6 is not None:
            self._listener6.close()
        if self.endpoint is not None and self.endpoint[0] == "socket":
            try:
                os.unlink(self.endpoint[1])
            except OSError:
                pass
        with self._lock:
            socks = list(self._socks)
            threads = list(self._threads)
        for sock in socks:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        log.info("egress: proxy closed (tunnels %d, refused %d)", self._tunnels, self._refused)

    def summary(self) -> dict:
        with self._lock:
            return {"tunnels": self._tunnels, "refused": self._refused}

    def active(self) -> int:
        with self._lock:
            return self._active

    # -- internals ---------------------------------------------------------
    def _track(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.add(sock)
        if self._stop.is_set():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _untrack_close(self, sock: Optional[socket.socket]) -> None:
        if sock is None:
            return
        with self._lock:
            self._socks.discard(sock)
        try:
            sock.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        listeners = [s for s in (self._listener, self._listener6) if s is not None]
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select(listeners, [], [], _EGRESS_TICK_S)
            except (OSError, ValueError):
                return
            if not ready or self._stop.is_set():
                continue
            for lsock in ready:
                self._accept_one(lsock)

    def _accept_one(self, lsock: socket.socket) -> None:
        try:
            client, _addr = lsock.accept()
        except OSError:
            return
        with self._lock:
            over = (self._connections >= _EGRESS_MAX_CONNECTIONS
                    or not self._budget.acquire(blocking=False))
            if not over:
                self._connections += 1
                self._threads = [t for t in self._threads if t.is_alive()]
        if over:
            log.info("egress: connection dropped (connection cap)")
            client.close()
            return
        thread = threading.Thread(target=self._serve, args=(client,),
                                  name="sandbox-egress-conn", daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _answer(self, client: socket.socket, status: int) -> None:
        reply = "HTTP/1.1 %d %s\r\n" % (status, _EGRESS_REASONS.get(status, "Error"))
        if status != 200:
            reply += "Content-Length: 0\r\nConnection: close\r\n"
        try:
            client.settimeout(_EGRESS_SEND_TIMEOUT_S)
            client.sendall((reply + "\r\n").encode("ascii"))
        except OSError:
            pass

    def _refuse(self, client: socket.socket, status: int, why: str,
                host: Optional[str] = None, port: Optional[int] = None) -> None:
        with self._lock:
            self._refused += 1
        if host is None:
            log.info("egress: refused %d (%s)", status, why)
        else:
            log.info("egress: refused %d %s:%d (%s)", status, _log_value(host), port, why)
        self._answer(client, status)
        # Half-close and drain briefly: closing with unread input would reset
        # the connection and could discard the answer.
        try:
            client.shutdown(socket.SHUT_WR)
            end = time.monotonic() + _EGRESS_LINGER_S
            while not self._stop.is_set():
                left = end - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([client], [], [], min(left, _EGRESS_TICK_S))
                if ready and not client.recv(_EGRESS_CHUNK):
                    break
        except (OSError, ValueError):
            pass

    def _read_head(self, client: socket.socket) -> Tuple[bytes, bytes]:
        """(head, bytes after it) under the TOTAL deadline, or EgressRefusal."""
        deadline = time.monotonic() + _EGRESS_HEAD_DEADLINE_S
        buf = b""
        while True:
            end = buf.find(b"\r\n\r\n")
            if end >= 0:
                return buf[:end + 4], buf[end + 4:]
            if b"\n\n" in buf or b"\n\r\n" in buf:
                return buf, b""                          # parse refuses the bare LF
            if len(buf) > _EGRESS_HEAD_MAX:
                raise EgressRefusal(431, "request head too large")
            if self._stop.is_set():
                raise EgressRefusal(503, "proxy closing")
            left = deadline - time.monotonic()
            if left <= 0:
                raise EgressRefusal(408, "request head deadline")
            ready, _, _ = select.select([client], [], [], min(left, _EGRESS_TICK_S))
            if not ready:
                continue
            chunk = client.recv(_EGRESS_HEAD_MAX + 1 - len(buf) if len(buf) <= _EGRESS_HEAD_MAX else 1)
            if not chunk:
                raise EgressRefusal(400, "connection closed before the head ended")
            buf += chunk

    def _serve(self, client: socket.socket) -> None:
        upstream: Optional[socket.socket] = None
        reserved = False
        self._track(client)
        try:
            try:
                head, rest = self._read_head(client)
            except EgressRefusal as exc:
                self._refuse(client, exc.status, str(exc))
                return
            except OSError:
                return
            try:
                host, port = parse_connect_head(head)
            except EgressRefusal as exc:
                self._refuse(client, exc.status, str(exc))
                return
            internal = f"{host}:{port}" in self._network.internal
            if not internal:
                problem = egress_host_refusal(host)
                if problem is not None:
                    self._refuse(client, 403, "host refused: " + problem)
                    return
                if port not in self._network.ports:
                    self._refuse(client, 403, "port not allowed", host, port)
                    return
                if not egress_host_allowed(host, self._network.allow):
                    self._refuse(client, 403, "host not allowlisted", host, port)
                    return
            with self._lock:
                if self._active >= _EGRESS_MAX_TUNNELS:
                    full = True
                else:
                    full = False
                    self._active += 1
                    reserved = True
            if full:
                self._refuse(client, 503, "tunnel cap reached", host, port)
                return
            if internal:
                # A listed literal: no lookup, no public-address check.
                answers = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                            (host, port))]
            else:
                try:
                    answers = list(self._resolver(host, port))
                except (OSError, UnicodeError, ValueError):
                    self._refuse(client, 502, "lookup failed", host, port)
                    return
                if self._stop.is_set():
                    return
                if not answers:
                    self._refuse(client, 502, "lookup returned nothing", host, port)
                    return
                for answer in answers:
                    if not egress_address_public(str(answer[4][0]), self._network.nat64):
                        self._refuse(client, 403, "a resolved address is not global", host, port)
                        return
            last_error = "no address"
            for family, _type, _proto, _canon, sockaddr in answers:
                if self._stop.is_set():
                    return
                try:
                    upstream = self._connector(family, sockaddr, _EGRESS_CONNECT_TIMEOUT_S)
                    break
                except OSError as exc:
                    last_error = _errno_name(exc)
            if upstream is None:
                if self._stop.is_set():
                    return
                self._refuse(client, 502, "connect failed (%s)" % last_error, host, port)
                return
            # A connect that returned after close() began is not a tunnel:
            # the finally closes the late upstream and close()'s summary
            # stays final.
            with self._lock:
                if self._stop.is_set():
                    return
                self._socks.add(upstream)
                self._tunnels += 1
            log.info("egress: tunnel open %s:%d via %s", _log_value(host), port,
                     _log_value(str(sockaddr[0])))
            self._answer(client, 200)
            up, down = self._splice(client, upstream, rest)
            log.info("egress: tunnel closed %s:%d (up %d bytes, down %d bytes)",
                     _log_value(host), port, up, down)
        finally:
            self._untrack_close(upstream)
            self._untrack_close(client)
            with self._lock:
                self._connections -= 1
                self._budget.release()
                if reserved:
                    self._active -= 1

    def _splice(self, client: socket.socket, upstream: socket.socket,
                rest: bytes) -> Tuple[int, int]:
        """Copy bytes both ways until both sides end, the tunnel idles out,
        or the proxy closes. An EOF from one side is relayed as a
        shutdown(SHUT_WR) of the other and the other direction keeps
        flowing (a half-closed request still gets its whole response).
        Returns (bytes up, bytes down)."""
        up = down = 0
        try:
            client.settimeout(_EGRESS_SEND_TIMEOUT_S)
            upstream.settimeout(_EGRESS_SEND_TIMEOUT_S)
            if rest:
                upstream.sendall(rest)
                up += len(rest)
            peer = {client: upstream, upstream: client}
            reading = [client, upstream]
            last = time.monotonic()
            while reading and not self._stop.is_set():
                if time.monotonic() - last > _EGRESS_IDLE_S:
                    log.info("egress: tunnel idle timeout")
                    break
                ready, _, _ = select.select(reading, [], [], _EGRESS_TICK_S)
                if not ready:
                    continue
                last = time.monotonic()
                for sock in ready:
                    data = sock.recv(_EGRESS_CHUNK)
                    if not data:
                        reading.remove(sock)
                        try:
                            peer[sock].shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                    peer[sock].sendall(data)
                    if sock is client:
                        up += len(data)
                    else:
                        down += len(data)
        except (OSError, ValueError):
            pass
        return up, down


# ---------------------------------------------------------------------------
# Mach-O reader (plan Step 8): pure, bounded, stdlib only
# ---------------------------------------------------------------------------
# Thin headers are little-endian (every architecture this runner serves);
# fat headers are big-endian. Every offset is checked before struct reads it,
# so a hostile image is a MachOError naming the broken rule, never a
# struct.error, an IndexError or a UnicodeDecodeError -- and never its bytes.

MH_MAGIC = 0xfeedface
MH_MAGIC_64 = 0xfeedfacf
FAT_MAGIC = 0xcafebabe
FAT_MAGIC_64 = 0xcafebabf
CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000c

LC_LOAD_DYLIB = 0xc
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001f
LC_LAZY_LOAD_DYLIB = 0x20
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_RPATH = 0x8000001c
_DYLIB_COMMANDS = frozenset({LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB,
                             LC_LAZY_LOAD_DYLIB, LC_LOAD_UPWARD_DYLIB})

_MACHO_SIZEOFCMDS_MAX = 1 * _MIB
_MACHO_NCMDS_MAX = 4096
_FAT_NARCH_MAX = 32
# Images in one closure; over it the closure is refused, never truncated.
_CLOSURE_IMAGES_MAX = 512
# Files visited per TARGETED keg image directory (M0:11, item-5.md: the
# largest is python3's lib-dynload, 76 files, all Mach-O; 76 x 4 = 304,
# rounded up to a power of two). Never a blanket keg walk.
_KEG_SCAN_MAX_FILES = 512
# Validation round 1 (C1): every other term of the walk is bounded too, each a
# refusal (MachOError), never a truncation. Measured on this host: the largest
# Homebrew lib/node_modules (node@10) is 850 directories, 8 levels deep, at
# most 369 entries in one directory; real rpath stacks hold a handful.
# Deduped rpath entries one image's search stack may hold (its loaders' +
# its own LC_RPATHs, deduped on the directory each resolves to).
_RPATH_STACK_MAX = 256
# Directories the node_modules walk visits, and how far below node_modules.
_KEG_SCAN_MAX_DIRS = 4096
_KEG_SCAN_MAX_DEPTH = 32
# Entries read from ONE keg directory, counted while scandir yields them.
_KEG_DIR_MAX_ENTRIES = 4096
# Symlinks followed while spelling one path out (the kernel's MAXSYMLINKS).
_SYMLINK_HOPS_MAX = 40

_MACH_HEADER_SIZE = 28              # struct mach_header
_MACH_HEADER_64_SIZE = 32           # struct mach_header_64
_DYLIB_COMMAND_SIZE = 24            # cmd, cmdsize, name.offset, timestamp, versions
_RPATH_COMMAND_SIZE = 12            # cmd, cmdsize, path.offset
_FAT_ARCH_SIZE = 20                 # cputype, cpusubtype, offset, size, align
_FAT_ARCH_64_SIZE = 32              # ... with 64-bit offset/size, + reserved


class MachOError(Exception):
    """An image the closure cannot trust; the message names the rule broken
    (and, from the I/O layer, the image) -- never the image's bytes."""


def _native_cputype() -> int:
    """The cputype of this host's slice, from platform.machine()."""
    if platform.machine().lower() in ("arm64", "aarch64"):
        return CPU_TYPE_ARM64
    return CPU_TYPE_X86_64


def _macho_slice(f, size: int, cputype: int) -> int:
    """Offset of the `cputype` image inside the open binary file `f` of
    `size` bytes: 0 for a thin image of that architecture, the fat_arch
    offset for a universal one. Anything else is a MachOError."""
    if size < 8:
        raise MachOError("truncated: under 8 bytes")
    f.seek(0)
    head = f.read(8)
    if len(head) != 8:
        raise MachOError("truncated: short read of the magic")
    (magic_be,) = struct.unpack_from(">I", head, 0)
    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        (nfat,) = struct.unpack_from(">I", head, 4)
        if nfat == 0:
            raise MachOError("fat header with no architectures")
        if nfat > _FAT_NARCH_MAX:
            raise MachOError("fat nfat_arch %d over the cap %d" % (nfat, _FAT_NARCH_MAX))
        entry = _FAT_ARCH_64_SIZE if magic_be == FAT_MAGIC_64 else _FAT_ARCH_SIZE
        table_end = 8 + nfat * entry
        if table_end > size:
            raise MachOError("truncated: fat_arch table past the end of the file")
        table = f.read(nfat * entry)
        if len(table) != nfat * entry:
            raise MachOError("truncated: short read of the fat_arch table")
        for i in range(nfat):
            pos = i * entry
            if magic_be == FAT_MAGIC_64:
                arch_cpu, _sub, offset, length = struct.unpack_from(">IIQQ", table, pos)
            else:
                arch_cpu, _sub, offset, length = struct.unpack_from(">IIII", table, pos)
            if arch_cpu != cputype:
                continue
            if offset < table_end or length < _MACH_HEADER_SIZE or offset + length > size:
                raise MachOError("fat_arch slice outside the file")
            return offset
        raise MachOError("fat image without the native architecture %#x" % cputype)
    (magic_le,) = struct.unpack_from("<I", head, 0)
    if magic_le in (MH_MAGIC, MH_MAGIC_64):
        (thin_cpu,) = struct.unpack_from("<I", head, 4)
        if thin_cpu != cputype:
            raise MachOError("thin image of cputype %#x, want %#x" % (thin_cpu, cputype))
        return 0
    raise MachOError("not a Mach-O image (bad magic)")


def _lc_str(cmd: bytes, fixed: int, what: str) -> str:
    """The lc_str of one load command `cmd` (exactly cmdsize bytes) whose
    fixed part is `fixed` bytes: the offset must land inside the command and
    after its fixed part, the string must be NUL-terminated within it, UTF-8,
    non-empty and free of a line break."""
    if len(cmd) < fixed:
        raise MachOError("%s command of %d bytes, under its %d-byte fixed part"
                         % (what, len(cmd), fixed))
    (offset,) = struct.unpack_from("<I", cmd, 8)
    if offset < fixed or offset >= len(cmd):
        raise MachOError("%s lc_str offset outside the command" % what)
    end = cmd.find(b"\0", offset)
    if end < 0:
        raise MachOError("%s lc_str not NUL-terminated inside the command" % what)
    try:
        text = cmd[offset:end].decode("utf-8")
    except UnicodeDecodeError:
        raise MachOError("%s lc_str not UTF-8" % what) from None
    if not text:
        raise MachOError("%s lc_str empty" % what)
    if "\n" in text or "\r" in text:
        raise MachOError("%s lc_str carries a line break" % what)
    return text


def parse_macho(header_bytes: bytes, cputype: int) -> MachO:
    """The dylib and rpath load commands of ONE thin image, from its header
    and load commands. PURE: no I/O; every bound is a MachOError."""
    data = header_bytes
    if len(data) < 4:
        raise MachOError("truncated: no magic")
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic == MH_MAGIC_64:
        hdr, align = _MACH_HEADER_64_SIZE, 8
    elif magic == MH_MAGIC:
        hdr, align = _MACH_HEADER_SIZE, 4
    else:
        raise MachOError("not a thin Mach-O image (bad magic)")
    if len(data) < hdr:
        raise MachOError("truncated: header under %d bytes" % hdr)
    _magic, cpu, _sub, _filetype, ncmds, sizeofcmds = struct.unpack_from("<IIIIII", data, 0)
    if cpu != cputype:
        raise MachOError("image of cputype %#x, want %#x" % (cpu, cputype))
    if ncmds > _MACHO_NCMDS_MAX:
        raise MachOError("ncmds %d over the cap %d" % (ncmds, _MACHO_NCMDS_MAX))
    if sizeofcmds > _MACHO_SIZEOFCMDS_MAX:
        raise MachOError("sizeofcmds %d over the cap %d" % (sizeofcmds, _MACHO_SIZEOFCMDS_MAX))
    end = hdr + sizeofcmds
    if end > len(data):
        raise MachOError("truncated: load commands past the end of the header bytes")

    dylibs: List[Tuple[int, str]] = []
    rpaths: List[str] = []
    pos = hdr
    for index in range(ncmds):
        if pos + 8 > end:
            raise MachOError("load command %d starts past sizeofcmds" % index)
        cmd_id, cmdsize = struct.unpack_from("<II", data, pos)
        if cmdsize < 8:
            raise MachOError("load command %d cmdsize %d under 8" % (index, cmdsize))
        if cmdsize % align:
            raise MachOError("load command %d cmdsize %d not %d-aligned" % (index, cmdsize, align))
        if pos + cmdsize > end:
            raise MachOError("load command %d runs past sizeofcmds" % index)
        if cmd_id in _DYLIB_COMMANDS:
            cmd = data[pos:pos + cmdsize]
            dylibs.append((cmd_id, _lc_str(cmd, _DYLIB_COMMAND_SIZE, "dylib")))
        elif cmd_id == LC_RPATH:
            cmd = data[pos:pos + cmdsize]
            rpaths.append(_lc_str(cmd, _RPATH_COMMAND_SIZE, "rpath"))
        pos += cmdsize
    return MachO(dylibs=tuple(dylibs), rpaths=tuple(rpaths))


# ---------------------------------------------------------------------------
# Path spelling (KD-14): every symlink hop is its own --read
# ---------------------------------------------------------------------------

def _symlink_hops(path: str) -> Tuple[Tuple[str, ...], str]:
    """(hops, real) for the absolute spelling `path`.

    A component walk (the realpath algorithm, spelled out): each component is
    joined to the REAL prefix resolved so far and lstat'ed; a symlink is a hop
    -- its spelling is recorded -- and its target (relative values against the
    link's own directory) replaces it in the walk. EVERY component is walked,
    so a symlinked ANCESTOR directory (/usr/local/opt/node in
    /usr/local/bin/node -> ../opt/node/bin/node) is a hop too: sandbox-exec
    exits 71 without its literal read (M0:3). Each symlinked lexical prefix of
    `path` as the caller spelled it is recorded as well. Hops go to reads
    ONLY; `real` (what execs take) is the walk's end. Over _SYMLINK_HOPS_MAX
    links is a MachOError. A missing component ends the resolution lexically,
    as os.path.realpath does.
    """
    if not os.path.isabs(path):
        raise MachOError("spelling is not absolute")
    hops: List[str] = []

    def _add(hop: str) -> None:
        if hop not in hops:
            hops.append(hop)

    # The caller's own spelling, prefix by prefix (stops at a "..": past it the
    # lexical prefix no longer names what the kernel walks).
    prefix = ""
    for comp in path.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            break
        prefix += "/" + comp
        if os.path.islink(prefix):
            _add(prefix)

    pending = [c for c in path.split("/") if c]
    resolved = "/"
    followed = 0
    while pending:
        comp = pending.pop(0)
        if comp == ".":
            continue
        if comp == "..":
            resolved = os.path.dirname(resolved)
            continue
        candidate = os.path.join(resolved, comp)
        try:
            is_link = stat.S_ISLNK(os.lstat(candidate).st_mode)
        except OSError:
            is_link = False             # missing/unreadable: lexical from here
        if not is_link:
            resolved = candidate
            continue
        followed += 1
        if followed > _SYMLINK_HOPS_MAX:
            raise MachOError("symlink chain over %d hops" % _SYMLINK_HOPS_MAX)
        try:
            target = os.readlink(candidate)
        except OSError as exc:
            raise MachOError("readlink failed (%s)" % _errno_name(exc)) from None
        _add(candidate)
        if target.startswith("/"):
            resolved = "/"
        pending = [c for c in target.split("/") if c] + pending
    return tuple(hops), resolved


def _resolve_install_name(name: str, loader_real: str, exe_real: str,
                          rpath_stack) -> List[str]:
    """Candidate paths for the install name `name`, in dyld's search order.

    `@executable_path/` resolves against the MAIN executable's directory,
    `@loader_path/` against the LOADING image's directory, `@rpath/` against
    each entry of `rpath_stack` -- a sequence of (rpath, declarer_real) pairs,
    the loader's own LC_RPATHs after those of the images that loaded it, each
    rpath resolved against the image that DECLARED it. An absolute name is
    returned as is. A bare relative name (cwd-relative to dyld) or an unknown
    @-token resolves to nothing. The base directories are realpaths, so the
    lexical normalisation of a leading `..` is the kernel's answer; the
    caller spells each candidate out with _symlink_hops.
    """
    if name.startswith("@rpath/"):
        rest = name[len("@rpath/"):]
        out: List[str] = []
        seen = set()
        for rpath, declarer_real in rpath_stack:
            base = _rpath_base(rpath, declarer_real, exe_real)
            if base is None:
                continue
            candidate = os.path.normpath(os.path.join(base, rest))
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
        return out
    resolved = _anchor_install(name, loader_real, exe_real)
    return [resolved] if resolved is not None else []


def _anchor_install(spelled: str, declarer_real: str, exe_real: str) -> Optional[str]:
    """`spelled` anchored: @executable_path/ against the main executable's
    directory, @loader_path/ against the declaring image's, an absolute path
    normalised; anything else resolves to nothing (None)."""
    if spelled.startswith("@executable_path/"):
        return os.path.normpath(os.path.join(
            os.path.dirname(exe_real), spelled[len("@executable_path/"):]))
    if spelled.startswith("@loader_path/"):
        return os.path.normpath(os.path.join(
            os.path.dirname(declarer_real), spelled[len("@loader_path/"):]))
    if spelled.startswith("/"):
        return os.path.normpath(spelled)
    return None


def _rpath_base(rpath: str, declarer_real: str, exe_real: str) -> Optional[str]:
    """The directory one LC_RPATH entry names (a bare @executable_path or
    @loader_path is that directory itself); None when it names none."""
    if rpath in ("@executable_path", "@loader_path"):
        rpath += "/"
    return _anchor_install(rpath, declarer_real, exe_real)


def _extend_rpath_stack(parent_stack: tuple, rpaths, declarer_real: str,
                        exe_real: str) -> tuple:
    """`parent_stack` followed by the image's own LC_RPATHs (C1): an entry
    naming a directory already on the stack -- or no directory at all -- is
    dropped (dyld would only search it again, or not at all), and a stack
    over _RPATH_STACK_MAX is a MachOError, never a truncation."""
    seen = set()
    for rpath, declarer in parent_stack:
        seen.add(_rpath_base(rpath, declarer, exe_real))
    out = list(parent_stack)
    for rpath in rpaths:
        base = _rpath_base(rpath, declarer_real, exe_real)
        if base is None or base in seen:
            continue
        seen.add(base)
        out.append((rpath, declarer_real))
        if len(out) > _RPATH_STACK_MAX:
            raise MachOError("%s: rpath search stack over %d entries"
                             % (declarer_real, _RPATH_STACK_MAX))
    return tuple(out)


# ---------------------------------------------------------------------------
# Dylib closure (plan Step 8): bounded image I/O, keg scans, the BFS
# ---------------------------------------------------------------------------

# Images under these prefixes live in the dyld shared cache (absent on disk
# since macOS 11) and are readable to every profile: never walked, never read.
_SYSTEM_PREFIXES = ("/usr/lib/", "/System/")
_HOMEBREW_KEG_RE = re.compile(r"^(/.+)/Cellar/([^/]+)/([^/]+)(/|$)")
_FRAMEWORK_RE = re.compile(r"^(.+\.framework)/Versions/([^/]+)/")
_OPEN_IMAGE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _is_system_path(path: str) -> bool:
    return path.startswith(_SYSTEM_PREFIXES)


def _has_macho_magic(path: str) -> bool:
    """True when `path` is a regular file (opened without following a
    symlink, checked on the fd) whose first 4 bytes are a thin Mach-O or fat
    magic. The extension is never consulted. Unreadable is False."""
    try:
        fd = os.open(path, _OPEN_IMAGE_FLAGS)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        head = os.read(fd, 4)
    except OSError:
        return False
    finally:
        os.close(fd)
    if len(head) != 4:
        return False
    (magic_le,) = struct.unpack("<I", head)
    (magic_be,) = struct.unpack(">I", head)
    return magic_le in (MH_MAGIC, MH_MAGIC_64) or magic_be in (FAT_MAGIC, FAT_MAGIC_64)


def _read_macho(real: str, cputype: int) -> MachO:
    """The load commands of the image at the realpath `real`: its native
    slice (_macho_slice), the header, then exactly header + sizeofcmds bytes
    (<= 1 MiB) -- never the whole file. Every failure is a MachOError naming
    the image and the rule."""
    try:
        fd = os.open(real, _OPEN_IMAGE_FLAGS)
    except OSError as exc:
        raise MachOError("%s: open failed (%s)" % (real, _errno_name(exc))) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise MachOError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as f:
            offset = _macho_slice(f, st.st_size, cputype)
            f.seek(offset)
            head = f.read(_MACH_HEADER_64_SIZE)
            if len(head) < _MACH_HEADER_SIZE:
                raise MachOError("truncated: short read of the header")
            (magic,) = struct.unpack_from("<I", head, 0)
            if magic not in (MH_MAGIC, MH_MAGIC_64):
                return parse_macho(head, cputype)        # refuses: bad magic
            hdr = _MACH_HEADER_64_SIZE if magic == MH_MAGIC_64 else _MACH_HEADER_SIZE
            (sizeofcmds,) = struct.unpack_from("<I", head, 20)
            if sizeofcmds > _MACHO_SIZEOFCMDS_MAX:
                raise MachOError("sizeofcmds %d over the cap %d"
                                 % (sizeofcmds, _MACHO_SIZEOFCMDS_MAX))
            if offset + hdr + sizeofcmds > st.st_size:
                raise MachOError("truncated: load commands past the end of the file")
            f.seek(offset)
            data = f.read(hdr + sizeofcmds)
        return parse_macho(data, cputype)
    except MachOError as exc:
        raise MachOError("%s: %s" % (real, exc)) from None
    except OSError as exc:
        raise MachOError("%s: read failed (%s)" % (real, _errno_name(exc))) from None
    finally:
        os.close(fd)


def _homebrew_keg(real: str) -> Optional[str]:
    """The Homebrew keg (<prefix>/Cellar/<formula>/<version>) holding the
    realpath `real`, or None."""
    m = _HOMEBREW_KEG_RE.match(real)
    if m is None:
        return None
    return "%s/Cellar/%s/%s" % (m.group(1), m.group(2), m.group(3))


def _is_real_dir(path: str) -> bool:
    """A directory that is not a symlink. Missing is False; any other
    failure is a MachOError (the scan fails closed)."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise MachOError("keg image scan: lstat failed (%s)" % _errno_name(exc)) from None


def _plan_check(plan_deadline: Optional[float]) -> None:
    """C1: the closure walk's per-image / per-directory check -- the request's
    cancel, and the plan term when one was handed in (_RefreshDeadline)."""
    _check_cancelled()
    if plan_deadline is not None:
        _remaining(plan_deadline)


def _keg_entries(directory: str, plan_deadline: Optional[float] = None) -> List[os.DirEntry]:
    """The entries of the real directory `directory`, sorted by name; [] when
    it is missing or a symlink. More than _KEG_DIR_MAX_ENTRIES entries is a
    refusal, counted WHILE scandir yields them (the sort only ever sees a
    bounded list)."""
    _plan_check(plan_deadline)
    if not _is_real_dir(directory):
        return []
    entries: List[os.DirEntry] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                entries.append(entry)
                if len(entries) > _KEG_DIR_MAX_ENTRIES:
                    raise MachOError("keg image scan: a directory over %d entries"
                                     % _KEG_DIR_MAX_ENTRIES)
    except OSError as exc:
        raise MachOError("keg image scan: scandir failed (%s)" % _errno_name(exc)) from None
    entries.sort(key=lambda e: e.name)
    return entries


def _keg_subdirs(directory: str, want, plan_deadline: Optional[float] = None) -> List[str]:
    """Real (non-symlink) subdirectories of `directory` whose name passes
    `want`."""
    return [e.path for e in _keg_entries(directory, plan_deadline)
            if want(e.name) and e.is_dir(follow_symlinks=False)]


def _keg_scan_top(directory: str, out: List[str],
                  plan_deadline: Optional[float] = None) -> None:
    """The Mach-O images among the top-level regular files of ONE targeted
    directory; more than _KEG_SCAN_MAX_FILES files is a refusal, never a
    truncation."""
    count = 0
    for entry in _keg_entries(directory, plan_deadline):
        if not entry.is_file(follow_symlinks=False):
            continue
        count += 1
        if count > _KEG_SCAN_MAX_FILES:
            raise MachOError("keg image scan over cap")
        if _has_macho_magic(entry.path) and entry.path not in out:
            out.append(entry.path)


def _keg_scan_node_modules(directory: str, out: List[str],
                           plan_deadline: Optional[float] = None) -> None:
    """The one recursive location: every *.node regular file under
    `directory`, symlinks never followed; more than _KEG_SCAN_MAX_FILES
    candidates, _KEG_SCAN_MAX_DIRS directories or a directory more than
    _KEG_SCAN_MAX_DEPTH levels below `directory` is a refusal (C1), each
    counted as the walk meets it."""
    count = 0
    dirs = 1
    pending = [(directory, 0)]
    while pending:
        current, depth = pending.pop()
        subdirs = []
        for entry in _keg_entries(current, plan_deadline):
            if entry.is_dir(follow_symlinks=False):
                dirs += 1
                if dirs > _KEG_SCAN_MAX_DIRS:
                    raise MachOError("keg image scan: node_modules over %d directories"
                                     % _KEG_SCAN_MAX_DIRS)
                if depth + 1 > _KEG_SCAN_MAX_DEPTH:
                    raise MachOError("keg image scan: node_modules deeper than %d levels"
                                     % _KEG_SCAN_MAX_DEPTH)
                subdirs.append((entry.path, depth + 1))
            elif entry.name.endswith(".node") and entry.is_file(follow_symlinks=False):
                count += 1
                if count > _KEG_SCAN_MAX_FILES:
                    raise MachOError("keg image scan over cap")
                if _has_macho_magic(entry.path) and entry.path not in out:
                    out.append(entry.path)
        pending.extend(reversed(subdirs))


def _keg_images(keg: str, plan_deadline: Optional[float] = None) -> List[str]:
    """The Mach-O images in the TARGETED locations of `keg` -- never a
    blanket walk (C-H3):
      <keg>/lib/*                                       (top level)
      <keg>/lib/python*/lib-dynload/*                   (top level)
      <keg>/Frameworks/*.framework/Versions/*/*         (top level)
      <keg>/Frameworks/*.framework/Versions/*/lib/python*/lib-dynload/*
                                  (top level; Homebrew python's real layout,
                                   IMPL:048 -- the plan's four miss it)
      <keg>/lib/node_modules/**/*.node                  (the only recursive one)
    Each targeted directory is capped at _KEG_SCAN_MAX_FILES files.
    *plan_deadline* (C1): checked, with the request's cancel, before every
    directory is read; None checks the cancel only."""
    dl = plan_deadline
    out: List[str] = []
    lib = os.path.join(keg, "lib")
    _keg_scan_top(lib, out, dl)
    for pydir in _keg_subdirs(lib, lambda n: n.startswith("python"), dl):
        _keg_scan_top(os.path.join(pydir, "lib-dynload"), out, dl)
    frameworks = os.path.join(keg, "Frameworks")
    for fw in _keg_subdirs(frameworks, lambda n: n.endswith(".framework"), dl):
        for vdir in _keg_subdirs(os.path.join(fw, "Versions"), lambda n: True, dl):
            _keg_scan_top(vdir, out, dl)
            for pydir in _keg_subdirs(os.path.join(vdir, "lib"),
                                      lambda n: n.startswith("python"), dl):
                _keg_scan_top(os.path.join(pydir, "lib-dynload"), out, dl)
    _keg_scan_node_modules(os.path.join(lib, "node_modules"), out, dl)
    return out


def _framework_launcher(real: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """For an interpreter realpath inside <fw>.framework/Versions/<v>/:
    (Python.app exec, main image) = the REALPATHS of
    <fw>/Versions/<v>/Resources/Python.app/Contents/MacOS/Python and
    <fw>/Versions/<v>/Python, each None unless it exists and is Mach-O by
    magic. None outside a framework or when neither is there. The first goes
    to execs (Homebrew's bin/python3.x posix_spawn(SETEXEC)s it; M0:3), the
    second is an extra closure root."""
    m = _FRAMEWORK_RE.match(real)
    if m is None:
        return None
    vdir = "%s/Versions/%s" % (m.group(1), m.group(2))
    found: List[Optional[str]] = []
    for candidate in (vdir + "/Resources/Python.app/Contents/MacOS/Python",
                      vdir + "/Python"):
        try:
            _hops, cand_real = _symlink_hops(candidate)
        except MachOError:
            cand_real = None
        found.append(cand_real if cand_real and _has_macho_magic(cand_real) else None)
    if not any(found):
        return None
    return found[0], found[1]


def dylib_closure(exe_spelling: str, plan_deadline: Optional[float] = None) -> Closure:
    """Every image the executable spelled `exe_spelling` can load, walked
    breadth-first from its realpath (plan Step 8, R-1/R-1a, C-H3).

    /usr/lib/ and /System/ are never walked. A missing weak dylib is skipped;
    a missing strong one is a MachOError. The visited set is keyed on the
    realpath. Each image records the SPELLING used to reach it plus its
    symlink hops (for --read), its keg (for --read) and its realpath. Every
    newly seen keg is scanned by _keg_images and each image found there is a
    further root; so is the framework main image of _framework_launcher.
    Extra roots start from the main executable's rpaths. Over
    _CLOSURE_IMAGES_MAX images is a MachOError, never a truncation; so is an
    rpath search stack over _RPATH_STACK_MAX deduped entries (C1).
    *plan_deadline* (C1, KD-17): checked with the request's cancel before
    every image is read and every keg directory is listed
    (_RefreshDeadline / RequestCancelled); None checks the cancel only."""
    cputype = _native_cputype()
    spellings: List[str] = []
    spelled_seen = set()
    kegs: List[str] = []
    images: List[str] = []
    visited = set()
    queue = deque()
    new_kegs: List[str] = []

    def _spell(spelling: str) -> str:
        hops, real = _symlink_hops(spelling)
        for path in (spelling,) + tuple(hops):
            if path not in spelled_seen and not _is_system_path(path):
                spelled_seen.add(path)
                spellings.append(path)
        return real

    def _admit(real: str, parent_stack: tuple) -> None:
        if real in visited:
            return
        visited.add(real)
        images.append(real)
        if len(images) > _CLOSURE_IMAGES_MAX:
            raise MachOError("closure over %d images" % _CLOSURE_IMAGES_MAX)
        queue.append((real, parent_stack))
        keg = _homebrew_keg(real)
        if keg is not None and keg not in kegs:
            kegs.append(keg)
            new_kegs.append(keg)

    _plan_check(plan_deadline)
    exe_real = _spell(exe_spelling)
    exe_macho = _read_macho(exe_real, cputype)
    root_stack = _extend_rpath_stack((), exe_macho.rpaths, exe_real, exe_real)
    parsed = {exe_real: exe_macho}
    _admit(exe_real, ())
    launcher = _framework_launcher(exe_real)
    if launcher is not None and launcher[1] is not None:
        _admit(_spell(launcher[1]), root_stack)

    while queue or new_kegs:
        _plan_check(plan_deadline)
        if new_kegs:
            for image in _keg_images(new_kegs.pop(0), plan_deadline):
                _admit(_spell(image), root_stack)
            continue
        real, parent_stack = queue.popleft()
        macho = parsed.pop(real, None) or _read_macho(real, cputype)
        stack = _extend_rpath_stack(parent_stack, macho.rpaths, real, exe_real)
        for cmd_id, name in macho.dylibs:
            if _is_system_path(name):
                continue
            target = None
            system = False
            for candidate in _resolve_install_name(name, real, exe_real, stack):
                if _is_system_path(candidate):
                    system = True
                    break
                _hops, cand_real = _symlink_hops(candidate)
                if os.path.isfile(cand_real):
                    target = candidate
                    break
            if system:
                continue
            if target is None:
                if cmd_id == LC_LOAD_WEAK_DYLIB:
                    continue
                raise MachOError("%s: dylib %s not found" % (real, name))
            target_real = _spell(target)
            if _is_system_path(target_real):
                continue
            _admit(target_real, stack)
    return Closure(spellings=tuple(spellings), kegs=tuple(kegs), images=tuple(images))


# ---------------------------------------------------------------------------
# run_script parameter validation (plan Step 9): pure, no filesystem access
# ---------------------------------------------------------------------------
# Every refusal is a RunRefusal naming the member (and, inside a list, its
# index) and the broken rule -- never the offending value (P8). Members are
# checked with isinstance, never coerced (R3:M3).

class RunRefusal(Exception):
    """A runner refusal raised below the dispatcher; `code` is the
    error.code (invalid_params, sandbox_unavailable), str() the message.
    handle_sandbox_call answers it as a _tool_refusal, never as internal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# The run_script members, exactly (HANDLERS reads the same set).
_RUN_PARAMS = frozenset({"files", "entry", "interpreter", "args", "stdin", "timeoutMs"})
_FILE_MEMBERS = frozenset({"path", "content", "encoding", "executable"})
_FILE_REQUIRED = frozenset({"path", "content"})
_FILE_ENCODINGS = ("utf8", "base64")

_RELPATH_MAX_CHARS = 1024
_RELPATH_MAX_DEPTH = 16
_COMPONENT_MAX_BYTES = 255
# First components the runner owns inside a workdir: out/ is collected, tmp/
# is the script's TMPDIR, and .sbx* is the remover's flatten namespace
# (.sbx-flat-%07d, KD-18). Compared casefolded: the macOS work root is
# case-insensitive.
_RESERVED_FIRST = frozenset({"out", "tmp"})
_RESERVED_PREFIX = ".sbx"


def _invalid(message: str) -> RunRefusal:
    return RunRefusal("invalid_params", message)


def _has_control(text: str) -> bool:
    """C0, DEL or C1 anywhere in *text*."""
    return any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in text)


def _valid_relpath(p: str) -> Tuple[str, ...]:
    """A caller's file path -> its components, or ValueError naming the rule.

    The path is relative and plain: 1..1024 characters, no NUL, no
    backslash, valid UTF-8; at most 16 components, each non-empty, not "."
    or "..", at most 255 UTF-8 bytes, free of C0/C1/DEL and not starting with
    "-"; the first component is none of the runner's own names (out, tmp,
    .sbx*). The ValueError message is a rule, never the path.
    """
    if not isinstance(p, str):
        raise ValueError("must be a string")
    if not 1 <= len(p) <= _RELPATH_MAX_CHARS:
        raise ValueError(f"must be 1..{_RELPATH_MAX_CHARS} characters")
    if "\0" in p:
        raise ValueError("must not contain a NUL character")
    if "\\" in p:
        raise ValueError("must not contain a backslash")
    if p.startswith("/"):
        raise ValueError("must be relative, not absolute")
    try:
        p.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid UTF-8 (a lone surrogate is refused)") from None
    parts = tuple(p.split("/"))
    if len(parts) > _RELPATH_MAX_DEPTH:
        raise ValueError(f"must have at most {_RELPATH_MAX_DEPTH} components")
    for part in parts:
        if not part:
            raise ValueError("must not have an empty component")
        if part in (".", ".."):
            raise ValueError("must not have a '.' or '..' component")
        if len(part.encode("utf-8")) > _COMPONENT_MAX_BYTES:
            raise ValueError(f"a component is longer than {_COMPONENT_MAX_BYTES} UTF-8 bytes")
        if _has_control(part):
            raise ValueError("must not contain a control character")
        if part.startswith("-"):
            raise ValueError("a component must not start with '-'")
    first = parts[0].casefold()
    if first in _RESERVED_FIRST or first.startswith(_RESERVED_PREFIX):
        raise ValueError("must not start with a reserved name (out, tmp, .sbx*)")
    return parts


def _path_key(parts: Tuple[str, ...]) -> Tuple[str, ...]:
    """The collision key of a path: per component NFD then casefold (and NFD
    again, so a casefold that leaves a composable pair cannot split a key)."""
    return tuple(unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())
                 for part in parts)


def _decode_content(item: dict, where: str) -> bytes:
    content = item["content"]
    if not isinstance(content, str):
        raise _invalid(f"{where}.content: must be a string")
    encoding = item.get("encoding", "utf8")
    if not isinstance(encoding, str) or encoding not in _FILE_ENCODINGS:
        raise _invalid(f"{where}.encoding: must be one of {', '.join(_FILE_ENCODINGS)}")
    if encoding == "utf8":
        try:
            return content.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid(f"{where}.content: not valid UTF-8 "
                           "(a lone surrogate is refused)") from None
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid(f"{where}.content: not valid base64") from None


def _validate_files(value: Any, limits: Limits) -> Tuple[Tuple[str, bytes, bool], ...]:
    if not isinstance(value, list) or not value:
        raise _invalid("files: must be a non-empty list of objects")
    if len(value) > limits.max_files:
        raise _invalid(f"files: more than limits.max_files ({limits.max_files}) entries")
    out: List[Tuple[str, bytes, bool]] = []
    file_keys: Dict[Tuple[str, ...], int] = {}
    dir_keys: Dict[Tuple[str, ...], int] = {}
    total = 0
    for i, item in enumerate(value):
        where = f"files[{i}]"
        if not isinstance(item, dict):
            raise _invalid(f"{where}: must be an object")
        unknown = sorted(str(key) for key in item if key not in _FILE_MEMBERS)
        if unknown:
            raise _invalid(f"{where}: unknown member(s): {', '.join(unknown)}. "
                           f"Accepted: {', '.join(sorted(_FILE_MEMBERS))}."
                           + _did_you_mean(unknown, _FILE_MEMBERS))
        missing = sorted(key for key in _FILE_REQUIRED if key not in item)
        if missing:
            raise _invalid(f"{where}: missing member(s): {', '.join(missing)}")
        try:
            parts = _valid_relpath(item["path"])
        except ValueError as exc:
            raise _invalid(f"{where}.path: {exc}") from None
        key = _path_key(parts)
        other = file_keys.get(key)
        if other is None:
            other = dir_keys.get(key)
        if other is None:
            for n in range(1, len(key)):
                if key[:n] in file_keys:
                    other = file_keys[key[:n]]
                    break
        if other is not None:
            raise _invalid(f"{where}.path: collides with files[{other}] (the same name, or a "
                           "file where the other needs a directory, after case folding and "
                           "Unicode normalisation)")
        executable = item.get("executable", False)
        if type(executable) is not bool:
            raise _invalid(f"{where}.executable: must be true or false")
        # Decoded and counted one entry at a time: the entry that crosses the
        # cap is refused before any later entry is decoded.
        data = _decode_content(item, where)
        total += len(data)
        if total > limits.max_files_bytes:
            raise _invalid(f"{where}: the decoded file contents exceed "
                           f"limits.max_files_bytes ({limits.max_files_bytes} bytes) in total")
        file_keys[key] = i
        for n in range(1, len(key)):
            dir_keys.setdefault(key[:n], i)
        out.append((item["path"], data, executable))
    return tuple(out)


def _validate_args(value: Any, limits: Limits) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise _invalid("args: must be a list of strings")
    if len(value) > limits.max_args:
        raise _invalid(f"args: more than limits.max_args ({limits.max_args}) entries")
    for i, arg in enumerate(value):
        where = f"args[{i}]"
        if not isinstance(arg, str):
            raise _invalid(f"{where}: must be a string")
        if len(arg) > limits.max_arg_chars:
            raise _invalid(f"{where}: longer than limits.max_arg_chars ({limits.max_arg_chars})")
        if "\0" in arg:
            raise _invalid(f"{where}: must not contain a NUL character")
        try:
            arg.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid(f"{where}: not valid UTF-8 (a lone surrogate is refused)") from None
    return tuple(value)


def _validate_stdin(value: Any, limits: Limits) -> bytes:
    if not isinstance(value, str):
        raise _invalid("stdin: must be a string")
    try:
        data = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("stdin: not valid UTF-8 (a lone surrogate is refused)") from None
    if len(data) > limits.max_stdin_bytes:
        raise _invalid(f"stdin: larger than limits.max_stdin_bytes "
                       f"({limits.max_stdin_bytes} bytes, UTF-8)")
    return data


def _validate_timeout(value: Any, limits: Limits) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _invalid("timeoutMs: must be an integer")
    if value < 1:
        raise _invalid("timeoutMs: must be at least 1")
    # Over the cap is clamped, not refused (plan Step 9).
    return min(value, limits.max_timeout_ms)


def validate_run_params(params: dict, cfg: SandboxConfig, healthy: FrozenSet[str]) -> RunRequest:
    """run_script's params -> a RunRequest, or a RunRefusal.

    Members exactly _RUN_PARAMS (an unknown one is refused with the fleet's
    _unknown_params_refusal sentence). `healthy` holds the interpreter names
    whose plan state is ready: a configured interpreter outside it is
    sandbox_unavailable; every other refusal is invalid_params. Defaults:
    args (), stdin None, timeoutMs limits.timeout_ms.
    """
    if not isinstance(params, dict):
        raise _invalid("params: must be an object")
    refusal = _unknown_params_refusal("run_script", params, _RUN_PARAMS)
    if refusal:
        raise _invalid(refusal)
    limits = cfg.limits

    if "files" not in params:
        raise _invalid("files: required")
    files = _validate_files(params["files"], limits)

    if "entry" not in params:
        raise _invalid("entry: required")
    entry = params["entry"]
    if not isinstance(entry, str):
        raise _invalid("entry: must be a string")
    if entry not in {path for path, _, _ in files}:
        raise _invalid("entry: must be the path of one of the files")

    if "interpreter" not in params:
        raise _invalid("interpreter: required")
    interpreter = params["interpreter"]
    configured = ", ".join(sorted(cfg.interpreters)) or "(none)"
    if not isinstance(interpreter, str) or interpreter not in cfg.interpreters:
        raise _invalid(f"interpreter: not a configured interpreter. Configured: {configured}.")
    if interpreter not in healthy:
        raise RunRefusal("sandbox_unavailable",
                         f"interpreter {interpreter}: its sandbox plan is not ready")

    args = _validate_args(params["args"], limits) if "args" in params else ()
    stdin = _validate_stdin(params["stdin"], limits) if "stdin" in params else None
    timeout_ms = (_validate_timeout(params["timeoutMs"], limits) if "timeoutMs" in params
                  else limits.timeout_ms)
    return RunRequest(files=files, entry=entry, interpreter=interpreter, args=args,
                      stdin=stdin, timeout_ms=timeout_ms)


# ---------------------------------------------------------------------------
# Work root, workdirs, out/ collection and cleanup (plan Step 10, KD-9, KD-18)
# ---------------------------------------------------------------------------
# Every filesystem operation below is fd-relative: a directory is opened
# O_DIRECTORY | O_NOFOLLOW against its parent's descriptor and vetted with
# fstat on the descriptor that was opened (P10), so a symlink planted by a
# script is never followed out of its workdir. Nothing here uses
# shutil.rmtree: the remover is the iterative FLATTEN walk of KD-18 (M0:9).
#
# Every caller reaches remove_tree through the MODULE GLOBAL (never a name
# bound at import or a default argument), so the in-process H rows can
# replace it with setattr (R3:M5); no test hook exists here.

_DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LEAF_OPEN = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_WORKDIR_RE = re.compile(r"^[0-9a-f]{32}\Z")
_LOCK_NAME = ".lock"
_QUARANTINE_NAME = "quarantine"
# The runner's own parent of the Linux per-run egress socket directories.
_EGRESS_NAME = "egress"

# KD-18 step 6: more quarantined workdirs than this and the runner is degraded.
_QUARANTINE_MAX = 16
# The opportunistic post-run quarantine retry runs at most this often.
_QUARANTINE_RETRY_EVERY_S = 60.0
# KD-18: the remover's total operation bound, whatever the tree.
_RM_MAX_OPS = 1_000_000
# M0:9: a segment is walked at most this many levels deep; a subdirectory
# found there is lifted to the top of the workdir (the fd window).
_FLATTEN_DEPTH = 50
_FLAT_PREFIX = ".sbx-flat-"
# Q1: the remover ticks (deadline + op bound) at least once per this many
# directory entries read, and holds at most _RM_PENDING_MAX of one
# directory's entries at a time (a directory holding more is re-listed once
# those are gone).
_RM_LIST_BATCH = 1024
_RM_PENDING_MAX = 65536
# darwin <sys/acl.h> (MacOSX14.2.sdk, M0:8).
ACL_TYPE_EXTENDED = 0x00000100
# darwin <sys/attr.h> / <sys/kauth.h> (MacOSX14.2.sdk): setattrlistat(2) on
# ONE name under a verified directory fd with FSOPT_NOFOLLOW acts on that
# entry itself, never through a symlink and never through a spelled path
# (validation round 1, W2/W1). Measured on this host (Darwin 23.2.0): it
# clears uchg and removes the item-3l broad deny ACE -- the one that refuses
# every open(2), O_EVTONLY included -- on a file and on a directory, mode 000
# or not, and on a symlink it changes the link, not its target.
_ATTR_BIT_MAP_COUNT = 5
_ATTR_CMN_FLAGS = 0x00040000
_ATTR_CMN_EXTENDED_SECURITY = 0x00400000
_FSOPT_NOFOLLOW = 0x00000001
_KAUTH_FILESEC_MAGIC = 0x012cc16d
_KAUTH_FILESEC_NOACL = 0xFFFFFFFF

# collect_out bounds (KD-9): directory depth below out/ and entries visited.
_OUT_MAX_DEPTH = 8
_OUT_MAX_ENTRIES = 4096
# An out/ file whose '/'-joined path (relative to out/) is longer than this
# many bytes is skipped (truncated): the A9 worst-case reply prices every
# path at this length (validation round 1).
_OUT_PATH_MAX_BYTES = 512
_READ_CHUNK = 64 * _KIB

# root fd -> the absolute spelling prepare_work_root opened it from;
# _root_path hands it out only while it still names the inode the fd holds
# (the free-space precheck, the plan and the run's paths). The remover never
# uses it: its darwin flag clear and ACL strip are fd-relative only
# (setattrlistat with FSOPT_NOFOLLOW on one name under the verified parent
# fd, then the parent through its own fd -- _Remover.clear_flags/strip_acl),
# and remove_tree ignores the abs_root it is still passed.
_WORK_ROOT_PATHS: Dict[int, str] = {}


def _refuse_start(message: str) -> None:
    """Fail-closed startup: one stderr line, exit 1."""
    sys.stderr.write("mcp-sandbox: refusing to start: %s\n" % message)
    sys.stderr.flush()
    raise SystemExit(1)


def _close_quietly(*fds: int) -> None:
    for fd in fds:
        if fd is not None and fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _vet_owned_dir(st: os.stat_result, what: str) -> None:
    """A directory owned by the euid that neither group nor others may use."""
    if not stat.S_ISDIR(st.st_mode):
        _refuse_start("%s is not a directory" % what)
    if st.st_uid != os.geteuid():
        _refuse_start("%s is not owned by the current user" % what)
    if st.st_mode & 0o077:
        _refuse_start("%s must not be accessible to group or others (chmod 700)" % what)


def _open_egress_root(root_fd: int, create: bool) -> int:
    """Create (when *create*), open and vet egress/ under the work root, the
    runner's own parent of every Linux run's <32-hex>/s egress socket
    directory: never inside a run workdir, never under a socket directory
    sbx refuses (/tmp). A non-directory or a symlink at the name refuses
    the start, exactly as one at quarantine/ does. Returns the fd; the
    caller closes it."""
    if create:
        try:
            os.mkdir(_EGRESS_NAME, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            _refuse_start("cannot create egress/ (%s)" % _errno_name(exc))
    try:
        e_fd = os.open(_EGRESS_NAME, _DIR_OPEN, dir_fd=root_fd)
    except OSError as exc:
        _refuse_start("cannot open egress/ (%s)" % _errno_name(exc))
    try:
        _vet_owned_dir(os.fstat(e_fd), "egress/")
    except BaseException:
        _close_quietly(e_fd)
        raise
    return e_fd


def prepare_work_root(path: str) -> Tuple[int, int, int]:
    """Open the work root, take its single-owner lock and open quarantine/.

    Returns (root_fd, lock_fd, quarantine_fd); the caller closes them. The
    lock fd is CLOEXEC and held for the process lifetime (C-M1). Any rule
    broken refuses the start: stderr one line, SystemExit(1).
    """
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        _refuse_start("cannot create the work root (%s)" % _errno_name(exc))
    try:
        lst = os.lstat(path)
    except OSError as exc:
        _refuse_start("cannot stat the work root (%s)" % _errno_name(exc))
    if stat.S_ISLNK(lst.st_mode):
        _refuse_start("the work root is a symlink")
    if os.path.realpath(path) != os.path.normpath(path):
        _refuse_start("the work root is not a canonical path (its realpath differs)")
    _vet_owned_dir(lst, "the work root")
    root_fd = lock_fd = q_fd = -1
    try:
        try:
            root_fd = os.open(path, _DIR_OPEN)
        except OSError as exc:
            _refuse_start("cannot open the work root (%s)" % _errno_name(exc))
        st = os.fstat(root_fd)
        if (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino):
            _refuse_start("the work root changed while it was opened")
        _vet_owned_dir(st, "the work root")
        try:
            lock_fd = os.open(_LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                              0o600, dir_fd=root_fd)
        except OSError as exc:
            _refuse_start("cannot open the work root lock (%s)" % _errno_name(exc))
        lst_lock = os.fstat(lock_fd)
        if not stat.S_ISREG(lst_lock.st_mode) or lst_lock.st_uid != os.geteuid():
            _refuse_start("the work root lock is not a regular file owned by the current user")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _refuse_start("work root %s is in use by another runner (lock held)" % path)
        try:
            os.mkdir(_QUARANTINE_NAME, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            _refuse_start("cannot create quarantine/ (%s)" % _errno_name(exc))
        try:
            q_fd = os.open(_QUARANTINE_NAME, _DIR_OPEN, dir_fd=root_fd)
        except OSError as exc:
            _refuse_start("cannot open quarantine/ (%s)" % _errno_name(exc))
        _vet_owned_dir(os.fstat(q_fd), "quarantine/")
        _close_quietly(_open_egress_root(root_fd, create=True))
    except BaseException:
        _close_quietly(q_fd, lock_fd, root_fd)
        raise
    _WORK_ROOT_PATHS[root_fd] = os.path.normpath(path)
    return root_fd, lock_fd, q_fd


def _root_path(root_fd: int) -> Optional[str]:
    """The spelling prepare_work_root recorded for *root_fd*, when it still
    names the inode the fd holds; None means the work root no longer
    resolves, and a run or a plan refresh that needs the path refuses
    (remove_tree is still handed it and ignores it)."""
    path = _WORK_ROOT_PATHS.get(root_fd)
    if path is None:
        return None
    try:
        st = os.fstat(root_fd)
        lst = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(lst.st_mode) or (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino):
        return None
    return path


def sweep_work_root(root_fd: int, quarantine_fd: int, deadline: float) -> int:
    """The startup sweep, under ONE shared absolute *deadline* (R3:M4).

    Every name is validated before anything is removed: the work root may
    hold only ^[0-9a-f]{32}$ leftovers, .lock, quarantine and egress;
    quarantine/ and egress/ only ^[0-9a-f]{32}$ entries; anything else
    refuses the start. Stale egress/ entries are removed first (a failure
    is logged, never quarantined: they hold only a runner socket). Leftovers
    are removed with remove_tree against the shared deadline; one that
    resists, or is not reached, is quarantined. The quarantine is then
    retried until the deadline. Returns the quarantine count; a count above
    _QUARANTINE_MAX starts the runner degraded, never exits.
    """
    try:
        top = sorted(os.listdir(root_fd))
        held = sorted(os.listdir(quarantine_fd))
    except OSError as exc:
        _refuse_start("cannot list the work root (%s)" % _errno_name(exc))
    leftovers = []
    for name in top:
        if name in (_LOCK_NAME, _QUARANTINE_NAME, _EGRESS_NAME):
            continue
        if not _WORKDIR_RE.match(name):
            _refuse_start("the work root holds a foreign entry %s" % _log_value(name))
        leftovers.append(name)
    for name in held:
        if not _WORKDIR_RE.match(name):
            _refuse_start("quarantine/ holds a foreign entry %s" % _log_value(name))
    e_fd = _open_egress_root(root_fd, create=True)
    try:
        try:
            stale = sorted(os.listdir(e_fd))
        except OSError as exc:
            _refuse_start("cannot list egress/ (%s)" % _errno_name(exc))
        for name in stale:
            if not _WORKDIR_RE.match(name):
                _refuse_start("egress/ holds a foreign entry %s" % _log_value(name))
        # A SIGKILLed runner's socket directories: no run can still use one
        # (the work root lock is ours), and nothing of a script's is there.
        for name in stale:
            if not remove_tree(e_fd, name, None, deadline):
                log.warning("startup: a stale egress/ entry was not removed")
    finally:
        _close_quietly(e_fd)
    quarantine = Quarantine(root_fd, quarantine_fd)
    abs_root = _root_path(root_fd)
    for name in leftovers:
        removed = False
        if time.monotonic() < deadline:
            removed = remove_tree(root_fd, name, abs_root, deadline)
        if not removed:
            quarantine.add(name)
    return quarantine.retry(deadline)


def create_workdir(root_fd: int) -> Tuple[str, int]:
    """A fresh 0700 ^[0-9a-f]{32}$ workdir holding tmp/ and out/.

    Returns (name, wd_fd); the caller closes wd_fd and removes the workdir.
    """
    name = ""
    for _ in range(8):
        name = secrets.token_hex(16)
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
            break
        except FileExistsError:
            continue
    else:
        raise RunRefusal("sandbox_unavailable", "could not allocate a work directory")
    wd_fd = os.open(name, _DIR_OPEN, dir_fd=root_fd)
    try:
        st = os.fstat(wd_fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
            raise RunRefusal("sandbox_unavailable", "the work directory is not ours")
        for sub in ("tmp", "out"):
            os.mkdir(sub, 0o700, dir_fd=wd_fd)
    except BaseException:
        os.close(wd_fd)
        raise
    return name, wd_fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def materialize(wd_fd: int, files: Tuple[Tuple[str, bytes, bool], ...]) -> None:
    """Write the run's files into the workdir, fd-relative (KD-9).

    Every directory component is created 0700 (or reused) and opened
    O_DIRECTORY | O_NOFOLLOW against its parent; every leaf is created
    O_CREAT | O_EXCL | O_NOFOLLOW, 0700 when executable else 0600. A
    symlink or an existing entry anywhere refuses the file (RunRefusal).
    """
    for index, (relpath, data, executable) in enumerate(files):
        where = "files[%d].path" % index
        parts = relpath.split("/")
        fds: List[int] = []
        try:
            cur = wd_fd
            for comp in parts[:-1]:
                try:
                    os.mkdir(comp, 0o700, dir_fd=cur)
                except FileExistsError:
                    pass
                try:
                    nxt = os.open(comp, _DIR_OPEN, dir_fd=cur)
                except OSError as exc:
                    raise RunRefusal("invalid_params", "%s: a directory component cannot be "
                                     "created (%s)" % (where, _errno_name(exc))) from None
                fds.append(nxt)
                cur = nxt
            mode = 0o700 if executable else 0o600
            try:
                leaf = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                               | os.O_CLOEXEC, mode, dir_fd=cur)
            except OSError as exc:
                code = ("invalid_params" if exc.errno in (errno.EEXIST, errno.ELOOP, errno.ENOTDIR,
                                                          errno.EISDIR)
                        else "sandbox_unavailable")
                raise RunRefusal(code, "%s: the file cannot be created (%s)"
                                 % (where, _errno_name(exc))) from None
            fds.append(leaf)
            os.fchmod(leaf, mode)
            _write_all(leaf, data)
        finally:
            _close_quietly(*fds)


def _read_out_file(fd: int, budget: int) -> Optional[bytes]:
    """At most *budget* bytes of *fd*; None when the file holds more."""
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(_READ_CHUNK, budget - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > budget:
            return None
        chunks.append(chunk)


def _out_names(dir_fd: int, budget: int) -> Tuple[List[str], bool]:
    """At most *budget* entry names of the directory *dir_fd*, sorted in
    reverse (the walk pops from the end), and whether more were there (Q1:
    the listing stops at the budget while scandir yields; only the bounded
    list is sorted)."""
    names: List[str] = []
    cut = False
    with os.scandir(dir_fd) as it:
        for entry in it:
            if len(names) >= budget:
                cut = True
                break
            names.append(entry.name)
    names.sort(reverse=True)
    return names, cut


def collect_out(wd_fd: int, limits: Limits) -> Tuple[List[dict], bool]:
    """The regular, single-link files the script left under out/ (KD-9).

    Returns (outFiles, outFilesTruncated); each file is {"path" (relative to
    out/, '/'-joined), "content", "encoding": "utf8" | "base64"}. A symlink,
    hardlink, FIFO, device or socket is skipped, as is anything past the
    depth / entry / file / byte bounds or a path longer than
    _OUT_PATH_MAX_BYTES; every skip sets truncated. An out/
    that is not a directory (replaced by a symlink or a file) yields none.
    """
    try:
        out_fd = os.open("out", _DIR_OPEN, dir_fd=wd_fd)
    except OSError:
        return [], False
    files: List[dict] = []
    truncated = False
    visited = 0
    total = 0
    # Frames: [fd, path parts, pending names]; the window is <= depth + 1 fds.
    stack: List[list] = []
    try:
        names, cut = _out_names(out_fd, _OUT_MAX_ENTRIES)
        truncated = truncated or cut
        stack.append([out_fd, (), names])
        out_fd = -1
        while stack:
            frame = stack[-1]
            if not frame[2]:
                os.close(frame[0])
                stack.pop()
                continue
            name = frame[2].pop()
            visited += 1
            if visited > _OUT_MAX_ENTRIES:
                truncated = True
                break
            parts = frame[1] + (name,)
            if len(os.fsencode("/".join(parts))) > _OUT_PATH_MAX_BYTES:
                truncated = True               # A9 prices no longer path
                continue
            try:
                st = os.stat(name, dir_fd=frame[0], follow_symlinks=False)
            except OSError:
                truncated = True
                continue
            if stat.S_ISDIR(st.st_mode):
                if len(parts) >= _OUT_MAX_DEPTH:
                    truncated = True
                    continue
                try:
                    sub = os.open(name, _DIR_OPEN, dir_fd=frame[0])
                except OSError:
                    truncated = True
                    continue
                try:
                    sst = os.fstat(sub)
                    if (sst.st_dev, sst.st_ino) != (st.st_dev, st.st_ino):
                        raise OSError(errno.ESTALE, "replaced")
                    names, cut = _out_names(sub, _OUT_MAX_ENTRIES - visited)
                    truncated = truncated or cut
                except OSError:
                    os.close(sub)
                    truncated = True
                    continue
                stack.append([sub, parts, names])
                continue
            if not stat.S_ISREG(st.st_mode) or len(files) >= limits.max_out_files:
                truncated = True
                continue
            try:
                fd = os.open(name, _LEAF_OPEN, dir_fd=frame[0])
            except OSError:
                truncated = True
                continue
            try:
                fst = os.fstat(fd)
                data = None
                if stat.S_ISREG(fst.st_mode) and fst.st_nlink == 1 and \
                        (fst.st_dev, fst.st_ino) == (st.st_dev, st.st_ino):
                    data = _read_out_file(fd, limits.max_out_bytes - total)
            except OSError:
                data = None
            finally:
                os.close(fd)
            if data is None:
                truncated = True
                continue
            total += len(data)
            try:
                content, encoding = data.decode("utf-8"), "utf8"
            except UnicodeDecodeError:
                content, encoding = base64.b64encode(data).decode("ascii"), "base64"
            files.append({"path": "/".join(parts), "content": content, "encoding": encoding})
    finally:
        _close_quietly(out_fd, *(frame[0] for frame in stack))
    return files, truncated


def _check_free_space(root: str, min_free_bytes: int) -> Optional[dict]:
    """None when the work root's filesystem has at least *min_free_bytes*
    free, else a sandbox_unavailable refusal (which never names the root)."""
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        return _tool_refusal("sandbox_unavailable",
                             "the work root's free space is unknown (%s)" % _errno_name(exc))
    if free < min_free_bytes:
        return _tool_refusal("sandbox_unavailable",
                             "the work root has less free space than limits.min_free_bytes")
    return None


# -- darwin flag clear and ACL strip, fd-relative only (KD-18 steps 4-5) -------
# Validation round 1 (W2/W1): no darwin fallback takes a spelled path any
# more. The entry is reached as ONE name under its verified parent fd
# (setattrlistat, FSOPT_NOFOLLOW), the parent itself through its own fd
# (fchflags, acl_set_fd_np). What these cannot clear stays and the tree is
# quarantined -- there is no path-based last resort.

_LIBC: Any = None
_LIBC_LOCK = threading.Lock()


def _libc() -> Any:
    """libc with the ACL, setattrlistat, fchflags and libproc prototypes,
    loaded lazily once (darwin)."""
    global _LIBC
    with _LIBC_LOCK:
        if _LIBC is None:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            libc.acl_init.restype = ctypes.c_void_p
            libc.acl_init.argtypes = [ctypes.c_int]
            libc.acl_set_fd_np.restype = ctypes.c_int
            libc.acl_set_fd_np.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            libc.acl_free.restype = ctypes.c_int
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.setattrlistat.restype = ctypes.c_int
            libc.setattrlistat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
            libc.fchflags.restype = ctypes.c_int
            libc.fchflags.argtypes = [ctypes.c_int, ctypes.c_uint]
            libc.proc_listpids.restype = ctypes.c_int
            libc.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                           ctypes.c_int]
            libc.proc_pidinfo.restype = ctypes.c_int
            libc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                          ctypes.c_void_p, ctypes.c_int]
            _LIBC = (ctypes, libc)
        return _LIBC


def _log_acl_errno(what: str, ctypes_mod: Any) -> None:
    err = ctypes_mod.get_errno()
    log.info("remove_tree: %s failed (%s)", what, errno.errorcode.get(err, str(err)))


def _strip_acl_fd(fd: int) -> bool:
    """Replace the extended ACL of the file *fd* holds with an EMPTY one
    (darwin; False elsewhere, nothing loaded)."""
    if sys.platform != "darwin":
        return False
    ctypes_mod, libc = _libc()
    acl = libc.acl_init(0)
    if not acl:
        _log_acl_errno("acl_init", ctypes_mod)
        return False
    try:
        if libc.acl_set_fd_np(fd, acl, ACL_TYPE_EXTENDED) != 0:
            _log_acl_errno("acl_set_fd_np", ctypes_mod)
            return False
    finally:
        libc.acl_free(acl)
    return True


def _clear_flags_fd(fd: int) -> bool:
    """fchflags(fd, 0): the owner clears uchg on the file *fd* holds
    (darwin; False elsewhere)."""
    if sys.platform != "darwin":
        return False
    ctypes_mod, libc = _libc()
    if libc.fchflags(fd, 0) != 0:
        _log_acl_errno("fchflags", ctypes_mod)
        return False
    return True


def _setattr_at(dir_fd: int, name: str, common: int, payload: bytes, what: str) -> bool:
    """setattrlistat(dir_fd, name, {common}, payload, FSOPT_NOFOLLOW): one
    common attribute of the entry *name* of the directory *dir_fd*, never
    through a symlink and never through a spelled path. *name* is ONE path
    component (the remover never holds anything else); darwin only."""
    if sys.platform != "darwin" or not name or "/" in name:
        return False
    ctypes_mod, libc = _libc()
    alist = ctypes_mod.create_string_buffer(struct.pack(
        "=HHIIIII", _ATTR_BIT_MAP_COUNT, 0, common, 0, 0, 0, 0))
    buf = ctypes_mod.create_string_buffer(payload, len(payload))
    if libc.setattrlistat(dir_fd, os.fsencode(name), alist, buf, len(payload),
                          _FSOPT_NOFOLLOW) != 0:
        _log_acl_errno(what, ctypes_mod)
        return False
    return True


def _clear_flags_at(dir_fd: int, name: str) -> bool:
    """ATTR_CMN_FLAGS = 0 on the entry *name* of *dir_fd* (clears uchg)."""
    return _setattr_at(dir_fd, name, _ATTR_CMN_FLAGS, struct.pack("=I", 0),
                       "setattrlistat(flags)")


def _remove_acl_at(dir_fd: int, name: str) -> bool:
    """Remove the extended ACL of the entry *name* of *dir_fd*: an
    ATTR_CMN_EXTENDED_SECURITY kauth_filesec whose entry count is
    KAUTH_FILESEC_NOACL and whose owner / group GUIDs are null (left as
    they are). It needs no open(2) of the entry, so the broad item-3l ACE
    that refuses every open cannot stop it."""
    fsec = struct.pack("=I16s16sII", _KAUTH_FILESEC_MAGIC, b"\0" * 16, b"\0" * 16,
                       _KAUTH_FILESEC_NOACL, 0)
    ref = struct.pack("=iI", 8, len(fsec))    # attrreference: data right after it
    return _setattr_at(dir_fd, name, _ATTR_CMN_EXTENDED_SECURITY, ref + fsec,
                       "setattrlistat(acl)")


# -- the FLATTEN remover (KD-18, M0:9) ----------------------------------------


class _RemoveStop(Exception):
    """The deadline or _RM_MAX_OPS was hit: the tree stays (progress kept)."""


class _Resist(Exception):
    """An entry survived every fallback: the tree stays (quarantine)."""


class _Frame:
    """One open directory on the remover's stack: its name in the parent,
    its fd, the (st_dev, st_ino) the fd holds, and the entries still to do."""
    __slots__ = ("name", "fd", "key", "pending", "partial")

    def __init__(self, name: Optional[str], fd: int, key: Tuple[int, int]) -> None:
        self.name = name
        self.fd = fd
        self.key = key
        self.pending: Optional[List[Tuple[str, bool]]] = None
        # The last listing stopped at _RM_PENDING_MAX: list again once done.
        self.partial = False


class _Remover:
    """One remove_tree call: an explicit stack of frames, never recursion.

    The stack is [parent of the workdir, workdir, segment levels...]; it is
    never deeper than _FLATTEN_DEPTH below the workdir, so at most ~50
    directory fds are open at once. A subdirectory found at that depth is
    LIFTED with one fd-relative rename to a fresh .sbx-flat-%07d name at the
    top of the workdir and removed by a later pass, so lifted segments
    persist across retries (monotone progress). The deadline and the
    operation bound are checked before every operation.
    """

    def __init__(self, parent_fd: int, deadline: float) -> None:
        self.deadline = deadline
        self.ops = 0
        self.seq = 0
        self.darwin = sys.platform == "darwin"
        st = os.fstat(parent_fd)
        self.root = _Frame(None, parent_fd, (st.st_dev, st.st_ino))
        self.owned: List[_Frame] = []

    # -- bounds -------------------------------------------------------------

    def tick(self) -> None:
        """Before EVERY operation (M0:9): the deadline and the op bound."""
        self.ops += 1
        if self.ops > _RM_MAX_OPS or time.monotonic() >= self.deadline:
            raise _RemoveStop()

    def close_all(self) -> None:
        while self.owned:
            _close_quietly(self.owned.pop().fd)

    # -- darwin flag clear and ACL strip: fd-relative only (W2/W1) -----------

    def clear_flags(self, stack: List[_Frame], name: str) -> None:
        """darwin: flags 0 on the entry -- ONE name under the verified parent
        fd, FSOPT_NOFOLLOW -- and on its parent through the parent's own fd
        (the owner clears uchg; never the work root itself)."""
        if not self.darwin:
            return
        self.tick()
        _clear_flags_at(stack[-1].fd, name)
        if len(stack) > 1:
            self.tick()
            _clear_flags_fd(stack[-1].fd)

    def strip_acl(self, stack: List[_Frame], name: str, is_dir: bool) -> None:
        """darwin: no extended ACL on the entry (ONE name under the verified
        parent fd, FSOPT_NOFOLLOW; no open(2), so the broad deny ACE that
        refuses every open cannot stop it), then an EMPTY one on its parent
        through the parent's own fd. What still refuses afterwards is
        _Resist -> quarantine; nothing is ever tried by a spelled path."""
        if not self.darwin:
            return
        self.tick()
        _remove_acl_at(stack[-1].fd, name)
        if len(stack) > 1:
            self.tick()
            _strip_acl_fd(stack[-1].fd)

    def chmod_entry(self, parent_fd: int, name: str) -> None:
        """Step 2: chmod 0700 a directory entry without following a symlink."""
        self.tick()
        try:
            if os.chmod in os.supports_follow_symlinks:
                os.chmod(name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
            else:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    os.chmod(name, 0o700, dir_fd=parent_fd)
        except OSError:
            pass

    def escalate(self, stack: List[_Frame], name: str, is_dir: bool, op: Any) -> Any:
        """op() on the entry *name* of stack[-1], with the KD-18 fallbacks in
        order: chmod 0700 (directories), darwin flag clear, darwin ACL strip.
        An entry that still refuses raises _Resist."""
        self.tick()
        try:
            return op()
        except PermissionError:
            pass
        if is_dir:
            self.chmod_entry(stack[-1].fd, name)
            self.tick()
            try:
                return op()
            except PermissionError:
                pass
        if not self.darwin:
            raise _Resist()
        self.clear_flags(stack, name)
        if is_dir:
            self.chmod_entry(stack[-1].fd, name)
        self.tick()
        try:
            return op()
        except PermissionError:
            pass
        self.strip_acl(stack, name, is_dir)
        if is_dir:
            self.chmod_entry(stack[-1].fd, name)
        self.tick()
        try:
            return op()
        except PermissionError:
            raise _Resist() from None

    # -- the walk -------------------------------------------------------------

    def open_dir(self, stack: List[_Frame], name: str) -> Optional[_Frame]:
        """Open a subdirectory of stack[-1]; None when it is not one."""
        parent = stack[-1]
        try:
            fd = self.escalate(stack, name, True,
                               lambda: os.open(name, _DIR_OPEN, dir_fd=parent.fd))
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                return None
            raise
        frame = _Frame(name, fd, (0, 0))
        self.owned.append(frame)
        st = os.fstat(fd)
        frame.key = (st.st_dev, st.st_ino)
        # Step 4: fchmod only when needed (a depth-priced mutation, M0:9).
        if (st.st_mode & 0o700) != 0o700:
            self.tick()
            try:
                os.fchmod(fd, 0o700)
            except PermissionError:
                if self.darwin:
                    self.clear_flags(stack, name)
                    _strip_acl_fd(fd)
                self.tick()
                try:
                    os.fchmod(fd, 0o700)
                except OSError:
                    pass
        return frame

    def list_dir(self, stack: List[_Frame]) -> List[Tuple[str, bool]]:
        """Up to _RM_PENDING_MAX entries of stack[-1], read in batches of
        _RM_LIST_BATCH with a tick() before each (Q1: a huge directory cannot
        hold the remover past its deadline in one tick); frame.partial says
        the listing stopped early and the directory is listed again once
        these entries are gone."""
        frame = stack[-1]

        def scan() -> List[Tuple[str, bool]]:
            out: List[Tuple[str, bool]] = []
            frame.partial = False
            with os.scandir(frame.fd) as it:
                while True:
                    if len(out) >= _RM_PENDING_MAX:
                        frame.partial = True       # maybe more: list again later
                        break
                    if out and len(out) % _RM_LIST_BATCH == 0:
                        self.tick()                # BEFORE the next batch is read
                    entry = next(it, None)
                    if entry is None:
                        break
                    out.append((entry.name, entry.is_dir(follow_symlinks=False)))
            return out

        self.tick()
        try:
            return scan()
        except PermissionError:
            pass
        self.tick()
        try:
            os.fchmod(frame.fd, 0o700)
        except OSError:
            pass
        if self.darwin and len(stack) > 1:
            self.clear_flags(stack[:-1], frame.name)
            self.tick()
            _strip_acl_fd(frame.fd)
        self.tick()
        try:
            return scan()
        except PermissionError:
            raise _Resist() from None

    def unlink(self, stack: List[_Frame], name: str) -> None:
        parent = stack[-1]
        try:
            self.escalate(stack, name, False, lambda: os.unlink(name, dir_fd=parent.fd))
        except FileNotFoundError:
            pass

    def rmdir(self, stack: List[_Frame], name: str) -> None:
        parent = stack[-1]
        try:
            self.escalate(stack, name, True, lambda: os.rmdir(name, dir_fd=parent.fd))
        except FileNotFoundError:
            pass

    def lift(self, stack: List[_Frame], wd: _Frame, name: str) -> None:
        """Cut: move stack[-1]/name to a fresh .sbx-flat-%07d at the top of
        the workdir (one fd-relative rename); EPERM clears flags first."""
        parent = stack[-1]
        flags_cleared = False
        while True:
            self.seq += 1
            if self.seq > _RM_MAX_OPS:
                raise _RemoveStop()
            target = "%s%07d" % (_FLAT_PREFIX, self.seq)
            self.tick()
            try:
                os.stat(target, dir_fd=wd.fd, follow_symlinks=False)
                continue                       # taken by an earlier attempt
            except FileNotFoundError:
                pass
            self.tick()
            try:
                os.rename(name, target, src_dir_fd=parent.fd, dst_dir_fd=wd.fd)
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                if exc.errno in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR):
                    continue
                if exc.errno not in (errno.EPERM, errno.EACCES) or flags_cleared:
                    raise _Resist() from None
            flags_cleared = True
            self.chmod_entry(parent.fd, name)
            self.clear_flags(stack, name)
            self.seq -= 1                      # retry the same name once

    def empty(self, wd: _Frame) -> None:
        """One pass: remove everything under the workdir bottom-up, lifting
        every subdirectory found _FLATTEN_DEPTH levels down."""
        stack = [self.root, wd]
        wd.pending = None
        while len(stack) > 1:
            frame = stack[-1]
            if frame.pending is None:
                frame.pending = self.list_dir(stack)
            if frame.pending:
                name, is_dir = frame.pending.pop()
                if is_dir and len(stack) - 1 >= _FLATTEN_DEPTH:
                    self.lift(stack, wd, name)
                    continue
                child = self.open_dir(stack, name) if is_dir else None
                if child is None:
                    self.unlink(stack, name)
                else:
                    stack.append(child)
                continue
            if frame.partial:
                frame.pending = None               # Q1: the rest of a huge directory
                continue
            if frame is wd:
                return
            stack.pop()
            self.owned.remove(frame)
            os.close(frame.fd)
            self.rmdir(stack, frame.name)

    def run(self, name: str) -> bool:
        stack = [self.root]
        self.tick()
        try:
            st = os.stat(name, dir_fd=self.root.fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(st.st_mode):
            self.unlink(stack, name)
            return True
        wd = self.open_dir(stack, name)
        if wd is None:
            self.unlink(stack, name)
            return True
        while True:
            self.empty(wd)
            self.tick()
            with os.scandir(wd.fd) as it:
                if next(it, None) is None:
                    break
        self.owned.remove(wd)
        os.close(wd.fd)
        self.rmdir(stack, name)
        return True


def remove_tree(root_fd: int, name: str, abs_root: Optional[str], deadline: float) -> bool:
    """Remove root_fd/name and everything under it (KD-18 FLATTEN remover).

    True when it is gone. False when the deadline or _RM_MAX_OPS was hit, or
    an entry resisted every fallback: the caller quarantines (KD-18 step 6);
    lifted segments persist, so a retry resumes where this call stopped.
    *abs_root* is no longer used by the removal itself: since validation
    round 1 (W2/W1) every darwin fallback is fd-relative and none takes a
    spelled path; it stays in the signature the callers and the suite pin,
    and is ignored. shutil.rmtree is never used.
    """
    del abs_root
    try:
        remover = _Remover(root_fd, deadline)
    except OSError as exc:
        log.warning("remove_tree: cannot stat the parent (%s)", _errno_name(exc))
        return False
    try:
        return remover.run(name)
    except _RemoveStop:
        log.info("remove_tree: deadline or operation bound hit after %d ops", remover.ops)
        return False
    except _Resist:
        log.info("remove_tree: an entry resisted every fallback")
        return False
    except OSError as exc:
        log.warning("remove_tree: gave up (%s)", _errno_name(exc))
        return False
    finally:
        remover.close_all()


def remove_workdir(root_fd: int, name: str, deadline: float) -> bool:
    """remove_tree on a workdir of the work root; False -> the caller
    quarantines it (Quarantine.add) and still returns the run's result."""
    return remove_tree(root_fd, name, _root_path(root_fd), deadline)


# -- the survivor sweep before every removal (validation round 1, W6) ---------
# sbx is reaped and the target's group killed before cleanup starts, but on
# darwin a process that setpgid()s or setsid()s out of that group survives
# the group kill (T11) and could still be WRITING into the workdir while the
# remover works on it -- the race the spelled-path fallbacks used to lose.
# So before remove_workdir, and before every quarantine retry, every euid
# process still in the run's session, or whose cwd is inside the tree about
# to be removed, is SIGKILLed. Bounded by the caller's cleanup deadline and
# by _SWEEP_ROUNDS. Declared limits: a survivor that left the session AND
# chdir()ed out of the tree but holds an fd into it is not found (no fd
# walk); a pid recycled between the check and the kill is a race the kernel
# gives no handle to close. Linux: bwrap --unshare-pid --die-with-parent
# tears the pid namespace down with sbx (W5), so only the group kill runs.

# darwin <sys/proc_info.h> / <libproc.h> / <sys/fcntl.h> (MacOSX14.2.sdk),
# measured on this host: struct proc_vnodepathinfo is two vnode_info_path
# (a 152-byte vnode_info + MAXPATHLEN path); the cwd path is at offset 152.
_PROC_UID_ONLY = 4
_PROC_PIDVNODEPATHINFO = 9
_VNODE_INFO_SIZE = 152
_MAXPATHLEN = 1024
_PROC_VNODEPATHINFO_SIZE = 2 * (_VNODE_INFO_SIZE + _MAXPATHLEN)
_F_GETPATH = 50
# At most this many euid pids are examined per round, and at most this many
# rounds (a survivor that forks between rounds is caught by the next one).
_SWEEP_PIDS_MAX = 16384
_SWEEP_ROUNDS = 5
_SWEEP_GAP_S = 0.01


def _fd_realpath(fd: int) -> Optional[str]:
    """The kernel's path of the directory *fd* (darwin F_GETPATH): the form
    a process's cwd is reported in. None elsewhere or on failure."""
    if sys.platform != "darwin":
        return None
    try:
        raw = fcntl.fcntl(fd, _F_GETPATH, bytes(_MAXPATHLEN))
    except OSError:
        return None
    path = os.fsdecode(raw.split(b"\0", 1)[0])
    return path if path.startswith("/") else None


def _euid_pids(ctypes_mod: Any, libc: Any) -> List[int]:
    want = libc.proc_listpids(_PROC_UID_ONLY, os.geteuid(), None, 0)
    if want <= 0:
        return []
    count = min(want // 4 + 64, _SWEEP_PIDS_MAX)
    buf = (ctypes_mod.c_int * count)()
    got = libc.proc_listpids(_PROC_UID_ONLY, os.geteuid(), buf, ctypes_mod.sizeof(buf))
    return [pid for pid in buf[:max(0, got) // 4] if pid > 0]


def _pid_cwd(ctypes_mod: Any, libc: Any, pid: int) -> Optional[str]:
    buf = ctypes_mod.create_string_buffer(_PROC_VNODEPATHINFO_SIZE)
    got = libc.proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, 0, buf, _PROC_VNODEPATHINFO_SIZE)
    if got != _PROC_VNODEPATHINFO_SIZE:
        return None                            # gone, a zombie, or not ours
    raw = buf.raw[_VNODE_INFO_SIZE:_VNODE_INFO_SIZE + _MAXPATHLEN]
    return os.fsdecode(raw.split(b"\0", 1)[0])


def _sweep_survivors(trees: List[str], sid: Optional[int], deadline: float) -> int:
    """SIGKILL what is left of a run before its tree is removed (W6).

    *sid* is the target's pid == pgid == sid (sbx setsid()s it), read from
    its `spawned` line; the target itself is reaped by now, so a LIVE
    session leader with that pid is somebody else's recycled pid and *sid*
    is then ignored. *trees* are the kernel paths (_fd_realpath form) of the
    directories about to be removed. Returns the number of kill(2)s made.
    """
    if sid is not None:
        try:
            if os.getsid(sid) == sid:
                sid = None                     # a live leader: not our target
        except OSError:
            pass
    killed = 0
    if sid is not None:
        try:
            os.killpg(sid, signal.SIGKILL)
            killed += 1
        except OSError:
            pass
    if sys.platform != "darwin":
        return killed
    prefixes = tuple(t.rstrip("/") for t in trees if t and t.startswith("/") and t != "/")
    if sid is None and not prefixes:
        return killed
    try:
        ctypes_mod, libc = _libc()
    except (OSError, AttributeError) as exc:
        log.warning("survivor sweep: libproc unavailable (%s)", type(exc).__name__)
        return killed
    me = os.getpid()
    for _ in range(_SWEEP_ROUNDS):
        hits = 0
        for pid in _euid_pids(ctypes_mod, libc):
            if time.monotonic() >= deadline:
                return killed + hits
            if pid == me:
                continue
            match = False
            if sid is not None:
                try:
                    match = os.getsid(pid) == sid
                except OSError:
                    continue                   # gone (or a zombie)
            if not match and prefixes:
                cwd = _pid_cwd(ctypes_mod, libc, pid)
                match = cwd is not None and any(cwd == p or cwd.startswith(p + "/")
                                                for p in prefixes)
            if match:
                try:
                    os.kill(pid, signal.SIGKILL)
                    hits += 1
                except OSError:
                    pass
        killed += hits
        if not hits or time.monotonic() + _SWEEP_GAP_S >= deadline:
            break
        time.sleep(_SWEEP_GAP_S)
    if killed:
        log.warning("survivor sweep: %d kill(s) before the removal", killed)
    return killed


class Quarantine:
    """Workdirs remove_tree could not remove (KD-18 step 6).

    add() renames one into quarantine/, or, when that rename fails, keeps
    its name in memory (stuck in place). count() is what quarantine/ holds
    plus the stuck names; above _QUARANTINE_MAX the runner is degraded.
    retry(deadline) hands every entry to remove_tree against ONE shared
    deadline; entries not reached stay and count.
    """

    def __init__(self, root_fd: int, quarantine_fd: int) -> None:
        self._root_fd = root_fd
        self._q_fd = quarantine_fd
        self._lock = threading.Lock()          # guards _stuck and _last_retry
        self._retry_lock = threading.Lock()    # one retry at a time
        self._stuck: set = set()
        self._last_retry: Optional[float] = None

    def add(self, name: str) -> bool:
        """Quarantine *name*; True when it was moved into quarantine/."""
        try:
            os.rename(name, name, src_dir_fd=self._root_fd, dst_dir_fd=self._q_fd)
            return True
        except OSError as exc:
            log.warning("quarantine: the rename failed (%s); the workdir stays in place",
                        _errno_name(exc))
        with self._lock:
            self._stuck.add(name)
        return False

    def _held(self) -> List[str]:
        try:
            return sorted(os.listdir(self._q_fd))
        except OSError as exc:
            log.warning("quarantine: cannot list quarantine/ (%s)", _errno_name(exc))
            return []

    def count(self) -> int:
        with self._lock:
            stuck = len(self._stuck)
        return len(self._held()) + stuck

    def over_cap(self) -> bool:
        return self.count() > _QUARANTINE_MAX

    def retry(self, deadline: float) -> int:
        """Try to remove every quarantined entry before *deadline* (the SAME
        absolute deadline for every remove_tree call); returns count()."""
        if not self._retry_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return self.count()
        try:
            with self._lock:
                self._last_retry = time.monotonic()
                stuck = sorted(self._stuck)
            root_path = _root_path(self._root_fd)
            q_path = (root_path + "/" + _QUARANTINE_NAME) if root_path is not None else None
            # W6: nothing still running in a quarantined tree races the
            # remover (every entry of quarantine/ and every stuck name is a
            # finished run's -- never an in-flight one's).
            real = _fd_realpath(self._root_fd)
            if real is not None:
                _sweep_survivors([real + "/" + _QUARANTINE_NAME]
                                 + [real + "/" + name for name in stuck], None, deadline)
            for name in self._held():
                if time.monotonic() >= deadline:
                    break
                remove_tree(self._q_fd, name, q_path, deadline)
            for name in stuck:
                if time.monotonic() >= deadline:
                    break
                if remove_tree(self._root_fd, name, root_path, deadline):
                    with self._lock:
                        self._stuck.discard(name)
        finally:
            self._retry_lock.release()
        return self.count()

    def maybe_retry(self, deadline: float) -> int:
        """The opportunistic post-run retry: at most once per
        _QUARANTINE_RETRY_EVERY_S, inside the run's own cleanup deadline."""
        with self._lock:
            last = self._last_retry
            due = last is None or time.monotonic() - last >= _QUARANTINE_RETRY_EVERY_S
            has = bool(self._stuck)
        if not due or (not has and not self._held()):
            return self.count()
        return self.retry(deadline)


# ---------------------------------------------------------------------------
# Execution engine (plan Step 11: argv, slots, capped read, status protocol)
# ---------------------------------------------------------------------------

# Every script's PATH ends with these two (M0:4: sbx admits their metadata,
# so a bare `sleep` or `cat` resolves).
_PATH_TAIL = ("/usr/bin", "/bin")
# The status pipe's unterminated-line ceiling; sbx's own line is <= 512 bytes.
_STATUS_BUFFER_MAX = 4096
# The sanitized sbx stderr line a pre-spawn refusal carries.
_SBX_LINE_MAX = 200
# How long one selector wait blocks before the stop condition is re-checked.
_SELECT_TICK_S = 0.05
# sbx's terminal `exit` outcomes; any other shape is ignored (T9).
_EXIT_OUTCOMES = frozenset({"exited", "signaled", "timeout", "refused"})

# The runner state main() builds (Step 12: RunnerState), read by run_one and
# capabilities(). None until the runner has started -- then every run is
# refused sandbox_unavailable. run_one reads exactly these members of it:
#   .cfg (SandboxConfig), .slots (RunSlots), .root_fd, .quarantine
#   (Quarantine), .degraded (None | "quarantine full"; run_one writes it after
#   every pre-run retry and every cleanup), .plan_cache (.get(name,
#   plan_deadline) -> PlanState).
_STATE: Any = None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _seccomp_on(mode: str) -> bool:
    """cfg.seccomp: "on" / "off" as written; "auto" only on Linux x86_64 --
    the ONE platform look the argv builder makes."""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return sys.platform.startswith("linux") and \
        platform.machine().lower() in ("x86_64", "amd64")


def _script_path(plan: InterpreterPlan) -> str:
    """The script's PATH: the interpreter's spelled and real directories,
    then /usr/bin:/bin -- nothing else."""
    dirs: List[str] = []
    for directory in (os.path.dirname(plan.path), os.path.dirname(plan.realpath)):
        if directory and directory not in dirs and directory not in _PATH_TAIL:
            dirs.append(directory)
    return ":".join(dirs + list(_PATH_TAIL))


def build_sbx_argv(plan: InterpreterPlan, req: RunRequest, wd: str, status_fd: int,
                   cfg: SandboxConfig,
                   egress: Optional[Tuple[str, Any]] = None) -> List[str]:
    """The sbx command line of one run (pure; golden per platform, rows D).

    sbx's environment is _spawn_sbx's two keys; the SCRIPT's is exactly
    PATH, HOME, TMPDIR, LANG and OUT (--clean-env + --env), plus the proxy
    variables when *egress* (an EgressProxy endpoint) is given. The entry is
    always absolute (<wd>/<entry>) and the caller's args follow it
    unchanged, after the `--` that ends sbx's own flags.
    """
    timeout_s = req.timeout_ms / 1000.0
    lim = cfg.limits
    argv = [sys.executable, "-I", "-B", cfg.sbx, "--restrict-reads", "--clean-env",
            "--env", "PATH=" + _script_path(plan), "--env", "HOME=" + wd,
            "--env", "TMPDIR=" + wd + "/tmp", "--env", "LANG=C.UTF-8",
            "--env", "OUT=" + wd + "/out"]
    egress_flag: List[str] = []
    if egress is not None:
        if egress[0] == "port":
            url = "http://127.0.0.1:%d" % int(egress[1])
            egress_flag = ["--egress-port", str(int(egress[1]))]
        elif egress[0] == "socket":
            url = "http://127.0.0.1:%d" % _EGRESS_LINUX_PORT
            egress_flag = ["--egress-socket", str(egress[1])]
        else:
            raise ValueError("unknown egress endpoint kind %r" % (egress[0],))
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            argv += ["--env", "%s=%s" % (key, url)]
        argv += ["--env", "NO_PROXY=", "--env", "no_proxy="]
    argv += ["--write", wd]
    for path in plan.reads:
        argv += ["--read", path]
    for path in plan.execs:
        argv += ["--exec", path]
    argv += ["--timeout", "%.3f" % timeout_s, "--status-fd", str(status_fd),
             "--rlimit-as", str(plan.as_limit),
             "--rlimit-cpu", str(int(math.ceil(timeout_s)) + 1),
             "--rlimit-nproc", "+%d" % lim.nproc_headroom,
             "--rlimit-fsize", str(lim.fsize),
             "--rlimit-nofile", str(lim.nofile),
             "--rlimit-core", "0"]
    argv += egress_flag
    if _seccomp_on(cfg.seccomp):
        argv.append("--seccomp")
    argv.append("--")
    argv += list(plan.argv_prefix)
    argv.append(wd + "/" + req.entry)
    argv += list(req.args)
    return argv


class RunSlots:
    """max_concurrent run slots. try_acquire() never blocks: a run beyond
    the slots is refused `busy` before any plan work (C-M6)."""

    def __init__(self, n: int) -> None:
        self._lock = threading.Lock()
        self._max = n
        self._used = 0

    @property
    def max(self) -> int:
        return self._max

    def in_flight(self) -> int:
        with self._lock:
            return self._used

    def try_acquire(self) -> bool:
        with self._lock:
            if self._used >= self._max:
                return False
            self._used += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._used > 0:
                self._used -= 1


def _cut_utf8(b: bytes) -> str:
    """A capped stream as text: a trailing INCOMPLETE UTF-8 sequence (the
    cap cut it) is dropped, never replaced; every invalid byte elsewhere is
    one U+FFFD. The incremental decoder with final=False is exactly that:
    it holds back a valid-so-far tail and replaces everything invalid."""
    return codecs.getincrementaldecoder("utf-8")("replace").decode(b, final=False)


def _parse_status_line(line: bytes) -> Optional[dict]:
    """One sbx status line through _strict_loads (R2:L10): a JSON object
    carrying "sbx": 1, else None (unknown shapes are ignored, T9)."""
    try:
        obj = _strict_loads(line.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict) or obj.get("sbx") != 1 or isinstance(obj.get("sbx"), bool):
        return None
    return obj


def _read_status_line(status_r: int, deadline: float) -> Optional[dict]:
    """Exactly one '\\n'-terminated status line from *status_r* before
    *deadline*, parsed; None on EOF, the deadline, a line over
    _STATUS_BUFFER_MAX or an unparseable line. Read one byte at a time so
    nothing after the line is consumed (the caller reads on)."""
    buf = bytearray()
    sel = selectors.DefaultSelector()
    try:
        sel.register(status_r, selectors.EVENT_READ)
        while len(buf) <= _STATUS_BUFFER_MAX:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not sel.select(remaining):
                return None
            try:
                byte = os.read(status_r, 1)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return None
            if not byte:
                return None
            if byte == b"\n":
                return _parse_status_line(bytes(buf))
            buf += byte
        return None
    finally:
        sel.close()


class _Capture:
    """One output stream: keep <= cap bytes, DISCARD the rest (truncated).
    The pipe is read to the end and never closed early, so the writer never
    gets an induced SIGPIPE (KD-7)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.chunks: List[bytes] = []
        self.size = 0
        self.truncated = False
        self.eof = False

    def want(self) -> int:
        return _READ_CHUNK

    def feed(self, chunk: bytes) -> None:
        room = self.cap - self.size
        if room > 0:
            keep = chunk[:room]
            self.chunks.append(keep)
            self.size += len(keep)
        if len(chunk) > room:
            self.truncated = True

    def data(self) -> bytes:
        return b"".join(self.chunks)

    def text(self) -> str:
        data = self.data()
        return _cut_utf8(data) if self.truncated else data.decode("utf-8", "replace")


class _StatusCapture:
    """sbx's --status-fd stream: '\\n'-split, every line through
    _strict_loads; the first `spawned` and the first terminal `exit` are
    kept. The pending (unterminated) buffer never exceeds
    _STATUS_BUFFER_MAX + 1 bytes; more is an overflow (-> internal)."""

    def __init__(self, reclaim: Optional[_Reclaim]) -> None:
        self._reclaim = reclaim
        self._buf = bytearray()
        self.overflow = False
        self.eof = False
        self.pgid: Optional[int] = None
        self.exit: Optional[dict] = None

    def want(self) -> int:
        return max(1, _STATUS_BUFFER_MAX + 1 - len(self._buf))

    def feed(self, chunk: bytes) -> None:
        if self.overflow:
            return
        self._buf += chunk
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            obj = _parse_status_line(line)
            if obj is not None:
                self._event(obj)
        if len(self._buf) > _STATUS_BUFFER_MAX:
            self.overflow = True
            self._buf.clear()

    def _event(self, obj: dict) -> None:
        event = obj.get("event")
        if event == "spawned" and self.pgid is None and self.exit is None:
            pgid = obj.get("pgid")
            # Never 0, 1 or our own group: killpg on those would hit us.
            if _is_int(pgid) and pgid > 1 and pgid != os.getpgrp():
                self.pgid = pgid
                if self._reclaim is not None:
                    self._reclaim.adopt_pgid(pgid)
        elif event == "exit" and self.exit is None and obj.get("outcome") in _EXIT_OUTCOMES:
            self.exit = obj


def _read_capped(captures: Dict[int, Any], deadline: float, stop: Any = None) -> None:
    """Pump every not-yet-EOF fd of *captures* (fd -> _Capture /
    _StatusCapture) through one selectors.DefaultSelector until each hit
    EOF, *stop()* is true, or *deadline* passes. Output beyond a capture's
    cap is read and discarded, never left in the pipe."""
    sel = selectors.DefaultSelector()
    try:
        for fd, cap in captures.items():
            if not cap.eof:
                sel.register(fd, selectors.EVENT_READ, cap)
        while sel.get_map():
            if stop is not None and stop():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for key, _events in sel.select(min(remaining, _SELECT_TICK_S)):
                cap = key.data
                try:
                    chunk = os.read(key.fd, cap.want())
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    chunk = b""
                if chunk:
                    cap.feed(chunk)
                else:
                    cap.eof = True
                    sel.unregister(key.fd)
    finally:
        sel.close()


def _feed_stdin(pipe: Any, data: bytes) -> None:
    """The stdin writer thread: write everything, then close. EPIPE (the
    target never read it, or is gone) ends it quietly. It owns the pipe:
    nothing else closes it, so the fd can never be reused under it."""
    try:
        fd = pipe.fileno()
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _signal_name(value: Any) -> Optional[str]:
    if not _is_int(value) or value <= 0:
        return None
    try:
        return signal.Signals(value).name
    except ValueError:
        return "SIG%d" % value


def _sbx_line(err: bytes) -> str:
    """One sanitized line of sbx's stderr (<= _SBX_LINE_MAX chars): its
    `sbx:` line when it wrote one, else the last non-empty line."""
    lines = [ln.strip() for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()]
    line = next((ln for ln in lines if ln.startswith("sbx:")), lines[-1] if lines else "")
    return "".join(ch if ch.isprintable() else "?" for ch in line)[:_SBX_LINE_MAX]


def _sbx_exit_logged(what: str, rc: Any, err: bytes) -> str:
    """O2: "sbx exit <rc>" for a caller-visible reason; sbx's own line (it
    may name host paths) goes to the runner's log only."""
    log.warning("%s: sbx exit %s: %r", what, rc, _sbx_line(err) or "no message")
    return "sbx exit %s" % rc


def _kill_target_group(pgid: int) -> None:
    """KD-1/KD-2 (E33): sbx died without a terminal `exit` event, so nobody
    swept the target's group; SIGKILL it. ESRCH (gone) is the normal case."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as exc:
        if exc.errno not in (errno.ESRCH, errno.EPERM):
            log.warning("run: killpg of the target group failed (%s)", _errno_name(exc))


def _wait_sbx(proc: subprocess.Popen, captures: Dict[int, Any], hard: float) -> bool:
    """Read while sbx runs, until it exits or the hard deadline; at the
    deadline SIGTERM sbx, _KILL_GRACE_S, SIGKILL. Returns True when the
    hard deadline fired. sbx is always reaped on return."""
    _read_capped(captures, hard, stop=lambda: proc.poll() is not None)
    if proc.poll() is None:
        remaining = hard - time.monotonic()
        if remaining > 0:
            try:
                proc.wait(remaining)
            except subprocess.TimeoutExpired:
                pass
    if proc.poll() is not None:
        return False
    log.warning("run: sbx overran its timeout by %.0fs; terminating it", _HARD_DEADLINE_SLACK_S)
    proc.send_signal(signal.SIGTERM)
    grace = time.monotonic() + _KILL_GRACE_S
    _read_capped(captures, grace, stop=lambda: proc.poll() is not None)
    if proc.poll() is None:
        try:
            proc.wait(max(0.0, grace - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
    proc.wait()
    return True


def _abandon(proc: subprocess.Popen, status: Optional[_StatusCapture]) -> None:
    """An unexpected failure mid-run: never leave sbx or its target behind."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if status is not None and status.exit is None and status.pgid is not None:
        _kill_target_group(status.pgid)


class _RunEgress:
    """One run's EgressProxy plus, on Linux, the 0700 socket directory it
    binds in -- <work_root>/egress/<32-hex>, created OUTSIDE the run
    workdir (the script can never touch it) and never under /tmp (sbx's
    egress_socket_refusal refuses a socket directory), removed by close()
    and, after a SIGKILL, by the next startup sweep. tcp on darwin, unix
    elsewhere."""

    def __init__(self, network: NetworkConfig, work_root: str) -> None:
        self._network = network
        self._egress_root = os.path.join(os.path.normpath(work_root), _EGRESS_NAME)
        self._dir: Optional[str] = None
        self._proxy: Optional[EgressProxy] = None
        self.endpoint: Optional[Tuple[str, Any]] = None

    def start(self) -> None:
        if sys.platform == "darwin":
            self._proxy = EgressProxy(self._network, "tcp")
        else:
            path = os.path.join(self._egress_root, secrets.token_hex(16))
            os.mkdir(path, 0o700)
            self._dir = path
            self._proxy = EgressProxy(self._network, "unix", sock_dir=self._dir)
        self._proxy.start()
        self.endpoint = self._proxy.endpoint

    def close(self, deadline: float) -> None:
        try:
            if self._proxy is not None:
                self._proxy.close(deadline)
        finally:
            if self._dir is not None:
                try:
                    os.unlink(os.path.join(self._dir, "s"))
                except OSError:
                    pass
                try:
                    os.rmdir(self._dir)
                except OSError as exc:
                    log.warning("egress: socket directory not removed (%s)",
                                _errno_name(exc))
                self._dir = None

    def summary(self) -> dict:
        if self._proxy is None:
            return {"tunnels": 0, "refused": 0}
        return self._proxy.summary()


def _spawn_and_read(cfg: SandboxConfig, req: RunRequest, plan: InterpreterPlan, wd: str,
                    wd_fd: int, run_deadline: float,
                    seen: Optional[Dict[str, Optional[int]]] = None) -> dict:
    """Spawn sbx on the materialized workdir, read its capped output and
    status, and shape the result (or a pre-spawn refusal). *seen* receives
    the target's pgid (== its sid) from `spawned`, for the cleanup's
    survivor sweep (W6)."""
    limits = cfg.limits
    egress: Optional[_RunEgress] = None
    if cfg.network is not None:
        egress = _RunEgress(cfg.network, cfg.work_root)
        try:
            egress.start()
        except EgressSocketTooLong as exc:
            egress.close(time.monotonic() + _EGRESS_CLOSE_S)
            log.warning("run: egress proxy could not start: %s", exc)
            return _tool_refusal("sandbox_unavailable", str(exc))
        except (OSError, ValueError) as exc:
            egress.close(time.monotonic() + _EGRESS_CLOSE_S)
            # an errno name, never an OSError message (it may carry a path);
            # an errno-less OSError (AF_UNIX path too long) carries none
            reason = (_errno_name(exc) if isinstance(exc, OSError) and exc.errno
                      else "%s: %s" % (type(exc).__name__, exc))
            log.warning("run: egress proxy could not start (%s)", reason)
            return _tool_refusal("sandbox_unavailable",
                                 "the egress proxy could not be started")
    try:
        status_r, status_w = os.pipe()
    except BaseException:
        if egress is not None:
            egress.close(time.monotonic() + _EGRESS_CLOSE_S)
        raise
    proc: Optional[subprocess.Popen] = None
    status: Optional[_StatusCapture] = None
    done = False
    try:
        argv = build_sbx_argv(plan, req, wd, status_w, cfg,
                              egress=egress.endpoint if egress is not None else None)
        try:
            proc = _spawn_sbx(argv, stdin_pipe=req.stdin is not None, status_w=status_w, cwd=wd)
        except OSError as exc:
            return _tool_refusal("sandbox_unavailable",
                                 "sbx could not be started (%s)" % _errno_name(exc))
        finally:
            os.close(status_w)
        spawn_t = time.monotonic()
        writer = None
        if req.stdin is not None and proc.stdin is not None:
            writer = threading.Thread(target=_feed_stdin, args=(proc.stdin, req.stdin),
                                      name="sandbox-stdin-feed", daemon=True)
            writer.start()
        out = _Capture(limits.stdout_bytes)
        err = _Capture(limits.stderr_bytes)
        status = _StatusCapture(_REQUEST_RECLAIM.get(None))
        outputs = {proc.stdout.fileno(): out, proc.stderr.fileno(): err}
        captures = dict(outputs)
        captures[status_r] = status
        hard = min(spawn_t + req.timeout_ms / 1000.0 + _HARD_DEADLINE_SLACK_S,
                   run_deadline - (_KILL_GRACE_S + _DRAIN_S + _CLEANUP_DEADLINE_S))
        hard_killed = _wait_sbx(proc, captures, hard)
        end_t = time.monotonic()
        # KD-8: sbx is reaped; drain what is left, ONE _DRAIN_S for all of it,
        # never waiting for an EOF a stray holder could withhold.
        drain = time.monotonic() + _DRAIN_S
        _read_capped({status_r: status}, drain)
        if status.exit is None and status.pgid is not None:
            _kill_target_group(status.pgid)
        _read_capped(outputs, drain)
        if writer is not None:
            writer.join(max(0.0, drain - time.monotonic()))
        done = True
        # sbx is reaped and its target group swept: no tunnel outlives the
        # run -- close the proxy now (bounded), so the summary is final.
        if egress is not None:
            egress.close(min(time.monotonic() + _EGRESS_CLOSE_S,
                             run_deadline - _CLEANUP_DEADLINE_S))

        if status.overflow:
            return _tool_refusal("internal", "sbx status protocol overflow")
        event = status.exit
        if status.pgid is None:
            # Never spawned: refused (exit 3), a usage error (exit 2) or sbx
            # dying first -- the script did not run. Validation round 1 (O2):
            # sbx's line may name host paths (its %r of a refused --read), so
            # the caller gets the code and a FIXED message; the raw line goes
            # to the runner's own log only.
            line = _sbx_line(err.data())
            if not line and event is not None and isinstance(event.get("reason"), str):
                line = "".join(ch if ch.isprintable() else "?"
                               for ch in event["reason"])[:_SBX_LINE_MAX]
            log.warning("run: sbx refused the run (exit %s): %r", proc.returncode,
                        line or "no message")
            return _tool_refusal("sandbox_unavailable", "sbx refused the run (exit %s)"
                                 % proc.returncode)

        # After `spawned` every outcome is a NORMAL result, an engine-stage
        # failure included (KD-16, declared).
        exit_code: Optional[int] = None
        sig_name: Optional[str] = None
        timed_out = False
        outcome = event.get("outcome") if event is not None else None
        if hard_killed:
            timed_out, sig_name = True, "SIGKILL"
        elif outcome == "exited" and _is_int(event.get("code")):
            exit_code = event["code"]
        elif outcome == "signaled" and _signal_name(event.get("signal")) is not None:
            sig_name = _signal_name(event.get("signal"))
        elif outcome == "timeout":
            timed_out = True
            sig_name = _signal_name(event.get("signal")) or "SIGKILL"
        else:
            rc = proc.returncode
            if rc is not None and rc < 0:
                sig_name = _signal_name(-rc)
            else:
                exit_code = rc
        duration = event.get("durationMs") if event is not None else None
        if not _is_int(duration) or duration < 0:
            duration = max(0, int((end_t - spawn_t) * 1000))
        out_files, out_truncated = collect_out(wd_fd, limits)
        result = {
            "exitCode": exit_code,
            "signal": sig_name,
            "timedOut": timed_out,
            "durationMs": duration,
            "stdout": out.text(),
            "stderr": err.text(),
            "stdoutTruncated": out.truncated,
            "stderrTruncated": err.truncated,
            "outFiles": out_files,
            "outFilesTruncated": out_truncated,
        }
        if egress is not None:
            result["egress"] = egress.summary()
        log.info("run: interpreter=%s exit=%s signal=%s timedOut=%s durationMs=%d "
                 "stdout=%d stderr=%d outFiles=%d", req.interpreter, exit_code, sig_name,
                 timed_out, duration, out.size, err.size, len(out_files))
        if cfg.log_content:
            log.info("run content: stdout=%s stderr=%s",
                     _log_value(result["stdout"]), _log_value(result["stderr"]))
        return result
    finally:
        if seen is not None and status is not None and _is_int(status.pgid) \
                and status.pgid > 1:
            seen["pgid"] = status.pgid
        if proc is not None:
            if not done:
                _abandon(proc, status)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            # proc.stdin belongs to the writer thread (or was never opened).
        os.close(status_r)
        if egress is not None:
            # Idempotent after the close above; the path for every early
            # return and exception (sbx already abandoned/reaped here).
            egress.close(time.monotonic() + _EGRESS_CLOSE_S)


def _cleanup_workdir(state: Any, name: str, sid: Optional[int] = None) -> None:
    """KD-18 step 6: remove the workdir within _CLEANUP_DEADLINE_S, else
    quarantine it (the run's result is still returned); the opportunistic
    quarantine retry shares the same deadline; then re-judge degraded.
    W6: first, inside the same deadline, the run's survivors are swept --
    its session *sid* (the target's pid from `spawned`, None when it never
    spawned) and every process whose cwd is inside the workdir."""
    deadline = time.monotonic() + _CLEANUP_DEADLINE_S
    try:
        real = _fd_realpath(state.root_fd)
        _sweep_survivors([real + "/" + name] if real is not None else [], sid, deadline)
        if not remove_workdir(state.root_fd, name, deadline):
            state.quarantine.add(name)
        state.quarantine.maybe_retry(deadline)
    finally:
        state.degraded = "quarantine full" if state.quarantine.over_cap() else None


def _run_in_slot(state: Any, req: RunRequest) -> dict:
    """run_one steps (2)-(5), inside the slot."""
    cfg = state.cfg
    run_deadline = time.monotonic() + _RUN_DEADLINE_S
    if state.degraded is not None:
        # R3:M4: one bounded retry first; still above the cap -> refused.
        if state.quarantine.retry(time.monotonic() + _DEGRADED_RETRY_S) > _QUARANTINE_MAX:
            return _tool_refusal("sandbox_unavailable",
                                 "the runner is degraded (%s)" % state.degraded)
        state.degraded = None
    root_path = _root_path(state.root_fd)
    if root_path is None:
        return _tool_refusal("sandbox_unavailable", "the work root no longer resolves")
    refusal = _check_free_space(root_path, cfg.limits.min_free_bytes)
    if refusal is not None:
        return refusal
    # R4:M2: the ONE plan deadline of this run, never re-armed.
    plan_deadline = time.monotonic() + _REFRESH_DEADLINE_S
    plan_state = state.plan_cache.get(req.interpreter, plan_deadline)
    if plan_state.state != "ready" or plan_state.plan is None:
        return _tool_refusal("sandbox_unavailable", "interpreter %s is not ready (%s)"
                             % (req.interpreter, plan_state.reason or plan_state.state))
    name: Optional[str] = None
    wd_fd = -1
    seen: Dict[str, Optional[int]] = {"pgid": None}
    try:
        try:
            name, wd_fd = create_workdir(state.root_fd)
            materialize(wd_fd, req.files)
        except RunRefusal as exc:
            return _tool_refusal(exc.code, str(exc))
        except OSError as exc:
            return _tool_refusal("sandbox_unavailable",
                                 "the work directory could not be prepared (%s)" % _errno_name(exc))
        return _spawn_and_read(cfg, req, plan_state.plan, root_path + "/" + name, wd_fd,
                               run_deadline, seen)
    finally:
        if wd_fd >= 0:
            os.close(wd_fd)
        if name is not None:
            _cleanup_workdir(state, name, seen["pgid"])


def run_one(req: RunRequest) -> dict:
    """One validated run, end to end -> the result object or a refusal.

    (1) slot FIRST, non-blocking -> busy (retryAfterMs 1000) before any plan
    work; (2) the run deadline; (3) degraded retry, free-space precheck;
    (4) the plan, under one fixed plan_deadline; (5) workdir, materialize,
    _spawn_sbx, capped read, pgid adoption, hard deadline, KD-1 sweep, drain;
    finally collect_out / remove or quarantine, release the slot. A cancel
    reaches sbx through _Reclaim; this thread's result is then discarded.
    """
    state = _STATE
    if state is None:
        return _tool_refusal("sandbox_unavailable", "the runner has not started")
    slots = state.slots
    if not slots.try_acquire():
        return _tool_refusal("busy", "%d runs in flight (max_concurrent=%d)"
                             % (slots.in_flight(), slots.max), retryAfterMs=1000)
    try:
        return _run_in_slot(state, req)
    finally:
        slots.release()


# ---------------------------------------------------------------------------
# Interpreter plans, VSZ baselines, self-test, self-refresh (plan Step 12)
# ---------------------------------------------------------------------------
# A plan is computed at startup and recomputed by the run that finds its
# KD-13 signature changed. Every spawn here is an sbx spawn through
# _spawn_sbx (VSZ probe, --version, self-test) or a /bin/ps sample through
# _run_child, so a cancel of the run that triggered a refresh reaches them.

# Where the backend has been verified by the live suite (G8).
LIVE_VERIFIED = {"darwin": True, "linux": False}
# The self-test's entry file name; the G6/G12/G13 fixtures key on it.
_SELF_TEST_NAME = "sbx-self-test"
# R-11: a refresh for the same failing signature is not repeated sooner.
_REFRESH_SUPPRESS_S = 5.0
# Each planning spawn's OWN cap, handed to sbx as --timeout; the runner waits
# for the spawn until the plan deadline only (KD-17: the cap never adds a
# term outside the one plan term).
_PLAN_SPAWN_CAP_S = 10.0
# One /bin/ps VSZ sample is bounded by this and by the remaining plan term.
_PS_SAMPLE_CAP_S = 5.0
_VSZ_SAMPLES = 3
_VSZ_SAMPLE_GAP_S = 0.1
# as_limit = max(interpreter VSZ, launcher VSZ) + as_budget + this.
_AS_HEADROOM = 256 * _MIB
# What a planning spawn's stdout/stderr may hold (a version line, "ok").
_PLAN_OUTPUT_MAX = 64 * _KIB
_VERSION_MAX = 120
_REASON_MAX = 200
# M0:5: /bin/sh opens it on every invocation on darwin.
_DARWIN_SELECT_SH = "/private/var/select/sh"

# Per interpreter: the flags after its path (argv_prefix), the stdin-blocking
# VSZ probe, and the self-test script (bash: builtins only -- under env={}
# bash has no PATH). python3 runs with -S unless its config entry says
# "site": true (_SITE_ARGV_FLAGS): -I is kept either way, so the user site
# and every PYTHON* variable stay off; only the site module (and with it a
# venv's site-packages) comes back.
_ARGV_FLAGS: Dict[str, Tuple[str, ...]] = {
    "python3": ("-I", "-S", "-B"),
    "bash": ("--noprofile", "--norc"),
    "node": (),
}
_SITE_ARGV_FLAGS: Dict[str, Tuple[str, ...]] = {
    "python3": ("-I", "-B"),
}
_VSZ_PROBES: Dict[str, Tuple[str, ...]] = {
    "python3": ("-c", "import sys; sys.stdin.read()"),
    "bash": ("-c", "read -r _"),
    "node": ("-e", "process.stdin.resume()"),
}
_SELF_TEST_SCRIPTS: Dict[str, bytes] = {
    "python3": b'print("ok")\n',
    "bash": b"echo ok\n",
    "node": b'console.log("ok")\n',
}


class PlanError(Exception):
    """An interpreter that cannot be planned (missing, not executable, a
    planning spawn that failed); the message names the rule, never bytes."""


class SelfTestFailed(Exception):
    """The self-test did not exit 0 printing `ok`; `outcome` says how."""

    def __init__(self, outcome: str) -> None:
        super().__init__("self-test failed (%s)" % outcome)
        self.outcome = outcome


class StartupRefusal(Exception):
    """Startup planning refused; main() writes the message and exits 1."""


class _RefreshDeadline(Exception):
    """The plan term ran out before (or during) a planning spawn."""


def _platform_key() -> Optional[str]:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def _engine_path() -> Optional[str]:
    """The sandbox engine sbx will run, found the way sbx finds it (its PATH
    is _CHILD_ENV's); None when there is none (fail closed)."""
    key = _platform_key()
    if key == "darwin":
        path = "/usr/bin/sandbox-exec"
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    if key == "linux":
        return shutil.which("bwrap", path=_CHILD_ENV["PATH"])
    return None


def _clean_line(text: str, limit: int) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in text.strip())[:limit]


def _reason(exc: BaseException) -> str:
    """A refresh failure as "<ExcType>: <rule>", one line, <= 200 chars."""
    text = str(exc)
    return _clean_line("%s: %s" % (type(exc).__name__, text) if text else type(exc).__name__,
                       _REASON_MAX)


def _remaining(plan_deadline: float) -> float:
    remaining = plan_deadline - time.monotonic()
    if remaining <= 0:
        raise _RefreshDeadline()
    return remaining


def _check_cancelled() -> None:
    """R3:L4: a spawn that returned after its request was cancelled is a
    cancel, never a failure to judge."""
    reclaim = _REQUEST_RECLAIM.get(None)
    if reclaim is not None and reclaim.cancelled:
        raise RequestCancelled()


def _chain_signature(paths) -> Tuple[Tuple[str, int, int], ...]:
    """KD-13: (path, st_ino, st_mtime_ns) of each path by os.lstat; a
    missing or unreadable path is (path, -1, -1), so its (re)appearance
    changes the signature too."""
    out: List[Tuple[str, int, int]] = []
    for path in paths:
        try:
            st = os.lstat(path)
            out.append((path, st.st_ino, st.st_mtime_ns))
        except OSError:
            out.append((path, -1, -1))
    return tuple(out)


def _linux_system_dir(directory: str) -> bool:
    top = directory.split("/")[1] if directory.startswith("/") else ""
    return top in ("usr", "bin", "sbin") or top.startswith("lib")


def _spec_paths(spec: InterpreterSpec) -> Tuple[str, ...]:
    """The configured spelling's hops and realpath plus the extra reads --
    what a failed plan's signature watches for a repair."""
    try:
        hops, real = _symlink_hops(spec.path)
    except MachOError:
        hops, real = (), spec.path
    out: List[str] = []
    for path in tuple(hops) + (spec.path, real) + tuple(spec.extra_reads):
        if path not in out:
            out.append(path)
    return tuple(out)


def plan_interpreter(spec: InterpreterSpec, cfg: SandboxConfig,
                     plan_deadline: Optional[float] = None) -> InterpreterPlan:
    """The read/exec sets and the KD-13 signature of one interpreter; no
    spawn (version, VSZ and as_limit are filled by the refresh).

    macOS: reads = the spelling's hops + the closure's spellings, images and
    kegs + extra_reads + /private/var/select/sh (M0:5); Linux: extra_reads
    plus the realpath's directory when outside /usr, /bin, /sbin, /lib*.
    execs = the realpath, /bin and /usr/bin (S-L1), and on macOS the
    framework launcher's Python.app image as its REALPATH (C-H2). Symlink
    hops are reads, never execs (M0:3). The closure walk spends the caller's
    *plan_deadline* (C1: KD-17's one plan term covers it too).
    """
    try:
        hops, real = _symlink_hops(spec.path)
    except MachOError as exc:
        raise PlanError("the path cannot be resolved (%s)" % exc) from None
    try:
        st = os.stat(real)
    except OSError as exc:
        raise PlanError("the interpreter does not exist (%s)" % _errno_name(exc)) from None
    if not stat.S_ISREG(st.st_mode):
        raise PlanError("the interpreter is not a regular file")
    if not os.access(real, os.X_OK):
        raise PlanError("the interpreter is not executable")

    reads: List[str] = []
    execs: List[str] = [real]
    sig_paths: List[str] = []

    def _add(seq: List[str], path: str) -> None:
        if path and path not in seq:
            seq.append(path)

    for hop in hops:
        _add(reads, hop)
        _add(sig_paths, hop)
    _add(sig_paths, real)
    if _platform_key() == "darwin":
        closure = dylib_closure(spec.path, plan_deadline)
        for path in closure.spellings:
            _add(reads, path)
        for path in closure.images:
            if not _is_system_path(path):
                _add(reads, path)
            _add(sig_paths, path)
        for keg in closure.kegs:
            _add(reads, keg)
            _add(sig_paths, keg)
        launcher = _framework_launcher(real)
        if launcher is not None and launcher[0] is not None:
            m = _FRAMEWORK_RE.match(real)
            if m is not None:
                spelled = "%s/Versions/%s/Resources/Python.app/Contents/MacOS/Python" % (
                    m.group(1), m.group(2))
                l_hops, _l_real = _symlink_hops(spelled)
                for hop in l_hops:
                    if not _is_system_path(hop):
                        _add(reads, hop)
                        _add(sig_paths, hop)
            _add(execs, launcher[0])
            _add(sig_paths, launcher[0])
        _add(reads, _DARWIN_SELECT_SH)
    else:
        directory = os.path.dirname(real)
        if not _linux_system_dir(directory):
            _add(reads, directory)
    for path in spec.extra_reads:
        _add(reads, path)
        _add(sig_paths, path)
    _add(execs, "/bin")
    _add(execs, "/usr/bin")
    # argv_prefix feeds the VSZ probe, the --version run, the self-test and
    # every run alike, so "site" reaches all of them from here.
    flags = _SITE_ARGV_FLAGS if spec.site else _ARGV_FLAGS
    return InterpreterPlan(
        name=spec.name,
        path=spec.path,
        realpath=real,
        version="",
        argv_prefix=(spec.path,) + flags.get(spec.name, ()),
        reads=tuple(reads),
        execs=tuple(execs),
        signature=_chain_signature(sig_paths),
        vsz_baseline=0,
        as_limit=0,
    )


def _planning_argv(plan: InterpreterPlan, cfg: SandboxConfig, wd: str, status_fd: int,
                   plan_deadline: float, tail: Optional[Tuple[str, ...]] = None,
                   with_as: bool = True) -> List[str]:
    """The production sbx argv for a planning spawn: its own cap
    (min(_PLAN_SPAWN_CAP_S, remaining)) as --timeout; `tail` replaces the
    entry + args after `--`; with_as False drops --rlimit-as (VSZ probe)."""
    timeout_s = min(_PLAN_SPAWN_CAP_S, _remaining(plan_deadline))
    req = RunRequest(files=(), entry=_SELF_TEST_NAME, interpreter=plan.name, args=(),
                     stdin=None, timeout_ms=max(1, int(timeout_s * 1000)))
    argv = build_sbx_argv(plan, req, wd, status_fd, cfg)
    if tail is None and with_as:
        return argv
    sep = argv.index("--")
    head = argv[:sep]
    if not with_as:
        i = head.index("--rlimit-as")
        del head[i:i + 2]
    return head + ["--"] + (list(tail) if tail is not None else argv[sep + 1:])


def _stop_spawn(proc: subprocess.Popen) -> None:
    """The plan deadline hit a spawn: SIGTERM its group, SIGKILL after the
    grace; always reaped on return."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        proc.wait()


def _planning_spawn(make_argv, wd: str, plan_deadline: float, stdin_pipe: bool = False,
                    on_spawned=None) -> Tuple[Optional[dict], bytes, bytes, Optional[int],
                                              Optional[int]]:
    """One planning sbx spawn, read to its end within the plan deadline.

    -> (terminal exit event, stdout, stderr, sbx returncode, target pgid).
    `on_spawned(target_pid, sbx_pid)` runs once the `spawned` line arrived,
    while stdin (stdin_pipe) is still open; stdin is closed after it. A
    spawn still running at the plan deadline is stopped and the refresh
    ends with _RefreshDeadline; a cancelled request raises RequestCancelled.
    """
    _remaining(plan_deadline)
    status_r, status_w = os.pipe()
    proc: Optional[subprocess.Popen] = None
    status: Optional[_StatusCapture] = None
    try:
        try:
            proc = _spawn_sbx(make_argv(status_w), stdin_pipe=stdin_pipe, status_w=status_w,
                              cwd=wd)
        finally:
            os.close(status_w)
        out = _Capture(_PLAN_OUTPUT_MAX)
        err = _Capture(_PLAN_OUTPUT_MAX)
        status = _StatusCapture(_REQUEST_RECLAIM.get(None))
        outputs = {proc.stdout.fileno(): out, proc.stderr.fileno(): err}
        captures: Dict[int, Any] = dict(outputs)
        captures[status_r] = status
        live = proc
        try:
            if on_spawned is not None:
                _read_capped(captures, plan_deadline,
                             stop=lambda: (status.pgid is not None or status.exit is not None
                                           or live.poll() is not None))
                if status.pgid is not None and status.exit is None and live.poll() is None:
                    on_spawned(status.pgid, live.pid)
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        _read_capped(captures, plan_deadline, stop=lambda: live.poll() is not None)
        if proc.poll() is None:
            remaining = plan_deadline - time.monotonic()
            if remaining > 0:
                try:
                    proc.wait(remaining)
                except subprocess.TimeoutExpired:
                    pass
        cut = proc.poll() is None
        if cut:
            _stop_spawn(proc)
        drain = time.monotonic() + _DRAIN_S
        _read_capped({status_r: status}, drain)
        if status.exit is None and status.pgid is not None:
            _kill_target_group(status.pgid)
        _read_capped(outputs, drain)
        _check_cancelled()
        if cut:
            raise _RefreshDeadline()
        return status.exit, out.data(), err.data(), proc.returncode, status.pgid
    except BaseException:
        if proc is not None and proc.poll() is None:
            _abandon(proc, status)
        raise
    finally:
        if proc is not None:
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
        os.close(status_r)


def _ps_vsz(pid: int, plan_deadline: float) -> int:
    """One /bin/ps VSZ sample of *pid* in bytes, bounded by the remaining
    plan term (R4:L2); a sample that times out ends the refresh."""
    timeout = min(_PS_SAMPLE_CAP_S, _remaining(plan_deadline))
    try:
        done = _run_child(["/bin/ps", "-o", "vsz=", "-p", str(pid)], timeout, None, env={})
    except subprocess.TimeoutExpired:
        _check_cancelled()
        raise _RefreshDeadline() from None
    _check_cancelled()
    text = (done.stdout or "").strip()
    if done.returncode != 0 or not text.isdigit():
        raise PlanError("a VSZ sample failed (ps exit %s)" % done.returncode)
    return int(text) * _KIB


def measure_vsz(plan: InterpreterPlan, cfg: SandboxConfig, plan_deadline: float,
                wd: str) -> Tuple[int, int]:
    """(interpreter VSZ, sbx launcher VSZ) in bytes (R2:M2): the interpreter
    under the production argv MINUS --rlimit-as, blocked on its open stdin,
    sampled _VSZ_SAMPLES times _VSZ_SAMPLE_GAP_S apart with /bin/ps; the max
    of each. On Linux with as_mode absolute nothing is measured: (0, 0)."""
    if _platform_key() != "darwin" and cfg.limits.as_mode != "baseline":
        return 0, 0
    tail = plan.argv_prefix + _VSZ_PROBES.get(plan.name, ())
    samples: Dict[str, int] = {}

    def _sample(target_pid: int, sbx_pid: int) -> None:
        for _ in range(_VSZ_SAMPLES):
            gap = min(_VSZ_SAMPLE_GAP_S, _remaining(plan_deadline))
            time.sleep(gap)
            samples["interp"] = max(samples.get("interp", 0), _ps_vsz(target_pid, plan_deadline))
            samples["sbx"] = max(samples.get("sbx", 0), _ps_vsz(sbx_pid, plan_deadline))

    _event, _out, err, rc, _pgid = _planning_spawn(
        lambda fd: _planning_argv(plan, cfg, wd, fd, plan_deadline, tail=tail, with_as=False),
        wd, plan_deadline, stdin_pipe=True, on_spawned=_sample)
    if "interp" not in samples or "sbx" not in samples:
        raise PlanError("the VSZ probe did not run (%s)"
                        % _sbx_exit_logged("plan: VSZ probe", rc, err))
    return samples["interp"], samples["sbx"]


def _version_run(plan: InterpreterPlan, cfg: SandboxConfig, plan_deadline: float,
                 wd: str) -> str:
    """`<interpreter> --version` under the production argv: its first line,
    sanitized, <= 120 chars."""
    event, out, err, rc, pgid = _planning_spawn(
        lambda fd: _planning_argv(plan, cfg, wd, fd, plan_deadline,
                                  tail=plan.argv_prefix + ("--version",)),
        wd, plan_deadline)
    if pgid is None:
        raise PlanError("the --version run was refused (%s)"
                        % _sbx_exit_logged("plan: --version", rc, err))
    if event is None or event.get("outcome") != "exited" or event.get("code") != 0:
        raise PlanError("the --version run failed (%s)" % _spawn_outcome(event, rc))
    for stream in (out, err):
        for line in stream.decode("utf-8", "replace").splitlines():
            if line.strip():
                return _clean_line(line, _VERSION_MAX)
    return "(unknown)"


def _spawn_outcome(event: Optional[dict], rc: Optional[int]) -> str:
    if event is None:
        return "no exit event, sbx exit %s" % rc
    outcome = event.get("outcome")
    if outcome == "exited":
        return "exit %s" % event.get("code")
    if outcome == "signaled":
        return "signal %s" % (_signal_name(event.get("signal")) or "?")
    return str(outcome)


def self_test(plan: InterpreterPlan, plan_deadline: float, cfg: SandboxConfig, wd: str) -> None:
    """The full production argv (incl. --rlimit-as) on the `sbx-self-test`
    file of *wd*: exit 0 printing `ok`, else SelfTestFailed. Its own cap
    (sbx --timeout) is min(10 s, remaining); the runner waits for it until
    the plan deadline only (a spawn cut there is _RefreshDeadline)."""
    event, out, err, rc, pgid = _planning_spawn(
        lambda fd: _planning_argv(plan, cfg, wd, fd, plan_deadline), wd, plan_deadline)
    if pgid is None:
        raise SelfTestFailed(_sbx_exit_logged("plan: self-test", rc, err))
    if event is None or event.get("outcome") != "exited" or event.get("code") != 0:
        raise SelfTestFailed(_spawn_outcome(event, rc))
    if out != b"ok\n":
        raise SelfTestFailed("unexpected output")


class PlanCache:
    """Every configured interpreter's PlanState, refreshed by the run that
    finds its KD-13 signature changed (plan Step 12).

    One mutex guards the entries; one lock per interpreter serialises its
    refresh. get() spends at most the caller's ONE plan_deadline (R4:M2):
    the wait on another run's refresh and any refresh of its own.
    """

    class _Entry:
        def __init__(self, spec: InterpreterSpec) -> None:
            self.spec = spec
            self.lock = threading.Lock()
            self.state = PlanState(None, "refresh-failed", "not planned yet")
            self.paths: Tuple[str, ...] = ()
            self.signature: Tuple[Tuple[str, int, int], ...] = ()
            self.failed_at: Optional[float] = None

    def __init__(self, state: Any) -> None:
        self._state = state
        self._mu = threading.Lock()
        self._entries = {name: PlanCache._Entry(spec)
                         for name, spec in state.cfg.interpreters.items()}

    def peek(self, name: str) -> PlanState:
        with self._mu:
            return self._entries[name].state

    def _current(self, entry: "PlanCache._Entry") -> Optional[PlanState]:
        """The state to answer without a refresh, or None when one is due."""
        with self._mu:
            state, paths, sig, failed_at = (entry.state, entry.paths, entry.signature,
                                            entry.failed_at)
        if _chain_signature(paths) != sig:
            return None
        if state.state == "ready":
            return state
        if failed_at is not None and time.monotonic() - failed_at < _REFRESH_SUPPRESS_S:
            return state
        return None

    def admitted(self) -> FrozenSet[str]:
        """validate_run_params' healthy set: every interpreter a run may try
        -- ready, or due a refresh; NOT one whose same failing signature is
        inside the R-11 suppression window."""
        out = set()
        for name, entry in self._entries.items():
            with self._mu:
                state, failed_at = entry.state, entry.failed_at
            if state.state == "ready" or failed_at is None \
                    or time.monotonic() - failed_at >= _REFRESH_SUPPRESS_S \
                    or self._current(entry) is None:
                out.add(name)
        return frozenset(out)

    def _ready(self, entry: "PlanCache._Entry", plan: InterpreterPlan) -> None:
        with self._mu:
            entry.state = PlanState(plan, "ready", None)
            entry.paths = tuple(path for path, _ino, _mt in plan.signature)
            entry.signature = plan.signature
            entry.failed_at = None

    def _fail(self, entry: "PlanCache._Entry", reason: str) -> None:
        with self._mu:
            paths = list(entry.paths)
        for path in _spec_paths(entry.spec):
            if path not in paths:
                paths.append(path)
        signature = _chain_signature(paths)
        with self._mu:
            entry.state = PlanState(None, "refresh-failed", _clean_line(reason, _REASON_MAX))
            entry.paths = tuple(paths)
            entry.signature = signature
            entry.failed_at = time.monotonic()

    def get(self, name: str, plan_deadline: float) -> PlanState:
        """The interpreter's PlanState for one run (KD-13, R4:M2, R3:L4).

        Signature equal -> the current state. Differs -> the per-interpreter
        lock, waited on until plan_deadline at most; re-checked after it
        (another run's refresh result); still differs -> this run refreshes
        against the SAME plan_deadline. A deadline exhausted after a wait
        refuses THIS run (RunRefusal sandbox_unavailable "refresh deadline")
        with no state change and no failure timestamp; RequestCancelled
        propagates with the prior state kept.
        """
        entry = self._entries[name]
        state = self._current(entry)
        if state is not None:
            return state
        waited = False
        if not entry.lock.acquire(blocking=False):
            waited = True
            remaining = plan_deadline - time.monotonic()
            if remaining <= 0 or not entry.lock.acquire(timeout=remaining):
                raise RunRefusal("sandbox_unavailable",
                                 "interpreter %s: refresh deadline (another run's refresh "
                                 "held it)" % name)
        try:
            state = self._current(entry)
            if state is not None:
                return state
            try:
                plan = self._refresh(entry.spec, plan_deadline)
            except RequestCancelled:
                raise
            except _RefreshDeadline:
                if waited:
                    raise RunRefusal("sandbox_unavailable",
                                     "interpreter %s: refresh deadline" % name) from None
                log.warning("plan refresh: interpreter %s ran out of its refresh deadline",
                            name)
                self._fail(entry, "refresh deadline")
            except Exception as exc:
                log.warning("plan refresh: interpreter %s failed (%s)", name,
                            type(exc).__name__)
                self._fail(entry, _reason(exc))
            else:
                self._ready(entry, plan)
            return self.peek(name)
        finally:
            entry.lock.release()

    def plan_all(self, deadline: float) -> None:
        """Startup (R3:M4): every interpreter planned against ONE shared
        *deadline*, each plan's spawns additionally capped by
        _REFRESH_DEADLINE_S. Any failure raises StartupRefusal."""
        for name in sorted(self._entries):
            entry = self._entries[name]
            now = time.monotonic()
            if now >= deadline:
                raise StartupRefusal("interpreter planning exceeded %d s"
                                     % int(_STARTUP_PLANS_DEADLINE_S))
            plan_deadline = min(deadline, now + _REFRESH_DEADLINE_S)
            with entry.lock:
                try:
                    plan = self._refresh(entry.spec, plan_deadline)
                except _RefreshDeadline:
                    if plan_deadline >= deadline:
                        raise StartupRefusal("interpreter planning exceeded %d s"
                                             % int(_STARTUP_PLANS_DEADLINE_S)) from None
                    raise StartupRefusal("interpreter %s: planning exceeded the %d s refresh "
                                         "deadline" % (name, int(_REFRESH_DEADLINE_S))) from None
                except SelfTestFailed as exc:
                    raise StartupRefusal("interpreter %s self-test failed (%s)"
                                         % (name, _clean_line(exc.outcome, _REASON_MAX))) \
                        from None
                except Exception as exc:
                    raise StartupRefusal("interpreter %s: %s" % (name, _reason(exc))) from None
                self._ready(entry, plan)

    def _refresh(self, spec: InterpreterSpec, plan_deadline: float) -> InterpreterPlan:
        """plan_interpreter + VSZ + --version + self-test in one planning
        workdir (removed after, quarantined when it resists)."""
        st = self._state
        cfg = st.cfg
        _remaining(plan_deadline)
        plan = plan_interpreter(spec, cfg, plan_deadline)
        root = _root_path(st.root_fd)
        if root is None:
            raise PlanError("the work root no longer resolves")
        name, wd_fd = create_workdir(st.root_fd)
        try:
            try:
                materialize(wd_fd, ((_SELF_TEST_NAME,
                                     _SELF_TEST_SCRIPTS.get(spec.name, b""), False),))
            finally:
                os.close(wd_fd)
            wd = root + "/" + name
            vsz_interp, vsz_sbx = measure_vsz(plan, cfg, plan_deadline, wd)
            if vsz_interp or vsz_sbx:
                as_limit = max(vsz_interp, vsz_sbx) + cfg.limits.as_budget + _AS_HEADROOM
            else:
                as_limit = cfg.limits.as_budget
            plan = plan._replace(vsz_baseline=vsz_interp, as_limit=as_limit)
            plan = plan._replace(version=_version_run(plan, cfg, plan_deadline, wd))
            self_test(plan, plan_deadline, cfg, wd)
            return plan
        finally:
            cleanup_deadline = time.monotonic() + _CLEANUP_DEADLINE_S
            real = _fd_realpath(st.root_fd)
            if real is not None:                       # W6, as _cleanup_workdir
                _sweep_survivors([real + "/" + name], None, cleanup_deadline)
            if not remove_workdir(st.root_fd, name, cleanup_deadline):
                st.quarantine.add(name)
                if st.quarantine.over_cap():
                    st.degraded = "quarantine full"


class RunnerState:
    """What main() builds after the sweep and run_one / capabilities() read
    through _STATE: cfg, slots, root_fd, quarantine, degraded, plan_cache.

    Construction counts quarantine/ and spawns nothing; plan_all(deadline)
    plans every configured interpreter (startup). IMPL:065: a workdir the
    startup sweep could neither remove nor rename into quarantine/ is still
    at the top of the work root (no run has happened yet); it is handed to
    this Quarantine here, so it counts and is retried (fail closed toward
    degraded).
    """

    def __init__(self, cfg: SandboxConfig, root_fd: int, lock_fd: int,
                 quarantine_fd: int) -> None:
        self.cfg = cfg
        self.root_fd = root_fd
        self.lock_fd = lock_fd
        self.quarantine_fd = quarantine_fd
        self.slots = RunSlots(cfg.limits.max_concurrent)
        self.quarantine = Quarantine(root_fd, quarantine_fd)
        self.engine = _engine_path()
        try:
            leftovers = sorted(n for n in os.listdir(root_fd) if _WORKDIR_RE.match(n))
        except OSError as exc:
            log.warning("runner state: cannot list the work root (%s)", _errno_name(exc))
            leftovers = []
        for name in leftovers:
            self.quarantine.add(name)
        self.degraded: Optional[str] = "quarantine full" if self.quarantine.over_cap() else None
        self.plan_cache = PlanCache(self)

    def plan_all(self, deadline: float) -> None:
        self.plan_cache.plan_all(deadline)


def _interpreter_report(name: str, spec: InterpreterSpec, ps: PlanState) -> dict:
    plan = ps.plan if ps.state == "ready" else None
    return {
        "path": spec.path,
        "site": spec.site,
        "realpath": plan.realpath if plan else None,
        "version": plan.version if plan else None,
        "state": ps.state,
        "reason": ps.reason,
        "asLimitBytes": plan.as_limit if plan else None,
        "effectiveBudgetBytes": (plan.as_limit - plan.vsz_baseline) if plan else None,
        "reads": list(plan.reads) if plan else [],
        "execs": list(plan.execs) if plan else [],
    }


def capabilities() -> dict:
    """The capabilities document (plan Step 12). It needs no run slot, so it
    answers while every slot is taken."""
    state = _STATE
    if state is None:
        return _tool_refusal("sandbox_unavailable", "the runner has not started")
    cfg = state.cfg
    lim = cfg.limits
    key = _platform_key()
    return {
        "interpreters": {name: _interpreter_report(name, spec, state.plan_cache.peek(name))
                         for name, spec in sorted(cfg.interpreters.items())},
        "limits": {
            "timeoutMs": lim.timeout_ms,
            "maxTimeoutMs": lim.max_timeout_ms,
            "asBudgetBytes": lim.as_budget,
            "asMode": lim.as_mode,
            "nprocHeadroom": lim.nproc_headroom,
            "fsizeBytes": lim.fsize,
            "nofile": lim.nofile,
            "stdoutBytes": lim.stdout_bytes,
            "stderrBytes": lim.stderr_bytes,
            "maxFiles": lim.max_files,
            "maxFilesBytes": lim.max_files_bytes,
            "maxOutFiles": lim.max_out_files,
            "maxOutBytes": lim.max_out_bytes,
            "maxStdinBytes": lim.max_stdin_bytes,
            "maxArgs": lim.max_args,
            "maxArgChars": lim.max_arg_chars,
            "minFreeBytes": lim.min_free_bytes,
        },
        "backend": {
            "platform": sys.platform,
            "engine": state.engine,
            "seccomp": _seccomp_on(cfg.seccomp),
            "liveVerified": LIVE_VERIFIED.get(key, False) if key else False,
            "sbx": cfg.sbx,
        },
        "concurrency": {"max": state.slots.max, "inFlight": state.slots.in_flight()},
        "quarantine": {"count": state.quarantine.count(), "max": _QUARANTINE_MAX},
        "network": ({"enabled": False, "allow": [], "ports": []} if cfg.network is None
                    else {"enabled": True, "allow": list(cfg.network.allow),
                          "ports": list(cfg.network.ports),
                          "internal": list(cfg.network.internal)}),
        "degraded": state.degraded,
    }


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

SANDBOX_CALL_TOOL = {
    "name": "sandbox_call",
    "description": (
        "Run a short script in a locked-down sandbox: one configured "
        "interpreter (python3, bash or node) on files you pass inline, with "
        "reads restricted to the interpreter's own closure, no network unless "
        "the operator configured a host allowlist (then HTTPS to those hosts "
        "only, through the proxy in HTTPS_PROXY; see capabilities.network), "
        "one writable work directory that is removed afterwards, and hard "
        "limits on time, memory, processes, file size and output.\n\n"
        "A failing script is a normal result: a non-zero exitCode, a signal "
        "or a timeout comes back with isError false, together with the "
        "captured stdout and stderr. isError is set only when the runner "
        "itself refuses, and the result then carries error.code and "
        "error.message, where error.code is one of: invalid_params (a "
        "malformed call, an unknown function or parameter -- with a did-you-"
        "mean pointer), busy (every run slot is taken; retry after "
        "error.retryAfterMs), sandbox_unavailable (the interpreter's sandbox "
        "plan could not be built or refreshed), internal (a runner bug; the "
        "message is the exception type only).\n\n"
        "Functions:\n"
        "  run_script    params: files [{path, content, executable?}], entry, "
        "interpreter, args?, stdin?, timeoutMs?\n"
        "  capabilities  no params -- the configured interpreters and their "
        "state, the limits, the backend, the concurrency, the quarantine and "
        "the network allowlist.\n"
        "Called without function: a short status and the function list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "function": {
                "type": "string",
                "enum": ["run_script", "capabilities"],
                "description": "run_script or capabilities (see description).",
            },
            "params": {"type": "object", "description": "Function parameters."},
        },
    },
}


# ---------------------------------------------------------------------------
# Dispatch (handle_sandbox_call)
# ---------------------------------------------------------------------------

# The config main() loaded, read by the status reply. None in-process (the
# test rows load the module without starting a server); nothing but main()
# assigns it, and only before the server starts.
_ACTIVE_CONFIG: Optional[SandboxConfig] = None


def _tool_refusal(code: str, message: str, **extra: Any) -> dict:
    """A runner refusal: the TOP-LEVEL "error" key is what sets isError
    (ADR 0010). `code` is one of busy, sandbox_unavailable, invalid_params,
    internal; `extra` carries code-specific members (busy: retryAfterMs)."""
    return {"error": {"code": code, "message": message, **extra}}


def _status_text() -> str:
    """The unflagged reply to a call without `function` (R4:M1): the function
    list and a short status -- interpreter NAMES and plan state, degraded, the
    in-flight count. Never a path, never the read set."""
    lines = ["## mcp-sandbox", "",
             "Functions: " + ", ".join(sorted(HANDLERS)), ""]
    state = _STATE
    cfg = state.cfg if state is not None else _ACTIVE_CONFIG
    if cfg is None:
        lines.append("Interpreters: (no config loaded)")
    elif state is None:
        states = ", ".join("%s: not started" % name for name in sorted(cfg.interpreters))
        lines.append("Interpreters: " + (states or "(none configured)"))
    else:
        states = ", ".join("%s: %s" % (name, state.plan_cache.peek(name).state)
                           for name in sorted(cfg.interpreters))
        lines.append("Interpreters: " + (states or "(none configured)"))
    degraded = state.degraded if state is not None else None
    lines.append("Degraded: " + ("yes (%s)" % degraded if degraded else "no"))
    lines.append("In flight: %d" % (state.slots.in_flight() if state is not None else 0))
    lines.append("")
    lines.append("Call capabilities for details.")
    return "\n".join(lines)


def h_run_script(params: dict) -> dict:
    """run_script: validate (invalid_params / sandbox_unavailable), then
    run_one (slot, plan, workdir, sbx, cleanup)."""
    state = _STATE
    if state is None:
        return _tool_refusal("sandbox_unavailable", "the runner has not started")
    req = validate_run_params(params, state.cfg, state.plan_cache.admitted())
    try:
        return run_one(req)
    except RequestCancelled:
        # The request was cancelled; this reply is discarded by _serve.
        return _tool_refusal("sandbox_unavailable", "the request was cancelled")


def h_capabilities(params: dict) -> dict:
    """capabilities: interpreters, limits, backend, concurrency, quarantine."""
    return capabilities()


# canonical -> (handler, accepted params, one-line description)
HANDLERS: Dict[str, Tuple[Any, frozenset, str]] = {
    "run_script": (h_run_script, _RUN_PARAMS,
                   "Run a script on inline files under one configured interpreter"),
    "capabilities": (h_capabilities, frozenset(),
                     "The interpreters and their state, the limits, the backend, "
                     "the concurrency and the quarantine"),
}


def handle_sandbox_call(arguments: dict) -> dict:
    """The sandbox_call dispatcher (R3:M2, R4:M1). Every refusal is a
    _tool_refusal; a failing script is a normal result."""
    function = arguments.get("function")
    if function is not None and not isinstance(function, str):
        return _tool_refusal("invalid_params", "function must be a string")
    function = (function or "").strip()
    # A JSON-encoded object string is accepted (fleet behaviour) and then held
    # to the same member rules as an object.
    raw_params = arguments.get("params") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return _tool_refusal("invalid_params", str(exc))

    if not function:
        return {"__raw_text__": _status_text()}

    entry = HANDLERS.get(function)
    if entry is None:
        return _tool_refusal("invalid_params", (
            f"Unknown function '{function}'."
            + _did_you_mean(function, HANDLERS)
            + " Available: " + ", ".join(sorted(HANDLERS)) + "."
        ))
    handler, accepted = entry[0], entry[1]
    refusal = _unknown_params_refusal(function, params, accepted)
    if refusal:
        return _tool_refusal("invalid_params", refusal)
    try:
        return handler(params)
    except RunRefusal as exc:
        # A narrow refusal raised below the handler (validate_run_params):
        # its own code and message, never layer D's internal.
        return _tool_refusal(exc.code, str(exc))
    except Exception as exc:  # layer D: a handler bug must not hang the client
        # The type only -- the message may carry the caller's payload.
        log.exception("sandbox_call handler failed")
        return _tool_refusal("internal", type(exc).__name__)


# How many tool calls the generic fleet runs at once. This server sizes its
# worker pool from the config instead (limits.max_concurrent + 2, see run()),
# and the config's max_concurrent ceiling is this same number.
# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_concurrency.py :: MAX_INFLIGHT_REQUESTS
MAX_INFLIGHT_REQUESTS = 8
# END GENERATED: 0ffae9f02744


class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, config: SandboxConfig):
        self.config = config

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.info("MCP server starting (%s)", sys.platform)
        # TWO executors and one task per request (ADR 0008, MCP_SKELETON §5a).
        # The reader owns a thread of its own, so runs holding every worker
        # can never stop the server from READING stdin.
        #
        # The worker pool is limits.max_concurrent + 2: a run blocks its
        # worker for up to the KD-17 run bound, so with every run slot taken
        # two workers stay free for the calls that must answer WHILE runs are
        # in flight -- capabilities, the status reply, and the refusals
        # (busy among them) a run_script beyond the slots receives.
        #
        # Concurrent dispatch is safe because the only mutable state a
        # handler reaches is the run-slot / plan bookkeeping, and every piece
        # of it is taken under its own lock; the config is built once in
        # main() and only read afterwards.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sandbox-stdin")
        workers = ThreadPoolExecutor(max_workers=self.config.limits.max_concurrent + 2,
                                     thread_name_prefix="sandbox-call")
        inflight: set = set()
        by_id: dict = {}  # R-0006: request id -> its in-flight task
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:
                    # A closed or detached stdin raises here; it is a shutdown.
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = _strict_loads(line)
                except json.JSONDecodeError as exc:
                    # Answered, never dropped: a bare `continue` leaves the
                    # caller's id unanswered until it times out.
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue

                # F12/CWE-532 + ADR 0011: protocol STRUCTURE only, never a
                # payload value -- a run's files, stdin and output stay out of
                # the log. CWE-117: every field is the peer's, so _log_value.
                # Defensive: params/arguments may be a non-dict here.
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
                # before anything is dispatched -- never queued behind the
                # request it names. A notification: nothing is written back.
                if (msg.get("method") == "notifications/cancelled"
                        and msg.get("id") is None):
                    self._cancel_request(msg.get("params"), by_id)
                    continue
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                self._track_request(msg, task, by_id)
        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)
            log.info("MCP server shutting down")

    # R-0006: notifications/cancelled. Hand-written per server on purpose
    # (ADR 0012), held to one shape by tests/test_cancel.py.
    @staticmethod
    def _request_key(value):
        """`value` as an in-flight registry key, or None if it cannot be one.

        A str, or an int that is not a bool: True == 1 and they hash alike,
        so accepting a bool would let `requestId: true` cancel request 1.
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
            # Only while the slot is still THIS task: a reused id owns it now.
            if by_id.get(key) is done:
                del by_id[key]

        task.add_done_callback(_forget)

    def _cancel_request(self, params, by_id: dict) -> None:
        """notifications/cancelled, on the loop thread. Never replies.

        An unknown, finished or malformed requestId is ignored silently.
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

    async def _serve(self, loop, workers: ThreadPoolExecutor, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        # This request's children (every sbx spawn, every /bin/ps), published
        # into the context the worker thread runs in. The task already owns a
        # private copy of the context, so the set() is invisible to every
        # other request, and copy_context().run carries it across -- a thread
        # starts with a fresh context, so without it _spawn_sbx would see None.
        reclaim = _Reclaim()
        _REQUEST_RECLAIM.set(reclaim)
        ctx = contextvars.copy_context()
        try:
            response = await loop.run_in_executor(
                workers, ctx.run, self._handle_message, msg)
        except asyncio.CancelledError:
            # notifications/cancelled (or shutdown). The worker thread is still
            # blocked reading sbx; SIGTERM to sbx's group (SIGKILL after
            # _KILL_GRACE_S) is what lets it return, and whatever it returns
            # is discarded. Re-raised, so no reply is written.
            reclaim.cancel(loop)
            raise
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            # Exception, NOT BaseException: run()'s `finally` cancels every
            # inflight task on shutdown, and a swallowed CancelledError would
            # write a bogus -32603 to a stdout that is already gone.
            log.exception("Unhandled exception while handling message")
            response = self._error(
                msg.get("id"), -32603,
                f"Internal error: {type(exc).__name__}",
            )
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """Serialize and emit one JSON-RPC message.

        Called only from the event-loop thread (`_serve` resumes there after
        its await), so two replies cannot interleave mid-line.
        """
        try:
            out = _strict_dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = _strict_dumps(self._error(response.get("id"), -32603,
                                            f"Response not serialisable: {type(exc).__name__}"))
        # F12/CWE-532: structure only (id + outcome), never the body -- a
        # reply carries script output (wire_log FULL).
        log.debug(
            "→ id=%s %s", _log_value(response.get("id")),
            "error" if "error" in response else "ok",
        )
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            log.warning("stdout write failed: %s", exc)

    def _handle_message(self, msg: dict) -> Optional[dict]:
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params")

        if msg_id is None:
            log.debug("Notification: %s", _log_value(method))
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-sandbox", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": [SANDBOX_CALL_TOOL]})
        if method == "tools/call":
            # The JSON-RPC params are an object or absent; anything else is
            # the caller's protocol error (-32602), answered before any
            # handler runs -- never an AttributeError reported as -32603
            # (mcp-search F11; Contract item 1, smoke forced_exc_code).
            if params is None:
                params = {}
            elif not isinstance(params, dict):
                return self._error(
                    msg_id, -32602,
                    f"Invalid params: expected an object, got {type(params).__name__}")
            return self._handle_tool_call(msg_id, params)

        return self._error(msg_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name != "sandbox_call":
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        if isinstance(arguments, str):
            try:
                arguments = _strict_loads(arguments)
            except json.JSONDecodeError:
                pass
        if not isinstance(arguments, dict):
            return self._result(msg_id, {
                "content": [{"type": "text", "text":
                    f"'arguments' must be an object; got {type(arguments).__name__}."}],
                "isError": True,
            })
        try:
            result = handle_sandbox_call(arguments)
        except Exception as exc:
            # Layer W: only a failure handle_sandbox_call's own layer D could
            # not shape reaches here. The type only -- never the message,
            # which may carry the caller's payload.
            log.exception("Unhandled exception in handle_sandbox_call")
            result = _tool_refusal("internal", type(exc).__name__)
        # ADR 0010: the TOP-LEVEL "error" key is the one predicate.
        is_error = "error" in result
        if "__raw_text__" in result:
            return self._result(msg_id, {
                "content": [{"type": "text", "text": result["__raw_text__"]}],
                "isError": is_error,
            })
        # A structured result (a run, capabilities, a refusal) travels as its
        # JSON text AND as structuredContent, so error.code / error.message
        # are machine-read either way.
        return self._result(msg_id, {
            "content": [{"type": "text", "text": _strict_dumps(result)}],
            "structuredContent": result,
            "isError": is_error,
        })

    # Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
    # BEGIN GENERATED: _mcp_json.py :: _result, _error
    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    # END GENERATED: 0a5c31c9ccd8


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mcp-sandbox — run a caller's script under sbx's restricted mode")
    parser.add_argument("--config", required=True,
                        help="The runner's JSON config file (owner-only writable)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    args = parser.parse_args()

    _configure_logging(args.debug, args.log_file)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # The message names the offending KEY, never its value (P8).
        sys.stderr.write(f"mcp-sandbox: config refused: {exc}\n")
        sys.exit(2)

    # Startup (KD-17 "Startup bound", R3:M4): fail closed before answering
    # initialize -- the engine, the work root and its lock, the sweep under
    # its own 20 s, then every interpreter plan under ONE shared 75 s.
    started = time.monotonic()
    if _engine_path() is None:
        _refuse_start("no sandbox engine (sandbox-exec / bwrap) for platform %s; refusing to "
                      "run unsandboxed (fail-closed)" % sys.platform)
    root_fd, lock_fd, q_fd = prepare_work_root(config.work_root)
    sweep_work_root(root_fd, q_fd, time.monotonic() + _STARTUP_SWEEP_DEADLINE_S)
    swept = time.monotonic()
    state = RunnerState(config, root_fd, lock_fd, q_fd)
    try:
        state.plan_all(time.monotonic() + _STARTUP_PLANS_DEADLINE_S)
    except StartupRefusal as exc:
        _refuse_start(str(exc))
    log.info("startup: sweep %.3fs, plans %.3fs (%d interpreter(s))",
             swept - started, time.monotonic() - swept, len(config.interpreters))

    global _ACTIVE_CONFIG, _STATE
    _ACTIVE_CONFIG = config
    _STATE = state
    server = McpServer(config)
    asyncio.run(server.run())
    # stdin is closed, so the client is gone. Handler threads live in the
    # server's OWN executors rather than the loop's default one, so asyncio
    # does not join them on the way out -- but concurrent.futures registers an
    # atexit hook that would, and one run mid-script would hold this process
    # open for the rest of its bound after the client had already left. Every
    # reply is flushed as it is written and logging flushes per record, so
    # there is nothing left to drain.
    os._exit(0)


if __name__ == "__main__":
    main()
