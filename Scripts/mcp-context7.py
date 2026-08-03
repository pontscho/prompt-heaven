#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Context7 Documentation MCP Server — standalone, no external dependencies.

Design:
  tools/list  → exposes only 'context7_call' (minimal token footprint)
  tools/call  → dispatches all Context7 documentation tools

Output ceiling (cap convention v1): replies are capped by max_answer_chars
(default 24000 chars, ~6k tokens). This server hands an UPSTREAM payload through,
so its size is decided by context7.com and not by anything here — which is
exactly why the ceiling matters most on this one. The library list is row-shaped
and drops whole RECORDS with an 'offset' resume hint; a documentation answer is
one opaque blob with no stable index, so it is cut on a line boundary, head-first
(upstream ranks by relevance to the query), and gets NO resume hint, because
there is nothing to resume from.

Usage:
  python3 mcp-context7.py [--api-key <key>] [--debug]
"""

import os
import re
import sys
import json
import logging
import asyncio
import argparse
import urllib.request
import urllib.parse
import urllib.error
from typing import Any, Optional


# ============================================================
# Logging
# ============================================================

log = logging.getLogger("mcp-context7")


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


# ============================================================
# Configuration
# ============================================================

CONTEXT7_API_BASE_URL = "https://context7.com/api"
API_KEY: Optional[str] = None


# ============================================================
# Output ceiling (cap convention v1)
# ============================================================

# 24000 chars is ~6k tokens at the usual ~4 chars/token — a reply one call may
# spend, not a reply that eats the session.
#
# Until now this server had NO ceiling of any kind, so the only backstop was the
# Claude Code harness: it generates the whole oversized payload, spills it to a
# file and costs an extra round trip to read it back. One uncapped call in this
# fleet has already produced 511617 chars that way. This server is the fleet's
# worst exposure, because the payload is UPSTREAM text: a /v2/context answer is
# as large as context7.com decides to make it, and no amount of care on this side
# bounds it.
#
# Per-call overridable via max_answer_chars, so a caller who genuinely wants the
# whole document asks for it explicitly. <= 0 means unlimited.
DEFAULT_MAX_ANSWER_CHARS = 24000

# Room kept free for the closing accounting line while filling a record budget.
PAGE_LINE_RESERVE = 80

# The separator between two search records. Named because the record pager has to
# charge it against the budget; the rendering is unchanged.
RESULT_SEPARATOR = "\n----------\n"


def _max_answer_chars(args: dict) -> int:
    """The per-call ceiling. <= 0 disables it — an explicit "give me all of it"."""
    try:
        return int(args.get("max_answer_chars", DEFAULT_MAX_ANSWER_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ANSWER_CHARS


def _offset(args: dict) -> int:
    """First search RECORD to display, 0-based — the value the page line hands back.

    Display-level paging over the record list this call already fetched, not an
    upstream cursor: the search is re-run on every call. Only
    context7_resolve_library_id reads it, because only its payload has records to
    index; context7_query_docs never emits an ``offset=`` hint, since a resume
    hint for a knob that does not exist lies to the caller.
    """
    try:
        return max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


def _rows_note(start: int, shown: int, total: int) -> str:
    """Record accounting for a row-shaped payload; goes on its LAST line.

    <total> is EXACT here: the search response is parsed into a complete list
    before any of it is rendered, so this server never has to guess a count for
    THIS payload. (It cannot know the size of a documentation answer in advance
    either, but that payload is not row-shaped and never reaches this line.)

    Display indices are 1-based inclusive, which makes the 1-based last record
    equal to the 0-based ``offset`` of the next one — so the hint is literally the
    value to pass back. ``offset=`` is emitted only when records really remain: a
    resume hint that would return nothing is worse than no hint.
    """
    last = start + shown
    if shown == 0:
        # Spelled out rather than as a 1-based range, which would invert
        # ("rows 100-99") when the caller offsets past the end.
        return (f"[no rows at offset {start} of {total}]" if start
                else f"[{total} rows]")
    if last < total:
        return (f"[showing rows {start + 1}-{last} of {total}; "
                f"offset={last} for more]")
    if start > 0:
        return f"[showing rows {start + 1}-{last} of {total}; no rows left]"
    return f"[{total} row{'s' if total != 1 else ''}]"


_FENCE_LINE_RE = re.compile(r"^(`{3,})", re.M)


def _balance_fences(body: str) -> str:
    """Close a fenced block the cut landed inside.

    This is the one server in the fleet that cannot inspect its payload before
    committing to it: a /v2/context answer is upstream MARKDOWN, and its bulk is
    fenced code snippets. Cutting such a document at an arbitrary line has a good
    chance of landing between an opening fence and its close, and a reply that
    stops mid-fence swallows the accounting line into what reads as code — the
    caller is then told nothing about the cut it can actually see.

    An odd number of fence lines means exactly one is missing. Only the "close at
    the bottom" direction exists here, because this server has no tail-biased
    payload — see _cap_text. (mcp-jenkins.py:603 carries both directions; if a
    tail bias is ever added here, take the other branch from there.)
    """
    fences = _FENCE_LINE_RE.findall(body)
    if len(fences) % 2 == 0:
        return body
    return "%s\n%s" % (body.rstrip("\n"), fences[-1])


def _cap_text(text: str, max_chars: int) -> str:
    """Cut to ``max_chars`` on a LINE BOUNDARY, head-biased, with ONE closing line.

    Head-biased, and there is no tail option: the informative end of every
    payload this server returns is the top — upstream ranks documentation
    snippets by relevance to the query, and a search record leads with its title
    and library ID. The closing line names the end it kept and is always the
    payload's last line, so the caller never has to guess which half survived.

    Cutting on a line boundary is what keeps a snippet line, an import path or a
    library ID from being served in half; the single exception is a payload with
    no newline to cut at (upstream minified into one line), where the boundary
    does not exist and a hard cut is the honest answer.

    For the search list this is the backstop, not the main path — records are
    dropped whole in _format_search_results() first.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    total = len(text)

    def marker(kept: int) -> str:
        return (f"\n[truncated: kept {kept} of {total} chars from the head; "
                f"raise max_answer_chars or narrow the query]")

    # marker(total) is the longest the line can ever get (kept <= total), so
    # reserving that much cannot overshoot once the real count is known. The
    # repair fence is reserved the same way: it can only ever be one of the fence
    # tokens already present, so the widest one plus its newline bounds it and the
    # whole reply stays inside the ceiling.
    fences = _FENCE_LINE_RE.findall(text)
    keep = max_chars - len(marker(total))
    if fences:
        keep -= max(len(f) for f in fences) + 1
    if keep <= 0:
        # The ceiling is smaller than the accounting line itself. The line still
        # wins: a payload with no accounting is worse than no payload.
        return marker(0).lstrip("\n")
    cut = text.rfind("\n", 0, keep + 1)
    body = text[:cut] if cut > 0 else text[:keep]
    # The accounting reports the payload chars that survived, so the repair fence
    # is added after the count is taken.
    return _balance_fences(body) + marker(len(body))


