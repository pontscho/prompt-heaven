#!/usr/bin/env python3
"""
Web search script with multi-backend support:
  1. DDG Lite  — DuckDuckGo lite endpoint via curl_cffi (default, may CAPTCHA)
  2. Bing      — auto-fallback when DDG CAPTCHAs, always works with curl_cffi
  3. CDP       — opt-in, uses real Chrome browser via DevTools Protocol

Usage:
  python3 search_duckduckgo.py "search phrase"
  python3 search_duckduckgo.py "query1" "query2" "query3"  # batch mode

Environment:
  DDG_BACKEND     — force backend: "bing", "cdp", or "ddg" (default: ddg with bing fallback)
  CHROME_CDP_URL  — Chrome debug endpoint for CDP backend (default: http://localhost:9222)

Platform-aware backend selection:
  - Linux: primp with impersonate="chrome" (a bare alias — primp picks and
    rotates the Chrome major itself) + impersonate_os="linux". primp 1.3.1
    auto-injects the browser headers, so none are supplied by hand.
  - macOS / Windows / other: curl_cffi (chrome146 etc.) — auto-generates all
    browser headers when impersonate= is set. Do NOT override User-Agent,
    Sec-CH-UA, Sec-Fetch-*, Accept, Accept-Encoding on curl_cffi sessions
    or the fingerprint breaks. Only Accept-Language needs manual setting.
"""
import sys
import re
import html as html_mod
import json
import platform
import random
import time
import os
import base64
from urllib.parse import parse_qs, urlparse

from lxml.html import document_fromstring

# ---------------------------------------------------------------------------
# Impersonation profiles — platform-aware backend selection
# Linux: primp with impersonate_os="linux" (curl_cffi has no OS knob and its
#        chrome profile claims macOS, which contradicts a Linux host's real
#        network stack under passive OS fingerprinting — see docs/spec-ddg.md §2.7)
# macOS / Windows / other: curl_cffi
# ---------------------------------------------------------------------------

# These four are VALID in curl_cffi 0.16.0 (verified 2026-08-04) and are the
# configuration the ~80% DDG pass-through was measured with, so they stay. A pin
# is tolerable here because curl_cffi rots LOUDLY: an unknown name raises
# ImpersonateError at request time. The bare aliases ("chrome", "safari") are
# the maintenance-free alternative if this list is ever re-measured.
CURL_CFFI_PROFILES = [
	"chrome146",
	"chrome145",
	"chrome136",
	"safari260",
]

# primp is the opposite: it rots SILENTLY. An unknown impersonate name prints
# one line to stderr and substitutes a RANDOM browser. This file pinned
# chrome_133/131/130/128 until 2026-08-04, and primp 1.3.1 had dropped every
# name below chrome_144 — so the Linux path had been putting an arbitrary
# fingerprint on the wire (a macOS Safari 26.3 UA in one measured run) while a
# hand-built dict announced Linux Chrome 133. Only aliases are accepted: primp
# offers no way to enumerate its valid names, and its Client.impersonate property
# merely echoes the input back — measured, it reports a name that does not exist
# while impersonating something else entirely.
PRIMP_ALIASES = frozenset({"chrome", "firefox", "edge", "safari", "opera", "random"})

# The hand-built _linux_chrome_headers() dict that used to live here is GONE, and
# docs/spec-ddg.md still refers to it by name. Measured against primp 1.3.1 on
# 2026-08-04: primp auto-injects all 13 of those keys, 11 with character-identical
# values, INCLUDING the two the spec records as load-bearing for DDG
# (upgrade-insecure-requests: 1 and sec-fetch-user: ?1). Client-level headers=
# loses every conflict against impersonate=, so re-measured with sentinel values
# both of those overrides were ignored outright — the dict was already inert.
# One difference shows up on the wire, and it is NOT ours to fix: primp stages
# `accept-encoding: gzip, deflate, br, zstd` — character-identical to the old
# dict — and then its transport rewrites the outgoing value to `gzip, br` to match
# what it can actually decode. No header we set can change that. Note also that
# header ORDER is itself a fingerprint and an echo endpoint cannot reveal it: if
# DDG throughput regresses, order or that rewrite is the suspect, not a missing key.

