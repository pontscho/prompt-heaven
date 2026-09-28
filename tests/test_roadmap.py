#!/usr/bin/env python3
"""Offline suite for the roadmap single writer
(ClaudeCode/skills/roadmap/scripts/roadmap.py).

THE SAFETY PROBLEM COMES FIRST
------------------------------
roadmap.py's default target is `docs/roadmap/roadmap.md` under the git
top-level, next to an `archive/` of immutable pages.  In this repository that
is live project state, and every mutating command rewrites it in place.  A
suite that ran one command against the default would edit the real roadmap.
So the default is UNREACHABLE by construction:

  * `sandbox_path()` is called on every path handed to the script, CLI and
    in-process alike, and raises unless the path resolves inside this run's
    `mkdtemp()` sandbox.
  * `run_cli()` refuses an argv without `--file`, and runs the child with
    `cwd=` a directory inside the sandbox.
  * `run_cli_default()`, the ONE runner for the default-target cases, refuses
    an argv that carries `--file` and a cwd outside the sandbox.
  * both runners apply the git isolation defaults -- GIT_CEILING_DIRECTORIES
    at the sandbox's parent, no global and no system git config -- and a
    case's `env_extra` is MERGED over them, never substituted for them, so a
    case that sets LC_ALL still cannot discover this repository or the user's
    git configuration.
  * group K digests the live roadmap and its archive before and after the run.

Group K needs no module: every case in it is about this suite's guards and the
live tree.  So it runs from the `finally` that wraps the whole body, ALSO when
roadmap.py does not exist.  That is the one deliberate difference from
test_checkpoint.py, which returns early on a missing target and so skips its
hygiene: here the guards are proven to bite before the first line of the
script exists.

WHAT GROUP A GATES, AND WHY BY AST
---------------------------------
The layer rules of roadmap.py (IO only in its section 3, the clock only in
`today()`, one spawn site, one reader, the standard streams named in four
functions) are properties of the SOURCE, and a behavioural test can only
sample them.  Each rule is checked over the parsed source against a set this
file spells independently of the module, and each scanner is pointed at a
planted copy of the real source carrying one defect, which it must report:
a checker that silently matches nothing is indistinguishable from a clean
tree.

The two caller-set cases and the IO_FUNCTIONS coverage case are EXACT set
checks.  While the `roadmap` row in tests/run.py still carries the `0`
placeholder they accept a SUBSET and name the declared members still absent;
the switch reads that row by AST at test time, so once a real count is
written the cases are exact for good.

A MISSING FUNCTION IS A RED CASE, NEVER A CRASH
----------------------------------------------
Every in-process case asks `need()` for the module attributes it touches; a
missing one is recorded as ONE failing case naming it.  Every group body is
also wrapped by `run_group()`, so an unexpected exception is one failing case
for that group and the next group still runs.  That is what let these groups
run red against a stub whose main() refused everything.

Groups:
  A  the format contract, the wiki type membership, and the AST gates over
     roadmap.py's source, each scanner with its planted control
  B  round trip against a hand-written expected file, parse/render identity,
     a render that is a no-op down to the mtime, init into a bare wiki root
  C  the wiki parsers read what roadmap.py writes (both copies, collect,
     freshness, verify), and the line-structure rule on every single-line
     value, with the planted `_line_break` control
  D  invariants, the WIP cap, move, rank, the append-only log, `wip`
  E  the dependency graph: self-block, unknown ids, cycles naming exactly
     the members and what they strand, `ready` over done AND dropped blockers
  F  the optimistic lock, in-process, with its two controls
  G  close: the golden archive, dropped on both reason routes, verbatim
     `show`, SHA-256, the commit and named close refusals, the close-route
     line and scalar rules, a missing or read-only archive/, containment,
     slugs, immutability, id-in-both repair, ids, crash simulation, the lock
     re-checked twice
  H  export: the golden on both routes twice, scope and counts, the source
     block (the filter-free dirty flag in git sandboxes, incl. planted
     fsmonitor and clean-filter config with their controls), --out refusals,
     a non-UTF-8 wiki root, a reader that closes early
  I  the section-7 refusals, staged input, file shapes, containment, dates
  J  negative controls: each G/H/B/C oracle pointed at a planted defect
  K  hygiene: the live roadmap and archive, the repo tree, bytecode, no
     `.roadmap-*` name in the sandbox, and the guards themselves (runs last,
     always)
  L  the CLI surface: --today, the default target, UTF-8 under an ASCII
     locale, a non-UTF-8 path, a reader that closes early, failing streams

The git sandboxes of G and H are built by this suite's own git (`git_run`),
under the same isolation as every child; a git that cannot init or commit
turns those cases INFO, never red.  The case count lives only in the SUITES
table of tests/run.py.

Usage:
  python3 tests/test_roadmap.py
  python3 tests/test_roadmap.py --brief
Exit code 0 iff every non-informational case passes.
"""

import ast
import collections
import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "roadmap"
TARGET = H.repo_path("ClaudeCode", "skills", "roadmap", "scripts",
                     "roadmap.py")

# The live state this suite must never reach.  Named here ONLY so group K can
# digest it; no other line in this file passes either to anything.
LIVE_ROADMAP = H.repo_path("docs", "roadmap", "roadmap.md")
LIVE_ARCHIVE = H.repo_path("docs", "roadmap", "archive")

WIKI_SCRIPTS = H.repo_path("ClaudeCode", "skills", "wiki", "scripts")
WIKILIB = os.path.join(WIKI_SCRIPTS, "_wikilib.py")
SERVER = H.repo_path("Scripts", "mcp-wiki.py")
REINDEX = os.path.join(WIKI_SCRIPTS, "reindex.py")
FRESHNESS = os.path.join(WIKI_SCRIPTS, "freshness.py")
RUN_PY = H.repo_path("tests", "run.py")

GA = "A. format contract + AST gates"
GB = "B. round trip, idempotence, byte preservation"
GC = "C. wiki-parser compatibility + line structure"
GD = "D. invariants, WIP, move, rank, log"
GE = "E. dependency graph + ready"
GF = "F. optimistic lock"
GG = "G. close, archive, crash recovery"
GH = "H. export"
GI = "I. refusals + staged input"
GJ = "J. negative control"
GK = "K. hygiene"
GL = "L. CLI surface"

# The on-disk format contract, spelled out independently of the module: a test
# that imported these would pass a rename that orphaned every file already
# written to disk.
BEGIN_PREFIX = "<!-- ROADMAP:BEGIN"
END_PREFIX = "<!-- ROADMAP:END"
COLUMNS = ("Lane", "Id", "State", "Title", "Ready")
TMP_PREFIX = ".roadmap-"
DEFAULT_REL = "docs/roadmap/roadmap.md"
LANES = (("now", "# now"), ("next", "# next"), ("later", "# later"),
         ("unset", "# inbox"))
SCHEMA = "roadmap-export/1"

# Every CLI case pins the clock; a case that is ABOUT --today overrides it.
TODAY = "2026-09-28"


def _d(label, value):
    return "%-12s: %s" % (label, value)


# ---------------------------------------------------------------------------
# the sandbox guard -- see the module docstring
# ---------------------------------------------------------------------------

_SANDBOX = None


def set_sandbox(path):
    global _SANDBOX
    _SANDBOX = os.path.realpath(path)


def sandbox_path(path):
    """Return `path`, or raise if it is not inside this run's sandbox.

    Called on EVERY path handed to the script under test.  The script's default
    target is the live roadmap; this is what makes reaching it impossible
    rather than unlikely.
    """
    real = os.path.realpath(path)
    if _SANDBOX is None:
        raise AssertionError("no sandbox is active; refusing to touch %r"
                             % (path,))
    if real != _SANDBOX and not real.startswith(_SANDBOX + os.sep):
        raise AssertionError("refusing to touch %r: outside the sandbox %r"
                             % (path, _SANDBOX))
    return path


def git_isolation():
    """The git defaults every child runs under.

    A function, not a constant: the ceiling is the sandbox's parent, which
    exists only once a sandbox does.  GIT_CEILING_DIRECTORIES stops the
    repository search AT the sandbox, so a child can find a git sandbox a case
    built inside it but never this repository; the two config variables keep
    the user's global and system git configuration out of every child.
    """
    if _SANDBOX is None:
        raise AssertionError("no sandbox is active; refusing to spawn")
    return {"GIT_CEILING_DIRECTORIES": os.path.dirname(_SANDBOX),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1"}


def _child_env(env_extra):
    env = dict(git_isolation())
    env.update(env_extra or {})
    return env


def _names_file_flag(arg):
    """True for `--file`, `--file=X` and every prefix argparse would expand to
    `--file` (allow_abbrev is argparse's default)."""
    flag = arg.split("=", 1)[0]
    return len(flag) > 2 and "--file".startswith(flag)


def _with_today(argv, today):
    if today is None or any(a == "--today" or a.startswith("--today=")
                            for a in argv):
        return argv
    return argv + ["--today", today]


def _spawn(argv, cwd, env, timeout):
    proc = subprocess.run([sys.executable, TARGET] + argv, input="",
                          capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=H.child_env(env))
    return proc.returncode, proc.stdout, proc.stderr


def run_cli(*args, **kwargs):
    """Run the script as a child process. Refuses an argv without `--file`.

    `--today TODAY` is appended unless the case passes its own `--today` (or
    `today=None` to send none at all).
    """
    env_extra = kwargs.pop("env_extra", None)
    timeout = kwargs.pop("timeout", 30)
    today = kwargs.pop("today", TODAY)
    if kwargs:
        raise TypeError("unexpected kwargs: %r" % (kwargs,))
    argv = [str(a) for a in args]
    if "--file" not in argv or argv.index("--file") + 1 >= len(argv):
        raise AssertionError("every invocation must name its own --file, or it "
                             "would target the live roadmap: %r" % (argv,))
    target = sandbox_path(argv[argv.index("--file") + 1])
    # The target's own directory, or its nearest existing ancestor: `init`
    # into a bare wiki root names a roadmap directory that does not exist yet.
    cwd = os.path.dirname(os.path.abspath(target))
    while not os.path.isdir(cwd):
        cwd = os.path.dirname(cwd)
    sandbox_path(cwd)
    return _spawn(_with_today(argv, today), cwd, _child_env(env_extra),
                  timeout)


def run_cli_default(cwd, *args, **kwargs):
    """Run the script with NO `--file`, for the default-target cases only.

    Refuses a cwd outside the sandbox, an argv that names `--file` in any
    spelling, and an environment whose GIT_CEILING_DIRECTORIES is not the
    sandbox's parent -- the three things that decide where the default
    resolves.
    """
    env_extra = kwargs.pop("env_extra", None)
    timeout = kwargs.pop("timeout", 30)
    today = kwargs.pop("today", TODAY)
    if kwargs:
        raise TypeError("unexpected kwargs: %r" % (kwargs,))
    sandbox_path(cwd)
    argv = [str(a) for a in args]
    if any(_names_file_flag(a) for a in argv):
        raise AssertionError("run_cli_default is for the default target; this "
                             "argv names --file: %r" % (argv,))
    env = _child_env(env_extra)
    if env.get("GIT_CEILING_DIRECTORIES") != os.path.dirname(_SANDBOX):
        raise AssertionError("GIT_CEILING_DIRECTORIES is %r, not the sandbox's "
                             "parent %r" % (env.get("GIT_CEILING_DIRECTORIES"),
                                            os.path.dirname(_SANDBOX)))
    return _spawn(_with_today(argv, today), cwd, env, timeout)


@contextlib.contextmanager
def captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def call_module(mod, name, path, *args, **kwargs):
    """Call a module function in-process on a sandbox path.

    `die()` raises SystemExit, so the refusals come back as an exit code here
    exactly as they do from the CLI.
    """
    sandbox_path(path)
    fn = getattr(mod, name)
    with captured() as (out, err):
        try:
            code = fn(path, *args, **kwargs)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def problem_if(condition, message):
    return [message] if condition else []


def missing_tokens(text, tokens):
    return [t for t in tokens if t not in text]


# ---------------------------------------------------------------------------
# fixtures: one wiki root per case, inside the sandbox
# ---------------------------------------------------------------------------

def write_file(path, body):
    """Write str (as UTF-8) or bytes to a sandbox path."""
    sandbox_path(path)
    data = body if isinstance(body, bytes) else body.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def stage_roadmap(workspace, case, text, archive=None):
    """`<case>/docs/roadmap/roadmap.md` (plus `archive/` entries) in the sandbox.

    Each case gets its own wiki root (`<case>/docs`), so the wiki-wide name
    scan sees only that case's pages and the leftover-temp checks read only
    that case's directories.  `text` None stages no roadmap.md; `archive`
    None stages no archive directory, `{}` an empty one.  Bodies may be BYTES,
    for the fixtures that exist to be undecodable.
    """
    roadmap_dir = sandbox_path(workspace.subdir(case, "docs", "roadmap"))
    path = os.path.join(roadmap_dir, "roadmap.md")
    if text is not None:
        write_file(path, text)
    if archive is not None:
        archive_dir = sandbox_path(workspace.subdir(case, "docs", "roadmap",
                                                    "archive"))
        for name in sorted(archive):
            write_file(os.path.join(archive_dir, name), archive[name])
    return path


def temp_leftovers(path):
    """`.roadmap-*` names next to the target."""
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory) if n.startswith(TMP_PREFIX))


def stale_temp_files(root):
    """Every `.roadmap-*` NAME anywhere under the sandbox: a temp file, a temp
    hard link the link route failed to unlink, or a directory."""
    found = []
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        found += [os.path.join(rel, name) for name in sorted(dirs + files)
                  if name.startswith(TMP_PREFIX)]
    return sorted(found)


def refusal(suite, workspace, cid, case, text, argv, tokens, why, group=GI,
            archive=None, absent=(), silent=False, one_line=False):
    """One refusal, gated on every property at once.

    A non-zero exit that still rewrote the roadmap, touched the archive, or
    left a `.roadmap-*` name behind is not a refusal.  `absent` names tokens
    the diagnostic must NOT carry (which of two gates fired); `silent` gates an
    empty stdout; `one_line` gates stderr being exactly one line -- the `die`
    route's shape, and the thing a traceback is not.
    """
    path = stage_roadmap(workspace, case, text, archive)
    archive_dir = os.path.join(os.path.dirname(path), "archive")
    existed = os.path.exists(path)
    before = H.sha256_file(path) if existed else None
    archive_before = H.file_digests(archive_dir)
    code, out, err = run_cli(*(list(argv) + ["--file", path]))
    problems = problem_if(code != 2, "exit %r, want 2" % code)
    if existed:
        problems += problem_if(H.sha256_file(path) != before,
                               "the roadmap was modified anyway")
    else:
        problems += problem_if(os.path.exists(path),
                               "a missing roadmap was CREATED")
    problems += problem_if(H.file_digests(archive_dir) != archive_before,
                           "the archive changed")
    leftovers = temp_leftovers(path) + temp_leftovers(
        os.path.join(archive_dir, "x"))
    problems += problem_if(leftovers, "temp name left behind: %r" % leftovers)
    problems += ["the diagnostic omits %r" % t
                 for t in missing_tokens(err, tokens)]
    problems += ["the diagnostic carries %r, so the wrong gate fired" % t
                 for t in absent if t in err]
    if silent:
        problems += problem_if(out.strip(), "it printed a partial answer: %r"
                               % out[:60])
    if one_line:
        problems += problem_if(len(err.strip().splitlines()) != 1,
                               "stderr is %d line(s), want exactly one"
                               % len(err.strip().splitlines()))
    suite.record(group, cid, problems,
                 detail=[why, "exit %r; stderr: %s" % (code, err.strip()
                                                       or "<empty>")])


# ---------------------------------------------------------------------------
# A. the sets the AST gates judge against -- section 5.1 of the plan, spelled
#    here independently of the module
# ---------------------------------------------------------------------------

# The only modules roadmap.py may import.  Each is named by the design:
# argparse (the CLI), collections (the namedtuples), datetime (today() and
# check_date), errno (the constructed FileNotFoundError), hashlib (the lock
# digests), io (io.UnsupportedOperation in _silence), json (--item-file and
# export), os, re, stat (S_ISREG in read_regular), subprocess (git()), sys,
# tempfile (mkstemp), unicodedata (slugify's NFKD).  A new one is a decision:
# add it here with its reason, never silently.
STDLIB_ALLOWED = frozenset((
    "argparse", "collections", "datetime", "errno", "hashlib", "io", "json",
    "os", "re", "stat", "subprocess", "sys", "tempfile", "unicodedata",
))

# The declared hardening prefix of every git spawn (S4-3): a VALUE check.
GIT_SAFE_ARGV = ("git", "-c", "core.fsmonitor=false", "-c",
                 "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
                 "-c", "safe.bareRepository=explicit")
# The only subcommands roadmap.py may run.  None of them refreshes the index,
# which is what would run a planted fsmonitor or clean filter (S2-2).
GIT_SUBCOMMANDS = frozenset(("rev-parse", "cat-file", "ls-tree"))

SHELL_NAMES = ("os.system", "os.popen", "subprocess.getoutput",
               "subprocess.getstatusoutput", "subprocess.Popen")
SHELL_PREFIXES = ("os.exec", "os.spawn")

# Section 3 of 5.1, verbatim: the IO boundary.  Nothing else may do IO.
IO_FUNCTIONS = (
    "_write_stream", "_silence",
    "io_fail", "_discard",
    "read_regular", "read_archive_entry",
    "decode", "digest",
    "take_snapshot", "check_snapshot",
    "write_atomic",
    "create_exclusive", "_publish_link",
    "git", "verify_commit", "resolve_target",
    "check_containment",
    "refuse_symlink", "refuse_existing",
    "ensure_archive_dir",
    "archive_names_for_id", "check_out_path",
    "read_state_files", "wiki_page_names",
    "read_staged_file",
    "worktree_dirty",
)
IO_FUNCTION_SET = frozenset(IO_FUNCTIONS)

# The only names under `os` pure code may use: string arithmetic on paths.
PURE_OS_NAMES = frozenset(("os.path.join", "os.path.basename",
                           "os.path.dirname", "os.path.splitext",
                           "os.path.normpath", "os.sep"))
FORBIDDEN_ROOTS = ("tempfile", "subprocess", "shutil", "glob")

CLOCK_NAMES = ("datetime.date.today", "datetime.datetime.now")
CLOCK_ROOT = "time"

# chain -> the only functions that may name it (M1).  _write_stream takes the
# stream as a parameter and names neither.
STREAM_OWNERS = collections.OrderedDict((
    ("sys.stdout", frozenset(("emit", "emit_raw"))),
    ("sys.stderr", frozenset(("die", "note"))),
))

# EXACT caller sets (round-3 M1), SUBSET only while the SUITES count is the
# `0` placeholder (L5).  Nobody weakens these to make a step green.
CREATE_EXCLUSIVE_CALLERS = frozenset(("cmd_close",))
WRITE_ATOMIC_CALLERS = frozenset(("commit", "cmd_init", "cmd_export"))

# The commands that do not end in commit(): cmd_init writes through
# write_atomic(ABSENT), the other three write no roadmap at all.
NO_COMMIT_COMMANDS = frozenset(("cmd_init", "cmd_list", "cmd_show",
                                "cmd_export"))

# Calls the IO error rule does not require a guard for -- each because it
# CANNOT raise an OSError, never because we chose not to guard it.
NONRAISING_IO_CALLS = {
    "os.path.islink": "returns False on any OSError (documented contract)",
    "os.path.lexists": "returns False on any OSError (documented contract)",
    "os.path.exists": "returns False on any OSError (documented contract)",
    "os.path.isdir": "returns False on any OSError (documented contract)",
    "os.path.isfile": "returns False on any OSError (documented contract)",
    "os.fsencode": "does no IO: a codec call on a path",
    "os.fsdecode": "does no IO: a codec call on a path",
    "os.strerror": "does no IO: a table lookup of an errno",
    "os.walk": "does not raise a listing error: it hands it to the onerror "
               "callback, which the walk-onerror case requires on every call",
}
# NOT in it: os.path.realpath and os.path.abspath (both can reach os.getcwd),
# and os.close (it can raise EIO or EINTR; read_regular nests its finally
# inside a guarded try instead).

# section-3 function -> the reason it may keep an unguarded IO call.  EMPTY:
# in the code the plan specifies, every IO call is guarded.  An entry must
# carry its reason, and an entry whose function no longer has an unguarded
# call fails the io-handler case, so this list cannot rot.
IO_HANDLER_EXEMPT = {}

IO_METHODS = ("write", "flush")

# chain -> the only functions that may name it (R31, S3-1).
READER_OWNERS = collections.OrderedDict((
    ("os.read", frozenset(("read_regular",))),
    ("os.open", frozenset(("read_regular", "_publish_link", "_silence"))),
    ("os.readlink", frozenset(("worktree_dirty",))),
))
# The builtin open, under both of its names (io.open IS the builtin).
BUILTIN_OPEN = ("open", "io.open")

# The re functions that neither match nor search: everything else under `re`
# is reached through a compiled object.
RE_MODULE_ALLOWED = frozenset(("re.compile", "re.escape"))

DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
NESTED_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ClassDef)
TRY_NODES = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())

MISSING = object()

Chain = collections.namedtuple("Chain", "name line owner")
Finding = collections.namedtuple("Finding", "owner what line text")
Mode = collections.namedtuple("Mode", "exact count error")


# ---------------------------------------------------------------------------
# A. AST plumbing -- every checker takes a parsed tree, so the planted copies
#    below go through exactly the code the real source does
# ---------------------------------------------------------------------------

def dotted(node):
    """`a.b.c` for a Name/Attribute chain rooted at a Name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _root(name):
    return name.split(".", 1)[0]


def _under(name, prefix):
    return name == prefix or name.startswith(prefix + ".")


def _where(owner):
    return "<module>" if owner is None else owner


def index_tree(tree):
    """node -> its top-level owner (function or class name, None at module
    level), and node -> parent."""
    owner_of, parent = {}, {}
    for top in tree.body:
        owner = top.name if isinstance(top, DEF_NODES) else None
        for node in ast.walk(top):
            owner_of[node] = owner
            for child in ast.iter_child_nodes(node):
                parent[child] = node
    return owner_of, parent


def chains(tree):
    """Every Name/Attribute chain, taken at its OUTERMOST Attribute: so
    `os.path.exists(p)` is judged as `os.path.exists`, never as `os.path`."""
    owner_of, parent = index_tree(tree)
    out = []
    for node, owner in owner_of.items():
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        up = parent.get(node)
        if isinstance(up, ast.Attribute) and up.value is node:
            continue
        name = dotted(node)
        if name is not None:
            out.append(Chain(name, node.lineno, owner))
    return sorted(out, key=lambda c: (c.line, c.name))


def calls(tree):
    """(dotted callee or None, Call node, owner) for every call, in order."""
    owner_of, _parent = index_tree(tree)
    out = [(dotted(n.func), n, owner) for n, owner in owner_of.items()
           if isinstance(n, ast.Call)]
    return sorted(out, key=lambda t: (t[1].lineno, t[1].col_offset))


def functions(tree):
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def references(tree, name):
    """The owners of every Load of the bare name `name`."""
    owner_of, _parent = index_tree(tree)
    return {_where(owner) for node, owner in owner_of.items()
            if isinstance(node, ast.Name) and node.id == name
            and isinstance(node.ctx, ast.Load)}


def _keyword(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _arg(call, index, name):
    if len(call.args) > index:
        return call.args[index]
    return _keyword(call, name)


def is_forbidden(name):
    """The purity rule (5.1): forbidden outside IO_FUNCTIONS."""
    if name == "open":
        return True
    root = _root(name)
    if root in FORBIDDEN_ROOTS:
        return True
    return root == "os" and name not in PURE_OS_NAMES


def catches_oserror(try_node):
    """A handler whose type SPELLS OSError, bare or in a tuple (I3)."""
    for handler in try_node.handlers:
        kind = handler.type
        names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
        if any(isinstance(n, ast.Name) and n.id == "OSError" for n in names):
            return True
    return False


def io_callee(call):
    """The callee of an IO CALL, or None (the io-handler rule, H1/M2)."""
    name = dotted(call.func)
    if name is not None and is_forbidden(name) \
            and name not in NONRAISING_IO_CALLS:
        return name
    if isinstance(call.func, ast.Attribute) and call.func.attr in IO_METHODS:
        return name or "<expr>.%s" % call.func.attr
    return None


def io_sites(fn):
    """(callee, line, guarded) for every IO call in fn's own body.

    Guarded means: inside the BODY of a try whose handlers spell OSError, at
    any depth.  A try's handlers, orelse and finalbody are judged by the
    ENCLOSING state, never by that try.  Nested defs are excluded.
    """
    sites = []

    def visit(node, guarded):
        if isinstance(node, NESTED_NODES):
            return
        if isinstance(node, TRY_NODES):
            inner = guarded or catches_oserror(node)
            for stmt in node.body:
                visit(stmt, inner)
            for part in (node.handlers, node.orelse, node.finalbody):
                for stmt in part:
                    visit(stmt, guarded)
            return
        if isinstance(node, ast.Call):
            callee = io_callee(node)
            if callee is not None:
                sites.append((callee, node.lineno, guarded))
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    for stmt in fn.body:
        visit(stmt, False)
    return sites


# ---------------------------------------------------------------------------
# A. the checkers -- each returns Findings, so a control can demand that a
#    SPECIFIC one fires
# ---------------------------------------------------------------------------

def stdlib_findings(tree):
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) not in STDLIB_ALLOWED:
                    out.append(Finding(None, alias.name, line,
                                       "line %d imports %s, outside the "
                                       "stdlib allowlist" % (line, alias.name)))
                if alias.asname:
                    out.append(Finding(None, alias.name, line,
                                       "line %d imports %s as %s: every "
                                       "dotted-name gate here matches the "
                                       "module's own name"
                                       % (line, alias.name, alias.asname)))
        elif isinstance(node, ast.ImportFrom):
            out.append(Finding(None, node.module, line,
                               "line %d is a from-import of %s: every "
                               "dotted-name gate here matches the module's "
                               "own name" % (line, node.module)))
        elif isinstance(node, ast.Name) and node.id in ("_wikilib",
                                                        "__import__"):
            out.append(Finding(None, node.id, line,
                               "line %d names %s" % (line, node.id)))
        elif isinstance(node, ast.Attribute) and node.attr == "_wikilib":
            out.append(Finding(None, node.attr, line,
                               "line %d names %s" % (line, node.attr)))
        if isinstance(node, (ast.Name, ast.FunctionDef)):
            ident = node.id if isinstance(node, ast.Name) else node.name
            if ident in ("SKIP_DIRS", "SKIP_FILES"):
                out.append(Finding(None, ident, line,
                                   "line %d defines or names %s, a copy of "
                                   "_wikilib's skip list" % (line, ident)))
    for chain in chains(tree):
        if _under(chain.name, "sys.path"):
            out.append(Finding(chain.owner, chain.name, chain.line,
                               "line %d touches %s (in %s)"
                               % (chain.line, chain.name,
                                  _where(chain.owner))))
    return out


def _starts_with_safe_argv(expr):
    while isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        expr = expr.left
    if isinstance(expr, ast.Name):
        return expr.id == "GIT_SAFE_ARGV"
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
            and expr.func.id in ("list", "tuple") and len(expr.args) == 1:
        return _starts_with_safe_argv(expr.args[0])
    if isinstance(expr, (ast.List, ast.Tuple)) and expr.elts \
            and isinstance(expr.elts[0], ast.Starred):
        return _starts_with_safe_argv(expr.elts[0].value)
    return False


def _argv_from_safe_argv(expr, fn):
    """The argv starts with GIT_SAFE_ARGV, directly or through ONE local name
    every assignment of which does."""
    if _starts_with_safe_argv(expr):
        return True
    if not isinstance(expr, ast.Name) or fn is None:
        return False
    values = [node.value for node in ast.walk(fn)
              if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == expr.id
                      for t in node.targets)]
    return bool(values) and all(_starts_with_safe_argv(v) for v in values)


def git_spawn_findings(tree):
    out = []
    funcs = functions(tree)
    for callee, node, owner in calls(tree):
        if callee != "subprocess.run":
            continue
        line = node.lineno
        missing = [k for k in ("stdin", "timeout") if _keyword(node, k) is None]
        if missing:
            out.append(Finding(owner, "keywords", line,
                               "line %d: subprocess.run without %s="
                               % (line, "=, ".join(missing))))
        if owner != "git":
            out.append(Finding(owner, "owner", line,
                               "line %d: subprocess.run in %s, not in git()"
                               % (line, _where(owner))))
        argv = _arg(node, 0, "args")
        if argv is None or not _argv_from_safe_argv(argv, funcs.get(owner)):
            out.append(Finding(owner, "argv", line,
                               "line %d: the argv is not built from "
                               "GIT_SAFE_ARGV" % line))
    return out


def git_subcommand_findings(tree):
    out = []
    for callee, node, owner in calls(tree):
        if callee != "git":
            continue
        line = node.lineno
        first = _arg(node, 0, "args")
        if not isinstance(first, ast.List) or not first.elts:
            out.append(Finding(owner, "<not a list literal>", line,
                               "line %d (%s): git() without a list literal"
                               % (line, _where(owner))))
            continue
        head = first.elts[0]
        if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
            out.append(Finding(owner, "<not a string constant>", line,
                               "line %d (%s): the subcommand is not a string "
                               "constant" % (line, _where(owner))))
        elif head.value not in GIT_SUBCOMMANDS:
            out.append(Finding(owner, head.value, line,
                               "line %d (%s): git %s is outside %s"
                               % (line, _where(owner), head.value,
                                  sorted(GIT_SUBCOMMANDS))))
    return out


def shell_findings(tree):
    out = []
    for _callee, node, owner in calls(tree):
        if _keyword(node, "shell") is not None:
            out.append(Finding(owner, "shell=", node.lineno,
                               "line %d (%s): a shell= keyword"
                               % (node.lineno, _where(owner))))
    for chain in chains(tree):
        if any(_under(chain.name, n) for n in SHELL_NAMES) \
                or chain.name.startswith(SHELL_PREFIXES):
            out.append(Finding(chain.owner, chain.name, chain.line,
                               "line %d (%s): %s"
                               % (chain.line, _where(chain.owner),
                                  chain.name)))
    return out


def purity_findings(tree):
    return [Finding(c.owner, c.name, c.line,
                    "line %d: %s in %s, outside section 3"
                    % (c.line, c.name, _where(c.owner)))
            for c in chains(tree)
            if c.owner not in IO_FUNCTION_SET and is_forbidden(c.name)]


def clock_findings(tree):
    return [Finding(c.owner, c.name, c.line,
                    "line %d: %s in %s -- only today() reads the clock"
                    % (c.line, c.name, _where(c.owner)))
            for c in chains(tree)
            if (c.name in CLOCK_NAMES or _root(c.name) == CLOCK_ROOT)
            and c.owner != "today"]


def stream_findings(tree):
    out = []
    for c in chains(tree):
        for stream, owners in STREAM_OWNERS.items():
            if _under(c.name, stream) and c.owner not in owners:
                out.append(Finding(c.owner, c.name, c.line,
                                   "line %d: %s in %s -- only %s may name %s"
                                   % (c.line, c.name, _where(c.owner),
                                      " / ".join(sorted(owners)), stream)))
    return out


def print_findings(tree):
    return [Finding(c.owner, c.name, c.line,
                    "line %d: print in %s" % (c.line, _where(c.owner)))
            for c in chains(tree) if c.name == "print"]


def commit_findings(tree):
    """Every cmd_* in COMMANDS but the four that write no roadmap state
    calls commit().  Returns (findings, the listed function names)."""
    table = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "COMMANDS"
                for t in node.targets):
            table = node.value
    if not isinstance(table, ast.Dict):
        return [Finding(None, "COMMANDS", 0,
                        "no module-level COMMANDS dict literal")], []
    funcs = functions(tree)
    out, listed = [], []
    for value in table.values:
        if not isinstance(value, ast.Name):
            out.append(Finding(None, "COMMANDS", value.lineno,
                               "line %d: a COMMANDS value is not a function "
                               "name" % value.lineno))
            continue
        listed.append(value.id)
        if value.id in NO_COMMIT_COMMANDS:
            continue
        fn = funcs.get(value.id)
        if fn is None:
            out.append(Finding(value.id, "undefined", value.lineno,
                               "%s is listed in COMMANDS and not defined"
                               % value.id))
            continue
        if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "commit" for n in ast.walk(fn)):
            out.append(Finding(value.id, "commit", fn.lineno,
                               "%s (line %d) has no commit( call"
                               % (value.id, fn.lineno)))
    return out, listed


def io_handler_findings(tree):
    """Every UNGUARDED IO call in a defined IO_FUNCTIONS member."""
    out = []
    funcs = functions(tree)
    for name in IO_FUNCTIONS:
        fn = funcs.get(name)
        if fn is None:
            continue
        for callee, line, guarded in io_sites(fn):
            if not guarded:
                out.append(Finding(name, callee, line,
                                   "%s line %d: %s outside the body of a try "
                                   "whose handlers spell OSError"
                                   % (name, line, callee)))
    return out


def walk_onerror_findings(tree):
    return [Finding(owner, "os.walk", node.lineno,
                    "line %d (%s): os.walk without onerror="
                    % (node.lineno, _where(owner)))
            for callee, node, owner in calls(tree)
            if callee == "os.walk" and _keyword(node, "onerror") is None]


def single_reader_findings(tree):
    out = []
    for c in chains(tree):
        if c.name in BUILTIN_OPEN:
            out.append(Finding(c.owner, c.name, c.line,
                               "line %d: %s in %s -- read_regular is the one "
                               "reader" % (c.line, c.name, _where(c.owner))))
        owners = READER_OWNERS.get(c.name)
        if owners is not None and c.owner not in owners:
            out.append(Finding(c.owner, c.name, c.line,
                               "line %d: %s in %s -- allowed only in %s"
                               % (c.line, c.name, _where(c.owner),
                                  " / ".join(sorted(owners)))))
    return out


def regex_findings(tree):
    out = []
    for callee, node, owner in calls(tree):
        line = node.lineno
        if callee == "re.compile":
            flags = _arg(node, 1, "flags")
            if flags is None or not any(
                    isinstance(n, ast.Attribute) and dotted(n) == "re.ASCII"
                    for n in ast.walk(flags)):
                out.append(Finding(owner, "re.ASCII", line,
                                   "line %d: re.compile without re.ASCII"
                                   % line))
            pattern = _arg(node, 0, "pattern")
            if not (isinstance(pattern, ast.Constant)
                    and isinstance(pattern.value, str)):
                out.append(Finding(owner, "pattern", line,
                                   "line %d: the pattern is not a str literal, "
                                   "so its anchors cannot be checked" % line))
            else:
                text = pattern.value
                if text.startswith("^"):
                    out.append(Finding(owner, "^", line,
                                       "line %d: pattern %r starts with ^"
                                       % (line, text)))
                if text.endswith("$") and not text.endswith("\\$"):
                    out.append(Finding(owner, "$", line,
                                       "line %d: pattern %r ends with $ -- $ "
                                       "also matches before a trailing "
                                       "newline" % (line, text)))
        elif callee is not None and _root(callee) == "re" \
                and callee not in RE_MODULE_ALLOWED:
            out.append(Finding(owner, callee, line,
                               "line %d (%s): %s -- every pattern is "
                               "compiled with re.ASCII and used through its "
                               "compiled object" % (line, _where(owner),
                                                    callee)))
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("match", "search"):
            receiver = dotted(func.value)
            if receiver is not None and receiver.split(".")[-1].endswith("_RE"):
                what = "%s.%s" % (receiver, func.attr)
                out.append(Finding(owner, what, line,
                                   "line %d (%s): %s -- only .fullmatch( on a "
                                   "*_RE" % (line, _where(owner), what)))
    return out


# ---------------------------------------------------------------------------
# A. the durable SUBSET/EXACT switch (L5): the `roadmap` row of tests/run.py,
#    read by AST -- run.py is parsed, never imported
# ---------------------------------------------------------------------------

def declared_suite_count(path=RUN_PY, name=NAME):
    """(count, None) or (None, why)."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SUITES"
                for t in node.targets)):
            continue
        if not isinstance(node.value, ast.List):
            return None, "SUITES in tests/run.py is not a list literal"
        for row in node.value.elts:
            if not (isinstance(row, ast.Tuple) and row.elts
                    and isinstance(row.elts[0], ast.Constant)
                    and row.elts[0].value == name):
                continue
            if len(row.elts) < 4:
                return None, "the %r SUITES row has no count" % name
            count = row.elts[3]
            if isinstance(count, ast.Constant) and type(count.value) is int:
                return count.value, None
            return None, ("the %r SUITES row's count is not an int literal: %s"
                          % (name, ast.unparse(count)))
        return None, "tests/run.py SUITES has no %r row" % name
    return None, "tests/run.py has no SUITES assignment"


