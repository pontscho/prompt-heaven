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

Group H is the multi-root contract: `path`/`paths` as a LIST.  It is a whitelist
(`MULTI_PATH_FUNCTIONS`), not a widening -- every other handler reads
`relative_path` as a scalar, so the refusal is still the default and the group
gates BOTH sides: the two functions that serve a list, and the ones that must
keep refusing it with a message naming the way out.  The paging rows are the
load-bearing ones.  `head_limit` and `offset` have to span the CONCATENATION of
the roots, not restart at each one, or the resume hint a caller pastes back
means something different depending on which root the previous page ended in --
so `head_limit=4` over a 3-match root and a 3-match root must return 4 rows, not
4 per root, and `offset=3` must land inside the SECOND root.

No external binary is involved in groups A-G -- these are the pure-stdlib file
handlers, so there is no skip path and they run everywhere in a couple of
seconds.  Group H's `clang_tidy` rows are the one exception and they are split
accordingly: the half that gates the ALIAS RESOLVER (the list reaches the
handler at all) needs no binary and is always gated, while the half that reads
the rendered report degrades to INFO when `clang-tidy` is not on PATH, the same
convention test_purity_lsp.py uses for a missing clangd.  If a case here needs
clangd, it is in the wrong file (see test_purity_lsp.py).

Fixtures live in a `tempfile.mkdtemp()` workspace and the servers'
`--project-root` points there, never into the repo tree.  Group F asserts that,
plus ZERO `.pyc` anywhere (absolute, not a delta: a file that already existed
reads as "1 before, 1 after" and sails through a delta check).

