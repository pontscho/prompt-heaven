#!/usr/bin/env python3
"""
A read-only kanban board over one `roadmap-export/2` document (R-0042). The
export contract lives in the p:roadmap SKILL.md; this script is a CONSUMER of
it and owns exactly the three jobs that contract hands its consumers: the
reverse `blocks` edges, escaping, and refusing a schema it was not written for.
It never reads or writes roadmap.md or the archive, spawns nothing, and writes
at most one file: the `--out` page.

    python3 board.py EXPORT.json [--format md]
    python3 board.py EXPORT.json --format html --out .claude/tmp/roadmap-board-<ts>.html
    python3 roadmap.py export | python3 board.py -          # `-` reads stdin

COLUMNS. One per kanban column, in the documented order inbox, later, next,
now, done, dropped. An open item sits in its horizon (`unset` is the inbox); a
closed item sits in the column of its state, whatever lane it closed from.
Within a column the export's own order is kept (rank for live items, id for
closed ones). `state` is the card's badge.

THE SCHEMA IS EXACT. Anything whose `schema` is not the string
`roadmap-export/2` is refused: a /3 may have changed what a field means, and a
board rendered on a guess looks exactly like a correct one. The document and
every item field the board renders are checked before anything is emitted;
every refusal is exit 2 with one `board: ` line on stderr.

UNSAFE CHARACTERS ARE REFUSED, THE VOCABULARY IS ESCAPED. A rendered string may
not carry a control, format or line-separator character other than newline,
CR and tab -- the rule roadmap.py's own reader applies, so a real export never
trips it. Newline, CR and tab (plus the backslash and the pipe) are what ADR
0016's one vocabulary escapes, so those are rendered, never refused.

MARKDOWN (--format md, the default, on stdout). One `## column (n)` section per
column, each an UNPADDED GFM pipe table (ADR 0016 Option 4: its reader parses
on the delimiter, so alignment would buy nothing). Every cell goes through
_md_cell: `\\` FIRST, then `\\|`, `\\n`, `\\r`, `\\t` -- reversible, and an
unescaped `|` is always a column boundary.

HTML (--format html --out PATH). One static page, no build step. It loads the
Vue 3.4.21 global production build from unpkg.com, pinned to that exact
version. VIEWING IT NEEDS NETWORK ACCESS, and the script tag carries NO
Subresource Integrity hash: the page trusts unpkg.com to serve that version's
bytes. Without JavaScript the page shows one <noscript> line pointing at the
markdown form. The data travels as JSON inside
<script type="application/json" id="data">, ASCII-only, with `<`, `>` and `&`
written as \\u003c, \\u003e, \\u0026 (U+2028/2029 are \\u-escaped by the ASCII
encoding as well), so no value can close the block or open a comment. The page
template is a constant and interpolates NO data: every value reaches the DOM
through Vue's text interpolation or an attribute binding, and Vue's raw-HTML
directive is never used, so `why` -- raw markdown -- is shown as plain
pre-wrapped text, never rendered.

--OUT. The basename must match `roadmap-board-[a-z0-9-]+.html` (the p:roadmap
staging rule, widened for this one output), an existing symlink is refused
rather than replaced, and the page is written to a temp file in the target's
directory and published with os.replace.
"""

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata

SCHEMA = "roadmap-export/2"
COLUMNS = ("inbox", "later", "next", "now", "done", "dropped")
HORIZONS = ("now", "next", "later", "unset")
OPEN_STATES = ("idea", "planned", "active")
CLOSED_STATES = ("done", "dropped")
MD_HEADERS = ("id", "title", "state", "severity", "tags", "ready",
              "blocked_by", "blocks", "why")
