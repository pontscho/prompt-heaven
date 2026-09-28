#!/usr/bin/env python3
"""addendum.py, the one legal write to an accepted ADR (A-E).

WHAT IS GATED
-------------
An accepted ADR is append-only (p:wiki schema §2).  The one write it may take
is a dated addendum at its end, and `ClaudeCode/skills/wiki/scripts/addendum.py`
is built so that this write is the only one it CAN do.  This suite drives the
script as a child process -- the way the skill calls it -- against a fake wiki
root inside a mkdtemp sandbox.  The live docs/ is never a target: every page
and every staged item file is a sandbox path, and group E digests docs/ before
and after the run.

GROUPS
------
  A  the append: the exact bytes added, every old byte kept, the one stdout
     line, the file mode and a fresh inode (temp file + os.replace)
  B  the page: only an existing, accepted (`status: active`) `type: adr` page
     under <git top-level>/docs is written; a draft, another type, a missing
     page, a path outside the root and a symlink out of it are refused
  C  the staged item file: exactly `title` + `body`; a title is one line with
     no backtick and no control character; a body carries no level 1-2
     heading (ATX or setext), but `###` and deeper are fine
  D  idempotency (the same heading twice is refused) and the date
  E  hygiene: the live docs/, the repo tree, bytecode, the sandbox, and the
     script's imports

Every refusal is held to one contract: exit 2, one stderr line, empty stdout,
the page byte-identical, and no temp file left beside it.

Each child runs with GIT_CEILING_DIRECTORIES at the sandbox's parent, so the
git top-level lookup can never climb out of the sandbox into a real repo.
The case count lives only in the SUITES table of tests/run.py.
"""

import ast
import datetime
import json
import os
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "wiki_addendum"
SCRIPT = H.repo_path("ClaudeCode", "skills", "wiki", "scripts", "addendum.py")
LIVE_DOCS = H.repo_path("docs")

GA = "A. the append: shape and byte preservation"
GB = "B. the page: an accepted ADR inside the wiki root"
GC = "C. the staged item file"
GD = "D. idempotency and the date"
GE = "E. hygiene"

TODAY = "2026-09-28"
PAGE_REL = "docs/adr/0001-fixture-decision.md"
TMP_PREFIX = ".addendum-"


def _d(label, value):
    return "%-12s: %s" % (label, value)


def page_text(typ="adr", status="active", tail="Last line of the decision.\n"):
    return ("---\nname: 0001-fixture-decision\ntype: %s\nstatus: %s\n"
            "title: Fixture decision\ndescription: A fixture ADR.\n---\n\n"
            "# Fixture decision\n\n**Status:** accepted.\n\n%s"
            % (typ, status, tail))


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class Case:
    """One fresh sandbox per case: <work>/<label>/docs/adr/... plus a staged
    item file.  Nothing is shared between cases, so no case sees another's
    append."""

    def __init__(self, work, label):
        self.root = os.path.realpath(work.subdir(label))
        self.docs = os.path.join(self.root, "docs")
        os.makedirs(os.path.join(self.docs, "adr"))
        self.item_path = os.path.join(self.root, "stage.json")

    def page(self, rel=PAGE_REL, text=None, raw=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw if raw is not None
                     else (text if text is not None else page_text()).encode("utf-8"))
        return path

    def item(self, obj=None, raw=None):
        with open(self.item_path, "w", encoding="utf-8") as fh:
            fh.write(raw if raw is not None else json.dumps(obj))
        return self.item_path

    def run(self, page=PAGE_REL, item=None, today=TODAY):
        argv = [sys.executable, "-B", SCRIPT, "--page", page,
                "--item-file", item or self.item_path]
        if today is not None:
            argv += ["--today", today]
        env = H.child_env({"GIT_CEILING_DIRECTORIES":
                           os.path.dirname(self.root)})
        proc = subprocess.run(argv, input="", capture_output=True, text=True,
                              timeout=30, cwd=self.root, env=env)
        return proc.returncode, proc.stdout, proc.stderr


