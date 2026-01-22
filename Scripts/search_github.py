#!/usr/bin/env python3
"""
GitHub code search using grep.app API with Python standard library only.
Usage: python3 search_github.py "search query" [options]
"""

import sys
import urllib.request
import urllib.parse
import json
import argparse
import re
import html as html_module
import os


def detect_language_from_path(file_path):
    """
    Detect programming language from file extension.

    Args:
        file_path: File path string

    Returns:
        Language name or None
    """
    ext_to_lang = {
        '.py': 'Python',
        '.js': 'JavaScript',
        '.ts': 'TypeScript',
        '.tsx': 'TypeScript',
        '.jsx': 'JavaScript',
        '.java': 'Java',
        '.cpp': 'C++',
        '.cc': 'C++',
        '.c': 'C',
        '.h': 'C/C++',
        '.hpp': 'C++',
        '.cs': 'C#',
        '.go': 'Go',
        '.rs': 'Rust',
        '.rb': 'Ruby',
        '.php': 'PHP',
        '.swift': 'Swift',
        '.kt': 'Kotlin',
        '.scala': 'Scala',
        '.sh': 'Shell',
        '.bash': 'Bash',
        '.html': 'HTML',
        '.css': 'CSS',
        '.scss': 'SCSS',
        '.json': 'JSON',
        '.xml': 'XML',
        '.yaml': 'YAML',
        '.yml': 'YAML',
        '.md': 'Markdown',
        '.sql': 'SQL',
        '.r': 'R',
        '.m': 'Objective-C',
        '.vim': 'Vim Script',
        '.lua': 'Lua',
        '.pl': 'Perl',
    }

    ext = os.path.splitext(file_path)[1].lower()
    return ext_to_lang.get(ext, None)


def search_github(query, lang=None, repo=None, path=None, limit=10):
    """
    Search GitHub using grep.app API.

    Args:
        query: Search query string
        lang: Programming language filter (e.g., "Python", "JavaScript")
        repo: Repository filter in "owner/repo" format
        path: Path filter for directory-specific searches
        limit: Maximum number of results to return (default: 10)

    Returns:
        List of dicts with keys: repo, file_path, branch, language, code_lines, url
    """
    # Build API URL
    base_url = "https://grep.app/api/search"

    params = {'q': query}
    if lang:
        params['f.lang'] = lang
    if repo:
        params['f.repo'] = repo
    if path:
        params['f.path'] = path

    url = f"{base_url}?{urllib.parse.urlencode(params)}"

    # Set user agent
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Parse results
        results = []
        hits = data.get('hits', {}).get('hits', [])

        for hit in hits[:limit]:
            file_path = hit.get('path', 'Unknown')
            result = {
                'repo': hit.get('repo', 'Unknown'),
                'file_path': file_path,
                'branch': hit.get('branch', 'main'),
                'language': detect_language_from_path(file_path) or 'Unknown',
                'code_lines': []
            }

            # Extract code snippet
            snippet_html = hit.get('content', {}).get('snippet', '')
            if snippet_html:
                result['code_lines'] = extract_code_from_snippet(snippet_html)

            # Build GitHub URL
            if result['code_lines']:
                first_line = result['code_lines'][0][0]
                result['url'] = build_github_url(
                    result['repo'],
                    result['file_path'],
                    result['branch'],
                    first_line
                )
            else:
                result['url'] = build_github_url(
                    result['repo'],
                    result['file_path'],
                    result['branch']
                )

            results.append(result)

        return results

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("No results found.", file=sys.stderr)
        elif e.code == 429:
            print("Rate limit exceeded. Please try again later.", file=sys.stderr)
        else:
            print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        return []
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing API response: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error searching GitHub: {e}", file=sys.stderr)
        return []


def extract_code_from_snippet(html_snippet):
    """
    Extract code lines and line numbers from HTML snippet.

    Args:
        html_snippet: HTML string with code snippet

    Returns:
        List of (line_number, code_line) tuples
    """
    lines = []

    # Pattern: <tr data-line="123">...<pre>code content</pre>...
    # Extract each table row with line number and code
    pattern = r'<tr data-line="(\d+)">.*?<pre>(.*?)</pre>'
    matches = re.findall(pattern, html_snippet, re.DOTALL)

    for line_num, code_html in matches:
        # Clean HTML tags (remove <span>, <mark>, etc.)
        code_text = re.sub(r'<[^>]+>', '', code_html)
        # Decode HTML entities
        code_text = html_module.unescape(code_text)
        # Strip whitespace but keep leading indentation
        code_text = code_text.rstrip()

        if code_text:  # Only add non-empty lines
            lines.append((int(line_num), code_text))

    return lines


def build_github_url(repo, path, branch, line_number=None):
    """
    Build direct GitHub file URL with optional line number.

    Args:
        repo: Repository name in "owner/repo" format
        path: File path in repository
        branch: Branch name
        line_number: Optional line number

    Returns:
        Full GitHub URL
    """
    base = f"https://github.com/{repo}/blob/{branch}/{path}"
    if line_number:
        return f"{base}#L{line_number}"
    return base


def format_results(results):
    """
    Format search results for output.

    Args:
        results: List of result dicts

    Returns:
        Formatted string for output
    """
    if not results:
        return "No results found."

    output = []

    for i, result in enumerate(results, 1):
        repo = result.get('repo', 'Unknown')
        file_path = result.get('file_path', 'Unknown')
        branch = result.get('branch', 'main')
        language = result.get('language', 'Unknown')
        code_lines = result.get('code_lines', [])
        url = result.get('url', '')

        # Result header
        output.append(f"# Result {i}: {repo} - {file_path}")
        output.append(f"URL: {url}")
        output.append(f"Branch: {branch}")
        output.append(f"Language: {language}")

        # Code snippet with line numbers
        if code_lines:
            first_line = code_lines[0][0]
            last_line = code_lines[-1][0]
            output.append(f"Line {first_line}-{last_line}:")

            for line_num, code in code_lines:
                output.append(f"    {code}")
        else:
            output.append("No code snippet available")

        # URL
        output.append("")  # Empty line between results

    return '\n'.join(output)


def parse_arguments():
    """
    Parse command line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description='Search GitHub code repositories using grep.app API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s "async function"
  %(prog)s "machine learning" --lang Python
  %(prog)s "useEffect" --repo facebook/react
  %(prog)s "import torch" --path models/
  %(prog)s "neural network" --lang Python --path src/ --limit 5
        '''
    )

    parser.add_argument(
        'query',
        help='Search query string'
    )

    parser.add_argument(
        '--lang',
        help='Programming language filter (e.g., Python, JavaScript, Go)'
    )

    parser.add_argument(
        '--repo',
        help='Repository filter in "owner/repo" format (e.g., facebook/react)'
    )

    parser.add_argument(
        '--path',
        help='Path filter for directory-specific searches (e.g., src/, models/)'
    )

    parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Maximum number of results to return (default: 10)'
    )

    return parser.parse_args()


def main():
    """Main function."""
    args = parse_arguments()

    # Perform search
    results = search_github(
        query=args.query,
        lang=args.lang,
        repo=args.repo,
        path=args.path,
        limit=args.limit
    )

    if not results:
        sys.exit(1)

    # Format and print results
    print(format_results(results))


if __name__ == '__main__':
    main()
