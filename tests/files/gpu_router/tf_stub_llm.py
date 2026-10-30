#!/usr/bin/env python3
"""tf_stub_llm -- the adversarial stub inferNO-style LLM child for tests/test_gpu_router.py.

A fixture, not project code (tests/files/README.md: every symbol starts with
``tf``).  Stdlib only, Python 3.9, run as ``__main__`` by the gpu-router suite
as a model's ``command``; nothing imports it.  It stands in for ONE inferNO
process that serves several model ids (chat, embeddings, rerank) on one
loopback port: HTTP/1.1, ``Connection: close`` on every answer.  It does not
listen while it "loads" (``--start-delay``), exactly like inferNO.

It NEVER prints a request body, a header value or an environment value to
stdout or stderr; the only stderr lines are the fixed texts of a refusal.

Routes
------
GET  /v1/models                 {"object": "list", "data": [{"id": <id>, ...}]} for --ids
                                (404 inside the --ready-404-for window after the bind: the
                                 probe of a model that is bound but not loaded yet)
GET  /health                    {"status": "ok"}
GET  /                          404
POST /v1/chat/completions       JSON answer, or an SSE stream when the body has "stream": true
POST /v1/messages               the same, Anthropic-shaped
POST /v1/embeddings             {"object": "list", "data": [{"embedding": [...]}], ...}
POST /v1/rerank                 {"results": [{"index", "relevance_score"}], ...}
any other method on a known route 405; any other path 404.
Every JSON answer carries "tf_request_sha256" (the SHA-256 of the request body).
A stream is ``data: {"tf_seq": i, "tf_request_sha256": ...}`` events (--events of them,
--event-delay apart, after a silent --think-gap), then ``data: [DONE]``; chunked unless
--close-delimited.  A POST body is read by its Content-Length (absent = empty).

State files (``--state-dir DIR``, prefix ``--name``, default ``tf_stub_llm``)
----------------------------------------------------------------------------
<name>.starts            one line per process start (pid), as the sd stub's.
<name>.pids              one integer pid per line.
<name>.events.jsonl      one JSON line per event (O_APPEND):
                         {"event": "start", "pid", "c", "port"}
                         {"event": "request", "pid", "seq", "method", "path", "length",
                          "sha256", "inflight", "header_names", "stream", "anthropic"}
                          (header_names: received header NAMES, lower-cased; anthropic: the
                           values of anthropic-version / anthropic-beta when present, a
                           protocol token and never a secret)
                         {"event": "answer", "pid", "seq", "status", "sha256"}
                         {"event": "client_gone", "pid", "seq"}   (peer closed while idle)
                         {"event": "closed_by_peer", "pid", "seq"} (a write found it closed)
                         {"event": "stream_end", "pid", "seq", "events"}
                         {"event": "crash", "pid", "seq", "reason"}
<name>.<pid>.<seq>.body  the exact bytes of every POST body received (0600).
<name>.env.json          --dump-env: {"pid", "argv", "env"} (0600).

Behaviour flags
---------------
--ids A,B,C                 the model ids /v1/models lists (default the inferNO trio).
--start-delay S             sleep S seconds before binding (a model load: nothing listens).
--crash-on-start            exit 3 before binding.
--bind-fail                 exit 98 before binding.
--ready-404-for S           for S seconds after the bind GET /v1/models answers 404.
--answer-delay S            sleep S seconds before answering a POST.
--hang-after-accept         a POST is read and recorded, then never answered.
--events N                  stream events (default 3).
--event-delay S             seconds between stream events (default 0).
--event-bytes N             pad every stream event with a "tf_pad" string of N bytes (a flood).
--think-gap S               a silent gap before the first event (a thinking phase).
--close-delimited           a stream is close-delimited (no chunked, no Content-Length).
--drop-after N              after N stream events close the socket with no terminator.
--head-extra "Name: value"  an extra response header line on every POST answer (repeatable).
--head-pad N                an X-Tf-Pad header of N bytes on every POST answer (an oversize head).
--answer-status N           every POST (stream or not) is answered N with a JSON error body.
--response-bytes N          pad a JSON answer to at least N bytes.
--dump-env                  write <name>.env.json at start.
--ignore-term               SIGTERM is ignored.
"""

