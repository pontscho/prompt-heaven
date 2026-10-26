#!/usr/bin/env python3
"""Create and wire Jenkins multibranch jobs from the terminal.

Subcommands: whoami, get-config, clone, set-config, scan.  Stdlib only, Python 3.9,
no third-party HTTP client.  Job creation is rare, so this is a documented skill and
a script, not functions in the mcp-jenkins server.

DRY-RUN IS THE DEFAULT FOR EVERY WRITE.  clone, set-config and scan print the exact
method, URL and a summary of what would change and exit 0.  They act only with
`--apply`.  Reads need no flag.

NOTHING IS EVER DELETED.  If `clone` creates a job and then cannot patch or post its
configuration, the half-created job is reported by name and the script stops; removing
it is the user's decision.

SECRETS.  Credentials come from the environment only (no token flag).  `get-config`
masks every token, password, key and Jenkins-encrypted `{AQAA...}` value and has no
flag that turns the masking off.  `clone` generates the new webhook token with
`secrets`, never reuses the source job's token, prints it ONCE together with the invoke
URL the Bitbucket webhook needs, and writes it nowhere else.

Usage:
	jenkins_job.py whoami [--folder <FOLDER>]
	jenkins_job.py get-config <FOLDER>/<TEMPLATE>
	jenkins_job.py clone <FOLDER>/<TEMPLATE> <NAME> \\
		--folder <FOLDER> --remote ssh://git@<HOST>:<PORT>/<PROJECT>/<REPO>.git \\
		--generate-webhook-token <NAME>            # dry run
	jenkins_job.py clone ... --apply
	jenkins_job.py set-config <FOLDER>/<NAME> --file config.xml [--apply]
	jenkins_job.py scan <FOLDER>/<NAME> [--apply]

Environment variables:
	JENKINS_ENDPOINT   base URL, https only (JENKINS_URL is read as a fallback spelling)
	JENKINS_USERNAME   user name for HTTP Basic auth
	JENKINS_TOKEN      API token for HTTP Basic auth (never a flag value)
	JENKINS_JOB_ALLOW_INSECURE_LOOPBACK
	                   TEST ONLY.  When set to 1, an http:// endpoint is accepted if
	                   and only if its host is 127.0.0.1 or ::1.

Exit codes:
	0  the command did what it said (including a dry run)
	1  the server answered and the answer is bad news
	2  bad invocation, bad configuration, an unreachable host or a refused write
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from typing import Any, Dict, List, Optional, Sequence, Tuple

PROG = "jenkins_job.py"
USER_AGENT = "jenkins_job.py (stdlib urllib)"

OK = 0
FINDING = 1
USAGE = 2

DEFAULT_TIMEOUT = 30.0
# A response bigger than this is refused rather than read into memory.  A Jenkins
# job config is a few KiB; four MiB is orders of magnitude of headroom.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# Same cap for an XML file given with --file or --trait-xml.
MAX_INPUT_BYTES = 1024 * 1024

LOOPBACK_ENV = "JENKINS_JOB_ALLOW_INSECURE_LOOPBACK"
LOOPBACK_HOSTS = ("127.0.0.1", "::1")

# A name the new job may carry.  Deliberately narrower than what Jenkins allows: it
# reaches a URL path and a query string, so nothing structural may be inside it.
RX_NEW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RX_TOKEN_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# URL-safe and long enough not to be guessed; nothing in it needs quoting.
RX_WEBHOOK_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")

GIT_SOURCE_CLASS = "jenkins.plugins.git.GitSCMSource"
WEBHOOK_TRIGGER_SUFFIX = "ComputedFolderWebHookTrigger"
WEBHOOK_INVOKE_PATH = "/multibranch-webhook-trigger/invoke"

REDACTED = "[REDACTED]"
# An element or attribute whose name contains one of these has its value masked.
RX_SENSITIVE_NAME = re.compile(
	r"(token|password|passwd|passphrase|private_?key|secret|api_?key)", re.I)
# Jenkins' own encrypted-value envelope.
RX_ENCRYPTED = re.compile(r"\{AQAA[A-Za-z0-9+/=\s]*\}")
# Only `user:password@` is masked.  `ssh://git@host` carries a user name, not a secret.
RX_URL_USERINFO = re.compile(r"(://)[^/@\s:]*:[^/@\s]*@")
RX_XML_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>")
RX_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


class JobError(Exception):
	"""A finding -- the server answered, and the answer is bad news (exit 1)."""


class SetupError(Exception):
	"""Bad invocation, configuration, host or a refused write (exit 2)."""


class HalfCreated(JobError):
	"""The job exists on the server but was not fully configured (exit 1)."""

	def __init__(self, inner: Exception) -> None:
		Exception.__init__(self, str(inner))


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def clean(text: Any, limit: int = 200) -> str:
	"""Untrusted text made safe for one terminal line: no control bytes, bounded."""
	text = RX_CONTROL.sub(" ", str(text)).strip()
	if len(text) > limit:
		text = text[:limit] + "..."
	return text


def redact_url(text: str) -> str:
	"""Drop `user:password@` from anything URL-shaped before it is printed."""
	return RX_URL_USERINFO.sub(r"\1***@", text)


def mask_text(text: str) -> str:
	return RX_ENCRYPTED.sub("{" + REDACTED + "}", text)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

class Config(object):
	def __init__(self, endpoint: str, username: str, token: str,
			timeout: float) -> None:
		self.endpoint = endpoint
		self.username = username
		self.token = token
		self.timeout = timeout

	def __repr__(self) -> str:
		return "Config(endpoint=%r, username=%r, token=<redacted>)" % (
			self.endpoint, self.username)


def _env(*names: str) -> Optional[str]:
	for name in names:
		value = os.environ.get(name)
		if value:
			return value
	return None


def resolve_config(timeout: float) -> Config:
	endpoint = _env("JENKINS_ENDPOINT", "JENKINS_URL")
	username = _env("JENKINS_USERNAME")
	token = _env("JENKINS_TOKEN")
	missing = []						# type: List[str]
	if not endpoint:
		missing.append("JENKINS_ENDPOINT")
	if not username:
		missing.append("JENKINS_USERNAME")
	if not token:
		missing.append("JENKINS_TOKEN")
	if missing:
		raise SetupError("missing required configuration: %s" % ", ".join(missing))
	assert endpoint and username and token

	endpoint = endpoint.rstrip("/")
	parsed = urllib.parse.urlparse(endpoint)
	if parsed.username or parsed.password:
		raise SetupError(
			"refusing an endpoint that carries userinfo (user:password@) — put the "
			"credentials in JENKINS_USERNAME / JENKINS_TOKEN")
	host = (parsed.hostname or "").lower()
	if parsed.scheme == "https":
		pass
	elif (parsed.scheme == "http" and host in LOOPBACK_HOSTS
			and os.environ.get(LOOPBACK_ENV) == "1"):
		pass
	else:
		raise SetupError(
			"refusing an endpoint whose scheme is %r — the API token is sent as "
			"HTTP Basic auth on every request, so anything but https:// would put "
			"it on the wire in cleartext" % parsed.scheme)
	if not host:
		raise SetupError("the endpoint has no host")
	if ":" in username:
		raise SetupError("JENKINS_USERNAME must not contain a colon")
	return Config(endpoint, username, token, timeout)


# ---------------------------------------------------------------------------
# job paths
# ---------------------------------------------------------------------------

def split_path(raw: str, what: str = "job path") -> List[str]:
	"""`a/b/c` -> [a, b, c].  Empty segments, `.` and `..` are refused."""
	if not isinstance(raw, str) or not raw:
		raise SetupError("empty %s" % what)
	segments = raw.split("/")
	for seg in segments:
		if seg in ("", ".", ".."):
			raise SetupError(
				"refusing %s %r: empty, '.' and '..' segments are not allowed"
				% (what, clean(raw, 80)))
		if RX_CONTROL.search(seg) or "\\" in seg:
			raise SetupError("refusing %s with a control or backslash character" % what)
	return segments


def job_url(base: str, segments: Sequence[str]) -> str:
	"""folder/job -> <base>/job/folder/job/job, each segment quoted whole."""
	return base + "".join("/job/" + urllib.parse.quote(s, safe="") for s in segments)


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

class Response(object):
	def __init__(self, status: int, headers: Dict[str, str], body: bytes) -> None:
		self.status = status
		self.headers = {k.lower(): v for k, v in headers.items()}
		self.body = body

	def text(self) -> str:
		return self.body.decode("utf-8", "replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
	"""Follow nothing.  A 3xx comes back as an answer and the caller decides.

	urllib would carry the Authorization header to whatever host a 302 names; this
	API has no redirect worth following, and createItem's own 302 only points at the
	page of the job it just made.
	"""

	def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
			headers: Any, newurl: str) -> None:
		return None


def _origin(url: str) -> Tuple[str, str, Any]:
	parsed = urllib.parse.urlparse(url)
	scheme = parsed.scheme.lower()
	try:
		port = parsed.port
	except ValueError:
		port = "?"
	if port is None:
		port = {"https": 443, "http": 80}.get(scheme)
	return (scheme, (parsed.hostname or "").lower(), port)


class Jenkins(object):
	def __init__(self, cfg: Config) -> None:
		self.cfg = cfg
		self.jar = http.cookiejar.CookieJar()
		handlers = [
			urllib.request.HTTPCookieProcessor(self.jar),
			urllib.request.HTTPSHandler(context=ssl.create_default_context()),
			NoRedirect(),
		]							# type: List[Any]
		host = (urllib.parse.urlparse(cfg.endpoint).hostname or "").lower()
		if host in LOOPBACK_HOSTS:
			handlers.append(urllib.request.ProxyHandler({}))
		self.opener = urllib.request.build_opener(*handlers)
		raw = ("%s:%s" % (cfg.username, cfg.token)).encode("utf-8")
		self.auth = "Basic " + base64.b64encode(raw).decode("ascii")
		self._crumb = None			# type: Optional[Tuple[str, str]]
		self._crumb_done = False

	def request(self, method: str, url: str, body: Optional[bytes] = None,
			content_type: Optional[str] = None, crumb: bool = False,
			lenient_size: bool = False) -> Response:
		if _origin(url)[:2] != _origin(self.cfg.endpoint)[:2]:
			raise SetupError("refusing to send a request off the Jenkins origin")
		headers = {"Authorization": self.auth, "User-Agent": USER_AGENT}
		if content_type:
			headers["Content-Type"] = content_type
		if crumb:
			pair = self.crumb()
			if pair:
				headers[pair[0]] = pair[1]
		req = urllib.request.Request(url=url, data=body, method=method,
			headers=headers)
		try:
			with self.opener.open(req, timeout=self.cfg.timeout) as resp:
				return Response(resp.status, dict(resp.headers),
					self._read(resp, lenient_size))
		except urllib.error.HTTPError as exc:
			return Response(exc.code, dict(exc.headers or {}),
				self._read(exc, True))
		except urllib.error.URLError as exc:
			raise SetupError("cannot reach %s: %s" % (
				redact_url(self.cfg.endpoint), clean(exc.reason, 120)))
		except (OSError, ValueError) as exc:
			raise SetupError("cannot reach %s: %s" % (
				redact_url(self.cfg.endpoint), clean(exc, 120)))

	@staticmethod
	def _read(fp: Any, lenient: bool) -> bytes:
		data = fp.read(MAX_RESPONSE_BYTES + 1)
		if len(data) > MAX_RESPONSE_BYTES:
			if lenient:
				return data[:MAX_RESPONSE_BYTES]
			raise JobError(
				"the response is larger than %d bytes — refusing to read it"
				% MAX_RESPONSE_BYTES)
		return data

	def crumb(self) -> Optional[Tuple[str, str]]:
		"""The CSRF crumb, fetched once on the same cookie session.  None when the
		crumb issuer is disabled (404), which Jenkins allows."""
		if self._crumb_done:
			return self._crumb
		self._crumb_done = True
		resp = self.request("GET", self.cfg.endpoint + "/crumbIssuer/api/json")
		if resp.status == 404:
			return None
		if resp.status in (401, 403):
			raise JobError(describe_status(resp, "crumb issuer"))
		if resp.status != 200:
			raise JobError(describe_status(resp, "crumb issuer"))
		try:
			data = json.loads(resp.text())
			field, value = data["crumbRequestField"], data["crumb"]
		except (ValueError, KeyError, TypeError):
			raise JobError("the crumb issuer answered with something that is not a crumb")
		if not (isinstance(field, str) and isinstance(value, str)
				and field and value):
			raise JobError("the crumb issuer answered with something that is not a crumb")
		self._crumb = (field, value)
		return self._crumb


def describe_status(resp: Response, subject: str) -> str:
	"""One line for a bad answer; the body is untrusted and only hinted at."""
	hint = {
		401: "authentication failed — check JENKINS_USERNAME / JENKINS_TOKEN",
		403: "forbidden — the user lacks the permission this needs",
		404: "not found (or not visible to this user)",
	}.get(resp.status, "")
	line = "%s: HTTP %d" % (subject, resp.status)
	if hint:
		line += " " + hint
	elif 300 <= resp.status < 400:
		location = resp.headers.get("location", "")
		host = urllib.parse.urlparse(location).hostname or "(same origin)"
		line += " redirect to %s (not followed)" % clean(host, 80)
	else:
		first = clean(resp.text().strip().splitlines()[0] if resp.text().strip()
			else "", 120)
		if first and not first.startswith("<"):
			line += " " + first
	return line


def expect_ok(resp: Response, subject: str, accept_redirect_to: Optional[str] = None) -> None:
	"""2xx is success.  A 3xx is success only when it points back into the same
	origin at a page that is not a login page (what createItem and build answer)."""
	if 200 <= resp.status < 300:
		return
	if 300 <= resp.status < 400 and accept_redirect_to:
		location = resp.headers.get("location", "")
		same = _origin(location)[:2] == _origin(accept_redirect_to)[:2]
		if same and "/login" not in location and "/error" not in location:
			return
	raise JobError(describe_status(resp, subject))


# ---------------------------------------------------------------------------
# XML: validation, masking, patching
# ---------------------------------------------------------------------------

def parse_xml(data: bytes, what: str) -> ET.Element:
	"""Parse untrusted XML.  DOCTYPE and ENTITY are refused before the parser sees
	them: entity expansion is the one way a hostile document costs more than its
	size, and a Jenkins config never needs either."""
	if len(data) > MAX_INPUT_BYTES * 4:
		raise JobError("%s is too large to handle" % what)
	head = data.lower()
	if b"<!doctype" in head or b"<!entity" in head:
		raise SetupError("%s contains a DOCTYPE or ENTITY declaration — refused" % what)
	try:
		return ET.fromstring(data)
	except ET.ParseError as exc:
		raise SetupError("%s is not well-formed XML: %s" % (what, clean(exc, 120)))


def serialize(root: ET.Element, original: Optional[bytes] = None) -> bytes:
	decl = b"<?xml version='1.0' encoding='UTF-8'?>"
	if original is not None:
		match = RX_XML_DECL.match(original)
		if match:
			decl = match.group(0).strip()
	return decl + b"\n" + ET.tostring(root, encoding="utf-8")


def mask_config(data: bytes) -> str:
	"""The config as text with every secret-looking value replaced.  If it will not
	parse the answer is a refusal: an unparseable body cannot be shown masked."""
	root = parse_xml(data, "the configuration")
	for el in root.iter():
		name = el.tag.split(".")[-1] if isinstance(el.tag, str) else ""
		if RX_SENSITIVE_NAME.search(name):
			for node in el.iter():
				if (node.text or "").strip():
					node.text = REDACTED
		for key in list(el.attrib):
			if RX_SENSITIVE_NAME.search(key):
				el.attrib[key] = REDACTED
	for el in root.iter():
		if el.text:
			el.text = mask_text(el.text)
		for key in list(el.attrib):
			el.attrib[key] = mask_text(el.attrib[key])
	return ET.tostring(root, encoding="unicode")


def _child(parent: ET.Element, tag: str, create: bool) -> ET.Element:
	node = parent.find(tag)
	if node is None:
		if not create:
			raise SetupError("the configuration has no <%s> element" % tag)
		node = ET.SubElement(parent, tag)
	return node


def find_git_source(root: ET.Element) -> ET.Element:
	for node in root.iter("source"):
		if node.get("class") == GIT_SOURCE_CLASS:
			return node
	raise SetupError(
		"the configuration has no <source class=\"%s\"> element — this tool only "
		"patches a plain Git branch source" % GIT_SOURCE_CLASS)


def find_webhook_trigger(root: ET.Element) -> ET.Element:
	triggers = root.find("triggers")
	if triggers is not None:
		for node in triggers:
			if isinstance(node.tag, str) and node.tag.endswith(WEBHOOK_TRIGGER_SUFFIX):
				return node
	raise SetupError(
		"the configuration has no ...%s trigger — refusing to clone a job whose "
		"webhook token cannot be replaced" % WEBHOOK_TRIGGER_SUFFIX)


class PatchSpec(object):
	def __init__(self) -> None:
		self.remote = None			# type: Optional[str]
		self.credentials_id = None	# type: Optional[str]
		self.script_path = None		# type: Optional[str]
		self.display_name = None	# type: Optional[str]
		self.description = None		# type: Optional[str]
		self.cron_spec = None		# type: Optional[str]
		self.token = None			# type: Optional[str]
		self.traits = []			# type: List[Tuple[str, bytes]]


def apply_patch(data: bytes, spec: PatchSpec) -> Tuple[bytes, List[str], str]:
	"""Patch a config.xml.  Returns (new bytes, change lines, token the config held
	BEFORE the patch).  Containers (the Git source, the factory, the trigger) must
	exist or the patch refuses; a missing leaf inside one is created."""
	root = parse_xml(data, "the job configuration")
	changes = []					# type: List[str]

	trigger = find_webhook_trigger(root)
	token_el = _child(trigger, "token", True)
	old_token = (token_el.text or "").strip()
	if spec.token is not None:
		if spec.token == old_token:
			raise SetupError("the new webhook token equals the source job's token")
		token_el.text = spec.token
		changes.append("webhook token: replaced with a new secret (not shown here)")
	if spec.cron_spec is not None:
		_child(trigger, "spec", True).text = spec.cron_spec
		changes.append("trigger spec: %s" % clean(spec.cron_spec, 80))

	if spec.remote is not None or spec.credentials_id is not None or spec.traits:
		git = find_git_source(root)
		if spec.remote is not None:
			el = _child(git, "remote", False)
			changes.append("remote: %s -> %s" % (
				redact_url(clean(el.text or "", 160)), redact_url(clean(spec.remote, 160))))
			el.text = spec.remote
		if spec.credentials_id is not None:
			el = _child(git, "credentialsId", True)
			changes.append("credentialsId: %s -> %s" % (
				clean(el.text or "", 80), clean(spec.credentials_id, 80)))
			el.text = spec.credentials_id
		for label, blob in spec.traits:
			traits = _child(git, "traits", True)
			traits.append(parse_xml(blob, "trait snippet %s" % label))
			changes.append("trait appended: %s" % clean(label, 80))

	if spec.script_path is not None:
		factory = _child(root, "factory", False)
		el = _child(factory, "scriptPath", True)
		changes.append("scriptPath: %s -> %s" % (
			clean(el.text or "", 80), clean(spec.script_path, 80)))
		el.text = spec.script_path
	if spec.display_name is not None:
		_child(root, "displayName", True).text = spec.display_name
		changes.append("displayName: %s" % clean(spec.display_name, 80))
	if spec.description is not None:
		_child(root, "description", True).text = spec.description
		changes.append("description: %s" % clean(spec.description, 80))
	return serialize(root, data), changes, old_token


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def _get_config(client: Jenkins, segments: Sequence[str]) -> bytes:
	url = job_url(client.cfg.endpoint, segments) + "/config.xml"
	resp = client.request("GET", url)
	if resp.status != 200:
		raise JobError(describe_status(resp, "GET %s/config.xml" % "/".join(segments)))
	return resp.body


def cmd_whoami(args: argparse.Namespace, client: Jenkins) -> int:
	resp = client.request("GET", client.cfg.endpoint + "/me/api/json")
	if resp.status != 200:
		raise JobError(describe_status(resp, "GET /me/api/json"))
	try:
		data = json.loads(resp.text())
		ident, full = str(data.get("id", "")), str(data.get("fullName", ""))
	except (ValueError, AttributeError):
		raise JobError("/me/api/json did not answer with a JSON object")
	print("user id:   %s" % clean(ident))
	print("full name: %s" % clean(full))
	print("endpoint:  %s" % client.cfg.endpoint)
	if ident == "anonymous":
		print("note: Jenkins sees this request as anonymous — the credentials were not accepted")
	if args.folder:
		segments = split_path(args.folder, "folder path")
		url = job_url(client.cfg.endpoint, segments) + "/newJob"
		probe = client.request("GET", url, lenient_size=True)
		if probe.status == 200:
			verdict = "yes (HTTP 200) — the user can create items there"
		elif probe.status == 403:
			verdict = "no (HTTP 403) — the user lacks Job/Create on that folder"
		elif probe.status == 404:
			verdict = "unknown (HTTP 404) — no such folder, or not visible"
		else:
			verdict = "unknown (HTTP %d)" % probe.status
		print("can create in %s: %s" % (clean(args.folder, 100), verdict))
	return OK


def cmd_get_config(args: argparse.Namespace, client: Jenkins) -> int:
	segments = split_path(args.job)
	data = _get_config(client, segments)
	print(mask_config(data))
	sys.stderr.write("--- secrets are masked; there is no flag to show them ---\n")
	return OK


def _dry_banner() -> None:
	print("DRY RUN — nothing is sent.  Re-run with --apply to act.")


def cmd_clone(args: argparse.Namespace, client: Jenkins) -> int:
	source = split_path(args.source)
	folder = split_path(args.folder, "folder path")
	name = args.name
	if not RX_NEW_NAME.match(name or ""):
		raise SetupError(
			"refusing new job name %r — letters, digits, '.', '_' and '-' only, "
			"starting with a letter or digit" % clean(name, 60))

	spec = PatchSpec()
	spec.remote = args.remote
	spec.credentials_id = args.credentials_id
	spec.script_path = args.script_path
	spec.display_name = args.display_name
	spec.description = args.description
	spec.cron_spec = args.cron_spec
	for path in args.trait_xml or []:
		spec.traits.append((os.path.basename(path), read_input_file(path)))

	# The token: always replaced.  Explicit, or generated; never the source's.
	explicit = args.webhook_token
	prefix = args.generate_webhook_token or name
	if explicit is not None:
		if not RX_WEBHOOK_TOKEN.match(explicit):
			raise SetupError(
				"refusing --webhook-token: 16-128 characters from A-Z a-z 0-9 . _ ~ -")
	else:
		if not RX_TOKEN_PREFIX.match(prefix):
			raise SetupError("refusing token prefix %r" % clean(prefix, 40))

	endpoint = client.cfg.endpoint
	folder_url = job_url(endpoint, folder)
	new_url = job_url(endpoint, folder + [name])
	from_param = "/" + "/".join(source)

	# Reads, always: the source must exist, the patch must apply to it, the name
	# must be free.  All of it happens BEFORE anything is created.
	source_config = _get_config(client, source)
	preview_spec = PatchSpec()
	preview_spec.__dict__.update(spec.__dict__)
	preview_spec.token = "preview-token-not-real-0000"
	_, preview_changes, source_token = apply_patch(source_config, preview_spec)
	if explicit is not None and explicit == source_token:
		raise SetupError("--webhook-token equals the source job's token — it must differ")

	exists = client.request("GET", new_url + "/api/json?tree=name", lenient_size=True)
	if exists.status == 200:
		raise SetupError(
			"a job named %r already exists in %s — refusing to clone over it"
			% (name, "/".join(folder)))
	if exists.status not in (404,):
		raise JobError(describe_status(exists, "existence check for %s" % name))

	create_url = "%s/createItem?%s" % (folder_url, urllib.parse.urlencode(
		[("name", name), ("mode", "copy"), ("from", from_param)]))
	config_url = new_url + "/config.xml"

	if not args.apply:
		_dry_banner()
		print("1. POST %s" % create_url)
		print("2. GET  %s" % config_url)
		print("3. POST %s   (the patched configuration)" % config_url)
		print("changes to apply to the copy:")
		for line in preview_changes:
			if line.startswith("webhook token"):
				continue
			print("  - %s" % line)
		how = "the value given with --webhook-token" if explicit is not None \
			else "generated with `secrets`, prefix %r" % prefix
		print("  - webhook token: a new one, %s; printed once with --apply" % how)
		return OK

	# --- apply -----------------------------------------------------------
	resp = client.request("POST", create_url, body=b"", crumb=True)
	expect_ok(resp, "createItem %s" % name, accept_redirect_to=new_url)

	def half_created(reason: str) -> JobError:
		return JobError(
			"the job %r WAS created (%s) but %s — nothing was deleted; remove or fix "
			"it yourself" % (name, new_url, reason))

	try:
		copied = client.request("GET", config_url)
		if copied.status != 200:
			raise HalfCreated(half_created(
				describe_status(copied, "reading its config.xml")))
		spec.token = explicit if explicit is not None else "%s-%s" % (
			prefix, secrets.token_urlsafe(24))
		try:
			patched, _changes, _old = apply_patch(copied.body, spec)
		except SetupError as exc:
			raise HalfCreated(half_created(
				"its configuration could not be patched: %s" % clean(exc, 160)))
		posted = client.request("POST", config_url, body=patched,
			content_type="application/xml; charset=utf-8", crumb=True)
		if not (200 <= posted.status < 300):
			raise HalfCreated(half_created(
				describe_status(posted, "posting the patched config.xml")))
	except HalfCreated:
		raise
	except (SetupError, JobError) as exc:
		raise HalfCreated(half_created(clean(exc, 160)))

	print("created %s" % new_url)
	for line in preview_changes:
		if not line.startswith("webhook token"):
			print("  - %s" % line)
	token = spec.token
	print("webhook token (shown ONCE, stored nowhere else): %s" % token)
	print("Bitbucket webhook URL: %s%s?token=%s" % (
		endpoint, WEBHOOK_INVOKE_PATH, urllib.parse.quote(token, safe="")))
	sys.stderr.write("--- created %s; register the webhook URL on Bitbucket ---\n" % name)
	return OK


def read_input_file(path: str) -> bytes:
	try:
		with open(path, "rb") as fp:
			data = fp.read(MAX_INPUT_BYTES + 1)
	except OSError as exc:
		raise SetupError("cannot read %s: %s" % (clean(path, 80), clean(exc.strerror or exc, 80)))
	if len(data) > MAX_INPUT_BYTES:
		raise SetupError("%s is larger than %d bytes" % (clean(path, 80), MAX_INPUT_BYTES))
	return data


def cmd_set_config(args: argparse.Namespace, client: Jenkins) -> int:
	segments = split_path(args.job)
	data = read_input_file(args.file)
	root = parse_xml(data, "the file")
	url = job_url(client.cfg.endpoint, segments) + "/config.xml"
	if not args.apply:
		_dry_banner()
		print("POST %s" % url)
		print("body: %d bytes, root element <%s>, sha256 %s" % (
			len(data), clean(root.tag, 80), hashlib.sha256(data).hexdigest()[:16]))
		print("this REPLACES the whole configuration of %s" % "/".join(segments))
		return OK
	resp = client.request("POST", url, body=data,
		content_type="application/xml; charset=utf-8", crumb=True)
	if not (200 <= resp.status < 300):
		raise JobError(describe_status(resp, "POST config.xml"))
	print("configuration of %s replaced (HTTP %d)" % ("/".join(segments), resp.status))
	return OK


def cmd_scan(args: argparse.Namespace, client: Jenkins) -> int:
	segments = split_path(args.job)
	base = job_url(client.cfg.endpoint, segments)
	url = base + "/build?delay=0"
	if not args.apply:
		_dry_banner()
		print("POST %s" % url)
		print("starts branch indexing of %s" % "/".join(segments))
		return OK
	resp = client.request("POST", url, body=b"", crumb=True)
	expect_ok(resp, "branch indexing of %s" % "/".join(segments), accept_redirect_to=base)
	print("branch indexing requested for %s (HTTP %d)" % ("/".join(segments), resp.status))
	return OK


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

class OneLineParser(argparse.ArgumentParser):
	def error(self, message: str) -> None:  # type: ignore[override]
		sys.stderr.write("%s: %s\n" % (PROG, clean(message, 300)))
		sys.exit(USAGE)


HANDLERS = {
	"whoami": cmd_whoami,
	"get-config": cmd_get_config,
	"clone": cmd_clone,
	"set-config": cmd_set_config,
	"scan": cmd_scan,
}


def build_parser() -> argparse.ArgumentParser:
	parser = OneLineParser(prog=PROG, description=__doc__.split("\n")[0])
	parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
		help="request timeout in seconds (default %(default)s)")
	sub = parser.add_subparsers(dest="command", parser_class=OneLineParser)
	sub.required = True

	p = sub.add_parser("whoami", help="who the token is; with --folder, can it create there")
	p.add_argument("--folder", help="folder path, e.g. <FOLDER>")

	p = sub.add_parser("get-config", help="print a job's config.xml with secrets masked")
	p.add_argument("job", help="job path, e.g. <FOLDER>/<TEMPLATE>")

	p = sub.add_parser("clone", help="copy a job, patch the copy, print its webhook URL")
	p.add_argument("source", help="source job path")
	p.add_argument("name", help="new job name")
	p.add_argument("--folder", required=True, help="folder the new job is created in")
	p.add_argument("--remote", help="new Git remote URL")
	p.add_argument("--credentials-id", dest="credentials_id")
	p.add_argument("--script-path", dest="script_path", help="Jenkinsfile path")
	p.add_argument("--display-name", dest="display_name")
	p.add_argument("--description")
	group = p.add_mutually_exclusive_group()
	group.add_argument("--webhook-token", dest="webhook_token",
		help="use this webhook token instead of generating one")
	group.add_argument("--generate-webhook-token", dest="generate_webhook_token",
		metavar="PREFIX", help="generate PREFIX-<random> (default prefix: the new name)")
	p.add_argument("--trait-xml", dest="trait_xml", action="append", metavar="FILE",
		help="XML snippet appended to the Git source's <traits>; repeatable")
	p.add_argument("--cron-spec", dest="cron_spec", help="spec of the webhook trigger")
	p.add_argument("--apply", action="store_true", help="act instead of a dry run")

	p = sub.add_parser("set-config", help="replace a job's config.xml from a file")
	p.add_argument("job")
	p.add_argument("--file", required=True, help="XML file")
	p.add_argument("--apply", action="store_true", help="act instead of a dry run")

	p = sub.add_parser("scan", help="trigger multibranch branch indexing")
	p.add_argument("job")
	p.add_argument("--apply", action="store_true", help="act instead of a dry run")
	return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
	try:
		args = build_parser().parse_args(argv)
		cfg = resolve_config(args.timeout)
		return HANDLERS[args.command](args, Jenkins(cfg))
	except SetupError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, clean(exc, 400)))
		return USAGE
	except JobError as exc:
		sys.stderr.write("%s: %s\n" % (PROG, clean(exc, 400)))
		return FINDING
	except BrokenPipeError:
		return OK
	except KeyboardInterrupt:
		sys.stderr.write("%s: interrupted\n" % PROG)
		return USAGE
	except Exception as exc:  # one line, never a traceback
		sys.stderr.write("%s: unexpected %s: %s\n" % (
			PROG, type(exc).__name__, clean(exc, 200)))
		return USAGE


if __name__ == "__main__":
	sys.exit(main())
