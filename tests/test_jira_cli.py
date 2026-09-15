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
  C  FOUR pagers, two of them behind ONE iterator.  Cloud pages by an opaque
     `nextPageToken` and has no total; DC pages by `startAt`/`total` and
     documents that a page may legitimately come back EMPTY and that `total` may
     move between pages.  Both stop conditions are gated for DC, both for Cloud,
     and `--limit` is checked to cap ACROSS pages (a limit honoured per page is
     the classic version of this bug).  The third is the agile
     `isLast`/`startAt` envelope behind `_paged_values`, which serves boards,
     sprints and create metadata: it is gated for walking past page one, for
     re-sending the caller's own query on every page, for stopping on an empty
     page when the envelope carries neither flag -- and for REFUSING, rather
     than growing without limit, against a server that ignores `startAt`
     altogether.  The FOURTH is `projects`, which is that same envelope
     hand-rolled a second time: it is gated against the same endless server,
     and against a `"total": true` that ends the walk after page one because
     `50 >= True` is true in Python.  The Cloud search pager is gated on the
     stop its DC sibling always had and it never did -- an EMPTY page, which
     is the only exit a server handing back a fresh token every time trips.
  D  config resolution: flag > env > unset, the source string each value
     carries, and the fail-fast that must name both the variable and its flag.
  E  the two write guards: JIRA_READ_ONLY refusing every write subcommand, and
     --dry-run printing what WOULD be sent while sending nothing.  Both are
     asserted with a call counter, not by reading the code.  --dry-run is also
     the instrument for one row that is not a write guard at all: `--started`
     reaching the wire exactly as typed, which is what makes the timestamp
     format a thing stated ONCE, in the help a caller can read, rather than
     twice with one copy unreachable.
  F  error mapping, which is the whole difference between usable and
     infuriating: an anonymous 200, a 404 that means two different things, an
     HTML login page where JSON was promised, a CAPTCHA lockout, and a retry
     ladder that must fire on 429/5xx and NEVER on another 4xx -- with a
     CEILING on the delay, because `Retry-After: 86400` is a header a server
     may legitimately send and DEFAULT_TIMEOUT governs the socket, not the
     sleep.  Three more failures live here because they all end the same way,
     as a traceback where a message was owed: an exception whose text is
     empty, an output pipe the reader closed, and an id the response did not
     carry being sent as the literal string `None`.  Two more are about a
     diagnosis that was made correctly and then thrown away.  `createmeta`
     caught every JiraError on its way to the legacy endpoint, so an auth
     refusal, a CAPTCHA lockout, a rate limit and the page bound all became a
     second request to a different URL and were finally reported against it;
     the discrimination is now an ALLOWLIST of statuses that mean "no such
     route here", and the rows assert it as zero requests to that endpoint
     rather than as an exception type, because the old behaviour raised a
     JiraError too.  And the SSO-proxy guard required a Content-Type to fire,
     so the response most likely to be a login page -- the one a bare proxy
     stripped the headers off -- was the only one that skipped it.
  G  issue-key AND board-id validation -- a local, unambiguous error instead of
     a round trip that comes back as an ambiguous 404.  A board id is the
     sharper half: it is concatenated into a REST path (api_url urlencodes the
     query and nothing else), so `1/../../api/2/issue/PROJ-1` does not fail, it
     succeeds against another resource -- and it arrives from the profile, from
     `--board`, and from the board list the server itself hands back.
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
  L  profile discovery -- the walk that decides which project's defaults a
     `create` inherits.  Its outcomes are asymmetric on purpose and each is
     gated in the direction that can do damage: no profile anywhere is FINE, a
     profile NAMED and missing is an error (naming a path is a claim that it
     exists), malformed JSON is an error that has to name the file, and the
     walk stops at $HOME rather than climbing into whatever a shared parent
     directory happens to hold.  The one-level `fields` / `aliases` merge is
     here too: a deep merge would reach inside a Jira field payload that was
     written to be sent whole.
  M  the `create` payload.  Every row here is a thing that files a ticket
     WRONG rather than not at all: profile defaults that cannot be overridden
     from the command line, an alias that resolves on one side only, a
     `NAME=VALUE` split that truncates a value at its own `=`, a system field
     sent in the wrong shape, and -- the expensive one -- `@active` guessing
     when the answer is ambiguous.  A sprint picked by guess files the work
     into the wrong sprint and reports success, so the four ambiguous board /
     sprint configurations are asserted on their REFUSAL TEXT rather than on
     an exception type, and the guessing implementation is carried as a mutant
     in group H.  One row here is the other kind: a payload Cloud will not
     take at all.  The shaping table says `assignee` is {"name": VALUE} and
     `user_ref` exists because Cloud hid `name`; both are right about their
     own half and they meet on one input, a literal name through --field.  It
     fails CLOSED, so what is gated is the refusal and the pointer to
     --field-json, and the control beside it holds `@me` and Data Center
     harmless -- a refusal keyed on the FIELD rather than on the VALUE would
     take the sentinel down with it.

NEGATIVE CONTROL (group H) -- mandatory, explicit, named
--------------------------------------------------------
An oracle that cannot fail proves nothing about the code it blesses.  Group H
feeds the SAME oracle functions groups A/C/G/M use a set of deliberately BROKEN
implementations and results -- an auth helper that ignores the email, a join
built on urljoin, a pager that over-fetches, a pager that ignores an empty page,
a key validator that accepts anything, a board-id validator that accepts a path
traversal, a sprint resolver that takes the first candidate instead of refusing
-- and FAILS if any of them is accepted.
The mirror assertion (the real implementations pass those same oracles) is
recorded alongside, so a control that silently stopped running is visible.
Groups J and K carry their own controls for the same reason, against the two
oracles that are local to them: the multipart encoder and the cell escaper.

Fixtures, such as they are, live in a `tempfile.mkdtemp()` workspace; group I
asserts the repo tree is untouched and that ZERO bytecode was written.

Groups:
  A  auth header + URL joining
  B  deployment detection: probe, cache, heuristic fallback
  C  paging: Cloud token model, DC offset model, --limit, agile envelope,
     projects
  D  configuration resolution and the fail-fast
  E  JIRA_READ_ONLY and --dry-run
  F  error mapping
  G  issue-key and board-id validation
  H  negative control
  I  hygiene
  J  attachment upload: the one non-JSON request body
  K  Markdown rendering: cell escaping, empty tables, and no duplicated fields
  L  profile discovery: the walk, its $HOME boundary, the one-level merge
  M  create payload: shaping, aliases, sentinels, and the sprint refusals

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
GG = "G. issue key + board id validation"
GH = "H. negative control"
GI = "I. hygiene"
GJ = "J. attachment upload"
GK = "K. markdown rendering"
GL = "L. profile discovery"
GM = "M. create payload"

# Every environment variable the CLI reads, in both spellings.  Cleared before
# each config case: a developer machine with a real JIRA_URL exported would
# otherwise turn these into a different test.
#
# JIRA_PROFILE is on this list for a sharper version of the same reason: it does
# not merely change what a case measures, it points `create` at a profile whose
# CONTENTS this suite does not control, and every payload case would then be
# asserting against somebody's real project defaults.
ENV_KEYS = ("JIRA_URL", "JIRA_TOKEN", "JIRA_EMAIL", "JIRA_READ_ONLY",
            "JIRA_PROFILE",
            "jira_url", "jira_token", "jira_email", "jira_read_only",
            "jira_profile")

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
def walking_from(cwd, home):
    """Run the body with the working directory and $HOME both redirected.

    The profile walk is the only thing in this CLI that reads either, and it
    compares them as STRINGS (`here == home`), so a caller must hand over two
    paths that are already realpath()ed: on macOS a `mkdtemp()` path lives
    under a symlinked `/var`, `os.getcwd()` hands back the resolved spelling,
    and an unresolved $HOME would therefore never be recognised -- the walk
    would sail straight past the boundary this group exists to pin.

    Both are restored on the way out, including the case where $HOME was not
    set at all, because group I asserts this run left the environment as it
    found it.
    """
    saved_cwd = os.getcwd()
    saved_home = os.environ.get("HOME")
    os.chdir(cwd)
    os.environ["HOME"] = home
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home


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


def agile_values(kind, start, count):
    """`count` agile entries -- one page of boards, or one page of sprints."""
    return [{"id": start + i, "name": "%s %d" % (kind, start + i)}
            for i in range(count)]


def query_of(call):
    """The recorded URL's query string, flattened to first values.

    A GET carries its parameters in the URL and not in `call.body`, so a paging
    case against a GET endpoint has to read them back off the wire the way the
    server would.
    """
    parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(call.url).query)
    return {key: values[0] for key, values in parsed.items()}


def parse_args(mod, argv):
    return mod.build_parser().parse_args(argv)


def subcommand_help(mod, name):
    """`jira.py NAME --help` as a USER would read it, or "".

    The subparser is LOCATED by walking the top-level actions for the one
    carrying a `choices` mapping, rather than reached through argparse's
    private `_subparsers` attribute, and the text comes from `format_help()`
    rather than from `action.help`: the raw help string still carries the
    `%%` argparse has not expanded yet, so a row asking what a user SEES
    would be reading the wrong copy of it.
    """
    for action in mod.build_parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and name in choices:
            return choices[name].format_help()
    return ""


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


# A board id is a NUMBER, and it is interpolated into a URL PATH -- where
# api_url does no escaping at all: it urlencodes the query and concatenates the
# path.  So `1/../../api/2/issue/PROJ-1` is not a 404, it is a request against
# another resource entirely, and the value arrives from three directions: the
# profile's "board", `--board`, and the board list the server itself hands back.
BOARD_IDS_OK = ["1", "42", "1698", 7, 1698]
BOARD_IDS_BAD = ["", None, "1/../../api/2/issue/PROJ-1", "1/sprint", "abc",
                 "12a", "-1", "1.0", " 1698 ", "1 OR 1=1", "%2e%2e", True]


def check_board_id_validator(fn):
    """Problems for a `_validate_board_id(value)` that raises on a bad id.

    Shared by the live row in group G and by the mutant in group H, for the
    same reason check_key_validator is shared: a row that blesses the real
    validator has to be passing an oracle that has been SHOWN to reject one
    which accepts anything.
    """
    problems = []
    for value in BOARD_IDS_OK:
        try:
            fn(value)
        except Exception as exc:
            problems.append("%r must be accepted (raised %s)" % (value, exc))
    for value in BOARD_IDS_BAD:
        try:
            fn(value)
        except Exception:
            continue
        problems.append("%r must be rejected, was accepted" % (value,))
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


# The board / sprint configurations `@active` has to survive.  Four of the six
# are AMBIGUOUS and the only correct answer to each is a refusal: a sprint
# chosen by guess files the work into the wrong sprint, reports success, and
# leaves nothing behind that says which decision was taken.  So the expectation
# is not "raises" -- an implementation that crashed on every input would satisfy
# that -- it is the refusal NAMING the candidates it could not choose between,
# which is the only form of the message a reader can act on.
#
#   (cid, boards, sprints, want_id, tokens the refusal must carry)
#
# `sprints=None` means the sprint endpoint must NOT be reached at all: with no
# board or with several, the question is already unanswerable, and the scripted
# transport turns a call made anyway into a failure rather than a silent extra
# round trip.
SCRUM_ONE = {"id": 1698, "name": "Platform board", "type": "scrum"}
SPRINT_ONE = {"id": 442, "name": "Sprint 12", "state": "active"}

SPRINT_SCENARIOS = [
    ("one-board-one-sprint", [SCRUM_ONE], [SPRINT_ONE], 442, ()),
    # A Kanban board on the same project has no sprints and answers the sprint
    # endpoint with a 400, so leaving it in the candidate set would manufacture
    # ambiguity where the project has exactly one board that can answer.
    ("kanban-beside-a-scrum-board-is-not-ambiguous",
     [{"id": 9, "name": "Support", "type": "kanban"}, SCRUM_ONE],
     [SPRINT_ONE], 442, ()),
    ("no-board-at-all", [], None, None, ["PROJ", "board"]),
    ("several-boards",
     [dict(SCRUM_ONE, id=10, name="Alpha"),
      dict(SCRUM_ONE, id=11, name="Beta")],
     None, None, ["10", "11", "Alpha", "Beta", "board", "@active"]),
    ("no-active-sprint", [SCRUM_ONE], [], None, ["1698", "@active"]),
    ("several-active-sprints", [SCRUM_ONE],
     [SPRINT_ONE, dict(SPRINT_ONE, id=443, name="Sprint 12b")],
     None, ["442", "443", "Sprint 12b", "@active"]),
]


