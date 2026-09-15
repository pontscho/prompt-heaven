#!/usr/bin/env python3
"""Read from and write to a Bitbucket Server / Data Center instance from the terminal.

Pull requests are the whole subject: list them, read one, read its activity feed, ask
whether it could merge, then create one, comment on it, set a review status, decline it,
reopen it, or merge it.  Stdlib only, no third-party HTTP client.

WHY SERVER/DC ONLY.  Bitbucket Cloud and Bitbucket Server are not two dialects of one
API the way Jira Cloud and Jira Data Center are -- they are different products with
different resource models, different paging and different auth.  jira.py can hide its
two search APIs behind one iterator because the nouns match.  Here they do not.  So a
Cloud host is refused at the door, and the refusal names the reason.  Supporting Cloud
is a second script, not a branch in this one.

WHY BEARER ONLY.  A Bitbucket personal HTTP access token is sent as
`Authorization: Bearer <token>`.  The same token also works as the password half of
Basic auth for a PERSONAL token, but not for a project- or repository-scoped one, which
needs the literal username `x-token-auth`.  Two auth modes where one works is surface
with no payoff.

WHY THE VERSION IS AN ASSERTION AND NOT AN INPUT.  Every state-changing operation on a
pull request carries an optimistic-lock integer, and a stale one comes back as a 409
that reads like a merge conflict.  This script always re-reads the pull request
immediately before a write and uses the version it just saw.  `--version` exists, but
passing it only asks this script to REFUSE when what you remember disagrees with what
the server holds -- turning a remote race into a local explanation.

WHY MERGE IS GATED RATHER THAN OFFERED.  Merging is the only call here that no later
call can undo.  Four separate refusals stand in front of it, each its own failure mode
rather than one confirmation prompt: the read-only environment gate, an explicit
`--yes`, a mergeability pre-flight that must come back CLEAN, and the version
assertion.  `--ignore-vetoes` is needed on top when the pre-flight reports a conflict
or a vetoed merge check.

WHY 401 IS EXPLAINED AND NOT MERELY REPORTED.  This API answers BOTH "who are you" and
"you may not do that" with 401.  A caller reading the usual REST convention will chase
a credential problem that is really a permission problem, so the error text says so.

WHY A 409 IS QUOTED VERBATIM.  On the merge endpoint one 409 covers merge conflicts, a
vetoed merge check, a stale version, a pull request that is not open, and an archived
target repository.  There is no code per cause, so the server's own message is the only
discriminator and is surfaced unedited.

WHY PAGING FOLLOWS nextPageStart.  The documentation is explicit that item identifiers
between pages are not guaranteed contiguous, so `start + size` can skip or repeat.
Every sweep here reads `nextPageStart` off the response and stops on `isLastPage`.

WHY NOTHING PROJECT-SPECIFIC IS IN THIS FILE.  `pr-create` reads its defaults -- the
project key, the repository slug, the target branch, the reviewers -- from a
`.claude/bitbucket.json` profile found by walking up from the working directory.  A
project's conventions belong to the project, not to a shared script.

LIVE VERIFICATION STATUS, 2026-09-15 against Bitbucket 9.4.23.  Every read plus
`pr-create` has now been exercised with a real token: version, whoami, repo, pr-list,
pr-get, pr-activities, pr-mergeability, pr-builds, pr-reviewers, and a create that
opened a real pull request.  Each of those call sites is marked `# VERIFIED` with
that date.  `whoami` is included on purpose: it prints the X-AUSERNAME header off
the application-properties response, so a run that printed a real username IS the
observation of that header.

Still only READ, never accepted by a server: the comment POST, the participant PUT
behind `pr-approve`, decline, reopen and merge.  Their bodies have been assembled
against real pull-request data under `--dry-run`, so the URL and the payload are known
good in shape -- what is unproven is that the endpoint accepts them.  Those sites keep
their `# UNVERIFIED` marker.  The markers are the honest state of this file, not
decoration.

Two defects were caught by that first live run and are worth remembering rather than
just fixing: the default-reviewers endpoint answers with a FLAT user array, not the
array of conditions its own reference describes, so a client that trusted the document
reported "1 condition, 0 reviewers" -- a silent zero indistinguishable from a
repository with none configured.  And `--description-file` was routed through the
helper that takes text rather than the one that takes a path, so it would have
published the path string as the pull request's description.  A dry run caught the
second; nothing but a live call could have caught the first.

Usage:
	bitbucket.py version
	bitbucket.py whoami
	bitbucket.py repo --project SL --repo ngs-media-server
	bitbucket.py pr-list --state OPEN --at master
	bitbucket.py pr-get 1211
	bitbucket.py pr-activities 1211 --action COMMENTED
	bitbucket.py pr-mergeability 1211
	bitbucket.py pr-builds 1211
	bitbucket.py pr-reviewers --from my-branch --to master
	bitbucket.py pr-create --from my-branch --title "..." --description-file -
	bitbucket.py pr-comment 1211 -
	bitbucket.py pr-approve 1211
	bitbucket.py pr-decline 1211 --comment "superseded"
	bitbucket.py pr-reopen 1211
	bitbucket.py pr-merge 1211 --yes

Environment variables:
	BITBUCKET_URL        base URL of the instance, e.g. https://bitbucket.example.com
	BITBUCKET_TOKEN      personal HTTP access token, sent as a bearer token
	BITBUCKET_PROJECT    default project key, overridden by --project
	BITBUCKET_REPO       default repository slug, overridden by --repo
	BITBUCKET_PROFILE    explicit path to a profile, overriding the walk up
	bitbucket_profile    the SAME input spelled in lowercase, and read second.
	                     Environment names are case-sensitive, so this is not a
	                     nicety -- it is a second, real way to point the profile
	                     loader at an arbitrary path, and it is documented here
	                     rather than removed because accepting both spellings is
	                     this fleet's established pattern
	BITBUCKET_READ_ONLY  1/true/yes refuses every write subcommand

Output:
	Markdown on stdout, a one-line summary on stderr, `--json` for the raw payload.

Exit codes:
	0  the command did what it said
	1  the server answered and the answer is bad news
	2  bad invocation, bad configuration, an unreachable host, or a refused write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

PROG = "bitbucket.py"
USER_AGENT = "bitbucket.py (stdlib urllib)"

# The API version segment is NOT the product version.  /rest/api/1.0 has been
# stable since the Stash era and is unrelated to the 9.x/10.x number the server
# reports -- Atlassian's own docs URL says v1004 for product 10.4 while the path
# still says 1.0.  `latest` is accepted as an alias; the pinned number is used
# here so an instance upgrade cannot change the contract underneath a caller.
API = "/rest/api/1.0"

# Default reviewers live under their OWN REST root.  Assuming everything
# pull-request shaped hangs off /rest/api/1.0 earns a 404 that reads like a
# missing repository.
DEFAULT_REVIEWERS_API = "/rest/default-reviewers/1.0"

# Build status is a THIRD root.  The /rest/api/1.0/.../commits/{id}/builds
# subresource exists on 9.4.23 but demands a `key` -- it fetches one named
# build, it does not list them.  This root lists, with the standard paging
# envelope.  VERIFIED 2026-09-15 against Bitbucket 9.4.23.
BUILD_STATUS_API = "/rest/build-status/1.0"

# Bitbucket's own build states.  Ordered worst-first: a set of builds is only
# as good as its unhappiest member, and that is how the verdict is computed.
BUILD_FAILED = "FAILED"
BUILD_INPROGRESS = "INPROGRESS"
BUILD_SUCCESSFUL = "SUCCESSFUL"
BUILD_PRECEDENCE = (BUILD_FAILED, BUILD_INPROGRESS, BUILD_SUCCESSFUL)

OK = 0
FINDING = 1
USAGE = 2

DEFAULT_TIMEOUT = 30.0

# The server caps `limit` at an admin-configured ceiling that is not documented
# as a fixed number, so the response's own `limit` is what a sweep believes,
# never the value that was asked for.  DEFAULT_LIMIT matches the documented
# server default so a caller who passes nothing sees the page the UI would.
DEFAULT_LIMIT = 25
PAGE_SIZE = 100

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0

TRUTHY = ("1", "true", "yes")

# Checked in main() BEFORE the client is constructed, so a read-only refusal
# cannot reach the network even by accident.
WRITE_COMMANDS = (
	"pr-approve",
	"pr-comment",
	"pr-create",
	"pr-decline",
	"pr-merge",
	"pr-reopen",
)

RX_PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
RX_REPO_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# 7 is the shortest abbreviation git will hand out, 64 the width of a SHA-256
# object id -- the range covers both hash algorithms and every abbreviation of
# either.  Hex only: this value reaches a URL PATH, so the point of the gate is
# that nothing structural (a `/`, a `.`, a `%`) can be inside it.
RX_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")

PROFILE_FILENAME = os.path.join(".claude", "bitbucket.json")

# Resolved against the default-reviewers endpoint for the branch pair actually
# being opened, which is why it cannot be expanded at parse time.
SENTINEL_DEFAULT = "@default"

REF_PREFIX = "refs/heads/"

# GET .../merge answers with one of these.  Only the first is safe to act on.
MERGE_CLEAN = "CLEAN"
MERGE_UNKNOWN = "UNKNOWN"

# A Cloud host reached with this script would 404 in confusing places rather
# than fail at the door, so the door is where it fails.
CLOUD_HOSTS = ("bitbucket.org", "api.bitbucket.org")

# Every response carries the authenticated username in this header, including
# responses otherwise indistinguishable from an anonymous read.  It is the only
# cheap way to answer "who am I" on an API that has no `myself` endpoint.
HEADER_USERNAME = "X-AUSERNAME"
ANONYMOUS = "anonymous"


# ---------------------------------------------------------------------------
# errors: the two exit codes that are not 0
# ---------------------------------------------------------------------------

class BitbucketError(Exception):
	"""A finding -- the server answered, and the answer is bad news (exit 1)."""


class SetupError(Exception):
	"""Bad invocation, bad configuration, or an unreachable host (exit 2)."""


# ---------------------------------------------------------------------------
# configuration: everything that is not a subcommand argument
# ---------------------------------------------------------------------------

class Config(object):
	"""Resolved connection settings, plus where each one came from."""

	def __init__(self, base_url: str, token: str, timeout: float,
			read_only_raw: Optional[str], sources: Dict[str, str]) -> None:
		self.base_url = base_url
		self.token = token
		self.timeout = timeout
		self.read_only_raw = read_only_raw
		self.read_only = _truthy(read_only_raw)
		self.sources = sources

	def __repr__(self) -> str:
		"""Never print the token, not even into a traceback."""
		return "Config(base_url=%r, token=<redacted>, timeout=%r)" % (
			self.base_url, self.timeout)


def _truthy(raw: Optional[str]) -> bool:
	"""One spelling rule for every boolean environment variable read here."""
	return bool(raw) and raw.strip().lower() in TRUTHY


def _env_first(*names: str) -> Tuple[Optional[str], str]:
	"""The first environment variable that is set, and the name it came from."""
	for name in names:
		value = os.environ.get(name)
		if value:
			return value, "env:%s" % name
	return None, "unset"


def _pick(flag: Optional[Any], flag_name: str,
		*env_names: str) -> Tuple[Optional[Any], str]:
	"""A flag beats the environment, and the winner names itself."""
	if flag:
		return flag, "flag:%s" % flag_name
	return _env_first(*env_names)


def normalize_base_url(raw: str) -> str:
	"""Strip trailing slashes so a context path survives the join.

	`urllib.parse.urljoin` is deliberately not used anywhere in this file: it
	would eat a Data Center context path such as /bitbucket the moment a path
	beginning with /rest is joined onto it.
	"""
	return raw.rstrip("/")


def reject_cloud(base_url: str) -> None:
	"""Refuse a Cloud host at the door rather than 404 halfway through."""
	host = (urllib.parse.urlparse(base_url).hostname or "").lower()
	for cloud in CLOUD_HOSTS:
		if host == cloud or host.endswith("." + cloud):
			raise SetupError(
				"%s is Bitbucket Cloud — this script speaks the Server/Data "
				"Center REST API (%s), which Cloud does not serve; they are "
				"different products, not two dialects" % (host, API))


def resolve_config(args: argparse.Namespace) -> Config:
	"""Flags, then the environment, then a refusal that names what is missing."""
	sources = {}					# type: Dict[str, str]

	base_url, sources["base_url"] = _pick(
		getattr(args, "url", None), "--url", "BITBUCKET_URL")
	token, sources["token"] = _pick(
		getattr(args, "token", None), "--token", "BITBUCKET_TOKEN")
	read_only_raw, sources["read_only"] = _env_first("BITBUCKET_READ_ONLY")

	missing = []					# type: List[str]
	if not base_url:
		missing.append("BITBUCKET_URL (or --url)")
	if not token:
		missing.append("BITBUCKET_TOKEN (or --token)")
	if missing:
		sys.stderr.write(
			"%s: refusing to run — missing required configuration: %s\n"
			% (PROG, ", ".join(missing)))
		sys.exit(USAGE)

	base_url = normalize_base_url(str(base_url))

	# WHY THE URL IS PARSED ONCE HERE, AND BEFORE reject_cloud.
	#
	# The scheme: the token is a Bearer PAT, so `http://` puts it on the wire in
	# cleartext on every single request.  Only `https` is accepted -- not "https
	# unless you asked otherwise", because there is no flag that could make
	# handing the token to the network a considered choice.
	#
	# The userinfo: a `user:secret@host` base URL survives into what this script
	# PRINTS -- cmd_version and cmd_whoami both render cfg.base_url, _dry_run
	# puts it in the command it echoes, and the "cannot reach %s" error carries
	# the whole URL -- which defeats Config.__repr__'s redaction entirely, since
	# the secret is no longer in the field __repr__ hides.  Worse, http.client
	# raises InvalidURL for userinfo; it derives from HTTPException, which NONE
	# of the handlers in urllib_fetch or main() catches, so it arrives as an
	# uncaught traceback with the credential in the frame.
	#
	# Both run BEFORE reject_cloud because reject_cloud reads `.hostname`, and a
	# scheme-less `--url bitbucket.org/x` parses to hostname None -- so the Cloud
	# check silently passes the very host it exists to refuse.  Requiring the
	# scheme first is what gives that check a hostname to look at.
	#
	# Neither refusal echoes the URL: that is the point of the second one.
	parsed = urllib.parse.urlparse(base_url)
	if parsed.scheme != "https":
		raise SetupError(
			"refusing a base URL whose scheme is %r — the token is sent as "
			"`Authorization: Bearer`, so anything but https:// hands it to the "
			"network in cleartext; give the URL an explicit https:// prefix"
			% parsed.scheme)
	if parsed.username or parsed.password:
		raise SetupError(
			"refusing a base URL that carries userinfo (the user:password@ part "
			"before the host) — this script prints its base URL back in several "
			"places and urllib puts it in its own error text, so a credential "
			"spelled there leaks; put the token in BITBUCKET_TOKEN instead")

	reject_cloud(base_url)

	return Config(base_url, str(token),
		float(getattr(args, "timeout", DEFAULT_TIMEOUT)),
		read_only_raw, sources)


# ---------------------------------------------------------------------------
# url joining: a context path is a path, not a host
# ---------------------------------------------------------------------------

def api_url(base_url: str, path: str,
		query: Optional[Dict[str, Any]] = None) -> str:
	"""Concatenate, never urljoin, and drop query keys whose value is None."""
	url = "%s%s" % (normalize_base_url(base_url), path)
	if not query:
		return url
	pairs = []						# type: List[Tuple[str, str]]
	for key in sorted(query):
		value = query[key]
		if value is None:
			continue
		if isinstance(value, bool):
			value = "true" if value else "false"
		pairs.append((key, str(value)))
	if not pairs:
		return url
	return "%s?%s" % (url, urllib.parse.urlencode(pairs))


def pr_path(project: str, repo: str, pull_request_id: Optional[int] = None,
		suffix: str = "") -> str:
	"""The one place that knows how a pull-request URL is spelled."""
	base = "%s/projects/%s/repos/%s/pull-requests" % (
		API, urllib.parse.quote(project), urllib.parse.quote(repo))
	if pull_request_id is None:
		return base
	return "%s/%d%s" % (base, pull_request_id, suffix)


def normalize_ref(name: str) -> str:
	"""Accept `master`, return `refs/heads/master`.

	The create endpoint rejects a short branch name with a 400 whose message
	does not mention refs at all, so the expansion happens here rather than in
	the caller's head.
	"""
	name = name.strip()
	if not name:
		raise SetupError("empty branch name")
	if name.startswith("refs/"):
		return name
	return REF_PREFIX + name


def short_ref(ref: str) -> str:
	"""The inverse of normalize_ref, for rendering only."""
	if ref.startswith(REF_PREFIX):
		return ref[len(REF_PREFIX):]
	return ref


# ---------------------------------------------------------------------------
# transport: the only place that touches urllib
# ---------------------------------------------------------------------------

class HttpResponse(object):
	"""A status, headers and a body -- including for a 4xx, which is an answer."""

	def __init__(self, status: int, headers: Dict[str, str],
			body: bytes) -> None:
		self.status = status
		self.headers = {k.lower(): v for k, v in headers.items()}
		self.body = body

	def header(self, name: str) -> Optional[str]:
		"""Case-insensitive lookup, because header case is not a contract."""
		return self.headers.get(name.lower())

	def text(self) -> str:
		"""Best-effort decode, for error messages that must not themselves fail."""
		try:
			return self.body.decode("utf-8")
		except UnicodeDecodeError:
			return repr(self.body[:512])


Fetch = Callable[[str, str, Optional[bytes], Dict[str, str]], HttpResponse]


def _origin(url: str) -> Tuple[str, str, Any]:
	"""The (scheme, host, port) triple a redirect is not allowed to change.

	A port that does not parse is reported as the string "?" rather than
	raised.  This is read inside a redirect decision, where any exception that
	is not the deliberate refusal would reach the caller as a traceback instead
	of an explanation -- and an unparseable port can only differ from a real
	one, which is the answer the caller needs anyway.

	An ABSENT port on an https URL is normalised to 443, because omitting it
	and spelling it are the same origin and a reverse proxy in front of Data
	Center may legitimately redirect between the two spellings.  Refusing that
	would be a false alarm about the one deployment shape this handler exists
	to survive.  The normalisation cannot weaken the check: it applies only to
	https, where 443 IS the default, so no two different origins are made to
	compare equal.
	"""
	parsed = urllib.parse.urlparse(url)
	scheme = parsed.scheme.lower()
	try:
		port = parsed.port
	except ValueError:
		port = "?"
	if port is None and scheme == "https":
		port = 443
	return (scheme, (parsed.hostname or "").lower(), port)


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
	"""Refuse a 30x that would carry the bearer token off this origin.

	WHY THIS EXISTS AT ALL.  urllib's own HTTPRedirectHandler rebuilds the
	redirected request from the original one and strips exactly two headers,
	`content-length` and `content-type`.  `Authorization` is not among them, so
	the stdlib default follows a redirect to a DIFFERENT host with the token
	still attached: whoever a 302 names is handed the credential.

	WHY REFUSE RATHER THAN STRIP THE HEADER.  Stripping is the right answer for
	a general-purpose client that cannot know whether the redirect is
	legitimate.  Here it is known: this is one REST API on one instance, and it
	has no cross-origin redirect a caller would ever want to follow.  Stripping
	would turn a genuinely alarming event into an ordinary-looking 401 three
	frames away from its cause; refusing names it where it happens.

	A same-origin redirect -- the context-path and trailing-slash kind a
	reverse proxy in front of Data Center really does emit -- is delegated
	straight back to the stdlib and keeps working.
	"""

	def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
			msg: str, headers: Any,
			newurl: str) -> Optional[urllib.request.Request]:
		"""Same (scheme, host, port), or no redirect at all."""
		here = _origin(req.full_url)
		there = _origin(newurl)
		if here != there:
			raise SetupError(
				"refusing a cross-origin redirect: %s://%s answered %d and "
				"pointed at %s://%s — the Authorization header would follow it, "
				"and this API has no redirect that legitimately leaves its own "
				"origin" % (here[0], here[1], code, there[0], there[1]))
		return urllib.request.HTTPRedirectHandler.redirect_request(
			self, req, fp, code, msg, headers, newurl)


def urllib_fetch(method: str, url: str, body: Optional[bytes],
		headers: Dict[str, str], timeout: Optional[float] = None) -> HttpResponse:
	"""The real transport.

	An HTTPError is unwrapped back into an ordinary HttpResponse on purpose: a
	409 carrying a merge veto is the most informative reply this API produces,
	and raising it away would throw that information out.  Only a genuinely
	unreachable host becomes an exception.

	The opener is built here rather than calling urllib.request.urlopen, for
	one reason: urlopen's opener carries the stdlib redirect handler, which lets
	the bearer token follow a 30x to another host.  Building the opener is the
	only way to put SameOriginRedirectHandler in its place.  The rest of the
	chain is what urlopen would have assembled -- passing an HTTPSHandler
	carrying ssl.create_default_context() per call is exactly what urlopen does
	with a `context` argument, so the TLS behaviour is unchanged.

	main() reaches this through the MODULE ATTRIBUTE of this name rather than a
	captured reference, so a test can replace one named attribute instead of
	monkeypatching urlopen globally.
	"""
	request = urllib.request.Request(url=url, data=body, method=method)
	for key, value in headers.items():
		request.add_header(key, value)
	context = ssl.create_default_context()
	opener = urllib.request.build_opener(
		urllib.request.HTTPSHandler(context=context),
		SameOriginRedirectHandler())
	try:
		with opener.open(request, timeout=timeout) as response:
			return HttpResponse(
				response.status, dict(response.headers), response.read())
	except urllib.error.HTTPError as exc:
		return HttpResponse(exc.code, dict(exc.headers or {}), exc.read())
	except urllib.error.URLError as exc:
		raise SetupError("cannot reach %s: %s" % (url, exc.reason))
	except OSError as exc:
		raise SetupError("cannot reach %s: %s" % (url, exc))


def _is_retryable(response: HttpResponse) -> bool:
	"""429 and 5xx only.  A 409 is a decision, not a hiccup."""
	return response.status == 429 or 500 <= response.status < 600


def _retry_delay(response: HttpResponse, attempt: int) -> float:
	"""Obey Retry-After when it is a number, back off otherwise."""
	raw = response.header("retry-after")
	if raw:
		try:
			return max(0.0, float(raw))
		except ValueError:
			pass
	return BACKOFF_SECONDS * (2 ** (attempt - 1))


def _status_hint(status: int) -> str:
	"""Say what a status code does NOT say on this particular API."""
	if status == 401:
		return ("401 here covers BOTH an unusable token and a missing "
			"permission — this API does not split them the way most REST "
			"services do")
	if status == 403:
		return ("403 on this API is narrow: a licensed-user limit, a "
			"self-degraded permission, or a per-endpoint feature flag such as "
			"auto-merge being switched off")
	if status == 404:
		return "404 is ambiguous with having no permission to see the resource"
	if status == 409:
		return ("409 is overloaded here — a stale version, a merge conflict, a "
			"vetoed merge check, a wrong current state and an archived "
			"repository all arrive as 409, so the message above is the only "
			"discriminator")
	if status == 415:
		return "415 usually means a missing Content-Type on a POST or PUT"
	return ""


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

class Bitbucket(object):
	"""One instance, one token, one place that turns a response into a value."""

	def __init__(self, cfg: Config, fetch: Optional[Fetch] = None,
			sleep: Optional[Callable[[float], None]] = None) -> None:
		self.cfg = cfg
		self._fetch = fetch or self._default_fetch
		self._sleep = sleep or time.sleep
		self.last_username = None	# type: Optional[str]

	def _default_fetch(self, method: str, url: str, body: Optional[bytes],
			headers: Dict[str, str]) -> HttpResponse:
		"""Dispatch through the module attribute so the seam stays patchable."""
		return urllib_fetch(method, url, body, headers, timeout=self.cfg.timeout)

	def _headers(self, has_body: bool) -> Dict[str, str]:
		"""Content-Type is sent even for an empty body: omitting it earns a 415."""
		headers = {
			"Authorization": "Bearer %s" % self.cfg.token,
			"Accept": "application/json",
			"User-Agent": USER_AGENT,
		}
		if has_body:
			headers["Content-Type"] = "application/json"
		return headers

	def request(self, method: str, path: str, body: Optional[Any] = None,
			query: Optional[Dict[str, Any]] = None,
			subject: Optional[str] = None) -> Any:
		"""Send, retry what a retry could fix, then decode or explain."""
		# WHY READ-ONLY IS CHECKED A SECOND TIME, HERE.
		#
		# main() is the ONLY other reader of cfg.read_only, and it gates on
		# WRITE_COMMANDS -- a hand-maintained tuple of subcommand names.  That
		# tuple is complete as measured today: every write subcommand in
		# HANDLERS is in it.  So this is drift prevention, not a hole being
		# closed.  What it prevents is the next subcommand: a new write added to
		# HANDLERS and forgotten in WRITE_COMMANDS would reach the network with
		# BITBUCKET_READ_ONLY set, and nothing would have failed loudly enough
		# to notice.  The check moves to where the verb actually is.
		#
		# This file's own prose sells read-only as the FIRST of four refusals
		# standing in front of a merge; a defence-in-depth claim made in prose
		# deserves an assertion in code rather than a second place to forget.
		if self.cfg.read_only and method.upper() != "GET":
			raise SetupError(
				"BITBUCKET_READ_ONLY is set — refusing to send %s %s"
				% (method.upper(), path))
		url = api_url(self.cfg.base_url, path, query)
		payload = None				# type: Optional[bytes]
		if body is not None:
			payload = json.dumps(body).encode("utf-8")
		response = self._send(method, url, payload)
		return self._decode(response, subject or path)

	def _send(self, method: str, url: str,
			payload: Optional[bytes]) -> HttpResponse:
		"""The retry loop, shared by every verb."""
		attempt = 0
		while True:
			attempt += 1
			response = self._fetch(
				method, url, payload, self._headers(payload is not None))
			username = response.header(HEADER_USERNAME)
			if username:
				self.last_username = username
			if attempt >= MAX_ATTEMPTS or not _is_retryable(response):
				return response
			self._sleep(_retry_delay(response, attempt))

	def _decode(self, response: HttpResponse, subject: str) -> Any:
		"""Turn a response into a value, or into the clearest possible refusal."""
		if response.header(HEADER_USERNAME) == ANONYMOUS:
			raise SetupError(
				"the server answered as %s — the token was not accepted; check "
				"BITBUCKET_TOKEN, and note that this API reports both a bad "
				"token and a missing permission as 401" % ANONYMOUS)

		if response.status >= 400:
			raise self._error(response, subject)

		if response.status == 204 or not response.body:
			return None

		content_type = (response.header("content-type") or "").lower()
		if "json" not in content_type:
			raise SetupError(
				"expected JSON from %s but got %s — an SSO login page or a "
				"reverse proxy in front of the instance is the usual cause"
				% (subject, content_type or "no content type"))

		try:
			return json.loads(response.body.decode("utf-8"))
		except ValueError as exc:
			raise BitbucketError(
				"%s answered %d with a body that is not JSON: %s"
				% (subject, response.status, exc))

	def _error(self, response: HttpResponse, subject: str) -> Exception:
		"""Build the message from the server's own words, then add what it omits."""
		messages = []				# type: List[str]
		try:
			payload = json.loads(response.body.decode("utf-8"))
		except ValueError:
			payload = None
		if isinstance(payload, dict):
			for item in payload.get("errors") or []:
				if not isinstance(item, dict):
					continue
				text = str(item.get("message") or "").strip()
				context = item.get("context")
				if text and context:
					messages.append("%s: %s" % (context, text))
				elif text:
					messages.append(text)

		if not messages:
			messages.append(response.text().strip()[:400] or "no message")

		hint = _status_hint(response.status)
		detail = " | ".join(messages)
		if hint:
			return BitbucketError("%s — %s (%s)" % (subject, detail, hint))
		return BitbucketError("%s — %s" % (subject, detail))

	def paged(self, path: str, query: Optional[Dict[str, Any]] = None,
			limit: Optional[int] = None,
			subject: Optional[str] = None) -> Iterator[Any]:
		"""Follow nextPageStart, never start + size.

		The documentation is explicit that identifiers between pages are not
		guaranteed contiguous, so computing the next offset locally can skip
		items or repeat them.
		"""
		query = dict(query or {})
		start = 0					# type: Optional[int]
		seen = 0
		while start is not None:
			query["start"] = start
			query["limit"] = PAGE_SIZE
			page = self.request("GET", path, query=query, subject=subject)
			if not isinstance(page, dict):
				return
			for value in page.get("values") or []:
				yield value
				seen += 1
				if limit is not None and seen >= limit:
					return
			if page.get("isLastPage", True):
				return
			nxt = page.get("nextPageStart")
			start = nxt if isinstance(nxt, int) else None

	# -- reads ----------------------------------------------------------

	def application_properties(self) -> Dict[str, Any]:
		"""The version banner, and the response `whoami` reads its header off."""
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23 WITH a token: the banner
		# came back, and the response carried X-AUSERNAME naming a real user.
		# Both halves matter, because `whoami` observes nothing else -- it makes
		# THIS call and reads that header off THIS response, so a run that
		# printed a real username is the observation of the header.
		return self.request(
			"GET", "%s/application-properties" % API,
			subject="application properties")

	def whoami(self) -> Optional[str]:
		"""There is no `myself` endpoint; the username rides on every response."""
		self.application_properties()
		return self.last_username

	def repository(self, project: str, repo: str) -> Dict[str, Any]:
		"""Repository metadata, and the numeric id other endpoints demand."""
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23.
		return self.request(
			"GET", "%s/projects/%s/repos/%s" % (
				API, urllib.parse.quote(project), urllib.parse.quote(repo)),
			subject="repository %s/%s" % (project, repo))

	def pull_requests(self, project: str, repo: str,
			state: Optional[str] = None, at: Optional[str] = None,
			direction: Optional[str] = None, author: Optional[str] = None,
			limit: Optional[int] = None) -> List[Any]:
		"""List pull requests.

		Author filtering is NOT a first-class parameter on this endpoint -- it
		is the generic numbered participant filter, whose numbering has to
		start at 1 and stay contiguous.  A caller who expects `author=` gets a
		silently unfiltered list, which is why it is spelled out here.
		"""
		query = {
			"state": state,
			"at": normalize_ref(at) if at else None,
			"direction": direction,
			"order": "NEWEST",
		}							# type: Dict[str, Any]
		if author:
			query["username.1"] = author
			query["role.1"] = "AUTHOR"
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23, including the numbered
		# participant filter standing in for an author parameter.
		return list(self.paged(
			pr_path(project, repo), query=query, limit=limit,
			subject="pull requests in %s/%s" % (project, repo)))

	def pull_request(self, project: str, repo: str,
			pull_request_id: int) -> Dict[str, Any]:
		"""One pull request, including the version every write needs."""
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23.
		return self.request(
			"GET", pr_path(project, repo, pull_request_id),
			subject="pull request %d" % pull_request_id)

	def activities(self, project: str, repo: str, pull_request_id: int,
			limit: Optional[int] = None) -> List[Any]:
		"""The activity feed.

		The action enum is open by documentation -- new kinds arrive with
		versions and with plugins -- so nothing here hard-fails on one it does
		not recognise.
		"""
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23 (an OPENED item).
		return list(self.paged(
			pr_path(project, repo, pull_request_id, "/activities"),
			limit=limit,
			subject="activity of pull request %d" % pull_request_id))

	def mergeability(self, project: str, repo: str,
			pull_request_id: int) -> Dict[str, Any]:
		"""The non-mutating pre-flight: would this merge, and what objects.

		A GET on the same path the merge POST uses.  Reading it first is the
		difference between a refusal that explains itself and a 409 that could
		mean any of five things.
		"""
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23 (a CLEAN outcome; the
		# CONFLICTED and vetoed shapes are still only read, not observed).
		return self.request(
			"GET", pr_path(project, repo, pull_request_id, "/merge"),
			subject="mergeability of pull request %d" % pull_request_id)

	def default_reviewers(self, project: str, repo: str, repo_id: int,
			from_ref: str, to_ref: str) -> List[Any]:
		"""Who this branch pair would be given as reviewers.

		Note the REST root: this is the one endpoint in this file that does not
		live under /rest/api/1.0.

		OBSERVED on 9.4.23 to answer with a flat array of user objects, not the
		array of conditions the reference describes.  `_flatten_reviewers`
		accepts both rather than betting on either.
		"""
		path = "%s/projects/%s/repos/%s/reviewers" % (
			DEFAULT_REVIEWERS_API,
			urllib.parse.quote(project), urllib.parse.quote(repo))
		query = {
			"sourceRepoId": repo_id,
			"sourceRefId": from_ref,
			"targetRepoId": repo_id,
			"targetRefId": to_ref,
		}
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23 -- and the RESPONSE shape
		# the reference describes turned out to be wrong for this endpoint; see
		# _flatten_reviewers.
		result = self.request(
			"GET", path, query=query,
			subject="default reviewers for %s -> %s" % (
				short_ref(from_ref), short_ref(to_ref)))
		return result if isinstance(result, list) else []

	def build_statuses(self, commit_id: str) -> List[Any]:
		"""Every build status reported against one commit.

		Keyed by COMMIT, not by pull request -- Bitbucket stores the status
		against the sha and the pull request merely displays whatever sits on
		its source tip.  So a stale answer here means the branch moved, not
		that the build vanished.
		"""
		# The commit id is the one value in this file that reaches a URL PATH
		# with no pattern in front of it -- project and repo have RX_PROJECT_KEY
		# and RX_REPO_SLUG.  It arrives from the SERVER's own pull-request
		# payload, so what is modelled here is a hostile or compromised
		# instance, not a caller: no CLI flag reaches this argument.  quote()
		# was also called with its default safe="/", which leaves `/` intact and
		# never touches `.`, so a value shaped like a traversal stayed shaped
		# like one all the way into the request line.  Both halves are fixed:
		# the shape is asserted, and nothing structural is left unescaped.
		if not RX_COMMIT_SHA.match(commit_id):
			raise SetupError(
				"refusing to ask about commit %r — a commit id is 7 to 64 hex "
				"digits, and this one came out of the server's own answer, so "
				"the instance is not replying the way Bitbucket does"
				% commit_id[:80])
		# VERIFIED 2026-09-15 against Bitbucket 9.4.23: returned one
		# SUCCESSFUL entry naming the Jenkins job and its build URL.
		return list(self.paged(
			"%s/commits/%s" % (BUILD_STATUS_API,
				urllib.parse.quote(commit_id, safe="")),
			subject="build statuses for commit %s" % commit_id[:12]))


# ---------------------------------------------------------------------------
# rendering: four helpers, deliberately not a templating engine
# ---------------------------------------------------------------------------

def md_escape(text: str) -> str:
	"""Make one value safe to sit inside a Markdown table cell.

	A `|` anywhere in a pull-request title opens a new COLUMN: the row grows a
	cell, stops matching its header, and every downstream renderer draws the
	rest of the table wrong -- silently, because the output is still valid
	Markdown, just not the table that was meant.  A newline ends the row
	outright, which is why it collapses to a space rather than being escaped.
	"""
	return " ".join(str(text).splitlines()).replace("|", "\\|")


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
	"""A GitHub-flavoured table, or `_(none)_` when there is nothing to show."""
	if not rows:
		return "_(none)_"
	out = ["| " + " | ".join(md_escape(h) for h in headers) + " |",
		"| " + " | ".join("---" for _ in headers) + " |"]
	for row in rows:
		out.append("| " + " | ".join(
			md_escape("" if cell is None else str(cell)) for cell in row) + " |")
	return "\n".join(out)


def md_kv(pairs: Sequence[Tuple[str, Any]]) -> str:
	"""A two-column table, with the EMPTY pairs left out rather than dashed."""
	rows = [[label, value] for label, value in pairs
		if value is not None and str(value).strip()]
	return md_table(("Field", "Value"), rows)


def md_heading(level: int, text: str) -> str:
	"""`#` repeated, clamped to the six levels Markdown actually has."""
	return "%s %s" % ("#" * max(1, min(6, int(level))), text)


def emit_json(payload: Any) -> None:
	"""Machine output owns stdout alone."""
	print(json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False))


