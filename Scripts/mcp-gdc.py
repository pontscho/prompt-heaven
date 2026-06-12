#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Chrome DevTools Protocol MCP Server — standalone, no external dependencies.

Design:
  tools/list  → exposes only 'gdc_call' (minimal token footprint)
  tools/call  → dispatches all CDP tools (documented in gdc-mcp skill)

Usage:
  python3 mcp-gdc.py [--browser-url URL] [--debug]
"""

import os
import sys
import json
import uuid
import base64
import hashlib
import struct
import asyncio
import argparse
import logging
import urllib.request
import urllib.parse
from typing import Dict, List, Optional, Any, Callable


log = logging.getLogger("mcp-gdc")


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


def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool.

    The wire frequently carries booleans as strings ("false"/"0"/"no"), where a
    naive bool("false") would wrongly yield True.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)


# ============================================================
# Log helpers (FIX-4: bounded log growth)
# ============================================================

_MAX_LOG = 1000


def _append_log(log: list, entry: dict) -> None:
    log.append(entry)
    if len(log) > _MAX_LOG:
        del log[:len(log) - _MAX_LOG]


# ============================================================
# WebSocket client (stdlib only, RFC 6455)
# ============================================================

async def _ws_connect(host: str, port: int, path: str):
    """Establish a WebSocket connection. Returns (reader, writer)."""
    reader, writer = await asyncio.open_connection(host, port)

    key = base64.b64encode(os.urandom(16)).decode()

    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    writer.write(handshake.encode())
    await writer.drain()

    # Read HTTP response headers
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = await reader.read(4096)
        if not chunk:
            raise ConnectionError("WebSocket handshake failed: connection closed")
        response += chunk

    status_line = response.split(b"\r\n")[0].decode()
    if "101" not in status_line:
        raise ConnectionError(f"WebSocket handshake rejected: {status_line}")

    # Verify Sec-WebSocket-Accept
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    if expected not in response.decode(errors="ignore"):
        raise ConnectionError("WebSocket handshake failed: invalid accept key")

    log.debug(f"WebSocket connected: {host}:{port}{path}")
    return reader, writer


async def _ws_recv(reader) -> Optional[str]:
    """Receive a WebSocket frame and return text payload (None on close)."""
    header = await reader.readexactly(2)
    opcode = header[0] & 0x0F
    masked = (header[1] & 0x80) != 0
    payload_len = header[1] & 0x7F

    if payload_len == 126:
        ext = await reader.readexactly(2)
        payload_len = struct.unpack(">H", ext)[0]
    elif payload_len == 127:
        ext = await reader.readexactly(8)
        payload_len = struct.unpack(">Q", ext)[0]

    mask = None
    if masked:
        mask = await reader.readexactly(4)

    payload = await reader.readexactly(payload_len)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    if opcode == 0x8:   # Close
        return None
    elif opcode in (0x9, 0xA):  # Ping / Pong — skip
        return await _ws_recv(reader)
    elif opcode in (0x1, 0x2):  # Text / Binary
        return payload.decode("utf-8", errors="replace")

    return None


async def _ws_send(writer, text: str) -> None:
    """Send a masked WebSocket text frame (client → server MUST be masked per RFC 6455)."""
    payload = text.encode("utf-8")
    length = len(payload)
    mask_key = os.urandom(4)
    masked_payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    header = bytearray()
    header.append(0x81)  # FIN=1 + opcode=text(1)
    if length <= 125:
        header.append(0x80 | length)
    elif length <= 65535:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    header.extend(mask_key)

    writer.write(bytes(header) + masked_payload)
    await writer.drain()


# ============================================================
# CDP Session
# ============================================================

class CdpSession:
    def __init__(self, target_id: str, ws_url: str):
        self.target_id = target_id
        self.ws_url = ws_url
        self.reader = None
        self.writer = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._listeners: Dict[str, List[Callable]] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._msg_id = 0
        self.console_log: List[dict] = []
        self.network_log: List[dict] = []
        self._connected = False
        self._load_event = asyncio.Event()  # set when Page.loadEventFired

    async def connect(self) -> None:
        """Parse ws_url, open WebSocket, enable CDP domains."""
        url = self.ws_url
        if url.startswith("ws://"):
            url = url[5:]
        elif url.startswith("wss://"):
            url = url[6:]

        if "/" in url:
            host_port, path = url.split("/", 1)
            path = "/" + path
        else:
            host_port = url
            path = "/"

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 80

        self.reader, self.writer = await _ws_connect(host, port, path)
        self._connected = True
        self._recv_task = asyncio.create_task(self._recv_loop())

        # Enable CDP domains
        await self.send("Page.enable", {})
        await self.send("Runtime.enable", {})
        await self.send("Network.enable", {})
        await self.send("Console.enable", {})
        try:
            await self.send("Accessibility.enable", {})
        except Exception:
            pass

        # Track page load completion
        self._listeners.setdefault("Page.loadEventFired", []).append(
            lambda e: self._load_event.set()
        )

        # FIX-5: fail pending futures when execution context is destroyed
        self._listeners.setdefault("Runtime.executionContextDestroyed", []).append(
            lambda e: self._fail_all_pending(ConnectionError("Execution context destroyed"))
        )

        # Register event listeners for in-memory logs (FIX-4: bounded via _append_log)
        self._listeners.setdefault("Console.messageAdded", []).append(
            lambda e: _append_log(self.console_log, e.get("message", {}))
        )
        self._listeners.setdefault("Runtime.consoleAPICalled", []).append(
            lambda e: _append_log(self.console_log, {
                "level": e.get("type", "log"),
                "text": " ".join(
                    a.get("value", str(a)) for a in e.get("args", [])
                ),
                "source": "console-api",
            })
        )
        self._listeners.setdefault("Network.requestWillBeSent", []).append(
            lambda e: _append_log(self.network_log, {
                "type": "request",
                "requestId": e.get("requestId"),
                "url": e.get("request", {}).get("url"),
                "method": e.get("request", {}).get("method"),
                "resourceType": e.get("type"),
                "timestamp": e.get("timestamp"),
            })
        )
        self._listeners.setdefault("Network.responseReceived", []).append(
            lambda e: _append_log(self.network_log, {
                "type": "response",
                "requestId": e.get("requestId"),
                "url": e.get("response", {}).get("url"),
                "status": e.get("response", {}).get("status"),
                "mimeType": e.get("response", {}).get("mimeType"),
                "timestamp": e.get("timestamp"),
            })
        )

        log.debug(f"CDP session ready: {self.target_id}")

    async def send(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """Send a CDP command and await the response."""
        if not self._connected:
            raise RuntimeError("CDP session not connected")

        self._msg_id += 1
        msg_id = self._msg_id

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut

        message = json.dumps({"id": msg_id, "method": method, "params": params})
        await _ws_send(self.writer, message)

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP command timed out: {method}")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(f"CDP error in {method}: {err.get('message', str(err))}")

        return result.get("result", {})

    async def _recv_loop(self) -> None:
        """Background task: receive CDP messages and dispatch to futures/listeners."""
        try:
            while self._connected:
                text = await _ws_recv(self.reader)
                if text is None:
                    log.debug(f"CDP connection closed: {self.target_id}")
                    break

                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue

                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    method = msg["method"]
                    params = msg.get("params", {})
                    for listener in self._listeners.get(method, []):
                        try:
                            listener(params)
                        except Exception as e:
                            log.debug(f"Listener error [{method}]: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"Recv loop error [{self.target_id}]: {e}")
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("CDP connection closed"))
            self._pending.clear()

    def _fail_all_pending(self, exc: Exception) -> None:
        """Fail all pending CDP futures with the given exception (FIX-5)."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def close(self) -> None:
        """Close the WebSocket connection and cancel the receive task."""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


