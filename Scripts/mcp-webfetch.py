#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "beautifulsoup4",
#     "markdownify",
#     "lxml",
#     "primp",
#     "curl_cffi",
# ]
# ///
"""mcp-webfetch — MCP server for browser-emulated URL fetching with HTML→Markdown extraction.

Usage:
    uv run --script mcp-webfetch.py --project-root /path/to/project
    uv run --script mcp-webfetch.py --list
    uv run --script mcp-webfetch.py --test https://example.com   # one-shot test mode

`uv run --script` is not a suggestion: the PEP-723 block above is the only place
the third-party deps are declared, and a bare `python3` interpreter that happens
to lack beautifulsoup4 dies at import. Every documented invocation uses uv.

Single-tool dispatcher: webfetch_call(function, params)

Functions:
    fetch    Fetch a URL (GET/HEAD), return body as markdown/html/text

Browser impersonation — NEVER a pinned version:
    Linux:  primp with a bare alias ("chrome") + impersonate_os="linux".
    Other:  curl_cffi with a bare alias, resolved via its REAL_TARGET_MAP.
    Pinned majors rot, and they rot SILENTLY on primp (see _create_session).

Cache: file-based disk cache under <project_root>/.cache/webfetch/, keyed by
SHA256(method + url + request-affecting headers). Default TTL 900s (15 min);
cache_ttl=0 bypasses it entirely. A stale entry is revalidated with
If-None-Match / If-Modified-Since and refreshed in place on a 304. The raw HTML
is what gets stored; output-mode conversion happens on read.
"""

import argparse
import asyncio
import hashlib
import html as html_mod
import ipaddress
import json
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

log = logging.getLogger("mcp-webfetch")


# ---------------------------------------------------------------------------
# Impersonation — platform-aware backend, bare aliases only
#
# Linux keeps primp because curl_cffi has no impersonate_os: its chrome profile
# claims macOS, which contradicts the host's real network stack under p0f-style
# passive OS fingerprinting (measured and written up in docs/spec-ddg.md §2.7).
# primp's impersonate_os="linux" makes the UA and the stack agree.
#
# What changed, and why nothing here names a version any more: this file used to
# pin ("chrome_133", 133) … ("chrome_128", 128) plus a hand-built Linux Chrome
# header dict whose major was kept "coherent" with the pin. Measured against
# primp 1.3.1 on 2026-08-04, every one of those four names is GONE — and primp
# does not raise on an unknown name. It prints `Impersonate 'chrome_133' does
# not exist, using 'random'` to stderr and silently substitutes a random
# browser. Asking for Chrome 133 on Linux actually put a macOS Safari 26.3 TLS
# fingerprint on the wire while the header dict still announced Linux Chrome
# 133: a self-contradicting fingerprint, worse than no impersonation at all,
# and invisible on macOS because only the Linux branch takes that path.
#
# The header dict went with the pins for two independent reasons: its premise
# ("primp does not auto-inject over HTTP/2") is false for 1.3.1, which injects a
# complete navigation set including sec-ch-ua-platform for impersonate_os; and
# client-level headers= loses EVERY conflict against impersonate=, so the dict
# was already inert except for the incoherence it created.
#
# A bare alias cannot rot. primp's "chrome" spans whatever majors the installed
# build supports and rotates the major per client on its own (measured: 145,
# 146, 147, 148 across fresh clients), which is exactly what the hand-rolled
# rotation was reaching for. curl_cffi resolves "chrome"/"safari"/… through its
# public REAL_TARGET_MAP, so it tracks each release.
#
# The two backends are validated against different sets, deliberately.
# _create_session checks curl_cffi names against BrowserTypeLiteral.__args__ (53
# entries), which is the union of the aliases AND the pinned names — so an
# explicit `profile="chrome146"` is accepted there while `PRIMP_ALIASES` forbids
# the equivalent on Linux. That asymmetry follows the failure modes: a stale pin
# raises ImpersonateError on curl_cffi and is fixed in one reading, where on primp
# it degrades to a random browser with nothing but a line on stderr. (The sets are
# not identical either way: REAL_TARGET_MAP holds `tor`, which the Literal omits,
# so `profile="tor"` is refused while `tor145` passes.)
# ---------------------------------------------------------------------------

# primp offers no way to enumerate its valid names, and its Client.impersonate
# property is no help at all: measured, it echoes back whatever was passed in —
# reporting 'chrome_133' for a name that does not exist while the client actually
# impersonates Safari. Degradation IS detectable indirectly (client.headers
# ["user-agent"], read right after construction, reveals the real profile with no
# network call and matches the wire), but a whitelist is the cheaper and more
# direct guard than parsing a UA string, and it refuses a bad name before
# anything goes out.
PRIMP_ALIASES = frozenset({"chrome", "firefox", "edge", "safari", "opera", "random"})

# Retry escalation. Bot detection that refuses one engine's fingerprint often
# accepts another, so the ladder changes ENGINE, not version — a second Chrome
# major is the least informative thing to try next.
#
# Linux gets edge where the others get safari, and the reason is measured: primp's
# "safari" + impersonate_os="linux" still announces `Macintosh; Intel Mac OS X` in
# the UA. There is no coherent Linux Safari to impersonate — Safari does not exist
# on Linux — so putting it in the ladder would reintroduce on the second attempt
# exactly the UA-vs-OS incoherence the primp branch exists to remove
# (docs/spec-ddg.md §2.7). chrome and edge both report X11; Linux x86_64. A caller
# who genuinely wants Safari from a Linux host can still ask via profile=.
IMPERSONATE_LADDER = (("chrome", "edge", "firefox") if platform.system() == "Linux"
                      else ("chrome", "safari", "firefox"))