def check_sprint_refusal(resolve, scenarios=SPRINT_SCENARIOS):
    """Problems for a `resolve(boards, sprints) -> int` that must REFUSE.

    Shared by the live rows in group M and by the mutant in group H, so the
    rows that bless the real resolver are passing an oracle that has been shown
    to reject one which guesses.  The unambiguous scenarios are part of the
    same table deliberately: without them a resolver that refused EVERYTHING
    would look perfect here, and `@active` would simply never work.
    """
    problems = []
    for cid, boards, sprints, want_id, tokens in scenarios:
        try:
            got = resolve(boards, sprints)
        except Exception as exc:
            if want_id is not None:
                problems.append("%s: refused (%s) where %r was unambiguous"
                                % (cid, exc, want_id))
                continue
            missing = missing_tokens(str(exc), tokens)
            if missing:
                problems.append("%s: the refusal does not name %s: %r"
                                % (cid, missing, str(exc)))
            continue
        if want_id is None:
            problems.append("%s: GUESSED %r where the only correct answer was "
                            "a refusal" % (cid, got))
        elif got != want_id:
            problems.append("%s: resolved %r, want %r" % (cid, got, want_id))
    return problems


def sprint_resolver(mod):
    """The REAL `active_sprint_id`, adapted to the oracle's signature.

    Each scenario gets its own client, because the scripted transport is the
    second half of the assertion: a scenario that scripts one response and a
    resolver that makes two requests fails here rather than quietly reading a
    board list it had no business asking for.  Both pages carry `isLast`
    because the real endpoints do, and the envelope flag is what tells a pager
    the walk is over -- a fixture that omits it is not a faithful stand-in for
    the server, it is a server that never says when it has finished.
    """
    def resolve(boards, sprints):
        script = [response(mod, 200, {"values": boards, "isLast": True})]
        if sprints is not None:
            script.append(response(mod, 200, {"values": sprints,
                                              "isLast": True}))
        client, _t = client_with(mod, script, deployment=mod.SERVER)
        return client.active_sprint_id("PROJ")
    return resolve


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

    # -- boards and sprints page too --------------------------------------
    #
    # A third pager, behind the agile `values`/`isLast` envelope rather than
    # either search model.  It fails differently and worse: a board or a sprint
    # on page two comes back as an ABSENCE rather than as an error, so the
    # caller cannot tell a short list from a complete one and `@active` either
    # refuses on an ordinary project or resolves against the wrong board.
    client, transport = client_with(mod, [
        response(mod, 200, {"values": agile_values("board", 1, 50),
                            "isLast": False}),
        response(mod, 200, {"values": agile_values("board", 51, 10),
                            "isLast": True}),
    ], deployment="Server")
    got = client.boards("PROJ")
    queries = [query_of(call) for call in transport.calls]
    starts = [q.get("startAt") for q in queries]
    projects = [q.get("projectKeyOrId") for q in queries]
    problems = []
    if len(got) != 60:
        problems.append("returned %d board(s), want 60" % len(got))
    if len(transport.calls) != 2:
        problems.append("made %d request(s), want 2" % len(transport.calls))
    if starts != ["0", "50"]:
        problems.append("startAt went %r, want ['0', '50']" % (starts,))
    if projects != ["PROJ"] * len(queries):
        problems.append("projectKeyOrId was not carried on every page: %r"
                        % (projects,))
    suite.record(GC, "boards-walks-past-one-page", problems,
                 detail=["queries: %r" % (queries,),
                         "50 boards on one project is not exotic -- every "
                         "team that ever made a personal board is on that list"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": agile_values("sprint", 1, 50),
                            "isLast": False}),
        response(mod, 200, {"values": agile_values("sprint", 51, 10),
                            "isLast": True}),
    ], deployment="Server")
    got = client.sprints(1698, "active")
    queries = [query_of(call) for call in transport.calls]
    starts = [q.get("startAt") for q in queries]
    states = [q.get("state") for q in queries]
    problems = []
    if len(got) != 60:
        problems.append("returned %d sprint(s), want 60" % len(got))
    if len(transport.calls) != 2:
        problems.append("made %d request(s), want 2" % len(transport.calls))
    if starts != ["0", "50"]:
        problems.append("startAt went %r, want ['0', '50']" % (starts,))
    if states != ["active"] * len(queries):
        problems.append("state was not carried on every page: %r" % (states,))
    suite.record(GC, "sprints-walks-past-one-page", problems,
                 detail=["queries: %r" % (queries,),
                         "the caller's query has to be re-sent on every page: "
                         "a pager that copies it once drops `state` on page "
                         "two and hands back every closed sprint on the board"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": agile_values("board", 1, 3),
                            "isLast": True}),
    ], deployment="Server")
    got = client.boards("PROJ")
    problems = []
    if len(got) != 3:
        problems.append("returned %d board(s), want 3" % len(got))
    if len(transport.calls) != 1:
        problems.append("made %d request(s), want 1" % len(transport.calls))
    suite.record(GC, "boards-single-page-makes-one-request", problems,
                 detail=["the control for the two rows above: paging must not "
                         "be bought with a gratuitous extra round trip on the "
                         "common case, which is a board list that fits"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": agile_values("board", 1, 50)}),
        response(mod, 200, {"values": []}),
    ], deployment="Server")
    got = client.boards("PROJ")
    problems = []
    if len(got) != 50:
        problems.append("returned %d board(s), want 50" % len(got))
    if len(transport.calls) != 2:
        problems.append("made %d request(s), want 2" % len(transport.calls))
    suite.record(GC, "boards-without-islast-stop-on-an-empty-page", problems,
                 detail=["neither isLast nor total came back, so the empty "
                         "page is the only stop condition left -- and a walk "
                         "that does not terminate here is invisible to the "
                         "caller, who sees a command that never returns"])

    # -- and the walk is BOUNDED, because one server shape defeats all three
    # stop conditions at once: a full page every time, no isLast, no total,
    # and `startAt` ignored.  `following` then advances on every iteration, so
    # the `following <= start` guard never fires either, and the walk grows
    # until the process is killed -- measured at 5.58 GB after 30 minutes.
    #
    # The ceiling below belongs to the TRANSPORT, not to the code under test:
    # a case that can only fail by hanging the suite is not a gate, so the
    # scripted queue runs out an order of magnitude above any legitimate bound
    # and the exception it raises is reported as "did not stop".
    ceiling = 500
    endless = response(mod, 200, {"values": agile_values("board", 1, 50)})
    client, transport = client_with(mod, [endless] * ceiling,
                                    deployment="Server")
    bound = getattr(mod, "MAX_PAGES", None)
    problems = []
    try:
        got = client.boards("PROJ")
    except Exception as exc:
        if not isinstance(exc, mod.JiraError):
            problems.append("raised %s after %d request(s) -- the walk has no "
                            "bound of its own, it stopped only because the "
                            "transport refused to answer again"
                            % (type(exc).__name__, len(transport.calls)))
        else:
            if bound is None:
                problems.append("refused, but MAX_PAGES is not declared beside "
                                "PAGE_SIZE, so the bound is a magic number")
            elif len(transport.calls) != bound:
                problems.append("made %d request(s), want MAX_PAGES (%d)"
                                % (len(transport.calls), bound))
            missing = missing_tokens(str(exc), ["boards for PROJ", "startAt"])
            if missing:
                problems.append("the refusal does not name %s: %r"
                                % (missing, str(exc)))
    else:
        problems.append("returned %d value(s) after %d request(s) instead of "
                        "refusing" % (len(got), len(transport.calls)))
    suite.record(GC, "paged-values-refuses-a-server-that-never-ends", problems,
                 detail=["requests made: %d (transport ceiling %d)"
                         % (len(transport.calls), ceiling),
                         "JiraError and not SetupError: the server answered, "
                         "and the answer is bad news -- exit 1, not exit 2",
                         "the refusal names the SUBJECT rather than the path, "
                         "because that is the string every caller already "
                         "passes and the rest of this file's errors print"])

    # -- the Cloud search pager has the same hole, reached differently -------
    #
    # `seen` advances only inside the issue loop, so a page with nothing in it
    # satisfies NONE of the three exits under it: `isLast` is absent, a token
    # is present, and it differs from the last one.  `while seen < limit` then
    # never advances and the walk never ends.  `_search_server` twenty lines
    # below already returns on an empty page; the Cloud half never did.
    #
    # The ceiling belongs to the TRANSPORT for the same reason as the row
    # above: a case that can only fail by hanging the suite is not a gate, so
    # the queue runs out far past any legitimate page count and the exception
    # it raises is reported as "did not stop".
    ceiling = 500
    client, transport = client_with(
        mod, [response(mod, 200, {"issues": [],
                                  "nextPageToken": "tok-%d" % i})
              for i in range(ceiling)],
        url=BASE_CLOUD, deployment="Cloud")
    problems = []
    try:
        got = keys_of(client.search_issues("project = CLD", limit=50))
    except Exception as exc:
        problems.append("did not stop: %s after %d request(s)"
                        % (type(exc).__name__, len(transport.calls)))
    else:
        problems += check_pages(got, transport.calls, [], 1)
    suite.record(GC, "cloud-stops-on-an-empty-page-with-a-fresh-token",
                 problems,
                 detail=["requests made: %d (transport ceiling %d)"
                         % (len(transport.calls), ceiling),
                         "an empty page is a stop condition in its own right "
                         "on BOTH deployments, and on Cloud it is the only one "
                         "a server handing back a fresh token never trips"])

    # A CONTROL, not a gate: there is no version of this code where a page
    # carrying an issue fails to advance `seen`, so it cannot be observed red.
    # It is recorded because it is the measurement behind a REFUSAL -- once an
    # empty page returns, the Cloud walk is bounded by `--limit` itself, so it
    # gets no MAX_PAGES ceiling of its own: one would refuse a legitimate
    # `--limit 10000`, and `_search_server`, the sibling the empty-page return
    # was copied from, carries no page bound for exactly that reason.
    ceiling = 200
    client, transport = client_with(
        mod, [response(mod, 200, {"issues": issues("CLD", i + 1, 1),
                                  "nextPageToken": "tok-%d" % i})
              for i in range(ceiling)],
        url=BASE_CLOUD, deployment="Cloud")
    problems = []
    try:
        got = keys_of(client.search_issues("project = CLD", limit=5))
    except Exception as exc:
        problems.append("did not stop: %s after %d request(s)"
                        % (type(exc).__name__, len(transport.calls)))
    else:
        problems += check_pages(got, transport.calls,
                                ["CLD-1", "CLD-2", "CLD-3", "CLD-4", "CLD-5"],
                                5)
    suite.record(GC, "cloud-one-issue-per-page-forever-is-bounded-by-limit",
                 problems,
                 detail=["requests made: %d (transport ceiling %d)"
                         % (len(transport.calls), ceiling),
                         "CONTROL: it cannot be red, and it is the reason the "
                         "Cloud pager is NOT given a MAX_PAGES bound -- every "
                         "surviving iteration advances `seen` by at least one"])

    # -- projects: the FOURTH pager, and it drifted from the other three -----
    #
    # The Cloud half of list_projects accumulates into a list, so the server
    # shape measured at 5.58 GB over thirty minutes against `_paged_values` is
    # the SAME shape here: a full page every time, no isLast, no total, and
    # `startAt` ignored, which leaves `following` advancing on every iteration
    # so the no-progress guard never fires either.
    ceiling = 500
    endless = response(mod, 200, {"values": [{"key": "P%d" % i, "id": i}
                                             for i in range(mod.PAGE_SIZE)]})
    client, transport = client_with(mod, [endless] * ceiling,
                                    url=BASE_CLOUD, deployment="Cloud")
    bound = getattr(mod, "MAX_PAGES", None)
    problems = []
    try:
        got = client.list_projects()
    except Exception as exc:
        if not isinstance(exc, mod.JiraError):
            problems.append("raised %s after %d request(s) -- the walk has no "
                            "bound of its own, it stopped only because the "
                            "transport refused to answer again"
                            % (type(exc).__name__, len(transport.calls)))
        else:
            if bound is not None and len(transport.calls) != bound:
                problems.append("made %d request(s), want MAX_PAGES (%d)"
                                % (len(transport.calls), bound))
            missing = missing_tokens(str(exc), ["project", "startAt"])
            if missing:
                problems.append("the refusal does not name %s: %r"
                                % (missing, str(exc)))
    else:
        problems.append("returned %d value(s) after %d request(s) instead of "
                        "refusing" % (len(got), len(transport.calls)))
    suite.record(GC, "projects-refuses-a-server-that-never-ends", problems,
                 detail=["requests made: %d (transport ceiling %d)"
                         % (len(transport.calls), ceiling),
                         "three pagers refuse this server and the fourth "
                         "grows: `while True` with an accumulator is the OOM "
                         "shape, and `projects` is the one command every "
                         "onboarding runs first"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [{"key": "P%d" % i}
                                       for i in range(mod.PAGE_SIZE)],
                            "total": True}),
        response(mod, 200, {"values": [{"key": "P50"}], "isLast": True}),
    ], url=BASE_CLOUD, deployment="Cloud")
    got = client.list_projects()
    problems = []
    if len(got) != mod.PAGE_SIZE + 1:
        problems.append("returned %d project(s), want %d"
                        % (len(got), mod.PAGE_SIZE + 1))
    if len(transport.calls) != 2:
        problems.append("made %d request(s), want 2" % len(transport.calls))
    suite.record(GC, "projects-a-boolean-total-does-not-truncate", problems,
                 detail=["`isinstance(True, int)` is True and `50 >= True` is "
                         "True, so a response carrying `\"total\": true` ends "
                         "the walk after page one -- SILENTLY, because a short "
                         "project list is indistinguishable from a complete "
                         "one at the call site",
                         "both siblings carry `and not isinstance(total, "
                         "bool)`: _paged_values and _search_server"])

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
    # `create` needs no profile fixture HERE, and that is itself the property
    # being measured: the refusal fires in main() before the handler runs, so a
    # guarded run never reaches the profile walk, never reads a file and never
    # resolves a sentinel.  Its payload cases live in group M, where a pinned
    # profile is in scope.
    "create": ["create", "--project", "PROJ", "--summary", "a new issue"],
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

    # -- one fact, written down once, in the copy a user can reach -----------
    # `--started`'s format was stated TWICE: as a module constant that a
    # full-file search finds exactly once (its own definition) and again, by
    # hand, in the argparse help.  The dead copy is deleted and the reachable
    # one kept.  The halves below that are NOT the gate are named as such,
    # because the obvious wrong repair is to make the constant reachable by
    # turning it into a validator -- and the constant's own comment argued
    # against that in as many words: a silent rewrite of a caller's timestamp
    # is worse than a server-side rejection.
    problems = problem_if(hasattr(mod, "STARTED_FORMAT"),
                          "STARTED_FORMAT is back: the format is written down "
                          "twice again, and the second copy is unreachable")
    help_text = subcommand_help(mod, "worklog")
    problems += ["the --started help omits %r" % t for t in missing_tokens(
        help_text, ["%Y-%m-%dT%H:%M:%S.%f%z",
                    "2026-08-26T09:00:00.000+0000"])]

    odd = "not-a-timestamp-at-all"
    with EnvSandbox():
        with NetworkGuard(mod) as guard:
            with captured() as (out, err):
                code = mod.main(connected(["worklog", "PROJ-1234", "3h",
                                           "--dry-run", "--started", odd]))
    printed = fenced_block(out.getvalue(), "json")
    if code != 0:
        problems.append("a --started this script does not parse became exit "
                        "%r: the deletion turned into a validator" % (code,))
    elif odd not in printed:
        problems.append("the caller's own timestamp did not reach the body "
                        "verbatim: %r" % printed)
    suite.record(GE, "the-started-format-is-written-down-once-and-it-is-"
                 "the-help", problems,
                 detail=["body: %s" % " ".join(printed.split()),
                         "GATE: the dead constant stays deleted",
                         "ANTI-VACUITY, not a gate: the help text survives the "
                         "deletion and `--started` still reaches the wire "
                         "unparsed -- validating it is a behaviour change with "
                         "a standing argument against it, not a tidy-up"])


