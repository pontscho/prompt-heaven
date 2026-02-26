#!/usr/bin/env python3
"""
mcp-clangd — C/C++ code intelligence MCP server via clangd LSP.

Design:
  tools/list → exposes only 'clangd_call' (minimal token footprint)
  tools/call → dispatches all 13 clangd tools (documented in SKILL.md)

Usage:
  python3 mcp-clangd.py [--debug]
"""

import asyncio
import json
import os
import pathlib
import re
import sys
import argparse
import logging
from typing import Any, Dict, List, Optional


# ============================================================
# Debug logging
# ============================================================

DEBUG = False
MARKDOWN_MODE = False
_log_file: Optional[str] = None


def debug_log(message: str) -> None:
    if not DEBUG:
        return
    line = f"[DEBUG] {message}\n"
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line)
    else:
        sys.stderr.write(line)
        sys.stderr.flush()


# ============================================================
# LSP framing
# ============================================================

def encode_lsp_message(body: dict) -> bytes:
    """Encode a dict as an LSP message with Content-Length framing."""
    text = json.dumps(body)
    encoded = text.encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n"
    return header.encode("ascii") + encoded


async def read_lsp_message(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one LSP message from reader. Returns None on EOF."""
    content_length = 0
    while True:
        try:
            line_bytes = await reader.readline()
        except Exception:
            return None
        if not line_bytes:
            return None
        line = line_bytes.decode("ascii", errors="replace").strip()
        if not line:
            # Empty line = end of headers
            if content_length > 0:
                break
            continue
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass

    if content_length == 0:
        return None

    try:
        body_bytes = await reader.readexactly(content_length)
    except (asyncio.IncompleteReadError, Exception):
        return None

    try:
        return json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return None


# ============================================================
# Utility helpers
# ============================================================

def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute path."""
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri


def path_to_uri(path: str) -> str:
    """Convert an absolute path to a file:// URI."""
    return pathlib.Path(path).absolute().as_uri()


SYMBOL_KIND_MAP = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package",
    5: "Class", 6: "Method", 7: "Property", 8: "Field",
    9: "Constructor", 10: "Enum", 11: "Interface", 12: "Function",
    13: "Variable", 14: "Constant", 15: "String", 16: "Number",
    17: "Boolean", 18: "Array", 19: "Object", 20: "Key",
    21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}

DEFINITION_KINDS = {
    "Class", "Struct", "Function", "Method", "Enum",
    "Interface", "Variable", "Field", "Constructor",
}


def symbol_kind_name(kind: int) -> str:
    return SYMBOL_KIND_MAP.get(kind, "Unknown")


def extract_code_range(file_path: str, lsp_range: dict) -> str:
    """Extract the exact text span described by an LSP range from a file."""
    start = lsp_range.get("start", {})
    end = lsp_range.get("end", {})
    start_line = start.get("line", 0)
    start_char = start.get("character", 0)
    end_line = end.get("line", 0)
    end_char = end.get("character", 0)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if start_line == end_line:
            return lines[start_line][start_char:end_char].rstrip("\n") if start_line < len(lines) else ""
        parts = [lines[start_line][start_char:]] if start_line < len(lines) else []
        for i in range(start_line + 1, end_line):
            if i < len(lines):
                parts.append(lines[i])
        if end_line < len(lines):
            parts.append(lines[end_line][:end_char])
        return "".join(parts).rstrip("\n")
    except Exception:
        return ""


