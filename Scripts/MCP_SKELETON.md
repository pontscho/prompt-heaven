# MCP_SKELETON — canonical single-file MCP server reference

> **This file is a GOLDEN REFERENCE. It is NOT imported by any server.**
> It is the anti-divergence anchor: every `Scripts/mcp-*.py` server converges its
> *plumbing* (PEP-723 header, logging, JSON-RPC framing, dispatch, catch-all) onto
> the shapes below. The actual tool logic of each server stays unique.
>
> Source of truth: distilled from `mcp-forge.py` (sync canonical) plus the
> designed async variant for the LSP/subprocess ("A-family") servers.
>
> **Some of the plumbing below is no longer described here but *generated*.** The
> named blocks in the canonical sources — `Scripts/_mcp_concurrency.py`,
> `Scripts/_mcp_json.py`,
> `Scripts/_mcp_logging.py`, `Scripts/_mcp_lsp.py` and `Scripts/_mcp_paging.py`
> — are pasted into each
> server by `Scripts/amalgamate.py`; for those, the canonical file is the source
> of truth and this one only explains the shape. §8 is the mechanism, and it is
> the first thing to read before editing a server's helpers.
>
> **When you touch a server's plumbing, diff it against this file.** When this file
> and the servers disagree, one of them is a bug — fix the bug, don't fork the style.

---

## 0. Parameterization

Throughout this document, substitute per server:

| Placeholder | Meaning | Example |
|---|---|---|
| `SERVER_NAME` | logger name + `serverInfo.name` | `mcp-forge`, `mcp-clangd` |
| `TOOL_NAME` | the single tool exposed in `tools/list` | `forge_call`, `clangd_call` |
| `DISPATCH(...)` | the server-specific tool handler call | `handle_forge_call(...)` |

**Indentation is NOT unified.** Each file keeps its existing indentation —
`mcp-forge.py` and `mcp-webfetch.py` use **TABS**; all others, `mcp-tshark.py`
included, use **4 spaces**. Match the file you are editing. Examples below use 4
spaces. (This line used to name tshark among the tab users; it does not use
tabs, and the generator reads a host's style off its own `INDENT` tokens — 220
space indents, no tabs — rather than off this table, which is why the error was
harmless until §8 made indentation a mechanical input.)

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
logger; level and handlers are configured once in `main()`, by the generated
`_configure_logging` block rather than by anything written here (§6, §8). The
logger itself stays hand-written: its NAME is the one thing that differs between
the fifteen copies, so a shared block would make every line in the fleet claim to
come from one server.

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

`_result` and `_error` are the raw JSON-RPC envelopes. The third thing is the MCP
`isError` tool-result envelope, and it is **not** optional: every server's tool can
fail, so every server must be able to say that it did (§3a). `_tool_error` is one
way to build it and eight servers define it; the other seven set `isError` inline
at the wrap — `mcp-purity.py` reads `is_error = "error" in result` and hands it
straight to `_result` — which satisfies the same contract with no helper at all.
What is required is the FLAG, never a particular spelling of it.

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

### 3a. The error-envelope contract

**A handler failure must reach `isError`.** Not per-server taste. The defect is a
reply the caller cannot distinguish from a success, and no amount of well-written
failure text repairs it, because prose is not the channel the flag is.

The predicate is tested at the wrap, on whatever the handler handed back, and
there are two shapes of it. Eleven servers test a **top-level `error` key** on a
structured result — `is_error = "error" in result` in `mcp-forge.py`,
`mcp-git.py`, `mcp-inspect.py`, `mcp-postgres.py`, `mcp-purity.py`,
`mcp-tshark.py`, `mcp-webfetch.py` and `mcp-wiki.py`, and the same test spelled
`isinstance(result, dict) and "error" in result` in `mcp-context7.py` (12 failure
sites), `mcp-gdc.py` (33) and `mcp-lldb.py` (60). Three test the **type of the
text**, because their dispatcher has already flattened the dict and cannot be
restructured: `isinstance(result, _ErrorText)` in `mcp-clangd.py`, `mcp-cuda.py`
and `mcp-lua-lsp.py`. `mcp-jenkins.py` names the predicate, `_is_error(payload)`.
That is fifteen servers and no fourth shape.