# ============================================================
# GDC Manager
# ============================================================

class GdcManager:
    def __init__(self, browser_url: str):
        self.browser_url = browser_url.rstrip("/")
        self.sessions: Dict[str, CdpSession] = {}
        self.selected_id: Optional[str] = None

    def get_targets(self) -> List[dict]:
        """Fetch target list via HTTP GET /json."""
        url = f"{self.browser_url}/json"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"Cannot reach Chrome at {self.browser_url}: {e}")

    async def get_session(self, target_id: str) -> CdpSession:
        """Get or lazily create a CDP session for a target."""
        if target_id in self.sessions:
            return self.sessions[target_id]

        targets = self.get_targets()
        target = next((t for t in targets if t.get("id") == target_id), None)
        if not target:
            raise ValueError(f"Target not found: {target_id}")

        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise ValueError(f"Target has no WebSocket debugger URL: {target_id}")

        session = CdpSession(target_id, ws_url)
        await session.connect()
        self.sessions[target_id] = session
        return session

    async def get_selected(self) -> CdpSession:
        """Return the active session; auto-select first page if none selected."""
        if self.selected_id:
            session = self.sessions.get(self.selected_id)
            if session and session._connected:
                return session
            # Session is stale (disconnected / broken pipe) — drop it and reconnect
            if session:
                self.sessions.pop(self.selected_id, None)
            self.selected_id = None

        targets = self.get_targets()
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise RuntimeError(
                "No pages available. Open a page in Chrome first, "
                "or use new_page to create one."
            )

        target_id = pages[0]["id"]
        session = await self.get_session(target_id)
        self.selected_id = target_id
        return session

    async def cleanup_all(self) -> None:
        for session in list(self.sessions.values()):
            await session.close()
        self.sessions.clear()


# ============================================================
# Tool Handlers
# ============================================================

# --- Meta ---

async def handle_gdc_status(mgr: GdcManager, args: dict) -> str:
    count = len(mgr.sessions)
    selected = mgr.selected_id or "none"
    try:
        targets = mgr.get_targets()
        page_count = sum(1 for t in targets if t.get("type") == "page")
        return (
            f"GDC MCP server running.\n"
            f"Browser: {mgr.browser_url}\n"
            f"Active sessions: {count}\n"
            f"Selected target: {selected}\n"
            f"Chrome pages: {page_count}"
        )
    except Exception as e:
        return (
            f"GDC MCP server running.\n"
            f"Browser: {mgr.browser_url} (unreachable: {e})\n"
            f"Active sessions: {count}\n"
            f"Selected target: {selected}"
        )


# --- Navigation ---