Groups:
  A  the exemption: `.claude/tmp` is searched, `build/` still is not
  B  the exemption is NARROW -- the inheritance rule (the shipped defect)
  C  list_dir, both branches, with and without skip_ignored_files
  D  the parameter contract: aliases (function and param, global and
     per-function -- `max_results`/`max` capping the three listings like
     `head_limit`, refused beside it, and left alone in `symbol`;
     `count`/`max_matches`/`limit` capping search the same way, `count` no
     longer refused as an unknown `max_results` it never was;
     `paths_include`/`paths_exclude` filtering like their `*_glob` params and
     refused beside them or beside `exclude`), the
     inverted `no_ignore` spelling, tolerated no-ops,
     real rejections, and search's `regex:false` literal mode (a `(` and a
     `\\|` taken literally) with the regex-mode compile error naming it
  E  path-shaped .gitignore: what the basename matcher does and does not honour
  F  hygiene
  G  glob semantics -- the spellings that can only ever match nothing
  H  a LIST of paths: one result stream where it is served, a refusal that
     names the way out everywhere else
  I  read_file's `limit`: a line count from the resolved start (negative
     offset included), a resume hint when it cuts before EOF and one that
     round-trips from a negative offset, refusals for the spellings that have
     no honest answer (a fractional float, a bool), and an offset past EOF
     answered with `_rows_note`'s past-the-end form, not an inverted range
  J  find_file and the ignore filter: off by default (list_dir's default),
     `skip_ignored_files` / `no_ignore` to turn it on, `.git` never listed
  K  a missing directory -- or a file where one is expected -- reaches the
     caller as an error, not as an empty reply; search_for_pattern's missing
     root too, while the file root it legitimately takes keeps working; and
     search's root OUTSIDE the project root, which it admitted and then read
     nothing of: now searched (file, directory, and in a list beside an
     in-root root), each file contained by the root it was admitted under so
     a symlink escaping THAT root is still dropped -- and refused under
     --strict, where safe_path stops the escape before any walk
  L  an offset at or past the last row answers with `_rows_note`'s
     past-the-end form in list_dir and find_file, never an inverted header
     range; search's pagers as the controls that already did
  M  a walk rooted at or inside `.git` is refused, in all three walkers and
     every spelling of the path; `.github` / `x.git` are not, and read_file
     still reads one file under `.git`
  N  search's `only_matching` (ripgrep -o): one row per MATCH carrying only
     the matched text, zero-length matches dropped, literal mode too,
     `head_limit`/`offset` counting match rows, a header that names both the
     match and the line count, and a refusal -- not a silent drop -- beside
     `count`, `files_with_matches` or any nonzero context
  O  fnmatch character classes in find_file's path-style branch (R-0012):
     `src/[ab].py`, `[!a]`, a range, a class in a directory segment and after
     `**/` answer as the bare-mask branch does; a class never matches `/`, an
     unclosed `[` stays literal, a bracketed filename is reached via `[[]`,
     braces are still refused; search's and list_dir's globs as controls
  P  a catastrophic search regex (`(a+)+$`) is BOUNDED (R-0016): each of the
     four match sites answers an error naming the time budget within a stated
     bound, on a fresh server that still answers afterwards; normal regexes
     (`$`, CRLF, no final newline, form feed, non-ASCII, context, count,
     files_with_matches, zero-length only_matching), literal mode and the
     pattern-length ceiling as controls
  Q  replace_content's `mode:"regex"` is bounded the same way: a catastrophic
     needle answers an error naming the budget and `mode:"literal"` within the
     group P bound, leaves the file's bytes untouched, and the server still
     replaces afterwards; backrefs, zero-length matches, the zero / one / many
     answers, a bad group reference and literal mode as controls
  R  a regex:true pattern with NO metacharacter is matched in-process, its
     answer byte-identical to the worker's; a TOTAL-budget overrun says so,
     with the file count and the way out, and backtracking is blamed only
     when the file in flight alone used half the budget (clock seam swapped)
  S  a directory below the search root holding a `.git` file or directory
     (worktree, submodule, nested clone) is skipped unless the filter is off,
     the root itself never; an out-of-root root takes its ignore rules from
     its OWN git toplevel's .gitignore, paths measured from that toplevel
"""

import os
import re
import shutil
import sys
import threading
import time

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

# Written by make_fixture but kept OUT of ALL_FILES, so no older row's path set
# moves.  P_LOG is ignored by its OWN name (`*.log`) rather than by a pruned
# ancestor -- the one shape groups A-C never had, and the one a file filter can
# get wrong while a directory prune still looks right.  P_GITFILE is a file
# under `.git`, which every walk prunes unconditionally (group J).
P_LOG = "src/debug.log"
P_GITFILE = ".git/HEAD"

# Group K's out-of-root tree (make_outside_fixture), relative to ITS own root.
O_SRC = "src"
O_KEEP = "src/keep.txt"                  # a plain file under the search root
O_ESC = "src/esc.txt"                    # symlink -> ../escape.txt
O_ESCAPE = "escape.txt"                  # the escaping symlink's target

# Basename-shaped patterns: what `_is_ignored` actually matches on, since it is
# handed a bare name at the prune sites.  This is the hostile shape.
GITIGNORE_BASENAME = ("tmp", ".claude", "build", "*.log")

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

# Group H's tree.  No .gitignore, for the same reason group G has none: a pruned
# directory and a root that was never visited render identically, so mixing the
# two questions would let either one pass for the other.
#
# The match COUNTS are the point.  Several matches per file is what makes
# "`head_limit` spans the concatenation" a different observable from "`head_limit`
# restarts per root" -- with one match each, 4-across-two-roots and 4-per-root
# return the same rows and the suite would gate nothing.  `two/sub/c.txt` sits
# under `two/` so a parent and a child can be named in the SAME call, which is
# the dedupe case; `three/d.txt` is under a root nobody names, so scoping is
# proven rather than assumed.
MULTI_FILES = (
    ("one/a.txt", 3),
    ("two/b.txt", 3),
    ("two/sub/c.txt", 2),
    ("three/d.txt", 1),
)
M_A, M_B, M_C, M_D = (rel for rel, _ in MULTI_FILES)
M_A_HITS, M_B_HITS, M_C_HITS, M_D_HITS = (n for _, n in MULTI_FILES)
M_TWO = "two"                           # the directory root M_C lives under

# Two real translation units for the clang_tidy rows.  Trivial on purpose: what
# is under test is that TWO paths survive the alias resolver and reach one
# invocation, not anything clang-tidy has an opinion about.
M_CFILES = ("one/x.c", "one/y.c")

# Group I's file: every line names its own 1-based number, so a window read
# from the wrong place is legible in the failure detail instead of being one
# anonymous row among identical ones.  Ten rows is enough to put a window in
# the middle, at the tail, and past the end.
R_FILE = "ten.txt"
R_LINES = 10
# The same numbering with a wide tail, for the one row that needs the handler's
# character ceiling to engage while the dispatcher's own cap (which enforces
# the same number on the WHOLE reply) still lets the result through.  `row N`
# stays the first token, so one parser reads both files.
R_WIDE = "wide.txt"
R_WIDE_PAD = "x" * 60

# Group D's literal-mode tree (make_literal_fixture): one file per line shape, so
# a literal and a regex reading of the same pattern are told apart by WHICH
# files match rather than by counting rows.  Its own root and its own child,
# because adding files to the basename fixture would move every row-counting
# case in groups J and L.  `L_CALL` is the reported pattern's real line; the
# three pipe files are `\|` read literally, `|` read literally, and a bare `a`
# that only the regex alternation `a|b` reaches.
L_CALL = "call.txt"
L_NOCALL = "nocall.txt"
L_ESCPIPE = "escpipe.txt"
L_PIPE = "pipe.txt"
L_ALT = "alt.txt"
LITERAL_FILES = (
    (L_CALL, "n = size(const amflite_amf0_value_t *v);\n"),
    (L_NOCALL, "size is not a call here\n"),
    (L_ESCPIPE, "a\\|b\n"),
    (L_PIPE, "a|b\n"),
    (L_ALT, "just a\n"),
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def make_fixture(ws, subdir, patterns):
    """Write one throwaway project root and return its absolute path."""
    ws.subdir(subdir)
    ws.write_text(os.path.join(subdir, ".gitignore"),
                  "\n".join(patterns) + "\n")
    for rel in ALL_FILES + (P_LOG, P_GITFILE):
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


def make_multi_fixture(ws, subdir):
    """Group H's tree, REALPATH'd for the same reason group G's is.

    Each file carries its declared number of matching lines, tagged with its own
    path so a row read out of order is legible in the failure detail rather than
    being an anonymous `NEEDLE_ALPHA`.
    """
    ws.subdir(subdir)
    for rel, count in MULTI_FILES:
        body = "".join("%s %s hit %d\n" % (NEEDLE, rel, i + 1)
                       for i in range(count))
        ws.write_text(os.path.join(subdir, rel), body)
    for rel in M_CFILES:
        stem = os.path.splitext(os.path.basename(rel))[0]
        ws.write_text(os.path.join(subdir, rel),
                      "int %s_fn(void) { return 0; }\n" % stem)
    return os.path.realpath(ws.join(subdir))


def make_outside_fixture(ws, subdir):
    """Group K's out-of-root tree: a sibling of every server's project root.

    `src/esc.txt` is a file symlink to `escape.txt` one level up, so it ESCAPES a
    search rooted at `src` but stays INSIDE one rooted at the tree -- the pair
    of rows that tells a dropped escape from a fixture that never existed.
    """
    ws.subdir(subdir)
    ws.write_text(os.path.join(subdir, O_KEEP), LINE)
    ws.write_text(os.path.join(subdir, O_ESCAPE), LINE)
    os.symlink(os.path.join("..", os.path.basename(O_ESCAPE)),
               ws.join(os.path.join(subdir, O_ESC)))
    return os.path.realpath(ws.join(subdir))


def make_read_fixture(ws, subdir):
    """Group I's tree: two numbered files and nothing else to get in the way."""
    ws.subdir(subdir)
    ws.write_text(os.path.join(subdir, R_FILE),
                  "".join("row %d\n" % (i + 1) for i in range(R_LINES)))
    ws.write_text(os.path.join(subdir, R_WIDE),
                  "".join("row %d %s\n" % (i + 1, R_WIDE_PAD)
                          for i in range(R_LINES)))
    return os.path.realpath(ws.join(subdir))


def make_literal_fixture(ws, subdir):
    """Group D's literal-mode tree: one line shape per file, no .gitignore."""
    ws.subdir(subdir)
    for rel, body in LITERAL_FILES:
        ws.write_text(os.path.join(subdir, rel), body)
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

    def __init__(self, project_root, timeout=RPC_TIMEOUT, strict=False):
        self.root = project_root
        self.cli = H.JsonRpcClient(
            [sys.executable, SERVER, "--project-root", project_root]
            + (["--strict"] if strict else []),
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


def search_row_paths(text):
    """The paths of a `content`-mode search reply IN REPLY ORDER, with repeats.

    `search_paths` above is a SET, which is exactly wrong for group H: order is
    the contract there (roots are scanned in the order given) and so is
    multiplicity (a file reached through two overlapping roots must appear its
    own number of times, not twice that).  A set answers neither question.
    """
    out = []
    for row in text.splitlines():
        m = RX_MATCH_ROW.match(row.strip())
        if m:
            out.append(m.group("path"))
    return out


RX_MATCH_HEADER = re.compile(r"^(?P<n>\d+)(?P<approx>\+?) match\(es\)")


def match_header(text):
    """`(count, approx)` from a search reply's header line, or `(None, None)`.

    `approx` is the `+` the handler appends when the SCAN stopped early, so the
    count is a lower bound.  Read rather than ignored: a dedupe that merely
    hides duplicate ROWS while still counting them twice leaves the rows right
    and the header wrong, and only this tells the two apart.
    """
    m = RX_MATCH_HEADER.match(text.strip().splitlines()[0] if text.strip() else "")
    return (int(m.group("n")), m.group("approx")) if m else (None, None)


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

    The file handlers answer under `__raw_text__`.  `text` is still read as a
    fallback, but it is NOT a key the wire renders: list_dir and find_file used
    to return their missing-directory message under it, and it reached the
    caller as an empty reply (group K).
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
                   want_error=True, detail=(), must_not_say=()):
    """Call a handler IN-PROCESS and assert on the EXCEPTION, not on a reply.

    `record_error` above cannot serve here.  Over the wire a refusal and a glob
    that simply matched nothing are both a short, unremarkable reply -- and the
    whole point of the rule being gated is that today they are the SAME reply,
    so a wire-level assertion would be asserting the defect.  The TYPE is
    checked as well as the message because `except ValueError` is what the
    dispatcher's error envelope is written against, and the MESSAGE because a
    refusal whose text does not name what it refused just moves the silence one
    layer out: the caller still has to guess which of its globs was the problem.
    `must_not_say` is the other half of that: advice the refusal must NOT give,
    because advice naming a spelling the parameter does not take costs the
    caller a second failed retry.
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
            for token in must_not_say:
                if token.lower() in low:
                    problems.append("refusal wrongly mentions %r" % token)
    elif raised is not None:
        problems.append("expected acceptance, raised %s: %s"
                        % (type(raised).__name__, raised))
    return suite.record("G", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "outcome: %s" % " | ".join(text.splitlines())[:200]],
                        text=text, showable=True)


def record_inproc_polarity(suite, cid, handler, params, root, must, must_not,
                           extract=search_paths, detail=()):
    """`record_polarity`'s in-process twin, for group G.

    A raised exception is recorded as the failure it is rather than allowed to
    abort the group: the defect this was written for WAS an exception -- a
    TypeError out of the glob layer -- and a case that crashed the suite would
    hide every row after it.
    """
    try:
        text = handler_text(handler(params, root))
        raised = None
    except Exception as exc:                                     # noqa: BLE001
        raised, text = exc, "%s: %s" % (type(exc).__name__, exc)
    if raised is not None:
        problems = ["raised %s: %s" % (type(raised).__name__, raised)]
    else:
        problems = polarity(text, False, must, must_not, extract)
    return suite.record("G", cid, problems,
                        detail=list(detail) + [
                            "params       : %s" % (params,),
                            "must find    : %s" % (", ".join(must) or "-"),
                            "must not find: %s" % (", ".join(must_not) or "-"),
                            "reported     : %s"
                            % (", ".join(sorted(extract(text))) or "-")],
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

def group_d(suite, drv, drv_lit):
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
        detail=["regex=true is the default and line_numbers=true is what",
                "content rows always carry, so both are accepted as no-ops"])
    # `regex:false` is a real literal mode: the pattern is re.escape()d, and the
    # `\|` -> `|` rewrite that serves regex mode is skipped.  The first row is
    # the reported pattern, whose `(` used to fail to compile.
    record_polarity(
        suite, "D", "regex-false-literal-paren", drv_lit, "search",
        {"substring_pattern": "size(const amflite_amf0_value_t",
         "regex": False},
        must=[L_CALL], must_not=[L_NOCALL],
        detail=["`(` is a literal character in literal mode, not an",
                "unterminated group"])
    record_polarity(
        suite, "D", "regex-false-literal-escaped-pipe", drv_lit, "search",
        {"substring_pattern": "a\\|b", "regex": False},
        must=[L_ESCPIPE], must_not=[L_PIPE, L_ALT],
        detail=["literal `a\\|b` is the three characters it spells; the",
                "regex-mode `\\|` -> `|` rewrite would widen it to `a|b`"])
    record_polarity(
        suite, "D", "regex-true-pipe-still-alternation", drv_lit, "search",
        {"substring_pattern": "a\\|b", "regex": True},
        must=[L_ESCPIPE, L_PIPE, L_ALT], must_not=[],
        detail=["CONTROL: regex mode keeps the `\\|` -> `|` rewrite, so the",
                "same pattern is the alternation `a|b` and reaches `just a`"])
    record_error(
        suite, "D", "regex-invalid-names-literal-mode", drv_lit, "search",
        {"substring_pattern": "size(const amflite_amf0_value_t"},
        must_say=["Invalid regex pattern", "regex:false"],
        detail=["a regex that does not compile must name the way out,",
                "not only the position of the unterminated group"])
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

    # `max_results` / `max` -> head_limit in the three file-layer listings.  The
    # global `max` row points at the semantic handlers' `max_results`, which
    # none of these three accepts, so before the per-function rows both words
    # died as an unknown param.  Each row compares the alias against the
    # canonical head_limit call ROW FOR ROW, and carries its own anti-vacuity
    # check: an uncapped call that already returns <= N rows would let an alias
    # that were tolerated and DROPPED pass as a cap.
    cap = 2
    for cid, function, base, alias, extract in (
            ("max_results-caps-search-like-head_limit", "search",
             {"substring_pattern": NEEDLE, "skip_ignored_files": False},
             "max_results", search_row_paths),
            ("max-caps-find_file-like-head_limit", "find_file",
             {"file_mask": "*.txt"}, "max", found_paths),
            ("max_results-caps-list_dir-like-head_limit", "list_dir",
             {"relative_path": ".", "recursive": True, "show_hidden": True},
             "max_results", listing_paths),
            # `count` used to fall through to the GLOBAL `count` -> max_results
            # row and die as "Unknown params: max_results" -- a key the caller
            # never wrote.  `max_matches` and `limit` are the other two words a
            # grep-taught caller reaches for; neither had any row at all.
            ("count-caps-search-like-head_limit", "search",
             {"substring_pattern": NEEDLE, "skip_ignored_files": False},
             "count", search_row_paths),
            ("max_matches-caps-search-like-head_limit", "search",
             {"substring_pattern": NEEDLE, "skip_ignored_files": False},
             "max_matches", search_row_paths),
            ("limit-caps-search-like-head_limit", "search",
             {"substring_pattern": NEEDLE, "skip_ignored_files": False},
             "limit", search_row_paths)):
        _, full_text = drv.call(function, base)
        _, canon_text = drv.call(function, dict(base, head_limit=cap))
        is_error, text = drv.call(function, dict(base, **{alias: cap}))
        full, canon, got = (extract(full_text), extract(canon_text),
                            extract(text))
        problems = ["server returned an error"] if is_error else []
        if len(full) <= cap:
            problems.append("VACUOUS: the uncapped call returned %d row(s), "
                            "not more than %d" % (len(full), cap))
        if len(canon) != cap:
            problems.append("CONTROL: head_limit=%d returned %d row(s)"
                            % (cap, len(canon)))
        if got != canon:
            problems.append("%s=%d rows differ from head_limit=%d rows"
                            % (alias, cap, cap))
        suite.record(
            "D", cid, problems,
            detail=["`%s` -> head_limit in %s (per-function row; the global"
                    % (alias, function),
                    "`max` row would send it to max_results, not accepted here)",
                    "uncapped : %d row(s)" % len(full),
                    "head_limit=%d: %s" % (cap, sorted(canon)),
                    "%s=%d: %s" % (alias, cap, sorted(got)),
                    "reply: %s" % " | ".join(text.splitlines())[:200]],
            text=text, showable=True)
    # Two spellings of one param is refused (ADR 0015), and the alias is one.
    record_error(
        suite, "D", "max_results-and-head_limit-ambiguous", drv, "search",
        {"substring_pattern": NEEDLE, "max_results": 1, "head_limit": 1},
        must_say=["ambiguous", "max_results", "head_limit"],
        must_not_say=["unknown params"],
        detail=["both set head_limit: a collision, not a precedence question"])
    # The re-pointed `count` and the two new words land on head_limit too, so
    # they collide the same way -- with the canonical name and with each other.
    # `max_results` must not be named: that was the defect's whole symptom.
    record_error(
        suite, "D", "count-and-head_limit-ambiguous", drv, "search",
        {"substring_pattern": NEEDLE, "count": 1, "head_limit": 1},
        must_say=["ambiguous", "'count'", "head_limit"],
        must_not_say=["unknown params", "max_results"],
        detail=["`count` now resolves to head_limit in search, so beside",
                "head_limit it is ADR 0015's collision, named by the keys",
                "the caller actually wrote"])
    record_error(
        suite, "D", "limit-and-max_matches-ambiguous", drv, "search",
        {"substring_pattern": NEEDLE, "limit": 1, "max_matches": 1},
        must_say=["ambiguous", "'limit'", "'max_matches'", "head_limit"],
        must_not_say=["unknown params"],
        detail=["two ALIASES of head_limit collide just as an alias and the",
                "canonical name do"])

    # `paths_include` / `paths_exclude` -> the *_glob params, GLOBAL rows beside
    # `include`/`exclude`.  The reported call was search_for_pattern with
    # `paths_exclude: [<file>]`, which died as an unknown param.  Each row
    # compares the alias against the canonical call ROW FOR ROW, and pins the
    # filter's effect against an unfiltered CONTROL: a key that were tolerated
    # and dropped returns the unfiltered rows, which differ from the canonical
    # ones only because the target file is in one set and not the other.
    base = {"substring_pattern": NEEDLE}
    _, plain_text = drv.call("search", base)
    plain = search_row_paths(plain_text)
    for cid, alias, canon_key, value, gone in (
            ("paths_exclude-list-excludes-like-glob", "paths_exclude",
             "paths_exclude_glob", [P_KEEP], P_KEEP),
            ("paths_include-list-includes-like-glob", "paths_include",
             "paths_include_glob", ["src/**"], P_SCRATCH)):
        _, canon_text = drv.call("search", dict(base, **{canon_key: value}))
        is_error, text = drv.call("search", dict(base, **{alias: value}))
        canon, got = search_row_paths(canon_text), search_row_paths(text)
        problems = ["server returned an error"] if is_error else []
        if gone not in plain:
            problems.append("VACUOUS: %s is absent even without the filter"
                            % gone)
        if gone in canon:
            problems.append("CONTROL: %s=%s still returned %s"
                            % (canon_key, value, gone))
        if gone in got:
            problems.append("%s=%s still returned %s" % (alias, value, gone))
        if not got:
            problems.append("%s=%s returned no rows at all" % (alias, value))
        if got != canon:
            problems.append("%s rows differ from %s rows" % (alias, canon_key))
        suite.record(
            "D", cid, problems,
            detail=["`%s` -> %s (global row)" % (alias, canon_key),
                    "unfiltered : %s" % sorted(plain),
                    "%s: %s" % (canon_key, sorted(canon)),
                    "%s: %s" % (alias, sorted(got)),
                    "reply: %s" % " | ".join(text.splitlines())[:200]],
            text=text, showable=True)
    # Two spellings of one param is refused (ADR 0015) -- against the canonical
    # name and against the older alias alike, never as an unknown param.
    record_error(
        suite, "D", "paths_exclude-and-glob-ambiguous", drv, "search",
        {"substring_pattern": NEEDLE, "paths_exclude": [P_KEEP],
         "paths_exclude_glob": [P_KEEP]},
        must_say=["ambiguous", "paths_exclude", "paths_exclude_glob"],
        must_not_say=["unknown params"],
        detail=["both set paths_exclude_glob: a collision, not a precedence",
                "question"])
    record_error(
        suite, "D", "paths_exclude-and-exclude-ambiguous", drv, "search",
        {"substring_pattern": NEEDLE, "paths_exclude": [P_KEEP],
         "exclude": [P_KEEP]},
        must_say=["ambiguous", "'exclude'", "'paths_exclude'"],
        must_not_say=["unknown params"],
        detail=["two ALIASES of one canonical param collide just the same"])
    # CONTROL: the per-function rows must not leak into the semantic layer,
    # where `max_results` is canonical and `max` / `count` must still reach it.
    problems, replies = [], []
    for key in ("max_results", "max", "count"):
        is_error, text = drv.call("symbol", {"query": NEEDLE, key: 1})
        low = text.lower()
        replies.append("%s: %s" % (key, text.strip()[:120]))
        if "unknown params" in low or "head_limit" in low:
            problems.append("`%s` in symbol was not left on max_results: %s"
                            % (key, text.strip()[:160]))
    suite.record("D", "symbol-keeps-max_results", problems,
                 detail=["an LSP-unavailable or empty answer is acceptable;",
                         "an unknown-param or head_limit mention is not"]
                 + replies, showable=True)

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
# Group H -- a LIST of paths
#
# Sits between E and G because source order is CALL order here: H drives a
# server child of its own, so it belongs with the wire-driven groups, while G
# runs in-process after every child has been closed.
# ---------------------------------------------------------------------------

def group_h(suite, drv, root):
    """`path`/`paths` as a list: served where it was whitelisted, refused where
    it was not, and paged as ONE stream either way.

    The refusal is the DEFAULT and stays that way, so this group has to gate two
    opposite things at once.  `relative_path` is a GLOBAL alias of
    `path`/`paths`/`file`/`root`, which means a list let through indiscriminately
    would reach a dozen handlers that read it as a scalar -- and those do not
    fail loudly, they interpolate a Python list repr into a path or a message and
    answer about a file nobody named.  So the whitelist is hand-written
    (`MULTI_PATH_FUNCTIONS`), the last two rows here gate that it names real
    handlers and is advertised where the model reads it, and the two refusal rows
    gate that everything else still says no -- while naming the way out, because
    a caller who is told only "no" spends the round trip finding out where "yes"
    lives.

    The paging rows are where a plausible implementation goes wrong.  Looping the
    roots and calling the existing single-root scan once per root passes
    `list-two-files-both-reported` and `list-order-is-call-order` and still
    breaks `head_limit`/`offset`: each root would start its own page, `head_limit=4`
    would return 4 rows PER root, and the `offset=N for more` hint at the bottom
    would resume inside whichever root the last page ended in.  Hence the exact
    expected row lists rather than counts of distinct paths.
    """
    mod = H.load_module_from_path("mcp_purity_multipath", SERVER)

    # -- the list is served, in the order given, and stays scoped --------------
    record_polarity(
        suite, "H", "list-two-files-both-reported", drv, "search",
        {"substring_pattern": NEEDLE, "paths": [M_A, M_B]},
        must=[M_A, M_B], must_not=[M_C, M_D],
        detail=["two roots in ONE call: both contribute rows, and the files",
                "under neither of them stay out -- a list must not quietly",
                "widen back to the whole project root"])

    _, fwd = drv.call("search", {"substring_pattern": NEEDLE,
                                 "paths": [M_A, M_B]})
    _, rev = drv.call("search", {"substring_pattern": NEEDLE,
                                 "paths": [M_B, M_A]})
    fwd_rows, rev_rows = search_row_paths(fwd), search_row_paths(rev)
    want_fwd = [M_A] * M_A_HITS + [M_B] * M_B_HITS
    want_rev = [M_B] * M_B_HITS + [M_A] * M_A_HITS
    problems = []
    if fwd_rows != want_fwd:
        problems.append("forward order is %s, want %s" % (fwd_rows, want_fwd))
    if rev_rows != want_rev:
        problems.append("reversed order is %s, want %s" % (rev_rows, want_rev))
    suite.record(
        "H", "list-order-is-call-order", problems,
        detail=["the SAME two roots, both ways round: the rows follow the",
                "CALL, never the filesystem or a set's iteration order",
                "[%s] -> %s" % (", ".join([M_A, M_B]), fwd_rows),
                "[%s] -> %s" % (", ".join([M_B, M_A]), rev_rows)],
        text=fwd, showable=True)

    # -- a parent and its own child named in one call -------------------------
    _, text = drv.call("search", {"substring_pattern": NEEDLE,
                                  "paths": [M_TWO, M_C]})
    rows = search_row_paths(text)
    count, approx = match_header(text)
    want = [M_B] * M_B_HITS + [M_C] * M_C_HITS
    problems = []
    if rows != want:
        problems.append("rows are %s, want %s" % (rows, want))
    if count != len(want) or approx:
        problems.append("header says %r match(es), want %d exactly"
                        % ("%s%s" % (count, approx), len(want)))
    suite.record(
        "H", "overlapping-roots-deduped", problems,
        detail=["`%s` is reached BOTH by walking `%s` and by being named"
                % (M_C, M_TWO),
                "outright; it is one file and must be reported once",
                "the header is checked too: a dedupe that drops the duplicate",
                "ROW while still counting the match leaves the rows right and",
                "the total wrong, and only the header tells those apart",
                "rows: %s" % (rows,)],
        text=text, showable=True)

    # -- the page is over the CONCATENATION, not over each root ---------------
    _, text = drv.call("search", {"substring_pattern": NEEDLE,
                                  "paths": [M_A, M_B], "head_limit": 4})
    rows = search_row_paths(text)
    want = [M_A] * M_A_HITS + [M_B]
    suite.record(
        "H", "head_limit-spans-the-list",
        [] if rows == want else ["rows are %s, want %s" % (rows, want)],
        detail=["head_limit=4 over a %d-match root and a %d-match root:"
                % (M_A_HITS, M_B_HITS),
                "4 rows TOTAL -- all of the first root and one of the second",
                "a per-root limit returns 4 from EACH and passes every other",
                "row in this group, which is why the expectation is the exact",
                "row list and not a count of distinct paths",
                "rows: %s" % (rows,)],
        text=text, showable=True)

    _, text = drv.call("search", {"substring_pattern": NEEDLE,
                                  "paths": [M_A, M_B],
                                  "offset": M_A_HITS, "head_limit": 2})
    rows = search_row_paths(text)
    want = [M_B] * 2
    problems = [] if rows == want else ["rows are %s, want %s" % (rows, want)]
    note_row = [r for r in text.splitlines() if r.strip().startswith("[")]
    if not any("rows %d-%d" % (M_A_HITS + 1, M_A_HITS + 2) in r for r in note_row):
        problems.append("accounting line does not number the page against the "
                        "whole stream: %s" % (note_row or ["<none>"]))
    suite.record(
        "H", "offset-spans-the-list", problems,
        detail=["offset=%d lands exactly on the boundary between the two"
                % M_A_HITS,
                "roots, so the page STARTS in the second one -- a per-root",
                "offset would skip %d matches in each and return nothing"
                % M_A_HITS,
                "the accounting line has to number the page against the whole",
                "stream too, because the caller pastes that offset back",
                "rows: %s | note: %s" % (rows, note_row or ["<none>"])],
        text=text, showable=True)

    # -- the property to protect above all: the scalar call is untouched -------
    problems = []
    for label, target, want in (("file target", M_A, [M_A] * M_A_HITS),
                                ("directory target", M_TWO,
                                 [M_B] * M_B_HITS + [M_C] * M_C_HITS)):
        _, scalar_text = drv.call("search", {"substring_pattern": NEEDLE,
                                             "path": target})
        _, one_text = drv.call("search", {"substring_pattern": NEEDLE,
                                          "paths": [target]})
        got = search_row_paths(scalar_text)
        if got != want:
            problems.append("%s: scalar reply is %s, want %s"
                            % (label, got, want))
        if scalar_text != one_text:
            problems.append("%s: scalar and 1-element list replies differ\n"
                            "  scalar: %r\n  list  : %r"
                            % (label, scalar_text[:200], one_text[:200]))
    suite.record(
        "H", "scalar-and-1-element-list-identical", problems,
        detail=["a 1-element list MEANS the scalar and is collapsed to it in",
                "the alias resolver, so the two replies must be byte-identical",
                "-- not merely equivalent -- for a file target AND a directory",
                "one.  The expected row list is asserted as well, so a handler",
                "that broke both spellings the same way cannot pass on equality"])

    record_polarity(
        suite, "H", "empty-list-means-project-root", drv, "search",
        {"substring_pattern": NEEDLE, "paths": []},
        must=[M_A, M_B, M_C, M_D], must_not=[],
        detail=["an empty list is DROPPED, not served as zero roots: the",
                "handler then applies its own default, which is the project",
                "root -- the same thing omitting the parameter does"])

    # -- and the refusal is still the default ---------------------------------
    record_error(
        suite, "H", "refusal-preserved-for-read_file", drv, "read_file",
        {"paths": [M_A, M_B]},
        must_say=["multi-element list", "read_file",
                  "search_for_pattern", "clang_tidy"],
        detail=["read_file reads relative_path as a SCALAR, so a list there",
                "is not a feature waiting to be enabled -- it is a call that",
                "cannot be answered.  The message must name the functions",
                "that DO take a list, or the caller's next move is a guess"])
    record_error(
        suite, "H", "refusal-names-canonical-function", drv, "ls",
        {"paths": ["one", "two"]},
        must_say=["multi-element list", "list_dir"],
        detail=["called by its ALIAS `ls`; the whitelist is keyed on the",
                "CANONICAL name, so the refusal has to say `list_dir` -- and",
                "a whitelist keyed on the raw name would let `grep` through",
                "while refusing `search_for_pattern`, or the reverse"])

    # -- clang_tidy: the resolver half needs no binary ------------------------
    ct_paths = list(M_CFILES)
    _, ct_text = drv.call("clang_tidy", {"paths": ct_paths, "timeout": 45})
    low = ct_text.lower()
    problems = []
    if "multi-element list" in low:
        problems.append("the alias resolver still refuses clang_tidy's list")
    if "unknown params" in low:
        problems.append("clang_tidy rejected the aliased `paths` key")
    suite.record(
        "H", "clang_tidy-list-past-the-resolver", problems,
        detail=["`rels = rel if isinstance(rel, list) else [rel]` has been in",
                "handle_clang_tidy from the start, and the resolver made the",
                "list branch UNREACHABLE for len > 1 -- a documented contract",
                "no caller could exercise.  This row gates the reachability",
                "only, so it needs no clang-tidy on PATH",
                "reply: %s" % " | ".join(ct_text.splitlines())[:200]],
        text=ct_text, showable=True)

    binary = shutil.which("clang-tidy")
    missing = [p for p in ct_paths if p not in ct_text]
    suite.record(
        "H", "clang_tidy-reports-every-path",
        [] if not missing else
        ["the report names neither or only one of %s" % (ct_paths,)],
        status=None if binary else H.INFO,
        detail=["clang-tidy on PATH: %s" % (binary or "NO -- row is INFO, the "
                                            "same convention a missing clangd "
                                            "gets in test_purity_lsp.py"),
                "every path handed in must appear in the rendered report: one",
                "invocation over both files, which is what the binary's own",
                "CLI takes",
                "reply head: %s" % " | ".join(ct_text.splitlines()[:3])],
        text=ct_text, showable=True)

    # -- the whitelist itself, in-process -------------------------------------
    names = sorted(mod.MULTI_PATH_FUNCTIONS)
    unknown = [n for n in names if n not in mod.HANDLERS]
    suite.record(
        "H", "whitelist-names-a-real-handler",
        [] if not unknown else
        ["MULTI_PATH_FUNCTIONS names no handler: %s" % unknown],
        detail=["a misspelled entry is invisible: the refusal simply keeps",
                "firing for a function the table claims is served",
                "MULTI_PATH_FUNCTIONS = %s" % (names,)])

    desc = mod.PURITY_CALL_TOOL["description"]
    unadvertised = [n for n in names if n not in desc]
    suite.record(
        "H", "whitelist-advertised-in-description",
        [] if not unadvertised else
        ["served by the code, absent from the tool description: %s"
         % unadvertised],
        detail=["the description is where the model reads the contract, so a",
                "function that accepts a list and does not say so is a feature",
                "nobody will call -- and one that says so without accepting it",
                "is worse.  Only the first direction is checkable from here;",
                "the row above checks the other"])


# ---------------------------------------------------------------------------
# Group I -- read_file's `limit`
#
# Wire-driven like H, so it sits with the wire groups and runs before G.
# ---------------------------------------------------------------------------

RX_READ_HEADER = re.compile(r"^\[[^\]]*\] lines (?P<a>-?\d+)-(?P<b>-?\d+) "
                            r"of (?P<n>\d+)$")
RX_READ_ROW = re.compile(r"^row (?P<n>\d+)(?: x+)?$")


def read_parts(text):
    """(header (a, b, n) or None, row numbers in reply order, accounting note).

    Parsed rather than substring-tested: `row 1` is a prefix of `row 10`, and
    the header's range and the rows it describes have to be checked AGAINST
    EACH OTHER, which only a parse can do.
    """
    lines = text.splitlines()
    m = RX_READ_HEADER.match(lines[0]) if lines else None
    header = (int(m.group("a")), int(m.group("b")), int(m.group("n"))) if m else None
    rows = [int(r.group("n")) for r in map(RX_READ_ROW.match, lines[1:]) if r]
    notes = [ln for ln in lines[1:] if ln.startswith("[")]
    return header, rows, (notes[-1] if notes else "")


def record_read(suite, cid, driver, params, want_rows, want_note=None,
                detail=()):
    """One read_file window: exact rows, a header that numbers them, the note.

    `want_note` is a substring the accounting line must carry, `""` asserts
    there is NO accounting line (the whole window was delivered), and None
    leaves the note unchecked.  An empty `want_rows` also asserts the header
    prints NO line range, since an empty window can only print an inverted one.
    """
    is_error, text = driver.call("read_file", params)
    header, rows, note = read_parts(text)
    problems = []
    if is_error:
        problems.append("server returned an error")
    if rows != want_rows:
        problems.append("rows are %s, want %s" % (rows, want_rows))
    want_header = ((want_rows[0], want_rows[-1], R_LINES) if want_rows
                   else None)
    if want_header and header != want_header:
        problems.append("header says %s, want lines %d-%d of %d"
                        % (header, want_header[0], want_header[1], R_LINES))
    if not want_rows and header is not None:
        # An empty window has no range to print; one that prints anyway can
        # only print an INVERTED one (`lines 21-20 of 10`).
        problems.append("header prints a line range for an empty window: %s"
                        % (header,))
    if want_note == "" and note:
        problems.append("unexpected accounting line: %s" % note)
    elif want_note and want_note not in note:
        problems.append("accounting line %r does not carry %r"
                        % (note or "<none>", want_note))
    return suite.record("I", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "header : %s | rows: %s | note: %s"
                            % (header, rows, note or "-")],
                        text=text, showable=True)


def group_i(suite, drv):
    """`limit` is the built-in Read's: at most N lines from the resolved start.

    It is applied AFTER the start has resolved, which is what keeps a negative
    offset meaning "the tail" -- a limit folded into the slice bounds instead
    would turn `offset=-4, limit=2` into a slice that ends before it starts.
    And a cut before EOF must print the same `offset=<n> for more` hint the
    character ceiling prints, or the caller has no way to ask for the rest.
    """
    rng = lambda a, b: list(range(a, b + 1))                     # noqa: E731

    record_read(
        suite, "limit-alone-from-the-top", drv,
        {"path": R_FILE, "limit": 3}, rng(1, 3),
        detail=["no start given: the window opens at line 1"])
    record_read(
        suite, "offset-plus-limit", drv,
        {"path": R_FILE, "offset": 4, "limit": 3}, rng(5, 7),
        detail=["offset is 0-based, so offset=4 is line 5"])
    record_read(
        suite, "start_line-plus-limit", drv,
        {"path": R_FILE, "start_line": 2, "limit": 2}, rng(2, 3))
    record_read(
        suite, "negative-offset-plus-limit", drv,
        {"path": R_FILE, "offset": -4, "limit": 2}, rng(7, 8),
        want_note="offset=8 for more",
        detail=["offset=-4 resolves to the last four lines FIRST, then the",
                "limit keeps two of them -- and the header and the hint are",
                "real line numbers, not the negative index they came from"])
    record_read(
        suite, "negative-offset-header-is-positive", drv,
        {"path": R_FILE, "offset": -3}, rng(8, 10), want_note="",
        detail=["without a limit too: the header used to print the raw",
                "index (`lines -2-0 of 10`)"])

    # The hint has to be the value to paste back, so the row pastes it back.
    _, first = drv.call("read_file", {"path": R_FILE, "limit": 3})
    _, _, note = read_parts(first)
    m = re.search(r"offset=(\d+) for more", note)
    problems = [] if m else ["no `offset=<n> for more` hint: %r"
                             % (note or "<none>")]
    rows = []
    if m:
        _, again = drv.call("read_file", {"path": R_FILE,
                                          "offset": int(m.group(1)),
                                          "limit": 3})
        rows = read_parts(again)[1]
        if rows != rng(4, 6):
            problems.append("pasting offset=%s back returned rows %s, want "
                            "%s" % (m.group(1), rows, rng(4, 6)))
    suite.record("I", "limit-cut-resume-hint-round-trips", problems,
                 detail=["a limit that stops before EOF prints the resume",
                         "hint, and passing that offset back with the same",
                         "limit lands on the very next line",
                         "note: %s | next page: %s" % (note or "-", rows)],
                 text=first, showable=True)

    record_read(
        suite, "limit-past-eof-is-whole-tail", drv,
        {"path": R_FILE, "offset": 8, "limit": 50}, rng(9, 10), want_note="",
        detail=["nothing was cut, so there is nothing to resume: no hint"])
    record_read(
        suite, "reported-call-shape-accepted", drv,
        {"path": R_FILE, "limit": 200}, rng(1, R_LINES), want_note="",
        detail=["the call that used to come back as an unknown-param",
                "rejection: `path` + `limit`, exactly as the built-in Read",
                "is called"])

    # The character ceiling still applies ON TOP of the line limit.  Eight wide
    # rows are ~540 chars, so a ceiling of 400 engages the handler's line
    # pager; its budget (the ceiling less the header and accounting reserve)
    # holds a few rows, and the reply stays under the dispatcher's cap of the
    # same number, so what comes back is the handler's cut and not a blind one.
    is_error, text = drv.call("read_file", {"path": R_WIDE, "limit": 8,
                                            "max_answer_chars": 400})
    _, rows, note = read_parts(text)
    problems = []
    if is_error:
        problems.append("server returned an error")
    if not rows or len(rows) >= 8 or rows != rng(1, len(rows)):
        problems.append("rows are %s, want a contiguous run from 1 shorter "
                        "than the limit of 8" % (rows,))
    elif "offset=%d for more" % len(rows) not in note:
        problems.append("accounting line %r does not resume at offset=%d"
                        % (note or "<none>", len(rows)))
    suite.record("I", "max_answer_chars-cuts-inside-limit", problems,
                 detail=["limit=8 with a ceiling too small for 8 rows: the",
                         "ceiling wins and its hint names the row it stopped",
                         "at, not the one the limit would have",
                         "rows: %s | note: %s" % (rows, note or "-")],
                 text=text, showable=True)

    record_error(
        suite, "I", "limit-with-end_line-refused", drv, "read_file",
        {"path": R_FILE, "limit": 3, "end_line": 5},
        must_say=["limit", "end_line", "mutually exclusive"],
        detail=["a COUNT and a POSITION that can disagree: whichever one",
                "silently lost would disobey half the request"])
    record_error(
        suite, "I", "limit-zero-refused", drv, "read_file",
        {"path": R_FILE, "limit": 0}, must_say=["limit", "positive integer"],
        detail=["zero lines is not a read; the built-in Read's limit has no",
                "'unlimited' sentinel either"])
    record_error(
        suite, "I", "limit-negative-refused", drv, "read_file",
        {"path": R_FILE, "limit": -1}, must_say=["limit", "positive integer"],
        detail=["a negative count would slice from the END of the window --",
                "a wrong answer shaped exactly like a right one"])
    record_error(
        suite, "I", "limit-non-integer-refused", drv, "read_file",
        {"path": R_FILE, "limit": "many"},
        must_say=["limit", "positive integer"],
        detail=["_int_param would fall back silently; for a line count the",
                "fallback has no honest value, so it is refused instead"])
    # A float with a fractional part is refused, not truncated: `int(2.5)` is 2,
    # and a caller who asked for two and a half lines gets two with nothing to
    # say a line was dropped.  An INTEGER-VALUED float (3.0) and a digit string
    # ("3") carry no such loss and stay accepted, like every other int param.
    record_error(
        suite, "I", "limit-fractional-float-refused", drv, "read_file",
        {"path": R_FILE, "limit": 2.5}, must_say=["limit", "positive integer"],
        detail=["int(2.5) == 2 would silently truncate a line count"])
    record_error(
        suite, "I", "limit-bool-refused", drv, "read_file",
        {"path": R_FILE, "limit": True}, must_say=["limit", "positive integer"],
        detail=["int(True) == 1: a boolean is not a count, however it coerces"])
    record_read(
        suite, "limit-integral-float-accepted", drv,
        {"path": R_FILE, "limit": 3.0}, rng(1, 3),
        detail=["3.0 names a whole number of lines; nothing is lost"])
    record_read(
        suite, "limit-digit-string-accepted", drv,
        {"path": R_FILE, "limit": "3"}, rng(1, 3),
        detail=["the wire carries numbers as strings; \"3\" is 3"])

    # An offset past EOF is an empty window, not an error -- the same answer
    # list_dir and find_file give through _row_page -- and it says so with
    # _rows_note's past-the-end form instead of an INVERTED header range
    # (`lines 21-20 of 10`) that reads like a parse error.
    for cid, params in (
            ("offset-past-eof-says-so", {"path": R_FILE, "offset": 20}),
            ("offset-past-eof-with-limit-says-so",
             {"path": R_FILE, "offset": 20, "limit": 3})):
        record_read(
            suite, cid, drv, params, [],
            want_note="[no rows at offset 20 of %d]" % R_LINES,
            detail=["no rows exist there; the note names the offset and the",
                    "total so the caller can see why"])

    # The negative-offset hint must be pasteable too: it names a REAL 0-based
    # offset (8), and calling again with it lands on the line after the window.
    _, first = drv.call("read_file", {"path": R_FILE, "offset": -4, "limit": 2})
    _, first_rows, note = read_parts(first)
    m = re.search(r"offset=(\d+) for more", note)
    problems = [] if m else ["no `offset=<n> for more` hint: %r"
                             % (note or "<none>")]
    rows = []
    if m:
        _, again = drv.call("read_file", {"path": R_FILE,
                                          "offset": int(m.group(1)),
                                          "limit": 2})
        rows = read_parts(again)[1]
        want = [first_rows[-1] + 1, first_rows[-1] + 2] if first_rows else []
        if not first_rows or rows != want:
            problems.append("pasting offset=%s back returned rows %s after a "
                            "window of %s, want %s"
                            % (m.group(1), rows, first_rows, want))
    suite.record("I", "negative-offset-hint-round-trips", problems,
                 detail=["offset=-4, limit=2 is rows 7-8; its hint pasted back",
                         "with the same limit must continue at row 9",
                         "window: %s | note: %s | next page: %s"
                         % (first_rows, note or "-", rows)],
                 text=first, showable=True)


# ---------------------------------------------------------------------------
# Group J -- find_file and the ignore filter
#
# Wire-driven, on group A-D's fixture: the same .gitignore, so find_file's
# answer can be read against list_dir's in group C row for row.
# ---------------------------------------------------------------------------

def group_j(suite, drv, root):
    """find_file takes list_dir's filter: OFF by default, `skip_ignored_files`
    or its ripgrep inverse `no_ignore` to turn it on, `.git` pruned either way.

    The default is list_dir's (False), not search's (True), because a finder
    that suddenly hid `build/` would change the answer every existing caller
    gets.  So the default row is the one that pins "nothing changed", and the
    two skip rows are the new behaviour -- each spelling separately, because an
    inversion dropped or applied twice leaves exactly one of them red.
    """
    everything = [P_KEEP, P_SCRATCH, P_GEN, P_LEAK, P_SETTINGS, P_LOG]
    skipped = [P_GEN, P_LEAK, P_SETTINGS, P_LOG, P_GITFILE]

    record_polarity(
        suite, "J", "find_file-default-no-filtering", drv, "find_file",
        {"file_mask": "*"}, must=everything, must_not=[P_GITFILE],
        extract=found_paths,
        detail=["the default is list_dir's FALSE: today's answer, unchanged"])
    record_polarity(
        suite, "J", "find_file-no_ignore-true-no-filtering", drv, "find_file",
        {"file_mask": "*", "no_ignore": True}, must=everything,
        must_not=[P_GITFILE], extract=found_paths)
    record_polarity(
        suite, "J", "find_file-no_ignore-false-skips", drv, "find_file",
        {"file_mask": "*", "no_ignore": False},
        must=[P_KEEP, P_SCRATCH], must_not=skipped, extract=found_paths,
        detail=["an ignored FILE (`*.log`), an ignored dir's subtree",
                "(`build/`), and the inherited-ignore gateway's other",
                "children all go; the `.claude/tmp` exemption stays"])
    record_polarity(
        suite, "J", "find_file-skip_ignored_files-skips", drv, "find_file",
        {"file_mask": "*", "skip_ignored_files": True},
        must=[P_KEEP, P_SCRATCH], must_not=skipped, extract=found_paths)

    # `.git` is pruned whatever the filter says.  The control is in-process: a
    # fixture that never wrote the file would make the must-not rows above pass
    # for the wrong reason.
    exists = os.path.isfile(os.path.join(root, P_GITFILE))
    is_error, text = drv.call("find_file", {"file_mask": "HEAD",
                                            "no_ignore": True})
    got = found_paths(text)
    problems = []
    if not exists:
        problems.append("CONTROL: the fixture never wrote %s" % P_GITFILE)
    if is_error:
        problems.append("server returned an error")
    if P_GITFILE in got:
        problems.append("LEAKED %s with the filter OFF" % P_GITFILE)
    suite.record("J", "find_file-git-never-listed", problems,
                 detail=["%s exists: %s; reported: %s"
                         % (P_GITFILE, exists, ", ".join(sorted(got)) or "-")],
                 text=text, showable=True)

    record_error(
        suite, "J", "find_file-no_ignore-contradiction", drv, "find_file",
        {"file_mask": "*", "skip_ignored_files": True, "no_ignore": True},
        must_say=["contradict"],
        detail=["the shared _skip_ignored_param refuses it here as it does",
                "in search"])
    record_polarity(
        suite, "J", "find_file-reported-call-shape", drv, "find_file",
        {"file_mask": "*", "relative_path": "build", "no_ignore": True},
        must=[P_GEN], must_not=[], extract=found_paths,
        detail=["the call that came back as an unknown-param rejection:",
                "find_file rooted INSIDE an ignored dir with no_ignore=true"])


# ---------------------------------------------------------------------------
# Group K -- a directory that is not there is an error, not an empty listing
#
# ADR 0017's rule, applied to the root rather than the glob: a reply that is
# EMPTY is indistinguishable from "nothing matched", so the caller reports
# absence.  The handlers built the right message and handed it back under a
# key (`text`) the wire layer never reads, so what arrived was "".
# ---------------------------------------------------------------------------

def group_k(suite, drv, drv_strict, outside_root):
    for fn, params in (
            ("find_file", {"file_mask": "*"}),
            ("list_dir", {"recursive": True})):
        record_error(
            suite, "K", "%s-missing-dir-is-error" % fn, drv, fn,
            dict(params, relative_path="no/such/dir"),
            must_say=["does not exist", "no/such/dir"],
            detail=["the message must REACH the caller, flagged isError like",
                    "read_file's `File not found`"])
        record_error(
            suite, "K", "%s-file-as-dir-is-error" % fn, drv, fn,
            dict(params, relative_path=P_KEEP),
            must_say=["not a directory", P_KEEP],
            detail=["a FILE where a directory is expected took the same",
                    "silent-empty branch"])

    # search_for_pattern walked a missing root with os.walk, which yields
    # nothing for a path that is not there -- `0 match(es)`, the same reply as a
    # needle that is genuinely absent.  Unlike the two walkers above it DOES take
    # a file as its root, so the refusal is for a missing path only: the two
    # controls pin that a file root and a directory root still search.
    record_error(
        suite, "K", "search-missing-path-is-error", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": "no/such/dir"},
        must_say=["does not exist", "no/such/dir"],
        detail=["a root that is not there answered `0 match(es)`"])
    record_error(
        suite, "K", "search-missing-path-in-list-is-error", drv,
        "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": ["src", "no/such/dir"]},
        must_say=["does not exist", "no/such/dir"],
        detail=["one missing root in a LIST refuses the call, as one escaping",
                "root already does -- not a partial answer that hides it"])
    record_polarity(
        suite, "K", "search-file-path-still-searched", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": P_KEEP},
        must=[P_KEEP], must_not=[P_SCRATCH],
        detail=["CONTROL: a FILE is a legitimate search root here"])
    record_polarity(
        suite, "K", "search-dir-path-unchanged", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": "src"},
        must=[P_KEEP], must_not=[P_SCRATCH, P_GEN],
        detail=["CONTROL: a directory root is scoped exactly as before"])

    # A root OUTSIDE the project root.  safe_path admits it for a read-only
    # handler (non --strict), but search's per-file symlink gate used to
    # re-contain every walked file against the PROJECT root, so every file of
    # the admitted root was dropped and the reply was `0 match(es)` -- for a
    # scope that was never searched, although the file holds the needle.  The
    # gate now measures each file against the root it was ADMITTED under, so
    # the out-of-root root is searched, and a symlink escaping THAT root is
    # still dropped.  `outside_root` is a sibling fixture workspace (see
    # make_outside_fixture), so the path is out-of-root and real; hits are
    # reported relative to the project root, hence the `../outside/` spelling.
    out_file = os.path.join(outside_root, O_KEEP)
    out_dir = os.path.join(outside_root, O_SRC)
    up = os.path.join("..", os.path.basename(outside_root))
    r_keep = os.path.join(up, O_KEEP)
    r_esc = os.path.join(up, O_ESC)
    r_escape = os.path.join(up, O_ESCAPE)
    record_polarity(
        suite, "K", "search-outside-root-file-finds", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": out_file},
        must=[r_keep], must_not=[],
        detail=["an out-of-root FILE holding the needle answered `0 match(es)`"])
    record_polarity(
        suite, "K", "search-outside-root-dir-finds", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": out_dir},
        must=[r_keep], must_not=[r_esc, r_escape],
        detail=["an out-of-root DIRECTORY walked every file and read none;",
                "its file symlink escaping THAT root must stay dropped"])
    record_polarity(
        suite, "K", "search-outside-root-symlink-in-root", drv,
        "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": outside_root},
        must=[r_keep, r_esc, r_escape], must_not=[],
        detail=["CONTROL for the row above: rooted one level up, the same",
                "symlink resolves INSIDE its search root and is read -- so the",
                "drop above is the escape, not a dead fixture"])
    record_polarity(
        suite, "K", "search-outside-root-in-list-finds", drv,
        "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": ["src", out_dir]},
        must=[P_KEEP, r_keep], must_not=[r_esc],
        detail=["a LIST mixing an in-root and an out-of-root root answers",
                "from BOTH; it used to be a partial answer hiding one"])
    record_error(
        suite, "K", "read-outside-root-file-unchanged", drv, "read_file",
        {"relative_path": out_file}, must_say=[NEEDLE], want_error=False,
        detail=["CONTROL: read_file reads an out-of-root file outside --strict"])
    record_error(
        suite, "K", "search-outside-root-strict-is-error", drv_strict,
        "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": out_dir},
        must_say=["escapes project root", out_dir],
        detail=["--strict is the hard sandbox: safe_path refuses the escape",
                "before any walk, so the permissive gate is never reached"])


# ---------------------------------------------------------------------------
# Group L -- an offset past the last row says so; it never prints a range
#
# list_dir and find_file stamp a 1-based `(showing A-B of N)` range on their
# header, computed from the offset and the rows kept.  With no rows kept that
# range can only come out INVERTED (`showing 21-20 of 10`), which reads like a
# parse error rather than "there is nothing at that offset".  The accounting
# line under the rows already has a form for this -- _rows_note's
# `[no rows at offset N of M]`, the one read_file answers with (group I) -- so
# the header must stay out of its way.  search_for_pattern's header carries no
# range at all; its rows are here as controls so the three stay one answer.
# ---------------------------------------------------------------------------

RX_RANGE = re.compile(r"(\d+)-(\d+) of")


def record_page(suite, cid, driver, function, params, want_note,
                want_range=None, detail=()):
    """One paging row: accepted, no inverted range, the expected note line.

    `want_note` is a regex matched against every line (the note is not always
    the last line of a zero-row reply's neighbours, so it is searched for, not
    positioned).  `want_range` pins the header's `(showing A-B of N)` when rows
    WERE shown -- the control that a fix for the empty window did not also
    delete the range from the windows that have one.
    """
    is_error, text = driver.call(function, params)
    problems = []
    if is_error:
        problems.append("server returned an error")
    for m in RX_RANGE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            problems.append("INVERTED range %r" % m.group(0))
    lines = text.splitlines()
    if not any(re.fullmatch(want_note, ln.strip()) for ln in lines):
        problems.append("no line matches %r" % want_note)
    if want_range and want_range not in (lines[0] if lines else ""):
        problems.append("header does not carry %r" % want_range)
    return suite.record("L", cid, problems,
                        detail=list(detail) + [
                            "params: %s" % (params,),
                            "reply : %s" % " | ".join(lines)[:300]],
                        text=text, showable=True)


def group_l(suite, drv):
    # `src` holds exactly P_KEEP and P_LOG, and a non-recursive listing with the
    # filter off shows both: two rows, so offset 2 is AT the end and 5 past it.
    src_rows = len([p for p in (P_KEEP, P_LOG) if p.startswith("src/")])
    for cid, params in (
            ("list_dir-offset-past-end-says-so",
             {"relative_path": "src", "offset": 5}),
            ("list_dir-offset-at-end-says-so",
             {"relative_path": "src", "offset": src_rows}),
            ("list_dir-offset-past-end-with-head_limit",
             {"relative_path": "src", "offset": 5, "head_limit": 1})):
        record_page(
            suite, cid, drv, "list_dir", params,
            re.escape("[no rows at offset %d of %d]"
                      % (params["offset"], src_rows)),
            detail=["the header printed `(showing %d-%d of %d)`"
                    % (params["offset"] + 1, params["offset"], src_rows)])

    # The same header, reached with NO offset: head_limit alone armed it, and a
    # grep that kept nothing made the window empty -- `(showing 1-0 of 0)`.
    # Nothing is left to account for, so no note is owed; only the inversion is.
    is_error, text = drv.call("list_dir", {"relative_path": "src",
                                           "grep": "zz_no_such_name_zz",
                                           "head_limit": 5})
    problems = ["server returned an error"] if is_error else []
    problems += ["INVERTED range %r" % m.group(0)
                 for m in RX_RANGE.finditer(text)
                 if int(m.group(1)) > int(m.group(2))]
    suite.record("L", "list_dir-empty-window-head_limit-no-range", problems,
                 detail=["head_limit with a grep that matched nothing printed",
                         "`(showing 1-0 of 0)` with no offset in sight",
                         "reply : %s" % " | ".join(text.splitlines())[:300]],
                 text=text, showable=True)
    record_page(
        suite, "list_dir-in-window-range-unchanged", drv, "list_dir",
        {"relative_path": "src", "offset": 1},
        re.escape("[showing rows 2-%d of %d; no rows left]"
                  % (src_rows, src_rows)),
        want_range="(showing 2-%d of %d)" % (src_rows, src_rows),
        detail=["CONTROL: a window that HAS rows keeps its header range"])

    # find_file on the root: every `.txt` the fixture wrote, and nothing else
    # carries that extension (`.git/HEAD` has none, and is pruned anyway).
    txt = len([p for p in ALL_FILES + (P_LOG, P_GITFILE) if p.endswith(".txt")])
    for cid, off in (("find_file-offset-past-end-says-so", 9),
                     ("find_file-offset-at-end-says-so", txt)):
        record_page(
            suite, cid, drv, "find_file", {"file_mask": "*.txt", "offset": off},
            re.escape("[no rows at offset %d of %d]" % (off, txt)),
            detail=["the header printed `(showing %d-%d of %d)`"
                    % (off + 1, off, txt)])
    record_page(
        suite, "find_file-in-window-range-unchanged", drv, "find_file",
        {"file_mask": "*.txt", "offset": txt - 1},
        re.escape("[showing rows %d-%d of %d; no rows left]" % (txt, txt, txt)),
        want_range="(showing %d-%d of %d)" % (txt, txt, txt),
        detail=["CONTROL: a window that HAS rows keeps its header range"])

    # search: CONTROLS.  Its header states a count and no range, so it never
    # had the inversion; these pin that the three pagers keep one answer.
    for cid, params in (
            ("search-files-offset-past-end-says-so",
             {"output_mode": "files_with_matches", "offset": 20}),
            ("search-content-offset-past-end-says-so",
             {"output_mode": "content", "offset": 20})):
        record_page(
            suite, cid, drv, "search_for_pattern",
            dict(params, substring_pattern=NEEDLE),
            r"\[no rows at offset 20 of \d+\]",
            detail=["CONTROL: search's pagers already answer in this form"])


# ---------------------------------------------------------------------------
# Group M -- a walk rooted at or inside `.git` is refused, not listed
#
# Every walker prunes a CHILD directory named `.git`, and the skill says `.git`
# is never listed -- but a walk ROOTED there never meets that child, so
# `list_dir .git` listed the object store and `search .git` grepped it.  The
# answer is a refusal (isError) naming the rule, not an empty reply: an empty
# listing of a directory that plainly exists is ADR 0017's silent zero.
# read_file is not a walker and keeps reading a single file under `.git`.
# ---------------------------------------------------------------------------

# Group M's tree.  `.github` and `x.git` are the two names a prefix or suffix
# test would wrongly refuse; `sub/.git` is the nested repo (a submodule's
# gitdir) that a check on the FIRST component only would miss.
GIT_FILES = (".git/HEAD", ".git/refs/heads/main", "sub/.git/config",
             ".github/workflows/ci.yml", "x.git/data.txt", "src/a.txt")
GM_HEAD, GM_REF, GM_SUBCFG, GM_CI, GM_XGIT, GM_SRC = GIT_FILES


def make_git_fixture(ws, subdir):
    """Group M's tree, REALPATH'd for the same reason group G's is."""
    ws.subdir(subdir)
    for rel in GIT_FILES:
        ws.write_text(os.path.join(subdir, rel), LINE)
    return os.path.realpath(ws.join(subdir))


def group_m(suite, drv):
    walkers = (
        ("list_dir", {"recursive": True, "show_hidden": True}),
        ("find_file", {"file_mask": "*"}),
        ("search_for_pattern", {"substring_pattern": NEEDLE}))
    for fn, params in walkers:
        for rel in (".git", ".git/refs", "sub/.git"):
            record_error(
                suite, "M", "%s-refuses-%s" % (fn, rel.replace("/", "-")),
                drv, fn, dict(params, relative_path=rel),
                must_say=[".git", "never", rel],
                detail=["a walk ROOTED in .git never meets the child-name",
                        "prune, so it listed / searched git internals"])

    # Spellings of the same directory: the check must see the path, not the
    # string.  `src/../.git` is only `.git` once normalised.
    for label, rel in (("dot-slash", "./.git"), ("trailing-slash", ".git/"),
                       ("dotdot", "src/../.git")):
        record_error(
            suite, "M", "list_dir-refuses-spelling-%s" % label, drv, "list_dir",
            {"relative_path": rel, "recursive": True},
            must_say=[".git", "never"],
            detail=["the same directory spelled another way"])
    record_error(
        suite, "M", "search-refuses-file-under-git", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": GM_HEAD},
        must_say=[".git", "never"],
        detail=["search takes a FILE root; one under .git is still inside it"])
    record_error(
        suite, "M", "search-refuses-git-root-in-list", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": ["src", ".git"]},
        must_say=[".git", "never"],
        detail=["one .git root in a LIST refuses the call"])

    # Must-stay-green: the rule is a whole COMPONENT equal to `.git`.
    record_polarity(
        suite, "M", "list_dir-github-not-refused", drv, "list_dir",
        {"relative_path": ".github", "recursive": True},
        must=[GM_CI], must_not=[], extract=listing_paths,
        detail=["CONTROL: `.github` merely STARTS with `.git`"])
    record_polarity(
        suite, "M", "find_file-x.git-not-refused", drv, "find_file",
        {"file_mask": "*", "relative_path": "x.git"},
        must=[GM_XGIT], must_not=[], extract=found_paths,
        detail=["CONTROL: `x.git` merely ENDS with `.git`"])
    record_polarity(
        suite, "M", "search-github-not-refused", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": ".github"},
        must=[GM_CI], must_not=[],
        detail=["CONTROL: the prefix case, on the third walker"])
    record_polarity(
        suite, "M", "search-x.git-not-refused", drv, "search_for_pattern",
        {"substring_pattern": NEEDLE, "relative_path": "x.git"},
        must=[GM_XGIT], must_not=[],
        detail=["CONTROL: the suffix case, on the third walker"])
    record_polarity(
        suite, "M", "list_dir-root-still-prunes-git-children", drv, "list_dir",
        {"relative_path": ".", "recursive": True, "show_hidden": True},
        must=[GM_CI, GM_XGIT, GM_SRC],
        must_not=[GM_HEAD, GM_REF, GM_SUBCFG], extract=listing_paths,
        detail=["CONTROL: a walk from the root keeps pruning every .git",
                "child, top-level and nested alike"])

    is_error, text = drv.call("read_file", {"relative_path": GM_HEAD})
    problems = []
    if is_error:
        problems.append("read_file refused a single file under .git")
    if NEEDLE not in text:
        problems.append("the file's content is not in the reply")
    suite.record("M", "read_file-git-head-still-reads", problems,
                 detail=["CONTROL: read_file is not a walker; reading one",
                         "named file under .git stays allowed",
                         "reply : %s" % " | ".join(text.splitlines())[:200]],
                 text=text, showable=True)


# ---------------------------------------------------------------------------
# Group N -- `only_matching` (ripgrep -o): the match, not the line
#
# The trigger was a real call against a .jsonl log: the caller wanted each
# `"outcome":"..."` value, not the whole event line, and the param died as
# unknown.  Every row here is compared EXACTLY -- a substring test would pass
# on the full line too, which is precisely the behaviour being replaced.
# ---------------------------------------------------------------------------

# Group N's tree.  EV_FILE's third line holds THREE matches, which is what
# makes "head_limit counts match rows" a different observable from "head_limit
# counts lines": with one match per line the two page identically.  The fourth
# line mentions `outcome` without the pattern's shape, so it must stay silent.
OM_EV = "events.jsonl"
OM_ZERO = "zero.txt"
OM_LIT = "lit.txt"
OM_PATTERN = '"outcome":"[a-z-]+"'
ONLYMATCH_FILES = (
    (OM_EV, '{"id":1,"outcome":"pass","note":"x"}\n'
            '{"id":2,"outcome":"fail-hard"}\n'
            '{"outcome":"skip","n":1,"outcome":"retry","outcome":"pass"}\n'
            'no outcome field here\n'),
    # `x*` matches the empty string at every position: line 1 has ONE non-empty
    # match (`xx`) among empties, line 2 has nothing but empties.
    (OM_ZERO, "axxb\nnothing\n"),
    # `size(` is an unterminated group as a regex; twice on one line.
    (OM_LIT, "call size(x) and size(y)\n"),
)
OM_ROWS = [
    '%s:1: "outcome":"pass"' % OM_EV,
    '%s:2: "outcome":"fail-hard"' % OM_EV,
    '%s:3: "outcome":"skip"' % OM_EV,
    '%s:3: "outcome":"retry"' % OM_EV,
    '%s:3: "outcome":"pass"' % OM_EV,
]


def make_onlymatch_fixture(ws, subdir):
    """Group N's tree, REALPATH'd for the same reason group G's is."""
    ws.subdir(subdir)
    for rel, body in ONLYMATCH_FILES:
        ws.write_text(os.path.join(subdir, rel), body)
    return os.path.realpath(ws.join(subdir))


def match_rows(text):
    """The `path:line: text` rows of a search reply, verbatim, in order."""
    return [row for row in text.splitlines() if RX_MATCH_ROW.match(row)]


def record_rows(suite, cid, driver, params, want_rows, want_lines=(),
                detail=()):
    """Call search and require EXACTLY `want_rows`, plus any `want_lines` regex
    matching some line of the reply (header or note)."""
    is_error, text = driver.call("search_for_pattern", params)
    problems = []
    if is_error:
        problems.append("server returned an error")
    got = match_rows(text)
    if got != list(want_rows):
        problems.append("rows %r, want %r" % (got, list(want_rows)))
    lines = [ln.strip() for ln in text.splitlines()]
    for want in want_lines:
        if not any(re.fullmatch(want, ln) for ln in lines):
            problems.append("no line matches %r" % want)
    return suite.record("N", cid, problems,
                        detail=list(detail) + [
                            "params: %s" % (params,),
                            "reply : %s" % " | ".join(text.splitlines())[:400]],
                        text=text, showable=True)


def group_n(suite, drv):
    base = {"substring_pattern": OM_PATTERN, "relative_path": OM_EV,
            "only_matching": True}

    record_rows(
        suite, "one-match-per-line-exact-text", drv,
        dict(base, output_mode="content", head_limit=2), OM_ROWS[:2],
        detail=["one match per line: the row is the matched text alone,",
                "never the `{\"id\":...}` line around it"])
    record_rows(
        suite, "multi-match-line-one-row-each", drv,
        dict(base, output_mode="content"), OM_ROWS,
        detail=["line 3 holds three matches: three rows, all `:3:`, in",
                "the order they occur on the line"])
    record_rows(
        suite, "header-counts-matches-and-lines", drv,
        dict(base, output_mode="content"), OM_ROWS,
        want_lines=[r"5 match\(es\) on 3 line\(s\)(\W.*)?"],
        detail=["under only_matching `N match(es)` counts ROWS (matches),",
                "and the line count is stated beside it, so neither number",
                "can be read as the other"])
    record_rows(
        suite, "default-output_mode-is-content", drv, dict(base), OM_ROWS,
        detail=["the default output_mode is already `content`, so",
                "only_matching needs no explicit mode"])
    record_rows(
        suite, "zero-length-matches-no-empty-rows", drv,
        {"substring_pattern": "x*", "relative_path": OM_ZERO,
         "only_matching": True},
        ["%s:1: xx" % OM_ZERO],
        detail=["`x*` matches the empty string everywhere; only `xx` is a",
                "row, and line 2 (empties only) yields none"])
    record_rows(
        suite, "regex-false-literal", drv,
        {"substring_pattern": "size(", "regex": False,
         "relative_path": OM_LIT, "only_matching": True},
        ["%s:1: size(" % OM_LIT, "%s:1: size(" % OM_LIT],
        detail=["literal mode: the match IS the literal, twice on one line"])
    record_rows(
        suite, "head_limit-counts-match-rows", drv,
        dict(base, offset=2, head_limit=2), OM_ROWS[2:4],
        want_lines=[r"\[showing rows 3-4 of .*; offset=4 for more\]"],
        detail=["offset 2 lands on line 3's first match and head_limit 2",
                "stops INSIDE that line: rows, not lines, are counted"])
    record_rows(
        suite, "offset-continues-inside-line", drv,
        dict(base, offset=4), OM_ROWS[4:],
        want_lines=[r"\[showing rows 5-5 of 5; no rows left\]"],
        detail=["the resume hint from the row above picks up the third",
                "match of line 3, not line 4"])

    for mode in ("count", "files_with_matches"):
        record_error(
            suite, "N", "refused-with-%s" % mode, drv, "search_for_pattern",
            dict(base, output_mode=mode),
            must_say=["only_matching", "valid only", "content", mode],
            must_not_say=["unknown params"],
            detail=["%s rows carry no match text to trim" % mode])
    for key in ("context_lines", "context_lines_after"):
        record_error(
            suite, "N", "refused-with-%s" % key, drv, "search_for_pattern",
            dict(base, **{key: 1}),
            must_say=["only_matching", "cannot be combined", "context"],
            must_not_say=["unknown params"],
            detail=["rg ignores context under -o; purity refuses instead of",
                    "silently dropping it"])
    record_rows(
        suite, "context_lines-zero-accepted", drv,
        dict(base, context_lines=0), OM_ROWS,
        detail=["CONTROL: zero context asks for nothing to drop"])
    record_error(
        suite, "N", "only_matching-false-count-accepted", drv,
        "search_for_pattern", dict(base, only_matching=False,
                                   output_mode="count"),
        want_error=False,
        detail=["CONTROL: the refusal is for only_matching TRUE"])
    record_rows(
        suite, "without-only_matching-rows-are-lines", drv,
        {"substring_pattern": OM_PATTERN, "relative_path": OM_EV},
        ['%s:1: {"id":1,"outcome":"pass","note":"x"}' % OM_EV,
         '%s:2: {"id":2,"outcome":"fail-hard"}' % OM_EV,
         '%s:3: {"outcome":"skip","n":1,"outcome":"retry",'
         '"outcome":"pass"}' % OM_EV],
        want_lines=[r"3 match\(es\)"],
        detail=["CONTROL: the default stays one row per matching LINE,",
                "and its header keeps its old shape"])


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
    # The ADVICE differs by whether the parameter takes a list.  search_for_
    # pattern's two globs do (via _glob_list), so their refusal names the list
    # form -- the one-retry answer to a brace call.  find_file's mask and
    # list_dir's filter do not, so theirs must NOT advise a list (that retry
    # would fail too) and keep "one call per alternative" instead.
    record_refusal(
        suite, "brace-refused-search-include-glob",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_include_glob": "*.{js,py}"}, root,
        must_say=("brace", "as a list"),
        must_not_say=("one call per alternative",),
        detail=["an include glob that can only ever match a file literally",
                "named `x.{js,py}` narrows the search to nothing, and the",
                "caller reads that as 'the needle is not in any .js or .py'",
                "-- the refusal points at the list form this parameter takes"])
    record_refusal(
        suite, "brace-refused-search-exclude-glob",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_exclude_glob": "*.{md,txt}"}, root,
        must_say=("brace", "as a list"),
        must_not_say=("one call per alternative",),
        detail=["the exclude side fails in the OPPOSITE direction -- it",
                "excludes nothing and the caller is handed the noise it",
                "asked to drop, which is why it needs its own row"])
    record_refusal(
        suite, "brace-refused-find_file-mask",
        mod.handle_find_file,
        {"file_mask": "**/*auth*.{js,py,php}"}, root,
        must_say=("brace", "one call per alternative"),
        must_not_say=("as a list",),
        detail=["this exact mask is taught by a shipped skill example, so it",
                "is not a hypothetical spelling -- it is one already in the",
                "corpus, returning `Found 0 file(s)` every time it is run;",
                "file_mask takes no list, so the refusal must not advise one"])
    record_refusal(
        suite, "brace-refused-list_dir-filter",
        mod.handle_list_dir,
        {"filter": "*.{js,py}", "relative_path": ".", "recursive": True}, root,
        must_say=("brace", "one call per alternative"),
        must_not_say=("as a list",),
        detail=["list_dir is the worst of the three: `_accept_name` applies",
                "the filter to FILES only, so a brace mask returns a listing",
                "of pure directories and looks like a populated answer;",
                "filter takes no list, so the refusal must not advise one"])

    # -- (b) must-stay-green: the rejector must not become a `{` ban -----------
    record_refusal(
        suite, "brace-without-comma-not-refused",
        mod.handle_find_file,
        {"file_mask": "{{cookiecutter}}*.py"}, root, want_error=False,
        detail=["CONTROL: a brace group with no comma is not an alternation,",
                "it is a literal filename (template scaffolding ships these",
                "by the thousand).  Refusing it would break callers who are",
                "asking for exactly what fnmatch already gives them"])

    # -- (c) a LIST of globs -------------------------------------------------
    # The reported call was `exclude: ["build/**", ".git/**", "vendor/**"]`,
    # and it died as `TypeError: expected string or bytes-like object, got
    # 'list'` inside _reject_brace_glob -- the list reached `re.search` whole.
    # A list of globs is what a ripgrep-taught caller writes (`-g` repeats), so
    # search_for_pattern's two globs take one: include keeps a file matching
    # ANY element, exclude drops a file matching ANY element.
    top = [p for p in GLOB_FILES if "/" not in p]
    nested = [p for p in GLOB_FILES if "/" in p]
    record_inproc_polarity(
        suite, "glob-list-search-exclude", mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE,
         "paths_exclude_glob": ["src/**", "tests/**"]}, root,
        must=top, must_not=nested,
        detail=["the reported shape: exclude as a LIST drops a file that",
                "matches ANY element, instead of raising a TypeError"])
    record_inproc_polarity(
        suite, "glob-list-search-include", mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE,
         "paths_include_glob": ["src/**", "tests/*.py"]}, root,
        must=nested, must_not=top,
        detail=["the include side shares the crash and the fix: a file is",
                "kept when it matches ANY element"])
    record_inproc_polarity(
        suite, "glob-list-empty-means-no-filter", mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_exclude_glob": []}, root,
        must=list(GLOB_FILES), must_not=[],
        detail=["CONTROL: an empty list is an absent filter, exactly as an",
                "empty `relative_path` list is the project root"])
    record_inproc_polarity(
        suite, "glob-scalar-exclude-unchanged", mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_exclude_glob": "src/**"}, root,
        must=[p for p in GLOB_FILES if not p.startswith("src/")],
        must_not=["src/a.py", "src/deep/nested.py"],
        detail=["CONTROL: the string spelling keeps its exact behaviour"])
    record_refusal(
        suite, "glob-list-non-string-element-refused",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_exclude_glob": ["*.md", 5]}, root,
        must_say=("paths_exclude_glob", "int"),
        detail=["a non-string element is a caller error named as one -- a",
                "ValueError that names the parameter and the type, never the",
                "TypeError the glob layer would raise on its own"])
    record_refusal(
        suite, "glob-list-empty-element-refused",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_include_glob": ["*.py", ""]}, root,
        must_say=("paths_include_glob", "empty"),
        detail=["an empty glob inside a list cannot be meant: as an include",
                "element it matches nothing, the silent zero ADR 0017 refuses"])
    record_refusal(
        suite, "glob-list-brace-element-refused",
        mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE,
         "paths_exclude_glob": ["*.md", "*.{js,py}"]}, root,
        must_say=("brace", "paths_exclude_glob[1]", "as a list"),
        must_not_say=("one call per alternative",),
        detail=["the brace rule reaches EVERY element; a list must not be",
                "the way around it -- and the refusal names the element index"])
    record_refusal(
        suite, "glob-non-string-refused-find_file-mask",
        mod.handle_find_file, {"file_mask": ["*.py"]}, root,
        must_say=("file_mask", "string"),
        detail=["find_file's mask is ONE glob and stays one; a list is",
                "refused by name, not answered with a TypeError"])
    record_refusal(
        suite, "glob-non-string-refused-list_dir-filter",
        mod.handle_list_dir,
        {"filter": ["*.py"], "relative_path": ".", "recursive": True}, root,
        must_say=("filter", "string"),
        detail=["list_dir's filter is ONE bare-name glob and stays one"])

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

    # Recorded as an INFO divergence by ADR 0017 and CLOSED by R-0012: the
    # path-style branch (`_compile_path_glob`) used to send `[` through its
    # `re.escape` fallthrough, so `src/[ab].py` looked for a LITERAL `[ab]`
    # while the bare-mask branch's fnmatch honoured the class -- one handler,
    # two answers, picked by the mask's `/`.  Now gated: the scoped answer must
    # be the bare answer restricted to `src/`'s direct children.  Group O
    # carries the rest of the class spellings.
    bare = found_paths(handler_text(
        mod.handle_find_file({"file_mask": "[ab].py"}, root)))
    scoped = found_paths(handler_text(
        mod.handle_find_file({"file_mask": "src/[ab].py"}, root)))
    want = {p for p in bare if p.startswith("src/") and p.count("/") == 1}
    suite.record(
        "G", "char-class-agrees-inside-find_file",
        [] if want and scoped == want else
        ["`src/[ab].py` -> %s, want %s (the bare answer under src/)"
         % (sorted(scoped) or "nothing", sorted(want) or "nothing")],
        detail=["`[ab].py` (bare mask -> fnmatch on the basename) -> %s"
                % (", ".join(sorted(bare)) or "nothing"),
                "`src/[ab].py` (path-style -> _compile_path_glob) -> %s"
                % (", ".join(sorted(scoped)) or "nothing"),
                "the declared divergence of ADR 0017, closed by R-0012: the",
                "mask's `/` no longer picks the meaning of a character class"])


# ---------------------------------------------------------------------------
# Group O -- fnmatch character classes in find_file's path-style branch
#
# ADR 0017 declared, rather than fixed, one divergence inside find_file: a mask
# holding `/` or `**` goes through `_compile_path_glob`, whose `re.escape`
# fallthrough turned `[ab]` into a LITERAL, while a bare mask goes through
# fnmatch and honours the class.  `[ab].py` found `a.py`; `src/[ab].py` found
# nothing -- a silent zero picked by the mask's slash.  R-0012 closes it.
#
# In-process, like group G, and on its own tree so group G's measured rows do
# not move.  The PATH-STYLE branch is the one under test; find_file is its only
# caller (search_for_pattern's globs and list_dir's filter are fnmatch already),
# so the two rows on those handlers are CONTROLS: they were green before the
# fix and prove the three engines now agree on the same spelling.
# ---------------------------------------------------------------------------

# `lit/[ab].py` is a file whose NAME carries brackets.  Neither ADR 0017 nor
# the code promised a literal-bracket spelling for it (0017's literal promise is
# the comma-less `{{...}}` brace group); fnmatch's own escape `[[]` reaches it,
# and after R-0012 that escape works in both branches.  `lit/[x.py` carries an
# UNCLOSED bracket, which fnmatch reads as a literal `[` -- the translator must
# too, rather than raise re.error or swallow the rest of the mask.
CLASS_FILES = ("a.py", "b.py", "c.py",
               "src/a.py", "src/b.py", "src/c.py", "src/deep/a.py",
               "pkg1/m.py", "pkg2/m.py", "pkg3/m.py",
               "lit/[ab].py", "lit/[x.py")


def make_class_fixture(ws, subdir):
    """Group O's tree, REALPATH'd for the same reason group G's is."""
    ws.subdir(subdir)
    for rel in CLASS_FILES:
        ws.write_text(os.path.join(subdir, rel), LINE)
    return os.path.realpath(ws.join(subdir))


def record_o(suite, cid, handler, params, root, must, must_not,
             extract=found_paths, detail=()):
    """One in-process polarity row in group O; an exception is the failure."""
    try:
        text = handler_text(handler(params, root))
        raised = None
    except Exception as exc:                                     # noqa: BLE001
        raised, text = exc, "%s: %s" % (type(exc).__name__, exc)
    if raised is not None:
        problems = ["raised %s: %s" % (type(raised).__name__, raised)]
    else:
        problems = polarity(text, False, must, must_not, extract)
    return suite.record("O", cid, problems,
                        detail=list(detail) + [
                            "params       : %s" % (params,),
                            "must find    : %s" % (", ".join(must) or "-"),
                            "must not find: %s" % (", ".join(must_not) or "-"),
                            "reported     : %s"
                            % (", ".join(sorted(extract(text))) or "-")],
                        text=text, showable=True)


def group_o(suite, root):
    """Character classes mean the same thing on both sides of find_file's `/`."""
    mod = H.load_module_from_path("mcp_purity_classes", SERVER)
    ff = mod.handle_find_file

    # -- the divergence itself: the path-style branch honours the class -------
    record_o(
        suite, "path-class-matches", ff, {"file_mask": "src/[ab].py"}, root,
        must=["src/a.py", "src/b.py"],
        must_not=["src/c.py", "a.py", "b.py", "src/deep/a.py"],
        detail=["the declared divergence: `src/[ab].py` must answer what",
                "`[ab].py` answers, scoped to `src/` -- not a literal `[ab]`"])
    record_o(
        suite, "path-negated-class", ff, {"file_mask": "src/[!a].py"}, root,
        must=["src/b.py", "src/c.py"], must_not=["src/a.py"],
        detail=["`[!a]` is fnmatch's negation; it must not be read as a",
                "literal `!` any more than `[ab]` is a literal pair"])
    record_o(
        suite, "path-range-class", ff, {"file_mask": "src/[a-b].py"}, root,
        must=["src/a.py", "src/b.py"], must_not=["src/c.py"],
        detail=["a range is a class too; `re.escape` turned its `-` into a",
                "literal hyphen"])
    record_o(
        suite, "path-class-in-dir-segment", ff, {"file_mask": "pkg[12]/m.py"},
        root, must=["pkg1/m.py", "pkg2/m.py"], must_not=["pkg3/m.py"],
        detail=["a class in a DIRECTORY segment, not only in the basename"])
    record_o(
        suite, "globstar-then-class", ff, {"file_mask": "**/[ab].py"}, root,
        must=["a.py", "b.py", "src/a.py", "src/b.py", "src/deep/a.py"],
        must_not=["c.py", "src/c.py", "lit/[ab].py"],
        detail=["globstar and a class in one mask: `**/` still means any",
                "depth INCLUDING zero (ADR 0017), and the class still bites"])

    # -- must-stay-green: the widening must not over-reach ---------------------
    record_o(
        suite, "bare-class-control", ff, {"file_mask": "[ab].py"}, root,
        must=["a.py", "b.py", "src/a.py", "src/b.py", "src/deep/a.py"],
        must_not=["c.py", "src/c.py"],
        detail=["CONTROL: the bare-mask branch (fnmatch on the basename)",
                "already honoured classes; it is the reference answer"])
    record_o(
        suite, "class-never-crosses-slash", ff,
        {"file_mask": "**/src[!x]a.py"}, root,
        must=[], must_not=["src/a.py"],
        detail=["CONTROL: in the path-style branch `*` and `?` never match",
                "`/`, so a negated class must not either -- fnmatch's own",
                "`[!x]` WOULD match `/`, and `src[!x]a.py` must not reach",
                "`src/a.py` by spending its class on the separator"])
    record_o(
        suite, "unclosed-bracket-is-literal", ff, {"file_mask": "lit/[x.py"},
        root, must=["lit/[x.py"], must_not=[],
        detail=["CONTROL: an unclosed `[` is a literal in fnmatch and stays",
                "one here -- no re.error, no swallowed tail"])
    record_o(
        suite, "literal-bracket-name-via-escape", ff,
        {"file_mask": "lit/[[]ab].py"}, root,
        must=["lit/[ab].py"], must_not=[],
        detail=["a filename that CARRIES brackets is reached with fnmatch's",
                "escape `[[]`, in the path-style branch exactly as in the",
                "bare one (`[[]ab].py`); no literal-bracket spelling was ever",
                "promised by ADR 0017 or the docs, so none is kept"])

    # -- brace alternation is still refused beside a class --------------------
    try:
        ff({"file_mask": "src/[ab].{py,txt}"}, root)
        raised = None
    except Exception as exc:                                     # noqa: BLE001
        raised = exc
    problems = []
    if raised is None:
        problems.append("expected ValueError, call was accepted")
    elif not isinstance(raised, ValueError):
        problems.append("expected ValueError, raised %s: %s"
                        % (type(raised).__name__, raised))
    elif "brace" not in str(raised).lower():
        problems.append("refusal does not mention 'brace'")
    suite.record(
        "O", "brace-beside-class-still-refused", problems,
        detail=["CONTROL: honouring `[...]` must not let `{a,b}` through --",
                "ADR 0017's refusal runs before the translator is reached",
                "outcome: %s" % (raised if raised is not None else "accepted")])

    # -- the other two engines already agreed: controls, green before ---------
    record_o(
        suite, "search-include-class-control", mod.handle_search_for_pattern,
        {"substring_pattern": NEEDLE, "paths_include_glob": "src/[ab].py"},
        root, must=["src/a.py", "src/b.py"], must_not=["src/c.py", "a.py"],
        extract=search_paths,
        detail=["CONTROL: search_for_pattern's `_glob_matches` is fnmatch and",
                "honoured the class before R-0012; find_file now agrees"])
    record_o(
        suite, "list_dir-filter-class-control", mod.handle_list_dir,
        {"filter": "[ab].py", "relative_path": "src"}, root,
        must=["src/a.py", "src/b.py"], must_not=["src/c.py"],
        extract=lambda t: {p if p.startswith("src/") else "src/" + p
                           for p in listing_paths(t)},
        detail=["CONTROL: list_dir's filter is a bare fnmatch name and has",
                "no path-style branch at all; it honoured the class already"])


