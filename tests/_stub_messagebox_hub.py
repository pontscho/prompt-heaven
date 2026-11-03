#!/usr/bin/env python3
"""An in-memory stand-in for the ai-soul hub's /peers/* routes.

Used by tests/test_messagebox.py (and by whoever builds the hook against the
same contract). Stdlib only; one ThreadingHTTPServer on 127.0.0.1:0; every
state lives in memory. It implements the six POST routes of the peer messaging
design (register, poll, list, send, status, bye) strictly enough that the
client's mistakes show up:

  * bearer check first (401), then a strict body (400: duplicate keys, NaN,
    unknown keys, wrong types, body over 64 KiB for send / 8 KiB otherwise),
    then the logic (404 for an unknown peer, a foreign token or an unknown
    target);
  * register: name uniqueness among live peers with -2 / -3 suffixes, case
    insensitive; (instanceKey, token) idempotence, another token 404; 16 live
    peers, the 17th 409; a peer is stale after `stale_s` without a poll;
  * the ack state machine queued < fetched < enqueued < read, forward only,
    `expired` absorbing, `undelivered` overridable by enqueued / read; acks for
    a message that is not the caller's are ignored;
  * poll lists the caller's queued messages in seq order, at most 20 and 64 KiB
    of text (always the first); send validates to / self / replyTo / text, the
    text rule being the hub's own (NFC, 1-8000 code points, 16 KiB, no
    control / bidi / format / separator / private / surrogate character other
    than \\n and \\t, no <system-reminder tag) and never echoed;
  * fault injection per route: `fault(route, kind, count=1, ...)`.

Fault kinds: status (status=429, retry_after=1), redirect (302 to a loopback
URL, `target` optional), drop (close without answering), slow (sleep
`seconds`, then answer normally), unknown_peer (404), raw (answer `body`
bytes with `status`). Faults of one route are consumed in the order added; the
route `*` matches every route. Every request is recorded in `requests`.

The raw bearer token is never stored in `requests`: only whether it was
accepted. `seen_tokens` is the set of Authorization values received, for the
tests that prove a token stayed home.
"""

import hashlib
import json
import re
import secrets
import socket
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTES = ("register", "poll", "list", "send", "status", "bye")
RANK = {"queued": 0, "fetched": 1, "enqueued": 2, "read": 3}
ACK_STATES = ("fetched", "enqueued", "read", "rejected")
NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
KEY_RE = re.compile(r"[A-Za-z0-9_-]{22,43}\Z")
ID_RE = re.compile(r"ms_[A-Za-z0-9_-]{22}\Z")
MAX_BODY = {"send": 65536}
DEFAULT_MAX_BODY = 8192
MAX_LIVE_PEERS = 16
MAX_QUEUED_PER_PEER = 100
MAX_MESSAGES = 1000
POLL_BATCH = 20
POLL_TEXT_BYTES = 65536
POLL_MS = 3000


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


def hub_text_problem(text):
    """The hub's text rule: None when fine, else a fixed phrase (never the text)."""
    text = unicodedata.normalize("NFC", text)
    if not 1 <= len(text) <= 8000:
        return "length"
    for ch in text:
        if ch in "\n\t":
            continue
        cat = unicodedata.category(ch)
        cp = ord(ch)
        if (cat in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp") or 0xFE00 <= cp <= 0xFE0F
                or 0xE0000 <= cp <= 0xE0FFF or 0xFDD0 <= cp <= 0xFDEF
                or (cp & 0xFFFE) == 0xFFFE or cp in (0x034F, 0x115F, 0x1160,
                                                    0x3164, 0xFFA0, 0x180E)):
            return "character"
    low = text.lower()
    if "<system-reminder" in low or "</system-reminder" in low:
        return "markup"
    try:
        if len(text.encode("utf-8")) > 16384:
            return "bytes"
    except UnicodeEncodeError:
        return "character"
    return None


class _QuietServer(ThreadingHTTPServer):
    """A dropped or abandoned connection is the test's doing, not a traceback."""

    def handle_error(self, request, client_address):
        return


