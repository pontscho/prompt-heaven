#!/usr/bin/env python3
"""purity_call's gitignore-aware file handlers: the exemption, its narrowness,
and the search parameter contract.

Two behaviours are pinned here, and they pull in OPPOSITE directions -- which is
the whole reason this suite exists rather than a couple of ad-hoc checks:

  1. `.claude/tmp` is never skipped.  It is gitignored on purpose (it must never
     be committed) but it is also where the agent fleet drops artifacts that the
     very next `search` or `list_dir` goes looking for.  Honouring the ignore
     rule there hands back an empty result for files created seconds earlier.
     `IGNORE_EXEMPT_PATHS` / `_ignore_exempt` in Scripts/mcp-purity.py.

  2. That exemption must stay NARROW.  Purity has no gitignore inheritance of
     its own: an ignored directory is enforced by PRUNING the walk, and
     everything beneath it disappears as a side effect.  The exemption has to
     un-prune the way IN to reach `.claude/tmp` -- and the first version of it
     thereby handed out the un-pruned ancestor's OTHER children too, so a
     wholesale-ignored `.claude` started surfacing `.claude/agents/**` merely
     because `.claude/tmp` was exempt.  `_ignore_inherited` restores the
     inheritance that pruning used to deliver.  **Group B is that defect.**  Its
     case ids say `narrow-` for a reason; if they start failing, the exemption
     has widened again.

Group B carries its own anti-vacuity control (`optout-proves-negatives-exist`):
every path the group asserts is INVISIBLE is re-asserted VISIBLE with
`skip_ignored_files:false`.  Without it, a fixture typo that never created
`leak.txt` would make the whole group pass by accident -- which is exactly how a
must-not-find assertion rots.

Group E is the honest counterweight.  `_is_ignored` is fed a BARE BASENAME at
both search call sites and at `list_dir`'s flat site, so a slash-bearing pattern
such as this repo's own `.claude/tmp` cannot match there at all.  That is a
LIMITATION, not a guarantee, so the rows that merely record it are INFO; the
rows that pin real behaviour in the same fixture (a basename pattern in the same
.gitignore still works; the exempt row appears in a recursive listing) are
gated.  Group E's fixture deliberately carries one pattern of each shape so the
asymmetry is visible in a single .gitignore rather than argued about.

No external binary is involved -- these are the pure-stdlib file handlers, so
there is no skip path and the suite runs everywhere in a couple of seconds.  If
a case here needs clangd, it is in the wrong file (see test_purity_lsp.py).

Fixtures live in a `tempfile.mkdtemp()` workspace and the servers'
`--project-root` points there, never into the repo tree.  Group F asserts that,
plus ZERO `.pyc` anywhere (absolute, not a delta: a file that already existed
reads as "1 before, 1 after" and sails through a delta check).

Groups:
  A  the exemption: `.claude/tmp` is searched, `build/` still is not
  B  the exemption is NARROW -- the inheritance rule (the shipped defect)
  C  list_dir, both branches, with and without skip_ignored_files
  D  the search parameter contract: aliases, tolerated no-ops, real rejections
  E  path-shaped .gitignore: what the basename matcher does and does not honour
  F  hygiene
"""

import os
import re
import sys
import threading

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "purity_file_ops"
SERVER = H.repo_path("Scripts", "mcp-purity.py")

RPC_TIMEOUT = 60.0
NEEDLE = "NEEDLE_ALPHA"
LINE = NEEDLE + " here\n"

# Fixture paths, exactly as the server reports them (root-relative).
P_SCRATCH = ".claude/tmp/scratch.txt"    # inside the exempt subtree
P_LEAK = ".claude/other/leak.txt"        # sibling of the exempt subtree
P_SETTINGS = ".claude/settings.json"     # file directly in the gateway dir
P_KEEP = "src/keep.txt"                  # never ignored
P_GEN = "build/gen.txt"                  # unrelated ignored subtree
ALL_FILES = (P_SCRATCH, P_LEAK, P_SETTINGS, P_KEEP, P_GEN)