ROTATE_EVERY = 4


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def clean_html_tags(text):
	text = re.sub(r'<[^>]+>', '', text)
	text = html_mod.unescape(text)
	return text.strip()


def _normalize(text):
	return re.sub(r'\s+', ' ', text).strip() if text else ""


# ---------------------------------------------------------------------------
# DDG Lite parsing
# ---------------------------------------------------------------------------

def decode_duckduckgo_url(ddg_url):
	match = re.search(r'uddg=([^&]+)', ddg_url)
	if match:
		from urllib.parse import unquote
		return unquote(match.group(1))
	if ddg_url.startswith('http'):
		return ddg_url
	if ddg_url.startswith('//'):
		return 'https:' + ddg_url
	return ddg_url


def parse_lite_results(html_content):
	results = []
	rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL)

	current = {}
	for row in rows:
		link_url = None
		link_title = None

		m = re.search(
			r"""<a[^>]*class=['"]result-link['"][^>]*href=['"]([^'"]+)['"][^>]*>(.*?)</a>""",
			row, re.DOTALL,
		)
		if m:
			link_url, link_title = m.group(1), m.group(2)
		else:
			m = re.search(
				r"""<a[^>]*href=['"]([^'"]+)['"][^>]*class=['"]result-link['"][^>]*>(.*?)</a>""",
				row, re.DOTALL,
			)
			if m:
				link_url, link_title = m.group(1), m.group(2)

		if link_url:
			if current.get('title') and current.get('url'):
				if 'snippet' not in current:
					current['snippet'] = 'No snippet available'
				results.append(current)
			current = {
				'url': decode_duckduckgo_url(link_url),
				'title': clean_html_tags(link_title),
			}
			continue

		snippet_match = re.search(
			r"""<td[^>]*class=['"](result-snippet)['"][^>]*>(.*?)</td>""",
			row, re.DOTALL,
		)
		if snippet_match and current.get('title'):
			current['snippet'] = clean_html_tags(snippet_match.group(2))
			results.append(current)
			current = {}

	if current.get('title') and current.get('url'):
		if 'snippet' not in current:
			current['snippet'] = 'No snippet available'
		results.append(current)

	return results


# ---------------------------------------------------------------------------
# Bing parsing
# ---------------------------------------------------------------------------

def _decode_bing_url(href):
	"""Decode Bing's base64-wrapped redirect URLs."""
	if not href or not href.startswith("https://www.bing.com/ck/a"):
		return href
	try:
		u_param = parse_qs(urlparse(href).query).get("u", [""])[0]
		if u_param and len(u_param) > 2:
			b = u_param[2:]
			return base64.urlsafe_b64decode(b + "=" * ((-len(b)) % 4)).decode()
	except Exception:
		pass
	return href


def parse_bing_results(html_text):
	"""Parse Bing search results using lxml (same approach as deedy5/ddgs)."""
	results = []
	try:
		tree = document_fromstring(html_text)
	except Exception:
		return results

	elements = tree.xpath("//li[contains(@class, 'b_algo')]")
	if not isinstance(elements, list):
		return results

	for e in elements:
		hrefxpath = e.xpath("./h2/a/@href | ./div[contains(@class, 'header')]/a/@href")
		href = str(hrefxpath[0]) if hrefxpath and isinstance(hrefxpath, list) else None
		if not href:
			continue

		href = _decode_bing_url(href)
		titlexpath = e.xpath("./h2/a//text() | ./div[contains(@class, 'header')]/a/h2//text()")
		title = _normalize("".join(str(x) for x in titlexpath)) if titlexpath else ""
		bodyxpath = e.xpath(".//p//text()")
		snippet = _normalize("".join(str(x) for x in bodyxpath)).replace("\xa0", " ") if bodyxpath else ""

		results.append({
			'url': href,
			'title': title,
			'snippet': snippet or 'No snippet available',
		})

	return results


# ---------------------------------------------------------------------------
# Session management (platform-aware backend)
# ---------------------------------------------------------------------------

