#!/usr/bin/env python3
"""The fleet is pure Python 3.9 + stdlib; every exception is allowlisted and guarded.

THE RULE (docs/adr/0024-pure-python-39-and-the-stdlib.md)
---------------------------------------------------------
Every Python file this repo ships runs on a bare Python 3.9 interpreter with the
standard library and nothing else.  A third-party module is allowed only where
the stdlib has no way to do the job, only from a short ALLOWLIST, and only
behind a guard: `importlib.util.find_spec("<name>")` BEFORE the import, so an
absent package produces a one-line message (or a declared degraded mode)
instead of a traceback, while an installed-but-BROKEN package still fails at
the real import with its own honest traceback.  `try: import x / except
ImportError` is the pattern this replaces: it reports a broken install as a
missing one.

WHAT IS GATED
-------------
A  the live tree -- every .py under Scripts/, ClaudeCode/ and tests/, plus the
   extensionless ClaudeCode/skills/sandbox-run/scripts/sbx (the scope of the
   forge `syntax` target, widened from ClaudeCode/hooks + ClaudeCode/skills to
   all of ClaudeCode/ because ClaudeCode/scripts/ holds shipped scripts too):
     * every import whose top-level name is not 3.9 stdlib and not a repo
       module is on the ALLOWLIST (or a declared per-file EXCEPTION);
     * every allowlisted import is preceded, lexically, by a find_spec call
       naming the same module as a string literal;
     * no allowlisted import sits in the body of a `try` that catches
       ImportError / ModuleNotFoundError or everything;
     * no file imports a module that was 3.9 stdlib and has since been
       REMOVED (PEP 594 and friends) -- it would break on today's interpreter.
   Imports are read with `ast`, never regex: `import x`, `from x import y`,
   function-local imports, and `__import__("x")` / `importlib.import_module
   ("x")` with a literal name.  A dynamic import with a COMPUTED name cannot be
   resolved statically; those sites are listed in an INFO row, not judged.
B  every scanned file parses with `ast.parse(src, feature_version=(3, 9))`.
   THIS CHECKS SYNTAX ONLY.  It cannot see 3.10+ stdlib API use (a
   `str.removeprefix` is fine, `zip(strict=True)` or an `int | None` evaluated
   at runtime is not), and CPython documents feature_version as best-effort:
   group C measures one construct it does NOT refuse on a newer interpreter
   and prints the finding as INFO.  The 3.9 interpreter itself is the only
   complete check; this is the cheap, always-available approximation.
D  the one behavioural consequence of the rule: `lxml` left
   Scripts/search_duckduckgo.py, and its stdlib replacement is pinned to the
   exact fields lxml produced on tests/files/html/tf_bing_serp.html.
F  two rules the import gate cannot see, both read with `ast`:
     * SYSTEM LIBRARIES.  `ctypes` is stdlib, so a shared library loaded
       through it never shows up as an import.  Every literal library name --
       the first argument of ctypes.CDLL / PyDLL / cdll.LoadLibrary /
       ctypes.util.find_library, and every lib<stem>.(so|dylib) element of a
       top-level tuple whose name contains SONAMES or FILES -- must reduce to
       a stem declared in SYSTEM_LIBS with the reason the stdlib cannot do the
       job (brotlidec, zstd).  CDLL(None) names no library -- it is the
       process's own namespace -- and is listed, not judged.  A declared stem
       no scanned file names is a stale licence.
     * NO 3.10+ API in NEW_39_SCOPE (the Chrome client, the two decoders and
       the capture tool).  A LIST of known APIs, not a model of the stdlib:
       int.bit_count; zip/map(strict=); int.to_bytes/from_bytes without the
       length/bytes AND byteorder arguments (both optional only from 3.11);
       bisect.*(key=); itertools.pairwise; create_connection(all_errors=);
       hashlib.file_digest; ssl.VERIFY_X509_PARTIAL_CHAIN; `X | Y` in an
       annotation.  A listed file that does not exist yet is INFO, not PASS.
   Its own planted controls and baits sit in the same group.
G  Scripts/mcp-webfetch.py's PEP 723 block (R-0058, security finding F47):
   every declared requirement carries a >= lower and a < upper bound, and
   every BeautifulSoup() call names the "lxml" tree builder by literal with
   lxml declared in the block -- a builder asked for by STRING is invisible to
   the import gate, so an undeclared one would surface only as FeatureNotFound
   at the first conversion.  lxml stays here by user decision (2026-10-02):
   html.parser has no implied end tags, so an unclosed <li>/<td> nests and
   the markdown loses list items and table cells.  Planted controls in the
   same group.

THE STDLIB SET IS EMBEDDED, NOT DERIVED -- AND WHY
--------------------------------------------------
`sys.stdlib_module_names` is 3.10+, so a suite that must run on 3.9 cannot
rely on it.  More importantly it describes the RUNNING interpreter, and the
question is the 3.9 floor: on 3.14 it lists `tomllib`, which a 3.9 host does
not have, and it omits `imp`, which a 3.9 host does.  So the 3.9 names are
embedded below (STDLIB_39), with two declared deltas (ADDED_AFTER_39,
REMOVED_AFTER_39), and on a 3.10+ interpreter group A cross-checks the table
against `sys.stdlib_module_names`: every embedded name must either exist on the
running interpreter or be declared removed.  A typo in the table therefore
fails here instead of silently widening "stdlib".

DECLARED BLIND SPOTS
--------------------
* A third-party package whose name equals a repo module stem (a `yaml.py` in
  this tree) is read as local.  None exists today.
* The guard check is LEXICAL (a find_spec call earlier in the file), not
  control flow: it proves the author wrote the guard, not that every path
  reaches it.
* A computed dynamic import is listed, not judged.
* Syntax only, as stated in B -- outside NEW_39_SCOPE; inside it group F
  checks the APIs it lists and no others, and does not parse a string
  annotation.
* A ctypes load with a computed name is not judged; the literal it is
  computed from is, only when it sits in a *SONAMES* / *FILES* tuple.  A
  find_library stem held in any other literal (the decoders' ("find_library",
  "zstd") attempt pair) is not read.

NEGATIVE CONTROL (groups C and F) -- mandatory: planted defects the checker
MUST flag and bait it must not, all in `.claude/tmp/test_py_deps/run-<unique>/`, outside
every scan root, removed in a `finally` unless --keep.

Offline, starts nothing, writes only its sandbox.

Usage:
  python3 tests/test_py_deps.py [--brief] [--keep]
Exit code 0 iff every non-informational case passes.
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "py_deps"

SCAN_ROOTS = ["Scripts", "ClaudeCode", "tests"]
EXTRA_FILES = [os.path.join("ClaudeCode", "skills", "sandbox-run", "scripts",
                            "sbx")]
PRUNE_DIRS = {"__pycache__", ".git", ".claude"}

# ---------------------------------------------------------------------------
# the policy tables
# ---------------------------------------------------------------------------

# module -> why the stdlib cannot do this job.  An entry here is permission to
# import the module AT ALL; the find_spec guard is required on top.
ALLOWLIST = {
    "yaml": "a YAML parser: there is none in the stdlib, and a regex cannot "
            "tell well-formed YAML from not",
    "tomli": "the TOML parser backport for 3.9/3.10, where tomllib does not "
             "exist; only ever a fallback, degrading to SKIP when absent",
}

# System shared library stem -> why the stdlib cannot do this job.  The ctypes
# counterpart of ALLOWLIST (group F): `ctypes` IS the stdlib, so the import gate
# never sees a library loaded through it.  A stem here is permission to name
# lib<stem>.so / lib<stem>.dylib in a ctypes load or a *SONAMES* / *FILES*
# tuple; the library may be ABSENT on a host and the loader must degrade to a
# one-line LookupError (ADR 0024 addendum).
SYSTEM_LIBS = {
    "brotlidec": "a brotli (RFC 7932) decoder: the stdlib has none on any "
                 "version (Scripts/_mcp_brotli.py)",
    "zstd": "a zstd (RFC 8878) decoder: the stdlib has none before "
            "compression.zstd in 3.14, far above the 3.9 floor "
            "(Scripts/_mcp_zstd.py)",
}

# Files that must not use a stdlib API newer than 3.9 (group F).  Syntax is
# group B's job; these are the new sources written against the floor, where an
# API check is cheap and exact.  A listed file that does not exist yet is an
# INFO row, never a vacuous PASS.
NEW_39_SCOPE = ("Scripts/_mcp_chrome.py", "Scripts/_mcp_brotli.py",
                "Scripts/_mcp_zstd.py", "Scripts/chrome_capture.py")

# stdlib on a newer interpreter, ABSENT on the 3.9 floor -- so it needs the
# same guard as a third-party module.  module -> first version that has it.
NEWER_STDLIB = {
    "tomllib": (3, 11),
}

# file -> (module names exempt from the allowlist AND the guard, reason).
# Exempting NAMES, not the file, is deliberate: a new dependency added to an
# excepted file still fails.
EXCEPTIONS = {
    "Scripts/mcp-webfetch.py": (
        frozenset({"bs4", "markdownify"}),
        "DECLARED, TEMPORARY (ADR 0024): launched with `uv run --script`, "
        "whose PEP 723 block resolves these; the fetch itself is the stdlib "
        "Chrome client now, but the HTML->Markdown conversion "
        "(bs4 + markdownify) has no stdlib replacement in the tree yet",
    ),
}

# Top-level module names of the Python 3.9 standard library, public names in
# full plus the private ones code in the wild imports.  See the module
# docstring for why this is embedded rather than read off sys.
STDLIB_39 = frozenset("""
__future__ _abc _ast _bisect _codecs _collections_abc _compat_pickle _csv
_datetime _decimal _functools _hashlib _heapq _imp _io _json _locale
_markupbase _operator _osx_support _pickle _posixsubprocess _py_abc
_pydecimal _pyio _random _signal _sitebuiltins _socket _sqlite3 _sre _ssl
_stat _string _strptime _struct _thread _threading_local _tracemalloc
_warnings _weakref _weakrefset _winapi
abc aifc antigravity argparse array ast asynchat asyncio asyncore atexit
audioop base64 bdb binascii binhex bisect builtins bz2 cProfile calendar cgi
cgitb chunk cmath cmd code codecs codeop collections colorsys compileall
concurrent configparser contextlib contextvars copy copyreg crypt csv ctypes
curses dataclasses datetime dbm decimal difflib dis distutils doctest email
encodings ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch
formatter fractions ftplib functools gc genericpath getopt getpass gettext
glob graphlib grp gzip hashlib heapq hmac html http idlelib imaplib imghdr
imp importlib inspect io ipaddress itertools json keyword lib2to3 linecache
locale logging lzma mailbox mailcap marshal math mimetypes mmap modulefinder
msilib msvcrt multiprocessing netrc nis nntplib nt ntpath nturl2path numbers
opcode operator optparse os ossaudiodev parser pathlib pdb pickle pickletools
pipes pkgutil platform plistlib poplib posix posixpath pprint profile pstats
pty pwd py_compile pyclbr pydoc pydoc_data pyexpat queue quopri random re
readline reprlib resource rlcompleter runpy sched secrets select selectors
shelve shlex shutil signal site smtpd smtplib sndhdr socket socketserver spwd
sqlite3 sre_compile sre_constants sre_parse ssl stat statistics string
stringprep struct subprocess sunau symbol symtable sys sysconfig syslog
tabnanny tarfile telnetlib tempfile termios textwrap this threading time
timeit tkinter token tokenize trace traceback tracemalloc tty turtle turtledemo
types typing unicodedata unittest urllib uu uuid venv warnings wave weakref
webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile zipimport
zlib zoneinfo
""".split())

# 3.9 stdlib modules removed since: importing one breaks on a current
# interpreter even though 3.9 has it.  module -> removed in.
REMOVED_AFTER_39 = {
    "formatter": (3, 10), "parser": (3, 10), "symbol": (3, 10),
    "binhex": (3, 11),
    "asynchat": (3, 12), "asyncore": (3, 12), "distutils": (3, 12),
    "imp": (3, 12), "smtpd": (3, 12),
    "aifc": (3, 13), "audioop": (3, 13), "cgi": (3, 13), "cgitb": (3, 13),
    "chunk": (3, 13), "crypt": (3, 13), "imghdr": (3, 13), "lib2to3": (3, 13),
    "mailcap": (3, 13), "msilib": (3, 13), "nis": (3, 13), "nntplib": (3, 13),
    "ossaudiodev": (3, 13), "pipes": (3, 13), "sndhdr": (3, 13),
    "spwd": (3, 13), "sunau": (3, 13), "telnetlib": (3, 13), "uu": (3, 13),
    "xdrlib": (3, 13),
}

# Public stdlib names that arrived after 3.9 -- the other declared delta.  Not
# stdlib for this fleet: importing one is judged like a third-party module
# (NEWER_STDLIB can make it legal behind a guard).
ADDED_AFTER_39 = frozenset({"tomllib", "annotationlib", "compression"})

GA = "A. gate: third-party imports allowlisted and find_spec-guarded"
GB = "B. syntax: every file parses as Python 3.9 (syntax only)"
GC = "C. negative control"
GD = "D. the lxml replacement, pinned to lxml's own output"
GE = "E. hygiene"
GF = "F. ctypes system libraries declared; no 3.10+ API in the new sources"
GG = "G. webfetch PEP 723 block: bounded, the lxml builder declared"

FIXTURE_BASE = H.repo_path(".claude", "tmp", "test_py_deps")
WRITES = []

BING_HTML = H.repo_path("tests", "files", "html", "tf_bing_serp.html")
BING_EXPECTED = H.repo_path("tests", "files", "html",
                            "tf_bing_serp.expected.json")
SEARCH_DDG = H.repo_path("Scripts", "search_duckduckgo.py")
WEBFETCH = H.repo_path("Scripts", "mcp-webfetch.py")

# Blindness FLOORS, not counts -- far below the live numbers, which move up as
# the tree grows and can never trip these; a scanner that stops resolving
# anything trips them at once.
MIN_FILES = 40
MIN_GUARDED_IMPORTS = 5

# verdicts
V_OK = "guarded"
V_EXEMPT = "exempt"
V_UNLISTED = "not-allowlisted"
V_UNGUARDED = "unguarded"
V_MASKED = "except-ImportError"
V_REMOVED = "removed-stdlib"

_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------

class Imp:
    """One import of a module that is neither 3.9 stdlib nor a repo module."""

    def __init__(self, path, lineno, top, form, masked):
        self.path = path
        self.lineno = lineno
        self.top = top
        self.form = form
        self.masked = masked
        self.verdicts = []

    @property
    def where(self):
        return "%s:%d" % (self.path, self.lineno)

    @property
    def ok(self):
        return self.verdicts in ([V_OK], [V_EXEMPT])

    def row(self, width=44):
        return "%-*s %-12s %-10s %s" % (width, self.where, self.top, self.form,
                                        ",".join(self.verdicts))


def _catches_import_error(try_node):
    for handler in try_node.handlers:
        kind = handler.type
        if kind is None:
            return True                      # bare `except:` masks it too
        names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
        for name in names:
            if isinstance(name, ast.Name) and name.id in _IMPORT_ERRORS:
                return True
            if isinstance(name, ast.Attribute) and name.attr in _IMPORT_ERRORS:
                return True
    return False


_TRY_TYPES = tuple(t for t in (getattr(ast, "Try", None),
                               getattr(ast, "TryStar", None)) if t)


def _literal_arg(call):
    if call.args and isinstance(call.args[0], ast.Constant) \
            and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _dynamic_import(call):
    """'literal' / None (computed) for __import__ / importlib.import_module,
    or False when the call is not a dynamic import at all."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "__import__":
        return _literal_arg(call)
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        return _literal_arg(call)
    return False