def extract_surrounding_code(file_path: str, line: int, ctx_lines: int = 5) -> str:
    """Extract ctx_lines lines of context around the given (0-based) line."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        start = max(0, line - ctx_lines)
        end = min(len(lines), line + ctx_lines + 1)
        out = []
        for i in range(start, end):
            marker = ">>> " if i == line else "    "
            out.append(f"{marker}{lines[i].rstrip()}")
        return "\n".join(out)
    except Exception:
        return ""


def _relative_path(uri: str, project_root: str) -> str:
    """Return the path relative to project_root, or the absolute path if outside."""
    abs_path = pathlib.Path(uri_to_path(uri))
    try:
        return str(abs_path.relative_to(project_root))
    except ValueError:
        return str(abs_path)


def _detect_language(file_path: str) -> str:
    suffix = pathlib.Path(file_path).suffix.lower()
    if suffix == ".c":
        return "c"
    return "cpp"


# ============================================================
# ClangdClient
# ============================================================

class ClangdClient:
    """Async LSP client for clangd with background reader and push-notification support."""

    def __init__(self) -> None:
        self.project_root: str = ""
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_id: int = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._diagnostics: Dict[str, List] = {}       # uri → list[diagnostic]
        self._diag_events: Dict[str, asyncio.Event] = {}
        self._opened_files: set = set()
        self._reader_task: Optional[asyncio.Task] = None
        self._indexing_done: asyncio.Event = asyncio.Event()
        self._active_progress: set = set()   # tokens with begin but no end yet
        self._send_lock = asyncio.Lock()

    async def start(self, project_root: str, clangd_path: str = "clangd",
                    compile_commands_dir: Optional[str] = None) -> str:
        """Launch clangd, perform LSP handshake, wait for background indexing."""
        if self.process is not None:
            return "already initialized"

        self.project_root = str(pathlib.Path(project_root).resolve())
        self._indexing_done.clear()

        # Resolve compile_commands_dir to absolute (before cwd changes to project_root)
        if compile_commands_dir:
            compile_commands_dir = str(pathlib.Path(compile_commands_dir).resolve())

        args = [
            clangd_path,
            "--background-index",
            "--clang-tidy=false",
            "--header-insertion=never",
            "--pch-storage=memory",
        ]
        if compile_commands_dir:
            args.append(f"--compile-commands-dir={compile_commands_dir}")

        # Ensure compile-commands-dir is set so clangd stores the background
        # index in <project_root>/.cache/clangd/index/ instead of ~/Library/Caches/
        if not compile_commands_dir:
            args.append(f"--compile-commands-dir={self.project_root}")

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
            cwd=self.project_root,
        )
        debug_log(f"clangd PID: {self.process.pid}")

        # Start background reader before sending any requests
        self._reader_task = asyncio.create_task(self._reader_loop())

        # LSP initialize
        init_params = {
            "processId": self.process.pid,
            "rootUri": path_to_uri(self.project_root),
            "workspaceFolders": [{"uri": path_to_uri(self.project_root), "name": "workspace"}],
            "initializationOptions": {
                "compilationDatabasePath": compile_commands_dir or self.project_root,
            },
            "capabilities": {
                "general": {"positionEncodings": ["utf-8", "utf-16"]},
                "textDocument": {
                    "definition": {"linkSupport": True},
                    "publishDiagnostics": {},
                    "inlayHint": {"dynamicRegistration": True},
                },
                "window": {
                    "workDoneProgress": True,
                },
            },
        }
        response = await self._request("initialize", init_params, timeout=30.0)
        if "error" in response:
            raise RuntimeError(f"clangd initialize failed: {response['error']}")

        await self._notify("initialized", {})

        # Wait for background indexing to complete (up to 60s)
        debug_log("Waiting for clangd background indexing...")
        try:
            await asyncio.wait_for(self._indexing_done.wait(), timeout=60.0)
            debug_log("Background indexing done.")
        except asyncio.TimeoutError:
            debug_log("Indexing wait timed out — priming index by opening source files...")

        # Prime the index: open up to 10 source files so workspace/symbol works
        await self._prime_index()

        version = response.get("result", {}).get("serverInfo", {})
        return f"clangd initialized at {self.project_root} — {version}"

    async def _prime_index(self) -> None:
        """Open a sample of source files to ensure the workspace index is populated."""
        source_files = []
        for root, _, files in os.walk(self.project_root):
            # Skip hidden directories and build dirs
            if any(part.startswith(".") or part in ("build", "out", "dist", ".git")
                   for part in pathlib.Path(root).parts):
                continue
            for fname in files:
                if pathlib.Path(fname).suffix.lower() in {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}:
                    source_files.append(os.path.join(root, fname))
                    if len(source_files) >= 10:
                        break
            if len(source_files) >= 10:
                break

        if source_files:
            debug_log(f"Priming index with {len(source_files)} file(s)")
            for path in source_files:
                await self.open_document(path)
                await asyncio.sleep(0.1)
            # Brief wait for clangd to parse the opened files
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self.process:
            try:
                await self._notify("exit", {})
            except Exception:
                pass
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                    await self.process.wait()
                except Exception:
                    pass
            self.process = None

        self._pending.clear()
        self._opened_files.clear()
        self._diagnostics.clear()

    async def _reader_loop(self) -> None:
        """Background task: read all LSP messages and route them."""
        assert self.process and self.process.stdout
        reader = self.process.stdout
        while True:
            msg = await read_lsp_message(reader)
            if msg is None:
                debug_log("clangd stdout EOF")
                break

            msg_id = msg.get("id")
            method = msg.get("method", "")

            # --- Response to a pending request ---
            if msg_id is not None and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)

            # --- clangd requests a progress token (indexing) ---
            elif method == "window/workDoneProgress/create":
                token = msg.get("params", {}).get("token", "")
                if token:
                    self._active_progress.add(token)
                await self._send({"jsonrpc": "2.0", "id": msg_id, "result": None})

            # --- Progress notification (indexing $/progress) ---
            elif method == "$/progress":
                token = msg.get("params", {}).get("token", "")
                kind = msg.get("params", {}).get("value", {}).get("kind", "")
                if kind == "begin":
                    self._active_progress.add(token)
                elif kind == "end":
                    self._active_progress.discard(token)
                    if not self._active_progress:
                        debug_log("All progress tokens finished — indexing done")
                        self._indexing_done.set()

            # --- Diagnostics push ---
            elif method == "textDocument/publishDiagnostics":
                params = msg.get("params", {})
                uri = params.get("uri", "")
                diags = params.get("diagnostics", [])
                self._diagnostics[uri] = diags
                ev = self._diag_events.get(uri)
                if ev:
                    ev.set()
                debug_log(f"Diagnostics for {uri}: {len(diags)} items")

            else:
                debug_log(f"Unhandled notification: {method}")

    async def _send(self, body: dict) -> None:
        assert self.process and self.process.stdin
        data = encode_lsp_message(body)
        async with self._send_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _request(self, method: str, params: Any, timeout: float = 10.0) -> dict:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"error": {"message": f"timeout waiting for {method}"}}

    async def _notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def open_document(self, path: str) -> None:
        """Send textDocument/didOpen if not already open."""
        abs_path = pathlib.Path(path)
        if not abs_path.is_absolute():
            abs_path = pathlib.Path(self.project_root) / abs_path
        abs_path = abs_path.resolve()
        uri = abs_path.as_uri()
        if uri in self._opened_files:
            return
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            debug_log(f"Cannot read {abs_path}: {e}")
            return
        self._opened_files.add(uri)
        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": _detect_language(str(abs_path)),
                "version": 1,
                "text": content,
            }
        })

    # ── LSP method wrappers ──────────────────────────────────

    async def workspace_symbol(self, query: str) -> List[dict]:
        resp = await self._request("workspace/symbol", {"query": query}, timeout=15.0)
        return resp.get("result") or []

    async def document_symbol(self, path: str) -> List[dict]:
        await self.open_document(path)
        abs_uri = pathlib.Path(path) if pathlib.Path(path).is_absolute() \
            else pathlib.Path(self.project_root) / path
        resp = await self._request("textDocument/documentSymbol", {
            "textDocument": {"uri": abs_uri.resolve().as_uri()}
        }, timeout=15.0)
        return resp.get("result") or []

    async def definition(self, path: str, line: int, char: int) -> Any:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=15.0)
        return resp.get("result")

    async def references(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
            "context": {"includeDeclaration": True},
        }, timeout=15.0)
        return resp.get("result") or []

    async def implementation(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=15.0)
        result = resp.get("result")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    async def hover(self, path: str, line: int, char: int) -> Optional[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=10.0)
        return resp.get("result")

    async def inlay_hints(self, path: str, start_line: int, end_line: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/inlayHint", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": start_line, "character": 0},
                "end": {"line": end_line, "character": 0},
            },
        }, timeout=15.0)
        return resp.get("result") or []

    async def prepare_call_hierarchy(self, path: str, line: int, char: int) -> List[dict]:
        await self.open_document(path)
        uri = self._abs_uri(path)
        resp = await self._request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": char},
        }, timeout=10.0)
        result = resp.get("result")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    async def call_hierarchy_incoming(self, item: dict) -> List[dict]:
        resp = await self._request("callHierarchy/incomingCalls", {"item": item}, timeout=10.0)
        return resp.get("result") or []

    async def call_hierarchy_outgoing(self, item: dict) -> List[dict]:
        resp = await self._request("callHierarchy/outgoingCalls", {"item": item}, timeout=10.0)
        return resp.get("result") or []

    async def get_diagnostics(self, path: str, timeout: float = 10.0) -> List[dict]:
        """Open document, wait for publishDiagnostics push, return diagnostics."""
        uri = self._abs_uri(path)
        ev = self._diag_events.setdefault(uri, asyncio.Event())
        ev.clear()
        await self.open_document(path)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self._diagnostics.get(uri, [])

    def _abs_uri(self, path: str) -> str:
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path(self.project_root) / p
        return p.resolve().as_uri()

    def _abs_path(self, path: str) -> str:
        p = pathlib.Path(path)
        if not p.is_absolute():
            p = pathlib.Path(self.project_root) / p
        return str(p.resolve())


# ============================================================
# Global state
# ============================================================

_client: Optional[ClangdClient] = None


def _require_client() -> ClangdClient:
    if _client is None or _client.process is None:
        raise RuntimeError("clangd not initialized — call clangd_init first")
    return _client


# ============================================================
# Location formatting helpers
# ============================================================

def _format_location(uri: str, lsp_range: dict, project_root: str) -> dict:
    rel = _relative_path(uri, project_root)
    start = lsp_range.get("start", {})
    end = lsp_range.get("end", {})
    return {
        "path": rel,
        "uri": uri,
        "range": lsp_range,
        "range_human": {
            "start": {"line": start.get("line", 0) + 1, "character": start.get("character", 0) + 1},
            "end": {"line": end.get("line", 0) + 1, "character": end.get("character", 0) + 1},
        },
        "line_text": _get_line(uri_to_path(uri), start.get("line", 0)),
    }


def _get_line(abs_path: str, zero_line: int) -> Optional[str]:
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if 0 <= zero_line < len(lines):
            return lines[zero_line].rstrip("\n")
    except Exception:
        pass
    return None


def _location_from_payload(payload: dict, project_root: str) -> Optional[dict]:
    """Normalise a raw LSP location/link dict into our standard format."""
    uri = payload.get("uri") or payload.get("targetUri")
    if not uri:
        return None
    lsp_range = (payload.get("range")
                 or payload.get("targetSelectionRange")
                 or payload.get("targetRange"))
    if not lsp_range:
        return None
    return _format_location(uri, lsp_range, project_root)


def _flatten_hover(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        val = contents.get("value")
        return str(val) if val is not None else ""
    if isinstance(contents, list):
        parts = [_flatten_hover(c) for c in contents]
        return "\n".join(p for p in parts if p)
    return str(contents)


def _infer_type(text: str) -> str:
    """Extract the most relevant type name from clangd hover text."""
    cleaned = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    for line in cleaned.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith(("@", "#")):
            continue
        if candidate.lower().startswith("type-alias"):
            continue
        if candidate.startswith("aka:"):
            candidate = candidate[4:].strip()
        candidate = candidate.split("//", 1)[0].strip()
        if not candidate:
            continue
        if candidate.startswith("(") and candidate.endswith(")"):
            inner = candidate[1:-1].strip()
            if inner:
                return inner
        if "->" in candidate:
            return candidate.split("->")[-1].strip()
        if any(sym in candidate for sym in ("::", "<", "*", "&")) or candidate.startswith("std::"):
            return candidate
        if any(c.isalpha() for c in candidate):
            return candidate
    return cleaned


def _iter_document_symbols(symbols: List[dict]):
    """Flatten hierarchical document symbols."""
    for s in symbols:
        if not isinstance(s, dict):
            continue
        yield s
        for child in _iter_document_symbols(s.get("children") or []):
            yield child


# ============================================================
# Symbol lookup: name → position
# ============================================================

async def _symbol_to_location(client: ClangdClient, symbol_name: str,
                               max_retries: int = 3) -> Optional[dict]:
    """
    Find the first workspace symbol matching symbol_name with a definition kind.
    Retries a few times to handle cases where the index isn't fully populated yet.
    """
    for attempt in range(max_retries):
        symbols = await client.workspace_symbol(symbol_name)

        # First pass: exact name match with definition kind
        for sym in symbols:
            kind = symbol_kind_name(sym.get("kind", 0))
            if sym.get("name") != symbol_name:
                continue
            if kind not in DEFINITION_KINDS:
                continue
            loc = sym.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            if uri:
                return {
                    "path": uri_to_path(uri),
                    "uri": uri,
                    "line": start.get("line", 0),
                    "char": start.get("character", 0),
                }

        # Second pass: exact name, any kind
        for sym in symbols:
            if sym.get("name") != symbol_name:
                continue
            loc = sym.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            if uri:
                return {
                    "path": uri_to_path(uri),
                    "uri": uri,
                    "line": start.get("line", 0),
                    "char": start.get("character", 0),
                }

        if attempt < max_retries - 1:
            debug_log(f"workspace/symbol retry {attempt + 1} for '{symbol_name}'")
            await asyncio.sleep(1.0)

    return None


# ============================================================
# Handler functions (13)
# ============================================================

async def handle_init(args: dict) -> Any:
    """
    Initialize clangd for a project.
    params: { project_root, clangd_path?, compile_commands_dir? }
    """
    global _client
    project_root = args.get("project_root", "")
    if not project_root:
        return {"error": "project_root is required"}

    clangd_path = args.get("clangd_path", "clangd")
    compile_commands_dir = args.get("compile_commands_dir")

    if _client is not None and _client.process is not None:
        return {"status": "already initialized", "project_root": _client.project_root}

    _client = ClangdClient()
    try:
        msg = await _client.start(project_root, clangd_path, compile_commands_dir)
        return {"status": "ok", "message": msg, "project_root": _client.project_root}
    except Exception as e:
        _client = None
        return {"error": str(e)}


async def handle_find_definition(args: dict) -> Any:
    """
    Find definition of a symbol by name.
    params: { symbol_name, context_lines? }
    """
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    context_lines = int(args.get("context_lines", 5))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    loc = await _symbol_to_location(client, symbol_name)
    if not loc:
        return {"error": f"Symbol '{symbol_name}' not found in workspace"}

    path, line, char = loc["path"], loc["line"], loc["char"]
    def_result = await client.definition(path, line, char)
    if not def_result:
        return {"error": f"No definition found for '{symbol_name}'"}

    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        def_line = location["range"]["start"]["line"]
        results.append({
            "symbol": symbol_name,
            "location": location,
            "context": extract_surrounding_code(abs_path, def_line, context_lines),
        })
    return results


async def handle_find_definition_at(args: dict) -> Any:
    """
    Find definition at a specific file position.
    params: { path, line, character, context_lines? }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1   # human (1-based) → LSP (0-based)
    char = int(args.get("character", 1)) - 1
    context_lines = int(args.get("context_lines", 5))
    if not path:
        return {"error": "path is required"}

    def_result = await client.definition(client._abs_path(path), line, char)
    if not def_result:
        return {"error": "No definition found"}

    locations = def_result if isinstance(def_result, list) else [def_result]
    results = []
    for payload in locations:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        def_line = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(abs_path, def_line, context_lines),
        })
    return results