def leftover_problems(directory):
    if not os.path.isdir(directory):
        return []
    left = [n for n in os.listdir(directory) if n.startswith(TMP_PREFIX)]
    return ["temp file(s) left in %s: %r" % (directory, left)] if left else []


def refusal_problems(result, path=None, before=None, want=None):
    """The one refusal contract: exit 2, one stderr line, nothing on stdout,
    the page untouched, no temp file beside it."""
    rc, out, err = result
    problems = []
    if rc != 2:
        problems.append("exit %d, want 2" % rc)
    if out:
        problems.append("stdout not empty: %r" % out[:200])
    lines = err.splitlines()
    if len(lines) != 1:
        problems.append("stderr has %d line(s), want 1: %r" % (len(lines), err[:300]))
    if want and want not in err:
        problems.append("stderr does not mention %r: %r" % (want, err[:300]))
    if path is not None and before is not None:
        after = _read_bytes(path) if os.path.isfile(path) else None
        if after != before:
            problems.append("the page changed on a refusal")
    if path is not None:
        problems += leftover_problems(os.path.dirname(path))
    return problems


def success_problems(result, want_out=PAGE_REL + "\n"):
    rc, out, err = result
    problems = []
    if rc != 0:
        problems.append("exit %d: %s" % (rc, err.strip()[:300]))
    if out != want_out:
        problems.append("stdout %r, want %r" % (out, want_out))
    if err:
        problems.append("stderr not empty: %r" % err[:200])
    return problems


def heading(date, title):
    return "## Addendum (%s): %s" % (date, title)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def group_a(suite, work):
    title, body = "the gap is closed", "The body.\nSecond line."
    c = Case(work, "a-exact")
    path = c.page()
    os.chmod(path, 0o640)
    before = _read_bytes(path)
    ino_before = os.stat(path).st_ino
    c.item({"title": title, "body": body})
    result = c.run()
    after = _read_bytes(path)
    want = before + ("\n%s\n\nThe body.\nSecond line.\n" % heading(TODAY, title)).encode()

    suite.record(GA, "exit-0-stdout-is-the-page-path", success_problems(result),
                 detail=[_d("stdout", repr(result[1]))])
    suite.record(GA, "old-bytes-byte-identical",
                 [] if after[:len(before)] == before
                 else ["the first %d byte(s) changed" % len(before)],
                 detail=[_d("bytes", "before %d, after %d" % (len(before), len(after)))])
    suite.record(GA, "appended-shape-exact",
                 [] if after == want else ["appended %r, want %r"
                                           % (after[len(before):], want[len(before):])],
                 detail=[_d("rule", "one blank line, the heading, one blank "
                                    "line, the body, exactly one final newline")])
    fm_b = before.split(b"\n---\n", 1)[0]
    suite.record(GA, "frontmatter-untouched",
                 [] if after.startswith(fm_b + b"\n---\n") else ["frontmatter changed"])
    mode = stat.S_IMODE(os.stat(path).st_mode)
    suite.record(GA, "file-mode-preserved",
                 [] if mode == 0o640 else ["mode %o, want 640" % mode])
    suite.record(GA, "replaced-not-rewritten-in-place",
                 [] if os.stat(path).st_ino != ino_before
                 else ["same inode: the file was written in place, not os.replace'd"],
                 detail=[_d("why", "temp file in the same dir + os.replace")])
    suite.record(GA, "no-temp-file-left", leftover_problems(os.path.dirname(path)))

    # A page whose last line has no newline: the missing one is supplied.
    c = Case(work, "a-no-newline")
    path = c.page(text=page_text(tail="Last line, no newline."))
    before = _read_bytes(path)
    c.item({"title": "t", "body": "b"})
    problems = success_problems(c.run())
    after = _read_bytes(path)
    want = before + ("\n\n%s\n\nb\n" % heading(TODAY, "t")).encode()
    if after != want:
        problems.append("appended %r, want %r" % (after[len(before):], want[len(before):]))
    suite.record(GA, "page-without-final-newline", problems)

    # A page that already ends in a blank line: no second one is added.
    c = Case(work, "a-blank-tail")
    path = c.page(text=page_text(tail="Last line.\n\n"))
    before = _read_bytes(path)
    c.item({"title": "t", "body": "b"})
    problems = success_problems(c.run())
    after = _read_bytes(path)
    want = before + ("%s\n\nb\n" % heading(TODAY, "t")).encode()
    if after != want:
        problems.append("appended %r, want %r" % (after[len(before):], want[len(before):]))
    suite.record(GA, "page-already-ending-in-a-blank-line", problems)

    # ### and deeper are subsections of the addendum, not siblings of it.
    c = Case(work, "a-h3")
    path = c.page()
    before = _read_bytes(path)
    body = "Intro.\n\n### Detail\n\nText.\n\n#### Deeper\n\n#hashtag is not a heading."
    c.item({"title": "t", "body": body})
    problems = success_problems(c.run())
    if _read_bytes(path) != before + ("\n%s\n\n%s\n" % (heading(TODAY, "t"), body)).encode():
        problems.append("the ###/#### body was not appended verbatim")
    suite.record(GA, "h3-and-deeper-accepted", problems)

    # Blank lines at the body's edges are dropped, the inside is kept.
    c = Case(work, "a-edges")
    path = c.page()
    before = _read_bytes(path)
    c.item({"title": "t", "body": "\n\n  indented first\n\nlast\n\n\n"})
    problems = success_problems(c.run())
    want = before + ("\n%s\n\n  indented first\n\nlast\n" % heading(TODAY, "t")).encode()
    if _read_bytes(path) != want:
        problems.append("appended %r" % _read_bytes(path)[len(before):])
    suite.record(GA, "body-blank-edges-dropped", problems)

    # An absolute --page is accepted; stdout is still the repo-relative path.
    c = Case(work, "a-absolute")
    path = c.page()
    c.item({"title": "t", "body": "b"})
    suite.record(GA, "absolute-page-path", success_problems(c.run(page=path)))