def _is_find_spec(call):
    func = call.func
    return ((isinstance(func, ast.Attribute) and func.attr == "find_spec")
            or (isinstance(func, ast.Name) and func.id == "find_spec"))


def analyse(tree):
    """(imports, find_spec, dynamic) for one parsed module.

    imports   [(lineno, top, form, masked)]  every import, stdlib or not
    find_spec {top: [lineno]}                 literal find_spec calls
    dynamic   [lineno]                        computed-name dynamic imports
    """
    imports, find_spec, dynamic = [], {}, []

    def visit(node, masked):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name.split(".")[0],
                                "import", masked))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.append((node.lineno, node.module.split(".")[0],
                                "from", masked))
        elif isinstance(node, ast.Call):
            dyn = _dynamic_import(node)
            if dyn:
                imports.append((node.lineno, dyn.split(".")[0], "dynamic",
                                masked))
            elif dyn is None:
                dynamic.append(node.lineno)
            if _is_find_spec(node):
                name = _literal_arg(node)
                if name:
                    find_spec.setdefault(name.split(".")[0],
                                         []).append(node.lineno)
        for field, value in ast.iter_fields(node):
            child_masked = masked
            if isinstance(node, _TRY_TYPES) and field == "body":
                child_masked = masked or _catches_import_error(node)
            for child in (value if isinstance(value, list) else [value]):
                if isinstance(child, ast.AST):
                    visit(child, child_masked)

    visit(tree, False)
    return imports, find_spec, dynamic


