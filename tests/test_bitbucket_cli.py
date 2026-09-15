#!/usr/bin/env python3
"""Offline suite for the Bitbucket CLI (ClaudeCode/skills/bitbucket/scripts/bitbucket.py).

NOTHING HERE TOUCHES THE NETWORK, and that is structural rather than hoped for.
The client takes its transport as an INJECTED CALLABLE -- `Bitbucket(cfg,
fetch=..., sleep=...)` -- so every request in this suite is answered from a
scripted queue that also records the exact method, URL, headers and decoded body
the CLI built.  The paths that do not take an injected transport -- `main()` and
everything it constructs -- run with the module's own `urllib_fetch` attribute
replaced by a guard that COUNTS calls and refuses to make one.  The single case
that has to look at the real opener (group B's redirect chain) calls
`urllib_fetch` with a URL that has no host, which urllib rejects in
`do_request_` BEFORE any socket is created; nothing global is monkeypatched and
nothing leaves the machine.

WHAT IS WORTH GATING IN A CLIENT NOBODY CAN REACH FROM CI
---------------------------------------------------------
Only the pure decisions -- but that is where this client's damage lives, since
the one call it makes that no later call can undo is behind four of them:

  A  config resolution, the URL join, and the two refusals at the door.
     `api_url` drops None-valued query keys and sorts the rest, which is what
     makes a URL assertion stable at all.  The scheme refusal is not style: the
     token is a Bearer PAT, so `http://` puts it on the wire in cleartext -- and
     it has to run BEFORE reject_cloud, because a scheme-less
     `--url bitbucket.org/x` parses to hostname None and sailed straight past
     the Cloud check that exists to refuse exactly that host.  The userinfo
     refusal is asserted on what it must NOT say: a base URL carrying
     `user:secret@` reaches several print sites, so the refusal that catches it
     must not itself reproduce the secret.
  B  the same-origin redirect handler.  urllib's own HTTPRedirectHandler strips
     `content-length` and `content-type` and nothing else, so the stdlib default
     hands the Authorization header to whoever a 302 names.  All three
     components of an origin are gated separately -- a handler comparing only
     the host is carried as a mutant in group I -- and the ABSENT-port-on-https
     normalisation is gated in both directions, because it was added after the
     rest and had no case of its own.
  C  BITBUCKET_READ_ONLY, at BOTH layers.  main() gates on WRITE_COMMANDS, a
     hand-maintained tuple; `Bitbucket.request` gates on the VERB.  The
     important row is neither: it drives every entry in HANDLERS through a
     verb-recording transport and asserts WRITE_COMMANDS equals exactly the set
     of subcommands that issue a non-GET -- measured against the handler table,
     never against a typed list, because the defect being prevented is a future
     subcommand added to one list and forgotten in the other.
  D  the merge gate, four refusals and their order.  Each is asserted on a
     MEASUREMENT (zero transport calls, no POST) rather than on the shape of the
     source, and gate 2 deliberately so: `_target()` legitimately runs before it
     and can raise its own profile error without touching the network, so "the
     first statement in the function" would be a false claim about the code.
  E  `pr-builds`, whose exit code is advertised as a CI gate.  The verdict
     precedence, the blank state that made the verdict row vanish from the
     rendered document rather than read wrong, and the exit code pinned in
     Markdown AND in --json in the same case -- that last one is a just-fixed
     regression where the --json branch returned a hard-coded OK, so every gate
     piping to jq was silently always green.
  F  the per-subcommand request contract: method, path, body, query.
     `pr-approve` costs THREE requests; decline, reopen and merge each re-read
     the pull request first, because the optimistic-lock version is an
     assertion here and never an input.  Paging follows `nextPageStart` and
     never computes `start + size`, which the API documents as unsafe.
  G  `_flatten_reviewers`, both shapes.  The published reference describes an
     array of conditions; the endpoint answers with a FLAT array of users.  A
     client that trusted the document reported "1 condition, 0 reviewers"
     against a real instance -- a silent zero indistinguishable from a
     repository with none configured -- so the flat shape has its own named row.
  H  cross-file parity on `_profile_path`, which is a verbatim copy of jira.py's
     -- the identity is what forced a boundary repair through BOTH files in one
     change instead of one, and the group also DRIVES that boundary through a
     real symlinked $HOME, because identical text in a file is not the same
     claim as identical behaviour in the loaded module.  See that group's
     docstring.
  J  INFO rows: what is NOT proven.

NEGATIVE CONTROL (group I) -- mandatory, explicit, named
--------------------------------------------------------
An oracle that cannot fail proves nothing about the code it blesses.  Group I
feeds the SAME oracle functions groups A/C/E/B use a set of deliberately BROKEN
implementations -- a reject_cloud comparing only the full hostname, a
_build_verdict returning states[0], a WRITE_COMMANDS missing one entry, a
redirect handler comparing only the host -- and FAILS if any is accepted.  The
mirror assertion (the real implementations pass those same oracles) is recorded
alongside, because an oracle that rejected EVERYTHING would satisfy the mutants
and prove nothing.

Fixtures live in a `tempfile.mkdtemp()` workspace; group K asserts the repo tree
is untouched and that ZERO bytecode exists.

Groups:
  A  config resolution, URL join, the scheme and userinfo refusals
  B  the same-origin redirect handler
  C  BITBUCKET_READ_ONLY at both layers, and the derived write set
  D  the merge gate: four refusals and their order
  E  pr-builds: the verdict, the blank state, and the exit code
  F  the per-subcommand request contract
  G  _flatten_reviewers, both shapes
  H  cross-file parity on _profile_path, and the $HOME boundary it now holds
  I  negative control
  J  INFO -- what is NOT proven
  K  hygiene

Usage:
  python3 tests/test_bitbucket_cli.py
  python3 tests/test_bitbucket_cli.py --brief
Exit code 0 iff every non-informational case passes.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402

NAME = "bitbucket_cli"
TARGET = H.repo_path("ClaudeCode", "skills", "bitbucket", "scripts",
                     "bitbucket.py")
# The parity twin.  Group H is the only reader.
JIRA_TARGET = H.repo_path("ClaudeCode", "skills", "jira", "scripts", "jira.py")

GA = "A. config, URL join, refusals"
GB = "B. same-origin redirects"
GC = "C. read-only, both layers"
GD = "D. the merge gate"
GE = "E. pr-builds"
GF = "F. request contract"
GG = "G. reviewer flattening"
GH = "H. _profile_path parity + boundary"
GI = "I. negative control"
GJ = "J. not proven"
GK = "K. hygiene"

# Every environment variable the CLI reads, in both spellings where it accepts
# two.  Cleared before each config case: a developer machine with a real
# BITBUCKET_URL exported would otherwise turn these into a different test.
#
# BITBUCKET_PROFILE and its lowercase twin are on this list for a sharper
# version of the same reason -- they do not merely change what a case measures,
# they point `pr-create` at a profile whose CONTENTS this suite does not
# control, and every payload case would then assert against somebody's real
# project defaults.
ENV_KEYS = ("BITBUCKET_URL", "BITBUCKET_TOKEN", "BITBUCKET_PROJECT",
            "BITBUCKET_REPO", "BITBUCKET_PROFILE", "BITBUCKET_READ_ONLY",
            "bitbucket_profile")

# A Data Center base URL WITH a context path, on purpose: /bitbucket is what
# urljoin would eat, and every path assertion in this suite carries it.
BASE = "https://bitbucket.corp.local/bitbucket"
TOKEN = "s3cr3t-token-value"
PROJECT = "SL"
REPO = "ngs-media-server"
PR_ID = 1211
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

API = "/rest/api/1.0"
PR_BASE = "%s/projects/%s/repos/%s/pull-requests" % (API, PROJECT, REPO)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

PULL_REQUEST = {
    "id": PR_ID,
    "version": 7,
    "title": "Fix the parser",
    "state": "OPEN",
    "open": True,
    "locked": False,
    "author": {"user": {"displayName": "A Person", "name": "aperson"}},
    "reviewers": [{"user": {"displayName": "B Person", "name": "bperson"},
                   "status": "APPROVED", "approved": True}],
    "fromRef": {"id": "refs/heads/my-branch", "displayId": "my-branch",
                "latestCommit": SHA},
    "toRef": {"id": "refs/heads/master", "displayId": "master"},
    "links": {"self": [{"href": BASE + "/projects/%s/repos/%s/pull-requests/%d"
                                % (PROJECT, REPO, PR_ID)}]},
}

CLEAN_MERGE = {"outcome": "CLEAN", "conflicted": False, "vetoes": []}

USERNAME = "svc.bot"


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

    @property
    def query(self):
        raw = urllib.parse.urlsplit(self.url).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def __repr__(self):
        return "<%s %s body=%r>" % (self.method, self.url, self.body)


class FakeTransport:
    """A scripted stand-in for the client's `fetch` callable.

    Answers from a queue and records what it was asked.  An unscripted request
    raises rather than returning something plausible: a client that makes one
    extra call is exactly the defect groups D and F are looking for.
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

    @property
    def methods(self):
        return [call.method for call in self.calls]


class RoutedTransport:
    """Like FakeTransport, but answers by CALLBACK instead of by position.

    Group C's sweep drives fifteen subcommands whose request counts differ and
    are part of what is being measured; a positional queue would force the sweep
    to know each count in advance, which is the very thing the sweep exists to
    discover.
    """

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def __call__(self, method, url, body, headers):
        decoded = json.loads(body.decode("utf-8")) if body else None
        self.calls.append(Call(method, url, decoded, dict(headers)))
        return self.responder(method, url)

    @property
    def methods(self):
        return [call.method for call in self.calls]


class RawTransport:
    """Like FakeTransport, but keeps the body as BYTES.

    Nothing in this client sends a non-JSON body, which is itself worth being
    able to assert: this transport is what group F's header rows read the raw
    outgoing payload off, without a json.loads in the way that would hide a
    body that is not what it claims.
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


class NetworkGuard:
    """Replaces the module's real transport for `main()`-level cases.

    It never returns a response unless a responder is supplied; it counts and
    raises.  So "sent nothing" is a measurement (`guard.calls == 0`), not a
    claim about the source.
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
    """Clear every BITBUCKET_* spelling, apply `values`, restore on exit."""

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

    `_profile_path` is the only thing in this CLI that reads either, and group
    H is the only group that lets it walk at all -- every other case pins
    `--profile` at a file this suite wrote.  Both are restored on the way out,
    including the case where $HOME was not set, because group K asserts this
    run left the environment as it found it.
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
    """Build an HttpResponse the way the server would."""
    head = {}
    if ctype:
        head["Content-Type"] = ctype
    head.update(headers or {})
    if body is None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return mod.HttpResponse(status, head, body)


def make_cfg(mod, url=BASE, token=TOKEN, read_only_raw=None, timeout=30.0):
    return mod.Config(url, token, timeout, read_only_raw, {
        "base_url": "flag:--url", "token": "flag:--token",
        "read_only": "env:BITBUCKET_READ_ONLY"})


def client_with(mod, script, url=BASE, read_only_raw=None):
    """A Bitbucket client on a scripted transport."""
    transport = FakeTransport(script)
    return mod.Bitbucket(make_cfg(mod, url=url, read_only_raw=read_only_raw),
                         fetch=transport, sleep=lambda _s: None), transport


def parse_args(mod, argv):
    return mod.build_parser().parse_args(argv)


def connected(argv, url=BASE, token=TOKEN):
    """`argv` plus the connection flags.

    They go AFTER the subcommand because they live on the subparsers (the
    `parents=` pattern): repeating them on the top-level parser too would give
    two options the same dest, and the subparser's None would then overwrite
    whatever the top level parsed.
    """
    return list(argv) + ["--url", url, "--token", token]


def scoped(argv, profile):
    """`argv` plus the project/repo/profile flags every scoped subcommand needs.

    The profile is PINNED to a file this suite wrote, never left to the walk:
    `_profile_path` climbs from the working directory, so an unpinned case would
    read whatever `.claude/bitbucket.json` the machine running it happens to
    have above the checkout.
    """
    return list(argv) + ["--project", PROJECT, "--repo", REPO,
                         "--profile", profile]


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


def raised(call, *types):
    """(result, exception) for `call`, catching only `types` (default: any)."""
    try:
        return call(), None
    except (types or (Exception,)) as exc:
        return None, exc


def drive(mod, tail, script, profile=None, url=BASE, read_only_raw=None):
    """Run one subcommand over a scripted transport.

    -> (exit code or None, exception or None, transport, stdout, stderr)
    """
    argv = connected(list(tail) if profile is None else scoped(tail, profile),
                     url=url)
    args = parse_args(mod, argv)
    client, transport = client_with(mod, script, url=url,
                                    read_only_raw=read_only_raw)
    with captured() as (out, err):
        try:
            code, error = mod.HANDLERS[args.command](args, client), None
        except Exception as exc:  # noqa: BLE001 -- the case decides what it means
            code, error = None, exc
    return code, error, transport, out.getvalue(), err.getvalue()


def main_run(mod, tail, env=None, responder=None, profile=None):
    """Run `main()` with the real transport replaced by a counting guard."""
    argv = connected(list(tail) if profile is None else scoped(tail, profile))
    with EnvSandbox(**(env or {})):
        with NetworkGuard(mod, responder) as guard:
            with captured() as (out, err):
                code = mod.main(argv)
    return code, out.getvalue(), err.getvalue(), guard.calls


# ---------------------------------------------------------------------------
# the ORACLES -- shared by the live groups and by the negative control
# ---------------------------------------------------------------------------

# (base URL, must this be refused as a Cloud host)
#
# The four FALSE rows are not padding: a reject_cloud that raised on everything
# would satisfy the TRUE rows perfectly and refuse every Data Center instance
# this script exists to talk to.
CLOUD_CASES = [
    ("https://bitbucket.org", True),
    ("https://bitbucket.org/scm/x", True),
    ("https://BITBUCKET.ORG", True),
    # the second Cloud host, which an equality-only check misses
    ("https://api.bitbucket.org/2.0", True),
    ("https://teamname.bitbucket.org", True),
    ("https://bitbucket.corp.local/bitbucket", False),
    # a lookalike that is NOT the Cloud domain: the suffix test must be dotted
    ("https://bitbucket.org.corp.local", False),
    ("https://notbitbucket.org", False),
    ("https://bitbucket.example.com", False),
]

# (states reported against one commit, the one-word verdict)
VERDICT_CASES = [
    ([], "NONE"),
    (["SUCCESSFUL", "FAILED"], "FAILED"),
    (["INPROGRESS", "SUCCESSFUL"], "INPROGRESS"),
    (["FAILED", "INPROGRESS", "SUCCESSFUL"], "FAILED"),
    (["SUCCESSFUL"], "SUCCESSFUL"),
    # a state this file does not know is INFORMATIVE and passes through, but
    # never at the expense of one it does know
    (["WEIRD", "FAILED"], "FAILED"),
    (["WEIRD"], "WEIRD"),
    (["UNKNOWN"], "UNKNOWN"),
]