def group_b(suite, work):
    def refused(label, cid, rel=PAGE_REL, text=None, raw=None, want=None,
                setup=None):
        c = Case(work, label)
        path = c.page(rel=rel, text=text, raw=raw) if (text or raw is not None
                                                        or setup is None) else None
        if setup is not None:
            path = setup(c)
        before = _read_bytes(path) if path and os.path.isfile(path) else None
        c.item({"title": "t", "body": "b"})
        result = c.run(page=rel)
        suite.record(GB, cid, refusal_problems(result, path, before, want),
                     detail=[_d("stderr", result[2].strip()[:160])])

    refused("b-draft", "draft-adr-refused", text=page_text(status="draft"),
            want="draft")
    refused("b-deprecated", "deprecated-adr-refused",
            text=page_text(status="deprecated"))
    refused("b-concept", "non-adr-refused", text=page_text(typ="concept"))
    refused("b-nofm", "no-frontmatter-refused",
            text="# Just a heading\n\nNo frontmatter.\n")
    refused("b-binary", "non-utf8-page-refused",
            raw=page_text().encode("utf-8") + b"\xff\xfe\n")

    refused("b-missing", "missing-page-refused",
            setup=lambda c: os.path.join(c.root, PAGE_REL + ".nope"),
            rel=PAGE_REL + ".nope")
    refused("b-dir", "directory-refused",
            setup=lambda c: (os.makedirs(os.path.join(c.root, "docs/adr/dir.md")),
                             os.path.join(c.root, "docs/adr/dir.md"))[1],
            rel="docs/adr/dir.md")
    refused("b-outside", "page-outside-the-wiki-root-refused",
            setup=lambda c: c.page(rel="outside/0001-fixture-decision.md"),
            rel="outside/0001-fixture-decision.md")
    refused("b-dotdot", "dotdot-out-of-the-wiki-root-refused",
            setup=lambda c: c.page(rel="outside/0001-fixture-decision.md"),
            rel="docs/adr/../../outside/0001-fixture-decision.md")

    def file_link(c):
        target = c.page(rel="outside/0001-fixture-decision.md")
        os.symlink(target, os.path.join(c.docs, "adr", "link.md"))
        return target
    refused("b-filelink", "symlinked-page-escaping-refused", setup=file_link,
            rel="docs/adr/link.md")

    def dir_link(c):
        target = c.page(rel="outside/0001-fixture-decision.md")
        os.symlink(os.path.dirname(target), os.path.join(c.docs, "linked"))
        return target
    refused("b-dirlink", "symlinked-directory-escaping-refused", setup=dir_link,
            rel="docs/linked/0001-fixture-decision.md")

    def no_root(c):
        os.rmdir(os.path.join(c.docs, "adr"))
        os.rmdir(c.docs)
        return c.page(rel="wiki/adr/0001-fixture-decision.md")
    refused("b-noroot", "no-wiki-root-refused", setup=no_root,
            rel="wiki/adr/0001-fixture-decision.md")


