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

NEGATIVE CONTROL (group H) -- mandatory, explicit, named
--------------------------------------------------------
An oracle that cannot fail proves nothing about the code it blesses.  Group H
feeds the SAME oracle functions groups A/C/G use a set of deliberately BROKEN
implementations and results -- an auth helper that ignores the email, a join
built on urljoin, a pager that over-fetches, a pager that ignores an empty page,
a key validator that accepts anything -- and FAILS if any of them is accepted.
The mirror assertion (the real implementations pass those same oracles) is
recorded alongside, so a control that silently stopped running is visible.

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
                 problem_if(got != mod.DEFAULT_FIELDS,
                            "sent %r, want %r" % (got, mod.DEFAULT_FIELDS)),
                 detail=["DC's search defaults to *navigable while get-issue "
                         "defaults to *all, so naming them is the only "
                         "portable behaviour",
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
        body_text = text[text.index("{"):] if "{" in text else ""
        try:
            printed = json.loads(body_text)
        except ValueError:
            printed = None
            problems.append("the printed body is not JSON: %r" % body_text)
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
                          "paging models, config, write guards, error mapping",
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
        finally:
            mod.reset_deployment_cache()
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
