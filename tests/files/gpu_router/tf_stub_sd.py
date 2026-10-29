#!/usr/bin/env python3
"""tf_stub_sd -- the adversarial stub sd-server child for tests/test_gpu_router.py.

A fixture, not project code (tests/files/README.md: every symbol starts with
``tf``).  Stdlib only, Python 3.9, run as ``__main__`` by the gpu-router suite
as a model's ``bin``; nothing imports it.  It stands in for stable-diffusion.cpp
``sd-server``: a loopback HTTP/1.1 server (``Connection: close`` on every
answer) serving the four synchronous image routes with canned sd-shaped JSON.
It never depends on the real sd-server flag names: the suite's config ``args``
pass ``--host``/``--port`` (``--listen-ip``/``--listen-port`` are accepted as
aliases) and whatever behaviour flags a case needs.

It NEVER prints a request body, a header value or an environment value to
stdout or stderr; the only stderr lines are the fixed texts of a refusal.

Routes
------
POST /v1/images/generations, /v1/images/edits    {"created": 0, "data": [{"b64_json": ...}],
                                                  "tf_request_sha256": <sha of the body>}
POST /sdapi/v1/txt2img, /sdapi/v1/img2img         {"images": [...], "parameters": {},
                                                  "info": "", "tf_request_sha256": ...}
GET  /                                            404 (the loaded-model readiness answer;
                                                  503 inside the --ready-503-for window)
any other method on an image route                405
any other path                                    404
Every error body is JSON ``{"error": {"code", "message"}}``.  A POST body is
read by its Content-Length (absent = empty).

State files (``--state-dir DIR``, prefix ``--name``, default ``tf_stub_sd``)
--------------------------------------------------------------------------
<name>.starts            one line per process start; the stub FIRST counts the
                         lines already there (c, 0 if absent), THEN appends its
                         own pid -- so ``*-first N`` flags can fail only the first
                         N incarnations.
<name>.pids              one integer pid per line: this process, and every
                         grandchild it spawns (``--spawn-grandchild``), so a
                         reaper that reads it sees every process to check.
<name>.events.jsonl      one JSON line per event (O_APPEND):
                         {"event": "start", "pid", "c", "port"}
                         {"event": "request", "pid", "seq", "method", "path",
                          "length", "sha256", "inflight", "header_names"}
                          (inflight counts the POSTs being handled, this one
                          included; header_names lists the received header
                          NAMES, lower-cased, in arrival order -- never a value)
                         {"event": "answer", "pid", "seq", "status", "sha256"}
                          (sha256 of the answer body sent)
                         {"event": "crash", "pid", "seq", "reason"}
                         {"event": "generating", "pid", "seq"}
                         {"event": "client_gone", "pid", "seq"}  (--keep-generating:
                          the client socket closed while the worker kept going)
                         {"event": "grandchild", "pid", "child"}
<name>.<pid>.<seq>.body  the exact bytes of every POST body received (0600).
<name>.busy              --keep-generating heartbeat: one line every
                         TF_BUSY_TICK_S while a worker is generating (at most
                         TF_BUSY_MAX_LINES lines), so a test can prove the child
                         stopped generating once it was killed.
<name>.env.json          --dump-env: {"pid", "argv", "env"} (0600).

Behaviour flags
---------------
--crash-on-start            exit 3 before binding.
--crash-on-start-first N    exit 3 before binding while c < N.
--start-delay S             sleep S seconds before binding (a cold model load).
--bind-fail                 exit 98 before binding, as if the port were taken.
--bind-fail-first N         exit 98 before binding while c < N.
--crash-after N             answer N POSTs, then on the next POST: record it, exit
                            3 with no answer (per process).
--crash-on-marker TEXT      a POST whose body contains TEXT: record it, exit 3 with
                            no answer (the poison prompt).
--ignore-term               SIGTERM is ignored (only SIGKILL ends the stub).
--spawn-grandchild          at start, ``sleep 600`` in the stub's own process
                            group, all streams DEVNULL; its pid goes to .pids.
--hang                      bind and listen, never accept: the port takes
                            connections and no request is ever answered, not even
                            GET / (a child that never becomes ready).
--hang-after-accept         a POST is read in full and recorded, then never
                            answered (GET / still answers, so the child is ready).
--keep-generating           a POST is read and recorded, then a worker generates
                            forever: it never answers, writes the .busy heartbeat,
                            and records "client_gone" once the client socket closes.
--answer-delay S            sleep S seconds before answering a POST (a slow image).
--response-bytes N          pad the image field so the answer body is at least N
                            bytes (the response-limit cases).
--ready-503-for S           for S seconds after bind EVERY request answers 503
                            {"error": {"code": "model_loading"}}; then GET / is 404.
--dump-env                  write <name>.env.json at start.
--control-file F            if F exists when a POST arrives, its first non-empty
                            line picks that POST's behaviour: ``ok``, ``crash``,
                            ``hang-after-accept``, ``keep-generating``, or one of
                            the raw answers a relay must refuse (502
                            upstream_failed): ``encoded`` (200 with
                            Content-Encoding: gzip), ``status-1xx`` (a 103 Early
                            Hints head, then a 200), ``status-3xx`` (a 302 with a
                            Location); an unknown word is ``ok``; argv flags
                            still apply.
"""