def judge(path, tree, local_names):
    """([Imp] for every non-stdlib, non-local import, [dynamic lineno])."""
    imports, find_spec, dynamic = analyse(tree)
    exempt = EXCEPTIONS.get(path, (frozenset(), ""))[0]
    found = []
    for lineno, top, form, masked in imports:
        if top in local_names:
            continue
        if top in STDLIB_39 and top not in REMOVED_AFTER_39:
            continue
        imp = Imp(path, lineno, top, form, masked)
        if top in REMOVED_AFTER_39:
            imp.verdicts.append(V_REMOVED)
        elif top in exempt:
            imp.verdicts.append(V_EXEMPT)
        elif top not in ALLOWLIST and top not in NEWER_STDLIB:
            imp.verdicts.append(V_UNLISTED)
        else:
            if not any(line < lineno for line in find_spec.get(top, ())):
                imp.verdicts.append(V_UNGUARDED)
            if masked:
                imp.verdicts.append(V_MASKED)
            if not imp.verdicts:
                imp.verdicts.append(V_OK)
        found.append(imp)
    return found, dynamic


def source_files(base, roots, extra=()):
    """Repo-relative paths of every .py under roots, plus `extra`, sorted."""
    out = []
    for root in roots:
        for dirpath, dirs, files in os.walk(os.path.join(base, root)):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE_DIRS)
            for name in sorted(files):
                if name.endswith(".py"):
                    out.append(os.path.relpath(os.path.join(dirpath, name),
                                               base))
    for rel in extra:
        if os.path.isfile(os.path.join(base, rel)):
            out.append(rel)
    return sorted(set(p.replace(os.sep, "/") for p in out))


def local_module_names(base, files):
    """Every name a repo file could be imported as: .py stems and packages."""
    names = set()
    for rel in files:
        stem = os.path.splitext(os.path.basename(rel))[0]
        names.add(stem)
        parent = os.path.dirname(os.path.join(base, rel))
        if os.path.isfile(os.path.join(parent, "__init__.py")):
            names.add(os.path.basename(parent))
    return names


def scan(base, roots, extra=()):
    """(imps, parse_errors, syntax39_errors, dynamic, files) over a tree."""
    files = source_files(base, roots, extra)
    local = local_module_names(base, files)
    imps, errors, syn39, dynamic = [], [], [], []
    for rel in files:
        try:
            with open(os.path.join(base, rel), "r", encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append((rel, "unreadable: %s" % exc))
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            errors.append((rel, "SyntaxError line %s: %s" % (exc.lineno,
                                                            exc.msg)))
            continue
        try:
            ast.parse(source, filename=rel, feature_version=(3, 9))
        except SyntaxError as exc:
            syn39.append((rel, "line %s: %s" % (exc.lineno, exc.msg)))
        found, dyn = judge(rel, tree, local)
        imps.extend(found)
        dynamic.extend("%s:%d" % (rel, line) for line in dyn)
    return imps, errors, syn39, dynamic, files


# ---------------------------------------------------------------------------
# group F checkers: ctypes library stems, and 3.10+ stdlib API use
# ---------------------------------------------------------------------------

# lib<stem>[.N...].(so|dylib)[.N...], optionally behind a directory.  The stem
# is matched lazily so a version between the stem and the suffix
# (libbrotlidec.1.dylib) is not swallowed into it.
_LIB_NAME_RX = re.compile(
    r"^(?:.*/)?lib([A-Za-z0-9_+-]+?)(?:\.[0-9]+)*\.(?:so|dylib)(?:\.[0-9]+)*$")

# ctypes calls whose first argument names a library FILE (ctypes.CDLL,
# ctypes.PyDLL, ctypes.cdll.LoadLibrary); find_library takes a bare STEM.
_CTYPES_LOADERS = frozenset({"CDLL", "PyDLL", "LoadLibrary"})

# A top-level tuple whose name carries one of these is a load-order table.
_LIB_TABLE_WORDS = ("SONAMES", "FILES")


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _lib_stem(text):
    """The stem of a library file name, or None when it is not one."""
    match = _LIB_NAME_RX.match(text)
    return match.group(1) if match else None


def library_stems(tree):
    """(sites, own) for one parsed module.

    sites [(lineno, form, literal, stem)]  every literal library name: the
          first argument of a ctypes load or find_library, and every lib-named
          string element of a top-level *SONAMES* / *FILES* tuple.  stem is
          None when a load names something that is not lib<stem>.(so|dylib).
    own   [lineno]  CDLL(None): the process's own namespace, no library named.
    A load with a COMPUTED name is not a site: the literal it is computed from
    is, when it sits in a load-order table.
    """
    sites, own = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _CTYPES_LOADERS and name != "find_library":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        value = node.args[0].value
        if value is None and name in _CTYPES_LOADERS:
            own.append(node.lineno)
        elif isinstance(value, str):
            stem = _lib_stem(value)
            if name == "find_library" and stem is None:
                stem = value
            sites.append((node.lineno, name, value, stem))
    for stmt in tree.body:
        if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, (ast.Tuple, ast.List))):
            continue
        table = stmt.targets[0].id
        if not any(word in table for word in _LIB_TABLE_WORDS):
            continue
        for elt in stmt.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                stem = _lib_stem(elt.value)
                if stem is not None:
                    sites.append((elt.lineno, table, elt.value, stem))
    return sorted(sites), sorted(own)


