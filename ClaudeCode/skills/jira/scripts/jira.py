#!/usr/bin/env python3
"""jira.py -- a read-mostly Jira CLI over REST API v2, stdlib only, no ADF.

Six read subcommands and four write ones, aimed at the two deployments that
actually exist in the wild -- Atlassian Cloud and Server/Data Center -- out of
ONE code path.

WHY v2 ON BOTH.  v2 exists on Cloud with the identical operation set, and
staying off v3 keeps Atlassian Document Format entirely out of the picture: a
comment body is a plain string here, never a node tree.  Nothing in this file
knows what ADF is, and that is the point.

WHY THE DEPLOYMENT PROBE IS LAZY.  Exactly one operation genuinely differs
between Cloud and DC -- `search` -- so the deployment is resolved only when it
changes what gets sent: `GET /rest/api/2/serverInfo` once per process, cached in
memory (never on disk), falling back to a hostname heuristic when serverInfo is
unreachable or does not carry `deploymentType`.  Every other subcommand is
deployment-agnostic and never pays for the probe.

WHY SEARCH IS A POST ON BOTH.  A GET would put JQL in the query string, and JQL
percent-encoding is the single largest source of spurious 400s.  A POST body
sidesteps the question.  The two deployments then page differently and the
difference is NOT cosmetic:

    Cloud   POST /rest/api/2/search/jql   opaque `nextPageToken`, strictly
                                          sequential, NO total, NO startAt
    DC      POST /rest/api/2/search       `startAt` / `maxResults` / `total`

so `search_issues()` returns an ITERATOR and deliberately exposes no total:
Cloud does not have one, and DC calls it optional and may change it between
pages.  A caller that could read a total would come to depend on it.

WHY THE FIELD LIST IS EXPLICIT.  DC's search defaults to `*navigable` while
get-issue defaults to `*all`, so the only portable behaviour is to name the
fields.  `--fields '*all'` opts back out.

Usage:
    jira.py whoami
    jira.py search 'project = OPS AND statusCategory != Done' --limit 20
    jira.py search 'assignee = currentUser()' --fields '*all' --json
    jira.py get PROJ-1234 --comments --changelog
    jira.py transitions PROJ-1234
    jira.py projects
    jira.py fields --grep 'story points'
    jira.py comment PROJ-1234 'deployed to staging'
    jira.py comment PROJ-1234 -            # body from stdin
    jira.py transition PROJ-1234 'In Progress' --comment 'picking this up'
    jira.py transition PROJ-1234 31 --dry-run
    jira.py worklog PROJ-1234 '3h 20m' --comment 'pairing' --started 2026-08-26T09:00:00.000+0000
    jira.py attach PROJ-1234 ./build.log --name failing-build.log

Every flag below is also an environment variable, and the flag wins.

Environment variables
    JIRA_URL        required.  Base URL, context path included and preserved:
                    https://jira.corp.example/jira is joined path-safely, never
                    through urljoin (which would eat the /jira).  Flag: --url
    JIRA_TOKEN      required.  Cloud API token, or a DC Personal Access Token.
                    Never printed -- not even masked; whoami reports its SOURCE
                    and its LENGTH.  Flag: --token
    JIRA_EMAIL      optional, and it is the SWITCH: present selects Cloud Basic
                    auth (base64 of email:token), absent selects DC Bearer PAT.
                    Flag: --email
    JIRA_READ_ONLY  optional.  1 / true / yes (case-insensitive) makes every
                    write subcommand refuse with exit 2 before anything is sent.

    Lowercase spellings (jira_url, jira_token, jira_email, jira_read_only) are
    accepted as a fallback.

Output
    Human-readable plain text on stdout, one line per item, aligned; the summary
    line goes to stderr, so a pipeline gets rows and a human still gets a count.
    --json switches stdout to a single JSON document.

Exit codes
    0  the command did what it says
    1  a real finding: an API error, a transition name that resolves to nothing,
       or an empty search under --fail-empty
    2  bad invocation, missing configuration, a malformed issue key, a refused
       write under JIRA_READ_ONLY, or an unreachable host
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

PROG = "jira"
USER_AGENT = "prompt-heaven-jira/1.0"
API = "/rest/api/2"

CLOUD = "Cloud"
SERVER = "Server"

OK = 0
FINDING = 1
USAGE = 2

DEFAULT_TIMEOUT = 30.0
DEFAULT_LIMIT = 50
PAGE_SIZE = 50

# Retry ONLY 429 and 5xx.  Any other 4xx is a statement about the request, and
# repeating it merely repeats the mistake.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0

# DC's search defaults to *navigable and get-issue defaults to *all, so naming
# the fields is the only behaviour that means the same thing on both.
# `reporter` is here because `get` RENDERS it: a rendered row whose field was
# never requested prints a dash forever and reads as "this issue has no
# reporter" rather than "nobody asked for it".
DEFAULT_FIELDS = ["summary", "status", "assignee", "reporter", "issuetype",
	"priority", "updated"]

TRUTHY = ("1", "true", "yes")

WRITE_COMMANDS = ("attach", "comment", "transition", "worklog")

# `PROJ-1234`: at least two leading uppercase alphanumerics (the project key),
# a hyphen, then digits.  Checked locally because Jira answers a nonexistent
# key and an unreadable one with the SAME 404, so a typo would come back as a
# permissions question.
RX_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")

# Started timestamps are `%Y-%m-%dT%H:%M:%S.%f%z` -- milliseconds, and a
# +0000-style offset with NO colon.  Documented rather than reformatted: a
# silent rewrite of a caller's timestamp is worse than a server-side rejection.
STARTED_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


# ---------------------------------------------------------------------------
# errors: the two exit codes that are not 0
# ---------------------------------------------------------------------------

class JiraError(Exception):
	"""A finding -- the server answered, and the answer is bad news (exit 1)."""


class SetupError(Exception):
	"""Bad invocation, bad configuration, or an unreachable host (exit 2)."""


# ---------------------------------------------------------------------------
# configuration: flag > env, and every value remembers where it came from
# ---------------------------------------------------------------------------

def _env_first(*names: str) -> Tuple[Optional[str], Optional[str]]:
	"""Return (value, env_name_that_provided_it) -- or (None, None)."""
	for name in names:
		value = os.environ.get(name)
		if value:
			return value, name
	return None, None


def _pick(cli_value: Optional[str], cli_flag: str,
		*env_names: str) -> Tuple[Optional[str], str]:
	"""CLI flag overrides env. Returns (value, source description)."""
	if cli_value:
		return cli_value, cli_flag
	value, env = _env_first(*env_names)
	if value:
		return value, "env:%s" % env
	return None, "<unset>"


def _truthy(value: Optional[str]) -> bool:
	return str(value or "").strip().lower() in TRUTHY


class Config:
	"""Resolved connection settings plus the provenance of each one."""

	def __init__(self, base_url: str, token: str, email: Optional[str],
			read_only: bool, read_only_raw: Optional[str],
			sources: Dict[str, str], timeout: float = DEFAULT_TIMEOUT):
		self.base_url = base_url
		self.token = token
		self.email = email
		self.read_only = read_only
		self.read_only_raw = read_only_raw
		self.sources = sources
		self.timeout = timeout

	@property
	def auth_mode(self) -> str:
		"""Cloud Basic vs DC Bearer -- decided by the PRESENCE of an email."""
		if self.email:
			return "Basic (Cloud: JIRA_EMAIL present)"
		return "Bearer (Server/DC personal access token: JIRA_EMAIL absent)"

	@property
	def auth_header(self) -> str:
		return auth_header(self.email, self.token)


def auth_header(email: Optional[str], token: str) -> str:
	"""The Authorization value. The email is the switch, not a second secret."""
	if email:
		return "Basic " + base64.b64encode(
			("%s:%s" % (email, token)).encode()).decode()
	return "Bearer " + token


def resolve_config(args: argparse.Namespace) -> Config:
	"""Flag > env, then fail fast and loudly on anything still missing."""
	url, url_src = _pick(getattr(args, "url", None), "--url",
		"JIRA_URL", "jira_url")
	token, token_src = _pick(getattr(args, "token", None), "--token",
		"JIRA_TOKEN", "jira_token")
	email, email_src = _pick(getattr(args, "email", None), "--email",
		"JIRA_EMAIL", "jira_email")
	read_only_raw, read_only_env = _env_first("JIRA_READ_ONLY",
		"jira_read_only")

	missing = []
	if not url:
		missing.append("JIRA_URL (or --url)")
	if not token:
		missing.append("JIRA_TOKEN (or --token)")
	if missing:
		sys.stderr.write("%s: refusing to run — missing required "
			"configuration:\n" % PROG)
		for item in missing:
			sys.stderr.write("  - %s\n" % item)
		sys.stderr.write("Set these as environment variables or pass the "
			"matching flags. See `jira.py --help`.\n")
		sys.exit(USAGE)

	sources = {
		"url": url_src,
		"token": token_src,
		"email": email_src,
		"read_only": ("env:%s" % read_only_env) if read_only_env else "<unset>",
	}
	timeout = float(getattr(args, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
	return Config(url, token, email, _truthy(read_only_raw), read_only_raw,
		sources, timeout)


def _validate_issue_key(key: str) -> None:
	"""Reject a malformed key HERE, where the error can still be unambiguous.

	Jira answers "no such issue" and "you may not see this issue" with the same
	404, so a typo that reaches the server comes back as a permissions
	question.  Raises SetupError (exit 2 -- bad invocation), never a finding.
	"""
	if not RX_ISSUE_KEY.match(key or ""):
		raise SetupError("invalid issue key: %s (expected e.g. PROJ-1234)"
			% (key if key else "<empty>"))


# ---------------------------------------------------------------------------
# URL joining: a DC base URL often carries a context path, and urljoin eats it
# ---------------------------------------------------------------------------

def api_url(base: str, path: str,
		query: Optional[Dict[str, str]] = None) -> str:
	"""Join `base` + `path` path-safely, preserving any context path.

	`urljoin("https://host/jira", "/rest/api/2/myself")` yields
	`https://host/rest/api/2/myself` -- the `/jira` is gone, and the 404 that
	follows looks like a permissions problem.  So: strip exactly ONE trailing
	slash from the base and concatenate.
	"""
	root = base[:-1] if base.endswith("/") else base
	if not path.startswith("/"):
		path = "/" + path
	url = root + path
	if query:
		url += "?" + urllib.parse.urlencode(query)
	return url


def deployment_from_url(base_url: str) -> str:
	"""The fallback when serverInfo cannot answer: judge by hostname."""
	host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
	if host.endswith(".atlassian.net") or host == "api.atlassian.com":
		return CLOUD
	return SERVER


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

class HttpResponse:
	"""One HTTP answer, with headers normalised to lowercase keys.

	The normalisation is load-bearing, not tidiness: Cloud sends `Retry-After`
	and DC sends `retry-after`, and a case-sensitive lookup honours exactly one
	of them.
	"""

	def __init__(self, status: int, headers: Any = (), body: bytes = b""):
		self.status = int(status)
		pairs = headers.items() if hasattr(headers, "items") else headers
		self.headers = {str(k).lower(): str(v) for k, v in pairs}
		self.body = body or b""

	def header(self, name: str) -> Optional[str]:
		return self.headers.get(name.lower())

	@property
	def content_type(self) -> str:
		return (self.header("content-type") or "").split(";")[0].strip().lower()

	@property
	def text(self) -> str:
		return self.body.decode("utf-8", "replace")


Fetch = Callable[[str, str, Optional[bytes], Dict[str, str]], HttpResponse]


def urllib_fetch(method: str, url: str, body: Optional[bytes],
		headers: Dict[str, str],
		timeout: float = DEFAULT_TIMEOUT) -> HttpResponse:
	"""The real transport. An HTTP error status is a RESPONSE, not an exception.

	urllib raises on 4xx/5xx, which would hide the body and the headers that
	carry the whole diagnosis (X-AUSERNAME, X-Seraph-LoginReason, Retry-After),
	so HTTPError is unwrapped back into a plain response.
	"""
	request = urllib.request.Request(url, data=body, method=method)
	for name, value in headers.items():
		request.add_header(name, value)
	context = ssl.create_default_context()
	try:
		with urllib.request.urlopen(request, timeout=timeout,
				context=context) as response:
			return HttpResponse(response.status, response.headers.items(),
				response.read())
	except urllib.error.HTTPError as exc:
		try:
			payload = exc.read()
		except Exception:
			payload = b""
		pairs = exc.headers.items() if exc.headers else ()
		return HttpResponse(exc.code, pairs, payload)
	except urllib.error.URLError as exc:
		raise SetupError("cannot reach %s: %s" % (url, exc.reason))
	except OSError as exc:
		raise SetupError("cannot reach %s: %s" % (url, exc))


# ---------------------------------------------------------------------------
# multipart: the one request body in this file that is not JSON
# ---------------------------------------------------------------------------

def encode_multipart_file(field_name: str, filename: str, data: bytes,
		boundary: Optional[str] = None) -> Tuple[bytes, str]:
	"""Encode ONE file part; return (body, the Content-Type value to send).

	`boundary` is a parameter only so a test can pin the exact bytes -- a
	caller in production passes None and gets a fresh random one.
	"""
	if boundary is None:
		# 32 hex chars from the CSPRNG. The length is the whole defence: the
		# boundary must not occur inside the payload, and nothing here can
		# rewrite the payload if it does.
		boundary = secrets.token_hex(16)
	# A `"` would close the Content-Disposition parameter early and the rest of
	# the name would be parsed as header syntax, so it is replaced outright.
	safe_name = filename.replace('"', "_")
	if not safe_name:
		raise SetupError("refusing to upload with an empty filename")
	if boundary.encode("utf-8") in data:
		# The server stops reading at the first boundary it finds, so a
		# collision truncates the file and reports success -- with a random
		# 32-hex boundary this cannot realistically happen, which is exactly
		# why it is checked rather than assumed.
		raise SetupError("the multipart boundary %s occurs inside the file "
			"data, which would truncate the upload" % boundary)
	# CRLF everywhere, per RFC 2046: an LF-only multipart body is accepted by
	# some servers and silently rejected by the proxies in front of others.
	head = ("--%s\r\n"
		"Content-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
		"Content-Type: application/octet-stream\r\n"
		"\r\n" % (boundary, field_name, safe_name))
	tail = "\r\n--%s--\r\n" % boundary
	# `data` is spliced in as raw bytes and never decoded: an attachment is not
	# text, and a decode/encode round trip would corrupt every binary file.
	body = head.encode("utf-8") + data + tail.encode("utf-8")
	return body, "multipart/form-data; boundary=%s" % boundary


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

# Cached for the PROCESS lifetime and nowhere else. A disk cache would outlive
# a migration, and this answer decides which search endpoint gets called.
_DEPLOYMENT_CACHE = None		# type: Optional[str]


def reset_deployment_cache() -> None:
	"""Forget the probed deployment (a long-lived process, or a test)."""
	global _DEPLOYMENT_CACHE
	_DEPLOYMENT_CACHE = None


class Jira:
	"""Everything that speaks to the server. `fetch` is injectable for tests."""

	def __init__(self, cfg: Config, fetch: Optional[Fetch] = None,
			sleep: Optional[Callable[[float], None]] = None):
		self.cfg = cfg
		self._fetch = fetch or self._default_fetch
		self._sleep = sleep or time.sleep

	def _default_fetch(self, method: str, url: str, body: Optional[bytes],
			headers: Dict[str, str]) -> HttpResponse:
		return urllib_fetch(method, url, body, headers, self.cfg.timeout)

	# -- request plumbing ------------------------------------------------

	def _headers(self, has_body: bool) -> Dict[str, str]:
		headers = {
			"Accept": "application/json",
			"Authorization": self.cfg.auth_header,
			# A real User-Agent, because the WAFs in front of on-prem Jira
			# reject Python-urllib/3.x outright and the 403 says nothing.
			"User-Agent": USER_AGENT,
		}
		if has_body:
			headers["Content-Type"] = "application/json"
		return headers

	def request(self, method: str, path: str,
			query: Optional[Dict[str, str]] = None,
			body: Optional[Any] = None,
			subject: Optional[str] = None) -> Any:
		"""One request, retries included, decoded into Python or raised."""
		url = api_url(self.cfg.base_url, path, query)
		payload = None
		if body is not None:
			payload = json.dumps(body).encode("utf-8")
		headers = self._headers(payload is not None)
		return self._send(method, url, payload, headers, subject or path)

	def _send(self, method: str, url: str, payload: Optional[bytes],
			headers: Dict[str, str], subject: str) -> Any:
		"""The retry ladder and the decode, for a body of ANY content type.

		Split out of request() so the multipart upload shares this error
		handling instead of owning a second copy of it -- a copy is a place for
		the 429/5xx ladder to drift out of step.
		"""
		response = None
		for attempt in range(1, MAX_ATTEMPTS + 1):
			response = self._fetch(method, url, payload, headers)
			if not _is_retryable(response.status) or attempt == MAX_ATTEMPTS:
				break
			self._sleep(_retry_delay(response, attempt))
		return self._decode(response, method, url, subject)

	def _decode(self, response: HttpResponse, method: str, url: str,
			subject: str) -> Any:
		# 1. The anonymous fallthrough. Jira sends no WWW-Authenticate
		#    challenge: a request with bad or missing credentials often comes
		#    back 200 with LESS DATA rather than 401, so the header is the only
		#    reliable tell and it is checked on every response, whatever the
		#    status.
		who = (response.header("x-ausername") or "").strip().lower()
		if who == "anonymous":
			raise JiraError("credentials were not accepted (X-AUSERNAME: "
				"anonymous) — the server answered as an anonymous user")

		# 2. Content-Type BEFORE json.loads. Behind an SSO proxy the body is a
		#    login page, and json.loads would report a syntax error at
		#    character 0 instead of the redirect that actually happened.
		ctype = response.content_type
		if response.body.strip() and ctype and ctype != "application/json":
			snippet = " ".join(response.text[:200].split())
			raise JiraError("HTTP %d from %s %s, but the body is %s, not JSON\n"
				"  body: %s\n"
				"  hint: got %s — you may be behind an SSO proxy, or the "
				"base URL's context path is wrong"
				% (response.status, method, url, ctype, snippet, ctype))

		if response.status >= 400:
			raise self._error(response, method, url, subject)

		# 3. 204 No Content is the SUCCESS answer for a transition, and
		#    json.loads("") would explode on it.
		if response.status == 204 or not response.body.strip():
			return None
		try:
			return json.loads(response.text)
		except ValueError as exc:
			raise JiraError("HTTP %d from %s %s: the body claims to be JSON "
				"and is not (%s)" % (response.status, method, url, exc))

	def _error(self, response: HttpResponse, method: str, url: str,
			subject: str) -> JiraError:
		"""The standard Jira error envelope, plus the status-specific hints."""
		lines = ["HTTP %d on %s %s" % (response.status, method, url)]
		payload = None
		if response.body.strip():
			try:
				payload = json.loads(response.text)
			except ValueError:
				payload = None
		if isinstance(payload, dict):
			messages = payload.get("errorMessages") or []
			if isinstance(messages, list) and messages:
				lines.append("  " + "; ".join(str(m) for m in messages))
			errors = payload.get("errors")
			if isinstance(errors, dict):
				for key in sorted(errors):
					lines.append("  %s=%s" % (key, errors[key]))

		if response.status == 404:
			# Atlassian's own wording: a 404 means one of two things and the
			# server will not say which.
			lines.append("  404 — %s does not exist, or your account "
				"lacks Browse Projects / issue-level security permission for "
				"it" % subject)
		if response.status == 401:
			reason = (response.header("x-seraph-loginreason") or "").upper()
			if "AUTHENTICATION_DENIED" in reason:
				lines.append("  CAPTCHA triggered — log in via the web UI "
					"to clear it")
		if response.status == 429:
			lines.append("  rate limited after %d attempt(s); Retry-After: %s"
				% (MAX_ATTEMPTS, response.header("retry-after") or "absent"))
		return JiraError("\n".join(lines))

	# -- deployment ------------------------------------------------------

	def _deployment(self) -> str:
		"""Cloud or Server, probed ONCE per process and cached in memory."""
		global _DEPLOYMENT_CACHE
		if _DEPLOYMENT_CACHE is not None:
			return _DEPLOYMENT_CACHE
		resolved = None
		try:
			info = self.request("GET", API + "/serverInfo") or {}
			raw = str(info.get("deploymentType") or "").strip().lower()
			if raw == "cloud":
				resolved = CLOUD
			elif raw == "server":
				resolved = SERVER
		except (JiraError, SetupError):
			resolved = None
		if resolved is None:
			resolved = deployment_from_url(self.cfg.base_url)
		_DEPLOYMENT_CACHE = resolved
		return resolved

	# -- search: two paging models, one iterator -------------------------

	def search_issues(self, jql: str, fields: Optional[Sequence[str]] = None,
			limit: int = DEFAULT_LIMIT) -> Iterator[dict]:
		"""Issues matching `jql`, at most `limit` of them, across pages.

		No total is exposed on purpose: Cloud does not return one, DC calls it
		optional and may change it between pages, and a caller who could read
		one would come to rely on it.
		"""
		chosen = list(fields) if fields else list(DEFAULT_FIELDS)
		if limit <= 0:
			return
		if self._deployment() == CLOUD:
			for issue in self._search_cloud(jql, chosen, limit):
				yield issue
		else:
			for issue in self._search_server(jql, chosen, limit):
				yield issue

	def _search_cloud(self, jql: str, fields: List[str],
			limit: int) -> Iterator[dict]:
		"""Token paging: strictly sequential, no total, no startAt.

		The termination condition is the ABSENCE of `nextPageToken` (or
		`isLast`), not an exhausted count -- there is no count.
		"""
		seen = 0
		token = None
		while seen < limit:
			body = {"jql": jql, "fields": fields,
				"maxResults": min(PAGE_SIZE, limit - seen)}
			if token is not None:
				body["nextPageToken"] = token
			payload = self.request("POST", API + "/search/jql",
				body=body) or {}
			for issue in payload.get("issues") or []:
				yield issue
				seen += 1
				if seen >= limit:
					return
			if payload.get("isLast"):
				return
			if "nextPageToken" not in payload:
				return
			following = payload["nextPageToken"]
			if following == token:
				# An unchanged token would replay the same page forever.
				return
			token = following

	def _search_server(self, jql: str, fields: List[str],
			limit: int) -> Iterator[dict]:
		"""Offset paging, with BOTH documented stop conditions.

		`startAt + len(issues) >= total` is the nominal one, but DC documents
		that a requested page may legitimately come back empty and that `total`
		may move between pages -- so an empty page ends the walk too, and a
		missing total leaves the empty page as the only stop.
		"""
		seen = 0
		start = 0
		while seen < limit:
			body = {"jql": jql, "fields": fields, "startAt": start,
				"maxResults": min(PAGE_SIZE, limit - seen)}
			payload = self.request("POST", API + "/search", body=body) or {}
			issues = payload.get("issues") or []
			if not issues:
				return
			for issue in issues:
				yield issue
				seen += 1
				if seen >= limit:
					return
			page_start = payload.get("startAt", start)
			try:
				page_start = int(page_start)
			except (TypeError, ValueError):
				page_start = start
			following = page_start + len(issues)
			total = payload.get("total")
			if isinstance(total, int) and not isinstance(total, bool):
				if following >= total:
					return
			if following <= start:
				return
			start = following

	# -- projects: a flat array on DC, a paginated envelope on Cloud -----

	def list_projects(self) -> List[dict]:
		if self._deployment() != CLOUD:
			payload = self.request("GET", API + "/project") or []
			return list(payload) if isinstance(payload, list) else []
		out = []		# type: List[dict]
		start = 0
		while True:
			payload = self.request("GET", API + "/project/search",
				query={"startAt": str(start),
					"maxResults": str(PAGE_SIZE)}) or {}
			values = payload.get("values") or []
			out.extend(values)
			if payload.get("isLast") or not values:
				return out
			page_start = payload.get("startAt", start)
			try:
				page_start = int(page_start)
			except (TypeError, ValueError):
				page_start = start
			following = page_start + len(values)
			total = payload.get("total")
			if isinstance(total, int) and following >= total:
				return out
			if following <= start:
				return out
			start = following

	def transitions(self, key: str) -> List[dict]:
		payload = self.request("GET", API + "/issue/%s/transitions" % key,
			query={"expand": "transitions.fields"}, subject=key) or {}
		return list(payload.get("transitions") or [])

	# -- attachments: the one write whose body is not JSON ----------------

	def upload_attachment(self, key: str, filename: str, data: bytes) -> list:
		"""Upload one file. The answer is a JSON ARRAY, not an object."""
		url = api_url(self.cfg.base_url, API + "/issue/%s/attachments" % key)
		# The form field is named `file` because Jira looks for that name and
		# answers a request without it with an unhelpful 500.
		body, content_type = encode_multipart_file("file", filename, data)
		# Built from the SAME helper as every other call, so Accept,
		# Authorization and User-Agent cannot drift on this one path.
		headers = self._headers(has_body=False)
		headers["Content-Type"] = content_type
		# Jira's CSRF gate rejects every multipart/form-data request that
		# arrives without this header, on Cloud AND on Server/DC, and the
		# rejection does not mention the header.
		headers["X-Atlassian-Token"] = "no-check"
		result = self._send("POST", url, body, headers, key)
		return list(result) if isinstance(result, list) else []


def _is_retryable(status: int) -> bool:
	"""429 and 5xx only. Any other 4xx is about the request, not the moment."""
	return status == 429 or status >= 500


def _retry_delay(response: HttpResponse, attempt: int) -> float:
	"""Retry-After in seconds when the server names one, else backoff."""
	raw = response.header("retry-after")
	if raw:
		try:
			return max(0.0, float(raw.strip()))
		except ValueError:
			pass
	return BACKOFF_SECONDS * (2 ** (attempt - 1))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _text(value: Any, default: str = "-") -> str:
	"""A Jira field is a scalar, or an object with a human-readable key."""
	if value is None:
		return default
	if isinstance(value, dict):
		for key in ("displayName", "name", "value", "key"):
			candidate = value.get(key)
			if candidate:
				return str(candidate)
		return default
	text = str(value).strip()
	return text or default


def _flat(value: Any, default: str = "-") -> str:
	"""One line, whatever the source did with newlines."""
	return " ".join(_text(value, default).split()) or default


def issue_row(issue: dict) -> str:
	fields = issue.get("fields") or {}
	return "%-14s %-13.13s %-11.11s %-18.18s %s" % (
		_text(issue.get("key")),
		_text(fields.get("status")),
		_text(fields.get("issuetype")),
		_text(fields.get("assignee")),
		_flat(fields.get("summary")))


def emit_json(payload: Any) -> None:
	print(json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False))


def summary(line: str) -> None:
	"""The count goes to stderr so a pipeline gets rows and a human a total."""
	sys.stderr.write("--- %s ---\n" % line)


# ---------------------------------------------------------------------------
# read subcommands
# ---------------------------------------------------------------------------

def cmd_whoami(args: argparse.Namespace, client: Jira) -> int:
	"""The sanctioned liveness probe.

	It exists because Jira does not challenge a bad credential -- it answers
	anonymously -- so "did my token work" needs an endpoint whose answer is
	meaningless without one.
	"""
	me = client.request("GET", API + "/myself") or {}
	cfg = client.cfg
	deployment = client._deployment()
	account = _text(me.get("displayName") or me.get("name")
		or me.get("accountId"))
	if args.json:
		emit_json({
			"deployment": deployment,
			"base_url": cfg.base_url,
			"auth": cfg.auth_mode,
			"account": account,
			"account_id": me.get("accountId") or me.get("key")
				or me.get("name"),
			"email": me.get("emailAddress"),
			"active": me.get("active"),
			"read_only": cfg.read_only,
			"sources": dict(cfg.sources),
			"token": {"source": cfg.sources["token"], "length": len(cfg.token)},
		})
	else:
		rows = [
			("deployment", deployment),
			("base url", cfg.base_url),
			("auth", cfg.auth_mode),
			("account", account),
			("account id", _text(me.get("accountId") or me.get("key")
				or me.get("name"))),
			("email", _text(me.get("emailAddress"))),
			("read-only", "yes (%s)" % cfg.sources["read_only"]
				if cfg.read_only else "no"),
			("url source", cfg.sources["url"]),
			# The token VALUE is never printed, masked or otherwise: a masked
			# secret in a pasted terminal log is still a secret in a log.
			("token source", "%s (%d chars, value never printed)"
				% (cfg.sources["token"], len(cfg.token))),
			("email source", cfg.sources["email"]),
		]
		for label, value in rows:
			print("%-14s %s" % (label, value))
	summary("ok: %s on %s as %s" % (cfg.auth_mode.split(" ")[0], deployment,
		account))
	return OK


def _field_list(raw: Optional[str], extra: Sequence[str] = ()) -> List[str]:
	fields = [f.strip() for f in (raw or "").split(",") if f.strip()]
	if not fields:
		fields = list(DEFAULT_FIELDS)
	for name in extra:
		if name not in fields and "*all" not in fields:
			fields.append(name)
	return fields


def cmd_search(args: argparse.Namespace, client: Jira) -> int:
	fields = _field_list(args.fields)
	issues = client.search_issues(args.jql, fields, args.limit)
	count = 0
	if args.json:
		rows = list(issues)
		count = len(rows)
		emit_json({"jql": args.jql, "fields": fields, "limit": args.limit,
			"count": count, "issues": rows})
	else:
		for issue in issues:
			print(issue_row(issue))
			count += 1
	summary("%d issue(s) for: %s" % (count, _flat(args.jql)))
	if count == 0 and args.fail_empty:
		sys.stderr.write("%s: no issue matched and --fail-empty was given\n"
			% PROG)
		return FINDING
	return OK


def _render_comments(payload: Any) -> None:
	comments = (payload or {}).get("comments") or []
	print("")
	print("comments (%d)" % len(comments))
	for comment in comments:
		print("  %-12s %-24.24s %s" % (
			_text(comment.get("id")),
			_text(comment.get("author")),
			_text(comment.get("created"))))
		for line in str(comment.get("body") or "").splitlines() or [""]:
			print("      %s" % line)


def _render_changelog(payload: Any) -> None:
	histories = (payload or {}).get("histories") or []
	print("")
	print("changelog (%d)" % len(histories))
	for entry in histories:
		print("  %-24.24s %s" % (_text(entry.get("author")),
			_text(entry.get("created"))))
		for item in entry.get("items") or []:
			print("      %-18.18s %s -> %s" % (
				_text(item.get("field")),
				_flat(item.get("fromString") or item.get("from")),
				_flat(item.get("toString") or item.get("to"))))


def cmd_get(args: argparse.Namespace, client: Jira) -> int:
	_validate_issue_key(args.key)
	extra = ["comment"] if args.comments else []
	fields = _field_list(args.fields, extra)
	query = {"fields": ",".join(fields)}
	if args.changelog:
		query["expand"] = "changelog"
	issue = client.request("GET", API + "/issue/%s" % args.key, query=query,
		subject=args.key) or {}
	if args.json:
		emit_json(issue)
		summary("%s" % _text(issue.get("key"), args.key))
		return OK

	fields_payload = issue.get("fields") or {}
	rows = [
		("key", _text(issue.get("key"), args.key)),
		("summary", _flat(fields_payload.get("summary"))),
		("status", _text(fields_payload.get("status"))),
		("type", _text(fields_payload.get("issuetype"))),
		("priority", _text(fields_payload.get("priority"))),
		("assignee", _text(fields_payload.get("assignee"))),
		("reporter", _text(fields_payload.get("reporter"))),
		("updated", _text(fields_payload.get("updated"))),
	]
	for label, value in rows:
		print("%-12s %s" % (label, value))
	if args.comments:
		_render_comments(fields_payload.get("comment"))
	if args.changelog:
		_render_changelog(issue.get("changelog"))
	summary("%s" % _text(issue.get("key"), args.key))
	return OK


def _transition_target(transition: dict) -> str:
	return _text((transition.get("to") or {}).get("name"))


def cmd_transitions(args: argparse.Namespace, client: Jira) -> int:
	"""Always run this before `transition`.

	A transition is referenced by NUMERIC ID, and the available set depends on
	the issue's CURRENT status, so an id that worked yesterday can be gone
	today.
	"""
	_validate_issue_key(args.key)
	available = client.transitions(args.key)
	if args.json:
		emit_json({"key": args.key, "transitions": available})
	else:
		for transition in available:
			print("%-8s %-28.28s -> %s" % (
				_text(transition.get("id")),
				_text(transition.get("name")),
				_transition_target(transition)))
	summary("%d transition(s) for %s" % (len(available), args.key))
	return OK


def cmd_projects(args: argparse.Namespace, client: Jira) -> int:
	projects = client.list_projects()
	if args.json:
		emit_json({"count": len(projects), "projects": projects})
	else:
		for project in projects:
			print("%-14s %-10s %s" % (
				_text(project.get("key")),
				_text(project.get("id")),
				_flat(project.get("name"))))
	summary("%d project(s)" % len(projects))
	return OK


def cmd_fields(args: argparse.Namespace, client: Jira) -> int:
	"""How a human turns "Story Points" into customfield_10016."""
	payload = client.request("GET", API + "/field") or []
	fields = list(payload) if isinstance(payload, list) else []
	needle = (args.grep or "").strip().lower()
	if needle:
		fields = [f for f in fields
			if needle in str(f.get("name") or "").lower()]
	if args.json:
		emit_json({"count": len(fields), "grep": args.grep or None,
			"fields": fields})
	else:
		for field in fields:
			print("%-22s %-10s %s" % (
				_text(field.get("id")),
				"custom" if field.get("custom") else "system",
				_flat(field.get("name"))))
	summary("%d field(s)%s" % (len(fields),
		" matching %r" % args.grep if needle else ""))
	return OK


# ---------------------------------------------------------------------------
# write subcommands -- each one honours JIRA_READ_ONLY and --dry-run
# ---------------------------------------------------------------------------

def _dry_run(method: str, base_url: str, path: str, body: Any) -> int:
	"""Print exactly what WOULD be sent, send nothing, exit 0."""
	print("%s %s" % (method, api_url(base_url, path)))
	if body is not None:
		print(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False))
	summary("dry run: nothing was sent")
	return OK


def _body_text(raw: str) -> str:
	"""`-` means stdin, so a long body never has to survive shell quoting."""
	if raw == "-":
		return sys.stdin.read()
	return raw


def _read_upload(path: str) -> bytes:
	"""The whole file as bytes, or a SetupError -- exit 2, never a finding."""
	if not os.path.isfile(path):
		# isfile, not exists: a directory or a device node would otherwise fail
		# later inside open()/read() as an obscure OSError.
		raise SetupError("file not found or not a regular file: %s" % path)
	try:
		with open(path, "rb") as handle:
			data = handle.read()
	except OSError as exc:
		raise SetupError("cannot read %s: %s" % (path, exc))
	if not data:
		# Jira accepts a zero-byte attachment and lists it like any other, so
		# the mistake surfaces as a file nobody can open rather than an error.
		raise SetupError("refusing to upload an empty file: %s" % path)
	return data


def cmd_comment(args: argparse.Namespace, client: Jira) -> int:
	_validate_issue_key(args.key)
	text = _body_text(args.text)
	# A plain string body -- this is precisely why the whole file targets v2.
	body = {"body": text}
	path = API + "/issue/%s/comment" % args.key
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)
	created = client.request("POST", path, body=body, subject=args.key) or {}
	if args.json:
		emit_json(created)
	else:
		print("%-12s %-24.24s %s" % (_text(created.get("id")),
			_text(created.get("author")), _text(created.get("created"))))
	summary("comment %s added to %s" % (_text(created.get("id")), args.key))
	return OK


def _resolve_transition(client: Jira, key: str,
		wanted: str) -> Tuple[Optional[str], List[dict]]:
	"""(id, available). A numeric argument is taken as an id, unresolved.

	Name resolution is case-insensitive and EXACT: a substring match would pick
	"Close Issue" for "close" on a board that also has "Close as Duplicate",
	and a transition is not a thing to guess at.
	"""
	if wanted.isdigit():
		return wanted, []
	available = client.transitions(key)
	matches = [t for t in available
		if str(t.get("name") or "").strip().lower() == wanted.strip().lower()]
	if len(matches) == 1:
		return _text(matches[0].get("id")), available
	return None, available


def cmd_transition(args: argparse.Namespace, client: Jira) -> int:
	_validate_issue_key(args.key)
	transition_id, available = _resolve_transition(client, args.key, args.target)
	if transition_id is None:
		sys.stderr.write("%s: no single transition named %r on %s\n"
			% (PROG, args.target, args.key))
		for transition in available:
			print("%-8s %-28.28s -> %s" % (
				_text(transition.get("id")),
				_text(transition.get("name")),
				_transition_target(transition)))
		summary("%d transition(s) available on %s; the set depends on the "
			"current status" % (len(available), args.key))
		return FINDING

	body = {"transition": {"id": str(transition_id)}}
	if args.comment:
		body["update"] = {"comment": [{"add": {"body": args.comment}}]}
	path = API + "/issue/%s/transitions" % args.key
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)
	# 204 No Content is the SUCCESS answer here; request() returns None for it.
	client.request("POST", path, body=body, subject=args.key)
	if args.json:
		emit_json({"key": args.key, "transition": str(transition_id),
			"applied": True})
	else:
		print("%-14s transition %s applied" % (args.key, transition_id))
	summary("%s transitioned via %s" % (args.key, transition_id))
	return OK


def cmd_worklog(args: argparse.Namespace, client: Jira) -> int:
	_validate_issue_key(args.key)
	body = {"timeSpent": args.timespent}
	if args.comment:
		body["comment"] = args.comment
	if args.started:
		body["started"] = args.started
	path = API + "/issue/%s/worklog" % args.key
	if args.dry_run:
		return _dry_run("POST", client.cfg.base_url, path, body)
	logged = client.request("POST", path, body=body, subject=args.key) or {}
	if args.json:
		emit_json(logged)
	else:
		print("%-12s %-14s %s" % (_text(logged.get("id")),
			_text(logged.get("timeSpent"), args.timespent),
			_text(logged.get("started"))))
	summary("worklog %s added to %s" % (_text(logged.get("id")), args.key))
	return OK


def cmd_attach(args: argparse.Namespace, client: Jira) -> int:
	_validate_issue_key(args.key)
	# basename() runs even on an explicit --name, not only on the derived one:
	# `--name ../../evil.md` must reduce to `evil.md` rather than smuggle a
	# path into the name the server stores.
	upload_name = os.path.basename(args.name or os.path.basename(args.path))
	# Read BEFORE anything is sent, so a mistyped path costs no round trip and
	# fails as exit 2 instead of a server-side error.
	data = _read_upload(args.path)
	if args.dry_run:
		# _dry_run() is not reused here: it prints a JSON body, and printing
		# one for a multipart request would describe bytes that never go out.
		print("%s %s" % ("POST", api_url(client.cfg.base_url,
			API + "/issue/%s/attachments" % args.key)))
		# The NAME and the COUNT, never the content: a dry run of a binary
		# attachment would otherwise dump the file into the terminal.
		print("%-12s %s" % ("name", upload_name))
		print("%-12s %d" % ("bytes", len(data)))
		summary("dry run: nothing was sent")
		return OK
	created = client.upload_attachment(args.key, upload_name, data)
	if args.json:
		emit_json(created)
	else:
		for item in created:
			print("%-12s %-40.40s %s" % (
				_text(item.get("id")),
				_flat(item.get("filename"), upload_name),
				_text(item.get("size"))))
	summary("%s attached to %s as %s (%d attachment(s) created)"
		% (args.path, args.key, upload_name, len(created)))
	return OK


HANDLERS = {
	"whoami": cmd_whoami,
	"search": cmd_search,
	"get": cmd_get,
	"transitions": cmd_transitions,
	"projects": cmd_projects,
	"fields": cmd_fields,
	"comment": cmd_comment,
	"transition": cmd_transition,
	"worklog": cmd_worklog,
	"attach": cmd_attach,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
	common = argparse.ArgumentParser(add_help=False)
	common.add_argument("--url", help="Jira base URL (overrides JIRA_URL); a "
		"context path such as /jira is preserved")
	common.add_argument("--token", help="API token or PAT (overrides "
		"JIRA_TOKEN); never printed back")
	common.add_argument("--email", help="Atlassian account email (overrides "
		"JIRA_EMAIL); its PRESENCE selects Cloud Basic auth, its absence "
		"selects Server/DC Bearer")
	common.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
		help="HTTP timeout in seconds (default: %(default)s)")
	common.add_argument("--json", action="store_true",
		help="emit one JSON document on stdout instead of aligned text")

	dry = argparse.ArgumentParser(add_help=False)
	dry.add_argument("--dry-run", action="store_true",
		help="print the method, URL and body that WOULD be sent, then exit 0")

	parser = argparse.ArgumentParser(prog="jira.py",
		description="Read-mostly Jira CLI over REST API v2 (Cloud and "
			"Server/DC), stdlib only.")
	sub = parser.add_subparsers(dest="command", required=True)

	sub.add_parser("whoami", parents=[common],
		help="liveness probe: who the server thinks you are")

	search = sub.add_parser("search", parents=[common],
		help="run a JQL query (POST on both deployments)")
	search.add_argument("jql", help="the JQL query")
	search.add_argument("--fields", help="comma-separated field list "
		"(default: %s); pass '*all' to opt out" % ",".join(DEFAULT_FIELDS))
	search.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
		help="total issues across pages (default: %(default)s)")
	search.add_argument("--fail-empty", action="store_true",
		help="exit 1 when nothing matched")

	get = sub.add_parser("get", parents=[common], help="fetch one issue")
	get.add_argument("key", help="issue key, e.g. PROJ-1234")
	get.add_argument("--fields", help="comma-separated field list")
	get.add_argument("--comments", action="store_true",
		help="request and render the comment thread")
	get.add_argument("--changelog", action="store_true",
		help="expand=changelog and render the history")

	transitions = sub.add_parser("transitions", parents=[common],
		help="list the transitions available on an issue RIGHT NOW")
	transitions.add_argument("key", help="issue key, e.g. PROJ-1234")

	sub.add_parser("projects", parents=[common],
		help="list projects (flat on DC, paginated on Cloud)")

	fields = sub.add_parser("fields", parents=[common],
		help="list field ids, e.g. to map a name to customfield_NNNNN")
	fields.add_argument("--grep", help="filter by name substring, "
		"case-insensitive")

	comment = sub.add_parser("comment", parents=[common, dry],
		help="add a comment (v2: the body is a plain string)")
	comment.add_argument("key", help="issue key, e.g. PROJ-1234")
	comment.add_argument("text", help="comment body, or '-' to read stdin")

	transition = sub.add_parser("transition", parents=[common, dry],
		help="apply a transition by numeric id or exact name")
	transition.add_argument("key", help="issue key, e.g. PROJ-1234")
	transition.add_argument("target", help="transition id (e.g. 31) or its "
		"exact name (case-insensitive)")
	transition.add_argument("--comment", help="comment to add with the "
		"transition")

	worklog = sub.add_parser("worklog", parents=[common, dry],
		help="log time against an issue")
	worklog.add_argument("key", help="issue key, e.g. PROJ-1234")
	worklog.add_argument("timespent", help="Jira duration, e.g. '3h 20m'")
	worklog.add_argument("--comment", help="worklog comment")
	worklog.add_argument("--started", help="start time as "
		"%%Y-%%m-%%dT%%H:%%M:%%S.%%f%%z -- milliseconds and a +0000-style "
		"offset with NO colon, e.g. 2026-08-26T09:00:00.000+0000")

	attach = sub.add_parser("attach", parents=[common, dry],
		help="upload a local file as an attachment (multipart/form-data)")
	attach.add_argument("key", help="issue key, e.g. PROJ-1234")
	attach.add_argument("path", help="path to the local file to upload")
	attach.add_argument("--name", help="name to store the attachment under "
		"(default: the file's basename); any directory part is stripped")

	return parser


def main(argv: Optional[List[str]] = None) -> int:
	args = build_parser().parse_args(argv)
	try:
		cfg = resolve_config(args)
	except SystemExit as exc:
		return int(exc.code or 0)

	# The read-only refusal fires BEFORE anything is built or sent, so a
	# guarded run cannot even resolve a transition name.
	if args.command in WRITE_COMMANDS and cfg.read_only:
		sys.stderr.write("%s: JIRA_READ_ONLY=%s (%s) — refusing the write "
			"subcommand %r\n" % (PROG, cfg.read_only_raw,
				cfg.sources["read_only"], args.command))
		return USAGE

	client = Jira(cfg)
	try:
		return HANDLERS[args.command](args, client)
	except SetupError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, exc))
		return USAGE
	except JiraError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, exc))
		return FINDING
	except KeyboardInterrupt:
		sys.stderr.write("%s: interrupted\n" % PROG)
		return USAGE


if __name__ == "__main__":
	sys.exit(main())
