#!/usr/bin/env python3
"""messagebox-hook -- hands the messages in a session's spool to the model.

The asyncRewake hook half of mcp-messagebox (Scripts/mcp-messagebox.py). Run as
`python3 -I <path>/messagebox-hook.py` from the SessionStart and Stop hooks of
the user's settings. It reads ONLY the local spool directory
~/.claude/messagebox/<cc_pid>-<cc_start>/ that the MCP server fills, and it
never talks to the network: the server acks `enqueued` when it sees the
`.delivered` file this hook leaves, and `rejected` when it sees `.rejected`.

  * exits 0 at once, printing nothing, when MESSAGEBOX_DISABLE=1, when no
    owned spool directory exists, when its owner.json does not name this
    Claude Code pid and start time, when the owning session's `entrypoint` is
    not `cli` (unless MESSAGEBOX_FORCE=1), or when hook.lock is held;
  * otherwise loops every 0.5 s with no deadline of its own (the session's
    exit kills it by signal). When `<msgId>.json` files exist it orders them
    by seq (a cheap pre-read, never trusted), then for each, up to 10 messages
    and 16 KiB of text (the first always): renames `.json` -> `.delivered`
    (the single claim point; only a successful rename goes on), opens THAT
    file O_NOFOLLOW, requires a regular file, reads and re-validates it, and
    injects only what it read after the rename. It writes ONE block to stderr
    and exits 2. A message that does not fit the caps once read goes back to
    `.json` for the next wake.

A claimed file that fails re-validation is renamed `.rejected` and never
injected (the server acks `rejected`; it acks `enqueued` for a `.delivered`
only after the file has rested there a moment, so a rejection never follows an
`enqueued`). The
stderr block starts with a newline (the harness glues the fixed rewakeMessage
directly before it) and frames each message with a fresh per-wake nonce:

  <<peer-message nonce=N msg_id=ms_... from=NAME@HOST reply_to=ms_...>>
  text
  <<end-peer-message nonce=N>>

The text is neutralised first: every `<<peer-message` becomes
`<< peer-message` and every `<<end-peer-message` becomes `<< end-peer-message`
in one substring pass, so no marker in a text can pass for the wake's own.
Message text is never logged and nothing goes to stdout.

Test hooks (honoured always, except the last): MESSAGEBOX_SESSIONS_DIR,
MESSAGEBOX_ROOT, MESSAGEBOX_WALK_PID (start the owner walk at that pid), and
MESSAGEBOX_HOOK_POLL_MS_FOR_TEST (poll interval in ms; honoured ONLY when
MESSAGEBOX_SESSIONS_DIR is also set).

Accepted case: if the stderr write fails after the renames, the messages are
`.delivered` but the model never saw them.
"""

import errno
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import time
import unicodedata
from typing import Any, List, NamedTuple, Optional

WALK_MAX_HOPS = 8                # the hook runs under sh -c: more hops than the server
POLL_DEFAULT_S = 0.5
MAX_MESSAGES = 10                # per wake
MAX_TEXT_BYTES = 16384           # of text, per wake (the first message always)
LIST_MAX_NAMES = 2000            # files looked at in one wake

_JSON_FILE_RE = re.compile(r"(ms_[A-Za-z0-9_-]{22})\.json\Z")


# ---------------------------------------------------------------------------
# The local contract with the server, generated once for both files (ADR 0014,
# 0025): the owner walk and the sessions-file reader, the private-directory and
# file-read rules, the name / host / id grammar, the text rule (ai-soul's
# isInvisibleUnsafe with the \n / \t allow-list and the system-reminder
# refusal), the record check and the strict JSON parser. Nothing in it opens a
# socket; a suite row reads this file's imports to keep it so.
# ---------------------------------------------------------------------------