# Basename-shaped patterns: what `_is_ignored` actually matches on, since it is
# handed a bare name at the prune sites.  This is the hostile shape.
GITIGNORE_BASENAME = ("tmp", ".claude", "build")

# One pattern of each shape in a single file, so group E can show that the
# slash-bearing one is inert while the bare one still bites.
GITIGNORE_PATHSHAPED = (".claude/tmp", "build")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def make_fixture(ws, subdir, patterns):
    """Write one throwaway project root and return its absolute path."""
    ws.subdir(subdir)
    ws.write_text(os.path.join(subdir, ".gitignore"),
                  "\n".join(patterns) + "\n")
    for rel in ALL_FILES:
        ws.write_text(os.path.join(subdir, rel), LINE)
    return ws.join(subdir)


# ---------------------------------------------------------------------------
# Server driver
# ---------------------------------------------------------------------------

class Driver:
    """One mcp-purity child rooted at a fixture, with stderr drained.

    Draining is cheap insurance rather than a live need: these handlers spawn no
    LSP, so the child is nearly silent -- but `H.JsonRpcClient` hands it a
    stderr PIPE nobody reads, and a full pipe would present as a mystery hang.
    """

    def __init__(self, project_root, timeout=RPC_TIMEOUT):
        self.root = project_root
        self.cli = H.JsonRpcClient(
            [sys.executable, SERVER, "--project-root", project_root],
            tool="purity_call", cwd=H.REPO_ROOT, timeout=timeout,
            client_name="ph-purity-file-ops")
        self._err = []
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            for line in self.cli.proc.stderr:
                self._err.append(line)
        except Exception as exc:
            self._err.append("<stderr drain ended: %r>\n" % (exc,))

    @property
    def stderr_text(self):
        return "".join(self._err)

    def call(self, function, params=None):
        """(is_error, text) -- a dead child becomes readable text, never an
        exception that aborts the whole suite."""
        try:
            return self.cli.call_tool(function, params or {})
        except Exception as exc:
            return True, "DRIVER-ERROR %s: %s" % (type(exc).__name__, exc)

    def close(self):
        try:
            self.cli.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reply parsing -- exact path sets, never substring sniffing
# ---------------------------------------------------------------------------

RX_MATCH_ROW = re.compile(r"^(?P<path>\S.*?):(?P<line>\d+):")


def search_paths(text):
    """Set of file paths in a `content`-mode search reply.

    Rows are `path:line: text`.  Parsed to exact paths on purpose: a substring
    test for `build/gen.txt` would also fire on a row merely MENTIONING it, and
    `.claude/tmp` is a substring of every path beneath it.
    """
    out = set()
    for row in text.splitlines():
        m = RX_MATCH_ROW.match(row.strip())
        if m:
            out.add(m.group("path"))
    return out


def listing_paths(text):
    """Set of entries in a `list_dir` reply, trailing slash stripped.

    The first line is a `[path] N entries` header; a truncation note (if any) is
    bracketed too, so bracketed lines are dropped wholesale.
    """
    out = set()
    for row in text.splitlines():
        row = row.strip()
        if not row or row.startswith("["):
            continue
        out.add(row.rstrip("/"))
    return out


def polarity(text, is_error, must, must_not, extract):
    """Problems for a must-find / must-not-find pair."""
    problems = []
    if is_error:
        problems.append("server returned an error")
    got = extract(text)
    for path in must:
        if path not in got:
            problems.append("MISSING %s (must be found)" % path)
    for path in must_not:
        if path in got:
            problems.append("LEAKED %s (must be skipped)" % path)
    return problems


def record_polarity(suite, group, cid, driver, function, params, must, must_not,
                    extract=search_paths, status=None, detail=()):
    """Call `function`, assert both polarities, record one case."""
    is_error, text = driver.call(function, params)
    problems = polarity(text, is_error, must, must_not, extract)
    detail = list(detail) + [
        "must find    : %s" % (", ".join(must) or "-"),
        "must not find: %s" % (", ".join(must_not) or "-"),
        "reported     : %s" % (", ".join(sorted(extract(text))) or "-"),
    ]
    return suite.record(group, cid, problems, status=status, detail=detail,
                        text=text, showable=True)


