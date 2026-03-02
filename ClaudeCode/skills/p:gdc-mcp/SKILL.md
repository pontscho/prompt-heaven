---
name: p:gdc-mcp
description: >
  Full API reference for the GDC (Chrome DevTools) MCP server. Use when
  working with Chrome DevTools via MCP: browser automation, DOM inspection,
  network monitoring, screenshots, JavaScript execution, page navigation,
  form filling, input simulation, accessibility tree, console logs.
  One tool: gdc_call. All functions invoked via gdc_call dispatcher.
  Called without 'function' returns server status.
triggers:
  - gdc
  - gdc_call
  - chrome devtools
  - browser automation
  - take screenshot
  - navigate page
  - click element
  - fill form
  - evaluate javascript
  - DOM inspection
  - network requests
  - console messages
  - accessibility tree
---

# GDC MCP Skill — Full API Reference

Tool: `gdc_call`
Parameters: `function` (string), `params` (object, optional)

Called without `function` → returns server status (same as `gdc_status`).

## How to call any function

```
mcp__mcp-gdc__gdc_call(
  function = "<function_name>",
  params   = { ...parameters... }
)
```

**Example — navigate:**
```
mcp__mcp-gdc__gdc_call(function="navigate", params={"url": "https://example.com"})
```

> **Sessions**: CDP sessions open lazily on first use and persist until `close_page` or server shutdown. Invalid `target_id` returns a tool error immediately.

## Meta

### `gdc_status`
Server status: browser URL, active sessions, selected target, Chrome page count.

```json
{"function": "gdc_status"}
```

Returns:
```
GDC MCP server running.
Browser: <url>
Active sessions: <n>
Selected target: <target_id|"none">
Chrome pages: <n>
```

## Navigation

### `list_pages`
List all Chrome targets (pages, extensions, workers).

```json
{"function": "list_pages"}
```

Returns:
```
Targets (<n>):
  [<type>] <id> [selected]
    title: <title>
    url:   <url>
```
Returns `"No targets found."` if empty. Use `target_id` values from here with `select_page`.

### `select_page`
Set a target as the active page. Opens a CDP session if not already open.

```json
{"function": "select_page", "params": {"target_id": "ABC123"}}
```

- `target_id` — required. Get from `list_pages`.

Returns: `"Selected page: <target_id>"`. Error if target_id is not found or has no WebSocket URL.

### `new_page`
Open a new browser tab.

```json
{"function": "new_page", "params": {"url": "https://example.com"}}
```

- `url` — optional, default `"about:blank"`.

### `close_page`
Close a tab.

```json
{"function": "close_page"}
{"function": "close_page", "params": {"target_id": "ABC123"}}
```

- `target_id` — optional. Closes the currently selected tab if omitted.

### `navigate`
Navigate the active page. Use either `url` **or** `action` — not both.

```json
{"function": "navigate", "params": {"url": "https://example.com"}}
{"function": "navigate", "params": {"action": "reload"}}
{"function": "navigate", "params": {"action": "back"}}
{"function": "navigate", "params": {"action": "forward"}}
```

- `url` — navigate to URL; waits for load event (timeout: 30s).
- `action` — `"reload"` (30s timeout), `"back"` or `"forward"` (5s timeout each).

Returns: `"Navigated to: <url>\nFrame: <frameId>"` for URL, `"Page reloaded"` / `"Navigated back"` / `"Navigated forward"` for actions. Returns `"Navigation error: <text>"` on failure.

### `wait_for`
Poll `document.body.innerText` until `text` appears or timeout expires.

```json
{"function": "wait_for", "params": {"text": "Welcome"}}
{"function": "wait_for", "params": {"text": "Welcome", "timeout": 15}}
```

- `text` — required. Substring to wait for.
- `timeout` — optional, default `10.0` seconds. Poll interval: 0.5s.

Returns: `"Text found: '<text>' (after <elapsed>s)"` or `"Timeout after <timeout>s: text not found: '<text>'"`.

> **Note**: Polls visible rendered text only (`innerText`). Does not match hidden elements, `<script>`/`<style>` content, or text inside iframes. Use `evaluate()` for more complex conditions.

## Input

### `click`
Click an element by CSS selector (scrolls into view first).

```json
{"function": "click", "params": {"selector": "#submit-button"}}
```