# Refresh: python3 Scripts/amalgamate.py -- do not edit inside the region (§8).
# BEGIN GENERATED: _mcp_spool.py :: _O_NOFOLLOW, _O_DIRECTORY, _O_NONBLOCK, _O_NOCTTY, _O_CLOEXEC, TEXT_MAX_CODE_POINTS, TEXT_MAX_BYTES, SPOOL_FILE_MAX_BYTES, SESSIONS_FILE_MAX_BYTES, _NAME_RE, _PEER_ID_RE, _MSG_ID_RE, _INSTANCE_KEY_RE, _UNSAFE_RANGES, _DEFAULT_IGNORABLE, _PRIVATE_USE, _BIDI, _MARKUP_FORBIDDEN, _in_ranges, _unsafe_label, unsafe_text_reason, _valid_name, _valid_stamp, _valid_message, SPOOL_INT_LITERAL_LIMIT, _spool_no_duplicate_keys, _spool_no_constant, _spool_finite_float, _spool_bounded_int, _SPOOL_DECODER, _parse_strict, _read_regular, _open_dir, _private_dir_problem, OwnerRefused, Owner, _read_session_file, _parent_pid, _find_owner
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


_O_NOCTTY = getattr(os, "O_NOCTTY", 0)


_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


TEXT_MAX_CODE_POINTS = 8000


TEXT_MAX_BYTES = 16384


SPOOL_FILE_MAX_BYTES = 65536


SESSIONS_FILE_MAX_BYTES = 65536


_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


_PEER_ID_RE = re.compile(r"pe_[A-Za-z0-9_-]{22}\Z")


_MSG_ID_RE = re.compile(r"ms_[A-Za-z0-9_-]{22}\Z")


_INSTANCE_KEY_RE = re.compile(r"[A-Za-z0-9_-]{22,43}\Z")


_UNSAFE_RANGES = ((0x0000, 0x001F), (0x007F, 0x009F), (0x00AD, 0x00AD), (0x061C, 0x061C), (0x180E, 0x180E), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x2069), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0xE0000, 0xE007F), (0xE0100, 0xE01EF))


_DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


_PRIVATE_USE = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


_BIDI = ((0x061C, 0x061C), (0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069))


_MARKUP_FORBIDDEN = ("<system-reminder", "</system-reminder")


def _in_ranges(cp, ranges):
    return any(lo <= cp <= hi for lo, hi in ranges)


def _unsafe_label(cp):
    """The class of a refused code point, or None when it is allowed."""
    if 0xD800 <= cp <= 0xDFFF:
        return "lone surrogate"
    category = unicodedata.category(chr(cp))
    if category == "Cc":
        return "control character"
    if cp in (0x2028, 0x2029):
        return "line or paragraph separator"
    if _in_ranges(cp, _BIDI):
        return "bidi control"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x180B <= cp <= 0x180D:
        return "variation selector"
    if 0xE0000 <= cp <= 0xE007F:
        return "TAG block character"
    if category == "Co" or _in_ranges(cp, _PRIVATE_USE):
        return "private use character"
    if category == "Cf":
        return "format character"
    if _in_ranges(cp, _DEFAULT_IGNORABLE):
        return "default-ignorable character"
    if 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE:
        return "noncharacter"
    if _in_ranges(cp, _UNSAFE_RANGES):
        return "invisible or unsafe character"
    return None


def unsafe_text_reason(text):
    """None when `text` may be sent or delivered, else the refusal reason.

    Allowed: every character except C0/C1 controls (only \\n and \\t pass),
    bidi controls, Cf format characters, default-ignorables, U+2028/2029,
    variation selectors, the TAG block, private use, noncharacters and lone
    surrogates -- and the text may not carry a `<system-reminder` or
    `</system-reminder` tag in any case. The reason names the class and the
    code point, never the text.
    """
    for ch in text:
        if ch == "\n" or ch == "\t":
            continue
        label = _unsafe_label(ord(ch))
        if label is not None:
            return "contains a %s (U+%04X)" % (label, ord(ch))
    lowered = text.lower()
    for marker in _MARKUP_FORBIDDEN:
        if marker in lowered:
            return "contains a system-reminder tag"
    return None


def _valid_name(value):
    return isinstance(value, str) and _NAME_RE.match(value) is not None