VUE_URL = "https://unpkg.com/vue@3.4.21/dist/vue.global.prod.js"
INPUT_MAX_BYTES = 16 * 1024 * 1024   # an export is small; this bounds a planted one
TMP_PREFIX = ".roadmap-board-"
TMP_SUFFIX = ".tmp"
ID_RE = re.compile(r"R-\d{4}", re.ASCII)
HEAD_RE = re.compile(r"[0-9a-f]{4,64}", re.ASCII)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
OUT_NAME_RE = re.compile(r"roadmap-board-[a-z0-9]+(?:-[a-z0-9]+)*\.html", re.ASCII)
VOCABULARY = "\n\r\t"   # escaped by _md_cell, so never refused


# ---------------------------------------------------------------------------
# errors and output
# ---------------------------------------------------------------------------

def _unsafe(ch):
    """roadmap.py's unsafe_char: a line break str.splitlines honours, or a
    control, format or surrogate code point."""
    return (len(("a" + ch + "a").splitlines()) != 1
            or unicodedata.category(ch) in ("Cc", "Cf", "Cs"))


def _shown(text):
    """A message value with every unsafe character backslash-escaped, so a
    refusal is always exactly one line."""
    return "".join(ch.encode("unicode_escape").decode("ascii") if _unsafe(ch) else ch
                   for ch in str(text))


def die(message):
    sys.stderr.write("board: %s\n" % _shown(message))
    sys.exit(2)


def _write_stdout(text):
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


class _Parser(argparse.ArgumentParser):
    """A usage error is one refusal line, not argparse's multi-line block."""

    def error(self, message):
        die(message)


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------

def read_input(source):
    """The export's bytes from a regular file (never through a symlink) or,
    for `-`, from stdin; at most INPUT_MAX_BYTES either way."""
    if source == "-":
        data = sys.stdin.buffer.read(INPUT_MAX_BYTES + 1)
    else:
        try:
            st = os.lstat(source)
            if not stat.S_ISREG(st.st_mode):
                die("%s is not a regular file" % source)
            if st.st_size > INPUT_MAX_BYTES:
                die("%s is larger than %d bytes" % (source, INPUT_MAX_BYTES))
            fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                data = stream.read(INPUT_MAX_BYTES + 1)
        except OSError as exc:
            die("cannot read %s: %s" % (source, exc.strerror or exc.__class__.__name__))
    if len(data) > INPUT_MAX_BYTES:
        die("%s is larger than %d bytes" % (source, INPUT_MAX_BYTES))
    return data


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key %r" % key)
        seen[key] = value
    return seen


def parse_export(source, data):
    name = "stdin" if source == "-" else source
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        die("%s is not valid UTF-8 (byte 0x%02x at offset %d)"
            % (name, exc.object[exc.start], exc.start))
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicates)
    except ValueError as exc:
        die("%s is not JSON: %s" % (name, exc))
    if not isinstance(doc, dict):
        die("%s: the export is not a JSON object" % name)
    schema = doc.get("schema")
    if schema != SCHEMA or not isinstance(schema, str):
        shown = "no schema" if "schema" not in doc else "schema %s" % json.dumps(schema)[:80]
        die("%s: %s -- this board reads %s only (a roadmap.py export)"
            % (name, shown, SCHEMA))
    return doc


# ---------------------------------------------------------------------------
# validation -- everything the board renders, before anything is emitted
# ---------------------------------------------------------------------------

def _check_text(where, field, value):
    for ch in value:
        if ch not in VOCABULARY and _unsafe(ch):
            die("%s: %s carries U+%04X, a control, format or line-separator character "
                "-- roadmap.py's reader refuses it too" % (where, field, ord(ch)))


