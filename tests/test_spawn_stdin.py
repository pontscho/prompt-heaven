#!/usr/bin/env python3
"""Every subprocess spawn site must choose its child's stdin EXPLICITLY (A-D).

WHY THIS IS A SUITE AND NOT AN AUDIT
------------------------------------
`subprocess.Popen` and friends INHERIT the parent's stdin when no `stdin=` is
passed.  In an MCP server the parent's stdin IS the JSON-RPC stream, so a child
that reads stdin eats protocol messages.  A fleet-wide audit found 11 such call
sites under `Scripts/`.  Two of them were not theoretical:

  * a build command swallowed a `ping` request and the reply never came --
    indistinguishable from a hang, and blamed on the wrong component twice;
  * another desynced the stream MID-MESSAGE, so the next reply was garbage.

The remaining sites were deliberate (`stdin=PIPE` for an LSP transport, a PTY
slave fd for a debugger, `stdin=PIPE` for the smoke harness feeding servers
under test).  Fixing 11 sites is worth one commit; making the class impossible
to REINTRODUCE is worth more, because the next such site will be added by
someone who never read the audit.  Hence a gate.

THE ASSERTION IS EXPLICITNESS, NOT A PARTICULAR VALUE
-----------------------------------------------------
`DEVNULL`, `PIPE`, a variable, a raw fd -- all correct.  The bug class is SILENT
INHERITANCE, so any deliberate choice passes whatever it is; a missing `stdin=`
keyword is the failure.  `input=` counts as explicit for `run`/`check_output`:
it is mutually exclusive with `stdin=` in the API, so demanding `stdin=` there
would demand a TypeError.

Forms that CANNOT take `stdin=` at all (`os.system`, `os.popen`,
`subprocess.getoutput`, `subprocess.getstatusoutput`) fail outright: there is no
way to write them safely.  There are currently ZERO of these, and a suite that
says so is worth having -- "zero, checked" and "zero, assumed" look identical
until one appears.

AST, NOT REGEX
--------------
Every file is parsed with `ast`, and the check reads the keyword list of the
`Call` node.  A regex over source text gets this wrong in both directions, and
BOTH mistakes exist in this repo right now:

  * `Scripts/mcp-tshark.py:92` -- `def _kill_process_group(proc: subprocess.Popen)`
    is a type ANNOTATION, not a spawn.  A regex flags it.
  * `Scripts/mcp-git.py:307`  -- a docstring that says "git is spawned via
    subprocess.run()".  A regex flags that too.
  * every real site here spans 3-8 lines, so a line-oriented regex looking for
    `stdin=` on the matched line misses the keyword entirely.

Module aliasing is resolved per file, so `import subprocess as sp; sp.run(...)`
and `from subprocess import Popen; Popen(...)` are both seen.

SCOPE -- gated vs surveyed, and why
-----------------------------------
GATED (a missing `stdin=` FAILS the suite):
  Scripts/**.py    the MCP server fleet.  These processes speak JSON-RPC over
                   stdin, so this is the tree where the bug class actually
                   bites, and the invariant holds today.

SURVEYED (reported as INFO, never a failure):
  ClaudeCode/**.py and tests/**.py.  Measured, deliberately NOT gated:

  * `ClaudeCode/hooks/**` -- a hook is not a JSON-RPC peer.  Its stdin is ONE
    JSON object which `load_payload()` consumes to EOF via
    `json.load(sys.stdin)` before anything is spawned, so there is no stream to
    desync and no reply to swallow.  `attention-reminder.py` has three
    inheriting spawns (`claude mcp list`, `pgrep`, `ps`) and none of those
    children read stdin.  Gating this tree would turn the suite red on day one
    over a class that cannot bite it, and the fix would be an edit to a hook
    that the audit deliberately did not touch.
  * `ClaudeCode/skills/**` -- standalone CLI tools run by a human in a terminal,
    where inheriting the terminal's stdin is often the POINT.  Surveying them
    still paid: `skills/wiki/scripts/_wikilib.py` is the same git helper as
    `Scripts/mcp-wiki.py`, and only the MCP copy got the `stdin=DEVNULL` fix.
  * `tests/**` -- `_harness.run_process()` uses `input=` (explicit, different
    spelling) and `JsonRpcClient` uses `stdin=PIPE`, but the shell oracle in
    `test_mcp_git_params.py` inherits.  Its child is `printf`, which never
    reads stdin.

The INFO rows are the point of the survey: the gap stays VISIBLE instead of
being invisible, so promoting a tree later is a scope decision on printed data,
not a fresh audit.

NEGATIVE CONTROL (group C) -- mandatory
---------------------------------------
A checker that silently matches nothing is indistinguishable from a clean tree.
So group C points the SAME functions at a synthetic sandbox: files it MUST flag
(missing `stdin=`, `os.system`, an aliased import, a bare import, an asyncio
spawn, a `**kwargs` splat, a MULTI-LINE call whose keyword a regex would miss,
an unparseable file), and files it must NOT (an explicit `DEVNULL`, an `input=`,
a multi-line call whose `stdin=` is buried mid-call, and a file where the only
mentions are a type annotation and a docstring).

SANDBOX DISCIPLINE -- all fixtures under
`.claude/tmp/test_spawn_stdin/run-<unique>/`, one subdirectory per run so a
concurrent instance's teardown cannot delete a live run's fixtures.  NEVER the
shared system temp dir, never the repo tree, never beside a source file.
Removed in a `finally` unless --keep.  Note the fixture root is deliberately
OUTSIDE every scan root, so a control fixture can never leak into the live scan.

The case COUNT is DERIVED (one case per site found), never typed -- see the
`None` in run.py's SUITES table.

Groups:
  A  GATE   -- Scripts/**: one case per spawn site, plus the aggregate
               invariant, the zero-forbidden-forms assertion, the
               every-file-parsed assertion, and a blindness floor
  B  SURVEY -- ClaudeCode/** and tests/**, INFO only, plus the live table
  C  negative control -- planted defects the checker MUST flag, and bait it
               must not
  D  hygiene -- every write lands under .claude/tmp, no .pyc anywhere

Offline, read-only apart from the sandbox, ~1s.

Usage:
  python3 tests/test_spawn_stdin.py
  python3 tests/test_spawn_stdin.py --brief
  python3 tests/test_spawn_stdin.py --keep
Exit code 0 iff every non-informational case passes.
"""