def _valid_stamp(value):
    """An integer, epoch milliseconds or a sequence number (a bool is not one)."""
    return type(value) is int and value >= 0


def _valid_message(item, msg_id, strict):
    """The message `item` as the spool stores it, or None when it is not the contract.

    Valid ids, from.name / from.host in the grammar, integer seq and sentAt, a
    replyTo that is an id or null, and a text that passes the character rule and
    the size limits. `msg_id`, when given, must be the id in the record (the hook
    passes its file name's). `strict` demands exactly the contract's keys, in the
    record and in `from` (the hook, the last step before the model); the server
    tolerates a key it does not know in a hub message.
    """
    if not isinstance(item, dict):
        return None
    if strict and set(item) != {"msgId", "seq", "from", "text", "replyTo", "sentAt"}:
        return None
    own, sender = item.get("msgId"), item.get("from")
    text, reply_to = item.get("text"), item.get("replyTo")
    if not (isinstance(own, str) and _MSG_ID_RE.match(own)) or (msg_id is not None and own != msg_id):
        return None
    if not _valid_stamp(item.get("seq")) or not _valid_stamp(item.get("sentAt")):
        return None
    if not (isinstance(sender, dict) and (not strict or set(sender) == {"name", "host"}) and _valid_name(sender.get("name")) and _valid_name(sender.get("host"))):
        return None
    if reply_to is not None and not (isinstance(reply_to, str) and _MSG_ID_RE.match(reply_to)):
        return None
    if not isinstance(text, str) or not 1 <= len(text) <= TEXT_MAX_CODE_POINTS:
        return None
    if unsafe_text_reason(text) is not None or len(text.encode("utf-8")) > TEXT_MAX_BYTES:
        return None
    return {"msgId": own, "seq": item["seq"], "from": {"name": sender["name"], "host": sender["host"]}, "text": text, "replyTo": reply_to, "sentAt": item["sentAt"]}


SPOOL_INT_LITERAL_LIMIT = 4300


def _spool_no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _spool_no_constant(name):
    raise ValueError("non-finite number")


def _spool_finite_float(text):
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError("overflowing number")
    return value


def _spool_bounded_int(text):
    if len(text) > SPOOL_INT_LITERAL_LIMIT:
        raise ValueError("integer literal too long")
    return int(text)


_SPOOL_DECODER = json.JSONDecoder(object_pairs_hook=_spool_no_duplicate_keys, parse_constant=_spool_no_constant, parse_float=_spool_finite_float, parse_int=_spool_bounded_int)


def _parse_strict(raw):
    """Bytes -> a JSON value, or ValueError: no duplicate key, no NaN or
    infinity, no integer literal over SPOOL_INT_LITERAL_LIMIT characters, no
    trailing data. The hooks of json.JSONDecoder, not a call to json.loads.
    """
    try:
        text = raw.decode("utf-8").lstrip(" \t\r\n")
        value, end = _SPOOL_DECODER.raw_decode(text)
    except RecursionError:
        raise ValueError("nesting too deep") from None
    if text[end:].strip(" \t\r\n"):
        raise ValueError("trailing data")
    return value


def _read_regular(path, cap, dir_fd=None):
    """A regular file we own, read with O_NOFOLLOW, at most `cap` bytes; else
    OSError / ValueError. `dir_fd` makes `path` relative to a directory fd.
    """
    fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_NOCTTY | _O_CLOEXEC, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_uid != os.getuid():
            raise ValueError("not owned by the current user")
        if st.st_size > cap:
            raise ValueError("too large")
        raw = os.read(fd, cap + 1)
    finally:
        os.close(fd)
    if len(raw) > cap:
        raise ValueError("too large")
    return raw


def _open_dir(path, dir_fd=None):
    return os.open(path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=dir_fd)


def _private_dir_problem(st):
    """Why the directory `st` (an lstat) is not a private one of ours, or None."""
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return "is not a real directory"
    if st.st_uid != os.getuid():
        return "is not owned by the current user"
    if st.st_mode & 0o077:
        return "has group or other permission bits (chmod 700)"
    return None