def create_session(imp=None):
	if platform.system() == "Linux":
		import primp
		# "chrome" rotates the Chrome major on its own across whatever majors the
		# installed primp supports (measured: 145/146/147/148 across fresh
		# clients), which is exactly what the pinned bundle list was reaching for.
		profile = imp if imp in PRIMP_ALIASES else "chrome"
		session = primp.Client(
			impersonate=profile,
			impersonate_os="linux",
			timeout=20,
		)
		return session, profile

	from curl_cffi import requests
	if imp is None:
		imp = random.choice(CURL_CFFI_PROFILES)
	session = requests.Session(impersonate=imp)
	session.headers["Accept-Language"] = "en-US,en;q=0.9"
	return session, imp


def warmup_session(session, endpoint="ddg"):
	try:
		if endpoint == "bing":
			session.get("https://www.bing.com/", timeout=10)
		else:
			session.get("https://lite.duckduckgo.com/lite/", timeout=10)
	except Exception:
		pass
	time.sleep(random.uniform(0.8, 1.5))


# ---------------------------------------------------------------------------
# DDG search
# ---------------------------------------------------------------------------

def search_ddg(query, session):
	try:
		resp = session.post(
			"https://lite.duckduckgo.com/lite/",
			data={"q": query, "kl": ""},
			headers={
				# Mimic real Chrome AJAX (fetch) POST from the lite page.
				# CDP Network.requestWillBeSentExtraInfo on 2026-05-24 showed Chrome
				# sends cors/empty/same-origin + Accept */* + Priority u=1 for XHR.
				# Chrome's request went through (status 200) where curl_cffi/primp
				# with navigation pattern (navigate/document) hit CAPTCHA.
				"Accept": "*/*",
				"Referer": "https://lite.duckduckgo.com/",
				"Sec-Fetch-Site": "same-origin",
				"Sec-Fetch-Mode": "cors",
				"Sec-Fetch-Dest": "empty",
				"Priority": "u=1, i",
			},
			timeout=15,
		)
		if "anomaly-modal" in resp.text or "Please complete the following" in resp.text:
			return None  # CAPTCHA
		return parse_lite_results(resp.text)
	except Exception as e:
		print(f"  [DDG error: {e}]", file=sys.stderr)
		return []


# ---------------------------------------------------------------------------
# Bing search
# ---------------------------------------------------------------------------

def search_bing(query, session):
	try:
		resp = session.get(
			"https://www.bing.com/search",
			params={"q": query},
			timeout=15,
		)
		if resp.status_code != 200:
			print(f"  [Bing HTTP {resp.status_code} for: {query}]", file=sys.stderr)
			return []
		return parse_bing_results(resp.text)
	except Exception as e:
		print(f"  [Bing error: {e}]", file=sys.stderr)
		return []


# ---------------------------------------------------------------------------
# CDP backend (opt-in via DDG_BACKEND=cdp)
# ---------------------------------------------------------------------------

def _discover_chrome():
	import urllib.request
	import urllib.error
	candidates = [os.environ.get("CHROME_CDP_URL", "")]
	candidates += [
		"http://192.168.2.2:9222",
		"http://localhost:9222",
		"http://127.0.0.1:9222",
		"http://localhost:9229",
	]
	for base in candidates:
		base = base.rstrip("/")
		if not base:
			continue
		try:
			resp = urllib.request.urlopen(f"{base}/json", timeout=2)
			targets = json.loads(resp.read())
			for t in targets:
				if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
					url = t.get("url", "")
					if url.startswith("devtools://") or url.startswith("chrome://"):
						continue
					return base, t["webSocketDebuggerUrl"]
		except Exception:
			continue
	return None