def suite_mode():
    count, error = declared_suite_count()
    if error is not None:
        return Mode(True, None, error)
    return Mode(count != 0, count, None)


def mode_line(mode):
    if mode.error is not None:
        return "UNKNOWN (%s) -- judged EXACT" % mode.error
    if mode.exact:
        return "EXACT (the roadmap SUITES count is %d)" % mode.count
    return "SUBSET (the roadmap SUITES count is the 0 placeholder)"


# ---------------------------------------------------------------------------
# A. planted copies: parse the REAL source, apply one defect, unparse, reparse
# ---------------------------------------------------------------------------

def planted(source, mutate):
    """(the planted tree, how many sites the mutation changed)."""
    tree = ast.parse(source)
    changes = mutate(tree)
    ast.fix_missing_locations(tree)
    return ast.parse(ast.unparse(tree)), changes


def insert_first(fn_name, statement):
    def mutate(tree):
        fn = functions(tree).get(fn_name)
        if fn is None:
            return 0
        fn.body.insert(0, ast.parse(statement).body[0])
        return 1
    return mutate


def append_to_module(source_text):
    def mutate(tree):
        tree.body.extend(ast.parse(source_text).body)
        return 1
    return mutate


class _UnwrapGuard(ast.NodeTransformer):
    """Replace every try that catches OSError by its bare body."""

    def __init__(self):
        self.count = 0

    def visit_Try(self, node):
        self.generic_visit(node)
        if catches_oserror(node):
            self.count += 1
            return node.body
        return node

    visit_TryStar = visit_Try


class _HoistMkstemp(ast.NodeTransformer):
    """Move the statement holding tempfile.mkstemp ABOVE its guarded try."""

    def __init__(self):
        self.count = 0

    def visit_Try(self, node):
        self.generic_visit(node)
        if self.count or not catches_oserror(node):
            return node
        for index, stmt in enumerate(node.body):
            if any(isinstance(n, ast.Call) and dotted(n.func) == "tempfile.mkstemp"
                   for n in ast.walk(stmt)):
                del node.body[index]
                if not node.body:
                    node.body.append(ast.Pass())
                self.count += 1
                return [stmt, node]
        return node

    visit_TryStar = visit_Try


class _BareFinallyClose(ast.NodeTransformer):
    """Flatten read_regular's nested try: the reads stay in the body of the
    guarded try, and os.close moves into THAT try's finally, so no enclosing
    guarded try is left around it."""

    def __init__(self):
        self.count = 0

    def visit_Try(self, node):
        self.generic_visit(node)
        if self.count or not catches_oserror(node):
            return node
        for index, stmt in enumerate(node.body):
            if isinstance(stmt, TRY_NODES) and not stmt.handlers and any(
                    isinstance(n, ast.Call) and dotted(n.func) == "os.close"
                    for s in stmt.finalbody for n in ast.walk(s)):
                body = node.body[:index] + stmt.body + node.body[index + 1:]
                self.count += 1
                return ast.Try(body=body, handlers=node.handlers,
                               orelse=node.orelse,
                               finalbody=stmt.finalbody + node.finalbody)
        return node


def transform(fn_name, transformer_cls):
    def mutate(tree):
        fn = functions(tree).get(fn_name)
        if fn is None:
            return 0
        transformer = transformer_cls()
        transformer.visit(fn)
        return transformer.count
    return mutate


def rename_oserror_handlers(fn_name, new_name):
    def mutate(tree):
        fn = functions(tree).get(fn_name)
        if fn is None:
            return 0
        count = 0
        for node in ast.walk(fn):
            if not isinstance(node, TRY_NODES):
                continue
            for handler in node.handlers:
                kind = handler.type
                names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
                if any(isinstance(n, ast.Name) and n.id == "OSError"
                       for n in names):
                    handler.type = ast.Name(id=new_name, ctx=ast.Load())
                    count += 1
        return count
    return mutate


def drop_keyword(fn_name, callee, keyword):
    def mutate(tree):
        fn = functions(tree).get(fn_name)
        if fn is None:
            return 0
        count = 0
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and dotted(node.func) == callee:
                kept = [k for k in node.keywords if k.arg != keyword]
                count += len(node.keywords) - len(kept)
                node.keywords = kept
        return count
    return mutate


def control(suite, cid, source, mutate, checker, want, planted_what, why,
            extra=None):
    """One negative control: the checker must report the planted defect.

    `want(finding)` picks the finding the plant must produce; `extra(tree)`
    returns further problems (a control that must ALSO prove what the plant
    left intact).
    """
    tree, changes = planted(source, mutate)
    problems, findings = [], []
    if not changes:
        problems.append("the plant changed nothing (%s) -- the control did not "
                        "run" % planted_what)
    else:
        findings = checker(tree)
        if not any(want(f) for f in findings):
            problems.append("the gate MISSED the planted defect: %s"
                            % planted_what)
        if extra is not None:
            problems += extra(tree)
    suite.record(GA, cid, problems,
                 detail=[_d("planted", planted_what),
                         _d("fired", "%d finding(s), %d site(s) changed"
                            % (len(findings), changes)),
                         _d("why", why)])


# ---------------------------------------------------------------------------
# A. the cases
# ---------------------------------------------------------------------------

def _findings_case(suite, cid, findings, rule, extra_detail=()):
    suite.record(GA, cid, [f.text for f in findings],
                 detail=[_d("rule", rule)] + list(extra_detail))


def case_format_contract(suite, mod):
    got = dict((name, getattr(mod, name, MISSING)) for name in (
        "DEFAULT_FILE", "SUMMARY_BEGIN", "SUMMARY_END", "SUMMARY_COLUMNS",
        "LANE_HEADINGS", "EXPORT_SCHEMA", "TMP_PREFIX"))
    problems = ["roadmap.py defines no %s" % name
                for name, value in sorted(got.items()) if value is MISSING]
    if got["DEFAULT_FILE"] is not MISSING:
        problems += problem_if(got["DEFAULT_FILE"] != DEFAULT_REL,
                               "DEFAULT_FILE is %r, want %r"
                               % (got["DEFAULT_FILE"], DEFAULT_REL))
    for name, prefix in (("SUMMARY_BEGIN", BEGIN_PREFIX),
                         ("SUMMARY_END", END_PREFIX)):
        value = got[name]
        if value is not MISSING:
            problems += problem_if(not str(value).startswith(prefix),
                                   "%s %r does not start with %r"
                                   % (name, value, prefix))
    if got["SUMMARY_COLUMNS"] is not MISSING:
        problems += problem_if(tuple(got["SUMMARY_COLUMNS"]) != COLUMNS,
                               "SUMMARY_COLUMNS is %r, want %r"
                               % (got["SUMMARY_COLUMNS"], COLUMNS))
    if got["LANE_HEADINGS"] is not MISSING:
        lanes = tuple(tuple(pair) for pair in got["LANE_HEADINGS"])
        problems += problem_if(lanes != LANES, "LANE_HEADINGS is %r, want %r"
                               % (got["LANE_HEADINGS"], LANES))
    if got["EXPORT_SCHEMA"] is not MISSING:
        problems += problem_if(got["EXPORT_SCHEMA"] != SCHEMA,
                               "EXPORT_SCHEMA is %r, want %r"
                               % (got["EXPORT_SCHEMA"], SCHEMA))
    if got["TMP_PREFIX"] is not MISSING:
        problems += problem_if(got["TMP_PREFIX"] != TMP_PREFIX,
                               "TMP_PREFIX is %r, want %r"
                               % (got["TMP_PREFIX"], TMP_PREFIX))
    suite.record(GA, "format-contract", problems,
                 detail=[_d("default", repr(got["DEFAULT_FILE"])),
                         _d("why", "the on-disk format spelled here, not "
                                   "imported: a rename that orphaned every "
                                   "written file would otherwise pass")])


def case_type_membership(suite, mod, lib):
    """R5: roadmap.py's type names are members of the wiki's own sets."""
    problems = []
    lib_file = os.path.realpath(getattr(lib, "__file__", None) or "")
    problems += problem_if(lib_file != os.path.realpath(WIKILIB),
                           "the wiki constants came from %r, not %s"
                           % (lib_file, WIKILIB))
    types_ = (("ROADMAP_TYPE", getattr(mod, "ROADMAP_TYPE", MISSING),
               ("TYPE_ORDER", "UNTRACKED_TYPES")),
              ("ITEM_TYPE", getattr(mod, "ITEM_TYPE", MISSING),
               ("TYPE_ORDER", "UNTRACKED_TYPES", "ORPHAN_EXEMPT_TYPES",
                "INDEX_COUNTED_TYPES")))
    for label, value, sets in types_:
        if value is MISSING:
            problems.append("roadmap.py defines no %s" % label)
            continue
        for const in sets:
            members = getattr(lib, const, ())
            problems += problem_if(value not in members,
                                   "%s %r is not in _wikilib.%s %r"
                                   % (label, value, const, members))
    suite.record(GA, "type-membership", problems,
                 detail=[_d("roadmap", repr(getattr(mod, "ROADMAP_TYPE",
                                                    None))),
                         _d("item", repr(getattr(mod, "ITEM_TYPE", None))),
                         _d("against", "_wikilib as reindex.py loads it")])


def case_stdlib(suite, tree):
    _findings_case(suite, "stdlib-only", stdlib_findings(tree),
                   "plain imports of %s only; no _wikilib, no sys.path, no "
                   "SKIP_DIRS / SKIP_FILES copy (NFR-3)"
                   % ", ".join(sorted(STDLIB_ALLOWED)))


def case_git(suite, mod, tree, source):
    _findings_case(suite, "git-spawn-site", git_spawn_findings(tree),
                   "every subprocess.run passes stdin= and timeout=, lives in "
                   "git(), and its argv starts with GIT_SAFE_ARGV (R18, R27)")
    value = getattr(mod, "GIT_SAFE_ARGV", MISSING)
    suite.record(GA, "git-safe-argv-value",
                 problem_if(value != GIT_SAFE_ARGV,
                            "GIT_SAFE_ARGV is %r, want %r"
                            % (None if value is MISSING else value,
                               GIT_SAFE_ARGV)),
                 detail=[_d("want", " ".join(GIT_SAFE_ARGV)),
                         _d("scope", "a value check on the declared hardening "
                                     "(S4-3); GIT_NO_LAZY_FETCH is declared "
                                     "and not gated")])
    _findings_case(suite, "git-subcommands", git_subcommand_findings(tree),
                   "every git(...) call passes a list literal whose first "
                   "element is one of %s -- no index-refreshing command "
                   "(S2-2)" % ", ".join(sorted(GIT_SUBCOMMANDS)))
    control(suite, "control-git-status-detected", source,
            append_to_module("def _planted_status():\n"
                             "    return git(['status', '--porcelain'], '.')\n"),
            git_subcommand_findings,
            lambda f: f.owner == "_planted_status" and f.what == "status",
            "a function calling git(['status', '--porcelain'], ...)",
            "status refreshes the index, which runs a planted fsmonitor")


def case_no_shell(suite, tree):
    _findings_case(suite, "no-shell", shell_findings(tree),
                   "no shell= keyword anywhere; none of %s, os.exec*, "
                   "os.spawn* (R25, NFR-10)" % ", ".join(SHELL_NAMES))


def case_purity(suite, tree, source, mode):
    _findings_case(suite, "purity-io-only-in-section-3", purity_findings(tree),
                   "outside IO_FUNCTIONS: no builtin open, nothing under "
                   "%s, nothing under os but %s (R22)"
                   % (", ".join(FORBIDDEN_ROOTS),
                      ", ".join(sorted(PURE_OS_NAMES))))
    _findings_case(suite, "purity-clock-only-in-today", clock_findings(tree),
                   "%s and anything under %s occur only in today()"
                   % (", ".join(CLOCK_NAMES), CLOCK_ROOT))
    _findings_case(suite, "purity-std-streams", stream_findings(tree),
                   "sys.stdout only in emit / emit_raw, sys.stderr only in "
                   "die / note; _write_stream names neither (M1)")
    _findings_case(suite, "purity-no-print", print_findings(tree),
                   "emit and emit_raw are the only stdout writers")

    for cid, callee, declared in (
            ("callers-create-exclusive", "create_exclusive",
             CREATE_EXCLUSIVE_CALLERS),
            ("callers-write-atomic", "write_atomic", WRITE_ATOMIC_CALLERS)):
        observed = references(tree, callee)
        extra = sorted(observed - declared)
        absent = sorted(declared - observed)
        problems = [mode.error] if mode.error is not None else []
        problems += problem_if(extra, "%s is reached from %s, outside the "
                               "declared set %s"
                               % (callee, extra, sorted(declared)))
        problems += problem_if(mode.exact and absent,
                               "declared caller(s) %s do not reach %s -- the "
                               "set is EXACT once the roadmap SUITES count is "
                               "set (L5)" % (absent, callee))
        suite.record(GA, cid, problems,
                     detail=[_d("mode", mode_line(mode)),
                             _d("declared", sorted(declared)),
                             _d("observed", sorted(observed)),
                             _d("absent", "%s%s" % (
                                 absent or "none",
                                 " (still absent, SUBSET mode)"
                                 if absent and not mode.exact else ""))])

    findings, listed = commit_findings(tree)
    _findings_case(suite, "commands-end-in-commit", findings,
                   "every cmd_* in COMMANDS but %s calls commit("
                   % ", ".join(sorted(NO_COMMIT_COMMANDS)),
                   [_d("listed", ", ".join(listed) or "none")])

    control(suite, "control-open-in-parse-roadmap", source,
            insert_first("parse_roadmap", "open('planted')"),
            purity_findings,
            lambda f: f.owner == "parse_roadmap" and f.what == "open",
            "open( at the top of parse_roadmap",
            "a pure parser that reads a file is the erosion R22 names")
    control(suite, "control-os-path-exists-in-cmd-init", source,
            insert_first("cmd_init", "os.path.exists('planted')"),
            purity_findings,
            lambda f: f.owner == "cmd_init" and f.what == "os.path.exists",
            "os.path.exists( at the top of cmd_init",
            "judged as os.path.exists, not as the pure-looking os.path")
    control(suite, "control-sys-stdout-in-cmd-list", source,
            insert_first("cmd_list", "sys.stdout.write('planted')"),
            stream_findings,
            lambda f: f.owner == "cmd_list" and _under(f.what, "sys.stdout"),
            "sys.stdout.write( at the top of cmd_list",
            "a second stdout writer bypasses the one UTF-8 byte route")


def case_io_handler(suite, tree, source, mode):
    unguarded = io_handler_findings(tree)
    funcs = functions(tree)
    defined = [name for name in IO_FUNCTIONS if name in funcs]
    total = sum(len(io_sites(funcs[name])) for name in defined)
    offenders = sorted({f.owner for f in unguarded})
    problems = [f.text for f in unguarded if f.owner not in IO_HANDLER_EXEMPT]
    problems += ["IO_HANDLER_EXEMPT names %s, which has no unguarded IO call "
                 "any more -- remove the entry" % name
                 for name in sorted(IO_HANDLER_EXEMPT) if name not in offenders]
    suite.record(GA, "io-handler", problems,
                 detail=[_d("rule", "every IO call in section 3 lies in the "
                                    "BODY of a try whose handlers spell "
                                    "OSError (R32, NFR-13, H1)"),
                         _d("checked", "%d of %d IO_FUNCTIONS defined, %d IO "
                                       "call(s), %d unguarded"
                            % (len(defined), len(IO_FUNCTIONS), total,
                               len(unguarded))),
                         _d("exempt", sorted(IO_HANDLER_EXEMPT) or "none")])

    absent = [name for name in IO_FUNCTIONS if name not in funcs]
    problems = [mode.error] if mode.error is not None else []
    problems += problem_if(mode.exact and absent,
                           "IO_FUNCTIONS member(s) not defined: %s -- every "
                           "one must exist once the roadmap SUITES count is "
                           "set (L5)" % absent)
    suite.record(GA, "io-functions-defined", problems,
                 detail=[_d("mode", mode_line(mode)),
                         _d("defined", "%d of %d" % (len(defined),
                                                     len(IO_FUNCTIONS))),
                         _d("absent", "%s%s" % (
                             absent or "none",
                             " (still absent, SUBSET mode)"
                             if absent and not mode.exact else ""))])

    _findings_case(suite, "walk-onerror", walk_onerror_findings(tree),
                   "every os.walk passes onerror=: its NONRAISING membership "
                   "must not hide a listing error (M1)")

    control(suite, "control-io-unguarded-archive-listing", source,
            transform("archive_names_for_id", _UnwrapGuard),
            io_handler_findings,
            lambda f: f.owner == "archive_names_for_id",
            "archive_names_for_id with its try / except OSError removed",
            "the listing error escapes as a traceback")
    control(suite, "control-io-baseexception-handler", source,
            rename_oserror_handlers("archive_names_for_id", "BaseException"),
            io_handler_findings,
            lambda f: f.owner == "archive_names_for_id",
            "archive_names_for_id with its handler spelled except "
            "BaseException:",
            "clean-up-and-re-raise is not a guard: the handler must SPELL "
            "OSError")

    def still_guarded(tree_):
        fn = functions(tree_).get("write_atomic")
        guarded = [s for s in io_sites(fn) if s[2]] if fn is not None else []
        return problem_if(not guarded,
                          "the planted write_atomic has no guarded IO call "
                          "left, so it does not prove the gate is per call")
    control(suite, "control-io-mkstemp-above-try", source,
            transform("write_atomic", _HoistMkstemp),
            io_handler_findings,
            lambda f: f.owner == "write_atomic" and f.what == "tempfile.mkstemp",
            "write_atomic with tempfile.mkstemp moved ABOVE its try",
            "one guarded and one unguarded IO site: a per-function 'has some "
            "handler' rule passes this (H1)",
            extra=still_guarded)

    def reads_still_guarded(tree_):
        hits = [f for f in io_handler_findings(tree_)
                if f.owner == "read_regular" and f.what == "os.read"]
        return problem_if(hits, "the plant unguarded os.read too, so the "
                                "detection does not isolate the finally rule")
    control(suite, "control-io-close-in-bare-finally", source,
            transform("read_regular", _BareFinallyClose),
            io_handler_findings,
            lambda f: f.owner == "read_regular" and f.what == "os.close",
            "read_regular with os.close(fd) in the finally of the guarded "
            "try, no enclosing guarded try",
            "a call in finalbody is not guarded by that try",
            extra=reads_still_guarded)
    control(suite, "control-walk-without-onerror", source,
            drop_keyword("wiki_page_names", "os.walk", "onerror"),
            walk_onerror_findings,
            lambda f: f.owner == "wiki_page_names",
            "wiki_page_names whose os.walk has no onerror=",
            "the walk-onerror sub-check must fire")


def case_single_reader(suite, tree, source):
    _findings_case(suite, "single-reader", single_reader_findings(tree),
                   "no builtin open anywhere; os.read only in read_regular; "
                   "os.open only in read_regular / _publish_link / _silence; "
                   "os.readlink only in worktree_dirty (R31, S3-1)")
    control(suite, "control-open-in-wiki-page-names", source,
            insert_first("wiki_page_names", "open('planted')"),
            single_reader_findings,
            lambda f: f.owner == "wiki_page_names" and f.what == "open",
            "open( at the top of wiki_page_names",
            "a second reader follows symlinks and blocks on a FIFO")


def case_regex_ascii(suite, tree, source):
    """R35, M3.  L2 is answered with the SECOND option: every pattern in
    roadmap.py -- including the one in slugify -- is compiled with re.ASCII
    and used through its compiled object.  So besides the three plan rules
    (re.ASCII on every re.compile, no ^ / $ anchor in a pattern literal, no
    .match( / .search( on a *_RE), EVERY module-level re function call except
    re.compile and re.escape is flagged, literal pattern or not."""
    _findings_case(suite, "regex-ascii", regex_findings(tree),
                   "re.compile always with re.ASCII, a str-literal pattern "
                   "without ^ or $ anchors, *_RE used only with .fullmatch(, "
                   "and no module-level re function but %s (R35, M3, L2)"
                   % ", ".join(sorted(RE_MODULE_ALLOWED)))
    control(suite, "control-date-re-match", source,
            append_to_module("def _planted_match(value):\n"
                             "    return DATE_RE.match(value)\n"),
            regex_findings,
            lambda f: f.owner == "_planted_match" and f.what == "DATE_RE.match",
            "a function calling DATE_RE.match(",
            "match accepts a prefix: 2026-09-28junk would pass")


def group_a(suite, mod, lib, source):
    tree = ast.parse(source)
    mode = suite_mode()
    case_format_contract(suite, mod)
    case_type_membership(suite, mod, lib)
    case_stdlib(suite, tree)
    case_git(suite, mod, tree, source)
    case_no_shell(suite, tree)
    case_purity(suite, tree, source, mode)
    case_io_handler(suite, tree, source, mode)
    case_single_reader(suite, tree, source)
    case_regex_ascii(suite, tree, source)


# ---------------------------------------------------------------------------
# B-L. plumbing: a missing function is a red case, a crash is one red case
# ---------------------------------------------------------------------------

def need(suite, group, cid, mod, *names):
    """Return the named module attributes, or record ONE failing case naming the
    missing ones and return None -- a missing function is a red case, never a
    crash of the suite."""
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        suite.record(group, cid, ["roadmap.py has no %s (not implemented yet)"
                                  % ", ".join(missing)])
        return None
    return [getattr(mod, n) for n in names]


def run_group(suite, group, body, *args):
    """Run one group body; an unexpected exception is ONE failing case for that
    group (the traceback's last line as the problem), and the next group still
    runs."""
    try:
        body(*args)
    except Exception:  # noqa: BLE001 -- one red case, never a crashed suite
        lines = traceback.format_exc().strip().splitlines()
        suite.record(group, "group-body-raised",
                     ["the group body raised: %s" % lines[-1]],
                     detail=lines[-8:])


Wiki = collections.namedtuple("Wiki", "lib reindex freshness server")


@contextlib.contextmanager
def patched(obj, name, value):
    """Set obj.name for the block; restored in finally, always."""
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


def in_process(fn, *args, **kwargs):
    """(exit code or None, return value, stdout, stderr) of one call."""
    with captured() as (out, err):
        try:
            value, code = fn(*args, **kwargs), None
        except SystemExit as exc:
            value, code = None, exc.code
    return code, value, out.getvalue(), err.getvalue()


def target_in(workspace, case):
    """`<case>/docs/roadmap/roadmap.md`, creating only `<case>/docs`: init into
    a bare wiki root must meet a missing roadmap/ directory."""
    workspace.subdir(case, "docs")
    return sandbox_path(os.path.join(workspace.path, case, "docs", "roadmap",
                                     "roadmap.md"))


def cli(path, *argv, **kwargs):
    return run_cli(*(list(argv) + ["--file", path]), **kwargs)


def build(workspace, case, steps=(), archive=None):
    """init `<case>`, then run every argv in `steps`.

    Returns (path, problems, stdouts); a failing step ends the setup and is
    the case's problem, so a case never judges a fixture that was not built.
    """
    path = target_in(workspace, case)
    if archive is not None:
        stage_roadmap(workspace, case, None, archive)
    problems, outs = [], []
    for argv in [("init",)] + list(steps):
        code, out, err = cli(path, *argv)
        outs.append(out)
        if code != 0:
            problems.append("setup `%s` exited %r: %s"
                            % (" ".join(argv[:3]), code, err.strip()[:300]))
            break
    return path, problems, outs


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_utf8(path):
    return read_bytes(path).decode("utf-8")


def first_diff(got, want):
    """The first differing line of two texts, for a byte-for-byte mismatch."""
    g, w = got.split("\n"), want.split("\n")
    for index in range(max(len(g), len(w))):
        a = g[index] if index < len(g) else "<EOF>"
        b = w[index] if index < len(w) else "<EOF>"
        if a != b:
            return "line %d: got %r, want %r" % (index + 1, a, b)
    return "equal"


def shape_problems(code, err, want=2):
    """The exit code, no traceback, and on a refusal exactly one stderr line."""
    problems = problem_if(code != want, "exit %r, want %r" % (code, want))
    problems += problem_if("Traceback" in err, "stderr carries a traceback")
    if want == 2:
        count = len(err.strip().splitlines())
        problems += problem_if(count != 1, "stderr is %d line(s), want exactly "
                                           "one" % count)
    return problems


def check_refusal(path, argv, tokens, absent=(), today=TODAY, timeout=30,
                  env_extra=None):
    """One refusal against an existing case: (problems, stderr).

    The same properties as refusal(), for cases whose fixture is built first:
    exit 2, one line, no traceback, the roadmap and the archive byte-unchanged,
    no `.roadmap-*` name, the tokens present and the `absent` ones not.
    `env_extra` is merged over the git isolation defaults, never substituted.
    """
    archive_dir = os.path.join(os.path.dirname(path), "archive")
    existed = os.path.lexists(path)
    before = read_bytes(path) if os.path.isfile(path) else None
    archive_before = H.file_digests(archive_dir)
    code, _out, err = cli(path, *argv, today=today, timeout=timeout,
                          env_extra=env_extra)
    problems = shape_problems(code, err)
    if before is not None:
        problems += problem_if(read_bytes(path) != before,
                               "the roadmap was modified anyway")
    elif not existed:
        problems += problem_if(os.path.lexists(path),
                               "a missing roadmap was CREATED")
    problems += problem_if(H.file_digests(archive_dir) != archive_before,
                           "the archive changed")
    leftovers = temp_leftovers(path) + temp_leftovers(
        os.path.join(archive_dir, "x"))
    problems += problem_if(leftovers, "temp name left behind: %r" % leftovers)
    problems += ["the diagnostic omits %r" % t
                 for t in missing_tokens(err, tokens)]
    problems += ["the diagnostic carries %r, so the wrong gate fired" % t
                 for t in absent if t in err]
    return problems, err


