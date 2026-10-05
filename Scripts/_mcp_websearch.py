#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for web search: DuckDuckGo lite first, Bing on a DDG block.

The domain is ONE question: how a query becomes web results -- the request each
endpoint is sent, the page each one answers with and how it is parsed, the
signal that says an answer is a bot block rather than an empty result, and the
run loop that moves a batch from DDG to Bing when DDG blocks. It is a source of
its own and not a corner of `_mcp_chrome.py` because that file answers how to
speak HTTP like Chrome and nothing about what a search engine's page means; the
rule that decided it is ADR 0014's. Its sibling `_mcp_codesearch.py` holds code
search (grep.app), a different endpoint with a different page and block signal.

**Hosts.** `Scripts/search_duckduckgo.py` (the CLI) and `Scripts/mcp-search.py`
(the MCP server) take this source WHOLE: one marker naming every block, in
source order, because the blocks call one another and `host_provides` offers a
region only the host's imports, never a name another region defines.

**The session is injected, never created here.** A search block is handed a
session object (`get` / `post` / `last_navigation_url` / `close`, the generated
`_ChSession`); creating one needs `_ch_session_new`, `_DECODERS` and
`_ch_public_only_policy`, which are names of the HOST and of another source, so
the host owns `create_session`, pacing, rotation and -- in the server -- the
per-endpoint lock. `run_web` reaches a session only through the host's
`with_session(endpoint, fn)` hook, one call per query, so the DDG lock is always
released before the Bing lock is taken.