# Retried statuses: the challenge/ratelimit family. A 404 or a 500 is the
# server's real answer and retrying it with another fingerprint is superstition.
RETRY_STATUSES = frozenset({403, 429, 503})


def _create_session(profile: Optional[str] = None, timeout: int = 30) -> Tuple[Any, str]:
	"""Create a fresh browser-emulated HTTP client. Returns (client, profile_name).

	Raises ValueError on a profile name the backend cannot honour, which is the
	whole point: both libraries fail confusingly on their own. primp degrades to
	a random browser with only a line on stderr, and curl_cffi accepts anything
	at construction time and raises only when the request goes out — surfacing a
	config mistake as a network failure.
	"""
	name = profile or "chrome"

	if platform.system() == "Linux":
		import primp
		if name not in PRIMP_ALIASES:
			raise ValueError(
				f"unknown primp profile {name!r}. primp does not raise on an unknown "
				f"name, it silently uses a RANDOM browser, so only aliases are "
				f"accepted here: {', '.join(sorted(PRIMP_ALIASES))}"
			)
		# No headers= here on purpose; see the module comment above.
		client = primp.Client(
			impersonate=name,
			impersonate_os="linux",
			timeout=timeout,
		)
		return client, name

	from curl_cffi import requests
	from curl_cffi.requests.impersonate import BrowserTypeLiteral
	valid = BrowserTypeLiteral.__args__
	if name not in valid:
		raise ValueError(
			f"unknown curl_cffi profile {name!r}. Valid: {', '.join(valid)}"
		)
	client = requests.Session(impersonate=name)
	# curl_cffi's Session.headers is live and merges into the impersonation set;
	# primp's is a dict snapshot whose mutation is a silent no-op, which is the
	# other half of why the two backends are configured so differently.
	client.headers["Accept-Language"] = "en-US,en;q=0.9"
	return client, name


def _close_session(client) -> None:
	"""Best-effort cleanup. primp.Client has no close(); curl_cffi.Session does."""
	close = getattr(client, "close", None)
	if callable(close):
		try:
			close()
		except Exception as exc:
			log.debug("session close raised: %s", exc)


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

