#!/usr/bin/env python3
"""The search scripts' result parsers are single html.parser passes (R-0057, F32).

`parse_lite_results` (Scripts/search_duckduckgo.py) and
`extract_code_from_snippet` (Scripts/search_github.py, under
`parse_grep_results`) used to be regex scans -- `<tr[^>]*>(.*?)</tr>` with
re.findall, and `<tr data-line="(\\d+)">.*?<pre>(.*?)</pre>` with re.finditer,
both DOTALL.  A lazy scan with no closer in sight rescans to the end of the
input for every opener, so a hostile body (the endpoint, or a MITM on the
unverified Chrome path) cost quadratic time, and the grep.app shape cubic.  The
2 MiB body cap bounded that cost; it did not change its shape.  Both are now
one html.parser pass with the same rules.

Groups:
  A  DDG lite: every page parses to the fields the regex parser produced --
     recorded from it before it was deleted and pinned here as literals: the
     uddg redirect unwrapped, a direct href kept as written (`&amp;` and all),
     the default snippet, the row a link wins, the unterminated row dropped
  B  grep.app: the same for the snippet parser and parse_grep_results (limit,
     defaults, the GitHub URL anchored at the first line)
  C  linearity: a body shaped from the old patterns' worst case, sized one KiB
     under the module's own SEARCH_MAX_BYTES, parsed in a child process and
     bounded in wall time.  Measured red against the regex parsers: they
     exceeded the child timeout on every row (quadratic or cubic; at 64 KiB the
     unterminated-<tr> body already took 6.4 s, the unclosed-<pre> body 18 s
     at 16 KiB), while the html.parser passes take about a second at 2 MiB
  E  hygiene

html.parser has no implied end tags, so neither parser depends on one: state
is driven by the start tags and attributes that mark a result.  A declared
divergence, measured by dropping each tag of the fixtures in turn: on
CPython 3.14 an unclosed <title> (and, by the same rule, <textarea>, <script>,
<style>) swallows the rest of the page in html.parser, as it does in a browser,
so such a page parses to no results where the regex still found some; 3.9.21's
html.parser treats only <script>/<style> that way and agreed on every drop.  A case-folded tag
(`<TR>`) or a `<pre>` hidden in a comment is likewise read as a browser reads
it, not as the regex did.

Usage:
  python3 tests/test_search_parsers.py [--brief]
The case count lives in the SUITES table in tests/run.py, never here.
"""

import os
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "search_parsers"
DDG = H.repo_path("Scripts", "search_duckduckgo.py")
GH = H.repo_path("Scripts", "search_github.py")

# A hostile body must parse within this bound.  Measured on the development
# host at 2 MiB: 1.3 s for the worst DDG shape, 0.65 s for the worst grep.app
# shape -- the bound is the generous side of that, the regex parsers it
# replaced needed hours (DDG) and days (grep.app) for the same bodies.
BOUND_S = 10.0
# The child is killed past this; a parser that needs it is red regardless.
CHILD_TIMEOUT_S = 60.0
# How far under the module's SEARCH_MAX_BYTES the hostile body is built.
UNDER_CAP = 1024


# ---------------------------------------------------------------------------
# Group A fixtures -- DDG lite pages
# ---------------------------------------------------------------------------

UDDG = "//duckduckgo.com/l/?uddg=https%3A%2F%2F{}&amp;rut=0123abcd"


def _num(n):
    return ('\n            <tr>\n              \n                <td valign="top">\n                  \n'
            '                    %d.&nbsp;\n                  \n                </td>\n' % n)


def _link_row(n, anchor):
    return (_num(n) + '                <td>\n                  ' + anchor
            + '\n                </td>\n                \n            </tr>\n')


def _snippet_row(text):
    return ('\n                <!-- Only show abstract separately if there\'s a click URL (not EOF) -->\n'
            '                <tr>\n                  <td>&nbsp;&nbsp;&nbsp;</td>\n'
            "                  <td class='result-snippet'>\n                    " + text
            + '\n                  </td>\n                </tr>\n')


