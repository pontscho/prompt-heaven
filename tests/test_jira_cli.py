#!/usr/bin/env python3
"""Offline suite for the Jira CLI (ClaudeCode/skills/jira/scripts/jira.py).

NOTHING HERE TOUCHES THE NETWORK, and that is structural rather than hoped for:
the module under test takes its transport as an INJECTED CALLABLE, so every
request in this suite is answered from a scripted queue that also records the
exact method, URL, headers and decoded body the CLI built.  The two paths that
do not take an injected transport -- `main()` and everything it constructs --
are exercised with the module's own `urllib_fetch` replaced by a guard that
COUNTS calls and refuses to make one.  urllib itself is never monkeypatched;
nothing global is touched.

WHAT IS WORTH GATING IN A CLIENT NOBODY CAN REACH FROM CI
---------------------------------------------------------
Only the pure decisions -- but those are where the bugs live, and each of the
groups below reproduces a real, named failure mode rather than a hypothetical:

  A  auth + URL joining.  `urljoin("https://host/jira", "/rest/api/2/myself")`
     silently drops the `/jira` context path, and the 404 that follows reads as
     a permissions problem.  The join is therefore a hand-written one-trailing-
     slash concatenation, and the group pins it against urljoin explicitly.
     Basic vs Bearer is decided by the PRESENCE of an email and nothing else.
  B  deployment detection.  The probe is lazy, cached in memory for the process,
     and falls back to a hostname heuristic when serverInfo cannot answer.  Both
     directions are pinned: the probe overriding the heuristic AND the heuristic
     catching an unreachable probe.
  C  the two paging models behind ONE iterator.  Cloud pages by an opaque
     `nextPageToken` and has no total; DC pages by `startAt`/`total` and
     documents that a page may legitimately come back EMPTY and that `total` may
     move between pages.  Both stop conditions are gated for DC, both for Cloud,
     and `--limit` is checked to cap ACROSS pages (a limit honoured per page is
     the classic version of this bug).
  D  config resolution: flag > env > unset, the source string each value
     carries, and the fail-fast that must name both the variable and its flag.
  E  the two write guards: JIRA_READ_ONLY refusing every write subcommand, and
     --dry-run printing what WOULD be sent while sending nothing.  Both are
     asserted with a call counter, not by reading the code.
  F  error mapping, which is the whole difference between usable and
     infuriating: an anonymous 200, a 404 that means two different things, an
     HTML login page where JSON was promised, a CAPTCHA lockout, and a retry
     ladder that must fire on 429/5xx and NEVER on another 4xx.
  G  issue-key validation -- a local, unambiguous error instead of a round trip
     that comes back as an ambiguous 404.
  J  the attachment upload, which is the ONE request in this client whose body
     is not JSON.  Four failures live here and none of them announces itself:
     an LF-only multipart body that some proxies drop, a boundary that also
     occurs inside the file (the server stops reading there and calls the
     truncated upload a success), a missing X-Atlassian-Token: no-check (Jira's
     CSRF gate refuses every multipart request without it and never says so),
     and a `--name` carrying a path.  The byte layout is therefore pinned
     byte-for-byte against a hand-written expectation, and the outgoing headers
     are read off a transport that records the RAW body.
  K  Markdown rendering.  The output is a DOCUMENT now, and a document has
     failure modes aligned text did not have.  A summary containing `|` opens a
     column its header does not have, and every row after it renders wrong --
     silently, because the result is still valid Markdown, just not the table
     that was meant.  A header row with no body rows is not a table in GFM at
     all.  And a field rendered twice is a field that can disagree with itself,
     which is why `get` is asserted with a COUNT of the key and the summary
     rather than a presence check.  The group also pins the two field lists
     apart in the direction that put `components` back into a rendered issue.

NEGATIVE CONTROL (group H) -- mandatory, explicit, named
--------------------------------------------------------
An oracle that cannot fail proves nothing about the code it blesses.  Group H
feeds the SAME oracle functions groups A/C/G use a set of deliberately BROKEN
implementations and results -- an auth helper that ignores the email, a join
built on urljoin, a pager that over-fetches, a pager that ignores an empty page,
a key validator that accepts anything -- and FAILS if any of them is accepted.
The mirror assertion (the real implementations pass those same oracles) is
recorded alongside, so a control that silently stopped running is visible.
Groups J and K carry their own controls for the same reason, against the two
oracles that are local to them: the multipart encoder and the cell escaper.

Fixtures, such as they are, live in a `tempfile.mkdtemp()` workspace; group I
asserts the repo tree is untouched and that ZERO bytecode was written.

Groups:
  A  auth header + URL joining
  B  deployment detection: probe, cache, heuristic fallback
  C  search paging: Cloud token model, DC offset model, --limit
  D  configuration resolution and the fail-fast
  E  JIRA_READ_ONLY and --dry-run
  F  error mapping
  G  issue-key validation
  H  negative control
  I  hygiene
  J  attachment upload: the one non-JSON request body
  K  Markdown rendering: cell escaping, empty tables, and no duplicated fields

Usage:
  python3 tests/test_jira_cli.py
  python3 tests/test_jira_cli.py --brief
Exit code 0 iff every non-informational case passes.
"""

import base64
import contextlib
import inspect
import io
import json
import os
import sys
import urllib.parse

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "jira_cli"
TARGET = H.repo_path("ClaudeCode", "skills", "jira", "scripts", "jira.py")

GA = "A. auth + URL join"
GB = "B. deployment detection"
GC = "C. search paging"
GD = "D. config resolution"
GE = "E. read-only + dry-run"
GF = "F. error mapping"
GG = "G. issue key validation"
GH = "H. negative control"
GI = "I. hygiene"
GJ = "J. attachment upload"
GK = "K. markdown rendering"

# Every environment variable the CLI reads, in both spellings.  Cleared before
# each config case: a developer machine with a real JIRA_URL exported would
# otherwise turn these into a different test.
ENV_KEYS = ("JIRA_URL", "JIRA_TOKEN", "JIRA_EMAIL", "JIRA_READ_ONLY",
            "jira_url", "jira_token", "jira_email", "jira_read_only")

BASE_DC = "https://jira.corp.local/jira"
BASE_CLOUD = "https://acme.atlassian.net"
TOKEN = "s3cr3t-token-value"
EMAIL = "someone@example.com"


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

class Call:
    """One recorded request, with the body already decoded."""

    def __init__(self, method, url, body, headers):
        self.method = method
        self.url = url
        self.body = body
        self.headers = headers

    @property
    def path(self):
        return urllib.parse.urlsplit(self.url).path

    def __repr__(self):
        return "<%s %s body=%r>" % (self.method, self.url, self.body)


class FakeTransport:
    """A scripted stand-in for the module's `fetch` callable.

    Answers from a queue and records what it was asked.  An unscripted request
    raises rather than returning something plausible: a client that makes one
    extra call is exactly the defect groups C and E are looking for.
    """

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, body, headers):
        decoded = json.loads(body.decode("utf-8")) if body else None
        self.calls.append(Call(method, url, decoded, dict(headers)))
        if not self.script:
            raise AssertionError("unscripted request: %s %s" % (method, url))
        item = self.script.pop(0)
        return item(method, url) if callable(item) else item


class NetworkGuard:
    """Replaces the module's real transport for `main()`-level cases.

    It never returns a response; it counts and raises.  So "sent nothing" is a
    measurement (`guard.calls == 0`), not a claim about the source.
    """

    def __init__(self, module, responder=None):
        self.module = module
        self.responder = responder
        self.calls = 0
        self._saved = None

    def __enter__(self):
        self._saved = self.module.urllib_fetch
        self.module.urllib_fetch = self._fetch
        return self

    def __exit__(self, exc_type, exc, tb):
        self.module.urllib_fetch = self._saved
        return False

    def _fetch(self, method, url, body, headers, timeout=None):
        self.calls += 1
        if self.responder is None:
            raise AssertionError("the network was used: %s %s" % (method, url))
        return self.responder(method, url, body, headers)


class EnvSandbox:
    """Clear every JIRA_* spelling, apply `values`, restore on exit."""

    def __init__(self, **values):
        self.values = values
        self._saved = {}

    def __enter__(self):
        for key in ENV_KEYS:
            self._saved[key] = os.environ.get(key)
            os.environ.pop(key, None)
        for key, value in self.values.items():
            if value is not None:
                os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


@contextlib.contextmanager
def captured():
    """(stdout, stderr) as StringIO. Never wraps a suite.record() call."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def response(mod, status=200, payload=None, headers=None, body=None,
             ctype="application/json"):
    """Build an HttpResponse the way a server would."""
    head = {}
    if ctype:
        head["Content-Type"] = ctype
    head.update(headers or {})
    if body is None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return mod.HttpResponse(status, head, body)


def make_cfg(mod, url=BASE_DC, token=TOKEN, email=None, read_only=False,
             read_only_raw=None):
    return mod.Config(url, token, email, read_only, read_only_raw, {
        "url": "--url", "token": "--token", "email": "<unset>",
        "read_only": "<unset>"})


def client_with(mod, script, url=BASE_DC, email=None, deployment=None):
    """A Jira client on a scripted transport, with the probe pre-answered.

    `deployment` writes the module-level cache directly instead of scripting a
    serverInfo round trip, so a paging case measures paging and nothing else.
    Group B is where the probe itself is the subject.
    """
    mod.reset_deployment_cache()
    if deployment is not None:
        mod._DEPLOYMENT_CACHE = deployment
    transport = FakeTransport(script)
    return mod.Jira(make_cfg(mod, url=url, email=email), fetch=transport,
                    sleep=lambda _seconds: None), transport


def issues(prefix, start, count):
    return [{"key": "%s-%d" % (prefix, start + i),
             "fields": {"summary": "issue %d" % (start + i)}}
            for i in range(count)]


def keys_of(rows):
    return [row.get("key") for row in rows]


def parse_args(mod, argv):
    return mod.build_parser().parse_args(argv)


def connected(argv, url=BASE_DC, token=TOKEN):
    """`argv` plus the connection flags.

    They go AFTER the subcommand because they live on the subparsers (the
    `parents=` pattern): repeating them on the top-level parser too would give
    two options the same dest, and the subparser's None would then overwrite
    whatever the top level parsed.
    """
    return list(argv) + ["--url", url, "--token", token]


def problem_if(condition, message):
    return [message] if condition else []


def missing_tokens(text, tokens):
    return [t for t in tokens if t not in text]


def fenced_block(text, language=""):
    """The contents of the first ```<language> fence in `text`, or ""."""
    opener = "```" + language + "\n"
    if opener not in text:
        return ""
    body = text.split(opener, 1)[1]
    return body.split("```", 1)[0] if "```" in body else body


def unescaped_pipes(line):
    """`|` characters that still act as a COLUMN SEPARATOR in a table row.

    An escaped `\\|` is counted by str.count("|") too, so it is subtracted back
    out -- which is the whole difference between "this cell contains a pipe"
    and "this row has an extra column".
    """
    return line.count("|") - line.count("\\|")


def table_rows(text):
    """Every line of `text` that looks like a Markdown table row."""
    return [line for line in text.splitlines() if line.strip().startswith("|")]


def has_heading(text, heading):
    """True iff `heading` appears as a whole line (not as a substring)."""
    return heading in [line.strip() for line in text.splitlines()]


# ---------------------------------------------------------------------------
# the ORACLES -- shared by the live groups and by the negative control
# ---------------------------------------------------------------------------