import ast
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "spawn_stdin"

# Trees where a missing `stdin=` is a FAILURE, and trees that are only reported.
GATED_ROOTS = ["Scripts"]
SURVEY_ROOTS = ["ClaudeCode", "tests"]

# `subprocess` callables that spawn a child and accept `stdin=`.
SUBPROCESS_SPAWN = {"Popen", "run", "call", "check_call", "check_output"}

# `asyncio` callables that spawn a child and accept `stdin=`.
ASYNCIO_SPAWN = {"create_subprocess_exec", "create_subprocess_shell"}

# Spawn forms with NO stdin parameter at all: the child always inherits, so
# there is no correct way to write these in a process whose stdin is a protocol
# stream.  module -> names.
NO_STDIN_FORMS = {
    "os": {"system", "popen"},
    "subprocess": {"getoutput", "getstatusoutput"},
}

# `run` and `check_output` take `input=` INSTEAD of `stdin=` (the two are
# mutually exclusive in the API, and passing both raises).  So `input=` is an
# explicit stdin choice, not an omission -- `_harness.run_process()` is written
# that way on purpose.
INPUT_IS_EXPLICIT = {"run", "check_output"}

MODULES = ("subprocess", "asyncio", "os")

# Verdicts
V_EXPLICIT = "EXPLICIT"
V_MISSING = "MISSING"
V_SPLAT = "INDETERMINATE"
V_FORBIDDEN = "NO-STDIN-FORM"

GA = "A. GATE: Scripts/** -- every spawn site explicit"
GB = "B. SURVEY (INFO): ClaudeCode/** and tests/**"
GC = "C. negative control"
GD = "D. hygiene"

# All scratch lives here, one mkdtemp subdir per run (concurrency-safe), and
# NEVER under a scan root -- a control fixture must not be able to leak into the
# live scan.
FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_spawn_stdin")

# Every path this suite writes is recorded, so group D can ASSERT the sandbox
# rule rather than state it in a comment.
WRITES = []

# A blindness FLOOR, not a case count.  It can only trip if the scanner stops
# resolving call targets at all (a refactor that breaks alias handling, say);
# legitimate growth moves the real numbers UP and never trips it.  Deliberately
# far below the ~19 sites in ~11 files measured today.
MIN_GATED_SITES = 8
MIN_GATED_FILES = 4


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------