# ---------------------------------------------------------------------------
# Group P -- a catastrophic regex is bounded, not a residual (R-0016)
#
# `(a+)+$` against forty `a`s and a `!` backtracks ~2^40 times.  CPython's re
# cannot be interrupted from Python code, and on 3.9 it holds the GIL for the
# whole match, so the old in-loop deadline never got a turn: the call never
# returned and the server answered nothing else either.  Every catastrophic row
# runs on its OWN fresh server with a harness-side timeout (P_RPC_TIMEOUT)
# shorter than the suite's, so a regression FAILS in seconds instead of hanging
# the suite, and the elapsed time is asserted against P_BOUND_SECS -- the
# stated bound: the server's 5 s search budget plus spawn and kill slack.  The
# four catastrophic rows are the four places the scan applies the regex:
# content, content with context, count (search without a row) and
# only_matching (finditer).  Each then asks the SAME server a normal search:
# a runaway that was abandoned rather than killed would still be spinning.
#
# The controls pin what an out-of-process matcher has to reproduce exactly:
# `$` before a line's `\n`, a CRLF line, a last line with no newline, a form
# feed that is NOT a line break, and a non-ASCII match -- plus literal mode
# and the pattern-length ceiling, the guard that existed before this group.
# ---------------------------------------------------------------------------