import argparse
import base64
import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TF_DEFAULT_NAME = "tf_stub_sd"
TF_OPENAI_ROUTES = ("/v1/images/generations", "/v1/images/edits")
TF_SDAPI_ROUTES = ("/sdapi/v1/txt2img", "/sdapi/v1/img2img")
TF_IMAGE_ROUTES = TF_OPENAI_ROUTES + TF_SDAPI_ROUTES
TF_EXIT_CRASH = 3
TF_EXIT_BIND = 98
TF_BUSY_TICK_S = 0.2
TF_BUSY_MAX_LINES = 3000
TF_FOREVER_TICK_S = 1.0
TF_RAW_WORDS = ("encoded", "status-1xx", "status-3xx")
TF_CONTROL_WORDS = ("ok", "crash", "hang-after-accept", "keep-generating") + TF_RAW_WORDS

tf_state_lock = threading.Lock()


class TfState:
    """Process-wide configuration, counters and the start-up incarnation count."""

    def __init__(self, args):
        self.args = args
        self.prior = 0
        self.seq = 0
        self.inflight = 0
        self.answered = 0
        self.bound_at = None
        self.busy_lines = 0
        self.prefix = None
        if args.state_dir:
            self.prefix = os.path.join(args.state_dir, args.name)
            # Count FIRST, then append our own pid: every start leaves one line.
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

    def heartbeat(self, seq):
        if not self.prefix:
            return
        with tf_state_lock:
            if self.busy_lines >= TF_BUSY_MAX_LINES:
                return
            self.busy_lines += 1
        tf_append(self.prefix + ".busy", "%d %d %.3f\n" % (os.getpid(), seq, time.monotonic()))


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


def tf_crash(state, seq, reason):
    state.record({"event": "crash", "pid": os.getpid(), "seq": seq, "reason": reason})
    os._exit(TF_EXIT_CRASH)


def tf_control_word(state):
    """--control-file: the first non-empty line, or ``ok``."""
    path = state.args.control_file
    if not path:
        return "ok"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                word = line.strip()
                if word:
                    return word if word in TF_CONTROL_WORDS else "ok"
    except OSError:
        pass
    return "ok"


def tf_image_payload(state, sha):
    """The canned image field: deterministic, padded to --response-bytes."""
    raw = ("tf-png:" + sha[:16]).encode("ascii")
    pad = state.args.response_bytes
    if pad and pad > len(raw):
        raw = raw + b"\0" * (pad - len(raw))
    return base64.b64encode(raw).decode("ascii")


def tf_answer_body(state, path, sha):
    image = tf_image_payload(state, sha)
    if path in TF_OPENAI_ROUTES:
        obj = {"created": 0, "data": [{"b64_json": image}], "tf_request_sha256": sha}
    else:
        obj = {"images": [image], "parameters": {}, "info": "", "tf_request_sha256": sha}
    return json.dumps(obj, sort_keys=True).encode("utf-8")


def tf_error_body(code, message):
    return json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")