JOIN_CASES = [
    # (base, path, expected)
    ("https://jira.corp.local", "/rest/api/2/myself",
     "https://jira.corp.local/rest/api/2/myself"),
    # the context path a DC base URL usually carries -- urljoin eats this one
    ("https://jira.corp.local/jira", "/rest/api/2/myself",
     "https://jira.corp.local/jira/rest/api/2/myself"),
    ("https://jira.corp.local/", "/rest/api/2/myself",
     "https://jira.corp.local/rest/api/2/myself"),
    ("https://jira.corp.local/jira/", "/rest/api/2/issue/PROJ-1",
     "https://jira.corp.local/jira/rest/api/2/issue/PROJ-1"),
    ("https://acme.atlassian.net", "/rest/api/2/search/jql",
     "https://acme.atlassian.net/rest/api/2/search/jql"),
    # a path handed over without its leading slash still lands correctly
    ("https://jira.corp.local/jira", "rest/api/2/field",
     "https://jira.corp.local/jira/rest/api/2/field"),
]

KEYS_OK = ["STR-1234", "ABC1-99", "PROJ-1234", "A1-1", "OPS-7", "X9Y-42"]
KEYS_BAD = ["str-1234", "STR1234", "STR-", "-1234", "", "STR-12a",
            "STR 1234", "STR--1", "1STR-12"]


def check_auth(fn):
    """Problems for an `auth_header(email, token) -> str` implementation."""
    problems = []
    want_basic = "Basic " + base64.b64encode(
        ("%s:%s" % (EMAIL, TOKEN)).encode()).decode()
    got = fn(EMAIL, TOKEN)
    if got != want_basic:
        problems.append("Cloud: got %r, want %r" % (got, want_basic))
    got = fn(None, TOKEN)
    if got != "Bearer " + TOKEN:
        problems.append("DC: got %r, want %r" % (got, "Bearer " + TOKEN))
    got = fn("", TOKEN)
    if got != "Bearer " + TOKEN:
        problems.append("an empty email must still select Bearer, got %r" % got)
    return problems


def check_join(fn):
    """Problems for an `api_url(base, path)` implementation."""
    problems = []
    for base, path, want in JOIN_CASES:
        got = fn(base, path)
        if got != want:
            problems.append("%s + %s -> %s, want %s" % (base, path, got, want))
    return problems


def check_key_validator(fn):
    """Problems for a `_validate_issue_key(key)` that raises on a bad key."""
    problems = []
    for key in KEYS_OK:
        try:
            fn(key)
        except Exception as exc:
            problems.append("%r must be accepted (raised %s)" % (key, exc))
    for key in KEYS_BAD:
        try:
            fn(key)
        except Exception:
            continue
        problems.append("%r must be rejected, was accepted" % key)
    return problems


def check_pages(got, calls, want_keys, want_requests):
    """Problems for one paging run: the issue keys AND the request count.

    The request count is half the assertion on purpose -- a pager that returns
    the right issues after one page too many is still broken, and on Cloud that
    extra page is a real HTTP round trip against a rate-limited API.
    """
    problems = []
    if list(got) != list(want_keys):
        problems.append("yielded %r, want %r" % (list(got), list(want_keys)))
    if len(calls) != want_requests:
        problems.append("made %d request(s), want %d" % (len(calls),
                                                         want_requests))
    return problems


# The real shape of the defect: a Jira summary is free text and pipes turn up
# in it constantly ("parse|render", "A|B testing", "500|502 on deploy").
PIPED = "parse|render crashes on boot"


def check_escaper(fn):
    """Problems for an `md_escape(text) -> str` implementation.

    Shared by the live row and by the control below, so the row that blesses
    the real escaper is passing an oracle that has been SHOWN to reject one
    which lets a pipe through.
    """
    problems = []
    got = fn(PIPED)
    if "\\|" not in got:
        problems.append("the pipe was not escaped as \\|: %r" % got)
    if unescaped_pipes(got) != 0:
        problems.append("%d separator pipe(s) survived in a cell value: %r"
                        % (unescaped_pipes(got), got))
    got = fn("two\nlines")
    if "\n" in got:
        problems.append("a newline survived, which ends the table row: %r"
                        % got)
    if fn("plain value") != "plain value":
        problems.append("a value with nothing to escape was rewritten: %r"
                        % fn("plain value"))
    return problems


# ---------------------------------------------------------------------------
# A. auth + URL join
# ---------------------------------------------------------------------------

def group_a(suite, mod):
    suite.record(GA, "auth-header-both-modes", check_auth(mod.auth_header),
                 detail=["Basic when an email is present, Bearer when it is "
                         "not -- the email is the SWITCH, not a second secret"])

    got = mod.auth_header(EMAIL, TOKEN)
    problems = []
    if not got.startswith("Basic "):
        problems.append("not a Basic header: %r" % got)
    else:
        decoded = base64.b64decode(got.split(" ", 1)[1]).decode()
        if decoded != "%s:%s" % (EMAIL, TOKEN):
            problems.append("decodes to %r, want %r"
                            % (decoded, "%s:%s" % (EMAIL, TOKEN)))
    suite.record(GA, "auth-basic-decodes-to-email-colon-token", problems,
                 detail=["independently base64-decoded, so a helper that "
                         "merely round-trips its own encoder cannot pass"])

    got = mod.auth_header(None, TOKEN)
    suite.record(GA, "auth-bearer-is-the-literal-token",
                 problem_if(got != "Bearer " + TOKEN,
                            "got %r" % got),
                 detail=["no base64, no email, no colon: a DC personal access "
                         "token is sent verbatim"])

    cloud = make_cfg(mod, url=BASE_CLOUD, email=EMAIL)
    dc = make_cfg(mod, url=BASE_DC)
    problems = []
    if "Basic" not in cloud.auth_mode or "Bearer" in cloud.auth_mode:
        problems.append("cloud label: %r" % cloud.auth_mode)
    if "Bearer" not in dc.auth_mode or "Basic" in dc.auth_mode:
        problems.append("dc label: %r" % dc.auth_mode)
    suite.record(GA, "auth-mode-label-follows-the-email", problems,
                 detail=["cloud: %s" % cloud.auth_mode, "dc   : %s" % dc.auth_mode])

    suite.record(GA, "join-table", check_join(mod.api_url),
                 detail=["%s + %s -> %s" % case for case in JOIN_CASES])

    base, path = "https://jira.corp.local/jira", "/rest/api/2/myself"
    ours = mod.api_url(base, path)
    theirs = urllib.parse.urljoin(base, path)
    problems = []
    if "/jira/rest/" not in ours:
        problems.append("the context path was lost: %s" % ours)
    if ours == theirs:
        problems.append("api_url agrees with urljoin here (%s), so this case "
                        "no longer demonstrates anything" % ours)
    suite.record(GA, "join-keeps-what-urljoin-would-eat", problems,
                 detail=["api_url : %s" % ours,
                         "urljoin : %s  <- the /jira is gone" % theirs,
                         "this is the #1 DC misconfiguration: the 404 that "
                         "follows reads as a permissions problem"])

    got = mod.api_url("https://jira.corp.local/jira//", "/rest/api/2/myself")
    suite.record(GA, "join-strips-exactly-one-trailing-slash",
                 problem_if(got != "https://jira.corp.local/jira//rest/api/2/"
                                   "myself", "got %s" % got),
                 detail=["ONE slash, not rstrip('/'): a base whose context "
                         "path genuinely ends in an empty segment keeps it",
                         "got: %s" % got])

    got = mod.api_url(BASE_DC, "/rest/api/2/issue/PROJ-1",
                      {"fields": "summary,status", "expand": "changelog"})
    problems = missing_tokens(got, ["fields=summary%2Cstatus",
                                    "expand=changelog", "?"])
    suite.record(GA, "join-encodes-the-query", problems,
                 detail=["got: %s" % got])

    client, transport = client_with(mod, [response(mod, 200, {"ok": True})])
    client.request("GET", "/rest/api/2/myself")
    headers = transport.calls[0].headers
    problems = []
    if headers.get("Accept") != "application/json":
        problems.append("Accept: %r" % headers.get("Accept"))
    if not (headers.get("User-Agent") or "").strip():
        problems.append("no User-Agent")
    if (headers.get("User-Agent") or "").startswith("Python-urllib"):
        problems.append("the default urllib UA is what on-prem WAFs reject")
    if headers.get("Authorization") != "Bearer " + TOKEN:
        problems.append("Authorization: %r" % headers.get("Authorization"))
    suite.record(GA, "headers-accept-ua-auth", problems,
                 detail=["User-Agent: %s" % headers.get("User-Agent"),
                         "a real UA is not cosmetic: the WAFs in front of "
                         "on-prem Jira reject Python-urllib/3.x outright"])

    client, transport = client_with(mod, [response(mod, 200, {"ok": True}),
                                          response(mod, 200, {"ok": True})])
    client.request("GET", "/rest/api/2/myself")
    client.request("POST", "/rest/api/2/issue/PROJ-1/comment",
                   body={"body": "hi"})
    problems = []
    if "Content-Type" in transport.calls[0].headers:
        problems.append("a GET carried a Content-Type")
    if transport.calls[1].headers.get("Content-Type") != "application/json":
        problems.append("the POST body went out as %r"
                        % transport.calls[1].headers.get("Content-Type"))
    suite.record(GA, "content-type-only-with-a-body", problems)


# ---------------------------------------------------------------------------
# B. deployment detection
# ---------------------------------------------------------------------------

HEURISTIC_CASES = [
    ("https://acme.atlassian.net", "Cloud"),
    ("https://acme.atlassian.net/", "Cloud"),
    ("https://api.atlassian.com", "Cloud"),
    ("https://jira.corp.local", "Server"),
    ("https://jira.corp.local/jira", "Server"),
    # a lookalike that is NOT the Cloud domain
    ("https://atlassian.net.corp.local", "Server"),
]


def group_b(suite, mod):
    for url, want in HEURISTIC_CASES:
        got = mod.deployment_from_url(url)
        suite.record(GB, "heuristic-%s" % urllib.parse.urlsplit(url).hostname
                     + ("-slash" if url.endswith("/") else ""),
                     problem_if(got != want, "%s -> %s, want %s"
                                % (url, got, want)),
                     detail=["hostname ends with .atlassian.net, or equals "
                             "api.atlassian.com -> Cloud; anything else -> "
                             "Server"])

    got = mod.deployment_from_url("https://ACME.ATLASSIAN.NET")
    suite.record(GB, "heuristic-is-case-insensitive",
                 problem_if(got != "Cloud", "got %s" % got))

    # the probe overrides the heuristic, in BOTH directions
    client, transport = client_with(
        mod, [response(mod, 200, {"deploymentType": "Cloud"})], url=BASE_DC)
    got = client._deployment()
    suite.record(GB, "probe-beats-heuristic-cloud-on-a-corp-host",
                 problem_if(got != "Cloud", "got %s" % got),
                 detail=["serverInfo said Cloud for %s" % BASE_DC,
                         "requested: %s" % transport.calls[0].path])

    client, transport = client_with(
        mod, [response(mod, 200, {"deploymentType": "Server"})],
        url=BASE_CLOUD)
    got = client._deployment()
    suite.record(GB, "probe-beats-heuristic-server-on-a-cloud-host",
                 problem_if(got != "Server", "got %s" % got),
                 detail=["the heuristic is the FALLBACK, never the answer "
                         "when serverInfo has spoken"])

    client, transport = client_with(mod, [response(mod, 200, {"baseUrl": "x"})],
                                    url=BASE_CLOUD)
    got = client._deployment()
    suite.record(GB, "probe-without-deploymentType-falls-back",
                 problem_if(got != "Cloud", "got %s" % got),
                 detail=["serverInfo answered but carried no deploymentType"])

    client, transport = client_with(
        mod, [response(mod, 500, {"errorMessages": ["boom"]})] * 3,
        url=BASE_DC)
    got = client._deployment()
    suite.record(GB, "probe-unreachable-falls-back-to-the-heuristic",
                 problem_if(got != "Server", "got %s" % got),
                 detail=["3 x HTTP 500 (the retry ceiling), then the hostname "
                         "decides", "requests: %d" % len(transport.calls)])

    client, transport = client_with(
        mod, [response(mod, 200, {"deploymentType": "Cloud"})], url=BASE_DC)
    first, second, third = (client._deployment(), client._deployment(),
                            client._deployment())
    problems = []
    if len(transport.calls) != 1:
        problems.append("%d serverInfo request(s) for 3 lookups"
                        % len(transport.calls))
    if not first == second == third == "Cloud":
        problems.append("answers differed: %r" % [first, second, third])
    suite.record(GB, "probe-runs-once-per-process", problems,
                 detail=["cached in a module-level variable, never on disk"])

    # and the cache is genuinely in memory: clearing it re-probes.
    mod.reset_deployment_cache()
    transport.script.append(response(mod, 200, {"deploymentType": "Server"}))
    got = client._deployment()
    problems = []
    if len(transport.calls) != 2:
        problems.append("clearing the cache did not re-probe (%d calls)"
                        % len(transport.calls))
    if got != "Server":
        problems.append("re-probe returned %s" % got)
    suite.record(GB, "cache-is-in-memory-only", problems,
                 detail=["a disk cache would outlive a migration and keep "
                         "sending Cloud's search endpoint to a DC host"])
    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# C. search paging