import argparse
import hashlib
import json
import os
import select
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TF_DEFAULT_NAME = "tf_stub_llm"
TF_DEFAULT_IDS = "Qwen3.8-Flash-Next,bge-m3,bge-reranker-v2-m3"
TF_CHAT_ROUTES = ("/v1/chat/completions", "/v1/messages")
TF_JSON_ROUTES = ("/v1/embeddings", "/v1/rerank")
TF_POST_ROUTES = TF_CHAT_ROUTES + TF_JSON_ROUTES
TF_EXIT_CRASH = 3
TF_EXIT_BIND = 98
TF_POLL_S = 0.05
TF_FOREVER_TICK_S = 1.0
TF_ANTHROPIC = ("anthropic-version", "anthropic-beta")

tf_state_lock = threading.Lock()


class TfState:
    """Process-wide configuration, counters and the start-up incarnation count."""

    def __init__(self, args):
        self.args = args
        self.prior = 0
        self.seq = 0
        self.inflight = 0
        self.bound_at = None
        self.prefix = None
        if args.state_dir:
            self.prefix = os.path.join(args.state_dir, args.name)
            try:
                with open(self.prefix + ".starts", "r", encoding="utf-8") as fh:
                    self.prior = sum(1 for ln in fh if ln.strip())
            except FileNotFoundError:
                self.prior = 0
            tf_append(self.prefix + ".starts", "%d\n" % os.getpid())
            tf_append(self.prefix + ".pids", "%d\n" % os.getpid())

    def next_seq(self):
        with tf_state_lock:
            self.seq += 1
            return self.seq

    def record(self, record):
        if self.prefix:
            tf_append(self.prefix + ".events.jsonl", json.dumps(record) + "\n")

    def save_body(self, seq, body):
        if not self.prefix:
            return
        path = "%s.%d.%d.body" % (self.prefix, os.getpid(), seq)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            view = memoryview(body)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        finally:
            os.close(fd)


def tf_append(path, text):
    """Append one record with O_APPEND so concurrent incarnations never interleave."""
    data = text.encode("utf-8")
    with tf_state_lock:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def tf_forever():
    """Block this thread until the process is killed."""
    while True:
        time.sleep(TF_FOREVER_TICK_S)


def tf_error_body(code, message):
    return json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")


def tf_pad(obj, size):
    """*obj* as JSON bytes, padded (a "tf_pad" key) to at least *size* bytes."""
    body = json.dumps(obj, sort_keys=True).encode("utf-8")
    if size and len(body) < size:
        obj = dict(obj, tf_pad="x" * (size - len(body)))
        body = json.dumps(obj, sort_keys=True).encode("utf-8")
    return body


