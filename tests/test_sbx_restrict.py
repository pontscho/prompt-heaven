#!/usr/bin/env python3
"""Suite for sbx restricted mode: `--restrict-reads`, `--read`, `--exec`,
`--clean-env`/`--env`, `--timeout`, `--status-fd` and the strict `--rlimit-*`
flags (docs/specs/spec-sbx-restricted-runner.md Step 2 onward; ADR 0031).

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run.

Why this is its own suite and not a group in test_sbx_gate.py: that suite gates
the PreToolUse gate and the legacy builders; this one gates the restricted mode
the stdio runner (Scripts/mcp-sandbox.py) drives sbx with. The helper is loaded
through test_sbx_gate._load_helper (imported, never copied), and every exit-code
row runs helper.main() in-process with exec, rlimits and engine detection
stubbed, the way test_sbx_gate._main_in does.

Every refusal row pairs the refusal with a CONTROL -- the same request spelled
correctly must be accepted -- so a row cannot pass merely because argparse
does not know a flag yet: the RED run fails on the control, for the stated
reason (flag unknown, function missing), never on an ImportError.

Groups (offline, both OSes; fixtures only under .claude/tmp/test_sbx_restrict/):
  A  parser / validation -- the flag grammar and the pure validators:
     --read/--exec need --restrict-reads, --env needs --clean-env, env keys,
     LD_/DYLD_ prefixes, duplicates, NUL values, --status-fd needs --timeout
     and is >= 3, --timeout > 0, decimal-only rlimits, the nproc +N form,
     --restrict-reads with --net, and allow_abbrev=False (C-L1).
  B  spelling / ancestors -- metadata_ancestors, canonicalize_read (normpath
     only, never realpath: KD-14) and resolve_reads (a symlink is a literal
     carrying its link value, a directory a subpath; missing refused).
  C  argv0 / cwd -- --clean-env needs an absolute argv0, restricted mode needs
     the cwd inside a scope (exit 3), and the 256-read cap.
  D  dry-run -- the plan prints the restricted fields, never an env VALUE;
     importing sbx has no side effects; the legacy builders stay byte-identical
     to the committed sbx at 4d3640c when no new flag is used.
  E  Seatbelt golden -- the restricted SBPL profile, pinned line by line for
     a typed Scope (M0-refined: (target same-sandbox), exec-dir metadata only
     when the target env can carry PATH, (deny file-write-acl ...) only, the
     depth cap as a LITERAL 100-group regex, the branch's own /dev/null
     read+write literal), plus the structural rules it must keep.
  F  bwrap golden -- the restricted argv on a usr-merged and a split-/usr
     base, no `--ro-bind / /`, reads under a base bind skipped, sorted
     --setenv after --clearenv, --chdir, the uts/cgroup unshares, carve-outs
     and out-of-scope shadow re-binds dropped, the legacy argv unchanged,
     --clearenv/--setenv kept for --clean-env without --restrict-reads.

Groups added by Step 4 (live rows run the REAL sbx in a subprocess, first one
legacy P23 control run: no output + 'sandbox_apply' on stderr = every live row
INFO; macOS rows are INFO on Linux and the Linux row INFO on macOS; every
fixture and status pipe stays under .claude/tmp/test_sbx_restrict/):
  U  supervise -- --timeout fork mode: exit 124 + the stderr marker, the
     --status-fd JSON lines (one `spawned` carrying pid == pgid, then one
     terminal `exit`), exit-code passthrough disambiguated by the status,
     128+N, the status fd never inherited, kill-before-reap (a background
     child of the target dead after a normal exit), SIGTERM to sbx kills the
     group, an engine exec failure is `refused` with no `spawned`, and the
     Linux PID-namespace teardown.
  V  strict rlimits -- soft == hard, the target cannot raise them, a refused
     limit is exit 3 with nothing run, the nproc +N form, and the CPU / FSIZE /
     NOFILE / CORE limits observed from inside the target.
  T  clean env -- the exact key set per interpreter (the ENV_ADDITIONS of
     the profile the row runs under -- M0:7 for restricted, re-measured for
     legacy -- keys only), no canary from sbx's own env, the engine env {} with the
     darwin `/usr/bin/env -i` trampoline (pure goldens of _prepare_exec), the
     legacy path unchanged, an argv0 with '=' refused on darwin.

Group added by Step 6 (live, macOS; INFO on Linux and under a nested sandbox
via the same P23 control; probes in tests/files/sbx_restrict/, each copied
into a fresh write scope and printing one JSON line):
  L  restricted Seatbelt -- runs shaped as mcp-sandbox builds them (realpath
     exec literals + python's Python.app launcher, /bin and /usr/bin exec
     dirs, symlink hops and keg roots as reads, /private/var/select/sh,
     openssl.cnf for node, a clean env with PATH): ~/.ssh, $HOME, /etc/passwd
     and a repo file denied, stat no oracle, a planted executable refused,
     the "/" listing declared (INFO), python3 / bash / node run, ACL writes
     denied while flags, copy2/copytree, write and chmod +x work in the
     write scope (M0:8), and the 100-level depth cap (M0:9).

Group added by Step 5 (pure; the gate is imported via test_sbx_gate._load_gate):
  S  gate grammar -- the new flags auto-allow only when they narrow, both
     `--flag VALUE` and `--flag=VALUE`; a bad env key, LD_/DYLD_, an rlimit
     above sbx's default, a relative --read/--exec, --net and --status-fd
     prompt; the gate's flag tables equal sbx's parser option strings.

Group added by the security review round 1 -> 2 fix pass (pure or in-process,
both OSes; each refusal beside its control):
  X  P1 a cwd whose name is an SBPL payload is refused (restricted and legacy,
     --dry-run included) and the Seatbelt builder refuses any interpolated path
     carrying an injection character; P2 a --read/--exec that resolves -- at a
     hop or finally -- under a socket directory (incl. /var/run), or to a
     socket, is refused; R1 a strict rlimit above the current soft limit is
     refused, never raised; M1 a status record with a long json-escaping
     reason still fits and ends in `}\\n`; G3 `~+` / `~-` / `~user` write
     scopes are refused; P4 the --env help says values travel in argv.

Group added by the egress work (domain-allowlisted network for mcp-sandbox
runs; the allowlisting CONNECT proxy itself is mcp-sandbox's, so this group
gates only sbx's half -- one endpoint the sandbox may reach):
  N  egress flags -- --egress-port (Seatbelt) / --egress-socket (bwrap) need
     --restrict-reads, refuse --net and each other, and each is refused on
     the other platform (exit 3); the port grammar; the socket must be the
     caller's own socket in a 0700 directory, no symlink hop, never under a
     socket directory; the Seatbelt golden keeps (deny network*) and adds ONE
     (allow network-outbound (remote tcp "localhost:N")) after it (M0: a
     "127.0.0.1" host is refused by the SBPL compiler); the bwrap golden
     binds the one socket at /.sbx-egress.sock after the socket tmpfs, keeps
     --unshare-net and wraps the target in the stdlib forwarder (127.0.0.1:
     3128 -> the socket); the forwarder relays, passes the target's exit
     code and dies with it; live: Seatbelt reaches only 127.0.0.1:N (no DNS,
     no other port, no outside address, no mDNSResponder socket), and bwrap
     reaches the bound socket through the forwarder only (Linux, INFO here
     otherwise). S17 pins the gate side: both flags always prompt.

Usage:
  python3 tests/test_sbx_restrict.py [--brief] [--keep]
Exit code 0 iff every case passes.
"""

import grp
import io
import json
import os
import pwd
import random
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402
import test_sbx_gate as G  # noqa: E402  (the one helper loader, P16)

NAME = "sbx_restrict"
HELPER = H.repo_path("ClaudeCode", "skills", "sandbox-run", "scripts", "sbx")
FIXTURE_ROOT = H.repo_path(".claude", "tmp", "test_sbx_restrict")

GRP_A = "A. parser / validation"
GRP_B = "B. spelling / ancestors"
GRP_C = "C. argv0 / cwd"
GRP_D = "D. dry-run / legacy"
GRP_E = "E. Seatbelt golden"
GRP_F = "F. bwrap golden"
GRP_U = "U. supervise (live)"
GRP_V = "V. strict rlimits (live)"
GRP_T = "T. clean env"
GRP_L = "L. restricted Seatbelt (live)"
GRP_S = "S. gate grammar"
GRP_X = "X. security review round 1"
GRP_N = "N. egress flags"

# P1's payload: a directory NAME that closes the SBPL string literal it is
# interpolated into and appends a write-anywhere rule of its own.
INJECT_DIR = ('a"))(allow file-write* (regex #"^"))'
              '(allow file-read-metadata (literal "b')

# The pure layer Step 2 adds to sbx (the plan's names; D3 asserts they exist).
PURE_API = ("canonicalize_read", "parse_env", "parse_limits", "parse_timeout",
            "parse_status_fd", "metadata_ancestors", "validate_restricted",
            "resolve_reads", "linux_base_reads")

# The fields --dry-run must print in restricted mode (Step 2, _format_plan).
PLAN_FIELDS = ("restrict_reads", "read_rules", "meta_literals", "exec_rules",
               "env", "timeout", "status_fd", "limits")

READ_CAP = 256
ARGV0 = "/bin/echo"       # absolute on both OSes (usr-merged Linux: a symlink)
GIB = str(1024 ** 3)

# ---------------------------------------------------------------------------
# D4's pinned fixture: what the COMMITTED sbx (4d3640c) builds for one fixed
# legacy Scope. Typed here, never derived from the helper under test.
# ---------------------------------------------------------------------------
LEGACY_SCOPE = dict(writes=["/w"], net=False, ro=False, argv=["true", "x"],
                    secret_paths=(("/h/.ssh", True), ("/r/.git/config", False)),
                    carveouts=("/h/.claude/skills",),
                    shadow_write_denies=("/r/ClaudeCode",),
                    file_mask_fds=(5,))
LEGACY_SEATBELT = ["sandbox-exec", "-p", "\n".join([
    "(version 1)",
    "(deny default)",
    "(allow process-exec)",
    "(allow process-fork)",
    "(allow signal (target self))",
    "(allow sysctl-read)",
    '(allow file-read* (subpath "/"))',
    '(allow file-write* (subpath "/w"))',
    '(allow file-write* (literal "/dev/null"))',
    '(deny file-read* (subpath "/h/.ssh"))',
    '(deny file-write* (subpath "/h/.ssh"))',
    '(allow file-read* (subpath "/h/.claude/skills"))',
    '(deny file-read* (subpath "/r/.git/config"))',
    '(deny file-write* (subpath "/r/.git/config"))',
    '(deny file-write* (subpath "/r/ClaudeCode"))',
    "(deny network*)",
]), "true", "x"]
LEGACY_SEATBELT_RO_NET = ["sandbox-exec", "-p", "\n".join([
    "(version 1)",
    "(deny default)",
    "(allow process-exec)",
    "(allow process-fork)",
    "(allow signal (target self))",
    "(allow sysctl-read)",
    '(allow file-read* (subpath "/"))',
    '(allow file-write* (literal "/dev/null"))',
    '(deny file-read* (subpath "/h/.ssh"))',
    '(deny file-write* (subpath "/h/.ssh"))',
    '(allow file-read* (subpath "/h/.claude/skills"))',
    '(deny file-read* (subpath "/r/.git/config"))',
    '(deny file-write* (subpath "/r/.git/config"))',
    '(deny file-write* (subpath "/r/ClaudeCode"))',
    "(allow network*)",
]), "true", "x"]
LEGACY_BWRAP = [
    "bwrap", "--ro-bind", "/", "/",
    "--dev", "/dev", "--remount-ro", "/dev",
    "--tmpfs", "/run", "--tmpfs", "/tmp", "--tmpfs", "/var/snap",
    "--bind", "/w", "/w",
    "--tmpfs", "/h/.ssh",
    "--ro-bind", "/h/.claude/skills", "/h/.claude/skills",
    "--ro-bind", "/r/ClaudeCode", "/r/ClaudeCode",
    "--ro-bind-data", "5", "/r/.git/config",
    "--remount-ro", "/run", "--remount-ro", "/tmp", "--remount-ro", "/var/snap",
    "--unshare-pid", "--unshare-ipc", "--proc", "/proc",
    "--unshare-net",
    "--new-session",
    "--die-with-parent",
    "--", "true", "x",
]
# The Scope fields Step 2 adds, at the defaults that must change NOTHING.
NEW_FIELD_DEFAULTS = dict(restrict_reads=False, read_rules=(), meta_literals=(),
                          exec_rules=(), linux_base=(), env=None, timeout=None,
                          status_fd=None, limits=(), nproc_headroom=None)

# ---------------------------------------------------------------------------
# E's pinned fixture: the restricted Seatbelt profile Step 3 must build for one
# fixed Scope (docs/specs/spec-sbx-restricted-runner.md Step 3, refined by M0).
# Typed here, never derived from the helper under test. The Scope mirrors what
# mcp-sandbox passes for a framework python (M0:3): the exec literals are
# REALPATHS plus the Python.app launcher, /bin and /usr/bin are exec DIRS, a
# symlinked parent dir (/usr/local/opt/node) is a READ literal, and the write
# scope's spelling carries "." and "+" so the depth-cap escaping is pinned.
# ---------------------------------------------------------------------------
SB_WRITE = "/wd/run.1+x"
SB_WRITE_RE = "/wd/run\\.1\\+x"          # every regex metacharacter escaped
SB_ARGV0 = "/opt/py/bin/python3"
SB_EXEC_RULES = (
    (SB_ARGV0, False),
    ("/opt/py/Versions/3.12/bin/python3.12", False),
    ("/opt/py/Versions/3.12/Resources/Python.app/Contents/MacOS/Python", False),
    ("/bin", True),
    ("/usr/bin", True),
)
SB_READ_RULES = (
    (SB_ARGV0, "symlink", "../Versions/3.12/bin/python3.12"),
    ("/opt/py/Versions/3.12", "dir", None),
    ("/private/var/select/sh", "file", None),
    ("/usr/local/opt/node", "symlink", "../Cellar/node/22.1.0"),
)
SB_META = ("/", "/opt", "/opt/py", "/opt/py/bin", "/opt/py/Versions",
           "/private", "/private/var", "/private/var/select", "/usr",
           "/usr/local", "/usr/local/opt", "/wd")
SB_ENV_PATH = {"PATH": "/usr/bin:/bin", "A": "1"}
SB_ENV_NO_PATH = {"A": "1"}
SB_SCOPE = dict(writes=[SB_WRITE], net=False, ro=False,
                argv=[SB_ARGV0, "x.py"],
                secret_paths=(("/h/.ssh", True), ("/r/.git/config", False)),
                carveouts=("/h/.claude/skills",),
                shadow_write_denies=("/r/ClaudeCode",),
                cwd=SB_WRITE, restrict_reads=True, read_rules=SB_READ_RULES,
                meta_literals=SB_META, exec_rules=SB_EXEC_RULES,
                env=SB_ENV_PATH)
SB_DEPTH_GROUPS = 100
SB_DEPTH_CAP = ('(deny file-write-create (regex #"^%s/%s"))'
                % (SB_WRITE_RE, "[^/]+/" * SB_DEPTH_GROUPS))
SB_DEV_NULL = '(allow file-read* file-write* (literal "/dev/null"))'
SB_EXEC_LITERALS = ('(literal "/opt/py/Versions/3.12/bin/python3.12") '
                    '(literal "/opt/py/Versions/3.12/Resources/Python.app/'
                    'Contents/MacOS/Python") '
                    '(subpath "/bin") (subpath "/usr/bin"))')
# the exec rule: argv0 spelling first (inert, M0:3), then /usr/bin/env when
# the env is clean (the KD-5 trampoline), then the --exec files, then dirs
SB_EXEC_CLEAN = ('(allow process-exec (literal "%s") (literal "/usr/bin/env") '
                 % SB_ARGV0) + SB_EXEC_LITERALS
SB_EXEC_INHERIT = ('(allow process-exec (literal "%s") ' % SB_ARGV0) \
    + SB_EXEC_LITERALS
SB_EXEC_DIR_META = ('(allow file-read-metadata (subpath "/bin") '
                    '(subpath "/usr/bin"))')


def _sb_golden(exec_line, with_dir_meta):
    """The pinned restricted profile, one line per rule. The two variant
    lines are the exec rule (with or without the /usr/bin/env trampoline
    literal) and the exec-dir metadata rule (only when the target env can
    carry PATH, M0:4); everything else is fixed text."""
    lines = [
        "(version 1)",
        "(deny default)",
        exec_line,
        "(allow process-fork)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        '(allow file-read-data (literal "/"))',
        "(allow file-read-metadata %s)"
        % " ".join('(literal "%s")' % p for p in SB_META),
    ]
    if with_dir_meta:
        lines.append(SB_EXEC_DIR_META)
    lines += [
        '(allow file-read* (literal "/opt/py/bin/python3") '
        '(literal "/private/var/select/sh") (literal "/usr/local/opt/node"))',
        '(allow file-read* (subpath "/opt/py/Versions/3.12"))',
        '(allow file-read* (subpath "%s"))' % SB_WRITE,
        '(allow file-write* (subpath "%s"))' % SB_WRITE,
        '(deny file-write-acl (subpath "%s"))' % SB_WRITE,
        SB_DEPTH_CAP,
        SB_DEV_NULL,
        '(deny file-read* (subpath "/h/.ssh"))',
        '(deny file-write* (subpath "/h/.ssh"))',
        '(deny file-read* (subpath "/r/.git/config"))',
        '(deny file-write* (subpath "/r/.git/config"))',
        '(deny file-write* (subpath "/r/ClaudeCode"))',
        "(deny network*)",
    ]
    return lines


# (label, env, golden lines): E1's three env variants (M0:4 + KD-5)
SB_VARIANTS = (
    ("clean env with PATH", SB_ENV_PATH, _sb_golden(SB_EXEC_CLEAN, True)),
    ("inherited env", None, _sb_golden(SB_EXEC_INHERIT, True)),
    ("clean env without PATH", SB_ENV_NO_PATH,
     _sb_golden(SB_EXEC_CLEAN, False)),
)

# ---------------------------------------------------------------------------
# F's pinned fixtures: the restricted bwrap argv for an Ubuntu 22.04
# (usr-merged) and a split-/usr base, in the linux_base_reads() shape.
# ---------------------------------------------------------------------------
BW_ARGV0 = "/usr/bin/python3"
BW_BASE_MERGED = (
    ("ro-bind", "/usr"),
    ("symlink", "usr/lib", "/lib"),
    ("symlink", "usr/lib64", "/lib64"),
    ("symlink", "usr/lib32", "/lib32"),
    ("symlink", "usr/bin", "/bin"),
    ("symlink", "usr/sbin", "/sbin"),
    ("ro-bind", "/etc/ld.so.cache"),
    ("ro-bind", "/etc/ld.so.conf"),
    ("ro-bind", "/etc/ld.so.conf.d"),
    ("symlink", "/usr/share/zoneinfo/Etc/UTC", "/etc/localtime"),
    ("ro-bind", "/etc/ssl/certs"),
    ("ro-bind", "/etc/alternatives"),
)
BW_BASE_SPLIT = (
    ("ro-bind", "/usr"),
    ("ro-bind", "/lib"),
    ("ro-bind", "/lib64"),
    ("ro-bind", "/bin"),
    ("ro-bind", "/sbin"),
    ("ro-bind", "/etc/ld.so.cache"),
    ("ro-bind", "/etc/ld.so.conf"),
    ("ro-bind", "/etc/ld.so.conf.d"),
    ("ro-bind", "/etc/localtime"),
    ("ro-bind", "/etc/ssl/certs"),
    ("ro-bind", "/etc/alternatives"),
)
# reads under a base bind are already visible and must not be re-bound (F4)
BW_COVERED = ("/usr/lib/python3.10/os.py", "/etc/ssl/certs/ca.pem")
BW_READ_RULES = (
    ("/opt/app", "dir", None),
    (BW_COVERED[0], "file", None),
    ("/opt/link", "symlink", "app/bin"),
    (BW_COVERED[1], "file", None),
)
BW_ENV = {"PATH": "/usr/bin:/bin", "A": "1"}
BW_SCOPE_MERGED = dict(writes=["/w"], net=False, ro=False,
                       argv=[BW_ARGV0, "x"],
                       secret_paths=(("/h/.ssh", True),
                                     ("/r/.git/config", False)),
                       carveouts=("/h/.claude/skills",),
                       shadow_write_denies=("/r/ClaudeCode", "/w/sub"),
                       file_mask_fds=(5,), cwd="/w", restrict_reads=True,
                       read_rules=BW_READ_RULES, linux_base=BW_BASE_MERGED,
                       exec_rules=((BW_ARGV0, False),), env=BW_ENV)
BW_SCOPE_SPLIT = dict(writes=["/w"], net=False, ro=False,
                      argv=[BW_ARGV0, "x"], cwd="/w", restrict_reads=True,
                      read_rules=(("/opt/app", "dir", None),),
                      linux_base=BW_BASE_SPLIT,
                      exec_rules=((BW_ARGV0, False),))
BW_SOCKETS = ["--tmpfs", "/run", "--tmpfs", "/tmp", "--tmpfs", "/var/snap"]
BW_SOCKETS_RO = ["--remount-ro", "/run", "--remount-ro", "/tmp",
                 "--remount-ro", "/var/snap"]
BW_TAIL_NS = ["--unshare-pid", "--unshare-ipc", "--proc", "/proc",
              "--unshare-net",
              "--unshare-uts", "--hostname", "sbx", "--unshare-cgroup-try",
              "--new-session", "--die-with-parent"]
BW_GOLDEN_MERGED = [
    "bwrap",
    "--ro-bind", "/usr", "/usr",
    "--symlink", "usr/lib", "/lib",
    "--symlink", "usr/lib64", "/lib64",
    "--symlink", "usr/lib32", "/lib32",
    "--symlink", "usr/bin", "/bin",
    "--symlink", "usr/sbin", "/sbin",
    "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
    "--ro-bind", "/etc/ld.so.conf", "/etc/ld.so.conf",
    "--ro-bind", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d",
    "--symlink", "/usr/share/zoneinfo/Etc/UTC", "/etc/localtime",
    "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
    "--ro-bind", "/etc/alternatives", "/etc/alternatives",
    "--ro-bind", "/opt/app", "/opt/app",
    "--symlink", "app/bin", "/opt/link",
    "--dev", "/dev", "--remount-ro", "/dev",
] + BW_SOCKETS + [
    "--bind", "/w", "/w",
    "--tmpfs", "/h/.ssh",
    "--ro-bind", "/w/sub", "/w/sub",
    "--ro-bind-data", "5", "/r/.git/config",
] + BW_SOCKETS_RO + BW_TAIL_NS + [
    "--clearenv", "--setenv", "A", "1", "--setenv", "PATH", "/usr/bin:/bin",
    "--chdir", "/w",
    "--", BW_ARGV0, "x",
]
BW_GOLDEN_SPLIT = [
    "bwrap",
    "--ro-bind", "/usr", "/usr",
    "--ro-bind", "/lib", "/lib",
    "--ro-bind", "/lib64", "/lib64",
    "--ro-bind", "/bin", "/bin",
    "--ro-bind", "/sbin", "/sbin",
    "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
    "--ro-bind", "/etc/ld.so.conf", "/etc/ld.so.conf",
    "--ro-bind", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d",
    "--ro-bind", "/etc/localtime", "/etc/localtime",
    "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
    "--ro-bind", "/etc/alternatives", "/etc/alternatives",
    "--ro-bind", "/opt/app", "/opt/app",
    "--dev", "/dev", "--remount-ro", "/dev",
] + BW_SOCKETS + [
    "--bind", "/w", "/w",
] + BW_SOCKETS_RO + BW_TAIL_NS + [
    "--chdir", "/w",
    "--", BW_ARGV0, "x",
]


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
    the RED run has to report every case, not stop at the first AttributeError."""
    saved = sys.stderr
    try:
        problems, extra = fn()
    except Exception as exc:              # noqa: BLE001 -- reported, not hidden
        problems, extra = ["raised %s: %s" % (type(exc).__name__, exc)], ()
    except SystemExit as exc:             # a stray exit must not end the suite
        problems, extra = ["exited %r outside a driver" % (exc.code,)], ()
    finally:
        sys.stderr = saved
    _rec(suite, group, name, problems, extra)


def _parse(helper, argv):
    """helper's parser on `argv`; a parser exit becomes a ValueError (FAIL)."""
    code, args, err = _call(helper._build_parser().parse_args, list(argv))
    if code is not None:
        raise ValueError("parser exited %r on %r: %s" % (code, argv,
                                                         _first(err)))
    return args