**Raising and returning are two routes into the same predicate, not two
mechanisms.** The wrap's own `except Exception` assigns `result = {"error": …}`,
so a `raise` is simply another way to produce the dict the test then reads — which
is why every one of the eleven does both, per site: `mcp-inspect.py` raises at 49
sites and returns the dict at 4, `mcp-purity.py` 45 and 30, `mcp-tshark.py` 3 and
30. Neither is the house style; what is not optional is that one of them happens.
The illegal fourth is what this batch removed: returning a **pre-rendered failure
string** the wrap cannot tell from a success.

**The predicate reads the TOP level only.** `mcp-jenkins.py` is the worked example
in both directions: a top-level `error` key is a failure, and a nested one — real
data copied out of an upstream JSON, `wfapi` per-stage records, `_probe_connection`
— is left alone. A recursive or wildcard search would have flagged data as
failure, which is this defect wearing the opposite sign.

**Where two behaviours describe one condition, they read one predicate.** Jenkins'
bug was two: rendering keyed on the payload's `error` key while the flag keyed on
a separate `__is_error__` sentinel that only `_err` set, so 25 handler sites
returning `_ok({"error": …})` rendered AS errors and reported `isError: False` — a
failure shipped as a success. The sentinel is gone and both now read `_is_error`.
Converting those 25 sites instead would only have made the shape *currently*
absent rather than impossible.

**An action that did not happen is a failure; a question answered with "none" is a
success.** This is what keeps the sign from inverting, and it is the half a fix of
this kind is most likely to get wrong on the rebound. `lldb_list_sessions`
answering "No active LLDB sessions.", `gdc_status` reporting Chrome unreachable,
`context7` answering that no library matched — all successes. They were asked, and
they answered.

**`_ErrorText` is declared here, not generated.** It is a `str` subclass carrying
the verdict a flattened reply would otherwise lose, applied inside `_serialize`
while the value is still a dict and tested at the wrap; it exists as three
byte-identical hand copies, 1146 bytes each. Its `free_names` is empty, so it
*could* be a canonical block (§8) — and it stays a hand copy deliberately.
`mcp-purity.py`'s dict-to-the-wrap shape is the convention; the trio flattens
inside its dispatcher because `_serialize` is a closure every exit already ran, so
the wrap only ever sees a `str`. Blessing a second mechanism as generated
infrastructure — in three servers that are not registered and never launched —
would buy drift protection *for* the divergence instead of removing it. Record a
divergence as a divergence. `_tool_error` is the precedent for wrap code
documented here rather than generated: it cannot be a block at all, because its
`free_names` holds `McpServer`, a name the host defines.

**`_send` is the third entry in that register, and it fails on a different rule.**
It is byte-identical in all four LSP servers and squarely inside `_mcp_lsp.py`'s
domain — it is `encode_lsp_message`'s only caller — but that is exactly what
refuses it: its one free name is `encode_lsp_message`, which every host holds as a
module-level `def` of its own (a generated region), while `host_provides` offers a
region only the host's module-level **imports**. The documented remedy, co-listing
the dependency on the same marker, does not transfer to an **indented** region: it
would emit a module-level function as a class member, where the block's own global
lookup would not reach it anyway. Reshaping the body or dropping the module-level
region would both change behaviour, so `_send` stays a hand copy and is recorded
as one.

**The control, and what it does not reach.** `Scripts/_mcp_smoke_test.py` check 7
(`error_envelope_checks`) drives every server over live JSON-RPC with
`function="__no_such_function__"` and requires `isError is True` — read as the
flag and never as the text, since several servers answer by listing their whole
catalogue. Its negative half omits `function` entirely, which most servers answer
with a status or catalogue reply BY DESIGN, and requires that reply stay
unflagged; without that half, a server flagging everything would pass. Its limit:
it lands on ONE failure site per server, so it proves the wrap and not the sites.
Jenkins passes it without exercising any of the 25 sites the predicate fixed,
because the unknown-function path was always an `_err`.

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

## 5. run() loop — one task per message, and a reader thread nothing can take

`tests/test_read_loop.py` gates four properties across all fifteen servers; the
WHY is `docs/adr/0008-a-serialized-read-loop-looks-like-a-dead-server.md`.

1. **`sys.stdin.readline` on a dedicated `max_workers=1` executor** — never
   `None`, never a pool a handler can enter. Otherwise saturated handlers starve
   the readline, the server stops reading stdin, and later requests time out
   client-side against ids the caller has abandoned: a dead server, restart the
   only lever.
