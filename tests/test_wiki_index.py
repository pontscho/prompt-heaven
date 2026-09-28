#!/usr/bin/env python3
"""INDEX.md rendering and the wiki page-type constants, in BOTH copies (A-F).

THE TWO COPIES
--------------
The wiki's INDEX renderer, its orphan rule and its page-type vocabulary live
twice: once in the p:wiki skill scripts (`ClaudeCode/skills/wiki/scripts/`,
homed in `_wikilib.py`) and once in `Scripts/mcp-wiki.py`.  The duplication is
deliberate -- a fleet server never imports a sibling, and the amalgamate
generator cannot reach a skill script -- so the only thing that keeps the two
honest is a gate that loads both and compares them.  This suite is that gate.

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
  B  render_index: byte-identical between the copies on every corpus variant,
     archive pages counted and never listed, the reindex.py CLI
  C  reindex collect: equal between the copies, the orphan exemption
  D  freshness classification of the two roadmap types
  E  negative controls -- each proves an oracle above can FIRE
  F  hygiene: the live docs/INDEX.md, the repo tree, bytecode, the sandbox

Every expected count is derived from the fixture list below, never typed.
The case count lives only in the SUITES table of tests/run.py.
"""

import ast
import collections
import os
import posixpath
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
LIVE_INDEX = H.repo_path("docs", "INDEX.md")

GA = "A. constant parity: one vocabulary, two copies"
GB = "B. render_index, both copies"
GC = "C. reindex collect, both copies"
GD = "D. freshness: the roadmap types are untracked"
GE = "E. negative control"
GF = "F. hygiene"

# The six names the two copies must agree on.  Their VALUES are never typed
# here: parity compares the copies, and the roadmap cases below name only the
# two type strings the fixture itself is written in.
CONSTANTS = ("TYPE_ORDER", "INDEX_LABELLED", "STATUS_FORBIDDEN",
             "UNTRACKED_TYPES", "ORPHAN_EXEMPT_TYPES", "INDEX_COUNTED_TYPES")

ROADMAP_TYPE = "roadmap"
ITEM_TYPE = "roadmap-item"


def _d(label, value):
    return "%-12s: %s" % (label, value)


# ---------------------------------------------------------------------------
# The fixture: one list of pages; every variant is a filter of it
# ---------------------------------------------------------------------------

Page = collections.namedtuple("Page", "path name type title links extra")


def _page(path, name, typ, title, links=(), extra=""):
    return Page(path, name, typ, title, tuple(links), extra)


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

Variant = collections.namedtuple("Variant",
                                 "label items with_roadmap link_roadmap only")

V_FULL = Variant("full", len(ARCHIVE), True, True, None)
V_SINGLE = Variant("single", 1, True, True, None)
V_NONE = Variant("none", 0, True, True, None)
V_ARCHIVE_ONLY = Variant("archive-only", len(ARCHIVE), False, False, None)
V_UNLINKED = Variant("unlinked-roadmap", len(ARCHIVE), True, False, None)
V_CLI = Variant("cli", len(ARCHIVE), True, True, None)
V_SOURCED = Variant("sourced-item", 0, False, False, (SOURCED_ITEM,))

RENDER_VARIANTS = (V_FULL, V_SINGLE, V_NONE, V_ARCHIVE_ONLY, V_UNLINKED)
ALL_VARIANTS = RENDER_VARIANTS + (V_CLI, V_SOURCED)


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
            "wiki_index fixture.\n%s---\n\n# %s\n\nFixture body. %s\n"
            % (page.name, page.type, page.title, page.title, page.extra,
               page.title, body_links))


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


def _lib_copy(lib, **changes):
    ns = types.SimpleNamespace(**{k: getattr(lib, k) for k in dir(lib)
                                  if not k.startswith("__")})
    for key, value in changes.items():
        setattr(ns, key, value)
    return ns


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


def _renders(srv, reindex_mod, root):
    return (srv.render_index(srv.reindex_collect(root)[0]),
            reindex_mod.render_index(reindex_mod.collect(root)[0]))