def _linktext_row(host):
    return ('\n              <tr>\n                <td>&nbsp;&nbsp;&nbsp;</td>\n                <td>\n'
            "                  <span class='link-text'>" + host + '</span>\n                  \n'
            '                </td>\n              </tr>\n')


SPACER = '\n            <tr>\n              <td>&nbsp;</td>\n              <td>&nbsp;</td>\n            </tr>\n'

HEAD = ('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<html>\n<head>\n'
        '  <meta http-equiv="content-type" content="text/html; charset=UTF-8">\n'
        '  <title>speed test at DuckDuckGo</title>\n'
        '  <link title="DuckDuckGo (Lite)" type="application/opensearchdescription+xml" rel="search" '
        'href="//duckduckgo.com/opensearch_lite_v2.xml">\n'
        '</head>\n<body>\n  <form action="/lite/" method="post">\n  <table class="header">\n    <tr>\n      <td>\n'
        '        <input class="query" type="text" size="40" name="q" value="speed test" >\n'
        '        <input class="submit" type="submit" value="Search">\n      </td>\n    </tr>\n  </table>\n'
        '  <input type="hidden" name="kl" value="wt-wt">\n  </form>\n'
        "  <p class='extra'>&nbsp;</p>\n  <table border=\"0\">\n    \n  </table>\n\n  <table border=\"0\">\n"
        '      <!-- Web results are present -->\n')
TAIL = ('\n  </table>\n  <table class="nav-link"><tr><td><form action="/lite/" method="post">'
        '<input type="submit" class=\'navbutton\' value="Next Page &gt;"></form></td></tr></table>\n</body>\n</html>\n')

# The live lite page's row structure (number+link row, snippet row, link-text
# row, spacer row per result), written here rather than committed: no captured
# DDG body is checked in.
DDG_FULL = HEAD + (
    # 1: the measured shape -- rel, href, class; &amp; in the title, <b> in the snippet
    _link_row(1, '<a rel="nofollow" href="' + UDDG.format("www.speedtest.net%2F")
              + '" class=\'result-link\'>Speedtest by Ookla - Broadband &amp; 5G</a>')
    + _snippet_row('<b>Test</b> your internet speed on any device with Speedtest by Ookla.')
    + _linktext_row("www.speedtest.net") + SPACER
    # 2: class before href, double quotes; a numeric charref in the snippet
    + _link_row(2, '<a class="result-link" rel="nofollow" href="' + UDDG.format("fast.com%2F")
                + '">Internet Speed Test | Fast.com</a>')
    + _snippet_row('How fast is your download speed? In seconds, FAST.com&#39;s simple test estimates it.')
    + _linktext_row("fast.com") + SPACER
    # 3: a sponsored row -- a direct y.js href (no uddg) keeps its raw &amp;
    + '\n            <tr class="result-sponsored">\n                <td valign="top">&nbsp;</td>\n                <td>\n'
      '                  <a rel="nofollow" href="https://duckduckgo.com/y.js?ad_domain=example.com&amp;'
      'ad_provider=bingv7aa&amp;ad_type=txad" class=\'result-link\'>Example Ad &ndash; Fast Internet</a>\n'
      '                </td>\n            </tr>\n'
    + _snippet_row('Sponsored text with a &lt;tag&gt; that is text.')
    # 4: no snippet row before the next result
    + _link_row(4, '<a rel="nofollow" href="' + UDDG.format("speed.cloudflare.com%2F")
                + '" class=\'result-link\'>Cloudflare <b>Speed</b> Test</a>')
    + _linktext_row("speed.cloudflare.com") + SPACER
    # 5: a non-result anchor first in the row; a nested tag and &quot; in the title
    + _link_row(5, '<a href="/settings">settings</a> <a rel="nofollow" href="' + UDDG.format("www.att.com%2Fsupport%2F")
                + '" class=\'result-link\'>AT&amp;T &quot;Official&quot; <span>Site</span></a>')
    + _snippet_row('Line one\n                    line two &amp; <i>three</i>')
    # 6: an empty title -- dropped, and the snippet after it attached to nothing
    + _link_row(6, '<a rel="nofollow" href="' + UDDG.format("empty.example%2F") + '" class=\'result-link\'></a>')
    + _snippet_row('orphan snippet')
    + _snippet_row('second orphan')
    # 7: class result-link-x is not result-link
    + _link_row(7, "<a href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fnot.a.result%2F\" "
                "class='result-link-x'>Not a result</a>")
    + _snippet_row('snippet after a non-result row')
    # 8: the last result, no snippet, flushed at the end
    + _link_row(8, '<a rel="nofollow" href="' + UDDG.format("www.ookla.com%2Fconsumer")
                + '" class=\'result-link\'>Consumer | Ookla&reg;</a>')
    + _linktext_row("www.ookla.com")
) + TAIL