async def handle_list_pages(mgr: GdcManager, args: dict) -> str:
    targets = mgr.get_targets()
    if not targets:
        return "No targets found."

    lines = [f"Targets ({len(targets)}):"]
    for t in targets:
        tid = t.get("id", "?")
        title = t.get("title", "")
        url = t.get("url", "")
        ttype = t.get("type", "?")
        selected = " [selected]" if tid == mgr.selected_id else ""
        lines.append(f"  [{ttype}] {tid}{selected}")
        lines.append(f"    title: {title}")
        lines.append(f"    url:   {url}")
    return "\n".join(lines)


async def handle_select_page(mgr: GdcManager, args: dict) -> str:
    target_id = args.get("target_id", "")
    if not target_id:
        return "Error: target_id required"

    await mgr.get_session(target_id)
    mgr.selected_id = target_id
    return f"Selected page: {target_id}"


async def handle_new_page(mgr: GdcManager, args: dict) -> str:
    url = args.get("url", "about:blank")

    # FIX-3: prefer CDP Target.createTarget (avoids HTTP query-string URL encoding issues)
    if mgr.sessions:
        try:
            session = next(iter(mgr.sessions.values()))
            result = await session.send("Target.createTarget", {"url": url})
            target_id = result.get("targetId")
            if target_id:
                mgr.selected_id = target_id
                return f"New page created: {target_id}\nURL: {url}"
        except Exception:
            pass  # fall through to HTTP fallback

    # Fallback: HTTP /json/new with properly encoded URL
    try:
        encoded_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=")
        create_url = f"{mgr.browser_url}/json/new?{encoded_url}"
        with urllib.request.urlopen(create_url, timeout=10) as resp:
            target = json.loads(resp.read().decode())
        target_id = target.get("id")
        mgr.selected_id = target_id
        return f"New page created: {target_id}\nURL: {url}"
    except Exception as e:
        return f"Failed to create page: {e}"


async def handle_close_page(mgr: GdcManager, args: dict) -> str:
    target_id = args.get("target_id") or mgr.selected_id
    if not target_id:
        return "Error: no target selected and no target_id provided"

    session = mgr.sessions.pop(target_id, None)
    if session:
        await session.close()
    if mgr.selected_id == target_id:
        mgr.selected_id = None

    try:
        close_url = f"{mgr.browser_url}/json/close/{target_id}"
        with urllib.request.urlopen(close_url, timeout=5) as resp:
            result = resp.read().decode()
        return f"Closed page: {target_id}\n{result}"
    except Exception as e:
        return f"Close request sent (page may still have closed): {e}"