# ---------------------------------------------------------------------------
# in-process drivers
# ---------------------------------------------------------------------------

def _cli(helper, cwd, argv):
    """helper.main(argv) as if started in `cwd`, exec / rlimits / engine / mask
    fds stubbed (the test_sbx_gate._main_in shape, plus stdout):
    (rc, stdout, stderr, execs)."""
    execs = []
    saved = (os.getcwd, os.execvp, helper._apply_rlimits, helper._detect_engine,
             helper._file_mask_fds, sys.stdout, sys.stderr)

    def _exec(path, args):
        execs.append(list(args))
        raise OSError("exec stubbed by the suite")
    os.getcwd = lambda: cwd
    os.execvp = _exec
    helper._apply_rlimits = lambda *a, **k: None
    helper._detect_engine = lambda: (helper._bwrap_argv, "/usr/bin/bwrap")
    helper._file_mask_fds = lambda paths: tuple(
        range(100, 100 + sum(1 for _p, d in paths or () if not d)))
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        try:
            rc = helper.main(list(argv))
        except SystemExit as exc:
            rc = exc.code
        except Exception as exc:          # noqa: BLE001 -- a crash is a finding
            rc = "raised %s: %s" % (type(exc).__name__, exc)
        out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        (os.getcwd, os.execvp, helper._apply_rlimits, helper._detect_engine,
         helper._file_mask_fds, sys.stdout, sys.stderr) = saved
    return rc, out, err, execs


def _call(fn, *args, **kw):
    """Call a pure-layer function, stderr captured: (exit_code, value, stderr);
    exit_code is None when it returned normally."""
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        try:
            return None, fn(*args, **kw), sys.stderr.getvalue()
        except SystemExit as exc:
            return exc.code, None, sys.stderr.getvalue()
    finally:
        sys.stderr = saved


def _accepted(helper, fx, flags, label, cwd=None, target=(ARGV0, "x")):
    """CONTROL: a restricted dry-run with `flags` must exit 0 with a plan."""
    rc, out, err, execs = _cli(helper, cwd or fx["w"],
                               ["--restrict-reads", "--write", fx["w"],
                                "--dry-run"] + list(flags) + ["--"]
                               + list(target))
    problems = []
    if rc != 0:
        problems.append("control %s: rc %r, want 0 (%s)"
                        % (label, rc, _first(err)))
    elif "sbx plan" not in out:
        problems.append("control %s: no plan printed" % label)
    if execs:
        problems.append("control %s: exec'd on a dry run" % label)
    return problems


def _refused(helper, fx, flags, label, needle=None, want=2, restrict=True,
             cwd=None, target=(ARGV0, "x")):
    """The same request spelled wrong must exit `want`, print no plan, exec
    nothing, and (when `needle` is given) name it on stderr."""
    head = ["--restrict-reads"] if restrict else []
    rc, out, err, execs = _cli(helper, cwd or fx["w"],
                               head + ["--write", fx["w"], "--dry-run"]
                               + list(flags) + ["--"] + list(target))
    problems = []
    if rc != want:
        problems.append("%s: rc %r, want %d" % (label, rc, want))
    if "sbx plan" in out:
        problems.append("%s: printed a plan" % label)
    if execs:
        problems.append("%s: exec'd %r" % (label, execs))
    if "sbx: error" not in err:
        problems.append("%s: no 'sbx: error' line on stderr" % label)
    if needle and needle not in err:
        problems.append("%s: stderr does not name %r: %s"
                        % (label, needle, _first(err)))
    return problems


def _first(err):
    """The error line of a stderr (argparse prints its usage line first)."""
    lines = [ln for ln in (err or "").splitlines() if ln.strip()]
    errs = [ln for ln in lines if "error" in ln]
    return (errs or lines or ["<no stderr>"])[0][:160]


def _pair(helper, fx, good, bad, label, needle=None, **kw):
    """One control + one refusal: the row's usual shape."""
    return (_accepted(helper, fx, good, label + " (control)")
            + _refused(helper, fx, bad, label, needle=needle, **kw)), []


# ---------------------------------------------------------------------------
# fixtures (only under .claude/tmp/test_sbx_restrict/run-<unique>/)
# ---------------------------------------------------------------------------

def _make_fixtures():
    os.makedirs(FIXTURE_ROOT, exist_ok=True)
    root = os.path.realpath(tempfile.mkdtemp(prefix="run-", dir=FIXTURE_ROOT))
    fx = {"root": root}
    for key, rel in (("w", "w"), ("r", "r"), ("nested", "a/b/c"),
                     ("outside", "outside"), ("many", "many")):
        fx[key] = os.path.join(root, rel)
        os.makedirs(fx[key], exist_ok=True)
    fx["file"] = os.path.join(fx["r"], "file.txt")
    with open(fx["file"], "w") as fh:
        fh.write("tf\n")
    fx["link"] = os.path.join(root, "link")
    os.symlink(fx["file"], fx["link"])
    fx["missing"] = os.path.join(root, "missing")
    fx["many_files"] = []
    for i in range(READ_CAP + 1):
        path = os.path.join(fx["many"], "f%03d" % i)
        with open(path, "w"):
            pass
        fx["many_files"].append(path)
    return fx


# ---------------------------------------------------------------------------
# A: parser / validation
# ---------------------------------------------------------------------------

def _run_a(suite, helper, fx):
    def a1():
        return (_accepted(helper, fx, ["--read", fx["r"]], "--read")
                + _refused(helper, fx, ["--read", fx["r"]],
                           "--read without --restrict-reads",
                           needle="--restrict-reads", restrict=False)), []
    _guard(suite, GRP_A, "A1 read-without-restrict-refused", a1)

    def a2():
        return (_accepted(helper, fx, ["--exec", "/bin"], "--exec")
                + _refused(helper, fx, ["--exec", "/bin"],
                           "--exec without --restrict-reads",
                           needle="--restrict-reads", restrict=False)), []
    _guard(suite, GRP_A, "A2 exec-without-restrict-refused", a2)

    def a3():
        return _pair(helper, fx, ["--clean-env", "--env", "A=1"],
                     ["--env", "A=1"], "--env without --clean-env",
                     needle="--clean-env")
    _guard(suite, GRP_A, "A3 env-without-clean-env-refused", a3)

    def a4():
        problems = _accepted(helper, fx, ["--clean-env", "--env", "_A9=1"],
                             "key _A9")
        for spec in ("1A=1", "A-B=1", "=1", "A", "A B=1", "K" * 129 + "=1"):
            problems += _refused(helper, fx, ["--clean-env", "--env", spec],
                                 "--env %r" % spec[:20])
        return problems, []
    _guard(suite, GRP_A, "A4 env-bad-key-refused", a4)

    def a5():
        return _pair(helper, fx, ["--clean-env", "--env", "PRELOAD=1"],
                     ["--clean-env", "--env", "LD_PRELOAD=/x.so"],
                     "--env LD_PRELOAD", needle="LD_")
    _guard(suite, GRP_A, "A5 env-ld-preload-refused", a5)

    def a6():
        return _pair(helper, fx, ["--clean-env", "--env", "INSERT=1"],
                     ["--clean-env", "--env", "DYLD_INSERT_LIBRARIES=/x"],
                     "--env DYLD_INSERT_LIBRARIES", needle="DYLD_")
    _guard(suite, GRP_A, "A6 env-dyld-refused", a6)

    def a7():
        return _pair(helper, fx, ["--clean-env", "--env", "A=1", "--env", "B=1"],
                     ["--clean-env", "--env", "A=1", "--env", "A=2"],
                     "duplicate --env A")
    _guard(suite, GRP_A, "A7 env-duplicate-key-refused", a7)

    def a8():
        return _pair(helper, fx, ["--clean-env", "--env", "A=x y"],
                     ["--clean-env", "--env", "A=x\x00y"], "NUL in --env value")
    _guard(suite, GRP_A, "A8 env-nul-value-refused", a8)

    def a9():
        return _pair(helper, fx, ["--timeout", "5", "--status-fd", "3"],
                     ["--status-fd", "3"], "--status-fd without --timeout",
                     needle="--timeout")
    _guard(suite, GRP_A, "A9 status-fd-without-timeout-refused", a9)

    def a10():
        problems = _accepted(helper, fx, ["--timeout", "5", "--status-fd", "9"],
                             "--status-fd 9")
        for fd in ("2", "1", "0", "-1", "3x", "12345"):
            problems += _refused(helper, fx, ["--timeout", "5",
                                              "--status-fd", fd],
                                 "--status-fd %s" % fd)
        return problems, []
    _guard(suite, GRP_A, "A10 status-fd-below-3-refused", a10)

    def a11():
        problems = _accepted(helper, fx, ["--timeout", "0.5"], "--timeout 0.5")
        for t in ("0", "0.0", "-1", "86401", "1e3", "1.2345", "nan"):
            problems += _refused(helper, fx, ["--timeout", t],
                                 "--timeout %s" % t)
        return problems, []
    _guard(suite, GRP_A, "A11 timeout-zero-refused", a11)

    def a12():
        problems = _accepted(helper, fx, ["--rlimit-as", GIB,
                                          "--rlimit-cpu", "10"], "--rlimit-as")
        for flag, val in (("--rlimit-as", "1e9"), ("--rlimit-as", "0x10"),
                          ("--rlimit-as", "12a"), ("--rlimit-as", " 100"),
                          ("--rlimit-as", "+100"), ("--rlimit-cpu", "1.5"),
                          ("--rlimit-nofile", "-1")):
            problems += _refused(helper, fx, [flag, val],
                                 "%s %r" % (flag, val))
        return problems, []
    _guard(suite, GRP_A, "A12 rlimit-non-decimal-refused", a12)

    def a13():
        problems = _accepted(helper, fx, ["--rlimit-nproc", "+64"],
                             "--rlimit-nproc +64")
        args = _parse(helper, ["--rlimit-nproc", "+64"])
        code, got, err = _call(helper.parse_limits, args)
        if code is not None:
            problems.append("parse_limits(+64) exited %r: %s"
                            % (code, _first(err)))
        else:
            limits, headroom = got
            if headroom != 64:
                problems.append("nproc headroom %r, want 64" % (headroom,))
            if any(name == "RLIMIT_NPROC" for name, _v in limits):
                problems.append("+N form emitted an absolute RLIMIT_NPROC: %r"
                                % (limits,))
        args = _parse(helper, ["--rlimit-nproc", "100"])
        code, got, err = _call(helper.parse_limits, args)
        if code is not None or got[1] is not None \
                or ("RLIMIT_NPROC", 100) not in tuple(got[0]):
            problems.append("--rlimit-nproc 100: %r / exit %r" % (got, code))
        for val in ("+0", "+4097", "+", "++1"):
            problems += _refused(helper, fx, ["--rlimit-nproc", val],
                                 "--rlimit-nproc %s" % val)
        return problems, []
    _guard(suite, GRP_A, "A13 rlimit-nproc-plus-form-parsed", a13)

    def a14():
        return (_accepted(helper, fx, [], "--restrict-reads alone")
                + _refused(helper, fx, ["--net"],
                           "--restrict-reads with --net", needle="--net")), []
    _guard(suite, GRP_A, "A14 restrict-with-net-refused", a14)

    def a15():
        problems = _accepted(helper, fx, ["--rlimit-as", GIB],
                             "full spellings")
        for flags in (["--restrict"], ["--rlimit-a", "1"]):
            problems += _refused(helper, fx, flags, " ".join(flags),
                                 restrict=False,
                                 cwd=fx["w"])
        rc, out, err, execs = _cli(helper, fx["w"],
                                   ["--write", fx["w"], "--dry", "--", ARGV0])
        if rc != 2 or "sbx plan" in out or execs:
            problems.append("--dry: rc %r, plan printed %s (abbreviation "
                            "resolved to --dry-run)" % (rc, "sbx plan" in out))
        return problems, []
    _guard(suite, GRP_A, "A15 abbreviated-flag-refused", a15)


# ---------------------------------------------------------------------------
# B: spelling / ancestors
# ---------------------------------------------------------------------------

def _run_b(suite, helper, fx):
    def b1():
        got = helper.metadata_ancestors(["/a/b/c/d"])
        want = ("/", "/a", "/a/b", "/a/b/c")
        return ([] if tuple(got) == want else
                ["ancestors %r, want %r" % (got, want)]), []
    _guard(suite, GRP_B, "B1 ancestors-of-nested", b1)

    def b2():
        problems = []
        got = tuple(helper.metadata_ancestors(["/x"]))
        if got != ("/",):
            problems.append("ancestors of /x: %r, want ('/',)" % (got,))
        got = tuple(helper.metadata_ancestors(["/x/y", "/x/z", "/q"]))
        if got != ("/", "/x"):
            problems.append("dedup/sort: %r, want ('/', '/x')" % (got,))
        return problems, []
    _guard(suite, GRP_B, "B2 ancestors-include-root", b2)

    def b3():
        code, got, err = _call(helper.resolve_reads, [fx["link"]], [], ARGV0)
        if code is not None:
            return ["resolve_reads exited %r: %s" % (code, _first(err))], []
        read_rules = tuple(got[0])
        want = (fx["link"], "symlink", os.readlink(fx["link"]))
        problems = []
        if want not in read_rules:
            problems.append("no literal symlink rule %r in %r"
                            % (want, read_rules))
        if any(rule[0] == fx["file"] for rule in read_rules):
            problems.append("the link target was followed into a rule")
        return problems, ["read_rules  : %r" % (read_rules,)]
    _guard(suite, GRP_B, "B3 symlink-read-is-literal-not-followed", b3)

    def b4():
        code, got, err = _call(helper.resolve_reads, [fx["r"], fx["file"]], [],
                               ARGV0)
        if code is not None:
            return ["resolve_reads exited %r: %s" % (code, _first(err))], []
        read_rules = tuple(got[0])
        problems = []
        for want in ((fx["r"], "dir", None), (fx["file"], "file", None)):
            if want not in read_rules:
                problems.append("missing %r in %r" % (want, read_rules))
        return problems, []
    _guard(suite, GRP_B, "B4 dir-read-is-subpath", b4)

    def b5():
        problems = []
        for raw, want in ((fx["r"] + "/./x/../file.txt", fx["file"]),
                          (fx["r"] + "//", fx["r"]),
                          (fx["link"], fx["link"])):
            code, got, err = _call(helper.canonicalize_read, raw)
            if code is not None or got != want:
                problems.append("canonicalize_read(%r) -> %r (exit %r), "
                                "want %r" % (raw, got, code, want))
        return problems, []
    _guard(suite, GRP_B, "B5 read-keeps-spelling-normpath-only", b5)

    def b6():
        problems = []
        for raw in ("r/file.txt", "./r", "~/x", "../x"):
            code, _got, _err = _call(helper.canonicalize_read, raw)
            if code != 2:
                problems.append("relative %r: exit %r, want 2" % (raw, code))
        problems += _refused(helper, fx, ["--read", "r"], "--read r")
        return problems, []
    _guard(suite, GRP_B, "B6 read-relative-refused", b6)

    def b7():
        problems = []
        code, _got, _err = _call(helper.resolve_reads, [fx["missing"]], [],
                                 ARGV0)
        if code != 2:
            problems.append("resolve_reads(missing): exit %r, want 2" % code)
        problems += _accepted(helper, fx, ["--read", fx["file"]],
                              "--read existing")
        problems += _refused(helper, fx, ["--read", fx["missing"]],
                             "--read missing")
        return problems, []
    _guard(suite, GRP_B, "B7 read-missing-refused", b7)

    def b8():
        problems = []
        for ch in ('"', "'", "(", ")", "\\", "\n"):
            raw = fx["r"] + "/x" + ch + "y"
            code, _got, _err = _call(helper.canonicalize_read, raw)
            if code != 2:
                problems.append("char %r: exit %r, want 2" % (ch, code))
        return problems, []
    _guard(suite, GRP_B, "B8 read-injection-char-refused", b8)


# ---------------------------------------------------------------------------
# C: argv0 / cwd
# ---------------------------------------------------------------------------

def _run_c(suite, helper, fx):
    def c1():
        problems = _accepted(helper, fx, ["--clean-env"], "absolute argv0")
        problems += _refused(helper, fx, ["--clean-env"],
                             "relative argv0 with --clean-env",
                             target=("echo", "x"))
        if sys.platform == "darwin":
            problems += _refused(helper, fx, ["--clean-env"],
                                 "argv0 with '=' (env -i trampoline)",
                                 target=(fx["r"] + "/A=1", "x"))
        return problems, []
    _guard(suite, GRP_C, "C1 clean-env-relative-argv0-refused", c1)

    def c2():
        args = _parse(helper, ["--restrict-reads", "--write", fx["w"],
                               "--read", fx["r"]])
        code, _got, err = _call(helper.validate_restricted, args, ARGV0,
                                fx["outside"], [fx["w"]])
        problems = [] if code == 3 else [
            "cwd outside every scope: exit %r, want 3 (%s)"
            % (code, _first(err))]
        return problems, []
    _guard(suite, GRP_C, "C2 restricted-cwd-outside-scopes-refused", c2)

    def c3():
        problems = []
        args = _parse(helper, ["--restrict-reads", "--write", fx["w"],
                               "--read", fx["r"]])
        for cwd in (fx["w"], os.path.join(fx["w"], "sub"), fx["r"]):
            code, _got, err = _call(helper.validate_restricted, args, ARGV0,
                                    cwd, [fx["w"]])
            if code is not None:
                problems.append("cwd %s: exit %r (%s)"
                                % (cwd, code, _first(err)))
        problems += _accepted(helper, fx, ["--read", fx["r"]],
                              "cwd inside the write scope")
        return problems, []
    _guard(suite, GRP_C, "C3 restricted-cwd-inside-write-ok", c3)

    def c4():
        problems = []
        files = fx["many_files"]
        code, got, err = _call(helper.resolve_reads, files[:READ_CAP], [],
                               ARGV0)
        if code is not None:
            problems.append("%d reads: exit %r (%s)" % (READ_CAP, code,
                                                        _first(err)))
        elif len(tuple(got[0])) != READ_CAP:
            problems.append("%d reads -> %d rules" % (READ_CAP,
                                                      len(tuple(got[0]))))
        code, _got, _err = _call(helper.resolve_reads, files, [], ARGV0)
        if code != 2:
            problems.append("%d reads: exit %r, want 2" % (READ_CAP + 1, code))
        flags = []
        for path in files:
            flags += ["--read", path]
        problems += _refused(helper, fx, flags, "%d --read" % (READ_CAP + 1))
        return problems, []
    _guard(suite, GRP_C, "C4 read-cap-256", c4)


# ---------------------------------------------------------------------------
# D: dry-run / legacy
# ---------------------------------------------------------------------------

def _run_d(suite, helper, fx):
    def d1():
        rc, out, err, execs = _cli(helper, fx["w"], [
            "--restrict-reads", "--write", fx["w"], "--read", fx["r"],
            "--exec", "/bin", "--clean-env", "--env", "A=1",
            "--timeout", "5", "--status-fd", "3", "--rlimit-as", GIB,
            "--dry-run", "--", ARGV0, "x"])
        problems = [] if rc == 0 else ["rc %r (%s)" % (rc, _first(err))]
        if execs:
            problems.append("exec'd on a dry run")
        missing = [f for f in PLAN_FIELDS if f not in out]
        if missing:
            problems.append("plan lacks %s" % ", ".join(missing))
        if rc == 0 and fx["r"] not in out:
            problems.append("plan does not show the --read spelling")
        return problems, ["plan        : %d lines" % len(out.splitlines())]
    _guard(suite, GRP_D, "D1 dry-run-prints-restricted-fields", d1)

    def d2():
        value = "tf-s3cr3t-VALUE-0d2"
        rc, out, err, _execs = _cli(helper, fx["w"], [
            "--restrict-reads", "--write", fx["w"], "--clean-env",
            "--env", "TF_SECRET=" + value, "--dry-run", "--", ARGV0])
        problems = [] if rc == 0 else ["rc %r (%s)" % (rc, _first(err))]
        if "TF_SECRET" not in out:
            problems.append("the env KEY is not shown")
        for label, text in (("stdout", out), ("stderr", err)):
            if value in text:
                problems.append("the env VALUE leaked to %s" % label)
        return problems, []
    _guard(suite, GRP_D, "D2 dry-run-env-values-never-printed", d2)

    def d3():
        import builtins
        import resource
        import subprocess
        touched = []

        def trap(label):
            def _t(*a, **k):
                touched.append(label)
                raise AssertionError("side effect at import: " + label)
            return _t
        targets = [(os, n) for n in ("execv", "execvp", "execve", "fork",
                                     "makedirs", "mkdir", "pipe", "kill",
                                     "killpg", "setsid", "chdir")
                   if hasattr(os, n)]
        targets += [(resource, "setrlimit"), (subprocess, "Popen"),
                    (builtins, "print")]
        saved = [(m, n, getattr(m, n)) for m, n in targets]
        before = H.pycache_snapshot()
        try:
            for mod, attr, _fn in saved:
                setattr(mod, attr, trap("%s.%s" % (mod.__name__, attr)))
            fresh = G._load_helper("sbx_restrict_import_probe")
        finally:
            for mod, attr, fn in saved:
                setattr(mod, attr, fn)
            sys.modules.pop("sbx_restrict_import_probe", None)
        after = H.pycache_snapshot()
        problems = ["touched %s" % ", ".join(touched)] if touched else []
        new = sorted(set(after) - set(before))
        if new:
            problems.append("bytecode written: %s" % ", ".join(new[:3]))
        absent = [n for n in PURE_API if not callable(getattr(fresh, n, None))]
        if absent:
            problems.append("pure layer missing: %s" % ", ".join(absent))
        return problems, []
    _guard(suite, GRP_D, "D3 import-has-no-side-effects", d3)

    def d4():
        problems = []
        legacy = helper.Scope(**LEGACY_SCOPE)
        for label, got, want in (
                ("seatbelt", helper._seatbelt_argv(legacy), LEGACY_SEATBELT),
                ("bwrap", helper._bwrap_argv(legacy), LEGACY_BWRAP),
                ("seatbelt --ro --net", helper._seatbelt_argv(helper.Scope(
                    **dict(LEGACY_SCOPE, ro=True, net=True, writes=[]))),
                 LEGACY_SEATBELT_RO_NET)):
            if got != want:
                problems.append("%s argv differs from 4d3640c" % label)
        explicit = helper.Scope(**dict(LEGACY_SCOPE, **NEW_FIELD_DEFAULTS))
        if helper._seatbelt_argv(explicit) != LEGACY_SEATBELT:
            problems.append("seatbelt differs with the new fields at defaults")
        if helper._bwrap_argv(explicit) != LEGACY_BWRAP:
            problems.append("bwrap differs with the new fields at defaults")
        return problems, []
    _guard(suite, GRP_D, "D4 legacy-argv-unchanged", d4)