# (module, name) pairs that do not exist on 3.9 when imported by name.
_NEW_FROM_IMPORTS = {
    ("itertools", "pairwise"): "itertools.pairwise (3.10)",
    ("hashlib", "file_digest"): "hashlib.file_digest (3.11)",
    ("ssl", "VERIFY_X509_PARTIAL_CHAIN"): "ssl.VERIFY_X509_PARTIAL_CHAIN (3.10)",
}

_BISECT_FUNCS = frozenset({"bisect", "bisect_left", "bisect_right", "insort",
                           "insort_left", "insort_right"})

# int.to_bytes / int.from_bytes: the first parameter's keyword name.  Both
# gained defaults for it AND for byteorder in 3.11; on 3.9 both are required.
_INT_BYTES_FIRST = {"to_bytes": "length", "from_bytes": "bytes"}


def _annotations(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        params = (list(getattr(args, "posonlyargs", [])) + args.args
                  + args.kwonlyargs + [a for a in (args.vararg, args.kwarg)
                                       if a is not None])
        return [p.annotation for p in params if p.annotation is not None] \
            + ([node.returns] if node.returns is not None else [])
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    return []


def api_310(tree):
    """[(lineno, what)] -- every 3.10+ stdlib API use this rule knows, sorted.

    The rule is a LIST of known APIs, not a model of the stdlib: an API not
    listed here is not seen (declared blind spot), and a string annotation is
    not parsed.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "bit_count":
                found.append((node.lineno, "int.bit_count (3.10)"))
            elif node.attr == "VERIFY_X509_PARTIAL_CHAIN":
                found.append((node.lineno,
                              "ssl.VERIFY_X509_PARTIAL_CHAIN (3.10)"))
            elif node.attr == "pairwise" and _is_name(node.value, "itertools"):
                found.append((node.lineno, "itertools.pairwise (3.10)"))
            elif node.attr == "file_digest" and _is_name(node.value,
                                                         "hashlib"):
                found.append((node.lineno, "hashlib.file_digest (3.11)"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                what = _NEW_FROM_IMPORTS.get((node.module, alias.name))
                if what:
                    found.append((node.lineno, what))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            kw = {k.arg for k in node.keywords if k.arg}
            splat = (any(k.arg is None for k in node.keywords)
                     or any(isinstance(a, ast.Starred) for a in node.args))
            if name in ("zip", "map") and isinstance(node.func, ast.Name) \
                    and "strict" in kw:
                found.append((node.lineno, "%s(strict=) (%s)"
                              % (name, "3.10" if name == "zip" else "3.14")))
            elif name in _BISECT_FUNCS and "key" in kw \
                    and (isinstance(node.func, ast.Name)
                         or _is_name(node.func.value, "bisect")):
                found.append((node.lineno, "bisect.%s(key=) (3.10)" % name))
            elif name == "create_connection" and "all_errors" in kw:
                found.append((node.lineno,
                              "create_connection(all_errors=) (3.11)"))
            elif name in _INT_BYTES_FIRST \
                    and isinstance(node.func, ast.Attribute) and not splat:
                first = len(node.args) >= 1 or _INT_BYTES_FIRST[name] in kw
                order = len(node.args) >= 2 or "byteorder" in kw
                if not (first and order):
                    found.append((node.lineno,
                                  "%s without an explicit %s (defaults are "
                                  "3.11)" % (name, " and ".join(
                                      w for w, ok in
                                      ((_INT_BYTES_FIRST[name], first),
                                       ("byteorder", order)) if not ok))))
        for ann in _annotations(node):
            if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
                   for n in ast.walk(ann)):
                found.append((ann.lineno, "X | Y in an annotation (3.10)"))
    return sorted(found)


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


# name -> (source, expected verdict lists in line order).  [] means the file
# must contribute NO judged import at all -- the bait half.
FIXTURES = {
    "unlisted_requests.py": (
        "import requests\n",
        [[V_UNLISTED]]),
    # the browser-impersonation libraries left the allowlist when the stdlib
    # Chrome client replaced them: importing one again must be refused
    "unlisted_primp.py": (
        "import primp\n",
        [[V_UNLISTED]]),
    "unlisted_dynamic.py": (
        "import importlib\n"
        "lx = importlib.import_module('lxml.html')\n"
        "bs = __import__('bs4')\n",
        [[V_UNLISTED], [V_UNLISTED]]),
    "allowlisted_unguarded.py": (
        "import yaml\n",
        [[V_UNGUARDED]]),
    "guard_after_import.py": (
        "import importlib.util\n"
        "def go():\n"
        "    import tomli\n"
        "    return tomli\n"
        "HAVE = importlib.util.find_spec('tomli') is not None\n",
        [[V_UNGUARDED]]),
    "guard_other_module.py": (
        "import importlib.util\n"
        "if importlib.util.find_spec('yaml') is None:\n"
        "    raise SystemExit(2)\n"
        "import tomli\n",
        [[V_UNGUARDED]]),
    "masked_except_importerror.py": (
        "import importlib.util\n"
        "OK = importlib.util.find_spec('yaml') is not None\n"
        "try:\n"
        "    import yaml\n"
        "except ImportError:\n"
        "    yaml = None\n",
        [[V_MASKED]]),
    "masked_bare_except.py": (
        "try:\n"
        "    import tomli\n"
        "except:\n"
        "    tomli = None\n",
        [[V_UNGUARDED, V_MASKED]]),
    "newer_stdlib_unguarded.py": (
        "import tomllib\n",
        [[V_UNGUARDED]]),
    "removed_stdlib.py": (
        "import imp\n"
        "from distutils import core\n",
        [[V_REMOVED], [V_REMOVED]]),
    # -- bait: must stay silent or pass --------------------------------------
    "ok_guarded.py": (
        "import importlib.util\n"
        "import sys\n"
        "if importlib.util.find_spec('yaml') is None:\n"
        "    sys.exit(2)\n"
        "from yaml import safe_load\n",
        [[V_OK]]),
    "ok_guarded_fallback.py": (
        "import importlib.util\n"
        "if importlib.util.find_spec('tomllib') is not None:\n"
        "    import tomllib as toml\n"
        "elif importlib.util.find_spec('tomli') is not None:\n"
        "    import tomli as toml\n",
        [[V_OK], [V_OK]]),
    "bait_stdlib_local_relative.py": (
        "from __future__ import annotations\n"
        "import os.path, json\n"
        "from . import sibling\n"
        "import _fixture_sibling\n"
        "def f():\n"
        "    import xml.etree.ElementTree as ET\n"
        "    return ET\n"
        "# import requests  (a comment is not an import)\n"
        "DOC = 'import lxml'\n",
        []),
    "_fixture_sibling.py": (
        "VALUE = 1\n",
        []),
    # -- group F: every import below is stdlib, so the import gate stays
    # silent ([]); GF_FIXTURES says which group-F rule judges each file, and
    # the lines that rule MUST flag end in `# FLAG` -- everything else is bait.
    "undeclared_soname.py": (
        "import ctypes\n"
        "import ctypes.util\n"
        "LIB = ctypes.CDLL('libfoo.so.1')  # FLAG\n"
        "ALT = ctypes.cdll.LoadLibrary('/usr/lib/libbar.dylib')  # FLAG\n"
        "PATH = ctypes.util.find_library('crypto')  # FLAG\n"
        "ODD = ctypes.CDLL('msvcrt')  # FLAG\n"
        "FOO_SONAMES = ('libfoo.so.2',)  # FLAG\n",
        []),
    "declared_soname.py": (
        "import ctypes\n"
        "import ctypes.util\n"
        "ZSTD = ctypes.CDLL('libzstd.so.1')\n"
        "BR = ctypes.cdll.LoadLibrary('/opt/homebrew/lib/libbrotlidec.1.dylib')\n"
        "PATH = ctypes.util.find_library('zstd')\n"
        "OWN = ctypes.CDLL(None, use_errno=True)\n"
        "DYN = ctypes.CDLL(PATH)\n"
        "BAR_SONAMES = ('libbrotlidec.so.1', 'libzstd.1.dylib')\n"
        "DATA_FILES = ('README.md', 'notes.txt')\n"
        "DOC = \"ctypes.CDLL('libfoo.so.1')\"\n"
        "# ctypes.CDLL('libfoo.so.1')  a comment is not a load\n",
        []),
    "api_310.py": (
        "import bisect\n"
        "import hashlib\n"
        "import itertools\n"
        "import socket\n"
        "import ssl\n"
        "from itertools import pairwise  # FLAG\n"
        "x = 5\n"
        "b = b'ab'\n"
        "n = x.bit_count()  # FLAG\n"
        "t0 = (5).to_bytes()  # FLAG\n"
        "t1 = (5).to_bytes(byteorder='big')  # FLAG\n"
        "t2 = (5).to_bytes(2, signed=False)  # FLAG\n"
        "f0 = int.from_bytes(b, signed=True)  # FLAG\n"
        "v = ssl.VERIFY_X509_PARTIAL_CHAIN  # FLAG\n"
        "z = zip([1], [2], strict=True)  # FLAG\n"
        "m = map(abs, [1], strict=True)  # FLAG\n"
        "i = bisect.bisect_left([1], 1, key=abs)  # FLAG\n"
        "p = itertools.pairwise([1, 2])  # FLAG\n"
        "c = socket.create_connection(('127.0.0.1', 1), all_errors=True)  # FLAG\n"
        "d = hashlib.file_digest(None, 'sha256')  # FLAG\n"
        "def f(a: int | None) -> None:  # FLAG\n"
        "    return None\n"
        "y: int | None = None  # FLAG\n",
        []),
    "api_39_ok.py": (
        "import bisect\n"
        "import ssl\n"
        "b = b'ab'\n"
        "x = 5\n"
        "t0 = (5).to_bytes(2, byteorder='big')\n"
        "t1 = (5).to_bytes(2, 'big', signed=True)\n"
        "t2 = (5).to_bytes(length=2, byteorder='big')\n"
        "f0 = int.from_bytes(b, 'big')\n"
        "f1 = int.from_bytes(bytes=b, byteorder='little')\n"
        "n = x.bit_length()\n"
        "z = zip([1], [2])\n"
        "i = bisect.bisect_left([1], 1)\n"
        "v = ssl.VERIFY_X509_TRUSTED_FIRST\n"
        "g = x | 1\n"
        "def f(a: int = 0) -> int:\n"
        "    return a\n"
        "DOC = 'x.bit_count() and zip(strict=True)'\n",
        []),
}

# group F: fixture -> the rule that judges it ("lib" = library_stems against
# SYSTEM_LIBS, "api" = api_310).  Expected hits are the `# FLAG` lines.
GF_FIXTURES = {
    "undeclared_soname.py": "lib",
    "declared_soname.py": "lib",
    "api_310.py": "api",
    "api_39_ok.py": "api",
}

# Syntax controls: name -> (source, must_fail_under_3_9)
SYNTAX_FIXTURES = {
    "syn_match_310.py": ("def f(x):\n"
                         "    match x:\n"
                         "        case 1:\n"
                         "            return 'one'\n"
                         "        case _:\n"
                         "            return 'other'\n", True),
    "syn_ok_39.py": ("def f(a, /, b, *, c):\n"
                     "    if (n := len(b)) > 1:\n"
                     "        return {**c, 'n': n}\n"
                     "    return a\n", False),
}

# A construct newer than 3.9 that feature_version is NOT documented to refuse:
# the PEP 701 f-string (3.12) reusing its own quote.  Measured and printed as
# INFO -- this is the blind spot the docstring declares, shown rather than
# asserted, because the answer depends on the interpreter running the suite.
BLIND_FIXTURE = ("syn_blind_pep701.py", "d = {'a': 1}\ns = f\"{d[\"a\"]}\"\n")


def write_fixtures(root):
    for name, (source, _exp) in sorted(FIXTURES.items()):
        write_text(root, name, source)
    return root


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _table(imps):
    if not imps:
        return ["(no non-stdlib import found)"]
    width = max([len(i.where) for i in imps] + [len("file:line")])
    head = "%-*s %-12s %-10s %s" % (width, "file:line", "module", "form",
                                    "verdict")
    return [head] + [i.row(width) for i in imps]


def group_gate(suite):
    """A. the live tree."""
    imps, errors, _syn, dynamic, files = scan(H.REPO_ROOT, SCAN_ROOTS,
                                              EXTRA_FILES)
    imps.sort(key=lambda i: (i.path, i.lineno))

    suite.record(GA, "every scanned file parsed",
                 ["%d file(s) could not be analysed: %s"
                  % (len(errors), "; ".join("%s (%s)" % e for e in errors))]
                 if errors else [],
                 detail=["files scanned: %d" % len(files),
                         "roots        : %s + %s"
                         % (", ".join(SCAN_ROOTS), ", ".join(EXTRA_FILES))])

    unlisted = [i for i in imps if V_UNLISTED in i.verdicts]
    suite.record(GA, "every non-stdlib import is allowlisted",
                 ["%d import(s) of a module outside the allowlist: %s"
                  % (len(unlisted), ", ".join("%s (%s)" % (i.where, i.top)
                                              for i in unlisted))]
                 if unlisted else [],
                 detail=["allowlist   : %s" % ", ".join(sorted(ALLOWLIST)),
                         "newer stdlib: %s (guarded like the allowlist)"
                         % ", ".join(sorted(NEWER_STDLIB)),
                         "fix         : use the stdlib; if there is truly no "
                         "stdlib way, extend ALLOWLIST with the reason and "
                         "record it in ADR 0024"])

    unguarded = [i for i in imps if V_UNGUARDED in i.verdicts]
    suite.record(GA, "every allowlisted import is find_spec-guarded",
                 ["%d allowlisted import(s) with no find_spec(<literal>) "
                  "before them: %s" % (len(unguarded),
                                       ", ".join(i.where for i in unguarded))]
                 if unguarded else [],
                 detail=["rule        : importlib.util.find_spec('<name>') on "
                         "an earlier line, naming the module as a literal",
                         "guarded     : %d" % sum(1 for i in imps
                                                  if i.verdicts == [V_OK])])

    masked = [i for i in imps if V_MASKED in i.verdicts]
    suite.record(GA, "no allowlisted import under except ImportError",
                 ["%d import(s) inside a try that catches ImportError: %s"
                  % (len(masked), ", ".join(i.where for i in masked))]
                 if masked else [],
                 detail=["why         : `except ImportError` reports a BROKEN "
                         "install as a missing one; find_spec does not import"])

    removed = [i for i in imps if V_REMOVED in i.verdicts]
    suite.record(GA, "no import of a stdlib module removed after 3.9",
                 ["%d import(s) of a removed module: %s"
                  % (len(removed), ", ".join("%s (%s)" % (i.where, i.top)
                                             for i in removed))]
                 if removed else [],
                 detail=["watched     : %d modules (PEP 594 and earlier "
                         "removals)" % len(REMOVED_AFTER_39)])

    stale = []
    for path, (names, _why) in sorted(EXCEPTIONS.items()):
        used = {i.top for i in imps if i.path == path}
        for name in sorted(names - used):
            stale.append("%s no longer imports %s" % (path, name))
    suite.record(GA, "declared exceptions are still live", stale,
                 detail=["%s: %s -- %s" % (p, ", ".join(sorted(n)), why)
                         for p, (n, why) in sorted(EXCEPTIONS.items())]
                 + ["rule        : an exception that exempts nothing is a "
                    "stale licence; delete it"])

    problems, note = [], []
    names = getattr(sys, "stdlib_module_names", None)
    if names is None:
        note.append("interpreter %s has no sys.stdlib_module_names (3.10+): "
                    "cross-check not possible here"
                    % sys.version.split()[0])
    else:
        unknown = sorted(n for n in STDLIB_39
                         if n not in names and n not in REMOVED_AFTER_39)
        if unknown:
            problems.append("embedded 3.9 names this interpreter does not "
                            "know and REMOVED_AFTER_39 does not declare: %s"
                            % ", ".join(unknown))
        resurrected = sorted(n for n in REMOVED_AFTER_39 if n in names
                             and sys.version_info >= REMOVED_AFTER_39[n])
        if resurrected:
            problems.append("declared removed but present: %s"
                            % ", ".join(resurrected))
        newer = sorted(n for n in names if not n.startswith("_")
                       and n not in STDLIB_39 and n not in ADDED_AFTER_39)
        note.append("interpreter : %s, %d stdlib names"
                    % (sys.version.split()[0], len(names)))
        note.append("public names newer than 3.9 and undeclared (INFO, "
                    "judged as third-party anyway): %s"
                    % (", ".join(newer) or "none"))
    # INFO, not a vacuous PASS, where there is nothing to compare against
    suite.record(GA, "embedded 3.9 stdlib table matches this interpreter",
                 problems, status=H.INFO if names is None else None,
                 detail=note + [
                     "embedded    : %d names, %d declared removed, %d "
                     "declared added" % (len(STDLIB_39),
                                         len(REMOVED_AFTER_39),
                                         len(ADDED_AFTER_39))])

    guarded = sum(1 for i in imps if i.verdicts == [V_OK])
    blind = []
    if len(files) < MIN_FILES:
        blind.append("only %d file(s) scanned (floor %d)"
                     % (len(files), MIN_FILES))
    if guarded < MIN_GUARDED_IMPORTS:
        blind.append("only %d guarded import(s) found (floor %d): the "
                     "resolver is probably blind" % (guarded,
                                                     MIN_GUARDED_IMPORTS))
    suite.record(GA, "scanner is not blind (floor, not a count)", blind,
                 detail=["files=%d guarded=%d floors=%d/%d"
                         % (len(files), guarded, MIN_FILES,
                            MIN_GUARDED_IMPORTS)])

    suite.record(GA, "live table", [], status=H.INFO, detail=_table(imps))
    suite.record(GA, "computed dynamic imports (listed, not judged)", [],
                 status=H.INFO,
                 detail=["sites       : %s" % (", ".join(dynamic) or "none"),
                         "blind spot  : a computed module name cannot be "
                         "resolved statically"])
    usage = {name: sorted({i.path for i in imps if i.top == name})
             for name in sorted(ALLOWLIST) + sorted(NEWER_STDLIB)}
    suite.record(GA, "allowlist usage", [], status=H.INFO,
                 detail=["%-10s %s -- %s" % (n, ", ".join(p) or "(unused)",
                                             ALLOWLIST.get(n, "stdlib from "
                                                           "%d.%d" %
                                                           NEWER_STDLIB.get(
                                                               n, (0, 0))))
                         for n, p in usage.items()])
    return files


def group_syntax(suite):
    """B. every scanned file parses under the 3.9 grammar."""
    _imps, _errors, syn39, _dyn, files = scan(H.REPO_ROOT, SCAN_ROOTS,
                                              EXTRA_FILES)
    suite.record(GB, "every file parses with feature_version=(3, 9)",
                 ["%d file(s) use syntax newer than 3.9: %s"
                  % (len(syn39), "; ".join("%s (%s)" % e for e in syn39))]
                 if syn39 else [],
                 detail=["files       : %d" % len(files),
                         "scope       : SYNTAX ONLY -- a 3.10+ stdlib API "
                         "call parses fine here and is not seen"])


def group_control(suite, root):
    """C. planted defects the checker MUST flag, and bait it must not."""
    write_fixtures(root)
    imps, errors, _syn, _dyn, files = scan(root, ["."])
    by_file = {}
    for imp in imps:
        by_file.setdefault(os.path.basename(imp.path), []).append(imp)

    for name, (_src, expected) in sorted(FIXTURES.items()):
        got = [i.verdicts for i in sorted(by_file.get(name, []),
                                          key=lambda i: i.lineno)]
        suite.record(GC, "control-" + name,
                     [] if got == expected
                     else ["verdicts %r != expected %r" % (got, expected)],
                     detail=["expected    : %r" % expected,
                             "got         : %r" % got])

    must = sum(1 for _s, exp in FIXTURES.values()
               if any(v not in ([V_OK], [V_EXEMPT]) for v in exp))
    fired = len({os.path.basename(i.path) for i in imps if not i.ok})
    suite.record(GC, "control fires at all",
                 [] if fired >= must and not errors
                 else ["%d fixture(s) flagged, %d must be; errors=%r"
                       % (fired, must, errors)],
                 detail=["fixtures    : %d, scanned %d" % (len(FIXTURES),
                                                           len(files))])

    # the exception table exempts NAMES of one file, not the file
    tree = ast.parse("import bs4\nimport requests\n")
    exempted, _d = judge("Scripts/mcp-webfetch.py", tree, set())
    got = [i.verdicts for i in exempted]
    suite.record(GC, "control-exception-exempts-names-not-files",
                 [] if got == [[V_EXEMPT], [V_UNLISTED]]
                 else ["verdicts %r" % (got,)],
                 detail=["planted     : bs4 (declared) + requests (not) in "
                         "the excepted file",
                         "got         : %r" % (got,)])

    for name, (source, must_fail) in sorted(SYNTAX_FIXTURES.items()):
        path = write_text(root, os.path.join("syntax", name), source)
        try:
            ast.parse(source, filename=path, feature_version=(3, 9))
            failed, msg = False, "parsed"
        except SyntaxError as exc:
            failed, msg = True, exc.msg
        suite.record(GC, "control-" + name,
                     [] if failed == must_fail
                     else ["feature_version=(3, 9) %s it, expected %s"
                           % ("refused" if failed else "accepted",
                              "refusal" if must_fail else "acceptance")],
                     detail=["must fail   : %s" % must_fail,
                             "result      : %s" % msg])

    name, source = BLIND_FIXTURE
    write_text(root, os.path.join("syntax", name), source)
    try:
        ast.parse(source, feature_version=(3, 9))
        verdict = "ACCEPTED -- the declared blind spot, measured"
    except SyntaxError as exc:
        verdict = "refused (%s)" % exc.msg
    suite.record(GC, "measured-blind-spot-" + name, [], status=H.INFO,
                 detail=["construct   : PEP 701 f-string (3.12) reusing its "
                         "quote",
                         "interpreter : %s" % sys.version.split()[0],
                         "feature_version=(3, 9): %s" % verdict])


def group_bing(suite):
    """D. the stdlib Bing parser reproduces lxml's fields exactly."""
    try:
        with open(BING_HTML, encoding="utf-8") as fh:
            html_text = fh.read()
        with open(BING_EXPECTED, encoding="utf-8") as fh:
            expected = json.load(fh)
        mod = H.load_module_from_path("_py_deps_search_ddg", SEARCH_DDG)
    except Exception as exc:                       # pragma: no cover
        for cid in ("fixture parses to lxml's fields",
                    "control: without libxml2's start-close table it differs",
                    "empty and blank input give no results"):
            suite.record(GD, cid, ["setup failed: %s: %s"
                                   % (type(exc).__name__, exc)])
        return

    got = mod.parse_bing_results(html_text)
    diff = [("#%d" % n, g, e) for n, (g, e) in enumerate(zip(got, expected))
            if g != e]
    problems = []
    if len(got) != len(expected):
        problems.append("%d result(s), lxml gave %d" % (len(got),
                                                        len(expected)))
    if diff:
        problems.append("first differing result %s: got %r, lxml %r" % diff[0])
    suite.record(GD, "fixture parses to lxml's fields", problems,
                 detail=["fixture     : %s" % os.path.relpath(BING_HTML,
                                                              H.REPO_ROOT),
                         "expected    : %s (recorded from the lxml parser)"
                         % os.path.relpath(BING_EXPECTED, H.REPO_ROOT),
                         "results     : %d got, %d expected"
                         % (len(got), len(expected))])

    saved = mod._START_CLOSE
    try:
        mod._START_CLOSE = {}
        mutant = mod.parse_bing_results(html_text)
    finally:
        mod._START_CLOSE = saved
    suite.record(GD, "control: without libxml2's start-close table it differs",
                 [] if mutant != expected
                 else ["the fixture cannot tell the implicit-close rule from "
                       "its absence -- it no longer exercises it"],
                 detail=["mutant      : _START_CLOSE = {} (an unclosed <p> "
                         "swallows the following <div>)"])

    blank = [mod.parse_bing_results(s) for s in ("", "   \n")]
    suite.record(GD, "empty and blank input give no results",
                 [] if blank == [[], []] else ["got %r" % (blank,)],
                 detail=["lxml        : document_fromstring raised 'Document "
                         "is empty' and the old parser returned []"])


def _parse_file(path):
    """(tree, None) or (None, error text)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=path), None
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _lib_hits(sites):
    return [(line, "%s(%r) -> %s" % (form, lit, stem or "(not lib<stem>)"))
            for line, form, lit, stem in sites
            if stem is None or stem not in SYSTEM_LIBS]


def group_system_libs(suite, root):
    """F. ctypes libraries declared; no 3.10+ stdlib API in NEW_39_SCOPE."""
    files = source_files(H.REPO_ROOT, SCAN_ROOTS, EXTRA_FILES)
    sites, own, errors = [], [], []
    for rel in files:
        tree, err = _parse_file(os.path.join(H.REPO_ROOT, rel))
        if tree is None:
            errors.append("%s (%s)" % (rel, err))
            continue
        found, mine = library_stems(tree)
        sites.extend((rel,) + s for s in found)
        own.extend("%s:%d" % (rel, line) for line in mine)

    bad = [s for s in sites if s[4] is None or s[4] not in SYSTEM_LIBS]
    suite.record(GF, "every ctypes library stem is declared in SYSTEM_LIBS",
                 (["%d undeclared library name(s): %s"
                   % (len(bad), ", ".join("%s:%d %r" % (p, ln, lit)
                                          for p, ln, _f, lit, _s in bad))]
                  if bad else [])
                 + (["%d file(s) could not be analysed: %s"
                     % (len(errors), "; ".join(errors))] if errors else []),
                 detail=["declared    : %s" % ", ".join(
                             "%s (%s)" % kv for kv in sorted(SYSTEM_LIBS.items())),
                         "sites       : %d literal library name(s) in %d "
                         "file(s)" % (len(sites), len({s[0] for s in sites})),
                         "read        : the first argument of ctypes.CDLL / "
                         "PyDLL / cdll.LoadLibrary / find_library, and every "
                         "lib-named element of a top-level *SONAMES* / *FILES* "
                         "tuple",
                         "fix         : declare the stem with the reason the "
                         "stdlib cannot do the job, and record it in ADR 0024"])

    used = {s[4] for s in sites}
    stale = sorted(set(SYSTEM_LIBS) - used)
    suite.record(GF, "declared system libraries are still live",
                 ["SYSTEM_LIBS declares %s but no scanned file names it"
                  % ", ".join(stale)] if stale else [],
                 detail=["%-10s %s" % (stem, ", ".join(sorted(
                     {s[0] for s in sites if s[4] == stem})) or "(unused)")
                         for stem in sorted(SYSTEM_LIBS)]
                 + ["rule        : a declaration that licenses nothing is a "
                    "stale licence; delete it"])

    suite.record(GF, "CDLL(None): the process's own namespace (declared)", [],
                 status=H.INFO,
                 detail=["sites       : %s" % (", ".join(own) or "none"),
                         "why         : no library is named, nothing can be "
                         "absent -- not a system-library dependency"])

    for rel in NEW_39_SCOPE:
        cid = "no 3.10+ stdlib API: " + rel
        path = os.path.join(H.REPO_ROOT, rel)
        if not os.path.isfile(path):
            suite.record(GF, cid, [], status=H.INFO,
                         detail=["NOT PRESENT YET: nothing to judge -- INFO, "
                                 "not a vacuous PASS"])
            continue
        tree, err = _parse_file(path)
        if tree is None:
            suite.record(GF, cid, ["could not be analysed: %s" % err])
            continue
        hits = api_310(tree)
        suite.record(GF, cid,
                     ["%d 3.10+ API use(s): %s"
                      % (len(hits), "; ".join("line %d %s" % h for h in hits))]
                     if hits else [],
                     detail=["rule        : a LIST of known 3.10+ APIs (see "
                             "api_310), not a model of the stdlib; the real "
                             "3.9 interpreter is the complete check"])

    for name, rule in sorted(GF_FIXTURES.items()):
        source = FIXTURES[name][0]
        expected = [n for n, line in enumerate(source.splitlines(), 1)
                    if line.endswith("# FLAG")]
        tree, err = _parse_file(os.path.join(root, name))
        if tree is None:
            suite.record(GF, "control-" + name, ["fixture unparsable: %s"
                                                 % err])
            continue
        problems = []
        if rule == "lib":
            fsites, fown = library_stems(tree)
            hits = _lib_hits(fsites)
            if name == "declared_soname.py":
                if len(fsites) < 5:
                    problems.append("only %d site(s) read, 5 planted: the "
                                    "reader is blind" % len(fsites))
                if not fown:
                    problems.append("CDLL(None) not recognised as the "
                                    "process's own namespace")
        else:
            hits = api_310(tree)
        got = [line for line, _what in hits]
        if got != expected:
            problems.insert(0, "flagged lines %r != expected %r"
                            % (got, expected))
        suite.record(GF, "control-" + name, problems,
                     detail=["rule        : %s" % {"lib": "library_stems vs "
                                                   "SYSTEM_LIBS",
                                                   "api": "api_310"}[rule],
                             "expected    : %r (the `# FLAG` lines)"
                             % expected]
                     + ["hit         : line %d %s" % h for h in hits])


# PEP 723's reference regex for an inline metadata block.
_PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s"
                        r"(?P<content>(^#(| .*)$\s)+)^# ///$")
_DEPS_RE = re.compile(r"(?ms)^dependencies\s*=\s*\[(?P<body>.*?)\]")


def pep723_deps(src):
    """The `dependencies` strings of src's PEP 723 `script` block, or None.

    No tomllib on the 3.9 floor, so the one array is read by regex: enough for
    a list of quoted requirement strings, which is all the block holds."""
    for match in _PEP723_RE.finditer(src):
        if match.group("type") != "script":
            continue
        content = "".join(line[2:] if line.startswith("# ") else line[1:]
                          for line in match.group("content")
                          .splitlines(keepends=True))
        deps = _DEPS_RE.search(content)
        return re.findall(r"\"([^\"]*)\"", deps.group("body")) if deps else []
    return None


def dep_name(dep):
    return re.split(r"[\s<>=!~;\[]", dep, 1)[0].lower()


def dep_problems(deps):
    """R-0058: every requirement bounded below (>=) and above (<)."""
    if deps is None:
        return ["no PEP 723 `script` block found"]
    return ["%r has no >= lower AND < upper bound: every cold start resolves "
            "whatever the index serves" % dep for dep in deps
            if not (re.search(r">=\s*\d", dep) and re.search(r"<\s*\d", dep))]


def tree_builder_problems(tree, deps):
    """Every BeautifulSoup(...) call names the literal "lxml" builder, and lxml
    is declared -- bs4 does not pull it in, so an undeclared builder dies with
    FeatureNotFound at the first conversion, not at startup."""
    problems = []
    if "lxml" not in {dep_name(d) for d in deps or ()}:
        problems.append("lxml is not declared in the PEP 723 block")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else None
        if name != "BeautifulSoup":
            continue
        feature = node.args[1] if len(node.args) > 1 else next(
            (k.value for k in node.keywords if k.arg == "features"), None)
        if not (isinstance(feature, ast.Constant) and feature.value == "lxml"):
            problems.append("line %d: BeautifulSoup() without the literal "
                            "tree builder 'lxml'" % node.lineno)
    return problems


def group_webfetch_deps(suite):
    """G. webfetch's PEP 723 block is bounded and builds with a declared lxml (R-0058)."""
    try:
        with open(WEBFETCH, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=WEBFETCH)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        for cid in ("webfetch PEP 723 deps all bounded",
                    "webfetch builds every soup with a declared lxml"):
            suite.record(GG, cid, ["setup failed: %s: %s"
                                   % (type(exc).__name__, exc)])
        return
    deps = pep723_deps(src)
    suite.record(GG, "webfetch PEP 723 deps all bounded", dep_problems(deps),
                 detail=["declared    : %r" % (deps,),
                         "rule        : each requirement carries a >= lower "
                         "and a < upper bound (R-0058, F47)"])
    suite.record(GG, "webfetch builds every soup with a declared lxml",
                 tree_builder_problems(tree, deps),
                 detail=["rule        : the literal 'lxml' at every "
                         "BeautifulSoup() call, and lxml in the PEP 723 "
                         "block; the import gate cannot see a builder asked "
                         "for by string"])

    unbounded = ("# /// script\n# dependencies = [\n#     \"beautifulsoup4\",\n"
                 "#     \"markdownify>=1.2,<2\",\n#     \"lxml\",\n"
                 "# ]\n# ///\n")
    got = dep_problems(pep723_deps(unbounded))
    suite.record(GG, "control: unbounded dependencies are flagged",
                 [] if len(got) == 2 else ["%d problem(s), 2 planted: %r"
                                           % (len(got), got)],
                 detail=["planted     : beautifulsoup4, lxml (no bound); "
                         "markdownify>=1.2,<2 (clean)"]
                 + ["hit         : %s" % g for g in got])
    bounded = unbounded.replace("\"beautifulsoup4\"",
                                "\"beautifulsoup4>=4.15,<5\"").replace(
        "\"lxml\"", "\"lxml>=6.1,<7\"")
    got = dep_problems(pep723_deps(bounded))
    suite.record(GG, "control: a fully bounded block is clean",
                 ["flagged %r" % got] if got else [],
                 detail=["planted     : beautifulsoup4>=4.15,<5, "
                         "markdownify>=1.2,<2, lxml>=6.1,<7"])
    planted = ast.parse("BeautifulSoup(h, 'lxml')\nBeautifulSoup(h)\n"
                        "bs4.BeautifulSoup(h, features='html.parser')\n")
    got = tree_builder_problems(planted, ["beautifulsoup4>=4.15,<5"])
    suite.record(GG, "control: a missing builder, a foreign builder and an "
                     "undeclared lxml are flagged",
                 [] if len(got) == 3 else ["%d problem(s), 3 expected: %r"
                                           % (len(got), got)],
                 detail=["planted     : 'lxml' (clean), no builder, "
                         "features='html.parser', lxml not declared"]
                 + ["hit         : %s" % g for g in got])


def group_hygiene(suite, root, pyc_before, tree_before):
    """E. every write under .claude/tmp, no bytecode, no new repo paths."""
    stray = [p for p in WRITES if not os.path.abspath(p).startswith(
        os.path.abspath(FIXTURE_BASE) + os.sep)]
    suite.record(GE, "every write lands under .claude/tmp",
                 ["%d write(s) outside the sandbox: %s" % (len(stray), stray)]
                 if stray else [],
                 detail=["writes      : %d" % len(WRITES)])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GE, "no .pyc written anywhere in the repo tree",
                 ["new=%r touched=%r" % (new, touched)]
                 if (new or touched) else [],
                 detail=["pyc before=%d after=%d" % (len(pyc_before),
                                                     len(pyc_after)),
                         "note        : a DELTA, not the absolute form"])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GE, "no new repo paths",
                 ["%d new path(s): %s" % (len(added), added[:5])]
                 if added else [],
                 detail=["note        : the scratch area is excluded"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="pure Python 3.9 + stdlib: allowlisted, find_spec-"
                          "guarded third-party imports and 3.9 syntax",
                    opts=opts, mode="grouped")
    pyc_before = H.pycache_snapshot()
    tree_before = H.repo_tree()
    del WRITES[:]

    os.makedirs(FIXTURE_BASE, exist_ok=True)
    root = tempfile.mkdtemp(prefix="run-", dir=FIXTURE_BASE)
    try:
        group_gate(suite)
        group_syntax(suite)
        group_control(suite, root)
        group_bing(suite)
        group_system_libs(suite, root)
        group_webfetch_deps(suite)
        group_hygiene(suite, root, pyc_before, tree_before)
    finally:
        if opts.keep:
            print("\n[--keep] fixtures retained at: %s" % root)
        else:
            shutil.rmtree(root, ignore_errors=True)

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
