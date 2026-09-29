#!/usr/bin/env python3
"""Offline suite for the read-only kanban board over `roadmap-export/2`
(ClaudeCode/skills/roadmap/scripts/board.py, roadmap item R-0042).

board.py is a CONSUMER of the export contract in the p:roadmap SKILL.md: it
reads one export document and renders it, either as markdown on stdout (one
section per kanban column) or as one self-contained static HTML page.  It never
writes a roadmap file.  This suite gates the three things a consumer of that
contract is told are ITS job, plus the one write it does make:

  * the schema: anything that is not exactly `roadmap-export/2` is refused with
    one line and exit 2, never rendered on a guess;
  * the reverse `blocks` edges, which the export deliberately does not carry;
  * escaping: `title` and `why` are free text, and `why` is raw markdown.  In
    the markdown form a cell is escaped with ADR 0016's one vocabulary (`\\`
    FIRST, then `\\|`, `\\n`, `\\r`, `\\t`) and every rendered row is decoded
    back and compared with the value it was handed.  In the HTML form the data
    travels as JSON inside a `<script type="application/json">` block, with
    `<`, `>`, `&`, U+2028 and U+2029 written as `\\uXXXX`, so a planted
    `</script>` cannot close the block, and the page renders through Vue text
    interpolation only -- `v-html` must not occur anywhere in the page or the
    script;
  * `--out`: only a basename matching `roadmap-board-[a-z0-9-]+.html`, never
    through a symlink, and nothing written on a refusal.

The golden export is test_roadmap.py's GOLDEN_EXPORT, imported rather than
copied: one document is the contract, and a second copy would drift from it.
The expected markdown for it is written out by hand below.

Everything runs in a mkdtemp sandbox; the live docs/roadmap/ is digested before
and after (group F) because the board is declared read-only.  The case count
lives only in the SUITES table of tests/run.py.

Groups:
  A  the target exists; stdlib-only imports, no process spawn, no `v-html`
     in the source, the pinned Vue URL declared
  B  input: the schema refusal on every route (file, stdin), a malformed
     document or item refused with one line, a control character refused
  C  markdown: the golden board byte for byte, column order, reverse blocks
     edges, planted `|` / newline / backslash in a title and a why decoded
     back from their rows
  D  html: the data block round-trips, a planted `</script>` stays inert, no
     `v-html`, the pinned Vue build is the only external script, a noscript
     line, and nothing from the data is interpolated outside the data block
  E  --out: name and symlink refusals leave nothing behind, an accepted write
     replaces a regular file
  F  hygiene: the input file and the live roadmap are byte-unchanged, no
     bytecode

Usage:
  python3 tests/test_roadmap_board.py
  python3 tests/test_roadmap_board.py --brief
Exit code 0 iff every non-informational case passes.
"""

import ast
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402
from test_roadmap import GOLDEN_EXPORT  # noqa: E402

NAME = "roadmap_board"
TARGET = H.repo_path("ClaudeCode", "skills", "roadmap", "scripts", "board.py")
LIVE_ROADMAP_DIR = H.repo_path("docs", "roadmap")
LIVE_ARCHIVE_DIR = H.repo_path("docs", "roadmap", "archive")

GA = "A. contract + source gates"
GB = "B. input + schema refusal"
GC = "C. markdown board"
GD = "D. html board"
GE = "E. --out"
GF = "F. hygiene"

# The contract, spelled independently of the script: a test that imported
# these would pass a rename that broke every consumer.
SCHEMA = "roadmap-export/2"
COLUMNS = ("inbox", "later", "next", "now", "done", "dropped")
MD_HEADERS = ("id", "title", "state", "severity", "tags", "ready",
              "blocked_by", "blocks", "why")
VUE_URL = "https://unpkg.com/vue@3.4.21/dist/vue.global.prod.js"
STDLIB_ALLOWED = frozenset(("argparse", "json", "os", "re", "stat", "sys",
                            "tempfile", "unicodedata"))