# ---------------------------------------------------------------------------
# F. error mapping
# ---------------------------------------------------------------------------

HTML_BODY = ("<!DOCTYPE html><html><head><title>Sign in</title></head>"
             "<body>Your session has expired. Please log in again via the "
             "single sign-on portal.</body></html>")


class SprintsStub:
    """The two client methods `cmd_sprints` calls, and nothing else.

    A scripted transport cannot produce what one row below measures: every
    JiraError the real client raises carries a message, and the defect is what
    the renderer does with one that does NOT -- `str(exc).splitlines()[-1]` is
    an IndexError on `[]`.  So the exception is injected rather than provoked.
    """

    def __init__(self, boards, raises):
        self._boards = boards
        self._raises = raises
        self.sprint_calls = []

    def boards(self, _project):
        return list(self._boards)

    def sprints(self, board_id, state="active"):
        self.sprint_calls.append((board_id, state))
        raise self._raises


class BrokenPipe:
    """A stdout whose reader has gone away -- `jira.py search ... | head -1`.

    `fileno()` refuses the way a captured stdout does rather than handing back
    a real descriptor: the code under test is expected to deal with stdout
    before the interpreter flushes it, and a stub that returned this process's
    own fd would have it dup2()ed over /dev/null for the rest of the run.
    """

    def __init__(self):
        self.writes = 0

    def write(self, _text):
        self.writes += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")

    def fileno(self):
        raise io.UnsupportedOperation("fileno")