def record_error(suite, group, cid, driver, function, params, must_say=(),
                 must_not_say=(), want_error=True, detail=()):
    """Assert on the ERROR TEXT rather than on a path set.

    `want_error=False` means the call must be accepted; a rejection message is
    the failure.  Both directions are used in group D, because "rejects the
    impossible" is only half the contract -- silently rejecting the satisfiable
    is the other half.
    """
    is_error, text = driver.call(function, params)
    low = text.lower()
    problems = []
    if want_error and not is_error:
        problems.append("expected an error, call was accepted")
    if not want_error and is_error:
        problems.append("expected acceptance, got an error")
    for token in must_say:
        if token.lower() not in low:
            problems.append("error text does not mention %r" % token)
    for token in must_not_say:
        if token.lower() in low:
            problems.append("error text must not mention %r" % token)
    return suite.record(group, cid, problems,
                        detail=list(detail) + ["reply: %s" % text.strip()[:300]],
                        text=text, showable=True)


# ---------------------------------------------------------------------------
# Group A -- the exemption
# ---------------------------------------------------------------------------

def group_a(suite, drv):
    record_polarity(
        suite, "A", "search-finds-exempt-and-src", drv, "search",
        {"substring_pattern": NEEDLE},
        must=[P_SCRATCH, P_KEEP], must_not=[P_GEN],
        detail=["the exemption fires, and it does not disable the filter"])
    record_polarity(
        suite, "A", "search-optout-reaches-ignored", drv, "search",
        {"substring_pattern": NEEDLE, "skip_ignored_files": False},
        must=list(ALL_FILES), must_not=[],
        detail=["skip_ignored_files=false must still turn the filter off"])
    record_polarity(
        suite, "A", "search-single-file-in-exempt", drv, "search",
        {"substring_pattern": NEEDLE, "relative_path": P_SCRATCH},
        must=[P_SCRATCH], must_not=[P_KEEP, P_GEN],
        detail=["a single-file search inside the exempt subtree"])
    record_polarity(
        suite, "A", "search-scoped-to-exempt-subtree", drv, "search",
        {"substring_pattern": NEEDLE, "relative_path": ".claude/tmp"},
        must=[P_SCRATCH], must_not=[P_KEEP, P_GEN],
        detail=["descending explicitly into the exempt subtree"])


# ---------------------------------------------------------------------------
# Group B -- the exemption is NARROW.  This group IS the shipped defect.
# ---------------------------------------------------------------------------

def group_b(suite, drv):
    record_polarity(
        suite, "B", "narrow-sibling-of-exempt-ignored", drv, "search",
        {"substring_pattern": NEEDLE},
        must=[P_SCRATCH], must_not=[P_LEAK],
        detail=["`.claude` is un-pruned only as a GATEWAY to `.claude/tmp`;",
                "its other children must stay ignored (_ignore_inherited)"])
    record_polarity(
        suite, "B", "narrow-gateway-dir-file-ignored", drv, "search",
        {"substring_pattern": NEEDLE},
        must=[P_SCRATCH], must_not=[P_SETTINGS],
        detail=["a file sitting DIRECTLY in the un-pruned gateway dir",
                "inherits the ignore too"])
    record_polarity(
        suite, "B", "narrow-unrelated-ignored-tree", drv, "search",
        {"substring_pattern": NEEDLE},
        must=[P_KEEP], must_not=[P_GEN],
        detail=["an ignored subtree unrelated to the exemption is untouched"])
    record_polarity(
        suite, "B", "optout-proves-negatives-exist", drv, "search",
        {"substring_pattern": NEEDLE, "skip_ignored_files": False},
        must=[P_LEAK, P_SETTINGS, P_GEN], must_not=[],
        detail=["ANTI-VACUITY CONTROL: every path this group asserts is",
                "invisible must be reachable with the filter off, or a",
                "fixture typo would make the whole group pass by accident"])
    # Pointing search AT an inherited-ignored dir still honours the ignore.
    # Recorded rather than gated: whether an explicit path should override the
    # filter is a design question nobody has asked, and the opt-out exists.
    is_error, text = drv.call("search", {"substring_pattern": NEEDLE,
                                         "relative_path": ".claude/other"})
    got = sorted(search_paths(text))
    suite.record("B", "explicit-path-into-ignored-dir", (), status=H.INFO,
                 detail=["relative_path=.claude/other with the filter ON",
                         "reported: %s" % (", ".join(got) or "nothing"),
                         "the inherited ignore is honoured even when the "
                         "caller names the directory; skip_ignored_files=false "
                         "is the documented way through"],
                 text=text)