# A fetcher driven by model-authored URLs is a confused deputy: it runs on the
# developer's machine, inside whatever network that machine can reach. Cloud
# metadata endpoints and localhost admin panels are the classic targets. This is
# a resolve-then-classify check, so it is not TOCTOU-proof (a hostile DNS answer
# can change between the check and the connection) — it stops accidents and
# casual prompt injection, not a determined attacker with DNS control.
def _check_host_allowed(url: str, allow_private: bool) -> Optional[str]:
	"""Return an error string when *url* resolves somewhere it should not."""
	if allow_private:
		return None
	host = urlparse(url).hostname
	if not host:
		return f"could not parse a host out of {url[:60]!r}"
	try:
		infos = socket.getaddrinfo(host, None)
	except socket.gaierror as exc:
		return f"DNS lookup for {host!r} failed: {exc}"
	for info in infos:
		addr = info[4][0]
		try:
			ip = ipaddress.ip_address(addr)
		except ValueError:
			continue
		if (ip.is_loopback or ip.is_link_local or ip.is_private
				or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
			return (
				f"{host!r} resolves to {addr} (loopback/private/link-local). "
				f"Refusing: a URL-driven fetcher reaching the local network is "
				f"the confused-deputy case. Pass allow_private=true if this is "
				f"deliberate."
			)
	return None


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

CACHE_DIR = ".cache/webfetch"

# Total budget for the cache tree. Entries hold whole raw HTML documents, so
# without a ceiling this grows for as long as the project lives.
CACHE_MAX_BYTES = 64 * 1024 * 1024

# Never persisted, never reported. dict(resp.headers) mangles multi-value
# headers differently on each backend (primp keeps the LAST set-cookie and
# drops the rest; curl_cffi joins them with ", " into a string that cannot be
# re-parsed, because Expires dates contain commas). So the cookie line was
# already misleading, and writing session credentials into a file inside the
# project tree is a leak that buys a fetcher nothing.
_DROP_HEADERS = frozenset({"set-cookie", "set-cookie2"})

# Request headers that do not change the response body. Everything else goes
# into the cache key: a fetch carrying Authorization or Accept: application/json
# must not hand its entry to a fetch that carries neither.
_CACHE_IRRELEVANT_REQUEST_HEADERS = frozenset({"user-agent", "referer", "priority"})


def _cache_key(url: str, method: str, extra_headers: Dict[str, str]) -> str:
	h = hashlib.sha256()
	h.update(method.upper().encode())
	h.update(b"\n")
	h.update(url.encode())
	for name in sorted(extra_headers):
		if name.lower() in _CACHE_IRRELEVANT_REQUEST_HEADERS:
			continue
		h.update(f"\n{name.lower()}: {extra_headers[name]}".encode())
	return h.hexdigest()


def _cache_dir(project_root: str) -> str:
	return os.path.join(project_root, CACHE_DIR)


def _cache_path(project_root: str, key: str) -> str:
	return os.path.join(_cache_dir(project_root), f"{key}.json")


def _cache_load(project_root: str, key: str) -> Optional[dict]:
	"""Load an entry regardless of age. Freshness is the caller's decision.

	Age is not applied here because a stale entry is still useful: it carries
	the ETag that turns the next fetch into a 304 and saves the whole body.
	"""
	path = _cache_path(project_root, key)
	if not os.path.isfile(path):
		return None
	try:
		with open(path) as fh:
			return json.load(fh)
	except (json.JSONDecodeError, OSError) as exc:
		log.debug("cache read failed for %s: %s", key[:12], exc)
		return None


def _cache_age(entry: dict) -> float:
	return time.time() - entry.get("fetched_at", 0)


def _cache_save(project_root: str, key: str, entry: dict) -> None:
	path = _cache_path(project_root, key)
	tmp = f"{path}.{os.getpid()}.tmp"
	try:
		os.makedirs(_cache_dir(project_root), exist_ok=True)
		# tmp + rename so a crash mid-write cannot leave a half-written entry
		# behind. A truncated JSON would be survivable (the loader treats a
		# decode error as a miss) but it would keep costing a failed parse.
		with open(tmp, "w") as fh:
			json.dump(entry, fh)
		os.replace(tmp, path)
	except OSError as exc:
		log.warning("cache save failed: %s", exc)
		try:
			os.unlink(tmp)
		except OSError:
			pass
		return
	_cache_evict(project_root)


def _cache_evict(project_root: str) -> None:
	"""Drop the oldest entries until the tree fits CACHE_MAX_BYTES."""
	cache_dir = _cache_dir(project_root)
	try:
		names = [n for n in os.listdir(cache_dir) if n.endswith(".json")]
	except OSError:
		return
	stats = []
	total = 0
	for name in names:
		full = os.path.join(cache_dir, name)
		try:
			st = os.stat(full)
		except OSError:
			continue
		stats.append((st.st_mtime, st.st_size, full))
		total += st.st_size
	if total <= CACHE_MAX_BYTES:
		return
	stats.sort()
	for _mtime, size, full in stats:
		if total <= CACHE_MAX_BYTES:
			break
		try:
			os.unlink(full)
		except OSError:
			continue
		total -= size
		log.debug("cache evicted %s (%d bytes)", os.path.basename(full), size)


# ---------------------------------------------------------------------------
# Body conversion
# ---------------------------------------------------------------------------

_MARKDOWNIFY_OPTS = {
	"heading_style": "ATX",
	"bullets": "*",
}

_NOISE_TAGS = ("script", "style", "noscript", "iframe", "svg", "link", "meta")

# Removed only in extract mode. These are the page's furniture: on a news or
# docs page they are most of the DOM and none of the answer.
_BOILERPLATE_TAGS = ("nav", "header", "footer", "aside")

# Where the content actually is, most specific first. Deliberately a short list
# of standards-based hooks rather than a text-density score: a heuristic that
# picks the "densest block" is unpredictable per-site and impossible to debug
# from a markdown diff, and output="markdown_full" already covers the case where
# extraction guesses wrong.
_MAIN_SELECTORS = ("main", "article", "[role=main]")


def _soup(html: str) -> BeautifulSoup:
	soup = BeautifulSoup(html, "lxml")
	# markdownify's strip= removes the tag but keeps its inner text, which leaks
	# raw CSS and JS into the body; decomposing the subtree is what actually
	# drops the content.
	for tag in soup(list(_NOISE_TAGS)):
		tag.decompose()
	return soup


def _main_content(soup: BeautifulSoup) -> str:
	"""The page's main content subtree, or the whole document if none is marked."""
	for tag in soup(list(_BOILERPLATE_TAGS)):
		tag.decompose()
	for selector in _MAIN_SELECTORS:
		found = soup.select_one(selector)
		# A <main> holding a spinner is worse than the de-furnitured body, so a
		# suspiciously short hit is not treated as the content.
		if found and len(found.get_text(strip=True)) > 200:
			return str(found)
	return str(soup)


def _to_markdown(html: str, extract: bool) -> str:
	if not html:
		return ""
	soup = _soup(html)
	cleaned = _main_content(soup) if extract else str(soup)
	md = markdownify(cleaned, **_MARKDOWNIFY_OPTS)
	# Collapse 3+ blank lines markdownify can produce.
	return re.sub(r"\n{3,}", "\n\n", md).strip()


_TAG_RE = re.compile(r"<[^>]+>")


def _to_text(html: str, extract: bool) -> str:
	"""Cheap text extraction (no markdown semantics)."""
	if not html:
		return ""
	soup = _soup(html)
	text = _main_content(soup) if extract else str(soup)
	text = _TAG_RE.sub("", text)
	text = html_mod.unescape(text)
	return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Output budget — paged by LINE, per the fleet convention
# ---------------------------------------------------------------------------

# Fleet default (mcp-purity.py). An uncapped fetch of a long documentation page
# spends more context on one call than the whole session's tool descriptions.
DEFAULT_MAX_ANSWER_CHARS = 24000

# Room kept free for the accounting line while the pager fills its budget.
PAGE_LINE_RESERVE = 80


def _rows_note(start: int, shown: int, total: int) -> str:
	"""Row accounting for a row-shaped payload; goes on its LAST line.

	Display indices are 1-based inclusive, which makes the 1-based last row
	equal to the 0-based ``offset`` of the next one — so the hint is literally
	the value to pass back. The four canonical forms are shared verbatim with
	the purity/psql/jenkins twins; the wording must not drift.
	"""
	last = start + shown
	if shown <= 0:
		# Spelled out rather than as a 1-based range, which would INVERT
		# ("rows 100-99 of 10") when the caller offsets past the end.
		return (f"[no rows at offset {start} of {total}]" if start
		        else f"[{total} rows]")
	if last < total:
		return f"[showing rows {start + 1}-{last} of {total}; offset={last} for more]"
	if start > 0:
		return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
	return f"[{total} row{'s' if total != 1 else ''}]"


def _line_page(text: str, offset: int, char_budget: int) -> Tuple[str, str]:
	"""(window, accounting line) for a document — paged by LINE, not character.

	Lines are a document's natural rows: cutting at an arbitrary character index
	severs a markdown link, a table row or a fenced block, and the caller cannot
	resume from a position it was never told. Whole lines survive intact and the
	closing note says where to continue.
	"""
	lines = text.split("\n")
	total = len(lines)
	start = max(0, min(offset, total))
	kept: List[str] = []
	budget = char_budget
	for line in lines[start:]:
		# At least one line always survives: a header with no body tells the
		# caller nothing about whether the fetch worked.
		if char_budget > 0 and kept and budget - len(line) - 1 < PAGE_LINE_RESERVE:
			break
		budget -= len(line) + 1
		kept.append(line)
	complete = start == 0 and len(kept) == total
	return "\n".join(kept), ("" if complete else _rows_note(start, len(kept), total))


def _cap_text(text: str, limit: int) -> str:
	"""Hard backstop for anything that is not row-shaped (errors, headers)."""
	if limit <= 0 or len(text) <= limit:
		return text
	return text[:limit] + f"\n\n[output capped at {limit} chars; raise max_answer_chars]"


# ---------------------------------------------------------------------------
# fetch handler
# ---------------------------------------------------------------------------

# set-cookie is absent by design (see _DROP_HEADERS). etag/last-modified are
# here because revalidation now depends on them, so a caller debugging a cache
# decision can see what the server offered.
_NOTABLE_HEADERS = (
	"content-type",
	"content-length",
	"server",
	"location",
	"cache-control",
	"etag",
	"last-modified",
)

# Content types this server converts. Anything else is refused BEFORE the body
# is turned into text: resp.text on a binary body does not raise, it returns
# mojibake (measured: a 908-byte favicon becomes 875 chars, 336 of them U+FFFD;
# a 17.8 KB avif yields 7334 replacement chars) and that mojibake would land in
# the model's context and in the cache file.
_TEXTUAL_TYPES = ("text/", "application/xhtml", "application/xml", "+xml",
                  "application/json", "application/javascript")

# Ceiling on the raw document before conversion. bs4+lxml+markdownify over a
# multi-megabyte DOM is slow enough to look like a hang.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024

ACCEPTED_FETCH_PARAMS = frozenset({
	"url", "method", "output", "timeout", "max_answer_chars", "offset",
	"cache_ttl", "profile", "headers", "max_bytes", "allow_private",
})

# max_chars is the name two siblings alias away (mcp-tshark.py, mcp-wiki.py);
# accepting it keeps a caller who guesses the old name from getting an error.
PARAM_ALIASES = {
	"max_chars": "max_answer_chars",
	"max_output_chars": "max_answer_chars",
	"skip": "offset",
	"ttl": "cache_ttl",
	"impersonate": "profile",
}

OUTPUT_MODES = ("markdown", "markdown_full", "text", "text_full", "html")


def _int_param(value: Any, default: int) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _bool_param(value: Any, default: bool = False) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	if isinstance(value, (int, float)):
		return bool(value)
	return default


def _resolve_aliases(params: dict) -> dict:
	return {PARAM_ALIASES.get(k, k): v for k, v in params.items()}


def _clean_headers(raw: Any) -> Dict[str, str]:
	"""Response headers as a plain dict, minus the ones we refuse to carry."""
	try:
		items = list(raw.items())
	except Exception:
		return {}
	return {str(k): str(v) for k, v in items if str(k).lower() not in _DROP_HEADERS}


def _is_textual(content_type: str) -> bool:
	ct = content_type.lower()
	if not ct:
		# No content-type at all: HTML is the overwhelmingly likely case for a
		# URL a human or model picked, so converting is the useful default.
		return True
	return any(marker in ct for marker in _TEXTUAL_TYPES)


def _format_response(
	status: int,
	final_url: str,
	resp_headers: dict,
	body: str,
	output: str,
	max_answer_chars: int,
	offset: int,
	profile: Optional[str],
	cached: str,
	attempts: Optional[List[str]] = None,
) -> dict:
	extract = not output.endswith("_full")
	if output.startswith("markdown"):
		content = _to_markdown(body, extract)
	elif output.startswith("text"):
		content = _to_text(body, extract)
	else:  # html
		content = body or ""

	lower = {k.lower(): v for k, v in resp_headers.items()}
	notable = [f"  {k}: {lower[k]}" for k in _NOTABLE_HEADERS if k in lower]
	if not notable:
		notable = ["  (no notable headers)"]

	cache_tag = f" ({cached})" if cached else ""
	profile_tag = f" via `{profile}`" if profile else ""
	ladder_tag = ""
	if attempts and len(attempts) > 1:
		ladder_tag = f"\n**retries**: {' → '.join(attempts)}"

	head = (
		f"# webfetch — `{final_url}`{cache_tag}\n\n"
		f"**status**: {status}{profile_tag}{ladder_tag}\n"
		f"**output**: {output}\n\n"
		f"## Response headers (selected)\n\n"
		f"```\n" + "\n".join(notable) + "\n```\n\n"
		f"## Body ({output})\n\n"
	)

	# The cap covers the WHOLE emitted text. Budgeting the body alone and then
	# prepending the header block — what this function used to do — meant the
	# result always overshot the number the caller asked for.
	# max(1, …) and not max(0, …): a 0 budget means "unlimited" to the pager, so
	# a max_answer_chars smaller than the header block would invert into an
	# uncapped body. One char lets the one-line-always-survives rule through, and
	# the _cap_text call below is the hard stop.
	budget = max(1, max_answer_chars - len(head)) if max_answer_chars > 0 else 0
	window, note = _line_page(content, offset, budget)
	# Cap the WINDOW, then append the note. The order is the whole point: the
	# pager guarantees at least one line survives, so a single minified line (a
	# measured 87929-char HTML row is not exotic) would otherwise overshoot the
	# ceiling by two orders of magnitude — while capping the FINISHED text instead
	# severs the trailing `offset=N` hint, the one line whose job is to say how to
	# get the rest. Capping here bounds the output at head + budget + note and
	# leaves the note intact, because the note is added afterwards.
	window = _cap_text(window, budget)
	tail = f"\n\n{note}" if note else ""
	return {"__raw_text__": head + window + tail + "\n"}


def _issue(session, method: str, url: str, headers: dict, timeout: int):
	"""One request. primp honours a per-request timeout over the client's."""
	if method == "HEAD":
		return session.head(url, headers=headers, timeout=timeout)
	return session.get(url, headers=headers, timeout=timeout)


def _fetch_once(
	profile: Optional[str], method: str, url: str, headers: dict,
	timeout: int, max_bytes: int,
) -> dict:
	"""Fetch with one profile. Returns a dict with status/headers/body or error."""
	session, profile_name = _create_session(profile, timeout)
	try:
		try:
			resp = _issue(session, method, url, headers, timeout)
		except Exception as exc:
			return {"error": f"fetch failed: {type(exc).__name__}: {exc}",
			        "profile": profile_name}

		status = getattr(resp, "status_code", 0)
		# .url is the FINAL url on both backends; redirects are followed by
		# default (primp: follow_redirects=True, max_redirects=20).
		final_url = str(getattr(resp, "url", url) or url)
		resp_headers = _clean_headers(getattr(resp, "headers", {}) or {})
		lower = {k.lower(): v for k, v in resp_headers.items()}

		body = ""
		size = -1
		if method == "GET" and status != 304:
			ctype = lower.get("content-type", "")
			if not _is_textual(ctype):
				return {"error": (
					f"refusing to convert non-textual content-type {ctype!r} "
					f"from {final_url} (status {status}). resp.text on a binary "
					f"body yields replacement-character mojibake, not content."
				), "profile": profile_name}
			# Measured on the RAW BYTES, which both backends already hold in
			# .content, so it costs nothing — and unlike Content-Length it can
			# neither lie nor be absent (it is missing on every chunked or gzipped
			# response). Comparing len(str) against a byte ceiling was the earlier
			# version and it erred the PERMISSIVE way: characters are never more
			# numerous than bytes, so ~3M chars of CJK (≈9 MB) sailed past a 5 MB
			# limit untouched.
			raw = getattr(resp, "content", None)
			size = len(raw) if raw is not None else -1
			if max_bytes > 0 and size > max_bytes:
				# Refused, not truncated. A silently halved document converts to
				# markdown that just stops, with nothing in the output to tell the
				# reader the tail is missing; a ceiling the caller can raise is the
				# honest version of the same limit.
				return {"error": (
					f"body is {size} bytes, over max_bytes={max_bytes}. Raise "
					f"max_bytes to convert it anyway."
				), "profile": profile_name}
			body = resp.text or ""
		return {
			"status": status,
			"final_url": final_url,
			"headers": resp_headers,
			"body": body,
			"size": size,
			"profile": profile_name,
		}
	finally:
		_close_session(session)


def handle_fetch(params: dict, project_root: str) -> dict:
	url = (params.get("url") or "").strip()
	if not url:
		return {"error": "url is required"}
	if not (url.startswith("http://") or url.startswith("https://")):
		return {"error": f"url must start with http:// or https:// (got {url[:40]!r})"}

	method = (params.get("method") or "GET").upper()
	if method not in ("GET", "HEAD"):
		return {"error": f"method {method} not supported yet (GET, HEAD only)"}

	output = (params.get("output") or "markdown").lower()
	if output not in OUTPUT_MODES:
		return {"error": f"output must be one of {', '.join(OUTPUT_MODES)} (got {output!r})"}

	timeout = _int_param(params.get("timeout"), 30)
	max_answer_chars = _int_param(params.get("max_answer_chars"), DEFAULT_MAX_ANSWER_CHARS)
	offset = max(0, _int_param(params.get("offset"), 0))
	ttl = _int_param(params.get("cache_ttl"), 900)
	max_bytes = _int_param(params.get("max_bytes"), DEFAULT_MAX_BYTES)
	allow_private = _bool_param(params.get("allow_private"))
	profile = params.get("profile") or None
	extra_headers = params.get("headers") or {}
	if not isinstance(extra_headers, dict):
		return {"error": "headers must be a dict"}

	# The guard runs BEFORE the cache read, not after it. "You may not fetch this
	# URL" and "here is that URL's content off the disk" are the same act from the
	# caller's side, so checking afterwards meant one allow_private=true call
	# seeded an entry that every later allow_private=false call would serve
	# happily for the rest of the TTL.
	blocked = _check_host_allowed(url, allow_private)
	if blocked:
		return {"error": blocked}

	key = _cache_key(url, method, extra_headers)
	entry = _cache_load(project_root, key) if ttl > 0 else None

	if entry and _cache_age(entry) <= ttl:
		# max_bytes has to be honoured here too. The ceiling exists to bound the
		# bs4+markdownify pass, and that pass runs on a cached document exactly as
		# it does on a fresh one — checking only inside _fetch_once meant a cached
		# 1.3 MB body came back 200 for a caller who asked for max_bytes=1000,
		# while the tool description promised a refusal. Entries written before
		# `size` existed report -1 and are simply not checked.
		cached_size = _int_param(entry.get("size"), -1)
		if max_bytes > 0 and cached_size > max_bytes:
			return {"error": (
				f"cached body is {cached_size} bytes, over max_bytes={max_bytes}. "
				f"Raise max_bytes to convert it anyway."
			)}
		return _format_response(
			entry.get("status", 0), entry.get("final_url", url),
			entry.get("headers", {}), entry.get("body", ""),
			output, max_answer_chars, offset,
			profile=entry.get("profile"), cached="cached",
		)

	# A stale entry still earns its keep: revalidating costs one round trip and
	# a 304 skips the whole body. The 304 response itself carries no body
	# (measured), so the cached one is what gets reused.
	req_headers = dict(extra_headers)
	if entry:
		if entry.get("etag"):
			req_headers.setdefault("If-None-Match", entry["etag"])
		elif entry.get("last_modified"):
			req_headers.setdefault("If-Modified-Since", entry["last_modified"])

	ladder = [profile] if profile else list(IMPERSONATE_LADDER)
	attempts: List[str] = []
	result: dict = {}
	for i, candidate in enumerate(ladder):
		result = _fetch_once(candidate, method, url, req_headers, timeout, max_bytes)
		attempts.append(result.get("profile") or str(candidate))
		if "error" in result:
			return {"error": result["error"]}
		if result["status"] not in RETRY_STATUSES:
			break
		if i + 1 < len(ladder):
			log.debug("status %s on %s, escalating past %s",
			          result["status"], url, attempts[-1])
			time.sleep(0.5 * (i + 1))

	status = result["status"]
	lower = {k.lower(): v for k, v in result["headers"].items()}

	if status == 304 and entry:
		entry["fetched_at"] = int(time.time())
		if lower.get("etag"):
			entry["etag"] = lower["etag"]
		_cache_save(project_root, key, entry)
		return _format_response(
			entry.get("status", 200), entry.get("final_url", url),
			entry.get("headers", {}), entry.get("body", ""),
			output, max_answer_chars, offset,
			profile=result.get("profile"), cached="revalidated",
			attempts=attempts,
		)

	# status != 304 guards a protocol-violating server that answers 304 when we
	# sent no conditional header (or hold no entry to revalidate): the branch
	# above would not have run, and storing that empty body under status 304
	# would make every within-TTL call serve an empty document.
	if ttl > 0 and 200 <= status < 400 and status != 304:
		_cache_save(project_root, key, {
			"url": url,
			"method": method,
			"status": status,
			"final_url": result["final_url"],
			"headers": result["headers"],
			"body": result["body"],
			"size": result.get("size", -1),
			"profile": result["profile"],
			"etag": lower.get("etag", ""),
			"last_modified": lower.get("last-modified", ""),
			"fetched_at": int(time.time()),
		})

	return _format_response(
		status, result["final_url"], result["headers"], result["body"],
		output, max_answer_chars, offset,
		profile=result["profile"], cached="", attempts=attempts,
	)


# ---------------------------------------------------------------------------
# Handler registry + dispatcher
# ---------------------------------------------------------------------------

HANDLERS: Dict[str, Callable[[dict, str], dict]] = {
	"fetch": handle_fetch,
	# aliases
	"get":   handle_fetch,
	"wget":  handle_fetch,
	"curl":  handle_fetch,
}

_PRIMARY_FUNCTIONS = {"fetch"}


def _ensure_dict(value: Any, name: str = "params") -> dict:
	"""Coerce *value* to a dict.

	Accepts None (→ {}), dict (passthrough), or JSON-encoded object string.
	Raises ValueError on a non-JSON string, JSON that is not an object,
	or any other type.
	"""
	if value is None:
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except json.JSONDecodeError as exc:
			raise ValueError(
				f"'{name}' was a string but not valid JSON: {exc}. "
				f"Pass '{name}' as an object, not a JSON-encoded string."
			)
	if not isinstance(value, dict):
		raise ValueError(
			f"'{name}' must be an object (dict) or a JSON-encoded object string; "
			f"got {type(value).__name__}."
		)
	return value


def handle_webfetch_call(arguments: dict, project_root: str) -> dict:
	function = (arguments.get("function") or arguments.get("f") or "").strip()
	raw_params = arguments.get("params") or arguments.get("p") or {}
	try:
		params = _resolve_aliases(_ensure_dict(raw_params))
	except ValueError as exc:
		return {"error": str(exc)}

	if not function:
		backend = "primp (Linux)" if platform.system() == "Linux" else "curl_cffi"
		funcs = "\n".join(f"  {n}" for n in sorted(_PRIMARY_FUNCTIONS))
		return {"__raw_text__": (
			f"mcp-webfetch OK — project: {project_root}\n"
			f"Backend: {backend}, impersonate=chrome (bare alias, never pinned)\n"
			f"Cache dir: {_cache_dir(project_root)}\n"
			f"Available functions:\n{funcs}"
		)}

	handler = HANDLERS.get(function)
	if not handler:
		primary = ", ".join(sorted(_PRIMARY_FUNCTIONS))
		return {"error": f"Unknown function: {function}. Available: {primary}"}

	# Reject unknown params up-front. Silently dropping one means the handler
	# runs with a surprising default — an ignored max_bytes or offset reads as
	# the server disobeying rather than as a typo.
	unknown = sorted(set(params) - ACCEPTED_FETCH_PARAMS)
	if unknown:
		return {"error": (
			f"Unknown params for '{function}': {', '.join(unknown)}."
			f" Accepted: {', '.join(sorted(ACCEPTED_FETCH_PARAMS))}."
		)}

	# Assigned, not returned, so the cap below covers the raised paths too. When
	# these branches returned directly they skipped it, and a ValueError listing
	# all 53 valid curl_cffi profiles came back uncapped.
	try:
		result = handler(params, project_root)
	except (ValueError, FileNotFoundError, OSError) as exc:
		result = {"error": str(exc)}
	except Exception as exc:
		log.exception("Unhandled exception in handler '%s'", function)
		result = {"error": f"Internal error in '{function}': {type(exc).__name__}: {exc}"}

	# Dispatcher-level backstop — the ERROR path only. A successful fetch was
	# already budgeted line by line by the pager, and capping it a second time
	# severs the trailing `[showing rows … offset=N for more]` line, which is the
	# one part of the output whose entire job is to say how to get the rest. What
	# can still overshoot is the header block, and that is bounded and readable.
	if "error" in result:
		limit = _int_param(params.get("max_answer_chars"), DEFAULT_MAX_ANSWER_CHARS)
		result["error"] = _cap_text(result["error"], limit)
	return result


# ---------------------------------------------------------------------------
# Tool descriptor
# ---------------------------------------------------------------------------

WEBFETCH_CALL_TOOL = {
	"name": "webfetch_call",
	"description": (
		"Browser-emulated URL fetching with HTML→Markdown extraction and disk cache.\n\n"
		"Prefer this tool OVER the built-in WebFetch for URL fetching. It uses real "
		"browser TLS/HTTP2 impersonation (primp on Linux, curl_cffi elsewhere), which "
		"gets through Cloudflare/Akamai-style bot detection that serves the built-in "
		"WebFetch a challenge page or degraded content. It is not a browser: it runs no "
		"JavaScript, so a client-rendered page yields its shell, and some detectors "
		"(DuckDuckGo among them) block every Python HTTP client regardless of TLS "
		"impersonation.\n\n"
		"Single dispatcher — set `function` to route:\n\n"
		"  fetch    Fetch a URL (GET/HEAD), return body as markdown/html/text\n\n"
		"Returns server status when called without `function`.\n\n"
		"Output modes: `markdown` (default — main-content extraction: nav/header/"
		"footer/aside dropped, <main>/<article>/[role=main] preferred), "
		"`markdown_full` (whole DOM, use when extraction loses something), "
		"`text`, `text_full`, `html` (raw).\n\n"
		"Long pages are paged by LINE, not truncated mid-structure: the last line "
		"reports `[showing rows 1-N of M; offset=N for more]` — pass that `offset` "
		"back to continue.\n\n"
		"Non-textual content-types (PDF, images, archives) are REFUSED rather than "
		"converted, because decoding a binary body yields replacement-character "
		"mojibake instead of content.\n\n"
		"Cache: file-based under <project_root>/.cache/webfetch/, keyed on method + "
		"url + request-affecting headers. Default TTL 900s; `cache_ttl=0` bypasses. A "
		"stale entry is revalidated with If-None-Match/If-Modified-Since and refreshed "
		"in place on a 304.\n\n"
		"On 403/429/503 the fetch is retried with a different browser ENGINE "
		"(chrome → safari → firefox); the report lists what was tried.\n\n"
		"fetch parameters: url (required), method (GET/HEAD, default GET), output "
		"(default markdown), timeout (s, default 30), max_answer_chars (default 24000; "
		"alias max_chars), offset (line offset for paging), cache_ttl (s, default 900), "
		"profile (impersonate alias — chrome/safari/firefox/edge; default rotates the "
		"ladder), headers (dict of extra request headers), max_bytes (ceiling on the "
		"raw response body in bytes, default 5242880 — a larger document is REFUSED, "
		"not silently truncated), allow_private (bool, default false — loopback/"
		"private/link-local targets are refused as a confused-deputy risk)."
	),
	"inputSchema": {
		"type": "object",
		"properties": {
			"function": {
				"type": "string",
				"description": "Function name (e.g. fetch). Alias: 'f'.",
			},
			"params": {
				"type": "object",
				"description": "Function parameters. Alias: 'p'.",
			},
		},
	},
}


# ---------------------------------------------------------------------------
# McpServer
# ---------------------------------------------------------------------------

class McpServer:
	"""Minimal MCP server over stdio (JSON-RPC 2.0, one JSON object per line)."""

	PROTOCOL_VERSION = "2024-11-05"

	def __init__(self, project_root: str):
		self.project_root = os.path.realpath(project_root)
		# Handlers run in executor threads, so two responses can be ready at
		# once; interleaved writes would corrupt the line protocol.
		self._write_lock = threading.Lock()
		# In-flight dispatches. Held so the loop keeps a strong reference — a
		# bare ensure_future() can be garbage-collected mid-execution — and so
		# shutdown can drain them instead of cutting a fetch in half.
		self._inflight: set = set()

	async def run(self) -> None:
		loop = asyncio.get_running_loop()
		log.info("MCP server starting, project_root=%s", self.project_root)
		try:
			while True:
				line = await loop.run_in_executor(None, sys.stdin.readline)
				if not line:
					break
				line = line.strip()
				if not line:
					continue
				try:
					msg = json.loads(line)
				except json.JSONDecodeError as exc:
					log.warning("Invalid JSON: %s", exc)
					continue
				log.debug("← %s", json.dumps(msg)[:200])
				# The fetch is blocking and can occupy the full 30s timeout.
				# Handling it on the loop would stall ping and every other
				# request for the duration, so it goes to a thread — the same
				# move mcp-jenkins and mcp-purity make for their sync backends.
				task = asyncio.ensure_future(self._dispatch(loop, msg))
				self._inflight.add(task)
				task.add_done_callback(self._inflight.discard)
		finally:
			if self._inflight:
				log.debug("draining %d in-flight request(s)", len(self._inflight))
				await asyncio.gather(*self._inflight, return_exceptions=True)
			log.info("MCP server shutting down")

	async def _dispatch(self, loop: asyncio.AbstractEventLoop, msg: dict) -> None:
		try:
			response = await loop.run_in_executor(None, self._handle_message, msg)
		except Exception as exc:
			log.exception("Unhandled exception while handling message")
			response = self._error(
				msg.get("id"), -32603,
				f"Internal error: {type(exc).__name__}: {exc}",
			)
		if response is None:
			return
		out = json.dumps(response)
		log.debug("→ %s", out[:200])
		with self._write_lock:
			sys.stdout.write(out + "\n")
			sys.stdout.flush()

	def _handle_message(self, msg: dict) -> Optional[dict]:
		msg_id = msg.get("id")
		method = msg.get("method", "")
		params = msg.get("params") or {}

		if msg_id is None:
			log.debug("Notification: %s", method)
			return None

		if method == "initialize":
			return self._result(msg_id, {
				"protocolVersion": self.PROTOCOL_VERSION,
				"serverInfo": {"name": "mcp-webfetch", "version": "1.0.0"},
				"capabilities": {"tools": {}},
			})
		if method == "ping":
			return self._result(msg_id, {})
		if method == "tools/list":
			return self._result(msg_id, {"tools": [WEBFETCH_CALL_TOOL]})
		if method == "tools/call":
			return self._handle_tool_call(msg_id, params)
		return self._error(msg_id, -32601, f"Method not found: {method}")

	def _handle_tool_call(self, msg_id: Any, params: dict) -> dict:
		tool_name = params.get("name", "")
		arguments = params.get("arguments") or {}
		if isinstance(arguments, str):
			try:
				arguments = json.loads(arguments)
			except json.JSONDecodeError as exc:
				return self._tool_error(
					msg_id, f"'arguments' was a string but not valid JSON: {exc}")
		if tool_name != "webfetch_call":
			return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
		if not isinstance(arguments, dict):
			return self._tool_error(
				msg_id, f"'arguments' must be an object; got {type(arguments).__name__}.")
		try:
			result = handle_webfetch_call(arguments, self.project_root)
		except Exception as exc:
			log.exception("Unhandled exception in handle_webfetch_call")
			result = {"error": f"Internal server error: {type(exc).__name__}: {exc}"}
		is_error = "error" in result
		text = result.get("__raw_text__") or result.get("error", "")
		return self._result(msg_id, {
			"content": [{"type": "text", "text": text}],
			"isError": is_error,
		})

	@staticmethod
	def _result(msg_id: Any, result: dict) -> dict:
		return {"jsonrpc": "2.0", "id": msg_id, "result": result}

	@staticmethod
	def _error(msg_id: Any, code: int, message: str) -> dict:
		return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

	@classmethod
	def _tool_error(cls, msg_id: Any, message: str) -> dict:
		"""A tool-level failure: an isError envelope, not a JSON-RPC error."""
		return cls._result(msg_id, {
			"content": [{"type": "text", "text": message}],
			"isError": True,
		})


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> None:
	parser = argparse.ArgumentParser(description="mcp-webfetch MCP server")
	parser.add_argument("--project-root", default=os.getcwd(),
	                    help="Project root (cache + sandbox base). Default: cwd.")
	parser.add_argument("--list", action="store_true",
	                    help="List handlers and exit")
	parser.add_argument("--test", metavar="URL",
	                    help="One-shot test fetch; print formatted result and exit")
	parser.add_argument("--output", default="markdown",
	                    help=f"Output mode for --test ({'/'.join(OUTPUT_MODES)}). Default: markdown.")
	parser.add_argument("--max-chars", type=int, default=5000,
	                    help="Max chars for --test output. Default: 5000.")
	parser.add_argument("--profile", default="",
	                    help="Impersonate alias for --test (default: rotate the ladder).")
	parser.add_argument("--no-cache", action="store_true",
	                    help="Bypass the disk cache for --test. A 15-minute-old entry "
	                         "otherwise reports success while the live fetch is broken.")
	parser.add_argument("--allow-private", action="store_true",
	                    help="Allow --test against loopback/private/link-local hosts.")
	parser.add_argument("--log-file", default="",
	                    help="Log to file instead of stderr.")
	parser.add_argument("--debug", "-v", "--verbose", dest="debug",
	                    action="store_true", help="Verbose logging.")
	args = parser.parse_args()
	level = logging.DEBUG if (args.debug or args.log_file) else logging.WARNING
	log_handlers = []
	if args.log_file:
		log_handlers.append(logging.FileHandler(args.log_file))
	else:
		log_handlers.append(logging.StreamHandler(sys.stderr))
	logging.basicConfig(
		level=level,
		format="%(asctime)s %(name)s %(levelname)s %(message)s",
		handlers=log_handlers,
	)

	if args.list:
		for name in sorted(HANDLERS):
			tag = "" if name in _PRIMARY_FUNCTIONS else " (alias)"
			print(f"  {name}{tag}")
		return

	if args.test:
		params = {
			"url": args.test,
			"output": args.output,
			"max_answer_chars": args.max_chars,
			"allow_private": args.allow_private,
		}
		if args.no_cache:
			params["cache_ttl"] = 0
		if args.profile:
			params["profile"] = args.profile
		# --test is the only caller that reaches handle_fetch without going
		# through handle_webfetch_call, so it has to honour the same error
		# contract. Without this, the ValueError _create_session raises on
		# purpose for an unknown --profile reaches the user as a traceback
		# instead of as the message that lists the valid names.
		try:
			result = handle_fetch(params, os.path.realpath(args.project_root))
		except (ValueError, FileNotFoundError, OSError) as exc:
			result = {"error": str(exc)}
		print(result.get("__raw_text__") or result.get("error", "no output"))
		return

	asyncio.run(McpServer(args.project_root).run())


if __name__ == "__main__":
	main()