class Site:
    """One spawn call site."""

    def __init__(self, path, lineno, callee, verdict, value="", note=""):
        self.path = path
        self.lineno = lineno
        self.callee = callee
        self.verdict = verdict
        self.value = value
        self.note = note

    @property
    def where(self):
        return "%s:%d" % (self.path, self.lineno)

    @property
    def ok(self):
        return self.verdict == V_EXPLICIT

    def row(self, width=42):
        return "%-*s %-34s %-13s %s" % (width, self.where, self.callee,
                                        self.verdict, self.value or "-")


def _render(node, limit=48):
    """Source text of an AST node, for the report only."""
    try:
        text = ast.unparse(node)
    except Exception:                                        # pragma: no cover
        return "<unrenderable>"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _imports(tree):
    """(module_alias, bare_name) maps for one parsed file.

    module_alias: local name -> canonical module   (`import subprocess as sp`)
    bare_name:    local name -> (canonical module, real attribute)
                                                  (`from subprocess import run`)

    Collected over the WHOLE tree, not just its top level: a function-local
    `import subprocess` is just as real as a module-level one.
    """
    module_alias, bare_name = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in MODULES:
                    module_alias[alias.asname or alias.name] = alias.name
                # `import asyncio.subprocess` binds the name `asyncio`
                elif alias.name.split(".")[0] in MODULES and not alias.asname:
                    module_alias[alias.name.split(".")[0]] = \
                        alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module in MODULES and node.level == 0:
                for alias in node.names:
                    bare_name[alias.asname or alias.name] = (node.module,
                                                             alias.name)
    return module_alias, bare_name


def _resolve(func, module_alias, bare_name):
    """(canonical_module, attribute, display) for a Call's func, or None.

    Recognises `sp.run(...)`, `asyncio.create_subprocess_exec(...)`,
    `asyncio.subprocess.Popen`-style dotted paths, and bare `run(...)` from a
    `from subprocess import run`.
    """
    if isinstance(func, ast.Attribute):
        base = func.value
        # peel one level so `asyncio.subprocess.X` resolves to module `asyncio`
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            base = base.value
        if isinstance(base, ast.Name):
            module = module_alias.get(base.id)
            if module:
                return module, func.attr, "%s.%s" % (base.id, func.attr)
        return None
    if isinstance(func, ast.Name):
        hit = bare_name.get(func.id)
        if hit:
            return hit[0], hit[1], "%s (from %s)" % (func.id, hit[0])
    return None


def _is_spawn(module, attr):
    return ((module == "subprocess" and attr in SUBPROCESS_SPAWN)
            or (module == "asyncio" and attr in ASYNCIO_SPAWN))


def scan_source(source, path):
    """([Site], parse_error_or_None) for one file's source text.

    An unparseable file is reported, never skipped: a scanner that shrugs at
    files it cannot read is exactly how one goes blind while staying green.
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [], "SyntaxError line %s: %s" % (exc.lineno, exc.msg)

    module_alias, bare_name = _imports(tree)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        hit = _resolve(node.func, module_alias, bare_name)
        if hit is None:
            continue
        module, attr, display = hit

        if attr in NO_STDIN_FORMS.get(module, ()):
            sites.append(Site(path, node.lineno, display, V_FORBIDDEN,
                              note="takes no stdin parameter at all"))
            continue
        if not _is_spawn(module, attr):
            continue

        keywords = {kw.arg for kw in node.keywords if kw.arg}
        splat = any(kw.arg is None for kw in node.keywords)
        if "stdin" in keywords:
            value = next(_render(kw.value) for kw in node.keywords
                         if kw.arg == "stdin")
            sites.append(Site(path, node.lineno, display, V_EXPLICIT, value))
        elif attr in INPUT_IS_EXPLICIT and "input" in keywords:
            value = next(_render(kw.value) for kw in node.keywords
                         if kw.arg == "input")
            sites.append(Site(path, node.lineno, display, V_EXPLICIT,
                              "input=" + value,
                              note="`input=` is mutually exclusive with "
                                   "`stdin=`; it IS the explicit choice"))
        elif splat:
            sites.append(Site(path, node.lineno, display, V_SPLAT,
                              "**" + _render(next(kw.value for kw in
                                                  node.keywords
                                                  if kw.arg is None)),
                              note="a `stdin=` hidden in a **kwargs splat is "
                                   "not visible at the call site; pass it "
                                   "literally"))
        else:
            sites.append(Site(path, node.lineno, display, V_MISSING,
                              note="child INHERITS the parent's stdin"))
    return sites, None


def python_files(root):
    """Every .py under `root`, repo-relative and sorted. Skips __pycache__."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if d not in ("__pycache__", ".git"))
        for name in sorted(files):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def scan_tree(roots, base=None):
    """([Site], [(path, error)], [scanned paths]) for every .py under `roots`."""
    base = base or H.REPO_ROOT
    sites, errors, scanned = [], [], []
    for root in roots:
        for path in python_files(os.path.join(base, root)):
            rel = os.path.relpath(path, base)
            scanned.append(rel)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    source = fh.read()
            except OSError as exc:
                errors.append((rel, "unreadable: %s" % exc))
                continue
            found, error = scan_source(source, rel)
            if error:
                errors.append((rel, error))
            sites.extend(found)
    return sites, errors, scanned