async def handle_find_references(args: dict) -> Any:
    """
    Find all references to a symbol by name.
    params: { symbol_name, max_results?, context_lines? }
    """
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    max_results = int(args.get("max_results", 50))
    context_lines = int(args.get("context_lines", 3))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    symbols = await client.workspace_symbol(symbol_name)
    all_refs = []
    seen = set()

    for sym in symbols:
        if sym.get("name") != symbol_name:
            continue
        kind = symbol_kind_name(sym.get("kind", 0))
        if kind not in DEFINITION_KINDS:
            continue
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        start = loc.get("range", {}).get("start", {})
        path = uri_to_path(uri)
        line = start.get("line", 0)
        char = start.get("character", 0)
        refs = await client.references(path, line, char)
        for ref in refs:
            if max_results > 0 and len(all_refs) >= max_results:
                break
            ref_uri = ref.get("uri", "")
            ref_start = ref.get("range", {}).get("start", {})
            key = f"{ref_uri}:{ref_start.get('line')}:{ref_start.get('character')}"
            if key in seen:
                continue
            seen.add(key)
            location = _location_from_payload(ref, client.project_root)
            if not location:
                continue
            abs_path = uri_to_path(location["uri"])
            ref_line = location["range"]["start"]["line"]
            all_refs.append({
                "symbol": symbol_name,
                "location": location,
                "context": extract_surrounding_code(abs_path, ref_line, context_lines) if context_lines > 0 else None,
            })
        if max_results > 0 and len(all_refs) >= max_results:
            break

    return {
        "symbol": symbol_name,
        "count": len(all_refs),
        "references": all_refs,
    }