RD_EVIL = "evil.txt"
RD_NORM = "norm.txt"
RD_EVIL_PATTERN = "(a+)+$"
RD_EVIL_LINE = "a" * 40 + "!\n"
RD_NORM_BYTES = ("alpha here\n"
                 "beta here\r\n"
                 "gamma été here\n"
                 "form\x0cfeed here\n"
                 "last here").encode("utf-8")
RD_NORM_ROWS = ["%s:1: alpha here" % RD_NORM,
                "%s:2: beta here" % RD_NORM,
                "%s:3: gamma été here" % RD_NORM,
                "%s:4: form\x0cfeed here" % RD_NORM,
                "%s:5: last here" % RD_NORM]
P_RPC_TIMEOUT = 20.0
P_BOUND_SECS = 12.0


def make_redos_fixture(ws, subdir):
    """Group P's tree: one catastrophic line, one line-shape zoo."""
    ws.subdir(subdir)
    ws.write_text(os.path.join(subdir, RD_EVIL), RD_EVIL_LINE)
    ws.write_bytes(os.path.join(subdir, RD_NORM), RD_NORM_BYTES)
    return os.path.realpath(ws.join(subdir))


def rows_exact(text):
    """`path:line: text` rows split on `\\n` ONLY -- str.splitlines() would cut
    the form-feed row in two, which is the very thing a row pins."""
    return [row for row in text.split("\n") if RX_MATCH_ROW.match(row)]