**Result contract.** `search_ddg` / `search_bing` return a list (the results,
possibly empty -- also on a transport failure) or None (a bot block, judged by
`_ddg_blocked` / `_bing_blocked` on the endpoint's own host only). Two optional
callables replace the stderr lines the CLI used to print from inside them:

* `note(event, query, detail)` -- `event` is a fixed token (`ddg_undecodable`,
  `ddg_error`, `ddg_captcha`, `bing_http`, `bing_undecodable`, `bing_error`),
  `detail` the status code, the decode error or the exception object. The CLI
  renders its historical stderr text from it; the server logs structure only.
* `on_transport_error(exc)` -- called in the `except Exception` arm before the
  `[]` is returned, so a caller can tell a transport failure from no results.

Both default to a no-op, so a caller that passes neither sees nothing.

**Tab safety is a constraint on how this file is WRITTEN.** One host indents
with tabs, and `Scripts/amalgamate.py:block_is_tab_safe` refuses a block that
joins a line inside an open bracket or continues one with a backslash. So every
call and literal here fits one physical line, and every multi-line table is a
builder function plus one single-target assignment (`_start_close` /
`_START_CLOSE`, `_end_priority` / `_END_PRIORITY`): the generator keys blocks
only on `def`, `class` and `NAME = ...`, and silently drops any other
module-level statement, so a module-level `_START_CLOSE["p"] = ...` row would
vanish from every generated copy.

**Block contract.** A block reads only builtins, the stdlib names this module
imports (`base64`, `random`, `re`, `time`, `unicodedata`, `urllib.parse`,
`HTMLParser`, `parse_qs`, `urlparse`), its own arguments and the blocks co-listed on the same
marker. Never `_ch_session_new`, `_ch_public_only_policy`, `_DECODERS`,
`ChromeClientError`, `log`, `ROTATE_EVERY` or `SEARCH_MAX_BYTES` -- those stay
hand-written in each host.

**No host imports this module**, for the reasons `_mcp_json.py` gives. The test
fleet does: `tests/test_search_parsers.py` and `tests/test_mcp_chrome.py` drive
the generated copies in the hosts.
"""

import base64
import random
import re
import time
import unicodedata
import urllib.parse
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse


def _normalize(text):
    return re.sub(r'\s+', ' ', text).strip() if text else ""


# Rendering third-party text (titles, snippets, URLs) into a reply the model
# reads. A result field is attacker-chosen -- the engine's page, or a MITM on
# the unverified Chrome path -- so before it is rendered every code point that
# is invisible or steers the display is DROPPED: C0 controls (except those in
# `keep`), DEL and C1 (category Cc), every format character (Cf: the bidi
# controls U+200E U+200F U+061C U+202A-202E U+2066-2069, the zero-width
# U+200B-200D U+2060 U+FEFF, the tag block U+E0001 U+E0020-E007F, the soft
# hyphen), lone surrogates (Cs), the variation selectors U+FE00-FE0F and
# U+E0100-E01EF and the rest of the U+E0000 block (data smuggling). The line
# and paragraph separators U+2028 U+2029 U+0085 become "\n". The SAME function
# lives in `_mcp_codesearch.py` as `_code_clean`: a region resolves names only
# against its own source (ADR 0014), so the two domains carry one copy each and
# tests/test_mcp_search.py asserts the copies agree on every class.
_WEB_LINE_SEPARATORS = "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\x85"


def _web_clean(text, keep):
    """*text* with every invisible or display-steering code point dropped.

    A character in *keep* (e.g. "\\t\\n") survives; U+2028 U+2029 U+0085 become
    "\\n". Printable ASCII is the fast path; the rest is judged by category.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if 0x20 <= code < 0x7f or ch in keep:
            out.append(ch)
        elif ch in _WEB_LINE_SEPARATORS:
            out.append("\n")
        elif 0xfe00 <= code <= 0xfe0f or 0xe0000 <= code <= 0xe01ef:
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def _web_line(text):
    """A single-line field: _web_clean, then every whitespace run one space (F2)."""
    if text is None:
        return ""
    return " ".join(_web_clean(str(text), "\t\n\r").split())


# The printable ASCII a rendered URL keeps as written (letters, digits and
# "_.-~" always are); a space, '"', '<', '>', '\\', '`' and every non-ASCII
# character are percent-encoded.
_WEB_URL_SAFE = "!#$%&'()*+,-./:;=?@[]^_{|}~"


def _web_url(url):
    """A result URL safe to render, or None when it must not be a link (F3).

    Controls and invisibles are dropped first; then only an absolute http(s)
    URL with a host survives. A non-ASCII host is rendered in its IDNA
    (punycode) form so a homoglyph cannot pose as another site -- None when it
    has none -- and a non-ASCII path, query or fragment is percent-encoded.
    """
    if not isinstance(url, str):
        return None
    url = _web_clean(url, " ").replace("\n", "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    head = len(parts.scheme) + 3
    if url[len(parts.scheme):head] != "://" or url[head:head + len(parts.netloc)] != parts.netloc:
        return None
    netloc = parts.netloc
    if not netloc.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        userinfo = netloc.rpartition("@")[0]
        netloc = (urllib.parse.quote(userinfo, safe=_WEB_URL_SAFE) + "@" if "@" in parts.netloc else "") + host + ("" if port is None else ":%d" % port)
    return parts.scheme + "://" + urllib.parse.quote(netloc, safe=_WEB_URL_SAFE) + urllib.parse.quote(url[head + len(parts.netloc):], safe=_WEB_URL_SAFE)


def decode_duckduckgo_url(ddg_url):
    match = re.search(r'uddg=([^&]+)', ddg_url)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    if ddg_url.startswith('http'):
        return ddg_url
    if ddg_url.startswith('//'):
        return 'https:' + ddg_url
    return ddg_url


def _raw_href(start_tag):
    """The href value exactly as written in a raw start tag, entities NOT decoded.

    The regex parser this replaced captured `href=['"]([^'"]+)['"]` from the raw
    markup, so a direct (non-uddg) link kept its `&amp;`; html.parser decodes
    attribute values, so the raw tag is read instead. One left-to-right pass.

    It reads the ATTRIBUTE named href (F14), never the first "href=" substring:
    a `data-href=` or an `href=` inside another attribute's quoted value is
    skipped. The first href attribute decides; a quoted non-empty value is
    returned, an unquoted or empty one is None, as the regex had it.
    """
    space = " \t\n\r\f"
    n = len(start_tag)
    i = 1
    while i < n and start_tag[i] not in space + "/>":
        i += 1  # the tag name
    while i < n:
        while i < n and start_tag[i] in space + "/":
            i += 1
        start = i
        while i < n and start_tag[i] not in space + "/>=":
            i += 1
        name = start_tag[start:i].lower()
        if not name:
            if i < n and start_tag[i] == "=":
                i += 1
                continue
            return None
        value = None
        j = i
        while j < n and start_tag[j] in space:
            j += 1
        if j < n and start_tag[j] == "=":
            j += 1
            while j < n and start_tag[j] in space:
                j += 1
            if j < n and start_tag[j] in "'\"":
                end = start_tag.find(start_tag[j], j + 1)
                if end < 0:
                    return None
                value = start_tag[j + 1:end]
                j = end + 1
            else:
                while j < n and start_tag[j] not in space + ">":
                    j += 1
        i = j
        if name == "href":
            return value or None
    return None


def _has_class(attrs, value):
    return any(name == "class" and val == value for name, val in attrs)


# The lite page used to be cut into rows with re.findall(r'<tr[^>]*>(.*?)</tr>')
# and each row searched for a result-link anchor and a result-snippet cell. On
# a hostile body (the endpoint, or a MITM on the unverified Chrome path) those
# lazy DOTALL scans rescan to the end of the input for every unterminated
# opener: quadratic in the body (F32, R-0057). This is one html.parser pass with
# the same rules, keyed on start tags and attributes and never on an implied end
# tag (html.parser has none): a row is a <tr> up to the first </tr>, an
# unterminated row is dropped, a row's first result-link anchor wins over any
# result-snippet cell in it, and a title or snippet is the text up to the first
# </a> or </td>, tags dropped and entities decoded.
class _LiteParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []  # (link_url, link_title, snippet), one per closed row
        self._in_row = False
        self._link = None  # [url, parts, closed]
        self._snippet = None  # [parts, closed]

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if not self._in_row:
                self._in_row = True
                self._link = None
                self._snippet = None
            return
        if not self._in_row:
            return
        if tag == "a" and self._link is None and _has_class(attrs, "result-link"):
            href = _raw_href(self.get_starttag_text() or "")
            if href:
                self._link = [href, [], False]
        elif tag == "td" and self._snippet is None and _has_class(attrs, "result-snippet"):
            self._snippet = [[], False]

    def handle_endtag(self, tag):
        if not self._in_row:
            return
        if tag == "tr":
            link = self._link if self._link and self._link[2] else None
            snippet = self._snippet if self._snippet and self._snippet[1] else None
            self.rows.append((link[0] if link else None, "".join(link[1]) if link else None, "".join(snippet[0]) if snippet else None))
            self._in_row = False
            self._link = None
            self._snippet = None
        elif tag == "a" and self._link and not self._link[2]:
            self._link[2] = True
        elif tag == "td" and self._snippet and not self._snippet[1]:
            self._snippet[1] = True

    def handle_data(self, data):
        if not self._in_row:
            return
        if self._link and not self._link[2]:
            self._link[1].append(data)
        if self._snippet and not self._snippet[1]:
            self._snippet[0].append(data)


def parse_lite_results(html_content):
    results = []
    parser = _LiteParser()
    try:
        parser.feed(html_content)
        parser.close()
    except Exception:
        pass

    current = {}
    for link_url, link_title, snippet in parser.rows:
        if link_url:
            if current.get('title') and current.get('url'):
                if 'snippet' not in current:
                    current['snippet'] = 'No snippet available'
                results.append(current)
            current = {'url': decode_duckduckgo_url(link_url), 'title': link_title.strip()}
            continue

        if snippet is not None and current.get('title'):
            current['snippet'] = snippet.strip()
            results.append(current)
            current = {}

    if current.get('title') and current.get('url'):
        if 'snippet' not in current:
            current['snippet'] = 'No snippet available'
        results.append(current)

    return results


def _decode_bing_url(href):
    """Decode Bing's base64-wrapped redirect URLs."""
    if not href or not href.startswith("https://www.bing.com/ck/a"):
        return href
    try:
        u_param = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u_param and len(u_param) > 2:
            b = u_param[2:]
            return base64.urlsafe_b64decode(b + "=" * ((-len(b)) % 4)).decode()
    except Exception:
        pass
    return href


# This parser used to be lxml XPath (the deedy5/ddgs approach):
#   results  //li[contains(@class, 'b_algo')]
#   href     ./h2/a/@href | ./div[contains(@class, 'header')]/a/@href   (first)
#   title    ./h2/a//text() | ./div[contains(@class, 'header')]/a/h2//text()
#   snippet  .//p//text()
# It is now a stdlib html.parser tree builder that evaluates those same four
# expressions by hand, so the script needs no third-party parser. Equivalence was
# measured against the lxml version on tests/files/html/tf_bing_serp.html, whose
# expected fields are the lxml output; tests/test_py_deps.py pins them.

# Elements with no content: never pushed on the open-element stack.
_VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))