2. **One task per message** (`loop.create_task` / `asyncio.ensure_future` —
   either), never awaited inline on the loop; keep a strong reference, since a
   bare `ensure_future` can be garbage-collected mid-flight.
3. **Malformed input is ANSWERED, never dropped** — `-32700` unparseable,
   `-32600` valid JSON that is not an object. A bare `continue` leaves the id
   unanswered until it times out (a hung server, not one bad line), and a bare
   `5` reaching `msg.get()` used to kill the process. Plain wording, **not**
   §7a's `Near the failure:` window: that quotes one hand-encoded field, this is
   a whole raw line.
4. **Both ends of the pipe guarded, every executor shut down** — a detached
   stdin *raises* rather than returning `""`, a client hanging up mid-reply
   raises `BrokenPipeError`, and an unshut executor hangs exit via
   `concurrent.futures`' atexit join.

**The transport is uniform; the dispatch decision is not.** Two groups, spelled
as the gate's `FLEET` table spells them — **`pool`** (8: forge, git, inspect,
jenkins, postgres, tshark, webfetch, wiki) and **`coroutine`** (7: clangd,
context7, cuda, gdc, lldb, lua-lsp, purity). Audit this server's own state to
pick; §5b is not a reduced §5a — for those seven a worker pool is a regression.

### 5a. `pool` — blocking sync handlers in a worker `ThreadPoolExecutor`

Reference: `mcp-jenkins.py:2660`; `mcp-forge.py:1694` is the same shape in tabs.

```python
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        # Say HERE why concurrent dispatch is safe for THIS server: which mutable
        # state a handler can reach, and which lock covers it. Never copy a verdict.
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="SERVER-stdin")
        workers = ThreadPoolExecutor(max_workers=MAX_INFLIGHT_REQUESTS,
                                     thread_name_prefix="SERVER-call")
        inflight: set = set()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(reader, sys.stdin.readline)
                except (OSError, ValueError) as exc:   # a detached stdin RAISES
                    log.warning("stdin read failed, shutting down: %s", exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("Invalid JSON: %s", exc)
                    self._write(self._error(None, -32700, f"Parse error: {exc}"))
                    continue
                if not isinstance(msg, dict):          # `5` is valid JSON
                    log.warning("Request was %s, not an object", type(msg).__name__)
                    self._write(self._error(
                        None, -32600,
                        "Invalid Request: expected a JSON object, got "
                        f"{type(msg).__name__}"))
                    continue
                # F12/CWE-532: log protocol structure only, never payload
                # values (params.content / result text can carry file contents).
                # Defensive: params/arguments may be a non-dict on a malformed
                # message; this is a debug log and must never crash the loop.
                _p = msg.get("params")
                _p = _p if isinstance(_p, dict) else {}
                _args = _p.get("arguments")
                _args = _args if isinstance(_args, dict) else {}
                log.debug(
                    "← method=%s id=%s fn=%s keys=%s",
                    msg.get("method"), msg.get("id"), _p.get("name"),
                    list(_args.keys()),
                )
                task = loop.create_task(self._serve(loop, workers, msg))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
        finally:
            for task in inflight:
                task.cancel()
            reader.shutdown(wait=False)
            workers.shutdown(wait=False)
            log.info("MCP server shutting down")

    async def _serve(self, loop, workers: ThreadPoolExecutor, msg: dict) -> None:
        """One request, from dispatch to written reply. Runs as its own task."""
        try:
            response = await loop.run_in_executor(workers, self._handle_message, msg)
        except Exception as exc:  # noqa: BLE001 — CancelledError is a BaseException
            log.exception("Unhandled exception while handling message")
            response = self._error(msg.get("id"), -32603,
                                   f"Internal error: {type(exc).__name__}: {exc}")
        if response is not None:
            self._write(response)

    def _write(self, response: dict) -> None:
        """No lock needed: handlers run in the worker pool, but `_serve` resumes on
        the event-loop thread after its await, so two replies cannot interleave."""
        try:
            out = json.dumps(response)
        except (TypeError, ValueError) as exc:
            log.exception("Response was not JSON-serialisable")
            out = json.dumps(self._error(response.get("id"), -32603,
                                         f"Response not serialisable: {exc}"))
        # F12/CWE-532: structure only (id + outcome), no body.
        log.debug(
            "→ id=%s %s", response.get("id"),
            "error" if "error" in response else "ok",
        )
        try:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError) as exc:
            log.warning("stdout write failed: %s", exc)
```