# ============================================================
# HTTP helper
# ============================================================

def _api_get_sync(path: str, params: dict, api_key: Optional[str] = None) -> str:
    """Blocking GET request to the Context7 API (run via executor)."""
    query_string = urllib.parse.urlencode(params)
    url = f"{CONTEXT7_API_BASE_URL}{path}?{query_string}"

    log.debug(f"GET {url}")

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

    # Guarded like every other optional field above and below. Unguarded, a
    # record with no trustScore carried "- Source Reputation: Unknown" -- 28
    # characters stating that we know nothing, on every such hit. The helper
    # already owns the definition of unknown, so ask it instead of repeating
    # the None/negative test here and letting the two drift apart.
    reputation = _get_source_reputation_label(result.get("trustScore"))
    if reputation != "Unknown":
        lines.append(f"- Source Reputation: {reputation}")

    benchmark_score = result.get("benchmarkScore")
    if benchmark_score is not None and benchmark_score > 0:
        lines.append(f"- Benchmark Score: {benchmark_score}")

    versions = result.get("versions")
    if versions and len(versions) > 0:
        lines.append(f"- Versions: {', '.join(versions)}")

    return "\n".join(lines)


def _format_search_results(results: list, offset: int = 0,
                           char_budget: int = 0) -> str:
    """The library list, paged by RECORD — never half a record.

    A search answer is row-shaped by definition, so its share of the ceiling is
    spent by dropping whole records rather than by cutting characters: every
    record kept is complete, and the closing line says where to resume. Each
    record is six upstream-controlled fields, one of which is a free-text
    description, so a handful of verbose libraries can outgrow the ceiling on
    their own. ``char_budget`` of 0 means unlimited.

    The note is emitted only when the view is actually partial; a full list needs
    no accounting line, and one on every reply is pure per-call boilerplate.
    """
    if not results:
        return "No documentation libraries found matching your query."

    total = len(results)
    # NOT clamped to `total`: an over-offset must be reported back as the value
    # the CALLER passed. Clamping first turns `offset=99 of 40` into the
    # meaningless `offset=40 of 40` and hides the caller's mistake. A Python
    # slice past the end is already empty, so the clamp bought nothing.
    start = max(0, offset)
    budget = char_budget if char_budget > 0 else 0

    blocks = []
    for result in results[start:]:
        block = _format_search_result(result)
        # At least one record always survives: a bare accounting line tells the
        # caller nothing, and _cap_text() is the hard backstop for the ceiling.
        if (budget > 0 and blocks
                and budget - len(block) - len(RESULT_SEPARATOR) < PAGE_LINE_RESERVE):
            break
        budget -= len(block) + len(RESULT_SEPARATOR)
        blocks.append(block)

    if start == 0 and len(blocks) == total:
        return RESULT_SEPARATOR.join(blocks)
    note = _rows_note(start, len(blocks), total)
    if not blocks:
        return note
    return RESULT_SEPARATOR.join(blocks) + "\n" + note


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
    # Capped everywhere a value from UPSTREAM can reach the reply: the record
    # list, but also the two error paths, since `message` and `error` are fields
    # context7.com fills in and neither has a documented length.
    cap = _max_answer_chars(args)
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
        return _cap_text(_parse_error_response(e.code, body, API_KEY), cap)
    except Exception as e:
        return _cap_text(f"Error searching libraries: {e}", cap)

    results = data.get("results", [])
    if not results:
        error = data.get("error")
        return _cap_text(error, cap) if error \
            else "No libraries found matching the provided name."

    header = "Available Libraries:\n\n"
    body = _format_search_results(results, _offset(args),
                                  max(0, cap - len(header)) if cap > 0 else 0)
    return _cap_text(header + body, cap)