# ---------------------------------------------------------------------------
# Group C -- list_dir, both branches, both settings of skip_ignored_files
# ---------------------------------------------------------------------------

def group_c(suite, drv):
    deep = {"relative_path": ".", "recursive": True, "show_hidden": True}
    flat = {"relative_path": ".", "recursive": False, "show_hidden": True}

    record_polarity(
        suite, "C", "recursive-skipignored-on", drv, "list_dir",
        dict(deep, skip_ignored_files=True),
        must=[P_SCRATCH, ".claude/tmp"],
        must_not=[P_GEN, P_LEAK, P_SETTINGS, ".claude/other", "build"],
        extract=listing_paths,
        detail=["group B's negatives must hold in list_dir too"])
    record_polarity(
        suite, "C", "recursive-skipignored-off", drv, "list_dir",
        dict(deep, skip_ignored_files=False),
        must=list(ALL_FILES) + [".claude/other", "build"], must_not=[],
        extract=listing_paths)
    # list_dir's default is False -- the OPPOSITE of search's True.  Pinned
    # because it is a live trap: a caller who omits the flag gets no filtering,
    # and a test that omits it silently measures nothing.
    record_polarity(
        suite, "C", "recursive-default-is-no-filtering", drv, "list_dir",
        dict(deep),
        must=[P_GEN, P_SETTINGS, P_LEAK], must_not=[],
        extract=listing_paths,
        detail=["skip_ignored_files defaults to FALSE in list_dir",
                "(search defaults it to TRUE -- they genuinely differ)"])
    record_polarity(
        suite, "C", "flat-gateway-dir-listing", drv, "list_dir",
        {"relative_path": ".claude", "recursive": False, "show_hidden": True,
         "skip_ignored_files": True},
        must=[".claude/tmp"], must_not=[".claude/other", P_SETTINGS],
        extract=listing_paths,
        detail=["the flat branch matches patterns on the BARE NAME"])
    record_polarity(
        suite, "C", "flat-root-listing", drv, "list_dir",
        dict(flat, skip_ignored_files=True),
        must=[".claude", "src"], must_not=["build"],
        extract=listing_paths,
        detail=["the gateway dir is listed; the ignored sibling is not"])
    record_polarity(
        suite, "C", "flat-skipignored-off-shows-build", drv, "list_dir",
        dict(flat, skip_ignored_files=False),
        must=[".claude", "src", "build"], must_not=[],
        extract=listing_paths)


# ---------------------------------------------------------------------------
# Group D -- the search parameter contract
# ---------------------------------------------------------------------------

