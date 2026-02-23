#!/usr/bin/env python3
"""
Context7 Documentation MCP Server — standalone, no external dependencies.

Design:
  tools/list  → exposes only 'context7_call' (minimal token footprint)
  tools/call  → dispatches all Context7 documentation tools

Usage:
  python3 mcp-context7.py [--api-key <key>] [--debug]
"""

import os
import sys
import json
import asyncio
import argparse
import urllib.request
import urllib.parse
import urllib.error
from typing import Any, Optional


# ============================================================
# Debug logging
# ============================================================

DEBUG = False


def debug_log(message: str) -> None:
    if DEBUG:
        print(f"[DEBUG] {message}", file=sys.stderr, flush=True)


# ============================================================
# Configuration
# ============================================================

CONTEXT7_API_BASE_URL = "https://context7.com/api"
API_KEY: Optional[str] = None


# ============================================================
# HTTP helper
# ============================================================

def _api_get_sync(path: str, params: dict, api_key: Optional[str] = None) -> str:
    """Blocking GET request to the Context7 API (run via executor)."""
    query_string = urllib.parse.urlencode(params)
    url = f"{CONTEXT7_API_BASE_URL}{path}?{query_string}"

    debug_log(f"GET {url}")

    headers = {"X-Context7-Source": "mcp-server"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8")


async def _api_get(path: str, params: dict, api_key: Optional[str] = None) -> str:
    """Async wrapper around _api_get_sync."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _api_get_sync, path, params, api_key)


# ============================================================
# Formatting helpers
# ============================================================

def _get_source_reputation_label(trust_score) -> str:
    if trust_score is None or trust_score < 0:
        return "Unknown"
    if trust_score >= 7:
        return "High"
    if trust_score >= 4:
        return "Medium"
    return "Low"


def _format_search_result(result: dict) -> str:
    lines = [
        f"- Title: {result.get('title', '')}",
        f"- Context7-compatible library ID: {result.get('id', '')}",
        f"- Description: {result.get('description', '')}",
    ]

    total_snippets = result.get("totalSnippets")
    if total_snippets is not None and total_snippets != -1:
        lines.append(f"- Code Snippets: {total_snippets}")

    reputation = _get_source_reputation_label(result.get("trustScore"))
    lines.append(f"- Source Reputation: {reputation}")

    benchmark_score = result.get("benchmarkScore")
    if benchmark_score is not None and benchmark_score > 0:
        lines.append(f"- Benchmark Score: {benchmark_score}")

    versions = result.get("versions")
    if versions and len(versions) > 0:
        lines.append(f"- Versions: {', '.join(versions)}")

    return "\n".join(lines)


def _format_search_results(results: list) -> str:
    if not results:
        return "No documentation libraries found matching your query."
    return "\n----------\n".join(_format_search_result(r) for r in results)


def _parse_error_response(status: int, body: str, api_key: Optional[str]) -> str:
    try:
        data = json.loads(body)
        if data.get("message"):
            return data["message"]
    except Exception:
        pass

    if status == 429:
        if api_key:
            return "Rate limited or quota exceeded. Upgrade your plan at https://context7.com/plans for higher limits."
        return "Rate limited or quota exceeded. Create a free API key at https://context7.com/dashboard for higher limits."
    if status == 404:
        return "The library you are trying to access does not exist. Please try with a different library ID."
    if status == 401:
        return "Invalid API key. Please check your API key. API keys should start with 'ctx7sk' prefix."
    return f"Request failed with status {status}. Please try again later."


# ============================================================
# Tool Handlers
# ============================================================

async def handle_context7_status(args: dict) -> str:
    has_key = API_KEY is not None
    auth_status = "authenticated (API key configured)" if has_key else "anonymous (no API key — rate limited)"
    return (
        f"Context7 MCP server is running.\n"
        f"API URL: {CONTEXT7_API_BASE_URL}\n"
        f"Auth: {auth_status}"
    )


async def handle_context7_resolve_library_id(args: dict) -> str:
    query = args.get("query", "").strip()
    library_name = args.get("library_name", "").strip()

    if not query:
        return "Error: 'query' parameter is required."
    if not library_name:
        return "Error: 'library_name' parameter is required."

    try:
        raw = await _api_get("/v2/libs/search", {"query": query, "libraryName": library_name}, API_KEY)
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return _parse_error_response(e.code, body, API_KEY)
    except Exception as e:
        return f"Error searching libraries: {e}"

    results = data.get("results", [])
    if not results:
        error = data.get("error")
        return error if error else "No libraries found matching the provided name."

    return f"Available Libraries:\n\n{_format_search_results(results)}"


async def handle_context7_query_docs(args: dict) -> str:
    library_id = args.get("library_id", "").strip()
    query = args.get("query", "").strip()

    if not library_id:
        return "Error: 'library_id' parameter is required."
    if not query:
        return "Error: 'query' parameter is required."

    try:
        text = await _api_get("/v2/context", {"query": query, "libraryId": library_id}, API_KEY)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return _parse_error_response(e.code, body, API_KEY)
    except Exception as e:
        return f"Error fetching library context. Please try again later. {e}"

    if not text or not text.strip():
        return (
            "Documentation not found or not finalized for this library. "
            "This might have happened because you used an invalid Context7-compatible library ID. "
            "To get a valid Context7-compatible library ID, use 'context7_resolve_library_id' "
            "with the package name you wish to retrieve documentation for."
        )

    return text


async def handle_context7_call(args: dict) -> str:
    """Dispatcher: call any Context7 tool by name."""
    function = args.get("function", "")
    params = args.get("params") or {}

    if not function:
        has_key = API_KEY is not None
        auth_status = "authenticated" if has_key else "anonymous (rate limited)"
        return f"Context7 MCP server is running. Auth: {auth_status}"

    if function == "context7_call":
        return "Cannot dispatch context7_call recursively."

    handler = ALL_HANDLERS.get(function)
    if handler is None:
        available = ", ".join(sorted(ALL_HANDLERS.keys()))
        return f"Unknown function: '{function}'. Available: {available}"

    return await handler(params)


# ============================================================
# MCP Tool Registry
# ============================================================

# Only one tool appears in tools/list (minimal token footprint).
# Called without 'function' → returns server status.
LISTED_TOOLS = [
    {
        "name": "context7_call",
        "description": (
            "Call any Context7 documentation function by name. "
            "Returns server status if called without 'function'. "
            "Invoke the context7-mcp skill for the full list of available functions and their parameters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "Function name (e.g. context7_resolve_library_id, context7_query_docs)",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the function (see context7-mcp skill for schema)",
                },
            },
            "required": [],
        },
    },
]

ALL_HANDLERS = {
    "context7_status":             handle_context7_status,
    "context7_call":               handle_context7_call,
    "context7_resolve_library_id": handle_context7_resolve_library_id,
    "context7_query_docs":         handle_context7_query_docs,
}


# ============================================================
# MCP Server — JSON-RPC 2.0 over stdio
# ============================================================

class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def _ok(self, msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _err(self, msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _tool_error(self, msg_id: Any, text: str) -> dict:
        """Return a tool-level error (visible in LLM context, per SEP-2140)."""
        return self._ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        debug_log(f"← {method} (id={msg_id})")

        # Notifications carry no id and require no response
        if msg_id is None:
            return None

        if method == "initialize":
            return self._ok(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-context7", "version": "1.0.0"},
            })

        if method == "ping":
            return self._ok(msg_id, {})

        if method == "tools/list":
            return self._ok(msg_id, {"tools": LISTED_TOOLS})

        if method == "tools/call":
            return await self._dispatch_tool(msg_id, msg.get("params", {}))

        return self._err(msg_id, -32601, f"Method not found: {method}")

    async def _dispatch_tool(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}

        handler = ALL_HANDLERS.get(name)
        if handler is None:
            return self._tool_error(
                msg_id,
                f"Unknown tool: '{name}'. Available: {', '.join(sorted(ALL_HANDLERS.keys()))}"
            )

        try:
            result = await handler(args)
            return self._ok(msg_id, {"content": [{"type": "text", "text": result}]})
        except Exception as e:
            debug_log(f"Handler '{name}' error: {e}")
            return self._tool_error(msg_id, f"Error in {name}: {e}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        debug_log("mcp-context7 server ready (stdio)")

        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                debug_log("stdin EOF — shutting down")
                break

            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                debug_log(f"JSON parse error: {e}")
                continue

            try:
                response = await self.handle_message(msg)
                if response is not None:
                    debug_log(f"→ id={response.get('id')}")
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                debug_log(f"Unhandled error: {e}")


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Context7 Documentation MCP Server — standalone, no external dependencies"
    )
    parser.add_argument("--api-key", help="API key for Context7 (or set CONTEXT7_API_KEY env var)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parsed = parser.parse_args()

    global DEBUG, API_KEY
    if parsed.debug:
        DEBUG = True
        debug_log("Debug logging enabled")

    # Priority: --api-key flag > CONTEXT7_API_KEY env var > anonymous
    API_KEY = parsed.api_key or os.environ.get("CONTEXT7_API_KEY")
    if API_KEY:
        debug_log("API key configured")
    else:
        debug_log("No API key — running in anonymous mode (rate limited)")

    server = McpServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        debug_log("Server stopped")


if __name__ == "__main__":
    main()