# ---------------------------------------------------------------------------

def group_c(suite, mod):
    # -- Cloud: the token model ------------------------------------------
    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("CLD", 1, 2),
                            "nextPageToken": "tok-1"}),
        response(mod, 200, {"issues": issues("CLD", 3, 2)}),
    ], url=BASE_CLOUD, deployment="Cloud")
    got = keys_of(client.search_issues("project = CLD", limit=50))
    suite.record(GC, "cloud-stops-when-nextPageToken-is-absent",
                 check_pages(got, transport.calls,
                             ["CLD-1", "CLD-2", "CLD-3", "CLD-4"], 2),
                 detail=["there is no total to count down: the ABSENCE of the "
                         "key is the only end-of-stream signal"])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("CLD", 1, 2),
                            "nextPageToken": "tok-1"}),
        response(mod, 200, {"issues": issues("CLD", 3, 1),
                            "nextPageToken": "tok-2", "isLast": True}),
    ], url=BASE_CLOUD, deployment="Cloud")
    got = keys_of(client.search_issues("project = CLD", limit=50))
    suite.record(GC, "cloud-stops-on-isLast-even-with-a-token",
                 check_pages(got, transport.calls,
                             ["CLD-1", "CLD-2", "CLD-3"], 2))

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("CLD", 1, 2),
                            "nextPageToken": "tok-1"}),
        response(mod, 200, {"issues": issues("CLD", 3, 2)}),
    ], url=BASE_CLOUD, deployment="Cloud")
    list(client.search_issues("project = CLD", limit=50))
    problems = []
    if "nextPageToken" in (transport.calls[0].body or {}):
        problems.append("the first request already carried a page token")
    if (transport.calls[1].body or {}).get("nextPageToken") != "tok-1":
        problems.append("the second request sent %r"
                        % (transport.calls[1].body or {}).get("nextPageToken"))
    if any("startAt" in (c.body or {}) for c in transport.calls):
        problems.append("startAt has no meaning on the Cloud endpoint")
    suite.record(GC, "cloud-echoes-the-token-and-never-startAt", problems,
                 detail=["bodies: %r" % [c.body for c in transport.calls]])

    problems = []
    for call in transport.calls:
        if call.method != "POST":
            problems.append("%s is not a POST" % call.method)
        if call.path != "/rest/api/2/search/jql":
            problems.append("path %s" % call.path)
    suite.record(GC, "cloud-endpoint-is-post-search-jql", problems,
                 detail=["POST, never GET: a GET would put JQL in the query "
                         "string, and its percent-encoding is the #1 source "
                         "of spurious 400s"])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("CLD", 1, 3),
                            "nextPageToken": "tok-1"}),
        response(mod, 200, {"issues": issues("CLD", 4, 3),
                            "nextPageToken": "tok-2"}),
    ], url=BASE_CLOUD, deployment="Cloud")
    got = keys_of(client.search_issues("project = CLD", limit=4))
    problems = check_pages(got, transport.calls,
                           ["CLD-1", "CLD-2", "CLD-3", "CLD-4"], 2)
    if (transport.calls[1].body or {}).get("maxResults") != 1:
        problems.append("the second page asked for %r, want the 1 still "
                        "outstanding"
                        % (transport.calls[1].body or {}).get("maxResults"))
    suite.record(GC, "cloud-limit-caps-ACROSS-pages", problems,
                 detail=["--limit is a total, not a per-page size: a limit "
                         "honoured per page is the classic version of this bug"])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("CLD", 1, 1),
                            "nextPageToken": "same"}),
        response(mod, 200, {"issues": issues("CLD", 2, 1),
                            "nextPageToken": "same"}),
    ], url=BASE_CLOUD, deployment="Cloud")
    got = keys_of(client.search_issues("project = CLD", limit=50))
    suite.record(GC, "cloud-an-unchanged-token-does-not-loop-forever",
                 check_pages(got, transport.calls, ["CLD-1", "CLD-2"], 2),
                 detail=["a server that keeps handing back the same token "
                         "would otherwise page until the process is killed"])

    # -- DC: the offset model --------------------------------------------
    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("DC", 1, 2), "startAt": 0,
                            "maxResults": 2, "total": 4}),
        response(mod, 200, {"issues": issues("DC", 3, 2), "startAt": 2,
                            "maxResults": 2, "total": 4}),
    ], deployment="Server")
    got = keys_of(client.search_issues("project = DC", limit=50))
    suite.record(GC, "dc-stops-when-startAt-plus-len-reaches-total",
                 check_pages(got, transport.calls,
                             ["DC-1", "DC-2", "DC-3", "DC-4"], 2))

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("DC", 1, 2), "startAt": 0}),
        response(mod, 200, {"issues": [], "startAt": 2}),
    ], deployment="Server")
    got = keys_of(client.search_issues("project = DC", limit=50))
    suite.record(GC, "dc-stops-on-an-empty-page-when-total-is-absent",
                 check_pages(got, transport.calls, ["DC-1", "DC-2"], 2),
                 detail=["total is documented as OPTIONAL, so the empty page "
                         "has to be a stop condition in its own right"])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("DC", 1, 2), "startAt": 0,
                            "total": 999}),
        response(mod, 200, {"issues": [], "startAt": 2, "total": 999}),
    ], deployment="Server")
    got = keys_of(client.search_issues("project = DC", limit=50))
    suite.record(GC, "dc-stops-on-an-empty-page-even-when-total-disagrees",
                 check_pages(got, transport.calls, ["DC-1", "DC-2"], 2),
                 detail=["total may change between pages, and a page may "
                         "legitimately come back empty -- trusting total "
                         "alone here is an infinite loop"])

    problems = []
    if (transport.calls[0].body or {}).get("startAt") != 0:
        problems.append("first startAt %r"
                        % (transport.calls[0].body or {}).get("startAt"))
    if (transport.calls[1].body or {}).get("startAt") != 2:
        problems.append("second startAt %r"
                        % (transport.calls[1].body or {}).get("startAt"))
    if any("nextPageToken" in (c.body or {}) for c in transport.calls):
        problems.append("nextPageToken has no meaning on the DC endpoint")
    suite.record(GC, "dc-advances-startAt-and-never-sends-a-token", problems,
                 detail=["bodies: %r" % [c.body for c in transport.calls]])

    problems = []
    for call in transport.calls:
        if call.method != "POST":
            problems.append("%s is not a POST" % call.method)
        # The context path is part of the expectation, not an inconvenience:
        # BASE_DC carries /jira, so a plain "/rest/api/2/search" here would
        # mean the join had eaten it.
        if call.path != "/jira/rest/api/2/search":
            problems.append("path %s" % call.path)
    suite.record(GC, "dc-endpoint-is-post-search-under-the-context-path",
                 problems,
                 detail=["base %s -> %s" % (BASE_DC, transport.calls[0].path)])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": issues("DC", 1, 2), "startAt": 0,
                            "total": 10}),
        response(mod, 200, {"issues": issues("DC", 3, 2), "startAt": 2,
                            "total": 10}),
    ], deployment="Server")
    got = keys_of(client.search_issues("project = DC", limit=3))
    problems = check_pages(got, transport.calls, ["DC-1", "DC-2", "DC-3"], 2)
    if (transport.calls[1].body or {}).get("maxResults") != 1:
        problems.append("the second page asked for %r, want 1"
                        % (transport.calls[1].body or {}).get("maxResults"))
    suite.record(GC, "dc-limit-caps-ACROSS-pages", problems)

    # -- the field list, and the shape of the public return --------------
    client, transport = client_with(mod, [
        response(mod, 200, {"issues": [], "startAt": 0, "total": 0}),
    ], deployment="Server")
    list(client.search_issues("project = DC", limit=10))
    got = (transport.calls[0].body or {}).get("fields")
    suite.record(GC, "default-fields-are-named-explicitly",
                 problem_if(got != mod.SEARCH_FIELDS,
                            "sent %r, want %r" % (got, mod.SEARCH_FIELDS)),
                 detail=["DC's search defaults to *navigable while get-issue "
                         "defaults to *all, so naming them is the only "
                         "portable behaviour",
                         "the constant is SEARCH_FIELDS, not one list shared "
                         "with `get`: group K pins the split itself",
                         "sent: %r" % (got,)])

    client, transport = client_with(mod, [
        response(mod, 200, {"issues": [], "startAt": 0, "total": 0}),
    ], deployment="Server")
    list(client.search_issues("project = DC", fields=["*all"], limit=10))
    got = (transport.calls[0].body or {}).get("fields")
    suite.record(GC, "star-all-opts-out-of-the-explicit-list",
                 problem_if(got != ["*all"], "sent %r" % (got,)))

    client, transport = client_with(mod, [], deployment="Server")
    result = client.search_issues("project = DC", limit=0)
    consumed = list(result)
    problems = []
    if not inspect.isgeneratorfunction(mod.Jira.search_issues):
        problems.append("search_issues is not a generator function, so it "
                        "cannot be the lazy iterator the API promises")
    if consumed:
        problems.append("limit=0 yielded %r" % consumed)
    if transport.calls:
        problems.append("limit=0 still sent %d request(s)"
                        % len(transport.calls))
    suite.record(GC, "search-returns-an-iterator-and-no-total", problems,
                 detail=["Cloud has no total and DC calls it optional, so the "
                         "public return type deliberately cannot carry one"])

    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# D. config resolution
# ---------------------------------------------------------------------------