class TfHandler(BaseHTTPRequestHandler):
    """One request per connection; every answer carries Connection: close."""

    protocol_version = "HTTP/1.1"
    server_version = "tf-stub-sd/1.0"
    sys_version = ""
    tf_state = None   # set on the class by tf_main

    def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
        """Silence the stdlib access log: it would echo request lines."""
        return

    def tf_send(self, status, body, seq=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True
        if seq is not None:
            self.tf_state.record({"event": "answer", "pid": os.getpid(), "seq": seq,
                                  "status": status,
                                  "sha256": hashlib.sha256(body).hexdigest()})

    def tf_send_raw(self, word, answer, seq):
        """One of TF_RAW_WORDS: a hand-built answer head a relay must refuse."""
        length = b"Content-Length: %d\r\n" % len(answer)
        if word == "encoded":
            status = 200
            wire = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Encoding: gzip\r\n" + length + b"Connection: close\r\n\r\n" + answer)
        elif word == "status-1xx":
            status = 103
            wire = (b"HTTP/1.1 103 Early Hints\r\nLink: </tf.png>; rel=preload\r\n\r\n"
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" + length
                    + b"Connection: close\r\n\r\n" + answer)
        else:
            status = 302
            wire = (b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:9/tf\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n")
        try:
            self.wfile.write(wire)
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True
        self.tf_state.record({"event": "answer", "pid": os.getpid(), "seq": seq,
                              "status": status, "sha256": hashlib.sha256(wire).hexdigest()})

    def tf_loading(self):
        state = self.tf_state
        window = state.args.ready_503_for
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

    def tf_client_gone(self):
        """True once the client closed its end (readable and EOF on peek)."""
        try:
            ready, _, _ = select.select([self.connection], [], [], TF_BUSY_TICK_S)
            if not ready:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def tf_keep_generating(self, seq):
        state = self.tf_state
        state.record({"event": "generating", "pid": os.getpid(), "seq": seq})
        gone = False
        while True:
            state.heartbeat(seq)
            if gone:
                time.sleep(TF_BUSY_TICK_S)
            elif self.tf_client_gone():
                gone = True
                state.record({"event": "client_gone", "pid": os.getpid(), "seq": seq})

    def do_GET(self):
        if self.tf_loading():
            self.tf_send(503, tf_error_body("model_loading", "tf model loading"))
            return
        if self.path in TF_IMAGE_ROUTES:
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
            state.record({"event": "request", "pid": os.getpid(), "seq": seq,
                          "method": "POST", "path": self.path, "length": len(body),
                          "sha256": sha, "inflight": inflight,
                          "header_names": [k.lower() for k in self.headers.keys()]})
            if self.tf_loading():
                self.tf_send(503, tf_error_body("model_loading", "tf model loading"), seq)
                return
            if self.path not in TF_IMAGE_ROUTES:
                self.tf_send(404, tf_error_body("not_found", "tf no such route"), seq)
                return
            word = tf_control_word(state)
            if word == "crash":
                tf_crash(state, seq, "control")
            if args.crash_on_marker and args.crash_on_marker.encode("utf-8") in body:
                tf_crash(state, seq, "marker")
            if args.crash_after is not None and state.answered >= args.crash_after:
                tf_crash(state, seq, "crash_after")
            if args.hang_after_accept or word == "hang-after-accept":
                tf_forever()
            if args.keep_generating or word == "keep-generating":
                self.tf_keep_generating(seq)
            if args.answer_delay:
                time.sleep(args.answer_delay)
            answer = tf_answer_body(state, self.path, sha)
            if word in TF_RAW_WORDS:
                self.tf_send_raw(word, answer, seq)
                return
            with tf_state_lock:
                state.answered += 1
            self.tf_send(200, answer, seq)
        finally:
            with tf_state_lock:
                state.inflight -= 1

    def tf_not_allowed(self):
        self.tf_read_body()
        if self.path in TF_IMAGE_ROUTES:
            self.tf_send(405, tf_error_body("method_not_allowed", "tf POST only"))
        else:
            self.tf_send(404, tf_error_body("not_found", "tf no such route"))

    do_PUT = tf_not_allowed
    do_DELETE = tf_not_allowed
    do_PATCH = tf_not_allowed


class TfServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def tf_spawn_grandchild(state):
    proc = subprocess.Popen(["sleep", "600"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if state.prefix:
        tf_append(state.prefix + ".pids", "%d\n" % proc.pid)
    state.record({"event": "grandchild", "pid": os.getpid(), "child": proc.pid})


def tf_dump_env(state):
    if not state.prefix:
        return
    data = json.dumps({"pid": os.getpid(), "argv": sys.argv,
                       "env": dict(os.environ)}, sort_keys=True).encode("utf-8")
    fd = os.open(state.prefix + ".env.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def tf_parse_args(argv):
    ap = argparse.ArgumentParser(description="tf adversarial stub sd-server child")
    ap.add_argument("--host", "--listen-ip", dest="host", default="127.0.0.1")
    ap.add_argument("--port", "--listen-port", dest="port", type=int, default=0)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--name", default=TF_DEFAULT_NAME)
    ap.add_argument("--crash-on-start", action="store_true")
    ap.add_argument("--crash-on-start-first", type=int, default=None)
    ap.add_argument("--start-delay", type=float, default=0.0)
    ap.add_argument("--bind-fail", action="store_true")
    ap.add_argument("--bind-fail-first", type=int, default=None)
    ap.add_argument("--crash-after", type=int, default=None)
    ap.add_argument("--crash-on-marker", default=None)
    ap.add_argument("--ignore-term", action="store_true")
    ap.add_argument("--spawn-grandchild", action="store_true")
    ap.add_argument("--hang", action="store_true")
    ap.add_argument("--hang-after-accept", action="store_true")
    ap.add_argument("--keep-generating", action="store_true")
    ap.add_argument("--answer-delay", type=float, default=0.0)
    ap.add_argument("--response-bytes", type=int, default=0)
    ap.add_argument("--ready-503-for", type=float, default=0.0)
    ap.add_argument("--dump-env", action="store_true")
    ap.add_argument("--control-file", default=None)
    return ap.parse_args(argv)


def tf_main(argv=None):
    args = tf_parse_args(argv)
    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    state = TfState(args)
    if args.dump_env:
        tf_dump_env(state)
    if args.spawn_grandchild:
        tf_spawn_grandchild(state)
    if args.crash_on_start or (args.crash_on_start_first is not None
                               and state.prior < args.crash_on_start_first):
        sys.stderr.write("tf crash on start\n")
        return TF_EXIT_CRASH
    if args.start_delay:
        time.sleep(args.start_delay)
    if args.bind_fail or (args.bind_fail_first is not None
                          and state.prior < args.bind_fail_first):
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
    if args.hang:
        tf_forever()
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(tf_main())