DDG_FULL_WANT = [
    {'url': 'https://www.speedtest.net/', 'title': 'Speedtest by Ookla - Broadband & 5G',
     'snippet': 'Test your internet speed on any device with Speedtest by Ookla.'},
    {'url': 'https://fast.com/', 'title': 'Internet Speed Test | Fast.com',
     'snippet': "How fast is your download speed? In seconds, FAST.com's simple test estimates it."},
    {'url': 'https://duckduckgo.com/y.js?ad_domain=example.com&amp;ad_provider=bingv7aa&amp;ad_type=txad',
     'title': 'Example Ad – Fast Internet', 'snippet': 'Sponsored text with a <tag> that is text.'},
    {'url': 'https://speed.cloudflare.com/', 'title': 'Cloudflare Speed Test', 'snippet': 'No snippet available'},
    {'url': 'https://www.att.com/support/', 'title': 'AT&T "Official" Site',
     'snippet': 'Line one\n                    line two & three'},
    {'url': 'https://www.ookla.com/consumer', 'title': 'Consumer | Ookla®', 'snippet': 'No snippet available'},
]

# A stray snippet before any link; a link row that also holds a result-snippet
# cell (the link wins); a single-quoted href whose uddg target has a query; a
# direct href; a relative href; an unterminated row at EOF (dropped).
DDG_EDGES = (
    "<html><body><table>"
    "<tr><td class='result-snippet'>before any link</td></tr>"
    "<tr><td><a href=\"" + UDDG.format("a.example%2F") + "\" class='result-link'>A</a></td>"
    "<td class='result-snippet'>same row as the link</td></tr>"
    "<tr><td class=\"result-snippet\">A's snippet</td></tr>"
    "<tr><td><a class='result-link' href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fb.example%2Fx%3Fy%3D1'>"
    "B &amp; b</a></td></tr>"
    "<tr><td>&nbsp;</td><td class='result-snippet'>  B's &nbsp;snippet  </td></tr>"
    "<tr><td><a href='https://direct.example/p?a=1&amp;b=2' class='result-link'>Direct</a></td></tr>"
    "<tr><td><a href='/relative/path' class='result-link'>Relative</a></td></tr>"
    "<tr><td class='result-snippet'>relative's snippet</td></tr>"
    "<tr><td><a href=\"" + UDDG.format("never.closed%2F") + "\" class='result-link'>Unterminated row</a></td>"
    "</table></body></html>"
)

DDG_EDGES_WANT = [
    {'url': 'https://a.example/', 'title': 'A', 'snippet': "A's snippet"},
    {'url': 'https://b.example/x?y=1', 'title': 'B & b', 'snippet': "B's \xa0snippet"},
    {'url': 'https://direct.example/p?a=1&amp;b=2', 'title': 'Direct', 'snippet': 'No snippet available'},
    {'url': '/relative/path', 'title': 'Relative', 'snippet': "relative's snippet"},
]