def group_f(suite, mod, workspace):
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

    # -- Retry-After is a number the SERVER picks, and nothing clamped it ----
    ceiling = getattr(mod, "MAX_RETRY_SLEEP", None)
    slept = []
    transport = FakeTransport([
        response(mod, 429, {"errorMessages": ["rate limited"]},
                 headers={"Retry-After": "86400"}),
        response(mod, 200, {"ok": True}),
    ])
    client = mod.Jira(make_cfg(mod), fetch=transport,
                      sleep=lambda s: slept.append(s))
    got = client.request("GET", "/rest/api/2/myself")
    problems = []
    if got != {"ok": True}:
        problems.append("the retry did not succeed: %r" % (got,))
    if ceiling is None:
        problems.append("there is no MAX_RETRY_SLEEP declared beside "
                        "MAX_PAGES, so any bound would be a magic number")
    if not slept:
        problems.append("the 429 was not retried at all")
    elif slept[0] > (ceiling or 0):
        problems.append("slept %r on `Retry-After: 86400`" % (slept[0],))
    suite.record(GF, "retry-after-is-clamped-to-a-named-ceiling", problems,
                 detail=["slept: %r, ceiling: %r" % (slept, ceiling),
                         "DEFAULT_TIMEOUT governs the SOCKET, never the sleep, "
                         "so an unclamped header sleeps 24 hours -- twice, "
                         "under MAX_ATTEMPTS -- and the CLI is at that point "
                         "indistinguishable from a dead connection",
                         "the two Retry-After: 7 rows above are the other half "
                         "of this: a ceiling that swallowed a legitimate delay "
                         "would fail them"])

    slept = []
    transport = FakeTransport([
        response(mod, 503, {"errorMessages": ["down"]},
                 headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        response(mod, 200, {"ok": True}),
    ])
    client = mod.Jira(make_cfg(mod), fetch=transport,
                      sleep=lambda s: slept.append(s))
    client.request("GET", "/rest/api/2/myself")
    suite.record(GF, "retry-after-as-an-http-date-still-falls-to-backoff",
                 problem_if(slept != [mod.BACKOFF_SECONDS],
                            "slept %r, want [%r]"
                            % (slept, mod.BACKOFF_SECONDS)),
                 detail=["slept: %r" % slept,
                         "CONTROL: it passes before the clamp above exists and "
                         "after, and it is here so the clamp cannot be bought "
                         "by breaking the OTHER documented header form -- "
                         "float() raises and the ValueError falls through"])

    # -- an exception carrying no message at all ----------------------------
    stub = SprintsStub([{"id": 1698, "name": "Platform"}], mod.JiraError(""))
    args = parse_args(mod, connected(["sprints", "--project", "PROJ"]))
    problems = []
    code, text = None, ""
    with EnvSandbox():
        with walking_from(os.path.realpath(workspace),
                          os.path.realpath(workspace)):
            try:
                with captured() as (out, err):
                    code = mod.cmd_sprints(args, stub)
                text = out.getvalue()
            except Exception as exc:
                problems.append("a JiraError with an EMPTY message escaped as "
                                "%s: str(exc).splitlines() is [] and [-1] "
                                "indexes it" % type(exc).__name__)
    if code is not None and code != 0:
        problems.append("exit %r, want 0" % code)
    if code is not None and "1698" not in text:
        problems.append("the board that could not answer is not in the table: "
                        "%r" % text)
    suite.record(GF, "an-error-with-no-message-does-not-become-a-traceback",
                 problems,
                 detail=text.splitlines()
                 + ["main() catches SetupError, JiraError and "
                    "KeyboardInterrupt, so an IndexError here is a traceback "
                    "and an accidental exit 1"])

    # -- the reader closed the pipe -----------------------------------------
    def answer(_method, url, _body, _headers):
        if "/serverInfo" in url:
            return response(mod, 200, {"deploymentType": "Server"})
        return response(mod, 200, {"name": "real.user",
                                   "displayName": "A Person"},
                        headers={"X-AUSERNAME": "real.user"})

    pipe = BrokenPipe()
    err = io.StringIO()
    code, problems = None, []
    mod.reset_deployment_cache()
    mod._DEPLOYMENT_CACHE = mod.SERVER
    with EnvSandbox():
        with NetworkGuard(mod, responder=answer):
            try:
                with contextlib.redirect_stdout(pipe):
                    with contextlib.redirect_stderr(err):
                        code = mod.main(connected(["whoami"]))
            except Exception as exc:
                problems.append("BrokenPipeError escaped main() as %s, so the "
                                "pipeline this file's own contract promises "
                                "gets a traceback" % type(exc).__name__)
    mod.reset_deployment_cache()
    if not pipe.writes:
        problems.append("nothing was written to stdout, so the case never "
                        "reached the defect")
    if code is not None and code != 0:
        problems.append("exit %r, want 0: a reader that stopped reading is "
                        "not a finding" % (code,))
    suite.record(GF, "a-closed-output-pipe-is-not-a-traceback", problems,
                 detail=["writes attempted: %d, exit: %r" % (pipe.writes, code),
                         "every render path calls bare print(), and `jira.py "
                         "search ... | head -1` closes the pipe under it",
                         "a clean exit also has to deal with stdout BEFORE "
                         "the interpreter flushes it, or CPython prints "
                         "\"Exception ignored in: <_io.TextIOWrapper ...>\" "
                         "after main() has already returned its code"])

    # -- an id the response did not carry, reported against the wrong thing --
    client, transport = client_with(mod, [
        response(mod, 200, {"values": [{"name": "Bug"}], "isLast": True}),
    ], deployment="Server")
    problems = []
    try:
        got = client._createmeta_split("PROJ", None)
    except mod.JiraError as exc:
        missing = missing_tokens(str(exc), ["Bug", "id"])
        if missing:
            problems.append("the refusal does not name %s: %r"
                            % (missing, str(exc)))
    except Exception as exc:
        problems.append("raised %s: %s" % (type(exc).__name__, exc))
    else:
        problems.append("returned %r instead of refusing" % (got,))
    if len(transport.calls) != 1:
        problems.append("made %d request(s): the missing id went into the URL "
                        "PATH as the literal string `None`, and the 404 that "
                        "follows is reported against the ISSUE TYPE -- a "
                        "diagnosis pointing at the wrong thing"
                        % len(transport.calls))
    suite.record(GF, "createmeta-refuses-an-issue-type-with-no-id", problems,
                 detail=["paths: %r" % [c.path for c in transport.calls],
                         "`project` on the same line IS validated; the id "
                         "beside it never was"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [{"name": "Bug", "id": "10004"}],
                            "isLast": True}),
        response(mod, 200, {"values": [{"fieldId": "summary",
                                        "name": "Summary", "required": True}],
                            "isLast": True}),
    ], deployment="Server")
    problems = []
    try:
        got = client._createmeta_split("PROJ", None)
    except Exception as exc:
        got = None
        problems.append("a well-formed issue type was refused: %s(%s)"
                        % (type(exc).__name__, exc))
    if got is not None:
        if [entry.get("issuetype") for entry in got] != ["Bug"]:
            problems.append("returned %r" % (got,))
        if len(transport.calls) != 2:
            problems.append("made %d request(s), want 2" % len(transport.calls))
        elif not transport.calls[1].path.endswith("/issuetypes/10004"):
            problems.append("the second request went to %s"
                            % transport.calls[1].path)
    suite.record(GF, "createmeta-still-follows-an-issue-type-that-has-one",
                 problems,
                 detail=["paths: %r" % [c.path for c in transport.calls],
                         "CONTROL: it passes before the refusal above exists "
                         "and after -- ANTI-VACUITY, because a refusal that "
                         "fired on every entry would satisfy that row and "
                         "break createmeta outright"])

    # -- the fallback that swallowed every finding on its way past -----------
    # `createmeta` tries the split endpoints and falls back to the legacy
    # expand call, and the fallback used to be a bare `except JiraError`.  That
    # catches everything `_decode` and `_error` raise, so a diagnosis meant for
    # the caller became a SECOND request to a different URL and was finally
    # reported against an endpoint that was never the problem.
    #
    # There is no POSITIVE tell to key on: a DC that dropped the legacy route
    # answers "Issue Does Not Exist" rather than 404, which is the whole reason
    # the fallback runs in this direction.  So the discrimination is INVERTED
    # -- an allowlist of statuses that mean "not here", and a re-raise for
    # everything else -- and the rows below assert the re-raise as ZERO
    # requests to the legacy endpoint rather than as an exception type, because
    # the bare `except` ended in a JiraError too.  It just ended in the wrong
    # one, from the wrong URL, after a round trip nobody asked for.

    def legacy_calls(transport):
        """Recorded requests to the LEGACY createmeta endpoint.

        Matched on a path ENDING at `/issue/createmeta`: the split endpoints
        live one and two segments below that, so a prefix test would count the
        very requests the fallback exists to replace.
        """
        return [c.path for c in transport.calls
                if c.path.endswith("/issue/createmeta")]

    def endless_pages(count):
        """`count` identical FULL pages: no isLast, no total, startAt ignored.

        The one server shape that defeats all three of _paged_values' stop
        conditions at once, which is the shape MAX_PAGES was written for.
        """
        page = {"values": [{"name": "Bug", "id": "10004"}] * mod.PAGE_SIZE}
        return [response(mod, 200, page)] * count

    problems = []
    client, _t = client_with(mod, [
        response(mod, 404, {"errorMessages": ["gone"]}),
    ], deployment="Server")
    try:
        client.request("GET", mod.API + "/issue/PROJ-1", subject="PROJ-1")
    except mod.JiraError as exc:
        got = getattr(exc, "status", "<no status attribute>")
        if got != 404:
            problems.append("a JiraError built from a 404 carries status %r"
                            % (got,))
    else:
        problems.append("a 404 did not raise at all")

    client, _t = client_with(mod, endless_pages(mod.MAX_PAGES),
                             deployment="Server")
    try:
        client._paged_values(mod.API + "/issue/createmeta/PROJ/issuetypes",
                             "issue types for PROJ")
    except mod.JiraError as exc:
        got = getattr(exc, "status", "<no status attribute>")
        if got is not None:
            problems.append("the page-bound refusal carries status %r, so "
                            "`status is None` is NOT what keeps it out of the "
                            "fallback" % (got,))
    else:
        problems.append("the page bound did not refuse")

    for label, kwargs in (
            ("an anonymous 200", {"payload": {"name": "x"},
                                  "headers": {"X-AUSERNAME": "anonymous"}}),
            ("an HTML body", {"body": HTML_BODY, "ctype": "text/html"})):
        client, _t = client_with(mod, [response(mod, 200, **kwargs)],
                                 deployment="Server")
        try:
            client.request("GET", mod.API + "/myself")
        except mod.JiraError as exc:
            got = getattr(exc, "status", "<no status attribute>")
            if got is not None:
                problems.append("%s carries status %r: a HEADER- or "
                                "BODY-derived diagnosis fires on any status, "
                                "including a proxy's own 404, and keying a "
                                "retry on it re-opens the hole" % (label, got))
        else:
            problems.append("%s was not diagnosed" % label)
    suite.record(GF, "a-JiraError-carries-the-status-that-caused-it", problems,
                 detail=["`status` is the HTTP status that CAUSED the error, "
                         "and None when nothing about a status did",
                         "the _decode raises are DELIBERATELY None even though "
                         "a status is in hand: each is diagnosed from a header "
                         "or a body and fires on ANY status, so a login page "
                         "served with a 404 would otherwise be read as `this "
                         "route is gone` and retried",
                         "MAX_PAGES refuses with no status at all, which is "
                         "what the fallback below reads to leave it alone"])

    problems = []
    walked_paths = []
    for status in (401, 403, 429):
        client, transport = client_with(
            mod, [response(mod, status, {"errorMessages": ["no"]})]
            * (mod.MAX_ATTEMPTS * 2), deployment="Server")
        try:
            got = client.createmeta("PROJ", None)
        except mod.JiraError as exc:
            if str(status) not in str(exc):
                problems.append("the %d escaped as %r" % (status, str(exc)))
        except Exception as exc:
            problems.append("%d raised %s: %s"
                            % (status, type(exc).__name__, exc))
        else:
            problems.append("%d came back as %r instead of reaching the "
                            "caller" % (status, got))
        retried = legacy_calls(transport)
        walked_paths.append("%d: %d request(s), legacy %r"
                            % (status, len(transport.calls), retried))
        if retried:
            problems.append("the %d was retried against the legacy endpoint "
                            "%r, so the error finally reported names THAT url"
                            % (status, retried))
    suite.record(GF, "createmeta-does-not-retry-a-401-403-or-429", problems,
                 detail=walked_paths
                 + ["a 401's CAPTCHA hint and a 429's Retry-After line are "
                    "diagnoses for the caller, and a rate-limited run that "
                    "falls back DOUBLES the request count that produced the "
                    "rate limit"])

    client, transport = client_with(mod, endless_pages(mod.MAX_PAGES + 2),
                                    deployment="Server")
    problems = []
    try:
        got = client.createmeta("PROJ", None)
    except mod.JiraError as exc:
        if "did not end after" not in str(exc):
            problems.append("what escaped is not the page bound: %r"
                            % str(exc))
    except Exception as exc:
        problems.append("raised %s: %s" % (type(exc).__name__, exc))
    else:
        problems.append("returned %r: the bound refused and the fallback "
                        "converted the refusal into a second walk" % (got,))
    retried = legacy_calls(transport)
    if retried:
        problems.append("the page bound was retried against the legacy "
                        "endpoint %r" % retried)
    if len(transport.calls) != mod.MAX_PAGES:
        problems.append("made %d request(s), want exactly MAX_PAGES (%d)"
                        % (len(transport.calls), mod.MAX_PAGES))
    suite.record(GF, "createmeta-does-not-retry-the-page-bound-refusal",
                 problems,
                 detail=["requests: %d, legacy: %r"
                         % (len(transport.calls), retried),
                         "the bound's own docstring says it refuses RATHER "
                         "THAN GROWS, and this caller was the one place that "
                         "sentence was false -- the refusal never reached a "
                         "user, it reached a second endpoint"])

    client, transport = client_with(mod, [
        response(mod, 404, {"errorMessages": ["null for uri: .../issuetypes"]}),
        response(mod, 200, {"projects": [{"issuetypes": [
            {"name": "Bug",
             "fields": {"summary": {"name": "Summary", "required": True}}}]}]}),
    ], deployment="Server")
    problems = []
    try:
        got = client.createmeta("PROJ", None)
    except Exception as exc:
        got = None
        problems.append("a 404 on the split endpoint no longer falls back: "
                        "%s(%s)" % (type(exc).__name__, exc))
    if got is not None:
        if [entry.get("issuetype") for entry in got] != ["Bug"]:
            problems.append("the legacy shape did not normalise: %r" % (got,))
        if len(legacy_calls(transport)) != 1:
            problems.append("the legacy endpoint was not reached: %r"
                            % [c.path for c in transport.calls])
    suite.record(GF, "createmeta-still-falls-back-when-the-route-is-a-404",
                 problems,
                 detail=["paths: %r" % [c.path for c in transport.calls],
                         "CONTROL: it passes before the re-raise above exists "
                         "and after -- ANTI-VACUITY, because deleting the "
                         "fallback outright would satisfy all three rows above "
                         "and break every Jira older than DC 8.4"])

    # -- the guard a header-less response walked straight past ---------------
    client, _t = client_with(mod, [
        response(mod, 200, body=HTML_BODY, ctype=None),
    ], deployment="Server")
    problems = []
    text = ""
    try:
        client.request("GET", mod.API + "/myself")
    except mod.JiraError as exc:
        text = str(exc)
        problems += ["the diagnosis omits %r" % t for t in missing_tokens(
            text, ["SSO proxy", "context path"])]
    except Exception as exc:
        problems.append("raised %s: %s" % (type(exc).__name__, exc))
    else:
        problems.append("an HTML login page with no Content-Type decoded "
                        "cleanly")
    suite.record(GF, "an-untyped-HTML-body-is-still-diagnosed", problems,
                 detail=[text or "<no error>"]
                 + ["the guard used to require a Content-Type (`and ctype`), "
                    "so the response most likely to BE a login page -- the one "
                    "a bare proxy stripped the headers off -- was the one that "
                    "skipped it and reached json.loads, which is the \"syntax "
                    "error at character 0\" the guard exists to replace"])

    problems = []
    for label, raw in (("an object", "{\"name\": \"jdoe\"}"),
                       ("an array", "[{\"name\": \"jdoe\"}]"),
                       ("leading whitespace", "\n  {\"name\": \"jdoe\"}")):
        client, _t = client_with(mod, [response(mod, 200, body=raw,
                                                ctype=None)],
                                 deployment="Server")
        try:
            got = client.request("GET", mod.API + "/myself")
        except Exception as exc:
            problems.append("%s with no Content-Type was refused: %s(%s)"
                            % (label, type(exc).__name__, exc))
        else:
            if not got:
                problems.append("%s decoded to %r" % (label, got))
    suite.record(GF, "an-untyped-JSON-body-is-still-decoded", problems,
                 detail=["CONTROL: it passes before the guard above is "
                         "widened and after -- ANTI-VACUITY, because simply "
                         "dropping the `and ctype` term would satisfy that row "
                         "while refusing a DC behind a minimal proxy, which is "
                         "a configuration rather than a hypothetical",
                         "so when the header is absent the BODY decides: what "
                         "does not open with `{` or `[` was never JSON"])

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