### 5b. `coroutine` — handlers awaited directly, no worker pool

Three lines differ: `run()` builds only `reader`, dispatch is
`loop.create_task(self._serve(msg))`, and `_serve` awaits `self._handle_message(msg)`
directly. Read `mcp-purity.py:5944`. A worker pool there would be a **regression**:
those handlers' per-client `_next_id += 1` and the `_pending[id] = fut` after it
sit between the same two awaits, which is safe only on one thread.
`mcp-context7.py:712` is the third arrangement — coroutine dispatch that still
owns a pool, because its one blocking call parks itself on a module-level
`_HTTP_EXECUTOR`. The `finally` body stays server-specific (`await _client.stop()`
for the LSP hosts, `await self.manager.cleanup_all()` for gdc/lldb), and
`mcp-webfetch.py:1315` **drains** instead of cancelling, because cancelling a task
does not stop the thread part-way through writing the file a caller asked for.

> **Behavioral note (intentional):** any exception reaching `_serve` produces a
> `-32603` reply on stdout. What reaches it is narrow — the tool wrap's own
> `except` already converts a handler failure into a flagged tool reply (§3a), so
> one bubbling past THAT is a failure the wrap itself could not shape: replying
> is correct, hanging is not.

---

## 6. main() — argparse + canonical logging setup

`--debug` and `--log-file` are universal. `--log-file` implies debug level. Default
level is `WARNING`. Logging is configured **before** `asyncio.run(...)`
so any background auto-init task logs through the same configuration.

**The configuration is a generated region; the flags are not.** `_configure_logging`
lives once in `Scripts/_mcp_logging.py` and is pasted in at module level by
`Scripts/amalgamate.py` (§8) — do not hand-write it here. The `add_argument` calls
stay per server on purpose: three servers word the `--log-file` help differently
and `mcp-webfetch` declares `-v` / `--verbose` aliases, and those are declarations,
not logic. Sharing them would mean deleting a feature to please a generator.

```python
def main():
    parser = argparse.ArgumentParser(description="SERVER_NAME — ...")
    # ... server-specific args ...
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    parser.add_argument("--log-file", help="Log to file (implies --debug)")
    # LSP trio also: parser.add_argument("--markdown", action="store_true", ...)
    args = parser.parse_args()

    _configure_logging(args.debug, args.log_file)

    # LSP trio: global MARKDOWN_MODE; if args.markdown: MARKDOWN_MODE = True

    server = McpServer(...)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
```

Every server (including the former "lite trio" lldb/gdc/context7) exposes BOTH
`--debug` and `--log-file` for full CLI parity.

Two properties of that block are worth knowing before you reach for the flag.
**`--log-file` does not redirect the log, it ENABLES it** — either flag raises the
level to DEBUG, and with neither the server sits at WARNING on stderr, never
stdout, which carries the frames. And the file is opened `0600` *and* `fchmod`ed
to `0600`, because the mode argument to `os.open` applies only on creation while
`fchmod` tightens a file an earlier run left `0644` under a laxer umask.
`tests/test_generated_region.py` pins that mode against a real file rather than
against a reading of the source: the fleet has already had to fix it in fifteen
places at once, in `c8b74d0`, which is the whole argument for the region.

---

## 7. Argument & parameter hygiene (dual-level JSON decode + bool coercion)

Two wire realities every server must tolerate, because clients (and LLMs) are
inconsistent about how they serialize tool input.

### 7a. Dual-level JSON-string decode

The `tools/call` payload can arrive JSON-**encoded as a string** at *two* levels:
the whole `arguments` object, and the inner `params` object. Both must be
decoded defensively — a server that only accepts a native dict rejects
otherwise-valid calls.

**Level 1 — `arguments`, in `_handle_tool_call`** (decode, then the existing
dict guard catches anything still not an object):

```python
if isinstance(arguments, str):
    try:
        arguments = json.loads(arguments)
    except json.JSONDecodeError:
        pass
if not isinstance(arguments, dict):
    return self._result(msg_id, {
        "content": [{"type": "text", "text":
            f"'arguments' must be an object; got {type(arguments).__name__}."}],
        "isError": True,
    })
```

