#!/usr/bin/env python3
"""Suite for mcp-messagebox (Scripts/mcp-messagebox.py): the peer-messaging
client behind one messagebox_call tool, run against tests/_stub_messagebox_hub.py
(an in-memory stand-in for the hub's /peers/* routes, started on loopback).

The case COUNT is deliberately absent from this docstring: it is written down
once, in the SUITES table in tests/run.py, which checks it against the run.

RED first: this suite was written and run before the server was in place.
Every row that needs the server fails for ONE stated reason -- the server file
does not exist -- raised as `_Red` from `_require_server()` inside `_guard`, so
the run reports every row instead of stopping at the first. The rows of group S
test the stub hub itself (the double the other groups lean on) and therefore
pass without the server; they are the controls that a stub which refused or
accepted everything could not satisfy.

Every scenario builds its own world under `<repo>/.claude/tmp/messagebox-<rand>/`:
a sessions dir holding a fake `<pid>.json` for THIS process (the server is a
child of the test runner, so its parent is the fake Claude Code), a 0700 spool
root, a 0600 token file and a stub hub on 127.0.0.1. The server is launched as a
real stdio subprocess with a clean MESSAGEBOX_* environment, polling every
100 ms through MESSAGEBOX_POLL_MS_FOR_TEST (honoured only because
MESSAGEBOX_SESSIONS_DIR is also set -- a row pins that rule). A second Claude
Code is a `sleep` child with its own sessions file, reached through
MESSAGEBOX_WALK_PID. Nothing outside `.claude/tmp` is written and nothing but
loopback is dialled.

Groups:
  A  protocol and envelope -- a plain launch with no environment, the one
     tool, status vs flagged errors, near-miss pointers, parameter refusals
  B  the text rule -- every refused class, the allowed set, CRLF, NFC, limits
  C  the token file -- every rule in-process, and fail closed through info
  D  owner detection -- cli, sdk-cli, FORCE, DISABLE, no/invalid/ambiguous
     sessions file, the parent-chain walk, the hub URL and the names
  E  register, poll, spool and acks -- the whole sequence, duplicates, the
     404 re-register, send/list/reply/ack/status, name uniqueness
  F  secrets and redirects -- no redirect followed, 401 stops, the token and
     message text never in any output
  G  backoff and Retry-After
  H  hostile hub payloads -- rejected without a spool write
  I  spool hygiene -- directory checks, the second server, planted files
  J  shutdown and sweep -- bye with the remaining ids, SIGTERM, dead siblings
  K  handler crash -- layer D and layer W, in-process
  L  concurrency -- a slow hub call does not block the read loop; a cancel
  S  the stub hub itself
"""

import hashlib
import http.client
import json
import logging
import os
import secrets
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _harness as H  # noqa: E402
import _stub_messagebox_hub as SH  # noqa: E402

NAME = "messagebox"
# PH_TEST_MESSAGEBOX_SERVER points the suite at a mutated copy of the server
# (the mutation runs of the change that added it); unset, it is the real one.
SERVER = os.environ.get("PH_TEST_MESSAGEBOX_SERVER") or H.repo_path("Scripts", "mcp-messagebox.py")
TOOL = "messagebox_call"
LOGGER = "mcp-messagebox"

GRP_A = "A. protocol and envelope"
GRP_B = "B. the text rule"
GRP_C = "C. the token file"
GRP_D = "D. owner detection"
GRP_E = "E. register, poll, spool, acks"
GRP_F = "F. secrets and redirects"
GRP_G = "G. backoff and Retry-After"
GRP_H = "H. hostile hub payloads"
GRP_I = "I. spool hygiene"
GRP_J = "J. shutdown and sweep"
GRP_K = "K. handler crash"
GRP_L = "L. concurrency"
GRP_S = "S. the stub hub"

RPC_TIMEOUT = 20.0
WAIT = 10.0
START_MS = 1700000000123
START_S = START_MS // 1000
CANARY = "CANARY-7f3a91c2"


class _Red(Exception):
    """The one stated RED reason: the server file does not exist yet."""


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

def _rec(suite, group, name, problems, extra=()):
    problems = list(problems)
    status = H.FAIL if problems else H.PASS
    brief = "%s | %s%s" % (status, name, (" | " + "; ".join(problems)) if problems else "")
    suite.record(group, name, problems, status=status, detail=list(extra),
                 brief=brief, text="; ".join(problems))


def _guard(suite, group, name, fn):
    """Record `fn()`'s (problems, extra); an exception is a FAIL, never a crash."""
    try:
        problems, extra = fn()
    except _Red as exc:
        problems, extra = ["RED: %s" % exc], ()
    except Exception as exc:              # noqa: BLE001 -- reported, not hidden
        problems, extra = ["raised %s: %s" % (type(exc).__name__, exc)], ()
    _rec(suite, group, name, problems, extra)


def _require_server():
    if not os.path.isfile(SERVER):
        raise _Red("Scripts/mcp-messagebox.py does not exist")


_MODULE = {}


def _mod():
    """The server loaded in-process, once, bytecode-free."""
    if "m" not in _MODULE:
        _require_server()
        _MODULE["m"] = H.load_module_from_path("messagebox_suite", SERVER)
    return _MODULE["m"]


def wait_for(cond, timeout=WAIT, step=0.03):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(step)
    return cond()


# ---------------------------------------------------------------------------
# the stdio client
# ---------------------------------------------------------------------------