async def handle_find_implementations_at(args: dict) -> Any:
    """
    Find implementations at a specific file position.
    params: { path, line, character, context_lines? }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    context_lines = int(args.get("context_lines", 5))
    if not path:
        return {"error": "path is required"}

    impls = await client.implementation(client._abs_path(path), line, char)
    results = []
    for payload in impls:
        location = _location_from_payload(payload, client.project_root)
        if not location:
            continue
        abs_path = uri_to_path(location["uri"])
        impl_line = location["range"]["start"]["line"]
        results.append({
            "location": location,
            "context": extract_surrounding_code(abs_path, impl_line, context_lines),
        })
    return results


async def handle_workspace_symbols(args: dict) -> Any:
    """
    Search workspace symbols by query string.
    params: { query, limit? }
    """
    client = _require_client()
    query = args.get("query", "")
    limit = int(args.get("limit", 50))
    if not query:
        return {"error": "query is required"}

    symbols = await client.workspace_symbol(query)
    results = []
    for sym in symbols[:limit]:
        loc = sym.get("location", {})
        uri = loc.get("uri", "")
        lsp_range = loc.get("range", {})
        results.append({
            "symbol": sym.get("name", ""),
            "kind": symbol_kind_name(sym.get("kind", 0)),
            "container": sym.get("containerName"),
            "location": _format_location(uri, lsp_range, client.project_root) if uri else None,
        })
    return {"query": query, "count": len(results), "symbols": results}


async def handle_document_outline(args: dict) -> Any:
    """
    Get document symbols / outline for a file.
    params: { path }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    if not path:
        return {"error": "path is required"}

    abs_path = client._abs_path(path)
    symbols = await client.document_symbol(abs_path)
    file_uri = pathlib.Path(abs_path).as_uri()

    def fmt(sym: dict) -> dict:
        # DocumentSymbol (hierarchical) vs SymbolInformation (flat)
        if "selectionRange" in sym:
            # DocumentSymbol
            sel = _format_location(file_uri, sym["selectionRange"], client.project_root)
            ext = _format_location(file_uri, sym.get("range", sym["selectionRange"]), client.project_root)
            node = {
                "symbol": sym.get("name", ""),
                "kind": symbol_kind_name(sym.get("kind", 0)),
                "detail": sym.get("detail"),
                "selection": sel,
                "extent": ext,
            }
            children = sym.get("children") or []
            if children:
                node["children"] = [fmt(c) for c in children]
        else:
            # SymbolInformation (flat)
            loc = sym.get("location", {})
            node = {
                "symbol": sym.get("name", ""),
                "kind": symbol_kind_name(sym.get("kind", 0)),
                "container": sym.get("containerName"),
                "location": _format_location(loc.get("uri", file_uri), loc.get("range", {}), client.project_root),
            }
        return node

    return [fmt(s) for s in symbols]


