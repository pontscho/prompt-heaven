#!/usr/bin/env python3
"""tf_stub_child -- the adversarial stub MCP child for tests/test_mcp_proxy.py.

A fixture, not project code (tests/files/README.md: every symbol starts with
``tf``).  Stdlib only, Python 3.9, run as ``__main__`` by the proxy suite and
by the smoke row of ``Scripts/mcp-proxy.py``.  It speaks newline-delimited
JSON-RPC 2.0 on stdin/stdout: ``initialize``, ``ping``, ``tools/list`` and
``tools/call``; any other request gets -32601, notifications are recorded and
otherwise ignored.  On stdin EOF it exits 0 at once, even with a ``tf_sleep``
pending (those run on daemon threads).

Handling is synchronous on the main thread, except ``tf_sleep``, ``tf_sample``
and ``tf_ping_proxy``, which run on daemon threads so the stub keeps reading
stdin: a sleep can then see its ``notifications/cancelled``, and a child-side
request can see its reply, matched by id while the tool call waits.  Every
stdout write goes through one ``threading.Lock``.

CLI flags
---------
--tool NAME                 repeatable, default ``tf_stub_call``; every name is
                            one tool served by the same dispatcher.  The first
                            name (``tool0``) names the state files.
--state-dir DIR             append one JSON line per received message to
                            ``DIR/<tool0>.events.jsonl`` (O_APPEND):
                            {"pid", "method", "id", "cancel_id", "reason",
                            "progress_token", "args_keys"}; a completed
                            ``tf_sleep`` adds {"method": "tf_sleep/done", "id"}.
                            At start-up the stub FIRST counts the lines already
                            in ``DIR/<tool0>.pids`` (c, 0 if absent), THEN
                            appends its own pid, so every spawn leaves exactly
                            one line.
--page-size N               paginate ``tools/list`` with ``nextCursor``.
--cursor-loop               always answer ``tools/list`` with the same
                            ``nextCursor``.
--crash-on-init             exit 3 when ``initialize`` arrives, no answer.
--toolset-file F            if F exists at start-up, the tool names are read
                            from it (one per line) instead of ``--tool``; the
                            state files keep the ``--tool`` name.
--crash-after-init-count N  on ``initialize``, if c >= N: exit 3, no answer.
--init-delay S              on ``initialize``, if c >= 1: sleep S seconds on the
                            main thread before answering.

Tool functions (``arguments = {"function": ..., "params": {...}}``)
-------------------------------------------------------------------
tf_echo                 text json.dumps(arguments, sort_keys=True)
tf_sleep {seconds}      daemon thread; answers after the sleep, never if the
                        call was cancelled first
tf_progress {n, after_result}
                        n notifications/progress carrying the received token,
                        then the result; with after_result one more after it
tf_crash {code}         os._exit(code) mid-call, no reply
tf_big {bytes}          a text result of exactly that many bytes
tf_oversize {bytes}     one line of that many bytes that is not JSON, no reply
tf_pollute              a print line, a non-UTF-8 line, three wrongly shaped
                        JSON objects, then a normal result
tf_sample               sends {"id": "s1", "method": "sampling/createMessage"}
                        to the proxy; text is the reply message (JSON)
tf_ping_proxy           sends {"id": "p1", "method": "ping"}; text is the reply
tf_pid                  text: the stub's pid
tf_env {name}           text {"present": name in os.environ}
tf_spawn_grandchild     ``sleep 600`` in the stub's own process group, all
                        three streams DEVNULL; text: its pid
tf_error                JSON-RPC error {-32000, "tf boom"}

An unknown function answers isError true with ``Unknown function '<x>'.
Available: tf_echo, ...``; an omitted function answers a status with isError
false (the smoke harness's two envelope halves).
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

TF_DEFAULT_TOOL = "tf_stub_call"
TF_PROTOCOL_VERSION = "2024-11-05"
TF_LOOP_CURSOR = "tf-cursor-loop"
TF_REPLY_WAIT_S = 10.0

TF_FUNCTIONS = (
    "tf_echo", "tf_sleep", "tf_progress", "tf_crash", "tf_big", "tf_oversize",
    "tf_pollute", "tf_sample", "tf_ping_proxy", "tf_pid", "tf_env",
    "tf_spawn_grandchild", "tf_error",
)

tf_write_lock = threading.Lock()
tf_state_lock = threading.Lock()
tf_sleepers = {}       # json.dumps(request id) -> threading.Event (set = cancelled)
tf_waiters = {}        # child-side request id -> [threading.Event, reply or None]
tf_waiters_lock = threading.Lock()


class TfState:
    """Process-wide configuration and the start-up incarnation count."""

    def __init__(self, args):
        self.args = args
        self.tool0 = (args.tool or [TF_DEFAULT_TOOL])[0]
        self.tools = list(args.tool or [TF_DEFAULT_TOOL])
        if args.toolset_file and os.path.isfile(args.toolset_file):
            with open(args.toolset_file, "r", encoding="utf-8") as fh:
                names = [ln.strip() for ln in fh if ln.strip()]
            if names:
                self.tools = names
        self.events_path = None
        self.prior = 0
        if args.state_dir:
            self.events_path = os.path.join(args.state_dir, self.tool0 + ".events.jsonl")
            pids_path = os.path.join(args.state_dir, self.tool0 + ".pids")
            # L5 ordering: count FIRST, then append our own pid.
            try:
                with open(pids_path, "r", encoding="utf-8") as fh:
                    self.prior = sum(1 for ln in fh if ln.strip())
            except FileNotFoundError:
                self.prior = 0
            tf_append(pids_path, "%d\n" % os.getpid())


def tf_append(path, text):
    """Append one record with O_APPEND so concurrent incarnations never interleave."""
    data = text.encode("utf-8")
    with tf_state_lock:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def tf_record(state, record):
    if state.events_path:
        tf_append(state.events_path, json.dumps(record) + "\n")


def tf_record_message(state, msg):
    method = msg.get("method")
    params = msg.get("params")
    params = params if isinstance(params, dict) else {}
    cancel_id = None
    reason = None
    if method == "notifications/cancelled":
        cancel_id = params.get("requestId")
        reason = params.get("reason")
    meta = params.get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    args_keys = None
    if method == "tools/call":
        arguments = params.get("arguments")
        if isinstance(arguments, dict):
            args_keys = list(arguments.keys())
    tf_record(state, {
        "pid": os.getpid(),
        "method": method,
        "id": msg.get("id"),
        "cancel_id": cancel_id,
        "reason": reason,
        "progress_token": token,
        "args_keys": args_keys,
    })


def tf_write(obj):
    """One JSON line on stdout, under the lock; a dead pipe ends the stub."""
    tf_write_raw((json.dumps(obj) + "\n").encode("utf-8"))


def tf_write_raw(data):
    try:
        with tf_write_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        os._exit(0)


def tf_result(req_id, result):
    tf_write({"jsonrpc": "2.0", "id": req_id, "result": result})


def tf_error(req_id, code, message):
    tf_write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def tf_text(req_id, text, is_error=False):
    tf_result(req_id, {"content": [{"type": "text", "text": text}], "isError": is_error})


def tf_ask_proxy(req_id, child_id, method):
    """Send a child-side request and wait for the reply matched by its id."""
    slot = [threading.Event(), None]
    with tf_waiters_lock:
        tf_waiters[child_id] = slot
    tf_write({"jsonrpc": "2.0", "id": child_id, "method": method, "params": {}})
    got = slot[0].wait(TF_REPLY_WAIT_S)
    with tf_waiters_lock:
        tf_waiters.pop(child_id, None)
    if not got:
        tf_text(req_id, "tf no reply to %s within %ss" % (method, TF_REPLY_WAIT_S), True)
        return
    tf_text(req_id, json.dumps(slot[1], sort_keys=True))


def tf_sleep_worker(state, req_id, seconds, cancelled):
    if cancelled.wait(seconds):
        return
    with tf_state_lock:
        tf_sleepers.pop(json.dumps(req_id), None)
    tf_text(req_id, "tf slept %s" % seconds)
    tf_record(state, {"pid": os.getpid(), "method": "tf_sleep/done", "id": req_id})


def tf_spawn_thread(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def tf_call_tool(state, req_id, params):
    name = params.get("name")
    if name not in state.tools:
        tf_error(req_id, -32602, "Unknown tool: %s" % name)
        return
    arguments = params.get("arguments")
    arguments = arguments if isinstance(arguments, dict) else {}
    meta = params.get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    fn = arguments.get("function")
    fp = arguments.get("params")
    fp = fp if isinstance(fp, dict) else {}

    if fn is None:
        tf_text(req_id, json.dumps({"status": "ok", "pid": os.getpid(),
                                    "tools": state.tools,
                                    "functions": list(TF_FUNCTIONS)}))
    elif fn == "tf_echo":
        tf_text(req_id, json.dumps(arguments, sort_keys=True))
    elif fn == "tf_sleep":
        cancelled = threading.Event()
        with tf_state_lock:
            tf_sleepers[json.dumps(req_id)] = cancelled
        tf_spawn_thread(tf_sleep_worker, state, req_id,
                        float(fp.get("seconds", 0)), cancelled)
    elif fn == "tf_progress":
        count = int(fp.get("n", 0))
        for i in range(count):
            if token is not None:
                tf_write({"jsonrpc": "2.0", "method": "notifications/progress",
                          "params": {"progressToken": token, "progress": i + 1,
                                     "total": count}})
        tf_text(req_id, "tf progress %d" % count)
        if fp.get("after_result") and token is not None:
            tf_write({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": count + 1,
                                 "total": count}})
    elif fn == "tf_crash":
        os._exit(int(fp.get("code", 1)))
    elif fn == "tf_big":
        tf_text(req_id, "x" * int(fp.get("bytes", 0)))
    elif fn == "tf_oversize":
        tf_write_raw(b"x" * int(fp.get("bytes", 0)) + b"\n")
    elif fn == "tf_pollute":
        tf_write_raw(b"hello from print\n")
        tf_write_raw(b"\xff\xfe tf not utf-8\n")
        tf_write({"jsonrpc": "2.0", "method": "notifications/progress", "params": "x"})
        tf_write({"jsonrpc": "2.0", "method": "notifications/progress", "params": [1, 2]})
        tf_write({"jsonrpc": "2.0", "method": 5})
        tf_text(req_id, "tf polluted")
    elif fn == "tf_sample":
        tf_spawn_thread(tf_ask_proxy, req_id, "s1", "sampling/createMessage")
    elif fn == "tf_ping_proxy":
        tf_spawn_thread(tf_ask_proxy, req_id, "p1", "ping")
    elif fn == "tf_pid":
        tf_text(req_id, str(os.getpid()))
    elif fn == "tf_env":
        tf_text(req_id, json.dumps({"present": str(fp.get("name")) in os.environ}))
    elif fn == "tf_spawn_grandchild":
        proc = subprocess.Popen(["sleep", "600"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tf_text(req_id, str(proc.pid))
    elif fn == "tf_error":
        tf_error(req_id, -32000, "tf boom")
    else:
        tf_text(req_id, "Unknown function '%s'. Available: %s"
                % (fn, ", ".join(TF_FUNCTIONS)), True)


def tf_list_tools(state, req_id, params):
    tools = [{
        "name": name,
        "description": "tf stub tool %s" % name,
        "inputSchema": {
            "type": "object",
            "properties": {
                "function": {"type": "string", "enum": list(TF_FUNCTIONS)},
                "params": {"type": "object"},
            },
        },
    } for name in state.tools]
    if state.args.cursor_loop:
        tf_result(req_id, {"tools": tools[:1], "nextCursor": TF_LOOP_CURSOR})
        return
    size = state.args.page_size
    if not size or size <= 0:
        tf_result(req_id, {"tools": tools})
        return
    cursor = params.get("cursor")
    try:
        start = int(cursor) if cursor is not None else 0
    except (TypeError, ValueError):
        tf_error(req_id, -32602, "tf bad cursor")
        return
    page = {"tools": tools[start:start + size]}
    if start + size < len(tools):
        page["nextCursor"] = str(start + size)
    tf_result(req_id, page)


def tf_initialize(state, req_id, params):
    args = state.args
    if args.crash_on_init:
        os._exit(3)
    if args.crash_after_init_count is not None and state.prior >= args.crash_after_init_count:
        os._exit(3)
    if args.init_delay and state.prior >= 1:
        time.sleep(args.init_delay)
    version = params.get("protocolVersion")
    tf_result(req_id, {
        "protocolVersion": version if isinstance(version, str) else TF_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "tf-stub-child", "version": "1.0.0"},
    })


def tf_dispatch(state, msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params")
    params = params if isinstance(params, dict) else {}

    if method is None:
        # A reply to one of our own child-side requests.
        with tf_waiters_lock:
            slot = tf_waiters.get(req_id) if isinstance(req_id, str) else None
        if slot is not None:
            slot[1] = msg
            slot[0].set()
        return
    if "id" not in msg:
        if method == "notifications/cancelled":
            key = json.dumps(params.get("requestId"))
            with tf_state_lock:
                cancelled = tf_sleepers.pop(key, None)
            if cancelled is not None:
                cancelled.set()
        return
    if method == "initialize":
        tf_initialize(state, req_id, params)
    elif method == "ping":
        tf_result(req_id, {})
    elif method == "tools/list":
        tf_list_tools(state, req_id, params)
    elif method == "tools/call":
        tf_call_tool(state, req_id, params)
    else:
        tf_error(req_id, -32601, "Method not found: %s" % method)


def tf_parse_args(argv):
    ap = argparse.ArgumentParser(description="tf adversarial stub MCP child")
    ap.add_argument("--tool", action="append", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--page-size", type=int, default=0)
    ap.add_argument("--cursor-loop", action="store_true")
    ap.add_argument("--crash-on-init", action="store_true")
    ap.add_argument("--toolset-file", default=None)
    ap.add_argument("--crash-after-init-count", type=int, default=None)
    ap.add_argument("--init-delay", type=float, default=0.0)
    return ap.parse_args(argv)


def tf_main(argv=None):
    state = TfState(tf_parse_args(argv))
    stdin = sys.stdin.buffer
    while True:
        try:
            raw = stdin.readline()
        except (OSError, ValueError):
            break
        if not raw:
            break
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            tf_error(None, -32700, "Parse error: %s" % exc)
            continue
        if not isinstance(msg, dict):
            tf_error(None, -32600, "Invalid Request: not an object")
            continue
        tf_record_message(state, msg)
        tf_dispatch(state, msg)
    return 0


if __name__ == "__main__":
    sys.exit(tf_main())