# tests/test_mcp_chrome.py:lite_page()'s markup, the page every host row uses.
DDG_LITE_PAGE = (
    "<html><body><form action=\"/lite/\" method=\"post\"><input class=\"query\" type=\"text\" name=\"q\" "
    "value=\"test\"></form><table>"
    "<tr><td valign=\"top\">1.&nbsp;</td><td><a rel=\"nofollow\" "
    "href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fdoc&amp;rut=abc\" class='result-link'>"
    "Example <b>Doc</b></a></td></tr>"
    "<tr><td>&nbsp;</td><td class='result-snippet'>A <b>tested</b> snippet.</td></tr>"
    "</table></body></html>"
)

DDG_NO_RESULTS = ("<html><body><form action=\"/lite/\" method=\"post\"><input name=\"q\" value=\"q\"></form>"
                  "<table border=\"0\"><tr><td>No results.</td></tr></table></body></html>")

DDG_CASES = [
    ("lite-page-one-result", DDG_LITE_PAGE,
     [{'url': 'https://example.org/doc', 'title': 'Example Doc', 'snippet': 'A tested snippet.'}]),
    ("measured-shape-eight-rows", DDG_FULL, DDG_FULL_WANT),
    ("edges-stray-same-row-direct-unterminated", DDG_EDGES, DDG_EDGES_WANT),
    ("no-results-page", DDG_NO_RESULTS, []),
    ("empty-body", "", []),
]


# ---------------------------------------------------------------------------
# Group B fixtures -- grep.app snippets
# ---------------------------------------------------------------------------

def _gh_row(n, code):
    return ('<tr data-line="%d"><td><div class="lineno">%d</div></td>'
            '<td><div class="highlight"><pre>%s</pre></div></td></tr>' % (n, n, code))


GH_TABLE = ('<table class="highlight-table">'
            + _gh_row(14, '<span class="kd">const</span> [a, setA] = <mark>useState</mark>(0);')
            + _gh_row(15, '  <mark>useEffect</mark>(() =&gt; {')
            + _gh_row(16, '')
            + _gh_row(17, '    if (a &amp;&amp; b) return &lt;div/&gt;;   ')
            + '<tr class="jump"><td colspan="2"><div class="jump">...</div></td></tr>'
            + _gh_row(42, '\t<span class="p">}</span>, [<mark>a</mark>]);')
            + _gh_row(43, '&nbsp;&nbsp;')
            + '</table>')

GH_TABLE_WANT = [(14, 'const [a, setA] = useState(0);'), (15, 'useEffect(() => {'),
                 (17, 'if (a && b) return <div/>;'), (42, '}, [a]);')]

# A row with no <pre> before the next row: its number takes the next <pre> (the
# regex's `.*?` crossed the row); a <pre> with attributes is skipped; a
# single-quoted or extra-attribute <tr> is not a line; a nested <pre> ends at
# the first </pre>; an unterminated <pre> yields nothing.
GH_EDGES = ('<table>'
            '<tr data-line="3"><td>no pre here</td></tr>'
            '<tr data-line="4"><td><pre>four</pre></td></tr>'
            '<tr data-line="5"><td><pre class="x">skipped</pre><pre>five</pre></td></tr>'
            '<tr data-line=\'6\'><td><pre>single-quoted attr</pre></td></tr>'
            '<tr data-line="7" class="c"><td><pre>extra attr</pre></td></tr>'
            '<tr data-line="8"><td><pre>a <b>nested <pre>inner</pre> tail</b></pre></td></tr>'
            '<tr data-line="9"><td><pre>never closed'
            '</td></tr></table>')

GH_EDGES_WANT = [(3, 'four'), (5, 'five'), (8, 'a nested inner')]

# tests/test_mcp_chrome.py:GH_CANNED's snippet.
GH_CANNED_SNIPPET = '<table><tr data-line="7"><td><pre>use<mark>Effect</mark>(fn)</pre></td></tr></table>'

GH_CASES = [
    ("canned-one-line", GH_CANNED_SNIPPET, [(7, 'useEffect(fn)')]),
    ("highlight-table-seven-rows", GH_TABLE, GH_TABLE_WANT),
    ("edges-crossed-row-attrs-nested-unterminated", GH_EDGES, GH_EDGES_WANT),
    ("no-rows", "<table></table>", []),
    ("empty", "", []),
]

