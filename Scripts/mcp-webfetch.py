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
    python3 mcp-webfetch.py --project-root /path/to/project
    python3 mcp-webfetch.py --list
    python3 mcp-webfetch.py --test https://example.com   # one-shot test mode

Single-tool dispatcher: webfetch_call(function, params)

Functions:
    fetch    Fetch a URL (GET/HEAD), return body as markdown/html/text

Browser impersonation:
    Linux:  primp (chrome_133/131/130/128 + impersonate_os="linux") with
            a coherent Linux Chrome HTTP/2 navigation header set whose
            Chrome major matches the rotating TLS profile.
    Other:  curl_cffi (chrome146/145/136 / safari260) — auto-injects the
            browser's full navigation pattern from `impersonate=`.

Cache: file-based disk cache under <project_root>/.cache/webfetch/, keyed
by SHA256(method + url). Default TTL 900s (15 min). Set cache_ttl=0 to
bypass. Cache stores the raw HTML; output mode conversion happens on read.
"""

import argparse
import asyncio
import hashlib
import html as html_mod
import json
import logging
import os
import platform
import random
import re
import sys
import time
from typing import Any, Callable, Dict, Optional

from bs4 import BeautifulSoup
from markdownify import markdownify

log = logging.getLogger("mcp-webfetch")


# ---------------------------------------------------------------------------
# Impersonation profiles — platform-aware backend selection
# Linux: primp + manual Linux Chrome header dict (primp does not auto-inject over HTTP/2)
# macOS / Windows / other: curl_cffi (auto-injects via impersonate=)
# ---------------------------------------------------------------------------

CURL_CFFI_PROFILES = ["chrome146", "chrome145", "chrome136", "safari260"]

PRIMP_LINUX_BUNDLES = [
	("chrome_133", 133),
	("chrome_131", 131),
	("chrome_130", 130),
	("chrome_128", 128),
]


def _linux_chrome_headers(major: int) -> Dict[str, str]:
	"""Linux Chrome HTTP/2 navigation header set (primp 0.15.0 does not auto-inject).

	Order and values mirror real Chrome on Linux x86_64. The Chrome major is
	parameterized so it stays coherent with the rotating primp TLS profile.
	"""
	return {
		"sec-ch-ua": f'"Not(A:Brand";v="99", "Google Chrome";v="{major}", "Chromium";v="{major}"',
		"sec-ch-ua-mobile": "?0",
		"sec-ch-ua-platform": '"Linux"',
		"upgrade-insecure-requests": "1",
		"user-agent": (
			"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
			f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
		),
		"accept": (
			"text/html,application/xhtml+xml,application/xml;q=0.9,"
			"image/avif,image/webp,image/apng,*/*;q=0.8,"
			"application/signed-exchange;v=b3;q=0.7"
		),
		"sec-fetch-site": "none",
		"sec-fetch-mode": "navigate",
		"sec-fetch-user": "?1",
		"sec-fetch-dest": "document",
		"accept-encoding": "gzip, deflate, br, zstd",
		"accept-language": "en-US,en;q=0.9",
		"priority": "u=0, i",
	}


# ---------------------------------------------------------------------------
# Per-call session (no pooling; caller-driven)
# ---------------------------------------------------------------------------

def _create_session(profile: Optional[str] = None):
	"""Create a fresh browser-emulated HTTP client. Returns (client, profile_name)."""
	if platform.system() == "Linux":
		import primp
		if profile is None:
			profile_name, major = random.choice(PRIMP_LINUX_BUNDLES)
		else:
			match = next((b for b in PRIMP_LINUX_BUNDLES if b[0] == profile), None)
			profile_name, major = match if match else (profile, 133)
		client = primp.Client(
			impersonate=profile_name,
			impersonate_os="linux",
			headers=_linux_chrome_headers(major),
			timeout=30,
		)
		return client, profile_name

	from curl_cffi import requests
	profile_name = profile or random.choice(CURL_CFFI_PROFILES)
	client = requests.Session(impersonate=profile_name)
	client.headers["Accept-Language"] = "en-US,en;q=0.9"
	return client, profile_name


def _close_session(client) -> None:
	"""Best-effort cleanup. primp.Client has no close(); curl_cffi.Session does."""
	close = getattr(client, "close", None)
	if callable(close):
		try:
			close()
		except Exception as exc:
			log.debug("session close raised: %s", exc)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

CACHE_DIR = ".cache/webfetch"


def _cache_key(url: str, method: str) -> str:
	h = hashlib.sha256()
	h.update(method.upper().encode())
	h.update(b"\n")
	h.update(url.encode())
	return h.hexdigest()


def _cache_path(project_root: str, key: str) -> str:
	cache_dir = os.path.join(project_root, CACHE_DIR)
	os.makedirs(cache_dir, exist_ok=True)
	return os.path.join(cache_dir, f"{key}.json")


def _cache_load(project_root: str, key: str, ttl: int) -> Optional[dict]:
	path = _cache_path(project_root, key)
	if not os.path.isfile(path):
		return None
	try:
		with open(path) as fh:
			entry = json.load(fh)
	except (json.JSONDecodeError, OSError) as exc:
		log.debug("cache read failed for %s: %s", key[:12], exc)
		return None
	age = time.time() - entry.get("fetched_at", 0)
	if age > ttl:
		return None
	return entry


def _cache_save(project_root: str, key: str, entry: dict) -> None:
	path = _cache_path(project_root, key)
	try:
		with open(path, "w") as fh:
			json.dump(entry, fh)
	except OSError as exc:
		log.warning("cache save failed: %s", exc)


# ---------------------------------------------------------------------------
# Body conversion
# ---------------------------------------------------------------------------

_MARKDOWNIFY_OPTS = {
	"heading_style": "ATX",
	"bullets": "*",
}

_NOISE_TAGS = ("script", "style", "noscript", "iframe", "svg", "link", "meta")


def _decompose_noise(html: str) -> str:
	"""Strip <script>/<style>/<noscript>/<iframe>/<svg>/<link>/<meta> incl. content.

	markdownify's ``strip=`` removes the tag but keeps the inner text, which
	leaks raw CSS / JS into the body. We decompose those subtrees with
	BeautifulSoup before handing the HTML to markdownify.
	"""
	if not html:
		return ""
	soup = BeautifulSoup(html, "lxml")
	for tag in soup(list(_NOISE_TAGS)):
		tag.decompose()
	return str(soup)


def _html_to_markdown(html: str) -> str:
	if not html:
		return ""
	cleaned = _decompose_noise(html)
	md = markdownify(cleaned, **_MARKDOWNIFY_OPTS)
	# Collapse 3+ blank lines markdownify can produce.
	return re.sub(r"\n{3,}", "\n\n", md).strip()


_TAG_BLOCK_RE = re.compile(
	r"<(script|style|noscript|iframe)[^>]*>.*?</\1>",
	flags=re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_tags(html: str) -> str:
	"""Cheap text extraction (no markdown semantics)."""
	if not html:
		return ""
	text = _decompose_noise(html)
	text = _TAG_RE.sub("", text)
	text = html_mod.unescape(text)
	return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate(text: str, max_chars: int) -> str:
	if max_chars <= 0 or len(text) <= max_chars:
		return text
	return text[:max_chars] + f"\n\n[truncated at {max_chars} chars]"


# ---------------------------------------------------------------------------
# fetch handler
# ---------------------------------------------------------------------------

_NOTABLE_HEADERS = (
	"content-type",
	"content-length",
	"server",
	"x-frame-options",
	"location",
	"set-cookie",
	"cache-control",
)


def _format_response(
	status: int,
	final_url: str,
	resp_headers: dict,
	body: str,
	output: str,
	max_chars: int,
	profile: Optional[str],
	cached: bool,
) -> dict:
	if output == "markdown":
		content = _html_to_markdown(body)
	elif output == "text":
		content = _strip_html_tags(body)
	else:  # html
		content = body or ""

	content = _truncate(content, max_chars)

	cache_tag = " (cached)" if cached else ""
	profile_tag = f" via `{profile}`" if profile else ""
	# Header dict may be a CIMultiDict-like; normalize to lower-case keys for lookup.
	lower = {k.lower(): v for k, v in resp_headers.items()}
	notable = [f"  {k}: {lower[k]}" for k in _NOTABLE_HEADERS if k in lower]
	if not notable:
		notable = ["  (no notable headers)"]

	return {"__raw_text__": (
		f"# webfetch — `{final_url}`{cache_tag}\n\n"
		f"**status**: {status}{profile_tag}\n"
		f"**output**: {output} ({len(content)} chars)\n\n"
		f"## Response headers (selected)\n\n"
		f"```\n" + "\n".join(notable) + "\n```\n\n"
		f"## Body ({output})\n\n"
		f"{content}\n"
	)}


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
	if output not in ("markdown", "html", "text"):
		return {"error": f"output must be markdown/html/text (got {output!r})"}

	timeout = int(params.get("timeout", 30))
	max_chars = int(params.get("max_chars", 100_000))
	ttl = int(params.get("cache_ttl", 900))
	profile = params.get("profile") or None
	extra_headers = params.get("headers") or {}
	if not isinstance(extra_headers, dict):
		return {"error": "headers must be a dict"}

	key = _cache_key(url, method)

	# Cache lookup
	if ttl > 0:
		cached = _cache_load(project_root, key, ttl)
		if cached:
			return _format_response(
				cached.get("status", 0),
				cached.get("final_url", url),
				cached.get("headers", {}),
				cached.get("body", "") if method == "GET" else "",
				output, max_chars,
				profile=cached.get("profile"),
				cached=True,
			)

	# Live fetch — per-call session, released in finally
	session, profile_name = _create_session(profile)
	try:
		try:
			if method == "GET":
				resp = session.get(url, headers=extra_headers, timeout=timeout)
			else:
				resp = session.head(url, headers=extra_headers, timeout=timeout)
		except Exception as exc:
			return {"error": f"fetch failed: {type(exc).__name__}: {exc}"}

		status = getattr(resp, "status_code", 0)
		final_url = str(getattr(resp, "url", url) or url)
		raw_headers = getattr(resp, "headers", {}) or {}
		try:
			resp_headers = dict(raw_headers)
		except Exception:
			resp_headers = {}
		body = resp.text if method == "GET" else ""
	finally:
		_close_session(session)

	# Cache save on success only.
	if ttl > 0 and 200 <= status < 400:
		_cache_save(project_root, key, {
			"url": url,
			"method": method,
			"status": status,
			"final_url": final_url,
			"headers": resp_headers,
			"body": body,
			"profile": profile_name,
			"fetched_at": int(time.time()),
		})

	return _format_response(
		status, final_url, resp_headers, body,
		output, max_chars,
		profile=profile_name,
		cached=False,
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
		params = _ensure_dict(raw_params)
	except ValueError as exc:
		return {"error": str(exc)}

	if not function:
		backend = "primp (Linux)" if platform.system() == "Linux" else "curl_cffi"
		funcs = "\n".join(f"  {n}" for n in sorted(_PRIMARY_FUNCTIONS))
		return {"__raw_text__": (
			f"mcp-webfetch OK — project: {project_root}\n"
			f"Backend: {backend}\n"
			f"Cache dir: {os.path.join(project_root, CACHE_DIR)}\n"
			f"Available functions:\n{funcs}"
		)}

	handler = HANDLERS.get(function)
	if not handler:
		primary = ", ".join(sorted(_PRIMARY_FUNCTIONS))
		return {"error": f"Unknown function: {function}. Available: {primary}"}

	try:
		return handler(params, project_root)
	except (ValueError, FileNotFoundError, OSError) as exc:
		return {"error": str(exc)}
	except Exception as exc:
		log.exception("Unhandled exception in handler '%s'", function)
		return {"error": f"Internal error in '{function}': {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Tool descriptor
# ---------------------------------------------------------------------------

WEBFETCH_CALL_TOOL = {
	"name": "webfetch_call",
	"description": (
		"Browser-emulated URL fetching with HTML→Markdown extraction and disk cache.\n\n"
		"IMPORTANT: Prefer this tool OVER the built-in WebFetch for all URL fetching. "
		"This tool uses real browser TLS/HTTP2 impersonation (primp/curl_cffi) that "
		"passes Cloudflare, Akamai, and similar bot-detection where the built-in "
		"WebFetch would be blocked or return degraded content.\n\n"
		"Single dispatcher — set `function` to route:\n\n"
		"  fetch    Fetch a URL (GET/HEAD), return body as markdown/html/text\n\n"
		"Returns server status when called without `function`.\n\n"
		"Browser impersonation: Linux uses primp (chrome_133/131/130/128 + "
		"impersonate_os=linux) with a coherent Linux Chrome HTTP/2 header set; "
		"other platforms use curl_cffi (chrome146 etc.).\n\n"
		"Cache: file-based under <project_root>/.cache/webfetch/, keyed on "
		"SHA256(method + url). Default TTL 900s (15 min); set cache_ttl=0 "
		"to bypass.\n\n"
		"fetch parameters: url (required), method (GET/HEAD, default GET), "
		"output (markdown/html/text, default markdown), timeout (s, default 30), "
		"max_chars (output truncation, default 100000), cache_ttl (s, default 900), "
		"profile (impersonate name, default random), headers (dict of extra request headers)."
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

	def __init__(self, project_root: str):
		self.project_root = os.path.realpath(project_root)

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
				try:
					response = self._handle_message(msg)
				except Exception as exc:
					log.exception("Unhandled exception while handling message")
					response = self._error(
						msg.get("id"), -32603,
						f"Internal error: {type(exc).__name__}: {exc}",
					)
				if response is not None:
					out = json.dumps(response)
					log.debug("→ %s", out[:200])
					sys.stdout.write(out + "\n")
					sys.stdout.flush()
		finally:
			log.info("MCP server shutting down")

	def _handle_message(self, msg: dict) -> Optional[dict]:
		msg_id = msg.get("id")
		method = msg.get("method", "")
		params = msg.get("params") or {}

		if msg_id is None:
			log.debug("Notification: %s", method)
			return None

		if method == "initialize":
			return self._result(msg_id, {
				"protocolVersion": "2024-11-05",
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
				return self._result(msg_id, {
					"content": [{"type": "text", "text":
						f"'arguments' was a string but not valid JSON: {exc}"}],
					"isError": True,
				})
		if tool_name != "webfetch_call":
			return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
		if not isinstance(arguments, dict):
			return self._result(msg_id, {
				"content": [{"type": "text", "text":
					f"'arguments' must be an object; got {type(arguments).__name__}."}],
				"isError": True,
			})
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
	                    help="Output mode for --test (markdown/html/text). Default: markdown.")
	parser.add_argument("--max-chars", type=int, default=5000,
	                    help="Max chars for --test output. Default: 5000.")
	parser.add_argument("--log-file", default="",
	                    help="Log to file instead of stderr.")
	parser.add_argument("-v", "--verbose", action="store_true")
	args = parser.parse_args()
	level = logging.DEBUG if (args.verbose or args.log_file) else logging.WARNING
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
		result = handle_fetch(
			{"url": args.test, "output": args.output, "max_chars": args.max_chars},
			os.path.realpath(args.project_root),
		)
		print(result.get("__raw_text__") or result.get("error", "no output"))
		return

	asyncio.run(McpServer(args.project_root).run())


if __name__ == "__main__":
	main()