def tf_answer_body(state, path, sha, model):
    base = {"tf_request_sha256": sha, "model": model}
    if path == "/v1/chat/completions":
        base.update({"id": "tf-chat", "object": "chat.completion",
                     "choices": [{"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": "tf"}}]})
    elif path == "/v1/messages":
        base.update({"id": "tf-msg", "type": "message", "role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "tf"}]})
    elif path == "/v1/embeddings":
        base.update({"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.5]}]})
    else:
        base.update({"results": [{"index": 0, "relevance_score": 0.5}]})
    return tf_pad(base, state.args.response_bytes)


def tf_json_field(body, key):
    """The top-level *key* of a JSON object body, or None (not an object, not JSON)."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return doc.get(key) if isinstance(doc, dict) else None


class TfHandler(BaseHTTPRequestHandler):
    """One request per connection; every answer carries Connection: close."""

    protocol_version = "HTTP/1.1"
    server_version = "tf-stub-llm/1.0"
    sys_version = ""
    tf_state = None   # set on the class by tf_main

    def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
        """Silence the stdlib access log: it would echo request lines."""
        return

    def tf_extra_lines(self):
        for line in self.tf_state.args.head_extra or ():
            name, _sep, value = line.partition(":")
            self.send_header(name.strip(), value.strip())
        if self.tf_state.args.head_pad:
            self.send_header("X-Tf-Pad", "a" * self.tf_state.args.head_pad)

    def tf_send(self, status, body, seq=None, extra=False):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            if extra:
                self.tf_extra_lines()
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True
        if seq is not None:
            self.tf_state.record({"event": "answer", "pid": os.getpid(), "seq": seq, "status": status,
                                  "sha256": hashlib.sha256(body).hexdigest()})

    def tf_loading(self):
        state = self.tf_state
        window = state.args.ready_404_for
        return bool(window) and time.monotonic() - state.bound_at < window

    def tf_read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        chunks = []
        remaining = max(length, 0)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def tf_peer_gone(self, wait):
        """True once the peer closed its end (readable and EOF on peek), polling up to *wait* s."""
        try:
            ready, _, _ = select.select([self.connection], [], [], wait)
            if not ready:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def tf_wait(self, seconds, seq):
        """Sleep *seconds* in ticks; False (and a client_gone record) when the peer closes."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return True
            if self.tf_peer_gone(min(left, TF_POLL_S)):
                self.tf_state.record({"event": "client_gone", "pid": os.getpid(), "seq": seq})
                return False

    def tf_write(self, data, seq, chunked):
        """Write one piece (a chunk when *chunked*); False when the peer is gone."""
        try:
            if chunked:
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            else:
                self.wfile.write(data)
            self.wfile.flush()
            return True
        except OSError:
            self.tf_state.record({"event": "closed_by_peer", "pid": os.getpid(), "seq": seq})
            return False

    def tf_stream(self, seq, sha):
        """An SSE answer: head, events, terminator (unless --drop-after)."""
        state = self.tf_state
        args = state.args
        chunked = not args.close_delimited
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.tf_extra_lines()
            self.end_headers()
            self.wfile.flush()
        except OSError:
            state.record({"event": "closed_by_peer", "pid": os.getpid(), "seq": seq})
            return
        sent = 0
        if args.think_gap and not self.tf_wait(args.think_gap, seq):
            return
        for index in range(args.events):
            if args.drop_after is not None and sent >= args.drop_after:
                state.record({"event": "stream_end", "pid": os.getpid(), "seq": seq, "events": sent})
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            doc = {"tf_seq": index, "tf_request_sha256": sha}
            if args.event_bytes:
                doc["tf_pad"] = "x" * args.event_bytes
            event = ("data: " + json.dumps(doc) + "\n\n").encode("utf-8")
            if not self.tf_write(event, seq, chunked):
                return
            sent += 1
            if args.event_delay and not self.tf_wait(args.event_delay, seq):
                return
        if not self.tf_write(b"data: [DONE]\n\n", seq, chunked):
            return
        if chunked:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        state.record({"event": "stream_end", "pid": os.getpid(), "seq": seq, "events": sent})
        state.record({"event": "answer", "pid": os.getpid(), "seq": seq, "status": 200, "sha256": sha})
        self.close_connection = True

    def do_GET(self):
        if self.path == "/v1/models":
            if self.tf_loading():
                self.tf_send(404, tf_error_body("not_found", "tf model loading"))
                return
            ids = [i for i in self.tf_state.args.ids.split(",") if i]
            doc = {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "tf"} for i in ids]}
            self.tf_send(200, json.dumps(doc).encode("utf-8"))
            return
        if self.path == "/health":
            self.tf_send(200, json.dumps({"status": "ok"}).encode("utf-8"))
            return
        if self.path in TF_POST_ROUTES:
            self.tf_send(405, tf_error_body("method_not_allowed", "tf POST only"))
            return
        self.tf_send(404, tf_error_body("not_found", "tf no such route"))

    def do_POST(self):
        state = self.tf_state
        args = state.args
        seq = state.next_seq()
        body = self.tf_read_body()
        sha = hashlib.sha256(body).hexdigest()
        with tf_state_lock:
            state.inflight += 1
            inflight = state.inflight
        try:
            state.save_body(seq, body)
            stream = tf_json_field(body, "stream") is True
            state.record({"event": "request", "pid": os.getpid(), "seq": seq, "method": "POST",
                          "path": self.path, "length": len(body), "sha256": sha, "inflight": inflight,
                          "header_names": [k.lower() for k in self.headers.keys()], "stream": stream,
                          "anthropic": {k: self.headers.get(k) for k in TF_ANTHROPIC if self.headers.get(k)}})
            if self.path not in TF_POST_ROUTES:
                self.tf_send(404, tf_error_body("not_found", "tf no such route"), seq)
                return
            if args.hang_after_accept:
                tf_forever()
            if args.answer_delay:
                time.sleep(args.answer_delay)
            if args.answer_status != 200:
                self.tf_send(args.answer_status, tf_error_body("tf_status", "tf scripted status"), seq, extra=True)
                return
            if stream and self.path in TF_CHAT_ROUTES:
                self.tf_stream(seq, sha)
                return
            model = tf_json_field(body, "model")
            answer = tf_answer_body(state, self.path, sha, model if isinstance(model, str) else None)
            self.tf_send(200, answer, seq, extra=True)
        finally:
            with tf_state_lock:
                state.inflight -= 1

    def tf_not_allowed(self):
        self.tf_read_body()
        if self.path in TF_POST_ROUTES or self.path == "/v1/models":
            self.tf_send(405, tf_error_body("method_not_allowed", "tf no such method"))
        else:
            self.tf_send(404, tf_error_body("not_found", "tf no such route"))

    do_PUT = tf_not_allowed
    do_DELETE = tf_not_allowed
    do_PATCH = tf_not_allowed


class TfServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def tf_dump_env(state):
    if not state.prefix:
        return
    data = json.dumps({"pid": os.getpid(), "argv": sys.argv, "env": dict(os.environ)},
                      sort_keys=True).encode("utf-8")
    fd = os.open(state.prefix + ".env.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def tf_parse_args(argv):
    ap = argparse.ArgumentParser(description="tf adversarial stub inferNO-style LLM child")
    ap.add_argument("--host", "--listen-ip", dest="host", default="127.0.0.1")
    ap.add_argument("--port", "--listen-port", dest="port", type=int, default=0)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--name", default=TF_DEFAULT_NAME)
    ap.add_argument("--ids", default=TF_DEFAULT_IDS)
    ap.add_argument("--start-delay", type=float, default=0.0)
    ap.add_argument("--crash-on-start", action="store_true")
    ap.add_argument("--bind-fail", action="store_true")
    ap.add_argument("--ready-404-for", type=float, default=0.0)
    ap.add_argument("--answer-delay", type=float, default=0.0)
    ap.add_argument("--hang-after-accept", action="store_true")
    ap.add_argument("--events", type=int, default=3)
    ap.add_argument("--event-delay", type=float, default=0.0)
    ap.add_argument("--event-bytes", type=int, default=0)
    ap.add_argument("--think-gap", type=float, default=0.0)
    ap.add_argument("--close-delimited", action="store_true")
    ap.add_argument("--drop-after", type=int, default=None)
    ap.add_argument("--head-extra", action="append", default=None)
    ap.add_argument("--head-pad", type=int, default=0)
    ap.add_argument("--answer-status", type=int, default=200)
    ap.add_argument("--response-bytes", type=int, default=0)
    ap.add_argument("--dump-env", action="store_true")
    ap.add_argument("--ignore-term", action="store_true")
    return ap.parse_args(argv)


def tf_main(argv=None):
    args = tf_parse_args(argv)
    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    state = TfState(args)
    if args.dump_env:
        tf_dump_env(state)
    if args.crash_on_start:
        sys.stderr.write("tf crash on start\n")
        return TF_EXIT_CRASH
    if args.start_delay:
        time.sleep(args.start_delay)
    if args.bind_fail:
        sys.stderr.write("tf bind failed: address already in use\n")
        return TF_EXIT_BIND
    TfHandler.tf_state = state
    try:
        server = TfServer((args.host, args.port), TfHandler)
    except OSError:
        sys.stderr.write("tf bind failed\n")
        return TF_EXIT_BIND
    state.bound_at = time.monotonic()
    state.record({"event": "start", "pid": os.getpid(), "c": state.prior,
                  "port": server.server_address[1]})
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(tf_main())