async def handle_symbol_context(args: dict) -> Any:
    """
    Get definition + references for a symbol in one call.
    params: { symbol_name, max_references?, context_lines? }
    """
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    max_references = int(args.get("max_references", 20))
    context_lines = int(args.get("context_lines", 5))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    definition = await handle_find_definition({"symbol_name": symbol_name, "context_lines": context_lines})
    references = await handle_find_references({
        "symbol_name": symbol_name,
        "max_results": max_references,
        "context_lines": 2,
    })
    return {
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
    }


async def handle_inlay_hints(args: dict) -> Any:
    """
    Get inlay hints (parameter names, type hints) for a file range.
    params: { path, start_line?, end_line?, limit? }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    start_line = int(args.get("start_line", 1)) - 1
    end_line = int(args.get("end_line", 9999)) - 1
    limit = int(args.get("limit", 100))
    if not path:
        return {"error": "path is required"}

    hints = await client.inlay_hints(client._abs_path(path), start_line, end_line)
    kind_map = {1: "Parameter", 2: "Type"}
    results = []
    for hint in hints[:limit]:
        pos = hint.get("position", {})
        label = hint.get("label", "")
        if isinstance(label, list):
            label = "".join(
                p.get("value", "") if isinstance(p, dict) else str(p)
                for p in label
            )
        raw_kind = hint.get("kind")
        results.append({
            "label": label,
            "kind": kind_map.get(raw_kind, "Unknown") if raw_kind else None,
            "position": {
                "lsp": pos,
                "human": {"line": pos.get("line", 0) + 1, "character": pos.get("character", 0) + 1},
            },
            "tooltip": hint.get("tooltip"),
        })
    return results


async def handle_symbol_change_impact(args: dict) -> Any:
    """
    Definition + references + call hierarchy for impact analysis before changing a symbol.
    params: { symbol_name, max_references?, call_hierarchy_depth? }
    """
    client = _require_client()
    symbol_name = args.get("symbol_name", "")
    max_references = int(args.get("max_references", 50))
    depth = int(args.get("call_hierarchy_depth", 1))
    if not symbol_name:
        return {"error": "symbol_name is required"}

    definition = await handle_find_definition({"symbol_name": symbol_name, "context_lines": 3})
    references = await handle_find_references({
        "symbol_name": symbol_name,
        "max_results": max_references,
        "context_lines": 2,
    })

    # Call hierarchy for each definition location
    call_hierarchies = []
    if isinstance(definition, list):
        seen_roots = set()
        for defn in definition:
            loc = defn.get("location", {})
            uri = loc.get("uri", "")
            start = loc.get("range", {}).get("start", {})
            line = start.get("line", 0)
            char = start.get("character", 0)
            key = f"{uri}:{line}:{char}"
            if key in seen_roots:
                continue
            seen_roots.add(key)
            hier = await _collect_call_hierarchy(client, uri_to_path(uri), line, char, depth)
            if hier:
                call_hierarchies.append(hier)

    # Impact summary
    ref_files = sorted({
        r.get("location", {}).get("path", "")
        for r in (references.get("references", []) if isinstance(references, dict) else [])
        if r.get("location", {}).get("path")
    })

    return {
        "symbol": symbol_name,
        "definition": definition,
        "references": references,
        "reference_summary": {
            "count": references.get("count", 0) if isinstance(references, dict) else 0,
            "files": ref_files,
        },
        "call_hierarchy": call_hierarchies,
    }


async def handle_hover(args: dict) -> Any:
    """
    Get hover information at a position.
    params: { path, line, character }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    if not path:
        return {"error": "path is required"}

    result = await client.hover(client._abs_path(path), line, char)
    if not result:
        return {"error": "No hover information at this position"}

    contents = result.get("contents")
    text = _flatten_hover(contents)
    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)
    return {
        "text": text,
        "location": location,
    }


