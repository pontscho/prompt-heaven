#!/usr/bin/env python3
"""INDEX.md rendering, the wiki page-type constants, and the two wiki CLIs (A-H).

WHAT IS STILL TWO COPIES, AND WHAT IS NOT
-----------------------------------------
The page-type vocabulary and the frontmatter/git helpers live twice: once in
`ClaudeCode/skills/wiki/scripts/_wikilib.py` (which addendum.py imports) and
once in `Scripts/mcp-wiki.py`.  That duplication is deliberate -- a fleet
server never imports a sibling, and the amalgamate generator cannot reach a
skill script -- so the only thing that keeps the two honest is a gate that
loads both and compares them.  This suite is that gate.

The INDEX renderer, the orphan rule and the freshness classifier are NOT two
copies any more (roadmap R-0033).  `freshness.py` and `reindex.py` are thin
wrappers that load the committed server module off disk -- through
`measure_cli.py`'s resolver and loader -- and call its `handle_wiki_call`, so
the render/collect/classify cases below judge the server alone, and the two
CLIs are judged by CONTRACT: `freshness.py`'s exit code is the server's own
`gating:` number, `reindex.py` fails exactly when the server's answer carries a
block its exported REINDEX_BLOCKING_PREFIXES name, both print the server's
answer, and both exit 2 when no server module can be found.

WHAT A ROADMAP ITEM DOES TO THE INDEX
-------------------------------------
INDEX.md is @-included by CLAUDE.md and loaded every session.  A closed roadmap
item is archived as its own page, and there will be many, so listing them one
line each would grow every session's context without bound.  The contract
gated here: a `roadmap-item` page is never LISTED, it is COUNTED, in exactly
one rendered line under `## roadmap`; it is exempt from the orphan rule
(archive pages are unlinked by design) while an unlinked `roadmap` page is not;
and both `roadmap` and `roadmap-item` are freshness-untracked, which holds only
while a page carries no `sources:` -- the control that documents why the
roadmap writer never writes that key.

GROUPS
------
  A  the six constants are equal between the copies, homed once on the skill
     side, and carry the roadmap types
  B  the server's render_index: archive pages counted and never listed; the
     reindex.py wrapper: writes the server's INDEX, fails on a duplicate slug
     through the server's answer, keeps `render_index` as a delegate, and
     exits 2 with no server to load
  C  the server's reindex collect: the orphan exemption
  D  the server's freshness classification of the two roadmap types, and the
     freshness.py wrapper: its exit code and `gating:` line are the server's
     (a dead body anchor gates), its output is the server's answer, git lag is
     advisory for every editorial `status:`, and no server means exit 2
  E  negative controls -- each proves an oracle above can FIRE
  F  hygiene: the live docs/INDEX.md, the repo tree, bytecode, the sandbox
  G  the git helper, both copies: the server's timeout and the roadmap's
     hardening (GIT_SAFE_ARGV prefix, GIT_NO_LAZY_FETCH=1)
  H  everything else the server vendors from _wikilib: each function compared
     as CODE (AST, docstring / annotations / name / `w.` qualifier taken out),
     each constant by value, and a census that names any shared name no case
     compares -- which also stops a CLI wrapper from growing a copy back

WHY PARITY GATES AND NOT GENERATION (roadmap R-0002)
----------------------------------------------------
Rendering the server's copies from `_wikilib.py` as generated regions was
measured and refused by the generator's own gated contract, not by taste:
`anchored-on-file` pins every canonical source to `Scripts/<name>`, and
`tab-safety-real-blocks` pins the tab-unsafe set to `_rows_note` -- every
block of the tab-indented skill module would join it, and the generator only
converts spaces to tabs, never back.  An import is out for the fleet-wide
reason (a server never imports a sibling; no server does today).  So the
copies stay, and every one of them is compared here.  The direction R-0033
took is the other one -- a skill CLI importing the SERVER -- which neither rule
forbids, and it is why the CLI copies could go while _wikilib's stay.

THE GIT HELPER IS A THIRD SHARED THING
--------------------------------------
`git()` is vendored the same way the constants are, and drifted the same way:
the server copy got a timeout the skill copy never did, and neither got the
`-c` prefix and child env that roadmap.py's `git()` carries (adr 0022, entry
12).  Group G drives each copy with its module's `subprocess` swapped for a
recorder -- one module attribute on one loaded copy, never the real
`subprocess` module -- and asserts the argv and the keyword arguments of the
one spawn.  The wanted prefix is read from roadmap.py's source with
`ast.literal_eval`, never typed here and never executed, so the three copies
cannot agree on a value this suite made up.  One live case then runs both
copies against the real git in this repo, because a `-c` pair a real git
rejects would pass every recorded assertion.

Every expected count is derived from the fixture list below, never typed.
The case count lives only in the SUITES table of tests/run.py.
"""

import ast
import collections
import contextlib
import difflib
import io
import os
import posixpath
import re
import shutil
import subprocess
import sys
import types

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "wiki_index"
SERVER = H.repo_path("Scripts", "mcp-wiki.py")
SCRIPTS_DIR = H.repo_path("ClaudeCode", "skills", "wiki", "scripts")
REINDEX = os.path.join(SCRIPTS_DIR, "reindex.py")
FRESHNESS = os.path.join(SCRIPTS_DIR, "freshness.py")
WIKILIB = os.path.join(SCRIPTS_DIR, "_wikilib.py")
LIVE_INDEX = H.repo_path("docs", "INDEX.md")
ROADMAP_PY = H.repo_path("ClaudeCode", "skills", "roadmap", "scripts",
                         "roadmap.py")

GA = "A. constant parity: one vocabulary, two copies"
GB = "B. render_index (server) and the reindex.py wrapper"
GC = "C. reindex collect (server)"
GD = "D. freshness: roadmap types untracked, the CLI exit code is the server's"
GE = "E. negative control"
GF = "F. hygiene"
GG = "G. the git helper, both copies: timeout and hardening"
GH = "H. vendored code: every other shared function and constant, both copies"

# The wrappers find the server the way measure_cli.py does; this is the one
# override the no-server cases have to take out of a child's environment, and
# the files a relocated copy of the wrappers needs beside it to import at all.
SERVER_ENV_VAR = "MCP_WIKI_SERVER"
NOT_FOUND_TOKEN = "cannot find the mcp-wiki server module"
CLI_COPY_FILES = ("freshness.py", "reindex.py", "measure_cli.py", "_wikilib.py")

# The argv every group-G call passes through `git()`: the shape of
# `repo_root()`, the one call both copies make on every run.
GIT_PROBE_ARGS = ["rev-parse", "--show-toplevel"]
LAZY_FETCH_ENV = "GIT_NO_LAZY_FETCH"

# The six names the two copies must agree on.  Their VALUES are never typed
# here: parity compares the copies, and the roadmap cases below name only the
# two type strings the fixture itself is written in.
CONSTANTS = ("TYPE_ORDER", "INDEX_LABELLED", "STATUS_FORBIDDEN",
             "UNTRACKED_TYPES", "ORPHAN_EXEMPT_TYPES", "INDEX_COUNTED_TYPES")

ROADMAP_TYPE = "roadmap"
ITEM_TYPE = "roadmap-item"

# Group H: every OTHER thing the server vendors from _wikilib.  The six
# CONSTANTS above and the git pair (group G) are gated where they were first
# gated; this is the rest, so that no duplicated line is left ungated.
#
# (skill script, name there, name in the server) -- a function compared as
# CODE, not as output: its AST, with only the differences declared in
# `normalized_function` taken out.  The eight freshness.py / reindex.py rows
# left with R-0033: those scripts no longer carry the code, they call it.
SKILL_ALIAS = "w"            # `import _wikilib as w` in a skill script
VENDORED_FUNCTIONS = (
    ("_wikilib.py", "git", "git"),
    ("_wikilib.py", "repo_root", "repo_root"),
    ("_wikilib.py", "split_frontmatter", "split_frontmatter"),
    ("_wikilib.py", "_unquote", "_unquote"),
    ("_wikilib.py", "_parse_scalar", "_parse_scalar"),
    ("_wikilib.py", "_collect_block", "_collect_block"),
    ("_wikilib.py", "parse_frontmatter", "parse_frontmatter"),
    ("_wikilib.py", "read_page", "read_page"),
    ("_wikilib.py", "iter_pages", "iter_pages"),
    ("_wikilib.py", "extract_wikilinks", "extract_wikilinks"),
    ("_wikilib.py", "as_list", "as_list"),
)
# (skill script, constant) -- compared by VALUE, same name on both sides.
VENDORED_CONSTANTS = (
    ("_wikilib.py", "SKIP_FILES"),
    ("_wikilib.py", "SKIP_DIRS"),
    ("_wikilib.py", "_WIKILINK_RE"),
)
# Same name in both, different on purpose -- the only names the census case
# lets through undeclared.  `main` is two different programs' argv parsing.
# `render_index` in reindex.py is NOT a copy: it is a one-line delegate to the
# loaded server's own, kept by name only because docs/adr/0022 (a frozen page)
# anchors `reindex.py:render_index` in its frontmatter; group B's
# `render-index-delegates` gates that it delegates rather than renders.
DECLARED_DIFFERENT = ("main", "render_index")
# Already gated elsewhere in this suite, so the census counts them as declared.
GATED_ELSEWHERE = ("GIT_TIMEOUT_SEC", "GIT_SAFE_ARGV")