def record_refusal(suite, group, cid, path, argv, tokens, why, absent=(),
                   today=TODAY, extra=(), timeout=30, after=None,
                   env_extra=None):
    """`extra`: problems found BEFORE the run (a fixture that did not build).
    `after`: a callable judged AFTER the run -- anything about what the run
    may have touched must go here, because an argument is evaluated before
    the child starts."""
    problems, err = check_refusal(path, argv, tokens, absent, today, timeout,
                                  env_extra)
    if after is not None:
        problems += after()
    suite.record(group, cid, list(extra) + problems,
                 detail=[_d("why", why), _d("stderr", err.strip() or "<empty>")])


def staged(workspace, case, name, body):
    """A staged input file under `<case>/stage/`, never inside the roadmap."""
    workspace.subdir(case, "stage")
    return write_file(os.path.join(workspace.path, case, "stage", name), body)


def names_under(root, prefix):
    """Every file or directory under `root` whose name starts with `prefix`."""
    found = []
    for _dirpath, dirs, files in os.walk(root):
        found += [n for n in dirs + files if n.startswith(prefix)]
    return sorted(found)


def can_symlink():
    return hasattr(os, "symlink")


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def git_init(path, extra=()):
    """`git init` a sandbox directory; True, or False when git is unusable."""
    sandbox_path(path)
    try:
        proc = subprocess.run(["git", "-c", "init.defaultBranch=main"]
                              + list(extra) + ["init", "-q", path],
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=30, env=H.child_env(git_isolation()))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def info_skip(suite, group, cid, why):
    suite.record(group, cid, status=H.INFO, detail=["skipped: %s" % why])


# ---------------------------------------------------------------------------
# B-L. fixtures: the on-disk format, written out by hand
# ---------------------------------------------------------------------------

DESCRIPTION = ("Known-but-unscheduled work by horizon (now, next, later, "
               "inbox), written only by roadmap.py; closed items are archived "
               "one file each under roadmap/archive/.")
WIP_NOTE = ("# wip_now is an unargued starting value (adr 0022, after adr "
            "0013) -- change it only with roadmap.py wip N --reason TEXT")
FRONT_HEAD = ("---\nname: roadmap\ntype: roadmap\nstatus: active\n"
              "title: Roadmap\ndescription: %s\n" % DESCRIPTION)
ROADMAP_FM = {"name": "roadmap", "type": "roadmap", "status": "active",
              "title": "Roadmap", "description": DESCRIPTION, "wip_now": "3"}
DOT = chr(0xB7)   # U+00B7 MIDDLE DOT: every non-ASCII character here is a code point
DISPLAY = "docs/roadmap/roadmap.md"

# What `init` writes: the empty roadmap, every byte.
EMPTY_TEXT = FRONT_HEAD + "\n".join((
    WIP_NOTE,
    "wip_now: 3",
    "---",
    "",
    BEGIN_PREFIX + " -- generated by roadmap.py; do not edit by hand -->",
    "WIP now: 0 of 3. Archive: 0 closed items (0 done, 0 dropped).",
    "",
    "| Lane | Id | State | Title | Ready |",
    "|------|----|-------|-------|-------|",
    END_PREFIX + " -->",
    "",
    "# now",
    "",
    "# next",
    "",
    "# later",
    "",
    "# inbox",
    ""))

WHY_1 = ("The skill-side git helper passes stdin=DEVNULL but has no timeout; "
         "the server copy has both.")
WHY_2 = ("Two copies of one helper drift; ADR 0012 declined a shared "
         "transport tier, not this helper.")
_ROW = "| %-5s | %-6s | %-7s | %-40s | %-5s |"

# Group B's round trip builds this through the CLI; every other group stages it
# as its base fixture.  The section 5.2 example shape, minus the archive.
EXPECTED_B = FRONT_HEAD + "\n".join((
    "# wip_now set to 4 on 2026-09-29 (was 3): two sessions in parallel",
    "wip_now: 4",
    "---",
    "",
    BEGIN_PREFIX + " -- generated by roadmap.py; do not edit by hand -->",
    "WIP now: 1 of 4. Archive: 0 closed items (0 done, 0 dropped).",
    "",
    _ROW % ("Lane", "Id", "State", "Title", "Ready"),
    "|" + "|".join("-" * n for n in (7, 8, 9, 42, 7)) + "|",
    _ROW % ("now", "R-0001", "active",
            "Give _wikilib.git() the server's timeout", "yes"),
    _ROW % ("next", "R-0004", "idea", "Pin the export golden", "yes"),
    _ROW % ("next", "R-0002", "planned",
            "Unify the skill and server git helpers", "no"),
    _ROW % ("inbox", "R-0003", "idea",
            "ADR authoring proposes deferred limits", "yes"),
    END_PREFIX + " -->",
    "",
    "# now",
    "",
    "## R-0001 %s Give _wikilib.git() the server's timeout" % DOT,
    "",
    "state: active",
    "horizon: now",
    "origin: tests/test_spawn_stdin.py#skills-survey",
    "blocked_by: []",
    "tags: [wiki]",
    "",
    WHY_1,
    "",
    "### Log",
    "",
    "- 2026-09-28 new->later: harvested by adopt",
    "- 2026-09-29 later->now [idea->active]: picked up",
    "",
    "# next",
    "",
    "## R-0004 %s Pin the export golden" % DOT,
    "",
    "state: idea",
    "horizon: next",
    "origin: user:2026-09-28:export-golden",
    "blocked_by: []",
    "tags: []",
    "",
    "### Log",
    "",
    "- 2026-09-28 new->next: added",
    "",
    "## R-0002 %s Unify the skill and server git helpers" % DOT,
    "",
    "state: planned",
    "horizon: next",
    "origin: docs/adr/0012-the-transport-tier-diverged.md#consequences",
    "blocked_by: [R-0001]",
    "severity: low",
    "tags: [scripts, wiki]",
    "",
    WHY_2,
    "",
    "### Log",
    "",
    "- 2026-09-28 new->next [idea->planned]: harvested by adopt",
    "",
    "# later",
    "",
    "# inbox",
    "",
    "## R-0003 %s ADR authoring proposes deferred limits" % DOT,
    "",
    "state: idea",
    "horizon: unset",
    "origin: user:2026-09-28:adr-producer",
    "blocked_by: []",
    "tags: [adr, producer]",
    "",
    "### Log",
    "",
    "- 2026-09-28 new->unset: added",
    ""))

B_STEPS = (
    ("add", "--title", "Give _wikilib.git() the server's timeout",
     "--origin", "tests/test_spawn_stdin.py#skills-survey",
     "--horizon", "later", "--tags", "wiki", "--why", WHY_1,
     "--reason", "harvested by adopt"),
    ("add", "--title", "Unify the skill and server git helpers",
     "--origin", "docs/adr/0012-the-transport-tier-diverged.md#consequences",
     "--horizon", "next", "--state", "planned", "--severity", "low",
     "--tags", "wiki", "--tags", "scripts", "--why", WHY_2,
     "--reason", "harvested by adopt"),
    ("add", "--title", "ADR authoring proposes deferred limits",
     "--origin", "user:2026-09-28:adr-producer", "--tags", "producer,adr"),
    ("add", "--title", "Pin the export golden",
     "--origin", "user:2026-09-28:export-golden", "--horizon", "next"),
    ("move", "R-0001", "now", "--state", "active", "--reason", "picked up",
     "--today", "2026-09-29"),
    ("rank", "R-0004", "--top"),
    ("link", "R-0002", "--blocked-by", "R-0001"),
    ("wip", "4", "--reason", "two sessions in parallel",
     "--today", "2026-09-29"),
)
B_EMITS = ("R-0001", "R-0002", "R-0003", "R-0004",
           "R-0001: later->now [idea->active]",
           "R-0004: position 1 of 2 in next",
           "R-0002: linked (blocked_by)",
           "wip_now: 3 -> 4")

SHA_40 = "0123456789abcdef0123456789abcdef01234567"
ARCHIVE_1_NAME = "0001-home-the-wiki-page-type-constants-in-wikilib.md"
ARCHIVE_1 = "\n".join((
    "---",
    "name: 0001-home-the-wiki-page-type-constants-in-wikilib",
    "type: roadmap-item",
    "status: active",
    "title: R-0001 %s Home the wiki page-type constants in _wikilib" % DOT,
    "description: Closed roadmap item R-0001 (done 2026-09-27).",
    "id: R-0001",
    "state: done",
    "horizon: now",
    "origin: user:2026-09-20:wiki-constants-home",
    "blocked_by: []",
    "tags: [wiki]",
    "closed: 2026-09-27",
    "commit: %s" % SHA_40,
    "---",
    "",
    "# R-0001 %s Home the wiki page-type constants in _wikilib" % DOT,
    "",
    "## Why",
    "",
    "Four page-type constants were typed in three files.",
    "",
    "## Log",
    "",
    "- 2026-09-20 new->next [idea->planned]: harvested by adopt",
    "- 2026-09-22 next->now [planned->active]: started",
    "- 2026-09-27 now->done: commit %s" % SHA_40,
    ""))
ARCHIVE_2_NAME = "0002-p0-p3-priority-labels.md"
ARCHIVE_2 = "\n".join((
    "---",
    "name: 0002-p0-p3-priority-labels",
    "type: roadmap-item",
    "status: active",
    "title: R-0002 %s P0-P3 priority labels" % DOT,
    "description: Closed roadmap item R-0002 (dropped 2026-09-28).",
    "id: R-0002",
    "state: dropped",
    "horizon: later",
    "origin: user:2026-09-21:priority-labels",
    "blocked_by: []",
    "tags: []",
    "closed: 2026-09-28",
    "reason: a second priority axis (adr 0022)",
    "---",
    "",
    "# R-0002 %s P0-P3 priority labels" % DOT,
    "",
    "## Why",
    "",
    "Proposed as a finer priority axis inside a lane.",
    "",
    "## Log",
    "",
    "- 2026-09-21 new->later: added",
    "- 2026-09-28 later->dropped: a second priority axis (adr 0022)",
    ""))
BOTH_ARCHIVES = {ARCHIVE_1_NAME: ARCHIVE_1, ARCHIVE_2_NAME: ARCHIVE_2}


def mutated(text, change):
    """(the mutated text, a problem or None): `change` is (old, new) replaced
    once, or a function.  A mutation that changes nothing is a fixture bug."""
    if callable(change):
        new = change(text)
    else:
        new = text.replace(change[0], change[1], 1)
    return new, (None if new != text else "the fixture mutation did not apply")


# ---------------------------------------------------------------------------
# B. round trip, idempotence, byte preservation
# ---------------------------------------------------------------------------

def group_b(suite, mod, work, wiki):
    path, problems, outs = build(work, "b-golden", B_STEPS)
    got = read_utf8(path) if os.path.isfile(path) else ""
    if not problems:
        problems += problem_if(got != EXPECTED_B,
                               "the file differs from the hand-written "
                               "expected file: %s" % first_diff(got, EXPECTED_B))
        problems += problem_if(outs[0].strip() != "initialized: %s" % path,
                               "init printed %r" % outs[0].strip())
        emits = tuple(o.strip() for o in outs[1:])
        problems += problem_if(emits != B_EMITS, "stdout lines %r, want %r"
                               % (emits, B_EMITS))
    suite.record(GB, "b-round-trip-golden", problems,
                 detail=[_d("steps", "init, add x4 across lanes, move, rank, "
                                     "link, wip -- all with --today"),
                         _d("oracle", "a hand-written expected file, byte for "
                                      "byte")])

    path, problems, _outs = build(work, "b-init-golden")
    if not problems:
        got = read_utf8(path)
        problems += problem_if(got != EMPTY_TEXT, "init wrote something else: "
                               "%s" % first_diff(got, EMPTY_TEXT))
    suite.record(GB, "b-init-golden", problems,
                 detail=[_d("oracle", "the empty roadmap, every byte")])

    got = need(suite, GB, "b-parse-render-identity", mod, "load_state",
               "parse_roadmap", "render_roadmap")
    if got is not None:
        load_state, parse_roadmap, render_roadmap = got
        path = stage_roadmap(work, "b-identity", EXPECTED_B)
        code, state, _out, err = in_process(load_state, path)
        problems = problem_if(code is not None, "load_state refused: %s"
                              % err.strip())
        if code is None:
            text = render_roadmap(state)
            problems += problem_if(text != EXPECTED_B,
                                   "render(parse(t)) != t: %s"
                                   % first_diff(text, EXPECTED_B))
            display = state.lanes["now"][0].path
            code, parsed, _out, err = in_process(parse_roadmap, text, display)
            problems += problem_if(code is not None or parsed != (state.meta,
                                                                  state.lanes),
                                   "parse(render(model)) != model (%s)"
                                   % (err.strip() or "models differ"))
        suite.record(GB, "b-parse-render-identity", problems,
                     detail=[_d("oracle", "both directions on the canonical "
                                          "fixture")])

    stale = EXPECTED_B.replace(
        "WIP now: 1 of 4. Archive: 0 closed items (0 done, 0 dropped).",
        "WIP now: stale.").replace(
        _ROW % ("inbox", "R-0003", "idea",
                "ADR authoring proposes deferred limits", "yes") + "\n", "")
    path = stage_roadmap(work, "b-render-preserves", stale)
    code, out, err = cli(path, "render")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "rendered: %s" % path,
                           "stdout %r" % out.strip())
    got = read_utf8(path)
    problems += problem_if(got != EXPECTED_B, "render did not regenerate "
                           "exactly the region: %s" % first_diff(got, EXPECTED_B))
    suite.record(GB, "b-render-preserves-bytes", problems,
                 detail=[_d("planted", "a stale summary region"),
                         _d("oracle", "the region regenerated, every byte "
                                      "outside it identical")])

    path = stage_roadmap(work, "b-render-noop", EXPECTED_B)
    before_ns = os.stat(path).st_mtime_ns
    code, out, err = cli(path, "render")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "unchanged: %s" % path,
                           "stdout %r" % out.strip())
    problems += problem_if(os.stat(path).st_mtime_ns != before_ns,
                           "the mtime moved: the file was rewritten")
    problems += problem_if(read_utf8(path) != EXPECTED_B, "the bytes changed")
    suite.record(GB, "b-second-render-is-a-noop", problems,
                 detail=[_d("oracle", "unchanged: on stdout, the mtime to the "
                                      "nanosecond")])

    work.subdir("b-bare", "docs")
    write_file(os.path.join(work.path, "b-bare", "docs", "overview.md"),
               "---\nname: overview\ntype: overview\n---\n\n# Overview\n")
    path = sandbox_path(os.path.join(work.path, "b-bare", "docs", "roadmap",
                                     "roadmap.md"))
    code, out, err = cli(path, "init")
    problems = shape_problems(code, err, 0)
    roadmap_dir = os.path.dirname(path)
    for label, what in (("roadmap/", os.path.isdir(roadmap_dir)),
                        ("roadmap/archive/",
                         os.path.isdir(os.path.join(roadmap_dir, "archive"))),
                        ("roadmap.md", os.path.isfile(path))):
        problems += problem_if(not what, "%s was not created" % label)
    problems += problem_if(temp_leftovers(path), "temp name left in roadmap/: "
                           "%r" % temp_leftovers(path))
    suite.record(GB, "b-init-bare-wiki-root", problems,
                 detail=[_d("fixture", "<case>/docs holds only overview.md"),
                         _d("why", "H1, R30: write_atomic's mkstemp needs "
                                   "roadmap/ to exist first")])

    work.subdir("b-crash", "docs", "roadmap", "archive")
    path = sandbox_path(os.path.join(work.path, "b-crash", "docs", "roadmap",
                                     "roadmap.md"))
    code, out, err = cli(path, "init")
    problems = shape_problems(code, err, 0)
    problems += problem_if(not os.path.isfile(path) or read_utf8(path)
                           != EMPTY_TEXT, "the re-run did not write the empty "
                                          "roadmap")
    suite.record(GB, "b-init-accepts-crash-state", problems,
                 detail=[_d("fixture", "empty roadmap/archive/, no roadmap.md: "
                                       "an init interrupted after step 3")])


# ---------------------------------------------------------------------------
# C. wiki-parser compatibility + the line-structure rule
# ---------------------------------------------------------------------------

# (title, origin, reason, tags): every character class check_line and
# check_scalar admit that the wiki parser could read differently.
C_CORPUS = (
    ("Colon: a title", "docs/x.md#colon-a-title", "why: because", ""),
    ("Hash # inside", "user:2026-09-28:hash # inside", "see #12", "wiki"),
    ("Pipe | inside", "user:2026-09-28:pipe|inside", "a | b", ""),
    ('Inner "quotes" here', "user:2026-09-28:'quoted' origin", 'said "no"',
     ""),
    ("Inner [brackets] here", "user:2026-09-28:[bracketed]-x", "[x] y", ""),
    ("Non-ASCII %s %s %s %s" % (chr(0xE9), chr(0xFC), chr(0x151),
                                 chr(0x6F22) + chr(0x5B57)),
     "user:2026-09-28:non-ascii-" + chr(0xE9),
     chr(0xFC) + "n" + chr(0xEF) + "code", ""),
    ("Middle %s dot %s twice" % (DOT, DOT), "user:2026-09-28:middle-dot",
     "a %s b" % DOT, "wiki,adr"),
)
C_WHY = ('Why with: a colon, # a hash, | a pipe, "quotes", [brackets], %s.'
         % chr(0xE9))

# The separators, spelled as code points, never as raw characters (S-M1,
# R26): newline, CR, VT, FF, FS, GS, RS, NEL, LINE SEPARATOR, PARAGRAPH
# SEPARATOR.
LINE_SEP = chr(0x2028)
PARA_SEP = chr(0x2029)
SEPARATORS = ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85",
              LINE_SEP, PARA_SEP)


def _archive_expected(index, state, reason):
    title, origin, _reason, tags = C_CORPUS[index]
    n = index + 1
    want = {"name": None, "type": "roadmap-item", "status": "active",
            "title": "R-%04d %s %s" % (n, DOT, title),
            "description": "Closed roadmap item R-%04d (%s 2026-09-30)."
                           % (n, state),
            "id": "R-%04d" % n, "state": state, "horizon": "unset",
            "origin": origin, "blocked_by": [],
            "tags": sorted(t for t in tags.split(",") if t),
            "closed": "2026-09-30"}
    if state == "done":
        want["commit"] = SHA_40
    else:
        want["reason"] = reason
    return want


def group_c(suite, mod, work, wiki):
    steps = []
    for index, (title, origin, reason, tags) in enumerate(C_CORPUS):
        argv = ["add", "--title", title, "--origin", origin, "--reason", reason]
        if tags:
            argv += ["--tags", tags]
        if index == 0:
            argv += ["--why", C_WHY]
        steps.append(tuple(argv))
    path, problems, _outs = build(work, "c-corpus", steps)
    root = os.path.join(work.path, "c-corpus", "docs")
    if not problems:
        text = read_utf8(path)
        for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                              ("server", wiki.server.parse_frontmatter)):
            got = parser(text)
            problems += problem_if(got != ROADMAP_FM, "%s reads roadmap.md's "
                                   "frontmatter as %r" % (label, got))
    suite.record(GC, "c-roadmap-frontmatter-both-parsers", problems,
                 detail=[_d("corpus", "%d items whose titles, origins and "
                                      "reasons carry : # | quotes brackets "
                                      "non-ASCII and U+00B7" % len(C_CORPUS)),
                         _d("oracle", "_wikilib and the server "
                                      "parse_frontmatter, exact dicts")])
    corpus_ok = not problems

    got = need(suite, GC, "c-archive-both-parsers", mod, "load_state",
               "render_archive", "parse_archive", "slugify", "LogEntry",
               "Closed")
    if got is not None and corpus_ok:
        load_state, render_archive, parse_archive, slugify, LogEntry, Closed \
            = got
        code, state, _out, err = in_process(load_state, path)
        problems = problem_if(code is not None, "load_state refused: %s"
                              % err.strip())
        archive_dir = sandbox_path(work.subdir("c-corpus", "docs", "roadmap",
                                               "archive"))
        items = state.lanes["unset"] if code is None else []
        problems += problem_if(len(items) != len(C_CORPUS),
                               "%d inbox items, want %d"
                               % (len(items), len(C_CORPUS)))
        for index, item in enumerate(items):
            done = index % 2 == 0
            closed_state = "done" if done else "dropped"
            reason = C_CORPUS[index][2]
            closing = LogEntry("2026-09-30", item.horizon, closed_state, None,
                               None, "commit %s" % SHA_40 if done else reason)
            archived = item._replace(
                state=closed_state, log=item.log + (closing,),
                closed=Closed("2026-09-30", SHA_40 if done else None,
                              None if done else reason))
            stem = "%04d-%s" % (item.id, slugify(item.title))
            text = render_archive(archived, stem)
            write_file(os.path.join(archive_dir, stem + ".md"), text)
            want = _archive_expected(index, closed_state, reason)
            want["name"] = stem
            for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                                  ("server", wiki.server.parse_frontmatter)):
                read = parser(text)
                problems += problem_if(read != want, "%s reads %s as %r, want "
                                       "%r" % (label, stem, read, want))
            code, parsed, _out, err = in_process(parse_archive, text, stem)
            problems += problem_if(
                code is not None or parsed is None
                or parsed._replace(path=None) != archived._replace(path=None),
                "parse_archive(render_archive(item)) != item for %s (%s)"
                % (stem, err.strip() or "models differ"))
            problems += problem_if("`" in text, "%s carries a backtick" % stem)
        suite.record(GC, "c-archive-both-parsers", problems,
                     detail=[_d("oracle", "exact dicts from both parsers, "
                                          "blocked_by: [] -> [], and "
                                          "parse_archive(render_archive(x)) "
                                          "== x"),
                             _d("rendered", "in-process by render_archive "
                                            "(M2): close is Step 6")])

        problems = []
        for label, collect in (("reindex.collect", wiki.reindex.collect),
                               ("server reindex_collect",
                                wiki.server.reindex_collect)):
            _entries, dups, orphans, malformed = collect(root)
            problems += problem_if(malformed, "%s: malformed %r"
                                   % (label, malformed))
            problems += problem_if(dups, "%s: dups %r" % (label, dups))
            stray = [o["path"] for o in orphans if o["type"] == "roadmap-item"]
            problems += problem_if(stray, "%s: roadmap-item orphans %r"
                                   % (label, stray))
        report = wiki.freshness.analyze(root, "HEAD")
        tracked = [(p["path"], p["status"]) for p in report["pages"]
                   if p["status"] != "untracked"]
        problems += problem_if(tracked, "freshness reports %r, want untracked "
                               "for every page" % tracked)
        gating = wiki.server.verify_analyze(root).get("gating")
        problems += problem_if(gating, "verify_analyze gating %r" % gating)
        suite.record(GC, "c-collect-freshness-verify", problems,
                     detail=[_d("oracle", "zero malformed, zero dups, no "
                                          "roadmap-item orphan in both "
                                          "collects; freshness untracked; "
                                          "verify gating == []")])

    base = stage_roadmap(work, "c-controls", EXPECTED_B)
    for cid, title, tokens in (
            ("c-control-quoted-title-refused", '"quoted"',
             ["title value", "wrapped in matching quotes"]),
            ("c-control-bracket-title-refused", "[x]",
             ["title value", "wrapped in brackets"]),
            ("c-control-two-line-title-refused", "a\nb",
             ["title value", "line separator (U+000A)"])):
        record_refusal(suite, GC, cid, base,
                       ["add", "--title", title, "--origin", "user:c-" + cid],
                       tokens, "a value the wiki parser would read otherwise")
    planted_text = FRONT_HEAD.replace("title: Roadmap", 'title: "quoted"')
    planted_text += "---\n"
    problems = []
    for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                          ("server", wiki.server.parse_frontmatter)):
        read = parser(planted_text).get("title")
        problems += problem_if(read == '"quoted"', "%s read %r verbatim: the "
                               "oracle cannot tell a misparse" % (label, read))
    suite.record(GC, "c-control-oracle-fires", problems,
                 detail=[_d("planted", "a hand-written title: \"quoted\""),
                         _d("why", "the parsers strip the quotes, so the "
                                   "differential oracle does fire")])

    got = need(suite, GC, "c-stripped-scalars-round-trip", mod, "load_state",
               "render_archive", "parse_archive", "Closed", "LogEntry")
    if got is not None:
        load_state, render_archive, parse_archive, Closed, LogEntry = got
        path, problems, _outs = build(work, "c-strip", [
            ("add", "--title", "  x  ", "--origin",
             "  user:2026-09-28:strip  ")])
        if not problems:
            text = read_utf8(path)
            problems += problem_if("\n## R-0001 %s x\n" % DOT not in text,
                                   "the heading is not '## R-0001 %s x'" % DOT)
            problems += problem_if("\norigin: user:2026-09-28:strip\n"
                                   not in text, "the origin was not stripped")
            code, state, _out, err = in_process(load_state, path)
            item = state.lanes["unset"][0] if code is None else None
            problems += problem_if(item is None or item.title != "x"
                                   or item.origin != "user:2026-09-28:strip",
                                   "parse_roadmap re-reads %r"
                                   % ((item.title, item.origin) if item
                                      else err.strip(),))
            if item is not None:
                archived = item._replace(
                    state="dropped", closed=Closed("2026-09-30", None, "gone"),
                    log=item.log + (LogEntry("2026-09-30", "unset", "dropped",
                                             None, None, "gone"),))
                atext = render_archive(archived, "0001-x")
                problems += problem_if("title: R-0001 %s x\n" % DOT not in atext
                                       or "\n# R-0001 %s x\n" % DOT
                                       not in atext,
                                       "the archive does not carry the "
                                       "stripped title")
                for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                                      ("server",
                                       wiki.server.parse_frontmatter)):
                    read = parser(atext)
                    problems += problem_if(
                        read.get("title") != "R-0001 %s x" % DOT
                        or read.get("origin") != "user:2026-09-28:strip",
                        "%s reads %r" % (label, read))
                code, parsed, _out, err = in_process(parse_archive, atext,
                                                     "0001-x")
                problems += problem_if(code is not None or parsed.title != "x"
                                       or parsed.origin
                                       != "user:2026-09-28:strip",
                                       "parse_archive re-reads %s"
                                       % (err.strip() or repr(parsed)))
        suite.record(GC, "c-stripped-scalars-round-trip", problems,
                     detail=[_d("why", "M2 round 5: every caller stores "
                                       "check_scalar's RETURN value")])

    problems = ["%r splits into %d line(s) on this Python; the list and the "
                "rule disagree" % (ch, len(("a" + ch + "a").splitlines()))
                for ch in SEPARATORS if len(("a" + ch + "a").splitlines()) == 1]
    suite.record(GC, "c-separator-list-is-splitlines", problems,
                 detail=[_d("list", " ".join("U+%04X" % ord(c)
                                             for c in SEPARATORS))])

    for ch in SEPARATORS:
        cid = "c-separator-U+%04X" % ord(ch)
        path = stage_roadmap(work, cid, EXPECTED_B)
        problems = []
        errs = []
        for field, argv in (
                ("title", ["add", "--title", "a%sb" % ch,
                           "--origin", "user:" + cid]),
                ("reason", ["move", "R-0003", "next", "--reason", "a%sb" % ch]),
                ("reason", ["wip", "5", "--reason", "a%sb" % ch])):
            got_problems, err = check_refusal(
                path, argv, ["%s value" % field,
                             "carries a line separator (U+%04X)" % ord(ch)])
            problems += ["%s: %s" % (argv[0], p) for p in got_problems]
            errs.append(err.strip())
        suite.record(GC, cid, problems,
                     detail=[_d("routes", "add --title, move --reason, wip "
                                          "--reason"),
                             _d("stderr", errs[0] or "<empty>")])

    path = stage_roadmap(work, "c-forged-key", EXPECTED_B)
    reason_file = staged(work, "c-forged-key", "reason.txt",
                         "ok\nstatus: deprecated\n")
    problems, err = check_refusal(path, ["wip", "5", "--reason-file",
                                         reason_file],
                                  ["reason value", "line separator (U+000A)"])
    text = read_utf8(path)
    for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                          ("server", wiki.server.parse_frontmatter)):
        problems += problem_if(parser(text).get("status") != "active",
                               "%s no longer reads status: active" % label)
    suite.record(GC, "c-forged-key-wip-reason-file", problems,
                 detail=[_d("attempt", "a reason file holding 'ok\\nstatus: "
                                       "deprecated'"),
                         _d("stderr", err.strip())])

    path = stage_roadmap(work, "c-forged-heading", EXPECTED_B)
    record_refusal(suite, GC, "c-forged-heading-title", path,
                   ["add", "--title", "x\n## R-0099 %s forged" % DOT,
                    "--origin", "user:c-forged-heading"],
                   ["title value", "line separator (U+000A)"],
                   "a title that would open a second item")

    for code_point in (0x00, 0x1b, 0x09):
        cid = "c-c0-U+%04X-title" % code_point
        path = stage_roadmap(work, cid, EXPECTED_B)
        item_file = staged(work, cid, "item.json", json.dumps(
            {"title": "a%sb" % chr(code_point), "origin": "user:" + cid}))
        record_refusal(suite, GC, cid, path, ["add", "--item-file", item_file],
                       ["title value", "control character (U+%04X)"
                        % code_point],
                       "a C0 control in a single-line value (via an item "
                       "file: argv cannot carry NUL)")
    path = stage_roadmap(work, "c-middle-dot-accepted", EXPECTED_B)
    code, out, err = cli(path, "add", "--title", "a %s b" % DOT,
                         "--origin", "user:c-middle-dot")
    suite.record(GC, "c-middle-dot-title-accepted", shape_problems(code, err, 0),
                 detail=[_d("why", "U+00B7 is not a control, and headings "
                                   "carry it anyway")])

    path = stage_roadmap(work, "c-backtick", EXPECTED_B)
    for cid, argv, field in (
            ("c-backtick-title", ["add", "--title", "a `b` c",
                                  "--origin", "user:c-bt-title"], "title"),
            ("c-backtick-origin", ["add", "--title", "t",
                                   "--origin", "user:`x`"], "origin"),
            ("c-backtick-move-reason", ["move", "R-0003", "next",
                                        "--reason", "see `x`"], "reason")):
        record_refusal(suite, GC, cid, path, argv,
                       ["%s value" % field, "carries a backtick"],
                       "generated text is backtick-free (M6)")
    code, out, err = cli(path, "add", "--title", "t", "--origin",
                         "user:c-bt-why", "--why", "see `docs/x.md` here")
    suite.record(GC, "c-backtick-why-accepted", shape_problems(code, err, 0),
                 detail=[_d("why", "user why prose is the one place a "
                                   "backticked anchor may live")])

    for field, extra in (("origin", ["--title", "t", "--origin"]),
                         ("spec", ["--title", "t", "--origin", "user:c-spec",
                                   "--spec"]),
                         ("severity", ["--title", "t", "--origin",
                                       "user:c-sev", "--severity"]),
                         ("tag", ["--title", "t", "--origin", "user:c-tag",
                                  "--tags"])):
        cid = "c-separator-reaches-%s" % field
        path = stage_roadmap(work, cid, EXPECTED_B)
        record_refusal(suite, GC, cid, path,
                       ["add"] + extra + ["a" + LINE_SEP + "b"],
                       ["%s value" % field, "line separator (U+2028)"],
                       "every single-line value goes through check_line")

    for cid, body, tokens in (
            ("c-surrogate-title-item-file",
             '{"title": "a\\ud800b", "origin": "user:c-sur"}',
             ["title value", "lone surrogate (U+D800)"]),
            ("c-surrogate-why-item-file",
             '{"title": "t", "origin": "user:c-sur", "why": "a\\udfffb"}',
             ["the why text carries a lone surrogate (U+DFFF)"])):
        path = stage_roadmap(work, cid, EXPECTED_B)
        item_file = staged(work, cid, "item.json", body)
        record_refusal(suite, GC, cid, path, ["add", "--item-file", item_file],
                       tokens, "json.loads turns the escape into a lone "
                               "surrogate (M4)")
    got = need(suite, GC, "c-surrogate-in-process", mod, "check_line")
    if got is not None:
        code, _value, _out, err = in_process(got[0], "title",
                                             "a" + chr(0xDC80) + "b")
        problems = problem_if(code != 2, "exit %r, want 2" % code)
        problems += missing_tokens(err, ["title value",
                                         "lone surrogate (U+DC80)"])
        suite.record(GC, "c-surrogate-in-process", problems,
                     detail=[_d("shape", "what argv decoding makes of a "
                                         "non-UTF-8 byte")])

    path = stage_roadmap(work, "c-why-separators", EXPECTED_B)
    problems = []
    for ch in SEPARATORS[1:]:
        got_problems, _err = check_refusal(
            path, ["add", "--title", "t", "--origin", "user:c-why",
                   "--why", "a%sb" % ch],
            ["the why text carries a line separator other than a newline "
             "(U+%04X)" % ord(ch)], absent=["internal:"])
        problems += ["U+%04X: %s" % (ord(ch), p) for p in got_problems]
    suite.record(GC, "c-why-separators", problems,
                 detail=[_d("rule", "every separator but \\n is refused, \\r "
                                    "as a user refusal (L3)")])

    path = stage_roadmap(work, "c-why-blank-lines", EXPECTED_B)
    code, out, err = cli(path, "add", "--title", "t", "--origin",
                         "user:c-why-blank", "--why", "\n\nbody line\n\n\n")
    problems = shape_problems(code, err, 0)
    text = read_utf8(path)
    problems += problem_if("tags: []\n\nbody line\n\n### Log" not in text,
                           "the why was not stored stripped")
    code, out, err = cli(path, "show", "R-0005")
    problems += shape_problems(code, err, 0)
    problems += problem_if("tags: []\n\nbody line\n\n### Log" not in out,
                           "show does not return the stripped why")
    suite.record(GC, "c-why-blank-lines-stripped", problems,
                 detail=[_d("why", "L3: leading and trailing blank lines "
                                   "are dropped")])

    got = need(suite, GC, "c-control-line-break-planted", mod, "_line_break",
               "load_state", "render_roadmap", "parse_roadmap")
    if got is not None:
        ctl = H.load_module_from_path("roadmap_planted_line_break", TARGET)
        real_break = ctl._line_break
        ctl._line_break = lambda ch: ch != LINE_SEP and real_break(ch)
        title = "a" + LINE_SEP + "b"
        code, _v, _out, err = in_process(ctl.check_line, "title", title)
        problems = problem_if(code is not None, "the planted check_line still "
                              "refused: the plant did not take (%s)"
                              % err.strip())
        path = stage_roadmap(work, "c-planted-break", EMPTY_TEXT)
        code, state, _out, err = in_process(ctl.load_state, path)
        item = ctl.Item(1, title, "idea", "unset", "user:c-planted", None, (),
                        None, None, (), "",
                        (ctl.LogEntry("2026-09-28", None, "unset", None, None,
                                      "added"),), None, DISPLAY)
        lanes = dict(state.lanes)
        lanes["unset"] = [item]
        text = ctl.render_roadmap(state._replace(lanes=lanes))
        code, parsed, _out, err = in_process(ctl.parse_roadmap, text, DISPLAY)
        reread = (code is not None
                  or parsed[1]["unset"][0].title != title)
        problems += problem_if(not reread, "the strict re-read did NOT detect "
                               "the split line")
        archived = item._replace(
            state="dropped", closed=ctl.Closed("2026-09-30", None, "x"),
            log=item.log + (ctl.LogEntry("2026-09-30", "unset", "dropped",
                                         None, None, "x"),))
        atext = ctl.render_archive(archived, "0001-ab")
        for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                              ("server", wiki.server.parse_frontmatter)):
            problems += problem_if(parser(atext).get("title")
                                   == "R-0001 %s " % DOT + title,
                                   "%s read the U+2028 title intact: the "
                                   "oracle did not fire" % label)
        suite.record(GC, "c-control-line-break-planted", problems,
                     detail=[_d("planted", "_line_break returns False for "
                                           "U+2028"),
                             _d("oracle", "both wiki parsers and the strict "
                                          "re-read detect the split line")])