def group_d(suite, mod):
    with EnvSandbox(JIRA_URL="https://env.example", JIRA_TOKEN="env-token"):
        args = parse_args(mod, connected(["whoami"], url="https://flag.example",
                                         token="flag-token"))
        cfg = mod.resolve_config(args)
    problems = []
    if cfg.base_url != "https://flag.example":
        problems.append("url: %r" % cfg.base_url)
    if cfg.token != "flag-token":
        problems.append("token: %r" % cfg.token)
    if cfg.sources["url"] != "--url" or cfg.sources["token"] != "--token":
        problems.append("sources: %r" % cfg.sources)
    suite.record(GD, "flag-beats-env", problems,
                 detail=["sources: %r" % cfg.sources])

    with EnvSandbox(JIRA_URL="https://env.example", JIRA_TOKEN="env-token"):
        cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
    problems = []
    if cfg.base_url != "https://env.example" or cfg.token != "env-token":
        problems.append("resolved %r / %r" % (cfg.base_url, cfg.token))
    if cfg.sources["url"] != "env:JIRA_URL":
        problems.append("url source: %r" % cfg.sources["url"])
    if cfg.sources["token"] != "env:JIRA_TOKEN":
        problems.append("token source: %r" % cfg.sources["token"])
    suite.record(GD, "env-fallback-records-its-source", problems,
                 detail=["sources: %r" % cfg.sources])

    with EnvSandbox(jira_url="https://lower.example", jira_token="lower-token"):
        cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
    problems = []
    if cfg.base_url != "https://lower.example":
        problems.append("url: %r" % cfg.base_url)
    if cfg.sources["url"] != "env:jira_url":
        problems.append("url source: %r" % cfg.sources["url"])
    suite.record(GD, "lowercase-env-alias-is-a-fallback", problems,
                 detail=["sources: %r" % cfg.sources])

    with EnvSandbox():
        with captured() as (out, err):
            try:
                mod.resolve_config(parse_args(mod, ["whoami"]))
                code = None
            except SystemExit as exc:
                code = exc.code
    text = err.getvalue()
    problems = []
    if code != 2:
        problems.append("exit code %r, want 2" % code)
    problems += ["missing from the message: %r" % t
                 for t in missing_tokens(text, ["refusing to run",
                                                "JIRA_URL (or --url)",
                                                "JIRA_TOKEN (or --token)"])]
    if out.getvalue().strip():
        problems.append("a configuration refusal wrote to stdout: %r"
                        % out.getvalue())
    suite.record(GD, "missing-config-exits-2-naming-var-and-flag", problems,
                 detail=[line for line in text.splitlines()])

    with EnvSandbox(JIRA_URL="https://env.example"):
        with captured() as (_out, err):
            try:
                mod.resolve_config(parse_args(mod, ["whoami"]))
                code = None
            except SystemExit as exc:
                code = exc.code
    text = err.getvalue()
    problems = []
    if code != 2:
        problems.append("exit code %r" % code)
    if "JIRA_TOKEN (or --token)" not in text:
        problems.append("the token is missing but unnamed")
    if "JIRA_URL" in text:
        problems.append("JIRA_URL was reported missing although it is set")
    suite.record(GD, "only-the-actually-missing-var-is-named", problems,
                 detail=[line for line in text.splitlines()])

    with EnvSandbox(JIRA_URL=BASE_CLOUD, JIRA_TOKEN=TOKEN, JIRA_EMAIL=EMAIL):
        cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
    suite.record(GD, "email-presence-selects-basic",
                 problem_if(not cfg.auth_header.startswith("Basic "),
                            "auth: %r" % cfg.auth_header.split(" ")[0]),
                 detail=["auth mode: %s" % cfg.auth_mode])

    with EnvSandbox(JIRA_URL=BASE_DC, JIRA_TOKEN=TOKEN):
        cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
    suite.record(GD, "email-absence-selects-bearer",
                 problem_if(cfg.auth_header != "Bearer " + TOKEN,
                            "auth: %r" % cfg.auth_header.split(" ")[0]),
                 detail=["auth mode: %s" % cfg.auth_mode])

    truthy = ["1", "true", "TRUE", "True", "yes", "YES", " true "]
    falsey = ["0", "false", "no", "", "off", "maybe", None]
    problems = []
    for value in truthy:
        with EnvSandbox(JIRA_URL=BASE_DC, JIRA_TOKEN=TOKEN,
                        JIRA_READ_ONLY=value):
            cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
        if not cfg.read_only:
            problems.append("%r must be truthy" % value)
    for value in falsey:
        with EnvSandbox(JIRA_URL=BASE_DC, JIRA_TOKEN=TOKEN,
                        JIRA_READ_ONLY=value):
            cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
        if cfg.read_only:
            problems.append("%r must be falsey" % value)
    suite.record(GD, "read-only-truthy-table", problems,
                 detail=["truthy: %r" % truthy, "falsey: %r" % falsey])

    with EnvSandbox(JIRA_URL=BASE_DC, JIRA_TOKEN=TOKEN):
        cfg = mod.resolve_config(parse_args(mod, ["whoami"]))
    missing = [k for k in ("url", "token", "email", "read_only")
               if k not in cfg.sources]
    suite.record(GD, "sources-map-covers-every-setting",
                 problem_if(missing, "no source recorded for %r" % missing),
                 detail=["sources: %r" % cfg.sources])

    client, transport = client_with(
        mod, [response(mod, 200, {"displayName": "A Person",
                                  "emailAddress": EMAIL,
                                  "accountId": "5b10a2"})],
        url=BASE_CLOUD, email=EMAIL, deployment="Cloud")
    args = parse_args(mod, connected(["whoami", "--email", EMAIL],
                                     url=BASE_CLOUD))
    with captured() as (out, err):
        code = mod.cmd_whoami(args, client)
    text = out.getvalue() + err.getvalue()
    problems = []
    if code != 0:
        problems.append("exit %r" % code)
    if TOKEN in text:
        problems.append("THE TOKEN VALUE WAS PRINTED")
    problems += ["whoami omits %r" % t
                 for t in missing_tokens(text, ["--token", "%d chars"
                                                % len(TOKEN), "Cloud",
                                                "A Person"])]
    suite.record(GD, "whoami-reports-token-source-and-length-never-the-value",
                 problems,
                 detail=out.getvalue().splitlines()
                 + ["a masked secret in a pasted terminal log is still a "
                    "secret in a log, so it is not printed at all"])
    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# E. read-only + dry-run
# ---------------------------------------------------------------------------

WRITE_INVOCATIONS = {
    "comment": ["comment", "PROJ-1234", "a body"],
    "transition": ["transition", "PROJ-1234", "31"],
    "worklog": ["worklog", "PROJ-1234", "3h 20m"],
}


def group_e(suite, mod):
    for name, tail in sorted(WRITE_INVOCATIONS.items()):
        argv = connected(tail)
        with EnvSandbox(JIRA_READ_ONLY="1"):
            with NetworkGuard(mod) as guard:
                with captured() as (out, err):
                    code = mod.main(argv)
        text = err.getvalue()
        problems = []
        if code != 2:
            problems.append("exit %r, want 2" % code)
        if guard.calls:
            problems.append("%d request(s) were made before refusing"
                            % guard.calls)
        if "JIRA_READ_ONLY" not in text:
            problems.append("the refusal does not name JIRA_READ_ONLY: %r"
                            % text)
        if name not in text:
            problems.append("the refusal does not name the subcommand")
        suite.record(GE, "read-only-refuses-%s" % name, problems,
                     detail=[text.strip(), "requests made: %d" % guard.calls])

    def myself(_method, _url, _body, _headers):
        return response(mod, 200, {"displayName": "A Person",
                                   "deploymentType": "Server"})

    mod.reset_deployment_cache()
    with EnvSandbox(JIRA_READ_ONLY="true"):
        with NetworkGuard(mod, responder=myself) as guard:
            with captured() as (out, err):
                code = mod.main(connected(["whoami"]))
    suite.record(GE, "read-only-does-not-block-a-read",
                 problem_if(code != 0, "whoami exited %r under JIRA_READ_ONLY"
                            % code),
                 detail=["requests: %d" % guard.calls,
                         "read-only gates WRITES; a read under it must still "
                         "work, or the flag becomes an off switch"])
    mod.reset_deployment_cache()

    with EnvSandbox(JIRA_READ_ONLY="0"):
        with NetworkGuard(mod) as guard:
            with captured() as (out, err):
                code = mod.main(connected(["comment", "PROJ-1234", "hi",
                                           "--dry-run"]))
    problems = []
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if guard.calls:
        problems.append("--dry-run sent %d request(s)" % guard.calls)
    suite.record(GE, "read-only-falsey-does-not-refuse", problems,
                 detail=[out.getvalue().strip()])

    dry_cases = [
        ("comment", ["comment", "PROJ-1234", "shipped it", "--dry-run"],
         "/rest/api/2/issue/PROJ-1234/comment", {"body": "shipped it"}),
        ("transition", ["transition", "PROJ-1234", "31", "--dry-run",
                        "--comment", "moving on"],
         "/rest/api/2/issue/PROJ-1234/transitions",
         {"transition": {"id": "31"},
          "update": {"comment": [{"add": {"body": "moving on"}}]}}),
        ("worklog", ["worklog", "PROJ-1234", "3h 20m", "--dry-run",
                     "--comment", "pairing",
                     "--started", "2026-08-26T09:00:00.000+0000"],
         "/rest/api/2/issue/PROJ-1234/worklog",
         {"timeSpent": "3h 20m", "comment": "pairing",
          "started": "2026-08-26T09:00:00.000+0000"}),
    ]
    for name, tail, path, want_body in dry_cases:
        argv = connected(tail)
        with EnvSandbox():
            with NetworkGuard(mod) as guard:
                with captured() as (out, err):
                    code = mod.main(argv)
        text = out.getvalue()
        problems = []
        if code != 0:
            problems.append("exit %r, want 0" % code)
        if guard.calls:
            problems.append("--dry-run sent %d request(s)" % guard.calls)
        if "POST" not in text:
            problems.append("the method is not printed")
        if BASE_DC + path not in text:
            problems.append("the exact URL is not printed (want %s)"
                            % (BASE_DC + path))
        # The body now lives inside a fenced ```json block, so it is read out
        # of the fence rather than from the first brace onwards: taking the
        # remainder of the document would swallow the closing fence and the
        # case would fail on the RENDERING instead of on the body.
        body_text = fenced_block(text, "json")
        try:
            printed = json.loads(body_text)
        except ValueError:
            printed = None
            problems.append("the body is not a fenced ```json block that "
                            "parses: %r" % body_text)
        if printed is not None and printed != want_body:
            problems.append("body %r, want %r" % (printed, want_body))
        suite.record(GE, "dry-run-%s-prints-and-sends-nothing" % name, problems,
                     detail=text.splitlines()
                     + ["requests made: %d" % guard.calls])


# ---------------------------------------------------------------------------
# F. error mapping
# ---------------------------------------------------------------------------

HTML_BODY = ("<!DOCTYPE html><html><head><title>Sign in</title></head>"
             "<body>Your session has expired. Please log in again via the "
             "single sign-on portal.</body></html>")