**Level 2 — `params`, in the param normalizer** (`_resolve_aliases` /
`_ensure_dict`), raising a clean `ValueError` the wrap turns into a flagged error
result. Raising is one of two routes into that flag, not a rule: a handler may
equally RETURN `{"error": …}`, which the same wrap predicate reads (§3a). What is
not optional is that one of the two happens:

```python
if isinstance(params, str):
    try:
        params = json.loads(params)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"'params' was a string but not valid JSON: {exc}. "
            f"Near the failure: {_json_error_window(params, exc.pos)}. "
            "Pass params as an object, not a JSON-encoded string."
        )
if not isinstance(params, dict):
    raise ValueError(f"'params' must be an object or JSON-encoded object string; "
                     f"got {type(params).__name__}.")
```

The offset the exception carries (`char 1530`) is unusable on its own: the
caller that hand-encoded the string cannot count to it, so its only recovery is
to re-emit the whole payload and hope the second encode is luckier. Quoting the
text around `exc.pos` names the one broken escape instead — and `repr` is the
load-bearing part, because the usual defect is a quote escaped one level too
shallow, which a raw slice prints identically to a correct one:

```python
def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    lead = "..." if start > 0 else ""
    tail = "..." if end < len(text) else ""
    return f"{lead}{text[start:end]!r}{tail}"
```

Both functions above are **generated, not hand-written**, in every server that
factors this out into an `_ensure_dict`. Do not copy them in: paste the marker
pair and let §8 fill the body. They travel on ONE marker, dependency first —

```python
# BEGIN GENERATED: _mcp_json.py :: _json_error_window, _ensure_dict
```

— because `_ensure_dict` reads `_json_error_window`, and a region may only reach
names its host *imports*. Asking for `_ensure_dict` alone is refused by name. A
server that keeps the logic inline against a local `params`, as the sample above
shows, requests `_json_error_window` on its own instead; both shapes are live in
the fleet and neither is the deprecated one.

Do **not** go further and try to *repair* the broken JSON. The failure shape is
ambiguous (a prematurely closed string is indistinguishable from a genuinely
short value), and on a write path a wrong guess silently commits corrupted
content to a file — strictly worse than the bounce.

> The `force-error` smoke check still passes: it sends a non-dict at the JSON-RPC
> `params` level (not `arguments`/inner-`params`), so `params.get(...)` in the
> dispatcher raises and bubbles to the `-32603` catch-all unchanged.

### 7b. Boolean coercion — `_bool_param`

The wire frequently carries booleans as strings (`"false"`, `"0"`, `"no"`),
where a naive `bool("false")` is `True` — the opposite of intent. Every server
that reads a boolean flag routes it through one canonical helper:

```python
def _bool_param(value, default=False):
    """Coerce a possibly-stringy value to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off", "none")
    return bool(value)
```

Read **every** boolean flag through it —
`analyze = _bool_param(params.get("analyze", False))` — never
`bool(params.get(...))` or a bare truthy test. Servers with no boolean parameter
omit the helper (no dead code). One server (`mcp-tshark`) keeps an older
`(params, key, default)` signature; behavior is identical, only the call shape
differs.

### 7c. Two spellings of one parameter — refuse, never choose

Every server accepts parameter aliases: `file`, `file_path` and `path` all mean
the same thing. Nothing stopped a caller from sending **two** of them at once,
and each resolver quietly kept one value and dropped the other.

**Which one it kept was decided by the order the keys arrived on the wire.** Six
servers kept the last, four kept the first, and neither rule is
order-independent, neither prefers the canonical spelling, and no server looked
at the raw key set before resolving — so `{"path": A, "relative_path": B}` and
`{"relative_path": B, "path": A}` were two different calls on all ten. Unifying
on one of the two rules would have picked a coin, not a fix: the ambiguity IS
the defect.

So the resolver **refuses**:

```python
    resolved = {}
    claimed = {}
    for key, value in params.items():
        canonical = PARAM_ALIASES.get(key, key)
        if canonical in resolved:
            first, second = sorted((claimed[canonical], key))
            raise ValueError(
                f"Ambiguous parameters: '{first}' and '{second}' both set "
                f"'{canonical}'. Pass exactly one."
            )
        resolved[canonical] = value
        claimed[canonical] = key
    return resolved
```

Four properties of that shape are load-bearing:

- **It detects in the loop, on `canonical` — never against a reverse map built
  from `PARAM_ALIASES`.** Three hosts overlay a per-function table on the global
  one, and `mcp-postgres.py` *inverts* it: globally `parameters` → `params`,
  but for `call_function` `parameters` → `args` **and** `params` → `args`. A
  reverse map from either table alone is wrong there. The loop already computes
  the effective mapping; the check reads what it computed.
- **`claimed` exists so the message can name what the caller actually wrote.**
  `resolved` only remembers canonical names, and telling someone that
  `relative_path` collided with `relative_path` is not an error anyone can act
  on.
- **`sorted()` makes the message order-independent too**, not just the verdict.
  The same mistake gets the same sentence whichever way round it was sent, which
  is the property the whole decision is about.
- **It is presence-based, not value-based.** `{"path": A, "relative_path": A}` is
  refused as well, even though nothing is lost. Exempting equal values would
  mean the same call shape errors or passes depending on the data — exactly the
  per-site subtlety that drifts back (ADR 0010 names this pattern).

The envelope-level `function`/`f` and `params`/`p` or-chains in each dispatcher
are a **different mechanism at a different layer** and are deliberately NOT
covered: they are `a or b` expressions, not table lookups, and they stay
first-wins.

**This function is a HAND COPY in all ten hosts and cannot become a §8 block.**
Its free names are each host's own alias tables — `PARAM_ALIASES`, plus
`PARAM_ALIASES_BY_FUNC` in three — and `mcp-forge.py` takes the table as an
argument instead, so the bodies are not copies that drifted but ten shapes that
never agreed: nine distinct bodies over ten files, only `mcp-clangd.py` and
`mcp-cuda.py` byte-identical. Two use tabs. When you change the rule, you change
it nine times, and the gate below is what proves you did.

Gated by `alias_collision_checks` in `Scripts/_mcp_smoke_test.py`, which drives
all ten over live JSON-RPC in three halves — the refusal, a one-spelling control
that catches a resolver flagging everything, and a coverage half running on all
fifteen servers that fails if a file defines `_resolve_aliases` without a probe
row. The WHY is frozen in `docs/adr/0015-ambiguity-is-the-defect.md`.

---

---

## 8. Generated regions — one source per domain, fifteen copies, no import

Some of the code above no longer lives in this file's copy of each server. It
lives once in a canonical source — `Scripts/_mcp_json.py` for the JSON-RPC
envelopes, wire-value coercion and JSON error reporting; `Scripts/_mcp_logging.py`
for how a server CONFIGURES logging, which is not the same question as what it
logs (the wire log is a security invariant governed by `tests/test_wire_log.py`
and deliberately stays hand-written in each server); `Scripts/_mcp_lsp.py`
for how the LSP wire is spoken by the four language-server hosts — the
`Content-Length` framing of a message, the `file://` DocumentUri of a path, and
the client-side request and notification hops that ride on both;
`Scripts/_mcp_paging.py` for output capping and the two halves of the pager
protocol, the `offset=<n> for more` line a payload ends with and the read that
takes the number back; and `Scripts/_mcp_concurrency.py` for how many tool calls
run at once, which is one constant and deliberately not the executors around it
— and is **pasted into** each server by
`python3 Scripts/amalgamate.py`. In a server the result looks like this, and it
is the whole of the mechanism:

```python
# BEGIN GENERATED: _mcp_json.py :: _json_error_window
def _json_error_window(text: str, pos: int, radius: int = 48) -> str:
    ...                                   # the function's text, pasted in
# END GENERATED: 4c5e7e3f59cb
```

The `BEGIN` line is a **request**: *put the top-level `_json_error_window` from
`_mcp_json.py` here.* The hex on the `END` line is a fingerprint of whatever
currently sits between the markers.

**The source filename on that line is not decoration.** It selects the block map
the region's names are resolved against, so a name defined in *another*
canonical file does not resolve — asking `_mcp_json.py` for `encode_lsp_message`
is refused by name, not quietly served from next door. That is what keeps each
canonical file a **domain** rather than a shelf, and it is why the JSON source
has been narrowed twice: framing left for `_mcp_lsp.py`, row accounting for
`_mcp_paging.py`, each move a marker-line-only diff with the END hash unchanged.
The domains are listed
explicitly in the generator's `CANONICAL_SOURCES`; the registry is hand-written
rather than globbed from `_mcp_*.py` so that a new helper file cannot become a
generation source merely by existing. The `generated_region` suite proves both
halves — a cross-source name is refused, and when two sources define the same
name the marker decides which body is emitted.