# ---------------------------------------------------------------------------
# D. invariants, WIP, move, rank, log, wip
# ---------------------------------------------------------------------------

def _heading_order(text):
    return [line.split(" ", 2)[1] for line in text.split("\n")
            if line.startswith("## R-")]


def group_d(suite, mod, work, wiki):
    path = stage_roadmap(work, "d-refusals", EXPECTED_B)
    for cid, argv, tokens, why in (
            ("d-invariant-1-add", ["add", "--title", "t", "--origin",
                                   "user:d-1", "--state", "planned"],
             ["an untriaged item must be an idea (invariant 1)",
              "R-0005 would be planned in the inbox"],
             "unset + planned"),
            ("d-invariant-2-add", ["add", "--title", "t", "--origin",
                                   "user:d-2", "--horizon", "next",
                                   "--state", "active"],
             ["an active item must be in now (invariant 2)",
              "R-0005 would be active in next"], "active outside now"),
            ("d-move-without-reason", ["move", "R-0003", "next"],
             ["a lane or state change needs --reason or --reason-file"],
             "every move is logged with a reason"),
            ("d-move-noop", ["move", "R-0004", "next", "--reason", "x"],
             ["R-0004 is already in next as idea -- nothing to move"],
             "a true no-op move"),
            ("d-rank-across-lanes", ["rank", "R-0004", "--before", "R-0001"],
             ["R-0004 (next) and R-0001 (now) are in different lanes -- rank "
              "orders within a lane"], "rank is within a lane"),
            ("d-rank-before-itself", ["rank", "R-0004", "--before", "R-0004"],
             ["an item cannot be ranked before itself"], "--before itself"),
            ("d-wip-noop", ["wip", "4", "--reason", "x"],
             ["wip_now is already 4 -- nothing to change"], "a no-op"),
            ("d-wip-zero", ["wip", "0", "--reason", "x"],
             ["wip needs a positive integer, got '0'"], "zero"),
            ("d-wip-negative", ["wip", "-2", "--reason", "x"],
             ["wip needs a positive integer, got '-2'"], "a negative value"),
            ("d-wip-missing-reason", ["wip", "5"],
             ["a WIP change needs --reason or --reason-file"],
             "the cap changes only with a reason")):
        record_refusal(suite, GD, cid, path, argv, tokens, why)

    path, problems, _outs = build(work, "d-wip-cap", [
        ("add", "--title", "a", "--origin", "user:d-a", "--horizon", "now"),
        ("add", "--title", "b", "--origin", "user:d-b", "--horizon", "now"),
        ("add", "--title", "c", "--origin", "user:d-c", "--horizon", "now")])
    if problems:
        suite.record(GD, "d-wip-cap-names-occupants", problems)
    else:
        record_refusal(suite, GD, "d-wip-cap-names-occupants", path,
                       ["add", "--title", "d", "--origin", "user:d-d",
                        "--horizon", "now"],
                       ["now is full (3 of 3: R-0001, R-0002, R-0003)",
                        "change the cap with roadmap.py wip"],
                       "the refusal names every occupant")

    path = stage_roadmap(work, "d-same-lane-state", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0004", "next", "--state", "planned",
                         "--reason", "scoped")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0004: next->next [idea->planned]",
                           "stdout %r" % out.strip())
    text = read_utf8(path)
    problems += problem_if(_heading_order(text) != ["R-0001", "R-0004",
                                                    "R-0002", "R-0003"],
                           "the rank moved: %r" % _heading_order(text))
    problems += problem_if("- 2026-09-28 new->next: added\n- 2026-09-28 "
                           "next->next [idea->planned]: scoped\n" not in text,
                           "the bracket log line was not appended")
    suite.record(GD, "d-same-lane-state-keeps-rank", problems,
                 detail=[_d("why", "a same-lane state change keeps its rank "
                                   "and records the bracket")])

    path = stage_roadmap(work, "d-log-append", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0001", "later", "--state", "planned",
                         "--reason", "parked", today="2026-09-30")
    problems = shape_problems(code, err, 0)
    text = read_utf8(path)
    want = ("- 2026-09-28 new->later: harvested by adopt\n"
            "- 2026-09-29 later->now [idea->active]: picked up\n"
            "- 2026-09-30 now->later [active->planned]: parked\n")
    problems += problem_if(want not in text, "the log is not the two old lines "
                           "byte-identical plus the new one")
    problems += problem_if(_heading_order(text) != ["R-0004", "R-0002",
                                                    "R-0001", "R-0003"],
                           "a lane change must append at the END of the "
                           "target lane: %r" % _heading_order(text))
    suite.record(GD, "d-log-is-append-only", problems,
                 detail=[_d("oracle", "the previous log lines byte-identical, "
                                      "in order")])

    path = stage_roadmap(work, "d-rank", EXPECTED_B)
    code, out, err = cli(path, "rank", "R-0002", "--top")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0002: position 1 of 2 in next",
                           "stdout %r" % out.strip())
    problems += problem_if(_heading_order(read_utf8(path))[1:3]
                           != ["R-0002", "R-0004"], "--top did not reorder")
    code, out, err = cli(path, "add", "--title", "e", "--origin", "user:d-e",
                         "--horizon", "next")
    problems += shape_problems(code, err, 0)
    code, out, err = cli(path, "rank", "R-0005", "--before", "R-0004")
    problems += shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0005: position 2 of 3 in next",
                           "stdout %r" % out.strip())
    problems += problem_if(_heading_order(read_utf8(path))[1:4]
                           != ["R-0002", "R-0005", "R-0004"],
                           "--before did not reorder: %r"
                           % _heading_order(read_utf8(path)))
    suite.record(GD, "d-rank-top-and-before", problems)

    path = stage_roadmap(work, "d-wip-raise", EXPECTED_B)
    code, out, err = cli(path, "wip", "6", "--reason", "more hands")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "wip_now: 4 -> 6", "stdout %r"
                           % out.strip())
    text = read_utf8(path)
    problems += problem_if("# wip_now set to 6 on 2026-09-28 (was 4): more "
                           "hands\nwip_now: 6\n" not in text,
                           "the note line was not rewritten")
    problems += problem_if("(was 3)" in text, "the old note survived")
    code, out, err = cli(path, "wip", "1", "--reason", "focus")
    problems += shape_problems(code, err, 0)
    problems += problem_if("\nwip_now: 1\n" not in read_utf8(path),
                           "lowering to the occupancy was not accepted")
    suite.record(GD, "d-wip-raise-and-lower-to-occupancy", problems)

    path = stage_roadmap(work, "d-wip-below", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0004", "now", "--reason", "x")
    problems = shape_problems(code, err, 0)
    got_problems, err = check_refusal(
        path, ["wip", "1", "--reason", "x"],
        ["now holds 2 items (R-0001, R-0004); a cap of 1 would already be "
         "exceeded -- move items out first"])
    suite.record(GD, "d-wip-below-occupancy", problems + got_problems,
                 detail=[_d("stderr", err.strip())])

    path = stage_roadmap(work, "d-lowered-cap", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0004", "now", "--reason", "x")
    problems = shape_problems(code, err, 0)
    planted, why = mutated(read_utf8(path), ("wip_now: 4\n", "wip_now: 1\n"))
    problems += [why] if why else []
    write_file(path, planted)
    code, out, err = cli(path, "move", "R-0004", "next", "--reason", "out")
    problems += shape_problems(code, err, 0)
    suite.record(GD, "d-lowered-cap-allows-move-out", problems,
                 detail=[_d("planted", "wip_now: 1 with two items in now"),
                         _d("why", "WIP is checked on transitions INTO now "
                                   "only (R24)")])
    _group_d_start(suite, work)


def _group_d_start(suite, work):
    """`start ID` is `move ID now --state active` with the default reason
    `started`: the same stdout, log line, WIP cap and refusals as move."""
    for cid, reason_argv, want_reason in (
            ("d-start-default-reason", [], "started"),
            ("d-start-reason-flag", ["--reason", "on it"], "on it"),
            ("d-start-reason-file", None, "picked up by a session")):
        path = stage_roadmap(work, cid, EXPECTED_B)
        if reason_argv is None:
            reason_argv = ["--reason-file",
                           staged(work, cid, "r.txt", want_reason + "\n")]
        code, out, err = cli(path, "start", "R-0004", *reason_argv)
        problems = shape_problems(code, err, 0)
        problems += problem_if(out != "R-0004: next->now [idea->active]\n",
                               "stdout %r" % out)
        text = read_utf8(path) if os.path.isfile(path) else ""
        problems += problem_if("- 2026-09-28 new->next: added\n- 2026-09-28 "
                               "next->now [idea->active]: %s\n" % want_reason
                               not in text,
                               "the log line with reason %r was not appended"
                               % want_reason)
        problems += problem_if(_heading_order(text) != ["R-0001", "R-0004",
                                                        "R-0002", "R-0003"],
                               "R-0004 did not join now at the end: %r"
                               % _heading_order(text))
        problems += problem_if("state: active\nhorizon: now\norigin: "
                               "user:2026-09-28:export-golden\n" not in text,
                               "R-0004 is not active in now")
        suite.record(GD, cid, problems,
                     detail=[_d("argv", "start R-0004 %s"
                                % " ".join(reason_argv[:1])),
                             _d("stdout", out.strip() or "<empty>")])

    path = stage_roadmap(work, "d-start-in-now", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0004", "now", "--state", "planned",
                         "--reason", "queued")
    problems = shape_problems(code, err, 0)
    code, out, err = cli(path, "start", "R-0004")
    problems += shape_problems(code, err, 0)
    problems += problem_if(out != "R-0004: now->now [planned->active]\n",
                           "stdout %r" % out)
    problems += problem_if("- 2026-09-28 now->now [planned->active]: started\n"
                           not in read_utf8(path),
                           "the same-lane state change was not logged")
    suite.record(GD, "d-start-planned-in-now", problems,
                 detail=[_d("why", "an item already in now changes state "
                                   "only; no WIP check (move's same-lane "
                                   "route)")])

    path = stage_roadmap(work, "d-start-refusals", EXPECTED_B)
    for cid, argv, tokens, why in (
            ("d-start-noop", ["start", "R-0001"],
             ["R-0001 is already in now as active -- nothing to move"],
             "already now+active: move's no-op refusal"),
            ("d-start-unknown-id", ["start", "R-0099"],
             ["unknown id R-0099"], "an unknown id"),
            ("d-start-bad-id", ["start", "R-00x1"],
             ["'R-00x1' is not an id (want R-NNNN)"], "a malformed id"),
            ("d-start-reason-flags", ["start", "R-0004", "--reason", "x",
                                      "--reason-file", "nope.txt"],
             ["--reason and --reason-file are exclusive"],
             "the two reason routes exclude each other"),
            ("d-start-backtick-reason", ["start", "R-0004", "--reason",
                                         "see `x`"],
             ["reason value", "carries a backtick"],
             "the single-line reason rule applies"),
            ("d-start-empty-reason", ["start", "R-0004", "--reason", "   "],
             ["reason is empty (after stripping whitespace)"],
             "an explicit empty reason is refused, not defaulted")):
        record_refusal(suite, GD, cid, path, argv, tokens, why)

    path, problems, _outs = build(work, "d-start-wip-cap", [
        ("add", "--title", "a", "--origin", "user:d-sa", "--horizon", "now"),
        ("add", "--title", "b", "--origin", "user:d-sb", "--horizon", "now"),
        ("add", "--title", "c", "--origin", "user:d-sc", "--horizon", "now"),
        ("add", "--title", "d", "--origin", "user:d-sd", "--horizon", "next")])
    if problems:
        suite.record(GD, "d-start-wip-cap-names-occupants", problems)
    else:
        record_refusal(suite, GD, "d-start-wip-cap-names-occupants", path,
                       ["start", "R-0004"],
                       ["now is full (3 of 3: R-0001, R-0002, R-0003)",
                        "change the cap with roadmap.py wip"],
                       "start enters now, so the WIP cap applies")

    path = stage_roadmap(work, "d-start-closed-id", EMPTY_TEXT, BOTH_ARCHIVES)
    record_refusal(suite, GD, "d-start-closed-id", path, ["start", "R-0001"],
                   ["R-0001 is closed (done) -- closed items are immutable; a "
                    "regression is a new item with --follows R-0001"],
                   "an archived item cannot be started")


# ---------------------------------------------------------------------------
# E. dependency graph + ready
# ---------------------------------------------------------------------------

def _list_ids(out):
    return [cell.strip() for line in out.split("\n")
            for cell in line.split("|")[2:3] if cell.strip().startswith("R-")]


def group_e(suite, mod, work, wiki):
    path = stage_roadmap(work, "e-refusals", EXPECTED_B)
    for cid, argv, tokens in (
            ("e-self-block", ["link", "R-0003", "--blocked-by", "R-0003"],
             ["an item cannot block itself"]),
            ("e-unknown-id", ["link", "R-0003", "--blocked-by", "R-0099"],
             ["unknown id in --blocked-by: R-0099"]),
            ("e-two-cycle", ["link", "R-0001", "--blocked-by", "R-0002"],
             ["blocked_by would create a cycle: R-0001, R-0002"]),
            ("e-follows-live", ["link", "R-0004", "--follows", "R-0003"],
             ["--follows must name a closed item; R-0003 is live"])):
        record_refusal(suite, GE, cid, path, argv, tokens, "R23")

    path = stage_roadmap(work, "e-three-cycle", EXPECTED_B)
    code, out, err = cli(path, "link", "R-0004", "--blocked-by", "R-0002")
    record_refusal(suite, GE, "e-three-cycle", path,
                   ["link", "R-0001", "--blocked-by", "R-0004"],
                   ["blocked_by would create a cycle: R-0001, R-0002, R-0004"],
                   "R-0001 <- R-0002 <- R-0004 <- R-0001",
                   extra=shape_problems(code, err, 0))

    path, problems, _outs = build(work, "e-downstream", [
        ("add", "--title", "A", "--origin", "user:e-a"),
        ("add", "--title", "B", "--origin", "user:e-b"),
        ("add", "--title", "C", "--origin", "user:e-c"),
        ("add", "--title", "D", "--origin", "user:e-d"),
        ("link", "R-0003", "--blocked-by", "R-0001"),
        ("link", "R-0001", "--blocked-by", "R-0004"),
        ("link", "R-0001", "--blocked-by", "R-0002")])
    if problems:
        suite.record(GE, "e-cycle-names-members-and-downstream", problems)
    else:
        record_refusal(suite, GE, "e-cycle-names-members-and-downstream", path,
                       ["link", "R-0002", "--blocked-by", "R-0001"],
                       ["blocked_by would create a cycle: R-0001, R-0002, "
                        "R-0003\n"], "L3: A<->B, C downstream of A named; D "
                                     "upstream of A not named",
                       absent=["R-0004"])

    path = stage_roadmap(work, "e-ready-live", EXPECTED_B)
    code, out, err = cli(path, "list", "--ready")
    problems = shape_problems(code, err, 0)
    problems += problem_if(sorted(_list_ids(out)) != ["R-0001", "R-0003",
                                                      "R-0004"],
                           "list --ready gave %r" % _list_ids(out))
    suite.record(GE, "e-ready-excludes-live-blocker", problems,
                 detail=[_d("fixture", "R-0002 blocked by the live R-0001")])

    path, problems, _outs = build(work, "e-ready-closed", [
        ("add", "--title", "X", "--origin", "user:e-x",
         "--blocked-by", "R-0001"),
        ("add", "--title", "Y", "--origin", "user:e-y",
         "--blocked-by", "R-0002"),
        ("add", "--title", "Z", "--origin", "user:e-z",
         "--blocked-by", "R-0003")], archive=BOTH_ARCHIVES)
    if not problems:
        code, out, err = cli(path, "list", "--ready")
        problems += shape_problems(code, err, 0)
        problems += problem_if(sorted(_list_ids(out)) != ["R-0003", "R-0004"],
                               "list --ready gave %r" % _list_ids(out))
    suite.record(GE, "e-ready-includes-done-and-dropped-blockers", problems,
                 detail=[_d("fixture", "archive R-0001 done, R-0002 dropped; "
                                       "live R-0003, R-0004 blocked by them, "
                                       "R-0005 by the live R-0003"),
                         _d("why", "R23: a dropped blocker is a void "
                                   "dependency")])


# ---------------------------------------------------------------------------
# F. the optimistic lock, in-process
# ---------------------------------------------------------------------------

def _lock_case(suite, mod, work, cid, concurrent, why, disable_lock=False):
    got = need(suite, GF, cid, mod, "load_state", "commit", "check_snapshot")
    if got is None:
        return
    load_state, commit, _check = got
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, state, _out, err = in_process(load_state, path)
    if code is not None:
        suite.record(GF, cid, ["load_state refused: %s" % err.strip()])
        return
    changed = state._replace(meta=dict(state.meta, wip_now=7))
    theirs = concurrent(path)
    if disable_lock:
        with patched(mod, "check_snapshot", lambda *a, **k: None):
            code, _v, _out, err = in_process(commit, changed)
    else:
        code, _v, _out, err = in_process(commit, changed)
    now = read_bytes(path)
    leftovers = temp_leftovers(path)
    if disable_lock:
        problems = problem_if(theirs is None, "control B needs a concurrent "
                              "roadmap change")
        problems += problem_if(code is not None, "the write refused anyway "
                               "(%s)" % err.strip())
        problems += problem_if(theirs is not None and now == theirs,
                               "the concurrent bytes survived: the oracle "
                               "cannot see a lost update")
    elif theirs is None and concurrent is not _no_change:
        problems = problem_if(code != 2, "exit %r, want 2" % code)
        problems += problem_if(now != EXPECTED_B.encode("utf-8"),
                               "roadmap.md was rewritten")
        problems += missing_tokens(err, ["changed since it was read"])
    elif concurrent is _no_change:
        problems = problem_if(code is not None, "the write refused: %s"
                              % err.strip())
        problems += problem_if(b"\nwip_now: 7\n" not in now,
                               "the change was not written")
    else:
        problems = problem_if(code != 2, "exit %r, want 2" % code)
        problems += problem_if(now != theirs, "roadmap.md does not hold the "
                               "CONCURRENT bytes")
        problems += missing_tokens(err, ["changed since it was read",
                                         "nothing written"])
    problems += problem_if(leftovers, "temp left behind: %r" % leftovers)
    suite.record(GF, cid, problems,
                 detail=[_d("why", why), _d("stderr", err.strip() or "<empty>")])


def _no_change(path):
    return None


def group_f(suite, mod, work, wiki):
    def roadmap_changed(path):
        theirs = EXPECTED_B.replace("wip_now: 4\n", "wip_now: 5\n").encode()
        write_file(path, theirs)
        return theirs

    def archive_grew(path):
        archive_dir = os.path.join(os.path.dirname(path), "archive")
        os.makedirs(archive_dir, exist_ok=True)
        write_file(os.path.join(archive_dir, ARCHIVE_2_NAME), ARCHIVE_2)
        return None

    _lock_case(suite, mod, work, "f-lock-roadmap-changed", roadmap_changed,
               "roadmap.md mutated behind the module's back (R1)")
    _lock_case(suite, mod, work, "f-lock-archive-listing-changed",
               archive_grew, "a new archive file appeared: the archive "
                             "digest (a concurrent close)")
    _lock_case(suite, mod, work, "f-control-a-no-mutation", _no_change,
               "with no concurrent change the write succeeds")
    _lock_case(suite, mod, work, "f-control-b-lost-update", roadmap_changed,
               "check_snapshot monkeypatched to a no-op: the oracle must see "
               "the concurrent bytes gone", disable_lock=True)


# ---------------------------------------------------------------------------
# G-J. git sandboxes: the suite's OWN git, never roadmap.py's
# ---------------------------------------------------------------------------

# The identity and signing overrides every commit this suite makes carries, so
# a developer's hooks, signing or identity can never decide a case.
GIT_USER = ("-c", "user.name=t", "-c", "user.email=t@t",
            "-c", "commit.gpgsign=false")


def git_run(root, *args, **kwargs):
    """(rc, stdout, stderr) of the suite's own git in a sandbox directory;
    never raises.  `plain=True` drops the identity pairs: the planted-config
    controls run a git WITHOUT any -c override."""
    sandbox_path(root)
    plain = kwargs.pop("plain", False)
    if kwargs:
        raise TypeError("unexpected kwargs: %r" % (kwargs,))
    argv = ["git"] + ([] if plain else list(GIT_USER)) + list(args)
    try:
        proc = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True,
                              errors="replace", timeout=30,
                              env=H.child_env(git_isolation()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def git_repo(root, object_format=None, commit=True):
    """`git init` (optionally with an object format) plus one empty commit;
    False when git -- or that object format -- is unusable here."""
    args = ["-c", "init.defaultBranch=main", "init", "-q"]
    if object_format is not None:
        args.append("--object-format=%s" % object_format)
    rc, _out, _err = git_run(root, *(args + [root]))
    if rc != 0:
        return False
    if not commit:
        return True
    rc, _out, _err = git_run(root, "commit", "-q", "--allow-empty", "-m", "x")
    return rc == 0


def git_commit_all(root, message="fixture"):
    rc_add, _out, err_add = git_run(root, "add", "-A")
    rc_commit, _out, err_commit = git_run(root, "commit", "-q", "-m", message)
    if rc_add == 0 and rc_commit == 0:
        return []
    return ["git add/commit failed: %s" % (err_add + err_commit).strip()[:200]]


def head_sha(root, short=False):
    rc, out, _err = git_run(root, *(["rev-parse"] + (["--short"] if short
                                                     else []) + ["HEAD"]))
    return out.strip() if rc == 0 and out.strip() else None


def fake_git(work, case, body):
    """A `git` script first on PATH: `#!/bin/sh` plus `body`, chmod 0o755
    right after it is written (L5) -- a script git cannot execute would make
    every negative result prove nothing."""
    bin_dir = sandbox_path(work.subdir(case, "bin"))
    fake = write_file(os.path.join(bin_dir, "git"), "#!/bin/sh\n" + body)
    os.chmod(fake, 0o755)
    return bin_dir


@contextlib.contextmanager
def path_first(bin_dir):
    """os.environ PATH with `bin_dir` first, for in-process git() calls;
    restored in finally, always."""
    saved = os.environ.get("PATH")
    os.environ["PATH"] = bin_dir + os.pathsep + (saved or "")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved


class _Crash(Exception):
    """An injected crash: nothing in roadmap.py catches it, so it models a
    process that dies between two writes."""


# ---------------------------------------------------------------------------
# G. close, archive, crash recovery
# ---------------------------------------------------------------------------

CLOSE_TODAY = "2026-09-30"
SLUG_1 = "0001-give-wikilib-git-the-server-s-timeout"
SLUG_3 = "0003-adr-authoring-proposes-deferred-limits"
SLUG_4 = "0004-pin-the-export-golden"
# A planted archive page whose id collides with nothing in EXPECTED_B.
ARCHIVE_9_NAME = "0009-planted.md"
ARCHIVE_9 = ARCHIVE_2.replace("R-0002", "R-0009").replace(
    "name: 0002-p0-p3-priority-labels", "name: 0009-planted")
SPEC_PAGE = "---\nname: real-page\ntype: concept\n---\n\n# Real\n"


def _golden_done(full, today):
    """R-0001 of EXPECTED_B closed as done, after `link --spec real-page`:
    every byte, written by hand."""
    return "\n".join((
        "---",
        "name: %s" % SLUG_1,
        "type: roadmap-item",
        "status: active",
        "title: R-0001 %s Give _wikilib.git() the server's timeout" % DOT,
        "description: Closed roadmap item R-0001 (done %s)." % today,
        "id: R-0001",
        "state: done",
        "horizon: now",
        "origin: tests/test_spawn_stdin.py#skills-survey",
        "spec: real-page",
        "blocked_by: []",
        "tags: [wiki]",
        "closed: %s" % today,
        "commit: %s" % full,
        "---",
        "",
        "# R-0001 %s Give _wikilib.git() the server's timeout" % DOT,
        "",
        "## Why",
        "",
        WHY_1,
        "",
        "## Log",
        "",
        "- 2026-09-28 new->later: harvested by adopt",
        "- 2026-09-29 later->now [idea->active]: picked up",
        "- %s now->done: commit %s" % (today, full),
        ""))


def _one_item_roadmap(n):
    """A roadmap whose only item is R-n in the inbox (the region is not
    parsed, so a bare one serves)."""
    return "\n".join([FRONT_HEAD + WIP_NOTE, "wip_now: 3", "---", "",
                      BEGIN_PREFIX + " -->", END_PREFIX + " -->", "",
                      "# now", "", "# next", "", "# later", "", "# inbox", "",
                      "## R-%04d %s Highest item" % (n, DOT), "",
                      "state: idea", "horizon: unset",
                      "origin: user:2026-09-28:highest", "blocked_by: []",
                      "tags: []", "", "### Log", "",
                      "- 2026-09-28 new->unset: added", ""])


def _archive_dir(path):
    return os.path.join(os.path.dirname(path), "archive")


def _archive_md(path):
    directory = _archive_dir(path)
    if not os.path.isdir(directory):
        return []
    return sorted(n for n in os.listdir(directory) if n.endswith(".md"))


def _no_temp(path):
    """No `.roadmap-*` name in roadmap/ or archive/ (L2)."""
    left = temp_leftovers(path) + temp_leftovers(
        os.path.join(_archive_dir(path), "x"))
    return problem_if(left, "temp name left behind: %r" % left)


def _nothing_archived(path):
    """No archive page AND no `.roadmap-*` name in archive/: a refusal of a
    close created nothing."""
    left = _archive_md(path) + names_under(_archive_dir(path), TMP_PREFIX)
    return problem_if(left, "archive/ holds %r after a refused close" % left)


def _item_survives(path, ident):
    """G's crash oracle: after ANY crash an item is live in roadmap.md or has
    its archive page -- never neither."""
    live = ("\n## %s " % ident) in read_utf8(path)
    archived = any(name.startswith(ident[2:] + "-") for name in _archive_md(path))
    return problem_if(not live and not archived,
                      "%s is in neither roadmap.md nor archive/: the item is "
                      "LOST" % ident)


def group_g(suite, mod, work, wiki):
    _g_close_done(suite, mod, work)
    _g_close_dropped(suite, mod, work)
    _g_show_closed(suite, mod, work)
    _g_sha256(suite, mod, work)
    _g_commit_refusals(suite, mod, work)
    _g_named_refusals(suite, mod, work)
    _g_line_rule(suite, mod, work)
    _g_reason_scalar(suite, mod, work)
    _g_archive_dir_cases(suite, mod, work)
    _g_slugs(suite, mod, work)
    _g_immutable(suite, mod, work)
    _g_id_in_both(suite, mod, work)
    _g_ids(suite, mod, work)
    _g_crash(suite, mod, work)
    _g_lock(suite, mod, work)


def _g_close_done(suite, mod, work):
    cid = "g-close-done-golden"
    root = sandbox_path(work.subdir(cid))
    if not git_repo(root):
        info_skip(suite, GG, cid, "git is not usable here")
        return
    path = stage_roadmap(work, cid, EXPECTED_B)
    page = write_file(os.path.join(work.subdir(cid, "docs", "concepts"),
                                   "real-page.md"), SPEC_PAGE)
    short, full = head_sha(root, short=True), head_sha(root)
    problems = problem_if(short is None or full is None,
                          "the sandbox repo has no HEAD")
    code, out, err = cli(path, "link", "R-0001", "--spec", "real-page")
    problems += shape_problems(code, err, 0)
    code, out, err = cli(path, "close", "R-0001", "--commit", short or "x",
                         today=CLOSE_TODAY)
    problems += shape_problems(code, err, 0)
    rel = "docs/roadmap/archive/%s.md" % SLUG_1
    problems += problem_if(out.strip() != rel, "stdout %r, want %r"
                           % (out.strip(), rel))
    archive = os.path.join(_archive_dir(path), SLUG_1 + ".md")
    want = _golden_done(full, CLOSE_TODAY)
    got = read_utf8(archive) if os.path.isfile(archive) else ""
    problems += problem_if(got != want, "the archive differs from the golden: "
                           "%s" % first_diff(got, want))
    text = read_utf8(path)
    problems += problem_if("\n## R-0001 " in text,
                           "the live block is still in roadmap.md")
    problems += problem_if("Archive: 1 closed item (1 done, 0 dropped)."
                           not in text, "the summary does not count the close")
    problems += problem_if(read_utf8(page) != SPEC_PAGE,
                           "the spec page changed")
    problems += _no_temp(path)
    code, _out, err = cli(path, "list")
    problems += shape_problems(code, err, 0)
    suite.record(GG, cid, problems,
                 detail=[_d("commit", "the real short sha %s, stored as the "
                                      "full %s" % (short, full)),
                         _d("oracle", "a hand-written archive, every byte; "
                                      "the live block gone; the spec page "
                                      "byte-unchanged")])


def _g_close_dropped(suite, mod, work):
    for cid, flag, ident, name, lane, reason in (
            ("g-close-dropped-reason", "--reason", "R-0003", SLUG_3, "unset",
             "not worth it"),
            ("g-close-dropped-reason-file", "--reason-file", "R-0004", SLUG_4,
             "next", "superseded by R-0002")):
        path = stage_roadmap(work, cid, EXPECTED_B)
        value = reason
        if flag == "--reason-file":
            value = staged(work, cid, "reason.txt", reason + "\n")
        code, out, err = cli(path, "close", ident, flag, value,
                             today=CLOSE_TODAY)
        problems = shape_problems(code, err, 0)
        archive = os.path.join(_archive_dir(path), name + ".md")
        text = read_utf8(archive) if os.path.isfile(archive) else ""
        for line in ("state: dropped", "horizon: %s" % lane,
                     "reason: %s" % reason, "closed: %s" % CLOSE_TODAY,
                     "description: Closed roadmap item %s (dropped %s)."
                     % (ident, CLOSE_TODAY)):
            problems += problem_if("\n%s\n" % line not in text,
                                   "the archive lacks %r" % line)
        problems += problem_if("\ncommit:" in text, "a dropped item carries "
                                                    "commit:")
        problems += problem_if(not text.endswith(
            "\n- %s %s->dropped: %s\n" % (CLOSE_TODAY, lane, reason)),
            "the closing log line is not the last line")
        problems += problem_if("\n## %s " % ident in read_utf8(path),
                               "the live block is still in roadmap.md")
        problems += _no_temp(path)
        suite.record(GG, cid, problems,
                     detail=[_d("route", flag), _d("stderr", err.strip()
                                                   or "<empty>")])


def _g_show_closed(suite, mod, work):
    cid = "g-show-closed-verbatim"
    got = need(suite, GG, cid, mod, "cmd_show", "read_regular")
    if got is None:
        return
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, out, err = cli(path, "close", "R-0003", "--reason", "gone",
                         today=CLOSE_TODAY)
    problems = shape_problems(code, err, 0)
    archive = os.path.join(_archive_dir(path), SLUG_3 + ".md")
    want = read_bytes(archive) if os.path.isfile(archive) else b""
    code, raw, err = _spawn_raw([sys.executable, TARGET, "show", "R-0003",
                                 "--file", path, "--today", TODAY],
                                os.path.dirname(path))
    problems += problem_if(code != 0, "show exited %r: %r" % (code, err[-200:]))
    problems += problem_if(not want or raw != want,
                           "show's stdout (%d bytes) is not the archive's "
                           "bytes (%d)" % (len(raw), len(want)))
    seen = []
    real = mod.read_regular

    def spy(p, cap):
        seen.append(p)
        return real(p, cap)

    with patched(mod, "read_regular", spy):
        code, out, err = call_module(mod, "cmd_show", path, "R-0003")
    problems += problem_if(code != 0, "in-process show exited %r: %s"
                           % (code, err.strip()))
    problems += problem_if(out.encode("utf-8") != want,
                           "in-process show is not the archive's text")
    problems += problem_if(archive not in seen, "the archive was not read "
                           "through read_regular: %r" % seen)
    suite.record(GG, cid, problems,
                 detail=[_d("oracle", "bytes equal, no extra trailing "
                                      "newline (L2); a spy on read_regular, "
                                      "restored in finally (R31)")])


def _g_sha256(suite, mod, work):
    cid = "g-close-sha256-repository"
    root = sandbox_path(work.subdir(cid))
    if not git_repo(root, object_format="sha256"):
        info_skip(suite, GG, cid, "git init --object-format=sha256 failed "
                                  "(no SHA-256 support)")
        return
    path = stage_roadmap(work, cid, EXPECTED_B)
    short, full = head_sha(root, short=True), head_sha(root)
    code, out, err = cli(path, "close", "R-0001", "--commit", short or "x",
                         today=CLOSE_TODAY)
    problems = shape_problems(code, err, 0)
    problems += problem_if(len(full or "") != 64, "HEAD is %r, not 64 hex"
                           % full)
    archive = os.path.join(_archive_dir(path), SLUG_1 + ".md")
    text = read_utf8(archive) if os.path.isfile(archive) else ""
    problems += problem_if("\ncommit: %s\n" % full not in text,
                           "the archive does not store the 64-hex sha")
    code, _out, err = cli(path, "list")
    problems += shape_problems(code, err, 0)
    suite.record(GG, cid, problems,
                 detail=[_d("why", "L4: FULL_SHA_RE takes 40 or 64 hex")])


def _g_commit_refusals(suite, mod, work):
    cid = "g-commit-missing"
    root = sandbox_path(work.subdir(cid))
    if git_repo(root):
        path = stage_roadmap(work, cid, EXPECTED_B)
        record_refusal(suite, GG, cid, path,
                       ["close", "R-0001", "--commit", "deadbeef"],
                       ["commit deadbeef does not exist in %s (git cat-file -e)"
                        % os.path.dirname(path)], "R19",
                       after=lambda: _nothing_archived(path))
    else:
        info_skip(suite, GG, cid, "git is not usable here")
    for cid, value in (("g-commit-non-hex", "xyz1234"),
                       ("g-commit-65-hex", "a" * 65),
                       ("g-commit-dash-x", "-x")):
        path = stage_roadmap(work, cid, EXPECTED_B)
        record_refusal(suite, GG, cid, path,
                       ["close", "R-0001", "--commit=%s" % value],
                       ["%r is not a commit sha (want 7-64 lowercase hex "
                        "digits)" % value], "R19: SHA_RE before any spawn",
                       after=lambda path=path: _nothing_archived(path))

    cid = "g-commit-token-shape"
    got = need(suite, GG, cid, mod, "cmd_close")
    if got is None:
        return
    path = stage_roadmap(work, cid, EXPECTED_B)
    record = os.path.join(work.path, cid, "git-argv.txt")
    bin_dir = fake_git(work, cid, 'echo "$@" >> \'%s\'\nexit 1\n' % record)
    arabic = "".join(chr(0x660 + int(c)) for c in "1234567")
    problems = []
    before = read_bytes(path)
    with path_first(bin_dir), patched(mod, "_TODAY", TODAY):
        for value in ("abc1234\n", arabic, "-x"):
            code, _out, err = call_module(mod, "cmd_close", path, "R-0003",
                                          value, None, None, None)
            problems += ["%r: %s" % (value, p)
                         for p in shape_problems(code, err)]
            problems += problem_if(
                "is not a commit sha (want 7-64 lowercase hex digits)"
                not in err, "%r: stderr %r" % (value, err.strip()))
    problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
    problems += _nothing_archived(path)
    problems += problem_if(os.path.exists(record), "git was spawned: %r"
                           % (read_utf8(record) if os.path.exists(record)
                              else ""))
    suite.record(GG, cid, problems,
                 detail=[_d("values", "a trailing newline, Arabic-Indic "
                                      "digits, -x (M3, R35, R19)"),
                         _d("oracle", "a fake git first on PATH records its "
                                      "argv; the record must not exist")])


def _g_named_refusals(suite, mod, work):
    cid = "close-not-a-repo"
    path = stage_roadmap(work, cid, EXPECTED_B)
    record_refusal(suite, GG, cid, path,
                   ["close", "R-0001", "--commit", "abc1234"],
                   ["cannot verify commit abc1234: %s is not inside a git "
                    "repository" % os.path.dirname(path)], "L3",
                   after=lambda: _nothing_archived(path))

    cid = "close-git-127"
    path = stage_roadmap(work, cid, EXPECTED_B)
    empty_bin = sandbox_path(work.subdir(cid, "empty-bin"))
    record_refusal(suite, GG, cid, path,
                   ["close", "R-0001", "--commit", "abc1234"],
                   ["cannot verify commit abc1234: git failed (git executable "
                    "not found)"], "L3: a missing git is never 'not a "
                                   "repository'",
                   env_extra={"PATH": empty_bin},
                   after=lambda: _nothing_archived(path))

    cid = "close-git-124"
    got = need(suite, GG, cid, mod, "cmd_close", "GIT_TIMEOUT_SEC")
    if got is not None:
        path = stage_roadmap(work, cid, EXPECTED_B)
        bin_dir = fake_git(work, cid, "exec sleep 5\n")
        before = read_bytes(path)
        with path_first(bin_dir), patched(mod, "GIT_TIMEOUT_SEC", 1), \
                patched(mod, "_TODAY", TODAY):
            code, _out, err = call_module(mod, "cmd_close", path, "R-0001",
                                          "abc1234", None, None, None)
        problems = shape_problems(code, err)
        problems += missing_tokens(err, ["cannot verify commit abc1234: git "
                                         "failed (git rev-parse timed out "
                                         "after 1s)"])
        problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
        problems += _nothing_archived(path)
        suite.record(GG, cid, problems,
                     detail=[_d("fake", "#!/bin/sh + exec sleep 5, 0o755, "
                                        "GIT_TIMEOUT_SEC=1"),
                             _d("stderr", err.strip())])

    cid = "close-unexpected-full-sha"
    root = sandbox_path(work.subdir(cid))
    path = stage_roadmap(work, cid, EXPECTED_B)
    bin_dir = fake_git(work, cid, "\n".join((
        'if [ "$1 $2 $3 $4 $5 $6 $7 $8" != "-c core.fsmonitor=false -c '
        'core.hooksPath=/dev/null -c protocol.allow=never '
        '-c safe.bareRepository=explicit" ]; then',
        '  echo "fake git: no GIT_SAFE_ARGV prefix: $*" >&2; exit 99',
        'fi',
        'shift 8',
        'case "$1 $2" in',
        '  "rev-parse --show-toplevel") echo \'%s\'; exit 0 ;;' % root,
        '  "cat-file -e") exit 0 ;;',
        '  "rev-parse --verify") echo nothex; exit 0 ;;',
        'esac',
        'exit 98',
        '')))
    record_refusal(suite, GG, cid, path,
                   ["close", "R-0001", "--commit", "abc1234"],
                   ["cannot verify commit abc1234: git failed (unexpected "
                    "rev-parse output)"],
                   "L4: the fake asserts the GIT_SAFE_ARGV -c pairs, then "
                   "dispatches on what follows them",
                   env_extra={"PATH": bin_dir + os.pathsep
                              + os.environ.get("PATH", "")},
                   after=lambda: _nothing_archived(path))

    cid = "close-already-archived"
    got = need(suite, GG, cid, mod, "cmd_close", "load_state")
    if got is not None:
        path = stage_roadmap(work, cid, EXPECTED_B, {})
        planted = os.path.join(_archive_dir(path), "0001-other.md")
        real_load = mod.load_state

        def load_then_plant(p):
            state = real_load(p)
            write_file(planted, "planted after the read\n")
            return state

        before = read_bytes(path)
        with patched(mod, "load_state", load_then_plant), \
                patched(mod, "_TODAY", TODAY):
            code, _out, err = call_module(mod, "cmd_close", path, "R-0001",
                                          None, "x", None, None)
        problems = shape_problems(code, err)
        problems += missing_tokens(err, ["R-0001 is already archived at "
                                         "docs/roadmap/archive/0001-other.md"])
        problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
        problems += problem_if(_archive_md(path) != ["0001-other.md"],
                               "archive/ holds %r" % _archive_md(path))
        suite.record(GG, cid, problems,
                     detail=[_d("planted", "archive/0001-other.md, AFTER "
                                           "load_state (R20)"),
                             _d("stderr", err.strip())])

    cid = "close-flags"
    path = stage_roadmap(work, cid, EXPECTED_B)
    problems = []
    for argv in (["close", "R-0001"],
                 ["close", "R-0001", "--commit", "abc1234", "--reason", "x"]):
        got_problems, _err = check_refusal(
            path, argv, ["close needs exactly one of --commit SHA (done), "
                         "--reason TEXT or --reason-file PATH (dropped)"])
        problems += ["%s: %s" % (" ".join(argv[2:]) or "no flag", p)
                     for p in got_problems]
    suite.record(GG, cid, problems,
                 detail=[_d("routes", "none of the three; --commit AND "
                                      "--reason")])


def _g_line_rule(suite, mod, work):
    path = stage_roadmap(work, "g-line-rule", EXPECTED_B)
    record_refusal(suite, GG, "g-close-reason-backtick", path,
                   ["close", "R-0003", "--reason", "see `x`"],
                   ["reason value", "carries a backtick"],
                   "M6 on the close route",
                   after=lambda: _nothing_archived(path))
    for field, argv_of in (
            ("reason", lambda ch: ["close", "R-0003", "--reason",
                                   "a%sb" % ch]),
            ("slug", lambda ch: ["close", "R-0003", "--reason", "x",
                                 "--slug", "a%sb" % ch])):
        problems = []
        for ch in SEPARATORS:
            got_problems, _err = check_refusal(
                path, argv_of(ch), ["%s value" % field,
                                    "carries a line separator (U+%04X)"
                                    % ord(ch)])
            problems += ["U+%04X: %s" % (ord(ch), p) for p in got_problems]
        problems += _nothing_archived(path)
        suite.record(GG, "g-close-%s-separators" % field, problems,
                     detail=[_d("list", "group C's separators, the ones "
                                        "str.splitlines honours (M2, R26)")])


def _g_reason_scalar(suite, mod, work):
    path = stage_roadmap(work, "g-reason-scalar", EXPECTED_B)
    empty = staged(work, "g-reason-scalar", "empty.txt", "\n")
    for cid, argv, tokens in (
            ("g-reason-file-empty", ["--reason-file", empty],
             ["reason is empty (after stripping whitespace)"]),
            ("g-reason-quote-wrapped", ["--reason", '"x"'],
             ["reason value", "wrapped in matching quotes"]),
            ("g-reason-bracket-wrapped", ["--reason", "[x]"],
             ["reason value", "wrapped in brackets"])):
        record_refusal(suite, GG, cid, path, ["close", "R-0003"] + argv,
                       tokens, "H1, M1 round 5: the close reason is also a "
                               "frontmatter scalar", absent=["internal:"],
                       after=lambda: _nothing_archived(path))

    cid = "g-reason-leading-space-stripped"
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, out, err = cli(path, "close", "R-0003", "--reason", " x",
                         today=CLOSE_TODAY)
    problems = shape_problems(code, err, 0)
    archive = os.path.join(_archive_dir(path), SLUG_3 + ".md")
    text = read_utf8(archive) if os.path.isfile(archive) else ""
    problems += problem_if("\nreason: x\n" not in text, "reason: is not 'x'")
    problems += problem_if(not text.endswith("->dropped: x\n"),
                           "the closing log line is not 'dropped: x'")
    code, _out, err = cli(path, "list")
    problems += shape_problems(code, err, 0)
    suite.record(GG, cid, problems,
                 detail=[_d("why", "M1/M2: every caller writes the "
                                   "stripped value")])

    cid = "g-reason-self-check-before-create"
    got = need(suite, GG, cid, mod, "cmd_close", "check_reason",
               "check_scalar")
    if got is None:
        return
    path = stage_roadmap(work, cid, EXPECTED_B)
    before = read_bytes(path)
    with patched(mod, "check_reason", lambda value: value), \
            patched(mod, "check_scalar", lambda field, value: value), \
            patched(mod, "_TODAY", TODAY):
        code, _out, err = call_module(mod, "cmd_close", path, "R-0003", None,
                                      " x", None, None)
    problems = shape_problems(code, err)
    problems += problem_if("internal:" not in err and "is incomplete" not in err,
                           "stderr %r is neither the round-trip refusal nor "
                           "parse_archive's" % err.strip())
    problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
    problems += _nothing_archived(path)
    suite.record(GG, cid, problems,
                 detail=[_d("planted", "check_reason and check_scalar return "
                                       "their argument unstripped (restored "
                                       "in finally)"),
                         _d("why", "H1 round 5: the self-check runs BEFORE "
                                   "create_exclusive"),
                         _d("stderr", err.strip())])


def _g_archive_dir_cases(suite, mod, work):
    cid = "g-close-no-archive-dir"
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, out, err = cli(path, "close", "R-0003", "--reason", "x")
    problems = shape_problems(code, err, 0)
    listing = (sorted(os.listdir(_archive_dir(path)))
               if os.path.isdir(_archive_dir(path)) else None)
    problems += problem_if(listing != [SLUG_3 + ".md"],
                           "archive/ holds %r, want exactly the new page"
                           % (listing,))
    code, _out, err = cli(path, "list")
    problems += shape_problems(code, err, 0)
    suite.record(GG, cid, problems,
                 detail=[_d("fixture", "live items, NO archive/: a fresh "
                                       "clone (M5, R30)")])

    cid = "g-close-readonly-archive"
    if is_root():
        info_skip(suite, GG, cid, "root ignores the permission bits")
    else:
        path = stage_roadmap(work, cid, EXPECTED_B, {})
        archive_dir = _archive_dir(path)
        before = read_bytes(path)
        os.chmod(archive_dir, 0o500)
        try:
            code, _out, err = cli(path, "close", "R-0003", "--reason", "x")
        finally:
            os.chmod(archive_dir, 0o700)
        problems = shape_problems(code, err)
        problems += problem_if(not err.startswith(
            "roadmap: cannot create a temporary file in %s" % archive_dir),
            "stderr %r" % err.strip())
        problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
        problems += problem_if(os.listdir(archive_dir), "archive/ holds %r"
                               % os.listdir(archive_dir))
        suite.record(GG, cid, problems,
                     detail=[_d("why", "I3 round 4, R32: create_exclusive's "
                                       "own guarded mkstemp"),
                             _d("stderr", err.strip())])

    cid = "g-close-containment"
    if not can_symlink():
        info_skip(suite, GG, cid, "no os.symlink")
        return
    outside = sandbox_path(work.subdir(cid + "-outside-roadmap"))
    write_file(os.path.join(outside, "roadmap.md"), EXPECTED_B)
    before = H.file_digests(outside)
    roadmap_dir = os.path.join(work.subdir(cid, "docs"), "roadmap")
    os.symlink(outside, roadmap_dir)
    path = os.path.join(roadmap_dir, "roadmap.md")
    record_refusal(suite, GG, cid, path,
                   ["close", "R-0001", "--reason", "x"],
                   ["roadmap directory %s is a symlink -- refusing"
                    % roadmap_dir], "S4-1, R33",
                   after=lambda: problem_if(
                       H.file_digests(outside) != before
                       or sorted(os.listdir(outside)) != ["roadmap.md"],
                       "the outside directory changed"))


def _g_slugs(suite, mod, work):
    for cid, sub, body in (
            ("g-slug-collides-adr", "adr",
             "---\nname: %s\ntype: adr\n---\n\n# x\n" % SLUG_3),
            ("g-slug-collides-sources", "sources", "just a source note\n")):
        path = stage_roadmap(work, cid, EXPECTED_B)
        planted = write_file(os.path.join(work.subdir(cid, "docs", sub),
                                          SLUG_3 + ".md"), body)
        record_refusal(suite, GG, cid, path,
                       ["close", "R-0003", "--reason", "x"],
                       ["archive name %s collides with %s -- pass --slug"
                        % (SLUG_3, planted)],
                       "R13: the scan is wider than SKIP_DIRS",
                       after=lambda path=path: _nothing_archived(path))
    path = os.path.join(work.path, "g-slug-collides-adr", "docs", "roadmap",
                        "roadmap.md")
    code, out, err = cli(path, "close", "R-0003", "--reason", "x",
                         "--slug", "other-name")
    problems = shape_problems(code, err, 0)
    problems += problem_if(_archive_md(path) != ["0003-other-name.md"],
                           "archive/ holds %r" % _archive_md(path))
    problems += _no_temp(path)
    suite.record(GG, "g-slug-resolves-collision", problems)

    path = stage_roadmap(work, "g-slug-bad", EXPECTED_B)
    record_refusal(suite, GG, "g-slug-bad", path,
                   ["close", "R-0003", "--reason", "x", "--slug", "Bad_Slug"],
                   ["--slug 'Bad_Slug' is not kebab-case"], "S2-1",
                   after=lambda: _nothing_archived(path))
    path = stage_roadmap(work, "g-slug-empty", EXPECTED_B)
    code, out, err = cli(path, "add", "--title", "?!", "--origin",
                         "user:g-empty-slug")
    record_refusal(suite, GG, "g-slug-empty", path,
                   ["close", "R-0005", "--reason", "x"],
                   ["the title of R-0005 yields no slug -- pass --slug"],
                   "a title of punctuation only",
                   extra=shape_problems(code, err, 0),
                   after=lambda: _nothing_archived(path))


def _g_immutable(suite, mod, work):
    cid = "g-second-close-refused"
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, _out, err = cli(path, "close", "R-0003", "--reason", "x")
    archive = os.path.join(_archive_dir(path), SLUG_3 + ".md")
    before = read_bytes(archive) if os.path.isfile(archive) else None
    record_refusal(suite, GG, cid, path, ["close", "R-0003", "--reason", "y"],
                   ["R-0003 is closed (dropped) -- closed items are immutable; "
                    "a regression is a new item with --follows R-0003"],
                   "no reopen, no re-close",
                   extra=shape_problems(code, err, 0),
                   after=lambda: problem_if(
                       before is None or read_bytes(archive) != before,
                       "the archive bytes changed"))

    cid = "g-archive-unchanged-by-later-commands"
    path = stage_roadmap(work, cid, EXPECTED_B)
    code, _out, err = cli(path, "close", "R-0003", "--reason", "x")
    problems = shape_problems(code, err, 0)
    before = H.file_digests(_archive_dir(path))
    for argv in (("add", "--title", "t", "--origin", "user:g-later"),
                 ("move", "R-0004", "later", "--reason", "x"),
                 ("rank", "R-0002", "--top"),
                 ("link", "R-0002", "--unblock", "R-0001"),
                 ("wip", "5", "--reason", "x"),
                 ("render",), ("list",), ("show", "R-0003"), ("export",)):
        code, _out, err = cli(path, *argv)
        problems += ["%s: %s" % (argv[0], p) for p in
                     shape_problems(code, err, 0)]
    problems += problem_if(H.file_digests(_archive_dir(path)) != before,
                           "an archive file changed")
    suite.record(GG, cid, problems,
                 detail=[_d("commands", "add, move, rank, link, wip, render, "
                                        "list, show, export")])


def _g_id_in_both(suite, mod, work):
    note = ("roadmap: note: R-0001 is in both roadmap.md and "
            "docs/roadmap/archive/%s; the archive wins -- run roadmap.py "
            "render to persist the repair" % ARCHIVE_1_NAME)
    cid = "g-id-in-both-read-commands-note"
    path = stage_roadmap(work, cid, EXPECTED_B, {ARCHIVE_1_NAME: ARCHIVE_1})
    problems = []
    export_out = ""
    for argv in (("list",), ("show", "R-0001"), ("export",)):
        before = read_bytes(path)
        code, out, err = cli(path, *argv)
        problems += ["%s: %s" % (argv[0], p) for p in
                     shape_problems(code, err, 0)]
        problems += problem_if(note not in err, "%s: stderr %r"
                               % (argv[0], err.strip()))
        problems += problem_if(read_bytes(path) != before,
                               "%s wrote roadmap.md" % argv[0])
        if argv[0] == "export":
            export_out = out
    suite.record(GG, cid, problems,
                 detail=[_d("why", "R2: read commands repair in memory and "
                                   "say so; they write nothing")])

    try:
        items = json.loads(export_out)["items"]
    except (ValueError, KeyError, TypeError) as exc:
        items, problems = [], ["the export is not JSON: %s" % exc]
    else:
        problems = []
    ones = [i for i in items if i.get("id") == "R-0001"]
    problems += problem_if(len(ones) != 1 or ones[0].get("closed") is None,
                           "R-0001 appears %d time(s); want once, archived"
                           % len(ones))
    suite.record(GG, "g-id-in-both-export-once", problems)

    code, _out, err = cli(path, "render")
    problems = shape_problems(code, err, 0)
    problems += problem_if(
        "roadmap: repaired: R-0001 was in both roadmap.md and "
        "docs/roadmap/archive/%s" % ARCHIVE_1_NAME not in err,
        "stderr %r" % err.strip())
    problems += problem_if("\n## R-0001 " in read_utf8(path),
                           "the live copy survived render")
    suite.record(GG, "g-id-in-both-render-repairs", problems)


def _g_ids(suite, mod, work):
    cid = "g-next-id-after-closing-highest"
    path = stage_roadmap(work, cid, _one_item_roadmap(5), BOTH_ARCHIVES)
    code, _out, err = cli(path, "close", "R-0005", "--reason", "x")
    problems = shape_problems(code, err, 0)
    code, out, err = cli(path, "add", "--title", "t", "--origin",
                         "user:g-next-id")
    problems += shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0006", "add gave %r, want R-0006 "
                           "(max of the archive + 1; the gap 3-4 is never "
                           "reused)" % out.strip())
    suite.record(GG, cid, problems,
                 detail=[_d("fixture", "archive R-0001, R-0002; live only "
                                       "R-0005, closed, leaving roadmap.md "
                                       "empty (R12)")])


def _g_crash(suite, mod, work):
    got = need(suite, GG, "g-crash-link-fallback", mod, "cmd_close")
    if got is None:
        return

    def broken(*_args, **_kwargs):
        raise OSError(errno.EIO, "Input/output error")

    ref = stage_roadmap(work, "g-crash-reference", EXPECTED_B)
    code, _out, err = cli(ref, "close", "R-0003", "--reason", "x",
                          today=CLOSE_TODAY)
    reference = os.path.join(_archive_dir(ref), SLUG_3 + ".md")
    want = read_bytes(reference) if os.path.isfile(reference) else None

    cid = "g-crash-link-fallback"
    path = stage_roadmap(work, cid, EXPECTED_B)
    with patched(os, "link", broken), patched(mod, "_TODAY", CLOSE_TODAY):
        code2, _out, err2 = call_module(mod, "cmd_close", path, "R-0003",
                                        None, "x", None, None)
    problems = shape_problems(code, err, 0)
    problems += problem_if(code2 not in (0, None), "the fallback close exited "
                           "%r: %s" % (code2, err2.strip()))
    archive = os.path.join(_archive_dir(path), SLUG_3 + ".md")
    problems += problem_if(want is None or not os.path.isfile(archive)
                           or read_bytes(archive) != want,
                           "the O_EXCL fallback did not publish the same "
                           "complete page")
    problems += _no_temp(path)
    code, _out, err = cli(path, "list")
    problems += shape_problems(code, err, 0)
    suite.record(GG, cid, problems,
                 detail=[_d("planted", "os.link raises EIO after the temp "
                                       "exists (restored in finally)")])

    cid = "g-crash-both-routes-fail"
    path = stage_roadmap(work, cid, EXPECTED_B)
    before = read_bytes(path)
    with patched(os, "link", broken), patched(os, "write", broken), \
            patched(mod, "_TODAY", CLOSE_TODAY):
        code, _out, err = call_module(mod, "cmd_close", path, "R-0003", None,
                                      "x", None, None)
    problems = shape_problems(code, err)
    problems += problem_if("cannot create" not in err, "stderr %r"
                           % err.strip())
    problems += problem_if(read_bytes(path) != before, "roadmap.md changed")
    problems += _nothing_archived(path)
    suite.record(GG, cid, problems,
                 detail=[_d("planted", "os.link AND the fallback's os.write "
                                       "raise EIO: no NNNN-*.md, no temp "
                                       "(R3)"),
                         _d("stderr", err.strip())])

    cid = "g-crash-between-writes-archive-wins"
    path = stage_roadmap(work, cid, EXPECTED_B)
    real_create = mod.create_exclusive

    def create_then_crash(p, text):
        real_create(p, text)
        raise _Crash("injected between the archive and roadmap.md")

    crashed = False
    with patched(mod, "create_exclusive", create_then_crash), \
            patched(mod, "_TODAY", CLOSE_TODAY):
        try:
            call_module(mod, "cmd_close", path, "R-0003", None, "x", None,
                        None)
        except _Crash:
            crashed = True
    problems = problem_if(not crashed, "the injected crash did not happen")
    problems += _item_survives(path, "R-0003")
    problems += problem_if(_archive_md(path) != [SLUG_3 + ".md"],
                           "archive/ holds %r" % _archive_md(path))
    code, _out, err = cli(path, "render")
    problems += shape_problems(code, err, 0)
    problems += problem_if("roadmap: repaired: R-0003" not in err,
                           "render did not repair: %r" % err.strip())
    problems += problem_if("\n## R-0003 " in read_utf8(path),
                           "the live copy survived render")
    suite.record(GG, cid, problems,
                 detail=[_d("oracle", "the item survives the crash (archive "
                                      "first), and render persists the "
                                      "repair (R2)")])


def _g_lock(suite, mod, work):
    got = need(suite, GG, "g-lock-before-create", mod, "cmd_close",
               "render_archive", "create_exclusive")
    if got is None:
        return
    theirs = EXPECTED_B.replace("wip_now: 4\n", "wip_now: 5\n").encode()

    cid = "g-lock-before-create"
    path = stage_roadmap(work, cid, EXPECTED_B)
    real_render = mod.render_archive

    def render_and_race(item, name):
        write_file(path, theirs)
        return real_render(item, name)

    with patched(mod, "render_archive", render_and_race), \
            patched(mod, "_TODAY", TODAY):
        code, _out, err = call_module(mod, "cmd_close", path, "R-0003", None,
                                      "x", None, None)
    problems = shape_problems(code, err)
    problems += missing_tokens(err, ["changed since it was read"])
    problems += problem_if(read_bytes(path) != theirs, "roadmap.md does not "
                           "hold the concurrent bytes")
    problems += _nothing_archived(path)
    suite.record(GG, cid, problems,
                 detail=[_d("race", "roadmap.md rewritten after the read, "
                                    "before create_exclusive"),
                         _d("stderr", err.strip())])

    def roadmap_race(p):
        write_file(p, theirs)

    def listing_race(p):
        write_file(os.path.join(_archive_dir(p), ARCHIVE_9_NAME), ARCHIVE_9)

    for cid, race in (("g-lock-after-create-roadmap", roadmap_race),
                      ("g-lock-after-create-listing", listing_race)):
        path = stage_roadmap(work, cid, EXPECTED_B)
        real_create = mod.create_exclusive

        def create_and_race(p, text, path=path, race=race,
                            real_create=real_create):
            real_create(p, text)
            race(path)

        with patched(mod, "create_exclusive", create_and_race), \
                patched(mod, "_TODAY", TODAY):
            code, _out, err = call_module(mod, "cmd_close", path, "R-0003",
                                          None, "x", None, None)
        problems = shape_problems(code, err)
        problems += missing_tokens(err, [
            "R-0003 was archived to docs/roadmap/archive/%s.md but "
            "roadmap.md or the archive listing changed underneath -- the "
            "next run removes the live copy (archive wins)" % SLUG_3])
        problems += problem_if(SLUG_3 + ".md" not in _archive_md(path),
                               "the archive page was not kept")
        code, _out, rerr = cli(path, "render")
        problems += shape_problems(code, rerr, 0)
        problems += problem_if("roadmap: repaired: R-0003" not in rerr,
                               "render did not repair: %r" % rerr.strip())
        problems += problem_if("\n## R-0003 " in read_utf8(path),
                               "the live copy survived render")
        suite.record(GG, cid, problems,
                     detail=[_d("why", "L6: the second re-check compares "
                                       "BOTH digests"),
                             _d("stderr", err.strip())])


# ---------------------------------------------------------------------------
# H. export
# ---------------------------------------------------------------------------

# The section 5.2 example roadmap, every byte; with BOTH_ARCHIVES it is the
# fixture the 5.3 golden export describes.
FIXTURE_52 = FRONT_HEAD + "\n".join((
    WIP_NOTE,
    "wip_now: 3",
    "---",
    "",
    BEGIN_PREFIX + " -- generated by roadmap.py; do not edit by hand -->",
    "WIP now: 1 of 3. Archive: 2 closed items (1 done, 1 dropped).",
    "",
    _ROW % ("Lane", "Id", "State", "Title", "Ready"),
    "|" + "|".join("-" * n for n in (7, 8, 9, 42, 7)) + "|",
    _ROW % ("now", "R-0003", "active",
            "Give _wikilib.git() the server's timeout", "yes"),
    _ROW % ("next", "R-0004", "planned",
            "Unify the skill and server git helpers", "no"),
    _ROW % ("inbox", "R-0005", "idea",
            "ADR authoring proposes deferred limits", "yes"),
    END_PREFIX + " -->",
    "",
    "# now",
    "",
    "## R-0003 %s Give _wikilib.git() the server's timeout" % DOT,
    "",
    "state: active",
    "horizon: now",
    "origin: tests/test_spawn_stdin.py#skills-survey",
    "blocked_by: []",
    "tags: [wiki]",
    "",
    WHY_1,
    "",
    "### Log",
    "",
    "- 2026-09-28 new->later: harvested by adopt",
    "- 2026-09-29 later->now [idea->active]: picked up",
    "",
    "# next",
    "",
    "## R-0004 %s Unify the skill and server git helpers" % DOT,
    "",
    "state: planned",
    "horizon: next",
    "origin: docs/adr/0012-the-transport-tier-diverged.md#consequences",
    "blocked_by: [R-0003]",
    "follows: R-0001",
    "severity: low",
    "tags: [scripts, wiki]",
    "",
    WHY_2,
    "",
    "### Log",
    "",
    "- 2026-09-28 new->next [idea->planned]: harvested by adopt",
    "",
    "# later",
    "",
    "# inbox",
    "",
    "## R-0005 %s ADR authoring proposes deferred limits" % DOT,
    "",
    "state: idea",
    "horizon: unset",
    "origin: user:2026-09-28:adr-producer",
    "blocked_by: []",
    "tags: [adr, producer]",
    "",
    "### Log",
    "",
    "- 2026-09-28 new->unset: added",
    ""))

# The section 5.3 document, byte for byte: an inline constant, so there is no
# committed fixture file to drift from its generator.
GOLDEN_EXPORT = '''{
  "counts": {
    "done": 1,
    "dropped": 1,
    "later": 0,
    "next": 1,
    "now": 1,
    "untriaged": 1
  },
  "items": [
    {
      "blocked_by": [],
      "closed": null,
      "follows": null,
      "horizon": "now",
      "id": "R-0003",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "harvested by adopt",
          "state": null,
          "to": "later"
        },
        {
          "date": "2026-09-29",
          "from": "later",
          "reason": "picked up",
          "state": {
            "from": "idea",
            "to": "active"
          },
          "to": "now"
        }
      ],
      "origin": "tests/test_spawn_stdin.py#skills-survey",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": true,
      "severity": null,
      "spec": null,
      "state": "active",
      "tags": [
        "wiki"
      ],
      "title": "Give _wikilib.git() the server's timeout",
      "why": "The skill-side git helper passes stdin=DEVNULL but has no timeout; the server copy has both."
    },
    {
      "blocked_by": [
        "R-0003"
      ],
      "closed": null,
      "follows": "R-0001",
      "horizon": "next",
      "id": "R-0004",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "harvested by adopt",
          "state": {
            "from": "idea",
            "to": "planned"
          },
          "to": "next"
        }
      ],
      "origin": "docs/adr/0012-the-transport-tier-diverged.md#consequences",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": false,
      "severity": "low",
      "spec": null,
      "state": "planned",
      "tags": [
        "scripts",
        "wiki"
      ],
      "title": "Unify the skill and server git helpers",
      "why": "Two copies of one helper drift; ADR 0012 declined a shared transport tier, not this helper."
    },
    {
      "blocked_by": [],
      "closed": null,
      "follows": null,
      "horizon": "unset",
      "id": "R-0005",
      "log": [
        {
          "date": "2026-09-28",
          "from": null,
          "reason": "added",
          "state": null,
          "to": "unset"
        }
      ],
      "origin": "user:2026-09-28:adr-producer",
      "path": "docs/roadmap/roadmap.md",
      "rank": 0,
      "ready": true,
      "severity": null,
      "spec": null,
      "state": "idea",
      "tags": [
        "adr",
        "producer"
      ],
      "title": "ADR authoring proposes deferred limits",
      "why": ""
    },
    {
      "blocked_by": [],
      "closed": {
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "date": "2026-09-27",
        "reason": null
      },
      "follows": null,
      "horizon": "now",
      "id": "R-0001",
      "log": [
        {
          "date": "2026-09-20",
          "from": null,
          "reason": "harvested by adopt",
          "state": {
            "from": "idea",
            "to": "planned"
          },
          "to": "next"
        },
        {
          "date": "2026-09-22",
          "from": "next",
          "reason": "started",
          "state": {
            "from": "planned",
            "to": "active"
          },
          "to": "now"
        },
        {
          "date": "2026-09-27",
          "from": "now",
          "reason": "commit 0123456789abcdef0123456789abcdef01234567",
          "state": null,
          "to": "done"
        }
      ],
      "origin": "user:2026-09-20:wiki-constants-home",
      "path": "docs/roadmap/archive/0001-home-the-wiki-page-type-constants-in-wikilib.md",
      "rank": null,
      "ready": null,
      "severity": null,
      "spec": null,
      "state": "done",
      "tags": [
        "wiki"
      ],
      "title": "Home the wiki page-type constants in _wikilib",
      "why": "Four page-type constants were typed in three files."
    },
    {
      "blocked_by": [],
      "closed": {
        "commit": null,
        "date": "2026-09-28",
        "reason": "a second priority axis (adr 0022)"
      },
      "follows": null,
      "horizon": "later",
      "id": "R-0002",
      "log": [
        {
          "date": "2026-09-21",
          "from": null,
          "reason": "added",
          "state": null,
          "to": "later"
        },
        {
          "date": "2026-09-28",
          "from": "later",
          "reason": "a second priority axis (adr 0022)",
          "state": null,
          "to": "dropped"
        }
      ],
      "origin": "user:2026-09-21:priority-labels",
      "path": "docs/roadmap/archive/0002-p0-p3-priority-labels.md",
      "rank": null,
      "ready": null,
      "severity": null,
      "spec": null,
      "state": "dropped",
      "tags": [],
      "title": "P0-P3 priority labels",
      "why": "Proposed as a finer priority axis inside a lane."
    }
  ],
  "schema": "roadmap-export/1",
  "scope": {
    "closed": true,
    "closed_since": null,
    "open": true
  },
  "source": {
    "dirty": null,
    "head": null
  },
  "wip_limit": {
    "now": 3
  }
}
'''


def _export(path, *extra, **kwargs):
    """(problems, the parsed export or None) of one `export` on stdout."""
    code, out, err = cli(path, "export", *extra, **kwargs)
    problems = shape_problems(code, err, 0)
    try:
        return problems, json.loads(out)
    except ValueError as exc:
        return problems + ["stdout is not JSON: %s" % exc], None


def _ids(doc):
    return [item.get("id") for item in (doc or {}).get("items", [])]


def group_h(suite, mod, work, wiki):
    _h_golden(suite, mod, work)
    _h_scope(suite, mod, work)
    _h_refusals(suite, mod, work)
    _h_non_utf8(suite, mod, work)
    _h_early_close(suite, mod, work)
    _h_ready(suite, mod, work)
    _h_source(suite, mod, work)
    _h_out(suite, mod, work)
    _h_planted_config(suite, mod, work)
    _h_planted_bare_repo(suite, mod, work)


def _h_golden(suite, mod, work):
    cid = "h-golden-both-routes-twice"
    path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
    case = os.path.join(work.path, cid)
    want = GOLDEN_EXPORT.encode("utf-8")
    roadmap_before = read_bytes(path)
    archive_before = H.file_digests(_archive_dir(path))
    listing_before = sorted(os.listdir(case))
    problems = []
    outs = []
    for attempt in (1, 2):
        code, raw, err = _spawn_raw([sys.executable, TARGET, "export",
                                     "--file", path, "--today", TODAY],
                                    os.path.dirname(path))
        problems += problem_if(code != 0, "stdout run %d exited %r: %r"
                               % (attempt, code, err[-200:]))
        problems += problem_if(raw != want, "stdout run %d is not the golden: "
                               "%s" % (attempt, first_diff(
                                   raw.decode("utf-8", "replace"),
                                   GOLDEN_EXPORT)))
        out_file = os.path.join(case, "out-%d.json" % attempt)
        outs.append("out-%d.json" % attempt)
        code, raw, err = _spawn_raw([sys.executable, TARGET, "export",
                                     "--out", out_file, "--file", path,
                                     "--today", TODAY],
                                    os.path.dirname(path))
        problems += problem_if(code != 0, "--out run %d exited %r: %r"
                               % (attempt, code, err[-200:]))
        problems += problem_if(raw.decode("utf-8", "replace").strip()
                               != "exported: %s" % out_file,
                               "--out run %d printed %r" % (attempt, raw))
        got = read_bytes(out_file) if os.path.isfile(out_file) else b""
        problems += problem_if(got != want, "--out run %d is not the golden: "
                               "%s" % (attempt, first_diff(
                                   got.decode("utf-8", "replace"),
                                   GOLDEN_EXPORT)))
    suite.record(GH, cid, problems,
                 detail=[_d("oracle", "the 5.3 document byte for byte, on "
                                      "stdout AND through --out, twice each "
                                      "(R10, L5)")])
    problems = problem_if(read_bytes(path) != roadmap_before,
                          "roadmap.md changed")
    problems += problem_if(H.file_digests(_archive_dir(path)) != archive_before,
                           "archive/ changed")
    problems += problem_if(sorted(os.listdir(case))
                           != sorted(listing_before + outs),
                           "the case directory holds %r" % sorted(
                               os.listdir(case)))
    problems += _no_temp(path)
    suite.record(GH, "h-export-writes-only-out", problems,
                 detail=[_d("oracle", "the roadmap and archive digests "
                                      "unchanged; --out wrote only its path")])


def _h_scope(suite, mod, work):
    path = stage_roadmap(work, "h-scope", FIXTURE_52, BOTH_ARCHIVES)
    live = ["R-0003", "R-0004", "R-0005"]
    for cid, extra, ids, scope, counts in (
            ("h-open-only", ("--open-only",), live,
             {"open": True, "closed": False, "closed_since": None},
             {"done": 0, "dropped": 0}),
            ("h-closed-since", ("--closed-since", "2026-09-28"),
             live + ["R-0002"],
             {"open": True, "closed": True, "closed_since": "2026-09-28"},
             {"done": 0, "dropped": 1}),
            ("h-closed-since-future", ("--closed-since", "2027-01-01"), live,
             {"open": True, "closed": True, "closed_since": "2027-01-01"},
             {"done": 0, "dropped": 0})):
        problems, doc = _export(path, *extra)
        if doc is not None:
            problems += problem_if(_ids(doc) != ids, "items %r, want %r"
                                   % (_ids(doc), ids))
            problems += problem_if(doc.get("scope") != scope, "scope %r"
                                   % doc.get("scope"))
            want = dict({"now": 1, "next": 1, "later": 0, "untriaged": 1},
                        **counts)
            problems += problem_if(doc.get("counts") != want,
                                   "counts %r, want %r" % (doc.get("counts"),
                                                           want))
        suite.record(GH, cid, problems,
                     detail=[_d("why", "counts describe the items IN the "
                                       "output (ADR 0018)")])


def _h_refusals(suite, mod, work):
    path = stage_roadmap(work, "h-refusals", FIXTURE_52, BOTH_ARCHIVES)
    for cid, argv, tokens, absent in (
            ("h-closed-since-calendar", ["--closed-since", "2026-02-30"],
             ["--closed-since: '2026-02-30' is not a calendar date"], ()),
            ("h-closed-since-arabic-indic", ["--closed-since", ARABIC_DATE],
             ["--closed-since", "is not a YYYY-MM-DD date"], ("calendar",)),
            ("h-closed-since-trailing-newline",
             ["--closed-since", "2026-09-28\n"],
             ["--closed-since '2026-09-28\\n' is not a YYYY-MM-DD date"], ()),
            ("h-export-flags", ["--closed-since", "2026-09-28",
                                "--open-only"],
             ["--closed-since and --open-only are exclusive"], ())):
        record_refusal(suite, GH, cid, path, ["export"] + argv, tokens,
                       "M3, R35", absent=absent)


def _h_non_utf8(suite, mod, work):
    cid = "h-non-utf8-wiki-root"
    case = sandbox_path(work.subdir(cid))
    raw_root = os.fsencode(case) + b"/d\xff"
    try:
        os.makedirs(raw_root + b"/roadmap/archive")
    except OSError as exc:
        info_skip(suite, GH, cid, "the filesystem refuses the name (%s)"
                  % (exc.strerror or exc))
        return
    root = sandbox_path(os.fsdecode(raw_root))
    write_file(os.path.join(root, "roadmap", "roadmap.md"), FIXTURE_52)
    for name, body in BOTH_ARCHIVES.items():
        write_file(os.path.join(root, "roadmap", "archive", name), body)
    target = raw_root + b"/roadmap/roadmap.md"
    out_raw = os.fsencode(case) + b"/out.json"
    base = [sys.executable.encode(), TARGET.encode(), b"--file", target,
            b"--today", TODAY.encode(), b"export"]
    code1, out1, err1 = _spawn_raw(base, case)
    code2, _out2, err2 = _spawn_raw(base + [b"--out", out_raw], case)
    text = err1.decode("utf-8", "replace")
    problems = problem_if(code1 != 2 or code2 != 2, "exits %r / %r, want 2 / 2"
                          % (code1, code2))
    problems += problem_if(err1 != err2, "the two routes refuse differently: "
                           "%r vs %r" % (err1, err2))
    problems += problem_if(len(text.strip().splitlines()) != 1
                           or "Traceback" in text, "stderr %r" % text)
    problems += missing_tokens(text, ["export path", "carries a lone "
                                      "surrogate (U+DCFF; a directory name "
                                      "that is not UTF-8) -- rename it; the "
                                      "export is UTF-8 JSON"])
    problems += problem_if(out1, "stdout carried %d bytes" % len(out1))
    problems += problem_if(os.path.lexists(os.fsdecode(out_raw)),
                           "--out was created")
    suite.record(GH, cid, problems,
                 detail=[_d("argv", "bytes, as in group L (L4, R36)"),
                         _d("stderr", text.strip())])


def _h_early_close(suite, mod, work):
    cid = "h-reader-closes-early"
    count = 4 * PIPE_BYTES // 200 + 1
    text = _bulk_roadmap(count)
    path = stage_roadmap(work, cid, text)
    cap = getattr(mod, "PAGE_MAX_BYTES", 4 * 1024 * 1024)
    problems = problem_if(len(text.encode("utf-8")) >= cap,
                          "the fixture would trip the read cap, not the pipe")
    proc = subprocess.Popen([sys.executable, TARGET, "export", "--file", path,
                             "--today", TODAY], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=os.path.dirname(path),
                            env=H.child_env(_child_env(None)))
    try:
        proc.stdout.readline()
        proc.stdout.close()
        code = proc.wait(timeout=60)
        err = proc.stderr.read().decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        code, err = None, ""
        problems.append("the child hung after the reader closed")
    finally:
        proc.stderr.close()
    problems += problem_if(code != 0, "exit %r, want 0" % code)
    problems += problem_if("Traceback" in err or "Exception ignored" in err,
                           "stderr %r" % err[-300:])
    suite.record(GH, cid, problems,
                 detail=[_d("fixture", "%d items, an export many times a "
                                       "%d-byte pipe" % (count, PIPE_BYTES)),
                         _d("why", "M2, R34: export | head exits 0")])


def _h_ready(suite, mod, work):
    cid = "h-ready-through-the-archive"
    path, problems, _outs = build(work, cid, [
        ("add", "--title", "X", "--origin", "user:h-x",
         "--blocked-by", "R-0001"),
        ("add", "--title", "Y", "--origin", "user:h-y",
         "--blocked-by", "R-0002"),
        ("add", "--title", "Z", "--origin", "user:h-z",
         "--blocked-by", "R-0001,R-0002")], archive=BOTH_ARCHIVES)
    if not problems:
        got, doc = _export(path)
        problems += got
        ready = dict((i.get("id"), i.get("ready"))
                     for i in (doc or {}).get("items", []))
        want = {"R-0003": True, "R-0004": True, "R-0005": True,
                "R-0001": None, "R-0002": None}
        problems += problem_if(ready != want, "ready %r, want %r"
                               % (ready, want))
    suite.record(GH, cid, problems,
                 detail=[_d("why", "R23: done AND dropped blockers are "
                                   "closed; closed items carry null")])


def _committed_repo(work, cid, object_format=None, extra=None):
    """(root, path, None) for a sandbox repo with FIXTURE_52 + BOTH_ARCHIVES
    (plus `extra` files under the roadmap directory) committed, or
    (None, None, why)."""
    root = sandbox_path(work.subdir(cid))
    if not git_repo(root, object_format=object_format):
        return None, None, "git init%s failed" % (
            " --object-format=%s" % object_format if object_format else "")
    path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
    for name, body in sorted((extra or {}).items()):
        write_file(os.path.join(os.path.dirname(path), name), body)
    problems = git_commit_all(root)
    if problems:
        return None, None, problems[0]
    return root, path, None


def _source(path, **kwargs):
    problems, doc = _export(path, **kwargs)
    return problems, (doc or {}).get("source")


def _h_source(suite, mod, work):
    root, path, why = _committed_repo(work, "h-source-clean")
    if root is None:
        for cid in ("h-source-clean", "h-dirty-modified", "h-dirty-untracked",
                    "h-dirty-deleted-archive", "h-dirty-outside-scope",
                    "h-dirty-sha256-clean", "h-dirty-unborn-head",
                    "h-dirty-symlink-to-fifo", "h-dirty-tracked-symlink",
                    "h-dirty-unreadable-file", "h-dirty-unlistable-subdir",
                    "h-dirty-cr-name"):
            info_skip(suite, GH, cid, "git is not usable here (%s)" % why)
        return
    # L10: a git that predates --show-object-format gives a null dirty for
    # every case; each case that asserts true/false is then INFO, and ONE case
    # asserts the null instead.
    rc, fmt, _err = git_run(root, "rev-parse", "--show-object-format")
    gate = rc == 0 and fmt.strip() in ("sha1", "sha256")
    if not gate:
        problems, source = _source(path)
        problems += problem_if((source or {}).get("dirty") is not None,
                               "dirty is %r, want null for this git"
                               % (source or {}).get("dirty"))
        suite.record(GH, "h-dirty-null-without-object-format", problems,
                     detail=[_d("why", "L10: rev-parse --show-object-format "
                                       "answered rc %d, %r" % (rc, fmt))])

    def judge(cid, path, want, detail, **kwargs):
        problems, source = _source(path, **kwargs)
        dirty = (source or {}).get("dirty")
        if not gate:
            suite.record(GH, cid, status=H.INFO,
                         detail=["skipped: this git lacks --show-object-format "
                                 "(L10); dirty was %r" % dirty])
            return
        problems += problem_if(dirty is not want, "dirty is %r, want %r"
                               % (dirty, want))
        suite.record(GH, cid, problems, detail=[_d("scenario", detail)])

    problems, source = _source(path)
    short = head_sha(root, short=True)
    problems += problem_if((source or {}).get("head") != short,
                           "head %r, want %r" % ((source or {}).get("head"),
                                                 short))
    if gate:
        problems += problem_if((source or {}).get("dirty") is not False,
                               "dirty %r on a clean tree, want false (and "
                               "proof that ls-tree took -- as a separator)"
                               % (source or {}).get("dirty"))
    suite.record(GH, "h-source-clean", problems,
                 detail=[_d("oracle", "head == git rev-parse --short HEAD; "
                                      "dirty false")])

    root, path, why = _committed_repo(work, "h-dirty-modified")
    code, _out, err = cli(path, "wip", "4", "--reason", "x")
    judge("h-dirty-modified", path, True, "roadmap.md modified by wip")

    root, path, why = _committed_repo(work, "h-dirty-untracked")
    write_file(os.path.join(os.path.dirname(path), "notes.txt"), "new\n")
    judge("h-dirty-untracked", path, True, "an untracked file under roadmap/")

    root, path, why = _committed_repo(work, "h-dirty-deleted-archive")
    os.remove(os.path.join(_archive_dir(path), ARCHIVE_2_NAME))
    judge("h-dirty-deleted-archive", path, True, "a tracked archive file "
                                                 "deleted")

    root, path, why = _committed_repo(work, "h-dirty-outside-scope")
    write_file(os.path.join(root, "outside.txt"), "not the roadmap\n")
    write_file(os.path.join(root, "docs", "other.md"), "not the roadmap\n")
    judge("h-dirty-outside-scope", path, False, "changes OUTSIDE the roadmap "
                                                "directory only")

    root, path, why = _committed_repo(work, "h-dirty-sha256-clean",
                                      object_format="sha256")
    if root is None:
        info_skip(suite, GH, "h-dirty-sha256-clean", why)
    else:
        judge("h-dirty-sha256-clean", path, False, "a clean SHA-256 repo: "
                                                   "the blob hash follows "
                                                   "the object format")

    cid = "h-dirty-unborn-head"
    root = sandbox_path(work.subdir(cid))
    if git_repo(root, commit=False):
        path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
        problems, source = _source(path)
        problems += problem_if(source != {"head": None, "dirty": None},
                               "source %r, want both null" % (source,))
        suite.record(GH, cid, problems,
                     detail=[_d("fixture", "git init, no commit")])
    else:
        info_skip(suite, GH, cid, "git init failed")

    cid = "h-dirty-symlink-to-fifo"
    if can_symlink() and hasattr(os, "mkfifo"):
        root, path, why = _committed_repo(work, cid,
                                          extra={"notes.md": "notes\n"})
        fifo = _fifo(os.path.join(root, "fifo"))
        notes = os.path.join(os.path.dirname(path), "notes.md")
        os.remove(notes)
        os.symlink(fifo, notes)
        judge(cid, path, True, "a tracked file replaced by a symlink to a "
                               "FIFO: never opened (test-side timeout)",
              timeout=20)
    else:
        info_skip(suite, GH, cid, "no os.symlink / os.mkfifo")

    cid = "h-dirty-tracked-symlink"
    if can_symlink():
        root = sandbox_path(work.subdir(cid))
        path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
        link = os.path.join(os.path.dirname(path), "link")
        os.symlink("roadmap.md", link)
        if not git_repo(root) or git_commit_all(root):
            info_skip(suite, GH, cid, "git init/commit failed")
        else:
            judge(cid, path, False, "a symlink committed as mode 120000, "
                                    "unchanged: os.readlink hashes to its "
                                    "blob")
            os.remove(link)
            os.symlink("archive", link)
            judge(cid + "-retargeted", path, True, "the same symlink "
                                                   "retargeted")
    else:
        info_skip(suite, GH, cid, "no os.symlink")

    cid = "h-dirty-unreadable-file"
    if is_root():
        info_skip(suite, GH, cid, "root ignores the permission bits")
    else:
        root, path, why = _committed_repo(work, cid,
                                          extra={"notes.md": "notes\n"})
        notes = os.path.join(os.path.dirname(path), "notes.md")
        os.chmod(notes, 0o000)
        try:
            judge(cid, path, True, "a tracked file chmod 000: a per-entry "
                                   "OSError is 'possibly different'")
        finally:
            os.chmod(notes, 0o644)

    cid = "h-dirty-unlistable-subdir"
    if is_root():
        info_skip(suite, GH, cid, "root ignores the permission bits")
    else:
        root, path, why = _committed_repo(work, cid)
        sub = sandbox_path(work.subdir(cid, "docs", "roadmap", "sub"))
        write_file(os.path.join(sub, "untracked.txt"), "x\n")
        os.chmod(sub, 0o000)
        try:
            judge(cid, path, True, "an untracked subdirectory chmod 000: the "
                                   "re-raising onerror (L3) -- a silent one "
                                   "would skip it and report false")
        finally:
            os.chmod(sub, 0o700)

    cid = "h-dirty-cr-name"
    try:
        root, path, why = _committed_repo(work, cid,
                                          extra={"a\rb.txt": "cr\n"})
    except OSError as exc:
        root, why = None, "the filesystem refuses the name (%s)" % exc
    if root is None:
        info_skip(suite, GH, cid, why)
    else:
        judge(cid, path, False, "a committed name holding \\r: the bytes-mode "
                                "ls-tree is not newline-translated")


def _h_out(suite, mod, work):
    cid = "h-out"
    path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
    case = os.path.join(work.path, cid)
    missing = os.path.join(case, "missing", "out.json")
    record_refusal(suite, GH, "h-out-missing-parent", path,
                   ["export", "--out", missing],
                   ["--out %s: directory %s does not exist"
                    % (missing, os.path.dirname(missing))], "round-3 H1",
                   after=lambda: problem_if(os.path.lexists(
                       os.path.dirname(missing)), "missing/ was created"))
    blocker = write_file(os.path.join(case, "file.txt"), "a file\n")
    under = os.path.join(blocker, "out.json")
    record_refusal(suite, GH, "h-out-parent-is-a-file", path,
                   ["export", "--out", under],
                   ["--out %s: directory %s does not exist" % (under, blocker)],
                   "round-3 H1: a parent that is not a directory")
    record_refusal(suite, GH, "h-out-is-the-roadmap", path,
                   ["export", "--out", path],
                   ["--out %s is the roadmap or its archive -- refusing"
                    % path], "never overwrite the roadmap")
    inside = os.path.join(_archive_dir(path), "x.json")
    record_refusal(suite, GH, "h-out-inside-archive", path,
                   ["export", "--out", inside],
                   ["--out %s is the roadmap or its archive -- refusing"
                    % inside], "the archive holds pages only",
                   after=lambda: problem_if(os.path.lexists(inside),
                                            "x.json was created"))
    record_refusal(suite, GH, "h-out-is-a-directory", path,
                   ["export", "--out", case],
                   ["--out %s is a directory" % case], "a directory")
    if can_symlink():
        target = write_file(os.path.join(case, "target.json"), "keep\n")
        link = os.path.join(case, "link.json")
        os.symlink(target, link)
        record_refusal(suite, GH, "h-out-symlink", path,
                       ["export", "--out", link],
                       ["--out %s is a symlink -- refusing" % link],
                       "S-L2, R28",
                       after=lambda: problem_if(read_utf8(target) != "keep\n",
                                                "the link target changed"))
    else:
        info_skip(suite, GH, "h-out-symlink", "no os.symlink")
    cid = "h-out-readonly-parent"
    if is_root():
        info_skip(suite, GH, cid, "root ignores the permission bits")
        return
    ro = sandbox_path(work.subdir("h-out", "ro"))
    os.chmod(ro, 0o500)
    try:
        code, _out, err = cli(path, "export", "--out",
                              os.path.join(ro, "out.json"))
    finally:
        os.chmod(ro, 0o700)
    problems = shape_problems(code, err)
    problems += problem_if(not err.startswith("roadmap: cannot create a "
                                              "temporary file in"),
                           "stderr %r" % err.strip())
    problems += problem_if(os.listdir(ro), "ro/ holds %r" % os.listdir(ro))
    suite.record(GH, cid, problems,
                 detail=[_d("why", "R32, NFR-13: write_atomic's io_fail "
                                   "backstop"), _d("stderr", err.strip())])


def _h_planted_config(suite, mod, work):
    cid = "h-planted-config-never-runs"
    root, path, why = _committed_repo(work, cid)
    if root is None:
        for name in (cid, "h-control-fsmonitor-live",
                     "h-control-clean-filter-live"):
            info_skip(suite, GH, name, "git is not usable here (%s)" % why)
        return
    tools = sandbox_path(work.subdir(cid + "-tools"))
    fsmon_marker = os.path.join(tools, "FSMON_RAN")
    clean_marker = os.path.join(tools, "CLEAN_RAN")
    fsmon = write_file(os.path.join(tools, "fsmon.sh"),
                       "#!/bin/sh\ntouch '%s'\n" % fsmon_marker)
    os.chmod(fsmon, 0o755)
    clean = write_file(os.path.join(tools, "clean.sh"),
                       "#!/bin/sh\ntouch '%s'\ncat\n" % clean_marker)
    os.chmod(clean, 0o755)
    problems = []
    for key, value in (("core.fsmonitor", fsmon), ("filter.pwn.clean", clean)):
        rc, _out, err = git_run(root, "config", key, value)
        problems += problem_if(rc != 0, "git config %s failed: %s"
                               % (key, err.strip()))
    write_file(os.path.join(root, ".gitattributes"), "*.md filter=pwn\n")
    code, _out, err = cli(path, "wip", "4", "--reason", "x")
    problems += shape_problems(code, err, 0)
    got, source = _source(path)
    problems += got
    rc, fmt, _err = git_run(root, "rev-parse", "--show-object-format")
    if rc == 0 and fmt.strip() in ("sha1", "sha256"):
        problems += problem_if((source or {}).get("dirty") is not True,
                               "dirty %r, want true" % (source or {}).get(
                                   "dirty"))
    problems += problem_if(os.path.exists(fsmon_marker),
                           "roadmap.py ran the planted fsmonitor")
    problems += problem_if(os.path.exists(clean_marker),
                           "roadmap.py ran the planted clean filter")
    suite.record(GH, cid, problems,
                 detail=[_d("planted", "core.fsmonitor and a clean filter "
                                       "on *.md, roadmap.md modified "
                                       "(S-L1, S2-2, R27)")])
    rel = os.path.relpath(path, root)
    for control, argv, marker in (
            ("h-control-fsmonitor-live", ["status"], fsmon_marker),
            ("h-control-clean-filter-live", ["diff", "--", rel],
             clean_marker)):
        git_run(root, *argv, plain=True)
        if os.path.exists(marker):
            suite.record(GH, control, [],
                         detail=[_d("oracle", "the suite's own git %s fired "
                                              "the planted setting, so its "
                                              "absence above is evidence"
                                   % argv[0])])
        else:
            suite.record(GH, control, status=H.INFO,
                         detail=["this git did not fire the planted setting "
                                 "through `git %s`, so its half of the case "
                                 "proves nothing here" % argv[0]])


# A bare repository planted as TRACKED files in the roadmap directory: clone
# transfers every one of them, config included, and git's discovery from
# cwd=docs/roadmap tests that directory for HEAD + objects/ + refs/ before it
# ever reaches the outer .git.  The planted ref and core.abbrev are the probe:
# a 12-character run of "d" in the export's head means both were honoured.
PLANTED_BARE_SHA = "d" * 40
PLANTED_BARE_ABBREV = 12


def _h_planted_bare_repo(suite, mod, work):
    cid = "h-planted-bare-repo-refused"
    for parts in (("objects", "info"), ("refs", "heads")):
        work.subdir(cid, "docs", "roadmap", *parts)
    root, path, why = _committed_repo(work, cid, extra={
        "HEAD": "ref: refs/heads/main\n",
        os.path.join("objects", "info", "keep"): "",
        os.path.join("refs", "heads", "main"): PLANTED_BARE_SHA + "\n",
        "config": "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"
                  "\tabbrev = %d\n" % PLANTED_BARE_ABBREV})
    if root is None:
        info_skip(suite, GH, cid, "git is not usable here (%s)" % why)
        return
    planted = PLANTED_BARE_SHA[:PLANTED_BARE_ABBREV]
    # The oracle first: the suite's own git, WITHOUT the pin, must discover
    # the plant, or its absence below proves nothing.
    rc, out, err = git_run(os.path.dirname(path), "rev-parse", "--short",
                           "HEAD", plain=True)
    if rc != 0 or out.strip() != planted:
        suite.record(GH, cid, status=H.INFO,
                     detail=["this git did not discover the planted bare "
                             "repository without safe.bareRepository (rc %d, "
                             "%r, %r), so the case proves nothing here"
                             % (rc, out.strip(), err.strip())])
        return
    problems, source = _source(path)
    head = (source or {}).get("head")
    problems += problem_if(head == planted, "head %r is the planted "
                           "repository's HEAD at its planted core.abbrev -- "
                           "its config was honoured" % head)
    problems += problem_if(head not in (None, head_sha(root, short=True)),
                           "head %r is neither null nor the outer HEAD" % head)
    suite.record(GH, cid, problems,
                 detail=[_d("planted", "HEAD, objects/, refs/heads/main and "
                                       "config (core.abbrev = %d) tracked "
                                       "under docs/roadmap" % PLANTED_BARE_ABBREV),
                         _d("oracle", "the suite's own git without the pin "
                                      "answered %r" % planted),
                         _d("head", repr(head))])


# ---------------------------------------------------------------------------
# J. negative controls: each oracle above, pointed at a planted defect
# ---------------------------------------------------------------------------

def group_j(suite, mod, work, wiki):
    cid = "j-control-quoted-title"
    got = need(suite, GJ, cid, mod, "load_state", "render_roadmap")
    if got is not None:
        load_state, render_roadmap = got
        path = stage_roadmap(work, cid, EXPECTED_B)
        code, state, _out, err = in_process(load_state, path)
        problems = problem_if(code is not None, "load_state refused: %s"
                              % err.strip())
        if code is None:
            planted = state._replace(meta=dict(state.meta, title='"quoted"'))
            fired, clean = [], []
            for label, parser in (("_wikilib", wiki.lib.parse_frontmatter),
                                  ("server", wiki.server.parse_frontmatter)):
                if parser(render_roadmap(planted)).get("title") \
                        != planted.meta["title"]:
                    fired.append(label)
                if parser(render_roadmap(state)).get("title") \
                        != state.meta["title"]:
                    clean.append(label)
            problems += problem_if(fired != ["_wikilib", "server"],
                                   "the C oracle missed the planted quoted "
                                   "title in %r"
                                   % sorted({"_wikilib", "server"}
                                            - set(fired)))
            problems += problem_if(clean, "the oracle fires on the real "
                                          "writer too: %r" % clean)
        suite.record(GJ, cid, problems,
                     detail=[_d("planted", "a writer emitting "
                                           "title: \"quoted\""),
                             _d("oracle", "C: each wiki parser reads what the "
                                          "model holds")])

    cid = "j-control-render-item-drops-severity"
    got = need(suite, GJ, cid, mod, "load_state", "render_item")
    if got is not None:
        ctl = H.load_module_from_path("roadmap_planted_severity", TARGET)
        real_item = ctl.render_item
        ctl.render_item = lambda item: [line for line in real_item(item)
                                        if not line.startswith("severity:")]
        path = stage_roadmap(work, cid, EXPECTED_B)
        code, state, _out, err = in_process(ctl.load_state, path)
        problems = problem_if(code is not None, "load_state refused: %s"
                              % err.strip())
        if code is None:
            text = ctl.render_roadmap(state)
            code, parsed, _out, err = in_process(ctl.parse_roadmap, text,
                                                 DISPLAY)
            detected = (text != EXPECTED_B or code is not None
                        or parsed != (state.meta, state.lanes))
            problems += problem_if(not detected, "the B round-trip oracle "
                                   "did not detect the dropped severity")
        suite.record(GJ, cid, problems,
                     detail=[_d("planted", "render_item without the severity "
                                           "line"),
                             _d("oracle", "B: the file equals the fixture and "
                                          "parses back to the model")])

    cid = "j-control-close-roadmap-first"
    got = need(suite, GJ, cid, mod, "load_state", "resolve", "write_atomic",
               "render_roadmap")
    if got is not None:
        path = stage_roadmap(work, cid, EXPECTED_B)

        def planted_close(p, n):
            state = mod.load_state(p)
            _item, lane, index = mod.resolve(state, n)
            lanes = dict((k, list(v)) for k, v in state.lanes.items())
            del lanes[lane][index]
            mod.write_atomic(p, mod.render_roadmap(state._replace(
                lanes=lanes)), state.snapshot)          # roadmap.md FIRST
            raise _Crash("injected before the archive write")

        crashed = False
        with captured():
            try:
                planted_close(path, 3)
            except _Crash:
                crashed = True
        problems = problem_if(not crashed, "the planted close did not reach "
                                           "the crash")
        problems += problem_if(not _item_survives(path, "R-0003"),
                               "the G crash oracle did not detect the lost "
                               "item")
        suite.record(GJ, cid, problems,
                     detail=[_d("planted", "a close that writes roadmap.md "
                                           "BEFORE the archive, crashing "
                                           "between"),
                             _d("oracle", "G: the item is live or archived "
                                          "after any crash")])

    cid = "j-control-export-wall-clock"
    got = need(suite, GJ, cid, mod, "cmd_export", "build_export")
    if got is not None:
        ctl = H.load_module_from_path("roadmap_planted_clock", TARGET)
        real_build = ctl.build_export

        def clocked(*args, **kwargs):
            doc = real_build(*args, **kwargs)
            doc["source"]["head"] = "%d" % time.time_ns()
            return doc

        ctl.build_export = clocked
        path = stage_roadmap(work, cid, FIXTURE_52, BOTH_ARCHIVES)
        runs = [call_module(ctl, "cmd_export", path, None, None, False)
                for _ in (1, 2)]
        real = [call_module(mod, "cmd_export", path, None, None, False)
                for _ in (1, 2)]
        problems = problem_if(runs[0][1] == runs[1][1], "the twice-identical "
                              "oracle did not detect the wall clock")
        problems += problem_if(real[0][1] != real[1][1] or not real[0][1],
                               "the real export is not identical twice")
        suite.record(GJ, cid, problems,
                     detail=[_d("planted", "build_export writing the wall "
                                           "clock into source"),
                             _d("oracle", "H: two runs, identical bytes")])


# ---------------------------------------------------------------------------
# I. refusals, staged input, file shapes, containment, dates
# ---------------------------------------------------------------------------

GRAMMAR_CASES = (
    ("no-frontmatter", lambda t: t[4:], ["no frontmatter"]),
    ("carriage-return", ("state: idea\nhorizon: next",
                         "state: idea\r\nhorizon: next"),
     ["carriage return in the file"]),
    ("fm-type", ("type: roadmap\n", "type: wiki\n"),
     ["frontmatter type is 'wiki', want roadmap"]),
    ("fm-unknown-key", ("title: Roadmap\n", "title: Roadmap\nowner: x\n"),
     ["unknown frontmatter key 'owner'"]),
    ("fm-missing-key", ("title: Roadmap\n", ""),
     ["frontmatter key 'title' is missing"]),
    ("fm-status", ("status: active\n", "status: current\n"),
     ["status 'current' is not one of draft, active, deprecated"]),
    ("fm-wip-now", ("wip_now: 4\n", "wip_now: 0\n"),
     ["wip_now must be a positive integer, got '0'"]),
    # Past 4300 digits int() raises ValueError (3.11+): the bound must refuse
    # first, one line, never a traceback.
    ("fm-wip-now-huge", ("wip_now: 4\n", "wip_now: %s\n" % ("9" * 5000)),
     ["wip_now must be a positive integer, got '999", "(at most 9 digits)"]),
    ("fm-comment", ("name: roadmap\n", "name: roadmap\n# hello\n"),
     ["unexpected comment line '# hello'"]),
    ("unterminated-region", (END_PREFIX + " -->\n", ""),
     ["unterminated summary region"]),
    ("text-before-lanes", (END_PREFIX + " -->\n",
                           END_PREFIX + " -->\nstray text\n"),
     ["unexpected text before the first lane heading: 'stray text'"]),
    ("unknown-lane", ("# later\n", "# someday\n"),
     ["unknown lane heading '# someday'"]),
    ("lane-out-of-order", lambda t: t.replace("# later\n\n", "", 1)
     + "\n# later\n", ["lane heading '# inbox' out of order"]),
    ("lane-missing", ("# later\n\n", ""), ["lane heading '# later' is missing"]),
    ("item-heading", ("## R-0004 %s Pin" % DOT, "## R-4 %s Pin" % DOT),
     ["malformed item heading"]),
    ("unknown-key", ("severity: low\n", "priority: low\n"),
     ["unknown key 'priority'"]),
    ("duplicate-key", ("tags: [wiki]\n", "tags: [wiki]\ntags: [wiki]\n"),
     ["duplicate key 'tags'"]),
    ("key-order", ("state: idea\nhorizon: next\n",
                   "horizon: next\nstate: idea\n"),
     ["key 'state' out of order (want state, horizon, origin, spec, "
      "blocked_by, follows, severity, tags)"]),
    ("missing-key", ("origin: user:2026-09-28:export-golden\n", ""),
     ["R-0004 has no origin line"]),
    ("inline-list", ("tags: [adr, producer]", "tags: adr, producer"),
     ["tags must be an inline list [...], got 'adr, producer'"]),
    ("quoted-value", ("origin: user:2026-09-28:export-golden",
                      'origin: "user:2026-09-28:export-golden"'),
     ["quoted value", "(roadmap.py never writes one)"]),
    ("horizon-lane", ("horizon: next\norigin: user:2026-09-28:export-golden",
                      "horizon: later\norigin: user:2026-09-28:export-golden"),
     ["R-0004 sits in lane next but says horizon: later"]),
    ("appears-twice", ("## R-0004 %s Pin" % DOT, "## R-0002 %s Pin" % DOT),
     ["R-0002 appears twice"]),
    ("state", ("state: idea\nhorizon: next", "state: done\nhorizon: next"),
     ["state 'done' is not one of idea, planned, active"]),
    ("missing-log", ("tags: []\n\n### Log\n\n- 2026-09-28 new->next: added\n",
                     "tags: []\n"), ["missing ### Log"]),
    ("malformed-log", ("- 2026-09-28 new->next: added",
                       "- 2026-09-28 new->soon: added"),
     ["malformed log line"]),
    ("closing-log", ("- 2026-09-28 new->next: added",
                     "- 2026-09-28 next->done: added"),
     ["a closing log line (done) in roadmap.md"]),
    ("log-calendar-date", ("- 2026-09-28 new->next: added",
                           "- 2026-02-30 new->next: added"),
     ["line", "'2026-02-30' is not a calendar date"]),
)

ARCHIVE_CASES = (
    ("stray", {"README.md": "x\n"},
     ["unexpected file in archive/: README.md (want NNNN-slug.md)"]),
    ("name-mismatch", {"0001-other.md": ARCHIVE_1},
     ["archive/0001-other.md: frontmatter name "
      "'0001-home-the-wiki-page-type-constants-in-wikilib' does not match "
      "its filename"]),
    ("id-mismatch", {"0003-x.md": ARCHIVE_1.replace(
        "name: 0001-home-the-wiki-page-type-constants-in-wikilib",
        "name: 0003-x")},
     ["archive/0003-x.md: frontmatter id R-0001 does not match its filename"]),
    ("incomplete", {ARCHIVE_1_NAME: ARCHIVE_1[:len(ARCHIVE_1) // 2]},
     ["archive/%s is incomplete (" % ARCHIVE_1_NAME,
      "a close interrupted mid-write? inspect it by hand"]),
    ("twin", {ARCHIVE_1_NAME: ARCHIVE_1, "0001-copy.md": ARCHIVE_1.replace(
        "name: 0001-home-the-wiki-page-type-constants-in-wikilib",
        "name: 0001-copy")},
     ["R-0001 is archived twice (0001-copy.md, %s) -- keep one by hand"
      % ARCHIVE_1_NAME]),
    ("not-utf8", {ARCHIVE_1_NAME: ARCHIVE_1.encode("utf-8") + b"\xff\n"},
     ["is not valid UTF-8 (byte 0xff at offset"]),
    ("closed-calendar", {ARCHIVE_1_NAME: ARCHIVE_1.replace(
        "closed: 2026-09-27", "closed: 2026-02-30")},
     ["archive/%s closed: '2026-02-30' is not a calendar date"
      % ARCHIVE_1_NAME]),
)

# Arabic-Indic digits (U+0660-U+0669): \d and int() accept them, re.ASCII not.
STAGED_CAP = 65536   # STAGED_MAX_BYTES, spelled here: a staged file is small

ARABIC_DATE = "".join(chr(0x660 + int(c)) if c.isdigit() else c
                      for c in "2026-09-28")
ARABIC_ID = "R-" + "".join(chr(0x660 + int(c)) for c in "0001")

# (cid, argv, tokens, absent) against the canonical fixture.
COMMAND_CASES = (
    ("bad-id", ["show", "R-00x1"], ["'R-00x1' is not an id (want R-NNNN)"], ()),
    ("unknown-id", ["show", "R-0099"], ["unknown id R-0099"], ()),
    ("bad-state", ["move", "R-0003", "next", "--state", "done", "--reason",
                   "x"],
     ["--state 'done' is not one of idea, planned, active -- done and "
      "dropped are reached only through close (invariant 3)"], ()),
    ("reason-flags", ["move", "R-0003", "next", "--reason", "x",
                      "--reason-file", "nope.txt"],
     ["--reason and --reason-file are exclusive"], ()),
    ("rank-flags-none", ["rank", "R-0004"],
     ["rank needs exactly one of --before ID or --top"], ()),
    ("rank-flags-both", ["rank", "R-0004", "--top", "--before", "R-0002"],
     ["rank needs exactly one of --before ID or --top"], ()),
    ("unblock-absent", ["link", "R-0004", "--unblock", "R-0001"],
     ["R-0004 is not blocked by R-0001"], ()),
    ("dedup-live", ["add", "--title", "Again", "--origin",
                    "user:2026-09-28:adr-producer"],
     ["origin 'user:2026-09-28:adr-producer' is already recorded by R-0003 "
      "(docs/roadmap/roadmap.md)"], ()),
    ("empty-reason-flag", ["move", "R-0003", "next", "--reason", "   "],
     ["reason is empty (after stripping whitespace)"], ("internal:",)),
    ("tag", ["add", "--title", "t", "--origin", "user:i-tag", "--tags",
             "Bad_Tag"], ["tag 'Bad_Tag' is not kebab-case"], ()),
    ("spec-shape-link", ["link", "R-0004", "--spec", "Foo_Bar"],
     ["--spec 'Foo_Bar' is not a wiki page name (want kebab-case "
      "[a-z0-9-])"], ("is not a wiki page under",)),
    ("spec-shape-add", ["add", "--title", "t", "--origin", "user:i-spec",
                        "--spec", "a b"],
     ["--spec 'a b' is not a wiki page name"], ("is not a wiki page under",)),
    ("spec-unknown", ["link", "R-0004", "--spec", "nope"],
     ["--spec 'nope' is not a wiki page under"], ()),
    ("why-heading", ["add", "--title", "t", "--origin", "user:i-why",
                     "--why", "a\n## b"],
     ["the why text carries a heading on line 2 ('## b') -- a level 1-3 "
      "heading would split the item"], ()),
    ("why-marker", ["add", "--title", "t", "--origin", "user:i-why",
                    "--why", END_PREFIX + " -->"],
     ["the why text carries a summary marker on line 1"], ()),
    ("why-control", ["add", "--title", "t", "--origin", "user:i-why",
                     "--why", "a\x1bb"],
     ["the why text carries a control character (U+001B) on line 1"], ()),
    ("why-flags", ["add", "--title", "t", "--origin", "user:i-why",
                   "--why", "x", "--why-file", "nope.txt"],
     ["--why and --why-file are exclusive"], ()),
    ("item-file-flags-add", ["add", "--item-file", "nope.json", "--title",
                             "x"],
     ["--item-file excludes --title, --origin, --why, --why-file, --tags, "
      "--severity, --horizon and --reason"], ()),
    ("item-file-flags-link", ["link", "R-0004", "--item-file", "nope.json",
                              "--origin", "x"],
     ["--item-file excludes --origin, --spec, --no-spec and --blocked-by"],
     ()),
    ("add-required", ["add", "--title", "x"],
     ["add needs --title and --origin"], ()),
    ("link-nothing", ["link", "R-0004"],
     ["link needs at least one of --item-file, --spec, --no-spec, --origin, "
      "--blocked-by, --unblock, --follows, --no-follows"], ()),
    ("link-spec-flags", ["link", "R-0004", "--spec", "a", "--no-spec"],
     ["--spec and --no-spec are exclusive"], ()),
    ("link-follows-flags", ["link", "R-0004", "--follows", "R-0001",
                            "--no-follows"],
     ["--follows and --no-follows are exclusive"], ()),
    ("init-exists", ["init"], ["already exists -- init never overwrites"],
     ()),
    ("wip-value", ["wip", "abc", "--reason", "x"],
     ["wip needs a positive integer, got 'abc'"], ()),
    ("wip-value-huge", ["wip", "9" * 5000, "--reason", "x"],
     ["wip needs a positive integer, got '999", "(at most 9 digits)"], ()),
    ("lane-spelling-move", ["move", "R-0001", "soon", "--reason", "x"],
     ["lane 'soon' is not one of now, next, later, inbox"], ()),
    ("horizon-spelling-list", ["list", "--horizon", "soon"],
     ["--horizon 'soon' is not one of now, next, later, inbox"], ()),
    ("horizon-spelling-add", ["add", "--title", "t", "--origin", "user:i-h",
                              "--horizon", "soon"],
     ["--horizon 'soon' is not one of now, next, later, inbox"], ()),
    ("today-calendar", ["list", "--today", "2026-02-30"],
     ["--today: '2026-02-30' is not a calendar date"], ()),
    ("today-arabic-indic", ["list", "--today", ARABIC_DATE],
     ["is not a YYYY-MM-DD date"], ("calendar",)),
    ("today-trailing-newline", ["list", "--today", "2026-09-28\n"],
     ["--today '2026-09-28\\n' is not a YYYY-MM-DD date"], ()),
    ("show-arabic-indic-id", ["show", ARABIC_ID],
     ["is not an id (want R-NNNN)"], ()),
    ("show-trailing-newline-id", ["show", "R-0001\n"],
     ["'R-0001\\n' is not an id (want R-NNNN)"], ()),
    ("rank-before-arabic-indic-id", ["rank", "R-0002", "--before",
                                     ARABIC_ID],
     ["is not an id (want R-NNNN)"], ()),
    ("spec-trailing-newline", ["add", "--title", "t", "--origin",
                               "user:i-spec-nl", "--spec", "x\n"],
     ["spec value", "line separator (U+000A)"], ("is not a wiki page name",)),
)

# (cid, body, argv-before-the-file, tokens): refusals of a staged file.
STAGED_CASES = (
    ("item-file-not-json", "{not json", ["add", "--item-file"],
     ["is not a JSON object ("]),
    ("item-file-array", '["a"]', ["add", "--item-file"],
     ["is not a JSON object ("]),
    ("item-file-unknown-key", '{"title": "t", "origin": "o", "priority": 1}',
     ["add", "--item-file"],
     [": unknown key 'priority' for add (want title, origin, why, tags, "
      "severity, horizon, reason)"]),
    ("item-file-duplicate-key", '{"title": "t", "title": "u", "origin": "o"}',
     ["add", "--item-file"], [": duplicate key 'title'"]),
    ("item-file-lacks-origin", '{"title": "t"}', ["add", "--item-file"],
     [": origin is missing"]),
    ("item-file-title-not-string", '{"title": 3, "origin": "o"}',
     ["add", "--item-file"], [": title must be a string"]),
    ("item-file-tags-string", '{"title": "t", "origin": "o", "tags": "a"}',
     ["add", "--item-file"], [": tags must be a list of strings"]),
    # The plan's 100000 brackets exceed STAGED_MAX_BYTES, so the size refusal
    # would fire first; the deepest file a stager can hand over is the cap.
    ("item-file-nested", "[" * STAGED_CAP, ["add", "--item-file"], None),
    ("item-file-horizon", '{"title": "t", "origin": "o", "horizon": "soon"}',
     ["add", "--item-file"],
     ["--horizon 'soon' is not one of now, next, later, inbox"]),
    ("link-item-file-title", '{"title": "t"}',
     ["link", "R-0004", "--item-file"],
     [": unknown key 'title' for link (want origin, spec, blocked_by)"]),
    ("link-item-file-empty", "{}", ["link", "R-0004", "--item-file"],
     [": link needs at least one of origin, spec, blocked_by"]),
    ("link-item-file-blocked-by-string", '{"blocked_by": "R-0003"}',
     ["link", "R-0004", "--item-file"],
     [": blocked_by must be a list of strings"]),
    ("reason-file-two-lines", "one\ntwo\n",
     ["move", "R-0003", "next", "--reason-file"],
     ["reason value", "line separator (U+000A)"]),
    ("reason-file-only-newline", "\n",
     ["move", "R-0001", "next", "--reason-file"],
     ["reason is empty (after stripping whitespace)"]),
    ("staged-too-large", "x" * (STAGED_CAP + 1),
     ["add", "--title", "t", "--origin", "user:i-big", "--why-file"],
     ["is larger than %d bytes -- a staged file is small by contract"
      % STAGED_CAP]),
)


def _nesting_tokens(body):
    """What the nested-JSON refusal must say on THIS Python: its decoder limit
    is stack-based (3.12+), so whether the deepest stageable file overflows is
    measured here, like the separator list, never typed."""
    try:
        json.loads(body)
    except RecursionError:
        return ["is not a JSON object (nested too deeply)"], "RecursionError"
    except ValueError:
        return ["is not a JSON object ("], "a decoder ValueError"
    return ["is not a JSON object ("], "no error"


def _fifo(path):
    sandbox_path(path)
    os.mkfifo(path)
    return path


def group_i(suite, mod, work, wiki):
    for name, change, tokens in GRAMMAR_CASES:
        cid = "i-grammar-" + name
        text, why = mutated(EXPECTED_B, change)
        path = stage_roadmap(work, cid, text)
        # The calendar refusal is check_date's own catalogued line (M3), not
        # the grammar shape.
        suffix = [] if name == "log-calendar-date" else ["refusing to guess"]
        record_refusal(suite, GI, cid, path, ["list"], tokens + suffix,
                       "the strict reader refuses a hand edit",
                       extra=[why] if why else [])

    for name, archive, tokens in ARCHIVE_CASES:
        cid = "i-archive-" + name
        path = stage_roadmap(work, cid, EMPTY_TEXT, archive)
        record_refusal(suite, GI, cid, path, ["list"], tokens,
                       "the strict archive reader")

    for cid, argv, tokens, absent in COMMAND_CASES:
        cid = "i-" + cid
        path = stage_roadmap(work, cid, EXPECTED_B)
        today = None if "--today" in argv else TODAY
        record_refusal(suite, GI, cid, path, argv, tokens, "section 7",
                       absent=absent, today=today)

    for cid, body, argv, tokens in STAGED_CASES:
        cid = "i-" + cid
        path = stage_roadmap(work, cid, EXPECTED_B)
        stage = staged(work, cid, "input.txt", body)
        why = "a staged file is data, validated like a flag"
        if tokens is None:
            tokens, branch = _nesting_tokens(body)
            why = ("L7: %d nested brackets; this Python's decoder raises %s"
                   % (len(body), branch))
        record_refusal(suite, GI, cid, path, argv + [stage], tokens, why)

    record_refusal(suite, GI, "i-file-not-found",
                   stage_roadmap(work, "i-file-not-found", None), ["list"],
                   ["file not found: ", " -- run roadmap.py init first"],
                   "P2: the missing roadmap")
    path = stage_roadmap(work, "i-not-regular", None)
    os.makedirs(path)
    record_refusal(suite, GI, "i-not-regular", path, ["list"],
                   ["not a regular file: %s" % path], "P2: a directory")
    path = stage_roadmap(work, "i-not-utf8",
                         EXPECTED_B.encode("utf-8") + b"\xff\n")
    record_refusal(suite, GI, "i-not-utf8", path, ["list"],
                   ["is not valid UTF-8 (byte 0xff at offset"], "P3")
    path = stage_roadmap(work, "i-closed-id", EMPTY_TEXT, BOTH_ARCHIVES)
    record_refusal(suite, GI, "i-closed-id-mutated", path,
                   ["move", "R-0001", "now", "--reason", "x"],
                   ["R-0001 is closed (done) -- closed items are immutable; a "
                    "regression is a new item with --follows R-0001"],
                   "the archive is immutable")
    record_refusal(suite, GI, "i-dedup-dropped-archive", path,
                   ["add", "--title", "t", "--origin",
                    "user:2026-09-21:priority-labels"],
                   ["origin 'user:2026-09-21:priority-labels' is already "
                    "recorded by R-0002 (docs/roadmap/archive/%s, dropped)"
                    % ARCHIVE_2_NAME], "dedup covers the archive")
    text, why = mutated(EXPECTED_B, ("## R-0003 %s" % DOT,
                                     "## R-9999 %s" % DOT))
    path = stage_roadmap(work, "i-id-space", text)
    record_refusal(suite, GI, "i-id-space", path,
                   ["add", "--title", "t", "--origin", "user:i-id-space"],
                   ["id space exhausted (R-9999)"], "four digits",
                   extra=[why] if why else [])
    path = stage_roadmap(work, "i-staged-is-roadmap", EXPECTED_B)
    record_refusal(suite, GI, "i-staged-is-roadmap", path,
                   ["add", "--title", "t", "--origin", "user:i-s",
                    "--why-file", path],
                   ["--why-file %s is the roadmap or its archive -- refusing"
                    % path], "a staged file must not be the target")
    path = stage_roadmap(work, "i-reason-file-one-line", EXPECTED_B)
    reason = staged(work, "i-reason-file-one-line", "r.txt", "one\n")
    code, out, err = cli(path, "move", "R-0004", "later", "--reason-file",
                         reason)
    problems = shape_problems(code, err, 0)
    problems += problem_if("- 2026-09-28 next->later: one\n"
                           not in read_utf8(path), "the reason is not 'one'")
    suite.record(GI, "i-reason-file-one-line-accepted", problems,
                 detail=[_d("why", "exactly one trailing newline is "
                                   "stripped")])

    _group_i_git_timeout(suite, mod, work)
    _group_i_staged_input(suite, mod, work)
    _group_i_file_shapes(suite, mod, work)
    _group_i_lanes_and_spec(suite, mod, work)
    _group_i_init_and_symlinks(suite, mod, work)
    _group_i_containment(suite, mod, work)


def _group_i_git_timeout(suite, mod, work):
    got = need(suite, GI, "i-git-timeout", mod, "git", "GIT_TIMEOUT_SEC")
    if got is None:
        return
    bin_dir = sandbox_path(work.subdir("i-git-timeout", "bin"))
    fake = work.write_text(os.path.join("i-git-timeout", "bin", "git"),
                           "#!/bin/sh\nexec sleep 5\n")
    os.chmod(fake, 0o755)
    saved = os.environ.get("PATH")
    os.environ["PATH"] = bin_dir + os.pathsep + (saved or "")
    try:
        with patched(mod, "GIT_TIMEOUT_SEC", 1):
            result = mod.git(["rev-parse", "--show-toplevel"], bin_dir)
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved
    want = (124, "", "git rev-parse timed out after 1s")
    suite.record(GI, "i-git-timeout",
                 problem_if(tuple(result) != want, "git() returned %r, want %r"
                            % (result, want)),
                 detail=[_d("fake", "#!/bin/sh + exec sleep 5 first on PATH"),
                         _d("why", "R18: git() is called directly; "
                                   "resolve_target would hide the 124")])


def _group_i_staged_input(suite, mod, work):
    cid = "i-injection-item-file"
    path = stage_roadmap(work, cid, EXPECTED_B)
    root = os.path.join(work.path, cid)
    title = 'Fix $(touch PWNED) and "quoted" and \'single\' parts'
    why = "Run $(touch PWNED2) and `touch PWNED3` here."
    item = staged(work, cid, "item.json", json.dumps(
        {"title": title, "origin": "user:2026-09-28:injection", "why": why}))
    code, out, err = cli(path, "add", "--item-file", item)
    problems = shape_problems(code, err, 0)
    code, out, err = cli(path, "show", "R-0005")
    problems += shape_problems(code, err, 0)
    problems += problem_if("## R-0005 %s %s\n" % (DOT, title) not in out,
                           "show does not return the title byte-exact")
    problems += problem_if("\n\n%s\n\n### Log" % why not in out,
                           "show does not return the why byte-exact")
    problems += problem_if(names_under(root, "PWNED"), "a marker exists: %r"
                           % names_under(root, "PWNED"))
    suite.record(GI, cid, problems,
                 detail=[_d("why", "S-H1, R25: $(...), quotes and a backtick "
                                   "in why are data, never evaluated")])

    cid = "i-injection-backtick-title"
    path = stage_roadmap(work, cid, EXPECTED_B)
    item = staged(work, cid, "item.json", json.dumps(
        {"title": "a `touch PWNED4` b", "origin": "user:i-pwned4"}))
    record_refusal(suite, GI, cid, path, ["add", "--item-file", item],
                   ["title value", "carries a backtick"],
                   "S-M1 reconciled with S-H1: the backtick is refused",
                   after=lambda: problem_if(names_under(
                       os.path.join(work.path, cid), "PWNED"), "PWNED4 exists"))

    cid = "i-injection-reason-file"
    path = stage_roadmap(work, cid, EXPECTED_B)
    reason = staged(work, cid, "r.txt", "$(touch PWNED5)\n")
    code, out, err = cli(path, "move", "R-0004", "later", "--reason-file",
                         reason)
    problems = shape_problems(code, err, 0)
    problems += problem_if("next->later: $(touch PWNED5)\n"
                           not in read_utf8(path),
                           "the reason did not round-trip into the log")
    problems += problem_if(names_under(os.path.join(work.path, cid), "PWNED"),
                           "PWNED5 exists")
    suite.record(GI, cid, problems)

    cid = "i-injection-link-item-file"
    path = stage_roadmap(work, cid, EXPECTED_B)
    origin = 'user:2026-09-28:a-$(touch PWNED6)-"q"'
    item = staged(work, cid, "item.json", json.dumps({"origin": origin}))
    code, out, err = cli(path, "link", "R-0004", "--item-file", item)
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0004: linked (origin)",
                           "stdout %r" % out.strip())
    code, out, err = cli(path, "show", "R-0004")
    problems += problem_if("\norigin: %s\n" % origin not in out,
                           "show does not return the origin byte-exact")
    problems += problem_if(names_under(os.path.join(work.path, cid), "PWNED"),
                           "PWNED6 exists")
    blockers = staged(work, cid, "b.json", '{"blocked_by": ["R-0003"]}')
    code, out, err = cli(path, "link", "R-0004", "--item-file", blockers)
    problems += shape_problems(code, err, 0)
    problems += problem_if("state: idea\nhorizon: next\norigin: %s\n"
                           "blocked_by: [R-0003]\n" % origin
                           not in read_utf8(path), "the blocker was not added")
    suite.record(GI, cid, problems,
                 detail=[_d("why", "S2-1, R29: link's free text has a staged "
                                   "route too")])


def _group_i_file_shapes(suite, mod, work):
    cid = "i-staged-missing"
    path = stage_roadmap(work, cid, EXPECTED_B)
    missing = os.path.join(work.path, cid, "stage", "nope.txt")
    record_refusal(suite, GI, cid, path,
                   ["add", "--title", "t", "--origin", "user:i-m",
                    "--why-file", missing],
                   ["--why-file %s does not exist" % missing], "round-3 M2")
    cid = "i-staged-directory"
    path = stage_roadmap(work, cid, EXPECTED_B)
    directory = sandbox_path(work.subdir(cid, "stage", "dir.json"))
    record_refusal(suite, GI, cid, path, ["add", "--item-file", directory],
                   ["--item-file %s is not a regular file" % directory],
                   "a directory")
    if can_symlink():
        cid = "i-staged-symlink"
        path = stage_roadmap(work, cid, EXPECTED_B)
        real = staged(work, cid, "real.txt", "x\n")
        link = os.path.join(work.path, cid, "stage", "link.txt")
        os.symlink(real, link)
        record_refusal(suite, GI, cid, path,
                       ["move", "R-0003", "next", "--reason-file", link],
                       ["--reason-file %s is not a regular file" % link],
                       "the stager writes plain files")
    else:
        info_skip(suite, GI, "i-staged-symlink", "no os.symlink")
    if hasattr(os, "mkfifo"):
        cid = "i-staged-fifo"
        path = stage_roadmap(work, cid, EXPECTED_B)
        work.subdir(cid, "stage")
        fifo = _fifo(os.path.join(work.path, cid, "stage", "item.json"))
        record_refusal(suite, GI, cid, path, ["add", "--item-file", fifo],
                       ["--item-file %s is not a regular file" % fifo],
                       "a FIFO is refused before any open, within a "
                       "test-side timeout", timeout=20)
        cid = "i-archive-entry-fifo"
        path = stage_roadmap(work, cid, EMPTY_TEXT, {})
        entry = _fifo(os.path.join(os.path.dirname(path), "archive",
                                   "0001-x.md"))
        record_refusal(suite, GI, cid, path, ["list"],
                       ["archive entry %s is not a regular file" % entry],
                       "S3-1: lstat refuses it before any open", timeout=20)
    else:
        info_skip(suite, GI, "i-staged-fifo", "no os.mkfifo")
        info_skip(suite, GI, "i-archive-entry-fifo", "no os.mkfifo")
    if can_symlink():
        cid = "i-archive-entry-symlink"
        path = stage_roadmap(work, cid, EMPTY_TEXT, {})
        real = staged(work, cid, ARCHIVE_1_NAME, ARCHIVE_1)
        entry = os.path.join(os.path.dirname(path), "archive", "0001-x.md")
        os.symlink(real, entry)
        record_refusal(suite, GI, cid, path, ["list"],
                       ["archive entry %s is not a regular file" % entry],
                       "S3-1: never followed",
                       after=lambda: problem_if(read_utf8(real) != ARCHIVE_1,
                                                "the link target changed"))
    else:
        info_skip(suite, GI, "i-archive-entry-symlink", "no os.symlink")

    for cid, archive_size, tokens in (
            ("i-roadmap-too-large", None,
             ["is larger than 100 bytes -- refusing to read it"]),
            ("i-archive-entry-too-large", True,
             ["archive entry", "is larger than", "bytes -- refusing to read "
                                                 "it"])):
        got = need(suite, GI, cid, mod, "cmd_list", "PAGE_MAX_BYTES")
        if got is None:
            continue
        if archive_size:
            cap = len(EMPTY_TEXT.encode("utf-8")) + 100
            path = stage_roadmap(work, cid, EMPTY_TEXT,
                                 {"0001-x.md": b"x" * (cap + 1)})
        else:
            cap = 100
            path = stage_roadmap(work, cid, EMPTY_TEXT)
        with patched(mod, "PAGE_MAX_BYTES", cap):
            code, out, err = call_module(mod, "cmd_list", path, None, None,
                                         False, False)
        problems = problem_if(code != 2, "exit %r, want 2" % code)
        problems += missing_tokens(err, tokens)
        suite.record(GI, cid, problems,
                     detail=[_d("cap", "PAGE_MAX_BYTES monkeypatched to %d, "
                                       "restored in finally" % cap),
                             _d("stderr", err.strip())])


def _group_i_lanes_and_spec(suite, mod, work):
    path = stage_roadmap(work, "i-lane-accepted", EXPECTED_B)
    code, out, err = cli(path, "move", "R-0004", "inbox", "--reason", "x")
    problems = shape_problems(code, err, 0)
    problems += problem_if(out.strip() != "R-0004: next->unset",
                           "stdout %r" % out.strip())
    code, out, err = cli(path, "list", "--horizon", "unset")
    problems += shape_problems(code, err, 0)
    problems += problem_if(sorted(_list_ids(out)) != ["R-0003", "R-0004"],
                           "list --horizon unset gave %r" % _list_ids(out))
    suite.record(GI, "i-lane-inbox-and-unset-accepted", problems,
                 detail=[_d("why", "L6: inbox is the spelling of unset")])

    for cid, with_link in (("i-spec-names-a-page", False),
                           ("i-spec-scan-skips-symlink", True)):
        path = stage_roadmap(work, cid, EXPECTED_B)
        docs = os.path.join(work.path, cid, "docs")
        work.subdir(cid, "docs", "sources")
        work.subdir(cid, "docs", "concepts")
        write_file(os.path.join(docs, "sources", "notes.md"), "just notes\n")
        write_file(os.path.join(docs, "concepts", "real-page.md"),
                   "---\nname: real-page\ntype: concept\n---\n\n# Real\n")
        if with_link:
            if not can_symlink():
                info_skip(suite, GI, cid, "no os.symlink")
                continue
            os.symlink(os.path.join(docs, "concepts", "real-page.md"),
                       os.path.join(docs, "linked.md"))
            code, out, err = cli(path, "link", "R-0004", "--spec", "real-page")
            problems = problem_if(code != 0, "exit %r, want 0" % code)
            notes = [line for line in err.strip().splitlines()
                     if "note: wiki name scan skipped" in line]
            problems += problem_if(len(notes) != 1 or "linked.md" not in err,
                                   "want one note: line naming linked.md, got "
                                   "%r" % err.strip())
            problems += problem_if("Traceback" in err, "a traceback")
            problems += problem_if("\nspec: real-page\n" not in read_utf8(path),
                                   "the spec was not written")
            suite.record(GI, cid, problems,
                         detail=[_d("why", "S3-1: a symlinked *.md is skipped "
                                           "with a note, never followed")])
        else:
            record_refusal(suite, GI, cid, path,
                           ["link", "R-0004", "--spec", "notes"],
                           ["--spec 'notes' is not a wiki page under %s"
                            % docs], "L8: a bare stem is not a page")
            code, out, err = cli(path, "link", "R-0004", "--spec", "real-page")
            suite.record(GI, cid + "-accepted", shape_problems(code, err, 0),
                         detail=[_d("why", "a frontmatter name: makes a page")])


def _group_i_init_and_symlinks(suite, mod, work):
    cid = "i-init-readonly-wiki-root"
    if is_root():
        info_skip(suite, GI, cid, "root ignores the permission bits")
    else:
        docs = work.subdir(cid, "docs")
        path = sandbox_path(os.path.join(docs, "roadmap", "roadmap.md"))
        os.chmod(docs, 0o500)
        try:
            code, out, err = cli(path, "init")
        finally:
            os.chmod(docs, 0o700)
        problems = shape_problems(code, err)
        problems += problem_if(not err.startswith("roadmap: cannot create "
                                                  "directory"),
                               "stderr %r" % err.strip())
        suite.record(GI, cid, problems,
                     detail=[_d("why", "R32: the io_fail backstop, one line"),
                             _d("stderr", err.strip())])

    cid = "i-refused-init-creates-nothing"
    path = stage_roadmap(work, cid, EXPECTED_B)
    problems, err = check_refusal(path, ["init"],
                                  ["already exists -- init never overwrites"])
    # Judged AFTER the refusal ran: an extra= argument would be evaluated
    # before the child starts and so could never see what it created.
    problems += problem_if(os.path.isdir(os.path.join(os.path.dirname(path),
                                                      "archive")),
                           "the refused init created archive/")
    suite.record(GI, cid, problems,
                 detail=[_d("why", "H1: decided before ensure_archive_dir"),
                         _d("stderr", err.strip())])

    if not can_symlink():
        for cid in ("i-symlinked-roadmap", "i-symlinked-archive",
                    "i-init-symlinked-archive"):
            info_skip(suite, GI, cid, "no os.symlink")
        return
    cid = "i-symlinked-roadmap"
    real = staged(work, cid, "real.md", EXPECTED_B)
    path = os.path.join(work.subdir(cid, "docs", "roadmap"), "roadmap.md")
    os.symlink(real, path)
    record_refusal(suite, GI, cid, path,
                   ["move", "R-0004", "later", "--reason", "x"],
                   ["%s is a symlink -- roadmap.py reads and replaces only a "
                    "regular file it owns" % path], "S-L2",
                   after=lambda: problem_if(read_utf8(real) != EXPECTED_B,
                                            "the link target changed"))
    cid = "i-symlinked-archive"
    path = stage_roadmap(work, cid, EXPECTED_B)
    outside = sandbox_path(work.subdir(cid, "real-archive"))
    archive = os.path.join(os.path.dirname(path), "archive")
    os.symlink(outside, archive)
    record_refusal(suite, GI, cid, path, ["list"],
                   ["archive directory %s is a symlink -- refusing" % archive],
                   "S-L2")
    cid = "i-init-symlinked-archive"
    roadmap_dir = work.subdir(cid, "docs", "roadmap")
    path = sandbox_path(os.path.join(roadmap_dir, "roadmap.md"))
    outside = sandbox_path(work.subdir(cid, "real-archive"))
    os.symlink(outside, os.path.join(roadmap_dir, "archive"))
    record_refusal(suite, GI, cid, path, ["init"],
                   ["archive directory", "is a symlink -- refusing"],
                   "H1: refused before anything is created",
                   after=lambda: problem_if(
                       os.listdir(outside) or os.listdir(roadmap_dir)
                       != ["archive"], "the refused init created something"))

    case = "i-target-shape"
    work.subdir(case, "docs", "roadmap")
    before = sorted(os.listdir(os.path.join(work.path, case)))
    problems = []
    for rel in ("notes/roadmap.md", "roadmap.md", "docs/roadmap/other.md"):
        path = sandbox_path(os.path.join(work.path, case, rel))
        got_problems, _err = check_refusal(
            path, ["init"], ["is not <wiki root>/roadmap/roadmap.md -- the "
                             "wiki root is the parent of the roadmap/ "
                             "directory"])
        problems += ["%s: %s" % (rel, p) for p in got_problems]
    problems += problem_if(sorted(os.listdir(os.path.join(work.path, case)))
                           != before or os.listdir(os.path.join(
                               work.path, case, "docs", "roadmap")),
                           "a refused shape created something")
    path = sandbox_path(os.path.join(work.path, case, "docs", "roadmap",
                                     "roadmap.md"))
    code, out, err = cli(path, "init")
    problems += shape_problems(code, err, 0)
    suite.record(GI, "i-target-shape", problems,
                 detail=[_d("why", "L5: the wiki root is well defined only "
                                   "for <root>/roadmap/roadmap.md")])


def _group_i_containment(suite, mod, work):
    if not can_symlink():
        for cid in ("i-containment-init", "i-containment-move",
                    "i-containment-git-docs"):
            info_skip(suite, GI, cid, "no os.symlink")
        return
    for cid, text in (("i-containment-init", None),
                      ("i-containment-move", EXPECTED_B)):
        outside = sandbox_path(work.subdir(cid + "-outside-roadmap"))
        if text is not None:
            write_file(os.path.join(outside, "roadmap.md"), text)
        before = H.file_digests(outside)
        docs = work.subdir(cid, "docs")
        roadmap_dir = os.path.join(docs, "roadmap")
        os.symlink(outside, roadmap_dir)
        path = os.path.join(roadmap_dir, "roadmap.md")
        argv = ["init"] if text is None else ["move", "R-0004", "later",
                                              "--reason", "x"]
        record_refusal(suite, GI, cid, path, argv,
                       ["roadmap directory %s is a symlink -- refusing"
                        % roadmap_dir], "S4-1, R33",
                       after=lambda: problem_if(
                           H.file_digests(outside) != before
                           or sorted(os.listdir(outside)) != sorted(before),
                           "the outside directory changed"))
    cid = "i-containment-git-docs"
    case_root = sandbox_path(work.subdir(cid))
    if not git_init(case_root):
        info_skip(suite, GI, cid, "git is not usable here")
        return
    outside = sandbox_path(work.subdir(cid + "-outside-docs"))
    os.symlink(outside, os.path.join(case_root, "docs"))
    path = os.path.join(case_root, "docs", "roadmap", "roadmap.md")
    record_refusal(suite, GI, cid, path, ["init"],
                   ["resolves to", ", outside ", " -- refusing to write "
                                                 "through a symlinked "
                                                 "directory"],
                   "S4-1: a symlinked ancestor inside the work tree",
                   after=lambda: problem_if(os.listdir(outside), "the outside "
                                            "directory is no longer empty"))


# ---------------------------------------------------------------------------
# L. the CLI surface
# ---------------------------------------------------------------------------

PIPE_BYTES = 65536   # a common pipe buffer; the fixture is a multiple of it


def _spawn_raw(argv, cwd, env_extra=None, timeout=30):
    """Bytes in, bytes out; for the cases about encodings."""
    sandbox_path(cwd)
    proc = subprocess.run(argv, input=b"", capture_output=True,
                          timeout=timeout, cwd=cwd,
                          env=H.child_env(_child_env(env_extra)))
    return proc.returncode, proc.stdout, proc.stderr


class _FailingBuffer:
    def __init__(self, exc):
        self.exc = exc
        self.writes = 0

    def write(self, data):
        self.writes += 1
        raise self.exc

    def flush(self):
        pass


class _FailingStream:
    """A stream whose byte route fails. fileno() raises, as captured()'s
    StringIO does, so _silence can never dup2 over the runner's own fds."""

    def __init__(self, exc):
        self.buffer = _FailingBuffer(exc)

    def flush(self):
        pass

    def write(self, text):
        raise AssertionError("the text route was taken")

    def fileno(self):
        raise io.UnsupportedOperation("a test stream has no descriptor")


def _bulk_roadmap(count):
    """A staged roadmap whose `list` output is large: `count` items in later."""
    lines = [FRONT_HEAD + WIP_NOTE, "wip_now: 3", "---", "",
             BEGIN_PREFIX + " -->", END_PREFIX + " -->", "", "# now", "",
             "# next", "", "# later", ""]
    for n in range(1, count + 1):
        lines += ["## R-%04d %s Bulk item %04d %s" % (n, DOT, n, "x" * 200), "",
                  "state: idea", "horizon: later",
                  "origin: user:2026-09-28:bulk-%d" % n, "blocked_by: []",
                  "tags: []", "", "### Log", "",
                  "- 2026-09-28 new->later: added", ""]
    lines += ["# inbox", ""]
    return "\n".join(lines)


def group_l(suite, mod, work, wiki):
    path, problems, _outs = build(work, "l-today", [
        ("add", "--title", "t", "--origin", "user:l-today",
         "--today", "2031-01-02")])
    problems += problem_if(os.path.isfile(path) and "- 2031-01-02 new->unset: "
                           "added\n" not in read_utf8(path),
                           "--today did not drive the log date")
    if not problems:
        got_problems, err = check_refusal(path, ["list", "--today",
                                                 "20310102"],
                                          ["--today '20310102' is not a "
                                           "YYYY-MM-DD date"], today=None)
        problems += got_problems
    suite.record(GL, "l-today-drives-dates", problems)

    repo = sandbox_path(work.subdir("l-repo"))
    if git_init(repo):
        sub = sandbox_path(work.subdir("l-repo", "sub", "dir"))
        want = os.path.realpath(os.path.join(repo, DEFAULT_REL))
        code, out, err = run_cli_default(sub, "list")
        problems = shape_problems(code, err)
        named = err.split("file not found: ", 1)[-1].split(" -- ", 1)[0]
        problems += problem_if(os.path.realpath(named.strip()) != want,
                               "list named %r, want %r" % (named, want))
        code, out, err = run_cli_default(sub, "init")
        problems += shape_problems(code, err, 0)
        problems += problem_if(not os.path.isfile(want), "init did not create "
                               "%s" % want)
        problems += problem_if(os.path.exists(os.path.join(sub, "docs")),
                               "init created docs/ under sub/dir")
        suite.record(GL, "l-default-target-git-top-level", problems,
                     detail=[_d("why", "L9: realpath on both sides")])
    else:
        info_skip(suite, GL, "l-default-target-git-top-level",
                  "git is not usable here")
    plain = sandbox_path(work.subdir("l-plain", "a"))
    code, out, err = run_cli_default(plain, "init")
    problems = shape_problems(code, err, 0)
    problems += problem_if(not os.path.isfile(os.path.join(plain,
                                                           DEFAULT_REL)),
                           "outside a repo init did not fall back to the cwd")
    suite.record(GL, "l-default-target-outside-git", problems)

    ascii_env = {"LC_ALL": "C", "PYTHONIOENCODING": "ascii"}
    path = stage_roadmap(work, "l-ascii-locale", EXPECTED_B)
    code, out, err = _spawn_raw([sys.executable, TARGET, "show", "R-0001",
                                 "--file", path, "--today", TODAY],
                                os.path.dirname(path), ascii_env)
    problems = problem_if(code != 0, "exit %r: %r" % (code, err[-200:]))
    try:
        problems += problem_if(DOT not in out.decode("utf-8"),
                               "stdout lacks U+00B7")
    except UnicodeDecodeError as exc:
        problems.append("stdout is not UTF-8: %s" % exc)
    suite.record(GL, "l-show-utf8-under-ascii-locale", problems,
                 detail=[_d("env", "LC_ALL=C PYTHONIOENCODING=ascii (R21)")])
    code, out, err = _spawn_raw([sys.executable, TARGET, "add", "--title",
                                 '"a %s b"' % DOT, "--origin", "user:l-dot",
                                 "--file", path, "--today", TODAY],
                                os.path.dirname(path), ascii_env)
    problems = problem_if(code != 2, "exit %r" % code)
    try:
        text = err.decode("utf-8")
        problems += problem_if(DOT not in text, "stderr lacks U+00B7")
        problems += problem_if(len(text.strip().splitlines()) != 1
                               or "Traceback" in text, "stderr %r" % text)
    except UnicodeDecodeError as exc:
        problems.append("stderr is not UTF-8: %s" % exc)
    suite.record(GL, "l-refusal-utf8-under-ascii-locale", problems)

    case = sandbox_path(work.subdir("l-non-utf8"))
    raw_path = os.fsencode(case) + b"/x\xff/docs/roadmap/roadmap.md"
    sandbox_path(os.fsdecode(raw_path))
    code, out, err = _spawn_raw(
        [sys.executable.encode(), TARGET.encode(), b"--file", raw_path,
         b"--today", b"2026-09-28", b"list"], case, ascii_env)
    text = err.decode("utf-8", "replace")
    problems = problem_if(code != 2, "exit %r" % code)
    problems += problem_if(len(text.strip().splitlines()) != 1,
                           "stderr is %d lines" % len(text.strip().splitlines()))
    problems += problem_if("Traceback" in text, "a traceback")
    problems += problem_if("\\udcff" not in text, "the surrogate is not "
                           "backslash-escaped: %r" % text)
    problems += problem_if("file not found" not in text
                           and "cannot read" not in text, "stderr %r" % text)
    problems += problem_if(os.listdir(case), "something was created")
    suite.record(GL, "l-non-utf8-path-on-refusal-route", problems,
                 detail=[_d("argv", "bytes, --file before the command"),
                         _d("stderr", text.strip())])

    count = 4 * PIPE_BYTES // 200 + 1
    text = _bulk_roadmap(count)
    path = stage_roadmap(work, "l-early-close", text)
    cap = getattr(mod, "PAGE_MAX_BYTES", 4 * 1024 * 1024)
    problems = problem_if(len(text.encode("utf-8")) >= cap,
                          "the fixture would trip the read cap, not the pipe")
    proc = subprocess.Popen([sys.executable, TARGET, "list", "--file", path,
                             "--today", TODAY], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=os.path.dirname(path),
                            env=H.child_env(_child_env(None)))
    try:
        proc.stdout.readline()
        proc.stdout.close()
        code = proc.wait(timeout=30)
        err = proc.stderr.read().decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        code, err = None, ""
        problems.append("the child hung after the reader closed")
    finally:
        proc.stderr.close()
    problems += problem_if(code != 0, "exit %r, want 0" % code)
    problems += problem_if("Traceback" in err or "Exception ignored" in err,
                           "stderr %r" % err[-300:])
    suite.record(GL, "l-reader-closes-early", problems,
                 detail=[_d("fixture", "%d items, about 4x a %d-byte pipe"
                            % (count, PIPE_BYTES)),
                         _d("why", "M2, R34: EPIPE on stdout exits 0")])

    got = need(suite, GL, "l-stream-failures", mod, "emit", "note", "die")
    if got is not None:
        emit, note, die = got
        saved = sys.stdout, sys.stderr
        problems = []
        try:
            sys.stdout = _FailingStream(OSError(errno.EIO, "Input/output "
                                                           "error"))
            sys.stderr = io.StringIO()
            try:
                emit("x")
                code = None
            except SystemExit as exc:
                code = exc.code
            err = sys.stderr.getvalue()
            problems += problem_if(code != 2, "EIO on stdout: exit %r" % code)
            problems += problem_if(
                not err.startswith("roadmap: cannot write to standard output")
                or len(err.strip().splitlines()) != 1,
                "EIO on stdout: stderr %r" % err)
            for label, fn in (("note", note), ("die", die)):
                sys.stdout = io.StringIO()
                fake = _FailingStream(BrokenPipeError(errno.EPIPE,
                                                      "Broken pipe"))
                sys.stderr = fake
                try:
                    fn("x")
                    code = None
                except SystemExit as exc:
                    code = exc.code
                problems += problem_if(code != 2, "%s on a broken stderr: "
                                       "exit %r" % (label, code))
                problems += problem_if(fake.buffer.writes != 1,
                                       "%s tried %d writes (recursion?)"
                                       % (label, fake.buffer.writes))
        finally:
            sys.stdout, sys.stderr = saved
        suite.record(GL, "l-stream-failures", problems,
                     detail=[_d("why", "M2: EIO on stdout is one line + 2; a "
                                       "broken stderr is 2 with no "
                                       "recursion")])


# ---------------------------------------------------------------------------
# K. hygiene -- runs LAST, always, also when roadmap.py does not exist
# ---------------------------------------------------------------------------

def _live_case(suite, cid, label, before, now):
    """`before` / `now`: a digest (or digest dict), or None when absent."""
    if before is None:
        suite.record(GK, cid,
                     problem_if(now is not None,
                                "%s did not exist before this run and does "
                                "NOW" % label),
                     status=H.INFO,
                     detail=["%s does not exist on this machine, so there was "
                             "nothing to protect -- the guards still ran"
                             % label])
        return
    problems = problem_if(now is None, "%s is GONE" % label)
    if now is not None:
        problems += problem_if(now != before,
                               "%s was MODIFIED by this run" % label)
    suite.record(GK, cid, problems,
                 detail=[_d("label", label),
                         _d("why", "the default target of every command "
                                   "under test")])


def _live_digests():
    roadmap = (H.sha256_file(LIVE_ROADMAP) if os.path.isfile(LIVE_ROADMAP)
               else None)
    archive = (H.file_digests(LIVE_ARCHIVE) if os.path.isdir(LIVE_ARCHIVE)
               else None)
    return roadmap, archive


def _raises_assertion(fn, *args):
    """None if fn(*args) raised AssertionError, else a problem string."""
    try:
        fn(*args)
    except AssertionError:
        return None
    except Exception as exc:  # noqa: BLE001 -- the wrong refusal is a finding
        return "raised %s instead of AssertionError: %s" % (
            exc.__class__.__name__, exc)
    return "did NOT raise"


def group_k(suite, tree_before, pyc_before, live_before, workspace):
    roadmap_now, archive_now = _live_digests()
    _live_case(suite, "k-live-roadmap-untouched", "docs/roadmap/roadmap.md",
               live_before[0], roadmap_now)
    _live_case(suite, "k-live-archive-untouched", "docs/roadmap/archive/",
               live_before[1], archive_now)

    after = H.repo_tree()
    new = sorted(after - tree_before)
    gone = sorted(tree_before - after)
    suite.record(GK, "k-no-new-repo-paths",
                 problem_if(new, "this suite wrote into the repo tree: %s"
                            % new[:12]),
                 detail=["%d path(s) before, %d after" % (len(tree_before),
                                                          len(after))])
    suite.record(GK, "k-no-removed-repo-paths",
                 problem_if(gone, "paths disappeared: %s" % gone[:12]))

    pyc_after = H.pycache_snapshot()
    created = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GK, "k-no-new-pycache",
                 problem_if(created or touched,
                            "this run wrote bytecode: new=%r touched=%r"
                            % (created[:6], touched[:6])),
                 detail=["%d .pyc before, %d after (a delta check)"
                         % (len(pyc_before), len(pyc_after))])

    inside = os.path.realpath(workspace).startswith(
        os.path.realpath(H.REPO_ROOT) + os.sep)
    suite.record(GK, "k-workspace-outside-the-repo-tree",
                 problem_if(inside, "the workspace is inside the repo: %s"
                            % workspace),
                 detail=["workspace: %s" % workspace])

    stale = stale_temp_files(workspace)
    suite.record(GK, "k-no-roadmap-temp-name-in-the-sandbox",
                 problem_if(stale, "left behind: %r" % stale[:6]),
                 detail=["no %s* name anywhere: a survivor is an abandoned "
                         "temp file or a temp hard link the link route did "
                         "not unlink -- the archive listing skips these "
                         "names, so nothing else would notice (L2)"
                         % TMP_PREFIX])

    # The guards from the module docstring, exercised: a suite whose safety
    # rests on them must prove they bite.
    problems = []
    for label, fn, args in (
            ("sandbox_path(LIVE_ROADMAP)", sandbox_path, (LIVE_ROADMAP,)),
            ("run_cli('list')", run_cli, ("list",)),
            ("run_cli_default(<repo root>, 'list')", run_cli_default,
             (H.REPO_ROOT, "list"))):
        why = _raises_assertion(fn, *args)
        if why is not None:
            problems.append("%s %s" % (label, why))
    suite.record(GK, "k-the-sandbox-guards-bite", problems,
                 detail=["sandbox_path refuses the live roadmap, run_cli "
                         "refuses an argv with no --file, run_cli_default "
                         "refuses a cwd outside the sandbox -- what makes "
                         "reaching %s impossible rather than unlikely"
                         % LIVE_ROADMAP])


# ---------------------------------------------------------------------------

def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="roadmap.py: the format contract, the AST gates "
                          "and their planted controls, the behavioural "
                          "groups, and hygiene that runs even before the "
                          "script exists",
                    opts=opts, mode="grouped")

    tree_before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    live_before = _live_digests()
    saved_path = list(sys.path)
    work = H.TempWorkspace("ph-roadmap-", keep=opts.keep)
    set_sandbox(work.path)
    try:
        if not os.path.isfile(TARGET):
            suite.record(GA, "target-exists",
                         ["the script under test is missing: %s" % TARGET],
                         detail=["skipped: every group that needs the module "
                                 "(A-J, L)",
                                 "group K runs regardless, from the finally "
                                 "(M1)"])
        else:
            suite.record(GA, "target-exists", [],
                         detail=[_d("target", TARGET)])
            mod = H.load_module_from_path("roadmap_under_test", TARGET)
            # reindex.py imports _wikilib the way the wiki tooling does
            # (P10); its `w` is the object whose constants are judged.
            reindex_mod = H.load_module_from_path("roadmap_wiki_reindex",
                                                  REINDEX)
            run_group(suite, GA, group_a, suite, mod, reindex_mod.w,
                      _read(TARGET))
            wiki = Wiki(reindex_mod.w, reindex_mod,
                        H.load_module_from_path("roadmap_wiki_freshness",
                                                FRESHNESS),
                        H.load_module_from_path("roadmap_wiki_server", SERVER))
            for group, body in ((GB, group_b), (GC, group_c), (GD, group_d),
                                (GE, group_e), (GF, group_f), (GG, group_g),
                                (GH, group_h), (GI, group_i), (GJ, group_j),
                                (GL, group_l)):
                run_group(suite, group, body, suite, mod, work, wiki)
    finally:
        # Loading reindex.py put its directory on sys.path and _wikilib into
        # sys.modules; no later suite in this process inherits either (L8).
        sys.path[:] = saved_path
        sys.modules.pop("_wikilib", None)
        try:
            # group_k LAST, always, and before the workspace goes: it walks
            # the sandbox for leftover temp names.
            group_k(suite, tree_before, pyc_before, live_before, work.path)
        finally:
            work.cleanup()

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