- `selector` — required. CSS selector.

### `click_at`
Dispatch mousePressed + mouseReleased at screen coordinates.

```json
{"function": "click_at", "params": {"x": 400, "y": 300}}
```

- `x`, `y` — required. Screen coordinates in pixels.

### `type_text`
Insert text at the focused element (uses `Input.insertText`).

```json
{"function": "type_text", "params": {"text": "hello world"}}
```

- `text` — required.

### `fill`
Set a form field's value and fire `input`/`change` events.

```json
{"function": "fill", "params": {"selector": "input[name=email]", "value": "user@example.com"}}
```

- `selector` — required. CSS selector.
- `value` — required. Value to set.

### `press_key`
Dispatch keyDown + keyUp for a key name.

```json
{"function": "press_key", "params": {"key": "Enter"}}
{"function": "press_key", "params": {"key": "Tab"}}
{"function": "press_key", "params": {"key": "Escape"}}
```

- `key` — required. CDP key name (e.g. `"Enter"`, `"Tab"`, `"Escape"`, `"ArrowDown"`).

### `handle_dialog`
Accept or dismiss a JavaScript dialog (alert/confirm/prompt).

```json
{"function": "handle_dialog", "params": {"accept": true}}
{"function": "handle_dialog", "params": {"accept": false}}
{"function": "handle_dialog", "params": {"accept": true, "prompt_text": "my answer"}}
```

- `accept` — required. `true` to accept, `false` to dismiss.
- `prompt_text` — optional. Text for `prompt()` dialogs only.

### `resize_page`
Set the visible viewport size.

```json
{"function": "resize_page", "params": {"width": 1920, "height": 1080}}
```

- `width`, `height` — required. Pixels.

### `scroll`
Dispatch a mouseWheel event.

```json
{"function": "scroll", "params": {"x": 0, "y": 0, "delta_y": 300}}
{"function": "scroll", "params": {"x": 400, "y": 300, "delta_x": -100, "delta_y": 0}}
```

- `x`, `y` — required. Coordinates of the scroll event.
- `delta_y` — optional, default `0`. Positive = scroll down.
- `delta_x` — optional, default `0`. Positive = scroll right.

## Debugging

### `take_screenshot`
Capture screenshot, save to `/tmp/gdc-screenshot-<uuid>.<format>`, return file path.

```json
{"function": "take_screenshot"}
{"function": "take_screenshot", "params": {"format": "jpeg", "quality": 90}}
{"function": "take_screenshot", "params": {"full_page": true}}
```

- `format` — optional, `"png"` (default) or `"jpeg"`.
- `quality` — optional, default `80`. Only used for `"jpeg"`.
- `full_page` — optional, default `false`. Capture entire scrollable page.

Returns: `"Screenshot saved: /tmp/gdc-screenshot-<uuid>.<fmt>"`.

### `evaluate`
Run JavaScript in the page context and return the result. Promises are awaited.

```json
{"function": "evaluate", "params": {"expression": "document.title"}}
{"function": "evaluate", "params": {"expression": "window.location.href"}}
{"function": "evaluate", "params": {"expression": "document.querySelectorAll('a').length"}}
```

- `expression` — required. JavaScript expression. CDP timeout: 10s.

Returns: `"Result (<type>): <value>"` or `"Exception: <message>"`.

> **Type limitations** — only JSON-serializable values are returned by value:
> - Primitives, plain objects, arrays → returned as JSON.
> - **DOM nodes** → `"Result (node): HTMLDivElement"` (no actual node data).
> - **Functions** → `"Result (function): function name() { ... }"`.
> - **`undefined`** → `"Result (undefined): undefined"`.
> - **`null`** → `"Result (object): undefined"` (known edge case: null treated as no-value).
> - Circular / non-serializable objects → `"Exception: ..."`.
>
> To work with DOM content, use `element.textContent`, `element.outerHTML`, etc. — not the element itself.

### `list_console_messages`
Return in-memory console log (captured since session opened). Buffer: last 1000 entries; up to 50 shown per call.

```json
{"function": "list_console_messages"}
{"function": "list_console_messages", "params": {"level": "error"}}
```

- `level` — optional. Filter by level: `"log"`, `"info"`, `"warning"`, `"error"`. Default: all levels.

Returns:
```
Console messages (<n>):
  [<level>] <text>
  ...
```