# new start tag -> the open elements it implicitly closes while one of them is on
# TOP of the stack. Transcribed from libxml2's htmlStartClose table, which is
# what lxml applied: e.g. a snippet <p> left unclosed before a <div> must not
# swallow the div, and a result whose </h2> and </li> are both missing must not
# swallow the next result. libxml2's `head` entries are left out on purpose:
# nothing under <head> is ever a result, so they cannot change a field.
_H = ("h1", "h2", "h3", "h4", "h5", "h6")


_LISTING = ("address", "pre", "listing", "xmp")


_FONTSTYLE = ("tt", "i", "b", "u", "s", "strike", "big", "small")


def _start_close():
    """The htmlStartClose table, one row per line (a builder: tab-safe, and a
    module-level subscript row would be dropped by the generator)."""
    t = {}
    t["form"] = frozenset(("form", "p", "hr") + _H + ("dl", "ul", "ol", "menu", "dir") + _LISTING)
    t["title"] = frozenset(("p",))
    t["body"] = frozenset(("style", "script", "title"))
    t["frameset"] = frozenset(("style", "script", "title"))
    t["li"] = frozenset(("p",) + _H + ("dl",) + _LISTING + ("li",))
    t["hr"] = frozenset(("p",))
    t["h1"] = frozenset(("p", "h2", "h3", "h4", "h5", "h6"))
    t["h2"] = frozenset(("p", "h1", "h3", "h4", "h5", "h6"))
    t["h3"] = frozenset(("p", "h1", "h2", "h4", "h5", "h6"))
    t["h4"] = frozenset(("p", "h1", "h2", "h3", "h5", "h6"))
    t["h5"] = frozenset(("p", "h1", "h2", "h3", "h4", "h6"))
    t["h6"] = frozenset(("p", "h1", "h2", "h3", "h4", "h5"))
    t["dir"] = frozenset(("p",))
    t["address"] = frozenset(("p", "ul"))
    t["pre"] = frozenset(("p", "ul"))
    t["listing"] = frozenset(("p",))
    t["xmp"] = frozenset(("p",))
    t["blockquote"] = frozenset(("p",))
    t["dl"] = frozenset(("p", "dt", "menu", "dir") + _LISTING)
    t["dt"] = frozenset(("p", "menu", "dir") + _LISTING + ("dd",))
    t["dd"] = frozenset(("p", "menu", "dir") + _LISTING + ("dt",))
    t["ul"] = frozenset(("p", "ol", "menu", "dir") + _LISTING)
    t["ol"] = frozenset(("p", "ul"))
    t["menu"] = frozenset(("p", "ul"))
    t["p"] = frozenset(("p",) + _H + _FONTSTYLE)
    t["div"] = frozenset(("p",))
    t["noscript"] = frozenset(("script",))
    t["center"] = frozenset(("font", "b", "i", "p"))
    t["a"] = frozenset(("a",))
    t["caption"] = frozenset(("p",))
    t["colgroup"] = frozenset(("caption", "colgroup", "col", "p"))
    t["col"] = frozenset(("caption", "col", "p"))
    t["table"] = frozenset(("p",) + _H + ("pre", "listing", "xmp", "a"))
    t["th"] = frozenset(("th", "td", "p", "span", "font", "a", "b", "i", "u"))
    t["td"] = frozenset(("th", "td", "p", "span", "font", "a", "b", "i", "u"))
    t["tr"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "p"))
    t["thead"] = frozenset(("caption", "col", "colgroup"))
    t["tfoot"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "thead", "tbody", "p"))
    t["tbody"] = frozenset(("th", "td", "tr", "caption", "col", "colgroup", "thead", "tfoot", "tbody", "p"))
    t["optgroup"] = frozenset(("option",))
    t["option"] = frozenset(("option",))
    t["fieldset"] = frozenset(("legend", "p") + _H + ("pre", "listing", "xmp", "a"))
    return t