def _d(label, value):
    return "%-12s: %s" % (label, value)


# ---------------------------------------------------------------------------
# The fixture: one list of pages; every variant is a filter of it
# ---------------------------------------------------------------------------

Page = collections.namedtuple("Page", "path name type title links extra body")


def _page(path, name, typ, title, links=(), extra="", body=""):
    return Page(path, name, typ, title, tuple(links), extra, body)


OVERVIEW = _page("overview.md", "overview", "overview", "Fixture Overview")
SPEC = _page("specs/spec-roadmap.md", "spec-roadmap", "spec",
             "Roadmap Spec", links=["0001-keep-a-roadmap"])
LONELY = _page("concepts/lonely.md", "lonely-concept", "concept",
               "Lonely Concept")
ODD = _page("misc/odd.md", "odd-page", "gizmo", "Odd Page")
ROADMAP = _page("roadmap/roadmap.md", "roadmap", ROADMAP_TYPE, "Roadmap")
ARCHIVE = (
    _page("roadmap/archive/0001-first-item.md", "r-0001-first-item",
          ITEM_TYPE, "First Item"),
    _page("roadmap/archive/0002-second-item.md", "r-0002-second-item",
          ITEM_TYPE, "Second Item"),
    _page("roadmap/archive/0003-third-item.md", "r-0003-third-item",
          ITEM_TYPE, "Third Item"),
)
# A roadmap item that WRONGLY carries `sources:`.  It lives in a corpus of its
# own so no other variant's derived counts see it.
SOURCED_ITEM = _page("roadmap/archive/0009-sourced-item.md",
                     "r-0009-sourced-item", ITEM_TYPE, "Sourced Item",
                     extra="sources:\n  - Scripts/never-written.py\n")

# The freshness.py exit code is the SERVER's verdict (R-0033): whatever its
# `gating:` line counts -- a dead `sources:` path, a dead body anchor, a
# measured-region defect.  Git lag -- `stale` and `unverified` -- is listed and
# advisory, for every editorial `status:`.  A concept page carrying `sources:`
# and NO `verified:`, once per status: all four are listed and none gates, so
# no status needs an exemption.  Its one source is the page itself, so the
# anchor resolves and only the git lag is left to report.  Each lives in a
# corpus of its own, because the exit code is corpus-wide.
UNVERIFIED_STATUSES = ("draft", "active", "deprecated", None)
# `stale` and `orphaned-source` need a `verified.commit` git can diff against,
# so those two pages live in real fixture repositories (see `git_fixture`),
# never in this repository and never through a stubbed classifier.
STALE_SOURCE = "src/moved.py"
GONE_SOURCE = "src/never-written.py"


def _verified_page(slug, source, commit):
    return _page("concepts/%s.md" % slug, slug, "concept",
                 slug.replace("-", " ").title(),
                 extra="sources:\n  - %s\nverified:\n  commit: %s\n"
                       "  date: 2026-09-28\n" % (source, commit))


def _unverified_page(status):
    label = status or "nostatus"
    path = "concepts/unverified-%s.md" % label
    extra = ("status: %s\n" % status if status else "") + \
        "sources:\n  - %s\n" % path
    return _page(path, "unverified-%s" % label,
                 "concept", "Unverified %s" % label.title(), extra=extra)


# An ACTIVE, non-ADR page whose one body anchor names a file that does not
# exist -- the defect the server's `gating:` line counts and the pre-R-0033
# CLI never read, which is how five gating pages hid behind a CLI exit of 0
# (R-0036).  It carries no `sources:`, so git lag cannot be what gates it.
DEAD_BODY_ANCHOR = "concepts/never-written.py"
DEAD_BODY = _page("concepts/dead-body.md", "dead-body", "concept", "Dead Body",
                  extra="status: active\n",
                  body="The code lives in `%s`." % DEAD_BODY_ANCHOR)
# Two pages, one slug: the reindex audit's duplicate-slug failure.
DUP_A = _page("concepts/dup-a.md", "dup-slug", "concept", "Dup A")
DUP_B = _page("concepts/dup-b.md", "dup-slug", "concept", "Dup B")


Variant = collections.namedtuple("Variant",
                                 "label items with_roadmap link_roadmap only")

V_FULL = Variant("full", len(ARCHIVE), True, True, None)
V_SINGLE = Variant("single", 1, True, True, None)
V_NONE = Variant("none", 0, True, True, None)
V_ARCHIVE_ONLY = Variant("archive-only", len(ARCHIVE), False, False, None)
V_UNLINKED = Variant("unlinked-roadmap", len(ARCHIVE), True, False, None)
V_CLI = Variant("cli", len(ARCHIVE), True, True, None)
V_SOURCED = Variant("sourced-item", 0, False, False, (SOURCED_ITEM,))

V_UNVERIFIED = {s: Variant("unverified-%s" % (s or "nostatus"), 0, False, False,
                           (_unverified_page(s),))
                for s in UNVERIFIED_STATUSES}
V_DEAD_BODY = Variant("dead-body", 0, False, False, (DEAD_BODY,))
V_DUP = Variant("dup-slug", 0, False, False, (DUP_A, DUP_B))

RENDER_VARIANTS = (V_FULL, V_SINGLE, V_NONE, V_ARCHIVE_ONLY, V_UNLINKED)
ALL_VARIANTS = RENDER_VARIANTS + (V_CLI, V_SOURCED, V_DEAD_BODY, V_DUP) + \
    tuple(V_UNVERIFIED[s] for s in UNVERIFIED_STATUSES)


def variant_pages(variant):
    if variant.only is not None:
        return list(variant.only)
    adr_links = [SPEC.name, ODD.name]
    if variant.with_roadmap and variant.link_roadmap:
        adr_links.append(ROADMAP.name)
    adr = _page("adr/0001-keep-a-roadmap.md", "0001-keep-a-roadmap", "adr",
                "Keep a Roadmap", links=adr_links)
    pages = [OVERVIEW, adr, SPEC, LONELY, ODD]
    if variant.with_roadmap:
        pages.append(ROADMAP)
    pages.extend(ARCHIVE[:variant.items])
    return pages


def page_text(page):
    body_links = " ".join("[[%s]]" % link for link in page.links)
    return ("---\nname: %s\ntype: %s\ntitle: %s\ndescription: %s in the "
            "wiki_index fixture.\n%s---\n\n# %s\n\nFixture body. %s\n%s"
            % (page.name, page.type, page.title, page.title, page.extra,
               page.title, body_links,
               (page.body + "\n") if page.body else ""))


def build_variant(work, variant):
    root = work.subdir(variant.label, "docs")
    for page in variant_pages(variant):
        work.write_text(os.path.join(variant.label, "docs", page.path),
                        page_text(page))
    return root


def archive_pages(pages):
    return [p for p in pages if p.type == ITEM_TYPE]


def expected_count_line(pages):
    """The one line the fixture's archive pages must collapse into, or None."""
    items = archive_pages(pages)
    if not items:
        return None
    dirs = sorted({posixpath.dirname(p.path) + "/" for p in items})
    return ("- Roadmap archive: %d closed item%s -> %s"
            % (len(items), "" if len(items) == 1 else "s", ", ".join(dirs)))


def linked_names(pages):
    out = set()
    for page in pages:
        out.update(page.links)
    return out


# ---------------------------------------------------------------------------
# Oracles -- each returns a list of problems, so group E can point them at a
# planted defect and demand that they FIRE
# ---------------------------------------------------------------------------

def parity_problems(srv, lib, name):
    problems = []
    if not hasattr(srv, name):
        return ["the server has no %s" % name]
    if not hasattr(lib, name):
        return ["_wikilib has no %s" % name]
    a, b = getattr(srv, name), getattr(lib, name)
    if type(a) is not type(b):
        problems.append("%s: server is a %s, _wikilib a %s"
                        % (name, type(a).__name__, type(b).__name__))
    if a != b:
        problems.append("%s differs: server %r, _wikilib %r" % (name, a, b))
    return problems