def group_g(suite, mod, workspace):
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

    # -- the board id: a number that lands in a URL PATH ---------------------
    validator = getattr(mod, "_validate_board_id", None)
    if validator is None:
        problems = ["there is no _validate_board_id at all, so nothing checks "
                    "the one caller-supplied value that reaches a REST path "
                    "with neither a regex nor an escape in front of it"]
    else:
        problems = check_board_id_validator(validator)
    suite.record(GG, "board-id-accepted-and-rejected-tables", problems,
                 detail=["accepted: %r" % (BOARD_IDS_OK,),
                         "rejected: %r" % (BOARD_IDS_BAD,),
                         "api_url urlencodes the QUERY and concatenates the "
                         "PATH, so `1/../../api/2/issue/PROJ-1` does not fail "
                         "-- it succeeds against another resource"])

    hostile = "1/../../api/2/issue/PROJ-1"
    code, text = None, ""
    problems = []
    with EnvSandbox():
        with walking_from(os.path.realpath(workspace),
                          os.path.realpath(workspace)):
            with NetworkGuard(mod) as guard:
                try:
                    with captured() as (out, err):
                        code = mod.main(connected(["sprints", "--board",
                                                   hostile]))
                    text = err.getvalue()
                except Exception as exc:
                    problems.append("the network was used before the id was "
                                    "looked at: %s" % exc)
    if code is not None and code != 2:
        problems.append("exit %r, want 2 -- a board id that cannot be one is a "
                        "bad invocation" % (code,))
    if guard.calls:
        problems.append("%d request(s) were made for a malformed board id"
                        % guard.calls)
    problems += ["message omits %r" % t for t in missing_tokens(
        text, ["invalid board id", hostile])]
    suite.record(GG, "sprints-rejects-a-bad-board-offline", problems,
                 detail=[text.strip(), "requests: %d" % guard.calls,
                         "`--board` has no type=int on the parser, so whatever "
                         "was typed is what goes into the path"])

    client, transport = client_with(mod, [], deployment=mod.SERVER)
    problems = []
    try:
        got = mod.resolve_sentinels(client, "PROJ", {"board": hostile},
                                    {"customfield_11300": "@active"})
    except mod.SetupError as exc:
        missing = missing_tokens(str(exc), ["invalid board id", hostile])
        if missing:
            problems.append("the refusal does not name %s: %r"
                            % (missing, str(exc)))
    except Exception as exc:
        problems.append("raised %s, want SetupError: %s"
                        % (type(exc).__name__, exc))
    else:
        problems.append("resolved to %r" % (got,))
    if transport.calls:
        problems.append("the board id went into the URL PATH of %d request(s): "
                        "%s" % (len(transport.calls), transport.calls[0].path))
    suite.record(GG, "a-profile-board-that-is-not-a-number-is-refused",
                 problems,
                 detail=["paths: %r" % [c.path for c in transport.calls],
                         "`.claude/jira.json` is a checked-in file that this "
                         "CLI reads out of whatever repository it is standing "
                         "in, and its \"board\" reaches the path unread"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [{"id": hostile, "name": "Evil",
                                        "type": "scrum"}], "isLast": True}),
    ], deployment=mod.SERVER)
    problems = []
    try:
        got = client.active_sprint_id("PROJ")
    except mod.SetupError as exc:
        if "invalid board id" not in str(exc):
            problems.append("the refusal does not name the cause: %r"
                            % str(exc))
    except Exception as exc:
        problems.append("raised %s, want SetupError -- anything else escapes "
                        "main() as a traceback: %s" % (type(exc).__name__, exc))
    else:
        problems.append("resolved to %r" % (got,))
    if len(transport.calls) != 1:
        problems.append("made %d request(s), want 1 (the board list, and "
                        "nothing built from what it said)"
                        % len(transport.calls))
    suite.record(GG, "a-board-id-the-server-handed-back-is-checked-too",
                 problems,
                 detail=["paths: %r" % [c.path for c in transport.calls],
                         "SetupError and not JiraError: the remedy is the same "
                         "one the four refusals beside it name -- pin a board "
                         "in the profile -- and splitting one refusal family "
                         "across two exit codes buys the caller nothing"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [{"id": hostile, "name": "Evil"},
                                       {"id": 1698, "name": "Platform"}],
                            "isLast": True}),
        response(mod, 200, {"values": [SPRINT_ONE], "isLast": True}),
    ], deployment=mod.SERVER)
    args = parse_args(mod, connected(["sprints", "--project", "PROJ"]))
    code, text = None, ""
    problems = []
    with EnvSandbox():
        with walking_from(os.path.realpath(workspace),
                          os.path.realpath(workspace)):
            try:
                with captured() as (out, err):
                    code = mod.cmd_sprints(args, client)
                text = out.getvalue()
            except Exception as exc:
                problems.append("the sweep put the id in a URL: %s" % exc)
    if code is not None and code != 0:
        problems.append("exit %r, want 0" % (code,))
    if len(transport.calls) != 2:
        problems.append("made %d request(s), want 2: the board list, then the "
                        "ONE board that can be asked" % len(transport.calls))
    elif "/board/1698/sprint" not in transport.calls[1].path:
        problems.append("the second request went to %s"
                        % transport.calls[1].path)
    if code is not None:
        problems += ["the table omits %r" % t for t in missing_tokens(
            text, ["invalid board id", "442"])]
    suite.record(GG, "sprints-skips-a-server-board-whose-id-is-not-a-number",
                 problems,
                 detail=text.splitlines() + ["paths: %r"
                                             % [c.path
                                                for c in transport.calls],
                                             "a row rather than a refusal, for "
                                             "the same reason the Kanban 400 "
                                             "is a row: one board that cannot "
                                             "answer must not hide every board "
                                             "listed after it"])


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


def _mutant_board_validator(_board_id):
    """Accepts anything -- which is what interpolating it raw looks like."""
    return None


def _mutant_active_sprint(boards, sprints):
    """Takes the first candidate instead of refusing.

    This is the defect in its natural form -- nobody writes "guess", they write
    `[0]` -- and it is invisible from the outside: the create succeeds, the
    ticket exists, and it is in the wrong sprint.
    """
    if not boards:
        return 0
    return int((sprints or [{"id": 0}])[0].get("id") or 0)


MUTANTS = [
    ("mutant-auth-ignores-the-email", lambda: check_auth(_mutant_auth),
     "always Bearer, so a Cloud account silently authenticates as nobody"),
    ("mutant-join-uses-urljoin", lambda: check_join(_mutant_join),
     "urljoin drops the /jira context path"),
    ("mutant-key-validator-accepts-anything",
     lambda: check_key_validator(_mutant_key_validator),
     "no validation at all"),
    ("mutant-board-validator-accepts-anything",
     lambda: check_board_id_validator(_mutant_board_validator),
     "no board-id validation at all, so `1/../../api/2/issue/PROJ-1` is "
     "concatenated into the URL path and re-points the request"),
    ("mutant-pager-over-fetches",
     lambda: check_pages(["A-1", "A-2", "A-3"], [1, 2, 3], ["A-1", "A-2"], 2),
     "one page too many: right-looking issues, one extra round trip"),
    ("mutant-pager-ignores-an-empty-page",
     lambda: check_pages(["A-1"], [1, 2, 3, 4], ["A-1"], 2),
     "kept paging past the empty page; the issue list alone cannot see it"),
    ("mutant-sprint-resolver-takes-the-first",
     lambda: check_sprint_refusal(_mutant_active_sprint),
     "picks candidate [0] rather than refusing, which files the work into the "
     "wrong sprint and reports success"),
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
    problems += ["board: %s" % p
                 for p in check_board_id_validator(
                     getattr(mod, "_validate_board_id", lambda _v: None))]
    problems += ["pages: %s" % p
                 for p in check_pages(["A-1", "A-2"], [1, 2], ["A-1", "A-2"], 2)]
    problems += ["sprint: %s" % p
                 for p in check_sprint_refusal(sprint_resolver(mod))]
    suite.record(GH, "control-real-implementations-pass-the-same-oracles",
                 problems,
                 detail=["an oracle that rejects EVERYTHING would satisfy the "
                         "mutant rows above and prove nothing"])
    mod.reset_deployment_cache()


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
# L. profile discovery: the walk, its $HOME boundary, and the one-level merge
# ---------------------------------------------------------------------------

def setup_error(mod, call):
    """The SetupError text raised by `call`, or None if it did not raise.

    Only SetupError is caught, deliberately.  "It raised something" is not the
    assertion any of these rows want -- a TypeError from a typo in the fixture
    would satisfy it -- and exit code 2 is reached from SetupError and from
    nothing else.
    """
    try:
        call()
    except mod.SetupError as exc:
        return str(exc)
    return None


def walk_tree(workspace, name):
    """(root, home, cwd) for one profile-walk case, all three realpath()ed.

    Every case gets its OWN tree.  They differ only in where a profile sits,
    and one shared tree would leave each case depending on which of the others
    had already run -- the walk reads the filesystem, so a leftover fixture two
    directories up is indistinguishable from the thing under test.

    `root` is the directory ABOVE the fake $HOME, which is where the rows that
    pin the boundary put their bait.
    """
    root = os.path.realpath(os.path.join(workspace, "walk", name))
    home = os.path.join(root, "home")
    cwd = os.path.join(home, "work", "repo")
    os.makedirs(cwd, exist_ok=True)
    return root, home, cwd


def put_profile(directory, body):
    """Write `.claude/jira.json` under `directory`; return its path.

    `body` is written verbatim when it is a string, so a row can plant a file
    that is NOT valid JSON.
    """
    path = os.path.join(directory, ".claude", "jira.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))
    return path


def walked(mod, cwd, home, explicit=None, env_profile=None):
    """`load_profile` reached the way the CLI reaches it: cwd, $HOME and env.

    JIRA_PROFILE is cleared by EnvSandbox unless a row sets it, which matters
    more here than anywhere else in this suite: an exported one would point
    every case at a real project's defaults and quietly pass.
    """
    with walking_from(cwd, home):
        with EnvSandbox(JIRA_PROFILE=env_profile):
            return mod.load_profile(explicit)


MERGE_DEFAULTS = {
    "issuetype": "Task",
    "board": 1,
    "aliases": {"epic": "customfield_11800"},
    "fields": {"priority": {"name": "Moderate"},
               "components": [{"name": "Shared"}]},
}

MERGE_PROJECT = {
    "issuetype": "Bug",
    "aliases": {"sprint": "customfield_11300"},
    "fields": {"components": [{"name": "Parser"}], "assignee": "@me"},
}


def group_l(suite, mod, workspace):
    # -- where the walk looks ---------------------------------------------
    root, home, cwd = walk_tree(workspace, "cwd")
    want = put_profile(cwd, {"project": "HERE"})
    data, path = walked(mod, cwd, home)
    problems = []
    if path != want:
        problems.append("found %r, want %r" % (path, want))
    if data.get("project") != "HERE":
        problems.append("loaded %r" % data)
    suite.record(GL, "profile-found-in-the-working-directory", problems,
                 detail=["found: %s" % path])

    root, home, cwd = walk_tree(workspace, "ancestor")
    want = put_profile(os.path.dirname(cwd), {"project": "ABOVE"})
    data, path = walked(mod, cwd, home)
    suite.record(GL, "profile-found-in-an-ancestor",
                 problem_if(path != want, "found %r, want %r" % (path, want)),
                 detail=["cwd  : %s" % cwd, "found: %s" % path,
                         "the walk is what makes the defaults depend on which "
                         "repository you are standing in"])

    root, home, cwd = walk_tree(workspace, "nearest")
    put_profile(home, {"project": "FAR"})
    want = put_profile(cwd, {"project": "NEAR"})
    data, path = walked(mod, cwd, home)
    problems = []
    if path != want:
        problems.append("found %r, want the nearer %r" % (path, want))
    if data.get("project") != "NEAR":
        problems.append("loaded the farther profile: %r" % data)
    suite.record(GL, "the-nearest-profile-wins", problems,
                 detail=["a repository's own profile has to beat the one in "
                         "the checkout above it, or a monorepo's outermost "
                         "project would file everything"])

    # -- absent is fine; NAMED and absent is not --------------------------
    root, home, cwd = walk_tree(workspace, "absent")
    problems = []
    text = setup_error(mod, lambda: walked(mod, cwd, home))
    if text is not None:
        problems.append("no profile anywhere was treated as an error: %s"
                        % text)
    else:
        data, path = walked(mod, cwd, home)
        if (data, path) != ({}, None):
            problems.append("returned %r / %r, want {} / None" % (data, path))
    suite.record(GL, "no-profile-anywhere-is-not-an-error", problems,
                 detail=["`create --project PROJ --summary ...` has to work in "
                         "a checkout that never wrote a profile"])

    missing = os.path.join(cwd, "nowhere", "jira.json")
    text = setup_error(mod, lambda: walked(mod, cwd, home, explicit=missing))
    problems = []
    if text is None:
        problems.append("a --profile that does not exist was accepted")
    else:
        problems += ["the error does not name %r" % t
                     for t in missing_tokens(text, [missing])]
    suite.record(GL, "a-named-profile-that-is-missing-is-an-error", problems,
                 detail=[text or "<no error>",
                         "naming a path is a claim that it exists; falling "
                         "back to no defaults would file half a ticket and "
                         "report success"])

    text = setup_error(mod,
                       lambda: walked(mod, cwd, home, env_profile=missing))
    suite.record(GL, "a-JIRA_PROFILE-that-is-missing-is-an-error",
                 problem_if(text is None,
                            "an env-named profile that does not exist was "
                            "accepted"),
                 detail=[text or "<no error>",
                         "same claim, made in the environment instead of on "
                         "the command line"])

    # -- the $HOME boundary ------------------------------------------------
    root, home, cwd = walk_tree(workspace, "above-home")
    bait = put_profile(root, {"project": "ABOVE-HOME"})
    data, path = walked(mod, cwd, home)
    problems = []
    if path is not None:
        problems.append("the walk climbed past $HOME and found %r" % path)
    if data:
        problems.append("it loaded %r" % data)
    suite.record(GL, "the-walk-stops-at-HOME", problems,
                 detail=["$HOME: %s" % home, "bait : %s" % bait,
                         "a profile above $HOME belongs to no project, and on "
                         "a shared machine it belongs to no one in particular"])

    root, home, cwd = walk_tree(workspace, "at-home")
    want = put_profile(home, {"project": "AT-HOME"})
    data, path = walked(mod, cwd, home)
    suite.record(GL, "the-HOME-boundary-is-inclusive",
                 problem_if(path != want, "found %r, want %r" % (path, want)),
                 detail=["$HOME itself is CHECKED and then the walk stops, so "
                         "a personal default works and the directory above it "
                         "does not"])

    # -- precedence --------------------------------------------------------
    root, home, cwd = walk_tree(workspace, "precedence")
    put_profile(cwd, {"project": "WALK"})
    env_path = os.path.join(root, "from-env.json")
    flag_path = os.path.join(root, "from-flag.json")
    for target, marker in ((env_path, "ENV"), (flag_path, "FLAG")):
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"project": marker}))
    data, path = walked(mod, cwd, home, env_profile=env_path)
    problems = problem_if(data.get("project") != "ENV",
                          "JIRA_PROFILE lost to the walk: %r" % data)
    data, path = walked(mod, cwd, home, explicit=flag_path,
                        env_profile=env_path)
    problems += problem_if(data.get("project") != "FLAG",
                           "--profile lost to JIRA_PROFILE: %r" % data)
    suite.record(GL, "flag-beats-env-beats-the-walk", problems,
                 detail=["--profile > JIRA_PROFILE > .claude/jira.json"])

    # -- a file that is there but is not a profile -------------------------
    root, home, cwd = walk_tree(workspace, "malformed")
    bad = put_profile(cwd, "{\"project\": \"PROJ\",}")
    text = setup_error(mod, lambda: walked(mod, cwd, home))
    problems = []
    if text is None:
        problems.append("malformed JSON was accepted")
    else:
        problems += ["the error does not name %r" % t
                     for t in missing_tokens(text, [bad])]
    suite.record(GL, "malformed-json-is-an-error-that-names-the-file",
                 problems,
                 detail=[text or "<no error>",
                         "the file is named because a walk found it and the "
                         "reader has no other way to know WHICH one it was"])

    root, home, cwd = walk_tree(workspace, "not-an-object")
    put_profile(cwd, "[1, 2, 3]")
    text = setup_error(mod, lambda: walked(mod, cwd, home))
    suite.record(GL, "a-json-array-is-not-a-profile",
                 problem_if(text is None,
                            "a top-level JSON array was accepted as a "
                            "profile"),
                 detail=[text or "<no error>"])

    # -- the measured divergence, recorded rather than gated ---------------
    root, home, cwd = walk_tree(workspace, "outside-home")
    outside = os.path.join(root, "outside", "repo")
    os.makedirs(outside, exist_ok=True)
    planted = put_profile(os.path.dirname(outside), {"project": "OUTSIDE"})
    data, path = walked(mod, outside, home)
    suite.record(GL, "walk-from-a-cwd-outside-HOME", [], status=H.INFO,
                 detail=["cwd   : %s" % outside,
                         "$HOME : %s" % home,
                         "found : %s" % path,
                         "planted: %s" % planted,
                         "MEASURED, not gated.  `_profile_path` in jira.py -- "
                         "NAMED, never pinned by line number, because the one "
                         "this row used to cite had already drifted twice "
                         "before anyone read it back -- stops the walk on "
                         "`here == home` OR at the filesystem root, so the "
                         "$HOME boundary only binds when the cwd is under "
                         "$HOME; from anywhere else the walk climbs to `/`.  "
                         "The docstring there and SKILL.md both say the walk "
                         "stops at $HOME without that qualifier.  Recorded as "
                         "INFO because deciding which of the two is wrong is a "
                         "change to the CLI, not to its tests."])

    # -- the merge ---------------------------------------------------------
    profile = {"defaults": dict(MERGE_DEFAULTS),
               "projects": {"PROJ": dict(MERGE_PROJECT)}}
    block = mod.project_profile(profile, "PROJ")
    problems = []
    if block.get("issuetype") != "Bug":
        problems.append("the project block did not override issuetype: %r"
                        % block.get("issuetype"))
    if block.get("board") != 1:
        problems.append("a defaults-only key was dropped: %r" % block)
    if block.get("aliases") != {"epic": "customfield_11800",
                                "sprint": "customfield_11300"}:
        problems.append("aliases did not merge key by key: %r"
                        % block.get("aliases"))
    if block.get("fields") != {"priority": {"name": "Moderate"},
                               "components": [{"name": "Parser"}],
                               "assignee": "@me"}:
        problems.append("fields did not merge key by key: %r"
                        % block.get("fields"))
    suite.record(GL, "defaults-merge-under-the-project-block", problems,
                 detail=["a key the project block does not mention survives; "
                         "one it does is replaced"])

    problems = []
    if block["fields"]["components"] != [{"name": "Parser"}]:
        problems.append("a field VALUE was merged instead of replaced: %r"
                        % block["fields"]["components"])
    if MERGE_DEFAULTS["fields"]["components"] != [{"name": "Shared"}]:
        problems.append("the merge mutated the profile it read: %r"
                        % MERGE_DEFAULTS)
    suite.record(GL, "the-merge-is-one-level-and-never-reaches-into-a-value",
                 problems,
                 detail=["`components` is a Jira field payload written to be "
                         "sent WHOLE; a deep merge would union two arrays that "
                         "each meant `these and only these`"])

    block = mod.project_profile(profile, "OTHER")
    problems = []
    if block.get("issuetype") != "Task":
        problems.append("an unnamed project did not fall back to defaults: %r"
                        % block)
    if block.get("fields", {}).get("components") != [{"name": "Shared"}]:
        problems.append("it inherited the PROJ block: %r" % block)
    suite.record(GL, "an-unnamed-project-gets-only-the-defaults", problems)

    # -- which project ------------------------------------------------------
    problems = []
    if mod._profile_project({"project": "PROJ"}, None) != "PROJ":
        problems.append("the profile's own project was ignored")
    if mod._profile_project({"project": "PROJ"}, "OTHER") != "OTHER":
        problems.append("--project lost to the profile")
    text = setup_error(mod, lambda: mod._profile_project({}, None))
    if text is None:
        problems.append("a create with no project anywhere was accepted")
    elif ".claude" not in text:
        problems.append("the refusal does not say where a project is named: "
                        "%r" % text)
    suite.record(GL, "the-project-comes-from-the-flag-then-the-profile",
                 problems, detail=[text or "<no error>"])

    problems = []
    for key in ("proj", "PROJ-1", "1PROJ", "", "P", "PR OJ", "PROJ/../OTHER"):
        if setup_error(mod,
                       lambda k=key: mod._profile_project({}, k)) is None:
            problems.append("%r was accepted as a project key" % key)
    for key in ("PROJ", "STR", "AB1", "X9Y"):
        text = setup_error(mod, lambda k=key: mod._profile_project({}, k))
        if text is not None:
            problems.append("%r was rejected: %s" % (key, text))
    suite.record(GL, "the-project-key-is-validated-locally", problems,
                 detail=["the same reason as an issue key -- a project you may "
                         "not create in answers 404 exactly like one that was "
                         "never there -- and it is the only caller-supplied "
                         "text that reaches a REST path unescaped"])