async def handle_navigate(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    url = args.get("url")
    action = args.get("action")

    if url:
        # Clear the load event before navigating so we wait for the NEW load
        session._load_event.clear()
        result = await session.send("Page.navigate", {"url": url})
        frame_id = result.get("frameId", "")
        error_text = result.get("errorText", "")
        if error_text:
            return f"Navigation error: {error_text}"
        # Wait for Page.loadEventFired — ensures DOM and JS are ready
        try:
            await asyncio.wait_for(session._load_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass  # proceed even if load event never fires (e.g. error pages)
        return f"Navigated to: {url}\nFrame: {frame_id}"
    elif action == "reload":
        session._load_event.clear()
        await session.send("Page.reload", {})
        try:
            await asyncio.wait_for(session._load_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass
        return "Page reloaded"
    elif action == "back":
        session._load_event.clear()
        await session.send("Runtime.evaluate", {
            "expression": "history.back()",
            "returnByValue": True,
        })
        try:
            # cached pages may not fire load event — short timeout, silent fail
            await asyncio.wait_for(session._load_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        return "Navigated back"
    elif action == "forward":
        session._load_event.clear()
        await session.send("Runtime.evaluate", {
            "expression": "history.forward()",
            "returnByValue": True,
        })
        try:
            await asyncio.wait_for(session._load_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        return "Navigated forward"
    else:
        return "Error: provide 'url' or 'action' (back|forward|reload)"


async def handle_wait_for(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    text = args.get("text", "")
    timeout = float(args.get("timeout", 10.0))

    if not text:
        return "Error: text required"

    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        elapsed = loop.time() - start
        if elapsed > timeout:
            return f"Timeout after {timeout:.1f}s: text not found: {text!r}"

        try:
            # Short per-attempt timeout: execution context may be destroyed during navigation
            result = await asyncio.wait_for(
                session.send("Runtime.evaluate", {
                    "expression": f"document.body ? document.body.innerText.includes({json.dumps(text)}) : false",
                    "returnByValue": True,
                }),
                timeout=3.0,
            )
            if result.get("result", {}).get("value") is True:
                return f"Text found: {text!r} (after {elapsed:.1f}s)"
        except Exception:
            pass  # context destroyed, pipe broken, etc — retry until outer timeout

        await asyncio.sleep(0.5)


# --- Input ---

async def handle_click(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    selector = args.get("selector", "")
    if not selector:
        return "Error: selector required"

    expression = """
    (function(sel) {
        const el = document.querySelector(sel);
        if (!el) return 'Element not found: ' + sel;
        el.scrollIntoView({block: 'center'});
        el.click();
        return 'Clicked: ' + sel;
    })(%s)
    """ % json.dumps(selector)

    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    return str(result.get("result", {}).get("value", ""))


async def handle_click_at(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    x = args.get("x", 0)
    y = args.get("y", 0)

    for event_type in ["mousePressed", "mouseReleased"]:
        await session.send("Input.dispatchMouseEvent", {
            "type": event_type,
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
        })
    return f"Clicked at ({x}, {y})"


async def handle_type_text(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    text = args.get("text", "")
    if not text:
        return "Error: text required"

    await session.send("Input.insertText", {"text": text})
    return f"Typed: {text!r}"


async def handle_fill(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    selector = args.get("selector", "")
    value = args.get("value", "")

    if not selector:
        return "Error: selector required"

    expression = """
    (function(sel, val) {
        const el = document.querySelector(sel);
        if (!el) return 'Element not found: ' + sel;
        el.focus();
        el.value = val;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return 'Filled: ' + sel;
    })(%s, %s)
    """ % (json.dumps(selector), json.dumps(value))

    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    return str(result.get("result", {}).get("value", ""))


async def handle_press_key(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    key = args.get("key", "")
    if not key:
        return "Error: key required"

    for event_type in ["keyDown", "keyUp"]:
        await session.send("Input.dispatchKeyEvent", {
            "type": event_type,
            "key": key,
            "code": f"Key{key.upper()}" if len(key) == 1 else key,
        })
    return f"Key pressed: {key}"


async def handle_handle_dialog(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    accept = _bool_param(args.get("accept"), default=True)
    prompt_text = args.get("prompt_text", "")

    params: dict = {"accept": accept}
    if prompt_text:
        params["promptText"] = prompt_text

    await session.send("Page.handleJavaScriptDialog", params)
    action = "accepted" if accept else "dismissed"
    return f"Dialog {action}"


async def handle_resize_page(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    width = args.get("width", 1280)
    height = args.get("height", 720)

    await session.send("Emulation.setVisibleSize", {
        "width": width,
        "height": height,
    })
    return f"Resized to {width}x{height}"


async def handle_scroll(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    x = args.get("x", 0)
    y = args.get("y", 0)
    delta_x = args.get("delta_x", 0)
    delta_y = args.get("delta_y", 100)

    await session.send("Input.dispatchMouseEvent", {
        "type": "mouseWheel",
        "x": x,
        "y": y,
        "deltaX": delta_x,
        "deltaY": delta_y,
    })
    return f"Scrolled at ({x}, {y}): delta=({delta_x}, {delta_y})"


# --- Debugging ---

async def handle_take_screenshot(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    fmt = args.get("format", "png")
    quality = args.get("quality", 80)
    full_page = _bool_param(args.get("full_page"), default=False)

    params: dict = {"format": fmt}
    if fmt == "jpeg":
        params["quality"] = quality
    if full_page:
        params["captureBeyondViewport"] = True

    result = await session.send("Page.captureScreenshot", params, timeout=30.0)
    data = result.get("data", "")
    if not data:
        return "Screenshot failed: no data returned"

    save_path = args.get("savePath") or args.get("save_path")
    raw_path = args.get("path", "/tmp")
    if save_path:
        # savePath is a full file path — ensure parent directory exists
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        filename = save_path
    elif raw_path != "/tmp" and os.path.splitext(raw_path)[1]:
        # path looks like a full file path (has extension) — treat as savePath
        os.makedirs(os.path.dirname(raw_path) or ".", exist_ok=True)
        filename = raw_path
    else:
        # path is a directory — append generated filename
        filename = f"{raw_path}/gdc-screenshot-{uuid.uuid4()}.{fmt}"

    with open(filename, "wb") as f:
        f.write(base64.b64decode(data))

    return f"Screenshot saved: {filename}"


async def handle_evaluate(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    expression = args.get("expression", "")
    if not expression:
        return "Error: expression required"

    # FIX-5: 10s timeout (30s caused hangs on context destruction)
    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    }, timeout=10.0)

    exc = result.get("exceptionDetails")
    if exc:
        # "text" is always "Uncaught"; the useful message is in exception.description
        exc_desc = exc.get("exception", {}).get("description", "")
        exc_text = exc.get("text", "")
        exc_msg = exc_desc or exc_text or str(exc)
        return f"Exception: {exc_msg}"

    rv = result.get("result", {})
    value = rv.get("value")
    rtype = rv.get("type", "")
    rdescription = rv.get("description", "")

    if value is not None:
        return f"Result ({rtype}): {json.dumps(value)}"
    elif rdescription:
        return f"Result ({rtype}): {rdescription}"
    else:
        return f"Result ({rtype}): undefined"


async def handle_list_console_messages(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    level_filter = args.get("level")

    msgs = session.console_log
    if level_filter:
        msgs = [m for m in msgs if m.get("level") == level_filter]

    if not msgs:
        return "No console messages."

    lines = [f"Console messages ({len(msgs)}):"]
    for msg in msgs[-50:]:
        lvl = msg.get("level", "?")
        text = msg.get("text", "")
        lines.append(f"  [{lvl}] {text}")
    return "\n".join(lines)


async def handle_take_snapshot(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()

    try:
        result = await session.send("Accessibility.getFullAXTree", {}, timeout=30.0)
    except Exception as e:
        return f"Failed to get accessibility tree: {e}"

    nodes = result.get("nodes", [])
    if not nodes:
        return "Accessibility tree is empty."

    # FIX-1: build dict upfront to avoid O(n²) linear search per child
    node_map = {n["nodeId"]: n for n in nodes}

    def fmt_node(node: dict, depth: int) -> List[str]:
        role = node.get("role", {}).get("value", "?")
        name = node.get("name", {}).get("value", "")
        prefix = "  " * depth
        lines = [f"{prefix}[{role}] {name}".rstrip()]
        for child_id in node.get("childIds", []):
            child = node_map.get(child_id)
            if child:
                lines.extend(fmt_node(child, depth + 1))
        return lines

    # Find root nodes (nodes not referenced as a child of any other node)
    all_child_ids: set = set()
    for n in nodes:
        all_child_ids.update(n.get("childIds", []))
    roots = [n for n in nodes if n.get("nodeId") not in all_child_ids]

    lines = [f"Accessibility tree ({len(nodes)} nodes):"]
    for root in roots[:1]:
        lines.extend(fmt_node(root, 0))

    if len(lines) > 100:
        lines = lines[:100]
        lines.append("... (truncated)")

    return "\n".join(lines)


# --- Network ---

async def handle_list_network_requests(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    type_filter = args.get("type")
    limit = int(args.get("limit", 50))

    requests = [e for e in session.network_log if e.get("type") == "request"]
    if type_filter:
        requests = [r for r in requests if r.get("resourceType") == type_filter]

    requests = requests[-limit:]

    if not requests:
        return "No network requests recorded."

    lines = [f"Network requests ({len(requests)}):"]
    for req in requests:
        rid = req.get("requestId", "?")
        method = req.get("method", "?")
        url = req.get("url", "?")
        rtype = req.get("resourceType", "?")
        lines.append(f"  [{method}] {rid} ({rtype})")
        lines.append(f"    {url}")
    return "\n".join(lines)


async def handle_get_network_request(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    request_id = args.get("request_id", "")
    if not request_id:
        return "Error: request_id required"

    entries = [e for e in session.network_log if e.get("requestId") == request_id]

    lines = [f"Request: {request_id}"]
    for e in entries:
        lines.append(json.dumps(e, indent=2))

    try:
        result = await session.send(
            "Network.getResponseBody", {"requestId": request_id}
        )
        body = result.get("body", "")
        is_b64 = result.get("base64Encoded", False)
        if is_b64:
            lines.append(f"\nResponse body: [base64 encoded, {len(body)} chars]")
        elif len(body) > 2000:
            lines.append(f"\nResponse body (truncated):\n{body[:2000]}\n...")
        else:
            lines.append(f"\nResponse body:\n{body}")
    except Exception as e:
        lines.append(f"\nBody unavailable: {e}")

    return "\n".join(lines)


# --- Emulation ---

async def handle_emulate(mgr: GdcManager, args: dict) -> str:
    session = await mgr.get_selected()
    viewport = args.get("viewport")
    user_agent = args.get("user_agent")
    network = args.get("network")

    results = []

    if viewport:
        width = viewport.get("width", 1280)
        height = viewport.get("height", 720)
        device_scale = viewport.get("deviceScaleFactor", 1)
        mobile = viewport.get("mobile", False)

        await session.send("Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": device_scale,
            "mobile": mobile,
        })
        results.append(f"Viewport: {width}x{height} (scale={device_scale}, mobile={mobile})")

    if user_agent:
        await session.send("Emulation.setUserAgentOverride", {"userAgent": user_agent})
        results.append(f"User-Agent: {user_agent}")

    if network is not None:
        _presets = {
            "offline":  {"offline": True,  "downloadThroughput": 0,       "uploadThroughput": 0,      "latency": 0},
            "slow3g":   {"offline": False, "downloadThroughput": 62500,    "uploadThroughput": 62500,  "latency": 400},
            "fast3g":   {"offline": False, "downloadThroughput": 196608,   "uploadThroughput": 98304,  "latency": 150},
            "reset":    {"offline": False, "downloadThroughput": -1,       "uploadThroughput": -1,     "latency": 0},
        }
        if isinstance(network, str) and network in _presets:
            params = _presets[network]
        elif isinstance(network, dict):
            params = network
        else:
            params = _presets["reset"]

        await session.send("Network.emulateNetworkConditions", params)
        results.append(f"Network: {network}")

    if not results:
        return "Error: provide at least one of: viewport, user_agent, network"

    return "Emulation applied:\n" + "\n".join(f"  {r}" for r in results)


# --- Extensions ---

async def handle_hover(mgr: GdcManager, args: dict) -> str:
    """EXT-1: Move mouse over element or coordinates."""
    session = await mgr.get_selected()
    selector = args.get("selector")
    x = args.get("x")
    y = args.get("y")

    if selector:
        expression = """
        (function(sel) {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.left + r.width / 2, y: r.top + r.height / 2};
        })(%s)
        """ % json.dumps(selector)
        result = await session.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        val = result.get("result", {}).get("value")
        if not val:
            return f"Element not found: {selector}"
        x, y = val["x"], val["y"]
    elif x is None or y is None:
        return "Error: provide 'selector' or both 'x' and 'y'"

    await session.send("Input.dispatchMouseEvent", {
        "type": "mouseMoved",
        "x": x,
        "y": y,
    })
    return f"Hovered at ({x:.1f}, {y:.1f})"


async def handle_get_cookies(mgr: GdcManager, args: dict) -> str:
    """EXT-2: Get cookies, optionally filtered by URL."""
    session = await mgr.get_selected()
    params: dict = {}
    if "url" in args:
        params["urls"] = [args["url"]]
    result = await session.send("Network.getCookies", params)
    cookies = result.get("cookies", [])
    if not cookies:
        return "No cookies found."
    lines = [f"Cookies ({len(cookies)}):"]
    for c in cookies:
        lines.append(
            f"  {c.get('name')}={c.get('value')} "
            f"(domain={c.get('domain')}, path={c.get('path')})"
        )
    return "\n".join(lines)


async def handle_set_cookie(mgr: GdcManager, args: dict) -> str:
    """EXT-2: Set a cookie."""
    session = await mgr.get_selected()
    name = args.get("name")
    if not name:
        return "Error: name required"
    params: dict = {"name": name, "value": args.get("value", "")}
    for key in ("url", "domain", "path", "httpOnly", "secure", "expires"):
        if key in args:
            params[key] = args[key]
    await session.send("Network.setCookie", params)
    return f"Cookie set: {name}"


async def handle_wait_for_selector(mgr: GdcManager, args: dict) -> str:
    """EXT-3: Poll until a CSS selector appears in the DOM."""
    session = await mgr.get_selected()
    selector = args.get("selector", "")
    timeout = float(args.get("timeout", 10.0))
    visible = _bool_param(args.get("visible"), default=False)

    if not selector:
        return "Error: selector required"

    sel_json = json.dumps(selector)
    if visible:
        expression = (
            f"(function(sel) {{"
            f" const el = document.querySelector(sel);"
            f" return el !== null && el.offsetParent !== null;"
            f"}})({sel_json})"
        )
    else:
        expression = f"document.querySelector({sel_json}) !== null"

    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        elapsed = loop.time() - start
        if elapsed > timeout:
            return f"Timeout after {timeout:.1f}s: selector not found: {selector!r}"
        try:
            result = await asyncio.wait_for(
                session.send("Runtime.evaluate", {
                    "expression": expression,
                    "returnByValue": True,
                }),
                timeout=3.0,
            )
            if result.get("result", {}).get("value") is True:
                return f"Selector found: {selector!r} (after {elapsed:.1f}s)"
        except Exception:
            pass
        await asyncio.sleep(0.25)


async def handle_get_html(mgr: GdcManager, args: dict) -> str:
    """EXT-4: Get outerHTML of a selector or the full document."""
    session = await mgr.get_selected()
    selector = args.get("selector")
    _MAX_HTML = 50000

    if selector:
        expression = """
        (function(sel) {
            const el = document.querySelector(sel);
            if (!el) return null;
            return el.outerHTML;
        })(%s)
        """ % json.dumps(selector)
    else:
        expression = "document.documentElement.outerHTML"

    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    val = result.get("result", {}).get("value")
    if val is None:
        return f"Element not found: {selector}" if selector else "Error: could not get HTML"
    if len(val) > _MAX_HTML:
        return val[:_MAX_HTML] + f"\n... (truncated, {len(val)} total chars)"
    return val


async def handle_select_option(mgr: GdcManager, args: dict) -> str:
    """EXT-5: Set a <select> dropdown value by value or label text."""
    session = await mgr.get_selected()
    selector = args.get("selector", "")
    value = args.get("value")
    label = args.get("label")

    if not selector:
        return "Error: selector required"
    if value is None and label is None:
        return "Error: provide 'value' or 'label'"

    if label is not None:
        expression = """
        (function(sel, lbl) {
            const el = document.querySelector(sel);
            if (!el) return 'Element not found: ' + sel;
            const opt = Array.from(el.options).find(o => o.text === lbl);
            if (!opt) return 'Option label not found: ' + lbl;
            el.value = opt.value;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'Selected: ' + opt.value;
        })(%s, %s)
        """ % (json.dumps(selector), json.dumps(label))
    else:
        expression = """
        (function(sel, val) {
            const el = document.querySelector(sel);
            if (!el) return 'Element not found: ' + sel;
            el.value = val;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'Selected: ' + val;
        })(%s, %s)
        """ % (json.dumps(selector), json.dumps(value))

    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    return str(result.get("result", {}).get("value", ""))


async def handle_inject_script(mgr: GdcManager, args: dict) -> str:
    """EXT-6: Inject persistent JS that runs on every new document."""
    session = await mgr.get_selected()
    expression = args.get("expression", "")
    world = args.get("world", "main")

    if not expression:
        return "Error: expression required"

    params: dict = {"source": expression}
    if world != "main":
        params["worldName"] = world

    result = await session.send("Page.addScriptToEvaluateOnNewDocument", params)
    identifier = result.get("identifier", "")
    return f"Script injected: {identifier}"


async def handle_remove_injected_script(mgr: GdcManager, args: dict) -> str:
    """EXT-6: Remove a previously injected persistent script."""
    session = await mgr.get_selected()
    identifier = args.get("identifier", "")

    if not identifier:
        return "Error: identifier required"

    await session.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier})
    return f"Injected script removed: {identifier}"


async def handle_clear_field(mgr: GdcManager, args: dict) -> str:
    """EXT-7: Clear an input field (works with React/Vue controlled inputs)."""
    session = await mgr.get_selected()
    selector = args.get("selector", "")

    if not selector:
        return "Error: selector required"

    # Focus and select all content
    focus_expr = """
    (function(sel) {
        const el = document.querySelector(sel);
        if (!el) return 'Element not found: ' + sel;
        el.focus();
        if (el.select) el.select();
        return 'focused';
    })(%s)
    """ % json.dumps(selector)

    result = await session.send("Runtime.evaluate", {"expression": focus_expr, "returnByValue": True})
    val = result.get("result", {}).get("value", "")
    if str(val).startswith("Element not found"):
        return str(val)

    # Ctrl+A to select all, then Delete
    for event_type in ("keyDown", "keyUp"):
        await session.send("Input.dispatchKeyEvent", {
            "type": event_type,
            "key": "a",
            "code": "KeyA",
            "modifiers": 2,  # Ctrl
        })
    for event_type in ("keyDown", "keyUp"):
        await session.send("Input.dispatchKeyEvent", {
            "type": event_type,
            "key": "Delete",
            "code": "Delete",
        })

    # Also clear via native value setter for React/Vue controlled inputs
    clear_expr = """
    (function(sel) {
        const el = document.querySelector(sel);
        if (!el) return 'ok';
        const proto = el.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value');
        if (setter && setter.set) {
            setter.set.call(el, '');
        } else {
            el.value = '';
        }
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return 'cleared';
    })(%s)
    """ % json.dumps(selector)

    await session.send("Runtime.evaluate", {"expression": clear_expr, "returnByValue": True})
    return f"Cleared: {selector}"


async def handle_find_element(mgr: GdcManager, args: dict) -> str:
    """EXT-8: Get position, size, text and visibility of an element."""
    session = await mgr.get_selected()
    selector = args.get("selector", "")

    if not selector:
        return "Error: selector required"

    expression = """
    (function(sel) {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
            x: r.left + r.width / 2,
            y: r.top + r.height / 2,
            left: r.left,
            top: r.top,
            width: r.width,
            height: r.height,
            text: (el.innerText || el.textContent || '').slice(0, 200),
            visible: el.offsetParent !== null,
            tag: el.tagName.toLowerCase(),
        };
    })(%s)
    """ % json.dumps(selector)

    result = await session.send("Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    val = result.get("result", {}).get("value")
    if val is None:
        return f"Element not found: {selector}"

    text = val.get("text", "")
    return (
        f"Element: {selector}\n"
        f"  tag:      {val.get('tag')}\n"
        f"  center:   ({val.get('x'):.1f}, {val.get('y'):.1f})\n"
        f"  size:     {val.get('width'):.1f}x{val.get('height'):.1f}\n"
        f"  visible:  {val.get('visible')}\n"
        f"  text:     {text!r}"
    )


# --- Dispatcher ---

async def handle_gdc_call(mgr: GdcManager, args: dict) -> str:
    """Dispatcher: call any GDC tool by name via the gdc-mcp skill."""
    function = args.get("function") or args.get("f") or ""
    raw_params = args.get("params") or args.get("p") or {}
    try:
        params = _ensure_dict(raw_params)
    except ValueError as exc:
        return f"Error: {exc}"

    if not function:
        return await handle_gdc_status(mgr, {})

    if function == "gdc_call":
        return "Cannot dispatch gdc_call recursively"

    handler = ALL_HANDLERS.get(function)
    if handler is None:
        available = ", ".join(sorted(ALL_HANDLERS.keys()))
        return f"Unknown function: '{function}'. Available: {available}"

    return await handler(mgr, params)


# ============================================================
# MCP Tool Registry
# ============================================================

# Only one tool appears in tools/list (minimal token footprint).
# All CDP tools are reachable via gdc_call (see gdc-mcp skill).
LISTED_TOOLS = [
    {
        "name": "gdc_call",
        "description": (
            "Call any Chrome DevTools function by name. "
            "Returns server status if called without 'function'. "
            "Invoke the gdc-mcp skill for the full list of available functions and their parameters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {
                    "type": "string",
                    "description": "Function name (e.g. navigate, take_screenshot). Alias: 'f'",
                },
                "f": {
                    "type": "string",
                    "description": "Alias for 'function'",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the function (see gdc-mcp skill for schema). Alias: 'p'",
                },
                "p": {
                    "type": "object",
                    "description": "Alias for 'params'",
                },
            },
            "required": [],
        },
    },
]

ALL_HANDLERS = {
    "gdc_status":                   handle_gdc_status,
    "gdc_call":                     handle_gdc_call,
    "list_pages":                   handle_list_pages,
    "select_page":                  handle_select_page,
    "new_page":                     handle_new_page,
    "close_page":                   handle_close_page,
    "navigate":                     handle_navigate,
    "wait_for":                     handle_wait_for,
    "click":                        handle_click,
    "click_at":                     handle_click_at,
    "type_text":                    handle_type_text,
    "fill":                         handle_fill,
    "press_key":                    handle_press_key,
    "handle_dialog":                handle_handle_dialog,
    "resize_page":                  handle_resize_page,
    "scroll":                       handle_scroll,
    "take_screenshot":              handle_take_screenshot,
    "evaluate":                     handle_evaluate,
    "list_console_messages":        handle_list_console_messages,
    "take_snapshot":                handle_take_snapshot,
    "list_network_requests":        handle_list_network_requests,
    "get_network_request":          handle_get_network_request,
    "emulate":                      handle_emulate,
    # Aliases
    "execute_js":                   handle_evaluate,
    # Extensions
    "hover":                        handle_hover,
    "get_cookies":                  handle_get_cookies,
    "set_cookie":                   handle_set_cookie,
    "wait_for_selector":            handle_wait_for_selector,
    "get_html":                     handle_get_html,
    "select_option":                handle_select_option,
    "inject_script":                handle_inject_script,
    "remove_injected_script":       handle_remove_injected_script,
    "clear_field":                  handle_clear_field,
    "find_element":                 handle_find_element,
}

TOOL_DESCRIPTIONS: Dict[str, str] = {
    # Meta
    "gdc_status":            "Server status: browser URL, sessions, selected target",
    # Navigation
    "list_pages":            "List all Chrome targets (pages, extensions, workers)",
    "select_page":           "Set active page by target_id; opens CDP session",
    "new_page":              "Open a new browser tab",
    "close_page":            "Close a tab (selected tab if target_id omitted)",
    "navigate":              "Navigate active page (url or action: back/forward/reload)",
    "wait_for":              "Poll document.body.innerText until text appears or timeout",
    # Input
    "click":                 "Click element by CSS selector",
    "click_at":              "Click at screen coordinates (x, y)",
    "type_text":             "Insert text at focused element",
    "fill":                  "Set form field value and fire input/change events",
    "press_key":             "Dispatch keyDown+keyUp for a key name",
    "handle_dialog":         "Accept or dismiss JS dialog (alert/confirm/prompt)",
    "resize_page":           "Set visible viewport size",
    "scroll":                "Dispatch mouseWheel event at coordinates",
    # Debugging
    "take_screenshot":       "Capture screenshot, save to /tmp, return file path",
    "evaluate":              "Run JavaScript in page context and return result",
    "list_console_messages": "Return in-memory console log (last 50 of 1000)",
    "take_snapshot":         "Return accessibility tree as text (max 100 lines)",
    # Network
    "list_network_requests": "List recorded network requests (last 1000)",
    "get_network_request":   "Get log entries and response body for a request ID",
    # Emulation
    "emulate":               "Override viewport, user-agent, or network conditions",
    # Extensions
    "hover":                 "Move mouse over element or coordinates",
    "get_cookies":           "Get cookies, optionally filtered by URL",
    "set_cookie":            "Set a cookie",
    "wait_for_selector":     "Poll until a CSS selector appears in the DOM",
    "get_html":              "Get outerHTML of a selector or the full document",
    "select_option":         "Set a <select> dropdown value by value or label",
    "inject_script":         "Inject persistent JS that runs on every new document",
    "remove_injected_script":"Remove a previously injected persistent script",
    "clear_field":           "Clear an input field (works with React/Vue controlled inputs)",
    "find_element":          "Get position, size, text and visibility of an element",
}


# ============================================================
# MCP Server — JSON-RPC 2.0 over stdio
# ============================================================

class McpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, browser_url: str):
        self.manager = GdcManager(browser_url)

    @staticmethod
    def _result(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(msg_id: Any, text: str) -> dict:
        return McpServer._result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    async def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        log.debug(f"<- {method} (id={msg_id})")

        if msg_id is None:
            return None

        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-gdc", "version": "1.0.0"},
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
                f"Unknown tool: '{name}'. Invoke the gdc-mcp skill for the full tool list."
            )

        try:
            result = await handler(self.manager, args)
            return self._result(msg_id, {"content": [{"type": "text", "text": result}]})
        except Exception as e:
            log.debug(f"Handler '{name}' error: {e}")
            return self._tool_error(msg_id, f"Error in {name}: {e}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        log.debug("mcp-gdc server ready (stdio)")

        try:
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

        finally:
            log.debug("Cleaning up all sessions")
            await self.manager.cleanup_all()


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chrome DevTools Protocol MCP Server — standalone, no external dependencies"
    )
    parser.add_argument(
        "--browser-url",
        default="http://127.0.0.1:9222",
        help="Chrome remote debugging URL (default: http://127.0.0.1:9222)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    parser.add_argument("--list", action="store_true", help="List all supported tool calls and exit")
    parsed = parser.parse_args()

    if parsed.list:
        tools = sorted(k for k in ALL_HANDLERS if k != "gdc_call")
        print(f"Supported tool calls ({len(tools)}):")
        width = max(len(n) for n in tools)
        for name in tools:
            desc = TOOL_DESCRIPTIONS.get(name, "")
            print(f"  {name:<{width}}  {desc}" if desc else f"  {name}")
        return

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

    server = McpServer(parsed.browser_url)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