SPAWN_NAMES = ("subprocess", "os.system", "os.popen", "os.exec", "os.spawn")

_RULE = "|" + "---|" * len(MD_HEADERS)
_HEAD = "| " + " | ".join(MD_HEADERS) + " |"

# GOLDEN_EXPORT rendered by hand.  Closed items sit in the column of their
# state; the inbox is the `unset` horizon; `ready` is empty for a closed item;
# R-0003's `blocks` is the reverse of R-0004's `blocked_by`.
EXPECTED_MD = "\n".join((
    "# Roadmap board",
    "",
    "source head: none; scope: open and closed; closed since: none; "
    "now WIP limit: 3",
    "",
    "## inbox (1)",
    "",
    _HEAD,
    _RULE,
    "| R-0005 | ADR authoring proposes deferred limits | idea |  | "
    "adr, producer | yes |  |  |  |",
    "",
    "## later (0)",
    "",
    "_(no items)_",
    "",
    "## next (1)",
    "",
    _HEAD,
    _RULE,
    "| R-0004 | Unify the skill and server git helpers | planned | low | "
    "scripts, wiki | no | R-0003 |  | Two copies of one helper drift; ADR "
    "0012 declined a shared transport tier, not this helper. |",
    "",
    "## now (1)",
    "",
    _HEAD,
    _RULE,
    "| R-0003 | Give _wikilib.git() the server's timeout | active |  | wiki "
    "| yes |  | R-0004 | The skill-side git helper passes stdin=DEVNULL but "
    "has no timeout; the server copy has both. |",
    "",
    "## done (1)",
    "",
    _HEAD,
    _RULE,
    "| R-0001 | Home the wiki page-type constants in _wikilib | done |  | "
    "wiki |  |  |  | Four page-type constants were typed in three files. |",
    "",
    "## dropped (1)",
    "",
    _HEAD,
    _RULE,
    "| R-0002 | P0-P3 priority labels | dropped |  |  |  |  |  | Proposed "
    "as a finer priority axis inside a lane. |",
    "",
))

# Planted values.  The markdown pair carries every character of the
# vocabulary, and the two-character sequence `\|` that turns a backslash-last
# escaper into a forged column (ADR 0016 Option 1).
MD_TITLE = "pipe | nl\nback \\ end \\| x"
MD_WHY = "w | a\nb \\ c\r\td \\n literal"
MARK = "PLANTED-7f3c"
BREAKOUT = "</script><script>alert(1)</script>"
HTML_TITLE = MARK + " title " + BREAKOUT + " & <b>x</b>"
HTML_WHY = (MARK + " why " + BREAKOUT + "\n<!-- c --> <SCRIPT>x</SCRIPT>"
            + " {{ 1 + 1 }}")
# U+2028 / U+2029 are line separators, which the board refuses on input like
# roadmap.py's reader does; the data-block encoder must still escape them, so
# that half is measured in-process on the encoder itself.
LS_PS = chr(0x2028) + chr(0x2029)


def _d(label, value):
    return "%-12s: %s" % (label, value)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run_board(args, cwd, stdin_text=""):
    proc = subprocess.run([sys.executable, TARGET] + [str(a) for a in args],
                          input=stdin_text.encode("utf-8"),
                          capture_output=True, timeout=30, cwd=cwd,
                          env=H.child_env())
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def refusal_problems(code, out, err):
    """A refusal is exit 2, exactly one stderr line prefixed `board: `, and
    nothing on stdout."""
    problems = []
    if code != 2:
        problems.append("exit %r, want 2" % (code,))
    lines = err.splitlines()
    if len(lines) != 1 or not lines[0].startswith("board: "):
        problems.append("stderr is not one `board: ` line: %r" % (err[:300],))
    if out:
        problems.append("stdout is not empty: %r" % (out[:200],))
    return problems