# (the URL a request was made to, the URL a 30x pointed at, must it be refused)
REDIRECT_CASES = [
    # the context-path and trailing-slash kind a reverse proxy really does emit
    (BASE + "/rest/api/1.0/x", BASE + "/rest/api/1.0/x/", False),
    (BASE + "/x", "https://bitbucket.corp.local/elsewhere", False),
    # an absent port on https IS 443; a proxy may redirect between the two
    # spellings, and refusing that is a false alarm about the one deployment
    # shape this handler exists to survive
    ("https://bitbucket.corp.local/x",
     "https://bitbucket.corp.local:443/y", False),
    ("https://bitbucket.corp.local:443/x",
     "https://bitbucket.corp.local/y", False),
    ("https://bitbucket.corp.local/x", "https://BITBUCKET.CORP.LOCAL/y", False),
    # the three components, each on its own
    ("https://bitbucket.corp.local/x", "https://evil.example/y", True),
    ("https://bitbucket.corp.local/x", "http://bitbucket.corp.local/y", True),
    ("https://bitbucket.corp.local/x",
     "https://bitbucket.corp.local:8443/y", True),
]


def check_reject_cloud(fn):
    """Problems for a `reject_cloud(base_url)` that must RAISE on a Cloud host.

    Shared by the live row in group A and by the mutant in group I, so the row
    that blesses the real function is passing an oracle that has been shown to
    reject one which compares only the full hostname.
    """
    problems = []
    for url, must_refuse in CLOUD_CASES:
        _got, exc = raised(lambda u=url: fn(u))
        refused = exc is not None
        if refused == must_refuse:
            continue
        if must_refuse:
            problems.append("%s was ACCEPTED; it is Bitbucket Cloud" % url)
        else:
            problems.append("%s was refused (%s); it is not Cloud" % (url, exc))
    return problems


def check_verdict(fn):
    """Problems for a `_build_verdict(states) -> str` implementation."""
    problems = []
    for states, want in VERDICT_CASES:
        got, exc = raised(lambda s=states: fn(s))
        if exc is not None:
            problems.append("%r raised %s: %s"
                            % (states, type(exc).__name__, exc))
            continue
        if got != want:
            problems.append("%r -> %r, want %r" % (states, got, want))
    return problems


def check_redirect(refuses):
    """Problems for a `refuses(from_url, to_url) -> bool` redirect decision.

    The FALSE rows carry the whole anti-vacuity burden: a handler that refused
    every 30x would satisfy the three cross-origin rows and break the trailing
    slash a Data Center reverse proxy emits on every other request.
    """
    problems = []
    for here, there, must_refuse in REDIRECT_CASES:
        got, exc = raised(lambda a=here, b=there: refuses(a, b))
        if exc is not None:
            problems.append("%s -> %s raised %s: %s"
                            % (here, there, type(exc).__name__, exc))
            continue
        if got != must_refuse:
            problems.append("%s -> %s: refused=%s, want %s"
                            % (here, there, got, must_refuse))
    return problems


def check_write_commands(declared, measured):
    """Problems for a WRITE_COMMANDS tuple against the MEASURED non-GET set.

    Both directions are problems and they are different defects.  A subcommand
    that writes and is not declared reaches the network with
    BITBUCKET_READ_ONLY set; one declared that only reads is a read refused for
    no reason, which teaches a caller to unset the variable.
    """
    problems = []
    missing = sorted(set(measured) - set(declared))
    extra = sorted(set(declared) - set(measured))
    if missing:
        problems.append("issues a non-GET but is NOT in WRITE_COMMANDS: %s"
                        % missing)
    if extra:
        problems.append("declared in WRITE_COMMANDS but only ever GETs: %s"
                        % extra)
    return problems


def real_redirect_refuses(mod):
    """The REAL handler, adapted to check_redirect's signature."""
    def refuses(here, there):
        handler = mod.SameOriginRedirectHandler()
        request = urllib.request.Request(here)
        try:
            handler.redirect_request(request, None, 302, "Found", {}, there)
        except mod.SetupError:
            return True
        return False
    return refuses


# ---------------------------------------------------------------------------
# A. config resolution, URL join, the two refusals at the door
# ---------------------------------------------------------------------------

def group_a(suite, mod):
    got = mod.api_url(BASE, "%s/x" % API,
                      {"state": "OPEN", "at": None, "direction": None,
                       "order": "NEWEST"})
    want = BASE + "/rest/api/1.0/x?order=NEWEST&state=OPEN"
    suite.record(GA, "api-url-drops-none-valued-query-keys",
                 problem_if(got != want, "got %s, want %s" % (got, want)),
                 detail=["got : %s" % got,
                         "`at` and `direction` are unset on most invocations "
                         "and the server treats `at=` as a filter on the empty "
                         "branch, so dropping them is not cosmetic"])

    got = mod.api_url(BASE, "/p", {"z": 1, "a": 2, "m": 3})
    want = BASE + "/p?a=2&m=3&z=1"
    suite.record(GA, "api-url-sorts-the-remaining-keys",
                 problem_if(got != want, "got %s, want %s" % (got, want)),
                 detail=["got: %s" % got,
                         "a dict's insertion order is not a contract, and "
                         "every URL assertion in this suite would be pinning "
                         "it instead of the query"])

    got = mod.api_url(BASE, "/p", {"a": None, "b": None})
    suite.record(GA, "api-url-emits-no-question-mark-when-all-keys-drop",
                 problem_if(got != BASE + "/p", "got %s" % got),
                 detail=["got: %s" % got,
                         "a bare trailing `?` is a different URL to some "
                         "reverse proxies and to every cache in front of one"])

    got = mod.api_url(BASE, "/p", {"flag": True, "off": False})
    suite.record(GA, "api-url-renders-booleans-as-lowercase-words",
                 problem_if(got != BASE + "/p?flag=true&off=false",
                            "got %s" % got),
                 detail=["got: %s" % got,
                         "str(True) is 'True', which this API reads as neither "
                         "true nor false"])

    ours = mod.api_url("https://bitbucket.corp.local/bitbucket/",
                       "/rest/api/1.0/application-properties")
    theirs = urllib.parse.urljoin("https://bitbucket.corp.local/bitbucket/",
                                  "/rest/api/1.0/application-properties")
    problems = []
    if ours != BASE + "/rest/api/1.0/application-properties":
        problems.append("got %s" % ours)
    if ours == theirs:
        problems.append("api_url agrees with urljoin here (%s), so this case "
                        "no longer demonstrates anything" % ours)
    suite.record(GA, "api-url-keeps-what-urljoin-would-eat", problems,
                 detail=["api_url : %s" % ours,
                         "urljoin : %s  <- the /bitbucket is gone" % theirs,
                         "a Data Center install behind a context path is the "
                         "normal shape, and the 404 that follows reads as a "
                         "missing repository"])

    got = mod.pr_path("S L", "re po", PR_ID, "/merge")
    suite.record(GA, "pr-path-quotes-its-segments",
                 problem_if("S%20L" not in got or "re%20po" not in got,
                            "unquoted segment survived: %s" % got),
                 detail=["got: %s" % got,
                         "RX_PROJECT_KEY and RX_REPO_SLUG refuse anything "
                         "structural upstream; the quoting is the second half "
                         "of that, not a substitute for it"])

    # -- the two refusals at the door -------------------------------------
    with EnvSandbox():
        _cfg, exc = raised(
            lambda: mod.resolve_config(parse_args(
                mod, connected(["version"], url="http://bitbucket.corp.local"))),
            mod.SetupError)
    text = str(exc or "")
    problems = []
    if exc is None:
        problems.append("an http:// base URL was accepted")
    problems += ["the refusal omits %r" % t
                 for t in missing_tokens(text, ["'http'", "Bearer",
                                                "cleartext", "https://"])]
    suite.record(GA, "http-scheme-is-refused-because-the-token-is-a-bearer",
                 problems,
                 detail=[text or "<no refusal>",
                         "this is not a style rule: the PAT goes out in the "
                         "Authorization header on every single request"])

    with EnvSandbox():
        _cfg, exc = raised(
            lambda: mod.resolve_config(parse_args(
                mod, connected(["version"], url="bitbucket.org/x"))),
            mod.SetupError)
    text = str(exc or "")
    problems = []
    if exc is None:
        problems.append("a scheme-less base URL was accepted")
    elif "scheme is ''" not in text:
        problems.append("refused, but not as a SCHEME problem: %r" % text)
    suite.record(GA, "scheme-less-url-is-refused-before-reject-cloud-sees-it",
                 problems,
                 detail=[text or "<no refusal>",
                         "`bitbucket.org/x` parses to hostname None, so "
                         "reject_cloud used to be handed nothing to look at "
                         "and PASSED the one host it exists to refuse",
                         "requiring the scheme first is what gives that check "
                         "a hostname"])

    with EnvSandbox():
        _cfg, exc = raised(
            lambda: mod.resolve_config(parse_args(
                mod, connected(["version"], url="https://bitbucket.org/x"))),
            mod.SetupError)
    text = str(exc or "")
    problems = []
    if exc is None:
        problems.append("https://bitbucket.org was accepted")
    elif "Cloud" not in text:
        problems.append("refused for some other reason: %r" % text)
    suite.record(GA, "the-same-host-WITH-a-scheme-is-refused-as-cloud",
                 problems,
                 detail=[text or "<no refusal>",
                         "ANTI-VACUITY for the row above: the scheme gate must "
                         "not be what stands in for the Cloud gate"])

    secret = "hunter2"
    with EnvSandbox():
        _cfg, exc = raised(
            lambda: mod.resolve_config(parse_args(
                mod,
                connected(["version"],
                          url="https://someone:%s@bitbucket.corp.local"
                              % secret))),
            mod.SetupError)
    text = str(exc or "")
    problems = []
    if exc is None:
        problems.append("a base URL carrying userinfo was accepted")
    if secret in text:
        problems.append("THE SECRET WAS REPRODUCED IN THE REFUSAL TEXT")
    if "bitbucket.corp.local" in text:
        problems.append("the refusal echoed the URL it was refusing")
    problems += ["the refusal omits %r" % t
                 for t in missing_tokens(text, ["userinfo", "BITBUCKET_TOKEN"])]
    suite.record(GA, "userinfo-is-refused-without-reproducing-the-secret",
                 problems,
                 detail=[text or "<no refusal>",
                         "this refusal exists BECAUSE a `user:secret@` base URL "
                         "reaches several print sites, so a refusal that quoted "
                         "the URL back would be the leak it was added to stop"])

    suite.record(GA, "reject-cloud-table", check_reject_cloud(mod.reject_cloud),
                 detail=["%s -> %s" % (url, "refuse" if must else "accept")
                         for url, must in CLOUD_CASES])

    _got, exc = raised(lambda: mod.reject_cloud("https://api.bitbucket.org"),
                       Exception)
    suite.record(GA, "reject-cloud-catches-the-api-host-by-equality",
                 problem_if(not isinstance(exc, mod.SetupError),
                            "raised %r, want SetupError" % (exc,)),
                 detail=[str(exc or "<accepted>"),
                         "CLOUD_HOSTS has two entries and the check is "
                         "equality OR dotted suffix; an equality-only test "
                         "against the first entry passes bitbucket.org and "
                         "misses this one"])

    _got, exc = raised(
        lambda: mod.reject_cloud("https://teamname.bitbucket.org"), Exception)
    suite.record(GA, "reject-cloud-catches-a-dotted-subdomain",
                 problem_if(not isinstance(exc, mod.SetupError),
                            "raised %r, want SetupError" % (exc,)),
                 detail=[str(exc or "<accepted>")])

    cfg = make_cfg(mod)
    text = repr(cfg)
    problems = []
    if TOKEN in text:
        problems.append("THE TOKEN VALUE IS IN THE REPR")
    if "<redacted>" not in text:
        problems.append("the redaction marker is gone: %r" % text)
    if cfg.base_url not in text:
        problems.append("the base URL is not in the repr: %r" % text)
    suite.record(GA, "config-repr-redacts-the-token", problems,
                 detail=["repr: %s" % text,
                         "a Config reaches a traceback frame on any uncaught "
                         "exception, and a traceback is the single most pasted "
                         "artifact there is"])

    with EnvSandbox():
        with captured() as (out, err):
            _cfg, exc = raised(
                lambda: mod.resolve_config(parse_args(mod, ["version"])),
                SystemExit)
    text = err.getvalue()
    problems = []
    if getattr(exc, "code", None) != 2:
        problems.append("exit code %r, want 2" % getattr(exc, "code", None))
    problems += ["the refusal omits %r" % t
                 for t in missing_tokens(text, ["refusing to run",
                                                "BITBUCKET_URL (or --url)",
                                                "BITBUCKET_TOKEN (or --token)"])]
    if out.getvalue().strip():
        problems.append("a configuration refusal wrote to stdout: %r"
                        % out.getvalue())
    suite.record(GA, "missing-config-exits-2-naming-var-and-flag", problems,
                 detail=text.splitlines())

    with EnvSandbox(BITBUCKET_URL="https://env.example",
                    BITBUCKET_TOKEN="env-token"):
        cfg = mod.resolve_config(parse_args(
            mod, connected(["version"], url="https://flag.example",
                           token="flag-token")))
    problems = []
    if cfg.base_url != "https://flag.example":
        problems.append("url: %r" % cfg.base_url)
    if cfg.token != "flag-token":
        problems.append("token: %r" % cfg.token)
    if cfg.sources.get("base_url") != "flag:--url":
        problems.append("url source: %r" % cfg.sources.get("base_url"))
    if cfg.sources.get("token") != "flag:--token":
        problems.append("token source: %r" % cfg.sources.get("token"))
    suite.record(GA, "flag-beats-env-and-the-winner-names-itself", problems,
                 detail=["sources: %r" % cfg.sources])

    with EnvSandbox(BITBUCKET_URL=BASE, BITBUCKET_TOKEN=TOKEN):
        cfg = mod.resolve_config(parse_args(mod, ["version"]))
    problems = []
    if cfg.base_url != BASE or cfg.token != TOKEN:
        problems.append("resolved %r / %r" % (cfg.base_url, cfg.token))
    if cfg.sources.get("base_url") != "env:BITBUCKET_URL":
        problems.append("url source: %r" % cfg.sources.get("base_url"))
    suite.record(GA, "env-fallback-records-its-source", problems,
                 detail=["sources: %r" % cfg.sources])

    got = mod.normalize_base_url("https://h/bitbucket///")
    suite.record(GA, "normalize-base-url-strips-every-trailing-slash",
                 problem_if(got != "https://h/bitbucket", "got %r" % got),
                 detail=["got: %s" % got,
                         "rstrip('/'), not one slash: every path this file "
                         "joins on starts with one, so a base that kept any "
                         "would produce a doubled separator"])

    truthy = ["1", "true", "TRUE", "True", "yes", "YES", " true "]
    falsey = ["0", "false", "no", "", "off", "maybe", "2", None]
    problems = []
    problems += ["%r must be truthy" % v for v in truthy if not mod._truthy(v)]
    problems += ["%r must be falsey" % v for v in falsey if mod._truthy(v)]
    suite.record(GA, "read-only-truthy-table", problems,
                 detail=["truthy: %r" % truthy, "falsey: %r" % falsey,
                         "one spelling rule for every boolean this file reads, "
                         "so `BITBUCKET_READ_ONLY=off` cannot mean on"])