class CDPSearcher:
	def __init__(self, ws_url):
		import websocket
		self.ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=30)
		self._id = 1
		self._warm = False

	def _send(self, method, params=None):
		msg = {"id": self._id, "method": method, "params": params or {}}
		self.ws.send(json.dumps(msg))
		while True:
			result = json.loads(self.ws.recv())
			if result.get("id") == self._id:
				self._id += 1
				return result

	def search(self, query):
		js = """
		(async () => {
			const fd = new URLSearchParams();
			fd.append('q', %s);
			fd.append('kl', '');
			const r = await fetch('https://lite.duckduckgo.com/lite/', {
				method: 'POST',
				body: fd,
				headers: {'Content-Type': 'application/x-www-form-urlencoded'}
			});
			return await r.text();
		})()
		""" % json.dumps(query)

		result = self._send("Runtime.evaluate", {
			"expression": js,
			"awaitPromise": True,
			"returnByValue": True,
		})
		value = result.get("result", {}).get("result", {}).get("value", "")
		if result.get("result", {}).get("exceptionDetails"):
			print(f"  [CDP JS error for: {query}]", file=sys.stderr)
			return []
		if "anomaly" in value or "Please complete" in value:
			print(f"  [CAPTCHA via CDP for: {query}]", file=sys.stderr)
			return []
		return parse_lite_results(value)

	def close(self):
		try:
			self.ws.close()
		except Exception:
			pass


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_results(results, query=None):
	output = []
	if query:
		output.append(f"## Query: {query}")
		output.append("")
	for i, result in enumerate(results, 1):
		title = result.get('title', 'No title')
		url = result.get('url', 'No URL')
		snippet = result.get('snippet', 'No snippet available')
		output.append(f"### Result {i}: {title}")
		output.append(f"**URL**: {url}")
		output.append(f"**Snippet**: {snippet}")
		output.append("")
	return '\n'.join(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_cdp(queries):
	chrome = _discover_chrome()
	if not chrome:
		print("  [CDP requested but no Chrome found, aborting]", file=sys.stderr)
		sys.exit(1)
	_, ws_url = chrome
	cdp = CDPSearcher(ws_url)
	print("  [Using CDP/Chrome backend]", file=sys.stderr)

	output_sections = []
	has_results = False
	for i, query in enumerate(queries):
		if i > 0:
			time.sleep(random.uniform(0.8, 1.5))
		results = cdp.search(query)
		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")
	cdp.close()
	return output_sections, has_results


def _run_bing(queries, session):
	"""Pure Bing backend."""
	output_sections = []
	has_results = False
	for i, query in enumerate(queries):
		if i > 0:
			time.sleep(random.uniform(1.5, 3.0))
		if i > 0 and i % ROTATE_EVERY == 0:
			session, _imp = create_session()
		results = search_bing(query, session)
		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")
	return output_sections, has_results


def _run_ddg_with_bing_fallback(queries):
	"""Try DDG lite first; on CAPTCHA switch to Bing for remaining queries."""
	session, _imp = create_session()
	warmup_session(session, "ddg")

	output_sections = []
	has_results = False
	using_bing = False

	for i, query in enumerate(queries):
		if i > 0:
			delay = random.uniform(1.5, 3.0) if using_bing else random.uniform(2.5, 5.0)
			time.sleep(delay)

		if i > 0 and i % ROTATE_EVERY == 0:
			session, _imp = create_session()
			if not using_bing:
				warmup_session(session, "ddg")

		if using_bing:
			results = search_bing(query, session)
		else:
			results = search_ddg(query, session)
			if results is None:
				# CAPTCHA — switch to Bing for this and all remaining queries
				print(f"  [DDG CAPTCHA on: {query} — switching to Bing fallback]", file=sys.stderr)
				using_bing = True
				session, _imp = create_session()
				warmup_session(session, "bing")
				results = search_bing(query, session)

		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")

	return output_sections, has_results


def main():
	if len(sys.argv) < 2:
		print("Usage: python3 search_duckduckgo.py \"search phrase\" [\"query2\" ...]", file=sys.stderr)
		sys.exit(1)

	queries = sys.argv[1:]
	forced = os.environ.get("DDG_BACKEND", "").lower()

	if forced == "cdp":
		output_sections, has_results = _run_cdp(queries)
	elif forced == "bing":
		session, _imp = create_session()
		warmup_session(session, "bing")
		print("  [Using Bing backend]", file=sys.stderr)
		output_sections, has_results = _run_bing(queries, session)
	else:
		output_sections, has_results = _run_ddg_with_bing_fallback(queries)

	if not has_results:
		print("No results found for any query.", file=sys.stderr)
		sys.exit(1)

	if len(queries) > 1:
		print("# DuckDuckGo Search Results\n")
		print('\n---\n\n'.join(output_sections))
	else:
		print(output_sections[0] if output_sections else "")


if __name__ == '__main__':
	main()