def ok_problems(code, err):
    problems = []
    if code != 0:
        problems.append("exit %r, want 0" % (code,))
    if err:
        problems.append("stderr is not empty: %r" % (err[:300],))
    return problems


def golden():
    return json.loads(GOLDEN_EXPORT)


def dump(doc):
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def item_of(doc, ident):
    for item in doc["items"]:
        if item["id"] == ident:
            return item
    raise KeyError(ident)


def split_row(line):
    """The cells of one `| a | b |` row, split on UNESCAPED pipes only, then
    decoded.  Returns None when the line is not a row or an escape is
    malformed."""
    if not (line.startswith("| ") and line.endswith(" |")):
        return None
    body = line[2:-2]
    cells, cur, i = [], [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                return None
            nxt = body[i + 1]
            mapped = {"\\": "\\", "|": "|", "n": "\n", "r": "\r",
                      "t": "\t"}.get(nxt)
            if mapped is None:
                return None
            cur.append(mapped)
            i += 2
            continue
        if ch == "|":
            # a boundary is " | ": strip the one pad space on each side
            text = "".join(cur)
            cells.append(text[:-1] if text.endswith(" ") else text)
            cur = []
            i += 1
            if i < len(body) and body[i] == " ":
                i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return cells


def md_rows(text):
    """id -> decoded cells, for every body row of every table."""
    rows = {}
    for line in text.split("\n"):
        cells = split_row(line)
        if cells and cells[0].startswith("R-"):
            rows[cells[0]] = cells
    return rows


DATA_RE = re.compile(r'<script type="application/json" id="data">(.*?)'
                     r'</script>', re.S)


def data_block(page):
    found = DATA_RE.findall(page)
    return found[0] if len(found) == 1 else None


def need_target(suite, group, cid):
    if not os.path.isfile(TARGET):
        suite.record(group, cid, ["board.py is missing: %s" % TARGET])
        return False
    return True


# ---------------------------------------------------------------------------
# A. contract + source gates
# ---------------------------------------------------------------------------

def _imports(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return base + "." + node.attr if base else None
    return None


def group_a(suite):
    exists = os.path.isfile(TARGET)
    suite.record(GA, "target-exists",
                 [] if exists else ["board.py is missing: %s" % TARGET],
                 detail=[_d("target", TARGET)])
    if not exists:
        for cid in ("stdlib-only", "no-spawn", "no-v-html-in-source",
                    "vue-pinned-in-source"):
            suite.record(GA, cid, ["board.py is missing"])
        return
    with open(TARGET, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    roots = sorted(set(n.split(".")[0] for n in _imports(tree)))
    extra = [n for n in roots if n not in STDLIB_ALLOWED]
    suite.record(GA, "stdlib-only",
                 ["import outside the declared stdlib set: %s"
                  % ", ".join(extra)] if extra else [],
                 detail=[_d("imports", ", ".join(roots))])
    spawns = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
        if name and any(name.startswith(s) for s in SPAWN_NAMES):
            spawns.append("%s at line %d" % (name, node.lineno))
    if "subprocess" in roots:
        spawns.append("imports subprocess")
    suite.record(GA, "no-spawn", spawns,
                 detail=["a renderer of a file spawns nothing: no git, no "
                         "shell, no browser"])
    suite.record(GA, "no-v-html-in-source",
                 problem_if("v-html" in source.lower(),
                            "the source mentions v-html; the page must "
                            "render through text interpolation only"))
    suite.record(GA, "vue-pinned-in-source",
                 problem_if(source.count(VUE_URL) != 1,
                            "want the pinned Vue URL exactly once, found %d"
                            % source.count(VUE_URL)),
                 detail=[_d("url", VUE_URL)])


def problem_if(condition, message):
    return [message] if condition else []


# ---------------------------------------------------------------------------
# B. input + schema refusal
# ---------------------------------------------------------------------------

def group_b(suite, work):
    cases = []
    doc = golden()
    doc["schema"] = "roadmap-export/1"
    cases.append(("schema-v1-file", dump(doc), False, ("roadmap-export/2",)))
    doc = golden()
    doc["schema"] = "roadmap-export/2 "
    cases.append(("schema-near-miss", dump(doc), False, ("roadmap-export/2",)))
    doc = golden()
    del doc["schema"]
    cases.append(("schema-missing", dump(doc), False, ("roadmap-export/2",)))
    doc = golden()
    doc["schema"] = 2
    cases.append(("schema-not-a-string", dump(doc), False,
                  ("roadmap-export/2",)))
    doc = golden()
    doc["schema"] = "roadmap-export/3"
    cases.append(("schema-v3-stdin", dump(doc), True, ("roadmap-export/2",)))
    cases.append(("not-json", "{not json\n", False, ("JSON",)))
    cases.append(("top-level-array", "[]\n", False, ("object",)))
    doc = golden()
    item_of(doc, "R-0005")["horizon"] = "sideways"
    cases.append(("item-bad-horizon", dump(doc), False, ("R-0005",)))
    doc = golden()
    item_of(doc, "R-0003")["title"] = "esc \x1b[31m red"
    cases.append(("control-char-refused", dump(doc), False, ("R-0003",)))
    doc = golden()
    item_of(doc, "R-0004")["why"] = "line" + LS_PS[0] + "separator"
    cases.append(("line-separator-refused", dump(doc), False, ("R-0004",)))
    doc = golden()
    item_of(doc, "R-0004")["blocked_by"] = "R-0003"
    cases.append(("item-blocked-by-not-list", dump(doc), False, ("R-0004",)))
    for cid, body, stdin, tokens in cases:
        if not need_target(suite, GB, cid):
            continue
        if stdin:
            code, out, err = run_board(["-"], work.path, stdin_text=body)
        else:
            path = work.write_text("b-%s.json" % cid, body)
            code, out, err = run_board([path], work.path)
        problems = refusal_problems(code, out, err)
        problems += ["stderr does not name %r" % t for t in tokens
                     if t not in err]
        suite.record(GB, cid, problems, detail=[_d("stderr", err.strip())])

    cid = "missing-file"
    if need_target(suite, GB, cid):
        code, out, err = run_board([work.join("no-such.json")], work.path)
        suite.record(GB, cid, refusal_problems(code, out, err),
                     detail=[_d("stderr", err.strip())])

    cid = "not-utf8"
    if need_target(suite, GB, cid):
        path = work.write_bytes("b-latin1.json",
                                GOLDEN_EXPORT.replace(
                                    "Four", "F\xf6ur").encode("latin-1"))
        code, out, err = run_board([path], work.path)
        suite.record(GB, cid, refusal_problems(code, out, err),
                     detail=[_d("stderr", err.strip())])

    cid = "stdin-equals-file"
    if need_target(suite, GB, cid):
        path = work.write_text("b-golden.json", GOLDEN_EXPORT)
        code1, out1, err1 = run_board([path], work.path)
        code2, out2, err2 = run_board(["-"], work.path,
                                      stdin_text=GOLDEN_EXPORT)
        problems = ok_problems(code1, err1) + ok_problems(code2, err2)
        problems += problem_if(out1 != out2, "stdin and file routes differ")
        suite.record(GB, cid, problems)


# ---------------------------------------------------------------------------
# C. markdown board
# ---------------------------------------------------------------------------

def _first_diff(got, want):
    for n, (a, b) in enumerate(zip(got.split("\n"), want.split("\n")), 1):
        if a != b:
            return "line %d: got %r, want %r" % (n, a, b)
    return "length: got %d lines, want %d" % (got.count("\n"),
                                               want.count("\n"))


def group_c(suite, work):
    path = work.write_text("c-golden.json", GOLDEN_EXPORT)

    cid = "golden-byte-for-byte"
    if need_target(suite, GC, cid):
        code, out, err = run_board([path], work.path)
        problems = ok_problems(code, err)
        if out != EXPECTED_MD:
            problems.append("markdown differs from EXPECTED_MD: %s"
                            % _first_diff(out, EXPECTED_MD))
        suite.record(GC, cid, problems, text=out)

    cid = "format-md-is-default"
    if need_target(suite, GC, cid):
        code1, out1, err1 = run_board([path], work.path)
        code2, out2, err2 = run_board([path, "--format", "md"], work.path)
        problems = ok_problems(code1, err1) + ok_problems(code2, err2)
        problems += problem_if(out1 != out2, "--format md differs from the "
                                             "default")
        suite.record(GC, cid, problems)

    cid = "column-order"
    if need_target(suite, GC, cid):
        code, out, err = run_board([path], work.path)
        heads = [line[3:].split(" (")[0] for line in out.split("\n")
                 if line.startswith("## ")]
        suite.record(GC, cid, ok_problems(code, err) + problem_if(
            tuple(heads) != COLUMNS, "columns %r, want %r" % (heads, COLUMNS)),
            detail=[_d("columns", ", ".join(heads))])

    cid = "reverse-blocks-edges"
    if need_target(suite, GC, cid):
        doc = golden()
        # a second blocker edge, so the reverse list must be sorted and joined
        item_of(doc, "R-0005")["blocked_by"] = ["R-0003"]
        item_of(doc, "R-0005")["ready"] = False
        bpath = work.write_text("c-blocks.json", dump(doc))
        code, out, err = run_board([bpath], work.path)
        rows = md_rows(out)
        problems = ok_problems(code, err)
        blocks = MD_HEADERS.index("blocks")
        blocked = MD_HEADERS.index("blocked_by")
        want = {"R-0003": ("", "R-0004, R-0005"), "R-0004": ("R-0003", ""),
                "R-0005": ("R-0003", "")}
        for ident, (want_by, want_blocks) in sorted(want.items()):
            row = rows.get(ident)
            if row is None or len(row) != len(MD_HEADERS):
                problems.append("%s: no well-formed row" % ident)
                continue
            if row[blocked] != want_by or row[blocks] != want_blocks:
                problems.append("%s: blocked_by %r blocks %r, want %r %r"
                                % (ident, row[blocked], row[blocks], want_by,
                                   want_blocks))
        suite.record(GC, cid, problems)

    cid = "planted-cells-round-trip"
    if need_target(suite, GC, cid):
        doc = golden()
        item_of(doc, "R-0003")["title"] = MD_TITLE
        item_of(doc, "R-0003")["why"] = MD_WHY
        item_of(doc, "R-0002")["title"] = MD_TITLE
        ppath = work.write_text("c-planted.json", dump(doc))
        code, out, err = run_board([ppath], work.path)
        problems = ok_problems(code, err)
        rows = md_rows(out)
        for ident in ("R-0003", "R-0002"):
            row = rows.get(ident)
            if row is None:
                problems.append("%s: no row decodes (a newline ended it, or "
                                "an escape is malformed)" % ident)
                continue
            if len(row) != len(MD_HEADERS):
                problems.append("%s: %d cells, want %d -- a cell forged a "
                                "column" % (ident, len(row), len(MD_HEADERS)))
                continue
            if row[1] != MD_TITLE:
                problems.append("%s: title decodes to %r" % (ident, row[1]))
        row = rows.get("R-0003")
        if row and len(row) == len(MD_HEADERS) and row[-1] != MD_WHY:
            problems.append("R-0003: why decodes to %r" % (row[-1],))
        raw = [line for line in out.split("\n") if "\r" in line or "\t" in line]
        problems += problem_if(raw, "a raw CR or TAB reached the output")
        suite.record(GC, cid, problems,
                     detail=[line for line in out.split("\n")
                             if line.startswith("| R-0003")])

    cid = "open-only-scope-line"
    if need_target(suite, GC, cid):
        doc = golden()
        doc["items"] = [i for i in doc["items"] if i["closed"] is None]
        doc["scope"]["closed"] = False
        doc["source"]["head"] = "abc1234"
        doc["counts"]["done"] = doc["counts"]["dropped"] = 0
        opath = work.write_text("c-open.json", dump(doc))
        code, out, err = run_board([opath], work.path)
        lines = out.split("\n")
        want = ("source head: abc1234; scope: open only; closed since: none; "
                "now WIP limit: 3")
        problems = ok_problems(code, err)
        problems += problem_if(len(lines) < 3 or lines[2] != want,
                               "scope line %r, want %r"
                               % (lines[2] if len(lines) > 2 else None, want))
        problems += problem_if("## done (0)" not in lines,
                               "an empty done column is not rendered")
        suite.record(GC, cid, problems)


# ---------------------------------------------------------------------------
# D. html board
# ---------------------------------------------------------------------------

def _html(work, cid, doc):
    src = work.write_text("d-%s.json" % cid, dump(doc))
    out_path = work.join("roadmap-board-%s.html" % cid)
    code, out, err = run_board([src, "--format", "html", "--out", out_path],
                               work.path)
    page = None
    if os.path.isfile(out_path):
        with open(out_path, "r", encoding="utf-8") as fh:
            page = fh.read()
    return code, out, err, page


def group_d(suite, work):
    doc = golden()
    item_of(doc, "R-0003")["title"] = HTML_TITLE
    item_of(doc, "R-0003")["why"] = HTML_WHY
    item_of(doc, "R-0001")["why"] = HTML_WHY

    cid = "written-one-line"
    if need_target(suite, GD, cid):
        code, out, err, page = _html(work, cid, doc)
        problems = ok_problems(code, err)
        problems += problem_if(page is None, "no page was written")
        problems += problem_if(len(out.splitlines()) != 1,
                               "stdout is not one line: %r" % out)
        suite.record(GD, cid, problems, detail=[_d("stdout", out.strip())])

    code, out, err, page = (None, "", "", None)
    if os.path.isfile(TARGET):
        code, out, err, page = _html(work, "planted", doc)
    block = data_block(page) if page else None

    cid = "data-block-inert"
    if need_target(suite, GD, cid):
        problems = ok_problems(code, err)
        if block is None:
            problems.append("no single <script type=\"application/json\" "
                            "id=\"data\"> block")
        else:
            low = block.lower()
            for token in ("</script", "<script", "<!--"):
                problems += problem_if(token in low,
                                       "%r occurs inside the data block"
                                       % token)
            for ch in ("<", ">", "&"):
                problems += problem_if(ch in block,
                                       "raw %r inside the data block" % ch)
        suite.record(GD, cid, problems)

    cid = "data-round-trips"
    if need_target(suite, GD, cid):
        problems = []
        data = None
        try:
            data = json.loads(block) if block is not None else None
        except ValueError as exc:
            problems.append("the data block is not JSON: %s" % exc)
        if data is None:
            problems.append("no data")
        else:
            cards = {}
            keys = []
            for column in data.get("columns", []):
                keys.append(column.get("key"))
                for card in column.get("cards", []):
                    cards[card.get("id")] = card
            problems += problem_if(tuple(keys) != COLUMNS,
                                   "columns %r, want %r" % (keys, COLUMNS))
            card = cards.get("R-0003", {})
            problems += problem_if(card.get("title") != HTML_TITLE,
                                   "R-0003 title did not survive")
            problems += problem_if(card.get("why") != HTML_WHY,
                                   "R-0003 why did not survive")
            problems += problem_if(card.get("blocks") != ["R-0004"],
                                   "R-0003 blocks %r, want ['R-0004']"
                                   % (card.get("blocks"),))
            problems += problem_if(
                cards.get("R-0004", {}).get("blocked_by") != ["R-0003"],
                "R-0004 blocked_by lost")
            problems += problem_if(
                cards.get("R-0001", {}).get("state") != "done",
                "R-0001 is not a done card")
        suite.record(GD, cid, problems)

    cid = "encoder-escapes-in-process"
    if need_target(suite, GD, cid):
        problems = []
        planted = {"a": BREAKOUT + " & " + LS_PS + " <!-- x -->"}
        try:
            mod = H.load_module_from_path("roadmap_board_under_test", TARGET)
            text = mod.script_json(planted)
        except Exception as exc:
            text = None
            problems.append("script_json failed: %s: %s"
                            % (type(exc).__name__, exc))
        if text is not None:
            for ch in ("<", ">", "&", LS_PS[0], LS_PS[1]):
                problems += problem_if(ch in text, "raw %r in %r" % (ch, text))
            try:
                problems += problem_if(json.loads(text) != planted,
                                       "the encoding does not round-trip")
            except ValueError as exc:
                problems.append("not JSON: %s" % exc)
        suite.record(GD, cid, problems, detail=[_d("encoded", text)])

    cid = "no-v-html"
    if need_target(suite, GD, cid):
        problems = problem_if(page is None, "no page")
        if page is not None:
            problems += problem_if("v-html" in page.lower(),
                                   "v-html occurs in the page")
            problems += problem_if("innerhtml" in page.lower(),
                                   "innerHTML occurs in the page")
        suite.record(GD, cid, problems)

    cid = "vue-pinned-only-external"
    if need_target(suite, GD, cid):
        problems = problem_if(page is None, "no page")
        if page is not None:
            srcs = re.findall(r'<script[^>]*\ssrc="([^"]*)"', page)
            problems += problem_if(srcs != [VUE_URL],
                                   "external scripts %r, want [%r]"
                                   % (srcs, VUE_URL))
            links = re.findall(r'<link[^>]*href="([^"]*)"', page)
            problems += problem_if(links, "external links %r" % links)
        suite.record(GD, cid, problems, detail=[_d("vue", VUE_URL)])

    cid = "noscript-line"
    if need_target(suite, GD, cid):
        problems = problem_if(page is None, "no page")
        if page is not None:
            problems += problem_if(not re.search(r"<noscript>[^<]+</noscript>",
                                                 page),
                                   "no <noscript> line")
        suite.record(GD, cid, problems)

    cid = "data-only-in-block"
    if need_target(suite, GD, cid):
        problems = problem_if(page is None or block is None, "no page/block")
        if page is not None and block is not None:
            outside = page.replace(block, "")
            problems += problem_if(MARK in outside,
                                   "planted text appears outside the data "
                                   "block: it was interpolated into the page")
            problems += problem_if(page.lower().count("<script") != 3,
                                   "want 3 <script> tags (vue, data, app), "
                                   "found %d" % page.lower().count("<script"))
        suite.record(GD, cid, problems)


# ---------------------------------------------------------------------------
# E. --out
# ---------------------------------------------------------------------------

def group_e(suite, work):
    src = work.write_text("e-golden.json", GOLDEN_EXPORT)
    base = work.subdir("e-out")

    def refused(cid, out_name, argv_tail=None, tokens=()):
        if not need_target(suite, GE, cid):
            return
        target = os.path.join(base, out_name)
        before = sorted(os.listdir(base))
        argv = [src] + (argv_tail if argv_tail is not None
                        else ["--format", "html", "--out", target])
        code, out, err = run_board(argv, work.path)
        problems = refusal_problems(code, out, err)
        problems += ["stderr does not name %r" % t for t in tokens
                     if t not in err]
        after = sorted(os.listdir(base))
        problems += problem_if(after != before, "the directory changed: %r"
                               % sorted(set(after) ^ set(before)))
        suite.record(GE, cid, problems, detail=[_d("stderr", err.strip())])

    refused("name-not-board", "board.html", tokens=("roadmap-board-",))
    refused("name-wrong-suffix", "roadmap-board-x.htm")
    refused("name-uppercase", "roadmap-board-X.html")
    refused("name-empty-stem", "roadmap-board-.html")
    refused("roadmap-md-refused", "roadmap.md")
    refused("html-needs-out", "unused", argv_tail=["--format", "html"])
    refused("md-takes-no-out", "roadmap-board-md.html",
            argv_tail=["--out", os.path.join(base, "roadmap-board-md.html")])
    refused("unknown-format", "unused", argv_tail=["--format", "svg"])

    cid = "symlink-target-refused"
    if need_target(suite, GE, cid):
        victim = os.path.join(base, "victim.txt")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("untouched\n")
        link = os.path.join(base, "roadmap-board-link.html")
        os.symlink(victim, link)
        code, out, err = run_board([src, "--format", "html", "--out", link],
                                   work.path)
        problems = refusal_problems(code, out, err)
        problems += problem_if(not os.path.islink(link),
                               "the symlink was replaced")
        with open(victim, "r", encoding="utf-8") as fh:
            problems += problem_if(fh.read() != "untouched\n",
                                   "the symlink's target was written")
        problems += problem_if(any(n.startswith(".") for n in os.listdir(base)),
                               "a temp file was left behind")
        os.unlink(link)
        os.unlink(victim)
        suite.record(GE, cid, problems, detail=[_d("stderr", err.strip())])

    cid = "accepted-replaces-regular"
    if need_target(suite, GE, cid):
        target = os.path.join(base, "roadmap-board-20260929t1200-1.html")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("old\n")
        code, out, err = run_board([src, "--format", "html", "--out", target],
                                   work.path)
        problems = ok_problems(code, err)
        with open(target, "r", encoding="utf-8") as fh:
            body = fh.read()
        problems += problem_if(not body.startswith("<!DOCTYPE html>"),
                               "the file was not replaced by a page")
        problems += problem_if(out.strip() != "board: %s" % target,
                               "stdout %r, want 'board: PATH'" % out)
        problems += problem_if(any(n.startswith(".") for n in os.listdir(base)),
                               "a temp file was left behind")
        suite.record(GE, cid, problems)


# ---------------------------------------------------------------------------
# F. hygiene
# ---------------------------------------------------------------------------

def _live_digests():
    return (H.file_digests(LIVE_ROADMAP_DIR), H.file_digests(LIVE_ARCHIVE_DIR))


def group_f(suite, work, live_before, pyc_before, inputs_before):
    changed = []
    for path, digest in sorted(inputs_before.items()):
        if not os.path.isfile(path) or H.sha256_file(path) != digest:
            changed.append(os.path.basename(path))
    suite.record(GF, "inputs-unchanged",
                 problem_if(changed, "input export(s) changed: %s"
                            % ", ".join(changed)),
                 detail=[_d("inputs", len(inputs_before))])
    suite.record(GF, "live-roadmap-unchanged",
                 problem_if(_live_digests() != live_before,
                            "docs/roadmap changed during the run"))
    new = sorted(set(H.pycache_snapshot()) - set(pyc_before))
    suite.record(GF, "no-bytecode",
                 problem_if(new, "new bytecode: %s" % ", ".join(new[:5])))


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="board.py: the read-only kanban board over "
                          "roadmap-export/2 -- schema refusal, markdown with "
                          "ADR 0016 cells, an html page whose data cannot "
                          "break out, and --out",
                    opts=opts, mode="grouped")
    live_before = _live_digests()
    pyc_before = H.pycache_snapshot()
    work = H.TempWorkspace("ph-roadmap-board-", keep=opts.keep)
    inputs_before = {}
    try:
        group_a(suite)
        seed = work.write_text("f-input.json", GOLDEN_EXPORT)
        inputs_before[seed] = H.sha256_file(seed)
        if os.path.isfile(TARGET):
            run_board([seed], work.path)
            run_board([seed, "--format", "html", "--out",
                       work.join("roadmap-board-seed.html")], work.path)
        for body in (group_b, group_c, group_d, group_e):
            body(suite, work)
        group_f(suite, work, live_before, pyc_before, inputs_before)
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