# ---------------------------------------------------------------------------
# M. the create payload: shaping, aliases, sentinels, and the sprint refusals
# ---------------------------------------------------------------------------

class RoutedTransport:
    """Like FakeTransport, but answers by URL substring instead of position.

    Sentinel resolution walks the payload in the order its keys happen to sit
    in, so a positional queue would pin THAT order: reordering two fields in a
    profile fixture would then fail a case about resolution depth.  Routing by
    URL keeps each case measuring the thing it is named after.

    Routes are tried in order, so the specific one goes first -- `/board` is a
    prefix of `/board/1698/sprint`.
    """

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def __call__(self, method, url, body, headers):
        decoded = json.loads(body.decode("utf-8")) if body else None
        self.calls.append(Call(method, url, decoded, dict(headers)))
        for needle, item in self.routes:
            if needle in url:
                return item(method, url) if callable(item) else item
        raise AssertionError("unrouted request: %s %s" % (method, url))

    @property
    def methods(self):
        return [call.method for call in self.calls]


def routed_client(mod, routes, url=BASE_DC, email=None, deployment=None):
    mod.reset_deployment_cache()
    if deployment is not None:
        mod._DEPLOYMENT_CACHE = deployment
    transport = RoutedTransport(routes)
    return mod.Jira(make_cfg(mod, url=url, email=email), fetch=transport,
                    sleep=lambda _seconds: None), transport


def create_args(mod, *tail):
    """Parsed `create` arguments, with the connection flags appended."""
    return parse_args(mod, connected(["create"] + list(tail)))


def created_fields(mod, block, *tail, **kwargs):
    """The `fields` object `create` would send, for a given profile block.

    A client is CONSTRUCTED rather than defaulted away.  `_create_fields` has
    to ask the deployment before it can let a literal user name through the
    shaping table, and a parameter that could be omitted would make that
    question skippable -- which is the same silent bypass the Cloud row below
    exists to gate.  `Server` is the default because it is the deployment
    every other row in this group is measured against, and because it is the
    one where the table's answer is right.

    The transport is EMPTY: nothing here may reach it, and an unscripted
    request raises rather than answering.
    """
    client, _transport = client_with(
        mod, [], deployment=kwargs.pop("deployment", None) or mod.SERVER)
    if kwargs:
        raise TypeError("unexpected: %r" % sorted(kwargs))
    return mod._create_fields(create_args(mod, *tail), block, "PROJ", client)


# A create payload's shape is decided by the FIELD and not by the value, so the
# expectation is a table too.  Every row is a 400 waiting to happen if the
# table and the server disagree -- and a 400 that blames the field rather than
# the shaping.
SHAPE_CASES = [
    # system fields addressed by an object with a `name`
    ("issuetype", "Bug", {"name": "Bug"}),
    ("priority", "Moderate", {"name": "Moderate"}),
    ("assignee", "jdoe", {"name": "jdoe"}),
    ("reporter", "jdoe", {"name": "jdoe"}),
    # system fields that are ARRAYS of those objects, comma-separated on the
    # command line and trimmed, because `a, b` is what a human types
    ("components", "Parser", [{"name": "Parser"}]),
    ("components", "Parser, Render", [{"name": "Parser"}, {"name": "Render"}]),
    ("fixVersions", "8.14.2", [{"name": "8.14.2"}]),
    ("versions", "8.14.2,8.15.0", [{"name": "8.14.2"}, {"name": "8.15.0"}]),
    # the one system field that is an array of BARE STRINGS
    ("labels", "regression", ["regression"]),
    ("labels", "regression, parser", ["regression", "parser"]),
    # anything else falls through untouched: its shape is per-instance
    ("customfield_11800", "PROJ-99", "PROJ-99"),
    ("description", "free text", "free text"),
    ("summary", "free text", "free text"),
    # a sentinel is ALWAYS a bare string leaving the table, whatever field it
    # names -- the shaping happens after it resolves, in the shape the
    # deployment wants, or it would be wrapped twice
    ("assignee", "@me", "@me"),
    ("customfield_11300", "@active", "@active"),
]

CLOUD_ME = {"accountId": "5b10a2c8", "name": "hidden-on-cloud",
            "displayName": "A Person"}
DC_ME = {"name": "jdoe", "key": "jdoe", "displayName": "A Person"}