# ---------------------------------------------------------------------------
# B. the same-origin redirect handler
# ---------------------------------------------------------------------------

def redirect(mod, here, there, code=302):
    """(returned Request or None, exception or None) for one 30x decision."""
    handler = mod.SameOriginRedirectHandler()
    request = urllib.request.Request(here)
    return raised(lambda: handler.redirect_request(
        request, None, code, "Found", {}, there))


def capture_opener(mod):
    """The OpenerDirector `urllib_fetch` really builds, and its handler.

    Reached WITHOUT a socket: the redirect handler class is replaced on the
    module under test -- the same one named attribute seam `urllib_fetch`
    already is -- with a subclass that records the opener handed to its
    `add_parent`, and `urllib_fetch` is then called with a URL that has no
    host.  urllib raises URLError('no host given') in `do_request_`, before any
    connection is attempted, so the opener has been fully assembled and nothing
    has been dialled.  urllib itself is never patched.
    """
    seen = []
    base = mod.SameOriginRedirectHandler

    class Recording(base):
        def add_parent(self, parent):
            base.add_parent(self, parent)
            seen.append(self)

    saved = mod.SameOriginRedirectHandler
    mod.SameOriginRedirectHandler = Recording
    try:
        raised(lambda: mod.urllib_fetch("GET", "https://", None, {}))
    finally:
        mod.SameOriginRedirectHandler = saved
    if not seen:
        return None, None
    return seen[0], getattr(seen[0], "parent", None)


def group_b(suite, mod):
    new, exc = redirect(mod, BASE + "/rest/api/1.0/x",
                        BASE + "/rest/api/1.0/x/")
    problems = []
    if exc is not None:
        problems.append("a same-origin 30x raised %s: %s"
                        % (type(exc).__name__, exc))
    elif not isinstance(new, urllib.request.Request):
        problems.append("returned %r, want a urllib.request.Request" % (new,))
    elif new.full_url != BASE + "/rest/api/1.0/x/":
        problems.append("the new Request points at %r" % new.full_url)
    suite.record(GB, "same-origin-30x-is-delegated-to-the-stdlib", problems,
                 detail=["-> %s" % (getattr(new, "full_url", None),),
                         "the trailing-slash and context-path kind a reverse "
                         "proxy in front of Data Center really does emit; "
                         "refusing it would break every other request"])

    for label, there in (("host", "https://evil.example/y"),
                         ("scheme", "http://bitbucket.corp.local/y"),
                         ("port", "https://bitbucket.corp.local:8443/y")):
        new, exc = redirect(mod, "https://bitbucket.corp.local/x", there)
        problems = []
        if not isinstance(exc, mod.SetupError):
            problems.append("differing %s was ACCEPTED (returned %r, raised %r)"
                            % (label, new, exc))
        else:
            problems += ["the refusal omits %r" % t for t in missing_tokens(
                str(exc), ["cross-origin", "Authorization"])]
        suite.record(GB, "cross-origin-%s-is-refused" % label, problems,
                     detail=[str(exc or "<accepted>"),
                             "each component on its own, because a handler "
                             "comparing only the host passes two of these "
                             "three -- group I carries exactly that mutant"])

    for label, here, there in (
            ("absent-then-443", "https://bitbucket.corp.local/x",
             "https://bitbucket.corp.local:443/y"),
            ("443-then-absent", "https://bitbucket.corp.local:443/x",
             "https://bitbucket.corp.local/y")):
        new, exc = redirect(mod, here, there)
        problems = []
        if exc is not None:
            problems.append("refused a same-origin redirect: %s" % exc)
        elif not isinstance(new, urllib.request.Request):
            problems.append("returned %r" % (new,))
        suite.record(GB, "https-%s-is-the-same-origin" % label, problems,
                     detail=["%s -> %s" % (here, there),
                             "443 IS the default for https, so omitting it and "
                             "spelling it are one origin; a proxy in front of "
                             "Data Center legitimately redirects between the "
                             "two spellings"])

    got = mod._origin("https://bitbucket.corp.local/x")
    suite.record(GB, "origin-normalises-an-absent-https-port-to-443",
                 problem_if(got != ("https", "bitbucket.corp.local", 443),
                            "got %r" % (got,)),
                 detail=["_origin -> %r" % (got,)])

    http_absent = mod._origin("http://bitbucket.corp.local/x")
    http_80 = mod._origin("http://bitbucket.corp.local:80/x")
    problems = []
    if http_absent[2] is not None:
        problems.append("an http URL had its port normalised to %r"
                        % (http_absent[2],))
    if http_absent == http_80:
        problems.append("http with and without :80 compare equal, so the "
                        "normalisation is NOT https-only")
    suite.record(GB, "the-443-normalisation-is-https-only", problems,
                 detail=["http, no port : %r" % (http_absent,),
                         "http, :80      : %r" % (http_80,),
                         "the docstring claims the normalisation cannot weaken "
                         "the check because it applies only where 443 IS the "
                         "default -- this is that claim, measured"])

    got = mod._origin("https://bitbucket.corp.local:0x50/y")
    suite.record(GB, "origin-reports-an-unparseable-port-as-question-mark",
                 problem_if(got != ("https", "bitbucket.corp.local", "?"),
                            "got %r" % (got,)),
                 detail=["_origin -> %r" % (got,),
                         "urlparse().port raises ValueError on a port that is "
                         "not an integer in range, and this is read INSIDE a "
                         "redirect decision where anything but the deliberate "
                         "refusal reaches the caller as a traceback"])

    new, exc = redirect(mod, "https://bitbucket.corp.local/x",
                        "https://bitbucket.corp.local:0x50/y")
    problems = []
    if isinstance(exc, ValueError):
        problems.append("the unparseable port escaped as a ValueError")
    elif not isinstance(exc, mod.SetupError):
        problems.append("an unparseable port was ACCEPTED (returned %r, "
                        "raised %r)" % (new, exc))
    suite.record(GB, "unparseable-port-is-refused-not-raised", problems,
                 detail=[str(exc or "<accepted>"),
                         "an unparseable port can only DIFFER from a real one, "
                         "which is the answer the caller needs anyway"])

    handler, opener = capture_opener(mod)
    problems = []
    if handler is None or opener is None:
        problems.append("urllib_fetch did not build an opener carrying "
                        "SameOriginRedirectHandler -- urlopen's default chain "
                        "would let the token follow a 30x off this origin")
        codes = {}
    else:
        codes = {k: v for k, v in (opener.handle_error.get("http") or {}).items()
                 if isinstance(k, int)}
        if 302 not in codes:
            problems.append("no 302 handler in the opener's chain: %r"
                            % sorted(codes))
        for code, handlers in sorted(codes.items()):
            # OpenerDirector.add_handler stores the HANDLER OBJECT in these
            # lists, not a bound method -- `_call_chain` does the getattr at
            # call time -- so these entries are what has to be type-checked.
            for owner in handlers:
                if not isinstance(owner, mod.SameOriginRedirectHandler):
                    problems.append("%d is handled by %s, which is not a "
                                    "SameOriginRedirectHandler"
                                    % (code, type(owner).__name__))
                if type(owner) is urllib.request.HTTPRedirectHandler:
                    problems.append("the STDLIB redirect handler is in the "
                                    "chain for %d" % code)
            if len(handlers) != 1:
                problems.append("%d has %d handlers, want exactly 1"
                                % (code, len(handlers)))
    suite.record(GB, "opener-redirect-chain-is-only-the-same-origin-handler",
                 problems,
                 detail=["redirect codes in the chain: %r" % sorted(codes),
                         "build_opener drops HTTPRedirectHandler from its "
                         "defaults when an INSTANCE of a subclass is passed, "
                         "so this asserts the substitution happened rather "
                         "than that a second handler was appended",
                         "reached with no socket: urllib_fetch was called with "
                         "a host-less URL and raised URLError in do_request_"])


# ---------------------------------------------------------------------------
# C. BITBUCKET_READ_ONLY at both layers, and the DERIVED write set
# ---------------------------------------------------------------------------

# One invocation per entry in HANDLERS.  The sweep below asserts this table
# covers the handler table exactly, so a subcommand cannot be added to the CLI
# and quietly skipped here.
SUBCOMMAND_ARGV = {
    "version": ["version"],
    "whoami": ["whoami"],
    "repo": ["repo"],
    "pr-list": ["pr-list"],
    "pr-get": ["pr-get", str(PR_ID)],
    "pr-activities": ["pr-activities", str(PR_ID)],
    "pr-mergeability": ["pr-mergeability", str(PR_ID)],
    "pr-reviewers": ["pr-reviewers", "--from", "my-branch"],
    "pr-builds": ["pr-builds", str(PR_ID)],
    "pr-create": ["pr-create", "--from", "my-branch", "--title", "a title"],
    "pr-comment": ["pr-comment", str(PR_ID), "a comment body"],
    "pr-approve": ["pr-approve", str(PR_ID)],
    "pr-decline": ["pr-decline", str(PR_ID)],
    "pr-reopen": ["pr-reopen", str(PR_ID)],
    "pr-merge": ["pr-merge", str(PR_ID), "--yes"],
}

# `version` and `whoami` are built from the `common` parent only; handing them
# --project would be an argparse error, not a finding.
UNSCOPED = ("version", "whoami")

# The six writes, spelled as main() sees them.  Used by the read-only table
# below; the SWEEP deliberately does not read this -- it measures.
READ_ONLY_INVOCATIONS = {name: SUBCOMMAND_ARGV[name]
                         for name in ("pr-approve", "pr-comment", "pr-create",
                                      "pr-decline", "pr-merge", "pr-reopen")}


def fleet_response(mod, method, url):
    """Answer any request the fifteen subcommands can make, by path shape."""
    path = urllib.parse.urlsplit(url).path
    if path.endswith("/application-properties"):
        return response(mod, 200,
                        {"displayName": "Bitbucket", "version": "9.4.23",
                         "buildNumber": "9004023"},
                        headers={"X-AUSERNAME": USERNAME})
    if path.startswith(BASE_PATH + "/rest/default-reviewers/"):
        return response(mod, 200, [{"name": "areviewer"}])
    if path.startswith(BASE_PATH + "/rest/build-status/"):
        return response(mod, 200, {"values": [{"state": "SUCCESSFUL",
                                               "name": "CI",
                                               "url": "https://ci.example/1"}],
                                   "isLastPage": True})
    if path.endswith("/activities"):
        return response(mod, 200, {"values": [], "isLastPage": True})
    if path.endswith("/merge"):
        if method == "GET":
            return response(mod, 200, dict(CLEAN_MERGE))
        return response(mod, 200, dict(PULL_REQUEST, state="MERGED"))
    if path.endswith("/comments"):
        return response(mod, 200, {"id": 9001, "version": 0,
                                   "author": {"name": USERNAME}})
    if "/participants/" in path:
        return response(mod, 200, {"status": "APPROVED",
                                   "user": {"name": USERNAME,
                                            "displayName": "Service Bot"}})
    if path.endswith("/decline"):
        return response(mod, 200, dict(PULL_REQUEST, state="DECLINED"))
    if path.endswith("/reopen"):
        return response(mod, 200, dict(PULL_REQUEST, state="OPEN"))
    if path.endswith("/pull-requests"):
        if method == "GET":
            return response(mod, 200, {"values": [dict(PULL_REQUEST)],
                                       "isLastPage": True})
        return response(mod, 200, dict(PULL_REQUEST, id=1212))
    if re.search(r"/pull-requests/\d+$", path):
        return response(mod, 200, dict(PULL_REQUEST))
    if re.search(r"/repos/[^/]+$", path):
        return response(mod, 200, {"id": 42, "name": "Repo",
                                   "state": "AVAILABLE",
                                   "defaultBranch": "refs/heads/master",
                                   "public": False, "archived": False})
    raise AssertionError("unrouted request: %s %s" % (method, url))


BASE_PATH = urllib.parse.urlsplit(BASE).path


def sweep_verbs(mod, profile):
    """Every subcommand, over a verb-recording transport.

    -> (name -> list of methods, name -> exception text)

    Nothing here consults WRITE_COMMANDS.  The point is to MEASURE which
    subcommands issue a non-GET and compare that set against the tuple
    afterwards, because the defect being prevented is a future subcommand added
    to one list and forgotten in the other.
    """
    verbs = {}
    errors = {}
    for name in sorted(mod.HANDLERS):
        tail = SUBCOMMAND_ARGV.get(name)
        if tail is None:
            errors[name] = "no invocation declared in SUBCOMMAND_ARGV"
            verbs[name] = []
            continue
        argv = connected(list(tail) if name in UNSCOPED
                         else scoped(tail, profile))
        transport = RoutedTransport(
            lambda method, url: fleet_response(mod, method, url))
        client = mod.Bitbucket(make_cfg(mod), fetch=transport,
                               sleep=lambda _s: None)
        with EnvSandbox():
            args = parse_args(mod, argv)
            with captured() as (_out, _err):
                try:
                    mod.HANDLERS[name](args, client)
                except Exception as exc:  # noqa: BLE001
                    errors[name] = "%s: %s" % (type(exc).__name__, exc)
        verbs[name] = transport.methods
    return verbs, errors