def group_b(suite, srv, reindex_mod, roots, work):
    rendered = {}
    for variant in RENDER_VARIANTS:
        server_text, skill_text = _renders(srv, reindex_mod, roots[variant.label])
        rendered[variant.label] = (server_text, skill_text)
        problems = [] if server_text == skill_text else [
            "the two render_index copies differ on the %s corpus" % variant.label]
        suite.record(GB, "render-parity-" + variant.label, problems,
                     detail=[_d("corpus", "%d page(s), %d archive"
                                % (len(variant_pages(variant)),
                                   len(archive_pages(variant_pages(variant))))),
                             _d("bytes", "server %d, skill %d"
                                % (len(server_text), len(skill_text)))],
                     text=server_text if problems else "")

    def both(variant, oracle):
        pages = variant_pages(variant)
        server_text, skill_text = rendered[variant.label]
        return (oracle(server_text, pages, "server")
                + oracle(skill_text, pages, "skill"))

    full_pages = variant_pages(V_FULL)
    suite.record(GB, "archive-pages-never-listed",
                 both(V_FULL, listed_archive_problems),
                 detail=[_d("archive", ", ".join(p.path for p in
                                                 archive_pages(full_pages)))],
                 text=rendered[V_FULL.label][0])
    suite.record(GB, "one-count-line-for-the-archive",
                 both(V_FULL, count_line_problems),
                 detail=[_d("want", repr(expected_count_line(full_pages)))],
                 text=rendered[V_FULL.label][0])
    suite.record(GB, "roadmap-under-its-heading-no-item-heading",
                 both(V_FULL, roadmap_section_problems),
                 detail=[_d("want", "`## %s` once, the roadmap page under it, "
                                    "no `## %s`" % (ROADMAP_TYPE, ITEM_TYPE))],
                 text=rendered[V_FULL.label][0])
    suite.record(GB, "singular-for-one-item",
                 both(V_SINGLE, count_line_problems),
                 detail=[_d("want", repr(expected_count_line(
                     variant_pages(V_SINGLE))))],
                 text=rendered[V_SINGLE.label][0])
    suite.record(GB, "no-archive-line-at-zero-items",
                 both(V_NONE, count_line_problems),
                 detail=[_d("want", "no `- Roadmap archive:` line at all")],
                 text=rendered[V_NONE.label][0])

    only_pages = variant_pages(V_ARCHIVE_ONLY)
    problems = both(V_ARCHIVE_ONLY, check_render)
    for label, text in zip(("server", "skill"), rendered[V_ARCHIVE_ONLY.label]):
        found, _headings = sections(text)
        want = [expected_count_line(only_pages)]
        if found.get(ROADMAP_TYPE) != want:
            problems.append("%s: `## %s` holds %r, want only %r"
                            % (label, ROADMAP_TYPE, found.get(ROADMAP_TYPE), want))
    suite.record(GB, "archive-only-keeps-the-roadmap-heading", problems,
                 detail=[_d("corpus", "archive pages, no roadmap page")],
                 text=rendered[V_ARCHIVE_ONLY.label][0])

    # The CLI, on a corpus of its own inside the sandbox.
    root = roots[V_CLI.label]
    index = os.path.join(root, "INDEX.md")
    problems = []
    if not _inside(root, work.path):
        problems.append("refusing: %s is outside the sandbox %s" % (root, work.path))
        suite.record(GB, "cli-check-writes-nothing", problems)
        suite.record(GB, "cli-writes-render-index", problems)
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
    want = reindex_mod.render_index(reindex_mod.collect(root)[0])
    if written is None:
        problems.append("INDEX.md was not written at %s" % index)
    elif written != want:
        problems.append("the written INDEX.md differs from render_index")
    suite.record(GB, "cli-writes-render-index", problems,
                 detail=[_d("argv", "reindex.py --root <sandbox>/docs"),
                         _d("bytes", "written %s, rendered %d"
                            % (None if written is None else len(written),
                               len(want)))],
                 text=written or out)


def _collects(srv, reindex_mod, root):
    return srv.reindex_collect(root), reindex_mod.collect(root)


def group_c(suite, srv, reindex_mod, roots):
    root = roots[V_FULL.label]
    pages = variant_pages(V_FULL)
    server_res, skill_res = _collects(srv, reindex_mod, root)
    labels = ("entries", "dups", "orphans", "malformed")
    problems = ["%s differ between the copies" % label
                for label, a, b in zip(labels, server_res, skill_res) if a != b]
    suite.record(GC, "collect-parity", problems,
                 detail=[_d("orphans", "server %r, skill %r"
                            % (orphan_names(server_res), orphan_names(skill_res)))])

    def per_copy(test):
        out = []
        for label, res in (("server", server_res), ("skill", skill_res)):
            out += test(label, res)
        return out

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

    u_root = roots[V_UNLINKED.label]
    u_server, u_skill = _collects(srv, reindex_mod, u_root)
    problems = []
    for label, res in (("server", u_server), ("skill", u_skill)):
        if ROADMAP.name not in orphan_names(res):
            problems.append("%s: the unlinked %s is not an orphan"
                            % (label, ROADMAP.path))
    suite.record(GC, "unlinked-roadmap-page-is-orphan", problems,
                 detail=[_d("why", "only the item type is exempt; the live "
                                   "roadmap page must be linked from somewhere")])

    suite.record(GC, "zero-malformed", per_copy(
        lambda label, res: ["%s: malformed %r" % (label, res[3])] if res[3] else []))