# ---------------------------------------------------------------------------
# E: Seatbelt golden (pure; the Scope is built directly)
# ---------------------------------------------------------------------------

LEGACY_READ_ALL = '(allow file-read* (subpath "/"))'
LEGACY_EXEC_ALL = "(allow process-exec)"
EXEC_DIRS = tuple(p for p, is_dir in SB_EXEC_RULES if is_dir)


def _sb_lines(helper, **over):
    """The restricted profile for SB_SCOPE with `over` applied, one rule per
    list element; a non-sandbox-exec argv is a ValueError (FAIL)."""
    argv = helper._seatbelt_argv(helper.Scope(**dict(SB_SCOPE, **over)))
    if argv[:2] != ["sandbox-exec", "-p"] or len(argv) < 3:
        raise ValueError("not a sandbox-exec argv: %r" % (argv[:3],))
    return argv[2].split("\n")


def _sb_legacy_lines(helper):
    return helper._seatbelt_argv(helper.Scope(**LEGACY_SCOPE))[2].split("\n")


def _sb_restricted(lines, label):
    """The RED reason, stated once: the builder ignored restrict_reads and
    returned the legacy read-anywhere / unfiltered-exec profile."""
    if LEGACY_READ_ALL in lines or LEGACY_EXEC_ALL in lines:
        return ["%s: restricted branch not taken (legacy read-anywhere / "
                "unfiltered process-exec profile built)" % label]
    return []


def _first_diff(got, want, unit="line"):
    """The first differing element of two sequences, plus a length mismatch."""
    for i in range(max(len(got), len(want))):
        g = got[i] if i < len(got) else "<missing>"
        w = want[i] if i < len(want) else "<missing>"
        if g != w:
            out = ["%s %d: got %r, want %r" % (unit, i + 1, str(g)[:140],
                                                str(w)[:140])]
            if len(got) != len(want):
                out.append("%d %ss, want %d" % (len(got), unit, len(want)))
            return out
    return []


def _subpaths(line):
    """Every `(subpath "X")` spelling on one SBPL rule line."""
    out, mark = [], '(subpath "'
    start = line.find(mark)
    while start >= 0:
        end = line.find('")', start + len(mark))
        if end < 0:
            break
        out.append(line[start + len(mark):end])
        start = line.find(mark, end)
    return out


def _sb_all_variants(helper):
    """(label, lines) for every restricted profile the E rows sweep: the
    three env variants plus --ro."""
    out = [(label, _sb_lines(helper, env=env))
           for label, env, _want in SB_VARIANTS]
    out.append(("--ro", _sb_lines(helper, ro=True)))
    return out


def _run_e(suite, helper, fx):
    def e1():
        problems, extra = [], []
        for label, env, want in SB_VARIANTS:
            lines = _sb_lines(helper, env=env)
            bad = _sb_restricted(lines, label)
            bad += ["%s: %s" % (label, p) for p in _first_diff(lines, want)]
            for line in lines:
                if "(regex" in line and "{" in line:
                    bad.append("%s: bounded quantifier in a regex (it "
                               "compiles and never matches, M0:9)" % label)
            caps = [ln for ln in lines if ln.startswith(
                "(deny file-write-create")]
            if [ln.count("[^/]+/") for ln in caps] != [SB_DEPTH_GROUPS]:
                bad.append("%s: depth-cap groups %r, want one rule with %d"
                           % (label, [ln.count("[^/]+/") for ln in caps],
                              SB_DEPTH_GROUPS))
            problems += bad
            extra.append("%-22s: %d lines%s" % (label, len(lines),
                                                " (golden)" if not bad else ""))
        return problems, extra
    _guard(suite, GRP_E, "E1 restricted-profile-golden", e1)

    def e2():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            for line in lines:
                if line.startswith("(allow") and "/" in _subpaths(line):
                    problems.append("%s: global subpath \"/\" grant: %s"
                                    % (label, line[:80]))
        return problems, []
    _guard(suite, GRP_E, "E2 no-global-read-subpath-root", e2)

    def e3():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            execs = [ln for ln in lines if ln.startswith("(allow process-exec")]
            if not execs:
                problems.append("%s: no process-exec rule" % label)
            for line in execs:
                if "(literal " not in line and "(subpath " not in line:
                    problems.append("%s: unfiltered exec rule %r"
                                    % (label, line))
                if '(literal "%s")' % SB_ARGV0 not in line:
                    problems.append("%s: argv0 spelling not in the exec "
                                    "filter" % label)
                for path in _subpaths(line):
                    if path not in EXEC_DIRS:
                        problems.append("%s: exec subpath %r is no --exec "
                                        "directory" % (label, path))
        return problems, []
    _guard(suite, GRP_E, "E3 no-unfiltered-process-exec", e3)

    def e4():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            if not any(ln.startswith('(allow file-read-metadata (literal "/")')
                       for ln in lines):
                problems.append("%s: no metadata LITERAL rule on \"/\" and "
                                "its ancestors" % label)
            for line in lines:
                if line in ("(allow file-read-metadata)", "(allow file-read*)"):
                    problems.append("%s: global rule %s" % (label, line))
                if line.startswith("(allow file-read-metadata"):
                    for path in _subpaths(line):
                        if path not in EXEC_DIRS:
                            problems.append("%s: metadata subpath %r is no "
                                            "--exec directory" % (label, path))
        return problems, []
    _guard(suite, GRP_E, "E4 no-global-file-read-metadata", e4)

    def e5():
        carve = '(subpath "%s")' % SB_SCOPE["carveouts"][0]
        problems = []
        if not any(carve in ln for ln in _sb_legacy_lines(helper)):
            problems.append("control: the legacy profile lost its carve-out")
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            if any(carve in ln for ln in lines):
                problems.append("%s: carve-out %s emitted in restricted mode"
                                % (label, carve))
        return problems, []
    _guard(suite, GRP_E, "E5 carveouts-dropped-when-restricted", e5)

    def e6():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            allows = [i for i, ln in enumerate(lines)
                      if ln.startswith("(allow file-")]
            denies = [i for i, ln in enumerate(lines)
                      if ln.startswith(("(deny file-read* ",
                                        "(deny file-write* "))]
            if not denies:
                problems.append("%s: no secret/shadow deny emitted" % label)
            elif allows and min(denies) < max(allows):
                problems.append("%s: allow at line %d after the deny at line "
                                "%d (last match wins)"
                                % (label, max(allows) + 1, min(denies) + 1))
        return problems, []
    _guard(suite, GRP_E, "E6 denies-after-allows", e6)

    def e7():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            if lines.count(SB_DEV_NULL) != 1:
                problems.append("%s: %r emitted %d times, want once"
                                % (label, SB_DEV_NULL, lines.count(SB_DEV_NULL)))
            others = [ln for ln in lines
                      if "/dev/null" in ln and ln != SB_DEV_NULL]
            if others:
                problems.append("%s: another /dev/null rule %r (the "
                                "restricted branch emits its own, M0:5)"
                                % (label, others[0]))
        return problems, []
    _guard(suite, GRP_E, "E7 dev-null-read-write-literal", e7)

    def e8():
        lines = _sb_lines(helper, ro=True)
        problems = _sb_restricted(lines, "--ro")
        for needle in ("file-write-acl", "file-write-create",
                       '(subpath "%s")' % SB_WRITE):
            if any(needle in ln for ln in lines):
                problems.append("--ro: %s emitted with no write scope" % needle)
        if any(ln.startswith("(allow file-write*") and "/dev/null" not in ln
               for ln in lines):
            problems.append("--ro: a write allow other than /dev/null")
        if SB_DEV_NULL not in lines:
            problems.append("--ro: /dev/null read+write literal missing")
        return problems, []
    _guard(suite, GRP_E, "E8 ro-restricted-no-write-scope", e8)

    def e9():
        problems = []
        for label, lines in _sb_all_variants(helper):
            problems += _sb_restricted(lines, label)
            if lines[-1:] != ["(deny network*)"]:
                problems.append("%s: last rule %r, want (deny network*)"
                                % (label, lines[-1:]))
            if "(allow network*)" in lines:
                problems.append("%s: network allowed" % label)
        return problems, []
    _guard(suite, GRP_E, "E9 network-deny-last", e9)

    def e10():
        writes = ["/wd/a", "/wd/b"]
        lines = _sb_lines(helper, writes=writes, cwd=writes[0])
        problems = _sb_restricted(lines, "two scopes")
        for scope in writes:
            sub = '(subpath "%s")' % scope
            allow = [i for i, ln in enumerate(lines)
                     if ln.startswith("(allow file-write*") and sub in ln]
            deny = [i for i, ln in enumerate(lines)
                    if ln.startswith("(deny file-write-acl") and sub in ln]
            if not allow:
                problems.append("%s: no write allow" % scope)
            if not deny:
                problems.append("%s: no (deny file-write-acl ...)" % scope)
            elif allow and min(deny) < max(allow):
                problems.append("%s: the ACL deny precedes the write allow"
                                % scope)
        for op in ("file-write-flags", "file-write-mode", "file-write-xattr"):
            if any(op in ln for ln in lines):
                problems.append("%s denied (breaks copy2 / chmod, M0:8)" % op)
        if SB_DEV_NULL not in lines:
            problems.append("/dev/null lost read+write")
        if any("file-write-acl" in ln for ln in _sb_legacy_lines(helper)):
            problems.append("the legacy profile carries file-write-acl")
        return problems, []
    _guard(suite, GRP_E, "E10 restricted-denies-acl-writes-in-write-scope",
           e10)


# ---------------------------------------------------------------------------
# F: bwrap golden (pure; the Scope is built directly)
# ---------------------------------------------------------------------------

def _bw(helper, base, **over):
    return helper._bwrap_argv(helper.Scope(**dict(base, **over)))


def _bw_restricted(argv, label):
    """The RED reason, stated once: the legacy `--ro-bind / /` root."""
    if _has_seq(argv, ["--ro-bind", "/", "/"]):
        return ["%s: restricted branch not taken (legacy --ro-bind / / "
                "built)" % label]
    return []


def _has_seq(argv, seq):
    n = len(seq)
    return any(argv[i:i + n] == seq for i in range(len(argv) - n + 1))


def _bw_fixtures(helper):
    return (("usr-merged", _bw(helper, BW_SCOPE_MERGED)),
            ("split-usr", _bw(helper, BW_SCOPE_SPLIT,
                              read_rules=BW_READ_RULES)))


def _before_end(argv, flag):
    """True when `flag` appears before the `--` options terminator."""
    return flag in argv and "--" in argv and argv.index(flag) < argv.index("--")


def _run_f(suite, helper, fx):
    def f1():
        got = _bw(helper, BW_SCOPE_MERGED)
        return (_bw_restricted(got, "usr-merged")
                + _first_diff(got, BW_GOLDEN_MERGED, "argv item")), \
            ["argv        : %d items" % len(got)]
    _guard(suite, GRP_F, "F1 restricted-argv-golden-usr-merged", f1)

    def f2():
        got = _bw(helper, BW_SCOPE_SPLIT)
        return (_bw_restricted(got, "split-usr")
                + _first_diff(got, BW_GOLDEN_SPLIT, "argv item")), \
            ["argv        : %d items" % len(got)]
    _guard(suite, GRP_F, "F2 restricted-argv-golden-split-usr", f2)

    def f3():
        problems = []
        for label, argv in _bw_fixtures(helper):
            problems += _bw_restricted(argv, label)
            if argv[1:4] != ["--ro-bind", "/usr", "/usr"]:
                problems.append("%s: argv does not open with the /usr base "
                                "bind: %r" % (label, argv[1:4]))
        return problems, []
    _guard(suite, GRP_F, "F3 no-ro-bind-root", f3)

    def f4():
        problems = []
        for label, argv in _bw_fixtures(helper):
            problems += _bw_restricted(argv, label)
            for path in BW_COVERED:
                if path in argv:
                    problems.append("%s: %s (under a base bind) mounted "
                                    "again" % (label, path))
            for seq in (["--ro-bind", "/opt/app", "/opt/app"],
                        ["--symlink", "app/bin", "/opt/link"]):
                if not _has_seq(argv, seq):
                    problems.append("%s: control read %r missing"
                                    % (label, seq))
        return problems, []
    _guard(suite, GRP_F, "F4 spelling-under-base-skipped", f4)

    def f5():
        argv = _bw(helper, BW_SCOPE_MERGED, env={"Z": "1", "A": "2", "M": "3"})
        problems = _bw_restricted(argv, "env Z/A/M")
        want = ["--clearenv", "--setenv", "A", "2", "--setenv", "M", "3",
                "--setenv", "Z", "1"]
        if not _has_seq(argv, want):
            problems.append("no sorted %r" % (want,))
        if argv.count("--setenv") != 3:
            problems.append("%d --setenv, want 3" % argv.count("--setenv"))
        if not _before_end(argv, "--clearenv"):
            problems.append("--clearenv missing or after `--`")
        empty = _bw(helper, BW_SCOPE_MERGED, env={})
        if "--clearenv" not in empty or "--setenv" in empty:
            problems.append("env {}: want --clearenv and no --setenv")
        inherit = _bw(helper, BW_SCOPE_SPLIT)
        if "--clearenv" in inherit or "--setenv" in inherit:
            problems.append("env None: --clearenv/--setenv emitted")
        return problems, []
    _guard(suite, GRP_F, "F5 clearenv-setenv-sorted", f5)

    def f6():
        problems = []
        for cwd in ("/w", "/w/sub"):
            argv = _bw(helper, BW_SCOPE_MERGED, cwd=cwd)
            problems += _bw_restricted(argv, "cwd %s" % cwd)
            if not _has_seq(argv, ["--chdir", cwd]):
                problems.append("cwd %s: no --chdir %s" % (cwd, cwd))
            elif not _before_end(argv, "--chdir"):
                problems.append("cwd %s: --chdir after `--`" % cwd)
            elif "--setenv" in argv and \
                    argv.index("--chdir") < argv.index("--setenv"):
                problems.append("cwd %s: --chdir before --setenv" % cwd)
        if "--chdir" in helper._bwrap_argv(helper.Scope(**LEGACY_SCOPE)):
            problems.append("the legacy argv gained --chdir")
        return problems, []
    _guard(suite, GRP_F, "F6 chdir-emitted", f6)

    def f7():
        problems = []
        for label, argv in _bw_fixtures(helper):
            problems += _bw_restricted(argv, label)
            if not _has_seq(argv, ["--unshare-uts", "--hostname", "sbx"]):
                problems.append("%s: no --unshare-uts --hostname sbx" % label)
            if not _before_end(argv, "--unshare-cgroup-try"):
                problems.append("%s: no --unshare-cgroup-try" % label)
        legacy = helper._bwrap_argv(helper.Scope(**LEGACY_SCOPE))
        if "--unshare-uts" in legacy or "--unshare-cgroup-try" in legacy:
            problems.append("the legacy argv gained the uts/cgroup unshares")
        return problems, []
    _guard(suite, GRP_F, "F7 unshare-uts-cgroup", f7)

    def f8():
        carve = BW_SCOPE_MERGED["carveouts"][0]
        problems = []
        if carve not in helper._bwrap_argv(helper.Scope(**LEGACY_SCOPE)):
            problems.append("control: the legacy argv lost its carve-out")
        argv = _bw(helper, BW_SCOPE_MERGED)
        problems += _bw_restricted(argv, "usr-merged")
        if carve in argv:
            problems.append("carve-out %s bound in restricted mode" % carve)
        return problems, []
    _guard(suite, GRP_F, "F8 carveouts-dropped", f8)

    def f9():
        problems = []
        for label, scope in (
                ("legacy", LEGACY_SCOPE),
                ("new fields at defaults",
                 dict(LEGACY_SCOPE, **NEW_FIELD_DEFAULTS))):
            got = helper._bwrap_argv(helper.Scope(**scope))
            problems += ["%s: %s" % (label, p)
                         for p in _first_diff(got, LEGACY_BWRAP, "argv item")]
        return problems, []
    _guard(suite, GRP_F, "F9 legacy-argv-unchanged", f9)

    def f10():
        out_of_scope = ["--ro-bind", "/r/ClaudeCode", "/r/ClaudeCode"]
        in_scope = ["--ro-bind", "/w/sub", "/w/sub"]
        problems = []
        argv = _bw(helper, BW_SCOPE_MERGED)
        problems += _bw_restricted(argv, "restricted")
        if _has_seq(argv, out_of_scope):
            problems.append("restricted: %r re-bound (a READ grant on the "
                            "empty root, C-H4)" % out_of_scope[1])
        if not _has_seq(argv, in_scope):
            problems.append("restricted: %r inside the write scope lost its "
                            "read-only re-bind" % in_scope[1])
        legacy = _bw(helper, dict(BW_SCOPE_MERGED, **NEW_FIELD_DEFAULTS))
        for seq in (out_of_scope, in_scope):
            if not _has_seq(legacy, seq):
                problems.append("without --restrict-reads: %r not re-bound"
                                % seq[1])
        return problems, []
    _guard(suite, GRP_F, "F10 shadows-dropped-when-restricted", f10)

    def f11():
        # IMPL:025 -- FR-4: --clean-env sets the target env EXACTLY whether or
        # not reads are restricted (darwin's trampoline already does). bwrap
        # is exec'd with {}, so without --clearenv/--setenv in the LEGACY
        # branch the --env pairs were silently dropped (an empty env).
        end = LEGACY_BWRAP.index("--")
        want = (LEGACY_BWRAP[:end]
                + ["--clearenv", "--setenv", "A", "1", "--setenv", "B", "2"]
                + LEGACY_BWRAP[end:])
        kw = dict(LEGACY_SCOPE, **NEW_FIELD_DEFAULTS)
        problems = ["env A/B: %s" % p for p in _first_diff(
            _bw(helper, kw, env={"B": "2", "A": "1"}), want, "argv item")]
        want_empty = LEGACY_BWRAP[:end] + ["--clearenv"] + LEGACY_BWRAP[end:]
        problems += ["env {}: %s" % p for p in _first_diff(
            _bw(helper, kw, env={}), want_empty, "argv item")]
        problems += ["env None: %s" % p for p in _first_diff(
            _bw(helper, kw), LEGACY_BWRAP, "argv item")]
        return problems, []
    _guard(suite, GRP_F, "F11 clean-env-without-restrict-reads-keeps-setenv",
           f11)


# ---------------------------------------------------------------------------
# U / V / T: the Step 4 supervisor, strict rlimits and clean-env exec.
# Live rows run the REAL helper as a subprocess (the production shape: a
# fresh status pipe passed by number, stdin /dev/null); the pure T rows call
# _prepare_exec with the engine detection and sys.platform stubbed.
# ---------------------------------------------------------------------------

LIVE_TIMEOUT_S = 60
STATUS_LINE_MAX = 512                 # KD-1: each status line <= 512 bytes
STATUS_BUF_MAX = 64 * 1024            # the suite never buffers more than this
STATUS_FD_TOKEN = "@STATUS_FD@"       # replaced by the status fd number in a target
# The pre-Step-4 fail-closed guard in sbx.main(): a REAL run carrying any
# restricted flag is refused with exit 3 before anything is built. Every live
# row names it as its RED reason instead of a generic mismatch.
GUARD_TEXT = "restricted-mode flags are not enforced by this build yet"
SPAWNED_KEYS = frozenset(("sbx", "event", "pid", "pgid"))
EXIT_KEYS = frozenset(("sbx", "event", "outcome", "code", "signal",
                       "durationMs"))
REFUSED_KEYS = EXIT_KEYS | frozenset(("reason",))
ANY = object()                        # "this field is checked by the row itself"
TIMEOUT_MARKER = ("sbx: timeout: wall-clock limit of %gs reached; process "
                  "group killed")    # KD-1, verbatim
# MEASURED, keys only -- never "a superset of": what a target sees beyond the
# --env keys under the `/usr/bin/env -i` trampoline, PER PROFILE, because the
# profile decides what the interpreter can do to its own env after exec:
#   restricted -- M0:7 (item-3h-j.md 3h). Seatbelt denies the read of
#     /usr/share/locale/UTF-8/LC_CTYPE, so python3's PEP 538 C-locale coercion
#     silently fails and sets nothing (out-3h-j.txt:21-25, out-3j.txt:146).
#   legacy -- re-measured live 2026-10-11 (macOS 14.2, /usr/local/bin/python3
#     3.14.0, `sbx --write <w> --clean-env --env A=1 --env B=2 -- <interp>`):
#     the locale is readable, so python3 coerces the empty (C) locale and
#     exports LC_CTYPE itself AFTER exec -- `env -i A=1 B=2 python3` shows it
#     outside any sandbox and PYTHONCOERCECLOCALE=0 removes it, so sbx adds
#     nothing. node and bash measured the same as M0:7.
# The T1/T2/T7 live rows run the LEGACY profile (no --restrict-reads).
ENV_ADDITIONS = {
    "restricted": {"python3": (), "node": (), "bash": ("PWD", "SHLVL", "_")},
    "legacy": {"python3": ("LC_CTYPE",), "node": (),
               "bash": ("PWD", "SHLVL", "_")},
}
_BASH_SOURCE = ("PWD, SHLVL exported by bash at startup; _ = the path bash "
                "executes, so it is seen through a CHILD (/usr/bin/env)")
ENV_ADDITIONS_SOURCE = {
    "restricted": {
        "python3": "none -- the framework launcher leaves no "
                   "__PYVENV_LAUNCHER__ in os.environ, and PEP 538 coercion "
                   "fails on the denied locale read (M0:7)",
        "node": "none (M0:7)",
        "bash": _BASH_SOURCE + " (M0:7)",
    },
    "legacy": {
        "python3": "LC_CTYPE -- python3's own PEP 538 C-locale coercion "
                   "after exec, not sbx (re-measured, legacy profile)",
        "node": "none (re-measured, legacy profile)",
        "bash": _BASH_SOURCE + " (re-measured, legacy profile)",
    },
}
LIVE_ENV_PROFILE = "legacy"           # the profile T1/T2/T7 run under
CANARY_KEY = "TF_SBX_RESTRICT_CANARY"
CANARY_VALUE = "tf-canary-%d-%d" % (os.getpid(), random.randint(1000, 9999))
GIB_I = 1024 ** 3
# an AS limit every interpreter starts under (macOS idle VSZ ~32.5 GiB, M0:2)
AS_ROOMY = 64 * GIB_I if sys.platform == "darwin" else 4 * GIB_I
AS_BELOW_VSZ = 64 * 1024 ** 2         # parse_limits' floor; below any macOS VSZ

PROBE_IDS = ("import json, os; print(json.dumps({'pid': os.getpid(), "
             "'pgid': os.getpgrp(), 'sid': os.getsid(0)}))")