class StubHub:
    def __init__(self, token=None, stale_s=90.0, ttl_s=7 * 86400.0):
        self.token = token or "aisc1_" + secrets.token_urlsafe(24)
        self.tokens = {self.token}
        self.stale_s = stale_s
        self.ttl_s = ttl_s
        self.lock = threading.Lock()
        self.peers = {}             # peer_id -> dict
        self.messages = {}          # msg_id -> dict
        self.order = []             # msg ids in seq order
        self.seq = 0
        self.requests = []          # dicts: route, status, body, t
        self.seen_tokens = set()
        self.faults = []            # dicts
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

    def clear_faults(self):
        with self.lock:
            self.faults = []

    def add_token(self, token):
        with self.lock:
            self.tokens.add(token)

    def revoke_token(self, token):
        with self.lock:
            self.tokens.discard(token)

    def forget_peer(self, peer_id):
        """Make the hub lose a peer row (a purge): the next poll is a 404."""
        with self.lock:
            self.peers.pop(peer_id, None)

    def count(self, route):
        with self.lock:
            return sum(1 for r in self.requests if r["route"] == route)

    def bodies(self, route):
        with self.lock:
            return [r["body"] for r in self.requests if r["route"] == route]

    def times(self, route):
        with self.lock:
            return [r["t"] for r in self.requests if r["route"] == route]

    def peer_by_name(self, name):
        with self.lock:
            for peer in self.peers.values():
                if peer["name"].lower() == name.lower() and not peer["gone"]:
                    return dict(peer)
        return None

    def message_state(self, msg_id):
        with self.lock:
            msg = self.messages.get(msg_id)
            return msg["state"] if msg else None

    def requeue(self, msg_id):
        """Put a message back to `queued` (the hub missed an ack)."""
        with self.lock:
            self.messages[msg_id]["state"] = "queued"

    def age_message(self, msg_id, seconds):
        with self.lock:
            self.messages[msg_id]["created"] -= seconds

    def enqueue(self, to_name, text, from_name="other", from_host="elsewhere", reply_to=None):
        """Put a message into `to_name`'s queue as if a peer had sent it."""
        with self.lock:
            target = self._live_by_name(to_name)
            return self._new_message(None, target["peer_id"], from_name, from_host,
                                     text, reply_to)

    # -- internals (lock held) --------------------------------------------------

    def _live(self, peer):
        return (not peer["gone"]
                and time.monotonic() - peer["last_seen"] <= self.stale_s)

    def _live_by_name(self, name):
        for peer in self.peers.values():
            if self._live(peer) and peer["name"].lower() == name.lower():
                return peer
        return None

    def _new_message(self, from_peer, to_peer, from_name, from_host, text, reply_to):
        self.seq += 1
        msg_id = "ms_" + secrets.token_urlsafe(16)
        self.messages[msg_id] = {
            "msg_id": msg_id, "seq": self.seq, "from_peer": from_peer,
            "to_peer": to_peer, "from_name": from_name, "from_host": from_host,
            "text": text, "reply_to": reply_to, "state": "queued",
            "created": time.time(), "updated": time.time()}
        self.order.append(msg_id)
        return msg_id

    def _state_of(self, msg):
        if msg["state"] != "read" and msg["state"] != "expired" \
                and time.time() - msg["created"] > self.ttl_s:
            msg["state"] = "expired"
        return msg["state"]

    def _apply_ack(self, peer, msg_id, state):
        msg = self.messages.get(msg_id)
        if msg is None or msg["to_peer"] != peer["peer_id"]:
            return
        cur = self._state_of(msg)
        if cur in ("expired", "rejected"):
            return
        if state == "rejected":
            # only a message not yet handed to the session can be refused
            if cur in ("queued", "fetched"):
                msg["state"] = "rejected"
                msg["updated"] = time.time()
            return
        if cur == "undelivered":
            if state in ("enqueued", "read"):
                msg["state"] = state
                msg["updated"] = time.time()
            return
        if RANK[state] > RANK[cur]:
            msg["state"] = state
            msg["updated"] = time.time()

    def _peer_for(self, body, token_hash):
        peer = self.peers.get(body["peerId"])
        if peer is None or peer["token_hash"] != token_hash:
            raise Bad(404)
        return peer

    # -- the routes (lock held, body already strict) -----------------------------

    def handle(self, route, body, token_hash):
        return getattr(self, "_r_" + route)(body, token_hash)

    def _need(self, body, spec):
        for key, kind in spec.items():
            optional = key.endswith("?")
            name = key.rstrip("?")
            if name not in body:
                if optional:
                    continue
                raise Bad(400, "missing " + name)
            if not isinstance(body[name], kind) or isinstance(body[name], bool) \
                    and kind is not bool:
                raise Bad(400, "type " + name)
        extra = set(body) - {k.rstrip("?") for k in spec}
        if extra:
            raise Bad(400, "unknown key")

    def _r_register(self, body, token_hash):
        self._need(body, {"instanceKey": str, "name": str, "host": str})
        if not (KEY_RE.match(body["instanceKey"]) and NAME_RE.match(body["name"])
                and NAME_RE.match(body["host"])):
            raise Bad(400, "grammar")
        existing = None
        for peer in self.peers.values():
            if peer["instance_key"] == body["instanceKey"]:
                existing = peer
        if existing is not None:
            if existing["token_hash"] != token_hash:
                raise Bad(404)
            if not existing["gone"]:
                existing["last_seen"] = time.monotonic()
                return {"peerId": existing["peer_id"], "name": existing["name"],
                        "pollMs": POLL_MS, "staleMs": int(self.stale_s * 1000)}
        live = [p for p in self.peers.values() if self._live(p)]
        if existing is None and len(live) >= MAX_LIVE_PEERS:
            raise Bad(409)
        name = body["name"]
        taken = {p["name"].lower() for p in live if p is not existing}
        n = 1
        while name.lower() in taken:
            n += 1
            name = "%s-%d" % (body["name"], n)
        if existing is None:
            existing = {"peer_id": "pe_" + secrets.token_urlsafe(16),
                        "instance_key": body["instanceKey"], "token_hash": token_hash}
            self.peers[existing["peer_id"]] = existing
        existing.update(name=name, host=body["host"], gone=False,
                        last_seen=time.monotonic())
        return {"peerId": existing["peer_id"], "name": name, "pollMs": POLL_MS,
                "staleMs": int(self.stale_s * 1000)}

    def _r_poll(self, body, token_hash):
        self._need(body, {"peerId": str, "acks?": list})
        peer = self._peer_for(body, token_hash)
        acks = body.get("acks", [])
        if len(acks) > 50:
            raise Bad(400, "acks")
        for ack in acks:
            if not (isinstance(ack, dict) and set(ack) == {"msgId", "state"}
                    and isinstance(ack["msgId"], str) and ID_RE.match(ack["msgId"])
                    and ack["state"] in ACK_STATES):
                raise Bad(400, "ack")
        if peer["gone"] or not self._live(peer):
            peer["gone"] = False
            taken = {p["name"].lower() for p in self.peers.values()
                     if p is not peer and self._live(p)}
            name, n = peer["name"], 1
            while name.lower() in taken:
                n += 1
                name = "%s-%d" % (peer["name"], n)
            peer["name"] = name
        peer["last_seen"] = time.monotonic()
        for ack in acks:
            self._apply_ack(peer, ack["msgId"], ack["state"])
        out, total = [], 0
        for msg_id in self.order:
            msg = self.messages[msg_id]
            if msg["to_peer"] != peer["peer_id"] or self._state_of(msg) != "queued":
                continue
            size = len(msg["text"].encode("utf-8"))
            if out and (len(out) >= POLL_BATCH or total + size > POLL_TEXT_BYTES):
                break
            total += size
            out.append({"msgId": msg_id, "seq": msg["seq"],
                        "from": {"name": msg["from_name"], "host": msg["from_host"]},
                        "text": msg["text"], "replyTo": msg["reply_to"],
                        "sentAt": int(msg["created"] * 1000)})
        return {"messages": out, "seq": self.seq, "name": peer["name"]}

    def _r_list(self, body, token_hash):
        self._need(body, {"peerId": str})
        peer = self._peer_for(body, token_hash)
        peer["last_seen"] = time.monotonic()
        return {"peers": [{"name": p["name"], "host": p["host"],
                           "lastSeenAt": int(time.time() * 1000),
                           "self": p is peer}
                          for p in self.peers.values() if self._live(p)]}

    def _r_send(self, body, token_hash):
        self._need(body, {"peerId": str, "to?": str, "replyTo?": str, "text": str})
        if ("to" in body) == ("replyTo" in body):
            raise Bad(400, "exactly one of to / replyTo")
        peer = self._peer_for(body, token_hash)
        reply_to = body.get("replyTo")
        if reply_to is not None:
            # routed by identity: the replied message's sender, never a name
            ref = self.messages.get(reply_to)
            if ref is None or ref["to_peer"] != peer["peer_id"]:
                raise Bad(400, "replyTo")
            target = self.peers.get(ref["from_peer"])
            if target is None or not self._live(target):
                raise Bad(404)
        else:
            target = self._live_by_name(body["to"])
            if target is None:
                raise Bad(404)
            if target is peer:
                raise Bad(400, "self")
        if hub_text_problem(body["text"]) is not None:
            raise Bad(400, "text")
        if len(self.messages) >= MAX_MESSAGES or sum(
                1 for m in self.messages.values()
                if m["to_peer"] == target["peer_id"] and m["state"] == "queued"
        ) >= MAX_QUEUED_PER_PEER:
            raise Bad(409)
        msg_id = self._new_message(peer["peer_id"], target["peer_id"], peer["name"],
                                   peer["host"],
                                   unicodedata.normalize("NFC", body["text"]), reply_to)
        return {"msgId": msg_id, "to": {"name": target["name"], "host": target["host"]}}

    def _r_status(self, body, token_hash):
        self._need(body, {"peerId": str, "msgIds": list})
        peer = self._peer_for(body, token_hash)
        ids = body["msgIds"]
        if not 1 <= len(ids) <= 50 or not all(
                isinstance(i, str) and ID_RE.match(i) for i in ids):
            raise Bad(400, "msgIds")
        out = []
        for msg_id in ids:
            msg = self.messages.get(msg_id)
            if msg is None or msg["from_peer"] != peer["peer_id"]:
                out.append({"msgId": msg_id, "state": "unknown", "updatedAt": 0})
            else:
                out.append({"msgId": msg_id, "state": self._state_of(msg),
                            "updatedAt": int(msg["updated"] * 1000)})
        return {"messages": out}

    def _r_bye(self, body, token_hash):
        self._need(body, {"peerId": str, "undelivered?": list})
        peer = self._peer_for(body, token_hash)
        ids = body.get("undelivered", [])
        if len(ids) > 200 or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
            raise Bad(400, "undelivered")
        peer["gone"] = True
        for msg_id in ids:
            msg = self.messages.get(msg_id)
            if msg and msg["to_peer"] == peer["peer_id"] and self._state_of(msg) in (
                    "queued", "fetched"):
                msg["state"] = "undelivered"
                msg["updated"] = time.time()
        return {"gone": True}


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
            if not self.path.startswith("/peers/") or route not in ROUTES:
                self._reply(404, {"error": "no route"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            limit = MAX_BODY.get(route, DEFAULT_MAX_BODY)
            raw = self.rfile.read(min(length, limit + 1)) if length else b""
            auth = self.headers.get("Authorization", "")
            record = {"route": route, "status": None, "body": None, "t": time.monotonic(),
                      "auth": bool(auth)}
            with hub.lock:
                hub.requests.append(record)
                hub.seen_tokens.add(auth)
            fault = self._next_fault(route)
            if fault is not None:
                if self._fault(route, fault, record):
                    return
            status, payload, headers = 200, None, ()
            try:
                token = auth[7:] if auth.startswith("Bearer ") else None
                with hub.lock:
                    ok = token is not None and token in hub.tokens
                if not ok:
                    raise Bad(401)
                if length > limit or len(raw) > limit:
                    raise Bad(400, "size")
                try:
                    body = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dups,
                                      parse_constant=_no_const)
                except (ValueError, UnicodeDecodeError):
                    raise Bad(400, "json")
                if not isinstance(body, dict):
                    raise Bad(400, "shape")
                record["body"] = body
                with hub.lock:
                    payload = hub.handle(route, body,
                                         hashlib.sha256(token.encode()).hexdigest())
            except Bad as exc:
                status, payload = exc.status, {"error": "refused"}
            record["status"] = status
            if route == "poll" and status == 200:
                record["delivered"] = [m["msgId"] for m in payload["messages"]]
            self._reply(status, payload, headers=headers)

        def _fault(self, route, fault, record):
            kind = fault["kind"]
            if kind == "status":
                headers = ()
                if fault.get("retry_after") is not None:
                    headers = (("Retry-After", str(fault["retry_after"])),)
                record["status"] = fault.get("status", 503)
                self._reply(record["status"], {"error": "fault"}, headers=headers)
                return True
            if kind == "unknown_peer":
                record["status"] = 404
                self._reply(404, {"error": "fault"})
                return True
            if kind == "redirect":
                record["status"] = 302
                target = fault.get("target", hub.url + "/elsewhere")
                self._reply(302, {"error": "moved"}, headers=(("Location", target),))
                return True
            if kind == "drop":
                record["status"] = "dropped"
                self.close_connection = True
                try:
                    self.request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return True
            if kind == "raw":
                record["status"] = fault.get("status", 200)
                self._reply(record["status"], raw=fault["body"])
                return True
            if kind == "slow":
                time.sleep(fault.get("seconds", 1.0))
                return False
            return False

    return Handler