def record_bounded(suite, cid, root, params, detail=()):
    """A catastrophic search on a FRESH server must come back as an error
    within P_BOUND_SECS, and the same server must answer a normal search
    afterwards."""
    drv = Driver(root, timeout=P_RPC_TIMEOUT)
    problems = []
    try:
        t0 = time.monotonic()
        is_error, text = drv.call("search_for_pattern", params)
        elapsed = time.monotonic() - t0
        low = text.lower()
        if text.startswith("DRIVER-ERROR"):
            problems.append("no reply within the %.0fs harness timeout (%s)"
                            % (P_RPC_TIMEOUT, text[:160]))
        elif not is_error:
            problems.append("expected an error, call was accepted")
        else:
            for token in ("time budget", "backtrack", "regex:false"):
                if token not in low:
                    problems.append("error text does not mention %r" % token)
        if elapsed > P_BOUND_SECS:
            problems.append("took %.1fs, bound is %.0fs"
                            % (elapsed, P_BOUND_SECS))
        after = "(not asked: the first call never returned)"
        if not text.startswith("DRIVER-ERROR"):
            a_err, after = drv.call("search_for_pattern", {
                "substring_pattern": "here$", "relative_path": RD_NORM,
                "output_mode": "count"})
            if a_err or "%s: 5" % RD_NORM not in [
                    ln.strip() for ln in after.split("\n")]:
                problems.append("server did not answer a normal search "
                                "afterwards: %s" % after.strip()[:200])
    finally:
        drv.close()
    return suite.record("P", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "elapsed: %.2fs (bound %.0fs)"
                            % (elapsed, P_BOUND_SECS),
                            "reply  : %s" % text.strip()[:300],
                            "after  : %s" % after.strip()[:200]],
                        text=text, showable=True)