def membership_problems(copies, name, wanted):
    problems = []
    for label, ns in copies:
        value = getattr(ns, name, ())
        for typ in wanted:
            if typ not in value:
                problems.append("%s.%s lacks %r: %r" % (label, name, typ, value))
    return problems


def check_constants(srv, lib):
    """The whole of group A's value half, as one oracle for control 2."""
    problems = []
    for name in CONSTANTS:
        problems += parity_problems(srv, lib, name)
    copies = (("server", srv), ("_wikilib", lib))
    for name in ("TYPE_ORDER", "UNTRACKED_TYPES"):
        problems += membership_problems(copies, name, (ROADMAP_TYPE, ITEM_TYPE))
    for name in ("ORPHAN_EXEMPT_TYPES", "INDEX_COUNTED_TYPES"):
        problems += membership_problems(copies, name, (ITEM_TYPE,))
    return problems


def sections(text):
    """heading -> list of its non-blank lines, in order; plus the heading list."""
    out = collections.OrderedDict()
    headings = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:]
            headings.append(current)
            out.setdefault(current, [])
            continue
        if current is not None and line.strip():
            out[current].append(line)
    return out, headings


def listed_archive_problems(text, pages, label):
    problems = []
    for page in archive_pages(pages):
        hits = [line for line in text.splitlines() if page.path in line]
        if hits:
            problems.append("%s lists archive page %s: %r"
                            % (label, page.path, hits[0]))
    return problems


def count_line_problems(text, pages, label):
    want = expected_count_line(pages)
    lines = text.splitlines()
    if want is None:
        stray = [line for line in lines if line.startswith("- Roadmap archive:")]
        return ["%s renders an archive line with no archive page: %r"
                % (label, stray[0])] if stray else []
    n = sum(1 for line in lines if line == want)
    if n != 1:
        return ["%s: %d line(s) equal %r, want exactly 1" % (label, n, want)]
    return []


def roadmap_section_problems(text, pages, label):
    problems = []
    found, headings = sections(text)
    if ITEM_TYPE in headings:
        problems.append("%s renders a `## %s` heading" % (label, ITEM_TYPE))
    n = headings.count(ROADMAP_TYPE)
    has_roadmap = any(p.type == ROADMAP_TYPE for p in pages)
    want_heading = has_roadmap or bool(archive_pages(pages))
    if want_heading and n != 1:
        problems.append("%s: `## %s` rendered %d time(s), want 1"
                        % (label, ROADMAP_TYPE, n))
    for page in pages:
        if page.type != ROADMAP_TYPE:
            continue
        prefix = "- [%s](%s)" % (page.title, page.path)
        if not any(line.startswith(prefix) for line in found.get(ROADMAP_TYPE, [])):
            problems.append("%s: %s is not listed under `## %s`"
                            % (label, page.path, ROADMAP_TYPE))
    return problems


def check_render(text, pages, label):
    """The whole of group B's content half, as one oracle for control 1."""
    return (listed_archive_problems(text, pages, label)
            + count_line_problems(text, pages, label)
            + roadmap_section_problems(text, pages, label))


def orphan_names(result):
    return sorted(e["name"] for e in result[2])


def check_collect(result, pages, label):
    """The whole of group C's orphan half, as one oracle for control 3."""
    _entries, dups, orphans, malformed = result
    problems = []
    for entry in orphans:
        if entry["type"] == ITEM_TYPE:
            problems.append("%s: %s page %s is an orphan"
                            % (label, ITEM_TYPE, entry["path"]))
        if entry["type"] == "overview":
            problems.append("%s: the overview %s is an orphan"
                            % (label, entry["path"]))
    names = set(orphan_names(result))
    linked = linked_names(pages)
    for page in pages:
        if page.type in (ITEM_TYPE, "overview"):
            continue
        if page.name not in linked and page.name not in names:
            problems.append("%s: unlinked %s page %s is NOT an orphan"
                            % (label, page.type, page.path))
    if malformed:
        problems.append("%s: malformed %r" % (label, malformed))
    if dups:
        problems.append("%s: duplicate slugs %r" % (label, dups))
    return problems


def _module_level_assignments(tree):
    """Names bound at module level, descending into if/try/with, never into a
    function or class body."""
    names = []
    stack = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.append((sub.id, node.lineno))
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(node, field, None) or [])
    return names


def single_home_problems(label, source):
    tree = ast.parse(source)
    return ["%s:%d assigns %s at module level" % (label, line, name)
            for name, line in _module_level_assignments(tree)
            if name in CONSTANTS]


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _tree_digests(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            out[os.path.relpath(path, root)] = H.sha256_file(path)
    return out


def _inside(path, root):
    real, base = os.path.realpath(path), os.path.realpath(root)
    return real == base or real.startswith(base + os.sep)


def roadmap_safe_argv():
    """roadmap.py's GIT_SAFE_ARGV, read from its source -- never executed."""
    tree = ast.parse(_read(ROADMAP_PY))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "GIT_SAFE_ARGV"
                for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    return None


class _SpawnRecorder:
    """Stands in for the `subprocess` module inside ONE loaded git() copy.

    Only the names git() reads are provided; each is the real object, so an
    `except subprocess.TimeoutExpired` in the copy catches what run() raises.
    """
    DEVNULL = subprocess.DEVNULL
    PIPE = subprocess.PIPE
    TimeoutExpired = subprocess.TimeoutExpired
    CompletedProcess = subprocess.CompletedProcess

    def __init__(self, raise_timeout=False):
        self.calls = []
        self.raise_timeout = raise_timeout

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, 0, "", "")


def record_git(mod, raise_timeout=False):
    """(result_or_exception, calls) of one `mod.git(GIT_PROBE_ARGS)` call with
    the copy's own `subprocess` attribute swapped for a recorder."""
    fake = _SpawnRecorder(raise_timeout)
    saved = mod.subprocess
    mod.subprocess = fake
    try:
        result = mod.git(list(GIT_PROBE_ARGS), cwd=H.REPO_ROOT)
    except Exception as exc:                  # a copy that lets it escape
        result = exc
    finally:
        mod.subprocess = saved
    return result, fake.calls


def git_call_problems(label, calls, want_argv, want_timeout):
    """Everything group G demands of ONE recorded git() spawn."""
    if len(calls) != 1:
        return ["%s: git() spawned %d time(s), want 1" % (label, len(calls))]
    argv, kwargs = calls[0]
    problems = []
    if want_argv is None:
        problems.append("%s: roadmap.py has no GIT_SAFE_ARGV to compare "
                        "against" % label)
    elif tuple(argv[:len(want_argv)]) != tuple(want_argv):
        problems.append("%s: argv %r does not start with roadmap.py's "
                        "GIT_SAFE_ARGV %r" % (label, argv, want_argv))
    elif argv[len(want_argv):] != GIT_PROBE_ARGS:
        problems.append("%s: args after the prefix are %r, want %r"
                        % (label, argv[len(want_argv):], GIT_PROBE_ARGS))
    if "timeout" not in kwargs:
        problems.append("%s: no timeout= -- subprocess.run waits forever"
                        % label)
    elif kwargs["timeout"] != want_timeout:
        problems.append("%s: timeout=%r, want the server's GIT_TIMEOUT_SEC %r"
                        % (label, kwargs["timeout"], want_timeout))
    env = kwargs.get("env")
    if env is None:
        problems.append("%s: no env= -- %s=1 is never set"
                        % (label, LAZY_FETCH_ENV))
    else:
        if env.get(LAZY_FETCH_ENV) != "1":
            problems.append("%s: env %s=%r, want '1'"
                            % (label, LAZY_FETCH_ENV, env.get(LAZY_FETCH_ENV)))
        rest = {k: v for k, v in env.items() if k != LAZY_FETCH_ENV}
        base = {k: v for k, v in os.environ.items() if k != LAZY_FETCH_ENV}
        if rest != base:
            problems.append("%s: env is not os.environ plus %s (differs in %r)"
                            % (label, LAZY_FETCH_ENV,
                               sorted(set(rest) ^ set(base))[:5]
                               or sorted(k for k in rest
                                         if rest[k] != base[k])[:5]))
    if kwargs.get("stdin") is not subprocess.DEVNULL:
        problems.append("%s: stdin=%r, want DEVNULL" % (label, kwargs.get("stdin")))
    return problems