def group_m(suite, mod, workspace):
    # -- the payload: profile first, command line laid over it -------------
    block = {"issuetype": "Bug",
             "fields": {"priority": {"name": "Moderate"},
                        "components": [{"name": "Parser"}]}}
    fields = created_fields(mod, block, "--summary", "drops the Foo header",
                            "--field", "priority=Critical")
    want = {"components": [{"name": "Parser"}],
            "priority": {"name": "Critical"},
            "project": {"key": "PROJ"},
            "issuetype": {"name": "Bug"},
            "summary": "drops the Foo header"}
    suite.record(GM, "profile-defaults-lie-under-the-command-line",
                 problem_if(fields != want, "built %r, want %r"
                            % (fields, want)),
                 detail=["a default that could not be overridden would be a "
                         "cage rather than a default, and `--field` is the "
                         "only way to say `this one issue is different`"])

    fields = created_fields(mod, block, "--summary", "s", "--type", "Task")
    suite.record(GM, "the-type-flag-beats-the-profile-issuetype",
                 problem_if(fields.get("issuetype") != {"name": "Task"},
                            "issuetype is %r" % fields.get("issuetype")))

    fields = created_fields(mod, block, "--summary", "s",
                            "--description", "a body")
    problems = problem_if(fields.get("description") != "a body",
                          "description is %r" % fields.get("description"))
    bare = created_fields(mod, block, "--summary", "s")
    problems += problem_if("description" in bare,
                           "an unset --description still sent a key: %r"
                           % bare.get("description"))
    suite.record(GM, "an-absent-description-is-not-sent-as-empty", problems,
                 detail=["an empty string is a VALUE: it would clear whatever "
                         "the project's create screen defaults the field to"])

    # -- aliases ------------------------------------------------------------
    aliased = {"aliases": {"epic": "customfield_11800",
                           "sprint": "customfield_11300"},
               "fields": {"epic": "PROJ-1"}}
    fields = created_fields(mod, aliased, "--summary", "s")
    problems = []
    if fields.get("customfield_11800") != "PROJ-1":
        problems.append("a profile key was not resolved: %r" % fields)
    if "epic" in fields:
        problems.append("the alias was sent as a field name: %r" % fields)
    fields = created_fields(mod, aliased, "--summary", "s",
                            "--field", "epic=PROJ-99")
    if fields.get("customfield_11800") != "PROJ-99":
        problems.append("--field did not resolve the alias: %r" % fields)
    if "epic" in fields:
        problems.append("--field sent the alias as a field name: %r" % fields)
    suite.record(GM, "aliases-resolve-on-both-sides", problems,
                 detail=["profile keys AND --field, or the same word would "
                         "mean two things one line apart",
                         "the per-instance ids then live in one block that a "
                         "single `fields --grep` run can refresh"])

    # -- NAME=VALUE ---------------------------------------------------------
    fields = created_fields(mod, {}, "--summary", "s",
                            "--field", "customfield_12000=a=b=c",
                            "--field", "description=k=v&x=y",
                            "--field", "customfield_12001=")
    problems = []
    if fields.get("customfield_12000") != "a=b=c":
        problems.append("a value containing `=` was truncated: %r"
                        % fields.get("customfield_12000"))
    if fields.get("description") != "k=v&x=y":
        problems.append("the description was truncated: %r"
                        % fields.get("description"))
    if fields.get("customfield_12001") != "":
        problems.append("an empty value did not survive: %r"
                        % fields.get("customfield_12001"))
    suite.record(GM, "NAME=VALUE-splits-at-the-FIRST-equals", problems,
                 detail=["a field name cannot contain `=` and a value very "
                         "much can -- a URL, a query string, a base64 blob",
                         "split() would have sent `a` where `a=b=c` was meant "
                         "and the server would have accepted it"])

    problems = []
    for raw in ("epic", "=value", "  =value"):
        text = setup_error(mod, lambda r=raw: created_fields(
            mod, {}, "--summary", "s", "--field", r))
        if text is None:
            problems.append("%r was accepted as NAME=VALUE" % raw)
    suite.record(GM, "a-field-without-a-name-is-refused", problems,
                 detail=["exit 2 before anything is sent, not a 400 about a "
                         "field called `epic` with no value"])

    # -- the shaping table --------------------------------------------------
    problems = []
    for field, value, want_shape in SHAPE_CASES:
        got = mod._shape(field, value)
        if got != want_shape:
            problems.append("%s=%s -> %r, want %r"
                            % (field, value, got, want_shape))
    suite.record(GM, "the-system-field-shaping-table", problems,
                 detail=["%d row(s): object fields, array fields, the one list "
                         "field, and the fall-through" % len(SHAPE_CASES)])

    problems = []
    for field in ("customfield_99999", "customfield_11800", "Epic Link"):
        got = mod._shape(field, "{\"id\": \"7\"}")
        if got != "{\"id\": \"7\"}":
            problems.append("%s was rewritten to %r" % (field, got))
    suite.record(GM, "an-unknown-custom-field-falls-through-untouched",
                 problems,
                 detail=["its shape is per-instance and there is nothing here "
                         "that could know it; a guess comes back as a 400 "
                         "blaming the FIELD rather than the guess",
                         "--field-json and the profile are the two ways to say "
                         "the shape out loud"])

    fields = created_fields(mod, {}, "--summary", "s",
                            "--field-json", "customfield_11300=[123]",
                            "--field-json", "components=[{\"id\": \"7\"}]")
    problems = []
    if fields.get("customfield_11300") != [123]:
        problems.append("--field-json was not parsed: %r"
                        % fields.get("customfield_11300"))
    if fields.get("components") != [{"id": "7"}]:
        problems.append("--field-json went through the shaping table: %r"
                        % fields.get("components"))
    text = setup_error(mod, lambda: created_fields(
        mod, {}, "--summary", "s", "--field-json", "components=[{id: 7}]"))
    if text is None:
        problems.append("invalid JSON was accepted")
    elif "components" not in text:
        problems.append("the error does not name the field: %r" % text)
    suite.record(GM, "field-json-is-parsed-and-bypasses-the-table", problems,
                 detail=[text or "<no error>",
                         "the escape hatch for a field whose shape this script "
                         "cannot know -- so the table must not second-guess it"])

    # -- the profile's own required list ------------------------------------
    required = dict(aliased, require=["epic"], fields={})
    text = setup_error(mod, lambda: mod._check_required(
        created_fields(mod, required, "--summary", "s"), required))
    problems = []
    if text is None:
        problems.append("a missing required field was not refused")
    else:
        problems += ["the refusal does not name %r" % t
                     for t in missing_tokens(text, ["customfield_11800",
                                                    "--field epic"])]
    supplied = created_fields(mod, required, "--summary", "s",
                              "--field", "epic=PROJ-99")
    if setup_error(mod, lambda: mod._check_required(supplied,
                                                    required)) is not None:
        problems.append("a field that WAS supplied was still reported missing")
    suite.record(GM, "profile-require-is-checked-locally-and-names-the-alias",
                 problems,
                 detail=[text or "<no error>",
                         "for the fields a project requires by CONVENTION; the "
                         "schema-required ones are named by Jira's own 400, "
                         "all at once"])

    # -- @me, per deployment -------------------------------------------------
    for label, deployment, me, want_ref in (
            ("cloud", "Cloud", CLOUD_ME, {"accountId": "5b10a2c8"}),
            ("dc", mod.SERVER, DC_ME, {"name": "jdoe"})):
        client, transport = routed_client(
            mod, [("/myself", response(mod, 200, me))],
            url=BASE_CLOUD if label == "cloud" else BASE_DC,
            email=EMAIL if label == "cloud" else None,
            deployment=deployment)
        got = mod.resolve_sentinels(client, "PROJ", {},
                                    {"assignee": "@me"})
        suite.record(GM, "me-resolves-to-the-%s-user-shape" % label,
                     problem_if(got != {"assignee": want_ref},
                                "resolved to %r, want %r"
                                % (got, {"assignee": want_ref})),
                     detail=["Cloud addresses a user by accountId and has "
                             "hidden `name` since the GDPR deprecation; DC has "
                             "no accountId at all",
                             "sending one deployment the other's shape is a "
                             "400 that names the field and not the reason"])

    client, transport = routed_client(
        mod, [("/myself", response(mod, 200, DC_ME))], deployment=mod.SERVER)
    got = mod.resolve_sentinels(client, "PROJ", {},
                                {"assignee": "@me", "reporter": "@me",
                                 "customfield_1": ["@me"]})
    problems = []
    if len(transport.calls) != 1:
        problems.append("%d request(s) for one identity" % len(transport.calls))
    if got.get("customfield_1") != [{"name": "jdoe"}]:
        problems.append("a sentinel inside a list was missed: %r" % got)
    suite.record(GM, "the-identity-is-fetched-once-however-often-it-appears",
                 problems,
                 detail=["`@me` may appear in the profile AND on the command "
                         "line in one invocation, and the answer cannot change "
                         "mid-run"])

    # -- and an identity that is EMPTY is refused rather than sent ----------
    #
    # Both branches of user_ref() were `str(... or "")`, so a /myself that
    # carries no accountId on Cloud (or no name and no key on DC) resolved
    # `@me` to `{"accountId": ""}` -- a value Jira accepts, in a field that
    # then belongs to nobody, on a create that reports success.
    for label, deployment, me, absent in (
            ("cloud", mod.CLOUD,
             {"displayName": "A Person", "name": "hidden-on-cloud"},
             "accountId"),
            ("dc", mod.SERVER,
             {"displayName": "A Person", "emailAddress": "a@b.example"},
             "name")):
        client, transport = routed_client(
            mod, [("/myself", response(mod, 200, me))], deployment=deployment)
        problems = []
        try:
            got = client.user_ref()
        except mod.SetupError as exc:
            missing = missing_tokens(str(exc), [mod.SENTINEL_ME, absent])
            if missing:
                problems.append("the refusal does not name %s: %r"
                                % (missing, str(exc)))
        except Exception as exc:
            problems.append("raised %s, want SetupError -- anything else "
                            "escapes main() as a traceback: %s"
                            % (type(exc).__name__, exc))
        else:
            problems.append("resolved to %r and would have SENT it" % (got,))
        suite.record(GM, "me-refuses-an-identity-with-no-%s-on-%s"
                     % (absent, label), problems,
                     detail=["/myself answered: %r" % (me,),
                             "every other response value in this file is "
                             "defended -- _meta_field coerces, _text defaults "
                             "-- and this one was sent as written"])

    client, transport = routed_client(
        mod, [("/myself", response(mod, 200, ["not", "an", "object"]))],
        deployment=mod.SERVER)
    problems = []
    try:
        got = client.user_ref()
    except mod.SetupError:
        pass
    except Exception as exc:
        problems.append("raised %s: a /myself that is not an object reaches "
                        ".get() and leaves main() as a traceback"
                        % type(exc).__name__)
    else:
        problems.append("resolved to %r" % (got,))
    suite.record(GM, "a-myself-that-is-not-an-object-is-refused-not-crashed",
                 problems,
                 detail=["behind an SSO proxy or a misrouted context path a "
                         "200 can carry anything at all, and `or {}` only "
                         "defends against a FALSY one"])

    # -- @active, at depth, against a PINNED board ---------------------------
    #
    # `isLast` for the same reason sprint_resolver carries it: a RoutedTransport
    # answers every request on a route with the SAME page, so a sprint envelope
    # that never says the walk is over describes a server that pages forever.
    client, transport = routed_client(
        mod, [("/board/1698/sprint", response(mod, 200,
                                              {"values": [SPRINT_ONE],
                                               "isLast": True})),
              ("/myself", response(mod, 200, DC_ME))],
        deployment=mod.SERVER)
    nested = {"assignee": "@me",
              "customfield_11300": {"outer": ["@active", {"who": "@me"}]},
              "labels": ["untouched", "@nothing"]}
    got = mod.resolve_sentinels(client, "PROJ", {"board": 1698}, nested)
    want = {"assignee": {"name": "jdoe"},
            "customfield_11300": {"outer": [442, {"who": {"name": "jdoe"}}]},
            "labels": ["untouched", "@nothing"]}
    problems = problem_if(got != want, "resolved to %r, want %r" % (got, want))
    problems += problem_if(
        any("/board?" in c.url or c.url.endswith("/board")
            for c in transport.calls),
        "a pinned board was looked up anyway: %r" % transport.methods)
    suite.record(GM, "sentinels-resolve-at-any-depth", problems,
                 detail=["the Sprint field is a bare number on some instances "
                         "and an array on others, so the sentinel legitimately "
                         "sits one level down",
                         "an unknown `@nothing` is left alone: only the two "
                         "named sentinels mean anything"])

    # -- @active refuses ambiguity ------------------------------------------
    for scenario in SPRINT_SCENARIOS:
        cid = scenario[0]
        suite.record(GM, "active-sprint-%s" % cid,
                     check_sprint_refusal(sprint_resolver(mod), [scenario]),
                     detail=["boards : %r" % (scenario[1],),
                             "sprints: %r" % (scenario[2],),
                             "want   : %s" % ("id %s" % scenario[3]
                                              if scenario[3] is not None
                                              else "a refusal naming %s"
                                              % list(scenario[4]))])

    # -- and a sprint id that cannot become an int refuses the same way -----
    #
    # These two rows are deliberately NOT entries in SPRINT_SCENARIOS: that
    # oracle catches a bare `Exception` and asserts on message tokens, so it
    # cannot express the property being measured here, which is the exception
    # TYPE.  `main()` maps SetupError and JiraError onto exit codes and lets
    # everything else out as a traceback, so an id that reaches int() unguarded
    # is the difference between a named refusal on stderr and a stack trace.
    # The oracle is also shared with the group H mutant, and editing it would
    # change what that control proves.
    for cid, sprint in (("with-no-id",
                         {"name": "Sprint 12", "state": "active"}),
                        ("with-a-non-numeric-id",
                         {"id": "not-a-number", "name": "Sprint 12",
                          "state": "active"})):
        client, _t = client_with(mod, [
            response(mod, 200, {"values": [SCRUM_ONE], "isLast": True}),
            response(mod, 200, {"values": [sprint], "isLast": True}),
        ], deployment=mod.SERVER)
        problems = []
        try:
            got = client.active_sprint_id("PROJ")
        except Exception as exc:
            if not isinstance(exc, mod.SetupError):
                problems.append("raised %s(%s); every exception that is not a "
                                "SetupError escapes main() as a traceback"
                                % (type(exc).__name__, exc))
            else:
                missing = missing_tokens(str(exc), ["1698", "@active"])
                if missing:
                    problems.append("the refusal does not name %s: %r"
                                    % (missing, str(exc)))
        else:
            problems.append("resolved to %r where the sprint carries no usable "
                            "id" % (got,))
        suite.record(GM, "active-sprint-%s-is-a-setup-error" % cid, problems,
                     detail=["sprint: %r" % (sprint,),
                             "the two branches are different exceptions from "
                             "int() -- absent is a TypeError, non-numeric a "
                             "ValueError -- and both have to land on the one "
                             "type main() knows how to report"])

    # -- --dry-run -----------------------------------------------------------
    plain = os.path.join(os.path.realpath(workspace), "plain-profile.json")
    with open(plain, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"project": "PROJ",
                             "projects": {"PROJ": {"issuetype": "Bug"}}}))
    with EnvSandbox():
        with NetworkGuard(mod) as guard:
            with captured() as (out, err):
                code = mod.main(connected(
                    ["create", "--profile", plain, "--summary", "shipped it",
                     "--field", "labels=regression, parser", "--dry-run"]))
    text = out.getvalue()
    problems = []
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if guard.calls:
        problems.append("--dry-run sent %d request(s)" % guard.calls)
    if "POST" not in text or BASE_DC + "/rest/api/2/issue" not in text:
        problems.append("the method and URL are not printed")
    want_body = {"fields": {"project": {"key": "PROJ"},
                            "issuetype": {"name": "Bug"},
                            "summary": "shipped it",
                            "labels": ["regression", "parser"]}}
    try:
        printed = json.loads(fenced_block(text, "json"))
    except ValueError:
        printed = None
        problems.append("the body is not a fenced ```json block that parses")
    if printed is not None and printed != want_body:
        problems.append("body %r, want %r" % (printed, want_body))
    suite.record(GM, "dry-run-create-prints-and-sends-nothing", problems,
                 detail=text.splitlines()
                 + ["requests made: %d" % guard.calls,
                    "this row is here and not in group E's table because it "
                    "is the one write whose body depends on a FILE, and the "
                    "profile has to be pinned or the case would read whatever "
                    "the machine running it happens to have"])

    sentinel_profile = os.path.join(os.path.realpath(workspace),
                                    "sentinel-profile.json")
    with open(sentinel_profile, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"project": "PROJ", "projects": {"PROJ": {
            "issuetype": "Bug", "board": 1698,
            "aliases": {"sprint": "customfield_11300"},
            "fields": {"assignee": "@me", "sprint": "@active"}}}}))

    seen = []

    def respond(method, url, _body, _headers):
        seen.append((method, url))
        if "/board/1698/sprint" in url:
            return response(mod, 200, {"values": [SPRINT_ONE],
                                       "isLast": True})
        if "/myself" in url:
            return response(mod, 200, DC_ME)
        raise AssertionError("unrouted request: %s %s" % (method, url))

    mod.reset_deployment_cache()
    mod._DEPLOYMENT_CACHE = mod.SERVER
    with EnvSandbox():
        with NetworkGuard(mod, responder=respond) as guard:
            with captured() as (out, err):
                code = mod.main(connected(
                    ["create", "--profile", sentinel_profile,
                     "--summary", "s", "--dry-run"]))
    mod.reset_deployment_cache()
    text = out.getvalue()
    problems = []
    if code != 0:
        problems.append("exit %r, want 0" % code)
    written = [pair for pair in seen if pair[0] != "GET"]
    if written:
        problems.append("--dry-run made a non-GET request: %r" % written)
    try:
        printed = json.loads(fenced_block(text, "json"))
    except ValueError:
        printed = None
        problems.append("the printed body does not parse")
    if printed is not None:
        resolved = printed.get("fields", {})
        if resolved.get("assignee") != {"name": "jdoe"}:
            problems.append("`@me` was echoed back unresolved: %r"
                            % resolved.get("assignee"))
        if resolved.get("customfield_11300") != 442:
            problems.append("`@active` was echoed back unresolved: %r"
                            % resolved.get("customfield_11300"))
    suite.record(GM, "dry-run-create-resolves-sentinels-with-reads-only",
                 problems,
                 detail=["requests: %r" % (seen,),
                         "a dry run that echoed `@active` back would confirm "
                         "the spelling and nothing else; resolving costs GETs "
                         "and the run still writes nothing"])

    # -- the real write ------------------------------------------------------
    args = create_args(mod, "--profile", plain, "--summary", "shipped it")
    client, transport = client_with(
        mod, [response(mod, 201, {"key": "PROJ-77", "id": "10077",
                                  "self": BASE_DC + "/rest/api/2/issue/10077"})],
        deployment=mod.SERVER)
    with EnvSandbox():
        with captured() as (out, err):
            code = mod.cmd_create(args, client)
    text = out.getvalue()
    problems = []
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if len(transport.calls) != 1:
        problems.append("%d request(s) for one create" % len(transport.calls))
    else:
        call = transport.calls[0]
        if (call.method, call.path) != ("POST", "/jira/rest/api/2/issue"):
            problems.append("sent %s %s" % (call.method, call.path))
        if call.body != {"fields": {"project": {"key": "PROJ"},
                                    "issuetype": {"name": "Bug"},
                                    "summary": "shipped it"}}:
            problems.append("body %r" % call.body)
    problems += ["the output omits %r" % t for t in missing_tokens(
        text, ["PROJ-77", BASE_DC + "/browse/PROJ-77"])]
    if "/rest/api/2/issue/10077" in text:
        problems.append("the API `self` link was printed as the issue URL")
    suite.record(GM, "create-prints-the-browse-url-not-the-self-link",
                 problems,
                 detail=text.splitlines()
                 + ["one is where a human goes to read the ticket, the other "
                    "is a JSON endpoint"])

    # -- the shaping table against the deployment it cannot see --------------
    # `_shape` maps assignee and reporter to {"name": VALUE} for every
    # deployment, and `user_ref` exists precisely because Cloud hid `name` at
    # the GDPR deprecation and addresses a user by accountId alone.  Both are
    # right about their own half and they contradict each other on one input:
    # a LITERAL name, on Cloud, through `--field`.  It fails closed -- a 400
    # naming the field and not the reason, nothing written -- so what is gated
    # is the round trip and the diagnosis, not a mis-filed ticket.
    #
    # `_shape` is a pure function of (field, value) and cannot make this call,
    # so the refusal lives one level up, in `_create_fields`, which is the
    # first frame that has a client.
    problems = []
    refusals = []
    for field in ("assignee", "reporter"):
        text = setup_error(mod, lambda f=field: created_fields(
            mod, {}, "--summary", "s", "--field", "%s=jdoe" % f,
            deployment=mod.CLOUD))
        if text is None:
            problems.append("`--field %s=jdoe` was shaped and sent on Cloud, "
                            "where {'name': ...} cannot resolve" % field)
            continue
        refusals.append(text)
        problems += ["the refusal does not name %r: %r" % (t, text)
                     for t in missing_tokens(text, [field, "jdoe",
                                                    "--field-json"])]
    suite.record(GM, "a-literal-user-name-on-cloud-is-refused-not-sent",
                 problems,
                 detail=(refusals or ["<no refusal>"])
                 + ["exit 2 before the round trip, with the way through named "
                    "-- rather than a user SEARCH, which is another request, "
                    "another ambiguity to break, and a second way to file the "
                    "work at the wrong person"])

    problems = []
    for deployment in (mod.SERVER, mod.CLOUD):
        fields = created_fields(mod, {}, "--summary", "s",
                                "--field", "assignee=@me",
                                "--field", "customfield_11300=@active",
                                deployment=deployment)
        if fields.get("assignee") != mod.SENTINEL_ME:
            problems.append("%s: `@me` did not survive _create_fields: %r"
                            % (deployment, fields.get("assignee")))
        if fields.get("customfield_11300") != mod.SENTINEL_ACTIVE_SPRINT:
            problems.append("%s: `@active` did not survive: %r"
                            % (deployment, fields.get("customfield_11300")))
    dc = created_fields(mod, {}, "--summary", "s", "--field", "assignee=jdoe",
                        deployment=mod.SERVER)
    if dc.get("assignee") != {"name": "jdoe"}:
        problems.append("Data Center stopped shaping a literal name: %r"
                        % dc.get("assignee"))
    suite.record(GM, "the-sentinels-and-Data-Center-are-untouched-by-it",
                 problems,
                 detail=["CONTROL: it passes before the refusal above exists "
                         "and after -- ANTI-VACUITY, because a refusal keyed "
                         "on the FIELD rather than on the value would take "
                         "`@me` down with it on the one deployment where `@me` "
                         "is the only spelling that works",
                         "a sentinel leaves the table a bare string on "
                         "purpose: resolve_sentinels swaps in user_ref(), "
                         "which is already per-deployment and right on both"])

    problems = []
    unknown = sorted(set(mod.SYSTEM_USER_FIELDS)
                     - set(mod.SYSTEM_OBJECT_FIELDS))
    if unknown:
        problems.append("SYSTEM_USER_FIELDS names %r, which the shaping table "
                        "does not shape as an object at all -- so the refusal "
                        "guards an input _shape() never produced" % unknown)
    suite.record(GM, "the-user-field-list-is-a-subset-of-the-object-list",
                 problems,
                 detail=["SYSTEM_USER_FIELDS  : %r" % (mod.SYSTEM_USER_FIELDS,),
                         "SYSTEM_OBJECT_FIELDS: %r"
                         % (mod.SYSTEM_OBJECT_FIELDS,),
                         "CONTROL: a drift guard, not a gate -- it cannot be "
                         "observed red against the unfixed file because the "
                         "narrower list does not exist there.  It is here "
                         "because two hand-written tables that have to agree "
                         "are exactly the pair that stops agreeing"])

    mod.reset_deployment_cache()


# ---------------------------------------------------------------------------
# I. hygiene
# ---------------------------------------------------------------------------

def group_i(suite, before, pyc_before, workspace):
    after = H.repo_tree()
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
                    title="Jira CLI: auth, URL join, deployment probe, the "
                          "four pagers, config, write guards, error mapping "
                          "and the three tracebacks it used to raise instead, "
                          "key and board-id validation, Markdown rendering, "
                          "the profile walk and the create payload",
                    opts=opts, mode="grouped")

    before = H.repo_tree()
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
            group_f(suite, mod, workspace.path)
            group_g(suite, mod, workspace.path)
            group_h(suite, mod)
            group_j(suite, mod, workspace.path)
            group_k(suite, mod)
            group_l(suite, mod, workspace.path)
            group_m(suite, mod, workspace.path)
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