_START_CLOSE = _start_close()


def _end_priority():
    """libxml2's end-tag priorities: an end tag may only close the open elements
    above its match if none of them outranks it, otherwise it is IGNORED. So with
    a stray <div> left open inside a result, `</li>` does not end the result and
    the next <li class="b_algo"> nests inside it. Measured: without this rule a
    single dropped </div> made 17 of the fixture's 26 variants disagree with lxml.
    """
    t = {}
    t["div"] = 150
    t["td"] = 160
    t["th"] = 160
    t["tr"] = 170
    t["thead"] = 180
    t["tbody"] = 180
    t["tfoot"] = 180
    t["table"] = 190
    t["head"] = 200
    t["body"] = 200
    t["html"] = 220
    return t


_END_PRIORITY = _end_priority()


_END_PRIORITY_DEFAULT = 100


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        self.children = []  # _Node or str (a text node), in document order


# F7: the end-tag scan walks the open-element stack, so a hostile body (deep
# unclosed elements, then many end tags that scan them and close nothing) was
# quadratic. Three bounds keep it linear: an end tag with no open element of
# its name returns at once (exactly what the full scan concluded), an element
# opened past _TREE_MAX_DEPTH is attached but never pushed (libxml2 refuses a
# tree past 256 levels; a real results page is a few dozen deep), and every
# scan step spends one unit of a per-document _TREE_SCAN_BUDGET, past which
# end tags are ignored. Only a hostile page reaches either cap.
_TREE_MAX_DEPTH = 512