def summary(text: str) -> None:
	"""The human one-liner goes to stderr, so --json stays pipeable."""
	sys.stderr.write("--- %s ---\n" % text)


def _person(entry: Any) -> str:
	"""A user reference here has three usable names; prefer the readable one."""
	if not isinstance(entry, dict):
		return ""
	user = entry.get("user") if isinstance(entry.get("user"), dict) else entry
	return str(user.get("displayName") or user.get("name")
		or user.get("slug") or "")


def _pr_row(pull_request: Dict[str, Any]) -> List[Any]:
	"""One pull request as a table row."""
	from_ref = (pull_request.get("fromRef") or {}).get("displayId", "")
	to_ref = (pull_request.get("toRef") or {}).get("displayId", "")
	return [
		pull_request.get("id"),
		pull_request.get("state"),
		pull_request.get("title"),
		"%s -> %s" % (from_ref, to_ref),
		_person(pull_request.get("author")),
	]


def _self_link(payload: Dict[str, Any]) -> str:
	"""The browsable URL, buried under links.self[0].href."""
	links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
	for entry in links.get("self") or []:
		if isinstance(entry, dict) and entry.get("href"):
			return str(entry["href"])
	return ""


def _flatten_reviewers(entries: Sequence[Any]) -> List[str]:
	"""Usernames out of whatever the default-reviewers endpoint answered with.

	OBSERVED on Bitbucket 9.4.23: this endpoint returns a FLAT array of user
	objects.  The published reference describes an array of condition objects
	each carrying a nested `reviewers` list -- which is the shape the sibling
	`/conditions` endpoints use, not this one.  Both are accepted here, because
	a client that understood only the documented shape reported "1 condition, 0
	reviewers" against a real instance: a silent zero, indistinguishable from a
	repository that genuinely has no default reviewers configured.
	"""
	names = []						# type: List[str]

	def _add(candidate: Any) -> None:
		"""One user object, however it was reached."""
		if not isinstance(candidate, dict):
			return
		name = candidate.get("name") or candidate.get("slug")
		if name and name not in names:
			names.append(str(name))

	for entry in entries:
		if not isinstance(entry, dict):
			continue
		nested = entry.get("reviewers")
		if isinstance(nested, list):
			for reviewer in nested:
				if not isinstance(reviewer, dict):
					continue
				user = reviewer.get("user")
				_add(user if isinstance(user, dict) else reviewer)
			continue
		_add(entry)
	return names