PROBE_FD = "\n".join([
    "import errno, json, os, sys",
    "fd = int(sys.argv[1])",
    "try:",
    "    os.fstat(fd)",
    "    state = 'open'",
    "except OSError as exc:",
    "    state = errno.errorcode.get(exc.errno, str(exc.errno))",
    "print(json.dumps({'fd': fd, 'state': state}))",
])
PROBE_LIMITS = "\n".join([
    "import json, resource, sys",
    "print(json.dumps({n: list(resource.getrlimit(getattr(resource, n)))",
    "                  for n in sys.argv[1:]}))",
])
PROBE_RAISE = "\n".join([
    "import json, resource, sys",
    "which = getattr(resource, sys.argv[1])",
    "soft, hard = resource.getrlimit(which)",
    "def attempt(new):",
    "    try:",
    "        resource.setrlimit(which, new)",
    "        return 'raised'",
    "    except (ValueError, OSError) as exc:",
    "        return 'refused:' + type(exc).__name__",
    "out = {'before': [soft, hard],",
    "       'raise_soft': attempt((soft + 1, hard)),",
    "       'raise_hard': attempt((hard + 1, hard + 1))}",
    "out['after'] = list(resource.getrlimit(which))",
    "print(json.dumps(out))",
])
PROBE_FSIZE = "\n".join([
    "import errno, json, os, signal, sys",
    "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)",
    "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)",
    "written, err = 0, None",
    "for _ in range(8):",
    "    try:",
    "        written += os.write(fd, b'x' * 4096)",
    "    except OSError as exc:",
    "        err = errno.errorcode.get(exc.errno, str(exc.errno))",
    "        break",
    "os.close(fd)",
    "print(json.dumps({'written': written, 'error': err}))",
])
PROBE_NOFILE = "\n".join([
    "import errno, json, os",
    "fds, err = [], None",
    "for _ in range(4096):",
    "    try:",
    "        fds.append(os.open('/dev/null', os.O_RDONLY))",
    "    except OSError as exc:",
    "        err = errno.errorcode.get(exc.errno, str(exc.errno))",
    "        break",
    "print(json.dumps({'opened': len(fds), 'error': err}))",
])
PROBE_BUSY = "while True:\n    pass"
PROBE_ENV = "import json, os; print(json.dumps(sorted(os.environ)))"
PROBE_EXIT3 = "import sys; sys.exit(3)"
PROBE_SIGTERM = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
PROBE_MARK = "import sys; open(sys.argv[1], 'w').write('ran')"


class _StatusPipe:
    """The read end of a --status-fd pipe, read against a deadline -- never
    to EOF blindly: a leaked process holding the write end must not hang the
    suite (the KD-8 rule, applied to the suite itself)."""

    def __init__(self, fd):
        self.fd, self.buf, self.eof = fd, b"", False

    def pump(self, seconds, until_line=False):
        import select
        deadline = time.monotonic() + seconds
        while not self.eof and len(self.buf) < STATUS_BUF_MAX:
            if until_line and b"\n" in self.buf:
                break
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if select.select([self.fd], [], [], min(left, 0.1))[0]:
                chunk = os.read(self.fd, 4096)
                if chunk:
                    self.buf += chunk
                else:
                    self.eof = True
        return b"\n" in self.buf


def _parse_status(buf):
    """(events, problems) of a status stream: UTF-8 JSON objects, one per
    '\\n'-terminated line of <= 512 bytes (KD-1)."""
    events, problems = [], []
    if buf and not buf.endswith(b"\n"):
        problems.append("status stream not newline-terminated: %r" % buf[-60:])
    for raw in buf.split(b"\n"):
        if not raw:
            continue
        if len(raw) + 1 > STATUS_LINE_MAX:
            problems.append("status line of %d bytes > %d"
                            % (len(raw) + 1, STATUS_LINE_MAX))
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            problems.append("unparsable status line %r" % raw[:80])
            continue
        if not isinstance(obj, dict):
            problems.append("status line is no JSON object: %r" % raw[:80])
            continue
        events.append(obj)
    return events, problems


def _live(fx, flags, target, status=True, env_over=None,
          timeout=LIVE_TIMEOUT_S, during=None):
    """Run the REAL helper: `sbx --write <w> FLAGS [--status-fd N] -- TARGET`
    from the write scope, stdin /dev/null. `during(proc, pipe)` runs while
    sbx is alive (U7). Returns a dict: rc, out, err, events, status_errors,
    elapsed, end, timed_out, during."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.update(env_over or {})
    rfd = wfd = None
    res = {"timed_out": False, "during": None}
    try:
        argv = [sys.executable, HELPER, "--write", fx["w"]] + list(flags)
        pass_fds = ()
        if status:
            rfd, wfd = os.pipe()
            argv += ["--status-fd", str(wfd)]
            pass_fds = (wfd,)
            target = [a.replace(STATUS_FD_TOKEN, str(wfd)) for a in target]
        argv += ["--"] + list(target)
        res["argv"] = argv
        start = time.monotonic()
        try:
            proc = subprocess.Popen(argv, cwd=fx["w"], env=env,
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    pass_fds=pass_fds,
                                    universal_newlines=True)
        finally:
            if wfd is not None:
                os.close(wfd)
        pipe = _StatusPipe(rfd) if status else None
        try:
            if during is not None:
                res["during"] = during(proc, pipe)
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            res["timed_out"] = True
            proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # a leaked process holds the pipes
                proc.stdout.close()
                proc.stderr.close()
                proc.wait()
                out, err = "", "<output pipes held open by a leaked process>"
        finally:
            if proc.returncode is None:
                proc.kill()
                proc.wait()
        res["end"] = time.monotonic()
        res["elapsed"] = res["end"] - start
        if pipe is not None:
            pipe.pump(1.0)
        res["events"], res["status_errors"] = _parse_status(
            pipe.buf if pipe is not None else b"")
    finally:
        if rfd is not None:
            os.close(rfd)
    res.update(rc=proc.returncode, out=out, err=err)
    return res


def _red_guard(res, label=""):
    """The RED reason, stated once: the pre-Step-4 guard refused the run."""
    if res["rc"] == 3 and GUARD_TEXT in res["err"]:
        return ["%sRED: sbx refused the real run with exit 3 (pre-Step-4 "
                "guard %r) -- no supervisor / status protocol / strict "
                "rlimits / clean-env exec yet" % (label, GUARD_TEXT)]
    return []


def _live_detail(res, label=""):
    return ["%src %r, %.2fs%s" % (label, res["rc"], res.get("elapsed", 0.0),
                                  " (harness timeout)" if res["timed_out"]
                                  else ""),
            "%sstatus %r" % (label, res["events"][:3]),
            "%sstderr %r" % (label, res["err"].strip()[:200])]


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _json_out(res):
    """The target's last stdout line as JSON, or None."""
    try:
        return json.loads(res["out"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _status_problems(res, outcome, code=ANY, sig=ANY, spawned=True):
    """The KD-1 status contract: exactly one `spawned` {sbx, event, pid,
    pgid} with pid == pgid (none for `refused`), then exactly one terminal
    `exit` with exactly the documented keys (`reason` only on `refused`)."""
    problems = list(res["status_errors"])
    events = res["events"]
    want = 2 if spawned else 1
    if len(events) != want:
        return problems + ["status fd carried %d event(s) %r, want %s"
                           % (len(events), [e.get("event") for e in events],
                              "spawned + exit" if spawned
                              else "one exit (no spawned)")]
    if spawned:
        sp = events[0]
        if set(sp) != SPAWNED_KEYS:
            problems.append("spawned keys %r, want %r"
                            % (sorted(sp), sorted(SPAWNED_KEYS)))
        if sp.get("sbx") != 1 or sp.get("event") != "spawned":
            problems.append("first event %r is no spawned record" % (sp,))
        if not _is_int(sp.get("pid")) or sp.get("pid", 0) <= 0:
            problems.append("spawned pid %r" % (sp.get("pid"),))
        elif sp.get("pgid") != sp.get("pid"):
            problems.append("spawned pgid %r != pid %r (the child setsid()s)"
                            % (sp.get("pgid"), sp.get("pid")))
    ex = events[-1]
    keys = REFUSED_KEYS if outcome == "refused" else EXIT_KEYS
    if set(ex) != keys:
        problems.append("exit keys %r, want %r" % (sorted(ex), sorted(keys)))
    if ex.get("sbx") != 1 or ex.get("event") != "exit":
        problems.append("last event %r is no exit record" % (ex,))
    if ex.get("outcome") != outcome:
        problems.append("outcome %r, want %r" % (ex.get("outcome"), outcome))
    for key, want_v in (("code", code), ("signal", sig)):
        if want_v is ANY:
            continue
        got = ex.get(key)
        if got != want_v or isinstance(got, bool):
            problems.append("exit %s %r, want %r" % (key, got, want_v))
    if not _is_int(ex.get("durationMs")) or ex.get("durationMs", -1) < 0:
        problems.append("durationMs %r" % (ex.get("durationMs"),))
    if outcome == "refused" and not (isinstance(ex.get("reason"), str)
                                     and ex.get("reason")):
        problems.append("refused without a reason string")
    return problems


def _gone(kill, ident, wait_s=3.0):
    """True once kill(ident, 0) answers ESRCH within wait_s. EPERM (macOS's
    answer for a zombie-only group, M0:1) and a live answer keep waiting."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            kill(ident, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _kill_quiet(kill, ident):
    try:
        kill(ident, signal.SIGKILL)
    except OSError:
        pass


def _token_pids(token):
    """Linux: host pids whose cmdline carries `token` (pid-namespace-proof)."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % name, "rb") as fh:
                if token.encode() in fh.read().split(b"\x00"):
                    found.append(int(name))
        except OSError:
            continue
    return found


def _uid_count():
    """The uid's RLIMIT_NPROC units, the way sbx's uid_process_count counts
    them: processes on macOS, tasks (threads) on Linux."""
    uid = str(os.getuid())
    if sys.platform == "darwin":
        p = subprocess.run(["/bin/ps", "-A", "-o", "ruid="], env={},
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True,
                           timeout=10)
        return sum(1 for ln in p.stdout.split() if ln == uid)
    count = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/status" % name) as fh:
                text = fh.read()
        except OSError:
            continue
        fields = dict(ln.split(":", 1) for ln in text.splitlines() if ":" in ln)
        if fields.get("Uid", "").split()[:1] == [uid]:
            count += int(fields.get("Threads", "1").split()[0])
    return count


def _sleep_token():
    """A unique fractional sleep duration: names one process in a scan."""
    return "%d.%03d" % (40 + random.randint(0, 19), random.randint(1, 999))


def _live_skips(fx):
    """{'darwin': reason|None, 'linux': reason|None}: None where that
    platform's live rows run. P23: one LEGACY control sbx run first (it needs
    no Step 4 code); a host that cannot apply a sandbox makes them INFO."""
    here = "darwin" if sys.platform == "darwin" else (
        "linux" if sys.platform.startswith("linux") else sys.platform)
    skips = {key: "a %s-only live row; this host is %s" % (key, sys.platform)
             for key in ("darwin", "linux") if key != here}
    if here not in ("darwin", "linux"):
        return skips
    engine = "sandbox-exec" if here == "darwin" else "bwrap"
    if not shutil.which(engine):
        skips[here] = "%s not on PATH" % engine
        return skips
    ctl = _live(fx, [], ["/bin/echo", "tf-live-control"], status=False,
                timeout=30)
    if ctl["out"].strip() != "tf-live-control" and (
            here == "linux" or "sandbox_apply" in ctl["err"]):
        skips[here] = ("the engine cannot apply a sandbox here (legacy "
                       "control rc %r): %s" % (ctl["rc"],
                                                ctl["err"].strip()[:200]))
    else:
        skips[here] = None
    return skips


def _live_row(suite, group, name, skip, fn):
    if skip:
        _rec(suite, group, name, [], ["skipped     : " + skip],
             status=H.INFO)
        return
    _guard(suite, group, name, fn)


def _supervised(fx, flags, target, **kw):
    return _live(fx, ["--timeout", "20"] + list(flags), target, **kw)


def _run_u(suite, helper, fx, skips):
    mac = skips["darwin"]
    here = skips["darwin"] if sys.platform == "darwin" else skips["linux"]
    py = sys.executable

    def u1():
        res = _live(fx, ["--timeout", "1"], ["/bin/sleep", "30"])
        problems = _red_guard(res)
        if not problems:
            marker = TIMEOUT_MARKER % 1.0
            if res["rc"] != 124:
                problems.append("rc %r, want 124" % (res["rc"],))
            if marker not in res["err"]:
                problems.append("stderr lacks the marker %r" % marker)
            if res["elapsed"] > 10:
                problems.append("took %.1fs for a 1s wall clock"
                                % res["elapsed"])
            problems += _status_problems(res, "timeout", code=None,
                                         sig=int(signal.SIGKILL))
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U1 timeout-exit-124-and-marker", mac, u1)

    def u2():
        res = _supervised(fx, [], [py, "-c", PROBE_IDS])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            ids = _json_out(res)
            pid = res["events"][0].get("pid") if res["events"] else None
            if not isinstance(ids, dict):
                problems.append("target printed no ids: %r" % res["out"][:120])
            elif not (ids.get("pid") == ids.get("pgid") == ids.get("sid")
                      == pid):
                problems.append("target ids %r vs spawned pid %r (want "
                                "pid == pgid == sid == spawned)" % (ids, pid))
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U2 status-fd-spawned-pgid-then-exit", mac, u2)

    def u3():
        res = _supervised(fx, [], [py, "-c", PROBE_EXIT3])
        problems = _red_guard(res)
        if not problems:
            if res["rc"] != 3:
                problems.append("rc %r, want the target's 3" % (res["rc"],))
            # the status, not the exit code, says the TARGET exited 3
            problems += _status_problems(res, "exited", code=3, sig=None)
            if "sbx: error" in res["err"]:
                problems.append("sbx wrote an error for a target's exit 3")
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U3 status-outcome-exited-code-passthrough", mac,
              u3)

    def u4():
        res = _supervised(fx, [], [py, "-c", PROBE_FD, STATUS_FD_TOKEN])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _json_out(res)
            if not isinstance(got, dict):
                problems.append("target printed no fd state: %r"
                                % res["out"][:120])
            elif got.get("state") != "EBADF":
                problems.append("status fd %r in the target: %r, want EBADF"
                                % (got.get("fd"), got.get("state")))
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U4 status-fd-not-inherited", mac, u4)

    def u5():
        res = _supervised(fx, [], [py, "-c", PROBE_SIGTERM])
        problems = _red_guard(res)
        if not problems:
            want = 128 + int(signal.SIGTERM)
            if res["rc"] != want:
                problems.append("rc %r, want %d (128 + SIGTERM)"
                                % (res["rc"], want))
            problems += _status_problems(res, "signaled", code=None,
                                         sig=int(signal.SIGTERM))
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U5 signaled-maps-128-plus", mac, u5)

    def u6():
        import inspect
        token = _sleep_token()
        res = _supervised(fx, [], [
            "/bin/sh", "-c",
            "/bin/sleep %s </dev/null >/dev/null 2>&1 & echo $!" % token])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            if sys.platform == "darwin":
                try:
                    bg = int(res["out"].strip().splitlines()[-1])
                except (ValueError, IndexError):
                    bg = None
                if bg is None:
                    problems.append("no background pid printed: %r"
                                    % res["out"][:80])
                elif not _gone(os.kill, bg):
                    problems.append("background child %d alive after the "
                                    "target exited (group not killed before "
                                    "reap)" % bg)
                    _kill_quiet(os.kill, bg)
            else:
                deadline = time.monotonic() + 3.0
                while _token_pids(token) and time.monotonic() < deadline:
                    time.sleep(0.05)
                left = _token_pids(token)
                if left:
                    problems.append("background child %r alive after the "
                                    "target exited" % left)
                    for pid in left:
                        _kill_quiet(os.kill, pid)
        # KD-2 / M0:1: the platform path is selected by sys.platform, never by
        # hasattr(os, "waitid") (True on darwin from Python 3.13).
        fn = getattr(helper, "_wait_exit_unreaped", None)
        if fn is None:
            problems.append("helper has no _wait_exit_unreaped (KD-2)")
        else:
            src = inspect.getsource(fn)
            for needle in ("sys.platform", "KQ_NOTE_EXIT", "WNOWAIT"):
                if needle not in src:
                    problems.append("_wait_exit_unreaped lacks %s" % needle)
            if 'hasattr(os, "waitid")' in src or "hasattr(os, 'waitid')" in src:
                problems.append("_wait_exit_unreaped selects the path by "
                                "hasattr(os, 'waitid') (M0:1)")
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U6 kill-before-reap", here, u6)

    def u7():
        token = _sleep_token()

        def during(proc, pipe):
            if not pipe.pump(15, until_line=True):
                return {"sent": False}
            first = _parse_status(pipe.buf.split(b"\n", 1)[0] + b"\n")[0]
            pgid = first[0].get("pgid") if first else None
            sent_at = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            return {"sent": True, "pgid": pgid, "t": sent_at}
        res = _live(fx, ["--timeout", "30"], [
            "/bin/sh", "-c", "/bin/sleep %s </dev/null >/dev/null 2>&1 & "
            "/bin/sleep 30" % token], timeout=20, during=during)
        problems = _red_guard(res)
        info = res["during"] or {}
        if not problems:
            if not info.get("sent"):
                problems.append("no spawned line within 15s; SIGTERM not sent")
            else:
                pgid = info.get("pgid")
                if res["end"] - info["t"] > 5.0:
                    problems.append("sbx took %.1fs to exit after SIGTERM"
                                    % (res["end"] - info["t"]))
                if res["rc"] in (0, None):
                    problems.append("rc %r after SIGTERM" % (res["rc"],))
                problems += _status_problems(res, "signaled", code=None)
                if not _is_int(pgid):
                    problems.append("spawned pgid %r" % (pgid,))
                elif not _gone(os.killpg, pgid):
                    problems.append("target group %d alive after sbx got "
                                    "SIGTERM" % pgid)
                    _kill_quiet(os.killpg, pgid)
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U7 sbx-sigterm-kills-group", mac, u7)

    def u8():
        engine = "sandbox-exec" if sys.platform == "darwin" else "bwrap"
        fakebin = os.path.join(fx["root"], "fakebin")
        os.makedirs(fakebin, exist_ok=True)
        fake = os.path.join(fakebin, engine)
        with open(fake, "wb") as fh:
            fh.write(b"\x00\x01tf: not an executable image\n")
        os.chmod(fake, 0o755)
        ctl = _supervised(fx, [], ["/usr/bin/true"])
        problems = _red_guard(ctl, "control: ")
        if not problems:
            if ctl["rc"] != 0:
                problems.append("control: rc %r, want 0" % (ctl["rc"],))
            problems += ["control: " + p for p in _status_problems(
                ctl, "exited", code=0, sig=None)]
        res = _supervised(fx, [], ["/usr/bin/true"], env_over={
            "PATH": fakebin + os.pathsep + os.environ.get("PATH", "")})
        if not _red_guard(res):
            if res["rc"] != 3:
                problems.append("engine exec failure: rc %r, want 3"
                                % (res["rc"],))
            problems += _status_problems(res, "refused", code=3, sig=None,
                                         spawned=False)
            lines = [ln for ln in res["err"].splitlines() if ln.strip()]
            if len(lines) != 1 or not lines[0].startswith("sbx: error"):
                problems.append("stderr is not one 'sbx: error' line: %r"
                                % lines[:3])
        return problems, _live_detail(ctl, "control ") + _live_detail(res)
    _live_row(suite, GRP_U, "U8 exec-failure-refused-status-no-spawned", mac,
              u8)

    def u9():
        token = _sleep_token()
        res = _live(fx, ["--timeout", "1"], [
            "/bin/sh", "-c", "setsid /bin/sleep %s </dev/null >/dev/null "
            "2>&1 & exec /bin/sleep 30" % token])
        problems = _red_guard(res)
        if not problems:
            if res["rc"] != 124:
                problems.append("rc %r, want 124" % (res["rc"],))
            problems += _status_problems(res, "timeout", code=None,
                                         sig=int(signal.SIGKILL))
            deadline = time.monotonic() + 3.0
            while _token_pids(token) and time.monotonic() < deadline:
                time.sleep(0.05)
            left = _token_pids(token)
            if left:
                problems.append("setsid grandchild %r survived the PID "
                                "namespace teardown" % left)
                for pid in left:
                    _kill_quiet(os.kill, pid)
        return problems, _live_detail(res)
    _live_row(suite, GRP_U, "U9 linux-pidns-teardown", skips["linux"], u9)


def _run_v(suite, helper, fx, skips):
    mac = skips["darwin"]
    py = sys.executable

    def _limits_run(flags, names):
        res = _supervised(fx, flags, [py, "-c", PROBE_LIMITS] + list(names))
        problems = _red_guard(res)
        got = None
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _json_out(res)
            if not isinstance(got, dict):
                problems.append("target printed no limits: %r"
                                % res["out"][:120])
                got = None
        return res, problems, got

    def v1():
        want = (("RLIMIT_AS", AS_ROOMY), ("RLIMIT_CPU", 30),
                ("RLIMIT_NOFILE", 256), ("RLIMIT_FSIZE", 64 * 1024 ** 2),
                ("RLIMIT_CORE", 0))
        flags = []
        for name, value in want:
            flags += ["--rlimit-" + name[len("RLIMIT_"):].lower(), str(value)]
        res, problems, got = _limits_run(flags, [n for n, _v in want])
        for name, value in want if got else ():
            if got.get(name) != [value, value]:
                problems.append("%s %r, want soft == hard == %d"
                                % (name, got.get(name), value))
        return problems, _live_detail(res)
    _live_row(suite, GRP_V, "V1 strict-hard-equals-soft", mac, v1)

    def _raise_row(flags, name, value):
        res = _supervised(fx, flags, [py, "-c", PROBE_RAISE, name])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _json_out(res)
            if not isinstance(got, dict):
                problems.append("target printed nothing: %r" % res["out"][:120])
            else:
                for key in ("before", "after"):
                    if got.get(key) != [value, value]:
                        problems.append("%s %s %r, want [%d, %d]"
                                        % (name, key, got.get(key), value,
                                           value))
                for key in ("raise_soft", "raise_hard"):
                    if not str(got.get(key)).startswith("refused:"):
                        problems.append("the target could %s %s: %r"
                                        % (key.replace("_", " "), name,
                                           got.get(key)))
        return problems, _live_detail(res)

    _live_row(suite, GRP_V, "V2 target-cannot-raise-soft", mac,
              lambda: _raise_row(["--rlimit-nofile", "256"],
                                 "RLIMIT_NOFILE", 256))

    def v3():
        marks = [os.path.join(fx["w"], "v3-%s" % k)
                 for k in ("control", "fork", "plain")]
        for mark in marks:
            if os.path.exists(mark):
                os.unlink(mark)
        ctl = _supervised(fx, ["--rlimit-as", str(AS_ROOMY)],
                          [py, "-c", PROBE_MARK, marks[0]])
        problems = _red_guard(ctl, "control: ")
        if not problems and (ctl["rc"] != 0 or not os.path.exists(marks[0])):
            problems.append("control (AS %d): rc %r, target ran %s"
                            % (AS_ROOMY, ctl["rc"], os.path.exists(marks[0])))
        fork = _supervised(fx, ["--rlimit-as", str(AS_BELOW_VSZ)],
                           [py, "-c", PROBE_MARK, marks[1]])
        if not _red_guard(fork):
            if fork["rc"] != 3:
                problems.append("fork mode: rc %r, want 3" % (fork["rc"],))
            problems += ["fork mode: " + p for p in _status_problems(
                fork, "refused", code=3, sig=None, spawned=False)]
            reason = (fork["events"][-1].get("reason", "")
                      if fork["events"] else "")
            if not str(reason).startswith("setrlimit RLIMIT_AS refused ("):
                problems.append("fork mode: reason %r" % (reason,))
        plain = _live(fx, ["--rlimit-as", str(AS_BELOW_VSZ)],
                      [py, "-c", PROBE_MARK, marks[2]], status=False)
        if not _red_guard(plain):
            if plain["rc"] != 3 or "sbx: error" not in plain["err"]:
                problems.append("exec mode: rc %r, stderr %r, want 3 + one "
                                "'sbx: error' line"
                                % (plain["rc"], _first(plain["err"])))
        for label, mark in (("fork mode", marks[1]), ("exec mode", marks[2])):
            if os.path.exists(mark):
                problems.append("%s: the target RAN under a refused limit"
                                % label)
        return problems, (_live_detail(ctl, "control ")
                          + _live_detail(fork, "fork ")
                          + _live_detail(plain, "exec "))
    _live_row(suite, GRP_V, "V3 refused-limit-exit-3", mac, v3)

    def v4():
        before = _uid_count()
        res, problems, got = _limits_run(["--rlimit-nproc", "+64"],
                                         ["RLIMIT_NPROC"])
        after = _uid_count()
        if got:
            lo = min(before, after) + 64 - 32
            hi = max(before, after) + 64 + 32
            value = got.get("RLIMIT_NPROC")
            if not (isinstance(value, list) and len(value) == 2
                    and value[0] == value[1] and lo <= value[0] <= hi):
                problems.append("RLIMIT_NPROC %r, want soft == hard in "
                                "[%d, %d] (uid count %d/%d + 64)"
                                % (value, lo, hi, before, after))
        return problems, _live_detail(res)
    _live_row(suite, GRP_V, "V4 nproc-plus-form-applied", mac, v4)

    def v5():
        res = _supervised(fx, ["--rlimit-cpu", "1"], [py, "-c", PROBE_BUSY])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "signaled", code=None)
            sig = res["events"][-1].get("signal") if res["events"] else None
            allowed = (int(signal.SIGXCPU), int(signal.SIGKILL))
            if sig not in allowed:
                problems.append("signal %r, want SIGXCPU or SIGKILL" % (sig,))
            elif res["rc"] != 128 + sig:
                problems.append("rc %r, want %d" % (res["rc"], 128 + sig))
            if res["elapsed"] > 15:
                problems.append("%.1fs: the wall clock, not RLIMIT_CPU, "
                                "ended it" % res["elapsed"])
        return problems, _live_detail(res)
    _live_row(suite, GRP_V, "V5 cpu-limit-kills-busy-loop", mac, v5)

    def v6():
        path = os.path.join(fx["w"], "v6-fsize.bin")
        res = _supervised(fx, ["--rlimit-fsize", "1024"],
                          [py, "-c", PROBE_FSIZE, path])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _json_out(res)
            if not isinstance(got, dict) or got.get("error") != "EFBIG":
                problems.append("write past 1024 bytes: %r, want EFBIG"
                                % (got,))
            elif got.get("written", 0) > 1024:
                problems.append("%r bytes written under a 1024-byte limit"
                                % got.get("written"))
            if os.path.exists(path) and os.path.getsize(path) > 1024:
                problems.append("file grew to %d bytes" % os.path.getsize(path))
        return problems, _live_detail(res)
    _live_row(suite, GRP_V, "V6 fsize-efbig", mac, v6)

    def v7():
        res = _supervised(fx, ["--rlimit-nofile", "32"],
                          [py, "-c", PROBE_NOFILE])
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _json_out(res)
            if not isinstance(got, dict) or got.get("error") != "EMFILE":
                problems.append("open loop: %r, want EMFILE" % (got,))
            elif got.get("opened", 99) >= 32:
                problems.append("%r fds opened under NOFILE 32"
                                % got.get("opened"))
        return problems, _live_detail(res)
    _live_row(suite, GRP_V, "V7 nofile-emfile", mac, v7)

    _live_row(suite, GRP_V, "V8 core-zero", mac,
              lambda: _raise_row(["--rlimit-core", "0"], "RLIMIT_CORE", 0))