### `take_snapshot`
Return the accessibility tree as formatted text.

```json
{"function": "take_snapshot"}
```

Returns:
```
Accessibility tree (<total_node_count> nodes):
[role] name
  [role] name
  ...
... (truncated)
```

> **Hard limit**: output is truncated to 100 lines regardless of tree size. There is no parameter to increase this limit. If you need full DOM content, use `evaluate("document.body.outerHTML")` or `evaluate("document.querySelector('...').outerHTML")` instead.

## Network

### `list_network_requests`
List recorded network requests (captured since session opened). Buffer: last 1000 entries.

```json
{"function": "list_network_requests"}
{"function": "list_network_requests", "params": {"type": "XHR", "limit": 20}}
```

- `type` — optional. CDP resource type filter: `"Document"`, `"Stylesheet"`, `"Image"`, `"Script"`, `"XHR"`, `"Fetch"`, `"Other"`, … Default: no filter.
- `limit` — optional, default `50`. Max entries to return.

Returns:
```
Network requests (<n>):
  [<method>] <requestId> (<resourceType>)
    <url>
```

Use `requestId` values with `get_network_request`.

### `get_network_request`
Get log entries and response body for a specific request ID.

```json
{"function": "get_network_request", "params": {"request_id": "1234.1"}}
```

- `request_id` — required. Get from `list_network_requests`.

Returns:
```
Request: <request_id>
<json log entries>

Response body:
<body>
```
Body is truncated at 2000 characters. Base64-encoded bodies are reported as `"[base64 encoded, N chars]"`.

## Emulation

### `emulate`
Override viewport, user-agent, or network conditions. All params are optional — pass only what you want to change.

```json
{"function": "emulate", "params": {"network": "slow3g"}}
{
  "function": "emulate",
  "params": {
    "viewport": {"width": 375, "height": 812, "deviceScaleFactor": 3, "mobile": true},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) ...",
    "network": "slow3g"
  }
}
```

- `viewport` — optional. Object with `width`, `height`, `deviceScaleFactor` (default 1), `mobile` (default false).
- `user_agent` — optional. Override User-Agent string.
- `network` — optional. Preset string: `"offline"`, `"slow3g"`, `"fast3g"`, `"reset"`. Or custom object: `{"offline": false, "downloadThroughput": 500000, "uploadThroughput": 200000, "latency": 50}`.

## Parallel call strategy

Many GDC calls are independent read operations — batch them in a single response to reduce round-trips.

**Safe to batch (read-only):**

| Function | Notes |
|---|---|
| `gdc_status` | |
| `list_pages` | |
| `list_console_messages` | |
| `list_network_requests` | |
| `take_screenshot` | |
| `take_snapshot` | |
| `evaluate` | multiple expressions at once |

**Do NOT batch** calls that depend on each other (e.g. `select_page` before `navigate`, `navigate` before `wait_for`).

### Examples

```
// Diagnose current page state in one round-trip
[BATCH] take_screenshot
      + list_console_messages { level: "error" }
      + list_network_requests { type: "Fetch" }
      + evaluate { expression: "document.title" }
```

## Workflow examples

```jsonc
// 1. Check what's open
{"function": "list_pages"}

// 2. Select a page
{"function": "select_page", "params": {"target_id": "<id from list_pages>"}}

// 3. Navigate and wait
{"function": "navigate", "params": {"url": "https://github.com"}}
{"function": "wait_for", "params": {"text": "Sign in", "timeout": 10}}

// 4. Diagnose page state (batch these)
{"function": "take_screenshot"}
{"function": "list_console_messages", "params": {"level": "error"}}
{"function": "evaluate", "params": {"expression": "document.readyState"}}

// 5. Fill and submit a form
{"function": "fill", "params": {"selector": "#login_field", "value": "myuser"}}
{"function": "fill", "params": {"selector": "#password", "value": "mypass"}}
{"function": "click", "params": {"selector": "[type=submit]"}}

// 6. Inspect XHR traffic after login
{"function": "list_network_requests", "params": {"type": "XHR"}}
{"function": "get_network_request", "params": {"request_id": "<id>"}}

// 7. Mobile emulation
{"function": "emulate", "params": {"viewport": {"width": 375, "height": 812, "mobile": true}, "network": "slow3g"}}
```