class _Normalize(ast.NodeTransformer):
    """Take out the differences between the copies that are DECLARED, and
    nothing else: annotations (erased at run time; the server annotates, the
    skill scripts mostly do not) and, on the skill side only, the `w.`
    qualifier the scripts reach _wikilib through."""

    def __init__(self, alias=None):
        self.alias = alias

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if (self.alias and isinstance(node.value, ast.Name)
                and node.value.id == self.alias):
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node.value is None or not node.simple:
            return node
        return ast.copy_location(ast.Assign(targets=[node.target],
                                            value=node.value), node)


def top_level_function(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def normalized_function(node, alias=None):
    """The function as comparable code: docstring, annotations, its own name
    and (skill side) the `w.` qualifier removed.  A docstring is where the two
    copies are ALLOWED to differ -- the server's git() says why stdin=DEVNULL
    guards its JSON-RPC stream, which the CLI's has no reason to say."""
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:] or [ast.Pass()]
    node = ast.FunctionDef(name="_", args=node.args, body=body,
                           decorator_list=node.decorator_list, returns=None,
                           type_comment=None)
    node = _Normalize(alias).visit(node)
    return ast.fix_missing_locations(node)


def code_parity_problems(skill_src, skill_name, server_src, server_name,
                         label):
    """One vendored function: the skill copy and the server copy must be the
    same code once the declared differences are taken out."""
    ours = top_level_function(skill_src, skill_name)
    theirs = top_level_function(server_src, server_name)
    if ours is None:
        return ["%s defines no top-level %s" % (label, skill_name)]
    if theirs is None:
        return ["the server defines no top-level %s" % server_name]
    a = normalized_function(ours, SKILL_ALIAS)
    b = normalized_function(theirs)
    if ast.dump(a) == ast.dump(b):
        return []
    diff = [ln for ln in difflib.unified_diff(
        ast.unparse(a).splitlines(), ast.unparse(b).splitlines(),
        "%s:%s" % (label, skill_name), "mcp-wiki.py:%s" % server_name,
        n=0, lineterm="") if not ln.startswith("@@")]
    return ["%s:%s and mcp-wiki.py:%s are different code: %s"
            % (label, skill_name, server_name, " | ".join(diff[2:8]))]


def constant_parity_problems(skill_mod, server_mod, name, label):
    if not hasattr(server_mod, name):
        return ["the server has no %s" % name]
    if not hasattr(skill_mod, name):
        return ["%s has no %s" % (label, name)]
    a, b = getattr(skill_mod, name), getattr(server_mod, name)
    if type(a) is not type(b):
        return ["%s: %s is a %s, the server's a %s"
                % (name, label, type(a).__name__, type(b).__name__)]
    if isinstance(a, re.Pattern):
        a, b = (a.pattern, a.flags), (b.pattern, b.flags)
    if a != b:
        return ["%s differs: %s %r, server %r" % (name, label, a, b)]
    return []


def top_level_names(source):
    """Names a module DEFINES at top level: def, class, and single-name
    assignment.  Imports are not definitions."""
    out = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            out.update(t.id for t in targets if isinstance(t, ast.Name))
    return out


def undeclared_shared_names(skill_sources, server_src):
    """Every name a skill script and the server both define that no case in
    this suite compares -- a new vendored copy lands here, by name."""
    declared = set(CONSTANTS) | set(GATED_ELSEWHERE) | set(DECLARED_DIFFERENT)
    declared |= {name for _f, name in VENDORED_CONSTANTS}
    declared |= {name for _f, name, _s in VENDORED_FUNCTIONS}
    server = top_level_names(server_src)
    out = []
    for label, source in sorted(skill_sources.items()):
        out += ["%s: %s" % (label, name) for name in
                sorted((top_level_names(source) & server) - declared)]
    return out


# ---------------------------------------------------------------------------
# The CLI wrappers: how they are driven, and what the server says to compare
# ---------------------------------------------------------------------------

def server_freshness(srv, root):
    """The server's own `freshness` answer for one corpus, uncut.  The root is
    realpath'd because the server's containment check compares a realpath
    against the project root it is handed (the sandbox sits under a symlinked
    temp directory on macOS)."""
    result = srv.handle_wiki_call(
        {"function": "freshness", "params": {"max_answer_chars": 0}},
        os.path.realpath(root), ".", False)
    if result.get("error"):
        return "error: %s" % result["error"]
    return result.get("__raw_text__") or ""


def gating_number(srv, text):
    """The count on the one line rendered with the server's exported
    GATING_LINE_PREFIX, or None.  The prefix is READ from the server, never
    typed here, so this oracle and the wrapper agree on one string."""
    prefix = getattr(srv, "GATING_LINE_PREFIX", None)
    if not prefix:
        return None
    lines = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    if len(lines) != 1:
        return None
    head = lines[0][len(prefix):].split(" ", 1)[0]
    return int(head) if head.isdigit() else None


def gating_line(srv, text):
    prefix = getattr(srv, "GATING_LINE_PREFIX", None) or "gating: "
    lines = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    return lines[0] if lines else "(none)"


def run_freshness_cli(fresh_mod, root, *extra):
    """freshness.py's main() in-process on one corpus: (exit, stdout, stderr).
    An argparse refusal is an exit code here, not a crashed suite."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fresh_mod.main(["--root", root] + list(extra))
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def run_cli(script, args, cwd, without_env=()):
    """One CLI as a child: (exit, stdout, stderr), with `without_env` taken
    out of the child's environment."""
    env = H.child_env()
    for key in without_env:
        env.pop(key, None)
    try:
        proc = subprocess.run([sys.executable, "-B", script] + list(args),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=120, cwd=cwd, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def relocated_cli(work):
    """The wrappers copied into the sandbox, where no ancestor of their real
    path holds a Scripts/mcp-wiki.py: the one place the discovery can miss."""
    target = work.subdir("cli-copy")
    for name in CLI_COPY_FILES:
        source = os.path.join(SCRIPTS_DIR, name)
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(target, name))
    return target