def _env_keys(name, out):
    """The key set a target printed: JSON for python3/node, `env` lines for
    bash's child view."""
    if name == "bash":
        return sorted(ln.split("=", 1)[0] for ln in out.splitlines()
                      if "=" in ln)
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return sorted(got) if isinstance(got, list) else None


def _env_targets():
    """(name, argv) per interpreter of the ENV_ADDITIONS table that exists
    here, plus the names that do not (reported, not failed)."""
    py3, node = shutil.which("python3"), shutil.which("node")
    out, missing = [], []
    for name, path, tail in (
            ("python3", py3 and os.path.abspath(py3), ["-c", PROBE_ENV]),
            ("node", node and os.path.abspath(node),
             ["-e", "console.log(JSON.stringify(Object.keys(process.env)"
                    ".sort()))"]),
            ("bash", "/bin/bash" if os.path.exists("/bin/bash") else None,
             ["-c", "/usr/bin/env; :"])):
        if path:
            out.append((name, [path] + tail))
        else:
            missing.append(name)
    return out, missing


def _prepare(helper, scope_kw, platform):
    """helper._prepare_exec(Scope(**scope_kw)) as on `platform`, with the
    engine detection stubbed: (got, stub_engine_path, builder_argv)."""
    if platform == "darwin":
        builder, engine = helper._seatbelt_argv, "/usr/bin/sandbox-exec"
    else:
        builder, engine = helper._bwrap_argv, "/usr/bin/bwrap"
    fn = getattr(helper, "_prepare_exec", None)
    if fn is None:
        raise LookupError("helper has no _prepare_exec (Step 4 not landed)")
    saved = (sys.platform, helper._detect_engine)
    sys.platform = platform
    helper._detect_engine = lambda: (builder, engine)
    try:
        got = fn(helper.Scope(**scope_kw))
        want = builder(helper.Scope(**scope_kw))
    finally:
        sys.platform, helper._detect_engine = saved
    if not (isinstance(got, tuple) and len(got) == 3):
        raise ValueError("_prepare_exec returned %r, want (engine_path, "
                         "argv, env)" % (got,))
    return got, engine, want


def _cli_real(helper, cwd, argv):
    """helper.main(argv) on a REAL (non-dry) run, in-process, with every exec
    / fork / rlimit stubbed and the darwin engine detected:
    (rc, stderr, execve_calls, execvp_calls)."""
    import resource
    execve, execvp = [], []

    def _ve(path, args, env):
        execve.append((path, list(args), dict(env)))
        raise OSError("execve stubbed by the suite")

    def _vp(path, args):
        execvp.append(list(args))
        raise OSError("execvp stubbed by the suite")

    def _no_fork():
        raise OSError("fork stubbed by the suite")
    saved = (os.getcwd, os.execve, os.execvp, os.fork, resource.setrlimit,
             helper._apply_rlimits, helper._detect_engine, sys.stdout,
             sys.stderr)
    strict = getattr(helper, "_strict_rlimits", None)
    os.getcwd = lambda: cwd
    os.execve, os.execvp, os.fork = _ve, _vp, _no_fork
    resource.setrlimit = lambda *a, **k: None
    helper._apply_rlimits = lambda *a, **k: None
    if strict is not None:
        helper._strict_rlimits = lambda *a, **k: None
    helper._detect_engine = lambda: (helper._seatbelt_argv,
                                     "/usr/bin/sandbox-exec")
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        try:
            rc = helper.main(list(argv))
        except SystemExit as exc:
            rc = exc.code
        except Exception as exc:          # noqa: BLE001 -- a crash is a finding
            rc = "raised %s: %s" % (type(exc).__name__, exc)
        err = sys.stderr.getvalue()
    finally:
        (os.getcwd, os.execve, os.execvp, os.fork, resource.setrlimit,
         helper._apply_rlimits, helper._detect_engine, sys.stdout,
         sys.stderr) = saved
        if strict is not None:
            helper._strict_rlimits = strict
    return rc, err, execve, execvp


def _run_t(suite, helper, fx, skips):
    mac = skips["darwin"]
    canary = {CANARY_KEY: CANARY_VALUE}

    def t1():
        targets, missing = _env_targets()
        problems = []
        extra = ["not installed: %s" % ", ".join(missing)] if missing else []
        for name, target in targets:
            res = _live(fx, ["--clean-env", "--env", "A=1", "--env", "B=2"],
                        target, status=False, env_over=canary)
            guard = _red_guard(res, "%s: " % name)
            if guard:
                return guard, extra + _live_detail(res, name + " ")
            want = sorted({"A", "B"}
                          | set(ENV_ADDITIONS[LIVE_ENV_PROFILE][name]))
            got = _env_keys(name, res["out"])
            extra.append("%-8s: additions %s" % (
                name, ENV_ADDITIONS_SOURCE[LIVE_ENV_PROFILE][name]))
            if res["rc"] != 0:
                problems.append("%s: rc %r (%s)" % (name, res["rc"],
                                                    _first(res["err"])))
            if got != want:
                problems.append("%s: keys %r, want exactly %r"
                                % (name, got, want))
            if CANARY_VALUE in res["out"]:
                problems.append("%s: the canary value reached the target"
                                % name)
        return problems, extra
    _live_row(suite, GRP_T, "T1 clean-env-exact-set", mac, t1)

    def t2():
        py3 = os.path.abspath(shutil.which("python3") or sys.executable)
        ctl = _live(fx, [], [py3, "-c", PROBE_ENV], status=False,
                    env_over=canary)
        problems = []
        if CANARY_KEY not in (_env_keys("python3", ctl["out"]) or ()):
            problems.append("control: the legacy (inherited) run does not "
                            "show the canary -- the probe sees nothing")
        res = _live(fx, ["--clean-env"], [py3, "-c", PROBE_ENV],
                    status=False, env_over=canary)
        guard = _red_guard(res)
        if guard:
            return problems + guard, _live_detail(res)
        got = _env_keys("python3", res["out"])
        want = sorted(ENV_ADDITIONS[LIVE_ENV_PROFILE]["python3"])
        if got != want:
            problems.append("--clean-env without --env: keys %r, want "
                            "exactly %r (the measured %s additions)"
                            % (got, want, LIVE_ENV_PROFILE))
        if CANARY_VALUE in res["out"] or CANARY_VALUE in res["err"]:
            problems.append("the canary value reached the target")
        return problems, _live_detail(res)
    _live_row(suite, GRP_T, "T2 canary-not-inherited", mac, t2)

    def t3():
        kw = dict(BW_SCOPE_MERGED, env={"B": "2", "A": "1"})
        (engine, argv, env), stub, want = _prepare(helper, kw, "linux")
        problems = []
        if env != {} or type(env) is not dict:
            problems.append("engine env %r, want {} (C-M7)" % (env,))
        if engine != stub:
            problems.append("engine path %r, want the detected %r"
                            % (engine, stub))
        if "/usr/bin/env" in argv:
            problems.append("the darwin trampoline leaked into the bwrap argv")
        problems += _first_diff(argv, want, "argv item")
        if not _has_seq(argv, ["--clearenv", "--setenv", "A", "1",
                               "--setenv", "B", "2"]):
            problems.append("no sorted --clearenv --setenv in the argv")
        return problems, []
    _guard(suite, GRP_T, "T3 engine-env-empty-linux-golden", t3)

    def t4():
        kw = dict(SB_SCOPE, env={"B": "2", "A": "1"})
        (engine, argv, env), stub, want = _prepare(helper, kw, "darwin")
        tail = ["/usr/bin/env", "-i", "A=1", "B=2"] + SB_SCOPE["argv"]
        problems = []
        if env != {} or type(env) is not dict:
            problems.append("engine env %r, want {} (C-M7 = S-M1)" % (env,))
        if engine != stub:
            problems.append("engine path %r, want the detected %r"
                            % (engine, stub))
        problems += _first_diff(argv, want[:3] + tail, "argv item")
        if len(argv) > 2 and '(literal "/usr/bin/env")' not in argv[2]:
            problems.append("the profile does not exec-allow /usr/bin/env")
        return problems, ["argv tail   : %r" % (argv[3:],)]
    _guard(suite, GRP_T, "T4 darwin-engine-env-empty-trampoline-golden", t4)

    def t5():
        problems = []
        for platform, golden in (("darwin", LEGACY_SEATBELT),
                                 ("linux", LEGACY_BWRAP)):
            (_engine, argv, env), _stub, _want = _prepare(
                helper, LEGACY_SCOPE, platform)
            if env is not None:
                problems.append("%s: env %r, want None (inherited; the "
                                "legacy execvp passes no env)"
                                % (platform, type(env).__name__))
            problems += ["%s: %s" % (platform, p)
                         for p in _first_diff(argv, golden, "argv item")]
        return problems, []
    _guard(suite, GRP_T, "T5 legacy-env-inherited-unchanged", t5)

    def t6():
        rc, err, execve, execvp = _cli_real(helper, fx["w"], [
            "--write", fx["w"], "--clean-env", "--env", "A=1", "--",
            "/bin/echo", "x"])
        problems = []
        tail = ["/usr/bin/env", "-i", "A=1", "/bin/echo", "x"]
        if GUARD_TEXT in err:
            problems.append("control: RED: the real run stopped at the "
                            "pre-Step-4 guard (rc %r), never reached the "
                            "exec site" % (rc,))
        elif not execve or execve[-1][2] != {} \
                or execve[-1][1][3:] != tail or execvp:
            problems.append("control: execve %r / execvp %r, want one "
                            "execve(<engine>, [sandbox-exec -p <profile> "
                            "%s], {})" % (execve[-1:], execvp,
                                          " ".join(tail)))
        rc, err, execve, execvp = _cli_real(helper, fx["w"], [
            "--write", fx["w"], "--clean-env", "--", fx["r"] + "/A=1", "x"])
        if rc != 2:
            problems.append("argv0 with '=': rc %r, want 2" % (rc,))
        if execve or execvp:
            problems.append("argv0 with '=': exec'd %r" % (execve or execvp,))
        if "sbx: error" not in err:
            problems.append("argv0 with '=': no 'sbx: error' line")
        return problems, []
    _live_row(suite, GRP_T, "T6 darwin-argv0-with-equals-refused",
              None if sys.platform == "darwin" else
              "darwin-only (the env -i trampoline); this host is %s"
              % sys.platform, t6)

    def t7():
        py3 = os.path.abspath(shutil.which("python3") or sys.executable)
        res = _supervised(fx, ["--clean-env", "--env", "A=1", "--env", "B=2"],
                          [py3, "-c", PROBE_ENV], env_over=canary)
        problems = _red_guard(res)
        if not problems:
            problems += _status_problems(res, "exited", code=0, sig=None)
            got = _env_keys("python3", res["out"])
            want = sorted({"A", "B"}
                          | set(ENV_ADDITIONS[LIVE_ENV_PROFILE]["python3"]))
            if got != want:
                problems.append("keys %r, want exactly %r (the --env keys + "
                                "the measured %s additions)"
                                % (got, want, LIVE_ENV_PROFILE))
            if CANARY_VALUE in res["out"]:
                problems.append("the canary value reached the target")
        return problems, _live_detail(res)
    _live_row(suite, GRP_T, "T7 darwin-trampoline-live-exact-env", mac, t7)


# ---------------------------------------------------------------------------
# L: live restricted Seatbelt (Step 6) -- the containment proof. Each run is
# shaped the way mcp-sandbox builds one (its Mach-O closure lands in Step 8,
# so the read set is spelled out here from the M0 measurements): argv0 is the
# interpreter's REALPATH (Seatbelt's exec filter matches the realpath, M0:3)
# plus python's Python.app launcher as --exec; /bin and /usr/bin are exec
# dirs; every symlink hop (incl. a symlinked ANCESTOR such as
# /usr/local/opt/node) is a --read literal and every Homebrew keg a --read
# subpath (M0:3, item 6); node reads openssl.cnf (M0:6); every invocation
# reads /private/var/select/sh (M0:5); --clean-env with PATH. The probe is
# copied INTO a fresh write scope (the way a script lands in a workdir), so
# no repo path is granted. The rows share the P23 control of _live_skips.
# ---------------------------------------------------------------------------

PROBE_DIR = H.repo_path("tests", "files", "sbx_restrict")
L_EXEC_DIRS = ("/bin", "/usr/bin")
L_SELECT_SH = "/private/var/select/sh"
L_OPENSSL_CNF = "/usr/local/etc/openssl@3/openssl.cnf"
L_ENV = ("--clean-env", "--env", "PATH=/usr/bin:/bin")
L_DEPTH_FORMS = ("abs101", "at101", "chdir101", "file_in_100",
                 "rename_to_101")
L_SH_ERROR = "Error opening /private/var/select/sh"
L_WD_TOKEN = "@WD@"                   # replaced by the row's write scope
L_CELLAR = "/usr/local/Cellar/"
# the dyld shared cache serves these: no read grant needed (item 6)
L_SHARED_CACHE = ("/usr/lib/", "/System/")


def _symlink_hops(path):
    """Every symlink spelling met while resolving `path` -- ANCESTOR
    components as well as the final one (item 6: a symlinked parent dir must
    be a read literal). Pure lexical walk over lstat/readlink."""
    hops, parts, cur, guard = [], path.strip("/").split("/"), "/", 0
    i = 0
    while i < len(parts):
        nxt = os.path.join(cur, parts[i])
        if os.path.islink(nxt):
            guard += 1
            if guard > 40:
                raise ValueError("symlink loop under %r" % path)
            hops.append(nxt)
            target = os.readlink(nxt)
            base = os.path.normpath(target if target.startswith("/")
                                    else os.path.join(cur, target))
            parts = base.strip("/").split("/") + parts[i + 1:]
            cur, i = "/", 0
            continue
        cur, i = nxt, i + 1
    return hops


def _closure_reads(spellings):
    """The --read set for executables `spellings`: each symlink hop as a
    literal, each Homebrew keg root (/usr/local/Cellar/<name>/<version>) as a
    subpath, any other non-shared-cache image as a literal, walked through
    `otool -L` for /usr/local and /opt install names."""
    reads, seen, queue = [], set(), list(spellings)
    while queue:
        spelling = queue.pop(0)
        if spelling in seen:
            continue
        seen.add(spelling)
        for hop in _symlink_hops(spelling):
            if hop not in reads:
                reads.append(hop)
        real = os.path.realpath(spelling)
        if real.startswith(L_CELLAR):
            grant = "/".join(real.split("/")[:6])
        elif real.startswith(L_SHARED_CACHE):
            continue
        else:
            grant = real
        if grant not in reads:
            reads.append(grant)
        proc = subprocess.run(["/usr/bin/otool", "-L", real], env={},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=30)
        for line in proc.stdout.splitlines()[1:]:
            name = line.strip().split(" (", 1)[0]
            if name.startswith(("/usr/local/", "/opt/")):
                queue.append(name)
    return reads


def _l_plan(name):
    """(argv0, flags) of the restricted run for interpreter `name`, or
    (None, reason) when it is not installed here."""
    execs, extra_reads = [], []
    if name == "python3":
        argv0 = os.path.realpath(sys.executable)
        launcher = os.path.join(os.path.realpath(sys.base_prefix), "Resources",
                                "Python.app", "Contents", "MacOS", "Python")
        if os.path.exists(launcher):
            execs.append(launcher)
        images = [sys.executable] + execs
    elif name == "node":
        node = shutil.which("node")
        if not node:
            return None, "node is not installed on this host"
        argv0, images = os.path.realpath(node), [os.path.abspath(node)]
        if os.path.exists(L_OPENSSL_CNF):
            extra_reads.append(L_OPENSSL_CNF)
    else:
        argv0, images = "/bin/bash", ["/bin/bash"]
    flags = ["--restrict-reads", "--timeout", "30"] + list(L_ENV)
    for path in execs + list(L_EXEC_DIRS):
        flags += ["--exec", path]
    for path in _closure_reads(images) + extra_reads + [L_SELECT_SH]:
        flags += ["--read", path]
    return argv0, flags


def _l_run(fx, name, label, probe, args=(), prepare=None):
    """Copy tests/files/sbx_restrict/<probe> into a fresh write scope and
    run it under interpreter `name`; `@WD@` in an arg is the scope, and
    `prepare(wd)` plants fixtures first: (res, json_or_None, wd, flags)."""
    argv0, flags = _l_plan(name)
    if argv0 is None:
        raise LookupError(flags)
    wd = os.path.join(fx["root"], "L-" + label)
    os.makedirs(wd)
    if prepare is not None:
        prepare(wd)
    script = os.path.join(wd, probe)
    shutil.copy(os.path.join(PROBE_DIR, probe), script)
    target = [argv0, script] + [a.replace(L_WD_TOKEN, wd) for a in args]
    res = _live({"w": wd}, flags, target, status=False)
    return res, _json_out(res), wd, flags


def _l_detail(res, flags, got):
    reads = [flags[i + 1] for i, f in enumerate(flags[:-1]) if f == "--read"]
    return (_live_detail(res)
            + ["result      : %r" % (got,),
               "reads       : %d (%s)" % (len(reads), ", ".join(reads)[:200])])


def _l_compare(got, want):
    """Problems of a probe result against {key: wanted value}."""
    return ["%s %r, want %r" % (key, got.get(key), value)
            for key, value in want if got.get(key) != value]