def group_f(suite, mod):
    client, _t = client_with(mod, [response(mod, 404, {"errorMessages": [
        "Issue does not exist or you do not have permission to see it."]})])
    try:
        client.request("GET", "/rest/api/2/issue/PROJ-1234",
                       subject="PROJ-1234")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = missing_tokens(text, [
        "404", "PROJ-1234", "does not exist, or your account lacks Browse "
        "Projects / issue-level security permission for it"])
    suite.record(GF, "404-says-it-means-two-things", problems,
                 detail=text.splitlines()
                 + ["Jira returns 404 for both 'no such issue' and 'exists "
                    "but you may not see it', so a message that picks one is "
                    "wrong half the time"])

    client, _t = client_with(mod, [response(mod, 404, {})])
    try:
        client.request("GET", "/rest/api/2/issue/OPS-9", subject="OPS-9")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    suite.record(GF, "404-names-the-subject",
                 problem_if("OPS-9" not in text, "subject missing: %r" % text),
                 detail=text.splitlines())

    client, _t = client_with(mod, [response(mod, 200, body=HTML_BODY,
                                            ctype="text/html;charset=UTF-8")])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = missing_tokens(text, [
        "text/html", "SSO proxy", "context path is wrong", "Sign in"])
    if "Expecting value" in text:
        problems.append("json.loads ran anyway; the Content-Type branch is "
                        "supposed to come FIRST")
    suite.record(GF, "html-body-is-diagnosed-before-json-loads", problems,
                 detail=text.splitlines()
                 + ["behind an SSO proxy the body is a login page, and a JSON "
                    "syntax error at character 0 says nothing useful"])

    client, _t = client_with(mod, [response(mod, 200, body=HTML_BODY,
                                            ctype="text/html")])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    body_part = text.split("body:", 1)[1] if "body:" in text else ""
    suite.record(GF, "html-snippet-is-trimmed",
                 problem_if(len(body_part) > 320,
                            "%d chars of body reproduced" % len(body_part)),
                 detail=["~200 chars is enough to recognise a login page and "
                         "little enough to read",
                         "reproduced: %d chars" % len(body_part)])

    # An anonymous answer on a 200: Jira does not challenge, it under-answers.
    client, _t = client_with(mod, [response(mod, 200, {"issues": []},
                                            headers={"X-AUSERNAME": "anonymous"})])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = missing_tokens(text, ["X-AUSERNAME: anonymous",
                                     "credentials were not accepted",
                                     "anonymous user"])
    suite.record(GF, "anonymous-fallthrough-on-a-200-is-an-auth-failure",
                 problems,
                 detail=text.splitlines()
                 + ["there is no WWW-Authenticate challenge: a bad credential "
                    "comes back 200 with LESS DATA, which is why the header "
                    "is checked on every response"])

    client, _t = client_with(mod, [response(mod, 200, {"ok": 1},
                                            headers={"x-ausername": "ANONYMOUS"})])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    suite.record(GF, "anonymous-check-ignores-header-and-value-case",
                 problem_if("anonymous" not in text.lower(),
                            "not detected: %r" % text))

    client, _t = client_with(mod, [response(mod, 200, {"name": "real.user"},
                                            headers={"X-AUSERNAME": "real.user"})])
    try:
        got = client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        got, text = None, str(exc)
    suite.record(GF, "a-named-user-is-not-an-auth-failure",
                 problem_if(got != {"name": "real.user"},
                            "rejected a legitimate answer: %r" % text),
                 detail=["ANTI-VACUITY: the anonymous check must not fire on "
                         "every response that carries the header"])

    client, _t = client_with(mod, [response(mod, 204, body=b"", ctype=None)])
    try:
        got = client.request("POST", "/rest/api/2/issue/PROJ-1/transitions",
                             body={"transition": {"id": "5"}})
        problems = problem_if(got is not None, "returned %r, want None" % got)
    except Exception as exc:
        problems = ["204 raised %s: %s" % (type(exc).__name__, exc)]
    suite.record(GF, "204-no-content-is-the-success-answer", problems,
                 detail=["a transition succeeds with an empty body, and "
                         "json.loads('') would explode on it"])

    client, _t = client_with(mod, [response(mod, 200, body=b"",
                                            ctype="application/json")])
    try:
        got = client.request("GET", "/rest/api/2/myself")
        problems = problem_if(got is not None, "returned %r" % got)
    except Exception as exc:
        problems = ["an empty 200 raised %s: %s" % (type(exc).__name__, exc)]
    suite.record(GF, "empty-200-body-does-not-explode", problems)

    client, _t = client_with(mod, [response(mod, 400, {
        "errorMessages": ["Field 'timeSpent' is required."],
        "errors": {"timeSpent": "must be set", "started": "bad format"}})])
    try:
        client.request("POST", "/rest/api/2/issue/PROJ-1/worklog",
                       body={"x": 1})
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = missing_tokens(text, ["Field 'timeSpent' is required.",
                                     "timeSpent=must be set",
                                     "started=bad format"])
    suite.record(GF, "error-envelope-is-surfaced-in-full", problems,
                 detail=text.splitlines()
                 + ["errorMessages joined, plus every errors key=value -- the "
                    "keys are where a field-level rejection actually lives"])

    client, _t = client_with(mod, [response(
        mod, 401, {"errorMessages": ["Login failed"]},
        headers={"X-Seraph-LoginReason": "AUTHENTICATION_DENIED"})])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = missing_tokens(text, ["CAPTCHA triggered",
                                     "log in via the web UI"])
    suite.record(GF, "captcha-lockout-is-named", problems,
                 detail=text.splitlines()
                 + ["without this line the symptom is 'my correct password "
                    "stopped working'"])

    client, _t = client_with(mod, [response(mod, 401, {
        "errorMessages": ["Client must be authenticated"]})])
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    suite.record(GF, "no-captcha-claim-without-the-header",
                 problem_if("CAPTCHA" in text,
                            "claimed a CAPTCHA with no X-Seraph-LoginReason"),
                 detail=["ANTI-VACUITY: the hint must be evidence-driven"] +
                 text.splitlines())

    table = [(200, False), (204, False), (400, False), (401, False),
             (403, False), (404, False), (409, False), (429, True),
             (500, True), (502, True), (503, True), (504, True)]
    problems = [
        "%d: retryable=%s, want %s" % (status, mod._is_retryable(status), want)
        for status, want in table if mod._is_retryable(status) != want]
    suite.record(GF, "retry-only-429-and-5xx", problems,
                 detail=["repeating any other 4xx merely repeats the mistake",
                         "table: %r" % table])

    for spelling in ("Retry-After", "retry-after"):
        slept = []
        mod.reset_deployment_cache()
        transport = FakeTransport([
            response(mod, 429, {"errorMessages": ["rate limited"]},
                     headers={spelling: "7"}),
            response(mod, 200, {"ok": True}),
        ])
        client = mod.Jira(make_cfg(mod), fetch=transport,
                          sleep=lambda s: slept.append(s))
        got = client.request("GET", "/rest/api/2/myself")
        problems = []
        if got != {"ok": True}:
            problems.append("the retry did not succeed: %r" % (got,))
        if slept != [7.0]:
            problems.append("slept %r, want [7.0]" % slept)
        suite.record(GF, "retry-after-honoured-as-%s" % spelling.lower()
                     + ("-uppercase" if spelling[0].isupper() else "-lowercase"),
                     problems,
                     detail=["Cloud sends Retry-After, DC sends retry-after; "
                             "a case-sensitive lookup honours exactly one",
                             "slept: %r" % slept])

    slept = []
    transport = FakeTransport([response(mod, 503, {"errorMessages": ["down"]})
                               for _ in range(5)])
    client = mod.Jira(make_cfg(mod), fetch=transport,
                      sleep=lambda s: slept.append(s))
    try:
        client.request("GET", "/rest/api/2/myself")
        text = ""
    except mod.JiraError as exc:
        text = str(exc)
    problems = []
    if len(transport.calls) != 3:
        problems.append("%d attempt(s), want 3" % len(transport.calls))
    if len(slept) != 2:
        problems.append("slept %d time(s) for 3 attempts" % len(slept))
    if slept != sorted(slept) or len(set(slept)) != len(slept):
        problems.append("backoff is not increasing: %r" % slept)
    suite.record(GF, "retry-caps-at-three-attempts-with-backoff", problems,
                 detail=["attempts: %d" % len(transport.calls),
                         "sleeps  : %r (no Retry-After header was sent)"
                         % slept])

    transport = FakeTransport([response(mod, 400, {"errorMessages": ["bad"]})])
    client = mod.Jira(make_cfg(mod), fetch=transport, sleep=lambda _s: None)
    try:
        client.request("GET", "/rest/api/2/myself")
    except mod.JiraError:
        pass
    suite.record(GF, "a-400-is-never-retried",
                 problem_if(len(transport.calls) != 1,
                            "%d attempt(s)" % len(transport.calls)))
    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# G. issue key validation
# ---------------------------------------------------------------------------

KEY_SUBCOMMANDS = {
    "get": ["get", "%s"],
    "transitions": ["transitions", "%s"],
    "comment": ["comment", "%s", "hi"],
    "transition": ["transition", "%s", "31"],
    "worklog": ["worklog", "%s", "3h"],
}


def group_g(suite, mod):
    suite.record(GG, "accepted-and-rejected-tables",
                 check_key_validator(mod._validate_issue_key),
                 detail=["accepted: %r" % KEYS_OK, "rejected: %r" % KEYS_BAD])

    for key in ("STR-1234", "ABC1-99"):
        try:
            mod._validate_issue_key(key)
            problems = []
        except Exception as exc:
            problems = ["%r rejected: %s" % (key, exc)]
        suite.record(GG, "accepts-%s" % key, problems)

    for key in ("str-1234", "STR1234", "STR-", "-1234", ""):
        try:
            mod._validate_issue_key(key)
            problems = ["%r was accepted" % key]
        except mod.SetupError:
            problems = []
        except Exception as exc:
            problems = ["%r raised %s, want SetupError"
                        % (key, type(exc).__name__)]
        suite.record(GG, "rejects-%s" % (key or "<empty>"), problems,
                     detail=["a malformed key must be a LOCAL error: sending "
                             "it would come back as an ambiguous 404"])

    for name, template in sorted(KEY_SUBCOMMANDS.items()):
        argv = connected([part % "not-a-key" if "%s" in part else part
                          for part in template])
        with EnvSandbox():
            with NetworkGuard(mod) as guard:
                with captured() as (out, err):
                    code = mod.main(argv)
        text = err.getvalue()
        problems = []
        if code != 2:
            problems.append("exit %r, want 2" % code)
        if guard.calls:
            problems.append("%d request(s) were made for a malformed key"
                            % guard.calls)
        problems += ["message omits %r" % t for t in missing_tokens(
            text, ["invalid issue key", "not-a-key", "PROJ-1234"])]
        suite.record(GG, "%s-rejects-a-bad-key-offline" % name, problems,
                     detail=[text.strip(), "requests: %d" % guard.calls])


# ---------------------------------------------------------------------------
# H. NEGATIVE CONTROL -- the oracles above must be able to fail
# ---------------------------------------------------------------------------

def _mutant_auth(email, token):
    """Ignores the email: always Bearer. The live Cloud-auth defect."""
    return "Bearer " + token


def _mutant_join(base, path):
    """urljoin -- which eats the context path. The live DC defect."""
    return urllib.parse.urljoin(base, path)


def _mutant_key_validator(_key):
    """Accepts anything, which is what having no validation looks like."""
    return None


MUTANTS = [
    ("mutant-auth-ignores-the-email", lambda: check_auth(_mutant_auth),
     "always Bearer, so a Cloud account silently authenticates as nobody"),
    ("mutant-join-uses-urljoin", lambda: check_join(_mutant_join),
     "urljoin drops the /jira context path"),
    ("mutant-key-validator-accepts-anything",
     lambda: check_key_validator(_mutant_key_validator),
     "no validation at all"),
    ("mutant-pager-over-fetches",
     lambda: check_pages(["A-1", "A-2", "A-3"], [1, 2, 3], ["A-1", "A-2"], 2),
     "one page too many: right-looking issues, one extra round trip"),
    ("mutant-pager-ignores-an-empty-page",
     lambda: check_pages(["A-1"], [1, 2, 3, 4], ["A-1"], 2),
     "kept paging past the empty page; the issue list alone cannot see it"),
]


def group_h(suite, mod):
    caught = 0
    for cid, run_oracle, why in MUTANTS:
        problems = run_oracle()
        if problems:
            caught += 1
        suite.record(GH, "control-%s" % cid,
                     problem_if(not problems,
                                "the oracle ACCEPTED a broken implementation, "
                                "so it is not protecting anything"),
                     detail=["mutant  : %s" % why,
                             "rejected with: %s" % ("; ".join(problems)
                                                    or "NOTHING")])

    suite.record(GH, "control-fires-at-all",
                 problem_if(caught != len(MUTANTS),
                            "only %d of %d mutants were caught"
                            % (caught, len(MUTANTS))),
                 detail=["%d/%d mutants rejected" % (caught, len(MUTANTS)),
                         "a control that stops running is indistinguishable "
                         "from code that is correct"])

    # The mirror: the SAME oracles must pass the real implementations, or the
    # control above would be satisfied by an oracle that rejects everything.
    problems = []
    problems += ["auth: %s" % p for p in check_auth(mod.auth_header)]
    problems += ["join: %s" % p for p in check_join(mod.api_url)]
    problems += ["key: %s" % p
                 for p in check_key_validator(mod._validate_issue_key)]
    problems += ["pages: %s" % p
                 for p in check_pages(["A-1", "A-2"], [1, 2], ["A-1", "A-2"], 2)]
    suite.record(GH, "control-real-implementations-pass-the-same-oracles",
                 problems,
                 detail=["an oracle that rejects EVERYTHING would satisfy the "
                         "mutant rows above and prove nothing"])