def group_d(suite, drv):
    # `query` is a PER-FUNCTION alias: it cannot be global, because `symbol`
    # takes `query` as its own canonical parameter (see the last case).
    case = record_polarity(
        suite, "D", "query-aliases-substring_pattern", drv, "search",
        {"query": NEEDLE, "path": P_SCRATCH},
        must=[P_SCRATCH], must_not=[P_KEEP, P_GEN],
        detail=["`query` -> substring_pattern, `path` -> relative_path"])
    hits = search_paths(case.text)
    if len(hits) != 1:
        case.problems.append("expected exactly 1 matching path, got %d: %s"
                             % (len(hits), sorted(hits)))

    record_polarity(
        suite, "D", "regex-and-line_numbers-true-ok", drv, "search",
        {"query": NEEDLE, "regex": True, "line_numbers": True},
        must=[P_SCRATCH, P_KEEP], must_not=[P_GEN],
        detail=["both flags are already unconditionally true here, so",
                "accepting them costs nothing and saves a round trip"])
    record_error(
        suite, "D", "regex-false-rejected", drv, "search",
        {"substring_pattern": NEEDLE, "regex": False},
        must_say=["regex", "cannot be false"],
        detail=["the pattern is ALWAYS regex-compiled; silently ignoring",
                "regex=false is how a literal search becomes a regex one"])
    record_error(
        suite, "D", "line_numbers-false-rejected", drv, "search",
        {"substring_pattern": NEEDLE, "line_numbers": False},
        must_say=["line_numbers", "cannot be false", "content mode"])
    record_error(
        suite, "D", "line_numbers-false-ok-in-count", drv, "search",
        {"substring_pattern": NEEDLE, "line_numbers": False,
         "output_mode": "count"},
        want_error=False, must_not_say=["cannot be false"],
        detail=["count mode carries no line numbers, so the flag is",
                "satisfiable rather than impossible"])
    record_error(
        suite, "D", "unknown-param-still-rejected", drv, "search",
        {"substring_pattern": NEEDLE, "definitely_not_a_param": 1},
        must_say=["unknown params", "definitely_not_a_param"],
        detail=["tolerating two named no-ops must not open the gate"])
    # The alias must stay per-function.  Any outcome is fine except `query`
    # being aliased out from under `symbol`, whose own canonical param it is.
    record_error(
        suite, "D", "symbol-keeps-its-own-query", drv, "symbol",
        {"query": NEEDLE}, want_error=False,
        must_not_say=["unknown params for 'symbol'"],
        detail=["an LSP-unavailable or empty answer is acceptable here;",
                "only an Unknown-params rejection of `query` is not"])


# ---------------------------------------------------------------------------
# Group E -- path-shaped .gitignore: limitation (INFO) vs behaviour (gated)
# ---------------------------------------------------------------------------

def group_e(suite, drv):
    is_error, text = drv.call("search", {"substring_pattern": NEEDLE})
    got = search_paths(text)
    missing = [p for p in (P_SCRATCH, P_LEAK, P_SETTINGS) if p not in got]
    suite.record(
        "E", "slash-pattern-inert-for-search", (), status=H.INFO,
        detail=["`.claude/tmp` is in .gitignore, yet search skips nothing "
                "under `.claude`",
                "cause: _is_ignored gets a BARE BASENAME at both search "
                "sites, so a slash-bearing pattern cannot match",
                "reported: %s" % ", ".join(sorted(got)),
                "consistent with the limitation: %s"
                % ("yes" if not missing else "NO -- now skipping %s" % missing)],
        text=text)
    record_polarity(
        suite, "E", "basename-pattern-same-file-works", drv, "search",
        {"substring_pattern": NEEDLE},
        must=[P_KEEP], must_not=[P_GEN],
        detail=["`build` (bare) and `.claude/tmp` (slashed) sit in the SAME",
                ".gitignore: the bare one bites, the slashed one does not"])
    record_polarity(
        suite, "E", "exempt-row-in-recursive-listing", drv, "list_dir",
        {"relative_path": ".", "recursive": True, "show_hidden": True,
         "skip_ignored_files": True},
        must=[".claude/tmp", P_SCRATCH], must_not=[P_GEN, "build"],
        extract=listing_paths,
        detail=["list_dir's RECURSIVE entry check is the one site fed a",
                "root-relative path, so `.claude/tmp` matches there and the",
                "exemption is what keeps the row visible"])
    is_error, text = drv.call("list_dir", {"relative_path": ".claude",
                                           "recursive": False,
                                           "show_hidden": True,
                                           "skip_ignored_files": True})
    rows = listing_paths(text)
    suite.record(
        "E", "flat-listing-slash-pattern-inert", (), status=H.INFO,
        detail=["flat list_dir matches on the bare name, so `.claude/tmp` "
                "is inert here too",
                "reported: %s" % (", ".join(sorted(rows)) or "-"),
                "`.claude/other` present: %s; `.claude/tmp` present: %s"
                % (".claude/other" in rows, ".claude/tmp" in rows)],
        text=text)


