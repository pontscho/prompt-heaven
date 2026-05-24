#!/usr/bin/env python3
"""
GitHub code search via grep.app API with browser impersonation.

Usage:
  python3 search_github.py "search query"
  python3 search_github.py "query1" "query2" "query3"  # batch mode
  python3 search_github.py "useEffect" --lang JavaScript --repo facebook/react

Options:
  --lang   Programming language filter
  --repo   Repository filter (owner/repo format)
  --path   Path filter for directory-specific searches
  --limit  Maximum results per query (default: 10)

Platform-aware backend selection:
  - Linux: primp (chrome_133 + impersonate_os="linux") with a manual Linux
    Chrome 133 header dict. primp 0.15.0 does not auto-inject browser
    headers over HTTP/2, so we supply them ourselves.
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
import argparse
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Impersonation profiles — platform-aware backend selection
# Linux: primp + manual Linux Chrome 133 header dict (primp does not auto-inject)
# macOS / Windows / other: curl_cffi (auto-injects everything via impersonate=)
# ---------------------------------------------------------------------------

CURL_CFFI_PROFILES = [
	"chrome146",
	"chrome145",
	"chrome136",
	"safari260",
]

def _linux_chrome_headers(major):
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


PRIMP_LINUX_BUNDLES = [
	("chrome_133", 133),
	("chrome_131", 131),
	("chrome_130", 130),
	("chrome_128", 128),
]

ROTATE_EVERY = 4


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def clean_html_tags(text):
	text = re.sub(r'<[^>]+>', '', text)
	text = html_mod.unescape(text)
	return text.strip()


# ---------------------------------------------------------------------------
# grep.app response parsing
# ---------------------------------------------------------------------------

EXT_TO_LANG = {
	'.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
	'.tsx': 'TypeScript', '.jsx': 'JavaScript', '.java': 'Java',
	'.cpp': 'C++', '.cc': 'C++', '.c': 'C', '.h': 'C/C++',
	'.hpp': 'C++', '.cs': 'C#', '.go': 'Go', '.rs': 'Rust',
	'.rb': 'Ruby', '.php': 'PHP', '.swift': 'Swift', '.kt': 'Kotlin',
	'.scala': 'Scala', '.sh': 'Shell', '.bash': 'Bash',
	'.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS',
	'.json': 'JSON', '.xml': 'XML', '.yaml': 'YAML', '.yml': 'YAML',
	'.md': 'Markdown', '.sql': 'SQL', '.r': 'R',
	'.m': 'Objective-C', '.vim': 'Vim Script', '.lua': 'Lua', '.pl': 'Perl',
}


def detect_language(file_path):
	ext = os.path.splitext(file_path)[1].lower()
	return EXT_TO_LANG.get(ext)


def extract_code_from_snippet(html_snippet):
	lines = []
	for m in re.finditer(r'<tr data-line="(\d+)">.*?<pre>(.*?)</pre>', html_snippet, re.DOTALL):
		code_text = clean_html_tags(m.group(2)).rstrip()
		if code_text:
			lines.append((int(m.group(1)), code_text))
	return lines


def build_github_url(repo, path, branch, line_number=None):
	base = f"https://github.com/{repo}/blob/{branch}/{path}"
	if line_number:
		return f"{base}#L{line_number}"
	return base


def parse_grep_results(data, limit):
	results = []
	for hit in data.get('hits', {}).get('hits', [])[:limit]:
		file_path = hit.get('path', 'Unknown')
		result = {
			'repo': hit.get('repo', 'Unknown'),
			'file_path': file_path,
			'branch': hit.get('branch', 'main'),
			'language': detect_language(file_path) or 'Unknown',
			'code_lines': [],
		}

		snippet_html = hit.get('content', {}).get('snippet', '')
		if snippet_html:
			result['code_lines'] = extract_code_from_snippet(snippet_html)

		first_line = result['code_lines'][0][0] if result['code_lines'] else None
		result['url'] = build_github_url(
			result['repo'], result['file_path'], result['branch'], first_line
		)
		results.append(result)

	return results


# ---------------------------------------------------------------------------
# Session management (platform-aware backend)
# ---------------------------------------------------------------------------

def create_session(imp=None):
	if platform.system() == "Linux":
		import primp
		if imp is None:
			profile, major = random.choice(PRIMP_LINUX_BUNDLES)
		else:
			match = next((b for b in PRIMP_LINUX_BUNDLES if b[0] == imp), None)
			profile, major = match if match else (imp, 133)
		session = primp.Client(
			impersonate=profile,
			impersonate_os="linux",
			headers=_linux_chrome_headers(major),
			timeout=20,
		)
		return session, profile

	from curl_cffi import requests
	if imp is None:
		imp = random.choice(CURL_CFFI_PROFILES)
	session = requests.Session(impersonate=imp)
	session.headers["Accept-Language"] = "en-US,en;q=0.9"
	return session, imp


def warmup_session(session):
	try:
		session.get("https://grep.app/", timeout=10)
	except Exception:
		pass
	time.sleep(random.uniform(0.8, 1.5))


# ---------------------------------------------------------------------------
# GitHub search via grep.app
# ---------------------------------------------------------------------------

def search_github(query, session, lang=None, repo=None, path=None, limit=10):
	params = {'q': query}
	if lang:
		params['f.lang'] = lang
	if repo:
		params['f.repo'] = repo
	if path:
		params['f.path'] = path

	url = f"https://grep.app/api/search?{urlencode(params)}"

	try:
		resp = session.get(
			url,
			headers={
				"Accept": "application/json, text/plain, */*",
				"Referer": "https://grep.app/search",
				"Sec-Fetch-Site": "same-origin",
				"Sec-Fetch-Mode": "cors",
				"Sec-Fetch-Dest": "empty",
				"Priority": "u=1, i",
			},
			timeout=15,
		)
		if resp.status_code == 429:
			print(f"  [Rate limited for: {query}]", file=sys.stderr)
			return []
		if resp.status_code != 200:
			print(f"  [HTTP {resp.status_code} for: {query}]", file=sys.stderr)
			return []
		data = json.loads(resp.text)
		return parse_grep_results(data, limit)
	except Exception as e:
		print(f"  [grep.app error: {e}]", file=sys.stderr)
		return []


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_results(results, query=None):
	if not results:
		return "No results found."

	output = []
	if query:
		output.append(f"## Query: {query}")
		output.append("")

	for i, result in enumerate(results, 1):
		output.append(f"### Result {i}: {result['repo']} - {result['file_path']}")
		output.append(f"**URL**: {result['url']}")
		output.append(f"**Branch**: {result['branch']}")
		output.append(f"**Language**: {result['language']}")

		if result['code_lines']:
			first_line = result['code_lines'][0][0]
			last_line = result['code_lines'][-1][0]
			output.append(f"**Line {first_line}-{last_line}:**")
			output.append("```")
			for _num, code in result['code_lines']:
				output.append(code)
			output.append("```")
		else:
			output.append("No code snippet available")

		output.append("")

	return '\n'.join(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_github(queries, lang=None, repo=None, path=None, limit=10):
	session, _imp = create_session()
	warmup_session(session)

	output_sections = []
	has_results = False

	for i, query in enumerate(queries):
		if i > 0:
			time.sleep(random.uniform(1.5, 3.0))

		if i > 0 and i % ROTATE_EVERY == 0:
			session, _imp = create_session()
			warmup_session(session)

		results = search_github(query, session, lang=lang, repo=repo, path=path, limit=limit)

		if results:
			has_results = True
			section = format_results(results, query=query) if len(queries) > 1 else format_results(results)
			output_sections.append(section)
		elif len(queries) > 1:
			output_sections.append(f"## Query: {query}\n\nNo results found.\n")

	return output_sections, has_results


def main():
	parser = argparse.ArgumentParser(
		description='Search GitHub code via grep.app API',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog='''
Examples:
  %(prog)s "async function"
  %(prog)s "machine learning" --lang Python
  %(prog)s "useEffect" --repo facebook/react
  %(prog)s "import torch" --path models/
  %(prog)s "query1" "query2" "query3"
		'''
	)
	parser.add_argument('query', nargs='+', help='Search query string(s)')
	parser.add_argument('--lang', help='Programming language filter')
	parser.add_argument('--repo', help='Repository filter (owner/repo)')
	parser.add_argument('--path', help='Path filter')
	parser.add_argument('--limit', type=int, default=10, help='Max results per query (default: 10)')
	args = parser.parse_args()

	output_sections, has_results = _run_github(
		args.query, lang=args.lang, repo=args.repo, path=args.path, limit=args.limit
	)

	if not has_results:
		print("No results found for any query.", file=sys.stderr)
		sys.exit(1)

	if len(args.query) > 1:
		print("# GitHub Search Results\n")
		print('\n---\n\n'.join(output_sections))
	else:
		print(output_sections[0] if output_sections else "")


if __name__ == '__main__':
	main()
