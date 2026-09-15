#!/usr/bin/env python3
"""purity_call's gitignore-aware file handlers: the exemption, its narrowness,
and the parameter contract.

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

Group G is the one group that does not speak JSON-RPC.  It imports the server
module and calls `_glob_matches` plus the three glob-accepting handlers
DIRECTLY, because what it gates is invisible from the wire: a glob that matches
nothing and a glob that was REFUSED render to the same empty reply, and the
embedded-`**/` question is decided inside one private helper that no parameter
can reach on its own.  Purity has THREE independent glob engines -- `_glob_
matches` (fnmatch plus a leading-`**/` special case, used only by search),
`_compile_path_glob` (a real globstar regex, used only by find_file) and raw
`fnmatch` on the bare name inside list_dir -- so "the glob semantics" is three
answers, not one, and a spelling is only safe when all three agree about it.
The group carries its own must-stay-green controls (a leading `**/`, a
path-scoped glob that must STAY scoped, a bare filename, a brace group with no
comma), because every rule it asserts is a WIDENING, and over-reach is the
characteristic failure of a widening.

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
  D  the parameter contract: aliases (function and param, global and
     per-function), the inverted `no_ignore` spelling, tolerated no-ops,
     real rejections
  E  path-shaped .gitignore: what the basename matcher does and does not honour
  F  hygiene
  G  glob semantics -- the spellings that can only ever match nothing
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

# Group G's tree.  No .gitignore at all: the glob question and the ignore
# question are separate, and mixing them would let a pruned directory pass for a
# glob that matched nothing.  Two depths exist so a `**/` mask has to reach BOTH
# (`root.py` and `src/deep/nested.py`); `a.py` and `src/a.py` exist so the
# character-class divergence can be MEASURED rather than argued about.
GLOB_FILES = ("root.py", "a.py", "auth.py",
              "src/a.py", "src/deep/nested.py", "tests/foo.py")
G_ROOT_PY = "root.py"
G_NESTED_PY = "src/deep/nested.py"


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


def make_glob_fixture(ws, subdir):
    """Group G's tree, returned REALPATH'd like the server resolves its own root.

    `McpServer.__init__` does `os.path.realpath(project_root)`
    (Scripts/mcp-purity.py:6002), and the handlers rely on that: `handle_find_
    file` / `handle_list_dir` send the search root through `safe_path`, which
    realpaths, then report each hit as `os.path.relpath(full, project_root)`.
    Hand them the UNRESOLVED mkdtemp path and on macOS -- where /var is a
    symlink to /private/var -- every reported path comes back prefixed with six
    `../`, which is not a bug in the handler, only in how it was called.
    """
    ws.subdir(subdir)
    for rel in GLOB_FILES:
        ws.write_text(os.path.join(subdir, rel), LINE)
    return os.path.realpath(ws.join(subdir))


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


RX_FOUND_HEADER = re.compile(r"^Found \d+ file\(s\)")


def found_paths(text):
    """Set of paths in a `find_file` reply.

    The reply is a `Found N file(s) matching '<mask>'` header followed by one
    path per row; a truncation note, if any, is bracketed.  The header is
    dropped by shape rather than by position, because a zero-hit reply is the
    header ALONE and an off-by-one would read it as a path.
    """
    out = set()
    for row in text.splitlines():
        row = row.strip()
        if not row or row.startswith("[") or RX_FOUND_HEADER.match(row):
            continue
        out.add(row)
    return out


def handler_text(reply):
    """The text of a handler's return value, called in-process (group G).

    The handlers answer with one of two keys -- `__raw_text__` for a
    pre-rendered body, `text` for a plain one -- and which is used is an
    internal detail of each handler, not part of what this suite gates.
    """
    return reply.get("__raw_text__") or reply.get("text") or ""


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


def record_glob(suite, gm, cid, rel_path, glob, want, detail=()):
    """One `_glob_matches(rel_path, glob)` row -- a UNIT row, on purpose.

    Routing these through a fixture would mix the glob answer with gitignore
    pruning, the walk order and the binary/size skips, and the thing under test
    here is a pure function of two strings.
    """
    try:
        got = gm(rel_path, glob)
        raised = None
    except Exception as exc:                                     # noqa: BLE001
        got, raised = None, "%s: %s" % (type(exc).__name__, exc)
    problems = []
    if raised:
        problems.append("_glob_matches(%r, %r) raised %s"
                        % (rel_path, glob, raised))
    elif got is not want:
        problems.append("_glob_matches(%r, %r) is %r, want %r"
                        % (rel_path, glob, got, want))
    return suite.record("G", cid, problems,
                        detail=list(detail) + [
                            "_glob_matches(%r, %r) -> %s (want %s)"
                            % (rel_path, glob, got, want)])


def record_refusal(suite, cid, handler, params, root, must_say=("brace",),
                   want_error=True, detail=()):
    """Call a handler IN-PROCESS and assert on the EXCEPTION, not on a reply.

    `record_error` above cannot serve here.  Over the wire a refusal and a glob
    that simply matched nothing are both a short, unremarkable reply -- and the
    whole point of the rule being gated is that today they are the SAME reply,
    so a wire-level assertion would be asserting the defect.  The TYPE is
    checked as well as the message because `except ValueError` is what the
    dispatcher's error envelope is written against, and the MESSAGE because a
    refusal whose text does not name what it refused just moves the silence one
    layer out: the caller still has to guess which of its globs was the problem.
    """
    try:
        text = handler_text(handler(params, root))
        raised = None
    except Exception as exc:                                     # noqa: BLE001
        raised, text = exc, "%s: %s" % (type(exc).__name__, exc)
    problems = []
    if want_error:
        if raised is None:
            problems.append("expected ValueError, call was accepted -> %s"
                            % " | ".join(text.splitlines())[:160])
        elif not isinstance(raised, ValueError):
            problems.append("expected ValueError, raised %s: %s"
                            % (type(raised).__name__, raised))
        else:
            low = str(raised).lower()
            for token in must_say:
                if token.lower() not in low:
                    problems.append("refusal does not mention %r" % token)
    elif raised is not None:
        problems.append("expected acceptance, raised %s: %s"
                        % (type(raised).__name__, raised))
    return suite.record("G", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "outcome: %s" % " | ".join(text.splitlines())[:200]],
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
    # Same flag, same handler, opposite default: `no_ignore=false` has to turn
    # filtering ON here, where the row above proves it is OFF by default.  A
    # `no_ignore` implemented by leaning on either handler's default -- rather
    # than by inverting the value -- passes in group D and fails right here.
    record_polarity(
        suite, "C", "no_ignore-false-makes-list_dir-skip", drv, "list_dir",
        dict(deep, no_ignore=False),
        must=[P_SCRATCH, ".claude/tmp"],
        must_not=[P_GEN, P_LEAK, P_SETTINGS, ".claude/other", "build"],
        extract=listing_paths,
        detail=["no_ignore=false == skip_ignored_files=true, so the ignored",
                "sibling a BARE list_dir shows (row above) disappears"])
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
# Group D -- the parameter contract
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

    # Same shape as the pair above, in the other direction: `pattern` is GLOBAL
    # (-> substring_pattern), and list_dir does not accept that param at all, so
    # the global row could only ever produce a rejection there.  The
    # per-function row aims it at the fnmatch name filter instead.  A filter
    # that were merely TOLERATED and dropped leaves the `.txt` negatives below
    # red, so this cannot pass on acceptance alone.
    record_polarity(
        suite, "D", "pattern-aliases-filter-in-list_dir", drv, "list_dir",
        {"pattern": "*.json", "relative_path": ".", "recursive": True,
         "show_hidden": True},
        must=[P_SETTINGS], must_not=[P_SCRATCH, P_KEEP, P_GEN, P_LEAK],
        extract=listing_paths,
        detail=["`pattern` -> filter (fnmatch on the bare name, files only)"])
    # ... and the override must stay per-function: moving it into the global
    # table instead is the tempting one-line fix, and it turns `pattern` into an
    # unknown param for every search.
    record_polarity(
        suite, "D", "pattern-stays-global-for-search", drv, "search",
        {"pattern": NEEDLE},
        must=[P_SCRATCH, P_KEEP], must_not=[P_GEN],
        detail=["`pattern` -> substring_pattern everywhere except list_dir"])

    # One case, both halves of the FUNCTION alias -- and it takes both to be a
    # real test.  Dispatch happens on the RAW name, so a reply at all proves the
    # `HANDLERS["search_content"]` entry; but `query` is a PER-FUNCTION param
    # alias resolved under the CANONICAL name, so with the
    # `FUNCTION_ALIASES["search_content"]` row missing, `query` would never be
    # recognised and this same call would come back as an unknown-param
    # rejection.  Either half absent turns this row red.
    record_polarity(
        suite, "D", "search_content-dispatches", drv, "search_content",
        {"query": NEEDLE, "path": P_SCRATCH},
        must=[P_SCRATCH], must_not=[P_KEEP, P_GEN],
        detail=["Serena's spelling reaches handle_search_for_pattern",
                "HANDLERS row  -> dispatch on the RAW name",
                "FUNCTION_ALIASES row -> `query`/`path` resolve under the",
                "CANONICAL name, so its absence reads as unknown-param"])

    # `no_ignore` is ripgrep's spelling and the INVERSE of skip_ignored_files,
    # so it cannot be a PARAM_ALIASES row (that layer renames keys and never
    # touches values).  Both directions are pinned: an inversion that is dropped
    # and an inversion applied twice both leave one of these two rows red.
    record_polarity(
        suite, "D", "no_ignore-true-reaches-ignored", drv, "search",
        {"substring_pattern": NEEDLE, "no_ignore": True},
        must=list(ALL_FILES), must_not=[],
        detail=["no_ignore=true == skip_ignored_files=false: `build/gen.txt`",
                "is skipped by the default search and reachable here"])
    record_polarity(
        suite, "D", "no_ignore-false-skips-ignored", drv, "search",
        {"substring_pattern": NEEDLE, "no_ignore": False},
        must=[P_SCRATCH, P_KEEP], must_not=[P_GEN, P_LEAK, P_SETTINGS],
        detail=["no_ignore=false == skip_ignored_files=true, which is also",
                "search's default -- the sign of the flip is what is pinned"])
    record_error(
        suite, "D", "no_ignore-contradiction-rejected", drv, "search",
        {"substring_pattern": NEEDLE, "skip_ignored_files": True,
         "no_ignore": True},
        must_say=["contradict"],
        detail=["both spellings with opposite meanings: either answer",
                "disobeys half the request, so neither is guessed"])
    record_error(
        suite, "D", "no_ignore-agreement-tolerated", drv, "search",
        {"substring_pattern": NEEDLE, "skip_ignored_files": False,
         "no_ignore": True},
        want_error=False, must_not_say=["contradict"],
        detail=["the two agree (both mean 'do not skip'), and rejecting a",
                "request that IS satisfiable would be gratuitous"])


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
# Group G -- glob semantics: the spellings that can only ever match nothing
#
# Sits above group F because source order is CALL order in this file, and
# hygiene has to be the last thing that runs: it snapshots the repo tree after
# every other group has had its chance to dirty it.
# ---------------------------------------------------------------------------

def group_g(suite, root):
    """Two silent-zero spellings, gated across all three of purity's globbers.

    Neither spelling is exotic and neither is a typo: both are what a model
    writes when it has been taught shell/ripgrep globs, and both come back as a
    clean, confident, EMPTY answer -- the one failure mode a caller cannot
    distinguish from "there is nothing there".

      (a) an embedded `**/`.  `fnmatch.translate("tests/**/*.py")` is
          `(?s:tests/(?>.*?/).*\\.py)\\z`: the atomic group DEMANDS a separator,
          so the spelling silently means "at least one directory deep" and
          `tests/foo.py` is missed.  `_glob_matches` strips a LEADING `**/`
          only (mcp-purity.py:1403-1404), so the fix is there and find_file's
          regex engine is already immune.

      (b) brace alternation.  `fnmatch.translate("*.{js,py}")` is
          `(?s:.*\\.\\{js,py\\})\\z` -- the braces are ESCAPED TO LITERALS, so
          the pattern can only match a file literally named `x.{js,py}`.  All
          THREE engines are affected, which is why the answer is a refusal
          rather than three separate implementations of brace expansion: one
          rule, stated once, that cannot leave the three disagreeing.  A brace
          group with NO comma is not an alternation and is not refused.
    """
    mod = H.load_module_from_path("mcp_purity_globs", SERVER)
    gm = mod._glob_matches

    # -- (a) the embedded globstar --------------------------------------------
    record_glob(
        suite, gm, "globstar-embedded-zero-dirs",
        "tests/foo.py", "tests/**/*.py", True,
        detail=["`**/` must mean 'zero or more directories' wherever it",
                "appears, not only at the start of the pattern"])
    record_glob(
        suite, gm, "globstar-embedded-one-or-more-dirs",
        "src/a/b/x.c", "src/**/*.c", True,
        detail=["the >=1-dir half of the SAME spelling: it already passes,",
                "and it is here so a fix cannot trade one half for the other"])

    # -- (a) must-stay-green: the widening must not over-reach -----------------
    record_glob(
        suite, gm, "globstar-leading-zero-dirs",
        "foo.py", "**/*.py", True,
        detail=["CONTROL: the leading-`**/` case the helper already special-",
                "cases (mcp-purity.py:1403-1404) must survive the general rule"])
    record_glob(
        suite, gm, "globstar-leading-nested",
        "a/b/foo.py", "**/*.py", True,
        detail=["CONTROL: and its nested half"])
    record_glob(
        suite, gm, "scoped-glob-misses-sibling-dir",
        "other/foo.py", "tests/**/*.py", False,
        detail=["CONTROL: a path-scoped glob STAYS scoped -- the basename",
                "of a file in another directory must not spuriously match"])
    record_glob(
        suite, gm, "scoped-glob-misses-nested-prefix",
        "a/tests/foo.py", "tests/**/*.py", False,
        detail=["CONTROL: the scope is anchored at the project root, so a",
                "`tests/` segment appearing further down is not a match"])
    record_glob(
        suite, gm, "bare-filename-hits-at-any-depth",
        "a/b/requirements.yaml", "requirements.yaml", True,
        detail=["CONTROL: the basename fallthrough -- the whole reason this",
                "helper exists instead of a bare fnmatch call"])

    # -- (b) the brace alternation, refused at all three entry points ----------
    record_refusal(
        suite, "brace-refused-search-include-glob",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_include_glob": "*.{js,py}"}, root,
        detail=["an include glob that can only ever match a file literally",
                "named `x.{js,py}` narrows the search to nothing, and the",
                "caller reads that as 'the needle is not in any .js or .py'"])
    record_refusal(
        suite, "brace-refused-search-exclude-glob",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_exclude_glob": "*.{md,txt}"}, root,
        detail=["the exclude side fails in the OPPOSITE direction -- it",
                "excludes nothing and the caller is handed the noise it",
                "asked to drop, which is why it needs its own row"])
    record_refusal(
        suite, "brace-refused-find_file-mask",
        mod.handle_find_file,
        {"file_mask": "**/*auth*.{js,py,php}"}, root,
        detail=["this exact mask is taught by a shipped skill example, so it",
                "is not a hypothetical spelling -- it is one already in the",
                "corpus, returning `Found 0 file(s)` every time it is run"])
    record_refusal(
        suite, "brace-refused-list_dir-filter",
        mod.handle_list_dir,
        {"filter": "*.{js,py}", "relative_path": ".", "recursive": True}, root,
        detail=["list_dir is the worst of the three: `_accept_name` applies",
                "the filter to FILES only, so a brace mask returns a listing",
                "of pure directories and looks like a populated answer"])

    # -- (b) must-stay-green: the rejector must not become a `{` ban -----------
    record_refusal(
        suite, "brace-without-comma-not-refused",
        mod.handle_find_file,
        {"file_mask": "{{cookiecutter}}*.py"}, root, want_error=False,
        detail=["CONTROL: a brace group with no comma is not an alternation,",
                "it is a literal filename (template scaffolding ships these",
                "by the thousand).  Refusing it would break callers who are",
                "asking for exactly what fnmatch already gives them"])

    # -- find_file's own globstar engine, which is NOT the one being widened ---
    reply = handler_text(mod.handle_find_file({"file_mask": "**/*.py"}, root))
    got = found_paths(reply)
    missing = [p for p in (G_ROOT_PY, G_NESTED_PY) if p not in got]
    suite.record(
        "G", "find_file-globstar-root-and-nested",
        [] if not missing else ["MISSING %s (must be found)" % ", ".join(missing)],
        detail=["CONTROL: `_compile_path_glob` emits `(?:.*/)?` for `**/`",
                "(mcp-purity.py:1119) and is already immune to spelling (a);",
                "it must stay immune, at BOTH depths",
                "must find: %s" % ", ".join((G_ROOT_PY, G_NESTED_PY)),
                "reported : %s" % (", ".join(sorted(got)) or "-")],
        text=reply, showable=True)

    # A divergence recorded rather than fixed, so it outlives the conversation
    # that measured it.  `_compile_path_glob` walks the mask character by
    # character and sends everything it does not recognise through `re.escape`
    # (Scripts/mcp-purity.py:1127), so an fnmatch character class becomes a
    # LITERAL `[ab]` in the path-style branch -- while the bare-mask branch
    # (Scripts/mcp-purity.py:1159) hands the mask to fnmatch and honours it.
    # One handler, two answers, and the only thing deciding which is whether the
    # mask happens to carry a `/`.  INFO, not FAIL: nobody has asked for classes
    # in a path mask, and inventing a second answer now would be a fix in search
    # of a caller.
    bare = found_paths(handler_text(
        mod.handle_find_file({"file_mask": "[ab].py"}, root)))
    scoped = found_paths(handler_text(
        mod.handle_find_file({"file_mask": "src/[ab].py"}, root)))
    suite.record(
        "G", "char-class-divergence-inside-find_file", (), status=H.INFO,
        detail=["`[ab].py` (bare mask -> fnmatch on the basename, "
                "Scripts/mcp-purity.py:1159) -> %s"
                % (", ".join(sorted(bare)) or "nothing"),
                "`src/[ab].py` (path-style -> _compile_path_glob, "
                "Scripts/mcp-purity.py:1127) -> %s"
                % (", ".join(sorted(scoped)) or "nothing"),
                "the re.escape fallthrough at mcp-purity.py:1127 turns `[ab]` "
                "into a LITERAL, so one handler answers a character class two "
                "ways and the mask's `/` is what picks the answer",
                "divergence present: %s"
                % ("yes" if bare and not scoped else
                   "NO -- the two branches now agree, update this row")])


# ---------------------------------------------------------------------------
# Group F -- hygiene
# ---------------------------------------------------------------------------

def group_f(suite, before, pyc_before, workspaces, stderr_bytes):
    after = H.repo_tree()
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
                          "its narrowness, the param contract, and the glob "
                          "spellings that can only ever match nothing",
                    opts=opts, mode="stream", group_width=3, cid_width=36)

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    stderr_bytes = 0

    with H.TempWorkspace("ph-purity-file-ops-", keep=opts.keep) as ws:
        basename_root = make_fixture(ws, "basename", GITIGNORE_BASENAME)
        pathshaped_root = make_fixture(ws, "pathshaped", GITIGNORE_PATHSHAPED)
        glob_root = make_glob_fixture(ws, "globs")
        suite.note("      server        : %s" % SERVER)
        suite.note("      fixture (A-D) : %s  .gitignore=%s"
                   % (basename_root, list(GITIGNORE_BASENAME)))
        suite.note("      fixture (E)   : %s  .gitignore=%s"
                   % (pathshaped_root, list(GITIGNORE_PATHSHAPED)))
        suite.note("      fixture (G)   : %s  no .gitignore, files=%s"
                   % (glob_root, list(GLOB_FILES)))

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

        # Group G starts no child: it imports the server module and calls the
        # handlers in-process, so it runs outside the driver lifetime entirely.
        group_g(suite, glob_root)

        workspaces = [basename_root, pathshaped_root, glob_root]
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