# ---------------------------------------------------------------------------
# Group F -- hygiene
# ---------------------------------------------------------------------------

def repo_tree():
    """Repo-relative paths (dirs end in '/'), excluding .git."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(H.REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = os.path.relpath(dirpath, H.REPO_ROOT)
        prefix = "" if rel == "." else rel + "/"
        for name in dirnames:
            out.add(prefix + name + "/")
        for name in filenames:
            out.add(prefix + name)
    return out


def group_f(suite, before, pyc_before, workspaces, stderr_bytes):
    after = repo_tree()
    new = sorted(after - before)
    gone = sorted(before - after)
    suite.record("F", "no-new-repo-paths",
                 [] if not new else
                 ["this suite wrote into the repo tree: %s" % new[:12]],
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    suite.record("F", "no-removed-repo-paths",
                 [] if not gone else ["paths disappeared: %s" % gone[:12]])

    # ZERO .pyc, absolute -- not "the count did not change".  A pre-existing
    # file reads as "1 before, 1 after" and sails through a delta check.
    pyc_after = H.pycache_snapshot()
    problems = []
    if pyc_after:
        created = sorted(set(pyc_after) - set(pyc_before))
        pre = sorted(set(pyc_after) & set(pyc_before))
        if created:
            problems.append("this run wrote bytecode: %s" % created[:6])
        if pre:
            problems.append("pre-existing .pyc a delta check would miss: %s"
                            % pre[:6])
    suite.record("F", "pycache-zero", problems,
                 detail=["%d .pyc before, %d after (contract: zero)"
                         % (len(pyc_before), len(pyc_after))])

    outside = [w for w in workspaces
               if not os.path.realpath(w).startswith(
                   os.path.realpath(H.REPO_ROOT) + os.sep)]
    suite.record("F", "fixtures-outside-repo-tree",
                 [] if len(outside) == len(workspaces) else
                 ["fixture root inside the repo tree: %s"
                  % [w for w in workspaces if w not in outside]],
                 detail=["%d/%d fixture root(s) outside the repo"
                         % (len(outside), len(workspaces))] +
                        ["  %s" % w for w in workspaces])
    suite.record("F", "child-stderr-quiet", (), status=H.INFO,
                 detail=["%d byte(s) drained from the server children's "
                         "stderr (no LSP is involved in these handlers)"
                         % stderr_bytes])


# ---------------------------------------------------------------------------

def run(opts=None):
    """Build the fixtures, drive two server children, return the Suite."""
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="purity_call file handlers: gitignore exemption, "
                          "its narrowness, and the search param contract",
                    opts=opts, mode="stream", group_width=3, cid_width=34)

    before = repo_tree()
    pyc_before = H.pycache_snapshot()
    stderr_bytes = 0

    with H.TempWorkspace("ph-purity-file-ops-", keep=opts.keep) as ws:
        basename_root = make_fixture(ws, "basename", GITIGNORE_BASENAME)
        pathshaped_root = make_fixture(ws, "pathshaped", GITIGNORE_PATHSHAPED)
        suite.note("      server        : %s" % SERVER)
        suite.note("      fixture (A-D) : %s  .gitignore=%s"
                   % (basename_root, list(GITIGNORE_BASENAME)))
        suite.note("      fixture (E)   : %s  .gitignore=%s"
                   % (pathshaped_root, list(GITIGNORE_PATHSHAPED)))

        drv = Driver(basename_root)
        drv_path = Driver(pathshaped_root)
        try:
            group_a(suite, drv)
            group_b(suite, drv)
            group_c(suite, drv)
            group_d(suite, drv)
            group_e(suite, drv_path)
            stderr_bytes = len(drv.stderr_text) + len(drv_path.stderr_text)
        finally:
            drv.close()
            drv_path.close()

        workspaces = [basename_root, pathshaped_root]
        group_f(suite, before, pyc_before, workspaces, stderr_bytes)

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