def _string(where, field, value, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        die("%s: %s is not a string" % (where, field))
    _check_text(where, field, value)
    return value


def _string_list(where, field, value, pattern=None):
    if not isinstance(value, list):
        die("%s: %s is not a list" % (where, field))
    for element in value:
        _string(where, field, element)
        if pattern is not None and not pattern.fullmatch(element):
            die("%s: %s holds %r, not an item id" % (where, field, element))
    return list(value)


def _meta(doc):
    source, scope, wip = doc.get("source"), doc.get("scope"), doc.get("wip_limit")
    if not isinstance(source, dict) or not isinstance(scope, dict) or not isinstance(wip, dict):
        die("malformed export: source, scope and wip_limit must be objects")
    head = source.get("head")
    if head is not None and not (isinstance(head, str) and HEAD_RE.fullmatch(head)):
        die("malformed export: source.head is not a short sha or null")
    closed = scope.get("closed")
    if not isinstance(closed, bool):
        die("malformed export: scope.closed is not a boolean")
    since = scope.get("closed_since")
    if since is not None and not (isinstance(since, str) and DATE_RE.fullmatch(since)):
        die("malformed export: scope.closed_since is not a date or null")
    now = wip.get("now")
    if not isinstance(now, int) or isinstance(now, bool):
        die("malformed export: wip_limit.now is not an integer")
    return {"head": head, "scope": "open and closed" if closed else "open only",
            "closed_since": since, "wip_now": now}


def _closed(where, value):
    if value is None:
        return None
    if not isinstance(value, dict):
        die("%s: closed is not an object or null" % where)
    return {"date": _string(where, "closed.date", value.get("date")),
            "commit": _string(where, "closed.commit", value.get("commit"), optional=True),
            "reason": _string(where, "closed.reason", value.get("reason"), optional=True)}


def _card(index, item):
    """One validated item, reduced to what a card shows."""
    if not isinstance(item, dict):
        die("item #%d is not an object" % index)
    ident = item.get("id")
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        die("item #%d: id is not R-NNNN" % index)
    where = "item %s" % ident
    state, horizon, ready = item.get("state"), item.get("horizon"), item.get("ready")
    if state not in OPEN_STATES + CLOSED_STATES:
        die("%s: state %s is not one of %s" % (where, json.dumps(state)[:40],
                                               ", ".join(OPEN_STATES + CLOSED_STATES)))
    if horizon not in HORIZONS:
        die("%s: horizon %s is not one of %s" % (where, json.dumps(horizon)[:40],
                                                 ", ".join(HORIZONS)))
    if ready not in (True, False, None) or isinstance(ready, int) and not isinstance(ready, bool):
        die("%s: ready is not true, false or null" % where)
    return {"id": ident, "state": state, "horizon": horizon, "ready": ready,
            "title": _string(where, "title", item.get("title")),
            "why": _string(where, "why", item.get("why")),
            "origin": _string(where, "origin", item.get("origin")),
            "severity": _string(where, "severity", item.get("severity"), optional=True),
            "tags": _string_list(where, "tags", item.get("tags")),
            "blocked_by": _string_list(where, "blocked_by", item.get("blocked_by"), ID_RE),
            "closed": _closed(where, item.get("closed"))}


def column_of(card):
    if card["state"] in CLOSED_STATES:
        return card["state"]
    return "inbox" if card["horizon"] == "unset" else card["horizon"]


def build_board(doc):
    """{"meta", "columns": [{"key", "cards"}]}, with each card's reverse
    `blocks` edges: the ids in THIS export whose blocked_by names it."""
    items = doc.get("items")
    if not isinstance(items, list):
        die("malformed export: items is not a list")
    cards, seen = [], set()
    for index, item in enumerate(items):
        card = _card(index, item)
        if card["id"] in seen:
            die("item %s appears twice" % card["id"])
        seen.add(card["id"])
        cards.append(card)
    blocks = dict((card["id"], []) for card in cards)
    for card in cards:
        for blocker in card["blocked_by"]:
            if blocker in blocks:
                blocks[blocker].append(card["id"])
    columns = [{"key": key, "cards": []} for key in COLUMNS]
    by_key = dict((column["key"], column) for column in columns)
    for card in cards:
        card["blocks"] = sorted(blocks[card["id"]])
        by_key[column_of(card)]["cards"].append(card)
    return {"meta": _meta(doc), "columns": columns}


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

def _md_cell(value):
    r"""Escape one cell so a value cannot open a column. Reversible: \\ \| \n \r \t.
    The escape character is escaped FIRST (ADR 0016's one vocabulary)."""
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def render_table(headers, rows):
    """An unpadded GFM table; every header and body cell is escaped."""
    def line(cells):
        return "| " + " | ".join(_md_cell(cell) for cell in cells) + " |"
    rule = "|" + "---|" * len(headers)
    return "\n".join([line(headers), rule] + [line(row) for row in rows])


def _md_row(card):
    ready = {True: "yes", False: "no", None: ""}[card["ready"]]
    return (card["id"], card["title"], card["state"], card["severity"] or "",
            ", ".join(card["tags"]), ready, ", ".join(card["blocked_by"]),
            ", ".join(card["blocks"]), card["why"])


def render_md(board):
    meta = board["meta"]
    blocks = [["# Roadmap board", "",
               "source head: %s; scope: %s; closed since: %s; now WIP limit: %d"
               % (meta["head"] or "none", meta["scope"], meta["closed_since"] or "none",
                  meta["wip_now"])]]
    for column in board["columns"]:
        cards = column["cards"]
        body = (render_table(MD_HEADERS, [_md_row(card) for card in cards])
                if cards else "_(no items)_")
        blocks.append(["## %s (%d)" % (column["key"], len(cards)), "", body])
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------

def script_json(value):
    """JSON that is inert inside a <script> element: ASCII-only (so U+2028 and
    U+2029 are \\u-escaped), and `<`, `>`, `&` written as \\u escapes, which
    JSON.parse reads back as the same characters. No `</script`, `<!--` or
    `-->` can survive."""
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return (text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Roadmap board</title>
<style>
[v-cloak] { display: none; }
body { font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; margin: 1rem;
       color: #222; background: #f6f7f9; }
h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
.meta { color: #555; margin: 0 0 1rem; }
.board { display: grid; grid-template-columns: repeat(6, minmax(13rem, 1fr)); gap: .75rem;
         align-items: start; overflow-x: auto; }
.column { background: #eceef2; border-radius: 6px; padding: .5rem; }
.column h2 { font-size: .95rem; margin: .2rem .2rem .5rem; text-transform: uppercase;
             letter-spacing: .04em; color: #444; }
.card { background: #fff; border: 1px solid #d8dbe0; border-radius: 5px; padding: .5rem;
        margin-bottom: .5rem; }
.id { font-family: ui-monospace, Menlo, monospace; color: #666; font-size: .85em; }
.title { font-weight: 600; margin: .2rem 0; overflow-wrap: anywhere; }
.badge { display: inline-block; font-size: .75em; padding: 0 .4em; border-radius: 3px;
         background: #e4e6eb; margin: 0 .25em .2em 0; }
.state-idea { background: #ececec; }
.state-planned { background: #d6e4f5; }
.state-active { background: #cde8cd; }
.state-done { background: #cfe9d9; }
.state-dropped { background: #f3d6d6; }
.blocked { background: #f7e3c4; }
.tag { background: #e8e8fa; }
.edges { font-size: .85em; color: #444; margin: .2rem 0; overflow-wrap: anywhere; }
details summary { cursor: pointer; color: #336; font-size: .85em; }
pre.why { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: .85em;
          margin: .3rem 0 0; }
.empty { color: #888; font-style: italic; margin: .2rem; }
</style>
</head>
<body>
<noscript>This board needs JavaScript and network access (Vue 3 from unpkg.com); for a text board run board.py --format md.</noscript>
<script type="application/json" id="data">%(data)s</script>
<div id="app" v-cloak>
  <h1>Roadmap board</h1>
  <p class="meta">source head: {{ meta.head || 'none' }}; scope: {{ meta.scope }}; closed since: {{ meta.closed_since || 'none' }}; now WIP limit: {{ meta.wip_now }}</p>
  <main class="board">
    <section class="column" v-for="column in columns" :key="column.key">
      <h2>{{ column.key }} ({{ column.cards.length }})</h2>
      <p class="empty" v-if="!column.cards.length">no items</p>
      <article class="card" v-for="card in column.cards" :key="card.id">
        <div class="id" :title="card.origin">{{ card.id }}</div>
        <div class="title">{{ card.title }}</div>
        <div>
          <span class="badge" :class="'state-' + card.state">{{ card.state }}</span>
          <span class="badge" v-if="card.ready === true">ready</span>
          <span class="badge blocked" v-if="card.ready === false">blocked</span>
          <span class="badge" v-if="card.severity">severity: {{ card.severity }}</span>
          <span class="badge tag" v-for="(tag, i) in card.tags" :key="i">{{ tag }}</span>
        </div>
        <p class="edges" v-if="card.blocked_by.length">blocked by: {{ card.blocked_by.join(', ') }}</p>
        <p class="edges" v-if="card.blocks.length">blocks: {{ card.blocks.join(', ') }}</p>
        <p class="edges" v-if="card.closed">closed {{ card.closed.date }}<span v-if="card.closed.commit">, commit {{ card.closed.commit.slice(0, 12) }}</span><span v-if="card.closed.reason">: {{ card.closed.reason }}</span></p>
        <details v-if="card.why"><summary>why</summary><pre class="why">{{ card.why }}</pre></details>
      </article>
    </section>
  </main>
</div>
<script src="%(vue)s" crossorigin="anonymous"></script>
<script>
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("data").textContent);
  Vue.createApp({ data: function () { return data; } }).mount("#app");
})();
</script>
</body>
</html>
"""


def render_html(board):
    return PAGE % {"data": script_json(board), "vue": VUE_URL}


def check_out(out):
    """Refused before the input is read, so a refusal writes nothing."""
    if not OUT_NAME_RE.fullmatch(os.path.basename(out)):
        die("--out %s: the file name must match roadmap-board-[a-z0-9-]+.html" % out)
    if os.path.islink(out):
        die("--out %s is a symlink -- refusing" % out)
    if os.path.lexists(out) and not os.path.isfile(out):
        die("--out %s exists and is not a regular file" % out)


def write_page(out, text):
    """Temp file in the target's directory, fsync, then os.replace: a reader
    never sees half a page, and the target is replaced, never written
    through."""
    directory = os.path.dirname(os.path.abspath(out))
    try:
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=TMP_PREFIX, suffix=TMP_SUFFIX)
    except OSError as exc:
        die("cannot create a temporary file in %s: %s"
            % (directory, exc.strerror or exc.__class__.__name__))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o644)
        if os.path.islink(out):
            die("--out %s became a symlink -- refusing" % out)
        os.replace(tmp, out)
    except OSError as exc:
        _discard(tmp)
        die("cannot write %s: %s" % (out, exc.strerror or exc.__class__.__name__))
    except BaseException:
        _discard(tmp)
        raise


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def build_parser():
    parser = _Parser(description="A read-only kanban board over a roadmap-export/2 "
                                 "document (roadmap.py export).")
    parser.add_argument("export", help="the export JSON file, or - for stdin")
    parser.add_argument("--format", default="md", help="md (default, stdout) or html")
    parser.add_argument("--out", metavar="PATH",
                        help="html only: roadmap-board-<name>.html to write")
    return parser


def main():
    args = build_parser().parse_args()
    if args.format not in ("md", "html"):
        die("--format %s: want md or html" % args.format)
    if args.format == "md" and args.out is not None:
        die("--out is for --format html; the markdown board goes to stdout")
    if args.format == "html":
        if args.out is None:
            die("--format html needs --out roadmap-board-<name>.html")
        check_out(args.out)
    board = build_board(parse_export(args.export, read_input(args.export)))
    try:
        if args.format == "md":
            _write_stdout(render_md(board))
        else:
            write_page(args.out, render_html(board))
            _write_stdout("board: %s\n" % _shown(args.out))
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