# Identity, signing and branch pinned on every fixture commit, and the user's
# global/system git configuration kept out, so no developer setting decides a
# case.  The fixture repositories live in the sandbox, never in this repo.
FIXTURE_GIT = ("-c", "user.name=t", "-c", "user.email=t@t",
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main")


def fixture_git(repo, *args):
    env = H.child_env({"GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CEILING_DIRECTORIES": os.path.dirname(repo)})
    try:
        proc = subprocess.run(["git"] + list(FIXTURE_GIT) + list(args),
                              cwd=repo, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30,
                              env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def git_fixture(work, label, slug, source):
    """A real repository: commit 1 holds STALE_SOURCE; the one page is written
    verified AT commit 1 and naming `source`; commit 2 moves STALE_SOURCE.
    Returns (docs root or None, the page, problems)."""
    repo = work.subdir(label)
    work.write_text(os.path.join(label, STALE_SOURCE), "VALUE = 1\n")
    problems = []

    def run(*args):
        rc, out, err = fixture_git(repo, *args)
        if rc != 0:
            problems.append("fixture git %s exited %d: %s"
                            % (" ".join(args), rc, err.strip()[:200]))
        return out

    run("init", "-q", ".")
    run("add", "-A")
    run("commit", "-q", "-m", "c1")
    commit = run("rev-parse", "HEAD").strip()
    page = _verified_page(slug, source, commit or "0000000")
    work.write_text(os.path.join(label, "docs", page.path), page_text(page))
    work.write_text(os.path.join(label, STALE_SOURCE), "VALUE = 2\n")
    run("add", "-A")
    run("commit", "-q", "-m", "c2")
    return (None if problems else os.path.join(repo, "docs")), page, problems


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def group_a(suite, srv, lib):
    for name in CONSTANTS:
        problems = parity_problems(srv, lib, name)
        suite.record(GA, "parity-" + name, problems,
                     detail=[_d("server", repr(getattr(srv, name, None))),
                             _d("_wikilib", repr(getattr(lib, name, None))),
                             _d("rule", "same type and equal value; a list "
                                        "compares in order")])

    problems = []
    for label, path in (("reindex.py", REINDEX), ("freshness.py", FRESHNESS)):
        problems += single_home_problems(label, _read(path))
    suite.record(GA, "single-home-in-wikilib", problems,
                 detail=[_d("scanned", "reindex.py, freshness.py (ast)"),
                         _d("why", "a local copy in a script is a third place "
                                   "to edit, and nothing would gate it")])

    copies = (("server", srv), ("_wikilib", lib))
    problems = []
    for name in ("TYPE_ORDER", "UNTRACKED_TYPES"):
        problems += membership_problems(copies, name, (ROADMAP_TYPE, ITEM_TYPE))
    suite.record(GA, "roadmap-types-known-and-untracked", problems,
                 detail=[_d("wanted", "%r and %r in TYPE_ORDER and "
                                      "UNTRACKED_TYPES of both copies"
                            % (ROADMAP_TYPE, ITEM_TYPE))])

    problems = []
    for name in ("ORPHAN_EXEMPT_TYPES", "INDEX_COUNTED_TYPES"):
        problems += membership_problems(copies, name, (ITEM_TYPE,))
    suite.record(GA, "roadmap-item-exempt-and-counted", problems,
                 detail=[_d("wanted", "%r in ORPHAN_EXEMPT_TYPES and "
                                      "INDEX_COUNTED_TYPES of both copies"
                            % ITEM_TYPE)])


def _render(srv, root):
    return srv.render_index(srv.reindex_collect(root)[0])


def group_b(suite, srv, reindex_mod, roots, work):
    rendered = {variant.label: _render(srv, roots[variant.label])
                for variant in RENDER_VARIANTS}

    def server_only(variant, oracle):
        return oracle(rendered[variant.label], variant_pages(variant), "server")

    full_pages = variant_pages(V_FULL)
    suite.record(GB, "archive-pages-never-listed",
                 server_only(V_FULL, listed_archive_problems),
                 detail=[_d("archive", ", ".join(p.path for p in
                                                 archive_pages(full_pages)))],
                 text=rendered[V_FULL.label])
    suite.record(GB, "one-count-line-for-the-archive",
                 server_only(V_FULL, count_line_problems),
                 detail=[_d("want", repr(expected_count_line(full_pages)))],
                 text=rendered[V_FULL.label])
    suite.record(GB, "roadmap-under-its-heading-no-item-heading",
                 server_only(V_FULL, roadmap_section_problems),
                 detail=[_d("want", "`## %s` once, the roadmap page under it, "
                                    "no `## %s`" % (ROADMAP_TYPE, ITEM_TYPE))],
                 text=rendered[V_FULL.label])
    suite.record(GB, "singular-for-one-item",
                 server_only(V_SINGLE, count_line_problems),
                 detail=[_d("want", repr(expected_count_line(
                     variant_pages(V_SINGLE))))],
                 text=rendered[V_SINGLE.label])
    suite.record(GB, "no-archive-line-at-zero-items",
                 server_only(V_NONE, count_line_problems),
                 detail=[_d("want", "no `- Roadmap archive:` line at all")],
                 text=rendered[V_NONE.label])

    only_pages = variant_pages(V_ARCHIVE_ONLY)
    problems = server_only(V_ARCHIVE_ONLY, check_render)
    found, _headings = sections(rendered[V_ARCHIVE_ONLY.label])
    want = [expected_count_line(only_pages)]
    if found.get(ROADMAP_TYPE) != want:
        problems.append("server: `## %s` holds %r, want only %r"
                        % (ROADMAP_TYPE, found.get(ROADMAP_TYPE), want))
    suite.record(GB, "archive-only-keeps-the-roadmap-heading", problems,
                 detail=[_d("corpus", "archive pages, no roadmap page")],
                 text=rendered[V_ARCHIVE_ONLY.label])

    group_b_delegate(suite, srv, reindex_mod)

    # The CLI, on a corpus of its own inside the sandbox.
    root = roots[V_CLI.label]
    index = os.path.join(root, "INDEX.md")
    problems = []
    if not _inside(root, work.path):
        problems.append("refusing: %s is outside the sandbox %s" % (root, work.path))
        for cid in ("cli-check-writes-nothing", "cli-writes-render-index",
                    "cli-check-fails-on-a-duplicate-slug",
                    "cli-reindex-no-server-exits-2"):
            suite.record(GB, cid, problems)
        return
    before = _tree_digests(root)
    rc, out, err = H.run_process([sys.executable, "-B", REINDEX, "--root", root,
                                  "--check"], cwd=work.path)
    after = _tree_digests(root)
    if rc != 0:
        problems.append("--check exited %d: %s" % (rc, err.strip()[:200]))
    changed = sorted(k for k in set(before) | set(after)
                     if before.get(k) != after.get(k))
    if changed:
        problems.append("--check changed the corpus: %r" % changed)
    suite.record(GB, "cli-check-writes-nothing", problems,
                 detail=[_d("argv", "reindex.py --root <sandbox>/docs --check"),
                         _d("files", "%d before, %d after"
                            % (len(before), len(after)))],
                 text=out)

    problems = []
    rc, out, err = H.run_process([sys.executable, "-B", REINDEX, "--root", root],
                                 cwd=work.path)
    if rc != 0:
        problems.append("reindex.py exited %d: %s" % (rc, err.strip()[:200]))
    written = _read(index) if os.path.isfile(index) else None
    want = _render(srv, root)
    if written is None:
        problems.append("INDEX.md was not written at %s" % index)
    elif written != want:
        problems.append("the written INDEX.md differs from the server's "
                        "render_index")
    suite.record(GB, "cli-writes-render-index", problems,
                 detail=[_d("argv", "reindex.py --root <sandbox>/docs"),
                         _d("bytes", "written %s, rendered %d"
                            % (None if written is None else len(written),
                               len(want)))],
                 text=written or out)

    group_b_wrapper(suite, srv, roots, work)


def group_b_delegate(suite, srv, reindex_mod):
    """reindex.py keeps a module-level `render_index` ONLY as a delegate: the
    frozen docs/adr/0022 anchors it by name.  A stub stands in for the loaded
    server; the delegate must hand it the entries and return its answer
    untouched -- a copy that renders by itself returns something else."""
    sentinel = object()
    seen = []
    stub = types.SimpleNamespace(
        render_index=lambda entries: (seen.append(entries), sentinel)[1])
    probe = []
    problems = []
    fn = getattr(reindex_mod, "render_index", None)
    if not callable(fn):
        problems.append("reindex.py has no module-level render_index (the "
                        "frozen docs/adr/0022 anchors it)")
    else:
        missing = object()
        saved = getattr(reindex_mod, "_SERVER", missing)
        reindex_mod._SERVER = stub
        try:
            got = fn(probe)
        except Exception as exc:              # a delegate that crashes on it
            got = exc
        finally:
            if saved is missing:
                del reindex_mod._SERVER
            else:
                reindex_mod._SERVER = saved
        if got is not sentinel:
            problems.append("render_index did not return the loaded server's "
                            "answer: got %s" % (repr(got)[:120]))
        if len(seen) != 1 or seen[0] is not probe:
            problems.append("the loaded server's render_index was called %d "
                            "time(s) with the caller's entries" % len(seen))
    suite.record(GB, "render-index-delegates", problems,
                 detail=[_d("stub", "reindex._SERVER = a namespace whose "
                                    "render_index returns a sentinel"),
                         _d("why", "the name must keep resolving for a frozen "
                                   "ADR, and must not be a second renderer")])


def group_b_wrapper(suite, srv, roots, work):
    """reindex.py's contract as a wrapper: its failure is the server's, read by
    the exported prefix, and without a server it cannot run at all."""
    root = roots[V_DUP.label]
    before = _tree_digests(root)
    rc, out, err = run_cli(REINDEX, ["--root", root, "--check",
                                     "--server", SERVER], work.path)
    after = _tree_digests(root)
    problems = []
    if rc != 1:
        problems.append("exit %r, want 1 on a duplicate slug: %s"
                        % (rc, err.strip()[:200]))
    prefix = getattr(srv, "REINDEX_DUPS_PREFIX", None)
    if prefix is None:
        problems.append("the server exports no REINDEX_DUPS_PREFIX to read "
                        "the failure by")
    elif not any(line.startswith(prefix) for line in out.splitlines()):
        problems.append("no line starts with the server's %r" % prefix)
    if before != after:
        problems.append("--check changed the corpus")
    suite.record(GB, "cli-check-fails-on-a-duplicate-slug", problems,
                 detail=[_d("argv", "reindex.py --root <sandbox>/dup --check "
                                    "--server Scripts/mcp-wiki.py"),
                         _d("exit", rc),
                         _d("pages", ", ".join(p.path for p in
                                               variant_pages(V_DUP)))],
                 text=out + err)

    copy_dir = relocated_cli(work)
    rc, out, err = run_cli(os.path.join(copy_dir, "reindex.py"),
                           ["--root", roots[V_FULL.label], "--check"],
                           work.path, without_env=(SERVER_ENV_VAR,))
    suite.record(GB, "cli-reindex-no-server-exits-2",
                 no_server_problems(rc, out, err),
                 detail=[_d("argv", "<sandbox>/cli-copy/reindex.py --check, "
                                    "$%s unset" % SERVER_ENV_VAR),
                         _d("exit", rc)],
                 text=out + err)


def no_server_problems(rc, out, err):
    problems = []
    if rc != 2:
        problems.append("exit %r, want 2 when no server module is found" % rc)
    if NOT_FOUND_TOKEN not in err:
        problems.append("stderr does not say %r: %r"
                        % (NOT_FOUND_TOKEN, err.strip()[:200]))
    if out.strip():
        problems.append("printed a report without a server: %r"
                        % out.strip()[:120])
    return problems


def group_c(suite, srv, roots):
    root = roots[V_FULL.label]
    pages = variant_pages(V_FULL)
    server_res = srv.reindex_collect(root)

    def per_copy(test):
        return test("server", server_res)

    suite.record(GC, "no-roadmap-item-orphan", per_copy(
        lambda label, res: ["%s: %s is an orphan" % (label, e["path"])
                            for e in res[2] if e["type"] == ITEM_TYPE]),
        detail=[_d("archive", "%d page(s), none linked by design"
                   % len(archive_pages(pages)))])
    suite.record(GC, "overview-still-exempt", per_copy(
        lambda label, res: ["%s: %s is an orphan" % (label, e["path"])
                            for e in res[2] if e["type"] == "overview"]))
    suite.record(GC, "unlinked-concept-is-orphan", per_copy(
        lambda label, res: [] if LONELY.name in orphan_names(res)
        else ["%s: %s is not an orphan" % (label, LONELY.path)]),
        detail=[_d("why", "without this the exemption could be `every type`")])

    u_server = srv.reindex_collect(roots[V_UNLINKED.label])
    problems = []
    if ROADMAP.name not in orphan_names(u_server):
        problems.append("server: the unlinked %s is not an orphan"
                        % ROADMAP.path)
    suite.record(GC, "unlinked-roadmap-page-is-orphan", problems,
                 detail=[_d("why", "only the item type is exempt; the live "
                                   "roadmap page must be linked from somewhere")])

    suite.record(GC, "zero-malformed", per_copy(
        lambda label, res: ["%s: malformed %r" % (label, res[3])] if res[3] else []))


def group_d(suite, srv, fresh_mod, roots, work):
    root = roots[V_FULL.label]

    def server_states(r):
        out = {}
        for relpath, fm, _body in srv.iter_pages(r):
            out[relpath.replace(os.sep, "/")] = srv._classify_page(
                relpath, fm, r, lambda _commit: None)["status"]
        return out

    states = server_states(root)
    pages = variant_pages(V_FULL)
    roadmap_pages = [p for p in pages if p.type in (ROADMAP_TYPE, ITEM_TYPE)]
    problems = ["server: %s classified %r, want 'untracked'"
                % (p.path, states.get(p.path))
                for p in roadmap_pages if states.get(p.path) != "untracked"]
    suite.record(GD, "roadmap-types-untracked-server", problems,
                 detail=[_d("pages", "%d roadmap/roadmap-item page(s)"
                            % len(roadmap_pages))])

    problems = [] if states.get(LONELY.path) == "no-sources" else [
        "server: %s classified %r, want 'no-sources'"
        % (LONELY.path, states.get(LONELY.path))]
    suite.record(GD, "sourceless-concept-stays-no-sources", problems,
                 detail=[_d("why", "the exemption is per type, not for every "
                                   "sourceless page")])

    sourced = server_states(roots[V_SOURCED.label])
    problems = ["server: a %s carrying sources: classified 'untracked'"
                % ITEM_TYPE] if sourced.get(SOURCED_ITEM.path) == "untracked" \
        else []
    suite.record(GD, "sourced-item-is-not-untracked", problems,
                 detail=[_d("server", sourced.get(SOURCED_ITEM.path)),
                         _d("why", "the untracked short-circuit is reached only "
                                   "without sources:, which is why the roadmap "
                                   "writer never writes that key")])

    group_d_unverified(suite, srv, fresh_mod, roots, work)
    group_d_wrapper(suite, srv, fresh_mod, roots, work)


def cli_verdict_problems(srv, rc, text, page, bucket, want_gating):
    """The CLI's contract on a one-page corpus: listed in its bucket, ONE line
    carrying the server's GATING_LINE_PREFIX whose count is the wanted one, an
    `advisory:` line for git lag and none otherwise, and an exit code that
    agrees with the gating count."""
    problems = []
    want_rc = 1 if want_gating else 0
    if rc != want_rc:
        problems.append("exit %r, want %r" % (rc, want_rc))
    lines = text.splitlines()
    listed = [ln for ln in lines
              if ln.startswith("- ") and ("`%s`" % page.path) in ln]
    if not listed:
        problems.append("%s is not listed in the report" % page.path)
    if "%s (1):" % bucket not in lines:
        problems.append("no `%s (1):` bucket in the report" % bucket)
    count = gating_number(srv, text)
    if count != want_gating:
        problems.append("gating line %r reads as %r, want a count of %d"
                        % (gating_line(srv, text), count, want_gating))
    prefix = getattr(srv, "ADVISORY_LINE_PREFIX", "advisory: ")
    advisory = [ln for ln in lines if ln.startswith(prefix)]
    if want_gating and advisory:
        problems.append("an orphaned-source page produced an advisory line: %r"
                        % advisory)
    if not want_gating and len(advisory) != 1:
        problems.append("%d `advisory:` line(s) for git lag, want 1"
                        % len(advisory))
    return problems, (listed[0] if listed else "(none)"), gating_line(srv, text)


def group_d_unverified(suite, srv, fresh_mod, roots, work):
    """Git lag never sets the exit code -- for every editorial status.

    A page with sources and no verified.commit is `unverified`: listed,
    advisory, exit 0.  `stale` is advisory too.  `orphaned-source` (a sources:
    path gone from the tree) gates, because the server's verify counts that
    dead path as a broken anchor -- the CLI no longer decides any of this, it
    reads the server's `gating:` line.  The two verified pages live in real
    fixture repositories, so git itself decides stale versus orphaned.
    """
    for status in UNVERIFIED_STATUSES:
        variant = V_UNVERIFIED[status]
        rc, text, err = run_freshness_cli(fresh_mod, roots[variant.label])
        problems, row, gline = cli_verdict_problems(
            srv, rc, text, variant.only[0], "unverified", 0)
        suite.record(GD, "cli-unverified-%s-listed-advisory"
                     % (status or "nostatus"), problems,
                     detail=[_d("status", status or "(absent)"),
                             _d("exit", rc), _d("row", row),
                             _d("gating", gline)],
                     text=text + err)

    for label, slug, source, bucket, want in (
            ("git-stale", "stale-page", STALE_SOURCE, "stale", 0),
            ("git-orphan", "orphan-page", GONE_SOURCE, "orphaned-source", 1)):
        root, page, problems = git_fixture(work, label, slug, source)
        rc, text, err, row, gline = None, "", "", "(none)", "(none)"
        if root is not None:
            rc, text, err = run_freshness_cli(fresh_mod, root)
            problems, row, gline = cli_verdict_problems(
                srv, rc, text, page, bucket, want)
        suite.record(GD, "cli-%s-%s" % (bucket,
                                        "gates" if want else "advisory"),
                     problems,
                     detail=[_d("exit", rc), _d("row", row),
                             _d("gating", gline),
                             _d("fixture", "a git repo: page verified at c1, "
                                           "%s moved in c2, source %s"
                                % (STALE_SOURCE, source))],
                     text=text + err)

    draft = V_UNVERIFIED["draft"]
    got = {}
    for relpath, fm, _body in srv.iter_pages(roots[draft.label]):
        got[relpath.replace(os.sep, "/")] = srv._classify_page(
            relpath, fm, roots[draft.label], lambda _commit: None)["status"]
    path = draft.only[0].path
    suite.record(GD, "server-draft-unverified-stays-visible",
                 [] if got.get(path) == "unverified"
                 else ["server classified %s %r, want 'unverified'"
                       % (path, got.get(path))],
                 detail=[_d("why", "the server classifies it `unverified`; "
                                   "git lag never gates")])


def group_d_wrapper(suite, srv, fresh_mod, roots, work):
    """freshness.py as a wrapper: its verdict and its words are the server's.

    The dead-body-anchor case is the one R-0036 was hidden by: an ACTIVE
    concept page whose body names a file that is gone.  No `sources:`, so the
    pre-R-0033 CLI classified it `no-sources` and exited 0 while the server's
    `gating:` line counted it."""
    root = roots[V_DEAD_BODY.label]
    rc, text, err = run_freshness_cli(fresh_mod, root)
    answer = server_freshness(srv, root)
    ours, theirs = gating_number(srv, text), gating_number(srv, answer)
    problems = []
    if not theirs:
        problems.append("the server's own answer does not gate the planted "
                        "dead body anchor (gating %r) -- the fixture proves "
                        "nothing" % theirs)
    if ours != theirs:
        problems.append("the CLI's gating number %r is not the server's %r"
                        % (ours, theirs))
    if rc != 1:
        problems.append("exit %r, want 1 while the server gates" % rc)
    suite.record(GD, "cli-dead-body-anchor-gates-like-the-server", problems,
                 detail=[_d("page", "%s, active concept, body anchor `%s`"
                            % (DEAD_BODY.path, DEAD_BODY_ANCHOR)),
                         _d("exit", rc),
                         _d("cli", gating_line(srv, text)),
                         _d("server", gating_line(srv, answer))],
                 text=text + err)

    root = roots[V_FULL.label]
    rc, text, err = run_freshness_cli(fresh_mod, root)
    answer = server_freshness(srv, root)
    problems = []
    if text != answer.rstrip("\n") + "\n":
        diff = [ln for ln in difflib.unified_diff(
            answer.splitlines(), text.splitlines(), "server", "freshness.py",
            n=0, lineterm="") if not ln.startswith("@@")]
        problems.append("the CLI's report is not the server's answer: %s"
                        % " | ".join(diff[2:8]))
    if rc != 0:
        problems.append("exit %r on a corpus the server does not gate: %s"
                        % (rc, err.strip()[:200]))
    suite.record(GD, "cli-output-is-the-server-answer", problems,
                 detail=[_d("corpus", "%d page(s)" % len(variant_pages(V_FULL))),
                         _d("bytes", "cli %d, server %d"
                            % (len(text), len(answer)))],
                 text=text + err)

    copy_dir = relocated_cli(work)
    rc, out, err = run_cli(os.path.join(copy_dir, "freshness.py"),
                           ["--root", roots[V_FULL.label]],
                           work.path, without_env=(SERVER_ENV_VAR,))
    suite.record(GD, "cli-freshness-no-server-exits-2",
                 no_server_problems(rc, out, err),
                 detail=[_d("argv", "<sandbox>/cli-copy/freshness.py, "
                                    "$%s unset" % SERVER_ENV_VAR),
                         _d("exit", rc)],
                 text=out + err)


def group_e(suite, srv, lib, roots):
    # 1. INDEX_COUNTED_TYPES emptied in a second server load -> the B oracle
    #    must name every archive page as listed.
    ctl = H.load_module_from_path("mcp_wiki_index_control_counted", SERVER)
    ctl.INDEX_COUNTED_TYPES = ()
    pages = variant_pages(V_FULL)
    fired = check_render(_render(ctl, roots[V_FULL.label]), pages, "server")
    missed = ["server: %s" % p.path for p in archive_pages(pages)
              if not any(p.path in f for f in fired)]
    suite.record(GE, "control-uncounted-items-are-reported-listed",
                 ["the B oracle did not report %s as listed" % m for m in missed],
                 detail=[_d("planted", "INDEX_COUNTED_TYPES = () in a second "
                                       "server load"),
                         _d("fired", "%d problem(s)" % len(fired))])

    # 2. UNTRACKED_TYPES minus `roadmap` in a copy of the server namespace ->
    #    the A oracle must fail, naming the constant.
    srv_copy = types.SimpleNamespace(**{name: getattr(srv, name)
                                        for name in CONSTANTS})
    srv_copy.UNTRACKED_TYPES = set(srv.UNTRACKED_TYPES) - {ROADMAP_TYPE}
    fired = check_constants(srv_copy, lib)
    problems = [] if any("UNTRACKED_TYPES" in f for f in fired) else [
        "the A oracle accepted UNTRACKED_TYPES without %r: %r"
        % (ROADMAP_TYPE, fired)]
    suite.record(GE, "control-untracked-without-roadmap-fails-parity", problems,
                 detail=[_d("planted", "server UNTRACKED_TYPES - {%r}"
                            % ROADMAP_TYPE),
                         _d("fired", "%d problem(s)" % len(fired))])

    # 3. ORPHAN_EXEMPT_TYPES back to overview-only in a second server load ->
    #    the C oracle must name every archive page as an orphan.
    ctl = H.load_module_from_path("mcp_wiki_index_control_orphans", SERVER)
    ctl.ORPHAN_EXEMPT_TYPES = ("overview",)
    pages = variant_pages(V_FULL)
    fired = check_collect(ctl.reindex_collect(roots[V_FULL.label]), pages,
                          "server")
    missed = ["server: %s" % p.path for p in archive_pages(pages)
              if not any(p.path in f for f in fired)]
    suite.record(GE, "control-unexempt-items-are-reported-orphans",
                 ["the C oracle did not report %s as an orphan" % m for m in missed],
                 detail=[_d("planted", "ORPHAN_EXEMPT_TYPES = ('overview',) in a "
                                       "second server load"),
                         _d("fired", "%d problem(s)" % len(fired))])

    # 4. The single-home AST scanner must see a planted module-level copy.
    planted = ("import os\nif True:\n    TYPE_ORDER = []\n"
               "def f():\n    INDEX_LABELLED = ()\n")
    fired = single_home_problems("planted.py", planted)
    problems = []
    if not any("TYPE_ORDER" in f for f in fired):
        problems.append("the scanner missed a module-level TYPE_ORDER")
    if any("INDEX_LABELLED" in f for f in fired):
        problems.append("the scanner flagged a function-local name")
    suite.record(GE, "control-single-home-scanner-fires", problems,
                 detail=[_d("fired", repr(fired)),
                         _d("why", "a scanner that matches nothing is "
                                   "indistinguishable from a clean tree")])

    # 5. The G oracle must fire on the helper's old shape: a bare `git` argv,
    #    no timeout, no env -- one problem per missing piece.
    bare = [(["git"] + GIT_PROBE_ARGS, {"stdin": subprocess.DEVNULL})]
    fired = git_call_problems("planted", bare, roadmap_safe_argv(),
                              getattr(srv, "GIT_TIMEOUT_SEC", 30))
    problems = ["the G oracle missed the absent %s" % piece
                for piece, word in (("prefix", "GIT_SAFE_ARGV"),
                                     ("timeout", "timeout"),
                                     ("env", LAZY_FETCH_ENV))
                if not any(word in f for f in fired)]
    suite.record(GE, "control-bare-git-call-fires-the-git-oracle", problems,
                 detail=[_d("planted", "argv %r, stdin=DEVNULL only"
                            % bare[0][0]),
                         _d("fired", repr(fired))])


def group_g(suite, srv, lib):
    want_argv = roadmap_safe_argv()
    want_timeout = getattr(srv, "GIT_TIMEOUT_SEC", None)
    copies = (("server", srv), ("skill", lib))

    problems = []
    if want_timeout is None:
        problems.append("the server has no GIT_TIMEOUT_SEC")
    elif getattr(lib, "GIT_TIMEOUT_SEC", None) != want_timeout:
        problems.append("_wikilib GIT_TIMEOUT_SEC is %r, the server's is %r"
                        % (getattr(lib, "GIT_TIMEOUT_SEC", None), want_timeout))
    suite.record(GG, "timeout-constant-parity", problems,
                 detail=[_d("server", repr(want_timeout)),
                         _d("_wikilib", repr(getattr(lib, "GIT_TIMEOUT_SEC",
                                                     None)))])

    problems = []
    for label, mod in copies:
        value = getattr(mod, "GIT_SAFE_ARGV", None)
        if value is None or tuple(value) != want_argv:
            problems.append("%s GIT_SAFE_ARGV is %r, roadmap.py's is %r"
                            % (label, value, want_argv))
    suite.record(GG, "safe-argv-parity-with-roadmap", problems,
                 detail=[_d("roadmap.py", repr(want_argv)),
                         _d("read", "ast.literal_eval of its source, never "
                                    "executed")])

    for label, mod in copies:
        result, calls = record_git(mod)
        problems = git_call_problems(label, calls, want_argv, want_timeout)
        if isinstance(result, Exception):
            problems.append("%s: git() raised %r" % (label, result))
        suite.record(GG, "spawn-shape-" + label, problems,
                     detail=[_d("argv", repr(calls[0][0]) if calls else "-"),
                             _d("kwargs", ", ".join(sorted(calls[0][1]))
                                if calls else "-")])

    for label, mod in copies:
        result, _calls = record_git(mod, raise_timeout=True)
        problems = []
        if isinstance(result, Exception):
            problems.append("%s: a timeout escaped git() as %r"
                            % (label, result))
        elif not (isinstance(result, tuple) and result[0] == 124):
            problems.append("%s: a timeout returned %r, want rc 124"
                            % (label, result))
        suite.record(GG, "timeout-returns-124-" + label, problems,
                     detail=[_d("result", repr(result)),
                             _d("why", "124 is what every caller already "
                                       "reads as `git could not answer`")])

    problems = []
    want_root = os.path.realpath(H.REPO_ROOT)
    for label, mod in copies:
        rc, out, err = mod.git(list(GIT_PROBE_ARGS), cwd=H.REPO_ROOT)
        if rc != 0:
            problems.append("%s: real git exited %d: %s"
                            % (label, rc, err.strip()[:200]))
        elif os.path.realpath(out.strip()) != want_root:
            problems.append("%s: toplevel %r, want %r"
                            % (label, out.strip(), want_root))
    suite.record(GG, "real-git-accepts-the-hardened-argv", problems,
                 detail=[_d("argv", "git() %s in the repo root"
                            % " ".join(GIT_PROBE_ARGS)),
                         _d("why", "a -c pair a real git rejects passes every "
                                   "recorded assertion above")])


def _skill_sources():
    return {name: _read(os.path.join(SCRIPTS_DIR, name))
            for name in ("_wikilib.py", "freshness.py", "reindex.py")}


def group_h(suite, srv, lib):
    """Everything the server vendors from _wikilib that groups A and G do not
    already gate.

    Functions are compared as CODE (see `normalized_function`), because the
    fixture corpora cannot reach every branch -- a quoted scalar, a nested
    dict, a `path:symbol` anchor -- and a copy that drifted in an unreached
    branch would pass any output comparison.  The census still reads
    freshness.py and reindex.py: they carry no vendored code now (R-0033), and
    a name they grow that the server also defines is the first sign of a copy
    coming back."""
    sources = _skill_sources()
    server_src = _read(SERVER)
    for label, skill_name, server_name in VENDORED_FUNCTIONS:
        problems = code_parity_problems(sources[label], skill_name, server_src,
                                        server_name, label)
        suite.record(GH, "code-parity-" + skill_name, problems,
                     detail=[_d("copies", "%s:%s, mcp-wiki.py:%s"
                                % (label, skill_name, server_name)),
                             _d("ignored", "docstring, annotations, the "
                                           "function's own name, `%s.`"
                                % SKILL_ALIAS)])

    mods = {"_wikilib.py": lib}
    for label, name in VENDORED_CONSTANTS:
        problems = constant_parity_problems(mods[label], srv, name, label)
        suite.record(GH, "value-parity-" + name, problems,
                     detail=[_d(label, repr(getattr(mods[label], name, None))),
                             _d("server", repr(getattr(srv, name, None)))])

    undeclared = undeclared_shared_names(sources, server_src)
    suite.record(GH, "every-shared-name-is-gated",
                 ["defined in a skill script AND the server, compared by no "
                  "case: %s" % ", ".join(undeclared)] if undeclared else [],
                 detail=[_d("declared", "%d function pair(s), %d constant(s) "
                            "here, %d in group A, %d in group G, %d different "
                            "on purpose" % (len(VENDORED_FUNCTIONS),
                                            len(VENDORED_CONSTANTS),
                                            len(CONSTANTS),
                                            len(GATED_ELSEWHERE),
                                            len(DECLARED_DIFFERENT)))])


def group_e_vendored(suite, srv, lib):
    """Controls for group H's oracles: each must FIRE on a planted defect and
    stay SILENT on every difference it declares acceptable."""
    base = ('def f(value):\n    """doc"""\n    if value is None:\n'
            '        return []\n    return [value]\n')
    quiet = ('def g(value: "Any") -> list:\n    """other doc"""\n'
             '    if value is None:\n        return []\n'
             '    return [value]\n')
    qualified = ('def f(value):\n    if w.value is None:\n        return []\n'
                 '    return [value]\n')
    plain = ('def f(value):\n    if value is None:\n        return []\n'
             '    return [value]\n')
    drifted = ('def f(value):\n    value = str(value)\n    if value is None:'
               '\n        return []\n    return [value]\n')
    problems = []
    if code_parity_problems(base, "f", quiet, "g", "planted.py"):
        problems.append("fired on a docstring/annotation/name-only difference")
    if code_parity_problems(qualified, "f", plain, "f", "planted.py"):
        problems.append("fired on the skill side's `w.` qualifier")
    fired = code_parity_problems(base, "f", drifted, "f", "planted.py")
    if not fired or "str(value)" not in fired[0]:
        problems.append("missed one added statement: %r" % fired)
    suite.record(GE, "control-code-parity-fires-only-on-code", problems,
                 detail=[_d("fired", repr(fired)),
                         _d("silent on", "docstring, annotations, function "
                                         "name, `w.` qualifier")])

    ctl = types.SimpleNamespace(SKIP_DIRS=set(lib.SKIP_DIRS) | {"planted"},
                                _WIKILINK_RE=re.compile(r"\[\[([^\]]+)\]\]",
                                                        re.I))
    fired = (constant_parity_problems(ctl, srv, "SKIP_DIRS", "planted")
             + constant_parity_problems(ctl, srv, "_WIKILINK_RE", "planted"))
    problems = ["the value oracle missed %s" % name
                for name in ("SKIP_DIRS", "_WIKILINK_RE")
                if not any(name in f for f in fired)]
    suite.record(GE, "control-value-parity-fires", problems,
                 detail=[_d("planted", "an extra skipped directory; the "
                                       "wikilink regex with re.I"),
                         _d("fired", repr(fired))])

    planted = {"planted.py": "def as_list(v):\n    return v\nMAIN_ONLY = 1\n"
                             "def render_index(e):\n    return e\n"}
    server_src = "def as_list(v):\n    return v\nNEW_SHARED = 2\n" \
                 "def brand_new(x):\n    return x\n"
    planted["planted.py"] += "NEW_SHARED = 2\ndef brand_new(x):\n    return x\n"
    fired = undeclared_shared_names(planted, server_src)
    problems = []
    if fired != ["planted.py: NEW_SHARED", "planted.py: brand_new"]:
        problems.append("the census reported %r, want exactly the two "
                        "undeclared shared names" % fired)
    suite.record(GE, "control-census-names-an-undeclared-copy", problems,
                 detail=[_d("fired", repr(fired))])


def group_f(suite, pyc_before, tree_before, live_before, work_path):
    live_after = (H.sha256_file(LIVE_INDEX) if os.path.isfile(LIVE_INDEX)
                  else None)
    suite.record(GF, "live-index-unchanged",
                 [] if live_after == live_before
                 else ["docs/INDEX.md changed: %s -> %s" % (live_before, live_after)],
                 detail=[_d("sha256", live_before)])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GF, "no-new-repo-paths",
                 [] if not added
                 else ["%d new path(s): %s" % (len(added), added[:5])])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GF, "no-pycache-written",
                 [] if not (new or touched)
                 else ["new=%r touched=%r" % (new, touched)],
                 detail=[_d("pyc", "before=%d after=%d"
                            % (len(pyc_before), len(pyc_after)))])

    suite.record(GF, "workspace-outside-repo",
                 ["the workspace %s is inside the repo" % work_path]
                 if _inside(work_path, H.REPO_ROOT) else [],
                 detail=[_d("workspace", work_path)])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="INDEX.md rendering and the page-type constants, "
                          "server and skill copies side by side",
                    opts=opts, mode="grouped")
    tree_before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    live_before = (H.sha256_file(LIVE_INDEX) if os.path.isfile(LIVE_INDEX)
                   else None)
    saved_path = list(sys.path)
    work = H.TempWorkspace("ph-wiki-index-", keep=opts.keep)
    try:
        srv = H.load_module_from_path("mcp_wiki_index_under_test", SERVER)
        reindex_mod = H.load_module_from_path("wiki_index_reindex", REINDEX)
        fresh_mod = H.load_module_from_path("wiki_index_freshness", FRESHNESS)
        # _wikilib loaded off disk by path: the CLIs no longer import it, so
        # neither of them is the route to the copy this suite judges.
        lib = H.load_module_from_path("wiki_index_wikilib", WIKILIB)
        roots = {v.label: build_variant(work, v) for v in ALL_VARIANTS}

        group_a(suite, srv, lib)
        group_b(suite, srv, reindex_mod, roots, work)
        group_c(suite, srv, roots)
        group_d(suite, srv, fresh_mod, roots, work)
        group_e(suite, srv, lib, roots)
        group_g(suite, srv, lib)
        group_h(suite, srv, lib)
        group_e_vendored(suite, srv, lib)
    finally:
        # Loading reindex.py / freshness.py put their directory on sys.path and
        # their sibling imports into sys.modules; no later suite in this
        # process inherits either.
        sys.path[:] = saved_path
        sys.modules.pop("_wikilib", None)
        sys.modules.pop("measure_cli", None)
        work.cleanup()
        group_f(suite, pyc_before, tree_before, live_before, work.path)

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