async def handle_diagnostics(args: dict) -> Any:
    """
    Get compiler diagnostics (warnings/errors) for a file.
    params: { path, timeout? }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    timeout = float(args.get("timeout", 10.0))
    if not path:
        return {"error": "path is required"}

    abs_path = client._abs_path(path)
    diags = await client.get_diagnostics(abs_path, timeout=timeout)
    severity_map = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
    results = []
    for d in diags:
        lsp_range = d.get("range", {})
        start = lsp_range.get("start", {})
        uri = pathlib.Path(abs_path).as_uri()
        results.append({
            "message": d.get("message", ""),
            "severity": severity_map.get(d.get("severity", 0), "Unknown"),
            "code": d.get("code"),
            "source": d.get("source"),
            "location": _format_location(uri, lsp_range, client.project_root),
        })
    return {
        "path": _relative_path(pathlib.Path(abs_path).as_uri(), client.project_root),
        "count": len(results),
        "diagnostics": results,
    }


async def handle_deduced_type_at(args: dict) -> Any:
    """
    Get the deduced type at a position (for auto/decltype variables).
    params: { path, line, character }
    """
    client = _require_client()
    path = args.get("path") or args.get("file_path", "")
    line = int(args.get("line", 1)) - 1
    char = int(args.get("character", 1)) - 1
    if not path:
        return {"error": "path is required"}

    result = await client.hover(client._abs_path(path), line, char)
    if not result:
        return {"error": "No hover information at this position"}

    contents = result.get("contents")
    raw_text = _flatten_hover(contents).strip()
    deduced = _infer_type(raw_text)

    hover_range = result.get("range")
    location = None
    if hover_range:
        uri = pathlib.Path(client._abs_path(path)).as_uri()
        location = _format_location(uri, hover_range, client.project_root)

    return {
        "type": deduced,
        "raw": raw_text,
        "location": location,
    }


# ============================================================
# Call hierarchy helper
# ============================================================

async def _collect_call_hierarchy(
    client: ClangdClient, path: str, line: int, char: int, depth: int
) -> Optional[dict]:
    if depth < 0:
        return None
    items = await client.prepare_call_hierarchy(path, line, char)
    if not items:
        return None

    formatted = []
    for item in items:
        expanded = await _expand_hierarchy_item(client, item, depth, set())
        if expanded:
            formatted.append(expanded)

    if not formatted:
        return None
    return formatted[0] if len(formatted) == 1 else {"roots": formatted}


async def _expand_hierarchy_item(
    client: ClangdClient, item: dict, depth: int, seen: set
) -> Optional[dict]:
    uri = item.get("uri", "")
    sel_range = item.get("selectionRange") or item.get("range", {})
    start = sel_range.get("start", {})
    node_id = f"{uri}:{start.get('line')}:{start.get('character')}"
    if node_id in seen:
        return None
    seen.add(node_id)

    location = _format_location(uri, sel_range, client.project_root) if uri else None
    node = {
        "symbol": item.get("name", ""),
        "kind": symbol_kind_name(item.get("kind", 0)),
        "location": location,
    }
    if item.get("detail"):
        node["detail"] = item["detail"]

    if depth == 0:
        return node

    incoming = await client.call_hierarchy_incoming(item)
    outgoing = await client.call_hierarchy_outgoing(item)

    if incoming:
        in_entries = []
        for entry in incoming:
            src = entry.get("from")
            if not src:
                continue
            child = await _expand_hierarchy_item(client, src, depth - 1, seen.copy())
            if child:
                if entry.get("fromRanges"):
                    child["ranges"] = entry["fromRanges"]
                in_entries.append(child)
        if in_entries:
            node["incoming"] = in_entries

    if outgoing:
        out_entries = []
        for entry in outgoing:
            tgt = entry.get("to")
            if not tgt:
                continue
            child = await _expand_hierarchy_item(client, tgt, depth - 1, seen.copy())
            if child:
                if entry.get("fromRanges"):
                    child["ranges"] = entry["fromRanges"]
                out_entries.append(child)
        if out_entries:
            node["outgoing"] = out_entries

    return node


# ============================================================
# Handler registry
# ============================================================

ALL_HANDLERS = {
    "clangd_init":                    handle_init,
    "clangd_find_definition":         handle_find_definition,
    "clangd_find_definition_at":      handle_find_definition_at,
    "clangd_find_references":         handle_find_references,
    "clangd_find_implementations_at": handle_find_implementations_at,
    "clangd_workspace_symbols":       handle_workspace_symbols,
    "clangd_document_outline":        handle_document_outline,
    "clangd_symbol_context":          handle_symbol_context,
    "clangd_inlay_hints":             handle_inlay_hints,
    "clangd_symbol_change_impact":    handle_symbol_change_impact,
    "clangd_hover":                   handle_hover,
    "clangd_diagnostics":             handle_diagnostics,
    "clangd_deduced_type_at":         handle_deduced_type_at,
}


# ============================================================
# MCP dispatcher
# ============================================================

async def handle_clangd_call(args: dict, server: Optional["McpServer"] = None) -> str:
    """Universal dispatcher: routes to one of 13 clangd handlers."""
    function = args.get("function", "")
    params = args.get("params") or {}

    def _serialize(fn: str, data: Any) -> str:
        if MARKDOWN_MODE:
            return _result_to_markdown(fn, data)
        return json.dumps(data, ensure_ascii=False)

    if not function:
        # If auto-init is still running, report that
        if server and server._init_task and not server._init_task.done():
            return _serialize("", {"status": "initializing", "project_root": server.auto_project_root or "—"})
        status = "running" if (_client and _client.process) else "not initialized"
        root = _client.project_root if _client else "—"
        return _serialize("", {"status": status, "project_root": root})

    if function == "clangd_call":
        return _serialize("", {"error": "Cannot dispatch clangd_call recursively"})

    # If auto-init is in progress and the requested function needs the client,
    # wait for it to complete (up to 90s) rather than failing immediately.
    if (function != "clangd_init"
            and server and server._init_task and not server._init_task.done()
            and (_client is None or _client.process is None)):
        debug_log(f"Waiting for auto-init to complete before {function}...")
        try:
            await asyncio.wait_for(asyncio.shield(server._init_task), timeout=90.0)
        except (asyncio.TimeoutError, Exception) as e:
            debug_log(f"Auto-init wait failed: {e}")

    handler = ALL_HANDLERS.get(function)
    if handler is None:
        available = ", ".join(sorted(ALL_HANDLERS.keys()))
        return _serialize("", {"error": f"Unknown function: '{function}'. Available: {available}"})

    result = await handler(params)
    return _serialize(function, result)


# ============================================================
# Markdown output formatter
# ============================================================

def _md_loc(loc: dict) -> str:
    if not loc:
        return "?"
    path = loc.get("path", "?")
    start = loc.get("range_human", {}).get("start", {})
    line = start.get("line", "?")
    return f"{path}:{line}"


def _md_context(ctx: Any) -> str:
    if not ctx:
        return ""
    return f"\n```cpp\n{ctx.strip()}\n```"


def _md_location_block(loc: dict, context: Any = None) -> str:
    line_text = loc.get("line_text", "")
    ref = _md_loc(loc)
    out = f"{ref}"
    if line_text:
        out += f" — `{line_text.strip()}`"
    if context:
        out += _md_context(context)
    return out


def _result_to_markdown(function: str, result: Any) -> str:
    sep = "\n"

    if isinstance(result, dict) and "error" in result:
        return f"ERROR: {result['error']}"

    if not function:
        if isinstance(result, dict) and "status" in result:
            return f"status: {result['status']} | root: {result.get('project_root', '—')}"
        return json.dumps(result, ensure_ascii=False)

    if function in ("clangd_find_definition", "clangd_find_definition_at"):
        if not result:
            return "No definition found"
        parts = []
        for item in (result if isinstance(result, list) else [result]):
            sym = item.get("symbol", "")
            header = f"## Definition{': ' + sym if sym else ''}"
            parts.append(header + sep + _md_location_block(item.get("location", {}), item.get("context")))
        return sep.join(parts)

    if function == "clangd_find_references":
        sym = result.get("symbol", "")
        count = result.get("count", 0)
        refs = result.get("references", [])
        lines = [f"## References: {sym} ({count})"]
        for r in refs:
            lines.append("- " + _md_location_block(r.get("location", {})))
        return sep.join(lines)

    if function == "clangd_find_implementations_at":
        if not result:
            return "No implementations found"
        lines = ["## Implementations"]
        for item in (result if isinstance(result, list) else [result]):
            lines.append("- " + _md_location_block(item.get("location", {}), item.get("context")))
        return sep.join(lines)

    if function == "clangd_workspace_symbols":
        if not result:
            return "No symbols found"
        lines = ["## Workspace Symbols"]
        for s in (result if isinstance(result, list) else [result]):
            kind = s.get("kind", "")
            sym = s.get("symbol", s.get("name", ""))
            loc = _md_loc(s.get("location", {}))
            lines.append(f"- {kind} **{sym}** {loc}")
        return sep.join(lines)

    if function == "clangd_document_outline":
        lines = ["## Outline"]
        def _fmt_node(node: dict, indent: int = 0) -> None:
            kind = node.get("kind", "")
            sym = node.get("symbol", "")
            detail = node.get("detail", "")
            loc_node = node.get("selection") or node.get("location") or {}
            path = loc_node.get("path", "")
            rh = loc_node.get("range_human", {})
            start = rh.get("start", {})
            end = rh.get("end", {})
            line = start.get("line", "?")
            end_line = end.get("line", "?")
            line_text = loc_node.get("line_text", "")
            prefix = "  " * indent + "- "
            detail_str = f" `{detail}`" if detail else ""
            loc_str = f" {path}:{line}-{end_line}" if path else f" :{line}"
            text_str = f" — `{line_text.strip()}`" if line_text else ""
            lines.append(f"{prefix}{kind} **{sym}**{detail_str}{loc_str}{text_str}")
            for child in node.get("children", []):
                _fmt_node(child, indent + 1)
        for node in (result if isinstance(result, list) else [result]):
            _fmt_node(node)
        return sep.join(lines)

    if function == "clangd_symbol_context":
        sym = result.get("symbol", "")
        parts = [f"## Symbol: {sym}"]
        defs = result.get("definition", [])
        if defs:
            parts.append("### Definition")
            for d in (defs if isinstance(defs, list) else [defs]):
                parts.append(_md_location_block(d.get("location", {}), d.get("context")))
        refs_data = result.get("references", {})
        refs = refs_data.get("references", []) if isinstance(refs_data, dict) else []
        count = refs_data.get("count", len(refs)) if isinstance(refs_data, dict) else len(refs)
        if refs:
            parts.append(f"### References ({count})")
            for r in refs:
                parts.append("- " + _md_location_block(r.get("location", {})))
        return sep.join(parts)

    if function == "clangd_symbol_change_impact":
        sym = result.get("symbol", "")
        parts = [f"## Change Impact: {sym}"]
        defs = result.get("definition", [])
        if defs:
            parts.append("### Definition")
            for d in (defs if isinstance(defs, list) else [defs]):
                parts.append(_md_location_block(d.get("location", {}), d.get("context")))
        refs_data = result.get("references", {})
        refs = refs_data.get("references", []) if isinstance(refs_data, dict) else []
        if refs:
            parts.append(f"### References ({len(refs)})")
            for r in refs:
                parts.append("- " + _md_location_block(r.get("location", {})))
        callers = result.get("callers", [])
        if callers:
            parts.append(f"### Callers ({len(callers)})")
            for c in callers:
                parts.append("- " + _md_location_block(c.get("location", {})))
        return sep.join(parts)

    if function == "clangd_inlay_hints":
        hints = result if isinstance(result, list) else result.get("hints", [])
        if not hints:
            return "No inlay hints"
        lines = [f"## Inlay Hints ({len(hints)})"]
        for h in hints:
            loc = _md_loc(h.get("location", {}))
            label = h.get("label", "")
            kind = h.get("kind", "")
            lines.append(f"- {loc} `{label}` ({kind})")
        return sep.join(lines)

    if function == "clangd_hover":
        text = result.get("text", "")
        loc = result.get("location")
        header = "## Hover"
        if loc:
            header += f": {_md_loc(loc)}"
        return f"{header}{sep}```cpp{sep}{text.strip()}{sep}```"

    if function == "clangd_diagnostics":
        path = result.get("path", "")
        count = result.get("count", 0)
        diags = result.get("diagnostics", [])
        lines = [f"## Diagnostics: {path} ({count})"]
        for d in diags:
            sev = d.get("severity", "")
            msg = d.get("message", "")
            loc = _md_loc(d.get("location", {}))
            lines.append(f"- {sev}:{loc} {msg}")
        return sep.join(lines)

    if function == "clangd_deduced_type_at":
        type_str = result.get("type", result.get("text", ""))
        loc = result.get("location")
        header = "## Deduced Type"
        if loc:
            header += f": {_md_loc(loc)}"
        return f"{header}{sep}`{type_str}`"

    # fallback
    return json.dumps(result, ensure_ascii=False)


# ============================================================
# MCP Tool registry
# ============================================================

LISTED_TOOLS = [
    {
        "name": "clangd_call",
        "description": (
            "Call any clangd C/C++ code intelligence function by name. "
            "Returns server status if called without 'function'. "
            "Invoke the clangd-mcp skill for the full API reference."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "Function name (e.g. clangd_init, clangd_find_definition, clangd_hover)",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the function (see clangd-mcp skill for schema)",
                },
            },
            "required": [],
        },
    }
]


# ============================================================
# MCP Server — JSON-RPC 2.0 over stdio
# ============================================================

class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, auto_project_root: Optional[str] = None,
                 auto_compile_commands_dir: Optional[str] = None,
                 auto_clangd_path: str = "clangd") -> None:
        self.auto_project_root = auto_project_root
        self.auto_compile_commands_dir = auto_compile_commands_dir
        self.auto_clangd_path = auto_clangd_path
        self._init_task: Optional[asyncio.Task] = None

    def _ok(self, msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _err(self, msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _tool_error(self, msg_id: Any, text: str) -> dict:
        return self._ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        debug_log(f"← {method} (id={msg_id})")

        if msg_id is None:
            return None

        if method == "initialize":
            return self._ok(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-clangd", "version": "1.0.0"},
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
        tool_args = params.get("arguments") or {}

        if name == "clangd_call":
            try:
                result = await handle_clangd_call(tool_args, server=self)
                return self._ok(msg_id, {"content": [{"type": "text", "text": result}]})
            except Exception as e:
                debug_log(f"clangd_call error: {e}")
                return self._tool_error(msg_id, f"Error: {e}")

        return self._tool_error(
            msg_id,
            f"Unknown tool: '{name}'. This server exposes 'clangd_call'. Invoke the clangd-mcp skill."
        )

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        debug_log("mcp-clangd server ready (stdio)")

        # Auto-init: kick off in background so it doesn't block the MCP handshake
        if self.auto_project_root:
            debug_log(f"Auto-init: {self.auto_project_root}")
            self._init_task = asyncio.create_task(
                handle_init({
                    "project_root": self.auto_project_root,
                    "clangd_path": self.auto_clangd_path,
                    "compile_commands_dir": self.auto_compile_commands_dir,
                })
            )

        try:
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

                debug_log(f"← RAW: {line}")

                try:
                    response = await self.handle_message(msg)
                    if response is not None:
                        debug_log(f"→ RAW: {json.dumps(response)}")
                        sys.stdout.write(json.dumps(response) + "\n")
                        sys.stdout.flush()
                except Exception as e:
                    debug_log(f"Unhandled error: {e}")

        finally:
            debug_log("Shutting down clangd...")
            if _client is not None:
                await _client.stop()


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="mcp-clangd — C/C++ code intelligence MCP server"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Write debug output to this file instead of stderr")
    parser.add_argument("--markdown", action="store_true", help="Output tool results as markdown instead of JSON")
    parser.add_argument("--project-root", help="Auto-initialize clangd for this project on startup")
    parser.add_argument("--compile-commands-dir", help="Directory containing compile_commands.json")
    parser.add_argument("--clangd-path", default="clangd", help="Path to clangd binary (default: clangd)")
    parsed = parser.parse_args()

    global DEBUG, MARKDOWN_MODE, _log_file
    if parsed.log_file:
        _log_file = parsed.log_file
        DEBUG = True
    if parsed.debug:
        DEBUG = True
    if DEBUG:
        debug_log("Debug logging enabled")
    if parsed.markdown:
        MARKDOWN_MODE = True

    server = McpServer(
        auto_project_root=parsed.project_root,
        auto_compile_commands_dir=parsed.compile_commands_dir,
        auto_clangd_path=parsed.clangd_path,
    )
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        debug_log("Server stopped")


if __name__ == "__main__":
    main()