TITLE_BAD = (
    ("title-empty-refused", "   "),
    ("title-newline-refused", "one\ntwo"),
    ("title-carriage-return-refused", "one\rtwo"),
    ("title-backtick-refused", "the `x` gap"),
    ("title-tab-refused", "a\tb"),
    ("title-line-separator-refused", "a" + chr(0x2028) + "b"),
    ("title-not-a-string-refused", 7),
)
BODY_BAD = (
    ("body-empty-refused", "\n  \n"),
    ("body-h1-refused", "Text.\n\n# Forged sibling"),
    ("body-h2-refused", "Text.\n\n## Forged sibling"),
    ("body-bare-h2-refused", "Text.\n##"),
    ("body-indented-h2-refused", "Text.\n\n   ## Forged sibling"),
    ("body-setext-h2-refused", "Forged sibling\n---"),
    ("body-setext-h1-refused", "Forged sibling\n==="),
    ("body-control-char-refused", "a\x07b"),
    ("body-carriage-return-refused", "a\r\nb"),
    ("body-not-a-string-refused", ["b"]),
)
RAW_BAD = (
    ("item-not-json-refused", "{title: nope"),
    ("item-not-an-object-refused", '["t", "b"]'),
    ("item-unknown-key-refused", '{"title": "t", "body": "b", "why": "x"}'),
    ("item-missing-body-refused", '{"title": "t"}'),
    ("item-duplicate-key-refused", '{"title": "t", "title": "u", "body": "b"}'),
)


def group_c(suite, work):
    def one(cid, obj=None, raw=None, item=None):
        c = Case(work, "c-" + cid)
        path = c.page()
        before = _read_bytes(path)
        if item is None:
            c.item(obj, raw)
        result = c.run(item=item)
        suite.record(GC, cid, refusal_problems(result, path, before),
                     detail=[_d("stderr", result[2].strip()[:160])])

    for cid, title in TITLE_BAD:
        one(cid, {"title": title, "body": "b"})
    for cid, body in BODY_BAD:
        one(cid, {"title": "t", "body": body})
    for cid, raw in RAW_BAD:
        one(cid, raw=raw)
    one("item-file-missing-refused", item="no-such-stage.json")