async def handle_context7_query_docs(args: dict) -> str:
    # THE payload this ceiling exists for: /v2/context returns as much markdown as
    # context7.com decides to return, and nothing on this side bounds it. Cut
    # head-first (upstream ranks snippets by relevance to `query`) with NO resume
    # hint: the answer is one opaque blob with no stable index, a second call
    # re-runs the upstream search, and there is no `offset` here to hand back.
    # The closing line points at the two levers that do exist instead — raise
    # max_answer_chars, or narrow the query.
    cap = _max_answer_chars(args)
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
        return _cap_text(_parse_error_response(e.code, body, API_KEY), cap)
    except Exception as e:
        return _cap_text(f"Error fetching library context. Please try again later. {e}", cap)

    if not text or not text.strip():
        return (
            "Documentation not found or not finalized for this library. "
            "This might have happened because you used an invalid Context7-compatible library ID. "
            "To get a valid Context7-compatible library ID, use 'context7_resolve_library_id' "
            "with the package name you wish to retrieve documentation for."
        )

    return _cap_text(text, cap)


async def handle_context7_call(args: dict) -> str:
    """Dispatcher: call any Context7 tool by name."""
    function = args.get("function", "")
    raw_params = args.get("params") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return f"Error: {exc}"

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

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        """Return a tool-level error (visible in LLM context, per SEP-2140)."""
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        log.debug(f"← {method} (id={msg_id})")

        # Notifications carry no id and require no response
        if msg_id is None:
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-context7", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": LISTED_TOOLS})

        if method == "tools/call":
            return await self._dispatch_tool(msg_id, msg.get("params", {}))

        return self._error(msg_id, -32601, f"Method not found: {method}")

    async def _dispatch_tool(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as exc:
                return self._tool_error(msg_id, f"'arguments' was a string but not valid JSON: {exc}")
        if not isinstance(args, dict):
            return self._tool_error(msg_id, f"'arguments' must be an object; got {type(args).__name__}.")

        handler = ALL_HANDLERS.get(name)
        if handler is None:
            return self._tool_error(
                msg_id,
                f"Unknown tool: '{name}'. Available: {', '.join(sorted(ALL_HANDLERS.keys()))}"
            )

        try:
            result = await handler(args)
            return self._result(msg_id, {"content": [{"type": "text", "text": result}]})
        except Exception as e:
            log.debug(f"Handler '{name}' error: {e}")
            return self._tool_error(msg_id, f"Error in {name}: {e}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.debug("mcp-context7 server ready (stdio)")

        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                log.debug("stdin EOF — shutting down")
                break

            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                log.debug(f"JSON parse error: {e}")
                continue

            try:
                response = await self.handle_message(msg)
            except Exception as exc:
                log.exception("Unhandled exception while handling message")
                response = self._error(
                    msg.get("id"), -32603,
                    f"Internal error: {type(exc).__name__}: {exc}",
                )
            if response is not None:
                log.debug("→ RAW: %s", json.dumps(response))
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Context7 Documentation MCP Server — standalone, no external dependencies"
    )
    parser.add_argument("--api-key", help="API key for Context7 (or set CONTEXT7_API_KEY env var)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    parsed = parser.parse_args()

    global API_KEY
    level = logging.DEBUG if (parsed.debug or parsed.log_file) else logging.WARNING
    log_handlers = []
    if parsed.log_file:
        log_handlers.append(logging.FileHandler(parsed.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=log_handlers,
    )

    # Priority: --api-key flag > CONTEXT7_API_KEY env var > anonymous
    API_KEY = parsed.api_key or os.environ.get("CONTEXT7_API_KEY")

    server = McpServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        log.debug("Server stopped")


if __name__ == "__main__":
    main()