def _repo_id(client: Bitbucket, project: str, repo: str) -> int:
	"""The default-reviewers endpoint wants numeric repository ids, not slugs."""
	payload = client.repository(project, repo)
	repo_id = payload.get("id")
	if not isinstance(repo_id, int):
		raise BitbucketError(
			"repository %s/%s did not report a numeric id" % (project, repo))
	return repo_id


# ---------------------------------------------------------------------------
# read subcommands
# ---------------------------------------------------------------------------

def cmd_version(args: argparse.Namespace, client: Bitbucket) -> int:
	"""The version banner, and the cheapest proof the URL points at Bitbucket."""
	payload = client.application_properties()
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Instance"))
		print(md_kv([
			("displayName", payload.get("displayName")),
			("version", payload.get("version")),
			("buildNumber", payload.get("buildNumber")),
			("base URL", client.cfg.base_url),
		]))
	summary("%s %s" % (payload.get("displayName", "Bitbucket"),
		payload.get("version", "?")))
	return OK


def cmd_whoami(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Who the token is, read off the header every response carries."""
	username = client.whoami()
	if not username:
		raise BitbucketError(
			"the server did not send %s — cannot tell who this token is"
			% HEADER_USERNAME)
	if args.json:
		emit_json({"username": username, "baseUrl": client.cfg.base_url})
	else:
		print(md_heading(2, "Identity"))
		print(md_kv([("username", username), ("base URL", client.cfg.base_url)]))
	summary("authenticated as %s" % username)
	return OK


def cmd_repo(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Repository metadata for the resolved project/repo pair."""
	project, repo = _target(args)
	payload = client.repository(project, repo)
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "%s/%s" % (project, repo)))
		print(md_kv([
			("id", payload.get("id")),
			("name", payload.get("name")),
			("state", payload.get("state")),
			("default branch", payload.get("defaultBranch")),
			("public", payload.get("public")),
			("archived", payload.get("archived")),
		]))
	summary("%s/%s id=%s" % (project, repo, payload.get("id")))
	return OK