# ---------------------------------------------------------------------------
# J. attachment upload -- the one request body in this client that is not JSON
# ---------------------------------------------------------------------------

BOUND = "BOUNDARYbeef"
HEAD_END = b"\r\n\r\n"


class RawTransport:
    """Like FakeTransport, but keeps the body as BYTES.

    FakeTransport json.loads() every body, which is right for eight of the nine
    subcommands and impossible for the ninth: a multipart body is binary and
    decoding it would either raise or silently corrupt what the case measures.
    """

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append(Call(method, url, body, dict(headers)))
        if not self.script:
            raise AssertionError("unscripted request: %s %s" % (method, url))
        item = self.script.pop(0)
        return item(method, url) if callable(item) else item


def raw_client(mod, script, url=BASE_DC):
    mod.reset_deployment_cache()
    mod._DEPLOYMENT_CACHE = mod.SERVER
    transport = RawTransport(script)
    return mod.Jira(make_cfg(mod, url=url), fetch=transport,
                    sleep=lambda _seconds: None), transport


def check_multipart(encode_fn):
    """Problems for an `encode(field, filename, data, boundary) -> (bytes, ct)`.

    Shared by the live rows and by the control below, so a row that passes here
    is passing an oracle that has been shown to reject a broken encoder.
    """
    problems = []
    body, ctype = encode_fn("file", "a.txt", b"DATA", boundary=BOUND)
    if not isinstance(body, bytes):
        return ["the encoder returned %s, not bytes" % type(body).__name__]
    head = body.split(HEAD_END, 1)[0]
    if b"\n" in head.replace(b"\r\n", b""):
        problems.append("a bare LF survives in the header section, which the "
                        "proxies in front of some Jira installs drop")
    if not body.startswith(("--%s\r\n" % BOUND).encode()):
        problems.append("the body does not open with the CRLF-terminated "
                        "opening boundary")
    if not body.endswith(("\r\n--%s--\r\n" % BOUND).encode()):
        problems.append("the body does not end with the closing boundary")
    if b'filename="a.txt"' not in body:
        problems.append("the filename is missing from Content-Disposition")
    if ("boundary=%s" % BOUND) not in ctype:
        problems.append("the returned Content-Type omits the boundary")
    return problems


def group_j(suite, mod, workspace):
    encode = mod.encode_multipart_file

    expected = (
        ("--%s\r\n" % BOUND).encode()
        + b'Content-Disposition: form-data; name="file"; '
          b'filename="a.txt"\r\n'
        + b"Content-Type: application/octet-stream\r\n\r\n"
        + b"DATA"
        + ("\r\n--%s--\r\n" % BOUND).encode())
    body, ctype = encode("file", "a.txt", b"DATA", boundary=BOUND)
    suite.record(GJ, "multipart-exact-bytes",
                 problem_if(body != expected,
                            "the encoded body does not match the pinned "
                            "expectation"),
                 detail=["got     : %r" % body, "expected: %r" % expected])

    head = body.split(HEAD_END, 1)[0]
    suite.record(GJ, "multipart-no-bare-lf-in-header",
                 problem_if(b"\n" in head.replace(b"\r\n", b""),
                            "a bare LF survives in the header section"),
                 detail=["RFC 2046 wants CRLF; an LF-only body is accepted by "
                         "some servers and dropped by other proxies, so the "
                         "failure is environment-dependent"])

    blob = b"\x00\xff\r\nnot-a-boundary\x00"
    raw, _ = encode("file", "b.bin", blob, boundary=BOUND)
    start = len(raw) - len(("\r\n--%s--\r\n" % BOUND).encode()) - len(blob)
    suite.record(GJ, "multipart-binary-passthrough",
                 problem_if(raw[start:start + len(blob)] != blob,
                            "the payload bytes were altered in transit "
                            "through the encoder"),
                 detail=["an attachment is not text; a decode/encode round "
                         "trip would corrupt every binary file"])

    suite.record(GJ, "multipart-content-type-value",
                 problem_if(ctype != "multipart/form-data; boundary=%s" % BOUND,
                            "unexpected Content-Type: %s" % ctype),
                 detail=["Content-Type: %s" % ctype])

    _, ct1 = encode("file", "a.txt", b"x")
    _, ct2 = encode("file", "a.txt", b"x")
    b1 = ct1.split("boundary=", 1)[1]
    b2 = ct2.split("boundary=", 1)[1]
    suite.record(GJ, "multipart-generated-boundary-shape",
                 problem_if(len(b1) != 32
                            or any(c not in "0123456789abcdef" for c in b1),
                            "a generated boundary is not 32 hex chars: %r" % b1),
                 detail=["length is the whole defence: nothing here can "
                         "rewrite the payload if the boundary collides"])
    suite.record(GJ, "multipart-generated-boundary-differs",
                 problem_if(b1 == b2,
                            "two calls produced the same boundary, so it is "
                            "not coming from the CSPRNG"))

    collided = ("--%s" % BOUND).encode()
    try:
        encode("file", "a.txt", b"before" + collided + b"after", boundary=BOUND)
        collision = ["the encoder accepted data containing its own boundary"]
    except mod.SetupError:
        collision = []
    suite.record(GJ, "multipart-boundary-collision-refused", collision,
                 detail=["the server stops reading at the first boundary it "
                         "sees, so a collision truncates the file and reports "
                         "SUCCESS -- the worst available failure mode"])

    quoted, _ = encode("file", 'we"ird.txt', b"x", boundary=BOUND)
    suite.record(GJ, "multipart-quote-in-filename-sanitised",
                 problem_if(b'filename="we_ird.txt"' not in quoted,
                            "a double quote survived into "
                            "Content-Disposition"),
                 detail=['a `"` closes the parameter early and the remainder '
                         "is parsed as header syntax"])

    try:
        encode("file", "", b"x", boundary=BOUND)
        empty_name = ["an empty filename was accepted"]
    except mod.SetupError:
        empty_name = []
    suite.record(GJ, "multipart-empty-filename-refused", empty_name)

    # -- the upload method: headers are the subject --------------------------

    created = [{"id": "4201", "filename": "spec.md", "size": 4}]
    client, transport = raw_client(mod, [response(mod, 200, created)])
    returned = client.upload_attachment("STR-7", "spec.md", b"DATA")
    call = transport.calls[0]

    suite.record(GJ, "upload-csrf-header-present",
                 problem_if(call.headers.get("X-Atlassian-Token") != "no-check",
                            "X-Atlassian-Token: no-check is missing"),
                 detail=["Jira's CSRF gate refuses EVERY multipart request "
                         "without it, on Cloud and on Server/DC, and the "
                         "rejection never mentions the header"])
    suite.record(GJ, "upload-content-type-is-multipart",
                 problem_if(not str(call.headers.get("Content-Type", "")
                                    ).startswith("multipart/form-data; "
                                                 "boundary="),
                            "Content-Type is %r"
                            % call.headers.get("Content-Type")))
    suite.record(GJ, "upload-authorization-unchanged-from-json-path",
                 problem_if(call.headers.get("Authorization")
                            != "Bearer " + TOKEN,
                            "the multipart path built a different "
                            "Authorization header"),
                 detail=["it comes from the same _headers() helper as every "
                         "other call, so it cannot drift on this one path"])
    suite.record(GJ, "upload-form-field-is-file",
                 problem_if(b'name="file"' not in call.body,
                            "the form field is not named `file`"),
                 detail=["Jira looks for that exact name and answers a "
                         "request without it with an unhelpful 500"])
    suite.record(GJ, "upload-array-response-returned",
                 problem_if(returned != created,
                            "the JSON array response was not returned "
                            "verbatim: %r" % (returned,)),
                 detail=["this endpoint answers with an ARRAY, not an object"])

    client, _ = raw_client(mod, [response(mod, 200, {"not": "a list"})])
    suite.record(GJ, "upload-non-list-response-is-empty",
                 problem_if(client.upload_attachment("STR-7", "a", b"x") != [],
                            "a non-list response did not collapse to []"),
                 detail=["an unexpected shape must not raise out of a write "
                         "that already succeeded server-side"])

    # -- main()-level: the guards -------------------------------------------

    good = os.path.join(workspace, "spec.md")
    with open(good, "wb") as handle:
        handle.write(b"hello attachment")
    empty = os.path.join(workspace, "empty.md")
    with open(empty, "wb") as handle:
        handle.write(b"")

    def attach_main(argv, env=None, responder=None):
        with EnvSandbox(**(env or {})):
            with NetworkGuard(mod, responder) as guard:
                with captured() as (out, err):
                    code = mod.main(connected(argv))
        return code, out.getvalue(), err.getvalue(), guard.calls

    code, out, _err, calls = attach_main(
        ["attach", "STR-7", good, "--name", "../../evil.md", "--dry-run"])
    suite.record(GJ, "attach-explicit-name-reduced-to-basename",
                 problem_if("evil.md" not in out or ".." in out,
                            "an explicit --name kept its path component"),
                 detail=["stdout: %r" % out,
                         "basename() runs on --name too, not only on the "
                         "name derived from the path"])

    code, _out, err, calls = attach_main(
        ["attach", "STR-7", os.path.join(workspace, "nope.md")])
    suite.record(GJ, "attach-missing-file-exit-2-and-no-request",
                 problem_if(code != 2 or calls != 0,
                            "exit %d after %d request(s); wanted exit 2 and 0"
                            % (code, calls)),
                 detail=["stderr: %r" % err.strip(),
                         "the file is read BEFORE anything is sent, so a "
                         "mistyped path costs no round trip"])

    code, _out, err, calls = attach_main(["attach", "STR-7", empty])
    suite.record(GJ, "attach-empty-file-exit-2",
                 problem_if(code != 2 or calls != 0,
                            "exit %d after %d request(s)" % (code, calls)),
                 detail=["stderr: %r" % err.strip(),
                         "Jira accepts a zero-byte attachment and lists it "
                         "like any other, so the mistake would surface as a "
                         "file nobody can open"])

    code, out, _err, calls = attach_main(
        ["attach", "STR-7", good, "--dry-run"])
    suite.record(GJ, "attach-dry-run-sends-nothing",
                 problem_if(code != 0 or calls != 0,
                            "exit %d after %d request(s)" % (code, calls)),
                 detail=["stdout: %r" % out])
    suite.record(GJ, "attach-dry-run-withholds-file-bytes",
                 problem_if("hello attachment" in out,
                            "the dry run printed the FILE CONTENT"),
                 detail=["a dry run of a binary attachment would otherwise "
                         "dump the file into the terminal",
                         "it reports the name and the byte count instead"])

    code, _out, err, calls = attach_main(["attach", "STR-7", good],
                                        env={"JIRA_READ_ONLY": "1"})
    suite.record(GJ, "attach-read-only-blocked",
                 problem_if(code != 2 or calls != 0,
                            "exit %d after %d request(s)" % (code, calls)),
                 detail=["stderr: %r" % err.strip(),
                         "attach is in WRITE_COMMANDS, so the refusal fires "
                         "before the file is even read"])

    # -- the negative control for THIS group --------------------------------

    def lf_encoder(field_name, filename, data, boundary=None):
        """A plausible encoder that uses LF where the format demands CRLF."""
        boundary = boundary or BOUND
        head = ("--%s\n"
                "Content-Disposition: form-data; name=\"%s\"; "
                "filename=\"%s\"\n"
                "Content-Type: application/octet-stream\n"
                "\n" % (boundary, field_name, filename))
        return (head.encode() + data
                + ("\n--%s--\n" % boundary).encode(),
                "multipart/form-data; boundary=%s" % boundary)

    suite.record(GJ, "control-multipart-oracle-rejects-lf-encoder",
                 problem_if(not check_multipart(lf_encoder),
                            "the oracle ACCEPTED an LF-only encoder, so it is "
                            "not protecting the CRLF requirement"),
                 detail=["mutant: CRLF replaced by LF throughout",
                         "rejected with: %s"
                         % ("; ".join(check_multipart(lf_encoder)) or "NOTHING")])

    # The mirror. Without it, an oracle that rejected EVERYTHING would satisfy
    # the row above and prove nothing.
    suite.record(GJ, "control-real-encoder-passes-the-same-oracle",
                 check_multipart(encode))


