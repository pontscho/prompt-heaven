#!/usr/bin/env python3
"""An in-memory stand-in for the ai-soul hub's POST /pack/sync route.

Used by tests/test_pack.py. Stdlib only; one ThreadingHTTPServer on
127.0.0.1:0; every state lives in memory. It takes the pack-sync/1 body as
strictly as the board does, so the client's mistakes show up:

  * bearer check first (401), then the 2 MiB body cap and strict JSON (400:
    duplicate keys, NaN), then the exact body shape (400): the top-level keys
    exactly member / project / export / plans, `export` null or exactly
    {items}, every item and plan and task with exactly its contract keys, no
    plan file twice, at most 8 plans naming one item;
  * every display row (an item title or severity, a task text) judged by the
    server's text rule: one line, no control / bidi / format / separator /
    private / surrogate / noncharacter code point, and no `<system-reminder`
    or `<<peer-message` marker, raw or folded (NFKD, then white space, `_`,
    dashes and combining marks removed: src/types/peers.ts peerMarkerFold).
    An unsanitised `<system-reminder>` row is a 400, so a client that forgot
    sanitise_row cannot pass;
  * the answer {changed, exportIgnored, plansApplied, plansSkipped,
    plansPending}: by default changed true, nothing ignored, every plan
    applied; `answer(**overrides)` queues the overrides of the next 200s, in
    order.

Fault injection per route: `fault(route, kind, count=1, ...)`. Kinds: status
(`status`, optional `retry_after`: the exact header text, None = no header),
redirect (302 to `target`), raw (answer `body` bytes with `status`), drop
(close without answering). Faults are consumed in the order added; the route
`*` matches every route. Every request is recorded in `requests` (route,
status, body, raw bytes), the bearer token never: only whether it was
accepted. `seen_tokens` holds every Authorization value received.
"""

import json
import re
import secrets
import socket
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTES = ("sync",)
MAX_BODY = 2 * 1048576
ITEM_KEYS = {"id", "title", "state", "horizon", "rank", "ready", "blockedBy", "tags", "severity"}
PLAN_KEYS = {"itemId", "file", "tasks"}
TASK_KEYS = {"taskId", "status", "text"}
LIVE_STATES = ("idea", "planned", "active")
HORIZONS = ("now", "next", "later", "unset")
TASK_STATUSES = ("pending", "in_progress", "completed", "cancel", "unknown")
MAX_PLANS_PER_ITEM = 8
ITEM_ID_RE = re.compile(r"R-[0-9]{4}\Z")
PLAN_FILE_RE = re.compile(r"requirements-[a-z0-9._-]{1,64}\.yaml\Z")
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
MEMBER_RE = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}\Z")
TASK_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
RAW_MARKER_RE = re.compile(r"</?system-reminder|<<(?:end-)?peer-message", re.IGNORECASE)
FOLDED_MARKER_RE = re.compile(r"</?systemreminder|<<(?:end)?peermessage", re.IGNORECASE)


class Bad(Exception):
    def __init__(self, status, why=""):
        super().__init__(why)
        self.status = status