_TREE_SCAN_BUDGET = 1000000


class _TreeBuilder(HTMLParser):
    """A minimal element tree: enough structure to answer the four XPaths."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._stack = [self.root]
        self._open = {}  # tag -> how many of the stack's elements carry it
        self._budget = _TREE_SCAN_BUDGET

    def _close_from(self, i):
        for gone in self._stack[i:]:
            self._open[gone.tag] -= 1
        del self._stack[i:]

    def handle_starttag(self, tag, attrs):
        closes = _START_CLOSE.get(tag, ())
        while len(self._stack) > 1 and self._stack[-1].tag in closes:
            self._close_from(len(self._stack) - 1)
        node_attrs = {}
        for name, value in attrs:
            # first occurrence wins, and a bare attribute is the empty string
            node_attrs.setdefault(name, "" if value is None else value)
        node = _Node(tag, node_attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS and len(self._stack) <= _TREE_MAX_DEPTH:
            self._stack.append(node)
            self._open[tag] = self._open.get(tag, 0) + 1

    def handle_endtag(self, tag):
        # close the nearest open element of this name, unless an element above it
        # outranks the end tag (_END_PRIORITY); a stray end tag is ignored
        if not self._open.get(tag):
            return
        priority = _END_PRIORITY.get(tag, _END_PRIORITY_DEFAULT)
        for i in range(len(self._stack) - 1, 0, -1):
            if self._budget <= 0:
                return
            self._budget -= 1
            if self._stack[i].tag == tag:
                self._close_from(i)
                return
            if _END_PRIORITY.get(self._stack[i].tag, _END_PRIORITY_DEFAULT) > priority:
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def _child_elements(node, tag):
    return [c for c in node.children if not isinstance(c, str) and c.tag == tag]


def _descendant_text(node, out, only_under=None):
    """Append node's descendant text nodes in document order. With only_under,
    only text that sits inside an element of that tag (below node) counts."""
    todo = [(node, only_under is None)]
    while todo:
        current, counting = todo.pop()
        if isinstance(current, str):
            out.append(current)
            continue
        for child in reversed(current.children):
            if isinstance(child, str):
                if counting:
                    todo.append((child, True))
            else:
                todo.append((child, counting or child.tag == only_under))
    return out


def _iter_elements(root):
    """Every element below root, in document order."""
    todo = list(reversed([c for c in root.children if not isinstance(c, str)]))
    while todo:
        node = todo.pop()
        yield node
        todo.extend(reversed([c for c in node.children if not isinstance(c, str)]))


def parse_bing_results(html_text):
    """Parse Bing search results: stdlib html.parser, same fields as the old XPath."""
    results = []
    builder = _TreeBuilder()
    try:
        builder.feed(html_text)
        builder.close()
    except Exception:
        return results

    for e in _iter_elements(builder.root):
        if e.tag != "li" or "b_algo" not in e.attrs.get("class", ""):
            continue
        hrefs = []
        title_parts = []
        for child in e.children:
            if isinstance(child, str):
                continue
            if child.tag == "h2":
                for a in _child_elements(child, "a"):
                    if "href" in a.attrs:
                        hrefs.append(a.attrs["href"])
                    _descendant_text(a, title_parts)
            elif child.tag == "div" and "header" in child.attrs.get("class", ""):
                for a in _child_elements(child, "a"):
                    if "href" in a.attrs:
                        hrefs.append(a.attrs["href"])
                    for h2 in _child_elements(a, "h2"):
                        _descendant_text(h2, title_parts)
        href = hrefs[0] if hrefs else None
        if not href:
            continue

        href = _decode_bing_url(href)
        title = _normalize("".join(title_parts))
        body = _descendant_text(e, [], only_under="p")
        snippet = _normalize("".join(body)).replace("\xa0", " ")

        results.append({'url': href, 'title': title, 'snippet': snippet or 'No snippet available'})

    return results


def warmup_session(session, endpoint="ddg"):
    try:
        if endpoint == "bing":
            session.get("https://www.bing.com/", timeout=10)
        else:
            session.get("https://lite.duckduckgo.com/lite/", timeout=10)
    except Exception:
        pass
    time.sleep(random.uniform(0.8, 1.5))


# The challenge markers of DDG's anomaly (CAPTCHA) page. They are judged by
# _ddg_blocked, never as a bare substring test of the body: the lite page
# reflects the query and third-party snippets.
_DDG_CHALLENGE_MARKERS = ("anomaly-modal", "Please complete the following")


def _ddg_blocked(resp, query, results=None):
    """DDG's bot-block signal (D15, D16 S1/S2): True only for a block.

    All of: (0) the final URL's host is lite.duckduckgo.com (a redirect
    target's answer is never a block); (a) the page parses to ZERO results, so
    a snippet carrying a marker cannot trip it (a page with a snippet has a
    result); and then EITHER (s) the status is 202 (R-0052: the challenge
    status spec-ddg recorded and the user has seen live; lite results answer
    200), with no marker needed, OR (b) a challenge marker occurs in the page
    AND the query does not contain that marker (case-insensitive), so the
    reflected query cannot trip it. An undecodable body is not judged here:
    zero results on it would mean nothing, so neither (s) nor (b) applies.

    `results` is parse_lite_results(resp.text) when the caller already has it
    (search_ddg parses each response ONCE, F32); None parses here.
    """
    # UNVERIFIED: (b) is the plan's FALLBACK predicate. task-038
    # (.claude/tmp/task038-live.txt) saw DDG answer both transports with 200 and
    # lite results; no anomaly page was recorded, so the structural marker
    # element (an element whose class/id is anomaly-modal, outside the results
    # container) is still unmeasured. Replace (b) with that structural match
    # once a live challenge page has been recorded. The 202 of (s) is the one
    # measured signal (spec-ddg; R-0052).
    if urllib.parse.urlsplit(resp.url or "").hostname != "lite.duckduckgo.com":
        return False
    if resp.decode_error is not None:
        return False
    page = resp.text
    if results is None:
        results = parse_lite_results(page)
    if results:
        return False
    if resp.status_code == 202:
        return True
    lowered_query = (query or "").lower()
    for marker in _DDG_CHALLENGE_MARKERS:
        if marker in page and marker.lower() not in lowered_query:
            return True
    return False


def search_ddg(query, session, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Lite results for *query*; [] on no results or an error; None on a block (_ddg_blocked).

    `note(event, query, detail)` receives `ddg_undecodable` (detail: the decode
    error) and `ddg_error` (detail: the exception); `on_transport_error(exc)` is
    called in the except arm before the [] is returned.
    """
    # The fetch is modelled as coming from the page the warm-up loaded (L8);
    # the warm-up URL itself stands in only when the warm-up failed. The cors
    # POST profile supplies Sec-Fetch-*, the form Content-Type and (h2 only)
    # Priority;
    # no Origin goes out, because the referer is same-origin with the target
    # (Chrome's captured same-origin fetches carry none) -- the client adds one
    # only for a cross-origin initiator, i.e. after a redirect off this origin.
    page = session.last_navigation_url or "https://lite.duckduckgo.com/lite/"
    try:
        resp = session.post("https://lite.duckduckgo.com/lite/", data={"q": query, "kl": ""}, headers={"Accept": "*/*"}, referer=page, timeout=15)
        if resp.decode_error is not None:
            # Never None: an undecodable body is not taken for a CAPTCHA.
            note("ddg_undecodable", query, resp.decode_error)
            return []
        # Parsed ONCE (F32): the block signal reuses these results.
        results = parse_lite_results(resp.text)
        if _ddg_blocked(resp, query, results):
            return None
        return results
    except Exception as e:
        # A transport failure is never a block (D16 M2): a note and [], so
        # breaking the handshake cannot force a block verdict, and the run
        # does not switch to Bing either.
        note("ddg_error", query, e)
        on_transport_error(e)
        return []