# ---------------------------------------------------------------------------
# K. Markdown rendering
# ---------------------------------------------------------------------------

# `self` deliberately carries the NUMERIC id, the way Jira builds it, and not
# the key: the "appears exactly once" rows below are about the RENDERING, and a
# fixture whose URL happened to spell the key would make them unprovable.
#
# The summary carries a `|` because that is the defect, and `description` is
# wiki markup whose second and third lines start with `#` -- outside a fence
# those are Markdown headings, which is the second reason the body is fenced.
SUMMARY_FRAGMENT = "crashes on boot"
DESCRIPTION = "h2. Steps\n# boot the service\n# watch it fall over"

GET_ISSUE = {
    "expand": "renderedFields,names,schema,operations,editmeta,changelog",
    "id": "10042",
    "self": BASE_DC + "/rest/api/2/issue/10042",
    "key": "STR-7",
    "renderedFields": None,
    "fields": {
        "summary": PIPED,
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Bug"},
        "priority": {"name": "High"},
        "resolution": None,
        "assignee": {"displayName": "A Person"},
        "reporter": {"displayName": "B Person"},
        "components": [{"name": "parser"}, {"name": "renderer"}],
        # An array of BARE STRINGS, unlike the two beside it: Jira does not
        # wrap a label in an object, and one flattener has to survive both.
        "labels": ["regression"],
        "fixVersions": [{"name": "2026.9.0"}],
        "created": "2026-08-20T09:15:00.000+0000",
        "updated": "2026-08-26T11:02:00.000+0000",
        "description": DESCRIPTION,
    },
}

# The same issue with every optional field empty: what the renderer does with
# ABSENCE is a separate question from what it does with data.
GET_BARE = {
    "id": "10043",
    "self": BASE_DC + "/rest/api/2/issue/10043",
    "key": "STR-8",
    "fields": {
        "summary": "a quiet issue",
        "status": {"name": "Open"},
        "issuetype": {"name": "Task"},
        "description": None,
        "components": [],
        "labels": [],
        "fixVersions": [],
    },
}


def render_get(mod, issue, tail=()):
    """cmd_get over a scripted transport -> (exit code, stdout, stderr)."""
    client, _transport = client_with(mod, [response(mod, 200, issue)],
                                     deployment="Server")
    args = parse_args(mod, connected(["get", issue["key"]] + list(tail)))
    with captured() as (out, err):
        code = mod.cmd_get(args, client)
    return code, out.getvalue(), err.getvalue()