def group_c(suite, mod, profile):
    # -- layer 1: main(), before the client exists ------------------------
    for name, tail in sorted(READ_ONLY_INVOCATIONS.items()):
        code, _out, err, calls = main_run(mod, tail,
                                          env={"BITBUCKET_READ_ONLY": "1"})
        problems = []
        if code != 2:
            problems.append("exit %r, want 2" % code)
        if calls:
            problems.append("%d request(s) were made before refusing" % calls)
        problems += ["the refusal omits %r" % t for t in missing_tokens(
            err, ["BITBUCKET_READ_ONLY", "=1", "env:BITBUCKET_READ_ONLY",
                  repr(name)])]
        suite.record(GC, "layer1-read-only-refuses-%s" % name, problems,
                     detail=[err.strip(), "requests made: %d" % calls,
                             "no --profile is passed on purpose: the refusal "
                             "fires before the handler, so a guarded run never "
                             "reaches the profile walk at all"])

    def banner(_method, _url, _body, _headers):
        return response(mod, 200,
                        {"displayName": "Bitbucket", "version": "9.4.23"},
                        headers={"X-AUSERNAME": USERNAME})

    code, out, err, calls = main_run(mod, ["version"],
                                     env={"BITBUCKET_READ_ONLY": "true"},
                                     responder=banner)
    problems = []
    if code != 0:
        problems.append("a read exited %r under BITBUCKET_READ_ONLY" % code)
    if calls != 1:
        problems.append("%d request(s) for one read" % calls)
    suite.record(GC, "layer1-read-only-does-not-block-a-read", problems,
                 detail=[out.strip(), err.strip(),
                         "read-only gates WRITES; a read under it must still "
                         "work, or the variable becomes an off switch and gets "
                         "unset instead of trusted"])

    # -- layer 2: the transport backstop ----------------------------------
    client, transport = client_with(mod, [], read_only_raw="1")
    _got, exc = raised(lambda: client.request("POST", "%s/x" % API,
                                              body={"a": 1}))
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a POST was allowed under read-only (raised %r)" % exc)
    else:
        problems += ["the refusal omits %r" % t for t in missing_tokens(
            str(exc), ["BITBUCKET_READ_ONLY", "POST", "%s/x" % API])]
    if transport.calls:
        problems.append("%d request(s) reached the transport"
                        % len(transport.calls))
    suite.record(GC, "layer2-request-refuses-a-non-GET", problems,
                 detail=[str(exc or "<allowed>"),
                         "main() gates on a hand-maintained tuple of NAMES; "
                         "this gates on the VERB, at the one place the verb "
                         "actually is"])

    client, transport = client_with(mod, [], read_only_raw="1")
    _got, exc = raised(lambda: client.request("put", "%s/x" % API, body={}))
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a lowercase verb slipped past (raised %r)" % exc)
    elif "PUT" not in str(exc):
        problems.append("the refusal does not name the verb in upper case: %r"
                        % str(exc))
    if transport.calls:
        problems.append("%d request(s) reached the transport"
                        % len(transport.calls))
    suite.record(GC, "layer2-a-lowercase-verb-is-still-a-write", problems,
                 detail=[str(exc or "<allowed>"),
                         "`method.upper() != \"GET\"`, not `method != \"GET\"` "
                         "-- the check is on the operation, not on the spelling "
                         "the caller happened to use"])

    client, transport = client_with(
        mod, [response(mod, 200, {"ok": True})], read_only_raw="1")
    got, exc = raised(lambda: client.request("GET", "%s/x" % API))
    problems = []
    if exc is not None:
        problems.append("a GET was refused under read-only: %s" % exc)
    if got != {"ok": True}:
        problems.append("the GET returned %r" % (got,))
    suite.record(GC, "layer2-a-GET-is-not-blocked", problems,
                 detail=["ANTI-VACUITY: a backstop that refused everything "
                         "would satisfy the two rows above"])

    client, transport = client_with(
        mod, [response(mod, 200, {"ok": True})], read_only_raw=None)
    got, exc = raised(lambda: client.request("POST", "%s/x" % API, body={}))
    problems = []
    if exc is not None:
        problems.append("a POST was refused with read-only UNSET: %s" % exc)
    if len(transport.calls) != 1:
        problems.append("%d request(s) reached the transport"
                        % len(transport.calls))
    suite.record(GC, "layer2-is-silent-when-read-only-is-unset", problems,
                 detail=["ANTI-VACUITY: the backstop must not be a second, "
                         "permanent ban on writing"])

    # -- the DERIVED set, which is the one that matters -------------------
    verbs, errors = sweep_verbs(mod, profile)

    suite.record(GC, "sweep-covers-every-handler",
                 problem_if(set(SUBCOMMAND_ARGV) != set(mod.HANDLERS),
                            "SUBCOMMAND_ARGV and HANDLERS disagree: only in "
                            "the table %s, only in HANDLERS %s"
                            % (sorted(set(SUBCOMMAND_ARGV) - set(mod.HANDLERS)),
                               sorted(set(mod.HANDLERS) - set(SUBCOMMAND_ARGV)))),
                 detail=["%d subcommand(s)" % len(mod.HANDLERS),
                         "without this the sweep below could go quiet on a new "
                         "subcommand and still report agreement"])

    problems = []
    problems += ["%s raised %s" % (name, why) for name, why in sorted(errors.items())]
    problems += ["%s made no request at all" % name
                 for name, methods in sorted(verbs.items()) if not methods]
    suite.record(GC, "sweep-every-subcommand-ran-and-made-a-request", problems,
                 detail=["%-16s %s" % (name, " ".join(verbs.get(name) or []))
                         for name in sorted(verbs)])

    measured = sorted(name for name, methods in verbs.items()
                      if any(m.upper() != "GET" for m in methods))
    suite.record(GC, "write-commands-equals-the-measured-non-GET-set",
                 check_write_commands(mod.WRITE_COMMANDS, measured),
                 detail=["declared: %r" % (tuple(mod.WRITE_COMMANDS),),
                         "measured: %r" % (tuple(measured),),
                         "asserted against the HANDLER TABLE, never against a "
                         "typed list: a typed list would agree with itself "
                         "while both drifted away from the code"])
    return measured


# ---------------------------------------------------------------------------
# D. the merge gate: four refusals and their order
# ---------------------------------------------------------------------------

VETO = {"summaryMessage": "Not enough approvals",
        "detailedMessage": "2 of 3 required reviewers have approved"}

# The three independent ways gate 3 blocks.  Each is its own case in BOTH
# directions -- blocked without --ignore-vetoes, allowed with it -- because an
# implementation that only ever looked at `outcome` would pass a combined row
# that happened to set all three.
BLOCKING = [
    ("outcome-not-clean",
     {"outcome": "CONFLICTED", "conflicted": False, "vetoes": []}),
    ("a-veto-with-a-clean-outcome",
     {"outcome": "CLEAN", "conflicted": False, "vetoes": [dict(VETO)]}),
    ("conflicted-with-a-clean-outcome",
     {"outcome": "CLEAN", "conflicted": True, "vetoes": []}),
]


def merge_run(mod, profile, tail, script):
    """cmd_pr_merge over a scripted transport."""
    return drive(mod, ["pr-merge", str(PR_ID)] + list(tail), script,
                 profile=profile)


def merged_script(mod, pull_request=None, check=None):
    """The three responses a merge that passes every gate consumes."""
    return [response(mod, 200, dict(check or CLEAN_MERGE)),
            response(mod, 200, dict(pull_request or PULL_REQUEST)),
            response(mod, 200, dict(PULL_REQUEST, state="MERGED"))]


def group_d(suite, mod, profile):
    # -- gate 1 ------------------------------------------------------------
    code, _out, err, calls = main_run(mod, ["pr-merge", str(PR_ID), "--yes"],
                                      env={"BITBUCKET_READ_ONLY": "1"})
    problems = []
    if code != 2:
        problems.append("exit %r, want 2" % code)
    if calls:
        problems.append("%d request(s) before refusing" % calls)
    suite.record(GD, "gate1-read-only-refuses-before-a-single-read", problems,
                 detail=[err.strip(), "requests: %d" % calls,
                         "checked in main() before the client is constructed, "
                         "so it cannot reach the network even by accident"])

    # -- gate 2 ------------------------------------------------------------
    code, exc, transport, _out, _err = merge_run(mod, profile, [], [])
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a merge without --yes returned %r / raised %r"
                        % (code, exc))
    else:
        problems += ["the refusal omits %r" % t for t in missing_tokens(
            str(exc), ["--yes", "no later call can undo"])]
    if transport.calls:
        problems.append("%d request(s) were made before refusing: %r"
                        % (len(transport.calls), transport.methods))
    suite.record(GD, "gate2-without-yes-refuses-with-zero-transport-calls",
                 problems,
                 detail=[str(exc or "<allowed>"),
                         "requests: %d" % len(transport.calls),
                         "asserted as ZERO CALLS, not as `the first statement "
                         "in the function`: _target() legitimately runs first "
                         "and the row below is what that looks like"])

    args = parse_args(mod, connected(
        ["pr-merge", str(PR_ID), "--project", "not a key", "--repo", REPO,
         "--profile", profile]))
    client, transport = client_with(mod, [])
    with captured() as (_out, _err):
        _got, exc = raised(lambda: mod.cmd_pr_merge(args, client))
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("an invalid project key was accepted: %r" % exc)
    elif "not a project key" not in str(exc):
        problems.append("refused, but as the --yes gate: %r" % str(exc))
    if transport.calls:
        problems.append("%d request(s) were made" % len(transport.calls))
    suite.record(GD, "gate2-target-resolution-legitimately-precedes-it",
                 problems,
                 detail=[str(exc or "<accepted>"),
                         "--yes was ALSO absent here and the profile error is "
                         "what came back, which is why the row above is "
                         "written as a measurement of the network and not as a "
                         "claim about statement order"])

    # -- gate 3 ------------------------------------------------------------
    for label, check in BLOCKING:
        code, exc, transport, _out, _err = merge_run(
            mod, profile, ["--yes"], [response(mod, 200, dict(check))])
        problems = []
        if not isinstance(exc, mod.SetupError):
            problems.append("the pre-flight did not block: returned %r, "
                            "raised %r" % (code, exc))
        else:
            problems += ["the refusal omits %r" % t for t in missing_tokens(
                str(exc), ["--ignore-vetoes", "outcome=%s" % check["outcome"]])]
        if [c.method for c in transport.calls] != ["GET"]:
            problems.append("requests were %r, want exactly one GET"
                            % transport.methods)
        suite.record(GD, "gate3-%s-blocks" % label, problems,
                     detail=[str(exc or "<allowed>"),
                             "pre-flight: %r" % (check,),
                             "requests: %r" % (transport.methods,)])

    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes"], [response(mod, 200, {"vetoes": []})])
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a pre-flight with NO outcome was treated as "
                        "mergeable: returned %r, raised %r" % (code, exc))
    elif "outcome=UNKNOWN" not in str(exc):
        problems.append("it blocked, but not as UNKNOWN: %r" % str(exc))
    suite.record(GD, "gate3-a-missing-outcome-defaults-to-unknown-and-blocks",
                 problems,
                 detail=[str(exc or "<allowed>"),
                         "fail-closed: an answer this script cannot read is "
                         "not an answer that says yes"])

    code, exc, transport, out, _err = merge_run(
        mod, profile, ["--yes"], merged_script(mod))
    problems = []
    if exc is not None:
        problems.append("a CLEAN pre-flight was blocked anyway: %s" % exc)
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if transport.methods != ["GET", "GET", "POST"]:
        problems.append("requests were %r" % transport.methods)
    suite.record(GD, "gate3-clean-with-nothing-against-it-proceeds", problems,
                 detail=["requests: %r" % transport.methods,
                         "ANTI-VACUITY: the three rows above would pass just "
                         "as well against a gate that refused every merge"])

    for label, check in BLOCKING:
        code, exc, transport, _out, _err = merge_run(
            mod, profile, ["--yes", "--ignore-vetoes"],
            merged_script(mod, check=check))
        problems = []
        if exc is not None:
            problems.append("--ignore-vetoes did not get past it: %s" % exc)
        if code != 0:
            problems.append("exit %r, want 0" % code)
        if "POST" not in transport.methods:
            problems.append("no POST was sent: %r" % transport.methods)
        suite.record(GD, "gate3-ignore-vetoes-passes-%s" % label, problems,
                     detail=["requests: %r" % transport.methods,
                             "the override is needed for ALL THREE conditions, "
                             "not only for a literal veto list"])

    # -- gate 4 ------------------------------------------------------------
    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes", "--version", "3"],
        [response(mod, 200, dict(CLEAN_MERGE)),
         response(mod, 200, dict(PULL_REQUEST, version=7))])
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a stale --version was accepted: returned %r, raised "
                        "%r" % (code, exc))
    else:
        problems += ["the refusal omits %r" % t
                     for t in missing_tokens(str(exc), ["--version 3", "7"])]
    if "POST" in transport.methods:
        problems.append("a POST was sent despite the version mismatch: %r"
                        % transport.methods)
    if transport.methods != ["GET", "GET"]:
        problems.append("requests were %r, want the pre-flight GET and the "
                        "pull-request GET and nothing else" % transport.methods)
    suite.record(GD, "gate4-a-stale-version-refuses-and-never-posts", problems,
                 detail=[str(exc or "<allowed>"),
                         "requests: %r" % transport.methods,
                         "exactly one GET of the PULL REQUEST, preceded by the "
                         "pre-flight GET gate 3 already made, and ZERO POSTs "
                         "-- the refusal is local, so the remote race never "
                         "happens"])

    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes", "--version", "7"], merged_script(mod))
    body = transport.calls[-1].body if transport.calls else None
    problems = []
    if exc is not None:
        problems.append("an agreeing --version was refused: %s" % exc)
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if (body or {}).get("version") != 7:
        problems.append("the POST carried version %r" % (body or {}).get("version"))
    suite.record(GD, "gate4-an-agreeing-version-lets-the-merge-through",
                 problems, detail=["POST body: %r" % (body,)])

    problems = []
    seen = []
    for server_version in (9, 41):
        _code, _exc, transport, _out, _err = merge_run(
            mod, profile, ["--yes"],
            merged_script(mod, pull_request=dict(PULL_REQUEST,
                                                 version=server_version)))
        body = transport.calls[-1].body if transport.calls else None
        seen.append((server_version, (body or {}).get("version")))
        if (body or {}).get("version") != server_version:
            problems.append("server held %r, the POST carried %r"
                            % (server_version, (body or {}).get("version")))
    suite.record(GD, "gate4-the-body-carries-the-version-just-READ", problems,
                 detail=["(server version, version sent): %r" % (seen,),
                         "no --version was passed in either run: the value is "
                         "taken from the answer to the GET one statement "
                         "earlier, which is what makes a 409 here impossible "
                         "for any reason other than a genuine race"])

    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes"],
        [response(mod, 200, dict(CLEAN_MERGE)),
         response(mod, 200, dict(PULL_REQUEST, version="7"))])
    problems = []
    if not isinstance(exc, mod.BitbucketError):
        problems.append("a non-numeric version was accepted: %r" % exc)
    if "POST" in transport.methods:
        problems.append("a POST was sent: %r" % transport.methods)
    suite.record(GD, "gate4-a-non-numeric-version-refuses-the-write", problems,
                 detail=[str(exc or "<allowed>"),
                         "an optimistic lock that is not an integer is not a "
                         "lock, and writing without one is how a merge lands "
                         "on a pull request that moved"])

    # -- --dry-run sits AFTER all four ------------------------------------
    code, exc, transport, _out, _err = merge_run(mod, profile, ["--dry-run"], [])
    problems = []
    if not isinstance(exc, mod.SetupError) or "--yes" not in str(exc or ""):
        problems.append("a dry run skipped the --yes gate: %r / %r" % (code, exc))
    if transport.calls:
        problems.append("%d request(s) were made" % len(transport.calls))
    suite.record(GD, "dry-run-still-requires-yes", problems,
                 detail=[str(exc or "<allowed>")])

    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes", "--dry-run"],
        [response(mod, 200, dict(BLOCKING[0][1]))])
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a dry run skipped the pre-flight: %r / %r" % (code, exc))
    if "POST" in transport.methods:
        problems.append("a POST was sent: %r" % transport.methods)
    suite.record(GD, "dry-run-still-requires-a-clean-preflight", problems,
                 detail=[str(exc or "<allowed>"),
                         "requests: %r" % transport.methods])

    code, exc, transport, _out, _err = merge_run(
        mod, profile, ["--yes", "--dry-run", "--version", "3"],
        [response(mod, 200, dict(CLEAN_MERGE)),
         response(mod, 200, dict(PULL_REQUEST, version=7))])
    suite.record(GD, "dry-run-still-asserts-the-version",
                 problem_if(not isinstance(exc, mod.SetupError),
                            "a dry run skipped the version assertion: %r / %r"
                            % (code, exc)),
                 detail=[str(exc or "<allowed>"),
                         "a dry run that printed a body built on a version the "
                         "server no longer holds would confirm the spelling "
                         "and nothing else"])

    code, exc, transport, out, _err = merge_run(
        mod, profile, ["--yes", "--dry-run", "--message", "shipped it",
                       "--strategy", "squash"],
        [response(mod, 200, dict(CLEAN_MERGE)),
         response(mod, 200, dict(PULL_REQUEST))])
    printed, parse_error = raised(
        lambda: json.loads(fenced_block(out, "json")), ValueError)
    problems = []
    if exc is not None:
        problems.append("the dry run raised %s" % exc)
    if code != 0:
        problems.append("exit %r, want 0" % code)
    if "POST" in transport.methods:
        problems.append("a dry run SENT the merge: %r" % transport.methods)
    if BASE + PR_BASE + "/%d/merge" % PR_ID not in out:
        problems.append("the exact URL is not printed")
    if parse_error is not None:
        problems.append("the body is not a fenced ```json block that parses")
    elif printed != {"version": 7, "message": "shipped it",
                     "strategyId": "squash"}:
        problems.append("printed body %r" % (printed,))
    suite.record(GD, "dry-run-prints-the-request-and-sends-nothing", problems,
                 detail=out.splitlines()
                 + ["requests: %r" % transport.methods,
                    "the two GETs are the gates doing their job; the write "
                    "itself is what --dry-run withholds"])


