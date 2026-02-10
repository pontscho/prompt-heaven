#!/usr/bin/env python3
"""
DuckDuckGo search script using only Python standard library.
Usage:
  python3 search_duckduckgo.py "search phrase"
  python3 search_duckduckgo.py "query1" "query2" "query3"  # batch mode
"""

import sys
import urllib.request
import urllib.parse
import re
import html


def clean_html_tags(text):
    """Remove HTML tags and decode entities from text."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities
    text = html.unescape(text)
    return text.strip()


def decode_duckduckgo_url(ddg_url):
    """Extract actual URL from DuckDuckGo redirect link."""
    # DuckDuckGo uses URLs like: //duckduckgo.com/l/?uddg=<encoded_url>&rut=...
    match = re.search(r'uddg=([^&]+)', ddg_url)
    if match:
        encoded_url = match.group(1)
        return urllib.parse.unquote(encoded_url)
    return ddg_url


def search_duckduckgo(query):
    """
    Search DuckDuckGo and return results.

    Args:
        query: Search query string

    Returns:
        List of dicts with keys: title, url, snippet
    """
    # Use DuckDuckGo HTML version (no JavaScript required)
    base_url = "https://html.duckduckgo.com/html/"
    params = urllib.parse.urlencode({'q': query})
    url = f"{base_url}?{params}"

    # Set user agent to avoid being blocked
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode('utf-8')

        # Extract result blocks
        # Pattern: <div class="result results_links ..."> ... </div>
        result_blocks = re.findall(
            r'<div class="result results_links[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html_content,
            re.DOTALL
        )

        results = []
        for block in result_blocks:
            result = {}

            # Extract title
            title_match = re.search(
                r'<a[^>]*class="result__a"[^>]*>([^<]+)</a>',
                block
            )
            if title_match:
                result['title'] = clean_html_tags(title_match.group(1))

            # Extract URL (from result__a link)
            url_match = re.search(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"',
                block
            )
            if url_match:
                ddg_url = url_match.group(1)
                result['url'] = decode_duckduckgo_url(ddg_url)

            # Extract snippet
            snippet_match = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                block,
                re.DOTALL
            )
            if snippet_match:
                result['snippet'] = clean_html_tags(snippet_match.group(1))

            # Only add results that have at least a title and URL
            if 'title' in result and 'url' in result:
                if 'snippet' not in result:
                    result['snippet'] = 'No snippet available'
                results.append(result)

        return results

    except Exception as e:
        print(f"Error searching DuckDuckGo: {e}", file=sys.stderr)
        return []


def format_results(results, query=None):
    """Format search results for output.

    Args:
        results: List of result dicts
        query: Optional query string for section header
    """
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
        output.append("")  # Empty line between results

    return '\n'.join(output)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 search_duckduckgo.py \"search phrase\" [\"query2\" \"query3\" ...]", file=sys.stderr)
        sys.exit(1)

    # Batch mode: each argument is a separate query
    queries = sys.argv[1:]

    # If only one argument, treat as single query
    if len(queries) == 1:
        results = search_duckduckgo(queries[0])
        if not results:
            print("No results found or error occurred.", file=sys.stderr)
            sys.exit(1)
        print(format_results(results))
    else:
        # Batch mode: process multiple queries
        output_sections = []
        has_results = False

        for query in queries:
            results = search_duckduckgo(query)
            if results:
                has_results = True
                output_sections.append(format_results(results, query=query))
            else:
                # Include failed query in output
                output_sections.append(f"## Query: {query}\n\nNo results found.\n")

        if not has_results:
            print("No results found for any query.", file=sys.stderr)
            sys.exit(1)

        # Print markdown document with all results
        print("# DuckDuckGo Search Results\n")
        print('\n---\n\n'.join(output_sections))


if __name__ == '__main__':
    main()