def group_k(suite, mod):
    # -- the escaper: the one helper the whole document rests on ----------
    suite.record(GK, "escape-pipe-and-newline", check_escaper(mod.md_escape),
                 detail=["md_escape(%r)" % PIPED,
                         "     -> %r" % mod.md_escape(PIPED),
                         "a `|` in a summary is not exotic -- parse|render, "
                         "A|B test, 500|502 on deploy -- and it opens a column "
                         "the header does not have"])

    table = mod.md_table(("Key", "Summary"), [["STR-7", PIPED]])
    lines = table.splitlines()
    problems = []
    if len(lines) != 3:
        problems.append("a 1-row table rendered %d line(s)" % len(lines))
    else:
        want = unescaped_pipes(lines[0])
        for index, line in enumerate(lines):
            if unescaped_pipes(line) != want:
                problems.append("line %d has %d separator pipe(s), the header "
                                "has %d: %r"
                                % (index, unescaped_pipes(line), want, line))
    suite.record(GK, "escaped-cell-does-not-add-a-column", problems,
                 detail=lines
                 + ["every row must carry the same number of SEPARATOR pipes "
                    "as the header, however many pipes the values contain"])

    empty = mod.md_table(("Id", "Name", "To"), [])
    problems = []
    if empty.strip() != "_(none)_":
        problems.append("rendered %r, want _(none)_" % empty)
    if "|" in empty:
        problems.append("a bare header survived into the empty case: %r"
                        % empty)
    suite.record(GK, "empty-table-is-the-none-marker", problems,
                 detail=["got: %r" % empty,
                         "a header row plus a delimiter row with NO body rows "
                         "is not a table in GFM; it renders as two stray lines "
                         "of pipes"])

    one = mod.md_table(("Id", "Name"), [["31", "In Progress"]])
    lines = one.splitlines()
    problems = []
    if len(lines) != 3:
        problems.append("a 1-row table rendered %r" % (lines,))
    else:
        if "Id" not in lines[0] or "Name" not in lines[0]:
            problems.append("no header row: %r" % lines[0])
        if set(lines[1].replace("|", "").replace(" ", "")) != set("-"):
            problems.append("no GFM delimiter row: %r" % lines[1])
        if "In Progress" not in lines[2]:
            problems.append("the body row is missing: %r" % lines[2])
    suite.record(GK, "non-empty-table-has-header-and-delimiter", problems,
                 detail=lines
                 + ["ANTI-VACUITY: the row above would pass just as well if "
                    "md_table returned _(none)_ for EVERYTHING"])

    kv = mod.md_kv([("kept", "yes"), ("blank", ""), ("absent", None),
                    ("spaces", "   "), ("zero", 0)])
    problems = []
    problems += ["%r was rendered although its value is empty" % label
                 for label in ("blank", "absent", "spaces") if label in kv]
    problems += ["%r was dropped although it has a value" % label
                 for label in ("kept", "zero") if label not in kv]
    suite.record(GK, "kv-omits-empty-values-but-not-falsey-ones", problems,
                 detail=kv.splitlines()
                 + ["a field the server did not return says nothing, and a "
                    "row of dashes for each one buries the half that does",
                    "0 is a VALUE, not an absence, so it stays -- an emptiness "
                    "test written as `if not value` would drop it"])

    got = mod.md_kv([("a", ""), ("b", None)])
    suite.record(GK, "kv-with-nothing-left-is-the-none-marker",
                 problem_if(got.strip() != "_(none)_", "got %r" % got),
                 detail=["md_kv is md_table underneath, so the all-empty case "
                         "has to fall through to the same marker rather than "
                         "emit a two-line header"])

    problems = []
    for level, want in ((1, "# T"), (3, "### T"), (9, "###### T")):
        if mod.md_heading(level, "T") != want:
            problems.append("level %d -> %r, want %r"
                            % (level, mod.md_heading(level, "T"), want))
    suite.record(GK, "heading-levels-clamp-at-six", problems,
                 detail=["Markdown has six levels; a seventh `#` renders as a "
                         "paragraph that starts with hashes"])

    # -- get: the subcommand item 2 was actually about --------------------
    code, out, _err = render_get(mod, GET_ISSUE)
    first = out.splitlines()[0] if out.strip() else ""
    problems = []
    if code != 0:
        problems.append("cmd_get exited %r" % code)
    if first != "# %s — %s" % (GET_ISSUE["key"], PIPED):
        problems.append("heading is %r" % first)
    suite.record(GK, "get-heading-is-key-em-dash-summary", problems,
                 detail=[first,
                         "the heading is NOT a table cell, so the summary is "
                         "reproduced there exactly as Jira holds it"])

    key_hits = out.count(GET_ISSUE["key"])
    verbatim_hits = out.count(PIPED)
    fragment_hits = out.count(SUMMARY_FRAGMENT)
    problems = []
    if key_hits != 1:
        problems.append("the key appears %d time(s), want exactly 1"
                        % key_hits)
    if verbatim_hits != 1:
        problems.append("the unescaped summary appears %d time(s), want 1"
                        % verbatim_hits)
    if fragment_hits != 1:
        problems.append("%r appears %d time(s), want 1"
                        % (SUMMARY_FRAGMENT, fragment_hits))
    suite.record(GK, "get-key-and-summary-appear-exactly-once", problems,
                 detail=["key %r: %d hit(s)" % (GET_ISSUE["key"], key_hits),
                         "summary verbatim: %d hit(s); pipe-free fragment "
                         "%r: %d hit(s)" % (verbatim_hits, SUMMARY_FRAGMENT,
                                            fragment_hits),
                         "a COUNT, not a presence: the old rendering printed "
                         "both in a row AND nowhere else, and two copies of a "
                         "field are two things that can disagree",
                         "the FRAGMENT is counted too because a second, "
                         "ESCAPED copy in a table cell would not match the "
                         "verbatim summary and would slip through",
                         "the stderr summary line carries the key as well and "
                         "is deliberately not counted -- it is not part of the "
                         "document"])

    problems = []
    if not has_heading(out, "## Description"):
        problems.append("the Description section is missing")
    if "```" not in out:
        problems.append("the description body is not fenced")
    suite.record(GK, "get-description-section-is-present", problems,
                 detail=["headings: %r" % [ln for ln in out.splitlines()
                                           if ln.startswith("#")]])

    body = fenced_block(out).rstrip("\n")
    suite.record(GK, "get-description-body-is-verbatim",
                 problem_if(body != DESCRIPTION,
                            "body %r, want %r" % (body, DESCRIPTION)),
                 detail=["Jira Server answers v2 with WIKI MARKUP -- `h2.`, "
                         "`{code}`, `*bold*` -- not Markdown and not ADF",
                         "reformatting it means guessing at somebody else's "
                         "markup and being wrong silently, so it is fenced and "
                         "left exactly as it came",
                         "two of its lines start with `#`: unfenced, they "
                         "would become headings of this document"])

    code, bare, _err = render_get(mod, GET_BARE)
    problems = []
    if code != 0:
        problems.append("cmd_get exited %r" % code)
    if has_heading(bare, "## Description"):
        problems.append("an issue with no description still got the section")
    if "```" in bare:
        problems.append("an empty fenced block was emitted anyway")
    suite.record(GK, "get-description-section-is-absent-without-one", problems,
                 detail=bare.splitlines()
                 + ["an empty `## Description` claims the description is "
                    "BLANK, which is a different statement from 'there is "
                    "none'"])

    joined = "\n".join(table_rows(out))
    problems = []
    for label, want in (("components", "parser, renderer"),
                        ("labels", "regression"),
                        ("fix versions", "2026.9.0")):
        if "| %s | %s |" % (label, want) not in joined:
            problems.append("no `| %s | %s |` row" % (label, want))
    if "{" in joined or "'name'" in joined:
        problems.append("a raw object reached a table cell: %r" % joined)
    suite.record(GK, "get-arrays-are-flattened-to-their-names", problems,
                 detail=table_rows(out)
                 + ["components and fixVersions are arrays of OBJECTS, labels "
                    "is an array of STRINGS, and all three have to come out as "
                    "names rather than as [{'name': ...}]"])

    problems = []
    if "resolution" in out:
        problems.append("an unresolved issue got a `resolution` row anyway")
    problems += ["an empty %s array still produced a row" % label
                 for label in ("components", "labels", "fix versions")
                 if label in bare]
    suite.record(GK, "get-absent-fields-produce-no-row", problems,
                 detail=table_rows(bare)
                 + ["resolution is null on the full fixture and all three "
                    "arrays are empty on the bare one; none of them may become "
                    "a row of dashes"])

    # -- --json payload hygiene -------------------------------------------
    stripped = mod.strip_envelope(GET_ISSUE)
    suite.record(GK, "strip-envelope-drops-expand-and-renderedFields",
                 [("%r survived" % noise) for noise in
                  ("expand", "renderedFields") if noise in stripped],
                 detail=["kept: %r" % sorted(stripped),
                         "`expand` lists what COULD have been expanded on the "
                         "resource -- the same list for every issue, saying "
                         "nothing about any one of them",
                         "`renderedFields` is null unless the request expanded "
                         "it, and nothing here ever does"])

    problems = [("%r was dropped" % kept) for kept in
                ("self", "key", "id", "fields") if kept not in stripped]
    if stripped.get("fields") != GET_ISSUE["fields"]:
        problems.append("the fields payload was altered")
    if stripped.get("self") != GET_ISSUE["self"]:
        problems.append("self was altered: %r" % stripped.get("self"))
    suite.record(GK, "strip-envelope-preserves-self-key-id-fields", problems,
                 detail=["ANTI-VACUITY: a stripper that returned {} would "
                         "satisfy the row above perfectly"])

    suite.record(GK, "strip-envelope-returns-a-copy",
                 problem_if("expand" not in GET_ISSUE
                            or "renderedFields" not in GET_ISSUE,
                            "strip_envelope MUTATED its argument"),
                 detail=["cmd_search calls it once per issue while walking an "
                         "iterator; an in-place pop would edit the caller's "
                         "own data on the way past"])

    code, json_out, _err = render_get(mod, GET_ISSUE, tail=["--json"])
    try:
        payload = json.loads(json_out)
    except ValueError:
        payload = None
    problems = []
    if code != 0:
        problems.append("cmd_get --json exited %r" % code)
    if payload is None:
        problems.append("stdout is not one JSON document: %r" % json_out[:120])
    else:
        problems += ["%r reached the --json payload" % noise
                     for noise in ("expand", "renderedFields")
                     if noise in payload]
        problems += ["%r is missing from the --json payload" % kept
                     for kept in ("self", "key", "fields")
                     if kept not in payload]
    suite.record(GK, "get-json-payload-is-stripped", problems,
                 detail=["keys: %r" % (sorted(payload) if payload else None)])

    page = {"issues": [dict(GET_ISSUE, key="STR-7"),
                       dict(GET_ISSUE, key="STR-8")],
            "startAt": 0, "total": 2}
    client, _t = client_with(mod, [response(mod, 200, page)],
                             deployment="Server")
    args = parse_args(mod, connected(["search", "project = STR", "--json"]))
    with captured() as (out_json, _err):
        code = mod.cmd_search(args, client)
    try:
        payload = json.loads(out_json.getvalue())
    except ValueError:
        payload = None
    rows = (payload or {}).get("issues") or []
    problems = []
    if code != 0:
        problems.append("cmd_search --json exited %r" % code)
    if len(rows) != 2:
        problems.append("%d issue(s) in the payload, want 2" % len(rows))
    for row in rows:
        problems += ["%r reached issue %r" % (noise, row.get("key"))
                     for noise in ("expand", "renderedFields") if noise in row]
        problems += ["issue %r lost %r" % (row.get("key"), kept)
                     for kept in ("self", "fields") if kept not in row]
    suite.record(GK, "search-json-issues-are-stripped", problems,
                 detail=["issue keys: %r" % keys_of(rows),
                         "the two noise keys ride on EVERY issue, so here they "
                         "are not one wasted key but one per row"])

    # -- the field-list split ---------------------------------------------
    suite.record(GK, "search-fields-excludes-description",
                 problem_if("description" in mod.SEARCH_FIELDS,
                            "SEARCH_FIELDS carries description"),
                 detail=["SEARCH_FIELDS: %r" % (mod.SEARCH_FIELDS,),
                         "a 500-row search would otherwise carry 500 issue "
                         "descriptions for a column the table does not render"])

    suite.record(GK, "issue-fields-includes-components",
                 problem_if("components" not in mod.ISSUE_FIELDS,
                            "ISSUE_FIELDS is missing components"),
                 detail=["ISSUE_FIELDS: %r" % (mod.ISSUE_FIELDS,),
                         "`get` inheriting the SEARCH list is exactly how "
                         "components went missing: nobody removed it, nobody "
                         "ever asked for it"])

    lost = [f for f in mod.SEARCH_FIELDS if f not in mod.ISSUE_FIELDS]
    suite.record(GK, "issue-fields-extends-rather-than-replaces",
                 problem_if(lost, "get would lose %r" % lost),
                 detail=["splitting the list must not cost `get` a column it "
                         "already rendered"])

    client, transport = client_with(mod, [response(mod, 200, GET_ISSUE)],
                                    deployment="Server")
    args = parse_args(mod, connected(["get", "STR-7"]))
    with captured() as (_out, _err):
        mod.cmd_get(args, client)
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(transport.calls[0].url).query)
    sent = (query.get("fields") or [""])[0].split(",")
    absent = [f for f in ("components", "description", "labels", "resolution")
              if f not in sent]
    suite.record(GK, "get-actually-requests-the-issue-fields",
                 problem_if(absent, "not requested: %r" % absent),
                 detail=["fields=%s" % ",".join(sent),
                         "the constant is only half the fix -- the REQUEST has "
                         "to carry it, or components renders as absent forever "
                         "and the table is honest about the wrong thing"])

    # -- the remaining subcommands, in outline ----------------------------
    client, _t = client_with(mod, [response(mod, 200, {
        "issues": [dict(GET_ISSUE)], "startAt": 0, "total": 1})],
        deployment="Server")
    args = parse_args(mod, connected(["search", "project = STR"]))
    with captured() as (out_search, _err):
        code = mod.cmd_search(args, client)
    text = out_search.getvalue()
    rows = table_rows(text)
    problems = []
    if code != 0:
        problems.append("exit %r" % code)
    if not has_heading(text, "## Search"):
        problems.append("no `## Search` heading")
    if fenced_block(text, "jql").strip() != "project = STR":
        problems.append("the JQL is not in a fenced block: %r"
                        % fenced_block(text, "jql"))
    if not rows:
        problems.append("no table at all")
    else:
        header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        if header != ["Key", "Status", "Type", "Assignee", "Summary"]:
            problems.append("header row: %r" % header)
        if unescaped_pipes(rows[0]) != unescaped_pipes(rows[-1]):
            problems.append("the piped summary changed the column count: %r"
                            % rows[-1])
    suite.record(GK, "search-renders-the-five-column-table", problems,
                 detail=text.splitlines())

    available = [{"id": "31", "name": "Start Progress",
                  "to": {"name": "In Progress"}}]
    client, _t = client_with(mod, [response(mod, 200,
                                            {"transitions": available})],
                             deployment="Server")
    args = parse_args(mod, connected(["transitions", "STR-7"]))
    with captured() as (out_tr, _err):
        code = mod.cmd_transitions(args, client)
    text = out_tr.getvalue()
    rows = table_rows(text)
    problems = []
    if code != 0:
        problems.append("exit %r" % code)
    if not has_heading(text, "## Transitions"):
        problems.append("no `## Transitions` heading")
    if not rows:
        problems.append("no table at all")
    elif [c.strip() for c in rows[0].strip().strip("|").split("|")] \
            != ["Id", "Name", "To"]:
        problems.append("header row: %r" % rows[0])
    if "| 31 | Start Progress | In Progress |" not in text:
        problems.append("the transition row is not rendered")
    suite.record(GK, "transitions-renders-the-id-name-to-table", problems,
                 detail=text.splitlines())

    client, _t = client_with(mod, [response(mod, 200, {
        "displayName": "A Person", "accountId": "5b10a2",
        "emailAddress": EMAIL})], url=BASE_CLOUD, email=EMAIL,
        deployment="Cloud")
    args = parse_args(mod, connected(["whoami", "--email", EMAIL],
                                     url=BASE_CLOUD))
    with captured() as (out_who, _err):
        code = mod.cmd_whoami(args, client)
    text = out_who.getvalue()
    problems = []
    if code != 0:
        problems.append("exit %r" % code)
    if not has_heading(text, "## Jira connection"):
        problems.append("no `## Jira connection` heading")
    if "| Field | Value |" not in text:
        problems.append("the connection facts are not a kv table")
    if TOKEN in text:
        problems.append("THE TOKEN VALUE WAS PRINTED")
    if "value never printed" not in text:
        problems.append("the token row lost its 'value never printed' wording")
    suite.record(GK, "whoami-is-a-markdown-kv-table", problems,
                 detail=text.splitlines()
                 + ["the rendering changed; the rule that the token VALUE is "
                    "never printed did not, so it is re-asserted through the "
                    "new renderer rather than assumed to have survived"])

    # -- the NEGATIVE CONTROL for this group ------------------------------

    def naive_escaper(text):
        """Collapses newlines and leaves the pipe alone -- the live defect.

        It looks finished: every value is one line and every row is one row,
        and the table renders correctly right up until a summary contains a `|`.
        """
        return " ".join(str(text).splitlines())

    caught = check_escaper(naive_escaper)
    suite.record(GK, "control-escaper-that-ignores-the-pipe-is-rejected",
                 problem_if(not caught,
                            "the oracle ACCEPTED an escaper that lets a "
                            "separator pipe through, so it is not protecting "
                            "the table"),
                 detail=["mutant: newlines collapsed, `|` left alone",
                         "rejected with: %s" % ("; ".join(caught) or "NOTHING")])

    suite.record(GK, "control-real-escaper-passes-the-same-oracle",
                 check_escaper(mod.md_escape),
                 detail=["the MIRROR: without it, an oracle that rejected "
                         "EVERYTHING would satisfy the row above and prove "
                         "nothing"])

    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# I. hygiene
# ---------------------------------------------------------------------------

def repo_tree():
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


def group_i(suite, before, pyc_before, workspace):
    after = repo_tree()
    new = sorted(after - before)
    gone = sorted(before - after)
    suite.record(GI, "no-new-repo-paths",
                 problem_if(new, "this suite wrote into the repo tree: %s"
                            % new[:12]),
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    suite.record(GI, "no-removed-repo-paths",
                 problem_if(gone, "paths disappeared: %s" % gone[:12]))

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
    suite.record(GI, "pycache-zero", problems,
                 detail=["%d .pyc before, %d after (contract: zero)"
                         % (len(pyc_before), len(pyc_after))])

    inside = os.path.realpath(workspace).startswith(
        os.path.realpath(H.REPO_ROOT) + os.sep)
    suite.record(GI, "workspace-outside-the-repo-tree",
                 problem_if(inside, "the workspace is inside the repo: %s"
                            % workspace),
                 detail=["workspace: %s" % workspace])

    leftover = [k for k in ENV_KEYS if k in os.environ]
    suite.record(GI, "env-sandbox-left-nothing-behind", [], status=H.INFO,
                 detail=["JIRA_* still set after the run: %s"
                         % (", ".join(leftover) or "none"),
                         "INFO, not a gate: a developer machine may "
                         "legitimately export these, and EnvSandbox restores "
                         "exactly what it found"])


# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="Jira CLI: auth, URL join, deployment probe, both "
                          "paging models, config, write guards, error "
                          "mapping, Markdown rendering",
                    opts=opts, mode="grouped")

    before = repo_tree()
    pyc_before = H.pycache_snapshot()

    if not os.path.isfile(TARGET):
        suite.record(GA, "target-exists",
                     ["the CLI under test is missing: %s" % TARGET])
        suite.print_summary()
        return suite

    mod = H.load_module_from_path("jira_cli_under_test", TARGET)
    with H.TempWorkspace("ph-jira-cli-", keep=opts.keep) as workspace:
        try:
            group_a(suite, mod)
            group_b(suite, mod)
            group_c(suite, mod)
            group_d(suite, mod)
            group_e(suite, mod)
            group_f(suite, mod)
            group_g(suite, mod)
            group_h(suite, mod)
            group_j(suite, mod, workspace.path)
            group_k(suite, mod)
        finally:
            mod.reset_deployment_cache()
        # group_i LAST, always: it asserts the repo tree is exactly as this run
        # found it, so every group that could write has to have finished.
        group_i(suite, before, pyc_before, workspace.path)

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
