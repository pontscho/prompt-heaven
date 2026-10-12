#!/usr/bin/env python3
"""Suite for mcp-sandbox (Scripts/mcp-sandbox.py): the sandboxed script
runner. Step 7 writes groups A and F -- the one JSON config file and the MCP
surface of the single `sandbox_call` tool; Step 8 group B (the Mach-O reader),
Step 9 group C (run_script parameter validation) and Step 10 group H (the
workdir lifecycle, out/ collection, the flatten remover and the quarantine).

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run.

RED first (SC-4): this suite was written and run before Scripts/mcp-sandbox.py
existed. Every row then fails for ONE stated reason -- the server file is
missing -- and never crashes the run or ImportErrors the runner: the module is
loaded lazily, per row, inside `_guard`. F5 and F6 stay RED after Step 7 (their
reason: `capabilities` and a startable runner land in Step 12, R4:M3).

Config fixtures (M0:13, the template contract of task-030): every config is
rendered from tests/files/mcp_sandbox/config.json.tmpl, whose three
placeholders {sbx}, {work_root} and {interpreters} stand UNQUOTED where a JSON
value goes. They are filled ONLY with `text.replace(placeholder,
json.dumps(value))` -- json.dumps supplies quoting and escaping, so a path with
`"` or `\\` cannot break the JSON -- and NEVER with str.format (the template's
own braces would be read as fields). The rendered file is written mode 0600
under <repo>/.claude/tmp/test_mcp_sandbox/run-<rand>/, `work_root` is always a
fresh directory there (never null, C-M1), and the run directory is removed at
the end unless --keep.

The in-process contract these rows hold the server to (plan Steps 7 and 8):
  load_config(path) -> SandboxConfig, raising ConfigError(msg) whose message
      names the offending KEY, never its value (P8).
  MachOError; _KEG_SCAN_MAX_FILES (512, M0:11);
  _macho_slice(f, size, cputype) -> int (f a binary file object; 0 for a
      thin image); parse_macho(header_bytes, cputype) -> MachO(dylibs
      ((load command, install name), ...), rpaths) -- pure;
  dylib_closure(exe_spelling) -> Closure(spellings, kegs, images), images
      being realpaths; _homebrew_keg(real); _keg_images(keg) -> list;
  _symlink_hops(path) -> (hops, real); _framework_launcher(real) ->
      Optional[(Python.app exec, Versions/<v>/Python root)].
  handle_sandbox_call(arguments: dict) -> dict; a top-level "error" key is the
      isError predicate (ADR 0010).
  HANDLERS: the module-level function table, name -> callable (or a tuple
      whose first element is the callable), the fleet's dispatch shape. F7 and
      F9 replace HANDLERS["capabilities"] with a raiser to reach layer D.
  McpServer(config).run() reads only config.limits.max_concurrent before a
      handler runs (F9 passes a SimpleNamespace with just that).
  log = logging.getLogger("mcp-sandbox").
Every other row drives the server over stdio:
  [python3, -I, -B, Scripts/mcp-sandbox.py, --config <rendered config>].

Groups:
  A  config (in-process) -- unknown keys at the top level and in `limits`,
     every numeric range at its edges, the interpreter-name enum, the config
     file's mode and symlink rules, `sbx` absolute and not world-writable, the
     worst-case response bound at exactly 16 MiB, `as_mode: absolute` refused
     on darwin, the per-interpreter `site` key (A12: a real bool, default
     false, python3 only). Each refusal is a ConfigError naming the key; each
     row has an accepted control beside it.
  B  Mach-O reader and dylib closure (in-process, Step 8) -- parse_macho on
     synthetic thin/fat headers (every bound refused as MachOError, never a
     struct.error), @rpath / @loader_path / @executable_path resolution, weak
     versus strong missing images, cycles, system paths, the image cap, the
     python framework launcher (Python.app + Versions/<v>/Python) and the
     TARGETED keg image scan capped at _KEG_SCAN_MAX_FILES = 512 (M0:11).
     Every image is built from header bytes by _MachOBuilder under
     <repo>/.claude/tmp/ -- no checked-in binaries. B18 is the one live row
     (the host's Homebrew python3; INFO off macOS or without a keg). RED
     reason before Step 8: the Mach-O function is missing from the server.
  C  run_script validation (in-process, pure, Step 9) --
     validate_run_params(params, cfg, healthy) on crafted params: every
     _valid_relpath rule (dotdot, absolute, empty and dot components, NUL,
     backslash, C0/C1/DEL, the reserved out/tmp/.sbx* first component, a
     leading dash), the NFD+casefold collision key for duplicates and for
     file-vs-dir prefixes, the file count, the running DECODED total checked
     before the next file is decoded, strict base64, timeoutMs (int not bool,
     clamped to max_timeout_ms), entry in files, args, stdin bytes, and an
     unknown member answered with the fleet's did-you-mean sentence. Each
     refusal is invalid_params naming the member / index, never the value,
     beside an accepted control. RED reason before Step 9:
     validate_run_params is missing from the server.
  F  MCP surface (stdio; F7 and F9 in-process) -- one tool, did-you-mean on
     an unknown function, unknown params refused, a non-object JSON-RPC params
     answered -32602, the capabilities shape, capabilities answering while two
     runs hold both slots, layer D answering `internal` with the exception type
     and log.exception, an omitted/empty function answering the unflagged
     status text, and (F9) the same layer-D crash driven as a framed
     tools/call through McpServer.run() on gated in-memory stdin/stdout:
     a tool result with isError true and code internal, and the next request
     still served.
  H  workdir lifecycle, cleanup and quarantine (in-process, Step 10) -- the
     0700 hex workdir, fd-relative O_EXCL|O_NOFOLLOW materialisation, out/
     collection that skips symlinks, hard links and FIFOs and reads nothing
     through a replaced out/, the free-space precheck (injected disk_usage),
     the startup sweep's name rule and its ONE shared deadline, and the
     FLATTEN remover (KD-18, M0:9) against chmod 000 / 0500 / 0100, uchg,
     deny ACEs (darwin), a 10 098-level and a 2 079-level rename-chained
     tree (H19 in a child under soft RLIMIT_NOFILE 256). H15, H16 and H18
     drive run_one with an injected remove_tree and stay RED until Steps 11
     and 12 (R4:M3). RED reason before Step 10: the Step 10 function is
     missing from the server -- checked BEFORE any hostile fixture is built,
     so a red run leaves nothing to scrub.
  D  sbx argv golden (in-process, pure, Step 11) -- build_sbx_argv on an
     InterpreterPlan built directly: the darwin golden (realpath --exec
     literals incl. Python.app, symlinked parents and /private/var/select/sh
     as --read), the linux golden with --seccomp, the absolute entry with the
     caller's args after it, the runner's own environment to sbx being
     exactly two keys (the script's arrives as --clean-env + --env), and
     (D5) python3's argv_prefix from a loaded config: -I -S -B by default,
     -I -B with "site": true. RED
     reason before Step 11: build_sbx_argv is missing from the server.
  I  live runs (stdio, Step 11, GREEN in Step 12) -- hello in python3, bash
     and node, a non-zero exit as a normal result, the UTF-8 cut on the cap
     boundary, invalid UTF-8 replaced, stdin, out/ files in utf8 and base64,
     a cancel that kills the run and removes its workdir, an sbx refusal
     answered sandbox_unavailable, an engine failure after `spawned`
     returned as a declared normal result (fixture sbx, KD-16), and (I12) a
     venv python3 whose site-packages module imports only with "site": true.
  G  startup, plans and refresh (stdio, Step 12) -- every refused start
     (missing sbx, missing / non-Mach-O interpreter, a world-writable or
     symlinked work root, a failed self-test, a held work-root lock) beside
     a start that succeeds, the AS limit above the launcher's VSZ, the
     capabilities backend's liveVerified, a refresh on a re-pointed hop, a
     refresh failure, a refresh holding its run's slot, and a cancel during
     a refresh that keeps the plan state (G13, the suite's longest row).
     RED reason before Steps 11/12 for I and G: the execution engine and the
     startup plan machinery are missing from the server -- checked BEFORE a
     runner is started, never an ImportError.
  E  the spec's acceptance escape suite (stdio, live, Step 13; NOT red
     first: the code exists) -- E1-E18 the spec's acceptance rows, E19-E34
     the risk rows. Each row is an escape attempt made from INSIDE a run by
     one probe under tests/files/mcp_sandbox/escape/ (stdlib only, ONE JSON
     line of outcomes, never a secret byte), beside a POSITIVE CONTROL -- the
     same probe run outside any sandbox, or the same action on a granted
     target -- so a broken probe cannot pass as "denied". One runner serves
     the group with MCP_SANDBOX_CANARY and MCP_PROXY_TOKEN in its OWN
     environment and a canary store under .claude/tmp/mcp_sandbox-canary-*;
     no canary value may reach a result, and every row ends with "work root
     is empty". Declared limits are recorded, never claimed closed: the
     existence oracle (E4, L5/IMPL:029) and the surviving setsid orphan
     (E10, T11).

Rows added by the egress work (domain-allowlisted network for a run; a
per-run allowlisting CONNECT proxy owned by the runner is the ONE endpoint
the sandbox may reach):
  A13-A15 the optional top-level "network" object -- absent is None and
     today's runner; allow patterns (exact or a leading "*." for strict
     subdomains; lower-case labels, no IP literal in any inet_aton spelling,
     no bare "*", no trailing dot, at most 64, no duplicates), ports (1..65535,
     default [443], at most 8), unknown keys -- each refusal naming the key.
  D6/D7 the run argv with an egress endpoint: --egress-port N (darwin) or
     --egress-socket PATH (linux) after --rlimit-core, and HTTPS_PROXY /
     HTTP_PROXY (both cases) = http://127.0.0.1:<port> with NO_PROXY empty;
     the linux URL's port is sbx's in-netns forwarder port (pinned equal).
  N  the egress proxy in-process, with a stub resolver and connector (no
     network): CONNECT-only head parsing (absolute-form 405, every malformed
     head 400, oversize 431), IP literals in every spelling refused, the
     allowlist (a "*." pattern never matches its apex), every resolved
     address global (a DNS answer mixing a public and a private address is
     refused; v4-mapped, 6to4, Teredo, NAT64, metadata, CGNAT, multicast),
     the policy refusing before any lookup, one lookup and a connect to the
     vetted address itself, the head's TOTAL deadline (a trickle is cut),
     the concurrent tunnel cap, close() ending every tunnel and thread, a
     metadata-only log, and the unix endpoint linux uses.
  I13-I16 live through the real runner (macOS; linux too when bwrap is on
     PATH): urllib HTTPS to the one allowlisted host (INFO when the host
     itself cannot reach it), a non-allowlisted host / port / plain-HTTP
     request refused by the proxy, a direct socket to an outside address or
     another loopback port denied, DNS denied inside. I17 (with the I runner,
     no "network"): capabilities says disabled, no proxy variable, no egress
     member, loopback denied -- today's behaviour.

Nested-sandbox INFO path (P23): F6 first runs one control sbx invocation; no
result plus "sandbox_apply" on its stderr means a nested Seatbelt that refuses
every profile, and F6 is recorded INFO with the stderr's first 200 chars. The
same control decides a stdio row whose runner FAILED TO START: under a nested
Seatbelt that is the host, not the server, so it is INFO; anywhere else the
failed start is a FAIL.

Usage:
  python3 tests/test_mcp_sandbox.py [--brief] [--keep]
Exit code 0 iff every case passes.
"""

import asyncio
import base64
import collections
import errno
import json
import logging
import math
import os
import platform
import re
import select
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
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "mcp_sandbox"
SERVER = H.repo_path("Scripts", "mcp-sandbox.py")
SBX = H.repo_path("ClaudeCode", "skills", "sandbox-run", "scripts", "sbx")
TEMPLATE = H.repo_path("tests", "files", "mcp_sandbox", "config.json.tmpl")
SCRATCH = H.repo_path(".claude", "tmp", "test_mcp_sandbox")
TOOL = "sandbox_call"
LOGGER = "mcp-sandbox"

GRP_A = "A. config"
GRP_B = "B. Mach-O reader and dylib closure"
GRP_C = "C. run_script validation"
GRP_D = "D. sbx argv golden"
GRP_F = "F. MCP surface"
GRP_H = "H. workdir lifecycle, cleanup and quarantine"
GRP_I = "I. live runs"
GRP_G = "G. startup, plans and refresh"
GRP_E = "E. escape suite (live)"
GRP_N = "N. egress proxy (in-process)"

MIB = 1024 * 1024
RESPONSE_BOUND = 16 * MIB          # plan section 5: worst case <= 16 MiB
OUT_PATH_MAX = 512                 # an out/ path longer than this is skipped
QUARANTINE_MAX = 16                # KD-18 step 6: _QUARANTINE_MAX
FUNCTIONS = ["capabilities", "run_script"]
ERROR_CODES = ("busy", "sandbox_unavailable", "invalid_params", "internal")

# The runner's startup may plan, VSZ-probe and self-test every interpreter
# (Step 12): the KD-17 startup bound is 20 s sweep + 75 s plans = 95 s.
STARTUP_TIMEOUT = 120.0
RPC_TIMEOUT = 30.0
RUN_TIMEOUT = 60.0


class _Red(Exception):
    """The one stated RED reason: the server file does not exist yet."""


class _Info(Exception):
    """A row the host cannot measure (nested Seatbelt, P23): INFO, counted."""


class _WireError(RuntimeError):
    """The stdio server did not answer (timeout, EOF, exit)."""


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

def _rec(suite, group, name, problems, extra=(), status=None):
    problems = list(problems)
    if status is None:
        status = H.FAIL if problems else H.PASS
    brief = "%s | %s%s" % (status, name,
                           (" | " + "; ".join(problems)) if problems else "")
    suite.record(group, name, problems, status=status, detail=list(extra),
                 brief=brief, text="; ".join(problems))


def _guard(suite, group, name, fn):
    """Record `fn()`'s problem list; an exception is a FAIL, never a crash --
    the RED run has to report every case, not stop at the first one."""
    status = None
    try:
        problems, extra = fn()
    except _Red as exc:
        problems, extra = ["RED: %s" % exc], ()
    except _Info as exc:
        problems, extra, status = [], ["INFO: %s" % exc], H.INFO
    except Exception as exc:              # noqa: BLE001 -- reported, not hidden
        problems, extra = ["raised %s: %s" % (type(exc).__name__, exc)], ()
    _rec(suite, group, name, problems, extra, status=status)


# ---------------------------------------------------------------------------
# the server: in-process module
# ---------------------------------------------------------------------------

_MODULE = {}


def _require_server():
    if not os.path.isfile(SERVER):
        raise _Red("Scripts/mcp-sandbox.py does not exist (Step 7 not "
                   "implemented)")


def _mod():
    """The server loaded in-process, once, bytecode-free."""
    if "m" not in _MODULE:
        _require_server()
        _MODULE["m"] = H.load_module_from_path("mcp_sandbox_suite", SERVER)
    return _MODULE["m"]


# ---------------------------------------------------------------------------
# fixtures: rendered configs
# ---------------------------------------------------------------------------

def _which(name, fallback=None):
    path = shutil.which(name)
    return os.path.abspath(path) if path else fallback


def _interpreters(full=True):
    """The configured interpreters: python3 always (absolute spelling); bash
    and node when `full` and present on the host."""
    out = {"python3": {"path": _which("python3", sys.executable)}}
    if full:
        if os.path.isfile("/bin/bash"):
            out["bash"] = {"path": "/bin/bash"}
        node = _which("node")
        if node:
            out["node"] = {"path": node}
    return out


class Fixtures:
    """Rendered configs and work roots under
    <repo>/.claude/tmp/test_mcp_sandbox/run-<rand>/."""

    def __init__(self, keep=False):
        self.keep = keep
        os.makedirs(SCRATCH, exist_ok=True)
        self.base = os.path.realpath(
            tempfile.mkdtemp(prefix="run-", dir=SCRATCH))
        self._n = 0
        with open(TEMPLATE, "r", encoding="utf-8") as fh:
            self.template = fh.read()

    def _name(self, label):
        self._n += 1
        return "%s-%02d" % (label, self._n)

    def work_root(self, label):
        """A fresh work root: 0700, owned by the euid, never null (C-M1)."""
        return os.path.realpath(tempfile.mkdtemp(
            prefix=self._name(label) + "-wr-", dir=self.base))

    def render(self, label, sbx=SBX, interpreters=None, mutate=None,
               mode=0o600):
        """Render the template (str.replace + json.dumps ONLY, never
        str.format), optionally edit the parsed object, write it `mode`."""
        if interpreters is None:
            interpreters = _interpreters()
        text = self.template
        text = text.replace("{sbx}", json.dumps(sbx))
        text = text.replace("{work_root}",
                            json.dumps(self.work_root(label)))
        text = text.replace("{interpreters}", json.dumps(interpreters))
        if mutate is not None:
            obj = json.loads(text)
            mutate(obj)
            text = json.dumps(obj, indent=2)
        path = os.path.join(self.base, self._name(label) + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, mode)
        return path

    def sbx_copy(self, label, mode):
        """A private copy of the real sbx, chmod'ed to `mode`."""
        path = os.path.join(self.base, self._name(label) + "-sbx")
        shutil.copyfile(SBX, path)
        os.chmod(path, mode)
        return path

    def cleanup(self):
        if self.keep:
            print("\n[--keep] fixtures retained at: %s" % self.base)
            return
        shutil.rmtree(self.base, ignore_errors=True)


def _set_limit(key, value):
    def mutate(obj):
        obj["limits"][key] = value
    return mutate


def _set_limits(**values):
    def mutate(obj):
        obj["limits"].update(values)
    return mutate


def _set_network(value):
    """A config with the optional top-level "network" object set to
    `value` (any JSON value: the refusal rows pass non-objects too)."""
    def mutate(obj):
        obj["network"] = value
    return mutate


# ---------------------------------------------------------------------------
# config verdicts
# ---------------------------------------------------------------------------

def _accepts(mod, path, label, problems):
    """`path` must load; returns the config or None."""
    try:
        return mod.load_config(path)
    except mod.ConfigError as exc:
        problems.append("%s: refused (%s), want accepted" % (label, exc))
    return None


def _refuses(mod, path, label, problems, names=(), not_values=()):
    """`path` must raise ConfigError; its message must name every key in
    `names` and none of the `not_values` (P8: the key, never the value)."""
    try:
        mod.load_config(path)
    except mod.ConfigError as exc:
        msg = str(exc)
        for key in names:
            if key not in msg:
                problems.append("%s: refusal %r does not name %r"
                                % (label, msg, key))
        for value in not_values:
            if value in msg:
                problems.append("%s: refusal %r echoes the value %r"
                                % (label, msg, value))
        return msg
    problems.append("%s: accepted, want ConfigError" % label)
    return None


# ---------------------------------------------------------------------------
# A: config (in-process)
# ---------------------------------------------------------------------------

def _run_a(suite, fx):
    def unknown_top():
        mod = _mod()
        problems = []
        cfg = _accepts(mod, fx.render("a1-ok"), "template control", problems)
        if cfg is not None:
            if getattr(cfg.limits, "max_timeout_ms", None) != 120000:
                problems.append("control: limits.max_timeout_ms %r, want "
                                "120000" % (getattr(cfg.limits,
                                                    "max_timeout_ms", None),))
        msg = _refuses(mod, fx.render(
            "a1", mutate=lambda o: o.__setitem__("extra_key", True)),
            "top-level extra_key", problems, names=("extra_key",))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_A, "A1 unknown-top-key-refused", unknown_top)

    def unknown_limit():
        mod = _mod()
        problems = []
        msg = _refuses(mod, fx.render("a2", mutate=_set_limit("bogus_limit",
                                                              1)),
                       "limits.bogus_limit", problems, names=("bogus_limit",))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_A, "A2 unknown-limit-key-refused", unknown_limit)

    def out_of_range():
        mod = _mod()
        problems = []
        refused = (
            ("max_timeout_ms", 120001), ("max_timeout_ms", 99),
            ("timeout_ms", 99), ("timeout_ms", 120001),
            ("as_budget_bytes", 64 * MIB - 1),
            ("as_budget_bytes", 16 * 1024 * MIB + 1),
            ("nproc_headroom", 7), ("nproc_headroom", 513),
            ("fsize_bytes", MIB - 1), ("nofile", 31), ("nofile", 4097),
            ("stdout_bytes", 1023), ("max_files", 0), ("max_files", 257),
            ("max_out_files", 65), ("max_args", 257),
            ("max_arg_chars", 65537),
            ("max_concurrent", 0), ("max_concurrent", 9),
            ("fsize_bytes", True), ("nofile", 256.0), ("nofile", "256"),
        )
        for key, value in refused:
            label = "limits.%s=%r" % (key, value)
            not_values = (str(value),) if str(value) in ("120001",
                                                         "4097") else ()
            _refuses(mod, fx.render("a3", mutate=_set_limit(key, value)),
                     label, problems, names=(key,), not_values=not_values)
        accepted = (
            ("timeout_ms", 100), ("max_timeout_ms", 120000),
            ("as_budget_bytes", 64 * MIB), ("nproc_headroom", 8),
            ("nproc_headroom", 512), ("nofile", 32), ("nofile", 4096),
            ("max_files", 1), ("max_files", 256), ("max_out_files", 0),
            ("max_out_files", 64), ("max_concurrent", 1),
            ("max_concurrent", 8),
        )
        for key, value in accepted:
            _accepts(mod, fx.render("a3-ok", mutate=_set_limit(key, value)),
                     "limits.%s=%r (edge)" % (key, value), problems)
        return problems, ["refused     : %d values" % len(refused),
                          "edges ok    : %d values" % len(accepted)]
    _guard(suite, GRP_A, "A3 limit-out-of-range-refused", out_of_range)

    def interp_enum():
        mod = _mod()
        problems = []
        py = _interpreters(full=False)["python3"]
        for bad in ("ruby", "python", "Python3", "sh"):
            interps = {"python3": py, bad: {"path": py["path"]}}
            _refuses(mod, fx.render("a4", interpreters=interps),
                     "interpreters.%s" % bad, problems,
                     names=("interpreters",))
        _accepts(mod, fx.render("a4-ok", interpreters={"python3": py}),
                 "interpreters {python3} control", problems)
        return problems, []
    _guard(suite, GRP_A, "A4 interpreter-name-not-in-enum-refused",
           interp_enum)

    def group_writable():
        mod = _mod()
        problems = []
        for mode in (0o620, 0o660, 0o602, 0o666):
            _refuses(mod, fx.render("a5", mode=mode),
                     "config mode %04o" % mode, problems)
        for mode in (0o600, 0o644):
            _accepts(mod, fx.render("a5-ok", mode=mode),
                     "config mode %04o control" % mode, problems)
        return problems, []
    _guard(suite, GRP_A, "A5 config-group-writable-refused", group_writable)

    def symlinked():
        mod = _mod()
        problems = []
        real = fx.render("a6")
        link = real + ".lnk"
        os.symlink(real, link)
        _refuses(mod, link, "config reached through a symlink", problems)
        _accepts(mod, real, "the symlink's target control", problems)
        return problems, []
    _guard(suite, GRP_A, "A6 config-symlink-refused", symlinked)

    def sbx_relative():
        mod = _mod()
        problems = []
        rel = os.path.relpath(SBX, H.REPO_ROOT)
        for value in (rel, "sbx", ""):
            _refuses(mod, fx.render("a7", sbx=value), "sbx %r" % value,
                     problems, names=("sbx",))
        _accepts(mod, fx.render("a7-ok", sbx=SBX), "absolute sbx control",
                 problems)
        return problems, []
    _guard(suite, GRP_A, "A7 sbx-not-absolute-refused", sbx_relative)

    def sbx_writable():
        mod = _mod()
        problems = []
        for mode in (0o757, 0o777, 0o775):
            copy = fx.sbx_copy("a8", mode)
            _refuses(mod, fx.render("a8", sbx=copy),
                     "sbx mode %04o" % mode, problems, names=("sbx",))
        copy = fx.sbx_copy("a8-ok", 0o755)
        _accepts(mod, fx.render("a8-ok", sbx=copy),
                 "sbx mode 0755 control", problems)
        return problems, []
    _guard(suite, GRP_A, "A8 sbx-world-writable-refused", sbx_writable)

    def response_bound():
        mod = _mod()
        problems = []

        # Validation round 1: every capped byte is carried twice, at most 6
        # chars in structuredContent ("\u0001") and 7 in the text copy (the
        # same JSON dumped again: "\\u0001"); an out/ file's path is at most
        # OUT_PATH_MAX bytes, at the same 13 x, plus its keys.
        def worst(s, e, o, f):
            return 13 * (s + e + o) + f * (13 * OUT_PATH_MAX + 256) + 16384

        # Every value inside its own range; the sum lands exactly on 16 MiB.
        edge = dict(stdout_bytes=119296, stderr_bytes=119296,
                    max_out_bytes=MIB, max_out_files=4)
        if worst(119296, 119296, MIB, 4) != RESPONSE_BOUND:
            problems.append("fixture: the edge config is not exactly 16 MiB")
        _accepts(mod, fx.render("a9-edge", mutate=_set_limits(**edge)),
                 "worst case exactly 16 MiB", problems)
        over = dict(edge, stderr_bytes=119297)
        _refuses(mod, fx.render("a9-over", mutate=_set_limits(**over)),
                 "worst case 16 MiB + 13 bytes", problems)
        big = dict(stdout_bytes=4 * MIB, stderr_bytes=4 * MIB)
        _refuses(mod, fx.render("a9-big", mutate=_set_limits(**big)),
                 "4 MiB stdout + 4 MiB stderr (each in range)", problems)
        return problems, ["edge        : %d bytes" % RESPONSE_BOUND,
                          "over        : %d bytes"
                          % worst(119296, 119297, MIB, 4)]
    _guard(suite, GRP_A, "A9 worst-case-response-bound", response_bound)

    def reply_within_bound():
        """Validation round 1: the config load_config ACCEPTS at the edge,
        filled with the worst bytes there are (C0 controls everywhere: 6
        chars in structuredContent, 7 in the text copy), must serialize to a
        frame of at most 16 MiB through the server's own tool-result path.
        Formula-agnostic: the edge is found by asking load_config."""
        mod = _mod()
        problems = []
        files, out_bytes = 4, MIB

        def accepted(s):
            path = fx.render("a11-%d" % s, mutate=_set_limits(
                stdout_bytes=s, stderr_bytes=s, max_out_bytes=out_bytes,
                max_out_files=files))
            try:
                mod.load_config(path)
                return True
            except mod.ConfigError:
                return False
        lo, hi = 1024, 4 * MIB
        if not accepted(lo):
            return ["fixture: stdout/stderr 1024 with a 1 MiB out/ refused"], []
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if accepted(mid):
                lo = mid
            else:
                hi = mid - 1
        s = lo
        # The longest path collect_out keeps (H24), all C0 controls.
        path = "/".join(["\x01" * 255, "\x01" * (OUT_PATH_MAX - 256)])
        result = {
            "exitCode": 0, "signal": None, "timedOut": False,
            "durationMs": 120000, "stdout": "\x01" * s, "stderr": "\x01" * s,
            "stdoutTruncated": True, "stderrTruncated": True,
            "outFiles": [{"path": path,
                          "content": "\x01" * (out_bytes // files),
                          "encoding": "utf8"} for _ in range(files)],
            "outFilesTruncated": True,
        }
        saved = []
        _patch(mod, saved, "handle_sandbox_call", lambda _args: result)
        try:
            fake = types.SimpleNamespace(_result=mod.McpServer._result,
                                         _error=mod.McpServer._error)
            response = mod.McpServer._handle_tool_call(
                fake, 2 ** 53, {"name": TOOL, "arguments": {}})
        finally:
            _restore(mod, saved)
        frame = len(mod._strict_dumps(response).encode("utf-8")) + 1
        if frame > RESPONSE_BOUND:
            problems.append("stdout/stderr %d bytes (the largest load_config "
                            "accepts) serialize to a %d-byte frame, over the "
                            "%d-byte bound by %d" % (s, frame, RESPONSE_BOUND,
                                                     frame - RESPONSE_BOUND))
        return problems, ["edge        : stdout/stderr %d, out/ %d in %d files"
                          % (s, out_bytes, files),
                          "frame       : %d of %d bytes" % (frame,
                                                            RESPONSE_BOUND)]
    _guard(suite, GRP_A, "A11 worst-case-reply-serializes-within-bound",
           reply_within_bound)

    def as_mode():
        mod = _mod()
        problems = []
        _accepts(mod, fx.render("a10-ok", mutate=_set_limit("as_mode",
                                                              "baseline")),
                 "as_mode baseline control", problems)
        _refuses(mod, fx.render("a10-bad", mutate=_set_limit("as_mode",
                                                               "relative")),
                 "as_mode relative", problems, names=("as_mode",))
        path = fx.render("a10", mutate=_set_limit("as_mode", "absolute"))
        if sys.platform == "darwin":
            _refuses(mod, path, "as_mode absolute on darwin", problems,
                     names=("as_mode",))
        else:
            _accepts(mod, path, "as_mode absolute off darwin", problems)
        return problems, ["platform    : %s" % sys.platform]
    _guard(suite, GRP_A, "A10 as-mode-absolute-refused-on-darwin", as_mode)

    def interp_site():
        """The per-interpreter `site` key: a real JSON bool, default false,
        accepted ONLY on python3 (it drops -S, so a venv interpreter's
        site-packages reaches sys.path); on bash and node the key itself is
        refused, naming it. Unknown interpreter keys are still refused."""
        mod = _mod()
        problems = []
        py = _interpreters(full=False)["python3"]
        lines = []

        def site_of(cfg, name="python3"):
            return getattr(cfg.interpreters[name], "site", "<absent>")

        cfg = _accepts(mod, fx.render("a12-default",
                                      interpreters={"python3": dict(py)}),
                       "python3 without site (default)", problems)
        if cfg is not None and site_of(cfg) is not False:
            problems.append("python3 site default %r, want False"
                            % (site_of(cfg),))
        for value in (True, False):
            cfg = _accepts(mod, fx.render(
                "a12-py", interpreters={"python3": dict(py, site=value)}),
                "python3 site %r" % value, problems)
            if cfg is not None and site_of(cfg) is not value:
                problems.append("python3 site %r loaded as %r"
                                % (value, site_of(cfg)))
        for value in (1, 0, 1.0, "yes-a12", None, [], {}):
            msg = _refuses(mod, fx.render(
                "a12-bad", interpreters={"python3": dict(py, site=value)}),
                "python3 site %r" % (value,), problems,
                names=("interpreters.python3.site",),
                not_values=("yes-a12",))
            if value == "yes-a12":
                lines.append("non-bool    : %r" % (msg,))
        for name, path in (("bash", "/bin/bash"),
                           ("node", "/usr/local/bin/node")):
            for value in (True, False):
                interps = {"python3": dict(py),
                           name: {"path": path, "site": value}}
                msg = _refuses(mod, fx.render("a12-%s" % name,
                                              interpreters=interps),
                               "%s site %r" % (name, value), problems,
                               names=("interpreters.%s.site" % name,))
                if value:
                    lines.append("%-12s: %r" % (name, msg))
            _accepts(mod, fx.render("a12-%s-ok" % name, interpreters={
                "python3": dict(py, site=True), name: {"path": path}}),
                "%s without site beside python3 site true" % name, problems)
        _refuses(mod, fx.render("a12-unknown", interpreters={
            "python3": dict(py, sites=True)}),
            "python3 sites (unknown key)", problems, names=("sites",))
        return problems, lines
    _guard(suite, GRP_A, "A12 interpreter-site-bool-python3-only",
           interp_site)

    def network_absent():
        """No "network" key is today's runner (no egress at all); a valid
        object loads as NetworkConfig(allow, ports) with ports defaulting
        to (443,)."""
        mod = _mod()
        problems = []
        cfg = _accepts(mod, fx.render("a13-none"), "template (no network)",
                       problems)
        if cfg is not None and getattr(cfg, "network", "<absent>") is not None:
            problems.append("no network key: cfg.network %r, want None"
                            % (getattr(cfg, "network", "<absent>"),))
        for label, net, allow, ports in (
                ("one host", {"allow": ["api.github.com"]},
                 ("api.github.com",), (443,)),
                ("wildcard + ports", {"allow": ["*.example.org",
                                                "xn--bcher-kva.example"],
                                      "ports": [443, 8443]},
                 ("*.example.org", "xn--bcher-kva.example"), (443, 8443))):
            cfg = _accepts(mod, fx.render("a13", mutate=_set_network(net)),
                           label, problems)
            got = getattr(cfg, "network", None) if cfg is not None else None
            if cfg is not None and (
                    tuple(getattr(got, "allow", ()) or ()) != allow
                    or tuple(getattr(got, "ports", ()) or ()) != ports):
                problems.append("%s: loaded %r, want allow %r ports %r"
                                % (label, got, allow, ports))
        return problems, []
    _guard(suite, GRP_A, "A13 network-absent-none-present-loaded",
           network_absent)

    def network_patterns():
        """D1's grammar: lower-case hostnames, exact or a leading "*." that
        matches strict subdomains, labels 1-63 of [a-z0-9-] not starting or
        ending with "-", no IP literal in any spelling, no bare "*", no
        trailing dot, at most 64 patterns, no duplicates. Each refusal names
        network.allow, never the value."""
        mod = _mod()
        problems = []
        label63 = "a" * 63
        for ok in ("api.github.com", "*.example.org", "a.b-c.d",
                   label63 + ".example", "xn--bcher-kva.example",
                   "123abc.example.com", "localhostlike"):
            _accepts(mod, fx.render("a14-ok", mutate=_set_network(
                {"allow": [ok]})), "pattern %r" % ok, problems)
        bad = ("API.github.com", "*", "*.", "a.*.b", "*foo.com", "*.*.com",
               "foo.com.", "1.2.3.4", "127.1", "0x7f000001", "2130706433",
               "0177.0.0.1", "[::1]", "::1", "foo..com", "-a.com", "a-.com",
               "a_b.com", "a" * 64 + ".com", "", "foo.com:443", "foo.123",
               "tf-a14-" + chr(0xE9) + ".com", " a.com", "a .com")
        for value in bad:
            _refuses(mod, fx.render("a14", mutate=_set_network(
                {"allow": ["ok.example", value]})), "pattern %r" % value,
                problems, names=("network.allow",),
                not_values=(value,) if value.startswith("tf-a14") else ())
        for label, net in (
                ("not a list", {"allow": "api.github.com"}),
                ("empty", {"allow": []}),
                ("non-string", {"allow": [1]}),
                ("65 patterns", {"allow": ["h%d.example" % i
                                           for i in range(65)]}),
                ("duplicate", {"allow": ["a.example", "a.example"]})):
            _refuses(mod, fx.render("a14-shape", mutate=_set_network(net)),
                     label, problems, names=("network.allow",))
        _accepts(mod, fx.render("a14-64", mutate=_set_network(
            {"allow": ["h%d.example" % i for i in range(64)]})),
            "64 patterns (edge)", problems)
        return problems, ["refused     : %d patterns" % len(bad)]
    _guard(suite, GRP_A, "A14 network-allow-pattern-grammar",
           network_patterns)

    def network_ports():
        """ports: integers 1..65535, never coerced, at most 8, no
        duplicates; network is an object with allow and optional ports,
        nothing else, and an explicit null is refused like a non-object."""
        mod = _mod()
        problems = []
        _accepts(mod, fx.render("a15-ok", mutate=_set_network(
            {"allow": ["a.example"], "ports": [1, 65535]})),
            "ports [1, 65535] (edges)", problems)
        _accepts(mod, fx.render("a15-8", mutate=_set_network(
            {"allow": ["a.example"], "ports": list(range(440, 448))})),
            "8 ports (edge)", problems)
        for value in ([0], [65536], [True], ["443"], [443.0], [], 443,
                      list(range(440, 449)), [443, 443], None):
            _refuses(mod, fx.render("a15", mutate=_set_network(
                {"allow": ["a.example"], "ports": value})),
                "ports %r" % (value,), problems, names=("network.ports",))
        for label, net, name in (
                ("unknown key", {"allow": ["a.example"], "deny": []}, "deny"),
                ("missing allow", {"ports": [443]}, "allow"),
                ("not an object", ["a.example"], "network"),
                ("null", None, "network")):
            _refuses(mod, fx.render("a15-shape", mutate=_set_network(net)),
                     label, problems, names=(name,))
        return problems, []
    _guard(suite, GRP_A, "A15 network-ports-and-shape", network_ports)

    def hex_letter_tlds():
        """Security round 2 (H): a last label of plain hex LETTERS is a TLD,
        not a number -- only all-decimal, 0x-prefixed and leading-0 octal
        labels are inet_aton number forms."""
        mod = _mod()
        problems = []
        for ok in ("example.be", "a.cafe", "x.ad", "y.bad", "z.dad",
                   "*.example.be"):
            _accepts(mod, fx.render("a16-ok", mutate=_set_network(
                {"allow": [ok]})), "pattern %r" % ok, problems)
        for value in ("1.2.3.4", "0x7f.1", "017", "a.0x1f", "a.123"):
            _refuses(mod, fx.render("a16", mutate=_set_network(
                {"allow": [value]})), "pattern %r" % value, problems,
                names=("network.allow",))
        return problems, []
    _guard(suite, GRP_A, "A16 network-allow-hex-letter-tlds-accepted",
           hex_letter_tlds)

    def wildcard_two_labels():
        """Security round 2 (F4): a "*." pattern over a single label would
        allow a whole top-level domain; refused, naming the key."""
        mod = _mod()
        problems = []
        for value in ("*.com", "*.io"):
            _refuses(mod, fx.render("a17", mutate=_set_network(
                {"allow": ["ok.example", value]})), "pattern %r" % value,
                problems, names=("network.allow[1]",))
        _accepts(mod, fx.render("a17-ok", mutate=_set_network(
            {"allow": ["*.example.com"]})), "*.example.com", problems)
        return problems, []
    _guard(suite, GRP_A, "A17 network-allow-wildcard-needs-two-labels",
           wildcard_two_labels)

    def nat64_prefixes():
        """Security round 2 (F5b): network.nat64 is an optional list of at
        most 8 distinct IPv6 prefixes of an RFC 6052 length; each refusal
        names the key, never the value; absent loads as ()."""
        mod = _mod()
        problems = []
        cfg = _accepts(mod, fx.render("a18-absent", mutate=_set_network(
            {"allow": ["a.example"]})), "no nat64", problems)
        if cfg is not None and tuple(getattr(cfg.network, "nat64",
                                             None) or ()) != ():
            problems.append("no nat64: %r, want ()"
                            % (getattr(cfg.network, "nat64", None),))
        good = ["2606:4700:64::/96", "2001:db8:100::/40", "2001:db8::/32"]
        cfg = _accepts(mod, fx.render("a18-ok", mutate=_set_network(
            {"allow": ["a.example"], "nat64": good})), "three prefixes",
            problems)
        if cfg is not None and [str(n) for n in getattr(
                cfg.network, "nat64", ())] != good:
            problems.append("loaded %r, want %r"
                            % (getattr(cfg.network, "nat64", None), good))
        for label, value, name in (
                ("not a list", "2606:4700:64::/96", "network.nat64"),
                ("non-string", [64], "network.nat64[0]"),
                ("v4", ["10.64.0.0/16"], "network.nat64[0]"),
                ("bad length", ["2606:4700:64::/80"], "network.nat64[0]"),
                ("host bits", ["2606:4700:64::1/96"], "network.nat64[0]"),
                ("garbage", ["tf-a18-nope"], "network.nat64[0]"),
                ("duplicate", ["2606:4700:64::/96", "2606:4700:64:0::/96"],
                 "network.nat64"),
                ("9 prefixes", ["2606:4700:%x::/48" % i for i in range(9)],
                 "network.nat64")):
            _refuses(mod, fx.render("a18", mutate=_set_network(
                {"allow": ["a.example"], "nat64": value})), label, problems,
                names=(name,), not_values=("tf-a18-nope", "2606:4700:64::"))
        return problems, []
    _guard(suite, GRP_A, "A18 network-nat64-prefixes", nat64_prefixes)

    def reads_over_work_root():
        """Security round 2 (F9): an extra_reads entry that is the work root
        or one of its ancestors -- spelled or through a symlink -- would
        expose sibling runs' workdirs and egress sockets; refused, naming
        interpreters.<name>.extra_reads[i]."""
        mod = _mod()
        problems = []
        link = os.path.join(fx.base, "a19-link")

        def with_reads(pick):
            def mutate(obj):
                wr = obj["work_root"]
                obj["interpreters"]["python3"]["extra_reads"] = [
                    "/usr/share", pick(wr)]
            return mutate

        def via_link(wr):
            if not os.path.lexists(link):
                os.symlink(os.path.dirname(wr), link)
            return link
        for label, pick in (
                ("the work root", lambda wr: wr),
                ("its parent", os.path.dirname),
                ("the root", lambda wr: "/"),
                ("a symlink to its parent", via_link)):
            _refuses(mod, fx.render("a19", interpreters=_interpreters(False),
                                    mutate=with_reads(pick)),
                     label, problems,
                     names=("interpreters.python3.extra_reads[1]",))
        _accepts(mod, fx.render("a19-ok", interpreters=_interpreters(False),
                                mutate=with_reads(
                                    lambda wr: os.path.join(wr + "-sib"))),
                 "a sibling sharing a name prefix", problems)
        return problems, []
    _guard(suite, GRP_A, "A19 extra-reads-over-work-root-refused",
           reads_over_work_root)


# ---------------------------------------------------------------------------
# B: Mach-O reader and dylib closure (in-process, synthetic images)
# ---------------------------------------------------------------------------

MH_MAGIC_64 = 0xfeedfacf
FAT_MAGIC = 0xcafebabe
FAT_MAGIC_64 = 0xcafebabf
CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000c
MH_EXECUTE = 0x2
MH_DYLIB = 0x6
LC_LOAD_DYLIB = 0xc
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001f
LC_LAZY_LOAD_DYLIB = 0x20
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_RPATH = 0x8000001c
LC_SEGMENT_64 = 0x19
KEG_SCAN_MAX_FILES = 512           # M0:11, item-5.md: 76 x 4 -> 512
IMAGE_MAX = 512                    # plan Step 8: images <= 512
RPATH_STACK_MAX = 256              # C1: deduped rpath entries per image
KEG_SCAN_MAX_DIRS = 4096           # C1: node_modules directories walked
KEG_SCAN_MAX_DEPTH = 32            # C1: node_modules levels below its root
KEG_DIR_MAX_ENTRIES = 4096         # C1: entries read from one keg directory
CLOSURE_TIMEOUT = 60.0             # a closure that never ends is the finding

_MACHO_FUNCS = ("MachOError", "_macho_slice", "parse_macho", "dylib_closure",
                "_homebrew_keg", "_keg_images", "_framework_launcher",
                "_symlink_hops")


def _native_cpu():
    """The cputype the server picks from platform.machine() (plan Step 8)."""
    if platform.machine().lower() in ("arm64", "aarch64"):
        return CPU_TYPE_ARM64
    return CPU_TYPE_X86_64


def _foreign_cpu():
    return CPU_TYPE_X86_64 if _native_cpu() == CPU_TYPE_ARM64 \
        else CPU_TYPE_ARM64


def _macho_mod(*names):
    """The server module, holding it to the Step 8 names. A missing one is the
    stated RED reason (the Mach-O reader is not implemented yet), never an
    AttributeError or an ImportError."""
    mod = _mod()
    missing = [n for n in (names or _MACHO_FUNCS) if not hasattr(mod, n)]
    if missing:
        raise _Red("Scripts/mcp-sandbox.py has no %s (Step 8 Mach-O reader "
                   "not implemented)" % ", ".join(missing))
    return mod


class _MachOBuilder:
    """Synthetic Mach-O images built from header bytes (no checked-in
    binaries): mach_header_64 + load commands, little-endian, 8-aligned;
    fat headers big-endian. Files land under the run's scratch dir."""

    def __init__(self, base):
        self.base = base

    # -- load commands ------------------------------------------------------

    @staticmethod
    def _pad8(data):
        return data + b"\0" * (-len(data) % 8)

    @classmethod
    def lc_dylib(cls, name, cmd_id=LC_LOAD_DYLIB):
        """dylib_command: cmd, cmdsize, name.offset = 24, timestamp,
        current_version, compatibility_version; then the NUL-terminated
        name, padded to 8."""
        raw = cls._pad8(struct.pack("<6I", cmd_id, 0, 24, 2, 0x10000, 0x10000)
                        + name.encode("utf-8") + b"\0")
        return struct.pack("<II", cmd_id, len(raw)) + raw[8:]

    @classmethod
    def lc_rpath(cls, path):
        raw = cls._pad8(struct.pack("<3I", LC_RPATH, 0, 12)
                        + path.encode("utf-8") + b"\0")
        return struct.pack("<II", LC_RPATH, len(raw)) + raw[8:]

    @staticmethod
    def lc_raw(cmd_id, cmdsize, payload):
        """A command whose fields are exactly what the caller says -- the
        malformed-image rows."""
        return struct.pack("<II", cmd_id, cmdsize) + payload

    @staticmethod
    def lc_segment():
        """An LC_SEGMENT_64 with no sections: a command the reader skips."""
        return struct.pack("<II16sQQQQiiII", LC_SEGMENT_64, 72,
                           b"__TEXT".ljust(16, b"\0"), 0, 0x1000, 0, 0x1000,
                           5, 5, 0, 0)

    # -- images -------------------------------------------------------------

    @staticmethod
    def thin(cmds, cpu=None, filetype=MH_DYLIB, ncmds=None, sizeofcmds=None):
        blob = b"".join(cmds)
        header = struct.pack(
            "<8I", MH_MAGIC_64, _native_cpu() if cpu is None else cpu, 3,
            filetype, len(cmds) if ncmds is None else ncmds,
            len(blob) if sizeofcmds is None else sizeofcmds, 0, 0)
        return header + blob

    @classmethod
    def image(cls, deps=(), rpaths=(), filetype=MH_DYLIB, cpu=None):
        """`deps`: install names (strong LC_LOAD_DYLIB) or (cmd, name)."""
        cmds = [cls.lc_segment()]
        for dep in deps:
            if isinstance(dep, tuple):
                cmds.append(cls.lc_dylib(dep[1], cmd_id=dep[0]))
            else:
                cmds.append(cls.lc_dylib(dep))
        for rp in rpaths:
            cmds.append(cls.lc_rpath(rp))
        return cls.thin(cmds, cpu=cpu, filetype=filetype)

    @staticmethod
    def fat(slices, magic64=False):
        """`slices`: [(cputype, bytes)]. Returns (data, [(offset, size)])."""
        entry = 32 if magic64 else 20
        offset = 0x1000
        layout = []
        for _cpu, body in slices:
            layout.append((offset, len(body)))
            offset += len(body) + (-len(body) % 0x1000)
        header = struct.pack(">II", FAT_MAGIC_64 if magic64 else FAT_MAGIC,
                             len(slices))
        for (cpu, _body), (off, size) in zip(slices, layout):
            if magic64:
                header += struct.pack(">IIQQII", cpu, 3, off, size, 12, 0)
            else:
                header += struct.pack(">IIIII", cpu, 3, off, size, 12)
        data = bytearray(header.ljust(0x1000, b"\0"))
        for (_cpu, body), (off, size) in zip(slices, layout):
            data[len(data):] = b"\0" * (off - len(data))
            data[off:off + size] = body
        return bytes(data), layout

    # -- files --------------------------------------------------------------

    def path(self, *parts):
        return os.path.join(self.base, *parts)

    def write(self, rel, data, mode=0o755):
        path = self.path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        os.chmod(path, mode)
        return path

    def link(self, rel, target):
        path = self.path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path


def _builder(fx, label):
    return _MachOBuilder(os.path.realpath(
        tempfile.mkdtemp(prefix=fx._name(label) + "-", dir=fx.base)))


def _as_macho(m):
    dylibs = tuple((int(c), str(n)) for c, n in getattr(m, "dylibs", ()))
    return dylibs, tuple(getattr(m, "rpaths", ()))


def _macho_refused(mod, label, fn, problems):
    """`fn()` must raise MachOError; anything else (a value, struct.error,
    IndexError, UnicodeDecodeError) is the finding."""
    try:
        got = fn()
    except mod.MachOError as exc:
        return str(exc)
    except Exception as exc:              # noqa: BLE001 -- reported, not hidden
        problems.append("%s: raised %s (%s), want MachOError"
                        % (label, type(exc).__name__, str(exc)[:120]))
        return None
    problems.append("%s: returned %s, want MachOError"
                    % (label, repr(got)[:160]))
    return None


def _closure(mod, spelling, timeout=CLOSURE_TIMEOUT):
    """dylib_closure on a daemon thread: a walk that never terminates is
    reported, not waited on forever. Returns (closure, exc)."""
    box = {}

    def work():
        try:
            box["v"] = mod.dylib_closure(spelling)
        except BaseException as exc:      # noqa: BLE001 -- handed back
            box["e"] = exc
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AssertionError("dylib_closure(%s) did not return within %.0fs"
                             % (spelling, timeout))
    return box.get("v"), box.get("e")


def _closure_ok(mod, spelling, label, problems):
    cl, exc = _closure(mod, spelling)
    if exc is not None:
        problems.append("%s: raised %s (%s), want a closure"
                        % (label, type(exc).__name__, str(exc)[:160]))
        return None
    return cl


def _images(cl):
    return set(getattr(cl, "images", ()) or ())


def _kegs(cl):
    return set(getattr(cl, "kegs", ()) or ())


def _spellings(cl):
    return set(getattr(cl, "spellings", ()) or ())


def _want_in(label, path, pool, problems):
    if path not in pool:
        problems.append("%s: %s missing (have %s)"
                        % (label, path, sorted(pool)[:8]))


def _want_out(label, path, pool, problems):
    if path in pool:
        problems.append("%s: %s present, want absent" % (label, path))


def _symlinked_prefixes(path):
    """Every component prefix of `path` that is itself a symlink."""
    out = []
    parts = path.split("/")
    for i in range(2, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if prefix and os.path.islink(prefix):
            out.append(prefix)
    return out


def _run_macho(suite, fx):
    B = _MachOBuilder
    native, foreign = _native_cpu(), _foreign_cpu()

    def thin64():
        mod = _macho_mod("MachOError", "parse_macho", "_macho_slice")
        b = _builder(fx, "b1")
        deps = ((LC_LOAD_DYLIB, "@rpath/liba.dylib"),
                (LC_LOAD_WEAK_DYLIB, "/opt/weak/libw.dylib"),
                (LC_REEXPORT_DYLIB, "@loader_path/libr.dylib"),
                (LC_LAZY_LOAD_DYLIB, "@executable_path/libz.dylib"),
                (LC_LOAD_UPWARD_DYLIB, "/opt/up/libu.dylib"))
        rpaths = ("@loader_path/../lib", "/opt/second/lib")
        data = b.image(deps=deps, rpaths=rpaths, filetype=MH_EXECUTE)
        problems = []
        got = _as_macho(mod.parse_macho(data, native))
        if got != (deps, rpaths):
            problems.append("parse_macho %r, want %r" % (got, (deps, rpaths)))
        path = b.write("thin", data)
        with open(path, "rb") as fh:
            off = mod._macho_slice(fh, len(data), native)
        if off != 0:
            problems.append("_macho_slice of a thin image %r, want 0" % (off,))
        return problems, ["load cmds   : %d dylib + %d rpath + 1 segment"
                          % (len(deps), len(rpaths))]
    _guard(suite, GRP_B, "B1 thin64-dylibs-and-rpaths", thin64)

    def fat_select(magic64):
        mod = _macho_mod("MachOError", "parse_macho", "_macho_slice")
        b = _builder(fx, "b2")
        mine = B.image(deps=["/opt/native/libn.dylib"], cpu=native)
        theirs = B.image(deps=["/opt/foreign/libf.dylib"], cpu=foreign)
        data, layout = B.fat([(foreign, theirs), (native, mine)],
                             magic64=magic64)
        path = b.write("fat64" if magic64 else "fat", data)
        problems = []
        for cpu, (want_off, size), want_name in (
                (native, layout[1], "/opt/native/libn.dylib"),
                (foreign, layout[0], "/opt/foreign/libf.dylib")):
            with open(path, "rb") as fh:
                off = mod._macho_slice(fh, len(data), cpu)
            if off != want_off:
                problems.append("cputype %#x: slice offset %r, want %#x"
                                % (cpu, off, want_off))
                continue
            dylibs, _ = _as_macho(mod.parse_macho(data[off:off + size], cpu))
            if dylibs != ((LC_LOAD_DYLIB, want_name),):
                problems.append("cputype %#x: dylibs %r, want %s"
                                % (cpu, dylibs, want_name))
        return problems, ["slices      : %s" % [hex(o) for o, _ in layout]]

    _guard(suite, GRP_B, "B2 fat-select-native", lambda: fat_select(False))
    _guard(suite, GRP_B, "B3 fat64-magic", lambda: fat_select(True))

    def fat_missing():
        mod = _macho_mod("MachOError", "_macho_slice")
        b = _builder(fx, "b4")
        theirs = B.image(cpu=foreign)
        problems = []
        for magic64 in (False, True):
            data, _ = B.fat([(foreign, theirs)], magic64=magic64)
            path = b.write("fat-%d" % magic64, data)
            with open(path, "rb") as fh:
                _macho_refused(mod, "fat%s without the native arch"
                               % ("64" if magic64 else ""),
                               lambda: mod._macho_slice(fh, len(data), native),
                               problems)
        # nfat_arch over the 32 cap, every entry the native arch.
        data, _ = B.fat([(native, B.image())] * 33)
        path = b.write("fat-33", data)
        with open(path, "rb") as fh:
            _macho_refused(mod, "nfat_arch 33 (> 32)",
                           lambda: mod._macho_slice(fh, len(data), native),
                           problems)
        return problems, []
    _guard(suite, GRP_B, "B4 fat-missing-arch-refused", fat_missing)

    def truncated():
        mod = _macho_mod("MachOError", "parse_macho")
        full = B.image(deps=["/opt/x/libx.dylib"])
        problems = []
        cases = (("empty", b""), ("20-byte header", full[:20]),
                 ("31-byte header", full[:31]),
                 ("commands cut short", full[:len(full) - 8]),
                 ("bad magic", b"\x7fELF" + full[4:]))
        for label, data in cases:
            _macho_refused(mod, label,
                           lambda d=data: mod.parse_macho(d, native),
                           problems)
        return problems, ["cases       : %d" % len(cases)]
    _guard(suite, GRP_B, "B5 truncated-header-refused", truncated)

    def ncmds_overflow():
        mod = _macho_mod("MachOError", "parse_macho")
        seg = B.lc_segment()
        problems = []
        cases = (
            ("ncmds 4097 (> 4096)", B.thin([seg] * 4097)),
            ("ncmds 3, one command present", B.thin([seg], ncmds=3)),
            ("ncmds 0xffffffff", B.thin([seg], ncmds=0xffffffff)),
            ("sizeofcmds 1 MiB + 8 (> 1 MiB)",
             B.thin([seg], sizeofcmds=MIB + 8)),
            ("sizeofcmds below the commands", B.thin([seg, seg],
                                                     sizeofcmds=72)),
        )
        for label, data in cases:
            _macho_refused(mod, label,
                           lambda d=data: mod.parse_macho(d, native),
                           problems)
        ok = B.thin([seg] * 4096)
        try:
            mod.parse_macho(ok, native)
        except Exception as exc:          # noqa: BLE001 -- the finding
            problems.append("ncmds 4096 control: raised %s (%s)"
                            % (type(exc).__name__, str(exc)[:120]))
        return problems, ["cases       : %d + 1 control" % len(cases)]
    _guard(suite, GRP_B, "B6 ncmds-overflow-refused", ncmds_overflow)

    def cmdsize_zero():
        mod = _macho_mod("MachOError", "parse_macho")
        problems = []
        cases = (
            ("cmdsize 0", B.thin([B.lc_raw(LC_SEGMENT_64, 0, b"\0" * 8)])),
            ("cmdsize 4 (< 8)", B.thin([B.lc_raw(LC_SEGMENT_64, 4,
                                                 b"\0" * 4)])),
            ("cmdsize 12 (not 8-aligned)",
             B.thin([B.lc_raw(LC_SEGMENT_64, 12, b"\0" * 12)])),
            ("cmdsize past sizeofcmds",
             B.thin([B.lc_raw(LC_SEGMENT_64, 4096, b"\0" * 8)])),
        )
        for label, data in cases:
            _macho_refused(mod, label,
                           lambda d=data: mod.parse_macho(d, native),
                           problems)
        return problems, ["cases       : %d" % len(cases)]
    _guard(suite, GRP_B, "B7 cmdsize-zero-refused", cmdsize_zero)

    def lcstr_offset():
        mod = _macho_mod("MachOError", "parse_macho")
        name = b"/opt/x/libx.dylib\0".ljust(24, b"\0")
        problems = []
        cases = []
        for off in (48, 49, 0xffffffff):          # cmdsize is 48
            cases.append(("dylib name.offset %#x (cmdsize 48)" % off,
                          B.lc_raw(LC_LOAD_DYLIB, 48, struct.pack(
                              "<4I", off, 2, 0x10000, 0x10000) + name)))
        cases.append(("rpath path.offset 0x40 (cmdsize 32)",
                      B.lc_raw(LC_RPATH, 32, struct.pack("<I", 0x40)
                               + b"/opt/r\0".ljust(20, b"\0"))))
        for label, cmd in cases:
            _macho_refused(mod, label,
                           lambda c=cmd: mod.parse_macho(B.thin([c]), native),
                           problems)
        return problems, ["cases       : %d" % len(cases)]
    _guard(suite, GRP_B, "B8 lcstr-offset-out-of-range-refused", lcstr_offset)

    def lcstr_nul():
        mod = _macho_mod("MachOError", "parse_macho")
        problems = []
        # 24 + 16 = 40: 8-aligned with no padding, so no stray NUL follows.
        cases = (
            ("dylib name without NUL",
             B.lc_raw(LC_LOAD_DYLIB, 40, struct.pack(
                 "<4I", 24, 2, 0x10000, 0x10000) + b"/opt/x/libxx.dyl")),
            ("rpath without NUL",
             B.lc_raw(LC_RPATH, 24, struct.pack("<I", 12)
                      + b"/opt/rpathxx")),
            ("dylib name with a newline",
             B.lc_raw(LC_LOAD_DYLIB, 48, struct.pack(
                 "<4I", 24, 2, 0x10000, 0x10000)
                 + b"/opt/x/li\nb.dylib\0".ljust(24, b"\0"))),
            ("dylib name not UTF-8",
             B.lc_raw(LC_LOAD_DYLIB, 48, struct.pack(
                 "<4I", 24, 2, 0x10000, 0x10000)
                 + b"/opt/x/\xff\xfe.dylib\0".ljust(24, b"\0"))),
        )
        for label, cmd in cases:
            _macho_refused(mod, label,
                           lambda c=cmd: mod.parse_macho(B.thin([c]), native),
                           problems)
        return problems, ["cases       : %d" % len(cases)]
    _guard(suite, GRP_B, "B9 lcstr-no-nul-refused", lcstr_nul)

    def rpath_order():
        mod = _macho_mod()
        b = _builder(fx, "b10")
        for d in ("r1", "r2"):
            b.write(d + "/libx.dylib", b.image())
        b.write("r2/liby.dylib", b.image())
        exe = b.write("bin/exe", b.image(
            deps=["@rpath/libx.dylib", "@rpath/liby.dylib"],
            rpaths=["@loader_path/../r1", "@loader_path/../r2"],
            filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "closure", problems)
        if cl is not None:
            imgs = _images(cl)
            _want_in("first rpath wins", b.path("r1", "libx.dylib"), imgs,
                     problems)
            _want_out("second rpath shadowed", b.path("r2", "libx.dylib"),
                      imgs, problems)
            _want_in("falls through to the second rpath",
                     b.path("r2", "liby.dylib"), imgs, problems)
        return problems, []
    _guard(suite, GRP_B, "B10 rpath-resolution-order", rpath_order)

    def rpath_declarer():
        mod = _macho_mod()
        b = _builder(fx, "b11")
        # exe (bin/) declares both rpaths; liba (lib/deep/) declares none and
        # loads @rpath/libc.dylib. The exe's "@loader_path/../lib2" must be
        # resolved against the exe (-> lib2/), never against liba (-> the
        # decoy lib/lib2/).
        b.write("lib/deep/liba.dylib", b.image(deps=["@rpath/libc.dylib"]))
        b.write("lib2/libc.dylib", b.image())
        b.write("lib/lib2/libc.dylib", b.image())             # decoy
        exe = b.write("bin/exe", b.image(
            deps=["@rpath/liba.dylib"],
            rpaths=["@loader_path/../lib/deep", "@loader_path/../lib2"],
            filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "closure", problems)
        if cl is not None:
            imgs = _images(cl)
            _want_in("liba via the exe's rpath",
                     b.path("lib", "deep", "liba.dylib"), imgs, problems)
            _want_in("libc against the declaring exe",
                     b.path("lib2", "libc.dylib"), imgs, problems)
            _want_out("libc against the loading liba (decoy)",
                      b.path("lib", "lib2", "libc.dylib"), imgs, problems)
        return problems, []
    _guard(suite, GRP_B, "B11 rpath-loader-path-relative-to-declarer",
           rpath_declarer)

    def executable_path():
        mod = _macho_mod()
        b = _builder(fx, "b12")
        b.write("lib/a/liba.dylib", b.image(
            deps=["@executable_path/../lib/libe.dylib"]))
        b.write("lib/libe.dylib", b.image())
        b.write("lib/lib/libe.dylib", b.image())              # decoy
        exe = b.write("bin/exe", b.image(
            deps=[b.path("lib", "a", "liba.dylib")], filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "closure", problems)
        if cl is not None:
            imgs = _images(cl)
            _want_in("@executable_path against the exe",
                     b.path("lib", "libe.dylib"), imgs, problems)
            _want_out("@executable_path against the loader (decoy)",
                      b.path("lib", "lib", "libe.dylib"), imgs, problems)
        return problems, []
    _guard(suite, GRP_B, "B12 executable-path", executable_path)

    def weak_missing():
        mod = _macho_mod()
        b = _builder(fx, "b13")
        b.write("lib/libw.dylib", b.image())
        exe = b.write("bin/exe", b.image(deps=[
            (LC_LOAD_WEAK_DYLIB, "@loader_path/../lib/nosuch.dylib"),
            (LC_LOAD_WEAK_DYLIB, b.path("nowhere", "libgone.dylib")),
            (LC_LOAD_WEAK_DYLIB, "@rpath/libnorpath.dylib"),
            (LC_LOAD_WEAK_DYLIB, "@loader_path/../lib/libw.dylib")],
            filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "three missing weak dylibs", problems)
        if cl is not None:
            _want_in("present weak dylib still walked",
                     b.path("lib", "libw.dylib"), _images(cl), problems)
        return problems, []
    _guard(suite, GRP_B, "B13 weak-missing-skipped", weak_missing)

    def strong_missing():
        mod = _macho_mod()
        b = _builder(fx, "b14")
        problems = []
        cases = (
            ("@loader_path strong missing", "@loader_path/../lib/nosuch.dylib",
             LC_LOAD_DYLIB),
            ("absolute strong missing", b.path("nowhere", "libgone.dylib"),
             LC_LOAD_DYLIB),
            ("@rpath strong with no rpath", "@rpath/libnorpath.dylib",
             LC_LOAD_DYLIB),
            ("re-export missing", "@loader_path/../lib/libre.dylib",
             LC_REEXPORT_DYLIB),
        )
        messages = []
        for i, (label, name, cmd) in enumerate(cases):
            exe = b.write("bin/exe%d" % i, b.image(deps=[(cmd, name)],
                                                   filetype=MH_EXECUTE))
            cl, exc = _closure(mod, exe)
            if isinstance(exc, mod.MachOError):
                messages.append(str(exc))
                if not str(exc).strip():
                    problems.append("%s: MachOError with an empty message"
                                    % label)
            elif exc is not None:
                problems.append("%s: raised %s, want MachOError"
                                % (label, type(exc).__name__))
            else:
                problems.append("%s: closure %r, want MachOError"
                                % (label, sorted(_images(cl))[:4]))
        return problems, ["refusal     : %r" % (messages[0] if messages
                                                 else None,)]
    _guard(suite, GRP_B, "B14 nonweak-missing-refused", strong_missing)

    def cycle():
        mod = _macho_mod()
        b = _builder(fx, "b15")
        b.write("lib/liba.dylib", b.image(deps=["@loader_path/libb.dylib"]))
        b.write("lib/libb.dylib", b.image(deps=["@loader_path/liba.dylib",
                                                "@loader_path/libb.dylib"]))
        exe = b.write("bin/exe", b.image(
            deps=["@loader_path/../lib/liba.dylib"], filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "a <-> b cycle", problems)
        if cl is not None:
            imgs = _images(cl)
            for name in ("liba.dylib", "libb.dylib"):
                _want_in("cycle member", b.path("lib", name), imgs, problems)
            images = list(getattr(cl, "images", ()) or ())
            if len(images) != len(set(images)):
                problems.append("images repeat an entry: %r" % (images,))
        return problems, []
    _guard(suite, GRP_B, "B15 cycle-terminates", cycle)

    def system_paths():
        mod = _macho_mod()
        b = _builder(fx, "b16")
        b.write("lib/libl.dylib", b.image())
        exe = b.write("bin/exe", b.image(deps=[
            "/usr/lib/libSystem.B.dylib",
            "/usr/lib/libph-test-nosuch.dylib",
            "/System/Library/Frameworks/PhNoSuch.framework/PhNoSuch",
            "@loader_path/../lib/libl.dylib"], filetype=MH_EXECUTE))
        problems = []
        cl = _closure_ok(mod, exe, "closure with system dylibs", problems)
        if cl is not None:
            imgs = _images(cl)
            _want_in("local dylib", b.path("lib", "libl.dylib"), imgs,
                     problems)
            sys_hits = sorted(p for p in imgs | _spellings(cl)
                              if p.startswith(("/usr/lib/", "/System/")))
            if sys_hits:
                problems.append("system paths walked: %s" % sys_hits)
        return problems, []
    _guard(suite, GRP_B, "B16 system-paths-skipped", system_paths)

    def image_cap():
        mod = _macho_mod()
        b = _builder(fx, "b17")
        leaf = b.image()
        problems = []
        lines = []
        for count, want_ok in ((IMAGE_MAX - 100, True), (IMAGE_MAX + 88,
                                                          False)):
            sub = "n%d" % count
            for i in range(count):
                b.write("%s/lib/l%04d.dylib" % (sub, i), leaf)
            exe = b.write("%s/bin/exe" % sub, b.image(
                deps=["@loader_path/../lib/l%04d.dylib" % i
                      for i in range(count)], filetype=MH_EXECUTE))
            cl, exc = _closure(mod, exe)
            label = "%d dylibs" % count
            if want_ok:
                if exc is not None:
                    problems.append("%s control: raised %s (%s)"
                                    % (label, type(exc).__name__,
                                       str(exc)[:120]))
                elif len(_images(cl)) < count:
                    problems.append("%s control: %d images"
                                    % (label, len(_images(cl))))
            elif not isinstance(exc, mod.MachOError):
                problems.append("%s (> %d images): %s, want MachOError"
                                % (label, IMAGE_MAX,
                                   type(exc).__name__ if exc
                                   else "a closure"))
            else:
                lines.append("refusal     : %r" % str(exc)[:120])
        return problems, lines
    _guard(suite, GRP_B, "B17 image-cap-refused", image_cap)

    def live_python():
        mod = _macho_mod()
        if sys.platform != "darwin":
            raise _Info("live Homebrew python3 closure is a macOS row "
                        "(platform %s)" % sys.platform)
        spelling = _which("python3")
        if not spelling:
            raise _Info("no python3 on PATH")
        keg = mod._homebrew_keg(os.path.realpath(spelling))
        if not keg:
            raise _Info("python3 %s is not inside a Homebrew keg" % spelling)
        problems = []
        hops, real = mod._symlink_hops(spelling)
        hops = tuple(hops)
        if real != os.path.realpath(spelling):
            problems.append("_symlink_hops real %r, want %r"
                            % (real, os.path.realpath(spelling)))
        launcher = mod._framework_launcher(real)
        if not launcher or not launcher[0] or not launcher[1]:
            return problems + ["_framework_launcher(%s) %r, want "
                               "(Python.app exec, Versions/<v>/Python)"
                               % (real, launcher)], []
        app, main = launcher[0], launcher[1]
        execs = [real, app]
        roots = [spelling, main]
        kegs, images, reads = set(), set(), set(hops)
        app_hops, _ = mod._symlink_hops(app)
        reads.update(app_hops)
        for root in roots:
            cl = _closure_ok(mod, root, "closure(%s)" % root, problems)
            if cl is not None:
                kegs |= _kegs(cl)
                images |= _images(cl)
                reads |= _spellings(cl)
        if not app.endswith("/Resources/Python.app/Contents/MacOS/Python"):
            problems.append("launcher exec %s is not Python.app's" % app)
        if not app.startswith(keg + "/"):
            problems.append("launcher exec %s outside the python keg %s"
                            % (app, keg))
        _want_in("python keg", keg, kegs, problems)
        ssl = sorted(k for k in kegs if "/Cellar/openssl@3/" in k + "/")
        if not ssl:
            problems.append("no openssl@3 keg in the closure (kegs %s)"
                            % sorted(kegs))
        _want_in("framework main image", main, images, problems)
        for path in execs:
            if os.path.realpath(path) != path:
                problems.append("exec %s is not a realpath" % path)
        for path in sorted(images):
            if os.path.realpath(path) != path:
                problems.append("image %s is not a realpath" % path)
        for spelled in [spelling] + sorted(reads):
            for hop in _symlinked_prefixes(spelled):
                if hop not in reads:
                    problems.append("symlink hop %s (of %s) not in reads"
                                    % (hop, spelled))
                if hop in execs:
                    problems.append("symlink hop %s in execs" % hop)
        return problems, ["python keg  : %s" % keg,
                          "openssl keg : %s" % (ssl[0] if ssl else None),
                          "launcher    : %s" % app,
                          "images      : %d, kegs %d, reads %d"
                          % (len(images), len(kegs), len(reads))]
    _guard(suite, GRP_B, "B18 live-python-closure-has-openssl-keg",
           live_python)

    def framework_launcher():
        mod = _macho_mod()
        b = _builder(fx, "b19")
        ver = "X.framework/Versions/9.9"
        main = b.write(ver + "/Python", b.image())
        app = b.write(ver + "/Resources/Python.app/Contents/MacOS/Python",
                      b.image(deps=["@executable_path/../../../../Python"],
                              filetype=MH_EXECUTE))
        launcher_real = b.write(ver + "/bin/python9.9", b.image(
            deps=["@loader_path/../Python"], filetype=MH_EXECUTE))
        b.link("X.framework/Versions/Current", "9.9")
        spelling = b.link("bin/python9",
                          "../X.framework/Versions/Current/bin/python9.9")
        problems = []
        hops, real = mod._symlink_hops(spelling)
        hops = tuple(hops)
        if real != launcher_real:
            problems.append("_symlink_hops real %r, want %r"
                            % (real, launcher_real))
        _want_in("the spelling is a hop", spelling, hops, problems)
        _want_in("the symlinked ANCESTOR dir is a hop",
                 b.path("X.framework", "Versions", "Current"), hops, problems)
        got = mod._framework_launcher(real)
        if not got or tuple(got[:2]) != (app, main):
            problems.append("_framework_launcher %r, want (%s, %s)"
                            % (got, app, main))
        execs = [real] + ([got[0]] if got and got[0] else [])
        for path in execs:
            if os.path.realpath(path) != path:
                problems.append("exec %s is not a realpath" % path)
        for hop in hops:
            if os.path.islink(hop) and hop in execs:
                problems.append("symlink hop %s in execs" % hop)
        cl = _closure_ok(mod, main, "closure from Versions/9.9/Python",
                         problems)
        if cl is not None:
            _want_in("main image is a closure root", main, _images(cl),
                     problems)
        # Same names, not Mach-O: NOT added (recognised by magic only).
        yver = "Y.framework/Versions/9.9"
        b.write(yver + "/Python", b"#!/bin/sh\necho not a mach-o\n")
        b.write(yver + "/Resources/Python.app/Contents/MacOS/Python",
                b"#!/bin/sh\nexec true\n")
        yreal = b.write(yver + "/bin/python9.9", b.image(filetype=MH_EXECUTE))
        ygot = mod._framework_launcher(yreal)
        for name in ("Python", "Resources/Python.app/Contents/MacOS/Python"):
            path = b.path(yver, name)
            if ygot and path in tuple(ygot):
                problems.append("non-Mach-O %s added by _framework_launcher"
                                % path)
        # Outside any *.framework: nothing to add.
        plain = b.write("plain/bin/python9.9", b.image(filetype=MH_EXECUTE))
        pgot = mod._framework_launcher(plain)
        if pgot and any(pgot):
            problems.append("_framework_launcher outside a framework %r"
                            % (pgot,))
        return problems, ["hops        : %s" % [os.path.relpath(h, b.base)
                                                for h in hops]]
    _guard(suite, GRP_B, "B19 framework-launcher-adds-python-app-and-main-image",
           framework_launcher)

    def keg_scan():
        mod = _macho_mod()
        b = _builder(fx, "b20")
        problems = []
        cap = getattr(mod, "_KEG_SCAN_MAX_FILES", None)
        if cap != KEG_SCAN_MAX_FILES:
            problems.append("_KEG_SCAN_MAX_FILES %r, want %d (M0:11)"
                            % (cap, KEG_SCAN_MAX_FILES))
        leaf = b.image()
        keg = b.path("Cellar", "fakepy", "9.9_1")
        if mod._homebrew_keg(b.path("Cellar", "fakepy", "9.9_1", "bin",
                                    "python9.9")) != keg:
            problems.append("_homebrew_keg did not find %s" % keg)
        want = [
            b.write("Cellar/fakepy/9.9_1/lib/libfoo.dylib", leaf),
            b.write("Cellar/fakepy/9.9_1/lib/libbar.1", leaf),
            b.write("Cellar/fakepy/9.9_1/lib/python9.9/lib-dynload/_ext",
                    leaf),
            b.write("Cellar/fakepy/9.9_1/Frameworks/P.framework/Versions/9.9/"
                    "P", leaf),
            b.write("Cellar/fakepy/9.9_1/lib/node_modules/a/b/addon.node",
                    leaf),
        ]
        skip = [
            b.write("Cellar/fakepy/9.9_1/lib/python9.9/test/evil.dylib",
                    leaf),
            b.write("Cellar/fakepy/9.9_1/lib/README", b"not an image\n",
                    mode=0o644),
            b.write("Cellar/fakepy/9.9_1/lib/python9.9/lib-dynload/x.py",
                    b"print(1)\n", mode=0o644),
            b.write("Cellar/fakepy/9.9_1/share/deep/libhidden.dylib", leaf),
            b.write("Cellar/fakepy/9.9_1/lib/node_modules/a/notnode.dylib",
                    leaf),
        ]
        exe = b.write("Cellar/fakepy/9.9_1/bin/python9.9", b.image(
            filetype=MH_EXECUTE))
        found = set(mod._keg_images(keg))
        for path in want:
            _want_in("targeted image", path, found, problems)
        for path in skip:
            _want_out("not scanned", path, found, problems)
        cl = _closure_ok(mod, exe, "closure from the keg's exe", problems)
        if cl is not None:
            _want_in("keg recorded", keg, _kegs(cl), problems)
            imgs = _images(cl)
            _want_in("lib-dynload image is a closure root",
                     b.path("Cellar", "fakepy", "9.9_1", "lib", "python9.9",
                            "lib-dynload", "_ext"), imgs, problems)
            _want_out("test/ image not walked", skip[0], imgs, problems)
        # The cap: a targeted dir AT the cap scans, one over it refuses.
        limit = cap if isinstance(cap, int) and 0 < cap < 4096 \
            else KEG_SCAN_MAX_FILES
        for count, label in ((limit, "at"), (limit + 1, "over")):
            ckeg = b.path("Cellar", "fake%s" % label, "1.0")
            for i in range(count):
                b.write("Cellar/fake%s/1.0/lib/python9.9/lib-dynload/m%04d"
                        % (label, i), leaf)
            if label == "at":
                try:
                    n = len(mod._keg_images(ckeg))
                    if n != count:
                        problems.append("%d files at the cap: %d images"
                                        % (count, n))
                except Exception as exc:  # noqa: BLE001 -- the finding
                    problems.append("%d files at the cap: raised %s (%s)"
                                    % (count, type(exc).__name__,
                                       str(exc)[:120]))
            else:
                _macho_refused(mod, "%d files in lib-dynload (> cap %d)"
                               % (count, limit),
                               lambda k=ckeg: mod._keg_images(k), problems)
        return problems, ["targeted    : %d found" % len(found),
                          "cap         : %r" % (cap,)]
    _guard(suite, GRP_B, "B20 keg-scan-targeted-dirs-only-and-capped",
           keg_scan)

    # Validation round 1 (C1): every term of the closure walk is bounded --
    # the rpath stack, the node_modules walk's directories, depth and
    # per-directory entries -- and the walk honours the plan deadline and a
    # cancel. Written RED first: the stack grew without bound, the walk had
    # no directory / depth / entry cap and dylib_closure took no deadline.

    def rpath_cap():
        mod = _macho_mod()
        b = _builder(fx, "b21")
        problems = []
        lines = []
        cap = getattr(mod, "_RPATH_STACK_MAX", None)
        if cap != RPATH_STACK_MAX:
            problems.append("_RPATH_STACK_MAX %r, want %d"
                            % (cap, RPATH_STACK_MAX))
        limit = cap if isinstance(cap, int) and 0 < cap <= 1024 \
            else RPATH_STACK_MAX
        b.write("ok/lib/libq.dylib", b.image())
        # Control: 2 x the cap spellings of ONE directory dedupe to one entry.
        same = ["@loader_path/" + "./" * (k % 256) + "../lib" + "/." * (k // 256)
                for k in range(2 * limit)]
        exe = b.write("ok/bin/exe", b.image(deps=["@rpath/libq.dylib"],
                                            rpaths=same, filetype=MH_EXECUTE))
        cl = _closure_ok(mod, exe, "%d spellings of one rpath directory"
                         % len(same), problems)
        if cl is not None:
            _want_in("found through the deduped rpath",
                     b.path("ok", "lib", "libq.dylib"), _images(cl), problems)
        # One image declaring more distinct directories than the cap.
        distinct = [b.path("ok", "lib")] + [b.path("nowhere", "r%04d" % i)
                                             for i in range(limit + 1)]
        wide = b.write("wide/bin/exe", b.image(deps=[b.path("ok", "lib",
                                                            "libq.dylib")],
                                               rpaths=distinct,
                                               filetype=MH_EXECUTE))
        msg = _macho_refused(mod, "%d distinct rpaths in one image (> cap %d)"
                             % (len(distinct), limit),
                             lambda: mod.dylib_closure(wide), problems)
        if msg:
            lines.append("wide        : %r" % msg[-120:])
        # The stack ACCUMULATES down a load chain: three images each
        # declaring half the cap is 1.5 x the cap at the third.
        half = limit // 2 + 1
        for i, (lib, nxt) in enumerate((("liba", "libb"), ("libb", "libc"),
                                        ("libc", None))):
            rps = [b.path("chain", "nowhere%d" % i, "r%04d" % k)
                   for k in range(half)]
            deps = ["@loader_path/%s.dylib" % nxt] if nxt else []
            b.write("chain/lib/%s.dylib" % lib, b.image(deps=deps, rpaths=rps))
        chain = b.write("chain/bin/exe", b.image(
            deps=["@loader_path/../lib/liba.dylib"], filetype=MH_EXECUTE))
        msg = _macho_refused(mod, "3 chained images x %d rpaths (> cap %d)"
                             % (half, limit),
                             lambda: mod.dylib_closure(chain), problems)
        if msg:
            lines.append("chain       : %r" % msg[-120:])
        return problems, lines
    _guard(suite, GRP_B, "B21 rpath-stack-deduped-and-capped", rpath_cap)

    def keg_walk_caps():
        mod = _macho_mod()
        b = _builder(fx, "b22")
        problems = []
        lines = []
        caps = {}
        for const, want in (("_KEG_SCAN_MAX_DIRS", KEG_SCAN_MAX_DIRS),
                            ("_KEG_SCAN_MAX_DEPTH", KEG_SCAN_MAX_DEPTH),
                            ("_KEG_DIR_MAX_ENTRIES", KEG_DIR_MAX_ENTRIES)):
            got = getattr(mod, const, None)
            if got != want:
                problems.append("%s %r, want %d" % (const, got, want))
            caps[const] = got if isinstance(got, int) and 0 < got <= 8192 \
                else want
        leaf = b.image()

        def scan(keg, label, ok, want_image=None):
            if not ok:
                return _macho_refused(mod, label,
                                      lambda: mod._keg_images(keg), problems)
            try:
                found = mod._keg_images(keg)
            except Exception as exc:          # noqa: BLE001 -- the finding
                problems.append("%s: raised %s (%s), want a scan"
                                % (label, type(exc).__name__, str(exc)[:120]))
                return None
            if want_image is not None:
                _want_in(label, want_image, set(found), problems)
            return None

        dcap = caps["_KEG_SCAN_MAX_DEPTH"]
        for depth, ok in ((dcap, True), (dcap + 1, False)):
            rel = "Cellar/deep%d/1.0/lib/node_modules/%s/a.node" % (
                depth, "/".join(["d"] * depth))
            image = b.write(rel, leaf)
            msg = scan(b.path("Cellar", "deep%d" % depth, "1.0"),
                       "node_modules %d levels deep (cap %d)" % (depth, dcap),
                       ok, image)
            if msg:
                lines.append("depth       : %r" % msg[-100:])
        ncap = caps["_KEG_SCAN_MAX_DIRS"]
        for count, ok in ((ncap - 1, True), (ncap, False)):
            top = b.path("Cellar", "wide%d" % count, "1.0", "lib",
                         "node_modules")
            os.makedirs(top)
            for i in range(count):
                os.mkdir(os.path.join(top, "m%05d" % i))
            msg = scan(b.path("Cellar", "wide%d" % count, "1.0"),
                       "node_modules + %d directories (cap %d)"
                       % (count, ncap), ok)
            if msg:
                lines.append("dirs        : %r" % msg[-100:])
        ecap = caps["_KEG_DIR_MAX_ENTRIES"]
        for count, ok in ((ecap, True), (ecap + 1, False)):
            lib = b.path("Cellar", "ents%d" % count, "1.0", "lib")
            os.makedirs(lib)
            for i in range(count):
                os.symlink("nowhere", os.path.join(lib, "s%05d" % i))
            msg = scan(b.path("Cellar", "ents%d" % count, "1.0"),
                       "lib/ holding %d entries (cap %d)" % (count, ecap), ok)
            if msg:
                lines.append("entries     : %r" % msg[-100:])
        return problems, lines
    _guard(suite, GRP_B, "B22 keg-walk-dirs-depth-and-entries-capped",
           keg_walk_caps)

    def closure_deadline():
        mod = _macho_mod("MachOError", "dylib_closure", "_keg_images",
                         "plan_interpreter", "_RefreshDeadline",
                         "RequestCancelled", "_Reclaim", "_REQUEST_RECLAIM",
                         "InterpreterSpec")
        import contextvars
        b = _builder(fx, "b23")
        problems = []
        b.write("lib/liba.dylib", b.image())
        exe = b.write("bin/exe", b.image(
            deps=["@loader_path/../lib/liba.dylib"], filetype=MH_EXECUTE))
        keg = b.path("Cellar", "k", "1.0")
        b.write("Cellar/k/1.0/lib/libk.dylib", b.image())
        past = time.monotonic() - 1.0

        def expect(label, exc_type, fn):
            try:
                got = fn()
            except exc_type:
                return
            except Exception as exc:          # noqa: BLE001 -- the finding
                problems.append("%s: raised %s (%s), want %s"
                                % (label, type(exc).__name__, str(exc)[:120],
                                   exc_type.__name__))
                return
            problems.append("%s: returned %s, want %s"
                            % (label, repr(got)[:80], exc_type.__name__))

        expect("dylib_closure past its plan deadline", mod._RefreshDeadline,
               lambda: mod.dylib_closure(exe, plan_deadline=past))
        expect("_keg_images past its plan deadline", mod._RefreshDeadline,
               lambda: mod._keg_images(keg, plan_deadline=past))
        if sys.platform == "darwin":
            spec = mod.InterpreterSpec(name="bash", path=exe, extra_reads=())
            expect("plan_interpreter past its plan deadline",
                   mod._RefreshDeadline,
                   lambda: mod.plan_interpreter(spec, None, plan_deadline=past))
        reclaim = mod._Reclaim()
        reclaim.cancelled = True

        def cancelled():
            mod._REQUEST_RECLAIM.set(reclaim)
            return mod.dylib_closure(exe, plan_deadline=time.monotonic() + 60)
        expect("dylib_closure for a cancelled request", mod.RequestCancelled,
               lambda: contextvars.copy_context().run(cancelled))
        # Control: a live deadline walks as before.
        try:
            cl = mod.dylib_closure(exe, plan_deadline=time.monotonic() + 60)
            _want_in("control closure", b.path("lib", "liba.dylib"),
                     _images(cl), problems)
            if b.path("Cellar", "k", "1.0", "lib", "libk.dylib") not in set(
                    mod._keg_images(keg, plan_deadline=time.monotonic() + 60)):
                problems.append("control: _keg_images missed libk.dylib")
        except Exception as exc:              # noqa: BLE001 -- the finding
            problems.append("control: raised %s (%s)"
                            % (type(exc).__name__, str(exc)[:120]))
        return problems, []
    _guard(suite, GRP_B, "B23 closure-honours-plan-deadline-and-cancel",
           closure_deadline)


# ---------------------------------------------------------------------------
# C: run_script parameter validation (in-process, pure, Step 9)
# ---------------------------------------------------------------------------
# The contract these rows pin (plan Step 9; the refusal SHAPE is not spelled
# out there, so it is written down here):
#   validate_run_params(params, cfg, healthy) -> RunRequest on acceptance.
#   A refusal is EITHER a raised exception whose `code` attribute is
#   "invalid_params" (its str() the message) OR a returned _tool_refusal
#   dict {"error": {"code": "invalid_params", "message": ...}}. A bare
#   ValueError/TypeError without a `code` is NOT a refusal -- it is the
#   finding (it would reach layer D and answer `internal`).
#   The message names the member (`files`, `entry`, `args`, `stdin`,
#   `timeoutMs`) and, for a list element, its index (`files[2]`, `args[1]`),
#   and never echoes the offending value: every bad value below carries a
#   marker (`c1zq` ...) that must not appear in the message (P8).
#   Omitted timeoutMs -> limits.timeout_ms; an over-cap timeoutMs is clamped
#   to limits.max_timeout_ms (not refused); omitted args -> (); omitted stdin
#   -> None; RunRequest.stdin is the UTF-8 BYTES.
# Every row carries an accepted control beside its refusals, so a stub that
# refuses everything fails it as surely as one that accepts everything.

_RUN_MEMBERS = frozenset({"files", "entry", "interpreter", "args", "stdin",
                          "timeoutMs"})
_HEALTHY = frozenset({"python3"})
# Limits for group C, small enough to cross every cap with a few bytes.
_C_LIMITS = dict(timeout_ms=3000, max_timeout_ms=5000, max_files=4,
                 max_files_bytes=64, max_stdin_bytes=16, max_args=4,
                 max_arg_chars=32)
_MAIN = {"path": "main.py", "content": "print(1)\n"}     # 9 bytes
# Non-ASCII fixtures built with chr(), never typed, so no editor can
# re-normalise them: NFC e-acute (2 UTF-8 bytes), its NFD spellings, eszett.
_NFC_E = chr(0xE9)
_NFD_E = "e" + chr(0x301)
_NFD_CAP_E = "E" + chr(0x301)
_ESZETT = chr(0xDF)


def _val_mod():
    """The server module, held to the Step 9 name. Its absence is the stated
    RED reason, never an AttributeError."""
    mod = _mod()
    if not hasattr(mod, "validate_run_params"):
        raise _Red("Scripts/mcp-sandbox.py has no validate_run_params (Step "
                   "9 run_script validation not implemented)")
    return mod


_CCFG = {}


def _c_cfg(mod, fx):
    """One config for the whole group: python3 only, the _C_LIMITS caps."""
    if "cfg" not in _CCFG:
        _CCFG["cfg"] = mod.load_config(fx.render(
            "c-cfg", interpreters=_interpreters(full=False),
            mutate=_set_limits(**_C_LIMITS)))
    return _CCFG["cfg"]


def _params(files=None, **over):
    """A valid run_script params object: main.py first, entry main.py."""
    out = {"files": [dict(_MAIN)] + list(files or ()), "entry": "main.py",
           "interpreter": "python3"}
    out.update(over)
    return out


def _validate(mod, cfg, params):
    """(request, code, message, unexpected_exception) -- exactly one of
    request / code / unexpected_exception is set."""
    try:
        got = mod.validate_run_params(params, cfg, _HEALTHY)
    except Exception as exc:              # noqa: BLE001 -- classified below
        code = getattr(exc, "code", None)
        if isinstance(code, str):
            return None, code, str(exc), None
        return None, None, None, exc
    if isinstance(got, dict) and isinstance(got.get("error"), dict):
        err = got["error"]
        return None, err.get("code"), str(err.get("message", "")), None
    return got, None, None, None


def _v_refused(mod, cfg, params, label, problems, names=(), any_of=(),
               secrets=()):
    """`params` must be refused invalid_params; the message must contain
    every one of `names`, at least one of `any_of`, none of `secrets`."""
    req, code, msg, exc = _validate(mod, cfg, params)
    if exc is not None:
        problems.append("%s: raised %s (%s), want an invalid_params refusal"
                        % (label, type(exc).__name__, str(exc)[:120]))
        return None
    if code is None:
        problems.append("%s: accepted (%s), want invalid_params"
                        % (label, repr(req)[:160]))
        return None
    if code != "invalid_params":
        problems.append("%s: code %r (%r), want invalid_params"
                        % (label, code, msg))
        return msg
    for needle in names:
        if needle not in msg:
            problems.append("%s: message %r does not name %r"
                            % (label, msg, needle))
    if any_of and not any(needle in msg for needle in any_of):
        problems.append("%s: message %r names none of %r"
                        % (label, msg, list(any_of)))
    for value in secrets:
        if value in msg:
            problems.append("%s: message %r echoes the value %r"
                            % (label, msg, value))
    return msg


def _v_accepted(mod, cfg, params, label, problems):
    """`params` must be accepted as a RunRequest; returns it or None."""
    req, code, msg, exc = _validate(mod, cfg, params)
    if exc is not None:
        problems.append("%s: raised %s (%s), want accepted"
                        % (label, type(exc).__name__, str(exc)[:120]))
        return None
    if code is not None:
        problems.append("%s: refused %s (%r), want accepted"
                        % (label, code, msg))
        return None
    if not isinstance(req, mod.RunRequest):
        problems.append("%s: returned %s, want a RunRequest"
                        % (label, type(req).__name__))
        return None
    return req


def _paths(req):
    return [f[0] for f in (getattr(req, "files", None) or ())]


def _path_row(suite, fx, name, marker, bad, good):
    """A _valid_relpath row: each `bad` path at files[1] (main.py at files[0],
    entry main.py) is refused naming files[1] without echoing `marker`; each
    `good` path at files[1] is accepted and lands in RunRequest.files."""
    def body():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        msg = None
        for path in bad:
            got = _v_refused(mod, cfg, _params([{"path": path,
                                                 "content": "x"}]),
                             "path %r" % path, problems, names=("files[1]",),
                             secrets=(marker,))
            msg = msg or got
        for path in good:
            req = _v_accepted(mod, cfg, _params([{"path": path,
                                                  "content": "x"}]),
                              "control path %r" % path, problems)
            if req is not None and _paths(req) != ["main.py", path]:
                problems.append("control path %r: files %r" % (path,
                                                                _paths(req)))
        return problems, ["refused     : %d paths, %d controls"
                          % (len(bad), len(good)),
                          "refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, name, body)


def _run_c(suite, fx):
    _path_row(suite, fx, "C1 dotdot-refused", "c1zq",
              ("../c1zq.py", "..", "../../c1zq"),
              ("c1zq.py", "c1zq..py", "..c1zq.py"))
    _path_row(suite, fx, "C2 absolute-refused", "c2zq",
              ("/tmp/c2zq.py", "/c2zq", "//c2zq/x.py"),
              ("abs/c2zq.py",))
    _path_row(suite, fx, "C3 nested-dotdot-refused", "c3zq",
              ("a/../c3zq.py", "a/b/../../c3zq.py", "a/c3zq/.."),
              ("a/b/c3zq.py", "a/..c3zq/x.py"))
    _path_row(suite, fx, "C4 empty-component-refused", "c4zq",
              ("a//c4zq.py", "c4zq/", ""),
              ("a/c4zq.py",))
    _path_row(suite, fx, "C5 dot-component-refused", "c5zq",
              ("./c5zq.py", "a/./c5zq.py", ".", "c5zq/."),
              (".c5zq.py", "a/.c5zq/x.py"))
    _path_row(suite, fx, "C6 nul-refused", "c6zq",
              ("c6zq\x00.py", "a/\x00c6zq"),
              ("c6zq.py",))
    _path_row(suite, fx, "C7 backslash-refused", "c7zq",
              ("a\\c7zq.py", "c7zq\\..\\x.py"),
              ("a/c7zq.py",))
    _path_row(suite, fx, "C8 control-char-refused", "c8zq",
              ("c8zq\x01.py", "c8zq\x1f", "c8zq\x7f.py", "c8zq\x85.py",
               "c8zq\n.py", "a/c8zq\t.py"),
              ("c8zq " + _NFC_E + ".py",))
    _path_row(suite, fx, "C9 reserved-out-refused", "c9zq",
              ("out/c9zq.py", "out", "tmp/c9zq.py", ".sbx/c9zq",
               ".sbxc9zq/x.py", ".sbx-flat-0000001"),
              ("a/out/c9zq.py", "outer/c9zq.py", "tmpc9zq.py",
               "a/tmp/c9zq.py", ".sb/c9zq.py"))

    def entry_dash():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        dash = {"path": "-c10zq.py", "content": "x"}
        msg = _v_refused(mod, cfg, _params([dash], entry="-c10zq.py"),
                         "entry -c10zq.py", problems,
                         any_of=("files[1]", "entry"), secrets=("c10zq",))
        _v_refused(mod, cfg, _params([dash]), "files[1] -c10zq.py",
                   problems, names=("files[1]",), secrets=("c10zq",))
        _v_refused(mod, cfg, _params([{"path": "a/-c10zq.py",
                                       "content": "x"}]),
                   "files[1] a/-c10zq.py", problems, names=("files[1]",),
                   secrets=("c10zq",))
        for path in ("a-c10zq.py", "a/b-c10zq.py"):
            req = _v_accepted(mod, cfg, _params([{"path": path,
                                                  "content": "x"}],
                                                entry=path),
                              "control entry %r" % path, problems)
            if req is not None and getattr(req, "entry", None) != path:
                problems.append("control %r: entry %r"
                                % (path, getattr(req, "entry", None)))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C10 entry-leading-dash-refused", entry_dash)

    def collision(pairs, good):
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        msg = None
        for first, second in pairs:
            files = [{"path": first, "content": "x"},
                     {"path": second, "content": "y"}]
            got = _v_refused(mod, cfg, _params(files),
                             "%r then %r" % (first, second), problems,
                             names=("files[2]",), secrets=("zq",))
            msg = msg or got
        for first, second in good:
            files = [{"path": first, "content": "x"},
                     {"path": second, "content": "y"}]
            req = _v_accepted(mod, cfg, _params(files),
                              "control %r + %r" % (first, second), problems)
            if req is not None and _paths(req) != ["main.py", first, second]:
                problems.append("control %r + %r: files %r"
                                % (first, second, _paths(req)))
        return problems, ["refusal     : %r" % (msg,)]

    _guard(suite, GRP_C, "C11 case-collision-refused", lambda: collision(
        (("C11zq.py", "c11zq.py"), ("c11zq.py", "c11zq.py"),
         ("a/C11zq.py", "A/c11zq.py"),
         (_ESZETT + "c11zq.py", "SSc11zq.py")),
        (("c11zq.py", "c11zq2.py"),)))
    # NFC U+00E9 against NFD e + U+0301 (and the NFD capital), built with
    # chr() so an editor's normalisation cannot merge the fixtures.
    _guard(suite, GRP_C, "C12 nfd-nfc-collision-refused", lambda: collision(
        ((_NFC_E + "c12zq.py", _NFD_E + "c12zq.py"),
         (_NFD_E + "c12zq.py", _NFC_E + "c12zq.py"),
         (_NFC_E + "c12zq.py", _NFD_CAP_E + "c12zq.py")),
        ((_NFC_E + "c12zq.py", "ec12zq.py"),)))
    # Prefix by key: a file "c13zq" cannot be a directory of "c13zq/x.py"
    # in either order, nor under a case or NFD/NFC spelling; "c13zq.py" next
    # to "c13zq/x.py" is no prefix (a bare startswith would refuse it).
    _guard(suite, GRP_C, "C13 file-dir-prefix-collision-refused",
           lambda: collision(
               (("c13zq", "c13zq/x.py"), ("c13zq/x.py", "c13zq"),
                ("a/c13zq", "a/c13zq/b/x.py"), ("C13zq", "c13zq/x.py")),
               (("c13zq.py", "c13zq/x.py"), ("c13zq/a.py", "c13zq/b.py"))))

    def too_many():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        cap = _C_LIMITS["max_files"]
        extra = [{"path": "f%d.py" % i, "content": "x"} for i in range(cap)]
        msg = _v_refused(mod, cfg, _params(extra[:cap]),
                         "%d files (> max_files %d)" % (cap + 1, cap),
                         problems, names=("files",))
        _v_refused(mod, cfg, {"files": [], "entry": "main.py",
                              "interpreter": "python3"},
                   "files []", problems, names=("files",))
        _v_refused(mod, cfg, {"files": "main.py", "entry": "main.py",
                              "interpreter": "python3"},
                   "files a string", problems, names=("files",))
        req = _v_accepted(mod, cfg, _params(extra[:cap - 1]),
                          "%d files (= max_files) control" % cap, problems)
        if req is not None and len(_paths(req)) != cap:
            problems.append("control: %d files in the request"
                            % len(_paths(req)))
        return problems, ["max_files   : %d" % cap,
                          "refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C14 too-many-files-refused", too_many)

    def decoded_total():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        cap = _C_LIMITS["max_files_bytes"]
        # main.py 9 + 40 + base64 of 20 = 69 > 64 at files[2]; files[3] is
        # NOT base64 -- refusing it instead would mean it was decoded first.
        over = [{"path": "a.txt", "content": "c15zq" + "a" * 35},
                {"path": "b.bin", "encoding": "base64",
                 "content": base64.b64encode(b"c15zq" + b"b" * 15).decode()},
                {"path": "c.bin", "encoding": "base64",
                 "content": "!!c15zq!!"}]
        msg = _v_refused(mod, cfg, _params(over), "running total at files[2]",
                         problems, names=("files[2]",),
                         secrets=("c15zq", "files[3]"))
        # The cap counts DECODED bytes, inclusive: 9 + 55 = 64 exactly, while
        # the base64 text alone (76 chars) is over it.
        blob = b"\xff" * 55
        edge = [{"path": "d.bin", "encoding": "base64",
                 "content": base64.b64encode(blob).decode()}]
        req = _v_accepted(mod, cfg, _params(edge),
                          "9 + 55 decoded bytes (= %d) control" % cap,
                          problems)
        if req is not None:
            datas = [f[1] for f in req.files]
            if datas[1:] != [blob]:
                problems.append("control: decoded data %r" % (datas[1:],))
        # UTF-8 content counts its encoded bytes: 9 + 28 x 2 = 65 > 64.
        _v_refused(mod, cfg, _params([{"path": "e.txt",
                                       "content": _NFC_E * 28}]),
                   "UTF-8 bytes over the cap (28 chars, 56 bytes)", problems,
                   names=("files[1]",))
        return problems, ["max_files_bytes: %d" % cap,
                          "refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C15 decoded-total-refused-before-next-decode",
           decoded_total)

    def bad_base64():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        msg = None
        for content in ("c16zq!!!", "YWJj\nc16zq", "YWJjZA", "YW Jj"):
            got = _v_refused(mod, cfg, _params([{"path": "x.bin",
                                                 "encoding": "base64",
                                                 "content": content}]),
                             "base64 %r" % content, problems,
                             names=("files[1]",), secrets=("c16zq",))
            msg = msg or got
        _v_refused(mod, cfg, _params([{"path": "x.bin", "encoding": "hex",
                                       "content": "616263"}]),
                   "encoding hex", problems, names=("files[1]",))
        req = _v_accepted(mod, cfg, _params([{"path": "x.bin",
                                              "encoding": "base64",
                                              "content": "YWJj",
                                              "executable": True}]),
                          "base64 YWJj control", problems)
        if req is not None and tuple(req.files[1]) != ("x.bin", b"abc", True):
            problems.append("control: files[1] %r, want ('x.bin', b'abc', "
                            "True)" % (tuple(req.files[1]),))
        req = _v_accepted(mod, cfg, _params([{"path": "y.txt",
                                              "encoding": "utf8",
                                              "content": "YWJj"}]),
                          "utf8 YWJj control", problems)
        if req is not None and tuple(req.files[1]) != ("y.txt", b"YWJj",
                                                       False):
            problems.append("utf8 control: files[1] %r"
                            % (tuple(req.files[1]),))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C16 bad-base64-refused", bad_base64)

    def timeout_bool():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        msg = None
        for value in (True, False, "1000", 1000.0, 0, -5, None):
            got = _v_refused(mod, cfg, _params(timeoutMs=value),
                             "timeoutMs %r" % (value,), problems,
                             names=("timeoutMs",))
            msg = msg or got
        for value in (1, 1000):
            req = _v_accepted(mod, cfg, _params(timeoutMs=value),
                              "timeoutMs %d control" % value, problems)
            if req is not None and req.timeout_ms != value:
                problems.append("timeoutMs %d control: timeout_ms %r"
                                % (value, req.timeout_ms))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C17 timeout-bool-refused", timeout_bool)

    def timeout_clamped():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        cap = _C_LIMITS["max_timeout_ms"]
        cases = ((cap + 1, cap), (999999, cap), (10 ** 30, cap), (cap, cap),
                 (cap - 1, cap - 1))
        for value, want in cases:
            req = _v_accepted(mod, cfg, _params(timeoutMs=value),
                              "timeoutMs %d" % value, problems)
            if req is not None and req.timeout_ms != want:
                problems.append("timeoutMs %d: timeout_ms %r, want %d"
                                % (value, req.timeout_ms, want))
        req = _v_accepted(mod, cfg, _params(), "timeoutMs omitted", problems)
        if req is not None:
            want = (("main.py", b"print(1)\n", False),)
            got = tuple(tuple(f) for f in req.files)
            if got != want:
                problems.append("base: files %r, want %r" % (got, want))
            for attr, value in (("entry", "main.py"),
                                ("interpreter", "python3"), ("args", ()),
                                ("stdin", None),
                                ("timeout_ms", _C_LIMITS["timeout_ms"])):
                have = getattr(req, attr, None)
                if attr == "args" and have is not None:
                    have = tuple(have)
                if have != value:
                    problems.append("base: %s %r, want %r"
                                    % (attr, have, value))
        return problems, ["max_timeout_ms: %d, default %d"
                          % (cap, _C_LIMITS["timeout_ms"])]
    _guard(suite, GRP_C, "C18 timeout-clamped", timeout_clamped)

    def entry_missing():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        pkg = [{"path": "pkg/x.py", "content": "x"}]
        msg = _v_refused(mod, cfg, _params(entry="c19zq.py"),
                         "entry c19zq.py", problems, names=("entry",),
                         secrets=("c19zq",))
        _v_refused(mod, cfg, _params(pkg, entry="pkg"),
                   "entry names a directory", problems, names=("entry",))
        _v_refused(mod, cfg, _params(entry=7), "entry 7", problems,
                   names=("entry",))
        missing = _params()
        del missing["entry"]
        _v_refused(mod, cfg, missing, "entry omitted", problems,
                   names=("entry",))
        req = _v_accepted(mod, cfg, _params(pkg, entry="pkg/x.py"),
                          "entry pkg/x.py control", problems)
        if req is not None and req.entry != "pkg/x.py":
            problems.append("control: entry %r" % (req.entry,))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C19 entry-not-in-files-refused", entry_missing)

    def args_nul():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        n, width = _C_LIMITS["max_args"], _C_LIMITS["max_arg_chars"]
        msg = _v_refused(mod, cfg, _params(args=["ok", "c20zq\x00x"]),
                         "args[1] with a NUL", problems, names=("args[1]",),
                         secrets=("c20zq",))
        _v_refused(mod, cfg, _params(args=["ok", 1]), "args[1] an int",
                   problems, names=("args[1]",))
        _v_refused(mod, cfg, _params(args="c20zq"), "args a string",
                   problems, names=("args",), secrets=("c20zq",))
        _v_refused(mod, cfg, _params(args=["a"] * (n + 1)),
                   "%d args (> max_args %d)" % (n + 1, n), problems,
                   names=("args",))
        _v_refused(mod, cfg, _params(args=["x" * (width + 1)]),
                   "args[0] of %d chars" % (width + 1), problems,
                   names=("args[0]",))
        want = ("-x", "x" * width, _NFC_E, "")
        req = _v_accepted(mod, cfg, _params(args=list(want)),
                          "%d args control" % n, problems)
        if req is not None and tuple(req.args) != want:
            problems.append("control: args %r, want %r" % (req.args, want))
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C20 args-nul-refused", args_nul)

    def stdin_large():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        cap = _C_LIMITS["max_stdin_bytes"]
        # 5 + 6 x 2 = 17 bytes in 11 characters: the cap is BYTES.
        msg = _v_refused(mod, cfg, _params(stdin="c21zq" + _NFC_E * 6),
                         "stdin of %d bytes" % (cap + 1), problems,
                         names=("stdin",), secrets=("c21zq",))
        _v_refused(mod, cfg, _params(stdin=7), "stdin an int", problems,
                   names=("stdin",))
        _v_refused(mod, cfg, _params(stdin="\ud800"),
                   "stdin a lone surrogate", problems, names=("stdin",))
        text = _NFC_E * (cap // 2)
        req = _v_accepted(mod, cfg, _params(stdin=text),
                          "stdin of %d bytes control" % cap, problems)
        if req is not None and req.stdin != text.encode("utf-8"):
            problems.append("control: stdin %r, want the UTF-8 bytes"
                            % (req.stdin,))
        return problems, ["max_stdin_bytes: %d" % cap,
                          "refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C21 stdin-too-large-refused", stdin_large)

    def unknown_member():
        mod = _val_mod()
        cfg = _c_cfg(mod, fx)
        problems = []
        params = _params(entri="main.py")
        want = mod._unknown_params_refusal("run_script", params,
                                           _RUN_MEMBERS)
        msg = _v_refused(mod, cfg, params, "member entri", problems,
                         names=("entri", "Did you mean 'entry'"))
        if msg is not None and want and want not in msg:
            problems.append("message %r does not carry the fleet sentence "
                            "%r (_unknown_params_refusal)" % (msg, want))
        _v_refused(mod, cfg, _params(Files=[]), "member Files", problems,
                   names=("Files", "Did you mean 'files'"))
        _v_refused(mod, cfg, _params(bogus_c22="c22secret"),
                   "member bogus_c22", problems, names=("bogus_c22",),
                   secrets=("c22secret",))
        _v_refused(mod, cfg, _params([{"path": "x.py", "content": "x",
                                       "encodng": "utf8"}]),
                   "files[1] member encodng", problems,
                   names=("files[1]", "encodng"))
        _v_accepted(mod, cfg, _params(args=[], stdin="", timeoutMs=100),
                    "every member present control", problems)
        return problems, ["refusal     : %r" % (msg,)]
    _guard(suite, GRP_C, "C22 unknown-member-did-you-mean", unknown_member)


# ---------------------------------------------------------------------------
# D: sbx argv golden (in-process, pure, Step 11)
# ---------------------------------------------------------------------------
# The contract these rows pin (plan Step 11; where the plan leaves a detail
# open it is PINNED here):
#   build_sbx_argv(plan, req, wd, status_fd, cfg) -> List[str], pure: plan an
#   InterpreterPlan, req a RunRequest, wd the absolute workdir, cfg a
#   SandboxConfig. The golden is the plan's order exactly:
#     [sys.executable, -I, -B, cfg.sbx, --restrict-reads, --clean-env,
#      --env PATH=..., --env HOME=<wd>, --env TMPDIR=<wd>/tmp,
#      --env LANG=C.UTF-8, --env OUT=<wd>/out, --write <wd>,
#      (--read r for r in plan.reads), (--exec e for e in plan.execs),
#      --timeout "%.3f" % (timeout_ms / 1000), --status-fd <fd>,
#      --rlimit-as <plan.as_limit>, --rlimit-cpu <ceil(timeout) + 1>,
#      --rlimit-nproc +<nproc_headroom>, --rlimit-fsize <fsize>,
#      --rlimit-nofile <nofile>, --rlimit-core 0, [--seccomp],
#      --, *plan.argv_prefix, <wd>/<entry>, *req.args]
#   PATH (pinned by RULE, the plan's "<interp dirs>" is not a literal): ends
#   with ":/usr/bin:/bin", carries dirname(plan.path), and every entry is one
#   of dirname(plan.path), dirname(plan.realpath), /usr/bin, /bin.
#   cfg.seccomp "on" -> --seccomp, "off" -> none; "auto" -> --seccomp iff the
#   host is Linux x86_64 (pinned: the builder may consult the platform for
#   "auto" only).
#   The runner's OWN environment to sbx is _spawn_sbx's Popen env, exactly
#   {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"} (D4).

_STEP11_NAMES = ("build_sbx_argv", "RunSlots", "run_one", "_read_capped",
                 "_read_status_line", "_cut_utf8")
_STEP12_NAMES = ("plan_interpreter", "measure_vsz", "self_test", "PlanCache",
                 "RunnerState", "capabilities", "LIVE_VERIFIED",
                 "_SELF_TEST_NAME")
_SCRIPT_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "OUT")
_RUNNER_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
_D_WD = "/private/tmp/ph-d-root/0123456789abcdef0123456789abcdef"
_D_STATUS_FD = 7


def _argv_mod(names=("build_sbx_argv",)):
    """The server held to the Step 11 builder; its absence is the stated RED
    reason, never an AttributeError."""
    mod = _mod()
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        raise _Red("Scripts/mcp-sandbox.py has no %s (Step 11 execution "
                   "engine not implemented)" % ", ".join(missing))
    return mod


def _d_cfg(mod, fx, seccomp):
    cfg = mod.load_config(fx.render("d-cfg",
                                    interpreters=_interpreters(full=False)))
    return cfg._replace(seccomp=seccomp)


def _d_plan(mod, **kw):
    base = dict(name="python3", path="/usr/bin/python3",
                realpath="/usr/bin/python3", version="Python 3.0",
                argv_prefix=("/usr/bin/python3", "-I", "-S", "-B"),
                reads=(), execs=("/usr/bin/python3", "/bin", "/usr/bin"),
                signature=(), vsz_baseline=0, as_limit=4 * 1024 * MIB)
    base.update(kw)
    return mod.InterpreterPlan(**base)


def _d_req(mod, interpreter, entry, args=(), timeout_ms=2500):
    return mod.RunRequest(files=((entry, b"x\n", False),), entry=entry,
                          interpreter=interpreter, args=tuple(args),
                          stdin=None, timeout_ms=timeout_ms)


def _d_golden(cfg, plan, req, wd, status_fd, seccomp_on):
    """The plan's argv with the PATH value left as a marker (checked by rule
    in _d_path)."""
    timeout = req.timeout_ms / 1000.0
    lim = cfg.limits
    argv = [sys.executable, "-I", "-B", cfg.sbx, "--restrict-reads",
            "--clean-env", "--env", "<PATH>", "--env", "HOME=" + wd,
            "--env", "TMPDIR=" + wd + "/tmp", "--env", "LANG=C.UTF-8",
            "--env", "OUT=" + wd + "/out", "--write", wd]
    for r in plan.reads:
        argv += ["--read", r]
    for e in plan.execs:
        argv += ["--exec", e]
    argv += ["--timeout", "%.3f" % timeout, "--status-fd", str(status_fd),
             "--rlimit-as", str(plan.as_limit),
             "--rlimit-cpu", str(int(math.ceil(timeout)) + 1),
             "--rlimit-nproc", "+%d" % lim.nproc_headroom,
             "--rlimit-fsize", str(lim.fsize),
             "--rlimit-nofile", str(lim.nofile), "--rlimit-core", "0"]
    if seccomp_on:
        argv.append("--seccomp")
    argv += ["--"] + list(plan.argv_prefix) + [wd + "/" + req.entry]
    argv += list(req.args)
    return argv


def _d_path(argv, plan, label, problems):
    """Masks the PATH= entry in `argv` and checks it by the pinned rule."""
    out = list(argv)
    paths = [i for i, tok in enumerate(out) if isinstance(tok, str)
             and tok.startswith("PATH=") and i > 0 and out[i - 1] == "--env"]
    if len(paths) != 1:
        problems.append("%s: %d '--env PATH=' entries, want 1"
                        % (label, len(paths)))
        return out
    value = out[paths[0]][len("PATH="):]
    out[paths[0]] = "<PATH>"
    parts = value.split(":")
    allowed = {os.path.dirname(plan.path), os.path.dirname(plan.realpath),
               "/usr/bin", "/bin"}
    if parts[-2:] != ["/usr/bin", "/bin"]:
        problems.append("%s: PATH %r does not end with /usr/bin:/bin"
                        % (label, value))
    if os.path.dirname(plan.path) not in parts:
        problems.append("%s: PATH %r lacks the interpreter dir %s"
                        % (label, value, os.path.dirname(plan.path)))
    stray = [p for p in parts if p not in allowed]
    if stray:
        problems.append("%s: PATH %r carries %s (not an interpreter dir)"
                        % (label, value, stray))
    return out


def _d_compare(got, want, label, problems):
    if not isinstance(got, list) or not all(isinstance(t, str) for t in got):
        problems.append("%s: build_sbx_argv returned %r, want a list of str"
                        % (label, type(got).__name__))
        return
    if got == want:
        return
    for i, (g, w) in enumerate(zip(got, want)):
        if g != w:
            problems.append("%s: argv[%d] %r, want %r (first difference; "
                            "got %d elements, want %d)"
                            % (label, i, g, w, len(got), len(want)))
            return
    problems.append("%s: argv has %d elements, want %d (tail %r / %r)"
                    % (label, len(got), len(want), got[len(want):][:4],
                       want[len(got):][:4]))


def _host_seccomp_auto():
    return sys.platform.startswith("linux") and \
        platform.machine().lower() in ("x86_64", "amd64")


def _run_d(suite, fx):
    def darwin_golden():
        mod = _argv_mod()
        problems = []
        keg = "/usr/local/Cellar/python@3.14/3.14.0"
        ver = keg + "/Frameworks/Python.framework/Versions/3.14"
        real = ver + "/bin/python3.14"
        app = ver + "/Resources/Python.app/Contents/MacOS/Python"
        plan = _d_plan(
            mod, path="/usr/local/bin/python3", realpath=real,
            version="Python 3.14.0",
            argv_prefix=("/usr/local/bin/python3", "-I", "-S", "-B"),
            reads=("/usr/local/bin/python3", "/usr/local/opt/python@3.14",
                   ver.replace("/Versions/3.14", "/Versions/Current"), keg,
                   "/usr/local/Cellar/openssl@3/3.4.0",
                   "/private/var/select/sh"),
            execs=(real, app, "/bin", "/usr/bin"),
            as_limit=34 * 1024 * MIB + 1234567)
        cfg = _d_cfg(mod, fx, "off")
        req = _d_req(mod, "python3", "main.py", args=("--flag", "two words"),
                     timeout_ms=2500)
        got = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg)
        _d_compare(_d_path(got, plan, "darwin", problems),
                   _d_golden(cfg, plan, req, _D_WD, _D_STATUS_FD, False),
                   "darwin", problems)
        if "--seccomp" in (got or []):
            problems.append("darwin (seccomp off): --seccomp present")
        execs = [got[i + 1] for i, t in enumerate(got[:-1])
                 if t == "--exec"] if isinstance(got, list) else []
        if app not in execs:
            problems.append("Python.app's realpath is not an --exec literal")
        return problems, ["elements    : %d" % len(got or [])]
    _guard(suite, GRP_D, "D1 darwin-sbx-argv-golden", darwin_golden)

    def linux_golden():
        mod = _argv_mod()
        problems = []
        plan = _d_plan(
            mod, name="bash", path="/usr/bin/bash", realpath="/usr/bin/bash",
            version="GNU bash, version 5.2",
            argv_prefix=("/usr/bin/bash", "--noprofile", "--norc"),
            reads=("/etc/ph-d2/extra.conf",),
            execs=("/usr/bin/bash", "/bin", "/usr/bin"),
            as_limit=2 * 1024 * MIB)
        req = _d_req(mod, "bash", "run.sh", timeout_ms=3000)
        lines = []
        for mode, want_on in (("on", True), ("off", False),
                              ("auto", _host_seccomp_auto())):
            cfg = _d_cfg(mod, fx, mode)
            got = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg)
            label = "linux seccomp %s" % mode
            _d_compare(_d_path(got, plan, label, problems),
                       _d_golden(cfg, plan, req, _D_WD, _D_STATUS_FD,
                                 want_on), label, problems)
            lines.append("%-11s : --seccomp %s" % (
                "seccomp " + mode, "--seccomp" in (got or [])))
        return problems, lines
    _guard(suite, GRP_D, "D2 linux-sbx-argv-golden-seccomp", linux_golden)

    def entry_absolute():
        mod = _argv_mod()
        problems = []
        cfg = _d_cfg(mod, fx, "off")
        node = "/usr/local/bin/node"
        plan = _d_plan(mod, name="node", path=node,
                       realpath="/usr/local/Cellar/node/22.0.0/bin/node",
                       argv_prefix=(node,),
                       execs=("/usr/local/Cellar/node/22.0.0/bin/node",
                              "/bin", "/usr/bin"))
        args = ("-e", "--", "../d3-escape", "/etc/passwd", "")
        for entry in ("src/app/main.js", "main.js"):
            req = _d_req(mod, "node", entry, args=args, timeout_ms=100)
            got = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg)
            label = "entry %s" % entry
            if not isinstance(got, list) or "--" not in got:
                problems.append("%s: no '--' separator in %r"
                                % (label, got))
                continue
            sep = got.index("--")
            tail = got[sep + 1:]
            want = [node, os.path.join(_D_WD, entry)] + list(args)
            if tail != want:
                problems.append("%s: target argv %r, want %r"
                                % (label, tail, want))
            elif not os.path.isabs(tail[1]):
                problems.append("%s: entry %r is not absolute"
                                % (label, tail[1]))
            _d_compare(_d_path(got, plan, label, problems),
                       _d_golden(cfg, plan, req, _D_WD, _D_STATUS_FD, False),
                       label, problems)
            if "--timeout" in got and \
                    got[got.index("--timeout") + 1] != "0.100":
                problems.append("%s: --timeout %r, want '0.100'"
                                % (label, got[got.index("--timeout") + 1]))
        return problems, ["args        : %r" % (args,)]
    _guard(suite, GRP_D, "D3 entry-absolute", entry_absolute)

    def runner_env():
        mod = _argv_mod(("build_sbx_argv", "_spawn_sbx"))
        problems = []
        cfg = _d_cfg(mod, fx, "off")
        plan = _d_plan(mod)
        req = _d_req(mod, "python3", "main.py")
        argv = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg)
        calls = []

        class _FakeProc:
            pid = 999999
            returncode = None

        def fake_popen(args, **kwargs):
            calls.append((list(args), dict(kwargs)))
            return _FakeProc()

        secret_key = "PH_D4_SECRET"
        secret = "d4-secret-%d" % os.getpid()
        saved_env = os.environ.get(secret_key)
        saved = []
        os.environ[secret_key] = secret
        try:
            _patch(mod, saved, "subprocess", types.SimpleNamespace(
                Popen=fake_popen, PIPE=subprocess.PIPE,
                DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT))
            mod._spawn_sbx(argv, False, _D_STATUS_FD, _D_WD)
        finally:
            _restore(mod, saved)
            if saved_env is None:
                os.environ.pop(secret_key, None)
            else:
                os.environ[secret_key] = saved_env
        if len(calls) != 1:
            return ["_spawn_sbx made %d Popen calls, want 1" % len(calls)], []
        args, kwargs = calls[0]
        if args != argv:
            problems.append("_spawn_sbx changed the argv")
        env = kwargs.get("env")
        if env != _RUNNER_ENV:
            problems.append("Popen env %r, want exactly %r"
                            % (sorted(env) if isinstance(env, dict) else env,
                               _RUNNER_ENV))
        if secret in json.dumps(env, default=str) or \
                secret in json.dumps(argv):
            problems.append("the runner's own environment reached sbx")
        for key, want in (("start_new_session", True), ("close_fds", True),
                          ("pass_fds", (_D_STATUS_FD,)), ("cwd", _D_WD),
                          ("stdin", subprocess.DEVNULL)):
            if kwargs.get(key) != want:
                problems.append("Popen %s=%r, want %r"
                                % (key, kwargs.get(key), want))
        if "--clean-env" not in argv:
            problems.append("argv lacks --clean-env")
        keys = [argv[i + 1].partition("=")[0] for i, t in enumerate(argv[:-1])
                if t == "--env"]
        if sorted(keys) != sorted(_SCRIPT_ENV_KEYS):
            problems.append("--env keys %s, want %s"
                            % (keys, list(_SCRIPT_ENV_KEYS)))
        return problems, ["runner env  : %s" % sorted(env or {}),
                          "script env  : %s" % keys]
    _guard(suite, GRP_D, "D4 runner-env-to-sbx-is-two-keys", runner_env)

    def site_golden():
        """plan_interpreter from a LOADED config: python3 without `site` (and
        with site false) keeps (path, -I, -S, -B); site true drops -S only
        -- (path, -I, -B), -I kept -- and the run argv after `--` follows."""
        mod = _argv_mod(("build_sbx_argv", "plan_interpreter"))
        problems = []
        py = _interpreters(full=False)["python3"]
        path = py["path"]
        lines = []
        for site, flags in ((None, ("-I", "-S", "-B")),
                            (False, ("-I", "-S", "-B")),
                            (True, ("-I", "-B"))):
            entry = dict(py) if site is None else dict(py, site=site)
            label = "site %s" % ("absent" if site is None else site)
            cfg = mod.load_config(fx.render("d5", interpreters={
                "python3": entry}))._replace(seccomp="off")
            plan = mod.plan_interpreter(cfg.interpreters["python3"], cfg)
            want = (path,) + flags
            if tuple(plan.argv_prefix) != want:
                problems.append("%s: argv_prefix %r, want %r"
                                % (label, plan.argv_prefix, want))
            req = _d_req(mod, "python3", "main.py", args=("a1",))
            got = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg)
            golden = _d_golden(cfg, plan._replace(argv_prefix=want), req,
                               _D_WD, _D_STATUS_FD, False)
            _d_compare(_d_path(got, plan, label, problems), golden, label,
                       problems)
            if isinstance(got, list) and "--" in got:
                tail = got[got.index("--") + 1:]
                if tail != list(want) + [_D_WD + "/main.py", "a1"]:
                    problems.append("%s: target argv %r" % (label, tail))
            lines.append("%-12s: %s" % (label, " ".join(flags)))
        return problems, lines
    _guard(suite, GRP_D, "D5 python3-site-drops-only-S", site_golden)

    def egress_golden(kind, endpoint, url):
        """The run argv with an egress endpoint: the D1 golden plus the six
        proxy variables after OUT (both cases, NO_PROXY empty) and the
        endpoint flag after --rlimit-core 0; without one, D1 unchanged."""
        mod = _argv_mod()
        problems = []
        plan = _d_plan(mod)
        cfg = _d_cfg(mod, fx, "off")
        req = _d_req(mod, "python3", "main.py", args=("a",))
        base = _d_golden(cfg, plan, req, _D_WD, _D_STATUS_FD, False)
        try:
            got = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg,
                                     egress=endpoint)
        except TypeError as exc:
            return ["build_sbx_argv takes no egress endpoint (%s)" % exc], []
        flag = "--egress-port" if kind == "port" else "--egress-socket"
        env = []
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy",
                    "http_proxy"):
            env += ["--env", "%s=%s" % (key, url)]
        env += ["--env", "NO_PROXY=", "--env", "no_proxy="]
        at = base.index("OUT=" + _D_WD + "/out") + 1
        core = base.index("--rlimit-core") + 2
        want = (base[:at] + env + base[at:core] + [flag, str(endpoint[1])]
                + base[core:])
        _d_compare(_d_path(got, plan, kind, problems), want, kind, problems)
        none = mod.build_sbx_argv(plan, req, _D_WD, _D_STATUS_FD, cfg,
                                  egress=None)
        _d_compare(_d_path(none, plan, "no egress", problems), base,
                   "no egress", problems)
        return problems, ["endpoint    : %s %s" % (flag, endpoint[1])]

    _guard(suite, GRP_D, "D6 darwin-egress-port-argv-golden",
           lambda: egress_golden("port", ("port", 48123),
                                 "http://127.0.0.1:48123"))

    def linux_endpoint():
        """The socket endpoint's URL is sbx's fixed in-netns forwarder port,
        a hand copy in the runner pinned equal to sbx's own constant."""
        mod = _argv_mod()
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_sbx_gate as G
        helper = G._load_helper("sbx_for_mcp_sandbox_d7")
        want = getattr(helper, "EGRESS_FORWARD_PORT", None)
        got = getattr(mod, "_EGRESS_LINUX_PORT", None)
        problems = []
        if want is None or got != want:
            problems.append("runner _EGRESS_LINUX_PORT %r != sbx "
                            "EGRESS_FORWARD_PORT %r" % (got, want))
        more, lines = egress_golden(
            "socket", ("socket", "/w/0123456789abcdef0123456789abcdef/s"),
            "http://127.0.0.1:%s" % (got,))
        return problems + more, lines
    _guard(suite, GRP_D, "D7 linux-egress-socket-argv-golden", linux_endpoint)


# ---------------------------------------------------------------------------
# N: the egress proxy (in-process; stub resolver and connector, no network)
# ---------------------------------------------------------------------------

_EGRESS_NAMES = ("NetworkConfig", "EgressProxy", "EgressRefusal",
                 "parse_connect_head", "egress_host_refusal",
                 "egress_host_allowed", "egress_address_public")
N_PUBLIC = "93.184.215.14"
N_SECRET = b"tf-egress-SECRET-payload"


def _egress_mod():
    """The server held to the egress proxy's names; their absence is the
    stated RED reason, never an AttributeError."""
    mod = _mod()
    missing = [n for n in _EGRESS_NAMES if not hasattr(mod, n)]
    if missing:
        raise _Red("Scripts/mcp-sandbox.py has no %s (the egress proxy is "
                   "not implemented)" % ", ".join(missing))
    return mod


def _n_net(mod, allow=("allowed.example", "*.wild.example"), ports=(443,)):
    return mod.NetworkConfig(allow=tuple(allow), ports=tuple(ports))


class _NStub:
    """The proxy's resolver and connector, recorded. `answers` maps a host
    to a list of address strings or to an exception to raise. connect()
    hands the proxy one end of a socketpair and keeps the other (peers)."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.resolved, self.connected, self.peers = [], [], []
        self._lock = threading.Lock()

    def resolve(self, host, port):
        with self._lock:
            self.resolved.append((host, port))
        answer = self.answers.get(host, [N_PUBLIC])
        if isinstance(answer, BaseException):
            raise answer
        out = []
        for ip in answer:
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                            (ip, port, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "",
                            (ip, port)))
        return out

    def connect(self, family, sockaddr, timeout):
        a, b = socket.socketpair()
        with self._lock:
            self.connected.append(tuple(sockaddr[:2]))
            self.peers.append(b)
        return a

    def close(self):
        for peer in self.peers:
            try:
                peer.close()
            except OSError:
                pass


def _n_proxy(mod, stub, network=None, kind="tcp", **kw):
    proxy = mod.EgressProxy(network or _n_net(mod), kind,
                            resolver=stub.resolve, connector=stub.connect,
                            **kw)
    proxy.start()
    return proxy


def _n_client(proxy, head=b"", timeout=5.0):
    kind, where = proxy.endpoint
    if kind == "port":
        sock = socket.create_connection(("127.0.0.1", where), timeout=timeout)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(where)
    if head:
        sock.sendall(head)
    return sock


def _n_status(sock, timeout=5.0):
    """The proxy's status code (an int) read off `sock`, or None on EOF /
    timeout before a full status line."""
    sock.settimeout(timeout)
    buf = b""
    try:
        while b"\r\n" not in buf and len(buf) < 4096:
            chunk = sock.recv(1)
            if not chunk:
                break
            buf += chunk
    except OSError:
        pass
    parts = buf.split(b" ", 2)
    if len(parts) >= 2 and parts[0] in (b"HTTP/1.1", b"HTTP/1.0") \
            and parts[1].isdigit():
        # drain the rest of the head so a tunnel's first byte is the peer's
        try:
            while not buf.endswith(b"\r\n\r\n") and len(buf) < 8192:
                chunk = sock.recv(1)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        return int(parts[1])
    return None


def _n_connect(host, port=443, extra=b""):
    return (b"CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n" % (
        host.encode(), port, host.encode(), port)) + extra + b"\r\n"


def _n_eof(sock, timeout=3.0):
    """True when `sock` reads EOF (or is reset) within `timeout`."""
    sock.settimeout(timeout)
    try:
        while True:
            if not sock.recv(4096):
                return True
    except socket.timeout:
        return False
    except OSError:
        return True


def _n_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _run_n(suite, fx):
    def heads():
        mod = _egress_mod()
        problems = []
        for head, want in (
                (_n_connect("allowed.example"), ("allowed.example", 443)),
                (b"CONNECT Allowed.EXAMPLE:443 HTTP/1.1\r\n\r\n",
                 ("allowed.example", 443)),
                (b"CONNECT a.wild.example:8443 HTTP/1.0\r\nUser-Agent: x\r\n"
                 b"Proxy-Connection: keep-alive\r\n\r\n",
                 ("a.wild.example", 8443))):
            try:
                got = mod.parse_connect_head(head)
            except mod.EgressRefusal as exc:
                problems.append("control %r refused %r" % (head[:40], exc))
                continue
            if tuple(got) != want:
                problems.append("control %r: %r, want %r"
                                % (head[:40], got, want))
        refused = (
            (b"GET http://allowed.example/ HTTP/1.1\r\nHost: allowed.example"
             b"\r\n\r\n", 405),
            (b"POST / HTTP/1.1\r\n\r\n", 405),
            (b"connect allowed.example:443 HTTP/1.1\r\n\r\n", 405),
            (b"CONNECT allowed.example HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:44x HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:+443 HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:0 HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:99999 HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:443 HTTP/2\r\n\r\n", 400),
            (b"CONNECT  allowed.example:443 HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT user@allowed.example:443 HTTP/1.1\r\n\r\n", 400),
            (b"CONNECT allowed.example:443 HTTP/1.1\nHost: x\n\n", 400),
            (b"CONNECT allowed.example:443 HTTP/1.1\r\nHost x\r\n\r\n", 400),
            (b"CONNECT allowed.example:443 HTTP/1.1\r\n folded\r\n\r\n", 400),
            (b"CONNECT allowed.example:443 HTTP/1.1\r\nX: a\x00b\r\n\r\n",
             400),
            (b"CONNECT allowed.example:443 HTTP/1.1\r\n"
             + b"X-A: b\r\n" * 65 + b"\r\n", 431),
            (b"CONNECT allowed.example:443 HTTP/1.1\r\nX: " + b"a" * 9000
             + b"\r\n\r\n", 431),
        )
        for head, want in refused:
            try:
                got = mod.parse_connect_head(head)
            except mod.EgressRefusal as exc:
                if getattr(exc, "status", None) != want:
                    problems.append("%r: status %r, want %d"
                                    % (head[:48], getattr(exc, "status",
                                                          None), want))
                continue
            problems.append("%r: accepted %r, want %d" % (head[:48], got,
                                                         want))
        return problems, ["refused     : %d heads" % len(refused)]
    _guard(suite, GRP_N, "N1 connect-head-parsing", heads)

    def literals():
        mod = _egress_mod()
        problems = []
        for host in ("example.com", "a1.example.com", "xn--bcher-kva.example",
                     "123abc.example.com", "x.y-z.example"):
            why = mod.egress_host_refusal(host)
            if why is not None:
                problems.append("control %r refused: %r" % (host, why))
        for host in ("1.2.3.4", "127.0.0.1", "127.1", "0x7f000001",
                     "0X7F.0.0.1", "2130706433", "0177.0.0.1", "017700000001",
                     "[::1]", "::1", "[::ffff:127.0.0.1]", "::ffff:127.0.0.1",
                     "1.2.3.4.", "example.com.", "", "a..b", "-a.com",
                     "a_b.com", "foo.123", "a" * 64 + ".com"):
            why = mod.egress_host_refusal(host)
            if not isinstance(why, str) or not why:
                problems.append("%r: %r, want a refusal" % (host, why))
        return problems, []
    _guard(suite, GRP_N, "N2 ip-literal-and-bad-host-forms-refused", literals)

    def matching():
        mod = _egress_mod()
        problems = []
        pats = ("api.github.com", "*.example.org")
        for host, want in (("api.github.com", True), ("github.com", False),
                           ("x.api.github.com", False),
                           ("a.example.org", True), ("a.b.example.org", True),
                           ("example.org", False), ("badexample.org", False),
                           ("example.org.evil.com", False),
                           ("a.example.org.evil.com", False),
                           ("xexample.org", False)):
            got = mod.egress_host_allowed(host, pats)
            if got is not want:
                problems.append("%r against %r: %r, want %r"
                                % (host, pats, got, want))
        return problems, []
    _guard(suite, GRP_N, "N3 allowlist-exact-and-strict-subdomains", matching)

    def vetting():
        mod = _egress_mod()
        problems = []
        for addr in ("8.8.8.8", "1.1.1.1", N_PUBLIC,
                     "2606:4700:4700::1111"):
            if mod.egress_address_public(addr) is not True:
                problems.append("control %s judged not public" % addr)
        for addr in ("127.0.0.1", "127.0.0.53", "10.0.0.1", "172.16.0.1",
                     "192.168.1.1", "169.254.169.254", "100.64.0.1",
                     "0.0.0.0", "224.0.0.1", "239.255.255.250",
                     "255.255.255.255", "192.0.2.1", "198.18.0.1",
                     "240.0.0.1", "::", "::1", "fe80::1", "fc00::1",
                     "fd00:ec2::254", "fec0::1", "ff02::1",
                     "::ffff:127.0.0.1", "::ffff:8.8.8.8",
                     "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
                     "64:ff9b::7f00:1", "not-an-address"):
            if mod.egress_address_public(addr) is not False:
                problems.append("%s judged public" % addr)
        return problems, []
    _guard(suite, GRP_N, "N4 resolved-address-must-be-global", vetting)

    def tunnel():
        mod = _egress_mod()
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        try:
            kind, port = proxy.endpoint
            if kind != "port" or not isinstance(port, int):
                problems.append("endpoint %r, want ('port', N)"
                                % (proxy.endpoint,))
            c = _n_client(proxy, _n_connect("allowed.example"))
            status = _n_status(c)
            if status != 200:
                return problems + ["CONNECT allowed.example: %r, want 200"
                                   % (status,)], []
            c.sendall(b"tf-up")
            peer = stub.peers[0]
            peer.settimeout(3)
            if peer.recv(64) != b"tf-up":
                problems.append("client -> upstream bytes lost")
            peer.sendall(b"tf-down")
            if c.recv(64) != b"tf-down":
                problems.append("upstream -> client bytes lost")
            c.close()
            if not _n_eof(peer):
                problems.append("upstream saw no EOF after the client closed")
            if stub.resolved != [("allowed.example", 443)]:
                problems.append("resolved %r, want one lookup"
                                % (stub.resolved,))
            if stub.connected != [(N_PUBLIC, 443)]:
                problems.append("connected %r, want the vetted %s:443 (no "
                                "re-resolution)" % (stub.connected, N_PUBLIC))
            if not _n_until(lambda: proxy.summary().get("tunnels") == 1):
                problems.append("summary %r, want tunnels 1"
                                % (proxy.summary(),))
            if proxy.summary().get("refused") != 0:
                problems.append("summary %r, want refused 0"
                                % (proxy.summary(),))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        return problems, ["summary     : %r" % (proxy.summary(),)]
    _guard(suite, GRP_N, "N5 tunnel-splices-to-the-vetted-address", tunnel)

    def mixed():
        mod = _egress_mod()
        stub = _NStub({"allowed.example": [N_PUBLIC, "10.0.0.5"],
                       "b.wild.example": ["::ffff:127.0.0.1"],
                       "c.wild.example": ["169.254.169.254"],
                       "d.wild.example": OSError("nxdomain")})
        proxy = _n_proxy(mod, stub)
        problems = []
        try:
            for host, want in (("allowed.example", 403),
                               ("b.wild.example", 403),
                               ("c.wild.example", 403),
                               ("d.wild.example", 502)):
                c = _n_client(proxy, _n_connect(host))
                got = _n_status(c)
                c.close()
                if got != want:
                    problems.append("%s: %r, want %d" % (host, got, want))
            if stub.connected:
                problems.append("connected %r after a refused answer"
                                % (stub.connected,))
            if len(stub.resolved) != 4:
                problems.append("resolved %r, want once per host"
                                % (stub.resolved,))
            if not _n_until(lambda: proxy.summary().get("refused") == 4):
                problems.append("summary %r, want refused 4"
                                % (proxy.summary(),))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        return problems, []
    _guard(suite, GRP_N, "N6 any-non-global-answer-refuses", mixed)

    def before_dns():
        mod = _egress_mod()
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        try:
            for label, head, want in (
                    ("not allowlisted", _n_connect("other.example"), 403),
                    ("port not allowed", _n_connect("allowed.example", 8443),
                     403),
                    ("IP literal", _n_connect(N_PUBLIC), 403),
                    ("wildcard apex", _n_connect("wild.example"), 403),
                    ("absolute-form GET",
                     b"GET http://allowed.example/ HTTP/1.1\r\nHost: "
                     b"allowed.example\r\n\r\n", 405)):
                c = _n_client(proxy, head)
                got = _n_status(c)
                c.close()
                if got != want:
                    problems.append("%s: %r, want %d" % (label, got, want))
            if stub.resolved:
                problems.append("resolved %r before the policy refused"
                                % (stub.resolved,))
            c = _n_client(proxy, _n_connect("a.wild.example"))
            got = _n_status(c)
            c.close()
            if got != 200:
                problems.append("control a.wild.example: %r, want 200"
                                % (got,))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        return problems, []
    _guard(suite, GRP_N, "N7 policy-refuses-before-any-lookup", before_dns)

    def deadline():
        mod = _egress_mod()
        saved = []
        _patch(mod, saved, "_EGRESS_HEAD_DEADLINE_S", 0.6)
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        try:
            c = _n_client(proxy, b"CONNECT allowed")
            t0 = time.monotonic()
            got = _n_status(c, timeout=5)
            took = time.monotonic() - t0
            c.close()
            if got != 408 or took > 2.5:
                problems.append("stalled head: %r after %.2fs, want 408 "
                                "within ~0.6s" % (got, took))
            # a trickle that keeps every single read short must still be cut
            head = _n_connect("allowed.example")
            c = _n_client(proxy)
            t0 = time.monotonic()
            for i in range(len(head)):
                # stop as soon as the proxy answered: a send after its close
                # would draw a reset that can discard the answer
                if select.select([c], [], [], 0)[0]:
                    break
                try:
                    c.sendall(head[i:i + 1])
                except OSError:
                    break
                time.sleep(0.1)
                if time.monotonic() - t0 > 2.0:
                    break
            got = _n_status(c, timeout=3)
            took = time.monotonic() - t0
            c.close()
            if got != 408 or took > 2.6:
                problems.append("trickled head: %r after %.2fs, want 408 at "
                                "the total deadline" % (got, took))
            c = _n_client(proxy, _n_connect("allowed.example"))
            got = _n_status(c)
            c.close()
            if got != 200:
                problems.append("control prompt head: %r, want 200" % (got,))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
            _restore(mod, saved)
        return problems, []
    _guard(suite, GRP_N, "N8 head-total-deadline", deadline)

    def cap():
        mod = _egress_mod()
        saved = []
        _patch(mod, saved, "_EGRESS_MAX_TUNNELS", 2)
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        held = []
        try:
            for i in range(2):
                c = _n_client(proxy, _n_connect("allowed.example"))
                held.append(c)
                if _n_status(c) != 200:
                    problems.append("tunnel %d not established" % (i + 1))
            c = _n_client(proxy, _n_connect("allowed.example"))
            got = _n_status(c)
            c.close()
            if got != 503:
                problems.append("third concurrent tunnel: %r, want 503"
                                % (got,))
            # A tunnel ends when both sides have (N18: a half-close keeps
            # the other direction), so close the second tunnel's upstream too.
            held.pop().close()
            stub.peers[1].close()
            ok = _n_until(lambda: proxy.active() < 2)
            c = _n_client(proxy, _n_connect("allowed.example"))
            got = _n_status(c)
            held.append(c)
            if got != 200:
                problems.append("after one tunnel closed: %r, want 200 (the "
                                "cap is concurrent, not total; active "
                                "dropped=%s)" % (got, ok))
            if proxy.summary().get("refused", 0) < 1:
                problems.append("summary %r, want refused >= 1"
                                % (proxy.summary(),))
        finally:
            for c in held:
                c.close()
            proxy.close(time.monotonic() + 2.0)
            stub.close()
            _restore(mod, saved)
        return problems, ["summary     : %r" % (proxy.summary(),)]
    _guard(suite, GRP_N, "N9 concurrent-tunnel-cap", cap)

    def close_all():
        mod = _egress_mod()
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        c = _n_client(proxy, _n_connect("allowed.example"))
        try:
            if _n_status(c) != 200:
                return ["the tunnel was not established"], []
            _kind, port = proxy.endpoint
            t0 = time.monotonic()
            proxy.close(time.monotonic() + 1.0)
            took = time.monotonic() - t0
            if took > 1.5:
                problems.append("close took %.2fs, want <= the 1 s given"
                                % took)
            if not _n_eof(c, 2.0):
                problems.append("the client side of the tunnel is still open")
            if not _n_eof(stub.peers[0], 2.0):
                problems.append("the upstream side is still open")
            alive = [t.name for t in threading.enumerate()
                     if t.name.startswith("sandbox-egress")]
            if not _n_until(lambda: not [t for t in threading.enumerate()
                                         if t.name.startswith(
                                             "sandbox-egress")], 2.0):
                problems.append("proxy threads outlived close: %s" % alive)
            s = socket.socket()
            s.settimeout(1)
            try:
                s.connect(("127.0.0.1", port))
                problems.append("the endpoint still accepts after close")
            except OSError:
                pass
            finally:
                s.close()
        finally:
            c.close()
            stub.close()
        return problems, []
    _guard(suite, GRP_N, "N10 close-ends-every-tunnel-and-thread", close_all)

    def log_meta():
        mod = _egress_mod()

        class _Grab(logging.Handler):
            def __init__(self):
                super().__init__(logging.DEBUG)
                self.lines = []

            def emit(self, record):
                self.lines.append(record.getMessage())
        grab = _Grab()
        logger = logging.getLogger(LOGGER)
        level = logger.level
        logger.addHandler(grab)
        logger.setLevel(logging.DEBUG)
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        try:
            c = _n_client(proxy, _n_connect("allowed.example"))
            if _n_status(c) != 200:
                return ["the tunnel was not established"], []
            c.sendall(N_SECRET)
            stub.peers[0].settimeout(3)
            stub.peers[0].recv(64)
            stub.peers[0].sendall(N_SECRET[::-1])
            c.recv(64)
            c.close()
            c = _n_client(proxy, _n_connect("other.example"))
            _n_status(c)
            c.close()
            _n_until(lambda: proxy.summary().get("refused") == 1)
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
            logger.removeHandler(grab)
            logger.setLevel(level)
        text = "\n".join(grab.lines)
        if "allowed.example" not in text or "other.example" not in text:
            problems.append("the tunnel log lacks the host metadata: %r"
                            % (text[-300:],))
        for needle in (N_SECRET, N_SECRET[::-1]):
            if needle.decode() in text:
                problems.append("a payload reached the log")
        return problems, ["log lines   : %d" % len(grab.lines)]
    _guard(suite, GRP_N, "N11 log-is-metadata-only", log_meta)

    def unix_endpoint():
        mod = _egress_mod()
        sdir = os.path.join(fx.base, fx._name("n12"))
        os.mkdir(sdir, 0o700)
        stub = _NStub()
        problems = []
        try:
            proxy = _n_proxy(mod, stub, kind="unix", sock_dir=sdir)
        except OSError as exc:
            raise _Info("cannot bind a unix socket under %s here (%s)"
                        % (sdir, exc))
        try:
            kind, path = proxy.endpoint
            if kind != "socket" or path != os.path.join(sdir, "s"):
                problems.append("endpoint %r, want ('socket', <dir>/s)"
                                % (proxy.endpoint,))
            elif not stat.S_ISSOCK(os.lstat(path).st_mode):
                problems.append("%s is not a socket" % path)
            c = _n_client(proxy, _n_connect("allowed.example"))
            got = _n_status(c)
            c.close()
            if got != 200:
                problems.append("CONNECT over the unix endpoint: %r" % (got,))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        if os.path.lexists(os.path.join(sdir, "s")):
            problems.append("the socket outlived close")
        return problems, []
    _guard(suite, GRP_N, "N12 unix-endpoint-in-a-0700-dir", unix_endpoint)

    def run_egress_dir_under_work_root():
        """N13: on Linux a run's unix endpoint lives in a fresh 0700
        <work_root>/egress/<32-hex>/ -- never in tempfile's directory, which
        is /tmp, a directory sbx's egress_socket_refusal refuses. The
        platform and the EgressProxy are injected, so the row runs anywhere;
        close() removes the per-run directory and keeps egress/."""
        mod = _h_mod(_STEP10_NAMES + ("_RunEgress", "EgressProxy"))
        problems = []
        seen = {}

        class _SysAs:
            def __getattr__(self, name):
                return "linux" if name == "platform" else getattr(sys, name)

        class _FakeProxy:
            def __init__(self, network, kind, sock_dir=None, **_kw):
                seen.update(kind=kind, sock_dir=sock_dir)
                self.endpoint = None

            def start(self):
                self.endpoint = ("socket",
                                 os.path.join(seen["sock_dir"] or "", "s"))

            def close(self, _deadline):
                pass

            def summary(self):
                return {"tunnels": 0, "refused": 0}

        root = _Root(mod, fx.work_root("n13"))
        saved = []
        _patch(mod, saved, "sys", _SysAs())
        _patch(mod, saved, "EgressProxy", _FakeProxy)
        try:
            egress = mod._RunEgress(_n_net(mod), root.path)
            egress.start()
            sdir = seen.get("sock_dir")
            want_parent = os.path.join(os.path.normpath(root.path), "egress")
            if seen.get("kind") != "unix":
                problems.append("proxy kind %r, want 'unix'" % (seen.get("kind"),))
            if not isinstance(sdir, str) or os.path.dirname(sdir) != want_parent \
                    or not _HEX32.match(os.path.basename(sdir)):
                problems.append("socket dir %r, want %s/<32-hex>"
                                % (sdir, want_parent))
            else:
                if os.path.realpath(sdir) != sdir:
                    problems.append("socket dir %r is not its own realpath" % sdir)
                st = os.lstat(sdir)
                if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() \
                        or st.st_mode & 0o077:
                    problems.append("socket dir mode %o uid %d, want a 0700 "
                                    "directory of the euid"
                                    % (st.st_mode, st.st_uid))
                if egress.endpoint != ("socket", os.path.join(sdir, "s")):
                    problems.append("endpoint %r, want ('socket', <dir>/s)"
                                    % (egress.endpoint,))
            egress.close(time.monotonic() + 2.0)
            if isinstance(sdir, str) and os.path.lexists(sdir):
                problems.append("the per-run socket dir outlived close")
            if not os.path.isdir(root.egress):
                problems.append("egress/ itself was removed by close")
        finally:
            _restore(mod, saved)
            root.close()
            root.scrub()
        return problems, ["socket dir  : %s" % (seen.get("sock_dir"),)]
    _guard(suite, GRP_N, "N13 linux-socket-dir-under-work-root-egress",
           run_egress_dir_under_work_root)

    def unix_endpoint_long_path():
        """N14: a sock_dir whose <dir>/s exceeds the AF_UNIX sun_path limit
        (a deep work_root). On Linux start() binds through the short
        /proc/self/fd alias and the socket still lands at the real <dir>/s
        (what sbx --egress-socket checks); elsewhere it is refused by name
        (EgressSocketTooLong), never an opaque OSError."""
        mod = _egress_mod()
        problems = []
        top = os.path.join(fx.base, fx._name("n14"))
        os.mkdir(top, 0o700)
        sdir = os.path.join(top, "d" * max(1, 112 - len(top)))
        os.mkdir(sdir, 0o700)
        path = os.path.join(sdir, "s")
        notes = ["socket path : %d bytes" % len(os.fsencode(path))]
        stub = _NStub()
        try:
            if not sys.platform.startswith("linux"):
                try:
                    proxy = _n_proxy(mod, stub, kind="unix", sock_dir=sdir)
                except Exception as exc:  # the type is the assertion
                    if not (hasattr(mod, "EgressSocketTooLong")
                            and isinstance(exc, mod.EgressSocketTooLong)
                            and "AF_UNIX limit" in str(exc)):
                        problems.append("start() raised %s: %s, want "
                                        "EgressSocketTooLong naming the "
                                        "AF_UNIX limit"
                                        % (type(exc).__name__, exc))
                else:
                    proxy.close(time.monotonic() + 2.0)
                    problems.append("start() bound %r past the AF_UNIX limit"
                                    % (proxy.endpoint,))
                return problems, notes + ["platform    : %s (refusal path)"
                                          % sys.platform]
            try:
                proxy = _n_proxy(mod, stub, kind="unix", sock_dir=sdir)
            except (OSError, ValueError) as exc:
                return ["start() failed on a long sock_dir: %s: %s"
                        % (type(exc).__name__, exc)], notes
            try:
                if proxy.endpoint != ("socket", path):
                    problems.append("endpoint %r, want ('socket', <dir>/s)"
                                    % (proxy.endpoint,))
                elif not stat.S_ISSOCK(os.lstat(path).st_mode):
                    problems.append("the real <dir>/s is not a socket")
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.settimeout(5.0)
                dfd = os.open(sdir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    c.connect("/proc/self/fd/%d/s" % dfd)
                finally:
                    os.close(dfd)
                c.sendall(_n_connect("allowed.example"))
                got = _n_status(c)
                c.close()
                if got != 200:
                    problems.append("CONNECT over the long endpoint: %r" % (got,))
            finally:
                proxy.close(time.monotonic() + 2.0)
            if os.path.lexists(path):
                problems.append("the socket outlived close")
            return problems, notes
        finally:
            stub.close()
            for p in (path, sdir, top):
                try:
                    (os.rmdir if p != path else os.unlink)(p)
                except OSError:
                    pass
    _guard(suite, GRP_N, "N14 unix-endpoint-past-the-sun-path-limit",
           unix_endpoint_long_path)

    def dual_stack():
        """N15 (security F1): the tcp endpoint holds [::1]:N beside
        127.0.0.1:N. sbx's Seatbelt rule (remote tcp "localhost:N") admits
        both, so a free [::1]:N could be bound by another local uid and
        receive the script's un-allowlisted connection. The ::1 listener
        serves CONNECT like the v4 one and close() releases it. INFO on a
        host without an IPv6 loopback (nobody can bind ::1 there)."""
        mod = _egress_mod()
        probe = None
        try:
            probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(("::1", 0))
        except (OSError, AttributeError) as exc:
            raise _Info("no IPv6 loopback on this host (%s)" % exc)
        finally:
            if probe is not None:
                probe.close()
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        port = proxy.endpoint[1]
        try:
            squat = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            try:
                squat.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                squat.bind(("::1", port))
                problems.append("[::1]:%d was free for another listener" % port)
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    problems.append("binding [::1]:%d: %s, want EADDRINUSE"
                                    % (port, exc))
            finally:
                squat.close()
            c = None
            try:
                c = socket.create_connection(("::1", port), timeout=5.0)
                c.sendall(_n_connect("allowed.example"))
                got = _n_status(c)
                if got != 200:
                    problems.append("CONNECT over [::1]:%d: %r, want 200"
                                    % (port, got))
            except OSError as exc:
                problems.append("CONNECT over [::1]:%d: %s" % (port, exc))
            finally:
                if c is not None:
                    c.close()
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        after = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            after.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            after.bind(("::1", port))
        except OSError as exc:
            problems.append("[::1]:%d still held after close: %s" % (port, exc))
        finally:
            after.close()
        return problems, ["port        : %d" % port]
    _guard(suite, GRP_N, "N15 tcp-endpoint-holds-both-loopbacks", dual_stack)

    def absolute_lookup():
        """N16 (security F3): the default resolver looks the allowlisted
        name up as an absolute name ("host."), so resolv.conf search/ndots
        can never expand an NXDOMAIN allowlisted name into
        name.<search-domain>. The client's own trailing dot stays refused
        (N2); the dot is appended only for the lookup."""
        mod = _egress_mod()
        seen = []

        def getaddrinfo(host, port, *args, **kw):
            seen.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (N_PUBLIC, port))]

        fake = types.SimpleNamespace(getaddrinfo=getaddrinfo,
                                     SOCK_STREAM=socket.SOCK_STREAM,
                                     IPPROTO_TCP=socket.IPPROTO_TCP)
        saved = []
        _patch(mod, saved, "socket", fake)
        try:
            mod._default_resolver("allowed.example", 443)
        finally:
            _restore(mod, saved)
        problems = []
        if seen != ["allowed.example."]:
            problems.append("getaddrinfo saw %r, want ['allowed.example.']" % (seen,))
        try:
            host, _port = mod.parse_connect_head(_n_connect("allowed.example."))
            if mod.egress_host_refusal(host) is None:
                problems.append("a client's trailing dot was not refused")
        except mod.EgressRefusal:
            pass
        return problems, []
    _guard(suite, GRP_N, "N16 default-lookup-is-absolute", absolute_lookup)

    def stop_during_connect():
        """N17 (security F6): a connector that returns only after close()
        began yields no counted tunnel -- the late upstream is closed, the
        summary close() leaves behind is final -- and the default connector
        honours the stop event within a tick instead of the 10 s connect
        timeout."""
        mod = _egress_mod()
        stub = _NStub()
        entered, gate = threading.Event(), threading.Event()
        late = []

        def connector(family, sockaddr, timeout):
            entered.set()
            gate.wait(5.0)
            a, b = socket.socketpair()
            late.append(b)
            return a

        proxy = mod.EgressProxy(_n_net(mod), "tcp", resolver=stub.resolve,
                                connector=connector)
        proxy.start()
        problems = []
        c = _n_client(proxy, _n_connect("allowed.example"))
        try:
            if not entered.wait(5.0):
                return ["the connector was never reached"], []
            closer = threading.Thread(target=proxy.close,
                                      args=(time.monotonic() + 2.0,))
            closer.start()
            time.sleep(0.2)
            gate.set()
            closer.join(5.0)
            final = proxy.summary()
            if final.get("tunnels") != 0:
                problems.append("summary %r after close, want tunnels 0" % (final,))
            if late and not _n_eof(late[0], 2.0):
                problems.append("the late upstream socket was left open")
            time.sleep(0.3)
            if proxy.summary() != final:
                problems.append("summary moved after close: %r -> %r"
                                % (final, proxy.summary()))
        finally:
            gate.set()
            c.close()
            for s in late:
                s.close()
            stub.close()
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        t0 = time.monotonic()
        try:
            sock = mod._default_connector(socket.AF_INET, ("192.0.2.1", 9), 10.0, stop)
            sock.close()
            problems.append("a TEST-NET-1 connect succeeded")
        except TypeError as exc:
            problems.append("the default connector takes no stop event: %s" % exc)
        except OSError:
            pass
        took = time.monotonic() - t0
        if took > 2.0:
            problems.append("the default connector ignored stop for %.2fs" % took)
        return problems, ["default stop: %.2fs" % took]
    _guard(suite, GRP_N, "N17 stop-during-connect-counts-no-tunnel",
           stop_during_connect)

    def half_close():
        """N18 (security F13): a client that half-closes after its request
        still receives the upstream's whole response -- an EOF on one side
        is relayed as a shutdown(SHUT_WR) and the other direction keeps
        flowing until it too ends."""
        mod = _egress_mod()
        stub = _NStub()
        proxy = _n_proxy(mod, stub)
        problems = []
        body = b"tf-response-" * 4096
        c = _n_client(proxy, _n_connect("allowed.example"))
        try:
            if _n_status(c) != 200:
                return ["the tunnel was not established"], []
            c.sendall(b"tf-request")
            c.shutdown(socket.SHUT_WR)
            peer = stub.peers[0]
            peer.settimeout(3.0)
            got = b""
            try:
                while True:
                    chunk = peer.recv(4096)
                    if not chunk:
                        break
                    got += chunk
            except OSError as exc:
                problems.append("upstream read: %s" % exc)
            if got != b"tf-request":
                problems.append("upstream got %r, want the request then EOF" % got[:40])
            try:
                peer.sendall(body)
                peer.shutdown(socket.SHUT_WR)
            except OSError as exc:
                problems.append("upstream send after the client's EOF: %s" % exc)
            c.settimeout(3.0)
            down = b""
            try:
                while True:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    down += chunk
            except OSError as exc:
                problems.append("client read: %s" % exc)
            if down != body:
                problems.append("client got %d of %d response bytes"
                                % (len(down), len(body)))
        finally:
            c.close()
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        return problems, []
    _guard(suite, GRP_N, "N18 half-close-keeps-the-other-direction", half_close)

    def hex_letter_hosts():
        """Security round 2 (H): a CONNECT host whose last label is plain hex
        letters is a name; the number forms stay refused."""
        mod = _egress_mod()
        problems = []
        for host in ("example.be", "a.cafe", "x.ad", "y.bad", "z.dad"):
            why = mod.egress_host_refusal(host)
            if why is not None:
                problems.append("%r refused: %r" % (host, why))
        for host in ("1.2.3.4", "0x7f.1", "017", "a.0x1f", "a.123"):
            if not mod.egress_host_refusal(host):
                problems.append("%r accepted, want a refusal" % host)
        return problems, []
    _guard(suite, GRP_N, "N19 hex-letter-tld-hosts-accepted", hex_letter_hosts)

    def v4_denylist():
        """Security round 2 (F5a): the IANA not-globally-reachable IPv4
        ranges are refused even when the interpreter's tables call them
        global (pre-CVE-2024-4032 patch levels differ)."""
        mod = _egress_mod()
        problems = []
        cls = mod.ipaddress.IPv4Address
        names = ("is_global", "is_private", "is_reserved")
        saved = {n: cls.__dict__[n] for n in names if n in cls.__dict__}
        try:
            cls.is_global = property(lambda self: True)
            cls.is_private = property(lambda self: False)
            cls.is_reserved = property(lambda self: False)
            if mod.egress_address_public("8.8.8.8") is not True:
                problems.append("control 8.8.8.8 judged not public")
            for addr in ("0.1.2.3", "10.0.0.1", "100.64.0.1", "127.0.0.1",
                         "169.254.169.254", "172.16.0.1", "192.0.0.8",
                         "192.0.2.1", "192.88.99.1", "192.168.1.1",
                         "198.18.0.1", "198.51.100.1", "203.0.113.1",
                         "224.0.0.1", "240.0.0.1", "255.255.255.255"):
                if mod.egress_address_public(addr) is not False:
                    problems.append("%s judged public under permissive "
                                    "tables" % addr)
        finally:
            for n in names:
                if n in saved:
                    setattr(cls, n, saved[n])
        return problems, []
    _guard(suite, GRP_N, "N20 v4-special-purpose-refused-whatever-the-tables",
           v4_denylist)

    def nat64_configured():
        """Security round 2 (F5b): an answer inside a configured NAT64
        prefix is refused 403 before any connect, like 64:ff9b::/96."""
        mod = _egress_mod()
        problems = []
        prefix = mod.ipaddress.ip_network("2606:4700:64::/96")
        addr = "2606:4700:64::808:808"
        if mod.egress_address_public(addr) is not True:
            problems.append("control %s judged not public without nat64"
                            % addr)
        if mod.egress_address_public(addr, (prefix,)) is not False:
            problems.append("%s judged public inside a configured prefix"
                            % addr)
        net = mod.NetworkConfig(allow=("allowed.example",), ports=(443,),
                                nat64=(prefix,))
        stub = _NStub({"allowed.example": [addr]})
        proxy = _n_proxy(mod, stub, network=net)
        try:
            c = _n_client(proxy, _n_connect("allowed.example"))
            got = _n_status(c)
            c.close()
            if got != 403:
                problems.append("CONNECT to a nat64 answer: %r, want 403"
                                % (got,))
            if stub.connected:
                problems.append("connected %r, want no connect"
                                % (stub.connected,))
        finally:
            proxy.close(time.monotonic() + 2.0)
            stub.close()
        return problems, []
    _guard(suite, GRP_N, "N21 configured-nat64-prefix-refused", nat64_configured)

    def global_budget():
        """Security round 2 (F12): a process-wide connection budget shared by
        every run's proxy; over it a connection is dropped like the per-run
        cap, and a released slot is usable again."""
        mod = _egress_mod()
        saved = []
        _patch(mod, saved, "_EGRESS_GLOBAL_BUDGET", threading.BoundedSemaphore(2))
        stub = _NStub()
        problems = []
        held = []
        proxies = []
        try:
            proxies = [_n_proxy(mod, stub), _n_proxy(mod, stub)]
            for i, proxy in enumerate(proxies):
                c = _n_client(proxy, _n_connect("allowed.example"))
                held.append(c)
                if _n_status(c) != 200:
                    problems.append("tunnel on proxy %d not established" % i)
            c = _n_client(proxies[0], _n_connect("allowed.example"))
            got = _n_status(c)
            c.close()
            if got is not None:
                problems.append("third connection across two proxies: %r, "
                                "want dropped (budget 2)" % (got,))
            held.pop().close()
            stub.peers[1].close()
            _n_until(lambda: proxies[1].active() < 1)
            time.sleep(0.2)
            c = _n_client(proxies[0], _n_connect("allowed.example"))
            got = _n_status(c)
            held.append(c)
            if got != 200:
                problems.append("after one connection ended: %r, want 200"
                                % (got,))
        finally:
            for c in held:
                c.close()
            for proxy in proxies:
                proxy.close(time.monotonic() + 2.0)
            stub.close()
            _restore(mod, saved)
        return problems, []
    _guard(suite, GRP_N, "N22 global-connection-budget-across-proxies",
           global_budget)


# ---------------------------------------------------------------------------
# the server: stdio child
# ---------------------------------------------------------------------------

class _Wire:
    """Line-delimited JSON-RPC over the server's stdio, with replies matched
    by id so several requests can be in flight at once (F6). Reads the raw fd
    with its own line buffer: select() on a buffered text stream would miss
    a second line already read into Python's buffer."""

    def __init__(self, config, env=None):
        _require_server()
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-B", SERVER, "--config", config],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=H.REPO_ROOT, env=H.child_env(env))
        self._buf = b""
        self._replies = {}
        self._id = 0
        self._err = bytearray()
        self._err_thread = threading.Thread(target=self._drain_err,
                                            daemon=True)
        self._err_thread.start()

    def _drain_err(self):
        try:
            # read1, not read: read(4096) on a pipe blocks until 4096 bytes
            # or EOF, so a short log line would sit unread (I10 reads it).
            for chunk in iter(lambda: self.proc.stderr.read1(4096), b""):
                self._err.extend(chunk)
                if len(self._err) > 65536:
                    del self._err[:len(self._err) - 65536]
        except (OSError, ValueError):
            pass

    def stderr_tail(self, limit=600):
        return bytes(self._err[-limit:]).decode("utf-8", "replace")

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method, params):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self._id

    def wait(self, msg_id, timeout=RPC_TIMEOUT):
        deadline = time.time() + timeout
        fd = self.proc.stdout.fileno()
        while msg_id not in self._replies:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise _WireError("timeout after %.1fs waiting for id %r; "
                                 "stderr=%r" % (timeout, msg_id,
                                                self.stderr_tail()))
            ready, _, _ = select.select([fd], [], [], min(0.25, remaining))
            if not ready:
                if self.proc.poll() is not None:
                    raise _WireError("server exited rc %r waiting for id %r; "
                                     "stderr=%r" % (self.proc.returncode,
                                                    msg_id,
                                                    self.stderr_tail()))
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                self.proc.poll()
                raise _WireError("server closed stdout (rc %r) waiting for "
                                 "id %r; stderr=%r"
                                 % (self.proc.returncode, msg_id,
                                    self.stderr_tail()))
            self._buf += chunk
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if not line.strip():
                    continue
                reply = json.loads(line.decode("utf-8"))
                if isinstance(reply, dict) and "id" in reply:
                    self._replies[reply["id"]] = reply
        return self._replies.pop(msg_id)

    def rpc(self, method, params=None, timeout=RPC_TIMEOUT):
        return self.wait(self.request(method, params), timeout)

    def initialize(self):
        reply = self.rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "ph-tests", "version": "1"}},
            timeout=STARTUP_TIMEOUT)
        if "result" not in reply:
            raise _WireError("initialize answered %r" % (reply,))
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, arguments, timeout=RPC_TIMEOUT):
        return self.wait(self.call_async(arguments), timeout)

    def call_async(self, arguments):
        return self.request("tools/call", {"name": TOOL,
                                           "arguments": arguments})

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


_NESTED = {}


def _nested_seatbelt(fx):
    """P23 control: the first 200 chars of sbx's stderr when sandbox-exec
    cannot apply ANY profile here (a nested Seatbelt), else None. Run once."""
    if "v" in _NESTED:
        return _NESTED["v"]
    reason = None
    if sys.platform == "darwin":
        argv = [sys.executable, "-I", "-B", SBX, "--ro", "--",
                sys.executable, "-c", "print('ok')"]
        try:
            rc, out, err = H.run_process(argv, timeout=60, cwd=fx.base)
        except subprocess.TimeoutExpired:
            rc, out, err = None, "", ""
        if out.strip() != "ok" and "sandbox_apply" in err:
            reason = ("sandbox-exec cannot apply a profile here: %s"
                      % err.strip()[:200])
    _NESTED["v"] = reason
    return reason


class _Session:
    """A started, initialized server over stdio. A start that fails under a
    nested Seatbelt is INFO (P23); anywhere else it is the row's failure."""

    def __init__(self, fx, label, interpreters=None):
        self.fx = fx
        if interpreters is None:
            interpreters = _interpreters(full=False)
        self.wire = _Wire(fx.render(label, interpreters=interpreters))

    def __enter__(self):
        try:
            self.wire.initialize()
        except _WireError:
            self.wire.close()
            nested = _nested_seatbelt(self.fx)
            if nested:
                raise _Info(nested)
            raise
        return self.wire

    def __exit__(self, exc_type, exc, tb):
        self.wire.close()
        return False


def _result(reply):
    if "error" in reply:
        raise AssertionError("tools/call answered a JSON-RPC error %r"
                             % (reply["error"],))
    return reply.get("result") or {}


def _text(result):
    content = result.get("content") or []
    return "".join(c.get("text", "") for c in content
                   if isinstance(c, dict))


def _payload(result):
    """The result object: structuredContent, else the JSON of the text."""
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        return sc
    try:
        obj = json.loads(_text(result))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _refusal(result):
    """(code, message) of an isError refusal, or (None, None)."""
    obj = _payload(result) or {}
    err = obj.get("error")
    if isinstance(err, dict):
        return err.get("code"), str(err.get("message", ""))
    return None, None


def _expect_refusal(result, code, label, problems, contains=()):
    if not result.get("isError"):
        problems.append("%s: isError %r, want true" % (label,
                                                       result.get("isError")))
    got, message = _refusal(result)
    if got != code:
        problems.append("%s: error.code %r, want %r (text %r)"
                        % (label, got, code, _text(result)[:200]))
    for needle in contains:
        if needle not in (message or ""):
            problems.append("%s: message %r lacks %r"
                            % (label, message, needle))
    return message


# ---------------------------------------------------------------------------
# F: MCP surface
# ---------------------------------------------------------------------------

def _run_f(suite, fx):
    def one_tool():
        with _Session(fx, "f1") as w:
            reply = w.rpc("tools/list")
        tools = (reply.get("result") or {}).get("tools") or []
        if len(tools) != 1 or tools[0].get("name") != TOOL:
            return ["tools %r, want exactly [%s]"
                    % ([t.get("name") for t in tools], TOOL)], []
        tool = tools[0]
        problems = []
        schema = tool.get("inputSchema") or {}
        if schema.get("type") != "object":
            problems.append("inputSchema is not an object schema")
        props = schema.get("properties") or {}
        enum = (props.get("function") or {}).get("enum")
        if sorted(enum or []) != FUNCTIONS or len(enum or []) != 2:
            problems.append("function enum %r, want %r" % (enum, FUNCTIONS))
        if (props.get("params") or {}).get("type") != "object":
            problems.append("params is not typed object")
        desc = tool.get("description") or ""
        for code in ERROR_CODES:
            if code not in desc:
                problems.append("description does not state %r" % code)
        if "a failing script is a normal result" not in desc.lower():
            problems.append("description does not say a failing script is "
                            "a normal result")
        return problems, ["description : %d chars" % len(desc)]
    _guard(suite, GRP_F, "F1 tools-list-one-tool", one_tool)

    def did_you_mean():
        problems = []
        with _Session(fx, "f2") as w:
            res = _result(w.call({"function": "run_scrip", "params": {}}))
            msg = _expect_refusal(res, "invalid_params", "run_scrip",
                                  problems,
                                  contains=("Did you mean 'run_script'",))
            res = _result(w.call({"function": "capabilites"}))
            _expect_refusal(res, "invalid_params", "capabilites", problems,
                            contains=("Did you mean 'capabilities'",))
        return problems, ["message     : %r" % (msg,)]
    _guard(suite, GRP_F, "F2 unknown-function-invalid-params-did-you-mean",
           did_you_mean)

    def unknown_param():
        problems = []
        with _Session(fx, "f3") as w:
            for function in FUNCTIONS:
                res = _result(w.call({"function": function,
                                      "params": {"bogus_param": 1}}))
                _expect_refusal(res, "invalid_params",
                                "%s {bogus_param}" % function, problems,
                                contains=("bogus_param",))
            # A JSON-encoded object string is decoded (fleet behaviour, R3:M2)
            # and then held to the same member rules.
            res = _result(w.call({"function": "capabilities",
                                  "params": '{"bogus_param": 1}'}))
            _expect_refusal(res, "invalid_params",
                            "capabilities params as a JSON string", problems,
                            contains=("bogus_param",))
            res = _result(w.call({"function": "capabilities",
                                  "params": "[1, 2]"}))
            _expect_refusal(res, "invalid_params",
                            "params a non-object JSON string", problems)
        return problems, []
    _guard(suite, GRP_F, "F3 unknown-param-refused", unknown_param)

    def non_object():
        problems = []
        with _Session(fx, "f4") as w:
            for params in ([TOOL, {}], "sandbox_call", 7):
                reply = w.rpc("tools/call", params)
                err = reply.get("error")
                if not isinstance(err, dict) or err.get("code") != -32602:
                    problems.append("tools/call params %r: %r, want a "
                                    "JSON-RPC error -32602" % (params, reply))
            reply = w.rpc("tools/list")              # control: still serving
            if not (reply.get("result") or {}).get("tools"):
                problems.append("tools/list after the refusals: %r" % reply)
        return problems, []
    _guard(suite, GRP_F, "F4 non-object-params-32602", non_object)

    def capabilities_shape():
        with _Session(fx, "f5") as w:
            res = _result(w.call({"function": "capabilities"}))
        if res.get("isError"):
            code, msg = _refusal(res)
            return ["capabilities answered isError: %s %r" % (code, msg)], []
        cap = _payload(res)
        if cap is None:
            return ["capabilities is not a JSON object: %r"
                    % _text(res)[:200]], []
        problems = []
        want = {"interpreters", "limits", "backend", "concurrency",
                "quarantine", "degraded"}
        missing = sorted(want - set(cap))
        if missing:
            problems.append("missing members %s" % missing)
        quarantine = cap.get("quarantine")
        if not isinstance(quarantine, dict) or set(quarantine) != {"count",
                                                                   "max"}:
            problems.append("quarantine %r, want {count, max}"
                            % (quarantine,))
        else:
            count = quarantine["count"]
            if type(count) is not int or count < 0:
                problems.append("quarantine.count %r" % (count,))
            if quarantine["max"] != QUARANTINE_MAX:
                problems.append("quarantine.max %r, want %d"
                                % (quarantine["max"], QUARANTINE_MAX))
        conc = cap.get("concurrency") or {}
        if conc.get("max") != 2 or conc.get("inFlight") != 0:
            problems.append("concurrency %r, want max 2 inFlight 0" % (conc,))
        if "degraded" in cap and cap["degraded"] is not None:
            problems.append("degraded %r on a fresh runner"
                            % (cap["degraded"],))
        backend = cap.get("backend") or {}
        if backend.get("platform") != sys.platform:
            problems.append("backend.platform %r, want %r"
                            % (backend.get("platform"), sys.platform))
        interps = cap.get("interpreters") or {}
        if sorted(interps) != ["python3"]:
            problems.append("interpreters %s, want ['python3']"
                            % sorted(interps))
        for name, entry in interps.items():
            state = (entry or {}).get("state")
            if state not in ("ready", "refresh-failed"):
                problems.append("%s.state %r" % (name, state))
        return problems, ["members     : %s" % sorted(cap)]
    _guard(suite, GRP_F, "F5 capabilities-shape", capabilities_shape)

    def during_two_runs():
        _require_server()
        nested = _nested_seatbelt(fx)
        if nested:
            raise _Info(nested)
        script = "import time\ntime.sleep(4)\nprint('done')\n"
        run = {"function": "run_script", "params": {
            "files": [{"path": "slow.py", "content": script}],
            "entry": "slow.py", "interpreter": "python3",
            "timeoutMs": 30000}}
        problems = []
        with _Session(fx, "f6") as w:
            started = time.time()
            first = w.call_async(run)
            second = w.call_async(run)
            time.sleep(1.0)
            cap_id = w.call_async({"function": "capabilities"})
            res = _result(w.wait(cap_id, RPC_TIMEOUT))
            answered = time.time() - started
            if res.get("isError"):
                problems.append("capabilities answered isError: %r"
                                % (_refusal(res),))
            cap = _payload(res) or {}
            in_flight = (cap.get("concurrency") or {}).get("inFlight")
            if in_flight != 2:
                problems.append("concurrency.inFlight %r, want 2"
                                % (in_flight,))
            runs = []
            for msg_id in (first, second):
                rres = _result(w.wait(msg_id, RUN_TIMEOUT))
                runs.append(rres)
                if rres.get("isError"):
                    problems.append("run %d answered isError: %r"
                                    % (msg_id, _refusal(rres)))
                    continue
                out = _payload(rres) or {}
                if out.get("exitCode") != 0 or "done" not in str(
                        out.get("stdout", "")):
                    problems.append("run %d: exitCode %r stdout %r"
                                    % (msg_id, out.get("exitCode"),
                                       out.get("stdout")))
            finished = time.time() - started
            if answered >= finished:
                problems.append("capabilities (%.1fs) did not answer before "
                                "the runs finished (%.1fs)"
                                % (answered, finished))
        return problems, ["capabilities: %.2fs" % answered]
    _guard(suite, GRP_F, "F6 capabilities-answers-during-two-runs",
           during_two_runs)

    def handler_crash():
        mod = _mod()
        secret = "f7-secret-%d" % os.getpid()

        def boom(*_a, **_k):
            raise RuntimeError(secret)

        table = getattr(mod, "HANDLERS", None)
        if not isinstance(table, dict) or "capabilities" not in table:
            return ["no module-level HANDLERS table carrying 'capabilities' "
                    "(the fleet's dispatch shape)"], []
        saved = table["capabilities"]
        if isinstance(saved, tuple):
            table["capabilities"] = (boom,) + tuple(saved[1:])
        else:
            table["capabilities"] = boom

        records = []

        class _Catch(logging.Handler):
            def emit(self, record):
                records.append(record)

        catch = _Catch(level=logging.DEBUG)
        logger = logging.getLogger(LOGGER)
        logger.addHandler(catch)
        try:
            result = mod.handle_sandbox_call({"function": "capabilities",
                                              "params": {}})
        finally:
            logger.removeHandler(catch)
            table["capabilities"] = saved
        problems = []
        want = {"error": {"code": "internal", "message": "RuntimeError"}}
        if result != want:
            problems.append("result %r, want %r" % (result, want))
        if "error" not in (result or {}):
            problems.append("no top-level 'error' key: isError would be "
                            "false")
        if secret in json.dumps(result, default=str):
            problems.append("the exception's message reached the caller")
        logged = [r for r in records if r.levelno >= logging.ERROR
                  and r.exc_info]
        if not logged:
            problems.append("no ERROR record with exc_info on %r (want "
                            "log.exception)" % LOGGER)
        for r in logged:
            if r.args:
                problems.append("log.exception format has arguments %r"
                                % (r.args,))
            if secret in r.getMessage():
                problems.append("the log message carries the payload")
        return problems, ["records     : %d" % len(records)]
    _guard(suite, GRP_F, "F7 handler-crash-iserror-and-log-exception",
           handler_crash)

    def omitted_function():
        problems = []
        texts = []
        with _Session(fx, "f8") as w:
            for args in ({"params": {}}, {"function": ""},
                         {"function": "  "}):
                res = _result(w.call(args))
                text = _text(res)
                if res.get("isError"):
                    problems.append("%r: isError true, want the unflagged "
                                    "status" % (args,))
                for name in FUNCTIONS:
                    if name not in text:
                        problems.append("%r: status text lacks %r"
                                        % (args, name))
                texts.append(text)
            for args in ({"function": 7}, {"function": ["run_script"]}):
                res = _result(w.call(args))
                _expect_refusal(res, "invalid_params", repr(args), problems)
        return problems, ["status      : %r" % (texts[0][:120]
                                                if texts else "")]
    _guard(suite, GRP_F, "F8 omitted-function-status-unflagged",
           omitted_function)

    def handler_crash_live():
        # F7 reaches layer D in-process; the smoke harness no longer can
        # (non-object params is -32602 now). This row drives a framed
        # tools/call through McpServer.run() itself (mcp_search group G's
        # run-catch-all-live precedent), with HANDLERS["capabilities"]
        # raising: the reply must be a tool result with isError true and
        # code internal, and the ping after it must still be answered.
        mod = _mod()
        secret = "f9-secret-%d" % os.getpid()

        def boom(*_a, **_k):
            raise RuntimeError(secret)

        table = getattr(mod, "HANDLERS", None)
        if not isinstance(table, dict) or "capabilities" not in table:
            return ["no module-level HANDLERS table carrying 'capabilities' "
                    "(the fleet's dispatch shape)"], []
        # run() reads only config.limits.max_concurrent (the worker pool);
        # the raiser stands where every other config read would be.
        config = types.SimpleNamespace(
            limits=types.SimpleNamespace(max_concurrent=1))
        msgs = [{"jsonrpc": "2.0", "id": 31, "method": "tools/call",
                 "params": {"name": TOOL,
                            "arguments": {"function": "capabilities",
                                          "params": {}}}},
                {"jsonrpc": "2.0", "id": 32, "method": "ping"}]
        saved = table["capabilities"]
        if isinstance(saved, tuple):
            table["capabilities"] = (boom,) + tuple(saved[1:])
        else:
            table["capabilities"] = boom
        records = []

        class _Catch(logging.Handler):
            def emit(self, record):
                records.append(record)

        catch = _Catch(level=logging.DEBUG)
        logger = logging.getLogger(LOGGER)
        logger.addHandler(catch)
        try:
            replies = _drive_run(mod.McpServer(config), msgs)
        finally:
            logger.removeHandler(catch)
            table["capabilities"] = saved
        problems = []
        reply = replies.get(31) or {}
        if "error" in reply:
            problems.append("id 31 answered a JSON-RPC error %r, want a tool "
                            "result" % (reply["error"],))
        res = reply.get("result") or {}
        if res.get("isError") is not True:
            problems.append("id 31 isError %r, want true (reply %r)"
                            % (res.get("isError"), reply))
        want = {"error": {"code": "internal", "message": "RuntimeError"}}
        if res.get("structuredContent") != want:
            problems.append("id 31 structuredContent %r, want %r"
                            % (res.get("structuredContent"), want))
        if secret in json.dumps(replies, default=str):
            problems.append("the exception's message reached the wire")
        if (replies.get(32) or {}).get("result") != {}:
            problems.append("the ping after the crash was not answered: %r"
                            % (replies.get(32),))
        if not [r for r in records if r.levelno >= logging.ERROR
                and r.exc_info]:
            problems.append("no ERROR record with exc_info on %r (want "
                            "log.exception)" % LOGGER)
        return problems, ["replies     : %r" % sorted(replies, key=str)]
    _guard(suite, GRP_F, "F9 handler-crash-live-jsonrpc-iserror-keeps-serving",
           handler_crash_live)


LIVE_REPLY_TIMEOUT = 10.0


def _drive_run(server, msgs, timeout=LIVE_REPLY_TIMEOUT):
    """Feed `msgs` to `server.run()` one framed line at a time -> {id: reply}.

    run() cancels every in-flight task at stdin EOF, so a plain StringIO stdin
    would race the reply against EOF. This stdin hands over the next line, or
    EOF, only once the previous message's reply is on stdout (or `timeout`
    passed, so a lost reply ends the run instead of hanging it).
    """
    replies = {}
    cond = threading.Condition()
    pending = list(msgs)

    class _Out:
        def write(self, text):
            with cond:
                for raw in text.splitlines():
                    try:
                        reply = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(reply, dict):
                        replies[reply.get("id")] = reply
                cond.notify_all()
            return len(text)

        def flush(self):
            pass

    class _In:
        last = None

        def readline(self):
            with cond:
                if self.last is not None:
                    want = self.last
                    cond.wait_for(lambda: want in replies, timeout)
                if not pending:
                    return ""
                msg = pending.pop(0)
                self.last = msg.get("id")
                return json.dumps(msg) + "\n"

    saved_in, saved_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = _In(), _Out()
    try:
        asyncio.run(server.run())
    finally:
        sys.stdin, sys.stdout = saved_in, saved_out
    return replies


# ---------------------------------------------------------------------------
# H: workdir lifecycle, out/ collection, cleanup and quarantine (Step 10)
# ---------------------------------------------------------------------------
# The in-process contract (plan Step 10, KD-9, KD-17, KD-18). Where the plan
# leaves a shape open, these rows PIN it (marked "pinned"):
#   prepare_work_root(path) -> (root_fd, lock_fd, quarantine_fd); the caller
#       owns and closes the three fds.
#   sweep_work_root(root_fd, quarantine_fd, deadline) -> int, the quarantine
#       count; a foreign entry refuses start by RAISING (any Exception, or a
#       SystemExit whose code is not 0) -- pinned -- and the root-level name
#       check runs before any removal.
#   create_workdir(root_fd) -> (name, wd_fd); the caller closes wd_fd; tmp/
#       and out/ are directories with mode & 0o077 == 0 (pinned).
#   materialize(wd_fd, files), `files` in the RunRequest.files shape
#       ((relpath, bytes, executable), ...); a refusal RAISES (pinned).
#   collect_out(wd_fd, limits) -> (out_files, truncated), `limits` a Limits;
#       each out file a dict carrying "path" (relative to out/, "/"-joined),
#       "content" and "encoding" ("utf8" | "base64") -- pinned.
#   remove_workdir(root_fd, name, deadline) -> bool, reaching
#       remove_tree(root_fd, name, abs_root, deadline) -> bool through the
#       MODULE GLOBAL; the CALLER quarantines on False (Quarantine.add).
#   _check_free_space(root, min_free_bytes) -> None, or a
#       {"error": {"code": "sandbox_unavailable", ...}} refusal that does not
#       echo the root path; it calls shutil.disk_usage through the module
#       global `shutil`, which H9 replaces (pinned name and shape).
#   Quarantine; _QUARANTINE_MAX = 16; _FLATTEN_DEPTH = 50; _RM_MAX_OPS.
#   H15/H16/H18 (green in Step 12): run_one(req) -> the result object (or
#       {"structuredContent": <it>}), a refusal {"error": {...}};
#       RunnerState(cfg, root_fd, lock_fd, quarantine_fd) counts quarantine/
#       on construction and spawns nothing, carries .degraded (None |
#       "quarantine full"), and .plan_all(deadline) plans every configured
#       interpreter; run_one and capabilities() read the module global
#       _STATE, which the rows set to the state they built (all pinned).
# No production test hook exists for any of it: the rows replace module
# globals with setattr and restore them in a finally (R3:M5).

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MISSING = object()

_STEP10_NAMES = ("prepare_work_root", "sweep_work_root", "create_workdir",
                 "materialize", "collect_out", "remove_tree",
                 "remove_workdir", "Quarantine")
_RUNNER_NAMES = _STEP10_NAMES + ("run_one", "RunnerState", "PlanCache",
                                 "capabilities")

CHAIN_SEG = 99          # M0 item 4b: the 99-deep carrier/parent under the cap
H11_ROUNDS = 101        # 99 * (101 + 1) = 10 098 levels >= 10 000
# H11 holds correctness at 10k levels, H19 holds the deadline (IMPL:H11
# follow-up 2026-10-11). A first call that misses the shipped deadline is
# resumed on the same name with a fresh deadline, as Quarantine.retry does in
# production. The remover is linear and its progress is monotone across calls
# (lifted .sbx-flat-* segments persist, nothing already removed comes back),
# so each extra call starts on a strictly smaller tree; 6 extra calls is 70 s
# of budget in total, 7x the slowest single removal measured (9.43 s). A tree
# that still stands after that is not slow, it is stuck: FAIL.
H11_EXTRA_CALLS = 6
H19_ROUNDS = 20         # 99 * (20 + 1) = 2 079 levels, item 4b's tree
H19_NOFILE = 256        # item-4.md finding 4: one fd per frame -> EMFILE ~252
H19_REFERENCE_S = 0.29  # item 4b, 2079 levels, this host
FLATTEN_DEPTH = 50      # M0:9
CLEANUP_DEADLINE_S = 10.0
STARTUP_SWEEP_S = 20.0
STARTUP_PLANS_S = 75.0
DEGRADED_RETRY_S = 2.0
H17_PLANTED = 40
H17_SWEEP_S = 2.0       # the injected _STARTUP_SWEEP_DEADLINE_S
H17_STUB_S = 0.5
H18_STUB_S = 0.25
H_SLACK_S = 0.5
FIFO_WAIT_S = 5.0
RM_LIST_BATCH = 1024    # Q1: entries the remover reads between two ticks

_Usage = collections.namedtuple("_Usage", "total used free")


def _h_mod(names=_STEP10_NAMES,
           why="Step 10 workdir lifecycle / flatten remover not implemented"):
    """The server held to the Step 10 names; their absence is the stated RED
    reason, checked before any hostile fixture exists."""
    mod = _mod()
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        raise _Red("Scripts/mcp-sandbox.py has no %s (%s)"
                   % (", ".join(missing), why))
    return mod


def _runner_mod():
    return _h_mod(_RUNNER_NAMES, "Step 10 workdir lifecycle, Step 11 run_one "
                                 "or Step 12 RunnerState/PlanCache/"
                                 "capabilities not implemented")


def _patch(mod, saved, name, value):
    saved.append((name, mod.__dict__.get(name, _MISSING)))
    setattr(mod, name, value)


def _restore(mod, saved):
    while saved:
        name, old = saved.pop()
        if old is _MISSING:
            try:
                delattr(mod, name)
            except AttributeError:
                pass
        else:
            setattr(mod, name, old)


def _write(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# -- test-side forced removal (failure paths only) ---------------------------
# A row that fails must not leave chmod 000 / uchg / ACL'd / 10k-deep trees
# behind for shutil.rmtree(ignore_errors=True) to skip silently. This is the
# item-4b prototype's flatten walk without a deadline, test-owned fixtures
# only (no sandboxed writer races it).

def _scrub_open(name, dir_fd):
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except PermissionError:
        os.chmod(name, 0o700, dir_fd=dir_fd)
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    if (os.fstat(fd).st_mode & 0o700) != 0o700:
        os.fchmod(fd, 0o700)
    return fd


def _scrub_segment(parent_fd, top):
    stack = [[top, _scrub_open(top, parent_fd), None]]
    try:
        while stack:
            frame = stack[-1]
            if frame[2] is None:
                with os.scandir(frame[1]) as it:
                    frame[2] = [(e.name, e.is_dir(follow_symlinks=False))
                                for e in it]
            if frame[2]:
                name, is_dir = frame[2].pop()
                if is_dir:
                    stack.append([name, _scrub_open(name, frame[1]), None])
                else:
                    os.unlink(name, dir_fd=frame[1])
                continue
            os.close(frame[1])
            stack.pop()
            os.rmdir(frame[0], dir_fd=stack[-1][1] if stack else parent_fd)
    finally:
        for frame in stack:
            try:
                os.close(frame[1])
            except OSError:
                pass


def _scrub_flat(parent_fd, top):
    segments = [top]
    seq = 0
    i = 0
    while i < len(segments):
        stack = [(_scrub_open(segments[i], parent_fd), 1)]
        try:
            while stack:
                fd, depth = stack.pop()
                try:
                    with os.scandir(fd) as it:
                        subs = [e.name for e in it
                                if e.is_dir(follow_symlinks=False)]
                    for name in subs:
                        if depth >= FLATTEN_DEPTH:
                            seq += 1
                            lift = ".h-scrub-%d-%07d" % (os.getpid(), seq)
                            os.rename(name, lift, src_dir_fd=fd,
                                      dst_dir_fd=parent_fd)
                            segments.append(lift)
                        else:
                            stack.append((_scrub_open(name, fd), depth + 1))
                finally:
                    os.close(fd)
        finally:
            for fd, _ in stack:
                try:
                    os.close(fd)
                except OSError:
                    pass
        i += 1
    for name in reversed(segments):
        _scrub_segment(parent_fd, name)


def _tool(*argv):
    try:
        subprocess.run(list(argv), capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _scrub_entry(parent, name, deep=False):
    """Remove parent/name whatever a failed row left there."""
    path = os.path.join(parent, name)
    try:
        if not os.path.lexists(path):
            return
        if not deep:
            if sys.platform == "darwin":
                for _ in range(2):
                    _tool("/usr/bin/chflags", "-R", "0", path)
                    _tool("/bin/chmod", "-R", "-N", path)
                    _tool("/bin/chmod", "-R", "u+rwx", path)
            else:
                _tool("/bin/chmod", "-R", "u+rwx", path)
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(path)
            return
        pfd = os.open(parent, _DIR_FLAGS)
        try:
            _scrub_flat(pfd, name)
        finally:
            os.close(pfd)
    except OSError:
        pass                      # Fixtures.cleanup's rmtree is the last word


class _Root:
    """One work root prepared by the server; closes its fds, reports what it
    still holds and scrubs a failed row's residue."""

    def __init__(self, mod, path):
        self.path = path
        self.fds = []
        try:
            fds = mod.prepare_work_root(path)
        except SystemExit as exc:         # a refused start must not end the run
            raise AssertionError("prepare_work_root exited %r" % (exc.code,))
        if not (isinstance(fds, tuple) and len(fds) == 3
                and all(type(fd) is int for fd in fds)):
            raise AssertionError("prepare_work_root returned %r, want "
                                 "(root_fd, lock_fd, quarantine_fd)" % (fds,))
        self.root_fd, self.lock_fd, self.q_fd = fds
        self.fds = list(fds)

    @property
    def quarantine(self):
        return os.path.join(self.path, "quarantine")

    @property
    def egress(self):
        return os.path.join(self.path, "egress")

    def entries(self):
        try:
            return sorted(os.listdir(self.quarantine))
        except OSError:
            return []

    def egress_entries(self):
        try:
            return sorted(os.listdir(self.egress))
        except OSError:
            return []

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []

    def residue(self):
        """Problems unless the root holds only .lock, an empty egress/ and
        an empty quarantine/ (plan section 8, R2:S-H1a)."""
        try:
            top = sorted(os.listdir(self.path))
        except OSError as exc:
            return ["work root unreadable: %s" % type(exc).__name__]
        out = []
        if top != [".lock", "egress", "quarantine"]:
            out.append("work root holds %s, want ['.lock', 'egress', "
                       "'quarantine']" % top[:6])
        left = self.entries()
        if left:
            out.append("quarantine/ holds %d entr%s %s"
                       % (len(left), "y" if len(left) == 1 else "ies",
                          left[:3]))
        stale = self.egress_entries()
        if stale:
            out.append("egress/ holds %s" % stale[:3])
        return out

    def scrub(self, deep=False):
        try:
            names = os.listdir(self.path)
        except OSError:
            return
        for name in names:
            if name not in (".lock", "quarantine", "egress"):
                _scrub_entry(self.path, name, deep)
        for name in self.entries():
            _scrub_entry(self.quarantine, name, deep)
        if os.path.isdir(self.egress) and not os.path.islink(self.egress):
            for name in self.egress_entries():
                _scrub_entry(self.egress, name, deep)


def _h_cfg(mod, fx, label, interpreters=None):
    if interpreters is None:
        interpreters = _interpreters(full=False)
    return mod.load_config(fx.render(label, interpreters=interpreters))


def _remove_ok(mod, root, name, label, problems):
    """remove_workdir under the shipped cleanup deadline: True AND gone."""
    started = time.monotonic()
    ok = mod.remove_workdir(root.root_fd, name,
                            started + mod._CLEANUP_DEADLINE_S)
    took = time.monotonic() - started
    if ok is not True:
        problems.append("%s: remove_workdir returned %r, want True"
                        % (label, ok))
    if os.path.lexists(os.path.join(root.path, name)):
        problems.append("%s: the workdir still exists after remove_workdir"
                        % label)
    return took


def _rename_chain(base_fd, prefix, n):
    fd = os.dup(base_fd)
    try:
        for i in range(1, n + 1):
            name = "%s%03d" % (prefix, i)
            os.mkdir(name, 0o700, dir_fd=fd)
            nxt = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _rename_chained_tree(wd_fd, rounds):
    """The tree a hostile script builds under the 100-level depth cap
    (item-depth.md driver3, M0 item 4b), built here unsandboxed and cheaply:
    a 99-deep carrier c001 is renamed under a fresh 99-deep parent p001..p099
    and the parent lifted back as the new carrier, `rounds` times. Every
    mkdir stays <= 99 levels deep, so the build is never depth-priced.
    Returns the carrier depth the fixture promises: 99 * (rounds + 1)."""
    os.close(_rename_chain(wd_fd, "c", CHAIN_SEG))
    pf = _rename_chain(wd_fd, "p", CHAIN_SEG)
    try:
        for _ in range(rounds):
            os.rename("c001", "c001", src_dir_fd=wd_fd, dst_dir_fd=pf)
            os.rename("p001", "c001", src_dir_fd=wd_fd, dst_dir_fd=wd_fd)
            os.close(pf)
            pf = -1
            pf = _rename_chain(wd_fd, "p", CHAIN_SEG)
    finally:
        if pf >= 0:
            os.close(pf)
    return CHAIN_SEG * (rounds + 1)


def _chain_depth(wd_fd, top):
    """Levels of the single chain under `top`, one fd open at a time."""
    fd = os.open(top, _DIR_FLAGS, dir_fd=wd_fd)
    depth = 1
    try:
        while True:
            with os.scandir(fd) as it:
                subs = [e.name for e in it if e.is_dir(follow_symlinks=False)]
            if not subs:
                return depth
            nxt = os.open(subs[0], _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
            depth += 1
    finally:
        os.close(fd)


def _out_map(res, label, problems):
    """({path: (content, encoding)}, truncated) of a collect_out result."""
    if not (isinstance(res, tuple) and len(res) == 2
            and isinstance(res[0], list)):
        problems.append("%s: collect_out returned %r, want (list, bool)"
                        % (label, type(res).__name__))
        return {}, None
    files, truncated = res
    out = {}
    for item in files:
        if not (isinstance(item, dict)
                and {"path", "content", "encoding"} <= set(item)):
            problems.append("%s: out file %r lacks path/content/encoding"
                            % (label, item if not isinstance(item, dict)
                               else sorted(item)))
            continue
        out[item["path"]] = (item["content"], item["encoding"])
    if type(truncated) is not bool:
        problems.append("%s: truncated %r is not a bool" % (label, truncated))
    return out, truncated


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _in_thread(fn, timeout):
    """(value, exception, still_running) of fn() on a worker thread."""
    box = {}

    def worker():
        try:
            box["v"] = fn()
        except BaseException as exc:          # noqa: BLE001 -- reported
            box["e"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("v"), box.get("e"), t.is_alive()


def _plant(directory, n, tag):
    """n ^[0-9a-f]{32}$ directories (each holding one file) in `directory`."""
    names = []
    for _ in range(n):
        name = os.urandom(16).hex()
        os.mkdir(os.path.join(directory, name), 0o700)
        _write(os.path.join(directory, name, "left.txt"), tag)
        names.append(name)
    return names


def _refusal_code(res):
    if isinstance(res, dict) and isinstance(res.get("error"), dict):
        return res["error"].get("code")
    return None


def _run_body(res):
    if isinstance(res, dict) and isinstance(res.get("structuredContent"),
                                            dict):
        return res["structuredContent"]
    return res


def _run_ok(res, exc, alive, tag, label, problems):
    """A normal run result whose stdout carries `tag`."""
    if alive:
        problems.append("%s: run_one did not return within %.0fs"
                        % (label, RUN_TIMEOUT))
        return False
    if exc is not None:
        problems.append("%s: run_one raised %s" % (label, type(exc).__name__))
        return False
    code = _refusal_code(res)
    if code is not None:
        problems.append("%s: refused %s (%r)" % (label, code,
                                                 res["error"].get("message")))
        return False
    body = _run_body(res)
    if not isinstance(body, dict):
        problems.append("%s: result %r is not an object"
                        % (label, type(body).__name__))
        return False
    if body.get("exitCode") != 0 or tag not in str(body.get("stdout", "")):
        problems.append("%s: exitCode %r stdout %r" % (
            label, body.get("exitCode"), str(body.get("stdout", ""))[:80]))
        return False
    return True


def _bash_req(mod, tag):
    return mod.RunRequest(files=(("main.sh", ("echo %s\n" % tag).encode(),
                                  False),),
                          entry="main.sh", interpreter="bash", args=(),
                          stdin=None, timeout_ms=10000)


def _live_runner(mod, fx, label):
    """(cfg, root, state) for a live /bin/bash runner; INFO off macOS and
    under a nested Seatbelt (P23)."""
    if sys.platform != "darwin":
        raise _Info("live run through the real sbx: macOS only (plan Step "
                    "10: Linux INFO)")
    nested = _nested_seatbelt(fx)
    if nested:
        raise _Info(nested)
    if not os.path.isfile("/bin/bash"):
        raise _Info("no /bin/bash on this host")
    cfg = _h_cfg(mod, fx, label, interpreters={"bash": {"path": "/bin/bash"}})
    root = _Root(mod, cfg.work_root)
    try:
        state = mod.RunnerState(cfg, root.root_fd, root.lock_fd, root.q_fd)
    except BaseException:
        root.close()
        raise
    return cfg, root, state


def _capabilities(mod, label, problems):
    cap = mod.capabilities()
    if not isinstance(cap, dict) or "error" in cap:
        problems.append("%s: capabilities() answered %r" % (label, cap))
        return {}
    return cap


class _CountingOs(types.ModuleType):
    """`os` as a server module global (validation round 1, Q1 / W2): every
    attribute is the real one, except that scandir counts the entries it
    hands out and the spelled-path flag calls are recorded."""

    def __init__(self, calls=None):
        super().__init__("os")
        self.yielded = 0
        self.calls = calls if calls is not None else []

    def __getattr__(self, name):
        return getattr(os, name)

    def scandir(self, *args, **kwargs):
        inner = os.scandir(*args, **kwargs)
        owner = self

        class _It:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                inner.close()
                return False

            def __iter__(self):
                return self

            def __next__(self):
                entry = next(inner)
                owner.yielded += 1
                return entry

            def close(self):
                inner.close()
        return _It()

    def lchflags(self, *args, **kwargs):
        self.calls.append("lchflags")
        return os.lchflags(*args, **kwargs)

    def chflags(self, *args, **kwargs):
        self.calls.append("chflags")
        return os.chflags(*args, **kwargs)


class _LibcRecorder:
    """The server's ctypes libc with every attribute fetch recorded (W2)."""

    def __init__(self, libc, calls):
        self._libc = libc
        self._calls = calls

    def __getattr__(self, name):
        self._calls.append(name)
        return getattr(self._libc, name)


_SPELLED_CALLS = ("chmodx_np", "acl_set_file", "acl_set_link_np", "lchflags",
                  "chflags")


def _kd17_sum(mod):
    return (mod._DEGRADED_RETRY_S + mod._REFRESH_DEADLINE_S + 120
            + mod._HARD_DEADLINE_SLACK_S + mod._KILL_GRACE_S + mod._DRAIN_S
            + mod._CLEANUP_DEADLINE_S)


def _run_h(suite, fx):
    def workdir_unique():
        mod = _h_mod()
        problems = []
        root = _Root(mod, fx.work_root("h1"))
        names = []
        try:
            euid = os.geteuid()
            for i in range(2):
                name, wd_fd = mod.create_workdir(root.root_fd)
                try:
                    st = os.fstat(wd_fd)
                finally:
                    os.close(wd_fd)
                if not isinstance(name, str) or not _HEX32.match(name):
                    problems.append("workdir %d: name %r is not "
                                    "^[0-9a-f]{32}$" % (i, name))
                    continue
                names.append(name)
                path = os.path.join(root.path, name)
                lst = os.lstat(path)
                if (lst.st_dev, lst.st_ino) != (st.st_dev, st.st_ino):
                    problems.append("workdir %d: wd_fd is not the inode at "
                                    "<root>/<name>" % i)
                if not stat.S_ISDIR(st.st_mode) or \
                        stat.S_IMODE(st.st_mode) != 0o700:
                    problems.append("workdir %d: mode %04o, want a 0700 "
                                    "directory" % (i, stat.S_IMODE(
                                        st.st_mode)))
                if st.st_uid != euid:
                    problems.append("workdir %d: owner %d, want euid %d"
                                    % (i, st.st_uid, euid))
                for sub in ("tmp", "out"):
                    try:
                        sst = os.lstat(os.path.join(path, sub))
                    except OSError:
                        problems.append("workdir %d: no %s/" % (i, sub))
                        continue
                    if not stat.S_ISDIR(sst.st_mode) or sst.st_mode & 0o077:
                        problems.append("workdir %d: %s/ mode %04o, want a "
                                        "directory with mode & 0o077 == 0"
                                        % (i, sub, stat.S_IMODE(sst.st_mode)))
            if len(names) == 2 and names[0] == names[1]:
                problems.append("two create_workdir calls returned the same "
                                "name")
            for name in names:
                _remove_ok(mod, root, name, "workdir %s" % name[:8], problems)
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, ["names       : %s" % [n[:8] for n in names]]
    _guard(suite, GRP_H, "H1 workdir-0700-unique", workdir_unique)

    def materialize_excl():
        mod = _h_mod()
        problems = []
        refusals = []
        root = _Root(mod, fx.work_root("h2"))
        outside = tempfile.mkdtemp(prefix="h2-outside-", dir=fx.base)
        secret = os.path.join(outside, "secret.txt")
        _write(secret, "h2-original")
        try:
            # Control: nested components, a shared parent, exact content.
            name, wd_fd = mod.create_workdir(root.root_fd)
            wd = os.path.join(root.path, name)
            files = (("main.py", b"print(1)\n", False),
                     ("pkg/a.py", b"A", False), ("pkg/b.py", b"B", False),
                     ("pkg/sub/c.py", b"C", False))
            try:
                mod.materialize(wd_fd, files)
                for rel, data, _x in files:
                    path = os.path.join(wd, rel)
                    if not os.path.isfile(path) or os.path.islink(path) or \
                            _read(path) != data:
                        problems.append("control: %s not materialized as "
                                        "%r" % (rel, data))
                for rel in ("pkg", "pkg/sub"):
                    mode = os.lstat(os.path.join(wd, rel)).st_mode
                    if not stat.S_ISDIR(mode) or mode & 0o077:
                        problems.append("control: %s/ mode %04o"
                                        % (rel, stat.S_IMODE(mode)))
            except Exception as exc:          # noqa: BLE001 -- the finding
                problems.append("control: materialize raised %s: %s"
                                % (type(exc).__name__, str(exc)[:120]))
            finally:
                os.close(wd_fd)
            _remove_ok(mod, root, name, "control workdir", problems)

            # Hostile: a leaf symlink, a component symlink, an existing file.
            name, wd_fd = mod.create_workdir(root.root_fd)
            wd = os.path.join(root.path, name)
            try:
                os.symlink(secret, os.path.join(wd, "a.py"))
                os.symlink(outside, os.path.join(wd, "d"))
                _write(os.path.join(wd, "e.py"), "h2-existing")
                for rel, label in (("a.py", "leaf symlink to an outside file"),
                                   ("d/x.py", "component symlink to an "
                                              "outside directory"),
                                   ("e.py", "an existing regular file")):
                    try:
                        mod.materialize(wd_fd, ((rel, b"h2-PWNED", False),))
                    except Exception as exc:  # noqa: BLE001 -- the refusal
                        refusals.append("%s: %s" % (rel, type(exc).__name__))
                    else:
                        problems.append("%s (%s): materialized, want a "
                                        "refusal" % (rel, label))
                if _read(secret) != b"h2-original":
                    problems.append("the outside file was written through "
                                    "the leaf symlink")
                if os.path.lexists(os.path.join(outside, "x.py")):
                    problems.append("a file was created through the "
                                    "component symlink")
                if not os.path.islink(os.path.join(wd, "a.py")):
                    problems.append("the planted leaf symlink was replaced")
                if _read(os.path.join(wd, "e.py")) != b"h2-existing":
                    problems.append("the existing file was overwritten")
            finally:
                os.close(wd_fd)
            _remove_ok(mod, root, name, "hostile workdir", problems)
            if not os.path.isfile(secret) or not os.path.isdir(outside):
                problems.append("removing the workdir followed a symlink out "
                                "of it")
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, ["refusals    : %s" % "; ".join(refusals)]
    _guard(suite, GRP_H, "H2 materialize-excl-nofollow", materialize_excl)

    def executable_mode():
        mod = _h_mod()
        problems = []
        modes = {}
        root = _Root(mod, fx.work_root("h3"))
        try:
            name, wd_fd = mod.create_workdir(root.root_fd)
            wd = os.path.join(root.path, name)
            try:
                mod.materialize(wd_fd, (("run.sh", b"#!/bin/sh\necho 3\n",
                                         True),
                                        ("data.txt", b"h3", False),
                                        ("bin/tool", b"#!/bin/sh\n", True)))
            finally:
                os.close(wd_fd)
            for rel, want in (("run.sh", 0o700), ("data.txt", 0o600),
                              ("bin/tool", 0o700)):
                st = os.lstat(os.path.join(wd, rel))
                modes[rel] = "%04o" % stat.S_IMODE(st.st_mode)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    problems.append("%s: not a single-link regular file" % rel)
                if stat.S_IMODE(st.st_mode) != want:
                    problems.append("%s: mode %04o, want %04o"
                                    % (rel, stat.S_IMODE(st.st_mode), want))
            _remove_ok(mod, root, name, "workdir", problems)
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, ["modes       : %s" % modes]
    _guard(suite, GRP_H, "H3 materialize-executable-mode", executable_mode)

    def out_row(label, plant, want, want_truncated, secret=None):
        """collect_out over a workdir whose out/ `plant(wd)` filled: the out
        files are exactly `want` ({path: (content, encoding)}), truncated is
        `want_truncated` (None = not asserted), `secret` appears nowhere."""
        def body():
            mod = _h_mod()
            problems = []
            cfg = _h_cfg(mod, fx, label)
            root = _Root(mod, cfg.work_root)
            got = {}
            try:
                name, wd_fd = mod.create_workdir(root.root_fd)
                wd = os.path.join(root.path, name)
                try:
                    extra = plant(wd) or {}
                    fifo = extra.get("fifo")
                    res, exc, alive = _in_thread(
                        lambda: mod.collect_out(wd_fd, cfg.limits),
                        FIFO_WAIT_S)
                    if alive:
                        problems.append("collect_out still blocked after "
                                        "%.0fs" % FIFO_WAIT_S)
                        if fifo:
                            # Unblock the reader: a writer that opens and
                            # closes hands it EOF.
                            try:
                                os.close(os.open(fifo, os.O_WRONLY
                                                 | os.O_NONBLOCK))
                            except OSError:
                                pass
                        time.sleep(1.0)
                    elif exc is not None:
                        problems.append("collect_out raised %s: %s"
                                        % (type(exc).__name__,
                                           str(exc)[:120]))
                    else:
                        got, truncated = _out_map(res, "collect_out",
                                                  problems)
                        if got != want:
                            problems.append("out files %r, want %r"
                                            % (sorted(got), sorted(want)))
                            for path in sorted(set(got) & set(want)):
                                if got[path] != want[path]:
                                    problems.append("%s: %r, want %r" % (
                                        path, got[path], want[path]))
                        if want_truncated is not None and \
                                truncated is not want_truncated:
                            problems.append("truncated %r, want %r"
                                            % (truncated, want_truncated))
                        if secret and secret in json.dumps(res, default=str):
                            problems.append("the result carries the outside "
                                            "secret")
                finally:
                    os.close(wd_fd)
                _remove_ok(mod, root, name, "workdir", problems)
                problems.extend(root.residue())
            finally:
                root.close()
                root.scrub()
            return problems, ["collected   : %s" % sorted(got)]
        return body

    def outside_dir(tag, secret):
        path = tempfile.mkdtemp(prefix=tag + "-outside-", dir=fx.base)
        _write(os.path.join(path, "leak.txt"), secret)
        return path

    h4_secret = "h4-secret-%d" % os.getpid()

    def plant_h4(wd):
        out = os.path.join(wd, "out")
        _write(os.path.join(out, "a.txt"), "h4 alpha")
        os.mkdir(os.path.join(out, "sub"), 0o700)
        _write(os.path.join(out, "sub", "b.bin"), b"\xff\xfeh4")
        outside = outside_dir("h4", h4_secret)
        os.symlink(os.path.join(outside, "leak.txt"),
                   os.path.join(out, "s.txt"))
        os.symlink(outside, os.path.join(out, "sd"))
    _guard(suite, GRP_H, "H4 out-symlink-skipped",
           out_row("h4", plant_h4,
                   {"a.txt": ("h4 alpha", "utf8"),
                    "sub/b.bin": (_b64(b"\xff\xfeh4"), "base64")},
                   True, secret=h4_secret))

    h5_secret = "h5-secret-%d" % os.getpid()

    def plant_h5(wd):
        out = os.path.join(wd, "out")
        _write(os.path.join(out, "a.txt"), "h5 alpha")
        _write(os.path.join(wd, "orig.txt"), h5_secret)
        os.link(os.path.join(wd, "orig.txt"), os.path.join(out, "h.txt"))
    _guard(suite, GRP_H, "H5 out-hardlink-skipped",
           out_row("h5", plant_h5, {"a.txt": ("h5 alpha", "utf8")}, True,
                   secret=h5_secret))

    def plant_h6(wd):
        out = os.path.join(wd, "out")
        _write(os.path.join(out, "a.txt"), "h6 alpha")
        fifo = os.path.join(out, "p")
        os.mkfifo(fifo, 0o600)
        return {"fifo": fifo}
    _guard(suite, GRP_H, "H6 out-fifo-no-hang",
           out_row("h6", plant_h6, {"a.txt": ("h6 alpha", "utf8")}, True))

    h7_secret = "h7-secret-%d" % os.getpid()

    def plant_h7_control(wd):
        _write(os.path.join(wd, "out", "a.txt"), "h7 alpha")

    def plant_h7_symlink(wd):
        os.rmdir(os.path.join(wd, "out"))
        os.symlink(outside_dir("h7", h7_secret), os.path.join(wd, "out"))

    def plant_h7_file(wd):
        os.rmdir(os.path.join(wd, "out"))
        _write(os.path.join(wd, "out"), h7_secret)

    def out_replaced():
        problems = []
        lines = []
        # Control first: a real out/ is read, so an always-empty stub fails.
        for label, plant, want in (
                ("control out/ directory", plant_h7_control,
                 {"a.txt": ("h7 alpha", "utf8")}),
                ("out/ replaced by a symlink to an outside directory",
                 plant_h7_symlink, {}),
                ("out/ replaced by a regular file", plant_h7_file, {})):
            sub, extra = out_row("h7", plant, want, None,
                                 secret=h7_secret)()
            problems.extend("%s: %s" % (label, p) for p in sub)
            lines.extend("%s: %s" % (label, e) for e in extra)
        return problems, lines
    _guard(suite, GRP_H, "H7 out-replaced-by-symlink-empty", out_replaced)

    def hostile_row(label, plant, deep=False):
        """remove_workdir on a workdir `plant(wd)` made hostile: True, gone,
        nothing quarantined, the work root clean."""
        def body():
            mod = _h_mod()
            problems = []
            root = _Root(mod, fx.work_root(label))
            lines = []
            try:
                name, wd_fd = mod.create_workdir(root.root_fd)
                try:
                    lines.extend(plant(os.path.join(root.path, name), wd_fd,
                                       problems) or ())
                finally:
                    os.close(wd_fd)
                took = _remove_ok(mod, root, name, "remove_workdir",
                                  problems)
                lines.append("removal     : %.3fs" % took)
                problems.extend(root.residue())
            finally:
                root.close()
                root.scrub(deep=deep)
            return problems, lines
        return body

    def _uchg(path, problems):
        os.chflags(path, stat.UF_IMMUTABLE)
        if not os.lstat(path).st_flags & stat.UF_IMMUTABLE:
            problems.append("fixture: uchg did not stick on %s"
                            % os.path.basename(path))

    def plant_h8(wd, _wd_fd, problems):
        os.mkdir(os.path.join(wd, "d000"), 0o700)
        _write(os.path.join(wd, "d000", "f"), "h8")
        _write(os.path.join(wd, "f000"), "h8")
        os.chmod(os.path.join(wd, "f000"), 0)
        os.chmod(os.path.join(wd, "d000"), 0)
        if sys.platform == "darwin":
            _write(os.path.join(wd, "u.txt"), "h8")
            _uchg(os.path.join(wd, "u.txt"), problems)
            return ["uchg        : u.txt (darwin)"]
        return ["uchg        : skipped (not darwin)"]
    _guard(suite, GRP_H, "H8 cleanup-chmod000-and-uchg",
           hostile_row("h8", plant_h8))

    def low_disk():
        mod = _h_mod(_STEP10_NAMES + ("_check_free_space",))
        problems = []
        root_path = fx.work_root("h9")
        minimum = 1 << 30
        box = {}
        calls = []

        def fake_usage(path):
            calls.append(path)
            return _Usage(total=box["free"] + 1, used=1, free=box["free"])

        saved = []
        _patch(mod, saved, "shutil",
               types.SimpleNamespace(disk_usage=fake_usage))
        try:
            for free, refused in ((minimum - 1, True), (0, True),
                                  (minimum, False), (minimum * 4, False)):
                box["free"] = free
                got = mod._check_free_space(root_path, minimum)
                label = "free %d vs min_free_bytes %d" % (free, minimum)
                if refused:
                    if _refusal_code(got) != "sandbox_unavailable":
                        problems.append("%s: %r, want sandbox_unavailable"
                                        % (label, got))
                    elif root_path in json.dumps(got, default=str):
                        problems.append("%s: the refusal echoes the work "
                                        "root path" % label)
                elif got is not None:
                    problems.append("%s: %r, want None (accepted)"
                                    % (label, got))
        finally:
            _restore(mod, saved)
        if not calls:
            problems.append("the injected shutil.disk_usage was never called")
        elif any(c != root_path for c in calls):
            problems.append("disk_usage asked about %r, want the work root"
                            % (sorted(set(calls))[:2],))
        return problems, ["disk_usage  : %d calls" % len(calls)]
    _guard(suite, GRP_H, "H9 low-disk-refused", low_disk)

    def sweep_foreign():
        mod = _h_mod()
        problems = []
        lines = []

        def sweep(root):
            return mod.sweep_work_root(
                root.root_fd, root.q_fd,
                time.monotonic() + mod._STARTUP_SWEEP_DEADLINE_S)

        cases = (
            ("a foreign file in the work root", "root", "notes.txt", False),
            ("an UPPERCASE 32-hex directory in the work root", "root",
             "A" * 32, True),
            ("a foreign directory in quarantine/", "quarantine",
             "zz-foreign", True),
        )
        for label, where, foreign, as_dir in cases:
            root = _Root(mod, fx.work_root("h10"))
            try:
                leftover = _plant(root.path, 1, "h10")[0]
                parent = root.path if where == "root" else root.quarantine
                target = os.path.join(parent, foreign)
                if as_dir:
                    os.mkdir(target, 0o700)
                else:
                    _write(target, "h10")
                try:
                    got = sweep(root)
                except SystemExit as exc:
                    if exc.code in (0, None):
                        problems.append("%s: SystemExit(%r), want a refusal"
                                        % (label, exc.code))
                    else:
                        lines.append("%s: SystemExit(%r)" % (label, exc.code))
                except Exception as exc:      # noqa: BLE001 -- the refusal
                    lines.append("%s: %s" % (label, type(exc).__name__))
                else:
                    problems.append("%s: sweep returned %r, want a refusal"
                                    % (label, got))
                if not os.path.lexists(target):
                    problems.append("%s: the foreign entry was removed"
                                    % label)
                if where == "root" and not os.path.isdir(
                        os.path.join(root.path, leftover)):
                    problems.append("%s: a leftover was removed before the "
                                    "names were validated" % label)
            finally:
                root.close()
                root.scrub()
        # Control: leftovers and a quarantined entry, nothing foreign.
        root = _Root(mod, fx.work_root("h10-ok"))
        try:
            _plant(root.path, 2, "h10-left")
            _plant(root.quarantine, 1, "h10-q")
            got = sweep(root)
            if got != 0:
                problems.append("control: sweep returned %r, want 0" % (got,))
            problems.extend("control: %s" % p for p in root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H10 sweep-refuses-foreign-entry", sweep_foreign)

    def plant_deep(rounds):
        def plant(_wd, wd_fd, problems):
            started = time.monotonic()
            want = _rename_chained_tree(wd_fd, rounds)
            built = time.monotonic() - started
            depth = _chain_depth(wd_fd, "c001")
            if depth != want:
                problems.append("fixture: the chain is %d levels deep, want "
                                "%d" % (depth, want))
            return ["depth       : %d levels (%d rename-chained rounds)"
                    % (depth, rounds),
                    "build       : %.3fs" % built]
        return plant
    def deep_tree_removed():
        """H11: correctness, not timing (H19 is the timing gate). The first
        call runs under the shipped deadline exactly as hostile_row does; a
        clean True there is a PASS. A False there is resumed on the same name
        with a fresh deadline per call, up to H11_EXTRA_CALLS more; completing
        clean on a resume is a declared INFO, never completing is a FAIL, and
        any exception (RecursionError, ...) reaches _guard as a FAIL."""
        mod = _h_mod()
        problems = []
        lines = []
        calls = 0
        root = _Root(mod, fx.work_root("h11"))
        try:
            name, wd_fd = mod.create_workdir(root.root_fd)
            try:
                lines.extend(plant_deep(H11_ROUNDS)(
                    os.path.join(root.path, name), wd_fd, problems) or ())
            finally:
                os.close(wd_fd)
            started = time.monotonic()
            ok = mod.remove_workdir(root.root_fd, name,
                                    started + mod._CLEANUP_DEADLINE_S)
            first = time.monotonic() - started
            calls = 1
            while ok is False and calls < 1 + H11_EXTRA_CALLS:
                ok = mod.remove_workdir(
                    root.root_fd, name,
                    time.monotonic() + mod._CLEANUP_DEADLINE_S)
                calls += 1
            total = time.monotonic() - started
            if ok is not True:
                problems.append("remove_workdir returned %r after %d call%s "
                                "(%.3fs), want True"
                                % (ok, calls, "" if calls == 1 else "s",
                                   total))
            if os.path.lexists(os.path.join(root.path, name)):
                problems.append("the workdir still exists after "
                                "remove_workdir")
            lines.append("removal     : %.3fs" % first)
            if calls > 1:
                lines.append("resumed     : %d calls, %.3fs total"
                             % (calls, total))
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub(deep=True)
        if problems or calls <= 1:
            return problems, lines
        raise _Info("first call missed the %.0fs deadline (%.3fs); removed "
                    "clean after %d calls, %.3fs total | %s | deadline miss "
                    "is quarantine+resume in production; timing gate is H19"
                    % (mod._CLEANUP_DEADLINE_S, first, calls, total,
                       " | ".join(lines)))
    _guard(suite, GRP_H, "H11 deep-tree-removed", deep_tree_removed)

    def plant_h12(wd, _wd_fd, problems):
        inner = []
        cur = wd
        for i in (1, 2, 3):
            cur = os.path.join(cur, "n%d" % i)
            os.mkdir(cur, 0o700)
            _write(os.path.join(cur, "f%d" % i), "h12")
            inner.append(cur)
        if sys.platform == "darwin":
            _uchg(os.path.join(inner[-1], "f3"), problems)
        for path in reversed(inner):
            os.chmod(path, 0)
        return ["nesting     : n1/n2/n3 chmod 000%s"
                % (", n3/f3 uchg" if sys.platform == "darwin" else "")]
    _guard(suite, GRP_H, "H12 nested-chmod000-removed",
           hostile_row("h12", plant_h12))

    def plant_h13(wd, _wd_fd, _problems):
        for name, mode in (("d500", 0o500), ("d100", 0o100)):
            os.mkdir(os.path.join(wd, name), 0o700)
            _write(os.path.join(wd, name, "f"), "h13")
            os.chmod(os.path.join(wd, name), mode)
        return ["dirs        : d500 (0500), d100 (0100)"]
    _guard(suite, GRP_H, "H13 dir-0500-and-0100-removed",
           hostile_row("h13", plant_h13))

    # item-3l.md section 4: the ONE broad ACE that defeated acl_set_fd_np +
    # acl_set_link_np (it refuses every open, O_EVTONLY included). Since
    # validation round 1 (W2) setattrlistat(ATTR_CMN_EXTENDED_SECURITY,
    # FSOPT_NOFOLLOW) under the verified parent fd strips it -- H22 holds
    # the remover to that, with no spelled path at all.
    file_all = ("everyone deny read,write,append,readattr,writeattr,"
                "readextattr,writeextattr,readsecurity,writesecurity,chown,"
                "delete")
    dir_all = ("everyone deny list,search,add_file,add_subdirectory,"
               "delete_child,readattr,writeattr,readextattr,writeextattr,"
               "readsecurity,writesecurity,chown,delete")

    def plant_h14(wd, _wd_fd, problems):
        os.mkdir(os.path.join(wd, "da"), 0o700)
        _write(os.path.join(wd, "da", "x"), "h14")
        os.mkdir(os.path.join(wd, "db"), 0o700)
        _write(os.path.join(wd, "db", "x"), "h14")
        _write(os.path.join(wd, "fa"), "h14")
        _write(os.path.join(wd, "fb"), "h14")
        aces = (("fa", "everyone deny delete"),
                ("da", "everyone deny delete"),
                ("da", "everyone deny list"),
                ("fb", file_all), ("db", dir_all))
        for name, ace in aces:
            proc = subprocess.run(["/bin/chmod", "+a", ace,
                                   os.path.join(wd, name)],
                                  capture_output=True, text=True, timeout=30,
                                  stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                problems.append("fixture: chmod +a %r on %s: %s"
                                % (ace, name, proc.stderr.strip()[:120]))
        # The narrow ACEs are visible to ls -le; the broad ones deny
        # readsecurity, so they are proved by their effect instead -- a
        # non-destructive read / list the ACE must refuse.
        for name in ("fa", "da"):
            proc = subprocess.run(["/bin/ls", "-led", os.path.join(wd, name)],
                                  capture_output=True, text=True, timeout=30,
                                  stdin=subprocess.DEVNULL)
            if "deny" not in proc.stdout:
                problems.append("fixture: %s carries no deny ACE" % name)
        for name, probe in (("fb", lambda p: os.close(os.open(p,
                                                               os.O_RDONLY))),
                            ("db", os.listdir)):
            try:
                probe(os.path.join(wd, name))
            except PermissionError:
                continue
            problems.append("fixture: the broad ACE on %s does not deny its "
                            "owner" % name)
        return ["aces        : fa/da deny delete (+ da deny list), fb/db the "
                "broad item-3l ACE"]

    def acl_row():
        _h_mod()
        if sys.platform != "darwin":
            raise _Info("deny ACEs are a darwin vector: a POSIX ACL cannot "
                        "deny the owner (plan KD-18 step 5)")
        return hostile_row("h14", plant_h14)()
    _guard(suite, GRP_H, "H14 acl-deny-delete-file-and-dir-removed", acl_row)

    def quarantined_serves():
        mod = _runner_mod()
        problems = []
        _cfg, root, state = _live_runner(mod, fx, "h15")
        real_remove = mod.remove_tree
        stuck = []

        def stub(root_fd, name, abs_root, deadline):
            if not stuck:
                stuck.append(name)
            if name == stuck[0]:
                return False
            return real_remove(root_fd, name, abs_root, deadline)

        saved_state, saved_rm = [], []
        try:
            _patch(mod, saved_state, "_STATE", state)
            state.plan_all(time.monotonic() + mod._STARTUP_PLANS_DEADLINE_S)
            _patch(mod, saved_rm, "remove_tree", stub)
            for tag in ("h15-one", "h15-two"):
                res, exc, alive = _in_thread(
                    lambda: mod.run_one(_bash_req(mod, tag)), RUN_TIMEOUT)
                _run_ok(res, exc, alive, tag, tag, problems)
                if alive:
                    break
                if root.entries() != stuck[:1] or not stuck or \
                        not _HEX32.match(stuck[0]):
                    problems.append("%s: quarantine/ holds %s, want the one "
                                    "workdir %s" % (tag, root.entries(),
                                                    stuck[:1]))
                cap = _capabilities(mod, tag, problems)
                q = cap.get("quarantine") or {}
                if q.get("count") != 1 or cap.get("degraded") is not None:
                    problems.append("%s: capabilities quarantine %r degraded "
                                    "%r, want count 1, degraded null"
                                    % (tag, q, cap.get("degraded")))
            _restore(mod, saved_rm)
            count = mod.sweep_work_root(
                root.root_fd, root.q_fd,
                time.monotonic() + mod._STARTUP_SWEEP_DEADLINE_S)
            if count != 0:
                problems.append("the sweep after the stub was removed "
                                "returned %r, want 0" % (count,))
            problems.extend(root.residue())
        finally:
            _restore(mod, saved_rm)
            _restore(mod, saved_state)
            root.close()
            root.scrub()
        return problems, ["stuck       : %s" % [s[:8] for s in stuck]]
    _guard(suite, GRP_H, "H15 unremovable-workdir-quarantined-runner-serves",
           quarantined_serves)

    def over_cap():
        mod = _runner_mod()
        problems = []
        _cfg, root, state = _live_runner(mod, fx, "h16")
        failed = []

        def stub(root_fd, name, abs_root, deadline):
            failed.append(name)
            return False

        saved_state, saved_rm = [], []
        lines = []
        try:
            _patch(mod, saved_state, "_STATE", state)
            state.plan_all(time.monotonic() + mod._STARTUP_PLANS_DEADLINE_S)
            _patch(mod, saved_rm, "remove_tree", stub)
            for i in range(1, QUARANTINE_MAX + 2):
                tag = "h16-run%d" % i
                res, exc, alive = _in_thread(
                    lambda: mod.run_one(_bash_req(mod, tag)), RUN_TIMEOUT)
                if not _run_ok(res, exc, alive, tag, tag, problems):
                    break
                want = None if i <= QUARANTINE_MAX else "quarantine full"
                if getattr(state, "degraded", _MISSING) != want:
                    problems.append("after %d quarantined workdirs: degraded "
                                    "%r, want %r" % (
                                        i, getattr(state, "degraded", None),
                                        want))
            lines.append("quarantined : %d" % len(root.entries()))
            cap = _capabilities(mod, "degraded", problems)
            q = cap.get("quarantine") or {}
            if cap.get("degraded") != "quarantine full" or \
                    q.get("count") != QUARANTINE_MAX + 1:
                problems.append("degraded capabilities: quarantine %r "
                                "degraded %r, want count %d, 'quarantine "
                                "full'" % (q, cap.get("degraded"),
                                           QUARANTINE_MAX + 1))
            res, exc, alive = _in_thread(
                lambda: mod.run_one(_bash_req(mod, "h16-refused")),
                RUN_TIMEOUT)
            if alive or exc is not None or \
                    _refusal_code(res) != "sandbox_unavailable":
                problems.append("a run while degraded: %r, want "
                                "sandbox_unavailable"
                                % (exc if exc is not None else res,))
            _restore(mod, saved_rm)
            res, exc, alive = _in_thread(
                lambda: mod.run_one(_bash_req(mod, "h16-back")), RUN_TIMEOUT)
            _run_ok(res, exc, alive, "h16-back", "the run after the stub was "
                    "removed", problems)
            cap = _capabilities(mod, "recovered", problems)
            if cap.get("degraded") is not None or \
                    (cap.get("quarantine") or {}).get("count") != 0:
                problems.append("recovered capabilities: quarantine %r "
                                "degraded %r, want count 0, null"
                                % (cap.get("quarantine"),
                                   cap.get("degraded")))
            problems.extend(root.residue())
        finally:
            _restore(mod, saved_rm)
            _restore(mod, saved_state)
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H16 quarantine-over-cap-degraded", over_cap)

    def sweep_bounded():
        mod = _h_mod()
        problems = []
        lines = []
        if mod._STARTUP_SWEEP_DEADLINE_S != STARTUP_SWEEP_S:
            problems.append("_STARTUP_SWEEP_DEADLINE_S %r, want %r"
                            % (mod._STARTUP_SWEEP_DEADLINE_S,
                               STARTUP_SWEEP_S))
        if not (mod._STARTUP_SWEEP_DEADLINE_S
                + mod._STARTUP_PLANS_DEADLINE_S < STARTUP_TIMEOUT):
            problems.append("startup bound %r + %r is not < %r" % (
                mod._STARTUP_SWEEP_DEADLINE_S, mod._STARTUP_PLANS_DEADLINE_S,
                STARTUP_TIMEOUT))
        root = _Root(mod, fx.work_root("h17"))
        deadlines = []

        def slow(root_fd, name, abs_root, deadline):
            deadlines.append(deadline)
            time.sleep(H17_STUB_S)
            return False

        saved = []
        try:
            planted = _plant(root.quarantine, H17_PLANTED, "h17")
            _patch(mod, saved, "_STARTUP_SWEEP_DEADLINE_S", H17_SWEEP_S)
            _patch(mod, saved, "remove_tree", slow)
            started = time.monotonic()
            deadline = started + mod._STARTUP_SWEEP_DEADLINE_S
            count = mod.sweep_work_root(root.root_fd, root.q_fd, deadline)
            took = time.monotonic() - started
            _restore(mod, saved)
            bound = H17_SWEEP_S + H17_STUB_S + H_SLACK_S
            lines += ["sweep       : %.3fs (bound %.1fs)" % (took, bound),
                      "stub calls  : %d" % len(deadlines)]
            if took > bound:
                problems.append("sweep took %.3fs, bound %.1fs" % (took,
                                                                   bound))
            if count != H17_PLANTED:
                problems.append("sweep returned %r, want %d (every planted "
                                "entry counted)" % (count, H17_PLANTED))
            if root.entries() != sorted(planted):
                problems.append("quarantine/ holds %d entries, want the %d "
                                "planted" % (len(root.entries()),
                                             H17_PLANTED))
            if not deadlines:
                problems.append("remove_tree was never tried: the sweep did "
                                "not retry the quarantine")
            elif len(deadlines) > int(H17_SWEEP_S / H17_STUB_S) + 1:
                problems.append("%d remove_tree calls in a %.1fs deadline of "
                                "%.1fs calls" % (len(deadlines), H17_SWEEP_S,
                                                 H17_STUB_S))
            if any(d != deadline for d in deadlines):
                problems.append("remove_tree got deadlines %s, want the ONE "
                                "shared %.3f" % (sorted(set(
                                    round(d - started, 3)
                                    for d in deadlines)), H17_SWEEP_S))
            for name in planted:
                shutil.rmtree(os.path.join(root.quarantine, name),
                              ignore_errors=True)
            problems.extend(root.residue())
        finally:
            _restore(mod, saved)
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H17 startup-sweep-bounded", sweep_bounded)

    def degraded_retry():
        mod = _runner_mod()
        problems = []
        lines = []
        if mod._DEGRADED_RETRY_S != DEGRADED_RETRY_S:
            problems.append("_DEGRADED_RETRY_S %r, want %r"
                            % (mod._DEGRADED_RETRY_S, DEGRADED_RETRY_S))
        if _kd17_sum(mod) > mod._RUN_DEADLINE_S or mod._RUN_DEADLINE_S > 175:
            problems.append("KD-17: %.1f s is not <= %r <= 175"
                            % (_kd17_sum(mod), mod._RUN_DEADLINE_S))
        cfg = _h_cfg(mod, fx, "h18")
        root = _Root(mod, cfg.work_root)
        deadlines = []
        spawned = []

        def slow(root_fd, name, abs_root, deadline):
            deadlines.append(deadline)
            time.sleep(H18_STUB_S)
            return False

        def no_spawn(*_a, **_k):
            spawned.append(1)
            raise RuntimeError("H18: a degraded run must spawn nothing")

        saved = []
        try:
            planted = _plant(root.quarantine, QUARANTINE_MAX + 1, "h18")
            state = mod.RunnerState(cfg, root.root_fd, root.lock_fd,
                                    root.q_fd)
            if getattr(state, "degraded", None) != "quarantine full":
                problems.append("RunnerState over %d planted entries: "
                                "degraded %r, want 'quarantine full'"
                                % (len(planted),
                                   getattr(state, "degraded", None)))
            _patch(mod, saved, "_STATE", state)
            _patch(mod, saved, "remove_tree", slow)
            _patch(mod, saved, "_spawn_sbx", no_spawn)
            _patch(mod, saved, "_run_child", no_spawn)
            req = mod.RunRequest(files=(("main.py", b"print(18)\n", False),),
                                 entry="main.py", interpreter="python3",
                                 args=(), stdin=None, timeout_ms=10000)
            started = time.monotonic()
            res, exc, alive = _in_thread(lambda: mod.run_one(req),
                                         RUN_TIMEOUT)
            took = time.monotonic() - started
            _restore(mod, saved)
            bound = mod._DEGRADED_RETRY_S + H_SLACK_S
            lines += ["refused in  : %.3fs (bound %.2fs)" % (took, bound),
                      "stub calls  : %d" % len(deadlines)]
            if alive or exc is not None or \
                    _refusal_code(res) != "sandbox_unavailable":
                problems.append("degraded run: %r, want sandbox_unavailable"
                                % ("still running" if alive else
                                   exc if exc is not None else res,))
            if took > bound:
                problems.append("the degraded run took %.3fs, bound %.2fs"
                                % (took, bound))
            if spawned:
                problems.append("the degraded run spawned %d process(es)"
                                % len(spawned))
            if not deadlines:
                problems.append("remove_tree was never tried: no pre-run "
                                "quarantine retry")
            elif len(set(deadlines)) != 1 or \
                    deadlines[0] > started + DEGRADED_RETRY_S + 0.05:
                problems.append("pre-run retry deadlines %s, want ONE shared "
                                "deadline <= start + %.1fs" % (sorted(set(
                                    round(d - started, 3)
                                    for d in deadlines)), DEGRADED_RETRY_S))
            if len(root.entries()) != QUARANTINE_MAX + 1:
                problems.append("quarantine/ holds %d entries, want %d"
                                % (len(root.entries()), QUARANTINE_MAX + 1))
            for name in planted:
                shutil.rmtree(os.path.join(root.quarantine, name),
                              ignore_errors=True)
            problems.extend(root.residue())
        finally:
            _restore(mod, saved)
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H18 degraded-retry-within-run-bound",
           degraded_retry)

    def flatten_child():
        mod = _h_mod()
        problems = []
        lines = []
        for const, want in (("_FLATTEN_DEPTH", FLATTEN_DEPTH),
                            ("_CLEANUP_DEADLINE_S", CLEANUP_DEADLINE_S)):
            got = getattr(mod, const, None)
            if got != want:
                problems.append("%s %r, want %r" % (const, got, want))
        root = _Root(mod, fx.work_root("h19"))
        name = None
        try:
            name, wd_fd = mod.create_workdir(root.root_fd)
            try:
                lines.extend(plant_deep(H19_ROUNDS)(None, wd_fd, problems))
            finally:
                os.close(wd_fd)
            # The child takes the work-root lock itself: release ours.
            root.close()
            argv = [sys.executable, "-I", "-B", "-c", _H19_CHILD, SERVER,
                    H.TESTS_DIR, root.path, name, str(H19_NOFILE)]
            rc, out, err = H.run_process(argv, timeout=120, cwd=fx.base)
            report = None
            for line in reversed(out.strip().splitlines()):
                try:
                    report = json.loads(line)
                    break
                except ValueError:
                    continue
            if rc != 0 or not isinstance(report, dict):
                problems.append("child exit %r, stderr %r" % (
                    rc, err.strip()[-300:]))
            else:
                lines += ["child       : %.3fs, ok %r, soft NOFILE %r "
                          "(reference %.2fs)" % (report.get("wall_s", -1),
                                                 report.get("ok"),
                                                 report.get("nofile"),
                                                 H19_REFERENCE_S)]
                if report.get("nofile") != H19_NOFILE:
                    problems.append("fixture: the child's soft NOFILE is %r, "
                                    "want %d" % (report.get("nofile"),
                                                 H19_NOFILE))
                if report.get("ok") is not True:
                    problems.append("remove_workdir returned %r in the child"
                                    % (report.get("ret"),))
                if not report.get("wall_s", 1e9) < CLEANUP_DEADLINE_S:
                    problems.append("removal took %rs, not before the %.0fs "
                                    "deadline" % (report.get("wall_s"),
                                                  CLEANUP_DEADLINE_S))
            if os.path.lexists(os.path.join(root.path, name)):
                problems.append("the workdir still exists")
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub(deep=True)
        return problems, lines
    _guard(suite, GRP_H,
           "H19 flatten-removes-rename-chained-tree-within-deadline",
           flatten_child)

    # Validation round 1 (Q1, W2/W1, W6), written RED first: collect_out
    # sorted the whole out/ listing before its entry cap, the remover listed
    # a directory in one tick, the darwin fallbacks operated on spelled
    # paths, and cleanup ran with the run's survivors still alive.

    def out_listing_bounded():
        mod = _h_mod()
        problems = []
        cap = getattr(mod, "_OUT_MAX_ENTRIES", None)
        if not isinstance(cap, int) or cap <= 0:
            return ["_OUT_MAX_ENTRIES %r is not a positive int" % (cap,)], []
        cfg = _h_cfg(mod, fx, "h20")
        root = _Root(mod, cfg.work_root)
        proxy = _CountingOs()
        n = cap + 1000
        files, truncated = [], None
        try:
            name, wd_fd = mod.create_workdir(root.root_fd)
            try:
                out = os.path.join(root.path, name, "out")
                for i in range(n):
                    os.close(os.open(os.path.join(out, "f%05d" % i),
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o600))
                saved = []
                _patch(mod, saved, "os", proxy)
                try:
                    files, truncated = mod.collect_out(wd_fd, cfg.limits)
                finally:
                    _restore(mod, saved)
            finally:
                os.close(wd_fd)
            if truncated is not True:
                problems.append("truncated %r for %d entries over the %d cap, "
                                "want True" % (truncated, n, cap))
            if len(files) > cfg.limits.max_out_files:
                problems.append("%d out files over max_out_files %d"
                                % (len(files), cfg.limits.max_out_files))
            if proxy.yielded > cap + 1:
                problems.append("the out/ listing handed out %d entries for a "
                                "%d-entry bound: it is read whole before the "
                                "cap" % (proxy.yielded, cap))
            _remove_ok(mod, root, name, "workdir", problems)
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, ["listing     : %d of %d entries read, %d files"
                          % (proxy.yielded, n, len(files))]
    _guard(suite, GRP_H, "H20 out-listing-bounded-while-scanning",
           out_listing_bounded)

    def remover_batches():
        mod = _h_mod()
        problems = []
        lines = []
        batch = getattr(mod, "_RM_LIST_BATCH", None)
        if not isinstance(batch, int) or not 0 < batch <= RM_LIST_BATCH:
            problems.append("_RM_LIST_BATCH %r, want a positive int <= %d"
                            % (batch, RM_LIST_BATCH))
            batch = RM_LIST_BATCH
        root = _Root(mod, fx.work_root("h21"))
        n = 5000
        try:
            for label, pending in (("ticks", None), ("pending cap 700", 700)):
                name, wd_fd = mod.create_workdir(root.root_fd)
                try:
                    for i in range(n):
                        os.close(os.open("f%05d" % i, os.O_WRONLY | os.O_CREAT
                                         | os.O_EXCL, 0o600, dir_fd=wd_fd))
                finally:
                    os.close(wd_fd)
                proxy = _CountingOs()
                marks = []
                real_tick = mod._Remover.tick

                def tick(self, _real=real_tick, _proxy=proxy, _marks=marks):
                    _marks.append(_proxy.yielded)
                    return _real(self)
                saved, saved_cls = [], []
                _patch(mod, saved, "os", proxy)
                _patch(mod._Remover, saved_cls, "tick", tick)
                if pending is not None:
                    _patch(mod, saved, "_RM_PENDING_MAX", pending)
                try:
                    ok = mod.remove_workdir(
                        root.root_fd, name,
                        time.monotonic() + mod._CLEANUP_DEADLINE_S)
                finally:
                    _restore(mod._Remover, saved_cls)
                    _restore(mod, saved)
                marks.append(proxy.yielded)
                gap = max(b - a for a, b in zip(marks, marks[1:])) \
                    if len(marks) > 1 else proxy.yielded
                if ok is not True:
                    problems.append("%s: remove_workdir returned %r, want True"
                                    % (label, ok))
                if os.path.lexists(os.path.join(root.path, name)):
                    problems.append("%s: the workdir still exists" % label)
                if gap > batch:
                    problems.append("%s: %d directory entries read between two "
                                    "ticks (batch %d): a listing is not "
                                    "bounded by the deadline"
                                    % (label, gap, batch))
                lines.append("%-11s : %d entries, %d ticks, widest gap %d"
                             % (label, proxy.yielded, len(marks), gap))
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H21 remover-lists-in-ticked-batches", remover_batches)

    def fd_only_fallbacks():
        mod = _h_mod()
        if sys.platform != "darwin":
            raise _Info("uchg and deny ACEs are darwin vectors (the spelled-"
                        "path fallbacks were darwin-only)")
        problems = []
        lines = []
        if hasattr(mod, "_strip_acl_spelled"):
            problems.append("_strip_acl_spelled still exists: a spelled-path "
                            "ACL strip (chmodx_np / acl_set_file follow "
                            "symlinks)")
        root = _Root(mod, fx.work_root("h22"))
        calls = []
        try:
            name, wd_fd = mod.create_workdir(root.root_fd)
            wd = os.path.join(root.path, name)
            try:
                lines.extend(plant_h14(wd, wd_fd, problems) or ())
                lines.extend(plant_h12(wd, wd_fd, problems) or ())
                _write(os.path.join(wd, "u2"), "h22")
                os.chmod(os.path.join(wd, "u2"), 0)
                _uchg(os.path.join(wd, "u2"), problems)
            finally:
                os.close(wd_fd)
            ctypes_mod, libc = mod._libc()
            saved = []
            _patch(mod, saved, "_LIBC", (ctypes_mod, _LibcRecorder(libc,
                                                                   calls)))
            _patch(mod, saved, "os", _CountingOs(calls))
            try:
                # abs_root None: no spelling of the tree exists at all.
                ok = mod.remove_tree(root.root_fd, name, None,
                                     time.monotonic() + mod._CLEANUP_DEADLINE_S)
            finally:
                _restore(mod, saved)
            spelled = sorted(set(c for c in calls if c in _SPELLED_CALLS))
            if spelled:
                problems.append("the remover called %s: a spelled-path "
                                "operation" % spelled)
            if ok is not True:
                problems.append("remove_tree(abs_root=None) returned %r: an "
                                "ACL'd / uchg / 000 entry still needs a "
                                "spelled path" % (ok,))
            if os.path.lexists(wd):
                problems.append("the workdir still exists")
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, lines + ["libc        : %s"
                                  % sorted(set(calls) - {"lchflags", "chflags"})]
    _guard(suite, GRP_H, "H22 darwin-fallbacks-fd-relative-only",
           fd_only_fallbacks)

    def survivors_swept():
        mod = _h_mod(_STEP10_NAMES + ("_cleanup_workdir",))
        if sys.platform != "darwin":
            raise _Info("the cwd/session survivor sweep is darwin's (Linux: "
                        "bwrap --unshare-pid tears the pid namespace down)")
        problems = []
        lines = []
        root = _Root(mod, fx.work_root("h23"))
        procs = []
        sleeper = [sys.executable, "-I", "-S", "-c",
                   "import time; time.sleep(60)"]

        def spawn(cwd):
            proc = subprocess.Popen(sleeper, cwd=cwd, start_new_session=True,
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            procs.append(proc)
            return proc

        def dead(proc, label):
            try:
                proc.wait(3.0)
            except subprocess.TimeoutExpired:
                problems.append("%s outlived the cleanup" % label)

        try:
            state = types.SimpleNamespace(
                root_fd=root.root_fd, degraded=None,
                quarantine=mod.Quarantine(root.root_fd, root.q_fd))
            name, wd_fd = mod.create_workdir(root.root_fd)
            os.close(wd_fd)
            wd = os.path.join(root.path, name)
            in_wd = spawn(wd)
            # The run's session as cleanup meets it: the leader (the target,
            # sid == its pid) is gone; a member that setpgid()ed out of the
            # target's group and chdir()ed away survives it.
            leader = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", _H23_SESSION_MEMBER],
                cwd=fx.base, start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            out, _err = leader.communicate(timeout=10)
            member = int(out.decode().strip() or "0")
            sid = leader.pid
            if member <= 1:
                return ["fixture: no session member pid (%r)" % out], lines
            member_sid = os.getsid(member)
            if member_sid != sid:
                problems.append("fixture: the member's sid %r is not the dead "
                                "leader's %d" % (member_sid, sid))
            bystander = spawn(fx.base)
            time.sleep(0.2)
            try:
                mod._cleanup_workdir(state, name, sid=sid)
                dead(in_wd, "a survivor whose cwd is the workdir")
                gone = time.monotonic() + 3.0
                while time.monotonic() < gone:
                    try:
                        os.kill(member, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    problems.append("a survivor in the run's session (own "
                                    "group, cwd outside) outlived the cleanup")
            finally:
                try:
                    os.kill(member, signal.SIGKILL)
                except OSError:
                    pass
            if bystander.poll() is not None:
                problems.append("a bystander (own session, cwd outside) was "
                                "killed")
            if os.path.lexists(wd):
                problems.append("the workdir still exists")
            # The quarantine retry sweeps first, too.
            qname = os.urandom(16).hex()
            qdir = os.path.join(root.quarantine, qname)
            os.mkdir(qdir, 0o700)
            in_q = spawn(qdir)
            time.sleep(0.2)
            state.quarantine.retry(time.monotonic() + mod._CLEANUP_DEADLINE_S)
            dead(in_q, "a survivor whose cwd is a quarantined workdir")
            if bystander.poll() is not None:
                problems.append("the quarantine retry killed the bystander")
            problems.extend(root.residue())
            lines.append("survivors   : %d spawned" % len(procs))
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H23 cleanup-sweeps-survivors-before-removal",
           survivors_swept)

    # A9 prices every out/ path at OUT_PATH_MAX bytes (validation round 1):
    # collect_out keeps a path of exactly that length and skips a longer one.
    h24_kept = "/".join(["a" * 255, "b" * (OUT_PATH_MAX - 258), "x"])
    h24_long = "/".join(["c" * 255, "d" * (OUT_PATH_MAX - 257), "y"])

    def plant_h24(wd):
        for rel in (h24_kept, h24_long):
            path = os.path.join(wd, "out", rel)
            os.makedirs(os.path.dirname(path), 0o700)
            _write(path, "h24")
    _guard(suite, GRP_H, "H24 out-path-over-cap-skipped",
           out_row("h24", plant_h24, {h24_kept: ("h24", "utf8")}, True))

    def egress_stale_swept():
        """H25: egress/ is the runner's own entry beside quarantine/; what a
        SIGKILLed runner left in it (a per-run dir holding its socket name,
        an empty one) is removed by the startup sweep, which still starts."""
        mod = _h_mod()
        problems = []
        root = _Root(mod, fx.work_root("h25"))
        try:
            os.makedirs(root.egress, 0o700, exist_ok=True)
            stale = _plant(root.egress, 2, "h25")
            try:
                got = mod.sweep_work_root(
                    root.root_fd, root.q_fd,
                    time.monotonic() + mod._STARTUP_SWEEP_DEADLINE_S)
            except SystemExit as exc:
                problems.append("the sweep refused the start: SystemExit(%r)"
                                % (exc.code,))
            else:
                if got != 0:
                    problems.append("sweep returned %r, want 0" % (got,))
            problems.extend(root.residue())
        finally:
            root.close()
            root.scrub()
        return problems, ["stale       : %d egress/ entries planted" % len(stale)]
    _guard(suite, GRP_H, "H25 startup-sweeps-stale-egress", egress_stale_swept)

    def egress_planted_refused():
        """H26: prepare_work_root creates egress/ 0700 like quarantine/, and a
        non-directory or a symlink planted at its name refuses the start the
        way one at quarantine/ does -- nothing is followed or removed."""
        mod = _h_mod()
        problems = []
        lines = []
        outside = os.path.realpath(tempfile.mkdtemp(prefix="h26-outside-",
                                                    dir=fx.base))
        for label, plant in (
                ("a regular file", lambda p: _write(p, "h26")),
                ("a symlink to a directory", lambda p: os.symlink(outside, p))):
            path = fx.work_root("h26")
            target = os.path.join(path, "egress")
            plant(target)
            try:
                fds = mod.prepare_work_root(path)
            except SystemExit as exc:
                if exc.code in (0, None):
                    problems.append("%s: SystemExit(%r), want a refusal"
                                    % (label, exc.code))
                else:
                    lines.append("%s: SystemExit(%r)" % (label, exc.code))
            else:
                for fd in fds:
                    os.close(fd)
                problems.append("%s at egress/: prepare_work_root accepted it"
                                % label)
            if not os.path.lexists(target):
                problems.append("%s: the planted entry was removed" % label)
        if os.listdir(outside):
            problems.append("the symlink's target gained %s"
                            % sorted(os.listdir(outside))[:3])
        # Control: a clean root gets a 0700 egress/ of the euid.
        root = _Root(mod, fx.work_root("h26-ok"))
        try:
            st = os.lstat(root.egress) if os.path.lexists(root.egress) else None
            if st is None or not stat.S_ISDIR(st.st_mode) \
                    or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                problems.append("control: egress/ is %r, want a 0700 "
                                "directory of the euid"
                                % (None if st is None else oct(st.st_mode),))
        finally:
            root.close()
            root.scrub()
        return problems, lines
    _guard(suite, GRP_H, "H26 planted-egress-entry-refused",
           egress_planted_refused)


# H23's session: the leader forks one member that leaves the leader's group
# (setpgid) and the work tree (chdir /), prints its pid and exits; the member
# sleeps in the leader's session.
_H23_SESSION_MEMBER = r'''
import os, sys, time
pid = os.fork()
if pid == 0:
    os.setpgid(0, 0)
    os.chdir("/")
    os.close(1)
    time.sleep(60)
    os._exit(0)
sys.stdout.write("%d\n" % pid)
sys.stdout.flush()
os._exit(0)
'''


# H19's child: soft RLIMIT_NOFILE 256 BEFORE anything opens, then the server
# loaded in-process; it reports only its wall time and the return value.
_H19_CHILD = r'''
import json, os, resource, sys, time
sys.dont_write_bytecode = True
server, tests_dir, root, name, nofile = sys.argv[1:6]
nofile = int(nofile)
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if hard != resource.RLIM_INFINITY and hard < nofile:
    nofile = hard
resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, hard))
sys.path.insert(0, tests_dir)
import _harness as H
mod = H.load_module_from_path("mcp_sandbox_h19", server)
root_fd, lock_fd, quarantine_fd = mod.prepare_work_root(root)
start = time.monotonic()
ret = mod.remove_workdir(root_fd, name, start + mod._CLEANUP_DEADLINE_S)
wall = time.monotonic() - start
print(json.dumps({"ok": ret is True, "ret": repr(ret)[:40],
                  "wall_s": round(wall, 3),
                  "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0]}))
'''


# ---------------------------------------------------------------------------
# live runners over stdio (groups I and G)
# ---------------------------------------------------------------------------
# Every I and G row checks the Step 11 + Step 12 names FIRST (the stated RED
# reason: no runner is started while the engine / plan machinery is
# missing), then the P23 control BEFORE any runner starts: off macOS, or
# under a nested Seatbelt, the row is INFO (counted) -- the live rows are
# verified on macOS only (LIVE_VERIFIED linux false), as H15/H16 are. A start
# that still fails is INFO only when the control then reports a nested
# Seatbelt; anywhere else it is the row's failure.
#
# The result object these rows pin (plan Step 11 "Result"): the members
# _RESULT_MEMBERS, out files in group H's pinned shape (path, content,
# encoding), `signal` a name or null. Pinned here where the plan is silent:
# a python3 or bash hello leaves stderr EMPTY (the M0:5
# /private/var/select/sh read is what keeps bash quiet); the bash script's
# HOME / TMPDIR / OUT are <wd>, <wd>/tmp, <wd>/out; an sbx refusal's message
# is the fixed "sbx refused the run (exit N)" and the marker of sbx's one
# stderr line reaches the runner's log only (O2); a refused start exits
# non-zero and names the offending thing on stderr (G11 and G6: exit 1).

_RESULT_MEMBERS = frozenset({"exitCode", "signal", "timedOut", "durationMs",
                             "stdout", "stderr", "stdoutTruncated",
                             "stderrTruncated", "outFiles",
                             "outFilesTruncated"})
KILL_GRACE_S = 2.0          # KD-17 _KILL_GRACE_S
REFRESH_DEADLINE_S = 30.0   # KD-17 _REFRESH_DEADLINE_S
REFRESH_SUPPRESS_S = 5.0    # R-11: a failing signature is not retried in 5 s
SELF_TEST_NAME = "sbx-self-test"
STDOUT_CAP = 65536          # config.json.tmpl limits.stdout_bytes
AS_BUDGET = 1024 * MIB      # config.json.tmpl limits.as_budget_bytes
AS_HEADROOM = 256 * MIB     # plan Step 12: as_limit = max(vsz) + budget + 256 MiB
PS_WAIT_S = 30.0            # a target / blocked self-test must show up by then
NODE_EXTRA_READS = ("/usr/local/etc/openssl@3/openssl.cnf",
                    "/opt/homebrew/etc/openssl@3/openssl.cnf")


def _live_mod():
    """The server held to the Step 11 engine and the Step 12 plan
    machinery; their absence is the stated RED reason (the runner would
    start, but has no startup planning and no engine to run anything)."""
    mod = _mod()
    missing = [n for n in _STEP11_NAMES + _STEP12_NAMES
               if not hasattr(mod, n)]
    if missing:
        raise _Red("Scripts/mcp-sandbox.py has no %s (runner startup "
                   "planning, Step 12, / execution engine, Step 11, not "
                   "implemented)" % ", ".join(missing))
    return mod


def _live_gate(fx):
    """P23 BEFORE a runner starts: INFO off macOS and under a nested
    Seatbelt."""
    if sys.platform != "darwin":
        raise _Info("live runner rows are verified on macOS only "
                    "(LIVE_VERIFIED linux false; platform %s)" % sys.platform)
    nested = _nested_seatbelt(fx)
    if nested:
        raise _Info(nested)


def _bash_only(path="/bin/bash"):
    return {"bash": {"path": path}}


def _live_interpreters():
    """python3 and /bin/bash always; node when present, with the measured
    openssl.cnf extra read (M0:6) when that file exists."""
    out = _interpreters(full=False)
    out["bash"] = {"path": "/bin/bash"}
    node = _which("node")
    if node:
        entry = {"path": node}
        extra = [p for p in NODE_EXTRA_READS if os.path.isfile(p)]
        if extra:
            entry["extra_reads"] = extra[:1]
        out["node"] = entry
    return out


class _Runner:
    """A started, initialized runner over stdio on its own rendered config.
    `work_root` is the rendered config's, read back from the file; `env`
    adds keys to the runner's OWN environment (group E's canaries)."""

    def __init__(self, fx, label, interpreters=None, sbx=SBX, mutate=None,
                 env=None):
        self.fx = fx
        self.config = fx.render(label, sbx=sbx,
                                interpreters=interpreters or _bash_only(),
                                mutate=mutate)
        with open(self.config, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        self.work_root = obj["work_root"]
        self.interpreters = obj["interpreters"]
        started = time.monotonic()
        self.wire = _Wire(self.config, env=env)
        try:
            self.wire.initialize()
        except (_WireError, OSError):
            tail = self.wire.stderr_tail()
            self.wire.close()
            nested = _nested_seatbelt(fx)
            if nested:
                raise _Info(nested)
            raise _WireError("runner did not start: %s" % tail)
        self.startup_s = time.monotonic() - started

    def call(self, arguments, timeout=RUN_TIMEOUT):
        return _result(self.wire.call(arguments, timeout))

    def run(self, params, timeout=RUN_TIMEOUT):
        return self.call({"function": "run_script", "params": params},
                         timeout)

    def run_async(self, params):
        return self.wire.call_async({"function": "run_script",
                                     "params": params})

    def caps(self, label, problems):
        res = self.call({"function": "capabilities"}, RPC_TIMEOUT)
        if res.get("isError"):
            problems.append("%s: capabilities answered isError %r"
                            % (label, _refusal(res)))
            return {}
        return _payload(res) or {}

    def cancel(self, msg_id, reason="test"):
        self.wire.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                        "params": {"requestId": msg_id, "reason": reason}})

    def close(self):
        self.wire.close()


def _py(script, **extra):
    out = {"files": [{"path": "main.py", "content": script}],
           "entry": "main.py", "interpreter": "python3"}
    out.update(extra)
    return out


def _sh(script, entry="main.sh", **extra):
    out = {"files": [{"path": entry, "content": script}], "entry": entry,
           "interpreter": "bash"}
    out.update(extra)
    return out


def _body(res, label, problems):
    """The normal result object of a run, its members checked; None (with
    the problem recorded) for a refusal or a malformed result."""
    if res.get("isError"):
        problems.append("%s: isError, refusal %r" % (label, _refusal(res)))
        return None
    body = _payload(res)
    if not isinstance(body, dict):
        problems.append("%s: result is not a JSON object: %r"
                        % (label, _text(res)[:160]))
        return None
    missing = sorted(_RESULT_MEMBERS - set(body))
    if missing:
        problems.append("%s: result lacks %s" % (label, missing))
    return body


def _want(body, label, problems, **expect):
    """Each `expect` member of `body` equals its value."""
    if body is None:
        return False
    ok = True
    for key, value in expect.items():
        if body.get(key) != value:
            problems.append("%s: %s %r, want %r"
                            % (label, key, _short(body.get(key)),
                               _short(value)))
            ok = False
    return ok


def _short(value):
    if isinstance(value, str) and len(value) > 120:
        return value[:60] + "...(%d chars)" % len(value)
    return value


def _hello_ok(body, label, stdout, problems, quiet=True):
    if body is None:
        return False
    expect = dict(exitCode=0, signal=None, timedOut=False, stdout=stdout,
                  stdoutTruncated=False, stderrTruncated=False, outFiles=[],
                  outFilesTruncated=False)
    if quiet:
        expect["stderr"] = ""
    ok = _want(body, label, problems, **expect)
    dur = body.get("durationMs")
    if type(dur) is not int or dur < 0:
        problems.append("%s: durationMs %r, want a non-negative int"
                        % (label, dur))
        ok = False
    return ok


def _wr_residue(path, wait=0.0):
    """Problems unless the work root holds only .lock, an empty egress/ and
    an empty quarantine/, polled up to `wait` seconds."""
    deadline = time.monotonic() + wait
    while True:
        out = []
        try:
            top = sorted(os.listdir(path))
            left = sorted(os.listdir(os.path.join(path, "quarantine")))
            stale = sorted(os.listdir(os.path.join(path, "egress")))
        except OSError as exc:
            return ["work root unreadable: %s" % type(exc).__name__]
        if top != [".lock", "egress", "quarantine"]:
            out.append("work root holds %s, want ['.lock', 'egress', "
                       "'quarantine']" % top[:6])
        if left:
            out.append("quarantine/ holds %s" % left[:3])
        if stale:
            out.append("egress/ holds %s" % stale[:3])
        if not out or time.monotonic() >= deadline:
            return out
        time.sleep(0.1)


def _workdirs(path):
    try:
        return sorted(n for n in os.listdir(path) if _HEX32.match(n))
    except OSError:
        return []


def _ps():
    """[(pid, pgid, command)] of every process, or None when ps failed."""
    try:
        proc = subprocess.run(["/bin/ps", "-axww", "-o", "pid=,pgid=,command="],
                              capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            out.append((int(parts[0]), int(parts[1]), parts[2]))
    return out


def _ps_matching(*needles):
    return [p for p in (_ps() or []) if p[0] != os.getpid()
            and all(n in p[2] for n in needles)]


def _wait_ps(needles, timeout=PS_WAIT_S):
    """The processes whose command carries every needle, once at least one
    shows up within `timeout`; [] when none did."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = _ps_matching(*needles)
        if found:
            return found
        time.sleep(0.1)
    return []


def _wait_groups_gone(pgids, timeout):
    """Seconds until no process is left in any of `pgids`, or None."""
    started = time.monotonic()
    while True:
        live = [p for p in (_ps() or []) if p[1] in pgids]
        if not live:
            return time.monotonic() - started
        if time.monotonic() - started > timeout:
            return None
        time.sleep(0.05)


def _atomic_symlink(target, link):
    """Re-point `link` atomically: a new symlink under a temp name, then
    os.replace -- a new st_ino even when `target` is unchanged."""
    tmp = "%s.tmp-%s" % (link, os.urandom(4).hex())
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _render_fake_sbx(fx, label):
    """tests/files/mcp_sandbox/fake_sbx.py.tmpl rendered (the two tokens
    replaced with repr() of absolute paths) mode 0700; (path, trigger dir)."""
    trigger = os.path.realpath(tempfile.mkdtemp(
        prefix=fx._name(label) + "-trigger-", dir=fx.base))
    with open(FAKE_SBX_TEMPLATE, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("@REAL_SBX@", repr(SBX))
    text = text.replace("@TRIGGER_DIR@", repr(trigger))
    path = os.path.join(fx.base, fx._name(label) + "-fake_sbx")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o700)
    return path, trigger


def _write_exec(path, text, mode=0o755):
    """Write `text` to `path` through a temp name + os.replace (atomic)."""
    tmp = "%s.tmp-%s" % (path, os.urandom(4).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


FAKE_SBX_TEMPLATE = H.repo_path("tests", "files", "mcp_sandbox",
                                "fake_sbx.py.tmpl")


# ---------------------------------------------------------------------------
# I: live runs (stdio; Step 11, GREEN in Step 12)
# ---------------------------------------------------------------------------

def _run_i(suite, fx):
    shared = {}

    def runner():
        """One runner for I1-I9 (python3, bash, node when present)."""
        if "e" in shared:
            raise shared["e"]
        if "r" not in shared:
            try:
                shared["r"] = _Runner(fx, "i", interpreters=_live_interpreters())
            except BaseException as exc:      # noqa: BLE001 -- every row
                shared["e"] = exc
                raise
        return shared["r"]

    def live(fn):
        def body():
            _live_mod()
            _live_gate(fx)
            r = runner()
            problems, lines = fn(r)
            problems = list(problems) + _wr_residue(r.work_root, wait=2.0)
            return problems, lines
        return body

    try:
        _run_i_rows(suite, fx, live)
        _run_i_no_egress(suite, fx, live)
    finally:
        if "r" in shared:
            shared["r"].close()
    _run_i_egress(suite, fx)


# The live egress rows (I13-I16): one runner whose config carries a network
# allowlist naming ONE stable host, used only here. Linux runs them too when
# bwrap is present (the egress path is per platform); INFO otherwise.
I_EGRESS_HOST = "example.com"
I_EGRESS_OTHER = "www.iana.org"
I_EGRESS_MARK = b"Example Domain"


def _egress_gate(fx):
    if sys.platform == "darwin":
        nested = _nested_seatbelt(fx)
        if nested:
            raise _Info(nested)
        return
    if sys.platform.startswith("linux"):
        if not shutil.which("bwrap"):
            raise _Info("bwrap is not on PATH")
        return
    raise _Info("no egress endpoint for platform %s" % sys.platform)


def _host_reaches(host, port=443, timeout=5.0):
    """The positive control of the live egress rows: this host itself can
    resolve and reach host:port; None, or why not."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return "%s:%d unreachable from the host itself (%s)" % (
            host, port, exc)
    sock.close()
    return None


def _ca_reads(python):
    """The read grants `python`'s ssl module needs for its default trust
    store: the directory of the default CA file (spelled) and of its
    realpath, plus the CA directory, when they exist."""
    try:
        proc = subprocess.run(
            [python, "-I", "-S", "-c",
             "import json, ssl; p = ssl.get_default_verify_paths(); "
             "print(json.dumps([p.cafile, p.capath, p.openssl_cafile, "
             "p.openssl_capath]))"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL, env={})
        paths = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    out = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        for cand in ((os.path.dirname(path), os.path.dirname(
                os.path.realpath(path))) if os.path.isfile(path)
                     else (path, os.path.realpath(path))):
            if cand not in out and cand not in ("/", "/etc", "/usr"):
                out.append(cand)
    return out


def _egress_interpreters():
    py = _interpreters(full=False)["python3"]
    reads = _ca_reads(py["path"])
    if reads:
        py = dict(py, extra_reads=reads)
    return {"python3": py}


def _run_i_egress(suite, fx):
    shared = {}

    def runner():
        if "e" in shared:
            raise shared["e"]
        if "r" not in shared:
            try:
                shared["r"] = _Runner(
                    fx, "i-egress", interpreters=_egress_interpreters(),
                    mutate=_set_network({"allow": [I_EGRESS_HOST],
                                         "ports": [443]}))
            except BaseException as exc:      # noqa: BLE001 -- every row
                shared["e"] = exc
                raise
        return shared["r"]

    def live(fn, needs_net=False):
        def body():
            _live_mod()
            _egress_mod()
            _egress_gate(fx)
            if needs_net:
                why = _host_reaches(I_EGRESS_HOST)
                if why:
                    raise _Info(why)
            r = runner()
            problems, lines = fn(r)
            problems = list(problems) + _wr_residue(r.work_root, wait=2.0)
            return problems, lines
        return body

    def egress_of(body, label, problems):
        got = (body or {}).get("egress")
        if not isinstance(got, dict) or set(got) != {"tunnels", "refused"}:
            problems.append("%s: egress summary %r, want {tunnels, refused}"
                            % (label, got))
            return {}
        return got

    def allowed_https(r):
        problems = []
        caps = r.caps("capabilities", problems)
        net = caps.get("network")
        if net != {"enabled": True, "allow": [I_EGRESS_HOST], "ports": [443]}:
            problems.append("capabilities.network %r" % (net,))
        script = (
            "import json, os, urllib.request\n"
            "out = {'proxy': os.environ.get('HTTPS_PROXY'),\n"
            "       'no_proxy': os.environ.get('NO_PROXY')}\n"
            "try:\n"
            "    with urllib.request.urlopen('https://%s/', timeout=30) as r:\n"
            "        body = r.read(400000)\n"
            "        out['status'] = r.status\n"
            "        out['mark'] = %r in body\n"
            "except Exception as e:\n"
            "    out['error'] = '%%s: %%s' %% (type(e).__name__, str(e)[:200])\n"
            "print(json.dumps(out))\n" % (I_EGRESS_HOST, I_EGRESS_MARK))
        body = _body(r.run(_py(script)), "allowed https", problems)
        got = _json_line((body or {}).get("stdout", "")) or {}
        if got.get("status") != 200 or got.get("mark") is not True:
            problems.append("urllib to https://%s/: %r (stderr %r)"
                            % (I_EGRESS_HOST, got, _short(
                                (body or {}).get("stderr"))))
        proxy = got.get("proxy") or ""
        if not proxy.startswith("http://127.0.0.1:") or got.get("no_proxy") \
                != "":
            problems.append("HTTPS_PROXY %r / NO_PROXY %r"
                            % (proxy, got.get("no_proxy")))
        eg = egress_of(body, "allowed https", problems)
        if eg and (eg.get("tunnels", 0) < 1 or eg.get("refused") != 0):
            problems.append("egress summary %r, want tunnels >= 1, refused 0"
                            % (eg,))
        return problems, ["result      : %r" % (got,),
                          "egress      : %r" % ((body or {}).get("egress"),)]
    _guard(suite, GRP_I, "I13 egress-allowed-host-https-urllib",
           live(allowed_https, needs_net=True))

    def not_allowlisted(r):
        problems = []
        script = (
            "import json, urllib.request\n"
            "out = {}\n"
            "for key, url in (('other', 'https://%s/'),\n"
            "                 ('port', 'https://%s:8443/'),\n"
            "                 ('plain', 'http://%s/')):\n"
            "    try:\n"
            "        urllib.request.urlopen(url, timeout=20).close()\n"
            "        out[key] = 'fetched'\n"
            "    except Exception as e:\n"
            "        out[key] = '%%s: %%s' %% (type(e).__name__, str(e)[:120])\n"
            "print(json.dumps(out))\n"
            % (I_EGRESS_OTHER, I_EGRESS_HOST, I_EGRESS_HOST))
        body = _body(r.run(_py(script)), "not allowlisted", problems)
        got = _json_line((body or {}).get("stdout", "")) or {}
        for key, needle in (("other", "403"), ("port", "403"),
                            ("plain", "405")):
            if needle not in str(got.get(key)):
                problems.append("%s: %r, want the proxy's %s"
                                % (key, got.get(key), needle))
        eg = egress_of(body, "not allowlisted", problems)
        if eg and (eg.get("refused", 0) < 3 or eg.get("tunnels") != 0):
            problems.append("egress summary %r, want refused >= 3, tunnels 0"
                            % (eg,))
        return problems, ["result      : %r" % (got,)]
    _guard(suite, GRP_I, "I14 egress-not-allowlisted-refused",
           live(not_allowlisted))

    def direct(r):
        problems = []
        ls = socket.socket()
        ls.bind(("127.0.0.1", 0))
        ls.listen(4)
        other = ls.getsockname()[1]
        try:
            script = (
                "import json, os, socket, sys\n"
                "def tcp(host, port):\n"
                "    s = socket.socket(); s.settimeout(3)\n"
                "    try:\n"
                "        s.connect((host, port)); return 'connected'\n"
                "    except OSError as e:\n"
                "        return 'denied:%s' % (e.errno,)\n"
                "    finally:\n"
                "        s.close()\n"
                "proxy = os.environ['HTTPS_PROXY'].rsplit(':', 1)\n"
                "print(json.dumps({'proxy': tcp('127.0.0.1', int(proxy[1])),\n"
                "                  'other': tcp('127.0.0.1', int(sys.argv[1])),\n"
                "                  'outside': tcp('1.1.1.1', 443),\n"
                "                  'outside80': tcp('8.8.8.8', 80)}))\n")
            body = _body(r.run(_py(script, args=[str(other)])), "direct",
                         problems)
        finally:
            ls.close()
        got = _json_line((body or {}).get("stdout", "")) or {}
        if got.get("proxy") != "connected":
            problems.append("control: the proxy endpoint itself %r"
                            % (got.get("proxy"),))
        for key in ("other", "outside", "outside80"):
            if not str(got.get(key, "")).startswith("denied"):
                problems.append("%s: %r, want denied" % (key, got.get(key)))
        return problems, ["result      : %r" % (got,)]
    _guard(suite, GRP_I, "I15 egress-direct-sockets-denied", live(direct))

    def dns(r):
        problems = []
        try:
            socket.getaddrinfo(I_EGRESS_HOST, 443)
        except OSError as exc:
            raise _Info("the host itself cannot resolve %s (%s)"
                        % (I_EGRESS_HOST, exc))
        script = (
            "import json, socket\n"
            "out = {}\n"
            "for name in (%r, %r):\n"
            "    try:\n"
            "        socket.getaddrinfo(name, 443); out[name] = 'resolved'\n"
            "    except OSError as e:\n"
            "        out[name] = 'failed'\n"
            "u = socket.socket(socket.AF_UNIX)\n"
            "try:\n"
            "    u.connect('/var/run/mDNSResponder'); out['mdns'] = 'connected'\n"
            "except OSError:\n"
            "    out['mdns'] = 'failed'\n"
            "print(json.dumps(out))\n" % (I_EGRESS_HOST, I_EGRESS_OTHER))
        body = _body(r.run(_py(script)), "dns", problems)
        got = _json_line((body or {}).get("stdout", "")) or {}
        for key in (I_EGRESS_HOST, I_EGRESS_OTHER, "mdns"):
            if got.get(key) != "failed":
                problems.append("%s: %r, want failed" % (key, got.get(key)))
        return problems, ["result      : %r" % (got,)]
    _guard(suite, GRP_I, "I16 egress-dns-denied-inside", live(dns))

    if "r" in shared:
        shared["r"].close()


def _run_i_no_egress(suite, fx, live):
    def absent(r):
        """(e): a config without "network" is today's runner -- capabilities
        says disabled, a result carries no egress member, the script sees no
        proxy variable and reaches no loopback port."""
        problems = []
        caps = r.caps("capabilities", problems)
        if caps.get("network") != {"enabled": False, "allow": [],
                                   "ports": []}:
            problems.append("capabilities.network %r, want disabled"
                            % (caps.get("network"),))
        ls = socket.socket()
        ls.bind(("127.0.0.1", 0))
        ls.listen(4)
        try:
            script = (
                "import json, os, socket, sys\n"
                "s = socket.socket(); s.settimeout(3)\n"
                "try:\n"
                "    s.connect(('127.0.0.1', int(sys.argv[1]))); got = 'connected'\n"
                "except OSError as e:\n"
                "    got = 'denied:%s' % (e.errno,)\n"
                "print(json.dumps({'loopback': got, 'proxy_env': sorted(\n"
                "    k for k in os.environ if k.lower().endswith('_proxy'))}))\n")
            body = _body(r.run(_py(script, args=[str(ls.getsockname()[1])])),
                         "no network", problems)
        finally:
            ls.close()
        got = _json_line((body or {}).get("stdout", "")) or {}
        if not str(got.get("loopback", "")).startswith("denied"):
            problems.append("loopback %r, want denied" % (got.get("loopback"),))
        if got.get("proxy_env") != []:
            problems.append("proxy variables %r, want none"
                            % (got.get("proxy_env"),))
        if body is not None and "egress" in body:
            problems.append("a run without network carries egress %r"
                            % (body.get("egress"),))
        return problems, ["result      : %r" % (got,)]
    _guard(suite, GRP_I, "I17 no-network-config-unchanged", live(absent))


def _run_i_rows(suite, fx, live):
    def hello_python(r):
        problems = []
        body = _body(r.run(_py('print("hello-i1")\n')), "python3", problems)
        _hello_ok(body, "python3", "hello-i1\n", problems)
        return problems, ["startup     : %.2fs (python3, bash%s)"
                          % (r.startup_s, ", node" if "node" in
                             r.interpreters else "")]
    _guard(suite, GRP_I, "I1 hello-python", live(hello_python))

    def hello_bash(r):
        problems = []
        script = ('echo hello-i2\necho "$HOME" | cat\necho "$TMPDIR"\n'
                  'echo "$OUT"\n')
        body = _body(r.run(_sh(script)), "bash", problems)
        if body is None:
            return problems, []
        lines = str(body.get("stdout", "")).split("\n")
        _hello_ok(body, "bash", body.get("stdout"), problems)
        if len(lines) != 5 or lines[0] != "hello-i2" or lines[4] != "":
            problems.append("bash stdout %r, want hello-i2 + HOME, TMPDIR, "
                            "OUT" % (body.get("stdout"),))
        else:
            home = lines[1]
            if not (os.path.isabs(home) and _HEX32.match(
                    os.path.basename(home))):
                problems.append("HOME %r is not the <work root>/<hex32> "
                                "workdir" % home)
            if lines[2:4] != [home + "/tmp", home + "/out"]:
                problems.append("TMPDIR/OUT %r, want <wd>/tmp, <wd>/out"
                                % (lines[2:4],))
            if home == os.path.expanduser("~"):
                problems.append("HOME is the runner's own HOME")
        return problems, ["stdout      : %r" % (str(body.get("stdout"))[:120],)]
    _guard(suite, GRP_I, "I2 hello-bash", live(hello_bash))

    def hello_node(r):
        if "node" not in r.interpreters:
            raise _Info("no node on this host")
        problems = []
        body = _body(r.run({"files": [{"path": "main.js",
                                       "content": 'console.log("hello-i3")\n'}],
                            "entry": "main.js", "interpreter": "node"}),
                     "node", problems)
        _hello_ok(body, "node", "hello-i3\n", problems, quiet=False)
        return problems, ["stderr      : %r" % (str((body or {}).get(
            "stderr", ""))[:120],)]
    _guard(suite, GRP_I, "I3 hello-node", live(hello_node))

    def nonzero_exit(r):
        problems = []
        res = r.run(_py('import sys\nprint("i4-out")\n'
                        'sys.stderr.write("i4-err\\n")\nsys.exit(7)\n'))
        body = _body(res, "exit 7", problems)
        _want(body, "exit 7", problems, exitCode=7, signal=None,
              timedOut=False, stdout="i4-out\n", stderr="i4-err\n")
        return problems, []
    _guard(suite, GRP_I, "I4 nonzero-exit-normal-result", live(nonzero_exit))

    def utf8_cut(r):
        problems = []
        script = ('import sys\nn = int(sys.argv[1])\n'
                  'sys.stdout.buffer.write(b"a" * n + "\\u00e9".encode())\n')
        e_acute = chr(0xE9)
        # Fits exactly: cap - 2 ASCII bytes + the 2-byte e-acute = cap.
        body = _body(r.run(_py(script, args=[str(STDOUT_CAP - 2)])),
                     "at the cap", problems)
        _want(body, "at the cap", problems, exitCode=0,
              stdout="a" * (STDOUT_CAP - 2) + e_acute,
              stdoutTruncated=False)
        # One byte over: the cap cuts the e-acute in half; the half is
        # dropped, never replaced.
        body = _body(r.run(_py(script, args=[str(STDOUT_CAP - 1)])),
                     "one over the cap", problems)
        _want(body, "one over the cap", problems, exitCode=0,
              stdout="a" * (STDOUT_CAP - 1), stdoutTruncated=True)
        if body is not None and "\ufffd" in str(body.get("stdout", "")):
            problems.append("the cut half sequence was replaced with U+FFFD")
        return problems, ["cap         : %d bytes" % STDOUT_CAP]
    _guard(suite, GRP_I, "I5 utf8-cut-on-boundary", live(utf8_cut))

    def invalid_utf8(r):
        problems = []
        res = r.run(_py('import sys\n'
                        'sys.stdout.buffer.write(b"i6-\\xff\\xfe-end\\n")\n'
                        'sys.stderr.buffer.write(b"e6-\\xc3\\n")\n'))
        body = _body(res, "invalid UTF-8", problems)
        _want(body, "invalid UTF-8", problems, exitCode=0,
              stdout="i6-\ufffd\ufffd-end\n", stderr="e6-\ufffd\n",
              stdoutTruncated=False, stderrTruncated=False)
        return problems, []
    _guard(suite, GRP_I, "I6 invalid-utf8-replaced", live(invalid_utf8))

    def stdin_delivered(r):
        problems = []
        script = ('import sys\ndata = sys.stdin.buffer.read()\n'
                  'sys.stdout.buffer.write(b"got:" + data[::-1])\n')
        text = "i7 line one\nzweite " + chr(0xE9) + "\n"
        body = _body(r.run(_py(script, stdin=text)), "stdin", problems)
        want = "got:" + text.encode("utf-8")[::-1].decode("utf-8", "replace")
        _want(body, "stdin", problems, exitCode=0, stdout=want)
        # Control: no stdin member -> an empty stdin, never a hang.
        body = _body(r.run(_py(script), timeout=RUN_TIMEOUT), "no stdin",
                     problems)
        _want(body, "no stdin", problems, exitCode=0, stdout="got:")
        return problems, []
    _guard(suite, GRP_I, "I7 stdin-delivered", live(stdin_delivered))

    def outfiles(r):
        problems = []
        script = ('import os\nout = os.environ["OUT"]\n'
                  'os.makedirs(os.path.join(out, "sub"))\n'
                  'with open(os.path.join(out, "a.txt"), "w", '
                  'encoding="utf-8") as fh:\n'
                  '    fh.write("i8 alpha \\u00e9")\n'
                  'with open(os.path.join(out, "sub", "b.bin"), "wb") as fh:\n'
                  '    fh.write(b"\\xff\\x00\\xfe")\n'
                  'print("i8")\n')
        body = _body(r.run(_py(script)), "out files", problems)
        if body is None:
            return problems, []
        _want(body, "out files", problems, exitCode=0, stdout="i8\n",
              outFilesTruncated=False)
        got, _t = _out_map((body.get("outFiles"), False), "outFiles",
                           problems)
        want = {"a.txt": ("i8 alpha " + chr(0xE9), "utf8"),
                "sub/b.bin": (_b64(b"\xff\x00\xfe"), "base64")}
        if got != want:
            problems.append("outFiles %r, want %r" % (got, want))
        return problems, ["outFiles    : %s" % sorted(got)]
    _guard(suite, GRP_I, "I8 outfiles-utf8-and-base64", live(outfiles))

    def cancel_kills(r):
        problems = []
        lines = []
        marker = "i9-marker-%d-%s" % (os.getpid(), os.urandom(4).hex())
        script = ('import sys, time\nprint("i9-started", flush=True)\n'
                  'time.sleep(60)\n')
        msg_id = r.run_async(_py(script, args=[marker], timeoutMs=90000))
        found = _wait_ps((marker,))
        if not found:
            return ["the run's target never showed up in ps (marker %s)"
                    % marker], []
        wds = _workdirs(r.work_root)
        pgids = {p[1] for p in found}
        r.cancel(msg_id, "i9")
        took = _wait_groups_gone(pgids, KILL_GRACE_S + 1.0)
        if took is None:
            problems.append("the run's process groups %s outlived the "
                            "cancel by %.1fs" % (sorted(pgids),
                                                 KILL_GRACE_S + 1.0))
        else:
            lines.append("killed in   : %.2fs" % took)
        leftover = _ps_matching(marker)
        if leftover:
            problems.append("%d process(es) with the marker still alive"
                            % len(leftover))
        problems.extend(_wr_residue(r.work_root,
                                    wait=CLEANUP_DEADLINE_S + 2.0))
        cap = r.caps("after the cancel", problems)
        if (cap.get("concurrency") or {}).get("inFlight") != 0:
            problems.append("concurrency after the cancel %r, want inFlight "
                            "0" % (cap.get("concurrency"),))
        body = _body(r.run(_py('print("i9-after")\n')), "run after the "
                     "cancel", problems)
        _want(body, "run after the cancel", problems, exitCode=0,
              stdout="i9-after\n")
        if msg_id in r.wire._replies:
            problems.append("the cancelled request was answered: %r"
                            % (r.wire._replies[msg_id],))
        lines.append("workdir     : %s" % [w[:8] for w in wds])
        return problems, lines
    _guard(suite, GRP_I, "I9 cancel-kills-run-and-cleans", live(cancel_kills))

    def sbx_refusal():
        _live_mod()
        _live_gate(fx)
        problems = []
        lines = []
        copy = fx.sbx_copy("i10", 0o755)
        r = _Runner(fx, "i10", sbx=copy)
        try:
            body = _body(r.run(_sh("echo i10-control\n")), "control",
                         problems)
            _want(body, "control", problems, exitCode=0,
                  stdout="i10-control\n")
            for code in (2, 3):
                marker = "i10-refused-marker-%d" % code
                _write_exec(copy, "import sys\nsys.stderr.write("
                            "'sbx: error: %s\\n')\nsys.exit(%d)\n"
                            % (marker, code))
                res = r.run(_sh("echo never\n"))
                msg = _expect_refusal(res, "sandbox_unavailable",
                                      "sbx exit %d" % code, problems)
                lines.append("exit %d      : %r" % (code, msg))
                # Validation round 1 (O2): sbx's refusal line may name host
                # paths; the caller gets the code and a FIXED message, the
                # raw line goes to the runner's own log only.
                want = "sbx refused the run (exit %d)" % code
                if marker in (msg or ""):
                    problems.append("sbx exit %d: the message carries sbx's "
                                    "raw stderr line %r" % (code, msg))
                elif msg != want:
                    problems.append("sbx exit %d: message %r, want %r"
                                    % (code, msg, want))
                # The log line races the reply: stderr is drained by its own
                # thread, so give it a moment to arrive.
                logged = time.monotonic() + 3.0
                while marker not in r.wire.stderr_tail(65536) and \
                        time.monotonic() < logged:
                    time.sleep(0.05)
                if marker not in r.wire.stderr_tail(65536):
                    problems.append("sbx exit %d: the raw sbx line is not in "
                                    "the runner's log (tail %r)"
                                    % (code, r.wire.stderr_tail(300)))
                if "never" in _text(res):
                    problems.append("sbx exit %d: the script ran" % code)
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            r.close()
        return problems, lines
    _guard(suite, GRP_I, "I10 sbx-refusal-is-sandbox-unavailable",
           sbx_refusal)

    def engine_failure():
        _live_mod()
        _live_gate(fx)
        problems = []
        fake, _trigger = _render_fake_sbx(fx, "i11")
        r = _Runner(fx, "i11", sbx=fake)
        try:
            body = _body(r.run(_sh("echo i11-control\n")),
                         "pass-through control", problems)
            _want(body, "pass-through control", problems, exitCode=0,
                  stdout="i11-control\n")
            body = _body(r.run(_sh("echo never\n",
                                   entry="i11-engine-fail.sh")),
                         "engine failure after spawned", problems)
            if body is not None:
                _want(body, "engine failure", problems, exitCode=1,
                      timedOut=False, stdout="")
                if "sandbox-exec: profile error" not in str(
                        body.get("stderr", "")):
                    problems.append("engine failure: stderr %r lacks the "
                                    "engine's message" % (body.get("stderr"),))
            cap = r.caps("after the engine failure", problems)
            if "degraded" not in cap or cap.get("degraded") is not None:
                problems.append("capabilities degraded %r, want null"
                                % (cap.get("degraded", "<absent>"),))
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            r.close()
        return problems, []
    _guard(suite, GRP_I, "I11 engine-failure-after-spawned-is-declared-result",
           engine_failure)

    def venv_site():
        """A venv python3 (python3 -m venv --without-pip --symlinks under
        the run's .claude/tmp fixture dir, the venv dir in extra_reads) with
        a one-line module dropped into its site-packages: with "site": true
        the script imports it; with "site": false (-S) it gets ImportError.
        capabilities reports `site` per interpreter either way."""
        _live_mod()
        _live_gate(fx)
        problems = []
        base = _which("python3", sys.executable)
        venv = os.path.join(fx.base, fx._name("i12") + "-venv")
        proc = subprocess.run([base, "-I", "-m", "venv", "--without-pip",
                               "--symlinks", venv], capture_output=True,
                              text=True, timeout=120,
                              stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            return ["fixture: python3 -m venv exited %d: %r"
                    % (proc.returncode, proc.stderr[-200:])], []
        lib = os.path.join(venv, "lib")
        sites = [os.path.join(lib, d, "site-packages")
                 for d in sorted(os.listdir(lib))
                 if os.path.isdir(os.path.join(lib, d, "site-packages"))]
        if len(sites) != 1:
            return ["fixture: venv site-packages %r, want one" % sites], []
        with open(os.path.join(sites[0], "ph_i12_mod.py"), "w",
                  encoding="utf-8") as fh:
            fh.write('VALUE = "i12-venv-import-ok"\n')
        exe = os.path.join(venv, "bin", "python")
        script = ("try:\n    import ph_i12_mod\nexcept ImportError:\n"
                  "    print('i12-import-error')\nelse:\n"
                  "    print(ph_i12_mod.VALUE)\n")
        lines = ["venv        : %s" % os.path.relpath(venv, H.REPO_ROOT)]
        for site, want in ((True, "i12-venv-import-ok\n"),
                           (False, "i12-import-error\n")):
            label = "site %s" % site
            r = _Runner(fx, "i12-%s" % str(site).lower(), interpreters={
                "python3": {"path": exe, "extra_reads": [venv],
                            "site": site}})
            try:
                entry = _interp_entry(r.caps(label, problems), "python3")
                if entry.get("site") is not site:
                    problems.append("%s: capabilities site %r, want %r"
                                    % (label, entry.get("site", "<absent>"),
                                       site))
                body = _body(r.run(_py(script)), label, problems)
                _want(body, label, problems, exitCode=0, stdout=want)
                if body is not None:
                    lines.append("%-12s: stdout %r stderr %r"
                                 % (label, body.get("stdout"),
                                    str(body.get("stderr", ""))[:120]))
                problems.extend(_wr_residue(r.work_root, wait=2.0))
            finally:
                r.close()
        return problems, lines
    _guard(suite, GRP_I, "I12 python3-site-imports-venv-packages", venv_site)


# ---------------------------------------------------------------------------
# G: startup plans, VSZ, self-test, refresh, capabilities (stdio; Step 12)
# ---------------------------------------------------------------------------
# Pinned here where the plan is silent: a refused start never answers
# initialize, exits non-zero and names on stderr the thing it refused (the
# interpreter NAME, "sbx", "work root"/"work_root", "self-test failed", the
# lock message); a work root refused for its mode or a symlink gets no .lock;
# capabilities' interpreter entry carries path, realpath, version, state,
# reason, asLimitBytes, effectiveBudgetBytes, reads, execs (plan Step 12);
# reads list every hop of the configured spelling (G9 observes a refresh by
# the new hop appearing there).

_G6_WRAPPER = r'''#!/usr/bin/env python3
"""G6's sbx wrapper: the real sbx, except that the runner's self-test fails
(exit 1, no status line) while the trigger file exists."""
import os, sys
TF_REAL_SBX = @REAL_SBX@
TF_TRIGGER = @TRIGGER@
TF_SELF_TEST = @SELF_TEST@
def tf_entry(argv):
    if "--" not in argv:
        return None
    for tok in argv[argv.index("--") + 2:]:
        if tok.startswith("/"):
            return os.path.basename(tok)
    return None
if tf_entry(sys.argv[1:]) == TF_SELF_TEST and os.path.exists(TF_TRIGGER):
    sys.stdout.write("g6-broken\n")
    sys.exit(1)
os.execv(sys.executable, [sys.executable, "-I", "-B", TF_REAL_SBX]
         + sys.argv[1:])
'''

_INTERP_MEMBERS = frozenset({"path", "realpath", "version", "state", "reason",
                             "asLimitBytes", "effectiveBudgetBytes", "reads",
                             "execs"})


def _refused_start(config, label, problems, any_of=(), rc_want=None):
    """Start a runner on `config` that must REFUSE: no initialize answer,
    a non-zero exit, stderr naming one of `any_of`. Returns the stderr."""
    wire = _Wire(config)
    started = True
    try:
        wire.initialize()
    except (_WireError, OSError):
        started = False
    if started:
        wire.close()
        problems.append("%s: the runner started (answered initialize), want "
                        "a refused start" % label)
        return ""
    try:
        wire.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        problems.append("%s: the runner neither answered nor exited" % label)
    wire._err_thread.join(5)
    rc = wire.proc.returncode
    err = wire.stderr_tail(2000)
    wire.close()
    if rc in (0, None):
        problems.append("%s: exit %r, want non-zero" % (label, rc))
    elif rc_want is not None and rc != rc_want:
        problems.append("%s: exit %r, want %r" % (label, rc, rc_want))
    if any_of and not any(n in err for n in any_of):
        problems.append("%s: stderr %r names none of %r"
                        % (label, err.strip()[-300:], list(any_of)))
    return err.strip()


def _start_ok(fx, label, problems, interp="bash", **kw):
    """A runner that starts, plans `interp` ready and runs a script: the
    positive control beside every refused start."""
    r = _Runner(fx, label, **kw)
    try:
        cap = r.caps(label, problems)
        entry = (cap.get("interpreters") or {}).get(interp) or {}
        if entry.get("state") != "ready":
            problems.append("%s: %s state %r reason %r, want ready"
                            % (label, interp, entry.get("state"),
                               entry.get("reason")))
        params = _sh("echo %s-ok\n" % label) if interp == "bash" else \
            _py('print("%s-ok")\n' % label)
        body = _body(r.run(params), label, problems)
        _want(body, label, problems, exitCode=0, stdout="%s-ok\n" % label)
        return r.startup_s
    finally:
        r.close()


def _hop_chain(fx, label, target="/bin/bash"):
    """A fixture interpreter chain under .claude/tmp/: <dir>/bash -> target."""
    chain = os.path.realpath(tempfile.mkdtemp(prefix=fx._name(label)
                                              + "-chain-", dir=fx.base))
    hop = os.path.join(chain, "bash")
    os.symlink(target, hop)
    return chain, hop


def _interp_entry(cap, name):
    return (cap.get("interpreters") or {}).get(name) or {}


def _run_g(suite, fx):
    control = {}

    def shared_control(problems):
        """G1-G5's positive control: the template with /bin/bash starts,
        plans ready and runs (one start, shared)."""
        if "p" not in control:
            sub = []
            control["s"] = _start_ok(fx, "g-control", sub)
            control["p"] = sub
        problems.extend("control: %s" % p for p in control["p"])

    def gated(fn):
        def body():
            _live_mod()
            _live_gate(fx)
            return fn()
        return body

    def missing_sbx():
        problems = []
        shared_control(problems)
        cfg = fx.render("g1", sbx=os.path.join(fx.base, "g1-no-such-sbx"),
                        interpreters=_bash_only())
        err = _refused_start(cfg, "missing sbx", problems, any_of=("sbx",))
        return problems, ["refusal     : %r" % err[-160:]]
    _guard(suite, GRP_G, "G1 missing-sbx-refuses-start", gated(missing_sbx))

    def missing_interp():
        problems = []
        shared_control(problems)
        cfg = fx.render("g2", interpreters=_bash_only(
            os.path.join(fx.base, "g2-no-such-bash")))
        err = _refused_start(cfg, "missing interpreter", problems,
                             any_of=("bash",))
        return problems, ["refusal     : %r" % err[-160:]]
    _guard(suite, GRP_G, "G2 interpreter-missing-refuses-start",
           gated(missing_interp))

    def non_macho():
        problems = []
        shared_control(problems)
        fake = os.path.join(tempfile.mkdtemp(prefix="g3-", dir=fx.base),
                            "bash")
        _write_exec(fake, "#!/bin/sh\necho g3 not a mach-o\n")
        cfg = fx.render("g3", interpreters=_bash_only(fake))
        err = _refused_start(cfg, "non-Mach-O interpreter", problems,
                             any_of=("bash",))
        return problems, ["refusal     : %r" % err[-160:]]
    _guard(suite, GRP_G, "G3 non-macho-interpreter-refuses-start",
           gated(non_macho))

    def world_writable():
        problems = []
        shared_control(problems)
        lines = []
        for mode in (0o777, 0o757):
            cfg = fx.render("g4", interpreters=_bash_only())
            with open(cfg, "r", encoding="utf-8") as fh:
                root = json.load(fh)["work_root"]
            os.chmod(root, mode)
            try:
                err = _refused_start(cfg, "work root %04o" % mode, problems,
                                     any_of=("work root", "work_root"))
                lines.append("%04o        : %r" % (mode, err[-120:]))
                if os.listdir(root):
                    problems.append("work root %04o: the refused start left "
                                    "%s" % (mode, sorted(os.listdir(root))))
            finally:
                os.chmod(root, 0o700)
        return problems, lines
    _guard(suite, GRP_G, "G4 work-root-world-writable-refuses-start",
           gated(world_writable))

    def symlinked_root():
        problems = []
        shared_control(problems)
        real = fx.work_root("g5-real")
        link = real + "-link"
        os.symlink(real, link)
        cfg = fx.render("g5", interpreters=_bash_only(),
                        mutate=lambda o: o.__setitem__("work_root", link))
        err = _refused_start(cfg, "symlinked work root", problems,
                             any_of=("work root", "work_root"))
        if os.listdir(real):
            problems.append("the symlink's target got %s"
                            % sorted(os.listdir(real)))
        return problems, ["refusal     : %r" % err[-160:]]
    _guard(suite, GRP_G, "G5 work-root-symlink-refuses-start",
           gated(symlinked_root))

    def self_test_fails():
        mod = _live_mod()
        _live_gate(fx)
        problems = []
        if mod._SELF_TEST_NAME != SELF_TEST_NAME:
            problems.append("_SELF_TEST_NAME %r, want %r (the fixture keys "
                            "on it)" % (mod._SELF_TEST_NAME, SELF_TEST_NAME))
        trigger_dir = os.path.realpath(tempfile.mkdtemp(prefix="g6-trigger-",
                                                        dir=fx.base))
        trigger = os.path.join(trigger_dir, "fail-selftest")
        wrapper = os.path.join(fx.base, fx._name("g6") + "-sbx")
        text = _G6_WRAPPER.replace("@REAL_SBX@", repr(SBX))
        text = text.replace("@TRIGGER@", repr(trigger))
        text = text.replace("@SELF_TEST@", repr(mod._SELF_TEST_NAME))
        _write_exec(wrapper, text, mode=0o700)
        # Control: the same wrapper without the trigger starts and runs.
        _start_ok(fx, "g6-control", problems, sbx=wrapper)
        _write(trigger, "g6")
        cfg = fx.render("g6", sbx=wrapper, interpreters=_bash_only())
        err = _refused_start(cfg, "failing self-test", problems,
                             any_of=("self-test failed",), rc_want=1)
        if "bash" not in err:
            problems.append("the refusal %r does not name the interpreter"
                            % err[-160:])
        return problems, ["refusal     : %r" % err[-160:]]
    _guard(suite, GRP_G, "G6 self-test-failure-refuses-start",
           self_test_fails)

    def as_limit():
        _live_mod()
        _live_gate(fx)
        problems = []
        lines = []
        r = _Runner(fx, "g7", interpreters=_interpreters(full=False))
        try:
            entry = _interp_entry(r.caps("g7", problems), "python3")
            as_bytes = entry.get("asLimitBytes")
            eff = entry.get("effectiveBudgetBytes")
            if type(as_bytes) is not int or type(eff) is not int:
                problems.append("asLimitBytes %r effectiveBudgetBytes %r, "
                                "want ints" % (as_bytes, eff))
            else:
                vsz_interp = as_bytes - eff
                launcher = _launcher_vsz()
                lines += ["asLimit     : %d MiB" % (as_bytes // MIB),
                          "interp VSZ  : %d MiB" % (vsz_interp // MIB),
                          "launcher VSZ: %s MiB" % (launcher // MIB
                                                    if launcher else None)]
                if vsz_interp <= 0:
                    problems.append("asLimitBytes - effectiveBudgetBytes = "
                                    "%d, want the interpreter's VSZ (> 0)"
                                    % vsz_interp)
                if eff < AS_BUDGET + AS_HEADROOM:
                    problems.append("effectiveBudgetBytes %d < as_budget + "
                                    "256 MiB" % eff)
                if launcher is None:
                    problems.append("could not measure the launcher's VSZ")
                elif as_bytes < launcher + AS_BUDGET:
                    problems.append("asLimitBytes %d is not above the "
                                    "launcher's VSZ %d + as_budget"
                                    % (as_bytes, launcher))
            body = _body(r.run(_py('print("g7-ok")\n')), "run under the "
                         "limit", problems)
            _want(body, "run under the limit", problems, exitCode=0,
                  stdout="g7-ok\n")
        finally:
            r.close()
        return problems, lines
    _guard(suite, GRP_G, "G7 as-limit-above-launcher-vsz", as_limit)

    def live_verified():
        mod = _live_mod()
        problems = []
        want_table = {"darwin": True, "linux": False}
        if mod.LIVE_VERIFIED != want_table:
            problems.append("LIVE_VERIFIED %r, want %r"
                            % (mod.LIVE_VERIFIED, want_table))
        try:
            _live_gate(fx)
        except _Info:
            if problems:
                return problems, []
            raise
        r = _Runner(fx, "g8")
        try:
            cap = r.caps("g8", problems)
        finally:
            r.close()
        backend = cap.get("backend") or {}
        for key, want in (("platform", sys.platform),
                          ("liveVerified", want_table.get(sys.platform)),
                          ("engine", "/usr/bin/sandbox-exec"),
                          ("seccomp", False), ("sbx", SBX)):
            if backend.get(key) != want:
                problems.append("backend.%s %r, want %r"
                                % (key, backend.get(key), want))
        entry = _interp_entry(cap, "bash")
        missing = sorted(_INTERP_MEMBERS - set(entry))
        if missing:
            problems.append("interpreters.bash lacks %s" % missing)
        _want(entry, "interpreters.bash", problems, path="/bin/bash",
              realpath="/bin/bash", state="ready", reason=None)
        version = entry.get("version")
        if not isinstance(version, str) or "bash" not in version.lower() \
                or len(version) > 120:
            problems.append("version %r, want the bash --version first line "
                            "(<= 120 chars)" % (version,))
        execs = entry.get("execs") or []
        if "/bin/bash" not in execs:
            problems.append("execs %r lack /bin/bash" % (execs,))
        for e in execs:
            if os.path.realpath(e) != e:
                problems.append("exec %s is not a realpath" % e)
        limits = cap.get("limits") or {}
        if limits.get("timeoutMs") != 30000 or \
                limits.get("maxTimeoutMs") != 120000:
            problems.append("limits %r, want timeoutMs 30000, maxTimeoutMs "
                            "120000" % ({k: limits.get(k) for k in
                                         ("timeoutMs", "maxTimeoutMs")},))
        return problems, ["backend     : %s" % sorted(backend.items())[:6]]
    _guard(suite, GRP_G, "G8 capabilities-reports-live-verified-per-platform",
           live_verified)

    def refresh_on_change():
        _live_mod()
        _live_gate(fx)
        problems = []
        chain, hop = _hop_chain(fx, "g9")
        r = _Runner(fx, "g9", interpreters=_bash_only(hop))
        try:
            entry = _interp_entry(r.caps("start", problems), "bash")
            if hop not in (entry.get("reads") or []):
                problems.append("start: reads %r lack the hop %s"
                                % (entry.get("reads"), hop))
            body = _body(r.run(_sh("echo g9-one\n")), "first run", problems)
            _want(body, "first run", problems, exitCode=0, stdout="g9-one\n")
            # Same target, new st_ino: the KD-13 signature changes.
            _atomic_symlink("/bin/bash", hop)
            body = _body(r.run(_sh("echo g9-two\n")), "after the re-point",
                         problems)
            _want(body, "after the re-point", problems, exitCode=0,
                  stdout="g9-two\n")
            # A second hop: only a recomputed plan carries it in reads (and
            # only a recomputed --read list lets the run resolve it).
            hop2 = os.path.join(chain, "bash-2")
            os.symlink("/bin/bash", hop2)
            _atomic_symlink(hop2, hop)
            body = _body(r.run(_sh("echo g9-three\n")), "after the two-hop "
                         "re-point", problems)
            _want(body, "after the two-hop re-point", problems, exitCode=0,
                  stdout="g9-three\n")
            entry = _interp_entry(r.caps("after", problems), "bash")
            if hop2 not in (entry.get("reads") or []):
                problems.append("after the refresh: reads %r lack the new "
                                "hop %s (the plan was not recomputed)"
                                % (entry.get("reads"), hop2))
            if entry.get("state") != "ready":
                problems.append("after the refresh: state %r reason %r"
                                % (entry.get("state"), entry.get("reason")))
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            r.close()
        return problems, []
    _guard(suite, GRP_G, "G9 plan-refreshes-on-signature-change",
           refresh_on_change)

    def refresh_failure():
        _live_mod()
        _live_gate(fx)
        problems = []
        lines = []
        chain, hop = _hop_chain(fx, "g10")
        r = _Runner(fx, "g10", interpreters=_bash_only(hop))
        try:
            body = _body(r.run(_sh("echo g10-before\n")), "before", problems)
            _want(body, "before", problems, exitCode=0,
                  stdout="g10-before\n")
            bad = os.path.join(chain, "bad-bash")
            fd = os.open(bad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
            with os.fdopen(fd, "wb") as fh:
                fh.write(struct.pack("<I", MH_MAGIC_64) + b"\0" * 12)
            os.chmod(bad, 0o755)
            _atomic_symlink(bad, hop)
            res = r.run(_sh("echo never\n"))
            msg = _expect_refusal(res, "sandbox_unavailable",
                                  "run over the unparseable image", problems)
            lines.append("refusal     : %r" % (msg,))
            entry = _interp_entry(r.caps("after the failure", problems),
                                  "bash")
            reason = entry.get("reason")
            if entry.get("state") != "refresh-failed":
                problems.append("state %r, want refresh-failed"
                                % (entry.get("state"),))
            if not isinstance(reason, str) or not reason.strip() or \
                    len(reason) > 200:
                problems.append("reason %r, want a short non-empty string"
                                % (reason,))
            lines.append("reason      : %r" % (reason,))
            # Recovery (past the R-11 window): the hop back on /bin/bash.
            _atomic_symlink("/bin/bash", hop)
            time.sleep(REFRESH_SUPPRESS_S + 0.5)
            body = _body(r.run(_sh("echo g10-after\n")), "after the repair",
                         problems)
            _want(body, "after the repair", problems, exitCode=0,
                  stdout="g10-after\n")
            entry = _interp_entry(r.caps("repaired", problems), "bash")
            if entry.get("state") != "ready" or entry.get("reason") is not None:
                problems.append("repaired: state %r reason %r, want ready, "
                                "null" % (entry.get("state"),
                                          entry.get("reason")))
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            r.close()
        return problems, lines
    _guard(suite, GRP_G,
           "G10 refresh-failure-refuses-runs-and-capabilities-reports",
           refresh_failure)

    def lock_held():
        _live_mod()
        _live_gate(fx)
        problems = []
        lines = []
        r = _Runner(fx, "g11")
        try:
            msg_id = r.run_async(_sh("echo g11-start\nsleep 4\n"
                                     "echo g11-end\n"))
            deadline = time.monotonic() + PS_WAIT_S
            wds = []
            while not wds and time.monotonic() < deadline:
                wds = _workdirs(r.work_root)
                time.sleep(0.05)
            if not wds:
                problems.append("the first runner's workdir never appeared")
            cfg = fx.render("g11-second", interpreters=_bash_only(),
                            mutate=lambda o: o.__setitem__("work_root",
                                                           r.work_root))
            err = _refused_start(cfg, "second runner", problems,
                                 any_of=("lock held",), rc_want=1)
            lines.append("refusal     : %r" % err[-160:])
            if "in use by another runner" not in err:
                problems.append("the refusal %r lacks 'in use by another "
                                "runner'" % err[-160:])
            if wds and _workdirs(r.work_root) != wds:
                problems.append("the first runner's in-flight workdir %s was "
                                "touched (now %s)"
                                % (wds, _workdirs(r.work_root)))
            body = _body(_result(r.wire.wait(msg_id, RUN_TIMEOUT)),
                         "the in-flight run", problems)
            _want(body, "the in-flight run", problems, exitCode=0,
                  stdout="g11-start\ng11-end\n")
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            r.close()
        return problems, lines
    _guard(suite, GRP_G, "G11 work-root-lock-held-refuses-start", lock_held)

    def blocked_runner(label):
        """(runner, fake path, trigger file, hop) for G12/G13: bash through
        a hop chain, sbx = the pass-through fixture."""
        mod = _live_mod()
        _live_gate(fx)
        if mod._SELF_TEST_NAME != SELF_TEST_NAME:
            raise AssertionError("_SELF_TEST_NAME %r, want %r (the fixture "
                                 "keys on it)" % (mod._SELF_TEST_NAME,
                                                  SELF_TEST_NAME))
        fake, trigger_dir = _render_fake_sbx(fx, label)
        _chain, hop = _hop_chain(fx, label)
        r = _Runner(fx, label, sbx=fake, interpreters=_bash_only(hop))
        return r, fake, os.path.join(trigger_dir, "block-selftest"), hop

    def refresh_in_slot():
        problems = []
        lines = []
        r, fake, trigger, hop = blocked_runner("g12")
        try:
            entry = _interp_entry(r.caps("start", problems), "bash")
            if entry.get("state") != "ready":
                problems.append("start: state %r" % (entry.get("state"),))
            _write(trigger, "g12")
            _atomic_symlink("/bin/bash", hop)
            a = r.run_async(_sh("echo g12-a\n"))
            if not _wait_ps((fake, SELF_TEST_NAME)):
                problems.append("run A's refresh never reached the fixture's "
                                "self-test")
            b = r.run_async(_sh("echo g12-b\n"))
            time.sleep(0.5)
            cap = r.caps("A refreshing, B waiting", problems)
            if (cap.get("concurrency") or {}).get("inFlight") != 2:
                problems.append("concurrency %r while A refreshes and B "
                                "waits, want inFlight 2"
                                % (cap.get("concurrency"),))
            sent = time.monotonic()
            c = r.run_async(_sh("echo g12-c\n"))
            res = _result(r.wire.wait(c, RPC_TIMEOUT))
            lines.append("C answered : %.2fs" % (time.monotonic() - sent))
            _expect_refusal(res, "busy", "run C", problems)
            err = (_payload(res) or {}).get("error") or {}
            if err.get("retryAfterMs") != 1000:
                problems.append("run C: retryAfterMs %r, want 1000"
                                % (err.get("retryAfterMs"),))
            if time.monotonic() - sent > 3.0:
                problems.append("run C's busy took %.1fs, want immediate"
                                % (time.monotonic() - sent))
            for name, msg_id in (("A", a), ("B", b)):
                if msg_id in r.wire._replies:
                    problems.append("run %s answered while the refresh was "
                                    "blocked: %r"
                                    % (name, r.wire._replies[msg_id]))
            os.unlink(trigger)
            for name, msg_id in (("A", a), ("B", b)):
                body = _body(_result(r.wire.wait(msg_id, RUN_TIMEOUT)),
                             "run " + name, problems)
                _want(body, "run " + name, problems, exitCode=0,
                      stdout="g12-%s\n" % name.lower())
            cap = r.caps("after", problems)
            if _interp_entry(cap, "bash").get("state") != "ready" or \
                    (cap.get("concurrency") or {}).get("inFlight") != 0:
                problems.append("after: state %r concurrency %r"
                                % (_interp_entry(cap, "bash").get("state"),
                                   cap.get("concurrency")))
            problems.extend(_wr_residue(r.work_root, wait=2.0))
        finally:
            if os.path.lexists(trigger):
                os.unlink(trigger)
            r.close()
        return problems, lines
    _guard(suite, GRP_G, "G12 refresh-runs-inside-a-slot", refresh_in_slot)

    def cancel_in_refresh():
        problems = []
        lines = []
        r, fake, trigger, hop = blocked_runner("g13")
        try:
            _write(trigger, "g13")
            _atomic_symlink("/bin/bash", hop)
            a = r.run_async(_sh("echo g13-a\n"))
            found = _wait_ps((fake, SELF_TEST_NAME))
            if not found:
                return problems + ["run A's refresh never reached the "
                                   "fixture's self-test"], lines
            pgids = {p[1] for p in found}
            b = r.run_async(_sh("echo g13-b\n"))
            b_sent = time.monotonic()
            time.sleep(5.0)
            r.cancel(a, "g13")
            took = _wait_groups_gone(pgids, KILL_GRACE_S + 1.0)
            if took is None:
                problems.append("A's self-test group %s outlived the cancel "
                                "by %.1fs" % (sorted(pgids),
                                              KILL_GRACE_S + 1.0))
            else:
                lines.append("A killed in : %.2fs" % took)
            cap = r.caps("after A's cancel", problems)
            entry = _interp_entry(cap, "bash")
            if entry.get("state") != "ready" or entry.get("reason") is not None:
                problems.append("after A's cancel: state %r reason %r, want "
                                "ready, null" % (entry.get("state"),
                                                 entry.get("reason")))
            # The waiter bound (R4:M2): ONE 30 s term from B's send.
            bound = REFRESH_DEADLINE_S + 2.0
            remaining = max(1.0, b_sent + bound + 5.0 - time.monotonic())
            try:
                res = _result(r.wire.wait(b, remaining))
                b_took = time.monotonic() - b_sent
                lines.append("B answered  : %.2fs (bound %.0fs)"
                             % (b_took, bound))
                _expect_refusal(res, "sandbox_unavailable", "run B",
                                problems, contains=("refresh deadline",))
                if b_took > bound:
                    problems.append("run B answered after %.1fs, bound %.0fs"
                                    % (b_took, bound))
            except _WireError:
                problems.append("run B not answered within %.0fs of its send"
                                % (bound + 5.0))
            entry = _interp_entry(r.caps("after B", problems), "bash")
            if entry.get("state") != "ready" or entry.get("reason") is not None:
                problems.append("after B's deadline: state %r reason %r, want "
                                "ready, null (B's term was partly spent "
                                "waiting)" % (entry.get("state"),
                                              entry.get("reason")))
            os.unlink(trigger)
            body = _body(r.run(_sh("echo g13-d\n")), "the run right after",
                         problems)
            _want(body, "the run right after", problems, exitCode=0,
                  stdout="g13-d\n")
            if a in r.wire._replies:
                problems.append("the cancelled run A was answered: %r"
                                % (r.wire._replies[a],))
            entry = _interp_entry(r.caps("end", problems), "bash")
            if entry.get("state") != "ready":
                problems.append("end: state %r" % (entry.get("state"),))
            problems.extend(_wr_residue(r.work_root,
                                        wait=CLEANUP_DEADLINE_S + 2.0))
        finally:
            if os.path.lexists(trigger):
                os.unlink(trigger)
            r.close()
        return problems, lines
    _guard(suite, GRP_G, "G13 cancel-during-refresh-keeps-plan-state",
           cancel_in_refresh)


def _launcher_vsz():
    """Bytes of VSZ of a python launcher like sbx's ([sys.executable, -I, -B]
    blocked on stdin), max of three /bin/ps samples; None on failure."""
    proc = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                             "import sys; sys.stdin.read()"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    best = None
    try:
        time.sleep(0.2)
        for _ in range(3):
            try:
                out = subprocess.run(["/bin/ps", "-o", "vsz=", "-p",
                                      str(proc.pid)], capture_output=True,
                                     text=True, timeout=10,
                                     stdin=subprocess.DEVNULL).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                continue
            if out.isdigit():
                best = max(best or 0, int(out) * 1024)
            time.sleep(0.1)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return best


# ---------------------------------------------------------------------------
# E: the spec's acceptance escape suite (stdio, live; Step 13)
# ---------------------------------------------------------------------------
# Every row is an escape attempt from INSIDE a run, made by one probe under
# tests/files/mcp_sandbox/escape/ (stdlib only, ONE JSON line naming each
# attempt's outcome, never a secret byte). Every row carries a POSITIVE
# CONTROL -- the same probe run outside any sandbox by the test, or the same
# action on a target the profile grants, inside the run -- so a probe that is
# broken cannot pass as "denied". Every row ends with "work root is empty".
# The runner is started ONCE for the group with the canaries in its own
# environment (MCP_SANDBOX_CANARY, MCP_PROXY_TOKEN); the canary directory
# .claude/tmp/mcp_sandbox-canary-<rand>/secret.txt stands in for the store
# root. No canary value may appear in any run's result.

ESCAPE_DIR = H.repo_path("tests", "files", "mcp_sandbox", "escape")
_DENIED = ("EPERM", "EACCES")
# M0:7 -- the keys a run's interpreter adds to the five --env keys (restricted
# profile; bash's PWD SHLVL _ as seen by a child). Asserted EQUAL (R2:L5).
E_ENV_ADDITIONS = {"python3": (), "node": (), "bash": ("PWD", "SHLVL", "_")}
E_PROBE_ENTRY = {"python3": "main.py", "bash": "main.sh", "node": "main.js"}
E_PROBE_EXT = {".py": "python3", ".sh": "bash", ".js": "node"}
FSIZE_BYTES = 16 * MIB         # config.json.tmpl limits.fsize_bytes
NOFILE_LIMIT = 256             # config.json.tmpl limits.nofile
NPROC_HEADROOM = 64            # config.json.tmpl limits.nproc_headroom
MAX_CONCURRENT = 2             # config.json.tmpl limits.max_concurrent
FLOOD_BYTES = 10 * 1024 * MIB  # E11: 10 GiB
RSS_DELTA_MAX = 32 * MIB       # E11: the runner's RSS may grow by less
OUTSIDE_TIMEOUT = 60.0
NO_HANG_S = 10.0               # E10 / E15: a returned run, never a hang
BUSY_S = 1.0                   # E18: busy is answered at once
E_SETUID = ("crontab", "at", "sudo")
E_PLANTED_CANDIDATES = ("/usr/local/bin/bash", "/opt/homebrew/bin/bash")
E_PLANTED_MAX = 3 * MIB        # inside limits.max_files_bytes (4 MiB)

# E20's outside control: the probe on a pty that IS its controlling tty.
_E_PTY_HELPER = r'''
import os, pty, sys
pid, fd = pty.fork()
if pid == 0:
    os.execv(sys.executable, [sys.executable, "-I", "-B"] + sys.argv[1:])
chunks = []
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    chunks.append(chunk)
os.waitpid(pid, 0)
sys.stdout.write(b"".join(chunks).decode("utf-8", "replace").replace("\r", ""))
'''

# E26's outside control: the probe with a soft RLIMIT_NOFILE above its cap.
_E_NOFILE_HELPER = r'''
import os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
want = 2048 if hard == resource.RLIM_INFINITY else min(2048, hard)
resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, want), hard))
os.execv(sys.executable, [sys.executable, "-I", "-B"] + sys.argv[1:])
'''


class _Canary:
    """The group's secrets: a store-root stand-in under .claude/tmp/ and two
    values in the runner's OWN environment. None may reach a result."""

    def __init__(self):
        self.dir = H.repo_path(".claude", "tmp", "mcp_sandbox-canary-"
                               + os.urandom(6).hex())
        os.makedirs(self.dir, mode=0o700)
        self.token = "canary-store-" + os.urandom(16).hex()
        self.secret = os.path.join(self.dir, "secret.txt")
        _write(self.secret, self.token)
        self.env_value = "canary-env-" + os.urandom(16).hex()
        self.proxy_token = "canary-proxy-" + os.urandom(16).hex()

    def runner_env(self):
        return {"MCP_SANDBOX_CANARY": self.env_value,
                "MCP_PROXY_TOKEN": self.proxy_token}

    def leaks(self, text):
        return [name for name, value in (("store secret", self.token),
                                         ("MCP_SANDBOX_CANARY",
                                          self.env_value),
                                         ("MCP_PROXY_TOKEN",
                                          self.proxy_token))
                if value in text]

    def cleanup(self, keep):
        if keep:
            print("\n[--keep] canary retained at: %s" % self.dir)
            return
        shutil.rmtree(self.dir, ignore_errors=True)


def _probe_text(name):
    with open(os.path.join(ESCAPE_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _probe_params(name, args=(), files=(), **extra):
    """run_script params running escape probe `name` as the entry."""
    interp = E_PROBE_EXT[os.path.splitext(name)[1]]
    entry = E_PROBE_ENTRY[interp]
    out = {"files": [{"path": entry, "content": _probe_text(name)}]
           + list(files),
           "entry": entry, "interpreter": interp,
           "args": [str(a) for a in args]}
    out.update(extra)
    return out


def _json_line(text):
    """The last line of `text` that is a JSON object, or None."""
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                return None
            return obj if isinstance(obj, dict) else None
    return None


def _leak_check(c, body, label, problems):
    if body is None:
        return
    text = json.dumps(body, sort_keys=True)
    for name in c.leaks(text):
        problems.append("%s: the %s value appears in the run's result"
                        % (label, name))


def _probe_run(r, c, name, label, problems, args=(), timeout=RUN_TIMEOUT,
               want_exit=0, files=(), json_from="stdout", **extra):
    """Run probe `name` INSIDE the runner: (body, parsed JSON line). A
    refusal, a missing JSON line, an unexpected exit code or a canary value
    in the result are problems."""
    body = _body(r.run(_probe_params(name, args, files, **extra), timeout),
                 label, problems)
    if body is None:
        return None, None
    _leak_check(c, body, label, problems)
    if want_exit is not None and body.get("exitCode") != want_exit:
        problems.append("%s: exitCode %r signal %r, want exit %r (stderr %r)"
                        % (label, body.get("exitCode"), body.get("signal"),
                           want_exit, str(body.get("stderr", ""))[:200]))
    got = _json_line(body.get(json_from))
    if got is None:
        problems.append("%s: the probe printed no JSON line (stdout %r, "
                        "stderr %r)" % (label,
                                        str(body.get("stdout", ""))[:160],
                                        str(body.get("stderr", ""))[:200]))
    return body, got


def _scratch(fx, label):
    return os.path.realpath(tempfile.mkdtemp(prefix=fx._name(label) + "-",
                                             dir=fx.base))


def _outside(fx, name, args=(), cwd=None, env=None, pass_fds=(), argv=None,
             json_from="stdout", timeout=OUTSIDE_TIMEOUT):
    """The POSITIVE CONTROL: probe `name` run by the test OUTSIDE any
    sandbox, in a scratch dir under fx.base: (rc, parsed JSON, stderr)."""
    path = os.path.join(ESCAPE_DIR, name)
    if argv is None:
        interp = E_PROBE_EXT[os.path.splitext(name)[1]]
        if interp == "python3":
            argv = [sys.executable, "-I", "-B"]
        elif interp == "bash":
            argv = ["/bin/bash", "--noprofile", "--norc"]
        else:
            argv = [_which("node", "node")]
    if cwd is None:
        cwd = _scratch(fx, "e-outside")
    proc = subprocess.run(list(argv) + [path] + [str(a) for a in args],
                          cwd=cwd, env=H.child_env(env),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout,
                          close_fds=True, pass_fds=tuple(pass_fds))
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    return proc.returncode, _json_line(err if json_from == "stderr" else out), \
        err


def _ctl(control, want, label="outside control"):
    """Problems of an outside control's (rc, JSON, stderr) against `want`."""
    rc, got, err = control
    if got is None:
        return ["%s: the probe printed no JSON line (rc %r: %s) -- a broken "
                "probe would pass as denied" % (label, rc, err.strip()[-200:])]
    return ["%s: %s %r, want %r" % (label, key, got.get(key), value)
            for key, value in want.items() if got.get(key) != value]


def _cmp(got, want, label):
    if not isinstance(got, dict):
        return []
    return ["%s: %s %r, want %r" % (label, key, got.get(key), value)
            for key, value in want.items() if got.get(key) != value]


def _denied(got, keys, label):
    """Each `keys` outcome must be a denial (EPERM / EACCES), never "ok"."""
    if not isinstance(got, dict):
        return []
    return ["%s: %s %r, want a denial (%s)" % (label, key, got.get(key),
                                               "/".join(_DENIED))
            for key in keys if got.get(key) not in _DENIED]


def _all_denied(values, label):
    if not isinstance(values, list):
        return ["%s: %r is not a list" % (label, values)]
    return ["%s[%d]: %r, want a denial (%s)" % (label, i, v, "/".join(_DENIED))
            for i, v in enumerate(values) if v not in _DENIED]


def _target_procs(marker):
    """The run's TARGET processes (pid, pgid, command) carrying `marker`:
    sbx's own parent and the transient engine stages excluded."""
    return [p for p in _ps_matching(marker)
            if SBX not in p[2] and "sandbox-exec" not in p[2]
            and not p[2].startswith("/usr/bin/env")]


def _wait_targets(marker, n=1, timeout=PS_WAIT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = _target_procs(marker)
        if len(found) >= n:
            return found
        time.sleep(0.1)
    return _target_procs(marker)


def _kill_marker(marker):
    """Test-side cleanup: SIGKILL whatever still carries `marker`."""
    for pid, _pgid, _cmd in _ps_matching(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _rss_bytes(pid):
    try:
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) * 1024 if out.isdigit() else None


def _marker(tag):
    return "%s-marker-%d-%s" % (tag, os.getpid(), os.urandom(4).hex())


def _ssh_file(ssh):
    """A regular file under ~/.ssh (known_hosts first), or None."""
    try:
        names = sorted(os.listdir(ssh))
    except OSError:
        return None
    names.sort(key=lambda n: n != "known_hosts")
    for name in names:
        path = os.path.join(ssh, name)
        if os.path.isfile(path) and not os.path.islink(path) \
                and os.access(path, os.R_OK):
            return path
    return None


def _planted_binary(fx):
    """(name, bytes, outside-control detail) of a NON-platform Mach-O that
    runs when copied to a scratch dir (M0 3b: a copied /bin binary is
    SIGKILLed even unsandboxed), or (None, None, why)."""
    for spelling in E_PLANTED_CANDIDATES:
        real = os.path.realpath(spelling)
        try:
            if not os.path.isfile(real) or os.path.getsize(real) > E_PLANTED_MAX:
                continue
            data = _read(real)
        except OSError:
            continue
        scratch = _scratch(fx, "e21-ctl")
        _write(os.path.join(scratch, "planted-bin"), data, mode=0o755)
        control = _outside(fx, "e21_exec_planted.py", ("planted-bin",),
                           cwd=scratch)
        if control[1] and control[1].get("planted_binary") == "ok":
            return "planted-bin", data, control
    return None, None, "no copyable non-platform binary (%s)" \
        % ", ".join(E_PLANTED_CANDIDATES)


def _run_e(suite, fx):
    shared = {}

    def runner():
        """One runner for the group, the canaries in its environment."""
        if "e" in shared:
            raise shared["e"]
        if "r" not in shared:
            try:
                shared["c"] = _Canary()
                shared["r"] = _Runner(fx, "e",
                                      interpreters=_live_interpreters(),
                                      env=shared["c"].runner_env())
            except BaseException as exc:      # noqa: BLE001 -- every row
                shared["e"] = exc
                raise
        return shared["r"]

    def live(fn):
        def body():
            _live_mod()
            _live_gate(fx)
            r = runner()
            problems, lines = fn(r, shared["c"])
            problems = list(problems) + _wr_residue(r.work_root, wait=2.0)
            return problems, lines
        return body

    try:
        _run_e_acceptance(suite, fx, live)
        _run_e_risk(suite, fx, live)
    finally:
        if "r" in shared:
            shared["r"].close()
        if "c" in shared:
            shared["c"].cleanup(fx.keep)


def _run_e_acceptance(suite, fx, live):
    """E1-E18: the spec's acceptance rows."""

    def read_ssh(r, c):
        ssh = os.path.expanduser("~/.ssh")
        target = _ssh_file(ssh)
        if target is None:
            raise _Info("no readable regular file under ~/.ssh on this host")
        problems = []
        control = _outside(fx, "e01_read_ssh.py", (ssh, target))
        problems += _ctl(control, {"list_ssh": "ok", "read_ssh_file": "ok",
                                   "read_own": "ok"})
        _body_, got = _probe_run(r, c, "e01_read_ssh.py", "inside", problems,
                                 args=(ssh, target))
        problems += _denied(got, ("list_ssh", "read_ssh_file"), "inside")
        problems += _cmp(got, {"read_own": "ok"}, "inside control")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],),
                          "target      : ~/.ssh/%s" % os.path.basename(target)]
    _guard(suite, GRP_E, "E1 read-ssh-denied", live(read_ssh))

    def read_store(r, c):
        problems = []
        control = _outside(fx, "e02_read_store.py", (c.secret,))
        problems += _ctl(control, {"read_secret": "ok",
                                   "size": len(c.token), "list_store": 1})
        _b, got = _probe_run(r, c, "e02_read_store.py", "inside", problems,
                             args=(c.secret,))
        problems += _denied(got, ("read_secret", "list_store"), "inside")
        problems += _cmp(got, {"size": None}, "inside")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E2 read-store-root-denied", live(read_store))

    def list_home(r, c):
        problems = []
        home = os.path.expanduser("~")
        control = _outside(fx, "e03_list_home.py", (home,))
        if control[1] is not None and type(control[1].get("list_home")) \
                is not int:
            problems.append("outside control: list_home %r, want a count"
                            % (control[1].get("list_home"),))
        problems += _ctl(control, {})
        _b, got = _probe_run(r, c, "e03_list_home.py", "inside", problems,
                             args=(home,))
        problems += _denied(got, ("list_home",), "inside")
        if got is not None and not (type(got.get("list_cwd")) is int
                                    and got.get("list_cwd") >= 1):
            problems.append("inside control: list_cwd %r, want the workdir's "
                            "entry count" % (got.get("list_cwd"),))
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E3 list-home-denied", live(list_home))

    def stat_oracle(r, c):
        problems, lines = [], []
        missing = os.path.join(c.dir, "missing-" + os.urandom(4).hex())
        control = _outside(fx, "e04_stat_oracle.py", (c.secret, missing))
        problems += _ctl(control, {"stat_existing": "ok",
                                   "size_existing": len(c.token),
                                   "stat_missing": "ENOENT"})
        _b, got = _probe_run(r, c, "e04_stat_oracle.py", "inside", problems,
                             args=(c.secret, missing))
        # the gated half: an existing path outside every grant yields a
        # denial and so no size, no times
        problems += _denied(got, ("stat_existing", "lstat_existing"),
                            "inside")
        problems += _cmp(got, {"size_existing": None}, "inside")
        if got is not None:
            # DECLARED (L5, IMPL:029): Seatbelt checks the terminal vnode
            # only -- a MISSING name answers ENOENT, so existence stays an
            # oracle; recorded, never asserted away, never claimed closed.
            if got.get("stat_missing") not in ("ENOENT",) + _DENIED:
                problems.append("inside: stat_missing %r, want ENOENT "
                                "(declared) or a denial"
                                % (got.get("stat_missing"),))
            lines.append("declared    : existence oracle -- missing answers "
                         "%s, existing %s (L5, IMPL:029)"
                         % (got.get("stat_missing"), got.get("stat_existing")))
        return problems, lines + ["inside      : %r" % (got,),
                                  "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E4 stat-oracle-denied", live(stat_oracle))

    def write_outside(r, c):
        problems = []
        tag = os.urandom(4).hex()
        targets = [os.path.join(c.dir, "e05-escape-%s.txt" % tag),
                   "/private/tmp/ph-e05-escape-%s.txt" % tag,
                   os.path.join(r.work_root, "e05-sibling-%s.txt" % tag),
                   os.path.join(os.path.expanduser("~"),
                                ".ph-e05-escape-%s.txt" % tag)]
        scratch = _scratch(fx, "e05-ctl")
        control = _outside(fx, "e05_write_outside.py",
                           (os.path.join(scratch, "e05-ctl.txt"),),
                           cwd=scratch)
        problems += _ctl(control, {"write": ["ok"], "write_cwd": "ok"})
        try:
            _b, got = _probe_run(r, c, "e05_write_outside.py", "inside",
                                 problems, args=targets)
            if got is not None:
                problems += _all_denied(got.get("write"), "inside write")
                problems += _cmp(got, {"write_cwd": "ok"}, "inside control")
        finally:
            for path in targets:
                if os.path.lexists(path):
                    problems.append("%s exists after the run" % path)
                    os.unlink(path)
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E5 write-outside-denied", live(write_outside))

    def tcp_connect(r, c):
        problems = []
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(8)
            srv.settimeout(0.3)
            udp.bind(("127.0.0.1", 0))
            udp.settimeout(0.3)
            args = ("127.0.0.1", srv.getsockname()[1], udp.getsockname()[1])

            def arrived():
                n_tcp = n_udp = 0
                while True:
                    try:
                        conn, _a = srv.accept()
                    except socket.timeout:
                        break
                    conn.close()
                    n_tcp += 1
                while True:
                    try:
                        udp.recvfrom(64)
                    except socket.timeout:
                        break
                    n_udp += 1
                return n_tcp, n_udp
            control = _outside(fx, "e06_tcp_connect.py", args)
            problems += _ctl(control, {"tcp_connect": "ok", "udp_send": "ok"})
            seen_ctl = arrived()
            if seen_ctl != (1, 1):
                problems.append("outside control: the listeners saw %r "
                                "(tcp, udp), want (1, 1)" % (seen_ctl,))
            _b, got = _probe_run(r, c, "e06_tcp_connect.py", "inside",
                                 problems, args=args)
            problems += _denied(got, ("tcp_connect", "udp_send"), "inside")
            seen = arrived()
            if seen != (0, 0):
                problems.append("inside: the listeners saw %r (tcp, udp) "
                                "from the run" % (seen,))
        finally:
            srv.close()
            udp.close()
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E6 tcp-connect-denied", live(tcp_connect))

    def unix_socket(r, c):
        problems = []
        path = os.path.join(fx.base, "e7-%s.sock" % os.urandom(2).hex())
        if len(path.encode()) > 103:
            raise _Info("the socket path is too long for AF_UNIX here (%d)"
                        % len(path.encode()))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(path)
            srv.listen(4)
            srv.settimeout(0.3)

            def arrived():
                n = 0
                while True:
                    try:
                        conn, _a = srv.accept()
                    except socket.timeout:
                        return n
                    conn.close()
                    n += 1
            control = _outside(fx, "e07_unix_socket.py", (path,))
            problems += _ctl(control, {"unix_connect": "ok"})
            if arrived() != 1:
                problems.append("outside control: the listener saw no "
                                "connection")
            _b, got = _probe_run(r, c, "e07_unix_socket.py", "inside",
                                 problems, args=(path,))
            problems += _denied(got, ("unix_connect",), "inside")
            n = arrived()
            if n:
                problems.append("inside: the listener accepted %d "
                                "connection(s) from the run" % n)
        finally:
            srv.close()
            if os.path.lexists(path):
                os.unlink(path)
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E7 unix-socket-denied", live(unix_socket))

    def fork_bomb(r, c):
        problems = []
        control = _outside(fx, "e08_fork_bomb.py", (4,))
        problems += _ctl(control, {"forked": 4, "stopped": "cap"})
        started = time.monotonic()
        _b, got = _probe_run(r, c, "e08_fork_bomb.py", "inside", problems,
                             args=(2000,), timeoutMs=30000, timeout=90)
        took = time.monotonic() - started
        if got is not None:
            problems += _cmp(got, {"stopped": "EAGAIN"}, "inside")
            forked = got.get("forked")
            # RLIMIT_NPROC = the uid's count at spawn + headroom: the bomb
            # stops near the headroom (other processes may exit meanwhile)
            if type(forked) is not int or forked > 2 * NPROC_HEADROOM:
                problems.append("inside: forked %r, want <= %d (nproc "
                                "+%d)" % (forked, 2 * NPROC_HEADROOM,
                                          NPROC_HEADROOM))
        cap = r.caps("after the fork bomb", problems)
        if (cap.get("concurrency") or {}).get("inFlight") != 0:
            problems.append("concurrency after the fork bomb %r"
                            % (cap.get("concurrency"),))
        return problems, ["inside      : %r (%.2fs)" % (got, took),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E8 fork-bomb-contained", live(fork_bomb))

    def infinite_loop(r, c):
        problems, lines = [], []
        marker = _marker("e09")
        control = _outside(fx, "e09_infinite_loop.py", (marker, "--once"))
        problems += _ctl(control, {"started": True})
        msg_id = r.run_async(_probe_params("e09_infinite_loop.py", (marker,),
                                           timeoutMs=2000))
        try:
            found = _wait_targets(marker, n=2)
            if len(found) < 2:
                problems.append("the run's two target processes never "
                                "showed up in ps (saw %d)" % len(found))
            pgids = {p[1] for p in found}
            res = _result(r.wire.wait(msg_id, RUN_TIMEOUT))
            body = _body(res, "timeout", problems)
            _leak_check(c, body, "timeout", problems)
            _want(body, "timeout", problems, timedOut=True, exitCode=None)
            if body is not None and not (_json_line(body.get("stdout"))
                                         or {}).get("started"):
                problems.append("timeout: the probe's started line is "
                                "missing (stdout %r)"
                                % (str(body.get("stdout"))[:120],))
            took = _wait_groups_gone(pgids, KILL_GRACE_S + 1.0) \
                if pgids else 0.0
            if took is None:
                problems.append("the target group %s outlived the timeout"
                                % sorted(pgids))
            left = _ps_matching(marker)
            if left:
                problems.append("%d process(es) with the marker alive"
                                % len(left))
            lines += ["result      : timedOut %r signal %r durationMs %r"
                      % ((body or {}).get("timedOut"),
                         (body or {}).get("signal"),
                         (body or {}).get("durationMs")),
                      "group       : %d target process(es), gone in %s"
                      % (len(found), "%.2fs" % took if took is not None
                         else "never")]
        finally:
            _kill_marker(marker)
        return problems, lines
    _guard(suite, GRP_E, "E9 infinite-loop-timeout-group-killed",
           live(infinite_loop))

    def setsid_orphan(r, c):
        problems, lines = [], []
        marker = _marker("e10")
        control = _outside(fx, "e10_setsid_orphan.py", (marker, "0.1"))
        if control[1] is None or type(control[1].get("orphan")) is not int:
            problems += _ctl(control, {"orphan": "<pid>"})
        try:
            started = time.monotonic()
            _b, got = _probe_run(r, c, "e10_setsid_orphan.py", "inside",
                                 problems, args=(marker, 30),
                                 timeoutMs=20000, timeout=NO_HANG_S + 30)
            took = time.monotonic() - started
            if took > NO_HANG_S:
                problems.append("the run took %.1fs: the orphan holding the "
                                "output pipes hung it (> %.0fs)"
                                % (took, NO_HANG_S))
            if got is not None and type(got.get("orphan")) is not int:
                problems.append("inside: orphan %r, want its pid"
                                % (got.get("orphan"),))
            lines.append("returned in : %.2fs" % took)
            alive = _target_procs(marker)
            # DECLARED (T11): on macOS a setsid() orphan leaves the target's
            # group and survives the group kill, inside the profile; it is
            # recorded, never claimed closed (Linux: PID-ns teardown).
            lines.append("declared    : T11 -- %s"
                         % ("the setsid orphan SURVIVED the run (%d "
                            "process(es), killed by the test; INFO)"
                            % len(alive) if alive else
                            "the setsid orphan did not survive"))
        finally:
            _kill_marker(marker)
        return problems, lines
    _guard(suite, GRP_E, "E10 setsid-orphan-no-hang", live(setsid_orphan))

    def stdout_flood(r, c):
        problems, lines = [], []
        control = _outside(fx, "e11_stdout_flood.py", (4 * MIB,),
                           json_from="stderr")
        problems += _ctl(control, {"written": 4 * MIB, "status": "ok"})
        pid = r.wire.proc.pid
        base = _rss_bytes(pid)
        samples = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                value = _rss_bytes(pid)
                if value is not None:
                    samples.append(value)
                stop.wait(0.2)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        started = time.monotonic()
        try:
            body, got = _probe_run(r, c, "e11_stdout_flood.py", "inside",
                                   problems, args=(FLOOD_BYTES,),
                                   timeoutMs=120000, timeout=200,
                                   json_from="stderr")
        finally:
            stop.set()
            sampler.join(5)
        took = time.monotonic() - started
        _want(body, "flood", problems, timedOut=False, stdoutTruncated=True)
        if body is not None and len(str(body.get("stdout", ""))) != STDOUT_CAP:
            problems.append("flood: stdout carries %d chars, want the %d-byte "
                            "cap" % (len(str(body.get("stdout", ""))),
                                     STDOUT_CAP))
        problems += _cmp(got, {"written": FLOOD_BYTES, "status": "ok"},
                         "inside")
        if base is None or not samples:
            problems.append("no RSS sample of the runner (ps failed)")
        else:
            delta = max(samples) - base
            lines.append("runner RSS  : base %.1f MiB, peak %.1f MiB, delta "
                         "%.1f MiB over %d samples"
                         % (base / MIB, max(samples) / MIB, delta / MIB,
                            len(samples)))
            if delta >= RSS_DELTA_MAX:
                problems.append("runner RSS delta %.1f MiB >= %d MiB"
                                % (delta / MIB, RSS_DELTA_MAX // MIB))
        lines.append("flood       : %r in %.1fs" % (got, took))
        return problems, lines
    _guard(suite, GRP_E, "E11 stdout-flood-10gib-capped", live(stdout_flood))

    def huge_alloc(r, c):
        problems = []
        cap = r.caps("capabilities", problems)
        as_limit = ((cap.get("interpreters") or {}).get("python3")
                    or {}).get("asLimitBytes")
        if type(as_limit) is not int or as_limit <= 0:
            return problems + ["python3 asLimitBytes %r" % (as_limit,)], []
        sizes = (64 * MIB, as_limit)
        control = _outside(fx, "e12_huge_alloc.py", sizes)
        problems += _ctl(control, {"map": ["ok", "ok"]})
        body, got = _probe_run(r, c, "e12_huge_alloc.py", "inside", problems,
                               args=sizes)
        if got is not None:
            maps = got.get("map") or [None, None]
            if maps[0] != "ok":
                problems.append("inside control: a 64 MiB map %r, want ok"
                                % (maps[0],))
            if maps[1] == "ok":
                problems.append("inside: a map of the whole AS limit "
                                "(%d bytes) succeeded" % as_limit)
        return problems, ["inside      : %r (asLimitBytes %d)"
                          % (got, as_limit),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E12 huge-alloc-normal-result", live(huge_alloc))

    def out_symlink(r, c):
        problems = []
        body, got = _probe_run(r, c, "e13_out_symlink.py", "inside", problems,
                               args=(c.secret,))
        # the control: the links WERE planted, and a regular file is collected
        problems += _cmp(got, {"link_abs": "ok", "link_rel": "ok",
                               "link_dir": "ok"}, "inside control")
        got_files = {}
        if body is not None:
            got_files, _t = _out_map((body.get("outFiles"), False),
                                     "outFiles", problems)
            if got_files != {"real.txt": ("e13-real", "utf8")}:
                problems.append("outFiles %r, want only real.txt"
                                % (sorted(got_files),))
        return problems, ["inside      : %r" % (got,),
                          "outFiles    : %s" % sorted(got_files)]
    _guard(suite, GRP_E, "E13 out-symlink-not-followed", live(out_symlink))

    def out_hardlink(r, c):
        problems = []
        body, got = _probe_run(r, c, "e14_out_hardlink.py", "inside",
                               problems, args=(c.secret,))
        problems += _cmp(got, {"link_wd": "ok"}, "inside control")
        if got is not None and got.get("link_secret") == "ok":
            problems.append("inside: the secret was hard-linked into out/")
        got_files = {}
        if body is not None:
            got_files, _t = _out_map((body.get("outFiles"), False),
                                     "outFiles", problems)
            if got_files != {"single.txt": ("e14-single", "utf8")}:
                problems.append("outFiles %r, want only single.txt"
                                % (sorted(got_files),))
        return problems, ["inside      : %r" % (got,),
                          "outFiles    : %s" % sorted(got_files)]
    _guard(suite, GRP_E, "E14 out-hardlink-skipped", live(out_hardlink))

    def out_fifo(r, c):
        problems = []
        started = time.monotonic()
        body, got = _probe_run(r, c, "e15_out_fifo.py", "inside", problems,
                               timeout=NO_HANG_S + 30)
        took = time.monotonic() - started
        if took > NO_HANG_S:
            problems.append("the run took %.1fs (> %.0fs): the FIFO hung "
                            "the collector" % (took, NO_HANG_S))
        problems += _cmp(got, {"mkfifo": "ok"}, "inside control")
        got_files = {}
        if body is not None:
            got_files, _t = _out_map((body.get("outFiles"), False),
                                     "outFiles", problems)
            if got_files != {"x.txt": ("e15-x", "utf8")}:
                problems.append("outFiles %r, want only x.txt"
                                % (sorted(got_files),))
        return problems, ["returned in : %.2fs" % took,
                          "outFiles    : %s" % sorted(got_files)]
    _guard(suite, GRP_E, "E15 out-fifo-no-hang", live(out_fifo))

    def env_keys(r, c):
        problems, lines = [], []
        control = _outside(fx, "e16_env.py", env=c.runner_env())
        keys = (control[1] or {}).get("keys") or []
        if "MCP_SANDBOX_CANARY" not in keys or "MCP_PROXY_TOKEN" not in keys:
            problems.append("outside control: the probe did not see the "
                            "canary keys (%r)" % (control[1],))
        for interp, probe in (("python3", "e16_env.py"), ("bash", "e16_env.sh"),
                              ("node", "e16_env.js")):
            if interp not in r.interpreters:
                lines.append("%-8s    : not configured on this host" % interp)
                continue
            _b, got = _probe_run(r, c, probe, interp, problems)
            if got is None:
                continue
            want = sorted(set(_SCRIPT_ENV_KEYS) | set(E_ENV_ADDITIONS[interp]))
            have = sorted(got.get("keys") or [])
            if have != want:
                problems.append("%s: keys %r, want exactly %r" % (interp, have,
                                                                 want))
            lines.append("%-8s    : %s" % (interp, " ".join(have)))
        return problems, lines
    _guard(suite, GRP_E, "E16 env-exact-five-keys", live(env_keys))

    def workdir_deleted(r, c):
        problems, lines = [], []
        # controls: the hostile actions DO work outside the profile
        for mode, want in (("uchg", {"uchg_file": "ok", "uchg_dir": "ok"}),
                           ("acl", {"acl_set_file": "ok",
                                    "acl_set_dir": "ok"})):
            scratch = _scratch(fx, "e17-ctl-" + mode)
            try:
                problems += _ctl(_outside(fx, "e17_hostile_workdir.py",
                                          (mode,), cwd=scratch), want,
                                 "outside control (%s)" % mode)
            finally:
                _scrub_entry(os.path.dirname(scratch),
                             os.path.basename(scratch))

        def outcome(label, args, want_exit=0, check=None, **extra):
            body, got = _probe_run(r, c, "e17_hostile_workdir.py", label,
                                   problems, args=args, want_exit=want_exit,
                                   **extra)
            if check is not None:
                problems.extend(check(body, got))
            residue = _wr_residue(r.work_root, wait=2.0)
            problems.extend("%s: %s" % (label, p) for p in residue)
            lines.append("%-12s: %s" % (label, "removed" if not residue
                                        else "LEFT"))
        outcome("ok", ("ok",))
        outcome("nonzero", ("exit", 3), want_exit=3)
        outcome("timeout", ("sleep", 30), want_exit=None, timeoutMs=1000,
                check=lambda b, _g: [] if (b or {}).get("timedOut") is True
                else ["timeout: timedOut %r" % ((b or {}).get("timedOut"),)])
        outcome("chmod000", ("chmod000",),
                check=lambda _b, g: _cmp(g, {"chmod000": "ok"}, "chmod000"))
        outcome("uchg", ("uchg",),
                check=lambda _b, g: _cmp(g, {"uchg_file": "ok",
                                             "uchg_dir": "ok"}, "uchg"))

        def acl_check(_b, got):
            out = _cmp(got, {"acl_set_file": "EPERM", "acl_set_dir": "EPERM"},
                       "acl")
            for key in ("chmod_acl_file", "chmod_acl_dir"):
                if (got or {}).get(key) == "ok":
                    out.append("acl: %s succeeded" % key)
            return out
        if sys.platform == "darwin":
            outcome("acl", ("acl",), check=acl_check)
        # the sbx refusal outcome: a private sbx copy that refuses (exit 3)
        copy = fx.sbx_copy("e17", 0o755)
        r2 = _Runner(fx, "e17", sbx=copy)
        try:
            body = _body(r2.run(_sh("echo e17-control\n")), "refusal control",
                         problems)
            _want(body, "refusal control", problems, exitCode=0,
                  stdout="e17-control\n")
            _write_exec(copy, "import sys\nsys.stderr.write('sbx: error: "
                        "e17 refused\\n')\nsys.exit(3)\n")
            _expect_refusal(r2.run(_sh("echo never\n")), "sandbox_unavailable",
                            "sbx refusal", problems)
            residue = _wr_residue(r2.work_root, wait=2.0)
            problems.extend("sbx refusal: %s" % p for p in residue)
            lines.append("%-12s: %s" % ("sbx refusal", "removed" if not residue
                                        else "LEFT"))
        finally:
            r2.close()
        cap = r.caps("after the hostile workdirs", problems)
        if "degraded" not in cap or cap.get("degraded") is not None:
            problems.append("capabilities degraded %r, want null"
                            % (cap.get("degraded", "<absent>"),))
        return problems, lines
    _guard(suite, GRP_E, "E17 workdir-deleted-every-outcome",
           live(workdir_deleted))

    def busy(r, c):
        problems, lines = [], []
        markers = [_marker("e18a"), _marker("e18b")]
        ids = [r.run_async(_probe_params("e18_sleeper.py", (m, 30),
                                         timeoutMs=30000)) for m in markers]
        try:
            for m in markers:
                if not _wait_targets(m):
                    problems.append("a slot holder never showed up in ps")
            cap = r.caps("both slots held", problems)
            if (cap.get("concurrency") or {}).get("inFlight") \
                    != MAX_CONCURRENT:
                problems.append("concurrency %r, want inFlight %d (the "
                                "control: both slots ARE held)"
                                % (cap.get("concurrency"), MAX_CONCURRENT))
            started = time.monotonic()
            res = r.run(_probe_params("e18_sleeper.py", ("e18-third", 0)))
            took = time.monotonic() - started
            _expect_refusal(res, "busy", "third run", problems)
            retry = ((_payload(res) or {}).get("error") or {}).get(
                "retryAfterMs")
            if retry != 1000:
                problems.append("busy: retryAfterMs %r, want 1000" % (retry,))
            if took > BUSY_S:
                problems.append("busy took %.2fs, want < %.1fs (never "
                                "queued)" % (took, BUSY_S))
            lines.append("busy in     : %.3fs" % took)
        finally:
            for msg_id in ids:
                r.cancel(msg_id, "e18")
            for m in markers:
                pgids = {p[1] for p in _target_procs(m)}
                if pgids and _wait_groups_gone(pgids, KILL_GRACE_S + 1.0) \
                        is None:
                    problems.append("a cancelled slot holder outlived its "
                                    "cancel")
                _kill_marker(m)
        problems += _wr_residue(r.work_root, wait=CLEANUP_DEADLINE_S + 2.0)
        body = _body(r.run(_py('print("e18-after")\n')), "after", problems)
        _want(body, "after", problems, exitCode=0, stdout="e18-after\n")
        return problems, lines
    _guard(suite, GRP_E, "E18 concurrent-limit-busy-immediately", live(busy))


def _run_e_risk(suite, fx, live):
    """E19-E34: the risk rows (R3:L3)."""

    def fd_inherit(r, c):
        problems = []
        rd, wr = os.pipe()
        try:
            control = _outside(fx, "e19_fd_inherit.py", pass_fds=(wr,))
        finally:
            os.close(rd)
            os.close(wr)
        if wr not in ((control[1] or {}).get("open_fds") or []):
            problems.append("outside control: the passed fd %d was not seen "
                            "(%r)" % (wr, control[1]))
        _b, got = _probe_run(r, c, "e19_fd_inherit.py", "inside", problems)
        problems += _cmp(got, {"open_fds": [0, 1, 2]}, "inside")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E19 fd-inheritance-only-stdio", live(fd_inherit))

    def no_tty(r, c):
        problems = []
        control = _outside(fx, "e20_no_tty.py", argv=[
            sys.executable, "-I", "-B", "-c", _E_PTY_HELPER])
        problems += _ctl(control, {"dev_tty": "ok",
                                   "isatty": [True, True, True]},
                         "outside control (on a pty)")
        _b, got = _probe_run(r, c, "e20_no_tty.py", "inside", problems)
        if got is not None and got.get("dev_tty") == "ok":
            problems.append("inside: /dev/tty opened")
        problems += _cmp(got, {"isatty": [False, False, False]}, "inside")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E20 no-tty", live(no_tty))

    def exec_planted(r, c):
        problems, lines = [], []
        name, data, control = _planted_binary(fx)
        files, args = (), ()
        if name is None:
            lines.append("planted bin : not measured -- %s" % control)
            control = _outside(fx, "e21_exec_planted.py")
            problems += _ctl(control, {"planted_script": "ok",
                                       "bin_echo": "ok"})
        else:
            problems += _ctl(control, {"planted_binary": "ok",
                                       "planted_script": "ok",
                                       "bin_echo": "ok"})
            files = ({"path": name, "content": _b64(data),
                      "encoding": "base64", "executable": True},)
            args = (name,)
        _b, got = _probe_run(r, c, "e21_exec_planted.py", "inside", problems,
                             args=args, files=files)
        want = {"planted_script": "exec EPERM", "bin_echo": "ok"}
        if name is not None:
            want["planted_binary"] = "exec EPERM"
        problems += _cmp(got, want, "inside")
        return problems, lines + ["inside      : %r" % (got,),
                                  "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E21 exec-own-binary-denied", live(exec_planted))

    def signal_runner(r, c):
        problems, lines = [], []
        helper = subprocess.Popen([sys.executable, "-c",
                                   "import time; time.sleep(60)"],
                                  stdin=subprocess.DEVNULL)
        try:
            control = _outside(fx, "e22_signal.py", (helper.pid, helper.pid))
        finally:
            helper.kill()
            helper.wait()
        problems += _ctl(control, {"runner": "ok", "sibling": "ok",
                                   "own_job": "ok", "own_job_status": -15})
        marker = _marker("e22")
        sib_id = r.run_async(_probe_params("e18_sleeper.py", (marker, 30),
                                           timeoutMs=30000))
        try:
            found = _wait_targets(marker)
            if not found:
                return problems + ["the sibling run's target never showed up "
                                   "in ps"], lines
            sibling = found[0][0]
            _b, got = _probe_run(r, c, "e22_signal.py", "inside", problems,
                                 args=(r.wire.proc.pid, sibling))
            problems += _denied(got, ("runner", "parent"), "inside")
            # T10: (target same-sandbox) -- whether a CONCURRENT run's own
            # sandbox-exec instance counts as the same sandbox was UNVERIFIED
            # in M0; this half is that measurement, and "ok" is a bug.
            problems += _denied(got, ("sibling",), "inside (concurrent run)")
            problems += _cmp(got, {"own_job": "ok", "own_job_status": -15},
                             "inside control")
            if not _target_procs(marker):
                problems.append("the sibling run died during the probe")
            lines += ["inside      : %r" % (got,),
                      "outside     : %r" % (control[1],)]
        finally:
            r.cancel(sib_id, "e22")
            pgids = {p[1] for p in _target_procs(marker)}
            if pgids:
                _wait_groups_gone(pgids, KILL_GRACE_S + 1.0)
            _kill_marker(marker)
        problems += _wr_residue(r.work_root, wait=CLEANUP_DEADLINE_S + 2.0)
        return problems, lines
    _guard(suite, GRP_E, "E22 signal-runner-denied", live(signal_runner))

    def status_forge(r, c):
        problems = []
        rd, wr = os.pipe()
        try:
            control = _outside(fx, "e23_status_forge.py", (7,),
                               pass_fds=(wr,))
            os.close(wr)
            wr = -1
            forged = os.read(rd, 4096)
        finally:
            os.close(rd)
            if wr >= 0:
                os.close(wr)
        written = (control[1] or {}).get("written") or []
        if not written or b'"event": "exit"' not in forged:
            problems.append("outside control: the forgery reached no "
                            "inherited fd (%r)" % (control[1],))
        body, got = _probe_run(r, c, "e23_status_forge.py", "inside",
                               problems, args=(7,), want_exit=7)
        problems += _cmp(got, {"written": []}, "inside")
        _want(body, "inside", problems, exitCode=7, signal=None,
              timedOut=False)
        return problems, ["inside      : %r" % (got,),
                          "outside     : written to %r" % (written,)]
    _guard(suite, GRP_E, "E23 status-fd-forge-ignored", live(status_forge))

    def cpu_rlimit(r, c):
        problems = []
        want = int(math.ceil(2000 / 1000.0)) + 1
        control = _outside(fx, "e24_cpu_rlimit.py")
        if control[1] is None or control[1].get("cpu") == [want, want]:
            problems.append("outside control: cpu %r -- the probe cannot "
                            "tell the run's limit apart"
                            % ((control[1] or {}).get("cpu"),))
        _b, got = _probe_run(r, c, "e24_cpu_rlimit.py", "inside", problems,
                             timeoutMs=2000)
        problems += _cmp(got, {"cpu": [want, want]}, "inside")
        return problems, ["inside      : %r (timeoutMs 2000)" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E24 cpu-rlimit-applied", live(cpu_rlimit))

    def fsize(r, c):
        problems = []
        size = FSIZE_BYTES + 1
        control = _outside(fx, "e25_fsize.py", (size,))
        problems += _ctl(control, {"write": "ok", "size": size})
        _b, got = _probe_run(r, c, "e25_fsize.py", "inside", problems,
                             args=(size,))
        problems += _cmp(got, {"write": "EFBIG", "size": FSIZE_BYTES,
                               "fsize": [FSIZE_BYTES, FSIZE_BYTES]}, "inside")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E25 fsize-efbig-normal-result", live(fsize))

    def nofile(r, c):
        problems = []
        control = _outside(fx, "e26_nofile.py", (1000,), argv=[
            sys.executable, "-I", "-B", "-c", _E_NOFILE_HELPER])
        problems += _ctl(control, {"opened": 1000, "stopped": "cap"})
        _b, got = _probe_run(r, c, "e26_nofile.py", "inside", problems,
                             args=(1000,))
        problems += _cmp(got, {"stopped": "EMFILE",
                               "nofile": [NOFILE_LIMIT, NOFILE_LIMIT]},
                         "inside")
        if got is not None and not (type(got.get("opened")) is int
                                    and got["opened"] < NOFILE_LIMIT):
            problems.append("inside: opened %r, want < %d"
                            % (got.get("opened"), NOFILE_LIMIT))
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E26 nofile-emfile-normal-result", live(nofile))

    def rlimit_raise(r, c):
        problems = []
        control = _outside(fx, "e27_rlimit_raise.py")
        raised = [k for k, v in ((control[1] or {}).get("raise") or {}).items()
                  if v == "ok"]
        if not raised:
            problems.append("outside control: no limit could be raised (%r) "
                            "-- the probe cannot show a raise working"
                            % (control[1],))
        _b, got = _probe_run(r, c, "e27_rlimit_raise.py", "inside", problems)
        if got is not None:
            limits, outcomes = got.get("limits") or {}, got.get("raise") or {}
            for name in ("AS", "CPU", "NPROC", "FSIZE", "NOFILE", "CORE"):
                lim = limits.get(name)
                if not (isinstance(lim, list) and len(lim) == 2
                        and lim[0] == lim[1] and lim[0] != -1):
                    problems.append("inside: %s %r, want soft == hard, finite"
                                    % (name, lim))
                if outcomes.get(name) == "ok":
                    problems.append("inside: %s was raised to unlimited"
                                    % name)
        return problems, ["inside      : %r" % (got,),
                          "outside     : raised %s" % raised]
    _guard(suite, GRP_E, "E27 rlimit-raise-denied", live(rlimit_raise))

    def proc_environ(r, c):
        # T6 on macOS, where there is no /proc: sysctl KERN_PROCARGS2 returns
        # the argv AND environment of a same-uid process. IMPL:069 -- MEASURED
        # on macOS 14.2 at the first live run: the script read the runner's
        # (MCP_PROXY_TOKEN seen). Neither (deny sysctl-read (sysctl-name
        # "kern.procargs2")) nor (sysctl-name-prefix "kern.procargs") closes
        # it; a blanket sysctl-read deny aborts node (M0:6) AND python's
        # `import ctypes`. DECLARED LIMIT (operator decision, Zoltan,
        # 2026-10-11; ADR 0031, supersedes T35's hostname/hw-model wording):
        # the row still runs the probe and still requires its positive
        # control, and records the measured leak as INFO -- never PASS, and
        # never asserted away.
        problems = []
        pid = r.wire.proc.pid
        control = _outside(fx, "e28_proc_environ.py", (pid, "MCP_PROXY_TOKEN"))
        problems += _ctl(control, {"other": "ok", "key_seen": True})
        _b, got = _probe_run(r, c, "e28_proc_environ.py", "inside", problems,
                             args=(pid, "MCP_PROXY_TOKEN"))
        if got is not None and not isinstance(got.get("key_seen"), bool):
            problems.append("inside: key_seen %r, want a bool"
                            % (got.get("key_seen"),))
        leaked = got is not None and (got.get("other") == "ok"
                                      or got.get("key_seen") is True)
        lines = ["declared    : E28 -- %s (declared limit, ADR 0031)"
                 % ("LEAK OBSERVED: the script read the runner's argv+"
                    "environment via KERN_PROCARGS2 (MCP_PROXY_TOKEN seen: "
                    "%r)" % (got.get("key_seen"),) if leaked else
                    "leak NOT observed this run"),
                 "inside      : %r" % (got,),
                 "outside     : %r" % (control[1],)]
        return problems, lines

    def proc_environ_declared():
        # A broken probe or a failed positive control stays a FAIL; only a
        # clean measurement is turned into the declared INFO row.
        problems, lines = live(proc_environ)()
        if problems:
            return problems, lines
        raise _Info(" | ".join(lines))
    _guard(suite, GRP_E, "E28 proc-environ-hidden", proc_environ_declared)

    def refused_paths(r, c):
        problems = []
        tag = os.urandom(4).hex()
        escape = "e29-escape-%s.txt" % tag
        absolute = os.path.join(r.work_root, "e29-abs-%s.txt" % tag)
        for label, path in (("dot-dot", "../" + escape),
                            ("absolute", absolute)):
            res = r.run(_probe_params("e29_refused_paths.py", files=(
                {"path": path, "content": "e29"},)))
            _expect_refusal(res, "invalid_params", label, problems)
        for path in (os.path.join(r.work_root, escape),
                     os.path.join(os.path.dirname(r.work_root), escape),
                     absolute):
            if os.path.lexists(path):
                problems.append("%s was created" % path)
        # the control: an inside path is accepted and lands in the workdir
        _b, got = _probe_run(r, c, "e29_refused_paths.py", "inside control",
                             problems, files=({"path": "e29-inside.txt",
                                               "content": "e29"},))
        if got is not None and not {"e29-inside.txt", "main.py"} <= set(
                got.get("cwd_files") or []):
            problems.append("inside control: cwd_files %r lacks "
                            "e29-inside.txt / main.py"
                            % (got.get("cwd_files"),))
        return problems, ["accepted    : %r" % (got,)]
    _guard(suite, GRP_E, "E29 dot-dot-and-absolute-files-refused",
           live(refused_paths))

    def dev_null(r, c):
        problems = []
        control = _outside(fx, "e30_dev_null.py")
        problems += _ctl(control, {"read": "ok", "write": "ok", "rdwr": "ok",
                                   "child": "ok", "dev_zero": "ok"})
        _b, got = _probe_run(r, c, "e30_dev_null.py", "inside", problems)
        problems += _cmp(got, {"read": "ok", "write": "ok", "rdwr": "ok",
                               "child": "ok"}, "inside")
        # the negative control: the probe DOES see a denial in /dev
        problems += _denied(got, ("dev_zero",), "inside control")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E30 dev-null-usable", live(dev_null))

    def keg_write(r, c):
        problems = []
        scratch = _scratch(fx, "e31-ctl")
        target = os.path.join(scratch, "file.txt")
        _write(target, "e31")
        control = _outside(fx, "e31_keg_write.py", (scratch, "--", target),
                           cwd=scratch)
        problems += _ctl(control, {"create": ["ok"], "append": ["ok"],
                                   "create_cwd": "ok"})
        _b, got = _probe_run(r, c, "e31_keg_write.py", "inside", problems)
        if got is not None:
            for path in (got.get("dirs") or []) + (got.get("files") or []):
                if path.startswith(r.work_root + "/"):
                    problems.append("inside: target %s is in the workdir"
                                    % path)
            problems += _all_denied(got.get("create"), "inside create")
            problems += _all_denied(got.get("append"), "inside append")
            problems += _cmp(got, {"create_cwd": "ok"}, "inside control")
        return problems, ["inside      : %r" % (got,)]
    _guard(suite, GRP_E, "E31 interpreter-keg-not-writable", live(keg_write))

    def sibling_stat(r, c):
        problems = []
        ancestors = [r.work_root, os.path.dirname(r.work_root)]
        siblings = [r.config, c.dir, H.repo_path("CLAUDE.md")]
        args = ancestors + ["--"] + siblings
        control = _outside(fx, "e32_sibling_stat.py", args)
        problems += _ctl(control, {"ancestor_stat": ["ok", "ok"],
                                   "sibling_stat": ["ok", "ok", "ok"],
                                   "sibling_read": ["ok", "ok"]})
        _b, got = _probe_run(r, c, "e32_sibling_stat.py", "inside", problems,
                             args=args)
        problems += _cmp(got, {"ancestor_stat": ["ok", "ok"]},
                         "inside control")
        if got is not None:
            problems += _all_denied(got.get("sibling_stat"), "inside stat")
            problems += _all_denied(got.get("sibling_read"), "inside read")
        return problems, ["inside      : %r" % (got,),
                          "siblings    : the runner config, the canary dir, "
                          "the repo's CLAUDE.md"]
    _guard(suite, GRP_E, "E32 sibling-of-ancestor-stat-denied",
           live(sibling_stat))

    def sbx_killed(r, c):
        problems, lines = [], []
        marker = _marker("e33")
        control = _outside(fx, "e33_group_sleeper.py", (marker, "0.1"))
        problems += _ctl(control, {"started": True})
        msg_id = r.run_async(_probe_params("e33_group_sleeper.py",
                                           (marker, 60), timeoutMs=60000))
        try:
            targets = _wait_targets(marker, n=2)
            sbx = [p for p in _ps_matching(marker) if SBX in p[2]]
            if len(targets) < 2 or len(sbx) != 1:
                return problems + ["the run's group (%d target(s)) or its sbx "
                                   "(%d) never showed up in ps"
                                   % (len(targets), len(sbx))], lines
            pgids = {p[1] for p in targets}
            os.kill(sbx[0][0], signal.SIGKILL)
            killed = time.monotonic()
            res = _result(r.wire.wait(msg_id, RUN_TIMEOUT))
            answered = time.monotonic() - killed
            body = _body(res, "after sbx died", problems)
            _leak_check(c, body, "after sbx died", problems)
            took = _wait_groups_gone(pgids, KILL_GRACE_S + 1.0)
            if took is None:
                problems.append("the target group %s outlived sbx's death"
                                % sorted(pgids))
            lines += ["result      : exitCode %r signal %r timedOut %r in "
                      "%.2fs" % ((body or {}).get("exitCode"),
                                 (body or {}).get("signal"),
                                 (body or {}).get("timedOut"), answered),
                      "group       : %d target(s) gone in %s"
                      % (len(targets), "%.2fs" % took if took is not None
                         else "never")]
        finally:
            _kill_marker(marker)
        problems += _wr_residue(r.work_root, wait=CLEANUP_DEADLINE_S + 2.0)
        cap = r.caps("after sbx died", problems)
        if (cap.get("concurrency") or {}).get("inFlight") != 0:
            problems.append("the slot was not released: %r"
                            % (cap.get("concurrency"),))
        body = _body(r.run(_py('print("e33-after")\n')), "after", problems)
        _want(body, "after", problems, exitCode=0, stdout="e33-after\n")
        return problems, lines
    _guard(suite, GRP_E, "E33 sbx-killed-target-group-killed-by-runner",
           live(sbx_killed))

    def setuid(r, c):
        problems = []
        control = _outside(fx, "e34_setuid.sh")
        ctl = control[1] or {}
        if control[1] is None:
            problems += _ctl(control, {})
        for name in E_SETUID:
            entry = ctl.get(name) or {}
            if entry.get("rc") == 126 or entry.get("eperm"):
                problems.append("outside control: %s %r -- the binary does "
                                "not run even unsandboxed" % (name, entry))
        _b, got = _probe_run(r, c, "e34_setuid.sh", "inside", problems)
        if got is not None:
            for name in E_SETUID:
                if got.get(name) != {"rc": 126, "eperm": True}:
                    problems.append("inside: %s %r, want sh exit 126 + "
                                    "'Operation not permitted' (the setuid "
                                    "exec refused by Seatbelt)"
                                    % (name, got.get(name)))
            problems += _cmp(got, {"true": {"rc": 0, "eperm": False}},
                             "inside control")
        return problems, ["inside      : %r" % (got,),
                          "outside     : %r" % (control[1],)]
    _guard(suite, GRP_E, "E34 setuid-exec-stays-confined", live(setuid))


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-sandbox: the sandboxed script runner",
                    opts=opts, mode="grouped")
    fx = Fixtures(keep=opts.keep)
    try:
        _run_a(suite, fx)
        _run_macho(suite, fx)
        _run_c(suite, fx)
        _run_d(suite, fx)
        _run_n(suite, fx)
        _run_f(suite, fx)
        _run_h(suite, fx)
        _run_i(suite, fx)
        _run_g(suite, fx)
        _run_e(suite, fx)
    finally:
        fx.cleanup()
        _MODULE.clear()
        _CCFG.clear()
        _NESTED.clear()
    suite.render()
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