**Nothing is dynamic at run time.** The server is an ordinary self-contained
file holding an ordinary function; Python never learns the generator exists,
there is no import, no `sys.path` entry, and no build step — the pasted code is
committed, so a fresh clone runs. This is why the trick exists at all: an
imported sibling module would write `Scripts/__pycache__` into a tree four
suites assert is empty, would need a `sys.path` entry the test harness's
`spec_from_file_location` never adds, and would move the helpers out of the
module attributes `tests/test_mcp_footprint.py` reaches for. Generating keeps
every one of those properties and still leaves one place to edit.

### The two things you actually do

**Change a shared helper** — edit its canonical source
(`Scripts/_mcp_concurrency.py`, `Scripts/_mcp_json.py`,
`Scripts/_mcp_logging.py`, `Scripts/_mcp_lsp.py` or `Scripts/_mcp_paging.py`),
then:

```
$ python3 Scripts/amalgamate.py
updated: mcp-purity.py [_json_error_window]
```

**Give a server a helper it does not have yet** — paste the two marker lines
with an empty body, listing what you want, then run the generator; it fills the
body in and writes the fingerprint:

```python
# BEGIN GENERATED: _mcp_json.py :: _json_error_window
# END GENERATED:
```

### If you edit inside a region

You get refused, not overwritten:

```
$ python3 Scripts/amalgamate.py
HAND-EDITED: mcp-purity.py [_json_error_window] -- recorded 4c5e7e3f59cb,
found f36927863526; re-run with --force to discard the edit
```

Your edit stays on disk and the generator tells you where to go instead. That is
the point of the fingerprint: neither party can silently eat the other's work.
The file has exactly one writer — the same conclusion
`docs/adr/0009-the-first-reader-is-a-cold-model.md` reached for the checkpoint
file's table of contents.

### The block contract

A block may reference **only** builtins, the stdlib names its own canonical file
imports, and its own arguments — never a name the host server defines. The near
miss that made the rule necessary is `_canonical_function`: two lines, identical
in three servers, but its body reads a per-server `FUNCTION_ALIASES` table, so
sharing it would mean sharing a promise about the host's globals. It stays out
until that promise has a form and a check.

Five more properties worth knowing before you touch it (the count was wrong at
three when the list already had four; it is checked now):

- **A block is a top-level function, a class, or a single-target CONSTANT.**
  `load_blocks_text` walks the canonical file's `tree.body`, and the constant
  arm is narrow on purpose: exactly `NAME = <value>`. `A = B = 1` and
  `A, B = f()` bind several names from one indivisible slice, so the region
  would define a name its marker never mentions; `x.attr = 1` binds no name to
  key on; and `X += 1` is refused because its target reads as a *binding* to
  `free_names`, which would hide the fact that the host must already define
  `X`. `X: int = 1` is out on demand rather than principle. The argument for
  each sits on `amalgamate.py:assign_name`, and `single-name-assign-only` in
  the suite's control group pins every arm of it.
  `DEFAULT_MAX_ANSWER_CHARS`, `PAGE_LINE_RESERVE` and `_FENCE_LINE_RE` are the
  first constants to travel this way. Give each one a region of its **own**:
  the sentence above a constant explaining what the number is for is
  host-specific prose, and a region per constant leaves it outside the markers
  where it was written.
- **Markers are found with `tokenize`, `COMMENT` tokens only.** A marker quoted
  inside a docstring is inert — necessarily, since `amalgamate.py`'s own
  docstring carries a full `BEGIN`/`END` pair as its example, and a line scanner
  would paste generated code into the middle of it.
- **A `BEGIN` without an `END` is a hard error**, never a skip. A region that
  quietly stops being maintained is the failure the whole mechanism prevents.
- **A file with no region at all is fine.** Servers are converted one at a time,
  and a server whose variant is deliberately different simply never asks for the
  block — that is how `mcp-webfetch`'s inverted-polarity `_bool_param` (§7b) can
  keep its own version while the others share one.
