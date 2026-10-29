#!/usr/bin/env python3
"""`Scripts/gpu-router.py` supervises one sd-server at a time and relays to it.

WHAT IS GATED
-------------
`Scripts/gpu-router.py` is a loopback HTTP front that owns a set of
stable-diffusion.cpp `sd-server` children, keeps at most ONE of them resident
(a single GPU), swaps on demand in strict FIFO order, and relays each image
request byte-exact to the resident child.  Everything it promises is
observable at its two edges: what the caller receives (the child's answer
byte-for-byte, or an OpenAI-shaped refusal from the closed code table), and
what the child receives (one request, recorded with its SHA-256 by the stub).
This suite drives the router against the adversarial stub child
`tests/files/gpu_router/tf_stub_sd.py` (flags and `tf_*` functions in its
docstring), as a subprocess for the CLI / exit-code / ready-file / signal
cases, and as a privately loaded module (in-process, constants and seams
patched) for every case whose observable is one of the router's own windows.

SANDBOX DISCIPLINE -- all fixtures (config files written mode 0600, stub state
directories, the router's stderr) live under
`.claude/tmp/test_gpu_router/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  Removed in
a `finally` unless --keep.  Every live router is closed SIGTERM -> SIGKILL in a
`finally`; every stub child is checked and reaped through its `.pids` file
(`reap_stubs`), and a stub that recorded no pid at all is a problem.

WAITS -- every wait is tick-bounded with a 10 s ceiling (`WAIT_CEILING_S`) on an
Event or on a predicate over a state file; no assertion is ever "sleep, then
look".

The case count is TYPED in run.py's SUITES table: this is a fixed case table,
so a count that moves is the alarm.  The run is authoritative.  Every case
fails RED while `Scripts/gpu-router.py` (or the name it gates) is missing.

Usage:
  python3 tests/test_gpu_router.py
  python3 tests/test_gpu_router.py --brief
  python3 tests/test_gpu_router.py --keep
Exit code 0 iff every non-informational case passes.

Groups:
  L        config         -- load_config / parse_config refusals and defaults, the CLI parser
  S        supervisor     -- spawn, readiness, the stop ladder, the process-group sweep
  D        decisions      -- the pure FIFO scheduler decisions (no sockets)
  Q-sched  scheduler      -- one in flight, queued and forced unloads, cancel, admission race
  Q-http   queue over HTTP-- client gone queued / running, disconnect loop, over-cap race,
                             racing unloads
  T        retries        -- retry counts, request_timeout, poison prompt, the restart budget
  P        proxy          -- auth, routing, framing refusals, byte-exact relay, startup lines
  R        routing        -- route_request and sniff_model (pure)
  U        unload         -- /v1/unload, ?force=1, the control routes
  G        front policy   -- the injected front policy pin (ADR 0029)
  A        static         -- AST over the router source
  E2E      end to end     -- llm-router (kind sdcpp, a bearer key) -> the live router -> two
                             stub models: round trip, swap, forced unload, wrong key

Fixture directory: tests/files/gpu_router/ (the stub child only).
"""

import ast
import base64
import contextlib
import dataclasses
import errno
import hashlib
import io
import json
import logging
import os
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "gpu_router"

SERVER = H.repo_path("Scripts", "gpu-router.py")
STUB = H.repo_path("tests", "files", "gpu_router", "tf_stub_sd.py")
STUB_DEFAULT_NAME = "tf_stub_sd"

GL = "L. config"
GS = "S. supervisor"
GD = "D. decisions"
GQS = "Q-sched. scheduler"
GQH = "Q-http. queue over HTTP"
GT = "T. retries and budget"
GP = "P. proxy"
GR = "R. routing"
GU = "U. unload"
GG = "G. front policy"
GA = "A. static"
GE = "E2E. end to end"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_gpu_router")
WRITES = []
LOG_PATHS = []               # every router stderr and --log-file path, swept for sentinels

LOOPBACK = "127.0.0.1"

# The router token: a per-run sentinel of 44 characters (the floor is 32, H4).
TF_TOKEN = "tf-sd-token-SENTINEL-" + secrets.token_hex(16)

# The stub's host/port flags as written into a model's `args`.  The stub accepts
# both families (--host/--port and --listen-ip/--listen-port); the router never
# depends on either, the operator writes {host}/{port} in `args`.
STUB_HOST_FLAG = "--host"
STUB_PORT_FLAG = "--port"

# Generous ceilings: a timing assertion is a FAIL only past these.
WAIT_CEILING_S = 10.0        # every Event / predicate wait
WAIT_TICK_S = 0.05           # the poll step of a predicate wait (never an assertion)
READY_TIMEOUT_S = 10.0       # --ready-file must appear within this
REFUSAL_TIMEOUT_S = 10.0     # a refused start (exit 2) must have exited within this
CLOSE_TIMEOUT_S = 15.0       # SIGTERM -> router exited
KILL_TIMEOUT_S = 5.0         # after SIGKILL
PID_DEAD_TIMEOUT_S = 10.0    # polling os.kill(pid, 0) until ProcessLookupError
JOIN_TIMEOUT_S = 5.0         # an in-process server thread after shutdown()


# ---------------------------------------------------------------------------
# Sandbox and config helpers
# ---------------------------------------------------------------------------

def free_port():
    """A port the kernel just handed out on loopback, released again (never connected)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def new_sandbox(fixture_root, label):
    """A fresh per-case directory under the run's fixture root."""
    path = tempfile.mkdtemp(prefix=label + "-", dir=fixture_root)
    WRITES.append(path)
    return path


def write_file(path, text, mode=0o600):
    """Write *text* to *path* with *mode* (also on an existing file); recorded in WRITES."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        data = text.encode("utf-8")
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)
    WRITES.append(path)
    return path


def write_config(sandbox, doc, mode=0o600, filename="gpu-router.json"):
    """Write *doc* as JSON into *sandbox* with exactly *mode*; return its path.

    *doc* is written as given (no defaults are filled in), so a case that needs
    a missing or malformed key states it.  *sandbox* must lie under the run's
    `.claude/tmp/test_gpu_router/run-<unique>/` root.
    """
    return write_file(os.path.join(sandbox, filename),
                      json.dumps(doc, indent=2, sort_keys=True) + "\n", mode)


def stub_model(state_dir, name, *flags, **keys):
    """A config model entry that runs the stub: `python -B STUB --host {host}
    --port {port} --state-dir <dir> --name <name> <flags...>`.

    Extra model keys (`start_timeout`, `retries`, `env`, ...) come from **keys.
    The stub's state files are `<state_dir>/<name>.*` (see its docstring).
    """
    entry = {
        "command": sys.executable,
        "args": ["-B", STUB, STUB_HOST_FLAG, "{host}", STUB_PORT_FLAG, "{port}",
                 "--state-dir", state_dir, "--name", name] + [str(f) for f in flags],
    }
    entry.update(keys)
    return entry


def base_config(models, token=TF_TOKEN, **top):
    """A config document: the token, *models* ({name: entry}) and any top-level key."""
    doc = {"token": token, "models": dict(models)}
    doc.update(top)
    return doc


# ---------------------------------------------------------------------------
# Stub state files and process checks
# ---------------------------------------------------------------------------

def stub_name(entry):
    """The stub's state-file prefix for a model entry: its --name, else the default."""
    args = (entry.get("args") or []) if isinstance(entry, dict) else []
    for i, arg in enumerate(args[:-1]):
        if arg == "--name":
            return args[i + 1]
    return STUB_DEFAULT_NAME


def _read_int_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [int(ln) for ln in (s.strip() for s in fh) if ln.isdigit()]
    except FileNotFoundError:
        return []


def read_pids(state_dir, name=STUB_DEFAULT_NAME):
    """Every pid in `<state_dir>/<name>.pids` (the stub and its grandchildren), in order."""
    return _read_int_lines(os.path.join(state_dir, name + ".pids"))


def read_starts(state_dir, name=STUB_DEFAULT_NAME):
    """One pid per process start, from `<state_dir>/<name>.starts`, in start order."""
    return _read_int_lines(os.path.join(state_dir, name + ".starts"))


def read_events(state_dir, name=STUB_DEFAULT_NAME):
    """Every JSON event the stub recorded in `<state_dir>/<name>.events.jsonl`."""
    path = os.path.join(state_dir, name + ".events.jsonl")
    events = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return events


def events_named(state_dir, event, name=STUB_DEFAULT_NAME):
    """The recorded events whose "event" is *event*."""
    return [ev for ev in read_events(state_dir, name) if ev.get("event") == event]


def read_body(state_dir, pid, seq, name=STUB_DEFAULT_NAME):
    """The exact bytes of the stub's POST body *seq* in process *pid*, or None."""
    path = "%s.%d.%d.body" % (os.path.join(state_dir, name), pid, seq)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def pid_alive(pid):
    """True while *pid* exists (a zombie awaiting its reaper counts as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_dead(pid, timeout=PID_DEAD_TIMEOUT_S):
    """Poll `os.kill(pid, 0)` until ProcessLookupError; True iff dead within *timeout*."""
    return wait_until(lambda: not pid_alive(pid), timeout)


def kill_pids(pids):
    """Best-effort SIGKILL of every pid still alive (a case's `finally`)."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def reap_stubs(state_dir, children):
    """Problems for every stub pid that outlived the case (each is then SIGKILLed).

    *children* is a list of model entries (see `stub_model`) or stub names.  A
    stub that recorded no pid at all is a problem too: the check must never
    pass vacuously because the stub was not spawned.  A case that expects a
    model never to start does not pass it here.
    """
    problems = []
    seen = set()
    for entry in children:
        name = entry if isinstance(entry, str) else stub_name(entry)
        if name in seen:
            continue
        seen.add(name)
        pids = read_pids(state_dir, name)
        if not pids:
            problems.append("stub %r: no pid recorded in %s.pids" % (name, name))
            continue
        alive = [pid for pid in pids if not pid_dead(pid)]
        if alive:
            kill_pids(alive)
            problems.append("stub %r: pid(s) %r outlived the case" % (name, alive))
    return problems


# ---------------------------------------------------------------------------
# Waits: Events and predicates, tick-bounded, always with a ceiling
# ---------------------------------------------------------------------------

def wait_event(event, timeout=WAIT_CEILING_S):
    """True iff *event* (a threading.Event) is set within *timeout*."""
    return event.wait(timeout)


def wait_until(predicate, timeout=WAIT_CEILING_S, tick=WAIT_TICK_S):
    """True once *predicate()* holds, checked every *tick* until *timeout* passes."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(tick)


def wait_events(state_dir, event, count=1, name=STUB_DEFAULT_NAME, timeout=WAIT_CEILING_S):
    """The first *count* stub events named *event*, or None if fewer arrived in time."""
    found = []

    def enough():
        found[:] = events_named(state_dir, event, name)
        return len(found) >= count

    return found[:count] if wait_until(enough, timeout) else None


# ---------------------------------------------------------------------------
# The live router (subprocess): CLI, exit code, ready file, signals
# ---------------------------------------------------------------------------

def router_argv(config_path, ready_path, *extra, server=None):
    return [sys.executable, "-B", server or SERVER, "--config", config_path, "--port", "0",
            "--ready-file", ready_path] + [str(a) for a in extra]


class RouterProc:
    """One live `gpu-router.py` with a ready file and its stderr in a file.

    Used only for CLI / exit-code / ready-file / signal cases, with real (small)
    config values: a subprocess cannot be monkeypatched.  *server* runs another
    script with the same command line (group E2E starts llm-router.py this way).
    """

    def __init__(self, config_path, extra_argv=(), label="router", server=None):
        sandbox = os.path.dirname(os.path.abspath(config_path))
        self.sandbox = sandbox
        self.token = TF_TOKEN
        self.ready_path = os.path.join(sandbox, label + ".ready.json")
        WRITES.append(self.ready_path)
        self.stderr_path = os.path.join(sandbox, label + ".stderr")
        WRITES.append(self.stderr_path)
        LOG_PATHS.append(self.stderr_path)
        extra = [str(a) for a in extra_argv]
        for i, arg in enumerate(extra[:-1]):
            if arg == "--log-file":
                LOG_PATHS.append(extra[i + 1])
                WRITES.append(extra[i + 1])
        self._err = open(self.stderr_path, "wb")
        self.port = None
        self.ready = None
        self._closed = False
        try:
            self.proc = subprocess.Popen(router_argv(config_path, self.ready_path, *extra, server=server),
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=self._err, cwd=sandbox, env=H.child_env())
        except Exception:
            self._err.close()
            raise

    def wait_ready(self, timeout=READY_TIMEOUT_S):
        """The ready file's {"port", "pid"} once it names a port; None if it never
        appeared or the router exited first."""
        def appeared():
            try:
                with open(self.ready_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return self.proc.poll() is not None
            if isinstance(data, dict) and isinstance(data.get("port"), int) and data["port"] > 0:
                self.ready = data
                self.port = data["port"]
                return True
            return self.proc.poll() is not None

        wait_until(appeared, timeout)
        return self.ready

    def wait_exit(self, timeout=REFUSAL_TIMEOUT_S):
        """The exit code within *timeout*, or None (still running)."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_text(self):
        try:
            self._err.flush()
        except (OSError, ValueError):
            pass
        try:
            with open(self.stderr_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def close(self, timeout=CLOSE_TIMEOUT_S):
        """SIGTERM, wait, then SIGKILL; idempotent; the exit code (None if unreaped)."""
        if self._closed:
            return self.proc.returncode
        self._closed = True
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.send_signal(signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self.proc.wait(timeout=KILL_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    pass
                return None
        finally:
            try:
                self._err.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def launch(config_path, extra_argv=(), label="router", timeout=READY_TIMEOUT_S, server=None):
    """(proc, ready): a started RouterProc and its ready-file dict ({"port", "pid"}),
    or None when the router never became ready (it may have exited: see
    `proc.wait_exit()` and `proc.stderr_text()`).  The caller closes *proc*."""
    proc = RouterProc(config_path, extra_argv, label, server=server)
    try:
        return proc, proc.wait_ready(timeout)
    except BaseException:
        proc.close()
        raise


# ---------------------------------------------------------------------------
# The in-process router: a privately loaded module, constants and seams patched
# ---------------------------------------------------------------------------
# A case whose observable is one of the router's own windows (backoff, grace,
# restart budget, body deadline) or that drives the scheduler directly loads
# the hyphenated file through importlib (`_harness.load_module_from_path`, no
# bytecode written) into its own module object, patches module-level names
# (clock, sleep, Popen, os.killpg, ports, constants) on THAT object, and runs
# the server on a daemon thread in the test process.  Everything patched is
# restored when the case ends.

_LOADS = [0]


def load_module(label="mod"):
    """(module, why): a private copy of Scripts/gpu-router.py, or (None, reason)
    when it is missing or fails to import -- a red case, never a crash."""
    _LOADS[0] += 1
    if not os.path.isfile(SERVER):
        return None, "Scripts/gpu-router.py does not exist"
    try:
        return H.load_module_from_path("ph_gpu_router_%s_%d" % (label, _LOADS[0]), SERVER), None
    except BaseException as exc:  # noqa: BLE001 -- a broken module is a finding, not a crash
        if isinstance(exc, KeyboardInterrupt):
            raise
        return None, "Scripts/gpu-router.py failed to import: %s" % type(exc).__name__


def missing_names(mod, names):
    """One problem naming every symbol of *names* the module does not define (red, never a crash)."""
    absent = [n for n in names if getattr(mod, n, None) is None]
    return ["the module defines no %s" % ", ".join(absent)] if absent else []


@contextlib.contextmanager
def patched(owner, pairs):
    """Set each (name, value) of *pairs* on *owner* for the block, restored after,
    in reverse order, even on failure.  A name the owner lacks is an
    AttributeError: a patch must never silently create a new seam."""
    saved = []
    try:
        for name, value in pairs:
            saved.append((name, getattr(owner, name)))
            setattr(owner, name, value)
        yield owner
    finally:
        while saved:
            name, value = saved.pop()
            setattr(owner, name, value)


def serve_in_thread(srv, name="tf-sd-serve"):
    """(srv, thread): *srv*.serve_forever on a daemon thread."""
    thread = threading.Thread(target=srv.serve_forever, name=name, daemon=True)
    thread.start()
    return srv, thread


def unserve(srv, thread):
    """Stop an in-process server: shutdown, close the socket, join the thread."""
    try:
        srv.shutdown()
    finally:
        srv.server_close()
        thread.join(JOIN_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Group L -- config: load_config / parse_config refusals and defaults, the CLI parser
# ---------------------------------------------------------------------------
# PURE parser/loader cases only, in-process against a privately loaded module (no
# router is started, no stub is spawned: `sys.executable` stands in for the
# command because it is absolute and executable).  The startup-log cases
# (cleartext WARNING, the single-user-host INFO line) are group P's: they need
# main() and the generated logging region (round-3 H1).
#
# The contract this group pins (task-009 / task-010 implement it):
#   ConfigError, ModelSpec, Settings (fields token, models, body_limit,
#   max_connections, response_limit; models maps name -> ModelSpec),
#   parse_config(raw, where="config") -> Settings over the decoded JSON object,
#   load_config(path) -> Settings (the fd read, then parse_config),
#   render_args(spec, host, port) -> List[str],
#   _SdArgumentParser and _sd_parse_args(argv) -> argparse.Namespace: every CLI
#   refusal (argparse's own, and --allow-remote without --insecure-cleartext)
#   is ONE stderr line and SystemExit(2); the namespace carries bind,
#   body_limit and max_connections.
# Every refusal is a ConfigError whose message is one line naming the rule or
# key, never a value (token, env value, an unknown key's value, a clashing
# port/host value).
#
# The port/host flag rule (orchestrator DECISION, resolving a contradiction in
# plan Step 2 between the deny-list and the `{port}`/`{host}` substitution; to
# be recorded as a deviation in ADR 0034): an `args` element NAMING a port/host
# flag -- equal to one of L_PORT_FLAGS, or starting with one of them followed
# by `=` -- is refused UNLESS its value is exactly `{port}` or `{host}`, where
# the value is the NEXT element for the bare form and the text after `=` for
# the `=` form.  So `--port`,`{port}` and `--listen-port={port}` are accepted;
# `--port`,`8080`, `--port=8080`, `-p`,`1234`, `--host`,`0.0.0.0` and a bare
# flag as the last element are refused.  The harness's own stub args
# (STUB_HOST_FLAG `{host}` STUB_PORT_FLAG `{port}`) must pass the loader.
#
# A placeholder is `{` + an identifier + `}`; only `{port}` and `{host}` are
# known.  Braces that are not an identifier placeholder (`{}`, a JSON object
# in an sd_cpp argument) are plain text and render unchanged.

GL_CASES = (
    "L1 mode 0644 refused",
    "L2 symlink refused",
    "L3 foreign owner refused",
    "L4 duplicate key refused",
    "L5 unknown key named, no value",
    "L6 token of 31 chars refused",
    "L7 token of 32 chars accepted",
    "L8 low-entropy token refused",
    "L9 token absent refused",
    "L10 request_timeout bounds",
    "L11 relative command refused",
    "L12 non-executable command",
    "L13 missing {port} refused",
    "L14 port/host flag + value",
    "L15 port/host flag =value",
    "L16 placeholder flag forms ok",
    "L17 unknown placeholder refused",
    "L18 retries bounds",
    "L19 start_timeout bounds",
    "L20 stop_grace bounds",
    "L21 env key/value types",
    "L22 no secret in repr/refusal",
    "L23 CLI refusal: exit 2, 1 line",
    "L24 render_args with braces",
    "L25 defaults 32 MiB / 8",
    "L26 --allow-remote alone refused",
    "L27 remote+cleartext, default ok",
    "L28 --insecure-cleartext alone ok",
    "L29 registered, amalgamate ok",
    "L30 NUL in a model entry refused",
    "L31 body/response_limit ranges",
    "L32 body_limit precedence (G4)",
    "L33 listener keys accepted",
    "L34 listener key types refused",
    "L35 listener key ranges refused",
    "L36 unknown key lists the keys",
    "L37 CLI beats config (six)",
    "L38 config used without a flag",
    "L39 config bind needs the acks",
)

L_NAMES_CONFIG = ("ConfigError", "ModelSpec", "Settings", "parse_config", "load_config",
                  "render_args")
L_NAMES_CLI = ("ConfigError", "_SdArgumentParser", "_sd_parse_args")
L_PORT_FLAGS = ("--port", "-p", "--listen-port", "--listen-ip", "--host")
L_CLASH_VALUES = {"--port": "8080", "-p": "1234", "--listen-port": "8081",
                  "--listen-ip": "0.0.0.0", "--host": "0.0.0.0"}
L_DEFAULT_BIND = "127.0.0.1"
L_DEFAULT_BODY_LIMIT = 32 * 1024 * 1024          # 33554432 (D-14)
L_DEFAULT_MAX_CONNECTIONS = 8                    # (D-14)
L_DEFAULT_RESPONSE_LIMIT = 64 * 1024 * 1024      # (D-6)
L_UNKNOWN_VALUE = "tf-unknown-VALUE-SENTINEL-" + secrets.token_hex(6)
L_ENV_VALUE = "tf-env-VALUE-SENTINEL-" + secrets.token_hex(6)
L_AMALGAMATE = H.repo_path("Scripts", "amalgamate.py")
L_AMALGAMATE_TIMEOUT_S = 180


def _l_doc(sandbox, **model_keys):
    """A VALID config document: one stub model "m1" (+ *model_keys* overrides)."""
    return base_config({"m1": stub_model(sandbox, "m1", **model_keys)})


def _l_raw(doc):
    """*doc* as parse_config receives it: the decoded JSON object."""
    return json.loads(json.dumps(doc))


def _l_attempt(mod, fn, *args):
    """("ok", value) | ("refused", message) | ("error", exception type name)."""
    try:
        return "ok", fn(*args)
    except mod.ConfigError as exc:
        return "refused", str(exc)
    except Exception as exc:  # noqa: BLE001 -- the wrong exception is a finding
        return "error", type(exc).__name__


def _l_refused(mod, label, fn, args, needles=(), absent=()):
    """Problems unless fn(*args) raises ConfigError with a one-line message that
    names every *needles* entry and echoes no *absent* value."""
    kind, val = _l_attempt(mod, fn, *args)
    if kind == "ok":
        return ["%s: accepted, a ConfigError was expected" % label]
    if kind == "error":
        return ["%s: raised %s, not ConfigError" % (label, val)]
    problems = []
    if not val.strip():
        problems.append("%s: the refusal message is empty" % label)
    if "\n" in val or "\r" in val:
        problems.append("%s: the refusal message is not one line" % label)
    for needle in needles:
        if needle not in val:
            problems.append("%s: the refusal does not name %r" % (label, needle))
    for value in absent:
        if value and value in val:
            problems.append("%s: the refusal echoes a value it must not" % label)
    return problems


def _l_accepted(mod, label, fn, args):
    """(problems, value): problems unless fn(*args) returns without a refusal."""
    kind, val = _l_attempt(mod, fn, *args)
    if kind == "ok":
        return [], val
    if kind == "refused":
        return ["%s: refused (%s), expected accepted" % (label, val[:160])], None
    return ["%s: raised %s, expected accepted" % (label, val)], None


def _l_parse(mod, doc):
    return mod.parse_config, (_l_raw(doc),)


def _l_cli(mod, argv):
    """(exit code or None, namespace or None, stderr text, error type or None) of
    mod._sd_parse_args(argv), stdout and stderr captured."""
    err = io.StringIO()
    code, ns, error = None, None, None
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        try:
            ns = mod._sd_parse_args(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        except Exception as exc:  # noqa: BLE001 -- the wrong exception is a finding
            error = type(exc).__name__
    return code, ns, err.getvalue(), error


def _l_cli_refused(mod, label, argv, absent=()):
    code, ns, err, error = _l_cli(mod, argv)
    if error is not None:
        return ["%s: raised %s instead of exiting 2" % (label, error)]
    if code is None:
        return ["%s: parsed, expected exit 2" % label]
    problems = []
    if code != 2:
        problems.append("%s: exit %r, expected 2" % (label, code))
    lines = [ln for ln in err.splitlines() if ln.strip()]
    if len(lines) != 1:
        problems.append("%s: %d stderr lines, expected exactly one" % (label, len(lines)))
    for value in absent:
        if value and value in err:
            problems.append("%s: stderr echoes a value it must not" % label)
    return problems


def _l_cli_parsed(mod, label, argv):
    """(problems, namespace)."""
    code, ns, err, error = _l_cli(mod, argv)
    if error is not None:
        return ["%s: raised %s, expected a parse" % (label, error)], None
    if code is not None:
        first = (err.strip().splitlines() or [""])[0]
        return ["%s: exit %r (%s), expected a parse" % (label, code, first[:160])], None
    if ns is None:
        return ["%s: _sd_parse_args returned None" % label], None
    return [], ns


def _l_config_file(sandbox, doc, mode=0o600, name="gpu-router.json"):
    return write_config(sandbox, doc, mode=mode, filename=name)


def l1(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox), mode=0o644)
    problems = _l_refused(mod, "mode 0644", mod.load_config, (path,), absent=(TF_TOKEN,))
    path = _l_config_file(sandbox, _l_doc(sandbox), mode=0o640, name="g.json")
    problems += _l_refused(mod, "mode 0640", mod.load_config, (path,), absent=(TF_TOKEN,))
    ok_path = _l_config_file(sandbox, _l_doc(sandbox), name="ok.json")
    problems += _l_accepted(mod, "mode 0600 control", mod.load_config, (ok_path,))[0]
    return problems, ["0644 and 0640 refused, 0600 accepted"]


def l2(mod, sandbox):
    target = _l_config_file(sandbox, _l_doc(sandbox))
    link = os.path.join(sandbox, "link.json")
    os.symlink(target, link)
    WRITES.append(link)
    problems = _l_refused(mod, "symlink", mod.load_config, (link,), absent=(TF_TOKEN,))
    return problems, ["a symlink to a valid 0600 config is refused (O_NOFOLLOW)"]


def l3(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox))
    if os.geteuid() == 0:
        os.chown(path, 65534, -1)
        problems = _l_refused(mod, "foreign owner", mod.load_config, (path,),
                              absent=(TF_TOKEN,))
        return problems, ["root: the file is chowned to uid 65534"]
    # Not root: the owner cannot be changed, so the effective uid is presented as
    # a different one for the call (os.geteuid patched, restored after).  Stronger
    # than an INFO row: the rule is still exercised.
    real = os.geteuid()
    with patched(os, [("geteuid", lambda: real + 1)]):
        problems = _l_refused(mod, "foreign owner", mod.load_config, (path,),
                              absent=(TF_TOKEN,))
    return problems, ["not root: os.geteuid() presented as uid+1 for the load"]


def l4(mod, sandbox):
    body = json.dumps(_l_doc(sandbox), indent=2)
    dup_top = body.replace('"token":', '"token": "%s",\n  "token":' % ("x" * 40), 1)
    path = write_file(os.path.join(sandbox, "dup-top.json"), dup_top)
    problems = _l_refused(mod, "duplicate top-level key", mod.load_config, (path,),
                          needles=("token",), absent=(TF_TOKEN, "x" * 40))
    dup_model = body.replace('"command":', '"retries": 1,\n      "retries": 2,\n      "command":', 1)
    path = write_file(os.path.join(sandbox, "dup-model.json"), dup_model)
    problems += _l_refused(mod, "duplicate model key", mod.load_config, (path,),
                           needles=("retries",), absent=(TF_TOKEN,))
    return problems, ["a duplicate key is refused at both levels and named"]


def l5(mod, sandbox):
    doc = _l_doc(sandbox)
    doc["tf_unknown_top"] = L_UNKNOWN_VALUE
    problems = _l_refused(mod, "unknown top-level key", *_l_parse(mod, doc),
                          needles=("tf_unknown_top",), absent=(L_UNKNOWN_VALUE, TF_TOKEN))
    doc = _l_doc(sandbox, tf_unknown_model=L_UNKNOWN_VALUE)
    problems += _l_refused(mod, "unknown model key", *_l_parse(mod, doc),
                           needles=("tf_unknown_model",), absent=(L_UNKNOWN_VALUE, TF_TOKEN))
    return problems, ["named by key, the value never echoed"]


def l6(mod, sandbox):
    token = TF_TOKEN[:31]
    doc = _l_doc(sandbox)
    doc["token"] = token
    problems = _l_refused(mod, "token of 31", *_l_parse(mod, doc),
                          needles=("token",), absent=(token,))
    return problems, ["31 characters is below the floor of 32 (H4)"]


def l7(mod, sandbox):
    token = TF_TOKEN[:32]
    doc = _l_doc(sandbox)
    doc["token"] = token
    problems, settings = _l_accepted(mod, "token of 32", *_l_parse(mod, doc))
    if settings is not None and getattr(settings, "token", None) not in (token, token.encode("ascii")):
        problems.append("Settings.token is not the configured token")
    return problems, ["exactly 32 characters is accepted (H4)"]


def l8(mod, sandbox):
    problems = []
    for label, token in (("32 x 'a'", "a" * 32), ("two distinct", "ab" * 20)):
        doc = _l_doc(sandbox)
        doc["token"] = token
        problems += _l_refused(mod, "low-entropy token (%s)" % label, *_l_parse(mod, doc),
                               needles=("token",), absent=(token,))
    return problems, ["long but with too few distinct characters is refused (llm-router V1)"]


def l9(mod, sandbox):
    doc = _l_doc(sandbox)
    del doc["token"]
    problems = _l_refused(mod, "token absent", *_l_parse(mod, doc), needles=("token",))
    doc = _l_doc(sandbox)
    doc["token"] = 12345678901234567890123456789012345
    problems += _l_refused(mod, "token not a string", *_l_parse(mod, doc), needles=("token",))
    return problems, ["the token is REQUIRED and a string"]


def _l_bounds(mod, sandbox, key, bad, good):
    problems = []
    for value in bad:
        problems += _l_refused(mod, "%s=%r" % (key, value), *_l_parse(mod, _l_doc(sandbox, **{key: value})),
                               needles=(key,), absent=(TF_TOKEN,))
    for value in good:
        found, settings = _l_accepted(mod, "%s=%r" % (key, value),
                                      *_l_parse(mod, _l_doc(sandbox, **{key: value})))
        problems += found
        if settings is not None:
            got = getattr(settings.models["m1"], key, None)
            if got != value:
                problems.append("%s=%r: the ModelSpec carries %r" % (key, value, got))
    return problems


def l10(mod, sandbox):
    return (_l_bounds(mod, sandbox, "request_timeout", (0, 1, -5, 7200.5, 10 ** 6, True, "600"),
                      (1.5, 600, 7200)),
            ["range (1, 7200]; bool and string refused (H7)"])


def l11(mod, sandbox):
    problems = []
    for cmd in ("python3", "./bin/sd-server", ""):
        problems += _l_refused(mod, "command %r" % cmd,
                               *_l_parse(mod, _l_doc(sandbox, command=cmd)), needles=("command",))
    return problems, ["a command that is not an absolute path is refused"]


def l12(mod, sandbox):
    plain = write_file(os.path.join(sandbox, "not-exec"), "#!/bin/sh\nexit 0\n", mode=0o644)
    problems = _l_refused(mod, "non-executable command",
                          *_l_parse(mod, _l_doc(sandbox, command=plain)), needles=("command",))
    missing = os.path.join(sandbox, "no-such-binary")
    problems += _l_refused(mod, "missing command",
                           *_l_parse(mod, _l_doc(sandbox, command=missing)), needles=("command",))
    problems += _l_refused(mod, "directory command",
                           *_l_parse(mod, _l_doc(sandbox, command=sandbox)), needles=("command",))
    return problems, ["absent, non-executable or not a regular file: refused"]


def l13(mod, sandbox):
    problems = []
    for label, args in (("no {port}", ["-B", STUB, STUB_HOST_FLAG, "{host}"]),
                        ("empty list", []),
                        ("not a list", "-B {port}"),
                        ("non-string element", ["{port}", 5])):
        problems += _l_refused(mod, "args %s" % label,
                               *_l_parse(mod, _l_doc(sandbox, args=args)), needles=("args",))
    return problems, ["args is a non-empty list of strings holding {port}"]


def _l_clash_args(flag, form):
    """Model args with one clashing *flag* in *form* ("pair", "eq" or "last")."""
    head = ["-B", STUB, "--state-dir", "/x", "--listen-port={port}" if flag != "--listen-port"
            else "--port={port}"]
    value = L_CLASH_VALUES[flag]
    if form == "pair":
        return head + [flag, value]
    if form == "eq":
        return head + ["%s=%s" % (flag, value)]
    return head + [flag]


def l14(mod, sandbox):
    problems = []
    for flag in L_PORT_FLAGS:
        for form in ("pair", "last"):
            args = _l_clash_args(flag, form)
            problems += _l_refused(mod, "%s %s" % (flag, form),
                                   *_l_parse(mod, _l_doc(sandbox, args=args)),
                                   needles=("args",), absent=(L_CLASH_VALUES[flag], TF_TOKEN))
    return problems, ["each family with a fixed value (or none, as the last element) is refused, "
                      "named by args index, value not echoed"]


def l15(mod, sandbox):
    problems = []
    for flag in L_PORT_FLAGS:
        args = _l_clash_args(flag, "eq")
        problems += _l_refused(mod, "%s=value" % flag,
                               *_l_parse(mod, _l_doc(sandbox, args=args)),
                               needles=("args",), absent=(L_CLASH_VALUES[flag], TF_TOKEN))
    return problems, ["each family's `=value` form with a fixed value is refused"]


def l16(mod, sandbox):
    problems = []
    forms = (
        ("harness stub args", None),
        ("--port {port}", ["--port", "{port}"]),
        ("-p {port}", ["-p", "{port}", "--host", "{host}"]),
        ("--listen-port={port}", ["--listen-port={port}", "--listen-ip={host}"]),
        ("--listen-ip {host}", ["--listen-ip", "{host}", "--listen-port", "{port}"]),
        ("--host={host}", ["--host={host}", "--port={port}"]),
    )
    for label, args in forms:
        doc = _l_doc(sandbox) if args is None else _l_doc(sandbox, args=args)
        found, settings = _l_accepted(mod, label, *_l_parse(mod, doc))
        problems += found
        if settings is not None and args is not None:
            got = tuple(settings.models["m1"].args)
            if got != tuple(args):
                problems.append("%s: ModelSpec.args is %r" % (label, got))
    return problems, ["a port/host flag whose value is exactly {port} or {host} is accepted"]


def l17(mod, sandbox):
    problems = []
    for placeholder in ("{model}", "{PORT}", "{port_}"):
        args = ["--port", "{port}", "--name=%s" % placeholder]
        problems += _l_refused(mod, "placeholder %s" % placeholder,
                               *_l_parse(mod, _l_doc(sandbox, args=args)), needles=("args",))
    return problems, ["only {port} and {host} are placeholders"]


def l18(mod, sandbox):
    return (_l_bounds(mod, sandbox, "retries", (-1, 4, 1.5, True, "1"), (0, 1, 3)),
            ["range 0..3, integers only (additional sends)"])


def l19(mod, sandbox):
    return (_l_bounds(mod, sandbox, "start_timeout", (0, 1, 3600.5, False, "120"), (1.5, 120, 3600)),
            ["range (1, 3600]"])


def l20(mod, sandbox):
    return (_l_bounds(mod, sandbox, "stop_grace", (0, -1, 60.5, True, "5"), (0.1, 5, 60)),
            ["range (0, 60]"])


def l21(mod, sandbox):
    problems = []
    bad = (("lower-case key", {"sd_var": L_ENV_VALUE}),
           ("leading digit", {"1SD": L_ENV_VALUE}),
           ("dash in key", {"SD-VAR": L_ENV_VALUE}),
           ("int value", {"SD_VAR": 5}),
           ("null value", {"SD_VAR": None}),
           ("not an object", ["SD_VAR=%s" % L_ENV_VALUE]))
    for label, env in bad:
        problems += _l_refused(mod, "env %s" % label, *_l_parse(mod, _l_doc(sandbox, env=env)),
                               needles=("env",), absent=(L_ENV_VALUE, TF_TOKEN))
    good = {"SD_VAR": L_ENV_VALUE, "_UNDER": "", "LD_LIBRARY_PATH": "/opt/x"}
    found, settings = _l_accepted(mod, "env valid", *_l_parse(mod, _l_doc(sandbox, env=good)))
    problems += found
    if settings is not None:
        got = dict(settings.models["m1"].env)
        if got != good:
            problems.append("env valid: ModelSpec.env does not carry the configured pairs")
    return problems, ["keys [A-Z_][A-Z0-9_]*, values str; trusted names are not denied"]


def l22(mod, sandbox):
    doc = _l_doc(sandbox, env={"SD_SECRET": L_ENV_VALUE})
    problems, settings = _l_accepted(mod, "valid config", *_l_parse(mod, doc))
    if settings is not None:
        spec = settings.models["m1"]
        for label, text in (("repr(Settings)", repr(settings)), ("str(Settings)", str(settings)),
                            ("repr(ModelSpec)", repr(spec)), ("str(ModelSpec)", str(spec))):
            if TF_TOKEN in text:
                problems.append("%s carries the token" % label)
            if L_ENV_VALUE in text:
                problems.append("%s carries an env value" % label)
    # A refusal raised AFTER the token and env were read names neither.
    doc = _l_doc(sandbox, env={"SD_SECRET": L_ENV_VALUE}, retries=9)
    problems += _l_refused(mod, "late refusal", *_l_parse(mod, doc), needles=("retries",),
                           absent=(TF_TOKEN, L_ENV_VALUE))
    path = _l_config_file(sandbox, doc, mode=0o644, name="leak.json")
    problems += _l_refused(mod, "file refusal", mod.load_config, (path,),
                           absent=(TF_TOKEN, L_ENV_VALUE))
    return problems, ["token and env values never in repr/str or a refusal"]


def l23(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox))
    problems = _l_cli_refused(mod, "--config-json", ["--config", path, "--config-json", L_UNKNOWN_VALUE],
                              absent=(L_UNKNOWN_VALUE,))
    problems += _l_cli_refused(mod, "--body-limit not an integer",
                               ["--config", path, "--body-limit", L_UNKNOWN_VALUE],
                               absent=(L_UNKNOWN_VALUE,))
    problems += _l_cli_refused(mod, "--config missing", [])
    return problems, ["argparse refusals are one stderr line and exit 2, no value echoed; "
                      "there is no --config-json"]


def l24(mod, sandbox):
    args = ["--port", "{port}", "--listen-ip={host}", '--extra={"steps": 4, "cfg": {"a": [1]}}',
            "{}", "{{port}}", "x{host}y{port}z"]
    problems, settings = _l_accepted(mod, "braces in args", *_l_parse(mod, _l_doc(sandbox, args=args)))
    if settings is None:
        return problems, []
    kind, rendered = _l_attempt(mod, mod.render_args, settings.models["m1"], "127.0.0.1", 41234)
    if kind != "ok":
        return problems + ["render_args: %s %s" % (kind, str(rendered)[:160])], []
    want = ["--port", "41234", "--listen-ip=127.0.0.1", '--extra={"steps": 4, "cfg": {"a": [1]}}',
            "{}", "{41234}", "x127.0.0.1y41234z"]
    if list(rendered) != want:
        problems.append("render_args gave %r, expected %r" % (list(rendered), want))
    if list(settings.models["m1"].args) != args:
        problems.append("render_args must not mutate ModelSpec.args")
    return problems, ["explicit .replace of {port}/{host}; other braces untouched"]


def l25(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox))
    problems, ns = _l_cli_parsed(mod, "defaults", ["--config", path])
    if ns is not None:
        # An absent flag is None, so the merge can tell it from an explicit one.
        for attr in ("bind", "port", "body_limit", "max_connections", "allow_remote",
                     "insecure_cleartext"):
            if not hasattr(ns, attr) or getattr(ns, attr) is not None:
                problems.append("parser default %s is %r, expected None (absent)"
                                % (attr, getattr(ns, attr, "<missing>")))
        if callable(getattr(mod, "_sd_settings", None)):
            cfg_path = _l_config_file(sandbox, _l_doc(sandbox), name="l25-eff.json")
            found, cfg = _l_accepted(mod, "load_config", mod.load_config, (cfg_path,))
            problems += found
            if cfg is not None:
                try:
                    eff = mod._sd_settings(ns, cfg)
                except Exception as exc:  # noqa: BLE001
                    problems.append("_sd_settings raised %s on the defaults" % type(exc).__name__)
                else:
                    for attr, want in (("bind", L_DEFAULT_BIND), ("port", 0),
                                       ("body_limit", L_DEFAULT_BODY_LIMIT),
                                       ("max_connections", L_DEFAULT_MAX_CONNECTIONS),
                                       ("allow_remote", False), ("insecure_cleartext", False)):
                        got = getattr(eff, attr, "<missing>")
                        if got != want or type(got) is not type(want):
                            problems.append("effective default %s is %r, expected %r" % (attr, got, want))
    found, settings = _l_accepted(mod, "Settings defaults", *_l_parse(mod, _l_doc(sandbox)))
    problems += found
    if settings is not None:
        for attr, want in (("body_limit", L_DEFAULT_BODY_LIMIT),
                           ("max_connections", L_DEFAULT_MAX_CONNECTIONS),
                           ("response_limit", L_DEFAULT_RESPONSE_LIMIT)):
            got = getattr(settings, attr, None)
            if got != want:
                problems.append("Settings default %s is %r, expected %r" % (attr, got, want))
        spec = settings.models["m1"]
        for attr, want in (("start_timeout", 120.0), ("stop_grace", 5.0), ("retries", 1),
                           ("request_timeout", 600.0), ("cwd", None)):
            got = getattr(spec, attr, "<absent>")
            if got != want:
                problems.append("ModelSpec default %s is %r, expected %r" % (attr, got, want))
    return problems, ["--body-limit 32 MiB, --max-connections 8, response_limit 64 MiB (D-14, D-6)"]


def _l_merge(mod, sandbox, top, argv, name):
    """("ok", Settings) | ("refused", message): a config with the *top* keys merged with
    the CLI *argv* through _sd_parse_args + load_config + _sd_settings (the real order)."""
    doc = _l_doc(sandbox)
    doc.update(top)
    path = _l_config_file(sandbox, doc, name=name)
    found, ns = _l_cli_parsed(mod, name, ["--config", path] + list(argv))
    if found:
        return "refused", "; ".join(found)
    kind, cfg = _l_attempt(mod, mod.load_config, path)
    if kind != "ok":
        return kind, cfg
    return _l_attempt(mod, mod._sd_settings, ns, cfg)


def l26(mod, sandbox):
    """D-13: --allow-remote needs --insecure-cleartext.  Since the listener options may
    come from the config the rule runs on the MERGED result (the final refusal is a
    ConfigError that main turns into exit 2, one line), not at argparse time."""
    problems = []
    for label, top, argv in (
            ("--allow-remote alone", {}, ["--bind", "10.0.0.1", "--allow-remote"]),
            ("--allow-remote alone, default bind", {}, ["--allow-remote"]),
            ("config allow_remote alone", {"allow_remote": True, "bind": "10.0.0.1"}, []),
            ("flag allow_remote, config insecure_cleartext false",
             {"insecure_cleartext": False}, ["--bind", "10.0.0.1", "--allow-remote"])):
        kind, val = _l_merge(mod, sandbox, top, argv, "l26-%d.json" % len(label))
        if kind != "refused":
            problems.append("%s: %s, expected a refusal" % (label, kind))
        elif "--insecure-cleartext" not in val or "\n" in val:
            problems.append("%s: the refusal does not name --insecure-cleartext on one line" % label)
    return problems, ["--allow-remote needs --insecure-cleartext (D-13), checked on the merged "
                      "CLI + config result: ConfigError, one line"]


def l27(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox))
    problems, ns = _l_cli_parsed(mod, "--allow-remote --insecure-cleartext",
                                 ["--config", path, "--bind", "10.0.0.1", "--allow-remote",
                                  "--insecure-cleartext"])
    if ns is not None and not (getattr(ns, "allow_remote", None) and getattr(ns, "insecure_cleartext", None)):
        problems.append("both flags parsed but the namespace does not carry them")
    found, ns = _l_cli_parsed(mod, "default bind", ["--config", path])
    problems += found
    return problems, ["both flags together parse; the default loopback bind parses"]


def l28(mod, sandbox):
    path = _l_config_file(sandbox, _l_doc(sandbox))
    problems, ns = _l_cli_parsed(mod, "--insecure-cleartext alone",
                                 ["--config", path, "--insecure-cleartext"])
    if ns is not None and not getattr(ns, "insecure_cleartext", None):
        problems.append("--insecure-cleartext parsed but the namespace does not carry it")
    return problems, ["--insecure-cleartext without --allow-remote is accepted (a no-op)"]


def _l_declared_hosts():
    """The DECLARED_HOSTS tuple literal of Scripts/amalgamate.py, read by AST."""
    with open(L_AMALGAMATE, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DECLARED_HOSTS"
                                                for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    return None


def l29(mod, sandbox):
    problems = []
    hosts = _l_declared_hosts()
    if hosts is None:
        problems.append("Scripts/amalgamate.py has no DECLARED_HOSTS literal")
    elif "gpu-router.py" not in hosts:
        problems.append("gpu-router.py is not in amalgamate.py's DECLARED_HOSTS (H3)")
    elif hosts[-1] != "gpu-router.py":
        problems.append("gpu-router.py is not the LAST entry of DECLARED_HOSTS")
    rc, _out, err = H.run_process([sys.executable, "-B", L_AMALGAMATE, "--check"],
                                  timeout=L_AMALGAMATE_TIMEOUT_S, cwd=H.REPO_ROOT)
    if rc != 0:
        tail = (err.strip().splitlines() or [""])[-1]
        problems.append("amalgamate.py --check exit %d (%s)" % (rc, tail[:200]))
    return problems, ["registered as the last declared host; amalgamate --check exit 0"]


def l30(mod, sandbox):
    """M24: a NUL reaches Popen as a ValueError, so the loader refuses it per key."""
    problems = []
    nul_dir = sandbox + "\0x"
    for label, keys, needle in (
            ("command", {"command": sys.executable + "\0"}, "command"),
            ("args element", {"args": ["-B", STUB, STUB_HOST_FLAG, "{host}", STUB_PORT_FLAG, "{port}",
                                       "--name", "tf\0nul"]}, "args"),
            ("env value", {"env": {"SD_TF_NUL": "a\0b"}}, "env"),
            ("cwd", {"cwd": nul_dir}, "cwd")):
        problems += _l_refused(mod, "NUL in %s" % label, *_l_parse(mod, _l_doc(sandbox, **keys)),
                               needles=(needle,), absent=(TF_TOKEN, "\0"))
    return problems, ["a NUL in command, an args element, an env value or cwd is a ConfigError "
                      "naming the key"]


def l31(mod, sandbox):
    """M20: body_limit takes the CLI's range (1 KiB..1 GiB); response_limit is capped at 1 GiB."""
    gib = 1024 * 1024 * 1024
    problems = []
    for key, bad, good in (("body_limit", (0, 1, 1023, gib + 1, True, "2048"), (1024, gib)),
                           ("response_limit", (0, -1, gib + 1, True, "2048"), (1, gib))):
        for value in bad:
            doc = _l_doc(sandbox)
            doc[key] = value
            problems += _l_refused(mod, "%s=%r" % (key, value), *_l_parse(mod, doc),
                                   needles=(key,), absent=(TF_TOKEN,))
        for value in good:
            doc = _l_doc(sandbox)
            doc[key] = value
            found, settings = _l_accepted(mod, "%s=%r" % (key, value), *_l_parse(mod, doc))
            problems += found
            if settings is not None and getattr(settings, key, None) != value:
                problems.append("%s=%r: Settings carries %r" % (key, value, getattr(settings, key, None)))
    return problems, ["body_limit in 1024..1 GiB (as --body-limit), response_limit in 1..1 GiB"]


def l32(mod, sandbox):
    """G4, CHANGED: the precedence is explicit CLI flag > config key > built-in default.
    (It used to pin the opposite for body_limit: a config value other than the 32 MiB
    default beat --body-limit, one equal to the default let the flag win.)"""
    problems = []
    if not callable(getattr(mod, "_sd_settings", None)):
        return ["the module defines no _sd_settings"], []
    flag = 4 * 1024 * 1024
    cfg_own = 2 * 1024 * 1024
    rows = (("config 2 MiB, flag 4 MiB", {"body_limit": cfg_own}, ["--body-limit", str(flag)], flag),
            ("config = default, flag 4 MiB", {"body_limit": L_DEFAULT_BODY_LIMIT},
             ["--body-limit", str(flag)], flag),
            ("config absent, flag 4 MiB", {}, ["--body-limit", str(flag)], flag),
            ("config 2 MiB, no flag", {"body_limit": cfg_own}, [], cfg_own),
            ("config absent, no flag", {}, [], L_DEFAULT_BODY_LIMIT),
            ("config 2 MiB, flag = default", {"body_limit": cfg_own},
             ["--body-limit", str(L_DEFAULT_BODY_LIMIT)], L_DEFAULT_BODY_LIMIT))
    for index, (label, top, argv, want) in enumerate(rows):
        kind, val = _l_merge(mod, sandbox, top, argv, "g4-%d.json" % index)
        if kind != "ok":
            problems.append("%s: %s (%s)" % (label, kind, str(val)[:120]))
        elif val.body_limit != want:
            problems.append("%s: body_limit %r, expected %r" % (label, val.body_limit, want))
    return problems, ["explicit --body-limit beats the config body_limit (also when it equals the "
                      "default); the config applies without the flag"]


def _l_top(mod, sandbox, top):
    return _l_attempt(mod, mod.parse_config, _l_raw(dict(_l_doc(sandbox), **top)))


def l33(mod, sandbox):
    """The five new optional top-level keys are accepted and carried by Settings."""
    problems = []
    top = {"bind": "10.1.2.3", "port": 8123, "allow_remote": True, "insecure_cleartext": True,
           "max_connections": 16}
    kind, val = _l_top(mod, sandbox, top)
    if kind != "ok":
        return ["all five keys: %s (%s)" % (kind, str(val)[:160])], []
    for key, want in top.items():
        got = getattr(val, key, "<missing>")
        if got != want or type(got) is not type(want):
            problems.append("Settings.%s is %r, expected %r" % (key, got, want))
    for label, extra in (("port 0", {"port": 0}), ("port 65535", {"port": 65535}),
                         ("max_connections 1", {"max_connections": 1}),
                         ("max_connections 1024", {"max_connections": 1024}),
                         ("bind ::1", {"bind": "::1"}), ("bind hostname", {"bind": "sd.example.test"}),
                         ("both bools false", {"allow_remote": False, "insecure_cleartext": False})):
        problems += _l_accepted(mod, label, mod.parse_config, (_l_raw(dict(_l_doc(sandbox), **extra)),))[0]
    kind, val = _l_top(mod, sandbox, {})
    if kind == "ok":
        for key, want in (("bind", L_DEFAULT_BIND), ("port", 0), ("allow_remote", False),
                          ("insecure_cleartext", False), ("max_connections", L_DEFAULT_MAX_CONNECTIONS)):
            if getattr(val, key, "<missing>") != want:
                problems.append("absent key: Settings.%s is %r, expected %r" % (key, getattr(val, key, "<missing>"), want))
    kind, val = _l_top(mod, sandbox, top)
    text = repr(val) + str(val)
    for key in top:
        if key + "=" not in text:
            problems.append("repr(Settings) does not show %s" % key)
    if TF_TOKEN in text:
        problems.append("repr(Settings) carries the token")
    return problems, ["bind, port, allow_remote, insecure_cleartext and max_connections accepted, "
                      "defaulted when absent, shown in repr"]


def l34(mod, sandbox):
    """A wrong type per key is refused naming the key, never the value."""
    problems = []
    sentinel = L_UNKNOWN_VALUE
    rows = (("bind", (5, True, None, ["10.0.0.1"], 1.5)),
            ("port", ("8080", True, False, 1.5, None, [8080], sentinel)),
            ("allow_remote", (1, 0, "true", None, [True], sentinel)),
            ("insecure_cleartext", (1, 0, "true", None, [True], sentinel)),
            ("max_connections", ("8", True, False, 2.5, None, sentinel)))
    for key, bad in rows:
        for value in bad:
            problems += _l_refused(mod, "%s=%r" % (key, value if value != sentinel else "<sentinel>"),
                                   mod.parse_config, (_l_raw(dict(_l_doc(sandbox), **{key: value})),),
                                   needles=(key,), absent=(sentinel, TF_TOKEN))
    return problems, ["bind must be a string, port/max_connections a real int (never a bool), "
                      "allow_remote/insecure_cleartext a real bool (never 0/1)"]


def l35(mod, sandbox):
    """Ranges (as --port / --max-connections) and string hygiene; no value echoed."""
    problems = []
    for key, bad in (("port", (-1, 65536, 10 ** 9)), ("max_connections", (0, -1, 1025, 10 ** 9)),
                     ("bind", ("", "10.0.0.1\0", "\0"))):
        for value in bad:
            problems += _l_refused(mod, "%s=%r" % (key, value), mod.parse_config,
                                   (_l_raw(dict(_l_doc(sandbox), **{key: value})),),
                                   needles=(key,), absent=(TF_TOKEN, "10.0.0.1"))
    return problems, ["port 0..65535, max_connections 1..1024, bind a non-empty string without a NUL"]


def l36(mod, sandbox):
    doc = _l_doc(sandbox)
    doc["tf_unknown_top"] = L_UNKNOWN_VALUE
    needles = ("tf_unknown_top", "bind", "port", "allow_remote", "insecure_cleartext", "max_connections",
               "body_limit", "response_limit")
    problems = _l_refused(mod, "unknown top-level key", mod.parse_config, (_l_raw(doc),),
                          needles=needles, absent=(L_UNKNOWN_VALUE, TF_TOKEN))
    return problems, ["the unknown-key refusal lists the allowed keys, the new five included"]


def l37(mod, sandbox):
    """An explicit CLI flag beats the config key, for each of the six."""
    problems = []
    if not callable(getattr(mod, "_sd_settings", None)):
        return ["the module defines no _sd_settings"], []
    rows = (
        ("bind", {"bind": "127.0.0.2"}, ["--bind", "127.0.0.3"], "bind", "127.0.0.3"),
        ("port 0 beats config 5", {"port": 5}, ["--port", "0"], "port", 0),
        ("port 7000 beats config 5", {"port": 5}, ["--port", "7000"], "port", 7000),
        ("max_connections", {"max_connections": 4}, ["--max-connections", "9"], "max_connections", 9),
        ("body_limit", {"body_limit": 2048}, ["--body-limit", "4096"], "body_limit", 4096),
        ("allow_remote", {"allow_remote": False, "insecure_cleartext": True, "bind": "10.0.0.1"},
         ["--allow-remote"], "allow_remote", True),
        ("insecure_cleartext", {"allow_remote": True, "insecure_cleartext": False, "bind": "10.0.0.1"},
         ["--insecure-cleartext"], "insecure_cleartext", True),
    )
    for index, (label, top, argv, attr, want) in enumerate(rows):
        kind, val = _l_merge(mod, sandbox, top, argv, "l37-%d.json" % index)
        if kind != "ok":
            problems.append("%s: %s (%s)" % (label, kind, str(val)[:120]))
            continue
        got = getattr(val, attr, "<missing>")
        if got != want or type(got) is not type(want):
            problems.append("%s: %s is %r, expected %r" % (label, attr, got, want))
    return problems, ["flag > config for bind, port (0 included), max_connections, body_limit, "
                      "allow_remote and insecure_cleartext"]


def l38(mod, sandbox):
    """With no flag the config key applies; with neither the built-in default."""
    problems = []
    if not callable(getattr(mod, "_sd_settings", None)):
        return ["the module defines no _sd_settings"], []
    top = {"bind": "10.4.5.6", "port": 8456, "allow_remote": True, "insecure_cleartext": True,
           "max_connections": 3, "body_limit": 4096}
    kind, val = _l_merge(mod, sandbox, top, [], "l38-a.json")
    if kind != "ok":
        return ["config only: %s (%s)" % (kind, str(val)[:120])], []
    for key, want in top.items():
        got = getattr(val, key, "<missing>")
        if got != want or type(got) is not type(want):
            problems.append("config only: %s is %r, expected %r" % (key, got, want))
    kind, val = _l_merge(mod, sandbox, {"port": 8456}, ["--bind", "127.0.0.1"], "l38-b.json")
    if kind != "ok" or (val.port, val.bind, val.max_connections) != (8456, "127.0.0.1", L_DEFAULT_MAX_CONNECTIONS):
        problems.append("mixed (config port, flag bind, default max_connections): %s %r" % (kind, val))
    helptext = mod._sd_parser().format_help()
    for needle in ("127.0.0.1", "config"):
        if needle not in helptext:
            problems.append("--help does not mention %r" % needle)
    return problems, ["config values apply when no flag is given, built-in defaults last; --help "
                      "states the precedence"]


def l39(mod, sandbox):
    """A non-loopback bind from the config is refused exactly like the CLI form."""
    problems = []
    if not callable(getattr(mod, "_sd_settings", None)):
        return ["the module defines no _sd_settings"], []
    kind_cli, msg_cli = _l_merge(mod, sandbox, {}, ["--bind", "0.0.0.0"], "l39-cli.json")
    kind_cfg, msg_cfg = _l_merge(mod, sandbox, {"bind": "0.0.0.0"}, [], "l39-cfg.json")
    for label, kind in (("CLI", kind_cli), ("config", kind_cfg)):
        if kind != "refused":
            problems.append("%s bind 0.0.0.0 without the acknowledgements: %s, expected a refusal" % (label, kind))
    if kind_cli == kind_cfg == "refused":
        if msg_cli != msg_cfg:
            problems.append("the config refusal differs from the CLI one (%r vs %r)" % (msg_cfg[:100], msg_cli[:100]))
        if "--allow-remote" not in msg_cfg or "--insecure-cleartext" not in msg_cfg or "\n" in msg_cfg:
            problems.append("the refusal does not name both acknowledgements on one line")
    kind, _msg = _l_merge(mod, sandbox, {"bind": "0.0.0.0", "allow_remote": True}, [], "l39-half.json")
    if kind != "refused":
        problems.append("config bind + allow_remote without insecure_cleartext: %s, expected a refusal" % kind)
    kind, val = _l_merge(mod, sandbox, {"bind": "0.0.0.0", "allow_remote": True, "insecure_cleartext": True},
                         [], "l39-full.json")
    if kind != "ok" or val.bind != "0.0.0.0":
        problems.append("config bind + both acknowledgements: %s, expected accepted" % kind)
    kind, val = _l_merge(mod, sandbox, {"bind": "0.0.0.0"}, ["--bind", "127.0.0.1"], "l39-flag.json")
    if kind != "ok":
        problems.append("config bind 0.0.0.0 overridden by a loopback --bind: %s, expected accepted" % kind)
    return problems, ["bind in the config without allow_remote + insecure_cleartext: the same refusal "
                      "text as --bind; with both it is accepted; a loopback flag overrides it"]


GL_FUNCS = (l1, l2, l3, l4, l5, l6, l7, l8, l9, l10, l11, l12, l13, l14, l15, l16, l17, l18,
            l19, l20, l21, l22, l23, l24, l25, l26, l27, l28, l29, l30, l31, l32,
            l33, l34, l35, l36, l37, l38, l39)
GL_CLI = frozenset(("L23", "L25", "L26", "L27", "L28", "L32", "L37", "L38", "L39"))


def group_l(suite, fixture_root):
    mod, why = load_module("l")
    for cid, fn in zip(GL_CASES, GL_FUNCS):
        tag = cid.split(" ", 1)[0]
        problems, detail = [], []
        if fn is not l29:
            if mod is None:
                suite.record(GL, cid, [why])
                continue
            names = L_NAMES_CONFIG + (L_NAMES_CLI if tag in GL_CLI else ())
            absent = missing_names(mod, names)
            if absent:
                suite.record(GL, cid, absent)
                continue
        sandbox = new_sandbox(fixture_root, "l-" + tag.lower())
        try:
            problems, detail = fn(mod, sandbox)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])]
        suite.record(GL, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group S -- supervisor: spawn, readiness, the stop ladder, the budget
# ---------------------------------------------------------------------------
# Unit 4 only: no scheduler, no HTTP.  Nine cases run the real stub child
# (S1, S3, S4, S7-S10, S14, S17); the rest run in-process
# against a privately loaded module with `subprocess.Popen`, `os.killpg`,
# `os.kill` and `_sd_wait_exit_unreaped` replaced by recorders (nothing is
# really spawned or signalled), or are pure (Budget).
#
# The contract this group pins (task-012..014 implement it; plan Step 3, D-1,
# H2, H5, M3).  Every name below is module-level in Scripts/gpu-router.py unit 4:
#
#   _SD_RESTART_BUDGET = 5, _SD_RESTART_WINDOW_S = 600.0  (pinned by S11),
#   _SD_STOP_GRACE_S, _SD_READY_TICK_S, _SD_PORT_TRIES = 3 (spawns per start,
#   S7), _SD_EARLY_EXIT_S = 2.0 (a child that exits within this of its spawn
#   is a port collision: respawn on a new port), _SD_KILL_STEP_S (exists).
#   Every constant is READ AT CALL TIME (tests patch them on the module).
#   _sd_now() -> float: the clock seam, time.monotonic by default.  Every
#   deadline below is on the time.monotonic scale.
#
#   _sd_free_port() -> int: bind(("127.0.0.1", 0)), read, close.
#   _signal_group(pgid, sig) -> bool: os.killpg(pgid, sig); True if delivered,
#     False on ESRCH (ProcessLookupError) or EPERM (PermissionError).
#   _sd_wait_exit_unreaped(pid, deadline) -> bool: True once pid has EXITED,
#     WITHOUT reaping it; False at the deadline.  The branch is chosen by
#     sys.platform, never by hasattr(os, "waitid"): "darwin" uses the module's
#     `select` (select.kqueue + select.kevent(pid, filter=KQ_FILTER_PROC,
#     flags=KQ_EV_ADD | KQ_EV_ONESHOT, fflags=KQ_NOTE_EXIT); ESRCH on register
#     = already exited); anything else polls os.waitid(os.P_PID, pid,
#     os.WEXITED | os.WNOWAIT | os.WNOHANG) (ChildProcessError = exited).  The
#     names are looked up on the module's `select` and `os` at call time (no
#     local import), so S19 can exercise both branches on any host.
#
#   Budget(): no lock of its own (round-3 H2).  record(now=None) -> bool
#     appends one crash at `now` (default _sd_now()), drops crashes older than
#     _SD_RESTART_WINDOW_S, and returns True while the crashes inside the
#     window number >= _SD_RESTART_BUDGET (the 5th trips it and it stays
#     tripped); clear() forgets every crash.
#
#   Supervisor(): `registry` is a set of the pgids this supervisor spawned
#     and has not yet seen gone.
#   Supervisor.start(spec) -> Child: ONE spawn on a fresh _sd_free_port(),
#     through subprocess.Popen (looked up on the subprocess module at call
#     time) with argv = [spec.command] + render_args(spec, "127.0.0.1", port)
#     as a LIST, shell False, start_new_session=True, stdin/stdout/stderr
#     DEVNULL, close_fds True, cwd=spec.cwd, env = a minimal fixed environment
#     (PATH, HOME, LANG; D-1) plus dict(spec.env) -- never os.environ.  The
#     pgid is registered at once.  Child carries: spec, proc (the Popen),
#     pid, pgid (== pid, start_new_session; no os.getpgid call), port, argv.
#   Supervisor.wait_ready(child, deadline, stop) -> bool: tick-bounded
#     (_SD_READY_TICK_S) probes of GET / on 127.0.0.1:child.port; any status
#     EXCEPT 503 is ready (H2), 503 keeps polling; True only while the child is
#     alive (re-checked after the answer); False at once when the child exits,
#     at the deadline, or when the `stop` Event is set.  It never kills.
#   Supervisor.launch(spec, deadline, stop) -> (outcome, child): start() then
#     wait_ready(); a child that exits within _SD_EARLY_EXIT_S of its spawn is
#     stopped and respawned on a new port, at most _SD_PORT_TRIES spawns in
#     all.  outcome is "ready" (child is the live Child), "failed" (the child
#     exited before it was ready, tries spent), "timeout" (deadline) or
#     "stopped" (the Event); on every outcome but "ready" child is None and
#     every group it spawned was stopped and dropped from the registry.
#   Supervisor.stop(child) -> bool: the kill-before-reap ladder (D-12, H5):
#     _signal_group(pgid, SIGTERM) -> _sd_wait_exit_unreaped(pid, now +
#     spec.stop_grace) -> _signal_group(pgid, SIGKILL) while the leader is
#     still unreaped -> reap through child.proc (poll/wait; never os.waitpid,
#     never os.kill on a pid) -> probe os.killpg(pgid, 0) until ESRCH/EPERM
#     within _SD_KILL_STEP_S.  True only when the group is confirmed gone, and
#     then the pgid leaves the registry at once (M3); False when the group
#     survives (the pgid stays registered: a zombie group).  stop() of a child
#     whose pgid is no longer registered signals nothing and returns True.
#   Supervisor.kill_all() -> None: the last-resort sweep over the registry;
#     each entry is probed with os.killpg(pgid, 0) RIGHT BEFORE its SIGKILL;
#     ESRCH or EPERM drops the entry and it is never SIGKILLed (M3, S-L6).

GS_CASES = (
    "S1 clean stop, group gone",
    "S2 _signal_group ESRCH/EPERM",
    "S3 SIGTERM ignored -> SIGKILL",
    "S4 grandchild swept",
    "S5 no signal after confirmed",
    "S6 ESRCH confirmed or False",
    "S7 port-collision retry",
    "S8 start_timeout",
    "S9 crash-on-start",
    "S10 wait_ready honours stop",
    "S11 budget trips on the 5th",
    "S12 budget clear()",
    "S13 budget window slides",
    "S14 child env minimal",
    "S15 argv list, no shell",
    "S16 free port on loopback",
    "S17 503 = loading, then ready",
    "S18 registry drop, sweep skips",
    "S19 wait branch by platform",
    "S20 early-reaped leader: grace",
)

S_NAMES_SUP = ("ModelSpec", "parse_config", "render_args", "Supervisor", "_signal_group",
               "_sd_free_port", "_sd_wait_exit_unreaped", "_SD_KILL_STEP_S", "_SD_PORT_TRIES",
               "_SD_EARLY_EXIT_S", "_SD_READY_TICK_S", "_SD_STOP_GRACE_S")
S_NAMES_BUDGET = ("Budget", "_SD_RESTART_BUDGET", "_SD_RESTART_WINDOW_S", "_sd_now")
S_METHODS = ("start", "wait_ready", "launch", "stop", "kill_all")
S_SLACK_S = 3.0              # added to every derived timing bound
S_FAKE_GRACE_S = 0.2         # stop_grace of the in-process fake child
S_FAKE_KILL_STEP_S = 0.3     # _SD_KILL_STEP_S while an in-process case runs
S_ENV_VALUE = "tf-sd-env-pass-" + secrets.token_hex(6)
S_LEAK_NAME = "TF_SD_ROUTER_LEAK"
S_ENV_ALLOWED = ("PATH", "HOME", "LANG")
# Names the platform or the interpreter adds to a child's environment by itself.
S_ENV_TOLERATED = ("__CF_USER_TEXT_ENCODING", "LC_CTYPE", "__PYVENV_LAUNCHER__")
# Linux values, injected only where this interpreter lacks them (S19).
S_WAITID_CONSTS = (("P_PID", 1), ("WEXITED", 4), ("WNOWAIT", 0x01000000), ("WNOHANG", 1))
TERM = int(signal.SIGTERM)
KILL = int(signal.SIGKILL)


def _s_spec(mod, state_dir, name, *flags, **keys):
    """A ModelSpec for the stub, through the real loader."""
    doc = base_config({name: stub_model(state_dir, name, *flags, **keys)})
    return mod.parse_config(_l_raw(doc)).models[name]


def _s_fake_spec(mod):
    return mod.ModelSpec(name="tfs", command=sys.executable, args=("--port", "{port}"), env=(),
                         stop_grace=S_FAKE_GRACE_S)


def _s_group_alive(pgid):
    """True while process group *pgid* has a member we may signal."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _s_ladder_bound(mod, spec):
    return spec.stop_grace + mod._SD_KILL_STEP_S + S_SLACK_S


def _s_launch(sup, spec, timeout, stop=None):
    """(outcome, child, seconds) of sup.launch with a deadline *timeout* from now."""
    stop = stop if stop is not None else threading.Event()
    t0 = time.monotonic()
    outcome, child = sup.launch(spec, t0 + timeout, stop)
    return outcome, child, time.monotonic() - t0


def _s_stopped(mod, sup, child, label):
    """Problems unless sup.stop(child) confirms the group gone within the ladder bound."""
    t0 = time.monotonic()
    ok = sup.stop(child)
    took = time.monotonic() - t0
    problems = []
    if ok is not True:
        problems.append("%s: stop() returned %r, expected True" % (label, ok))
    if took > _s_ladder_bound(mod, child.spec):
        problems.append("%s: stop() took %.2fs, bound %.2fs" % (label, took, _s_ladder_bound(mod, child.spec)))
    if _s_group_alive(child.pgid):
        problems.append("%s: stop() returned but group %d still has members" % (label, child.pgid))
    if child.proc.returncode is None:
        problems.append("%s: the leader was not reaped" % label)
    if child.pgid in sup.registry:
        problems.append("%s: the registry still holds pgid %d after a confirmed stop" % (label, child.pgid))
    return problems, took


def _s_live(fn):
    """A real-stub case: a fresh Supervisor, every stub named in *names* reaped after."""
    def run(mod, sandbox):
        sup = mod.Supervisor()
        names = []
        try:
            problems, detail = fn(mod, sup, sandbox, names)
        finally:
            leftovers = reap_stubs(sandbox, names)
        return problems + leftovers, detail
    run.__name__ = fn.__name__
    return run


def _s_ready(sup, spec, names, timeout=10.0):
    """(problems, child): launch must answer "ready" with a live child."""
    names.append(spec.name)
    outcome, child, took = _s_launch(sup, spec, timeout)
    if outcome != "ready" or child is None:
        return ["launch answered (%r, %r) after %.2fs, expected 'ready' with a child"
                % (outcome, child, took)], None
    return [], child


@_s_live
def s1(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s1", stop_grace=2.0)
    problems, child = _s_ready(sup, spec, names)
    if child is None:
        return problems, []
    if child.pgid != child.pid:
        problems.append("pgid %r != pid %r (start_new_session)" % (child.pgid, child.pid))
    if child.pgid not in sup.registry:
        problems.append("the spawned pgid is not in the registry")
    if not pid_alive(child.pid):
        problems.append("the ready child is not alive")
    found, took = _s_stopped(mod, sup, child, "stop")
    problems += found
    if took >= spec.stop_grace:
        problems.append("stop took %.2fs: a child that exits on SIGTERM must not wait out "
                        "stop_grace %.1fs" % (took, spec.stop_grace))
    return problems, ["SIGTERM ends it in %.2fs; group gone, reaped, unregistered" % took]


def s2(mod, sandbox):
    problems = []
    pgid = _s_free_pids(1)[0]
    for label, exc, want in (("delivered", None, True), ("ESRCH", ProcessLookupError, False),
                             ("EPERM", PermissionError, False)):
        rec = _SKillpgRecorder({TERM: exc} if exc else {})
        with patched(os, [("killpg", rec)]):
            got = mod._signal_group(pgid, signal.SIGTERM)
        if got is not want:
            problems.append("%s: _signal_group returned %r, expected %r" % (label, got, want))
        if rec.calls != [(pgid, TERM)]:
            problems.append("%s: killpg calls %r, expected [(%d, %d)]" % (label, rec.calls, pgid, TERM))
    return problems, ["True when delivered, False on ESRCH and EPERM; exactly one killpg"]


@_s_live
def s3(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s3", "--ignore-term", stop_grace=0.5)
    problems, child = _s_ready(sup, spec, names)
    if child is None:
        return problems, []
    found, took = _s_stopped(mod, sup, child, "stop")
    problems += found
    if took < spec.stop_grace * 0.9:
        problems.append("stop took %.2fs, less than stop_grace %.1fs: SIGKILL came early"
                        % (took, spec.stop_grace))
    if child.proc.returncode != -KILL:
        problems.append("leader returncode %r, expected %d (SIGKILL)" % (child.proc.returncode, -KILL))
    return problems, ["SIGTERM ignored; SIGKILL after stop_grace, %.2fs" % took]


@_s_live
def s4(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s4", "--spawn-grandchild", stop_grace=2.0)
    problems, child = _s_ready(sup, spec, names)
    if child is None:
        return problems, []
    if not wait_until(lambda: len(read_pids(sandbox, "s4")) >= 2):
        problems.append("control: the stub recorded no grandchild")
    pids = read_pids(sandbox, "s4")
    found, _took = _s_stopped(mod, sup, child, "stop")
    problems += found
    alive = [pid for pid in pids if not pid_dead(pid)]
    if alive:
        problems.append("pid(s) %r of the group outlived stop()" % alive)
    return problems, ["leader and grandchild %r gone after stop()" % pids]


class _SKillpgRecorder:
    """Stands in for os.killpg: records (pgid, sig) and the reap order, signals nothing.

    `script` maps (pgid, sig) or sig to an exception class to raise.
    """

    def __init__(self, script=None):
        self.calls = []
        self.order = []
        self.script = dict(script or {})

    def __call__(self, pgid, sig):
        self.calls.append((pgid, int(sig)))
        self.order.append(("killpg", pgid, int(sig)))
        exc = self.script.get((pgid, int(sig)), self.script.get(int(sig)))
        if exc is not None:
            raise exc("tf scripted killpg answer")


class _SFakeProc:
    """A Popen that spawned nothing.  It is reaped (returncode set, a "reap"
    entry in the order) by poll()/wait() only once its leader has exited (the
    patched wait helper said so) or its group was SIGKILLed."""

    def __init__(self, world, argv, kwargs, pid):
        self.world = world
        self.argv = argv
        self.kwargs = kwargs
        self.pid = pid
        self.returncode = None

    def _reapable(self):
        return (self.pid in self.world.exited or (self.pid, KILL) in self.world.rec.calls)

    def poll(self):
        if self.returncode is None and self._reapable():
            self.returncode = -KILL
            self.world.rec.order.append(("reap", self.pid))
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            if not self._reapable():
                raise subprocess.TimeoutExpired(self.argv, timeout or 0)
            self.poll()
        return self.returncode

    def send_signal(self, sig):
        self.world.direct.append((self.pid, int(sig)))

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)


def _s_free_pids(count):
    """*count* pids no process holds now (fake pids: never really signalled)."""
    found, pid = [], 99000
    while len(found) < count and pid > 30000:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            found.append(pid)
        except PermissionError:
            pass
        pid -= 7
    return found


@contextlib.contextmanager
def _s_fake_world(mod, pids, script=None, wait_result=True):
    """Popen, os.killpg, os.kill and _sd_wait_exit_unreaped replaced for the block.

    Yields a namespace: rec (the killpg recorder, its `order` also holds the
    reaps), made (the fake processes in spawn order), direct (signals sent to a
    pid through the Popen), kills (os.kill calls), waits (the wait-helper pids).
    """
    world = types.SimpleNamespace(rec=_SKillpgRecorder(script), made=[], direct=[], kills=[],
                                  waits=[], exited=set())
    pool = list(pids)

    def fake_popen(argv, **kwargs):
        if len(world.made) >= len(pool):
            raise OSError("tf: more spawns than the case allows")
        proc = _SFakeProc(world, argv, kwargs, pool[len(world.made)])
        world.made.append(proc)
        return proc

    def fake_kill(pid, sig):
        world.kills.append((pid, int(sig)))
        raise ProcessLookupError("tf: os.kill is not the supervisor's tool")

    def fake_wait(pid, deadline, *args, **kwargs):
        world.waits.append(pid)
        if wait_result:
            world.exited.add(pid)
        return wait_result

    with patched(os, [("killpg", world.rec), ("kill", fake_kill)]), \
            patched(subprocess, [("Popen", fake_popen)]), \
            patched(mod, [("_sd_wait_exit_unreaped", fake_wait),
                          ("_SD_KILL_STEP_S", S_FAKE_KILL_STEP_S)]):
        yield world


def _s_direct_problems(world):
    problems = []
    if world.kills:
        problems.append("os.kill on a pid %r: the supervisor signals groups only" % world.kills)
    if world.direct:
        problems.append("Popen.send_signal/terminate/kill %r: the supervisor signals groups only"
                        % world.direct)
    return problems


def s5(mod, sandbox):
    pid = _s_free_pids(1)[0]
    problems = []
    with _s_fake_world(mod, [pid], script={0: ProcessLookupError}) as world:
        sup = mod.Supervisor()
        child = sup.start(_s_fake_spec(mod))
        if sup.stop(child) is not True:
            problems.append("control: the first stop() did not confirm the group gone")
        mark = len(world.rec.calls)
        again = sup.stop(child)
        if world.rec.calls[mark:]:
            problems.append("a second stop() signalled %r" % world.rec.calls[mark:])
        if again is not True:
            problems.append("a second stop() of a gone group returned %r, expected True" % again)
        sup.kill_all()
        if world.rec.calls[mark:]:
            problems.append("kill_all() signalled %r after the group was confirmed gone"
                            % world.rec.calls[mark:])
        problems += _s_direct_problems(world)
    return problems, ["after ESRCH the pgid is never signalled again (stop or kill_all)"]


def s6(mod, sandbox):
    pid_ok, pid_bad = _s_free_pids(2)
    problems = []
    with _s_fake_world(mod, [pid_ok], script={0: ProcessLookupError}) as world:
        sup = mod.Supervisor()
        child = sup.start(_s_fake_spec(mod))
        ok = sup.stop(child)
        calls, order = world.rec.calls, world.rec.order
        if ok is not True:
            problems.append("confirmed: stop() returned %r, expected True" % ok)
        if not calls or calls[0] != (pid_ok, TERM):
            problems.append("confirmed: the first signal is %r, expected SIGTERM to the group"
                            % (calls[:1],))
        if (pid_ok, KILL) not in calls:
            problems.append("confirmed: no SIGKILL to the group (kill before reap)")
        elif ("reap", pid_ok) in order and order.index(("reap", pid_ok)) < order.index(
                ("killpg", pid_ok, KILL)):
            problems.append("confirmed: the leader was reaped BEFORE the group SIGKILL")
        if ("reap", pid_ok) not in order:
            problems.append("confirmed: the leader was never reaped through child.proc")
        if not calls or calls[-1] != (pid_ok, 0):
            problems.append("confirmed: the ladder did not end on the signal-0 ESRCH probe "
                            "(calls %r)" % calls)
        if pid_ok not in world.waits:
            problems.append("confirmed: _sd_wait_exit_unreaped was not used for the grace")
        if child.pgid in sup.registry:
            problems.append("confirmed: pgid still registered")
        problems += _s_direct_problems(world)
    with _s_fake_world(mod, [pid_bad]) as world:
        sup = mod.Supervisor()
        child = sup.start(_s_fake_spec(mod))
        t0 = time.monotonic()
        ok = sup.stop(child)
        took = time.monotonic() - t0
        bound = S_FAKE_GRACE_S + S_FAKE_KILL_STEP_S + S_SLACK_S
        if ok is not False:
            problems.append("survivor: stop() returned %r, expected False" % ok)
        if took > bound:
            problems.append("survivor: stop() took %.2fs, bound %.2fs" % (took, bound))
        for sig in (TERM, KILL, 0):
            if (pid_bad, sig) not in world.rec.calls:
                problems.append("survivor: no signal %d to the group" % sig)
        if child.pgid not in sup.registry:
            problems.append("survivor: a live (zombie) group was dropped from the registry")
        problems += _s_direct_problems(world)
    return problems, ["TERM, wait, KILL, reap, probe 0 -> ESRCH: True and unregistered; "
                      "a group that answers signal 0 forever: False, still registered"]


@_s_live
def s7(mod, sup, sandbox, names):
    problems = []
    spec = _s_spec(mod, sandbox, "s7", "--bind-fail-first", "2", stop_grace=2.0)
    found, child = _s_ready(sup, spec, names, timeout=15.0)
    problems += found
    starts = read_starts(sandbox, "s7")
    if len(starts) != 3:
        problems.append("ready after %d spawn(s), expected 3 (two port collisions)" % len(starts))
    if child is not None:
        ports = [ev.get("port") for ev in events_named(sandbox, "start", "s7")]
        if ports[-1:] != [child.port]:
            problems.append("child.port %r is not the port the stub bound %r" % (child.port, ports))
        problems += _s_stopped(mod, sup, child, "stop")[0]
    spec = _s_spec(mod, sandbox, "s7b", "--bind-fail", stop_grace=2.0)
    names.append("s7b")
    outcome, child, took = _s_launch(sup, spec, 15.0)
    if outcome != "failed" or child is not None:
        problems.append("always colliding: launch answered (%r, %r), expected ('failed', None)"
                        % (outcome, child))
    tries = mod._SD_PORT_TRIES
    if len(read_starts(sandbox, "s7b")) != tries:
        problems.append("always colliding: %d spawn(s), expected _SD_PORT_TRIES = %d"
                        % (len(read_starts(sandbox, "s7b")), tries))
    if sup.registry:
        problems.append("the registry holds %r after both launches ended" % sorted(sup.registry))
    return problems, ["an exit within _SD_EARLY_EXIT_S respawns on a new port, at most "
                      "_SD_PORT_TRIES spawns"]


@_s_live
def s8(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s8", "--hang", stop_grace=1.0)
    names.append("s8")
    window = 1.0
    outcome, child, took = _s_launch(sup, spec, window)
    problems = []
    if outcome != "timeout" or child is not None:
        problems.append("launch answered (%r, %r), expected ('timeout', None)" % (outcome, child))
    if took < window * 0.9:
        problems.append("timed out after %.2fs, before the %.1fs deadline" % (took, window))
    bound = window + _s_ladder_bound(mod, spec)
    if took > bound:
        problems.append("launch took %.2fs, bound %.2fs" % (took, bound))
    if len(read_starts(sandbox, "s8")) != 1:
        problems.append("%d spawns: a child that stays alive is not a port collision"
                        % len(read_starts(sandbox, "s8")))
    if sup.registry:
        problems.append("the registry holds %r after the timeout" % sorted(sup.registry))
    return problems, ["a child that never answers GET /: 'timeout' in %.2fs, group stopped" % took]


@_s_live
def s9(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s9", "--crash-on-start", stop_grace=1.0)
    names.append("s9")
    outcome, child, took = _s_launch(sup, spec, 20.0)
    problems = []
    if outcome != "failed" or child is not None:
        problems.append("launch answered (%r, %r), expected ('failed', None)" % (outcome, child))
    if took > 10.0:
        problems.append("'failed' took %.2fs: a crash must fail fast, not wait the deadline" % took)
    starts = len(read_starts(sandbox, "s9"))
    if not 1 <= starts <= mod._SD_PORT_TRIES:
        problems.append("%d spawns, expected 1.._SD_PORT_TRIES" % starts)
    if sup.registry:
        problems.append("the registry holds %r after the failure" % sorted(sup.registry))
    return problems, ["exit before ready: 'failed' in %.2fs after %d spawn(s)" % (took, starts)]


@_s_live
def s10(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s10", "--start-delay", "30", stop_grace=2.0)
    names.append("s10")
    child = sup.start(spec)
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    problems = []
    try:
        t0 = time.monotonic()
        ready = sup.wait_ready(child, t0 + 10.0, stop)
        took = time.monotonic() - t0
    finally:
        timer.cancel()
    if ready is not False:
        problems.append("wait_ready returned %r while the child was still loading" % ready)
    if took > 0.3 + 1.5:
        problems.append("wait_ready took %.2fs after stop was set at 0.3s" % took)
    if child.proc.poll() is not None:
        problems.append("wait_ready killed the child; it must only stop waiting")
    problems += _s_stopped(mod, sup, child, "stop")[0]
    return problems, ["the stop Event ends the wait in %.2fs; the child is left alive" % took]


def _s_records(budget, times):
    return [budget.record(now=t) for t in times]


def s11(mod, sandbox):
    problems = []
    if mod._SD_RESTART_BUDGET != 5 or mod._SD_RESTART_WINDOW_S != 600.0:
        problems.append("budget constants are (%r, %r), expected (5, 600.0)"
                        % (mod._SD_RESTART_BUDGET, mod._SD_RESTART_WINDOW_S))
    got = _s_records(mod.Budget(), (100.0, 101.0, 102.0, 103.0, 104.0, 105.0))
    want = [False, False, False, False, True, True]
    if got != want:
        problems.append("record() gave %r, expected %r" % (got, want))
    return problems, ["the 5th crash within 600 s trips it, and it stays tripped"]


def s12(mod, sandbox):
    budget = mod.Budget()
    tripped = _s_records(budget, (10.0, 11.0, 12.0, 13.0, 14.0))
    budget.clear()
    after = _s_records(budget, (15.0, 16.0, 17.0, 18.0, 19.0))
    problems = []
    if tripped[-1] is not True:
        problems.append("control: the 5th record() did not trip (%r)" % tripped)
    if after != [False, False, False, False, True]:
        problems.append("after clear() record() gave %r, expected four False then True" % after)
    return problems, ["clear() forgets every crash; five new ones are needed to trip again"]


def s13(mod, sandbox):
    problems = []
    got = _s_records(mod.Budget(), (0.0, 100.0, 200.0, 300.0, 601.0, 602.0))
    if got != [False, False, False, False, False, True]:
        problems.append("window 600: record() gave %r; t=0 must expire by t=601 and t=100 "
                        "must still count at t=602" % got)
    with patched(mod, [("_SD_RESTART_BUDGET", 2), ("_SD_RESTART_WINDOW_S", 10.0)]):
        got = _s_records(mod.Budget(), (0.0, 5.0))
        got2 = _s_records(mod.Budget(), (0.0, 11.0, 12.0))
        clock = [500.0]
        with patched(mod, [("_sd_now", lambda: clock[0])]):
            budget = mod.Budget()
            first = budget.record()
            clock[0] = 515.0
            second = budget.record()
            clock[0] = 516.0
            third = budget.record()
    if got != [False, True]:
        problems.append("patched budget 2/10 s: %r, expected [False, True]" % got)
    if got2 != [False, False, True]:
        problems.append("patched window 10 s: %r, expected [False, False, True]" % got2)
    if [first, second, third] != [False, False, True]:
        problems.append("record() without now must read _sd_now(): %r" % [first, second, third])
    return problems, ["crashes older than the window drop out; the constants and the "
                      "clock seam are read at call time"]


@_s_live
def s14(mod, sup, sandbox, names):
    spec = _s_spec(mod, sandbox, "s14", "--dump-env", env={"SD_TF_PASS": S_ENV_VALUE},
                   stop_grace=2.0)
    had = os.environ.get(S_LEAK_NAME)
    os.environ[S_LEAK_NAME] = TF_TOKEN
    try:
        problems, child = _s_ready(sup, spec, names)
    finally:
        if had is None:
            os.environ.pop(S_LEAK_NAME, None)
        else:
            os.environ[S_LEAK_NAME] = had
    if child is None:
        return problems, []
    path = os.path.join(sandbox, "s14.env.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            dump = json.load(fh)
        env, argv = dump.get("env") or {}, dump.get("argv") or []
    except (OSError, ValueError):
        env, argv = None, []
        problems.append("control: the stub wrote no env dump")
    if env is not None:
        extra = sorted(k for k in env if k not in S_ENV_ALLOWED + ("SD_TF_PASS",) + S_ENV_TOLERATED)
        if extra:
            problems.append("the child env carries names beyond PATH/HOME/LANG + config env: %r" % extra)
        if S_LEAK_NAME in env:
            problems.append("the router's environment leaked into the child")
        if any(TF_TOKEN in str(v) for v in env.values()):
            problems.append("a child env value carries the token")
        if "PATH" not in env:
            problems.append("the child env has no PATH")
        if env.get("SD_TF_PASS") != S_ENV_VALUE:
            problems.append("the config env pair SD_TF_PASS did not reach the child")
    if any(TF_TOKEN in a for a in argv):
        problems.append("the child argv carries the token")
    problems += _s_stopped(mod, sup, child, "stop")[0]
    return problems, ["env = PATH/HOME/LANG + config env (D-1); no router env, no token"]


def s15(mod, sandbox):
    pid = _s_free_pids(1)[0]
    problems = []
    cwd = new_sandbox(sandbox, "cwd")
    spec = mod.ModelSpec(name="tfs", command=sys.executable,
                         args=("-B", "x; echo {port}", "--port", "{port}", "--host", "{host}"),
                         env=(("SD_TF_PASS", S_ENV_VALUE),), cwd=cwd, stop_grace=S_FAKE_GRACE_S)
    with _s_fake_world(mod, [pid]) as world:
        sup = mod.Supervisor()
        child = sup.start(spec)
        if len(world.made) != 1:
            return ["%d Popen calls, expected 1" % len(world.made)], []
        proc = world.made[0]
        kw = proc.kwargs
        want = [sys.executable] + list(mod.render_args(spec, LOOPBACK, child.port))
        if not isinstance(proc.argv, list) or proc.argv != want:
            problems.append("Popen argv %r, expected the list %r" % (proc.argv, want))
        if list(child.argv) != want:
            problems.append("child.argv %r is not the spawned argv" % (child.argv,))
        if kw.get("shell"):
            problems.append("Popen shell=%r" % kw.get("shell"))
        if kw.get("start_new_session") is not True:
            problems.append("Popen start_new_session=%r, expected True" % kw.get("start_new_session"))
        for stream in ("stdin", "stdout", "stderr"):
            if kw.get(stream) != subprocess.DEVNULL:
                problems.append("Popen %s=%r, expected DEVNULL" % (stream, kw.get(stream)))
        if kw.get("close_fds") is False:
            problems.append("Popen close_fds=False")
        if kw.get("cwd") != cwd:
            problems.append("Popen cwd %r, expected the spec cwd" % kw.get("cwd"))
        env = kw.get("env")
        if not isinstance(env, dict) or env.get("SD_TF_PASS") != S_ENV_VALUE:
            problems.append("Popen env does not carry the config env")
        elif any(k not in S_ENV_ALLOWED + ("SD_TF_PASS",) for k in env):
            problems.append("Popen env names %r" % sorted(env))
        if child.pid != pid or child.pgid != pid:
            problems.append("child pid/pgid (%r, %r), expected (%d, %d)" % (child.pid, child.pgid, pid, pid))
        if child.pgid not in sup.registry:
            problems.append("the pgid was not registered at spawn")
        if child.spec is not spec or child.proc is not proc:
            problems.append("child.spec / child.proc are not the spec and the Popen")
        problems += _s_direct_problems(world)
        sup.registry.discard(child.pgid)
    return problems, ["argv a list from render_args, no shell, new session, DEVNULL, spec cwd"]


def s16(mod, sandbox):
    port = mod._sd_free_port()
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return ["_sd_free_port() returned %r" % (port,)], []
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK, port))
    except OSError as exc:
        return ["port %d is not bindable on loopback: %s" % (port, exc.strerror)], []
    finally:
        sock.close()
    return [], ["port %d, released and bindable on 127.0.0.1" % port]


@_s_live
def s17(mod, sup, sandbox, names):
    window = 1.5
    spec = _s_spec(mod, sandbox, "s17", "--ready-503-for", str(window), stop_grace=2.0)
    names.append("s17")
    child = sup.start(spec)
    t0 = time.monotonic()
    ready = sup.wait_ready(child, t0 + 10.0, threading.Event())
    took = time.monotonic() - t0
    problems = []
    if ready is not True:
        problems.append("wait_ready returned %r, expected True once the 503 window ended" % ready)
    elif took < window * 0.95:
        problems.append("ready after %.2fs, inside the %.1fs 503 window: 503 is loading, "
                        "not ready (H2)" % (took, window))
    if child.proc.poll() is not None:
        problems.append("the child is not alive after wait_ready")
    problems += _s_stopped(mod, sup, child, "stop")[0]
    return problems, ["503 model_loading keeps polling; ready after %.2fs" % took]


def s18(mod, sandbox):
    pa, pb, pc = _s_free_pids(3)
    problems = []
    script = {(pa, 0): ProcessLookupError, (pc, 0): PermissionError}
    with _s_fake_world(mod, [pa, pb, pc], script=script) as world:
        sup = mod.Supervisor()
        ca = sup.start(_s_fake_spec(mod))
        sup.start(_s_fake_spec(mod))
        sup.start(_s_fake_spec(mod))
        if sup.stop(ca) is not True:
            problems.append("control: stop(a) did not confirm the group gone")
        if pa in sup.registry:
            problems.append("pgid a is still registered after ESRCH")
        mark = len(world.rec.calls)
        sup.kill_all()
        sweep = world.rec.calls[mark:]
        if any(pgid == pa for pgid, _sig in sweep):
            problems.append("kill_all signalled the dropped pgid a: %r" % sweep)
        if (pb, KILL) not in sweep:
            problems.append("kill_all did not SIGKILL the registered group b: %r" % sweep)
        else:
            i = sweep.index((pb, KILL))
            if i == 0 or sweep[i - 1] != (pb, 0):
                problems.append("b's SIGKILL is not immediately preceded by a signal-0 probe: %r"
                                % sweep)
        if (pc, 0) not in sweep:
            problems.append("kill_all did not probe c: %r" % sweep)
        if (pc, KILL) in sweep:
            problems.append("kill_all SIGKILLed c after its probe answered EPERM")
        if pc in sup.registry:
            problems.append("c is still registered after EPERM")
        problems += _s_direct_problems(world)
    return problems, ["ESRCH drops a at once; the sweep probes before each SIGKILL; "
                      "EPERM drops c unsignalled"]


class _SFakeKqueue:
    def __init__(self, log, mode):
        self.log = log
        self.mode = mode
        log.append("kqueue")

    def control(self, changes, max_events, timeout=None):
        if changes:
            self.log.append("register")
            if self.mode == "esrch":
                raise OSError(errno.ESRCH, "tf: already exited")
            return []
        if timeout:
            time.sleep(min(timeout, 0.05))
        return []

    def close(self):
        self.log.append("close")


def _s_fake_select(log, mode):
    def kevent(ident, *args, **kwargs):
        log.append(("kevent", ident))
        return ("tf-kevent", ident)

    return types.SimpleNamespace(kqueue=lambda: _SFakeKqueue(log, mode), kevent=kevent,
                                 KQ_FILTER_PROC=-5, KQ_EV_ADD=1, KQ_EV_ONESHOT=0x10,
                                 KQ_NOTE_EXIT=0x80000000, select=select.select)


@contextlib.contextmanager
def _s_inject(owner, pairs):
    """Like `patched`, but a name the owner lacks is added and removed after."""
    saved = []
    try:
        for name, value in pairs:
            saved.append((name, hasattr(owner, name), getattr(owner, name, None)))
            setattr(owner, name, value)
        yield owner
    finally:
        while saved:
            name, had, value = saved.pop()
            if had:
                setattr(owner, name, value)
            else:
                delattr(owner, name)


def s19(mod, sandbox):
    pid = _s_free_pids(1)[0]
    problems = []

    def run(platform, mode, answers):
        log, calls = [], []

        def waitid(idtype, ident, options):
            calls.append((idtype, ident, options))
            ans = answers[min(len(calls), len(answers)) - 1]
            if isinstance(ans, type) and issubclass(ans, BaseException):
                raise ans("tf scripted waitid")
            return ans

        consts = [(n, getattr(os, n, v)) for n, v in S_WAITID_CONSTS]
        t0 = time.monotonic()
        with patched(sys, [("platform", platform)]), \
                patched(mod, [("select", _s_fake_select(log, mode))]), \
                _s_inject(os, [("waitid", waitid)] + consts):
            got = mod._sd_wait_exit_unreaped(pid, time.monotonic() + 0.3)
            flags = os.WEXITED | os.WNOWAIT | os.WNOHANG
            p_pid = os.P_PID
        return got, log, calls, time.monotonic() - t0, flags, p_pid

    got, log, calls, took, _f, _p = run("darwin", "esrch", [None])
    if got is not True:
        problems.append("darwin, already exited: returned %r, expected True" % got)
    if "kqueue" not in log or ("kevent", pid) not in log:
        problems.append("darwin: no kqueue EVFILT_PROC registration for the pid (%r)" % log)
    if calls:
        problems.append("darwin: os.waitid was called %r" % calls)
    got, log, calls, took, _f, _p = run("darwin", "never", [None])
    if got is not False or took > 0.3 + 1.0:
        problems.append("darwin, never exits: (%r after %.2fs), expected False at the deadline"
                        % (got, took))
    got, log, calls, took, flags, p_pid = run("linux", None, [None, object()])
    if got is not True:
        problems.append("linux, exits on the 2nd poll: returned %r, expected True" % got)
    if "kqueue" in log:
        problems.append("linux: select.kqueue was used")
    if not calls or any(c != (p_pid, pid, flags) for c in calls):
        problems.append("linux: waitid calls %r, expected (P_PID, pid, WEXITED|WNOWAIT|WNOHANG)"
                        % calls)
    got, log, calls, took, _f, _p = run("linux", None, [None])
    if got is not False or took > 0.3 + 1.0:
        problems.append("linux, never exits: (%r after %.2fs), expected False at the deadline"
                        % (got, took))
    got, log, calls, took, _f, _p = run("linux", None, [ChildProcessError])
    if got is not True:
        problems.append("linux, ChildProcessError: returned %r, expected True" % got)
    return problems, ["sys.platform picks the branch: kqueue on darwin, waitid elsewhere "
                      "(both exercised on this host)"]


S20_GRACE_S = 0.6            # stop_grace of S20's fake child (measured, so not tiny)


def s20(mod, sandbox):
    """M14 / S-L6: wait_ready's poll() reaps a leader that exited early.  A group that
    still has a member (a grandchild) keeps its pgid registered and gets the FULL grace
    between SIGTERM and SIGKILL (the zombie wait would return at once after the reap);
    a group observed empty right after the reap leaves the registry and is never
    signalled again."""
    pid_live, pid_empty = _s_free_pids(2)
    problems = []
    spec = mod.ModelSpec(name="tfs", command=sys.executable, args=("--port", "{port}"), env=(),
                         stop_grace=S20_GRACE_S)
    with _s_fake_world(mod, [pid_live]) as world:
        times = []

        def timed(pgid, sig):
            times.append((int(sig), time.monotonic()))
            return world.rec(pgid, sig)

        with patched(os, [("killpg", timed)]):
            sup = mod.Supervisor()
            child = sup.start(spec)
            world.exited.add(pid_live)          # the leader exited; a grandchild survives
            ready = sup.wait_ready(child, time.monotonic() + 1.0, threading.Event())
            if ready is not False:
                problems.append("survivor: wait_ready returned %r for an exited leader" % ready)
            if child.proc.returncode is None:
                problems.append("control: poll() did not reap the exited leader")
            if child.pgid not in sup.registry:
                problems.append("survivor: a group that answers signal 0 left the registry")
            mark = len(times)
            sup.stop(child)
            ladder = times[mark:]
            term = [t for sig, t in ladder if sig == TERM]
            kill = [t for sig, t in ladder if sig == KILL]
            if not term or not kill:
                problems.append("survivor: the ladder sent %r, expected SIGTERM then SIGKILL"
                                % [sig for sig, _t in ladder])
            elif kill[0] - term[0] < S20_GRACE_S * 0.9:
                problems.append("survivor: SIGKILL %.2fs after SIGTERM, inside stop_grace %.1fs: the "
                                "grandchild got no grace" % (kill[0] - term[0], S20_GRACE_S))
        problems += _s_direct_problems(world)
        sup.registry.discard(child.pgid)
    with _s_fake_world(mod, [pid_empty], script={0: ProcessLookupError}) as world:
        sup = mod.Supervisor()
        child = sup.start(spec)
        world.exited.add(pid_empty)             # the leader exited and the group is empty
        sup.wait_ready(child, time.monotonic() + 1.0, threading.Event())
        if child.pgid in sup.registry:
            problems.append("empty: the pgid of an emptied group is still registered after the reap")
        mark = len(world.rec.calls)
        if sup.stop(child) is not True:
            problems.append("empty: stop() of a group already seen empty did not return True")
        stale = [c for c in world.rec.calls[mark:] if c[1] != 0]
        if stale:
            problems.append("empty: stop() signalled the stale pgid %r" % stale)
        problems += _s_direct_problems(world)
    return problems, ["a reaped leader with a live group: SIGKILL only after stop_grace; an "
                      "emptied group: unregistered at the reap, never signalled again"]


GS_FUNCS = (s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15, s16, s17,
            s18, s19, s20)
GS_BUDGET = frozenset(("S11", "S12", "S13"))


def group_s(suite, fixture_root):
    mod, why = load_module("s")
    for cid, fn in zip(GS_CASES, GS_FUNCS):
        tag = cid.split(" ", 1)[0]
        if mod is None:
            suite.record(GS, cid, [why])
            continue
        absent = missing_names(mod, S_NAMES_BUDGET if tag in GS_BUDGET else S_NAMES_SUP)
        if not absent and tag not in GS_BUDGET:
            lacking = [m for m in S_METHODS if not callable(getattr(mod.Supervisor, m, None))]
            if lacking:
                absent = ["Supervisor has no %s" % ", ".join(lacking)]
        if absent:
            suite.record(GS, cid, absent)
            continue
        sandbox = new_sandbox(fixture_root, "s-" + tag.lower())
        try:
            problems, detail = fn(mod, sandbox)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GS, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group R -- routing: route_request and sniff_model (unit 2, PURE)
# ---------------------------------------------------------------------------
# PURE cases only: strings and bytes in, one value out, milliseconds.  No
# socket, no subprocess, no thread, no clock.  The oracle is the plan's
# "Path handling" and "Model resolution order" rules (H1, D-11) and the
# llm-router sniffer behaviour it copies (canonical forms only, a spelling two
# readers could read two ways is a 400, never a guess; ADR 0015).
#
# The contract this group pins (task-022 implements it; plan Step 5 unit 2,
# Type Definitions).  Every name is module-level in Scripts/gpu-router.py
# unit 2:
#
#   ROUTES: a READ-ONLY mapping (assignment raises TypeError), exactly
#     {"/v1/images/generations": "openai", "/v1/images/edits": "openai",
#      "/sdapi/v1/txt2img": "sdapi", "/sdapi/v1/img2img": "sdapi"}.
#     The kind decides the model source: "openai" may fall back to the body
#     `model`, "sdapi" takes the /m/<model>/ prefix only.  /v1/models and
#     /v1/unload are control routes the handler dispatches BEFORE routing:
#     route_request answers them route_not_found like any other path.
#   Target(NamedTuple): model, upstream_path, via -- via is "prefix" or
#     "body"; upstream_path IS the ROUTES key object (the table constant,
#     never the client's string).
#   RouteError(NamedTuple): code, message -- code is one of route_not_found,
#     method_not_allowed, invalid_request, model_required, model_unknown (all
#     in the closed table _SD_ERROR_STATUS); message names a rule, never a
#     value from the request.
#   SniffError(Exception): a body the sniffer refuses (a 400); its text names
#     a rule, never a value.
#   _SD_SNIFF_LIMIT == 65536: the sniffer reads at most this many leading
#     body bytes.  _SD_SNIFF_PART_CAP == 64: multipart parts scanned at most.
#   _sd_route_head(method, raw_path, models) -> (prefix_model_or_None,
#     upstream_path) | RouteError: every decision that needs no body (the
#     handler calls it before admission), in this order: the path (an exact
#     ROUTES key, bare or after /m/<model>/; any `%`, `//`, `.`/`..` segment,
#     empty model or unknown rest -> route_not_found), the method (exactly
#     "POST", else method_not_allowed; an unknown path stays 404), the query
#     (any `?` -> invalid_request), the prefix model (exact, case-sensitive
#     membership in *models*, never percent-decoded -> model_unknown), and an
#     "sdapi" route without a prefix -> model_required.
#   route_request(method, raw_path, content_type, body, models) -> Target |
#     RouteError, never raises: _sd_route_head, then -- only when there is no
#     prefix -- the module-level sniff_model (looked up at call time, so a
#     test can replace it): SniffError -> invalid_request, None ->
#     model_required, a name not in *models* -> model_unknown.  With a prefix
#     the body is NOT sniffed and its `model` is ignored (D-11).  *models* is
#     any container of configured names (Settings.models); it is never
#     mutated, nor is *body*.
#   sniff_model(content_type, body) -> Optional[str]: None = no model field;
#     SniffError = 400.  application/json (parameters ignored): the whole
#     body must lie within _SD_SNIFF_LIMIT (a longer JSON body is refused,
#     never half-parsed), UTF-8, one JSON object, no repeated key at any
#     depth, no NaN/Infinity, no key that case-folds to "model" unless spelled
#     "model"; model a string of 1-256 characters without control characters.
#     multipart/form-data: exactly one parameter, boundary (a 1-70 character
#     token or quoted); every part carries exactly one canonical
#     `Content-Disposition: form-data; name="..."[; filename="..."]` and at
#     most a Content-Type (a text field: absent, empty or text/plain[;
#     charset=utf-8]); delimiters are CRLF-led (a bare-LF delimiter is
#     refused), the closing delimiter is followed by nothing or one CRLF; at
#     most _SD_SNIFF_PART_CAP parts; a `model` part is a text part, at most
#     one, spelled exactly "model".  Only the first _SD_SNIFF_LIMIT bytes are
#     read: a model part found there is the answer (later bytes are never
#     looked at -- declared), a window that ends before the model part is
#     found is a SniffError, a fully scanned body without one is None.  Any
#     other media type is a SniffError.

GR_CASES = (
    "R1 names, table, constants",
    "R2 route table closed",
    "R3 prefix routes, table const",
    "R4 method 405 vs path 404",
    "R5 image routes: no query",
    "R6 prefix model exact",
    "R7 no percent-decoding",
    "R8 no dot segments, no //",
    "R9 prefix wins over body",
    "R10 prefix: body not sniffed",
    "R11 sdapi needs the prefix",
    "R12 JSON body fallback",
    "R13 JSON refusals",
    "R14 JSON window bound",
    "R15 multipart canonical",
    "R16 multipart refusals",
    "R17 multipart content-type",
    "R18 multipart part cap",
    "R19 multipart window bound",
    "R20 closed codes, head agrees",
)

R_NAMES = ("ROUTES", "Target", "RouteError", "SniffError", "route_request", "sniff_model",
           "_sd_route_head", "_SD_SNIFF_LIMIT", "_SD_SNIFF_PART_CAP", "_SD_ERROR_STATUS")
R_ROUTES = {"/v1/images/generations": "openai", "/v1/images/edits": "openai",
            "/sdapi/v1/txt2img": "sdapi", "/sdapi/v1/img2img": "sdapi"}
R_OPENAI = ("/v1/images/generations", "/v1/images/edits")
R_SDAPI = ("/sdapi/v1/txt2img", "/sdapi/v1/img2img")
R_CODES = {"route_not_found": 404, "method_not_allowed": 405, "invalid_request": 400,
           "model_required": 400, "model_unknown": 404}
R_LIMIT = 64 * 1024
R_PART_CAP = 64
R_MODELS = types.MappingProxyType({"A": "spec-A", "B": "spec-B", "sd-xl.v1~2": "spec-X"})
R_JSON = "application/json"
R_BOUNDARY = "tfR7MA4YWxkTrZu0gW"
R_MULTI = "multipart/form-data; boundary=" + R_BOUNDARY
R_SENTINEL = "tf-r-VALUE-SENTINEL-" + secrets.token_hex(6)


def _r_json(obj):
    return json.dumps(obj).encode("utf-8")


def _r_part(name, value, filename=None, ctype=None, extra=()):
    """One multipart part (headers CRLF CRLF data) in the canonical spelling unless *extra* says otherwise."""
    head = 'Content-Disposition: form-data; name="%s"' % name
    if filename is not None:
        head += '; filename="%s"' % filename
    lines = [head]
    if ctype is not None:
        lines.append("Content-Type: %s" % ctype)
    lines.extend(extra)
    return "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n" + value


def _r_raw_part(head, value):
    return head.encode("latin-1") + b"\r\n\r\n" + value


def _r_multipart(parts, boundary=R_BOUNDARY, preamble=b"", epilogue=b"\r\n"):
    delim = b"--" + boundary.encode("ascii")
    body = preamble + b"\r\n" if preamble else b""
    for part in parts:
        body += delim + b"\r\n" + part + b"\r\n"
    return body + delim + b"--" + epilogue


def _r_route(mod, path, body=b"", ctype=R_JSON, method="POST", models=R_MODELS):
    return mod.route_request(method, path, ctype, body, models)


def _r_target(mod, label, got, model, path, via):
    if not isinstance(got, mod.Target):
        return ["%s: gave %r, expected a Target(%r, %r, %r)" % (label, got, model, path, via)]
    if tuple(got) != (model, path, via):
        return ["%s: Target %r, expected %r" % (label, tuple(got), (model, path, via))]
    return []


def _r_refused(mod, label, got, code):
    if not isinstance(got, mod.RouteError):
        return ["%s: gave %r, expected RouteError %s" % (label, got, code)]
    if got.code != code:
        return ["%s: code %r, expected %r" % (label, got.code, code)]
    if R_SENTINEL in str(got.message):
        return ["%s: the message echoes a request value" % label]
    return []


def _r_sniff(mod, ctype, body):
    """("ok", value) | ("refused", text) | ("raised", exception name)."""
    try:
        return "ok", mod.sniff_model(ctype, body)
    except mod.SniffError as exc:
        return "refused", str(exc)
    except Exception as exc:  # noqa: BLE001 -- anything but SniffError is a defect
        return "raised", "%s: %s" % (type(exc).__name__, str(exc)[:120])


def _r_sniff_is(mod, label, ctype, body, want):
    kind, value = _r_sniff(mod, ctype, body)
    if (kind, value) != ("ok", want):
        return ["%s: sniff_model gave %s %r, expected %r" % (label, kind, value, want)]
    return []


def _r_sniff_refused(mod, label, ctype, body):
    """Problems unless sniff_model raises SniffError (value-free) AND route_request turns
    the same body into 400 invalid_request on an un-prefixed openai route."""
    kind, value = _r_sniff(mod, ctype, body)
    if kind != "refused":
        return ["%s: sniff_model gave %s %r, expected SniffError" % (label, kind, value)]
    if R_SENTINEL in value:
        return ["%s: the SniffError text echoes a body value" % label]
    return _r_refused(mod, label + " (route)", _r_route(mod, "/v1/images/edits", body, ctype),
                      "invalid_request")


def _r_spy(answer):
    calls = []

    def spy(content_type, body):
        calls.append((content_type, len(body)))
        return answer
    return spy, calls


def r1(mod):
    problems = []
    if dict(mod.ROUTES) != R_ROUTES:
        problems.append("ROUTES is %r, expected %r" % (dict(mod.ROUTES), R_ROUTES))
    try:
        mod.ROUTES["/sdcpp/v1/img_gen"] = "openai"
        problems.append("ROUTES accepted an assignment: it must be read-only")
    except TypeError:
        pass
    for cls, want in (("Target", ("model", "upstream_path", "via")), ("RouteError", ("code", "message"))):
        got = tuple(getattr(getattr(mod, cls), "_fields", ()))
        if got != want:
            problems.append("%s fields %r, expected %r" % (cls, got, want))
    if not (isinstance(mod.SniffError, type) and issubclass(mod.SniffError, Exception)):
        problems.append("SniffError must be an Exception subclass")
    if (mod._SD_SNIFF_LIMIT, mod._SD_SNIFF_PART_CAP) != (R_LIMIT, R_PART_CAP):
        problems.append("sniff bounds %r, expected %r"
                        % ((mod._SD_SNIFF_LIMIT, mod._SD_SNIFF_PART_CAP), (R_LIMIT, R_PART_CAP)))
    for code, status in R_CODES.items():
        if mod._SD_ERROR_STATUS.get(code) != status:
            problems.append("closed table: %s -> %r, expected %d" % (code, mod._SD_ERROR_STATUS.get(code), status))
    return problems, ["four routes, two kinds, read-only; 64 KiB / 64 parts"]


def r2(mod):
    problems = []
    others = ("/sdcpp/v1/img_gen", "/v1/models/load", "/sdapi/v1/options", "/sdapi/v1/sd-models",
              "/v1/images/variations", "/", "", "/v1/images/generations/", "/V1/images/generations",
              "/v1/images", "/v1/models", "/v1/unload", "v1/images/generations",
              "http://127.0.0.1/v1/images/generations", "/v1/images/generations#x",
              "/" + R_SENTINEL)
    count = 0
    for path in others:
        for full in ((path, "/m/A" + path) if path.startswith("/") else (path,)):
            count += 1
            problems += _r_refused(mod, "POST %r" % full, _r_route(mod, full, _r_json({"model": "A"})),
                                   "route_not_found")
    return problems, ["%d non-allowlisted paths, bare and prefixed: 404 route_not_found" % count]


def r3(mod):
    problems = []
    for path in R_ROUTES:
        client = "".join(["/m/", "A", path])        # a fresh string object, never the table's
        got = _r_route(mod, client, b"\xff not a body")
        problems += _r_target(mod, "POST %s" % client, got, "A", path, "prefix")
        if isinstance(got, mod.Target):
            key = next(k for k in mod.ROUTES if k == path)
            if got.upstream_path is not key:
                problems.append("%s: upstream_path is not the ROUTES key object (the client string "
                                "leaked into the upstream request line)" % client)
        bare = "".join(["", path])
        got = _r_route(mod, bare, _r_json({"model": "B"}))
        if path in R_OPENAI:
            problems += _r_target(mod, "POST %s (body)" % path, got, "B", path, "body")
            if isinstance(got, mod.Target) and got.upstream_path is not next(k for k in mod.ROUTES if k == path):
                problems.append("%s: body-routed upstream_path is not the ROUTES key object" % path)
    return problems, ["each route under /m/A/ -> prefix Target; upstream path is the table constant"]


def r4(mod):
    problems = []
    for method in ("GET", "PUT", "HEAD", "DELETE", "OPTIONS", "post", "Post"):
        for path in ("/v1/images/generations", "/m/A/sdapi/v1/img2img"):
            problems += _r_refused(mod, "%s %s" % (method, path),
                                   _r_route(mod, path, _r_json({"model": "A"}), method=method),
                                   "method_not_allowed")
        problems += _r_refused(mod, "%s /nope" % method, _r_route(mod, "/nope", method=method),
                               "route_not_found")
        problems += _r_refused(mod, "%s /m/A/v1/models/load" % method,
                               _r_route(mod, "/m/A/v1/models/load", method=method), "route_not_found")
    return problems, ["a known path with a wrong method is 405; an unknown path stays 404"]


def r5(mod):
    problems = []
    for path in ("/v1/images/generations?x=1", "/v1/images/generations?", "/m/A/v1/images/edits?model=B",
                 "/m/A/sdapi/v1/txt2img?token=" + R_SENTINEL, "/v1/images/edits?model=A"):
        problems += _r_refused(mod, "POST %s" % path, _r_route(mod, path, _r_json({"model": "A"})),
                               "invalid_request")
    problems += _r_refused(mod, "POST /nope?x=1", _r_route(mod, "/nope?x=1"), "route_not_found")
    return problems, ["any query on an image route is 400 invalid_request"]


def r6(mod):
    problems = []
    problems += _r_target(mod, "/m/sd-xl.v1~2/", _r_route(mod, "/m/sd-xl.v1~2/v1/images/edits"),
                          "sd-xl.v1~2", "/v1/images/edits", "prefix")
    for name in ("a", "AA", "A.", "nosuch", R_SENTINEL, "sd-xl.v1~"):
        problems += _r_refused(mod, "/m/%s/" % name, _r_route(mod, "/m/%s/v1/images/generations" % name),
                               "model_unknown")
    for path in ("/m/", "/m/A", "/m/A/", "/m//v1/images/generations", "/m"):
        problems += _r_refused(mod, "POST %r" % path, _r_route(mod, path), "route_not_found")
    one = types.MappingProxyType({"only": 1})
    problems += _r_refused(mod, "/m/A/ with A unconfigured",
                           _r_route(mod, "/m/A/v1/images/generations", models=one), "model_unknown")
    return problems, ["prefix model matched exactly and case-sensitively against the configured names"]


def r7(mod):
    problems = []
    for path in ("/m/A/v1%2Fimages/generations", "/m/A%2Fv1/images/generations",
                 "/v1/images%2Fgenerations", "/m/%41/v1/images/generations",
                 "/m/A/v1/images/generations%00", "/m/A/v1/images/%67enerations",
                 "/m/A%2F..%2F/v1/images/generations", "/m/sd-xl.v1%7E2/v1/images/edits"):
        problems += _r_refused(mod, "POST %s" % path, _r_route(mod, path), "route_not_found")
    return problems, ["no percent-decoding anywhere in the path: every %-form is 404"]


def r8(mod):
    problems = []
    dots = types.MappingProxyType({"A": 1, "..": 2, ".": 3})   # both are valid config names
    for path in ("/m/A/../v1/images/generations", "/m/../v1/images/generations",
                 "/m/./v1/images/generations", "/m/A/./v1/images/generations",
                 "//v1/images/generations", "/v1//images/generations", "/m/A//v1/images/generations",
                 "/m/A/v1/images/../images/generations", "/m//A/v1/images/generations",
                 "/m/A/v1/images/generations/.", "/./v1/images/generations"):
        problems += _r_refused(mod, "POST %s" % path, _r_route(mod, path, models=dots), "route_not_found")
    return problems, ["`.` / `..` segments and `//` are 404, even for a model named `..`"]


def r9(mod):
    problems = []
    for body, what in ((_r_json({"model": "B", "prompt": "x"}), "configured B"),
                       (_r_json({"model": "sd-route-key", "prompt": "x"}), "llm-router route key"),
                       (b'{"model":"B","model":"C"}', "repeated key"),
                       (_r_json({"prompt": "x"}), "no model")):
        problems += _r_target(mod, "/m/A/ + body %s" % what,
                              _r_route(mod, "/m/A/v1/images/generations", body), "A",
                              "/v1/images/generations", "prefix")
    multi = _r_multipart([_r_part("model", b"B"), _r_part("prompt", b"x")])
    problems += _r_target(mod, "/m/A/ + multipart model B",
                          _r_route(mod, "/m/A/v1/images/edits", multi, R_MULTI), "A", "/v1/images/edits",
                          "prefix")
    return problems, ["the /m/<model>/ prefix is authoritative; the body model is ignored (D-11)"]


def r10(mod):
    problems = []
    spy, calls = _r_spy("B")
    big = b"\xff" * (R_LIMIT * 2)
    bodies = ((b"--x\nnot multipart", "multipart/form-data"), (b"{\"model\":", R_JSON),
              (big, "text/plain"), (b"", ""), (bytearray(b'{"model":"B"}'), R_JSON))
    with patched(mod, (("sniff_model", spy),)):
        for body, ctype in bodies:
            before = bytes(body)
            got = _r_route(mod, "/m/A/v1/images/edits", body, ctype)
            problems += _r_target(mod, "/m/A/ + %s body" % (ctype or "no"), got, "A", "/v1/images/edits",
                                  "prefix")
            if bytes(body) != before:
                problems.append("route_request modified the body")
        if calls:
            problems.append("sniff_model was called %d time(s) for prefixed requests" % len(calls))
        got = _r_route(mod, "/v1/images/edits", b"{}", R_JSON)
        problems += _r_target(mod, "un-prefixed (sniff seam)", got, "B", "/v1/images/edits", "body")
        if len(calls) != 1:
            problems.append("an un-prefixed request must consult the module-level sniff_model once, "
                            "got %d call(s)" % len(calls))
    return problems, ["with a prefix the body is never sniffed (malformed, huge or binary forwarded)"]


def r11(mod):
    problems = []
    spy, calls = _r_spy("A")
    with patched(mod, (("sniff_model", spy),)):
        for path in R_SDAPI:
            problems += _r_refused(mod, "POST %s (body model A)" % path,
                                   _r_route(mod, path, _r_json({"model": "A"})), "model_required")
            problems += _r_target(mod, "POST /m/B%s" % path, _r_route(mod, "/m/B" + path), "B", path,
                                  "prefix")
        if calls:
            problems.append("an /sdapi/ body was sniffed (%d call(s)); /sdapi takes the prefix only"
                            % len(calls))
    return problems, ["/sdapi/* without a prefix is 400 model_required; its body is never sniffed"]


def r12(mod):
    problems = []
    for ctype in (R_JSON, "application/json; charset=utf-8", "Application/JSON"):
        problems += _r_sniff_is(mod, "json %r" % ctype, ctype, _r_json({"prompt": "p", "model": "A", "n": 1}), "A")
    problems += _r_sniff_is(mod, "json without model", R_JSON, _r_json({"prompt": "p"}), None)
    problems += _r_sniff_is(mod, "json nested model only", R_JSON, _r_json({"x": {"model": "A"}}), None)
    problems += _r_sniff_is(mod, "json unicode model", R_JSON, _r_json({"model": "mé"}), "mé")
    for path in R_OPENAI:
        problems += _r_target(mod, "POST %s body A" % path, _r_route(mod, path, _r_json({"model": "A"})),
                              "A", path, "body")
    problems += _r_refused(mod, "body without model", _r_route(mod, "/v1/images/generations",
                                                               _r_json({"prompt": "p"})), "model_required")
    problems += _r_refused(mod, "body model unknown", _r_route(mod, "/v1/images/generations",
                                                               _r_json({"model": R_SENTINEL})), "model_unknown")
    problems += _r_refused(mod, "body model wrong case", _r_route(mod, "/v1/images/generations",
                                                                  _r_json({"model": "a"})), "model_unknown")
    return problems, ["un-prefixed openai routes take the JSON body model; absent 400, unknown 404"]


def r13(mod):
    problems = []
    sentinel = json.dumps(R_SENTINEL)
    bad = (
        ('{"model":"A","model":"B"}', "top-level repeated model"),
        ('{"model":"A","x":{"k":1,"k":2}}', "nested repeated key"),
        ('{"Model":"A"}', "case-variant key"),
        ('{"model":"A","MODEL":"A"}', "case-variant beside model"),
        ('{"model":1}', "number model"),
        ('{"model":null}', "null model"),
        ('{"model":["A"]}', "list model"),
        ('{"model":""}', "empty model"),
        ('{"model":"%s"}' % ("m" * 257), "model over 256"),
        ('{"model":"A\\u0000"}', "control character"),
        ('["A"]', "array body"),
        ('"A"', "string body"),
        ('{"model":"A"', "truncated JSON"),
        ('{"model":"A","n":NaN}', "NaN"),
        ('{"model":%s,"model":%s}' % (sentinel, sentinel), "repeated sentinel"),
        ('{"model":%s,"Model":%s}' % (sentinel, sentinel), "case-variant sentinel"),
        ("", "empty body"),
    )
    for text, what in bad:
        problems += _r_sniff_refused(mod, "json %s" % what, R_JSON, text.encode("utf-8"))
    problems += _r_sniff_refused(mod, "json invalid UTF-8", R_JSON, b'{"model":"A\xff"}')
    problems += _r_sniff_refused(mod, "json BOM", R_JSON, b'\xef\xbb\xbf{"model":"A"}')
    return problems, ["%d malformed / ambiguous JSON bodies: SniffError, 400 invalid_request" % (len(bad) + 2)]


def r14(mod):
    problems = []
    head = b'{"model":"A","prompt":"'
    tail = b'"}'
    exact = head + b"p" * (R_LIMIT - len(head) - len(tail)) + tail
    problems += _r_sniff_is(mod, "json of exactly 64 KiB", R_JSON, exact, "A")
    over = head + b"p" * (R_LIMIT - len(head) - len(tail) + 1) + tail
    problems += _r_sniff_refused(mod, "json of 64 KiB + 1", R_JSON, over)
    # valid JSON whose first 64 KiB is valid JSON too: only the length rule refuses it
    padded = b'{"model":"A"}' + b" " * R_LIMIT
    problems += _r_sniff_refused(mod, "json padded past 64 KiB", R_JSON, padded)
    huge = bytearray(head + b"p" * (4 * R_LIMIT) + tail)
    before = bytes(huge)
    problems += _r_sniff_refused(mod, "json of 256 KiB", R_JSON, huge)
    if bytes(huge) != before:
        problems.append("sniff_model modified the body")
    return problems, ["a JSON body must lie within the 64 KiB window; a longer one is refused, never half-read"]


def r15(mod):
    problems = []
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
    forms = (
        ("model first", [_r_part("model", b"A"), _r_part("prompt", b"a cat\r\non two lines")]),
        ("model after a file", [_r_part("image", png, filename="in.png", ctype="image/png"),
                                _r_part("model", b"A")]),
        ("text/plain model", [_r_part("model", b"A", ctype="text/plain; charset=utf-8")]),
        ("empty ctype model", [_r_part("model", b"A", ctype="")]),
        ("unicode model", [_r_part("model", "mé".encode("utf-8"))]),
        ("no model", [_r_part("prompt", b"x")]),
    )
    for label, parts in forms:
        want = None if label == "no model" else ("mé" if label == "unicode model" else "A")
        problems += _r_sniff_is(mod, "multipart %s" % label, R_MULTI, _r_multipart(parts), want)
    body = _r_multipart([_r_part("model", b"A")], preamble=b"preamble text")
    problems += _r_sniff_is(mod, "multipart with preamble", R_MULTI, body, "A")
    body = _r_multipart([_r_part("model", b"A")], epilogue=b"")
    problems += _r_sniff_is(mod, "multipart without final CRLF", R_MULTI, body, "A")
    quoted = 'multipart/form-data; boundary="tf quoted:boundary"'
    body = _r_multipart([_r_part("model", b"A")], boundary="tf quoted:boundary")
    problems += _r_sniff_is(mod, "quoted boundary", quoted, body, "A")
    body = _r_multipart([_r_part("prompt", b"p"), _r_part("model", b"B")])
    before = bytes(body)
    problems += _r_target(mod, "POST /v1/images/edits multipart", _r_route(mod, "/v1/images/edits", body, R_MULTI),
                          "B", "/v1/images/edits", "body")
    if bytes(body) != before:
        problems.append("route_request modified a multipart body")
    return problems, ["canonical multipart forms: one text `model` part anywhere in the form"]


def r16(mod):
    problems = []
    sv = R_SENTINEL.encode("ascii")
    cases = (
        ("two model parts", _r_multipart([_r_part("model", b"A"), _r_part("model", b"A")])),
        ("two model parts (sentinel)", _r_multipart([_r_part("model", sv), _r_part("model", sv)])),
        ("Model spelling", _r_multipart([_r_part("Model", b"A")])),
        ("model as a file", _r_multipart([_r_part("model", b"A", filename="m.txt")])),
        ("name*= form", _r_multipart([_r_raw_part("Content-Disposition: form-data; name*=utf-8''model", b"A")])),
        ("unquoted name", _r_multipart([_r_raw_part("Content-Disposition: form-data; name=model", b"A")])),
        ("no space after ;", _r_multipart([_r_raw_part('Content-Disposition: form-data;name="model"', b"A")])),
        ("no disposition", _r_multipart([_r_raw_part("Content-Type: text/plain", b"A")])),
        ("no headers at all", _r_multipart([b"\r\nA"])),
        ("transfer encoding", _r_multipart([_r_part("model", b"QQ==",
                                                    extra=("Content-Transfer-Encoding: base64",))])),
        ("repeated header", _r_multipart([_r_part("model", b"A", ctype="text/plain",
                                                  extra=("Content-Type: text/plain",))])),
        ("image ctype on model", _r_multipart([_r_part("model", b"A", ctype="image/png")])),
        ("empty model", _r_multipart([_r_part("prompt", b"p"), _r_part("model", b"")])),
        ("control char model", _r_multipart([_r_part("model", b"A\x00")])),
        ("model over 256", _r_multipart([_r_part("model", b"m" * 257)])),
        ("invalid UTF-8 model", _r_multipart([_r_part("model", b"A\xff")])),
        ("bare-LF delimiter", _r_multipart([_r_part("prompt", b"x\n--" + R_BOUNDARY.encode() + b"\r\n"),
                                            _r_part("model", b"A")])),
        ("missing closing", _r_multipart([_r_part("model", b"A")])[:-(len(R_BOUNDARY) + 6)]),
        ("epilogue", _r_multipart([_r_part("model", b"A")], epilogue=b"\r\ntrailing")),
        ("delimiter + junk", _r_multipart([_r_part("model", b"A")]).replace(
            b"--" + R_BOUNDARY.encode() + b"\r\n", b"--" + R_BOUNDARY.encode() + b"x\r\n", 1)),
        ("no delimiter", b"just bytes"),
        ("empty body", b""),
    )
    for label, body in cases:
        problems += _r_sniff_refused(mod, "multipart %s" % label, R_MULTI, body)
    return problems, ["%d ambiguous / non-canonical multipart forms: SniffError, never a guess" % len(cases)]


def r17(mod):
    problems = []
    body = _r_multipart([_r_part("model", b"A")])
    for ctype in ("multipart/form-data", "multipart/form-data; boundary=", 'multipart/form-data; boundary=""',
                  "multipart/form-data; boundary=%s; charset=utf-8" % R_BOUNDARY,
                  "multipart/form-data; charset=utf-8",
                  "multipart/form-data; boundary=%s; boundary=%s" % (R_BOUNDARY, R_BOUNDARY),
                  "multipart/form-data; boundary=" + "b" * 71,
                  "multipart/form-data; boundary=other",
                  "multipart/form-data; boundary=a\x01b",
                  "multipart/mixed; boundary=" + R_BOUNDARY,
                  "text/plain", "application/x-www-form-urlencoded", ""):
        problems += _r_sniff_refused(mod, "content-type %r" % ctype, ctype, body)
    problems += _r_sniff_is(mod, "media type case-insensitive", "Multipart/Form-Data; boundary=" + R_BOUNDARY,
                            body, "A")
    return problems, ["multipart takes exactly one parameter, a valid boundary; other media types refused"]


def r18(mod):
    problems = []
    filler = [_r_part("f%d" % i, b"v") for i in range(R_PART_CAP - 1)]
    at_cap = _r_multipart(filler + [_r_part("model", b"A")])
    problems += _r_sniff_is(mod, "64 parts", R_MULTI, at_cap, "A")
    over = _r_multipart(filler + [_r_part("f-extra", b"v"), _r_part("model", b"A")])
    problems += _r_sniff_refused(mod, "65 parts", R_MULTI, over)
    early = _r_multipart([_r_part("model", b"A")] + filler + [_r_part("f-extra", b"v")])
    problems += _r_sniff_refused(mod, "65 parts, model first", R_MULTI, early)
    return problems, ["at most 64 parts are scanned; the 65th is a 400 wherever the model sits"]


def r19(mod):
    problems = []
    blob = b"\xab" * (3 * R_LIMIT)
    first = _r_multipart([_r_part("model", b"A"), _r_part("image", blob, filename="i.png", ctype="image/png")])
    problems += _r_sniff_is(mod, "model before a 192 KiB file", R_MULTI, first, "A")
    beyond = _r_multipart([_r_part("model", b"A"), _r_part("image", blob, filename="i.png", ctype="image/png"),
                           _r_part("model", b"B"), b"garbage without headers\n--" + R_BOUNDARY.encode()])
    before = bytes(beyond)
    problems += _r_sniff_is(mod, "bytes after the window never read", R_MULTI, beyond, "A")
    if bytes(beyond) != before:
        problems.append("sniff_model modified the body")
    late = _r_multipart([_r_part("image", blob, filename="i.png", ctype="image/png"), _r_part("model", b"A")])
    problems += _r_sniff_refused(mod, "model after the window", R_MULTI, late)
    edge = _r_multipart([_r_part("image", b"\xab" * (R_LIMIT - 400), filename="i.png", ctype="image/png"),
                         _r_part("model", b"A")])
    if len(edge) > R_LIMIT:
        problems.append("fixture: the edge body is %d bytes, over the window" % len(edge))
    problems += _r_sniff_is(mod, "model just inside the window", R_MULTI, edge, "A")
    return problems, ["only the first 64 KiB is read: a model there wins, one beyond it is a 400"]


def r20(mod):
    problems, seen = [], set()
    paths = ("/v1/images/generations", "/v1/images/edits", "/sdapi/v1/txt2img", "/m/A/sdapi/v1/img2img",
             "/m/B/v1/images/edits", "/m/nosuch/v1/images/edits", "/m/A/v1/images/edits?x",
             "/v1/models", "/m//v1/images/edits", "/m/A/v1%2Fimages/edits", "/nope", "/m/A/../x",
             "/v1/images/edits?" + R_SENTINEL, "/m/" + R_SENTINEL + "/v1/images/edits")
    bodies = ((_r_json({"model": "B"}), R_JSON), (_r_json({"model": R_SENTINEL}), R_JSON),
              (_r_json({"prompt": "x"}), R_JSON), (b"\x00\xff", R_JSON), (b"", R_MULTI),
              (_r_multipart([_r_part("model", b"B")]), R_MULTI), (b"x", "image/png"))
    models = {"A": 1, "B": 2}
    snapshot = dict(models)
    for method in ("POST", "GET"):
        for path in paths:
            head = mod._sd_route_head(method, path, models)
            for body, ctype in bodies:
                label = "%s %s [%s]" % (method, path, ctype)
                try:
                    got = mod.route_request(method, path, ctype, body, models)
                except Exception as exc:  # noqa: BLE001 -- route_request must never raise
                    problems.append("%s: route_request raised %s" % (label, type(exc).__name__))
                    continue
                if isinstance(got, mod.RouteError):
                    seen.add(got.code)
                    if got.code not in R_CODES or mod._SD_ERROR_STATUS.get(got.code) != R_CODES[got.code]:
                        problems.append("%s: code %r is not a routing code of the closed table" % (label, got.code))
                    if R_SENTINEL in str(got.message):
                        problems.append("%s: the message echoes a request value" % label)
                elif isinstance(got, mod.Target):
                    if got.via not in ("prefix", "body") or got.model not in models:
                        problems.append("%s: implausible Target %r" % (label, tuple(got)))
                else:
                    problems.append("%s: returned %r, neither Target nor RouteError" % (label, got))
                    continue
                # the pre-body split must agree with the full decision
                if isinstance(head, mod.RouteError):
                    if not isinstance(got, mod.RouteError) or got.code != head.code:
                        problems.append("%s: _sd_route_head said %s, route_request %r" % (label, head.code, got))
                elif not isinstance(head, tuple) or len(head) != 2:
                    problems.append("%s: _sd_route_head returned %r" % (label, head))
                elif head[0] is not None:
                    if not isinstance(got, mod.Target) or tuple(got) != (head[0], head[1], "prefix"):
                        problems.append("%s: head %r but route_request %r" % (label, head, got))
                elif isinstance(got, mod.Target) and (got.via != "body" or got.upstream_path != head[1]):
                    problems.append("%s: head %r but route_request %r" % (label, head, tuple(got)))
    if models != snapshot:
        problems.append("route_request mutated the models mapping")
    if seen != set(R_CODES):
        problems.append("routing codes reached %r, expected all of %r" % (sorted(seen), sorted(R_CODES)))
    return problems, ["never raises; every refusal a closed-table routing code, value-free; "
                      "_sd_route_head agrees"]


GR_FUNCS = (r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, r13, r14, r15, r16, r17, r18, r19, r20)


def group_r(suite, fixture_root):
    mod, why = load_module("r")
    absent = missing_names(mod, R_NAMES) if mod is not None else [why]
    for cid, fn in zip(GR_CASES, GR_FUNCS):
        if absent:
            suite.record(GR, cid, absent)
            continue
        try:
            problems, detail = fn(mod)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GR, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group D -- decisions: the pure FIFO scheduler decision (unit 3, no sockets)
# ---------------------------------------------------------------------------
# PURE cases only: plain data in, one Decision out, milliseconds.  No socket,
# no subprocess, no thread, no clock.  The state-machine table of the plan
# (Section 5) is the oracle; RESIDENT has ONE definition (D-17).
#
# The contract this group pins (task-016 implements it; plan Step 4 unit 3,
# Type Definitions, D-17, M2, final M2).  Every name is module-level in
# Scripts/gpu-router.py unit 3:
#
#   ModelState(enum.Enum): exactly STOPPED, STARTING, READY, STOPPING, DEAD.
#     DISABLED is NOT a member: it is a per-model boolean flag.
#   ModelView(NamedTuple): state, group_alive=False, disabled=False,
#     restart_at=0.0 -- one model's snapshot as unit 6 copies it under the
#     lock.  group_alive matters only with DEAD (a zombie group); restart_at is
#     the earliest `now` at which an automatic restart may start (backoff, D-8;
#     0.0 = immediate).
#   TicketView(NamedTuple): kind ("request" | "unload"), model (None = an
#     unload's default target), force, cancelled (bool), attempts (sends so
#     far).  Plain data: the runtime Ticket with its Events lives in unit 6.
#   Decision(NamedTuple): action, model, code=None.  action is one of
#     _SD_ACTIONS == frozenset({"run", "swap", "start", "stop", "fail",
#     "idle"}), closed.  code is None except: "fail" carries the closed-table
#     error code (model_disabled, unload_failed, model_unknown); "stop"
#     carries _SD_NOT_RESIDENT (== "not_resident") when the unload target is
#     not resident (answer 200 {"unloaded": false} at once, clear the flag and
#     the Budget, no ladder) and None when the ladder must run on `model`.
#   _sd_is_resident(view) -> bool: state in {STARTING, READY, STOPPING} or
#     (state is DEAD and group_alive).  The disabled flag never changes it.
#   _sd_resident_model(states) -> Optional[str]: the name of the resident
#     model of a {name: ModelView} mapping, or None.
#   _sd_pick(force_slots, queue) -> (source, index): ("force", 0) when a
#     force unload waits (force jumps the FIFO, which is left untouched);
#     otherwise ("fifo", i) where queue[i] is the first ticket that is not
#     cancelled and queue[:i] are cancelled tickets to drop; (None,
#     len(queue)) when nothing live waits.  Sequences of TicketView.
#   _sd_requeue_head(queue, ticket) -> tuple: the SAME ticket first, the
#     rest in their order (a retry re-queues at the head, never the tail).
#   _sd_may_retry(attempts, retries, disabled) -> bool: not disabled and
#     attempts <= retries (retries=1 allows a second send, never a third).
#   decide(head, states, resident, now) -> Decision, where head is a
#     TicketView or None, states a {name: ModelView} mapping (never mutated),
#     resident the resident model's name or None, now a float (the ONLY time
#     source: decide reads no clock).  Rules, in order:
#       no head -> idle(None).  A cancelled head -> idle(None): it is never
#       decided on, never swaps or starts anything.
#       unload: the target is head.model, or the resident model AT THE HEAD
#       when head.model is None; an unknown explicit target -> fail
#       model_unknown; target resident -> stop(target) (the ladder, also
#       for a zombie); otherwise stop(target or None, "not_resident").  A
#       force unload is decided exactly like a queued one.
#       request: an unknown target -> fail model_unknown; a disabled target
#       -> fail model_disabled (no swap, no start); any zombie group (DEAD
#       with group_alive) -> fail unload_failed (M2), whatever the target;
#       target == resident: READY -> run(target), STARTING or STOPPING ->
#       idle(target) (a transition is in progress: never a second child);
#       another model resident -> swap(target); nothing resident -> start
#       (target) when now >= restart_at, else idle(target) (backoff).

GD_CASES = (
    "D1 names, fields, enum",
    "D2 action vocabulary closed",
    "D3 resident definition D-17",
    "D4 empty queue -> idle",
    "D5 READY head -> run",
    "D6 nothing resident -> start",
    "D7 swap on head change",
    "D8 strict FIFO across models",
    "D9 cancelled head: no swap",
    "D10 cancelled skipped by pick",
    "D11 disabled fails, no swap",
    "D12 disabled never starts",
    "D13 force jumps the queue",
    "D14 unload resident -> ladder",
    "D15 unload non-resident: once",
    "D16 unload default = head res.",
    "D17 zombie fails requests",
    "D18 zombie clears on ESRCH",
    "D19 transition in progress",
    "D20 retry: same ticket at head",
    "D21 retry allowance",
    "D22 backoff by now, no clock",
)

D_NAMES = ("ModelState", "ModelView", "TicketView", "Decision", "decide", "_SD_ACTIONS",
           "_SD_NOT_RESIDENT", "_sd_is_resident", "_sd_resident_model", "_sd_pick",
           "_sd_requeue_head", "_sd_may_retry")
D_ACTIONS = frozenset(("run", "swap", "start", "stop", "fail", "idle"))
D_STATES = ("STOPPED", "STARTING", "READY", "STOPPING", "DEAD")
D_FAIL_CODES = frozenset(("model_disabled", "unload_failed", "model_unknown"))
D_NOT_RESIDENT = "not_resident"


def _d_view(mod, state, group_alive=False, disabled=False, restart_at=0.0):
    return mod.ModelView(getattr(mod.ModelState, state), group_alive, disabled, restart_at)


def _d_states(mod, **views):
    """{name: ModelView} as a read-only mapping: decide must never write to it.
    Each keyword is a state name or a (state, group_alive, disabled, restart_at) tuple."""
    out = {}
    for name, spec in views.items():
        out[name] = _d_view(mod, spec) if isinstance(spec, str) else _d_view(mod, *spec)
    return types.MappingProxyType(out)


def _d_req(mod, model, cancelled=False, attempts=0):
    return mod.TicketView("request", model, False, cancelled, attempts)


def _d_unload(mod, model=None, force=False, cancelled=False):
    return mod.TicketView("unload", model, force, cancelled, 0)


def _d_decide(mod, head, states, now=0.0, resident="auto"):
    """decide() with the resident model derived by the module's own helper unless given."""
    if resident == "auto":
        resident = mod._sd_resident_model(states)
    return mod.decide(head, states, resident, now)


def _d_expect(label, got, action, model, code=None):
    """Problems unless *got* is (action, model, code)."""
    want = (action, model, code)
    have = (getattr(got, "action", got), getattr(got, "model", None), getattr(got, "code", None))
    if have != want:
        return ["%s: decide gave %r, expected %r" % (label, have, want)]
    return []


def d1(mod):
    problems = []
    members = tuple(m.name for m in mod.ModelState)
    if members != D_STATES:
        problems.append("ModelState members %r, expected %r (DISABLED is a flag, not a state)"
                        % (members, D_STATES))
    fields = (("TicketView", ("kind", "model", "force", "cancelled", "attempts")),
              ("Decision", ("action", "model", "code")),
              ("ModelView", ("state", "group_alive", "disabled", "restart_at")))
    for cls, want in fields:
        got = tuple(getattr(getattr(mod, cls), "_fields", ()))
        if got != want:
            problems.append("%s fields %r, expected %r" % (cls, got, want))
    if not problems:
        if mod.Decision("idle", None).code is not None:
            problems.append("Decision.code must default to None")
        view = mod.ModelView(mod.ModelState.STOPPED)
        if (view.group_alive, view.disabled, view.restart_at) != (False, False, 0.0):
            problems.append("ModelView defaults %r, expected (False, False, 0.0)" % (view[1:],))
    if frozenset(mod._SD_ACTIONS) != D_ACTIONS:
        problems.append("_SD_ACTIONS %r, expected %r" % (sorted(mod._SD_ACTIONS), sorted(D_ACTIONS)))
    if mod._SD_NOT_RESIDENT != D_NOT_RESIDENT:
        problems.append("_SD_NOT_RESIDENT is %r, expected %r" % (mod._SD_NOT_RESIDENT, D_NOT_RESIDENT))
    return problems, ["five states, no DISABLED member; the three plain-data shapes"]


def d2(mod):
    """Every decision over a grid of inputs is in the closed vocabulary and obeys the
    structural invariants of the state table."""
    problems, count = [], 0
    heads = (_d_req(mod, "A"), _d_req(mod, "B"), _d_req(mod, "B", cancelled=True),
             _d_unload(mod, "A"), _d_unload(mod, "B"), _d_unload(mod), _d_unload(mod, "A", force=True))
    for state in D_STATES:
        for alive in (False, True):
            for disabled in (False, True):
                states = _d_states(mod, A=(state, alive, disabled, 0.0), B="STOPPED")
                resident = mod._sd_resident_model(states)
                zombie = state == "DEAD" and alive
                for head in heads:
                    count += 1
                    got = mod.decide(head, states, resident, 0.0)
                    where = "A=%s alive=%s disabled=%s head=%s/%s" % (
                        state, alive, disabled, head.kind, head.model)
                    if not isinstance(got, mod.Decision):
                        problems.append("%s: not a Decision (%r)" % (where, got))
                        continue
                    if got.action not in D_ACTIONS:
                        problems.append("%s: action %r outside the vocabulary" % (where, got.action))
                    if got.action == "fail" and got.code not in D_FAIL_CODES:
                        problems.append("%s: fail with code %r" % (where, got.code))
                    if got.action == "stop" and got.code not in (None, D_NOT_RESIDENT):
                        problems.append("%s: stop with code %r" % (where, got.code))
                    if got.action not in ("fail", "stop") and got.code is not None:
                        problems.append("%s: %s carries code %r" % (where, got.action, got.code))
                    if got.action == "start" and (resident is not None or zombie):
                        problems.append("%s: start while %r is resident" % (where, resident))
                    if got.action == "swap" and (resident is None or resident == got.model or zombie):
                        problems.append("%s: swap to %r with resident %r" % (where, got.model, resident))
                    if got.action == "run" and (got.model != resident
                                                or states[resident].state.name != "READY"):
                        problems.append("%s: run of %r, resident %r not READY" % (where, got.model, resident))
                    if head.cancelled and got.action != "idle":
                        problems.append("%s: a cancelled head gave %r" % (where, got.action))
                    if len(problems) > 6:
                        return problems, []
    return problems, ["%d decisions over every state x flag x head: closed vocabulary, "
                      "start/swap/run only where the table allows" % count]


def d3(mod):
    problems = []
    want = {("STOPPED", False): False, ("STOPPED", True): False,
            ("STARTING", False): True, ("STARTING", True): True,
            ("READY", False): True, ("READY", True): True,
            ("STOPPING", False): True, ("STOPPING", True): True,
            ("DEAD", False): False, ("DEAD", True): True}
    for (state, alive), resident in sorted(want.items()):
        for disabled in (False, True):
            got = mod._sd_is_resident(_d_view(mod, state, alive, disabled))
            if bool(got) is not resident:
                problems.append("%s group_alive=%s disabled=%s: resident %r, expected %r"
                                % (state, alive, disabled, got, resident))
    cases = ((dict(A="READY", B="STOPPED"), "A"), (dict(A="STOPPED", B="STARTING"), "B"),
             (dict(A=("DEAD", True), B="STOPPED"), "A"), (dict(A=("DEAD", False), B="STOPPED"), None),
             (dict(A=("STOPPED", False, True), B=("DEAD", False, True)), None),
             (dict(A="STOPPED", B="STOPPING"), "B"))
    for views, want_name in cases:
        got = mod._sd_resident_model(_d_states(mod, **views))
        if got != want_name:
            problems.append("_sd_resident_model(%r) = %r, expected %r" % (views, got, want_name))
    return problems, ["STARTING/READY/STOPPING or DEAD with a live group; the disabled flag "
                      "and DEAD-without-group are not resident"]


def d4(mod):
    problems = _d_expect("no head, A READY", _d_decide(mod, None, _d_states(mod, A="READY")),
                         "idle", None)
    problems += _d_expect("no head, nothing resident",
                          _d_decide(mod, None, _d_states(mod, A="STOPPED")), "idle", None)
    return problems, ["an empty queue is idle"]


def d5(mod):
    states = _d_states(mod, A="READY", B="STOPPED")
    problems = _d_expect("A READY, head A", _d_decide(mod, _d_req(mod, "A"), states), "run", "A")
    problems += _d_expect("retried head A", _d_decide(mod, _d_req(mod, "A", attempts=1), states),
                          "run", "A")
    return problems, ["the resident READY model runs its own head"]


def d6(mod):
    problems = _d_expect("all STOPPED, head B",
                         _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A="STOPPED", B="STOPPED")),
                         "start", "B")
    problems += _d_expect("A DEAD no group, head B",
                          _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A=("DEAD", False), B="STOPPED")),
                          "start", "B")
    return problems, ["nothing resident: start the head's model"]


def d7(mod):
    problems = _d_expect("A READY, head B",
                         _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A="READY", B="STOPPED")),
                         "swap", "B")
    problems += _d_expect("B READY, head A",
                          _d_decide(mod, _d_req(mod, "A"), _d_states(mod, A="STOPPED", B="READY")),
                          "swap", "A")
    return problems, ["a head for another model swaps to it"]


def _d_simulate(mod, order):
    """Drive _sd_pick + decide over a FIFO of request heads, applying each decision to a
    plain state dict; (served models, decision trail)."""
    states = {"A": _d_view(mod, "STOPPED"), "B": _d_view(mod, "STOPPED")}
    queue = [_d_req(mod, name) for name in order]
    served, trail = [], []
    for _step in range(4 * len(order) + 4):
        source, index = mod._sd_pick((), queue)
        del queue[:index]
        if source is None:
            break
        head = queue[0]
        resident = mod._sd_resident_model(types.MappingProxyType(states))
        got = mod.decide(head, types.MappingProxyType(dict(states)), resident, 0.0)
        trail.append((got.action, got.model))
        if got.action == "start":
            states[got.model] = _d_view(mod, "READY")
        elif got.action == "swap":
            states[resident] = _d_view(mod, "STOPPED")
            states[got.model] = _d_view(mod, "READY")
        elif got.action == "run":
            served.append(head.model)
            queue.pop(0)
        else:
            break
    return served, trail


def d8(mod):
    order = ("A", "B", "B", "A", "B")
    served, trail = _d_simulate(mod, order)
    want = [("start", "A"), ("run", "A"), ("swap", "B"), ("run", "B"), ("run", "B"),
            ("swap", "A"), ("run", "A"), ("swap", "B"), ("run", "B")]
    problems = []
    if tuple(served) != order:
        problems.append("served %r, expected strict FIFO %r" % (served, order))
    if trail != want:
        problems.append("decision trail %r, expected %r" % (trail, want))
    return problems, ["A B B A B is served in order: start, run, swap, run, run, swap, run, swap, run"]


def d9(mod):
    states = _d_states(mod, A="READY", B="STOPPED")
    problems = _d_expect("A READY, cancelled head B",
                         _d_decide(mod, _d_req(mod, "B", cancelled=True), states), "idle", None)
    problems += _d_expect("nothing resident, cancelled head A",
                          _d_decide(mod, _d_req(mod, "A", cancelled=True),
                                    _d_states(mod, A="STOPPED", B="STOPPED")), "idle", None)
    problems += _d_expect("A READY, cancelled unload A",
                          _d_decide(mod, _d_unload(mod, "A", cancelled=True), states), "idle", None)
    return problems, ["a cancelled head never swaps, starts or stops anything"]


def d10(mod):
    problems = []
    queue = (_d_req(mod, "B", cancelled=True), _d_req(mod, "B", cancelled=True), _d_req(mod, "A"),
             _d_req(mod, "B", cancelled=True))
    got = mod._sd_pick((), queue)
    if tuple(got) != ("fifo", 2):
        problems.append("_sd_pick over two cancelled then A: %r, expected ('fifo', 2)" % (got,))
    else:
        problems += _d_expect("picked head", _d_decide(mod, queue[2], _d_states(mod, A="READY", B="STOPPED")),
                              "run", "A")
    got = mod._sd_pick((), queue[:2])
    if tuple(got) != (None, 2):
        problems.append("_sd_pick over cancelled only: %r, expected (None, 2)" % (got,))
    got = mod._sd_pick((), ())
    if tuple(got) != (None, 0):
        problems.append("_sd_pick over nothing: %r, expected (None, 0)" % (got,))
    return problems, ["cancelled tickets ahead of a live one are dropped, never decided on; "
                      "the resident A runs with no swap to B"]


def d11(mod):
    states = _d_states(mod, A="READY", B=("STOPPED", False, True))
    problems = _d_expect("A READY, head B disabled", _d_decide(mod, _d_req(mod, "B"), states),
                         "fail", "B", "model_disabled")
    problems += _d_expect("control: head A runs", _d_decide(mod, _d_req(mod, "A"), states), "run", "A")
    return problems, ["a disabled model's head fails model_disabled; the resident A is not swapped out"]


def d12(mod):
    problems = _d_expect("nothing resident, B disabled",
                         _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A="STOPPED", B=("STOPPED", False, True))),
                         "fail", "B", "model_disabled")
    problems += _d_expect("B DEAD no group, disabled",
                          _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A="STOPPED", B=("DEAD", False, True))),
                          "fail", "B", "model_disabled")
    return problems, ["a disabled model is never started (only an unload clears the flag)"]


def d13(mod):
    problems = []
    force = _d_unload(mod, "A", force=True)
    queue = (_d_req(mod, "B"), _d_req(mod, "A"))
    got = mod._sd_pick((force,), queue)
    if tuple(got) != ("force", 0):
        problems.append("_sd_pick with a force slot: %r, expected ('force', 0)" % (got,))
    got = mod._sd_pick((), queue)
    if tuple(got) != ("fifo", 0):
        problems.append("control: _sd_pick without a force slot: %r, expected ('fifo', 0)" % (got,))
    states = _d_states(mod, A="READY", B="STOPPED")
    problems += _d_expect("force unload A, A READY", _d_decide(mod, force, states), "stop", "A")
    problems += _d_expect("force unload A, A STARTING",
                          _d_decide(mod, force, _d_states(mod, A="STARTING", B="STOPPED")), "stop", "A")
    return problems, ["a waiting force unload is picked before the FIFO head and stops the resident model"]


def d14(mod):
    problems = _d_expect("unload A, A READY",
                         _d_decide(mod, _d_unload(mod, "A"), _d_states(mod, A="READY", B="STOPPED")),
                         "stop", "A")
    problems += _d_expect("unload A, A READY and disabled",
                          _d_decide(mod, _d_unload(mod, "A"),
                                    _d_states(mod, A=("READY", False, True), B="STOPPED")),
                          "stop", "A")
    return problems, ["a queued unload of the resident model runs the ladder (code None)"]


def d15(mod):
    problems = []
    rows = ((dict(A="READY", B="STOPPED"), "B"),
            (dict(A="READY", B=("STOPPED", False, True)), "B"),
            (dict(A="STOPPED", B=("DEAD", False, True)), "B"),
            (dict(A="STOPPED", B="STOPPED"), "A"))
    for views, target in rows:
        for force in (False, True):
            label = "unload %s force=%s over %r" % (target, force, views)
            problems += _d_expect(label, _d_decide(mod, _d_unload(mod, target, force=force),
                                                   _d_states(mod, **views)),
                                  "stop", target, D_NOT_RESIDENT)
    problems += _d_expect("unload of an unknown model",
                          _d_decide(mod, _d_unload(mod, "Z"), _d_states(mod, A="READY")),
                          "fail", "Z", "model_unknown")
    return problems, ["an unload whose target is not resident (STOPPED, DEAD without a group, "
                      "disabled) completes at once with not_resident; the resident A is untouched"]


def d16(mod):
    head = _d_unload(mod)
    problems = _d_expect("default unload, A resident",
                         _d_decide(mod, head, _d_states(mod, A="READY", B="STOPPED")), "stop", "A")
    problems += _d_expect("same ticket, B resident at the head",
                          _d_decide(mod, head, _d_states(mod, A="STOPPED", B="READY")), "stop", "B")
    problems += _d_expect("same ticket, nothing resident",
                          _d_decide(mod, head, _d_states(mod, A="STOPPED", B="STOPPED")),
                          "stop", None, D_NOT_RESIDENT)
    return problems, ["the default target is the model resident when the unload reaches the head"]


def d17(mod):
    states = _d_states(mod, A=("DEAD", True), B="STOPPED")
    problems = _d_expect("zombie A, head B", _d_decide(mod, _d_req(mod, "B"), states),
                         "fail", "B", "unload_failed")
    problems += _d_expect("zombie A, head A", _d_decide(mod, _d_req(mod, "A"), states),
                          "fail", "A", "unload_failed")
    problems += _d_expect("zombie A, unload A re-runs the ladder",
                          _d_decide(mod, _d_unload(mod, "A"), states), "stop", "A")
    problems += _d_expect("zombie A, default unload", _d_decide(mod, _d_unload(mod), states), "stop", "A")
    return problems, ["while a zombie group holds the GPU every request head fails unload_failed "
                      "(no swap, no start); an unload re-runs the ladder"]


def d18(mod):
    states = _d_states(mod, A=("DEAD", False), B="STOPPED")
    problems = _d_expect("A gone (ESRCH), head B", _d_decide(mod, _d_req(mod, "B"), states), "start", "B")
    problems += _d_expect("A gone (ESRCH), head A", _d_decide(mod, _d_req(mod, "A"), states), "start", "A")
    problems += _d_expect("A re-probed STOPPED, head B",
                          _d_decide(mod, _d_req(mod, "B"), _d_states(mod, A="STOPPED", B="STOPPED")),
                          "start", "B")
    return problems, ["once the zombie group is confirmed gone the queue proceeds normally"]


def d19(mod):
    problems = []
    for state in ("STARTING", "STOPPING"):
        states = _d_states(mod, A=state, B="STOPPED")
        problems += _d_expect("A %s, head A" % state, _d_decide(mod, _d_req(mod, "A"), states), "idle", "A")
        problems += _d_expect("A %s, head B" % state, _d_decide(mod, _d_req(mod, "B"), states), "swap", "B")
    return problems, ["a head for a model mid-transition waits (never a second child); "
                      "another model's head swaps"]


def d20(mod):
    problems = []
    retried = _d_req(mod, "A", attempts=1)
    rest = [_d_req(mod, "B"), _d_req(mod, "A")]
    snapshot = list(rest)
    got = mod._sd_requeue_head(rest, retried)
    if not isinstance(got, tuple) or len(got) != 3:
        return ["_sd_requeue_head gave %r, expected a 3-tuple" % (got,)], []
    if got[0] is not retried:
        problems.append("the retried ticket is not at the head (same object)")
    if list(got[1:]) != snapshot:
        problems.append("the rest of the queue moved: %r" % (got[1:],))
    if rest != snapshot:
        problems.append("_sd_requeue_head mutated its input")
    source, index = mod._sd_pick((), got)
    if (source, index) != ("fifo", 0) or got[index] is not retried:
        problems.append("_sd_pick after the re-queue did not pick the retried ticket")
    problems += _d_expect("retried head, A READY again",
                          _d_decide(mod, got[0], _d_states(mod, A="READY", B="STOPPED")), "run", "A")
    problems += _d_expect("retried head, A crashed (DEAD, no group)",
                          _d_decide(mod, got[0], _d_states(mod, A=("DEAD", False), B="STOPPED")),
                          "start", "A")
    return problems, ["a retry re-queues the SAME ticket at the head; B behind it does not swap in first"]


def d21(mod):
    problems = []
    rows = (((1, 1, False), True), ((2, 1, False), False), ((1, 0, False), False),
            ((0, 0, False), True), ((3, 3, False), True), ((4, 3, False), False),
            ((1, 1, True), False), ((1, 3, True), False))
    for args, want in rows:
        got = mod._sd_may_retry(*args)
        if bool(got) is not want:
            problems.append("_sd_may_retry(attempts=%d, retries=%d, disabled=%s) = %r, expected %r"
                            % (args + (got, want)))
    return problems, ["retries=1 allows a second send, never a third; never after DISABLED"]


def d22(mod):
    problems = []
    states = _d_states(mod, A=("DEAD", False, False, 100.0), B="STOPPED")
    head = _d_req(mod, "A", attempts=1)

    def boom(*_a, **_k):
        raise AssertionError("decide read a clock")

    # The module's own `time` name is replaced (never the shared time module).
    fake_time = types.SimpleNamespace(monotonic=boom, time=boom, perf_counter=boom, sleep=boom)
    with patched(mod, [("_sd_now", boom), ("time", fake_time)]):
        try:
            early = mod.decide(head, states, None, 99.5)
            due = mod.decide(head, states, None, 100.0)
            late = mod.decide(head, states, None, 250.0)
        except AssertionError as exc:
            return [str(exc)], []
    problems += _d_expect("now 99.5 < restart_at 100", early, "idle", "A")
    problems += _d_expect("now 100 == restart_at", due, "start", "A")
    problems += _d_expect("now 250", late, "start", "A")
    before = dict(states)
    mod.decide(head, states, None, 99.5)
    if dict(states) != before or head != _d_req(mod, "A", attempts=1):
        problems.append("decide changed its inputs")
    return problems, ["backoff is decided by the injected now alone (restart_at); decide reads "
                      "no clock and changes nothing"]


GD_FUNCS = (d1, d2, d3, d4, d5, d6, d7, d8, d9, d10, d11, d12, d13, d14, d15, d16, d17,
            d18, d19, d20, d21, d22)


def group_d(suite, fixture_root):
    mod, why = load_module("d")
    absent = missing_names(mod, D_NAMES) if mod is not None else [why]
    for cid, fn in zip(GD_CASES, GD_FUNCS):
        if absent:
            suite.record(GD, cid, absent)
            continue
        try:
            problems, detail = fn(mod)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GD, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Groups Q-sched and T -- the scheduler runtime (units 5 and 6), no HTTP
# ---------------------------------------------------------------------------
# In-process against a privately loaded module and the REAL stub child: the
# case builds Settings through parse_config, constructs a Scheduler on it
# (real Supervisor unless the case needs a recording or misbehaving subclass)
# and drives submit_request / submit_unload / cancel / try_admit directly,
# acting as the handler.  Waits are Events / predicates with 10 s ceilings;
# nothing is asserted after a sleep.  Backoff is patched small (Q_FAST).
# Fakes are used only where the plan says so: a Supervisor subclass that
# records or reports a surviving group (zombie, STARTING-before-spawn, ladder
# order, a restart that fails), and an `_sd_send` WRAPPER around the real one
# (flip the stub's control file after a crash, drop a fully read answer, log
# the send order).
#
# The contract these groups pin (task-018 unit 5, task-019 unit 6 data side,
# task-020 the scheduler thread; plan Section 5 "Lock / thread model",
# "Cancellation, force, abort and deadline semantics", the state table,
# FR-5, FR-10 M9, D-3, D-12, D-17, H6, H7, M1-M4).  Every name is module-level
# in Scripts/gpu-router.py and is looked up AT CALL TIME (tests patch it):
#
# Unit 5
#   UpstreamError(kind): an Exception whose `.kind` is one of "connect", "eof",
#     "aborted", "timeout", "too_large", "failed"; constructed with the kind as
#     its only positional argument (T9 raises UpstreamError("eof") itself).
#   _sd_send(port, method, path, headers, body, deadline, abort_evt,
#            on_socket=None) -> (status, headers, body): the ONE upstream send
#     site.  The scheduler calls it through the module global `_sd_send`
#     with the first SEVEN arguments POSITIONAL (body is args[4]) and
#     on_socket by keyword: on_socket(sock) is called once with the connected
#     socket so the runtime can store it on the ticket for a force/client
#     shutdown(SHUT_RDWR).  `deadline` is absolute on the _sd_now() scale
#     (dispatch + spec.request_timeout, H7); abort_evt is a threading.Event.
#     The answer is buffered whole under settings.response_limit; status is
#     the child's int status, headers a tuple of (name, value) pairs to relay
#     (Content-Type), body bytes.  Every failure is an UpstreamError.
#
# Unit 6
#   _SD_SCHED_TICK_S: the scheduler's wait tick, 0 < tick <= 0.2 (QS1 pins).
#   TicketResult(NamedTuple): status (int), code (closed-table code or None),
#     headers (tuple of (name, value)), body (bytes or None).  A relayed child
#     answer has code None and the child's status/body byte-exact; a refusal
#     has the closed-table code and status (Q_STATUS below); an unload's 200
#     has code None and body = JSON {"unloaded": bool, "model": name | null}.
#   Ticket: kind ("request" | "unload"), model, force, attempts (SENDS so far;
#     the first send makes it 1, FR-10 M9), body (bytes; None once dropped),
#     method, path, headers, reservation (the one passed to submit_request,
#     None for an unload), done (threading.Event), cancelled
#     (threading.Event), abort_reason (None | "force" | "client" |
#     "shutdown" | "timeout", written under the lock BEFORE the upstream
#     socket is shut down), result (TicketResult or None).
#     finish(result) -> bool: set-once; the first call stores result and sets
#     done (True), every later call changes nothing and returns False.
#   Scheduler(settings, supervisor): ONE threading.Condition `cond` guards
#     everything below; `supervisor` (the Supervisor driven by the scheduler
#     thread only); `queue` (collections.deque of queued Tickets); `running`
#     (the ticket popped by the scheduler, from its pop until it is finished
#     or dropped -- this includes the start/swap wait before its send);
#     `force_slots` (list); `admitted` (int, queued + running IMAGE tickets);
#     `budgets` ({model: Budget}, every configured model); `thread` (the ONE
#     scheduler thread, set by start()).
#     start(): starts the scheduler thread.
#     states() -> {model: ModelView}: a snapshot taken under the lock.
#       restart_at is set from _sd_backoff_s after a crash and reset to 0.0
#       by an unload (an unload's first start is immediate, D-8).
#     try_admit() -> reservation | None: atomic under the lock; None once
#       `admitted` == settings.max_connections (H2).  release(reservation):
#       idempotent per reservation.  An unload never takes a reservation.
#     submit_request(model, method, path, headers, body, reservation) ->
#       Ticket: appends to `queue` and notifies; never admits by itself.
#     submit_unload(model | None, force) -> Ticket (always one with `done`):
#       non-force: ALWAYS queued (FIFO), resolved by decide() at the head
#       (default target = the resident model AT THE HEAD; a non-resident
#       target answers {"unloaded": false, "model": <target>} and clears that
#       model's disabled flag, Budget and restart_at).  force with a RESIDENT
#       target (model None = the resident one): an unload Ticket in
#       `force_slots`; if the running ticket is for that model its
#       abort_reason becomes "force", its `cancelled` is set and its upstream
#       socket is shut down; the scheduler finishes the force ticket with
#       {"unloaded": true} only after the group is confirmed gone (500
#       unload_failed otherwise); the aborted ticket gets 503 unload_forced
#       and is NOT a crash (no Budget.record, no retry, attempts unchanged).
#       force with a NON-resident target: executed by submit_unload itself
#       under `cond` in the caller's thread (D-17) -- the running request is
#       untouched, the target's disabled flag, Budget and restart_at are
#       cleared, nothing enters force_slots or the queue, and the Ticket is
#       returned ALREADY finished (finish() called after the lock section)
#       with {"unloaded": false, "model": <target>}.  After shutdown() both
#       submit_* return a Ticket already finished 503 shutting_down.
#     cancel(ticket): sets ticket.cancelled.  A QUEUED ticket is removed from
#       `queue`, its body set to None, its reservation released and it is
#       finished with result None -- one locked section, done before cancel()
#       returns.  A popped ticket (`running`) gets abort_reason "client"; the
#       scheduler drops it (result None, body None, reservation released by
#       the scheduler, never by the caller):
#         * not yet sent (its model STARTING or a swap in progress): dropped
#           WITHOUT a send once the wait ends; the start is NOT aborted and
#           the started model stays READY and resident (final M1).  The
#           launch `stop` Event is set only by a force for that model or by
#           shutdown, never by a client cancel;
#         * sent: the upstream socket is shut down and the child is KILLED
#           (stop ladder, group confirmed gone) before the next ticket is
#           served (D-12); not a crash, no retry, no Budget charge.
#     shutdown(): idempotent and bounded.  The popped ticket is finished 503
#       shutting_down (a start in progress is stopped, never sent), every
#       still-queued ticket 503 queue_closed; a ladder in progress is
#       FINISHED, never abandoned; then the thread is joined and
#       supervisor.kill_all() runs, so no child group survives shutdown().
#   The scheduler thread executes decide() (unit 3).  Crash = an
#     UpstreamError from _sd_send with abort_reason None, or the child exiting
#     mid-request: DEAD (group_alive True until ESRCH), ladder, ONE
#     Budget.record under the lock; re-queue the SAME ticket at the HEAD while
#     _sd_may_retry(attempts, spec.retries, disabled) (retries=1 => at most 2
#     sends), else 502 model_crashed -- also when that crash tripped the
#     budget.  request_timeout (from dispatch) => abort_reason "timeout",
#     ladder, 504 upstream_timeout, ONE Budget.record, never retried.  A
#     start failure of a RETRY restart => 502 model_start_failed (504
#     model_start_timeout), attempts unchanged, ONE Budget.record; if that
#     record trips the budget the ticket and every later ticket for the model
#     get 503 model_disabled (M3).  A FIRST start failure answers
#     model_start_failed or, when it tripped the budget, model_disabled.  A
#     ladder whose group survives (Supervisor.stop False) leaves the model
#     DEAD with group_alive True (a zombie): every request head fails 500
#     unload_failed, and each head pop re-probes the group with
#     _signal_group(pgid, 0): gone => the zombie clears (state STOPPED,
#     group_alive False, pgid out of the registry) and the queue proceeds
#     (M2).  The STOPPED -> STARTING write is in the same locked section as
#     the pop, BEFORE the lock is released for Supervisor.launch / start
#     (final M3, round-4 M1): no lock is held while spawning.
#
# Codes and statuses used here (the closed table, plan Section 3):
Q_STATUS = {"unload_forced": 503, "model_crashed": 502, "model_start_failed": 502,
            "model_start_timeout": 504, "model_disabled": 503, "unload_failed": 500,
            "upstream_timeout": 504, "shutting_down": 503, "queue_closed": 503}

GQS_CASES = (
    "QS1 one in flight, FIFO",
    "QS2 queued unload waits",
    "QS3 force resident aborts",
    "QS4 force B leaves A running",
    "QS5 budget clear/record lock",
    "QS6 force clears DISABLED",
    "QS7 cancel queued / starting",
    "QS8 cancel running kills",
    "QS9 zombie fails, ESRCH clears",
    "QS10 force while STARTING",
    "QS11 shutdown while STARTING",
    "QS12 shutdown during a swap",
    "QS13 single-GPU invariant",
    "QS14 STARTING before spawn",
    "QS15 admission race",
    "QS16 repeated unloads coalesce",
)

GT_CASES = (
    "T1 retries=0: one send, 502",
    "T2 retries=1: crash, re-send",
    "T3 retries=2: three sends",
    "T4 crash in the retry",
    "T5 request_timeout -> 504",
    "T6 repeated timeout disables",
    "T7 poison prompt, all clients",
    "T8 5 crashes, unload clears",
    "T9 no half answer replayed",
    "T10 aborts are not crashes",
    "T11 retry stays at the head",
    "T12 start fails in the retry",
    "T13 Popen ValueError -> DEAD",
)

Q_NAMES = ("Scheduler", "Ticket", "TicketResult", "UpstreamError", "_sd_send", "_SD_SCHED_TICK_S",
           "Supervisor", "Budget", "ModelView", "ModelState", "parse_config", "_sd_is_resident",
           "_sd_resident_model", "_SD_BACKOFF_S", "_SD_BACKOFF_JITTER", "_SD_RESTART_BUDGET",
           "_SD_KILL_STEP_S", "_sd_now")
Q_METHODS = ("start", "shutdown", "states", "try_admit", "release", "submit_request",
             "submit_unload", "cancel")
Q_PATH = "/v1/images/generations"
Q_HEADERS = (("Content-Type", "application/json"),)
Q_CAP = 8
Q_GRACE_S = 1.0              # stop_grace of every stub model unless a case sets one
Q_START_TIMEOUT_S = 10.0
Q_CLOSE_CEILING_S = 30.0     # shutdown() must have returned within this
Q_FAST = (("_SD_BACKOFF_S", (0.05,)), ("_SD_BACKOFF_JITTER", 0.0))
Q_POISON = "TF-POISON-" + secrets.token_hex(4)


def _q_sha(body):
    return hashlib.sha256(body).hexdigest()


def _q_body(tag, extra=""):
    """A unique JSON image body (its SHA identifies it in the stub's events)."""
    return json.dumps({"model": "tf", "prompt": "tf-%s-%s%s" % (tag, secrets.token_hex(4), extra)},
                      sort_keys=True).encode("utf-8")


def _q_entry(sandbox, name, *flags, **keys):
    keys.setdefault("stop_grace", Q_GRACE_S)
    keys.setdefault("start_timeout", Q_START_TIMEOUT_S)
    return stub_model(sandbox, name, *flags, **keys)


class _QWorld:
    """One Scheduler under test; the case acts as its handler."""

    def __init__(self, sched):
        self.sched = sched
        self.problems = []

    def request(self, model, body, path=Q_PATH):
        res = self.sched.try_admit()
        if res is None:
            raise AssertionError("try_admit() refused below the cap")
        return self.sched.submit_request(model, "POST", path, Q_HEADERS, body, res)

    def release(self, *tickets):
        """What the handler does after it wrote a completed answer."""
        for ticket in tickets:
            self.sched.release(ticket.reservation)

    def states(self):
        return self.sched.states()

    def state(self, model):
        return self.sched.states()[model].state.name

    def admitted(self):
        with self.sched.cond:
            return self.sched.admitted

    def crashes(self, model):
        with self.sched.cond:
            return len(self.sched.budgets[model].crashes)

    def wait_state(self, model, state, timeout=WAIT_CEILING_S):
        return wait_until(lambda: self.state(model) == state, timeout, tick=0.01)


def _q_close(sched, ceiling=Q_CLOSE_CEILING_S):
    """Problems unless sched.shutdown() returns (without raising) within *ceiling*."""
    errors = []

    def target():
        try:
            sched.shutdown()
        except Exception as exc:  # noqa: BLE001 -- reported as a problem
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=target, name="tf-sd-shutdown", daemon=True)
    thread.start()
    thread.join(ceiling)
    if thread.is_alive():
        return ["shutdown() did not return within %.0fs" % ceiling]
    return ["shutdown() raised %s" % errors[0]] if errors else []


@contextlib.contextmanager
def _q_world(mod, models, cap=Q_CAP, supervisor=None, patches=()):
    """A started Scheduler over *models* ({name: entry}); shut down (bounded) on exit,
    its shutdown problems in world.problems.  Q_FAST and *patches* apply throughout."""
    settings = dataclasses.replace(mod.parse_config(_l_raw(base_config(models))),
                                   max_connections=cap)
    with patched(mod, list(Q_FAST) + list(patches)):
        sched = mod.Scheduler(settings, supervisor if supervisor is not None else mod.Supervisor())
        world = _QWorld(sched)
        sched.start()
        try:
            yield world
        finally:
            world.problems += _q_close(sched)


def _q_live(fn):
    """A stub case: *names* (stubs that must have started) are reaped after, as are the
    *optional* ones that recorded a pid."""
    def run(mod, sandbox):
        names, optional = [], []
        try:
            problems, detail = fn(mod, sandbox, names, optional)
        finally:
            leftovers = reap_stubs(sandbox, names)
            for name in optional:
                alive = [pid for pid in read_pids(sandbox, name) if not pid_dead(pid)]
                if alive:
                    kill_pids(alive)
                    leftovers.append("stub %r: pid(s) %r outlived the case" % (name, alive))
        return problems + leftovers, detail
    run.__name__ = fn.__name__
    return run


def _q_done(label, ticket, timeout=WAIT_CEILING_S):
    if ticket.done.wait(timeout):
        return []
    return ["%s: the ticket was not finished within %.0fs" % (label, timeout)]


def _q_result(ticket):
    result = ticket.result
    return (getattr(result, "status", None), getattr(result, "code", None)) if result is not None else None


def _q_code(label, ticket, code, timeout=WAIT_CEILING_S):
    problems = _q_done(label, ticket, timeout)
    if problems:
        return problems
    got = _q_result(ticket)
    if got != (Q_STATUS[code], code):
        return ["%s: result (status, code) %r, expected %r" % (label, got, (Q_STATUS[code], code))]
    return []


def _q_ok(label, ticket, body, timeout=WAIT_CEILING_S):
    """Problems unless *ticket* relayed the stub's 200 answer for exactly *body*."""
    problems = _q_done(label, ticket, timeout)
    if problems:
        return problems
    result = ticket.result
    if _q_result(ticket) != (200, None):
        return ["%s: result (status, code) %r, expected (200, None)" % (label, _q_result(ticket))]
    try:
        sha = json.loads(result.body.decode("utf-8")).get("tf_request_sha256")
    except (AttributeError, ValueError):
        return ["%s: the relayed body is not the stub's JSON answer" % label]
    if sha != _q_sha(body):
        return ["%s: the stub answered a different body (sha mismatch)" % label]
    return []


def _q_unloaded(label, ticket, unloaded, model, timeout=WAIT_CEILING_S):
    problems = _q_done(label, ticket, timeout)
    if problems:
        return problems
    if _q_result(ticket) != (200, None):
        return ["%s: unload result %r, expected (200, None)" % (label, _q_result(ticket))]
    try:
        doc = json.loads(ticket.result.body.decode("utf-8"))
    except (AttributeError, ValueError):
        return ["%s: the unload body is not JSON" % label]
    if doc != {"unloaded": unloaded, "model": model}:
        return ["%s: unload body %r, expected %r" % (label, doc, {"unloaded": unloaded, "model": model})]
    return []


def _q_sends(sandbox, name, body=None):
    """The stub's request events for model *name* (only *body*'s when given)."""
    events = events_named(sandbox, "request", name)
    if body is None:
        return events
    sha = _q_sha(body)
    return [ev for ev in events if ev.get("sha256") == sha]


def _q_alive(sandbox, name):
    return [pid for pid in read_pids(sandbox, name) if pid_alive(pid)]


def _q_control(path, word):
    write_file(path, word + "\n")


def _q_flip_send(mod, control, log=None):
    """A wrapper around the module's real _sd_send: logs each body's SHA and, when the
    send fails, rewrites the stub's control file to `ok` (the next send succeeds)."""
    real = mod._sd_send

    def send(*args, **kwargs):
        if log is not None:
            log.append(_q_sha(args[4]))
        try:
            return real(*args, **kwargs)
        except mod.UpstreamError:
            _q_control(control, "ok")
            raise
    return send


@_q_live
def qs1(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    tick = getattr(mod, "_SD_SCHED_TICK_S", None)
    if not (isinstance(tick, (int, float)) and 0 < tick <= 0.2):
        problems.append("_SD_SCHED_TICK_S is %r, expected 0 < tick <= 0.2" % (tick,))
    bodies = [_q_body("qs1-%d" % i) for i in range(4)]
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--answer-delay", "0.2")}) as w:
        tickets = [w.request("A", body) for body in bodies]
        for i, (ticket, body) in enumerate(zip(tickets, bodies)):
            problems += _q_ok("request %d" % i, ticket, body)
        w.release(*tickets)
        sends = _q_sends(sandbox, "A")
        if [ev.get("sha256") for ev in sends] != [_q_sha(b) for b in bodies]:
            problems.append("the stub saw %d sends not in submission order" % len(sends))
        over = sorted(set(ev.get("inflight") for ev in sends) - {1})
        if over:
            problems.append("the stub saw %r requests in flight at once, expected 1" % over)
        if len(read_starts(sandbox, "A")) != 1:
            problems.append("A started %d times, expected once" % len(read_starts(sandbox, "A")))
        if w.admitted() != 0:
            problems.append("admitted %d after every release, expected 0" % w.admitted())
    return problems + w.problems, ["4 requests: one in flight at the stub, served in FIFO order, "
                                   "one start"]


@_q_live
def qs2(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    body = _q_body("qs2")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--answer-delay", "0.6")}) as w:
        r1 = w.request("A", body)
        if wait_events(sandbox, "request", 1, "A") is None:
            return problems + ["the request never reached the stub"], []
        unload = w.sched.submit_unload(None, False)
        if unload.done.is_set():
            problems.append("the queued unload finished while the request was still running")
        problems += _q_done("unload", unload)
        request_first = r1.done.is_set()
        alive = _q_alive(sandbox, "A")
        problems += _q_unloaded("unload", unload, True, "A")
        problems += _q_ok("request", r1, body)
        w.release(r1)
        if not request_first:
            problems.append("the unload finished before the request queued ahead of it")
        if alive:
            problems.append("unload answered while pid(s) %r were alive (SC-2)" % alive)
        if w.state("A") != "STOPPED":
            problems.append("A is %s after the unload, expected STOPPED" % w.state("A"))
    return problems + w.problems, ["the unload waited for the earlier request and answered "
                                   "only after the group was gone"]


@_q_live
def qs3(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    body = _q_body("qs3")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--keep-generating")}) as w:
        r1 = w.request("A", body)
        if wait_events(sandbox, "generating", 1, "A") is None:
            return problems + ["the request never reached the stub"], []
        t0 = time.monotonic()
        force = w.sched.submit_unload(None, True)
        problems += _q_done("force", force)
        took = time.monotonic() - t0
        alive = _q_alive(sandbox, "A")
        problems += _q_unloaded("force", force, True, "A")
        problems += _q_code("aborted request", r1, "unload_forced")
        bound = Q_GRACE_S + mod._SD_KILL_STEP_S + S_SLACK_S
        if took > bound:
            problems.append("the force took %.2fs, bound %.2fs" % (took, bound))
        if alive:
            problems.append("force answered while pid(s) %r were alive" % alive)
        if r1.abort_reason != "force":
            problems.append("abort_reason %r, expected 'force'" % (r1.abort_reason,))
        if r1.attempts != 1:
            problems.append("attempts %r after a force abort, expected 1" % (r1.attempts,))
        if w.crashes("A") != 0:
            problems.append("a force abort charged %d crash(es)" % w.crashes("A"))
        if len(_q_sends(sandbox, "A", body)) != 1:
            problems.append("the aborted request was re-sent")
        view = w.states()["A"]
        if view.state.name != "STOPPED" or view.disabled:
            problems.append("A is %s disabled=%s, expected STOPPED and enabled"
                            % (view.state.name, view.disabled))
        w.release(r1)
    return problems + w.problems, ["503 unload_forced for the running request, 200 for the force "
                                   "after the group was gone in %.2fs; no crash, no retry" % took]


@_q_live
def qs4(mod, sandbox, names, optional):
    names.extend(("A", "B"))
    problems = []
    models = {"A": _q_entry(sandbox, "A", "--hang-after-accept", request_timeout=30.0),
              "B": _q_entry(sandbox, "B", "--crash-on-start")}
    body_a, body_b = _q_body("qs4-a"), _q_body("qs4-b")
    with _q_world(mod, models, patches=(("_SD_RESTART_BUDGET", 1),)) as w:
        rb = w.request("B", body_b)
        problems += _q_done("B request", rb)
        if _q_result(rb) not in ((502, "model_start_failed"), (503, "model_disabled")):
            problems.append("B start failure answered %r" % (_q_result(rb),))
        w.release(rb)
        if not w.states()["B"].disabled:
            return problems + ["precondition: B is not disabled after its budget tripped"], []
        ra = w.request("A", body_a)
        if wait_events(sandbox, "request", 1, "A") is None:
            return problems + ["A's request never reached the stub"], []
        force = w.sched.submit_unload("B", True)
        immediate = force.done.is_set()
        problems += _q_unloaded("force B", force, False, "B", timeout=0.0)
        if not immediate:
            problems.append("submit_unload(B, force) returned an unfinished ticket")
        with w.sched.cond:
            running, reason, slots = w.sched.running, ra.abort_reason, len(w.sched.force_slots)
        if running is not ra or reason is not None or ra.done.is_set():
            problems.append("A's running request was touched (running=%r abort_reason=%r)"
                            % (running is ra, reason))
        if slots:
            problems.append("the non-resident force left %d entry(ies) in force_slots" % slots)
        if w.states()["B"].disabled or w.crashes("B"):
            problems.append("B still disabled=%s crashes=%d" % (w.states()["B"].disabled, w.crashes("B")))
        if not _q_alive(sandbox, "A"):
            problems.append("A's child died from a force aimed at B")
        cleanup = w.sched.submit_unload("A", True)
        problems += _q_unloaded("force A", cleanup, True, "A")
        problems += _q_code("A request", ra, "unload_forced")
        w.release(ra)
    return problems + w.problems, ["while A was blocked in _sd_send, force B answered "
                                   "{unloaded: false} at once and cleared B's flag and budget"]


@_q_live
def qs5(mod, sandbox, names, optional):
    problems = []
    with _q_world(mod, {"A": _q_entry(sandbox, "A")}) as w:
        sched = w.sched
        out = []
        with sched.cond:
            thread = threading.Thread(target=lambda: out.append(sched.submit_unload("A", True)),
                                      name="tf-qs5-force", daemon=True)
            thread.start()
            thread.join(0.3)
            blocked = thread.is_alive()
        thread.join(WAIT_CEILING_S)
        if not blocked:
            problems.append("submit_unload(force) on a non-resident model did not take the "
                            "scheduler lock")
        if not out:
            return problems + ["submit_unload(force) did not return"], []
        problems += _q_unloaded("force under the lock", out[0], False, "A", timeout=0.0)
        budget, base, errors, records = sched.budgets["A"], mod._sd_now(), [], 400

        def recorder():
            try:
                for i in range(records):
                    with sched.cond:
                        budget.record(base + i * 0.001)
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

        def clearer():
            try:
                for _ in range(60):
                    ticket = sched.submit_unload("A", True)
                    if not ticket.done.is_set():
                        errors.append("unfinished force ticket")
                        return
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

        workers = [threading.Thread(target=recorder, daemon=True),
                   threading.Thread(target=clearer, daemon=True)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(WAIT_CEILING_S)
        if any(worker.is_alive() for worker in workers):
            return problems + ["the record/clear workers did not finish"], []
        if errors:
            problems.append("concurrent record/clear raised %r" % sorted(set(errors)))
        with sched.cond:
            left = list(budget.crashes)
        if left != sorted(left) or len(left) > records:
            problems.append("the budget holds %d crash times out of order" % len(left))
        final = sched.submit_unload("A", True)
        problems += _q_unloaded("final force", final, False, "A", timeout=0.0)
        if w.crashes("A") != 0 or w.states()["A"].disabled:
            problems.append("the final force left %d crash(es)" % w.crashes("A"))
        if read_starts(sandbox, "A"):
            problems.append("a non-resident force started the model")
    return problems + w.problems, ["submit_unload(force) waits for the lock; %d records against "
                                   "60 clears stay consistent" % records]


@_q_live
def qs6(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    entry = _q_entry(sandbox, "A", "--crash-after", "0", retries=0)
    with _q_world(mod, {"A": entry}, patches=(("_SD_RESTART_BUDGET", 2),)) as w:
        for i in (1, 2):
            ticket = w.request("A", _q_body("qs6-%d" % i))
            problems += _q_code("crash %d" % i, ticket, "model_crashed")
            w.release(ticket)
        if not w.states()["A"].disabled:
            return problems + ["precondition: A is not disabled after 2 crashes (budget 2)"], []
        starts, sends = len(read_starts(sandbox, "A")), len(_q_sends(sandbox, "A"))
        refused = w.request("A", _q_body("qs6-3"))
        problems += _q_code("while disabled", refused, "model_disabled")
        w.release(refused)
        if (len(read_starts(sandbox, "A")), len(_q_sends(sandbox, "A"))) != (starts, sends):
            problems.append("a disabled model was started or sent to")
        force = w.sched.submit_unload("A", True)
        problems += _q_unloaded("force A", force, False, "A", timeout=0.0)
        view = w.states()["A"]
        if view.disabled or w.crashes("A") or view.restart_at != 0.0:
            problems.append("after the force: disabled=%s crashes=%d restart_at=%r"
                            % (view.disabled, w.crashes("A"), view.restart_at))
        again = w.request("A", _q_body("qs6-4"))
        problems += _q_code("after the force", again, "model_crashed")
        w.release(again)
        if len(read_starts(sandbox, "A")) != starts + 1:
            problems.append("the re-enabled model was not started again")
    return problems + w.problems, ["2 crashes disabled A (model_disabled, no start); a force "
                                   "unload cleared flag, budget and backoff"]


@_q_live
def qs7(mod, sandbox, names, optional):
    names.extend(("A", "C"))
    optional.append("B")
    problems = []
    models = {"A": _q_entry(sandbox, "A", "--answer-delay", "0.6"), "B": _q_entry(sandbox, "B"),
              "C": _q_entry(sandbox, "C", "--start-delay", "1.0")}
    b1, b2, b3, bc = (_q_body("qs7-%d" % i) for i in range(4))
    with _q_world(mod, models) as w:
        r1 = w.request("A", b1)
        if wait_events(sandbox, "request", 1, "A") is None:
            return problems + ["the first request never reached the stub"], []
        r2, r3 = w.request("B", b2), w.request("A", b3)
        w.sched.cancel(r2)
        with w.sched.cond:
            queued = r2 in w.sched.queue
            admitted = w.sched.admitted
        if queued or r2.body is not None or not r2.done.is_set() or r2.result is not None:
            problems.append("cancel of a queued ticket: queued=%s body dropped=%s done=%s"
                            % (queued, r2.body is None, r2.done.is_set()))
        if admitted != 2:
            problems.append("admitted %d after cancelling one of 3, expected 2" % admitted)
        problems += _q_ok("r1", r1, b1) + _q_ok("r3", r3, b3)
        w.release(r1, r3)
        if read_starts(sandbox, "B"):
            problems.append("the cancelled ticket swapped B in")
        if len(read_starts(sandbox, "A")) != 1:
            problems.append("A was restarted around the cancelled ticket")
        rc = w.request("C", bc)
        if not w.wait_state("C", "STARTING"):
            return problems + ["C never became STARTING"], []
        w.sched.cancel(rc)
        if not w.wait_state("C", "READY"):
            problems.append("a client cancel aborted C's start (C is %s)" % w.state("C"))
        problems += _q_done("cancelled while STARTING", rc)
        if rc.result is not None or rc.body is not None:
            problems.append("the dropped ticket kept a result or its body")
        if not wait_until(lambda: w.admitted() == 0):
            problems.append("admitted %d after the drop, expected 0 (the scheduler releases)"
                            % w.admitted())
        if _q_sends(sandbox, "C"):
            problems.append("the ticket cancelled while STARTING was sent")
        if w.state("C") != "READY" or not _q_alive(sandbox, "C"):
            problems.append("the started C was not kept (state %s)" % w.state("C"))
    return problems + w.problems, ["a queued cancel drops the body and slot at once, no swap; a "
                                   "cancel while STARTING drops without a send, C stays READY"]


@_q_live
def qs8(mod, sandbox, names, optional):
    names.append("A")
    problems, trail = [], []
    control = os.path.join(sandbox, "control")
    _q_control(control, "keep-generating")

    class Rec(mod.Supervisor):
        def launch(self, spec, deadline, stop):
            trail.append(("launch",))
            return super().launch(spec, deadline, stop)

        def stop(self, child):
            ok = super().stop(child)
            trail.append(("stop", child.pid, ok))
            return ok

    b1, b2 = _q_body("qs8-1"), _q_body("qs8-2")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--control-file", control)}, supervisor=Rec()) as w:
        r1 = w.request("A", b1)
        found = wait_events(sandbox, "generating", 1, "A")
        if found is None:
            return problems + ["the request never reached the stub"], []
        pid1 = found[0]["pid"]
        _q_control(control, "ok")
        w.sched.cancel(r1)
        r2 = w.request("A", b2)
        problems += _q_done("cancelled running", r1)
        problems += _q_ok("next request", r2, b2)
        w.release(r2)
        if r1.result is not None or r1.abort_reason != "client":
            problems.append("the client-aborted ticket: result %r abort_reason %r"
                            % (_q_result(r1), r1.abort_reason))
        stops = [i for i, row in enumerate(trail) if row[0] == "stop" and row[1] == pid1]
        launches = [i for i, row in enumerate(trail) if row[0] == "launch"]
        if not stops or trail[stops[0]][2] is not True:
            problems.append("the running child %d was not stopped (trail %r)" % (pid1, trail))
        elif len(launches) < 2 or launches[1] < stops[0]:
            problems.append("the next child was launched before the aborted one was gone")
        if pid_alive(pid1):
            problems.append("the client-aborted child %d is alive" % pid1)
        if len(_q_sends(sandbox, "A", b1)) != 1 or w.crashes("A"):
            problems.append("a client abort was retried or charged as a crash")
        if not wait_until(lambda: w.admitted() == 0):
            problems.append("admitted %d after the drop and the release" % w.admitted())
    return problems + w.problems, ["a client abort of a running request killed the child (group "
                                   "gone) before the next ticket started; no crash (D-12)"]


@_q_live
def qs9(mod, sandbox, names, optional):
    names.extend(("A", "B"))
    problems, held = [], []

    class Zombie(mod.Supervisor):
        def stop(self, child):
            if not held:
                held.append(child)  # the group "survives": nothing is signalled
                return False
            return super().stop(child)

    sup = Zombie()
    b0, b1, b2, b3 = (_q_body("qs9-%d" % i) for i in range(4))
    with _q_world(mod, {"A": _q_entry(sandbox, "A"), "B": _q_entry(sandbox, "B")}, supervisor=sup) as w:
        r0 = w.request("A", b0)
        problems += _q_ok("first", r0, b0)
        w.release(r0)
        unload = w.sched.submit_unload(None, False)
        problems += _q_code("unload with a surviving group", unload, "unload_failed")
        view = w.states()["A"]
        if view.state.name != "DEAD" or not view.group_alive:
            return problems + ["A is %s group_alive=%s, expected a DEAD zombie"
                               % (view.state.name, view.group_alive)], []
        r1, r2 = w.request("B", b1), w.request("A", b2)
        problems += _q_code("B behind the zombie", r1, "unload_failed")
        problems += _q_code("A behind the zombie", r2, "unload_failed")
        w.release(r1, r2)
        if read_starts(sandbox, "B") or len(read_starts(sandbox, "A")) != 1:
            problems.append("a model was started while the zombie held the GPU")
        child = held[0]
        try:
            os.killpg(child.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.proc.wait(KILL_TIMEOUT_S)
        r3 = w.request("B", b3)
        problems += _q_ok("after ESRCH", r3, b3)
        w.release(r3)
        if w.states()["A"].group_alive or child.pgid in sup.registry:
            problems.append("the zombie was not cleared after its group was gone")
    return problems + w.problems, ["while the group survived every request failed "
                                   "unload_failed without a start; ESRCH cleared it"]


@_q_live
def qs10(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    body = _q_body("qs10")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--start-delay", "1.5")}) as w:
        r1 = w.request("A", body)
        if not (w.wait_state("A", "STARTING") and wait_until(lambda: read_pids(sandbox, "A"))):
            return problems + ["A never became STARTING with a spawned child"], []
        force = w.sched.submit_unload("A", True)
        problems += _q_code("request while STARTING", r1, "unload_forced")
        problems += _q_unloaded("force while STARTING", force, True, "A")
        alive = _q_alive(sandbox, "A")
        w.release(r1)
        if alive:
            problems.append("force answered while pid(s) %r were alive" % alive)
        if _q_sends(sandbox, "A") or w.crashes("A"):
            problems.append("the waiting request was sent or the force counted as a crash")
        if w.state("A") != "STOPPED":
            problems.append("A is %s after the force, expected STOPPED" % w.state("A"))
    return problems + w.problems, ["a force during STARTING fails the waiter unload_forced without "
                                   "a send and stops the starting child"]


@_q_live
def qs11(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--start-delay", "2.0")}) as w:
        r1 = w.request("A", _q_body("qs11-1"))
        if not (w.wait_state("A", "STARTING") and wait_until(lambda: read_pids(sandbox, "A"))):
            return problems + ["A never became STARTING with a spawned child"], []
        r2 = w.request("A", _q_body("qs11-2"))
        problems += _q_close(w.sched)
        alive = _q_alive(sandbox, "A")
        problems += _q_code("popped while STARTING", r1, "shutting_down", timeout=0.0)
        problems += _q_code("still queued", r2, "queue_closed", timeout=0.0)
        if alive:
            problems.append("pid(s) %r alive after shutdown() returned" % alive)
        if w.sched.thread is not None and w.sched.thread.is_alive():
            problems.append("the scheduler thread is alive after shutdown()")
        if _q_sends(sandbox, "A"):
            problems.append("a ticket was sent during shutdown")
        late = w.sched.submit_unload(None, False)
        problems += _q_code("submitted after shutdown", late, "shutting_down", timeout=0.0)
    return problems + w.problems, ["shutdown during STARTING: shutting_down, queue_closed, no "
                                   "child left, the thread joined"]


@_q_live
def qs12(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    problems = []
    models = {"A": _q_entry(sandbox, "A", "--ignore-term", stop_grace=1.5), "B": _q_entry(sandbox, "B")}
    b0 = _q_body("qs12-0")
    with _q_world(mod, models) as w:
        r0 = w.request("A", b0)
        problems += _q_ok("first", r0, b0)
        w.release(r0)
        rb = w.request("B", _q_body("qs12-b"))
        if not w.wait_state("A", "STOPPING"):
            return problems + ["the swap never put A in STOPPING"], []
        problems += _q_close(w.sched)
        alive = _q_alive(sandbox, "A") + _q_alive(sandbox, "B")
        problems += _q_code("swapping request", rb, "shutting_down", timeout=0.0)
        if alive:
            problems.append("pid(s) %r alive after shutdown(): the ladder was abandoned" % alive)
    return problems + w.problems, ["shutdown during a swap finished the SIGTERM-ignoring child's "
                                   "ladder before it returned"]


@_q_live
def qs13(mod, sandbox, names, optional):
    names.extend(("A", "B"))
    problems, samples, seen, worst = [], [0], set(), [0]
    models = {"A": _q_entry(sandbox, "A", "--answer-delay", "0.05"),
              "B": _q_entry(sandbox, "B", "--answer-delay", "0.05")}
    bodies = [(m, _q_body("qs13-%d" % i)) for i, m in enumerate("ABABAB")]
    with _q_world(mod, models) as w:
        stop = threading.Event()

        def sampler():
            while not stop.is_set():
                snap = w.sched.states()
                resident = [name for name, view in snap.items() if mod._sd_is_resident(view)]
                samples[0] += 1
                worst[0] = max(worst[0], len(resident))
                seen.update(resident)
                stop.wait(0.005)

        thread = threading.Thread(target=sampler, name="tf-qs13-sampler", daemon=True)
        thread.start()
        try:
            tickets = [(w.request(m, b), b) for m, b in bodies]
            for i, (ticket, body) in enumerate(tickets):
                problems += _q_ok("request %d" % i, ticket, body, timeout=2 * WAIT_CEILING_S)
            w.release(*(t for t, _b in tickets))
        finally:
            stop.set()
            thread.join(WAIT_CEILING_S)
        if worst[0] > 1:
            problems.append("%d models were resident at once" % worst[0])
        if seen != {"A", "B"} or samples[0] < 20:
            problems.append("vacuous sampling: %d samples, resident seen %r" % (samples[0], sorted(seen)))
    return problems + w.problems, ["%d snapshots across 5 swaps: never more than one resident "
                                   "model" % samples[0]]


@_q_live
def qs14(mod, sandbox, names, optional):
    names.append("A")
    problems, probes, holder = [], [], []

    class Rec(mod.Supervisor):
        def start(self, spec):
            sched, got = holder[0], []

            def probe():
                ok = sched.cond.acquire(timeout=1.0)
                if ok:
                    sched.cond.release()
                    snap = sched.states()
                    got.append((True, snap[spec.name].state.name, mod._sd_resident_model(snap)))
                else:
                    got.append((False, None, None))

            thread = threading.Thread(target=probe, name="tf-qs14-probe", daemon=True)
            thread.start()
            thread.join(WAIT_CEILING_S)
            probes.append(got[0] if got else (False, None, None))
            return super().start(spec)

    body = _q_body("qs14")
    with _q_world(mod, {"A": _q_entry(sandbox, "A")}, supervisor=Rec()) as w:
        holder.append(w.sched)
        r1 = w.request("A", body)
        problems += _q_ok("request", r1, body)
        w.release(r1)
    if not probes:
        problems.append("Supervisor.start was never called")
    elif probes[0] != (True, "STARTING", "A"):
        problems.append("at the spawn: (lock free, state, resident) = %r, expected (True, "
                        "'STARTING', 'A')" % (probes[0],))
    return problems + w.problems, ["before the spawn A is already STARTING and resident, and no "
                                   "lock is held while spawning"]


@_q_live
def qs15(mod, sandbox, names, optional):
    problems, cap = [], 4
    with _q_world(mod, {"A": _q_entry(sandbox, "A")}, cap=cap) as w:
        barrier = threading.Barrier(3 * cap)
        got, errors = [], []

        def admit():
            try:
                barrier.wait(WAIT_CEILING_S)
                got.append(w.sched.try_admit())
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=admit, daemon=True) for _ in range(3 * cap)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT_CEILING_S)
        admitted = [r for r in got if r is not None]
        if errors or len(got) != 3 * cap:
            problems.append("the admission threads failed: %r" % sorted(set(errors)))
        if len(admitted) != cap or w.admitted() != cap:
            problems.append("%d admitted (counter %d) of %d racing, cap %d"
                            % (len(admitted), w.admitted(), 3 * cap, cap))
        for res in admitted:
            w.sched.release(res)
            w.sched.release(res)
        if w.admitted() != 0:
            problems.append("counter %d after releasing every reservation twice, expected 0"
                            % w.admitted())
        again = [w.sched.try_admit() for _ in range(cap + 1)]
        if sum(r is not None for r in again) != cap:
            problems.append("after the releases %d of %d admitted, expected %d"
                            % (sum(r is not None for r in again), cap + 1, cap))
        for res in again:
            if res is not None:
                w.sched.release(res)
    return problems + w.problems, ["%d threads raced try_admit(): exactly %d admitted; double "
                                   "release is idempotent" % (3 * cap, cap)]


QS16_REPEATS = 6


@_q_live
def qs16(mod, sandbox, names, optional):
    """M19: a non-force unload for the same target as the queue's last ticket attaches
    to that ticket instead of growing the queue (an unload survives its client)."""
    names.append("A")
    problems = []
    body = _q_body("qs16")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--answer-delay", "1.5")}) as w:
        r1 = w.request("A", body)
        if wait_events(sandbox, "request", 1, "A") is None:
            return problems + ["the request never reached the stub"], []
        tickets = [w.sched.submit_unload("A", False) for _ in range(QS16_REPEATS)]
        with w.sched.cond:
            queued = [t for t in w.sched.queue if t.kind == "unload"]
        if len(queued) != 1:
            problems.append("%d repeated unloads of A left %d unload tickets queued, expected 1"
                            % (QS16_REPEATS, len(queued)))
        for i, ticket in enumerate(tickets):
            problems += _q_unloaded("unload %d" % i, ticket, True, "A")
        problems += _q_ok("request", r1, body)
        w.release(r1)
        if w.state("A") != "STOPPED":
            problems.append("A is %s after the unloads, expected STOPPED" % w.state("A"))
    return problems + w.problems, ["%d identical unloads behind a running request: one queued "
                                   "ticket, every caller answered from it" % QS16_REPEATS]


GQS_FUNCS = (qs1, qs2, qs3, qs4, qs5, qs6, qs7, qs8, qs9, qs10, qs11, qs12, qs13, qs14, qs15, qs16)


def _t_retry(mod, sandbox, names, retries, tag):
    """A request against a stub that crashes on every POST: (problems, ticket, body, w)."""
    names.append("A")
    body = _q_body(tag)
    entry = _q_entry(sandbox, "A", "--crash-after", "0", retries=retries)
    with _q_world(mod, {"A": entry}, patches=(("_SD_RESTART_BUDGET", 50),)) as w:
        ticket = w.request("A", body)
        problems = _q_code("request", ticket, "model_crashed", timeout=2 * WAIT_CEILING_S)
        w.release(ticket)
        crashes = w.crashes("A")
    sends = len(_q_sends(sandbox, "A", body))
    want = retries + 1
    if sends != want or ticket.attempts != want or crashes != want:
        problems.append("retries=%d: %d send(s), attempts %r, %d crash(es); expected %d each"
                        % (retries, sends, ticket.attempts, crashes, want))
    return problems + w.problems, ticket


@_q_live
def t1(mod, sandbox, names, optional):
    problems, _ticket = _t_retry(mod, sandbox, names, 0, "t1")
    return problems, ["retries=0: exactly one send, then 502 model_crashed"]


@_q_live
def t2(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    control = os.path.join(sandbox, "control")
    _q_control(control, "crash")
    body = _q_body("t2")
    entry = _q_entry(sandbox, "A", "--control-file", control, retries=1)
    with _q_world(mod, {"A": entry}, patches=(("_sd_send", _q_flip_send(mod, control)),)) as w:
        ticket = w.request("A", body)
        problems += _q_ok("re-sent request", ticket, body)
        w.release(ticket)
        crashes = w.crashes("A")
    sends = _q_sends(sandbox, "A", body)
    if len(sends) != 2 or ticket.attempts != 2 or crashes != 1:
        problems.append("%d send(s), attempts %r, %d crash(es); expected 2, 2, 1"
                        % (len(sends), ticket.attempts, crashes))
    elif sends[0]["pid"] == sends[1]["pid"]:
        problems.append("the re-send reached the crashed process")
    for ev in sends:
        if read_body(sandbox, ev["pid"], ev["seq"], "A") != body:
            problems.append("send seq %r: the stub received other bytes" % ev.get("seq"))
    return problems + w.problems, ["retries=1: the crashed send was re-sent once to the restarted "
                                   "child, byte-exact, and answered 200"]


@_q_live
def t3(mod, sandbox, names, optional):
    problems, _ticket = _t_retry(mod, sandbox, names, 2, "t3")
    return problems, ["retries=2: exactly three sends, then 502 model_crashed"]


@_q_live
def t4(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    control = os.path.join(sandbox, "control")
    _q_control(control, "crash")
    body, later = _q_body("t4"), _q_body("t4-later")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--control-file", control, retries=1)}) as w:
        ticket = w.request("A", body)
        problems += _q_code("crash in the retry", ticket, "model_crashed")
        w.release(ticket)
        crashes, disabled = w.crashes("A"), w.states()["A"].disabled
        sends = len(_q_sends(sandbox, "A", body))
        _q_control(control, "ok")
        after = w.request("A", later)
        problems += _q_ok("next request", after, later)
        w.release(after)
    if sends != 2 or ticket.attempts != 2 or crashes != 2 or disabled:
        problems.append("%d send(s), attempts %r, %d crash(es), disabled %s; expected 2, 2, 2, False"
                        % (sends, ticket.attempts, crashes, disabled))
    if len(_q_sends(sandbox, "A", body)) != 2:
        problems.append("a third send of the failed ticket happened")
    return problems + w.problems, ["the retry crashed too: 502 model_crashed, both crashes "
                                   "counted, no third send; the queue goes on"]


@_q_live
def t5(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    timeout = 1.5
    body = _q_body("t5")
    entry = _q_entry(sandbox, "A", "--hang-after-accept", retries=1, request_timeout=timeout)
    with _q_world(mod, {"A": entry}) as w:
        t0 = time.monotonic()
        ticket = w.request("A", body)
        bound = timeout + Q_GRACE_S + mod._SD_KILL_STEP_S + S_SLACK_S
        problems += _q_code("hung child", ticket, "upstream_timeout", timeout=bound)
        took = time.monotonic() - t0
        alive = _q_alive(sandbox, "A")
        w.release(ticket)
        crashes = w.crashes("A")
    if took < timeout - 0.1:
        problems.append("upstream_timeout after %.2fs, before request_timeout %.1fs" % (took, timeout))
    if alive:
        problems.append("the hung child %r survived the timeout" % alive)
    if len(_q_sends(sandbox, "A", body)) != 1 or ticket.attempts != 1:
        problems.append("a timed-out request was retried")
    if crashes != 1 or ticket.abort_reason != "timeout":
        problems.append("%d crash(es), abort_reason %r; expected 1 and 'timeout'"
                        % (crashes, ticket.abort_reason))
    return problems + w.problems, ["504 upstream_timeout after %.2fs, child killed, one crash, "
                                   "never retried (H7)" % took]


@_q_live
def t6(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    entry = _q_entry(sandbox, "A", "--hang-after-accept", retries=0, request_timeout=1.1)
    bound = 1.1 + Q_GRACE_S + mod._SD_KILL_STEP_S + S_SLACK_S
    with _q_world(mod, {"A": entry}, patches=(("_SD_RESTART_BUDGET", 2),)) as w:
        for i in (1, 2):
            ticket = w.request("A", _q_body("t6-%d" % i))
            problems += _q_code("timeout %d" % i, ticket, "upstream_timeout", timeout=bound)
            w.release(ticket)
        if not w.states()["A"].disabled:
            problems.append("2 upstream_timeouts with budget 2 did not disable A (M4)")
        starts, sends = len(read_starts(sandbox, "A")), len(_q_sends(sandbox, "A"))
        third = w.request("A", _q_body("t6-3"))
        problems += _q_code("after the budget", third, "model_disabled")
        w.release(third)
        if (len(read_starts(sandbox, "A")), len(_q_sends(sandbox, "A"))) != (starts, sends):
            problems.append("a disabled model was started or sent to")
    return problems + w.problems, ["each upstream_timeout charges the budget; the budget-th one "
                                   "disables the model (M4)"]


@_q_live
def t7(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    entry = _q_entry(sandbox, "A", "--crash-on-marker", Q_POISON, retries=1)
    with _q_world(mod, {"A": entry}, patches=(("_SD_RESTART_BUDGET", 4),)) as w:
        for client in (1, 2):
            body = _q_body("t7-%d" % client, Q_POISON)
            ticket = w.request("A", body)
            problems += _q_code("poison client %d" % client, ticket, "model_crashed")
            w.release(ticket)
            if len(_q_sends(sandbox, "A", body)) != 2:
                problems.append("poison client %d: %d sends, expected 2"
                                % (client, len(_q_sends(sandbox, "A", body))))
        if not w.states()["A"].disabled or w.crashes("A") != 4:
            problems.append("2 poison prompts x 2 sends: disabled=%s crashes=%d, expected True, 4"
                            % (w.states()["A"].disabled, w.crashes("A")))
        innocent = _q_body("t7-innocent")
        refused = w.request("A", innocent)
        problems += _q_code("innocent client", refused, "model_disabled")
        w.release(refused)
        if _q_sends(sandbox, "A", innocent):
            problems.append("the disabled model received the innocent body")
        unload = w.sched.submit_unload("A", False)
        problems += _q_unloaded("unload A", unload, False, "A")
        healed = w.request("A", innocent)
        problems += _q_ok("after the unload", healed, innocent)
        w.release(healed)
    return problems + w.problems, ["a poison prompt burned retries+1 crashes per request and "
                                   "disabled the model for every client until unload (M9)"]


@_q_live
def t8(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    if getattr(mod, "_SD_RESTART_BUDGET", None) != 5:
        problems.append("_SD_RESTART_BUDGET is %r, expected 5" % (getattr(mod, "_SD_RESTART_BUDGET", None),))
    with _q_world(mod, {"A": _q_entry(sandbox, "A", "--crash-after", "0", retries=0)}) as w:
        tickets = [w.request("A", _q_body("t8-%d" % i)) for i in range(5)]
        for i, ticket in enumerate(tickets):
            problems += _q_code("crash %d" % (i + 1), ticket, "model_crashed", timeout=2 * WAIT_CEILING_S)
        w.release(*tickets)
        view = w.states()["A"]
        if not view.disabled or w.crashes("A") != 5:
            problems.append("after 5 crashes: disabled=%s crashes=%d" % (view.disabled, w.crashes("A")))
        starts = len(read_starts(sandbox, "A"))
        sixth = w.request("A", _q_body("t8-6"))
        problems += _q_code("6th", sixth, "model_disabled")
        w.release(sixth)
        if len(read_starts(sandbox, "A")) != starts:
            problems.append("a disabled model was started")
        unload = w.sched.submit_unload("A", False)
        problems += _q_unloaded("unload", unload, False, "A")
        view = w.states()["A"]
        if view.disabled or w.crashes("A") or view.restart_at != 0.0:
            problems.append("the unload left disabled=%s crashes=%d restart_at=%r"
                            % (view.disabled, w.crashes("A"), view.restart_at))
        seventh = w.request("A", _q_body("t8-7"))
        problems += _q_code("after the unload", seventh, "model_crashed")
        w.release(seventh)
        if len(read_starts(sandbox, "A")) != starts + 1:
            problems.append("the re-enabled model was not started")
    return problems + w.problems, ["5 crashes in the window -> model_disabled without a start; "
                                   "a queued unload re-enabled it (SC-4)"]


@_q_live
def t9(mod, sandbox, names, optional):
    names.append("A")
    problems, calls, holder = [], [], []
    real = mod._sd_send

    def send(*args, **kwargs):
        calls.append(holder[0].done.is_set() if holder else None)
        answer = real(*args, **kwargs)
        if len(calls) == 1:
            raise mod.UpstreamError("eof")  # the answer was read, then the link "broke"
        return answer

    body = _q_body("t9")
    with _q_world(mod, {"A": _q_entry(sandbox, "A", retries=1)}, patches=(("_sd_send", send),)) as w:
        ticket = w.request("A", body)
        holder.append(ticket)
        problems += _q_ok("request", ticket, body)
        w.release(ticket)
        crashes = w.crashes("A")
    answers = events_named(sandbox, "answer", "A")
    if len(calls) != 2 or True in calls:
        problems.append("sends %r (done already set at a send: %s)" % (len(calls), True in calls))
    if len(answers) != 2:
        problems.append("the stub answered %d time(s), expected 2" % len(answers))
    elif _q_sha(ticket.result.body or b"") != answers[1].get("sha256"):
        problems.append("the result is not exactly the second, complete answer")
    first = ticket.result
    if ticket.finish(None) is not False or ticket.result is not first:
        problems.append("finish() is not set-once")
    if crashes != 1 or ticket.attempts != 2:
        problems.append("%d crash(es), attempts %r; expected 1 and 2" % (crashes, ticket.attempts))
    return problems + w.problems, ["an answer lost after it was read was never delivered: one "
                                   "result, the complete re-sent answer; finish() is set-once"]


@_q_live
def t10(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    control = os.path.join(sandbox, "control")
    _q_control(control, "crash")
    entry = _q_entry(sandbox, "A", "--control-file", control, retries=1)
    with _q_world(mod, {"A": entry}, patches=(("_sd_send", _q_flip_send(mod, control)),)) as w:
        b1 = _q_body("t10-1")
        r1 = w.request("A", b1)
        problems += _q_ok("crash then re-send", r1, b1)
        w.release(r1)
        if w.crashes("A") != 1:
            problems.append("a crash and a successful re-send charged %d crash(es), expected 1"
                            % w.crashes("A"))
        _q_control(control, "keep-generating")
        r2 = w.request("A", _q_body("t10-2"))
        if wait_events(sandbox, "generating", 1, "A") is None:
            return problems + ["r2 never reached the stub"], []
        force = w.sched.submit_unload(None, True)
        problems += _q_code("force-aborted", r2, "unload_forced")
        problems += _q_unloaded("force", force, True, "A")
        w.release(r2)
        _q_control(control, "keep-generating")
        r3 = w.request("A", _q_body("t10-3"))
        if wait_events(sandbox, "generating", 2, "A") is None:
            return problems + ["r3 never reached the stub"], []
        w.sched.cancel(r3)
        problems += _q_done("client-aborted", r3)
        if not wait_until(lambda: w.admitted() == 0):
            problems.append("admitted %d after the client abort" % w.admitted())
        view = w.states()["A"]
        if w.crashes("A") != 1 or view.disabled:
            problems.append("after a force and a client abort: %d crash(es) disabled=%s, "
                            "expected 1 and False" % (w.crashes("A"), view.disabled))
        if (r2.attempts, r3.attempts) != (1, 1):
            problems.append("aborted tickets' attempts %r, expected (1, 1)" % ((r2.attempts, r3.attempts),))
    return problems + w.problems, ["one crash = one charge; force and client aborts charge "
                                   "nothing and are never retried"]


@_q_live
def t11(mod, sandbox, names, optional):
    names.extend(("A", "B"))
    problems, order = [], []
    control = os.path.join(sandbox, "control")
    _q_control(control, "crash")
    models = {"A": _q_entry(sandbox, "A", "--control-file", control, retries=1), "B": _q_entry(sandbox, "B")}
    b1, b2, b3 = _q_body("t11-1"), _q_body("t11-2"), _q_body("t11-3")
    with _q_world(mod, models, patches=(("_sd_send", _q_flip_send(mod, control, order)),)) as w:
        tickets = [w.request("A", b1), w.request("B", b2), w.request("A", b3)]
        for ticket, body in zip(tickets, (b1, b2, b3)):
            problems += _q_ok("request", ticket, body, timeout=2 * WAIT_CEILING_S)
        w.release(*tickets)
    want = [_q_sha(b) for b in (b1, b1, b2, b3)]
    if order != want:
        names_of = {_q_sha(b1): "A1", _q_sha(b2): "B2", _q_sha(b3): "A3"}
        problems.append("send order %r, expected ['A1', 'A1', 'B2', 'A3']"
                        % [names_of.get(sha, "?") for sha in order])
    return problems + w.problems, ["the crashed ticket was re-sent at the head, before B behind it "
                                   "could swap in"]


@_q_live
def t12(mod, sandbox, names, optional):
    names.append("A")
    problems = []

    class FailingRestart(mod.Supervisor):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def start(self, spec):
            self.calls += 1
            if self.calls > 1:
                spec = dataclasses.replace(spec, args=tuple(spec.args) + ("--crash-on-start",))
            return super().start(spec)

    entry = _q_entry(sandbox, "A", "--crash-after", "0", retries=1)
    body = _q_body("t12-a")
    with _q_world(mod, {"A": entry}, supervisor=FailingRestart()) as w:
        ticket = w.request("A", body)
        problems += _q_code("restart failed", ticket, "model_start_failed")
        w.release(ticket)
        crashes = w.crashes("A")
    if ticket.attempts != 1 or len(_q_sends(sandbox, "A", body)) != 1 or crashes != 2:
        problems.append("attempts %r, %d send(s), %d crash(es); expected 1, 1, 2 (M3)"
                        % (ticket.attempts, len(_q_sends(sandbox, "A", body)), crashes))
    problems += w.problems
    with _q_world(mod, {"A": entry}, supervisor=FailingRestart(),
                  patches=(("_SD_RESTART_BUDGET", 2),)) as w:
        first, second = w.request("A", _q_body("t12-b1")), w.request("A", _q_body("t12-b2"))
        problems += _q_code("restart failure trips the budget", first, "model_disabled")
        starts = len(read_starts(sandbox, "A"))
        problems += _q_code("queued behind it", second, "model_disabled")
        w.release(first, second)
        if len(read_starts(sandbox, "A")) != starts:
            problems.append("the disabled model was started for the queued ticket")
    return problems + w.problems, ["a failed restart answers model_start_failed with attempts "
                                   "unchanged; when it trips the budget, model_disabled (M3)"]


@_q_live
def t13(mod, sandbox, names, optional):
    """M24: Popen raising ValueError (a NUL in argv/env/cwd) is a failed start: the model
    goes DEAD with one Budget charge and the ticket is answered model_start_failed;
    the model is not left STARTING, so the next request is served (and fails) too."""
    problems = []
    spawns = []

    def boom(argv, **kwargs):
        spawns.append(len(argv))
        raise ValueError("tf: embedded null byte")

    with patched(subprocess, [("Popen", boom)]):
        with _q_world(mod, {"A": _q_entry(sandbox, "A")}) as w:
            first = w.request("A", _q_body("t13-1"))
            problems += _q_code("first", first, "model_start_failed")
            w.release(first)
            if w.state("A") != "DEAD":
                problems.append("A is %s after the ValueError, expected DEAD" % w.state("A"))
            if w.crashes("A") != 1:
                problems.append("%d crash(es) charged, expected 1" % w.crashes("A"))
            second = w.request("A", _q_body("t13-2"))
            problems += _q_code("second", second, "model_start_failed")
            w.release(second)
    if len(spawns) != 2:
        problems.append("%d spawn attempt(s), expected 2 (one per request)" % len(spawns))
    return problems + w.problems, ["a ValueError from Popen runs the failure path: DEAD, one "
                                   "charge, model_start_failed; the next request is not blocked"]


GT_FUNCS = (t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13)


def _q_group(suite, fixture_root, group, cases, funcs, label):
    mod, why = load_module(label)
    absent = missing_names(mod, Q_NAMES) if mod is not None else [why]
    if not absent:
        lacking = [m for m in Q_METHODS if not callable(getattr(mod.Scheduler, m, None))]
        if lacking:
            absent = ["Scheduler has no %s" % ", ".join(lacking)]
    for cid, fn in zip(cases, funcs):
        if absent:
            suite.record(group, cid, absent)
            continue
        sandbox = new_sandbox(fixture_root, "%s-%s" % (label, cid.split(" ", 1)[0].lower()))
        try:
            problems, detail = fn(mod, sandbox)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(group, cid, problems, detail=detail)


def group_q_sched(suite, fixture_root):
    _q_group(suite, fixture_root, GQS, GQS_CASES, GQS_FUNCS, "qs")


def group_t(suite, fixture_root):
    _q_group(suite, fixture_root, GT, GT_CASES, GT_FUNCS, "t")


# ---------------------------------------------------------------------------
# Groups P and Q-http -- the HTTP front, the request pipeline, main()
# ---------------------------------------------------------------------------
# Real sockets against the router and the REAL stub child.  The in-process
# cases build Settings through parse_config, their own Scheduler (as Q-sched
# does) and the front around it on 127.0.0.1:0, run serve_forever on a thread,
# and speak raw HTTP/1.1 bytes to it (no http.client: several requests are
# deliberately malformed).  The startup-line cases and P30 run the router as a
# subprocess (`launch`), because main() configures the logging and the
# signals.  A refusal that must not read the body is sent with a DECLARED
# Content-Length and NO body: it has to arrive within P_REFUSE_S anyway (and
# sending a body the router never reads would turn its close into a reset).
# Every in-process case also sweeps its own DEBUG log for the token and the
# prompt sentinels.
#
# The contract these groups pin (task-024 unit 7 + generated regions, task-025
# unit 8 do_POST and the error table, task-027 unit 9 main; plan Step 5, the
# closed error table, D-11, D-13..D-15, H2, H8, S-L1..S-L3, S-M1, NFR-4).  Every
# name is module-level in Scripts/gpu-router.py:
#
# Unit 7 -- the front (task-024)
#   _SdHttpServer(server_address, settings, scheduler): a ThreadingHTTPServer
#     subclass that binds in __init__ (port 0 = ephemeral, `server_port` after
#     the bind).  Attributes: `settings` (the Settings given; its
#     max_connections is BOTH the image cap try_admit() enforces and the base of
#     the connection cap), `scheduler` (the Scheduler given; the front never
#     starts it -- the caller does, and the caller shuts it down), `conn_sem`
#     = threading.BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE)
#     created BEFORE super().__init__ (H8), `front_log` (the module's `log`),
#     a hand-written process_request_thread releasing conn_sem in a finally,
#     and the generated server_bind / process_request / handle_error.
#   _SdHandler: the BaseHTTPRequestHandler subclass (protocol HTTP/1.1, class
#     `timeout` = the header bound, setup() installing the generated
#     _HeaderDeadlineReader).  ONE exchange per connection: every answer
#     carries Connection: close and the socket is closed after it; a pipelined
#     second request is never answered (P29).  The suite always sends exactly
#     one `Host: 127.0.0.1:<port>`; other Host values are not pinned here (the
#     closed table has no 403 -- a plan gap left to task-025).
#     ROUTE ON THE RAW REQUEST TARGET (the second word of self.requestline),
#     never on self.path: the stdlib parse_request of Python 3.11+ (this host
#     runs 3.14) collapses a leading "//" in self.path, so routing on it would
#     turn "//v1/images/generations" into a route (P22 pins 404).
#
# Unit 8 -- the pipeline and the closed table (task-025)
#   _sd_error(code, message) -> (status, body): status from _SD_ERROR_STATUS,
#     body the JSON bytes {"error": {"message": message, "type": <non-empty
#     str>, "code": code}} with exactly those keys; a code outside the table
#     raises KeyError or ValueError (P34).  Every refusal is written with that
#     status, Content-Type application/json, a Content-Length framing the body
#     and Connection: close; 401 adds `WWW-Authenticate: Bearer`, queue_full
#     adds `Retry-After: 1`.
#   do_POST, in this order (plan Step 5 round-2 H2):
#     1. auth: exactly ONE Authorization header whose value is `Bearer `
#        (scheme case-insensitive, then ONE space) + the token, compared as
#        UTF-8 bytes with hmac.compare_digest; anything else -- absent, wrong,
#        repeated, another scheme, empty, extra spaces or text, a tab, a
#        non-ASCII byte -- is 401 (never a 500).  A token in the query or the
#        path authenticates nothing.  Before the route check and before any
#        body byte is read (P4..P8).
#     2. the route and the method from _sd_route_head on the raw target
#        (404 route_not_found / 405 method_not_allowed / 400 invalid_request
#        for a query on an image route / 404 model_unknown for an unknown
#        prefix / 400 model_required for an un-prefixed /sdapi/ route).  Every
#        other method on an image path (GET, PUT, DELETE) is 405 after auth
#        too, so do_GET / do_PUT / do_DELETE exist (P18).
#     3. header-only framing: ANY Transfer-Encoding header (with or without a
#        Content-Length) and a missing, repeated or non-decimal Content-Length
#        are 411 length_required; Content-Length > settings.body_limit is 413
#        body_too_large; a Content-Type the route does not take is 415 (P24
#        pins text/plain on /v1/images/generations and
#        application/x-www-form-urlencoded on /sdapi/v1/txt2img; JSON on
#        generations, txt2img and img2img and multipart/form-data on edits
#        must pass).  None of these takes a reservation.
#     4. scheduler.try_admit(); None => 503 queue_full + Retry-After: 1 with
#        the body unread (QH4).
#     5. `Expect: 100-continue` is answered "HTTP/1.1 100 Continue" only now,
#        after auth and admission; a refused request never sees a 100 (P32).
#     6. the body is read under _SD_BODY_DEADLINE_S, read at CALL time from
#        the module global (P31 patches it on a private module) and measured
#        from the end of the header read; expiry => 400 invalid_request, the
#        reservation released, the body never reaches the child.
#     7. without a prefix route_request (the sniffer) picks the model from
#        the body (400 invalid_request / 400 model_required / 404
#        model_unknown); with a prefix the body is never sniffed (D-11, P15).
#     8. scheduler.submit_request(model, "POST", upstream_path, headers, body,
#        reservation); while it waits on ticket.done the handler checks its
#        client every <= 0.5 s (select + MSG_PEEK, never consuming a byte, as
#        llm-router's _rt_client_gone) and calls scheduler.cancel(ticket) once
#        the client is gone (QH1, QH2, QH3).
#     9. a result with code None is written as its status, its Content-Type,
#        our own Content-Length and Connection: close, the body byte-exact; a
#        result with a code through _sd_error.  The reservation is released by
#        the handler in a finally for a ticket that never ran, AFTER the answer
#        is written for a completed one, and never by the handler for a ticket
#        the scheduler dropped (the scheduler owns that release: QH2, QH3 wait
#        for the counter to come back to 0).
#   Only Content-Type crosses the hop (the unit 5 sanitation, P26).
#   UNIT 6 GAP (P28): an UpstreamError of kind "failed" (a 1xx, a 3xx, an
#     encoded or malformed answer) with abort_reason None must finish the
#     ticket 502 upstream_failed after exactly ONE send -- it is not a crash
#     and is never retried.  Today Scheduler._send_failed treats every
#     abort-free UpstreamError as a crash (re-send, then model_crashed); the
#     front tasks must split "failed" out (the closed table's upstream_failed
#     row; "too_large" belongs to the same row but is not exercised here).
#
# Unit 9 -- main (task-027)
#   main(): _sd_parse_args(argv), the generated _configure_logging (WARNING to
#   stderr by default; --debug lowers it), load_config (a ConfigError is one
#   stderr line and exit 2), Supervisor + Scheduler + _SdHttpServer on --bind
#   / --port, the signal handlers BEFORE the generated ready file {"port",
#   "pid"}, serve; SIGTERM / SIGINT => the ordered shutdown: no child group
#   survives (P30 reaps the stub), the ready file is removed (P2) and the exit
#   code is 0.  `if __name__ == "__main__":` runs it.  Startup lines (round-3
#   H1, moved here from group L):
#     * every start logs ONE INFO line containing "single-user host" -- the
#       fixed text "single-user host assumed: the sd-server child listens
#       unauthenticated on 127.0.0.1 and is reachable by any local user" --
#       carrying no token, config path or env value (P3);
#     * with BOTH --allow-remote and --insecure-cleartext exactly ONE WARNING
#       line containing "cleartext" ("remote bind without TLS: bearer is sent
#       in cleartext"), keyed on the two flags and NOT on the bind address:
#       P1 binds 127.0.0.1 so the suite never listens on a LAN address;
#     * otherwise no line contains "cleartext" (P2).
#
# Cases provable only once the generated regions exist (task-024): P1..P3
# need _configure_logging, P2 _http_remove_ready_file, QH4 the generated
# process_request admitting max_connections + _SD_CONTROL_RESERVE connections
# (N = cap + reserve clients must all get a slot, none the bare 503 of a full
# conn_sem).  Group Q-http's fifth case (two unloads racing) arrives with
# group U (task-029) and is appended to GQH_CASES / GQH_FUNCS there.

GP_CASES = (
    "P1 remote+cleartext: 1 WARNING",
    "P2 default bind: no warning",
    "P3 trust INFO line, no values",
    "P4 401 before any body read",
    "P5 token in query/path: 401",
    "P6 non-ASCII Authorization 401",
    "P7 duplicate Authorization 401",
    "P8 bearer scheme case-insens.",
    "P9 prefix routing",
    "P10 JSON body model fallback",
    "P11 multipart model fallback",
    "P12 ambiguous body model 400",
    "P13 no model resolvable 400",
    "P14 prefix wins over body model",
    "P15 prefixed body not sniffed",
    "P16 unknown model 404",
    "P17 non-allowlisted path 404",
    "P18 wrong method 405",
    "P19 duplicate CL 411",
    "P20 Transfer-Encoding 411",
    "P21 bad/missing Content-Length",
    "P22 // .. % paths 404",
    "P23 body over the limit 413",
    "P24 Content-Type refused 415",
    "P25 byte-exact in and out",
    "P26 client auth not forwarded",
    "P27 sd_cpp_extra_args intact",
    "P28 encoded/1xx/3xx answer 502",
    "P29 one answer, then close",
    "P30 logs free of sentinels",
    "P31 slow body 400, slot freed",
    "P32 100-continue after auth",
    "P33 absolute-URI target 404",
    "P34 _sd_error closed table",
    "P35 folded Content-Type in: 400",
    "P36 folded Content-Type out: 502",
    "P37 relayed answer is nosniff",
    "P38 signal handler: a flag only",
    "P39 config port, CLI --port wins",
    "P40 config bind without acks: exit 2",
)

GQH_CASES = (
    "QH1 client gone while queued",
    "QH2 client gone while running",
    "QH3 disconnect loop bounded",
    "QH4 over-cap race queue_full",
)

P_NAMES = ("_SdHttpServer", "_SdHandler", "_sd_error", "main", "Scheduler", "Supervisor",
           "parse_config", "log", "_SD_BODY_DEADLINE_S", "_SD_CONTROL_RESERVE", "_SD_ERROR_STATUS")
# The closed error table (plan Section 3): code -> HTTP status.
P_STATUS = {"unauthorized": 401, "route_not_found": 404, "method_not_allowed": 405,
            "length_required": 411, "body_too_large": 413, "unsupported_media_type": 415,
            "invalid_request": 400, "model_required": 400, "model_unknown": 404,
            "model_crashed": 502, "model_start_failed": 502, "model_start_timeout": 504,
            "model_disabled": 503, "unload_forced": 503, "unload_failed": 500,
            "upstream_timeout": 504, "queue_full": 503, "upstream_failed": 502,
            "shutting_down": 503, "queue_closed": 503}
P_GEN = "/v1/images/generations"
P_EDITS = "/v1/images/edits"
P_TXT = "/sdapi/v1/txt2img"
P_IMG = "/sdapi/v1/img2img"
P_JSON = "application/json"
P_CAP = 4
P_REFUSE_S = 5.0             # a refusal that needs no body byte arrives within this
P_EARLY_LENGTH = 4096        # the Content-Length declared (never sent) by an early refusal
P_SLOW_DEADLINE_S = 2.0      # _SD_BODY_DEADLINE_S while P31 runs
P_LIMIT = 65536              # body_limit of P23
P_IMAGE = bytes(range(256)) * 8
P_WRONG_TOKEN = "tf-sd-wrong-SENTINEL-" + secrets.token_hex(16)   # as long as TF_TOKEN
P_PROMPT = "tf-p-PROMPT-SENTINEL-" + secrets.token_hex(6)
P_FILENAME = "tf-p-FILE-SENTINEL-%s.png" % secrets.token_hex(4)
P_COOKIE = "tf-p-COOKIE-SENTINEL-" + secrets.token_hex(6)
P_ENV_VALUE = "tf-p-ENV-SENTINEL-" + secrets.token_hex(6)
P_PATH_DIR = "tf-p-PATH-SENTINEL-" + secrets.token_hex(4)
P_RULE = "tf rule text"
P_TRUST_NEEDLE = "single-user host"
P_SECRETS = (TF_TOKEN, P_WRONG_TOKEN, P_PROMPT, P_FILENAME, P_COOKIE, P_ENV_VALUE)
P_FORWARDED_OK = frozenset(("host", "content-type", "content-length", "accept", "accept-encoding",
                            "connection"))
QH_BUSY_S = 3.0              # the stub's answer delay while a QH1 request is queued behind it
QH_LOOP_BODY = 128 * 1024    # each departed client's body in QH3
QH_RACE_CAP = 2              # QH4: max_connections; N = cap + _SD_CONTROL_RESERVE (4) clients
QH_RACE_N = QH_RACE_CAP + 4


class _PResp:
    """One answer parsed from raw bytes: the interim 1xx statuses, the final status,
    the header pairs (names lower-cased) and the body (cut at Content-Length)."""

    def __init__(self, raw, eof):
        self.raw = raw
        self.eof = eof
        self.interim = []
        self.status = None
        self.headers = []
        self.body = b""
        rest = raw
        while rest:
            head, sep, tail = rest.partition(b"\r\n\r\n")
            if not sep:
                break
            lines = head.split(b"\r\n")
            words = lines[0].split(b" ", 2)
            try:
                status = int(words[1])
            except (IndexError, ValueError):
                break
            headers = []
            for line in lines[1:]:
                name, colon, value = line.partition(b":")
                if colon:
                    headers.append((name.strip().decode("latin-1").lower(),
                                    value.strip().decode("latin-1")))
            if 100 <= status < 200:
                self.interim.append(status)
                rest = tail
                continue
            self.status = status
            self.headers = headers
            length = self.header("content-length")
            self.body = tail[:int(length)] if length is not None and length.isdigit() else tail
            break

    def header(self, name):
        for key, value in self.headers:
            if key == name:
                return value
        return None

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def code(self):
        doc = self.json()
        err = doc.get("error") if isinstance(doc, dict) else None
        return err.get("code") if isinstance(err, dict) else None

    def describe(self):
        if self.status is None:
            return "no answer (%d bytes, %s)" % (len(self.raw), "closed" if self.eof else "still open")
        return "%d %s" % (self.status, self.code() or "")


def _p_read(sock, timeout=WAIT_CEILING_S):
    """(bytes, eof): everything *sock* delivers until EOF, a reset or *timeout*."""
    deadline = time.monotonic() + timeout
    chunks = []
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return b"".join(chunks), False
        sock.settimeout(min(left, 0.5))
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return b"".join(chunks), True
        if not data:
            return b"".join(chunks), True
        chunks.append(data)


def _p_read_head(sock, timeout=P_REFUSE_S):
    """The bytes up to and including the first blank line (or what came before *timeout*)."""
    deadline = time.monotonic() + timeout
    data = b""
    while b"\r\n\r\n" not in data:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        sock.settimeout(min(left, 0.5))
        try:
            chunk = sock.recv(1)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    return data


def _p_head(port, method, target, token=TF_TOKEN, ctype=P_JSON, length=None, extra=(), auth=None):
    """A request head (UTF-8, surrogateescape for raw bytes): one `Host: 127.0.0.1:<port>`,
    one `Authorization: Bearer <token>` unless *token* is None, or exactly the header
    values in *auth* when given; a Content-Type, a Content-Length (when not None) and
    the *extra* header lines in order."""
    lines = ["%s %s HTTP/1.1" % (method, target), "Host: %s:%d" % (LOOPBACK, port)]
    values = list(auth) if auth is not None else (["Bearer " + token] if token is not None else [])
    lines += ["Authorization: " + value for value in values]
    if ctype is not None:
        lines.append("Content-Type: " + ctype)
    if length is not None:
        lines.append("Content-Length: %s" % length)
    lines += list(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8", "surrogateescape")


def _p_open(port, data):
    """A connected socket that has sent *data* (the caller reads and closes it)."""
    sock = socket.create_connection((LOOPBACK, port), timeout=WAIT_CEILING_S)
    try:
        sock.sendall(data)
    except BaseException:
        sock.close()
        raise
    return sock


def _p_exchange(port, data, timeout=WAIT_CEILING_S):
    sock = _p_open(port, data)
    try:
        return _PResp(*_p_read(sock, timeout))
    finally:
        sock.close()


def _p_post(port, target, body, ctype=P_JSON, timeout=WAIT_CEILING_S, **kw):
    """A complete request: the head with Content-Length len(body), then *body*."""
    return _p_exchange(port, _p_head(port, "POST", target, ctype=ctype, length=len(body), **kw) + body,
                       timeout)


def _p_early(port, target, method="POST", length=P_EARLY_LENGTH, timeout=P_REFUSE_S, **kw):
    """A head whose body is DECLARED but never sent: an answer that needs no body byte
    arrives within *timeout*; a router that waits for the body answers nothing."""
    return _p_exchange(port, _p_head(port, method, target, length=length, **kw), timeout)


def _p_m(model, path):
    return "/m/%s%s" % (model, path)


def _p_body(tag, model=None, extra=""):
    """A unique JSON image body carrying the prompt sentinel (its SHA identifies it)."""
    doc = {"prompt": "%s %s-%s%s" % (P_PROMPT, tag, secrets.token_hex(4), extra), "n": 1}
    if model is not None:
        doc["model"] = model
    return json.dumps(doc, sort_keys=True).encode("utf-8")


def _p_multipart(tag, models=(), image=P_IMAGE):
    """A canonical multipart /edits body: prompt, one `model` part per *models*, an image."""
    parts = [_r_part("prompt", ("%s %s-%s" % (P_PROMPT, tag, secrets.token_hex(4))).encode("ascii"))]
    parts += [_r_part("model", m.encode("ascii")) for m in models]
    parts.append(_r_part("image", image, filename=P_FILENAME, ctype="image/png"))
    return _r_multipart(parts)


def _p_framing(label, resp):
    """Problems unless *resp* is one framed answer followed by the router's close."""
    problems = []
    if (resp.header("connection") or "").lower() != "close":
        problems.append("%s: the answer carries no Connection: close" % label)
    length = resp.header("content-length")
    if length is None or not length.isdigit() or int(length) != len(resp.body):
        problems.append("%s: Content-Length %r does not frame the body" % (label, length))
    if not resp.eof:
        problems.append("%s: the router kept the connection open after the answer" % label)
    return problems


def _p_refused(label, resp, code):
    """Problems unless *resp* is the closed table's refusal *code*, OpenAI-shaped."""
    want = P_STATUS[code]
    if resp.status is None:
        return ["%s: %s, expected %d %s" % (label, resp.describe(), want, code)]
    doc = resp.json()
    err = doc.get("error") if isinstance(doc, dict) else None
    if (resp.status, resp.code()) != (want, code):
        return ["%s: answered %s, expected %d %s" % (label, resp.describe(), want, code)]
    problems = []
    if set(err) != {"message", "type", "code"} or not all(isinstance(v, str) and v for v in err.values()):
        problems.append("%s: the error is not {message, type, code} non-empty strings" % label)
    if (resp.header("content-type") or "").split(";")[0].strip() != P_JSON:
        problems.append("%s: the refusal is not application/json" % label)
    problems += _p_framing(label, resp)
    if code == "unauthorized" and (resp.header("www-authenticate") or "").split(" ")[0].lower() != "bearer":
        problems.append("%s: the 401 carries no WWW-Authenticate: Bearer" % label)
    if code == "queue_full" and resp.header("retry-after") != "1":
        problems.append("%s: queue_full carries Retry-After %r, expected '1'" % (label, resp.header("retry-after")))
    return problems


def _p_relayed(label, resp, body, sandbox, name, upstream):
    """Problems unless *resp* is stub *name*'s 200 for exactly *body*, which the stub
    received once on *upstream*; the relayed bytes equal the bytes the stub sent."""
    if resp.status != 200:
        return ["%s: answered %s, expected the stub's 200" % (label, resp.describe())]
    problems = _p_framing(label, resp)
    doc = resp.json()
    if not isinstance(doc, dict) or doc.get("tf_request_sha256") != _q_sha(body):
        return problems + ["%s: the answer is not the stub's answer for this body" % label]
    if (resp.header("content-type") or "") != P_JSON:
        problems.append("%s: Content-Type %r, expected the stub's %r" % (label, resp.header("content-type"), P_JSON))
    sends = _q_sends(sandbox, name, body)
    if len(sends) != 1:
        problems.append("%s: stub %s received this body %d times, expected once" % (label, name, len(sends)))
    elif sends[0].get("path") != upstream:
        problems.append("%s: stub %s received it on %r, expected %r" % (label, name, sends[0].get("path"), upstream))
    sha = _q_sha(resp.body)
    if not any(ev.get("sha256") == sha for ev in events_named(sandbox, "answer", name)):
        problems.append("%s: the relayed body is not byte-identical to the stub's answer" % label)
    return problems


def _p_stored(label, sandbox, name, body):
    """Problems unless the stub stored exactly *body* (the bytes on disk, not the SHA)."""
    sends = _q_sends(sandbox, name, body)
    if not sends:
        return ["%s: stub %s never received this body" % (label, name)]
    if read_body(sandbox, sends[0].get("pid"), sends[0].get("seq"), name) != body:
        return ["%s: the stored body differs from the bytes sent" % label]
    return []


def _p_lines(text, needle):
    return [ln for ln in text.splitlines() if needle in ln.lower()]


def _p_not_ready(proc):
    lines = [ln for ln in proc.stderr_text().splitlines() if ln.strip()]
    return "the router never became ready (exit %r; %s)" % (
        proc.proc.poll(), (lines[-1][:160] if lines else "no stderr"))


def _p_leaks(label, text):
    return ["%s carries a secret or sentinel value" % label] if any(s in text for s in P_SECRETS) else []


def _p_log_to(mod, path):
    """The router's logger -> *path* (0600) at DEBUG, unpropagated; returns the undo."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    WRITES.append(path)
    LOG_PATHS.append(path)
    handler = logging.StreamHandler(os.fdopen(fd, "a", encoding="utf-8"))
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    handler.setLevel(logging.DEBUG)
    logger = mod.log
    saved = (logger.level, logger.propagate)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    def undo():
        logger.removeHandler(handler)
        logger.setLevel(saved[0])
        logger.propagate = saved[1]
        handler.close()
        try:
            handler.stream.close()
        except (OSError, ValueError):
            pass
    return undo


def _p_file_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


class _PWorld(_QWorld):
    """A live in-process front: `port`, `srv`, and the Scheduler's counters."""

    def __init__(self, sched, srv):
        super().__init__(sched)
        self.srv = srv
        self.port = srv.server_port

    def queued(self):
        with self.sched.cond:
            return len(self.sched.queue)

    def body_bytes(self):
        """Request-body bytes the Scheduler holds: queued tickets plus the running one."""
        with self.sched.cond:
            tickets = list(self.sched.queue)
            if self.sched.running is not None:
                tickets.append(self.sched.running)
            return sum(len(t.body) for t in tickets if t.body)

    def settled(self, admitted, queued=0):
        return wait_until(lambda: self.admitted() == admitted and self.queued() == queued)


@contextlib.contextmanager
def _p_front(mod, sandbox, models, cap=P_CAP, patches=(), label="front", supervisor=None, **top):
    """The in-process router: Settings from *models* (+ top-level config keys *top*) with
    max_connections *cap*, a started Scheduler (over *supervisor*, else a fresh
    mod.Supervisor()), _SdHttpServer on 127.0.0.1:0 serving on a thread, the module
    logger in a 0600 DEBUG file.  On exit: scheduler shutdown (bounded), the front
    stopped, the log swept for secrets; problems in world.problems."""
    settings = dataclasses.replace(mod.parse_config(_l_raw(base_config(models, **top))),
                                   max_connections=cap)
    log_path = os.path.join(sandbox, label + ".log")
    with patched(mod, list(Q_FAST) + list(patches)):
        undo = _p_log_to(mod, log_path)
        sched = srv = thread = world = None
        try:
            sched = mod.Scheduler(settings, supervisor if supervisor is not None else mod.Supervisor())
            sched.start()
            srv, thread = serve_in_thread(mod._SdHttpServer((LOOPBACK, 0), settings, sched), "tf-sd-front")
            world = _PWorld(sched, srv)
            yield world
        finally:
            closing = _q_close(sched) if sched is not None else []
            if srv is not None:
                unserve(srv, thread)
            undo()
            if world is not None:
                world.problems += closing + _p_leaks("the router log", _p_file_text(log_path))


def _p_entry(sandbox, name, *flags, **keys):
    return _q_entry(sandbox, name, *flags, **keys)


def _p_config(sandbox, models, subdir=None):
    """A 0600 config for a subprocess router (optionally inside *subdir*)."""
    where = sandbox
    if subdir is not None:
        where = os.path.join(sandbox, subdir)
        os.mkdir(where, 0o700)
        WRITES.append(where)
    return write_config(where, base_config(models))


# -- startup lines and main() (subprocess) ------------------------------------

@_q_live
def p1(mod, sandbox, names, optional):
    optional.append("A")
    path = _p_config(sandbox, {"A": _p_entry(sandbox, "A")})
    proc, ready = launch(path, ["--bind", LOOPBACK, "--allow-remote", "--insecure-cleartext"], label="p1")
    try:
        if ready is None:
            return [_p_not_ready(proc)], []
        wait_until(lambda: bool(_p_lines(proc.stderr_text(), "cleartext")))
    finally:
        code = proc.close()
    problems = []
    text = proc.stderr_text()
    lines = _p_lines(text, "cleartext")
    if len(lines) != 1:
        problems.append("%d stderr lines name cleartext, expected exactly one" % len(lines))
    elif " WARNING " not in lines[0]:
        problems.append("the cleartext line is not logged at WARNING")
    if code != 0:
        problems.append("SIGTERM: exit %r, expected 0" % (code,))
    problems += _p_leaks("stderr", text)
    return problems, ["--allow-remote --insecure-cleartext: ready, ONE cleartext WARNING (D-13); "
                      "SIGTERM exits 0"]


@_q_live
def p2(mod, sandbox, names, optional):
    optional.append("A")
    path = _p_config(sandbox, {"A": _p_entry(sandbox, "A")})
    proc, ready = launch(path, ["--debug"], label="p2")
    try:
        if ready is None:
            return [_p_not_ready(proc)], []
        wait_until(lambda: bool(_p_lines(proc.stderr_text(), P_TRUST_NEEDLE)), P_REFUSE_S)
    finally:
        code = proc.close()
    problems = []
    text = proc.stderr_text()
    if _p_lines(text, "cleartext"):
        problems.append("the default loopback bind logged a cleartext line")
    if code != 0:
        problems.append("SIGTERM: exit %r, expected 0" % (code,))
    if os.path.lexists(proc.ready_path):
        problems.append("the ready file survived the ordered shutdown")
    problems += _p_leaks("stderr", text)
    return problems, ["default loopback bind, --debug: no cleartext line; SIGTERM exits 0 and "
                      "removes the ready file"]


@_q_live
def p3(mod, sandbox, names, optional):
    optional.append("A")
    path = _p_config(sandbox, {"A": _p_entry(sandbox, "A", env={"TF_SD_P3": P_ENV_VALUE})},
                     subdir=P_PATH_DIR)
    proc, ready = launch(path, ["--debug"], label="p3")
    try:
        if ready is None:
            return [_p_not_ready(proc)], []
        wait_until(lambda: bool(_p_lines(proc.stderr_text(), P_TRUST_NEEDLE)))
    finally:
        proc.close()
    problems = []
    text = proc.stderr_text()
    lines = _p_lines(text, P_TRUST_NEEDLE)
    if len(lines) != 1:
        problems.append("%d stderr lines carry %r, expected exactly one" % (len(lines), P_TRUST_NEEDLE))
    else:
        if " INFO " not in lines[0]:
            problems.append("the single-user-host line is not logged at INFO")
        if P_PATH_DIR in lines[0] or path in lines[0]:
            problems.append("the single-user-host line carries the config path")
        problems += _p_leaks("the single-user-host line", lines[0])
    problems += _p_leaks("stderr", text)
    return problems, ["every start logs ONE fixed INFO trust line (S-M1): no token, path or env value"]


# -- auth -------------------------------------------------------------------------

@_q_live
def p4(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, target, token in (("no Authorization", _p_m("A", P_GEN), None),
                                     ("a wrong token", _p_m("A", P_GEN), P_WRONG_TOKEN),
                                     ("no Authorization, unknown path", "/tf/no/such/route", None),
                                     ("no Authorization, unknown model", _p_m("nosuch", P_GEN), None),
                                     ("a wrong token, un-prefixed", P_GEN, P_WRONG_TOKEN)):
            resp = _p_early(w.port, target, token=token)
            problems += _p_refused(label, resp, "unauthorized")
            problems += _p_leaks(label + ": the answer", resp.raw.decode("latin-1"))
        if w.admitted() != 0:
            problems.append("a 401 left %d admission(s) taken" % w.admitted())
    if read_starts(sandbox, "A"):
        problems.append("an unauthenticated request started the model")
    return problems + w.problems, ["401 + WWW-Authenticate: Bearer with the body declared and never "
                                   "sent; auth precedes the route; no admission, no start"]


@_q_live
def p5(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for target in (_p_m("A", P_GEN) + "?token=" + TF_TOKEN, P_GEN + "?access_token=" + TF_TOKEN,
                       "/" + TF_TOKEN + P_GEN, _p_m(TF_TOKEN, P_GEN)):
            resp = _p_early(w.port, target, token=None)
            problems += _p_refused("the token in %s" % ("the query" if "?" in target else "the path"),
                                   resp, "unauthorized")
            problems += _p_leaks("a token-in-URL answer", resp.raw.decode("latin-1"))
        resp = _p_early(w.port, _p_m("A", P_GEN) + "?n=1")
        problems += _p_refused("an authenticated image route with a query", resp, "invalid_request")
    if read_starts(sandbox, "A"):
        problems.append("a token in the URL started the model")
    return problems + w.problems, ["a token in the query or the path authenticates nothing (401, never "
                                   "echoed); an image route takes no query (400)"]


@_q_live
def p6(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, value in (("a non-ASCII last character", "Bearer " + TF_TOKEN[:-1] + "é"),
                             ("a non-ASCII first character", "Bearer ü" + TF_TOKEN),
                             ("a raw 0xff byte after the token", "Bearer " + TF_TOKEN + "\udcff"),
                             ("a non-ASCII scheme", "Béarer " + TF_TOKEN)):
            problems += _p_refused(label, _p_early(w.port, _p_m("A", P_GEN), auth=[value]), "unauthorized")
    if read_starts(sandbox, "A"):
        problems.append("a non-ASCII Authorization started the model")
    return problems + w.problems, ["a non-ASCII Authorization is a clean 401, never a 500 or a dropped "
                                   "connection (S-L1: compare_digest on bytes)"]


@_q_live
def p7(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    right, wrong = "Bearer " + TF_TOKEN, "Bearer " + P_WRONG_TOKEN
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, values in (("two identical right headers", [right, right]),
                              ("right then wrong", [right, wrong]),
                              ("wrong then right", [wrong, right])):
            problems += _p_refused(label, _p_early(w.port, _p_m("A", P_GEN), auth=values), "unauthorized")
    if read_starts(sandbox, "A"):
        problems.append("a repeated Authorization started the model")
    return problems + w.problems, ["exactly one Authorization header or 401 (S-L2, ADR 0015)"]


@_q_live
def p8(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for scheme in ("bearer", "BEARER", "Bearer", "bEaReR"):
            body = _p_body("p8-" + scheme)
            resp = _p_post(w.port, _p_m("A", P_GEN), body, auth=["%s %s" % (scheme, TF_TOKEN)])
            problems += _p_relayed("scheme %r" % scheme, resp, body, sandbox, "A", P_GEN)
        for label, value in (("Basic", "Basic " + TF_TOKEN), ("an empty token", "Bearer "),
                             ("the scheme alone", "Bearer"), ("two spaces", "Bearer  " + TF_TOKEN),
                             ("trailing text", "Bearer %s x" % TF_TOKEN), ("a tab", "Bearer\t" + TF_TOKEN),
                             ("no scheme", TF_TOKEN), ("no space", "Bearer" + TF_TOKEN),
                             ("another scheme", "Token " + TF_TOKEN),
                             ("a prefix of the token", "Bearer " + TF_TOKEN[:-1]),
                             ("the token plus a character", "Bearer " + TF_TOKEN + "x")):
            problems += _p_refused(label, _p_early(w.port, _p_m("A", P_GEN), auth=[value]), "unauthorized")
    return problems + w.problems, ["`Bearer ` is matched case-insensitively; every other form is 401 "
                                   "(S-L2)"]


# -- routing ----------------------------------------------------------------------

@_q_live
def p9(mod, sandbox, names, optional):
    names.extend(("A", "B"))
    problems = []
    models = {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}
    with _p_front(mod, sandbox, models) as w:
        for model, path, body, ctype in (("A", P_GEN, _p_body("p9-gen"), P_JSON),
                                         ("A", P_TXT, _p_body("p9-txt"), P_JSON),
                                         ("B", P_IMG, _p_body("p9-img"), P_JSON),
                                         ("B", P_EDITS, _p_multipart("p9-edits"), R_MULTI)):
            resp = _p_post(w.port, _p_m(model, path), body, ctype=ctype)
            problems += _p_relayed("/m/%s%s" % (model, path), resp, body, sandbox, model, path)
    return problems + w.problems, ["/m/<model>/ selects the child; the upstream path is the route "
                                   "without the prefix, on all four routes"]


@_q_live
def p10(mod, sandbox, names, optional):
    names.append("B")
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}) as w:
        body = _p_body("p10", model="B")
        problems += _p_relayed("un-prefixed JSON model B", _p_post(w.port, P_GEN, body), body,
                               sandbox, "B", P_GEN)
    if read_starts(sandbox, "A"):
        problems.append("model A started for a body naming B")
    return problems + w.problems, ["no prefix: the JSON body model picks the child (FR-2)"]


@_q_live
def p11(mod, sandbox, names, optional):
    names.append("B")
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}) as w:
        body = _p_multipart("p11", models=("B",))
        problems += _p_relayed("un-prefixed multipart model B", _p_post(w.port, P_EDITS, body, ctype=R_MULTI),
                               body, sandbox, "B", P_EDITS)
        problems += _p_stored("un-prefixed multipart model B", sandbox, "B", body)
    if read_starts(sandbox, "A"):
        problems.append("model A started for a body naming B")
    return problems + w.problems, ["no prefix: the canonical multipart `model` part picks the child; "
                                   "the body is forwarded byte-exact"]


@_q_live
def p12(mod, sandbox, names, optional):
    optional.extend(("A", "B"))
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}) as w:
        body = _p_multipart("p12", models=("A", "B"))
        problems += _p_refused("two multipart model parts", _p_post(w.port, P_EDITS, body, ctype=R_MULTI),
                               "invalid_request")
        dup = ('{"model": "A", "prompt": "%s p12", "model": "B"}' % P_PROMPT).encode("utf-8")
        problems += _p_refused("a duplicate JSON model key", _p_post(w.port, P_GEN, dup), "invalid_request")
        if not w.settled(0):
            problems.append("a refused body left %d admission(s) taken" % w.admitted())
    if read_starts(sandbox, "A") or read_starts(sandbox, "B"):
        problems.append("an ambiguous body started a model")
    return problems + w.problems, ["an ambiguous body model is 400 invalid_request, never a guess; "
                                   "the reservation comes back"]


@_q_live
def p13(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        problems += _p_refused("/sdapi/ without a prefix", _p_early(w.port, P_TXT), "model_required")
        problems += _p_refused("/sdapi/ img2img without a prefix", _p_early(w.port, P_IMG), "model_required")
        problems += _p_refused("a JSON body without a model", _p_post(w.port, P_GEN, _p_body("p13")),
                               "model_required")
        if not w.settled(0):
            problems.append("a refused request left %d admission(s) taken" % w.admitted())
    if read_starts(sandbox, "A"):
        problems.append("a request without a model started one")
    return problems + w.problems, ["no model resolvable: 400 model_required (/sdapi/ needs the prefix)"]


@_q_live
def p14(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}) as w:
        for label, model in (("a body naming B", "B"), ("an llm-router route key", "lan-sd-route"),
                             ("a body naming an unknown model", "nosuch")):
            body = _p_body("p14", model=model)
            problems += _p_relayed("/m/A/ with " + label, _p_post(w.port, _p_m("A", P_GEN), body), body,
                                   sandbox, "A", P_GEN)
    if read_starts(sandbox, "B"):
        problems.append("the body model B started although the prefix named A")
    return problems + w.problems, ["the /m/<model>/ prefix is authoritative; the body model is "
                                   "ignored, no mismatch code (D-11)"]


@_q_live
def p15(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        junk = (_r_multipart([_r_part("model", b"A"), _r_part("model", b"B")])
                + b"\r\n--tf-not-a-boundary\r\nContent-Disposition: form-data\r\n\r\n\xff\x00junk")
        broken_json = ('{"model": "A", "model": "B", "prompt": "%s p15", ' % P_PROMPT).encode("utf-8")
        for label, path, body, ctype in (("a malformed multipart body", P_EDITS, junk, R_MULTI),
                                         ("a malformed JSON body", P_GEN, broken_json, P_JSON)):
            resp = _p_post(w.port, _p_m("A", path), body, ctype=ctype)
            problems += _p_relayed("/m/A/ with " + label, resp, body, sandbox, "A", path)
            problems += _p_stored(label, sandbox, "A", body)
    return problems + w.problems, ["with a prefix the body is not sniffed: a body the sniffer would "
                                   "refuse is forwarded untouched"]


@_q_live
def p16(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        problems += _p_refused("/m/nosuch/", _p_early(w.port, _p_m("nosuch", P_GEN)), "model_unknown")
        problems += _p_refused("/m/a/ (names are case-sensitive)", _p_early(w.port, _p_m("a", P_TXT)),
                               "model_unknown")
        problems += _p_refused("a body naming nosuch", _p_post(w.port, P_GEN, _p_body("p16", model="nosuch")),
                               "model_unknown")
        if not w.settled(0):
            problems.append("an unknown model left %d admission(s) taken" % w.admitted())
    if read_starts(sandbox, "A"):
        problems.append("an unknown model started a configured one")
    return problems + w.problems, ["a model name outside the config is 404 model_unknown"]


# -- paths, methods, framing ------------------------------------------------------

@_q_live
def p17(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for target in ("/sdcpp/v1/img_gen", "/v1/models/load", _p_m("A", "/sdcpp/v1/img_gen"),
                       "/v1/images", P_GEN + "/", "/m/A", "/m/A/", "/", "/sdapi/v1/options",
                       "/V1/images/generations", _p_m("A", "/v1/models/load")):
            problems += _p_refused(target, _p_early(w.port, target), "route_not_found")
    if read_starts(sandbox, "A"):
        problems.append("a non-allowlisted path started the model")
    return problems + w.problems, ["only the four image routes (bare or under /m/<model>/) are "
                                   "relayed; sd-server's other routes are 404"]


@_q_live
def p18(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for method, target, length in (("GET", P_GEN, None), ("GET", _p_m("A", P_TXT), None),
                                       ("PUT", _p_m("A", P_GEN), P_EARLY_LENGTH),
                                       ("DELETE", P_EDITS, None)):
            resp = _p_early(w.port, target, method=method, length=length)
            problems += _p_refused("%s %s" % (method, target), resp, "method_not_allowed")
    if read_starts(sandbox, "A"):
        problems.append("a wrong method started the model")
    return problems + w.problems, ["GET / PUT / DELETE on an image route: 405 method_not_allowed"]


@_q_live
def p19(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, values in (("two equal Content-Length", ("16", "16")),
                              ("two different Content-Length", ("16", "17"))):
            extra = ["Content-Length: " + v for v in values]
            problems += _p_refused(label, _p_early(w.port, _p_m("A", P_GEN), length=None, extra=extra),
                                   "length_required")
        if w.admitted() != 0:
            problems.append("a framing refusal took an admission")
    if read_starts(sandbox, "A"):
        problems.append("a repeated Content-Length started the model")
    return problems + w.problems, ["a repeated Content-Length is 411 before any body read"]


@_q_live
def p20(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, length, te in (("chunked, no Content-Length", None, "chunked"),
                                  ("chunked plus Content-Length", 16, "chunked"),
                                  ("identity plus Content-Length", 16, "identity"),
                                  ("gzip, chunked", None, "gzip, chunked")):
            resp = _p_early(w.port, _p_m("A", P_GEN), length=length, extra=["Transfer-Encoding: " + te])
            problems += _p_refused(label, resp, "length_required")
        if w.admitted() != 0:
            problems.append("a Transfer-Encoding refusal took an admission")
    if read_starts(sandbox, "A"):
        problems.append("a Transfer-Encoding request started the model")
    return problems + w.problems, ["ANY Transfer-Encoding is 411 from the hand-written check, before "
                                   "any body read (the generated front does not refuse it)"]


@_q_live
def p21(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        problems += _p_refused("no Content-Length", _p_early(w.port, _p_m("A", P_GEN), length=None),
                               "length_required")
        for value in ("abc", "-1", "+16", "0x10", "16, 16", "1e1", "١٦"):
            resp = _p_early(w.port, _p_m("A", P_GEN), length=value)
            problems += _p_refused("Content-Length %r" % value, resp, "length_required")
    if read_starts(sandbox, "A"):
        problems.append("a bad Content-Length started the model")
    return problems + w.problems, ["a missing or non-decimal Content-Length is 411 (exactly one "
                                   "ASCII decimal value)"]


@_q_live
def p22(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for target in ("//v1/images/generations", "/m/A//v1/images/generations",
                       "/m/A/v1/../v1/images/generations", "/v1/images/./generations",
                       "/m/../v1/images/generations", "/m/A%2Fv1/images/generations",
                       "/v1/images%2Fgenerations", "/m/%41/v1/images/generations",
                       "/v1/images/generations%00"):
            problems += _p_refused(target, _p_early(w.port, target), "route_not_found")
    if read_starts(sandbox, "A"):
        problems.append("a non-canonical path started the model")
    return problems + w.problems, ["`//`, dot segments and any `%` are 404, never normalised (the "
                                   "raw request target is routed, not self.path)"]


@_q_live
def p23(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}, body_limit=P_LIMIT) as w:
        problems += _p_refused("Content-Length limit + 1",
                               _p_early(w.port, _p_m("A", P_GEN), length=P_LIMIT + 1), "body_too_large")
        problems += _p_refused("Content-Length 1 GiB, un-prefixed",
                               _p_early(w.port, P_GEN, length=1 << 30), "body_too_large")
        if w.admitted() != 0:
            problems.append("a 413 took an admission")
        head = _p_body("p23")
        body = head[:-1] + b"," + b'"pad": "' + b"x" * (P_LIMIT - len(head) - 10) + b'"}'
        if len(body) != P_LIMIT:
            return problems + ["fixture: the at-limit body is %d bytes" % len(body)], []
        problems += _p_relayed("a body of exactly body_limit", _p_post(w.port, _p_m("A", P_GEN), body),
                               body, sandbox, "A", P_GEN)
    return problems + w.problems, ["Content-Length > body_limit: 413 from the head alone; exactly "
                                   "body_limit passes"]


@_q_live
def p24(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for path, ctype in ((P_GEN, "text/plain"), (P_TXT, "application/x-www-form-urlencoded")):
            resp = _p_early(w.port, _p_m("A", path), ctype=ctype)
            problems += _p_refused("%s on %s" % (ctype, path), resp, "unsupported_media_type")
        if w.admitted() != 0:
            problems.append("a 415 took an admission")
    if read_starts(sandbox, "A"):
        problems.append("a refused Content-Type started the model")
    return problems + w.problems, ["a Content-Type the route does not take is 415 before any body read"]


# -- the relay --------------------------------------------------------------------

@_q_live
def p25(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        unicode_json = ('{ "prompt" : "%s ✓ 日本 \\u0000 \\"q\\"",\n  "n":1 }\n\n' % P_PROMPT
                        ).encode("utf-8")
        big = json.dumps({"init_images": [base64.b64encode(P_IMAGE * 100).decode("ascii")],
                          "prompt": P_PROMPT}).encode("ascii")
        for label, path, body, ctype in (("unicode JSON, odd spacing", P_GEN, unicode_json, P_JSON),
                                         ("binary multipart", P_EDITS, _p_multipart("p25"), R_MULTI),
                                         ("a 200 KiB img2img body", P_IMG, big, P_JSON)):
            resp = _p_post(w.port, _p_m("A", path), body, ctype=ctype)
            problems += _p_relayed(label, resp, body, sandbox, "A", path)
            problems += _p_stored(label, sandbox, "A", body)
    return problems + w.problems, ["request bodies stored byte-identical by the stub; answers relayed "
                                   "byte-identical (FR-7)"]


@_q_live
def p26(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        body = _p_body("p26")
        extra = ["Cookie: tf=" + P_COOKIE, "Proxy-Authorization: Basic " + P_COOKIE, "X-Tf-Extra: 1",
                 "Forwarded: for=192.0.2.1", "Connection: keep-alive"]
        resp = _p_post(w.port, _p_m("A", P_GEN), body, extra=extra)
        problems += _p_relayed("headers stripped", resp, body, sandbox, "A", P_GEN)
        sends = _q_sends(sandbox, "A", body)
        seen = sends[0].get("header_names") if sends else None
        if not isinstance(seen, list):
            problems.append("the stub recorded no header names")
        else:
            extra_names = sorted(set(seen) - P_FORWARDED_OK)
            if extra_names:
                problems.append("headers crossed the hop: %s" % ", ".join(extra_names))
            if "content-type" not in seen:
                problems.append("Content-Type did not cross the hop")
            if "authorization" in seen:
                problems.append("the client's Authorization reached the child (D-9)")
    return problems + w.problems, ["only Content-Type (+ the router's own Host, Content-Length, "
                                   "Accept, Accept-Encoding, Connection) reach the child"]


@_q_live
def p27(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    marker = ('<sd_cpp_extra_args>{"sample_steps": 4,  "cfg_scale": 1.0, "lora": "/tf/x.safetensors"}'
              '</sd_cpp_extra_args>')
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for path in (P_GEN, P_TXT):
            body = ('{"prompt": "%s a cat %s", "sd_cpp_extra_args": {"seed" : -1}}' % (P_PROMPT, marker)
                    ).encode("utf-8")
            body = body[:-1] + (', "tf": "%s"}' % path).encode("ascii")
            resp = _p_post(w.port, _p_m("A", path), body)
            problems += _p_relayed("sd_cpp_extra_args on " + path, resp, body, sandbox, "A", path)
            problems += _p_stored("sd_cpp_extra_args on " + path, sandbox, "A", body)
    return problems + w.problems, ["sd_cpp_extra_args blocks pass byte-exact, neither parsed nor "
                                   "rewritten (FR-7)"]


@_q_live
def p28(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--control-file", control)}) as w:
        for word in ("encoded", "status-1xx", "status-3xx"):
            _q_control(control, word)
            body = _p_body("p28-" + word)
            problems += _p_refused("a child answer %s" % word, _p_post(w.port, _p_m("A", P_GEN), body),
                                   "upstream_failed")
            sent = len(_q_sends(sandbox, "A", body))
            if sent != 1:
                problems.append("%s: the child received the body %d times, expected once (not "
                                "retried)" % (word, sent))
        _q_control(control, "ok")
        body = _p_body("p28-ok")
        problems += _p_relayed("afterwards", _p_post(w.port, _p_m("A", P_GEN), body), body, sandbox, "A", P_GEN)
    return problems + w.problems, ["an encoded, 1xx or 3xx child answer is 502 upstream_failed after "
                                   "one send, never retried; the model still serves"]


@_q_live
def p29(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        twice = _p_head(w.port, "POST", "/tf/none", token=None, length=0) * 2
        resp = _p_exchange(w.port, twice)
        if resp.raw.count(b"HTTP/1.1 ") != 1:
            problems.append("two pipelined refusals got %d answers, expected one"
                            % resp.raw.count(b"HTTP/1.1 "))
        problems += _p_refused("the first pipelined request", resp, "unauthorized")
        first, second = _p_body("p29-first"), _p_body("p29-second")
        pair = b"".join(_p_head(w.port, "POST", _p_m("A", P_GEN), length=len(b), extra=["Connection: keep-alive"])
                        + b for b in (first, second))
        resp = _p_exchange(w.port, pair)
        if resp.raw.count(b"HTTP/1.1 ") != 1:
            problems.append("two pipelined image requests got %d answers, expected one"
                            % resp.raw.count(b"HTTP/1.1 "))
        problems += _p_relayed("the first pipelined image request", resp, first, sandbox, "A", P_GEN)
        if _q_sends(sandbox, "A", second):
            problems.append("the second pipelined request reached the child (keep-alive)")
    return problems + w.problems, ["one exchange per connection: Connection: close, then the close; a "
                                   "pipelined second request is never served"]


@_q_live
def p30(mod, sandbox, names, optional):
    names.append("A")
    path = _p_config(sandbox, {"A": _p_entry(sandbox, "A", env={"TF_SD_P30": P_ENV_VALUE})})
    proc, ready = launch(path, ["--debug"], label="p30")
    problems = []
    try:
        if ready is None:
            return [_p_not_ready(proc)], []
        port = proc.port
        body = _p_body("p30")
        problems += _p_relayed("a JSON request", _p_post(port, _p_m("A", P_GEN), body), body, sandbox, "A", P_GEN)
        body = _p_multipart("p30")
        problems += _p_relayed("a multipart request", _p_post(port, _p_m("A", P_EDITS), body, ctype=R_MULTI),
                               body, sandbox, "A", P_EDITS)
        problems += _p_refused("a wrong token", _p_early(port, P_GEN, token=P_WRONG_TOKEN), "unauthorized")
        problems += _p_refused("the token in the query", _p_early(port, P_GEN + "?token=" + TF_TOKEN, token=None),
                               "unauthorized")
        dup = ('{"model": "A", "prompt": "%s", "model": "A"}' % P_PROMPT).encode("utf-8")
        problems += _p_refused("an ambiguous body", _p_post(port, P_GEN, dup), "invalid_request")
    finally:
        code = proc.close()
    text = proc.stderr_text()
    if not text.strip():
        problems.append("the --debug stderr is empty: nothing was logged to check")
    problems += _p_leaks("the --debug stderr", text)
    if code != 0:
        problems.append("SIGTERM: exit %r, expected 0" % (code,))
    return problems, ["--debug stderr carries no token, wrong token, prompt, filename, cookie or env "
                      "value (ADR 0011); SIGTERM exits 0 and leaves no child"]


# -- deadlines, Expect, request target, the table ----------------------------------

@_q_live
def p31(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}, cap=2,
                  patches=(("_SD_BODY_DEADLINE_S", P_SLOW_DEADLINE_S),)) as w:
        stalled = _p_body("p31-slow")
        slow = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(stalled)) + stalled[:10])
        try:
            started = time.monotonic()
            if not wait_until(lambda: w.admitted() == 1, P_REFUSE_S):
                problems.append("the stalled request holds %d admissions, expected 1" % w.admitted())
            fast = _p_body("p31-fast")
            problems += _p_relayed("a concurrent fast request", _p_post(w.port, _p_m("A", P_GEN), fast),
                                   fast, sandbox, "A", P_GEN)
            resp = _PResp(*_p_read(slow, P_SLOW_DEADLINE_S + P_REFUSE_S))
            elapsed = time.monotonic() - started
        finally:
            slow.close()
        problems += _p_refused("the stalled body", resp, "invalid_request")
        if resp.status is not None and elapsed < P_SLOW_DEADLINE_S * 0.5:
            problems.append("the stalled body was refused after %.2fs, before the deadline" % elapsed)
        if not w.settled(0):
            problems.append("the stalled body left %d admission(s) taken" % w.admitted())
    if _q_sends(sandbox, "A", stalled):
        problems.append("the stalled body reached the child")
    return problems + w.problems, ["a body stalled past _SD_BODY_DEADLINE_S is 400 invalid_request and "
                                   "frees its slot; a concurrent request is served (D-15)"]


@_q_live
def p32(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    expect = ["Expect: 100-continue"]
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        resp = _p_early(w.port, _p_m("A", P_GEN), token=P_WRONG_TOKEN, extra=expect)
        if resp.interim:
            problems.append("an unauthenticated request was sent %r before its refusal" % resp.interim)
        problems += _p_refused("Expect with a wrong token", resp, "unauthorized")
        body = _p_body("p32")
        sock = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(body), extra=expect))
        try:
            first = _p_read_head(sock)
            if not first.startswith(b"HTTP/1.1 100"):
                problems.append("an admitted Expect request got %r, expected 100 Continue first"
                                % first[:24].decode("latin-1"))
            sock.sendall(body)
            raw, eof = _p_read(sock)
        finally:
            sock.close()
        resp = _PResp(first + raw, eof)
        problems += _p_relayed("Expect: 100-continue, then the body", resp, body, sandbox, "A", P_GEN)
    return problems + w.problems, ["100 Continue only after auth and admission; a refused request "
                                   "never sees one"]


@_q_live
def p33(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for target in ("http://%s:%d%s" % (LOOPBACK, w.port, P_GEN),
                       "http://%s:%d%s" % (LOOPBACK, w.port, _p_m("A", P_GEN)),
                       P_GEN + "#frag", _p_m("A", P_GEN) + "#"):
            problems += _p_refused(target, _p_early(w.port, target), "route_not_found")
    if read_starts(sandbox, "A"):
        problems.append("an absolute-URI request started the model")
    return problems + w.problems, ["an absolute-URI or fragment request target is 404: the upstream "
                                   "line is rebuilt from the route table only"]


def p34(mod, sandbox):
    problems = []
    table = getattr(mod, "_SD_ERROR_STATUS", None)
    if not isinstance(table, dict) or dict(table) != P_STATUS:
        diff = sorted(set((table or {}).items()) ^ set(P_STATUS.items())) if isinstance(table, dict) else table
        problems.append("_SD_ERROR_STATUS is not the closed table: %r" % (diff,))
    for code, status in sorted(P_STATUS.items()):
        try:
            got = mod._sd_error(code, P_RULE)
        except Exception as exc:  # noqa: BLE001 -- the wrong exception is a finding
            problems.append("_sd_error(%r) raised %s" % (code, type(exc).__name__))
            continue
        if not (isinstance(got, tuple) and len(got) == 2 and isinstance(got[1], bytes)):
            problems.append("_sd_error(%r) did not return (status, body bytes)" % code)
            continue
        try:
            doc = json.loads(got[1].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            problems.append("_sd_error(%r): the body is not JSON" % code)
            continue
        err = doc.get("error") if isinstance(doc, dict) else None
        if got[0] != status:
            problems.append("_sd_error(%r): status %r, expected %d" % (code, got[0], status))
        if (not isinstance(doc, dict) or set(doc) != {"error"} or not isinstance(err, dict)
                or set(err) != {"message", "type", "code"} or err.get("code") != code
                or err.get("message") != P_RULE or not (isinstance(err.get("type"), str) and err["type"])):
            problems.append("_sd_error(%r): body %r is not {error: {message, type, code}}" % (code, doc))
    for bogus in ("model_mismatch", "tf_no_such_code"):
        try:
            mod._sd_error(bogus, P_RULE)
        except (KeyError, ValueError):
            continue
        except Exception as exc:  # noqa: BLE001
            problems.append("_sd_error(%r) raised %s, expected KeyError/ValueError" % (bogus, type(exc).__name__))
            continue
        problems.append("_sd_error(%r) accepted a code outside the closed table" % bogus)
    return problems, ["the 20-code closed table; one renderer, OpenAI-shaped; model_mismatch is gone "
                      "(D-11)"]


P_FOLDED = ("application/json;\r\n x-tf=1", "multipart/form-data; boundary=tf\r\n\tx=1",
            "application/json;\x7f", "application/json;\x01")


@_q_live
def p35(mod, sandbox, names, optional):
    """M6 / M23 inbound: a Content-Type carrying a control character (an obs-fold CRLF,
    a HT continuation, DEL, a C0 byte) is refused 400 invalid_request at the framing,
    on the /m/<model>/ prefix path too (where the body is never sniffed), before any
    admission and before the child starts."""
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for ctype in P_FOLDED:
            path = P_EDITS if ctype.startswith("multipart/") else P_GEN
            for target in (_p_m("A", path), path):
                resp = _p_early(w.port, target, ctype=ctype)
                problems += _p_refused("Content-Type %r on %s" % (ctype, target), resp, "invalid_request")
        if w.admitted() != 0:
            problems.append("a refused Content-Type took an admission")
    if read_starts(sandbox, "A"):
        problems.append("a Content-Type with a control character started the model")
    return problems + w.problems, ["a control character in Content-Type is 400 before the body, "
                                   "prefixed or not; nothing crosses the hop"]


# A child that answers every POST 200 with an obs-folded Content-Type and GET / 404
# (ready).  It records its pid in <argv[2]> so reap_stubs can check it.
P_FOLD_CHILD = r"""
import os, socket, sys
with open(sys.argv[2], "a") as fh:
    fh.write("%d\n" % os.getpid())
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1])))
srv.listen(16)
while True:
    conn, _addr = srv.accept()
    try:
        conn.settimeout(5.0)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        head, _sep, rest = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            name, _colon, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip() or b"0")
        while len(rest) < length:
            chunk = conn.recv(65536)
            if not chunk:
                break
            rest += chunk
        if head.startswith(b"POST"):
            body = b"[1]"
            wire = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json;\r\n x-tf-folded=1\r\n"
                    b"Content-Length: " + str(len(body)).encode("ascii") +
                    b"\r\nConnection: close\r\n\r\n" + body)
        else:
            wire = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        conn.sendall(wire)
    except OSError:
        pass
    finally:
        conn.close()
"""


@_q_live
def p36(mod, sandbox, names, optional):
    """M6 outbound: a child answer whose Content-Type carries a control character (here
    an obs-fold) is never relayed: 502 upstream_failed, the folded text never reaches
    the client."""
    names.append("F")
    problems = []
    entry = {"command": sys.executable,
             "args": ["-I", "-B", "-c", P_FOLD_CHILD, "{port}", os.path.join(sandbox, "F.pids")],
             "stop_grace": Q_GRACE_S, "start_timeout": Q_START_TIMEOUT_S}
    with _p_front(mod, sandbox, {"F": entry}) as w:
        resp = _p_post(w.port, _p_m("F", P_GEN), _p_body("p36"))
        problems += _p_refused("a folded child Content-Type", resp, "upstream_failed")
        if b"x-tf-folded" in resp.raw:
            problems.append("the child's folded header text reached the client")
    return problems + w.problems, ["a child Content-Type with a control character is 502 "
                                   "upstream_failed, never written to the client"]


@_q_live
def p37(mod, sandbox, names, optional):
    """M7: a relayed child answer carries X-Content-Type-Options: nosniff."""
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        body = _p_body("p37")
        resp = _p_post(w.port, _p_m("A", P_GEN), body)
        problems += _p_relayed("relayed 200", resp, body, sandbox, "A", P_GEN)
        if resp.header("x-content-type-options") != "nosniff":
            problems.append("the relayed answer carries X-Content-Type-Options %r, expected 'nosniff'"
                            % resp.header("x-content-type-options"))
    return problems + w.problems, ["the relayed 200 carries X-Content-Type-Options: nosniff"]


def _p_main_signal_lambdas():
    """Lambdas passed as a handler to signal.signal inside main(), by AST."""
    with open(SERVER, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "signal" and len(call.args) == 2
                        and isinstance(call.args[1], ast.Lambda)):
                    found.append(call.lineno)
    return found


def p38(mod, sandbox):
    """M18: the signal handler only stores a flag (no Event.set, whose lock the main
    thread may hold inside Event.wait); main's loop polls the flag after each tick
    and leaves within a tick once the handler ran."""
    problems = []
    absent = missing_names(mod, ("_SdStopFlag", "_sd_wait_stop", "_SD_MAIN_TICK_S"))
    if absent:
        return absent, []
    lambdas = _p_main_signal_lambdas()
    if lambdas:
        problems.append("main() installs a lambda signal handler (line(s) %r)" % lambdas)
    flag = mod._SdStopFlag()
    tick = threading.Event()
    waiter = threading.Thread(target=mod._sd_wait_stop, args=(flag, tick), name="tf-p38-wait", daemon=True)
    waiter.start()
    waiter.join(3 * mod._SD_MAIN_TICK_S)
    if not waiter.is_alive():
        problems.append("control: _sd_wait_stop returned before the handler ran")
    t0 = time.monotonic()
    flag.handler(signal.SIGTERM, None)
    waiter.join(mod._SD_MAIN_TICK_S + 1.0)
    took = time.monotonic() - t0
    if waiter.is_alive():
        problems.append("_sd_wait_stop did not return within a tick after the handler ran")
    if tick.is_set():
        problems.append("the handler set an Event (a lock inside a signal handler)")
    return problems, ["the handler stores a flag; the main loop left %.2fs after it ran" % took]


def p39(mod, sandbox):
    """The listener keys travel through main(): a config `port` is used when no flag
    names one, and the harness's explicit `--port 0` beats it (None-default proof)."""
    path = write_config(sandbox, base_config({"A": _p_entry(sandbox, "A")}, port=5, max_connections=3,
                                             bind="127.0.0.1"))
    proc, ready = launch(path, [], label="p39")
    try:
        if ready is None:
            return [_p_not_ready(proc)], []
        port = ready.get("port")
    finally:
        code = proc.close()
    problems = []
    if port == 5:
        problems.append("the config port 5 won over the explicit --port 0")
    if code != 0:
        problems.append("SIGTERM: exit %r, expected 0" % (code,))
    problems += _p_leaks("stderr", proc.stderr_text())
    return problems, ["config port 5 + bind + max_connections, explicit --port 0: ready on an "
                      "ephemeral port"]


def p40(mod, sandbox):
    """A config `bind` of 0.0.0.0 without allow_remote + insecure_cleartext is refused by
    main() like the CLI form: exit 2, one `gpu-router:` line, never ready, no socket."""
    path = write_config(sandbox, base_config({"A": _p_entry(sandbox, "A")}, bind="0.0.0.0"))
    proc, ready = launch(path, [], label="p40")
    try:
        code = proc.wait_exit()
    finally:
        proc.close()
    problems = []
    if ready is not None:
        problems.append("the router became ready on a non-loopback config bind")
    if code != 2:
        problems.append("exit %r, expected 2" % (code,))
    lines = [ln for ln in proc.stderr_text().splitlines() if ln.strip()]
    if len(lines) != 1 or not lines[0].startswith("gpu-router: ") or "--insecure-cleartext" not in lines[0]:
        problems.append("stderr is not the one-line refusal: %r" % (lines[:3],))
    problems += _p_leaks("stderr", proc.stderr_text())
    return problems, ["config bind 0.0.0.0 alone: exit 2 with the one-line non-loopback refusal"]


GP_FUNCS = (p1, p2, p3, p4, p5, p6, p7, p8, p9, p10, p11, p12, p13, p14, p15, p16, p17, p18, p19,
            p20, p21, p22, p23, p24, p25, p26, p27, p28, p29, p30, p31, p32, p33, p34, p35, p36,
            p37, p38, p39, p40)


# -- Q-http ---------------------------------------------------------------------------

@_q_live
def qh1(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--answer-delay", str(QH_BUSY_S))}) as w:
        run, gone = _p_body("qh1-run"), _p_body("qh1-gone")
        runner = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(run)) + run)
        try:
            if wait_events(sandbox, "request", 1, "A") is None:
                problems.append("the first request never reached the child")
            leaver = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(gone)) + gone)
            try:
                if not w.settled(2, queued=1):
                    problems.append("the second request is not queued (admitted %d, queued %d)"
                                    % (w.admitted(), w.queued()))
            finally:
                leaver.close()
            if not w.settled(1, queued=0):
                problems.append("the departed queued client was not cancelled (admitted %d, queued %d)"
                                % (w.admitted(), w.queued()))
            resp = _PResp(*_p_read(runner, QH_BUSY_S + WAIT_CEILING_S))
        finally:
            runner.close()
        problems += _p_relayed("the running request", resp, run, sandbox, "A", P_GEN)
        if not w.settled(0):
            problems.append("admitted %d after the answer, expected 0" % w.admitted())
    if _q_sends(sandbox, "A", gone):
        problems.append("the departed client's body reached the child")
    if len(read_starts(sandbox, "A")) != 1:
        problems.append("A started %d times, expected once" % len(read_starts(sandbox, "A")))
    return problems + w.problems, ["a queued client that closes its socket is cancelled: out of the "
                                   "queue, slot back, never sent"]


@_q_live
def qh2(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    _q_control(control, "keep-generating")
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--control-file", control)}) as w:
        body = _p_body("qh2")
        client = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(body)) + body)
        try:
            found = wait_events(sandbox, "generating", 1, "A")
        finally:
            client.close()
        if found is None:
            return problems + ["the child never started generating"], []
        pid = found[0].get("pid")
        if not pid_dead(pid):
            problems.append("the generating child survived its client's departure (D-12)")
        if not w.settled(0):
            problems.append("admitted %d after the scheduler dropped the ticket, expected 0" % w.admitted())
        if not w.wait_state("A", "STOPPED"):
            problems.append("A is %s after the client abort, expected STOPPED" % w.state("A"))
        if w.crashes("A") != 0:
            problems.append("the client abort was charged as %d crash(es)" % w.crashes("A"))
        _q_control(control, "ok")
        again = _p_body("qh2-again")
        problems += _p_relayed("the next request", _p_post(w.port, _p_m("A", P_GEN), again), again,
                               sandbox, "A", P_GEN)
    if len(read_starts(sandbox, "A")) != 2:
        problems.append("A started %d times, expected 2 (a cold start after the abort)"
                        % len(read_starts(sandbox, "A")))
    return problems + w.problems, ["a running client that closes its socket: the child is killed, the "
                                   "slot freed by the scheduler, no crash charged; the next request "
                                   "cold-starts"]


@_q_live
def qh3(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    cap = 3
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    _q_control(control, "hang-after-accept")
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--control-file", control)}, cap=cap) as w:
        run = _p_body("qh3-run")
        runner = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(run)) + run)
        peak = {"admitted": 0, "bytes": 0}
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                peak["admitted"] = max(peak["admitted"], w.admitted())
                peak["bytes"] = max(peak["bytes"], w.body_bytes())
                stop.wait(0.005)

        sampler = threading.Thread(target=sample, name="tf-qh3-sample", daemon=True)
        try:
            found = wait_events(sandbox, "request", 1, "A")
            if found is None:
                problems.append("the running request never reached the child")
            sampler.start()
            for i in range(3 * cap):
                big = _p_body("qh3-%d" % i, extra="x" * QH_LOOP_BODY)
                _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(big)) + big).close()
            if not w.settled(1, queued=0):
                problems.append("after the loop: admitted %d, queued %d, expected 1 and 0"
                                % (w.admitted(), w.queued()))
        finally:
            stop.set()
            if sampler.is_alive():
                sampler.join(JOIN_TIMEOUT_S)
            runner.close()
        if peak["admitted"] > cap:
            problems.append("the admission counter peaked at %d over the cap %d" % (peak["admitted"], cap))
        bound = cap * (QH_LOOP_BODY + 1024)
        if peak["bytes"] > bound:
            problems.append("held request bodies peaked at %d bytes, over the bound %d" % (peak["bytes"], bound))
        if found is not None and not pid_dead(found[0].get("pid")):
            problems.append("the running request's child survived its client's departure")
        if not w.settled(0):
            problems.append("admitted %d after the running client left, expected 0" % w.admitted())
        sent = len(_q_sends(sandbox, "A"))
        if sent != 1:
            problems.append("the child received %d requests, expected only the running one" % sent)
    return problems + w.problems, ["%d connect/send-body/disconnect clients behind a busy model: the "
                                   "counter and the held body bytes stay within the cap; the counter "
                                   "returns to 0 once the scheduler dropped the running ticket" % (3 * cap)]


def _qh_collect(socks, want, timeout):
    """{index: (bytes, eof)} for every socket; stops once *want* sockets reached EOF (plus a
    short settle, so an over-count is seen) or at *timeout*."""
    bufs = {i: [b"", False] for i in range(len(socks))}
    deadline = time.monotonic() + timeout
    settle = None
    while True:
        now = time.monotonic()
        done = sum(1 for b in bufs.values() if b[1])
        if done >= want and settle is None:
            settle = now + 0.5
        if now >= deadline or (settle is not None and now >= settle):
            break
        open_socks = [s for i, s in enumerate(socks) if not bufs[i][1]]
        if not open_socks:
            break
        ready, _w, _x = select.select(open_socks, [], [], 0.05)
        for sock in ready:
            i = socks.index(sock)
            try:
                data = sock.recv(65536)
            except OSError:
                data = b""
            if data:
                bufs[i][0] += data
            else:
                bufs[i][1] = True
    return {i: (b[0], b[1]) for i, b in bufs.items()}


@_q_live
def qh4(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}, cap=QH_RACE_CAP) as w:
        bodies = [_p_body("qh4-%d" % i) for i in range(QH_RACE_N)]
        socks = []
        try:
            for _ in range(QH_RACE_N):
                socks.append(socket.create_connection((LOOPBACK, w.port), timeout=WAIT_CEILING_S))
            barrier = threading.Barrier(QH_RACE_N)

            def send(i):
                try:
                    barrier.wait(WAIT_CEILING_S)
                except threading.BrokenBarrierError:
                    pass
                socks[i].sendall(_p_head(w.port, "POST", _p_m("A", P_GEN), length=len(bodies[i])))

            threads = [threading.Thread(target=send, args=(i,), daemon=True) for i in range(QH_RACE_N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(WAIT_CEILING_S)
            got = _qh_collect(socks, QH_RACE_N - QH_RACE_CAP, P_REFUSE_S)
            answered = [i for i, (_raw, eof) in got.items() if eof]
            if len(answered) != QH_RACE_N - QH_RACE_CAP:
                problems.append("%d of %d head-only requests were answered before their body, expected %d"
                                % (len(answered), QH_RACE_N, QH_RACE_N - QH_RACE_CAP))
            for i in answered:
                problems += _p_refused("over-cap request %d" % i, _PResp(*got[i]), "queue_full")
            if w.admitted() != QH_RACE_CAP:
                problems.append("admitted %d while the bodies are pending, expected %d"
                                % (w.admitted(), QH_RACE_CAP))
            for i in [i for i in range(QH_RACE_N) if i not in answered]:
                if got[i][0]:
                    problems.append("admitted request %d was answered before its body" % i)
                    continue
                socks[i].sendall(bodies[i])
                problems += _p_relayed("admitted request %d" % i, _PResp(*_p_read(socks[i])), bodies[i],
                                       sandbox, "A", P_GEN)
        finally:
            for sock in socks:
                sock.close()
        if not w.settled(0):
            problems.append("admitted %d at the end, expected 0" % w.admitted())
    sent = len(_q_sends(sandbox, "A"))
    if sent != QH_RACE_CAP:
        problems.append("the child received %d requests, expected %d" % (sent, QH_RACE_CAP))
    return problems + w.problems, ["%d concurrent heads over a cap of %d: exactly %d admitted, the rest "
                                   "503 queue_full + Retry-After: 1 with no body sent; the counter "
                                   "returns to 0" % (QH_RACE_N, QH_RACE_CAP, QH_RACE_CAP)]


GQH_FUNCS = (qh1, qh2, qh3, qh4)   # QH5 (racing unloads) is appended with group U below


def _p_group(suite, fixture_root, group, cases, funcs, required, label):
    mod, why = load_module(label)
    absent = missing_names(mod, required) if mod is not None else [why]
    for cid, fn in zip(cases, funcs):
        if absent:
            suite.record(group, cid, absent)
            continue
        sandbox = new_sandbox(fixture_root, "%s-%s" % (label, cid.split(" ", 1)[0].lower()))
        try:
            problems, detail = fn(mod, sandbox)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(group, cid, problems, detail=detail)


def group_p(suite, fixture_root):
    _p_group(suite, fixture_root, GP, GP_CASES, GP_FUNCS, P_NAMES, "p")


def group_q_http(suite, fixture_root):
    _p_group(suite, fixture_root, GQH, GQH_CASES, GQH_FUNCS, P_NAMES + Q_NAMES, "qh")


# ---------------------------------------------------------------------------
# Group U (+ Q-http QH5) -- the control routes /v1/models and /v1/unload
# ---------------------------------------------------------------------------
# In-process, the same front as group P (`_p_front`: Settings, a started
# Scheduler, _SdHttpServer on 127.0.0.1:0, the REAL stub child) and raw HTTP/1.1
# bytes.  Every case waits on Events / predicates with 10 s ceilings and checks
# processes by pid (`pid_alive` at the moment the answer arrived: SC-2).
#
# The contract these cases pin (task-030 the strict unload query splitter,
# task-031 the /v1/unload and /v1/models handlers; plan Step 6, FR-5, FR-6,
# D-3, D-5, D-14, D-16, D-17, H6, H8, S-L3, final M2, final L5, round-4
# security LOW a).  They replace the stub `_sd_control(handler, method, model,
# path, query)` (unit 8), which today answers every control path 404
# route_not_found.  The dispatch around it stays as it is: _sd_pipeline runs
# _precheck (bearer, header defects, Host, Origin) FIRST and only then hands a
# control target (bare or under /m/<x>/, split by _sd_control_target) to
# _sd_control -- so a control route is never answered before the bearer check
# (S-L3) and never takes an image reservation.  No new module-level name is
# gated: the splitter and the two handlers may be named freely (suggested:
# `_sd_unload_query(query) -> (model | None, force) | RouteError`, PURE, in unit
# 8 next to _sd_control).  Wire behaviour:
#
#   Routes and methods.  `/v1/models` takes GET only, `/v1/unload` and the D-5
#     alias `/m/<x>/v1/unload` take POST only; any other verb on them is 405
#     method_not_allowed (after the bearer, like the image routes).  Every
#     answer is closed-table JSON or the JSON below, Content-Type
#     application/json, our own Content-Length, Connection: close.
#   Control bodies (D-14).  A control request reads at most
#     _SD_CONTROL_BODY_LIMIT = 4096 body bytes: a declared Content-Length over
#     it is 413 body_too_large BEFORE any body byte is read (header-only, as the
#     image routes' 413).  The unload body must be empty (Content-Length 0) or
#     exactly `{}`; any other body is 400 invalid_request (the body is never a
#     model selector).  A GET /v1/models sends no Content-Length.  Never a
#     reservation: try_admit() is not called for a control route, so a full
#     image queue never refuses one (H8).
#   The strict unload query splitter (D-16; task-030).  The query (the raw text
#     after the first `?` of the request-line target, never self.path) is split
#     on `&`, each element on its first `=`; NO percent-decoding.  Allowed keys:
#     `model` and `force`, each at most once.  `force` must be exactly `1`;
#     `model` must match [A-Za-z0-9._~-]{1,64}.  A duplicate key, an element
#     without `=`, an empty value, ANY `%` in the query, an unknown key (e.g.
#     `token=`, `MODEL=`) or a value outside those forms is 400
#     invalid_request -- and nothing is unloaded.  A model name of valid form
#     that is not configured is 404 model_unknown (below).  An absent or empty
#     query (`/v1/unload`) is the default: no model, no force.
#   Target (final L5, round-4 LOW a).  `/m/<x>/v1/unload` names <x>; together
#     with `?model=<y>` the two must be EQUAL (`/m/A/v1/unload?model=A` is
#     accepted) or it is 400 invalid_request.  An unknown <x> or <y> -- with or
#     without force -- is 404 model_unknown, decided by the handler BEFORE
#     submit_unload, so it is never answered 200 {"unloaded": false}.  No name
#     at all = the default target (the resident model at the head).
#   Unload (FR-5, D-17).  The handler calls scheduler.submit_unload(target,
#     force) and waits on ticket.done (no timeout of its own besides the client
#     poll), then writes ticket.result: 200 {"unloaded": <bool>, "model": <name
#     | null>} exactly as the scheduler built it, or the closed-table refusal
#     (500 unload_failed, 503 shutting_down).  Non-force: queued FIFO behind
#     every earlier ticket, answered only when it reached the head and, for a
#     resident target, after the group is confirmed gone (U1, U2, U9).  Force:
#     the resident target is stopped at once, its running request answered 503
#     unload_forced (U3, U12); a non-resident target answers {"unloaded":
#     false} at once without touching the running request (U10).  An unload
#     re-enables a disabled model (U6).  Concurrent forces for one model share
#     ONE stop ladder and are all answered {"unloaded": true} (QH5).
#   /v1/models (FR-6, Section 3).  200 {"object": "list", "data": [entry per
#     configured model]}, each entry EXACTLY {"id", "object": "model",
#     "created": <int>, "owned_by": "gpu-router", "modality": "image", "state"}
#     with state "stopped" | "starting" | "ready" | "stopping" | "dead" |
#     "disabled" ("disabled" when the flag is set and the model is not
#     resident, else the ModelState name lower-cased), read from
#     scheduler.states().  No command, args, env, cwd, port or pid -- not as a
#     key, not as text (U7).
# Not pinned (plan gaps, left open on purpose): `/m/<x>/v1/models` (the plan
# names no prefixed models route; `_sd_control_target` dispatches it, its answer
# is free), a query on /v1/models, an unload POST with no Content-Length at all,
# and a Transfer-Encoding on a control route.

GU_CASES = (
    "U1 queued unload after the run",
    "U2 200 only after group gone",
    "U3 force aborts the running",
    "U4 unload of STOPPED, bodies",
    "U5 ?model= and /m/<x> alias",
    "U6 unload clears DISABLED",
    "U7 /v1/models hides internals",
    "U8 unauthenticated unload 401",
    "U9 unload of B queues behind A",
    "U10 force B leaves A running",
    "U11 flood: queue_full, models",
    "U12 flood: force via reserve",
    "U13 /v1/models needs a bearer",
    "U14 duplicate query key 400",
    "U15 empty / non-1 value 400",
    "U16 %, unknown key, bad name",
    "U17 unknown model 404",
    "U18 prefix vs ?model= clash",
)

GQH_CASES = GQH_CASES + ("QH5 racing unloads, one ladder",)

U_NAMES = P_NAMES + Q_NAMES
U_MODELS = "/v1/models"
U_UNLOAD = "/v1/unload"
U_CONTROL_BODY_LIMIT = 4096          # D-14: the largest control body
U_BUSY_S = 1.5                       # the stub's answer delay while an unload queues behind it
U_ENTRY_KEYS = frozenset(("id", "object", "created", "owned_by", "modality", "state"))
U_STATES = ("stopped", "starting", "ready", "stopping", "dead", "disabled")
U_FLOOD_CAP = 2                      # U11/U12: max_connections; the reserve keeps 4 more slots


def _u_head(port, method, target, length=None, ctype=None, **kw):
    """A control-route head: no Content-Type unless given, Content-Length only when given."""
    return _p_head(port, method, target, ctype=ctype, length=length, **kw)


def _u_open(port, target, body=b"", ctype=None, **kw):
    """A sent POST to a control route (the caller reads and closes the socket)."""
    return _p_open(port, _u_head(port, "POST", target, length=len(body), ctype=ctype, **kw) + body)


def _u_post(port, target, body=b"", ctype=None, timeout=WAIT_CEILING_S, **kw):
    return _p_exchange(port, _u_head(port, "POST", target, length=len(body), ctype=ctype, **kw) + body,
                       timeout)


def _u_get(port, target=U_MODELS, timeout=P_REFUSE_S, **kw):
    return _p_exchange(port, _u_head(port, "GET", target, **kw), timeout)


def _u_unloaded(label, resp, unloaded, model):
    """Problems unless *resp* is 200 {"unloaded": *unloaded*, "model": *model*}, framed."""
    want = {"unloaded": unloaded, "model": model}
    if resp.status != 200:
        return ["%s: answered %s, expected 200 %r" % (label, resp.describe(), want)]
    problems = _p_framing(label, resp)
    if (resp.header("content-type") or "").split(";")[0].strip() != P_JSON:
        problems.append("%s: the unload answer is not application/json" % label)
    if resp.json() != want:
        problems.append("%s: unload body %r, expected %r" % (label, resp.json(), want))
    return problems


def _u_models(label, resp):
    """(problems, {id: entry}) of a /v1/models answer checked against the entry shape."""
    if resp.status != 200:
        return ["%s: answered %s, expected 200 and the model list" % (label, resp.describe())], {}
    problems = _p_framing(label, resp)
    if (resp.header("content-type") or "").split(";")[0].strip() != P_JSON:
        problems.append("%s: the model list is not application/json" % label)
    doc = resp.json()
    if not isinstance(doc, dict) or set(doc) != {"object", "data"} or doc.get("object") != "list" \
            or not isinstance(doc.get("data"), list):
        return problems + ["%s: not {\"object\": \"list\", \"data\": [...]}" % label], {}
    entries = {}
    for entry in doc["data"]:
        if not isinstance(entry, dict):
            problems.append("%s: an entry is not an object" % label)
            continue
        if set(entry) != U_ENTRY_KEYS:
            problems.append("%s: entry keys %r, expected exactly %r" % (label, sorted(entry), sorted(U_ENTRY_KEYS)))
        created = entry.get("created")
        if (entry.get("object"), entry.get("owned_by"), entry.get("modality")) != ("model", "gpu-router", "image") \
                or not isinstance(created, int) or isinstance(created, bool) or created < 0 \
                or entry.get("state") not in U_STATES or not isinstance(entry.get("id"), str):
            problems.append("%s: entry %r is not {object model, owned_by gpu-router, modality image, "
                            "created int, state in %r}" % (label, entry.get("id"), U_STATES))
        if entry.get("id") in entries:
            problems.append("%s: model %r listed twice" % (label, entry.get("id")))
        entries[entry.get("id")] = entry
    return problems, entries


def _u_state(label, port, model, want):
    """Problems unless GET /v1/models lists *model* in state *want*."""
    problems, entries = _u_models(label, _u_get(port))
    if problems:
        return problems
    got = entries.get(model, {}).get("state")
    return [] if got == want else ["%s: /v1/models lists %s as %r, expected %r" % (label, model, got, want)]


def _u_refusals(port, targets, code, method="POST", **kw):
    """Problems for every control *target* not answered with the closed table's *code*."""
    problems = []
    for target in targets:
        resp = _p_exchange(port, _u_head(port, method, target, length=0 if method == "POST" else None, **kw),
                           P_REFUSE_S)
        # The label never carries the token sentinel a target may hold in its query.
        problems += _p_refused("%s %s" % (method, target.replace(TF_TOKEN, "<token>")), resp, code)
    return problems


def _u_intact(label, w, pid, model="A"):
    """Problems unless the refusals left *model* resident: its pid alive, state READY."""
    problems = []
    if not pid_alive(pid):
        problems.append("%s: %s's child (pid %d) is gone -- a refusal unloaded it" % (label, model, pid))
    if w.state(model) != "READY":
        problems.append("%s: %s is %s, expected READY (nothing unloaded)" % (label, model, w.state(model)))
    return problems


def _u_load(w, sandbox, name, tag):
    """(problems, pid): one relayed request that makes *name* resident, and its child pid."""
    body = _p_body(tag)
    problems = _p_relayed("loading %s" % name, _p_post(w.port, _p_m(name, P_GEN), body), body,
                          sandbox, name, P_GEN)
    sends = _q_sends(sandbox, name, body)
    return problems, (sends[0].get("pid") if sends else None)


class _UReader(threading.Thread):
    """Reads one socket to EOF on its own thread; `resp` and the monotonic `at` of the
    end, plus `alive`: whether *pid* still existed the moment the answer was complete."""

    def __init__(self, sock, pid=None, timeout=WAIT_CEILING_S):
        super().__init__(name="tf-u-reader", daemon=True)
        self.sock, self.pid, self.timeout = sock, pid, timeout
        self.resp, self.at, self.alive = None, None, None

    def run(self):
        raw, eof = _p_read(self.sock, self.timeout)
        self.at = time.monotonic()
        self.alive = pid_alive(self.pid) if self.pid is not None else None
        self.resp = _PResp(raw, eof)

    def result(self):
        self.join(self.timeout + JOIN_TIMEOUT_S)
        return self.resp if self.resp is not None else _PResp(b"", False)


@_q_live
def u1(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--answer-delay", str(U_BUSY_S))}) as w:
        run, after = _p_body("u1-run"), _p_body("u1-after")
        socks = []
        try:
            socks.append(_p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(run)) + run))
            first = wait_events(sandbox, "request", 1, "A")
            if first is None:
                return problems + ["the first request never reached the child"], []
            pid = first[0].get("pid")
            socks.append(_u_open(w.port, U_UNLOAD))
            if not w.settled(1, queued=1):
                problems.append("the unload is not queued behind the running request (admitted %d, queued %d)"
                                % (w.admitted(), w.queued()))
            socks.append(_p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(after)) + after))
            if not w.settled(2, queued=2):
                problems.append("the request behind the unload is not queued (admitted %d, queued %d)"
                                % (w.admitted(), w.queued()))
            early, _w, _x = select.select([socks[1]], [], [], 0)
            if early:
                problems.append("the queued unload answered while the request ahead of it was running")
            r_unload = _PResp(*_p_read(socks[1]))
            alive_at_answer = pid_alive(pid)
            behind_sent = len(_q_sends(sandbox, "A", after))
            r_run = _PResp(*_p_read(socks[0]))
            r_after = _PResp(*_p_read(socks[2]))
        finally:
            for sock in socks:
                sock.close()
        problems += _u_unloaded("the queued unload", r_unload, True, "A")
        if alive_at_answer:
            problems.append("the unload answered 200 while A's child (pid %d) was alive (SC-2)" % pid)
        if behind_sent:
            problems.append("the request queued behind the unload reached the child before the unload answered")
        problems += _p_relayed("the request ahead of the unload", r_run, run, sandbox, "A", P_GEN)
        problems += _p_relayed("the request behind the unload", r_after, after, sandbox, "A", P_GEN)
        behind = _q_sends(sandbox, "A", after)
        if behind and behind[0].get("pid") == pid:
            problems.append("the request behind the unload was served by the unloaded child")
        if len(read_starts(sandbox, "A")) != 2:
            problems.append("A started %d times, expected 2 (a cold start after the unload)"
                            % len(read_starts(sandbox, "A")))
        if not w.settled(0):
            problems.append("admitted %d at the end, expected 0" % w.admitted())
    return problems + w.problems, ["request, unload, request: the unload waited for the first, "
                                   "answered {unloaded: true} with the group gone, and the third "
                                   "cold-started A again (FIFO)"]


@_q_live
def u2(mod, sandbox, names, optional):
    names.append("A")
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--ignore-term")}) as w:
        problems, pid = _u_load(w, sandbox, "A", "u2")
        if pid is None:
            return problems + ["A never became resident"], []
        pids = read_pids(sandbox, "A")
        t0 = time.monotonic()
        resp = _u_post(w.port, U_UNLOAD + "?model=A")
        took = time.monotonic() - t0
        alive = [p for p in pids if pid_alive(p)]
        problems += _u_unloaded("the unload", resp, True, "A")
        if alive:
            problems.append("the unload answered 200 while pid(s) %r of A's group were alive (SC-2)" % alive)
        if resp.status == 200 and took < Q_GRACE_S * 0.9:
            problems.append("the unload of a SIGTERM-ignoring child answered after %.2fs, before its "
                            "%.1fs grace ran out -- it cannot have waited for the SIGKILL" % (took, Q_GRACE_S))
        if w.state("A") != "STOPPED":
            problems.append("A is %s after the unload, expected STOPPED" % w.state("A"))
        problems += _u_state("after the unload", w.port, "A", "stopped")
    return problems + w.problems, ["a SIGTERM-ignoring child: the unload answered after %.2fs, every "
                                   "pid of the group already gone" % took]


@_q_live
def u3(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--keep-generating")}) as w:
        body = _p_body("u3")
        runner = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(body)) + body)
        try:
            found = wait_events(sandbox, "generating", 1, "A")
            if found is None:
                return problems + ["the child never started generating"], []
            pid = found[0].get("pid")
            t0 = time.monotonic()
            resp = _u_post(w.port, U_UNLOAD + "?force=1")
            took = time.monotonic() - t0
            alive = pid_alive(pid)
            r_run = _PResp(*_p_read(runner))
        finally:
            runner.close()
        problems += _u_unloaded("the force", resp, True, "A")
        problems += _p_refused("the aborted request", r_run, "unload_forced")
        bound = Q_GRACE_S + mod._SD_KILL_STEP_S + S_SLACK_S
        if took > bound:
            problems.append("the force took %.2fs, bound %.2fs (SC-3)" % (took, bound))
        if alive:
            problems.append("the force answered 200 while A's child (pid %d) was alive" % pid)
        if w.crashes("A"):
            problems.append("the forced abort was charged as %d crash(es)" % w.crashes("A"))
        if len(_q_sends(sandbox, "A", body)) != 1:
            problems.append("the aborted request was re-sent")
        if not w.settled(0):
            problems.append("admitted %d at the end, expected 0" % w.admitted())
    return problems + w.problems, ["POST /v1/unload?force=1: 503 unload_forced for the running "
                                   "request, 200 {unloaded: true} after %.2fs with the child gone" % took]


@_q_live
def u4(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, target, body, ctype, model in (
                ("bare unload", U_UNLOAD, b"", None, None),
                ("bare unload again", U_UNLOAD, b"", None, None),
                ("bare force", U_UNLOAD + "?force=1", b"", None, None),
                ("?model=A with body {}", U_UNLOAD + "?model=A", b"{}", P_JSON, "A"),
                ("?model=A&force=1", U_UNLOAD + "?model=A&force=1", b"", None, "A")):
            problems += _u_unloaded(label, _u_post(w.port, target, body, ctype, timeout=P_REFUSE_S), False, model)
        problems += _p_refused("a body other than {}",
                               _u_post(w.port, U_UNLOAD, b'{"model": "A"}', P_JSON, timeout=P_REFUSE_S),
                               "invalid_request")
        problems += _p_refused("a control body over 4 KiB (declared, never sent)",
                               _p_early(w.port, U_UNLOAD, length=U_CONTROL_BODY_LIMIT + 1, ctype=None),
                               "body_too_large")
        if read_starts(sandbox, "A"):
            problems.append("an unload of a stopped model started it")
    return problems + w.problems, ["nothing resident: {unloaded: false, model: null}, idempotent, also "
                                   "forced; ?model=A names A; only an empty body or {} is taken, 4 KiB+1 "
                                   "is 413 unread"]


@_q_live
def u5(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    models = {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}
    with _p_front(mod, sandbox, models) as w:
        problems, pid = _u_load(w, sandbox, "A", "u5-1")
        if pid is None:
            return problems + ["A never became resident"], []
        for label, target in (("?model=B", U_UNLOAD + "?model=B"), ("/m/B/v1/unload", _p_m("B", U_UNLOAD))):
            problems += _u_unloaded(label, _u_post(w.port, target, timeout=P_REFUSE_S), False, "B")
            if not pid_alive(pid):
                problems.append("%s stopped A's child" % label)
        for i, (label, target) in enumerate((("/m/A/v1/unload", _p_m("A", U_UNLOAD)),
                                             ("?model=A", U_UNLOAD + "?model=A"),
                                             ("?force=1&model=A", U_UNLOAD + "?force=1&model=A"))):
            if i:
                more, pid = _u_load(w, sandbox, "A", "u5-%d" % (i + 1))
                problems += more
                if pid is None:
                    return problems + ["A did not become resident again"], []
            problems += _u_unloaded(label, _u_post(w.port, target), True, "A")
            if pid_alive(pid):
                problems.append("%s answered while A's child (pid %d) was alive" % (label, pid))
        if read_starts(sandbox, "B"):
            problems.append("an unload naming B started B")
        if len(read_starts(sandbox, "A")) != 3:
            problems.append("A started %d times, expected 3" % len(read_starts(sandbox, "A")))
    return problems + w.problems, ["?model=B and /m/B/v1/unload leave resident A alone ({unloaded: "
                                   "false, model: B}); /m/A/v1/unload, ?model=A and ?force=1&model=A "
                                   "unload A"]


@_q_live
def u6(mod, sandbox, names, optional):
    names.append("A")
    problems = []
    entry = _p_entry(sandbox, "A", "--crash-on-start-first", "1", retries=0)
    patches = (("_SD_RESTART_BUDGET", 1), ("_SD_PORT_TRIES", 1))
    with _p_front(mod, sandbox, {"A": entry}, patches=patches) as w:
        problems += _p_refused("the first start (budget 1)", _p_post(w.port, _p_m("A", P_GEN), _p_body("u6-1")),
                               "model_disabled")
        if not w.states()["A"].disabled:
            return problems + ["precondition: A is not disabled after its budget tripped"], []
        problems += _u_state("while disabled", w.port, "A", "disabled")
        starts = len(read_starts(sandbox, "A"))
        problems += _p_refused("a request while disabled", _p_post(w.port, _p_m("A", P_GEN), _p_body("u6-2")),
                               "model_disabled")
        if len(read_starts(sandbox, "A")) != starts:
            problems.append("a disabled model was started")
        problems += _u_unloaded("the unload", _u_post(w.port, U_UNLOAD + "?model=A"), False, "A")
        view = w.states()["A"]
        if view.disabled or w.crashes("A") or view.restart_at != 0.0:
            problems.append("after the unload: disabled=%s crashes=%d restart_at=%r"
                            % (view.disabled, w.crashes("A"), view.restart_at))
        state_problems, entries = _u_models("after the unload", _u_get(w.port))
        problems += state_problems
        if entries.get("A", {}).get("state") == "disabled":
            problems.append("/v1/models still lists A as disabled after the unload")
        again = _p_body("u6-3")
        problems += _p_relayed("a request after the unload", _p_post(w.port, _p_m("A", P_GEN), again), again,
                               sandbox, "A", P_GEN)
    return problems + w.problems, ["budget 1: a failed start disabled A (503 model_disabled, listed "
                                   "disabled, not started); POST /v1/unload?model=A re-enabled it"]


@_q_live
def u7(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    env = {"TF_SD_SECRET": P_ENV_VALUE}
    models = {"A": _p_entry(sandbox, "A", env=env), "B": _p_entry(sandbox, "B", env=env)}
    with _p_front(mod, sandbox, models) as w:
        problems, pid = _u_load(w, sandbox, "A", "u7")
        resp = _u_get(w.port)
        more, entries = _u_models("GET /v1/models", resp)
        problems += more
        if set(entries) != {"A", "B"}:
            problems.append("/v1/models lists %r, expected exactly A and B" % sorted(map(str, entries)))
        for model, want in (("A", "ready"), ("B", "stopped")):
            if model in entries and entries[model].get("state") != want:
                problems.append("%s is listed %r, expected %r" % (model, entries[model].get("state"), want))
        text = resp.body.decode("utf-8", "replace")
        for what, needle in (("the stub path", STUB), ("the state directory", sandbox),
                             ("an env value", P_ENV_VALUE), ("an env key", "TF_SD_SECRET"),
                             ("the port flag", STUB_PORT_FLAG), ("the token", TF_TOKEN)):
            if needle in text:
                problems.append("the model list carries %s" % what)
        problems += _u_refusals(w.port, (U_MODELS,), "method_not_allowed", method="POST")
        problems += _u_refusals(w.port, (U_UNLOAD,), "method_not_allowed", method="GET")
        if pid is not None and not pid_alive(pid):
            problems.append("listing the models stopped A")
    return problems + w.problems, ["GET /v1/models: {object: list, data: [id, object, created, owned_by, "
                                   "modality, state]} per model, no command/args/env/port/pid; POST "
                                   "/v1/models and GET /v1/unload are 405"]


@_q_live
def u8(mod, sandbox, names, optional):
    names.append("A")
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        problems, pid = _u_load(w, sandbox, "A", "u8")
        if pid is None:
            return problems + ["A never became resident"], []
        basic = "Basic " + base64.b64encode(("tf:" + TF_TOKEN).encode("ascii")).decode("ascii")
        for label, target, kw in (
                ("no Authorization", U_UNLOAD + "?force=1", {"token": None}),
                ("a wrong token", U_UNLOAD + "?force=1", {"auth": ("Bearer " + P_WRONG_TOKEN,)}),
                ("Basic with the token", U_UNLOAD + "?force=1", {"auth": (basic,)}),
                ("the token in the query", U_UNLOAD + "?force=1&token=" + TF_TOKEN, {"token": None}),
                ("the alias, no Authorization", _p_m("A", U_UNLOAD) + "?force=1", {"token": None})):
            resp = _p_exchange(w.port, _u_head(w.port, "POST", target, length=0, **kw), P_REFUSE_S)
            problems += _p_refused(label, resp, "unauthorized")
        problems += _u_intact("after the refused unloads", w, pid)
        problems += _u_unloaded("the authenticated force", _u_post(w.port, U_UNLOAD + "?force=1"), True, "A")
    return problems + w.problems, ["an unload without the bearer (absent, wrong, Basic, in the query, on "
                                   "the alias) is 401 and unloads nothing; with it, it unloads"]


@_q_live
def u9(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    problems = []
    models = {"A": _p_entry(sandbox, "A", "--answer-delay", str(U_BUSY_S)), "B": _p_entry(sandbox, "B")}
    with _p_front(mod, sandbox, models) as w:
        run = _p_body("u9")
        socks = []
        try:
            socks.append(_p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(run)) + run))
            first = wait_events(sandbox, "request", 1, "A")
            if first is None:
                return problems + ["A's request never reached the child"], []
            pid = first[0].get("pid")
            socks.append(_u_open(w.port, U_UNLOAD + "?model=B"))
            if not w.settled(1, queued=1):
                problems.append("the unload of B is not queued behind A's request (admitted %d, queued %d)"
                                % (w.admitted(), w.queued()))
            early, _w, _x = select.select([socks[1]], [], [], 0)
            if early:
                problems.append("the non-force unload of B answered at once instead of queueing (final M2)")
            r_run = _PResp(*_p_read(socks[0]))
            r_unload = _PResp(*_p_read(socks[1]))
        finally:
            for sock in socks:
                sock.close()
        problems += _p_relayed("A's request", r_run, run, sandbox, "A", P_GEN)
        problems += _u_unloaded("the unload of B", r_unload, False, "B")
        problems += _u_intact("after the unload of B", w, pid)
        if read_starts(sandbox, "B"):
            problems.append("the unload of B started B")
    return problems + w.problems, ["POST /v1/unload?model=B while A runs: queued behind A, answered "
                                   "{unloaded: false, model: B} at the head; A stays resident"]


@_q_live
def u10(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    problems = []
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    _q_control(control, "hang-after-accept")
    models = {"A": _p_entry(sandbox, "A", "--control-file", control, request_timeout=30.0),
              "B": _p_entry(sandbox, "B")}
    with _p_front(mod, sandbox, models) as w:
        body = _p_body("u10")
        runner = _p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(body)) + body)
        try:
            found = wait_events(sandbox, "request", 1, "A")
            if found is None:
                return problems + ["A's request never reached the child"], []
            pid = found[0].get("pid")
            resp = _u_post(w.port, U_UNLOAD + "?force=1&model=B", timeout=P_REFUSE_S)
            problems += _u_unloaded("force B", resp, False, "B")
            with w.sched.cond:
                running = w.sched.running
                touched = running is None or running.abort_reason is not None or running.cancelled.is_set()
            if touched:
                problems.append("force B touched A's running request")
            if not pid_alive(pid):
                problems.append("force B stopped A's child")
            if read_starts(sandbox, "B"):
                problems.append("force B started B")
            problems += _u_unloaded("force A (cleanup)", _u_post(w.port, U_UNLOAD + "?force=1&model=A"), True, "A")
            problems += _p_refused("A's aborted request", _PResp(*_p_read(runner)), "unload_forced")
        finally:
            runner.close()
    return problems + w.problems, ["?force=1&model=B while A runs: {unloaded: false, model: B} at once, "
                                   "A's request and child untouched"]


def _u_flood(w, sandbox, socks):
    """(problems, pid, queued body): cap = U_FLOOD_CAP image tickets held (one hung at the
    child, one queued; their sockets appended to *socks*), the image counter at the cap."""
    body, queued = _p_body("flood-run"), _p_body("flood-queued")
    socks.append(_p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(body)) + body))
    found = wait_events(sandbox, "request", 1, "A")
    if found is None:
        return ["the running request never reached the child"], None, queued
    socks.append(_p_open(w.port, _p_head(w.port, "POST", _p_m("A", P_GEN), length=len(queued)) + queued))
    if not w.settled(U_FLOOD_CAP, queued=1):
        return (["the flood did not fill the image cap (admitted %d, queued %d)" % (w.admitted(), w.queued())],
                found[0].get("pid"), queued)
    return [], found[0].get("pid"), queued


@_q_live
def u11(mod, sandbox, names, optional):
    names.append("A")
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    _q_control(control, "hang-after-accept")
    entry = _p_entry(sandbox, "A", "--control-file", control, request_timeout=30.0)
    with _p_front(mod, sandbox, {"A": entry}, cap=U_FLOOD_CAP) as w:
        socks = []
        try:
            problems, pid, _queued = _u_flood(w, sandbox, socks)
            if not problems:
                problems += _p_refused("an image request over the cap", _p_early(w.port, _p_m("A", P_GEN)),
                                       "queue_full")
                for i in (1, 2):
                    more, entries = _u_models("GET /v1/models during the flood (%d)" % i, _u_get(w.port))
                    problems += more
                    if entries and entries.get("A", {}).get("state") != "ready":
                        problems.append("A is listed %r during the flood, expected 'ready'"
                                        % entries.get("A", {}).get("state"))
                if w.admitted() != U_FLOOD_CAP:
                    problems.append("the control requests moved the image counter to %d" % w.admitted())
            _q_control(control, "ok")
            force = w.sched.submit_unload("A", True)
            problems += _q_done("the cleanup force", force)
            for sock in socks:
                _p_read(sock)
        finally:
            for sock in socks:
                sock.close()
    return problems + w.problems, ["image cap %d full: one more image request is 503 queue_full at "
                                   "once, GET /v1/models still answers through the control reserve"
                                   % U_FLOOD_CAP]


@_q_live
def u12(mod, sandbox, names, optional):
    names.append("A")
    control = os.path.join(sandbox, "A.control")
    WRITES.append(control)
    _q_control(control, "hang-after-accept")
    entry = _p_entry(sandbox, "A", "--control-file", control, request_timeout=30.0)
    with _p_front(mod, sandbox, {"A": entry}, cap=U_FLOOD_CAP) as w:
        socks = []
        took = 0.0
        try:
            problems, pid, queued = _u_flood(w, sandbox, socks)
            if not problems:
                _q_control(control, "ok")
                t0 = time.monotonic()
                resp = _u_post(w.port, U_UNLOAD + "?force=1")
                took = time.monotonic() - t0
                alive = pid_alive(pid)
                problems += _u_unloaded("the force during the flood", resp, True, "A")
                bound = Q_GRACE_S + mod._SD_KILL_STEP_S + S_SLACK_S
                if took > bound:
                    problems.append("the force took %.2fs, bound %.2fs" % (took, bound))
                if alive:
                    problems.append("the force answered while A's child (pid %d) was alive" % pid)
                problems += _p_refused("the running request", _PResp(*_p_read(socks[0])), "unload_forced")
                problems += _p_relayed("the queued request (D-3: cold-starts again)", _PResp(*_p_read(socks[1])),
                                       queued, sandbox, "A", P_GEN)
                if len(read_starts(sandbox, "A")) != 2:
                    problems.append("A started %d times, expected 2" % len(read_starts(sandbox, "A")))
                if not w.settled(0):
                    problems.append("admitted %d at the end, expected 0" % w.admitted())
            else:
                w.sched.submit_unload("A", True).done.wait(WAIT_CEILING_S)
        finally:
            for sock in socks:
                sock.close()
    return problems + w.problems, ["image cap %d full: POST /v1/unload?force=1 still got a slot, "
                                   "unloaded A in %.2fs; the queued request cold-started A again"
                                   % (U_FLOOD_CAP, took)]


@_q_live
def u13(mod, sandbox, names, optional):
    optional.append("A")
    problems = []
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        for label, kw, target in (
                ("no Authorization", {"token": None}, U_MODELS),
                ("Basic", {"auth": ("Basic dGY6dGY=",)}, U_MODELS),
                ("a wrong token", {"auth": ("Bearer " + P_WRONG_TOKEN,)}, U_MODELS),
                ("two spaces", {"auth": ("Bearer  " + TF_TOKEN,)}, U_MODELS),
                ("the bare scheme", {"auth": ("Bearer",)}, U_MODELS),
                ("two Authorization headers", {"auth": ("Bearer " + TF_TOKEN,) * 2}, U_MODELS),
                ("the token in the query", {"token": None}, U_MODELS + "?token=" + TF_TOKEN)):
            problems += _p_refused(label, _u_get(w.port, target, **kw), "unauthorized")
        more, entries = _u_models("GET /v1/models with the bearer", _u_get(w.port))
        problems += more
        if entries and entries.get("A", {}).get("state") != "stopped":
            problems.append("A is listed %r, expected 'stopped'" % entries.get("A", {}).get("state"))
        if read_starts(sandbox, "A"):
            problems.append("listing the models started A")
    return problems + w.problems, ["GET /v1/models is 401 without the exact bearer (S-L3) and lists "
                                   "the models with it"]


def _u_query_case(mod, sandbox, names, tag, targets, code, detail):
    """A loaded A, then every target refused with *code*; A must stay resident."""
    names.append("A")
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A")}) as w:
        problems, pid = _u_load(w, sandbox, "A", tag)
        if pid is None:
            return problems + ["A never became resident"], []
        problems += _u_refusals(w.port, targets, code)
        problems += _u_intact("after the refusals", w, pid)
        problems += _u_unloaded("a well-formed unload after them", _u_post(w.port, U_UNLOAD + "?model=A"),
                                True, "A")
    return problems + w.problems, [detail]


@_q_live
def u14(mod, sandbox, names, optional):
    return _u_query_case(mod, sandbox, names, "u14", (
        U_UNLOAD + "?model=A&model=A",
        U_UNLOAD + "?force=1&force=1",
        U_UNLOAD + "?force=1&model=A&force=1",
        _p_m("A", U_UNLOAD) + "?model=A&model=A"), "invalid_request",
        "a repeated model= or force= is 400 invalid_request and unloads nothing (D-16)")


@_q_live
def u15(mod, sandbox, names, optional):
    return _u_query_case(mod, sandbox, names, "u15", (
        U_UNLOAD + "?model=",
        U_UNLOAD + "?force=",
        U_UNLOAD + "?model=A&force=",
        U_UNLOAD + "?force",
        U_UNLOAD + "?force=0",
        U_UNLOAD + "?force=true",
        U_UNLOAD + "?force=1&",
        U_UNLOAD + "?&model=A"), "invalid_request",
        "an empty value or element, a bare key, force other than exactly 1: 400 invalid_request (D-16)")


@_q_live
def u16(mod, sandbox, names, optional):
    return _u_query_case(mod, sandbox, names, "u16", (
        U_UNLOAD + "?model=%41",
        U_UNLOAD + "?force=%31",
        U_UNLOAD + "?model=A%26force%3D1",
        U_UNLOAD + "?model%3DA",
        U_UNLOAD + "?foo=1",
        U_UNLOAD + "?model=A&foo=1",
        U_UNLOAD + "?MODEL=A",
        U_UNLOAD + "?token=" + TF_TOKEN,
        U_UNLOAD + "?model=A+B",
        U_UNLOAD + "?model=A=B",
        U_UNLOAD + "?model=" + "a" * 65), "invalid_request",
        "no percent-decoding (any % is 400), unknown keys and names outside [A-Za-z0-9._~-]{1,64} "
        "are 400 invalid_request (D-16)")


@_q_live
def u17(mod, sandbox, names, optional):
    return _u_query_case(mod, sandbox, names, "u17", (
        U_UNLOAD + "?model=nosuch",
        U_UNLOAD + "?model=nosuch&force=1",
        U_UNLOAD + "?force=1&model=nosuch",
        _p_m("nosuch", U_UNLOAD),
        _p_m("nosuch", U_UNLOAD) + "?force=1"), "model_unknown",
        "an unknown model by query or by prefix, with or without force: 404 model_unknown, never "
        "200 {unloaded: false}")


@_q_live
def u18(mod, sandbox, names, optional):
    names.append("A")
    optional.append("B")
    models = {"A": _p_entry(sandbox, "A"), "B": _p_entry(sandbox, "B")}
    with _p_front(mod, sandbox, models) as w:
        problems, pid = _u_load(w, sandbox, "A", "u18")
        if pid is None:
            return problems + ["A never became resident"], []
        problems += _u_refusals(w.port, (_p_m("A", U_UNLOAD) + "?model=B",
                                         _p_m("B", U_UNLOAD) + "?model=A",
                                         _p_m("A", U_UNLOAD) + "?model=B&force=1"), "invalid_request")
        problems += _u_intact("after the clashing names", w, pid)
        problems += _u_unloaded("/m/A/v1/unload?model=A", _u_post(w.port, _p_m("A", U_UNLOAD) + "?model=A"),
                                True, "A")
        if pid_alive(pid):
            problems.append("/m/A/v1/unload?model=A answered while A's child was alive")
        if read_starts(sandbox, "B"):
            problems.append("a clashing unload started B")
    return problems + w.problems, ["/m/A/v1/unload?model=B (either way round, with or without force) is "
                                   "400 invalid_request; the same name twice is accepted (final L5)"]


GU_FUNCS = (u1, u2, u3, u4, u5, u6, u7, u8, u9, u10, u11, u12, u13, u14, u15, u16, u17, u18)


@_q_live
def qh5(mod, sandbox, names, optional):
    names.append("A")

    class _URecSupervisor(mod.Supervisor):
        """The real Supervisor, counting stop ladders."""

        def __init__(self):
            super().__init__()
            self.stops = []

        def stop(self, child):
            self.stops.append(child.pid)
            return super().stop(child)

    sup = _URecSupervisor()
    with _p_front(mod, sandbox, {"A": _p_entry(sandbox, "A", "--ignore-term")}, supervisor=sup) as w:
        problems, pid = _u_load(w, sandbox, "A", "qh5")
        if pid is None:
            return problems + ["A never became resident"], []
        targets = (U_UNLOAD + "?force=1", _p_m("A", U_UNLOAD) + "?force=1")
        socks, readers = [], []
        try:
            for _ in targets:
                socks.append(socket.create_connection((LOOPBACK, w.port), timeout=WAIT_CEILING_S))
            barrier = threading.Barrier(len(targets))

            def send(i):
                try:
                    barrier.wait(WAIT_CEILING_S)
                except threading.BrokenBarrierError:
                    pass
                socks[i].sendall(_u_head(w.port, "POST", targets[i], length=0))

            senders = [threading.Thread(target=send, args=(i,), daemon=True) for i in range(len(targets))]
            readers = [_UReader(sock, pid) for sock in socks]
            for thread in senders + readers:
                thread.start()
            for thread in senders:
                thread.join(WAIT_CEILING_S)
            results = [reader.result() for reader in readers]
        finally:
            for sock in socks:
                sock.close()
        for target, reader, resp in zip(targets, readers, results):
            problems += _u_unloaded("racing %s" % target, resp, True, "A")
            if reader.alive:
                problems.append("racing %s answered while A's child (pid %d) was alive" % (target, pid))
        if len(sup.stops) != 1:
            problems.append("%d stop ladder(s) ran for the two racing unloads, expected exactly 1"
                            % len(sup.stops))
        if w.crashes("A"):
            problems.append("the racing unloads charged %d crash(es)" % w.crashes("A"))
        if w.state("A") != "STOPPED":
            problems.append("A is %s after the racing unloads, expected STOPPED" % w.state("A"))
    return problems + w.problems, ["two forced unloads raced over HTTP against a SIGTERM-ignoring "
                                   "child: one stop ladder, both answered {unloaded: true} with the "
                                   "group gone"]


GQH_FUNCS = GQH_FUNCS + (qh5,)


def group_u(suite, fixture_root):
    _p_group(suite, fixture_root, GU, GU_CASES, GU_FUNCS, U_NAMES, "u")


# ---------------------------------------------------------------------------
# Group G -- the injected front policy pin (ADR 0029)
# ---------------------------------------------------------------------------
# The per-host pin llm-router carries as J40 / J41 and mcp-proxy as K5: the
# HTTP front's members are generated from Scripts/_mcp_httpfront.py and are the
# same in every host, so the policy they apply reaches them INJECTED by the host
# -- never as a host global read inside a generated member.  G1-G4 are AST rules
# over Scripts/gpu-router.py, G5 checks the same wiring on a privately loaded
# module (constants patched, a real listener bound on 127.0.0.1:0), G6 is the
# planted control: a small clean front passes all four rules and a deliberately
# wrong one fails each of them, so a rule that went blind is a red case.
#
# The contract these cases pin (task-032; plan Step 8, H4, H8, ADR 0029 PD-1..PD-7):
#   Token floor (H4).  `_SD_TOKEN_MIN_LEN = 32` is bound ONCE at module level as
#     the literal 32, and every `_http_token_value(...)` call passes exactly four
#     positional arguments, the 3rd `_SD_TOKEN_MIN_LEN` and the 4th
#     `ConfigError` (never a literal floor, never another error class).  Live:
#     the floor is read at call time (a patched 40 refuses a 33-character token).
#   Connection cap (H8).  `_SdHttpServer.__init__` assigns `self.conn_sem =
#     threading.BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE)`
#     exactly once, BEFORE `super().__init__` binds; `conn_sem` is assigned
#     nowhere else; `_SD_CONTROL_RESERVE` is a positive int literal.  Live: the
#     semaphore admits exactly max_connections + reserve, the reserve read at
#     construction time.
#   Header deadline.  `_SD_HEADER_TIMEOUT_S` is a positive literal not above
#     `_SD_SOCKET_TIMEOUT_S`; `_SdHandler` holds the class-level `timeout =
#     _SD_HEADER_TIMEOUT_S`; every `_HeaderDeadlineReader(...)` call passes two
#     positional arguments, the cap `_SD_SOCKET_TIMEOUT_S`; `handle_one_request`
#     reads `_SD_HEADER_TIMEOUT_S` at call time and sets the reader's `deadline`.
#   Logger and no host global (PD-3, PD-7).  `_SdHttpServer` holds the class-level
#     `front_log = log`; the generated server_bind / process_request /
#     handle_error read no `log`, the generated parse_request no
#     `_SD_HEADER_TIMEOUT_S`; no other class attribute of either class snapshots
#     an `_SD_*` / `_HTTP_*` constant at class definition.
# Not pinned here: the generated regions' bytes (tests/test_generated_region.py
# and amalgamate --check own them) and the HTTPFRONT_ADAPTATIONS rows.

GG_CASES = (
    "G1 token floor 32 injected",
    "G2 cap = max_conn + reserve",
    "G3 header deadline wiring",
    "G4 front_log, no host global",
    "G5 live: cap, floor, timeout",
    "G6 control: planted policy",
)

G_SERVER = "_SdHttpServer"
G_HANDLER = "_SdHandler"
G_TOKEN_MIN = "_SD_TOKEN_MIN_LEN"
G_TOKEN_FLOOR = 32                   # H4
G_ERROR = "ConfigError"
G_RESERVE = "_SD_CONTROL_RESERVE"
G_HEADER = "_SD_HEADER_TIMEOUT_S"
G_CAP = "_SD_SOCKET_TIMEOUT_S"
G_GLOBAL_READS = ((G_SERVER, ("server_bind", "process_request", "handle_error"), "log"),
                  (G_HANDLER, ("parse_request",), G_HEADER))
G_POLICY_PREFIXES = ("_SD_", "_HTTP_")
G_LIVE_CAP = 3                       # G5: max_connections of the live listener
G_LIVE_RESERVE = 1                   # G5: the patched reserve
G_LIVE_FLOOR = 40                    # G5: the patched floor
G_LIVE_TOKEN = "abcdefghijklmnopqrstuvwxyz0123456"   # 33 distinct characters
G_NAMES = ("_SdHttpServer", "_SdHandler", "_sd_cfg_token", "ConfigError", "log",
           "_SD_TOKEN_MIN_LEN", "_SD_CONTROL_RESERVE", "_SD_HEADER_TIMEOUT_S")


def _g_read_source():
    """(text, why): Scripts/gpu-router.py's text, or (None, reason)."""
    if not os.path.isfile(SERVER):
        return None, "Scripts/gpu-router.py does not exist"
    with open(SERVER, "r", encoding="utf-8") as fh:
        return fh.read(), None


def _g_parse(source):
    """(tree, found): the parsed *source*, or (None, [the parse failure])."""
    try:
        return ast.parse(source), []
    except SyntaxError as exc:
        return None, ["the source does not parse: line %s" % exc.lineno]


def _g_is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _g_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _g_method(cls, name):
    for stmt in cls.body if cls is not None else ():
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _g_module_values(tree, name):
    """The value of every module-level binding of *name*."""
    values = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(_g_is_name(t, name) for t in stmt.targets):
            values.append(stmt.value)
        elif isinstance(stmt, ast.AnnAssign) and _g_is_name(stmt.target, name) and stmt.value is not None:
            values.append(stmt.value)
    return values


def _g_literal(tree, name, found, kinds=(int, float)):
    """The one module-level numeric literal bound to *name*, or None (a finding)."""
    values = _g_module_values(tree, name)
    if len(values) != 1:
        found.append("%s is bound %d time(s) at module level, expected once" % (name, len(values)))
        return None
    value = values[0]
    if not (isinstance(value, ast.Constant) and type(value.value) in kinds):
        found.append("%s is not a numeric literal" % name)
        return None
    return value.value


def _g_class_assign(cls, target, value):
    return any(isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
               and _g_is_name(stmt.targets[0], target) and _g_is_name(stmt.value, value)
               for stmt in cls.body)


def _g_calls(tree, name):
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _g_is_name(node.func, name)]


def rule_g_token(source):
    """G1 (H4): the floor is the literal 32, passed in at every _http_token_value call."""
    tree, found = _g_parse(source)
    if tree is None:
        return found
    floor = _g_literal(tree, G_TOKEN_MIN, found, kinds=(int,))
    if floor is not None and floor != G_TOKEN_FLOOR:
        found.append("%s is %r, expected the literal %d (H4)" % (G_TOKEN_MIN, floor, G_TOKEN_FLOOR))
    calls = _g_calls(tree, "_http_token_value")
    if not calls:
        found.append("no _http_token_value call: the floor is never injected")
    for call in calls:
        if len(call.args) != 4 or call.keywords:
            what = "%d args" % (len(call.args) + len(call.keywords))
        elif _g_is_name(call.args[2], G_TOKEN_MIN) and _g_is_name(call.args[3], G_ERROR):
            continue
        else:
            what = "%s, %s" % (ast.dump(call.args[2]), ast.dump(call.args[3]))
        found.append("%d: _http_token_value called with %s, expected %s, %s"
                     % (call.lineno, what, G_TOKEN_MIN, G_ERROR))
    return found


def _g_is_cap_sum(node):
    """True for `<x>.max_connections + _SD_CONTROL_RESERVE` (either order)."""
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return False
    pair = (node.left, node.right)
    has_cap = any(isinstance(n, ast.Attribute) and n.attr == "max_connections" for n in pair)
    has_reserve = any(_g_is_name(n, G_RESERVE) for n in pair)
    return has_cap and has_reserve


def _g_conn_sem_assigns(node):
    return [stmt for stmt in ast.walk(node) if isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "conn_sem" for t in stmt.targets)]


def rule_g_cap(source):
    """G2 (H8): conn_sem = BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE),
    once, in _SdHttpServer.__init__ before the bind."""
    tree, found = _g_parse(source)
    if tree is None:
        return found
    reserve = _g_literal(tree, G_RESERVE, found, kinds=(int,))
    if reserve is not None and reserve < 1:
        found.append("%s is %r, expected a positive slot count" % (G_RESERVE, reserve))
    server = _g_class(tree, G_SERVER)
    init = _g_method(server, "__init__")
    if init is None:
        found.append("no %s.__init__" % G_SERVER)
        return found
    assigns = _g_conn_sem_assigns(init)
    if len(assigns) != 1:
        found.append("%s.__init__ assigns conn_sem %d time(s), expected once" % (G_SERVER, len(assigns)))
    for stmt in assigns:
        value = stmt.value
        ok = (isinstance(value, ast.Call) and len(value.args) == 1 and not value.keywords
              and ((isinstance(value.func, ast.Attribute) and value.func.attr == "BoundedSemaphore")
                   or _g_is_name(value.func, "BoundedSemaphore"))
              and _g_is_cap_sum(value.args[0]))
        if not ok:
            found.append("%d: conn_sem is not BoundedSemaphore(settings.max_connections + %s)"
                         % (stmt.lineno, G_RESERVE))
    supers = [node.lineno for node in ast.walk(init) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == "__init__"
              and isinstance(node.func.value, ast.Call) and _g_is_name(node.func.value.func, "super")]
    if assigns and supers and assigns[0].lineno > min(supers):
        found.append("conn_sem is assigned after super().__init__ binds the listener")
    elsewhere = [stmt.lineno for stmt in _g_conn_sem_assigns(tree) if stmt not in assigns]
    for line in elsewhere:
        found.append("%d: conn_sem is reassigned outside %s.__init__" % (line, G_SERVER))
    return found


def rule_g_deadline(source):
    """G3: the header bound is the handler's class timeout, the reader's cap the socket
    timeout, and handle_one_request reads the bound at call time."""
    tree, found = _g_parse(source)
    if tree is None:
        return found
    header = _g_literal(tree, G_HEADER, found)
    cap = _g_literal(tree, G_CAP, found)
    if header is not None and header <= 0:
        found.append("%s is not positive" % G_HEADER)
    if header is not None and cap is not None and header > cap:
        found.append("%s is above %s" % (G_HEADER, G_CAP))
    handler = _g_class(tree, G_HANDLER)
    if handler is None:
        found.append("no class %s" % G_HANDLER)
        return found
    if not _g_class_assign(handler, "timeout", G_HEADER):
        found.append("%s: no class-level timeout = %s" % (G_HANDLER, G_HEADER))
    readers = _g_calls(tree, "_HeaderDeadlineReader")
    if not readers:
        found.append("no _HeaderDeadlineReader call: the header deadline is not wired")
    for call in readers:
        if len(call.args) != 2 or call.keywords:
            what = "%d args" % (len(call.args) + len(call.keywords))
        elif not _g_is_name(call.args[1], G_CAP):
            what = ast.dump(call.args[1])
        else:
            continue
        found.append("%d: _HeaderDeadlineReader cap is %s, expected %s" % (call.lineno, what, G_CAP))
    one = _g_method(handler, "handle_one_request")
    if one is None:
        found.append("%s has no handle_one_request" % G_HANDLER)
        return found
    if not any(_g_is_name(node, G_HEADER) for node in ast.walk(one)):
        found.append("handle_one_request does not read %s at call time" % G_HEADER)
    if not any(isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "deadline"
                                                    for t in node.targets)
               for node in ast.walk(one)):
        found.append("handle_one_request sets no reader deadline")
    return found


def rule_g_logger(source):
    """G4 (PD-3, PD-7): front_log = log; no host global read in a generated member; no
    other class attribute snapshots an _SD_* / _HTTP_* constant."""
    tree, found = _g_parse(source)
    if tree is None:
        return found
    classes = {name: _g_class(tree, name) for name in (G_SERVER, G_HANDLER)}
    for name, cls in classes.items():
        if cls is None:
            found.append("no class %s" % name)
    server, handler = classes[G_SERVER], classes[G_HANDLER]
    if server is not None and not _g_class_assign(server, "front_log", "log"):
        found.append("%s: no class-level front_log = log" % G_SERVER)
    for cls_name, members, glob in G_GLOBAL_READS:
        cls = classes.get(cls_name)
        for member in members:
            fn = _g_method(cls, member)
            if fn is None:
                continue
            for node in ast.walk(fn):
                if _g_is_name(node, glob) and isinstance(node.ctx, ast.Load):
                    found.append("%d: %s.%s reads the host global %s" % (node.lineno, cls_name, member, glob))
    for cls in (server, handler):
        if cls is None:
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            else:
                continue
            reads = sorted({node.id for node in ast.walk(value)
                            if isinstance(node, ast.Name) and node.id.startswith(G_POLICY_PREFIXES)})
            if not reads:
                continue
            if cls is handler and len(targets) == 1 and _g_is_name(targets[0], "timeout") \
                    and _g_is_name(value, G_HEADER):
                continue
            shown = ", ".join(t.id if isinstance(t, ast.Name) else type(t).__name__ for t in targets)
            found.append("%d: class attribute %s snapshots %s at class definition (PD-7)"
                         % (stmt.lineno, shown, ", ".join(reads)))
    return found


def _g_rule_case(rule, scope):
    def case(text):
        return rule(text), ["scope       : Scripts/gpu-router.py: %s" % scope]
    return case


g1 = _g_rule_case(rule_g_token, "every _http_token_value call, %s" % G_TOKEN_MIN)
g2 = _g_rule_case(rule_g_cap, "%s.__init__ conn_sem, %s" % (G_SERVER, G_RESERVE))
g3 = _g_rule_case(rule_g_deadline, "%s.timeout, handle_one_request, every _HeaderDeadlineReader"
                  % G_HANDLER)
g4 = _g_rule_case(rule_g_logger, "%s, %s class bodies and generated members" % (G_SERVER, G_HANDLER))


def _g_capacity(sem):
    """How many non-blocking acquires *sem* grants (every one released again)."""
    taken = 0
    while taken < 4096 and sem.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        sem.release()
    return taken


def _g_live_cap(mod, problems):
    """The live listener's semaphore capacity, the reserve read at construction time."""
    settings = types.SimpleNamespace(max_connections=G_LIVE_CAP)
    with patched(mod, [(G_RESERVE, G_LIVE_RESERVE)]):
        srv = mod._SdHttpServer((LOOPBACK, 0), settings, None)
        try:
            got = _g_capacity(srv.conn_sem)
        finally:
            srv.server_close()
    want = G_LIVE_CAP + G_LIVE_RESERVE
    if got != want:
        problems.append("conn_sem admits %d, expected max_connections %d + patched reserve %d"
                        % (got, G_LIVE_CAP, G_LIVE_RESERVE))
    return got


def g5(text):
    del text
    mod, why = load_module("g")
    if mod is None:
        return [why], []
    problems = missing_names(mod, G_NAMES)
    if problems:
        return problems, []
    if mod._SD_TOKEN_MIN_LEN != G_TOKEN_FLOOR:
        problems.append("_SD_TOKEN_MIN_LEN is %r, expected %d" % (mod._SD_TOKEN_MIN_LEN, G_TOKEN_FLOOR))
    try:
        mod._sd_cfg_token(G_LIVE_TOKEN, "config")
    except mod.ConfigError:
        problems.append("a 33-character token is refused at the default floor")
    with patched(mod, [(G_TOKEN_MIN, G_LIVE_FLOOR)]):
        try:
            mod._sd_cfg_token(G_LIVE_TOKEN, "config")
            problems.append("a patched floor of %d still accepts 33 characters: the floor is "
                            "not read at call time" % G_LIVE_FLOOR)
        except mod.ConfigError:
            pass
    got = _g_live_cap(mod, problems)
    if mod._SdHandler.timeout != mod._SD_HEADER_TIMEOUT_S:
        problems.append("_SdHandler.timeout is not _SD_HEADER_TIMEOUT_S")
    if mod._SdHttpServer.front_log is not mod.log:
        problems.append("_SdHttpServer.front_log is not the module's log")
    return problems, ["floor       : %d (a patched %d refuses 33 characters)" % (G_TOKEN_FLOOR, G_LIVE_FLOOR),
                      "cap         : %d slots for max_connections %d + reserve %d"
                      % (got, G_LIVE_CAP, G_LIVE_RESERVE)]


G_CLEAN = '''_SD_TOKEN_MIN_LEN = 32
_SD_CONTROL_RESERVE = 4
_SD_HEADER_TIMEOUT_S = 5.0
_SD_SOCKET_TIMEOUT_S = 30.0


def _sd_cfg_token(token, where):
    _http_token_value(token, where, _SD_TOKEN_MIN_LEN, ConfigError)
    return token


class _SdHttpServer(ThreadingHTTPServer):
    front_log = log

    def __init__(self, server_address, settings, scheduler):
        self.settings = settings
        self.conn_sem = threading.BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE)
        super().__init__(server_address, _SdHandler)

    def server_bind(self):
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        self.front_log.warning("refused: %d open", self.settings.max_connections)

    def handle_error(self, request, client_address):
        self.front_log.warning("failed: %s", "T")


class _SdHandler(BaseHTTPRequestHandler):
    timeout = _SD_HEADER_TIMEOUT_S

    def setup(self):
        self._header_reader = _HeaderDeadlineReader(self.connection, _SD_SOCKET_TIMEOUT_S)

    def handle_one_request(self):
        bound = _SD_HEADER_TIMEOUT_S
        self._header_reader.deadline = time.monotonic() + bound

    def parse_request(self):
        self.connection.settimeout(self.timeout)
'''
# G6: a literal floor (and a lowered constant), the reserve left out of the cap, the
# reader capped by the header bound, the bound frozen in handle_one_request, the
# logger rebound away from the host's, the global read in handle_error and a second
# policy class attribute (PD-7).
G_PLANTED = G_CLEAN.replace(
    "_SD_TOKEN_MIN_LEN = 32\n", "_SD_TOKEN_MIN_LEN = 16\n",
).replace(
    "_http_token_value(token, where, _SD_TOKEN_MIN_LEN, ConfigError)",
    "_http_token_value(token, where, 16, ConfigError)",
).replace(
    "BoundedSemaphore(settings.max_connections + _SD_CONTROL_RESERVE)",
    "BoundedSemaphore(settings.max_connections)",
).replace(
    "_HeaderDeadlineReader(self.connection, _SD_SOCKET_TIMEOUT_S)",
    "_HeaderDeadlineReader(self.connection, _SD_HEADER_TIMEOUT_S)",
).replace(
    "        bound = _SD_HEADER_TIMEOUT_S\n", "        bound = 5.0\n",
).replace(
    "    front_log = log\n", "    front_log = logging.getLogger(\"x\")\n",
).replace(
    "        self.front_log.warning(\"failed: %s\", \"T\")\n", "        log.warning(\"failed: %s\", \"T\")\n",
).replace(
    "    timeout = _SD_HEADER_TIMEOUT_S\n", "    timeout = _SD_HEADER_TIMEOUT_S\n    body_s = _SD_BODY_DEADLINE_S\n",
)
G_CONTROL = ((rule_g_token, ("expected the literal 32", "_http_token_value called with")),
             (rule_g_cap, ("conn_sem is not BoundedSemaphore",)),
             (rule_g_deadline, ("_HeaderDeadlineReader cap is", "handle_one_request does not read")),
             (rule_g_logger, ("no class-level front_log = log", "handle_error reads the host global log",
                              "class attribute body_s snapshots")))


def g6(text, rules=None):
    """The planted control: every rule is silent on G_CLEAN and reports each needle on
    G_PLANTED.  *rules* replaces the rule table so a red run can hand in a blind rule."""
    del text
    problems = []
    if G_PLANTED == G_CLEAN:
        return ["the plant did not change the input (an anchor drifted)"], []
    shown = 0
    for rule, needles in (rules or G_CONTROL):
        clean = rule(G_CLEAN)
        if clean:
            problems.append("%s reports the clean front: %s" % (rule.__name__, clean[0][:160]))
        planted = rule(G_PLANTED)
        for needle in needles:
            if not any(needle in finding for finding in planted):
                problems.append("%s misses the plant %r" % (rule.__name__, needle))
            else:
                shown += 1
    return problems, ["control     : %d plant(s) caught by %d rule(s); the clean front passes"
                      % (shown, len(rules or G_CONTROL))]


GG_FUNCS = (g1, g2, g3, g4, g5, g6)
G_NEEDS_SOURCE = frozenset((g1, g2, g3, g4))


def group_g(suite, fixture_root):
    del fixture_root
    text, why = _g_read_source()
    for cid, fn in zip(GG_CASES, GG_FUNCS):
        if fn in G_NEEDS_SOURCE and text is None:
            suite.record(GG, cid, [why])
            continue
        try:
            problems, detail = fn(text)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GG, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group E2E -- llm-router (kind sdcpp) -> gpu-router -> the stub child
# ---------------------------------------------------------------------------
# Three kinds of real process per case and nothing in-process: Scripts/gpu-router.py
# as a subprocess (`launch`, --debug), Scripts/llm-router.py on the same
# command line (`launch(..., server=LLM_SERVER)`, --debug) and the stub children
# the gpu-router spawns.  The client speaks raw HTTP/1.1 to llm-router's
# /v1/images/generations with llm-router's own bearer (`Connection: close`).
# Every live router is closed SIGTERM -> SIGKILL in a `finally`, llm-router
# first; the stubs are reaped through `_q_live`.
#
# The contract these cases pin (task-039; plan Step 8, D-10, D-11, FR-11, SC-6):
#   llm-router (task-038).  A backend of kind sdcpp whose base_url is
#     http://127.0.0.1:<gpu-router port>/m/<model>, with allow_private,
#     allow_loopback, an api_key of >= 32 characters and auth_header
#     authorization, is ACCEPTED, and every upstream request carries
#     `Authorization: Bearer <api_key>` to base_path + /v1/images/generations.
#     The client's bytes are the upstream body.  The 2xx answer is REBUILT
#     ({created, data: [{b64_json}]}), not relayed, so the round trip is proven
#     by the stub's b64_json -- base64 of "tf-png:" + the first 16 hex digits of
#     the SHA-256 of the body the stub received -- and by the body file the stub
#     stored.  An upstream 401 reaches the client as 502 and an upstream 503
#     that is not model_not_loaded / model_loading as 529 (_UPSTREAM_STATUS),
#     both with the fixed text "backend <n> answered <status>".
#   gpu-router.  The bearer is checked first, then the /m/<x> prefix alone
#     selects the model (D-11): the body's `model` is llm-router's route key and
#     is never sniffed -- E1 names the route "B" while it points at /m/A, so a
#     router that sniffed the body would start B.  The bearer is never
#     forwarded: the stub's recorded header names hold no `authorization`.  A
#     swap stops A's whole group before B serves (single GPU); a forced unload
#     on the gpu-router control route aborts the request llm-router waits on, and
#     answers {"unloaded": true} only with A's group gone (SC-2, SC-3).  A
#     wrong key is refused before any child starts.
#   SC-6.  Neither key (the gpu-router token, which is llm-router's api_key, and
#     llm-router's own auth_token) appears in either router's stderr at --debug,
#     in a stub's environment or argv (--dump-env), or in a client answer.
# If llm-router refuses the config -- the credential phase absent: exit 2 with
# the refusal naming `api_key` -- the case is INFO and skipped, so the
# gpu-router phases stay shippable alone.  Any other llm-router start failure,
# and a gpu-router that does not exit 0 on SIGTERM, is a FAIL.

GE_CASES = (
    "E1 round trip, prefix wins",
    "E2 swap through two prefixes",
    "E3 forced unload, 529 relayed",
    "E4 wrong key: 502, no start",
)

LLM_SERVER = H.repo_path("Scripts", "llm-router.py")
E_LLM_TOKEN = "tf-e2e-llm-token-SENTINEL-" + secrets.token_hex(16)    # llm-router's own auth_token
E_WRONG_KEY = "tf-e2e-wrong-key-SENTINEL-" + secrets.token_hex(16)    # an api_key gpu-router never accepts
E_SECRETS = (TF_TOKEN, E_LLM_TOKEN, E_WRONG_KEY)
E_UPSTREAM_401 = 502         # llm-router's _UPSTREAM_STATUS[401]
E_UPSTREAM_503 = 529         # llm-router's _UPSTREAM_STATUS[503] (not model_not_loaded / model_loading)
E_SKIP_NEEDLE = "api_key"    # llm-router's refusal of a keyed sdcpp backend names the key path


class _ESkip(Exception):
    """llm-router refused the keyed sdcpp config: the credential phase is not in place."""


def _e_llm_config(sandbox, sd_port, routes, key=TF_TOKEN):
    """A 0600 llm-router config: one sdcpp backend per route of *routes* ({route key:
    gpu-router model}), each at http://127.0.0.1:<sd_port>/m/<model> with *key* as a bearer."""
    backends, table = {}, {}
    for index, (route, model) in enumerate(sorted(routes.items())):
        backend = "sd%d" % index
        backends[backend] = {"kind": "sdcpp", "base_url": "http://%s:%d/m/%s" % (LOOPBACK, sd_port, model),
                             "allow_private": True, "allow_loopback": True,
                             "api_key": key, "auth_header": "authorization"}
        table[route] = {"backend": backend, "model": route, "modality": "image"}
    doc = {"auth_token": E_LLM_TOKEN, "backends": backends, "routes": table}
    return write_file(os.path.join(sandbox, "llm-router.json"), json.dumps(doc, indent=2, sort_keys=True) + "\n")


def _e_secret_in(text):
    return any(s in text for s in E_SECRETS)


def _e_sweep(sandbox, world, models, names):
    """SC-6: no key in either router's stderr or in a stub's env dump (argv + env)."""
    problems = []
    for label, proc in (("the gpu-router stderr", world.sd), ("the llm-router stderr", world.llm)):
        if proc is not None and _e_secret_in(proc.stderr_text()):
            problems.append("%s carries a key" % label)
    for name in models:
        text = _p_file_text(os.path.join(sandbox, name + ".env.json"))
        if not text:
            if name in names:
                problems.append("stub %s wrote no env dump: SC-6 cannot be checked" % name)
            continue
        if _e_secret_in(text):
            problems.append("stub %s's environment or argv carries a key" % name)
    return problems


@contextlib.contextmanager
def _e_world(sandbox, models, routes, names, key=TF_TOKEN):
    """gpu-router over *models*, then llm-router over *routes* pointing at it; both at --debug.

    Raises _ESkip when llm-router refuses the keyed sdcpp backends.  On exit both
    are closed (llm-router first), the gpu-router exit code checked and the SC-6
    sweep run; problems in world.problems."""
    world = types.SimpleNamespace(sd=None, llm=None, problems=[])
    try:
        world.sd, ready = launch(write_config(sandbox, base_config(models)), ["--debug"], label="sd")
        if ready is None:
            raise AssertionError(_p_not_ready(world.sd))
        world.llm, ready = launch(_e_llm_config(sandbox, world.sd.port, routes, key), ["--debug"],
                                  label="llm", server=LLM_SERVER)
        if ready is None:
            code = world.llm.wait_exit()
            if code == 2 and E_SKIP_NEEDLE in world.llm.stderr_text():
                raise _ESkip("llm-router refuses an sdcpp backend with an api_key (exit 2): the "
                             "credential phase (task-038) is not in place")
            raise AssertionError(_p_not_ready(world.llm).replace("the router", "llm-router"))
        yield world
    finally:
        if world.llm is not None:
            world.llm.close()
        if world.sd is not None and world.sd.close() != 0:
            world.problems.append("gpu-router did not exit 0 on SIGTERM (exit %r)" % world.sd.proc.returncode)
        world.problems += _e_sweep(sandbox, world, models, names)


def _e_body(tag, route):
    """A unique generation body for llm-router route *route* (its SHA identifies it)."""
    return json.dumps({"model": route, "n": 1, "prompt": "%s %s-%s" % (P_PROMPT, tag, secrets.token_hex(4))},
                      sort_keys=True).encode("utf-8")


def _e_request(world, body, token=E_LLM_TOKEN):
    port = world.llm.port
    return _p_head(port, "POST", P_GEN, token=token, length=len(body), extra=("Connection: close",)) + body


def _e_post(world, body):
    return _p_exchange(world.llm.port, _e_request(world, body))


def _e_reached(label, sandbox, name, body):
    """Problems unless stub *name* received *body* once on P_GEN, byte-exact, without the bearer."""
    sends = _q_sends(sandbox, name, body)
    if len(sends) != 1:
        return ["%s: stub %s received this body %d times, expected once" % (label, name, len(sends))]
    problems = []
    if sends[0].get("path") != P_GEN:
        problems.append("%s: stub %s received it on %r, expected %r" % (label, name, sends[0].get("path"), P_GEN))
    if "authorization" in (sends[0].get("header_names") or []):
        problems.append("%s: an Authorization header reached stub %s (the bearer was forwarded)" % (label, name))
    return problems + _p_stored(label, sandbox, name, body)


def _e_image(label, resp, body, sandbox, name):
    """Problems unless llm-router answered 200 with stub *name*'s image for exactly *body*."""
    if _e_secret_in(resp.raw.decode("latin-1")):
        return ["%s: the client's answer carries a key" % label]
    if resp.status != 200:
        return ["%s: llm-router answered %s, expected 200 and the stub's image" % (label, resp.describe())]
    doc = resp.json()
    data = doc.get("data") if isinstance(doc, dict) else None
    want = base64.b64encode(("tf-png:" + _q_sha(body)[:16]).encode("ascii")).decode("ascii")
    problems = []
    if not (isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict)
            and data[0].get("b64_json") == want):
        problems.append("%s: the answer's b64_json is not stub %s's image for this body" % (label, name))
    return problems + _e_reached(label, sandbox, name, body)


def _e_refused(label, resp, status, upstream):
    """Problems unless llm-router answered *status* with the fixed text for an upstream *upstream*."""
    if _e_secret_in(resp.raw.decode("latin-1")):
        return ["%s: the client's answer carries a key" % label]
    if resp.status != status:
        return ["%s: llm-router answered %s, expected %d (gpu-router's %d)" % (label, resp.describe(), status, upstream)]
    doc = resp.json()
    err = doc.get("error") if isinstance(doc, dict) else None
    message = err.get("message") if isinstance(err, dict) else None
    if not isinstance(message, str) or ("answered %d" % upstream) not in message:
        return ["%s: the error message %r does not name the upstream %d" % (label, message, upstream)]
    return []


@_q_live
def e1(mod, sandbox, names, optional):
    del mod
    names.append("A")
    optional.append("B")
    models = {"A": _p_entry(sandbox, "A", "--dump-env"), "B": _p_entry(sandbox, "B", "--dump-env")}
    with _e_world(sandbox, models, {"B": "A"}, names) as w:
        body = _e_body("e1", "B")
        problems = _e_image("the round trip", _e_post(w, body), body, sandbox, "A")
        if read_starts(sandbox, "B"):
            problems.append("model B started: the body's model (llm-router's route key) was sniffed "
                            "over the /m/A prefix (D-11)")
    return problems + w.problems, ["llm-router route \"B\" -> /m/A: A served the body byte-exact, B never "
                                   "started, no Authorization reached the child, no key in any log or env"]


@_q_live
def e2(mod, sandbox, names, optional):
    del mod
    names.extend(("A", "B"))
    models = {"A": _p_entry(sandbox, "A", "--dump-env"), "B": _p_entry(sandbox, "B", "--dump-env")}
    with _e_world(sandbox, models, {"sd-a": "A", "sd-b": "B"}, names) as w:
        body_a, body_b = _e_body("e2-a", "sd-a"), _e_body("e2-b", "sd-b")
        problems = _e_image("the request for A", _e_post(w, body_a), body_a, sandbox, "A")
        a_pids = read_pids(sandbox, "A")
        problems += _e_image("the request for B", _e_post(w, body_b), body_b, sandbox, "B")
        alive = [pid for pid in a_pids if pid_alive(pid)]
        if alive:
            problems.append("pid(s) %r of A's group were alive after B answered: two models resident" % alive)
        starts = (len(read_starts(sandbox, "A")), len(read_starts(sandbox, "B")))
        if starts != (1, 1):
            problems.append("A and B started %r times, expected once each" % (starts,))
    return problems + w.problems, ["sd-a then sd-b through llm-router: A answered, its group was gone "
                                   "before B answered, one start each"]


@_q_live
def e3(mod, sandbox, names, optional):
    del mod
    names.append("A")
    models = {"A": _p_entry(sandbox, "A", "--keep-generating", "--dump-env")}
    problems = []
    with _e_world(sandbox, models, {"sd-a": "A"}, names) as w:
        body = _e_body("e3", "sd-a")
        sock = _p_open(w.llm.port, _e_request(w, body))
        r_unload = r_client = None
        try:
            if wait_events(sandbox, "request", 1, "A") is None:
                problems.append("the request never reached the child through llm-router")
            else:
                pids = read_pids(sandbox, "A")
                r_unload = _u_post(w.sd.port, U_UNLOAD + "?force=1")
                alive = [pid for pid in pids if pid_alive(pid)]
                r_client = _PResp(*_p_read(sock))
        finally:
            sock.close()
        if r_unload is not None:
            problems += _u_unloaded("the forced unload", r_unload, True, "A")
            if alive:
                problems.append("the forced unload answered 200 while pid(s) %r of A's group were alive (SC-2)"
                                % alive)
            problems += _e_refused("the aborted request", r_client, E_UPSTREAM_503, 503)
            problems += _e_reached("the aborted request", sandbox, "A", body)
    return problems + w.problems, ["a force on gpu-router's control route ended A's group; llm-router "
                                   "relayed gpu-router's 503 unload_forced as 529"]


@_q_live
def e4(mod, sandbox, names, optional):
    del mod
    optional.append("A")
    models = {"A": _p_entry(sandbox, "A", "--dump-env")}
    with _e_world(sandbox, models, {"sd-a": "A"}, names, key=E_WRONG_KEY) as w:
        body = _e_body("e4", "sd-a")
        problems = _e_refused("the wrong key", _e_post(w, body), E_UPSTREAM_401, 401)
        if read_starts(sandbox, "A") or _q_sends(sandbox, "A"):
            problems.append("a request with a wrong key started or reached the child")
    return problems + w.problems, ["llm-router's api_key is not the gpu-router token: 401 upstream, 502 "
                                   "to the client, no child started, no key echoed"]


GE_FUNCS = (e1, e2, e3, e4)


def group_e2e(suite, fixture_root):
    absent = ["%s does not exist" % os.path.relpath(path, H.REPO_ROOT)
              for path in (SERVER, LLM_SERVER, STUB) if not os.path.isfile(path)]
    for cid, fn in zip(GE_CASES, GE_FUNCS):
        if absent:
            suite.record(GE, cid, absent)
            continue
        sandbox = new_sandbox(fixture_root, "e2e-%s" % cid.split(" ", 1)[0].lower())
        try:
            problems, detail = fn(None, sandbox)
        except _ESkip as exc:
            suite.record(GE, cid, [], status=H.INFO, detail=["skipped: %s" % exc])
            continue
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GE, cid, problems, detail=detail)


# ---------------------------------------------------------------------------
# Group A -- static AST over Scripts/gpu-router.py
# ---------------------------------------------------------------------------
# One read and one parse of the router; every case is an AST walk (well under
# 2 s each), RED while the file is missing or does not parse.  A unit is the
# line span from a `# ---- unit N ----` header to the next one (the code before
# unit 1 -- imports and generated regions -- is unit 0).
#
# The contract these cases pin (task-032; plan Step 8, Section 3, Section 5,
# D-1, H5, NFR, final security 3):
#   A1  ONE upstream send site.  An HTTPConnection is built only in `_sd_send`
#       (the relay, unit 5) and in the readiness probe `_sd_probe_status`, whose
#       only request is the literal `GET /` (DEVIATION from the plan's "only
#       _sd_send": the probe predates this gate and sends no client byte; it is
#       declared here rather than refused).  `_sd_send` is defined once and
#       called from exactly one site; a socket connect happens only in
#       `_sd_connect` (called only by `_sd_send`) and the probe.
#   A2  No `shell=` other than a literal False, no os.system / os.popen /
#       os.exec* / os.spawn* / os.fork, no subprocess.call / run / check_* /
#       getoutput.
#   A3  Exactly one Popen, in unit 4, with `start_new_session=True` and a list
#       argv (a list display, or a name bound to one / to `[...] + ...`).
#   A4  Units 1-3 import nothing and name no socket, socketserver, subprocess,
#       http, threading, select, signal, asyncio or stdlib server class.
#   A5  Units 1-3 read no clock and no randomness (time, random, datetime,
#       _sd_now, _sd_jitter): decide() gets `now` from its caller.
#   A6  No `except ImportError` / `ModuleNotFoundError` (ADR 0024).
#   A7  No eval / exec / compile / __import__ call, no pickle / marshal /
#       shelve import.
#   A8  The router and the stub child parse under ast.parse(feature_version=(3, 9)).
#   A9  The closed table `_SD_ERROR_STATUS` equals the set of codes the source
#       uses: the string constants handed as the code to _sd_error, _refuse,
#       _sd_reply_refuse, _sd_ticket_error, RouteError and Decision (the
#       Decision marker `_SD_NOT_RESIDENT`, "not_resident", is not a code and
#       is excluded), plus the codes of `_SD_STDLIB_REFUSAL` and its default.
#       `_SD_ERROR_TEXT` names no code outside it; `model_mismatch` appears
#       nowhere; `upstream_timeout` and `queue_full` are in the table and used.
#   A10 The route table is ONE mapping: `ROUTES` is bound once, a literal of
#       string keys; the only other literal keyed by a route path is the media
#       annotation `_SD_ROUTE_MEDIA`, with exactly ROUTES's keys; no route path
#       literal appears anywhere else (docstrings aside).
#   A11 No `--config-json` option: no add_argument names it, no string is it.
#   A12 No 3.10+ API: none of the names the 3.10-3.13 stdlib added, no
#       zip(strict=), no dataclass(slots= / kw_only=), no `X | Y` in an
#       evaluated annotation or in an isinstance / issubclass type.
#   A13 No token reaches a log call: no argument of a log.* / logger.* /
#       front_log.* call names a token, authorization, bearer, secret,
#       password or api key, or reads headers, a body, an env, an argv, a
#       prompt, the request line or the raw path.
#   A14 `Ticket` is defined once, outside units 1-3 (it owns Events: unit 6).
#   A15 `_sd_wait_exit_unreaped` branches on `sys.platform` (kqueue on the darwin
#       side, os.waitid only outside it); nothing asks hasattr / getattr(os, "waitid").
#   A16 The child env never comes from the router's: no os.environ / environb /
#       getenv / putenv / unsetenv anywhere, and the Popen's `env=` is
#       `_sd_child_env(...)`.
#   A17 Unit 6 lock discipline (final security 3): inside every `with <x>.cond`
#       section and every `*_locked` method -- submit_unload included -- no log
#       call, no blocking call (Popen, connect, sleep, join, a `wait` without a
#       timeout, the ladder and launch functions, socket I/O) and no call of a
#       Scheduler method that is not itself `*_locked`; `finish()` and
#       `done.set()` are called only outside those sections, and submit_unload
#       does finish its already-answered ticket after its section.
# Not pinned here: the repo hygiene snapshot (no bytecode, no new repo paths) --
# run() hands `pyc_before` / `tree_before` to this group, but the typed count
# carries no hygiene case.

GA_CASES = (
    "A1 one upstream send site",
    "A2 no shell, os.system/popen",
    "A3 Popen in unit 4, list argv",
    "A4 units 1-3: no blocking mod",
    "A5 units 1-3: no clock/random",
    "A6 no except ImportError",
    "A7 no eval/exec/pickle",
    "A8 parses at Python 3.9",
    "A9 error table == codes used",
    "A10 ONE route table",
    "A11 no --config-json",
    "A12 no 3.10+ API",
    "A13 no token in a log call",
    "A14 Ticket outside units 1-3",
    "A15 platform by sys.platform",
    "A16 child env not os.environ",
    "A17 lock: no log/block/finish",
)

A_SEND_OWNERS = frozenset(("_sd_send", "_sd_probe_status"))
A_CONNECT_OWNERS = frozenset(("_sd_connect", "_sd_probe_status"))
A_CONNECT_CALLS = frozenset(("connect", "connect_ex", "create_connection"))
A_OS_BANNED = frozenset(("system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle",
                         "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl",
                         "spawnle", "spawnlp", "spawnlpe", "posix_spawn", "posix_spawnp", "fork",
                         "forkpty"))
A_SUBPROCESS_BANNED = frozenset(("call", "run", "check_call", "check_output", "getoutput",
                                 "getstatusoutput"))
A_PURE_UNITS = (1, 2, 3)
A_BLOCKING_NAMES = frozenset(("socket", "socketserver", "subprocess", "http", "threading", "select",
                              "signal", "asyncio", "ThreadingHTTPServer", "BaseHTTPRequestHandler"))
A_CLOCK_NAMES = frozenset(("time", "random", "datetime", "_sd_now", "_sd_jitter"))
A_IMPORT_ERRORS = frozenset(("ImportError", "ModuleNotFoundError"))
A_EVAL_CALLS = frozenset(("eval", "exec", "compile", "__import__"))
A_PICKLE_MODULES = frozenset(("pickle", "_pickle", "cPickle", "marshal", "shelve", "dill"))
A_CODE_FIRST = frozenset(("_sd_error", "_refuse", "_sd_ticket_error", "RouteError"))
A_ABSENT_CODE = "model_mismatch"
A_PRESENT_CODES = ("upstream_timeout", "queue_full")
A_PY310_NAMES = frozenset(("aiter", "anext", "pairwise", "bit_count", "TaskGroup", "StrEnum",
                           "ExceptionGroup", "BaseExceptionGroup", "EncodingWarning", "add_note",
                           "get_annotations", "TypeAlias", "ParamSpec", "Concatenate", "TypeGuard",
                           "Self", "Never", "LiteralString", "Required", "NotRequired",
                           "assert_never", "reveal_type", "dataclass_transform", "KW_ONLY",
                           "tomllib", "TypeVarTuple", "Unpack", "override", "batched"))
A_PY310_KEYWORDS = {"zip": ("strict",), "dataclass": ("slots", "kw_only", "match_args")}
A_LOG_METHODS = frozenset(("debug", "info", "warning", "warn", "error", "critical", "exception", "log"))
A_LOG_RECEIVERS = frozenset(("log", "logger", "logging", "front_log"))
A_SECRET_PARTS = ("token", "authorization", "bearer", "secret", "password", "api_key", "apikey")
A_SECRET_EXACT = frozenset(("headers", "body", "env", "argv", "prompt", "requestline", "raw", "path",
                            "raw_requestline"))
A_ENV_ATTRS = frozenset(("environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"))
A_LOCK_BLOCKING = frozenset(("Popen", "connect", "connect_ex", "create_connection", "sleep", "join",
                             "launch", "wait_ready", "stop", "start", "kill_all", "_ladder",
                             "_signal_group", "_sd_wait_exit_unreaped", "_confirm_gone", "_sd_send",
                             "_sd_connect", "_sd_write", "_sd_read_all", "_sd_probe_status",
                             "recv", "recv_into", "send", "sendall", "accept", "select", "killpg",
                             "kill", "waitid", "waitpid", "communicate", "acquire"))
A_LOCK_WAITS = frozenset(("wait", "wait_for"))
A_SCHEDULER = "Scheduler"


def _a_units(text):
    """{unit number: (first line, last line)} from the `# ---- unit N ----` headers."""
    lines = text.splitlines()
    heads = []
    for number, line in enumerate(lines, 1):
        if line.startswith("# ---- unit ") and line.endswith(" ----"):
            word = line[len("# ---- unit "):-len(" ----")]
            if word.isdigit():
                heads.append((int(word), number))
    spans = {}
    for index, (unit, start) in enumerate(heads):
        end = heads[index + 1][1] - 1 if index + 1 < len(heads) else len(lines)
        spans[unit] = (start, end)
    return spans


def _a_unit_of(units, lineno):
    for unit, (start, end) in units.items():
        if start <= lineno <= end:
            return unit
    return 0


def _a_owner_map(tree):
    """{id(node): the dotted name of the def / class that encloses it ("" = module)}."""
    owners = {}

    def visit(node, qual):
        for child in ast.iter_child_nodes(node):
            owners[id(child)] = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, child.name if not qual else qual + "." + child.name)
            else:
                visit(child, qual)

    visit(tree, "")
    return owners


def _a_context():
    """(ctx, why): one read and one parse of the router, or (None, reason)."""
    text, why = _g_read_source()
    if text is None:
        return None, why
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return None, "Scripts/gpu-router.py does not parse: line %s" % exc.lineno
    units = _a_units(text)
    if not all(unit in units for unit in range(1, 10)):
        return None, "Scripts/gpu-router.py lacks a `# ---- unit N ----` header for units 1-9"
    return types.SimpleNamespace(text=text, tree=tree, units=units, owners=_a_owner_map(tree)), None


def _a_call_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _a_calls(tree, names):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _a_call_name(node) in names]


def _a_defs(tree, name):
    return [node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]


def _a_leaf(qual):
    return qual.rsplit(".", 1)[-1]


def _a_strs(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _a_in_units(ctx, units):
    """Every node of the tree whose line lies in one of *units*."""
    return [node for node in ast.walk(ctx.tree)
            if hasattr(node, "lineno") and _a_unit_of(ctx.units, node.lineno) in units]


def a1(ctx):
    problems = []
    for call in _a_calls(ctx.tree, ("HTTPConnection", "HTTPSConnection")):
        owner = _a_leaf(ctx.owners.get(id(call), ""))
        if owner not in A_SEND_OWNERS:
            problems.append("%d: an HTTP connection is built in %s, not _sd_send or the readiness probe"
                            % (call.lineno, owner or "module scope"))
    if not any(_a_leaf(ctx.owners.get(id(c), "")) == "_sd_send"
               for c in _a_calls(ctx.tree, ("HTTPConnection",))):
        problems.append("_sd_send builds no HTTPConnection")
    defs = _a_defs(ctx.tree, "_sd_send")
    if len(defs) != 1:
        problems.append("_sd_send is defined %d time(s), expected once" % len(defs))
    elif _a_unit_of(ctx.units, defs[0].lineno) != 5:
        problems.append("_sd_send is not in unit 5")
    sends = [c for c in ast.walk(ctx.tree) if isinstance(c, ast.Call) and _g_is_name(c.func, "_sd_send")]
    callers = sorted({ctx.owners.get(id(c), "") for c in sends})
    if len(sends) != 1:
        problems.append("_sd_send is called from %d site(s) (%s), expected exactly one"
                        % (len(sends), ", ".join(callers) or "none"))
    for call in _a_calls(ctx.tree, ("request",)):
        if _a_leaf(ctx.owners.get(id(call), "")) != "_sd_probe_status":
            continue
        args = [a.value if isinstance(a, ast.Constant) else None for a in call.args[:2]]
        if args != ["GET", "/"]:
            problems.append("%d: the readiness probe sends something other than GET /" % call.lineno)
    for call in _a_calls(ctx.tree, A_CONNECT_CALLS):
        owner = _a_leaf(ctx.owners.get(id(call), ""))
        if owner not in A_CONNECT_OWNERS:
            problems.append("%d: a socket connect in %s" % (call.lineno, owner or "module scope"))
    for call in ast.walk(ctx.tree):
        if isinstance(call, ast.Call) and _g_is_name(call.func, "_sd_connect"):
            owner = _a_leaf(ctx.owners.get(id(call), ""))
            if owner != "_sd_send":
                problems.append("%d: _sd_connect called from %s, not _sd_send" % (call.lineno, owner))
    return problems, ["send site   : _sd_send (unit 5), called from %s" % ", ".join(callers),
                      "declared    : the readiness probe _sd_probe_status (GET / only)"]


def a2(ctx):
    problems = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                problems.append("%d: shell= is not the literal False" % node.lineno)
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr in A_OS_BANNED:
                problems.append("%d: os.%s" % (node.lineno, func.attr))
            if func.value.id == "subprocess" and func.attr in A_SUBPROCESS_BANNED:
                problems.append("%d: subprocess.%s" % (node.lineno, func.attr))
    return problems, ["scope       : every call: shell=, os.system/popen/exec*/spawn*/fork, subprocess.run/call/check_*"]


def _a_enclosing_def(tree, target):
    """The innermost def whose body holds *target*."""
    found = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and any(child is target for child in ast.walk(node)):
            found = node
    return found


def _a_listish(expr, fn, depth=0):
    if isinstance(expr, ast.List):
        return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return isinstance(expr.left, ast.List)
    if isinstance(expr, ast.Name) and fn is not None and depth == 0:
        values = [stmt.value for stmt in ast.walk(fn) if isinstance(stmt, ast.Assign)
                  and any(_g_is_name(t, expr.id) for t in stmt.targets)]
        return bool(values) and all(_a_listish(v, None, 1) for v in values)
    return False


def a3(ctx):
    problems = []
    popens = _a_calls(ctx.tree, ("Popen",))
    if len(popens) != 1:
        problems.append("%d Popen call(s), expected exactly one" % len(popens))
    for call in popens:
        unit = _a_unit_of(ctx.units, call.lineno)
        if unit != 4:
            problems.append("%d: Popen in unit %d, not unit 4" % (call.lineno, unit))
        session = [kw for kw in call.keywords if kw.arg == "start_new_session"]
        if not (len(session) == 1 and isinstance(session[0].value, ast.Constant) and session[0].value.value is True):
            problems.append("%d: Popen without start_new_session=True" % call.lineno)
        argv = call.args[0] if call.args else next((kw.value for kw in call.keywords if kw.arg == "args"), None)
        if argv is None or not _a_listish(argv, _a_enclosing_def(ctx.tree, call)):
            problems.append("%d: the Popen argv is not a list" % call.lineno)
    return problems, ["popen       : %s" % ", ".join("line %d (%s)" % (c.lineno, ctx.owners.get(id(c), ""))
                                                    for c in popens)]


def _a_unit_names(ctx, banned, label):
    problems = []
    for node in _a_in_units(ctx, A_PURE_UNITS):
        unit = _a_unit_of(ctx.units, node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            problems.append("%d: unit %d imports" % (node.lineno, unit))
        elif isinstance(node, ast.Name) and node.id in banned:
            problems.append("%d: unit %d names %s (%s)" % (node.lineno, unit, node.id, label))
    spans = ", ".join("unit %d %d-%d" % (u, ctx.units[u][0], ctx.units[u][1]) for u in A_PURE_UNITS)
    return problems, ["scope       : %s" % spans]


def a4(ctx):
    return _a_unit_names(ctx, A_BLOCKING_NAMES, "a blocking module")


def a5(ctx):
    return _a_unit_names(ctx, A_CLOCK_NAMES, "a clock or randomness")


def a6(ctx):
    problems = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        kinds = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for kind in kinds:
            if isinstance(kind, ast.Name) and kind.id in A_IMPORT_ERRORS:
                problems.append("%d: except %s" % (node.lineno, kind.id))
    return problems, ["scope       : every except clause"]


def a7(ctx):
    problems = []
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in A_EVAL_CALLS:
            problems.append("%d: %s() call" % (node.lineno, node.func.id))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in A_PICKLE_MODULES:
                    problems.append("%d: import %s" % (node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in A_PICKLE_MODULES:
            problems.append("%d: from %s import" % (node.lineno, node.module))
    return problems, ["scope       : eval/exec/compile/__import__ calls, pickle/marshal/shelve imports"]


def a8(ctx):
    problems = []
    for path in (SERVER, STUB):
        rel = os.path.relpath(path, H.REPO_ROOT)
        if not os.path.isfile(path):
            problems.append("%s does not exist" % rel)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        try:
            ast.parse(text, filename=rel, feature_version=(3, 9))
        except SyntaxError as exc:
            problems.append("%s does not parse as Python 3.9: line %s" % (rel, exc.lineno))
    return problems, ["parsed      : Scripts/gpu-router.py, tests/files/gpu_router/tf_stub_sd.py at (3, 9)"]


def _a_dict_keys(value):
    """The string keys of a dict literal, also inside MappingProxyType(...); None otherwise."""
    if isinstance(value, ast.Call) and _a_call_name(value) == "MappingProxyType" and len(value.args) == 1:
        value = value.args[0]
    if not isinstance(value, ast.Dict):
        return None
    return value


def a9(ctx):
    problems = []
    tree = ctx.tree
    tables = _g_module_values(tree, "_SD_ERROR_STATUS")
    table_node = _a_dict_keys(tables[0]) if len(tables) == 1 else None
    if table_node is None:
        return ["_SD_ERROR_STATUS is not ONE module-level dict literal"], []
    table = {k.value for k in table_node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    marker = set()
    for value in _g_module_values(tree, "_SD_NOT_RESIDENT"):
        marker |= _a_strs(value)
    used, sites = set(), 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = _a_call_name(call)
        if name in A_CODE_FIRST and call.args:
            used |= _a_strs(call.args[0])
            sites += 1
        elif name == "_sd_reply_refuse" and len(call.args) >= 2:
            used |= _a_strs(call.args[1])
            sites += 1
        elif name == "Decision":
            code = call.args[2] if len(call.args) > 2 else next(
                (kw.value for kw in call.keywords if kw.arg == "code"), None)
            if code is not None:
                used |= _a_strs(code) - marker
                sites += 1
    for name in ("_SD_STDLIB_REFUSAL", "_SD_STDLIB_REFUSAL_DEFAULT"):
        for value in _g_module_values(tree, name):
            rows = value.values if isinstance(value, ast.Dict) else [value]
            for row in rows:
                if isinstance(row, ast.Tuple) and row.elts:
                    used |= _a_strs(row.elts[0])
    for missing in sorted(table - used):
        problems.append("%s is in the table but no refusal uses it" % missing)
    for extra in sorted(used - table):
        problems.append("%s is used as a code but is not in the table" % extra)
    for value in _g_module_values(tree, "_SD_ERROR_TEXT"):
        node = _a_dict_keys(value)
        for key in (node.keys if node is not None else ()):
            if isinstance(key, ast.Constant) and key.value not in table:
                problems.append("_SD_ERROR_TEXT names %s, outside the table" % key.value)
    if any(isinstance(n, ast.Constant) and n.value == A_ABSENT_CODE for n in ast.walk(tree)):
        problems.append("%s appears in the source" % A_ABSENT_CODE)
    for code in A_PRESENT_CODES:
        if code not in table or code not in used:
            problems.append("%s is not both in the table and used" % code)
    return problems, ["table       : %d codes, %d code site(s) scanned (_SD_NOT_RESIDENT excluded)"
                      % (len(table), sites)]


def _a_docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for stmt in body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                ids.add(id(stmt.value))
    return ids


def a10(ctx):
    problems = []
    tree = ctx.tree
    bound = _g_module_values(tree, "ROUTES")
    node = _a_dict_keys(bound[0]) if len(bound) == 1 else None
    if node is None:
        return ["ROUTES is not bound once to a dict literal (%d binding(s))" % len(bound)], []
    routes = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if len(routes) != len(node.keys) or not routes:
        problems.append("ROUTES has a key that is not a string literal")
    allowed = {id(k) for k in node.keys}
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else (
            [stmt.target] if isinstance(stmt, ast.AnnAssign) and stmt.value is not None else [])
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        literal = _a_dict_keys(stmt.value) if targets else None
        if literal is None or "ROUTES" in names:
            continue
        keys = {k.value for k in literal.keys if isinstance(k, ast.Constant)}
        if not keys & routes:
            continue
        if names == ["_SD_ROUTE_MEDIA"] and keys == routes:
            allowed |= {id(k) for k in literal.keys}
        else:
            problems.append("%d: %s is a second literal keyed by route paths" % (stmt.lineno, ", ".join(names)))
    docs = _a_docstring_ids(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and n.value in routes and id(n) not in allowed and id(n) not in docs:
            problems.append("%d: the route path %r is spelled outside ROUTES" % (n.lineno, n.value))
    return problems, ["routes      : %d paths in ROUTES; _SD_ROUTE_MEDIA mirrors its keys" % len(routes)]


def a11(ctx):
    problems = []
    for call in _a_calls(ctx.tree, ("add_argument",)):
        if any("config-json" in s or "config_json" in s for s in _a_strs(call)):
            problems.append("%d: add_argument names --config-json" % call.lineno)
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.Constant) and n.value in ("--config-json", "--config_json"):
            problems.append("%d: the string --config-json" % n.lineno)
    return problems, ["scope       : every add_argument call and every string constant"]


def _a_bitor(node):
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(node))


def a12(ctx):
    problems = []
    tree = ctx.tree
    future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                 and any(a.name == "annotations" for a in n.names) for n in tree.body)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in A_PY310_NAMES:
            problems.append("%d: %s (3.10+)" % (n.lineno, n.id))
        elif isinstance(n, ast.Attribute) and n.attr in A_PY310_NAMES:
            problems.append("%d: .%s (3.10+)" % (n.lineno, n.attr))
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and (n.value.id, n.attr) in (
                ("sys", "exception"), ("datetime", "UTC")):
            problems.append("%d: %s.%s (3.11+)" % (n.lineno, n.value.id, n.attr))
        elif isinstance(n, ast.ImportFrom):
            for alias in n.names:
                if alias.name in A_PY310_NAMES or (n.module or "") == "tomllib":
                    problems.append("%d: from %s import %s (3.10+)" % (n.lineno, n.module, alias.name))
        elif isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name == "tomllib":
                    problems.append("%d: import tomllib (3.11+)" % n.lineno)
        elif isinstance(n, ast.Call):
            name = _a_call_name(n)
            for kw in n.keywords:
                if kw.arg in A_PY310_KEYWORDS.get(name, ()):
                    problems.append("%d: %s(%s=) (3.10+)" % (n.lineno, name, kw.arg))
            if name in ("isinstance", "issubclass") and len(n.args) == 2 and _a_bitor(n.args[1]):
                problems.append("%d: %s with an X | Y type (3.10+)" % (n.lineno, name))
        if future:
            continue
        annotations = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = n.args
            every = args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]
            annotations = [a.annotation for a in every if a.annotation is not None]
            if n.returns is not None:
                annotations.append(n.returns)
        elif isinstance(n, ast.AnnAssign):
            annotations = [n.annotation]
        for ann in annotations:
            if _a_bitor(ann):
                problems.append("%d: an X | Y annotation evaluated at runtime (3.10+)" % ann.lineno)
    return problems, ["scope       : 3.10-3.13 names, zip(strict=), dataclass(slots=/kw_only=), X | Y types"]


def _a_is_log_call(call):
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr in A_LOG_METHODS):
        return False
    receiver = func.value
    return (isinstance(receiver, ast.Name) and receiver.id in A_LOG_RECEIVERS) or \
        (isinstance(receiver, ast.Attribute) and receiver.attr in A_LOG_RECEIVERS)


def _a_secret_word(word):
    low = word.lower()
    return low in A_SECRET_EXACT or any(part in low for part in A_SECRET_PARTS)


def a13(ctx):
    problems = []
    calls = [c for c in ast.walk(ctx.tree) if isinstance(c, ast.Call) and _a_is_log_call(c)]
    for call in calls:
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for n in ast.walk(arg):
                word = n.id if isinstance(n, ast.Name) else (n.attr if isinstance(n, ast.Attribute) else None)
                if word is not None and _a_secret_word(word):
                    problems.append("%d: a log call reads %s" % (call.lineno, word))
    if not calls:
        problems.append("no log call found: the scan saw nothing")
    return problems, ["scanned     : %d log call(s)" % len(calls)]


def a14(ctx):
    problems = []
    defs = [n for n in ast.walk(ctx.tree) if isinstance(n, ast.ClassDef) and n.name == "Ticket"]
    if len(defs) != 1:
        return ["Ticket is defined %d time(s), expected once" % len(defs)], []
    unit = _a_unit_of(ctx.units, defs[0].lineno)
    if unit in A_PURE_UNITS or unit == 0:
        problems.append("Ticket is defined in unit %d, inside or before the pure units" % unit)
    return problems, ["ticket      : line %d, unit %d" % (defs[0].lineno, unit)]


def _a_reads_platform(node):
    return any(isinstance(n, ast.Attribute) and n.attr == "platform" and _g_is_name(n.value, "sys")
               for n in ast.walk(node))


def a15(ctx):
    problems = []
    defs = _a_defs(ctx.tree, "_sd_wait_exit_unreaped")
    if len(defs) != 1:
        return ["_sd_wait_exit_unreaped is defined %d time(s), expected once" % len(defs)], []
    branches = [n for n in ast.walk(defs[0]) if isinstance(n, ast.If) and _a_reads_platform(n.test)]
    if not branches:
        problems.append("_sd_wait_exit_unreaped has no branch on sys.platform")
    for branch in branches:
        body = ast.Module(body=branch.body, type_ignores=[])
        if not any(isinstance(n, ast.Attribute) and n.attr == "kqueue" for n in ast.walk(body)):
            problems.append("%d: the sys.platform branch does not use kqueue" % branch.lineno)
        if any(isinstance(n, ast.Attribute) and n.attr == "waitid" for n in ast.walk(body)):
            problems.append("%d: os.waitid inside the darwin branch" % branch.lineno)
    for call in _a_calls(ctx.tree, ("hasattr", "getattr")):
        if "waitid" in _a_strs(call):
            problems.append("%d: %s(..., \"waitid\") picks the platform" % (call.lineno, _a_call_name(call)))
    return problems, ["branch      : sys.platform in _sd_wait_exit_unreaped (line %d)" % defs[0].lineno]


def a16(ctx):
    problems = []
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.Attribute) and n.attr in A_ENV_ATTRS and _g_is_name(n.value, "os"):
            problems.append("%d: os.%s" % (n.lineno, n.attr))
        elif isinstance(n, ast.ImportFrom) and n.module == "os" and any(a.name in A_ENV_ATTRS for a in n.names):
            problems.append("%d: from os import an environment name" % n.lineno)
    if not _a_defs(ctx.tree, "_sd_child_env"):
        problems.append("no _sd_child_env")
    for call in _a_calls(ctx.tree, ("Popen",)):
        env = next((kw.value for kw in call.keywords if kw.arg == "env"), None)
        if not (isinstance(env, ast.Call) and _g_is_name(env.func, "_sd_child_env")):
            problems.append("%d: the Popen env= is not _sd_child_env(...)" % call.lineno)
    return problems, ["scope       : os.environ/getenv/putenv anywhere; Popen env=_sd_child_env(...)"]


def _a_is_cond_with(node):
    return isinstance(node, ast.With) and any(
        (isinstance(item.context_expr, ast.Attribute) and item.context_expr.attr == "cond")
        or _g_is_name(item.context_expr, "cond") for item in node.items)


def _a_section_nodes(stmts):
    """Every node of *stmts*, not descending into a nested def or lambda (run later)."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                stack.append(child)


def _a_lock_findings(call, methods):
    """What is wrong with *call* inside a lock section ("" when it is allowed)."""
    func = call.func
    name = _a_call_name(call)
    if _a_is_log_call(call):
        return "a log call"
    if name == "finish":
        return "finish()"
    if isinstance(func, ast.Attribute) and func.attr == "set" and isinstance(func.value, ast.Attribute) \
            and func.value.attr == "done":
        return "done.set()"
    if name in A_LOCK_WAITS and not call.args and not any(kw.arg == "timeout" for kw in call.keywords):
        return "%s() without a timeout" % name
    if name in A_LOCK_BLOCKING:
        return "the blocking call %s()" % name
    if isinstance(func, ast.Attribute) and _g_is_name(func.value, "self") and func.attr in methods \
            and not func.attr.endswith("_locked"):
        return "the Scheduler method %s(), not a *_locked helper" % func.attr
    return ""


def a17(ctx):
    problems = []
    start, end = ctx.units[6]
    scheduler = _g_class(ctx.tree, A_SCHEDULER)
    if scheduler is None or not (start <= scheduler.lineno <= end):
        return ["no class Scheduler in unit 6"], []
    methods = {stmt.name for stmt in scheduler.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))}
    sections = []
    for node in ast.walk(ctx.tree):
        if not hasattr(node, "lineno") or not (start <= node.lineno <= end):
            continue
        if _a_is_cond_with(node):
            sections.append(("with cond", node.lineno, node.body))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.endswith("_locked"):
            sections.append((node.name, node.lineno, node.body))
    inside = set()
    for label, line, body in sections:
        for n in _a_section_nodes(body):
            if not isinstance(n, ast.Call):
                continue
            inside.add(id(n))
            what = _a_lock_findings(n, methods)
            if what:
                problems.append("%d: %s inside the lock section (%s at %d)" % (n.lineno, what, label, line))
    submit = _g_method(scheduler, "submit_unload")
    if submit is None:
        problems.append("Scheduler has no submit_unload")
    else:
        if not any(_a_is_cond_with(n) for n in ast.walk(submit)):
            problems.append("submit_unload holds no `with self.cond` section")
        finishes = [c for c in ast.walk(submit) if isinstance(c, ast.Call) and _a_call_name(c) == "finish"]
        if not finishes or any(id(c) in inside for c in finishes):
            problems.append("submit_unload does not finish its answered ticket after the lock section")
    if not sections:
        problems.append("no lock section found in unit 6: the scan saw nothing")
    return problems, ["sections    : %d lock section(s) in unit 6 (lines %d-%d), submit_unload included"
                      % (len(sections), start, end)]


GA_FUNCS = (a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17)


def group_a(suite, fixture_root, pyc_before, tree_before):
    del fixture_root, pyc_before, tree_before   # the hygiene snapshot is not part of A (see above)
    ctx, why = _a_context()
    for cid, fn in zip(GA_CASES, GA_FUNCS):
        if ctx is None:
            suite.record(GA, cid, [why])
            continue
        try:
            problems, detail = fn(ctx)
        except Exception as exc:  # noqa: BLE001 -- a broken case fails, never aborts the group
            problems, detail = ["case raised %s: %s" % (type(exc).__name__, str(exc)[:200])], []
        suite.record(GA, cid, problems, detail=detail)


# ---------------------------------------------------------------------------

# The groups in run order.  A group whose task has not landed yet is absent
# from this module and is simply not called.  A runs last but one and E2E
# last: the static pass and the hygiene snapshot need nothing alive.
GROUP_ORDER = ("group_l", "group_r", "group_d", "group_s", "group_q_sched",
               "group_t", "group_p", "group_q_http", "group_u", "group_g",
               "group_e2e", "group_a")


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="gpu-router supervises one sd-server at a time and relays to it: "
                          "config, supervisor, scheduler, retries, proxy and unload "
                          "against an adversarial stub child",
                    opts=opts, mode="grouped", cid_width=30)
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]
    del LOG_PATHS[:]

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    WRITES.append(fixture_root)
    try:
        for name in GROUP_ORDER:
            group = globals().get(name)
            if group is None:
                continue
            if name == "group_a":
                group(suite, fixture_root, pyc_before, tree_before)
            else:
                group(suite, fixture_root)
    finally:
        if opts.keep:
            print("\n[--keep] fixtures retained at: %s" % fixture_root)
        else:
            shutil.rmtree(fixture_root, ignore_errors=True)

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
