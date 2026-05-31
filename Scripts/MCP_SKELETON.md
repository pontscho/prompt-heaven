# MCP_SKELETON — canonical single-file MCP server reference

> **This file is a GOLDEN REFERENCE. It is NOT imported by any server.**
> It is the anti-divergence anchor: every `Scripts/mcp-*.py` server converges its
> *plumbing* (PEP-723 header, logging, JSON-RPC framing, dispatch, catch-all) onto
> the shapes below. The actual tool logic of each server stays unique.
>
> Source of truth: distilled from `mcp-compile.py` (sync canonical) plus the
> designed async variant for the LSP/subprocess ("A-family") servers.
>
> **When you touch a server's plumbing, diff it against this file.** When this file
> and the servers disagree, one of them is a bug — fix the bug, don't fork the style.

---

## 0. Parameterization

Throughout this document, substitute per server:

| Placeholder | Meaning | Example |
|---|---|---|
| `SERVER_NAME` | logger name + `serverInfo.name` | `mcp-compile`, `mcp-clangd` |
| `TOOL_NAME` | the single tool exposed in `tools/list` | `compile_call`, `clangd_call` |
| `DISPATCH(...)` | the server-specific tool handler call | `handle_compile_call(...)` |

**Indentation is NOT unified.** Each file keeps its existing indentation —
`mcp-compile.py` / `mcp-forge.py` / `mcp-webfetch.py` / `mcp-tshark.py` use **TABS**;
all others use **4 spaces**. Match the file you are editing. Examples below use 4 spaces.

**Dispatch sync/async is NOT unified.** Servers that `await` a subprocess (the
LSP/A-family) keep an `async def handle_message` + `await DISPATCH`. Pure-stdlib
tooling servers keep a sync `_handle_message`. Only the *plumbing* converges.

---

## 1. Header — shebang + PEP-723 + docstring

Every server starts with a shebang, a PEP-723 inline-script-metadata block, then the
module docstring. The PEP-723 block lets `uv run` / `pipx run` resolve deps in a
clean environment. **Stdlib-only servers still declare an empty `dependencies` list**
so the contract is explicit.

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""SERVER_NAME — one-line purpose.

Single-tool dispatcher pattern: exposes one MCP tool (TOOL_NAME) that routes
to internal handlers via the 'function' parameter.

Usage:
  python3 SERVER_NAME.py [--debug] [--log-file <path>] ...
