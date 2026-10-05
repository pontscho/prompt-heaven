#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Canonical source for code search: grep.app's search API over public GitHub.

The domain is ONE question: how a code query becomes code results -- the request
grep.app is sent, the JSON it answers with and the HTML snippet inside it, the
signal that says an answer is a bot block rather than a rate limit or an empty
result, and the markdown a result is rendered as. It is a source of its own and
not a corner of `_mcp_websearch.py` because it is a different endpoint with a
different page, a different block signal and a different result shape; the rule
that decided it is ADR 0014's.

**Hosts.** `Scripts/search_github.py` (the CLI) and `Scripts/mcp-search.py` (the
MCP server) take this source WHOLE: one marker naming every block, in source
order, because the blocks call one another and `host_provides` offers a region
only the host's imports, never a name another region defines.

**The session is injected, never created here.** `search_github` and
`warmup_code_session` are handed a session object (the generated `_ChSession`);
creating one needs `_ch_session_new`, `_DECODERS` and `_ch_public_only_policy`,
which are names of the HOST and of another source, so the host owns
`create_session`, pacing, rotation and -- in the server -- the endpoint lock.
There is no shared run loop: a host calls `search_github` once per query.

**Result contract.** `search_github` returns a list (the results, possibly
empty -- also on a transport failure) or None (a bot block, judged by
`_grep_app_blocked` on grep.app's own host only). Two optional callables replace
the stderr lines the CLI used to print from inside it:

* `note(event, query, detail)` -- `event` is a fixed token (`grep_undecodable`,
  `grep_rate_limited`, `grep_http`, `grep_error`), `detail` the decode error,
  the status code or the exception object. The CLI renders its historical
  stderr text from it; the server logs structure only.
* `on_transport_error(exc)` -- called in the `except Exception` arm before the
  `[]` is returned, so a caller can tell a transport failure from no results.

Both default to a no-op, so a caller that passes neither sees nothing.

**Fence safety.** A snippet is third-party text; a bare three-backtick fence
around it is closed by the first "```" inside it. `_code_fence` picks a fence one
backtick longer than the longest backtick run in the snippet, minimum three.

**Tab safety is a constraint on how this file is WRITTEN.** One host indents
with tabs, and `Scripts/amalgamate.py:block_is_tab_safe` refuses a block that
joins a line inside an open bracket or continues one with a backslash. So every
call and literal here fits one physical line, and the extension table is a
builder function plus one single-target assignment (`_ext_to_lang` /
`EXT_TO_LANG`).

**Block contract.** A block reads only builtins, the stdlib names this module
imports (`json`, `os`, `random`, `time`, `urllib.parse`, `HTMLParser`,
`urlencode`), its own arguments and the blocks co-listed on the same marker.
Never `_ch_session_new`, `_ch_public_only_policy`, `_DECODERS`,
`ChromeClientError` (its base `ConnectionError` is caught instead), `log`,
`ROTATE_EVERY` or `SEARCH_MAX_BYTES` -- those stay hand-written in each host.

**No host imports this module**, for the reasons `_mcp_json.py` gives. The test
fleet does: `tests/test_search_parsers.py` and `tests/test_mcp_chrome.py` drive
the generated copies in the hosts.
"""

import json
import os
import random
import time
import unicodedata
import urllib.parse
from html.parser import HTMLParser
from urllib.parse import urlencode


# Rendering third-party text (repo, path, branch, the code itself) into a reply
# the model reads: every invisible or display-steering code point is DROPPED --
# Cc (C0 except `keep`, DEL, C1), Cf (bidi controls, zero-width, tags, soft
# hyphen), Cs, the variation selectors U+FE00-FE0F and U+E0100-E01EF and the
# rest of the U+E0000 block -- and U+2028 U+2029 U+0085 become "\n". This is
# `_mcp_websearch.py`'s `_web_clean` under this domain's name: a region
# resolves names only against its own source (ADR 0014), so each search domain
# carries one copy, and tests/test_mcp_search.py asserts the two agree.
_CODE_LINE_SEPARATORS = "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\x85"


def _code_clean(text, keep):
    """*text* with every invisible or display-steering code point dropped.

    A character in *keep* survives; U+2028 U+2029 U+0085 become a newline.
    Printable ASCII is the fast path; the rest is judged by category.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if 0x20 <= code < 0x7f or ch in keep:
            out.append(ch)
        elif ch in _CODE_LINE_SEPARATORS:
            out.append("\n")
        elif 0xfe00 <= code <= 0xfe0f or 0xe0000 <= code <= 0xe01ef:
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def _code_line(text):
    """A single-line field: _code_clean, then every whitespace run one space."""
    if text is None:
        return ""
    return " ".join(_code_clean(str(text), "\t\n\r").split())


def _code_field(text):
    """A header field (repo, path, branch): one line and no backtick (F4)."""
    return _code_line(str(text).replace("`", ""))


def _ext_to_lang():
    """File extension -> language name, one row per line (a builder: tab-safe)."""
    t = {}
    t['.py'] = 'Python'
    t['.js'] = 'JavaScript'
    t['.ts'] = 'TypeScript'
    t['.tsx'] = 'TypeScript'
    t['.jsx'] = 'JavaScript'
    t['.java'] = 'Java'
    t['.cpp'] = 'C++'
    t['.cc'] = 'C++'
    t['.c'] = 'C'
    t['.h'] = 'C/C++'
    t['.hpp'] = 'C++'
    t['.cs'] = 'C#'
    t['.go'] = 'Go'
    t['.rs'] = 'Rust'
    t['.rb'] = 'Ruby'
    t['.php'] = 'PHP'
    t['.swift'] = 'Swift'
    t['.kt'] = 'Kotlin'
    t['.scala'] = 'Scala'
    t['.sh'] = 'Shell'
    t['.bash'] = 'Bash'
    t['.html'] = 'HTML'
    t['.css'] = 'CSS'
    t['.scss'] = 'SCSS'
    t['.json'] = 'JSON'
    t['.xml'] = 'XML'
    t['.yaml'] = 'YAML'
    t['.yml'] = 'YAML'
    t['.md'] = 'Markdown'
    t['.sql'] = 'SQL'
    t['.r'] = 'R'
    t['.m'] = 'Objective-C'
    t['.vim'] = 'Vim Script'
    t['.lua'] = 'Lua'
    t['.pl'] = 'Perl'
    return t


EXT_TO_LANG = _ext_to_lang()


def detect_language(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    return EXT_TO_LANG.get(ext)


# The snippet used to be scanned with re.finditer(r'<tr data-line="(\d+)">.*?'
# r'<pre>(.*?)</pre>', DOTALL), which rescans to the end of the input for every
# unterminated opener -- quadratic or worse on a hostile body (F32, R-0057).
# This is one html.parser pass with the same rules, keyed on the raw start tags
# and never on an implied end tag (html.parser has none): a line starts at an
# exact `<tr data-line="N">`, takes the first exact `<pre>` after it and ends at
# the first </pre>; any `<tr data-line>` before that `<pre>` is skipped, the
# text has its tags dropped and entities decoded, and an empty line is skipped.
class _SnippetParser(HTMLParser):

    _TR_OPEN = '<tr data-line="'

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self._line_no = None
        self._parts = None  # the open <pre>'s text, None outside one

    def handle_starttag(self, tag, attrs):
        if self._parts is not None:
            return
        raw = self.get_starttag_text() or ""
        if tag == "tr" and self._line_no is None:
            digits = raw[len(self._TR_OPEN):-2]
            # at most 9 digits (F13): int() of a huge decimal string is
            # quadratic on CPython before 3.9.14, and no file has 10^9 lines
            if raw.startswith(self._TR_OPEN) and raw.endswith('">') and digits and len(digits) <= 9 and digits.isdecimal():
                self._line_no = int(digits)
        elif tag == "pre" and self._line_no is not None and raw == "<pre>":
            self._parts = []

    def handle_endtag(self, tag):
        if tag == "pre" and self._parts is not None:
            code_text = "".join(self._parts).strip()
            if code_text:
                self.lines.append((self._line_no, code_text))
            self._line_no = None
            self._parts = None

    def handle_data(self, data):
        if self._parts is not None:
            self._parts.append(data)


def extract_code_from_snippet(html_snippet):
    parser = _SnippetParser()
    try:
        parser.feed(html_snippet)
        parser.close()
    except Exception:
        pass
    return parser.lines


def build_github_url(repo, path, branch, line_number=None):
    """The blob URL; repo, branch and path are percent-encoded with "/" kept (F4),
    so a path carrying a space, "#", "?", a newline or non-ASCII stays one URL."""
    base = "https://github.com/%s/blob/%s/%s" % (urllib.parse.quote(repo, safe="/"), urllib.parse.quote(branch, safe="/"), urllib.parse.quote(path, safe="/"))
    if line_number:
        return f"{base}#L{line_number}"
    return base


def _grep_hits(data):
    """data["hits"]["hits"] when the answer has grep.app's shape, else None (F8).

    A shape mismatch is a parse failure, not a transport error: search_github
    notes it as `grep_schema` and keeps the session."""
    hits = data.get('hits', {}) if isinstance(data, dict) else None
    hits = hits.get('hits', []) if isinstance(hits, dict) else None
    return hits if isinstance(hits, list) else None


def _grep_str(hit, key, default):
    value = hit.get(key, default)
    return value if isinstance(value, str) else default


def parse_grep_results(data, limit):
    """The results of a grep.app answer. Every level is type-checked (F8): a hit
    that is not an object is skipped, a repo / path / branch that is not a string
    takes its default, and a content or snippet of the wrong type is no snippet."""
    results = []
    for hit in (_grep_hits(data) or [])[:limit]:
        if not isinstance(hit, dict):
            continue
        file_path = _grep_str(hit, 'path', 'Unknown')
        result = {'repo': _grep_str(hit, 'repo', 'Unknown'), 'file_path': file_path, 'branch': _grep_str(hit, 'branch', 'main'), 'language': detect_language(file_path) or 'Unknown', 'code_lines': []}

        content = hit.get('content', {})
        snippet_html = _grep_str(content, 'snippet', '') if isinstance(content, dict) else ''
        if snippet_html:
            result['code_lines'] = extract_code_from_snippet(snippet_html)

        first_line = result['code_lines'][0][0] if result['code_lines'] else None
        result['url'] = build_github_url(result['repo'], result['file_path'], result['branch'], first_line)
        results.append(result)

    return results


def warmup_code_session(session):
    try:
        session.get("https://grep.app/", timeout=10)
    except Exception:
        pass
    time.sleep(random.uniform(0.8, 1.5))


def _body_is_json(resp):
    """True when the (decoded) body parses as JSON; False otherwise, never raises.

    ConnectionError is the base of the Chrome client's ChromeClientError, which
    reading `resp.text` may raise; the base is caught because the subclass is a
    name of another canonical source.
    """
    try:
        json.loads(resp.text)
    except (ValueError, ConnectionError):
        return False
    return True


def _grep_app_blocked(resp):
    """grep.app's bot-block signal (D15, D16 S2/M3, D17): True only for a block.

    Judged on grep.app's own host only (a redirect target's answer is never a
    block), then: 403; a 429 whose body is a non-JSON text/html page; a 200
    whose body is not JSON. A 429 with a JSON body is a rate limit and 5xx a
    server fault -- never a block. An undecodable body is not judged here.
    """
    # task-038 measured (.claude/tmp/task038-live.txt): the verified transport got 429 text/html non-JSON on both / and /api/search while the Chrome path got 200 JSON seconds apart -- a fingerprint block, not a rate limit.
    if urllib.parse.urlsplit(resp.url or "").hostname != "grep.app":
        return False
    if resp.decode_error is not None:
        return False
    if resp.status_code == 403:
        return True
    if resp.status_code == 429:
        content_type = (resp.headers.get("content-type") or "").lower()
        return "text/html" in content_type and not _body_is_json(resp)
    if resp.status_code == 200:
        return not _body_is_json(resp)
    return False


def search_github(query, session, lang=None, repo=None, path=None, limit=10, note=lambda event, query, detail: None, on_transport_error=lambda exc: None):
    """Results for *query*; [] on no results or an error; None on a block (_grep_app_blocked).

    `note(event, query, detail)` receives `grep_undecodable` (detail: the
    decode error), `grep_rate_limited` (detail: 429), `grep_http` (detail: the
    status code), `grep_schema` (detail None: a JSON answer not in grep.app's
    shape) and `grep_error` (detail: the exception);
    `on_transport_error(exc)` is called in the except arm before the [] is
    returned.
    """
    params = {'q': query}
    if lang:
        params['f.lang'] = lang
    if repo:
        params['f.repo'] = repo
    if path:
        params['f.path'] = path

    url = f"https://grep.app/api/search?{urlencode(params)}"
    # The fetch is modelled as coming from the page the warm-up loaded; the
    # warm-up URL itself stands in only when the warm-up failed (R2-L4).
    page = session.last_navigation_url or "https://grep.app/"

    try:
        resp = session.get(url, headers={"Accept": "application/json, text/plain, */*"}, mode="cors", referer=page, timeout=15)
        if resp.decode_error is not None:
            note("grep_undecodable", query, resp.decode_error)
            return []
        if _grep_app_blocked(resp):
            return None
        if resp.status_code == 429:
            note("grep_rate_limited", query, resp.status_code)
            return []
        if resp.status_code != 200:
            note("grep_http", query, resp.status_code)
            return []
        data = json.loads(resp.text)
        if _grep_hits(data) is None:
            # Not grep.app's shape (F8): a parse failure, not a transport
            # error -- noted, [] returned, and the session kept.
            note("grep_schema", query, None)
            return []
        return parse_grep_results(data, limit)
    except Exception as e:
        # A transport failure is never a block (D16 M2): a note and [].
        note("grep_error", query, e)
        on_transport_error(e)
        return []


def _code_fence(lines):
    """A backtick fence no line of *lines* can close: one backtick longer than
    the longest backtick run in them, and never shorter than three."""
    longest = 0
    for line in lines:
        run = 0
        for ch in line:
            if ch == "`":
                run += 1
                if run > longest:
                    longest = run
            else:
                run = 0
    return "`" * max(3, longest + 1)


def format_code_results(results, query=None):
    """The results as markdown. The header fields go through _code_field (one
    line, no backtick, no invisible or display-steering code point: F4 and the
    Unicode classes), the URL and the query echo through _code_line, and the
    code through _code_clean keeping tab and newline, before the fence is
    measured over exactly the text it encloses."""
    if not results:
        return "No results found."

    output = []
    if query:
        output.append(f"## Query: {_code_line(query)}")
        output.append("")

    for i, result in enumerate(results, 1):
        output.append(f"### Result {i}: {_code_field(result['repo'])} - {_code_field(result['file_path'])}")
        output.append(f"**URL**: {_code_line(result['url'])}")
        output.append(f"**Branch**: {_code_field(result['branch'])}")
        output.append(f"**Language**: {result['language']}")

        if result['code_lines']:
            first_line = result['code_lines'][0][0]
            last_line = result['code_lines'][-1][0]
            output.append(f"**Line {first_line}-{last_line}:**")
            codes = [_code_clean(code, "\t\n") for _num, code in result['code_lines']]
            fence = _code_fence(codes)
            output.append(fence)
            for code in codes:
                output.append(code)
            output.append(fence)
        else:
            output.append("No code snippet available")

        output.append("")

    return '\n'.join(output)