def record_norm(suite, cid, drv, params, want_rows=None, want_lines=(),
                want_text=None, detail=()):
    """A normal search must be answered exactly as before the bound existed."""
    t0 = time.monotonic()
    is_error, text = drv.call("search_for_pattern", params)
    elapsed = time.monotonic() - t0
    problems = []
    if is_error:
        problems.append("server returned an error")
    if want_rows is not None and rows_exact(text) != list(want_rows):
        problems.append("rows %r, want %r" % (rows_exact(text), list(want_rows)))
    lines = [ln.strip() for ln in text.split("\n")]
    for want in want_lines:
        if want not in lines:
            problems.append("no line equals %r" % want)
    if want_text is not None and want_text not in text:
        problems.append("reply does not contain %r" % want_text)
    return suite.record("P", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "elapsed: %.2fs" % elapsed,
                            "reply  : %s" % text.strip()[:400]],
                        text=text, showable=True)


def group_p(suite, drv, root):
    evil = {"substring_pattern": RD_EVIL_PATTERN, "relative_path": RD_EVIL}
    record_bounded(
        suite, "catastrophic-content-bounded", root, dict(evil),
        detail=["the default content mode: one pattern.search per line"])
    record_bounded(
        suite, "catastrophic-context-bounded", root,
        dict(evil, context_lines=1),
        detail=["the context branch reads the file whole before matching"])
    record_bounded(
        suite, "catastrophic-count-bounded", root,
        dict(evil, output_mode="count"),
        detail=["count mode: a search per line that renders no row"])
    record_bounded(
        suite, "catastrophic-only_matching-bounded", root,
        dict(evil, only_matching=True),
        detail=["only_matching drives finditer, not search"])

    norm = {"relative_path": RD_NORM}
    record_norm(
        suite, "dollar-crlf-eof-formfeed-nonascii", drv,
        dict(norm, substring_pattern="here$"), want_rows=RD_NORM_ROWS,
        detail=["CONTROL: `$` matches before each line's `\\n`; the CRLF",
                "line is one line; the unterminated last line matches; the",
                "form feed does not split line 4; the non-ASCII line survives"])
    record_norm(
        suite, "only_matching-nonascii-exact", drv,
        dict(norm, substring_pattern=r"\w+(?= here)", only_matching=True),
        want_rows=["%s:1: alpha" % RD_NORM, "%s:2: beta" % RD_NORM,
                   "%s:3: été" % RD_NORM, "%s:4: feed" % RD_NORM,
                   "%s:5: last" % RD_NORM],
        detail=["CONTROL: finditer's matches, a Unicode \\w among them"])
    record_norm(
        suite, "zero-length-only_matching-control", drv,
        dict(norm, substring_pattern="z*", only_matching=True),
        want_rows=[],
        want_lines=["0 match(es) on 0 line(s), one row per match"],
        detail=["CONTROL: `z*` matches only the empty string: no rows"])
    record_norm(
        suite, "context-lines-control", drv,
        dict(norm, substring_pattern="^beta", context_lines=1),
        want_text="%s:2:\nalpha here\nbeta here\ngamma été here"
                  % RD_NORM,
        detail=["CONTROL: the context branch's line numbers and window"])
    record_norm(
        suite, "count-control", drv,
        dict(norm, substring_pattern="here$", output_mode="count"),
        want_lines=["%s: 5" % RD_NORM],
        detail=["CONTROL: count mode"])
    record_norm(
        suite, "files_with_matches-control", drv,
        {"substring_pattern": "^beta", "output_mode": "files_with_matches"},
        want_lines=[RD_NORM],
        detail=["CONTROL: a whole-root walk; evil.txt does not match"])
    record_norm(
        suite, "literal-mode-evil-pattern-fast", drv,
        dict(evil, regex=False), want_rows=[], want_lines=["0 match(es)"],
        detail=["CONTROL: regex:false escapes the pattern, which is then",
                "linear: the same text is answered, not refused"])
    record_error(
        suite, "P", "pattern-length-ceiling-still-refused", drv,
        "search_for_pattern",
        dict(norm, substring_pattern="a" * 1001),
        must_say=["too long", "1000"],
        detail=["CONTROL: the length ceiling that predates this group"])