# ---------------------------------------------------------------------------
# E. pr-builds: the verdict, the blank state, and the exit code
# ---------------------------------------------------------------------------

def builds_run(mod, profile, rows, tail=(), pull_request=None):
    """cmd_pr_builds over a two-response script."""
    script = [response(mod, 200, dict(pull_request or PULL_REQUEST)),
              response(mod, 200, {"values": list(rows), "isLastPage": True})]
    return drive(mod, ["pr-builds", str(PR_ID)] + list(tail), script,
                 profile=profile)


def build_row(state, name="CI"):
    row = {"name": name, "url": "https://ci.example/1"}
    if state is not None:
        row["state"] = state
    return row


def group_e(suite, mod, profile):
    code, exc, transport, out, err = builds_run(
        mod, profile, [build_row("SUCCESSFUL")])
    problems = []
    if exc is not None:
        problems.append("raised %s" % exc)
    if len(transport.calls) != 2:
        problems.append("%d request(s), want 2" % len(transport.calls))
    else:
        if transport.calls[0].path != BASE_PATH + PR_BASE + "/%d" % PR_ID:
            problems.append("the first request was %s" % transport.calls[0].path)
        second = transport.calls[1].path
        if not second.startswith(BASE_PATH + "/rest/build-status/1.0/commits/"):
            problems.append("the second request is not on the build-status "
                            "root: %s" % second)
    suite.record(GE, "two-requests-the-second-on-the-third-rest-root", problems,
                 detail=[c.path for c in transport.calls]
                 + ["build status is keyed by COMMIT and lives under its own "
                    "REST root; assuming everything pull-request shaped hangs "
                    "off /rest/api/1.0 earns a 404 that reads like a missing "
                    "repository"])

    other = "f" * 40
    code, exc, transport, out, err = builds_run(
        mod, profile, [build_row("SUCCESSFUL")],
        pull_request=dict(PULL_REQUEST,
                          fromRef=dict(PULL_REQUEST["fromRef"],
                                       latestCommit=other)))
    second = transport.calls[1].path if len(transport.calls) > 1 else ""
    problems = []
    if other not in second:
        problems.append("the sha asked about was not the server's: %s" % second)
    if SHA in second:
        problems.append("a sha from somewhere other than this response was used")
    suite.record(GE, "the-sha-comes-from-the-servers-fromRef-latestCommit",
                 problems,
                 detail=["asked about: %s" % second,
                         "`pr-builds` takes an ID and nothing else -- there is "
                         "no sha flag to get wrong -- because a build status "
                         "answered for a commit the pull request has moved past "
                         "is worse than no answer"])

    code, exc, transport, _out, _err = builds_run(
        mod, profile, [build_row("SUCCESSFUL")],
        pull_request=dict(PULL_REQUEST, fromRef={"displayId": "my-branch"}))
    problems = []
    if not isinstance(exc, mod.BitbucketError):
        problems.append("a pull request with no source tip was accepted: %r"
                        % exc)
    if len(transport.calls) != 1:
        problems.append("%d request(s); the build lookup should not have "
                        "happened" % len(transport.calls))
    suite.record(GE, "a-pull-request-with-no-source-tip-is-a-finding", problems,
                 detail=[str(exc or "<accepted>")])

    suite.record(GE, "build-verdict-precedence-table",
                 check_verdict(mod._build_verdict),
                 detail=["%r -> %s" % (states, want)
                         for states, want in VERDICT_CASES]
                 + ["worst member first: a pull request with a green build and "
                    "a red one is not green, and a caller reading only the "
                    "first row would believe otherwise"])

    code, exc, transport, out, err = builds_run(
        mod, profile, [build_row(None), build_row("   ", name="Nightly")])
    problems = []
    if exc is not None:
        problems.append("raised %s" % exc)
    if "| verdict | UNKNOWN |" not in out:
        problems.append("the verdict row is not in the rendered document")
    if "verdict" not in out:
        problems.append("the word `verdict` does not appear at all")
    suite.record(GE, "a-blank-state-becomes-UNKNOWN-and-the-row-stays-VISIBLE",
                 problems,
                 detail=out.splitlines()
                 + ["the defect was not a wrong verdict, it was NO verdict: an "
                    "empty string fell through _build_verdict's first-state "
                    "fallback and md_kv drops blank values, so the row "
                    "disappeared from the output entirely"])

    md_code, _exc, _t, md_out, _err = builds_run(
        mod, profile, [build_row("SUCCESSFUL"), build_row("FAILED")])
    js_code, _exc, _t, js_out, _err = builds_run(
        mod, profile, [build_row("SUCCESSFUL"), build_row("FAILED")],
        tail=["--json"])
    payload, parse_error = raised(lambda: json.loads(js_out), ValueError)
    problems = []
    if md_code != 1:
        problems.append("Markdown exited %r for a FAILED verdict, want 1"
                        % md_code)
    if js_code != 1:
        problems.append("--json exited %r for a FAILED verdict, want 1"
                        % js_code)
    if md_code != js_code:
        problems.append("the two formats DISAGREE: %r vs %r"
                        % (md_code, js_code))
    if parse_error is not None:
        problems.append("--json stdout is not one JSON document")
    elif (payload or {}).get("verdict") != "FAILED":
        problems.append("the JSON verdict is %r" % (payload or {}).get("verdict"))
    suite.record(GE, "FAILED-exits-1-in-BOTH-markdown-and-json", problems,
                 detail=["markdown exit: %r" % md_code,
                         "--json   exit: %r" % js_code,
                         "PINNED IN ONE CASE on purpose. The --json branch "
                         "used to return a hard-coded OK, so a CI gate piping "
                         "this into jq was silently always green -- and a case "
                         "per format, passing separately, is exactly what that "
                         "regression would survive"])

    md_code, _exc, _t, _md_out, _err = builds_run(
        mod, profile, [build_row("SUCCESSFUL")])
    js_code, _exc, _t, _js_out, _err = builds_run(
        mod, profile, [build_row("SUCCESSFUL")], tail=["--json"])
    problems = []
    if (md_code, js_code) != (0, 0):
        problems.append("a SUCCESSFUL verdict exited %r / %r, want 0 / 0"
                        % (md_code, js_code))
    suite.record(GE, "a-green-build-exits-0-in-both-formats", problems,
                 detail=["ANTI-VACUITY: a command that always exited 1 would "
                         "satisfy the row above and gate nothing"])

    bad = ["abc", "", "../../etc/passwd", "a" * 65, "g" * 7, "a1b2c3d/../x",
           "HEAD", "a1b2c3d%2fx"]
    problems = []
    for value in bad:
        client, transport = client_with(mod, [])
        _got, exc = raised(lambda v=value: client.build_statuses(v))
        if not isinstance(exc, mod.SetupError):
            problems.append("%r was accepted (raised %r)" % (value, exc))
        if transport.calls:
            problems.append("%r reached the network" % value)
    suite.record(GE, "the-commit-sha-gate-refuses-a-non-sha", problems,
                 detail=["refused: %r" % bad,
                         "this value reaches a URL PATH and arrives from the "
                         "SERVER's own answer, so what is modelled is a "
                         "hostile instance, not a caller"])

    good = ["a1b2c3d", "0" * 64, SHA, SHA.upper()]
    problems = []
    for value in good:
        client, transport = client_with(
            mod, [response(mod, 200, {"values": [], "isLastPage": True})])
        _got, exc = raised(lambda v=value: client.build_statuses(v))
        if exc is not None:
            problems.append("%r was refused: %s" % (value, exc))
    suite.record(GE, "the-commit-sha-gate-accepts-both-hash-widths", problems,
                 detail=["accepted: 7 hex (git's shortest abbreviation) "
                         "through 64 (a SHA-256 object id), either case",
                         "ANTI-VACUITY: a gate that refused everything would "
                         "satisfy the row above and break every instance"])

    code, exc, transport, _out, _err = builds_run(
        mod, profile, [],
        pull_request=dict(PULL_REQUEST,
                          fromRef=dict(PULL_REQUEST["fromRef"],
                                       latestCommit="../../../etc/passwd")))
    problems = []
    if not isinstance(exc, mod.SetupError):
        problems.append("a traversal-shaped sha from the server was accepted: "
                        "%r" % exc)
    if len(transport.calls) != 1:
        problems.append("%d request(s); only the pull-request read should have "
                        "happened" % len(transport.calls))
    suite.record(GE, "a-non-sha-in-the-servers-own-answer-stops-the-command",
                 problems,
                 detail=[str(exc or "<accepted>"),
                         "requests: %r" % transport.methods])


# ---------------------------------------------------------------------------
# F. the per-subcommand request contract
# ---------------------------------------------------------------------------

def full(path):
    """A repo-relative REST path with the base URL's CONTEXT PATH in front.

    Spelled out rather than trimmed away: /bitbucket is precisely what urljoin
    would have eaten, so every path assertion here carries it on purpose.
    """
    return BASE_PATH + path


REF_CASES = [
    ("master", "refs/heads/master"),
    ("  master  ", "refs/heads/master"),
    ("feature/PROJ-1", "refs/heads/feature/PROJ-1"),
    ("refs/heads/master", "refs/heads/master"),
    ("refs/tags/v1.2.3", "refs/tags/v1.2.3"),
]