def _has_acl(path):
    """True when `path` carries an extended ACL (read as the owner, outside
    the profile; the link itself, never followed)."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_link_np.restype = ctypes.c_void_p
    libc.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_free.argtypes = [ctypes.c_void_p]
    acl = libc.acl_get_link_np(path.encode(), 0x100)   # ACL_TYPE_EXTENDED
    if not acl:
        return False
    libc.acl_free(acl)
    return True


def _l_plant(wd):
    """L6's planted executable: a script in the write scope (M0 3b)."""
    planted = os.path.join(wd, "tf_planted")
    with open(planted, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(planted, 0o755)


def _run_l(suite, helper, fx, skips):
    mac = skips["darwin"]
    cache = {}

    def read_probe():
        """One probe_read.py run feeds L1-L8 and L11 (one interpreter start)."""
        if "read" not in cache:
            cache["read"] = _l_run(fx, "python3", "read", "probe_read.py", (
                os.path.expanduser("~/.ssh/known_hosts"),
                os.path.expanduser("~"), H.repo_path("CLAUDE.md"),
                os.path.join(L_WD_TOKEN, "tf_planted"),
                fx["file"], os.path.join(fx["r"], "missing"),
                fx["r"], fx["missing"]), prepare=_l_plant)
        return cache["read"]

    def row(name, want, check=None, keys=()):
        """A row over the shared probe_read.py result."""
        def fn():
            res, got, _wd, flags = read_probe()
            if not isinstance(got, dict):
                return (["probe printed no JSON (rc %r): %s"
                         % (res["rc"], res["err"].strip()[:200])],
                        _l_detail(res, flags, got))
            problems = _l_compare(got, want)
            extra = []
            if check is not None:
                more, extra = check(res, got)
                problems += more
            shown = [k for k, _v in want] + list(keys)
            return problems, _l_detail(res, flags, {k: got.get(k)
                                                    for k in shown}) + extra
        _live_row(suite, GRP_L, name, mac, fn)

    # assert the denial only: whether the file exists here is not the point
    row("L1 ssh-known-hosts-read-denied", (), lambda _r, got: (
        [] if got.get("known_hosts") not in (None, "ok") else
        ["known_hosts %r: the read was NOT denied"
         % (got.get("known_hosts"),)], []), keys=("known_hosts",))
    row("L2 home-listing-denied", (("home_listing", "EPERM"),))
    row("L3 etc-passwd-denied", (("etc_passwd", "EPERM"),))
    row("L4 repo-file-denied", (("repo_file", "EPERM"),))

    def l5_declared(_res, got):
        # IMPL:029 -- MEASURED on macOS 14.2: Seatbelt checks the TERMINAL
        # vnode only. An existing path outside every grant answers EPERM (no
        # size, no times: the gated half), but a MISSING name answers ENOENT
        # -- there is no vnode to deny -- whether its parent is a
        # metadata-granted ancestor or a directory nothing grants. Existence
        # is therefore an oracle for any path the uid can traverse; no path
        # rule can close it. Recorded, never asserted away.
        problems, seen = [], []
        for key in ("stat_missing", "stat_missing_child"):
            value = got.get(key)
            seen.append("%s=%s" % (key, value))
            if value not in ("ENOENT", "EPERM"):
                problems.append("%s %r, want ENOENT (declared) or EPERM"
                                % (key, value))
        return problems, ["declared    : existence oracle -- a missing name "
                          "answers ENOENT, an existing one EPERM (%s; "
                          "IMPL:029)" % ", ".join(seen)]
    # the gated half: an existing path outside every grant yields EPERM and
    # so no metadata, under an ungranted dir and under a granted ancestor
    row("L5 stat-oracle-denied", (("stat_existing", "EPERM"),
                                  ("stat_existing_child", "EPERM")),
        l5_declared, keys=("stat_missing", "stat_missing_child"))
    row("L6 exec-planted-binary-denied", (("exec_planted", "exec EPERM"),))

    def l7():
        res, got, _wd, flags = read_probe()
        count = got.get("root_listing") if isinstance(got, dict) else None
        detail = _l_detail(res, flags, {"root_listing": count}) + [
            "declared    : readdir of \"/\" is granted on purpose "
            "((allow file-read-data (literal \"/\")), ADR 0031): the "
            "top-level NAMES are visible, nothing below them"]
        if _is_int(count):
            return [], detail
        return ["root listing %r: the declared grant does not hold"
                % (count,)], detail
    if mac:
        _live_row(suite, GRP_L, "L7 root-listing-declared", mac, l7)
    else:
        try:
            problems, detail = l7()
        except Exception as exc:          # noqa: BLE001 -- reported, not hidden
            problems, detail = ["raised %s: %s" % (type(exc).__name__,
                                                    exc)], []
        # declared, not gated: INFO while the grant holds as declared
        _rec(suite, GRP_L, "L7 root-listing-declared", problems, detail,
             status=None if problems else H.INFO)

    row("L8 python-runs", (("python", "ok"),), lambda res, _got: (
        [] if res["rc"] == 0 else ["rc %r, want 0" % (res["rc"],)], []))

    def l9():
        res, got, _wd, flags = _l_run(fx, "bash", "bash", "probe_exec.sh")
        problems = [] if res["rc"] == 0 else ["rc %r, want 0" % (res["rc"],)]
        if not isinstance(got, dict):
            problems.append("probe printed no JSON: %r" % res["out"][:120])
        else:
            # 143 = the background job killed by `kill $!` (same-sandbox)
            problems += _l_compare(got, (("subshell", "sub"), ("sh", "sh"),
                                         ("bg_kill_status", 143)))
        if L_SH_ERROR in res["err"]:
            problems.append("/bin/sh printed %r (the M0:5 read is missing)"
                            % L_SH_ERROR)
        return problems, _l_detail(res, flags, got)
    _live_row(suite, GRP_L, "L9 bash-runs-with-subshell", mac, l9)

    def l10():
        res, got, _wd, flags = _l_run(fx, "node", "node", "probe_node.js")
        problems = [] if res["rc"] == 0 else ["rc %r, want 0" % (res["rc"],)]
        if not isinstance(got, dict):
            problems.append("probe printed no JSON: %r"
                            % res["err"].strip()[:200])
        else:
            problems += _l_compare(got, (("require_os", "ok"), ("cpus", "ok"),
                                         ("write", "ok")))
        return problems, _l_detail(res, flags, got)
    _live_row(suite, GRP_L, "L10 node-runs-require-os",
              mac or (None if shutil.which("node") else
                      "node is not installed on this host"), l10)

    def l11():
        res, got, wd, flags = read_probe()
        uchg = os.path.join(wd, "tf_uchg")
        want = (("acl_set_file", "EPERM"), ("chflags_uchg", "ok"),
                ("copy2", "ok"), ("copytree", "ok"), ("write_chmod_x", "ok"))
        extra = []
        try:
            if not isinstance(got, dict):
                return (["probe printed no JSON (rc %r): %s"
                         % (res["rc"], res["err"].strip()[:200])],
                        _l_detail(res, flags, got))
            problems = _l_compare(got, want)
            # IMPL:029 -- MEASURED: under the restricted profile `chmod +a`
            # stops BEFORE the kernel ("Unable to translate 'everyone' to a
            # UUID": no mach-lookup to opendirectoryd), so it never reaches
            # the file-write-acl deny that acl_set_file proves above. The
            # vector is gated by its OUTCOME: refused, and no ACL on disk.
            chmod_acl = got.get("chmod_acl")
            if chmod_acl == "ok":
                problems.append("chmod +a succeeded inside the write scope")
            elif chmod_acl != "EPERM":
                extra.append("chmod +a    : refused before the kernel: %s"
                             % chmod_acl)
            for name in ("tf_acl_cmd", "tf_acl_lib"):
                if _has_acl(os.path.join(wd, name)):
                    problems.append("%s carries an extended ACL" % name)
            # CONTROL: the same ACE set by the owner, unsandboxed, is seen
            control = os.path.join(wd, "tf_acl_control")
            with open(control, "w") as fh:
                fh.write("x")
            subprocess.run(["/bin/chmod", "+a", "everyone deny delete",
                            control], env={}, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
            if not _has_acl(control):
                problems.append("control: the owner's chmod +a left no ACL "
                                "the check can see")
            if got.get("chflags_uchg") == "ok" and not (
                    os.lstat(uchg).st_flags & stat.UF_IMMUTABLE):
                problems.append("chflags_uchg reported ok, uchg not set")
        finally:
            # the owner, outside the profile: flags are deliberately NOT
            # denied, so the test clears uchg before its cleanup (M0:8);
            # an ACL that slipped through is stripped the same way
            if os.path.lexists(uchg):
                os.lchflags(uchg, 0)
            for name in ("tf_acl_cmd", "tf_acl_lib", "tf_acl_control"):
                path = os.path.join(wd, name)
                if os.path.lexists(path):
                    subprocess.run(["/bin/chmod", "-N", path], env={},
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10)
        shown = ("chmod_acl",) + tuple(k for k, _v in want)
        return problems, _l_detail(res, flags,
                                   {k: got.get(k) for k in shown}) + extra
    _live_row(suite, GRP_L,
              "L11 acl-writes-denied-flags-allowed-in-write-scope", mac, l11)

    def l12():
        res, got, wd, flags = _l_run(fx, "python3", "depth", "probe_depth.py",
                                     (L_WD_TOKEN,))
        problems = [] if res["rc"] == 0 else ["rc %r, want 0" % (res["rc"],)]
        if not isinstance(got, dict):
            problems.append("probe printed no JSON: %s"
                            % res["err"].strip()[:200])
        else:
            problems += _l_compare(got, (("level100", "ok"),
                                         ("deepest", 100)))
            problems += _l_compare(got, tuple((k, "EPERM")
                                              for k in L_DEPTH_FORMS))
            problems += _l_compare(got, (("control_1_10", "ok"),))
        # the owner removes the probe's chains (a 100-deep path is short)
        shutil.rmtree(wd, ignore_errors=True)
        if os.path.exists(wd):
            problems.append("the owner could not remove %s" % wd)
        return problems, _l_detail(res, flags, got) + [
            "declared    : the cap refuses a level-101 mkdir/rename "
            "DESTINATION; a rename-chained subtree still grows past it "
            "(R-13 / ADR 0032), which the mcp-sandbox flatten remover "
            "bounds -- this row does not claim that closed"]
    _live_row(suite, GRP_L, "L12 depth-cap-101-refused", mac, l12)


# ---------------------------------------------------------------------------
# S: gate grammar (pure; the gate is imported, WRAPPER_PATH spelled first)
# ---------------------------------------------------------------------------

def _run_s(suite, helper, fx):
    gate = G._load_gate()
    cwd = fx["root"]

    def verdict(flags):
        cmd = " ".join([gate.WRAPPER_PATH] + ([flags] if flags else [])
                       + ["--", "/bin/echo", "x"])
        return gate.is_clean_sbx(cmd, cwd)

    def allowed(*flag_sets):
        out = []
        for flags in flag_sets:
            ok, reason = verdict(flags)
            if ok is not True or not reason:
                out.append("%r prompts, want auto-allow" % flags)
        return out

    def prompts(*flag_sets):
        out = []
        for flags in flag_sets:
            got = verdict(flags)
            if got != (False, ""):
                out.append("%r -> %r, want (False, '')" % (flags, got))
        return out

    def row(name, allow=(), prompt=()):
        def fn():
            # base identity control: the bare wrapper must auto-allow, so a
            # row can never pass on a gate that prompts for everything
            return (allowed("") + allowed(*allow) + prompts(*prompt)), []
        _guard(suite, GRP_S, name, fn)

    row("S1 restrict-reads-allowed", ["--restrict-reads"],
        ["--restrict-reads=1"])
    row("S2 read-space-form-allowed",
        ["--restrict-reads --read /usr/lib",
         "--restrict-reads --read /usr/lib --read /etc/ssl"],
        ["--restrict-reads --read"])
    row("S3 read-equals-form-allowed", ["--restrict-reads --read=/usr/lib"],
        ["--restrict-reads --read="])
    row("S4 exec-allowed",
        ["--restrict-reads --exec /bin", "--restrict-reads --exec=/usr/bin"],
        ["--restrict-reads --exec"])
    row("S5 clean-env-allowed", ["--clean-env"], ["--clean-env=1"])
    row("S6 env-allowed",
        ["--clean-env --env A=1", "--clean-env --env _B9=x --env C="],
        ["--clean-env --env"])
    row("S7 env-equals-form-allowed", ["--clean-env --env=A=1"],
        ["--clean-env --env="])
    row("S8 env-bad-key-prompts", ["--clean-env --env A=1"],
        ["--clean-env --env 1A=1", "--clean-env --env A-B=1",
         "--clean-env --env =1", "--clean-env --env A",
         "--clean-env --env=1A=1"])
    row("S9 env-ld-preload-prompts", ["--clean-env --env PRELOAD=1"],
        ["--clean-env --env LD_PRELOAD=/x.so",
         "--clean-env --env LD_LIBRARY_PATH=/x",
         "--clean-env --env=DYLD_INSERT_LIBRARIES=/x"])
    row("S10 timeout-allowed", ["--timeout 5", "--timeout=0.5"],
        # U+0665 is an Arabic-Indic five: str.isdigit() would accept it
        ["--timeout 5s", "--timeout=-1", "--timeout " + chr(0x0665),
         "--timeout"])
    row("S11 rlimit-as-above-default-prompts",
        ["--rlimit-as %d" % (8 * GIB_I), "--rlimit-cpu=900"],
        ["--rlimit-as %d" % (8 * GIB_I + 1), "--rlimit-as=99999999999999",
         "--rlimit-cpu 901", "--rlimit-as 1e9"])
    # the nproc forms, plus the three strict limits with no sbx default
    # (fsize / nofile / core). Only an ABSOLUTE nproc N <= 512 narrows (R2:
    # `+N` is headroom over the uid's CURRENT count, so it can exceed sbx's
    # absolute 512), and only --rlimit-core 0 narrows (R1: a non-zero core
    # enables kernel core dumps outside every write scope)
    row("S12 rlimit-nproc-absolute-only",
        ["--rlimit-nproc 512",
         "--rlimit-fsize 1048576 --rlimit-nofile 256 --rlimit-core 0"],
        ["--rlimit-nproc +64", "--rlimit-nproc=+512", "--rlimit-nproc +513",
         "--rlimit-nproc 513", "--rlimit-nproc ++1", "--rlimit-core 1"])
    row("S13 net-with-restrict-still-prompts", ["--restrict-reads"],
        ["--restrict-reads --net", "--net --restrict-reads", "--net"])

    def s14():
        problems = []
        parsed = set()
        for action in helper._build_parser()._actions:
            parsed.update(s for s in action.option_strings
                          if s not in ("-h", "--help"))
        for must in ("--write", "--net", "--restrict-reads", "--status-fd"):
            if must not in parsed:     # control: the parser side is real
                problems.append("control: sbx's parser lacks %s" % must)
        names = ("_BOOL_FLAGS", "_VALUE_FLAGS", "_PROMPT_FLAGS")
        tables = [getattr(gate, n, None) for n in names]
        absent = [n for n, t in zip(names, tables) if t is None]
        if absent:
            return problems + ["gate has no %s (Step 5)"
                               % ", ".join(absent)], []
        union = set()
        for name, table in zip(names, tables):
            overlap = union & set(table)
            if overlap:
                problems.append("%s overlaps another table: %s"
                                % (name, sorted(overlap)))
            union |= set(table)
        if union != parsed:
            problems.append("gate-only %s; parser-only %s"
                            % (sorted(union - parsed), sorted(parsed - union)))
        if "--status-fd" not in set(tables[2]) or "--net" not in set(tables[2]):
            problems.append("_PROMPT_FLAGS %r lacks --net / --status-fd"
                            % (sorted(tables[2]),))
        return problems, ["parser flags: %d" % len(parsed)]
    _guard(suite, GRP_S, "S14 gate-flag-table-matches-parser", s14)

    row("S15 read-or-exec-relative-prompts",
        ["--restrict-reads --read /usr/lib --exec=/bin"],
        ["--restrict-reads --read rel/x", "--restrict-reads --exec=bin/x",
         "--restrict-reads --read ~/x", "--restrict-reads --exec ./x"])
    row("S16 status-fd-prompts",
        ["--timeout 5", "--restrict-reads --timeout=5"],
        ["--timeout 5 --status-fd 3", "--timeout=5 --status-fd=9",
         "--restrict-reads --timeout 5 --status-fd 3",
         "--restrict-reads --timeout=5 --status-fd=9"])

    def s17():
        # egress opens a network path the gate cannot judge: both flags are
        # _PROMPT_FLAGS members and prompt in every spelling, beside the
        # restricted control that still auto-allows
        problems = allowed("--restrict-reads", "--restrict-reads --clean-env")
        table = getattr(gate, "_PROMPT_FLAGS", ())
        for flag in ("--egress-port", "--egress-socket"):
            if flag not in table:
                problems.append("%s is not in _PROMPT_FLAGS %r"
                                % (flag, sorted(table)))
        problems += prompts(
            "--restrict-reads --egress-port 3128",
            "--restrict-reads --egress-port=3128",
            "--egress-port 3128 --restrict-reads --clean-env",
            "--restrict-reads --egress-socket /x/s",
            "--restrict-reads --egress-socket=/x/s",
            "--restrict-reads --dry-run --egress-socket /x/s")
        return problems, []
    _guard(suite, GRP_S, "S17 egress-flags-always-prompt", s17)


# ---------------------------------------------------------------------------
# X: security review round 1 (pure / in-process; both OSes)
# ---------------------------------------------------------------------------

def _raises_value_error(fn, *args):
    """'' when fn(*args) raises ValueError, else what it did instead."""
    try:
        got = fn(*args)
    except ValueError:
        return ""
    return "returned %r" % (str(got)[:80],)


def _status_bytes(helper, obj):
    """What helper._write_status(fd, obj) writes on a pipe, read to EOF."""
    r, w = os.pipe()
    try:
        helper._write_status(w, obj)
    finally:
        os.close(w)
    data = b""
    try:
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                return data
            data += chunk
    finally:
        os.close(r)


def _run_x(suite, helper, fx):
    evil = os.path.join(fx["root"], INJECT_DIR)
    os.makedirs(evil, exist_ok=True)

    def x1():
        # P1 restricted: the payload rode in through the cwd's ancestors into
        # the metadata LITERALS; `--read <root>` keeps the cwd inside a scope
        problems = _accepted(helper, fx, ["--read", fx["root"]],
                             "a clean cwd under the --read scope",
                             cwd=fx["nested"])
        problems += _refused(helper, fx, ["--read", fx["root"]],
                             "restricted, cwd is an SBPL payload",
                             needle="working directory", want=3, cwd=evil)
        return problems, ["cwd         : %r" % evil]
    _guard(suite, GRP_X, "X1 restricted-cwd-sbpl-payload-refused", x1)

    def x2():
        # P1 legacy (pre-existing): <cwd>/.claude/tmp is the default write
        # SUBPATH and every ancestor .git/config a deny -- --dry-run included
        problems = []
        for label, cwd, want in (("control", fx["w"], 0),
                                 ("payload cwd", evil, 3)):
            for flags in ([], ["--ro"]):
                rc, out, err, execs = _cli(
                    helper, cwd, flags + ["--dry-run", "--", ARGV0, "x"])
                if rc != want:
                    problems.append("legacy %s %s: rc %r, want %d (%s)"
                                    % (label, flags, rc, want, _first(err)))
                if want and "sbx plan" in out:
                    problems.append("legacy %s %s: printed a plan"
                                    % (label, flags))
                if execs:
                    problems.append("legacy %s: exec'd" % label)
        return problems, []
    _guard(suite, GRP_X, "X2 legacy-cwd-sbpl-payload-refused", x2)

    def x3():
        # P1 builder: every interpolated path passes ONE refusal point
        problems = []
        for label, kw in (("restricted control", SB_SCOPE),
                          ("legacy control", LEGACY_SCOPE)):
            try:
                helper._seatbelt_argv(helper.Scope(**kw))
            except Exception as exc:      # noqa: BLE001 -- a control must build
                problems.append("%s raised %r" % (label, exc))
        bad = "/x/" + INJECT_DIR
        tab = "/x/tab\tdir"
        cases = (
            ("meta_literal", SB_SCOPE, dict(meta_literals=SB_META + (bad,))),
            ("restricted write", SB_SCOPE, dict(writes=[bad])),
            ("read literal", SB_SCOPE,
             dict(read_rules=SB_READ_RULES + ((bad, "file", None),))),
            ("exec dir", SB_SCOPE,
             dict(exec_rules=SB_EXEC_RULES + ((bad, True),))),
            ("scratch write", LEGACY_SCOPE, dict(writes=[bad + "/.claude/tmp"])),
            ("tab write", LEGACY_SCOPE, dict(writes=[tab])),
            ("file secret", LEGACY_SCOPE,
             dict(secret_paths=((bad + "/.git/config", False),))),
            ("dir secret", LEGACY_SCOPE, dict(secret_paths=((bad, True),))),
            ("carve-out", LEGACY_SCOPE, dict(carveouts=(bad,))),
            ("shadow deny", LEGACY_SCOPE, dict(shadow_write_denies=(bad,))),
        )
        for label, base, over in cases:
            why = _raises_value_error(helper._seatbelt_argv,
                                      helper.Scope(**dict(base, **over)))
            if why:
                problems.append("%s with a payload path: %s, want ValueError"
                                % (label, why))
        return problems, []
    _guard(suite, GRP_X, "X3 seatbelt-builder-refuses-injected-path", x3)

    def x4():
        # P2: a symlink in the fixture tree into a socket directory, and the
        # /var/run spelling itself (a symlink to /run, or /private/var/run)
        links = []
        for name, target in (("to-var-run", "/var/run"), ("to-tmp", "/tmp"),
                             ("to-run-sub", "/run/x")):
            link = os.path.join(fx["root"], name)
            if not os.path.lexists(link):
                os.symlink(target, link)
            links.append(link)
        hop = os.path.join(fx["root"], "hop-to-var-run")
        if not os.path.lexists(hop):
            os.symlink(links[0], hop)     # two hops: hop -> to-var-run -> /var/run
        problems = _accepted(helper, fx, ["--read", fx["link"],
                                          "--exec", fx["link"]],
                             "a symlink to a regular file")
        refused = [("--read", p) for p in links[:2] + [hop]]
        refused.append(("--exec", links[0]))
        if os.path.lexists("/var/run"):
            refused.append(("--read", "/var/run"))
        for flag, path in refused:
            problems += _refused(helper, fx, [flag, path],
                                 "%s %s" % (flag, path), needle="socket")
        # a dangling hop into /run is refused by its spelling alone
        why = helper.socket_read_refusal(links[2])
        if why is None:
            problems.append("a dangling link into /run was not refused")
        return problems, []
    _guard(suite, GRP_X, "X4 read-resolving-into-socket-dir-refused", x4)

    def x5():
        # P2: a socket FILE outside every socket directory, and a link to it
        sock_path = os.path.join(fx["root"], "s.sock")
        link = os.path.join(fx["root"], "s-link")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        here = os.getcwd()
        try:
            os.chdir(fx["root"])          # a relative bind: no sun_path limit
            try:
                sock.bind("s.sock")
            finally:
                os.chdir(here)
            if not os.path.lexists(link):
                os.symlink(sock_path, link)
            problems = _accepted(helper, fx, ["--read", fx["file"]],
                                 "a regular file")
            for path in (sock_path, link):
                problems += _refused(helper, fx, ["--read", path],
                                     "--read %s" % path, needle="socket")
        finally:
            sock.close()
        return problems, []
    _guard(suite, GRP_X, "X5 read-of-a-socket-refused", x5)

    def x6():
        # R1: setrlimit(value, value) must never RAISE the inherited soft
        import resource
        calls = []
        limits = {"RLIMIT_CORE": (0, resource.RLIM_INFINITY),
                  "RLIMIT_NOFILE": (256, resource.RLIM_INFINITY)}
        by_id = {getattr(resource, n): v for n, v in limits.items()}
        saved = (resource.getrlimit, resource.setrlimit)
        resource.getrlimit = lambda which: by_id[which]
        resource.setrlimit = lambda which, pair: calls.append((which, pair))
        problems = []
        try:
            for name, value, want_ok in (("RLIMIT_CORE", 0, True),
                                         ("RLIMIT_NOFILE", 256, True),
                                         ("RLIMIT_NOFILE", 32, True),
                                         ("RLIMIT_CORE", 1048576, False),
                                         ("RLIMIT_NOFILE", 1024, False)):
                del calls[:]
                got = helper._strict_rlimits(((name, value),))
                if want_ok and (got is not None or len(calls) != 1):
                    problems.append("%s %d (control): %r, %d setrlimit call(s)"
                                    % (name, value, got, len(calls)))
                if not want_ok:
                    if not (isinstance(got, str) and "soft" in got):
                        problems.append("%s %d above soft %d: %r, want a "
                                        "refusal naming the soft limit"
                                        % (name, value, limits[name][0], got))
                    if calls:
                        problems.append("%s %d: setrlimit %r was called"
                                        % (name, value, calls))
        finally:
            resource.getrlimit, resource.setrlimit = saved
        return problems, []
    _guard(suite, GRP_X, "X6 strict-rlimit-never-raises-soft", x6)

    def x7():
        # M1: the encoded line is never sliced; the reason is halved to fit
        problems = []
        base = {"sbx": 1, "event": "exit", "outcome": "refused", "code": 3,
                "signal": None, "durationMs": 1}
        for label, reason in (("short", "setrlimit RLIMIT_AS refused (x)"),
                              ("ascii x4000", "r" * 4000),
                              ("control chars", "\x01\"\\" * 300),
                              ("non-ascii", "é " * 400)):
            data = _status_bytes(helper, dict(base, reason=reason))
            if len(data) > 512:
                problems.append("%s: %d bytes > 512" % (label, len(data)))
            if not data.endswith(b"}\n"):
                problems.append("%s: the record does not end in '}\\n': %r"
                                % (label, data[-12:]))
                continue
            try:
                got = json.loads(data.decode("ascii"))
            except ValueError as exc:
                problems.append("%s: not one JSON record (%s)" % (label, exc))
                continue
            if got.get("outcome") != "refused" or \
                    not reason.startswith(got.get("reason", "\0")):
                problems.append("%s: record %r" % (label, got))
            if label == "short" and got.get("reason") != reason:
                problems.append("a reason that fits was cut")
        return problems, []
    _guard(suite, GRP_X, "X7 status-record-never-sliced", x7)

    def x8():
        # G3 mirror: only a bare "~" or a leading "~/" is a tilde both bash
        # and expanduser read alike
        problems = []
        for arg in ("~", "~/x"):
            code, _got, err = _call(helper.canonicalize, arg, fx["w"])
            if code is not None:
                problems.append("control %r: exit %r (%s)"
                                % (arg, code, _first(err)))
        for arg in ("~+/..", "~-", "~root/x", "./~+/..", "~/a~b", "x~"):
            code, got, _err = _call(helper.canonicalize, arg, fx["w"])
            if code != 2:
                problems.append("%r: exit %r (resolved %r), want 2"
                                % (arg, code, got))
        return problems, []
    _guard(suite, GRP_X, "X8 write-scope-tilde-forms-refused", x8)

    def x9():
        # P4: the help must not promise a secrecy the argv does not keep
        text = ""
        for action in helper._build_parser()._actions:
            if "--env" in action.option_strings:
                text = action.help or ""
        problems = []
        if "process list" not in text:
            problems.append("--env help does not say values are visible in "
                            "the process list: %r" % text)
        if "values are never printed" in text:
            problems.append("--env help still says values are never printed")
        return problems, []
    _guard(suite, GRP_X, "X9 env-help-declares-argv-exposure", x9)

    def x10():
        # P2 residual: a read that is an ANCESTOR of a socket directory
        # (`--read /`, `--read /var`) must not bury the socket tmpfs -- every
        # socket-dir --tmpfs comes after the last read bind (restricted is
        # never wider than legacy). Pure builder, no bwrap needed.
        problems = []
        for read in ("/", "/var"):
            argv = _bw(helper, BW_SCOPE_SPLIT,
                       read_rules=((read, "dir", None),))
            label = "--read %s" % read
            if argv[1:4] == ["--ro-bind", "/", "/"]:
                problems.append("%s: restricted branch not taken (legacy "
                                "root built)" % label)
            binds = [i for i in range(len(argv) - 2)
                     if argv[i] == "--ro-bind" and argv[i + 1] == read
                     and argv[i + 2] == read]
            if not binds:
                problems.append("%s: no --ro-bind %s %s emitted"
                                % (label, read, read))
                continue
            last = binds[-1]
            for d in helper.SOCKET_DIRS:
                tmpfs = [i for i in range(len(argv) - 1)
                         if argv[i] == "--tmpfs" and argv[i + 1] == d]
                if not tmpfs:
                    problems.append("%s: no --tmpfs %s" % (label, d))
                elif max(tmpfs) < last:
                    problems.append("%s: --tmpfs %s (argv[%d]) is buried by "
                                    "the read bind at argv[%d]"
                                    % (label, d, max(tmpfs), last))
        # control: the legacy argv is byte-identical
        problems += ["legacy control: " + p for p in _first_diff(
            helper._bwrap_argv(helper.Scope(**LEGACY_SCOPE)), LEGACY_BWRAP,
            "argv item")]
        return problems, []
    _guard(suite, GRP_X, "X10 ancestor-read-cannot-bury-socket-tmpfs", x10)

    def x11():
        # H1 (round 3, both modes): a write scope EQUAL TO or an ANCESTOR of a
        # socket directory is bound `--bind w w` after the socket tmpfs and
        # buries it -- host sockets writable and connectable. cwd falls back
        # to the project root when there is no .git, so cwd=/ makes
        # `--write .` the whole filesystem. Pure canonicalize and dry runs
        # only: no sandbox is ever built over /.
        problems = []
        for arg in (fx["w"], "sub", ".claude/tmp/x"):
            code, _got, err = _call(helper.canonicalize, arg, fx["w"])
            if code is not None:
                problems.append("control %r: exit %r (%s)"
                                % (arg, code, _first(err)))
        refused = [("/", fx["w"]), ("/var", fx["w"]), ("/run", fx["w"]),
                   ("/tmp", fx["w"]), ("/var/run", fx["w"]),
                   ("/var/run/..", fx["w"]), ("/var/snap", fx["w"]),
                   (".", "/"), ("..", "/var"), (".", "/var"),
                   ("/" + "/" + "var/./", fx["w"])]
        for arg, cwd in refused:
            code, got, err = _call(helper.canonicalize, arg, cwd)
            if code != 2:
                problems.append("--write %r from %s: exit %r (resolved %r), "
                                "want 2" % (arg, cwd, code, got))
            elif "socket" not in err:
                problems.append("--write %r from %s: stderr does not name the "
                                "socket directory: %s" % (arg, cwd, _first(err)))
        # the CLI, both modes, cwd=/ and `--write .` -- a dry run only
        for head in ([], ["--restrict-reads"]):
            rc, out, err, execs = _cli(
                helper, "/", head + ["--write", ".", "--dry-run", "--",
                                     ARGV0, "x"])
            label = "cwd=/ %s--write ." % (" ".join(head) + " " if head else "")
            if rc != 2:
                problems.append("%s: rc %r, want 2 (%s)"
                                % (label, rc, _first(err)))
            if "sbx plan" in out or execs:
                problems.append("%s: printed a plan or exec'd" % label)
        # the cwd-derived scratch default: <cwd>/.claude/tmp planted as a
        # symlink to / is refused (exit 3, the cwd floor's code)
        for name, target in (("scr-root", "/"), ("scr-var", "/var")):
            scr = os.path.join(fx["root"], name)
            os.makedirs(os.path.join(scr, ".claude"), exist_ok=True)
            link = os.path.join(scr, ".claude", "tmp")
            if not os.path.lexists(link):
                os.symlink(target, link)
            rc, out, err, execs = _cli(helper, scr,
                                       ["--dry-run", "--", ARGV0, "x"])
            if rc != 3:
                problems.append("scratch .claude/tmp -> %s: rc %r, want 3 (%s)"
                                % (target, rc, _first(err)))
            if "sbx plan" in out or execs:
                problems.append("scratch -> %s: printed a plan or exec'd"
                                % target)
        # control: the default scratch from a clean cwd still plans
        rc, out, err, _execs = _cli(helper, fx["w"],
                                    ["--dry-run", "--", ARGV0, "x"])
        if rc != 0 or "sbx plan" not in out:
            problems.append("control default scratch: rc %r (%s)"
                            % (rc, _first(err)))
        return problems, []
    _guard(suite, GRP_X, "X11 write-scope-burying-socket-dir-refused", x11)

    def x12():
        # H2 (round 3, restricted bwrap): `--dev /dev` before the reads let
        # `--read /` (or `--read /dev`) ro-bind the host /dev over the fresh
        # devtmpfs -- host /dev/shm and /dev/mqueue readable. The fresh /dev
        # now comes after the last read bind; legacy is byte-identical.
        problems = []
        for read in ("/", "/dev"):
            argv = _bw(helper, BW_SCOPE_SPLIT,
                       read_rules=((read, "dir", None),))
            label = "--read %s" % read
            if argv[1:4] == ["--ro-bind", "/", "/"]:
                problems.append("%s: restricted branch not taken (legacy "
                                "root built)" % label)
            binds = [i for i in range(len(argv) - 2)
                     if argv[i] == "--ro-bind" and argv[i + 1] == read
                     and argv[i + 2] == read]
            devs = [i for i in range(len(argv) - 1)
                    if argv[i] == "--dev" and argv[i + 1] == "/dev"]
            if not binds:
                problems.append("%s: no --ro-bind %s %s" % (label, read, read))
            elif len(devs) != 1:
                problems.append("%s: %d --dev /dev, want 1" % (label, len(devs)))
            elif devs[0] < binds[-1]:
                problems.append("%s: --dev /dev (argv[%d]) is buried by the "
                                "read bind at argv[%d]"
                                % (label, devs[0], binds[-1]))
            elif argv[devs[0] + 2:devs[0] + 4] != ["--remount-ro", "/dev"]:
                problems.append("%s: --dev /dev not followed by --remount-ro "
                                "/dev" % label)
        problems += ["legacy control: " + p for p in _first_diff(
            helper._bwrap_argv(helper.Scope(**LEGACY_SCOPE)), LEGACY_BWRAP,
            "argv item")]
        return problems, []
    _guard(suite, GRP_X, "X12 read-cannot-bury-fresh-dev", x12)

    def x13():
        # R1 (round-3 LOW, legacy bwrap): a ~/.claude carve-out (expanduser'd,
        # bwrap follows the source link) or a shadow re-bind target (the
        # realpath of a ~/.claude child linking out) is ro-bound AFTER the
        # socket tmpfs; one that IS, is an ancestor of, or lies under
        # /run, /var/run or /var/snap re-exposes the host sockets. It is
        # refused, exit 3, never silently dropped (that would lose the deny).
        # A fake HOME under the fixture root; dry runs only.
        problems = []
        cases = [("shadow-root", "evil", "/", 3),
                 ("shadow-tmp", "evil", "/tmp", 3),
                 ("shadow-varrun", "evil", "/var/run", 3),
                 ("carve-tmp", "skills", "/tmp", 3),
                 ("control", "ok", fx["outside"], 0)]
        saved = os.environ.get("HOME")
        try:
            for name, child, target, want in cases:
                home = os.path.join(fx["root"], "home-" + name)
                os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
                link = os.path.join(home, ".claude", child)
                if not os.path.lexists(link):
                    os.symlink(target, link)
                os.environ["HOME"] = home
                for head in ([], ["--restrict-reads"]):
                    label = "%s%s ~/.claude/%s -> %s" % (
                        name, " restricted" if head else "", child, target)
                    rc, out, err, execs = _cli(
                        helper, fx["w"], head + ["--write", fx["w"],
                                                 "--dry-run", "--", ARGV0,
                                                 "x"])
                    if rc != want:
                        problems.append("%s: rc %r, want %d (%s)"
                                        % (label, rc, want, _first(err)))
                    if want and ("sbx plan" in out or execs):
                        problems.append("%s: printed a plan or exec'd" % label)
                    if want and "socket" not in err:
                        problems.append("%s: stderr does not name the socket "
                                        "directory: %s" % (label, _first(err)))
        finally:
            if saved is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved
        return problems, []
    _guard(suite, GRP_X, "X13 carveout-or-shadow-socket-dir-refused", x13)

    def x14():
        # R2 (round-3 LOW): a --write scope UNDER /run, /var/run or /var/snap
        # (/run/user/<uid>: D-Bus, ssh-agent, gpg-agent) was accepted while a
        # cwd there is refused. Now exit 2; under /tmp stays accepted (a cwd
        # under /tmp is supported through the read-only re-bind, declared).
        problems = []
        for arg in ("/run/user/1000", "/run/user/1000/x", "/var/run/x",
                    "/var/snap/lxd", "/var/snap/x/y"):
            code, got, err = _call(helper.canonicalize, arg, fx["w"])
            if code != 2:
                problems.append("--write %r: exit %r (resolved %r), want 2"
                                % (arg, code, got))
            elif "socket" not in err:
                problems.append("--write %r: stderr does not name the socket "
                                "directory: %s" % (arg, _first(err)))
        for arg in ("/tmp/sbx-x14", "/tmp/sbx-x14/y", fx["w"]):
            code, _got, err = _call(helper.canonicalize, arg, fx["w"])
            if code is not None:
                problems.append("control --write %r: exit %r (%s)"
                                % (arg, code, _first(err)))
        # restricted mode also needs the cwd inside a write scope
        for head in ([], ["--restrict-reads", "--write", fx["w"]]):
            rc, out, err, execs = _cli(
                helper, fx["w"], head + ["--write", "/run/user/1000",
                                         "--dry-run", "--", ARGV0, "x"])
            label = "%s--write /run/user/1000" % ("restricted " if head else "")
            if rc != 2:
                problems.append("%s: rc %r, want 2 (%s)"
                                % (label, rc, _first(err)))
            if "sbx plan" in out or execs:
                problems.append("%s: printed a plan or exec'd" % label)
            rc, out, err, _execs = _cli(
                helper, fx["w"], head + ["--write", "/tmp/sbx-x14",
                                         "--dry-run", "--", ARGV0, "x"])
            if rc != 0 or "sbx plan" not in out:
                problems.append("control %s--write /tmp/sbx-x14: rc %r (%s)"
                                % ("restricted " if head else "", rc,
                                   _first(err)))
        # the cwd-derived scratch default under /run is refused like a cwd
        # there (exit 3, the scratch refusal's code), --dry-run included
        rc, out, err, execs = _cli(helper, "/run/user/1000",
                                   ["--dry-run", "--", ARGV0, "x"])
        if rc != 3 or "sbx plan" in out or execs:
            problems.append("cwd=/run/user/1000 default scratch: rc %r, want 3 "
                            "(%s)" % (rc, _first(err)))
        return problems, []
    _guard(suite, GRP_X, "X14 write-scope-under-socket-dir-refused", x14)

    def x15():
        # Linux RLIMIT_NPROC counts TASKS (threads) of the real uid, so the
        # `--rlimit-nproc +N` base must too: a uid with 127 processes but
        # 1054 threads got limit 191 and bwrap's clone failed EAGAIN (para,
        # Ubuntu 22.04). A fake proc tree pins the count; runs on both OSes.
        root = tempfile.mkdtemp(prefix="proc-", dir=fx["root"])
        want = 4242
        rows = {"100": "Uid:\t4242\t4242\t4242\t4242\nThreads:\t7\n",
                "101": "Uid:\t1\t1\t1\t1\nThreads:\t50\n",
                "102": "Uid:\t4242\t4242\t4242\t4242\nThreads:\t1\n",
                "104": "Uid:\t4242\t4242\t4242\t4242\n",  # no Threads: -> 1
                "105": "Uid:\t1\t4242\t4242\t4242\nThreads:\t3\n",
                "self": "Uid:\t4242\t4242\t4242\t4242\nThreads:\t99\n"}
        for name, text in rows.items():
            os.makedirs(os.path.join(root, name))
            with open(os.path.join(root, name, "status"), "w") as fh:
                fh.write(text)
        os.makedirs(os.path.join(root, "103"))  # exited mid-scan: no status
        got = helper.uid_task_count(want, root)
        if got != 9:
            return ["uid_task_count over the fake tree = %r, want 9 (7 + 1 "
                    "+ 1 tasks of real uid %d; effective-only uid, a "
                    "non-pid entry and a vanished pid ignored)"
                    % (got, want)], []
        return [], []
    _guard(suite, GRP_X, "X15 nproc-plus-base-counts-tasks", x15)


# ---------------------------------------------------------------------------
# N: egress flags (--egress-port on Seatbelt, --egress-socket on bwrap)
# ---------------------------------------------------------------------------

# The fixed in-sandbox endpoint of the Linux path (sbx's EGRESS_SOCKET_DEST
# and EGRESS_FORWARD_PORT), typed here, never read from the helper.
N_SOCKET_DEST = "/.sbx-egress.sock"
N_FORWARD_PORT = 3128
# The forwarder's connection cap: the runner proxy's _EGRESS_MAX_CONNECTIONS
# (Scripts/mcp-sandbox.py), typed here, never read from either file.
N_FORWARD_SLOTS = 32
N_PORT = 48123
N_PY = "/usr/bin/python3"
N_SB_NET = ["(deny network*)",
            '(allow network-outbound (remote tcp "localhost:%d"))' % N_PORT]
N_EGRESS_DEFAULTS = dict(egress_port=None, egress_socket=None,
                         egress_python=None)


def _n_private_group(gid, euid):
    """Is `gid` a user-private group of `euid` (no other gr_mem name, no
    other passwd entry's primary group)? An unknown gid or euid -> False.
    Typed here, independent of the helper's rule."""
    try:
        me = pwd.getpwuid(euid).pw_name
        members = grp.getgrgid(gid).gr_mem
    except KeyError:
        return False
    if [m for m in members if m != me]:
        return False
    return not [p for p in pwd.getpwall()
                if p.pw_gid == gid and p.pw_uid != euid]


def _n_exposed_ancestor(d):
    """The first of `d` and its ancestors another uid may write (an other
    write bit, or a group write bit whose group is not the euid's private
    one -- neither on a root-owned sticky dir; or a foreign owner) -> its
    path, or None. Typed here, independent of the helper's walk."""
    euid = os.geteuid()
    while True:
        st = os.lstat(d)
        sticky_root = st.st_uid == 0 and st.st_mode & stat.S_ISVTX
        if st.st_uid not in (0, euid) or (
                not sticky_root
                and (st.st_mode & 0o002
                     or (st.st_mode & 0o020
                         and not _n_private_group(st.st_gid, euid)))):
            return d
        if d == "/":
            return None
        d = os.path.dirname(d)


def _n_host_flag(fx):
    """The egress flag this host's backend takes, as a dry-run would pass
    it: --egress-port on darwin, --egress-socket <fixture socket> on linux."""
    if sys.platform == "darwin":
        return ["--egress-port", str(N_PORT)]
    return ["--egress-socket", _n_socket(fx)[0]]


def _n_socket(fx):
    """(path, listener) of a unix socket in a 0700 fixture dir, bound once
    per run through a relative name (no sun_path limit)."""
    if "n_sock" in fx:
        return fx["n_sock"]
    sdir = os.path.join(fx["root"], "egress")
    os.makedirs(sdir, exist_ok=True)
    os.chmod(sdir, 0o700)
    path = os.path.join(sdir, "s")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    here = os.getcwd()
    try:
        os.chdir(sdir)
        sock.bind("s")
    finally:
        os.chdir(here)
    sock.listen(8)
    fx["n_sock"] = (path, sock)
    return fx["n_sock"]


def _n_echo_tcp():
    """A host TCP listener on 127.0.0.1:0 answering `ECHO:<data>`; (port,
    socket). The thread is a daemon and ends when the socket is closed."""
    import threading
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.bind(("127.0.0.1", 0))
    ls.listen(8)

    def loop():
        while True:
            try:
                conn, _addr = ls.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                conn.sendall(b"ECHO:" + conn.recv(64))
            except OSError:
                pass
            conn.close()
    threading.Thread(target=loop, daemon=True).start()
    return ls.getsockname()[1], ls


def _n_echo_unix(path):
    """A host unix listener at `path` (bound relatively) answering
    `ECHO:<data>`, for the forwarder rows; returns the socket."""
    import threading
    if os.path.lexists(path):
        os.unlink(path)
    ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    here = os.getcwd()
    try:
        os.chdir(os.path.dirname(path))
        ls.bind(os.path.basename(path))
    finally:
        os.chdir(here)
    ls.listen(8)

    def loop():
        while True:
            try:
                conn, _addr = ls.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                conn.sendall(b"ECHO:" + conn.recv(64))
            except OSError:
                pass
            conn.close()
    threading.Thread(target=loop, daemon=True).start()
    return ls


# One probe for the live egress rows: every outcome as one JSON line.
N_PROBE = r'''
import json, os, socket, sys
allowed, other = int(sys.argv[1]), int(sys.argv[2])
out = {}
def tcp(host, port, fam=socket.AF_INET):
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((host, port))
        s.sendall(b"tf")
        return s.recv(64).decode("ascii", "replace")
    except OSError as e:
        return "EPERM" if e.errno == 1 else (os.strerror(e.errno) if e.errno
                                             else type(e).__name__)
    finally:
        s.close()
out["allowed"] = tcp("127.0.0.1", allowed)
out["other"] = tcp("127.0.0.1", other)
out["outside"] = tcp("1.1.1.1", 443)
try:
    socket.getaddrinfo("example.com", 443)
    out["dns"] = "resolved"
except OSError as e:
    out["dns"] = "failed"
u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    u.connect("/var/run/mDNSResponder")
    out["mdns"] = "connected"
except OSError as e:
    out["mdns"] = "EPERM" if e.errno == 1 else "failed"
finally:
    u.close()
if len(sys.argv) > 3:
    try:
        os.unlink(sys.argv[3])
        out["unlink_dest"] = "removed"
    except OSError as e:
        out["unlink_dest"] = "refused"
print(json.dumps(out, sort_keys=True))
'''


def _run_n(suite, helper, fx, skips):
    host_flag = _n_host_flag(fx)

    def n1():
        problems = _accepted(helper, fx, host_flag, "restricted + egress")
        problems += _refused(helper, fx, host_flag,
                             "egress without --restrict-reads",
                             needle="--restrict-reads", restrict=False)
        return problems, ["flag        : %s" % " ".join(host_flag)]
    _guard(suite, GRP_N, "N1 egress-requires-restrict-reads", n1)

    def n2():
        problems = _accepted(helper, fx, host_flag, "restricted + egress")
        problems += _refused(helper, fx, ["--net"] + host_flag,
                             "egress with --net", needle="--net")
        problems += _refused(helper, fx,
                             ["--egress-port", str(N_PORT), "--egress-socket",
                              _n_socket(fx)[0]],
                             "both egress flags", needle="--egress-")
        return problems, []
    _guard(suite, GRP_N, "N2 egress-net-and-both-flags-refused", n2)

    def n3():
        fn = getattr(helper, "parse_egress_port", None)
        if fn is None:
            return ["helper has no parse_egress_port"], []
        problems = []
        for raw, want in (("1", 1), ("3128", 3128), ("65535", 65535)):
            code, got, err = _call(fn, raw)
            if code is not None or got != want:
                problems.append("control %r: exit %r / %r (%s)"
                                % (raw, code, got, _first(err)))
        for raw in ("0", "65536", "+1", "-1", "08x", "", "1e3", "3128 ",
                    chr(0x0663), "000000000000000000001"):
            code, got, _err = _call(fn, raw)
            if code != 2:
                problems.append("%r: exit %r (got %r), want 2"
                                % (raw, code, got))
        return problems, []
    _guard(suite, GRP_N, "N3 egress-port-grammar", n3)

    def n4():
        fn = getattr(helper, "validate_egress", None)
        if fn is None:
            return ["helper has no validate_egress"], []
        sock = _n_socket(fx)[0]
        port_args = _parse(helper, ["--restrict-reads", "--egress-port",
                                    str(N_PORT)])
        sock_args = _parse(helper, ["--restrict-reads", "--egress-socket",
                                    sock])
        problems = []
        for label, args, key, want_code, want in (
                ("port on darwin", port_args, "darwin", None, (N_PORT, None)),
                ("socket on linux", sock_args, "linux", None, (None, sock)),
                ("port on linux", port_args, "linux", 3, None),
                ("socket on darwin", sock_args, "darwin", 3, None),
                ("port elsewhere", port_args, "win32", 3, None)):
            code, got, err = _call(fn, args, key)
            if code != want_code:
                problems.append("%s: exit %r, want %r (%s)"
                                % (label, code, want_code, _first(err)))
            elif want_code is None and tuple(got or ()) != want:
                problems.append("%s: %r, want %r" % (label, got, want))
            elif want_code and "sbx: error" not in err:
                problems.append("%s: no 'sbx: error' line" % label)
        return problems, []
    _guard(suite, GRP_N, "N4 egress-flag-is-per-platform", n4)

    def n5():
        fn = getattr(helper, "egress_socket_refusal", None)
        if fn is None:
            return ["helper has no egress_socket_refusal"], []
        sock, _l = _n_socket(fx)
        problems = []
        notes = []
        why = fn(sock)
        # The control's verdict depends on the checkout's own ancestors
        # (N12, N14): under an other-writable one, or a group-writable one
        # whose group is shared, the correct answer is the ancestor refusal,
        # never acceptance -- a 0775 clone under a user-private group (UPG,
        # umask 0002) is not exposed.
        exposed = _n_exposed_ancestor(os.path.dirname(os.path.dirname(sock)))
        if exposed:
            notes.append("control     : %s is foreign-writable -> refusal "
                         "expected" % exposed)
            if not isinstance(why, str) or "ancestor" not in why:
                problems.append("control under a foreign-writable ancestor "
                                "%s: %r, want the ancestor refusal"
                                % (exposed, why))
        elif why is not None:
            problems.append("control (own socket, 0700 dir): refused %r"
                            % (why,))
        # the refusals: not a socket, a symlink hop, a group/other-readable
        # parent, under a socket directory, missing
        plain = os.path.join(fx["root"], "egress", "plain")
        with open(plain, "w") as fh:
            fh.write("x")
        link = os.path.join(fx["root"], "egress-link")
        if not os.path.lexists(link):
            os.symlink(sock, link)
        open_dir = os.path.join(fx["root"], "egress-open")
        os.makedirs(open_dir, exist_ok=True)
        os.chmod(open_dir, 0o755)
        open_sock = os.path.join(open_dir, "s")
        if not os.path.lexists(open_sock):
            s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            here = os.getcwd()
            try:
                os.chdir(open_dir)
                s2.bind("s")
            finally:
                os.chdir(here)
                s2.close()
        cases = (("a regular file", plain), ("a symlink to the socket", link),
                 ("a socket in a 0755 dir", open_sock),
                 ("under /tmp", "/tmp/sbx-egress-n5/s"),
                 ("under /run", "/run/user/1000/bus"),
                 ("under /var/run", "/var/run/docker.sock"),
                 ("missing", os.path.join(fx["root"], "egress", "nope")))
        for label, path in cases:
            why = fn(path)
            if not isinstance(why, str) or not why:
                problems.append("%s (%s): %r, want a refusal reason"
                                % (label, path, why))
        return problems, ["socket      : %s" % sock] + notes
    _guard(suite, GRP_N, "N5 egress-socket-must-be-own-socket", n5)

    def n6():
        problems = []
        base = _sb_lines(helper, env=SB_ENV_PATH)
        problems += _first_diff(base, SB_VARIANTS[0][2])
        try:
            got = _sb_lines(helper, env=SB_ENV_PATH, egress_port=N_PORT)
        except Exception as exc:          # noqa: BLE001 -- the red reason
            return problems + ["egress_port scope raised %r" % (exc,)], []
        want = SB_VARIANTS[0][2][:-1] + N_SB_NET
        problems += _sb_restricted(got, "egress") + _first_diff(got, want)
        for label, kw in (
                ("legacy scope", dict(LEGACY_SCOPE, egress_port=N_PORT)),
                ("port 0", dict(SB_SCOPE, egress_port=0)),
                ("port 65536", dict(SB_SCOPE, egress_port=65536)),
                ("bool port", dict(SB_SCOPE, egress_port=True)),
                ("str port", dict(SB_SCOPE, egress_port="3128"))):
            why = _raises_value_error(helper._seatbelt_argv,
                                      helper.Scope(**kw))
            if why:
                problems.append("%s: %s, want ValueError" % (label, why))
        return problems, ["network     : %s" % " | ".join(got[-2:])]
    _guard(suite, GRP_N, "N6 seatbelt-egress-golden", n6)

    def n7():
        problems = _first_diff(_bw(helper, BW_SCOPE_SPLIT), BW_GOLDEN_SPLIT,
                               "argv item")
        fwd = getattr(helper, "EGRESS_FORWARDER", None)
        if not isinstance(fwd, str) or not fwd:
            return problems + ["helper has no EGRESS_FORWARDER source"], []
        try:
            compile(fwd, "<egress forwarder>", "exec")
        except SyntaxError as exc:
            problems.append("the forwarder does not compile: %s" % exc)
        sock = "/s/e/s"
        got = _bw(helper, BW_SCOPE_SPLIT, egress_socket=sock,
                  egress_python=N_PY)
        sep = BW_GOLDEN_SPLIT.index("--bind")
        cut = BW_GOLDEN_SPLIT.index("--")
        want = (BW_GOLDEN_SPLIT[:sep] + ["--ro-bind", sock, N_SOCKET_DEST]
                + BW_GOLDEN_SPLIT[sep:cut + 1]
                + [N_PY, "-I", "-S", "-B", "-c", fwd, str(N_FORWARD_PORT),
                   N_SOCKET_DEST, "--"] + BW_GOLDEN_SPLIT[cut + 1:])
        problems += _bw_restricted(got, "egress") + _first_diff(
            got, want, "argv item")
        if "--unshare-net" not in got:
            problems.append("egress dropped --unshare-net")
        for label, kw in (
                ("no forwarder python", dict(BW_SCOPE_SPLIT,
                                             egress_socket=sock)),
                ("legacy scope", dict(LEGACY_SCOPE, egress_socket=sock,
                                      egress_python=N_PY))):
            why = _raises_value_error(helper._bwrap_argv, helper.Scope(**kw))
            if why:
                problems.append("%s: %s, want ValueError" % (label, why))
        return problems, ["argv        : %d items" % len(got)]
    _guard(suite, GRP_N, "N7 bwrap-egress-golden", n7)

    def n8():
        rc, out, err, execs = _cli(helper, fx["w"], [
            "--restrict-reads", "--write", fx["w"], "--dry-run"] + host_flag
            + ["--", ARGV0, "x"])
        problems = [] if rc == 0 else ["rc %r (%s)" % (rc, _first(err))]
        if execs:
            problems.append("exec'd on a dry run")
        field = "egress_port" if sys.platform == "darwin" else "egress_socket"
        if field not in out:
            problems.append("the plan does not print %s" % field)
        legacy = helper.Scope(**dict(LEGACY_SCOPE, **N_EGRESS_DEFAULTS))
        if helper._seatbelt_argv(legacy) != LEGACY_SEATBELT:
            problems.append("legacy seatbelt differs with egress defaults")
        if helper._bwrap_argv(legacy) != LEGACY_BWRAP:
            problems.append("legacy bwrap differs with egress defaults")
        if not helper._restricted_flags_given(_parse(helper, host_flag)):
            problems.append("an egress flag alone is not routed to the "
                            "restricted path")
        return problems, []
    _guard(suite, GRP_N, "N8 dry-run-prints-egress-legacy-unchanged", n8)

    def n9():
        # The forwarder, host side (no bwrap): listen on a free port, fork,
        # exec the target; the target talks to a unix echo server through
        # it; the target's exit code is the run's; the forwarder child is
        # gone once the target has exited (its parent-watch, which the pid
        # namespace makes redundant inside bwrap).
        fwd = getattr(helper, "EGRESS_FORWARDER", None)
        if not isinstance(fwd, str) or not fwd:
            return ["helper has no EGRESS_FORWARDER source"], []
        path = os.path.join(fx["root"], "egress", "fwd")
        srv = _n_echo_unix(path)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        client = ("import socket, sys, time\n"
                  "for _ in range(50):\n"
                  "    s = socket.socket(); s.settimeout(3)\n"
                  "    try:\n"
                  "        s.connect(('127.0.0.1', %d)); break\n"
                  "    except OSError:\n"
                  "        time.sleep(0.05)\n"
                  "s.sendall(b'tf-n9'); print(s.recv(64).decode())\n"
                  "sys.exit(5)\n" % port)
        problems = []
        try:
            p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", fwd,
                                str(port), path, "--", sys.executable, "-I",
                                "-S", "-c", client],
                               capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            if p.returncode != 5:
                problems.append("rc %r, want the target's 5 (%s)"
                                % (p.returncode, p.stderr.strip()[:200]))
            if p.stdout.strip() != "ECHO:tf-n9":
                problems.append("stdout %r, want ECHO:tf-n9"
                                % (p.stdout.strip()[:80],))
            deadline = time.monotonic() + 5
            gone = False
            while time.monotonic() < deadline:
                s = socket.socket()
                s.settimeout(1)
                try:
                    s.connect(("127.0.0.1", port))
                except OSError:
                    gone = True
                    break
                finally:
                    s.close()
                time.sleep(0.1)
            if not gone:
                problems.append("the forwarder still listens 5 s after the "
                                "target exited")
        finally:
            srv.close()
        return problems, ["port        : %d" % port]
    _guard(suite, GRP_N, "N9 forwarder-relays-and-dies-with-target", n9)

    def n10():
        # live Seatbelt: --egress-port P reaches ONLY 127.0.0.1:P
        port, ls = _n_echo_tcp()
        other, ls2 = _n_echo_tcp()
        try:
            argv0, flags = _l_plan("python3")
            wd = os.path.join(fx["root"], "N-seatbelt")
            os.makedirs(wd, exist_ok=True)
            script = os.path.join(wd, "probe.py")
            with open(script, "w") as fh:
                fh.write(N_PROBE)
            res = _live({"w": wd}, flags + ["--egress-port", str(port)],
                        [argv0, "-I", "-S", script, str(port), str(other)],
                        status=False)
            ctl = _live({"w": wd}, flags,
                        [argv0, "-I", "-S", script, str(port), str(other)],
                        status=False)
        finally:
            ls.close()
            ls2.close()
        got, base = _json_out(res), _json_out(ctl)
        problems = [] if res["rc"] == 0 else ["rc %r: %s" % (
            res["rc"], res["err"].strip()[:200])]
        if not isinstance(got, dict) or not isinstance(base, dict):
            return problems + ["probe printed no JSON: %r / %r"
                               % (res["out"][:120], ctl["out"][:120])], []
        problems += ["control (no egress): " + p for p in _l_compare(
            base, (("allowed", "EPERM"),))]
        problems += _l_compare(got, (("allowed", "ECHO:tf"),
                                     ("other", "EPERM"), ("outside", "EPERM"),
                                     ("dns", "failed"), ("mdns", "EPERM")))
        return problems, ["egress      : %r" % (got,),
                          "control     : %r" % (base,)]
    _live_row(suite, GRP_N, "N10 seatbelt-egress-port-only (live)",
              skips["darwin"], n10)

    def n11():
        # live bwrap: --egress-socket S reaches S through the forwarder at
        # 127.0.0.1:3128 inside the private netns, and nothing else
        path = os.path.join(fx["root"], "egress", "live")
        srv = _n_echo_unix(path)
        try:
            wd = os.path.join(fx["root"], "N-bwrap")
            os.makedirs(wd, exist_ok=True)
            script = os.path.join(wd, "probe.py")
            with open(script, "w") as fh:
                fh.write(N_PROBE)
            py = os.path.realpath(sys.executable)
            # the cwd is the write scope _live adds; the echo socket stays
            # outside every grant (reached only through --egress-socket)
            flags = ["--restrict-reads", "--timeout", "30", "--clean-env",
                     "--env", "PATH=/usr/bin:/bin"]
            res = _live({"w": wd}, flags + ["--egress-socket", path],
                        [py, "-I", "-S", script, str(N_FORWARD_PORT), "9",
                         N_SOCKET_DEST], status=False)
            ctl = _live({"w": wd}, flags,
                        [py, "-I", "-S", script, str(N_FORWARD_PORT), "9"],
                        status=False)
        finally:
            srv.close()
        got, base = _json_out(res), _json_out(ctl)
        problems = [] if res["rc"] == 0 else ["rc %r: %s" % (
            res["rc"], res["err"].strip()[:200])]
        if not isinstance(got, dict) or not isinstance(base, dict):
            return problems + ["probe printed no JSON: %r / %r"
                               % (res["out"][:120], res["err"][:200])], []
        if base.get("allowed") == "ECHO:tf":
            problems.append("control (no egress) reached the forwarder port")
        problems += _l_compare(got, (("allowed", "ECHO:tf"),
                                     ("unlink_dest", "refused"),
                                     ("dns", "failed")))
        for key in ("other", "outside"):
            if got.get(key) == "ECHO:tf" or got.get(key) is None:
                problems.append("%s %r: reached" % (key, got.get(key)))
        return problems, ["egress      : %r" % (got,),
                          "control     : %r" % (base,)]
    _live_row(suite, GRP_N, "N11 bwrap-egress-socket-forwarder (live)",
              skips["linux"], n11)

    def n12():
        # Security review F8 (CWE-367): bwrap re-resolves the bind source at
        # mount time, so every ancestor above the 0700 directory must be one
        # no other uid can write -- here a 0777 grandparent, and a sticky
        # one the caller (not root) owns, both refused.
        fn = getattr(helper, "egress_socket_refusal", None)
        if fn is None:
            return ["helper has no egress_socket_refusal"], []
        problems = []
        for label, mode in (("a 0777 grandparent", 0o777),
                            ("a user-owned 01777 grandparent", 0o1777)):
            gp = os.path.join(fx["root"], "egress-gp%o" % mode)
            par = os.path.join(gp, "p")
            os.makedirs(par, exist_ok=True)
            os.chmod(par, 0o700)
            os.chmod(gp, mode)
            sock = os.path.join(par, "s")
            if not os.path.lexists(sock):
                s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                here = os.getcwd()
                try:
                    os.chdir(par)
                    s2.bind("s")
                finally:
                    os.chdir(here)
                    s2.close()
            why = fn(sock)
            os.chmod(gp, 0o700)
            if not isinstance(why, str) or not why:
                problems.append("%s (%s): %r, want a refusal reason"
                                % (label, sock, why))
        # A root-owned grandparent cannot be made here without root, so it
        # is injected through os.lstat: every ancestor reads as the euid's,
        # write bits cleared, and the grandparent as root's -- sticky 01777
        # accepted (/tmp), plain 0777 refused.
        gp = os.path.join(fx["root"], "egress-gp777")
        sock = os.path.join(gp, "p", "s")
        real_lstat = os.lstat

        def fake_lstat(p, mode):
            def lstat(q, *a, **kw):
                st = real_lstat(q, *a, **kw)
                if q == gp:
                    return os.stat_result((stat.S_IFDIR | mode, st.st_ino,
                                           st.st_dev, st.st_nlink, 0,
                                           st.st_gid, st.st_size,
                                           st.st_atime, st.st_mtime,
                                           st.st_ctime))
                if q != "/" and not gp.startswith(q.rstrip("/") + "/"):
                    return st
                return os.stat_result((st.st_mode & ~0o022, st.st_ino,
                                       st.st_dev, st.st_nlink,
                                       os.geteuid() if st.st_uid else 0,
                                       st.st_gid, st.st_size, st.st_atime,
                                       st.st_mtime, st.st_ctime))
            return lstat
        for label, mode, want_ok in (("a root-owned 01777 grandparent",
                                      0o1777, True),
                                     ("a root-owned 0777 grandparent",
                                      0o777, False)):
            os.lstat = fake_lstat(gp, mode)
            try:
                why = fn(sock)
            finally:
                os.lstat = real_lstat
            if want_ok and why is not None:
                problems.append("%s: refused %r, want accepted" % (label, why))
            elif not want_ok and not why:
                problems.append("%s: %r, want a refusal reason" % (label, why))
        return problems, []
    _guard(suite, GRP_N, "N12 egress-socket-ancestors-not-foreign-writable",
           n12)

    def n14():
        # Measured on Ubuntu 22.04: umask 0002 with user-private groups
        # (UPG) makes every user dir 0775, so N12's rule refused every
        # egress run. A GROUP-writable ancestor is acceptable when its group
        # is the euid's private one: no other gr_mem name, no other passwd
        # entry with it as primary group, the ancestor owned by the euid or
        # root; an unknown gid fails closed, and o+w stays refused. The
        # grandparent's stat is injected through os.lstat (as N12), the
        # group and passwd databases through the helper's grp/pwd modules.
        fn = getattr(helper, "egress_socket_refusal", None)
        if fn is None:
            return ["helper has no egress_socket_refusal"], []
        euid = os.geteuid()
        me = "sbx-n14-me"
        gid = 0x7ffff00e
        gp = os.path.join(fx["root"], "egress-gp-upg")
        par = os.path.join(gp, "p")
        os.makedirs(par, exist_ok=True)
        os.chmod(par, 0o700)
        os.chmod(gp, 0o700)
        sock = os.path.join(par, "s")
        if not os.path.lexists(sock):
            s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            here = os.getcwd()
            try:
                os.chdir(par)
                s2.bind("s")
            finally:
                os.chdir(here)
                s2.close()
        real_lstat = os.lstat

        def fake_lstat(mode, uid):
            def lstat(q, *a, **kw):
                st = real_lstat(q, *a, **kw)
                if q == gp:
                    return os.stat_result((stat.S_IFDIR | mode, st.st_ino,
                                           st.st_dev, st.st_nlink, uid, gid,
                                           st.st_size, st.st_atime,
                                           st.st_mtime, st.st_ctime))
                if q != "/" and not gp.startswith(q.rstrip("/") + "/"):
                    return st
                return os.stat_result((st.st_mode & ~0o022, st.st_ino,
                                       st.st_dev, st.st_nlink,
                                       euid if st.st_uid else 0,
                                       st.st_gid, st.st_size, st.st_atime,
                                       st.st_mtime, st.st_ctime))
            return lstat

        class _Rec(object):
            def __init__(self, **kw):
                self.__dict__.update(kw)

        def fake_dbs(members, others_gid, known=True):
            def getgrgid(g):
                if g != gid or not known:
                    raise KeyError(g)
                return _Rec(gr_name=me, gr_gid=gid, gr_mem=list(members))

            def getpwuid(u):
                if u != euid:
                    raise KeyError(u)
                return _Rec(pw_name=me, pw_uid=euid, pw_gid=gid)

            def getpwall():
                return [_Rec(pw_name=me, pw_uid=euid, pw_gid=gid),
                        _Rec(pw_name="sbx-n14-other", pw_uid=euid + 1,
                             pw_gid=others_gid)]
            return (_Rec(getgrgid=getgrgid),
                    _Rec(getpwuid=getpwuid, getpwall=getpwall))

        cases = (
            ("a UPG 0775 grandparent (no gr_mem)", 0o775, euid, [], 100,
             True, True),
            ("a UPG 0775 grandparent (gr_mem = the user)", 0o775, euid, [me],
             100, True, True),
            ("a root-owned 0775 grandparent in the user's UPG", 0o775, 0, [],
             100, True, True),
            ("a 0775 grandparent whose group has another member", 0o775,
             euid, [me, "sbx-n14-other"], 100, True, False),
            ("a 0775 grandparent whose gid is another user's primary group",
             0o775, euid, [], gid, True, False),
            ("a 0775 grandparent with an unknown gid", 0o775, euid, [], 100,
             False, False),
            ("a world-writable 0777 grandparent in the user's UPG", 0o777,
             euid, [], 100, True, False),
            ("a foreign-owned 0775 grandparent in the user's UPG", 0o775,
             euid + 1, [], 100, True, False))
        problems = []
        saved = (getattr(helper, "grp", None), getattr(helper, "pwd", None))
        for label, mode, uid, members, others_gid, known, want_ok in cases:
            helper.grp, helper.pwd = fake_dbs(members, others_gid, known)
            os.lstat = fake_lstat(mode, uid)
            try:
                why = fn(sock)
            finally:
                os.lstat = real_lstat
                helper.grp, helper.pwd = saved
            if want_ok and why is not None:
                problems.append("%s: refused %r, want accepted" % (label, why))
            elif not want_ok and (not isinstance(why, str)
                                  or "ancestor" not in why):
                problems.append("%s: %r, want the ancestor refusal"
                                % (label, why))
        return problems, []
    _guard(suite, GRP_N, "N14 egress-socket-upg-group-writable-ancestor", n14)

    def n13():
        # Security review F14 (CWE-755): a thread the forwarder cannot start
        # (RLIMIT_NPROC) drops that one connection, never the relay -- the
        # first Thread.start raises, the second connection still echoes;
        # and the slot cap is the runner proxy's 32, typed here.
        fwd = getattr(helper, "EGRESS_FORWARDER", None)
        if not isinstance(fwd, str) or not fwd:
            return ["helper has no EGRESS_FORWARDER source"], []
        problems = []
        if "BoundedSemaphore(%d)" % N_FORWARD_SLOTS not in fwd:
            problems.append("the forwarder's slot cap is not %d"
                            % N_FORWARD_SLOTS)
        path = os.path.join(fx["root"], "egress", "fwe")
        srv = _n_echo_unix(path)
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        shim = ("import threading\n"
                "_start, _n = threading.Thread.start, [0]\n"
                "def start(self):\n"
                "    _n[0] += 1\n"
                "    if _n[0] == 1:\n"
                "        raise RuntimeError(\"can't start new thread\")\n"
                "    return _start(self)\n"
                "threading.Thread.start = start\n"
                "exec(compile(%r, '<fwd>', 'exec'), {'__name__': '__main__'})\n"
                % fwd)
        client = ("import socket, sys, time\n"
                  "for _ in range(50):\n"
                  "    s = socket.socket(); s.settimeout(3)\n"
                  "    try:\n"
                  "        s.connect(('127.0.0.1', %d)); break\n"
                  "    except OSError:\n"
                  "        time.sleep(0.05)\n"
                  "try:\n"
                  "    s.recv(64)\n"
                  "except OSError:\n"
                  "    pass\n"
                  "s.close(); time.sleep(0.2)\n"
                  "s = socket.socket(); s.settimeout(3)\n"
                  "s.connect(('127.0.0.1', %d))\n"
                  "s.sendall(b'tf-n13'); print(s.recv(64).decode())\n"
                  "sys.exit(5)\n" % (port, port))
        try:
            p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", shim,
                                str(port), path, "--", sys.executable, "-I",
                                "-S", "-c", client],
                               capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)
            if p.returncode != 5 or p.stdout.strip() != "ECHO:tf-n13":
                problems.append("rc %r stdout %r, want 5 and ECHO:tf-n13 -- "
                                "the relay died with the failed thread (%s)"
                                % (p.returncode, p.stdout.strip()[:80],
                                   p.stderr.strip()[-200:]))
        finally:
            srv.close()
        return problems, ["port        : %d" % port]
    _guard(suite, GRP_N, "N13 forwarder-survives-thread-start-failure", n13)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="sbx restricted mode: reads, exec filter, "
                    "clean env, supervised timeout, strict rlimits",
                    opts=opts, mode="grouped")
    helper = G._load_helper("sbx_helper_restrict")
    fx = _make_fixtures()
    try:
        _run_a(suite, helper, fx)
        _run_b(suite, helper, fx)
        _run_c(suite, helper, fx)
        _run_d(suite, helper, fx)
        _run_e(suite, helper, fx)
        _run_f(suite, helper, fx)
        skips = _live_skips(fx)
        _run_u(suite, helper, fx, skips)
        _run_v(suite, helper, fx, skips)
        _run_t(suite, helper, fx, skips)
        _run_l(suite, helper, fx, skips)
        _run_s(suite, helper, fx)
        _run_x(suite, helper, fx)
        _run_n(suite, helper, fx, skips)
    finally:
        if "n_sock" in fx:
            fx["n_sock"][1].close()
        if opts.keep:
            print("\n[--keep] fixtures retained at: %s" % fx["root"])
        else:
            shutil.rmtree(fx["root"], ignore_errors=True)
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