# ---------------------------------------------------------------------------
# negative control fixtures
# ---------------------------------------------------------------------------

def write_text(root, name, body):
    """The ONLY write path in this module -- every target is recorded."""
    target = os.path.join(root, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    WRITES.append(target)
    return target


# name -> (source, {lineno-independent expectations})
#
# `flag` lists the verdicts the file's sites MUST produce, in order.  An empty
# list means "no site at all may be reported from this file", which is the bait
# half of the control.
FIXTURES = {
    # -- MUST be flagged ---------------------------------------------------
    "missing_popen.py": (
        "import subprocess\n"
        "proc = subprocess.Popen(['x'], stdout=subprocess.PIPE)\n",
        [V_MISSING],
    ),
    "missing_multiline.py": (
        # The whole point of the AST path: the call spans five lines and there
        # is a `stdin=` -- in a COMMENT and in an unrelated dict -- that a
        # line-oriented regex would happily accept.
        "import subprocess\n"
        "CONF = {'stdin': 'devnull'}\n"
        "proc = subprocess.Popen(\n"
        "    ['x', '--flag'],\n"
        "    stdout=subprocess.PIPE,   # stdin=subprocess.DEVNULL would go here\n"
        "    stderr=subprocess.PIPE,\n"
        "    text=True,\n"
        ")\n",
        [V_MISSING],
    ),
    "missing_aliased.py": (
        "import subprocess as sp\n"
        "sp.run(['x'], capture_output=True)\n",
        [V_MISSING],
    ),
    "missing_bare_import.py": (
        "from subprocess import Popen, PIPE\n"
        "Popen(['x'], stdout=PIPE)\n",
        [V_MISSING],
    ),
    "missing_asyncio.py": (
        "import asyncio\n"
        "async def go():\n"
        "    return await asyncio.create_subprocess_exec(\n"
        "        'x', '--v',\n"
        "        stdout=asyncio.subprocess.PIPE,\n"
        "    )\n",
        [V_MISSING],
    ),
    "missing_nested_import.py": (
        # a function-local import must be resolved too
        "def go(argv):\n"
        "    import subprocess\n"
        "    return subprocess.check_call(argv)\n",
        [V_MISSING],
    ),
    "splat_kwargs.py": (
        "import subprocess\n"
        "def go(argv, **kw):\n"
        "    return subprocess.Popen(argv, **kw)\n",
        [V_SPLAT],
    ),
    "forbidden_os_system.py": (
        "import os\n"
        "os.system('ls -la')\n",
        [V_FORBIDDEN],
    ),
    "forbidden_getoutput.py": (
        "import subprocess\n"
        "import os\n"
        "a = subprocess.getoutput('id')\n"
        "b = subprocess.getstatusoutput('id')\n"
        "c = os.popen('id').read()\n",
        [V_FORBIDDEN, V_FORBIDDEN, V_FORBIDDEN],
    ),
    # -- must NOT be flagged (bait) ----------------------------------------
    "ok_devnull.py": (
        "import subprocess\n"
        "subprocess.Popen(['x'], stdin=subprocess.DEVNULL)\n",
        [V_EXPLICIT],
    ),
    "ok_input_kw.py": (
        "import subprocess\n"
        "subprocess.run(['x'], input='payload', capture_output=True)\n",
        [V_EXPLICIT],
    ),
    "ok_multiline_buried.py": (
        # the mirror of missing_multiline.py: same shape, `stdin=` present but
        # on neither the first nor the last line of the call
        "import subprocess\n"
        "proc = subprocess.Popen(\n"
        "    ['x', '--flag'],\n"
        "    stdout=subprocess.PIPE,\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stderr=subprocess.PIPE,\n"
        "    text=True,\n"
        ")\n",
        [V_EXPLICIT],
    ),
    "ok_fd_value.py": (
        # an fd, not DEVNULL/PIPE: the assertion is explicitness, not a value
        "import subprocess\n"
        "def go(argv, slave_fd):\n"
        "    return subprocess.Popen(argv, stdin=slave_fd)\n",
        [V_EXPLICIT],
    ),
    "bait_annotation_and_docstring.py": (
        # both real false positives a regex hits in this repo, side by side
        "import subprocess\n"
        "\n"
        "def kill(proc: subprocess.Popen) -> None:\n"
        "    '''Spawned via subprocess.run() elsewhere; subprocess.Popen(...)\n"
        "    is only NAMED here, never called.'''\n"
        "    proc.kill()\n"
        "\n"
        "HANDLER = subprocess.Popen   # a reference, not a call\n",
        [],
    ),
    "bait_unrelated_names.py": (
        # same attribute names on a DIFFERENT object must not resolve
        "class Fake:\n"
        "    def run(self, argv):\n"
        "        return argv\n"
        "shell = Fake()\n"
        "shell.run(['x'])\n"
        "import subprocess\n"
        "subprocess.PIPE\n",
        [],
    ),
}

# Deliberately invalid Python: an unparseable file must be REPORTED, not
# skipped.  Kept out of FIXTURES because it produces an error, not sites.
BROKEN_FIXTURE = ("broken_syntax.py",
                  "import subprocess\n"
                  "subprocess.Popen(['x'\n")


def write_fixtures(root):
    for name, (source, _expect) in sorted(FIXTURES.items()):
        write_text(root, name, source)
    write_text(root, BROKEN_FIXTURE[0], BROKEN_FIXTURE[1])
    return root


# ---------------------------------------------------------------------------
# reporting helpers
# ---------------------------------------------------------------------------

def _site_detail(site):
    detail = ["site        : %s" % site.where,
              "callee      : %s" % site.callee,
              "verdict     : %s" % site.verdict,
              "stdin       : %s" % (site.value or "(absent)")]
    if site.note:
        detail.append("note        : %s" % site.note)
    return detail


def _table(sites):
    """The aligned live table. Width is computed, never clipped: a path
    truncated to fit is a path you cannot paste into an editor."""
    if not sites:
        return ["(no spawn site found)"]
    width = max([len(s.where) for s in sites] + [len("file:line")])
    head = "%-*s %-34s %-13s %s" % (width, "file:line", "callee", "verdict",
                                    "stdin value")
    return [head] + [s.row(width) for s in sites]


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def group_gate(suite):
    """A. Scripts/**: one case per site, then the aggregate invariants."""
    sites, errors, scanned = scan_tree(GATED_ROOTS)
    sites.sort(key=lambda s: (s.path, s.lineno))

    for site in sites:
        problems = []
        if site.verdict == V_MISSING:
            problems.append(
                "no explicit `stdin=`: the child inherits this process's stdin, "
                "which for an MCP server IS the JSON-RPC stream. Pass "
                "stdin=subprocess.DEVNULL (or PIPE/an fd if the child really "
                "needs input)")
        elif site.verdict == V_SPLAT:
            problems.append(
                "`stdin=` may be hidden in a **kwargs splat: pass it literally "
                "at the call site so the choice is visible here")
        elif site.verdict == V_FORBIDDEN:
            problems.append(
                "%s cannot take stdin= at all -- the child always inherits. "
                "Use subprocess.run(..., stdin=subprocess.DEVNULL)"
                % site.callee)
        suite.record(GA, site.where, problems, detail=_site_detail(site),
                     brief="%s | %s | %s | %s"
                           % (H.FAIL if problems else H.PASS, site.where,
                              site.verdict, site.value or "-"))

    offenders = [s for s in sites if not s.ok]
    suite.record(GA, "INVARIANT every spawn site is explicit",
                 [] if not offenders
                 else ["%d site(s) not explicit: %s"
                       % (len(offenders), ", ".join(s.where for s in offenders))],
                 detail=["gated roots : %s" % ", ".join(GATED_ROOTS),
                         "spawn sites : %d in %d file(s)"
                         % (len(sites), len({s.path for s in sites})),
                         "explicit    : %d" % sum(1 for s in sites if s.ok)])

    forbidden = [s for s in sites if s.verdict == V_FORBIDDEN]
    suite.record(GA, "zero os.system / os.popen / getoutput / getstatusoutput",
                 [] if not forbidden
                 else ["%d unsafe-by-construction form(s): %s"
                       % (len(forbidden), ", ".join(s.where for s in forbidden))],
                 detail=["forms watched: %s"
                         % ", ".join("%s.%s" % (m, n)
                                     for m in sorted(NO_STDIN_FORMS)
                                     for n in sorted(NO_STDIN_FORMS[m])),
                         "found        : %d -- 'zero, checked' rather than "
                         "'zero, assumed'" % len(forbidden)])

    suite.record(GA, "every gated file parsed",
                 [] if not errors
                 else ["%d file(s) could not be analysed: %s"
                       % (len(errors), "; ".join("%s (%s)" % e for e in errors))],
                 detail=["files scanned: %d" % len(scanned),
                         "note         : an unparseable file is a FAILURE, not "
                         "a skip -- shrugging at unreadable files is how a "
                         "scanner goes blind while staying green"])

    files = len({s.path for s in sites})
    blind = []
    if len(sites) < MIN_GATED_SITES:
        blind.append("only %d spawn site(s) found (floor %d): the scanner is "
                     "probably no longer resolving call targets"
                     % (len(sites), MIN_GATED_SITES))
    if files < MIN_GATED_FILES:
        blind.append("only %d file(s) contributed a site (floor %d)"
                     % (files, MIN_GATED_FILES))
    suite.record(GA, "scanner is not blind (floor, not a count)", blind,
                 detail=["sites=%d files=%d floors=%d/%d"
                         % (len(sites), files, MIN_GATED_SITES,
                            MIN_GATED_FILES),
                         "note        : a FLOOR, deliberately far below the "
                         "live numbers. Adding a server moves these UP and can "
                         "never trip it; a broken resolver trips it at once"])

    suite.record(GA, "live table (gated)", [], status=H.INFO,
                 detail=_table(sites))
    return sites


def group_survey(suite):
    """B. ClaudeCode/** + tests/**: measured, reported, never gated."""
    sites, errors, scanned = scan_tree(SURVEY_ROOTS)
    sites.sort(key=lambda s: (s.path, s.lineno))

    for site in sites:
        detail = _site_detail(site)
        if not site.ok:
            detail.append("gated       : NO -- see the SCOPE section of this "
                          "module's docstring for why this tree is surveyed "
                          "rather than gated")
        suite.record(GB, site.where, [], status=H.INFO, detail=detail,
                     brief="INFO | %s | %s | %s"
                           % (site.where, site.verdict, site.value or "-"))

    inherit = [s for s in sites if s.verdict != V_EXPLICIT]
    suite.record(GB, "live table (surveyed)", [], status=H.INFO,
                 detail=_table(sites)
                 + ["", "files scanned : %d" % len(scanned),
                    "sites         : %d, of which %d inherit stdin"
                    % (len(sites), len(inherit)),
                    "parse errors  : %d%s"
                    % (len(errors),
                       "" if not errors
                       else " (" + "; ".join("%s: %s" % e for e in errors) + ")")])
    return sites


def group_control(suite, fixture_root):
    """C. planted defects the checker MUST flag, and bait it must not."""
    write_fixtures(fixture_root)
    sites, errors, scanned = scan_tree(["."], base=fixture_root)
    by_file = {}
    for site in sites:
        by_file.setdefault(os.path.basename(site.path), []).append(site)

    for name, (_source, expected) in sorted(FIXTURES.items()):
        found = sorted(by_file.get(name, []), key=lambda s: s.lineno)
        got = [s.verdict for s in found]
        problems = []
        if got != expected:
            problems.append("verdicts %r != expected %r" % (got, expected))
        detail = ["fixture     : %s" % name,
                  "expected    : %r" % expected,
                  "got         : %r" % got]
        detail += ["  %s" % s.row() for s in found]
        suite.record(GC, "control-" + name, problems, detail=detail,
                     brief="%s | control-%s | %r"
                           % (H.FAIL if problems else H.PASS, name, got))

    # The unparseable fixture must surface as an ERROR, and must contribute no
    # sites -- silently skipping it would be the blind failure mode.
    broken = [e for e in errors if os.path.basename(e[0]) == BROKEN_FIXTURE[0]]
    problems = []
    if not broken:
        problems.append("an unparseable file was NOT reported: %r" % (errors,))
    if by_file.get(BROKEN_FIXTURE[0]):
        problems.append("sites were reported from an unparseable file")
    suite.record(GC, "control-unparseable-file-is-reported", problems,
                 detail=["fixture     : %s" % BROKEN_FIXTURE[0],
                         "errors      : %r" % (broken or errors,)])

    # The control must actually fire: a run in which nothing was flagged proves
    # nothing about a clean live tree.
    flagged = [s for s in sites if not s.ok]
    must_flag = sum(1 for _n, (_s, exp) in FIXTURES.items()
                    if any(v != V_EXPLICIT for v in exp))
    problems = []
    if len(flagged) < must_flag:
        problems.append("only %d flagged site(s) from %d fixtures that must "
                        "flag" % (len(flagged), must_flag))
    suite.record(GC, "control fires at all", problems,
                 detail=["fixtures    : %d files + 1 unparseable"
                         % len(FIXTURES),
                         "scanned     : %d" % len(scanned),
                         "flagged     : %d site(s) across %d fixture(s) that "
                         "must flag" % (len(flagged), must_flag),
                         "verdicts    : %s"
                         % ", ".join(sorted({s.verdict for s in flagged}))])

    # And the bait must stay silent: the same run must produce ZERO sites from
    # the annotation/docstring/unrelated-name files -- the two false positives a
    # regex hits in the live tree.
    bait = [n for n, (_s, exp) in FIXTURES.items() if exp == []]
    stray = [s.row() for s in sites
             if os.path.basename(s.path) in bait]
    suite.record(GC, "bait stays silent (annotation, docstring, other object)",
                 [] if not stray else ["bait flagged: %r" % stray],
                 detail=["bait files  : %s" % ", ".join(sorted(bait)),
                         "sites from them: %d" % len(stray),
                         "note        : these are the two REAL regex false "
                         "positives in this repo -- mcp-tshark.py:92 (a type "
                         "annotation) and mcp-git.py:307 (a docstring)"])
    suite.record(GC, "control table", [], status=H.INFO,
                 detail=_table(sorted(sites, key=lambda s: (s.path, s.lineno))))


def group_hygiene(suite, fixture_root, pyc_before):
    """D. every write under .claude/tmp, and no bytecode anywhere."""
    stray = [p for p in WRITES
             if not os.path.abspath(p).startswith(
                 os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GD, "every write lands under .claude/tmp",
                 [] if not stray
                 else ["%d write(s) outside the sandbox: %s"
                       % (len(stray), ", ".join(stray))],
                 detail=["sandbox     : %s/run-<unique>"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "writes      : %d, all inside: %s"
                         % (len(WRITES), not stray),
                         "files       : %s"
                         % ", ".join(sorted(os.path.relpath(p, fixture_root)
                                            for p in WRITES))])

    outside = [r for r in (GATED_ROOTS + SURVEY_ROOTS)
               if os.path.abspath(FIXTURE_BASE).startswith(
                   os.path.abspath(H.repo_path(r)) + os.sep)]
    suite.record(GD, "fixture root is outside every scan root",
                 [] if not outside
                 else ["the sandbox lives inside scan root(s) %r, so a control "
                       "fixture could leak into the live scan" % outside],
                 detail=["sandbox     : %s"
                         % os.path.relpath(FIXTURE_BASE, H.REPO_ROOT),
                         "scan roots  : %s"
                         % ", ".join(GATED_ROOTS + SURVEY_ROOTS)])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GD, "no .pyc written anywhere in the repo tree",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=["pyc before=%d after=%d"
                         % (len(pyc_before), len(pyc_after)),
                         "note        : ZERO, not 'unchanged' -- a pre-existing "
                         "file reads as 1 before / 1 after and sails through a "
                         "delta check"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="explicit stdin= at every subprocess spawn site",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    del WRITES[:]

    # A per-run subdirectory inside the project sandbox: two instances (a
    # standalone run and a tests/run.py run) may overlap, and a fixed path
    # means one instance's teardown deletes the other's fixtures mid-scan.
    os.makedirs(FIXTURE_BASE, exist_ok=True)
    fixture_root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_gate(suite)
        group_survey(suite)
        group_control(suite, fixture_root)
        group_hygiene(suite, fixture_root, pyc_before)
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