def group_f(suite, mod, profile):
    banner = {"displayName": "Bitbucket", "version": "9.4.23",
              "buildNumber": "9004023"}

    code, exc, transport, out, _err = drive(
        mod, ["version"], [response(mod, 200, banner)])
    call = transport.calls[0] if transport.calls else None
    problems = []
    if exc is not None or code != 0:
        problems.append("exit %r, raised %r" % (code, exc))
    if len(transport.calls) != 1:
        problems.append("%d request(s), want 1" % len(transport.calls))
    elif (call.method, call.path) != ("GET",
                                      full(API + "/application-properties")):
        problems.append("sent %s %s" % (call.method, call.path))
    suite.record(GF, "version-is-one-GET-on-application-properties", problems,
                 detail=["%s %s" % (call.method, call.path) if call else "-"])

    code, exc, transport, out, err = drive(
        mod, ["whoami"],
        [response(mod, 200, banner, headers={"X-AUSERNAME": USERNAME})])
    problems = []
    if exc is not None or code != 0:
        problems.append("exit %r, raised %r" % (code, exc))
    if len(transport.calls) != 1:
        problems.append("%d request(s), want 1" % len(transport.calls))
    if USERNAME not in out:
        problems.append("the username is not in the document: %r" % out)
    if TOKEN in out + err:
        problems.append("THE TOKEN VALUE WAS PRINTED")
    suite.record(GF, "whoami-reads-the-username-off-the-same-response",
                 problems,
                 detail=out.splitlines()
                 + ["this API has no `myself` endpoint; the authenticated "
                    "username rides on the header of every response, so "
                    "`whoami` makes the banner call and reads X-AUSERNAME"])

    code, exc, transport, _out, _err = drive(
        mod, ["repo"], [response(mod, 200, {"id": 42, "name": "Repo"})],
        profile=profile)
    call = transport.calls[0] if transport.calls else None
    suite.record(GF, "repo-path",
                 problem_if(
                     not call or (call.method, call.path)
                     != ("GET", full("%s/projects/%s/repos/%s"
                                     % (API, PROJECT, REPO))),
                     "sent %s" % (call,)),
                 detail=["%s %s" % (call.method, call.path) if call else "-"])

    page = {"values": [dict(PULL_REQUEST)], "isLastPage": True}
    code, exc, transport, _out, _err = drive(
        mod, ["pr-list"], [response(mod, 200, page)], profile=profile)
    call = transport.calls[0] if transport.calls else None
    problems = []
    if not call or call.method != "GET" or call.path != full(PR_BASE):
        problems.append("sent %s" % (call,))
    else:
        want = {"state": "OPEN", "order": "NEWEST", "start": "0",
                "limit": str(mod.PAGE_SIZE)}
        if call.query != want:
            problems.append("query %r, want %r" % (call.query, want))
    suite.record(GF, "pr-list-query", problems,
                 detail=["%s %s" % (call.method, call.url) if call else "-",
                         "`at` and `direction` are unset and therefore ABSENT, "
                         "not empty: api_url drops a None-valued key"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-list", "--at", "master"], [response(mod, 200, page)],
        profile=profile)
    call = transport.calls[0] if transport.calls else None
    suite.record(GF, "pr-list-expands-at-to-a-fully-qualified-ref",
                 problem_if(not call or call.query.get("at")
                            != "refs/heads/master",
                            "at=%r" % (call.query.get("at") if call else None)),
                 detail=["query: %r" % (call.query if call else None),
                         "the endpoint answers a short branch name with an "
                         "unfiltered list rather than an error"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-list", "--author", "jdoe"], [response(mod, 200, page)],
        profile=profile)
    call = transport.calls[0] if transport.calls else None
    query = call.query if call else {}
    problems = []
    if query.get("username.1") != "jdoe":
        problems.append("username.1=%r" % query.get("username.1"))
    if query.get("role.1") != "AUTHOR":
        problems.append("role.1=%r" % query.get("role.1"))
    if "author" in query:
        problems.append("an `author` parameter was sent; this endpoint has "
                        "none and silently ignores it")
    suite.record(GF, "pr-list-author-is-the-numbered-participant-filter",
                 problems,
                 detail=["query: %r" % query,
                         "the numbering has to start at 1 and stay contiguous; "
                         "a caller who expects `author=` gets an unfiltered "
                         "list back and no error at all"])

    for name, tail, want_path in (
            ("pr-get", ["pr-get", str(PR_ID)], PR_BASE + "/%d" % PR_ID),
            ("pr-activities", ["pr-activities", str(PR_ID)],
             PR_BASE + "/%d/activities" % PR_ID),
            ("pr-mergeability", ["pr-mergeability", str(PR_ID)],
             PR_BASE + "/%d/merge" % PR_ID)):
        script = [response(mod, 200, dict(PULL_REQUEST))] if name == "pr-get" \
            else [response(mod, 200, {"values": [], "isLastPage": True})] \
            if name == "pr-activities" else [response(mod, 200,
                                                      dict(CLEAN_MERGE))]
        code, exc, transport, _out, _err = drive(mod, tail, script,
                                                 profile=profile)
        call = transport.calls[0] if transport.calls else None
        problems = []
        if exc is not None or code != 0:
            problems.append("exit %r, raised %r" % (code, exc))
        if not call or (call.method, call.path) != ("GET", full(want_path)):
            problems.append("sent %s" % (call,))
        suite.record(GF, "%s-is-a-GET-on-its-own-path" % name, problems,
                     detail=["%s %s" % (call.method, call.path) if call
                             else "-"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-reviewers", "--from", "my-branch"],
        [response(mod, 200, {"id": 42}),
         response(mod, 200, [{"name": "areviewer"}])], profile=profile)
    problems = []
    if len(transport.calls) != 2:
        problems.append("%d request(s), want 2" % len(transport.calls))
    else:
        first, second = transport.calls
        if first.path != full("%s/projects/%s/repos/%s" % (API, PROJECT, REPO)):
            problems.append("the repository read went to %s" % first.path)
        want = full("/rest/default-reviewers/1.0/projects/%s/repos/%s/reviewers"
                    % (PROJECT, REPO))
        if second.path != want:
            problems.append("the reviewer read went to %s, want %s"
                            % (second.path, want))
        want_query = {"sourceRepoId": "42", "targetRepoId": "42",
                      "sourceRefId": "refs/heads/my-branch",
                      "targetRefId": "refs/heads/master"}
        if second.query != want_query:
            problems.append("query %r, want %r" % (second.query, want_query))
    suite.record(GF, "pr-reviewers-costs-two-requests-on-two-rest-roots",
                 problems,
                 detail=[c.url for c in transport.calls]
                 + ["the numeric repository id is why the first call exists: "
                    "this endpoint wants ids, not slugs, and it is the one "
                    "endpoint in the file that does not live under "
                    "/rest/api/1.0"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-create", "--from", "my-branch", "--title", "a title",
              "--description", "why", "--reviewer", "alice",
              "--reviewer", "bob"],
        [response(mod, 200, dict(PULL_REQUEST, id=1212))], profile=profile)
    call = transport.calls[0] if transport.calls else None
    want_repo = {"slug": REPO, "project": {"key": PROJECT}}
    want_body = {"title": "a title", "description": "why",
                 "fromRef": {"id": "refs/heads/my-branch",
                             "repository": dict(want_repo)},
                 "toRef": {"id": "refs/heads/master",
                           "repository": dict(want_repo)},
                 "reviewers": [{"user": {"name": "alice"}},
                               {"user": {"name": "bob"}}]}
    problems = []
    if not call or (call.method, call.path) != ("POST", full(PR_BASE)):
        problems.append("sent %s" % (call,))
    elif call.body != want_body:
        problems.append("body %r, want %r" % (call.body, want_body))
    suite.record(GF, "pr-create-body", problems,
                 detail=["POST %s" % (call.path if call else "-"),
                         "body: %r" % (call.body if call else None)])

    problems = []
    body = call.body if call else {}
    for side in ("fromRef", "toRef"):
        if (body.get(side) or {}).get("repository") != want_repo:
            problems.append("%s carries repository %r"
                            % (side, (body.get(side) or {}).get("repository")))
    suite.record(GF, "pr-create-both-refs-carry-a-repository-object", problems,
                 detail=["that nesting is how a FORK's pull request is "
                         "expressed, and the endpoint wants the shape either "
                         "way -- including for a same-repo pull request, which "
                         "is what this one is"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-comment", str(PR_ID), "looks good"],
        [response(mod, 200, {"id": 9001})], profile=profile)
    call = transport.calls[0] if transport.calls else None
    problems = []
    if not call or (call.method, call.path) != (
            "POST", full(PR_BASE + "/%d/comments" % PR_ID)):
        problems.append("sent %s" % (call,))
    elif call.body != {"text": "looks good"}:
        problems.append("body %r" % (call.body,))
    suite.record(GF, "pr-comment-body", problems,
                 detail=["POST %s" % (call.path if call else "-"),
                         "body: %r" % (call.body if call else None),
                         "only the un-anchored form: an inline comment needs a "
                         "diff anchor carrying two commit hashes, a line "
                         "number and a line type"])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-comment", str(PR_ID), "a reply", "--parent", "77"],
        [response(mod, 200, {"id": 9002})], profile=profile)
    call = transport.calls[0] if transport.calls else None
    suite.record(GF, "pr-comment-parent-is-an-object-not-an-id",
                 problem_if(not call or call.body != {"text": "a reply",
                                                      "parent": {"id": 77}},
                            "body %r" % (call.body if call else None)),
                 detail=["body: %r" % (call.body if call else None)])

    code, exc, transport, _out, _err = drive(
        mod, ["pr-approve", str(PR_ID)],
        [response(mod, 200, {"displayName": "Bitbucket"},
                  headers={"X-AUSERNAME": USERNAME}),
         response(mod, 200, dict(PULL_REQUEST)),
         response(mod, 200, {"status": "APPROVED",
                             "user": {"name": USERNAME}})], profile=profile)
    problems = []
    if exc is not None or code != 0:
        problems.append("exit %r, raised %r" % (code, exc))
    if len(transport.calls) != 3:
        problems.append("%d request(s), want 3" % len(transport.calls))
    else:
        first, second, third = transport.calls
        if first.path != full(API + "/application-properties"):
            problems.append("the identity read went to %s" % first.path)
        if second.path != full(PR_BASE + "/%d" % PR_ID):
            problems.append("the pull-request read went to %s" % second.path)
        want = full(PR_BASE + "/%d/participants/%s" % (PR_ID, USERNAME))
        if (third.method, third.path) != ("PUT", want):
            problems.append("sent %s %s, want PUT %s"
                            % (third.method, third.path, want))
        if third.body != {"status": "APPROVED", "lastReviewedCommit": SHA}:
            problems.append("body %r" % (third.body,))
    suite.record(GF, "pr-approve-costs-three-requests", problems,
                 detail=["%s %s" % (c.method, c.path) for c in transport.calls]
                 + ["who the token is, what the pull request currently says, "
                    "then the participant PUT -- the deprecated POST "
                    ".../approve is not used, and the concurrency check moved "
                    "from a version query parameter to lastReviewedCommit"])

    for action, tail, want_body in (
            ("decline", ["pr-decline", str(PR_ID), "--comment", "superseded"],
             {"version": 7, "comment": "superseded"}),
            ("reopen", ["pr-reopen", str(PR_ID)], {"version": 7})):
        code, exc, transport, _out, _err = drive(
            mod, tail,
            [response(mod, 200, dict(PULL_REQUEST)),
             response(mod, 200, dict(PULL_REQUEST, state="DECLINED"))],
            profile=profile)
        problems = []
        if exc is not None or code != 0:
            problems.append("exit %r, raised %r" % (code, exc))
        if len(transport.calls) != 2:
            problems.append("%d request(s), want 2" % len(transport.calls))
        else:
            first, second = transport.calls
            if (first.method, first.path) != ("GET",
                                              full(PR_BASE + "/%d" % PR_ID)):
                problems.append("the re-read was %s %s"
                                % (first.method, first.path))
            want = full(PR_BASE + "/%d/%s" % (PR_ID, action))
            if (second.method, second.path) != ("POST", want):
                problems.append("sent %s %s, want POST %s"
                                % (second.method, second.path, want))
            if second.body != want_body:
                problems.append("body %r, want %r" % (second.body, want_body))
        suite.record(GF, "pr-%s-re-reads-the-pull-request-first" % action,
                     problems,
                     detail=["%s %s" % (c.method, c.path)
                             for c in transport.calls]
                     + ["the optimistic-lock version is never an input: a "
                        "stale one comes back as a 409 that reads like a merge "
                        "conflict"])

    problems = []
    for raw, want in REF_CASES:
        got, exc = raised(lambda r=raw: mod.normalize_ref(r))
        if exc is not None:
            problems.append("%r raised %s" % (raw, exc))
        elif got != want:
            problems.append("%r -> %r, want %r" % (raw, got, want))
    for raw in ("", "   ", "\t\n"):
        _got, exc = raised(lambda r=raw: mod.normalize_ref(r))
        if not isinstance(exc, mod.SetupError):
            problems.append("%r was accepted as a branch name" % raw)
    suite.record(GF, "normalize-ref-table", problems,
                 detail=["%r -> %r" % pair for pair in REF_CASES]
                 + ["empty/whitespace refused",
                    "the create endpoint rejects a short branch name with a "
                    "400 whose message does not mention refs at all, so the "
                    "expansion happens here rather than in the caller's head"])

    transport = RawTransport([response(mod, 200, {"ok": True}),
                              response(mod, 200, {"ok": True})])
    client = mod.Bitbucket(make_cfg(mod), fetch=transport,
                           sleep=lambda _s: None)
    client.request("GET", "%s/x" % API)
    client.request("POST", "%s/y" % API, body={"a": 1})
    problems = []
    get_call, post_call = transport.calls
    if "Content-Type" in get_call.headers:
        problems.append("a GET carried a Content-Type")
    if post_call.headers.get("Content-Type") != "application/json":
        problems.append("the POST body went out as %r"
                        % post_call.headers.get("Content-Type"))
    if post_call.body != b'{"a": 1}':
        problems.append("the raw body is %r" % (post_call.body,))
    for call in transport.calls:
        if call.headers.get("Authorization") != "Bearer " + TOKEN:
            problems.append("Authorization: %r"
                            % call.headers.get("Authorization"))
        if call.headers.get("Accept") != "application/json":
            problems.append("Accept: %r" % call.headers.get("Accept"))
        if not (call.headers.get("User-Agent") or "").strip():
            problems.append("no User-Agent")
    suite.record(GF, "headers-bearer-accept-ua-and-content-type-with-a-body",
                 problems,
                 detail=["User-Agent: %s" % get_call.headers.get("User-Agent"),
                         "Bearer only: the same token works as the password "
                         "half of Basic for a PERSONAL token and not for a "
                         "scoped one, so two auth modes would buy nothing"])

    # -- paging ------------------------------------------------------------
    client, transport = client_with(mod, [
        response(mod, 200, {"values": [1, 2], "size": 2, "start": 0,
                            "isLastPage": False, "nextPageStart": 137}),
        response(mod, 200, {"values": [3], "isLastPage": True}),
    ])
    got = list(client.paged("%s/things" % API))
    problems = []
    if got != [1, 2, 3]:
        problems.append("yielded %r" % (got,))
    if len(transport.calls) != 2:
        problems.append("%d request(s), want 2" % len(transport.calls))
    else:
        second = transport.calls[1].query.get("start")
        if second != "137":
            problems.append("the second page asked for start=%r, want the "
                            "server's nextPageStart 137" % second)
        if second == "2":
            problems.append("start + size was computed locally")
    suite.record(GF, "paging-follows-nextPageStart-never-start-plus-size",
                 problems,
                 detail=["starts: %r" % [c.query.get("start")
                                         for c in transport.calls],
                         "the documentation is explicit that item identifiers "
                         "between pages are not guaranteed contiguous, so a "
                         "locally computed offset can skip items or repeat "
                         "them -- 137 here is deliberately not 0 + 2"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [1], "isLastPage": True,
                            "nextPageStart": 99}),
    ])
    got = list(client.paged("%s/things" % API))
    problems = []
    if got != [1]:
        problems.append("yielded %r" % (got,))
    if len(transport.calls) != 1:
        problems.append("%d request(s); isLastPage said stop"
                        % len(transport.calls))
    suite.record(GF, "paging-stops-on-isLastPage-even-with-a-nextPageStart",
                 problems,
                 detail=["a server that sends both is not a hypothetical: the "
                         "cursor is the position, isLastPage is the verdict"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [1, 2], "isLastPage": False,
                            "nextPageStart": 2}),
        response(mod, 200, {"values": [3, 4], "isLastPage": False,
                            "nextPageStart": 4}),
    ])
    got = list(client.paged("%s/things" % API, limit=3))
    problems = []
    if got != [1, 2, 3]:
        problems.append("yielded %r, want the first 3 items" % (got,))
    if len(transport.calls) != 2:
        problems.append("%d request(s), want 2" % len(transport.calls))
    sizes = {c.query.get("limit") for c in transport.calls}
    if sizes != {str(mod.PAGE_SIZE)}:
        problems.append("the wire `limit` followed --limit instead of "
                        "PAGE_SIZE: %r" % sizes)
    suite.record(GF, "paging-limit-counts-items-YIELDED-not-pages-fetched",
                 problems,
                 detail=["yielded: %r" % (got,),
                         "requests: %d" % len(transport.calls),
                         "wire limit: %r" % sizes,
                         "the server caps `limit` at an admin-configured "
                         "ceiling that is not a documented number, so the "
                         "response's own paging is what a sweep believes and "
                         "--limit is applied on this side"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [1, 2]}),
    ])
    got = list(client.paged("%s/things" % API))
    suite.record(GF, "paging-a-missing-isLastPage-is-the-last-page",
                 problem_if(got != [1, 2] or len(transport.calls) != 1,
                            "yielded %r after %d request(s)"
                            % (got, len(transport.calls))),
                 detail=["`page.get(\"isLastPage\", True)` -- the default is "
                         "STOP, so a truncated or unfamiliar envelope ends the "
                         "sweep instead of looping"])

    client, transport = client_with(mod, [
        response(mod, 200, {"values": [1], "isLastPage": False,
                            "nextPageStart": "not-an-int"}),
    ])
    got = list(client.paged("%s/things" % API))
    suite.record(GF, "paging-a-non-integer-nextPageStart-stops-the-sweep",
                 problem_if(got != [1] or len(transport.calls) != 1,
                            "yielded %r after %d request(s)"
                            % (got, len(transport.calls))),
                 detail=["the cursor goes straight into a query parameter; a "
                         "value that is not an integer would be sent verbatim "
                         "and answered with the first page forever"])