def cmd_pr_list(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Pull requests, newest first."""
	project, repo = _target(args)
	rows = client.pull_requests(
		project, repo, state=args.state, at=args.at,
		direction=args.direction, author=args.author, limit=args.limit)
	if args.json:
		emit_json(rows)
	else:
		print(md_heading(2, "Pull requests in %s/%s" % (project, repo)))
		print(md_table(
			("Id", "State", "Title", "Branches", "Author"),
			[_pr_row(row) for row in rows if isinstance(row, dict)]))
	summary("%d pull request(s)" % len(rows))
	return OK


def cmd_pr_get(args: argparse.Namespace, client: Bitbucket) -> int:
	"""One pull request in full, including its lock version."""
	project, repo = _target(args)
	payload = client.pull_request(project, repo, args.id)
	if args.json:
		emit_json(payload)
		summary("pull request %d" % args.id)
		return OK

	from_ref = (payload.get("fromRef") or {}).get("displayId", "")
	to_ref = (payload.get("toRef") or {}).get("displayId", "")
	print(md_heading(2, "#%s %s" % (payload.get("id"), payload.get("title"))))
	print(md_kv([
		("state", payload.get("state")),
		("version", payload.get("version")),
		("author", _person(payload.get("author"))),
		("branches", "%s -> %s" % (from_ref, to_ref)),
		("open", payload.get("open")),
		("locked", payload.get("locked")),
		("url", _self_link(payload)),
	]))
	reviewers = payload.get("reviewers") or []
	if reviewers:
		print("")
		print(md_heading(3, "Reviewers"))
		print(md_table(
			("Reviewer", "Status", "Approved"),
			[[_person(r), r.get("status"), r.get("approved")]
				for r in reviewers if isinstance(r, dict)]))
	description = payload.get("description")
	if description:
		print("")
		print(md_heading(3, "Description"))
		print(description)
	summary("pull request %d is %s at version %s" % (
		args.id, payload.get("state"), payload.get("version")))
	return OK


def cmd_pr_activities(args: argparse.Namespace, client: Bitbucket) -> int:
	"""The activity feed, optionally narrowed to one action."""
	project, repo = _target(args)
	rows = client.activities(project, repo, args.id, limit=args.limit)
	if args.action:
		wanted = args.action.upper()
		rows = [r for r in rows if isinstance(r, dict)
			and str(r.get("action", "")).upper() == wanted]

	if args.json:
		emit_json(rows)
		summary("%d activity item(s)" % len(rows))
		return OK

	table = []						# type: List[List[Any]]
	for row in rows:
		if not isinstance(row, dict):
			continue
		comment = row.get("comment") if isinstance(row.get("comment"), dict) else {}
		table.append([
			row.get("id"),
			row.get("action"),
			_person(row.get("user")),
			(comment.get("text") or "")[:160],
		])
	print(md_heading(2, "Activity on pull request %d" % args.id))
	print(md_table(("Id", "Action", "Who", "Text"), table))
	summary("%d activity item(s)" % len(rows))
	return OK


def cmd_pr_mergeability(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Would it merge, and if not, which check objects."""
	project, repo = _target(args)
	payload = client.mergeability(project, repo, args.id)
	vetoes = payload.get("vetoes") or []
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Mergeability of pull request %d" % args.id))
		print(md_kv([
			("outcome", payload.get("outcome")),
			("conflicted", payload.get("conflicted")),
			("vetoes", len(vetoes)),
		]))
		if vetoes:
			print("")
			print(md_heading(3, "Vetoes"))
			print(md_table(
				("Summary", "Detail"),
				[[v.get("summaryMessage"), v.get("detailedMessage")]
					for v in vetoes if isinstance(v, dict)]))
	summary("outcome=%s vetoes=%d" % (payload.get("outcome"), len(vetoes)))
	return OK


def cmd_pr_reviewers(args: argparse.Namespace, client: Bitbucket) -> int:
	"""What the repository would auto-assign for a branch pair."""
	project, repo = _target(args)
	repo_id = _repo_id(client, project, repo)
	entries = client.default_reviewers(
		project, repo, repo_id,
		normalize_ref(args.from_ref), normalize_ref(args.to_ref))
	names = _flatten_reviewers(entries)
	if args.json:
		emit_json({"entries": entries, "reviewers": names})
	else:
		print(md_heading(2, "Default reviewers %s -> %s" % (
			args.from_ref, args.to_ref)))
		print(md_table(("Reviewer",), [[name] for name in names]))
	summary("%d default reviewer(s) from %d entry/entries" % (
		len(names), len(entries)))
	return OK


def _build_verdict(states: Sequence[str]) -> str:
	"""One word for a set of builds, worst member first.

	A pull request with a green build and a red one is not green, and a caller
	who only reads the first row would believe otherwise.

	The fallback, which this docstring used to leave out: an empty set is NONE,
	a set holding any state this file knows reports the unhappiest of them, and
	anything else falls through to the FIRST state given.  That last branch is
	deliberate -- a state Bitbucket grew after this was written is informative
	and is passed through rather than flattened.  It does mean a BLANK state in
	comes straight back out, which is why the caller names a missing one:
	`md_kv` drops an empty value, so a blank verdict does not read as odd, it
	disappears from the output entirely.
	"""
	if not states:
		return "NONE"
	for state in BUILD_PRECEDENCE:
		if state in states:
			return state
	return states[0]


def cmd_pr_builds(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Build statuses reported against a pull request's source tip.

	Two requests: read the pull request to learn its tip commit, then ask what
	was reported against that sha.  Reading the tip rather than taking one on
	the command line is the point -- a build status answered for a commit the
	pull request has moved past is worse than no answer.
	"""
	project, repo = _target(args)
	pull_request = client.pull_request(project, repo, args.id)
	commit_id = (pull_request.get("fromRef") or {}).get("latestCommit")
	if not commit_id:
		raise BitbucketError(
			"pull request %d did not report a source tip commit — cannot ask "
			"what was built" % args.id)

	rows = client.build_statuses(str(commit_id))
	# A row whose `state` is missing or blank is NAMED, not left empty.  An
	# empty string reaches _build_verdict, falls through to its first-state
	# fallback and comes back as an empty verdict -- which md_kv then drops,
	# because it drops blank values.  The result was that the verdict row went
	# MISSING from the output: not wrong, absent.  A state that is present but
	# unrecognised is left exactly as it arrived; it says something.
	states = [str(row.get("state") or "").strip() or "UNKNOWN" for row in rows
		if isinstance(row, dict)]
	verdict = _build_verdict(states)

	# Computed ONCE, above the branch.  SKILL.md advertises the exit code as
	# what makes this command usable as a gate, and --json is the form a gate
	# actually pipes into jq -- which is precisely the path that used to return
	# a hard-coded OK, so every JSON-consuming gate was silently always green.
	# The exit code answers the question asked; it does not depend on the
	# format the answer is printed in.
	code = FINDING if verdict == BUILD_FAILED else OK

	if args.json:
		emit_json({"commit": commit_id, "verdict": verdict, "builds": rows})
		summary("%s — %d build(s) on %s" % (
			verdict, len(rows), str(commit_id)[:12]))
		return code

	print(md_heading(2, "Builds on pull request %d" % args.id))
	print(md_kv([
		("source tip", commit_id),
		("verdict", verdict),
		("builds", len(rows)),
	]))
	if rows:
		print("")
		print(md_table(
			("State", "Name", "URL"),
			[[row.get("state"), row.get("name"), row.get("url")]
				for row in rows if isinstance(row, dict)]))
	summary("%s — %d build(s) on %s" % (
		verdict, len(rows), str(commit_id)[:12]))
	return code


# ---------------------------------------------------------------------------
# write subcommands
# ---------------------------------------------------------------------------

def _dry_run(method: str, base_url: str, path: str, body: Optional[Any],
		query: Optional[Dict[str, Any]] = None) -> int:
	"""Print exactly what would go on the wire, send nothing, succeed."""
	print("```")
	print("%s %s" % (method, api_url(base_url, path, query)))
	print("```")
	if body is not None:
		print("```json")
		print(json.dumps(body, indent=2, sort_keys=True))
		print("```")
	summary("dry run — nothing was sent")
	return OK


def _body_text(raw: str) -> str:
	"""The text ITSELF, with `-` meaning stdin.

	For arguments that ARE the content -- a comment body, a merge message --
	so a long one never has to survive shell quoting.
	"""
	if raw == "-":
		return sys.stdin.read()
	return raw


def _file_text(raw: str) -> str:
	"""The text NAMED BY a path, with `-` still meaning stdin.

	Deliberately separate from _body_text.  The two take arguments that look
	identical and mean opposite things: one is handed content, the other a
	path.  Routing --description-file through _body_text posted the path
	itself as the pull request's description, which a dry run caught and a
	live run would have published.
	"""
	if raw == "-":
		return sys.stdin.read()
	try:
		with open(raw, "r", encoding="utf-8") as handle:
			return handle.read()
	except OSError as exc:
		raise SetupError("cannot read %s: %s" % (raw, exc))


def _assert_version(args: argparse.Namespace,
		pull_request: Dict[str, Any]) -> int:
	"""The freshly read version wins; --version is checked against it.

	WHY THIS IS NOT AN INPUT.  Letting a caller supply the version they
	remember is how a write lands on a pull request that moved underneath them.
	The server's current value is used either way; passing --version only asks
	this script to refuse when the two disagree, which turns a remote 409 into
	a local explanation.
	"""
	version = pull_request.get("version")
	if not isinstance(version, int):
		raise BitbucketError(
			"pull request %s did not report a numeric version — refusing to "
			"write without an optimistic lock" % pull_request.get("id"))
	claimed = getattr(args, "version", None)
	if claimed is not None and claimed != version:
		raise SetupError(
			"--version %d does not match the server's current version %d — the "
			"pull request moved since you read it; read it again and decide "
			"again" % (claimed, version))
	return version


def _resolve_reviewers(args: argparse.Namespace, block: Dict[str, Any],
		client: Bitbucket, project: str, repo: str,
		from_ref: str, to_ref: str) -> List[str]:
	"""--reviewer wins, then the profile, and @default asks the server.

	@default cannot be expanded at parse time because the answer depends on the
	branch pair, which is exactly why it is a sentinel and not a default value.
	"""
	names = list(args.reviewer or [])
	if not names:
		names = [str(name) for name in (block.get("reviewers") or [])]

	out = []						# type: List[str]
	for name in names:
		if name != SENTINEL_DEFAULT:
			if name not in out:
				out.append(name)
			continue
		repo_id = _repo_id(client, project, repo)
		entries = client.default_reviewers(
			project, repo, repo_id, from_ref, to_ref)
		for resolved in _flatten_reviewers(entries):
			if resolved not in out:
				out.append(resolved)
	return out


def cmd_pr_create(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Open a pull request from the profile's defaults plus the flags given."""
	profile, profile_path = load_profile(getattr(args, "profile", None))
	project, repo = _target(args, profile)
	block = project_profile(profile, project)
	_check_required(args, block)

	from_ref = normalize_ref(args.from_ref)
	to_ref = normalize_ref(args.to_ref or block.get("target") or "master")

	description = ""
	if args.description_file:
		description = _file_text(args.description_file)
	elif args.description:
		description = args.description

	reviewers = _resolve_reviewers(
		args, block, client, project, repo, from_ref, to_ref)

	# Both refs carry a repository object even for a same-repo pull request;
	# that nesting is how a fork's pull request is expressed, and the endpoint
	# wants the shape either way.
	ref_repo = {"slug": repo, "project": {"key": project}}
	body = {
		"title": args.title,
		"description": description,
		"fromRef": {"id": from_ref, "repository": dict(ref_repo)},
		"toRef": {"id": to_ref, "repository": dict(ref_repo)},
		"reviewers": [{"user": {"name": name}} for name in reviewers],
	}								# type: Dict[str, Any]

	path = pr_path(project, repo)
	if args.dry_run:
		if profile_path:
			summary("profile: %s" % profile_path)
		return _dry_run("POST", client.cfg.base_url, path, body)

	# VERIFIED 2026-09-15 against Bitbucket 9.4.23: this body opened PR 1212.
	payload = client.request(
		"POST", path, body=body,
		subject="creating a pull request in %s/%s" % (project, repo))
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Pull request %s created" % payload.get("id")))
		print(md_kv([
			("id", payload.get("id")),
			("title", payload.get("title")),
			("state", payload.get("state")),
			("version", payload.get("version")),
			("url", _self_link(payload)),
		]))
	summary("created pull request %s" % payload.get("id"))
	return OK


def cmd_pr_comment(args: argparse.Namespace, client: Bitbucket) -> int:
	"""A general comment on a pull request.

	Only the un-anchored form is supported.  An inline comment needs a diff
	anchor carrying both commit hashes, a line number and a line type, and
	assembling one from a handful of CLI flags is how a comment ends up
	attached to the wrong line of the wrong file.
	"""
	project, repo = _target(args)
	text = _body_text(args.text)
	if not text.strip():
		raise SetupError("refusing to post an empty comment")

	body = {"text": text}			# type: Dict[str, Any]
	if args.parent:
		body["parent"] = {"id": args.parent}

	path = pr_path(project, repo, args.id, "/comments")
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)

	# UNVERIFIED -- body shape read from the published reference, never observed.
	payload = client.request(
		"POST", path, body=body,
		subject="commenting on pull request %d" % args.id)
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Comment added"))
		print(md_kv([
			("id", payload.get("id")),
			("author", _person(payload.get("author"))),
			("version", payload.get("version")),
		]))
	summary("comment %s added to pull request %d" % (payload.get("id"), args.id))
	return OK


def cmd_pr_approve(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Set this token's review status on a pull request.

	The old POST .../approve has been deprecated since Bitbucket 4.2.  This
	uses the participant endpoint, which is the current spelling, and sends
	lastReviewedCommit -- the concurrency check moved there from the version
	query parameter, which is itself now deprecated.  Two deprecation layers on
	one feature is why copy-pasting an older recipe here goes wrong.
	"""
	project, repo = _target(args)
	username = client.whoami()
	if not username:
		raise BitbucketError("cannot set a review status without knowing who "
			"the token is")

	pull_request = client.pull_request(project, repo, args.id)
	body = {"status": args.status}	# type: Dict[str, Any]
	latest = (pull_request.get("fromRef") or {}).get("latestCommit")
	if latest:
		body["lastReviewedCommit"] = latest

	path = pr_path(project, repo, args.id,
		"/participants/%s" % urllib.parse.quote(username))
	if args.dry_run:
		return _dry_run("PUT", client.cfg.base_url, path, body)

	# UNVERIFIED -- endpoint and body read from the published reference.
	payload = client.request(
		"PUT", path, body=body,
		subject="setting the review status on pull request %d" % args.id)
	status = payload.get("status") if isinstance(payload, dict) else args.status
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Review status"))
		print(md_kv([
			("reviewer", _person(payload) or username),
			("status", status),
		]))
	summary("%s is now %s on pull request %d" % (username, status, args.id))
	return OK


def _state_change(args: argparse.Namespace, client: Bitbucket, action: str,
		expected: str, extra: Optional[Dict[str, Any]] = None) -> int:
	"""decline and reopen differ only in a word and an expected end state."""
	project, repo = _target(args)
	pull_request = client.pull_request(project, repo, args.id)
	version = _assert_version(args, pull_request)

	body = {"version": version}		# type: Dict[str, Any]
	if extra:
		body.update(extra)

	path = pr_path(project, repo, args.id, "/%s" % action)
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)

	# UNVERIFIED -- body shape read from the published schema, never observed.
	payload = client.request(
		"POST", path, body=body,
		subject="%s of pull request %d" % (action, args.id))
	state = payload.get("state") if isinstance(payload, dict) else None
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Pull request %d %sd" % (args.id, action)))
		print(md_kv([("state", state), ("expected", expected)]))
	summary("pull request %d is now %s" % (args.id, state or expected))
	return OK


def cmd_pr_decline(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Decline a pull request at the version the server currently holds."""
	return _state_change(args, client, "decline", "DECLINED",
		extra={"comment": args.comment} if args.comment else None)


def cmd_pr_reopen(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Reopen a declined pull request at the version the server currently holds."""
	return _state_change(args, client, "reopen", "OPEN")


def cmd_pr_merge(args: argparse.Namespace, client: Bitbucket) -> int:
	"""Merge a pull request, behind four separate refusals.

	WHY FOUR AND NOT A PROMPT.  This is the only call in the file that no later
	call can undo, and it is here over an explicit objection, which means the
	gate is the thing carrying the decision.  Each refusal is a distinct
	failure mode so that each can be asserted on its own:

	  1. BITBUCKET_READ_ONLY, checked in main() before the client exists, so it
	     fires without a single request being sent.
	  2. --yes, absent by default, so no merge is ever one typo away.  Checked
	     before the pre-flight, so a refused merge reads nothing either.
	  3. the mergeability pre-flight, which must come back CLEAN with no vetoes
	     and no conflict unless --ignore-vetoes is given as well.
	  4. the version assertion, which refuses when --version disagrees with
	     what the server currently holds.

	A gate with no case asserting it is a gate that can stop gating silently.
	"""
	project, repo = _target(args)

	if not args.yes:
		raise SetupError(
			"refusing to merge pull request %d without --yes — merging is the "
			"one operation here that no later call can undo" % args.id)

	check = client.mergeability(project, repo, args.id)
	outcome = str(check.get("outcome") or MERGE_UNKNOWN)
	vetoes = check.get("vetoes") or []
	blocked = (outcome != MERGE_CLEAN or bool(vetoes)
		or bool(check.get("conflicted")))
	if blocked and not args.ignore_vetoes:
		reasons = [str(veto.get("summaryMessage") or "").strip()
			for veto in vetoes if isinstance(veto, dict)]
		raise SetupError(
			"refusing to merge pull request %d — the pre-flight reports "
			"outcome=%s conflicted=%s with %d veto(es)%s. Pass --ignore-vetoes "
			"only when you know which check you are overriding"
			% (args.id, outcome, check.get("conflicted"), len(vetoes),
				(": " + "; ".join(r for r in reasons if r)) if reasons else ""))

	pull_request = client.pull_request(project, repo, args.id)
	version = _assert_version(args, pull_request)

	body = {"version": version}		# type: Dict[str, Any]
	if args.message:
		body["message"] = _body_text(args.message)
	if args.strategy:
		body["strategyId"] = args.strategy

	path = pr_path(project, repo, args.id, "/merge")
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)

	# UNVERIFIED -- body shape read from the published schema, never observed.
	# This path in particular has never been run against a live instance.
	payload = client.request(
		"POST", path, body=body,
		subject="merging pull request %d" % args.id)
	state = payload.get("state") if isinstance(payload, dict) else None
	if args.json:
		emit_json(payload)
	else:
		print(md_heading(2, "Pull request %d merged" % args.id))
		print(md_kv([
			("state", state),
			("outcome before merge", outcome),
			("vetoes overridden", len(vetoes) if args.ignore_vetoes else 0),
		]))
	summary("pull request %d is now %s" % (args.id, state or "MERGED"))
	return OK


# ---------------------------------------------------------------------------
# profiles: everything project-specific, and none of it in this file
# ---------------------------------------------------------------------------

# WHY THIS FUNCTION IS A VERBATIM COPY, DEFECT INCLUDED.
#
# The body below, from `here = os.path.abspath(os.getcwd())` onwards, is
# byte-for-byte jira.py's `_profile_path`.  Only the four lines above it differ,
# and only because they name this script's own environment variable.  It
# carries a KNOWN OPEN DEFECT, recorded but not yet fixed there:
#
#   `here == home` is raw string equality between two paths produced by
#   normalisations of DIFFERENT STRENGTH.  os.getcwd() is guaranteed on POSIX to
#   return the resolved, symlink-free physical path, while
#   os.path.expanduser("~") returns whatever $HOME literally says.  So the
#   comparison does not fail by accident -- it fails systematically whenever
#   $HOME is a symlinked spelling (/Users/x versus /System/Volumes/Data/Users/x
#   on macOS is the textbook case) or differs in case on a case-insensitive
#   volume.  The walk then falls through to the `parent == here` filesystem-root
#   check and climbs past $HOME, reading, for instance,
#   /tmp/.claude/bitbucket.json on a shared machine.
#
# It was copied AS IS on purpose rather than corrected here.  A generated region
# requires a byte-identical body, so two identical copies stay foldable into one
# canonical source later, while a corrected copy would diverge and make that
# unification a change to jira.py's behaviour smuggled inside a refactor.  The
# fix belongs in both sites at once, or in the shared source that replaces them.
#
# Do not read the presence of a second consumer as evidence that the behaviour
# is settled.  It is not.

def _profile_path(explicit: Optional[str]) -> Optional[str]:
	"""--profile, then BITBUCKET_PROFILE, then the walk up from the cwd."""
	if explicit:
		return explicit
	from_env, _ = _env_first("BITBUCKET_PROFILE", "bitbucket_profile")
	if from_env:
		return from_env
	here = os.path.abspath(os.getcwd())
	home = os.path.abspath(os.path.expanduser("~"))
	while True:
		candidate = os.path.join(here, PROFILE_FILENAME)
		if os.path.isfile(candidate):
			return candidate
		parent = os.path.dirname(here)
		if here == home or parent == here:
			return None
		here = parent


def load_profile(explicit: Optional[str]) -> Tuple[Dict[str, Any],
		Optional[str]]:
	"""(profile, path).  No profile anywhere is FINE.

	An explicitly named one that is missing is not: naming a path is a claim
	that it exists, and silently falling back to "no defaults at all" would
	open a pull request against the wrong branch and report success.
	"""
	path = _profile_path(explicit)
	if not path:
		return {}, None
	if not os.path.isfile(path):
		raise SetupError("profile not found: %s" % path)
	try:
		with open(path, "r", encoding="utf-8") as handle:
			data = json.load(handle)
	except OSError as exc:
		raise SetupError("cannot read profile %s: %s" % (path, exc))
	except ValueError as exc:
		raise SetupError("profile %s is not valid JSON: %s" % (path, exc))
	if not isinstance(data, dict):
		raise SetupError("profile %s must be a JSON object" % path)
	return data, path


def project_profile(profile: Dict[str, Any], project: str) -> Dict[str, Any]:
	"""`defaults` with `projects.<KEY>` laid over it, ONE level deep.

	One level and not recursive: a deep merge would reach inside a reviewer
	list or a ref object to combine two values that were each written to be
	used whole.
	"""
	block = {}						# type: Dict[str, Any]
	for source in (profile.get("defaults"),
			(profile.get("projects") or {}).get(project)):
		if isinstance(source, dict):
			block.update(source)
	return block


def _profile_project(profile: Dict[str, Any], explicit: Optional[str]) -> str:
	"""--project wins, then BITBUCKET_PROJECT, then the profile's own key."""
	candidate = (explicit or _env_first("BITBUCKET_PROJECT")[0]
		or profile.get("project"))
	if not candidate:
		raise SetupError(
			"no project key — pass --project, set BITBUCKET_PROJECT, or put "
			"\"project\" in %s" % PROFILE_FILENAME)
	candidate = str(candidate)
	if not RX_PROJECT_KEY.match(candidate):
		raise SetupError("not a project key: %r" % candidate)
	return candidate


def _profile_repo(profile: Dict[str, Any], block: Dict[str, Any],
		explicit: Optional[str]) -> str:
	"""--repo wins, then BITBUCKET_REPO, then the project block, then the root."""
	candidate = (explicit or _env_first("BITBUCKET_REPO")[0]
		or block.get("repo") or profile.get("repo"))
	if not candidate:
		raise SetupError(
			"no repository slug — pass --repo, set BITBUCKET_REPO, or put "
			"\"repo\" in %s" % PROFILE_FILENAME)
	candidate = str(candidate)
	if not RX_REPO_SLUG.match(candidate):
		raise SetupError("not a repository slug: %r" % candidate)
	return candidate


def _target(args: argparse.Namespace,
		profile: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
	"""The (project, repo) pair every subcommand needs, resolved in one place."""
	if profile is None:
		profile, _ = load_profile(getattr(args, "profile", None))
	project = _profile_project(profile, getattr(args, "project", None))
	block = project_profile(profile, project)
	repo = _profile_repo(profile, block, getattr(args, "repo", None))
	return project, repo


def _check_required(args: argparse.Namespace, block: Dict[str, Any]) -> None:
	"""The profile's own `require` list, enforced before any network call.

	This is NOT the server's validation.  It exists so a project can insist on
	something the server is perfectly happy to omit -- a target branch, a
	reviewer set -- and have the refusal arrive as exit 2 rather than as a pull
	request nobody was asked to review.
	"""
	required = block.get("require") or []
	if not isinstance(required, (list, tuple)):
		raise SetupError("\"require\" in the profile must be a list")

	unset = []						# type: List[str]
	for name in required:
		name = str(name)
		if name == "reviewers":
			if not (args.reviewer or block.get("reviewers")):
				unset.append(name)
			continue
		if name == "target":
			if not (args.to_ref or block.get("target")):
				unset.append(name)
			continue
		if not getattr(args, name.replace("-", "_"), None) \
				and not block.get(name):
			unset.append(name)
	if unset:
		raise SetupError(
			"the profile requires these and they are unset: %s"
			% ", ".join(sorted(unset)))


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

HANDLERS = {
	"version": cmd_version,
	"whoami": cmd_whoami,
	"repo": cmd_repo,
	"pr-list": cmd_pr_list,
	"pr-get": cmd_pr_get,
	"pr-activities": cmd_pr_activities,
	"pr-mergeability": cmd_pr_mergeability,
	"pr-reviewers": cmd_pr_reviewers,
	"pr-builds": cmd_pr_builds,
	"pr-create": cmd_pr_create,
	"pr-comment": cmd_pr_comment,
	"pr-approve": cmd_pr_approve,
	"pr-decline": cmd_pr_decline,
	"pr-reopen": cmd_pr_reopen,
	"pr-merge": cmd_pr_merge,
}


def build_parser() -> argparse.ArgumentParser:
	"""One parser, three parent parsers, composed per subcommand."""
	common = argparse.ArgumentParser(add_help=False)
	common.add_argument("--url",
		help="base URL of the instance (overrides BITBUCKET_URL); a context "
			"path such as /bitbucket is preserved")
	common.add_argument("--token",
		help="HTTP access token (overrides BITBUCKET_TOKEN); never printed back")
	common.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
		help="HTTP timeout in seconds (default: %(default)s)")
	common.add_argument("--json", action="store_true",
		help="emit one JSON document on stdout instead of the Markdown one")

	scoped = argparse.ArgumentParser(add_help=False)
	scoped.add_argument("--profile",
		help="path to a profile, overriding the walk up from the cwd")
	scoped.add_argument("--project",
		help="project key (overrides BITBUCKET_PROJECT and the profile)")
	scoped.add_argument("--repo",
		help="repository slug (overrides BITBUCKET_REPO and the profile)")

	dry = argparse.ArgumentParser(add_help=False)
	dry.add_argument("--dry-run", action="store_true",
		help="print the method, URL and body that WOULD be sent, then exit 0")

	parser = argparse.ArgumentParser(prog=PROG,
		description="Pull requests on a Bitbucket Server / Data Center instance.")
	sub = parser.add_subparsers(dest="command", required=True)

	sub.add_parser("version", parents=[common],
		help="the instance version banner")
	sub.add_parser("whoami", parents=[common],
		help="which user the token authenticates as")
	sub.add_parser("repo", parents=[common, scoped],
		help="repository metadata")

	p = sub.add_parser("pr-list", parents=[common, scoped],
		help="list pull requests")
	p.add_argument("--state", default="OPEN",
		choices=("OPEN", "MERGED", "DECLINED", "ALL"),
		help="default: %(default)s")
	p.add_argument("--at",
		help="branch to filter on, short or fully qualified")
	p.add_argument("--direction", choices=("INCOMING", "OUTGOING"),
		help="INCOMING targets --at, OUTGOING is sourced from it")
	p.add_argument("--author",
		help="username; applied as the numbered participant filter this API "
			"uses instead of a first-class author parameter")
	p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
		help="stop after this many (default: %(default)s)")

	p = sub.add_parser("pr-get", parents=[common, scoped],
		help="one pull request in full")
	p.add_argument("id", type=int)

	p = sub.add_parser("pr-activities", parents=[common, scoped],
		help="the activity feed of a pull request")
	p.add_argument("id", type=int)
	p.add_argument("--action",
		help="keep only this action, e.g. COMMENTED or APPROVED")
	p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
		help="stop after this many (default: %(default)s)")

	p = sub.add_parser("pr-mergeability", parents=[common, scoped],
		help="would it merge, and what objects if not")
	p.add_argument("id", type=int)

	p = sub.add_parser("pr-builds", parents=[common, scoped],
		help="build statuses on a pull request's source tip")
	p.add_argument("id", type=int)

	p = sub.add_parser("pr-reviewers", parents=[common, scoped],
		help="default reviewers for a branch pair")
	p.add_argument("--from", dest="from_ref", required=True)
	p.add_argument("--to", dest="to_ref", default="master")

	p = sub.add_parser("pr-create", parents=[common, scoped, dry],
		help="open a pull request")
	p.add_argument("--from", dest="from_ref", required=True,
		help="source branch, short or fully qualified")
	p.add_argument("--to", dest="to_ref",
		help="target branch; defaults to the profile's target, then master")
	p.add_argument("--title", required=True)
	p.add_argument("--description", help="description text")
	p.add_argument("--description-file",
		help="read the description from this file, or - for stdin")
	p.add_argument("--reviewer", action="append",
		help="repeatable; %s asks the server which reviewers this branch pair "
			"would be given" % SENTINEL_DEFAULT)

	p = sub.add_parser("pr-comment", parents=[common, scoped, dry],
		help="add a general comment to a pull request")
	p.add_argument("id", type=int)
	p.add_argument("text", help="comment body, or - to read stdin")
	p.add_argument("--parent", type=int, help="reply to this comment id")

	p = sub.add_parser("pr-approve", parents=[common, scoped, dry],
		help="set this token's review status")
	p.add_argument("id", type=int)
	p.add_argument("--status", default="APPROVED",
		choices=("APPROVED", "UNAPPROVED", "NEEDS_WORK"),
		help="default: %(default)s")

	p = sub.add_parser("pr-decline", parents=[common, scoped, dry],
		help="decline a pull request")
	p.add_argument("id", type=int)
	p.add_argument("--comment", help="optional reason")
	p.add_argument("--version", type=int,
		help="assert the server currently holds this version; refuses on a "
			"mismatch instead of racing")

	p = sub.add_parser("pr-reopen", parents=[common, scoped, dry],
		help="reopen a declined pull request")
	p.add_argument("id", type=int)
	p.add_argument("--version", type=int,
		help="assert the server currently holds this version")

	p = sub.add_parser("pr-merge", parents=[common, scoped, dry],
		help="merge a pull request (gated)")
	p.add_argument("id", type=int)
	p.add_argument("--yes", action="store_true",
		help="required; without it the merge is refused before anything is read")
	p.add_argument("--ignore-vetoes", action="store_true",
		help="required additionally when the pre-flight reports a conflict or "
			"a vetoed merge check")
	p.add_argument("--message", help="merge commit message, or - to read stdin")
	p.add_argument("--strategy",
		help="strategyId, e.g. no-ff, squash, ff-only")
	p.add_argument("--version", type=int,
		help="assert the server currently holds this version")

	return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
	"""Parse, gate, dispatch, and map the two exception types onto exit codes."""
	parser = build_parser()
	args = parser.parse_args(argv)

	try:
		cfg = resolve_config(args)
	except SetupError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, exc))
		return USAGE

	if args.command in WRITE_COMMANDS and cfg.read_only:
		sys.stderr.write(
			"%s: BITBUCKET_READ_ONLY=%s (%s) — refusing the write subcommand "
			"%r\n" % (PROG, cfg.read_only_raw, cfg.sources["read_only"],
				args.command))
		return USAGE

	client = Bitbucket(cfg)
	try:
		return HANDLERS[args.command](args, client)
	except SetupError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, exc))
		return USAGE
	except BitbucketError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, exc))
		return FINDING
	except KeyboardInterrupt:
		sys.stderr.write("%s: interrupted\n" % PROG)
		return USAGE


if __name__ == "__main__":
	sys.exit(main())