def group_d(suite, srv, fresh_mod, roots):
    root = roots[V_FULL.label]

    def server_states(r):
        out = {}
        for relpath, fm, _body in srv.iter_pages(r):
            out[relpath.replace(os.sep, "/")] = srv._classify_page(
                relpath, fm, r, lambda _commit: None)["status"]
        return out

    def skill_states(r):
        return {p["path"].replace(os.sep, "/"): p["status"]
                for p in fresh_mod.analyze(r, "HEAD")["pages"]}

    states = {"server": server_states(root), "skill": skill_states(root)}
    pages = variant_pages(V_FULL)
    roadmap_pages = [p for p in pages if p.type in (ROADMAP_TYPE, ITEM_TYPE)]
    for label in ("skill", "server"):
        problems = ["%s: %s classified %r, want 'untracked'"
                    % (label, p.path, states[label].get(p.path))
                    for p in roadmap_pages
                    if states[label].get(p.path) != "untracked"]
        suite.record(GD, "roadmap-types-untracked-" + label, problems,
                     detail=[_d("pages", "%d roadmap/roadmap-item page(s)"
                                % len(roadmap_pages))])

    problems = ["%s: %s classified %r, want 'no-sources'"
                % (label, LONELY.path, states[label].get(LONELY.path))
                for label in ("skill", "server")
                if states[label].get(LONELY.path) != "no-sources"]
    suite.record(GD, "sourceless-concept-stays-no-sources", problems,
                 detail=[_d("why", "the exemption is per type, not for every "
                                   "sourceless page")])

    s_root = roots[V_SOURCED.label]
    sourced = {"server": server_states(s_root), "skill": skill_states(s_root)}
    problems = ["%s: a %s carrying sources: classified 'untracked'"
                % (label, ITEM_TYPE) for label in ("skill", "server")
                if sourced[label].get(SOURCED_ITEM.path) == "untracked"]
    suite.record(GD, "sourced-item-is-not-untracked", problems,
                 detail=[_d("server", sourced["server"].get(SOURCED_ITEM.path)),
                         _d("skill", sourced["skill"].get(SOURCED_ITEM.path)),
                         _d("why", "the untracked short-circuit is reached only "
                                   "without sources:, which is why the roadmap "
                                   "writer never writes that key")])


def group_e(suite, srv, reindex_mod, lib, roots):
    # 1. INDEX_COUNTED_TYPES emptied in BOTH copies -> the B oracle must name
    #    every archive page as listed.
    ctl = H.load_module_from_path("mcp_wiki_index_control_counted", SERVER)
    ctl.INDEX_COUNTED_TYPES = ()
    saved = reindex_mod.w
    reindex_mod.w = _lib_copy(lib, INDEX_COUNTED_TYPES=())
    try:
        pages = variant_pages(V_FULL)
        server_text, skill_text = _renders(ctl, reindex_mod, roots[V_FULL.label])
        fired = (check_render(server_text, pages, "server")
                 + check_render(skill_text, pages, "skill"))
    finally:
        reindex_mod.w = saved
    missed = ["%s: %s" % (label, p.path) for label in ("server", "skill")
              for p in archive_pages(pages)
              if not any(f.startswith(label) and p.path in f for f in fired)]
    suite.record(GE, "control-uncounted-items-are-reported-listed",
                 ["the B oracle did not report %s as listed" % m for m in missed],
                 detail=[_d("planted", "INDEX_COUNTED_TYPES = () in a second "
                                       "server load and in a _wikilib copy"),
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

    # 3. ORPHAN_EXEMPT_TYPES back to overview-only in BOTH copies -> the C
    #    oracle must name every archive page as an orphan.
    ctl = H.load_module_from_path("mcp_wiki_index_control_orphans", SERVER)
    ctl.ORPHAN_EXEMPT_TYPES = ("overview",)
    reindex_mod.w = _lib_copy(lib, ORPHAN_EXEMPT_TYPES=("overview",))
    try:
        pages = variant_pages(V_FULL)
        server_res, skill_res = _collects(ctl, reindex_mod, roots[V_FULL.label])
        fired = (check_collect(server_res, pages, "server")
                 + check_collect(skill_res, pages, "skill"))
    finally:
        reindex_mod.w = saved
    missed = ["%s: %s" % (label, p.path) for label in ("server", "skill")
              for p in archive_pages(pages)
              if not any(f.startswith(label) and p.path in f for f in fired)]
    suite.record(GE, "control-unexempt-items-are-reported-orphans",
                 ["the C oracle did not report %s as an orphan" % m for m in missed],
                 detail=[_d("planted", "ORPHAN_EXEMPT_TYPES = ('overview',) in a "
                                       "second server load and a _wikilib copy"),
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
        lib = reindex_mod.w
        roots = {v.label: build_variant(work, v) for v in ALL_VARIANTS}

        group_a(suite, srv, lib)
        group_b(suite, srv, reindex_mod, roots, work)
        group_c(suite, srv, reindex_mod, roots)
        group_d(suite, srv, fresh_mod, roots)
        group_e(suite, srv, reindex_mod, lib, roots)
    finally:
        # Loading reindex.py / freshness.py put their directory on sys.path and
        # _wikilib into sys.modules; no later suite in this process inherits
        # either.
        sys.path[:] = saved_path
        sys.modules.pop("_wikilib", None)
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