# ---------------------------------------------------------------------------
# G. _flatten_reviewers, both shapes
# ---------------------------------------------------------------------------

FLAT = [{"name": "alice", "displayName": "Alice A"},
        {"name": "bob", "displayName": "Bob B"}]

CONDITIONS = [{"id": 1,
               "reviewers": [{"user": {"name": "alice"}},
                             {"user": {"name": "bob"}}]}]


def group_g(suite, mod):
    got = mod._flatten_reviewers(FLAT)
    suite.record(GG, "a-FLAT-array-of-users-must-not-yield-zero-names",
                 problem_if(got != ["alice", "bob"], "yielded %r" % (got,)),
                 detail=["input : %r" % (FLAT,), "output: %r" % (got,),
                         "THE LIVE DEFECT, recorded as its own row: 9.4.23 "
                         "answers this endpoint with a flat user array, the "
                         "published reference describes conditions, and a "
                         "client that believed the document reported `1 "
                         "condition, 0 reviewers` -- a silent zero, "
                         "indistinguishable from a repository that genuinely "
                         "has none configured"])

    got = mod._flatten_reviewers(CONDITIONS)
    suite.record(GG, "a-condition-array-with-nested-reviewers-yields-names",
                 problem_if(got != ["alice", "bob"], "yielded %r" % (got,)),
                 detail=["input : %r" % (CONDITIONS,), "output: %r" % (got,),
                         "the documented shape is accepted too: both, because "
                         "betting on either is how the zero happened"])

    got = mod._flatten_reviewers([{"reviewers": [{"name": "alice"}]}])
    suite.record(GG, "a-nested-reviewer-without-a-user-wrapper-still-resolves",
                 problem_if(got != ["alice"], "yielded %r" % (got,)),
                 detail=["output: %r" % (got,)])

    got = mod._flatten_reviewers([])
    suite.record(GG, "an-empty-array-yields-nothing",
                 problem_if(got != [], "yielded %r" % (got,)),
                 detail=["a repository with no default reviewers is an "
                         "ordinary state, not an error"])

    duplicated = [{"name": "bob"}, {"name": "alice"}, {"name": "bob"},
                  {"slug": "alice"}]
    got = mod._flatten_reviewers(duplicated)
    suite.record(GG, "duplicates-collapse-and-the-order-is-preserved",
                 problem_if(got != ["bob", "alice"], "yielded %r" % (got,)),
                 detail=["input : %r" % (duplicated,), "output: %r" % (got,),
                         "the same user can arrive from several conditions; "
                         "sending a duplicate reviewer is a 400"])

    got = mod._flatten_reviewers([{"slug": "carol"}])
    suite.record(GG, "slug-is-the-fallback-when-name-is-absent",
                 problem_if(got != ["carol"], "yielded %r" % (got,)))

    noisy = ["a string", None, 7, {"displayName": "no name at all"},
             {"name": "alice"}]
    got = mod._flatten_reviewers(noisy)
    suite.record(GG, "entries-that-are-not-user-objects-are-skipped",
                 problem_if(got != ["alice"], "yielded %r" % (got,)),
                 detail=["input : %r" % (noisy,), "output: %r" % (got,),
                         "displayName is deliberately NOT a source: the create "
                         "payload wants `{\"user\": {\"name\": ...}}`, and a "
                         "display name there is a 400"])

    mixed = FLAT[:1] + CONDITIONS
    got = mod._flatten_reviewers(mixed)
    suite.record(GG, "both-shapes-in-one-array-are-handled",
                 problem_if(got != ["alice", "bob"], "yielded %r" % (got,)),
                 detail=["output: %r" % (got,),
                         "the discriminator is a `reviewers` LIST on the "
                         "entry, decided per entry rather than once for the "
                         "whole response"])


# ---------------------------------------------------------------------------
# H. cross-file parity on _profile_path
# ---------------------------------------------------------------------------

# sha256 over the ten sliced lines joined with "\n" AND a trailing newline.
# The convention matters: without the trailing newline the digest differs, and
# a reader re-measuring this by hand would get a number that looks like drift.
PARITY_SHA = "9ca53163f48a861b9eae85dde712f2a0d16f9b2b6250816bae96ca834ea233bd"
PARITY_LINES = 10

DEF_LINE = "def _profile_path("
# BOTH of these moved when the $HOME boundary was repaired, and they are the
# two strings that make this gate LOCATE rather than pin -- so changing them is
# not a tidy-up, it is re-aiming the instrument.  Worth saying out loud: while
# ANCHOR named the old spelling and only one file had moved, the locate row
# below failed with "no `def _profile_path(` carrying ..." and RETURNED, which
# means a one-sided edit TO THE ANCHOR LINE ITSELF is caught by that row and
# never reaches the byte-identity row underneath it.  Both behaviours are the
# gate working; they are just different rows, and a reader chasing a red here
# should start at the top of the group rather than at the digest.
ANCHOR = "here = os.path.realpath(os.getcwd())"
HOME_LINE = "if parent == here or not _within(parent, home):"

# The helper the walk's boundary test CALLS.  It is copied verbatim for the
# same reason the walk is, and it is located separately because it lives above
# `def _profile_path(` and therefore outside the slice: byte-identity of the
# walk alone would let the two files answer the same question differently
# while every row below stayed green.
WITHIN_DEF = "def _within("


def source_lines(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().split("\n")


def line_numbers(lines, needle):
    """1-based line numbers whose STRIPPED text equals `needle`."""
    return [i + 1 for i, line in enumerate(lines) if line.strip() == needle]


def locate_profile_path(lines):
    """(header, slice, 1-based anchor line) for `_profile_path` in `lines`.

    LOCATED, never pinned by line number.  These two functions have already
    moved once (1493 -> 1671 in bitbucket.py in a single day) and will move
    again; a case that pinned the number would fail on an unrelated edit above
    it and say nothing about the property it exists to protect.

    The slice runs from the anchor to the last line of the function, found by
    the first line afterwards that is non-empty and starts at column 0.
    """
    start = next((i for i, line in enumerate(lines)
                  if line.startswith(DEF_LINE)), None)
    if start is None:
        return None, None, None
    anchor = next((i for i in range(start, len(lines))
                   if lines[i].strip() == ANCHOR), None)
    if anchor is None:
        return None, None, None
    end = anchor
    for i in range(anchor, len(lines)):
        if lines[i].strip() and not lines[i][0].isspace():
            break
        end = i
    while end > anchor and not lines[end].strip():
        end -= 1
    return lines[start:anchor], lines[anchor:end + 1], anchor + 1


def locate_whole_def(lines, header):
    """Every line of the first `def` starting with `header`, or None.

    Same end rule as the slice above -- the first following line that is
    non-empty and starts at column 0 -- and located for the same reason: a
    line number written down here is a line number that drifts.
    """
    start = next((i for i, line in enumerate(lines)
                  if line.startswith(header)), None)
    if start is None:
        return None
    end = start
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][0].isspace():
            break
        end = i
    while end > start and not lines[end].strip():
        end -= 1
    return lines[start:end + 1]


def symlinked_home(workspace, name):
    """(base, real_home, linked_home, cwd) for a $HOME reachable by two names.

    A REAL directory plus a symlink pointing at it -- the shape macOS ships
    (`/Users/x` is also `/System/Volumes/Data/Users/x`) and the shape any
    home-on-another-volume setup produces.  `cwd` is created under the REAL
    spelling because that is what `os.getcwd()` hands back whichever name was
    used to get there, and telling those two spellings apart is the entire
    point of the fixture.
    """
    base = os.path.realpath(os.path.join(workspace.path, "walk", name))
    real = os.path.join(base, "real-home")
    linked = os.path.join(base, "linked-home")
    cwd = os.path.join(real, "work", "repo")
    os.makedirs(cwd, exist_ok=True)
    if not os.path.islink(linked):
        os.symlink(real, linked)
    return base, real, linked, cwd


def put_profile(directory, body):
    """Write `.claude/bitbucket.json` under `directory`; return its path."""
    path = os.path.join(directory, ".claude", "bitbucket.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(body))
    return path


def group_h(suite, mod, workspace):
    """Byte-identity between two deliberate copies of one function, and the
    boundary they now hold.

    WHY THIS GATE EXISTS.  `_profile_path` in bitbucket.py is a verbatim copy of
    jira.py's from the anchor line onwards -- the only difference above it is
    the environment variable this script names -- and the copy was made ON
    PURPOSE rather than by accident.

    The byte-identity is the property that keeps the eventual unification
    MECHANICAL.  A generated region requires a byte-identical body; two
    identical copies fold into one canonical source with no behaviour change to
    argue about, while a copy corrected on one side only turns that fold into a
    change to jira.py's behaviour smuggled inside a refactor.  So the gate is
    not "this code is right" -- it is "these two are still the same code".

    That is exactly what it bought.  The copy was made while the walk carried a
    $HOME boundary that did not bind, and when the repair landed these rows are
    what forced it through BOTH files in one change: the digest moved, and it
    could only be recorded again once the two slices matched each other.  The
    helper the boundary test calls is located and compared on its own, because
    it lives above the slice and identity of the walk alone would not cover it.

    The last rows are BEHAVIOURAL, and they are here rather than left to the
    jira suite for one reason identity cannot cover: identical text in a file
    is not the same claim as identical behaviour in the loaded module.  A
    second definition further down, or a shadowed helper, would satisfy every
    row above it.  So the boundary is also driven, through a real symlink,
    against this script's own function.
    """
    bb_lines = source_lines(TARGET)
    jira_lines = source_lines(JIRA_TARGET)
    bb_head, bb_body, bb_at = locate_profile_path(bb_lines)
    jira_head, jira_body, jira_at = locate_profile_path(jira_lines)

    problems = []
    if bb_body is None:
        problems.append("bitbucket.py: no `%s` carrying `%s`"
                        % (DEF_LINE, ANCHOR))
    if jira_body is None:
        problems.append("jira.py: no `%s` carrying `%s`" % (DEF_LINE, ANCHOR))
    suite.record(GH, "the-anchor-is-LOCATED-in-both-files", problems,
                 detail=["bitbucket.py: anchor at line %s" % bb_at,
                         "jira.py     : anchor at line %s" % jira_at,
                         "MEASURED, not pinned: this pair moved 1493 -> 1671 in "
                         "one day, and a case holding either number would fail "
                         "on an edit that touched nothing it cares about"])

    if bb_body is None or jira_body is None:
        return

    problems = []
    if len(bb_body) != PARITY_LINES:
        problems.append("bitbucket.py sliced %d line(s), want %d"
                        % (len(bb_body), PARITY_LINES))
    if len(jira_body) != PARITY_LINES:
        problems.append("jira.py sliced %d line(s), want %d"
                        % (len(jira_body), PARITY_LINES))
    suite.record(GH, "the-slice-is-ten-lines-in-both", problems,
                 detail=["bitbucket.py: %d line(s)" % len(bb_body),
                         "jira.py     : %d line(s)" % len(jira_body)])

    diff = [i for i in range(max(len(bb_body), len(jira_body)))
            if bb_body[i:i + 1] != jira_body[i:i + 1]]
    suite.record(GH, "the-slice-is-byte-identical-across-the-two-files",
                 problem_if(bb_body != jira_body,
                            "line(s) %r of the slice differ" % diff),
                 detail=(["identical, %d line(s)" % len(bb_body)]
                         if bb_body == jira_body
                         else ["bitbucket.py[%d]: %r" % (i, bb_body[i:i + 1])
                               for i in diff]
                         + ["jira.py[%d]     : %r" % (i, jira_body[i:i + 1])
                            for i in diff]))

    blob = ("\n".join(bb_body) + "\n").encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    suite.record(GH, "the-slice-digest-matches-the-recorded-sha256",
                 problem_if(digest != PARITY_SHA,
                            "digest %s, recorded %s" % (digest, PARITY_SHA)),
                 detail=["digest  : %s" % digest,
                         "recorded: %s" % PARITY_SHA,
                         "CONVENTION: the ten lines joined with \"\\n\" PLUS a "
                         "trailing newline. Without the trailing newline the "
                         "digest differs, and a hand re-measurement would read "
                         "as drift that is not there."])

    normalised = [line.replace("BITBUCKET", "JIRA").replace("bitbucket", "jira")
                  for line in bb_head]
    problems = []
    if normalised != jira_head:
        offenders = [i for i in range(max(len(normalised), len(jira_head)))
                     if normalised[i:i + 1] != jira_head[i:i + 1]]
        problems.append("the header differs by more than the variable names, "
                        "at line(s) %r of the header" % offenders)
    suite.record(GH, "the-lines-above-the-anchor-differ-only-in-the-names",
                 problems,
                 detail=bb_head
                 + ["normalising BITBUCKET->JIRA and bitbucket->jira makes the "
                    "bitbucket header EQUAL to the jira one: the divergence "
                    "above the anchor is the environment variable names and "
                    "the docstring sentence that quotes one of them, and "
                    "nothing else"])

    suite.record(GH, "the-two-headers-really-do-differ",
                 problem_if(bb_head == jira_head,
                            "the two headers are already identical, so the "
                            "normalisation above proves nothing"),
                 detail=["ANTI-VACUITY: if the substitution were a no-op the "
                         "row above would be asserting that two copies of the "
                         "same text are the same text",
                         "PROFILE_FILENAME is what lets the SLICE be identical "
                         "while the files read different files: it is a module "
                         "constant, so the differing value never appears in "
                         "the shared body",
                         "the header is also where the env-name ASYMMETRY "
                         "lives, and normalising it away is how this row gates "
                         "it: bitbucket.py must read BITBUCKET_PROFILE and "
                         "bitbucket_profile, jira.py JIRA_PROFILE and "
                         "jira_profile, and the case-distinct member of each "
                         "pair is load-bearing -- environment names are "
                         "case-sensitive, so a change covering only the "
                         "uppercase spelling closes half a door"])

    bb_within = locate_whole_def(bb_lines, WITHIN_DEF)
    jira_within = locate_whole_def(jira_lines, WITHIN_DEF)
    problems = []
    if bb_within is None:
        problems.append("bitbucket.py: no `%s`" % WITHIN_DEF)
    if jira_within is None:
        problems.append("jira.py: no `%s`" % WITHIN_DEF)
    if bb_within is not None and jira_within is not None \
            and bb_within != jira_within:
        offenders = [i for i in range(max(len(bb_within), len(jira_within)))
                     if bb_within[i:i + 1] != jira_within[i:i + 1]]
        problems.append("line(s) %r of the helper differ" % offenders)
    suite.record(GH, "the-helper-beside-the-walk-is-identical-too", problems,
                 detail=(bb_within or ["<not found>"])
                 + ["it carries NO environment name, so unlike the header it "
                    "has to match with no normalisation at all",
                    "it is compared separately because it sits ABOVE `%s` and "
                    "therefore outside the slice: the boundary test is a CALL "
                    "into it, so two identical walks reading two different "
                    "helpers would pass every row above and answer the same "
                    "question differently" % DEF_LINE])

    # -- the boundary, driven rather than read -----------------------------
    base, real_home, linked_home, cwd = symlinked_home(workspace, "symlinked")
    bait = put_profile(base, {"project": "ABOVE-A-SYMLINKED-HOME"})
    with walking_from(cwd, linked_home):
        with EnvSandbox():
            found = mod._profile_path(None)
    problems = []
    if found is not None:
        problems.append("$HOME spelled through a symlink did not stop the "
                        "walk; it climbed past and found %r" % found)
    suite.record(GH, "the-boundary-binds-in-this-script-too", problems,
                 detail=["$HOME as SET     : %s" % linked_home,
                         "$HOME as RESOLVED: %s"
                         % os.path.realpath(linked_home),
                         "cwd              : %s" % cwd,
                         "bait             : %s" % bait,
                         "found            : %s" % found,
                         "those two spellings ARE the defect this copy was "
                         "made carrying: os.getcwd() hands back the second, "
                         "os.path.expanduser(\"~\") hands back the first, and "
                         "one `==` between them decided whether the walk "
                         "stopped. On a shared machine the next thing it "
                         "reached was /tmp/.claude/bitbucket.json, and a "
                         "world-writable file there selects the repository "
                         "every scoped subcommand acts in."])

    base, real_home, linked_home, cwd = symlinked_home(workspace,
                                                       "symlinked-at-home")
    want = put_profile(real_home, {"project": "AT-A-SYMLINKED-HOME"})
    with walking_from(cwd, linked_home):
        with EnvSandbox():
            found = mod._profile_path(None)
    suite.record(GH, "a-profile-at-a-symlinked-HOME-is-still-found",
                 problem_if(found != want,
                            "found %r, want %r" % (found, want)),
                 detail=["$HOME as SET: %s" % linked_home,
                         "found       : %s" % found,
                         "CONTROL: it passes before the boundary is repaired "
                         "and after -- ANTI-VACUITY, because a `_profile_path` "
                         "that returned None for everything would satisfy the "
                         "row above and quietly take the walk out of this "
                         "script altogether"])

    base, real_home, linked_home, cwd = symlinked_home(workspace, "lower-env")
    put_profile(cwd, {"project": "WALK"})
    lower = os.path.join(base, "from-lowercase-env.json")
    with open(lower, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"project": "LOWER"}))
    with walking_from(cwd, linked_home):
        with EnvSandbox(bitbucket_profile=lower):
            found = mod._profile_path(None)
    suite.record(GH, "the-lowercase-bitbucket_profile-spelling-still-wins",
                 problem_if(found != lower,
                            "`bitbucket_profile` lost to the walk: %r"
                            % (found,)),
                 detail=["found: %s" % found,
                         "CONTROL: it cannot be observed red against the "
                         "unrepaired file -- `_env_first(\"BITBUCKET_PROFILE\""
                         ", \"bitbucket_profile\")` already read it.  It is "
                         "here because the env pair sits directly above the "
                         "anchor, so every change to the walk edits the lines "
                         "around it, and the row above gates the SPELLING "
                         "while this one gates that it still reaches the "
                         "loader and still beats the walk"])