def _bing_blocked(resp):
    """Bing's bot-block signal (D15, D16 M3/S2): a 403 from www.bing.com itself.

    429 (a volume verdict), 5xx (a server fault) and every other status are
    never a block, nor is any answer from another host. DEFERRED, declared
    (R25): a challenge served as a 200 that parses to [] is not detected.
    """
    # UNVERIFIED: Bing's block shape against a non-browser TLS fingerprint was
    # never observed; task-038 saw the verified transport answered 200 with results.
    return resp.status_code == 403 and urllib.parse.urlsplit(resp.url or "").hostname == "www.bing.com"


def search_bing(query, session, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Bing results for *query*; [] on no results or an error; None on a block (_bing_blocked).

    `note(event, query, detail)` receives `bing_http` (detail: the status code
    of a non-200 answer that is NOT a block -- a block is reported by its None
    alone, so the CLI prints one line for it, FR-9), `bing_undecodable`
    (detail: the decode error) and `bing_error` (detail: the exception);
    `on_transport_error(exc)` is called in the except arm before the [] is
    returned.
    """
    try:
        resp = session.get("https://www.bing.com/search", params={"q": query}, timeout=15)
        if resp.status_code != 200:
            if _bing_blocked(resp):
                return None
            note("bing_http", query, resp.status_code)
            return []
        if resp.decode_error is not None:
            note("bing_undecodable", query, resp.decode_error)
            return []
        return parse_bing_results(resp.text)
    except Exception as e:
        # A transport failure is never a block (D16 M2).
        note("bing_error", query, e)
        on_transport_error(e)
        return []


def run_web(queries, with_session, note_for, out=None):
    """Search every query, DDG first; a DDG block moves this and every later
    query to Bing. Returns [(query, results_or_None, transport_failed), ...].

    The outcomes are appended to *out* (a fresh list when None) as each query
    finishes, so a host whose hook raises mid-run -- a busy endpoint, a call
    deadline -- still holds every outcome gathered before the raise.

    `with_session(endpoint, fn)` is the host's hook: `endpoint` is "ddg" or
    "bing", and the host calls `fn(session, bad)` with a ready session and its
    own transport-failure callable, returning what `fn` returns. Each query is
    one `with_session` call per endpoint it touches, so a host that locks per
    endpoint never holds the DDG lock while taking the Bing one. `fn` wraps
    `bad` so the failure is recorded here as well before the host sees it --
    without the wrap only the host would know, and `transport_failed` could not
    be computed. `note_for(i)` returns the note callable for query index i; at
    the DDG -> Bing switch it receives `ddg_captcha` (detail None). A result of
    None means Bing blocked that query too.
    """
    def call(search, query, note, failed):
        def fn(session, bad):
            def on_err(exc):
                failed.append(exc)
                bad(exc)
            return search(query, session, note, on_err)
        return fn

    out = [] if out is None else out
    using_bing = False
    for i, query in enumerate(queries):
        note = note_for(i)
        failed = []
        results = None
        if not using_bing:
            results = with_session("ddg", call(search_ddg, query, note, failed))
            if results is None:
                # CAPTCHA -- switch to Bing for this and all remaining queries
                note("ddg_captcha", query, None)
                using_bing = True
                failed = []
        if using_bing:
            results = with_session("bing", call(search_bing, query, note, failed))
        out.append((query, results, bool(failed)))
    return out


def format_web_results(results, query=None):
    """The results as markdown. Every third-party field is rendered through
    _web_line (one line, no invisible or display-steering code point: F2, the
    Unicode classes) and the URL through _web_url ("No URL" when it is not a
    safe http(s) link: F3); the query echo goes through _web_line as well."""
    output = []
    if query:
        output.append(f"## Query: {_web_line(query)}")
        output.append("")
    for i, result in enumerate(results, 1):
        title = _web_line(result.get('title', 'No title'))
        url = _web_url(result.get('url')) or 'No URL'
        snippet = _web_line(result.get('snippet', 'No snippet available'))
        output.append(f"### Result {i}: {title}")
        output.append(f"**URL**: {url}")
        output.append(f"**Snippet**: {snippet}")
        output.append("")
    return '\n'.join(output)