GH_DATA = {"hits": {"hits": [
    {"repo": "octo/demo", "path": "src/app.py", "branch": "main", "content": {"snippet": GH_CANNED_SNIPPET}},
    {"repo": "facebook/react", "path": "packages/react/src/Hooks.JS", "branch": "trunk",
     "content": {"snippet": GH_TABLE}},
    {"path": "README", "content": {"snippet": ""}},
    {"repo": "x/y", "path": "lib/z.rs", "branch": "dev", "content": {"snippet": GH_EDGES}},
    {"repo": "over/limit", "path": "a.go", "branch": "main", "content": {"snippet": GH_CANNED_SNIPPET}},
]}}

GH_DATA_WANT = [
    {'repo': 'octo/demo', 'file_path': 'src/app.py', 'branch': 'main', 'language': 'Python',
     'code_lines': [(7, 'useEffect(fn)')], 'url': 'https://github.com/octo/demo/blob/main/src/app.py#L7'},
    {'repo': 'facebook/react', 'file_path': 'packages/react/src/Hooks.JS', 'branch': 'trunk',
     'language': 'JavaScript', 'code_lines': GH_TABLE_WANT,
     'url': 'https://github.com/facebook/react/blob/trunk/packages/react/src/Hooks.JS#L14'},
    {'repo': 'Unknown', 'file_path': 'README', 'branch': 'main', 'language': 'Unknown', 'code_lines': [],
     'url': 'https://github.com/Unknown/blob/main/README'},
    {'repo': 'x/y', 'file_path': 'lib/z.rs', 'branch': 'dev', 'language': 'Rust', 'code_lines': GH_EDGES_WANT,
     'url': 'https://github.com/x/y/blob/dev/lib/z.rs#L3'},
    {'repo': 'over/limit', 'file_path': 'a.go', 'branch': 'main', 'language': 'Go',
     'code_lines': [(7, 'useEffect(fn)')], 'url': 'https://github.com/over/limit/blob/main/a.go#L7'},
]


# ---------------------------------------------------------------------------
# Group C -- hostile bodies, shaped from the old patterns' worst case
# ---------------------------------------------------------------------------

# (cid, host key, function, prefix, unit, suffix, what it attacked)
HOSTILE = [
    ("ddg-unterminated-tr-openers", "ddg", "parse_lite_results", "", "<tr>", "",
     "re.findall(r'<tr[^>]*>(.*?)</tr>') rescans to EOF from every <tr>"),
    ("ddg-one-row-of-classless-anchors", "ddg", "parse_lite_results", "<tr>", "<a ", "</tr>",
     "<a[^>]*class=... backtracks over the row from every <a"),
    ("gh-unterminated-data-line-rows", "gh", "extract_code_from_snippet", "", '<tr data-line="1">', "",
     "<tr data-line=..>.*?<pre> rescans to EOF from every row"),
    ("gh-rows-with-unclosed-pre", "gh", "extract_code_from_snippet", "", '<tr data-line="1"><pre>', "",
     "<pre>(.*?)</pre> nested in .*?<pre>: a rescan per row per <pre>"),
]

CHILD = r'''
import importlib.util, sys, time
path, func, prefix, unit, suffix, size = sys.argv[1:7]
size = int(size)
spec = importlib.util.spec_from_file_location("hostile_under_test", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
body = prefix + unit * ((size - len(prefix) - len(suffix)) // len(unit)) + suffix
t0 = time.perf_counter()
out = getattr(mod, func)(body)
print("%.3f %d %d" % (time.perf_counter() - t0, len(out), len(body)))
'''