class OwnerRefused(Exception):
    """No unambiguous owner: the message is the reason."""


class Owner(NamedTuple):
    pid: int
    start: int
    entrypoint: str
    cwd: str


def _read_session_file(sessions_dir, pid):
    """The Owner a sessions file names (`start` is startedAt // 1000); None when
    there is no such file; OwnerRefused when the file is there but not a valid
    session record.
    """
    path = os.path.join(sessions_dir, "%d.json" % pid)
    try:
        raw = _read_regular(path, SESSIONS_FILE_MAX_BYTES)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise OwnerRefused("a sessions file exists but cannot be read safely") from None
    try:
        data = _parse_strict(raw)
    except ValueError:
        raise OwnerRefused("a sessions file exists but is not strict JSON") from None
    started = data.get("startedAt") if isinstance(data, dict) else None
    if not isinstance(data, dict) or type(data.get("pid")) is not int or data["pid"] != pid or type(started) is not int or started < 0:
        raise OwnerRefused("a sessions file exists but does not describe its pid")
    entry, cwd = data.get("entrypoint"), data.get("cwd")
    return Owner(pid, started // 1000, entry if isinstance(entry, str) else "", cwd if isinstance(cwd, str) else "")


def _parent_pid(pid):
    """The parent of `pid`: /proc where it exists, else ps(1)."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            data = fh.read(4096)
        return int(data[data.rfind(b")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    ps = "/bin/ps"
    if not os.path.exists(ps):
        return None
    try:
        out = subprocess.run([ps, "-o", "ppid=", "-p", str(pid)], stdin=subprocess.DEVNULL, capture_output=True, timeout=3, text=True).stdout
        return int(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _find_owner(sessions_dir, start_pid, hops):
    """Walk the parent chain from `start_pid` through at most `hops` pids and
    return the first with a sessions file. A file that is there but invalid ends
    the walk (fail closed). OwnerRefused when none is found.
    """
    pid = start_pid
    for _hop in range(hops):
        if pid is None or pid <= 1:
            break
        owner = _read_session_file(sessions_dir, pid)
        if owner is not None:
            return owner
        pid = _parent_pid(pid)
    raise OwnerRefused("no Claude Code session was found above this process")
# END GENERATED: cdb0cdd35f9d


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config(NamedTuple):
    disable: bool
    force: bool
    sessions_dir: str
    root: str
    walk_pid: Optional[int]
    poll_s: float


def _env_int(env, key: str, lo: int, hi: int) -> Optional[int]:
    raw = env.get(key)
    if raw is None or not re.match(r"[0-9]{1,10}\Z", raw):
        return None
    value = int(raw)
    return value if lo <= value <= hi else None


def config_from_env(env) -> Config:
    home = os.path.expanduser("~")
    sessions_override = env.get("MESSAGEBOX_SESSIONS_DIR") or None
    poll_ms = None
    if sessions_override is not None:
        poll_ms = _env_int(env, "MESSAGEBOX_HOOK_POLL_MS_FOR_TEST", 10, 5000)
    return Config(
        disable=env.get("MESSAGEBOX_DISABLE") == "1",
        force=env.get("MESSAGEBOX_FORCE") == "1",
        sessions_dir=sessions_override or os.path.join(home, ".claude", "sessions"),
        root=env.get("MESSAGEBOX_ROOT") or os.path.join(home, ".claude", "messagebox"),
        walk_pid=_env_int(env, "MESSAGEBOX_WALK_PID", 2, 2 ** 31 - 1),
        poll_s=(poll_ms / 1000.0) if poll_ms is not None else POLL_DEFAULT_S,
    )


# ---------------------------------------------------------------------------
# The owning Claude Code session (the walk itself is in the generated region,
# the same one the server takes; the hook climbs further because it runs under
# sh -c)
# ---------------------------------------------------------------------------

def find_owner(sessions_dir: str, start_pid: Optional[int], hops: int = WALK_MAX_HOPS) -> Owner:
    """The owning session: the walk climbs from `start_pid` through at most
    `hops` pids. OwnerRefused when there is none, or when the nearest file is
    invalid."""
    return _find_owner(sessions_dir, start_pid, hops)

# ---------------------------------------------------------------------------
# The spool
# ---------------------------------------------------------------------------

def open_spool(root: str, owner: Owner) -> Optional[int]:
    """An fd on the owned spool directory, or None. Creates nothing; every
    component must already be a real, private directory of ours."""
    try:
        if _private_dir_problem(os.lstat(root)) is not None:
            return None
        root_fd = _open_dir(root)
    except OSError:
        return None
    try:
        name = "%d-%d" % (owner.pid, owner.start)
        if _private_dir_problem(os.stat(name, dir_fd=root_fd, follow_symlinks=False)) is not None:
            return None
        fd = _open_dir(name, root_fd)
    except OSError:
        return None
    finally:
        os.close(root_fd)
    if _private_dir_problem(os.fstat(fd)) is not None:
        os.close(fd)
        return None
    return fd


def owner_json_names(spool_fd: int, owner: Owner) -> bool:
    """True when the spool's owner.json names exactly this pid and start."""
    try:
        data = _parse_strict(_read_regular("owner.json", SPOOL_FILE_MAX_BYTES, spool_fd))
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict) and type(data.get("ccPid")) is int
            and type(data.get("ccStart")) is int
            and data["ccPid"] == owner.pid and data["ccStart"] == owner.start)