# ---------------------------------------------------------------------------
# I. NEGATIVE CONTROL -- the oracles above must be able to fail
# ---------------------------------------------------------------------------

class MutantRefusal(Exception):
    """What a mutant reject_cloud raises, so the oracle sees a refusal."""


def _mutant_reject_cloud(base_url):
    """Compares only the full hostname against ONE Cloud host.

    This is the defect in its natural form -- nobody writes "miss the other
    host", they write `host == "bitbucket.org"` -- and from the outside it looks
    finished: the obvious URL is refused, and api.bitbucket.org walks straight
    through to 404 somewhere confusing.
    """
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    if host == "bitbucket.org":
        raise MutantRefusal("%s is Bitbucket Cloud" % host)


def _mutant_build_verdict(states):
    """Returns the first state given, unconditionally.

    A pull request with a green build and a red one reports whichever the
    server happened to list first, and `pr-builds` exits 0 on a broken branch.
    """
    return states[0]


def _mutant_redirect_refuses(here, there):
    """Compares only the host, which is the half urllib itself gets right."""
    return ((urllib.parse.urlparse(here).hostname or "").lower()
            != (urllib.parse.urlparse(there).hostname or "").lower())


def group_i(suite, mod, measured):
    short = tuple(c for c in mod.WRITE_COMMANDS if c != "pr-comment")

    mutants = [
        ("reject-cloud-compares-only-the-full-hostname",
         lambda: check_reject_cloud(_mutant_reject_cloud),
         "catches bitbucket.org and misses api.bitbucket.org and every "
         "team.bitbucket.org"),
        ("build-verdict-returns-the-first-state",
         lambda: check_verdict(_mutant_build_verdict),
         "a red build beside a green one reports whichever came first, and the "
         "CI gate exits 0"),
        ("write-commands-missing-one-entry",
         lambda: check_write_commands(short, measured),
         "`pr-comment` removed from the tuple: it would reach the network with "
         "BITBUCKET_READ_ONLY set, and nothing would fail loudly"),
        ("redirect-compares-only-the-host",
         lambda: check_redirect(_mutant_redirect_refuses),
         "a 302 from https to http on the same host, or to a different port, "
         "carries the bearer token and looks same-origin"),
    ]

    caught = 0
    for cid, run_oracle, why in mutants:
        problems = run_oracle()
        if problems:
            caught += 1
        suite.record(GI, "control-%s" % cid,
                     problem_if(not problems,
                                "the oracle ACCEPTED a broken implementation, "
                                "so it is not protecting anything"),
                     detail=["mutant  : %s" % why,
                             "rejected with: %s" % ("; ".join(problems)
                                                    or "NOTHING")])

    suite.record(GI, "control-fires-at-all",
                 problem_if(caught != len(mutants),
                            "only %d of %d mutants were caught"
                            % (caught, len(mutants))),
                 detail=["%d/%d mutants rejected" % (caught, len(mutants)),
                         "a control that stops running is indistinguishable "
                         "from code that is correct"])

    problems = []
    problems += ["cloud: %s" % p for p in check_reject_cloud(mod.reject_cloud)]
    problems += ["verdict: %s" % p for p in check_verdict(mod._build_verdict)]
    problems += ["write-commands: %s" % p
                 for p in check_write_commands(mod.WRITE_COMMANDS, measured)]
    problems += ["redirect: %s" % p
                 for p in check_redirect(real_redirect_refuses(mod))]
    suite.record(GI, "control-real-implementations-pass-the-same-oracles",
                 problems,
                 detail=["THE MIRROR. An oracle that rejected EVERYTHING would "
                         "satisfy all four mutant rows above and prove "
                         "nothing; this is the row that makes them mean "
                         "something."])


# ---------------------------------------------------------------------------
# J. INFO -- what is NOT proven
# ---------------------------------------------------------------------------

# (case id, what the path is, the marker the source carries)
UNVERIFIED_PATHS = [
    ("pr-comment-post", "POST .../pull-requests/{id}/comments",
     "the comment body shape is read from the published reference"),
    ("pr-approve-participant-put",
     "PUT .../pull-requests/{id}/participants/{user}",
     "the endpoint AND the body are read from the published reference, on a "
     "feature carrying two deprecation layers"),
    ("pr-decline-post", "POST .../pull-requests/{id}/decline",
     "shares _state_change's single marker with reopen"),
    ("pr-reopen-post", "POST .../pull-requests/{id}/reopen",
     "shares _state_change's single marker with decline"),
    ("pr-merge-post", "POST .../pull-requests/{id}/merge",
     "the one call no later call can undo, and the one that has never been "
     "run"),
]


def group_j(suite, mod):
    lines = source_lines(TARGET)
    markers = [i + 1 for i, line in enumerate(lines)
               if line.strip().startswith("# UNVERIFIED")]

    for cid, where, why in UNVERIFIED_PATHS:
        suite.record(GJ, "unverified-%s" % cid, [], status=H.INFO,
                     detail=["path : %s" % where,
                             "note : %s" % why,
                             "# UNVERIFIED markers in bitbucket.py at lines: %r"
                             % markers,
                             "NOT PROVEN. The offline cases in groups C, D and "
                             "F prove the REQUEST SHAPE this script builds and "
                             "prove the refusals in front of it. They do not "
                             "and cannot prove that a Bitbucket instance "
                             "ACCEPTS the request: the transport is injected, "
                             "so the only thing answering is this suite."])

    suite.record(GJ, "gate3-conflicted-and-vetoed-branches-are-offline-only",
                 [], status=H.INFO,
                 detail=["group D drives all three blocking conditions plus "
                         "the fail-closed UNKNOWN default, from scripted "
                         "payloads",
                         "LIVE, only a CLEAN pre-flight has ever run (2026-09-15 "
                         "against Bitbucket 9.4.23). The CONFLICTED and vetoed "
                         "SHAPES are read from the reference, so what group D "
                         "proves is that this script blocks on the payload it "
                         "believes in -- not that a real instance sends that "
                         "payload."])

    jira_lines = source_lines(JIRA_TARGET)
    bb_home = line_numbers(lines, HOME_LINE)
    jira_home = line_numbers(jira_lines, HOME_LINE)
    suite.record(GJ, "what-the-repaired-HOME-boundary-still-cannot-see", [],
                 status=H.INFO,
                 detail=["bitbucket.py:%s" % (bb_home[0] if bb_home else "?"),
                         "jira.py:%s" % (jira_home[0] if jira_home else "?"),
                         "both spell `%s`" % HOME_LINE,
                         "The boundary itself is GATED, in group H here and in "
                         "the jira suite's group L, through a real symlinked "
                         "$HOME. What is recorded here is the one residue "
                         "those gates do not close: os.path.realpath resolves "
                         "SYMLINKS and does not canonicalise CASE, and "
                         "os.path.normcase is a no-op on POSIX, so a $HOME "
                         "spelled in a different case on a case-insensitive "
                         "volume still fails to match its own directory.",
                         "The residue runs in the CONSERVATIVE direction, "
                         "which is why it is a residue and not the defect "
                         "again: a parent the boundary cannot recognise is "
                         "REFUSED, so the walk stops at the working directory "
                         "instead of climbing past $HOME to /tmp. Fewer files "
                         "trusted, an ancestor profile inside your own home "
                         "missed, and the way out is the one --profile and "
                         "BITBUCKET_PROFILE were always for.",
                         "Closing it means st_dev/st_ino per directory -- a "
                         "stat() per level, a different failure mode on every "
                         "network filesystem, and a boundary nobody can read "
                         "off the source. Refused, and written down here."])


# ---------------------------------------------------------------------------
# K. hygiene
# ---------------------------------------------------------------------------

def group_k(suite, before, pyc_before, workspace):
    after = H.repo_tree()
    new = sorted(after - before)
    gone = sorted(before - after)
    suite.record(GK, "no-new-repo-paths",
                 problem_if(new, "this suite wrote into the repo tree: %s"
                            % new[:12]),
                 detail=["%d path(s) before, %d after" % (len(before),
                                                          len(after))])
    suite.record(GK, "no-removed-repo-paths",
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
    suite.record(GK, "pycache-zero", problems,
                 detail=["%d .pyc before, %d after (contract: zero)"
                         % (len(pyc_before), len(pyc_after)),
                         "ABSOLUTE, not a delta: a file that was already there "
                         "reads as `1 before, 1 after` and sails through a "
                         "delta check, which is exactly how one hid under "
                         "Scripts/__pycache__ until it was found by hand"])

    inside = os.path.realpath(workspace).startswith(
        os.path.realpath(H.REPO_ROOT) + os.sep)
    suite.record(GK, "workspace-outside-the-repo-tree",
                 problem_if(inside, "the workspace is inside the repo: %s"
                            % workspace),
                 detail=["workspace: %s" % workspace])


# ---------------------------------------------------------------------------

PROFILE_BODY = {"project": PROJECT, "repo": REPO}


def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME,
                    title="Bitbucket CLI offline: the URL join and the two "
                          "refusals at the door, the same-origin redirect "
                          "handler, BITBUCKET_READ_ONLY at both layers with "
                          "the write set DERIVED from the handler table, the "
                          "four merge refusals and their order, pr-builds' "
                          "verdict and its exit code in both formats, the "
                          "per-subcommand request contract, reviewer "
                          "flattening in both shapes, and byte parity with "
                          "jira.py's _profile_path -- plus the $HOME boundary "
                          "that parity forced through both files at once, "
                          "driven here through a real symlinked $HOME",
                    opts=opts, mode="grouped")

    before = H.repo_tree()
    pyc_before = H.pycache_snapshot()

    if not os.path.isfile(TARGET):
        suite.record(GA, "target-exists",
                     ["the CLI under test is missing: %s" % TARGET])
        suite.print_summary()
        return suite

    mod = H.load_module_from_path("bitbucket_cli_under_test", TARGET)
    with H.TempWorkspace("ph-bitbucket-cli-", keep=opts.keep) as workspace:
        # PINNED, never walked: `_profile_path` climbs from the working
        # directory, so every scoped case points --profile at this file rather
        # than at whatever the machine running the suite happens to have above
        # the checkout.
        profile = workspace.write_text("bitbucket-profile.json",
                                       json.dumps(PROFILE_BODY))
        group_a(suite, mod)
        group_b(suite, mod)
        measured = group_c(suite, mod, profile)
        group_d(suite, mod, profile)
        group_e(suite, mod, profile)
        group_f(suite, mod, profile)
        group_g(suite, mod)
        group_h(suite, mod, workspace)
        group_i(suite, mod, measured)
        group_j(suite, mod)
        # group_k LAST, always: it asserts the repo tree is exactly as this run
        # found it, so every group that could write has to have finished.
        group_k(suite, before, pyc_before, workspace.path)

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