def hostile_row(suite, cid, path, func, prefix, unit, suffix, size, attacked):
    """Parse one hostile body in a child; FAIL past BOUND_S or the child timeout."""
    argv = [sys.executable, "-B", "-c", CHILD, path, func, prefix, unit, suffix, str(size)]
    detail = ["module      : %s" % os.path.relpath(path, H.REPO_ROOT),
              "body        : %r x N, %d chars (%d under the cap)" % (unit, size, UNDER_CAP),
              "old shape   : %s" % attacked]
    try:
        rc, out, err = H.run_process(argv, timeout=CHILD_TIMEOUT_S, cwd=H.REPO_ROOT)
    except subprocess.TimeoutExpired:
        suite.record("C", cid, ["no answer within the %.0f s child timeout" % CHILD_TIMEOUT_S],
                     detail=detail)
        return
    fields = out.split()
    if rc != 0 or len(fields) != 3:
        suite.record("C", cid, ["child rc=%d out=%r err=%r" % (rc, out[-200:], err[-400:])],
                     detail=detail)
        return
    elapsed, nres, nbody = float(fields[0]), int(fields[1]), int(fields[2])
    problems = []
    if elapsed >= BOUND_S:
        problems.append("parsed in %.3f s, bound %.1f s" % (elapsed, BOUND_S))
    if nres != 0:
        problems.append("%d result(s) from a body with no complete result" % nres)
    if nbody < size - len(unit):
        problems.append("body is %d chars, wanted ~%d" % (nbody, size))
    suite.record("C", cid, problems,
                 detail=detail + ["parse time  : %.3f s (bound %.1f s)" % (elapsed, BOUND_S)])


def group_c(suite, targets):
    """targets: host key -> (module path, SEARCH_MAX_BYTES)."""
    for cid, key, func, prefix, unit, suffix, attacked in HOSTILE:
        path, cap = targets[key]
        hostile_row(suite, cid, path, func, prefix, unit, suffix, cap - UNDER_CAP, attacked)


# ---------------------------------------------------------------------------
# Groups A, B, E
# ---------------------------------------------------------------------------

def _diff(got, want):
    if got == want:
        return []
    if len(got) != len(want):
        return ["%d result(s), want %d: got %r" % (len(got), len(want), got)]
    first = next(i for i, (g, w) in enumerate(zip(got, want)) if g != w)
    return ["first differing result #%d: got %r, want %r" % (first, got[first], want[first])]


def group_a(suite, ddg):
    for cid, page, want in DDG_CASES:
        got = ddg.parse_lite_results(page)
        suite.record("A", cid, _diff(got, want),
                     detail=["results     : %d got, %d want" % (len(got), len(want))])


def group_b(suite, gh):
    for cid, snippet, want in GH_CASES:
        got = gh.extract_code_from_snippet(snippet)
        suite.record("B", cid, _diff(got, want),
                     detail=["lines       : %d got, %d want" % (len(got), len(want))])
    for limit in (10, 3):
        want = GH_DATA_WANT[:limit]
        got = gh.parse_grep_results(GH_DATA, limit)
        suite.record("B", "parse-grep-results-limit-%d" % limit, _diff(got, want),
                     detail=["results     : %d got, %d want" % (len(got), len(want))])


def group_e(suite, before, pyc_before):
    new = sorted(H.repo_tree() - before)
    suite.record("E", "no-new-repo-paths",
                 [] if not new else ["this suite wrote into the repo tree: %s" % new[:12]])
    pyc_after = H.pycache_snapshot()
    changed = sorted(p for p in pyc_after if pyc_before.get(p) != pyc_after[p])
    suite.record("E", "no-new-bytecode",
                 [] if not changed else ["this run wrote or touched bytecode: %s" % changed[:6]])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="search result parsers: single html.parser passes, "
                          "the regex parsers' fields, linear on a hostile body",
                    opts=opts, mode="stream", group_width=3, cid_width=48)
    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()
    ddg = H.load_module_from_path("search_parsers_ddg", DDG)
    gh = H.load_module_from_path("search_parsers_gh", GH)

    group_a(suite, ddg)
    group_b(suite, gh)
    group_c(suite, {"ddg": (DDG, ddg.SEARCH_MAX_BYTES), "gh": (GH, gh.SEARCH_MAX_BYTES)})
    group_e(suite, before, pyc_before)

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