def take_lock(spool_fd: int) -> Optional[int]:
    """hook.lock, held for the process's life; None when another hook has it."""
    try:
        fd = os.open("hook.lock", os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC,
                     0o600, dir_fd=spool_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


# ---------------------------------------------------------------------------
# Re-validation, hand-off, framing
# ---------------------------------------------------------------------------

def validate_record(data: Any, msg_id: str) -> Optional[dict]:
    """The spool record as the hook will use it, or None when anything about
    it is off: exactly the contract's keys, the id of its own file name, the
    grammar on from.name / from.host / replyTo, an integer seq and sentAt, and
    a text that passes the character rule and the size limits. The check is
    the generated _valid_message, strict."""
    return _valid_message(data, msg_id, True)


def neutralise(text: str) -> str:
    """Every `<<peer-message` -> `<< peer-message` and every
    `<<end-peer-message` -> `<< end-peer-message`, in one pass. Only a space is
    inserted, so no marker can be formed and a second pass changes nothing."""
    return text.replace("<<peer-message", "<< peer-message").replace(
        "<<end-peer-message", "<< end-peer-message")


def frame_block(records: List[dict], nonce: str) -> str:
    """The whole stderr block: a leading newline, then every message framed."""
    out = ["\n"]
    for rec in records:
        header = "<<peer-message nonce=%s msg_id=%s from=%s@%s" % (
            nonce, rec["msgId"], rec["from"]["name"], rec["from"]["host"])
        if rec["replyTo"] is not None:
            header += " reply_to=%s" % rec["replyTo"]
        out.append(header + ">>\n" + neutralise(rec["text"]) + "\n"
                   + "<<end-peer-message nonce=%s>>\n" % nonce)
    return "".join(out)


def claim(spool_fd: int, msg_id: str) -> bool:
    """Rename `<msgId>.json` -> `<msgId>.delivered`. True only for the one
    instance whose rename succeeded; ENOENT means someone else took it."""
    try:
        os.rename(msg_id + ".json", msg_id + ".delivered",
                  src_dir_fd=spool_fd, dst_dir_fd=spool_fd)
    except OSError:
        return False
    return True


def _reject(spool_fd: int, msg_id: str) -> None:
    """A claimed file that failed re-validation: `.delivered` -> `.rejected`
    (a rename never follows a symlink, so a planted link is moved, not read
    through). The server acks `rejected` for it."""
    try:
        os.rename(msg_id + ".delivered", msg_id + ".rejected",
                  src_dir_fd=spool_fd, dst_dir_fd=spool_fd)
    except OSError:
        pass


def _unclaim(spool_fd: int, msg_id: str) -> None:
    """A claimed message that does not fit this wake: `.delivered` -> `.json`,
    so the next wake takes it."""
    try:
        os.rename(msg_id + ".delivered", msg_id + ".json",
                  src_dir_fd=spool_fd, dst_dir_fd=spool_fd)
    except OSError:
        pass


_BIG_SEQ = 2 ** 63


def _pre_seq(spool_fd: int, msg_id: str) -> int:
    """The seq a `.json` claims, read ONLY to order candidates -- never trusted
    and never used for content. Anything odd sorts last."""
    try:
        data = _parse_strict(_read_regular(msg_id + ".json", SPOOL_FILE_MAX_BYTES, spool_fd))
    except (OSError, ValueError):
        return _BIG_SEQ
    seq = data.get("seq") if isinstance(data, dict) else None
    return seq if _valid_stamp(seq) else _BIG_SEQ


def wake(spool_fd: int, nonce: str, pid: int, start: int) -> Optional[str]:
    """One look at the spool: the stderr block for the messages handed over,
    or None when there is nothing to deliver (or owner.json is not ours)."""
    try:
        names = os.listdir(spool_fd)
    except OSError:
        return None
    ids = sorted(m.group(1) for m in (_JSON_FILE_RE.match(n) for n in names) if m)
    if not ids or not owner_json_names(spool_fd, Owner(pid, start, "", "")):
        return None
    # Order the candidates by a cheap pre-read of their seq (never trusted),
    # then CLAIM each by rename -- the single claim point -- and only then read
    # and re-validate the claimed `.delivered` file: what is injected is what
    # was read after the rename, so a swap between listing and claim changes
    # nothing the model sees.
    order = sorted(ids[:LIST_MAX_NAMES], key=lambda m: (_pre_seq(spool_fd, m), m))
    taken: List[dict] = []
    total = 0
    for msg_id in order:
        if len(taken) >= MAX_MESSAGES:
            break
        if not claim(spool_fd, msg_id):
            continue                # someone else took it
        try:
            data = _parse_strict(_read_regular(msg_id + ".delivered", SPOOL_FILE_MAX_BYTES, spool_fd))
            record = validate_record(data, msg_id)
        except FileNotFoundError:
            continue                # moved on after our claim (read by the model)
        except (OSError, ValueError):
            record = None
        if record is None:
            _reject(spool_fd, msg_id)
            continue                # a rejected file does not count against the caps
        size = len(record["text"].encode("utf-8"))
        if taken and total + size > MAX_TEXT_BYTES:
            _unclaim(spool_fd, msg_id)
            break
        taken.append(record)
        total += size
    taken.sort(key=lambda rec: (rec["seq"], rec["msgId"]))
    return frame_block(taken, nonce) if taken else None


def write_stderr(data: bytes) -> None:
    """One os.write loop; a failure is the accepted case and not retried."""
    view = memoryview(data)
    try:
        while view:
            view = view[os.write(2, view):]
    except OSError:
        pass


def main() -> None:
    try:
        cfg = config_from_env(os.environ)
        if cfg.disable:
            os._exit(0)
        owner = find_owner(cfg.sessions_dir,
                           cfg.walk_pid if cfg.walk_pid is not None else os.getppid())
        if owner.entrypoint != "cli" and not cfg.force:
            os._exit(0)
        spool_fd = open_spool(cfg.root, owner)
        if spool_fd is None or not owner_json_names(spool_fd, owner):
            os._exit(0)
        if take_lock(spool_fd) is None:
            os._exit(0)
    except Exception:  # noqa: BLE001 -- fail closed and quiet, whatever it was
        os._exit(0)
    while True:
        try:
            block = wake(spool_fd, secrets.token_hex(16), owner.pid, owner.start)
        except Exception:  # noqa: BLE001 -- one bad wake must not end the hook
            block = None
        if block is not None:
            write_stderr(block.encode("utf-8"))
            os._exit(2)
        time.sleep(cfg.poll_s)


if __name__ == "__main__":
    main()