"""
```

Server **with** third-party deps (only `mcp-webfetch.py` today) declares them
exactly — naming every top-level and dynamically-imported third-party package:

```python
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
```

Pick `requires-python` to match the oldest syntax the file actually relies on
(`>=3.9` is the floor used across this repo).

---

## 2. Logging — stdlib `logging`, module-level logger

No custom `debug_log()`. No `DEBUG` / `_log_file` globals. A single module-level
logger; level and handlers are configured once in `main()`.

```python
import logging

log = logging.getLogger("SERVER_NAME")
```

Call sites use the standard levels: `log.debug(...)`, `log.info(...)`,
`log.warning(...)`, `log.exception(...)`. The `%`-style lazy form is preferred
(`log.debug("← %s", x)`), but a pre-formatted f-string argument is acceptable when
porting (`log.debug(f"← {x}")`).

> `MARKDOWN_MODE` (LSP trio: clangd/cuda/lua-lsp) is an orthogonal output-format
> flag, **not** a logging concern — it stays as a global and is untouched by the
> logging convergence.

---

## 3. RPC helpers — static, on `McpServer`

Three static helpers. `_result` and `_error` are the raw JSON-RPC envelopes;
`_tool_error` is the MCP `isError` tool-result envelope (present on servers whose
tool can fail with a human-readable message — the A-family and any tooling server
that wants it).

```python
class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    # ... __init__ ...

    @staticmethod
    def _result(msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id, text):
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})
```

Notes:
- `_tool_error` calls `McpServer._result(...)` (not `self._result`) so it works as a
  pure static.
- Callers may still write `self._result(...)` / `self._error(...)` — Python resolves
  static methods through the instance fine. Only the **definitions** must be static.
- The legacy A-family names `_ok` → `_result` and `_err` → `_error`.

---

## 4. Dispatch — `_handle_message` (initialize / ping / notifications / unknown)

The dispatcher is identical in shape for sync and async servers; only the keyword
`async` and the `await DISPATCH` differ. **`initialize` key order is canonical:**
`{protocolVersion, serverInfo, capabilities}`.

```python
    def _handle_message(self, msg):              # async def handle_message(self, msg)  (A-family)
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if msg_id is None:                       # JSON-RPC notification — never reply
            log.debug("Notification: %s", method)
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "SERVER_NAME", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            })

        if method == "ping":
            return self._result(msg_id, {})

        if method == "tools/list":
            return self._result(msg_id, {"tools": [TOOL_DEFINITION]})

        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)        # await ... (A-family)

        return self._error(msg_id, -32601, f"Method not found: {method}")
```

Canonical invariants verified by the smoke test:
- `initialize` → `protocolVersion == "2024-11-05"`, `serverInfo.version == "1.0.0"`.
- `notifications/initialized` (id is None) → **no response**, no error.
- `ping` → `result == {}`.
- unknown method → `error.code == -32601`.
- `tools/list` → exactly one tool, named `TOOL_NAME`.

The tool-call handler wraps its own work in try/except and returns `_tool_error`
(or an `isError` result) for *expected* failures, so a normal tool failure never
reaches the message-level catch-all.

---

## 5. run() loop — the message-level catch-all (FIX-1)

The read loop reads one JSON object per line. The **handler call is wrapped in
try/except, and the `-32603` error response is ALWAYS written** when a handler
raises unexpectedly. Silently swallowing the exception (logging without replying)
leaves the client blocked forever — that is the bug this shape fixes.

### 5a. Sync variant (pure-stdlib tooling servers)

```python
    async def run(self):
        loop = asyncio.get_running_loop()
        log.info("MCP server starting")
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
```

### 5b. Async variant (LSP / subprocess — A-family)

Identical except the handler is awaited, and the `finally` performs the server's
subprocess cleanup. **The `finally` body is server-specific and is NOT changed by
the convergence** (sub-shapes: `await _client.stop()` for clangd/cuda/lua-lsp;
`await self.manager.cleanup_all()` for gdc/lldb; context7 has no `finally`).

```python
    async def run(self):
        loop = asyncio.get_running_loop()
        log.debug("SERVER_NAME server ready (stdio)")
        # ... optional background auto-init task ...
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
                    log.debug("JSON parse error: %s", exc)
                    continue

                log.debug("← RAW: %s", line)
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
        finally:
            # server-specific cleanup — unchanged by convergence
            if _client is not None:
                await _client.stop()
```

> **Behavioral note (intentional):** with this shape, any exception that reaches
> the loop now produces a `-32603` reply on stdout. Tool handlers already return
> their own `_tool_error`, so an exception bubbling this far is genuinely
> unexpected — replying is correct, hanging is not.

---

## 6. main() — argparse + canonical logging setup

`--debug` and `--log-file` are universal. `--log-file` implies debug level. Default
level is `WARNING`. `logging.basicConfig(...)` runs **before** `asyncio.run(...)`
so any background auto-init task logs through the same configuration.

```python
def main():
    parser = argparse.ArgumentParser(description="SERVER_NAME — ...")
    # ... server-specific args ...
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    # LSP trio also: parser.add_argument("--markdown", action="store_true", ...)
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

    # LSP trio: global MARKDOWN_MODE; if args.markdown: MARKDOWN_MODE = True

    server = McpServer(...)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
```

Every server (including the former "lite trio" lldb/gdc/context7) exposes BOTH
`--debug` and `--log-file` for full CLI parity.

---

## 7. Convergence checklist (per file)

- [ ] shebang + PEP-723 block (`dependencies = []` if stdlib-only; exact list otherwise)
- [ ] `import logging`; module-level `log = logging.getLogger("SERVER_NAME")`; no `debug_log`, no `DEBUG`/`_log_file` globals
- [ ] static `_result` / `_error` / `_tool_error` (legacy `_ok`/`_err` renamed)
- [ ] `initialize` → `{protocolVersion, serverInfo, capabilities}`, version `"1.0.0"`
- [ ] `ping` → `_result(msg_id, {})`; notifications → `None`; unknown → `-32601`
- [ ] run() loop wraps the handler in try/except and **writes** a `-32603` reply
- [ ] `main()` canonical logging block; `--debug` + `--log-file` present
- [ ] `MARKDOWN_MODE` / subprocess `finally` cleanup left intact where present