- **A region is emitted at its BEGIN marker's own column.** Indent the marker and
  the block lands indented, which is what lets a region sit inside a class body —
  the route by which the `_result` / `_error` methods of §3, and the `_request` /
  `_notify` / `_abs_uri` / `_abs_path` client methods the four LSP servers share,
  are shareable at all. The column is the whole mechanism: those four are written
  at module top level in `_mcp_lsp.py` and land inside three differently-named
  client classes, so a method taking `self` is no obstacle — `self` is an ordinary
  first parameter, and the `@staticmethod` on `_result` / `_error` was never what
  made the route work.

### Tabs: refused per BLOCK, not per file

**This narrows an earlier decision.** The rule used to be that a tab-indented
file could host no generated region at all, because converting leading spaces to
tabs would also convert **alignment** to tabs — `_rows_note`'s continuation line
aligns its `else` under an open paren — putting the code in a column nobody
chose, in a region no human is supposed to read closely. That reasoning is
correct, and it is a property of **that block**, not of tab indentation. Every
**other** canonical block contains no bracket continuation at all, so every one
of their indents is structural and a tab conversion is mechanical. The
`generated_region` suite asserts that the unsafe set is exactly `_rows_note`, so
that claim is measured on every run rather than counted here by hand.

The generator now decides it per block, and mechanically. `block_is_tab_safe`
demands two things of a block: no implicit line join (`tokenize`, the same pass
that finds the markers) and every leading run a whole 4-space level (arithmetic,
which also covers indentation inside a string, where the tokenizer sees one atom
and has nothing to say). Anything unprovable is unsafe. The host's own style
comes from its `INDENT` tokens, not from the marker's column — a module-level
marker sits at column 0 and carries no signal.

A tab host asking for an unsafe block is **refused by name**; it is not quietly
served spaces, because a file mixing both is worse than either. `_rows_note` is
the only block that hits this today.

`mcp-forge.py` came in under the narrowed rule and its diff was marker lines
only — the emitted bodies were byte-for-byte what it already had. **`mcp-webfetch.py`
hosts two regions in tabs (`_json_error_window`, `_int_param`), so tabs are not
what keeps its `_result` and its `_bool_param` out:** its `_result` annotates
`result: dict` where the canonical says `result: Any`, and its `_bool_param` is
an **allow**-list where the canonical is a deny-list, so an unrecognised string
reads `False` there and `True` here. Those are body and behaviour differences,
and they would survive any amount of re-indenting.

`python3 Scripts/amalgamate.py --check` writes nothing and exits 1 if any region
is stale; the `generated_region` suite does the same comparison in memory, so
drift committed into a server turns the fleet red.

---

## 9. Convergence checklist (per file)

- [ ] shebang + PEP-723 block (`dependencies = []` if stdlib-only; exact list otherwise)
- [ ] `import logging`; module-level `log = logging.getLogger("SERVER_NAME")`; no `debug_log`, no `DEBUG`/`_log_file` globals
- [ ] static `_result` / `_error` (legacy `_ok`/`_err` renamed); a tool-level `isError` envelope built either by a static `_tool_error` or inline at the wrap
- [ ] **a handler failure REACHES the flag** — raise, or return `{"error": ...}`, or mark the text with `_ErrorText`; never a pre-rendered failure string the wrap cannot tell from a success (§3a)
- [ ] `initialize` → `{protocolVersion, serverInfo, capabilities}`, version `"1.0.0"`
- [ ] `ping` → `_result(msg_id, {})`; notifications → `None`; unknown → `-32601`
- [ ] run() loop: readline on a dedicated `max_workers=1` executor, one task per message, `-32700`/`-32600` **answered**, readline and write guarded, every executor shut down (§5 — `tests/test_read_loop.py` gates this)
- [ ] `_serve` wraps the handler in try/except and **writes** a `-32603` reply
- [ ] `_configure_logging` present as a GENERATED region (`_mcp_logging.py`), called once from `main()`; `--debug` + `--log-file` present and hand-written (§6)
- [ ] `_handle_tool_call` decodes a string `arguments` (JSON) before the dict guard (§7a)
- [ ] param normalizer decodes a string `params` (JSON); every bool flag read via `_bool_param` (§7a/§7b)
- [ ] `MARKDOWN_MODE` / subprocess `finally` cleanup left intact where present
- [ ] every `# BEGIN GENERATED` region left to the generator, never hand-edited; `python3 Scripts/amalgamate.py --check` clean (§8)