def _no_dups(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _no_const(name):
    raise ValueError("non-finite number")


def marker_fold(text):
    """The server's peerMarkerFold: NFKD, white space, `_`, Pd and Mn removed."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not (ch.isspace() or ch == "_" or unicodedata.category(ch) in ("Pd", "Mn")))


def row_problem(text, cap):
    """The server's display-row rule: None when fine, else a fixed phrase
    (never the text). An empty row is allowed (a task with no description)."""
    if not isinstance(text, str):
        return "type"
    if len(text) > cap:
        return "length"
    if unicodedata.normalize("NFC", text) != text:
        return "nfc"
    for ch in text:
        cat = unicodedata.category(ch)
        cp = ord(ch)
        if (cat in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp") or 0xFE00 <= cp <= 0xFE0F
                or 0xE0000 <= cp <= 0xE0FFF or 0xFDD0 <= cp <= 0xFDEF
                or (cp & 0xFFFE) == 0xFFFE or cp in (0x034F, 0x115F, 0x1160,
                                                    0x3164, 0xFFA0, 0x180E)):
            return "character"
    if RAW_MARKER_RE.search(text) or FOLDED_MARKER_RE.search(marker_fold(text)):
        return "marker"
    return None


class _QuietServer(ThreadingHTTPServer):
    """A dropped or abandoned connection is the test's doing, not a traceback."""

    def handle_error(self, request, client_address):
        return


class StubPackHub:
    def __init__(self, token=None):
        self.token = token or "aisc1_" + secrets.token_urlsafe(24)
        self.tokens = {self.token}
        self.lock = threading.Lock()
        self.requests = []          # dicts: route, status, body, raw
        self.seen_tokens = set()
        self.faults = []
        self.answers = []           # override dicts for the next 200s
        self.server = _QuietServer(("127.0.0.1", 0), _make_handler(self))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- test knobs -----------------------------------------------------------

    def fault(self, route, kind, count=1, **kw):
        with self.lock:
            self.faults.append(dict(kw, route=route, kind=kind, left=count))

    def answer(self, **overrides):
        with self.lock:
            self.answers.append(overrides)

    def reset(self):
        with self.lock:
            self.faults = []
            self.answers = []
            self.requests = []

    def count(self, route="sync"):
        with self.lock:
            return sum(1 for r in self.requests if r["route"] == route)

    def bodies(self, route="sync"):
        with self.lock:
            return [r["body"] for r in self.requests if r["route"] == route and r["body"] is not None]

    def statuses(self, route="sync"):
        with self.lock:
            return [r["status"] for r in self.requests if r["route"] == route]

    # -- the route (lock held, body already strict JSON) -------------------------

    def handle(self, route, body):
        return getattr(self, "_r_" + route)(body)

    def _r_sync(self, body):
        if not isinstance(body, dict) or set(body) != {"member", "project", "export", "plans"}:
            raise Bad(400, "keys")
        if not (isinstance(body["member"], str) and MEMBER_RE.match(body["member"])):
            raise Bad(400, "member")
        if not (isinstance(body["project"], str) and PROJECT_RE.match(body["project"])):
            raise Bad(400, "project")
        export = body["export"]
        if export is not None:
            if not isinstance(export, dict) or set(export) != {"items"} or not isinstance(export["items"], list):
                raise Bad(400, "export")
            seen = set()
            for item in export["items"]:
                self._check_item(item, seen)
        plans = body["plans"]
        if not isinstance(plans, list):
            raise Bad(400, "plans")
        files, per_item = set(), {}
        for plan in plans:
            if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
                raise Bad(400, "plan keys")
            if not (isinstance(plan["itemId"], str) and ITEM_ID_RE.match(plan["itemId"])):
                raise Bad(400, "plan itemId")
            if not (isinstance(plan["file"], str) and PLAN_FILE_RE.match(plan["file"])) or plan["file"] in files:
                raise Bad(400, "plan file")
            files.add(plan["file"])
            per_item[plan["itemId"]] = per_item.get(plan["itemId"], 0) + 1
            if per_item[plan["itemId"]] > MAX_PLANS_PER_ITEM:
                raise Bad(400, "plans per item")
            if not isinstance(plan["tasks"], list):
                raise Bad(400, "tasks")
            for task in plan["tasks"]:
                if not isinstance(task, dict) or set(task) != TASK_KEYS:
                    raise Bad(400, "task keys")
                if not (isinstance(task["taskId"], str) and TASK_ID_RE.match(task["taskId"])):
                    raise Bad(400, "taskId")
                if task["status"] not in TASK_STATUSES:
                    raise Bad(400, "status")
                if row_problem(task["text"], 300) is not None:
                    raise Bad(400, "task text")
        out = {"changed": True, "exportIgnored": False, "plansApplied": len(plans),
               "plansSkipped": 0, "plansPending": 0}
        if self.answers:
            out.update(self.answers.pop(0))
        return out

    def _check_item(self, item, seen):
        if not isinstance(item, dict) or set(item) != ITEM_KEYS:
            raise Bad(400, "item keys")
        if not (isinstance(item["id"], str) and ITEM_ID_RE.match(item["id"])) or item["id"] in seen:
            raise Bad(400, "item id")
        seen.add(item["id"])
        if row_problem(item["title"], 200) is not None or item["title"] == "":
            raise Bad(400, "title")
        if item["state"] not in LIVE_STATES or item["horizon"] not in HORIZONS:
            raise Bad(400, "state")
        if item["rank"] is not None and type(item["rank"]) is not int:
            raise Bad(400, "rank")
        if item["ready"] is not None and type(item["ready"]) is not bool:
            raise Bad(400, "ready")
        if not isinstance(item["blockedBy"], list) or not all(isinstance(b, str) and ITEM_ID_RE.match(b) for b in item["blockedBy"]):
            raise Bad(400, "blockedBy")
        if not isinstance(item["tags"], list) or not all(isinstance(t, str) for t in item["tags"]):
            raise Bad(400, "tags")
        if item["severity"] is not None and row_problem(item["severity"], 32) is not None:
            raise Bad(400, "severity")


def _make_handler(hub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _reply(self, status, payload=None, raw=None, headers=()):
            data = raw if raw is not None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _next_fault(self, route):
            with hub.lock:
                for fault in hub.faults:
                    if fault["left"] > 0 and fault["route"] in (route, "*"):
                        fault["left"] -= 1
                        return dict(fault)
            return None

        def do_POST(self):  # noqa: N802
            route = self.path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(length, MAX_BODY + 1)) if length else b""
            auth = self.headers.get("Authorization", "")
            record = {"route": route, "status": None, "body": None, "raw": raw, "path": self.path}
            with hub.lock:
                hub.requests.append(record)
                hub.seen_tokens.add(auth)
            if not self.path.startswith("/pack/") or route not in ROUTES:
                record["status"] = 404
                self._reply(404, {"error": "no route"})
                return
            fault = self._next_fault(route)
            if fault is not None and self._fault(fault, record):
                return
            status, payload = 200, None
            try:
                token = auth[7:] if auth.startswith("Bearer ") else None
                with hub.lock:
                    ok = token is not None and token in hub.tokens
                if not ok:
                    raise Bad(401)
                if length > MAX_BODY or len(raw) > MAX_BODY:
                    raise Bad(413, "size")
                try:
                    body = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dups,
                                      parse_constant=_no_const)
                except (ValueError, UnicodeDecodeError):
                    raise Bad(400, "json")
                record["body"] = body
                with hub.lock:
                    payload = hub.handle(route, body)
            except Bad as exc:
                status, payload = exc.status, {"error": "refused"}
                record["why"] = str(exc)
            record["status"] = status
            self._reply(status, payload)

        def _fault(self, fault, record):
            kind = fault["kind"]
            if kind == "status":
                headers = ()
                if fault.get("retry_after") is not None:
                    headers = (("Retry-After", fault["retry_after"]),)
                record["status"] = fault.get("status", 503)
                self._reply(record["status"], {"error": "fault body text"}, headers=headers)
                return True
            if kind == "redirect":
                record["status"] = 302
                self._reply(302, {"error": "moved"}, headers=(("Location", fault["target"]),))
                return True
            if kind == "raw":
                record["status"] = fault.get("status", 200)
                self._reply(record["status"], raw=fault["body"])
                return True
            if kind == "drop":
                record["status"] = "dropped"
                self.close_connection = True
                try:
                    self.request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return True
            return False

    return Handler