# ---------------------------------------------------------------------------
# Group Q -- replace_content's regex mode is bounded too
#
# Group P bounded search_for_pattern; replace_content's `mode:"regex"` still
# ran re.finditer and re.sub in-process on up to 10 MB, so the same `(a+)+$`
# froze the whole server there.  Every catastrophic row runs on its OWN fresh
# server under the same harness timeout as group P, asserts the error names
# the budget and `mode:"literal"`, asserts the file's BYTES are unchanged (a
# replace that gave up must not have written anything), and then asks the SAME
# server for a normal replace.  The controls pin replace_content's semantics
# as they were: backrefs (numbered and named), zero-length matches counted the
# way re.sub counts them, the zero / one / many-without-allow_multiple
# answers, a bad group reference, and literal mode -- which stays in-process
# and answers the evil needle at once.
# ---------------------------------------------------------------------------

RQ_EVIL = "evil.txt"
RQ_EVIL_BYTES = ("a" * 40 + "!\n").encode("utf-8")


def make_replace_fixture(ws, subdir):
    """Group Q's tree: only the catastrophic file; the controls write their
    own file first, because every accepted replace mutates it."""
    ws.subdir(subdir)
    ws.write_bytes(os.path.join(subdir, RQ_EVIL), RQ_EVIL_BYTES)
    return os.path.realpath(ws.join(subdir))


def _q_write(root, rel, data):
    with open(os.path.join(root, rel), "wb") as fh:
        fh.write(data.encode("utf-8"))


def _q_read(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read()


def record_rc_bounded(suite, cid, root, params, detail=()):
    """A catastrophic replace on a FRESH server: an error within P_BOUND_SECS,
    the file untouched, and the same server still replacing afterwards."""
    drv = Driver(root, timeout=P_RPC_TIMEOUT)
    problems = []
    elapsed = 0.0
    after = "(not asked: the first call never returned)"
    try:
        t0 = time.monotonic()
        is_error, text = drv.call("replace_content", params)
        elapsed = time.monotonic() - t0
        low = text.lower()
        if text.startswith("DRIVER-ERROR"):
            problems.append("no reply within the %.0fs harness timeout (%s)"
                            % (P_RPC_TIMEOUT, text[:160]))
        elif not is_error:
            problems.append("expected an error, call was accepted")
        else:
            for token in ("time budget", "backtrack", 'mode:"literal"'):
                if token not in low:
                    problems.append("error text does not mention %r" % token)
        if elapsed > P_BOUND_SECS:
            problems.append("took %.1fs, bound is %.0fs"
                            % (elapsed, P_BOUND_SECS))
        if _q_read(root, RQ_EVIL) != RQ_EVIL_BYTES:
            problems.append("the file was modified by a replace that failed")
        if not text.startswith("DRIVER-ERROR"):
            rel = "after-%s.txt" % cid
            _q_write(root, rel, "one two\n")
            a_err, after = drv.call("replace_content", {
                "relative_path": rel, "needle": r"(\w+) (\w+)",
                "repl": r"\2 \1", "mode": "regex"})
            if a_err or _q_read(root, rel) != b"two one\n":
                problems.append("server did not answer a normal replace "
                                "afterwards: %s" % after.strip()[:200])
    finally:
        drv.close()
    return suite.record("Q", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "elapsed: %.2fs (bound %.0fs)"
                            % (elapsed, P_BOUND_SECS),
                            "reply  : %s" % text.strip()[:300],
                            "after  : %s" % after.strip()[:200]],
                        text=text, showable=True)


def record_rc(suite, cid, drv, before, params, want_error=False,
              want_after=None, must_say=(), detail=()):
    """Write *before* to params' file, replace, then assert the reply AND the
    bytes on disk: an error must leave *before* untouched."""
    root, rel = drv.root, params["relative_path"]
    _q_write(root, rel, before)
    t0 = time.monotonic()
    is_error, text = drv.call("replace_content", params)
    elapsed = time.monotonic() - t0
    got = _q_read(root, rel).decode("utf-8")
    problems = []
    if want_error and not is_error:
        problems.append("expected an error, call was accepted")
    if not want_error and is_error:
        problems.append("expected acceptance, got an error")
    for token in must_say:
        if token.lower() not in text.lower():
            problems.append("reply does not mention %r" % token)
    want = before if want_error else want_after
    if want is not None and got != want:
        problems.append("file is %r, want %r" % (got, want))
    return suite.record("Q", cid, problems,
                        detail=list(detail) + [
                            "params : %s" % (params,),
                            "elapsed: %.2fs" % elapsed,
                            "reply  : %s" % text.strip()[:300],
                            "file   : %r" % got[:200]],
                        text=text, showable=True)


def group_q(suite, drv, root):
    evil = {"relative_path": RQ_EVIL, "needle": "(a+)+$", "repl": "x",
            "mode": "regex"}
    record_rc_bounded(
        suite, "catastrophic-replace-bounded", root, dict(evil),
        detail=["the default: count the matches, refuse or substitute"])
    record_rc_bounded(
        suite, "catastrophic-replace-multi-bounded", root,
        dict(evil, allow_multiple_occurrences=True),
        detail=["allow_multiple_occurrences: the substitution path"])

    rx = {"relative_path": "ctl.txt", "mode": "regex"}
    record_rc(
        suite, "backref-numbered-multi", drv, "foo=1\nbar=2\nété=3\n",
        dict(rx, needle=r"(\w+)=(\d)", repl=r"\2=\1",
             allow_multiple_occurrences=True),
        want_after="1=foo\n2=bar\n3=été\n", must_say=["Replaced 3"],
        detail=["CONTROL: numbered backrefs, a Unicode \\w, three matches"])
    record_rc(
        suite, "backref-named-one", drv, "key: value\nother\n",
        dict(rx, needle=r"(?m)^(?P<k>\w+): (?P<v>\w+)$",
             repl=r"\g<v>: \g<k>"),
        want_after="value: key\nother\n", must_say=["Replaced 1"],
        detail=["CONTROL: named backrefs and an inline (?m) flag"])
    record_rc(
        suite, "many-without-allow_multiple-refused", drv, "ab ab ab\n",
        dict(rx, needle="ab", repl="X"), want_error=True,
        must_say=["Multiple matches (3)", "allow_multiple_occurrences"],
        detail=["CONTROL: the count check still refuses, file untouched"])
    record_rc(
        suite, "zero-matches-refused", drv, "nothing here\n",
        dict(rx, needle=r"\d+", repl="N"), want_error=True,
        must_say=["Pattern not found"],
        detail=["CONTROL: no match is an error, file untouched"])
    record_rc(
        suite, "zero-length-matches-counted", drv, "ab",
        dict(rx, needle="x*", repl="-", allow_multiple_occurrences=True),
        want_after="-a-b-", must_say=["Replaced 3"],
        detail=["CONTROL: re.sub's empty-match rule, and a count that agrees"])
    record_rc(
        suite, "bad-group-reference-refused", drv, "abc\n",
        dict(rx, needle="(b)", repl=r"\2"), want_error=True,
        must_say=["group"],
        detail=["CONTROL: an invalid backref is an error, file untouched"])
    record_rc(
        suite, "bad-pattern-refused", drv, "abc\n",
        dict(rx, needle="(b", repl="x"), want_error=True,
        detail=["CONTROL: a pattern that does not compile"])
    record_rc(
        suite, "pipe-escape-still-alternation", drv, "cat dog\n",
        dict(rx, needle=r"cat\|dog", repl="pet",
             allow_multiple_occurrences=True),
        want_after="pet pet\n", must_say=["Replaced 2"],
        detail=["CONTROL: the `\\|` -> `|` rewrite regex mode always did"])
    record_rc(
        suite, "literal-mode-parens", drv, "f(a+)+$ g\n",
        {"relative_path": "ctl.txt", "needle": "(a+)+$", "repl": "Z"},
        want_after="fZ g\n", must_say=["Replaced 1"],
        detail=["CONTROL: literal mode (the default) takes the text as-is"])
    t0 = time.monotonic()
    is_error, text = drv.call("replace_content", {
        "relative_path": RQ_EVIL, "needle": "(a+)+$", "repl": "x",
        "mode": "literal"})
    elapsed = time.monotonic() - t0
    problems = []
    if not is_error or "needle not found" not in text.lower():
        problems.append("expected 'Needle not found', got: %s" % text[:200])
    if elapsed > 2.0:
        problems.append("literal mode took %.1fs" % elapsed)
    if _q_read(root, RQ_EVIL) != RQ_EVIL_BYTES:
        problems.append("the file was modified")
    suite.record("Q", "literal-mode-evil-needle-fast", problems,
                 detail=["CONTROL: literal mode stays in-process and linear",
                         "elapsed: %.2fs" % elapsed,
                         "reply  : %s" % text.strip()[:300]],
                 text=text, showable=True)


# ---------------------------------------------------------------------------
# Group R -- the literal fast path and the honest budget message
#
# The reported call: `substring_pattern: "release/ngs-"` over a large out-of-
# root tree answered "the pattern may backtrack catastrophically", blaming
# whichever file happened to be in flight when the call's ONE total 5 s budget
# ran out.  Two separate defects, both gated here, in-process (the clock seam
# `_search_clock` and the matcher class are module attributes, so a row can
# swap them without a server child):
#
#   1. a pattern holding NO regex metacharacter is a literal, so it is matched
#      in-process like regex:false -- no child, no pipe round trip per file --
#      and its answer must be BYTE-IDENTICAL to the worker's (rows R2-R4);
#   2. a total-budget overrun says so, with the file count and the way out;
#      backtracking is blamed only when the file in flight alone used a large
#      share of the budget (rows R5-R8).
# ---------------------------------------------------------------------------

FP_FILES = (
    ("rel.txt", "branch release/ngs-1.2 here\nno match\n"
                "release/ngs- twice release/ngs-\n"),
    ("dash.txt", "a-b/c %s\n" % NEEDLE),
    ("uni.txt", "été release/ngs-é\nlast release/ngs-"),
)
FP_CRLF = ("crlf.txt", b"release/ngs-x\r\nfoo\r\nform\x0crelease/ngs-\r\n")
FP_MANY = 30                                # files under many/ (budget rows)


def make_fastpath_fixture(ws, subdir):
    """Group R's tree, REALPATH'd for the same reason group G's is."""
    ws.subdir(subdir)
    for rel, body in FP_FILES:
        ws.write_text(os.path.join(subdir, rel), body)
    ws.write_bytes(os.path.join(subdir, FP_CRLF[0]), FP_CRLF[1])
    for i in range(FP_MANY):
        ws.write_text(os.path.join(subdir, "many", "f%02d.txt" % i), LINE)
    return os.path.realpath(ws.join(subdir))


class StepClock:
    """A monotonic clock that advances *step* seconds on every read."""

    def __init__(self, start=0.0, step=0.5):
        self.now, self.step = start - step, step

    def __call__(self):
        self.now += self.step
        return self.now


class SeqClock:
    """A clock answering *values* in order, then the last one forever."""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _budget_raise(fn):
    """(exception or None, text) of calling *fn*."""
    try:
        return None, handler_text(fn())
    except Exception as exc:                                     # noqa: BLE001
        return exc, "%s: %s" % (type(exc).__name__, exc)


