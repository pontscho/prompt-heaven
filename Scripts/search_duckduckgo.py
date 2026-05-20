#!/usr/bin/env python3
"""
DuckDuckGo search script using curl_cffi for browser TLS fingerprint impersonation.
Usage:
  python3 search_duckduckgo.py "search phrase"
  python3 search_duckduckgo.py "query1" "query2" "query3"  # batch mode
"""

import sys
import re
import html
import random
import time

from curl_cffi import requests

BROWSER_IMPERSONATIONS = [
	"chrome120",
	"chrome124",
	"edge101",
	"safari17_0",
]

USER_AGENTS = [
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/101.0.1210.53",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def clean_html_tags(text):
	"""Remove HTML tags and decode entities from text."""
	text = re.sub(r'<[^>]+>', '', text)
	text = html.unescape(text)
	return text.strip()


def decode_duckduckgo_url(ddg_url):
	"""Extract actual URL from DuckDuckGo redirect link."""
	match = re.search(r'uddg=([^&]+)', ddg_url)
	if match:
		from urllib.parse import unquote
		return unquote(match.group(1))
	# Direct URL (lite endpoint gives these)
	if ddg_url.startswith('http'):
		return ddg_url
	if ddg_url.startswith('//'):
		return 'https:' + ddg_url
	return ddg_url


def search_duckduckgo(query, session):
	"""
	Search DuckDuckGo via the lite endpoint.

	Args:
		query: Search query string
		session: curl_cffi Session with browser impersonation

	Returns:
		List of dicts with keys: title, url, snippet
	"""
	url = "https://lite.duckduckgo.com/lite/"

	try:
		resp = session.post(url, data={"q": query, "kl": ""}, timeout=15)
		html_content = resp.text

		if "anomaly-modal" in html_content or "Please complete the following" in html_content:
			print(f"  [CAPTCHA hit for: {query}]", file=sys.stderr)
			return []

		return _parse_lite_results(html_content)

	except Exception as e:
		print(f"Error searching DuckDuckGo: {e}", file=sys.stderr)
		return []


def _parse_lite_results(html_content):
	"""Parse results from lite.duckduckgo.com (table-based layout)."""
	results = []

	rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL)

	current = {}
	for row in rows:
		# Title + URL: <a ... class='result-link' href='...'>Title</a>
		# DDG lite uses single quotes; handle both attr orderings
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

		# Snippet: <td class='result-snippet'>...</td>  (single quotes!)
		snippet_match = re.search(
			r"""<td[^>]*class=['"](result-snippet)['"][^>]*>(.*?)</td>""",
			row, re.DOTALL,
		)
		if snippet_match and current.get('title'):
			current['snippet'] = clean_html_tags(snippet_match.group(2))
			results.append(current)
			current = {}

	# Flush last result
	if current.get('title') and current.get('url'):
		if 'snippet' not in current:
			current['snippet'] = 'No snippet available'
		results.append(current)

	return results


def format_results(results, query=None):
	"""Format search results for output."""
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


def create_session():
	"""Create a curl_cffi session with random browser impersonation."""
	idx = random.randint(0, len(BROWSER_IMPERSONATIONS) - 1)
	session = requests.Session(impersonate=BROWSER_IMPERSONATIONS[idx])
	session.headers.update({
		"User-Agent": USER_AGENTS[idx],
		"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"Accept-Language": "en-US,en;q=0.9",
		"Referer": "https://duckduckgo.com/",
	})
	return session


def main():
	if len(sys.argv) < 2:
		print("Usage: python3 search_duckduckgo.py \"search phrase\" [\"query2\" \"query3\" ...]", file=sys.stderr)
		sys.exit(1)

	queries = sys.argv[1:]
	session = create_session()

	# Warm up — visit DDG homepage to get cookies
	try:
		session.get("https://duckduckgo.com/", timeout=10)
	except Exception:
		pass

	if len(queries) == 1:
		results = search_duckduckgo(queries[0], session)
		if not results:
			print("No results found or error occurred.", file=sys.stderr)
			sys.exit(1)
		print(format_results(results))
	else:
		output_sections = []
		has_results = False

		for i, query in enumerate(queries):
			if i > 0:
				delay = random.uniform(1.0, 3.0)
				time.sleep(delay)

			results = search_duckduckgo(query, session)
			if results:
				has_results = True
				output_sections.append(format_results(results, query=query))
			else:
				output_sections.append(f"## Query: {query}\n\nNo results found.\n")

		if not has_results:
			print("No results found for any query.", file=sys.stderr)
			sys.exit(1)

		print("# DuckDuckGo Search Results\n")
		print('\n---\n\n'.join(output_sections))


if __name__ == '__main__':
	main()