class MbClient:
    """One server subprocess over stdio. stderr is drained on a thread, every
    stdout line is kept."""

    def __init__(self, env, args=(), argv=None, cwd=None):
        _require_server()
        cmd = argv if argv is not None else [sys.executable, "-I", "-B", SERVER]
        cmd = list(cmd) + list(args)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, cwd=cwd,
                                     text=True, encoding="utf-8", bufsize=1)
        self.err = []
        self.out = []
        self._id = 0
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()
        self.init = self.rpc("initialize", {"protocolVersion": "2024-11-05",
                                            "capabilities": {},
                                            "clientInfo": {"name": "ph-tests", "version": "1"}})

    def _drain(self):
        for line in self.proc.stderr:
            self.err.append(line)

    def stderr_text(self):
        return "".join(self.err)

    def stdout_text(self):
        return "\n".join(self.out)

    def write(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout=RPC_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                rest = self.proc.stdout.readline()
                if rest.strip():
                    self.out.append(rest.strip())
                    return json.loads(rest)
                return None
            ready, _, _ = select.select([self.proc.stdout], [], [], 0.1)
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    return None
                if line.strip():
                    self.out.append(line.strip())
                    return json.loads(line)
        return None

    def rpc(self, method, params=None, timeout=RPC_TIMEOUT):
        self._id += 1
        rid = self._id
        self.write({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params if params is not None else {}})
        while True:
            resp = self.read(timeout)
            if resp is None:
                raise AssertionError("no answer to %s (stderr: %r)"
                                     % (method, self.stderr_text()[-400:]))
            if resp.get("id") == rid:
                return resp

    def call(self, function=None, params=None, timeout=RPC_TIMEOUT):
        """(is_error, text, structured) of one tools/call."""
        args = {}
        if function is not None:
            args["function"] = function
        if params is not None:
            args["params"] = params
        resp = self.rpc("tools/call", {"name": TOOL, "arguments": args}, timeout)
        result = resp.get("result") or {}
        content = result.get("content") or [{}]
        return (result.get("isError"), content[0].get("text", ""),
                result.get("structuredContent"))

    def info(self):
        is_error, text, data = self.call("info")
        if is_error or not isinstance(data, dict):
            raise AssertionError("info failed: %r" % (text,))
        return data

    def close(self, wait=8):
        """Close stdin and wait; the exit code, or None when it did not exit."""
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            return self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None
        finally:
            self._thread.join(2)

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def _err_code(structured):
    return ((structured or {}).get("error") or {}).get("code")


# ---------------------------------------------------------------------------
# worlds
# ---------------------------------------------------------------------------

def _write(path, text, mode=0o600):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def make_session(sessions, pid, entrypoint="cli", cwd="/work/proj", started_ms=START_MS,
                 raw=None):
    body = raw if raw is not None else json.dumps({
        "pid": pid, "sessionId": "00000000-0000-0000-0000-000000000000", "cwd": cwd,
        "startedAt": started_ms, "kind": "interactive", "entrypoint": entrypoint})
    return _write(os.path.join(sessions, "%d.json" % pid), body)


class Fixtures:
    def __init__(self, keep=False):
        self.keep = keep
        scratch = H.repo_path(".claude", "tmp")
        os.makedirs(scratch, exist_ok=True)
        self.base = tempfile.mkdtemp(prefix="messagebox-", dir=scratch)
        self.worlds = []

    def cleanup(self):
        for world in self.worlds:
            world.cleanup()
        if self.keep:
            print("\n[--keep] fixtures retained at: %s" % self.base)
            return
        shutil.rmtree(self.base, ignore_errors=True)


class World:
    """One scenario: sessions dir, spool root, token file, stub hub."""

    def __init__(self, fx, label, entrypoint="cli", cwd="/work/proj", session=True,
                 poll_ms=100):
        _require_server()
        fx.worlds.append(self)
        self.dir = tempfile.mkdtemp(prefix=label + "-", dir=fx.base)
        self.sessions = os.path.join(self.dir, "sessions")
        self.root = os.path.join(self.dir, "root")
        os.mkdir(self.sessions)
        os.mkdir(self.root, 0o700)
        os.chmod(self.root, 0o700)
        self.hub = SH.StubHub().start()
        self.token = self.hub.token
        self.token_path = _write(os.path.join(self.dir, "token"), self.token + "\n")
        self.pid = os.getpid()
        self.poll_ms = poll_ms
        self.clients = []
        self.procs = []
        if session:
            make_session(self.sessions, self.pid, entrypoint, cwd)

    def spool(self, pid=None, start_s=START_S):
        return os.path.join(self.root, "%d-%d" % (pid or self.pid, start_s))

    def env(self, **over):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MESSAGEBOX_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(MESSAGEBOX_HUB=self.hub.url, MESSAGEBOX_TOKEN_FILE=self.token_path,
                   MESSAGEBOX_SESSIONS_DIR=self.sessions, MESSAGEBOX_ROOT=self.root,
                   MESSAGEBOX_POLL_MS_FOR_TEST=str(self.poll_ms))
        env.update(over)
        return {k: v for k, v in env.items() if v is not None}

    def server(self, args=(), argv=None, **over):
        client = MbClient(self.env(**over), args=args, argv=argv, cwd=H.REPO_ROOT)
        self.clients.append(client)
        return client

    def helper_cc(self, cwd="/work/second", entrypoint="cli", started_ms=START_MS + 500000):
        """A live `sleep` standing in for a second Claude Code."""
        proc = subprocess.Popen(["sleep", "600"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        make_session(self.sessions, proc.pid, entrypoint, cwd, started_ms)
        return proc.pid, started_ms // 1000

    def other(self, name="other", host="elsewhere"):
        return Peer(self.hub, name, host)

    def cleanup(self):
        for client in self.clients:
            client.kill()
        for proc in self.procs:
            proc.kill()
            proc.wait()
        self.hub.stop()


def api(hub, route, body=None, token="default", raw=None, headers=None):
    """(status, parsed-or-None, response headers) of one POST to the stub."""
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    tok = hub.token if token == "default" else token
    if tok is not None:
        hdrs["Authorization"] = "Bearer " + tok
    hdrs.update(headers or {})
    try:
        conn.request("POST", "/peers/" + route, data, hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = None
        return resp.status, parsed, dict(resp.getheaders())
    finally:
        conn.close()


class Peer:
    """A hand-driven peer on the stub (the 'other session')."""

    def __init__(self, hub, name="other", host="elsewhere", token="default"):
        self.hub, self.token = hub, token
        self.key = secrets.token_urlsafe(16)
        self.status, ans, _ = api(hub, "register", {"instanceKey": self.key, "name": name,
                                                    "host": host}, token)
        self.peer_id = (ans or {}).get("peerId")
        self.name = (ans or {}).get("name")

    def poll(self, acks=None):
        body = {"peerId": self.peer_id}
        if acks:
            body["acks"] = acks
        return api(self.hub, "poll", body, self.token)[1]

    def send(self, to, text, reply_to=None):
        """A message to `to`; with `reply_to` a reply routed by identity (no `to`)."""
        body = {"peerId": self.peer_id, "text": text}
        if reply_to:
            body["replyTo"] = reply_to
        else:
            body["to"] = to
        return api(self.hub, "send", body, self.token)


def spool_files(world, suffix=None, pid=None, start_s=START_S):
    path = world.spool(pid, start_s)
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    return [n for n in names if suffix is None or n.endswith(suffix)]


def only_json(world, **kw):
    return [n for n in spool_files(world, ".json", **kw) if n.startswith("ms_")]


def spool_json_path(world, msg_id, suffix=".json"):
    return os.path.join(world.spool(), msg_id + suffix)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def registered(world, client=None, n=1):
    return wait_for(lambda: world.hub.count("register") >= n
                    and (client is None or client.info()["hub"]["registered"]))


def inbound(msg_id, seq=1, name="other", host="elsewhere", text="hi", reply_to=None,
            sent=1700000000000):
    return {"msgId": msg_id, "seq": seq, "from": {"name": name, "host": host},
            "text": text, "replyTo": reply_to, "sentAt": sent}


MSG_A = "ms_" + "A" * 22
MSG_B = "ms_" + "B" * 22


def poll_answer(*messages, **extra):
    return json.dumps(dict({"messages": list(messages), "seq": 1, "name": "proj"},
                           **extra)).encode("utf-8")


# ---------------------------------------------------------------------------
# A. protocol and envelope
# ---------------------------------------------------------------------------

def _bare_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MESSAGEBOX_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_a(suite, fx):
    holder = {}

    def bare():
        if "c" not in holder:
            holder["c"] = MbClient(_bare_env(), cwd=H.REPO_ROOT)
        return holder["c"]

    def plain_launch():
        c = bare()
        problems = []
        res = c.init.get("result") or {}
        if res.get("protocolVersion") != "2024-11-05":
            problems.append("protocolVersion %r" % res.get("protocolVersion"))
        if (res.get("serverInfo") or {}).get("version") != "1.0.0":
            problems.append("serverInfo.version %r" % (res.get("serverInfo"),))
        if c.rpc("ping").get("result") != {}:
            problems.append("ping did not answer {}")
        if c.rpc("foo/bar").get("error", {}).get("code") != -32601:
            problems.append("unknown method was not -32601")
        return problems, ["stderr: %r" % c.stderr_text()[-200:]]
    _guard(suite, GRP_A, "A1 plain launch, no arguments, no environment", plain_launch)

    def one_tool():
        tools = bare().rpc("tools/list").get("result", {}).get("tools", [])
        problems = []
        if [t.get("name") for t in tools] != [TOOL]:
            problems.append("tools: %r" % [t.get("name") for t in tools])
        else:
            enum = tools[0]["inputSchema"]["properties"]["function"].get("enum")
            if enum != ["info", "list", "send", "reply", "ack", "status"]:
                problems.append("function enum %r" % (enum,))
        return problems, ()
    _guard(suite, GRP_A, "A2 tools/list is exactly messagebox_call", one_tool)

    def omitted():
        is_error, text, _s = bare().call()
        problems = []
        if is_error:
            problems.append("omitted function was flagged isError")
        if "Inactive" not in text or "MESSAGEBOX_HUB" not in text:
            problems.append("status text lacks the inactive reason: %r" % text)
        return problems, ()
    _guard(suite, GRP_A, "A3 omitted function is an unflagged status", omitted)

    def info_inactive():
        is_error, text, data = bare().call("info")
        problems = []
        if is_error:
            problems.append("info was flagged isError")
        if data.get("active") is not False or "MESSAGEBOX_HUB" not in (data.get("inactiveReason") or ""):
            problems.append("info: %r" % (data,))
        return problems, ()
    _guard(suite, GRP_A, "A4 info says why the server is inactive", info_inactive)

    def tools_refuse():
        problems = []
        for fn, params in (("list", {}), ("send", {"to": "x", "text": "y"}),
                           ("ack", {"msg_id": MSG_A}), ("status", {"msg_ids": [MSG_A]})):
            is_error, _t, data = bare().call(fn, params)
            if is_error is not True or _err_code(data) != "inactive":
                problems.append("%s: isError=%r code=%r" % (fn, is_error, _err_code(data)))
        return problems, ()
    _guard(suite, GRP_A, "A5 every other function answers a clear isError when inactive", tools_refuse)

    def near_miss():
        c = bare()
        problems = []
        is_error, text, _s = c.call("sned", {})
        if is_error is not True or "Did you mean 'send'?" not in text:
            problems.append("near-miss function: %r %r" % (is_error, text[-120:]))
        is_error, text, _s = c.call("qxqxqxqx", {})
        if is_error is not True or "Did you mean" in text:
            problems.append("far-miss function: %r %r" % (is_error, text[-120:]))
        return problems, ()
    _guard(suite, GRP_A, "A6 unknown function: flagged, near miss pointed at, far miss not", near_miss)

    def unknown_param():
        c = bare()
        problems = []
        is_error, text, _s = c.call("send", {"to": "x", "txt": "y"})
        if (is_error is not True or "Unknown params for 'send': txt." not in text
                or "Did you mean 'text'?" not in text):
            problems.append("unknown param: %r %r" % (is_error, text[-160:]))
        is_error, text, _s = c.call("info", {"zzz": "x"})
        if is_error is not True or "Unknown params for 'info'" not in text:
            problems.append("info with a param: %r %r" % (is_error, text[-120:]))
        return problems, ()
    _guard(suite, GRP_A, "A7 unknown params are refused with the generated pointer", unknown_param)

    def missing_param():
        c = bare()
        problems = []
        for fn, params, want in (("send", {"to": "x"}, "text is required"),
                                 ("reply", {"text": "x"}, "msg_id is required"),
                                 ("status", {}, "msg_ids is required"),
                                 ("send", {"to": 3, "text": "x"}, "to must be a string"),
                                 ("status", {"msg_ids": "x"}, "msg_ids must be a list")):
            is_error, text, _s = c.call(fn, params)
            if is_error is not True or want not in text:
                problems.append("%s %r: %r %r" % (fn, params, is_error, text[-100:]))
        return problems, ()
    _guard(suite, GRP_A, "A8 required and typed parameters", missing_param)

    def schema_fill():
        c = bare()
        filled = {"to": "", "text": "", "msg_id": "", "msg_ids": []}
        is_error, text, data = c.call("list", filled)
        problems = []
        if "Unknown params" in text or _err_code(data) != "inactive":
            problems.append("empty schema keys were not dropped: %r" % text[-120:])
        is_error, text, data = c.call("send", dict(filled, to="x"))
        if "text is required" not in text:
            problems.append("empty text was not 'omitted': %r" % text[-120:])
        return problems, ()
    _guard(suite, GRP_A, "A9 a key holding its type's empty value counts as omitted", schema_fill)

    def forced():
        c = bare()
        c._id += 1
        c.write({"jsonrpc": "2.0", "id": c._id, "method": "tools/call", "params": "force-error"})
        resp = c.read()
        code = (resp or {}).get("error", {}).get("code")
        return ([] if code == -32602 else ["code %r" % code]), ()
    _guard(suite, GRP_A, "A10 non-object params are -32602, and the server answers on", forced)

    def unknown_tool():
        resp = bare().rpc("tools/call", {"name": "nope", "arguments": {}})
        code = resp.get("error", {}).get("code")
        return ([] if code == -32602 else ["code %r" % code]), ()
    _guard(suite, GRP_A, "A11 an unknown tool name is -32602", unknown_tool)

    def teardown():
        c = holder.pop("c", None)
        rc = c.close() if c else 0
        return ([] if rc == 0 else ["exit code %r" % rc]), ()
    _guard(suite, GRP_A, "A12 stdin EOF exits 0", teardown)


# ---------------------------------------------------------------------------
# B. the text rule
# ---------------------------------------------------------------------------

REFUSED_CLASSES = [
    ("NUL", "a\x00b", "control character"),
    ("ESC", "a\x1bb", "control character"),
    ("DEL", "a\x7fb", "control character"),
    ("C1 NEL", "a\x85b", "control character"),
    ("lone CR", "a\rb", "control character"),
    ("bidi RLO U+202E", "a‮b", "bidi control"),
    ("bidi isolate U+2066", "a⁦b", "bidi control"),
    ("LRM U+200E", "a‎b", "bidi control"),
    ("ALM U+061C", "a؜b", "bidi control"),
    ("zero-width space U+200B", "a​b", "format character"),
    ("zero-width joiner U+200D", "a‍b", "format character"),
    ("BOM U+FEFF", "a﻿b", "format character"),
    ("soft hyphen U+00AD", "a­b", "format character"),
    ("Arabic number sign U+0600 (Cf)", "a؀b", "format character"),
    ("line separator U+2028", "a b", "line or paragraph separator"),
    ("paragraph separator U+2029", "a b", "line or paragraph separator"),
    ("variation selector U+FE0F", "a️b", "variation selector"),
    ("variation selector supplement U+E0100", "a\U000e0100b", "variation selector"),
    ("Mongolian FVS U+180B", "a᠋b", "variation selector"),
    ("TAG letter U+E0041", "a\U000e0041b", "TAG block character"),
    ("private use U+E000", "ab", "private use character"),
    ("private use plane 15 U+F0000", "a\U000f0000b", "private use character"),
    ("lone surrogate U+D800", "a\ud800b", "lone surrogate"),
    ("lone low surrogate U+DFFF", "a\udfffb", "lone surrogate"),
    ("combining grapheme joiner U+034F", "a͏b", "default-ignorable character"),
    ("Hangul filler U+3164", "aㅤb", "default-ignorable character"),
    ("Hangul choseong filler U+115F", "aᅟb", "default-ignorable character"),
    ("halfwidth Hangul filler U+FFA0", "aﾠb", "default-ignorable character"),
    ("noncharacter U+FDD0", "a﷐b", "noncharacter"),
    ("noncharacter U+FFFF", "a￿b", "noncharacter"),
    ("noncharacter U+1FFFE", "a\U0001fffeb", "noncharacter"),
    ("system-reminder tag", "x <system-reminder> y", "system-reminder tag"),
    ("closing system-reminder tag, upper case", "x </SYSTEM-REMINDER> y", "system-reminder tag"),
    ("mixed case system-reminder open", "x <System-Reminder foo", "system-reminder tag"),
]

ALLOWED_TEXTS = [
    ("plain", "hello world"),
    ("newline and tab", "line one\n\tline two\n"),
    ("accents and CJK", "café über 日本語"),
    ("astral emoji", "ok \U0001f600 done"),
    ("braille blank U+2800 (So, not ignorable)", "a⠀b"),
    ("no-break space and em space", "a b c"),
    ("a lookalike tag without the marker", "<system reminder> and <reminder>"),
]


def _run_b(suite, fx):
    for label, text, klass in REFUSED_CLASSES:
        def refused(text=text, klass=klass):
            mod = _mod()
            why = mod.unsafe_text_reason(text)
            problems = []
            if why is None:
                problems.append("accepted")
            elif klass not in why:
                problems.append("reason %r does not name %r" % (why, klass))
            elif text[1] != " " and text[1] in why:
                problems.append("reason echoes the offending character: %r" % why)
            if mod.prepare_text(text)[0] is not None:
                problems.append("prepare_text accepted it")
            return problems, ["reason: %r" % why]
        _guard(suite, GRP_B, "B1 refused: " + label, refused)

    def allowed():
        mod = _mod()
        problems = []
        for label, text in ALLOWED_TEXTS:
            why = mod.unsafe_text_reason(text)
            prepared, prep_why = mod.prepare_text(text)
            if why is not None or prepared is None:
                problems.append("%s refused: %r / %r" % (label, why, prep_why))
        return problems, ()
    _guard(suite, GRP_B, "B2 the allowed set: \\n and \\t, accents, CJK, emoji, a lookalike", allowed)

    def crlf_nfc():
        mod = _mod()
        problems = []
        out, why = mod.prepare_text("a\r\nb\r\nc")
        if out != "a\nb\nc":
            problems.append("CRLF -> %r (%r)" % (out, why))
        out, why = mod.prepare_text("café")
        if out != "café":
            problems.append("NFC -> %r (%r)" % (out, why))
        out, why = mod.prepare_text("a\rb")
        if out is not None:
            problems.append("a lone CR was kept: %r" % (out,))
        return problems, ()
    _guard(suite, GRP_B, "B3 CRLF becomes LF, text is NFC, a lone CR is refused", crlf_nfc)

    def limits():
        mod = _mod()
        problems = []
        checks = [("1 character", "x", True), ("empty", "", False),
                  ("8000 code points", "x" * 8000, True),
                  ("8001 code points", "x" * 8001, False),
                  ("16 KiB of UTF-8 exactly", "€" * 5461 + "x", True),
                  ("over 16 KiB of UTF-8 in under 8000 code points", "€" * 5462 + "xx", False),
                  ("a non-string", 7, False), ("a list", ["x"], False),
                  ("an absurd length", "x" * 300000, False)]
        for label, text, ok in checks:
            out, why = mod.prepare_text(text)
            if (out is not None) != ok:
                problems.append("%s: %s (%r)" % (label, "refused" if ok else "accepted", why))
        if len(("€" * 5461 + "x").encode("utf-8")) != 16384:
            problems.append("fixture arithmetic: not 16384 bytes")
        return problems, ()
    _guard(suite, GRP_B, "B4 length limits: 1-8000 code points and 16 KiB of UTF-8", limits)

    def via_tool():
        w = World(fx, "b5")
        c = w.server()
        w.other()
        registered(w, c)
        problems = []
        for label, text in (("bidi", "x‮y"), ("system-reminder", "<system-reminder>x"),
                            ("closing, upper case", "x</SYSTEM-REMINDER>")):
            is_error, _t, data = c.call("send", {"to": "other", "text": text})
            if is_error is not True or _err_code(data) != "invalid_params":
                problems.append("%s: %r %r" % (label, is_error, _err_code(data)))
        if w.hub.count("send"):
            problems.append("a refused text reached the hub (%d send request(s))" % w.hub.count("send"))
        is_error, _t, data = c.call("send", {"to": "other", "text": "a\r\nb é"})
        sent = w.hub.bodies("send")
        if is_error or not sent or sent[-1]["text"] != "a\nb é":
            problems.append("normalised text did not arrive: %r" % (sent[-1:] ,))
        return problems, ()
    _guard(suite, GRP_B, "B5 send refuses locally before the network, and normalises", via_tool)

    def parser_agreement():
        # The spool parser is its own block (a block cannot call another source's
        # _strict_loads); the two must agree, as the two search sanitizers do.
        mod = _mod()
        problems = []
        valid = ['{"a":1}', '[1,2.5,"x",null,true]', ' {"a": {"b": []}} ', '"s"', '123', "1" * 4300,
                 '{"k": "caf\\u00e9"}']
        invalid = ["NaN", '{"a": Infinity}', "[-Infinity]", "[1e999]", "[-1e999]", "1" * 4301,
                   '{"a":1} x', "{", "", "[1,]", "{'a': 1}"]
        for text in valid:
            if mod._parse_strict(text.encode("utf-8")) != mod._strict_loads(text):
                problems.append("valid text read differently: %r" % text[:30])
        for text in invalid:
            for name, fn in (("_parse_strict", lambda t: mod._parse_strict(t.encode("utf-8"))),
                             ("_strict_loads", mod._strict_loads)):
                try:
                    fn(text)
                    problems.append("%s accepted %r" % (name, text[:30]))
                except ValueError:
                    pass
        try:
            mod._parse_strict(b'{"a": 1, "a": 2}')
            problems.append("a duplicate key was accepted")
        except ValueError:
            pass
        try:
            mod._parse_strict(b"\xff\xfe")
            problems.append("invalid UTF-8 was accepted")
        except ValueError:
            pass
        try:
            mod._parse_strict(b"[" * 100000)
            problems.append("deep nesting was accepted")
        except ValueError:
            pass
        return problems, ()
    _guard(suite, GRP_B, "B6 the spool's strict parser agrees with the generated _strict_loads, and also refuses a duplicate key", parser_agreement)


# ---------------------------------------------------------------------------
# C. the token file
# ---------------------------------------------------------------------------

def _run_c(suite, fx):
    good = "aisc1_" + "Ab0_-" * 8

    def tokfile(name, content, mode=0o600):
        path = os.path.join(fx.base, "tok-" + name)
        if os.path.lexists(path):
            os.unlink(path)
        with open(path, "wb") as fh:
            fh.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        os.chmod(path, mode)
        return path

    def refused(mod, path, want):
        try:
            mod._read_token_file(path)
        except mod.TokenRefused as exc:
            return [] if want in exc.rule else ["rule %r does not say %r" % (exc.rule, want)]
        return ["accepted"]

    def ok_file():
        mod = _mod()
        return ([] if mod._read_token_file(tokfile("ok", good + "\n")) == good else ["wrong token"]), ()
    _guard(suite, GRP_C, "C1 a 0600 regular file with one aisc1_ line is read (newline stripped)", ok_file)

    def no_newline():
        mod = _mod()
        return ([] if mod._read_token_file(tokfile("nonl", good)) == good else ["wrong token"]), ()
    _guard(suite, GRP_C, "C2 the trailing newline is optional", no_newline)

    def symlink():
        mod = _mod()
        real = tokfile("real", good + "\n")
        link = os.path.join(fx.base, "tok-link")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(real, link)
        return refused(mod, link, "symlink"), ()
    _guard(suite, GRP_C, "C3 a symlink is refused (O_NOFOLLOW)", symlink)

    def modes():
        mod = _mod()
        problems = []
        for mode in (0o640, 0o604, 0o644, 0o400, 0o660, 0o700):
            problems += ["%o: %s" % (mode, p) for p in
                         refused(mod, tokfile("m%o" % mode, good + "\n", mode), "0600")]
        return problems, ()
    _guard(suite, GRP_C, "C4 any mode but exactly 0600 is refused (group, other, 0400, 0700)", modes)

    def hardlink():
        mod = _mod()
        real = tokfile("hl", good + "\n")
        link = os.path.join(fx.base, "tok-hl2")
        if os.path.lexists(link):
            os.unlink(link)
        os.link(real, link)
        return refused(mod, real, "hardlinked"), ()
    _guard(suite, GRP_C, "C5 a hardlinked file (nlink > 1) is refused", hardlink)

    def multiline():
        mod = _mod()
        problems = []
        for label, body in (("two lines", good + "\n" + good + "\n"), ("CRLF", good + "\r\n"),
                            ("leading blank", "\n" + good), ("embedded space", good + " x\n"),
                            ("empty", ""), ("only newline", "\n"), ("non-ASCII", good + "é\n")):
            problems += ["%s: %s" % (label, p) for p in
                         refused(mod, tokfile("ml", body), "printable ASCII line")]
        return problems, ()
    _guard(suite, GRP_C, "C6 anything but one printable-ASCII line is refused", multiline)

    def prefix():
        mod = _mod()
        problems = []
        for label, body in (("wrong prefix", "aisc2_" + "x" * 30), ("no prefix", "x" * 40),
                            ("too short", "aisc1_abc")):
            problems += ["%s: %s" % (label, p) for p in
                         refused(mod, tokfile("pf", body + "\n"), "aisc1_")]
        return problems, ()
    _guard(suite, GRP_C, "C7 the token must start with aisc1_ and be long enough", prefix)

    def relative():
        mod = _mod()
        return refused(mod, "relative/token", "absolute"), ()
    _guard(suite, GRP_C, "C8 a relative path is refused", relative)

    def special():
        mod = _mod()
        problems = []
        fifo = os.path.join(fx.base, "tok-fifo")
        if os.path.lexists(fifo):
            os.unlink(fifo)
        os.mkfifo(fifo, 0o600)
        problems += ["fifo: " + p for p in refused(mod, fifo, "regular")]
        problems += ["directory: " + p for p in refused(mod, fx.base, "regular")]
        problems += ["missing: " + p for p in refused(mod, os.path.join(fx.base, "nope"), "missing")]
        problems += ["oversize: " + p for p in
                     refused(mod, tokfile("big", "aisc1_" + "x" * 5000 + "\n"), "larger")]
        return problems, ()
    _guard(suite, GRP_C, "C9 FIFO, directory, missing and oversize files are refused", special)

    def config_rules():
        mod = _mod()
        problems = []
        env = {"MESSAGEBOX_HUB": "http://127.0.0.1:1", "MESSAGEBOX_TOKEN_FILE": "rel/path"}
        cfg = mod.config_from_env(env)
        if "absolute" not in (cfg.token_problem or "") or cfg.token_file is not None:
            problems.append("relative token path in config: %r" % (cfg.token_problem,))
        cfg = mod.config_from_env({"MESSAGEBOX_HUB": "http://127.0.0.1:1"})
        if "not set" not in (cfg.token_problem or ""):
            problems.append("missing token path: %r" % (cfg.token_problem,))
        return problems, ()
    _guard(suite, GRP_C, "C10 the config refuses an unset or relative MESSAGEBOX_TOKEN_FILE", config_rules)

    def fail_closed():
        problems = []
        for label, make in (("symlink", lambda w: (os.unlink(w.token_path),
                                                   os.symlink(os.path.join(w.dir, "target"), w.token_path))),
                            ("mode 0644", lambda w: os.chmod(w.token_path, 0o644)),
                            ("not aisc1_", lambda w: _write(w.token_path, "nope" * 10 + "\n"))):
            w = World(fx, "c11")
            _write(os.path.join(w.dir, "target"), w.token + "\n")
            make(w)
            c = w.server()
            data = c.info()
            if data["active"] or "token file refused" not in (data["inactiveReason"] or ""):
                problems.append("%s: %r" % (label, data))
            if w.hub.count("register") or os.path.exists(w.spool()):
                problems.append("%s: registered or spooled" % label)
            if w.token in c.stdout_text() or w.token in c.stderr_text():
                problems.append("%s: token in output" % label)
        return problems, ()
    _guard(suite, GRP_C, "C11 a bad token file fails closed: info says so, no spool, no register", fail_closed)


# ---------------------------------------------------------------------------
# D. owner detection
# ---------------------------------------------------------------------------

def _run_d(suite, fx):
    def cli_ok():
        w = World(fx, "d1", cwd="/work/My Project!")
        c = w.server()
        problems = []
        if not registered(w, c):
            return ["did not register"], ["stderr: %r" % c.stderr_text()[-300:]]
        data = c.info()
        if not data["active"] or data["inactiveReason"] is not None:
            problems.append("info: %r" % (data,))
        body = w.hub.bodies("register")[0]
        if sorted(body) != ["host", "instanceKey", "name"]:
            problems.append("register keys %r" % sorted(body))
        if body["name"] != "My-Project-":
            problems.append("name %r" % body["name"])
        if not SH.NAME_RE.match(body["host"]) or not SH.KEY_RE.match(body["instanceKey"]):
            problems.append("host/instanceKey grammar: %r" % (body,))
        if len(body["instanceKey"]) != 22:
            problems.append("instanceKey length %d" % len(body["instanceKey"]))
        return problems, ()
    _guard(suite, GRP_D, "D1 entrypoint cli: registers; name is the sanitised cwd basename", cli_ok)

    def sdk_cli():
        w = World(fx, "d2", entrypoint="sdk-cli")
        c = w.server()
        data = c.info()
        problems = []
        if data["active"] or "not interactive" not in (data["inactiveReason"] or ""):
            problems.append("info: %r" % (data,))
        time.sleep(0.4)
        if w.hub.count("register") or os.path.exists(w.spool()):
            problems.append("registered or created a spool")
        is_error, _t, d = c.call("list")
        if is_error is not True or _err_code(d) != "inactive":
            problems.append("list: %r %r" % (is_error, _err_code(d)))
        return problems, ()
    _guard(suite, GRP_D, "D2 entrypoint sdk-cli is refused: no register, no spool", sdk_cli)

    def forced():
        w = World(fx, "d3", entrypoint="sdk-cli")
        c = w.server(MESSAGEBOX_FORCE="1")
        ok = registered(w, c)
        return ([] if ok else ["MESSAGEBOX_FORCE=1 did not register"]), ()
    _guard(suite, GRP_D, "D3 MESSAGEBOX_FORCE=1 registers a sdk-cli session", forced)

    def disable():
        w = World(fx, "d4")
        c = w.server(MESSAGEBOX_DISABLE="1", MESSAGEBOX_FORCE="1")
        data = c.info()
        time.sleep(0.4)
        problems = []
        if data["active"] or "MESSAGEBOX_DISABLE" not in (data["inactiveReason"] or ""):
            problems.append("info: %r" % (data,))
        if w.hub.count("register") or os.path.exists(w.spool()):
            problems.append("registered or spooled")
        return problems, ()
    _guard(suite, GRP_D, "D4 MESSAGEBOX_DISABLE=1 always wins (even over FORCE and cli)", disable)

    def no_file():
        w = World(fx, "d5", session=False)
        c = w.server()
        data = c.info()
        time.sleep(0.4)
        problems = []
        if data["active"] or "no Claude Code session" not in (data["inactiveReason"] or ""):
            problems.append("info: %r" % (data,))
        if w.hub.count("register") or os.path.exists(w.spool()) or os.listdir(w.root):
            problems.append("registered or spooled")
        return problems, ()
    _guard(suite, GRP_D, "D5 no sessions file anywhere on the chain: fail closed", no_file)

    def ambiguous():
        problems = []
        cases = [("wrong pid inside", lambda w, p: json.dumps({"pid": p + 1, "startedAt": START_MS,
                                                               "entrypoint": "cli", "cwd": "/x"})),
                 ("not JSON", lambda w, p: "{nope"),
                 ("duplicate keys", lambda w, p: '{"pid": %d, "pid": %d, "startedAt": 1, '
                                                 '"entrypoint": "cli"}' % (p, p)),
                 ("startedAt not an integer", lambda w, p: json.dumps({"pid": p, "startedAt": "1",
                                                                       "entrypoint": "cli"})),
                 ("startedAt a float", lambda w, p: json.dumps({"pid": p, "startedAt": 1.5,
                                                                "entrypoint": "cli"})),
                 ("pid a bool", lambda w, p: '{"pid": true, "startedAt": 1, "entrypoint": "cli"}'),
                 ("NaN", lambda w, p: '{"pid": %d, "startedAt": NaN, "entrypoint": "cli"}' % p),
                 ("a list", lambda w, p: "[1]")]
        for label, build in cases:
            w = World(fx, "d6", session=False)
            make_session(w.sessions, w.pid, raw=build(w, w.pid))
            c = w.server()
            data = c.info()
            time.sleep(0.2)
            if data["active"] or "sessions file" not in (data["inactiveReason"] or ""):
                problems.append("%s: %r" % (label, data))
            if w.hub.count("register") or os.path.exists(w.spool()):
                problems.append("%s: registered or spooled" % label)
        return problems, ()
    _guard(suite, GRP_D, "D6 a sessions file that is there but invalid ends the walk: fail closed", ambiguous)

    def hops():
        w = World(fx, "d7")
        argv = ["/bin/sh", "-c", '"$@"; exit $?', "sh", sys.executable, "-I", "-B", SERVER]
        c = w.server(argv=argv)
        ok = registered(w, c)
        problems = [] if ok else ["no register through an sh wrapper (two hops)"]
        # nobody above the chain: the same wrapper but the file only for a pid far away
        w2 = World(fx, "d7b", session=False)
        make_session(w2.sessions, 1, cwd="/x")      # pid 1 is never consulted
        c2 = w2.server(argv=argv)
        data = c2.info()
        if data["active"]:
            problems.append("pid 1 was consulted")
        return problems, ["stderr: %r" % c.stderr_text()[-200:]]
    _guard(suite, GRP_D, "D7 the walk climbs through a wrapper (two hops) and never consults pid 1", hops)

    def walk_hook():
        w = World(fx, "d8", session=False)
        pid, start_s = w.helper_cc(cwd="/work/second")
        c = w.server(MESSAGEBOX_WALK_PID=str(pid))
        ok = registered(w, c)
        problems = [] if ok else ["did not register through MESSAGEBOX_WALK_PID"]
        if not os.path.isdir(w.spool(pid, start_s)):
            problems.append("no spool at <pid>-<startedAt//1000>: %r" % os.listdir(w.root))
        return problems, ()
    _guard(suite, GRP_D, "D8 MESSAGEBOX_WALK_PID starts the walk elsewhere", walk_hook)

    def spool_identity():
        w = World(fx, "d9")
        c = w.server()
        registered(w, c)
        wait_for(lambda: os.path.exists(os.path.join(w.spool(), "owner.json")))
        problems = []
        if not os.path.isdir(w.spool()):
            return ["no spool at %s" % os.path.basename(w.spool())], ()
        owner = read_json(os.path.join(w.spool(), "owner.json"))
        if sorted(owner) != ["ccPid", "ccStart", "instanceKey", "peerId"]:
            problems.append("owner.json keys %r" % sorted(owner))
        if owner.get("ccPid") != w.pid or owner.get("ccStart") != START_S:
            problems.append("owner.json identity %r" % (owner,))
        if owner.get("instanceKey") != w.hub.bodies("register")[0]["instanceKey"]:
            problems.append("owner.json instanceKey differs from the registered one")
        data = c.info()
        blob = json.dumps(data)
        for secret in (owner.get("peerId", "?"), owner.get("instanceKey", "?"), "peerId", "instanceKey"):
            if secret in blob:
                problems.append("info shows %r" % secret)
        if "pid" in data or "ccPid" in data:
            problems.append("info shows a pid")
        return problems, ()
    _guard(suite, GRP_D, "D9 spool is <pid>-<startedAt//1000>; owner.json is exact; info hides ids", spool_identity)

    def hub_urls():
        mod = _mod()
        problems = []
        bad = ["ftp://h/", "http://user:pw@127.0.0.1:1/", "http://user@127.0.0.1/",
               "http://127.0.0.1:1/?q=1", "http://127.0.0.1:1/#f", "http://", "127.0.0.1:1",
               "http://127.0.0.1:99999/", "http://exa mple/", "http://h/\n", "", "file:///x"]
        for url in bad:
            cfg = mod.config_from_env({"MESSAGEBOX_HUB": url})
            if cfg.hub is not None or not cfg.hub_problem:
                problems.append("accepted %r" % url)
            elif "pw" in cfg.hub_problem or "user" in cfg.hub_problem.replace("userinfo", ""):
                problems.append("echoed the URL: %r" % cfg.hub_problem)
        for url, base in (("http://127.0.0.1:1", "http://127.0.0.1:1"),
                          ("https://hub.example/", "https://hub.example"),
                          ("http://h:8080/api/", "http://h:8080/api")):
            cfg = mod.config_from_env({"MESSAGEBOX_HUB": url})
            if cfg.hub != base:
                problems.append("%r -> %r" % (url, cfg.hub))
        return problems, ()
    _guard(suite, GRP_D, "D10 the hub URL: http/https only, no userinfo/query/fragment, never echoed", hub_urls)

    def poll_override_rule():
        mod = _mod()
        problems = []
        cfg = mod.config_from_env({"MESSAGEBOX_POLL_MS_FOR_TEST": "100"})
        if cfg.poll_ms is not None:
            problems.append("override honoured without MESSAGEBOX_SESSIONS_DIR")
        cfg = mod.config_from_env({"MESSAGEBOX_POLL_MS_FOR_TEST": "100",
                                   "MESSAGEBOX_SESSIONS_DIR": "/x"})
        if cfg.poll_ms != 100:
            problems.append("override ignored with MESSAGEBOX_SESSIONS_DIR")
        cfg = mod.config_from_env({"MESSAGEBOX_POLL_MS_FOR_TEST": "5",
                                   "MESSAGEBOX_SESSIONS_DIR": "/x"})
        if cfg.poll_ms is not None:
            problems.append("an out-of-range override was honoured")
        box = mod.Messagebox(mod.config_from_env({}))
        if box.poll_s != 3.0:
            problems.append("default poll interval %r" % box.poll_s)
        return problems, ()
    _guard(suite, GRP_D, "D11 the poll override needs MESSAGEBOX_SESSIONS_DIR; the default is 3 s", poll_override_rule)

    def parent_walk():
        mod = _mod()
        problems = []
        if mod._parent_pid(os.getpid()) != os.getppid():
            problems.append("_parent_pid(self) != getppid")
        proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
        try:
            if mod._parent_pid(proc.pid) != os.getpid():
                problems.append("_parent_pid(child) != our pid")
        finally:
            proc.kill()
            proc.wait()
        return problems, ()
    _guard(suite, GRP_D, "D12 the parent lookup works on this platform", parent_walk)

    def labels():
        mod = _mod()
        problems = []
        for raw, want in (("proj", "proj"), ("My Project!", "My-Project-"), ("", "claude"),
                          ("...", "claude"), ("a.b-c_d", "a.b-c_d"), ("!!!", "claude"), ("a" * 100, "a" * 64),
                          ("über", "-ber"), (None, "claude")):
            got = mod._label(raw, "claude")
            if got != want:
                problems.append("%r -> %r, want %r" % (raw, got, want))
            if not SH.NAME_RE.match(got):
                problems.append("%r -> %r breaks the grammar" % (raw, got))
        return problems, ()
    _guard(suite, GRP_D, "D13 names and hosts are restricted to [A-Za-z0-9._-], 1-64", labels)


# ---------------------------------------------------------------------------
# E. register, poll, spool, acks
# ---------------------------------------------------------------------------

def _run_e(suite, fx):
    def sequence():
        w = World(fx, "e1")
        c = w.server()
        other = w.other()
        problems = []
        if not registered(w, c):
            return ["no register"], ()
        sp = w.spool()
        for path, want in ((w.root, 0o700), (sp, 0o700), (os.path.join(sp, "mcp.lock"), 0o600)):
            got = stat.S_IMODE(os.stat(path).st_mode)
            if got != want:
                problems.append("%s mode %o, want %o" % (os.path.basename(path), got, want))
        status, ans, _h = api(w.hub, "send", {"peerId": other.peer_id, "to": c.info()["name"],
                                              "text": "hello\nworld"})
        msg_id = ans["msgId"]
        if not wait_for(lambda: os.path.exists(spool_json_path(w, msg_id))):
            return problems + ["message never reached the spool"], ()
        path = spool_json_path(w, msg_id)
        data = read_json(path)
        if sorted(data) != sorted(["msgId", "seq", "from", "text", "replyTo", "sentAt"]):
            problems.append("spool keys %r" % sorted(data))
        if data["from"] != {"name": "other", "host": "elsewhere"} or data["text"] != "hello\nworld" \
                or data["replyTo"] is not None or data["msgId"] != msg_id:
            problems.append("spool content %r" % (data,))
        if stat.S_IMODE(os.stat(path).st_mode) != 0o600:
            problems.append("message file mode %o" % stat.S_IMODE(os.stat(path).st_mode))
        if not os.path.exists(spool_json_path(w, msg_id, ".seen")):
            problems.append("no .seen claim")
        if any(n.endswith(".tmp") for n in spool_files(w)):
            problems.append("a .tmp file was left")
        if not wait_for(lambda: w.hub.message_state(msg_id) == "fetched"):
            problems.append("hub state %r, want fetched" % w.hub.message_state(msg_id))
        # ack folding: the poll that delivered it carried no ack for it; the next did
        reqs = [r for r in w.hub.requests if r["route"] == "poll" and r["body"]]
        delivering = [i for i, r in enumerate(reqs) if msg_id in r.get("delivered", [])]
        acking = [i for i, r in enumerate(reqs)
                  if any(a["msgId"] == msg_id for a in r["body"].get("acks", []))]
        if len(delivering) != 1 or len(acking) != 1 or acking[0] <= delivering[0]:
            problems.append("fold order: delivered at %r, acked at %r" % (delivering, acking))
        elif [a for a in reqs[acking[0]]["body"]["acks"] if a["msgId"] == msg_id] != \
                [{"msgId": msg_id, "state": "fetched"}]:
            problems.append("first ack was not fetched")
        # hand-off
        os.rename(path, spool_json_path(w, msg_id, ".delivered"))
        if not wait_for(lambda: w.hub.message_state(msg_id) == "enqueued"):
            problems.append("hub state %r after the hand-off, want enqueued" % w.hub.message_state(msg_id))
        # the model's ack
        is_error, _t, d = c.call("ack", {"msg_id": msg_id})
        if is_error or d.get("state") != "read":
            problems.append("ack: %r %r" % (is_error, d))
        if not (os.path.exists(spool_json_path(w, msg_id, ".read"))
                and not os.path.exists(spool_json_path(w, msg_id, ".delivered"))):
            problems.append(".delivered did not become .read")
        if not wait_for(lambda: w.hub.message_state(msg_id) == "read"):
            problems.append("hub state %r after ack, want read" % w.hub.message_state(msg_id))
        n = sum(1 for r in w.hub.requests if r["route"] == "poll" and r["body"]
                for a in r["body"].get("acks", []) if a["msgId"] == msg_id and a["state"] == "fetched")
        if n != 1:
            problems.append("fetched was acked %d times" % n)
        return problems, ()
    _guard(suite, GRP_E, "E1 register, spool write, fetched folded into the NEXT poll, hand-off, ack", sequence)

    def ack_variants():
        w = World(fx, "e2")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        ids = []
        for i in range(2):
            ids.append(other.send(me, "m%d" % i)[1]["msgId"])
        wait_for(lambda: len(only_json(w)) == 2)
        problems = []
        is_error, _t, d = c.call("ack", {"msg_id": ids[0]})     # straight from .json
        if is_error or not os.path.exists(spool_json_path(w, ids[0], ".read")):
            problems.append("ack from .json: %r %r" % (is_error, d))
        is_error, _t, d = c.call("ack", {"msg_id": ids[0]})     # twice
        if is_error:
            problems.append("a second ack failed: %r" % (d,))
        is_error, _t, d = c.call("ack", {"msg_id": "ms_" + "Z" * 22})
        if is_error is not True or _err_code(d) != "unknown_message":
            problems.append("unknown id: %r %r" % (is_error, _err_code(d)))
        is_error, _t, d = c.call("ack", {"msg_id": "../etc/passwd"})
        if is_error is not True or _err_code(d) != "invalid_params":
            problems.append("bad id grammar: %r %r" % (is_error, _err_code(d)))
        if not wait_for(lambda: w.hub.message_state(ids[0]) == "read"):
            problems.append("hub state %r, want read (a jump from fetched)" % w.hub.message_state(ids[0]))
        return problems, ()
    _guard(suite, GRP_E, "E2 ack: from .json, twice, an unknown id, a bad id", ack_variants)

    def reply_flow():
        w = World(fx, "e3")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        mid = other.send(me, "question?")[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
        problems = []
        is_error, _t, d = c.call("reply", {"msg_id": mid, "text": "answer\r\nline2"})
        if is_error or d.get("to") != "other" or not SH.ID_RE.match(d.get("msgId", "")):
            problems.append("reply: %r %r" % (is_error, d))
        sent = w.hub.bodies("send")[-1]
        if sent.get("replyTo") != mid or "to" in sent or sent.get("text") != "answer\nline2" \
                or sorted(sent) != ["peerId", "replyTo", "text"]:
            problems.append("send body %r" % (sent,))
        if not os.path.exists(spool_json_path(w, mid, ".read")):
            problems.append("the replied-to message is not .read")
        if not wait_for(lambda: w.hub.message_state(mid) == "read"):
            problems.append("hub state %r, want read" % w.hub.message_state(mid))
        got = other.poll()["messages"]
        if len(got) != 1 or got[0]["replyTo"] != mid or got[0]["text"] != "answer\nline2":
            problems.append("the other session got %r" % (got,))
        is_error, _t, d = c.call("reply", {"msg_id": "ms_" + "Q" * 22, "text": "x"})
        if is_error is not True or _err_code(d) != "unknown_message":
            problems.append("reply to an unknown id: %r %r" % (is_error, _err_code(d)))
        is_error, _t, d = c.call("reply", {"msg_id": mid, "text": "x‮y"})
        if is_error is not True or _err_code(d) != "invalid_params":
            problems.append("reply with a bidi text: %r %r" % (is_error, _err_code(d)))
        return problems, ()
    _guard(suite, GRP_E, "E3 reply: sends with replyTo to the sender's name, then acks read", reply_flow)

    def send_list_status():
        w = World(fx, "e4")
        c = w.server()
        other = w.other()
        registered(w, c)
        problems = []
        is_error, _t, d = c.call("list")
        peers = (d or {}).get("peers", [])
        if is_error or sorted(p["name"] for p in peers) != ["other", "proj"]:
            problems.append("list: %r %r" % (is_error, d))
        for p in peers:
            if sorted(p) != ["host", "lastSeen", "name", "self"]:
                problems.append("list row keys %r" % sorted(p))
        if [p["name"] for p in peers if p["self"]] != [c.info()["name"]]:
            problems.append("self marker wrong: %r" % (peers,))
        if "pe_" in json.dumps(d):
            problems.append("list shows a peer id")
        is_error, _t, d = c.call("send", {"to": "other", "text": "ping " + CANARY})
        if is_error or d.get("to") != "other" or not SH.ID_RE.match(d.get("msgId", "")):
            problems.append("send: %r %r" % (is_error, d))
        if "pe_" in json.dumps(d):
            problems.append("send shows a peer id")
        mid = d["msgId"]
        states = []
        for acks in (None, [{"msgId": mid, "state": "fetched"}], [{"msgId": mid, "state": "enqueued"}],
                     [{"msgId": mid, "state": "read"}]):
            if acks is None:
                is_error, _t, s = c.call("status", {"msg_ids": [mid]})
            else:
                other.poll(acks)
                is_error, _t, s = c.call("status", {"msg_ids": [mid]})
            states.append(s["messages"][0]["state"] if not is_error else "ERR " + _t)
        if states != ["queued", "fetched", "enqueued", "read"]:
            problems.append("states %r" % states)
        is_error, _t, s = c.call("status", {"msg_ids": [mid, "ms_" + "N" * 22]})
        if [m["state"] for m in s["messages"]] != ["read", "unknown"]:
            problems.append("status with an unknown id: %r" % (s,))
        for bad in ([], ["x"], [mid] * 51):
            is_error, _t, s = c.call("status", {"msg_ids": bad})
            if is_error is not True or _err_code(s) != "invalid_params":
                problems.append("status %d ids: %r %r" % (len(bad), is_error, _err_code(s)))
        return problems, ()
    _guard(suite, GRP_E, "E4 list, send and status: names only, the state walks queued..read", send_list_status)

    def send_errors():
        w = World(fx, "e5")
        c = w.server()
        w.other()
        registered(w, c)
        me = c.info()["name"]
        problems = []
        is_error, _t, d = c.call("send", {"to": "ghost", "text": "x"})
        if is_error is not True or _err_code(d) != "no_such_peer" or "ghost" not in _t:
            problems.append("unknown target: %r %r" % (is_error, _err_code(d)))
        is_error, _t, d = c.call("send", {"to": me, "text": "x"})
        if is_error is not True or _err_code(d) != "hub_refused":
            problems.append("self: %r %r" % (is_error, _err_code(d)))
        is_error, _t, d = c.call("send", {"to": "bad name!", "text": "x"})
        if is_error is not True or _err_code(d) != "invalid_params":
            problems.append("bad to: %r %r" % (is_error, _err_code(d)))
        for peer in list(w.hub.peers.values()):
            peer["last_seen"] = 0.0
        is_error, _t, d = c.call("list")
        return problems, ()
    _guard(suite, GRP_E, "E5 send errors: unknown target, self, bad name", send_errors)

    def uniqueness():
        w = World(fx, "e6", cwd="/work/Proj")
        pid, start_s = w.helper_cc(cwd="/other/proj")
        c1 = w.server()
        registered(w, c1)
        c2 = w.server(MESSAGEBOX_WALK_PID=str(pid))
        wait_for(lambda: w.hub.count("register") >= 2)
        problems = []
        names = sorted([c1.info()["name"], c2.info()["name"]])
        if names != ["Proj", "proj-2"]:
            problems.append("assigned names %r" % names)
        wait_for(lambda: c2.info()["hub"]["registered"])
        if c2.info()["name"] != "proj-2":
            problems.append("info does not show the hub-assigned name: %r" % c2.info()["name"])
        return problems, ()
    _guard(suite, GRP_E, "E6 name uniqueness is the hub's (case-insensitive -2 suffix); info shows it", uniqueness)

    def reregister():
        w = World(fx, "e7")
        c = w.server()
        registered(w, c)
        wait_for(lambda: os.path.exists(os.path.join(w.spool(), "owner.json")))
        first = read_json(os.path.join(w.spool(), "owner.json"))["peerId"]
        w.hub.forget_peer(first)
        problems = []
        if not wait_for(lambda: w.hub.count("register") >= 2):
            return ["no re-register after a 404"], ()
        wait_for(lambda: read_json(os.path.join(w.spool(), "owner.json"))["peerId"] != first)
        second = read_json(os.path.join(w.spool(), "owner.json"))["peerId"]
        keys = [b["instanceKey"] for b in w.hub.bodies("register")]
        if len(set(keys)) != 1:
            problems.append("instanceKey changed across the re-register: %r" % keys)
        if second == first:
            problems.append("owner.json still names the lost peer")
        wait_for(lambda: c.info()["hub"]["registered"])
        polls_after = w.hub.count("poll")
        time.sleep(0.5)
        if w.hub.count("poll") <= polls_after:
            problems.append("polling did not resume")
        return problems, ()
    _guard(suite, GRP_E, "E7 an unknown peerId (404) re-registers with the SAME instanceKey", reregister)

    def duplicate_delivery():
        w = World(fx, "e8")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        mid = other.send(me, "once")[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
        wait_for(lambda: w.hub.message_state(mid) == "fetched")
        before = os.stat(spool_json_path(w, mid))
        problems = []
        w.hub.requeue(mid)                      # the hub "missed" the fetched ack
        if not wait_for(lambda: w.hub.message_state(mid) == "fetched"):
            problems.append("a duplicate delivery was not re-acked fetched")
        after = os.stat(spool_json_path(w, mid))
        if (before.st_ino, before.st_mtime) != (after.st_ino, after.st_mtime):
            problems.append("the spool file was rewritten on a duplicate")
        if len(only_json(w)) != 1:
            problems.append("%d .json files" % len(only_json(w)))
        os.rename(spool_json_path(w, mid), spool_json_path(w, mid, ".delivered"))
        wait_for(lambda: w.hub.message_state(mid) == "enqueued")
        w.hub.requeue(mid)
        if not wait_for(lambda: w.hub.message_state(mid) == "enqueued"):
            problems.append("a duplicate of a .delivered message was not re-acked enqueued")
        if only_json(w):
            problems.append("a duplicate of a .delivered message came back as .json")
        os.rename(spool_json_path(w, mid, ".delivered"), spool_json_path(w, mid, ".read"))
        w.hub.requeue(mid)
        if not wait_for(lambda: w.hub.message_state(mid) == "read"):
            problems.append("a duplicate of a .read message was not re-acked read")
        return problems, ()
    _guard(suite, GRP_E, "E8 duplicate delivery: .seen EEXIST re-acks the current state, writes nothing", duplicate_delivery)

    def reconcile():
        w = World(fx, "e9")
        os.mkdir(w.spool(), 0o700)
        os.chmod(w.spool(), 0o700)
        dlv, rd, js = "ms_" + "D" * 22, "ms_" + "R" * 22, "ms_" + "J" * 22
        rej = "ms_" + "E" * 22
        for name in (dlv + ".delivered", rd + ".read", dlv + ".seen", rd + ".seen",
                     rej + ".rejected", rej + ".seen"):
            _write(os.path.join(w.spool(), name), "{}")
        _write(os.path.join(w.spool(), js + ".json"), json.dumps(inbound(js)))
        c = w.server()
        registered(w, c)
        wait_for(lambda: any(r["body"] and r["body"].get("acks") for r in w.hub.requests
                             if r["route"] == "poll"))
        acks = {}
        for body in w.hub.bodies("poll"):
            for a in body.get("acks", []):
                acks[a["msgId"]] = a["state"]
        problems = []
        if acks.get(dlv) != "enqueued" or acks.get(rd) != "read" or acks.get(rej) != "rejected":
            problems.append("start-up acks %r" % (acks,))
        if js in acks:
            problems.append("an unhandled .json was acked before any write: %r" % acks.get(js))
        return problems, ()
    _guard(suite, GRP_E, "E9 start-up reconciliation: .delivered -> enqueued, .read -> read in the first poll", reconcile)

    def unread_count():
        w = World(fx, "e10")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        if c.info()["localUnread"] != 0:
            return ["localUnread not 0 at start: %r" % c.info()["localUnread"]], ()
        ids = [other.send(me, "n%d" % i)[1]["msgId"] for i in range(3)]
        wait_for(lambda: len(only_json(w)) == 3)
        problems = []
        if c.info()["localUnread"] != 3:
            problems.append("localUnread %r, want 3" % c.info()["localUnread"])
        os.rename(spool_json_path(w, ids[0]), spool_json_path(w, ids[0], ".delivered"))
        c.call("ack", {"msg_id": ids[1]})
        if c.info()["localUnread"] != 1:
            problems.append("localUnread %r, want 1" % c.info()["localUnread"])
        age = c.info()["lastPollAgeSeconds"]
        if not isinstance(age, int) or age > 3:
            problems.append("lastPollAgeSeconds %r" % (age,))
        return problems, ()
    _guard(suite, GRP_E, "E10 info: local unread counts .json files; last poll age", unread_count)

    def keep_address():
        w = World(fx, "e11")
        c1 = w.server()
        registered(w, c1)
        wait_for(lambda: os.path.exists(os.path.join(w.spool(), "owner.json")))
        first = read_json(os.path.join(w.spool(), "owner.json"))
        c1.close()
        c2 = w.server()
        wait_for(lambda: w.hub.count("register") >= 2)
        problems = []
        keys = [b["instanceKey"] for b in w.hub.bodies("register")]
        if len(keys) < 2 or keys[0] != keys[1] or keys[0] != first["instanceKey"]:
            problems.append("instanceKey not reused after a restart: %r" % (keys,))
        wait_for(lambda: c2.info()["hub"]["registered"])
        if len([p for p in w.hub.peers.values() if not p["gone"]]) != 1:
            problems.append("the restart made a second live peer")
        if read_json(os.path.join(w.spool(), "owner.json"))["peerId"] != first["peerId"]:
            problems.append("the restarted server did not get the same peer back")
        return problems, ()
    _guard(suite, GRP_E, "E11 an MCP restart in one session reuses the instanceKey (same peer)", keep_address)

    def fresh_key_cases():
        problems = []
        good = "k" * 22
        cases = [("another ccStart", lambda w: {"instanceKey": good, "ccPid": w.pid, "ccStart": START_S + 1, "peerId": "x"}),
                 ("another ccPid", lambda w: {"instanceKey": good, "ccPid": w.pid + 1, "ccStart": START_S, "peerId": "x"}),
                 ("bad key grammar", lambda w: {"instanceKey": "short!", "ccPid": w.pid, "ccStart": START_S, "peerId": "x"}),
                 ("key not a string", lambda w: {"instanceKey": 7, "ccPid": w.pid, "ccStart": START_S, "peerId": "x"}),
                 ("ccPid a bool", lambda w: {"instanceKey": good, "ccPid": True, "ccStart": START_S, "peerId": "x"})]
        for label, build in cases:
            w = World(fx, "e12")
            os.mkdir(w.spool(), 0o700)
            os.chmod(w.spool(), 0o700)
            _write(os.path.join(w.spool(), "owner.json"), json.dumps(build(w)))
            w.server()
            wait_for(lambda: w.hub.count("register") >= 1)
            if w.hub.bodies("register")[0]["instanceKey"] == good:
                problems.append("%s: the key was reused" % label)
        w = World(fx, "e12b")
        os.mkdir(w.spool(), 0o700)
        os.chmod(w.spool(), 0o700)
        _write(os.path.join(w.spool(), "owner.json"),
               json.dumps({"instanceKey": good, "ccPid": w.pid, "ccStart": START_S, "peerId": "x"}))
        w.server()
        wait_for(lambda: w.hub.count("register") >= 1)
        if w.hub.bodies("register")[0]["instanceKey"] != good:
            problems.append("control: a matching owner.json was not reused")
        return problems, ()
    _guard(suite, GRP_E, "E12 owner.json is reused only for the same ccPid+ccStart and a valid key", fresh_key_cases)

    def reply_by_identity():
        w = World(fx, "e13")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        mid = other.send(me, "question?")[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
        problems = []
        api(w.hub, "bye", {"peerId": other.peer_id})
        impostor = w.other("other")                       # the freed name passes on
        if impostor.name != "other":
            problems.append("the impostor did not take the name")
        is_error, text, d = c.call("reply", {"msg_id": mid, "text": "secret answer"})
        if is_error is not True or _err_code(d) != "sender_gone" or "no longer live" not in text:
            problems.append("reply to a departed sender: %r %r %r" % (is_error, _err_code(d), text))
        if impostor.poll()["messages"]:
            problems.append("the reply reached the session that took the name")
        if os.path.exists(spool_json_path(w, mid, ".read")):
            problems.append("a failed reply marked the message read")
        sent = w.hub.bodies("send")[-1]
        if "to" in sent or sent.get("replyTo") != mid or sorted(sent) != ["peerId", "replyTo", "text"]:
            problems.append("wire body %r" % (sent,))
        is_error, text, d = c.call("send", {"to": "nobody", "text": "x"})
        if _err_code(d) != "no_such_peer" or w.hub.count("list"):
            problems.append("send 404 classification: %r, %d list probe(s)" % (_err_code(d), w.hub.count("list")))
        return problems, ()
    _guard(suite, GRP_E, "E13 a reply is routed by identity (no `to`); a departed sender is sender_gone, never the new owner of its name", reply_by_identity)

    def not_registered_404():
        w = World(fx, "e14")
        c = w.server()
        w.other()
        registered(w, c)
        peer_id = read_json(os.path.join(w.spool(), "owner.json"))["peerId"]
        # the hub loses us and cannot take a re-register: a poll 404 sets the flag
        w.hub.fault("register", "status", status=503, count=50, retry_after=60)
        w.hub.forget_peer(peer_id)
        problems = []
        if not wait_for(lambda: c.info()["hub"]["registered"] is False):
            return ["the poll 404 did not mark the session unregistered"], ()
        is_error, text, d = c.call("send", {"to": "other", "text": "x"})
        if is_error is not True or _err_code(d) != "not_registered":
            problems.append("send while unregistered: %r %r" % (is_error, _err_code(d)))
        if w.hub.count("list"):
            problems.append("the list probe was called")
        return problems, ()
    _guard(suite, GRP_E, "E14 a send 404 is classified by our own registration state, with no list probe", not_registered_404)

    def rejected_by_hook():
        w = World(fx, "e15")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        ids = [other.send(me, "m%d" % i)[1]["msgId"] for i in range(2)]
        wait_for(lambda: len(only_json(w)) == 2)
        wait_for(lambda: w.hub.message_state(ids[0]) == "fetched")
        os.rename(spool_json_path(w, ids[0]), spool_json_path(w, ids[0], ".rejected"))   # what the hook does
        problems = []
        if not wait_for(lambda: w.hub.message_state(ids[0]) == "rejected"):
            problems.append("the hub saw %r, want rejected" % w.hub.message_state(ids[0]))
        shown = c.call("status", {"msg_ids": ids})  # sender is `other`, not us: unknown here
        if shown[0]:
            problems.append("status failed")
        s = api(w.hub, "status", {"peerId": other.peer_id, "msgIds": ids})[1]["messages"]
        if [m["state"] for m in s] != ["rejected", "fetched"]:
            problems.append("sender's status %r" % ([m["state"] for m in s],))
        if c.info()["localUnread"] != 1:
            problems.append("localUnread %r, want 1" % c.info()["localUnread"])
        n = sum(1 for b in w.hub.bodies("poll") for a in b.get("acks", [])
                if a["msgId"] == ids[0] and a["state"] == "rejected")
        if n != 1:
            problems.append("rejected acked %d times" % n)
        return problems, ()
    _guard(suite, GRP_E, "E15 a .rejected file made by the hook is acked rejected, once", rejected_by_hook)

    def rejected_after_delivered():
        w = World(fx, "e16")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        problems = []
        for _round in range(5):
            mid = other.send(me, "m")[1]["msgId"]
            wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
            wait_for(lambda: w.hub.message_state(mid) == "fetched")
            # what the hook does to a claimed file that fails re-validation
            os.rename(spool_json_path(w, mid), spool_json_path(w, mid, ".delivered"))
            os.rename(spool_json_path(w, mid, ".delivered"), spool_json_path(w, mid, ".rejected"))
            if not wait_for(lambda: w.hub.message_state(mid) == "rejected"):
                problems.append("round %d: the hub saw %r, want rejected" % (_round, w.hub.message_state(mid)))
            if any(a["msgId"] == mid and a["state"] == "enqueued"
                   for b in w.hub.bodies("poll") for a in b.get("acks", [])):
                problems.append("round %d: enqueued was acked before the rejection" % _round)
        # and a .delivered that stays is acked enqueued once it has rested
        keep = other.send(me, "stays")[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, keep)))
        os.rename(spool_json_path(w, keep), spool_json_path(w, keep, ".delivered"))
        if not wait_for(lambda: w.hub.message_state(keep) == "enqueued"):
            problems.append("a settled .delivered was not acked enqueued")
        return problems, ()
    _guard(suite, GRP_E, "E16 enqueued is acked only for a .delivered that has rested: a rejection after the claim is never preceded by it", rejected_after_delivered)


# ---------------------------------------------------------------------------
# F. secrets and redirects
# ---------------------------------------------------------------------------

def _run_f(suite, fx):
    def redirects():
        w = World(fx, "f1")
        trap = SH.StubHub().start()
        try:
            c = w.server(args=("--debug",))
            w.hub.fault("register", "redirect", count=2, target=trap.url + "/peers/register")
            w.hub.fault("poll", "redirect", count=3, target=trap.url + "/peers/poll")
            problems = []
            if not wait_for(lambda: c.info()["hub"]["registered"] and w.hub.count("poll") >= 4, 15):
                problems.append("never recovered after the redirects")
            if trap.requests or trap.seen_tokens:
                problems.append("the redirect target saw %d request(s) / tokens %r"
                                % (len(trap.requests), trap.seen_tokens))
            data = c.info()
            if "redirect" not in (data["hub"]["lastError"] or "") and not data["hub"]["registered"]:
                problems.append("lastError does not mention the redirect: %r" % (data["hub"],))
            for where, text in (("stdout", c.stdout_text()), ("stderr", c.stderr_text())):
                if w.token in text:
                    problems.append("token in " + where)
            return problems, ()
        finally:
            trap.stop()
    _guard(suite, GRP_F, "F1 a 3xx is never followed: the second hub saw nothing, the token stayed home", redirects)

    def redirect_on_tool():
        w = World(fx, "f2")
        trap = SH.StubHub().start()
        try:
            c = w.server()
            w.other()
            registered(w, c)
            w.hub.fault("send", "redirect", count=1, target=trap.url + "/peers/send")
            is_error, text, d = c.call("send", {"to": "other", "text": "x"})
            problems = []
            if is_error is not True or "redirect" not in text:
                problems.append("send over a redirect: %r %r" % (is_error, text))
            if trap.requests or trap.seen_tokens:
                problems.append("the redirect target saw a request")
            if w.token in text:
                problems.append("token in the error")
            return problems, ()
        finally:
            trap.stop()
    _guard(suite, GRP_F, "F2 a tool call over a redirect is an isError, not a follow", redirect_on_tool)

    def dead_token():
        w = World(fx, "f3")
        c = w.server()
        registered(w, c)
        wait_for(lambda: w.hub.count("poll") >= 2)
        w.hub.revoke_token(w.token)
        problems = []
        if not wait_for(lambda: c.info()["hub"]["tokenDead"]):
            return ["tokenDead never became true"], ()
        # not stopped for good: one slow retry (10 poll intervals in the test mode)
        times = w.hub.times("poll")
        wait_for(lambda: len(w.hub.times("poll")) >= len(times) + 2, 6)
        later = w.hub.times("poll")[len(times) - 1:]
        gaps = [b - a for a, b in zip(later, later[1:])]
        if len(gaps) < 2 or min(gaps) < 0.9:
            problems.append("retries after a 401 are not slow: gaps %r" % ([round(g, 2) for g in gaps],))
        is_error, text, d = c.call("list")
        if is_error is not True or _err_code(d) != "token_dead":
            problems.append("list after a 401: %r %r" % (is_error, _err_code(d)))
        if w.token in text:
            problems.append("token in the error")
        return problems, ()
    _guard(suite, GRP_F, "F3 a 401: info says the token is dead, tools answer token_dead, polling drops to a slow retry", dead_token)

    def recovers():
        w = World(fx, "f6")
        c = w.server()
        registered(w, c)
        w.hub.revoke_token(w.token)
        problems = []
        if not wait_for(lambda: c.info()["hub"]["tokenDead"]):
            return ["tokenDead never became true"], ()
        fresh = "aisc1_" + "N" * 30
        w.hub.add_token(fresh)
        os.unlink(w.token_path)
        _write(w.token_path, fresh + "\n")            # the operator replaces the file
        if not wait_for(lambda: c.info()["hub"]["tokenDead"] is False, 8):
            problems.append("a replaced token file did not recover")
        is_error, text, d = c.call("list")
        if is_error:
            problems.append("list after the recovery: %r" % (text[:100],))
        if fresh in c.stdout_text() or fresh in c.stderr_text():
            problems.append("the new token is in the output")
        return problems, ()
    _guard(suite, GRP_F, "F6 a replaced token file recovers without an MCP reconnect", recovers)

    def stays_dead():
        w = World(fx, "f7")
        c = w.server()
        registered(w, c)
        w.hub.revoke_token(w.token)
        problems = []
        if not wait_for(lambda: c.info()["hub"]["tokenDead"]):
            return ["tokenDead never became true"], ()
        os.unlink(w.token_path)
        _write(w.token_path, "aisc1_" + "U" * 30 + "\n")  # well formed, unknown to the hub
        n = w.hub.count("poll")
        wait_for(lambda: w.hub.count("poll") >= n + 3, 8)
        if w.hub.count("poll") < n + 3:
            problems.append("no retries while dead")
        if c.info()["hub"]["tokenDead"] is not True:
            problems.append("a still-dead token was reported alive")
        is_error, _t, d = c.call("send", {"to": "x", "text": "y"})
        if is_error is not True or _err_code(d) != "token_dead":
            problems.append("tools while still dead: %r %r" % (is_error, _err_code(d)))
        return problems, ()
    _guard(suite, GRP_F, "F7 a still-dead token stays dead, retried each time", stays_dead)

    def secrets_never():
        w = World(fx, "f4")
        c = w.server(args=("--debug",))
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        mid = other.send(me, "inbound " + CANARY)[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
        outputs = [c.call("send", {"to": "other", "text": "outbound " + CANARY})[1],
                   c.call("list")[1], c.call("info")[1], c.call()[1],
                   c.call("reply", {"msg_id": mid, "text": "back " + CANARY})[1],
                   c.call("status", {"msg_ids": [mid]})[1],
                   c.call("ack", {"msg_id": mid})[1]]
        w.hub.revoke_token(w.token)
        wait_for(lambda: c.info()["hub"]["tokenDead"])
        outputs.append(c.call("send", {"to": "other", "text": "x"})[1])
        w.hub.fault("poll", "drop", count=1)
        c.close()
        problems = []
        haystacks = {"tool output": "\n".join(outputs), "stdout": c.stdout_text(),
                     "stderr": c.stderr_text()}
        for where, text in haystacks.items():
            if w.token in text or w.token.split("_", 1)[1] in text:
                problems.append("the token is in the %s" % where)
        for where in ("stderr",):
            if CANARY in haystacks[where]:
                problems.append("message text is in the %s (--debug)" % where)
        if "pe_" in haystacks["tool output"].replace("ms_", ""):
            problems.append("a peer id reached tool output")
        return problems, ["stderr bytes: %d" % len(haystacks["stderr"])]
    _guard(suite, GRP_F, "F4 the token and message text never appear in output, errors or --debug logs", secrets_never)

    def log_file():
        w = World(fx, "f5")
        logp = os.path.join(w.dir, "mb.log")
        c = w.server(args=("--log-file", logp))
        other = w.other()
        registered(w, c)
        mid = other.send(c.info()["name"], "x " + CANARY)[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, mid)))
        c.call("send", {"to": "other", "text": "y " + CANARY})
        c.close()
        with open(logp, "r", encoding="utf-8") as fh:
            text = fh.read()
        problems = []
        if not text:
            problems.append("--log-file wrote nothing")
        if w.token in text or CANARY in text:
            problems.append("token or message text in the log file")
        if stat.S_IMODE(os.stat(logp).st_mode) != 0o600:
            problems.append("log file mode %o" % stat.S_IMODE(os.stat(logp).st_mode))
        return problems, ()
    _guard(suite, GRP_F, "F5 --log-file: structure only, 0600", log_file)


# ---------------------------------------------------------------------------
# G. backoff and Retry-After
# ---------------------------------------------------------------------------

def _run_g(suite, fx):
    def backoff():
        w = World(fx, "g1")
        w.hub.fault("poll", "drop", count=4)
        c = w.server()
        problems = []
        if not wait_for(lambda: w.hub.count("poll") >= 6, 20):
            return ["not enough polls"], ()
        times = w.hub.times("poll")
        gaps = [b - a for a, b in zip(times, times[1:])]
        if not (gaps[0] >= 0.19 and gaps[1] > gaps[0] and gaps[2] > gaps[1] and gaps[3] > gaps[2]):
            problems.append("gaps do not grow: %r" % ([round(g, 2) for g in gaps],))
        if gaps[3] < 1.5:
            problems.append("fourth gap %.2f: no exponential growth" % gaps[3])
        if gaps[4] > 0.6:
            problems.append("no return to the normal pace after recovery: %.2f" % gaps[4])
        if not wait_for(lambda: c.info()["hub"]["reachable"] is True):
            problems.append("info never showed the hub reachable again")
        return problems, ["gaps: %r" % ([round(g, 2) for g in gaps],)]
    _guard(suite, GRP_G, "G1 hub down: the retry gaps double, then the normal pace returns", backoff)

    def down_info():
        w = World(fx, "g2")
        w.hub.fault("poll", "drop", count=3)
        c = w.server()
        problems = []
        seen_down = wait_for(lambda: c.info()["hub"]["reachable"] is False, 10)
        if not seen_down:
            problems.append("info never reported the hub unreachable")
        else:
            data = c.info()
            if not data["hub"]["lastError"]:
                problems.append("no lastError while the hub is down")
            w.hub.fault("list", "drop", count=1)
            is_error, _t, d = c.call("list")
            if is_error is not True or _err_code(d) != "hub_unreachable":
                problems.append("list while down: %r %r" % (is_error, _err_code(d)))
        return problems, ()
    _guard(suite, GRP_G, "G2 while the hub is down tools answer hub_unreachable and info says so", down_info)

    def retry_after():
        problems = []
        for status in (429, 503):
            w = World(fx, "g3")
            w.hub.fault("poll", "status", status=status, retry_after=1)
            c = w.server()
            if not wait_for(lambda: w.hub.count("poll") >= 3, 15):
                problems.append("%d: not enough polls" % status)
                continue
            times = w.hub.times("poll")
            gap = times[1] - times[0]
            if gap < 1.0:
                problems.append("%d: Retry-After 1 honoured as %.2f s" % (status, gap))
            if gap > 3.0:
                problems.append("%d: waited %.2f s for Retry-After 1" % (status, gap))
            if times[2] - times[1] > 0.6:
                problems.append("%d: pace did not recover" % status)
        return problems, ()
    _guard(suite, GRP_G, "G3 Retry-After on 429 and 503 is honoured (with jitter above it)", retry_after)

    def retry_register():
        w = World(fx, "g4")
        w.hub.fault("register", "status", status=429, retry_after=1)
        w.server()
        problems = []
        if not wait_for(lambda: w.hub.count("register") >= 2, 15):
            return ["no second register attempt"], ()
        times = w.hub.times("register")
        if times[1] - times[0] < 1.0:
            problems.append("register retried after %.2f s" % (times[1] - times[0]))
        return problems, ()
    _guard(suite, GRP_G, "G4 a 429 on register is also waited out", retry_register)

    def pace():
        w = World(fx, "g5", poll_ms=150)
        w.server()
        wait_for(lambda: w.hub.count("poll") >= 12, 15)
        times = w.hub.times("poll")
        gaps = [b - a for a, b in zip(times, times[1:])]
        problems = []
        if min(gaps) < 0.14:
            problems.append("polled faster than the interval: %.3f s" % min(gaps))
        if max(gaps) > 0.6:
            problems.append("a gap of %.2f s in a healthy run" % max(gaps))
        return problems, ["min gap %.3f" % min(gaps)]
    _guard(suite, GRP_G, "G5 never polls faster than the interval", pace)

    def refused_register():
        w = World(fx, "g6")
        w.hub.fault("register", "status", status=409, count=2)
        c = w.server()
        problems = []
        if not wait_for(lambda: c.info()["hub"]["registered"], 15):
            problems.append("never registered after two 409s")
        if w.hub.count("register") < 3:
            problems.append("%d register attempts" % w.hub.count("register"))
        return problems, ()
    _guard(suite, GRP_G, "G6 a refused register (409) is retried with backoff, not abandoned", refused_register)


# ---------------------------------------------------------------------------
# H. hostile hub payloads
# ---------------------------------------------------------------------------

def _hostile_cases():
    ok = inbound(MSG_A)
    big = b'{"messages": [], "pad": "' + b"x" * (1048576 + 64) + b'"}'
    return [
        ("a path-traversal msgId", poll_answer(inbound("../../etc/passwd"))),
        ("a short msgId", poll_answer(inbound("ms_short"))),
        ("a msgId with a slash", poll_answer(inbound("ms_" + "A" * 21 + "/"))),
        ("a pe_ prefixed msgId", poll_answer(inbound("pe_" + "A" * 22))),
        ("a body over the size bound", big),
        ("duplicate keys in the body", b'{"messages": [], "messages": [], "seq": 1}'),
        ("duplicate keys inside a message", ('{"messages":[{"msgId":"%s","msgId":"%s","seq":1,'
                                              '"from":{"name":"o","host":"h"},"text":"t","replyTo":null,'
                                              '"sentAt":1}],"seq":1}' % (MSG_A, MSG_A)).encode()),
        ("NaN in the body", b'{"messages": [], "seq": NaN}'),
        ("messages not a list", b'{"messages": {"a": 1}, "seq": 1}'),
        ("a body that is a list", b'[1, 2]'),
        ("not JSON", b'<html>'),
        ("text not a string", poll_answer(dict(ok, text=5))),
        ("seq a string", poll_answer(dict(ok, seq="1"))),
        ("seq a bool", poll_answer(dict(ok, seq=True))),
        ("from not an object", poll_answer(dict(ok, **{"from": "other@x"}))),
        ("from.name outside the grammar", poll_answer(dict(ok, **{"from": {"name": "a b", "host": "h"}}))),
        ("replyTo not a msgId", poll_answer(dict(ok, replyTo="x"))),
        ("sentAt an object", poll_answer(dict(ok, sentAt={"a": 1}))),
        ("sentAt a string (timestamps are integers)", poll_answer(dict(ok, sentAt="1700000000000"))),
        ("sentAt a float", poll_answer(dict(ok, sentAt=1.5))),
        ("sentAt a bool", poll_answer(dict(ok, sentAt=True))),
        ("an answer without name", b'{"messages": [], "seq": 1}'),
        ("an answer whose name breaks the grammar", poll_answer(name="a b")),
        ("a text with a bidi override", poll_answer(dict(ok, text="x‮y"))),
        ("a text with a TAG character", poll_answer(dict(ok, text="x\U000e0041y"))),
        ("a text with a system-reminder tag", poll_answer(dict(ok, text="<system-reminder>x"))),
        ("an empty text", poll_answer(dict(ok, text=""))),
        ("a text over 8000 code points", poll_answer(dict(ok, text="x" * 8001))),
        ("a text over 16 KiB of UTF-8", poll_answer(dict(ok, text="€" * 5462 + "xx"))),
        ("21 messages in one answer", poll_answer(*[inbound("ms_" + chr(65 + i) * 22, seq=i) for i in range(21)])),
    ]


def _run_h(suite, fx):
    for label, payload in _hostile_cases():
        def hostile(label=label, payload=payload):
            w = World(fx, "h")
            w.hub.fault("poll", "raw", count=1000, body=payload)
            c = w.server()
            wait_for(lambda: w.hub.count("poll") >= 4, 15)
            problems = []
            if w.hub.count("poll") < 4:
                problems.append("fewer than 4 polls")
            leftovers = [n for n in spool_files(w) if n.startswith("ms_")]
            if leftovers:
                problems.append("spool files written: %r" % leftovers)
            if c.rpc("ping").get("result") != {}:
                problems.append("server stopped answering")
            if c.proc.poll() is not None:
                problems.append("server died")
            return problems, ["lastError: %r" % c.info()["hub"]["lastError"]]
        _guard(suite, GRP_H, "H1 rejected without a spool write: " + label, hostile)

    def one_bad_one_good():
        w = World(fx, "h2")
        w.hub.fault("poll", "raw", count=1,
                    body=poll_answer(inbound("../x"), inbound(MSG_B, text="good"),
                                     dict(inbound(MSG_A), text="x‮y")))
        w.server()
        problems = []
        if not wait_for(lambda: os.path.exists(spool_json_path(w, MSG_B))):
            return ["the valid sibling was not stored"], ()
        names = [n for n in spool_files(w) if n.startswith("ms_")]
        if sorted(names) != [MSG_B + ".json", MSG_B + ".seen"]:
            problems.append("spool holds %r" % names)
        return problems, ()
    _guard(suite, GRP_H, "H2 one bad message among good ones is dropped alone", one_bad_one_good)

    def logged_once():
        w = World(fx, "h5")
        w.hub.fault("poll", "raw", count=1000, body=poll_answer(dict(inbound(MSG_A), text="x‮y")))
        c = w.server()
        wait_for(lambda: w.hub.count("poll") >= 8, 15)
        time.sleep(0.2)
        n = c.stderr_text().count("malformed inbound message was dropped")
        problems = [] if n == 1 else ["logged %d times over %d polls" % (n, w.hub.count("poll"))]
        if CANARY in c.stderr_text() or "‮" in c.stderr_text():
            problems.append("message text in the log")
        return problems, ()
    _guard(suite, GRP_H, "H5 a message that fails re-validation is logged once per id, not once per poll", logged_once)

    def refused_is_acked_rejected():
        w = World(fx, "h6")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        bad = w.hub.enqueue(me, "x‮y", from_name="other")        # bypasses the hub's own text rule
        good = other.send(me, "fine")[1]["msgId"]
        problems = []
        if not wait_for(lambda: w.hub.message_state(bad) == "rejected"):
            problems.append("the hub saw %r for the refused message" % w.hub.message_state(bad))
        wait_for(lambda: os.path.exists(spool_json_path(w, good)))
        time.sleep(0.5)
        delivered = [i for r in w.hub.requests if r["route"] == "poll" for i in r.get("delivered", [])]
        if delivered.count(bad) != 1:
            problems.append("the refused message was delivered %d times" % delivered.count(bad))
        if [n for n in spool_files(w) if n.startswith(bad)]:
            problems.append("spool files for a refused message: %r" % spool_files(w))
        if not os.path.exists(spool_json_path(w, good)):
            problems.append("the good message behind it was held back")
        n = sum(1 for b in w.hub.bodies("poll") for a in b.get("acks", [])
                if a["msgId"] == bad and a["state"] == "rejected")
        if n != 1:
            problems.append("rejected acked %d times" % n)
        return problems, ()
    _guard(suite, GRP_H, "H6 a message the server refuses is acked rejected once and never delivered again", refused_is_acked_rejected)

    def strict_stamps():
        w = World(fx, "h7")
        c = w.server()
        w.other()
        registered(w, c)
        problems = []
        for stamp in ("1700000000000", 1.5, True, None, -1):
            w.hub.fault("list", "raw", count=1, body=json.dumps(
                {"peers": [{"name": "a", "host": "h", "lastSeenAt": stamp, "self": True}]}).encode())
            is_error, _t, d = c.call("list")
            if is_error is not True or _err_code(d) != "hub_error":
                problems.append("lastSeenAt %r accepted: %r" % (stamp, d))
        w.hub.fault("status", "raw", count=1, body=json.dumps(
            {"messages": [{"msgId": MSG_A, "state": "read", "updatedAt": "x"}]}).encode())
        is_error, _t, d = c.call("status", {"msg_ids": [MSG_A]})
        if is_error is not True:
            problems.append("updatedAt 'x' accepted")
        w.hub.fault("list", "raw", count=1, body=json.dumps(
            {"peers": [{"name": "a", "host": "h", "lastSeenAt": 1700000000000, "self": True}]}).encode())
        is_error, _t, d = c.call("list")
        if is_error or d["peers"][0]["lastSeen"] != 1700000000000:
            problems.append("control: an integer stamp was refused: %r" % (d,))
        return problems, ()
    _guard(suite, GRP_H, "H7 timestamps are integers (epoch ms): a string, float, bool or negative is refused", strict_stamps)

    def bad_register():
        problems = []
        for label, body in (("bad peerId", {"peerId": "pe_x", "name": "n", "pollMs": 3000}),
                            ("name outside the grammar", {"peerId": "pe_" + "A" * 22, "name": "a b",
                                                          "pollMs": 3000}),
                            ("pollMs a string", {"peerId": "pe_" + "A" * 22, "name": "n", "pollMs": "3000"})):
            w = World(fx, "h3")
            w.hub.fault("register", "raw", count=1000, body=json.dumps(body).encode())
            c = w.server()
            wait_for(lambda: w.hub.count("register") >= 2, 15)
            if w.hub.count("poll") or c.info()["hub"]["registered"]:
                problems.append("%s: accepted" % label)
            if os.path.exists(os.path.join(w.spool(), "owner.json")):
                problems.append("%s: owner.json written" % label)
        return problems, ()
    _guard(suite, GRP_H, "H3 a malformed register answer is not trusted: no poll, no owner.json", bad_register)

    def bad_tool_answers():
        w = World(fx, "h4")
        c = w.server()
        w.other()
        registered(w, c)
        problems = []
        for route, fn, params in (("list", "list", {}), ("send", "send", {"to": "other", "text": "x"}),
                                  ("status", "status", {"msg_ids": [MSG_A]})):
            w.hub.fault(route, "raw", count=1, body=b'{"peers": [{"name": "x y"}], "messages": [{}], "msgId": 1}')
            is_error, text, d = c.call(fn, params)
            if is_error is not True:
                problems.append("%s: a malformed answer was returned as success: %r" % (fn, text[:100]))
        return problems, ()
    _guard(suite, GRP_H, "H4 malformed list/send/status answers are errors, not passed through", bad_tool_answers)


# ---------------------------------------------------------------------------
# I. spool hygiene
# ---------------------------------------------------------------------------

def _run_i(suite, fx):
    def refused(label, setup, want):
        w = World(fx, "i")
        setup(w)
        c = w.server()
        data = c.info()
        time.sleep(0.3)
        problems = []
        if data["active"] or want not in (data["inactiveReason"] or ""):
            problems.append("%s: %r" % (label, data["inactiveReason"]))
        if w.hub.count("register"):
            problems.append("%s: registered" % label)
        return problems

    def root_perms():
        return refused("root 0755", lambda w: os.chmod(w.root, 0o755), "chmod 700"), ()
    _guard(suite, GRP_I, "I1 a spool root with group/other bits is refused", root_perms)

    def root_symlink():
        def setup(w):
            real = os.path.join(w.dir, "real")
            os.mkdir(real, 0o700)
            os.rmdir(w.root)
            os.symlink(real, w.root)
        return refused("root symlink", setup, "not a real directory"), ()
    _guard(suite, GRP_I, "I2 a symlinked spool root is refused", root_symlink)

    def session_symlink():
        def setup(w):
            real = os.path.join(w.dir, "elsewhere")
            os.mkdir(real, 0o700)
            os.symlink(real, w.spool())
        return refused("session dir symlink", setup, "not a real directory"), ()
    _guard(suite, GRP_I, "I3 a symlinked session directory is refused", session_symlink)

    def session_perms():
        def setup(w):
            os.mkdir(w.spool(), 0o755)
            os.chmod(w.spool(), 0o755)
        return refused("session dir 0755", setup, "chmod 700"), ()
    _guard(suite, GRP_I, "I4 a session directory with group/other bits is refused", session_perms)

    def session_file():
        def setup(w):
            _write(w.spool(), "x")
        return refused("session path is a file", setup, "not a real directory"), ()
    _guard(suite, GRP_I, "I5 a regular file where the session directory should be is refused", session_file)

    def second_server():
        w = World(fx, "i6")
        c1 = w.server()
        registered(w, c1)
        c2 = w.server()
        data = c2.info()
        time.sleep(0.4)
        problems = []
        if data["active"] or "another messagebox server" not in (data["inactiveReason"] or ""):
            problems.append("second server: %r" % (data,))
        if w.hub.count("register") != 1:
            problems.append("%d registers; the second server must not register" % w.hub.count("register"))
        is_error, _t, d = c2.call("send", {"to": "x", "text": "y"})
        if is_error is not True or _err_code(d) != "inactive":
            problems.append("second server send: %r %r" % (is_error, _err_code(d)))
        if c1.close() != 0:
            problems.append("first server did not exit 0")
        c3 = w.server()
        if not wait_for(lambda: w.hub.count("register") >= 2 and c3.info()["active"]):
            problems.append("a third server could not take the released lock: %r" % (c3.info(),))
        return problems, ()
    _guard(suite, GRP_I, "I6 a second server for one session holds off (flock); the lock dies with its holder", second_server)

    def planted_files():
        w = World(fx, "i7")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        problems = []
        link_id, big_id = "ms_" + "L" * 22, "ms_" + "G" * 22
        os.symlink("/etc/hosts", spool_json_path(w, link_id))
        with open(spool_json_path(w, big_id), "w") as fh:
            fh.write(json.dumps(inbound(big_id, text="x")) + " " * 70000)
        for mid, label in ((link_id, "symlink"), (big_id, "over 64 KiB")):
            is_error, _t, d = c.call("reply", {"msg_id": mid, "text": "x"})
            if is_error is not True or _err_code(d) != "spool_error":
                problems.append("%s: %r %r" % (label, is_error, _err_code(d)))
        good = other.send(me, "fine")[1]["msgId"]
        wait_for(lambda: os.path.exists(spool_json_path(w, good)))
        os.unlink(spool_json_path(w, good))
        os.symlink("/etc/hosts", spool_json_path(w, good))
        is_error, _t, d = c.call("reply", {"msg_id": good, "text": "x"})
        if is_error is not True or _err_code(d) != "spool_error":
            problems.append("a swapped-in symlink: %r %r" % (is_error, _err_code(d)))
        return problems, ()
    _guard(suite, GRP_I, "I7 spool files are read O_NOFOLLOW, regular, at most 64 KiB", planted_files)

    def exclusive_claim():
        w = World(fx, "i8")
        os.mkdir(w.spool(), 0o700)
        os.chmod(w.spool(), 0o700)
        victim = os.path.join(w.dir, "victim")
        _write(victim, "precious")
        os.symlink(victim, spool_json_path(w, MSG_A, ".seen"))
        w.hub.fault("poll", "raw", count=1000, body=poll_answer(inbound(MSG_A)))
        c = w.server()
        wait_for(lambda: w.hub.count("poll") >= 4)
        with open(victim, "r") as fh:
            content = fh.read()
        problems = []
        if content != "precious":
            problems.append("a planted .seen symlink was written through")
        if os.path.exists(spool_json_path(w, MSG_A)) and read_json(spool_json_path(w, MSG_A)).get("text") != "hi":
            problems.append("odd message file")
        return problems, ()
    _guard(suite, GRP_I, "I8 the claim is O_EXCL|O_NOFOLLOW: a planted .seen symlink is never written through", exclusive_claim)


# ---------------------------------------------------------------------------
# J. shutdown and sweep
# ---------------------------------------------------------------------------

def _run_j(suite, fx):
    def bye():
        w = World(fx, "j1")
        c = w.server()
        other = w.other()
        registered(w, c)
        me = c.info()["name"]
        m1 = other.send(me, "one")[1]["msgId"]
        m2 = other.send(me, "two")[1]["msgId"]
        wait_for(lambda: len(only_json(w)) == 2)
        os.rename(spool_json_path(w, m2), spool_json_path(w, m2, ".delivered"))
        wait_for(lambda: w.hub.message_state(m2) == "enqueued")
        wait_for(lambda: w.hub.message_state(m1) == "fetched")
        t0 = time.time()
        rc = c.close()
        problems = []
        if rc != 0:
            problems.append("exit code %r" % rc)
        if time.time() - t0 > 6:
            problems.append("slow exit: %.1f s" % (time.time() - t0))
        byes = w.hub.bodies("bye")
        if len(byes) != 1 or byes[0].get("undelivered") != [m1] or not SH.ID_RE.match("ms_" + "A" * 22):
            problems.append("bye bodies %r" % (byes,))
        if w.hub.message_state(m1) != "undelivered":
            problems.append("m1 is %r at the hub, want undelivered" % w.hub.message_state(m1))
        if w.hub.message_state(m2) != "enqueued":
            problems.append("m2 is %r, want enqueued" % w.hub.message_state(m2))
        left = sorted(n for n in spool_files(w) if n.startswith("ms_"))
        if m1 + ".json" not in left or m2 + ".delivered" not in left:
            problems.append("bye removed spool files: %r" % left)
        if not os.path.exists(os.path.join(w.spool(), "mcp.lock")):
            problems.append("mcp.lock was removed")
        return problems, ()
    _guard(suite, GRP_J, "J1 stdin EOF: bye carries the remaining .json ids only; nothing is removed", bye)

    def sigterm():
        w = World(fx, "j2")
        c = w.server()
        registered(w, c)
        wait_for(lambda: w.hub.count("poll") >= 2)
        c.proc.send_signal(signal.SIGTERM)
        problems = []
        try:
            rc = c.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            return ["SIGTERM did not stop the server"], ()
        if rc != 0:
            problems.append("exit code %r" % rc)
        if w.hub.count("bye") != 1:
            problems.append("%d bye request(s)" % w.hub.count("bye"))
        return problems, ()
    _guard(suite, GRP_J, "J2 SIGTERM takes the same exit path (bye, exit 0)", sigterm)

    def bye_dead_hub():
        w = World(fx, "j3")
        c = w.server()
        registered(w, c)
        w.hub.fault("bye", "slow", seconds=6)
        t0 = time.time()
        rc = c.close(wait=10)
        problems = []
        if rc != 0 or time.time() - t0 > 7:
            problems.append("exit %r after %.1f s with a slow hub" % (rc, time.time() - t0))
        return problems, ()
    _guard(suite, GRP_J, "J3 a hub that stalls on bye cannot hold the exit hostage", bye_dead_hub)

    def sweep():
        w = World(fx, "j4", session=False)
        alive_pid, alive_start = w.helper_cc(cwd="/live", started_ms=START_MS + 900000)
        make_session(w.sessions, w.pid, cwd="/work/proj")
        mkdir = lambda name: (os.mkdir(os.path.join(w.root, name), 0o700),
                              os.chmod(os.path.join(w.root, name), 0o700))[0]
        dead = "99999991-5"
        mismatch = "%d-%d" % (alive_pid, alive_start - 7)
        alive = "%d-%d" % (alive_pid, alive_start)
        held = "99999992-5"
        for name in (dead, mismatch, alive, held, "notes"):
            mkdir(name)
        _write(os.path.join(w.root, dead, "ms_" + "X" * 22 + ".json"), "{}")
        _write(os.path.join(w.root, held, "mcp.lock"), "")
        real = os.path.join(w.dir, "linktarget")
        os.mkdir(real, 0o700)
        _write(os.path.join(real, "keep"), "x")
        os.symlink(real, os.path.join(w.root, "123-4"))
        loose = os.path.join(w.root, "99999993-5")
        os.mkdir(loose, 0o755)
        os.chmod(loose, 0o755)
        lock_fd = os.open(os.path.join(w.root, held, "mcp.lock"), os.O_RDWR)
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # own dir: old .read/.seen go, fresh ones and .json stay
        os.mkdir(w.spool(), 0o700)
        os.chmod(w.spool(), 0o700)
        old = time.time() - 8 * 86400
        young = time.time() - 6 * 86400
        names = {"ms_" + "a" * 22 + ".read": old, "ms_" + "b" * 22 + ".seen": old,
                 "ms_" + "c" * 22 + ".read": young, "ms_" + "d" * 22 + ".seen": young,
                 "ms_" + "e" * 22 + ".json": old, "ms_" + "f" * 22 + ".delivered": old,
                 "ms_" + "g" * 22 + ".abcd1234.tmp": time.time() - 7200,
                 "owner.json.ffff0000.tmp": time.time() - 7200,
                 "ms_" + "h" * 22 + ".abcd1234.tmp": time.time() - 600}
        for name, when in names.items():
            path = os.path.join(w.spool(), name)
            _write(path, "{}")
            os.utime(path, (when, when))
        try:
            c = w.server()
            registered(w, c)
            time.sleep(0.2)
            problems = []
            left = set(os.listdir(w.root))
            for name, want in ((dead, False), (mismatch, False), (alive, True), (held, True),
                               ("notes", True), ("123-4", True), ("99999993-5", True)):
                if (name in left) != want:
                    problems.append("%s: %s" % (name, "kept" if name in left else "removed"))
            if not os.path.exists(os.path.join(real, "keep")):
                problems.append("the sweep followed a symlink")
            mine = set(os.listdir(w.spool()))
            for name, want in (("ms_" + "a" * 22 + ".read", False), ("ms_" + "b" * 22 + ".seen", False),
                               ("ms_" + "c" * 22 + ".read", True), ("ms_" + "d" * 22 + ".seen", True),
                               ("ms_" + "e" * 22 + ".json", True), ("ms_" + "f" * 22 + ".delivered", True),
                               ("ms_" + "g" * 22 + ".abcd1234.tmp", False),
                               ("owner.json.ffff0000.tmp", False),
                               ("ms_" + "h" * 22 + ".abcd1234.tmp", True)):
                if (name in mine) != want:
                    problems.append("own %s: %s" % (name, "kept" if name in mine else "removed"))
            return problems, ["root: %r" % sorted(left)]
        finally:
            os.close(lock_fd)
    _guard(suite, GRP_J, "J4 sweep: dead and mismatched siblings go; live, locked, odd and symlinked stay; old .read/.seen go", sweep)


# ---------------------------------------------------------------------------
# K. handler crash (in-process)
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _run_k(suite, fx):
    def layer_d():
        mod = _mod()
        handler = _Capture()
        logger = logging.getLogger(LOGGER)
        logger.addHandler(handler)
        propagate, logger.propagate = logger.propagate, False   # no stderr noise
        saved = dict(mod._HANDLERS)

        def boom(params):
            raise RuntimeError("secret-" + CANARY)
        mod._HANDLERS["info"] = boom
        try:
            res = mod.handle_messagebox_call({"function": "info"})
        finally:
            mod._HANDLERS.clear()
            mod._HANDLERS.update(saved)
            logger.removeHandler(handler)
            logger.propagate = propagate
        problems = []
        if res != {"error": {"code": "internal", "message": "RuntimeError"}}:
            problems.append("result %r" % (res,))
        logged = [r for r in handler.records if r.exc_info and r.levelno >= logging.ERROR]
        if not logged:
            problems.append("no log.exception record with exc_info")
        elif CANARY in logged[0].getMessage() or "%" in str(logged[0].msg) and "%s" in str(logged[0].msg):
            problems.append("the message is not a literal")
        return problems, ()
    _guard(suite, GRP_K, "K1 layer D: internal + the exception TYPE only + a traceback in the log", layer_d)

    def layer_w():
        mod = _mod()
        saved = mod.handle_messagebox_call

        def boom(arguments):
            raise RuntimeError("secret-" + CANARY)
        mod.handle_messagebox_call = boom
        try:
            resp = mod.McpServer()._handle_tool_call(7, {"name": TOOL, "arguments": {}})
        finally:
            mod.handle_messagebox_call = saved
        result = resp["result"]
        problems = []
        if result.get("isError") is not True or (result.get("structuredContent") or {}).get(
                "error", {}).get("code") != "internal":
            problems.append("result %r" % (result,))
        if CANARY in json.dumps(resp):
            problems.append("the exception message leaked")
        return problems, ()
    _guard(suite, GRP_K, "K2 layer W: a handler escape is still an isError result", layer_w)

    def log_value_used():
        mod = _mod()
        return ([] if callable(getattr(mod, "_log_value", None)) and
                callable(getattr(mod, "_strict_loads", None)) else ["generated blocks absent"]), ()
    _guard(suite, GRP_K, "K3 the generated logging and strict-JSON blocks are present", log_value_used)


# ---------------------------------------------------------------------------
# L. concurrency
# ---------------------------------------------------------------------------

def _run_l(suite, fx):
    def slow_call():
        w = World(fx, "l1")
        c = w.server()
        w.other()
        registered(w, c)
        w.hub.fault("send", "slow", seconds=2.5)
        c._id += 1
        sid = c._id
        c.write({"jsonrpc": "2.0", "id": sid, "method": "tools/call",
                 "params": {"name": TOOL, "arguments": {"function": "send",
                                                        "params": {"to": "other", "text": "slow"}}}})
        t0 = time.time()
        c._id += 1
        pid = c._id
        c.write({"jsonrpc": "2.0", "id": pid, "method": "ping", "params": {}})
        first = c.read(8)
        problems = []
        if not first or first.get("id") != pid or time.time() - t0 > 1.5:
            problems.append("the ping was not answered ahead of the slow call: %r" % (first,))
        second = c.read(10)
        if not second or second.get("id") != sid or (second.get("result") or {}).get("isError"):
            problems.append("the slow call was not answered: %r" % (second,))
        return problems, ()
    _guard(suite, GRP_L, "L1 a slow hub call does not block the read loop", slow_call)

    def cancel():
        w = World(fx, "l2")
        c = w.server()
        w.other()
        registered(w, c)
        w.hub.fault("send", "slow", seconds=1.5)
        c._id += 1
        sid = c._id
        c.write({"jsonrpc": "2.0", "id": sid, "method": "tools/call",
                 "params": {"name": TOOL, "arguments": {"function": "send",
                                                        "params": {"to": "other", "text": "slow"}}}})
        time.sleep(0.3)
        c.write({"jsonrpc": "2.0", "method": "notifications/cancelled",
                 "params": {"requestId": sid}})
        time.sleep(2.2)
        c._id += 1
        pid = c._id
        c.write({"jsonrpc": "2.0", "id": pid, "method": "ping", "params": {}})
        seen = []
        while True:
            resp = c.read(5)
            if resp is None:
                break
            seen.append(resp.get("id"))
            if resp.get("id") == pid:
                break
        problems = []
        if sid in seen:
            problems.append("a cancelled request was answered")
        if pid not in seen:
            problems.append("the server stopped answering after a cancel")
        return problems, ()
    _guard(suite, GRP_L, "L2 notifications/cancelled suppresses the reply; the server goes on", cancel)


# ---------------------------------------------------------------------------
# S. the stub hub itself
# ---------------------------------------------------------------------------

def _run_s(suite, fx):
    def with_hub(fn):
        def run():
            hub = SH.StubHub().start()
            try:
                return fn(hub)
            finally:
                hub.stop()
        return run

    def auth_and_bodies(hub):
        problems = []
        key = "k" * 22
        good = {"instanceKey": key, "name": "n", "host": "h"}
        if api(hub, "register", good, token=None)[0] != 401:
            problems.append("no token was not 401")
        if api(hub, "register", good, token="aisc1_wrong")[0] != 401:
            problems.append("a wrong token was not 401")
        if api(hub, "register", raw=b'{"instanceKey":"%s","name":"n","name":"m","host":"h"}' % key.encode())[0] != 400:
            problems.append("duplicate keys were not 400")
        for label, raw in (("NaN", b'{"instanceKey":"%s","name":"n","host":NaN}' % key.encode()),
                           ("extra key", json.dumps(dict(good, extra=1)).encode()),
                           ("wrong type", json.dumps(dict(good, name=5)).encode()),
                           ("bool as str", json.dumps(dict(good, host=True)).encode()),
                           ("short key", json.dumps(dict(good, instanceKey="short")).encode()),
                           ("bad name", json.dumps(dict(good, name="a b")).encode()),
                           ("not an object", b"[1]"), ("not JSON", b"nope"),
                           ("over 8 KiB", b'{"pad":"' + b"x" * 9000 + b'"}')):
            if api(hub, "register", raw=raw)[0] != 400:
                problems.append("%s was not 400" % label)
        if api(hub, "register", good)[0] != 200:
            problems.append("a valid register was refused")
        status = api(hub, "send", raw=b'{"peerId":"p","to":"t","text":"' + b"x" * 70000 + b'"}')[0]
        if status != 400:
            problems.append("a 70 KB send was %d" % status)
        return problems, ()
    _guard(suite, GRP_S, "S1 the stub: 401 first, then strict bodies (400)", with_hub(auth_and_bodies))

    def register_rules(hub):
        problems = []
        a = Peer(hub, "Proj")
        b = Peer(hub, "proj")
        c3 = Peer(hub, "PROJ")
        if [a.name, b.name, c3.name] != ["Proj", "proj-2", "PROJ-3"]:
            problems.append("names %r" % [a.name, b.name, c3.name])
        again = api(hub, "register", {"instanceKey": a.key, "name": "Other", "host": "h"})[1]
        if again["peerId"] != a.peer_id or again["name"] != "Proj":
            problems.append("register is not idempotent: %r" % (again,))
        other_token = "aisc1_" + "z" * 30
        hub.add_token(other_token)
        if api(hub, "register", {"instanceKey": a.key, "name": "n", "host": "h"}, other_token)[0] != 404:
            problems.append("another token re-registering an instanceKey was not 404")
        if api(hub, "poll", {"peerId": a.peer_id}, other_token)[0] != 404:
            problems.append("token binding: another token polled this peer")
        peers = [a, b, c3] + [Peer(hub, "p%d" % i) for i in range(13)]
        if any(p.status != 200 for p in peers):
            problems.append("the first 16 were not all accepted")
        if Peer(hub, "extra").status != 409:
            problems.append("the 17th live peer was not 409")
        return problems, ()
    _guard(suite, GRP_S, "S2 the stub: name uniqueness, idempotence, token binding, 16-peer cap", with_hub(register_rules))

    def ack_machine(hub):
        problems = []
        a, b = Peer(hub, "a"), Peer(hub, "b")
        outsider = Peer(hub, "c")
        mid = a.send("b", "x")[1]["msgId"]
        sender_state = lambda: api(hub, "status", {"peerId": a.peer_id, "msgIds": [mid]})[1]["messages"][0]["state"]

        def ack(state, who=b):
            who.poll([{"msgId": mid, "state": state}])
            return sender_state()
        seq = [ack("read"), ack("fetched"), ack("enqueued"), ack("read")]
        if seq != ["read", "read", "read", "read"]:
            problems.append("a jump then backward acks: %r" % seq)
        mid2 = a.send("b", "y")[1]["msgId"]
        walk = []
        for state in ("fetched", "fetched", "enqueued", "fetched", "read"):
            b.poll([{"msgId": mid2, "state": state}])
            walk.append(api(hub, "status", {"peerId": a.peer_id, "msgIds": [mid2]})[1]["messages"][0]["state"])
        if walk != ["fetched", "fetched", "enqueued", "enqueued", "read"]:
            problems.append("forward only: %r" % walk)
        mid3 = a.send("b", "z")[1]["msgId"]
        outsider.poll([{"msgId": mid3, "state": "read"}])
        if hub.message_state(mid3) != "queued":
            problems.append("a foreign ack changed the state")
        hub.age_message(mid3, hub.ttl_s + 5)
        b.poll([{"msgId": mid3, "state": "read"}])
        if hub.message_state(mid3) != "expired" and api(hub, "status", {"peerId": a.peer_id, "msgIds": [mid3]})[1]["messages"][0]["state"] != "expired":
            problems.append("expired was not absorbing")
        mid4 = a.send("b", "w")[1]["msgId"]
        api(hub, "bye", {"peerId": b.peer_id, "undelivered": [mid4]})
        if hub.message_state(mid4) != "undelivered":
            problems.append("bye did not mark undelivered")
        hub.messages[mid4]["to_peer"] = b.peer_id
        b2 = api(hub, "poll", {"peerId": b.peer_id, "acks": [{"msgId": mid4, "state": "enqueued"}]})
        if hub.message_state(mid4) != "enqueued":
            problems.append("undelivered was not overridden by enqueued (%r)" % hub.message_state(mid4))
        # rejected: from queued or fetched only, then absorbing, and status shows it
        r1 = a.send("b", "r1")[1]["msgId"]
        r2 = a.send("b", "r2")[1]["msgId"]
        r3 = a.send("b", "r3")[1]["msgId"]
        b.poll([{"msgId": r1, "state": "rejected"}])
        b.poll([{"msgId": r2, "state": "fetched"}])
        b.poll([{"msgId": r2, "state": "rejected"}])
        b.poll([{"msgId": r3, "state": "enqueued"}])
        b.poll([{"msgId": r3, "state": "rejected"}])
        got = [hub.message_state(m) for m in (r1, r2, r3)]
        if got != ["rejected", "rejected", "enqueued"]:
            problems.append("rejected from queued/fetched/enqueued: %r" % (got,))
        for state in ("read", "enqueued", "fetched"):
            b.poll([{"msgId": r1, "state": state}])
        if hub.message_state(r1) != "rejected":
            problems.append("rejected was not absorbing")
        shown = api(hub, "status", {"peerId": a.peer_id, "msgIds": [r1]})[1]["messages"][0]["state"]
        if shown != "rejected":
            problems.append("status shows %r for a rejected message" % shown)
        if [m["msgId"] for m in b.poll()["messages"] if m["msgId"] in (r1, r2)]:
            problems.append("a rejected message was delivered again")
        return problems, ()
    _guard(suite, GRP_S, "S3 the stub: the ack state machine", with_hub(ack_machine))

    def send_rules(hub):
        problems = []
        a, b = Peer(hub, "a"), Peer(hub, "b")
        if a.send("ghost", "x")[0] != 404:
            problems.append("unknown target was not 404")
        if a.send("a", "x")[0] != 400:
            problems.append("self was not 400")
        if a.send("b", "x", reply_to="ms_" + "Q" * 22)[0] != 400:
            problems.append("an unknown replyTo was not 400")
        mid = a.send("b", "first")[1]["msgId"]
        if b.send("a", "back", reply_to=mid)[0] != 200:
            problems.append("a valid replyTo was refused")
        if a.send("b", "again", reply_to=mid)[0] != 400:
            problems.append("a replyTo not addressed to the sender was not 400")
        for label, text in (("bidi", "x‮y"), ("system-reminder", "x<system-reminder>y"),
                            ("closing upper", "x</SYSTEM-REMINDER>"), ("NUL", "a\x00b"),
                            ("empty", ""), ("8001", "x" * 8001), ("TAG", "a\U000e0041"),
                            ("surrogate-free private use", "a")):
            status, body, _h = a.send("b", text)
            if status != 400:
                problems.append("%s was %d" % (label, status))
            if text and text in json.dumps(body):
                problems.append("%s: the 400 echoed the text" % label)
        if a.send("b", "ok\n\tfine")[0] != 200:
            problems.append("\\n and \\t were refused")
        both = api(hub, "send", {"peerId": a.peer_id, "to": "b", "replyTo": mid, "text": "x"})[0]
        neither = api(hub, "send", {"peerId": a.peer_id, "text": "x"})[0]
        if (both, neither) != (400, 400):
            problems.append("to+replyTo / neither: %r" % ((both, neither),))
        # a reply is routed by identity: the name may have passed to someone else
        third_id = a.send("b", "q2")[1]["msgId"]
        api(hub, "bye", {"peerId": a.peer_id})
        impostor = Peer(hub, "a")
        if impostor.name != "a":
            problems.append("the impostor did not get the freed name")
        status, _body, _h = b.send(None, "late", reply_to=third_id)
        if status != 404:
            problems.append("a reply to a sender that left was %d, not 404" % status)
        if impostor.poll()["messages"]:
            problems.append("the reply reached the session that took the name")
        return problems, ()
    _guard(suite, GRP_S, "S4 the stub: send validation; refusals never echo the text", with_hub(send_rules))

    def poll_batch(hub):
        problems = []
        a, b = Peer(hub, "a"), Peer(hub, "b")
        ids = [a.send("b", "m%02d" % i)[1]["msgId"] for i in range(25)]
        got = b.poll()["messages"]
        if [m["msgId"] for m in got] != ids[:20]:
            problems.append("first batch %d, not the 20 oldest in order" % len(got))
        b.poll([{"msgId": m["msgId"], "state": "fetched"} for m in got])
        rest = b.poll()["messages"]
        if [m["msgId"] for m in rest] != ids[20:]:
            problems.append("second batch %r" % len(rest))
        hub2 = a.send("b", "x" * 8000)
        return problems, ()
    _guard(suite, GRP_S, "S5 the stub: poll lists queued messages in seq order, 20 at a time", with_hub(poll_batch))

    def stale_and_bye(hub):
        problems = []
        hub.stale_s = 0.3
        a = Peer(hub, "a")
        time.sleep(0.5)
        b = Peer(hub, "a")
        if b.name != "a":
            problems.append("a stale peer's name was not freed: %r" % b.name)
        ans = a.poll()
        if ans is None or ans.get("name") == "a":
            problems.append("a revived peer kept a taken name: %r" % (ans,))
        hub.stale_s = 90
        api(hub, "bye", {"peerId": b.peer_id})
        c = Peer(hub, "a")
        if c.name != "a":
            problems.append("bye did not free the name: %r" % c.name)
        return problems, ()
    _guard(suite, GRP_S, "S6 the stub: stale peers free their name and are revived by a poll; bye frees at once", with_hub(stale_and_bye))

    def faults(hub):
        problems = []
        a = Peer(hub, "a")
        hub.fault("poll", "status", status=429, retry_after=7)
        status, _b, headers = api(hub, "poll", {"peerId": a.peer_id})
        if status != 429 or headers.get("Retry-After") != "7":
            problems.append("status fault: %r %r" % (status, headers))
        hub.fault("poll", "redirect")
        if api(hub, "poll", {"peerId": a.peer_id})[0] != 302:
            problems.append("redirect fault")
        hub.fault("poll", "unknown_peer")
        if api(hub, "poll", {"peerId": a.peer_id})[0] != 404:
            problems.append("unknown_peer fault")
        hub.fault("poll", "raw", body=b"RAW", status=200)
        conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
        conn.request("POST", "/peers/poll", b"{}", {"Authorization": "Bearer " + hub.token})
        if conn.getresponse().read() != b"RAW":
            problems.append("raw fault")
        hub.fault("poll", "drop")
        try:
            api(hub, "poll", {"peerId": a.peer_id})
            problems.append("drop fault answered")
        except (http.client.HTTPException, OSError):
            pass
        hub.fault("poll", "slow", seconds=0.4)
        t0 = time.time()
        status = api(hub, "poll", {"peerId": a.peer_id})[0]
        if status != 200 or time.time() - t0 < 0.35:
            problems.append("slow fault: %d after %.2f s" % (status, time.time() - t0))
        if api(hub, "poll", {"peerId": a.peer_id})[0] != 200:
            problems.append("faults were not consumed")
        if hub.count("poll") < 7:
            problems.append("requests were not recorded")
        return problems, ()
    _guard(suite, GRP_S, "S7 the stub: every fault kind, consumed in order", with_hub(faults))

    def status_rules(hub):
        problems = []
        a, b = Peer(hub, "a"), Peer(hub, "b")
        mid = a.send("b", "x")[1]["msgId"]
        st = api(hub, "status", {"peerId": b.peer_id, "msgIds": [mid]})[1]["messages"][0]["state"]
        if st != "unknown":
            problems.append("a recipient saw the sender's status: %r" % st)
        hub.age_message(mid, hub.ttl_s + 1)
        st = api(hub, "status", {"peerId": a.peer_id, "msgIds": [mid]})[1]["messages"][0]["state"]
        if st != "expired":
            problems.append("an old unread message was %r, want expired" % st)
        if api(hub, "status", {"peerId": a.peer_id, "msgIds": []})[0] != 400:
            problems.append("0 msgIds was not 400")
        if api(hub, "status", {"peerId": a.peer_id, "msgIds": ["ms_" + "A" * 22] * 51})[0] != 400:
            problems.append("51 msgIds was not 400")
        listed = api(hub, "list", {"peerId": a.peer_id})[1]["peers"]
        if sorted(p["name"] for p in listed) != ["a", "b"] or [p["self"] for p in listed if p["name"] == "a"] != [True]:
            problems.append("list: %r" % (listed,))
        if any(set(p) != {"name", "host", "lastSeenAt", "self"} for p in listed):
            problems.append("list rows carry other keys")
        return problems, ()
    _guard(suite, GRP_S, "S8 the stub: status is the sender's only; expiry; list shape", with_hub(status_rules))


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(opts=None):
    opts = opts or H.Options()
    suite = H.Suite(NAME, title="mcp-messagebox: peer messaging client", opts=opts, mode="grouped")
    fx = Fixtures(keep=opts.keep)
    try:
        for step in (_run_a, _run_b, _run_c, _run_d, _run_e, _run_f, _run_g, _run_h, _run_i,
                     _run_j, _run_k, _run_l, _run_s):
            step(suite, fx)
    finally:
        fx.cleanup()
        _MODULE.clear()
    suite.render()
    suite.print_summary()
    return suite


def main(argv=None):
    opts = H.parse_options(argv)
    if opts.help:
        print(__doc__)
        return 0
    return run(opts).exit_code


if __name__ == "__main__":
    sys.exit(main())