def group_r(suite, root):
    mod = H.load_module_from_path("mcp_purity_fastpath", SERVER)
    search = mod.handle_search_for_pattern
    real_worker = mod._RegexWorker
    spawned = []

    class RecordingWorker(real_worker):
        def _start(self):
            spawned.append(self._pattern)
            super()._start()

    mod._RegexWorker = RecordingWorker

    # -- R1: the metacharacter set, spelled out --------------------------------
    plain = ("release/ngs-", "a-b/c", "foo bar", "été", "x#y", "a,b",
             "<tag>", "k=v;", "NEEDLE_ALPHA", "~/'\"%&!@")
    meta = tuple("a%sb" % c for c in ".^$*+?{}[]()|\\")
    pred = getattr(mod, "_is_plain_literal", None)
    problems = []
    if pred is None:
        problems.append("Scripts/mcp-purity.py has no _is_plain_literal")
    else:
        problems += ["%r judged a regex, it is a literal" % p
                     for p in plain if not pred(p)]
        problems += ["%r judged a literal, it holds a metacharacter" % p
                     for p in meta if pred(p)]
    suite.record("R", "metachar-set-unit", problems,
                 detail=["`-`, `/`, space, `#`, `,` are literal at the top level",
                         "of a pattern (re.escape escapes several of them, which",
                         "is why the set is not derived from it); every one of",
                         ". ^ $ * + ? { } [ ] ( ) | \\ makes a regex"])

    # -- R2/R3: which matcher runs ---------------------------------------------
    del spawned[:]
    exc, text = _budget_raise(lambda: search(
        {"substring_pattern": "release/ngs-"}, root))
    problems = ["raised %s" % exc] if exc else []
    if spawned:
        problems.append("a regex worker child was spawned for a plain literal")
    if not search_paths(text):
        problems.append("no rows at all: the fast path matched nothing")
    suite.record("R", "plain-literal-no-worker", problems,
                 detail=["regex:true (the default) with no metacharacter: the",
                         "pattern is matched in-process, like regex:false",
                         "worker spawns: %d | rows: %s"
                         % (len(spawned), sorted(search_paths(text)))],
                 text=text, showable=True)
    del spawned[:]
    exc, text = _budget_raise(lambda: search(
        {"substring_pattern": "release/ngs-.+"}, root))
    suite.record("R", "metachar-pattern-uses-worker",
                 (["raised %s" % exc] if exc else [])
                 + ([] if spawned else ["no worker spawned for a real regex"]),
                 detail=["CONTROL: a pattern holding `.` and `+` still runs in",
                         "the killable child (the ReDoS guard is untouched)"],
                 text=text, showable=True)

    # -- R4: byte-identical answers --------------------------------------------
    problems, compared = [], 0
    for pat in ("release/ngs-", "a-b/c", "é", NEEDLE):
        for mode in ({}, {"output_mode": "count"},
                     {"output_mode": "files_with_matches"},
                     {"only_matching": True}, {"context_lines": 1},
                     {"head_limit": 2, "offset": 1}):
            params = dict(mode, substring_pattern=pat)
            del spawned[:]
            exc_f, fast = _budget_raise(lambda: search(dict(params), root))
            fast_spawned = len(spawned)
            orig = getattr(mod, "_is_plain_literal", None)
            mod._is_plain_literal = lambda s: False
            try:
                del spawned[:]
                exc_w, slow = _budget_raise(lambda: search(dict(params), root))
            finally:
                if orig is not None:
                    mod._is_plain_literal = orig
                else:
                    del mod._is_plain_literal
            compared += 1
            if exc_f or exc_w:
                problems.append("%r %s raised: %s / %s" % (pat, mode, exc_f, exc_w))
            elif fast_spawned:
                problems.append("%r %s: the fast path spawned a worker"
                                % (pat, mode))
            elif not spawned:
                problems.append("%r %s: VACUOUS -- the forced-worker run spawned "
                                "nothing" % (pat, mode))
            elif fast != slow:
                problems.append("%r %s differ:\n  fast  : %r\n  worker: %r"
                                % (pat, mode, fast[:300], slow[:300]))
    suite.record("R", "fast-path-identical-to-worker", problems[:8],
                 detail=["%d pattern x mode pairs, each run twice: once on the"
                         % compared,
                         "fast path, once with _is_plain_literal forced False",
                         "so the SAME pattern goes through the child -- CRLF,",
                         "form feed, non-ASCII, a last line with no newline,",
                         "context, count, files, only_matching and paging"])

    # -- R5/R6: a total-budget overrun over many files --------------------------
    rx_files = re.compile(r"after (?:scanning )?(\d+) file\(s\)")
    for cid, pat, label in (
            ("budget-total-literal-many-files", NEEDLE, "fast path"),
            ("budget-total-regex-many-files", "NEEDLE_A.PHA", "regex worker")):
        clock = StepClock(0.0, 0.5)
        orig_clock = getattr(mod, "_search_clock", None)
        mod._search_clock = clock
        try:
            exc, text = _budget_raise(lambda: search(
                {"substring_pattern": pat, "relative_path": "many"}, root))
        finally:
            if orig_clock is not None:
                mod._search_clock = orig_clock
        low = str(exc).lower() if exc else ""
        problems = []
        if orig_clock is None:
            problems.append("Scripts/mcp-purity.py has no _search_clock seam")
        if not isinstance(exc, ValueError):
            problems.append("expected ValueError, got %s" % (text[:200],))
        else:
            for token in ("total", "time budget", "relative_path",
                          "paths_include_glob"):
                if token not in low:
                    problems.append("message does not mention %r" % token)
            if "backtrack" in low:
                problems.append("message blames backtracking for a WIDE walk")
            m = rx_files.search(low)
            if not m:
                problems.append("message does not say after how many files")
            elif not 0 < int(m.group(1)) < FP_MANY:
                problems.append("file count %s is not inside 1..%d"
                                % (m.group(1), FP_MANY - 1))
        suite.record("R", cid, problems,
                     detail=["%s, a clock advancing 0.5 s per read over %d"
                             % (label, FP_MANY),
                             "files: the budget runs out mid-walk with no file",
                             "slow, so the honest cause is the WIDTH of the walk",
                             "reply: %s" % text[:300]],
                     text=text, showable=True)

    # -- R7/R8: the worker's own overrun, classified by the in-flight share -----
    for cid, values, want_backtrack in (
            ("budget-worker-short-inflight-total", [4.9, 5.1], False),
            ("budget-worker-long-inflight-backtrack", [1.0, 5.1], True)):
        orig_clock = getattr(mod, "_search_clock", None)
        mod._search_clock = SeqClock(values)
        worker = None
        try:
            try:
                worker = real_worker(
                    "x+", 5.0, budget=5.0,
                    total_budget_message=lambda where, used:
                        mod._search_budget_message(3, 5.0, (where, used)))
                exc, text = _budget_raise(
                    lambda: worker.search_lines(["xxx\n"], "f.txt"))
            except Exception as e:                               # noqa: BLE001
                exc, text = e, "%s: %s" % (type(e).__name__, e)
        finally:
            if worker is not None:
                worker.close()
            if orig_clock is not None:
                mod._search_clock = orig_clock
        low = str(exc).lower() if exc else ""
        problems = []
        if not isinstance(exc, ValueError):
            problems.append("expected ValueError, got %s" % text[:200])
        elif want_backtrack:
            for token in ("backtrack", "regex:false", "f.txt"):
                if token not in low:
                    problems.append("message does not mention %r" % token)
        else:
            if "backtrack" in low:
                problems.append("blamed backtracking for a file that had used "
                                "0.2 s of a 5 s budget")
            for token in ("total", "after scanning 3 file(s)"):
                if token not in low:
                    problems.append("message does not mention %r" % token)
        suite.record("R", cid, problems,
                     detail=["file sent at t=%.1f, deadline 5.0 noticed at t=%.1f"
                             % (values[0], values[1]),
                             "in flight %.1f s of a 5 s budget -> %s"
                             % (values[1] - values[0],
                                "backtracking blamed" if want_backtrack
                                else "the TOTAL budget named"),
                             "reply: %s" % text[:300]],
                     text=text, showable=True)

    mod._RegexWorker = real_worker


# ---------------------------------------------------------------------------
# Group S -- nested repositories and a foreign tree's own .gitignore
#
# The reported call searched `<other repo>/.claude` from a session rooted in a
# different project.  The walk descended into `.claude/worktrees/<name>/` -- a
# git worktree, whose `.git` is a FILE, so the `.git`-named prune never fired --
# and the other repo's .gitignore was never read, because only the PROJECT
# root's is.  Now a directory BELOW the search root holding a `.git` file or
# directory is skipped unless the ignore filter is off, and an out-of-root
# search root takes its ignore rules from its OWN git toplevel, paths measured
# from there.
# ---------------------------------------------------------------------------

NS_GITIGNORE = ("projonly",)
NS_FILES = ("top.txt", "plain/x.txt", "projonly/p.txt", "wt/inner.txt",
            "wt/deeper/d.txt", "sub/inner.txt")
NS_GITFILES = ("wt/.git", "wt/deeper/.git")       # worktree-style .git FILES
NS_GITDIR = "sub/.git/HEAD"                       # a submodule/clone .git DIR
FG_GITIGNORE = ("gen", "*.log", ".claude")
FG_FILES = ("src/keep.txt", "src/x.log", "gen/out.txt", "projonly/f.txt",
            ".claude/tmp/t.txt", ".claude/other/o.txt", "vendor/lib/v.txt")


def make_nested_fixture(ws, proj, foreign):
    """Group S's project root and its out-of-root sibling, both REALPATH'd."""
    ws.subdir(proj)
    ws.write_text(os.path.join(proj, ".gitignore"),
                  "\n".join(NS_GITIGNORE) + "\n")
    for rel in NS_FILES:
        ws.write_text(os.path.join(proj, rel), LINE)
    for rel in NS_GITFILES:
        ws.write_text(os.path.join(proj, rel), "gitdir: /nonexistent\n")
    ws.write_text(os.path.join(proj, NS_GITDIR), "ref: refs/heads/main\n")
    ws.subdir(foreign)
    ws.write_text(os.path.join(foreign, ".git", "HEAD"), "ref: refs/heads/main\n")
    ws.write_text(os.path.join(foreign, ".gitignore"),
                  "\n".join(FG_GITIGNORE) + "\n")
    for rel in FG_FILES:
        ws.write_text(os.path.join(foreign, rel), LINE)
    ws.write_text(os.path.join(foreign, "vendor", "lib", ".git"),
                  "gitdir: /nonexistent\n")
    return os.path.realpath(ws.join(proj)), os.path.realpath(ws.join(foreign))


def group_s(suite, drv, foreign_root):
    rec = lambda cid, params, must, must_not, detail: record_polarity(  # noqa: E731
        suite, "S", cid, drv, "search_for_pattern",
        dict(params, substring_pattern=NEEDLE), must=must, must_not=must_not,
        detail=detail)
    nested = ["wt/inner.txt", "wt/deeper/d.txt", "sub/inner.txt"]

    rec("nested-repos-skipped-by-default", {},
        ["top.txt", "plain/x.txt"], nested + ["projonly/p.txt"],
        ["`wt/` holds a `.git` FILE (a worktree), `sub/` a `.git` DIRECTORY",
         "(a submodule or clone): both are another repository, skipped"])
    rec("no_ignore-true-reaches-nested-repos", {"no_ignore": True},
        ["top.txt", "plain/x.txt", "projonly/p.txt"] + nested, [],
        ["no_ignore=true turns the skip off with the rest of the filter"])
    rec("skip_ignored_files-false-reaches-nested", {"skip_ignored_files": False},
        nested, [],
        ["the canonical spelling of the same opt-out"])
    rec("nested-repo-as-root-is-searched", {"relative_path": "wt"},
        ["wt/inner.txt"], ["wt/deeper/d.txt"],
        ["the search ROOT is never pruned, even though it holds a `.git`;",
         "a nested repo BELOW it still is"])
    rec("nested-root-no_ignore-reaches-deeper",
        {"relative_path": "wt", "no_ignore": True},
        ["wt/inner.txt", "wt/deeper/d.txt"], [],
        ["CONTROL: the deeper file exists and is reachable"])

    up = os.path.join("..", os.path.basename(foreign_root))
    fg = lambda rel: os.path.join(up, rel)                         # noqa: E731
    rec("foreign-root-own-gitignore", {"relative_path": foreign_root},
        [fg("src/keep.txt"), fg("projonly/f.txt"), fg(".claude/tmp/t.txt")],
        [fg("src/x.log"), fg("gen/out.txt"), fg(".claude/other/o.txt"),
         fg("vendor/lib/v.txt")],
        ["an out-of-root root takes its ignore rules from ITS OWN git",
         "toplevel (`gen`, `*.log`, `.claude`), not from the project's",
         "(`projonly`, which must NOT hide the foreign `projonly/f.txt`);",
         "the nested repo `vendor/lib` (a `.git` file) is skipped too"])
    rec("foreign-subdir-root-measured-from-toplevel",
        {"relative_path": os.path.join(foreign_root, ".claude")},
        [fg(".claude/tmp/t.txt")], [fg(".claude/other/o.txt")],
        ["rooted at the foreign `.claude` -- the reported call's shape: the",
         "paths are measured from the foreign TOPLEVEL, so `.claude/other`",
         "inherits the `.claude` ignore and `.claude/tmp` stays exempt;",
         "measured from the search root, `other/` would have leaked"])
    rec("foreign-root-no_ignore-reaches-all",
        {"relative_path": foreign_root, "no_ignore": True},
        [fg(r) for r in FG_FILES], [],
        ["ANTI-VACUITY CONTROL: every foreign file the rows above hide",
         "exists and is reachable with the filter off"])


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
                          "its narrowness, the param contract, the glob "
                          "spellings that can only ever match nothing, and a "
                          "list of paths served as one paged result stream",
                    opts=opts, mode="stream", group_width=3, cid_width=36)

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    stderr_bytes = 0

    with H.TempWorkspace("ph-purity-file-ops-", keep=opts.keep) as ws:
        basename_root = make_fixture(ws, "basename", GITIGNORE_BASENAME)
        pathshaped_root = make_fixture(ws, "pathshaped", GITIGNORE_PATHSHAPED)
        glob_root = make_glob_fixture(ws, "globs")
        multi_root = make_multi_fixture(ws, "multiroot")
        read_root = make_read_fixture(ws, "readfile")
        git_root = make_git_fixture(ws, "gitwalk")
        outside_root = make_outside_fixture(ws, "outside")
        literal_root = make_literal_fixture(ws, "literal")
        onlymatch_root = make_onlymatch_fixture(ws, "onlymatch")
        suite.note("      server        : %s" % SERVER)
        suite.note("      fixture (A-D,J-L): %s  .gitignore=%s"
                   % (basename_root, list(GITIGNORE_BASENAME)))
        suite.note("      fixture (E)   : %s  .gitignore=%s"
                   % (pathshaped_root, list(GITIGNORE_PATHSHAPED)))
        suite.note("      fixture (G)   : %s  no .gitignore, files=%s"
                   % (glob_root, list(GLOB_FILES)))
        suite.note("      fixture (H)   : %s  no .gitignore, files=%s"
                   % (multi_root,
                      ["%s x%d" % (rel, n) for rel, n in MULTI_FILES]
                      + list(M_CFILES)))
        suite.note("      fixture (I)   : %s  files=[%s x%d lines]"
                   % (read_root, R_FILE, R_LINES))
        suite.note("      fixture (M)   : %s  no .gitignore, files=%s"
                   % (git_root, list(GIT_FILES)))
        suite.note("      fixture (K)   : %s  out-of-root, files=%s"
                   % (outside_root, [O_KEEP, O_ESCAPE, O_ESC + " -> symlink"]))
        suite.note("      fixture (D lit): %s  no .gitignore, files=%s"
                   % (literal_root, [rel for rel, _ in LITERAL_FILES]))
        suite.note("      fixture (N)   : %s  no .gitignore, files=%s"
                   % (onlymatch_root, [rel for rel, _ in ONLYMATCH_FILES]))

        drv = Driver(basename_root)
        drv_strict = Driver(basename_root, strict=True)
        drv_path = Driver(pathshaped_root)
        drv_multi = Driver(multi_root)
        drv_read = Driver(read_root)
        drv_git = Driver(git_root)
        drv_lit = Driver(literal_root)
        drv_om = Driver(onlymatch_root)
        try:
            group_a(suite, drv)
            group_b(suite, drv)
            group_c(suite, drv)
            group_d(suite, drv, drv_lit)
            group_e(suite, drv_path)
            group_h(suite, drv_multi, multi_root)
            group_i(suite, drv_read)
            group_j(suite, drv, basename_root)
            group_k(suite, drv, drv_strict, outside_root)
            group_l(suite, drv)
            group_m(suite, drv_git)
            group_n(suite, drv_om)
            stderr_bytes = (len(drv.stderr_text) + len(drv_path.stderr_text)
                            + len(drv_multi.stderr_text)
                            + len(drv_read.stderr_text)
                            + len(drv_git.stderr_text)
                            + len(drv_lit.stderr_text)
                            + len(drv_om.stderr_text)
                            + len(drv_strict.stderr_text))
        finally:
            drv_om.close()
            drv_lit.close()
            drv.close()
            drv_strict.close()
            drv_path.close()
            drv_multi.close()
            drv_read.close()
            drv_git.close()

        # Group G starts no child: it imports the server module and calls the
        # handlers in-process, so it runs outside the driver lifetime entirely.
        group_g(suite, glob_root)

        # Group O is in-process too, on its own tree (see CLASS_FILES).
        class_root = make_class_fixture(ws, "classes")
        suite.note("      fixture (O)   : %s  no .gitignore, files=%s"
                   % (class_root, list(CLASS_FILES)))
        group_o(suite, class_root)

        # Group P owns its servers: every catastrophic row starts a fresh one
        # (see record_bounded), the controls share drv_rd.
        redos_root = make_redos_fixture(ws, "redos")
        suite.note("      fixture (P)   : %s  no .gitignore, files=%s"
                   % (redos_root, [RD_EVIL, RD_NORM]))
        drv_rd = Driver(redos_root)
        try:
            group_p(suite, drv_rd, redos_root)
            stderr_bytes += len(drv_rd.stderr_text)
        finally:
            drv_rd.close()

        # Group Q likewise: a fresh server per catastrophic row, the controls
        # share drv_rq.
        replace_root = make_replace_fixture(ws, "replace")
        suite.note("      fixture (Q)   : %s  no .gitignore, files=%s"
                   % (replace_root, [RQ_EVIL]))
        drv_rq = Driver(replace_root)
        try:
            group_q(suite, drv_rq, replace_root)
            stderr_bytes += len(drv_rq.stderr_text)
        finally:
            drv_rq.close()

        # Group R is in-process (it swaps the clock seam and the matcher).
        fastpath_root = make_fastpath_fixture(ws, "fastpath")
        suite.note("      fixture (R)   : %s  no .gitignore, files=%s + "
                   "many/ x%d" % (fastpath_root,
                                  [rel for rel, _ in FP_FILES] + [FP_CRLF[0]],
                                  FP_MANY))
        group_r(suite, fastpath_root)

        # Group S: a project root holding nested repos, and a foreign git tree
        # beside it with its own .gitignore.
        nested_root, foreign_root = make_nested_fixture(ws, "nestproj",
                                                        "foreign")
        suite.note("      fixture (S)   : %s  .gitignore=%s, nested .git at %s"
                   % (nested_root, list(NS_GITIGNORE),
                      list(NS_GITFILES) + [os.path.dirname(NS_GITDIR)]))
        suite.note("      fixture (S fg): %s  own .git + .gitignore=%s"
                   % (foreign_root, list(FG_GITIGNORE)))
        drv_ns = Driver(nested_root)
        try:
            group_s(suite, drv_ns, foreign_root)
            stderr_bytes += len(drv_ns.stderr_text)
        finally:
            drv_ns.close()

        workspaces = [basename_root, pathshaped_root, glob_root, multi_root,
                      read_root, git_root, outside_root, literal_root,
                      onlymatch_root, class_root, redos_root, replace_root,
                      fastpath_root, nested_root, foreign_root]
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