def group_d(suite, work):
    c = Case(work, "d-dup")
    path = c.page()
    c.item({"title": "t", "body": "b"})
    first = c.run()
    once = _read_bytes(path)
    problems = success_problems(first)
    problems += refusal_problems(c.run(), path, once)
    suite.record(GD, "same-date-and-title-refused", problems,
                 detail=[_d("why", "a double run must not append twice")])

    c.item({"title": "t", "body": "other"})
    problems = success_problems(c.run(today="2026-09-29"))
    text = _read_bytes(path).decode("utf-8")
    for want in (heading(TODAY, "t"), heading("2026-09-29", "t")):
        if text.splitlines().count(want) != 1:
            problems.append("%r appears %d time(s)" % (want, text.splitlines().count(want)))
    suite.record(GD, "same-title-new-date-accepted", problems)

    c = Case(work, "d-default")
    path = c.page()
    c.item({"title": "t", "body": "b"})
    days = {datetime.date.today().isoformat()}
    problems = success_problems(c.run(today=None))
    days.add(datetime.date.today().isoformat())
    lines = _read_bytes(path).decode("utf-8").splitlines()
    if not any(heading(d, "t") in lines for d in days):
        problems.append("no heading dated %s" % sorted(days))
    suite.record(GD, "default-date-is-today", problems)

    for cid, bad in (("today-not-iso-refused", "2026-9-28"),
                     ("today-impossible-date-refused", "2026-02-30")):
        c = Case(work, "d-" + cid)
        path = c.page()
        before = _read_bytes(path)
        c.item({"title": "t", "body": "b"})
        result = c.run(today=bad)
        suite.record(GD, cid, refusal_problems(result, path, before),
                     detail=[_d("stderr", result[2].strip()[:160])])


def _tree_digests(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = H.sha256_file(p)
    return out


def import_problems():
    """stdlib + the sibling _wikilib only, and no second frontmatter parser."""
    if not os.path.isfile(SCRIPT):
        return ["%s does not exist" % SCRIPT]
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    stdlib = getattr(sys, "stdlib_module_names", None)
    problems = []
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.FunctionDef) and "frontmatter" in node.name:
            problems.append("defines %s -- reuse _wikilib's parser" % node.name)
    if "_wikilib" not in names:
        problems.append("does not import _wikilib")
    if stdlib is not None:
        extra = sorted(n for n in names - {"_wikilib", "__future__"} if n not in stdlib)
        if extra:
            problems.append("non-stdlib import(s): %r" % extra)
    return problems


def group_e(suite, pyc_before, tree_before, live_before, work_path):
    live_after = _tree_digests(LIVE_DOCS)
    changed = sorted(k for k in set(live_before) | set(live_after)
                     if live_before.get(k) != live_after.get(k))
    suite.record(GE, "live-docs-unchanged",
                 ["docs/ changed: %r" % changed[:5]] if changed else [],
                 detail=[_d("files", len(live_before))])

    added = sorted(H.repo_tree() - tree_before)
    suite.record(GE, "no-new-repo-paths",
                 ["%d new path(s): %s" % (len(added), added[:5])] if added else [])

    pyc_after = H.pycache_snapshot()
    new = sorted(set(pyc_after) - set(pyc_before))
    touched = sorted(k for k in set(pyc_after) & set(pyc_before)
                     if pyc_after[k] != pyc_before[k])
    suite.record(GE, "no-pycache-written",
                 ["new=%r touched=%r" % (new, touched)] if (new or touched) else [])

    real, repo = os.path.realpath(work_path), os.path.realpath(H.REPO_ROOT)
    suite.record(GE, "workspace-outside-repo",
                 ["the workspace %s is inside the repo" % work_path]
                 if real == repo or real.startswith(repo + os.sep) else [],
                 detail=[_d("workspace", work_path)])

    suite.record(GE, "stdlib-and-wikilib-only", import_problems(),
                 detail=[_d("scanned", "addendum.py (ast)")])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="addendum.py: the one legal write to an accepted ADR",
                    opts=opts, mode="grouped")
    tree_before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    live_before = _tree_digests(LIVE_DOCS)
    work = H.TempWorkspace("ph-wiki-addendum-", keep=opts.keep)
    try:
        group_a(suite, work)
        group_b(suite, work)
        group_c(suite, work)
        group_d(suite, work)
    finally:
        work.cleanup()
        group_e(suite, pyc_before, tree_before, live_before, work.path)

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
